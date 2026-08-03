# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dashboard, session detail, SSE, and control endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from functools import wraps
from time import monotonic
from typing import Any, cast
from uuid import UUID

from apps.agents.delete import AgentNotFoundError, delete_agent_for_user
from apps.agents.models import Agent, SpendPolicy
from apps.bus.client import async_client, key_prefix
from apps.keys.credential_guides import credential_guides_for_ui
from apps.keys.exceptions import (
    KeyNotFoundError,
    KeyStorageMisconfiguredError,
    KeyValidationError,
    OAuthConfigurationError,
    OAuthProviderError,
    OAuthStateError,
)
from apps.keys.models import CredentialSource
from apps.keys.oauth import OAUTH_PROVIDERS
from apps.keys.oauth import services as oauth_services
from apps.keys.services import commands
from apps.keys.services.queries import (
    get_oauth_metadata,
    get_owned_user_credential,
    list_user_credentials,
)
from apps.keys.types import SERVICE_TYPES
from apps.queues.models import Queue, QueueItem, QueueItemStatus
from apps.queues.services.queries import (
    QUEUE_ITEMS_TABLE_SCHEMA,
    get_queue,
    list_queue_items_page,
    list_queue_summaries,
    list_source_ids,
)
from apps.runner.dispatch import (
    maybe_dispatch_session,
    push_chat_and_dispatch,
    push_control_and_maybe_dispatch,
)
from apps.runner.session_start import StartSessionError
from apps.runner.start import start_button_session, start_manual_session
from apps.sessions.models import AgentSession
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    user_daily_spend,
    user_monthly_spend,
)
from apps.sessions.services.queries import activities_for
from apps.web.services.queries import (
    get_active_button_trigger,
    get_activity_snapshot,
    get_agent_detail_data,
    get_credential_for_write_check,
    get_dashboard_data,
    get_owned_agent,
    get_owned_direct_parent,
    get_owned_session,
    get_session_llm_label,
    list_active_button_triggers,
)
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST
from libs.providers.key.health_codes import HEALTH_CODE_LABELS
from libs.web_tables import ListPage, parse_table_query
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)
SESSION_SSE_POLL_SECONDS = 1.0
SESSION_SSE_HEARTBEAT_SECONDS = 15.0
SESSION_REDIS_TRANSPORT_FAILURES = (RedisConnectionError, RedisTimeoutError, OSError)


def _html_safe_json(value: Any) -> str:
    """Serialize page data while preventing JSON text from becoming HTML markup."""
    return json.dumps(value).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def _oauth_catalog_for_ui() -> dict[str, list[dict[str, str]]]:
    """Return provider-owned, secret-free capability copy for the credential form.

    Iterates every registered provider so the Keys page form generalizes to new
    providers without hardcoding a single provider id.
    """
    catalog: dict[str, list[dict[str, str]]] = {}
    for provider_id in OAUTH_PROVIDERS.provider_ids():
        provider = OAUTH_PROVIDERS.get(provider_id)
        catalog[provider.id] = [
            {
                'id': capability.id,
                'label': capability.label,
                'description': capability.description,
                'scope': capability.scope,
                'support': capability.support,
                'support_label': 'Available now' if capability.support == 'current' else 'Future support',
            }
            for capability in provider.capabilities
        ]
    return catalog


_CALLBACK_ROUTE_NAMES = {
    'google': 'settings_keys_oauth_google_callback',
    'dropbox': 'settings_keys_oauth_dropbox_callback',
}

_PROVIDER_LABELS = {
    'google': 'Google',
    'dropbox': 'Dropbox',
}


def _provider_label(provider_id: str | None) -> str:
    """Return the capitalized provider name used in fixed, secret-free user messages."""
    return _PROVIDER_LABELS.get(provider_id or '', 'OAuth')


def _fixed_oauth_callback_uri(request: HttpRequest, provider_id: str) -> str:
    """Build the provider-fixed callback URI; require HTTPS outside local development."""
    route_name = _CALLBACK_ROUTE_NAMES.get(provider_id)
    if route_name is None:
        raise OAuthConfigurationError('OAuth callback is unavailable')
    callback_uri = request.build_absolute_uri(reverse(route_name))
    if not settings.DEBUG and not callback_uri.startswith('https://'):
        raise OAuthConfigurationError('OAuth callback is unavailable')
    return callback_uri


def _existing_session_key(request: HttpRequest, *, create: bool) -> str:
    """Return the current session key, creating one only for authorization start."""
    session_key = request.session.session_key
    if session_key is None and create:
        request.session.create()
        session_key = request.session.session_key
    if not isinstance(session_key, str) or not session_key:
        raise OAuthStateError('OAuth authorization state is invalid')
    return session_key


def _scrub_caught_oauth_failure(failure: BaseException) -> None:
    """Drop retained traceback chains after converting OAuth failures to fixed HTTP text."""
    failure.__traceback__ = None
    failure.__context__ = None
    failure.__cause__ = None


def _harden_oauth_callback_response(
    view: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    """Prevent every callback response from caching or forwarding its sensitive URL."""

    @wraps(view)
    def wrapped(request: HttpRequest) -> HttpResponse:
        """Apply callback-only response headers after all route outcomes."""
        response = view(request)
        response['Referrer-Policy'] = 'no-referrer'
        response['Cache-Control'] = 'no-store'
        return response

    return wrapped


def _credential_write_denied(row: Any | None) -> HttpResponseBadRequest | None:
    """Return a clear bad request when disk owns the credential."""
    if row is not None and row.source == CredentialSource.DISK:
        return HttpResponseBadRequest('disk-sourced credential is read-only; edit the source file instead')
    return None


def _require_authenticated_user_id(request: HttpRequest) -> int:
    """Extract the authenticated user's pk, or raise Http404."""
    if not request.user.is_authenticated:
        raise Http404('Not found')
    return int(cast(AbstractBaseUser, request.user).pk)


def _chatbox_context(*, agent: Agent, session: AgentSession | None) -> dict[str, Any]:
    """Build template context for the chat input box."""
    button_triggers = list_active_button_triggers(agent)
    if session is None:
        return {
            'agent': agent,
            'session': None,
            'chat_mode': 'start',
            'chat_post_url': reverse('agent_start_chat', kwargs={'agent_id': agent.id}),
            'button_triggers': button_triggers,
        }
    return {
        'agent': agent,
        'session': session,
        'chat_mode': 'continue',
        'chat_post_url': reverse('session_chat', kwargs={'session_id': session.id}),
        'button_triggers': button_triggers,
    }


def dashboard(request: HttpRequest) -> HttpResponse:
    """Main dashboard listing agents and recent sessions."""
    user_id = cast(AbstractBaseUser, request.user).pk if request.user.is_authenticated else None
    data = get_dashboard_data(user_id=user_id)

    usage_context: dict[str, Any] = {}
    if user_id is not None:
        usage_context['user_daily_spend'] = user_daily_spend(user_id)
        usage_context['user_monthly_spend'] = user_monthly_spend(user_id)
        try:
            policy = SpendPolicy.objects.get(user_id=user_id)
            usage_context['user_daily_limit'] = policy.daily_spend_limit_usd
            usage_context['user_monthly_limit'] = policy.monthly_spend_limit_usd
        except SpendPolicy.DoesNotExist:
            usage_context['user_daily_limit'] = getattr(settings, 'DEFAULT_USER_DAILY_SPEND_LIMIT_USD', None)
            usage_context['user_monthly_limit'] = getattr(settings, 'DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD', None)

    return render(
        request,
        'web/dashboard.html',
        {'agents': data.agents, 'sessions': data.sessions, 'examples': data.examples, **usage_context},
    )


@login_required(login_url='/admin/login/')
@require_GET
def dashboard_agents_partial(request: HttpRequest) -> HttpResponse:
    """Render the authenticated user's current agent-list fragment."""
    data = get_dashboard_data(user_id=_require_authenticated_user_id(request))
    return render(
        request,
        'web/partials/agent_list.html',
        {'agents': data.agents, 'examples': data.examples},
    )


@login_required(login_url='/admin/login/')
@require_GET
def agent_detail(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Agent overview with session list and chat input."""
    data = get_agent_detail_data(_require_authenticated_user_id(request), agent_id)
    context: dict[str, Any] = {
        'agent': data.agent,
        'sessions': data.sessions,
        'queue_summaries': list_queue_summaries(agent=data.agent),
        'source_label': data.source_label,
        'config_dirty': data.config_dirty,
        'agent_daily_spend': agent_daily_spend(data.agent.pk),
        'agent_monthly_spend': agent_monthly_spend(data.agent.pk),
        'agent_daily_limit': data.agent.daily_spend_limit_usd,
        'agent_monthly_limit': data.agent.monthly_spend_limit_usd,
    }
    context.update(_chatbox_context(agent=data.agent, session=None))
    return render(request, 'web/agent_detail.html', context)


def _owned_queue(agent: Agent, queue_id: str) -> Queue:
    """Return *agent*'s queue with slug *queue_id*, or raise Http404 when missing."""
    queue = get_queue(agent=agent, queue_id=queue_id)
    if queue is None:
        raise Http404('Queue not found')
    return queue


def _queue_items_page_for_request(
    request: HttpRequest,
    agent_id: UUID,
    queue_id: str,
) -> tuple[Agent, Queue, ListPage[QueueItem]]:
    """Load the owned agent/queue and one filtered/sorted/paginated items page from the request."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    queue = _owned_queue(agent, queue_id)
    query = parse_table_query(request.GET, QUEUE_ITEMS_TABLE_SCHEMA)
    list_page = list_queue_items_page(queue=queue, query=query)
    return agent, queue, list_page


@login_required(login_url='/admin/login/')
@require_GET
def agent_queues_partial(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Render the owned agent's Queues section fragment (per-status counts + links)."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    return render(
        request,
        'web/partials/agent_queues.html',
        {'agent': agent, 'queue_summaries': list_queue_summaries(agent=agent)},
    )


@login_required(login_url='/admin/login/')
@require_GET
def queue_items(request: HttpRequest, agent_id: UUID, queue_id: str) -> HttpResponse:
    """Full queue items page: agent frame chrome plus the filter/sort/pagination table."""
    agent, queue, list_page = _queue_items_page_for_request(request, agent_id, queue_id)
    context: dict[str, Any] = {
        'agent': agent,
        'queue': queue,
        'list_page': list_page,
        'status_choices': QueueItemStatus.choices,
        'source_ids': list_source_ids(queue=queue),
    }
    context.update(_chatbox_context(agent=agent, session=None))
    return render(request, 'web/queue_items.html', context)


@login_required(login_url='/admin/login/')
@require_GET
def queue_items_partial(request: HttpRequest, agent_id: UUID, queue_id: str) -> HttpResponse:
    """Render only the queue items table region, for the initial embed and htmx refetch."""
    agent, queue, list_page = _queue_items_page_for_request(request, agent_id, queue_id)
    return render(
        request,
        'web/partials/queue_items_table.html',
        {'agent': agent, 'queue': queue, 'list_page': list_page},
    )


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def agent_start_chat(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Start a new session with an initial chat message."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    content = request.POST.get('content', '').strip()
    if not content:
        return HttpResponseBadRequest('content required')
    try:
        session = start_manual_session(agent, initial_message=content)
    except StartSessionError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('session_detail', session_id=session.id)


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def delete_agent(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Delete an agent and all its sessions."""
    try:
        delete_agent_for_user(cast(AbstractBaseUser, request.user), agent_id)
    except AgentNotFoundError as exc:
        raise Http404('Agent not found') from exc
    return redirect('dashboard')


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def agent_run_button_trigger(request: HttpRequest, agent_id: UUID, trigger_id: UUID) -> HttpResponse:
    """Start a new session from a button trigger and redirect to session detail."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    trigger = get_active_button_trigger(agent, trigger_id)
    try:
        session = start_button_session(agent, trigger)
    except StartSessionError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('session_detail', session_id=session.id)


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def start_agent_session(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Start a new empty session for an agent."""
    agent = get_owned_agent(_require_authenticated_user_id(request), agent_id)
    try:
        session = start_manual_session(agent)
    except StartSessionError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('session_detail', session_id=session.id)


@login_required(login_url='/admin/login/')
@require_GET
def session_detail(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """Session activity log and chat continuation."""
    user_id = _require_authenticated_user_id(request)
    session = get_owned_session(user_id, session_id)
    direct_parent = get_owned_direct_parent(session, user_id=user_id)
    parent_session = None
    if direct_parent is not None:
        parent_session = {
            'id': direct_parent.id,
            'name': direct_parent.name,
        }
    context: dict[str, Any] = {
        'session': session,
        'agent': session.agent,
        'llm_label': get_session_llm_label(session),
        'parent_session': parent_session,
    }
    context.update(_chatbox_context(agent=session.agent, session=session))
    return render(request, 'web/session_detail.html', context)


@login_required(login_url='/admin/login/')
@require_GET
def session_activity_snapshot(request: HttpRequest, session_id: UUID) -> JsonResponse:
    """Return the authorized JSON snapshot of one session's current activities."""
    payload = get_activity_snapshot(_require_authenticated_user_id(request), session_id)
    return JsonResponse(payload)


def _sse_event(data: dict[str, Any], *, event: str) -> str:
    """Format one typed server-sent event frame."""
    return f'event: {event}\ndata: {json.dumps(data)}\n\n'


def _parse_session_message(message: Any) -> tuple[str, dict[str, Any]] | None:
    """Return a validated session channel and object payload from Redis data."""
    if not isinstance(message, dict) or message.get('type') != 'message':
        return None
    encoded = message.get('data')
    if not isinstance(encoded, str | bytes | bytearray):
        return None
    try:
        raw = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    channel = raw.get('channel')
    payload = raw.get('payload')
    if (
        not isinstance(channel, str)
        or channel not in {'session_activity', 'session_update'}
        or not isinstance(payload, dict)
    ):
        return None
    return channel, payload


def _accept_activity_upsert(
    payload: dict[str, Any],
    *,
    session_id: UUID,
    highest_revisions: dict[str, int],
) -> dict[str, Any] | None:
    """Validate and record a strictly newer full activity upsert for this session."""
    if payload.get('operation') != 'upsert':
        return None
    activity = payload.get('activity')
    if not isinstance(activity, dict):
        return None
    activity_id = activity.get('id')
    revision = activity.get('revision')
    if (
        not isinstance(activity_id, str)
        or not activity_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or activity.get('session_id') != str(session_id)
        or revision <= highest_revisions.get(activity_id, 0)
    ):
        return None
    highest_revisions[activity_id] = revision
    return {'operation': 'upsert', 'activity': activity}


@require_GET
@login_required(login_url='/admin/login/')
async def session_events_sse(request: HttpRequest, session_id: UUID) -> StreamingHttpResponse:
    """Replay authoritative activities then tail only newer activity revisions."""
    user_id = await sync_to_async(_require_authenticated_user_id)(request)
    await sync_to_async(get_owned_session)(user_id, session_id)

    async def stream() -> AsyncIterator[str]:
        """Yield replay upserts and validated messages from this session's channel."""
        highest_revisions: dict[str, int] = {}

        async def replay() -> AsyncIterator[str]:
            """Yield the DB snapshot and release all replay ORM objects afterward."""
            activities = await sync_to_async(activities_for)(session_id)
            try:
                for activity in activities:
                    activity_payload = activity.to_stream_dict()
                    highest_revisions[activity_payload['id']] = activity_payload['revision']
                    yield _sse_event(
                        {'operation': 'upsert', 'activity': activity_payload},
                        event='session_activity',
                    )
            finally:
                # The live tail needs only primitive revision state, not replay ORM objects.
                del activities

        try:
            client = async_client()
        except RuntimeError:
            # A missing Redis client still permits authoritative replay.
            async for frame in replay():
                yield frame
            return

        try:
            pubsub = client.pubsub()
            try:
                channel = f'{key_prefix()}session:{session_id}:events'
                subscribed = False
                try:
                    try:
                        await pubsub.subscribe(channel)
                        subscribed = True
                    except SESSION_REDIS_TRANSPORT_FAILURES:
                        # Preserve DB replay when Redis cannot establish the live tail.
                        async for frame in replay():
                            yield frame
                        return

                    # Subscribe first so commits racing the authoritative snapshot remain buffered.
                    last_heartbeat = monotonic()
                    async for frame in replay():
                        yield frame

                    while True:
                        try:
                            message = await pubsub.get_message(
                                ignore_subscribe_messages=True,
                                timeout=SESSION_SSE_POLL_SECONDS,
                            )
                        except SESSION_REDIS_TRANSPORT_FAILURES:
                            return
                        if message is None:
                            now = monotonic()
                            if now - last_heartbeat >= SESSION_SSE_HEARTBEAT_SECONDS:
                                # Comments keep intermediaries alive without creating browser events.
                                yield ': heartbeat\n\n'
                                last_heartbeat = now
                            await asyncio.sleep(0.1)
                            continue
                        parsed = _parse_session_message(message)
                        if parsed is None:
                            continue
                        channel_name, payload = parsed
                        if channel_name == 'session_activity':
                            accepted = _accept_activity_upsert(
                                payload,
                                session_id=session_id,
                                highest_revisions=highest_revisions,
                            )
                            if accepted is None:
                                continue
                            yield _sse_event(accepted, event='session_activity')
                        elif channel_name == 'session_update':
                            yield _sse_event(payload, event='session_update')
                finally:
                    if subscribed:
                        try:
                            await pubsub.unsubscribe(channel)
                        except SESSION_REDIS_TRANSPORT_FAILURES:
                            logger.debug('Session activity unsubscribe unavailable')
            finally:
                try:
                    await pubsub.close()
                except SESSION_REDIS_TRANSPORT_FAILURES:
                    logger.debug('Session activity pubsub close unavailable')
        finally:
            try:
                await client.close()
            except SESSION_REDIS_TRANSPORT_FAILURES:
                logger.debug('Session activity client close unavailable')

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@require_GET
async def sse_spike(request: HttpRequest) -> StreamingHttpResponse:
    """M0 plumbing check: stream timestamped events through nginx."""

    async def stream() -> AsyncIterator[str]:
        for i in range(5):
            yield _sse_event({'n': i, 'message': f'spike-{i}'}, event='spike')
            await asyncio.sleep(1)

    response = StreamingHttpResponse(stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@csrf_protect
@require_POST
@login_required(login_url='/admin/login/')
def session_chat(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """Post a follow-up chat message to an existing session."""
    get_owned_session(_require_authenticated_user_id(request), session_id)
    content = request.POST.get('content', '').strip()
    if not content:
        return HttpResponseBadRequest('content required')
    push_chat_and_dispatch(session_id, content)
    return HttpResponse(status=204)


@csrf_protect
@require_POST
@login_required(login_url='/admin/login/')
def session_pause(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """Pause a running session."""
    session = get_owned_session(_require_authenticated_user_id(request), session_id)
    push_control_and_maybe_dispatch(session_id, 'pause')
    session.refresh_from_db()
    return render(request, 'web/partials/session_status.html', {'session': session})


@csrf_protect
@require_POST
@login_required(login_url='/admin/login/')
def session_resume(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """Resume a paused session."""
    session = get_owned_session(_require_authenticated_user_id(request), session_id)
    maybe_dispatch_session(session_id)
    session.refresh_from_db()
    return render(request, 'web/partials/session_status.html', {'session': session})


@csrf_protect
@require_POST
@login_required(login_url='/admin/login/')
def session_abort(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """Abort a session."""
    session = get_owned_session(_require_authenticated_user_id(request), session_id)
    push_control_and_maybe_dispatch(session_id, 'abort')
    maybe_dispatch_session(session_id)
    session.refresh_from_db()
    return render(request, 'web/partials/session_status.html', {'session': session})


def render_event_partial(request: HttpRequest, session_id: UUID) -> HttpResponse:
    """HTMX SSE swap target — individual event rows."""
    return HttpResponse('')


@login_required(login_url='/admin/login/')
@require_GET
def settings_keys(request: HttpRequest) -> HttpResponse:
    """Write-only settings page for user-named credentials (metadata only)."""
    user = cast(AbstractBaseUser, request.user)
    oauth_catalog = _oauth_catalog_for_ui()
    return render(
        request,
        'web/keys.html',
        {
            'named_keys': list_user_credentials(user.pk),
            'service_types': sorted(SERVICE_TYPES),
            'credential_guides_json': _html_safe_json(credential_guides_for_ui()),
            'google_oauth_capabilities': oauth_catalog['google'],
            'dropbox_oauth_capabilities': oauth_catalog['dropbox'],
            'oauth_capable_types_json': _html_safe_json(sorted(oauth_catalog)),
            'HEALTH_CODE_LABELS': HEALTH_CODE_LABELS,
        },
    )


@login_required(login_url='/admin/login/')
@require_GET
def settings_keys_partial(request: HttpRequest) -> HttpResponse:
    """Render credential metadata for the authenticated user's key-list fragment."""
    named_keys = list_user_credentials(_require_authenticated_user_id(request))
    return render(
        request,
        'web/partials/key_list.html',
        {'named_keys': named_keys, 'HEALTH_CODE_LABELS': HEALTH_CODE_LABELS},
    )


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def settings_keys_add_named(request: HttpRequest) -> HttpResponse:
    """Create a UI-owned credential unless an existing disk credential owns its name."""
    name = request.POST.get('name', '').strip()
    type_name = request.POST.get('type', '').strip()
    auth_kind = request.POST.get('auth_kind', 'static').strip()
    secret = request.POST.get('secret', '')
    user = cast(AbstractBaseUser, request.user)
    row = get_credential_for_write_check(user.pk, name)
    denied = _credential_write_denied(row)
    if denied is not None:
        return denied
    try:
        if auth_kind == 'static':
            commands.upsert_user_named(user.pk, name, type_name, secret)
        elif auth_kind == 'oauth':
            commands.create_user_oauth(
                user.pk,
                name,
                type_name,
                provider_id=OAUTH_PROVIDERS.provider_id_for_credential_type(type_name),
                capability_ids=request.POST.getlist('capabilities'),
            )
        else:
            raise KeyValidationError('credential authentication kind is invalid')
    except KeyValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('settings_keys')


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def settings_keys_oauth_authorize(request: HttpRequest, credential_id: UUID) -> HttpResponse:
    """Start an owned OAuth declaration using its provider's fixed callback and current session."""
    user_id = _require_authenticated_user_id(request)
    provider_id: str | None = None
    try:
        row = get_owned_user_credential(user_id, credential_id)
        provider_id, _ = get_oauth_metadata(row)
        start = oauth_services.start_authorization(
            user_id=user_id,
            credential_id=credential_id,
            session_key=_existing_session_key(request, create=True),
            redirect_uri=_fixed_oauth_callback_uri(request, provider_id or ''),
        )
    except KeyNotFoundError as exc:
        raise Http404('OAuth credential not found') from exc
    except (KeyValidationError, OAuthConfigurationError, OAuthProviderError, OAuthStateError) as failure:
        _scrub_caught_oauth_failure(failure)
        return HttpResponseBadRequest(f'{_provider_label(provider_id)} authorization could not be started.')
    return redirect(start.authorization_url)


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def settings_keys_oauth_disconnect(request: HttpRequest, credential_id: UUID) -> HttpResponse:
    """Disconnect an owned active OAuth credential while retaining its declaration."""
    try:
        oauth_services.disconnect_authorization(
            user_id=_require_authenticated_user_id(request),
            credential_id=credential_id,
        )
    except KeyNotFoundError as exc:
        raise Http404('OAuth credential not found') from exc
    except KeyValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('settings_keys')


def _complete_oauth_callback(request: HttpRequest, *, provider_id: str) -> HttpResponse:
    """Complete one provider's fixed callback route with safe, provider-labeled messages.

    Shared by every provider callback view; the caller supplies the fixed ``provider_id``
    for its own route so no query parameter ever selects the provider or callback URI.
    """
    if not request.user.is_authenticated:
        # Do not copy callback query parameters containing the authorization code into `next`.
        return redirect('/admin/login/')
    label = _provider_label(provider_id)
    provider_denied = bool(request.GET.get('error'))
    try:
        oauth_services.complete_authorization(
            user_id=_require_authenticated_user_id(request),
            session_key=_existing_session_key(request, create=False),
            state=request.GET.get('state', ''),
            code=None if provider_denied else request.GET.get('code', ''),
            redirect_uri=_fixed_oauth_callback_uri(request, provider_id),
        )
    except (
        KeyNotFoundError,
        KeyStorageMisconfiguredError,
        KeyValidationError,
        OAuthConfigurationError,
        OAuthProviderError,
        OAuthStateError,
    ) as failure:
        _scrub_caught_oauth_failure(failure)
        messages.error(request, f'{label} authorization could not be completed.')
    else:
        if provider_denied:
            messages.error(request, f'{label} authorization was denied.')
        else:
            messages.success(request, f'{label} authorization completed.')
    return redirect('settings_keys')


@_harden_oauth_callback_response
@require_GET
def settings_keys_oauth_google_callback(request: HttpRequest) -> HttpResponse:
    """Complete Google's fixed callback route with Google-labeled safe messages."""
    return _complete_oauth_callback(request, provider_id='google')


@_harden_oauth_callback_response
@require_GET
def settings_keys_oauth_dropbox_callback(request: HttpRequest) -> HttpResponse:
    """Complete Dropbox's fixed callback route with Dropbox-labeled safe messages."""
    return _complete_oauth_callback(request, provider_id='dropbox')


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def settings_keys_delete_named(request: HttpRequest, name: str) -> HttpResponse:
    """Delete a UI-owned credential while preserving disk-owned credentials."""
    user = cast(AbstractBaseUser, request.user)
    row = get_credential_for_write_check(user.pk, name)
    denied = _credential_write_denied(row)
    if denied is not None:
        return denied
    try:
        commands.delete_user_credential(user.pk, name)
    except KeyValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    except KeyNotFoundError as exc:
        raise Http404('Key not found') from exc
    return redirect('settings_keys')

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Dashboard, session detail, SSE, and control endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import UUID

from apps.agents.delete import AgentNotFoundError, delete_agent_for_user
from apps.agents.models import Agent, SpendPolicy
from apps.bus.client import async_client, key_prefix
from apps.keys.credential_guides import credential_guides_for_ui
from apps.keys.exceptions import KeyNotFoundError, KeyValidationError
from apps.keys.models import CredentialSource
from apps.keys.services import commands
from apps.keys.services.queries import list_user_credentials
from apps.keys.types import SERVICE_TYPES
from apps.runner.dispatch import (
    maybe_dispatch_session,
    push_chat_and_dispatch,
    push_control_and_maybe_dispatch,
)
from apps.runner.session_start import StartSessionError
from apps.runner.start import start_manual_session
from apps.sessions.events import events_for
from apps.sessions.models import AgentSession
from apps.sessions.services.budget import (
    agent_daily_spend,
    agent_monthly_spend,
    user_daily_spend,
    user_monthly_spend,
)
from apps.web.services.queries import (
    get_agent_detail_data,
    get_credential_for_write_check,
    get_dashboard_data,
    get_owned_agent,
    get_owned_session,
    get_session_llm_label,
)
from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    StreamingHttpResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)


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
    if session is None:
        return {
            'agent': agent,
            'session': None,
            'chat_mode': 'start',
            'chat_post_url': reverse('agent_start_chat', kwargs={'agent_id': agent.id}),
        }
    return {
        'agent': agent,
        'session': session,
        'chat_mode': 'continue',
        'chat_post_url': reverse('session_chat', kwargs={'session_id': session.id}),
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
            from django.conf import settings

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
        'source_label': data.source_label,
        'config_dirty': data.config_dirty,
        'agent_daily_spend': agent_daily_spend(data.agent.pk),
        'agent_monthly_spend': agent_monthly_spend(data.agent.pk),
        'agent_daily_limit': data.agent.daily_spend_limit_usd,
        'agent_monthly_limit': data.agent.monthly_spend_limit_usd,
    }
    context.update(_chatbox_context(agent=data.agent, session=None))
    return render(request, 'web/agent_detail.html', context)


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
    """Session event log and chat continuation."""
    session = get_owned_session(_require_authenticated_user_id(request), session_id)
    context: dict[str, Any] = {
        'session': session,
        'agent': session.agent,
        'llm_label': get_session_llm_label(session),
    }
    context.update(_chatbox_context(agent=session.agent, session=session))
    return render(request, 'web/session_detail.html', context)


def _sse_event(data: dict[str, Any], *, event: str = 'session_event') -> str:
    return f'event: {event}\ndata: {json.dumps(data)}\n\n'


@require_GET
@login_required(login_url='/admin/login/')
async def session_events_sse(request: HttpRequest, session_id: UUID) -> StreamingHttpResponse:
    """Replay persisted events then tail pub/sub (dedupe by seq)."""
    user_id = await sync_to_async(_require_authenticated_user_id)(request)
    await sync_to_async(get_owned_session)(user_id, session_id)

    async def stream() -> AsyncIterator[str]:
        last_seq = 0
        events = await sync_to_async(events_for)(session_id)
        for event in events:
            payload = event.to_stream_dict()
            last_seq = max(last_seq, payload['seq'])
            yield _sse_event(payload, event='session_event')

        try:
            client = async_client()
            pubsub = client.pubsub()
            channel = f'{key_prefix()}session:{session_id}:events'
            await pubsub.subscribe(channel)
            try:
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None:
                        await asyncio.sleep(0.1)
                        continue
                    if message['type'] != 'message':
                        continue
                    raw = json.loads(message['data'])
                    channel_name = raw.get('channel')
                    payload = raw.get('payload', {})
                    if channel_name == 'session_event':
                        if payload.get('seq', 0) <= last_seq:
                            continue
                        last_seq = payload['seq']
                        yield _sse_event(payload, event='session_event')
                    elif channel_name == 'session_update':
                        yield _sse_event(payload, event='session_update')
                    else:
                        logger.warning('Unknown session message channel %r', channel_name)
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
                await client.close()
        except RuntimeError:
            pass

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
    return render(
        request,
        'web/keys.html',
        {
            'named_keys': list_user_credentials(user.pk),
            'service_types': sorted(SERVICE_TYPES),
            'credential_guides_json': json.dumps(credential_guides_for_ui())
            .replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026'),
        },
    )


@login_required(login_url='/admin/login/')
@require_GET
def settings_keys_partial(request: HttpRequest) -> HttpResponse:
    """Render credential metadata for the authenticated user's key-list fragment."""
    named_keys = list_user_credentials(_require_authenticated_user_id(request))
    return render(request, 'web/partials/key_list.html', {'named_keys': named_keys})


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def settings_keys_add_named(request: HttpRequest) -> HttpResponse:
    """Create a UI-owned credential unless an existing disk credential owns its name."""
    name = request.POST.get('name', '').strip()
    type_name = request.POST.get('type', '').strip()
    secret = request.POST.get('secret', '')
    user = cast(AbstractBaseUser, request.user)
    row = get_credential_for_write_check(user.pk, name)
    denied = _credential_write_denied(row)
    if denied is not None:
        return denied
    try:
        commands.upsert_user_named(user.pk, name, type_name, secret)
    except KeyValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect('settings_keys')


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

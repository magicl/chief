# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Agent configuration editor and create endpoints."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast
from uuid import UUID

from apps.agents.ingest import persist_agent_config
from apps.agents.models import AgentConfig
from apps.agents.services.config_commands import (
    ConfigCommandError,
    create_from_yaml,
    update_agent_profile,
)
from apps.agents.services.config_mutations import (
    ConfigMutationError,
    apply_config_mutation,
)
from apps.agents.services.config_sync import compute_save_metadata
from apps.agents.services.config_validation import (
    ConfigValidationError,
    validate_agent_config_yaml,
)
from apps.agents.services.queries import (
    build_config_catalog,
    get_config_editor_context,
    get_create_editor_context,
)
from apps.web.views import _owned_agent
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from libs.agent_spec.yaml_dump import dump_agent_config_spec
from libs.agent_specs import load_example_text


def _validation_json_response(exc: ConfigValidationError) -> JsonResponse:
    """Serialize structured validation errors as a 400 JSON response."""
    return JsonResponse(
        {'errors': [asdict(item) for item in exc.errors]},
        status=400,
    )


def _parse_mutation(request: HttpRequest) -> dict[str, Any]:
    """Parse the helper mutation JSON object from POST form data."""
    raw = request.POST.get('mutation', '').strip()
    if raw:
        parsed: dict[str, Any] = json.loads(raw)
        return parsed
    return {}


def _json_for_script_tag(payload: dict[str, Any]) -> str:
    """Serialize JSON safe for embedding in a ``<script type=\"application/json\">`` tag."""
    return json.dumps(payload).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def _attach_page_urls(context: dict[str, Any], request: HttpRequest) -> None:
    """Merge save/mutate/csrf into page_data for the editor script."""
    context['page_data']['urls'] = {
        'save': context['save_url'],
        'mutate': context['mutate_url'],
        'csrf': get_token(request),
    }
    context['page_data_json'] = _json_for_script_tag(context['page_data'])


@login_required(login_url='/admin/login/')
@csrf_protect
@require_http_methods(['GET', 'POST'])
def agent_create(request: HttpRequest) -> HttpResponse:
    """Show the unified create editor or instantiate an agent from editor YAML."""
    user = cast(AbstractBaseUser, request.user)
    if request.method == 'GET':
        example_slug = request.GET.get('example', '').strip() or 'minimal'
        try:
            initial_yaml = load_example_text(example_slug)
        except FileNotFoundError:
            example_slug = 'minimal'
            initial_yaml = load_example_text(example_slug)
        context = get_create_editor_context(
            user.pk,
            initial_yaml=initial_yaml,
            active_example=example_slug,
        )
        _attach_page_urls(context, request)
        return render(request, 'web/agent_config.html', context)

    spec_yaml = request.POST.get('spec_yaml', '').strip()
    name = request.POST.get('name', '').strip()
    identifier = request.POST.get('identifier', '').strip() or None
    if not spec_yaml:
        return HttpResponseBadRequest('spec_yaml required')
    try:
        agent = create_from_yaml(user, spec_yaml, name=name, identifier=identifier)
    except ConfigValidationError as exc:
        if 'application/json' in request.headers.get('Accept', ''):
            return _validation_json_response(exc)
        context = get_create_editor_context(
            user.pk,
            initial_yaml=spec_yaml,
            import_errors=exc.errors,
        )
        _attach_page_urls(context, request)
        return render(request, 'web/agent_config.html', context)
    except ConfigCommandError as exc:
        return HttpResponseBadRequest(str(exc))
    redirect_url = reverse('agent_config', kwargs={'agent_id': agent.id})
    if 'application/json' in request.headers.get('Accept', ''):
        return JsonResponse({'ok': True, 'redirect': redirect_url})
    return redirect(redirect_url)


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def agent_create_mutate(request: HttpRequest) -> HttpResponse:
    """Apply a helper mutation to posted YAML on the create screen (no agent yet)."""
    spec_yaml = request.POST.get('spec_yaml', '')
    if not spec_yaml.strip():
        return JsonResponse({'errors': [{'path': '', 'message': 'spec_yaml required'}]}, status=400)
    try:
        mutation = _parse_mutation(request)
        new_yaml = apply_config_mutation(spec_yaml, mutation)
    except ConfigValidationError as exc:
        return _validation_json_response(exc)
    except (ConfigMutationError, json.JSONDecodeError, KeyError) as exc:
        return JsonResponse({'errors': [{'path': '', 'message': str(exc)}]}, status=400)
    return JsonResponse({'yaml': new_yaml})


@login_required(login_url='/admin/login/')
@require_GET
def agent_config(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Render the YAML config editor and helpers for an owned agent."""
    agent = _owned_agent(request, agent_id)
    user = cast(AbstractBaseUser, request.user)
    context = get_config_editor_context(agent, user.pk)
    context['save_url'] = reverse('agent_config_save', kwargs={'agent_id': agent.id})
    context['mutate_url'] = reverse('agent_config_mutate', kwargs={'agent_id': agent.id})
    _attach_page_urls(context, request)
    return render(request, 'web/agent_config.html', context)


@login_required(login_url='/admin/login/')
@require_GET
def agent_config_catalog(request: HttpRequest) -> JsonResponse:
    """Return the server-side catalog for autocomplete and helper dropdowns."""
    user = cast(AbstractBaseUser, request.user)
    return JsonResponse(build_config_catalog(user.pk))


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def agent_config_save(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Validate posted YAML and persist a new immutable config revision."""
    agent = _owned_agent(request, agent_id)
    spec_yaml = request.POST.get('spec_yaml', '')
    if not spec_yaml.strip():
        return JsonResponse({'errors': [{'path': '', 'message': 'spec_yaml required'}]}, status=400)
    try:
        spec = validate_agent_config_yaml(spec_yaml)
        source_rev, dirty = compute_save_metadata(agent, spec_yaml)
    except ConfigValidationError as exc:
        return _validation_json_response(exc)
    new_name = request.POST.get('name', '').strip()
    new_identifier = request.POST.get('identifier', '').strip()
    try:
        update_agent_profile(
            agent,
            cast(AbstractBaseUser, request.user).pk,
            name=new_name,
            identifier=new_identifier,
        )
    except ConfigCommandError as exc:
        path = 'identifier' if 'identifier' in str(exc) or 'already exists' in str(exc) else 'name'
        return JsonResponse(
            {'errors': [{'path': path, 'message': str(exc)}]},
            status=400,
        )
    persist_agent_config(agent, spec, source_rev=source_rev, dirty=dirty)
    if request.headers.get('Accept', '').find('application/json') >= 0:
        return JsonResponse({'ok': True, 'source_rev': source_rev, 'dirty': dirty})
    return redirect('agent_config', agent_id=agent.id)


@login_required(login_url='/admin/login/')
@csrf_protect
@require_POST
def agent_config_mutate(request: HttpRequest, agent_id: UUID) -> HttpResponse:
    """Apply a helper mutation to posted YAML without persisting."""
    _owned_agent(request, agent_id)
    spec_yaml = request.POST.get('spec_yaml', '')
    if not spec_yaml.strip():
        return JsonResponse({'errors': [{'path': '', 'message': 'spec_yaml required'}]}, status=400)
    try:
        mutation = _parse_mutation(request)
        new_yaml = apply_config_mutation(spec_yaml, mutation)
    except ConfigValidationError as exc:
        return _validation_json_response(exc)
    except (ConfigMutationError, json.JSONDecodeError, KeyError) as exc:
        return JsonResponse({'errors': [{'path': '', 'message': str(exc)}]}, status=400)
    return JsonResponse({'yaml': new_yaml})


@login_required(login_url='/admin/login/')
@require_GET
def agent_config_history(request: HttpRequest, agent_id: UUID, config_id: UUID) -> HttpResponse:
    """Show a read-only historical config revision with restore-to-editor action."""
    agent = _owned_agent(request, agent_id)
    config = get_object_or_404(AgentConfig, pk=config_id, agent=agent)
    spec_yaml = dump_agent_config_spec(config.get_spec())
    return render(
        request,
        'web/agent_config_history.html',
        {
            'agent': agent,
            'config': config,
            'spec_yaml': spec_yaml,
            'spec_yaml_json': json.dumps(spec_yaml),
        },
    )

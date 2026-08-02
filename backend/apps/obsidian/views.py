# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Internal HTTP surface for the Obsidian vault service (inter-service auth)."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from apps.obsidian.services.queries import build_vault_bindings_snapshot
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET


def _require_vault_service_token(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Reject requests that lack a valid vault inter-service bearer token."""

    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        expected = settings.OBSIDIAN_VAULT_TOKEN
        if not expected:
            return JsonResponse({'ok': False, 'error': {'kind': 'auth', 'message': 'vault token unset'}}, status=503)
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.startswith('Bearer '):
            return JsonResponse({'ok': False, 'error': {'kind': 'auth', 'message': 'missing bearer'}}, status=401)
        provided = header.removeprefix('Bearer ').strip()
        if not provided or not hmac.compare_digest(provided, expected):
            return JsonResponse({'ok': False, 'error': {'kind': 'auth', 'message': 'unauthorized'}}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


@require_GET
@_require_vault_service_token
def vault_bindings_snapshot(request: HttpRequest) -> JsonResponse:
    """Return the full agent→vault binding snapshot for vault-service restart recovery."""
    del request  # auth already enforced
    return JsonResponse({'ok': True, 'agents': build_vault_bindings_snapshot()})

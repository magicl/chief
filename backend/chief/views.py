# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def check(request: HttpRequest) -> HttpResponse:
    """Liveness ping for clients: returns 200 immediately and does nothing else.

    Kept separate from the /health endpoints so those can grow checks over time
    without affecting this minimal reachability probe.
    """
    return HttpResponse(status=200)


def livez(request: HttpRequest) -> HttpResponse:
    """Liveness: the process is up. No dependencies checked."""
    return HttpResponse('ok', content_type='text/plain')


def readyz(request: HttpRequest) -> HttpResponse:
    """Readiness: the app can serve traffic (DB reachable)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - any DB failure means not ready
        return HttpResponse('db unavailable', status=503, content_type='text/plain')
    return HttpResponse('ok', content_type='text/plain')

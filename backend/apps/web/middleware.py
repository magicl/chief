# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Route-specific response hardening for sensitive web endpoints."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

GOOGLE_OAUTH_CALLBACK_PATH = '/settings/keys/oauth/google/callback/'


class OAuthCallbackResponseMiddleware:
    """Harden callback responses after Django converts all route outcomes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in Django's middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Apply callback-only cache and referrer policy to every response."""
        response = self.get_response(request)
        if request.path == GOOGLE_OAUTH_CALLBACK_PATH:
            response['Referrer-Policy'] = 'no-referrer'
            response['Cache-Control'] = 'no-store'
        return response

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for unauthenticated process health endpoints."""

from django.urls import NoReverseMatch, reverse

from olib.py.django.test.cases import OTestCase


class TestHealthEndpoints(OTestCase):
    """Verify process health routes used by Kubernetes probes."""

    def test_startup_probe_returns_healthy_response(self) -> None:
        """Startup succeeds once Django can serve an HTTP request."""
        try:
            startup_url = reverse('health_startupz')
        except NoReverseMatch:
            self.fail('health_startupz URL is not registered')
        response = self.client.get(startup_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertEqual(response['Content-Type'], 'text/plain')

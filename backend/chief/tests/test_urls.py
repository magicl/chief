# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify project-level URL routing."""

import logging

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestAdminUrls(OTestCase):
    """Keep Chief's Django admin path aligned with Floors."""

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /admin/$', count=1)])
    def test_admin_uses_loelabs_path(self) -> None:
        """Expose admin only below /loelabs-admin and canonicalize its root."""
        response = self.client.get('/loelabs-admin')
        self.assertRedirects(response, '/loelabs-admin/', fetch_redirect_response=False)

        response = self.client.get('/loelabs-admin/')
        self.assertRedirects(
            response,
            '/loelabs-admin/login/?next=/loelabs-admin/',
            fetch_redirect_response=False,
        )

        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 404)

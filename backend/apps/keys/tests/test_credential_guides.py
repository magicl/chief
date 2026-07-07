# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for credential setup guide content."""

from apps.keys.credential_guides import credential_guide, credential_guides_for_ui

from olib.py.django.test.cases import OTestCase


class TestCredentialGuides(OTestCase):
    def test_gmail_guide_lists_delegation_scopes(self) -> None:
        guide = credential_guide('gmail')
        assert guide is not None
        self.assertIn('gmail.modify', guide.scopes or '')
        self.assertIn('gmail.send', guide.scopes or '')

    def test_ui_guides_cover_all_service_types(self) -> None:
        from apps.keys.types import SERVICE_TYPES

        guides = credential_guides_for_ui()
        self.assertEqual(set(guides), set(SERVICE_TYPES))

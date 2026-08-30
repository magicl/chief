# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for credential setup guide content."""

from apps.keys.credential_guides import credential_guide, credential_guides_for_ui

from olib.py.django.test.cases import OTestCase


class TestCredentialGuides(OTestCase):
    def test_google_guide_lists_shared_service_account_setup(self) -> None:
        guide = credential_guide('google')
        assert guide is not None
        self.assertIn('gmail.modify', guide.scopes or '')
        self.assertIn('gmail.send', guide.scopes or '')
        self.assertIn('drive.metadata.readonly', guide.scopes or '')
        steps = ' '.join(guide.find_steps)
        self.assertIn('Gmail API', steps)
        self.assertIn('Google Drive API', steps)
        self.assertIn('domain-wide delegation', steps)
        self.assertIn('as needed', steps)
        self.assertIn('required when Gmail is enabled', steps)
        self.assertIn('Drive impersonates a Google Workspace user', steps)
        self.assertIn('unnecessary only for non-delegated Drive access using the service-account identity', steps)
        self.assertIn('union of scopes required by the enabled tools', steps)
        self.assertIn('Gmail scopes only when Gmail is enabled', steps)
        self.assertIn('Drive scope only when Drive is enabled', steps)
        self.assertIn('full JSON', steps)

    def test_dropbox_guide_lists_offline_token_setup(self) -> None:
        guide = credential_guide('dropbox')
        assert guide is not None
        self.assertIn('files.metadata.read', guide.scopes or '')
        steps = ' '.join(guide.find_steps)
        self.assertIn('offline', steps)
        self.assertIn('externally', steps)
        self.assertIn('app_key', guide.input_placeholder)
        self.assertIn('app_secret', guide.input_placeholder)
        self.assertIn('refresh_token', guide.input_placeholder)

    def test_dropbox_guide_points_oauth_mode_at_keys_authenticate(self) -> None:
        """Direct OAuth-mode users to Authenticate instead of claiming Chief never runs OAuth."""
        guide = credential_guide('dropbox')
        assert guide is not None
        steps = ' '.join(guide.find_steps)
        self.assertIn('Authenticate', steps)
        self.assertNotIn('does not run the OAuth consent flow', steps)

    def test_obsidian_guide_spells_out_token_extraction(self) -> None:
        """The Sync token cannot be copied from the Obsidian UI, so the guide must give the exact CLI recipe."""
        guide = credential_guide('obsidian')
        assert guide is not None
        steps = ' '.join(guide.find_steps)
        self.assertIn('npm install -g obsidian-headless', steps)
        self.assertIn('ob login', steps)
        self.assertIn('~/.config/obsidian-headless/auth_token', steps)
        self.assertIn('~/.obsidian-headless/auth_token', steps)
        self.assertIn('https://obsidian.md/help/sync/headless', steps)
        self.assertIn('auth_token', guide.input_placeholder)
        self.assertIn('encryption_password', guide.input_placeholder)

    def test_obsidian_guide_cautions_that_logout_invalidates_the_token(self) -> None:
        """`ob logout` on the login machine revokes the pasted token, which silently stops vault sync."""
        guide = credential_guide('obsidian')
        assert guide is not None
        steps = ' '.join(guide.find_steps)
        self.assertIn('ob logout', steps)

    def test_obsidian_guide_shows_how_to_find_the_vault_name(self) -> None:
        """The agent's obsidian tool config needs a remote vault id/name, discoverable from the same CLI."""
        guide = credential_guide('obsidian')
        assert guide is not None
        steps = ' '.join(guide.find_steps)
        self.assertIn('ob sync-list-remote', steps)

    def test_ui_guides_cover_all_service_types(self) -> None:
        from apps.keys.types import SERVICE_TYPES

        guides = credential_guides_for_ui()
        self.assertEqual(set(guides), set(SERVICE_TYPES))

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Verify the static nginx config serves web manifests and hides its version."""

from pathlib import Path

from olib.py.django.test.cases import OTestCase

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_NGINX_STATIC_CONF = _REPOSITORY_ROOT / 'infra/k8s/nginx.static.conf'


class TestNginxStaticConf(OTestCase):
    """Check MIME mapping and the Server header in the static nginx config."""

    def _between_mime_include_and_default_type(self) -> str:
        """Return the config between the mime.types include and default_type.

        The extra types map and server_tokens must sit in that span so they
        merge with the included types before the octet-stream default applies.
        """
        source = _NGINX_STATIC_CONF.read_text(encoding='utf-8')
        include_at = source.index('include /etc/nginx/mime.types;')
        default_at = source.index('default_type', include_at)
        return source[include_at:default_at]

    def test_webmanifest_maps_to_manifest_json(self) -> None:
        """`.webmanifest` is served as application/manifest+json."""
        between = self._between_mime_include_and_default_type()
        self.assertIn('application/manifest+json webmanifest;', between)

    def test_server_tokens_off(self) -> None:
        """nginx omits its version from the Server header."""
        between = self._between_mime_include_and_default_type()
        self.assertIn('server_tokens off;', between)

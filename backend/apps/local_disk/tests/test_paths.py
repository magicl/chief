# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from pathlib import Path

from apps.local_disk.hashing import content_hash
from apps.local_disk.paths import agents_dir, keys_dir, resolve_local_root
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


class TestLocalDiskPaths(OTestCase):
    def test_content_hash_is_stable_sha256_prefix(self) -> None:
        self.assertEqual(content_hash('a\nb'), content_hash('a\nb'))
        self.assertTrue(content_hash('x').startswith('sha256:'))

    def test_content_hash_normalizes_crlf(self) -> None:
        self.assertEqual(content_hash('a\r\nb'), content_hash('a\nb'))

    @override_settings(CHIEF_LOCAL_DIR='')
    def test_resolve_root_unset_returns_none(self) -> None:
        self.assertIsNone(resolve_local_root())

    @override_settings(CHIEF_LOCAL_DIR='/tmp/chief-local-test')
    def test_resolve_root_and_subdirs(self) -> None:
        root = resolve_local_root()
        assert root is not None
        self.assertEqual(root, Path('/tmp/chief-local-test').resolve())
        self.assertEqual(keys_dir(), root / 'keys')
        self.assertEqual(agents_dir(), root / 'agents')

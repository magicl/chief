# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import unittest

from libs.file.hashing import content_hash


class TestContentHash(unittest.TestCase):
    """Verify stable hashing for normalized file contents."""

    def test_content_hash_is_stable_with_sha256_prefix(self) -> None:
        first = content_hash('stable contents')
        second = content_hash('stable contents')

        self.assertEqual(first, second)
        self.assertTrue(first.startswith('sha256:'))

    def test_content_hash_normalizes_crlf_to_lf(self) -> None:
        self.assertEqual(content_hash('a\r\nb'), content_hash('a\nb'))

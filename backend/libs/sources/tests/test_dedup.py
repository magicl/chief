# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for shared source dedupe helpers."""

from __future__ import annotations

from libs.sources.dedup import (
    clickup_external_id,
    dedupe_enabled,
    gmail_external_id,
    should_skip_known,
)

from olib.py.django.test.cases import OTestCase


class TestSourceDedupHelpers(OTestCase):
    def test_dedupe_defaults_true(self) -> None:
        self.assertTrue(dedupe_enabled({}))
        self.assertTrue(dedupe_enabled({'dedupe': True}))
        self.assertFalse(dedupe_enabled({'dedupe': False}))

    def test_gmail_external_id_stable_when_dedupe_on(self) -> None:
        self.assertEqual(gmail_external_id('m1', history_id='99', dedupe=True), 'm1')

    def test_gmail_external_id_includes_history_when_dedupe_off(self) -> None:
        self.assertEqual(gmail_external_id('m1', history_id='99', dedupe=False), 'm1:99')

    def test_clickup_external_id_stable_when_dedupe_on(self) -> None:
        self.assertEqual(clickup_external_id('t1', date_updated='9', dedupe=True), 't1')

    def test_clickup_external_id_includes_updated_when_dedupe_off(self) -> None:
        self.assertEqual(clickup_external_id('t1', date_updated='9', dedupe=False), 't1:9')

    def test_should_skip_known_only_when_dedupe_and_set_contains_id(self) -> None:
        known = frozenset({'m1'})
        self.assertTrue(should_skip_known(dedupe=True, external_id='m1', known_external_ids=known))
        self.assertFalse(should_skip_known(dedupe=True, external_id='m2', known_external_ids=known))
        self.assertFalse(should_skip_known(dedupe=False, external_id='m1', known_external_ids=known))
        self.assertFalse(should_skip_known(dedupe=True, external_id='m1', known_external_ids=None))

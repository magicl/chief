# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for example specs catalog."""

from libs.agent_specs import list_examples

from olib.py.django.test.cases import OTestCase


class TestExampleSpecs(OTestCase):
    def test_list_examples_non_empty(self) -> None:
        examples = list_examples()
        self.assertGreaterEqual(len(examples), 2)
        titles = {ex.title for ex in examples}
        self.assertIn('Clock assistant', titles)

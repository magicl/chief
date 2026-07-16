# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for example agent specs library."""

from apps.agents.ingest import validate_spec_tools
from libs.agent_spec import list_examples, load_example

from olib.py.django.test.cases import OTestCase


class AgentSpecsTests(OTestCase):
    def test_list_examples_includes_clock_assistant(self) -> None:
        slugs = {item.slug for item in list_examples()}
        self.assertIn('clock-assistant', slugs)
        self.assertIn('queue-echo', slugs)

    def test_load_example_validates(self) -> None:
        spec = load_example('clock-assistant')
        validate_spec_tools(spec)
        self.assertEqual(spec.tools[0].type, 'clock')

    def test_queue_echo_has_queues(self) -> None:
        spec = load_example('queue-echo')
        self.assertEqual(len(spec.queues), 1)
        self.assertEqual(spec.queues[0].id, 'inbox')

    def test_gmail_triage_example_validates(self) -> None:
        spec = load_example('gmail-triage')
        validate_spec_tools(spec)
        self.assertEqual(spec.tools[0].type, 'gmail')
        self.assertEqual(spec.tools[0].config['subject'], 'me@example.com')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'gmail')

    def test_clickup_inbox_example_validates(self) -> None:
        spec = load_example('clickup-inbox')
        validate_spec_tools(spec)
        self.assertEqual(spec.tools[0].type, 'clickup')
        self.assertEqual(spec.tools[0].config['team_id'], '9000000')
        self.assertEqual(spec.queues[0].sources[0].adapter_type, 'clickup')

    def test_skills_demo_example_validates(self) -> None:
        spec = load_example('skills-demo')
        validate_spec_tools(spec)
        self.assertEqual(len(spec.skills), 1)
        self.assertEqual(spec.skills[0].id, 'greeting-style')

    def test_load_example_rejects_path_traversal(self) -> None:
        from libs.agent_spec import load_example_text

        with self.assertRaises(FileNotFoundError):
            load_example_text('../../../config')
        with self.assertRaises(FileNotFoundError):
            load_example_text('..')

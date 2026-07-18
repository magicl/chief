# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for example agent specs library."""

from apps.agents.ingest import validate_spec_tools
from libs.agent_spec import list_examples, load_example
from libs.tools.context import ToolContext
from libs.tools.registry import get_tool

from olib.py.django.test.cases import OTestCase


class AgentSpecsTests(OTestCase):
    def test_list_examples_includes_clock_assistant(self) -> None:
        """List the baseline and cloud metadata examples in the catalog."""
        slugs = {item.slug for item in list_examples()}
        self.assertIn('clock-assistant', slugs)
        self.assertIn('queue-echo', slugs)
        self.assertIn('cloud-files-browser', slugs)

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

    def test_cloud_files_browser_example_validates(self) -> None:
        """Validate both cloud tools, configured roots, and wired functions."""
        spec = load_example('cloud-files-browser')
        validate_spec_tools(spec)
        self.assertEqual(
            [tool.type for tool in spec.tools],
            ['google_drive', 'dropbox'],
        )
        self.assertEqual(spec.tools[0].config['roots'][0]['id'], 'my-drive')
        self.assertEqual(spec.tools[1].config['roots'][0]['id'], 'projects')
        self.assertEqual(spec.queues, [])

        ctx = ToolContext(spec=spec, user_id=0)
        expected_functions = {'list_roots', 'list_folder', 'get_metadata', 'search'}
        for instance in spec.tools:
            tool = get_tool(instance.type)
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertEqual({function.name for function in tool.functions(ctx, instance)}, expected_functions)

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

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for canonical activity output from the headless agent runner."""

import io
import json

from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.management.commands.run_agent import Command
from apps.runner.run_agent import write_run_agent_activities
from libs.agent_spec import AgentConfigSpec, LLMSpec

from olib.py.django.test.cases import OTestCase


class TestRunAgentActivityOutput(OTestCase):
    def test_writer_emits_canonical_activity_json(self) -> None:
        """The runtime helper serializes lowercase activity fields and details."""
        backend = MemorySessionBackend(
            AgentConfigSpec(
                llm=LLMSpec(provider='openai', model='test-model'),
                system_prompt='Test.',
            ),
            user_id=1,
        )
        backend.create_activity(
            kind='status',
            status='succeeded',
            name='ready',
            summary='Ready',
            details={'message': 'ready'},
        )
        output = io.StringIO()

        write_run_agent_activities(backend, output)

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['kind'], 'status')
        self.assertEqual(records[0]['status'], 'succeeded')
        self.assertEqual(records[0]['details'], {'message': 'ready'})

    def test_management_command_describes_activity_output(self) -> None:
        """The management command help uses canonical activity terminology."""
        self.assertIn('session activities', Command.help)

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Functional inbox usecase scenarios driven by FakeProvider plans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.runner.backends.base import RecordedEvent
from apps.runner.usecases.scenarios import (
    UsecaseScenario,
    build_mock_client_factories,
    fake_provider_for_scenario,
    load_usecase_scenario,
)
from apps.runner.usecases.setup import build_memory_session_runner
from apps.sessions.models import AgentSessionEventKind, AgentSessionStatus

# isort: split

from libs.agent_spec import AgentConfigSpec
from libs.agent_specs import load_example
from libs.clients.clickup.mock import MockClickUpClient
from libs.clients.gmail.mock import MockGmailClient

from olib.py.django.test.cases import OTestCase
from olib.py.eval import EventLogWriter, RunPartition

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios' / 'functional'


class TestInboxFunctionalUsecases(OTestCase):
    def test_routes_obvious_junk_mail_to_spam(self) -> None:
        """Spam routing applies the planned label, marks the message as spam, and logs the run."""
        result = self._run_scenario('spam_route')

        gmail = result.gmail
        expect = result.scenario.expect
        message_id = expect['gmail']['spam'][0]
        labels_by_name = _label_ids_by_name(gmail)

        self.assertEqual(gmail.spam, expect['gmail']['spam'])
        self.assertEqual(gmail.archived, [])
        self.assertEqual(gmail.trashed, [])
        self.assertIn(labels_by_name[expect['gmail']['has_label_names'][0]], gmail.get_message(message_id)['labelIds'])
        self.assertIn('SPAM', gmail.get_message(message_id)['labelIds'])
        self.assertNotIn('INBOX', gmail.get_message(message_id)['labelIds'])
        self.assertEqual(result.clickup.created_tasks, [])
        self._assert_session_completed(result)

    def test_turns_actionable_mail_into_clickup_task(self) -> None:
        """Actionable mail creates one ClickUp task, applies the Gmail label, and logs the run."""
        result = self._run_scenario('todo_to_clickup')

        gmail = result.gmail
        clickup = result.clickup
        expect = result.scenario.expect
        message_id = expect['gmail']['labeled'][0]['message_id']
        labels_by_name = _label_ids_by_name(gmail)

        self.assertEqual(clickup.created_tasks, expect['clickup']['created_tasks'])
        self.assertEqual(gmail.spam, [])
        self.assertEqual(gmail.archived, [])
        self.assertIn(
            labels_by_name[expect['gmail']['labeled'][0]['label_name']], gmail.get_message(message_id)['labelIds']
        )
        self._assert_session_completed(result)

    def _run_scenario(self, scenario_name: str) -> _ScenarioResult:
        """Run one YAML scenario through the memory runner with seeded mocks and FakeProvider."""
        scenario = load_usecase_scenario(SCENARIO_DIR / f'{scenario_name}.yaml')
        client_factories, gmail, clickup = build_mock_client_factories(scenario)
        spec = _load_inbox_usecase_spec()

        with TemporaryDirectory() as temp_dir:
            partition = RunPartition(
                kind='usecase',
                suite='runner-inbox-functional',
                sample_id=scenario.id,
                model='fake',
                run_id='test',
            )
            log_writer = EventLogWriter(Path(temp_dir))
            backend, runner = build_memory_session_runner(
                spec=spec,
                client_factories=client_factories,
                partition=partition,
                log_writer=log_writer,
                prompt=scenario.prompt,
            )

            with patch('apps.runner.loop.make_provider', return_value=fake_provider_for_scenario(scenario)):
                runner.run()

            log_path = log_writer.path_for(partition)
            log_text = log_path.read_text(encoding='utf-8')

        return _ScenarioResult(
            scenario=scenario,
            gmail=gmail,
            clickup=clickup,
            events=backend.events(),
            status=backend.get_status(),
            log_text=log_text,
        )

    def _assert_session_completed(self, result: _ScenarioResult) -> None:
        """Assert the memory runner completed the planned turn without queue state coupling."""
        kinds = [event.kind for event in result.events]
        expected_tool_calls = result.scenario.expect['tool_calls']
        actual_tool_calls = [
            event.payload['instance_id'] + '__' + event.payload['function']
            for event in result.events
            if event.kind == AgentSessionEventKind.TOOL_CALL
        ]

        self.assertEqual(result.status, AgentSessionStatus.WAITING)
        self.assertIn(AgentSessionEventKind.INPUT, kinds)
        self.assertIn(AgentSessionEventKind.OUTPUT, kinds)
        self.assertIn(AgentSessionEventKind.TOOL_RESULT, kinds)
        self.assertNotIn(AgentSessionEventKind.FAILURE, kinds)
        self.assertEqual(actual_tool_calls, expected_tool_calls)
        self.assertIn('"event": "session_event"', result.log_text)
        self.assertGreater(len(result.log_text.strip()), 0)


@dataclass(frozen=True)
class _ScenarioResult:
    """Bundle runner outputs needed by the scenario-specific assertions."""

    scenario: UsecaseScenario
    gmail: MockGmailClient
    clickup: MockClickUpClient
    events: list[RecordedEvent]
    status: str
    log_text: str


def _load_inbox_usecase_spec() -> AgentConfigSpec:
    """Load the inbox example spec and permit the spam action used by the functional route."""
    spec = load_example('inbox-triage-usecase')
    tools = [
        (
            tool.model_copy(update={'allow': [*tool.allow, 'mark_spam']})
            if tool.id == 'gmail' and 'mark_spam' not in tool.allow
            else tool
        )
        for tool in spec.tools
    ]
    return spec.model_copy(update={'tools': tools})


def _label_ids_by_name(client: MockGmailClient) -> dict[str, str]:
    """Return Gmail label ids keyed by display name for final state assertions."""
    return {str(label['name']): str(label['id']) for label in client.list_labels()}

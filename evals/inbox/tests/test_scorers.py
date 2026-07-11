# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# pylint: disable=import-error,wrong-import-position

import sys
import unittest
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[3] / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from libs.clients.clickup.mock import MockClickUpClient
from libs.clients.gmail.mock import MockGmailClient

from evals.inbox.scorers import score_inbox_state


class TestInboxScorers(unittest.TestCase):
    """Unit tests for inbox eval scoring."""

    def test_score_inbox_state_averages_expected_axes(self) -> None:
        """Scorer averages Gmail label, spam, ClickUp task, and tool-call axes."""
        gmail = MockGmailClient(token_supplier=lambda: None, config={})
        gmail.seed_message('msg-1', labels=('INBOX',), message={'subject': 'Action'})
        clickup = MockClickUpClient(token_supplier=lambda: None, config={})
        clickup.seed_list('space-1', {'id': 'list-1', 'name': 'Inbox'})

        label_id = gmail.create_label('ClickUp')['id']
        gmail.modify_labels('msg-1', add=(label_id,))
        clickup.create_task(list_id='list-1', name='Prepare launch checklist', description='From msg-1')

        score = score_inbox_state(
            expect={
                'tool_calls': ['gmail__list', 'gmail__read', 'clickup__create_task', 'gmail__label'],
                'gmail': {
                    'labeled': [{'message_id': 'msg-1', 'label_name': 'ClickUp'}],
                    'spam': ['msg-1'],
                },
                'clickup': {
                    'created_tasks': [
                        {
                            'id': 'mock-task-1',
                            'list_id': 'list-1',
                            'name': 'Prepare launch checklist',
                            'description': 'From msg-1',
                        },
                    ],
                },
            },
            gmail=gmail,
            clickup=clickup,
            tool_calls=['gmail__list', 'gmail__read', 'clickup__create_task', 'gmail__label'],
        )

        self.assertEqual(score.value, 0.75)
        self.assertEqual(
            score.axes,
            {
                'tool_calls': 1.0,
                'gmail.labeled': 1.0,
                'gmail.spam': 0.0,
                'clickup.created_tasks': 1.0,
            },
        )

    def test_required_tool_calls_is_subset_match(self) -> None:
        """required_tool_calls passes when each expected tool appears in the trace."""
        gmail = MockGmailClient(token_supplier=lambda: None, config={})
        clickup = MockClickUpClient(token_supplier=lambda: None, config={})
        score = score_inbox_state(
            expect={'required_tool_calls': ['gmail__list']},
            gmail=gmail,
            clickup=clickup,
            tool_calls=['gmail__list', 'gmail__read'],
        )
        self.assertEqual(score.value, 1.0)
        self.assertEqual(score.axes, {'required_tool_calls': 1.0})

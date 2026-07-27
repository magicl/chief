# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Regression tests for the project-root inbox eval runner."""

import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from apps.runner.backends.base import RecordedActivity
from libs.providers.llm.base import StreamResult
from libs.providers.llm.fake_provider import FakeProvider

from evals.inbox.runner import (
    InboxSampleRunner,
    _raise_for_missing_credentials,
    _tool_calls_from_activities,
)
from olib.py.django.test.cases import OTestCase
from olib.py.eval import EvalAbortError, RunPartition, Sample


class TestInboxEvalRunnerActivities(OTestCase):
    @staticmethod
    def _activity(*, kind: str, status: str, details: dict[str, object]) -> RecordedActivity:
        """Build one canonical activity for eval helper tests."""
        return RecordedActivity(
            id=uuid4(),
            session_id=uuid4(),
            parent_id=None,
            seq=1,
            revision=1,
            kind=kind,
            status=status,
            name=kind,
            summary='',
            details=details,
        )

    def test_activity_helpers_read_lowercase_kind_status_and_details(self) -> None:
        """Eval scoring and credential aborts consume canonical activity fields."""
        tool = self._activity(
            kind='tool',
            status='succeeded',
            details={'instance_id': 'gmail', 'function': 'list'},
        )
        failure = self._activity(
            kind='failure',
            status='failed',
            details={
                'code': 'missing_openai_credentials',
                'message': 'OpenAI credentials missing',
            },
        )

        self.assertEqual(_tool_calls_from_activities([tool, failure]), ['gmail__list'])
        with self.assertRaisesMessage(EvalAbortError, 'OpenAI credentials missing'):
            _raise_for_missing_credentials([tool, failure])

    def test_run_sample_emits_only_canonical_activity_observability(self) -> None:
        """An imported inbox runner executes and writes canonical activity records."""
        sample = Sample(
            id='activity-observability',
            payload={
                'scenario': {
                    'id': 'activity-observability',
                    'prompt': 'Say done.',
                    'expect': {},
                }
            },
        )
        partition = RunPartition(
            kind='eval',
            suite='inbox',
            sample_id=sample.id,
            model='openai/test-model',
            run_id='activity-run',
        )

        with TemporaryDirectory() as temp_dir:
            runner = InboxSampleRunner(log_root=temp_dir)
            with patch(
                'evals.inbox.runner._make_checked_provider',
                return_value=FakeProvider.for_responses([StreamResult(content='done')]),
            ):
                score = runner.run_sample(sample, model=partition.model, partition=partition)
            records = [
                json.loads(line)
                for line in runner.log_writer.path_for(partition).read_text(encoding='utf-8').splitlines()
            ]

        self.assertEqual(score.value, 1.0)
        self.assertEqual(
            {record['event'] for record in records},
            {'generate_start', 'generate_end', 'session_activity'},
        )
        activities = [record['record'] for record in records if record['event'] == 'session_activity']
        self.assertEqual({activity['kind'] for activity in activities}, {'input', 'llm', 'output'})
        self.assertEqual(
            {activity['status'] for activity in activities},
            {'running', 'succeeded'},
        )

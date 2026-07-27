# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for migrating flat session events into hierarchical activities."""

from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from typing import Any
from uuid import uuid4

from apps.sessions.models import AgentSessionActivity
from apps.sessions.tests.base import make_test_session
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


class TestMigrateEventsToActivities(OTransactionTestCase):
    """Exercise the data-migration forwards function against legacy rows."""

    historical_apps: Any
    Event: Any
    Activity: Any
    _created_event_table: bool

    @classmethod
    def setUpClass(cls) -> None:
        """Create only the historical event table needed by migration-unit tests."""
        super().setUpClass()
        executor = MigrationExecutor(connection)
        cls.historical_apps = executor.loader.project_state(
            [('agent_sessions', '0006_hierarchical_session_activities')]
        ).apps
        cls.Event = cls.historical_apps.get_model('agent_sessions', 'AgentSessionEvent')
        cls.Activity = cls.historical_apps.get_model('agent_sessions', 'AgentSessionActivity')
        cls._created_event_table = cls.Event._meta.db_table not in connection.introspection.table_names()
        if cls._created_event_table:
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(cls.Event)

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop the temporary historical event table after migration-unit tests."""
        if cls._created_event_table:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(cls.Event)
        super().tearDownClass()

    def setUp(self) -> None:
        """Load the migration helpers and historical model-compatible classes."""
        super().setUp()
        migration = import_module('apps.sessions.migrations.0007_migrate_events_to_activities')
        self.forwards = migration.forwards
        self.backwards = migration.backwards
        self.Event.objects.all().delete()

    def tearDown(self) -> None:
        """Clear rows unknown to the current registry before Django flushes."""
        self.Event.objects.all().delete()
        super().tearDown()

    def _run_forwards(self) -> None:
        """Invoke the migration using its historical Django app registry."""
        self.forwards(self.historical_apps, connection.schema_editor())

    def test_pairs_tool_call_and_result(self) -> None:
        """A matching result completes one tool activity rooted at the call row."""
        session = make_test_session('mig-pair')
        call_id = str(uuid4())
        call_id_pk = uuid4()
        result_id_pk = uuid4()
        call_created_at = timezone.now() - timedelta(days=2)
        result_created_at = call_created_at + timedelta(seconds=1)
        call = self.Event.objects.create(
            id=call_id_pk,
            session_id=session.id,
            seq=3,
            kind='TOOL_CALL',
            payload={
                'call_id': call_id,
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {'timezone': 'UTC'},
                'trace': {'source': 'legacy'},
            },
            latency_ms=2,
        )
        self.Event.objects.filter(pk=call.pk).update(created_at=call_created_at)
        result = self.Event.objects.create(
            id=result_id_pk,
            session_id=session.id,
            seq=4,
            kind='TOOL_RESULT',
            payload={
                'call_id': call_id,
                'content': '2026-01-01T00:00:00+00:00',
                'provider_metadata': {'cached': True},
            },
            latency_ms=5,
        )
        self.Event.objects.filter(pk=result.pk).update(created_at=result_created_at)

        self._run_forwards()

        tool = self.Activity.objects.get(session_id=session.id)
        self.assertEqual(tool.id, call_id_pk)
        self.assertEqual(tool.seq, 3)
        self.assertEqual(tool.kind, 'tool')
        self.assertEqual(tool.status, 'succeeded')
        self.assertEqual(tool.name, 'clock__now')
        self.assertEqual(tool.details['arguments'], {'timezone': 'UTC'})
        self.assertEqual(tool.details['trace'], {'source': 'legacy'})
        self.assertEqual(tool.details['result'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(tool.details['result_metadata'], {'provider_metadata': {'cached': True}})
        self.assertEqual(tool.details['result_latency_ms'], 5)
        self.assertEqual(tool.details['legacy_result_id'], str(result_id_pk))
        self.assertEqual(tool.details['legacy_result_seq'], 4)
        self.assertEqual(tool.details['legacy_result_created_at'], result_created_at.isoformat())
        self.assertEqual(tool.latency_ms, 5)
        self.assertEqual(tool.created_at, call_created_at)

    def test_structured_failed_pair_marks_tool_failed(self) -> None:
        """Structured failure markers make paired tool activities terminal failed."""
        session = make_test_session('mig-failed-pair')
        for offset, content in enumerate(('{"failure": {"code": "denied"}}', '{"ok": false}'), start=0):
            call_id = f'failed-{offset}'
            self.Event.objects.create(
                session_id=session.id,
                seq=(offset * 2) + 1,
                kind='TOOL_CALL',
                payload={'call_id': call_id, 'tool': 'clock', 'function': 'now', 'arguments': {}},
            )
            self.Event.objects.create(
                session_id=session.id,
                seq=(offset * 2) + 2,
                kind='TOOL_RESULT',
                payload={'call_id': call_id, 'content': content},
            )

        self._run_forwards()

        self.assertEqual(
            list(self.Activity.objects.filter(session_id=session.id).order_by('seq').values_list('status', flat=True)),
            ['failed', 'failed'],
        )

    def test_orphan_tool_call_becomes_failed_advisory(self) -> None:
        """An unmatched call remains visible as a failed legacy tool activity."""
        session = make_test_session('mig-orphan-call')
        self.Event.objects.create(
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={'call_id': 'missing', 'instance_id': 'clock', 'function': 'now', 'arguments': {}},
        )

        self._run_forwards()

        tool = self.Activity.objects.get(session_id=session.id)
        self.assertEqual(tool.kind, 'tool')
        self.assertEqual(tool.status, 'failed')
        self.assertTrue(tool.details['legacy_orphan'])

    def test_orphan_tool_result_becomes_status_advisory(self) -> None:
        """An unmatched result remains visible with its identity and latency."""
        session = make_test_session('mig-orphan-result')
        event_id = uuid4()
        self.Event.objects.create(
            id=event_id,
            session_id=session.id,
            seq=8,
            kind='TOOL_RESULT',
            payload={'call_id': 'missing', 'content': 'late'},
            latency_ms=17,
        )

        self._run_forwards()

        row = self.Activity.objects.get(session_id=session.id)
        self.assertEqual(row.id, event_id)
        self.assertEqual(row.seq, 8)
        self.assertEqual(row.kind, 'status')
        self.assertEqual(row.status, 'succeeded')
        self.assertTrue(row.details['legacy_orphan_tool_result'])
        self.assertEqual(row.details['content'], 'late')
        self.assertEqual(row.latency_ms, 17)

    def test_preserves_input_output_usage_identity_and_timestamp(self) -> None:
        """Messages retain content, usage metadata, IDs, sequence, and creation time."""
        session = make_test_session('mig-usage')
        input_id = uuid4()
        output_id = uuid4()
        created_at = timezone.now() - timedelta(days=1)
        self.Event.objects.create(
            id=input_id,
            session_id=session.id,
            seq=2,
            kind='INPUT',
            payload={'content': 'hi', 'source': 'chat'},
        )
        output = self.Event.objects.create(
            id=output_id,
            session_id=session.id,
            seq=5,
            kind='OUTPUT',
            payload={'content': 'hello', 'format': 'markdown'},
            model='gpt-test',
            input_tokens=10,
            output_tokens=2,
            cost_usd=Decimal('0.001000'),
            latency_ms=21,
        )
        self.Event.objects.filter(pk=output.pk).update(created_at=created_at)

        self._run_forwards()

        input_row = self.Activity.objects.get(pk=input_id)
        output_row = self.Activity.objects.get(pk=output_id)
        self.assertEqual((input_row.kind, input_row.status, input_row.name), ('input', 'succeeded', 'input'))
        self.assertEqual(input_row.details, {'content': 'hi', 'source': 'chat'})
        self.assertEqual((output_row.seq, output_row.kind, output_row.name), (5, 'output', 'output'))
        self.assertEqual(output_row.details, {'content': 'hello', 'format': 'markdown'})
        self.assertEqual(output_row.model, 'gpt-test')
        self.assertEqual(output_row.input_tokens, 10)
        self.assertEqual(output_row.output_tokens, 2)
        self.assertEqual(output_row.cost_usd, Decimal('0.001000'))
        self.assertEqual(output_row.latency_ms, 21)
        self.assertEqual(output_row.created_at, created_at)

    def test_failure_payload_is_sanitized_for_storage_and_streaming(self) -> None:
        """Legacy failure diagnostics cannot enter canonical rows or SSE payloads."""
        session = make_test_session('mig-failure-sanitize')
        event_id = uuid4()
        secret = 'Authorization: Bearer migration-secret-value'
        self.Event.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='FAILURE',
            payload={
                'message': f'provider failed at https://user:pass@example.invalid/?token={secret}',
                'traceback': f'Traceback with {secret}',
                'Authorization': secret,
                'url': f'https://example.invalid/?credential={secret}',
                'code': 'provider_failure',
                'unknown': {'nested': secret},
            },
        )

        self._run_forwards()

        row = self.Activity.objects.get(pk=event_id)
        current_row = AgentSessionActivity.objects.get(pk=event_id)
        self.assertEqual((row.kind, row.status, row.name), ('failure', 'failed', 'failure'))
        self.assertEqual(row.summary, 'Historical session failure')
        self.assertEqual(
            row.details,
            {
                'legacy_failure': True,
                'message': 'Historical session failure',
                'code': 'provider_failure',
            },
        )
        serialized = str(current_row.to_stream_dict())
        self.assertNotIn(secret, serialized)
        self.assertNotIn('example.invalid', serialized)
        self.assertNotIn('traceback', serialized.lower())
        self.assertNotIn('Authorization', serialized)
        self.assertNotIn('unknown', serialized)

    def test_failure_unsafe_code_and_unknown_fields_are_not_retained(self) -> None:
        """Credential-shaped and otherwise unknown codes never survive sanitization."""
        session = make_test_session('mig-failure-unsafe-code')
        unknown_codes = (
            'supersecrettoken',
            'apikey1234567890',
            'provider_timeout',
            'provider_timeout:https_secret',
        )
        event_ids = []
        for seq, code in enumerate(unknown_codes, start=1):
            event_id = uuid4()
            event_ids.append(event_id)
            self.Event.objects.create(
                id=event_id,
                session_id=session.id,
                seq=seq,
                kind='FAILURE',
                payload={
                    'code': code,
                    f'field_{code}': code,
                    'future_diagnostic': [code],
                },
            )

        self._run_forwards()

        rows = list(self.Activity.objects.filter(pk__in=event_ids).order_by('seq'))
        for row, unknown_code in zip(rows, unknown_codes, strict=True):
            stream_values = AgentSessionActivity.objects.get(pk=row.pk).to_stream_dict()
            self.assertEqual(
                row.details,
                {
                    'legacy_failure': True,
                    'message': 'Historical session failure',
                    'code': 'legacy_failure',
                },
            )
            self.assertNotIn(unknown_code, str(row.__dict__))
            self.assertNotIn(unknown_code, str(stream_values))

    def test_failure_known_code_allowlist_is_preserved(self) -> None:
        """Every code emitted by the legacy runner/provider remains identifiable."""
        session = make_test_session('mig-failure-known-codes')
        known_codes = (
            'missing_openai_credentials',
            'missing_anthropic_credentials',
            'unsupported_llm_provider',
            'credential_storage_misconfigured',
            'session_iteration_limit',
            'session_spend_limit',
            'agent_daily_spend_limit',
            'agent_monthly_spend_limit',
            'user_daily_spend_limit',
            'user_monthly_spend_limit',
            'provider_failure',
            'unexpected_failure',
        )
        event_ids = []
        for seq, code in enumerate(known_codes, start=1):
            event_id = uuid4()
            event_ids.append(event_id)
            self.Event.objects.create(
                id=event_id,
                session_id=session.id,
                seq=seq,
                kind='FAILURE',
                payload={'code': code, 'message': 'raw diagnostic'},
            )

        self._run_forwards()

        rows = list(self.Activity.objects.filter(pk__in=event_ids).order_by('seq'))
        self.assertEqual([row.details['code'] for row in rows], list(known_codes))

    def test_matching_never_crosses_session_boundary(self) -> None:
        """Identical call IDs in different sessions remain independent orphans."""
        call_session = make_test_session('mig-cross-call')
        result_session = make_test_session('mig-cross-result')
        self.Event.objects.create(
            session_id=call_session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={'call_id': 'shared', 'instance_id': 'clock', 'function': 'now', 'arguments': {}},
        )
        self.Event.objects.create(
            session_id=result_session.id,
            seq=1,
            kind='TOOL_RESULT',
            payload={'call_id': 'shared', 'content': 'wrong session'},
        )

        self._run_forwards()

        call = self.Activity.objects.get(session_id=call_session.id)
        result = self.Activity.objects.get(session_id=result_session.id)
        self.assertEqual((call.kind, call.status), ('tool', 'failed'))
        self.assertTrue(call.details['legacy_orphan'])
        self.assertEqual(result.kind, 'status')
        self.assertTrue(result.details['legacy_orphan_tool_result'])

    def test_duplicate_call_ids_pair_in_sequence_and_preserve_unmatched_call(self) -> None:
        """Duplicate IDs pair deterministically while every unmatched call survives."""
        session = make_test_session('mig-duplicate')
        first_id = uuid4()
        second_id = uuid4()
        self.Event.objects.create(
            id=first_id,
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={'call_id': 'duplicate', 'instance_id': 'clock', 'function': 'first', 'arguments': {}},
        )
        self.Event.objects.create(
            id=second_id,
            session_id=session.id,
            seq=2,
            kind='TOOL_CALL',
            payload={'call_id': 'duplicate', 'instance_id': 'clock', 'function': 'second', 'arguments': {}},
        )
        self.Event.objects.create(
            session_id=session.id,
            seq=3,
            kind='TOOL_RESULT',
            payload={'call_id': 'duplicate', 'content': 'one result'},
        )

        self._run_forwards()

        first = self.Activity.objects.get(pk=first_id)
        second = self.Activity.objects.get(pk=second_id)
        self.assertEqual(first.status, 'succeeded')
        self.assertEqual(first.details['result'], 'one result')
        self.assertEqual(second.status, 'failed')
        self.assertTrue(second.details['legacy_orphan'])
        self.assertEqual(self.Activity.objects.filter(session_id=session.id).count(), 2)

    def test_malformed_and_unknown_rows_become_safe_advisories(self) -> None:
        """Malformed payloads and future kinds remain queryable without aborting."""
        session = make_test_session('mig-malformed')
        malformed_id = uuid4()
        unknown_id = uuid4()
        self.Event.objects.create(
            id=malformed_id,
            session_id=session.id,
            seq=1,
            kind='TOOL_RESULT',
            payload=['not', 'an', 'object'],
        )
        self.Event.objects.create(
            id=unknown_id,
            session_id=session.id,
            seq=2,
            kind='FUTURE_KIND',
            payload='opaque legacy payload',
        )

        self._run_forwards()

        malformed = self.Activity.objects.get(pk=malformed_id)
        unknown = self.Activity.objects.get(pk=unknown_id)
        self.assertEqual(malformed.kind, 'status')
        self.assertTrue(malformed.details['legacy_malformed_payload'])
        self.assertEqual(malformed.details['legacy_payload'], ['not', 'an', 'object'])
        self.assertEqual(unknown.kind, 'status')
        self.assertEqual(unknown.name, 'legacy_event')
        self.assertEqual(unknown.details['legacy_kind'], 'FUTURE_KIND')
        self.assertEqual(unknown.details['legacy_payload'], 'opaque legacy payload')

    def test_forwards_tolerates_dual_written_rows_and_repeat_invocation(self) -> None:
        """A shared dual-write identity converges to one activity across reruns."""
        session = make_test_session('mig-idempotent')
        shared_id = uuid4()
        self.Event.objects.create(
            id=shared_id,
            session_id=session.id,
            seq=1,
            kind='INPUT',
            payload={'content': 'dual-written'},
        )
        self.Activity.objects.create(
            id=shared_id,
            session_id=session.id,
            seq=1,
            revision=3,
            kind='input',
            status='succeeded',
            name='input',
            summary='dual-written',
            details={'content': 'dual-written'},
        )

        self._run_forwards()
        self._run_forwards()

        row = self.Activity.objects.get(pk=shared_id)
        self.assertEqual(row.seq, 1)
        self.assertEqual(row.revision, 3)
        self.assertEqual(row.details, {'content': 'dual-written'})
        self.assertEqual(self.Activity.objects.filter(session_id=session.id).count(), 1)
        self.assertTrue(self.Event.objects.filter(pk=shared_id).exists())

    def test_consumed_result_slot_preserves_dual_input_and_legacy_identity(self) -> None:
        """A shifted dual input survives behind the exact later legacy identity."""
        session = make_test_session('mig-shifted-input')
        call_id = 'shifted-input-call'
        call_event_id = uuid4()
        result_event_id = uuid4()
        input_event_id = uuid4()
        self.Event.objects.create(
            id=call_event_id,
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={
                'call_id': call_id,
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
            },
        )
        self.Event.objects.create(
            id=result_event_id,
            session_id=session.id,
            seq=2,
            kind='TOOL_RESULT',
            payload={'call_id': call_id, 'content': 'done'},
        )
        self.Event.objects.create(
            id=input_event_id,
            session_id=session.id,
            seq=3,
            kind='INPUT',
            payload={'content': 'next turn'},
        )
        dual_input_id = uuid4()
        self.Activity.objects.create(
            id=dual_input_id,
            session_id=session.id,
            seq=2,
            kind='input',
            status='succeeded',
            name='input',
            summary='next turn',
            details={'content': 'next turn'},
        )

        self._run_forwards()
        self._run_forwards()

        inputs = list(self.Activity.objects.filter(session_id=session.id, kind='input').order_by('seq'))
        self.assertEqual(len(inputs), 2)
        self.assertEqual(inputs[0].id, input_event_id)
        self.assertEqual(inputs[0].seq, 3)
        self.assertEqual(inputs[0].details, {'content': 'next turn'})
        self.assertEqual(inputs[1].id, dual_input_id)
        self.assertEqual(inputs[1].seq, 4)
        self.assertEqual(inputs[1].details, {'content': 'next turn'})
        self.assertFalse(self.Activity.objects.filter(pk=result_event_id).exists())

    def test_unrelated_same_kind_and_name_survives_sequence_collision(self) -> None:
        """A same-kind sequence occupant is preserved when its fingerprint differs."""
        session = make_test_session('mig-unrelated-input')
        event_id = uuid4()
        occupant_id = uuid4()
        self.Event.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='INPUT',
            payload={'content': 'legacy input'},
        )
        self.Activity.objects.create(
            id=occupant_id,
            session_id=session.id,
            seq=1,
            kind='input',
            status='succeeded',
            name='input',
            summary='independent input',
            details={'content': 'independent input'},
        )

        self._run_forwards()
        self._run_forwards()

        migrated = self.Activity.objects.get(pk=event_id)
        occupant = self.Activity.objects.get(pk=occupant_id)
        self.assertEqual(migrated.seq, 1)
        self.assertEqual(migrated.details, {'content': 'legacy input'})
        self.assertEqual(occupant.seq, 2)
        self.assertEqual(occupant.details, {'content': 'independent input'})

    def test_identical_content_rows_are_never_adopted_by_fingerprint(self) -> None:
        """Content equality does not replace exact legacy-event identity."""
        session = make_test_session('mig-identical-content')
        event_id = uuid4()
        existing_ids = [uuid4(), uuid4()]
        self.Event.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='INPUT',
            payload={'content': 'same'},
        )
        for seq, activity_id in enumerate(existing_ids, start=2):
            self.Activity.objects.create(
                id=activity_id,
                session_id=session.id,
                seq=seq,
                kind='input',
                status='succeeded',
                name='input',
                summary='same',
                details={'content': 'same'},
            )

        self._run_forwards()
        self._run_forwards()

        rows = list(self.Activity.objects.filter(session_id=session.id).order_by('seq'))
        self.assertEqual([row.id for row in rows], [event_id, *existing_ids])
        self.assertEqual([row.seq for row in rows], [1, 2, 3])

    def test_legacy_chronology_moves_preexisting_rows_to_trailing_sequences(self) -> None:
        """Legacy sequence wins while displaced activities keep relative order."""
        session = make_test_session('mig-global-seq')
        event_id = uuid4()
        older_id = uuid4()
        newer_id = uuid4()
        self.Event.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='INPUT',
            payload={'content': 'historical first'},
        )
        self.Activity.objects.create(
            id=older_id,
            session_id=session.id,
            seq=1,
            kind='status',
            status='succeeded',
            name='older',
            details={'order': 1},
        )
        self.Activity.objects.create(
            id=newer_id,
            session_id=session.id,
            seq=2,
            kind='status',
            status='succeeded',
            name='newer',
            details={'order': 2},
        )

        self._run_forwards()
        self._run_forwards()

        rows = list(self.Activity.objects.filter(session_id=session.id).order_by('seq'))
        self.assertEqual([row.id for row in rows], [event_id, older_id, newer_id])
        self.assertEqual([row.seq for row in rows], [1, 2, 3])
        self.assertEqual([row.details.get('order') for row in rows[1:]], [1, 2])

    def test_session_reconciliation_uses_bounded_queries(self) -> None:
        """Per-session query count stays bounded as event and activity counts grow."""
        session = make_test_session('mig-query-bound')
        for seq in range(1, 41):
            self.Event.objects.create(
                session_id=session.id,
                seq=seq,
                kind='INPUT',
                payload={'content': f'legacy-{seq}'},
            )
            self.Activity.objects.create(
                session_id=session.id,
                seq=seq,
                kind='status',
                status='succeeded',
                name=f'existing-{seq}',
                details={'order': seq},
            )

        with CaptureQueriesContext(connection) as queries:
            self._run_forwards()

        # TransactionTestCase adds constant transaction-control statements around bulk writes.
        self.assertLessEqual(len(queries), 15)
        rows = list(self.Activity.objects.filter(session_id=session.id).order_by('seq'))
        self.assertEqual([row.seq for row in rows], list(range(1, 81)))
        self.assertEqual([row.kind for row in rows[:40]], ['input'] * 40)
        self.assertEqual([row.details['order'] for row in rows[40:]], list(range(1, 41)))

    def test_pair_converges_preexisting_identity_and_sequence_duplicates(self) -> None:
        """Paired dual writes converge while an unrelated activity remains untouched."""
        session = make_test_session('mig-pair-converge')
        call_event_id = uuid4()
        result_event_id = uuid4()
        call_id = 'converge-call'
        self.Event.objects.create(
            id=call_event_id,
            session_id=session.id,
            seq=1,
            kind='TOOL_CALL',
            payload={
                'call_id': call_id,
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
            },
        )
        self.Event.objects.create(
            id=result_event_id,
            session_id=session.id,
            seq=2,
            kind='TOOL_RESULT',
            payload={'call_id': call_id, 'content': 'unified result'},
            latency_ms=11,
        )
        duplicate_call_id = uuid4()
        result_slot_id = uuid4()
        unrelated_id = uuid4()
        self.Activity.objects.create(
            id=duplicate_call_id,
            session_id=session.id,
            seq=1,
            kind='tool',
            status='running',
            name='clock__now',
            details={
                'call_id': call_id,
                'instance_id': 'clock',
                'function': 'now',
                'arguments': {},
            },
        )
        self.Activity.objects.create(
            id=result_slot_id,
            session_id=session.id,
            seq=2,
            kind='status',
            status='succeeded',
            name='independent-checkpoint',
            details={'keep': 'result-slot-data'},
        )
        self.Activity.objects.create(
            id=unrelated_id,
            session_id=session.id,
            seq=3,
            kind='status',
            status='succeeded',
            name='checkpoint',
            details={'keep': 'unchanged'},
        )
        self.Activity.objects.create(
            id=call_event_id,
            session_id=session.id,
            seq=10,
            kind='tool',
            status='running',
            name='clock__now',
            details={'call_id': call_id, 'stale': True},
        )
        self.Activity.objects.create(
            id=result_event_id,
            session_id=session.id,
            seq=11,
            kind='status',
            status='succeeded',
            name='legacy_tool_result',
            details={'call_id': call_id, 'content': 'stale identity result'},
        )

        self._run_forwards()
        self._run_forwards()

        tools = list(self.Activity.objects.filter(session_id=session.id, kind='tool'))
        self.assertEqual(len(tools), 2)
        migrated_tool = self.Activity.objects.get(pk=call_event_id)
        duplicate_tool = self.Activity.objects.get(pk=duplicate_call_id)
        self.assertEqual(migrated_tool.seq, 1)
        self.assertEqual(migrated_tool.status, 'succeeded')
        self.assertEqual(migrated_tool.details['result'], 'unified result')
        self.assertEqual(migrated_tool.latency_ms, 11)
        self.assertEqual(duplicate_tool.seq, 2)
        self.assertEqual(duplicate_tool.status, 'running')
        self.assertNotIn('result', duplicate_tool.details)
        self.assertFalse(self.Activity.objects.filter(pk=result_event_id).exists())
        result_slot = self.Activity.objects.get(pk=result_slot_id)
        self.assertEqual(result_slot.seq, 3)
        self.assertEqual(result_slot.name, 'independent-checkpoint')
        self.assertEqual(result_slot.details, {'keep': 'result-slot-data'})
        unrelated = self.Activity.objects.get(pk=unrelated_id)
        self.assertEqual(unrelated.seq, 4)
        self.assertEqual(unrelated.name, 'checkpoint')
        self.assertEqual(unrelated.details, {'keep': 'unchanged'})
        self.assertEqual(self.Activity.objects.filter(session_id=session.id).count(), 4)

    def test_malformed_tool_calls_become_status_advisories(self) -> None:
        """Invalid call payload shapes retain raw data without posing as tools."""
        session = make_test_session('mig-malformed-calls')
        raw_payloads = (
            ['not', 'an', 'object'],
            {'call_id': 'missing-structure', 'instance_id': 'clock'},
        )
        event_ids = []
        for seq, payload in enumerate(raw_payloads, start=1):
            event_id = uuid4()
            event_ids.append(event_id)
            self.Event.objects.create(
                id=event_id,
                session_id=session.id,
                seq=seq,
                kind='TOOL_CALL',
                payload=payload,
            )

        self._run_forwards()

        rows = list(self.Activity.objects.filter(pk__in=event_ids).order_by('seq'))
        self.assertEqual([row.kind for row in rows], ['status', 'status'])
        self.assertEqual([row.name for row in rows], ['legacy_malformed_tool_call'] * 2)
        self.assertEqual([row.details['legacy_payload'] for row in rows], list(raw_payloads))
        self.assertTrue(all(row.details['legacy_malformed_tool_call'] for row in rows))

    def test_oversized_tool_identity_becomes_status_advisory(self) -> None:
        """Tool identities that exceed activity fields remain raw status data."""
        session = make_test_session('mig-oversized-tool')
        raw_payloads = (
            {
                'call_id': 'long-name',
                'instance_id': 'i' * 250,
                'function': 'function',
                'arguments': {},
            },
            {
                'call_id': 'long-summary',
                'tool': 'clock',
                'function': 'f' * 513,
                'arguments': {},
            },
        )
        event_ids = []
        for seq, payload in enumerate(raw_payloads, start=1):
            event_id = uuid4()
            event_ids.append(event_id)
            self.Event.objects.create(
                id=event_id,
                session_id=session.id,
                seq=seq,
                kind='TOOL_CALL',
                payload=payload,
            )

        self._run_forwards()

        rows = list(self.Activity.objects.filter(pk__in=event_ids).order_by('seq'))
        self.assertEqual([row.kind for row in rows], ['status', 'status'])
        self.assertEqual([row.name for row in rows], ['legacy_malformed_tool_call'] * 2)
        self.assertEqual([row.details['legacy_payload'] for row in rows], list(raw_payloads))
        self.assertTrue(all(row.details['legacy_malformed_tool_call'] for row in rows))
        self.assertTrue(all(len(row.name) <= 255 for row in rows))
        self.assertTrue(all(len(row.summary) <= 512 for row in rows))

    def test_malformed_known_event_payloads_become_status_advisories(self) -> None:
        """Known kinds with invalid payloads retain identity and raw legacy JSON."""
        session = make_test_session('mig-malformed-known')
        malformed = (
            ('INPUT', ['input']),
            ('INPUT', {'content': ['not', 'text']}),
            ('OUTPUT', {'format': 'markdown'}),
            ('OUTPUT', {'content': {'not': 'text'}}),
            ('FAILURE', 'failure text'),
            ('RESTART', ['restart']),
        )
        event_ids = []
        for seq, (kind, payload) in enumerate(malformed, start=1):
            event_id = uuid4()
            event_ids.append(event_id)
            self.Event.objects.create(
                id=event_id,
                session_id=session.id,
                seq=seq,
                kind=kind,
                payload=payload,
            )

        self._run_forwards()

        rows = list(self.Activity.objects.filter(pk__in=event_ids).order_by('seq'))
        self.assertEqual([row.id for row in rows], event_ids)
        self.assertEqual([row.seq for row in rows], [1, 2, 3, 4, 5, 6])
        self.assertEqual([row.kind for row in rows], ['status', 'status', 'status', 'status', 'failure', 'status'])
        self.assertEqual(
            [row.name for row in rows],
            [
                'legacy_malformed_event',
                'legacy_malformed_event',
                'legacy_malformed_event',
                'legacy_malformed_event',
                'failure',
                'legacy_malformed_event',
            ],
        )
        advisory_rows = [*rows[:4], rows[5]]
        advisory_payloads = [*malformed[:4], malformed[5]]
        self.assertEqual(
            [(row.details['legacy_kind'], row.details['legacy_payload']) for row in advisory_rows],
            advisory_payloads,
        )
        self.assertEqual(
            rows[4].details,
            {
                'legacy_failure': True,
                'message': 'Historical session failure',
                'code': 'legacy_failure',
            },
        )
        self.assertTrue(all(row.details['legacy_malformed_payload'] for row in advisory_rows))

    def test_malformed_results_do_not_complete_tool_calls(self) -> None:
        """Invalid result content remains advisory and leaves each call orphaned."""
        session = make_test_session('mig-malformed-results')
        malformed_results: tuple[dict[str, object], ...] = (
            {'call_id': 'missing-content'},
            {'call_id': 'non-string-content', 'content': {'ok': True}},
        )
        result_ids = []
        call_ids = []
        for offset, result_payload in enumerate(malformed_results):
            call_id = str(result_payload['call_id'])
            call_event_id = uuid4()
            result_event_id = uuid4()
            call_ids.append(call_event_id)
            result_ids.append(result_event_id)
            self.Event.objects.create(
                id=call_event_id,
                session_id=session.id,
                seq=(offset * 2) + 1,
                kind='TOOL_CALL',
                payload={
                    'call_id': call_id,
                    'instance_id': 'clock',
                    'function': 'now',
                    'arguments': {},
                },
            )
            self.Event.objects.create(
                id=result_event_id,
                session_id=session.id,
                seq=(offset * 2) + 2,
                kind='TOOL_RESULT',
                payload=result_payload,
            )

        self._run_forwards()

        calls = list(self.Activity.objects.filter(pk__in=call_ids).order_by('seq'))
        results = list(self.Activity.objects.filter(pk__in=result_ids).order_by('seq'))
        self.assertEqual([row.kind for row in calls], ['tool', 'tool'])
        self.assertEqual([row.status for row in calls], ['failed', 'failed'])
        self.assertTrue(all(row.details['legacy_orphan'] for row in calls))
        self.assertEqual([row.kind for row in results], ['status', 'status'])
        self.assertEqual([row.name for row in results], ['legacy_malformed_tool_result'] * 2)
        self.assertEqual(
            [row.details['legacy_payload'] for row in results],
            list(malformed_results),
        )

    def test_backwards_is_explicitly_irreversible(self) -> None:
        """Reverse migration refuses to fabricate consumed tool-result rows."""
        with self.assertRaisesMessage(RuntimeError, 'paired tool results cannot be reconstructed losslessly'):
            self.backwards(self.historical_apps, connection.schema_editor())


class TestHistoricalActivityMigrationExecutor(OTransactionTestCase):
    """Verify Django executes the data migration with historical model states."""

    def setUp(self) -> None:
        """Capture current migration state and the historical event model for cleanup."""
        super().setUp()
        self.recorder = MigrationRecorder(connection)
        self.original_migrations = set(
            self.recorder.migration_qs.filter(app='agent_sessions').values_list('name', flat=True)
        )
        executor = MigrationExecutor(connection)
        historical_apps = executor.loader.project_state(
            [('agent_sessions', '0009_activity_terminal_transition_index')]
        ).apps
        self.HistoricalEvent = historical_apps.get_model('agent_sessions', 'AgentSessionEvent')

    def tearDown(self) -> None:
        """Restore current schema and migration records even after assertion failures."""
        table_name = self.HistoricalEvent._meta.db_table
        if table_name in connection.introspection.table_names():
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(self.HistoricalEvent)
        self.recorder.migration_qs.filter(app='agent_sessions').delete()
        for migration_name in sorted(self.original_migrations):
            self.recorder.record_applied('agent_sessions', migration_name)
        restored_migrations = set(
            self.recorder.migration_qs.filter(app='agent_sessions').values_list('name', flat=True)
        )
        self.assertNotIn(table_name, connection.introspection.table_names())
        self.assertEqual(restored_migrations, self.original_migrations)
        super().tearDown()

    def test_fixture_starts_at_current_schema_without_legacy_table(self) -> None:
        """Executor tests begin from recorded 0010 state without legacy storage."""
        self.assertIn('0010_remove_agentsessionevent', self.original_migrations)
        self.assertNotIn(self.HistoricalEvent._meta.db_table, connection.introspection.table_names())

    def test_reverse_drop_is_blocked_before_recreating_legacy_table(self) -> None:
        """Reversing 0010 fails while the dropped table remains absent."""
        executor = MigrationExecutor(connection)

        with self.assertRaisesMessage(RuntimeError, 'legacy session event table drop is irreversible'):
            executor.migrate([('agent_sessions', '0009_activity_terminal_transition_index')])

        self.assertNotIn(self.HistoricalEvent._meta.db_table, connection.introspection.table_names())
        self.assertTrue(
            self.recorder.migration_qs.filter(
                app='agent_sessions',
                name='0010_remove_agentsessionevent',
            ).exists()
        )

    def test_migrates_0006_event_into_0007_activity(self) -> None:
        """MigrationExecutor applies the historical data cutover with old model state."""
        session = make_test_session('mig-executor')
        event_id = uuid4()
        executor = MigrationExecutor(connection)
        old_target = [('agent_sessions', '0006_hierarchical_session_activities')]
        old_state = executor.loader.project_state(old_target)
        old_apps = old_state.apps
        HistoricalEvent = old_apps.get_model('agent_sessions', 'AgentSessionEvent')
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HistoricalEvent)
        HistoricalEvent.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='INPUT',
            payload={'content': 'historical executor input'},
        )

        self.recorder.migration_qs.filter(
            app='agent_sessions',
            name='0007_migrate_events_to_activities',
        ).delete()
        migration = executor.loader.get_migration('agent_sessions', '0007_migrate_events_to_activities')
        new_state = executor.apply_migration(old_state, migration, fake=False)

        new_apps = new_state.apps
        HistoricalActivity = new_apps.get_model('agent_sessions', 'AgentSessionActivity')
        row = HistoricalActivity.objects.get(pk=event_id)
        self.assertEqual(row.session_id, session.id)
        self.assertEqual(row.seq, 1)
        self.assertEqual(row.kind, 'input')
        self.assertEqual(row.status, 'succeeded')
        self.assertEqual(row.details, {'content': 'historical executor input'})

    def test_executor_sanitizes_legacy_failure_diagnostics(self) -> None:
        """MigrationExecutor applies failure sanitization through historical states."""
        session = make_test_session('mig-executor-failure')
        event_id = uuid4()
        secret = 'Cookie: session=migration-executor-secret'
        executor = MigrationExecutor(connection)
        old_target = [('agent_sessions', '0006_hierarchical_session_activities')]
        old_state = executor.loader.project_state(old_target)
        old_apps = old_state.apps
        HistoricalEvent = old_apps.get_model('agent_sessions', 'AgentSessionEvent')
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(HistoricalEvent)
        HistoricalEvent.objects.create(
            id=event_id,
            session_id=session.id,
            seq=1,
            kind='FAILURE',
            payload={
                'message': secret,
                'traceback': secret,
                'headers': {'Cookie': secret},
                'code': 'supersecrettoken',
                'future': secret,
            },
        )

        self.recorder.migration_qs.filter(
            app='agent_sessions',
            name='0007_migrate_events_to_activities',
        ).delete()
        migration = executor.loader.get_migration('agent_sessions', '0007_migrate_events_to_activities')
        new_state = executor.apply_migration(old_state, migration, fake=False)

        HistoricalActivity = new_state.apps.get_model('agent_sessions', 'AgentSessionActivity')
        row = HistoricalActivity.objects.get(pk=event_id)
        self.assertEqual(row.summary, 'Historical session failure')
        self.assertEqual(
            row.details,
            {
                'legacy_failure': True,
                'message': 'Historical session failure',
                'code': 'legacy_failure',
            },
        )
        self.assertNotIn(secret, str(row.details))
        self.assertNotIn('supersecrettoken', str(row.__dict__))

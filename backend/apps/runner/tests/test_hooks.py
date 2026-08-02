# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""SessionRunner observability hook tests."""

import asyncio
import json
import logging
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from apps.runner.backends.base import RecordedActivity
from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.hooks import HookRegistry, HookSet
from apps.runner.loop import SessionRunner
from apps.runner.usecases.observability import build_observability_hooks
from apps.sessions.models import AgentSessionActivityKind, AgentSessionStatus
from libs.agent_spec import load_example
from libs.providers.llm.base import ProviderError, StreamResult
from libs.providers.llm.fake_provider import FakeProvider

from olib.py.django.test.cases import OTestCase
from olib.py.eval import EventLogWriter, RunPartition
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestSessionRunnerHooks(OTestCase):
    @staticmethod
    def _activity(*, kind: str, seq: int) -> RecordedActivity:
        """Build a minimal canonical activity snapshot for registry tests."""
        return RecordedActivity(
            id=uuid4(),
            session_id=uuid4(),
            parent_id=None,
            seq=seq,
            revision=1,
            kind=kind,
            status='succeeded',
            name=kind,
            summary=kind,
            details={},
        )

    def test_activity_hooks_fire_once_in_persistence_order(self) -> None:
        """Create and update hooks receive each canonical runner revision exactly once."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        tool_call = StreamResult(content='', tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'}])
        follow_up = StreamResult(content='done')
        observed: list[tuple[str, Any]] = []

        hooks = HookSet(
            on_run_start=lambda: observed.append(('run_start', None)),
            on_run_end=lambda: observed.append(('run_end', None)),
            on_generate_start=lambda messages, tool_definitions: observed.append(
                ('generate_start', (messages, tool_definitions)),
            ),
            on_generate_end=lambda result: observed.append(('generate_end', result)),
            on_tool_call_start=lambda call: observed.append(('tool_call_start', call)),
            on_tool_call_end=lambda call, result_content: observed.append(
                ('tool_call_end', (call, result_content)),
            ),
            on_activity_created=lambda activity: observed.append(
                ('create', (activity.kind, activity.status, activity.revision)),
            ),
            on_activity_updated=lambda activity: observed.append(
                ('update', (activity.kind, activity.status, activity.revision)),
            ),
            on_status=lambda status: observed.append(('status', status)),
        )

        runner = SessionRunner(backend)
        runner.add_hook(hooks)
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses([tool_call, follow_up]),
        ):
            runner.run()

        hook_names = [name for name, _payload in observed]
        self.assertEqual(hook_names.count('run_start'), 1)
        self.assertEqual(hook_names.count('run_end'), 1)
        self.assertEqual(hook_names.count('generate_start'), 2)
        self.assertEqual(hook_names.count('generate_end'), 2)
        self.assertEqual(hook_names.count('tool_call_start'), 1)
        self.assertEqual(hook_names.count('tool_call_end'), 1)
        activity_observations = [item for item in observed if item[0] in {'create', 'update'}]
        self.assertEqual(
            activity_observations,
            [
                ('create', (AgentSessionActivityKind.INPUT, 'succeeded', 1)),
                ('create', (AgentSessionActivityKind.LLM, 'running', 1)),
                # The tool-call turn has no assistant text, so it records no output row.
                ('create', (AgentSessionActivityKind.TOOL, 'running', 1)),
                ('update', (AgentSessionActivityKind.TOOL, 'succeeded', 2)),
                ('update', (AgentSessionActivityKind.LLM, 'succeeded', 2)),
                ('create', (AgentSessionActivityKind.LLM, 'running', 1)),
                ('create', (AgentSessionActivityKind.OUTPUT, 'succeeded', 1)),
                ('update', (AgentSessionActivityKind.LLM, 'succeeded', 2)),
            ],
        )
        self.assertIn(('status', AgentSessionStatus.WAITING), observed)

        tool_start = next(payload for name, payload in observed if name == 'tool_call_start')
        self.assertEqual(tool_start, {'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'})
        tool_end = next(payload for name, payload in observed if name == 'tool_call_end')
        ended_call, result_content = tool_end
        self.assertEqual(ended_call, tool_start)
        self.assertIn('T', result_content)
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)
        self.assertNotIn(
            AgentSessionActivityKind.FAILURE,
            [activity.kind for activity in backend.activities()],
        )

    def test_restart_failure_status_and_span_emit_canonical_revisions(self) -> None:
        """Restart/failure records and recorder status/span work use activity hooks."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        observed: list[tuple[str, str, str, int]] = []
        runner = SessionRunner(backend, emit_restart=True)
        runner.add_hook(
            HookSet(
                on_activity_created=lambda activity: observed.append(
                    ('create', activity.kind, activity.status, activity.revision)
                ),
                on_activity_updated=lambda activity: observed.append(
                    ('update', activity.kind, activity.status, activity.revision)
                ),
            )
        )

        runner.recorder.status_note(name='checkpoint', summary='Ready')
        with runner.recorder.span(name='prepare', summary='Preparing'):
            pass
        with patch(
            'apps.runner.loop.make_provider',
            return_value=FakeProvider.for_responses(
                [StreamResult(error=ProviderError(message='Unavailable', code='provider_unavailable'))]
            ),
        ):
            runner.run()

        self.assertEqual(
            observed,
            [
                ('create', AgentSessionActivityKind.STATUS, 'succeeded', 1),
                ('create', AgentSessionActivityKind.SPAN, 'running', 1),
                ('update', AgentSessionActivityKind.SPAN, 'succeeded', 2),
                ('create', AgentSessionActivityKind.RESTART, 'succeeded', 1),
                ('create', AgentSessionActivityKind.INPUT, 'succeeded', 1),
                ('create', AgentSessionActivityKind.LLM, 'running', 1),
                ('update', AgentSessionActivityKind.LLM, 'failed', 2),
                ('create', AgentSessionActivityKind.FAILURE, 'failed', 1),
            ],
        )

    def test_failed_create_does_not_fire_activity_hook(self) -> None:
        """A failed persistence attempt cannot publish a phantom create callback."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        original_create = backend.create_activity
        created_kinds: list[str] = []

        def fail_output_create(**kwargs: Any) -> RecordedActivity:
            """Fail only output persistence after input and LLM creation."""
            if kwargs['kind'] == AgentSessionActivityKind.OUTPUT:
                raise RuntimeError('output write failed')
            return original_create(**kwargs)

        backend.create_activity = fail_output_create  # type: ignore[method-assign]
        runner = SessionRunner(backend)
        runner.add_hook(HookSet(on_activity_created=lambda activity: created_kinds.append(activity.kind)))
        with (
            patch(
                'apps.runner.loop.make_provider',
                return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
            ),
            expectLogItems(
                [
                    ExpectLogItem(
                        'apps.runner.loop',
                        logging.ERROR,
                        r'Session .* unexpected failure',
                        count=1,
                    )
                ],
            ),
        ):
            runner.run()

        self.assertNotIn(AgentSessionActivityKind.OUTPUT, created_kinds)
        self.assertEqual(created_kinds.count(AgentSessionActivityKind.LLM), 1)
        self.assertEqual(created_kinds.count(AgentSessionActivityKind.FAILURE), 1)

    def test_hook_set_has_no_legacy_event_field(self) -> None:
        """The hook API exposes only canonical activity create/update callbacks."""
        names = {item.name for item in fields(HookSet)}

        self.assertEqual(
            names,
            {
                'on_run_start',
                'on_run_end',
                'on_generate_start',
                'on_generate_end',
                'on_tool_call_start',
                'on_tool_call_end',
                'on_activity_created',
                'on_activity_updated',
                'on_status',
            },
        )

    def test_tool_end_hook_cancellation_terminalizes_open_lifecycles(self) -> None:
        """Cancellation at tool-end observation cannot strand tool or LLM work."""
        cancellation = asyncio.CancelledError('tool end cancelled')
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'time?'})
        runner = SessionRunner(backend)

        def cancel_tool_end(_call: dict[str, Any], _result: str) -> None:
            """Cancel after tool execution but before normal terminalization."""
            raise cancellation

        runner.add_hook(HookSet(on_tool_call_end=cancel_tool_end))
        tool_call = StreamResult(
            content='',
            tool_calls=[{'name': 'clock__now', 'arguments': {}, 'id': 'clock-1'}],
        )

        with (
            patch(
                'apps.runner.loop.make_provider',
                return_value=FakeProvider.for_responses([tool_call]),
            ),
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            runner.run()

        self.assertIs(caught.exception, cancellation)
        activities = backend.activities()
        tool = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.TOOL)
        llm = next(activity for activity in activities if activity.kind == AgentSessionActivityKind.LLM)
        self.assertEqual(tool.status, 'failed')
        self.assertEqual(llm.status, 'failed')
        self.assertFalse(any(activity.status == 'running' for activity in activities))

    def test_hook_failure_does_not_fail_session(self) -> None:
        """Observability hook raises are swallowed so the session still completes."""
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})

        def fail_run_start() -> None:
            """Raise from a hook to prove observability failures are isolated."""
            raise RuntimeError('hook broke')

        runner = SessionRunner(backend)
        runner.add_hook(HookSet(on_run_start=fail_run_start))
        with (
            patch(
                'apps.runner.loop.make_provider',
                return_value=FakeProvider.for_responses([StreamResult(content='pong')]),
            ),
            expectLogItems(
                [
                    ExpectLogItem(
                        'apps.runner.hooks', logging.ERROR, r'Session runner hook on_run_start failed', count=1
                    )
                ],
            ),
        ):
            runner.run()

        kinds = [activity.kind for activity in backend.activities()]
        self.assertIn(AgentSessionActivityKind.OUTPUT, kinds)
        self.assertNotIn(AgentSessionActivityKind.FAILURE, kinds)
        self.assertEqual(backend.get_status(), AgentSessionStatus.WAITING)

    def test_reentrant_activity_notification_waits_for_parent_delivery(self) -> None:
        """Nested activity fires queue until every observer receives the parent."""
        registry = HookRegistry()
        parent = self._activity(kind='span', seq=1)
        child = self._activity(kind='status', seq=2)
        observed: list[tuple[str, str]] = []

        def first(activity: RecordedActivity) -> None:
            """Record first-observer delivery and recursively emit one child."""
            observed.append(('first', activity.kind))
            if activity.id == parent.id:
                registry.fire('on_activity_created', child)

        def second(activity: RecordedActivity) -> None:
            """Record second-observer delivery order."""
            observed.append(('second', activity.kind))

        registry.add(HookSet(on_activity_created=first))
        registry.add(HookSet(on_activity_created=second))
        registry.fire('on_activity_created', parent)

        self.assertEqual(
            observed,
            [
                ('first', 'span'),
                ('second', 'span'),
                ('first', 'status'),
                ('second', 'status'),
            ],
        )

    def test_registration_during_fire_starts_with_next_notification(self) -> None:
        """A hook added during dispatch cannot receive that in-flight event."""
        registry = HookRegistry()
        first = self._activity(kind='input', seq=1)
        second = self._activity(kind='output', seq=2)
        observed: list[tuple[str, str]] = []
        registered = False

        def late(activity: RecordedActivity) -> None:
            """Record delivery to the hook registered by another callback."""
            observed.append(('late', activity.kind))

        def registering(activity: RecordedActivity) -> None:
            """Register one hook while handling the first notification."""
            nonlocal registered
            observed.append(('registering', activity.kind))
            if not registered:
                registered = True
                registry.add(HookSet(on_activity_created=late))

        registry.add(HookSet(on_activity_created=registering))
        registry.fire('on_activity_created', first)
        registry.fire('on_activity_created', second)

        self.assertEqual(
            observed,
            [
                ('registering', 'input'),
                ('registering', 'output'),
                ('late', 'output'),
            ],
        )

    def test_reentrant_child_cancellation_compensates_sources_and_cleans_queue(self) -> None:
        """Queued child cancellation drains observers, closes work, and resets dispatch."""
        cancellation = asyncio.CancelledError('nested child cancelled')
        backend = MemorySessionBackend(load_example('clock-assistant').model_copy(), user_id=1)
        backend.push_mailbox({'action': 'chat', 'content': 'ping'})
        runner = SessionRunner(backend)
        observed: list[tuple[str, str, str, int]] = []

        def reentrant(activity: RecordedActivity) -> None:
            """Create a child from its parent callback and cancel child delivery."""
            observed.append(('first-create', activity.name, activity.status, activity.revision))
            if activity.kind == AgentSessionActivityKind.LLM:
                runner.recorder.start(
                    kind=AgentSessionActivityKind.SPAN,
                    name='nested-child',
                    summary='Nested child',
                )
            elif activity.name == 'nested-child':
                raise cancellation

        def remaining(activity: RecordedActivity) -> None:
            """Record delivery to the observer after the cancelling callback."""
            observed.append(('second-create', activity.name, activity.status, activity.revision))

        def first_update(activity: RecordedActivity) -> None:
            """Record compensated updates for source attribution."""
            observed.append(('first-update', activity.name, activity.status, activity.revision))

        def second_update(activity: RecordedActivity) -> None:
            """Record compensated updates for all remaining observers."""
            observed.append(('second-update', activity.name, activity.status, activity.revision))

        runner.add_hook(
            HookSet(
                on_activity_created=reentrant,
                on_activity_updated=first_update,
            )
        )
        runner.add_hook(
            HookSet(
                on_activity_created=remaining,
                on_activity_updated=second_update,
            )
        )

        with self.assertRaises(asyncio.CancelledError) as caught:
            runner.run()

        self.assertIs(caught.exception, cancellation)
        llm = next(activity for activity in backend.activities() if activity.kind == AgentSessionActivityKind.LLM)
        child = next(activity for activity in backend.activities() if activity.name == 'nested-child')
        self.assertEqual(llm.status, 'failed')
        self.assertEqual(child.status, 'failed')
        self.assertEqual(llm.revision, 2)
        self.assertEqual(child.revision, 2)
        self.assertFalse(any(activity.status == 'running' for activity in backend.activities()))

        parent_first = observed.index(('first-create', llm.name, 'running', 1))
        parent_second = observed.index(('second-create', llm.name, 'running', 1))
        child_first = observed.index(('first-create', 'nested-child', 'running', 1))
        child_second = observed.index(('second-create', 'nested-child', 'running', 1))
        child_update_first = observed.index(('first-update', 'nested-child', 'failed', 2))
        child_update_second = observed.index(('second-update', 'nested-child', 'failed', 2))
        self.assertLess(parent_first, parent_second)
        self.assertLess(parent_second, child_first)
        self.assertLess(child_first, child_second)
        self.assertLess(child_second, child_update_first)
        self.assertLess(child_update_first, child_update_second)

        runner.recorder.status_note(name='after-cancellation', summary='Queue reset')
        self.assertEqual(
            observed[-2:],
            [
                ('first-create', 'after-cancellation', 'succeeded', 1),
                ('second-create', 'after-cancellation', 'succeeded', 1),
            ],
        )


class TestActivityObservability(OTestCase):
    def test_jsonl_uses_activity_shape_and_redacts_unsafe_failure_data(self) -> None:
        """Observability writes create/update activity records without legacy events."""
        session_id = uuid4()
        parent_id = uuid4()
        child_session_id = uuid4()
        activity = RecordedActivity(
            id=uuid4(),
            session_id=session_id,
            parent_id=parent_id,
            seq=7,
            revision=1,
            kind='failure',
            status='failed',
            name='failure',
            summary='Provider failed',
            details={'message': 'Unavailable', 'traceback': 'secret stack'},
            model='gpt-5.4-mini',
            input_tokens=11,
            output_tokens=2,
            cost_usd=Decimal('0.001200'),
            latency_ms=17,
            started_at=datetime(2026, 7, 26, tzinfo=UTC),
            ended_at=datetime(2026, 7, 26, 0, 0, 1, tzinfo=UTC),
            created_at=datetime(2026, 7, 26, tzinfo=UTC),
            child_session_id=child_session_id,
        )
        emitted: list[str] = []

        with TemporaryDirectory() as temp_dir:
            partition = RunPartition(
                kind='unit',
                suite='hooks',
                sample_id='activity',
                model='fake',
                run_id='r1',
            )
            writer = EventLogWriter(Path(temp_dir))
            hooks = build_observability_hooks(partition=partition, log_writer=writer, print_fn=emitted.append)
            assert hooks.on_activity_created is not None
            assert hooks.on_activity_updated is not None
            hooks.on_activity_created(activity)
            hooks.on_activity_updated(replace(activity, revision=2))
            records = [json.loads(line) for line in writer.path_for(partition).read_text(encoding='utf-8').splitlines()]

        self.assertEqual([record['event'] for record in records], ['session_activity', 'session_activity'])
        self.assertEqual([record['op'] for record in records], ['create', 'update'])
        self.assertEqual({record['record']['kind'] for record in records}, {'failure'})
        self.assertEqual({record['record']['status'] for record in records}, {'failed'})
        record = records[0]['record']
        self.assertEqual(record['id'], str(activity.id))
        self.assertEqual(record['session_id'], str(session_id))
        self.assertEqual(record['parent_id'], str(parent_id))
        self.assertEqual(record['child_session_id'], str(child_session_id))
        self.assertEqual(record['seq'], 7)
        self.assertEqual(record['revision'], 1)
        self.assertEqual(record['name'], 'failure')
        self.assertEqual(record['summary'], 'Provider failed')
        self.assertEqual(record['details'], {'message': 'Unavailable'})
        self.assertEqual(record['cost_usd'], '0.001200')
        self.assertEqual(
            record['usage'],
            {
                'model': 'gpt-5.4-mini',
                'input_tokens': 11,
                'output_tokens': 2,
                'cost_usd': '0.001200',
                'latency_ms': 17,
            },
        )
        self.assertNotIn('secret stack', json.dumps(records))
        self.assertEqual(
            emitted,
            ['[activity] create 7 failure failed', '[activity] update 7 failure rev=2'],
        )

    def test_nested_sensitive_keys_and_json_strings_are_redacted(self) -> None:
        """Sensitive structured values are redacted while ordinary model text remains."""
        details = {
            'message': 'The user explicitly discussed password rotation.',
            'nested': {
                'API-Key': 'api-value',
                'Authorization': 'Bearer private',
                'credentials': {'username': 'user', 'password': 'pass'},
                'safe': 'visible',
            },
            'arguments': [
                {'access_token': 'access-value'},
                {'refreshToken': 'refresh-value'},
                {'cookie': 'session=value'},
            ],
            'result': json.dumps(
                {
                    'ok': True,
                    'data': {'client_secret': 'client-value', 'name': 'visible'},
                }
            ),
        }
        activity = self._write_activity(details=details)

        record = activity['record']
        self.assertEqual(record['details']['message'], details['message'])
        self.assertEqual(record['details']['nested']['safe'], 'visible')
        self.assertEqual(record['details']['nested']['API-Key'], '<redacted>')
        self.assertEqual(record['details']['nested']['Authorization'], '<redacted>')
        self.assertEqual(record['details']['nested']['credentials'], '<redacted>')
        self.assertEqual(record['details']['arguments'][0]['access_token'], '<redacted>')
        self.assertEqual(record['details']['arguments'][1]['refreshToken'], '<redacted>')
        self.assertEqual(record['details']['arguments'][2]['cookie'], '<redacted>')
        parsed_result = json.loads(record['details']['result'])
        self.assertEqual(parsed_result['data']['client_secret'], '<redacted>')
        self.assertEqual(parsed_result['data']['name'], 'visible')

    def test_nonfinite_and_cyclic_values_produce_strict_json(self) -> None:
        """NaN, infinities, and cycles become bounded strict-JSON markers."""
        cyclic: dict[str, Any] = {'safe': 'visible'}
        cyclic['self'] = cyclic
        details = {
            'nan': float('nan'),
            'positive': float('inf'),
            'negative': float('-inf'),
            'cycle': cyclic,
        }

        record = self._write_activity(details=details)

        safe_details = record['record']['details']
        self.assertEqual(safe_details['nan'], '<non-finite:NaN>')
        self.assertEqual(safe_details['positive'], '<non-finite:Infinity>')
        self.assertEqual(safe_details['negative'], '<non-finite:-Infinity>')
        self.assertEqual(safe_details['cycle']['self'], '<cycle>')
        json.dumps(record, allow_nan=False)

    def test_tool_event_redacts_structured_arguments_and_result(self) -> None:
        """Tool observability sanitizes structured arguments and JSON result strings."""
        with TemporaryDirectory() as temp_dir:
            partition = self._partition()
            writer = EventLogWriter(Path(temp_dir))
            hooks = build_observability_hooks(partition=partition, log_writer=writer)
            call = {
                'name': 'example__run',
                'arguments': {'password': 'private', 'query': 'visible'},
            }
            result = json.dumps({'token': 'private', 'answer': 'visible'})
            assert hooks.on_tool_call_start is not None
            assert hooks.on_tool_call_end is not None
            assert hooks.on_generate_end is not None
            hooks.on_tool_call_start(call)
            hooks.on_tool_call_end(call, result)
            hooks.on_generate_end(
                StreamResult(
                    content=json.dumps({'api_key': 'private', 'answer': 'visible'}),
                    latency_ms=cast(int, float('nan')),
                )
            )
            records = [json.loads(line) for line in writer.path_for(partition).read_text(encoding='utf-8').splitlines()]

        self.assertEqual(records[0]['call']['arguments']['password'], '<redacted>')
        self.assertEqual(records[0]['call']['arguments']['query'], 'visible')
        self.assertEqual(json.loads(records[1]['result'])['token'], '<redacted>')
        self.assertEqual(json.loads(records[1]['result'])['answer'], 'visible')
        self.assertEqual(json.loads(records[2]['content'])['api_key'], '<redacted>')
        self.assertEqual(json.loads(records[2]['content'])['answer'], 'visible')
        self.assertEqual(records[2]['latency_ms'], '<non-finite:NaN>')

    def test_provider_failure_observability_omits_raw_message(self) -> None:
        """Provider diagnostics retain only a stable code in persisted JSONL."""
        secret = 'Authorization: Bearer observability-secret-value'
        with TemporaryDirectory() as temp_dir:
            partition = self._partition()
            writer = EventLogWriter(Path(temp_dir))
            hooks = build_observability_hooks(partition=partition, log_writer=writer)
            assert hooks.on_generate_end is not None
            hooks.on_generate_end(
                StreamResult(
                    error=ProviderError(message=secret, code='provider_unavailable'),
                )
            )
            text = writer.path_for(partition).read_text(encoding='utf-8')
            record = json.loads(text)

        self.assertNotIn(secret, text)
        self.assertEqual(
            record['error'],
            {'message': 'Provider request failed', 'code': 'provider_unavailable'},
        )

    def test_provider_failure_observability_includes_http_status(self) -> None:
        """Observability keeps the curated message and appends HTTP status when known."""
        secret = 'Authorization: Bearer observability-secret-value'
        with TemporaryDirectory() as temp_dir:
            partition = self._partition()
            writer = EventLogWriter(Path(temp_dir))
            hooks = build_observability_hooks(partition=partition, log_writer=writer)
            assert hooks.on_generate_end is not None
            hooks.on_generate_end(
                StreamResult(
                    error=ProviderError(message=secret, code='provider_unavailable', status_code=429),
                )
            )
            text = writer.path_for(partition).read_text(encoding='utf-8')
            record = json.loads(text)

        self.assertNotIn(secret, text)
        self.assertEqual(
            record['error'],
            {'message': 'Provider request failed (429)', 'code': 'provider_unavailable'},
        )

    def _write_activity(self, *, details: dict[str, Any]) -> dict[str, Any]:
        """Write one activity record and parse its strict JSONL representation."""
        activity = RecordedActivity(
            id=uuid4(),
            session_id=uuid4(),
            parent_id=None,
            seq=1,
            revision=1,
            kind='status',
            status='succeeded',
            name='status',
            summary='status',
            details=details,
        )
        with TemporaryDirectory() as temp_dir:
            partition = self._partition()
            writer = EventLogWriter(Path(temp_dir))
            hooks = build_observability_hooks(partition=partition, log_writer=writer)
            assert hooks.on_activity_created is not None
            hooks.on_activity_created(activity)
            line = writer.path_for(partition).read_text(encoding='utf-8').strip()
        return cast(dict[str, Any], json.loads(line))

    @staticmethod
    def _partition() -> RunPartition:
        """Return a stable test partition for JSONL observability checks."""
        return RunPartition(
            kind='unit',
            suite='hooks',
            sample_id='sanitization',
            model='fake',
            run_id='r1',
        )

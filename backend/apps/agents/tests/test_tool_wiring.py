# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from apps.agents.ingest import create_agent_from_spec, persist_agent_config
from apps.agents.tool_wiring import build_bound_tools
from apps.queues.models import Queue, QueueItem, QueueItemStatus
from apps.queues.services import commands
from apps.queues.tests.base import make_second_session
from django.contrib.auth import get_user_model

# isort: split

from libs.agent_spec import (
    AgentConfigSpec,
    LLMSpec,
    QueueSpec,
    SourceSpec,
    ToolInstance,
    load_example,
)
from libs.tools.base import Tool, ToolFunction
from libs.tools.context import ToolContext
from libs.tools.registry import register_tool

from olib.py.django.test.cases import OTestCase

if TYPE_CHECKING:
    from uuid import UUID


def _make_ctx(
    *,
    user_id: int | None = None,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
    secret_supplier_factory: Callable[[str | None, str], Callable[[], str | None]] | None = None,
    client_factories: dict[str, Callable[..., Any]] | None = None,
) -> ToolContext:
    """Build a ToolContext with a minimal spec for tests."""
    spec = AgentConfigSpec(
        llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
        system_prompt='test',
    )
    return ToolContext(
        spec=spec,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        secret_supplier_factory=secret_supplier_factory,
        client_factories=client_factories or {},
    )


class _EchoCredTool(Tool):
    name = 'echo_cred'
    credential_type = 'gmail'

    def functions(self, ctx: ToolContext, instance: ToolInstance | None = None) -> list[ToolFunction]:
        return [
            ToolFunction(
                name='ping',
                description='x',
                parameters={'type': 'object', 'properties': {}},
                handler=self._ping,
                readonly=True,
            ),
        ]

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Echo whether a token resolved and surface the injected config."""
        config = instance.config if instance else {}
        token_supplier: Callable[[], str | None]
        if ctx.secret_supplier_factory and (instance and instance.credential_ref or self.credential_type):
            token_supplier = ctx.secret_supplier_factory(
                instance.credential_ref if instance else None,
                self.credential_type or '',
            )
        else:
            token_supplier = lambda: None

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'ping':
                raise ValueError(function)
            token = token_supplier()
            return {'token_set': token is not None, 'subject': config.get('subject')}

        return invoke

    @staticmethod
    def _ping(**_kwargs: Any) -> str:
        return 'ok'


class TestBuildBoundTools(OTestCase):
    def setUp(self) -> None:
        register_tool('echo_cred', _EchoCredTool())

    def test_clock_instance_invokes_without_credentials(self) -> None:
        instances = [ToolInstance(id='clock', type='clock', allow=['now'])]
        ctx = _make_ctx(user_id=1)
        bound = build_bound_tools(instances, ctx=ctx)
        self.assertIn('clock', bound)
        result = bound['clock'].invoke('now', {})
        self.assertIsInstance(result, str)

    def test_credential_tool_uses_supplier(self) -> None:
        instances = [ToolInstance(id='gmail-a', type='echo_cred', allow=['ping'])]
        ctx = _make_ctx(user_id=1, secret_supplier_factory=lambda ref, typ: lambda: 'tok')
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['gmail-a'].invoke('ping', {})
        self.assertEqual(out, {'token_set': True, 'subject': None})

    def test_clickup_tool_wires_with_config_and_credential(self) -> None:
        instances = [
            ToolInstance(
                id='clickup',
                type='clickup',
                credential_ref='clickup',
                allow=['list_tasks'],
                config={'team_id': '9'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_tasks.return_value = {'tasks': [{'id': 't1'}], 'last_page': True}
        ctx = _make_ctx(
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: 'pk_test',
            client_factories={'clickup': lambda **_kwargs: fake_client},
        )
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['clickup'].invoke('list_tasks', {'list_id': '901'})
        self.assertEqual(out['tasks'], [{'id': 't1'}])

    def test_gmail_tool_wires_with_config_and_credential(self) -> None:
        instances = [
            ToolInstance(
                id='gmail-personal',
                type='gmail',
                credential_ref='gmail-personal',
                allow=['list'],
                config={'subject': 'me@example.com'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_messages.return_value = {'message_ids': ['m1'], 'next_page_token': None}
        ctx = _make_ctx(
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: '{"sa": true}',
            client_factories={'gmail': lambda **_kwargs: fake_client},
        )
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['gmail-personal'].invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out['message_ids'], ['m1'])

    def test_gmail_tool_uses_injected_client_factory(self) -> None:
        instances = [
            ToolInstance(
                id='gmail-personal',
                type='gmail',
                credential_ref='gmail-personal',
                allow=['list'],
                config={'subject': 'me@example.com'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_messages.return_value = {'message_ids': ['m-factory'], 'next_page_token': None}
        ctx = _make_ctx(
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: '{"sa": true}',
            client_factories={'gmail': lambda **_kwargs: fake_client},
        )
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['gmail-personal'].invoke('list', {'query': 'in:inbox'})
        self.assertEqual(out['message_ids'], ['m-factory'])
        fake_client.list_messages.assert_called_once_with(query='in:inbox', max_results=100, page_token=None)

    def test_gmail_tool_uses_injected_client_factory_without_user_supplier(self) -> None:
        """Client factories bind even when env-only sessions have no Django user credential supplier."""
        instances = [
            ToolInstance(
                id='gmail-personal',
                type='gmail',
                credential_ref='gmail-personal',
                allow=['list'],
                config={'subject': 'me@example.com'},
            ),
        ]
        fake_client = MagicMock()
        fake_client.list_messages.return_value = {'message_ids': ['m-env-only'], 'next_page_token': None}

        ctx = _make_ctx(
            user_id=None,
            client_factories={'gmail': lambda **_kwargs: fake_client},
        )
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['gmail-personal'].invoke('list', {'query': 'in:inbox'})

        self.assertEqual(out['message_ids'], ['m-env-only'])
        fake_client.list_messages.assert_called_once_with(query='in:inbox', max_results=100, page_token=None)

    def test_credential_tool_receives_instance_config(self) -> None:
        instances = [
            ToolInstance(
                id='gmail-a',
                type='echo_cred',
                allow=['ping'],
                config={'subject': 'me@example.com'},
            ),
        ]
        ctx = _make_ctx(user_id=1, secret_supplier_factory=lambda ref, typ: lambda: 'tok')
        bound = build_bound_tools(instances, ctx=ctx)
        out = bound['gmail-a'].invoke('ping', {})
        self.assertEqual(out, {'token_set': True, 'subject': 'me@example.com'})

    def test_queue_tool_round_trip_take_and_complete(self) -> None:
        user = get_user_model().objects.create_user(username='queue-wire-user', password='x')
        agent = create_agent_from_spec(
            user,
            load_example('queue-echo'),
            name='Queue wire',
            identifier='queue-wire-agent',
            config_source='ui',
            source_rev='test',
        )
        spec = AgentConfigSpec(
            llm=LLMSpec(provider='openai', model='gpt-5.4-mini'),
            system_prompt='hello',
            tools=[ToolInstance(id='q1', type='queue', allow=['put', 'take', 'complete'])],
            queues=[
                QueueSpec(
                    id='inbox',
                    sources=[SourceSpec(id='src-a', adapter_type='test', config={'prefix': 'x'})],
                ),
            ],
        )
        persist_agent_config(agent, spec, source_rev='queue-wire-v1')
        queue = Queue.objects.get(agent=agent, queue_id='inbox')
        config = agent.current_config
        assert config is not None
        session = make_second_session(agent, config)

        commands.put_item(queue=queue, payload={'task': 'one'})

        ctx = _make_ctx(
            user_id=user.pk,
            agent_id=agent.id,
            session_id=session.id,
        )
        bound = build_bound_tools(spec.tools, ctx=ctx)
        take_out = bound['q1'].invoke('take', {'queue': 'inbox'})
        self.assertIn('item_id', take_out)
        item_id = take_out['item_id']

        complete_out = bound['q1'].invoke('complete', {'item_id': item_id})
        self.assertEqual(complete_out, {'ok': True})

        item = QueueItem.objects.get(pk=item_id)
        self.assertEqual(item.status, QueueItemStatus.DONE)

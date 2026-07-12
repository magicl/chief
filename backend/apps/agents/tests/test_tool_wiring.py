# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

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
)
from libs.agent_specs import load_example
from libs.tools.base import Tool, ToolFunction
from libs.tools.registry import register_tool

from olib.py.django.test.cases import OTestCase


class _EchoCredTool(Tool):
    name = 'echo_cred'
    credential_type = 'gmail'

    def functions(self) -> list[ToolFunction]:
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
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Echo whether a token resolved and surface the injected config."""
        cfg = config or {}

        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'ping':
                raise ValueError(function)
            token = token_supplier()
            return {'token_set': token is not None, 'subject': cfg.get('subject')}

        return invoke

    @staticmethod
    def _ping(**_kwargs: Any) -> str:
        return 'ok'


class TestBuildBoundTools(OTestCase):
    def setUp(self) -> None:
        register_tool('echo_cred', _EchoCredTool())

    def test_clock_instance_invokes_without_credentials(self) -> None:
        instances = [ToolInstance(id='clock', type='clock', allow=['now'])]
        bound = build_bound_tools(instances, user_id=1)
        self.assertIn('clock', bound)
        result = bound['clock'].invoke('now', {})
        self.assertIsInstance(result, str)

    def test_credential_tool_uses_supplier(self) -> None:
        instances = [ToolInstance(id='gmail-a', type='echo_cred', allow=['ping'])]
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'tok'):
            bound = build_bound_tools(instances, user_id=1)
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
        with (
            patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'pk_test'),
            patch('libs.tools.tools.clickup.ClickUpClient', return_value=fake_client),
        ):
            bound = build_bound_tools(instances, user_id=1)
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
        with (
            patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: '{"sa": true}'),
            patch('libs.tools.tools.gmail.GmailClient', return_value=fake_client),
        ):
            bound = build_bound_tools(instances, user_id=1)
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
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: '{"sa": true}'):
            bound = build_bound_tools(
                instances,
                user_id=1,
                client_factories={'gmail': lambda **_kwargs: fake_client},
            )
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

        bound = build_bound_tools(
            instances,
            user_id=None,
            client_factories={'gmail': lambda **_kwargs: fake_client},
        )
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
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'tok'):
            bound = build_bound_tools(instances, user_id=1)
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

        bound = build_bound_tools(
            spec.tools,
            user_id=user.pk,
            agent_id=agent.id,
            session_id=session.id,
        )
        take_out = bound['q1'].invoke('take', {'queue': 'inbox'})
        self.assertIn('item_id', take_out)
        item_id = take_out['item_id']

        complete_out = bound['q1'].invoke('complete', {'item_id': item_id})
        self.assertEqual(complete_out, {'ok': True})

        item = QueueItem.objects.get(pk=item_id)
        self.assertEqual(item.status, QueueItemStatus.DONE)

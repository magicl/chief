# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from apps.agents.models import SpendPolicy
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
    HourlyUsage,
)
from apps.sessions.services.commands import create_activity, update_session_name
from apps.sessions.services.queries import get_algorithm_session_for_target
from apps.sessions.tasks import generate_session_name
from apps.sessions.tests.base import make_test_session
from django.utils import timezone
from libs.algorithms import CHAT_NAME_ID, ChatNameResult

from olib.py.django.test.cases import OTransactionTestCase

FIRST_MESSAGE = 'How do I reset my password?'


class TestGenerateSessionNameTask(OTransactionTestCase):
    def _record_first_input(self, session: AgentSession) -> None:
        """Persist the first user message that the naming task reads."""
        create_activity(
            session,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
            summary=FIRST_MESSAGE,
            details={'content': FIRST_MESSAGE},
        )

    @patch('apps.sessions.services.commands.publish_session_update')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(
            title='Password reset help',
            cost_usd=Decimal('0.001000'),
            model='gpt-5.4-nano',
        ),
    )
    def test_creates_algorithm_session_with_llm_and_names_both(
        self,
        mock_generate: Any,
        _mock_publish: Any,
    ) -> None:
        chat = make_test_session('name-task-agent')
        self._record_first_input(chat)

        generate_session_name.run(str(chat.id))

        chat.refresh_from_db()
        self.assertEqual(chat.name, 'Password reset help')
        # Naming traces belong to the algorithm session, never to the chat.
        self.assertFalse(
            AgentSessionActivity.objects.filter(
                session=chat,
                kind=AgentSessionActivityKind.LLM,
            ).exists()
        )
        algo = AgentSession.objects.get(algorithm_id=CHAT_NAME_ID, user_id=chat.user_id)
        self.assertIsNone(algo.agent_id)
        self.assertIsNone(algo.agent_config_id)
        self.assertEqual(algo.name, 'Password reset help')
        self.assertEqual(algo.status, AgentSessionStatus.DONE)
        self.assertIsNotNone(algo.ended_at)
        root = AgentSessionActivity.objects.get(session=algo, parent_id=None)
        self.assertEqual(root.details.get('target_session_id'), str(chat.id))
        llm = AgentSessionActivity.objects.get(session=algo, kind=AgentSessionActivityKind.LLM)
        self.assertEqual(llm.parent_id, root.id)
        self.assertEqual(llm.cost_usd, Decimal('0.001000'))
        mock_generate.assert_called_once()
        self.assertIsNotNone(mock_generate.call_args.kwargs.get('recorder'))

    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(title='Already named'),
    )
    def test_skips_when_chat_already_named(self, mock_generate: Any) -> None:
        chat = make_test_session('name-task-skip-agent')
        chat.name = 'Existing'
        chat.save(update_fields=['name'])

        generate_session_name.run(str(chat.id))

        mock_generate.assert_not_called()
        self.assertFalse(AgentSession.objects.filter(algorithm_id=CHAT_NAME_ID).exists())

    @patch('apps.sessions.services.commands.publish_session_update')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(title='Provider title'),
    )
    def test_user_cap_skips_provider_and_still_writes_algorithm_session(
        self,
        mock_generate: Any,
        _mock_publish: Any,
    ) -> None:
        chat = make_test_session('name-task-cap-agent')
        self._record_first_input(chat)
        SpendPolicy.objects.create(user_id=chat.user_id, daily_spend_limit_usd=Decimal('0.00'))
        HourlyUsage.objects.create(
            user_id=chat.user_id,
            agent=None,
            algorithm_id=CHAT_NAME_ID,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='gpt-5.4-nano',
            cost_usd=Decimal('1.000000'),
        )

        generate_session_name.run(str(chat.id))

        mock_generate.assert_not_called()
        chat.refresh_from_db()
        self.assertEqual(chat.name, FIRST_MESSAGE)
        algo = AgentSession.objects.get(algorithm_id=CHAT_NAME_ID, user_id=chat.user_id)
        self.assertEqual(algo.status, AgentSessionStatus.DONE)
        llm = AgentSessionActivity.objects.get(session=algo, kind=AgentSessionActivityKind.LLM)
        self.assertEqual(llm.status, AgentSessionActivityStatus.FAILED)

    @patch('apps.sessions.services.commands.publish_session_update')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(title='Password reset help'),
    )
    def test_retry_does_not_call_provider_twice_for_same_chat(
        self,
        mock_generate: Any,
        _mock_publish: Any,
    ) -> None:
        chat = make_test_session('name-task-retry-agent')
        self._record_first_input(chat)

        generate_session_name.run(str(chat.id))
        first_run = AgentSession.objects.get(algorithm_id=CHAT_NAME_ID, user_id=chat.user_id)
        generate_session_name.run(str(chat.id))

        mock_generate.assert_called_once()
        self.assertEqual(
            AgentSession.objects.filter(algorithm_id=CHAT_NAME_ID, user_id=chat.user_id).count(),
            1,
        )
        reused = get_algorithm_session_for_target(chat.user_id, CHAT_NAME_ID, chat.id)
        assert reused is not None
        self.assertEqual(reused.id, first_run.id)

    @patch('apps.sessions.services.commands.publish_session_update')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(title='Password reset help'),
    )
    def test_retry_reuses_title_when_chat_name_write_did_not_land(
        self,
        mock_generate: Any,
        _mock_publish: Any,
    ) -> None:
        """A retry after the provider succeeded must not bill a second LLM call."""
        chat = make_test_session('name-task-retry-partial-agent')
        self._record_first_input(chat)

        generate_session_name.run(str(chat.id))
        AgentSession.objects.filter(pk=chat.id).update(name=None)
        generate_session_name.run(str(chat.id))

        mock_generate.assert_called_once()
        chat.refresh_from_db()
        self.assertEqual(chat.name, 'Password reset help')
        self.assertEqual(
            AgentSession.objects.filter(algorithm_id=CHAT_NAME_ID, user_id=chat.user_id).count(),
            1,
        )
        self.assertEqual(
            AgentSessionActivity.objects.filter(
                session__algorithm_id=CHAT_NAME_ID,
                kind=AgentSessionActivityKind.LLM,
            ).count(),
            1,
        )

    @patch('apps.sessions.services.commands.publish_session_update')
    @patch('apps.sessions.tasks.update_session_name')
    @patch(
        'apps.sessions.tasks.generate_chat_name',
        return_value=ChatNameResult(
            title='Password reset help',
            cost_usd=Decimal('0.001000'),
        ),
    )
    def test_retry_does_not_rebill_when_chat_name_write_fails(
        self,
        mock_generate: Any,
        mock_update_name: Any,
        _mock_publish: Any,
    ) -> None:
        """A billed llm row must survive a failed chat name write so retry is free."""
        chat = make_test_session('name-task-retry-failed-write-agent')
        self._record_first_input(chat)
        attempts = {'n': 0}

        def fail_first_write(session_id: Any, name: str) -> Any:
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise RuntimeError('chat name write failed')
            return update_session_name(session_id, name)

        mock_update_name.side_effect = fail_first_write

        with self.assertRaises(RuntimeError):
            generate_session_name.run(str(chat.id))

        self.assertEqual(
            AgentSessionActivity.objects.filter(
                session__algorithm_id=CHAT_NAME_ID,
                kind=AgentSessionActivityKind.LLM,
            ).count(),
            1,
        )
        generate_session_name.run(str(chat.id))

        mock_generate.assert_called_once()
        chat.refresh_from_db()
        self.assertEqual(chat.name, 'Password reset help')
        self.assertEqual(
            AgentSessionActivity.objects.filter(
                session__algorithm_id=CHAT_NAME_ID,
                kind=AgentSessionActivityKind.LLM,
            ).count(),
            1,
        )

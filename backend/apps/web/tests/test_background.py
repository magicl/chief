# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Web-layer coverage for the Background algorithm catalog and algorithm detail page."""

import logging

from apps.sessions.models import AgentSession, AgentSessionStatus, TriggerType
from apps.sessions.tests.base import make_algorithm_session, make_test_session
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from libs.algorithms import CHAT_NAME_ID

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

User = get_user_model()


class TestBackgroundDashboard(OTransactionTestCase):
    """Dashboard Background card, agent-only recents, and per-user algorithm detail."""

    def setUp(self) -> None:
        """Log in the owner of a plain agent chat session."""
        super().setUp()
        self.client = Client()
        self.chat = make_test_session('bg-chat')
        self.user = User.objects.get(username='user-bg-chat')
        self.client.force_login(self.user)

    def _make_algorithm_session(self, user_id: int) -> AgentSession:
        """Create a done algorithm-owned session billed to the given user."""
        return AgentSession.objects.create(
            user_id=user_id,
            algorithm_id=CHAT_NAME_ID,
            status=AgentSessionStatus.DONE,
            trigger_type=TriggerType.ALGORITHM,
        )

    def test_dashboard_lists_chat_name_with_zero_runs(self) -> None:
        """Registered algorithms appear before any run has happened."""
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Background')
        self.assertContains(response, 'Chat name')
        self.assertContains(response, reverse('algorithm_detail', kwargs={'algorithm_id': CHAT_NAME_ID}))

    def test_recent_sessions_exclude_algorithm_sessions(self) -> None:
        """Recent sessions stay agent-owned only."""
        algo = self._make_algorithm_session(self.user.pk)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, reverse('session_detail', kwargs={'session_id': self.chat.id}))
        self.assertNotContains(response, reverse('session_detail', kwargs={'session_id': algo.id}))

    def test_algorithm_detail_lists_only_this_users_sessions(self) -> None:
        """Algorithm detail is scoped to the logged-in user's sessions."""
        mine = self._make_algorithm_session(self.user.pk)
        other_user = User.objects.create_user(username='bg-other', password='x')
        foreign = self._make_algorithm_session(other_user.pk)
        response = self.client.get(reverse('algorithm_detail', kwargs={'algorithm_id': CHAT_NAME_ID}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('session_detail', kwargs={'session_id': mine.id}))
        self.assertNotContains(response, reverse('session_detail', kwargs={'session_id': foreign.id}))

    def test_algorithm_detail_omits_agent_chrome(self) -> None:
        """The algorithm page has no chatbox or configuration controls."""
        response = self.client.get(reverse('algorithm_detail', kwargs={'algorithm_id': CHAT_NAME_ID}))
        self.assertNotContains(response, 'frame-chatbox')
        self.assertNotContains(response, 'Message the agent')
        self.assertNotContains(response, 'Configuration')

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /algorithms/nope/', count=1)])
    def test_unknown_algorithm_is_not_found(self) -> None:
        """Ids outside the registry never render a detail page."""
        response = self.client.get(reverse('algorithm_detail', kwargs={'algorithm_id': 'nope'}))
        self.assertEqual(response.status_code, 404)


class TestAlgorithmSessionPage(OTransactionTestCase):
    """Algorithm session pages expose traces without agent chat controls."""

    def setUp(self) -> None:
        """Log in the owner of an algorithm session."""
        super().setUp()
        self.client = Client()
        self.session = make_algorithm_session('web-page')
        self.client.force_login(self.session.user)

    def test_detail_shows_algorithm_trace_without_composer(self) -> None:
        """Algorithm sessions link to Background and omit agent-only chrome."""
        response = self.client.get(reverse('session_detail', kwargs={'session_id': self.session.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'event-panel')
        self.assertNotContains(response, 'Message the agent')
        self.assertNotContains(response, 'Back to agent')
        self.assertContains(response, 'Background')
        self.assertContains(
            response,
            reverse('algorithm_detail', kwargs={'algorithm_id': self.session.algorithm_id}),
        )

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Not Found: /sessions/.+/chat/',
                count=1,
            )
        ]
    )
    def test_chat_post_is_not_found(self) -> None:
        """Algorithm sessions reject agent chat continuation."""
        response = self.client.post(
            reverse('session_chat', kwargs={'session_id': self.session.id}),
            {'content': 'continue'},
        )

        self.assertEqual(response.status_code, 404)

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Not Found: /sessions/.+/(pause|resume|abort)/',
                count=3,
            )
        ]
    )
    def test_control_posts_are_not_found(self) -> None:
        """Algorithm sessions reject agent-only lifecycle controls."""
        for route_name in ('session_pause', 'session_resume', 'session_abort'):
            with self.subTest(route_name=route_name):
                response = self.client.post(reverse(route_name, kwargs={'session_id': self.session.id}))
                self.assertEqual(response.status_code, 404)

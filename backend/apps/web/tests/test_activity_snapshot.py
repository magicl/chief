# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""HTTP tests for the authorized session activity snapshot endpoint."""

from __future__ import annotations

from uuid import UUID, uuid4

from apps.sessions.models import (
    AgentSession,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    TriggerType,
)
from apps.sessions.services.commands import create_activity
from apps.sessions.tests.base import make_test_session
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


def make_activity_session(identifier: str) -> AgentSession:
    """Create an owned session with one root activity for snapshot field checks."""
    session = make_test_session(identifier)
    create_activity(
        session,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='snapshot-output',
        summary='Seed activity',
        details={'content': 'seed'},
    )
    return session


def make_parent_with_subagent(
    identifier: str,
) -> tuple[AgentSession, AgentSession, UUID]:
    """Create a parent, linked child, and a child-only activity id.

    Uses Spec 2 session/activity APIs: owned parent via make_test_session,
    child via AgentSession.objects.create with TOOL_CALL + parent_session, then
    create_activity kind=subagent with child_session_id.
    """
    parent = make_test_session(identifier)
    child = AgentSession.objects.create(
        agent=parent.agent,
        agent_config=parent.agent_config,
        status=parent.status,
        trigger_type=TriggerType.TOOL_CALL,
        parent_session=parent,
        name=f'{identifier}-child',
    )
    create_activity(
        parent,
        kind=AgentSessionActivityKind.SUBAGENT,
        status=AgentSessionActivityStatus.RUNNING,
        name='delegate',
        summary='Child linked',
        details={},
        child_session_id=child.id,
    )
    child_only = create_activity(
        child,
        kind=AgentSessionActivityKind.OUTPUT,
        status=AgentSessionActivityStatus.SUCCEEDED,
        name='child-only',
        summary='Lives on child session',
        details={'content': 'child-only'},
    )
    return parent, child, child_only.id


class TestActivitySnapshot(OTransactionTestCase):
    """Authorized JSON snapshot of one session's activities."""

    def setUp(self) -> None:
        """Seed an owned session and log in as its owner."""
        self.client = Client()
        self.session = make_activity_session('snap-agent')
        self.user = get_user_model().objects.get(username='user-snap-agent')
        self.client.force_login(self.user)

    def test_snapshot_requires_login(self) -> None:
        """Anonymous clients are redirected to login."""
        anon = Client()
        response = anon.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 302)

    def test_snapshot_hides_other_users_sessions(self) -> None:
        """A different authenticated user cannot read another owner's snapshot."""
        other = get_user_model().objects.create_user(username='other-snap', password='x')
        self.client.force_login(other)
        response = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 404)

    def test_snapshot_returns_stable_activity_fields(self) -> None:
        """Snapshot activities expose the same JSON-safe fields as SSE upserts."""
        response = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 200)
        activity = response.json()['activities'][0]
        for key in (
            'id',
            'session_id',
            'parent_id',
            'seq',
            'revision',
            'kind',
            'status',
            'name',
            'summary',
            'details',
            'child_session_id',
        ):
            self.assertIn(key, activity)

    def test_parent_snapshot_excludes_child_session_activities(self) -> None:
        """Parent snapshot keeps the subagent link but never child-session rows."""
        parent, child, child_only_id = make_parent_with_subagent('sep-agent')
        self.client.force_login(get_user_model().objects.get(username='user-sep-agent'))
        body = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': parent.id}),
        ).json()
        ids = {row['id'] for row in body['activities']}
        self.assertNotIn(str(child_only_id), ids)
        sub = next(row for row in body['activities'] if row['kind'] == 'subagent')
        self.assertEqual(sub['child_session_id'], str(child.id))

    def test_child_snapshot_includes_parent_breadcrumb(self) -> None:
        """Child snapshot session metadata includes the direct parent id and name."""
        parent, child, _ = make_parent_with_subagent('crumb-agent')
        self.client.force_login(get_user_model().objects.get(username='user-crumb-agent'))
        body = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': child.id}),
        ).json()
        self.assertEqual(
            body['session']['parent'],
            {'id': str(parent.id), 'name': parent.name},
        )

    def test_inaccessible_child_matches_missing_child_status(self) -> None:
        """Foreign and missing session ids both return 404 for the same caller."""
        foreign = make_activity_session('foreign-agent')
        missing = uuid4()
        self.client.force_login(self.user)
        status_foreign = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': foreign.id}),
        ).status_code
        status_missing = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': missing}),
        ).status_code
        self.assertEqual(status_foreign, 404)
        self.assertEqual(status_missing, 404)

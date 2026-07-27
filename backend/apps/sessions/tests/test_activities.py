# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from typing import Any, cast
from unittest.mock import patch

from apps.agents.models import AgentConfig
from apps.sessions import activities as session_activities
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivity,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
)
from apps.sessions.services import commands as session_commands
from apps.sessions.services import queries as session_queries
from apps.sessions.tests.base import make_test_session
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import skipUnlessDBFeature
from django.utils import timezone

from olib.py.django.test.cases import OTransactionTestCase


class TestLegacyEventCutover(OTransactionTestCase):
    def test_legacy_event_api_and_model_are_removed(self) -> None:
        """Current Django state exposes activities without the legacy event surface."""
        current_models = {model.__name__ for model in django_apps.get_app_config('agent_sessions').get_models()}
        self.assertEqual(current_models, {'AgentSession', 'AgentSessionActivity', 'HourlyUsage'})


class TestAgentSessionAncestry(OTransactionTestCase):
    def test_root_session_has_null_parent(self) -> None:
        """A directly created session starts without an ancestor."""
        session = make_test_session('root-act')

        self.assertIsNone(session.parent_session_id)

    def test_child_session_points_to_parent(self) -> None:
        """A child exposes its parent and the parent's reverse manager."""
        parent = make_test_session('parent-act')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )

        self.assertEqual(child.parent_session_id, parent.id)
        self.assertEqual(list(parent.child_sessions.all()), [child])

    def test_rejects_self_parenting(self) -> None:
        """A session cannot use its own preallocated identity as parent."""
        session = make_test_session('ancestry-self')
        candidate = AgentSession(
            agent=session.agent,
            agent_config=session.agent_config,
            status=session.status,
            trigger_type=session.trigger_type,
        )
        candidate.parent_session_id = candidate.id

        with self.assertRaises(ValidationError):
            candidate.save()

    def test_rejects_cross_user_parent(self) -> None:
        """Direct model creation cannot connect sessions owned by different users."""
        parent = make_test_session('ancestry-owner-parent')
        other = make_test_session('ancestry-owner-other')

        with self.assertRaises(ValidationError):
            AgentSession.objects.create(
                agent=other.agent,
                agent_config=other.agent_config,
                status=other.status,
                trigger_type=other.trigger_type,
                parent_session=parent,
            )

    def test_parent_is_immutable_after_creation(self) -> None:
        """Ordinary saves cannot silently move an existing session in the tree."""
        first_parent = make_test_session('ancestry-first-parent')
        second_parent = AgentSession.objects.create(
            agent=first_parent.agent,
            agent_config=first_parent.agent_config,
            status=first_parent.status,
            trigger_type=first_parent.trigger_type,
        )
        child = AgentSession.objects.create(
            agent=first_parent.agent,
            agent_config=first_parent.agent_config,
            status=first_parent.status,
            trigger_type=first_parent.trigger_type,
            parent_session=first_parent,
        )
        child.parent_session = second_parent

        with self.assertRaises(ValidationError):
            child.save()

        child.refresh_from_db()
        self.assertEqual(child.parent_session_id, first_parent.id)

    def test_rejects_cycle_through_ancestry_mutation(self) -> None:
        """An attempted root-to-descendant edge is rejected at the model boundary."""
        root = make_test_session('ancestry-cycle')
        child = AgentSession.objects.create(
            agent=root.agent,
            agent_config=root.agent_config,
            status=root.status,
            trigger_type=root.trigger_type,
            parent_session=root,
        )
        root.parent_session = child

        with self.assertRaises(ValidationError):
            root.save()

        root.refresh_from_db()
        self.assertIsNone(root.parent_session_id)

    def test_root_owner_pinning_is_immutable_with_or_without_descendants(self) -> None:
        """Neither an isolated root nor a parent root can change owner/config."""
        for suffix, with_child in (('alone', False), ('parent', True)):
            with self.subTest(with_child=with_child):
                root = make_test_session(f'pin-root-{suffix}')
                if with_child:
                    AgentSession.objects.create(
                        agent=root.agent,
                        agent_config=root.agent_config,
                        status=root.status,
                        trigger_type=root.trigger_type,
                        parent_session=root,
                    )
                other = make_test_session(f'pin-other-{suffix}')
                root.agent = other.agent
                root.agent_config = other.agent_config

                with self.assertRaises(ValidationError):
                    root.save()

                root.refresh_from_db()
                self.assertNotEqual(root.agent_id, other.agent_id)

    def test_agent_config_is_immutable_within_same_owner(self) -> None:
        """A session remains pinned to its original config revision."""
        session = make_test_session('pin-config')
        replacement = AgentConfig.objects.create(
            agent=session.agent,
            source_rev='replacement',
            spec=session.agent_config.spec,
            spec_version=session.agent_config.spec_version,
        )
        session.agent_config = replacement

        with self.assertRaises(ValidationError):
            session.save()

        session.refresh_from_db()
        self.assertNotEqual(session.agent_config_id, replacement.id)

    def test_child_owner_pinning_is_immutable(self) -> None:
        """A child cannot be reassigned away from its parent's owner."""
        parent = make_test_session('pin-child-parent')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        other = make_test_session('pin-child-other')
        child.agent = other.agent
        child.agent_config = other.agent_config

        with self.assertRaises(ValidationError):
            child.save()

        child.refresh_from_db()
        self.assertEqual(child.agent_id, parent.agent_id)

    def test_fk_attname_partial_saves_cannot_bypass_immutability(self) -> None:
        """Relation attnames remain immutable alone or mixed with mutable fields."""
        parent = make_test_session('attname-parent')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        alternate_parent = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
        )
        alternate_config = AgentConfig.objects.create(
            agent=parent.agent,
            source_rev='attname-replacement',
            spec=parent.agent_config.spec,
            spec_version=parent.agent_config.spec_version,
        )
        other = make_test_session('attname-other')
        cases = (
            ('parent_session_id', alternate_parent.id),
            ('agent_id', other.agent_id),
            ('agent_config_id', alternate_config.id),
        )

        for field, replacement_id in cases:
            for mixed in (False, True):
                with self.subTest(field=field, mixed=mixed):
                    candidate = AgentSession.objects.get(pk=child.id)
                    original_id = getattr(candidate, field)
                    setattr(candidate, field, replacement_id)
                    candidate.status = 'running'
                    update_fields = [field, 'status'] if mixed else [field]

                    with self.assertRaises(ValidationError):
                        candidate.save(update_fields=update_fields)

                    candidate.refresh_from_db()
                    self.assertEqual(getattr(candidate, field), original_id)
                    self.assertNotEqual(candidate.status, 'running')

    @patch.object(AgentSession, '_validate_locked_ancestry')
    def test_status_only_save_skips_ancestry_walk(self, mock_validate: Any) -> None:
        """Legitimate status changes do not relock and walk an immutable tree."""
        session = make_test_session('status-save')
        mock_validate.reset_mock()
        session.status = 'running'

        session.save(update_fields=['status'])

        session.refresh_from_db()
        self.assertEqual(session.status, 'running')
        mock_validate.assert_not_called()

    @patch.object(AgentSession, '_validate_locked_ancestry')
    def test_status_and_timestamp_save_skips_ancestry_walk(self, mock_validate: Any) -> None:
        """Mutable state partial saves retain the efficient no-ancestry path."""
        session = make_test_session('state-timestamp-save')
        ended_at = timezone.now()
        mock_validate.reset_mock()
        session.status = 'waiting'
        session.ended_at = ended_at

        session.save(update_fields=['status', 'ended_at'])

        session.refresh_from_db()
        self.assertEqual(session.status, 'waiting')
        self.assertEqual(session.ended_at, ended_at)
        mock_validate.assert_not_called()


class TestAgentSessionActivityBasics(OTransactionTestCase):
    def test_activity_defaults_revision_and_parent(self) -> None:
        """An activity receives model defaults and may nest below another activity."""
        session = make_test_session('act-basic')
        parent = AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='work',
        )
        child = AgentSessionActivity.objects.create(
            session=session,
            parent=parent,
            seq=2,
            kind=AgentSessionActivityKind.STATUS,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='checkpoint',
        )

        self.assertEqual(parent.revision, 1)
        self.assertIsNone(parent.parent_id)
        self.assertEqual(parent.summary, '')
        self.assertEqual(parent.details, {})
        self.assertEqual(child.parent_id, parent.id)
        self.assertEqual(list(parent.children.all()), [child])

    def test_session_seq_is_unique(self) -> None:
        """A session cannot contain two activities with the same sequence."""
        session = make_test_session('act-unique')
        AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionActivityKind.INPUT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='input',
        )

        with self.assertRaises(IntegrityError):
            AgentSessionActivity.objects.create(
                session=session,
                seq=1,
                kind=AgentSessionActivityKind.OUTPUT,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='output',
            )

    def test_stream_dict_serializes_full_activity(self) -> None:
        """The stream payload contains JSON-safe values for every activity field."""
        session = make_test_session('act-stream')
        child_session = AgentSession.objects.create(
            agent=session.agent,
            agent_config=session.agent_config,
            status=session.status,
            trigger_type=session.trigger_type,
            parent_session=session,
        )
        started_at = timezone.now()
        ended_at = timezone.now()
        parent = AgentSessionActivity.objects.create(
            session=session,
            seq=1,
            kind=AgentSessionActivityKind.LLM,
            status=AgentSessionActivityStatus.RUNNING,
            name='generation',
        )
        activity = AgentSessionActivity.objects.create(
            session=session,
            parent=parent,
            seq=2,
            revision=3,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='delegate',
            summary='Finished delegated work',
            details={'result': 'done'},
            model='gpt-test',
            input_tokens=12,
            output_tokens=4,
            cost_usd=Decimal('0.001234'),
            latency_ms=25,
            started_at=started_at,
            ended_at=ended_at,
            child_session=child_session,
        )

        self.assertEqual(
            activity.to_stream_dict(),
            {
                'id': str(activity.id),
                'session_id': str(session.id),
                'parent_id': str(parent.id),
                'seq': 2,
                'revision': 3,
                'kind': AgentSessionActivityKind.SUBAGENT,
                'status': AgentSessionActivityStatus.SUCCEEDED,
                'name': 'delegate',
                'summary': 'Finished delegated work',
                'details': {'result': 'done'},
                'model': 'gpt-test',
                'input_tokens': 12,
                'output_tokens': 4,
                'cost_usd': '0.001234',
                'latency_ms': 25,
                'started_at': started_at.isoformat(),
                'ended_at': ended_at.isoformat(),
                'created_at': activity.created_at.isoformat(),
                'child_session_id': str(child_session.id),
            },
        )


class TestActivityCommands(OTransactionTestCase):
    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_create_publishes_full_activity_after_commit(self, mock_publish: Any) -> None:
        """Create emits one complete revision only after its transaction commits."""
        session = make_test_session('publish-create')

        with transaction.atomic():
            row = session_commands.create_activity(
                session,
                kind=AgentSessionActivityKind.STATUS,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='ready',
                summary='Ready',
                details={'step': 1},
            )
            mock_publish.assert_not_called()

        mock_publish.assert_called_once_with(session.id, row.to_stream_dict())

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_update_publishes_new_revision_after_commit(self, mock_publish: Any) -> None:
        """Update emits exactly the complete newly committed activity revision."""
        session = make_test_session('publish-update')
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='work',
            summary='Working',
            details={},
        )
        mock_publish.reset_mock()

        with transaction.atomic():
            updated = session_commands.update_activity(
                row.id,
                status=AgentSessionActivityStatus.SUCCEEDED,
                summary='Done',
            )
            mock_publish.assert_not_called()

        mock_publish.assert_called_once_with(session.id, updated.to_stream_dict())

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_create_rollback_suppresses_publication(self, mock_publish: Any) -> None:
        """A rolled-back activity never reaches the committed activity stream."""
        session = make_test_session('publish-rollback')

        with self.assertRaises(IntegrityError), transaction.atomic():
            session_commands.create_activity(
                session,
                kind=AgentSessionActivityKind.STATUS,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='rolled-back',
                summary='',
                details={},
            )
            raise IntegrityError('force rollback')

        mock_publish.assert_not_called()
        self.assertFalse(AgentSessionActivity.objects.filter(session=session).exists())

    @patch('apps.sessions.services.commands.publish_session_activity')
    def test_create_validation_suppresses_publication(self, mock_publish: Any) -> None:
        """Rejected activity input schedules no stream publication."""
        session = make_test_session('publish-validation')

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                session,
                kind='unknown',
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='invalid',
                summary='',
                details={},
            )

        mock_publish.assert_not_called()

    def test_create_rejects_unknown_kind(self) -> None:
        """Create validation rejects a kind outside the canonical vocabulary."""
        session = make_test_session('invalid-kind')

        with self.assertRaises(ValidationError) as raised:
            session_commands.create_activity(
                session,
                kind='unknown',
                status=AgentSessionActivityStatus.RUNNING,
                name='bad',
                summary='',
                details={},
            )

        self.assertEqual(raised.exception.message_dict, {'kind': ['invalid activity kind']})
        self.assertFalse(AgentSessionActivity.objects.filter(session=session).exists())

    def test_create_rejects_unknown_status(self) -> None:
        """Create validation rejects a status outside the lifecycle vocabulary."""
        session = make_test_session('invalid-status')

        with self.assertRaises(ValidationError) as raised:
            session_commands.create_activity(
                session,
                kind=AgentSessionActivityKind.SPAN,
                status='unknown',
                name='bad',
                summary='',
                details={},
            )

        self.assertEqual(raised.exception.message_dict, {'status': ['invalid activity status']})
        self.assertFalse(AgentSessionActivity.objects.filter(session=session).exists())

    def test_update_rejects_unknown_status_without_revision(self) -> None:
        """Update validation leaves the row untouched for an unknown status."""
        session = make_test_session('invalid-update-status')
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='work',
            summary='running',
            details={},
        )

        with self.assertRaises(ValidationError) as raised:
            session_commands.update_activity(row.id, status='unknown')

        self.assertEqual(raised.exception.message_dict, {'status': ['invalid activity status']})
        row.refresh_from_db()
        self.assertEqual(row.status, AgentSessionActivityStatus.RUNNING)
        self.assertEqual(row.revision, 1)

    def test_create_nested_child_under_same_session(self) -> None:
        """Service-created children retain their parent and creation order."""
        session = make_test_session('nest-1')
        parent = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='work',
            summary='doing work',
            details={},
        )
        child = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.STATUS,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='note',
            summary='checkpoint',
            details={},
            parent_id=parent.id,
        )

        self.assertEqual(child.parent_id, parent.id)
        self.assertEqual(child.seq, 2)
        self.assertEqual(
            [row.id for row in session_queries.activities_for(session.id)],
            [parent.id, child.id],
        )
        self.assertIsNotNone(parent.started_at)
        self.assertIsNone(child.ended_at)

    def test_rejects_cross_session_parent(self) -> None:
        """An activity cannot nest below an activity from another session."""
        first = make_test_session('nest-a')
        second = make_test_session('nest-b')
        parent = session_commands.create_activity(
            first,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='first',
            summary='',
            details={},
        )

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                second,
                kind=AgentSessionActivityKind.STATUS,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='bad',
                summary='',
                details={},
                parent_id=parent.id,
            )

    def test_rejects_missing_parent(self) -> None:
        """A supplied parent identifier must resolve to an activity."""
        session = make_test_session('nest-missing')

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                session,
                kind=AgentSessionActivityKind.STATUS,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='bad',
                summary='',
                details={},
                parent_id=uuid.uuid4(),
            )

    def test_create_rejects_child_link_on_non_subagent(self) -> None:
        """Only a subagent activity may carry a child-session reference."""
        parent = make_test_session('link-kind-create')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                parent,
                kind=AgentSessionActivityKind.SPAN,
                status=AgentSessionActivityStatus.RUNNING,
                name='invalid-link',
                summary='',
                details={},
                child_session_id=child.id,
            )

        self.assertFalse(parent.activities.exists())

    def test_create_rejects_child_link_from_another_parent(self) -> None:
        """A referenced child must point directly to the activity's session."""
        parent = make_test_session('link-parent-create')
        other_parent = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
        )
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=other_parent,
        )

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                parent,
                kind=AgentSessionActivityKind.SUBAGENT,
                status=AgentSessionActivityStatus.RUNNING,
                name='invalid-parent',
                summary='',
                details={},
                child_session_id=child.id,
            )

    def test_create_rejects_cross_user_child_link(self) -> None:
        """A corrupt cross-user child cannot be exposed from a parent activity."""
        parent = make_test_session('link-owner-create-parent')
        foreign_child = make_test_session('link-owner-create-child')
        AgentSession.objects.filter(pk=foreign_child.id).update(parent_session_id=parent.id)
        foreign_child.refresh_from_db()

        with self.assertRaises(ValidationError):
            session_commands.create_activity(
                parent,
                kind=AgentSessionActivityKind.SUBAGENT,
                status=AgentSessionActivityStatus.RUNNING,
                name='invalid-owner',
                summary='',
                details={},
                child_session_id=foreign_child.id,
            )

    def test_update_rejects_malformed_child_links(self) -> None:
        """Link-on-update enforces kind, direct parentage, and ownership."""
        parent = make_test_session('link-update-parent')
        valid_child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        other_parent = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
        )
        other_child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=other_parent,
        )
        foreign_child = make_test_session('link-update-foreign')
        AgentSession.objects.filter(pk=foreign_child.id).update(parent_session_id=parent.id)
        foreign_child.refresh_from_db()
        span = session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SPAN,
            status=AgentSessionActivityStatus.RUNNING,
            name='span',
            summary='',
            details={},
        )
        reference = session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.PENDING,
            name='child',
            summary='',
            details={},
        )

        malformed = (
            (span.id, valid_child.id),
            (reference.id, other_child.id),
            (reference.id, foreign_child.id),
        )
        update_with_link = cast(Any, vars(session_commands)['update_activity'])
        for activity_id, child_session_id in malformed:
            with self.subTest(activity_id=activity_id, child_session_id=child_session_id):
                with self.assertRaises(TypeError):
                    update_with_link(
                        activity_id,
                        child_session_id=child_session_id,
                    )

        span.refresh_from_db()
        reference.refresh_from_db()
        self.assertIsNone(span.child_session_id)
        self.assertIsNone(reference.child_session_id)
        self.assertEqual(span.revision, 1)
        self.assertEqual(reference.revision, 1)

    def test_update_rejects_clearing_child_link(self) -> None:
        """A linked subagent cannot lose its canonical child identity."""
        parent = make_test_session('link-clear')
        child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        reference = session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.RUNNING,
            name='child',
            summary='',
            details={},
            child_session_id=child.id,
        )

        update_with_link = cast(Any, vars(session_commands)['update_activity'])
        with self.assertRaises(TypeError):
            update_with_link(
                reference.id,
                child_session_id=None,
            )

        reference.refresh_from_db()
        self.assertEqual(reference.child_session_id, child.id)
        self.assertEqual(reference.revision, 1)

    def test_update_rejects_replacing_child_link(self) -> None:
        """A linked subagent cannot be retargeted to another valid child."""
        parent = make_test_session('link-replace')
        first_child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        second_child = AgentSession.objects.create(
            agent=parent.agent,
            agent_config=parent.agent_config,
            status=parent.status,
            trigger_type=parent.trigger_type,
            parent_session=parent,
        )
        reference = session_commands.create_activity(
            parent,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.RUNNING,
            name='child',
            summary='',
            details={},
            child_session_id=first_child.id,
        )

        update_with_link = cast(Any, vars(session_commands)['update_activity'])
        with self.assertRaises(TypeError):
            update_with_link(
                reference.id,
                child_session_id=second_child.id,
            )

        reference.refresh_from_db()
        self.assertEqual(reference.child_session_id, first_child.id)
        self.assertEqual(reference.revision, 1)

    def test_update_increments_revision_and_closes_lifecycle(self) -> None:
        """Completing a running activity increments its revision and end time."""
        session = make_test_session('rev-1')
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.RUNNING,
            name='clock__now',
            summary='running',
            details={'call_id': 'c1'},
        )

        updated = session_commands.update_activity(
            row.id,
            status=AgentSessionActivityStatus.SUCCEEDED,
            summary='done',
            details={'call_id': 'c1', 'result': 'ok'},
            latency_ms=12,
        )

        self.assertEqual(updated.revision, 2)
        self.assertEqual(updated.status, AgentSessionActivityStatus.SUCCEEDED)
        self.assertEqual(updated.latency_ms, 12)
        self.assertIsNotNone(updated.ended_at)

    def test_terminal_activity_is_immutable_without_reconciliation(self) -> None:
        """Normal updates cannot mutate an activity after it becomes terminal."""
        session = make_test_session('rev-terminal')
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.TOOL,
            status=AgentSessionActivityStatus.SUCCEEDED,
            name='clock__now',
            summary='done',
            details={},
        )

        with self.assertRaises(ValidationError):
            session_commands.update_activity(
                row.id,
                status=AgentSessionActivityStatus.FAILED,
                summary='nope',
                details={},
            )

    def test_update_has_no_terminal_reconciliation_escape(self) -> None:
        """Only the authoritative child-session command may revise terminal references."""
        public_parameters = inspect.signature(session_commands.update_activity).parameters
        row_parameters = inspect.signature(session_activities.update_activity_row).parameters

        self.assertNotIn('allow_terminal_reconcile', public_parameters)
        self.assertNotIn('allow_terminal_reconcile', row_parameters)

    def test_update_explicitly_clears_nullable_metadata(self) -> None:
        """Explicit nulls clear nullable values while omitted fields remain unchanged."""
        session = make_test_session('rev-clear')
        child_session = AgentSession.objects.create(
            agent=session.agent,
            agent_config=session.agent_config,
            status=session.status,
            trigger_type=session.trigger_type,
            parent_session=session,
        )
        row = session_commands.create_activity(
            session,
            kind=AgentSessionActivityKind.SUBAGENT,
            status=AgentSessionActivityStatus.RUNNING,
            name='delegate',
            summary='still running',
            details={},
            model='gpt-test',
            latency_ms=25,
            child_session_id=child_session.id,
        )

        updated = session_commands.update_activity(
            row.id,
            model=None,
            latency_ms=None,
        )

        self.assertIsNone(updated.model)
        self.assertIsNone(updated.latency_ms)
        self.assertEqual(updated.child_session_id, child_session.id)
        self.assertEqual(updated.summary, 'still running')
        self.assertEqual(updated.revision, 2)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_creates_receive_contiguous_sequences(self) -> None:
        """Concurrent database writers serialize through the owning session lock."""
        session = make_test_session('concurrent-seq')
        barrier = Barrier(2)

        def create_in_thread(index: int) -> int:
            """Create through an independent connection after both writers arrive."""
            close_old_connections()
            try:
                thread_session = AgentSession.objects.get(pk=session.id)
                barrier.wait(timeout=5)
                row = session_commands.create_activity(
                    thread_session,
                    kind=AgentSessionActivityKind.STATUS,
                    status=AgentSessionActivityStatus.SUCCEEDED,
                    name=f'writer-{index}',
                    summary='',
                    details={},
                )
                return row.seq
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sequences = list(executor.map(create_in_thread, range(2)))

        self.assertEqual(sorted(sequences), [1, 2])
        self.assertEqual(
            list(AgentSessionActivity.objects.filter(session=session).order_by('seq').values_list('seq', flat=True)),
            [1, 2],
        )

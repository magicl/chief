# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Owner-mode invariants for sessions and hourly usage."""

from unittest.mock import patch

from apps.sessions.models import AgentSession, HourlyUsage, TriggerType
from apps.sessions.tests.base import make_algorithm_session, make_test_session
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from libs.algorithms import CHAT_NAME_ID

from olib.py.django.test.cases import OTestCase

User = get_user_model()


class TestAgentSessionOwner(OTestCase):
    def test_agent_session_populates_matching_required_user(self) -> None:
        """Agent-owned sessions copy their user from the agent when omitted."""
        session = make_test_session('owner-agent')

        self.assertEqual(session.user_id, session.agent.user_id)
        self.assertIsNone(session.algorithm_id)

    def test_algorithm_session_has_only_algorithm_owner_fields(self) -> None:
        """Algorithm-owned sessions carry a user but no agent ancestry fields."""
        session = make_algorithm_session('owner-algorithm')

        self.assertEqual(session.algorithm_id, CHAT_NAME_ID)
        self.assertIsNotNone(session.user_id)
        self.assertIsNone(session.agent_id)
        self.assertIsNone(session.agent_config_id)
        self.assertIsNone(session.parent_session_id)
        self.assertEqual(session.trigger_type, TriggerType.ALGORITHM)
        self.assertIn(CHAT_NAME_ID, str(session))

    def test_algorithm_session_skips_agent_ancestry_validation(self) -> None:
        """Algorithm creation must not enter validation that dereferences an agent."""
        user = User.objects.create_user(username='algorithm-no-ancestry', password='test')

        with patch.object(
            AgentSession,
            '_validate_locked_ancestry',
            side_effect=AssertionError('agent ancestry must be skipped'),
        ):
            session = AgentSession.objects.create(
                user=user,
                algorithm_id=CHAT_NAME_ID,
                trigger_type=TriggerType.ALGORITHM,
            )

        self.assertEqual(session.user_id, user.id)

    def test_rejects_both_owner_modes(self) -> None:
        """A session cannot combine agent and algorithm ownership."""
        agent_session = make_test_session('owner-both')

        with self.assertRaises(ValidationError):
            AgentSession.objects.create(
                user=agent_session.user,
                agent=agent_session.agent,
                agent_config=agent_session.agent_config,
                algorithm_id=CHAT_NAME_ID,
                trigger_type=TriggerType.ALGORITHM,
            )

    def test_rejects_missing_or_invalid_owner_at_model_boundary(self) -> None:
        """Session saves reject absent owners and unregistered algorithms."""
        user = User.objects.create_user(username='owner-invalid', password='test')

        with self.assertRaises(ValidationError):
            AgentSession.objects.create(user=user, trigger_type=TriggerType.ALGORITHM)
        with self.assertRaises(ValidationError):
            AgentSession.objects.create(
                user=user,
                algorithm_id='not_registered',
                trigger_type=TriggerType.ALGORITHM,
            )

    def test_rejects_mismatched_agent_config_and_user(self) -> None:
        """Agent sessions require one agent's config and matching user."""
        first = make_test_session('owner-first')
        second = make_test_session('owner-second')

        with self.assertRaises(ValidationError):
            AgentSession.objects.create(
                user=first.user,
                agent=first.agent,
                agent_config=second.agent_config,
                trigger_type=TriggerType.TRIGGER,
            )
        with self.assertRaises(ValidationError):
            AgentSession.objects.create(
                user=second.user,
                agent=first.agent,
                agent_config=first.agent_config,
                trigger_type=TriggerType.TRIGGER,
            )

    def test_database_constraint_rejects_both_and_missing_owners(self) -> None:
        """Bulk updates cannot bypass the owner XOR database invariant."""
        agent_session = make_test_session('owner-db-agent')
        algorithm_session = make_algorithm_session('owner-db-algorithm')

        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentSession.objects.filter(pk=agent_session.pk).update(algorithm_id=CHAT_NAME_ID)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentSession.objects.filter(pk=algorithm_session.pk).update(algorithm_id=None)

    def test_database_rejects_null_algorithm_session_user(self) -> None:
        """The required user column protects algorithm sessions below model APIs."""
        session = make_algorithm_session('owner-db-user')

        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentSession.objects.filter(pk=session.pk).update(user=None)

    def test_owner_fields_are_immutable(self) -> None:
        """User and all mode-defining fields remain frozen after creation."""
        agent_session = make_test_session('owner-immutable-agent')
        other_user = User.objects.create_user(username='owner-other', password='test')
        agent_session.user = other_user
        with self.assertRaises(ValidationError):
            agent_session.save()

        algorithm_session = make_algorithm_session('owner-immutable-algorithm')
        algorithm_session.algorithm_id = 'not_registered'
        with self.assertRaises(ValidationError):
            algorithm_session.save()


class TestHourlyUsageOwner(OTestCase):
    def test_agent_and_algorithm_buckets_use_exact_owner_modes(self) -> None:
        """Hourly usage supports separate agent and algorithm bucket identities."""
        session = make_test_session('usage-agent')
        algorithm_session = make_algorithm_session('usage-algorithm')
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)

        agent_row = HourlyUsage.objects.create(
            user=session.user,
            agent=session.agent,
            hour=hour,
            model='agent-model',
        )
        algorithm_row = HourlyUsage.objects.create(
            user=algorithm_session.user,
            algorithm_id=CHAT_NAME_ID,
            hour=hour,
            model='algorithm-model',
        )

        self.assertEqual(agent_row.user_id, session.agent.user_id)
        self.assertIsNone(agent_row.algorithm_id)
        self.assertIsNone(algorithm_row.agent_id)

    def test_model_rejects_invalid_usage_owner_modes(self) -> None:
        """Normal model creation rejects missing, combined, and unknown owners."""
        session = make_test_session('usage-model-invalid')
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)

        with self.assertRaises(ValidationError):
            HourlyUsage.objects.create(user=session.user, hour=hour, model='missing')
        with self.assertRaises(ValidationError):
            HourlyUsage.objects.create(
                user=session.user,
                agent=session.agent,
                algorithm_id=CHAT_NAME_ID,
                hour=hour,
                model='both',
            )
        with self.assertRaises(ValidationError):
            HourlyUsage.objects.create(
                user=session.user,
                algorithm_id='not_registered',
                hour=hour,
                model='unknown',
            )

    def test_update_or_create_preserves_valid_usage_owner(self) -> None:
        """The aggregation write pattern remains valid with model checks enabled."""
        session = make_test_session('usage-update-or-create')
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)

        row, created = HourlyUsage.objects.update_or_create(
            user=session.user,
            agent=session.agent,
            hour=hour,
            model='model',
            defaults={'iteration_count': 1},
        )
        updated, created_again = HourlyUsage.objects.update_or_create(
            user=session.user,
            agent=session.agent,
            hour=hour,
            model='model',
            defaults={'iteration_count': 2},
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(updated.pk, row.pk)
        self.assertEqual(updated.iteration_count, 2)

    def test_database_constraint_rejects_both_and_missing_usage_owners(self) -> None:
        """Hourly usage owner XOR remains enforced below model APIs."""
        session = make_test_session('usage-db-agent')
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)
        row = HourlyUsage.objects.create(
            user=session.user,
            agent=session.agent,
            hour=hour,
            model='model',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            HourlyUsage.objects.filter(pk=row.pk).update(algorithm_id=CHAT_NAME_ID)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HourlyUsage.objects.filter(pk=row.pk).update(agent=None)

    def test_database_rejects_null_algorithm_usage_user(self) -> None:
        """The required user column protects algorithm usage below model APIs."""
        session = make_algorithm_session('usage-db-user')
        row = HourlyUsage.objects.create(
            user=session.user,
            algorithm_id=CHAT_NAME_ID,
            hour=timezone.now().replace(minute=0, second=0, microsecond=0),
            model='model',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            HourlyUsage.objects.filter(pk=row.pk).update(user=None)

    def test_agent_bucket_unique_constraint(self) -> None:
        """Agent buckets reject duplicate owner, hour, and model identities."""
        session = make_test_session('usage-unique-agent')
        values = {
            'user': session.user,
            'agent': session.agent,
            'hour': timezone.now().replace(minute=0, second=0, microsecond=0),
            'model': 'model',
        }
        HourlyUsage.objects.create(**values)

        with self.assertRaises(IntegrityError), transaction.atomic():
            HourlyUsage.objects.create(**values)

    def test_algorithm_bucket_unique_constraint(self) -> None:
        """Algorithm buckets reject duplicate user, algorithm, hour, and model identities."""
        session = make_algorithm_session('usage-unique-algorithm', algorithm_id=CHAT_NAME_ID)
        values = {
            'user': session.user,
            'algorithm_id': session.algorithm_id,
            'hour': timezone.now().replace(minute=0, second=0, microsecond=0),
            'model': 'model',
        }
        HourlyUsage.objects.create(**values)

        with self.assertRaises(IntegrityError), transaction.atomic():
            HourlyUsage.objects.create(**values)

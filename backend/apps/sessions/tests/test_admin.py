# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Admin regressions for immutable session activity history."""

from apps.sessions.admin import (
    AgentSessionActivityAdmin,
    AgentSessionActivityInline,
    AgentSessionAdmin,
)
from apps.sessions.models import AgentSession, AgentSessionActivity
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from olib.py.django.test.cases import OTransactionTestCase


class TestAgentSessionActivityAdmin(OTransactionTestCase):
    def test_session_list_can_filter_by_user(self) -> None:
        """Session admin exposes the required owner user as a list filter."""
        session_admin = AgentSessionAdmin(AgentSession, admin.site)

        self.assertIn('user', session_admin.list_filter)

    def test_superuser_cannot_add_or_delete_activity_history(self) -> None:
        """Both direct and inline activity admin surfaces reject creation and deletion."""
        request = RequestFactory().get('/admin/agent_sessions/agentsessionactivity/')
        request.user = get_user_model().objects.create_superuser(
            username='activity-admin',
            password='test-password',
        )
        activity_admin = AgentSessionActivityAdmin(AgentSessionActivity, admin.site)
        activity_inline = AgentSessionActivityInline(AgentSession, admin.site)

        self.assertFalse(activity_admin.has_add_permission(request))
        self.assertFalse(activity_admin.has_delete_permission(request))
        self.assertFalse(activity_inline.has_add_permission(request))
        self.assertFalse(activity_inline.has_delete_permission(request))

    def test_all_activity_fields_remain_read_only(self) -> None:
        """The change form exposes every persisted activity field as read-only."""
        activity_admin = AgentSessionActivityAdmin(AgentSessionActivity, admin.site)
        persisted_fields = {field.name for field in AgentSessionActivity._meta.fields}

        self.assertLessEqual(persisted_fields, set(activity_admin.readonly_fields))

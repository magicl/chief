# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.sessions.tests.base import make_test_session
from apps.web.tests.test_activity_snapshot import make_parent_with_subagent
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


class TestSessionActivityView(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.session = make_test_session('event-view-agent')
        user = get_user_model().objects.get(username='user-event-view-agent')
        self.client.force_login(user)

    def test_session_page_contains_event_panel(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="event-panel"')
        self.assertContains(response, 'Events')
        self.assertContains(response, 'Following')

    def test_session_page_groups_controls_and_navigation(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'session-control')
        self.assertContains(response, 'Session status')
        self.assertContains(response, 'Back to agent')
        self.assertContains(response, 'New session')

    def test_session_page_includes_follow_and_tree_logic(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'toggleFollow')
        self.assertContains(response, 'scrollToBottom')
        self.assertContains(response, 'rootStore')
        self.assertNotContains(response, 'formatEventStats')

    def test_session_page_loads_activity_tree_module(self) -> None:
        """The page loads the recursive store and exposes semantic tree markup."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        body = response.content.decode()
        self.assertContains(response, 'web/activity_tree.js')
        self.assertContains(response, 'id="activity-tree"')
        self.assertContains(response, 'aria-expanded')
        self.assertLess(body.index('web/activity_tree.js'), body.index('function sessionView'))

    def test_session_page_includes_compact_activity_tree_styles(self) -> None:
        """Shared frame styles define compact tree layout, focus, and raw JSON bounds."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'class="activity-tree"')
        self.assertContains(response, 'class="activity-row-main"')
        self.assertContains(response, 'class="activity-toggle"')
        self.assertContains(response, 'class="activity-raw-pre"')
        self.assertContains(response, '.activity-tree {')
        self.assertContains(response, '.activity-toggle:focus-visible {')
        self.assertContains(response, '.activity-raw-pre {')
        self.assertContains(response, '@media (max-width: 640px)')

    def test_session_page_loads_rich_output_assets(self) -> None:
        """The session page loads the rich renderer bundle and styles."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        body = response.content.decode()
        self.assertContains(response, 'web/rich-content/rich_content.bundle.js')
        self.assertContains(response, 'web/rich-content/rich_content.bundle.css')
        self.assertLess(body.index('rich_content.bundle.js'), body.index('function sessionView'))

    def test_session_page_has_default_on_beautify_control(self) -> None:
        """Beautification starts on with an understandable pressed control."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'class="event-toolbar-actions"')
        self.assertContains(response, 'beautify: true')
        self.assertContains(response, ':aria-pressed="beautify.toString()"')
        self.assertContains(response, '@click="toggleBeautify()"')
        self.assertContains(response, "beautify ? 'Beautify: On' : 'Beautify: Off'")
        self.assertContains(response, ':class="{ \'active\': beautify }"')

    def test_session_page_routes_only_outputs_to_rich_renderer(self) -> None:
        """Only lowercase output activity content enters the rich renderer."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, '<template x-if="activity.kind === \'output\'">')
        self.assertContains(response, 'class="event-body rich-output"')
        self.assertContains(response, 'x-show="beautify && richContentReady"')
        self.assertContains(
            response,
            'x-effect="beautify && richContentReady && renderOutput($el, activity)"',
        )
        self.assertContains(response, 'x-show="!beautify || !richContentReady"')
        self.assertContains(response, '<template x-if="activity.kind === \'input\'">')
        self.assertContains(response, 'x-text="formatPayload(activity)"', count=2)

    def test_session_page_keeps_beautify_state_local(self) -> None:
        """Beautify state stays page-local and cancels hidden rich output."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertNotContains(response, 'localStorage')
        self.assertNotContains(response, 'sessionStorage')
        self.assertContains(response, 'renderedOutputs: new WeakMap()')
        self.assertContains(response, "this.$refs.eventPanel.querySelectorAll('.rich-output')")
        self.assertNotContains(response, "document.querySelectorAll('.rich-output')")
        self.assertContains(response, 'cancelRichContent(element)')
        self.assertContains(response, 'renderRichOutputAttempt')

    def test_session_page_observes_delayed_rich_content_readiness(self) -> None:
        """The session reacts to renderer readiness and removes its listener."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'web/rich_content_lifecycle.js')
        self.assertContains(response, 'richContentReady:')
        self.assertContains(response, 'watchRichContentReadiness')
        self.assertContains(response, 'this._stopWatchingRichContent?.()')

    def test_session_page_has_no_expandable_event_log(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertNotContains(response, 'event-log-toggle')
        self.assertNotContains(response, 'Conversation')
        self.assertNotContains(response, 'dialog-messages')

    def test_child_session_page_shows_parent_breadcrumb(self) -> None:
        """A child session links back to its direct parent."""
        parent, child, _ = make_parent_with_subagent('ui-crumb')
        user = get_user_model().objects.get(username='user-ui-crumb')
        self.client.force_login(user)

        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': child.id}),
        )

        self.assertContains(response, 'Parent session')
        self.assertContains(
            response,
            reverse('session_detail', kwargs={'session_id': parent.id}),
        )
        # Stored name when set; otherwise template falls back to id hex prefix.
        expected_label = parent.name or parent.id.hex[:8]
        self.assertContains(response, expected_label)

    def test_session_requires_login(self) -> None:
        client = Client()
        response = client.get(reverse('session_detail', kwargs={'session_id': self.session.id}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_dashboard_session_links(self) -> None:
        response = self.client.get(reverse('dashboard'))
        self.assertContains(
            response,
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )

    def test_dashboard_agent_link_in_recent_sessions(self) -> None:
        response = self.client.get(reverse('dashboard'))
        self.assertContains(
            response,
            reverse('agent_detail', kwargs={'agent_id': self.session.agent.id}),
        )

    def test_session_link_navigates(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'frame-page')
        self.assertContains(response, 'event-panel')

    def test_chief_header_links_to_dashboard(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, f'href="{reverse("dashboard")}"')
        self.assertContains(response, '>Chief</a>')

    def test_session_header_shows_model_and_total_cost(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'openai / gpt-5.4-mini')
        self.assertContains(response, 'session-header-meta')
        self.assertContains(response, 'formatTotalCost')
        self.assertContains(response, 'totalCostUsd')

    def test_session_x_data_attribute_is_valid(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        body = response.content.decode()
        marker = "x-data='sessionView("
        start = body.find(marker)
        self.assertGreater(start, -1, 'sessionView x-data not found')
        end = body.find("' x-init=", start)
        self.assertGreater(end, start)
        x_data = body[start + len('x-data=') : end + 1]
        self.assertIn('"openai / gpt-5.4-mini"', x_data)
        self.assertNotIn('x-data="sessionView', body)

    def test_session_page_delegates_revisioned_activity_streams_to_store(self) -> None:
        """The page controller uses the canonical store without a flat adapter."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'createActivityStore(sessionId)')
        self.assertContains(response, 'await this.rootStore.loadRoot()')
        self.assertNotContains(response, 'events: []')
        self.assertNotContains(response, 'activityById')
        self.assertNotContains(response, 'upsertActivity')

    def test_session_page_formats_tree_details_and_root_cost(self) -> None:
        """Tree rows show curated/raw details while totals use only the root store."""
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'Raw JSON')
        self.assertContains(response, 'formatRawDetails(activity.details)')
        self.assertContains(response, 'Object.values(this.rootStore.activities)')
        self.assertNotContains(response, 'Object.values(this.rootStore.childStores)')

    def test_session_closes_sse_on_navigation(self) -> None:
        response = self.client.get(
            reverse('session_detail', kwargs={'session_id': self.session.id}),
        )
        self.assertContains(response, 'rootStore.dispose()')
        self.assertContains(response, 'pagehide')
        self.assertContains(response, 'refreshExpandedSnapshots')

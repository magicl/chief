# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for user-scoped agent and credential list fragments."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from unittest.mock import patch

from apps.agents.services.config_commands import create_from_example
from apps.keys.services import commands as key_commands
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


@dataclass
class ParsedElement:
    """Represent one parsed HTML element with structural relationships."""

    tag: str
    attrs: dict[str, str | None]
    parent: ParsedElement | None
    children: list[ParsedElement] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def descendants(self) -> list[ParsedElement]:
        """Return all nested elements in document order."""
        nested: list[ParsedElement] = []
        for child in self.children:
            nested.append(child)
            nested.extend(child.descendants())
        return nested

    def text_content(self) -> str:
        """Return normalized text from this element and its descendants."""
        text = [*self.text_parts]
        for child in self.children:
            text.append(child.text_content())
        return ' '.join(' '.join(text).split())


class HtmlDocumentParser(HTMLParser):
    """Build the minimal element tree needed for structural template assertions."""

    _VOID_TAGS = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source'})

    def __init__(self) -> None:
        """Initialize root elements, document order, and open ancestry."""
        super().__init__()
        self.roots: list[ParsedElement] = []
        self.elements: list[ParsedElement] = []
        self._stack: list[ParsedElement] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Add an element beneath the current open parent."""
        parent = self._stack[-1] if self._stack else None
        element = ParsedElement(tag=tag, attrs=dict(attrs), parent=parent)
        if parent is None:
            self.roots.append(element)
        else:
            parent.children.append(element)
        self.elements.append(element)
        if tag not in self._VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Process a self-closing element without changing ancestry."""
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        """Close the most recent matching element for resilient HTML parsing."""
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        """Attach text to its currently open element."""
        if self._stack:
            self._stack[-1].text_parts.append(data)

    def elements_with_id(self, element_id: str) -> list[ParsedElement]:
        """Return every element carrying the requested id."""
        return [element for element in self.elements if element.attrs.get('id') == element_id]


class TestResourcePartials(OTransactionTestCase):
    """Verify partial authentication, ownership, and stable page shells."""

    def setUp(self) -> None:
        """Create isolated users and suppress resource transport side effects."""
        super().setUp()
        redis_client = patch('apps.bus.resources.sync_client')
        redis_client.start()
        self.addCleanup(redis_client.stop)
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='partial-user', password='test')
        self.other = User.objects.create_user(username='partial-other', password='test')

    def test_partial_routes_are_exact(self) -> None:
        """Keep the two fragment routes stable for htmx callers."""
        self.assertEqual(reverse('dashboard_agents_partial'), '/partials/agents/')
        self.assertEqual(reverse('settings_keys_partial'), '/partials/keys/')

    def test_agent_partial_requires_login_for_htmx(self) -> None:
        """Redirect anonymous htmx agent requests to the exact admin login URL."""
        response = self.client.get(reverse('dashboard_agents_partial'), HTTP_HX_REQUEST='true')
        self.assertRedirects(
            response,
            '/admin/login/?next=/partials/agents/',
            fetch_redirect_response=False,
        )

    def test_agent_partial_lists_only_owned_agents_and_list_content(self) -> None:
        """Render owned agents and creation links without unrelated page state."""
        create_from_example(self.user, 'minimal', name='Mine', identifier='mine')
        create_from_example(self.other, 'minimal', name='Theirs', identifier='theirs')
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard_agents_partial'))

        self.assertContains(response, 'Mine')
        self.assertNotContains(response, 'Theirs')
        self.assertContains(response, 'Agents')
        self.assertContains(response, 'Create agent')
        self.assertContains(response, '?example=clock-assistant')
        self.assertNotContains(response, 'id="agent-list"')
        self.assertNotContains(response, '<section')
        self.assertNotContains(response, 'Usage')
        self.assertNotContains(response, 'Recent sessions')

    def test_key_partial_requires_login_for_htmx(self) -> None:
        """Redirect anonymous htmx key requests to the exact admin login URL."""
        response = self.client.get(reverse('settings_keys_partial'), HTTP_HX_REQUEST='true')
        self.assertRedirects(
            response,
            '/admin/login/?next=/partials/keys/',
            fetch_redirect_response=False,
        )

    def test_key_partial_lists_only_owned_metadata_without_form_state(self) -> None:
        """Render owned metadata without other users, secrets, or add-form state."""
        key_commands.upsert_user_named(self.user.pk, 'mine-key', 'openai', 'mine-secret')
        key_commands.upsert_user_named(self.other.pk, 'their-key', 'openai', 'their-secret')
        self.client.force_login(self.user)

        response = self.client.get(reverse('settings_keys_partial'))

        self.assertContains(response, 'mine-key')
        self.assertNotContains(response, 'their-key')
        self.assertNotContains(response, 'mine-secret')
        self.assertNotContains(response, 'their-secret')
        self.assertNotContains(response, 'id="key-list"')
        self.assertNotContains(response, 'name="secret"')
        self.assertNotContains(response, 'Add key')
        self.assertNotContains(response, 'credential-guides-data')
        self.assertNotContains(response, 'x-data=')

    def test_dashboard_shell_preserves_refresh_target_and_other_sections(self) -> None:
        """Keep one exact agent target while preserving usage and session sections."""
        self.client.force_login(self.user)

        response = self.client.get(reverse('dashboard'))
        body = response.content.decode()
        parser = HtmlDocumentParser()
        parser.feed(body)
        wrappers = parser.elements_with_id('agent-list')

        self.assertEqual(len(wrappers), 1)
        self.assertEqual(
            wrappers[0].attrs,
            {
                'id': 'agent-list',
                'class': 'card',
                'hx-get': '/partials/agents/',
                'hx-trigger': 'chief:agents-changed from:body',
                'hx-swap': 'innerHTML',
            },
        )
        self.assertEqual(wrappers[0].tag, 'section')
        self.assertIn('Usage', body)
        self.assertIn('Recent sessions', body)

    def test_keys_shell_keeps_add_form_outside_refresh_target(self) -> None:
        """Keep one exact key target followed by the complete sibling add card."""
        self.client.force_login(self.user)

        response = self.client.get(reverse('settings_keys'))
        parser = HtmlDocumentParser()
        parser.feed(response.content.decode())
        wrappers = parser.elements_with_id('key-list')

        self.assertEqual(len(wrappers), 1)
        key_list = wrappers[0]
        self.assertEqual(
            key_list.attrs,
            {
                'id': 'key-list',
                'class': 'card',
                'hx-get': '/partials/keys/',
                'hx-trigger': 'chief:keys-changed from:body',
                'hx-swap': 'innerHTML',
            },
        )
        self.assertEqual(key_list.tag, 'div')
        self.assertIsNotNone(key_list.parent)
        assert key_list.parent is not None
        siblings = key_list.parent.children
        add_card = siblings[siblings.index(key_list) + 1]
        self.assertEqual(add_card.tag, 'div')
        self.assertIn('card', (add_card.attrs.get('class') or '').split())

        add_elements = add_card.descendants()
        headings = [element for element in add_elements if element.tag == 'h3']
        forms = [element for element in add_elements if element.tag == 'form']
        guides = [element for element in add_elements if element.attrs.get('id') == 'credential-guides-data']
        alpine_states = [element for element in add_elements if 'x-data' in element.attrs]

        self.assertEqual([heading.text_content() for heading in headings], ['Add key'])
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].attrs.get('method'), 'post')
        self.assertEqual(forms[0].attrs.get('action'), reverse('settings_keys_add_named'))
        form_fields = {element.attrs.get('name') for element in forms[0].descendants()}
        self.assertTrue({'type', 'name', 'secret'}.issubset(form_fields))
        self.assertEqual(len(guides), 1)
        self.assertEqual(guides[0].tag, 'script')
        self.assertEqual(len(alpine_states), 1)
        self.assertIn(forms[0], alpine_states[0].descendants())
        add_element_ids = {id(element) for element in add_elements}
        key_element_ids = {id(element) for element in key_list.descendants()}
        self.assertTrue(add_element_ids.isdisjoint(key_element_ids))

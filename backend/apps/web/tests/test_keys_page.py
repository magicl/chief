# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from apps.keys import crypto
from apps.keys.exceptions import OAuthProviderError, OAuthStateError
from apps.keys.models import CredentialStatus, UserCredential
from apps.keys.oauth.providers.google import GOOGLE_CAPABILITIES, GoogleOAuthProvider
from apps.keys.oauth.services import OAuthStart
from apps.keys.services import commands
from apps.keys.services.queries import get_owned_user_credential
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404, HttpRequest, HttpResponse
from django.test import Client, override_settings
from django.urls import reverse
from libs.providers.key.health_codes import HEALTH_CODE_LABELS

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

AUTHORIZATION_CODE_SENTINEL = 'authorization-code-sentinel'
REFRESH_TOKEN_SENTINEL = 'refresh-token-sentinel'
ACCESS_TOKEN_SENTINEL = 'access-token-sentinel'
CLIENT_SECRET_SENTINEL = 'client-secret-sentinel'
SERVICE_ACCOUNT_SENTINEL = '{"private_key":"service-account-json-sentinel"}'
PROVIDER_BODY_SENTINEL = '{"provider_body":"provider-body-sentinel"}'
SECRET_SENTINELS = (
    AUTHORIZATION_CODE_SENTINEL,
    REFRESH_TOKEN_SENTINEL,
    ACCESS_TOKEN_SENTINEL,
    CLIENT_SECRET_SENTINEL,
    SERVICE_ACCOUNT_SENTINEL,
    PROVIDER_BODY_SENTINEL,
)


class _CapabilityCheckboxParser(HTMLParser):
    """Collect capability checkbox attributes from rendered form HTML."""

    def __init__(self) -> None:
        """Initialize an ordered collection of parsed capability inputs."""
        super().__init__()
        self.checkboxes: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record only input elements belonging to the repeated capabilities field."""
        attributes = dict(attrs)
        if tag == 'input' and attributes.get('name') == 'capabilities':
            self.checkboxes.append(attributes)


class _RecordingHandler(logging.Handler):
    """Capture fully formatted log records for secret-surface assertions."""

    def __init__(self) -> None:
        """Initialize an empty formatted-record collection."""
        super().__init__(level=logging.DEBUG)
        self.rendered: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store the complete formatted record, including traceback text."""
        self.rendered.append(self.format(record))


class _RaiseAfterResponseMiddleware:
    """Test middleware that fails while processing a downstream response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the temporary test chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Raise only after the callback view and inner middleware return."""
        self.get_response(request)
        raise RuntimeError('safe downstream response failure')


def _retained_failure_text(failure: BaseException) -> str:
    """Render retained cause, context, and traceback locals without the public message."""
    rendered: list[str] = []
    if failure.__cause__ is not None:
        rendered.append(repr(failure.__cause__))
    if failure.__context__ is not None:
        rendered.append(repr(failure.__context__))
    traceback = failure.__traceback__
    while traceback is not None:
        rendered.append(repr(traceback.tb_frame.f_locals))
        traceback = traceback.tb_next
    return '\n'.join(rendered)


@override_settings(DEBUG=True)
class TestKeysPage(OTransactionTestCase):
    def setUp(self) -> None:
        """Create a client and suppress resource transport outside event tests."""
        super().setUp()
        publisher = patch('apps.bus.resources.publish_resource_update')
        publisher.start()
        self.addCleanup(publisher.stop)
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user(username='keys-user', password='test')

    def _assert_callback_hardening(self, response: Any) -> None:
        """Assert callback responses cannot be cached or used as referrer sources."""
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_requires_login(self) -> None:
        response = self.client.get(reverse('settings_keys'))
        self.assertEqual(response.status_code, 302)

    def test_shows_set_not_set_without_secret_material(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'openai-work', 'openai', 'sk-hidden')
        response = self.client.get(reverse('settings_keys'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Set', response.content)
        self.assertNotIn(b'sk-hidden', response.content)
        self.assertNotIn(b'Replace', response.content)

    def test_add_form_requires_type_before_secret_fields(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'Select a credential type', response.content)
        self.assertIn(b'Choose a type above', response.content)
        self.assertIn(b'credential-guides-data', response.content)

    def test_shows_google_setup_instructions_in_page_data(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'domain-wide delegation', response.content)
        self.assertIn(b'gmail.modify', response.content)
        self.assertIn(b'drive.metadata.readonly', response.content)

    def test_google_form_renders_provider_capability_catalog_unchecked(self) -> None:
        """Expose provider descriptions, scope URLs, and support state without preselection."""
        self.client.force_login(self.user)

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, 'Service account')
        self.assertContains(response, 'OAuth')
        for capability in GOOGLE_CAPABILITIES:
            self.assertContains(response, capability.label)
            self.assertContains(response, capability.description)
            self.assertContains(response, f'{capability.description} ({capability.scope})')
            support_label = 'Available now' if capability.support == 'current' else 'Future support'
            self.assertContains(response, support_label)
        self.assertContains(response, 'Google includes sending in this scope.')
        self.assertContains(response, 'without changing or sending mail.')

        parser = _CapabilityCheckboxParser()
        parser.feed(response.content.decode())
        self.assertEqual(
            [checkbox.get('value') for checkbox in parser.checkboxes],
            [capability.id for capability in GOOGLE_CAPABILITIES],
        )
        for checkbox in parser.checkboxes:
            self.assertEqual(checkbox.get('type'), 'checkbox')
            self.assertNotIn('checked', checkbox)

    def test_google_form_distinguishes_drive_and_future_document_choices(self) -> None:
        """Keep broad and selected-file consent choices visibly distinct."""
        self.client.force_login(self.user)

        response = self.client.get(reverse('settings_keys'))

        for label in (
            'Read Drive metadata',
            'Read Drive files',
            'Manage selected Drive files',
            'Manage all Drive files',
            'Read Google Docs',
            'Manage Google Docs',
            'Read Google Sheets',
            'Manage Google Sheets',
        ):
            self.assertContains(response, label)
        self.assertContains(response, 'Future support', count=7)

    def test_provider_catalog_json_escapes_html_metacharacters(self) -> None:
        """Render provider text once through normal HTML escaping without duplicate JSON."""
        self.client.force_login(self.user)
        capability = GOOGLE_CAPABILITIES[0]
        escaped = type(capability)(
            id=capability.id,
            label='<catalog>&',
            description='safe > markup',
            scope=capability.scope,
            support=capability.support,
        )

        with patch.object(GoogleOAuthProvider, 'capabilities', (escaped,)):
            response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, '&lt;catalog&gt;&amp;', html=False)
        self.assertContains(response, 'safe &gt; markup', html=False)
        self.assertNotContains(response, '<catalog>')
        self.assertNotContains(response, 'oauth-provider-catalog-data')
        self.assertNotContains(response, 'oauthProviders')

    def test_post_add_named(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'gmail-personal', 'type': 'google', 'auth_kind': 'static', 'secret': 'tok'},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertIn(b'gmail-personal', response.content)

    def test_post_add_oauth_needs_capabilities_but_no_secret(self) -> None:
        """Create an unconnected declaration from repeated capability values only."""
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('settings_keys_add_named'),
            {
                'name': 'google-oauth',
                'type': 'google',
                'auth_kind': 'oauth',
                'capabilities': ['drive_metadata', 'gmail_read'],
            },
        )

        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        row = self.user.credentials.get(name='google-oauth')
        self.assertEqual(row.auth_kind, 'oauth')
        self.assertEqual(row.auth_config, {'provider': 'google', 'capabilities': ['gmail_read', 'drive_metadata']})
        self.assertEqual(bytes(row.encrypted_value), b'')

    def test_post_add_multiline_google_json(self) -> None:
        self.client.force_login(self.user)
        secret = '{\n  "type": "service_account",\n  "client_email": "sa@example.com"\n}\n'
        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'google-sa', 'type': 'google', 'secret': secret},
        )
        self.assertEqual(response.status_code, 302)
        from apps.keys.services.queries import resolve_secret

        stored = resolve_secret(self.user.pk, 'google-sa', expected_type='google')
        self.assertIn('\n', stored)
        self.assertIn('service_account', stored)

    def test_post_delete_named(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'clickup', 'clickup', 'tok')
        response = self.client.post(reverse('settings_keys_delete_named', kwargs={'name': 'clickup'}))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(reverse('settings_keys'))
        self.assertNotIn(b'<code>clickup</code>', response.content)

    def test_disk_key_shows_source_without_delete_control(self) -> None:
        self.client.force_login(self.user)
        _, changed = commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )
        self.assertTrue(changed)

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, '<code>disk-openai</code>', html=True)
        self.assertContains(response, 'Disk')
        self.assertNotContains(
            response,
            reverse('settings_keys_delete_named', kwargs={'name': 'disk-openai'}),
        )

    def test_disabled_disk_key_shows_disabled_status(self) -> None:
        """Render disabled metadata instead of treating encrypted content as set."""
        self.client.force_login(self.user)
        _, changed = commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )
        self.assertTrue(changed)
        self.user.credentials.filter(name='disk-openai').update(status='disabled')

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, '<span class="pill waiting">Disabled</span>', html=True)

    def test_needs_attention_rows_show_health_labels_instead_of_set_status(self) -> None:
        """Show the actionable health label for each needs_attention disk row."""
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-empty',
            'openai',
            '',
            source_path='keys/disk-empty.yaml',
            source_rev='sha256:empty',
        )
        commands.upsert_disk_health(
            self.user.pk,
            'disk-broken',
            'openai',
            None,
            health_status='needs_attention',
            health_code='invalid_declaration',
            source_path='keys/disk-broken.yaml',
            source_rev='sha256:broken',
        )
        commands.upsert_disk_health(
            self.user.pk,
            'disk-mystery',
            'mystery',
            None,
            health_status='needs_attention',
            health_code='unknown_type',
            source_path='keys/disk-mystery.yaml',
            source_rev='sha256:mystery',
        )

        response = self.client.get(reverse('settings_keys'))

        self.assertContains(response, 'Value empty')
        self.assertContains(response, 'Invalid declaration')
        self.assertContains(response, 'Unknown type')
        self.assertNotContains(response, '<span class="pill succeeded">Set</span>', html=True)

    def test_authenticate_control_is_hidden_for_invalid_declaration_and_unknown_type(self) -> None:
        """Hide the Authenticate control for disk OAuth rows that cannot start consent."""
        self.client.force_login(self.user)
        commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-google',
            'google',
            None,
            auth_kind='oauth',
            auth_config={'provider': 'google', 'capabilities': ['drive_metadata']},
            source_path='keys/disk-google.yaml',
            source_rev='sha256:oauth',
        )
        row = self.user.credentials.get(name='disk-google')
        authorize_url = reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})

        for health_code in ('invalid_declaration', 'unknown_type'):
            with self.subTest(health_code=health_code):
                UserCredential.objects.filter(pk=row.pk).update(
                    health_status='needs_attention',
                    health_code=health_code,
                )
                response = self.client.get(reverse('settings_keys'))
                self.assertNotContains(response, authorize_url)
                self.assertContains(response, HEALTH_CODE_LABELS[health_code])

    def test_oauth_rows_render_connection_lifecycle_controls(self) -> None:
        """Render connection controls strictly from active metadata and grant presence."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')

        response = self.client.get(reverse('settings_keys'))
        authorize_url = reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})
        disconnect_url = reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk})
        self.assertContains(response, 'OAuth not connected')
        self.assertContains(response, 'Authenticate')
        self.assertContains(response, authorize_url)
        self.assertNotContains(response, disconnect_url)

        row.encrypted_value = crypto.encrypt('grant-sentinel')
        row.health_status = 'ready'
        row.health_code = ''
        row.save(update_fields=['encrypted_value', 'health_status', 'health_code'])
        response = self.client.get(reverse('settings_keys'))
        self.assertContains(response, 'Connected')
        self.assertContains(response, 'Reauthenticate')
        self.assertContains(response, 'Disconnect')
        self.assertContains(response, disconnect_url)
        self.assertNotContains(response, 'grant-sentinel')

    def test_ui_owned_oauth_row_renders_red_cross_delete_action_last(self) -> None:
        """Place the compact delete control after every OAuth lifecycle action."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')
        row.encrypted_value = crypto.encrypt('grant-sentinel')
        row.save(update_fields=['encrypted_value'])

        response = self.client.get(reverse('settings_keys'))

        content = response.content.decode()
        authorize_url = reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})
        disconnect_url = reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk})
        delete_url = reverse('settings_keys_delete_named', kwargs={'name': row.name})
        self.assertContains(response, delete_url)
        self.assertLess(content.index(authorize_url), content.index(delete_url))
        self.assertLess(content.index(disconnect_url), content.index(delete_url))
        self.assertContains(response, f'aria-label="Delete {row.name}"')
        self.assertContains(response, 'color:#ef4444;')
        self.assertContains(response, '>×</button>')

    def test_disk_oauth_keeps_lifecycle_controls_but_disabled_has_none(self) -> None:
        """Allow grant lifecycle actions without making disk declarations editable."""
        self.client.force_login(self.user)
        _, changed = commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-google',
            'google',
            None,
            auth_kind='oauth',
            auth_config={'provider': 'google', 'capabilities': ['drive_metadata']},
            source_path='keys/disk-google.yaml',
            source_rev='sha256:oauth',
        )
        self.assertTrue(changed)
        row = self.user.credentials.get(name='disk-google')
        authorize_url = reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})

        response = self.client.get(reverse('settings_keys'))
        self.assertContains(response, authorize_url)
        self.assertNotContains(response, reverse('settings_keys_delete_named', kwargs={'name': row.name}))

        row.status = CredentialStatus.DISABLED
        row.save(update_fields=['status'])
        response = self.client.get(reverse('settings_keys'))
        self.assertNotContains(response, authorize_url)
        self.assertNotContains(
            response,
            reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk}),
        )

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request', logging.WARNING, r'Method Not Allowed \(GET\): /settings/keys/oauth/.+', count=2
            ),
            ExpectLogItem(
                'django.security.csrf',
                logging.WARNING,
                r'Forbidden \(CSRF cookie not set\.\): /settings/keys/oauth/.+',
                count=2,
            ),
        ]
    )
    def test_oauth_mutation_routes_require_login_post_and_csrf(self) -> None:
        """Protect authorization mutations with login, method, and CSRF checks."""
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')
        authorize_url = reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})
        disconnect_url = reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk})

        self.assertEqual(self.client.post(authorize_url).status_code, 302)
        self.assertEqual(self.client.post(disconnect_url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(authorize_url).status_code, 405)
        self.assertEqual(self.client.get(disconnect_url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(authorize_url).status_code, 403)
        self.assertEqual(csrf_client.post(disconnect_url).status_code, 403)

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID='public-client-id',
        GOOGLE_OAUTH_CLIENT_SECRET='app-secret-sentinel',
    )
    def test_authorize_uses_existing_session_and_fixed_callback(self) -> None:
        """Start with a created session and the sole named callback URL."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')
        authorization_url = 'https://accounts.google.test/authorize?state=safe-state'

        with patch(
            'apps.web.views.oauth_services.start_authorization',
            return_value=OAuthStart(authorization_url=authorization_url, state='safe-state'),
        ) as start:
            response = self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk}))

        self.assertRedirects(response, authorization_url, fetch_redirect_response=False)
        self.assertTrue(self.client.session.session_key)
        call = start.call_args.kwargs
        self.assertEqual(call['user_id'], self.user.pk)
        self.assertEqual(call['credential_id'], row.pk)
        self.assertEqual(call['session_key'], self.client.session.session_key)
        self.assertEqual(
            call['redirect_uri'],
            f'http://testserver{reverse("settings_keys_oauth_google_callback")}',
        )
        self.assertNotIn('app-secret-sentinel', response.headers['Location'])

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Not Found: /settings/keys/oauth/.+', count=2)])
    def test_oauth_mutation_routes_enforce_ownership(self) -> None:
        """Hide credentials owned by another authenticated user."""
        User = get_user_model()
        other = User.objects.create_user(username='other-keys-user', password='test')
        commands.create_user_oauth(
            other.pk,
            'other-google',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = other.credentials.get(name='other-google')
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk})).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk})).status_code,
            404,
        )

    def test_disconnect_clears_grant_and_redirects(self) -> None:
        """Disconnect an owned active declaration without deleting it."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')
        row.encrypted_value = crypto.encrypt('refresh-token-sentinel')
        row.save(update_fields=['encrypted_value'])

        response = self.client.post(reverse('settings_keys_oauth_disconnect', kwargs={'credential_id': row.pk}))

        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        row.refresh_from_db()
        self.assertEqual(bytes(row.encrypted_value), b'')

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Method Not Allowed \(POST\): /settings/keys/oauth/google/callback/',
                count=1,
            )
        ]
    )
    def test_callback_requires_login_and_get(self) -> None:
        """Keep the provider callback on the authenticated existing session."""
        callback_url = reverse('settings_keys_oauth_google_callback')
        anonymous = self.client.get(
            callback_url,
            {'state': 'state-sentinel', 'code': 'code-sentinel'},
        )
        self.assertEqual(anonymous.status_code, 302)
        self._assert_callback_hardening(anonymous)
        self.assertNotIn('state-sentinel', anonymous.headers['Location'])
        self.assertNotIn('code-sentinel', anonymous.headers['Location'])
        self.client.force_login(self.user)
        method_rejected = self.client.post(callback_url)
        self.assertEqual(method_rejected.status_code, 405)
        self._assert_callback_hardening(method_rejected)

    def test_callback_completes_with_fixed_parameters_and_redirect(self) -> None:
        """Pass only callback state/code and the fixed URL into the lifecycle service."""
        self.client.force_login(self.user)
        session = self.client.session
        session['oauth-test'] = True
        session.save()
        callback_url = reverse('settings_keys_oauth_google_callback')

        with patch('apps.web.views.oauth_services.complete_authorization') as complete:
            response = self.client.get(
                callback_url,
                {'state': 'safe-state', 'code': 'code-sentinel', 'next': 'https://evil.test/'},
            )

        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        self._assert_callback_hardening(response)
        complete.assert_called_once_with(
            user_id=self.user.pk,
            session_key=self.client.session.session_key,
            state='safe-state',
            code='code-sentinel',
            redirect_uri=f'http://testserver{callback_url}',
        )
        self.assertNotIn('code-sentinel', response.headers['Location'])
        self.assertNotIn('evil.test', response.headers['Location'])

    def test_callback_denial_and_failures_use_fixed_safe_messages(self) -> None:
        """Consume denial state and sanitize state/provider failures."""
        self.client.force_login(self.user)
        callback_url = reverse('settings_keys_oauth_google_callback')
        sentinels = ('code-sentinel', 'refresh-token-sentinel', 'client-secret-sentinel')

        cases = (
            (
                {'state': 'safe-state', 'error': 'access_denied'},
                None,
                None,
                'Google authorization was denied.',
            ),
            (
                {'state': 'safe-state', 'code': 'code-sentinel', 'error': 'access_denied'},
                None,
                None,
                'Google authorization was denied.',
            ),
            (
                {'state': 'safe-state'},
                OAuthStateError('safe callback failure'),
                '',
                'Google authorization could not be completed.',
            ),
            (
                {'state': 'safe-state', 'error': ''},
                OAuthStateError('safe callback failure'),
                '',
                'Google authorization could not be completed.',
            ),
            (
                {'state': 'state-sentinel', 'code': 'code-sentinel'},
                OAuthStateError('state-sentinel'),
                'code-sentinel',
                'Google authorization could not be completed.',
            ),
            (
                {'state': 'safe-state', 'code': 'code-sentinel'},
                OAuthProviderError('refresh-token-sentinel'),
                'code-sentinel',
                'Google authorization could not be completed.',
            ),
        )
        for query, side_effect, expected_code, expected_message in cases:
            with self.subTest(query=query):
                client = Client()
                client.force_login(self.user)
                with patch(
                    'apps.web.views.oauth_services.complete_authorization',
                    side_effect=side_effect,
                ) as complete:
                    response = client.get(callback_url, query)
                self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
                self._assert_callback_hardening(response)
                self.assertEqual(complete.call_args.kwargs['code'], expected_code)
                rendered = client.get(response.headers['Location']).content.decode()
                self.assertEqual(rendered.count(expected_message), 1)
                combined = rendered + response.headers['Location']
                for sentinel in sentinels:
                    self.assertNotIn(sentinel, combined)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Not Found: /settings/keys/oauth/google/callback/',
                count=1,
            )
        ]
    )
    def test_callback_converted_not_found_keeps_hardening_headers(self) -> None:
        """Apply callback headers after Django converts a route miss to a response."""
        callback_url = reverse('settings_keys_oauth_google_callback')
        client = Client(raise_request_exception=False)
        client.force_login(self.user)

        with patch('apps.web.views.oauth_services.complete_authorization', side_effect=Http404('safe')):
            response = client.get(
                callback_url,
                {'state': 'safe-state', 'code': 'safe-code'},
                HTTP_X_FORWARDED_PROTO='https',
                SERVER_PORT='443',
            )

        self.assertEqual(response.status_code, 404)
        self._assert_callback_hardening(response)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.ERROR,
                r'Internal Server Error: /settings/keys/oauth/google/callback/',
                count=1,
            )
        ]
    )
    def test_callback_converted_server_failure_keeps_hardening_headers(self) -> None:
        """Apply callback headers after Django converts an unhandled route failure."""
        callback_url = reverse('settings_keys_oauth_google_callback')
        client = Client(raise_request_exception=False)
        client.force_login(self.user)

        with patch('apps.web.views.oauth_services.complete_authorization', side_effect=RuntimeError('safe')):
            response = client.get(
                callback_url,
                {'state': 'safe-state', 'code': 'safe-code'},
                HTTP_X_FORWARDED_PROTO='https',
                SERVER_PORT='443',
            )

        self.assertEqual(response.status_code, 500)
        self._assert_callback_hardening(response)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.ERROR,
                r'Internal Server Error: /settings/keys/oauth/google/callback/',
                count=1,
            )
        ]
    )
    def test_callback_outer_middleware_hardens_downstream_response_failure(self) -> None:
        """Harden a 500 converted from downstream response processing."""
        middleware_path = 'apps.web.middleware.OAuthCallbackResponseMiddleware'
        self.assertEqual(settings.MIDDLEWARE[0], middleware_path)
        middleware = list(settings.MIDDLEWARE)
        middleware.insert(1, 'apps.web.tests.test_keys_page._RaiseAfterResponseMiddleware')
        callback_url = reverse('settings_keys_oauth_google_callback')

        with override_settings(MIDDLEWARE=middleware):
            response = Client(raise_request_exception=False).get(callback_url)

        self.assertEqual(response.status_code, 500)
        self._assert_callback_hardening(response)

    def test_non_callback_response_has_no_callback_hardening_headers(self) -> None:
        """Leave ordinary responses outside the callback path unchanged."""
        response = self.client.get(reverse('settings_keys'))

        self.assertNotEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
        self.assertNotEqual(response.headers.get('Cache-Control'), 'no-store')

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Bad Request: /settings/keys/oauth/.+/authorize/',
                count=1,
            )
        ]
    )
    def test_start_failure_scrubs_every_secret_surface(self) -> None:
        """Exclude distinct credential sentinels from start output, logs, and retained tracebacks."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')
        failure = OAuthProviderError(' '.join(SECRET_SENTINELS))
        handler = _RecordingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with patch('apps.web.views.oauth_services.start_authorization', side_effect=failure):
                response = self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk}))
        finally:
            root_logger.removeHandler(handler)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Location', response.headers)
        self.assertIsNone(failure.__cause__)
        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__traceback__)
        surfaces = {
            'html': response.content.decode(),
            'logs': '\n'.join(handler.rendered),
            'retained failure state': _retained_failure_text(failure),
        }
        for surface_name, surface in surfaces.items():
            for sentinel in SECRET_SENTINELS:
                self.assertNotIn(sentinel, surface, msg=f'{sentinel!r} leaked through {surface_name}')

    def test_callback_failure_scrubs_every_secret_surface(self) -> None:
        """Exclude callback and credential sentinels from redirects, messages, logs, and traceback state."""
        self.client.force_login(self.user)
        callback_url = reverse('settings_keys_oauth_google_callback')
        failure = OAuthProviderError(' '.join(SECRET_SENTINELS))
        handler = _RecordingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with patch('apps.web.views.oauth_services.complete_authorization', side_effect=failure):
                response = self.client.get(
                    callback_url,
                    {
                        'state': 'safe-state',
                        'code': AUTHORIZATION_CODE_SENTINEL,
                        'provider_body': PROVIDER_BODY_SENTINEL,
                    },
                )
        finally:
            root_logger.removeHandler(handler)

        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        self._assert_callback_hardening(response)
        self.assertIsNone(failure.__cause__)
        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__traceback__)
        rendered = self.client.get(response.headers['Location']).content.decode()
        self.assertEqual(rendered.count('Google authorization could not be completed.'), 1)
        surfaces = {
            'html and messages': rendered,
            'redirect URL': response.headers['Location'],
            'logs': '\n'.join(handler.rendered),
            'retained failure state': _retained_failure_text(failure),
        }
        for surface_name, surface in surfaces.items():
            for sentinel in SECRET_SENTINELS:
                self.assertNotIn(sentinel, surface, msg=f'{sentinel!r} leaked through {surface_name}')

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Bad Request: /settings/keys/oauth/.+/authorize/',
                count=1,
            )
        ]
    )
    def test_oauth_routes_reject_non_https_callback_url(self) -> None:
        """Refuse production OAuth flow handling when the fixed callback is not HTTPS."""
        self.client.force_login(self.user)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = self.user.credentials.get(name='google-oauth')

        with patch('apps.web.views.oauth_services.start_authorization') as start:
            response = self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk}))
        self.assertEqual(response.status_code, 400)
        start.assert_not_called()

        with patch('apps.web.views.oauth_services.complete_authorization') as complete:
            response = self.client.get(
                reverse('settings_keys_oauth_google_callback'),
                {'state': 'safe-state', 'code': 'safe-code'},
            )
        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        self._assert_callback_hardening(response)
        complete.assert_not_called()

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_proxied_https_callback_uses_trusted_forwarded_scheme(self) -> None:
        """Accept the fixed HTTPS callback when the trusted front proxy marks the request secure."""
        self.client.force_login(self.user)
        callback_url = reverse('settings_keys_oauth_google_callback')

        with patch('apps.web.views.oauth_services.complete_authorization') as complete:
            response = self.client.get(
                callback_url,
                {'state': 'safe-state', 'code': 'safe-code'},
                HTTP_X_FORWARDED_PROTO='https',
                SERVER_PORT='443',
            )

        self.assertEqual(
            getattr(settings, 'SECURE_PROXY_SSL_HEADER', None),
            ('HTTP_X_FORWARDED_PROTO', 'https'),
        )
        self.assertRedirects(response, reverse('settings_keys'), fetch_redirect_response=False)
        self._assert_callback_hardening(response)
        complete.assert_called_once_with(
            user_id=self.user.pk,
            session_key=self.client.session.session_key,
            state='safe-state',
            code='safe-code',
            redirect_uri=f'https://testserver{callback_url}',
        )

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID='public-client-id',
        GOOGLE_OAUTH_CLIENT_SECRET=CLIENT_SECRET_SENTINEL,
    )
    def test_page_and_provider_redirect_omit_secret_material(self) -> None:
        """Keep credentials and deployment secrets out of HTML and redirect queries."""
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'google-static', 'google', SERVICE_ACCOUNT_SENTINEL)
        commands.create_user_oauth(
            self.user.pk,
            'google-oauth',
            'google',
            provider_id='google',
            capability_ids=['gmail_read'],
        )
        row = get_owned_user_credential(self.user.pk, self.user.credentials.get(name='google-oauth').pk)

        page = self.client.get(reverse('settings_keys'))
        for sentinel in SECRET_SENTINELS:
            self.assertNotContains(page, sentinel)

        response = self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk}))
        location = response.headers['Location']
        query = parse_qs(urlparse(location).query)
        self.assertNotIn('client_secret', query)
        for sentinel in SECRET_SENTINELS:
            self.assertNotIn(sentinel, location)

    @expectLogItems([ExpectLogItem('django.request', logging.WARNING, r'Bad Request: /settings/keys/named/', count=1)])
    def test_post_add_cannot_replace_disk_key(self) -> None:
        self.client.force_login(self.user)
        _, changed = commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )
        self.assertTrue(changed)

        response = self.client.post(
            reverse('settings_keys_add_named'),
            {'name': 'disk-openai', 'type': 'openai', 'secret': 'sk-ui'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'disk-sourced credential is read-only', response.content)
        row = self.user.credentials.get(name='disk-openai')
        self.assertEqual(row.source, 'disk')
        self.assertEqual(row.source_path, 'keys/disk-openai.yaml')

    @expectLogItems(
        [
            ExpectLogItem(
                'django.request',
                logging.WARNING,
                r'Bad Request: /settings/keys/named/disk-openai/delete/',
                count=1,
            )
        ]
    )
    def test_post_delete_cannot_remove_disk_key(self) -> None:
        self.client.force_login(self.user)
        _, changed = commands.upsert_user_named_from_disk(
            self.user.pk,
            'disk-openai',
            'openai',
            'sk-disk',
            source_path='keys/disk-openai.yaml',
            source_rev='sha256:disk',
        )
        self.assertTrue(changed)

        response = self.client.post(
            reverse('settings_keys_delete_named', kwargs={'name': 'disk-openai'}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'disk-sourced credential is read-only', response.content)
        self.assertTrue(self.user.credentials.filter(name='disk-openai').exists())

    @expectLogItems(
        [ExpectLogItem('django.request', logging.WARNING, r'Not Found: /settings/keys/named/openai-work/', count=1)]
    )
    def test_update_endpoint_removed(self) -> None:
        self.client.force_login(self.user)
        commands.upsert_user_named(self.user.pk, 'openai-work', 'openai', 'sk-old')
        response = self.client.post('/settings/keys/named/openai-work/', {'secret': 'sk-new'})
        self.assertEqual(response.status_code, 404)

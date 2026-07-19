# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Contract and secret-hygiene tests for the Dropbox OAuth provider."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from apps.keys.exceptions import (
    KeyValidationError,
    OAuthConfigurationError,
    OAuthGrantError,
    OAuthProviderError,
)
from apps.keys.oauth.providers.dropbox import (
    AUTHORIZATION_ENDPOINT,
    DROPBOX_CAPABILITIES,
    DROPBOX_OAUTH_PROVIDER,
    TOKEN_ENDPOINT,
)
from django.test import override_settings
from libs.clients.dropbox import client as dropbox_client
from libs.dropbox_scopes import DROPBOX_OAUTH_SCOPE_VALUES, DROPBOX_OAUTH_SCOPES

from olib.py.django.test.cases import OTestCase

CATALOG_ROWS = (
    (
        'files_metadata',
        'Read Dropbox metadata',
        'list/search file and folder names and metadata without downloading content.',
        'files.metadata.read',
        'current',
    ),
)

APP_KEY = 'app-key-secret-sentinel'
APP_SECRET = 'app-secret-secret-sentinel'
REFRESH_TOKEN = 'refresh-token-secret-sentinel'
ACCESS_TOKEN = 'access-token-secret-sentinel'
PROVIDER_BODY = 'provider-body-secret-sentinel'
AUTHORIZATION_CODE = 'authorization-code-secret-sentinel'
FailureT = TypeVar('FailureT', bound=Exception)


def _retained_text(value: Any, *, seen: set[int] | None = None, depth: int = 0) -> str:
    """Render reachable failure state deeply enough to detect retained secret sentinels."""
    if seen is None:
        seen = set()
    if depth > 8 or id(value) in seen:
        return ''
    seen.add(id(value))

    rendered: list[str] = []
    for renderer in (str, repr):
        try:
            rendered.append(renderer(value))
        except Exception:  # pragma: no cover  # pylint: disable=broad-exception-caught
            rendered.append('<unrenderable>')

    if isinstance(value, BaseException):
        rendered.extend(_retained_text(item, seen=seen, depth=depth + 1) for item in value.args)
        if value.__context__ is not None:
            rendered.append(_retained_text(value.__context__, seen=seen, depth=depth + 1))
        if value.__cause__ is not None:
            rendered.append(_retained_text(value.__cause__, seen=seen, depth=depth + 1))
        traceback = value.__traceback__
        while traceback is not None:
            if traceback.tb_frame.f_globals.get('__name__') == 'apps.keys.oauth.providers.dropbox':
                rendered.append(_retained_text(traceback.tb_frame.f_locals, seen=seen, depth=depth + 1))
            traceback = traceback.tb_next
    elif isinstance(value, Mapping):
        for key, item in value.items():
            rendered.append(_retained_text(key, seen=seen, depth=depth + 1))
            rendered.append(_retained_text(item, seen=seen, depth=depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        rendered.extend(_retained_text(item, seen=seen, depth=depth + 1) for item in value)
    elif hasattr(value, '__dict__'):
        rendered.append(_retained_text(vars(value), seen=seen, depth=depth + 1))
    return '\n'.join(rendered)


def _capture_failure(action: Callable[[], Any], expected_type: type[FailureT]) -> FailureT:
    """Run an action and preserve the expected failure's traceback for inspection."""
    try:
        action()
    except Exception as failure:
        if isinstance(failure, expected_type):
            return failure
        raise
    raise AssertionError('Expected provider operation to fail')


def _token_response(payload: object) -> Mock:
    """Build a successful response stub with a controlled JSON payload."""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _grant(*capability_ids: str) -> str:
    """Build a valid stored grant for the requested capability IDs."""
    scope_by_id = {capability.id: capability.scope for capability in DROPBOX_CAPABILITIES}
    return json.dumps(
        {
            'version': 1,
            'refresh_token': REFRESH_TOKEN,
            'granted_scopes': [scope_by_id[capability_id] for capability_id in capability_ids],
        }
    )


@override_settings(DROPBOX_OAUTH_APP_KEY=APP_KEY, DROPBOX_OAUTH_APP_SECRET=APP_SECRET)
class TestDropboxOAuthProvider(OTestCase):
    def _assert_failure_scrubbed(self, failure: BaseException, sentinels: tuple[str, ...]) -> None:
        """Assert provider frames and chained failures retain no supplied secret marker."""
        self.assertIsNone(failure.__context__)
        self.assertIsNone(failure.__cause__)
        retained = _retained_text(failure)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, retained)

    def test_provider_and_runtime_share_one_django_free_scope_catalog(self) -> None:
        """Derive provider capabilities and runtime validation from one libs constant set."""
        self.assertIs(dropbox_client.DROPBOX_OAUTH_SCOPES, DROPBOX_OAUTH_SCOPES)
        self.assertEqual(
            tuple(capability.scope for capability in DROPBOX_CAPABILITIES),
            DROPBOX_OAUTH_SCOPE_VALUES,
        )

    def test_catalog_has_the_exact_complete_rows(self) -> None:
        rows = tuple(
            (
                capability.id,
                capability.label,
                capability.description,
                capability.scope,
                capability.support,
            )
            for capability in DROPBOX_CAPABILITIES
        )

        self.assertEqual(rows, CATALOG_ROWS)

    def test_normalization_returns_catalog_order(self) -> None:
        normalized = DROPBOX_OAUTH_PROVIDER.normalize_capabilities(('files_metadata',))

        self.assertEqual(normalized, ('files_metadata',))

    def test_normalization_rejects_empty_selection(self) -> None:
        with self.assertRaisesRegex(KeyValidationError, r'^At least one Dropbox OAuth capability is required$'):
            DROPBOX_OAUTH_PROVIDER.normalize_capabilities(())

    def test_normalization_rejects_unknown_id_without_echoing_it(self) -> None:
        secret_id = 'unknown-capability-secret-sentinel'

        with self.assertRaises(KeyValidationError) as caught:
            DROPBOX_OAUTH_PROVIDER.normalize_capabilities((secret_id,))

        self.assertNotIn(secret_id, str(caught.exception))
        self.assertNotIn(secret_id, repr(caught.exception))

    def test_normalization_rejects_raw_scope_string(self) -> None:
        with self.assertRaises(KeyValidationError):
            DROPBOX_OAUTH_PROVIDER.normalize_capabilities(('files.metadata.read',))

    def test_normalization_rejects_blank_and_non_string_ids(self) -> None:
        for invalid in ('', '   ', None, 1):
            with self.subTest(invalid=invalid), self.assertRaises(KeyValidationError):
                DROPBOX_OAUTH_PROVIDER.normalize_capabilities((invalid,))  # type: ignore[arg-type]

    def test_authorization_url_uses_fixed_endpoint_and_exact_parameters(self) -> None:
        authorization_url = DROPBOX_OAUTH_PROVIDER.build_authorization_url(
            redirect_uri='https://chief.example.test/oauth/dropbox/callback',
            state='signed-state',
            capability_ids=('files_metadata',),
        )
        parsed = urlparse(authorization_url)
        query = parse_qs(parsed.query)

        self.assertEqual(f'{parsed.scheme}://{parsed.netloc}{parsed.path}', AUTHORIZATION_ENDPOINT)
        self.assertEqual(
            query,
            {
                'client_id': [APP_KEY],
                'redirect_uri': ['https://chief.example.test/oauth/dropbox/callback'],
                'response_type': ['code'],
                'scope': ['files.metadata.read'],
                'state': ['signed-state'],
                'token_access_type': ['offline'],
            },
        )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_exchange_posts_code_with_bounded_timeout_and_stores_minimal_grant(self, post: Mock) -> None:
        post.return_value = _token_response(
            {
                'access_token': ACCESS_TOKEN,
                'expires_in': 14400,
                'refresh_token': REFRESH_TOKEN,
                'scope': 'files.metadata.read',
                'token_type': 'bearer',
                'provider_extra': PROVIDER_BODY,
            }
        )

        raw_grant = DROPBOX_OAUTH_PROVIDER.exchange_code(
            code=AUTHORIZATION_CODE,
            redirect_uri='https://chief.example.test/oauth/dropbox/callback',
            capability_ids=('files_metadata',),
        )
        grant = json.loads(raw_grant)

        post.assert_called_once()
        (endpoint,) = post.call_args.args
        self.assertEqual(endpoint, TOKEN_ENDPOINT)
        self.assertEqual(
            post.call_args.kwargs['data'],
            {
                'client_id': APP_KEY,
                'client_secret': APP_SECRET,
                'code': AUTHORIZATION_CODE,
                'grant_type': 'authorization_code',
                'redirect_uri': 'https://chief.example.test/oauth/dropbox/callback',
            },
        )
        timeout = post.call_args.kwargs['timeout']
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 30)
        self.assertEqual(
            grant,
            {
                'version': 1,
                'refresh_token': REFRESH_TOKEN,
                'granted_scopes': ['files.metadata.read'],
            },
        )
        self.assertNotIn(ACCESS_TOKEN, raw_grant)
        self.assertNotIn(PROVIDER_BODY, raw_grant)

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_exchange_rejects_missing_refresh_token_safely(self, post: Mock) -> None:
        post.return_value = _token_response(
            {
                'access_token': ACCESS_TOKEN,
                'scope': 'files.metadata.read',
                'provider_extra': PROVIDER_BODY,
            }
        )

        with self.assertNoLogs('apps.keys.oauth.providers.dropbox', level='DEBUG'):
            with self.assertRaises(OAuthProviderError) as caught:
                DROPBOX_OAUTH_PROVIDER.exchange_code(
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                    capability_ids=('files_metadata',),
                )

        rendered = f'{caught.exception!s} {caught.exception!r}'
        for secret in (ACCESS_TOKEN, PROVIDER_BODY, APP_SECRET, AUTHORIZATION_CODE):
            self.assertNotIn(secret, rendered)

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_exchange_rejects_incomplete_scope_grant_safely(self, post: Mock) -> None:
        post.return_value = _token_response(
            {
                'refresh_token': REFRESH_TOKEN,
                'scope': '',
            }
        )

        with self.assertRaises(OAuthProviderError) as caught:
            DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            )

        self.assertNotIn(REFRESH_TOKEN, str(caught.exception))
        self.assertNotIn(REFRESH_TOKEN, repr(caught.exception))

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_exchange_sanitizes_transport_failure(self, post: Mock) -> None:
        post.side_effect = httpx.ConnectError(PROVIDER_BODY)

        with self.assertRaisesRegex(OAuthProviderError, r'^Dropbox OAuth code exchange failed$') as caught:
            DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            )

        self.assertNotIn(PROVIDER_BODY, repr(caught.exception))

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_exchange_sanitizes_http_status_and_provider_payload_failures(self, post: Mock) -> None:
        request = httpx.Request('POST', TOKEN_ENDPOINT)
        response = httpx.Response(400, request=request, text=PROVIDER_BODY)
        post.return_value = response

        with self.assertRaisesRegex(OAuthProviderError, r'^Dropbox OAuth code exchange failed$') as caught:
            DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            )

        self.assertNotIn(PROVIDER_BODY, repr(caught.exception))

        post.return_value = _token_response({'error': PROVIDER_BODY})
        with self.assertRaisesRegex(OAuthProviderError, r'^Dropbox OAuth code exchange failed$') as caught:
            DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            )
        self.assertNotIn(PROVIDER_BODY, repr(caught.exception))

    def test_materialization_returns_exact_runtime_envelope(self) -> None:
        runtime_payload = DROPBOX_OAUTH_PROVIDER.materialize_runtime(
            grant_payload=_grant('files_metadata'),
            capability_ids=('files_metadata',),
        )

        self.assertEqual(
            json.loads(runtime_payload),
            {
                'chief_dropbox_oauth': 1,
                'app_key': APP_KEY,
                'app_secret': APP_SECRET,
                'refresh_token': REFRESH_TOKEN,
                'scopes': ['files.metadata.read'],
            },
        )

    def test_materialization_rejects_malformed_or_mismatched_grants_safely(self) -> None:
        malformed_grants = (
            'not-json-' + REFRESH_TOKEN,
            json.dumps({'version': True, 'refresh_token': REFRESH_TOKEN, 'granted_scopes': []}),
            json.dumps({'version': 2, 'refresh_token': REFRESH_TOKEN, 'granted_scopes': []}),
            json.dumps({'version': 1, 'refresh_token': '', 'granted_scopes': []}),
            json.dumps({'version': 1, 'refresh_token': REFRESH_TOKEN, 'granted_scopes': []}),
            json.dumps(
                {
                    'version': 1,
                    'refresh_token': REFRESH_TOKEN,
                    'granted_scopes': ['files.metadata.read'],
                    'access_token': ACCESS_TOKEN,
                }
            ),
        )

        for grant in malformed_grants:
            with self.subTest(grant=grant[:20]), self.assertRaises(OAuthGrantError) as caught:
                DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                    grant_payload=grant,
                    capability_ids=('files_metadata',),
                )
            rendered = f'{caught.exception!s} {caught.exception!r}'
            for secret in (REFRESH_TOKEN, ACCESS_TOKEN, APP_SECRET):
                self.assertNotIn(secret, rendered)

    @override_settings(DROPBOX_OAUTH_APP_KEY='', DROPBOX_OAUTH_APP_SECRET='')
    def test_app_credentials_are_loaded_lazily_for_each_operation(self) -> None:
        with self.assertRaisesRegex(OAuthConfigurationError, r'^Dropbox OAuth is not configured$'):
            DROPBOX_OAUTH_PROVIDER.build_authorization_url(
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                state='signed-state',
                capability_ids=('files_metadata',),
            )
        with patch('apps.keys.oauth.providers.dropbox.httpx.post') as post:
            with self.assertRaisesRegex(OAuthConfigurationError, r'^Dropbox OAuth is not configured$'):
                DROPBOX_OAUTH_PROVIDER.exchange_code(
                    code=AUTHORIZATION_CODE,
                    redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                    capability_ids=('files_metadata',),
                )
            post.assert_not_called()
        with self.assertRaisesRegex(OAuthConfigurationError, r'^Dropbox OAuth is not configured$'):
            DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                grant_payload=_grant('files_metadata'),
                capability_ids=('files_metadata',),
            )

    def test_provider_repr_does_not_retain_app_credentials(self) -> None:
        rendered = repr(DROPBOX_OAUTH_PROVIDER)

        self.assertNotIn(APP_KEY, rendered)
        self.assertNotIn(APP_SECRET, rendered)

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_transport_failure_scrubs_traceback_and_chain(self, post: Mock) -> None:
        request_content = 'transport-request-content-secret-sentinel'
        provider_detail = 'transport-provider-detail-secret-sentinel'
        request = httpx.Request('POST', TOKEN_ENDPOINT, content=request_content)
        post.side_effect = httpx.ConnectError(provider_detail, request=request)

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthProviderError,
        )

        self._assert_failure_scrubbed(
            failure,
            (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, request_content, provider_detail),
        )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_http_status_failure_scrubs_response_and_request(self, post: Mock) -> None:
        request_content = 'status-request-content-secret-sentinel'
        response_content = 'status-response-content-secret-sentinel'
        request = httpx.Request('POST', TOKEN_ENDPOINT, content=request_content)
        post.return_value = httpx.Response(400, request=request, text=response_content)

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthProviderError,
        )

        self._assert_failure_scrubbed(
            failure,
            (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, request_content, response_content),
        )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_malformed_json_failure_scrubs_parser_state(self, post: Mock) -> None:
        parser_detail = 'malformed-json-provider-detail-secret-sentinel'
        response = _token_response({})
        response.json.side_effect = ValueError(parser_detail)
        post.return_value = response

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthProviderError,
        )

        self._assert_failure_scrubbed(
            failure,
            (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, parser_detail),
        )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_provider_failure_payload_scrubs_tokens(self, post: Mock) -> None:
        provider_detail = 'provider-failure-detail-secret-sentinel'
        post.return_value = _token_response(
            {
                'error': provider_detail,
                'access_token': ACCESS_TOKEN,
                'refresh_token': REFRESH_TOKEN,
            }
        )

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthProviderError,
        )

        self._assert_failure_scrubbed(
            failure,
            (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, provider_detail, ACCESS_TOKEN, REFRESH_TOKEN),
        )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_missing_and_incomplete_scope_failures_scrub_grant_state(self, post: Mock) -> None:
        scope_cases = (
            ('missing-scope', None),
            ('empty-scope', ''),
        )
        for label, scope_value in scope_cases:
            post.return_value = _token_response(
                {
                    'access_token': ACCESS_TOKEN,
                    'refresh_token': REFRESH_TOKEN,
                    'scope': scope_value,
                }
            )
            with self.subTest(case=label):
                failure = _capture_failure(
                    lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                        code=AUTHORIZATION_CODE,
                        redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                        capability_ids=('files_metadata',),
                    ),
                    OAuthProviderError,
                )
                self._assert_failure_scrubbed(
                    failure,
                    (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, ACCESS_TOKEN, REFRESH_TOKEN),
                )

    @patch('apps.keys.oauth.providers.dropbox.httpx.post')
    def test_missing_refresh_token_failure_scrubs_provider_payload(self, post: Mock) -> None:
        provider_detail = 'missing-refresh-provider-detail-secret-sentinel'
        post.return_value = _token_response(
            {
                'access_token': ACCESS_TOKEN,
                'scope': 'files.metadata.read',
                'provider_extra': provider_detail,
            }
        )

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=AUTHORIZATION_CODE,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthProviderError,
        )

        self._assert_failure_scrubbed(
            failure,
            (AUTHORIZATION_CODE, APP_KEY, APP_SECRET, ACCESS_TOKEN, provider_detail),
        )

    def test_malformed_stored_grant_scrubs_raw_payload_and_parser_failure(self) -> None:
        raw_detail = 'malformed-stored-grant-secret-sentinel'
        raw_grant = '{"refresh_token":"' + raw_detail

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                grant_payload=raw_grant,
                capability_ids=('files_metadata',),
            ),
            OAuthGrantError,
        )

        self._assert_failure_scrubbed(
            failure,
            (APP_KEY, APP_SECRET, raw_detail),
        )

    def test_materialization_validation_scrubs_raw_and_parsed_grant(self) -> None:
        parsed_detail = 'parsed-grant-provider-detail-secret-sentinel'
        raw_grant = json.dumps(
            {
                'version': 1,
                'refresh_token': REFRESH_TOKEN,
                'granted_scopes': [],
                'provider_extra': parsed_detail,
            }
        )

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                grant_payload=raw_grant,
                capability_ids=('files_metadata',),
            ),
            OAuthGrantError,
        )

        self._assert_failure_scrubbed(
            failure,
            (APP_KEY, APP_SECRET, REFRESH_TOKEN, parsed_detail),
        )

    def test_authorization_invalid_capability_scrubs_state_preflight(self) -> None:
        state = 'authorization-preflight-state-secret-sentinel'

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.build_authorization_url(
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                state=state,
                capability_ids=('invalid-capability',),
            ),
            KeyValidationError,
        )

        self._assert_failure_scrubbed(failure, (state,))

    def test_exchange_invalid_capability_scrubs_code_preflight(self) -> None:
        code = 'exchange-preflight-code-secret-sentinel'

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=code,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('invalid-capability',),
            ),
            KeyValidationError,
        )

        self._assert_failure_scrubbed(failure, (code,))

    def test_materialization_invalid_capability_scrubs_grant_preflight(self) -> None:
        grant_detail = 'materialization-preflight-grant-secret-sentinel'
        grant_payload = json.dumps(
            {
                'version': 1,
                'refresh_token': grant_detail,
                'granted_scopes': ['files.metadata.read'],
            }
        )

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                grant_payload=grant_payload,
                capability_ids=('invalid-capability',),
            ),
            KeyValidationError,
        )

        self._assert_failure_scrubbed(failure, (grant_detail,))

    @override_settings(
        DROPBOX_OAUTH_APP_KEY='',
        DROPBOX_OAUTH_APP_SECRET='authorization-partial-config-secret-sentinel',
    )
    def test_authorization_partial_configuration_scrubs_loader_and_state(self) -> None:
        state = 'authorization-partial-state-secret-sentinel'

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.build_authorization_url(
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                state=state,
                capability_ids=('files_metadata',),
            ),
            OAuthConfigurationError,
        )

        self._assert_failure_scrubbed(
            failure,
            ('authorization-partial-config-secret-sentinel', state),
        )

    @override_settings(
        DROPBOX_OAUTH_APP_KEY='',
        DROPBOX_OAUTH_APP_SECRET='exchange-partial-config-secret-sentinel',
    )
    def test_exchange_partial_configuration_scrubs_loader_and_code(self) -> None:
        code = 'exchange-partial-code-secret-sentinel'

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.exchange_code(
                code=code,
                redirect_uri='https://chief.example.test/oauth/dropbox/callback',
                capability_ids=('files_metadata',),
            ),
            OAuthConfigurationError,
        )

        self._assert_failure_scrubbed(
            failure,
            ('exchange-partial-config-secret-sentinel', code),
        )

    @override_settings(
        DROPBOX_OAUTH_APP_KEY='',
        DROPBOX_OAUTH_APP_SECRET='materialization-partial-config-secret-sentinel',
    )
    def test_materialization_partial_configuration_scrubs_loader_and_grant(self) -> None:
        grant_detail = 'materialization-partial-grant-secret-sentinel'
        grant_payload = json.dumps(
            {
                'version': 1,
                'refresh_token': grant_detail,
                'granted_scopes': ['files.metadata.read'],
            }
        )

        failure = _capture_failure(
            lambda: DROPBOX_OAUTH_PROVIDER.materialize_runtime(
                grant_payload=grant_payload,
                capability_ids=('files_metadata',),
            ),
            OAuthConfigurationError,
        )

        self._assert_failure_scrubbed(
            failure,
            ('materialization-partial-config-secret-sentinel', grant_detail),
        )

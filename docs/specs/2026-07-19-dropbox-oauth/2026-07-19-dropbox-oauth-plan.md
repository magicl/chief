# Dropbox OAuth Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: This plan is entered through `/ship`.
> Use `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development`
> task-by-task. Create
> `docs/specs/2026-07-19-dropbox-oauth/2026-07-19-dropbox-oauth-revision.md` from the
> review template before implementation, but do not read it during implementation.
> After all implementation tasks, `/ship` owns mandatory **S_final**: request code
> review, fix or explicitly reject every finding, re-verify, and open the PR without
> pausing at `/plan`, `/impl`, or `/finish` handoffs.

**Goal:** Let a user-owned `dropbox` credential contain either the existing static refresh
JSON or a Chief-managed Dropbox OAuth refresh grant selected through the allowlisted
`files_metadata` capability and usable by the existing Dropbox metadata tool.

**Architecture:** Register a Dropbox plugin in the existing `apps.keys.oauth` provider
framework. Generalize Keys HTTP views so callback URIs and provider ids are selected from
the credential declaration rather than hardcoding Google. Runtime resolution materializes
an operation-local envelope that the Django-free Dropbox client accepts alongside legacy
static JSON.

**Tech Stack:** Python 3.13, Django 5.2 signing/cache/messages, PostgreSQL, Fernet,
`httpx`, official Dropbox SDK, Jinja/Alpine, PyYAML, and olib Django test cases.

**Branch:** `feat/2026-07-19-dropbox-oauth`

---

## Conventions

- Run every command from the repository root with the consumer prefix:
  `./olib/scripts/orunr …`.
- Use scoped tests while iterating, then gate every PR-ready stage with
  `./olib/scripts/orunr py test-all`.
- Plan/design documents are the only changes committed on `main`. `/ship` creates an
  isolated worktree on `feat/2026-07-19-dropbox-oauth` before implementation.
- After each PR-ready implementation commit run
  `git fetch origin main && git rebase origin/main && git push -u origin HEAD`. Stop and
  ask the human if rebase reports conflicts.
- Every new or materially changed function/method gets a concise purpose docstring,
  including assumptions and non-obvious authorization or secret-lifetime behavior, per
  `AGENTS.md`.
- Tests use `OTestCase`, `OTransactionTestCase`, or `OLiveServerTestCase`, never bare
  `unittest.TestCase`.
- Test names avoid the parproc-highlighted words `exception`, `error`, `warning`, `notice`,
  `deprecated`, and `deprecation`.
- `apps.web` handles HTTP only and never imports decrypt/resolve functions or accesses ORM
  models directly. `apps.keys` owns OAuth persistence and operational secrets.
- `backend/libs/` stays Django-free. The Dropbox client receives one operation-local string
  and never imports `apps.*`.
- No compatibility re-export shims: update imports to canonical modules and delete replaced
  code rather than retaining pass-through modules.
- Never log or render authorization codes, provider response bodies, access/refresh tokens,
  app secrets, decrypted grants, or the runtime OAuth envelope.
- Existing static Dropbox credentials and Google OAuth retain current behavior.
- `/ship` owns final review/fix/PR handling. Do not use Cursor Bugbot.

## File map

### Create

- `backend/apps/keys/oauth/providers/dropbox.py` — Dropbox capability catalog, authorize/token
  endpoints, code exchange, grant serialization, runtime materialization.
- `backend/apps/keys/oauth/tests/test_dropbox.py` — provider contract and secret-hygiene tests.

### Modify

- `backend/apps/keys/oauth/registry.py` — register `DROPBOX_OAUTH_PROVIDER`.
- `backend/apps/keys/oauth/__init__.py` — export Dropbox provider if other providers are
  exported.
- `backend/apps/keys/oauth/tests/test_registry.py` — expect `google` and `dropbox` ids.
- `backend/chief/settings.py` — `DROPBOX_OAUTH_APP_KEY`, `DROPBOX_OAUTH_APP_SECRET`.
- `backend/chief/tests/test_compose_config.py` — Dropbox env/Knox/callback contract.
- `.env.local.example` — blank Dropbox OAuth placeholders under `#[backend]`.
- `backend/apps/web/views.py` — multi-provider catalog, provider→callback URI helper,
  provider_id from credential type on add, authorize/callback messages per provider,
  Dropbox callback view.
- `backend/apps/web/urls.py` — `settings/keys/oauth/dropbox/callback/`.
- `backend/apps/web/tests/test_keys_page.py` — Dropbox OAuth create/connect/callback and
  generalized Google regressions.
- `backend/templates/web/keys.html` — Static/OAuth choice for `dropbox` and Dropbox
  capability checkboxes (mirror Google, avoid duplicating secret-bearing markup).
- `backend/apps/keys/credential_guides.py` — Dropbox guide distinguishes static JSON vs
  in-app OAuth.
- `backend/apps/keys/tests/test_credential_guides.py`
- `backend/libs/providers/key/tests/test_disk_parse.py` — Dropbox OAuth YAML form.
- `backend/apps/keys/tests/test_disk_sync.py` — Dropbox OAuth grant preserve/clear.
- `backend/libs/clients/dropbox/client.py` — accept static JSON or
  `chief_dropbox_oauth` envelope.
- `backend/libs/clients/dropbox/tests/test_client.py` — dual-auth construction tests.
- `examples/local/keys/example-dropbox.yaml` — document both forms (keep static example;
  add commented OAuth example or sibling note in docs).
- `docs/docs/oauth-apps.md` — Chief callback for Dropbox; Knox/env wiring; deprecate
  external-only refresh flow as the primary path.
- `docs/docs/agents.md` — Dropbox OAuth credential form.
- `docs/ARCHITECTURE.md` — Dropbox OAuth application ownership beside Google.

---

### Task 1: Dropbox OAuth provider and deployment settings

**Files:**
- Create: `backend/apps/keys/oauth/providers/dropbox.py`
- Create: `backend/apps/keys/oauth/tests/test_dropbox.py`
- Modify: `backend/apps/keys/oauth/registry.py`
- Modify: `backend/apps/keys/oauth/tests/test_registry.py`
- Modify: `backend/chief/settings.py`
- Modify: `backend/chief/tests/test_compose_config.py`
- Modify: `.env.local.example`

- [ ] **Step 1: Write failing provider, registry, and settings tests**

In `test_dropbox.py`, assert the catalog is exactly:

```python
(
    (
        'files_metadata',
        'Read Dropbox metadata',
        'list/search file and folder names and metadata without downloading content.',
        'files.metadata.read',
        'current',
    ),
)
```

Assert `normalize_capabilities` rejects empty/unknown IDs and returns catalog order.
Stub `httpx.post` against `https://api.dropboxapi.com/oauth2/token`. Assert
`build_authorization_url` targets `https://www.dropbox.com/oauth2/authorize` with
`response_type=code`, `token_access_type=offline`, `scope=files.metadata.read`, and the
app key from settings. Assert `exchange_code` stores only:

```python
{'version': 1, 'refresh_token': '...', 'granted_scopes': ['files.metadata.read']}
```

Reject missing refresh token and incomplete scope grants. Assert
`materialize_runtime` returns compact JSON with exact keys:

```python
{
    'chief_dropbox_oauth': 1,
    'app_key': '...',
    'app_secret': '...',
    'refresh_token': '...',
    'scopes': ['files.metadata.read'],
}
```

Never leave app secrets/tokens in exception messages or retained locals after failure.
In `test_registry.py`, assert `OAUTH_PROVIDERS.provider_ids() == ('google', 'dropbox')`.
Extend compose-config tests for blank/default Dropbox settings, `.env.local.example`
placeholders, and Architecture Knox mapping `$KNOX/chief/oauth/dropbox` with
`app_key` / `app_secret`.

- [ ] **Step 2: Verify the tests fail**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests.test_dropbox apps.keys.oauth.tests.test_registry chief.tests.test_compose_config
```

Expected: import/attribute failures because the Dropbox provider and settings do not exist.

- [ ] **Step 3: Implement provider and settings**

Mirror `providers/google.py` structure:

- Load `settings.DROPBOX_OAUTH_APP_KEY` / `DROPBOX_OAUTH_APP_SECRET` lazily; raise
  `OAuthConfigurationError('Dropbox OAuth is not configured')` when blank.
- Clear secret-bearing locals in `finally` blocks like Google.
- Register `DROPBOX_OAUTH_PROVIDER` in `registry.py` after Google.
- Add settings defaults `''` and example env lines under `#[backend]`.

- [ ] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests chief.tests.test_compose_config
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/keys/oauth/providers/dropbox.py backend/apps/keys/oauth/tests/test_dropbox.py backend/apps/keys/oauth/registry.py backend/apps/keys/oauth/tests/test_registry.py backend/chief/settings.py backend/chief/tests/test_compose_config.py .env.local.example
git commit -m "feat: register Dropbox OAuth provider"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 2: Multi-provider Keys HTTP surface and Dropbox callback

**Files:**
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/apps/web/tests/test_keys_page.py`
- Modify: `backend/templates/web/keys.html`
- Modify: `backend/apps/keys/credential_guides.py`
- Modify: `backend/apps/keys/tests/test_credential_guides.py`

- [ ] **Step 1: Write failing web/UI tests**

Add assertions equivalent to:

```python
# Add Dropbox OAuth declaration
response = self.client.post(
    reverse('settings_keys_add_named'),
    {
        'name': 'team-dropbox',
        'type': 'dropbox',
        'auth_kind': 'oauth',
        'capabilities': ['files_metadata'],
    },
)
row = self.user.credentials.get(name='team-dropbox')
self.assertEqual(row.auth_kind, 'oauth')
self.assertEqual(row.auth_config['provider'], 'dropbox')
self.assertEqual(row.auth_config['capabilities'], ['files_metadata'])
self.assertEqual(bytes(row.encrypted_value), b'')

# Keys page exposes Dropbox OAuth controls and capabilities
response = self.client.get(reverse('settings_keys'))
self.assertContains(response, 'files_metadata')
self.assertContains(response, 'Read Dropbox metadata')

# Authorize uses Dropbox callback URI
with override_settings(
    DROPBOX_OAUTH_APP_KEY='app-key',
    DROPBOX_OAUTH_APP_SECRET='app-secret-sentinel',
), patch('apps.web.views.oauth_services.start_authorization') as start:
    start.return_value = OAuthStart(authorization_url='https://www.dropbox.com/oauth2/authorize?x=1', state='s')
    self.client.post(reverse('settings_keys_oauth_authorize', kwargs={'credential_id': row.pk}))
self.assertEqual(
    start.call_args.kwargs['redirect_uri'],
    f'http://testserver{reverse("settings_keys_oauth_dropbox_callback")}',
)

# Google authorize still uses Google callback (regression)
```

Also assert Dropbox callback completes via `complete_authorization` with the Dropbox
redirect URI, hardens headers, and never echoes codes/secrets. Assert authorize for a
Dropbox row no longer returns the Google-only failure string when configuration is missing
— use a provider-neutral or Dropbox-specific fixed message that does not leak secrets.

Update credential-guide tests so the Dropbox static guide no longer claims Chief never runs
OAuth; OAuth mode should point users at Keys Authenticate after declaration.

- [ ] **Step 2: Verify the tests fail**

```bash
./olib/scripts/orunr py test apps.web.tests.test_keys_page apps.keys.tests.test_credential_guides
```

Expected: failures for missing Dropbox callback route and hardcoded Google provider/callback.

- [ ] **Step 3: Implement multi-provider web wiring**

Concrete changes:

1. `_oauth_catalog_for_ui()` iterates `OAUTH_PROVIDERS.provider_ids()` and builds the
   capability dict for every registered provider.
2. Replace `_fixed_google_callback_uri` with:

```python
_CALLBACK_ROUTE_NAMES = {
    'google': 'settings_keys_oauth_google_callback',
    'dropbox': 'settings_keys_oauth_dropbox_callback',
}

def _fixed_oauth_callback_uri(request: HttpRequest, provider_id: str) -> str:
    """Build the provider-fixed callback URI; require HTTPS outside local development."""
    route_name = _CALLBACK_ROUTE_NAMES.get(provider_id)
    if route_name is None:
        raise OAuthConfigurationError('OAuth callback is unavailable')
    callback_uri = request.build_absolute_uri(reverse(route_name))
    if not settings.DEBUG and not callback_uri.startswith('https://'):
        raise OAuthConfigurationError('OAuth callback is unavailable')
    return callback_uri
```

3. `settings_keys_add_named`: map credential type to provider via registry
   (`provider.credential_type == type_name`); do not hardcode `'google'`.
4. `settings_keys_oauth_authorize`: load owned active OAuth row provider (via
   `oauth_services` helper or `queries.get_owned_user_credential` + validated metadata),
   pass `_fixed_oauth_callback_uri(request, provider_id)`. Keep HTTP layer free of decrypt.
5. Add `settings_keys_oauth_dropbox_callback` mirroring Google’s callback, with Dropbox
   user-facing messages and the Dropbox redirect URI.
6. Template: treat `google` and `dropbox` as OAuth-capable types — auth_kind radios and
   capability fieldsets driven by catalog entries (`dropbox_oauth_capabilities` or a loop
   over the catalog). Prefer extending Alpine so `type === 'dropbox'` gets the same
   Static JSON / OAuth choice as Google without duplicating secret fields incorrectly.
7. Pass both catalogs (or the full catalog dict as JSON) into `keys.html` context.

- [ ] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.web.tests.test_keys_page apps.keys.tests.test_credential_guides
./olib/scripts/orunr py test-all
```

Expected: exit 0; Google OAuth tests remain green.

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/web/views.py backend/apps/web/urls.py backend/apps/web/tests/test_keys_page.py backend/templates/web/keys.html backend/apps/keys/credential_guides.py backend/apps/keys/tests/test_credential_guides.py
git commit -m "feat: add Dropbox OAuth Keys connect flow"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 3: Disk OAuth declarations for Dropbox

**Files:**
- Modify: `backend/libs/providers/key/tests/test_disk_parse.py`
- Modify: `backend/apps/keys/tests/test_disk_sync.py`
- Modify: `examples/local/keys/example-dropbox.yaml` (commented OAuth example and note)

- [ ] **Step 1: Write failing disk tests**

```python
parsed = parse_disk_key_yaml(
    '''
    name: team-dropbox
    type: dropbox
    owner: user@example.com
    source: oauth
    scopes:
      - files_metadata
    '''
)
self.assertEqual(parsed.auth_kind, 'oauth')
self.assertEqual(parsed.auth_config['provider'], 'dropbox')
self.assertEqual(parsed.auth_config['capabilities'], ['files_metadata'])
self.assertIsNone(parsed.value)
```

Reject `value` combined with `source: oauth`. In disk sync tests, create a connected Dropbox
OAuth row, re-upsert an unchanged declaration, and assert ciphertext is preserved; change
capabilities and assert grant clears.

- [ ] **Step 2: Verify failures (if parser already generic, tighten assertions)**

```bash
./olib/scripts/orunr py test libs.providers.key.tests.test_disk_parse apps.keys.tests.test_disk_sync
```

If the generic OAuth parser already accepts any registered provider, the new tests may pass
without code changes — still keep them as regression coverage. Only change production code
if Dropbox-specific validation is missing.

- [ ] **Step 3: Update example YAML comments**

Document both forms in `example-dropbox.yaml` without embedding real secrets. Keep the
static JSON example as the copy-paste default; add a commented OAuth declaration matching
the design.

- [ ] **Step 4: Gate and commit**

```bash
./olib/scripts/orunr py test libs.providers.key.tests.test_disk_parse apps.keys.tests.test_disk_sync
./olib/scripts/orunr py test-all
git add backend/libs/providers/key/tests/test_disk_parse.py backend/apps/keys/tests/test_disk_sync.py examples/local/keys/example-dropbox.yaml
git commit -m "test: cover Dropbox OAuth disk declarations"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 4: Dropbox client dual-auth envelope

**Files:**
- Modify: `backend/libs/clients/dropbox/client.py`
- Modify: `backend/libs/clients/dropbox/tests/test_client.py`
- Modify: `backend/libs/tools/tests/test_dropbox_tool.py` (only if resolution wiring needs an
  explicit OAuth envelope case)

- [ ] **Step 1: Write failing client tests**

```python
def test_builds_sdk_from_static_refresh_json(self) -> None:
    ...

def test_builds_sdk_from_chief_oauth_envelope(self) -> None:
    raw = json.dumps(
        {
            'chief_dropbox_oauth': 1,
            'app_key': 'key',
            'app_secret': 'secret',
            'refresh_token': 'refresh',
            'scopes': ['files.metadata.read'],
        },
        separators=(',', ':'),
        sort_keys=True,
    )
    with patch('dropbox.Dropbox') as ctor:
        _build_sdk(raw)
    ctor.assert_called_once_with(
        oauth2_refresh_token='refresh',
        app_key='key',
        app_secret='secret',
        max_retries_on_error=0,
        max_retries_on_rate_limit=0,
    )

def test_rejects_invalid_oauth_envelope_version(self) -> None:
    with self.assertRaises(DropboxAuthError):
        _build_sdk('{"chief_dropbox_oauth": 2, ...}')
```

Assert secret-bearing locals are cleared on failure paths (follow existing client tests’
traceback local scrubbing pattern). Static JSON path remains the default when the OAuth
sentinel is absent.

- [ ] **Step 2: Verify the tests fail**

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests.test_client
```

Expected: OAuth envelope path fails because `_build_sdk` only accepts the three-field
static shape.

- [ ] **Step 3: Implement dual parsing in `_build_sdk`**

Recognition rule (mirror Google):

- If `chief_dropbox_oauth` is present, require integer `1` (not bool) and exact field set
  `{chief_dropbox_oauth, app_key, app_secret, refresh_token, scopes}` with non-empty
  strings and scopes equal to `['files.metadata.read']` for the current capability set
  (or non-empty validated scope strings matching the envelope).
- Otherwise require the existing static JSON object with `app_key`, `app_secret`,
  `refresh_token`.

Build the SDK the same way in both cases. Do not retain the envelope or static JSON on the
client instance.

Also add a resolver/materialize integration test in `apps.keys.tests.test_queries` (or
oauth services tests) that a connected Dropbox OAuth credential resolves to an envelope
consumed by `_build_sdk` without contacting Dropbox.

- [ ] **Step 4: Gate and commit**

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests.test_client apps.keys.tests.test_queries libs.tools.tests.test_dropbox_tool
./olib/scripts/orunr py test-all
git add backend/libs/clients/dropbox/client.py backend/libs/clients/dropbox/tests/test_client.py backend/apps/keys/tests/test_queries.py
git commit -m "feat: accept Dropbox OAuth runtime envelopes"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 5: Documentation and architecture contract

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/docs/oauth-apps.md`
- Modify: `docs/docs/agents.md`
- Modify: `backend/chief/tests/test_compose_config.py` (architecture assertions for Dropbox)

- [ ] **Step 1: Write failing architecture assertions**

Extend compose-config architecture tests:

```python
self.assertIn('`$KNOX/chief/oauth/dropbox`', architecture)
self.assertIn('- `app_key` → `DROPBOX_OAUTH_APP_KEY`', architecture)
self.assertIn('- `app_secret` → `DROPBOX_OAUTH_APP_SECRET`', architecture)
self.assertIn('`https://<origin>/settings/keys/oauth/dropbox/callback/`', architecture)
```

- [ ] **Step 2: Update docs**

- `ARCHITECTURE.md`: add Dropbox OAuth application/grant ownership beside Google; note
  static JSON remains supported.
- `oauth-apps.md`: Dropbox uses Chief callback
  `/settings/keys/oauth/dropbox/callback/`; document env/Knox; keep optional external
  static provisioning as secondary.
- `agents.md`: document Dropbox `source: oauth` / `scopes: [files_metadata]` and that
  Authenticate stores the grant in Postgres.

- [ ] **Step 3: Gate and commit**

```bash
./olib/scripts/orunr py test chief.tests.test_compose_config
./olib/scripts/orunr py test-all
git add docs/ARCHITECTURE.md docs/docs/oauth-apps.md docs/docs/agents.md backend/chief/tests/test_compose_config.py
git commit -m "docs: document Dropbox OAuth application setup"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## S_final — Code review (mandatory)

### Task 6: Code review

> **REQUIRED SKILL:** Under `/ship`, the parent follows
> **`superpowers/requesting-code-review`**, writes `*-review.md`, fixes or rejects every
> actionable finding, re-verifies, then opens the PR. Do not use Cursor Bugbot.

**Files:** (review only until findings require edits)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review**

Dispatch reviewer subagent with design + plan paths and `$BASE_SHA..$HEAD_SHA`.

- [ ] **Step 4: Write review file**

Write `docs/specs/2026-07-19-dropbox-oauth/2026-07-19-dropbox-oauth-review.md` using
`review-file-template.md`.

- [ ] **Step 5: Fix or reject every finding**

Update **Status** to `Fixed` or `Rejected` with rationale. Re-run
`./olib/scripts/orunr py test-all` after fixes. If any Critical/Important was Fixed, run one
more review pass on the new range.

- [ ] **Step 6: Open PR**

`/ship` finishes via `superpowers/finishing-a-development-branch` (squash → push →
`gh pr create`). Set design status to `review` via `managing-active`.

---

## Out of scope

- Dropbox content or write scopes and corresponding tool operations
- SystemCredential OAuth
- Removing static Dropbox JSON support
- ClickUp ticket bookkeeping for this ship
- Background token health checks

## References

- Design: `docs/specs/2026-07-19-dropbox-oauth/2026-07-19-dropbox-oauth-design.md`
- Google OAuth (framework precedent): `docs/specs/2026-07-18-google-oauth/`
- Cloud file integrations: `docs/specs/2026-07-18-cloud-file-integrations/`

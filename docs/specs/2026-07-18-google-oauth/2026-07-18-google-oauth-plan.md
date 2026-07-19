# Google OAuth Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: This plan is entered through `/ship`.
> Use `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development`
> task-by-task. Create
> `docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-revision.md` from the
> review template before implementation, but do not read it during implementation.
> After all implementation tasks, `/ship` owns mandatory **S_final**: request code
> review, fix or explicitly reject every finding, re-verify, open the PR, and perform
> the ClickUp review-stage actions without pausing at `/plan`, `/impl`, or `/finish`
> handoffs.

**Goal:** Let a user-owned `google` credential contain either the existing service-account
JSON or a Chief-managed Google OAuth refresh grant selected through human-described,
allowlisted capabilities and usable by the existing Gmail and Google Drive integrations.

**Architecture:** Extend `UserCredential` with explicit authentication metadata, then add a
small provider protocol/registry and a Google implementation under `apps.keys.oauth`.
Authenticated web views call key services for a signed, session-bound, one-time OAuth flow;
the encrypted database value remains the only grant store. Runtime resolution emits a
versioned operation-local envelope to one Django-free Google credential builder shared by
Gmail and Drive, while disk reconciliation compares normalized declarations separately from
raw file revisions.

**Tech Stack:** Python 3.13, Django 5.2 models/signing/cache/messages, PostgreSQL, Fernet,
`httpx`, `google-auth`, `google-api-python-client`, Jinja/Alpine, PyYAML, and olib Django
test cases.

**Branch:** `feat/2026-07-18-google-oauth`

**ClickUp:** https://app.clickup.com/t/868kdw0yb

**ClickUp branch field:** `feat/2026-07-18-google-oauth`

---

## Conventions

- Run every command from the repository root with the consumer prefix:
  `./olib/scripts/orunr …`.
- Use scoped tests while iterating, then gate every PR-ready stage with
  `./olib/scripts/orunr py test-all`.
- Generate Django migrations with
  `./olib/scripts/orunr django manage makemigrations keys`; never hand-write a migration.
  Confirm determinism with `./olib/scripts/orunr django manage makemigrations --check`.
- Plan/design documents are the only changes committed on `main`. `/ship` creates an
  isolated worktree on `feat/2026-07-18-google-oauth` before implementation.
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
- `backend/libs/` stays Django-free. Runtime Google helpers receive one operation-local
  string and never import `apps.*`.
- No compatibility re-export shims: update imports to canonical modules and delete replaced
  code rather than retaining pass-through modules.
- Never log or render authorization codes, provider response bodies, access/refresh tokens,
  client secrets, service-account JSON, decrypted grants, or the runtime OAuth envelope.
- Existing static/system credentials and non-Google key types retain their current behavior.
  OAuth remains user-owned; do not add OAuth fields or flows to `SystemCredential`.
- `/ship` owns final review/fix/PR handling. Do not use Cursor Bugbot.

## ClickUp lifecycle

- [x] Before implementation, read task `868kdw0yb` through the ClickUp MCP and verify status
  `doing`, tag `agent`, and Branch custom field
  `407066ea-6c53-41fc-9d9b-c7332886eb82` =
  `feat/2026-07-18-google-oauth`. These claim actions were completed at design start; only
  correct a missing/stale value.
- [ ] After implementation and required verification are green, every review row is
  `Fixed` or `Rejected`, and the GitHub PR is open, set task `868kdw0yb` to `review`, leave
  tag `agent`, and add a ClickUp comment containing the PR URL.

## File map

### Create

- `backend/apps/keys/migrations/0006_usercredential_auth_config_usercredential_auth_kind.py`
  — generated schema migration adding static-default authentication metadata.
- `backend/apps/keys/oauth/__init__.py` — canonical OAuth catalog/registry exports.
- `backend/apps/keys/oauth/types.py` — provider protocol, capability, support, and flow
  records.
- `backend/apps/keys/oauth/registry.py` — duplicate-safe generic provider registry and the
  Google singleton registration.
- `backend/apps/keys/oauth/providers/__init__.py` — provider package marker.
- `backend/apps/keys/oauth/providers/google.py` — complete Google capability catalog,
  authorization/token endpoints, safe code exchange, grant validation/serialization, and
  runtime materialization.
- `backend/apps/keys/oauth/services.py` — auth-config normalization/fingerprints, signed
  one-time state, start/callback/disconnect lifecycle, and atomic grant replacement.
- `backend/apps/keys/oauth/tests/__init__.py`
- `backend/apps/keys/oauth/tests/test_registry.py`
- `backend/apps/keys/oauth/tests/test_google.py`
- `backend/apps/keys/oauth/tests/test_services.py`
- `backend/libs/clients/google_auth.py` — Django-free service-account/OAuth envelope parser
  and Google credential builder.
- `backend/libs/clients/tests/__init__.py`
- `backend/libs/clients/tests/test_google_auth.py`

### Modify

- `backend/apps/keys/models.py` — `CredentialAuthKind`, `auth_kind`, and `auth_config`.
- `backend/apps/keys/admin.py` — display authentication metadata without grant access.
- `backend/apps/keys/exceptions.py` — safe OAuth configuration/state/flow failures.
- `backend/apps/keys/services/commands.py` — static reset semantics, OAuth declaration
  creation, and normalized disk upserts.
- `backend/apps/keys/services/queries.py` — metadata fields, owned-row query, and
  just-in-time OAuth runtime materialization.
- `backend/apps/keys/services/disk_sync.py` — validate disk OAuth capabilities and pass
  normalized declarations to commands.
- `backend/apps/keys/tests/test_models.py`
- `backend/apps/keys/tests/test_migrations.py`
- `backend/apps/keys/tests/test_admin.py`
- `backend/apps/keys/tests/test_commands.py`
- `backend/apps/keys/tests/test_queries.py`
- `backend/apps/keys/tests/test_disk_sync.py`
- `backend/libs/providers/key/disk_parse.py` — mutually exclusive static `value` and OAuth
  `source`/`scopes` forms.
- `backend/libs/providers/key/tests/test_disk_parse.py`
- `backend/apps/web/views.py` — metadata-only page context and thin OAuth HTTP views.
- `backend/apps/web/urls.py` — fixed connect, disconnect, and Google callback routes.
- `backend/apps/web/tests/test_keys_page.py` — form/catalog/status/actions, security
  boundaries, callback behavior, and secret leak assertions.
- `backend/templates/web/keys.html` — service-account/OAuth choice and described capability
  checkboxes.
- `backend/templates/web/partials/key_list.html` — Connected/Not connected and lifecycle
  controls, including disk-owned OAuth rows.
- `backend/libs/clients/gmail/client.py` — shared credential builder and OAuth subject
  semantics.
- `backend/libs/clients/gmail/tests/test_client.py`
- `backend/libs/clients/google_drive/client.py` — shared credential builder while preserving
  root enforcement.
- `backend/libs/clients/google_drive/tests/test_client.py`
- `backend/libs/sources/adapters/gmail.py` — allow OAuth Gmail configs without a delegation
  subject while retaining runtime enforcement for service accounts.
- `backend/libs/sources/tests/test_gmail_adapter.py`
- `backend/chief/settings.py` — optional Google OAuth app settings and state lifetime.
- `backend/chief/tests/test_compose_config.py` — environment and structured-secret contract.
- `.env.local.example` — backend-group OAuth client settings.
- `docs/ARCHITECTURE.md` — OAuth ownership, runtime envelope, capabilities, and Knox
  structured-secret wiring.

---

### Task 1: Add explicit credential authentication metadata

**Files:**
- Modify: `backend/apps/keys/models.py`
- Generate: `backend/apps/keys/migrations/0006_usercredential_auth_config_usercredential_auth_kind.py`
- Modify: `backend/apps/keys/services/queries.py`
- Modify: `backend/apps/keys/admin.py`
- Test: `backend/apps/keys/tests/test_models.py`
- Test: `backend/apps/keys/tests/test_migrations.py`
- Test: `backend/apps/keys/tests/test_queries.py`
- Test: `backend/apps/keys/tests/test_admin.py`

- [x] **Step 1: Write failing model, metadata, and admin tests**

Add assertions equivalent to:

```python
row = UserCredential.objects.create(
    user=user,
    name='legacy-google',
    type='google',
    encrypted_value=b'unchanged-ciphertext',
)
self.assertEqual(row.auth_kind, CredentialAuthKind.STATIC)
self.assertEqual(row.auth_config, {})

metadata = queries._user_metadata(row)
self.assertEqual(metadata.auth_kind, 'static')
self.assertEqual(metadata.oauth_capabilities, ())
self.assertNotIn('unchanged-ciphertext', repr(metadata))
```

In `test_migrations.py`, inspect the generated migration's operations and assert both fields
have defaults (`static` and `dict`) without a `RunPython` operation. In `test_admin.py`,
assert user credential admin exposes `auth_kind` and safe `auth_config` metadata while
continuing to exclude `encrypted_value`.

- [x] **Step 2: Verify the tests fail before schema changes**

Run:

```bash
./olib/scripts/orunr py test apps.keys.tests.test_models apps.keys.tests.test_migrations apps.keys.tests.test_queries apps.keys.tests.test_admin
```

Expected: failure because `CredentialAuthKind`, model fields, and metadata attributes do not
exist.

- [x] **Step 3: Implement fields and metadata, then generate the migration**

Add:

```python
class CredentialAuthKind(models.TextChoices):
    """Select how a user credential is authenticated at runtime."""

    STATIC = 'static', 'Static'
    OAUTH = 'oauth', 'OAuth'


class UserCredential(models.Model):
    # Existing fields remain.
    encrypted_value = models.BinaryField(blank=True, default=bytes)
    auth_kind = models.CharField(
        max_length=16,
        choices=CredentialAuthKind.choices,
        default=CredentialAuthKind.STATIC,
    )
    auth_config = models.JSONField(default=dict, blank=True)
```

Extend `KeyMetadata` with:

```python
auth_kind: str = 'static'
oauth_provider: str | None = None
oauth_capabilities: tuple[str, ...] = ()
```

Populate those fields only from validated `auth_config`; malformed stored metadata must
produce empty provider/capability metadata rather than raise or decrypt. Add the safe fields
to `UserCredentialAdmin.list_display`/`readonly_fields`.

Generate, do not hand-author:

```bash
./olib/scripts/orunr django manage makemigrations keys
./olib/scripts/orunr django manage makemigrations --check
```

Expected: generated `0006_…` adds the two fields and adjusts `encrypted_value`; the check
reports no model changes.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.keys.tests
./olib/scripts/orunr py test-all
```

Expected: exit 0; existing rows/default construction remain static without ciphertext
changes.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/apps/keys/models.py backend/apps/keys/migrations/0006_*.py backend/apps/keys/services/queries.py backend/apps/keys/admin.py backend/apps/keys/tests/
git commit -m "feat: add credential authentication metadata"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 2: Build the generic OAuth registry and complete Google capability catalog

**Files:**
- Create: `backend/apps/keys/oauth/__init__.py`
- Create: `backend/apps/keys/oauth/types.py`
- Create: `backend/apps/keys/oauth/registry.py`
- Create: `backend/apps/keys/oauth/providers/__init__.py`
- Create: `backend/apps/keys/oauth/providers/google.py`
- Create: `backend/apps/keys/oauth/tests/__init__.py`
- Create: `backend/apps/keys/oauth/tests/test_registry.py`
- Create: `backend/apps/keys/oauth/tests/test_google.py`
- Modify: `backend/apps/keys/exceptions.py`

- [x] **Step 1: Write failing registry, catalog, URL, and exchange tests**

Test these concrete contracts:

```python
@dataclass(frozen=True, slots=True)
class OAuthCapability:
    id: str
    label: str
    description: str
    scope: str
    support: Literal['current', 'future']


class OAuthProvider(Protocol):
    id: str
    credential_type: str
    capabilities: tuple[OAuthCapability, ...]

    def normalize_capabilities(self, capability_ids: Iterable[str]) -> tuple[str, ...]: ...
    def build_authorization_url(self, *, redirect_uri: str, state: str,
                                capability_ids: tuple[str, ...]) -> str: ...
    def exchange_code(self, *, code: str, redirect_uri: str,
                      capability_ids: tuple[str, ...]) -> str: ...
    def materialize_runtime(self, *, grant_payload: str,
                            capability_ids: tuple[str, ...]) -> str: ...
```

Assert `OAuthProviderRegistry.register()` rejects duplicate IDs, `get()` rejects unknown
providers, and Google rejects empty/unknown IDs and raw scope URLs. Assert normalization
deduplicates and returns stable catalog order.

Assert the Google catalog contains exactly these rows:

```python
(
    ('gmail_read', 'Read Gmail', 'view messages and Gmail settings without changing or sending mail.',
     'https://www.googleapis.com/auth/gmail.readonly', 'current'),
    ('gmail_modify', 'Manage Gmail',
     'read mail, change labels/archive/trash, and compose/send mail. Google includes sending in this scope.',
     'https://www.googleapis.com/auth/gmail.modify', 'current'),
    ('gmail_send', 'Send Gmail', 'send mail without granting mailbox read access.',
     'https://www.googleapis.com/auth/gmail.send', 'current'),
    ('drive_metadata', 'Read Drive metadata',
     'list/search file names and metadata without downloading content.',
     'https://www.googleapis.com/auth/drive.metadata.readonly', 'current'),
    ('drive_read', 'Read Drive files',
     'search, view, and download all visible Drive files without changing them.',
     'https://www.googleapis.com/auth/drive.readonly', 'future'),
    ('drive_file', 'Manage selected Drive files',
     'create or modify only files opened with or explicitly shared with Chief; a future Google Picker/share flow is required.',
     'https://www.googleapis.com/auth/drive.file', 'future'),
    ('drive_manage', 'Manage all Drive files',
     'search, read, create, update, move, and delete all visible Drive files.',
     'https://www.googleapis.com/auth/drive', 'future'),
    ('docs_read', 'Read Google Docs', 'read all visible Google Docs documents.',
     'https://www.googleapis.com/auth/documents.readonly', 'future'),
    ('docs_write', 'Manage Google Docs',
     'read, create, edit, and delete all visible Google Docs documents.',
     'https://www.googleapis.com/auth/documents', 'future'),
    ('sheets_read', 'Read Google Sheets', 'read all visible spreadsheets.',
     'https://www.googleapis.com/auth/spreadsheets.readonly', 'future'),
    ('sheets_write', 'Manage Google Sheets',
     'read, create, edit, and delete all visible spreadsheets.',
     'https://www.googleapis.com/auth/spreadsheets', 'future'),
)
```

Stub `httpx.post`; assert exchange stores only version, refresh token, and validated granted
scopes, rejects missing refresh token/partial scope grants, and never includes the returned
access token or provider body in messages/repr/logs.

- [x] **Step 2: Run tests to confirm the package is absent**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests
```

Expected: import failure for `apps.keys.oauth`.

- [x] **Step 3: Implement protocol, registry, and Google provider**

Use `OAuthProviderRegistry` with a private `dict[str, OAuthProvider]`, explicit duplicate and
unknown-provider `KeyValidationError`s, and one registered `GoogleOAuthProvider`.

Use fixed provider endpoints:

```python
AUTHORIZATION_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
GOOGLE_CAPABILITIES: tuple[OAuthCapability, ...] = (...)
```

Load `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` lazily from Django settings
inside start/exchange/materialize methods. Missing values raise
`OAuthConfigurationError('Google OAuth is not configured')`. Build authorization URLs with
`response_type=code`, exact expanded scopes, `access_type=offline`, `prompt=consent`, and
the supplied fixed callback/state. Exchange through an injectable `httpx.post` seam with a
bounded timeout; sanitize all HTTP/provider failures.

Serialize an encrypted-at-rest grant shape:

```json
{"version":1,"refresh_token":"provider-refresh-token","granted_scopes":["..."]}
```

and a runtime-only shape:

```json
{"chief_google_oauth":1,"client_id":"...","client_secret":"...","refresh_token":"...","scopes":["..."],"token_uri":"https://oauth2.googleapis.com/token"}
```

The runtime shape is returned only by `materialize_runtime`; it is never stored or rendered.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests.test_registry apps.keys.oauth.tests.test_google
./olib/scripts/orunr py test-all
```

Expected: exit 0 with all eleven capability label/description/scope/support records covered.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/apps/keys/oauth backend/apps/keys/exceptions.py
git commit -m "feat: add Google OAuth provider registry"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 3: Implement declaration, secure state, callback, and disconnect services

**Files:**
- Create: `backend/apps/keys/oauth/services.py`
- Create: `backend/apps/keys/oauth/tests/test_services.py`
- Modify: `backend/apps/keys/services/commands.py`
- Modify: `backend/apps/keys/services/queries.py`
- Modify: `backend/apps/keys/tests/test_commands.py`
- Modify: `backend/apps/keys/tests/test_queries.py`

- [x] **Step 1: Write failing lifecycle and resolver tests**

Cover these signatures:

```python
def normalize_auth_config(*, provider_id: str, credential_type: str,
                          capability_ids: Iterable[str]) -> dict[str, object]: ...
def auth_config_fingerprint(auth_config: Mapping[str, object]) -> str: ...
def create_user_oauth(user_id: int, name: str, type_name: str, *,
                      provider_id: str, capability_ids: Iterable[str]) -> KeyMetadata: ...
def start_authorization(*, user_id: int, credential_id: UUID, session_key: str,
                        redirect_uri: str) -> OAuthStart: ...
def complete_authorization(*, user_id: int, session_key: str, state: str,
                           code: str | None, redirect_uri: str) -> KeyMetadata: ...
def disconnect_authorization(*, user_id: int,
                             credential_id: UUID) -> KeyMetadata: ...
```

Tests must demonstrate:

- OAuth creation requires type `google`, provider `google`, and at least one allowlisted
  capability; it creates active `auth_kind=oauth`, normalized sorted `auth_config`, and
  `encrypted_value=b''`.
- Static upsert explicitly resets `auth_kind=static` and `auth_config={}`.
- start rejects cross-user, disabled, static, and unconfigured rows without decrypting.
- signed state contains user ID, credential UUID, provider, random nonce, session binding,
  and auth-config fingerprint but no grant/app secret.
- tampered, expired, replayed, cross-session, cross-user, and mismatched-provider/config
  states fail before exchange.
- callback consumes the nonce before code exchange; consent denial consumes state without
  exchanging.
- provider exchange failure, missing scope, and a concurrent credential change preserve
  the old ciphertext.
- successful reauthentication locks the row, rechecks every binding, encrypts the new grant,
  and publishes one post-commit keys refresh.
- disconnect clears only `encrypted_value`, retaining name/type/source/auth metadata.
- `resolve_secret()` raises the existing typed missing-credential failure for an unconnected
  OAuth row and materializes a connected OAuth row only at call time.

- [x] **Step 2: Run tests to verify lifecycle functions are missing**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests.test_services apps.keys.tests.test_commands apps.keys.tests.test_queries
```

Expected: failures for missing lifecycle APIs.

- [x] **Step 3: Implement normalized declarations and one-time state**

Use canonical JSON (`sort_keys=True`, compact separators) and SHA-256 for the config
fingerprint. Use `django.core.signing.dumps(..., salt='chief.keys.oauth.state')` and
`loads(..., max_age=settings.OAUTH_STATE_MAX_AGE_SECONDS)` with a default of 600 seconds.
Bind the state to `sha256(session_key.encode()).hexdigest()`.

Use a random 32-byte URL-safe nonce and a cache marker named only from its SHA-256 digest:

```python
marker = f'keys:oauth-state:{hashlib.sha256(nonce.encode()).hexdigest()}'
if not cache.add(marker, True, timeout=settings.OAUTH_STATE_MAX_AGE_SECONDS):
    raise OAuthStateError('OAuth authorization could not be started')
```

After signature and user/session/provider validation, require `cache.delete(marker)` to
return true before exchange. A second callback therefore cannot exchange or write. Query
the owned active OAuth row before exchange; after exchange, enter `transaction.atomic()`,
reload with `select_for_update()`, and recheck owner, status, provider, auth kind, and
fingerprint before encrypting and saving. Never clear an old grant at start.

In `resolve_secret()`, preserve system/static behavior, but for a user OAuth row:

```python
if row.auth_kind == CredentialAuthKind.OAUTH:
    if not _is_set(row.encrypted_value):
        raise KeyNotFoundError(f'credential not connected: {name}')
    return materialize_runtime_credential(row)
```

`materialize_runtime_credential()` decrypts the grant, delegates to the registered provider,
and clears grant-bearing locals before propagating a sanitized typed failure.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.keys.oauth.tests.test_services apps.keys.tests.test_commands apps.keys.tests.test_queries
./olib/scripts/orunr py test-all
```

Expected: exit 0; replay/concurrency tests prove unsuccessful callbacks never replace a
grant.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/apps/keys/oauth/services.py backend/apps/keys/oauth/tests/test_services.py backend/apps/keys/services/commands.py backend/apps/keys/services/queries.py backend/apps/keys/tests/test_commands.py backend/apps/keys/tests/test_queries.py
git commit -m "feat: secure the OAuth grant lifecycle"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 4: Reconcile static and OAuth disk declarations without losing grants

**Files:**
- Modify: `backend/libs/providers/key/disk_parse.py`
- Modify: `backend/libs/providers/key/tests/test_disk_parse.py`
- Modify: `backend/apps/keys/services/disk_sync.py`
- Modify: `backend/apps/keys/services/commands.py`
- Modify: `backend/apps/keys/tests/test_disk_sync.py`
- Modify: `backend/apps/keys/tests/test_commands.py`

- [x] **Step 1: Write failing parser and reconciliation tests**

Change the parser record to:

```python
@dataclass(frozen=True)
class KeyDiskFile:
    name: str
    type: str
    owner: str
    auth_kind: Literal['static', 'oauth']
    value: str | None
    capabilities: tuple[str, ...]
    source_path: str
    source_rev: str
```

Parse this exact OAuth form:

```yaml
name: work-google
type: google
owner: user@example.com
source: oauth
scopes:
  - drive_metadata
  - gmail_read
```

Assert `value` and `source: oauth` are mutually exclusive; OAuth requires a non-empty list
of non-empty string capability IDs and static declarations still require the `value` key
(including an explicitly empty value). Reject unsupported `source` values and extra
OAuth/static-only fields.

Reconciliation tests must cover new unconnected creation, grant preservation across
whitespace/key-order/raw revision changes, clearing on capability/auth-kind/type changes,
 missing-file disable, unchanged restore preserving the grant, changed restore clearing it,
unknown/raw scopes, and safe logs with no YAML value/grant.

- [x] **Step 2: Run disk tests to confirm current parser requires `value`**

```bash
./olib/scripts/orunr py test libs.providers.key.tests.test_disk_parse apps.keys.tests.test_disk_sync apps.keys.tests.test_commands
```

Expected: OAuth-form tests fail.

- [x] **Step 3: Implement semantic reconciliation**

Keep `source_rev=content_hash(raw)` for provenance, but compare normalized semantic fields
before deciding grant retention. Extend the disk command boundary:

```python
def upsert_user_named_from_disk(
    user_id: int,
    name: str,
    type_name: str,
    secret: str | None,
    *,
    auth_kind: str,
    auth_config: Mapping[str, object],
    source_path: str,
    source_rev: str,
) -> tuple[KeyMetadata, bool]:
    """Reconcile one disk declaration while preserving only semantically valid grants."""
```

For OAuth, validate via `normalize_auth_config()`, create with empty bytes, and preserve
existing ciphertext only when existing `type`, `auth_kind`, and normalized `auth_config`
match. Always update path/revision/status on a present declaration. For static, validate and
encrypt `secret`; changing from OAuth clears/replaces the grant. Restoring a disabled row
uses the same semantic comparison. Existing DB-owned collision behavior remains unchanged.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test libs.providers.key.tests.test_disk_parse apps.keys.tests.test_disk_sync apps.local_sync.tests
./olib/scripts/orunr py test-all
```

Expected: exit 0; formatting-only edits and unchanged restore preserve OAuth ciphertext.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/libs/providers/key/disk_parse.py backend/libs/providers/key/tests/test_disk_parse.py backend/apps/keys/services/disk_sync.py backend/apps/keys/services/commands.py backend/apps/keys/tests/test_disk_sync.py backend/apps/keys/tests/test_commands.py
git commit -m "feat: reconcile disk OAuth declarations"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 5: Add the Keys-page OAuth form and fixed authorization routes

**Files:**
- Modify: `backend/apps/keys/services/queries.py`
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/templates/web/keys.html`
- Modify: `backend/templates/web/partials/key_list.html`
- Modify: `backend/apps/web/tests/test_keys_page.py`

- [x] **Step 1: Write failing HTTP/UI/security tests**

Assert the Google add form offers Service account and OAuth modes. For every capability,
assert rendered label, description, and `Available now`/`Future support`; explicitly assert
the `gmail_modify` text says Google includes sending, `gmail_read` says it cannot send,
Drive metadata/read/selected-file/all-file choices are distinct, and Docs/Sheets read/write
choices are marked future. Assert all capabilities default unchecked and OAuth submission
contains no required secret field.

Cover these fixed routes:

```python
path('settings/keys/oauth/<uuid:credential_id>/authorize/', views.settings_keys_oauth_authorize,
     name='settings_keys_oauth_authorize')
path('settings/keys/oauth/<uuid:credential_id>/disconnect/', views.settings_keys_oauth_disconnect,
     name='settings_keys_oauth_disconnect')
path('settings/keys/oauth/google/callback/', views.settings_keys_oauth_google_callback,
     name='settings_keys_oauth_google_callback')
```

Test login, ownership, POST-only and CSRF behavior for start/disconnect; callback GET with
the existing session; fixed internal redirect; HTTPS enforcement when `DEBUG=False`; safe
messages for denial/state/provider failures; Authenticate/Reauthenticate/Disconnect button
states; disk OAuth lifecycle buttons despite read-only declaration metadata; disabled rows
having no active controls; and absence of codes/tokens/client secrets/service-account
sentinels from HTML, redirect URLs, messages, and captured logs.

- [x] **Step 2: Run page tests to verify routes/catalog are absent**

```bash
./olib/scripts/orunr py test apps.web.tests.test_keys_page
```

Expected: failures for missing catalog markup and OAuth routes.

- [x] **Step 3: Implement thin views and provider-driven rendering**

Add a keys query `get_owned_user_credential(user_id: int, credential_id: UUID) ->
UserCredential` and use it inside key services; views must not query the model. Serialize a
secret-free provider catalog into page data using the existing `<`, `>`, `&` escaping
pattern.

Post `auth_kind=static|oauth` and repeated `capabilities` values. Static calls
`upsert_user_named`; OAuth calls `create_user_oauth`. Start ensures the Django session has a
session key, computes only the fixed absolute callback URL, calls `start_authorization`,
and redirects to the returned Google URL. Callback accepts only `state`, `code`, and
provider `error`, calls `complete_authorization`, uses Django messages with fixed safe text,
and always redirects to `settings_keys`. Outside `DEBUG`, reject a callback URL not using
HTTPS.

Render OAuth status as Connected iff active and `is_set`; this is metadata, not a provider
health probe. Disconnect clears the grant but leaves the row. Keep replace/delete behavior
for static UI-owned rows, and keep disk declaration fields read-only while allowing OAuth
authorize/disconnect actions.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test apps.web.tests.test_keys_page apps.keys.oauth.tests.test_services
./olib/scripts/orunr py test-all
```

Expected: exit 0; rendered forms expose descriptions/support statuses but no secret
material.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/apps/keys/services/queries.py backend/apps/web/views.py backend/apps/web/urls.py backend/templates/web/keys.html backend/templates/web/partials/key_list.html backend/apps/web/tests/test_keys_page.py
git commit -m "feat: add Google OAuth key controls"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 6: Use one runtime Google credential builder for Gmail and Drive

**Files:**
- Create: `backend/libs/clients/google_auth.py`
- Create: `backend/libs/clients/tests/__init__.py`
- Create: `backend/libs/clients/tests/test_google_auth.py`
- Modify: `backend/libs/clients/gmail/client.py`
- Modify: `backend/libs/clients/gmail/tests/test_client.py`
- Modify: `backend/libs/clients/google_drive/client.py`
- Modify: `backend/libs/clients/google_drive/tests/test_client.py`
- Modify: `backend/libs/sources/adapters/gmail.py`
- Modify: `backend/libs/sources/tests/test_gmail_adapter.py`
- Modify: `backend/apps/keys/tests/test_queries.py`

- [x] **Step 1: Write failing helper and integration tests**

Exercise:

```python
def build_google_credentials(
    raw_credential: str,
    *,
    service_account_scopes: tuple[str, ...],
    subject: str | None,
    require_service_account_subject: bool,
) -> google.auth.credentials.Credentials:
    """Build operation-local service-account or Chief OAuth credentials."""
```

For service-account JSON, assert current scopes/delegation remain unchanged and Gmail still
requires `subject`. For the versioned Chief envelope, assert
`google.oauth2.credentials.Credentials` receives refresh token, client ID/secret, token URI,
and envelope scopes; `subject` is ignored.

Add Gmail tests proving OAuth uses `userId='me'` without a subject for read/send paths and
that `gmail_read`, `gmail_modify`, and `gmail_send` scopes are passed through rather than
replaced by the service-account scope tuple. Add Drive tests proving OAuth ignores subject
and all existing configured-root/ancestry behavior is unchanged. Add Gmail source tests
allowing an omitted subject but rejecting a malformed supplied subject.

Inspect propagated traceback locals and exception text/repr to ensure raw static JSON,
refresh token, client secret, envelope, parsed dict, credentials, service, provider body,
and access token sentinels are cleared or absent.

- [x] **Step 2: Run client tests to verify only service accounts are supported**

```bash
./olib/scripts/orunr py test libs.clients.tests.test_google_auth libs.clients.gmail.tests.test_client libs.clients.google_drive.tests.test_client libs.sources.tests.test_gmail_adapter
```

Expected: helper import failure and OAuth-path test failures.

- [x] **Step 3: Implement and adopt the shared Django-free helper**

Strictly distinguish the OAuth envelope by the exact integer sentinel
`chief_google_oauth == 1`; otherwise require a Google service-account object. Reject unknown
envelope fields, malformed/non-string secrets, empty scopes, and boolean versions with safe
auth failures.

For OAuth:

```python
credentials.Credentials(
    token=None,
    refresh_token=envelope['refresh_token'],
    token_uri=envelope['token_uri'],
    client_id=envelope['client_id'],
    client_secret=envelope['client_secret'],
    scopes=envelope['scopes'],
)
```

For service accounts, use each client's existing scope tuple and apply `with_subject()` only
when present; Gmail sets `require_service_account_subject=True`, Drive sets it false.
Refactor each `_build_service` to call the helper, preserve `cache_discovery=False`, and
clear credential-bearing locals in `finally`.

Make Gmail source structural validation require `query`; `subject` becomes optional but, if
present, must be a non-empty string. Runtime helper enforcement keeps the service-account
path strict. Existing runner/tool/source `make_secret_supplier` wiring is unchanged and now
materializes OAuth lazily for every operation.

- [x] **Step 4: Run integration and full gates**

```bash
./olib/scripts/orunr py test libs.clients.tests.test_google_auth libs.clients.gmail.tests libs.clients.google_drive.tests libs.sources.tests.test_gmail_adapter apps.agents.tests.test_tool_wiring apps.keys.tests.test_queries
./olib/scripts/orunr py test-all
```

Expected: exit 0; Gmail and Drive accept both auth kinds through the same `google`
credential reference, with no persisted access token.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/libs/clients/google_auth.py backend/libs/clients/tests backend/libs/clients/gmail/client.py backend/libs/clients/gmail/tests/test_client.py backend/libs/clients/google_drive/client.py backend/libs/clients/google_drive/tests/test_client.py backend/libs/sources/adapters/gmail.py backend/libs/sources/tests/test_gmail_adapter.py backend/apps/keys/tests/test_queries.py
git commit -m "feat: authenticate Google clients with OAuth"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 7: Wire optional app settings and document the Knox structured secret

**Files:**
- Modify: `backend/chief/settings.py`
- Modify: `.env.local.example`
- Modify: `backend/chief/tests/test_compose_config.py`
- Modify: `docs/ARCHITECTURE.md`

- [x] **Step 1: Write failing configuration contract tests**

Assert settings default to empty strings so deployments without OAuth boot normally. Assert
`.env.local.example` places both variables under `#[backend]`:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
```

Assert architecture docs name the single production structured secret
`$KNOX/chief/oauth/google`, exact keys `client_id` and `client_secret`, mapping to the two
environment settings, fixed per-origin callback registration, and HTTPS outside local
development. Assert docs say Chief never reads Knox directly.

- [x] **Step 2: Run the configuration tests**

```bash
./olib/scripts/orunr py test chief.tests.test_compose_config
```

Expected: failure because settings/example/docs do not yet define the contract.

- [x] **Step 3: Add lazy optional settings and architecture documentation**

Add:

```python
GOOGLE_OAUTH_CLIENT_ID = env.str('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_OAUTH_CLIENT_SECRET = env.str('GOOGLE_OAUTH_CLIENT_SECRET', default='')
OAUTH_STATE_MAX_AGE_SECONDS = 600
```

Document that `.env.local` is already loaded by backend, worker, and Beat Compose services;
only web start/callback and operation-time OAuth materialization require the values.
Document the provider registry boundary, capability IDs/support statuses, encrypted grant
versus runtime envelope, disk declaration ownership, and Knox mapping. Do not add a Knox
client or commit real values.

- [x] **Step 4: Run focused and full gates**

```bash
./olib/scripts/orunr py test chief.tests.test_compose_config apps.keys.oauth.tests
./olib/scripts/orunr py test-all
```

Expected: exit 0 and application startup remains valid with blank OAuth app settings.

- [x] **Step 5: Commit and sync this PR-ready chunk**

```bash
git add backend/chief/settings.py backend/chief/tests/test_compose_config.py .env.local.example docs/ARCHITECTURE.md
git commit -m "docs: wire Google OAuth application secrets"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

### Task 8: Run cross-cutting regression and secret-retention verification

**Files:**
- Modify only a failing implementation/test file named by the gate; do not broaden scope.

- [x] **Step 1: Run all affected suites together**

```bash
./olib/scripts/orunr py test apps.keys.tests apps.keys.oauth.tests apps.web.tests.test_keys_page apps.local_sync.tests libs.providers.key.tests libs.clients.gmail.tests libs.clients.google_drive.tests libs.clients.tests.test_google_auth libs.sources.tests.test_gmail_adapter apps.agents.tests.test_tool_wiring chief.tests.test_compose_config
```

Expected: exit 0 with no network access; Google authorization/token/API boundaries are
stubbed.

- [x] **Step 2: Run migration and project checks**

```bash
./olib/scripts/orunr django manage makemigrations --check
./olib/scripts/orunr django manage check
```

Expected: no model changes and no Django system-check findings.

- [x] **Step 3: Run the mandatory Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: lint, mypy, tests, and bandit all exit 0.

- [x] **Step 4: Commit and sync only if verification required a correction**

If no files changed, do not create an empty commit. If a focused correction was necessary:

```bash
git add -u backend
git commit -m "fix: harden Google OAuth integration"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

Stop and ask the human if rebase conflicts.

---

## S_final — Code review (mandatory)

### Task 9: Review, fix findings, re-verify, and hand back to `/ship`

> **REQUIRED SKILL:** Read and follow `superpowers/requesting-code-review`. Dispatch the
> reviewer using `requesting-code-review/code-reviewer.md`, reviewing the feature branch
> against both this plan and the approved design. Write findings to
> `docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-review.md` using
> `review-file-template.md`. Because the entry command is `/ship`, fix every actionable
> finding, mark every row `Fixed` or `Rejected` with rationale, re-verify, and only then
> continue to PR creation.

**Files:**
- Create: `docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-review.md`
- Modify: finding-specific implementation/test files when required

- [x] **Step 1: Confirm the full gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [x] **Step 2: Compute the review range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [x] **Step 3: Dispatch the required reviewer**

Use:

- `{DESCRIPTION}` — Google OAuth declarations, provider registry/capabilities, secure
  authorization lifecycle, disk reconciliation, Keys UI, and Gmail/Drive runtime auth.
- `{PLAN_OR_REQUIREMENTS}` —
  `docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-design.md` and this plan.
- `{BASE_SHA}` / `{HEAD_SHA}` — values from Step 2.

Require special review attention to state replay/session binding, row rechecks and atomic
replacement, capability/scope completeness (including Gmail modify's send permission),
disk grant preservation/clearing, runtime subject behavior, root enforcement, and every
human/log/traceback secret surface.

- [x] **Step 4: Write and classify the review file**

Create one table per severity with columns `#`, `Status`, `Location`, `Finding`, and
`Notes`. Record all findings and an overall assessment.

- [x] **Step 5: Fix or explicitly reject every finding**

Follow `superpowers/receiving-code-review` for technically questionable feedback. Add a
regression test before each code fix, set each row to `Fixed` after verification, or
`Rejected` with a concrete rationale. If any Critical/Important row was fixed, run one more
review pass on the new range and resolve new actionable findings.

- [x] **Step 6: Re-run verification and commit review/fixes**

```bash
./olib/scripts/orunr py test-all
git add docs/specs/2026-07-18-google-oauth/2026-07-18-google-oauth-review.md
git add -u backend
git commit -m "fix: address Google OAuth review findings"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If no implementation finding required a fix, use commit message
`docs: record Google OAuth code review`. Stop on rebase conflicts.

- [ ] **Step 7: Return to `/ship` for PR and ClickUp review stage**

Follow `superpowers/finishing-a-development-branch` with `/ship`: final verify, squash as
that skill directs, push, open the PR, set design status to `review` through
`managing-active`, then execute the ClickUp review-status/comment checklist above. Do not
merge or set design status `done`.

---

## Out of scope

- OAuth providers other than Google, OAuth `SystemCredential`, multiple grants/history,
  arbitrary scope URLs, device flows, token revocation endpoints, health polling, and
  automatic consent renewal.
- Persisted access tokens or Google account/profile discovery.
- Drive content/download/mutation, Picker/share flows, and Docs/Sheets tools. Their
  capabilities are consent choices marked future, not runtime features in this change.
- Changes to existing tool-instance `allow`/`deny`; it remains the application-level way to
  block Gmail send when `gmail_modify` is needed for mailbox changes.

## Plan self-review

- Design coverage: Tasks 1–7 cover explicit metadata, every capability label/description/
  support state, Gmail read/manage/send caveat, all four Drive levels, future Docs/Sheets,
  registry, secure callback, disk semantics, runtime Gmail/Drive auth, and the Knox secret.
- Security consistency: state is signed, TTL-bound, user/session/provider/config-bound and
  atomically consumed before exchange; callback rechecks the locked row before replacement.
- Type consistency: model `auth_config` uses `provider` + ordered `capabilities`; the same
  normalized shape drives fingerprints, metadata, disk comparison, and runtime provider
  lookup.
- Migration consistency: Task 1 generates the migration with Django tooling and checks for
  drift; no manual migration or JSON data rewrite is planned.
- Hygiene: docstrings, olib test bases, parproc names, canonical imports, no re-export shims,
  scoped `orunr` commands, and one commit per PR-ready chunk are explicit.
- Workflow: this is a standalone spec (no Epic line/side effects), design status moves to
  `plan` on `main`, no feature branch is created during planning, and S_final is the last
  checkbox task.
- Placeholder scan: implementation steps contain concrete paths, signatures, payloads,
  commands, and expected outcomes; no unresolved product or implementation decisions remain.

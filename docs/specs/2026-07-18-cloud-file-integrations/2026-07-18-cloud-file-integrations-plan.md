# Dropbox and Google Drive Metadata Integrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: This plan is entered through `/ship`.
> Use `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development`
> task-by-task. Create
> `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-revision.md`
> from the review template before implementation, but do not read it during implementation.
> After all implementation tasks, `/ship` owns mandatory **S_final**: request code review,
> fix or explicitly reject every finding, re-verify, open the PR, and perform the ClickUp
> review-stage actions without pausing at `/plan`, `/impl`, or `/finish` handoffs.

**Goal:** Give Chief agents metadata-only, read-only access to explicitly configured Google
Drive and Dropbox roots through separate tools, while cutting Gmail over to the canonical
shared `google` credential type.

**Architecture:** Add Django-free provider clients under `backend/libs/clients/` and thin
agent tools under `backend/libs/tools/tools/`, following the existing Gmail and ClickUp
credential-supplier and injectable-client-factory patterns. Each provider validates
non-empty aliased roots, normalizes metadata into the shared response shape, wraps
provider pagination in instance-bound cursors, and revalidates current ancestry/path on
every result. A narrow irreversible Django data migration changes stored Gmail credential
metadata without decrypting ciphertext.

**Tech Stack:** Python 3.13, Django 5.2 migrations, Pydantic agent specs, Google Drive v3 via
`google-auth` and `google-api-python-client`, official Dropbox Python SDK, `OTestCase` /
`OTransactionTestCase`, injected `MagicMock` provider boundaries.

**Branch:** `feat/2026-07-18-cloud-file-integrations`

**ClickUp:** https://app.clickup.com/t/868kduv64

**ClickUp branch field:** `feat/2026-07-18-cloud-file-integrations`

---

## Conventions

- Commands run from repository root with the explicit consumer prefix:
  `./olib/scripts/orunr …`.
- Use scoped commands such as
  `./olib/scripts/orunr py test libs.clients.google_drive.tests` while iterating and
  `./olib/scripts/orunr py test-all` for the final Python gate.
- Use `./olib/scripts/orunr django manage …` for Django migration generation and checks.
- Implementation occurs only in the worktree on
  `feat/2026-07-18-cloud-file-integrations`; plan/design documents remain docs-only work on
  the default branch until `/ship` creates the worktree.
- After each PR-ready implementation commit run:
  `git fetch origin main && git rebase origin/main && git push -u origin HEAD`. Stop and
  ask the human if the rebase conflicts.
- Every new or materially changed function/method receives a concise purpose docstring and
  documents assumptions or non-obvious authorization behavior, per `AGENTS.md`.
- Tests use `OTestCase`, `OTransactionTestCase`, or `OLiveServerTestCase`; never bare
  `unittest.TestCase`.
- Test names avoid the parproc keywords `exception`, `error`, `warning`, `notice`,
  `deprecated`, and `deprecation`.
- Clients under `libs/` remain Django-free. They receive secret suppliers and test seams;
  they never import `apps.keys`.
- Secrets are resolved just in time. Provider clients may retain supplier callables and
  non-secret config, but not plaintext credentials, built API services, SDK clients, access
  tokens, response bodies, or search results beyond one public operation.
- No compatibility aliases or re-export shims: `gmail` is removed as a credential type.
  Integration, tool, and source identifiers named `gmail` remain unchanged.
- All cloud-file functions are metadata-only and `readonly=True`. Do not add download,
  export, preview, thumbnail, permission, sharing, mutation, source-adapter, or queue code.
- Provider cursors are convenience/validation envelopes, not the authorization boundary.
  Every resumed result is revalidated against the selected configured root.
- `/ship` owns the final review/fix/PR flow. Do not stop after writing the review file and do
  not use Cursor Bugbot.

## ClickUp lifecycle

- The design-stage claim is expected to already have set task `868kduv64` to `doing`, added
  tag `agent`, and set Branch custom field
  `407066ea-6c53-41fc-9d9b-c7332886eb82` to
  `feat/2026-07-18-cloud-file-integrations`.
- Before implementation, verify those three values through the ClickUp MCP. Correct only a
  missing or stale claim value; do not create a second branch or ticket.
- After the implementation is complete, all verification is green, every review finding is
  `Fixed` or `Rejected`, and the GitHub PR is open, set the ClickUp status to `review` and
  add a task comment containing the PR URL. Leave tag `agent` in place.

## File map

### Create

- `backend/apps/keys/migrations/0005_rename_gmail_credentials_to_google.py` — generated empty
  migration, then filled with the irreversible metadata-only cutover for both credential
  tables and the default system name.
- `backend/apps/keys/tests/test_migrations.py` — cutover preservation and conflict tests.
- `backend/libs/clients/google_drive/__init__.py` — canonical public exports.
- `backend/libs/clients/google_drive/config.py` — immutable root/config records and strict
  structural validation.
- `backend/libs/clients/google_drive/errors.py` — typed failures matching common tool kinds.
- `backend/libs/clients/google_drive/protocol.py` — structural interface consumed by the
  tool and mock.
- `backend/libs/clients/google_drive/client.py` — Drive auth, metadata operations, retries,
  cursor envelopes, normalization, and ancestry enforcement.
- `backend/libs/clients/google_drive/mock.py` — deterministic in-memory Drive metadata tree.
- `backend/libs/clients/google_drive/tests/__init__.py`
- `backend/libs/clients/google_drive/tests/test_client.py`
- `backend/libs/clients/google_drive/tests/test_mock.py`
- `backend/libs/tools/tools/google_drive.py` — four read-only Drive functions and failure
  mapping.
- `backend/libs/tools/tests/test_google_drive_tool.py`
- `backend/libs/clients/dropbox/__init__.py` — canonical public exports.
- `backend/libs/clients/dropbox/config.py` — immutable root/config records, absolute path
  normalization, and segment-safe containment.
- `backend/libs/clients/dropbox/errors.py` — typed failures matching common tool kinds.
- `backend/libs/clients/dropbox/protocol.py` — structural interface consumed by the tool and
  mock.
- `backend/libs/clients/dropbox/client.py` — refresh-token SDK construction, namespace
  selection, metadata operations, retries, cursor envelopes, normalization, and path
  enforcement.
- `backend/libs/clients/dropbox/mock.py` — deterministic in-memory Dropbox metadata tree.
- `backend/libs/clients/dropbox/tests/__init__.py`
- `backend/libs/clients/dropbox/tests/test_client.py`
- `backend/libs/clients/dropbox/tests/test_mock.py`
- `backend/libs/tools/tools/dropbox.py` — four read-only Dropbox functions and failure
  mapping.
- `backend/libs/tools/tests/test_dropbox_tool.py`
- `backend/libs/agent_spec/examples/cloud-files-browser.yaml` — one manual metadata browser
  using both integrations and explicit roots.
- `examples/local/keys/example-google.yaml` — disk-key shape for complete service-account
  JSON.
- `examples/local/keys/example-dropbox.yaml` — disk-key shape for app key, app secret, and
  offline refresh token JSON.

### Modify

- `backend/apps/keys/types.py` — replace credential type `gmail` with `google`; add
  `dropbox`; emit explicit legacy cutover guidance.
- `backend/apps/keys/credential_guides.py` — shared Google setup/union scopes and Dropbox
  setup.
- `backend/apps/keys/services/disk_sync.py` — safely preserve `KeyValidationError` guidance
  in `SyncItemResult.detail` without logging values.
- `backend/apps/keys/tests/test_types.py`
- `backend/apps/keys/tests/test_credential_guides.py`
- `backend/apps/keys/tests/test_disk_sync.py`
- `backend/apps/keys/tests/test_models.py`
- `backend/apps/keys/tests/test_queries.py`
- `backend/apps/keys/tests/test_commands.py`
- `backend/apps/web/tests/test_keys_page.py`
- `backend/libs/tools/tools/gmail.py` — expect `google`.
- `backend/libs/tools/tests/test_gmail_tool.py`
- `backend/libs/tools/tests/test_token_supplier.py`
- `backend/libs/sources/adapters/gmail.py` — expect `google`.
- `backend/libs/sources/tests/test_gmail_adapter.py`
- `backend/libs/clients/gmail/client.py` — credential diagnostics say Google credential.
- `backend/apps/agents/tools_wiring.py` — register both new tools.
- `backend/apps/agents/tests/test_tool_wiring.py` — shared Google expectation and injected
  cloud clients.
- `backend/pyproject.toml` — add official `dropbox` SDK dependency.
- `uv.lock` — lock the dependency using project tooling.
- `backend/libs/agent_spec/tests/test_examples.py` — validate the cloud browser example.
- `docs/ARCHITECTURE.md` — canonical shared Google credential and cloud client/tool
  descriptions.
- `docs/docs/agents.md` — credential setup, built-in tool contracts, required roots,
  nullable `web_url`, and example catalog.

No agent-spec schema migration is required: `integrations[].config` and `tools[].config`
already accept provider-specific dictionaries, and adding optional tool types is
backward-compatible.

---

## Stage 0 — `/ship` setup and lifecycle verification

### Task 1: Create the isolated implementation context

**Files:**

- Create during implementation:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-revision.md`
- Read:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-design.md`
- Read:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-plan.md`

- [ ] **Step 1: Verify the ClickUp claim**

Use the ClickUp MCP to fetch task `868kduv64` and confirm:

```text
status = doing
tag includes agent
Branch = feat/2026-07-18-cloud-file-integrations
```

If one value is stale, set only that value according to the ClickUp skill.

- [ ] **Step 2: Create the worktree and branch**

Read and follow `superpowers/using-git-worktrees` with `/ship` as the entry command. Create
or reuse an isolated worktree on exactly:

```text
feat/2026-07-18-cloud-file-integrations
```

- [ ] **Step 3: Apply implementation status and create revision file**

In the worktree, apply `Status: **implementing**` through `managing-active`. Create the
revision document from the repository review template. Do not read it again during
implementation.

- [ ] **Step 4: Establish a clean baseline**

Run:

```bash
./olib/scripts/orunr py test apps.keys.tests libs.clients.gmail.tests libs.tools.tests.test_gmail_tool libs.sources.tests.test_gmail_adapter
```

Expected: exit 0 before feature changes.

No implementation commit is made for the baseline alone.

---

## Stage 1 — Canonical Google credential cutover

### Task 2: Replace the Gmail-only credential type and migrate stored rows

**Files:**

- Modify: `backend/apps/keys/types.py`
- Modify: `backend/apps/keys/credential_guides.py`
- Modify: `backend/apps/keys/services/disk_sync.py`
- Generate:
  `backend/apps/keys/migrations/0005_rename_gmail_credentials_to_google.py`
- Create: `backend/apps/keys/tests/test_migrations.py`
- Modify: `backend/apps/keys/tests/test_types.py`
- Modify: `backend/apps/keys/tests/test_credential_guides.py`
- Modify: `backend/apps/keys/tests/test_disk_sync.py`
- Modify: `backend/apps/keys/tests/test_models.py`
- Modify: `backend/apps/keys/tests/test_queries.py`
- Modify: `backend/apps/keys/tests/test_commands.py`
- Modify: `backend/apps/web/tests/test_keys_page.py`
- Modify: `backend/libs/tools/tools/gmail.py`
- Modify: `backend/libs/tools/tests/test_gmail_tool.py`
- Modify: `backend/libs/tools/tests/test_token_supplier.py`
- Modify: `backend/libs/sources/adapters/gmail.py`
- Modify: `backend/libs/sources/tests/test_gmail_adapter.py`
- Modify: `backend/libs/clients/gmail/client.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write failing registry, guide, disk-sync, wiring, and migration tests**

Add tests with these concrete assertions:

```python
def test_google_and_dropbox_are_registered_without_legacy_gmail(self) -> None:
    self.assertTrue(is_registered_type('google'))
    self.assertTrue(is_registered_type('dropbox'))
    self.assertFalse(is_registered_type('gmail'))
    with self.assertRaisesRegex(
        KeyValidationError,
        r"credential type 'gmail' was renamed to 'google'; update type: google",
    ):
        validate_type('gmail')


def test_google_guide_lists_gmail_and_drive_scopes(self) -> None:
    guide = credential_guide('google')
    assert guide is not None
    self.assertIn('gmail.modify', guide.scopes or '')
    self.assertIn('gmail.send', guide.scopes or '')
    self.assertIn('drive.metadata.readonly', guide.scopes or '')


def test_legacy_gmail_disk_key_reports_cutover_guidance(self) -> None:
    self.write_key(type_name='gmail')
    report = sync_keys_dir()
    self.assertEqual(report.failed, 1)
    self.assertIn("renamed to 'google'", report.items[0].detail)
    self.assertFalse(UserCredential.objects.exists())
```

In `test_migrations.py`, import the generated numeric module with `importlib`, seed both
current model tables with encrypted byte sentinels, invoke the forward function through
the Django app registry, and assert:

```python
user_row.refresh_from_db()
system_row.refresh_from_db()
self.assertEqual(user_row.type, 'google')
self.assertEqual(system_row.type, 'google')
self.assertEqual(system_row.name, 'default:google')
self.assertEqual(bytes(user_row.encrypted_value), user_ciphertext)
self.assertEqual(bytes(system_row.encrypted_value), system_ciphertext)
```

Add separate tests proving a pre-existing `default:google` name and two system defaults
that would become `type='google'` raise `RuntimeError` before any row is changed.

Update Gmail tool/source tests to capture the supplier arguments:

```python
resolved: list[tuple[str | None, str]] = []

def supplier_factory(ref: str | None, credential_type: str) -> Callable[[], str | None]:
    resolved.append((ref, credential_type))
    return lambda: '{"type":"service_account"}'

self.assertEqual(resolved, [('gmail-personal', 'google')])
self.assertEqual(GmailSourceAdapter.credential_type, 'google')
```

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
./olib/scripts/orunr py test apps.keys.tests.test_types apps.keys.tests.test_credential_guides apps.keys.tests.test_disk_sync apps.keys.tests.test_migrations apps.web.tests.test_keys_page libs.tools.tests.test_gmail_tool libs.sources.tests.test_gmail_adapter apps.agents.tests.test_tool_wiring
```

Expected: failures because `google`/`dropbox` are not registered, migration `0005` does not
exist, and Gmail consumers still request `gmail`.

- [ ] **Step 3: Implement canonical types, guides, safe legacy detail, and Gmail cutover**

Make the registry shape exactly:

```python
SERVICE_TYPES: frozenset[str] = frozenset(
    {'openai', 'anthropic', 'local_openai', 'google', 'dropbox', 'clickup', 'obsidian'}
)
LLM_SERVICE_TYPES: frozenset[str] = frozenset({'openai', 'anthropic', 'local_openai'})
EXTERNAL_SERVICE_TYPES: frozenset[str] = frozenset({'google', 'dropbox', 'clickup', 'obsidian'})
```

Special-case only the removed type before generic unknown-type handling:

```python
def validate_type(type_name: str) -> str:
    """Validate a credential type and explain the one supported pre-v1 cutover."""
    if type_name == 'gmail':
        raise KeyValidationError(
            "credential type 'gmail' was renamed to 'google'; update type: google"
        )
    if not is_registered_type(type_name):
        raise KeyValidationError(f'unknown credential type: {type_name}')
    return type_name
```

Define Google scopes and guide:

```python
_GOOGLE_SCOPES = (
    'https://www.googleapis.com/auth/gmail.modify,'
    'https://www.googleapis.com/auth/gmail.send,'
    'https://www.googleapis.com/auth/drive.metadata.readonly'
)
```

The Google guide must describe one full service-account JSON value, enabling the Gmail and
Drive APIs as needed, optional Workspace delegation, and authorizing the union of enabled
scopes. The Dropbox guide must describe JSON containing `app_key`, `app_secret`, and
`refresh_token`, externally provisioning the offline token, and requesting only
`files.metadata.read`.

Split the safe validation path in disk sync:

```python
except KeyValidationError as exc:
    logger.error('Credential file validation failed for %s (%s)', source_path, exc)
    return SyncItemResult(source_path=source_path, success=False, detail=str(exc))
except (OSError, UnicodeError, yaml.YAMLError, ValueError, IntegrityError) as exc:
    logger.error('Credential file sync failed for %s (%s)', source_path, type(exc).__name__)
    return SyncItemResult(source_path=source_path, success=False, detail=type(exc).__name__)
```

Do not include parsed YAML, provider response bodies, or credential values in either log.

Set:

```python
class GmailTool(Tool):
    name = 'gmail'
    credential_type = 'google'


class GmailSourceAdapter(SourceAdapter):
    adapter_type = 'gmail'
    credential_type = 'google'
```

Change Gmail client authentication messages from “gmail credential” to “google
service-account credential”. Keep every tool/integration/source type and client-factory key
named `gmail`.

- [ ] **Step 4: Generate and implement the irreversible data migration**

Generate the file, rather than creating migration boilerplate manually:

```bash
./olib/scripts/orunr django manage makemigrations keys --empty --name rename_gmail_credentials_to_google
```

Implement a forward function with historical models:

```python
def rename_gmail_credentials_to_google(apps, schema_editor) -> None:
    """Rename Gmail credential metadata without reading or rewriting ciphertext."""
    del schema_editor
    SystemCredential = apps.get_model('keys', 'SystemCredential')
    UserCredential = apps.get_model('keys', 'UserCredential')

    gmail_defaults = SystemCredential.objects.filter(type='gmail', is_default=True)
    google_defaults = SystemCredential.objects.filter(type='google', is_default=True)
    if gmail_defaults.exists() and google_defaults.exists():
        raise RuntimeError(
            'cannot migrate gmail credential: a google system default already exists'
        )
    if (
        SystemCredential.objects.filter(name='default:gmail').exists()
        and SystemCredential.objects.filter(name='default:google').exists()
    ):
        raise RuntimeError(
            'cannot migrate default:gmail: system credential default:google already exists'
        )

    UserCredential.objects.filter(type='gmail').update(type='google')
    SystemCredential.objects.filter(type='gmail').update(type='google')
    SystemCredential.objects.filter(name='default:gmail').update(name='default:google')
```

Use:

```python
migrations.RunPython(
    rename_gmail_credentials_to_google,
    migrations.RunPython.noop,
)
```

The migration must not call decrypt/encrypt, update `encrypted_value`, or reverse all
`google` rows back to `gmail`.

- [ ] **Step 5: Update existing key tests and run green**

Replace credential-type uses in active key/web tests with `google` where they represent
service-account credentials or type-safe query expectations. Credential names such as
`gmail-personal` may remain because names are operator-chosen. Keep tests that intentionally
pass `gmail` only for the explicit cutover-guidance case.

Run:

```bash
./olib/scripts/orunr py test apps.keys.tests apps.web.tests.test_keys_page libs.clients.gmail.tests libs.tools.tests.test_gmail_tool libs.tools.tests.test_token_supplier libs.sources.tests.test_gmail_adapter apps.agents.tests.test_tool_wiring
```

Expected: exit 0.

- [ ] **Step 6: Commit and sync the credential cutover**

```bash
git add backend/apps/keys backend/apps/web/tests/test_keys_page.py backend/libs/clients/gmail backend/libs/tools/tools/gmail.py backend/libs/tools/tests/test_gmail_tool.py backend/libs/tools/tests/test_token_supplier.py backend/libs/sources/adapters/gmail.py backend/libs/sources/tests/test_gmail_adapter.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "refactor: share Google credentials across integrations"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 2 — Google Drive client foundation

### Task 3: Add strict Drive config, auth, normalization, and typed failures

**Files:**

- Create: `backend/libs/clients/google_drive/__init__.py`
- Create: `backend/libs/clients/google_drive/config.py`
- Create: `backend/libs/clients/google_drive/errors.py`
- Create: `backend/libs/clients/google_drive/protocol.py`
- Create: `backend/libs/clients/google_drive/client.py`
- Create: `backend/libs/clients/google_drive/tests/__init__.py`
- Create: `backend/libs/clients/google_drive/tests/test_client.py`

- [ ] **Step 1: Write failing config/auth/normalization tests**

Define tests around these public records and signatures:

```python
@dataclass(frozen=True, slots=True)
class GoogleDriveRoot:
    id: str
    file_id: str
    corpus: Literal['user', 'drive']
    drive_id: str | None = None


@dataclass(frozen=True, slots=True)
class GoogleDriveConfig:
    subject: str | None
    roots: tuple[GoogleDriveRoot, ...]


def parse_google_drive_config(config: Mapping[str, Any]) -> GoogleDriveConfig:
    """Validate non-secret Drive addressing and required aliased roots."""
```

Test:

- omitted/empty/non-list `roots`;
- malformed entries and duplicate aliases;
- default `corpus='user'`;
- `drive_id` implies `corpus='drive'`;
- drive corpus requires a non-empty `drive_id`;
- delegated and non-delegated service creation;
- exact scope tuple containing only
  `https://www.googleapis.com/auth/drive.metadata.readonly`;
- missing/malformed service-account JSON;
- `max_results` below 1 or above 100;
- normalized folder/file/shortcut metadata;
- nullable `path` and `web_url`;
- no content, export, thumbnail, permission, or download fields.

Use the constructor:

```python
class GoogleDriveClient:
    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
        service_factory: ServiceFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """Create a metadata client without resolving or retaining plaintext credentials."""
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
./olib/scripts/orunr py test libs.clients.google_drive.tests.test_client
```

Expected: import failure because the package does not exist.

- [ ] **Step 3: Implement config and failures**

Create these typed classes in `errors.py`, each with a purpose docstring:

```python
class GoogleDriveError(Exception):
    """Base class for Drive metadata client failures."""


class GoogleDriveAuthError(GoogleDriveError):
    """Credential parsing, authentication, or delegation failed."""


class GoogleDriveForbiddenError(GoogleDriveError):
    """The Google identity lacks permission for the requested item."""


class GoogleDriveOutsideRootError(GoogleDriveError):
    """The current item ancestry does not reach the configured root."""


class GoogleDriveNotFoundError(GoogleDriveError):
    """The requested Drive item is not currently visible."""


class GoogleDriveRateLimitedError(GoogleDriveError):
    """The bounded provider retry was exhausted by a quota response."""


class GoogleDriveInvalidCursorError(GoogleDriveError):
    """The cursor is malformed or bound to a different invocation context."""


class GoogleDriveConfigError(GoogleDriveError):
    """Non-secret Drive integration configuration is invalid."""


class GoogleDriveAPIError(GoogleDriveError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Retain a safe status code without retaining provider response content."""
        super().__init__(message)
        self.status = status
```

Implement `parse_google_drive_config()` exactly as tested. Strip aliases and locator
strings, reject duplicates, and return immutable records. Do not make provider API calls
during structural config parsing.

- [ ] **Step 4: Implement just-in-time Drive service creation and metadata normalization**

Use:

```python
DRIVE_METADATA_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly'
DRIVE_FIELDS = (
    'id,name,mimeType,size,modifiedTime,parents,webViewLink,driveId,shortcutDetails'
)
ServiceFactory = Callable[[str, str | None], Any]
```

The default service factory parses JSON, creates service-account credentials with only
`DRIVE_METADATA_SCOPE`, calls `with_subject(subject)` only when subject is non-empty, and
builds Drive v3 with `cache_discovery=False`.

Add:

```python
def _service(self) -> Any:
    """Resolve the Google credential and build one service for the current operation."""


def _normalize_item(
    self,
    raw: Mapping[str, Any],
    *,
    root_alias: str,
) -> dict[str, Any]:
    """Return the metadata-only cross-provider shape for one Drive item."""
```

Normalize `size` to `int | None`; map folder MIME type to `folder`, shortcut MIME type to
`shortcut`, and everything else to `file`. Set `path=None`; set `web_url` to
`webViewLink` or `None`; keep only `drive_id` and shortcut target MIME type in
`provider_metadata`. Do not expose shortcut target IDs.

- [ ] **Step 5: Implement bounded execution and provider failure mapping**

Use one retry after the initial attempt for 429 and transient 5xx statuses, honoring a
numeric `Retry-After` header when present. Map 401 to `auth`, ordinary 403 to `forbidden`,
Google quota reasons/429 to `rate_limited`, 404 to `not_found`, and remaining failures to
`api`. Authentication, config, root, and cursor failures do not retry.

Do not format `HttpError` response content into user-facing messages or logs. Safe messages
contain operation/resource context and status only.

- [ ] **Step 6: Run green**

Run:

```bash
./olib/scripts/orunr py test libs.clients.google_drive.tests.test_client
```

Expected: exit 0 for config/auth/normalization/failure tests; operation tests added in the
next task may remain absent.

- [ ] **Step 7: Commit and sync the Drive foundation**

```bash
git add backend/libs/clients/google_drive
git commit -m "feat: add Google Drive metadata client foundation"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 3 — Google Drive root-safe operations

### Task 4: Implement Drive listing, lookup, search, cursor binding, and mock

**Files:**

- Modify: `backend/libs/clients/google_drive/client.py`
- Modify: `backend/libs/clients/google_drive/protocol.py`
- Modify: `backend/libs/clients/google_drive/tests/test_client.py`
- Create: `backend/libs/clients/google_drive/mock.py`
- Create: `backend/libs/clients/google_drive/tests/test_mock.py`

- [ ] **Step 1: Write failing operation and authorization tests**

The protocol and real/mock clients must expose:

```python
class GoogleDriveClientProtocol(Protocol):
    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured roots only."""

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one page of direct children beneath an authorized folder."""

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch one item after current ancestry reaches the selected root."""

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Run bounded native search and discard results outside the selected root."""
```

Add tests for:

- `list_roots()` fetching only configured locators;
- `file_id='root'` resolving to the returned canonical ID before ancestry comparison;
- My Drive/shared-item `corpora='user'`;
- Shared Drive `corpora='drive'`, `driveId`, `supportsAllDrives=True`, and
  `includeItemsFromAllDrives=True`;
- direct-child query and deterministic `orderBy='folder,name_natural'`;
- list/search rejection when the selected root resolves to a file;
- arbitrary in-root item accepted only after ancestry walk;
- sibling/outside item rejected;
- item moved outside after an earlier successful result;
- cycle and fixed depth limit rejected as `outside_root`;
- shortcut item serialized but its target never fetched;
- Drive search expression escaping quotes/backslashes;
- every search candidate ancestry-checked;
- out-of-root candidates discarded, allowing fewer than `max_results`;
- at most five provider pages scanned per invocation;
- `kinds=('file',)` excludes folders and shortcuts;
- `kinds=('folder',)` includes only folders;
- invalid kind rejected as `config`;
- cursor instance/root/operation/query/kinds mismatch rejected before an API request;
- resumed pages are revalidated rather than trusted.

- [ ] **Step 2: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.clients.google_drive.tests
```

Expected: failures for missing operations, cursor checks, ancestry checks, and mock.

- [ ] **Step 3: Implement canonical roots and ancestry enforcement**

Use fixed bounds:

```python
_MAX_RESULTS = 100
_MAX_PROVIDER_PAGES = 5
_MAX_ANCESTRY_DEPTH = 100
```

At the start of each public operation, build one service and resolve the selected root with
`files().get(fileId=configured.file_id, fields=DRIVE_FIELDS,
supportsAllDrives=True)`. Store the response `id` as the canonical authorization root for
that operation. This makes the special My Drive locator `root` safe.

Implement:

```python
def _assert_within_root(
    self,
    service: Any,
    *,
    item: Mapping[str, Any],
    canonical_root_id: str,
) -> None:
    """Walk current parents until the canonical root is reached or reject the item."""
```

Accept the canonical root itself. Track visited IDs, stop after 100 edges, stop at a
parentless storage boundary, and never traverse `shortcutDetails.targetId`. Fetch parent
metadata with only `id,parents` and `supportsAllDrives=True`.

- [ ] **Step 4: Implement opaque instance-bound cursors**

Encode URL-safe base64 JSON with no plaintext secret:

```python
{
    'v': 1,
    'instance': self._instance_id,
    'root': root_alias,
    'root_locator': root.file_id,
    'operation': operation,
    'query': query_or_none,
    'kinds': sorted_kinds,
    'provider_cursor': provider_page_token,
}
```

Decode with strict type checks and compare all binding fields supplied by the current call.
Malformed base64/JSON, unsupported versions, or any mismatch raises
`GoogleDriveInvalidCursorError`. A forged matching provider token still cannot bypass
per-result ancestry checks.

- [ ] **Step 5: Implement list, metadata, and bounded native search**

`list_folder()` defaults `folder_ref` to the selected canonical root, verifies the folder
itself is within the root and is a folder, then calls `files.list` with direct-parent query,
bounded page size, selected corpus, all-drive flags, deterministic ordering, and
`DRIVE_FIELDS`.

`get_metadata()` fetches the requested item with `DRIVE_FIELDS`, verifies ancestry, and
returns `{'item': normalized}`.

`search()`:

```python
query_text = _escape_drive_query(query)
drive_query = (
    f"trashed = false and (name contains '{query_text}' "
    f"or fullText contains '{query_text}')"
)
```

Apply provider MIME filters where possible, scan at most five pages, ancestry-check every
candidate, append only authorized candidates until `max_results`, and wrap the remaining
provider token in the bound cursor. Search ranking remains provider-native.

- [ ] **Step 6: Implement the deterministic mock and its protocol test**

Use constructor compatibility:

```python
class MockGoogleDriveClient:
    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
    ) -> None:
        """Create an in-memory metadata tree with the production constructor shape."""

    def seed_item(
        self,
        item_id: str,
        *,
        name: str,
        kind: Literal['file', 'folder', 'shortcut'],
        parent_refs: tuple[str, ...] = (),
        drive_id: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Add or replace metadata so tests can model moves and cycles."""
```

Implement all protocol methods, deterministic alias/name ordering, cursor checks, and the
same current-parent authorization behavior. The mock never returns content and never
follows shortcut targets.

- [ ] **Step 7: Run green**

Run:

```bash
./olib/scripts/orunr py test libs.clients.google_drive.tests
```

Expected: exit 0.

- [ ] **Step 8: Commit and sync Drive operations**

```bash
git add backend/libs/clients/google_drive
git commit -m "feat: enforce Google Drive metadata roots"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 4 — Google Drive tool

### Task 5: Expose four read-only Drive functions and wire injected clients

**Files:**

- Create: `backend/libs/tools/tools/google_drive.py`
- Create: `backend/libs/tools/tests/test_google_drive_tool.py`
- Modify: `backend/apps/agents/tools_wiring.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write failing schema, dispatch, failure, and wiring tests**

Assert:

```python
functions = {fn.name: fn for fn in GoogleDriveTool().functions(ctx)}
self.assertEqual(
    set(functions),
    {'list_roots', 'list_folder', 'get_metadata', 'search'},
)
self.assertTrue(all(fn.readonly for fn in functions.values()))
self.assertEqual(GoogleDriveTool.credential_type, 'google')
```

Assert `root` is required where applicable, `max_results` has default 50/minimum 1/maximum
100, `kinds` items enumerate `file` and `folder`, and cursor/folder/item references are
strings.

Use an injected fake to assert exact dispatch arguments and constructor kwargs:

```python
self.assertEqual(factory_kwargs['instance_id'], 'drive')
self.assertEqual(factory_kwargs['config']['roots'][0]['id'], 'my-drive')
self.assertEqual(resolved, [('work-google', 'google')])
```

Parameterize every client failure class to the common kinds:

```text
auth, forbidden, outside_root, not_found, rate_limited,
invalid_cursor, config, api
```

Assert missing roots return a `config` result without constructing the injected client.

- [ ] **Step 2: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.tools.tests.test_google_drive_tool apps.agents.tests.test_tool_wiring
```

Expected: import/registry failures.

- [ ] **Step 3: Implement the tool contract**

Use:

```python
class GoogleDriveTool(Tool):
    """Expose root-safe Drive metadata operations to an agent."""

    name = 'google_drive'
    credential_type = 'google'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Bind one configured Drive instance to a lazy credential and client."""

    def _dispatch(
        self,
        client: GoogleDriveClientProtocol,
        function: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Route one validated tool function to the client protocol."""

    def functions(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> list[ToolFunction]:
        """Return the matching four-function read-only metadata schema."""
```

Call `parse_google_drive_config()` during bind. If it raises, return a bound invoke that
always returns `_failure(config_failure)`; this preserves uniform tool results even when an
injected factory would otherwise skip production validation.

Resolve the supplier with `token_supplier_for`, select
`ctx.client_factories.get('google_drive') or GoogleDriveClient`, and pass
`token_supplier`, original non-secret `config`, and `instance_id`.

- [ ] **Step 4: Register the tool**

In `wire_tools()` import and register:

```python
register_tool('google_drive', GoogleDriveTool())
```

Add a `build_bound_tools()` regression using `ToolInstance(type='google_drive')`,
non-empty roots, a supplier that records expected type `google`, and an injected mock.

- [ ] **Step 5: Run green**

Run:

```bash
./olib/scripts/orunr py test libs.tools.tests.test_google_drive_tool apps.agents.tests.test_tool_wiring
```

Expected: exit 0.

- [ ] **Step 6: Commit and sync the Drive tool**

```bash
git add backend/libs/tools/tools/google_drive.py backend/libs/tools/tests/test_google_drive_tool.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "feat: expose root-safe Google Drive metadata tools"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 5 — Dropbox dependency and client foundation

### Task 6: Add Dropbox SDK, strict config, refresh auth, namespace, and failures

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `uv.lock`
- Create: `backend/libs/clients/dropbox/__init__.py`
- Create: `backend/libs/clients/dropbox/config.py`
- Create: `backend/libs/clients/dropbox/errors.py`
- Create: `backend/libs/clients/dropbox/protocol.py`
- Create: `backend/libs/clients/dropbox/client.py`
- Create: `backend/libs/clients/dropbox/tests/__init__.py`
- Create: `backend/libs/clients/dropbox/tests/test_client.py`

- [ ] **Step 1: Add the dependency through the project lock workflow**

Add the official package requirement `dropbox` to `backend/pyproject.toml`, then resolve the
current compatible release and lock it:

```bash
./olib/scripts/orun py upgrade
```

Expected: `uv.lock` contains the resolved Dropbox SDK and transitive requirements, and the
environment sync succeeds. Do not invent a package version without resolving it.

- [ ] **Step 2: Write failing config/auth/namespace/failure tests**

Use these public records:

```python
@dataclass(frozen=True, slots=True)
class DropboxRoot:
    id: str
    path: str


@dataclass(frozen=True, slots=True)
class DropboxConfig:
    namespace_id: str | None
    roots: tuple[DropboxRoot, ...]


def normalize_dropbox_path(path: str) -> str:
    """Return one absolute normalized Dropbox path, preserving '/' as root."""


def is_path_within(root_path_lower: str, candidate_path_lower: str) -> bool:
    """Compare normalized case-folded path segments rather than string prefixes."""


def parse_dropbox_config(config: Mapping[str, Any]) -> DropboxConfig:
    """Validate namespace selection and required aliased absolute roots."""
```

Test:

- empty/malformed/duplicate roots;
- rejection of relative paths, `.`/`..` segments, repeated separators, and trailing slash
  except `/`;
- `/Projects2` is not within `/projects`;
- `/Projects/Q3` is within `/projects`;
- credential JSON requires non-empty `app_key`, `app_secret`, and `refresh_token`;
- SDK constructor receives those exact refresh parameters;
- optional namespace calls `with_path_root(PathRoot.namespace_id(namespace_id))` before the
  files API method;
- a fresh SDK object is built per public operation;
- `web_url=None`;
- auth/forbidden/not-found/rate-limit/API SDK failures map without response bodies;
- one bounded retry for provider rate limits and transient transport failures.

Use:

```python
DropboxSDKFactory = Callable[[str], Any]


class DropboxClient:
    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
        sdk_factory: DropboxSDKFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        """Create a metadata client without resolving or retaining Dropbox secrets."""
```

- [ ] **Step 3: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests.test_client
```

Expected: import failure because the package does not exist.

- [ ] **Step 4: Implement config and failures**

Create these Dropbox failure categories:

```python
class DropboxError(Exception):
    """Base class for Dropbox metadata client failures."""


class DropboxAuthError(DropboxError):
    """Refresh credential parsing or authentication failed."""


class DropboxForbiddenError(DropboxError):
    """The Dropbox identity lacks permission for the requested item."""


class DropboxOutsideRootError(DropboxError):
    """The current normalized item path is outside the configured root."""


class DropboxNotFoundError(DropboxError):
    """The requested Dropbox item is not currently visible."""


class DropboxRateLimitedError(DropboxError):
    """The bounded provider retry was exhausted by a rate response."""


class DropboxInvalidCursorError(DropboxError):
    """The cursor is malformed or bound to a different invocation context."""


class DropboxConfigError(DropboxError):
    """Non-secret Dropbox integration configuration is invalid."""


class DropboxAPIError(DropboxError):
    """A remaining Dropbox SDK or transport operation failed."""
```

Implement path normalization with `PurePosixPath`-equivalent explicit segment checks, while
retaining `/` as the configured display root. Compare lowercased path segments:

```python
def is_path_within(root_path_lower: str, candidate_path_lower: str) -> bool:
    """Accept the root itself or descendants with a true segment boundary."""
    root_parts = _path_parts(root_path_lower)
    candidate_parts = _path_parts(candidate_path_lower)
    return candidate_parts[: len(root_parts)] == root_parts
```

- [ ] **Step 5: Implement refresh-token SDK creation and normalization**

The default SDK factory parses credential JSON and constructs:

```python
dropbox.Dropbox(
    oauth2_refresh_token=refresh_token,
    app_key=app_key,
    app_secret=app_secret,
)
```

If `namespace_id` is configured, replace the operation-local client with:

```python
sdk = sdk.with_path_root(dropbox.common.PathRoot.namespace_id(namespace_id))
```

Normalize Dropbox metadata to the common keys. Use provider metadata type to derive
`file`/`folder`, return file size and server-modified timestamp when present, set
`mime_type=None`, derive `parent_refs` from the normalized parent path, preserve
`path_display`, set `web_url=None`, and include only file `rev` in
`provider_metadata`. Never call shared-link, temporary-link, download, thumbnail, preview,
upload, or mutation APIs.

- [ ] **Step 6: Run green for the foundation**

Run:

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests.test_client
```

Expected: exit 0 for config/auth/namespace/normalization/failure coverage.

- [ ] **Step 7: Commit and sync Dropbox dependency/foundation**

```bash
git add backend/pyproject.toml uv.lock backend/libs/clients/dropbox
git commit -m "feat: add Dropbox metadata client foundation"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 6 — Dropbox root-safe operations

### Task 7: Implement Dropbox listing, lookup, search, cursors, and mock

**Files:**

- Modify: `backend/libs/clients/dropbox/client.py`
- Modify: `backend/libs/clients/dropbox/protocol.py`
- Modify: `backend/libs/clients/dropbox/tests/test_client.py`
- Create: `backend/libs/clients/dropbox/mock.py`
- Create: `backend/libs/clients/dropbox/tests/test_mock.py`

- [ ] **Step 1: Write failing operation and authorization tests**

The Dropbox protocol is:

```python
class DropboxClientProtocol(Protocol):
    def list_roots(self) -> dict[str, Any]:
        """Return current metadata for configured Dropbox roots only."""

    def list_folder(
        self,
        *,
        root: str,
        folder_ref: str | None = None,
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """List one page of direct children beneath an authorized folder."""

    def get_metadata(self, *, root: str, item_ref: str) -> dict[str, Any]:
        """Fetch one item after checking its current normalized path."""

    def search(
        self,
        *,
        root: str,
        query: str,
        kinds: tuple[str, ...] = (),
        cursor: str | None = None,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Run bounded native search and discard results outside the selected root."""
```

Test:

- `list_roots()` returns only configured roots after current metadata lookup;
- root `/` is converted to the SDK empty-string path;
- namespace selection precedes all lookup/list/search calls;
- `files_list_folder(selected_path, recursive=False, limit=max_results)`;
- continue cursors use `files_list_folder_continue`;
- `files_search_v2` uses the selected configured path;
- continue cursors use `files_search_continue_v2`;
- every returned `path_lower` passes segment-safe containment;
- sibling-prefix, arbitrary outside ID, and moved-item attempts fail `outside_root`;
- individual file root supports metadata but rejects list/search with `config`;
- `kinds=('file',)` excludes folders, `kinds=('folder',)` includes only folders, omitted
  `kinds` returns both, and any other value fails with `config`;
- max 100 results and five provider pages;
- cursor binds version, instance, root alias/path, operation, query, and kinds;
- cursor mismatch fails before an SDK call;
- resumed result paths are rechecked.

- [ ] **Step 2: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests
```

Expected: failures for missing operations, cursors, path checks, and mock.

- [ ] **Step 3: Implement Dropbox operations and cursor envelopes**

Set `_MAX_RESULTS=100` and `_MAX_PROVIDER_PAGES=5`. Each operation constructs one
namespaced SDK client and re-resolves the selected root with `files_get_metadata`.

Encode:

```python
{
    'v': 1,
    'instance': self._instance_id,
    'root': root_alias,
    'root_locator': root.path,
    'operation': operation,
    'query': query_or_none,
    'kinds': sorted_kinds,
    'provider_cursor': provider_cursor,
}
```

Validate all fields before using the provider cursor. For ID references, call
`files_get_metadata(item_ref)`, then authorize the returned current `path_lower`; never
authorize an ID from previously returned state.

- [ ] **Step 4: Implement the Dropbox mock**

Use constructor compatibility and seed API:

```python
class MockDropboxClient:
    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        instance_id: str,
    ) -> None:
        """Create an in-memory Dropbox metadata namespace."""

    def seed_item(
        self,
        item_id: str,
        *,
        path: str,
        kind: Literal['file', 'folder'],
        size: int | None = None,
        rev: str | None = None,
    ) -> dict[str, Any]:
        """Add or replace an item so tests can model provider-side moves."""
```

Implement all protocol methods, deterministic path/name ordering, pagination, file-root
rules, and current path checks. The mock returns no content and no shared links.

- [ ] **Step 5: Run green**

Run:

```bash
./olib/scripts/orunr py test libs.clients.dropbox.tests
```

Expected: exit 0.

- [ ] **Step 6: Commit and sync Dropbox operations**

```bash
git add backend/libs/clients/dropbox
git commit -m "feat: enforce Dropbox metadata roots"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 7 — Dropbox tool

### Task 8: Expose four read-only Dropbox functions and wire injected clients

**Files:**

- Create: `backend/libs/tools/tools/dropbox.py`
- Create: `backend/libs/tools/tests/test_dropbox_tool.py`
- Modify: `backend/apps/agents/tools_wiring.py`
- Modify: `backend/apps/agents/tests/test_tool_wiring.py`

- [ ] **Step 1: Write failing schema, dispatch, failure, and wiring tests**

Assert the Dropbox function set and readonly flags:

```python
functions = {fn.name: fn for fn in DropboxTool().functions(ctx)}
self.assertEqual(
    set(functions),
    {'list_roots', 'list_folder', 'get_metadata', 'search'},
)
self.assertTrue(all(fn.readonly for fn in functions.values()))
self.assertEqual(DropboxTool.credential_type, 'dropbox')
```

Assert exact JSON schemas, dispatch kwargs, `instance_id`, supplier type, injected factory,
all eight failure kinds, and missing-root `config` result. Specifically, `root` is required
for list/get/search, `folder_ref` and `cursor` are optional strings, `item_ref` is required
for metadata, `query` is required for search, `kinds` enumerates `file`/`folder`, and
`max_results` has default 50, minimum 1, and maximum 100.

- [ ] **Step 2: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.tools.tests.test_dropbox_tool apps.agents.tests.test_tool_wiring
```

Expected: import/registry failures.

- [ ] **Step 3: Implement the Dropbox tool**

Use:

```python
class DropboxTool(Tool):
    """Expose root-safe Dropbox metadata operations to an agent."""

    name = 'dropbox'
    credential_type = 'dropbox'

    def bind(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> Callable[[str, dict[str, Any]], Any]:
        """Bind one configured Dropbox instance to a lazy credential and client."""

    def _dispatch(
        self,
        client: DropboxClientProtocol,
        function: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Route one validated tool function to the client protocol."""

    def functions(
        self,
        ctx: ToolContext,
        instance: ToolInstance | None = None,
    ) -> list[ToolFunction]:
        """Return the matching four-function read-only metadata schema."""
```

Validate config during bind, map structural failures to `config`, resolve through
`token_supplier_for`, and pass `token_supplier`, original config, and instance ID to the
injected/default client.

- [ ] **Step 4: Register and run green**

Add:

```python
register_tool('dropbox', DropboxTool())
```

Run:

```bash
./olib/scripts/orunr py test libs.tools.tests.test_dropbox_tool apps.agents.tests.test_tool_wiring
```

Expected: exit 0.

- [ ] **Step 5: Commit and sync the Dropbox tool**

```bash
git add backend/libs/tools/tools/dropbox.py backend/libs/tools/tests/test_dropbox_tool.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py
git commit -m "feat: expose root-safe Dropbox metadata tools"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 8 — Examples, operator docs, and regressions

### Task 9: Document and validate metadata-only cloud integrations

**Files:**

- Create: `backend/libs/agent_spec/examples/cloud-files-browser.yaml`
- Create: `examples/local/keys/example-google.yaml`
- Create: `examples/local/keys/example-dropbox.yaml`
- Modify: `backend/libs/agent_spec/tests/test_examples.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/docs/agents.md`

- [ ] **Step 1: Write failing example tests**

Add:

```python
def test_cloud_files_browser_example_validates(self) -> None:
    spec = load_example('cloud-files-browser')
    validate_spec_tools(spec)
    self.assertEqual(
        [tool.type for tool in spec.tools],
        ['google_drive', 'dropbox'],
    )
    self.assertEqual(spec.tools[0].config['roots'][0]['id'], 'my-drive')
    self.assertEqual(spec.tools[1].config['roots'][0]['id'], 'projects')
```

Also assert `list_examples()` includes `cloud-files-browser`, existing Gmail/ClickUp
examples still validate, and all four cloud functions are available through ingest.

- [ ] **Step 2: Run red**

Run:

```bash
./olib/scripts/orunr py test libs.agent_spec.tests.test_examples
```

Expected: failure because the example does not exist.

- [ ] **Step 3: Add the cloud browser example**

Create a current-schema manual agent with:

```yaml
# title: Cloud files browser
# description: Metadata-only navigation and search across explicitly approved Drive and Dropbox roots.
schema_version: 4
description: Browse approved cloud metadata without reading file content
llm:
  provider: anthropic
  model: claude-sonnet-4-6
system_prompt: |
  Use only the configured cloud metadata tools. Never claim to have read file contents.
integrations:
  - id: work-google
    type: google_drive
    credential_ref: work-google
    config:
      subject: agent@example.com
      roots:
        - {id: my-drive, file_id: root, corpus: user}
        - {id: company, file_id: shared-drive-root-id, drive_id: shared-drive-id}
  - id: team-dropbox
    type: dropbox
    credential_ref: team-dropbox
    config:
      namespace_id: optional-team-namespace-id
      roots:
        - {id: projects, path: /Projects}
tools:
  - {id: drive, integration: work-google}
  - {id: dropbox, integration: team-dropbox}
triggers:
  - name: manual
    kind: manual
```

The example contains no secret values and no source/queue configuration.

- [ ] **Step 4: Add disk-key examples**

`example-google.yaml` uses `type: google` and a YAML block string containing a complete
service-account JSON object shape. `example-dropbox.yaml` uses `type: dropbox` and a block
string containing JSON keys `app_key`, `app_secret`, and `refresh_token`. Use unmistakable
example values and comments telling operators to replace them; never include working
credentials.

- [ ] **Step 5: Update canonical docs**

In `docs/ARCHITECTURE.md`:

- replace the Gmail-specific credential statement with canonical `type=google`;
- state both Gmail tool and Gmail source consume `google`;
- add Drive and Dropbox client/tool rows;
- document required roots, metadata-only scope, nullable `web_url`, Drive canonical root
  resolution, Dropbox namespace/path checks, and no source adapters for this feature.

In `docs/docs/agents.md`:

- describe `google` and `dropbox` credential JSON shapes;
- retain `gmail` as a tool/integration/source type;
- add both four-function tool tables with every function read-only;
- document root records, defaults, caps, cursor binding, and normalized response fields;
- state Dropbox `web_url` is null unless a future non-metadata feature is approved;
- add `cloud-files-browser.yaml` to the example catalog.

Do not rewrite historical approved specs to retroactively change their terminology.

- [ ] **Step 6: Run example and integration regressions**

Run:

```bash
./olib/scripts/orunr py test libs.agent_spec.tests libs.providers.key.tests libs.clients.gmail.tests libs.clients.clickup.tests libs.tools.tests.test_gmail_tool libs.tools.tests.test_clickup_tool libs.sources.tests.test_gmail_adapter apps.runner.tests.usecases
```

Expected: exit 0.

- [ ] **Step 7: Commit and sync examples/docs**

```bash
git add backend/libs/agent_spec/examples/cloud-files-browser.yaml backend/libs/agent_spec/tests/test_examples.py examples/local/keys/example-google.yaml examples/local/keys/example-dropbox.yaml docs/ARCHITECTURE.md docs/docs/agents.md
git commit -m "docs: add cloud metadata integration setup"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

---

## Stage 9 — Full verification

### Task 10: Prove migrations, typing, lint, security checks, and regressions

**Files:**

- Verify all files listed above; no new production files are introduced in this task.

- [ ] **Step 1: Confirm no ungenerated model changes**

Run:

```bash
./olib/scripts/orunr django manage makemigrations --check --dry-run
```

Expected: exit 0 and no model changes detected.

- [ ] **Step 2: Run focused cloud and credential suites**

Run:

```bash
./olib/scripts/orunr py test apps.keys.tests apps.web.tests.test_keys_page libs.clients.google_drive.tests libs.clients.dropbox.tests libs.tools.tests.test_google_drive_tool libs.tools.tests.test_dropbox_tool libs.tools.tests.test_gmail_tool libs.sources.tests.test_gmail_adapter apps.agents.tests.test_tool_wiring libs.agent_spec.tests
```

Expected: exit 0 with no external provider calls.

- [ ] **Step 3: Run the required full Python gate**

Run:

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0 for lint, mypy, Django tests, and bandit.

- [ ] **Step 4: Audit metadata-only and credential cutover invariants**

Run repository searches and inspect matches to confirm:

```text
No production cloud client calls download/export/preview/thumbnail/shared-link/mutation APIs.
No production cloud response includes content, permission lists, temporary links, or tokens.
No current credential registry/guide/tool/source expects credential type gmail.
Gmail integration/tool/source names remain gmail.
Every google_drive/dropbox ToolFunction is readonly=True.
Every client result path is checked after provider lookup/search/list and after cursor resume.
```

Use the workspace search tool for this audit; do not use shell `grep`.

- [ ] **Step 5: Commit verification-only fixes if required and sync**

If verification required code changes, rerun the affected scoped command and full gate, then
commit one coherent verification fix:

```bash
git add backend/apps/keys backend/apps/web/tests/test_keys_page.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py backend/libs/clients backend/libs/tools backend/libs/sources backend/libs/agent_spec backend/pyproject.toml uv.lock examples/local/keys docs/ARCHITECTURE.md docs/docs/agents.md
git commit -m "fix: satisfy cloud integration verification"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If no files changed, do not create an empty commit.

---

## S_final — Code review, fixes, PR, and ClickUp handoff (mandatory `/ship` owner)

### Task 11: Review the complete branch, resolve findings, and open the PR

> **REQUIRED SKILLS:** Read and follow `superpowers/requesting-code-review`,
> `superpowers/receiving-code-review`, `superpowers/verification-before-completion`, and
> `superpowers/finishing-a-development-branch` with `/ship` as the entry command. Dispatch
> the reviewer using `requesting-code-review/code-reviewer.md`; never use Cursor Bugbot.

**Files:**

- Create:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-review.md`
- Modify as findings are resolved:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-review.md`
- Modify as PR opens:
  `docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-design.md`

- [ ] **Step 1: Reconfirm the final gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0.

- [ ] **Step 2: Capture the review range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Dispatch the code reviewer**

Provide:

```text
DESCRIPTION:
Add metadata-only Google Drive and Dropbox clients/tools with required-root enforcement,
canonical shared Google credentials, mocks, examples, and operator documentation.

PLAN_OR_REQUIREMENTS:
docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-design.md
docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-plan.md

BASE_SHA:
value from Step 2

HEAD_SHA:
value from Step 2
```

The reviewer must specifically inspect required-root authorization, canonical Drive root
resolution, Dropbox segment containment, resumed-page checks, shortcut handling, cursor
instance binding, secret retention/logging, migration conflict behavior, nullable
`web_url`, metadata-only scopes/API methods, and all-readonly schemas.

- [ ] **Step 4: Write the review file**

Create the review document from `review-file-template.md`. Include assessment and one table
per severity with columns:

```text
# | Status | Location | Finding | Notes
```

Initially leave actionable finding statuses empty.

- [ ] **Step 5: Fix or reject every actionable finding**

Under `/ship`, do not stop after reporting. For each real finding:

1. Reproduce or verify it.
2. Apply the smallest design-consistent fix.
3. Add/update a regression test first where behavior changes.
4. Run the scoped test.
5. Set review status to `Fixed`.

Set status to `Rejected` only with a concise technical rationale in Notes. Commit coherent
review fixes, then fetch/rebase/push:

```bash
git add backend/apps/keys backend/apps/web/tests/test_keys_page.py backend/apps/agents/tools_wiring.py backend/apps/agents/tests/test_tool_wiring.py backend/libs/clients backend/libs/tools backend/libs/sources backend/libs/agent_spec backend/pyproject.toml uv.lock examples/local/keys docs/ARCHITECTURE.md docs/docs/agents.md docs/specs/2026-07-18-cloud-file-integrations/2026-07-18-cloud-file-integrations-review.md
git commit -m "fix: address cloud integration review findings"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If Critical or Important findings were fixed, dispatch one additional review over the new
range. Repeat until no open Critical/Important findings remain and every row is `Fixed` or
`Rejected`.

- [ ] **Step 6: Re-verify after review fixes**

```bash
./olib/scripts/orunr django manage makemigrations --check --dry-run
./olib/scripts/orunr py test-all
```

Expected: both exit 0.

- [ ] **Step 7: Open the pull request**

Follow `superpowers/finishing-a-development-branch` `/ship` override: auto-select the Pull
Request path, squash as directed by that skill, push the exact feature branch, and create a
PR summarizing:

```text
- shared canonical google credential cutover for Gmail and Drive
- metadata-only, required-root Google Drive and Dropbox clients/tools
- migration, mocks, examples, setup docs, and full verification
```

The PR test plan includes the migration check and `./olib/scripts/orunr py test-all`. Do not
merge the PR.

- [ ] **Step 8: Apply review status and ClickUp actions**

After the PR exists:

1. Apply design `Status: **review**` through `managing-active`.
2. Use ClickUp MCP to set task `868kduv64` status to `review`.
3. Leave tag `agent`.
4. Confirm Branch remains `feat/2026-07-18-cloud-file-integrations`.
5. Add a ClickUp comment containing the GitHub PR URL.

- [ ] **Step 9: Final handoff**

Report:

```text
plan path
worktree path
branch
review file path
verification commands and exit status
PR URL
ClickUp review status/comment result
```

Do not mark the design `done`; `done` is reserved for a confirmed merge to the default
branch.

---

## Out of scope

- File reads, downloads, exports, previews, parsing, thumbnails, or temporary links.
- Upload, edit, move, delete, sharing, permission, or collaborator operations.
- Provider source adapters, queues, change feeds, webhooks, or recursive tree dumps.
- Interactive OAuth/authorization-code flows.
- Cross-provider search merging.
- A shared cloud-storage client adapter protocol.
- Compatibility support for credential type `gmail`.

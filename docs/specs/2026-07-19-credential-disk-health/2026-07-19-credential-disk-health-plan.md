# Credential disk health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: This plan is entered through `/ship`.
> Use `superpowers/using-git-worktrees`, then `superpowers/subagent-driven-development`
> task-by-task. Create
> `docs/specs/2026-07-19-credential-disk-health/2026-07-19-credential-disk-health-revision.md`
> from the review template before implementation, but do not read it during
> implementation. After all implementation tasks, `/ship` owns mandatory **S_final**:
> request code review, fix or explicitly reject every finding, re-verify, and open the
> PR without pausing at `/plan`, `/impl`, or `/finish` handoffs.

**Goal:** Persist identifiable disk credentials even when the declaration is not yet
usable, store durable health codes on `UserCredential`, gate resolution on
`active` + `ready`, and stop ERROR-logging recoverable validation failures.

**Architecture:** Add `health_status` / `health_code` beside lifecycle `status`. Refactor
disk parse into identity extraction plus declaration validation so recoverable failures
still upsert via `upsert_user_named_from_disk`. Keep disk OAuth YAML as `source: oauth`
only — never accept or alias `auth_kind` from disk. OAuth connect/disconnect and static
create paths keep health consistent; Keys UI shows health labels.

**Tech Stack:** Python 3.13, Django 5.2, PostgreSQL, PyYAML, Jinja templates, olib
`OTestCase` / `OTransactionTestCase`.

**Branch:** `feat/2026-07-19-credential-disk-health`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (scoped tests while iterating)
- **Git:** plan docs commit on `main`; implementation tasks use
  `feat/2026-07-19-credential-disk-health`, and after each stage commit run
  `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method
  you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete
  replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` only — never bare `unittest.TestCase`
- **Django migrations:** generate schema with
  `./olib/scripts/orunr django manage makemigrations keys`; do not hand-write schema
  files. Data backfill may be a second generated/empty migration with `RunPython`.
- **Test naming:** avoid the words error/exception/warning/deprecated in test names
- **Final task:** code review via **`superpowers/requesting-code-review`** (S_final;
  owned by `/ship`)

---

## File map

| File | Responsibility |
|------|----------------|
| `backend/apps/keys/models.py` | `CredentialHealthStatus`, `health_status`, `health_code` |
| `backend/apps/keys/migrations/0007_*.py` | Schema + backfill |
| `backend/libs/providers/key/disk_parse.py` | Identity + validated parse result / health codes |
| `backend/apps/keys/services/commands.py` | Disk upsert + DB create paths set health |
| `backend/apps/keys/services/disk_sync.py` | Persist recoverable failures; quieter logging |
| `backend/apps/keys/services/queries.py` | Metadata + resolve gate on health |
| `backend/apps/keys/oauth/services.py` | Connect/disconnect health updates; reject unhealthy authorize |
| `backend/templates/web/partials/key_list.html` | Health labels |
| `docs/docs/agents.md` | Document health behavior |
| Tests under `apps/keys/tests/`, `libs/providers/key/tests/`, `apps/web/tests/` | Coverage |

---

### Task 1: Model health fields + migration backfill

**Files:**
- Modify: `backend/apps/keys/models.py`
- Create: `backend/apps/keys/migrations/0007_…` (via makemigrations)
- Test: `backend/apps/keys/tests/test_models.py` (extend)

- [ ] **Step 1: Write failing model expectations**

Add tests that `UserCredential` exposes `health_status` default `ready` and blank
`health_code`, and that `CredentialHealthStatus` has `READY` / `NEEDS_ATTENTION`.

- [ ] **Step 2: Implement model fields**

```python
class CredentialHealthStatus(models.TextChoices):
    """Describe whether a credential declaration is usable at resolve time."""

    READY = 'ready', 'Ready'
    NEEDS_ATTENTION = 'needs_attention', 'Needs attention'


# on UserCredential:
health_status = models.CharField(
    max_length=32,
    choices=CredentialHealthStatus.choices,
    default=CredentialHealthStatus.READY,
)
health_code = models.CharField(max_length=64, blank=True, default='')
```

Stable codes (document in module comment or constants near models/commands):
`value_empty`, `oauth_not_connected`, `invalid_declaration`, `unknown_type`.

- [ ] **Step 3: Generate migration and data backfill**

```bash
./olib/scripts/orunr django manage makemigrations keys --name usercredential_health
```

Add a data migration (or RunPython in the same/follow-up migration) that sets:

- OAuth rows with empty `encrypted_value` → `needs_attention` / `oauth_not_connected`
- Static rows with empty `encrypted_value` → `needs_attention` / `value_empty`
- Otherwise leave `ready` / `''`

- [ ] **Step 4: Run focused tests**

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_models.py backend/apps/keys/tests/test_migrations.py -v
```

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/keys/models.py backend/apps/keys/migrations/ backend/apps/keys/tests/
git commit -m "feat(keys): add credential health fields"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: Disk parse returns identity + health outcome

**Files:**
- Modify: `backend/libs/providers/key/disk_parse.py`
- Modify: `backend/libs/providers/key/tests/test_disk_parse.py`

- [ ] **Step 1: Failing tests for recoverable outcomes**

Cover:

1. Valid static empty `value` → success parse with empty value (existing) — keep.
2. OAuth missing `scopes` → recoverable result with `health_code=invalid_declaration`,
   identity name/owner/type filled, no secret retained.
3. Extra key `auth_kind: oauth` (with otherwise oauth-ish or static-ish fields) →
   `invalid_declaration` (never treat as valid oauth/static).
4. Unparseable YAML still raises (unrecoverable).
5. Valid oauth with scopes still fully parses with `auth_kind='oauth'`, `value=None`.

Introduce a result type, e.g.:

```python
@dataclass(frozen=True)
class KeyDiskParseOutcome:
    """Represent one disk file after identity extraction and declaration validation."""

    file: KeyDiskFile | None
    name: str
    type: str
    owner: str
    source_path: str
    source_rev: str
    health_code: str  # '' when usable declaration
```

When `health_code == ''`, `file` is a full `KeyDiskFile`. When non-empty, `file` may be
`None` and callers must not invent auth/secret from invalid YAML. Keep
`parse_key_file` as a thin wrapper that returns `KeyDiskFile` on success or raises on
unrecoverable / soft-failure if callers still need raise semantics — prefer a new
`parse_key_outcome(path, root) -> KeyDiskParseOutcome` used by disk sync.

- [ ] **Step 2: Implement two-stage parse**

1. Read bytes, hash `source_rev`, strict YAML load.
2. Non-mapping / YAML failure → raise (unrecoverable).
3. Extract `owner` (required non-empty string), `name` (explicit or stem), `type`
   (string; may be unknown).
4. Validate declaration:
   - If keys imply oauth (`source` present): require exact field set
     `{name?, type, owner, source, scopes}` with `source == 'oauth'` and valid scopes
     list; else `health_code=invalid_declaration`.
   - Else static: require exact `{name?, type, owner, value}` with string/None value;
     else `invalid_declaration`.
   - Presence of `auth_kind` → always `invalid_declaration` (not in allowed sets).
5. Do not call Django type registry here; leave `unknown_type` to disk_sync/commands.

- [ ] **Step 3: Run parse tests**

```bash
./olib/scripts/orunr py test backend/libs/providers/key/tests/test_disk_parse.py -v
```

Expected: PASS

- [ ] **Step 4: Commit and sync**

```bash
git add backend/libs/providers/key/
git commit -m "feat(keys): parse disk credentials with recoverable health outcomes"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 3: Commands + disk sync persist health

**Files:**
- Modify: `backend/apps/keys/services/commands.py`
- Modify: `backend/apps/keys/services/disk_sync.py`
- Modify: `backend/apps/keys/tests/test_commands.py`
- Modify: `backend/apps/keys/tests/test_disk_sync.py`

- [ ] **Step 1: Failing sync/command tests**

Add/adjust:

1. Empty static value → row `needs_attention` / `value_empty`; no ERROR log.
2. Valid oauth → `oauth_not_connected` when grant empty; `ready` when grant retained.
3. Missing scopes / `auth_kind` present with resolvable owner → row with
   `invalid_declaration`; no ERROR log; sync item counts as succeeded change (or
   explicit success with health — treat as succeeded write, `failed=0`).
4. `type: gmail` or unknown type with valid static shape → `unknown_type` row; store
   declared type string; do not encrypt secret for unknown_type if that would require
   accepting invalid type into ready path — for unknown_type with a static `value`,
   still encrypt so operators can fix type without re-entering secret **only if** the
   YAML is otherwise a valid static shape; if declaration invalid, do not store secret.
5. Unresolvable owner / bad YAML → still failed sync + ERROR; no row.
6. `create_user_oauth` / `upsert_user_named` set health appropriately.
7. Existing tests that expect oauth `ACTIVE` with empty grant: update health assertions.

Extend `upsert_user_named_from_disk` signature:

```python
def upsert_user_named_from_disk(
    ...
    *,
    health_status: str = CredentialHealthStatus.READY,
    health_code: str = '',
    ...
) -> tuple[KeyMetadata, bool]:
```

For unhealthy upserts without a valid secret/auth_config:

- Pass `auth_kind=STATIC`, `secret=''` or keep prior ciphertext when row exists and
  health is `invalid_declaration` / `unknown_type` (design: retain previous ciphertext;
  do not write raw invalid secret text from failed parse).
- Prefer a dedicated path `upsert_disk_health(...)` if branching in the existing
  function becomes unsafe — keep one command if possible.

Skip-equality short-circuit must include health fields (unchanged content + same health
→ `changed=False`).

- [ ] **Step 2: Wire disk_sync**

```python
outcome = parse_key_outcome(path, root=root)
# resolve owner from outcome.owner
# duplicate identity checks unchanged
if outcome.health_code == 'invalid_declaration':
    upsert ... health needs_attention / invalid_declaration
elif not registered type (catch KeyValidationError from validate_type / gmail):
    upsert ... unknown_type
elif outcome.file.auth_kind == 'oauth':
    upsert oauth ... health oauth_not_connected or ready based on grant after upsert
elif empty secret:
    upsert static ... value_empty
else:
    upsert static ... ready
# Do not logger.error for these recoverable writes
```

After oauth upsert, if grant empty set `oauth_not_connected`; if non-empty and health
ready path, `ready`. The command should set this from secret emptiness + auth_kind
when health not overridden.

- [ ] **Step 3: Run tests**

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_disk_sync.py backend/apps/keys/tests/test_commands.py -v
```

Expected: PASS

- [ ] **Step 4: Commit and sync**

```bash
git add backend/apps/keys/services/ backend/apps/keys/tests/
git commit -m "feat(keys): persist disk credential health instead of only logging"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Resolution gate + OAuth connect/disconnect health

**Files:**
- Modify: `backend/apps/keys/services/queries.py`
- Modify: `backend/apps/keys/oauth/services.py`
- Modify: `backend/apps/keys/tests/test_queries.py`
- Modify: `backend/apps/keys/oauth/tests/test_services.py`

- [ ] **Step 1: Failing tests**

1. Active + `value_empty` / `invalid_declaration` / `unknown_type` → resolve raises
   not-found / not-set style failure (no decrypt of unhealthy static even if ciphertext
   present for invalid_declaration retention cases — gate on health first).
2. Active + `oauth_not_connected` → existing `credential not connected`.
3. Complete authorization → health `ready`.
4. Disconnect → `oauth_not_connected`.
5. Authorize start rejected for `invalid_declaration` rows.
6. `KeyMetadata` includes `health_status` / `health_code`.

- [ ] **Step 2: Implement**

In `resolve_secret`, after loading active user row:

```python
if row.health_status != CredentialHealthStatus.READY:
    if row.auth_kind == CredentialAuthKind.OAUTH and row.health_code == 'oauth_not_connected':
        raise KeyNotFoundError(f'credential not connected: {name}')
    raise KeyNotFoundError(f'credential not available: {name}')
```

Update `_user_metadata` fields. On `complete_authorization` save, set ready/clear code.
On `disconnect_authorization`, set needs_attention / oauth_not_connected.
`_validated_oauth_declaration` / `_owned_active_oauth`: require ready **or** allow
authorize when health is exactly `oauth_not_connected` (valid declaration, empty grant).
Reject `invalid_declaration` / `unknown_type`.

- [ ] **Step 3: Run tests**

```bash
./olib/scripts/orunr py test backend/apps/keys/tests/test_queries.py backend/apps/keys/oauth/tests/test_services.py -v
```

Expected: PASS

- [ ] **Step 4: Commit and sync**

```bash
git add backend/apps/keys/services/queries.py backend/apps/keys/oauth/ backend/apps/keys/tests/
git commit -m "feat(keys): gate credential resolve on health status"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 5: Keys UI health labels + docs

**Files:**
- Modify: `backend/templates/web/partials/key_list.html`
- Modify: `backend/apps/web/tests/test_keys_page.py`
- Modify: `docs/docs/agents.md`
- Optionally: `docs/ARCHITECTURE.md` one-line pointer if credentials section needs it

- [ ] **Step 1: Failing UI test**

Assert keys partial shows "Value empty" / "Invalid declaration" / "OAuth not connected"
for corresponding metadata instead of only Set/Not set.

- [ ] **Step 2: Template**

Status column priority:

1. disabled → Disabled
2. `health_status == needs_attention` → map codes to labels
3. else existing Connected / Set paths

Disable Authenticate when health is `invalid_declaration` or `unknown_type`.

- [ ] **Step 3: Docs**

In `docs/docs/agents.md` credentials section: note that identifiable but invalid disk
keys appear in Settings → Keys with a health status; list codes; reaffirm OAuth YAML is
`source: oauth` + `scopes` (no `auth_kind`).

- [ ] **Step 4: Full gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 5: Commit and sync**

```bash
git add backend/templates/web/partials/key_list.html backend/apps/web/tests/test_keys_page.py docs/
git commit -m "feat(keys): show disk credential health in UI"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 6: Code review

> **REQUIRED SKILL:** Under `/ship`, parent runs **`superpowers/requesting-code-review`**,
> writes `*-review.md`, fixes actionable findings, re-verifies, then opens the PR.

- [ ] Confirm `./olib/scripts/orunr py test-all` passes
- [ ] Review range `merge-base(HEAD, origin/main)..HEAD`
- [ ] Write `docs/specs/2026-07-19-credential-disk-health/2026-07-19-credential-disk-health-review.md`
- [ ] Fix or reject every finding; no open Critical/Important before PR

---

## Out of scope

- Accepting disk `auth_kind`
- Auto-rewriting operator YAML
- Separate per-file status table for unparseable paths
- Changing Google OAuth consent UX beyond health field updates

## References

- Design: `docs/specs/2026-07-19-credential-disk-health/2026-07-19-credential-disk-health-design.md`
- `docs/ARCHITECTURE.md` credentials section
- Prior: `docs/specs/2026-07-09-local-disk-providers/`, `docs/specs/2026-07-18-google-oauth/`

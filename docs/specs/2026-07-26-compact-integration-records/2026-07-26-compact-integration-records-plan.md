# Compact Integration Records Implementation Plan

Epic: [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md) · Spec **1 of 3** · Item: **Compact integration records**

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-07-26-compact-integration-records/2026-07-26-compact-integration-records-revision.md` from the review template in `olib/docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Project Gmail and ClickUp provider responses into stable, bounded, LLM-oriented records shared by source adapters and tools, with decoded Gmail bodies/auth signals and bounded ClickUp task/comment content.

**Architecture:** Add pure Django-free projection modules beside each client package, plus a tiny shared bounds helper. Clients stay raw transport (ClickUp gains `list_comments` and `get_task` query flags). Tools and source adapters call the same projection functions so summary field names match. No new agent-config fields; no raw-result escape hatch.

**Tech Stack:** Python 3.13, stdlib `email` / `html.parser` / `base64` / `quopri` (no new deps), existing `GmailClient` / `ClickUpClient` / `GmailTool` / `ClickUpTool` / source adapters, `OTestCase`, `./olib/scripts/orunr`.

**Branch:** `feat/2026-07-26-compact-integration-records`

**Design:** [`2026-07-26-compact-integration-records-design.md`](./2026-07-26-compact-integration-records-design.md)

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate while iterating: scoped `./olib/scripts/orunr py test <module.path>` (see `ai/commands/py-checks.md`)
- Gate after each stage commit: `./olib/scripts/orunr py test-all` when the chunk touches multiple packages; otherwise scoped tests plus a final `./olib/scripts/orunr py test-all` before S_final
- **Git:** plan/design docs on `main` are docs-only; implementation tasks use `feat/2026-07-26-compact-integration-records` from this plan header, and after each stage commit run `git fetch origin main && git rebase origin/main && git push` (stop on rebase conflicts)
- **Function documentation:** every new or materially changed function/method gets a brief purpose docstring (and assumptions where non-obvious) per `AGENTS.md`
- **No compatibility re-exports:** update imports to the new canonical modules; delete replaced local helpers in adapters — no shim files
- **Test bases:** `OTestCase` only here — never bare `unittest.TestCase`
- **Test naming:** avoid parproc keywords `error`, `exception`, `warning`, `notice`, `deprecated`, `deprecation` in test names
- **Libs:** projections and clients stay Django-free; never import `apps.*`; never put credentials, auth headers, or private provider exception bodies in projection output
- **Final task:** code review via **`superpowers/requesting-code-review`** (mandatory **S_final** below)

## Locked shapes (implement exactly)

### Shared truncation / collection meta

```python
{
  'truncated': True,
  'omitted_count': <int>,          # collections
  'omitted_chars': <int>,          # text (when known)
  'included': <int>,               # collections
  'total': <int | None>,           # collections; None if provider total unknown
  'ref': {'service': '...', 'resource_type': '...', 'resource_id': '...'},  # text body only
}
```

Advisories are `{'code': <str>, 'message': <str>}` objects collected under `advisories: list[...]`.

### Gmail summary (source `data` + tool metadata path)

Same field names as full message where present; **omit** `body` rather than invent empty content:

`id`, `thread_id`, `label_ids`, `from`, `to`, `cc`, `reply_to`, `return_path`, `subject`, `message_id`, `date`, `received_at`, `snippet`, `has_attachments`, `attachments`, `attachments_meta`, `authentication`, `advisories`

Optional source-only: when `config.include_body` is true, add `body_preview` (truncated Gmail `snippet`, same 2000-char behavior as today) — not a full `body`.

### Gmail full (`gmail.read`)

Summary fields plus:

```python
'body': {'text': <str>, 'source': 'plain' | 'html_to_text'},
'body_truncation': <meta or omitted when not truncated>,
```

Body limit **32000** chars (prefix retained). Attachments limit **25**.

### Gmail authentication

```python
'authentication': {
  'spf': {'verdict': <str>, 'domain': <str | None>},
  'dkim': [{'verdict': <str>, 'domain': <str | None>}, ...],
  'dmarc': {'verdict': <str>, 'policy': <str | None>, 'header_from': <str | None>},
  'arc': {'verdict': <str | None>},
  'alignment': {
    'from_domain': <str | None>,
    'reply_to_domain': <str | None>,
    'return_path_domain': <str | None>,
    'from_matches_reply_to': <bool | None>,
    'from_matches_return_path': <bool | None>,
  },
}
```

Verdicts use provider terms (`pass`, `fail`, `softfail`, `neutral`, `temperror`, `permerror`) or `unknown`. Never invent a phishing/safe label.

### Gmail mutations / list / labels / attachment

- `list`: keep `{message_ids, next_page_token}` (already compact).
- `list_labels`: `{'labels': [{'id', 'name', 'type'?}, ...]}` — drop colors / visibility noise.
- `get_attachment`: `{'attachment_id', 'size', 'mime_type', 'data_base64'}` where `data_base64` is standard base64 of **already-decoded** bytes from `GmailClient.get_attachment` (never the provider’s raw base64url string).
- Mutations (`label` / `archive` / `mark_spam` / `trash` / `send`): `{'ok': True, 'message_id': <str>, 'label_ids': <list>?}` (include `label_ids` when the client response has them; send may omit labels).

### ClickUp task summary (source `data` + `list_tasks`)

`id`, `custom_id`, `name`, `status`, `assignees`, `priority`, `due_date`, `url`, `date_updated`

Do **not** put `list_id` or `text_content` in the shared summary (agents use `ref` + `get_task` for full content). Source may still know `list_id` from config for polling only.

### ClickUp full task (`get_task`)

Summary fields plus: `description` / `markdown_description` (bounded), `status` (string name), `location` `{list, folder, space}` compact ids/names, `creator`, `watchers`, `mentions`, `tags`, `start_date`, `time_estimate`, `points`, `custom_fields` `[{id, name, type, value}]`, `parent`, `dependencies`, `linked_tasks`, `checklists`, `attachments` (+ meta), `subtasks` (+ meta), `comments` (+ meta), `advisories`.

Bounds: description **32000**; comments newest **10** × **4000** chars; subtasks **25**; attachments **25**.

### ClickUp mutations / spaces / lists

- Spaces/lists: `{spaces|lists: [{id, name, archived, ...minimum parent ref}]}`.
- Create/update/comment/delete: `{'ok': True, 'task_id': <str>, 'url': <str|None>, ...relevant resulting state only}` — never echo full task/workspace/user objects.

### Excluded (allowlist tests must fail if present)

Gmail: `payload`, `raw`, `historyId`, `sizeEstimate`, `internalDate` as opaque bookkeeping if unused, encoded body `data` blobs, full `Received` chains, raw `Authentication-Results` string (only normalized `authentication`), generic header dumps.

ClickUp: `color`, `orderindex` / `ordering`, avatar/profile presentation, permission objects, duplicated hierarchy blobs, custom-field `type_config`, feature flags, workspace settings, transport bookkeeping.

---

## File map

### Create

- `backend/libs/clients/compact.py` — shared text/collection bound helpers + advisory builder
- `backend/libs/clients/gmail/projection.py` — Gmail decode + summary/full/label/attachment/mutation projections
- `backend/libs/clients/gmail/tests/test_projection.py` — MIME, auth, bounds, allowlist
- `backend/libs/clients/gmail/tests/fixtures/` — provider-shaped JSON/MIME fixtures used by projection tests
- `backend/libs/clients/clickup/projection.py` — ClickUp summary/full/list/mutation projections
- `backend/libs/clients/clickup/tests/test_projection.py` — field groups, bounds, comment advisory, allowlist
- `backend/libs/clients/clickup/tests/fixtures/` — provider-shaped task/comment fixtures
- `backend/libs/tools/tests/test_gmail_clickup_projection_contract.py` — same fixtures through source + tool paths

### Modify

- `backend/libs/clients/gmail/client.py` — request auth-relevant `metadataHeaders` on metadata gets used by poll
- `backend/libs/clients/clickup/client.py` — `get_task(..., include_subtasks=, include_markdown_description=)`; add `list_comments(task_id)`
- `backend/libs/clients/clickup/protocol.py` — match new client methods/signatures
- `backend/libs/clients/clickup/mock.py` — support `list_comments` + `get_task` flags; seed comments
- `backend/libs/clients/clickup/tests/test_client.py` / `test_mock.py` — cover new APIs
- `backend/libs/tools/tools/gmail.py` — project every success path; update `get_attachment` description
- `backend/libs/tools/tools/clickup.py` — `get_task` fetches comments + projects; project other success paths
- `backend/libs/sources/adapters/gmail.py` — delete local header/attachment helpers; call `project_message_summary`
- `backend/libs/sources/adapters/clickup.py` — call `project_task_summary`; drop ad-hoc status-only shaping where projection covers it
- `backend/libs/tools/tests/test_gmail_tool.py` / `test_clickup_tool.py` — expect projected shapes / compact acks
- `backend/libs/sources/tests/test_gmail_adapter.py` / `test_clickup_adapter.py` — expect projected summary fields
- `docs/ARCHITECTURE.md` — document projection layer beside client/source/tool
- `docs/docs/agents.md` — update Gmail/ClickUp function descriptions for compact results (no new config fields)

### Do not change

- Credential types, OAuth, `ToolInstance.config` / source config keys, dedupe logic, allow/deny gating, UI
- `GmailClient.get_attachment` decoded-bytes return (projection encodes for LLM at the tool boundary)
- Example YAML configs (no new fields)

---

## Task 1: Shared compact helpers

**Files:**
- Create: `backend/libs/clients/compact.py`
- Test: `backend/libs/clients/gmail/tests/test_projection.py` (initial shared-helper cases live here until ClickUp tests exist; or put shared cases at top of this file)

- [ ] **Step 1: Write failing tests for truncate + bound collection**

```python
from libs.clients.compact import advisory, bound_items, truncate_text
from olib.py.django.test.cases import OTestCase


class TestCompactHelpers(OTestCase):
    def test_truncate_text_reports_omitted_chars_and_ref(self) -> None:
        text, meta = truncate_text('abcdef', limit=3, ref={'service': 'gmail', 'resource_type': 'message', 'resource_id': 'm1'})
        self.assertEqual(text, 'abc')
        self.assertEqual(meta, {
            'truncated': True,
            'omitted_chars': 3,
            'ref': {'service': 'gmail', 'resource_type': 'message', 'resource_id': 'm1'},
        })

    def test_bound_items_reports_counts(self) -> None:
        items, meta = bound_items(list(range(5)), limit=2)
        self.assertEqual(items, [0, 1])
        self.assertEqual(meta, {'truncated': True, 'included': 2, 'total': 5, 'omitted_count': 3})

    def test_advisory_shape(self) -> None:
        self.assertEqual(advisory(code='mime_part', message='unsupported'), {'code': 'mime_part', 'message': 'unsupported'})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestCompactHelpers -v
```

Expected: FAIL with `ModuleNotFoundError` / import failure for `libs.clients.compact`.

- [ ] **Step 3: Implement helpers**

```python
# backend/libs/clients/compact.py
"""Shared truncation, collection bounds, and advisory helpers for integration projections."""

from __future__ import annotations

from typing import Any, TypeVar

T = TypeVar('T')

BODY_CHAR_LIMIT = 32_000
ATTACHMENT_LIMIT = 25
CLICKUP_COMMENT_LIMIT = 10
CLICKUP_COMMENT_CHAR_LIMIT = 4_000
CLICKUP_SUBTASK_LIMIT = 25


def truncate_text(
    text: str,
    *,
    limit: int,
    ref: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Return a prefix-limited string and truncation meta when the input exceeds *limit*."""
    if len(text) <= limit:
        return text, None
    meta: dict[str, Any] = {'truncated': True, 'omitted_chars': len(text) - limit}
    if ref is not None:
        meta['ref'] = ref
    return text[:limit], meta


def bound_items(items: list[T], *, limit: int, total: int | None = None) -> tuple[list[T], dict[str, Any]]:
    """Return at most *limit* items plus included/total/omitted/truncated metadata."""
    resolved_total = len(items) if total is None else total
    included = items[:limit]
    omitted = max(0, resolved_total - len(included))
    return included, {
        'truncated': omitted > 0,
        'included': len(included),
        'total': resolved_total,
        'omitted_count': omitted,
    }


def advisory(*, code: str, message: str) -> dict[str, str]:
    """Build one compact advisory object for LLM-facing projection output."""
    return {'code': code, 'message': message}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestCompactHelpers -v
```

Expected: PASS

- [ ] **Step 5: Commit PR-ready chunk**

```bash
git add backend/libs/clients/compact.py backend/libs/clients/gmail/tests/test_projection.py
git commit -m "$(cat <<'EOF'
feat(clients): add shared compact projection helpers

EOF
)"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

## Task 2: Gmail MIME decode + body extraction

**Files:**
- Modify: `backend/libs/clients/gmail/projection.py` (create)
- Test: `backend/libs/clients/gmail/tests/test_projection.py`
- Fixtures under: `backend/libs/clients/gmail/tests/fixtures/`

- [ ] **Step 1: Write failing MIME fixture tests**

Cover at least: plain text; HTML-only → `html_to_text`; multipart/alternative prefers plain; nested multipart; RFC 2047 subject/from; charset + base64url + quoted-printable; attachment parts listed without body duplication; malformed part yields advisory without discarding siblings.

```python
from libs.clients.gmail.projection import decode_message_content


class TestGmailMimeDecode(OTestCase):
    def test_prefers_plain_over_html_alternative(self) -> None:
        raw = {  # minimal Gmail full message fixture
            'id': 'm1',
            'payload': {
                'mimeType': 'multipart/alternative',
                'parts': [
                    {'mimeType': 'text/plain', 'body': {'data': _b64url('plain body')}},
                    {'mimeType': 'text/html', 'body': {'data': _b64url('<b>html</b>')}},
                ],
            },
        }
        body, attachments, advisories = decode_message_content(raw)
        self.assertEqual(body, {'text': 'plain body', 'source': 'plain'})
        self.assertEqual(attachments, [])
        self.assertEqual(advisories, [])

    def test_html_only_converts_to_text_without_markup(self) -> None:
        raw = {
            'id': 'm2',
            'payload': {'mimeType': 'text/html', 'body': {'data': _b64url('<p>Hi <b>there</b></p>')}},
        }
        body, _, _ = decode_message_content(raw)
        self.assertEqual(body['source'], 'html_to_text')
        self.assertIn('Hi', body['text'])
        self.assertNotIn('<', body['text'])
```

Helper `_b64url` in the test module: `base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')`.

- [ ] **Step 2: Run to verify failure**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailMimeDecode -v
```

Expected: FAIL — `decode_message_content` missing.

- [ ] **Step 3: Implement decode in `projection.py`**

Public function:

```python
def decode_message_content(
    raw: Mapping[str, Any],
) -> tuple[dict[str, str] | None, list[dict[str, Any]], list[dict[str, str]]]:
    """Decode body text and lightweight attachment metadata from a Gmail message resource.

    Prefers non-attachment text/plain; otherwise converts non-attachment text/html to text.
    Records advisories for malformed/unsupported parts without discarding other content.
    """
```

Implementation requirements (stdlib only):

1. Walk `payload` / `parts` recursively; skip returning the provider tree.
2. Decode RFC 2047 later in header projection; for bodies: Gmail body `data` via urlsafe base64, honor `Content-Transfer-Encoding` when present (`base64`, `quoted-printable`, 7bit).
3. Charset from `Content-Type` / part headers; decode with `errors='replace'`.
4. Attachment = non-empty `filename` with `body.attachmentId` (same rule as today’s adapter).
5. HTML → text via `html.parser.HTMLParser` subclass that collects text and inserts spaces/newlines for block tags; strip scripts/styles content entirely; never emit href/src URLs as executable markup.
6. Do not concatenate both plain and html from multipart/alternative.

- [ ] **Step 4: Run to verify pass**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailMimeDecode -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/gmail/projection.py backend/libs/clients/gmail/tests/test_projection.py backend/libs/clients/gmail/tests/fixtures
git commit -m "$(cat <<'EOF'
feat(gmail): decode MIME bodies for compact projections

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 3: Gmail authentication + header projection

**Files:**
- Modify: `backend/libs/clients/gmail/projection.py`
- Test: `backend/libs/clients/gmail/tests/test_projection.py`

- [ ] **Step 1: Write failing auth-signal tests**

```python
class TestGmailAuthSignals(OTestCase):
    def test_parses_spf_dkim_dmarc_arc_and_alignment(self) -> None:
        headers = [
            {'name': 'From', 'value': 'Alice <alice@example.com>'},
            {'name': 'Reply-To', 'value': 'other@phish.example'},
            {'name': 'Return-Path', 'value': '<bounce@mail.example.com>'},
            {'name': 'Authentication-Results', 'value': (
                'mx.google.com; spf=pass smtp.mailfrom=mail.example.com; '
                'dkim=pass header.d=example.com; dmarc=pass action=none header.from=example.com; '
                'arc=pass'
            )},
        ]
        auth = project_authentication(headers)
        self.assertEqual(auth['spf']['verdict'], 'pass')
        self.assertEqual(auth['dkim'][0]['domain'], 'example.com')
        self.assertEqual(auth['dmarc']['verdict'], 'pass')
        self.assertEqual(auth['arc']['verdict'], 'pass')
        self.assertFalse(auth['alignment']['from_matches_reply_to'])

    def test_missing_evidence_is_unknown_not_pass(self) -> None:
        auth = project_authentication([{'name': 'From', 'value': 'a@b.com'}])
        self.assertEqual(auth['spf']['verdict'], 'unknown')
        self.assertEqual(auth['dmarc']['verdict'], 'unknown')
```

Also test RFC 2047 header decode via `project_headers(headers) -> dict`.

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailAuthSignals -v
```

- [ ] **Step 3: Implement `project_headers` + `project_authentication`**

- Decode header values with `email.header.decode_header`.
- Parse `Authentication-Results` / `Received-SPF` / `ARC-Authentication-Results` with small deterministic parsers (split on `;`, read `spf=`, `dkim=`, `dmarc=`, `arc=` tokens and `header.d` / `smtp.mailfrom` / `header.from` / `action=`).
- Multiple DKIM results → list.
- Domains for alignment from mailbox addr-spec (`email.utils.parseaddr`); compare lowercased registrable-ish host strings with exact equality (no PSL dependency).
- Malformed auth header → leave verdicts `unknown` and optionally add advisory `auth_header_malformed`.

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailAuthSignals -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/gmail/projection.py backend/libs/clients/gmail/tests/test_projection.py
git commit -m "$(cat <<'EOF'
feat(gmail): project delivery authentication signals

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 4: Gmail message summary/full projection + bounds

**Files:**
- Modify: `backend/libs/clients/gmail/projection.py`
- Test: `backend/libs/clients/gmail/tests/test_projection.py`

- [ ] **Step 1: Write failing projection tests**

```python
class TestGmailMessageProjection(OTestCase):
    def test_full_message_bounds_body_and_attachments(self) -> None:
        raw = load_fixture('full_message_with_many_parts.json')
        out = project_message_full(raw)
        self.assertEqual(out['id'], raw['id'])
        self.assertEqual(out['thread_id'], raw['threadId'])
        self.assertNotIn('payload', out)
        self.assertNotIn('historyId', out)
        self.assertLessEqual(len(out['body']['text']), 32000)
        self.assertTrue(out['body_truncation']['truncated'])
        self.assertEqual(out['attachments_meta']['included'], 25)
        self.assertGreater(out['attachments_meta']['omitted_count'], 0)

    def test_summary_omits_body_but_keeps_shared_field_names(self) -> None:
        raw = load_fixture('metadata_message.json')
        summary = project_message_summary(raw)
        self.assertNotIn('body', summary)
        self.assertIn('from', summary)
        self.assertIn('authentication', summary)
```

Allowlist assertion helper:

```python
FORBIDDEN = {'payload', 'raw', 'historyId', 'sizeEstimate'}


def assert_no_forbidden(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            self.assertNotIn(key, FORBIDDEN)
            assert_no_forbidden(value)
    elif isinstance(obj, list):
        for item in obj:
            assert_no_forbidden(item)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailMessageProjection -v
```

- [ ] **Step 3: Implement `project_message_summary` / `project_message_full`**

```python
def project_message_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project Gmail metadata/list fields into the shared LLM summary shape."""


def project_message_full(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project a full Gmail message with decoded body, bounds, and auth signals."""
```

Details:

- `to` / `cc` as lists of decoded address strings (split on commas after decode).
- `received_at`: prefer Gmail `internalDate` ms → ISO-8601 UTC when present; else decoded `Date` header string in `date` and mirror into `received_at` when no internalDate.
- Attachments via `bound_items(..., limit=ATTACHMENT_LIMIT)` with fields `attachment_id`, `filename`, `mime_type`, `size`.
- `has_attachments` boolean from total > 0.
- Full path sets `body` + optional `body_truncation` with `ref` `{service:'gmail', resource_type:'message', resource_id: id}`.

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailMessageProjection -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/gmail/projection.py backend/libs/clients/gmail/tests/
git commit -m "$(cat <<'EOF'
feat(gmail): project bounded message summary and full shapes

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 5: Gmail labels, attachment, mutation projections + metadata headers

**Files:**
- Modify: `backend/libs/clients/gmail/projection.py`
- Modify: `backend/libs/clients/gmail/client.py` (`_get_message_with_service` / metadata path)
- Modify: `backend/libs/clients/gmail/tests/test_client.py`
- Test: `backend/libs/clients/gmail/tests/test_projection.py`

- [ ] **Step 1: Write failing tests**

```python
class TestGmailOtherProjections(OTestCase):
    def test_labels_drop_provider_noise(self) -> None:
        labels = project_labels([
            {'id': 'INBOX', 'name': 'INBOX', 'type': 'system', 'color': {'textColor': '#fff'}},
        ])
        self.assertEqual(labels, [{'id': 'INBOX', 'name': 'INBOX', 'type': 'system'}])

    def test_attachment_uses_decoded_bytes_as_standard_base64(self) -> None:
        out = project_attachment({'attachment_id': 'a1', 'size': 5, 'mime_type': 'text/plain', 'data': b'hello'})
        self.assertEqual(out['data_base64'], base64.b64encode(b'hello').decode('ascii'))
        self.assertNotIn('data', out)

    def test_mutation_ack_is_compact(self) -> None:
        out = project_mutation_ack({'id': 'm1', 'labelIds': ['INBOX'], 'threadId': 't', 'historyId': '9'}, message_id='m1')
        self.assertEqual(out, {'ok': True, 'message_id': 'm1', 'label_ids': ['INBOX']})
```

Client test: metadata get passes `metadataHeaders` including `Authentication-Results`, `Received-SPF`, `ARC-Authentication-Results`, `Return-Path`, plus address/subject headers.

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection.TestGmailOtherProjections libs.clients.gmail.tests.test_client -v
```

- [ ] **Step 3: Implement projections + client metadataHeaders**

In `client.py`, when `fmt == 'metadata'`, call:

```python
service.users().messages().get(
    userId='me',
    id=message_id,
    format='metadata',
    metadataHeaders=list(_METADATA_HEADERS),
)
```

Keep `format='full'` unchanged (full payload still returned raw to callers; tools project it).

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.gmail.tests.test_projection libs.clients.gmail.tests.test_client -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/gmail/
git commit -m "$(cat <<'EOF'
feat(gmail): compact labels, attachments, mutations; request auth headers

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 6: Wire Gmail tool + source adapter

**Files:**
- Modify: `backend/libs/tools/tools/gmail.py`
- Modify: `backend/libs/sources/adapters/gmail.py`
- Modify: `backend/libs/tools/tests/test_gmail_tool.py`
- Modify: `backend/libs/sources/tests/test_gmail_adapter.py`

- [ ] **Step 1: Write failing tool/adapter expectations**

Update `_FakeGmailClient.get_message` to return a provider-shaped full message. Assert:

```python
out = invoke('read', {'message_id': 'm1'})
self.assertIn('body', out)
self.assertNotIn('payload', out)

out = invoke('archive', {'message_id': 'm1'})
self.assertEqual(out['ok'], True)
self.assertEqual(out['message_id'], 'm1')
self.assertNotIn('historyId', out)
```

Adapter: after poll, `payload['data']` uses projected summary keys (`from`, `subject`, `attachments`, …) and must **not** contain `payload` MIME trees. Keep `include_body` → `body_preview` behavior by applying truncation to `snippet` in the adapter **after** summary projection (or via optional `include_body_preview=` on summary helper — prefer adapter-local to avoid config in projection).

Delete adapter helpers `_header`, `_parse_to_addresses`, `_walk_payload_parts`, `_attachment_meta`, `_inline_body` once projection covers them.

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.tools.tests.test_gmail_tool libs.sources.tests.test_gmail_adapter -v
```

- [ ] **Step 3: Wire dispatch**

In `GmailTool._dispatch`:

| function | projection |
|----------|------------|
| `list` | pass-through (already compact) |
| `read` | `project_message_full(client.get_message(..., fmt='full'))` |
| `list_labels` | `{'labels': project_labels(client.list_labels())}` |
| `get_attachment` | `project_attachment(client.get_attachment(...))` |
| `label`/`archive`/`mark_spam`/`trash`/`send` | `project_mutation_ack(..., message_id=...)` |

Update ToolFunction description for `get_attachment` to “Download one attachment as decoded base64” (not provider-encoded).

In `GmailSourceAdapter.poll`, replace manual `data = {...}` with `project_message_summary(msg)` (+ optional `body_preview`).

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.tools.tests.test_gmail_tool libs.sources.tests.test_gmail_adapter libs.clients.gmail.tests -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/tools/tools/gmail.py backend/libs/sources/adapters/gmail.py backend/libs/tools/tests/test_gmail_tool.py backend/libs/sources/tests/test_gmail_adapter.py
git commit -m "$(cat <<'EOF'
feat(gmail): serve compact projections from tool and source

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 7: ClickUp client — subtasks + list_comments

**Files:**
- Modify: `backend/libs/clients/clickup/client.py`
- Modify: `backend/libs/clients/clickup/protocol.py`
- Modify: `backend/libs/clients/clickup/mock.py`
- Modify: `backend/libs/clients/clickup/tests/test_client.py`
- Modify: `backend/libs/clients/clickup/tests/test_mock.py`

- [ ] **Step 1: Write failing client tests**

```python
def test_get_task_passes_include_flags(self) -> None:
    # mock transport records query params include_subtasks=true&include_markdown_description=true
    client.get_task('t1', include_subtasks=True, include_markdown_description=True)

def test_list_comments_hits_task_comment_path(self) -> None:
    # GET /task/t1/comment → {'comments': [...]}
    out = client.list_comments('t1')
    self.assertIn('comments', out)
```

Update `ClickUpClientProtocol`:

```python
def get_task(
    self,
    task_id: str,
    *,
    include_subtasks: bool = False,
    include_markdown_description: bool = False,
) -> dict[str, Any]:
    """Fetch one task by id, optionally including subtasks and markdown description."""

def list_comments(self, task_id: str) -> dict[str, Any]:
    """Return one page of raw comments for a task (provider newest-first)."""
```

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.clients.clickup.tests.test_client libs.clients.clickup.tests.test_mock -v
```

- [ ] **Step 3: Implement**

```python
def get_task(
    self,
    task_id: str,
    *,
    include_subtasks: bool = False,
    include_markdown_description: bool = False,
) -> dict[str, Any]:
    """Fetch one task; optionally request subtasks and markdown description."""
    params: dict[str, Any] = {}
    if include_subtasks:
        params['include_subtasks'] = 'true'
    if include_markdown_description:
        params['include_markdown_description'] = 'true'
    return self._request('GET', f'/task/{task_id}', params=params or None)

def list_comments(self, task_id: str) -> dict[str, Any]:
    """List comments for one task (raw provider page)."""
    return self._request('GET', f'/task/{task_id}/comment')
```

Mock: store seeded comments; `list_comments` returns them newest-first; `get_task` may attach `subtasks` when flag set.

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.clickup.tests -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/clickup/
git commit -m "$(cat <<'EOF'
feat(clickup): fetch subtasks flags and list comments

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 8: ClickUp task projection (summary + full)

**Files:**
- Create: `backend/libs/clients/clickup/projection.py`
- Create: `backend/libs/clients/clickup/tests/test_projection.py`
- Create: `backend/libs/clients/clickup/tests/fixtures/`

- [ ] **Step 1: Write failing projection tests**

Cover: status object → string; people `{id, username/name, email?}`; custom fields without `type_config`; parent/deps/links/checklists; attachment meta; subtask summaries; comment newest-10 + per-comment 4000; description 32000; optional `comments_advisory` when comments is `None` due to failure; allowlist excludes `color`, `orderindex`, `type_config`, permission blobs.

```python
def project_task_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project list/poll task rows into the shared ClickUp summary shape."""

def project_task_full(
    raw: Mapping[str, Any],
    *,
    comments: list[Mapping[str, Any]] | None = None,
    comments_advisory: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Project one full task plus optional comment page into the bounded LLM shape."""
```

Person helper:

```python
def project_person(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return stable id/display name/email when present; drop avatars and profile noise."""
```

Invalid custom-field types → `value: None` with field `type: 'unknown'` or omit value; never paste raw unparsed structures as a fallback dump.

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.clients.clickup.tests.test_projection -v
```

- [ ] **Step 3: Implement projection module**

Map provider fields:

| Provider | Projected |
|----------|-----------|
| `id` | `id` |
| `custom_id` | `custom_id` |
| `name` | `name` |
| `status.status` or str | `status` |
| `url` | `url` |
| `text_content` / `description` | `description` (truncate) |
| `markdown_description` | `markdown_description` (truncate) |
| `assignees` / `watchers` / `creator` / `group_assignees` mentions | people helpers |
| `priority` | compact priority (id/priority/null — drop color) |
| `tags` | names (or `{name}` only) |
| `due_date` / `start_date` | pass through epoch strings |
| `time_estimate` / `points` | pass through |
| `custom_fields` | `{id, name, type, value}` |
| `parent` | id string or null |
| `dependencies` / `linked_tasks` | summary id/name/url/status |
| `checklists` | `{id, name, resolved, unresolved, items?}` compact |
| `attachments` | filename, mime/extension, size, url, date, user |
| `subtasks` | summary shape, bound 25 |

Comments: sort by `date` descending if needed; take 10; truncate `comment_text` / text field per comment; include `id`, `date`, `user` person, `text`.

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.clickup.tests.test_projection -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/clickup/projection.py backend/libs/clients/clickup/tests/
git commit -m "$(cat <<'EOF'
feat(clickup): project bounded task summary and full shapes

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 9: ClickUp spaces/lists/mutations projections + tool/source wire-up

**Files:**
- Modify: `backend/libs/clients/clickup/projection.py`
- Modify: `backend/libs/tools/tools/clickup.py`
- Modify: `backend/libs/sources/adapters/clickup.py`
- Modify: `backend/libs/tools/tests/test_clickup_tool.py`
- Modify: `backend/libs/sources/tests/test_clickup_adapter.py`
- Modify: fake clients in those tests to implement `list_comments`

- [ ] **Step 1: Write failing wire-up tests**

```python
# tool get_task
fake.get_task returns rich task; fake.list_comments returns comments
out = invoke('get_task', {'task_id': 't1'})
self.assertIn('comments', out)
self.assertNotIn('orderindex', out)

# optional comment failure
fake.list_comments raises ClickUpAPIError → out still has task fields + advisories entry

# create_task compact ack
out = invoke('create_task', {...})
self.assertEqual(out['ok'], True)
self.assertEqual(out['task_id'], 't9')
self.assertNotIn('creator', out)

# source summary
payload['data'] keys match project_task_summary; no text_content; status string
```

- [ ] **Step 2: Run — expect FAIL**

```bash
./olib/scripts/orunr py test libs.tools.tests.test_clickup_tool libs.sources.tests.test_clickup_adapter -v
```

- [ ] **Step 3: Implement remaining projections + dispatch**

```python
def project_spaces(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return `{spaces: [{id, name, archived}]}` from a ClickUp list-spaces payload."""
    spaces = []
    for space in raw.get('spaces') or []:
        if not isinstance(space, Mapping):
            continue
        spaces.append({
            'id': space.get('id'),
            'name': space.get('name'),
            'archived': bool(space.get('archived', False)),
        })
    return {'spaces': spaces}


def project_lists(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return `{lists: [{id, name, archived, space_id?}]}` from a list-lists payload."""
    lists = []
    for item in raw.get('lists') or []:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, Any] = {
            'id': item.get('id'),
            'name': item.get('name'),
            'archived': bool(item.get('archived', False)),
        }
        space = item.get('space')
        if isinstance(space, Mapping) and space.get('id') is not None:
            entry['space_id'] = space.get('id')
        lists.append(entry)
    return {'lists': lists}


def project_task_list(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project list_tasks response to `{tasks: [summary...], last_page?: bool}`."""
    tasks = [project_task_summary(task) for task in (raw.get('tasks') or []) if isinstance(task, Mapping)]
    out: dict[str, Any] = {'tasks': tasks}
    if 'last_page' in raw:
        out['last_page'] = raw.get('last_page')
    return out


def project_mutation_ack(raw: Mapping[str, Any], *, task_id: str | None = None) -> dict[str, Any]:
    """Compact success acknowledgement for create/update/comment/delete."""
    resolved_id = task_id or raw.get('id') or raw.get('task_id')
    ack: dict[str, Any] = {'ok': True, 'task_id': resolved_id}
    if raw.get('url'):
        ack['url'] = raw['url']
    if raw.get('deleted') is True:
        ack['deleted'] = True
    return ack
```

`ClickUpTool._dispatch` for `get_task`:

```python
task = client.get_task(
    arguments['task_id'],
    include_subtasks=True,
    include_markdown_description=True,
)
comments: list[Any] | None
comments_advisory = None
try:
    comments = list(client.list_comments(arguments['task_id']).get('comments') or [])
except ClickUpError:
    comments = None
    comments_advisory = advisory(code='comments_unavailable', message='comment retrieval failed')
return project_task_full(task, comments=comments, comments_advisory=comments_advisory)
```

Do **not** convert comment failure into `{ok: False}` when the task fetch succeeded.

Source adapter:

```python
envelope = {
    'data': project_task_summary(task),
    'ref': {'service': 'clickup', 'resource_type': 'task', 'resource_id': task_id},
}
```

Remove `_status_name` if unused.

- [ ] **Step 4: Run — expect PASS**

```bash
./olib/scripts/orunr py test libs.clients.clickup.tests libs.tools.tests.test_clickup_tool libs.sources.tests.test_clickup_adapter -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/libs/clients/clickup/projection.py backend/libs/tools/tools/clickup.py backend/libs/sources/adapters/clickup.py backend/libs/tools/tests/test_clickup_tool.py backend/libs/sources/tests/test_clickup_adapter.py backend/libs/clients/clickup/mock.py
git commit -m "$(cat <<'EOF'
feat(clickup): serve compact projections from tool and source

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 10: Cross-path contract + allowlist tests

**Files:**
- Create: `backend/libs/tools/tests/test_gmail_clickup_projection_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
class TestProjectionContracts(OTestCase):
    def test_gmail_source_and_tool_share_summary_field_names(self) -> None:
        raw = load_gmail_fixture('metadata_message.json')
        from_source = project_message_summary(raw)
        # tool metadata path uses the same helper
        from_tool = project_message_summary(raw)
        self.assertEqual(set(from_source) & SUMMARY_KEYS, set(from_tool) & SUMMARY_KEYS)
        for key in ('id', 'thread_id', 'from', 'subject', 'label_ids'):
            self.assertEqual(from_source.get(key), from_tool.get(key))

    def test_clickup_source_and_list_tasks_share_summary_field_names(self) -> None:
        raw = load_clickup_fixture('task_list_item.json')
        summary = project_task_summary(raw)
        for key in ('id', 'custom_id', 'name', 'status', 'assignees', 'priority', 'due_date', 'url', 'date_updated'):
            self.assertIn(key, summary)
        self.assertNotIn('text_content', summary)
        self.assertNotIn('orderindex', summary)

    def test_gmail_forbidden_keys_absent_in_full_projection(self) -> None:
        out = project_message_full(load_gmail_fixture('full_message.json'))
        self._assert_allowlist(out, forbidden={'payload', 'historyId', 'sizeEstimate', 'raw'})

    def test_clickup_forbidden_keys_absent_in_full_projection(self) -> None:
        out = project_task_full(load_clickup_fixture('full_task.json'), comments=[])
        self._assert_allowlist(out, forbidden={'orderindex', 'type_config', 'permission_level'})
```

Also assert mutation acks from both tools remain compact via one integration-style fake invoke each.

- [ ] **Step 2: Run — expect FAIL** until helpers/wiring match (should mostly PASS if Tasks 1–9 done; fix any drift)

```bash
./olib/scripts/orunr py test libs.tools.tests.test_gmail_clickup_projection_contract -v
```

- [ ] **Step 3: Fix any field-name drift discovered** (update projection, not call-site renames)

- [ ] **Step 4: Run full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 5: Commit**

```bash
git add backend/libs/tools/tests/test_gmail_clickup_projection_contract.py backend/libs/clients/
git commit -m "$(cat <<'EOF'
test(integrations): contract and allowlist compact projections

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## Task 11: Docs (ARCHITECTURE + agents.md)

**Files:**
- Modify: `docs/ARCHITECTURE.md` (External integrations)
- Modify: `docs/docs/agents.md` (Gmail / ClickUp function blurbs — **no new config fields**)

- [ ] **Step 1: Update ARCHITECTURE anatomy table**

Extend the three-component description to four logical pieces:

| Layer | Package | Role |
|-------|---------|------|
| Client | `libs/clients/<service>/` | Raw transport |
| Projection | `libs/clients/<service>/projection.py` (+ `libs/clients/compact.py`) | Decode, field selection, bounds, advisories |
| Source adapter | … | Queue `{data, ref}` using summary projection |
| Tool | … | LLM results using the same projections |

Note: clients remain provider-native internally; LLM-facing paths never return raw bookkeeping.

- [ ] **Step 2: Update agents.md**

- Gmail `read`: decoded compact message (not raw MIME).
- Gmail `get_attachment`: decoded content as standard base64 (`data_base64`).
- Gmail mutations: compact acknowledgement.
- ClickUp `get_task`: bounded normalized task including recent comments.
- ClickUp list/mutation functions: compact projected results.
- Explicitly state source queue `data` uses the same summary field names as tool list/metadata paths.

No schema_version bump; no config table changes.

- [ ] **Step 3: Commit**

```bash
git add docs/ARCHITECTURE.md docs/docs/agents.md
git commit -m "$(cat <<'EOF'
docs: describe compact Gmail and ClickUp projections

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 12: Code review

> **REQUIRED SKILL:** Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Review the feature branch against the plan/design. Write findings to **`*-review.md`** (see `review-file-template.md`). Do not fix findings unless the user asks — summarize in chat and in the review file.

**Files:** (review only — no edits unless user requests fixes)

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

Read `superpowers/requesting-code-review` skill. Dispatch reviewer subagent with:

- `{DESCRIPTION}` — Compact Gmail/ClickUp projections shared by sources and tools; ClickUp `list_comments` + subtask flags; docs updates
- `{PLAN_OR_REQUIREMENTS}` — `docs/specs/2026-07-26-compact-integration-records/2026-07-26-compact-integration-records-design.md` and `…-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

1. Write `docs/specs/2026-07-26-compact-integration-records/2026-07-26-compact-integration-records-review.md`.
2. One issue table per severity with columns: `#`, **Status** (empty initially), **Location**, **Finding**, **Notes**.
3. Summarize in chat.

Stop here unless the user asks to fix issues.

- [ ] **Step 5: Track feedback**

When the user requests fixes or rejects findings, update **Status** in `*-review.md` to **Fixed** or **Rejected** (with Notes).

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch` (PR / merge options). Do **not** check epic/spec boxes in `-revision.md` or the epic file unless the user explicitly approves after review.

---

## Out of scope

- Raw-result tool option / diagnostic dumps
- Credential, OAuth, dedupe, allow/deny, or UI changes
- New agent-config / source-config fields or schema migrations
- Google Drive / Dropbox projection changes
- Epic checklist check-off (wait until merge → `done`)

## Spec coverage (self-review)

| Design requirement | Task(s) |
|--------------------|---------|
| Gmail decoded MIME bodies | 2, 4, 6 |
| Auth / alignment signals | 3, 4, 5, 6 |
| ClickUp full bounded task + comments | 7–9 |
| Shared source/tool summary fields | 4, 6, 8–10 |
| Explicit truncation | 1, 4, 8, 10 |
| Optional-section failure (comments) | 9, 10 |
| Excluded provider noise / allowlist | 4, 5, 8, 10 |
| Compact mutation acks | 5, 6, 9, 10 |
| No new config; docs only for semantics | 11 |
| Clients stay raw internally | 5, 7 (projections separate) |

## References

- Design: `docs/specs/2026-07-26-compact-integration-records/2026-07-26-compact-integration-records-design.md`
- Architecture: `docs/ARCHITECTURE.md`
- Existing APIs: `backend/libs/clients/gmail/{client,protocol}.py`, `backend/libs/clients/clickup/{client,protocol}.py`, `backend/libs/tools/tools/{gmail,clickup}.py`, `backend/libs/sources/adapters/{gmail,clickup}.py`
- Checks: `ai/commands/py-checks.md`, `AGENTS.md`, `AGENTS.local.md`

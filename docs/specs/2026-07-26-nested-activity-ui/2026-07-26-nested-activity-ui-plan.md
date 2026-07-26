# Nested Activity UI Implementation Plan

Epic: [Agent Context and Activity Clarity](../../epics/2026-07-26-agent-context-activity-clarity.md) · Spec **3 of 3** · Item: **Nested activity UI**

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-07-26-nested-activity-ui/2026-07-26-nested-activity-ui-revision.md` from the review template in `olib/docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Render the hierarchical activity model as a compact recursive session-page tree with collapsed execution rows, on-demand details/raw JSON, and separately authorized live child-session stores.

**Architecture:** Keep Jinja + Alpine session pages. Split the large inline `sessionView` controller so a focused static `activity_tree` module owns per-session stores (indexing, revisions, expansion, child loading, SSE cleanup). Add a thin `apps.web` JSON snapshot endpoint that authenticates, calls ownership + sessions service queries from spec 2, and returns session metadata plus activities. Adapt the existing session SSE client to apply activity upserts by revision. Do not persist activities, invent sub-agent tools, or change rich-content parsing.

**Tech Stack:** Django/Jinja, Alpine.js, EventSource SSE, Vitest/jsdom (+ Vitest browser/Playwright for interaction smoke), existing `chiefRichContentLifecycle` / rich-content bundle, olib `OTestCase` / `OTransactionTestCase`.

**Branch:** `feat/2026-07-26-nested-activity-ui`

---

## Hard dependency (do not skip)

**Spec 2 must be merged to `origin/main` before creating this feature worktree or writing implementation code.**

Verify on `main`:

```bash
git fetch origin main
git log -1 --oneline origin/main -- docs/specs/2026-07-26-hierarchical-session-activities/
# Confirm AgentSessionActivity (or the merged canonical model), parent_session,
# activity upsert SSE, and sessions activity query helpers exist under backend/apps/sessions/
```

If those APIs are missing, stop. Do not stub a parallel persistence layer in this spec.

## Consumed backend contract (spec 2 design only)

Lock the UI to these approved shapes. After merge, bind to the **canonical serializers/helpers** shipped by spec 2 (names may match or be close — update imports to the real canonical paths; no re-export shims).

### Activity row (stream / snapshot item)

Fields from `AgentSessionActivity` in
`docs/specs/2026-07-26-hierarchical-session-activities/2026-07-26-hierarchical-session-activities-design.md`:

| Field | UI use |
|-------|--------|
| `id` | Stable identity; expansion key |
| `session_id` | Store key; reject cross-session patches |
| `parent_id` | Tree edge; unresolved bucket key when parent missing |
| `seq` | Immutable sibling order |
| `revision` | Idempotent upserts (apply only when newer) |
| `kind` | `input` \| `output` \| `tool` \| `llm` \| `span` \| `status` \| `subagent` \| `failure` \| `restart` |
| `status` | `pending` \| `running` \| `succeeded` \| `failed` \| `cancelled` |
| `name` | Collapsed operation name |
| `summary` | Curated one-line segment (omit when empty) |
| `details` | Curated expand panel + raw-JSON disclosure (already raw-safe) |
| `model`, usage, `cost_usd`, `latency_ms` | LLM stats / duration / header totals |
| `started_at`, `ended_at`, `created_at` | Timing / duration |
| `child_session_id` | Sub-agent expand → separate child snapshot/SSE |

Kinds and statuses are lowercase in the new model (unlike today’s uppercase `INPUT`/`OUTPUT` events).

### SSE upsert envelope

Spec 2: session-scoped SSE gains activity upsert envelopes with `operation: "upsert"` and the full current activity representation (id, parent_id, session_id, seq, revision, …). Parent streams receive **sub-agent reference status patches only**, never child activities.

After merge, read the exact Redis/`event:` label and dict helper (likely near `apps.sessions.notify` / model `to_stream_dict`). Client rules:

- Apply when `operation === 'upsert'` (or the merged equivalent discriminator).
- Keep highest `revision` per activity id.
- Reject patches that change `parent_id` after create → refresh that session’s snapshot.
- Continue handling `session_update` for `{name}` (and session status if spec 2 publishes it there).

### Session ancestry / breadcrumbs

`AgentSession.parent_session_id` is null for roots; children point at their direct parent. Same-user ownership is enforced at create (spec 2) and again at read. Snapshot includes enough parent display fields for a breadcrumb link (id + display name at minimum).

### What this plan may add (spec 3 only)

- HTTP **activity snapshot** JSON endpoint under `apps.web` (view → service query → JSON).
- Client store, recursive compact-tree markup/CSS, expansion/subscription lifecycle.
- Template wiring for breadcrumbs, Follow/Beautify/cost semantics over the new model.

### What this plan must not invent

- New activity kinds/fields, persistence, migration, runner recorder, or sub-agent start tool.
- Parent endpoints that return another session’s activities by shortcut.
- Distinct HTTP bodies for not-found vs unauthorized children (both surface as unavailable).

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped tests while iterating; before each PR-ready commit run
  `./olib/scripts/orunr py test-all`, `./olib/scripts/orunr js test-unit`,
  `./olib/scripts/orunr js lint`, and `./olib/scripts/orunr js tsc`
- **Git:** plan/design docs only on `main`; implementation uses
  `feat/2026-07-26-nested-activity-ui`. After each stage commit:
  `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** brief docstring/comment on every new or materially changed
  function/method per `AGENTS.md`
- **No compatibility re-exports:** update imports to canonical modules; delete replaced files
- **Test bases:** `OTestCase` / `OTransactionTestCase` / `OLiveServerTestCase` only
- **Test naming:** avoid `exception`, `error`, `warning`, `notice`, `deprecated`,
  `deprecation` in Python and Vitest titles
- **Layers:** `apps.web` views do not query ORM; call `apps.web.services.queries` /
  `apps.sessions.services.queries` only
- **Final task:** **S_final** via `superpowers/requesting-code-review`

Focused JS iteration (unit config only):

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

Focused Django:

```bash
./olib/scripts/orunr py test apps.web.tests.test_activity_snapshot apps.web.tests.test_sse apps.web.tests.test_session_dialog
```

---

## File map

| Path | Responsibility |
|------|----------------|
| `backend/apps/web/static/web/activity_tree.js` | Per-session store factory: index, upsert, expansion, child registry, fetch/SSE lifecycle, depth helpers, formatting |
| `backend/apps/web/static/web/activity_tree.test.js` | Vitest/jsdom unit coverage for store + lifecycle |
| `backend/apps/web/static/web/activity_tree.browser.test.js` | Chromium smoke: keyboard expand, focus stability, depth marker |
| `backend/apps/web/static/web/vitest.config.js` | Include `activity_tree.test.js` (and keep `rich_content.test.js`) |
| `backend/apps/web/static/web/vitest.browser.config.js` | Include `activity_tree.browser.test.js` beside rich-content browser tests |
| `backend/templates/web/session_detail.html` | Compact tree markup; slim `sessionView` (page lifecycle, Follow, Beautify, chat, cost header) |
| `backend/templates/web/partials/agent_frame_styles.html` | Compact-tree, connector, depth-cap, raw-JSON, a11y focus styles |
| `backend/templates/web/macros/session.html` | Optional breadcrumb helper if useful |
| `backend/apps/web/views.py` | `session_activity_snapshot` JSON view; adapt `session_events_sse` client contract only as needed for upsert event name from spec 2 |
| `backend/apps/web/urls.py` | `sessions/<uuid>/activities/` → snapshot |
| `backend/apps/web/services/queries.py` | Thin helpers composing ownership + sessions activity queries into snapshot DTO (no ORM activity scans in the view) |
| `backend/apps/web/tests/test_activity_snapshot.py` | Auth/ownership, field stability, child separation, unavailable equivalence |
| `backend/apps/web/tests/test_sse.py` | Upsert SSE ownership + parent/child stream separation regressions |
| `backend/apps/web/tests/test_session_dialog.py` | Template contracts: tree assets, breadcrumbs, Beautify/Follow, no flat `events` list |
| `docs/specs/2026-07-26-nested-activity-ui/*-revision.md` | Human review scaffold (create at impl start) |
| `docs/specs/2026-07-26-nested-activity-ui/*-review.md` | Agent review from S_final |

Do **not** modify `rich_content.js` parsing. Reuse `rich_content_lifecycle.js` for OUTPUT (and lowercase `output`) rendering.

---

### Task 0: Prerequisite lock + worktree gate

**Files:** none (verification only)

- [ ] **Step 1: Confirm spec 2 is on `origin/main`**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main origin/main
# Inspect merged APIs (adjust names to what actually landed):
rg -n "AgentSessionActivity|parent_session|operation.*upsert|activities_for|activity_snapshot" backend/apps/sessions
```

Expected: activity model + serializer, parent FK, upsert publish path, and a sessions query that returns ordered activities (and parent breadcrumb data) for an owned session.

- [ ] **Step 2: Record the exact merged symbols in the work notes**

Write the concrete imports you will use into the first implementation commit message body or a short comment at the top of `activity_tree.js` / snapshot view, for example:

- Activity dict helper: `AgentSessionActivity.to_stream_dict` (or merged name)
- Ordered activities: `apps.sessions.services.queries.…`
- SSE event label: whatever `session_events_sse` yields after spec 2

If anything required by the design is missing, stop and escalate — do not invent it here.

- [ ] **Step 3: Only then create the worktree**

`/impl` runs `superpowers/using-git-worktrees` on `feat/2026-07-26-nested-activity-ui` from a `main` that already contains spec 2. Create the empty revision scaffold from the superpowers template. Apply design status `implementing` via `managing-active` at impl start (not during this planning session).

---

### Task 1: Activity store — indexing and revision upserts

**Files:**
- Create: `backend/apps/web/static/web/activity_tree.js`
- Create: `backend/apps/web/static/web/activity_tree.test.js`
- Modify: `backend/apps/web/static/web/vitest.config.js`

- [ ] **Step 1: Extend the unit Vitest include**

```js
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['rich_content.test.js', 'activity_tree.test.js'],
  },
});
```

- [ ] **Step 2: Write failing store tests**

```js
import { describe, expect, test } from 'vitest';
import './activity_tree.js';

const { createActivityStore } = window.chiefActivityTree;

const act = (over = {}) => ({
  id: 'a1',
  session_id: 's1',
  parent_id: null,
  seq: 1,
  revision: 1,
  kind: 'tool',
  status: 'running',
  name: 'clickup.get_task',
  summary: 'Task CU-184',
  details: { args: { task_id: 'CU-184' } },
  model: null,
  cost_usd: null,
  latency_ms: null,
  child_session_id: null,
  ...over,
});

describe('activity store indexing', () => {
  test('indexes snapshot roots and children by seq', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'root', seq: 1, kind: 'input', status: 'succeeded', name: null, summary: null }),
        act({ id: 'child', seq: 2, parent_id: 'root', kind: 'tool', status: 'succeeded', latency_ms: 420 }),
      ],
    });
    expect(store.rootIds).toEqual(['root']);
    expect(store.childIds['root']).toEqual(['child']);
    expect(store.activities.root.revision).toBe(1);
  });

  test('applies newer revisions in place and ignores stale ones', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 't', revision: 1, status: 'running' }));
    store.applyUpsert(act({ id: 't', revision: 2, status: 'succeeded', latency_ms: 10 }));
    store.applyUpsert(act({ id: 't', revision: 1, status: 'running' }));
    expect(store.activities.t.status).toBe('succeeded');
    expect(store.activities.t.revision).toBe(2);
  });

  test('buffers unresolved children until the parent arrives', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'c', parent_id: 'missing', seq: 2 }));
    expect(store.rootIds).toEqual([]);
    expect(store.unresolvedChildIds.missing).toEqual(['c']);
    store.applyUpsert(act({ id: 'missing', parent_id: null, seq: 1, kind: 'span', name: 'outer' }));
    expect(store.childIds.missing).toEqual(['c']);
    expect(store.unresolvedChildIds.missing).toBeUndefined();
  });

  test('rejects parent_id changes after create and requests refresh', () => {
    const store = createActivityStore('s1');
    const refresh = [];
    store.onNeedsRefresh = () => refresh.push(true);
    store.applyUpsert(act({ id: 't', parent_id: null, revision: 1 }));
    store.applyUpsert(act({ id: 't', parent_id: 'other', revision: 2 }));
    expect(store.activities.t.parent_id).toBeNull();
    expect(refresh).toEqual([true]);
  });
});
```

- [ ] **Step 3: Run unit tests — expect fail**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

Expected: FAIL (`chiefActivityTree` / `createActivityStore` undefined).

- [ ] **Step 4: Minimal store implementation**

Implement a classic IIFE in `activity_tree.js` that assigns `window.chiefActivityTree` with at least:

```js
/**
 * Build a per-session activity store. Session and activity IDs key all state;
 * child sessions never merge into this map.
 */
function createActivityStore(sessionId) {
  const store = {
    sessionId,
    session: null,
    activities: Object.create(null),
    rootIds: [],
    childIds: Object.create(null),
    unresolvedChildIds: Object.create(null),
    expandedIds: Object.create(null),
    manualExpandIds: Object.create(null),
    manualCollapseIds: Object.create(null),
    onNeedsRefresh: null,
    applySnapshot(snapshot) { /* replace maps; reindex; preserve expansion keys */ },
    applyUpsert(activity) { /* revision gate; parent_id immutability; reindex */ },
    // …later methods filled in subsequent tasks
  };
  return store;
}
```

Rules:

- Sort sibling ids by ascending `seq`.
- After authoritative snapshot, any remaining unresolved ids become a synthetic root group flagged `unresolved: true` (compact “Unresolved activity” UI in Task 5) — do not drop them.
- Docstrings on every exported function.

- [ ] **Step 5: Re-run — expect pass**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

Expected: PASS for Task 1 tests.

- [ ] **Step 6: Commit PR-ready chunk**

```bash
git add backend/apps/web/static/web/activity_tree.js \
  backend/apps/web/static/web/activity_tree.test.js \
  backend/apps/web/static/web/vitest.config.js
git commit -m "$(cat <<'EOF'
feat: add session activity store indexing and revision upserts

EOF
)"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: Default expansion rules and manual intent

**Files:**
- Modify: `backend/apps/web/static/web/activity_tree.js`
- Modify: `backend/apps/web/static/web/activity_tree.test.js`

- [ ] **Step 1: Failing expansion tests**

```js
describe('activity expansion defaults', () => {
  test('collapses tools and completed subagents; expands running subagents', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'tool', kind: 'tool', status: 'succeeded' }),
        act({ id: 'sub-done', kind: 'subagent', status: 'succeeded', child_session_id: 'c1' }),
        act({ id: 'sub-run', kind: 'subagent', status: 'running', child_session_id: 'c2' }),
      ],
    });
    expect(store.isExpanded('tool')).toBe(false);
    expect(store.isExpanded('sub-done')).toBe(false);
    expect(store.isExpanded('sub-run')).toBe(true);
  });

  test('manual collapse of running subagent survives status patches', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'c' }));
    store.setExpanded('sub', false, { manual: true });
    store.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', revision: 2, child_session_id: 'c' }));
    expect(store.isExpanded('sub')).toBe(false);
  });

  test('manual expand survives terminal status patches', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'running' }));
    store.setExpanded('tool', true, { manual: true });
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'succeeded', revision: 2 }));
    expect(store.isExpanded('tool')).toBe(true);
  });

  test('new running subagent auto-expands unless an ancestor is manually collapsed', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'span', kind: 'span', status: 'running', name: 'outer' }));
    store.setExpanded('span', false, { manual: true });
    store.applyUpsert(act({
      id: 'sub', parent_id: 'span', kind: 'subagent', status: 'running', child_session_id: 'c', seq: 2,
    }));
    expect(store.isExpanded('sub')).toBe(false);
  });
});
```

- [ ] **Step 2: Run — expect fail**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

Expected: FAIL on missing `isExpanded` / `setExpanded` behavior.

- [ ] **Step 3: Implement expansion helpers**

- Default expanded: `kind === 'subagent' && status === 'running'` and no manually collapsed ancestor.
- Messages (`input`/`output`) are always shown as content rows (not collapse targets for body visibility); execution kinds use the disclosure button.
- Page-lifetime only — no `localStorage` / `sessionStorage`.

- [ ] **Step 4: Run — expect pass**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/web/static/web/activity_tree.js backend/apps/web/static/web/activity_tree.test.js
git commit -m "$(cat <<'EOF'
feat: encode activity-tree default and manual expansion rules

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 3: Child-session registry, snapshot fetch, and SSE lifecycle

**Files:**
- Modify: `backend/apps/web/static/web/activity_tree.js`
- Modify: `backend/apps/web/static/web/activity_tree.test.js`

- [ ] **Step 1: Failing lifecycle tests** (mock `fetch` + `EventSource`)

```js
describe('child session loading', () => {
  test('expanding a subagent loads a separate child store from snapshot URL', async () => {
    const fetches = [];
    window.fetch = async (url) => {
      fetches.push(String(url));
      return {
        ok: true,
        json: async () => ({
          session: { id: 'child', status: 'running', name: 'nested', parent_session_id: 's1', parent: { id: 's1', name: 'root' } },
          activities: [act({ id: 'in', session_id: 'child', kind: 'input', status: 'succeeded' })],
        }),
      };
    };
    const root = createActivityStore('s1');
    root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child' }));
    await root.ensureChildLoaded('sub');
    expect(fetches[0]).toContain('/sessions/child/activities/');
    expect(root.childStores.child.rootIds).toEqual(['in']);
    expect(root.activities).not.toHaveProperty('in');
  });

  test('opens child SSE only while expanded and non-terminal; collapse closes it', async () => {
    const sources = [];
    class FakeEventSource {
      constructor(url) { this.url = url; this.listeners = {}; sources.push(this); }
      addEventListener(type, fn) { this.listeners[type] = fn; }
      close() { this.closed = true; }
    }
    window.EventSource = FakeEventSource;
    window.fetch = async () => ({
      ok: true,
      json: async () => ({
        session: { id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null },
        activities: [],
      }),
    });
    const root = createActivityStore('s1');
    root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child' }));
    await root.ensureChildLoaded('sub');
    expect(sources[0].url).toContain('/sessions/child/events/');
    root.setExpanded('sub', false, { manual: true });
    await root.syncChildSubscription('sub');
    expect(sources[0].closed).toBe(true);
  });

  test('terminal child applies final state and closes stream', async () => {
    const sources = [];
    class FakeEventSource {
      constructor(url) { this.url = url; this.listeners = {}; sources.push(this); }
      addEventListener(type, fn) { this.listeners[type] = fn; }
      close() { this.closed = true; }
    }
    window.EventSource = FakeEventSource;
    window.fetch = async () => ({
      ok: true,
      json: async () => ({
        session: { id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null },
        activities: [],
      }),
    });
    const root = createActivityStore('s1');
    root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child' }));
    await root.ensureChildLoaded('sub');
    root.childStores.child.applySessionUpdate({ status: 'done' });
    await root.syncChildSubscription('sub');
    expect(sources[0].closed).toBe(true);
    expect(root.childStores.child.session.status).toBe('done');
  });

  test('unavailable child snapshot sets local unavailable flag without failing the parent store', async () => {
    window.fetch = async () => ({ ok: false, status: 404 });
    const root = createActivityStore('s1');
    root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'succeeded', child_session_id: 'gone' }));
    await root.ensureChildLoaded('sub');
    expect(root.childLoadState.gone).toEqual({ status: 'unavailable' });
    expect(root.rootIds.length).toBe(1);
  });

  test('visited-session guard refuses cyclic child expansion', async () => {
    const root = createActivityStore('s1', { ancestryPath: ['s1'] });
    root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 's1' }));
    await root.ensureChildLoaded('sub');
    expect(root.childLoadState.s1.status).toBe('cycle');
  });
});
```

- [ ] **Step 2: Run — expect fail**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

- [ ] **Step 3: Implement lifecycle API**

Public methods on the store / manager:

- `ensureChildLoaded(activityId)` — GET `/sessions/${child_session_id}/activities/`; on non-OK → `unavailable` (do not branch on 403 vs 404).
- `syncChildSubscription(activityId)` — at most one `EventSource` per expanded running child; close on collapse, terminal, `dispose()`, or stream failure after bounded backoff (`maxAttempts` small, e.g. 5, delay capped).
- `dispose()` / pagehide hook — close all child and root sources.
- `refreshExpandedSnapshots()` — BFCache path: re-fetch root + each still-expanded child, then reconnect.
- Re-expand after prior close always re-fetches snapshot before SSE.
- Keep reconnect backoff **per session id**.

Constants:

```js
const SNAPSHOT_PATH = (id) => `/sessions/${id}/activities/`;
const EVENTS_PATH = (id) => `/sessions/${id}/events/`;
const MAX_VISUAL_DEPTH = 6;
```

- [ ] **Step 4: Run — expect pass**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/web/static/web/activity_tree.js backend/apps/web/static/web/activity_tree.test.js
git commit -m "$(cat <<'EOF'
feat: load nested child activity stores with scoped SSE lifecycle

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Formatting helpers (collapsed row, details, raw JSON, depth)

**Files:**
- Modify: `backend/apps/web/static/web/activity_tree.js`
- Modify: `backend/apps/web/static/web/activity_tree.test.js`

- [ ] **Step 1: Failing formatter tests**

```js
describe('activity row formatting', () => {
  test('builds collapsed tool line without dumping arguments', () => {
    const { formatCollapsedLine } = window.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'tool', name: 'clickup.get_task', summary: 'Task CU-184',
      status: 'succeeded', latency_ms: 420,
    }))).toBe('TOOL · clickup.get_task · Task CU-184 · Succeeded · 420ms');
  });

  test('omits empty summary segments', () => {
    const { formatCollapsedLine } = window.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'llm', name: 'claude-sonnet', summary: '', status: 'succeeded', latency_ms: 1200,
    }))).toBe('LLM · claude-sonnet · Succeeded · 1.2s');
  });

  test('formats raw details as JSON text and falls back to escaped string', () => {
    const { formatRawDetails } = window.chiefActivityTree;
    expect(formatRawDetails({ a: 1 })).toContain('"a": 1');
    const cyclic = {}; cyclic.self = cyclic;
    expect(formatRawDetails(cyclic)).toEqual(expect.any(String));
  });

  test('caps visual depth and exposes depth markers', () => {
    const { visualDepth } = window.chiefActivityTree;
    expect(visualDepth(3)).toEqual({ indentLevel: 3, showMarker: false, marker: null });
    expect(visualDepth(8)).toEqual({ indentLevel: 6, showMarker: true, marker: 'L8' });
  });
});
```

- [ ] **Step 2: Run — expect fail; implement; re-run pass**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/web/static/web/activity_tree.js backend/apps/web/static/web/activity_tree.test.js
git commit -m "$(cat <<'EOF'
feat: format compact activity rows, raw JSON, and depth markers

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 5: Django activity snapshot endpoint

**Files:**
- Create: `backend/apps/web/tests/test_activity_snapshot.py`
- Modify: `backend/apps/web/services/queries.py`
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`

- [ ] **Step 1: Write failing HTTP tests**

```python
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from olib.py.django.test.cases import OTransactionTestCase


class TestActivitySnapshot(OTransactionTestCase):
    def setUp(self) -> None:
        self.client = Client()
        # Use spec 2 factories once merged (parent session + activities + linked child).
        self.session = make_activity_session('snap-agent')  # canonical helper from spec 2
        self.user = get_user_model().objects.get(username='user-snap-agent')
        self.client.force_login(self.user)

    def test_snapshot_requires_login(self) -> None:
        anon = Client()
        response = anon.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 302)

    def test_snapshot_hides_other_users_sessions(self) -> None:
        other = get_user_model().objects.create_user(username='other-snap', password='x')
        self.client.force_login(other)
        response = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 404)

    def test_snapshot_returns_stable_activity_fields(self) -> None:
        response = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': self.session.id}),
        )
        self.assertEqual(response.status_code, 200)
        activity = response.json()['activities'][0]
        for key in (
            'id', 'session_id', 'parent_id', 'seq', 'revision', 'kind', 'status',
            'name', 'summary', 'details', 'child_session_id',
        ):
            self.assertIn(key, activity)

    def test_parent_snapshot_excludes_child_session_activities(self) -> None:
        parent, child, child_only_id = make_parent_with_subagent('sep-agent')
        self.client.force_login(get_user_model().objects.get(username='user-sep-agent'))
        body = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': parent.id}),
        ).json()
        ids = {row['id'] for row in body['activities']}
        self.assertNotIn(str(child_only_id), ids)
        sub = next(row for row in body['activities'] if row['kind'] == 'subagent')
        self.assertEqual(sub['child_session_id'], str(child.id))

    def test_child_snapshot_includes_parent_breadcrumb(self) -> None:
        parent, child, _ = make_parent_with_subagent('crumb-agent')
        self.client.force_login(get_user_model().objects.get(username='user-crumb-agent'))
        body = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': child.id}),
        ).json()
        self.assertEqual(body['session']['parent']['id'], str(parent.id))

    def test_inaccessible_child_matches_missing_child_status(self) -> None:
        foreign = make_activity_session('foreign-agent')
        missing = uuid4()
        self.client.force_login(self.user)
        status_foreign = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': foreign.id}),
        ).status_code
        status_missing = self.client.get(
            reverse('session_activity_snapshot', kwargs={'session_id': missing}),
        ).status_code
        self.assertEqual(status_foreign, 404)
        self.assertEqual(status_missing, 404)
```

`make_activity_session` / `make_parent_with_subagent` must be imported from the canonical
spec 2 test module discovered in Task 0. Do not reimplement activity ORM setup in web tests.

- [ ] **Step 2: Run — expect fail**

```bash
./olib/scripts/orunr py test apps.web.tests.test_activity_snapshot
```

Expected: FAIL (URL/view missing or wrong payload).

- [ ] **Step 3: Implement query + view + URL**

`urls.py`:

```python
path(
    'sessions/<uuid:session_id>/activities/',
    views.session_activity_snapshot,
    name='session_activity_snapshot',
),
```

`views.session_activity_snapshot` (sync `@require_GET` + `@login_required`):

1. `user_id = _require_authenticated_user_id(request)`
2. `payload = get_activity_snapshot(user_id, session_id)` from `apps.web.services.queries`
3. `return JsonResponse(payload)`

`get_activity_snapshot` must:

- Call existing `get_owned_session` (404 if not owned).
- Call the **spec 2** sessions query for ordered activities + parent breadcrumb (no ORM in the view).
- Serialize via the spec 2 activity dict helper (same shape as SSE upserts).
- Never include another session’s activity rows.

Example response shape:

```json
{
  "session": {
    "id": "…",
    "name": "…",
    "status": "running",
    "parent_session_id": null,
    "parent": null
  },
  "activities": [ { "id": "…", "revision": 1, "kind": "tool", "…": "…" } ]
}
```

- [ ] **Step 4: Run — expect pass**

```bash
./olib/scripts/orunr py test apps.web.tests.test_activity_snapshot
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/web/tests/test_activity_snapshot.py \
  backend/apps/web/services/queries.py \
  backend/apps/web/views.py \
  backend/apps/web/urls.py
git commit -m "$(cat <<'EOF'
feat: add authorized session activity snapshot JSON endpoint

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 6: SSE ownership + upsert client contract regressions

**Files:**
- Modify: `backend/apps/web/tests/test_sse.py`
- Modify: `backend/apps/web/views.py` only if spec 2 left the web SSE adapter still emitting legacy `session_event` rows — align replay/tail to activity upserts using sessions helpers (no new protocol).

- [ ] **Step 1: Extend SSE tests**

```python
def test_sse_replays_activity_upserts_for_owned_session(self) -> None:
    session = make_activity_session('sse-act')
    seed_tool_activity(session, name='demo.op', status='succeeded')  # spec 2 helper

    async def collect() -> str:
        client = AsyncClient()
        user = await sync_to_async(get_user_model().objects.get)(username='user-sse-act')
        await sync_to_async(client.force_login)(user)
        response = await client.get(f'/sessions/{session.id}/events/')
        assert isinstance(response, StreamingHttpResponse)
        parts: list[bytes] = []
        async for part in cast(AsyncIterator[bytes], response.streaming_content):
            parts.append(part)
        return b''.join(parts).decode()

    body = asyncio.run(collect())
    self.assertIn('"operation": "upsert"', body)
    self.assertIn('"kind": "tool"', body)

def test_sse_rejects_other_users_session(self) -> None:
    session = make_activity_session('sse-deny')

    async def status_of() -> int:
        client = AsyncClient()
        other = await sync_to_async(get_user_model().objects.create_user)(
            username='sse-other', password='x',
        )
        await sync_to_async(client.force_login)(other)
        response = await client.get(f'/sessions/{session.id}/events/')
        return response.status_code

    self.assertEqual(asyncio.run(status_of()), 404)

def test_parent_stream_excludes_child_activity_rows(self) -> None:
    parent, child, child_activity_id = make_parent_with_subagent('sse-sep')

    async def collect() -> str:
        client = AsyncClient()
        user = await sync_to_async(get_user_model().objects.get)(username='user-sse-sep')
        await sync_to_async(client.force_login)(user)
        response = await client.get(f'/sessions/{parent.id}/events/')
        assert isinstance(response, StreamingHttpResponse)
        parts: list[bytes] = []
        async for part in cast(AsyncIterator[bytes], response.streaming_content):
            parts.append(part)
        return b''.join(parts).decode()

    body = asyncio.run(collect())
    self.assertNotIn(str(child_activity_id), body)
    self.assertIn('"kind": "subagent"', body)
```

Bind `make_activity_session`, `seed_tool_activity`, and `make_parent_with_subagent` to the canonical helpers from spec 2 after Task 0 (rename imports only — do not duplicate ORM setup in web).

- [ ] **Step 2: Run red/green with**

```bash
./olib/scripts/orunr py test apps.web.tests.test_sse
```

Adapt `session_events_sse` to:

- Replay authoritative activities via the spec 2 query (not `events_for` / `AgentSessionEvent`).
- Tail Redis; forward upsert envelopes and `session_update`.
- Keep `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- Dedupe using activity id + revision (not seq-only), matching the client store.

- [ ] **Step 3: Commit**

```bash
git add backend/apps/web/tests/test_sse.py backend/apps/web/views.py
git commit -m "$(cat <<'EOF'
feat: stream session activity upserts on the owned SSE channel

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 7: Session template — recursive compact tree + slim page controller

**Files:**
- Modify: `backend/templates/web/session_detail.html`
- Modify: `backend/apps/web/tests/test_session_dialog.py`
- Modify: `backend/templates/web/macros/session.html` (breadcrumb macro if needed)

- [ ] **Step 1: Update Django template contract tests (fail first)**

Replace flat-event assertions with tree contracts, for example:

```python
def test_session_page_loads_activity_tree_module(self) -> None:
    response = self.client.get(reverse('session_detail', kwargs={'session_id': self.session.id}))
    self.assertContains(response, 'web/activity_tree.js')
    self.assertContains(response, 'id="activity-tree"')
    self.assertContains(response, 'aria-expanded')

def test_session_page_keeps_beautify_for_output_only(self) -> None:
    response = self.client.get(reverse('session_detail', kwargs={'session_id': self.session.id}))
    self.assertContains(response, "activity.kind === 'output'")
    self.assertNotContains(response, 'localStorage')
    self.assertContains(response, 'toggleBeautify')
    self.assertContains(response, 'renderRichOutputAttempt')

def test_child_session_page_shows_parent_breadcrumb(self) -> None:
    parent, child, _ = make_parent_with_subagent('ui-crumb')
    user = get_user_model().objects.get(username='user-ui-crumb')
    self.client.force_login(user)
    response = self.client.get(reverse('session_detail', kwargs={'session_id': child.id}))
    self.assertContains(response, reverse('session_detail', kwargs={'session_id': parent.id}))
    self.assertContains(response, 'Parent session')
```

Delete obsolete asserts on `evt.kind === 'OUTPUT'`, flat `events: []`, and `formatEventStats` once the template no longer emits them.

- [ ] **Step 2: Run — expect fail**

```bash
./olib/scripts/orunr py test apps.web.tests.test_session_dialog
```

- [ ] **Step 3: Rewrite `session_detail.html` main panel**

Load:

```html
<script src="{{ static('web/activity_tree.js') }}"></script>
<script src="{{ static('web/rich_content_lifecycle.js') }}"></script>
<script type="module" src="{{ static('web/rich-content/rich_content.bundle.js') }}"></script>
```

Markup (semantic lists, **not** ARIA `tree`). Keep the existing Beautify/Follow toolbar markup. Replace the flat `x-for="evt in events"` stream with:

```html
<section class="event-panel" aria-label="Session activity">
  <!-- existing event-toolbar unchanged -->
  <div id="event-panel" class="event-stream-wrap" x-ref="eventPanel" @scroll="onScroll()">
    <div id="activity-tree" class="activity-tree">
      <template x-if="rootStore.loadState === 'failed'">
        <div class="activity-load-failed">
          <p>Could not load activity.</p>
          <button type="button" class="frame-btn" @click="retryRoot()">Retry</button>
        </div>
      </template>
      <ol class="activity-list" x-show="rootStore.loadState === 'ready'">
        <template x-for="id in rootStore.rootIds" :key="id">
          <li
            x-data="activityNode(() => rootStore, id, 0)"
            x-bind="activityNodeBindings"
          ></li>
        </template>
      </ol>
    </div>
  </div>
</section>
```

Register a recursive Alpine component once (in `activity_tree.js` or the session script) before Alpine starts:

```js
document.addEventListener('alpine:init', () => {
  /**
   * One activity row. `getStore` returns the owning session store so nested
   * subagent children can switch to `childStores[child_session_id]`.
   */
  Alpine.data('activityNode', (getStore, activityId, depth) => ({
    getStore,
    activityId,
    depth,
    get store() { return this.getStore(); },
    get activity() { return this.store.activities[this.activityId]; },
    get detailId() { return `activity-detail-${this.activityId}`; },
    get childStore() {
      const childId = this.activity?.child_session_id;
      return childId ? this.store.childStores[childId] : null;
    },
    init() {
      if (this.activity?.kind === 'subagent' && this.store.isExpanded(this.activityId)) {
        this.store.ensureChildLoaded(this.activityId);
      }
    },
  }));
});
```

Provide the row HTML via `activityNodeBindings` as an Alpine `x-html`/`template` pattern, **or** expand the `<li>` body inline in `session_detail.html` (preferred for reviewability) including:

1. Message kinds (`input`/`output`): prominent body; OUTPUT/output uses existing Beautify + `renderOutput`.
2. Execution kinds: collapsed line button + curated details + independent Raw JSON `<details>`.
3. Nested `<ol>` over `store.childIds[activityId]` with `x-data="activityNode(() => store, childId, depth + 1)"`.
4. Subagent expanded body: if `childLoadState` is `unavailable`/`cycle`, show that text; else render `childStore.rootIds` with `activityNode(() => childStore, id, depth + 1)`.

Each expandable row must include:

```html
<button
  type="button"
  class="activity-toggle"
  :aria-expanded="store.isExpanded(activityId).toString()"
  :aria-controls="detailId"
  :aria-label="toggleLabel(activity)"
  @click="toggle(activityId)"
></button>
<div :id="detailId" x-show="store.isExpanded(activityId)">
  <!-- curated details by kind -->
  <details class="activity-raw">
    <summary>Raw JSON</summary>
    <pre class="activity-raw-pre" x-text="formatRawDetails(activity.details)"></pre>
  </details>
</div>
```

Sub-agent row always includes:

```html
<a :href="`/sessions/${activity.child_session_id}/`">Open session</a>
```

Unavailable copy is exactly `Child session unavailable`.

Header: if `session.parent` present, show breadcrumb link back to parent session.

`sessionView` keeps: chat helpers, Follow, Beautify, rich output rendering via lifecycle, `pagehide`/`pageshow`/`alpine:destroy`, `formatTotalCost` from **this session store’s** activities only (never sum child stores).

Init:

1. `rootStore = createActivityStore(sessionId)`
2. `await rootStore.loadRoot()` (snapshot then SSE)
3. Wire Follow to scroll when root or expanded-child height changes (`$watch` / `$nextTick`) without forcing Follow on manual expand.

- [ ] **Step 4: Run template tests — expect pass**

```bash
./olib/scripts/orunr py test apps.web.tests.test_session_dialog
```

- [ ] **Step 5: Commit**

```bash
git add backend/templates/web/session_detail.html \
  backend/templates/web/macros/session.html \
  backend/apps/web/tests/test_session_dialog.py
git commit -m "$(cat <<'EOF'
feat: render recursive compact activity tree on the session page

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 8: Compact-tree styles and responsive behavior

**Files:**
- Modify: `backend/templates/web/partials/agent_frame_styles.html`
- Modify: `backend/apps/web/tests/test_session_dialog.py` (assert key classnames if useful)

- [ ] **Step 1: Add styles matching existing dark frame tokens**

Reuse colors already in `agent_frame_styles.html` / `base.html` (`#12151c`, `#232732`, `#8b93a7`, kind pills). Add:

- `.activity-tree`, `.activity-list`, `.activity-row`, `.activity-row-message` (messages more prominent; ops denser)
- `.activity-row-main` single scan line; wrap metadata under name at narrow widths (`@media (max-width: 640px)`)
- Connector/indent via `padding-left` + border; `.activity-depth-cap` for marker after level 6
- `.activity-raw-pre { max-height: 12rem; overflow: auto; }` — no page-level horizontal overflow
- Visible `:focus-visible` on `.activity-toggle`
- Touch-sized controls (`min-height: 2.25rem` on toggles)
- Kind/status text classes (do not rely on color alone)

Do not introduce card-heavy nesting; keep the existing event-panel chrome.

- [ ] **Step 2: Visual smoke via browser test in Task 9; commit styles with any classname asserts**

```bash
git add backend/templates/web/partials/agent_frame_styles.html backend/apps/web/tests/test_session_dialog.py
git commit -m "$(cat <<'EOF'
feat: style compact nested activity rows for desktop and narrow screens

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 9: Browser interaction smoke (keyboard, focus, depth)

**Files:**
- Create: `backend/apps/web/static/web/activity_tree.browser.test.js`
- Modify: `backend/apps/web/static/web/vitest.browser.config.js`

- [ ] **Step 1: Include the browser file**

Extend `include` to `['rich_content.browser.test.js', 'activity_tree.browser.test.js']`. Serve `activity_tree.js` from the existing browser middleware pattern (add a `/activity-tree-source/activity_tree.js` branch like the lifecycle probe).

- [ ] **Step 2: Write browser tests**

```js
import { describe, expect, test } from 'vitest';

describe('activity tree browser smoke', () => {
  test('toggle button exposes aria-expanded and does not move focus on data patches', async () => {
    document.body.innerHTML = `
      <div id="fixture">
        <button type="button" id="tog" aria-expanded="false" aria-controls="detail">Tool</button>
        <div id="detail" hidden>args</div>
      </div>`;
    const { createActivityStore } = window.chiefActivityTree;
    const store = createActivityStore('s1');
    store.applyUpsert({
      id: 'tool', session_id: 's1', parent_id: null, seq: 1, revision: 1,
      kind: 'tool', status: 'running', name: 'demo.op', summary: '', details: {},
      child_session_id: null, latency_ms: null, cost_usd: null, model: null,
    });
    const button = document.getElementById('tog');
    button.focus();
    button.addEventListener('click', () => {
      store.setExpanded('tool', true, { manual: true });
      button.setAttribute('aria-expanded', store.isExpanded('tool') ? 'true' : 'false');
      document.getElementById('detail').hidden = !store.isExpanded('tool');
    });
    button.click();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    store.applyUpsert({
      id: 'tool', session_id: 's1', parent_id: null, seq: 1, revision: 2,
      kind: 'tool', status: 'succeeded', name: 'demo.op', summary: '', details: {},
      child_session_id: null, latency_ms: 5, cost_usd: null, model: null,
    });
    expect(document.activeElement).toBe(button);
  });

  test('deep rows show depth marker without horizontal page overflow', async () => {
    const { visualDepth } = window.chiefActivityTree;
    const depth = visualDepth(8);
    document.body.innerHTML = `
      <div id="fixture" style="width:320px;overflow:auto">
        <div class="activity-row" style="padding-left:${depth.indentLevel}rem">
          <span class="activity-depth-cap">${depth.marker}</span>
          <span>deep-op</span>
        </div>
      </div>`;
    expect(document.querySelector('.activity-depth-cap').textContent).toBe('L8');
    const fixture = document.getElementById('fixture');
    expect(fixture.scrollWidth).toBeLessThanOrEqual(fixture.clientWidth + 1);
  });
});
```

Load `activity_tree.js` in the browser config middleware (mirror the lifecycle script route) before these tests run.

- [ ] **Step 3: Run**

```bash
pnpm --dir backend/apps/web/static/web run test:browser -- activity_tree.browser.test.js
```

Expected: FAIL then PASS after fixture/impl glue.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/web/static/web/activity_tree.browser.test.js \
  backend/apps/web/static/web/vitest.browser.config.js
git commit -m "$(cat <<'EOF'
test: cover activity-tree keyboard focus and deep nesting in browser

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 10: Follow, Beautify, cost totals, and BFCache integration tests

**Files:**
- Modify: `backend/apps/web/static/web/activity_tree.test.js`
- Modify: `backend/templates/web/session_detail.html` (if gaps remain)

- [ ] **Step 1: Unit tests for page-integration helpers**

```js
test('totalCostUsd sums only the current store activities', () => {
  const { sumCostUsd } = window.chiefActivityTree;
  const root = createActivityStore('s1');
  root.applyUpsert(act({ id: 'llm', kind: 'llm', cost_usd: '0.010000', status: 'succeeded' }));
  root.childStores.child = createActivityStore('child');
  root.childStores.child.applyUpsert(act({
    id: 'llm2', session_id: 'child', kind: 'llm', cost_usd: '9.000000', status: 'succeeded',
  }));
  expect(sumCostUsd(root)).toBeCloseTo(0.01);
});

test('beautify targets output kind only', () => {
  const { isBeautifiable } = window.chiefActivityTree;
  expect(isBeautifiable({ kind: 'output' })).toBe(true);
  expect(isBeautifiable({ kind: 'tool' })).toBe(false);
});

test('refreshExpandedSnapshots refetches root and expanded children', async () => {
  const urls = [];
  window.fetch = async (url) => {
    urls.push(String(url));
    const id = String(url).includes('/child/') ? 'child' : 's1';
    return {
      ok: true,
      json: async () => ({
        session: {
          id, status: id === 'child' ? 'running' : 'running',
          name: null, parent_session_id: id === 'child' ? 's1' : null, parent: null,
        },
        activities: [],
      }),
    };
  };
  class FakeEventSource {
    constructor() { this.listeners = {}; }
    addEventListener() {}
    close() { this.closed = true; }
  }
  window.EventSource = FakeEventSource;
  const root = createActivityStore('s1');
  await root.loadRoot();
  root.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child' }));
  await root.ensureChildLoaded('sub');
  urls.length = 0;
  await root.refreshExpandedSnapshots();
  expect(urls.some((u) => u.includes('/sessions/s1/activities/'))).toBe(true);
  expect(urls.some((u) => u.includes('/sessions/child/activities/'))).toBe(true);
});
```

Wire `pageshow` persisted → `refreshExpandedSnapshots()` then reconnect (replace today’s wipe-only `reconnectStream`).

- [ ] **Step 2: Run JS unit + session dialog tests**

```bash
pnpm --dir backend/apps/web/static/web run test:unit -- activity_tree.test.js
./olib/scripts/orunr py test apps.web.tests.test_session_dialog
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/web/static/web/activity_tree.js \
  backend/apps/web/static/web/activity_tree.test.js \
  backend/templates/web/session_detail.html
git commit -m "$(cat <<'EOF'
feat: preserve Follow, Beautify, costs, and BFCache across activity trees

EOF
)"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 11: Full verification gate

**Files:** none expected beyond fixes

- [ ] **Step 1: Run required gates**

```bash
./olib/scripts/orunr py test-all
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: all exit 0.

- [ ] **Step 2: Fix any failures without expanding scope; commit if needed**

```bash
git add -u
git commit -m "$(cat <<'EOF'
fix: clear nested activity UI verification findings

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
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
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

Read `superpowers/requesting-code-review`. Dispatch reviewer with:

- `{DESCRIPTION}` — nested activity UI: compact tree, snapshot endpoint, child SSE lifecycle
- `{PLAN_OR_REQUIREMENTS}` —
  `docs/specs/2026-07-26-nested-activity-ui/2026-07-26-nested-activity-ui-design.md` and
  `docs/specs/2026-07-26-nested-activity-ui/2026-07-26-nested-activity-ui-plan.md`
- `{BASE_SHA}` / `{HEAD_SHA}` — from Step 2

- [ ] **Step 4: Write review file and report findings**

Write
`docs/specs/2026-07-26-nested-activity-ui/2026-07-26-nested-activity-ui-review.md`
per `review-file-template.md` (Status column empty initially). Summarize in chat.

- [ ] **Step 5: Track feedback**

Update Status to **Fixed** or **Rejected** as the user directs.

- [ ] **Step 6: Human handoff**

Offer `superpowers/finishing-a-development-branch`. Do not check epic/spec boxes in
`-revision.md` or the epic unless the user explicitly approves after review.

---

## Out of scope

- Spec 1 integration projections and payload normalization
- Spec 2 persistence, migration, runner recorder, sub-agent start service
- Changing rich-content Markdown/Mermaid/KaTeX parsing
- Storing expansion state in Postgres or localStorage
- ARIA `tree` / `treeitem` widgets
- Aggregating child-session cost into the parent header

## References

- Design: `docs/specs/2026-07-26-nested-activity-ui/2026-07-26-nested-activity-ui-design.md`
- Backend contract: `docs/specs/2026-07-26-hierarchical-session-activities/2026-07-26-hierarchical-session-activities-design.md`
- Epic: `docs/epics/2026-07-26-agent-context-activity-clarity.md`
- Architecture: `docs/ARCHITECTURE.md` (web views → services; SSE ownership)
- Current UI baselines: `backend/templates/web/session_detail.html`,
  `backend/apps/web/views.py` (`session_events_sse`),
  `backend/apps/web/static/web/rich_content_lifecycle.js`,
  `backend/templates/web/partials/agent_frame_styles.html`

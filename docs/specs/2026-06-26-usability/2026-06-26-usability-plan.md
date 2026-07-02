# Chief — Usability implementation plan

Companion to [`2026-06-26-usability-design.md`](2026-06-26-usability-design.md). Each section maps to one SPEC item and lists concrete build steps plus tests.

**Current baseline** (what exists today):

| Area | Today |
|------|-------|
| Routes | `GET /` dashboard; `POST /agents/bootstrap/`; `POST /agents/<id>/start/`; `GET /sessions/<id>/` |
| Agent page | Does not exist |
| Session page | Event log (left) + sidebar chat/controls (`session_detail.html`) |
| Chat | Inline in session template; optimistic htmx append only for user messages |
| Bootstrap redirect | Dashboard (`views.bootstrap_agent` → `redirect('dashboard')`) |
| `start_manual_session` | Already accepts `initial_message` (`runner/start.py`) but web view does not pass it |

**Suggested build order** (dependencies flow downward):

1. Shared layout frame + CSS (fixed viewport, scrollable panels)
2. Shared chatbox partial + Alpine helpers
3. Agent detail route/view/template
4. Bootstrap redirect + dashboard links
5. Start-session-from-chatbox endpoint
6. Session page refactor (conversation dialog + shared chatbox)
7. Follow / auto-scroll behavior
8. Dashboard session links

---

## 0. Shared infrastructure (prerequisite for most items)

Several SPEC items depend on the same templates and CSS. Build these first.

### 0.1 Agent/session frame template

**Goal:** One outer shell for agent and session views — header, scrollable main panel, fixed bottom chatbox. The page body never scrolls.

**Files:**

- Add `backend/templates/web/partials/agent_frame.html` — layout skeleton:
  - `{% block frame_header %}` — agent identifier, session status pill, optional controls
  - `<div class="frame-main" id="frame-main">{% block frame_main %}{% endblock %}</div>` — scrollable
  - `{% include "web/partials/chatbox.html" %}` — always at bottom
- Add shared CSS (either in `partials/agent_frame_styles.html` included by both pages, or `extra_styles` block in a thin wrapper)

**CSS sketch:**

```css
.frame-page { display: flex; flex-direction: column; height: calc(100vh - /* header height */); overflow: hidden; }
.frame-main { flex: 1; min-height: 0; overflow-y: auto; }
.frame-chatbox { flex-shrink: 0; border-top: 1px solid #262a33; }
```

Adjust `base.html` `main` for agent/session pages: drop `max-width` centering or use a full-width variant (`{% block main_class %}`) so the frame can use the full viewport height.

**Tests:**

- `backend/apps/web/tests/test_agent_frame.py` (optional smoke): render agent and session templates, assert presence of `.frame-page`, `.frame-main`, `.frame-chatbox`, and that chatbox partial is included once.

---

### 0.2 Shared chatbox partial

**Goal:** One component for agent screen (pre-session) and session screen (active chat).

**File:** `backend/templates/web/partials/chatbox.html`

**Parameters (Jinja context):**

| Variable | Agent screen | Session screen |
|----------|--------------|----------------|
| `agent` | required | from `session.agent` |
| `session` | `None` | required |
| `chat_post_url` | `url('agent_start_chat', …)` | `url('session_chat', …)` |
| `chat_mode` | `'start'` | `'continue'` |

**Markup (extract from current `session_detail.html`):**

- `<form>` with `hx-post="{{ chat_post_url }}"`, `@submit="clearChatInput($event)"`, `@keydown.enter` handling
- On **continue** mode: `hx-target="#dialog-messages"`, `hx-swap="beforeend"` (optimistic user bubble in dialog, not a separate sidebar list)
- On **start** mode: full form POST (see §3) or htmx POST that returns `HX-Redirect` to session URL — prefer redirect so URL bar updates and SSE connects on the session page
- Shared `<textarea>` styling, hint text, `x-ref="chatInput"`

**Alpine helpers:** move `onChatKeydown` / `clearChatInput` into a small inline or static `chatbox.js`, or a shared `chatboxBehavior()` mixin included via `<script>` in the partial. Both `agent_detail.html` and `session_detail.html` should use the same helpers.

**Tests:**

- `backend/apps/web/tests/test_chatbox_partial.py`:
  - Agent detail page contains chatbox form pointing at agent start-chat URL, textarea, hint.
  - Session detail page contains same chatbox classes/structure, form pointing at session chat URL.
  - Both pages include identical `frame-chatbox` wrapper (snapshot-style `assertContains` on shared CSS class names).

---

## 1. New agent → agent screen

**SPEC:** Clicking a new-agent button on the Chief page goes directly into that agent.

### Implementation

1. **`bootstrap_agent` returns the created agent** — `create_bootstrap_agent(...)` already returns `Agent`; capture it in `views.bootstrap_agent`.
2. **Redirect target** — replace `redirect('dashboard')` with `redirect('agent_detail', agent_id=agent.id)`.
3. **Add route** (if not done in §2): `path('agents/<uuid:agent_id>/', views.agent_detail, name='agent_detail')`.

**Files:** `backend/apps/web/views.py`, `backend/apps/web/urls.py`

### Tests

Update `backend/apps/web/tests/test_bootstrap.py`:

- `test_creates_agent_for_current_user`: expect redirect to `reverse('agent_detail', kwargs={'agent_id': agent.id})` instead of dashboard.
- Add `test_bootstrap_lands_on_agent_page`: follow redirect, assert 200, page shows agent identifier and chatbox (§0.2).

---

## 2. Agent identifier is a link

**SPEC:** Clicking an agent's identifier opens the agent screen.

### Implementation

1. **`agent_detail` view** — `GET`, `@login_required`, `get_object_or_404(Agent, pk=agent_id, user=request.user)`.
2. **Query context** — agent; sessions for that agent ordered by `-created_at` (paginate later if needed; start with all or last N).
3. **Template** `backend/templates/web/agent_detail.html` — extends frame (§0.1):
   - `frame_main`: sessions table (identifier column linkable per §8), status, created time; optional Delete button (existing POST).
   - Bottom: shared chatbox in `start` mode (§0.2).
4. **Dashboard link** — in `dashboard.html`, wrap `{{ agent.identifier }}` in `<a href="{{ url('agent_detail', kwargs={'agent_id': agent.id}) }}">`.

**Files:** `views.py`, `urls.py`, `agent_detail.html`, `dashboard.html`

### Tests

Add `backend/apps/web/tests/test_agent_detail.py`:

- `test_requires_login` — anonymous GET → 302 login.
- `test_renders_owned_agent` — 200, contains identifier, sessions section, chatbox.
- `test_cannot_view_other_users_agent` — 404.
- `test_lists_agent_sessions` — create two sessions for agent, only those appear (not other agents').
- `test_dashboard_agent_identifier_links_to_detail` — GET dashboard, response contains `href` to agent detail URL.

---

## 3. Agent screen layout + start chat from chatbox

**SPEC:** Dashboard (sessions) on top; bottom chatbox; Enter starts a new chat and navigates to session view.

### Implementation

1. **New endpoint** `POST agents/<uuid:agent_id>/chat/` → `agent_start_chat` view:
   - `@login_required`, ownership check.
   - Read `content` from POST; 400 if empty.
   - `session = start_manual_session(agent, initial_message=content)` — backend already queues first message via `push_chat_and_dispatch`.
   - `redirect('session_detail', session_id=session.id)`.
2. **Wire chatbox** on agent page to this URL (§0.2 `chat_mode='start'`).
3. **Remove or demote "Start" button** on dashboard agent row — starting empty sessions is redundant once chatbox is the primary entry; keep Delete. (Optional: keep Start as power-user shortcut that goes to agent page instead of empty session.)
4. **Sessions table on agent page** — columns: session id (link §8), status, created; no separate "Open" link needed if id is the link.

**Files:** `views.py`, `urls.py`, `agent_detail.html`, optionally `dashboard.html`

### Tests

Add `backend/apps/web/tests/test_agent_start_chat.py`:

- `test_requires_login`
- `test_requires_content` — empty POST → 400
- `test_creates_session_with_initial_message` — POST content `"hello"`, redirect to session detail; session exists; first INPUT event (or mailbox) contains `"hello"` — use `events_for(session.id)` from `apps.sessions.events`.
- `test_cannot_start_chat_on_other_users_agent` — 404
- `test_agent_page_chat_form_posts_to_start_chat` — GET agent detail, form `action` / htmx URL matches `agent_start_chat`

Extend `backend/apps/runner/tests/` if no test yet for `start_manual_session(initial_message=...)` — may already be covered; add unit test there if missing.

---

## 4. Seamless agent → session transition

**SPEC:** Chatbox stays visually fixed; only the top switches from dashboard to conversation.

### Implementation

The transition is a **navigation** to the session URL, but both pages must share the **same frame** so the chatbox does not jump.

1. **Shared frame** (§0.1) — identical structure on `agent_detail.html` and `session_detail.html`.
2. **Shared chatbox CSS** (§0.2) — same height, border, textarea rows, padding on both pages.
3. **Preserve focus** — on session page `x-init`, focus chat input (`session_detail.html` already does this); after redirect from agent chat, user lands with empty input (message already sent) and cursor ready for follow-up.
4. **Optional polish** — `hx-push-url` not needed if using redirect; avoid layout shift by setting fixed `min-height` on `.frame-chatbox`.

**Do not** build a separate transition animation in v1; structural identity of the frame is enough.

### Tests

- `test_agent_start_chat_lands_on_session_with_same_frame` — GET agent page, note chatbox markup/classes; POST chat; follow redirect; assert session page has same `.frame-chatbox` structure and conversation panel visible (§5).
- Manual QA checklist in test plan (see end).

---

## 5. Shared chatbox component

**SPEC:** One shared component on agent and session views.

Covered by §0.2. Session refactor (below) must **delete** the inline chat form from the old sidebar and include the partial instead.

### Session page refactor (part of this item)

1. Rewrite `session_detail.html` to extend/use `agent_frame.html`.
2. **`frame_main`** — conversation dialog (§6) + collapsible/raw event log optional for observability (can stay behind a toggle; SPEC prioritizes dialog).
3. **Controls** (Pause/Resume/Abort) — move to `frame_header` or a slim toolbar above dialog; keep existing htmx partial swaps to `#session-status`.
4. Remove duplicate chat markup from sidebar.

### Tests

- `test_chatbox_partial.py` (§0.2)
- `test_session_detail_renders_shared_chatbox` — no duplicate textarea outside partial
- `test_session_detail_does_not_include_old_sidebar_chat` — regression guard

---

## 6. Fixed outer frame, scrollable inner panels

**SPEC:** Page never scrolls; chatbox always visible; dashboard/dialog scroll internally.

### Implementation

1. **CSS in frame partial** (§0.1):
   - `html, body { height: 100%; overflow: hidden; }` scoped to frame pages only (body class `chief-frame` set in template) so dashboard can keep normal scroll.
   - `.frame-main { overflow-y: auto; min-height: 0; }`
2. **Agent dashboard panel** — sessions table inside `.frame-main`; long lists scroll inside panel.
3. **Session dialog panel** — `#dialog-panel` inside `.frame-main` with `overflow-y: auto` (see §7).

**Files:** `partials/agent_frame.html`, styles block, `base.html` optional body class block

### Tests

- Template tests asserting CSS classes present (`frame-page`, `frame-main`, `frame-chatbox`).
- No automated viewport test required; manual QA: resize window, confirm chatbox pinned.

---

## 7. Dialog auto-scroll + Follow toggle

**SPEC:** Conversation scrolls to latest message unless user scrolls manually; **Follow** button re-enables or disables auto-follow.

### Implementation

1. **Conversation panel** — replace raw event log as primary UI:
   - `#dialog-panel` with `#dialog-messages` container.
   - Alpine `sessionView` filters `events` to `INPUT` / `OUTPUT` (and optionally merges into chat bubbles); render as conversation rows (user vs agent styling), not debug `pre` blocks.
2. **Auto-scroll logic** in `sessionView` (extend inline script or extract `session_dialog.js`):

```javascript
follow: true,
onScroll() {
  const el = this.$refs.dialogPanel;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (!atBottom) this.follow = false;
},
scrollToBottom() {
  const el = this.$refs.dialogPanel;
  el.scrollTop = el.scrollHeight;
},
// After pushing event or htmx swap: if (this.follow) this.$nextTick(() => this.scrollToBottom());
toggleFollow() {
  this.follow = !this.follow;
  if (this.follow) this.scrollToBottom();
},
```

3. **Wire scroll listener** — `@scroll="onScroll"` on `#dialog-panel`.
4. **Follow button** — in dialog header: `<button @click="toggleFollow()" x-text="follow ? 'Following' : 'Follow'">`.
5. **SSE handler** — after `this.events.push(data)`, call conditional `scrollToBottom`.
6. **htmx optimistic message** — after chat POST appends user bubble, scroll if following.

Keep a secondary **Event log** toggle for full observability (tool calls, failures) — aligns with design doc goals; not required for SPEC checkbox but useful for debugging.

### Tests

- `backend/apps/web/tests/test_session_dialog.py`:
  - Session page contains `#dialog-panel`, `#dialog-messages`, Follow button.
  - Page includes Alpine `follow` / `toggleFollow` in script (string assert).
- `test_sse.py` already covers event stream — no change required.
- Manual QA: send messages, confirm scroll; scroll up, confirm follow stops; click Follow, confirm snap to bottom.

---

## 8. Session identifier is a link

**SPEC:** Clicking a session identifier opens that session.

### Implementation

1. **Agent detail sessions table** — session id column (or short id prefix) links to `url('session_detail', kwargs={'session_id': s.id})`.
2. **Global dashboard** (`dashboard.html`) — replace separate "Open" link with clickable session id, or add id link alongside agent column. Prefer linking a human-readable token (first 8 chars of UUID) if full uuid is noisy.
3. **Consistency** — use `url()` helper everywhere, not hardcoded `/sessions/.../`.

**Files:** `agent_detail.html`, `dashboard.html`

### Tests

- `test_agent_detail_session_links` — session row contains `reverse('session_detail', ...)`.
- `test_dashboard_session_links` — recent sessions table contains session detail href.
- `test_session_link_navigates` — GET link returns 200 with shared frame + dialog.

---

## Cross-cutting concerns

### Auth and ownership

Today `session_detail`, `session_chat`, and SSE are **unauthenticated**. For usability work, align with agent endpoints:

- `@login_required` on `session_detail`, `session_chat`, pause/resume/abort, SSE (or return 404 for non-owner).
- Add tests mirroring `test_start_session.py` / `test_agent_detail.py` patterns.

Do this when touching session views (§5), not necessarily as first PR.

### URL map (target)

```
GET  /                                          dashboard
POST /agents/bootstrap/                         → redirect agent_detail
GET  /agents/<id>/                              agent_detail
POST /agents/<id>/chat/                         → redirect session_detail (new session)
POST /agents/<id>/delete/                       → redirect dashboard
GET  /sessions/<id>/                            session_detail
POST /sessions/<id>/chat/                       htmx partial (user bubble)
GET  /sessions/<id>/events/                     SSE
POST /sessions/<id>/pause|resume|abort/         htmx status partial
```

### Files to add/change (summary)

| Action | Path |
|--------|------|
| Add | `backend/templates/web/agent_detail.html` |
| Add | `backend/templates/web/partials/agent_frame.html` |
| Add | `backend/templates/web/partials/chatbox.html` |
| Add | `backend/templates/web/partials/dialog_message.html` (optional; user/agent bubble for htmx + Alpine) |
| Change | `backend/templates/web/session_detail.html` — major refactor |
| Change | `backend/templates/web/dashboard.html` — links |
| Change | `backend/apps/web/views.py` — new views, redirects |
| Change | `backend/apps/web/urls.py` — new routes |
| Add | `backend/apps/web/tests/test_agent_detail.py` |
| Add | `backend/apps/web/tests/test_agent_start_chat.py` |
| Add | `backend/apps/web/tests/test_chatbox_partial.py` |
| Add | `backend/apps/web/tests/test_session_dialog.py` |
| Change | `backend/apps/web/tests/test_bootstrap.py` — redirect expectation |

---

## Test plan checklist

Run after implementation:

```bash
./olib/scripts/orunr.sh py test-all
```

| Area | Automated | Manual |
|------|-----------|--------|
| Bootstrap → agent page | `test_bootstrap` | Click model button in browser |
| Agent id link | `test_dashboard_agent_identifier_links_to_detail` | Click identifier on dashboard |
| Agent page layout | `test_agent_detail` | Sessions list + bottom chat visible without page scroll |
| Start chat Enter | `test_agent_start_chat` | Type message, Enter, land on session |
| Seamless transition | frame markup test | Chatbox position unchanged across navigation |
| Shared chatbox | `test_chatbox_partial` | Inspect both pages |
| Fixed frame | CSS class asserts | Resize window; chatbox stays visible |
| Dialog + Follow | `test_session_dialog` | SSE messages auto-scroll; scroll up; Follow |
| Session id link | `test_agent_detail_session_links`, dashboard | Click session id |

---

## SPEC traceability

| SPEC item (`2026-06-26-usability-design.md`) | Primary sections |
|-------------------------------|------------------|
| New agent → agent screen | §1 |
| Agent identifier is a link | §2 |
| Agent screen layout | §2, §3, §0.1 |
| Seamless agent → session transition | §4 |
| Shared chatbox component | §0.2, §5 |
| Fixed outer frame | §0.1, §6 |
| Dialog auto-scroll + Follow | §7 |
| Session identifier is a link | §8 |

# Button triggers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: `/impl` first uses `superpowers/using-git-worktrees`, then uses superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task in the prepared absolute worktree. Then create `docs/specs/2026-08-02-button-triggers/2026-08-02-button-triggers-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — under `/ship`, return to the ship skill for **S_final** (`superpowers/requesting-code-review`); do not run finishing menus from the executor.

**Goal:** Add a `button` trigger kind with `button_text` + `prompt`, render clickable quick-action buttons above the agent/session chatbox, and start a new chatable session on click.

**Architecture:** Extend `TriggerSpec` / `TriggerKind` (no schema_version bump). Materialization already stores the full trigger dict. Add `start_button_session` beside `start_manual_session` (prompt dispatch like schedule; lifecycle like manual). Web POST run endpoint + chatbox partial row; config helper gets button text field.

**Tech Stack:** Django, pydantic `TriggerSpec`, Jinja chatbox partial, Alpine/htmx frame, config editor JS (esbuild bundle)

**Branch:** `feat/2026-08-02-button-triggers`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: scoped `./olib/scripts/orunr py test …` while iterating; `./olib/scripts/orunr py test-all` before S_final
- JS editor changes: edit `backend/apps/web/static/web/agent_config_editor.js`, then rebuild with `./olib/scripts/orunr js …` / package `build:editor` from that static root as used elsewhere in the repo
- **Git:** plan docs commit on `main`; implementation tasks use `feat/2026-08-02-button-triggers`; after each stage commit run `git fetch origin main && git rebase origin/main && git push`
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Test bases:** `OTestCase` / `OTransactionTestCase` only — never bare `unittest.TestCase`
- Avoid parproc-highlighted words in test names (`error`, `exception`, `warning`, `deprecated`, …)
- **Agent docs:** update `docs/docs/agents.md` when changing trigger schema (`AGENTS.local.md`)
- **Final task:** under `/ship`, ship skill owns S_final — plan still includes the S_final checkbox block for completeness

## File map

| File | Responsibility |
|------|----------------|
| `backend/libs/agent_spec/spec.py` | `button` kind, `button_text`, validators |
| `backend/apps/agents/models.py` | `TriggerKind.BUTTON` |
| `backend/apps/agents/services/queries.py` | `TRIGGER_KINDS` + catalog prompt default for helper |
| `backend/apps/agents/services/config_mutations.py` | Persist `button_text` on add_trigger |
| `backend/apps/runner/start.py` | `start_button_session` |
| `backend/apps/runner/scheduling.py` | (reuse) `trigger_prompt`, `trigger_has_capacity` — no beat for button |
| `backend/apps/runner/session_lifecycle.py` | Leave `_AUTOMATED_TERMINATE_KINDS` unchanged (no button) |
| `backend/apps/web/views.py` | Run endpoint; chatbox context includes buttons |
| `backend/apps/web/urls.py` | Route for run |
| `backend/templates/web/partials/chatbox.html` | Button row above textarea |
| `backend/templates/web/partials/agent_frame_styles.html` | Minimal button-row styles |
| `backend/templates/web/agent_config.html` | Helper button_text field |
| `backend/apps/web/static/web/agent_config_editor.js` (+ bundle) | Kind toggle for button_text |
| `docs/docs/agents.md` | Document `button` kind |

---

### Task 1: Schema — `button` kind + `button_text`

**Files:**
- Modify: `backend/libs/agent_spec/spec.py`
- Modify: `backend/apps/agents/models.py`
- Modify: `backend/apps/agents/services/queries.py`
- Modify: `docs/docs/agents.md`
- Test: `backend/apps/agents/tests/test_spec.py`

- [ ] **Step 1: Write failing tests**

In `test_spec.py`, add (use `MINIMAL_SPEC_DICT` pattern already in file):

```python
def test_button_trigger_requires_button_text(self) -> None:
    with self.assertRaises(ValidationError):
        AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'triage',
                        'kind': 'button',
                        'prompt': 'Triage now.',
                    },
                ],
            }
        )

def test_button_trigger_requires_prompt(self) -> None:
    with self.assertRaises(ValidationError):
        AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'triage',
                        'kind': 'button',
                        'button_text': 'Triage',
                    },
                ],
            }
        )

def test_button_trigger_rejects_cron(self) -> None:
    with self.assertRaises(ValidationError):
        AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'triage',
                        'kind': 'button',
                        'button_text': 'Triage',
                        'prompt': 'Triage now.',
                        'cron': '0 * * * *',
                    },
                ],
            }
        )

def test_button_trigger_rejects_overlong_button_text(self) -> None:
    with self.assertRaises(ValidationError):
        AgentConfigSpec.model_validate(
            {
                **MINIMAL_SPEC_DICT,
                'triggers': [
                    {
                        'name': 'triage',
                        'kind': 'button',
                        'button_text': 'x' * 41,
                        'prompt': 'Triage now.',
                    },
                ],
            }
        )

def test_button_max_sessions_defaults_to_none(self) -> None:
    spec = AgentConfigSpec.model_validate(
        {
            **MINIMAL_SPEC_DICT,
            'triggers': [
                {
                    'name': 'triage',
                    'kind': 'button',
                    'button_text': 'Triage',
                    'prompt': 'Triage now.',
                },
            ],
        }
    )
    button = next(t for t in spec.triggers if t.kind == 'button')
    self.assertEqual(button.button_text, 'Triage')
    self.assertIsNone(button.max_sessions)
```

- [ ] **Step 2: Run tests — expect fail**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -k button
```

Expected: FAIL (kind/`button_text` not accepted)

- [ ] **Step 3: Implement schema**

In `TriggerSpec`:

- Extend `kind` Literal with `'button'`
- Add `button_text: str | None = None`
- In `_trigger_defaults`: do **not** force `max_sessions` for button (omit → pydantic `None`; unlike manual which forces `None`)
- In `_kind_specific_fields`:
  - If `kind == 'button'`: require stripped non-empty `button_text` with `len <= 40`; reject if `cron` or `queue` set
  - Prompt rules already require prompt for non-manual — keep that
- Optional: `@field_validator('button_text')` that strips whitespace when set

In `TriggerKind` add `BUTTON = 'button', 'Button'`.

In `queries.py`: add `'button'` to `TRIGGER_KINDS`; add `'button': 'add prompt here'` to `trigger_prompt_defaults`.

Update `docs/docs/agents.md` Triggers section: example YAML entry, kinds table row, fields table (`button_text`; kind enum includes `button`; `max_sessions` defaults note includes button → `null`).

- [ ] **Step 4: Run tests — expect pass**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_spec.py -k button
```

Expected: PASS

- [ ] **Step 5: Commit and sync**

```bash
git add backend/libs/agent_spec/spec.py backend/apps/agents/models.py \
  backend/apps/agents/services/queries.py backend/apps/agents/tests/test_spec.py \
  docs/docs/agents.md
git commit -m "feat: add button trigger kind to agent config schema"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 2: `start_button_session`

**Files:**
- Modify: `backend/apps/runner/start.py`
- Test: `backend/apps/runner/tests/test_start.py` (extend) and/or new focused tests in same file
- Touch only if needed: `backend/apps/runner/scheduling.py` (`trigger_has_capacity` already treats non-schedule/queue default cap as unlimited)

- [ ] **Step 1: Write failing tests**

```python
from apps.runner.start import start_button_session
from apps.agents.models import Trigger, TriggerKind
from apps.sessions.models import AgentSessionStatus
from unittest.mock import patch

# helper: persist agent with manual + button trigger via AgentConfigSpec

@patch('apps.runner.dispatch.push_chat_and_dispatch')
def test_start_button_session_dispatches_prompt(self, mock_push) -> None:
    # create active agent with button trigger "triage" prompt "Do triage."
    session = start_button_session(agent, trigger)
    self.assertEqual(session.trigger_ref, trigger.id)
    mock_push.assert_called_once()
    self.assertEqual(mock_push.call_args.args[1], 'Do triage.')
    trigger.refresh_from_db()
    self.assertIsNotNone(trigger.last_fired_at)

def test_start_button_rejects_wrong_kind(self) -> None:
    # pass manual trigger → StartSessionError mentioning button

def test_start_button_rejects_disabled_agent(self) -> None:
    # AgentStatus.DISABLED → StartSessionError

@patch('apps.runner.start.budget_allows_dispatch', return_value=False)
def test_start_button_rejects_when_budget_blocked(self, _budget) -> None:
    # StartSessionError / budget message

@patch('apps.runner.start.trigger_has_capacity', return_value=False)
def test_start_button_rejects_when_at_capacity(self, _cap) -> None:
    # StartSessionError capacity
```

Also assert button is **not** in automated terminate set (import `_AUTOMATED_TERMINATE_KINDS` from `session_lifecycle` or call `finalize_automated_trigger_session` on a waiting button session and assert status stays `WAITING` — prefer the frozenset membership assert for clarity):

```python
from apps.runner.session_lifecycle import _AUTOMATED_TERMINATE_KINDS
from apps.agents.models import TriggerKind

def test_button_kind_is_not_auto_terminated(self) -> None:
    self.assertNotIn(TriggerKind.BUTTON, _AUTOMATED_TERMINATE_KINDS)
```

- [ ] **Step 2: Run — expect fail**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_start.py -k button
```

- [ ] **Step 3: Implement**

In `start.py`:

```python
def start_button_session(agent: Agent, trigger: Trigger) -> AgentSession:
    """Start a new session from an active button trigger and dispatch its prompt."""
    from apps.runner.budget_gate import budget_allows_dispatch
    from apps.runner.dispatch import push_chat_and_dispatch
    from apps.runner.scheduling import trigger_has_capacity, trigger_prompt

    if agent.status != AgentStatus.ACTIVE:
        raise StartSessionError(f'Agent {agent.identifier!r} is disabled')
    if trigger.kind != TriggerKind.BUTTON:
        raise StartSessionError(f'Trigger {trigger.name!r} is not a button trigger')
    if not budget_allows_dispatch(agent):
        raise StartSessionError(f'Agent {agent.identifier!r} is over budget')
    if not trigger_has_capacity(trigger):
        raise StartSessionError(f'Trigger {trigger.name!r} is at max_sessions capacity')

    session = start_trigger_session(agent, trigger)
    push_chat_and_dispatch(session.id, trigger_prompt(trigger))
    Trigger.objects.filter(pk=trigger.pk).update(last_fired_at=timezone.now())
    return session
```

Assumptions: caller already ensured trigger belongs to agent current config / ACTIVE (also enforced inside `start_trigger_session`).

- [ ] **Step 4: Run — expect pass**

```bash
./olib/scripts/orunr py test backend/apps/runner/tests/test_start.py -k button
```

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/runner/start.py backend/apps/runner/tests/test_start.py
git commit -m "feat: start sessions from button triggers"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 3: Web run endpoint + chatbox buttons

**Files:**
- Modify: `backend/apps/web/views.py`
- Modify: `backend/apps/web/urls.py`
- Modify: `backend/templates/web/partials/chatbox.html`
- Modify: `backend/templates/web/partials/agent_frame_styles.html`
- Prefer query helper in `backend/apps/web/services/queries.py` or `apps/agents` queries — list active button triggers for an agent (hide when agent not `ACTIVE`)
- Test: `backend/apps/web/tests/test_button_triggers.py` (new) and extend `test_chatbox_partial.py` / `test_agent_detail.py` as needed

- [ ] **Step 1: Write failing tests**

```python
# OTransactionTestCase + Client
# Persist agent with button trigger via persist_agent_config / create_from_example + save new config

def test_agent_detail_renders_button_text(self) -> None:
    # GET agent_detail → contains button_text and run URL

def test_session_detail_renders_button_text(self) -> None:
    # GET session_detail → same

def test_run_button_creates_session_and_redirects(self) -> None:
    with patch('apps.runner.dispatch.push_chat_and_dispatch'):
        # POST run URL → 302 to new session; session.trigger_ref == button trigger id

def test_run_button_other_users_agent_not_found(self) -> None:
    # other user → 404

def test_inactive_agent_hides_buttons(self) -> None:
    # DISABLED agent detail → button_text not in response
```

- [ ] **Step 2: Run — expect fail**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_button_triggers.py
```

- [ ] **Step 3: Implement**

1. Query helper e.g. `list_active_button_triggers(agent) -> list[Trigger]`:
   - Empty if agent not `ACTIVE` or no `current_config`
   - Filter `kind=BUTTON`, `status=ACTIVE`, `agent_config=current_config`
   - Order by `id` (UUID7 ≈ materialization/YAML order)

2. `_chatbox_context`: include `button_triggers=list_active_button_triggers(agent)` and ensure `csrf` already available via template.

3. URL: `agents/<uuid:agent_id>/triggers/<uuid:trigger_id>/run/` → name `agent_run_button_trigger`

4. View `agent_run_button_trigger`:
   - `@login_required`, `@csrf_protect`, `@require_POST`
   - `get_owned_agent`
   - Load trigger for agent+id; require `BUTTON` + ACTIVE + current config; else 404/400
   - `start_button_session(agent, trigger)` → redirect `session_detail`; on `StartSessionError` → 400

5. `chatbox.html` — above the form(s):

```html
{% if button_triggers %}
<div class="chatbox-buttons">
  {% for t in button_triggers %}
  <form method="post" action="{{ url('agent_run_button_trigger', kwargs={'agent_id': agent.id, 'trigger_id': t.id}) }}">
    <input type="hidden" name="csrfmiddlewaretoken" value="{{ csrf_token }}">
    <button type="submit" class="frame-btn">{{ t.spec.button_text }}</button>
  </form>
  {% endfor %}
</div>
{% endif %}
```

(Adjust Jinja access if `spec` is dict: `t.spec['button_text']`.)

6. Styles: `.chatbox-buttons { display:flex; flex-wrap:wrap; gap:.5rem; margin-bottom:.5rem; }` and forms `margin:0`.

- [ ] **Step 4: Run — expect pass**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_button_triggers.py backend/apps/web/tests/test_chatbox_partial.py
```

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/web backend/templates/web
git commit -m "feat: render and run button triggers above chatbox"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 4: Config helper — button text field + mutation

**Files:**
- Modify: `backend/apps/agents/services/config_mutations.py`
- Modify: `backend/templates/web/agent_config.html`
- Modify: `backend/apps/web/static/web/agent_config_editor.js`
- Rebuild: `backend/apps/web/static/web/codemirror/agent_config_editor.bundle.js`
- Test: `backend/apps/agents/tests/test_config_mutations.py`

- [ ] **Step 1: Write failing mutation test**

```python
def test_add_button_trigger_includes_button_text(self) -> None:
    raw = dump_agent_config_spec(load_example('clock-assistant'))
    updated = apply_config_mutation(
        raw,
        {
            'action': 'add_trigger',
            'name': 'triage',
            'kind': 'button',
            'button_text': 'Triage inbox',
            'prompt': 'Triage the inbox now.',
        },
    )
    self.assertIn('kind: button', updated)
    self.assertIn('button_text: Triage inbox', updated)
    self.assertIn('prompt: Triage the inbox now.', updated)
```

- [ ] **Step 2: Run — expect fail**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_config_mutations.py -k button
```

- [ ] **Step 3: Implement mutation + UI**

`_trigger_entry`: if `mutation.get('button_text')`: set `entry['button_text']`. For `kind == 'button'`, do **not** substitute `default_trigger_prompt` with a runtime schedule-style default when prompt omitted — either require prompt from the form or use catalog string `add prompt here` only as JS placeholder (prefer: if prompt empty and kind is button, leave unset so validation fails, matching required prompt). Existing code uses `default_trigger_prompt(kind)` which returns `None` for unknown kinds — keep that (no entry in `_DEFAULT_PROMPTS_BY_KIND` for button).

HTML: add

```html
<label id="helper-trigger-button-text-row" hidden>
  Button text
  <input name="button_text" id="helper-trigger-button-text" maxlength="40">
</label>
```

JS `initTriggerHelper` sync:

```javascript
const buttonTextRow = document.getElementById('helper-trigger-button-text-row');
const buttonTextInput = document.getElementById('helper-trigger-button-text');
// ...
const isButton = kind === 'button';
if (buttonTextRow) buttonTextRow.hidden = !isButton;
if (buttonTextInput) buttonTextInput.required = isButton;
if (promptInput) {
  promptInput.required = !isManual;
  promptInput.placeholder = defaults[kind] || '';
}
```

Ensure mutate form posts `button_text` (existing helper form serializes named inputs).

Rebuild editor bundle from `backend/apps/web/static/web` via the project's JS orun command (e.g. `pnpm run build:editor` under that package / `./olib/scripts/orunr js …` — match existing repo practice).

- [ ] **Step 4: Run — expect pass**

```bash
./olib/scripts/orunr py test backend/apps/agents/tests/test_config_mutations.py -k button
```

- [ ] **Step 5: Commit and sync**

```bash
git add backend/apps/agents/services/config_mutations.py \
  backend/apps/agents/tests/test_config_mutations.py \
  backend/templates/web/agent_config.html \
  backend/apps/web/static/web/agent_config_editor.js \
  backend/apps/web/static/web/codemirror/agent_config_editor.bundle.js
git commit -m "feat: config helper support for button triggers"
git fetch origin main && git rebase origin/main && git push
```

---

### Task 5: Verification gate

- [ ] **Step 1: Full Python gate**

```bash
./olib/scripts/orunr py test-all
```

Expected: exit 0

- [ ] **Step 2: If JS roots configured, run JS checks touched by editor**

```bash
./olib/scripts/orunr has js && ./olib/scripts/orunr js test-unit && ./olib/scripts/orunr js lint && ./olib/scripts/orunr js tsc
```

(Skip cleanly if `has js` is false.)

- [ ] **Step 3: Create empty revision stub for human post-impl notes**

Create `docs/specs/2026-08-02-button-triggers/2026-08-02-button-triggers-revision.md` from the template in `docs/specs/01-superpowers/01-superpowers.spec.md` (do not fill as implementer). Commit on the feature branch if required by executor conventions.

- [ ] **Step 4: Commit any remaining docs/fixes if needed; rebase + push**

---

## S_final — Code review (mandatory)

### Task 6: Code review

> **REQUIRED SKILL:** Under `/ship`, the **ship** skill owns this step after implementation returns. Read and follow **`superpowers/requesting-code-review`**. Dispatch a code reviewer subagent using the template at `requesting-code-review/code-reviewer.md`. Write findings to **`*-review.md`**. Ship then auto-fixes actionable findings before PR.

**Files:** (review only — fixes applied by ship after findings)

- [ ] **Step 1: Confirm tests pass**

```bash
./olib/scripts/orunr py test-all
```

- [ ] **Step 2: Get git range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [ ] **Step 3: Run code review** via `requesting-code-review` / `code-reviewer.md`

- [ ] **Step 4: Write** `docs/specs/2026-08-02-button-triggers/2026-08-02-button-triggers-review.md`

- [ ] **Step 5–6:** Ship skill updates Status Fixed/Rejected, re-verifies, opens PR — do not present finish menu from the executor

---

## Out of scope

- Injecting button prompts into the current session
- Icons / colors / confirm dialogs
- Beat scheduling for buttons
- schema_version bump

## Spec coverage check

| Design requirement | Task |
|--------------------|------|
| Schema `button` + `button_text` max 40 + prompt | 1 |
| docs/agents.md | 1 |
| `start_button_session`, budget, capacity, last_fired_at | 2 |
| Not auto-DONE | 2 |
| Chatbox on agent + session; hide when inactive | 3 |
| POST run + redirect new session | 3 |
| Config helper + mutation | 4 |
| Full verification | 5 |
| Code review | 6 / ship S_final |

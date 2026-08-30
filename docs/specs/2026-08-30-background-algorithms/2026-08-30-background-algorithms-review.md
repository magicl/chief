# Background algorithms — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-08-30-background-algorithms-design.md`](./2026-08-30-background-algorithms-design.md)
**Plan:** [`2026-08-30-background-algorithms-plan.md`](./2026-08-30-background-algorithms-plan.md)
**Branch:** `feat/2026-08-30-background-algorithms`
**Review range:** `730ba77e9cb03692f74ca27b80798bd46786d00c..HEAD` (2026-08-30; second pass after retry/lock split)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Owner XOR, usage buckets, Background UI, and in-tool recorder boundary match the design. Naming bills once: llm commits before chat name writes; the chat row is not held across the provider call.

## Strengths

- Owner XOR is enforced in Python and Postgres, with required `user` and frozen owner fields (`backend/apps/sessions/models.py`).
- Spend split: `user_*_spend` uses `user_id`; `agent_*_spend` uses `agent_id`; dual hourly rollup keeps algorithm cost off the chat agent.
- `libs.algorithms` stays Django-free; Celery owns session create/complete.
- Dashboard Background, agent-only recents, algorithm detail, and composer-free algorithm session pages match the plan.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
|   |        |          | None    |       |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/sessions/tasks.py:191-212` | Retry after a successful provider call but failed chat name write still calls `generate_chat_name` again. `_ensure_llm_activity` only skips writing a second llm row. | Reuse existing llm/title; llm commits before name writes. |
| 2 | Fixed | `backend/apps/sessions/services/queries.py:35-44`, `backend/apps/sessions/tasks.py:184-188` | Target identity lives only in JSON `details`. Concurrent retries can create two algorithm sessions before either writes a root span. | `select_for_update` on the chat row before create-or-reuse. |
| 3 | Fixed | `backend/apps/sessions/tasks.py:179-207` | Provider sat in the same `atomic()` as the chat lock and name writes, so a failed name write rolled back the llm row and a retry billed again; the chat row also stalled for LLM RTT. | Chat lock is setup-only. Provider + llm persist commit on the algorithm session before `_finish_naming_run` / `update_session_name`. Test `test_retry_does_not_rebill_when_chat_name_write_fails`. |
| 4 | Fixed | `backend/apps/sessions/tests/test_tasks.py:135-182` | Retry tests never failed a name write after a committed llm row. | New test raises on first `update_session_name` and asserts one llm row. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/sessions/models.py:378-379` | `HourlyUsage.__str__` is still agent-only; algorithm buckets render `None`. | Uses algorithm_id when agent_id is null. |
| 2 | Rejected | `backend/apps/sessions/services/queries.py:108-147` | `child_sessions_for` / `parent_session_breadcrumb` still use `agent__user_id`. Harmless in v1. | Algorithm sessions have no parent/children in v1. |
| 3 | Rejected | `backend/apps/sessions/models.py:14` | Model save imports `get_algorithm`; ARCHITECTURE only mentions tasks/commands. | Save-time registry check belongs on the model. |
| 4 | Fixed | `backend/libs/algorithms/chat_name.py:198-216` | `_sanitize_title` / `_fallback_title` lack purpose docstrings. | |
| 5 | Fixed | `backend/templates/web/session_detail.html:341` | Algorithm session `init` still calls `focusChatInput()` with no composer. | Gated on `show_composer`. |

## Recommendations

- Treat “already has llm/title on the reused algorithm session” as the retry success path.
- Keep JSON `target_session_id` as the operator link, but serialize create/reuse.

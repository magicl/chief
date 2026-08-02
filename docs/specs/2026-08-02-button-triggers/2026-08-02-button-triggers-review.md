# Button triggers — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are fixed or rejected.

**Design:** [`2026-08-02-button-triggers-design.md`](./2026-08-02-button-triggers-design.md)
**Plan:** [`2026-08-02-button-triggers-plan.md`](./2026-08-02-button-triggers-plan.md)
**Branch:** `feat/2026-08-02-button-triggers`
**Review range:** `b717a2f34391a48fce48e49aa08f5d6aab453e55..6ca431fac651d4016ed899721c9d60e6b51a2d81` (2026-08-02)

## Assessment

**Ready to merge?** Yes (after Important fixes)

**Reasoning:** Capacity locking and empty cron/queue rejection addressed; tests cover real capacity and WAITING lifecycle.

## Strengths

- Schema, model enum, docs, UI helper, POST endpoint, and chatbox rendering align with the plan
- Web view delegates to query services; no new ORM in the view
- Buttons limited to active agents/current config; POST + CSRF; new session on click
- `button` not in automated terminate kinds; no Beat wiring

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/runner/start.py` | `max_sessions` checked outside a transaction/row lock; concurrent POSTs can exceed capacity | Locked trigger with `select_for_update`; capacity re-check in atomic block |
| 2 | Fixed | `backend/libs/agent_spec/spec.py` | Button validation only rejects truthy `cron`/`queue`; empty strings accepted | Reject `is not None`; added empty-field tests |
| 3 | Fixed | `backend/apps/runner/start.py` (re-review) | Agent ACTIVE not re-checked under lock after pre-lock budget/status checks | Revalidate `locked.agent.status` inside atomic block |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/runner/tests/test_start.py` | Capacity only mocked; lifecycle only checks frozenset membership | Added real max_sessions=1 test and finalize-stays-WAITING test |
| 2 | Fixed | `backend/apps/runner/start.py` (re-review) | Missing trigger under lock could 500 via DoesNotExist | Map to StartSessionError |
| 3 | Rejected | `backend/apps/runner/tests/test_start.py` (final re-review) | No tests for under-lock disabled/deleted paths | Notes: nice-to-have; pre-lock disabled covered; not blocking |

## Recommendations

- Keep prompt dispatch outside the capacity-lock transaction
- Add concurrency-focused transaction test after locking capacity enforcement

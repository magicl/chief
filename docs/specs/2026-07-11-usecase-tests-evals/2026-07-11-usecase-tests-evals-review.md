# Usecase tests and evals — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-11-usecase-tests-evals-design.md`](./2026-07-11-usecase-tests-evals-design.md)
**Plan:** [`2026-07-11-usecase-tests-evals-plan.md`](./2026-07-11-usecase-tests-evals-plan.md)
**Branch:** `feat/2026-07-11-usecase-tests-evals`
**Review range:** `bbb04c7be16446e3cd76df03669940f902257d70..e34529c0903510352066430f1bcd67a6bbec495d` (2026-07-11)

## Assessment

**Ready to merge?** With fixes

**Reasoning:** Architecture matches the design (SessionRunner-only, test-only mocks, olib eval + hooks). One Important contract mismatch on missing-credential / `--allow-skip` behavior for live matrix runs should be fixed or explicitly deferred before merge.

## Strengths

- `SessionRunner` remains the only agent loop; hooks are runner-owned and failure-isolated
- Mocks injected via `client_factories`, not agent YAML; `user_id=None` path works with noop token supplier
- `olib.py.eval` is Django-free with reusable matrix/log/report primitives
- Functional FakeProvider tests assert mock end-state + event log files
- Live `orunr eval run --suite inbox --model anthropic/claude-sonnet-4-6` scored both samples 1.0 during implementation
- Helpers correctly live under `apps/runner/usecases` (libs must not import apps)

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | | `olib/py/eval/runner.py`, `olib/py/cli/run/templates/eval_.py`, `evals/inbox/runner.py` | Missing credentials raise `RuntimeError` in the sample runner, but `run_matrix` converts all exceptions into failed cells and the CLI only exits nonzero after the matrix. Plan: missing API keys should fail the command unless `--allow-skip`. Today missing keys look like ordinary cell failures and may burn remaining matrix cells. | |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | | `olib/py/cli/run/templates/eval_.py` | `orunr eval report` is a placeholder (“not available yet”); design/plan listed report as part of the CLI surface | |
| 2 | | `backend/apps/runner/tests/usecases/test_inbox_functional.py` | Log partition `kind='usecase'` vs design’s `functional \| eval` | |
| 3 | | `evals/inbox/scenarios/empty-inbox.yaml` | Loose expect (no spam / no tasks) can score a no-op model as perfect | |

## Recommendations

- Fail fast on credential/preflight errors when `--allow-skip` is false (before or outside soft cell-failure conversion)
- Implement or explicitly defer `eval report` in design notes
- Normalize partition `kind` to `functional` for functional tests
- Tighten empty-inbox eval expectations (at least require a list/read tool path)

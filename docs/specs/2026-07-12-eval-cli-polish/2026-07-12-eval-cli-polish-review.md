# Eval CLI polish — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-12-eval-cli-polish-design.md`](./2026-07-12-eval-cli-polish-design.md)
**Plan:** [`2026-07-12-eval-cli-polish-plan.md`](./2026-07-12-eval-cli-polish-plan.md)
**Branch:** `feat/2026-07-12-eval-cli-polish`
**Review range:** `08dd572..3acc73c` (chief) · olib `8dc2135..62fadc2` (2026-07-12)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Polish matches design/plan end-to-end (rich tables, suite models/default, list-models, optional `--model` with skip-on-mismatch, logging observability, discoverable evals tests). Remaining items are minor nits.

## Strengths

- Clear separation: protocol in `types.py`, rich rendering in `report.py`, CLI policy in `eval_.py`
- `_models_for_suite` returns missing ids for clear skip messages
- Mismatch skips without failure cells; skip-only exits 0
- Observability defaults to `logger.info`; stdout quietness covered
- `evals/inbox/tests` package restores unittest discovery

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | | | None | |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | | `olib/py/cli/run/templates/eval_.py` | `eval run` uses `click.echo(format_score_table(...))` without `nl=False`; extra blank line after rich table | |
| 2 | | `olib/py/cli/run/templates/eval_.py` | With `--allow-skip` and no `--model`, suite load miss records cell with `model=''` | |
| 3 | | `olib/py/cli/run/tests/test_eval.py` | Default-model test does not assert `fake/alt` is absent | |
| 4 | | `olib/py/eval/tests/test_report.py` | No direct unit tests for suite/models list formatters (CLI-only coverage) | |
| 5 | | `olib/py/cli/run/tests/test_eval.py` | `list-models --suite` filter untested | |
| 6 | | `olib/py/cli/run/templates/eval_.py` | CLI does not assert `default_model ∈ models()` | |
| 7 | | design status | Was still **implementing** at review time; move to **review** | |

## Recommendations

- Optional: `nl=False` on run echo; strengthen default-model / `--suite` tests; CLI invariant check for default ∈ models
- TSV → rich is an intentional breaking change for consumers

# Eval CLI polish Implementation Plan

Epic: [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md) · Follow-on to usecase tests and evals

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers/subagent-driven-development (recommended) or superpowers/executing-plans to implement this plan task-by-task. `/impl` creates or checks out the declared feature branch before the first code change. Then create `docs/specs/2026-07-12-eval-cli-polish/2026-07-12-eval-cli-polish-revision.md` from the review template in `docs/specs/01-superpowers/01-superpowers.spec.md` — for the human reviewer to fill in **after** implementation; **do not read `-revision.md` during implementation** unless the user explicitly asks (then only check off completed items — no rewrites). Steps use checkbox (`- [ ]`) syntax for tracking. **After all implementation tasks:** REQUIRED — run **S_final** (`superpowers/requesting-code-review` skill).

**Goal:** Rich eval CLI tables, per-suite models/default, list-models + mismatch skip, logging-based observability, and discoverable `evals/` tests.

**Architecture:** Extend `Suite` with `models()` / `default_model`; render tables via rich in `olib/py/eval/report.py`; teach `eval_.py` optional `--model`, skip-on-mismatch, and `list-models`; switch usecase observability from `print` to logging; package `evals/inbox/tests` for unittest discovery.

**Tech Stack:** Python, Click, rich, unittest, Django OTestCase (observability only)

**Branch:** `feat/2026-07-12-eval-cli-polish`

---

## Conventions

- Commands from repo root: `./olib/scripts/orunr …`
- Gate after each stage: `./olib/scripts/orunr py test-all` (or scoped tests while iterating)
- **Git:** implementation on `feat/2026-07-12-eval-cli-polish`; after each stage commit: `git fetch origin main && git rebase origin/main && git push`
- **olib submodule:** code under `olib/` is a git submodule — commit inside `olib/` on its matching feature branch when changing olib files, then update the parent pointer in chief
- **Function documentation:** per `AGENTS.md` — brief docstring on every function/method you write or materially change
- **No compatibility re-exports:** update imports to the new canonical module; delete replaced files — no re-export shims
- **Final task:** code review via **`superpowers/requesting-code-review`**
- Test naming: avoid the words exception/error/warning/deprecated in test method names (parproc)
- Inbox model ids use locked `provider/model` format (see `evals/inbox/runner.py`)

## File map

| File | Role |
|------|------|
| `olib/py/eval/types.py` | Suite protocol: `default_model`, `models()` |
| `olib/py/eval/report.py` | rich table helpers; replace TSV formatters |
| `olib/py/eval/tests/test_report.py` | report unit tests (create if missing) |
| `olib/py/cli/run/templates/eval_.py` | list / list-models / run / report CLI |
| `olib/py/cli/run/tests/test_eval.py` | CLI tests |
| `evals/inbox/suite.py` | inbox models + default |
| `evals/inbox/tests/__init__.py` | package for discovery |
| `backend/apps/runner/usecases/observability.py` | logging instead of print |

---

### Task 1: Suite protocol + rich report helpers (olib)

**Files:**
- Modify: `olib/py/eval/types.py`
- Modify: `olib/py/eval/report.py`
- Create or modify: `olib/py/eval/tests/test_report.py`
- Modify: `olib/py/cli/run/tests/test_eval.py` (fake suites need new members — minimal stub for later tasks OK if Task 3 owns CLI)

- [ ] **Step 1: Extend Suite protocol**

Add to `Suite` in `types.py`:

```python
@property
def default_model(self) -> str:
    """Model id used when eval run omits --model."""
    raise NotImplementedError

def models(self) -> list[str]:
    """Model ids this suite may run against (must include default_model)."""
    raise NotImplementedError
```

- [ ] **Step 2: Rewrite report helpers with rich**

In `report.py`, keep function names `format_score_table` and `format_log_dir_report` returning `str`. Internally build `rich.table.Table` (use `box.SIMPLE` or similar; match olib inspect style lightly) and render with `Console(file=StringIO(), width=120)` (or `record=True`). Columns unchanged:

- score table: Sample | Model | Score | Notes
- log report: Suite | Sample | Model | Score | Notes

FAIL cells still show score `FAIL` and failure text in Notes.

Add a small private helper `_render_table(table: Table) -> str` with a docstring.

- [ ] **Step 3: Tests for report rendering**

Assert rendered output contains column headers and row values (not tab-separated). Cover scored row + FAIL row + empty log dir (headers only).

- [ ] **Step 4: Run tests**

```bash
./olib/scripts/orunr py test ./olib/py/eval/tests/
```

Expected: PASS (olib root). From chief: ensure submodule tests run via configured roots.

- [ ] **Step 5: Commit in olib submodule, then update chief pointer**

```bash
cd olib && git checkout -B feat/2026-07-12-eval-cli-polish
# add/commit
git commit -m "feat(eval): suite models protocol and rich report tables"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
cd .. && git add olib && git commit -m "chore: bump olib for eval rich reports"
```

---

### Task 2: Eval CLI — list-models, optional --model, mismatch skip (olib)

**Files:**
- Modify: `olib/py/cli/run/templates/eval_.py`
- Modify: `olib/py/cli/run/tests/test_eval.py`

- [ ] **Step 1: Update fake suites in CLI tests**

Give `ScoredSuite` / `MixedSuite`:

```python
@property
def default_model(self):
    return 'fake/default'

def models(self):
    return ['fake/default', 'fake/alt']
```

- [ ] **Step 2: Rich `eval list`**

Replace TSV with rich table: suite, name, samples, default, models.

- [ ] **Step 3: Add `eval list-models`**

```python
@eval_group.command(name='list-models')
@click.option('--suite', 'suite_names', multiple=True)
@click.pass_context
def list_models(ctx, suite_names):
    ...
```

Rows: suite key, model, default (yes/no). Filter with `_selected_suite_keys`.

- [ ] **Step 4: `eval run` model resolution + skip**

- `--model` / `models` option: `required=False`, `multiple=True`
- Helper:

```python
def _models_for_suite(suite, requested: list[str]) -> list[str] | None:
    """Return models to run, or None if suite should be skipped."""
    allowed = set(suite.models())
    if not requested:
        return [suite.default_model]
    missing = [m for m in requested if m not in allowed]
    if missing:
        return None
    return list(requested)
```

When `None`, `click.echo(f"Skipping suite {suite_key} due to model mismatch (missing: {', '.join(missing)})")` and continue (no cells).

Print skip messages to stderr or stdout consistently; then print rich `format_score_table(results)`.

Update tests:

- `test_eval_run_requires_model` → becomes “without --model uses default” (assert score table includes `fake/default`)
- new: mismatch skip message and exit 0 when only skip (no cell failures)
- new: list-models output contains suite/model/default

- [ ] **Step 5: Run CLI tests and commit**

```bash
./olib/scripts/orunr py test ./olib/py/cli/run/tests/test_eval.py
# commit in olib + bump parent as in Task 1
```

---

### Task 3: Inbox suite models + evals test package (chief)

**Files:**
- Modify: `evals/inbox/suite.py`
- Create: `evals/inbox/tests/__init__.py`
- Optional test: assert `default_model in suite.models()` in `evals/inbox/tests/test_scorers.py` or new small test

- [ ] **Step 1: Declare inbox models**

```python
DEFAULT_MODEL = 'openai/gpt-4o-mini'
ALLOWED_MODELS = (
    'openai/gpt-4o-mini',
    'openai/gpt-4o',
    'anthropic/claude-sonnet-4-5',
)

@property
def default_model(self) -> str:
    return DEFAULT_MODEL

def models(self) -> list[str]:
    return list(ALLOWED_MODELS)
```

(Adjust ids only if runner/docs already standardize different ones — must match `provider/model`.)

- [ ] **Step 2: Add `evals/inbox/tests/__init__.py`** (empty or docstring-only)

- [ ] **Step 3: Verify discovery**

```bash
.venv/bin/python3 -m unittest discover -s evals -v
./olib/scripts/orunr py test --fast
```

Expect scorer tests to run (not “No tests found in . (evals)”).

- [ ] **Step 4: Commit on chief feature branch**

```bash
git add evals/inbox/suite.py evals/inbox/tests/__init__.py
git commit -m "feat(evals): inbox suite models and discoverable tests"
git fetch origin main && git rebase origin/main && git push -u origin HEAD
```

---

### Task 4: Observability uses logging (chief)

**Files:**
- Modify: `backend/apps/runner/usecases/observability.py`
- Modify: `backend/apps/runner/tests/usecases/test_inbox_functional.py` and/or `usecases/tests/test_setup.py` if needed to assert no stdout

- [ ] **Step 1: Replace default print**

```python
import logging
logger = logging.getLogger(__name__)

def build_observability_hooks(..., print_fn: Callable[[str], None] | None = None) -> HookSet:
    emit = print_fn or (lambda msg: logger.info(msg))
    ...
```

Do not default `print_fn` to `print`.

- [ ] **Step 2: Prove quiet stdout in a functional test**

Capture stdout around `_run_scenario` (or setup test) and assert no `[generate]` / `[event]` lines. Prefer `contextlib.redirect_stdout(io.StringIO())`.

- [ ] **Step 3: Run tests and commit**

```bash
./olib/scripts/orunr py test ./backend/apps/runner/tests/usecases/ ./backend/apps/runner/usecases/tests/
git commit -m "fix(runner): usecase observability logs via logging"
git fetch origin main && git rebase origin/main && git push
```

---

## S_final — Code review (mandatory)

### Task 5: Code review

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

Also note olib submodule range if olib commits are part of the change.

- [ ] **Step 3: Run code review**

Dispatch reviewer with description of eval CLI polish, paths to design+plan, BASE/HEAD SHAs.

- [ ] **Step 4: Write `2026-07-12-eval-cli-polish-review.md` and report**

- [ ] **Step 5: Track feedback** (Fixed / Rejected as user responds)

- [ ] **Step 6: Human handoff** — offer finishing-a-development-branch

---

## Out of scope

- kombu hostname `log_ignore`
- Model metadata objects beyond opaque strings
- New `PyRoot('./evals')`

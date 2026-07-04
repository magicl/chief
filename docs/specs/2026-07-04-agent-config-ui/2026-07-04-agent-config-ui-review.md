# Agent configuration UI — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Epic:** [Inbox cleanup (U1)](../../epics/2026-07-03-inbox-cleanup.md)
**Design:** [`2026-07-04-agent-config-ui-design.md`](./2026-07-04-agent-config-ui-design.md)
**Plan:** *(skipped — implemented from design only)*
**Branch:** `feat/2026-07-04-agent-config-ui`
**Review range:** `08fd9ade8e9a16c1e757bcd64b9b443a66dd2dd4..aef8d9683ac4fb9594b1e57d3780d1df3d87bb53` (2026-07-04)

## Assessment

**Ready to merge?** Yes (after fix commit)

**Reasoning:** Review findings addressed: path containment, file-backed save errors, vendored CodeMirror, helper UI completion, tests, and minor spec alignment items.

## Strengths

- Clean service split: validation, mutations, commands, sync, queries — save goes `validate_agent_config_yaml` → `persist_agent_config` with no `save_spec_from_ui` wrapper.
- Bootstrap removal complete: `hardcoded.py` / `demo_models.py` gone; dashboard and create flows use `libs/agent_specs`.
- Structured validation errors (`ConfigValidationError` + `ValidationErrorItem`) with consistent JSON serialization in views.
- Example specs (`clock-assistant`, `queue-echo`) are well-formed and cover manual + queue scenarios.
- Routes, ownership checks, CSRF on POST endpoints; expanded test coverage (108 tests passing).
- YAML dump uses block style for multiline strings (`system_prompt`).

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/agent_specs/__init__.py` | Path traversal in `load_example_text`. | `_example_path()` resolves and checks `is_relative_to(examples/)`. |
| 2 | Fixed | `backend/apps/agents/services/config_sync.py`, `config_views.py` | File-backed save 500 when bound file unreadable. | `ConfigSyncError` + 400 JSON/flash; UI-only saves unchanged. |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/agents/services/config_commands.py` | `create_from_example` bypassed unified validator. | Uses `validate_agent_config_yaml(load_example_text(slug))`. |
| 2 | Fixed | `backend/apps/agents/tests/test_config_sync.py` | Missing file-sync tests. | Dirty, idempotent sync, missing file, clear source. |
| 3 | Fixed | `backend/templates/web/agent_config.html` | Incomplete helper UI. | System prompt, remove rows, add source, credential pickers. |
| 4 | Fixed | `backend/templates/web/agent_create.html` | Import errors not rendered. | Error list with line numbers. |
| 5 | Fixed | `backend/apps/web/static/web/codemirror/` | CDN CodeMirror. | Vendored esbuild bundle + `npm run build:editor`. |
| 6 | Fixed | `backend/apps/agents/services/config_validation.py` | YAML line numbers stub. | `_yaml_error_line()` from `problem_mark`. |
| 7 | Fixed | `backend/apps/web/config_views.py`, `base.html` | Sync “up to date” message missing. | Django messages flash. |
| 8 | Fixed | `backend/apps/agents/services/config_commands.py` | Stale `dirty` on clear file source. | `clear_file_source()`. |
| 9 | Fixed | `backend/libs/agent_specs/__init__.py` | Duplicate `_EXAMPLES_DIR`. | Removed. |
| 10 | Fixed | tests | Coverage gaps. | Mutate, ownership, YAML create, adapter errors, traversal. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/agents/services/config_sync.py` | `source_rev` format. | `ui-sha256:<hex>` for file-backed saves. |
| 2 | Fixed | `backend/libs/agent_spec/yaml_dump.py` | `sort_keys=False`. | `sort_keys=True`. |
| 3 | Fixed | `backend/apps/agents/services/queries.py` | Shallow autocomplete keys. | Nested `tools[]`, `queues[]`, `triggers[]` paths. |
| 4 | Fixed | `backend/apps/web/urls.py` | `/agents/create/submit/` route. | POST merged into `/agents/create/`. |
| 5 | Fixed | `backend/apps/web/config_views.py` | Mutate ignored ownership. | `_owned_agent()` check added. |
| 6 | Fixed | `backend/templates/web/agent_config_history.html` | `data-yaml` attribute. | JSON script block. |
| 7 | Fixed | `backend/apps/agents/services/config_mutations.py` | Redundant validate+dump. | `validate_agent_config_spec()` once. |

## Recommendations

- Consider adding `@js` to Chief `config.py` so pre-commit `eslint`/`tsc` hooks run on future JS edits.
- Queue/source JSON sub-editor could grow into structured fields per adapter type (follow-on polish).

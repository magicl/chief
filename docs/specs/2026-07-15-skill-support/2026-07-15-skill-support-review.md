# Skill support — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-07-15-skill-support-design.md`](./2026-07-15-skill-support-design.md)
**Plan:** [`2026-07-15-skill-support-plan.md`](./2026-07-15-skill-support-plan.md)
**Branch:** `feat/2026-07-15-skill-support`
**Review range:** `17a553e..5f973f9` (2026-07-16)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All plan deliverables implemented, critical runtime bug (auto-tool invocations rejected by `_is_allowed`) found and fixed with tests added. Full test suite passes (lint, mypy, bandit, unit tests).

## Strengths

- Clean `ToolContext` design — frozen dataclass in `context.py` decouples `libs/tools` from Django
- Dramatic simplification of `tool_wiring.py` — per-tool-type branching replaced by uniform `tool.bind(ctx, inst)` loop
- Faithful design-to-code translation — `ToolContext`, `SkillSpec`, `LoadSkillTool` match the spec
- Sound `SkillSpec` validation — reuses `_INSTANCE_ID_RE`, enforces `min_length=1`, duplicate id detection
- Consistent tool migration — all four tools adopted new signatures mechanically

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `apps/runner/loop.py:231` | Auto-tool invocations rejected by `_is_allowed` — it scans `config_spec.tools` which excludes auto-tools, so `load_skill__load` always returned permission denied | Fixed in `5f973f9`: added `is_auto` flag to `BoundToolInstance`, bypass permission check for auto-tools |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `test_tool_wiring.py`, `test_tool_definitions.py` | Missing auto-tool integration tests from plan's test matrix | Fixed in `5f973f9`: added 5 new test cases |
| 2 | | `tool_wiring.py:46-54` | No guard against auto-tool / explicit-tool ID collision — user could configure `{id: 'load_skill', type: 'clock'}` and auto-tool would overwrite it | Low likelihood; can add validation later |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | | `gmail.py`, `clickup.py`, `test_tool_wiring.py` | Credential-supplier resolution pattern duplicated across tool `bind()` methods | Can extract helper in future when more credential tools are added |
| 2 | | `ingest.py:25`, `queries.py:56`, `base.py:85` | `_DUMMY_CTX` singletons use fake spec values — fragile if future tools inspect `ctx.spec` in `functions()` | Works for current tools; document constraint |

## Recommendations

- Consider validating that explicit tool instance IDs don't collide with auto-tool names (low priority)
- Extract a `resolve_token_supplier` helper when adding more credential tools

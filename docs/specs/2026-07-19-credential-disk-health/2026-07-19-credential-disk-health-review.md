# Credential disk health — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as the user gives feedback.

**Design:** [`2026-07-19-credential-disk-health-design.md`](./2026-07-19-credential-disk-health-design.md)
**Plan:** [`2026-07-19-credential-disk-health-plan.md`](./2026-07-19-credential-disk-health-plan.md)
**Branch:** `feat/2026-07-19-credential-disk-health`
**Review range:** `5484461cb17cbcff19c18d882418bb53fe279953..a1543084af0f6166e670f18e25c4a509c81c4a1a` (2026-07-19)

## Assessment

**Ready to merge?** Yes

**Reasoning:** Implementation matches the design; Important and Minor review findings are fixed.

## Strengths

- Two-stage `parse_key_outcome` matches the design architecture
- Disk `auth_kind` never accepted; covered by dedicated tests
- Centralized health codes; resolve and authorize gating tested
- Ciphertext retention on invalid_declaration/unknown_type
- Secret-safe logging with sentinel tests
- Migration backfill with unit coverage

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | — | — | None | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/providers/key/disk_parse.py` (`parse_key_file`) | Dead duplicate of `parse_key_outcome`; only exercised by its own tests. Delete function and `TestKeyDiskParse` to avoid drift. | Deleted `parse_key_file`; folded coverage into `TestKeyDiskParseOutcome`. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/keys/services/commands.py` (`upsert_user_named_from_disk`) | No-op short-circuit omits explicit health field compare (unlike `upsert_disk_health`). | Short-circuit now includes health fields. |
| 2 | Fixed | `backend/apps/keys/tests/test_disk_sync.py` | Missing explicit recovery test (invalid → fixed YAML → ready). | Added `test_fixed_yaml_recovers_invalid_declaration_to_ready`. |
| 3 | Fixed | `docs/docs/agents.md` | Health prose omits full code list. | Added stable code table. |
| 4 | Fixed | `backend/libs/providers/key/disk_parse.py` | Redundant `with_traceback(None)` call in `parse_key_outcome`. | Simplified raise path. |

## Recommendations

- None remaining after review fixes.

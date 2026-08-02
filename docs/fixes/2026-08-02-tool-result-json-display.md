# Format tool result JSON for display

**Date:** 2026-08-02
**Branch:** `fix/tool-result-json-display`

## Problem

Tool RESULT sections call `formatRawDetails`, which always `JSON.stringify`s the
value. When the stored result is already a JSON string (common for tool output),
the UI shows escaped quotes like `"{\"id\": ...}"` instead of readable JSON.
Plain string results are also wrapped in JSON string quotes (`"..."`) even when
the content is just text.

## Approach

Teach `formatRawDetails` to detect string values that parse as JSON: pretty-print
objects/arrays, and for non-JSON (or JSON primitives) fall back to the raw string
content without surrounding quotes. Existing object/array inputs keep the current
pretty-print path.

## Changes

- `backend/apps/web/static/web/activity_tree.js`: JSON-aware `formatRawDetails`
- `backend/apps/web/static/web/activity_tree.test.js`: coverage for JSON strings,
  arrays, plain text, and invalid JSON fallback

## Verification

- Command: `./olib/scripts/orunr js test-unit`
- Result: pass (includes activity_tree 70 unit tests)
- Command: `./olib/scripts/orunr js lint` / `./olib/scripts/orunr js tsc`
- Result: pass
- Command: `./olib/scripts/orunr py test-all`
- Result: pass

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No Critical / Important / Minor findings from code review.

## Links

- PR: https://github.com/magicl/chief/pull/41

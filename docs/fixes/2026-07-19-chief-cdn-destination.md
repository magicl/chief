# Create Chief CDN destination before upload

**Date:** 2026-07-19
**Branch:** `fix/chief-cdn-destination`

## Problem

The first Chief stage static upload failed with rsync exit code 11 because the
release-specific remote `public/static` directory did not exist.

## Approach

Match the working Floors deployment pattern by creating Chief's remote static
destination before syncing collected backend assets. Keep the existing build and
deployment graph unchanged.

## Changes

- Deployment configuration: create the target CDN directory before rsync.
- Deployment contract test: require directory creation to precede synchronization.

## Verification

- Command: `./olib/scripts/orunr py test`
- Result: pass (946 tests)
- Command: `./olib/scripts/orunr py test-all`
- Result: pass
- Command: `./olib/scripts/orunr js test-unit`
- Result: pass
- Command: `./olib/scripts/orunr js lint`
- Result: pass
- Command: `./olib/scripts/orunr js tsc`
- Result: pass

## Review

| # | Severity | Status | Location | Finding | Notes |
|---|----------|--------|----------|---------|-------|

No findings. Reviewer assessment: ready to merge.

## Links

- PR: https://github.com/magicl/chief/pull/22

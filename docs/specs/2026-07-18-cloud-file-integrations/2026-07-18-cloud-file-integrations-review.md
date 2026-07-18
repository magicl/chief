# Cloud file integrations — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-07-18-cloud-file-integrations-design.md`](./2026-07-18-cloud-file-integrations-design.md)
**Plan:** [`2026-07-18-cloud-file-integrations-plan.md`](./2026-07-18-cloud-file-integrations-plan.md)
**Branch:** `feat/2026-07-18-cloud-file-integrations`
**Review range:** `5d2a8c53667775c3d5e52d12cf19389155cdcca1..5a77e8be0bae73fa77ad0d865d0bdc224e319cdd` (2026-07-18, final pass)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All review findings are fixed and the final full-branch re-review found no remaining Critical, Important, or Minor issues.

## Strengths

- Direct, listed, searched, and resumed results are reauthorized against configured roots.
- Drive root canonicalization and Dropbox provider-authoritative `path_lower` handling are extensively tested.
- Credentials, SDK clients, provider services, retries, and migration conflicts have explicit safety controls.
- Cloud APIs remain metadata-only, with all exposed tool functions marked read-only.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | — | — | None. | |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/tools/tools/google_drive.py:87-118`; `backend/libs/tools/tools/dropbox.py:87-118` | Non-mapping or malformed argument values can escape the safe tool envelope through `TypeError`; validation must also match the published schema’s empty-string semantics. | Validators type-check JSON-like kinds, permit empty queries, and align non-empty refs with schemas. |
| 2 | Fixed | `backend/libs/clients/google_drive/client.py:440-470` | Drive cursor decoding lacks encoded/payload/provider-token bounds and does not contain deeply nested JSON `RecursionError`, allowing untyped resource amplification. | Added strict encoded, decoded, field, collection, and provider-state limits. |
| 3 | Fixed | `backend/libs/clients/google_drive/client.py:553,650,692`; `backend/libs/clients/dropbox/client.py:640,762,841` | Folder/item references and search queries lack practical string bounds and matching JSON-schema limits before provider calls. | Real clients, mocks, and exact tool schemas now share practical limits. |
| 4 | Fixed | `backend/libs/clients/dropbox/client.py:865-879,910-928` | Dropbox search aborts with `outside_root` for stale or over-returned candidates instead of discarding them as required by bounded post-filtering. | Candidate-scoped outside-root failures are discarded while other failures propagate. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/libs/clients/google_drive/client.py:614-625` | Drive folder listing trusts provider page-size limits and can return or process an unbounded over-returned page. | Overruns are bounded and buffered by ID; resume refetches and reauthorizes each item. |
| 2 | Fixed | `backend/libs/clients/google_drive/client.py:423-430,533-540` | Drive can encode a provider token larger than its own resume limit, producing a cursor that the next call rejects. | Search cursor encoding now enforces the same provider-token bound as decoding. |
| 3 | Fixed | `backend/libs/clients/google_drive/client.py:905-929` | Drive search does not apply the provider-page entry bound before processing over-returned candidates. | Oversized pages fail before candidate ancestry processing. |
| 4 | Fixed | `backend/libs/clients/google_drive/mock.py:147-166,216-239` | Mock folder pagination still uses positional offsets and can skip children moved between pages instead of mirroring production pending-ID cursors. | Folder cursors now buffer bounded ordered IDs and reauthorize each resumed item. |

## Recommendations

- Track pre-existing Gmail traceback credential retention separately; it predates this branch.

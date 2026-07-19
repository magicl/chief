# Chief rich content rendering — Code Review

> Agent-generated from `superpowers/requesting-code-review`. Update **Status** as findings are resolved.

**Design:** [`2026-07-18-rich-content-rendering-design.md`](./2026-07-18-rich-content-rendering-design.md)
**Plan:** [`2026-07-18-rich-content-rendering-plan.md`](./2026-07-18-rich-content-rendering-plan.md)
**Branch:** `feat/868kdvye9-rich-content-rendering`
**Review range:** `3ab4cf3f69ad271549b482a12159a4428d24d2da..beebf153661515b2295c4d52e2e773d2be653c26` (2026-07-18)

## Assessment

**Ready to merge?** Yes

**Reasoning:** All findings are fixed and verified. Mermaid navigation directives, including plural sequence-diagram `links`, are rejected before rendering independent of destination; dangerous browser-handler schemes are also blocked while ordinary diagrams continue to render.

## Strengths

- Raw Markdown HTML is disabled, KaTeX trust remains off, Mermaid uses strict security, and HTML/SVG are sanitized separately.
- Generation tokens, cancellation, readiness events, and attempt ownership handle asynchronous races cleanly.
- OUTPUT-only routing, exact source views, local fallbacks, accessible toggle state, and read-only Compose mounting align with the approved design.
- The pinned external build is reproducible and generated assets remain outside Git.

## Issues

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| — | Fixed | | None. | No critical findings were reported. |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/static/web/rich_content.js:18` | Markdown and sanitized SVG permit more external resource schemes and elements than intended, allowing untrusted output to trigger remote requests or non-web handlers. Restrict anchors to intended web schemes, disable Markdown images, and permit only safe same-document SVG references. | Explicit HTTP(S)-only anchors, inert image source, and post-DOMPurify SVG resource neutralization now have focused regressions. |
| 2 | Fixed | `config.py:154` | Generated assets are delivered only through the Compose NFS mount, while a separate unimplemented hosting design proposes immutable static images without NFS. Add hosted delivery or formally document that integration as deferred to that hosting effort. | Design and plan now define Compose-only delivery and require future immutable hosting to copy assets into its image without NFS and verify hosted URLs. |
| 3 | Fixed | `backend/apps/web/static/web/rich_content.test.js:211` | Mocks and string contracts do not exercise the built split bundle with the real Mermaid runtime and readiness/toggle lifecycle. Add an integration smoke test that loads built assets and covers real Mermaid rendering plus lifecycle behavior. | Vitest Browser Mode builds and loads the actual split ESM output in Chromium, exercising readiness, lifecycle replacement, Markdown, KaTeX, and real Mermaid markers. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `docs/specs/2026-07-18-chief-dev-hosting/2026-07-18-chief-dev-hosting-design.md:1` | An unrelated hosting design from another ticket and branch is included in this feature range. Remove it from this PR. | Removed from this branch's final diff. |
| 2 | Fixed | `backend/apps/web/static/web/vitest.config.js:9` | The stale pre-test comment and `passWithNoTests: true` can conceal accidental test removal. Remove both now that tests exist. | Removed both and constrained the fast unit project to its maintained test file. |

## Post-fix re-review

**Review range:** `bcaf690d1fabb7e4c9c3ff77de319b19fe3fdbc6..f0e06debeefe8b212fb2f0613f6cddb584681c9c` (2026-07-18)

### Critical

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/static/web/rich_content.js:91` | Mermaid configuration directives and URL-bearing styles can initiate network requests during `mermaid.render()`, before returned SVG sanitization. Reject network-capable source before rendering, lock relevant configuration, and add a real-browser no-request regression. | Fail-closed preflight blocks configuration, style/link/image/resource forms before `mermaid.render`; secure keys are locked and the Chromium server probe records zero CSS/image requests. |

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/static/web/build_rich_content.mjs:96` | Immediate stale hashed-chunk deletion can break cached fixed entries or existing pages that request an old lazy chunk. Retain old dependencies beyond the entry cache lifetime before garbage collection. | Unreferenced hashed dependencies remain for 24 hours; recent/aged coverage, repeat inventory, entry-last ordering, and live inode checks pass. |

### Minor

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/static/web/vitest.browser.config.js:17` | Browser-test cleanup is registered only after a successful isolated build, so build failure can leave its temporary directory. Register cleanup before building or use `try/finally`. | Shared temporary-build setup registers cleanup before execution and has explicit synchronous build-failure coverage. |
| 2 | Fixed | `backend/apps/web/static/web/tsconfig.json:14` | The publication-safety implementation in `build_rich_content.mjs` is excluded from JavaScript type checking. Include `*.mjs` and required Node types. | `*.mjs` and Node types are included; the unscoped repository TypeScript gate passes all configured roots. |

## Final security re-review

**Review range:** `bcaf690d1fabb7e4c9c3ff77de319b19fe3fdbc6..a567eef` (2026-07-19)

### Important

| # | Status | Location | Finding | Notes |
|---|--------|----------|---------|-------|
| 1 | Fixed | `backend/apps/web/static/web/rich_content.js:165` | Mermaid's plural sequence-diagram `links` directive bypasses the singular `link` preflight, and navigation schemes such as `javascript:` are omitted from the denylist. Reject all navigation directives independent of destination and cover dangerous schemes before rendering. | Exact valid `links A: {"bad":"javascript:alert(1)"}` coverage preserves source and proves Mermaid is not called; singular/navigation cases, expanded schemes, real Chromium zero-request smoke, and normal arrow/marker rendering all pass. |

## Recommendations

- Clean stale hashed chunks from repeated external builds without disrupting the live read-only mount.
- Consider cache-busting the fixed entry JS/CSS filenames when hosted delivery is implemented.

The build recommendation is fixed with staged per-file publication, fixed
entries published last, 24-hour stale-dependency retention, aged cleanup, and
inode/repeat-build coverage.
Cache-busting remains deferred to future hosted delivery; Compose Nginx uses a
300-second maximum age.

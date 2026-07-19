# Chief Rich Content Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers/subagent-driven-development` (recommended) or
> `superpowers/executing-plans` to implement this plan task-by-task. `/impl`
> creates or checks out the declared feature branch before the first code
> change. Then create
> `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-revision.md`
> from the review template in `olib/docs/specs/01-superpowers/01-superpowers.spec.md`;
> do not read that file during implementation unless the user asks. Steps use
> checkbox syntax for tracking. After all implementation tasks, run mandatory
> S_final through `superpowers/requesting-code-review`.

**Goal:** Render agent OUTPUT events as sanitized Markdown with Mermaid and
KaTeX, with a page-local, default-on Beautify toggle that reveals exact source
on demand.

**Architecture:** A focused browser module parses and sanitizes untrusted model
output, then hydrates Mermaid diagrams asynchronously. The existing Alpine
session component controls presentation state while SSE payloads, backend
services, and persisted events remain unchanged.

**Tech Stack:** Alpine.js, markdown-it, `@vscode/markdown-it-katex`, KaTeX,
Mermaid, DOMPurify, esbuild, Vitest/jsdom, Vitest Browser Mode with Playwright
and Chromium, Django/Jinja templates.

**Branch:** `feat/868kdvye9-rich-content-rendering`

**ClickUp:** https://app.clickup.com/t/868kdvye9

---

## Conventions

- Commands run from the repository root with `./olib/scripts/orunr …`.
- Use TDD: add a focused failing test, observe the expected failure, implement
  the smallest behavior, then rerun the focused test.
- Required final gates: `./olib/scripts/orunr py test-all`,
  `./olib/scripts/orunr js test-unit`, `./olib/scripts/orunr js lint`, and
  `./olib/scripts/orunr js tsc`.
- Git: this plan is committed on `main`; implementation uses the exact branch
  above. After each PR-ready feature commit, run
  `git fetch origin main && git rebase origin/main && git push`.
- Every new or materially changed function gets a brief docstring or leading
  comment describing purpose and non-obvious assumptions.
- Do not add compatibility re-export files. Update canonical call sites
  directly and delete replaced files if any.
- Django tests use `OTestCase`, `OTransactionTestCase`, or
  `OLiveServerTestCase`, never bare `unittest.TestCase`.
- Test names and Vitest titles avoid the log-highlight words listed in
  `AGENTS.md`.
- Do not add license headers; repository hooks own them.
- Final review uses `superpowers/requesting-code-review`; Cursor Bugbot is not
  part of this workflow.

## File map

| Path | Responsibility |
|------|----------------|
| `backend/apps/web/static/web/rich_content.js` | Parse, sanitize, render formulas, hydrate Mermaid, and suppress stale async results |
| `backend/apps/web/static/web/rich_content.test.js` | Browser-module unit tests with mocked Mermaid rendering |
| `backend/apps/web/static/web/rich_content.browser.test.js` | Real split-bundle smoke in headless Chromium |
| `backend/apps/web/static/web/build_rich_content.mjs` | Staged publication that preserves the live bind-mount directory |
| `backend/apps/web/static/web/temporary_build.mjs` | Browser-test temporary lane with setup-failure cleanup |
| `backend/apps/web/static/web/package.json` | Runtime packages plus build/test scripts |
| `backend/apps/web/static/web/pnpm-lock.yaml` | Exact pnpm dependency graph |
| `backend/apps/web/static/web/package-lock.json` | Remove the superseded npm dependency graph |
| `backend/apps/web/static/web/tsconfig.json` | Type-check new source and tests while excluding generated assets |
| `/mnt/infra-assets/chief/js/gen/` | External generated JS/CSS/legal/font lane; never committed to Git |
| `backend/templates/web/base.html` | Add an overridable head-assets block |
| `backend/templates/web/session_detail.html` | Load assets, route OUTPUT display, and own Beautify state |
| `backend/templates/web/partials/agent_frame_styles.html` | Toolbar and rich-output layout/theme styles |
| `backend/apps/web/tests/test_session_dialog.py` | Server-rendered integration contract for assets and toggle |
| `config.py` | Resolve the external assets lane and register the rich-content build job |
| `infra/docker/docker-compose.yml` | Mount generated rich-content assets into static Nginx read-only |
| `docs/specs/2026-07-18-rich-content-rendering/*-revision.md` | Empty human review scaffold created before code changes |
| `docs/specs/2026-07-18-rich-content-rendering/*-review.md` | Agent final-review findings created during S_final |

---

### Task 1: Start implementation state and JavaScript test/build surface

**Files:**
- Create: `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-revision.md`
- Modify: `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-design.md`
- Modify: `backend/apps/web/static/web/package.json`
- Create: `backend/apps/web/static/web/pnpm-lock.yaml`
- Delete: `backend/apps/web/static/web/package-lock.json`
- Modify: `backend/apps/web/static/web/tsconfig.json`
- Modify: `config.py`

- [x] **Step 1: Create the feature branch/worktree and workflow scaffold**

Create or enter the worktree on
`feat/868kdvye9-rich-content-rendering`. Apply design status
`implementing` through `managing-active`, then create the revision file exactly
as:

```markdown
# Chief Rich Content Rendering — Implementation Review

> For the reviewer. Created before implementation; fill in after reviewing the completed work.
> Implementers follow `-plan.md` only — do not read this file unless the user asks.

## Review notes

<!-- Corrections, gaps, and follow-ups discovered while reviewing the implementation. -->

## Items to address

- [ ]   <!-- Implementer: mark [x] only when this item is done. Do not edit other sections. -->
```

Do not read the revision file again during implementation.

- [x] **Step 2: Add pinned renderer and test dependencies**

Use the package manager so current compatible versions and integrity hashes are
recorded rather than hand-editing versions:

```bash
pnpm --dir backend/apps/web/static/web add markdown-it @vscode/markdown-it-katex mermaid dompurify
pnpm --dir backend/apps/web/static/web add --save-exact katex@0.16.47
pnpm --dir backend/apps/web/static/web add --save-dev vitest jsdom
```

Update `package.json` scripts to retain the existing editor build and add:

```json
{
  "scripts": {
    "build": "pnpm run build:editor && pnpm run build:rich-content",
    "build:editor": "esbuild agent_config_editor.js --bundle --format=esm --outfile=codemirror/agent_config_editor.bundle.js --minify",
    "build:rich-content": "esbuild rich_content.js --bundle --format=esm --splitting --outdir=${CHIEF_RICH_CONTENT_OUTDIR:-/mnt/infra-assets/chief/js/gen} --entry-names=rich_content.bundle --chunk-names=assets/[name]-[hash] --asset-names=assets/[name]-[hash] --loader:.woff=file --loader:.woff2=file --loader:.ttf=file --legal-comments=external --minify",
    "test": "vitest run",
    "lint": "eslint . --fix"
  }
}
```

- [x] **Step 3: Include source/tests and exclude generated assets**

Update `tsconfig.json`:

```json
{
  "compilerOptions": {
    "allowJs": true,
    "checkJs": true,
    "noEmit": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": false,
    "skipLibCheck": true,
    "types": ["vitest/globals"]
  },
  "include": ["*.js", "*.d.ts"],
  "exclude": ["node_modules", "codemirror", "rich-content"]
}
```

Generated rich-content assets are outside the repository under
`/mnt/infra-assets/chief/js/gen`, so only source and tests participate in the
configured JavaScript checks.

Delivery in this plan is Compose-only. A separate future immutable-image
hosting effort must build or copy these assets into its static image without
NFS and add hosted URL verification. It may also add cache-busted fixed entry
URLs; current Compose Nginx uses a 300-second maximum age.

- [x] **Step 4: Verify the empty JavaScript suite is wired**

```bash
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js tsc
```

Expected: both exit 0; Vitest reports no failing tests and type-checking includes
the new top-level JS test path.

---

### Task 2: Build the safe rich-content renderer

**Files:**
- Create: `backend/apps/web/static/web/rich_content.js`
- Create: `backend/apps/web/static/web/rich_content.test.js`
- Modify: `config.py`
- Modify: `infra/docker/docker-compose.yml`
- Generate externally: `/mnt/infra-assets/chief/js/gen/rich_content.bundle.js`
- Generate externally: `/mnt/infra-assets/chief/js/gen/rich_content.bundle.css`
- Generate externally: `/mnt/infra-assets/chief/js/gen/assets/*`

- [x] **Step 1: Write failing parsing and safety tests**

Create jsdom Vitest coverage with explicit imports:

```javascript
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (id, source) => ({
      svg: `<svg><text>${source}</text></svg>`,
    })),
  },
}));

import {
  cancelRichContent,
  initializeRichContent,
  renderRichContent,
} from './rich_content.js';

describe('rich output rendering', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="target"></div>';
    initializeRichContent();
  });

  it('renders markdown and dollar formulas', async () => {
    const target = document.querySelector('#target');
    await renderRichContent(target, '# Heading\n\nInline $x^2$.\n\n$$y=2$$');
    expect(target.querySelector('h1')?.textContent).toBe('Heading');
    expect(target.querySelectorAll('.katex').length).toBeGreaterThan(0);
  });

  it('removes raw markup and unsafe links', async () => {
    const target = document.querySelector('#target');
    await renderRichContent(
      target,
      '<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))\n\n[good](https://example.com)',
    );
    expect(target.querySelector('img')).toBeNull();
    expect(target.innerHTML).not.toContain('javascript:');
    expect(target.querySelector('a')?.getAttribute('target')).toBe('_blank');
    expect(target.querySelector('a')?.getAttribute('rel')).toBe('noopener noreferrer');
  });
});
```

Add tests for fenced code remaining code, Mermaid success, Mermaid rejection
preserving source with `.rich-render-failure`, formula rejection preserving its
source, cancellation while a diagram is pending, and a stale render losing to a
newer generation. Cover Mermaid SVG fragment references after
`SANITIZE_NAMED_PROPS`: URL attributes, fragment `href`/`xlink:href`, and ARIA
IDREF lists must resolve to the corresponding sanitized IDs.

- [x] **Step 2: Run tests and observe the missing module**

```bash
./olib/scripts/orunr js test-unit
```

Expected: FAIL because `rich_content.js` or its exports do not exist.

- [x] **Step 3: Implement parser initialization and link policy**

Create `rich_content.js` with documented public functions and module-private
helpers. Initialize Mermaid once:

```javascript
import MarkdownIt from 'markdown-it';
import markdownItKatex from '@vscode/markdown-it-katex';
import DOMPurify from 'dompurify';
import mermaid from 'mermaid';
import 'katex/dist/katex.min.css';

let initialized = false;
const generations = new WeakMap();

/** Configure Mermaid once for untrusted, manually rendered diagrams. */
export function initializeRichContent() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize'],
    theme: 'dark',
    suppressErrorRendering: true,
  });
  initialized = true;
}
```

Build one MarkdownIt instance with `html: false`, `linkify: true`, and
`typographer: false`; install `markdownItKatex` with `throwOnError: false`. Wrap
the `link_open` rule so every accepted link receives:

```javascript
token.attrSet('target', '_blank');
token.attrSet('rel', 'noopener noreferrer');
```

Keep MarkdownIt's default URL validation, then sanitize parsed markup with:

```javascript
DOMPurify.sanitize(parsedHtml, {
  USE_PROFILES: { html: true },
  SANITIZE_NAMED_PROPS: true,
});
```

- [x] **Step 4: Implement inert Mermaid placeholders and local degradation**

Override only the `fence` renderer when `token.info.trim() === 'mermaid'`.
Return an inert wrapper containing escaped source in `<pre><code>` and a
generated `data-mermaid-index`; delegate every other fence to the original
renderer.

After sanitized HTML insertion, process each placeholder serially with
`mermaid.render`. Sanitize returned SVG separately:

```javascript
const safeSvg = DOMPurify.sanitize(svg, {
  USE_PROFILES: { svg: true, svgFilters: true },
  SANITIZE_NAMED_PROPS: true,
});
```

On rejection, leave the original source `<pre>` and append:

```html
<span class="rich-render-failure" role="status">Diagram could not be rendered</span>
```

Find `.katex-error` nodes after HTML insertion and append a nearby
`Formula could not be rendered` indicator without removing the visible source.
Never log source text.

- [x] **Step 5: Suppress stale asynchronous writes**

Implement the public renderer as:

```javascript
/** Render untrusted Markdown into one event container, discarding stale async work. */
export async function renderRichContent(target, source) {
  initializeRichContent();
  const generation = (generations.get(target) || 0) + 1;
  generations.set(target, generation);

  try {
    const safeHtml = renderSafeMarkdown(source);
    if (generations.get(target) !== generation) return false;
    target.innerHTML = safeHtml;
    markFormulaFailures(target);
    await hydrateMermaid(target, generation);
    return generations.get(target) === generation;
  } catch {
    if (generations.get(target) === generation) {
      target.replaceChildren(sourceFallback(source));
    }
    return false;
  }
}
```

`hydrateMermaid` checks the generation both before and after every awaited
render. `sourceFallback` creates DOM nodes and assigns `textContent`; it does
not construct fallback HTML from source.

Export a cancellation helper used when Beautify turns off:

```javascript
/** Invalidate pending asynchronous work for one rich output element. */
export function cancelRichContent(target) {
  generations.set(target, (generations.get(target) || 0) + 1);
}
```

- [x] **Step 6: Run focused tests and build assets**

```bash
./olib/scripts/orunr js test-unit
./olib/scripts/orun job js.rich-content-build
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: renderer tests pass; the job resolves `js_gen`, prepares the static-web
pnpm runtime, and emits split JS/CSS/legal/font assets under
`/mnt/infra-assets/chief/js/gen`; lint/type checks exit 0; and `git status`
contains no generated rich-content output.

The automated delivery contracts parse Compose and assert the exact read-only
`chief-static` mount. A JavaScript smoke test builds into a temporary
`CHIEF_RICH_CONTENT_OUTDIR`, verifies entry bundles plus split chunks, legal
files, and fonts, then removes the temporary output.

The build stages all output outside the destination, publishes hashed
dependencies by per-file atomic rename, publishes fixed entry files last, and
removes obsolete fixed/root artifacts immediately. Unreferenced hashed
dependencies under `assets/` are retained for 24 hours before collection so
cached entries and existing sessions can still lazy-load their chunks. It must
preserve the inode of `/mnt/infra-assets/chief/js/gen` because Docker
bind-mounts that directory.

Register `@assets(app_name='chief', asset_paths={'js': '/js'})`, make
`TargetInfo` include `AssetsClusterInfo`, and add a documented
`js.rich-content-build` proto depending on `assets.ensure::compose`. The proto
validates `AssetsPathsResult`, uses its `js_gen` path as
`CHIEF_RICH_CONTENT_OUTDIR`, prepares the configured static-web JavaScript
runtime, and runs `pnpm run build:rich-content`.

Add `js.rich-content-build` to `docker.compose-deps`. Keep collectstatic and the
external build as independent dependencies because static Nginx mounts
`/mnt/infra-assets/chief/js/gen` directly at
`/etc/storage/public/static/web/rich-content:ro`.

---

### Task 3: Integrate OUTPUT-only rendering and the Beautify control

**Files:**
- Modify: `backend/templates/web/base.html`
- Modify: `backend/templates/web/session_detail.html`
- Modify: `backend/templates/web/partials/agent_frame_styles.html`
- Modify: `backend/apps/web/tests/test_session_dialog.py`

- [x] **Step 1: Write failing Django template-contract tests**

Extend `TestSessionEventView` using its existing authenticated setup:

```python
def test_session_page_loads_rich_output_assets(self) -> None:
    """The session page loads the pinned rich renderer bundle and styles."""
    response = self.client.get(
        reverse('session_detail', kwargs={'session_id': self.session.id}),
    )
    self.assertContains(response, 'web/rich-content/rich_content.bundle.js')
    self.assertContains(response, 'web/rich-content/rich_content.bundle.css')

def test_session_page_has_default_on_beautify_control(self) -> None:
    """Beautification starts on and exposes accessible pressed state."""
    response = self.client.get(
        reverse('session_detail', kwargs={'session_id': self.session.id}),
    )
    self.assertContains(response, 'beautify: true')
    self.assertContains(response, ':aria-pressed="beautify.toString()"')
    self.assertContains(response, 'toggleBeautify')

def test_session_page_routes_only_outputs_to_rich_renderer(self) -> None:
    """Only immutable agent output content enters the HTML renderer."""
    response = self.client.get(
        reverse('session_detail', kwargs={'session_id': self.session.id}),
    )
    self.assertContains(response, "evt.kind === 'OUTPUT'")
    self.assertContains(response, 'renderOutput')
    self.assertContains(response, 'x-text="formatPayload(evt)"')
```

Also assert that neither `localStorage` nor `sessionStorage` appears in the
Beautify implementation.

- [x] **Step 2: Run the focused Django test and observe failure**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_session_dialog.py
```

Expected: FAIL because assets, state, and toggle markup are absent.

- [x] **Step 3: Add a head-assets extension point**

In `backend/templates/web/base.html`, add this block after Alpine/htmx scripts
and before the inline style:

```jinja
{% block head_assets %}{% endblock %}
```

In `session_detail.html`, load the generated CSS:

```jinja
{% block head_assets %}
<link rel="stylesheet" href="{{ static('web/rich-content/rich_content.bundle.css') }}">
{% endblock %}
```

Load the module before the session's inline component script:

```jinja
<script type="module" src="{{ static('web/rich-content/rich_content.bundle.js') }}"></script>
```

The bundle assigns only its documented entry points to
`window.chiefRichContent` so Alpine's classic inline script can call it.

- [x] **Step 4: Add the Beautify toolbar control and dual OUTPUT view**

Wrap toolbar actions so Beautify and Follow share the top-right:

```html
<div class="event-toolbar-actions">
  <button
    type="button"
    class="frame-btn"
    :class="{ 'active': beautify }"
    :aria-pressed="beautify.toString()"
    @click="toggleBeautify()"
    x-text="beautify ? 'Beautify: On' : 'Beautify: Off'"
  ></button>
  <!-- existing Follow button -->
</div>
```

Replace the single event body with explicit branches:

```html
<template x-if="evt.kind === 'OUTPUT'">
  <div>
    <div
      class="event-body rich-output"
      x-show="beautify"
      x-effect="beautify && renderOutput($el, evt)"
    ></div>
    <pre
      class="event-body"
      x-show="!beautify"
      x-text="formatPayload(evt)"
    ></pre>
  </div>
</template>
<template x-if="evt.kind !== 'OUTPUT'">
  <pre class="event-body" x-text="formatPayload(evt)"></pre>
</template>
```

Add `beautify: true`, a `WeakSet` of rendered elements that avoids redundant
renders, and documented methods:

```javascript
/** Toggle presentation only; raw event payloads remain unchanged. */
toggleBeautify() {
  this.beautify = !this.beautify;
  if (!this.beautify) {
    document.querySelectorAll('.rich-output').forEach((element) => {
      window.chiefRichContent.cancelRichContent(element);
    });
  }
},
/** Render one OUTPUT element through the isolated rich-content module. */
renderOutput(element, event) {
  return window.chiefRichContent.renderRichContent(
    element,
    this.formatPayload(event),
  );
},
```

Do not read or write browser storage. Reconnect resets event elements normally;
page reload reconstructs `beautify: true`.

- [x] **Step 5: Add contained dark-theme styling**

Add `.event-toolbar-actions` flex styling and rich content rules to
`agent_frame_styles.html`. Cover headings, paragraph/list spacing, blockquotes,
 links, code/pre, table overflow, KaTeX overflow, responsive SVG, source
fallback, and `.rich-render-failure`. Preserve `.event-body` source styling by
moving monospace-only properties to `.event-body:not(.rich-output)` where
needed.

Required containment rules include:

```css
.event-toolbar-actions { display: flex; align-items: center; gap: .5rem; }
.rich-output { min-width: 0; overflow-wrap: anywhere; }
.rich-output > :first-child { margin-top: 0; }
.rich-output > :last-child { margin-bottom: 0; }
.rich-output pre,
.rich-output .katex-display,
.rich-output .table-wrap { overflow-x: auto; }
.rich-output svg { display: block; max-width: 100%; height: auto; margin: 0 auto; }
.rich-render-failure { display: block; margin-top: .35rem; color: #ffb4b8; font-size: .78rem; }
```

- [x] **Step 6: Run focused and full integration checks**

```bash
./olib/scripts/orunr py test backend/apps/web/tests/test_session_dialog.py
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: all commands exit 0.

---

### Task 4: Verify the complete feature and commit the PR-ready chunk

**Files:**
- Verify all files from Tasks 1–3

- [x] **Step 1: Rebuild external static assets**

```bash
./olib/scripts/orun job js.rich-content-build
test -f /mnt/infra-assets/chief/js/gen/rich_content.bundle.js
test -f /mnt/infra-assets/chief/js/gen/rich_content.bundle.css
git status --short
```

Expected: split bundles, legal files, and KaTeX fonts exist under the external
generated lane. Git reports only source, configuration, Compose, tests, and
spec changes—never generated rich-content output.

- [x] **Step 2: Run all repository quality gates**

```bash
./olib/scripts/orunr py test-all
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: every command exits 0.

- [x] **Step 3: Perform a security-focused source check**

Confirm:

- Markdown raw HTML is disabled.
- DOMPurify runs before Markdown HTML insertion and before Mermaid SVG insertion.
- Mermaid uses `securityLevel: 'strict'` with secure config keys.
- no code path interpolates untrusted source into HTML strings except the
  Markdown parser followed by sanitization;
- link targets and relations are set centrally; and
- rendering rejections cannot escape into the SSE callback.

- [x] **Step 4: Commit, rebase, and push the implementation**

Stage only the spec artifacts and planned implementation files:

```bash
git add config.py \
  backend/apps/web/static/web \
  backend/apps/web/tests/test_session_dialog.py \
  backend/templates/web/base.html \
  backend/templates/web/session_detail.html \
  backend/templates/web/partials/agent_frame_styles.html \
  docs/specs/2026-07-18-rich-content-rendering
git commit -m "feat: render rich agent output safely"
git fetch origin main
git rebase origin/main
git push -u origin HEAD
```

If rebase reports conflicts, stop and ask the human rather than resolving or
force-pushing.

---

## Post-fix security and delivery hardening

- Mermaid source is rejected before `mermaid.render()` when it contains
  configuration/frontmatter, style/class/link/click directives, image syntax,
  explicit or relative resource URLs, dangerous schemes, or CSS-escaped
  `url(...)`. Rejection preserves source and shows the local diagram status.
- Mermaid secure configuration additionally locks theme CSS/variables and
  font/HTML-label settings. The real Chromium smoke uses a server-side request
  probe that records CSS and image loads and requires zero malicious requests.
- Build publication retains stale hashed dependencies for 24 hours, removes
  aged dependencies and obsolete root entries, publishes fixed entries last,
  and preserves the bind-mounted output inode.
- Browser temporary-lane cleanup is registered before the build callback and is
  covered on setup failure. TypeScript `checkJs` includes `*.mjs` with Node
  types. On a clean checkout, run `./olib/scripts/orun init --js` before the
  unscoped JavaScript gates so every configured root has dependencies.

---

## S_final — Code review (mandatory)

### Task 5: Code review

> **REQUIRED SKILL:** Read and follow
> `superpowers/requesting-code-review`. Dispatch a code reviewer subagent using
> `requesting-code-review/code-reviewer.md`. Review the feature branch against
> this plan and the design. Write findings to
> `2026-07-18-rich-content-rendering-review.md`. Under `/ship`, return findings
> to the ship orchestrator, which fixes all actionable items before opening the
> PR.

**Files:**
- Create: `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-review.md`

- [x] **Step 1: Confirm final gates pass**

```bash
./olib/scripts/orunr py test-all
./olib/scripts/orunr js test-unit
./olib/scripts/orunr js lint
./olib/scripts/orunr js tsc
```

Expected: all exit 0.

- [x] **Step 2: Compute the review range**

```bash
git fetch origin main
BASE_SHA=$(git merge-base HEAD origin/main)
HEAD_SHA=$(git rev-parse HEAD)
echo "Review range: $BASE_SHA..$HEAD_SHA"
```

- [x] **Step 3: Dispatch the code reviewer**

Use:

- Description: sanitized Markdown, KaTeX, and Mermaid OUTPUT rendering with a
  page-local Beautify toggle.
- Requirements:
  `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-design.md`
  and
  `docs/specs/2026-07-18-rich-content-rendering/2026-07-18-rich-content-rendering-plan.md`.
- Base/head SHAs from Step 2.

- [x] **Step 4: Record and resolve findings**

Write the review file from
`superpowers/requesting-code-review/review-file-template.md`, with one table per
severity and columns `#`, `Status`, `Location`, `Finding`, and `Notes`.

Under `/ship`, fix every actionable finding and mark it `Fixed`; use `Rejected`
only with a concrete technical rationale. Re-run all gates. If any Critical or
Important issue was fixed, run one additional review pass and resolve any new
actionable findings.

- [ ] **Step 5: Open the PR and advance ClickUp**

After final verification, use `superpowers/finishing-a-development-branch` with
the `/ship` override: squash if required by that skill, rebase on `origin/main`,
push, and create the GitHub PR. Apply design status `review` through
`managing-active`.

For ClickUp task `868kdvye9`, after verification is green and the PR exists:

1. Set status to `review`.
2. Leave tag `agent`.
3. Confirm Branch remains `feat/868kdvye9-rich-content-rendering`.
4. Add a task comment containing the PR URL.

Do not mark the design or ClickUp task done before merge.

## Out of scope

- Rich rendering for INPUT, tool, or failure events.
- Persisting the Beautify preference.
- Raw HTML in Markdown.
- `\(...\)` or `\[...\]` formula delimiters.
- Backend rendering, new HTTP endpoints, event schema changes, or model changes.
- Rich rendering outside the session event stream.

/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import { afterEach, describe, expect, test, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { env } from 'node:process';
import { fileURLToPath } from 'node:url';
import DOMPurify from 'dompurify';
import mermaid from 'mermaid';

import './rich_content_lifecycle.js';
import { createTemporaryBuild } from './temporary_build.mjs';
import {
  cancelRichContent,
  initializeRichContent,
  renderRichContent,
} from './rich_content.js';

/**
 * Create a controllable Mermaid result promise for generation-race tests.
 * The fallback resolver is replaced synchronously by the Promise constructor.
 */
function createDiagramDeferred() {
  /** @type {(value: import('mermaid').RenderResult | PromiseLike<import('mermaid').RenderResult>) => void} */
  let resolveDiagram = () => {};
  const promise = new Promise((resolve) => {
    resolveDiagram = resolve;
  });
  return { promise, resolve: resolveDiagram };
}

/** Create a controllable session-render result for same-source attempt races. */
function createRenderDeferred() {
  /** @type {(value: boolean | PromiseLike<boolean>) => void} */
  let resolveRender = () => {};
  /** @type {(reason?: unknown) => void} */
  let rejectRender = () => {};
  const promise = new Promise((resolve, reject) => {
    resolveRender = resolve;
    rejectRender = reject;
  });
  return { promise, reject: rejectRender, resolve: resolveRender };
}

describe('rich content rendering', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  test('initializes Mermaid once and exposes the classic-script API', () => {
    const initializeMermaid = vi.spyOn(mermaid, 'initialize');

    initializeRichContent();
    initializeRichContent();

    expect(initializeMermaid).toHaveBeenCalledOnce();
    expect(initializeMermaid).toHaveBeenCalledWith({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'dark',
      suppressErrorRendering: true,
      secure: [
        'secure',
        'securityLevel',
        'startOnLoad',
        'maxTextSize',
        'theme',
        'themeCSS',
        'themeVariables',
        'fontFamily',
        'altFontFamily',
        'fontSize',
        'darkMode',
        'htmlLabels',
      ],
    });
    expect(window).toHaveProperty('chiefRichContent', {
      cancelRichContent,
      initializeRichContent,
      renderRichContent,
    });
  });

  test('announces browser API readiness after assigning the global', async () => {
    const onReady = vi.fn();
    window.addEventListener('chief:rich-content-ready', onReady);
    vi.resetModules();
    try {
      await import('./rich_content.js');

      expect(window).toHaveProperty('chiefRichContent.renderRichContent');
      expect(onReady).toHaveBeenCalledOnce();
      expect(onReady.mock.calls[0][0]).toBeInstanceOf(CustomEvent);
    } finally {
      window.removeEventListener('chief:rich-content-ready', onReady);
    }
  });

  test('tracks delayed readiness and stops observing after disposal', () => {
    const browserWindow = /** @type {any} */ (window);
    const originalRenderer = browserWindow.chiefRichContent;
    const readinessStates = [];
    delete browserWindow.chiefRichContent;
    let stopWatching;
    try {
      const lifecycle = browserWindow.chiefRichContentLifecycle;
      expect(lifecycle).toBeDefined();
      stopWatching = lifecycle?.watchRichContentReadiness((ready) => {
        readinessStates.push(ready);
      });
      expect(readinessStates).toEqual([false]);

      browserWindow.chiefRichContent = { renderRichContent: vi.fn() };
      window.dispatchEvent(new CustomEvent('chief:rich-content-ready'));
      expect(readinessStates).toEqual([false, true]);

      stopWatching?.();
      delete browserWindow.chiefRichContent;
      window.dispatchEvent(new CustomEvent('chief:rich-content-ready'));
      expect(readinessStates).toEqual([false, true]);
    } finally {
      stopWatching?.();
      browserWindow.chiefRichContent = originalRenderer;
    }
  });

  test('ignores a stale same-source rejection after a newer attempt succeeds', async () => {
    const lifecycle = /** @type {any} */ (window).chiefRichContentLifecycle;
    expect(lifecycle.renderRichOutputAttempt).toBeTypeOf('function');
    if (typeof lifecycle.renderRichOutputAttempt !== 'function') {
      return;
    }
    const attempts = new WeakMap();
    const target = document.createElement('div');
    const firstRender = createRenderDeferred();
    const source = '# Same source';
    const render = vi
      .fn()
      .mockReturnValueOnce(firstRender.promise)
      .mockImplementationOnce(async () => {
        target.innerHTML = '<h1>Newer rich output</h1>';
        return true;
      });
    const showFallback = vi.fn((element, fallbackSource) => {
      const fallback = document.createElement('pre');
      fallback.textContent = fallbackSource;
      element.replaceChildren(fallback);
    });

    const firstResult = lifecycle.renderRichOutputAttempt(
      attempts,
      target,
      source,
      render,
      showFallback,
    );
    const firstAttempt = attempts.get(target);
    attempts.delete(target);
    const secondResult = lifecycle.renderRichOutputAttempt(
      attempts,
      target,
      source,
      render,
      showFallback,
    );
    const secondAttempt = attempts.get(target);

    expect(secondAttempt).not.toBe(firstAttempt);
    await expect(secondResult).resolves.toBe(true);
    firstRender.reject(new Error('synthetic stale rejection'));
    await expect(firstResult).resolves.toBe(false);

    expect(target.querySelector('h1')?.textContent).toBe('Newer rich output');
    expect(showFallback).not.toHaveBeenCalled();
    expect(attempts.get(target)).toBe(secondAttempt);
  });

  test('renders Markdown headings, lists, and ordinary code fences', async () => {
    const target = document.createElement('div');

    const rendered = await renderRichContent(target, '# Title\n\n- one\n- two\n\n```js\nconst value = 1;\n```');

    expect(rendered).toBe(true);
    expect(target.querySelector('h1')?.textContent).toBe('Title');
    expect([...target.querySelectorAll('li')].map((item) => item.textContent)).toEqual(['one', 'two']);
    expect(target.querySelector('code.language-js')?.textContent).toBe('const value = 1;\n');
  });

  test('renders inline and block formulas with KaTeX markup', async () => {
    const target = document.createElement('div');

    const rendered = await renderRichContent(target, 'Inline $x^2$.\n\n$$\ny = mx + b\n$$');

    expect(rendered).toBe(true);
    expect(target.querySelector('.katex')).not.toBeNull();
    expect(target.querySelector('.katex-display')).not.toBeNull();
  });

  test('blocks unsafe markup and decorates accepted links', async () => {
    const target = document.createElement('div');
    const source =
      '<img src=x onload="globalThis.compromised = true">\n\n' +
      '[unsafe](javascript:globalThis.compromised=true)\n\n' +
      '[safe](https://example.com/path)';

    const rendered = await renderRichContent(target, source);

    expect(rendered).toBe(true);
    expect(target.querySelector('img')).toBeNull();
    expect(target.querySelector('[onload]')).toBeNull();
    expect(target.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(target.querySelector('a[href="https://example.com/path"]')).toMatchObject({
      target: '_blank',
      rel: 'noopener noreferrer',
    });
  });

  test('permits only explicit HTTP and HTTPS Markdown links', async () => {
    const target = document.createElement('div');
    const source = [
      '[https](https://example.com/path)',
      '[http](http://example.com/path)',
      '[mail](mailto:user@example.com)',
      '[ftp](ftp://example.com/file)',
      '[data](data:text/plain,hello)',
      '[protocol relative](//example.com/path)',
      '[relative](../private)',
      '[fragment](#section)',
    ].join('\n\n');

    await renderRichContent(target, source);

    expect([...target.querySelectorAll('a')].map((link) => link.getAttribute('href'))).toEqual([
      'https://example.com/path',
      'http://example.com/path',
    ]);
    expect(target.textContent).toContain('mail');
    expect(target.textContent).toContain('relative');
  });

  test('renders Markdown images as inert visible source', async () => {
    const target = document.createElement('div');

    await renderRichContent(
      target,
      'Before ![remote alt](https://example.com/tracker.png "title") and ![data alt](data:image/svg+xml,bad) after.',
    );

    expect(target.querySelector('img')).toBeNull();
    expect(target.textContent).toContain('![remote alt](https://example.com/tracker.png');
    expect(target.textContent).toContain('![data alt](data:image/svg+xml');
  });

  test('renders Mermaid source into a separately sanitized SVG', async () => {
    const target = document.createElement('div');
    const diagramSource = 'graph TD;\n  A-->B;\n';
    const renderDiagram = vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: '<svg xmlns="http://www.w3.org/2000/svg" onload="globalThis.compromised=true"><script>globalThis.compromised=true</script><g id="safe"/></svg>',
      diagramType: 'flowchart-v2',
    });

    const rendered = await renderRichContent(target, `\`\`\`mermaid\n${diagramSource}\`\`\``);

    expect(rendered).toBe(true);
    expect(renderDiagram).toHaveBeenCalledOnce();
    expect(renderDiagram.mock.calls[0][1]).toBe(diagramSource);
    expect(renderDiagram.mock.calls[0][0]).toMatch(/^chief-mermaid-\d+-0$/);
    expect(target.querySelector('[data-mermaid-index]')).toBeNull();
    expect(target.querySelector('svg g')).not.toBeNull();
    expect(target.querySelector('svg script, svg[onload]')).toBeNull();
  });

  test('rejects network-capable Mermaid source before invoking the renderer', async () => {
    const renderDiagram = vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: '<svg><text>must not render</text></svg>',
      diagramType: 'flowchart-v2',
    });
    const unsafeSources = [
      '%%{init: {"themeCSS": ".node{fill:url(https://attacker.invalid/a.svg)}"}}%%\nflowchart LR\nA-->B',
      '%% { init: {"themeVariables": {"fontFamily": "url(//attacker.invalid/font)"}} } %%\nflowchart LR\nA-->B',
      String.raw`flowchart LR
A-->B
style A fill:\75rl(https://attacker.invalid/a.svg)`,
      'flowchart LR; A-->B; classDef custom fill:red',
      'flowchart LR\nA-->B\nclassDef remote fill:url(data:image/svg+xml,bad)',
      'flowchart LR\nA-->B\nclick A "https://attacker.invalid/"',
      'sequenceDiagram\nparticipant A\nlink A: phone @ tel:+15555550123',
      'flowchart LR\nA-->B\nhref A "sms:+15555550123"',
      'flowchart LR\nA["vbscript:msgbox(1)"]-->B',
      'flowchart LR\nA["intent://scan/#Intent;scheme=zxing;end"]-->B',
      'flowchart LR\nA@{ img: "https://attacker.invalid/a.png" }',
      'flowchart LR\nA@{ image: "relative.png" }',
      'flowchart LR\nA["![remote](../asset.png)"]-->B',
      '---\nconfig:\n  themeCSS: url(blob:https://attacker.invalid/id)\n---\nflowchart LR\nA-->B',
    ];

    for (const diagramSource of unsafeSources) {
      const target = document.createElement('div');
      const rendered = await renderRichContent(
        target,
        `\`\`\`mermaid\n${diagramSource}\n\`\`\``,
      );

      expect(rendered).toBe(true);
      expect(target.querySelector('[data-mermaid-index] code')?.textContent).toBe(
        `${diagramSource}\n`,
      );
      expect(target.querySelector('.rich-render-failure')).toMatchObject({
        textContent: 'Diagram could not be rendered',
        role: 'status',
      });
      expect(target.querySelector('svg')).toBeNull();
    }
    expect(renderDiagram).not.toHaveBeenCalled();
  });

  test('rejects sequence navigation directives before invoking the renderer', async () => {
    const target = document.createElement('div');
    const diagramSource = `sequenceDiagram
  participant A
  participant B
  A->>B: hello
  links A: {"bad":"javascript:alert(1)"}
`;
    const renderDiagram = vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: '<svg><text>must not render</text></svg>',
      diagramType: 'sequence',
    });

    const rendered = await renderRichContent(
      target,
      `\`\`\`mermaid\n${diagramSource}\`\`\``,
    );

    expect(rendered).toBe(true);
    expect(renderDiagram).not.toHaveBeenCalled();
    expect(target.querySelector('[data-mermaid-index] code')?.textContent).toBe(diagramSource);
    expect(target.querySelector('.rich-render-failure')).toMatchObject({
      textContent: 'Diagram could not be rendered',
      role: 'status',
    });
    expect(target.querySelector('svg')).toBeNull();
  });

  test('allows ordinary Mermaid arrows, labels, and formula-like text', async () => {
    const target = document.createElement('div');
    const diagramSource = 'flowchart LR\n  A["ratio x/y and E = mc²"] -->|ordinary label| B\n';
    const renderDiagram = vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: '<svg><defs><marker id="arrow"/></defs><path marker-end="url(#arrow)"/></svg>',
      diagramType: 'flowchart-v2',
    });

    await renderRichContent(target, `\`\`\`mermaid\n${diagramSource}\`\`\``);

    expect(renderDiagram).toHaveBeenCalledOnce();
    expect(renderDiagram.mock.calls[0][1]).toBe(diagramSource);
    expect(target.querySelector('svg')).not.toBeNull();
  });

  test('repairs sanitized Mermaid fragment references', async () => {
    const target = document.createElement('div');
    vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: `
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
             aria-labelledby="diagram-title" aria-describedby="diagram-description">
          <title id="diagram-title">Diagram</title>
          <desc id="diagram-description">Description</desc>
          <defs>
            <marker id="arrow"><path d="M0,0 L10,5 L0,10 z"/></marker>
            <filter id="shadow"><feGaussianBlur stdDeviation="1"/></filter>
            <clipPath id="clip"><rect width="10" height="10"/></clipPath>
            <mask id="fade"><rect width="10" height="10" fill="white"/></mask>
          </defs>
          <g id="node" marker-end="url(#arrow)" filter="url('#shadow')" clip-path="url(#clip)"
             mask="url(#fade)"/>
          <a href="#node"><text>Link</text></a>
          <a class="legacy-link" xlink:href="#node"><text>Legacy link</text></a>
        </svg>`,
      diagramType: 'flowchart-v2',
    });

    await renderRichContent(target, '```mermaid\ngraph TD;\n  A-->B;\n```');

    const svg = target.querySelector('svg');
    const node = svg?.querySelector('g');
    for (const attributeName of ['marker-end', 'filter', 'clip-path', 'mask']) {
      const referencedId = node?.getAttribute(attributeName)?.match(/url\(#([^)]+)\)/)?.[1];
      expect(referencedId).toBeTruthy();
      expect(svg?.querySelector(`[id="${referencedId}"]`)).not.toBeNull();
    }
    const legacyLink = svg?.querySelector('.legacy-link');
    for (const reference of [
      svg?.querySelector('a')?.getAttribute('href'),
      legacyLink?.getAttributeNS('http://www.w3.org/1999/xlink', 'href') ?? legacyLink?.getAttribute('href'),
    ]) {
      expect(reference).toMatch(/^#/);
      expect(svg?.querySelector(`[id="${reference?.slice(1)}"]`)).not.toBeNull();
    }
    for (const attributeName of ['aria-labelledby', 'aria-describedby']) {
      for (const referencedId of svg?.getAttribute(attributeName)?.split(/\s+/) ?? []) {
        expect(svg?.querySelector(`[id="${referencedId}"]`)).not.toBeNull();
      }
    }
  });

  test('removes external Mermaid SVG resources while retaining valid local references', async () => {
    const target = document.createElement('div');
    vi.spyOn(mermaid, 'render').mockResolvedValue({
      svg: `
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
          <style>
            @import url("https://example.com/theme.css");
            .local { marker-end: url(#arrow); filter: url("#shadow"); }
            .remote { fill: url(https://example.com/fill.svg#paint); }
          </style>
          <defs>
            <marker id="arrow"><path d="M0,0 L10,5 L0,10 z"/></marker>
            <filter id="shadow"><feGaussianBlur stdDeviation="1"/></filter>
          </defs>
          <image href="https://example.com/tracker.png"/>
          <image xlink:href="data:image/svg+xml,bad"/>
          <a class="local-use" href="#arrow"><text>local</text></a>
          <use class="remote-use" href="https://example.com/icons.svg#icon"/>
          <a class="remote-anchor" href="https://example.com/"><text>remote</text></a>
          <path class="local" marker-end="url(#arrow)" filter="url(#shadow)"/>
          <path class="missing" marker-end="url(#missing)"/>
          <path class="remote-paint" fill="url('https://example.com/fill.svg#paint')"/>
        </svg>`,
      diagramType: 'flowchart-v2',
    });

    await renderRichContent(target, '```mermaid\ngraph LR;\n  A-->B;\n```');

    const svg = target.querySelector('svg');
    expect(svg?.querySelector('image')).toBeNull();
    expect(svg?.querySelector('.remote-anchor')?.hasAttribute('href')).toBe(false);
    expect(svg?.querySelector('.remote-use')?.getAttribute('href') ?? null).toBeNull();
    expect(svg?.querySelector('.local-use')?.getAttribute('href')).toMatch(/^#user-content-/);
    expect(svg?.querySelector('.local')?.getAttribute('marker-end')).toMatch(
      /^url\(#user-content-/,
    );
    expect(svg?.querySelector('.local')?.getAttribute('filter')).toMatch(/^url\(#user-content-/);
    expect(svg?.querySelector('.missing')?.getAttribute('marker-end')).toBe('none');
    expect(svg?.querySelector('.remote-paint')?.getAttribute('fill')).toBe('none');
    expect(svg?.querySelector('style')?.textContent).not.toContain('@import');
    expect(svg?.querySelector('style')?.textContent).not.toContain('https:');
    expect(svg?.innerHTML).not.toContain('data:image');
    expect(svg?.innerHTML).not.toContain('example.com');
  });

  test('preserves Mermaid source when diagram rendering rejects', async () => {
    const target = document.createElement('div');
    const diagramSource = 'not a valid <diagram> & still exact\n';
    vi.spyOn(mermaid, 'render').mockRejectedValue(new Error('synthetic Mermaid rejection'));

    const rendered = await renderRichContent(target, `\`\`\`mermaid\n${diagramSource}\`\`\``);

    expect(rendered).toBe(true);
    expect(target.querySelector('[data-mermaid-index] code')?.textContent).toBe(diagramSource);
    expect(target.querySelector('.rich-render-failure')).toMatchObject({
      textContent: 'Diagram could not be rendered',
      role: 'status',
    });
  });

  test('preserves invalid formula source and adds a local status', async () => {
    const target = document.createElement('div');
    const formulaSource = String.raw`\frac{`;

    const rendered = await renderRichContent(target, `$${formulaSource}$`);

    expect(rendered).toBe(true);
    const invalidFormula = target.querySelector('.katex-error');
    expect(invalidFormula?.textContent).toBe(formulaSource);
    expect(invalidFormula?.parentElement?.querySelector('.rich-render-failure')).toMatchObject({
      textContent: 'Formula could not be rendered',
      role: 'status',
    });
  });

  test('keeps source when a pending diagram render is cancelled', async () => {
    const target = document.createElement('div');
    const diagramSource = 'graph TD;\n  A-->B;\n';
    const diagram = createDiagramDeferred();
    vi.spyOn(mermaid, 'render').mockReturnValue(diagram.promise);

    const pendingRender = renderRichContent(target, `\`\`\`mermaid\n${diagramSource}\`\`\``);
    cancelRichContent(target);
    diagram.resolve({ svg: '<svg><text>stale</text></svg>', diagramType: 'flowchart-v2' });

    await expect(pendingRender).resolves.toBe(false);
    expect(target.querySelector('[data-mermaid-index] code')?.textContent).toBe(diagramSource);
    expect(target.querySelector('svg')).toBeNull();
  });

  test('lets a newer render generation win over pending work', async () => {
    const target = document.createElement('div');
    const olderDiagram = createDiagramDeferred();
    vi.spyOn(mermaid, 'render').mockReturnValue(olderDiagram.promise);

    const olderRender = renderRichContent(target, '```mermaid\ngraph TD;\n  Old-->State;\n```');
    await expect(renderRichContent(target, '# Current state')).resolves.toBe(true);
    olderDiagram.resolve({
      svg: '<svg><text>older state</text></svg>',
      diagramType: 'flowchart-v2',
    });

    await expect(olderRender).resolves.toBe(false);
    expect(target.querySelector('h1')?.textContent).toBe('Current state');
    expect(target.querySelector('svg')).toBeNull();
  });

  test('falls back to exact source when top-level rendering fails', async () => {
    const target = document.createElement('div');
    const source = '<img src=x onload="globalThis.compromised=true"> & exact';
    vi.spyOn(DOMPurify, 'sanitize').mockImplementationOnce(() => {
      throw new Error('synthetic sanitizer failure');
    });

    const rendered = await renderRichContent(target, source);

    expect(rendered).toBe(false);
    expect(target.textContent).toBe(source);
    expect(target.querySelector('img')).toBeNull();
  });

  test('removes the browser output directory when its build fails', () => {
    let temporaryDirectory = '';

    expect(() =>
      createTemporaryBuild((directory) => {
        temporaryDirectory = directory;
        throw new Error('synthetic build rejection');
      }),
    ).toThrow('synthetic build rejection');
    expect(temporaryDirectory).not.toBe('');
    expect(existsSync(temporaryDirectory)).toBe(false);
  });

  test('retains recent chunks, removes aged chunks, and publishes entries last', () => {
    const outputDirectory = mkdtempSync(join(tmpdir(), 'chief-rich-content-'));
    const publishLog = `${outputDirectory}-publish.log`;
    const packageDirectory = dirname(fileURLToPath(import.meta.url));
    try {
      mkdirSync(join(outputDirectory, 'assets'));
      writeFileSync(join(outputDirectory, 'stale-root.js'), 'stale');
      const recentChunk = join(outputDirectory, 'assets', 'recent-stale-chunk.js');
      const agedChunk = join(outputDirectory, 'assets', 'aged-stale-chunk.js');
      writeFileSync(recentChunk, 'recent');
      writeFileSync(agedChunk, 'aged');
      const olderThanRetention = new Date(Date.now() - 25 * 60 * 60 * 1000);
      utimesSync(agedChunk, olderThanRetention, olderThanRetention);
      const outputDirectoryInode = statSync(outputDirectory).ino;
      const initialStats = readdirSync(outputDirectory);
      execFileSync('pnpm', ['run', 'build:rich-content'], {
        cwd: packageDirectory,
        env: {
          ...env,
          CHIEF_RICH_CONTENT_OUTDIR: outputDirectory,
          CHIEF_RICH_CONTENT_PUBLISH_LOG: publishLog,
        },
        stdio: 'pipe',
      });

      const firstRootFiles = readdirSync(outputDirectory).sort();
      const firstAssetFiles = readdirSync(join(outputDirectory, 'assets')).sort();
      const firstPublishOrder = readFileSync(publishLog, 'utf8').trim().split('\n');
      execFileSync('pnpm', ['run', 'build:rich-content'], {
        cwd: packageDirectory,
        env: {
          ...env,
          CHIEF_RICH_CONTENT_OUTDIR: outputDirectory,
          CHIEF_RICH_CONTENT_PUBLISH_LOG: publishLog,
        },
        stdio: 'pipe',
      });
      const rootFiles = readdirSync(outputDirectory).sort();
      const assetFiles = readdirSync(join(outputDirectory, 'assets')).sort();

      expect(initialStats).toContain('stale-root.js');
      expect(rootFiles).toEqual(firstRootFiles);
      expect(assetFiles).toEqual(firstAssetFiles);
      expect(statSync(outputDirectory).ino).toBe(outputDirectoryInode);
      expect(rootFiles).not.toContain('stale-root.js');
      expect(existsSync(recentChunk)).toBe(true);
      expect(existsSync(agedChunk)).toBe(false);
      expect(firstPublishOrder.slice(-2)).toEqual([
        'rich_content.bundle.css',
        'rich_content.bundle.js',
      ]);
      expect(rootFiles).toContain('rich_content.bundle.js');
      expect(rootFiles).toContain('rich_content.bundle.css');
      expect(rootFiles.some((name) => name.endsWith('.LEGAL.txt'))).toBe(true);
      expect(assetFiles.some((name) => name.endsWith('.js'))).toBe(true);
      expect(assetFiles.some((name) => /\.(?:woff2?|ttf)$/.test(name))).toBe(true);
      expect(assetFiles.some((name) => name.endsWith('.LEGAL.txt'))).toBe(true);
    } finally {
      rmSync(outputDirectory, { recursive: true, force: true });
      rmSync(publishLog, { force: true });
    }
  });
});

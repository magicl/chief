/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import { afterAll, beforeAll, describe, expect, test } from 'vitest';

/** Load one browser script and reject if the real resource cannot execute. */
function loadScript(source, module = false) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = source;
    script.type = module ? 'module' : 'text/javascript';
    script.addEventListener('load', resolve, { once: true });
    script.addEventListener('error', reject, { once: true });
    document.head.append(script);
  });
}

describe('built rich-content browser runtime', () => {
  /** @type {any} */
  const browserWindow = window;
  const readinessStates = [];
  let stopWatching;

  beforeAll(async () => {
    delete browserWindow.chiefRichContent;
    delete browserWindow.chiefRichContentLifecycle;
    await loadScript('/rich-content-source/rich_content_lifecycle.js');
    stopWatching = browserWindow.chiefRichContentLifecycle.watchRichContentReadiness((ready) => {
      readinessStates.push(ready);
    });
    await loadScript('/rich-content-bundle/rich_content.bundle.js', true);
  });

  afterAll(() => {
    stopWatching?.();
    document.body.replaceChildren();
  });

  test('loads split chunks and transitions the lifecycle bridge to ready', () => {
    expect(readinessStates).toEqual([false, true]);
    expect(browserWindow.chiefRichContent?.renderRichContent).toBeTypeOf('function');
    const loadedChunks = performance
      .getEntriesByType('resource')
      .map((entry) => entry.name)
      .filter((name) => /\/rich-content-bundle\/assets\/.*\.js$/.test(name));
    expect(loadedChunks.length).toBeGreaterThan(0);
  });

  test('renders real Markdown, KaTeX, and Mermaid arrow markers', async () => {
    const target = document.createElement('div');
    document.body.append(target);

    const rendered = await browserWindow.chiefRichContent.renderRichContent(
      target,
      '# Browser smoke\n\nInline $x^2$.\n\n```mermaid\nflowchart LR\n  A --> B\n```',
    );

    expect(rendered).toBe(true);
    expect(target.querySelector('h1')?.textContent).toBe('Browser smoke');
    expect(target.querySelector('.katex')).not.toBeNull();
    const markerReference = target
      .querySelector('[marker-end]')
      ?.getAttribute('marker-end')
      ?.match(/^url\(#([^)]+)\)$/)?.[1];
    expect(markerReference).toBeTruthy();
    expect(target.querySelector(`marker[id="${markerReference}"]`)).not.toBeNull();
    expect(target.querySelector('.rich-mermaid-source')).toBeNull();
  });

  test('blocks Mermaid sources before CSS and image resource loading', async () => {
    await fetch('/rich-content-probe-log', { method: 'DELETE' });
    const probeUrl = `${location.origin}/network-probe/attacker-paint.svg`;
    const maliciousConfig = JSON.stringify({
      themeCSS: `.node rect { fill: url("${probeUrl}") !important; }`,
    });
    const maliciousDiagrams = [
      `%%{init: ${maliciousConfig}}%%
flowchart LR
  A --> B`,
      String.raw`flowchart LR
  A --> B
  style A fill:\75rl("${probeUrl}")`,
      `flowchart LR
  A@{ img: "${probeUrl}" } --> B`,
      `sequenceDiagram
  participant A
  participant B
  A->>B: hello
  links A: {"bad":"javascript:alert(1)"}`,
    ];

    for (const maliciousDiagram of maliciousDiagrams) {
      const target = document.createElement('div');
      document.body.append(target);
      const rendered = await browserWindow.chiefRichContent.renderRichContent(
        target,
        `\`\`\`mermaid\n${maliciousDiagram}\n\`\`\``,
      );

      expect(rendered).toBe(true);
      expect(target.querySelector('svg')).toBeNull();
      expect(target.querySelector('[data-mermaid-index] code')?.textContent).toBe(
        `${maliciousDiagram}\n`,
      );
      expect(target.querySelector('.rich-render-failure')?.textContent).toBe(
        'Diagram could not be rendered',
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
    const probeRequests = await fetch('/rich-content-probe-log').then((response) =>
      response.json(),
    );

    expect(probeRequests).toEqual([]);
  });

  test('uses the lifecycle attempt bridge for replacement rendering', async () => {
    const lifecycle = browserWindow.chiefRichContentLifecycle;
    const attempts = new WeakMap();
    const target = document.createElement('div');
    document.body.append(target);
    const showFallback = (element, source) => {
      element.textContent = source;
    };

    await lifecycle.renderRichOutputAttempt(
      attempts,
      target,
      '# First',
      browserWindow.chiefRichContent.renderRichContent,
      showFallback,
    );
    attempts.delete(target);
    const replaced = await lifecycle.renderRichOutputAttempt(
      attempts,
      target,
      '# Replacement',
      browserWindow.chiefRichContent.renderRichContent,
      showFallback,
    );

    expect(replaced).toBe(true);
    expect(target.querySelector('h1')?.textContent).toBe('Replacement');
  });
});

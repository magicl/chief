/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import { beforeAll, describe, expect, test } from 'vitest';

/** Load the activity-tree runtime through the browser middleware under test. */
function loadActivityTree() {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/activity-tree-source/activity_tree.js';
    script.addEventListener('load', resolve, { once: true });
    script.addEventListener('error', reject, { once: true });
    document.head.append(script);
  });
}

describe('activity tree browser smoke', () => {
  beforeAll(async () => {
    delete /** @type {any} */ (window).chiefActivityTree;
    await loadActivityTree();
  });

  test('toggle button exposes aria-expanded and does not move focus on data patches', () => {
    document.body.innerHTML = `
      <div id="fixture">
        <button type="button" id="tog" aria-expanded="false" aria-controls="detail">Tool</button>
        <div id="detail" hidden>args</div>
      </div>`;
    const { createActivityStore } = /** @type {any} */ (window).chiefActivityTree;
    const store = createActivityStore('s1');
    store.applyUpsert({
      id: 'tool', session_id: 's1', parent_id: null, seq: 1, revision: 1,
      kind: 'tool', status: 'running', name: 'demo.op', summary: '', details: {},
      child_session_id: null, latency_ms: null, cost_usd: null, model: null,
    });
    const button = document.getElementById('tog');
    const detail = document.getElementById('detail');
    button.focus();
    button.addEventListener('click', () => {
      store.setExpanded('tool', true, { manual: true });
      button.setAttribute('aria-expanded', store.isExpanded('tool') ? 'true' : 'false');
      detail.hidden = !store.isExpanded('tool');
    });

    button.click();

    expect(button.getAttribute('aria-expanded')).toBe('true');
    store.applyUpsert({
      id: 'tool', session_id: 's1', parent_id: null, seq: 1, revision: 2,
      kind: 'tool', status: 'succeeded', name: 'demo.op', summary: '', details: {},
      child_session_id: null, latency_ms: 5, cost_usd: null, model: null,
    });
    expect(document.activeElement).toBe(button);
  });

  test('deep rows show depth marker without horizontal page overflow', () => {
    const { visualDepth } = /** @type {any} */ (window).chiefActivityTree;
    const depth = visualDepth(8);
    document.body.innerHTML = `
      <div id="fixture" style="width:320px;overflow:auto">
        <div class="activity-row" style="padding-left:${depth.indentLevel}rem">
          <span class="activity-depth-cap">${depth.marker}</span>
          <span>deep-op</span>
        </div>
      </div>`;

    expect(document.querySelector('.activity-depth-cap').textContent).toBe('L8');
    const fixture = document.getElementById('fixture');
    expect(fixture.scrollWidth).toBeLessThanOrEqual(fixture.clientWidth + 1);
  });
});

/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Renders the real recursive row template with a real Alpine instance.
 *
 * The store unit tests cover indexing and disclosure state, but they never mount
 * the markup, so a row template whose directives are never initialized still
 * looks healthy to them. This suite loads `#activity-node-template` straight out
 * of the Jinja session page so template/controller drift shows up as a failure.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import Alpine from 'alpinejs';
import './activity_tree.js';

const runtimeWindow = /** @type {any} */ (window);
const SESSION_PAGE = '../../../../templates/web/session_detail.html';
const OPEN_TAG = '<template id="activity-node-template">';
const CLOSE_TAG = '</template>';

/**
 * Return the `#activity-node-template` element markup from the session page.
 * Nested `<template x-if>` elements are counted so the outer closing tag wins;
 * the block contains no Jinja expressions, so the slice is valid HTML as-is.
 */
function readNodeTemplateMarkup() {
  const page = readFileSync(fileURLToPath(new URL(SESSION_PAGE, import.meta.url)), 'utf8');
  const start = page.indexOf(OPEN_TAG);
  if (start < 0) {
    throw new Error(`${OPEN_TAG} is missing from ${SESSION_PAGE}`);
  }
  let depth = 0;
  let cursor = start;
  for (;;) {
    const nextOpen = page.indexOf('<template', cursor + 1);
    const nextClose = page.indexOf(CLOSE_TAG, cursor + 1);
    if (nextClose < 0) {
      throw new Error(`${OPEN_TAG} is never closed in ${SESSION_PAGE}`);
    }
    if (nextOpen >= 0 && nextOpen < nextClose) {
      depth += 1;
      cursor = nextOpen;
    } else if (depth === 0) {
      return page.slice(start, nextClose + CLOSE_TAG.length);
    } else {
      depth -= 1;
      cursor = nextClose;
    }
  }
}

/** Build a stream-shaped activity row with overrides. */
const act = (over) => ({
  id: 'a1',
  session_id: 's1',
  parent_id: null,
  seq: 1,
  revision: 1,
  kind: 'tool',
  status: 'running',
  name: null,
  summary: null,
  details: {},
  model: null,
  cost_usd: null,
  latency_ms: null,
  child_session_id: null,
  ...over,
});

/** Return trimmed text for every element matching a selector. */
const textsOf = (selector) => Array.from(
  runtimeWindow.document.querySelectorAll(selector),
  (element) => element.textContent.trim(),
);

/**
 * Report whether `x-show` leaves an element on screen.
 * Rows stay in the DOM when hidden, so text assertions alone cannot tell a
 * rendered row from one buried in a collapsed ancestor's disclosure.
 */
const isVisible = (element) => {
  // A missing element would otherwise skip the loop and report "visible".
  if (!element) {
    throw new Error('cannot judge visibility of a missing element');
  }
  for (let node = element; node; node = node.parentElement) {
    if (node.style?.display === 'none') {
      return false;
    }
  }
  return true;
};

/**
 * Return the first element matching a selector, optionally the first whose
 * trimmed text starts with `textPrefix`. Throws rather than returning
 * undefined so a stale selector fails the test instead of passing it.
 */
const elementOf = (selector, textPrefix = null) => {
  const matches = Array.from(runtimeWindow.document.querySelectorAll(selector));
  const found = textPrefix === null
    ? matches[0]
    : matches.find((element) => element.textContent.trim().startsWith(textPrefix));
  if (!found) {
    const suffix = textPrefix === null ? '' : ` starting with "${textPrefix}"`;
    throw new Error(`no element matched ${selector}${suffix}`);
  }
  return found;
};

/** Let Alpine flush its effect queue and the async row init work. */
const settle = async () => {
  await new Promise((resolve) => { Alpine.nextTick(resolve); });
  await new Promise((resolve) => { setTimeout(resolve, 0); });
};

/**
 * Serve the child session snapshot a running subagent row loads on expansion.
 * The child session is terminal, so the store never opens an EventSource
 * (jsdom has none) and the row settles straight into its loaded state.
 */
const CHILD_SNAPSHOT = {
  session: {
    id: 'kid', status: 'done', name: 'kid', parent_session_id: 's1', parent: null,
  },
  activities: [
    act({
      id: 'kid-tool', session_id: 'kid', seq: 1, kind: 'tool', name: 'gmail.search',
      summary: 'inbox',
    }),
  ],
};

describe('recursive activity row rendering', () => {
  /** Root store of the mounted tree; tests drive disclosure state through it. */
  let store;

  beforeAll(async () => {
    runtimeWindow.Alpine = Alpine;
    runtimeWindow.fetch = async (url) => (
      url === runtimeWindow.chiefActivityTree.SNAPSHOT_PATH('kid')
        ? { ok: true, json: async () => CHILD_SNAPSHOT }
        : { ok: false, json: async () => ({}) }
    );

    store = runtimeWindow.chiefActivityTree.createActivityStore('s1');
    store.applySnapshot({
      session: {
        id: 's1', status: 'running', name: 'demo', parent_session_id: null, parent: null,
      },
      activities: [
        act({ id: 'in', seq: 1, kind: 'input', summary: 'user', details: { content: 'hello there' } }),
        act({
          id: 'tool', seq: 2, kind: 'tool', name: 'clickup.get_task', summary: 'Task CU-184',
          details: { args: { task_id: 'CU-184' } },
        }),
        act({
          id: 'nested', parent_id: 'tool', seq: 3, kind: 'llm', name: 'gpt-5',
          status: 'succeeded', latency_ms: 1200, model: 'gpt-5', details: { usage: { input: 10 } },
        }),
        // The runtime parents generated messages to the LLM turn that produced
        // them, so this is the shape a real assistant reply arrives in.
        act({
          id: 'nested-out', parent_id: 'nested', seq: 4, kind: 'output',
          summary: 'assistant', details: { content: 'nested reply' },
        }),
        act({ id: 'out', seq: 5, kind: 'output', summary: 'assistant', details: { content: 'hi back' } }),
        act({ id: 'sub', seq: 6, kind: 'subagent', name: 'research', child_session_id: 'kid' }),
      ],
    });

    // Mirrors the sessionView scope the row template resolves helpers from, with
    // beautification off so rich-content assets stay out of this suite.
    Alpine.data('sessionFixture', () => ({
      rootStore: store,
      beautify: false,
      richContentReady: false,
      formatPayload: (activity) => activity.details?.content || '',
      formatUsd: (amount) => `$${amount}`,
      renderOutput: () => Promise.resolve(false),
    }));

    runtimeWindow.document.body.innerHTML = `
      <div id="page" x-data="sessionFixture()">
        <ol class="activity-list">
          <template x-for="id in rootStore.rootIds" :key="id">
            <li x-data="activityNode(() => rootStore, id, 0)"></li>
          </template>
        </ol>
      </div>
      ${readNodeTemplateMarkup()}
    `;

    Alpine.start();
    await settle();
  });

  // Tear the tree down while the jsdom document still exists; Alpine's mutation
  // observer would otherwise flush row cleanups after the environment is gone.
  afterAll(async () => {
    Alpine.destroyTree(runtimeWindow.document.getElementById('page'));
    runtimeWindow.document.body.innerHTML = '';
    await settle();
  });

  test('renders one initialized row per activity, including nested children', () => {
    expect(runtimeWindow.document.querySelectorAll('.activity-row')).toHaveLength(7);
    // An uninitialized clone keeps x-show markers visible and leaves x-if unexpanded.
    expect(runtimeWindow.document.querySelector('.activity-depth-marker').style.display).toBe('none');
  });

  test('renders input and output message bodies as plain text', () => {
    expect(textsOf('.kind-input .event-body')).toEqual(['hello there']);
    expect(textsOf('.kind-output pre.event-body')).toEqual(['nested reply', 'hi back']);
  });

  test('shows a message nested under a collapsed execution row', () => {
    expect(isVisible(elementOf('.kind-output pre.event-body', 'nested reply'))).toBe(true);
  });

  test('shows nested execution rows while their parent stays collapsed', () => {
    expect(isVisible(elementOf('.activity-toggle-text', 'gpt-5'))).toBe(true);
  });

  test('hides curated details of a collapsed execution row', () => {
    const toolRow = elementOf('.activity-toggle-text', 'clickup.get_task').closest('.activity-row');
    expect(isVisible(toolRow.querySelector('.activity-detail'))).toBe(false);
  });

  test('renders collapsed execution lines for non-message activities', () => {
    // The kind renders as its own pill so execution rows read like the
    // input/output cards; the rest of the line stays one ellipsized string.
    expect(textsOf('.activity-toggle .activity-kind-pill')).toEqual(['TOOL', 'LLM', 'SUBAGENT', 'TOOL']);
    expect(textsOf('.activity-toggle-text')).toEqual([
      'clickup.get_task · Task CU-184 · Running',
      'gpt-5 · Succeeded · 1.2s',
      'research · Running',
      'gmail.search · inbox · Running',
    ]);
  });

  test('colors the kind pill from the execution wrapper class', () => {
    const kinds = Array.from(
      runtimeWindow.document.querySelectorAll('.activity-execution'),
      (element) => Array.from(element.classList).find((name) => name.startsWith('kind-')),
    );
    expect(kinds).toEqual(['kind-tool', 'kind-llm', 'kind-subagent', 'kind-tool']);
  });

  test('renders the loaded child session subtree of a running subagent', () => {
    expect(textsOf('.activity-child-session .activity-toggle-text')).toEqual([
      'gmail.search · inbox · Running',
    ]);
    const childLink = runtimeWindow.document.querySelector('.activity-row-main a');
    expect(childLink.getAttribute('href')).toBe('/sessions/kid/');
  });

  // Runs last: it drives disclosure state on the shared mounted tree.
  test('hides only the child-session subtree when a subagent row collapses', async () => {
    const childRow = () => elementOf('.activity-child-session .activity-toggle-text', 'gmail.search');
    expect(isVisible(childRow())).toBe(true);

    store.setExpanded('sub', false, { manual: true });
    await settle();

    // The separately authorized child session belongs to the disclosure, unlike
    // the same-session rows that must survive their parent collapsing.
    expect(isVisible(childRow())).toBe(false);
    expect(isVisible(elementOf('.kind-output pre.event-body', 'nested reply'))).toBe(true);
  });
});

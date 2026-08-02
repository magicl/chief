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

/** Let Alpine flush its effect queue and the async row init work. */
const settle = async () => {
  await new Promise((resolve) => { Alpine.nextTick(resolve); });
  await new Promise((resolve) => { setTimeout(resolve, 0); });
};

describe('recursive activity row rendering', () => {
  beforeAll(async () => {
    runtimeWindow.Alpine = Alpine;
    const store = runtimeWindow.chiefActivityTree.createActivityStore('s1');
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
        act({ id: 'out', seq: 4, kind: 'output', summary: 'assistant', details: { content: 'hi back' } }),
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
    expect(runtimeWindow.document.querySelectorAll('.activity-row')).toHaveLength(4);
    // An uninitialized clone keeps x-show markers visible and leaves x-if unexpanded.
    expect(runtimeWindow.document.querySelector('.activity-depth-marker').style.display).toBe('none');
    expect(textsOf('.activity-child-list .activity-toggle')).toEqual(['LLM · gpt-5 · Succeeded · 1.2s']);
  });

  test('renders input and output message bodies as plain text', () => {
    expect(textsOf('.kind-input .event-body')).toEqual(['hello there']);
    expect(textsOf('.kind-output pre.event-body')).toEqual(['hi back']);
  });

  test('renders collapsed execution lines for non-message activities', () => {
    expect(textsOf('.activity-toggle')).toEqual([
      'TOOL · clickup.get_task · Task CU-184 · Running',
      'LLM · gpt-5 · Succeeded · 1.2s',
    ]);
  });
});

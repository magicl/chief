/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import './activity_tree.js';

const runtimeWindow = /** @type {any} */ (window);
const { createActivityStore } = runtimeWindow.chiefActivityTree;

/** Build a stream-shaped activity row with optional field overrides. */
const act = (over = {}) => ({
  id: 'a1',
  session_id: 's1',
  parent_id: null,
  seq: 1,
  revision: 1,
  kind: 'tool',
  status: 'running',
  name: 'clickup.get_task',
  summary: 'Task CU-184',
  details: { args: { task_id: 'CU-184' } },
  model: null,
  cost_usd: null,
  latency_ms: null,
  child_session_id: null,
  ...over,
});

describe('activity row formatting', () => {
  test('builds collapsed tool line without dumping arguments', () => {
    const { formatCollapsedLine } = runtimeWindow.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'tool', name: 'clickup.get_task', summary: 'Task CU-184',
      status: 'succeeded', latency_ms: 420,
    }))).toBe('TOOL · clickup.get_task · Task CU-184 · Succeeded · 420ms');
  });

  test('omits empty summary segments', () => {
    const { formatCollapsedLine } = runtimeWindow.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'llm', name: 'claude-sonnet', summary: '', status: 'succeeded', latency_ms: 1200,
    }))).toBe('LLM · claude-sonnet · Succeeded · 1.2s');
  });

  test('derives duration from started_at and ended_at when latency_ms is absent', () => {
    const { formatCollapsedLine } = runtimeWindow.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'tool',
      name: 'clickup.get_task',
      summary: 'Task CU-184',
      status: 'succeeded',
      latency_ms: null,
      started_at: '2026-07-26T12:00:00.000Z',
      ended_at: '2026-07-26T12:00:00.420Z',
    }))).toBe('TOOL · clickup.get_task · Task CU-184 · Succeeded · 420ms');
  });

  test('prefers latency_ms over started_at/ended_at duration', () => {
    const { formatCollapsedLine } = runtimeWindow.chiefActivityTree;
    expect(formatCollapsedLine(act({
      kind: 'tool',
      name: 'clickup.get_task',
      summary: 'Task CU-184',
      status: 'succeeded',
      latency_ms: 10,
      started_at: '2026-07-26T12:00:00.000Z',
      ended_at: '2026-07-26T12:00:00.420Z',
    }))).toBe('TOOL · clickup.get_task · Task CU-184 · Succeeded · 10ms');
  });

  test('formats raw details as JSON text and falls back to escaped string', () => {
    const { formatRawDetails } = runtimeWindow.chiefActivityTree;
    expect(formatRawDetails({ a: 1 })).toContain('"a": 1');
    const cyclic = {};
    cyclic.self = cyclic;
    expect(formatRawDetails(cyclic)).toEqual(expect.any(String));
  });

  test('caps visual depth and exposes depth markers', () => {
    const { visualDepth } = runtimeWindow.chiefActivityTree;
    expect(visualDepth(3)).toEqual({ indentLevel: 3, showMarker: false, marker: null });
    expect(visualDepth(8)).toEqual({ indentLevel: 6, showMarker: true, marker: 'L8' });
  });
});

describe('session activity presentation helpers', () => {
  test('sums costs from only the current store activities', () => {
    const { sumCostUsd } = runtimeWindow.chiefActivityTree;
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'first', cost_usd: '0.25' }),
        act({ id: 'second', cost_usd: 0.5, seq: 2 }),
        act({ id: 'missing', cost_usd: null, seq: 3 }),
        act({ id: 'invalid', cost_usd: 'not-a-number', seq: 4 }),
      ],
    });
    const childStore = createActivityStore('child');
    childStore.applySnapshot({
      session: {
        id: 'child', status: 'done', name: null, parent_session_id: 's1', parent: null,
      },
      activities: [
        act({ id: 'expensive', session_id: 'child', cost_usd: 999 }),
      ],
    });
    store.childStores.child = childStore;

    expect(sumCostUsd(store)).toBe(0.75);
  });

  test('allows beautification only for output activities', () => {
    const { isBeautifiable } = runtimeWindow.chiefActivityTree;
    expect(isBeautifiable({ kind: 'output' })).toBe(true);
    expect(isBeautifiable({ kind: 'input' })).toBe(false);
    expect(isBeautifiable({ kind: 'tool' })).toBe(false);
    expect(isBeautifiable({})).toBe(false);
  });
});

describe('activity store indexing', () => {
  test('indexes snapshot roots and children by seq', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'child-b', seq: 3, parent_id: 'root', kind: 'tool', status: 'succeeded', latency_ms: 10 }),
        act({ id: 'root', seq: 1, kind: 'input', status: 'succeeded', name: null, summary: null }),
        act({ id: 'child-a', seq: 2, parent_id: 'root', kind: 'tool', status: 'succeeded', latency_ms: 420 }),
      ],
    });
    expect(store.rootIds).toEqual(['root']);
    expect(store.childIds.root).toEqual(['child-a', 'child-b']);
    expect(store.activities.root.revision).toBe(1);
  });

  test('applies newer revisions in place and ignores stale ones', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 't', revision: 1, status: 'running' }));
    store.applyUpsert(act({ id: 't', revision: 2, status: 'succeeded', latency_ms: 10 }));
    store.applyUpsert(act({ id: 't', revision: 1, status: 'running' }));
    expect(store.activities.t.status).toBe('succeeded');
    expect(store.activities.t.revision).toBe(2);
  });

  test('buffers unresolved children until the parent arrives', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'c', parent_id: 'missing', seq: 2 }));
    expect(store.rootIds).toEqual([]);
    expect(store.unresolvedChildIds.missing).toEqual(['c']);
    store.applyUpsert(act({ id: 'missing', parent_id: null, seq: 1, kind: 'span', name: 'outer' }));
    expect(store.childIds.missing).toEqual(['c']);
    expect(store.unresolvedChildIds.missing).toBeUndefined();
  });

  test('rejects parent_id changes after create and requests refresh', () => {
    const store = createActivityStore('s1');
    const refresh = [];
    store.onNeedsRefresh = () => refresh.push(true);
    store.applyUpsert(act({ id: 't', parent_id: null, revision: 1 }));
    store.applyUpsert(act({ id: 't', parent_id: 'other', revision: 2 }));
    expect(store.activities.t.parent_id).toBeNull();
    expect(refresh).toEqual([true]);
  });

  test('retains authoritative snapshot unresolved children as synthetic unresolved roots', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'kept', seq: 1, kind: 'input', status: 'succeeded', name: null, summary: null }),
        act({ id: 'orphan', parent_id: 'gone', seq: 2 }),
      ],
    });
    expect(store.activities.orphan).toBeDefined();
    expect(store.activities.orphan.parent_id).toBe('gone');
    expect(store.unresolvedChildIds.gone).toBeUndefined();
    const syntheticIds = store.rootIds.filter((id) => store.activities[id]?.unresolved === true);
    expect(syntheticIds).toHaveLength(1);
    expect(store.childIds[syntheticIds[0]]).toEqual(['orphan']);
    expect(store.rootIds).toContain('kept');
  });

  test('preserves expansion and manual maps across applySnapshot', () => {
    const store = createActivityStore('s1');
    store.expandedIds.tool = true;
    store.manualExpandIds.span = true;
    store.manualCollapseIds.sub = true;
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [act({ id: 'tool', kind: 'tool', status: 'succeeded' })],
    });
    expect(store.expandedIds.tool).toBe(true);
    expect(store.manualExpandIds.span).toBe(true);
    expect(store.manualCollapseIds.sub).toBe(true);
  });

  test('rejects cross-session upsert and snapshot rows', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'foreign', session_id: 'other' }));
    expect(store.activities.foreign).toBeUndefined();
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'foreign', session_id: 'other' }),
        act({ id: 'local', seq: 1, kind: 'input', status: 'succeeded', name: null, summary: null }),
      ],
    });
    expect(store.activities.foreign).toBeUndefined();
    expect(store.activities.local).toBeDefined();
    expect(store.rootIds).toEqual(['local']);
  });

  test('snapshot orphan keeps parent_id, accepts newer revision without refresh, and reattaches', () => {
    const store = createActivityStore('s1');
    const refresh = [];
    store.onNeedsRefresh = () => refresh.push(true);
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [act({ id: 'orphan', parent_id: 'gone', seq: 2, revision: 1, status: 'running' })],
    });
    const syntheticIds = store.rootIds.filter((id) => store.activities[id]?.unresolved === true);
    expect(store.activities.orphan.parent_id).toBe('gone');
    expect(store.childIds[syntheticIds[0]]).toEqual(['orphan']);

    store.applyUpsert(act({
      id: 'orphan', parent_id: 'gone', seq: 2, revision: 2, status: 'succeeded', latency_ms: 9,
    }));
    expect(store.activities.orphan.parent_id).toBe('gone');
    expect(store.activities.orphan.status).toBe('succeeded');
    expect(store.activities.orphan.revision).toBe(2);
    expect(refresh).toEqual([]);
    expect(store.childIds[syntheticIds[0]]).toEqual(['orphan']);

    store.applyUpsert(act({ id: 'gone', parent_id: null, seq: 1, kind: 'span', name: 'outer' }));
    expect(store.childIds.gone).toEqual(['orphan']);
    expect(store.activities.orphan.parent_id).toBe('gone');
    expect(store.rootIds.filter((id) => store.activities[id]?.unresolved === true)).toEqual([]);
    expect(refresh).toEqual([]);
  });

  test('rejects upserts with non-finite revision', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 't', revision: undefined, status: 'running' }));
    store.applyUpsert(act({ id: 't', revision: Number.NaN, status: 'running' }));
    store.applyUpsert(act({ id: 'u', revision: 1, status: 'running' }));
    store.applyUpsert(act({ id: 'u', revision: undefined, status: 'succeeded' }));
    expect(store.activities.t).toBeUndefined();
    expect(store.activities.u.status).toBe('running');
    expect(store.activities.u.revision).toBe(1);
  });
});

describe('activity expansion defaults', () => {
  test('collapses tools and completed subagents; expands running subagents', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'tool', kind: 'tool', status: 'succeeded' }),
        act({ id: 'sub-done', kind: 'subagent', status: 'succeeded', child_session_id: 'c1' }),
        act({ id: 'sub-run', kind: 'subagent', status: 'running', child_session_id: 'c2' }),
      ],
    });
    expect(store.isExpanded('tool')).toBe(false);
    expect(store.isExpanded('sub-done')).toBe(false);
    expect(store.isExpanded('sub-run')).toBe(true);
  });

  test('manual collapse of running subagent survives status patches', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'c' }));
    store.setExpanded('sub', false, { manual: true });
    store.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', revision: 2, child_session_id: 'c' }));
    expect(store.isExpanded('sub')).toBe(false);
  });

  test('manual expand survives terminal status patches', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'running' }));
    store.setExpanded('tool', true, { manual: true });
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'succeeded', revision: 2 }));
    expect(store.isExpanded('tool')).toBe(true);
  });

  test('new running subagent auto-expands unless an ancestor is manually collapsed', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'span', kind: 'span', status: 'running', name: 'outer' }));
    store.setExpanded('span', false, { manual: true });
    store.applyUpsert(act({
      id: 'sub', parent_id: 'span', kind: 'subagent', status: 'running', child_session_id: 'c', seq: 2,
    }));
    expect(store.isExpanded('sub')).toBe(false);
  });

  test('default auto-expansion does not overwrite explicit manual collapse', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'c' }));
    store.setExpanded('sub', false, { manual: true });
    store.setExpanded('sub', true, { manual: false });
    expect(store.isExpanded('sub')).toBe(false);
    expect(store.manualCollapseIds.sub).toBe(true);
    expect(store.manualExpandIds.sub).toBeUndefined();
  });

  test('setExpanded keeps manual expand and collapse maps mutually coherent', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'running' }));
    store.setExpanded('tool', true, { manual: true });
    expect(store.manualExpandIds.tool).toBe(true);
    expect(store.manualCollapseIds.tool).toBeUndefined();
    store.setExpanded('tool', false, { manual: true });
    expect(store.manualCollapseIds.tool).toBe(true);
    expect(store.manualExpandIds.tool).toBeUndefined();
    store.setExpanded('tool', true, { manual: true });
    expect(store.manualExpandIds.tool).toBe(true);
    expect(store.manualCollapseIds.tool).toBeUndefined();
  });

  test('ancestor collapse walk guards cycles and missing parents', () => {
    const store = createActivityStore('s1');
    store.applyUpsert(act({ id: 'a', parent_id: null, kind: 'span', status: 'running', name: 'a' }));
    store.applyUpsert(act({ id: 'b', parent_id: 'a', kind: 'span', status: 'running', name: 'b', seq: 2 }));
    // Malformed cycle: do not hang when walking ancestors.
    store.activities.a.parent_id = 'b';
    store.setExpanded('a', false, { manual: true });
    store.applyUpsert(act({
      id: 'sub', parent_id: 'b', kind: 'subagent', status: 'running', child_session_id: 'c', seq: 3,
    }));
    expect(store.isExpanded('sub')).toBe(false);

    store.applyUpsert(act({
      id: 'orphan-sub', parent_id: 'gone', kind: 'subagent', status: 'running', child_session_id: 'c2', seq: 4,
    }));
    expect(store.isExpanded('orphan-sub')).toBe(true);
  });

  test('input and output message bodies are not disclosure-dependent', () => {
    const store = createActivityStore('s1');
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [
        act({ id: 'in', kind: 'input', status: 'succeeded', name: null, summary: 'hello' }),
        act({ id: 'out', kind: 'output', status: 'succeeded', name: null, summary: 'world' }),
      ],
    });
    // Default disclosure is collapsed; message body visibility is independent of that.
    expect(store.isExpanded('in')).toBe(false);
    expect(store.isExpanded('out')).toBe(false);
    store.setExpanded('in', true, { manual: true });
    expect(store.isExpanded('in')).toBe(true);
    store.setExpanded('in', false, { manual: true });
    expect(store.isExpanded('in')).toBe(false);
  });

  test('expansion intent is keyed by activity id for page lifetime only', () => {
    const store = createActivityStore('s1');
    const localBefore = localStorage.length;
    const sessionBefore = sessionStorage.length;
    store.applyUpsert(act({ id: 'tool', kind: 'tool', status: 'running' }));
    store.setExpanded('tool', true, { manual: true });
    store.applySnapshot({
      session: { id: 's1', status: 'running', name: null, parent_session_id: null, parent: null },
      activities: [act({ id: 'tool', kind: 'tool', status: 'succeeded', revision: 2 })],
    });
    expect(store.isExpanded('tool')).toBe(true);
    expect(store.manualExpandIds.tool).toBe(true);
    expect(localStorage.length).toBe(localBefore);
    expect(sessionStorage.length).toBe(sessionBefore);
  });
});

describe('child session loading', () => {
  let originalEventSource;
  let originalFetch;

  beforeEach(() => {
    originalEventSource = runtimeWindow.EventSource;
    originalFetch = runtimeWindow.fetch;
  });

  afterEach(() => {
    runtimeWindow.EventSource = originalEventSource;
    runtimeWindow.fetch = originalFetch;
  });

  test('expanding a subagent loads a separate child store from snapshot URL', async () => {
    const fetches = [];
    runtimeWindow.fetch = async (url) => {
      fetches.push(String(url));
      return {
        ok: true,
        json: async () => ({
          session: {
            id: 'child',
            status: 'running',
            name: 'nested',
            parent_session_id: 's1',
            parent: { id: 's1', name: 'root' },
          },
          activities: [act({ id: 'in', session_id: 'child', kind: 'input', status: 'succeeded' })],
        }),
      };
    };
    runtimeWindow.EventSource = class {
      /** Record the child stream URL without opening a real connection. */
      constructor(url) {
        this.url = url;
      }

      /** Accept lifecycle listeners used by the activity store. */
      addEventListener() {}

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));

    await root.ensureChildLoaded('sub');

    expect(fetches[0]).toContain('/sessions/child/activities/');
    expect(root.childStores.child.rootIds).toEqual(['in']);
    expect(root.activities).not.toHaveProperty('in');
  });

  test('opens child SSE only while expanded and non-terminal; collapse closes it', async () => {
    const sources = [];
    runtimeWindow.EventSource = class {
      /** Track each fake EventSource so lifecycle assertions can inspect it. */
      constructor(url) {
        this.url = url;
        this.listeners = {};
        sources.push(this);
      }

      /** Retain event handlers for later lifecycle simulation. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    runtimeWindow.fetch = async () => ({
      ok: true,
      json: async () => ({
        session: {
          id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null,
        },
        activities: [],
      }),
    });
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));

    await root.ensureChildLoaded('sub');

    expect(sources).toHaveLength(1);
    expect(sources[0].url).toContain('/sessions/child/events/');
    root.setExpanded('sub', false, { manual: true });
    await root.syncChildSubscription('sub');
    expect(sources[0].closed).toBe(true);
  });

  test('terminal child applies final state and closes stream', async () => {
    const sources = [];
    runtimeWindow.EventSource = class {
      /** Track child streams opened by the store. */
      constructor(url) {
        this.url = url;
        this.listeners = {};
        sources.push(this);
      }

      /** Retain event handlers for later lifecycle simulation. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    runtimeWindow.fetch = async () => ({
      ok: true,
      json: async () => ({
        session: {
          id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null,
        },
        activities: [],
      }),
    });
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));
    await root.ensureChildLoaded('sub');

    root.childStores.child.applySessionUpdate({ status: 'done' });
    await root.syncChildSubscription('sub');

    expect(sources[0].closed).toBe(true);
    expect(root.childStores.child.session.status).toBe('done');
  });

  test('unavailable child snapshot sets local unavailable flag without failing the parent store', async () => {
    runtimeWindow.fetch = async () => ({ ok: false, status: 403 });
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'succeeded', child_session_id: 'gone',
    }));

    await root.ensureChildLoaded('sub');

    expect(root.childLoadState.gone).toEqual({ status: 'unavailable' });
    expect(root.rootIds).toEqual(['sub']);
  });

  test('visited-session guard refuses cyclic child expansion', async () => {
    const root = createActivityStore('s1', { ancestryPath: ['s1'] });
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 's1',
    }));

    await root.ensureChildLoaded('sub');

    expect(root.childLoadState.s1).toEqual({ status: 'cycle' });
  });

  test('typed SSE applies valid updates, ignores malformed or stale activity, and closes on terminal', async () => {
    const sources = [];
    runtimeWindow.EventSource = class {
      /** Track streams and their typed handlers for message simulation. */
      constructor() {
        this.listeners = {};
        sources.push(this);
      }

      /** Retain typed event handlers for message simulation. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    runtimeWindow.fetch = async () => ({
      ok: true,
      json: async () => ({
        session: {
          id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null,
        },
        activities: [act({ id: 'tool', session_id: 'child', revision: 2, status: 'running' })],
      }),
    });
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));
    await root.ensureChildLoaded('sub');

    sources[0].listeners.session_activity({ data: '{bad json' });
    sources[0].listeners.session_activity({
      data: JSON.stringify({
        operation: 'upsert',
        activity: act({ id: 'tool', session_id: 'child', revision: 1, status: 'succeeded' }),
      }),
    });
    expect(root.childStores.child.activities.tool.status).toBe('running');

    sources[0].listeners.session_activity({
      data: JSON.stringify({
        operation: 'upsert',
        activity: act({ id: 'tool', session_id: 'child', revision: 3, status: 'succeeded' }),
      }),
    });
    sources[0].listeners.session_update({ data: JSON.stringify({ status: 'done' }) });

    expect(root.childStores.child.activities.tool.status).toBe('succeeded');
    expect(root.childStores.child.session.status).toBe('done');
    expect(sources[0].closed).toBe(true);
  });

  test('re-expanding after collapse refetches before opening one replacement stream', async () => {
    let fetchCount = 0;
    const sources = [];
    runtimeWindow.fetch = async () => {
      fetchCount += 1;
      return {
        ok: true,
        json: async () => ({
          session: {
            id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null,
          },
          activities: [],
        }),
      };
    };
    runtimeWindow.EventSource = class {
      /** Track each stream instance opened across disclosure changes. */
      constructor() {
        this.listeners = {};
        sources.push(this);
      }

      /** Retain lifecycle handlers expected by the store. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    const root = createActivityStore('s1');
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));
    await root.ensureChildLoaded('sub');
    await root.ensureChildLoaded('sub');
    expect(fetchCount).toBe(1);
    expect(sources).toHaveLength(1);

    root.setExpanded('sub', false, { manual: true });
    await root.syncChildSubscription('sub');
    root.setExpanded('sub', true, { manual: true });
    await root.syncChildSubscription('sub');

    expect(fetchCount).toBe(2);
    expect(sources).toHaveLength(2);
    expect(sources[0].closed).toBe(true);
  });

  test('BFCache refresh refetches root and expanded child before replacing streams', async () => {
    const fetches = [];
    const sources = [];
    let rootFetchCount = 0;
    runtimeWindow.fetch = async (url) => {
      const targetId = String(url).includes('/child/') ? 'child' : 's1';
      fetches.push(String(url));
      if (targetId === 's1') {
        rootFetchCount += 1;
      }
      return {
        ok: true,
        json: async () => ({
          session: {
            id: targetId,
            status: 'running',
            name: null,
            parent_session_id: targetId === 'child' ? 's1' : null,
            parent: null,
          },
          activities: targetId === 's1' && rootFetchCount > 1
            ? [act({
              id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
            })]
            : [],
        }),
      };
    };
    runtimeWindow.EventSource = class {
      /** Track root and child stream replacement across BFCache refresh. */
      constructor(url) {
        this.url = url;
        this.listeners = {};
        sources.push(this);
      }

      /** Retain lifecycle handlers expected by the store. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };
    const root = createActivityStore('s1');
    await root.loadRoot();
    root.applyUpsert(act({
      id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
    }));
    await root.ensureChildLoaded('sub');
    const previousSources = [...sources];
    fetches.length = 0;

    await root.refreshExpandedSnapshots();

    expect(fetches).toEqual([
      '/sessions/s1/activities/',
      '/sessions/child/activities/',
    ]);
    expect(previousSources.every((source) => source.closed)).toBe(true);
    expect(sources).toHaveLength(4);
  });

  test('dispose then refresh reopens grandchild streams after clearing child disposed', async () => {
    const sources = [];
    const snapshotFor = (id) => {
      if (id === 's1') {
        return {
          session: {
            id: 's1', status: 'running', name: null, parent_session_id: null, parent: null,
          },
          activities: [act({
            id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
          })],
        };
      }
      if (id === 'child') {
        return {
          session: {
            id: 'child', status: 'running', name: null, parent_session_id: 's1', parent: null,
          },
          activities: [act({
            id: 'nested',
            session_id: 'child',
            kind: 'subagent',
            status: 'running',
            child_session_id: 'grand',
          })],
        };
      }
      return {
        session: {
          id: 'grand', status: 'running', name: null, parent_session_id: 'child', parent: null,
        },
        activities: [act({
          id: 'in', session_id: 'grand', kind: 'input', status: 'succeeded',
        })],
      };
    };
    runtimeWindow.fetch = async (url) => {
      const path = String(url);
      const id = path.includes('/grand/') ? 'grand' : path.includes('/child/') ? 'child' : 's1';
      return { ok: true, json: async () => snapshotFor(id) };
    };
    runtimeWindow.EventSource = class {
      /** Track nested session streams across dispose and BFCache revive. */
      constructor(url) {
        this.url = url;
        this.listeners = {};
        sources.push(this);
      }

      /** Retain lifecycle handlers expected by the store. */
      addEventListener(type, fn) {
        this.listeners[type] = fn;
      }

      /** Mark the fake stream closed. */
      close() {
        this.closed = true;
      }
    };

    const root = createActivityStore('s1');
    await root.loadRoot();
    await root.ensureChildLoaded('sub');
    const childStore = root.childStores.child;
    await childStore.ensureChildLoaded('nested');
    expect(sources.map((source) => source.url).sort()).toEqual([
      '/sessions/child/events/',
      '/sessions/grand/events/',
      '/sessions/s1/events/',
    ]);

    root.dispose();
    expect(sources.every((source) => source.closed)).toBe(true);
    const closedCount = sources.length;

    await root.refreshExpandedSnapshots();

    const openAfter = sources.slice(closedCount).filter((source) => !source.closed);
    expect(openAfter.map((source) => source.url).sort()).toEqual([
      '/sessions/child/events/',
      '/sessions/grand/events/',
      '/sessions/s1/events/',
    ]);
    expect(childStore.disposed).toBe(false);
  });

  test('stream retries are bounded per child and dispose closes root and child sources', async () => {
    vi.useFakeTimers();
    try {
      const sources = [];
      runtimeWindow.fetch = async (url) => {
        const targetId = String(url).includes('/child/') ? 'child' : 's1';
        return {
          ok: true,
          json: async () => ({
            session: {
              id: targetId,
              status: 'running',
              name: null,
              parent_session_id: targetId === 'child' ? 's1' : null,
              parent: null,
            },
            activities: [],
          }),
        };
      };
      runtimeWindow.EventSource = class {
        /** Track sources and handlers across reconnect attempts. */
        constructor(url) {
          this.url = url;
          this.listeners = {};
          sources.push(this);
        }

        /** Retain lifecycle handlers for failure simulation. */
        addEventListener(type, fn) {
          this.listeners[type] = fn;
        }

        /** Mark the fake stream closed. */
        close() {
          this.closed = true;
        }
      };
      const root = createActivityStore('s1');
      await root.loadRoot();
      root.applyUpsert(act({
        id: 'sub', kind: 'subagent', status: 'running', child_session_id: 'child',
      }));
      await root.ensureChildLoaded('sub');

      for (let attempt = 0; attempt < 6; attempt += 1) {
        const childSource = sources.filter((source) => source.url.includes('/child/')).at(-1);
        childSource.listeners.error();
        await vi.runOnlyPendingTimersAsync();
      }

      expect(sources.filter((source) => source.url.includes('/child/'))).toHaveLength(6);
      expect(root.childLoadState.child).toEqual({ status: 'disconnected' });
      root.dispose();
      expect(sources.every((source) => source.closed)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

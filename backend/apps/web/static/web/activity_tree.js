/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Per-session activity tree store (classic browser IIFE → window.chiefActivityTree).
 *
 * Backend contracts this module will consume later (do not invent shims here):
 * - Activity rows: AgentSessionActivity.to_stream_dict
 * - Ordered snapshot query: apps.sessions.services.queries.activities_for
 * - SSE channel session_activity payload: {operation:'upsert', activity:...}
 *
 * Indexing invariants:
 * - Sibling order is ascending immutable seq within rootIds / childIds buckets.
 * - Canonical parent_id is never rewritten by the store. Live upserts whose parent
 *   is absent stay in unresolvedChildIds; only an authoritative applySnapshot
 *   marks those orphans in snapshotUnresolvedIds so indexing places them under a
 *   synthetic unresolved root for display without mutating parent_id.
 * - When the real parent later arrives, reindex reattaches naturally and drops
 *   the synthetic overlay once no promoted orphans remain.
 * - parent_id is immutable after first insert; a newer patch with a different
 *   parent_id triggers onNeedsRefresh, keeps the canonical parent_id, and still
 *   applies the remaining fields from that patch (partial-field application).
 * - Expansion key maps survive snapshot replacement so Alpine UI state sticks.
 * - Expansion intent is page-lifetime only (no localStorage/sessionStorage), keyed
 *   by activity id. Running subagents default open unless a manually collapsed
 *   ancestor blocks them; manual expand/collapse outrank status-driven defaults.
 * - Input/output message bodies are not gated by disclosure state.
 * - Activity rows are shallow-cloned on ingest; nested payload objects (e.g.
 *   details) are assumed read-only by callers and are not deep-copied.
 * - revision must be a finite number; non-finite values are rejected (backend
 *   always sends integer revisions, but the store guards malformed client data).
 */
((browserWindow) => {
  const runtimeWindow = /** @type {any} */ (browserWindow);

  /** Stable id for the synthetic root that holds snapshot orphans. */
  const SYNTHETIC_UNRESOLVED_ID = '__unresolved__';

  /** Build the same-origin authoritative activity snapshot URL for a session. */
  const SNAPSHOT_PATH = (id) => `/sessions/${id}/activities/`;

  /** Build the same-origin authenticated SSE URL for a session. */
  const EVENTS_PATH = (id) => `/sessions/${id}/events/`;

  /** Cap indentation while retaining complete logical nesting. */
  const MAX_VISUAL_DEPTH = 6;

  /** Capitalize a status word for collapsed-row display (Succeeded, Running, …). */
  const formatStatusLabel = (status) => {
    if (!status || typeof status !== 'string') {
      return '';
    }
    return status.charAt(0).toUpperCase() + status.slice(1);
  };

  /**
   * Format latency for collapsed rows: Nms under 1000ms; otherwise seconds
   * with one decimal when needed (1.2s) or whole seconds when exact (2s).
   */
  const formatLatency = (latencyMs) => {
    if (latencyMs == null || typeof latencyMs !== 'number' || !Number.isFinite(latencyMs)) {
      return '';
    }
    if (latencyMs < 1000) {
      return `${latencyMs}ms`;
    }
    const seconds = latencyMs / 1000;
    if (Number.isInteger(seconds)) {
      return `${seconds}s`;
    }
    return `${seconds.toFixed(1)}s`;
  };

  /**
   * Resolve display latency in ms: prefer finite latency_ms; otherwise derive
   * from parseable started_at/ended_at when both are present and ordered.
   */
  const resolveLatencyMs = (activity) => {
    const direct = activity?.latency_ms;
    if (typeof direct === 'number' && Number.isFinite(direct)) {
      return direct;
    }
    const started = activity?.started_at != null ? Date.parse(String(activity.started_at)) : Number.NaN;
    const ended = activity?.ended_at != null ? Date.parse(String(activity.ended_at)) : Number.NaN;
    if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
      return null;
    }
    return ended - started;
  };

  /**
   * Report whether a message activity has a body worth drawing a card for.
   * Sessions recorded before the runner stopped persisting text-free outputs
   * still contain `output` rows with empty content; without this guard they
   * render as blank cards.
   */
  const hasMessageBody = (activity) => (
    (activity?.kind === 'input' || activity?.kind === 'output')
    && String(activity?.details?.content ?? '').trim() !== ''
  );

  /** Uppercase an activity kind for the collapsed-row pill (TOOL, LLM, …). */
  const formatKindLabel = (activity) => (activity?.kind ? String(activity.kind).toUpperCase() : '');

  /**
   * Build the collapsed-row text that follows the kind pill, without dumping
   * tool arguments. Joins name, summary, status, and latency with ·; omits
   * empty segments. Returns plain text for x-text (no HTML escaping).
   */
  const formatCollapsedDetail = (activity) => {
    const name = activity?.name ? String(activity.name) : '';
    const summary = activity?.summary ? String(activity.summary) : '';
    const status = formatStatusLabel(activity?.status);
    const latency = formatLatency(resolveLatencyMs(activity));
    return [name, summary, status, latency].filter(Boolean).join(' · ');
  };

  /**
   * Pretty-print activity details for curated Result/Arguments and Raw JSON.
   * String values that parse as JSON objects/arrays are pretty-printed;
   * other strings are shown as-is (no surrounding JSON quotes). Falls back
   * to String(value) when JSON.stringify fails (cycles, etc.).
   * Plain text only — Alpine x-text consumes the result.
   */
  const formatRawDetails = (value) => {
    if (typeof value === 'string') {
      try {
        const parsed = JSON.parse(value);
        if (parsed !== null && typeof parsed === 'object') {
          return JSON.stringify(parsed, null, 2);
        }
      } catch {
        // Not JSON — fall through to raw string content.
      }
      return value;
    }
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  };

  /** Sum finite activity costs from this store without traversing childStores. */
  const sumCostUsd = (store) => Object.values(store?.activities ?? {}).reduce(
    (sum, activity) => {
      if (activity?.cost_usd == null || activity.cost_usd === '') {
        return sum;
      }
      const cost = Number(activity.cost_usd);
      return Number.isFinite(cost) ? sum + cost : sum;
    },
    0,
  );

  /** Report whether an activity supports rich-content beautification. */
  const isBeautifiable = (activity) => activity?.kind === 'output';

  /**
   * Map logical nesting depth to capped indent plus an optional L{n} marker.
   * Depths at or below MAX_VISUAL_DEPTH indent fully; deeper rows cap indent
   * and show the true depth marker (e.g. L8).
   */
  const visualDepth = (depth) => {
    const level = typeof depth === 'number' && Number.isFinite(depth) ? depth : 0;
    if (level <= MAX_VISUAL_DEPTH) {
      return { indentLevel: level, showMarker: false, marker: null };
    }
    return { indentLevel: MAX_VISUAL_DEPTH, showMarker: true, marker: `L${level}` };
  };

  /**
   * Session statuses that no longer need a live stream.
   * AgentSessionStatus currently has one terminal value; activity terminal values
   * such as succeeded/failed are intentionally not used for session lifecycle.
   */
  const TERMINAL_SESSION_STATUSES = new Set(['done']);

  /** Bound reconnect work per session so a broken subtree cannot retry forever. */
  const MAX_RECONNECT_ATTEMPTS = 5;

  /** Cap exponential reconnect delay so recovery remains responsive. */
  const MAX_RECONNECT_DELAY_MS = 4000;

  /**
   * Report whether a revision value is safe to compare and store.
   * Rejects undefined/null/NaN/Infinity so malformed patches cannot regress state.
   */
  const isFiniteRevision = (revision) => typeof revision === 'number' && Number.isFinite(revision);

  /** Report whether session metadata describes a terminal session. */
  const isTerminalSession = (session) => TERMINAL_SESSION_STATUSES.has(session?.status);

  /**
   * Parse a typed SSE event's JSON object, returning null for malformed input.
   * EventSource receives the HTTP payload directly; the Redis channel envelope
   * has already been unwrapped by the SSE view.
   */
  const parseEventData = (event) => {
    if (!event || typeof event.data !== 'string') {
      return null;
    }
    try {
      const parsed = JSON.parse(event.data);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch {
      return null;
    }
  };

  /**
   * Compare two activity ids by ascending seq (stable tie-break on id).
   * Assumes both ids exist in activities.
   */
  const bySeq = (activities, leftId, rightId) => {
    const left = activities[leftId];
    const right = activities[rightId];
    const seqDelta = (left?.seq ?? 0) - (right?.seq ?? 0);
    if (seqDelta !== 0) {
      return seqDelta;
    }
    return String(leftId).localeCompare(String(rightId));
  };

  /**
   * Ensure the synthetic unresolved root row exists for display-only grouping.
   */
  const ensureSyntheticRoot = (store) => {
    if (store.activities[SYNTHETIC_UNRESOLVED_ID]) {
      return;
    }
    store.activities[SYNTHETIC_UNRESOLVED_ID] = {
      id: SYNTHETIC_UNRESOLVED_ID,
      session_id: store.sessionId,
      parent_id: null,
      seq: Number.MAX_SAFE_INTEGER,
      revision: 0,
      kind: 'span',
      status: 'succeeded',
      name: null,
      summary: null,
      details: {},
      model: null,
      cost_usd: null,
      latency_ms: null,
      child_session_id: null,
      unresolved: true,
    };
  };

  /**
   * Rebuild rootIds, childIds, and unresolvedChildIds from activities.
   * Snapshot-promoted orphans (snapshotUnresolvedIds) render under the synthetic
   * root while their canonical parent_id is still missing; they are never rewritten.
   */
  const reindex = (store) => {
    const rootIds = [];
    const childIds = Object.create(null);
    const unresolvedChildIds = Object.create(null);
    const syntheticChildren = [];

    for (const id of Object.keys(store.activities)) {
      if (id === SYNTHETIC_UNRESOLVED_ID) {
        continue;
      }
      const row = store.activities[id];
      const parentId = row.parent_id;
      if (parentId == null) {
        rootIds.push(id);
        delete store.snapshotUnresolvedIds[id];
        continue;
      }
      if (store.activities[parentId] && parentId !== SYNTHETIC_UNRESOLVED_ID) {
        if (!childIds[parentId]) {
          childIds[parentId] = [];
        }
        childIds[parentId].push(id);
        delete store.snapshotUnresolvedIds[id];
        continue;
      }
      if (store.snapshotUnresolvedIds[id]) {
        syntheticChildren.push(id);
        continue;
      }
      if (!unresolvedChildIds[parentId]) {
        unresolvedChildIds[parentId] = [];
      }
      unresolvedChildIds[parentId].push(id);
    }

    if (syntheticChildren.length > 0) {
      ensureSyntheticRoot(store);
      rootIds.push(SYNTHETIC_UNRESOLVED_ID);
      syntheticChildren.sort((a, b) => bySeq(store.activities, a, b));
      childIds[SYNTHETIC_UNRESOLVED_ID] = syntheticChildren;
    } else {
      delete store.activities[SYNTHETIC_UNRESOLVED_ID];
    }

    rootIds.sort((a, b) => bySeq(store.activities, a, b));
    for (const parentId of Object.keys(childIds)) {
      if (parentId === SYNTHETIC_UNRESOLVED_ID) {
        continue;
      }
      childIds[parentId].sort((a, b) => bySeq(store.activities, a, b));
    }
    for (const parentId of Object.keys(unresolvedChildIds)) {
      unresolvedChildIds[parentId].sort((a, b) => bySeq(store.activities, a, b));
    }

    store.rootIds = rootIds;
    store.childIds = childIds;
    store.unresolvedChildIds = unresolvedChildIds;
  };

  /**
   * After an authoritative snapshot, mark remaining unresolved children for
   * synthetic-root display without changing their canonical parent_id.
   */
  const promoteUnresolvedAfterSnapshot = (store) => {
    const pendingParentIds = Object.keys(store.unresolvedChildIds);
    if (pendingParentIds.length === 0) {
      return;
    }

    for (const parentId of pendingParentIds) {
      for (const orphanId of store.unresolvedChildIds[parentId]) {
        store.snapshotUnresolvedIds[orphanId] = true;
      }
    }

    reindex(store);
  };

  /**
   * Shallow-clone an activity dict so top-level store fields are not aliased.
   * Nested objects such as details are shared by reference and treated as read-only.
   */
  const cloneActivity = (activity) => ({ ...activity });

  /**
   * Walk ancestors looking for a manually collapsed activity.
   * Stops on missing parents or parent_id cycles so malformed trees cannot hang.
   */
  const hasManuallyCollapsedAncestor = (store, activityId) => {
    const seen = Object.create(null);
    let parentId = store.activities[activityId]?.parent_id ?? null;
    while (parentId != null) {
      if (seen[parentId]) {
        return false;
      }
      seen[parentId] = true;
      if (store.manualCollapseIds[parentId]) {
        return true;
      }
      const parent = store.activities[parentId];
      if (!parent) {
        return false;
      }
      parentId = parent.parent_id;
    }
    return false;
  };

  /**
   * Default disclosure for execution rows: only running subagents start open,
   * and only when no ancestor was manually collapsed. Input/output message bodies
   * are rendered independently of this disclosure state.
   */
  const defaultExpanded = (store, activityId) => {
    const row = store.activities[activityId];
    if (!row || row.kind !== 'subagent' || row.status !== 'running') {
      return false;
    }
    return !hasManuallyCollapsedAncestor(store, activityId);
  };

  /**
   * Build a per-session activity store. Session and activity IDs key all state;
   * child sessions never merge into this map.
   */
  function createActivityStore(sessionId, options = {}) {
    const configuredPath = Array.isArray(options?.ancestryPath) ? options.ancestryPath : [];
    const ancestryPath = configuredPath.includes(sessionId)
      ? [...configuredPath]
      : [...configuredPath, sessionId];
    const childSources = Object.create(null);
    const childNeedsRefresh = Object.create(null);
    const childLoadPromises = Object.create(null);
    const reconnectState = Object.create(null);
    let disposed = false;
    let rootLoaded = false;
    let rootSource = null;

    let store = {
      sessionId,
      ancestryPath,
      session: null,
      activities: Object.create(null),
      rootIds: [],
      childIds: Object.create(null),
      unresolvedChildIds: Object.create(null),
      /** Child session id → isolated per-session store. */
      childStores: Object.create(null),
      /** Child session id → local loading/failure state. */
      childLoadState: Object.create(null),
      /** Orphan ids from an authoritative snapshot that render under the synthetic root. */
      snapshotUnresolvedIds: Object.create(null),
      /** Page-lifetime expansion keys only — never localStorage/sessionStorage. */
      expandedIds: Object.create(null),
      manualExpandIds: Object.create(null),
      manualCollapseIds: Object.create(null),
      onNeedsRefresh: null,

      /**
       * Apply a partial session metadata patch without replacing activity state.
       * Assumes SSE patches are for this stream; an explicit foreign id is rejected.
       */
      applySessionUpdate(patch) {
        if (!patch || typeof patch !== 'object' || Array.isArray(patch)) {
          return;
        }
        if (patch.id != null && patch.id !== store.sessionId) {
          return;
        }
        store.session = { ...(store.session ?? { id: store.sessionId }), ...patch };
      },

      /**
       * Replace session metadata and activity maps from an authoritative snapshot.
       * Preserves expansion key maps; rejects cross-session and non-finite-revision
       * rows; promotes snapshot orphans into synthetic display grouping.
       */
      applySnapshot(snapshot) {
        store.session = snapshot?.session ? { ...snapshot.session } : null;
        store.activities = Object.create(null);
        store.snapshotUnresolvedIds = Object.create(null);

        const rows = Array.isArray(snapshot?.activities) ? snapshot.activities : [];
        for (const row of rows) {
          if (!row || row.session_id !== store.sessionId) {
            continue;
          }
          if (!isFiniteRevision(row.revision)) {
            continue;
          }
          store.activities[row.id] = cloneActivity(row);
        }

        reindex(store);
        promoteUnresolvedAfterSnapshot(store);
      },

      /**
       * Apply one activity upsert when revision is newer than the stored row.
       * Rejects cross-session rows and non-finite revisions. On parent_id mismatch,
       * keeps the canonical parent_id, applies other patch fields, and signals refresh.
       */
      applyUpsert(activity) {
        if (!activity || activity.session_id !== store.sessionId) {
          return;
        }
        if (!isFiniteRevision(activity.revision)) {
          return;
        }

        const existing = store.activities[activity.id];
        if (existing && activity.revision <= existing.revision) {
          return;
        }

        if (existing && activity.parent_id !== existing.parent_id) {
          if (typeof store.onNeedsRefresh === 'function') {
            store.onNeedsRefresh();
          }
          // Partial-field application: accept status/summary/etc. from the newer
          // patch while preserving the immutable canonical parent_id.
          const kept = cloneActivity(activity);
          kept.parent_id = existing.parent_id;
          store.activities[activity.id] = kept;
          reindex(store);
          return;
        }

        store.activities[activity.id] = cloneActivity(activity);
        reindex(store);
      },

      /**
       * Report whether an activity's disclosure should be open.
       * Manual expand/collapse outrank defaults; running subagents default open
       * unless a manually collapsed ancestor blocks auto-expansion.
       */
      isExpanded(id) {
        if (store.manualExpandIds[id]) {
          return true;
        }
        if (store.manualCollapseIds[id]) {
          return false;
        }
        if (store.expandedIds[id]) {
          return true;
        }
        return defaultExpanded(store, id);
      },

      /**
       * Set disclosure open/closed for an activity id.
       * manual:true records user intent (mutually exclusive expand/collapse maps).
       * manual:false is programmatic auto-expansion and must not clear an explicit
       * manual collapse (or clobber an explicit manual expand when collapsing).
       */
      setExpanded(id, expanded, options = {}) {
        const manual = options?.manual === true;
        if (manual) {
          if (expanded) {
            store.manualExpandIds[id] = true;
            delete store.manualCollapseIds[id];
            store.expandedIds[id] = true;
          } else {
            store.manualCollapseIds[id] = true;
            delete store.manualExpandIds[id];
            delete store.expandedIds[id];
          }
          return;
        }

        // Programmatic updates never overwrite explicit user intent for this id.
        if (expanded) {
          if (store.manualCollapseIds[id]) {
            return;
          }
          store.expandedIds[id] = true;
          return;
        }
        if (store.manualExpandIds[id]) {
          return;
        }
        delete store.expandedIds[id];
      },

      /**
       * Fetch an expanded subagent's authoritative snapshot into a separate store.
       * Same-origin fetch is assumed to carry the browser's auth cookie. All non-OK
       * responses, including both 403 and 404, intentionally become unavailable.
       */
      async ensureChildLoaded(activityId) {
        return loadChild(activityId, false);
      },

      /**
       * Match one child stream to current disclosure and child session status.
       * A prior collapse/stream close requires a fresh snapshot before reopening.
       */
      async syncChildSubscription(activityId) {
        const activity = childActivity(activityId);
        if (!activity) {
          return null;
        }
        const childId = activity.child_session_id;
        const childStore = store.childStores[childId];

        if (!store.isExpanded(activityId)) {
          closeChildSource(childId, true);
          childStore?.pauseSubscriptions();
          return childStore ?? null;
        }
        if (!childStore || childNeedsRefresh[childId]) {
          return loadChild(activityId, true);
        }
        if (isTerminalSession(childStore.session)) {
          closeChildSource(childId, false);
          childStore.pauseSubscriptions();
          return childStore;
        }
        openChildSource(activityId, childStore);
        return childStore;
      },

      /**
       * Report whether this store has been permanently disposed.
       * Reused child stores must revive before streams can reopen after dispose.
       */
      get disposed() {
        return disposed;
      },

      /**
       * Clear dispose and reconnect pause so streams may open again.
       * Mirrors the root revive path used by loadRoot/refreshExpandedSnapshots;
       * call on reused child stores before nested load/open work.
       */
      revive() {
        disposed = false;
        for (const state of Object.values(reconnectState)) {
          if (state.timer != null) {
            runtimeWindow.clearTimeout(state.timer);
            state.timer = null;
          }
          state.attempts = 0;
        }
      },

      /**
       * Load the root snapshot and open its stream when the session is running.
       * This minimal root lifecycle is shared by BFCache refresh and later page wiring.
       */
      async loadRoot() {
        store.revive();
        const loaded = await fetchSnapshot(store, store.sessionId);
        if (!loaded) {
          return false;
        }
        rootLoaded = true;
        store.onNeedsRefresh = () => {
          void refreshRootAfterStructuralPatch();
        };
        openRootSource();
        return true;
      },

      /**
       * Refresh the root and every still-expanded child before reconnecting streams.
       * This is the BFCache restoration path; snapshots remain authoritative.
       */
      async refreshExpandedSnapshots() {
        store.revive();
        const expandedReferences = expandedChildReferences();
        store.pauseSubscriptions();
        const loaded = await fetchSnapshot(store, store.sessionId);
        if (loaded) {
          rootLoaded = true;
          openRootSource();
        }
        for (const { activityId, childId } of expandedReferences) {
          const current = childActivity(activityId);
          let childStore;
          if (current?.child_session_id === childId && store.isExpanded(activityId)) {
            childStore = await loadChild(activityId, true);
          } else {
            childStore = await refreshDetachedChild(childId);
          }
          if (childStore) {
            await childStore.refreshExpandedChildrenOnly();
          }
        }
        return loaded;
      },

      /**
       * Temporarily close this store's root and descendant subscriptions.
       * Used when an ancestor collapses; unlike dispose, the store can be reopened.
       */
      pauseSubscriptions() {
        closeRootSource();
        for (const childId of Object.keys(childSources)) {
          closeChildSource(childId, true);
        }
        for (const childStore of Object.values(store.childStores)) {
          childStore.pauseSubscriptions();
        }
      },

      /**
       * Permanently release root and descendant sources and reconnect timers.
       * Page controllers may pass this directly to pagehide/Alpine teardown hooks.
       */
      dispose() {
        disposed = true;
        store.pauseSubscriptions();
        for (const state of Object.values(reconnectState)) {
          if (state.timer != null) {
            runtimeWindow.clearTimeout(state.timer);
            state.timer = null;
          }
        }
        for (const childStore of Object.values(store.childStores)) {
          childStore.dispose();
        }
      },
    };

    /** Return a valid subagent activity with a child session id, or null. */
    const childActivity = (activityId) => {
      const activity = store.activities[activityId];
      if (
        !activity
        || activity.kind !== 'subagent'
        || typeof activity.child_session_id !== 'string'
        || !activity.child_session_id
      ) {
        return null;
      }
      return activity;
    };

    /**
     * Fetch and apply one authoritative session snapshot.
     * Browser fetch/EventSource use same-origin cookie authentication by default.
     */
    const fetchSnapshot = async (targetStore, targetSessionId) => {
      try {
        const response = await runtimeWindow.fetch(SNAPSHOT_PATH(targetSessionId), {
          headers: { Accept: 'application/json' },
        });
        if (!response?.ok) {
          return false;
        }
        const snapshot = await response.json();
        if (
          !snapshot
          || typeof snapshot !== 'object'
          || snapshot.session?.id !== targetSessionId
        ) {
          return false;
        }
        targetStore.applySnapshot(snapshot);
        return true;
      } catch {
        return false;
      }
    };

    /** Return mutable reconnect state scoped to one session id. */
    const retryFor = (targetSessionId) => {
      if (!reconnectState[targetSessionId]) {
        reconnectState[targetSessionId] = { attempts: 0, timer: null };
      }
      return reconnectState[targetSessionId];
    };

    /** Cancel pending reconnect work for one session without affecting siblings. */
    const clearReconnect = (targetSessionId) => {
      const retry = reconnectState[targetSessionId];
      if (retry?.timer != null) {
        runtimeWindow.clearTimeout(retry.timer);
        retry.timer = null;
      }
    };

    /**
     * Attach validated activity/session handlers to one session EventSource.
     * The caller owns source registration and terminal/error cleanup.
     */
    const attachSourceHandlers = (source, targetStore, onTerminal, onFailure) => {
      source.addEventListener('open', () => {
        retryFor(targetStore.sessionId).attempts = 0;
      });
      source.addEventListener('session_activity', (event) => {
        const payload = parseEventData(event);
        if (payload?.operation !== 'upsert' || !payload.activity) {
          return;
        }
        targetStore.applyUpsert(payload.activity);
      });
      source.addEventListener('session_update', (event) => {
        const patch = parseEventData(event);
        if (!patch) {
          return;
        }
        targetStore.applySessionUpdate(patch);
        if (isTerminalSession(targetStore.session)) {
          onTerminal();
        }
      });
      source.addEventListener('error', onFailure);
    };

    /**
     * Close one child source and optionally require a snapshot before reopening.
     * Intentional closes also cancel that session's pending reconnect timer.
     */
    const closeChildSource = (childId, refreshRequired) => {
      const source = childSources[childId];
      if (source) {
        source.close();
        delete childSources[childId];
      }
      clearReconnect(childId);
      if (refreshRequired) {
        childNeedsRefresh[childId] = true;
      }
    };

    /** Close the root source and cancel its pending reconnect timer. */
    const closeRootSource = () => {
      if (rootSource) {
        rootSource.close();
        rootSource = null;
      }
      clearReconnect(store.sessionId);
    };

    /**
     * Schedule a bounded child reconnect after refreshing its snapshot.
     * Retry counters are keyed by child session id so siblings remain independent.
     */
    const scheduleChildReconnect = (activityId, childId) => {
      const retry = retryFor(childId);
      retry.attempts += 1;
      if (retry.attempts > MAX_RECONNECT_ATTEMPTS) {
        store.childLoadState[childId] = { status: 'disconnected' };
        return;
      }
      const delay = Math.min(250 * (2 ** (retry.attempts - 1)), MAX_RECONNECT_DELAY_MS);
      retry.timer = runtimeWindow.setTimeout(() => {
        retry.timer = null;
        const activity = childActivity(activityId);
        if (
          disposed
          || !activity
          || activity.child_session_id !== childId
          || !store.isExpanded(activityId)
        ) {
          return;
        }
        void loadChild(activityId, true);
      }, delay);
    };

    /** Open at most one EventSource for an expanded, running child session. */
    const openChildSource = (activityId, childStore) => {
      const childId = childStore.sessionId;
      if (
        disposed
        || childSources[childId]
        || !store.isExpanded(activityId)
        || isTerminalSession(childStore.session)
      ) {
        return;
      }
      // EventSource sends same-origin auth cookies; child authorization is still
      // checked independently by the child session endpoint.
      const source = new runtimeWindow.EventSource(EVENTS_PATH(childId));
      childSources[childId] = source;
      childNeedsRefresh[childId] = false;
      attachSourceHandlers(
        source,
        childStore,
        () => closeChildSource(childId, false),
        () => {
          if (childSources[childId] !== source) {
            return;
          }
          source.close();
          delete childSources[childId];
          childNeedsRefresh[childId] = true;
          scheduleChildReconnect(activityId, childId);
        },
      );
    };

    /** Schedule a bounded root reconnect after an authoritative snapshot refresh. */
    const scheduleRootReconnect = () => {
      const retry = retryFor(store.sessionId);
      retry.attempts += 1;
      if (retry.attempts > MAX_RECONNECT_ATTEMPTS) {
        return;
      }
      const delay = Math.min(250 * (2 ** (retry.attempts - 1)), MAX_RECONNECT_DELAY_MS);
      retry.timer = runtimeWindow.setTimeout(() => {
        retry.timer = null;
        if (disposed) {
          return;
        }
        void refreshRootAfterStructuralPatch();
      }, delay);
    };

    /** Open the root EventSource when loadRoot has a non-terminal snapshot. */
    const openRootSource = () => {
      if (disposed || !rootLoaded || rootSource || isTerminalSession(store.session)) {
        return;
      }
      const source = new runtimeWindow.EventSource(EVENTS_PATH(store.sessionId));
      rootSource = source;
      attachSourceHandlers(
        source,
        store,
        () => closeRootSource(),
        () => {
          if (rootSource !== source) {
            return;
          }
          source.close();
          rootSource = null;
          scheduleRootReconnect();
        },
      );
    };

    /** Refresh and reconnect the root after a structural patch or stream failure. */
    const refreshRootAfterStructuralPatch = async () => {
      closeRootSource();
      const loaded = await fetchSnapshot(store, store.sessionId);
      if (loaded) {
        rootLoaded = true;
        openRootSource();
      }
      return loaded;
    };

    /**
     * Fetch a child snapshot, preserving strict separation from the parent map.
     * forceRefresh is used after any prior close and by BFCache restoration.
     */
    const loadChild = async (activityId, forceRefresh) => {
      const activity = childActivity(activityId);
      if (!activity) {
        return null;
      }
      const childId = activity.child_session_id;
      if (store.ancestryPath.includes(childId)) {
        closeChildSource(childId, false);
        store.childLoadState[childId] = { status: 'cycle' };
        return null;
      }
      if (
        !forceRefresh
        && childSources[childId]
        && store.childLoadState[childId]?.status === 'loaded'
      ) {
        return store.childStores[childId];
      }
      if (childLoadPromises[childId]) {
        return childLoadPromises[childId];
      }
      if (forceRefresh) {
        closeChildSource(childId, false);
      }

      const promise = (async () => {
        store.childLoadState[childId] = { status: 'loading' };
        let childStore = store.childStores[childId];
        if (!childStore) {
          childStore = createActivityStore(childId, {
            ancestryPath: [...store.ancestryPath, childId],
          });
          store.childStores[childId] = childStore;
        } else {
          // Parent dispose recursively disposed this store; revive before nested open.
          childStore.revive();
        }
        const loaded = await fetchSnapshot(childStore, childId);
        if (!loaded) {
          closeChildSource(childId, false);
          store.childLoadState[childId] = { status: 'unavailable' };
          return null;
        }
        childStore.onNeedsRefresh = () => {
          childNeedsRefresh[childId] = true;
          void store.syncChildSubscription(activityId);
        };
        store.childLoadState[childId] = { status: 'loaded' };
        childNeedsRefresh[childId] = false;
        if (store.isExpanded(activityId) && !isTerminalSession(childStore.session)) {
          openChildSource(activityId, childStore);
        } else {
          closeChildSource(childId, false);
        }
        return childStore;
      })();
      childLoadPromises[childId] = promise;
      try {
        return await promise;
      } finally {
        delete childLoadPromises[childId];
      }
    };

    /**
     * Capture expanded child references before an authoritative parent refresh.
     * A BFCache refresh still refreshes these known children even if a mocked or
     * racing parent snapshot no longer includes the reference.
     */
    const expandedChildReferences = () => Object.keys(store.activities)
      .map((activityId) => ({ activityId, activity: childActivity(activityId) }))
      .filter(({ activityId, activity }) => activity && store.isExpanded(activityId))
      .map(({ activityId, activity }) => ({
        activityId,
        childId: activity.child_session_id,
      }));

    /**
     * Refresh a previously expanded child whose parent reference is no longer present.
     * Its stream stays closed because it is no longer renderable from this parent.
     */
    const refreshDetachedChild = async (childId) => {
      const childStore = store.childStores[childId];
      if (!childStore) {
        return null;
      }
      childStore.revive();
      const loaded = await fetchSnapshot(childStore, childId);
      if (!loaded) {
        store.childLoadState[childId] = { status: 'unavailable' };
        return null;
      }
      store.childLoadState[childId] = { status: 'loaded' };
      childNeedsRefresh[childId] = true;
      return childStore;
    };

    /** Refresh all expanded direct children and recurse through loaded descendants. */
    const refreshExpandedChildren = async () => {
      for (const { activityId } of expandedChildReferences()) {
        const childStore = await loadChild(activityId, true);
        if (childStore) {
          await childStore.refreshExpandedChildrenOnly();
        }
      }
    };

    /**
     * Refresh expanded descendants without refetching this store's own snapshot.
     * Parent registries own each child's current-session snapshot and stream.
     */
    store.refreshExpandedChildrenOnly = refreshExpandedChildren;

    // Alpine must observe mutations made through store closures by fetch/SSE handlers.
    // Wrapping here and rebinding the closure target keeps the standalone API unchanged.
    if (typeof runtimeWindow.Alpine?.reactive === 'function') {
      store = runtimeWindow.Alpine.reactive(store);
    }

    return store;
  }

  /**
   * Register the recursive activity-row controller before Alpine initializes page data.
   * Rows clone reviewable template markup from the session page, then nested rows reuse
   * this same controller with either the owning store or an isolated child-session store.
   */
  const registerActivityNode = () => {
    runtimeWindow.Alpine.data('activityNode', (getStore, activityId, depth) => ({
      getStore,
      activityId,
      depth,

      /**
       * Clone the shared row template, initialize it, and start any child load.
       * The explicit Alpine.initTree call is required, not decorative — see the
       * comment below before simplifying this to a bare append.
       */
      init() {
        const template = runtimeWindow.document.getElementById('activity-node-template');
        if (template && this.$el.childElementCount === 0) {
          const clone = template.content.cloneNode(true);
          const rows = Array.from(clone.children);
          this.$el.append(clone);
          // Alpine defers directive handlers until its tree walk finishes, so the
          // walk that runs this x-data has already passed the still-empty host
          // element, and x-for inserts rows inside mutateDom() with the mutation
          // observer disconnected. Both fallbacks are gone: initialize the cloned
          // rows here or every directive inside them stays inert.
          rows.forEach((row) => runtimeWindow.Alpine.initTree(row));
        }
        void this.syncSubagent();
      },

      /** Return the session store that owns this activity row. */
      get store() {
        return this.getStore();
      },

      /** Return the current highest-revision activity representation. */
      get activity() {
        return this.store.activities[this.activityId];
      },

      /** Return a DOM-safe disclosure id derived from the activity id. */
      get detailId() {
        return `activity-detail-${String(this.activityId).replace(/[^A-Za-z0-9_-]/g, '-')}`;
      },

      /** Return the isolated child store linked by a subagent activity, when loaded. */
      get childStore() {
        const childId = this.activity?.child_session_id;
        return childId ? this.store.childStores[childId] : null;
      },

      /** Return the local loading state for this row's child session. */
      get childState() {
        const childId = this.activity?.child_session_id;
        return childId ? this.store.childLoadState[childId]?.status : null;
      },

      /** Return capped visual nesting metadata without changing logical depth. */
      get depthView() {
        return visualDepth(this.depth);
      },

      /** Toggle one execution row while preserving page-level Follow preference. */
      async toggle() {
        const expanded = !this.store.isExpanded(this.activityId);
        this.store.setExpanded(this.activityId, expanded, { manual: true });
        await this.syncSubagent();
      },

      /** Load or pause the separately authorized child session for this subagent. */
      async syncSubagent() {
        if (this.activity?.kind !== 'subagent') {
          return;
        }
        if (this.store.isExpanded(this.activityId)) {
          await this.store.ensureChildLoaded(this.activityId);
        }
        await this.store.syncChildSubscription(this.activityId);
      },

      /** Build an accessible disclosure action label for the current activity. */
      toggleLabel() {
        const action = this.store.isExpanded(this.activityId) ? 'Collapse' : 'Expand';
        const label = this.activity?.name || this.activity?.kind || 'activity';
        return `${action} ${label}`;
      },

      /** Expose compact plain-text formatting to Alpine template expressions. */
      formatCollapsedDetail,
      formatKindLabel,

      /** Expose the empty-message guard to Alpine template expressions. */
      hasMessageBody,

      /** Expose raw-safe JSON formatting to Alpine template expressions. */
      formatRawDetails,
    }));
  };

  runtimeWindow.document.addEventListener('alpine:init', registerActivityNode);

  runtimeWindow.chiefActivityTree = {
    EVENTS_PATH,
    MAX_VISUAL_DEPTH,
    SNAPSHOT_PATH,
    createActivityStore,
    formatCollapsedDetail,
    formatKindLabel,
    formatRawDetails,
    hasMessageBody,
    isBeautifiable,
    sumCostUsd,
    visualDepth,
  };
})(window);

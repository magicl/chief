# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Per-vault agent bindings and first-sync readiness gate.

Tracks which agents are bound to which Obsidian vaults, with per-vault
refcounting (so a vault's supervisor process is started once for the first
interested agent and torn down once for the last), a per-vault first-sync
readiness flag, and a per-vault threading `Lock` for callers that need to
serialize supervisor lifecycle transitions. This module has no filesystem
or process knowledge — it is pure in-memory bookkeeping shared across
whatever layer actually starts/stops vault supervisors.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


class SyncPendingError(Exception):
    """Raised when a caller requires vault readiness before first sync has completed."""


@dataclass
class _VaultRecord:
    """Internal shared state for a single vault_id.

    `agent_ids` is the set of agents currently bound to this vault; its
    size is the refcount used to decide when to start/stop the vault's
    supervisor. `ready` is vault-level (not per agent) first-sync state.
    """

    agent_ids: set[str] = field(default_factory=set)
    ready: bool = False


@dataclass(frozen=True)
class VaultBinding:
    """Snapshot of one agent's configuration for a single vault.

    `roots` and `credential` are specific to the (agent_id, vault_id)
    pair. `ready` reflects vault-level first-sync readiness, which is
    shared across every agent bound to the same vault_id.
    """

    vault_id: str
    roots: list[str]
    credential: dict[str, Any]
    ready: bool


class VaultBindingStore:
    """In-memory registry of agent-to-vault bindings with refcounting and readiness.

    Not persisted to disk (Chief remains authoritative; credentials must not
    sit on the vault volume). On process restart, `replace_all_agents` is fed
    from Chief's snapshot API, and incremental `ensure_agent` / `release_agent`
    keep the map current. All public methods take a single internal guard lock
    for structural updates; `lock_for` exposes a separate per-vault lock for
    callers coordinating supervisor start/stop around readiness changes.
    """

    def __init__(self) -> None:
        """Initialize empty agent and vault state with a guard lock for structural changes."""
        self._guard = threading.Lock()
        self._agent_bindings: dict[str, dict[str, dict[str, Any]]] = {}
        self._vaults: dict[str, _VaultRecord] = {}
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, vault_id: str) -> threading.Lock:
        """Return the per-vault Lock for vault_id, creating it on first use.

        Intended for callers that need to serialize supervisor lifecycle
        transitions (e.g. start-then-mark-ready) for a single vault across
        threads. The lock is created lazily and reused for the life of the
        store, even if the vault's refcount later drops to zero.
        """
        with self._guard:
            lock = self._locks.get(vault_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[vault_id] = lock
            return lock

    def ensure_agent(self, agent_id: str, bindings: list[dict[str, Any]]) -> list[str]:
        """Replace agent_id's desired vault bindings, updating vault refcounts.

        `bindings` is a list of dicts with keys `vault_id`, `roots`, and
        `credential`, describing the full desired set for this agent (any
        vault previously bound by this agent but absent here is released).
        Returns the vault ids that need a supervisor (re)start: vaults
        newly referenced by this agent, or vaults this agent already
        referenced whose credential dict changed (a changed credential may
        require reconnecting to the vault).
        """
        new_bindings = {binding['vault_id']: binding for binding in bindings}
        with self._guard:
            old_bindings = self._agent_bindings.get(agent_id, {})
            needs_start: list[str] = []

            for vault_id, binding in new_bindings.items():
                record = self._vaults.setdefault(vault_id, _VaultRecord())
                is_new_reference = agent_id not in record.agent_ids
                credential_changed = not is_new_reference and old_bindings.get(vault_id, {}).get(
                    'credential'
                ) != binding.get('credential')
                if is_new_reference or credential_changed:
                    needs_start.append(vault_id)
                record.agent_ids.add(agent_id)

            for vault_id in set(old_bindings) - set(new_bindings):
                self._discard_reference(vault_id, agent_id)

            self._agent_bindings[agent_id] = new_bindings

        return needs_start

    def release_agent(self, agent_id: str) -> list[str]:
        """Remove all of agent_id's bindings, returning vault ids that lost their last reference.

        Callers should stop the supervisor for each returned vault id,
        since no remaining agent needs it.
        """
        with self._guard:
            old_bindings = self._agent_bindings.pop(agent_id, {})
            released = [vault_id for vault_id in old_bindings if self._discard_reference(vault_id, agent_id)]
        return released

    def _discard_reference(self, vault_id: str, agent_id: str) -> bool:
        """Drop agent_id's reference to vault_id; return True if the refcount hit 0.

        Assumes the caller already holds `self._guard`. The `_VaultRecord` is
        left in place at zero references rather than deleted (so `lock_for`
        keeps returning the same lock), but `ready` is cleared: once no
        agent needs the vault, its supervisor/checkout is expected to be
        torn down, so a later recreate must complete a fresh first sync
        before file ops are allowed again.
        """
        record = self._vaults.get(vault_id)
        if record is None:
            return False
        record.agent_ids.discard(agent_id)
        if not record.agent_ids:
            record.ready = False
            return True
        return False

    def get_binding(self, agent_id: str, vault_id: str) -> VaultBinding:
        """Return agent_id's current VaultBinding snapshot for vault_id.

        Raises KeyError if agent_id has no binding for vault_id. `ready`
        on the returned binding reflects vault-level readiness, shared
        across all agents bound to this vault_id.
        """
        binding = self._agent_bindings[agent_id][vault_id]
        record = self._vaults.get(vault_id)
        return VaultBinding(
            vault_id=vault_id,
            roots=binding['roots'],
            credential=binding.get('credential', {}),
            ready=record.ready if record is not None else False,
        )

    def mark_vault_ready(self, vault_id: str) -> None:
        """Mark vault_id as having completed first sync.

        Affects every agent bound to this vault, since readiness is
        tracked per vault rather than per agent binding.
        """
        with self._guard:
            record = self._vaults.setdefault(vault_id, _VaultRecord())
            record.ready = True

    def is_vault_ready(self, vault_id: str) -> bool:
        """Return True if vault_id has completed first sync, without requiring an agent binding.

        Unlike `get_binding`/`require_ready`, this needs no agent_id — used
        by the HTTP status route, which reports vault-level readiness given
        only a vault_id. Returns False for a vault_id with no record yet.
        """
        record = self._vaults.get(vault_id)
        return record.ready if record is not None else False

    def has_references(self, vault_id: str) -> bool:
        """Return True if any agent is currently bound to vault_id.

        Used after `release_agent` returns a vault id whose refcount hit zero
        to detect a concurrent re-acquire before stopping the vault supervisor.
        """
        with self._guard:
            record = self._vaults.get(vault_id)
            return record is not None and bool(record.agent_ids)

    def replace_all_agents(self, agents: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        """Atomically replace every agent binding from a Chief snapshot.

        ``agents`` is a list of ``{"agent_id": str, "bindings": [binding dicts...]}``.
        Returns ``(needs_start, released)``: vault ids that need supervisor start
        (new or credential-changed) and vault ids whose last agent left so the
        supervisor should stop. Readiness is preserved for vaults that remain
        referenced and were already ready; vaults that drop to zero refs clear ready.
        """
        with self._guard:
            previous_agents = set(self._agent_bindings)
            previous_vault_agents = {vault_id: set(record.agent_ids) for vault_id, record in self._vaults.items()}
            previous_ready = {vault_id: record.ready for vault_id, record in self._vaults.items()}
            previous_credentials: dict[tuple[str, str], Any] = {}
            for agent_id, bindings in self._agent_bindings.items():
                for vault_id, binding in bindings.items():
                    previous_credentials[(agent_id, vault_id)] = binding.get('credential')

            self._agent_bindings = {}
            self._vaults = {}

            needs_start: list[str] = []
            seen_start: set[str] = set()
            for entry in agents:
                agent_id = entry['agent_id']
                new_bindings = {binding['vault_id']: binding for binding in entry.get('bindings', [])}
                self._agent_bindings[agent_id] = new_bindings
                for vault_id, binding in new_bindings.items():
                    record = self._vaults.setdefault(vault_id, _VaultRecord())
                    record.agent_ids.add(agent_id)
                    was_referenced = agent_id in previous_vault_agents.get(vault_id, set())
                    credential_changed = was_referenced and previous_credentials.get(
                        (agent_id, vault_id)
                    ) != binding.get('credential')
                    if (not was_referenced or credential_changed) and vault_id not in seen_start:
                        needs_start.append(vault_id)
                        seen_start.add(vault_id)

            for vault_id, record in self._vaults.items():
                if previous_ready.get(vault_id) and record.agent_ids:
                    record.ready = True

            released = [
                vault_id
                for vault_id, old_agents in previous_vault_agents.items()
                if old_agents and vault_id not in self._vaults
            ]
            # Agents removed entirely: covered by vaults that disappeared above.
            del previous_agents
        return needs_start, released

    def require_ready(self, agent_id: str, vault_id: str) -> bool:
        """Return True if vault_id is ready for agent_id, else raise SyncPendingError.

        Raises KeyError if agent_id has no binding for vault_id.
        """
        binding = self.get_binding(agent_id, vault_id)
        if not binding.ready:
            raise SyncPendingError(f'Vault {vault_id!r} has not completed first sync yet')
        return True

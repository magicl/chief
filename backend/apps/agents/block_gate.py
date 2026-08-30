# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Pre-dispatch block gate — evaluates a trigger's declared block conditions.

A trigger may declare ordered ``blocks`` in its spec; every condition must report
ready before Chief starts a session. The gate lives in ``apps.agents`` so runner
dispatch and web rendering share one implementation, and so kind-specific
evaluators (Obsidian vault readiness today, other domains later) can register from
their own app the same way `apps.agents.lifecycle` handlers do — ``apps.agents``
never imports those apps.

Fail closed: an unregistered kind, a raising evaluator, or a malformed persisted
``trigger.spec`` / ``blocks`` value blocks dispatch. A missed session is recoverable
(the next beat, cron tick, or click retries); starting a session against an unready
dependency burns LLM turns and is not.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from libs.tools.readiness import BlockResult

if TYPE_CHECKING:
    from apps.agents.models import Agent, Trigger

logger = logging.getLogger(__name__)

# Mirrors the slug rule enforced on ``blocks[].kind`` at ingest, so a registry key can
# never drift from a kind an operator is able to write in YAML.
BLOCK_KIND_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# Deliberately generic: emitted when a condition cannot be judged at all (no handler, or
# the handler raised). Diagnostic detail goes to the log, never to this operator string.
UNEVALUATED_REASON = 'a block condition could not be evaluated'

BlockEvaluator = Callable[['Agent', 'Trigger', dict[str, Any]], BlockResult]

_evaluators: dict[str, BlockEvaluator] = {}


@dataclass(frozen=True)
class BlockGateResult:
    """Aggregate verdict for one trigger's ordered block list.

    ``reason`` carries the first blocking condition's operator-facing text and is empty
    when dispatch is allowed.
    """

    ready: bool
    reason: str = ''


def register_block_kind(kind: str, evaluate: BlockEvaluator) -> None:
    """Install the evaluator for one block ``kind``.

    Registration is last-writer-wins on the kind key, which keeps startup wiring
    idempotent across repeated ``AppConfig.ready()`` calls (tests, autoreload).
    """
    if not BLOCK_KIND_RE.match(kind):
        raise ValueError(f'invalid block kind {kind!r}: expected a slug matching {BLOCK_KIND_RE.pattern}')
    _evaluators[kind] = evaluate


def registered_block_kinds() -> frozenset[str]:
    """Return the block kinds that currently have an evaluator."""
    return frozenset(_evaluators)


def clear_block_kinds() -> None:
    """Remove all registered evaluators (tests only; prefer ``isolated_block_kinds``)."""
    _evaluators.clear()


@contextmanager
def isolated_block_kinds() -> Iterator[None]:
    """Temporarily clear the registry, then restore the previous one (tests only).

    Preserves AppConfig-registered kinds so later tests still see production wiring
    after a test registers its own kinds.
    """
    saved = dict(_evaluators)
    clear_block_kinds()
    try:
        yield
    finally:
        clear_block_kinds()
        _evaluators.update(saved)


def blocks_allow_dispatch(agent: Agent, trigger: Trigger) -> BlockGateResult:
    """Return whether every block condition on *trigger* currently allows a session.

    Conditions are evaluated in declared order and short-circuit on the first
    not-ready result, so an operator can put the cheapest or most decisive probe
    first. ``blocks`` absent or ``[]`` means no extra gates. Any other present
    shape (null, non-list, non-mapping entries, non-string kinds) is treated as
    unevaluable. Evaluators may perform I/O — callers must run this outside
    database row locks.
    """
    spec = trigger.spec
    if not isinstance(spec, dict):
        return _blocked_result(trigger, '', UNEVALUATED_REASON)

    if 'blocks' not in spec:
        return BlockGateResult(ready=True)

    blocks = spec['blocks']
    if isinstance(blocks, list) and not blocks:
        return BlockGateResult(ready=True)
    if not isinstance(blocks, list):
        return _blocked_result(trigger, '', UNEVALUATED_REASON)

    kinds: list[str] = []
    for entry in blocks:
        kind, result = _evaluate_entry(agent, trigger, entry)
        kinds.append(kind)
        if not result.ready:
            return _blocked_result(trigger, kind, result.reason)

    logger.info('Block gate: trigger %s allowed by conditions %s', trigger.pk, kinds)
    return BlockGateResult(ready=True)


def _blocked_result(trigger: Trigger, kind: str, reason: str) -> BlockGateResult:
    """Log one aggregate not-ready verdict and return it."""
    logger.info('Block gate: trigger %s blocked by condition %r: %s', trigger.pk, kind, reason)
    return BlockGateResult(ready=False, reason=reason)


def _evaluate_entry(agent: Agent, trigger: Trigger, entry: Any) -> tuple[str, BlockResult]:
    """Validate one list entry, then look up its handler only for a string kind.

    Non-mapping entries and non-string kinds never reach the registry, so an
    unhashable persisted ``kind`` cannot raise during lookup.
    """
    if not isinstance(entry, dict):
        return '', BlockResult(ready=False, reason=UNEVALUATED_REASON)
    kind = entry.get('kind', '')
    if not isinstance(kind, str):
        return '', BlockResult(ready=False, reason=UNEVALUATED_REASON)
    return kind, _evaluate_block(agent, trigger, kind, entry)


def _evaluate_block(agent: Agent, trigger: Trigger, kind: str, block: dict[str, Any]) -> BlockResult:
    """Evaluate one well-shaped condition, failing closed when it cannot be judged.

    An unregistered kind means wiring is missing or the row predates the handler; a
    raising evaluator means the probe itself is broken. Both block dispatch and are
    reported with a generic reason so provider failure text never reaches operators.
    """
    evaluate = _evaluators.get(kind)
    if evaluate is None:
        return BlockResult(ready=False, reason=UNEVALUATED_REASON)
    try:
        return evaluate(agent, trigger, block)
    except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
        logger.exception('Block gate: evaluator for block kind %r failed for trigger %s', kind, trigger.pk)
        return BlockResult(ready=False, reason=UNEVALUATED_REASON)

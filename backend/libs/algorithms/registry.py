# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Code catalog of background algorithms (not Django models, not Agents)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlgorithmInfo:
    """Stable registry row listed on the dashboard Background card."""

    algorithm_id: str
    display_name: str


CHAT_NAME_ID = 'chat_name'

ALGORITHMS: tuple[AlgorithmInfo, ...] = (AlgorithmInfo(algorithm_id=CHAT_NAME_ID, display_name='Chat name'),)


def index_algorithms(entries: tuple[AlgorithmInfo, ...]) -> dict[str, AlgorithmInfo]:
    """Build an id lookup; raise if any algorithm_id appears more than once."""
    by_id: dict[str, AlgorithmInfo] = {}
    for item in entries:
        if item.algorithm_id in by_id:
            raise ValueError(f'Duplicate algorithm_id: {item.algorithm_id}')
        by_id[item.algorithm_id] = item
    return by_id


_BY_ID = index_algorithms(ALGORITHMS)


def list_algorithms() -> tuple[AlgorithmInfo, ...]:
    """Return every registered algorithm, including those with zero runs."""
    return ALGORITHMS


def get_algorithm(algorithm_id: str) -> AlgorithmInfo | None:
    """Return the registry row for ``algorithm_id``, or None if unknown."""
    return _BY_ID.get(algorithm_id)

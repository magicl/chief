# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Scorers for inbox eval scenarios."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from olib.py.eval import Score


def score_inbox_state(
    *,
    expect: Mapping[str, Any],
    gmail: Any,
    clickup: Any,
    tool_calls: Sequence[str] | None = None,
) -> Score:
    """Compare Gmail and ClickUp mock end-state against a scenario expect mapping."""
    axes: dict[str, float] = {}
    notes: list[str] = []

    if 'tool_calls' in expect:
        _record_axis(axes, notes, 'tool_calls', list(tool_calls or []) == list(expect['tool_calls']))

    gmail_expect = expect.get('gmail') or {}
    if not isinstance(gmail_expect, Mapping):
        raise ValueError('expect.gmail must be a mapping')
    _score_gmail_axes(axes, notes, gmail_expect, gmail)

    clickup_expect = expect.get('clickup') or {}
    if not isinstance(clickup_expect, Mapping):
        raise ValueError('expect.clickup must be a mapping')
    _score_clickup_axes(axes, notes, clickup_expect, clickup)

    value = sum(axes.values()) / len(axes) if axes else 1.0
    return Score(value=value, axes=axes, notes=', '.join(notes))


def _score_gmail_axes(axes: dict[str, float], notes: list[str], expect: Mapping[str, Any], gmail: Any) -> None:
    """Add Gmail label and folder mutation axes to the current score."""
    labels_by_name = _label_ids_by_name(gmail)

    if 'labeled' in expect:
        expected_labeled = _mapping_list(expect['labeled'], 'expect.gmail.labeled')
        _record_axis(
            axes,
            notes,
            'gmail.labeled',
            all(
                _message_has_label(gmail, str(record['message_id']), labels_by_name.get(str(record['label_name'])))
                for record in expected_labeled
            ),
        )

    if 'has_label_names' in expect:
        expected_names = {str(name) for name in expect['has_label_names']}
        _record_axis(axes, notes, 'gmail.has_label_names', expected_names.issubset(labels_by_name))

    for axis_name, attr in (
        ('gmail.spam', 'spam'),
        ('gmail.archived', 'archived'),
        ('gmail.trashed', 'trashed'),
    ):
        key = axis_name.removeprefix('gmail.')
        if key in expect:
            _record_axis(axes, notes, axis_name, list(getattr(gmail, attr)) == list(expect[key]))


def _score_clickup_axes(axes: dict[str, float], notes: list[str], expect: Mapping[str, Any], clickup: Any) -> None:
    """Add ClickUp mutation axes to the current score."""
    if 'created_tasks' in expect:
        expected = _mapping_list(expect['created_tasks'], 'expect.clickup.created_tasks')
        _record_axis(
            axes,
            notes,
            'clickup.created_tasks',
            _records_match_expected(expected, list(clickup.created_tasks)),
        )


def _record_axis(axes: dict[str, float], notes: list[str], name: str, passed: bool) -> None:
    """Store one exact-match axis and append a note for failed axes."""
    axes[name] = 1.0 if passed else 0.0
    if not passed:
        notes.append(f'{name}=0')


def _label_ids_by_name(gmail: Any) -> dict[str, str]:
    """Return Gmail label ids keyed by display name from a mock client."""
    return {str(label['name']): str(label['id']) for label in gmail.list_labels()}


def _message_has_label(gmail: Any, message_id: str, label_id: str | None) -> bool:
    """Return whether a Gmail mock message currently has the resolved label id."""
    if label_id is None:
        return False
    return label_id in gmail.get_message(message_id).get('labelIds', [])


def _records_match_expected(expected: list[Mapping[str, Any]], actual: list[Mapping[str, Any]]) -> bool:
    """Compare expected records as ordered subsets of actual mock records."""
    if len(expected) != len(actual):
        return False
    return all(
        all(actual_item.get(key) == value for key, value in expected_item.items())
        for expected_item, actual_item in zip(expected, actual, strict=True)
    )


def _mapping_list(value: Any, label: str) -> list[Mapping[str, Any]]:
    """Validate that a YAML list contains mapping records."""
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a list')
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f'{label} entries must be mappings')
    return value

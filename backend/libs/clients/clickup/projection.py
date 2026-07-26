# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Project ClickUp spaces, lists, tasks, and mutation acknowledgements to compact records."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, NotRequired, TypedDict, TypeVar

from libs.clients.compact import (
    ATTACHMENT_LIMIT,
    BODY_CHAR_LIMIT,
    CLICKUP_COMMENT_CHAR_LIMIT,
    CLICKUP_COMMENT_LIMIT,
    CLICKUP_SUBTASK_LIMIT,
    truncate_text,
)

_SUPPORTED_CUSTOM_FIELD_TYPES = {
    'automatic_progress',
    'checkbox',
    'currency',
    'date',
    'drop_down',
    'email',
    'emoji',
    'formula',
    'labels',
    'list_relationship',
    'location',
    'manual_progress',
    'number',
    'phone',
    'progress',
    'rating',
    'relationship',
    'rollup',
    'short_text',
    'tasks',
    'text',
    'url',
    'users',
}
_ADVISORY_MESSAGES = {
    'comments_unavailable': 'Comments could not be loaded.',
}

ItemT = TypeVar('ItemT')


class PersonProjection(TypedDict):
    """Define the stable public identity fields for one ClickUp person."""

    id: str
    display_name: str
    email: NotRequired[str]


class PriorityProjection(TypedDict):
    """Define compact ClickUp priority identity without presentation metadata."""

    id: str | None
    priority: str | None


class TaskSummaryProjection(TypedDict):
    """Define the exact shared fields returned by every task projection."""

    id: str
    custom_id: str | None
    name: str
    status: str | None
    assignees: list[PersonProjection]
    priority: PriorityProjection | None
    due_date: str | None
    url: str | None
    date_updated: str | None


class SpaceProjection(TypedDict):
    """Define the exact public fields retained for one ClickUp space."""

    id: str
    name: str
    archived: bool


class SpacesProjection(TypedDict):
    """Define the exact projected ClickUp spaces collection envelope."""

    spaces: list[SpaceProjection]


class ListProjection(TypedDict):
    """Define one ClickUp list with only optional stable parent references."""

    id: str
    name: str
    archived: bool
    space_id: NotRequired[str]
    folder_id: NotRequired[str]


class ListsProjection(TypedDict):
    """Define the exact projected ClickUp lists collection envelope."""

    lists: list[ListProjection]


class TaskListProjection(TypedDict):
    """Define one task page containing only compact summaries and pagination state."""

    tasks: list[TaskSummaryProjection]
    last_page: NotRequired[bool]


class MutationAckProjection(TypedDict):
    """Define the exact compact acknowledgement for one affected ClickUp task."""

    ok: bool
    task_id: str
    url: NotRequired[str]
    deleted: NotRequired[bool]
    status: NotRequired[str]
    name: NotRequired[str]


class SubtaskProjection(TypedDict):
    """Define the exact bounded summary subset retained for a child task."""

    id: str
    custom_id: str | None
    name: str
    status: str | None
    assignees: list[PersonProjection]
    priority: PriorityProjection | None
    due_date: str | None
    url: str | None


class IdentityProjection(TypedDict):
    """Define one compact provider object identity."""

    id: str | None
    name: str | None


class LocationProjection(TypedDict):
    """Define task placement without provider hierarchy or settings."""

    list: IdentityProjection | None
    folder: IdentityProjection | None
    space: IdentityProjection | None


class TagProjection(TypedDict):
    """Define one compact tag without colors or ordering."""

    name: str


SafeScalar = str | int | float | bool | None


class CustomFieldOptionProjection(TypedDict):
    """Define one resolved dropdown or label option."""

    id: str
    name: str


class CustomFieldCoordinatesProjection(TypedDict, total=False):
    """Define allowlisted numeric coordinates for a location field."""

    lat: int | float
    lng: int | float


class CustomFieldLocationProjection(TypedDict, total=False):
    """Define allowlisted location value fields."""

    formatted_address: str
    location: CustomFieldCoordinatesProjection


class CustomFieldProgressProjection(TypedDict, total=False):
    """Define allowlisted numeric progress values."""

    current: int | float
    start: int | float
    end: int | float
    percent_completed: int | float


CustomFieldValue = (
    SafeScalar
    | list[SafeScalar]
    | list[PersonProjection]
    | list[CustomFieldOptionProjection]
    | list[SafeScalar | CustomFieldOptionProjection]
    | CustomFieldOptionProjection
    | CustomFieldLocationProjection
    | CustomFieldProgressProjection
)


class CustomFieldProjection(TypedDict):
    """Define one custom-field value without provider configuration."""

    id: str
    name: str
    type: str
    value: CustomFieldValue


class ResourceSummaryProjection(TypedDict):
    """Define a compact related-task resource summary."""

    id: str
    name: str
    url: str | None
    status: str | None


class ChecklistItemProjection(TypedDict):
    """Define one compact checklist item."""

    id: str
    name: str
    resolved: bool


class ChecklistProjection(TypedDict):
    """Summarize checklist completion without duplicating checklist items."""

    id: str
    name: str
    resolved: int
    unresolved: int
    items: list[ChecklistItemProjection]


class AttachmentProjection(TypedDict):
    """Define allowlisted attachment metadata without transport details."""

    filename: str
    mime_type: str | None
    extension: str | None
    size: int | None
    uploader: PersonProjection | None
    date: str | None
    url: str | None
    url_w_query: str | None
    url_w_host: str | None


class CommentProjection(TypedDict):
    """Define one bounded ClickUp comment."""

    id: str
    text: str
    date: str | None
    user: PersonProjection | None
    text_truncation: NotRequired[dict[str, object]]


class CollectionMetaProjection(TypedDict):
    """Define explicit collection inclusion and omission counts."""

    included: int
    total: int | None
    truncated: bool
    omitted_count: int


class AdvisoryProjection(TypedDict):
    """Define one safe optional-fetch advisory."""

    code: str
    message: str


class TaskFullProjection(TaskSummaryProjection):
    """Define the full task shape by extending its required summary fields."""

    description: str
    description_truncation: NotRequired[dict[str, object]]
    markdown_description: str
    markdown_description_truncation: NotRequired[dict[str, object]]
    location: LocationProjection
    creator: PersonProjection | None
    watchers: list[PersonProjection]
    mentions: list[PersonProjection]
    tags: list[TagProjection]
    start_date: str | None
    time_estimate: int | float | None
    points: int | float | None
    custom_fields: list[CustomFieldProjection]
    parent: str | None
    dependencies: list[ResourceSummaryProjection]
    linked_tasks: list[ResourceSummaryProjection]
    checklists: list[ChecklistProjection]
    attachments: list[AttachmentProjection]
    attachments_meta: CollectionMetaProjection
    subtasks: list[SubtaskProjection]
    subtasks_meta: CollectionMetaProjection
    comments: list[CommentProjection]
    comments_meta: CollectionMetaProjection
    advisories: list[AdvisoryProjection]


def _stable_id(value: object) -> str | None:
    """Normalize provider string or integer ids while rejecting booleans and empty values."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object) -> str | None:
    """Return a non-empty string value, treating malformed provider values as absent."""
    return value if isinstance(value, str) and value else None


def _number(value: object) -> int | float | None:
    """Return finite non-boolean provider numbers only."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_scalar(value: object) -> SafeScalar:
    """Normalize one JSON-safe scalar and replace structured or non-finite values with null."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _safe_value(value: object) -> SafeScalar | list[SafeScalar]:
    """Normalize a custom-field scalar or flat scalar list without retaining structures."""
    if isinstance(value, list):
        return [
            normalized
            for item in value
            if not isinstance(item, (Mapping, list, tuple, set))
            if (normalized := _safe_scalar(item)) is not None or item is None
        ]
    return _safe_scalar(value)


def _timestamp(value: object) -> str | None:
    """Normalize provider epoch strings or integers to the public string representation."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _string(value)


def project_person(raw: object) -> PersonProjection | None:
    """Project stable ClickUp person identity; records without a non-empty id are omitted."""
    if not isinstance(raw, Mapping):
        return None
    person_id = _stable_id(raw.get('id'))
    if person_id is None:
        return None
    display_name = _string(raw.get('username')) or _string(raw.get('name')) or ''
    projected: PersonProjection = {'id': person_id, 'display_name': display_name}
    email = _string(raw.get('email'))
    if email is not None:
        projected['email'] = email
    return projected


def _people(raw: object) -> list[PersonProjection]:
    """Project a provider people list while omitting malformed identities."""
    if not isinstance(raw, list):
        return []
    return [person for item in raw if (person := project_person(item)) is not None]


def _merge_people(*collections: object) -> list[PersonProjection]:
    """Merge person and group collections in provider order, deduplicating stable ids."""
    projected: list[PersonProjection] = []
    seen: set[str] = set()
    for collection in collections:
        for person in _people(collection):
            if person['id'] not in seen:
                projected.append(person)
                seen.add(person['id'])
    return projected


def _status(raw: object) -> str | None:
    """Normalize ClickUp's object or string status forms to a name."""
    if isinstance(raw, Mapping):
        return _string(raw.get('status')) or _string(raw.get('name'))
    return _string(raw)


def _priority(raw: object) -> PriorityProjection | None:
    """Project priority id and provider priority label only."""
    if not isinstance(raw, Mapping):
        return None
    priority_id = _stable_id(raw.get('id'))
    priority = _string(raw.get('priority')) or _string(raw.get('name'))
    if priority_id is None and priority is None:
        return None
    return {'id': priority_id, 'priority': priority}


def project_task_id(raw: Mapping[str, Any]) -> str:
    """Extract one stable ClickUp task id or raise a fixed local validation failure."""
    task_id = _stable_id(raw.get('id'))
    if task_id is None:
        raise ValueError('Invalid ClickUp task id')
    return task_id


def project_task_summary(raw: Mapping[str, Any]) -> TaskSummaryProjection:
    """Project the exact compact task summary, requiring a stable provider task id."""
    task_id = project_task_id(raw)
    return {
        'id': task_id,
        'custom_id': _string(raw.get('custom_id')),
        'name': _string(raw.get('name')) or '',
        'status': _status(raw.get('status')),
        'assignees': _people(raw.get('assignees')),
        'priority': _priority(raw.get('priority')),
        'due_date': _string(raw.get('due_date')),
        'url': _string(raw.get('url')),
        'date_updated': _string(raw.get('date_updated')),
    }


def project_spaces(raw: Mapping[str, Any]) -> SpacesProjection:
    """Project spaces to the exact id, name, and archived allowlist."""
    records = raw.get('spaces')
    if not isinstance(records, list):
        return {'spaces': []}
    spaces: list[SpaceProjection] = []
    for record in records:
        if not isinstance(record, Mapping) or (space_id := _stable_id(record.get('id'))) is None:
            continue
        spaces.append(
            {
                'id': space_id,
                'name': _string(record.get('name')) or '',
                'archived': record.get('archived') is True,
            }
        )
    return {'spaces': spaces}


def _parent_id(raw: Mapping[str, Any], key: str) -> str | None:
    """Read a flat parent id or the id from ClickUp's minimal nested parent record."""
    direct = _stable_id(raw.get(f'{key}_id'))
    if direct is not None:
        return direct
    nested = raw.get(key)
    return _stable_id(nested.get('id')) if isinstance(nested, Mapping) else None


def project_lists(raw: Mapping[str, Any]) -> ListsProjection:
    """Project lists to exact identity, archive state, and supplied parent ids."""
    records = raw.get('lists')
    if not isinstance(records, list):
        return {'lists': []}
    lists: list[ListProjection] = []
    for record in records:
        if not isinstance(record, Mapping) or (list_id := _stable_id(record.get('id'))) is None:
            continue
        projected: ListProjection = {
            'id': list_id,
            'name': _string(record.get('name')) or '',
            'archived': record.get('archived') is True,
        }
        if (space_id := _parent_id(record, 'space')) is not None:
            projected['space_id'] = space_id
        if (folder_id := _parent_id(record, 'folder')) is not None:
            projected['folder_id'] = folder_id
        lists.append(projected)
    return {'lists': lists}


def project_task_list(raw: Mapping[str, Any]) -> TaskListProjection:
    """Project a task page while skipping entries without stable provider identity."""
    records = raw.get('tasks')
    tasks: list[TaskSummaryProjection] = []
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping) or _stable_id(record.get('id')) is None:
                continue
            tasks.append(project_task_summary(record))
    projected: TaskListProjection = {'tasks': tasks}
    last_page = raw.get('last_page')
    if isinstance(last_page, bool):
        projected['last_page'] = last_page
    return projected


def project_mutation_ack(
    raw: Mapping[str, Any],
    *,
    task_id: str | None = None,
) -> MutationAckProjection:
    """Project one mutation acknowledgement, preferring the caller's affected task id."""
    affected_task_id = _stable_id(task_id) or _stable_id(raw.get('id')) or _stable_id(raw.get('task_id'))
    if affected_task_id is None:
        raise ValueError('Invalid ClickUp mutation response')
    projected: MutationAckProjection = {'ok': True, 'task_id': affected_task_id}
    if (url := _string(raw.get('url'))) is not None:
        projected['url'] = url
    if raw.get('deleted') is True:
        projected['deleted'] = True
    if (status := _status(raw.get('status'))) is not None:
        projected['status'] = status
    if (name := _string(raw.get('name'))) is not None:
        projected['name'] = name
    return projected


def _project_subtask(raw: Mapping[str, Any]) -> SubtaskProjection:
    """Project the locked child-task subset without duplicating full task fields."""
    summary = project_task_summary(raw)
    return {
        'id': summary['id'],
        'custom_id': summary['custom_id'],
        'name': summary['name'],
        'status': summary['status'],
        'assignees': summary['assignees'],
        'priority': summary['priority'],
        'due_date': summary['due_date'],
        'url': summary['url'],
    }


def _identity(raw: object) -> IdentityProjection | None:
    """Project an id/name pair when at least one stable identity field is present."""
    if not isinstance(raw, Mapping):
        return None
    identity_id = _stable_id(raw.get('id'))
    name = _string(raw.get('name'))
    if identity_id is None and name is None:
        return None
    return {'id': identity_id, 'name': name}


def _tags(raw: object) -> list[TagProjection]:
    """Project non-empty tag names from provider tag records."""
    if not isinstance(raw, list):
        return []
    projected: list[TagProjection] = []
    for item in raw:
        if isinstance(item, Mapping) and (name := _string(item.get('name'))) is not None:
            projected.append({'name': name})
    return projected


def _custom_field_options(raw: object) -> dict[str, CustomFieldOptionProjection]:
    """Index only stable option identity and display name from type_config."""
    if not isinstance(raw, Mapping):
        return {}
    options = raw.get('options')
    if not isinstance(options, list):
        return {}
    indexed: dict[str, CustomFieldOptionProjection] = {}
    for option in options:
        if not isinstance(option, Mapping) or (option_id := _stable_id(option.get('id'))) is None:
            continue
        projected: CustomFieldOptionProjection = {
            'id': option_id,
            'name': _string(option.get('name')) or _string(option.get('label')) or '',
        }
        indexed[option_id] = projected
    return indexed


def _custom_field_option(value: object, *, options: Mapping[str, CustomFieldOptionProjection]) -> CustomFieldValue:
    """Resolve one option id to stable metadata, retaining a safe unmatched scalar."""
    option_id = _stable_id(value)
    if option_id is not None and option_id in options:
        return options[option_id]
    return _safe_scalar(value)


def _custom_field_labels(value: object, *, options: Mapping[str, CustomFieldOptionProjection]) -> CustomFieldValue:
    """Resolve a labels list while excluding malformed or structured unknown values."""
    if not isinstance(value, list):
        return []
    projected: list[SafeScalar | CustomFieldOptionProjection] = []
    for item in value:
        option_id = _stable_id(item)
        if option_id is not None and option_id in options:
            projected.append(options[option_id])
        elif not isinstance(item, (Mapping, list, tuple, set)):
            normalized = _safe_scalar(item)
            if normalized is not None or item is None:
                projected.append(normalized)
    return projected


def _custom_field_location(value: object) -> CustomFieldLocationProjection | None:
    """Project a location's formatted address and finite latitude/longitude only."""
    if not isinstance(value, Mapping):
        return None
    projected: CustomFieldLocationProjection = {}
    if (formatted_address := _string(value.get('formatted_address'))) is not None:
        projected['formatted_address'] = formatted_address
    raw_coordinates = value.get('location')
    if isinstance(raw_coordinates, Mapping):
        coordinates: CustomFieldCoordinatesProjection = {}
        if (latitude := _number(raw_coordinates.get('lat'))) is not None:
            coordinates['lat'] = latitude
        if (longitude := _number(raw_coordinates.get('lng'))) is not None:
            coordinates['lng'] = longitude
        if coordinates:
            projected['location'] = coordinates
    return projected or None


def _custom_field_users(value: object) -> list[PersonProjection]:
    """Project users or user ids through the existing stable person shape."""
    if not isinstance(value, list):
        return []
    projected: list[PersonProjection] = []
    for item in value:
        person = project_person(item)
        if person is None and (person_id := _stable_id(item)) is not None:
            person = {'id': person_id, 'display_name': ''}
        if person is not None:
            projected.append(person)
    return projected


def _custom_field_relationships(value: object) -> list[SafeScalar]:
    """Project task relationship values to stable task ids only."""
    if not isinstance(value, list):
        return []
    projected: list[SafeScalar] = []
    for item in value:
        if isinstance(item, Mapping):
            nested_task = item.get('task')
            relationship_id = (
                _stable_id(item.get('id'))
                or _stable_id(item.get('task_id'))
                or (_stable_id(nested_task.get('id')) if isinstance(nested_task, Mapping) else None)
            )
        else:
            relationship_id = _stable_id(item)
        if relationship_id is not None:
            projected.append(relationship_id)
    return projected


def _custom_field_progress(value: object) -> CustomFieldProgressProjection | SafeScalar:
    """Project known progress counters or retain a finite scalar provider value."""
    if not isinstance(value, Mapping):
        return _safe_scalar(value)
    projected: CustomFieldProgressProjection = {}
    if (current := _number(value.get('current'))) is not None:
        projected['current'] = current
    if (start := _number(value.get('start'))) is not None:
        projected['start'] = start
    if (end := _number(value.get('end'))) is not None:
        projected['end'] = end
    if (percent_completed := _number(value.get('percent_completed'))) is not None:
        projected['percent_completed'] = percent_completed
    return projected


def _custom_field_value(field_type: str, value: object, *, type_config: object) -> CustomFieldValue:
    """Dispatch supported custom-field values to type-specific allowlisted normalizers."""
    options = _custom_field_options(type_config)
    if field_type == 'drop_down':
        return _custom_field_option(value, options=options)
    if field_type == 'labels':
        return _custom_field_labels(value, options=options)
    if field_type == 'location':
        return _custom_field_location(value)
    if field_type == 'users':
        return _custom_field_users(value)
    if field_type in {'tasks', 'relationship', 'list_relationship'}:
        return _custom_field_relationships(value)
    if field_type in {'progress', 'automatic_progress', 'manual_progress'}:
        return _custom_field_progress(value)
    return _safe_value(value)


def _custom_fields(raw: object) -> list[CustomFieldProjection]:
    """Project custom-field identities and type-aware values without exposing type configuration."""
    if not isinstance(raw, list):
        return []
    projected: list[CustomFieldProjection] = []
    for item in raw:
        if not isinstance(item, Mapping) or (field_id := _stable_id(item.get('id'))) is None:
            continue
        raw_field_type = _string(item.get('type'))
        field_type = raw_field_type if raw_field_type in _SUPPORTED_CUSTOM_FIELD_TYPES else 'unknown'
        projected.append(
            {
                'id': field_id,
                'name': _string(item.get('name')) or '',
                'type': field_type,
                'value': (
                    None
                    if field_type == 'unknown'
                    else _custom_field_value(field_type, item.get('value'), type_config=item.get('type_config'))
                ),
            }
        )
    return projected


def _resource_summary(
    raw: object, *, fallback_id_key: str, nested_key: str | None = None
) -> ResourceSummaryProjection | None:
    """Project one related task, using its relationship id only when no nested resource is supplied."""
    if not isinstance(raw, Mapping):
        return None
    nested = raw.get(nested_key) if nested_key is not None else None
    resource = nested if isinstance(nested, Mapping) else raw
    resource_id = _stable_id(resource.get('id')) or _stable_id(raw.get(fallback_id_key))
    if resource_id is None:
        return None
    return {
        'id': resource_id,
        'name': _string(resource.get('name')) or _string(raw.get('name')) or '',
        'url': _string(resource.get('url')) or _string(raw.get('url')),
        'status': _status(resource.get('status')) or _status(raw.get('status')),
    }


def _dependencies(raw: object) -> list[ResourceSummaryProjection]:
    """Project dependencies as resource summaries, where depends_on identifies the dependency."""
    if not isinstance(raw, list):
        return []
    return [
        projected
        for item in raw
        if (projected := _resource_summary(item, fallback_id_key='depends_on', nested_key='depends_on')) is not None
    ]


def _linked_tasks(raw: object) -> list[ResourceSummaryProjection]:
    """Project linked tasks as compact resource summaries rather than relationship records."""
    if not isinstance(raw, list):
        return []
    return [
        projected
        for item in raw
        if (projected := _resource_summary(item, fallback_id_key='task_id', nested_key='task')) is not None
    ]


def _resolved(value: object) -> bool:
    """Normalize ClickUp boolean and zero/one completion flags."""
    return value is True or value == 1


def _checklist_item(raw: object) -> ChecklistItemProjection | None:
    """Project one checklist item only when it has a stable provider id."""
    if not isinstance(raw, Mapping) or (item_id := _stable_id(raw.get('id'))) is None:
        return None
    return {
        'id': item_id,
        'name': _string(raw.get('name')) or '',
        'resolved': _resolved(raw.get('resolved')),
    }


def _checklists(raw: object) -> list[ChecklistProjection]:
    """Project checklist completion counts and compact allowlisted items."""
    if not isinstance(raw, list):
        return []
    projected: list[ChecklistProjection] = []
    for item in raw:
        if not isinstance(item, Mapping) or (checklist_id := _stable_id(item.get('id'))) is None:
            continue
        items = item.get('items')
        raw_items = items if isinstance(items, list) else []
        checklist_items = [
            projected_item
            for checklist_item in raw_items
            if (projected_item := _checklist_item(checklist_item)) is not None
        ]
        resolved_count = sum(1 for checklist_item in checklist_items if checklist_item['resolved'])
        projected.append(
            {
                'id': checklist_id,
                'name': _string(item.get('name')) or '',
                'resolved': resolved_count,
                'unresolved': len(checklist_items) - resolved_count,
                'items': checklist_items,
            }
        )
    return projected


def _attachment(raw: object) -> AttachmentProjection | None:
    """Project one attachment only when it has recognizable attachment metadata."""
    if not isinstance(raw, Mapping):
        return None
    filename = _string(raw.get('title')) or _string(raw.get('filename'))
    url = _string(raw.get('url'))
    url_w_query = _string(raw.get('url_w_query'))
    url_w_host = _string(raw.get('url_w_host'))
    if filename is None and url is None and url_w_query is None and url_w_host is None:
        return None
    size = raw.get('size')
    normalized_size = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
    return {
        'filename': filename or '',
        'mime_type': _string(raw.get('mimetype')) or _string(raw.get('mime_type')) or _string(raw.get('type')),
        'extension': _string(raw.get('extension')),
        'size': normalized_size,
        'uploader': project_person(raw.get('user')) or project_person(raw.get('uploader')),
        'date': _timestamp(raw.get('date')),
        'url': url,
        'url_w_query': url_w_query,
        'url_w_host': url_w_host,
    }


def _is_attachment_record(raw: object) -> bool:
    """Recognize valid attachment metadata using only cheap identity fields."""
    if not isinstance(raw, Mapping):
        return False
    return any(_string(raw.get(key)) is not None for key in ('title', 'filename', 'url', 'url_w_query', 'url_w_host'))


def _subtask(raw: object) -> SubtaskProjection | None:
    """Project one subtask only when it has the stable identity required by summaries."""
    if not isinstance(raw, Mapping) or _stable_id(raw.get('id')) is None:
        return None
    return _project_subtask(raw)


def _is_subtask_record(raw: object) -> bool:
    """Recognize subtask records using only their mandatory stable id."""
    return isinstance(raw, Mapping) and _stable_id(raw.get('id')) is not None


def _retain_valid(
    records: Sequence[object],
    *,
    limit: int,
    is_valid: Callable[[object], bool],
    project: Callable[[object], ItemT | None],
) -> tuple[list[ItemT], int]:
    """Count all valid records while projecting no more than the retained limit."""
    retained: list[ItemT] = []
    valid_count = 0
    for record in records:
        if not is_valid(record):
            continue
        valid_count += 1
        if len(retained) >= limit:
            continue
        projected = project(record)
        if projected is not None:
            retained.append(projected)
    return retained, valid_count


def _comment(raw: object) -> CommentProjection | None:
    """Project one stable comment identity with bounded plain text."""
    if not isinstance(raw, Mapping) or (comment_id := _stable_id(raw.get('id'))) is None:
        return None
    text = _string(raw.get('comment_text')) or _string(raw.get('text')) or ''
    bounded_text, truncation = truncate_text(text, limit=CLICKUP_COMMENT_CHAR_LIMIT)
    projected: CommentProjection = {
        'id': comment_id,
        'text': bounded_text,
        'date': _timestamp(raw.get('date')),
        'user': project_person(raw.get('user')),
    }
    if truncation is not None:
        projected['text_truncation'] = truncation
    return projected


def _comment_date(raw: Mapping[str, Any]) -> int:
    """Return a sortable numeric comment date, placing malformed dates last."""
    date = _timestamp(raw.get('date'))
    if date is not None and date.isascii() and date.isdecimal():
        return int(date)
    return -1


def _comment_tagged_users(records: Sequence[object]) -> list[object]:
    """Collect raw tagged-user records from ClickUp rich comment segments."""
    tagged_users: list[object] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        segments = record.get('comment')
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if isinstance(segment, Mapping) and segment.get('type') == 'tag':
                tagged_users.append(segment.get('user'))
    return tagged_users


def _select_comments(
    records: Sequence[object], *, limit: int
) -> tuple[list[CommentProjection], int, list[Mapping[str, Any]]]:
    """Select newest comments with stable source-order ties before projecting bounded text."""
    newest: list[tuple[int, int, Mapping[str, Any]]] = []
    valid_count = 0
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or _stable_id(record.get('id')) is None:
            continue
        valid_count += 1
        candidate = (_comment_date(record), -index, record)
        if len(newest) < limit:
            heapq.heappush(newest, candidate)
        elif candidate[:2] > newest[0][:2]:
            heapq.heapreplace(newest, candidate)

    selected: list[CommentProjection] = []
    selected_records: list[Mapping[str, Any]] = []
    for _date, _index, record in sorted(newest, key=lambda item: item[:2], reverse=True):
        projected = _comment(record)
        if projected is not None:
            selected.append(projected)
            selected_records.append(record)
    return selected, valid_count, selected_records


def _provider_total(raw: Mapping[str, Any], key: str) -> int | None:
    """Return an explicit non-negative provider total, leaving unavailable totals unknown."""
    value = raw.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _bounded_collection(
    items: list[Any],
    *,
    limit: int,
    total: int | None,
    observed: int,
) -> tuple[list[Any], CollectionMetaProjection]:
    """Bound projected items without claiming an unavailable provider total."""
    bounded = list(items[:limit])
    if total is not None:
        resolved_total = max(total, observed)
        omitted_count = max(0, resolved_total - len(bounded))
    else:
        resolved_total = None
        omitted_count = max(0, observed - len(bounded))
    return bounded, {
        'included': len(bounded),
        'total': resolved_total,
        'truncated': omitted_count > 0,
        'omitted_count': omitted_count,
    }


def _advisory(raw: object) -> AdvisoryProjection | None:
    """Map an allowlisted advisory code to fixed internal text."""
    if not isinstance(raw, Mapping):
        return None
    code = _string(raw.get('code'))
    if code is None or (message := _ADVISORY_MESSAGES.get(code)) is None:
        return None
    return {'code': code, 'message': message}


def project_task_full(
    raw: Mapping[str, Any],
    *,
    comments: Mapping[str, Any] | Sequence[object] | None = None,
    comments_advisory: Mapping[str, Any] | None = None,
) -> TaskFullProjection:
    """Project a bounded full task; optional comment-fetch failure is represented by an advisory."""
    summary = project_task_summary(raw)

    description, description_truncation = truncate_text(
        _string(raw.get('text_content')) or _string(raw.get('description')) or '',
        limit=BODY_CHAR_LIMIT,
    )
    markdown_description, markdown_truncation = truncate_text(
        _string(raw.get('markdown_description')) or '',
        limit=BODY_CHAR_LIMIT,
    )

    raw_attachments = raw.get('attachments')
    attachment_records: list[object] = raw_attachments if isinstance(raw_attachments, list) else []
    attachments, valid_attachment_count = _retain_valid(
        attachment_records,
        limit=ATTACHMENT_LIMIT,
        is_valid=_is_attachment_record,
        project=_attachment,
    )
    bounded_attachments, attachments_meta = _bounded_collection(
        attachments,
        limit=ATTACHMENT_LIMIT,
        total=_provider_total(raw, 'attachments_count'),
        observed=valid_attachment_count,
    )

    raw_subtasks = raw.get('subtasks')
    subtask_records: list[object] = raw_subtasks if isinstance(raw_subtasks, list) else []
    subtasks, valid_subtask_count = _retain_valid(
        subtask_records,
        limit=CLICKUP_SUBTASK_LIMIT,
        is_valid=_is_subtask_record,
        project=_subtask,
    )
    bounded_subtasks, subtasks_meta = _bounded_collection(
        subtasks,
        limit=CLICKUP_SUBTASK_LIMIT,
        total=_provider_total(raw, 'subtasks_count'),
        observed=valid_subtask_count,
    )

    if isinstance(comments, Mapping):
        raw_comments = comments.get('comments')
        comment_records: Sequence[object] = raw_comments if isinstance(raw_comments, list) else ()
        comments_total = _provider_total(comments, 'total')
    elif isinstance(comments, Sequence) and not isinstance(comments, (str, bytes)):
        comment_records = comments
        comments_total = None
    else:
        comment_records = ()
        comments_total = None
    projected_comments, valid_comment_count, selected_comment_records = _select_comments(
        comment_records,
        limit=CLICKUP_COMMENT_LIMIT,
    )
    bounded_comments, comments_meta = _bounded_collection(
        projected_comments,
        limit=CLICKUP_COMMENT_LIMIT,
        total=comments_total,
        observed=valid_comment_count,
    )

    advisory = _advisory(comments_advisory)
    projected: TaskFullProjection = {
        'id': summary['id'],
        'custom_id': summary['custom_id'],
        'name': summary['name'],
        'status': summary['status'],
        'assignees': summary['assignees'],
        'priority': summary['priority'],
        'due_date': summary['due_date'],
        'url': summary['url'],
        'date_updated': summary['date_updated'],
        'description': description,
        'markdown_description': markdown_description,
        'location': {
            'list': _identity(raw.get('list')),
            'folder': _identity(raw.get('folder')),
            'space': _identity(raw.get('space')),
        },
        'creator': project_person(raw.get('creator')),
        'watchers': _people(raw.get('watchers')),
        'mentions': _merge_people(_comment_tagged_users(selected_comment_records)),
        'tags': _tags(raw.get('tags')),
        'start_date': _string(raw.get('start_date')),
        'time_estimate': _number(raw.get('time_estimate')),
        'points': _number(raw.get('points')),
        'custom_fields': _custom_fields(raw.get('custom_fields')),
        'parent': _stable_id(raw.get('parent')),
        'dependencies': _dependencies(raw.get('dependencies')),
        'linked_tasks': _linked_tasks(raw.get('linked_tasks')),
        'checklists': _checklists(raw.get('checklists')),
        'attachments': bounded_attachments,
        'attachments_meta': attachments_meta,
        'subtasks': bounded_subtasks,
        'subtasks_meta': subtasks_meta,
        'comments': bounded_comments,
        'comments_meta': comments_meta,
        'advisories': [advisory] if advisory is not None else [],
    }
    if description_truncation is not None:
        projected['description_truncation'] = description_truncation
    if markdown_truncation is not None:
        projected['markdown_description_truncation'] = markdown_truncation
    return projected

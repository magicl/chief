# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for bounded, allowlisted ClickUp task projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from libs.clients.clickup.projection import (
    project_lists,
    project_mutation_ack,
    project_person,
    project_spaces,
    project_task_full,
    project_task_list,
    project_task_summary,
)
from libs.clients.compact import (
    ATTACHMENT_LIMIT,
    BODY_CHAR_LIMIT,
    CLICKUP_COMMENT_CHAR_LIMIT,
    CLICKUP_COMMENT_LIMIT,
    CLICKUP_SUBTASK_LIMIT,
)

from olib.py.django.test.cases import OTestCase

_SUMMARY_KEYS = {
    'id',
    'custom_id',
    'name',
    'status',
    'assignees',
    'priority',
    'due_date',
    'url',
    'date_updated',
}
_FULL_KEYS = _SUMMARY_KEYS | {
    'description',
    'markdown_description',
    'location',
    'creator',
    'watchers',
    'mentions',
    'tags',
    'start_date',
    'time_estimate',
    'points',
    'custom_fields',
    'parent',
    'dependencies',
    'linked_tasks',
    'checklists',
    'attachments',
    'attachments_meta',
    'subtasks',
    'subtasks_meta',
    'comments',
    'comments_meta',
    'advisories',
}


def _fixture(name: str) -> dict[str, Any]:
    """Load one checked-in raw ClickUp response fixture."""
    path = Path(__file__).parent / 'fixtures' / name
    loaded = json.loads(path.read_text())
    assert isinstance(loaded, dict)
    return loaded


class _ProjectionGuard(dict[str, Any]):
    """Fail when projection reads fields beyond a record's cheap selection keys."""

    def __init__(self, values: dict[str, Any], *, allowed: set[str]) -> None:
        """Store provider values and the keys selection is allowed to inspect."""
        super().__init__(values)
        self._allowed = allowed

    def get(self, key: str, default: object = None) -> Any:
        """Allow cheap selection fields while rejecting full projection access."""
        if key not in self._allowed:
            raise AssertionError(f'unbounded projection accessed {key}')
        return super().get(key, default)


class TestClickUpPersonProjection(OTestCase):
    def test_projects_stable_identity_and_optional_email_only(self) -> None:
        """Prefer username over name and remove all profile/provider metadata."""
        self.assertEqual(
            project_person(
                {
                    'id': 17,
                    'username': 'Ada',
                    'name': 'Ignored fallback',
                    'email': 'ada@example.com',
                    'avatar': 'private-avatar',
                    'profilePicture': 'private-picture',
                    'permission': {'edit': True},
                }
            ),
            {'id': '17', 'display_name': 'Ada', 'email': 'ada@example.com'},
        )
        self.assertEqual(project_person({'id': 'grace', 'name': 'Grace'}), {'id': 'grace', 'display_name': 'Grace'})

    def test_rejects_people_without_stable_identity(self) -> None:
        """Return no person when the provider record lacks a usable id."""
        self.assertIsNone(project_person({'id': '', 'username': 'Ada'}))
        self.assertIsNone(project_person({'id': {'private': 1}, 'name': 'Grace'}))
        self.assertIsNone(project_person('not-a-person'))


class TestClickUpTaskSummaryProjection(OTestCase):
    def test_projects_exact_summary_shape(self) -> None:
        """Retain only shared summary fields and normalize nested identities."""
        projected = project_task_summary(_fixture('task_full.json'))

        self.assertEqual(set(projected), _SUMMARY_KEYS)
        self.assertEqual(
            projected,
            {
                'id': 'task-1',
                'custom_id': 'OPS-7',
                'name': 'Investigate compact records',
                'status': 'in progress',
                'assignees': [
                    {'id': '101', 'display_name': 'Ada', 'email': 'ada@example.com'},
                    {'id': '102', 'display_name': 'Grace'},
                ],
                'priority': {'id': '2', 'priority': 'high'},
                'due_date': '2000',
                'url': 'https://app.clickup.com/t/task-1',
                'date_updated': '1900',
            },
        )
        self.assertNotIn('list_id', projected)
        self.assertNotIn('text_content', projected)
        self.assertNotIn('orderindex', projected)

    def test_summary_requires_a_stable_task_id_without_leaking_provider_content(self) -> None:
        """Reject missing or malformed task identity using one fixed local failure."""
        for raw in ({}, {'id': ''}, {'id': {'secret': 'provider-content'}}):
            with self.subTest(raw=raw):
                with self.assertRaisesMessage(ValueError, 'Invalid ClickUp task id') as caught:
                    project_task_summary(raw)
                self.assertNotIn('provider-content', str(caught.exception))

    def test_malformed_optional_summary_values_become_stable_safe_values(self) -> None:
        """Normalize malformed optional values without copying raw structures."""
        projected = project_task_summary(
            {
                'id': 'task-safe',
                'custom_id': ['private'],
                'name': None,
                'status': {'status': {'private': 'status'}},
                'assignees': {'private': 'people'},
                'priority': {'id': [], 'priority': {'private': 'name'}},
                'due_date': {'private': 'date'},
                'url': ['private'],
                'date_updated': False,
            }
        )

        self.assertEqual(
            projected,
            {
                'id': 'task-safe',
                'custom_id': None,
                'name': '',
                'status': None,
                'assignees': [],
                'priority': None,
                'due_date': None,
                'url': None,
                'date_updated': None,
            },
        )
        self.assertNotIn('private', repr(projected))


class TestClickUpCollectionProjection(OTestCase):
    def test_projects_spaces_with_exact_allowlist_and_skips_malformed_entries(self) -> None:
        """Return stable space identity and lifecycle state without provider metadata."""
        projected = project_spaces(
            {
                'spaces': [
                    {'id': 's1', 'name': 'Engineering', 'archived': False, 'private': True},
                    {'id': 2, 'name': 'Operations', 'archived': True, 'features': {'private': True}},
                    {'name': 'No identity'},
                    'malformed',
                ],
                'teams': [{'private': True}],
            }
        )

        self.assertEqual(
            projected,
            {
                'spaces': [
                    {'id': 's1', 'name': 'Engineering', 'archived': False},
                    {'id': '2', 'name': 'Operations', 'archived': True},
                ]
            },
        )
        self.assertNotIn('private', repr(projected))

    def test_projects_lists_with_optional_flat_parent_ids(self) -> None:
        """Retain only list identity, lifecycle state, and supplied minimal parent references."""
        projected = project_lists(
            {
                'lists': [
                    {
                        'id': 'l1',
                        'name': 'Inbox',
                        'archived': False,
                        'space': {'id': 's1', 'name': 'Private space name'},
                        'folder': {'id': 9, 'name': 'Private folder name'},
                        'statuses': [{'private': True}],
                    },
                    {'id': 'l2', 'name': 'Loose', 'archived': True},
                    {'id': '', 'name': 'No identity'},
                    {'private': 'malformed'},
                ]
            }
        )

        self.assertEqual(
            projected,
            {
                'lists': [
                    {
                        'id': 'l1',
                        'name': 'Inbox',
                        'archived': False,
                        'space_id': 's1',
                        'folder_id': '9',
                    },
                    {'id': 'l2', 'name': 'Loose', 'archived': True},
                ]
            },
        )
        self.assertNotIn('Private', repr(projected))
        self.assertNotIn('private', repr(projected))

    def test_collection_projection_treats_malformed_containers_as_empty(self) -> None:
        """Reject malformed collection envelopes without copying or raising on provider data."""
        self.assertEqual(project_spaces({'spaces': {'private': True}}), {'spaces': []})
        self.assertEqual(project_lists({'lists': ['bad', {'id': {'private': True}}]}), {'lists': []})

    def test_projects_task_page_and_skips_tasks_without_stable_ids(self) -> None:
        """Project valid task summaries independently and preserve only a boolean page marker."""
        projected = project_task_list(
            {
                'tasks': [
                    {'id': 't1', 'name': 'One', 'status': {'status': 'open'}, 'text_content': 'private'},
                    {'name': 'Missing id', 'private': 'content'},
                    {'id': {'private': True}, 'name': 'Malformed id'},
                    {'id': 2, 'name': 'Two', 'orderindex': 'private'},
                ],
                'last_page': False,
                'private': {'cursor': 'secret'},
            }
        )

        self.assertEqual([task['id'] for task in projected['tasks']], ['t1', '2'])
        self.assertEqual(projected['tasks'][0]['status'], 'open')
        self.assertEqual(projected['last_page'], False)
        self.assertNotIn('text_content', repr(projected))
        self.assertNotIn('orderindex', repr(projected))
        self.assertNotIn('private', repr(projected))

    def test_task_page_omits_malformed_last_page(self) -> None:
        """Include last_page only when ClickUp supplies a real boolean."""
        self.assertEqual(project_task_list({'tasks': [], 'last_page': 'private'}), {'tasks': []})
        self.assertEqual(project_task_list({'tasks': {'private': True}}), {'tasks': []})


class TestClickUpMutationProjection(OTestCase):
    def test_projects_exact_ack_fields_without_provider_identity_graphs(self) -> None:
        """Retain task identity plus explicitly useful resulting task state only."""
        projected = project_mutation_ack(
            {
                'id': 't1',
                'url': 'https://app.clickup.com/t/t1',
                'name': 'Renamed',
                'status': {'status': 'done'},
                'deleted': True,
                'creator': {'id': 7, 'username': 'Private'},
                'team': {'id': 'private'},
                'workspace': {'id': 'private'},
            }
        )

        self.assertEqual(
            projected,
            {
                'ok': True,
                'task_id': 't1',
                'url': 'https://app.clickup.com/t/t1',
                'name': 'Renamed',
                'status': 'done',
                'deleted': True,
            },
        )
        self.assertNotIn('Private', repr(projected))
        self.assertNotIn('workspace', repr(projected))

    def test_uses_caller_task_identity_for_empty_delete_and_comment_ack(self) -> None:
        """Prefer the affected task id so empty deletes and comment ids acknowledge the task."""
        self.assertEqual(
            project_mutation_ack({'deleted': True}, task_id='t-delete'),
            {'ok': True, 'task_id': 't-delete', 'deleted': True},
        )
        self.assertEqual(
            project_mutation_ack({'id': 'comment-1', 'date': 'private'}, task_id='t-comment'),
            {'ok': True, 'task_id': 't-comment'},
        )

    def test_falls_back_to_raw_task_id_after_caller_and_raw_id(self) -> None:
        """Accept provider acknowledgements that identify the affected task via task_id."""
        self.assertEqual(
            project_mutation_ack({'task_id': 't-raw'}),
            {'ok': True, 'task_id': 't-raw'},
        )
        self.assertEqual(
            project_mutation_ack({'id': 't-id', 'task_id': 't-raw'}, task_id='t-caller'),
            {'ok': True, 'task_id': 't-caller'},
        )
        self.assertEqual(
            project_mutation_ack({'id': 't-id', 'task_id': 't-raw'}),
            {'ok': True, 'task_id': 't-id'},
        )

    def test_rejects_ack_without_any_stable_task_identity(self) -> None:
        """Fail locally with fixed text when neither response nor caller identifies a task."""
        for raw in (
            {'id': {'private': 'provider-content'}},
            {'task_id': ''},
            {'task_id': {'private': 'provider-content'}},
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesMessage(ValueError, 'Invalid ClickUp mutation response'):
                    project_mutation_ack(raw)


class TestClickUpTaskFullProjection(OTestCase):
    def test_full_requires_a_stable_task_id_without_leaking_provider_content(self) -> None:
        """Reject malformed full-task identity before projecting provider fields."""
        with self.assertRaisesMessage(ValueError, 'Invalid ClickUp task id') as caught:
            project_task_full({'id': {'secret': 'provider-content'}, 'description': 'provider-content'})

        self.assertNotIn('provider-content', str(caught.exception))

    def test_projects_every_full_field_group(self) -> None:
        """Project the complete fixture to bounded records with locked key sets."""
        raw = _fixture('task_full.json')
        comments = {
            'comments': [
                {
                    'id': 'comment-1',
                    'comment_text': 'Newest comment',
                    'date': '3000',
                    'user': {'id': 106, 'username': 'Mary', 'avatar': 'private-avatar'},
                    'replies': [{'private': 'nested'}],
                }
            ],
            'total': 1,
            'transport': {'cursor': 'private'},
        }

        projected = project_task_full(raw, comments=comments)

        self.assertEqual(set(projected), _FULL_KEYS)
        self.assertEqual(projected['description'], 'Task primary text')
        self.assertEqual(projected['markdown_description'], '# Task description')
        self.assertEqual(
            projected['location'],
            {
                'list': {'id': 'list-1', 'name': 'Inbox'},
                'folder': {'id': 'folder-1', 'name': 'Ops'},
                'space': {'id': 'space-1', 'name': 'Engineering'},
            },
        )
        self.assertEqual(projected['creator'], {'id': '100', 'display_name': 'Lin', 'email': 'lin@example.com'})
        self.assertEqual(projected['watchers'], [{'id': '103', 'display_name': 'Margaret'}])
        self.assertEqual(projected['mentions'], [])
        self.assertEqual(projected['tags'], [{'name': 'security'}, {'name': 'backend'}])
        self.assertEqual(projected['start_date'], '1000')
        self.assertEqual(projected['time_estimate'], 3_600_000)
        self.assertEqual(projected['points'], 3.5)
        self.assertEqual(
            projected['custom_fields'],
            [
                {'id': 'cf-1', 'name': 'Impact', 'type': 'drop_down', 'value': 'high'},
                {'id': 'cf-2', 'name': 'Labels', 'type': 'labels', 'value': ['alpha', 2, True]},
                {'id': 'cf-3', 'name': 'Unknown field', 'type': 'unknown', 'value': None},
            ],
        )
        self.assertEqual(projected['parent'], 'task-parent')
        self.assertEqual(
            projected['dependencies'],
            [
                {
                    'id': 'task-0',
                    'name': 'Foundation task',
                    'url': 'https://app.clickup.com/t/task-0',
                    'status': 'done',
                }
            ],
        )
        self.assertEqual(
            projected['linked_tasks'],
            [
                {
                    'id': 'task-2',
                    'name': 'Related task',
                    'url': 'https://app.clickup.com/t/task-2',
                    'status': 'open',
                }
            ],
        )
        self.assertEqual(
            projected['checklists'],
            [
                {
                    'id': 'check-1',
                    'name': 'Release',
                    'resolved': 1,
                    'unresolved': 1,
                    'items': [
                        {'id': 'item-1', 'name': 'Test', 'resolved': True},
                        {'id': 'item-2', 'name': 'Deploy', 'resolved': False},
                    ],
                }
            ],
        )
        self.assertEqual(
            projected['attachments'],
            [
                {
                    'filename': 'report.txt',
                    'mime_type': 'text/plain',
                    'extension': 'txt',
                    'size': 12,
                    'uploader': {'id': '105', 'display_name': 'Dorothy'},
                    'date': '1700',
                    'url': 'https://files.example/report.txt',
                    'url_w_query': 'https://files.example/report.txt?token=fetch',
                    'url_w_host': 'https://files-host.example/report.txt',
                }
            ],
        )
        self.assertEqual(
            projected['subtasks'],
            [
                {
                    'id': 'sub-1',
                    'custom_id': None,
                    'name': 'Write tests',
                    'status': 'open',
                    'assignees': [{'id': '101', 'display_name': 'Ada'}],
                    'priority': {'id': '3', 'priority': 'normal'},
                    'due_date': None,
                    'url': 'https://app.clickup.com/t/sub-1',
                }
            ],
        )
        self.assertEqual(
            projected['comments'],
            [
                {
                    'id': 'comment-1',
                    'text': 'Newest comment',
                    'date': '3000',
                    'user': {'id': '106', 'display_name': 'Mary'},
                }
            ],
        )
        self.assertEqual(
            projected['attachments_meta'],
            {'included': 1, 'total': None, 'truncated': False, 'omitted_count': 0},
        )
        self.assertEqual(
            projected['subtasks_meta'],
            {'included': 1, 'total': None, 'truncated': False, 'omitted_count': 0},
        )
        self.assertEqual(
            projected['comments_meta'],
            {'included': 1, 'total': 1, 'truncated': False, 'omitted_count': 0},
        )
        self.assertEqual(projected['advisories'], [])

    def test_provider_attachment_preserves_mimetype_and_numeric_date(self) -> None:
        """Normalize provider attachment aliases without retaining thumbnails or raw metadata."""
        provider = _fixture('task_provider_structures.json')

        projected = project_task_full(provider['task'], comments=provider['comments'])

        self.assertEqual(
            projected['attachments'],
            [
                {
                    'filename': 'incident-report.pdf',
                    'mime_type': 'application/pdf',
                    'extension': 'pdf',
                    'size': 4096,
                    'uploader': {'id': '700', 'display_name': 'Attachment Owner'},
                    'date': '1785100200123',
                    'url': 'https://attachments.example/incident-report.pdf',
                    'url_w_query': None,
                    'url_w_host': None,
                }
            ],
        )
        self.assertNotIn('thumbnail', repr(projected['attachments']))

    def test_structured_custom_fields_use_type_specific_exact_allowlists(self) -> None:
        """Preserve supported provider values while excluding configuration and arbitrary nested data."""
        provider = _fixture('task_provider_structures.json')

        projected = project_task_full(provider['task'], comments=provider['comments'])

        self.assertEqual(
            projected['custom_fields'],
            [
                {
                    'id': 'field-location',
                    'name': 'Office',
                    'type': 'location',
                    'value': {
                        'formatted_address': 'Gold Coast QLD, Australia',
                        'location': {'lat': -28.016667, 'lng': 153.4},
                    },
                },
                {
                    'id': 'field-users',
                    'name': 'Reviewers',
                    'type': 'users',
                    'value': [
                        {'id': '701', 'display_name': 'Ada Reviewer', 'email': 'ada@example.com'},
                        {'id': '702', 'display_name': 'Grace Reviewer'},
                    ],
                },
                {
                    'id': 'field-tasks',
                    'name': 'Related tasks',
                    'type': 'tasks',
                    'value': ['task-a', '42', 'task-c'],
                },
                {
                    'id': 'field-relationship',
                    'name': 'Blocking',
                    'type': 'list_relationship',
                    'value': ['task-b'],
                },
                {
                    'id': 'field-progress',
                    'name': 'Delivery progress',
                    'type': 'progress',
                    'value': {'current': 3, 'start': 0, 'end': 5, 'percent_completed': 60},
                },
                {
                    'id': 'field-automatic-progress',
                    'name': 'Automatic progress',
                    'type': 'automatic_progress',
                    'value': {'percent_completed': 75},
                },
                {
                    'id': 'field-manual-progress',
                    'name': 'Manual progress',
                    'type': 'manual_progress',
                    'value': {'current': 20, 'start': 10, 'end': 30},
                },
                {
                    'id': 'field-dropdown',
                    'name': 'Risk',
                    'type': 'drop_down',
                    'value': {'id': 'risk-high', 'name': 'High'},
                },
                {
                    'id': 'field-labels',
                    'name': 'Signals',
                    'type': 'labels',
                    'value': [
                        {'id': 'signal-security', 'name': 'Security'},
                        {'id': 'signal-customer', 'name': 'Customer'},
                    ],
                },
                {
                    'id': 'field-unknown',
                    'name': 'Future provider field',
                    'type': 'unknown',
                    'value': None,
                },
            ],
        )

        def assert_no_presentation_metadata(value: object, path: str = '$') -> None:
            """Recursively reject type configuration and option presentation keys."""
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, {'color', 'type_config'}, f'forbidden key at {path}.{key}')
                    assert_no_presentation_metadata(child, f'{path}.{key}')
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    assert_no_presentation_metadata(child, f'{path}[{index}]')

        assert_no_presentation_metadata(projected['custom_fields'])
        self.assertNotIn('must-not-leak', repr(projected['custom_fields']))

    def test_rich_comment_tags_supply_mentions_without_group_assignees(self) -> None:
        """Collect tagged users from rich segments while keeping comments compact and bounded."""
        provider = _fixture('task_provider_structures.json')

        projected = project_task_full(provider['task'], comments=provider['comments'])

        self.assertEqual(
            projected['mentions'],
            [
                {'id': '701', 'display_name': 'Ada Reviewer', 'email': 'ada@example.com'},
                {'id': '702', 'display_name': 'Grace Reviewer'},
            ],
        )
        self.assertEqual(
            projected['comments'],
            [
                {
                    'id': 'comment-rich-2',
                    'text': 'Ada, please pair with Grace.',
                    'date': '1785100400000',
                    'user': {'id': '703', 'display_name': 'Comment Author'},
                },
                {
                    'id': 'comment-rich-1',
                    'text': 'Ada owns the follow-up.',
                    'date': '1785100300000',
                    'user': {'id': '704', 'display_name': 'Second Author'},
                },
            ],
        )
        self.assertEqual(
            projected['comments_meta'],
            {'included': 2, 'total': 2, 'truncated': False, 'omitted_count': 0},
        )
        self.assertNotIn('group-platform', repr(projected['mentions']))
        self.assertNotIn("'comment':", repr(projected['comments']))
        self.assertNotIn('must-not-leak', repr(projected))

    def test_unknown_custom_field_types_discard_scalar_and_list_values(self) -> None:
        """Allow only supported ClickUp field types and null values for adversarial unknown types."""
        projected = project_task_full(
            {
                'id': 'task-1',
                'custom_fields': [
                    {'id': 'known', 'name': 'Known', 'type': 'number', 'value': 7},
                    {'id': 'scalar', 'name': 'Scalar', 'type': 'secret_provider_type', 'value': 'secret-scalar'},
                    {'id': 'list', 'name': 'List', 'type': 'future_private_type', 'value': ['secret-list']},
                ],
            }
        )

        self.assertEqual(
            projected['custom_fields'],
            [
                {'id': 'known', 'name': 'Known', 'type': 'number', 'value': 7},
                {'id': 'scalar', 'name': 'Scalar', 'type': 'unknown', 'value': None},
                {'id': 'list', 'name': 'List', 'type': 'unknown', 'value': None},
            ],
        )
        self.assertNotIn('secret-', repr(projected))

    def test_bounds_collections_and_preserves_available_provider_totals(self) -> None:
        """Limit large provider collections and report all omitted records."""
        raw = {
            'id': 'task-1',
            'attachments': [{'title': f'a-{index}'} for index in range(ATTACHMENT_LIMIT + 5)],
            'attachments_count': 40,
            'subtasks': [
                {'id': f's-{index}', 'name': f'Subtask {index}'} for index in range(CLICKUP_SUBTASK_LIMIT + 5)
            ],
            'subtasks_count': 50,
        }
        comments = {
            'comments': [
                {'id': f'c-{index}', 'comment_text': f'comment {index}', 'date': str(index)}
                for index in range(CLICKUP_COMMENT_LIMIT + 5)
            ],
            'total': 80,
        }

        projected = project_task_full(raw, comments=comments)

        self.assertEqual(len(projected['attachments']), ATTACHMENT_LIMIT)
        self.assertEqual(
            projected['attachments_meta'],
            {'included': ATTACHMENT_LIMIT, 'total': 40, 'truncated': True, 'omitted_count': 15},
        )
        self.assertEqual(len(projected['subtasks']), CLICKUP_SUBTASK_LIMIT)
        self.assertEqual(
            projected['subtasks_meta'],
            {'included': CLICKUP_SUBTASK_LIMIT, 'total': 50, 'truncated': True, 'omitted_count': 25},
        )
        self.assertEqual([item['id'] for item in projected['comments']], [f'c-{index}' for index in range(14, 4, -1)])
        self.assertEqual(
            projected['comments_meta'],
            {'included': CLICKUP_COMMENT_LIMIT, 'total': 80, 'truncated': True, 'omitted_count': 70},
        )

    def test_truncates_descriptions_and_individual_comment_text_explicitly(self) -> None:
        """Bound every large text field and identify each omitted suffix."""
        raw = {
            'id': 'task-1',
            'text_content': 'd' * (BODY_CHAR_LIMIT + 7),
            'description': 'must not replace primary text',
            'markdown_description': 'm' * (BODY_CHAR_LIMIT + 9),
        }
        comments = {
            'comments': [
                {
                    'id': 'comment-1',
                    'comment_text': 'c' * (CLICKUP_COMMENT_CHAR_LIMIT + 11),
                    'date': '5',
                }
            ]
        }

        projected = project_task_full(raw, comments=comments)

        self.assertEqual(len(projected['description']), BODY_CHAR_LIMIT)
        self.assertEqual(projected['description_truncation'], {'truncated': True, 'omitted_chars': 7})
        self.assertEqual(len(projected['markdown_description']), BODY_CHAR_LIMIT)
        self.assertEqual(projected['markdown_description_truncation'], {'truncated': True, 'omitted_chars': 9})
        self.assertEqual(len(projected['comments'][0]['text']), CLICKUP_COMMENT_CHAR_LIMIT)
        self.assertEqual(
            projected['comments'][0]['text_truncation'],
            {'truncated': True, 'omitted_chars': 11},
        )

    def test_description_prefers_text_content_and_falls_back_to_description(self) -> None:
        """Retain ClickUp's primary plain text while supporting responses that only include description."""
        preferred = project_task_full({'id': 'task-1', 'text_content': 'Primary', 'description': 'Fallback'})
        fallback = project_task_full({'id': 'task-1', 'text_content': {'private': 'bad'}, 'description': 'Fallback'})

        self.assertEqual(preferred['description'], 'Primary')
        self.assertEqual(fallback['description'], 'Fallback')
        self.assertNotIn('private', repr(fallback))

    def test_unknown_collection_totals_do_not_claim_complete_provider_pages(self) -> None:
        """Use null totals while reporting only locally observed omissions."""
        one_each = project_task_full(
            {
                'id': 'task-1',
                'attachments': [{'title': 'one'}],
                'subtasks': [{'id': 'sub-1', 'name': 'One'}],
            },
            comments=[{'id': 'comment-1', 'comment_text': 'One'}],
        )
        locally_bounded = project_task_full(
            {'id': 'task-1', 'attachments': [{'title': str(index)} for index in range(ATTACHMENT_LIMIT + 2)]}
        )

        for meta_key in ('attachments_meta', 'subtasks_meta', 'comments_meta'):
            self.assertEqual(
                one_each[meta_key],
                {'included': 1, 'total': None, 'truncated': False, 'omitted_count': 0},
            )
        self.assertEqual(
            locally_bounded['attachments_meta'],
            {'included': ATTACHMENT_LIMIT, 'total': None, 'truncated': True, 'omitted_count': 2},
        )

    def test_inconsistent_provider_total_is_normalized_to_observed_collection_size(self) -> None:
        """Keep metadata coherent when a stale provider count is smaller than the observed page."""
        projected = project_task_full(
            {
                'id': 'task-1',
                'attachments': [{'title': str(index)} for index in range(ATTACHMENT_LIMIT + 5)],
                'attachments_count': 3,
            }
        )

        self.assertEqual(
            projected['attachments_meta'],
            {
                'included': ATTACHMENT_LIMIT,
                'total': ATTACHMENT_LIMIT + 5,
                'truncated': True,
                'omitted_count': 5,
            },
        )

    def test_comments_failure_keeps_task_and_adds_explicit_advisory(self) -> None:
        """Represent an optional comment-fetch failure without failing task projection."""
        projected = project_task_full(
            {'id': 'task-1', 'name': 'Still available'},
            comments=None,
            comments_advisory={'code': 'comments_unavailable', 'message': 'secret provider failure'},
        )

        self.assertEqual(projected['id'], 'task-1')
        self.assertEqual(projected['comments'], [])
        self.assertEqual(
            projected['comments_meta'],
            {'included': 0, 'total': None, 'truncated': False, 'omitted_count': 0},
        )
        self.assertEqual(
            projected['advisories'],
            [{'code': 'comments_unavailable', 'message': 'Comments could not be loaded.'}],
        )
        self.assertNotIn('secret provider failure', repr(projected))

    def test_unknown_advisory_code_is_ignored(self) -> None:
        """Do not copy unrecognized advisory codes or provider-controlled messages."""
        projected = project_task_full(
            {'id': 'task-1'},
            comments_advisory={'code': 'provider_secret', 'message': 'secret provider failure'},
        )

        self.assertEqual(projected['advisories'], [])
        self.assertNotIn('secret', repr(projected))

    def test_large_collections_project_only_retained_valid_records(self) -> None:
        """Select bounded valid records before expensive projection while preserving counts and tie order."""
        attachments: list[dict[str, Any]] = [{'title': f'kept-{index}'} for index in range(ATTACHMENT_LIMIT)]
        attachments.extend(
            _ProjectionGuard(
                {'title': f'omitted-{index}'}, allowed={'title', 'filename', 'url', 'url_w_query', 'url_w_host'}
            )
            for index in range(75)
        )
        subtasks: list[dict[str, Any]] = [
            {'id': f'kept-{index}', 'name': f'Kept {index}'} for index in range(CLICKUP_SUBTASK_LIMIT)
        ]
        subtasks.extend(_ProjectionGuard({'id': f'omitted-{index}'}, allowed={'id'}) for index in range(75))
        comments: list[dict[str, Any]] = [
            _ProjectionGuard({'id': f'old-{index}', 'date': str(index)}, allowed={'id', 'date'}) for index in range(88)
        ]
        comments.extend(
            {'id': f'new-{index}', 'date': '1000', 'comment_text': f'newest {index}'} for index in range(12)
        )

        projected = project_task_full(
            {'id': 'task-1', 'attachments': attachments, 'subtasks': subtasks},
            comments=comments,
        )

        self.assertEqual([item['filename'] for item in projected['attachments']], [f'kept-{i}' for i in range(25)])
        self.assertEqual([item['id'] for item in projected['subtasks']], [f'kept-{i}' for i in range(25)])
        self.assertEqual([item['id'] for item in projected['comments']], [f'new-{i}' for i in range(10)])
        self.assertEqual(
            projected['attachments_meta'],
            {'included': 25, 'total': None, 'truncated': True, 'omitted_count': 75},
        )
        self.assertEqual(
            projected['subtasks_meta'],
            {'included': 25, 'total': None, 'truncated': True, 'omitted_count': 75},
        )
        self.assertEqual(
            projected['comments_meta'],
            {'included': 10, 'total': None, 'truncated': True, 'omitted_count': 90},
        )

    def test_malformed_full_fields_are_safe_and_stable(self) -> None:
        """Collapse malformed full-only fields to null or empty locked shapes."""
        private = {'private': {'transport': 'secret'}}
        projected = project_task_full(
            {
                'id': 'task-1',
                'description': private,
                'markdown_description': ['private'],
                'list': private,
                'creator': private,
                'watchers': private,
                'mentions': private,
                'tags': [private, {'name': private}],
                'start_date': private,
                'time_estimate': False,
                'points': float('nan'),
                'custom_fields': [
                    {'id': 'cf', 'name': 'Bad', 'type': private, 'type_config': private, 'value': private}
                ],
                'parent': private,
                'dependencies': private,
                'linked_tasks': [private],
                'checklists': [{'id': private, 'name': private, 'items': private}],
                'attachments': [private],
                'subtasks': [private],
            },
            comments={'comments': [private], 'total': 'private'},
            comments_advisory=private,
        )

        self.assertEqual(projected['description'], '')
        self.assertEqual(projected['markdown_description'], '')
        self.assertEqual(projected['location'], {'list': None, 'folder': None, 'space': None})
        self.assertIsNone(projected['creator'])
        self.assertEqual(projected['watchers'], [])
        self.assertEqual(projected['mentions'], [])
        self.assertEqual(projected['tags'], [])
        self.assertIsNone(projected['start_date'])
        self.assertIsNone(projected['time_estimate'])
        self.assertIsNone(projected['points'])
        self.assertEqual(
            projected['custom_fields'],
            [{'id': 'cf', 'name': 'Bad', 'type': 'unknown', 'value': None}],
        )
        self.assertIsNone(projected['parent'])
        self.assertEqual(projected['dependencies'], [])
        self.assertEqual(projected['linked_tasks'], [])
        self.assertEqual(projected['checklists'], [])
        self.assertEqual(projected['attachments'], [])
        self.assertEqual(projected['subtasks'], [])
        self.assertEqual(projected['comments'], [])
        self.assertEqual(projected['advisories'], [])
        self.assertNotIn('private', repr(projected))

    def test_recursive_allowlist_blocks_adversarial_nested_noise(self) -> None:
        """Forbid provider presentation, hierarchy, permission, and transport data at every output path."""
        projected = project_task_full(_fixture('task_full.json'), comments={'comments': []})
        forbidden = {
            'color',
            'orderindex',
            'ordering',
            'avatar',
            'profilePicture',
            'permission',
            'type_config',
            'features',
            'settings',
            'transport',
            'text_content',
            'list_id',
        }

        def assert_allowlisted(value: object, path: str = '$') -> None:
            """Walk projected containers and reject forbidden keys with their exact path."""
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key, forbidden, f'forbidden key at {path}.{key}')
                    assert_allowlisted(child, f'{path}.{key}')
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    assert_allowlisted(child, f'{path}[{index}]')

        assert_allowlisted(projected)
        self.assertNotIn('private-', repr(projected))

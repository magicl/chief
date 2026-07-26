# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Cross-path contracts for compact Gmail and ClickUp projections."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from libs.agent_spec import AgentConfigSpec, LLMSpec, ToolInstance
from libs.clients.clickup import ClickUpClient
from libs.clients.clickup.errors import ClickUpAPIError
from libs.clients.gmail import GmailClient
from libs.sources.base import PutItemResult
from libs.sources.registry import get_adapter
from libs.tools.context import ToolContext
from libs.tools.tools.clickup import ClickUpTool
from libs.tools.tools.gmail import GmailTool

from olib.py.django.test.cases import OTestCase

_BODY_LIMIT = 32_000
_ATTACHMENT_LIMIT = 25
_COMMENT_LIMIT = 10
_COMMENT_TEXT_LIMIT = 4_000
_SUBTASK_LIMIT = 25

_PERSON_SHAPE = {'id': None, 'display_name': None, 'email': None}
_PERSON_WITHOUT_EMAIL_SHAPE = {'id': None, 'display_name': None}
_PRIORITY_SHAPE = {'id': None, 'priority': None}
_COLLECTION_META_SHAPE = {'included': None, 'total': None, 'truncated': None, 'omitted_count': None}
_GMAIL_ATTACHMENT_SHAPE = {'attachment_id': None, 'filename': None, 'mime_type': None, 'size': None}
_GMAIL_AUTH_SHAPE = {
    'spf': {'verdict': None, 'domain': None},
    'dkim': [{'verdict': None, 'domain': None}],
    'dmarc': {'verdict': None, 'policy': None, 'header_from': None},
    'arc': {'verdict': None},
    'alignment': {
        'from_domain': None,
        'reply_to_domain': None,
        'return_path_domain': None,
        'from_matches_reply_to': None,
        'from_matches_return_path': None,
    },
}
_GMAIL_SUMMARY_SHAPE = {
    'id': None,
    'thread_id': None,
    'label_ids': None,
    'from': None,
    'to': None,
    'cc': None,
    'reply_to': None,
    'return_path': None,
    'subject': None,
    'message_id': None,
    'date': None,
    'received_at': None,
    'snippet': None,
    'has_attachments': None,
    'attachments': [_GMAIL_ATTACHMENT_SHAPE],
    'attachments_meta': {
        'truncated': None,
        'included': None,
        'total': None,
        'omitted_count': None,
    },
    'authentication': _GMAIL_AUTH_SHAPE,
    'advisories': [{'code': None, 'message': None}],
}
_CLICKUP_SUMMARY_SHAPE = {
    'id': None,
    'custom_id': None,
    'name': None,
    'status': None,
    'assignees': [_PERSON_SHAPE],
    'priority': _PRIORITY_SHAPE,
    'due_date': None,
    'url': None,
    'date_updated': None,
}
_CLICKUP_ATTACHMENT_SHAPE = {
    'filename': None,
    'mime_type': None,
    'extension': None,
    'size': None,
    'uploader': _PERSON_WITHOUT_EMAIL_SHAPE,
    'date': None,
    'url': None,
    'url_w_query': None,
    'url_w_host': None,
}
_CLICKUP_SUBTASK_SHAPE = {
    'id': None,
    'custom_id': None,
    'name': None,
    'status': None,
    'assignees': [_PERSON_WITHOUT_EMAIL_SHAPE],
    'priority': _PRIORITY_SHAPE,
    'due_date': None,
    'url': None,
}
_CLICKUP_FULL_SHAPE = {
    **_CLICKUP_SUMMARY_SHAPE,
    'description': None,
    'description_truncation': {'truncated': None, 'omitted_chars': None},
    'markdown_description': None,
    'markdown_description_truncation': {'truncated': None, 'omitted_chars': None},
    'location': {
        'list': {'id': None, 'name': None},
        'folder': {'id': None, 'name': None},
        'space': {'id': None, 'name': None},
    },
    'creator': _PERSON_SHAPE,
    'watchers': [_PERSON_SHAPE],
    'mentions': [_PERSON_WITHOUT_EMAIL_SHAPE],
    'tags': [{'name': None}],
    'start_date': None,
    'time_estimate': None,
    'points': None,
    'custom_fields': [{'id': None, 'name': None, 'type': None, 'value': None}],
    'parent': None,
    'dependencies': [{'id': None, 'name': None, 'url': None, 'status': None}],
    'linked_tasks': [{'id': None, 'name': None, 'url': None, 'status': None}],
    'checklists': [
        {
            'id': None,
            'name': None,
            'resolved': None,
            'unresolved': None,
            'items': [{'id': None, 'name': None, 'resolved': None}],
        }
    ],
    'attachments': [_CLICKUP_ATTACHMENT_SHAPE],
    'attachments_meta': _COLLECTION_META_SHAPE,
    'subtasks': [_CLICKUP_SUBTASK_SHAPE],
    'subtasks_meta': _COLLECTION_META_SHAPE,
    'comments': [
        {
            'id': None,
            'text': None,
            'date': None,
            'user': _PERSON_WITHOUT_EMAIL_SHAPE,
            'text_truncation': {'truncated': None, 'omitted_chars': None},
        }
    ],
    'comments_meta': _COLLECTION_META_SHAPE,
    'advisories': [{'code': None, 'message': None}],
}


def _canary(branch: str, kind: str = 'RAW') -> str:
    """Return a unique provider-only sentinel for one fixture branch."""
    return f'CANARY_{kind}_{branch}'


def _noise(branch: str) -> dict[str, Any]:
    """Add uniquely identifiable provider fields that projections must discard."""
    return {
        'provider_canary': _canary(branch),
        'permission': {'provider_canary': _canary(branch, 'PERMISSION')},
        'profile': {'provider_canary': _canary(branch, 'PROFILE')},
        'feature': {'provider_canary': _canary(branch, 'FEATURE')},
        'settings': {'provider_canary': _canary(branch, 'SETTINGS')},
        'type_config': {'provider_canary': _canary(branch, 'TYPE_CONFIG')},
    }


def _assert_no_canary(test: OTestCase, value: object, *, path: str = '$') -> None:
    """Recursively reject provider-only canaries and presentation metadata."""
    if isinstance(value, Mapping):
        test.assertNotIn('provider_canary', value, path)
        test.assertNotIn('type_config', value, path)
        test.assertNotIn('color', value, path)
        for key, child in value.items():
            _assert_no_canary(test, child, path=f'{path}.{key}')
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_canary(test, child, path=f'{path}[{index}]')
    elif isinstance(value, str):
        test.assertFalse(value.startswith('CANARY_'), f'{path}: leaked {value}')


def _assert_shape(test: OTestCase, value: object, shape: object, *, path: str = '$') -> None:
    """Recursively enforce exact keys for every retained projection branch."""
    if isinstance(shape, Mapping):
        test.assertIsInstance(value, Mapping, path)
        actual = cast(Mapping[str, Any], value)
        test.assertEqual(set(actual), set(shape), path)
        for key, child_shape in shape.items():
            _assert_shape(test, actual[key], child_shape, path=f'{path}.{key}')
    elif isinstance(shape, list):
        test.assertIsInstance(value, list, path)
        children = cast(list[Any], value)
        test.assertEqual(len(shape), 1, f'{path}: list shape must define one item')
        for index, child in enumerate(children):
            _assert_shape(test, child, shape[0], path=f'{path}[{index}]')


def _gmail_attachment(index: int) -> dict[str, Any]:
    """Build one noisy provider MIME attachment part."""
    return {
        'partId': f'attachment-part-{index}',
        'mimeType': 'application/pdf',
        'filename': f'report-{index}.pdf',
        'headers': [
            {
                'name': 'Content-Disposition',
                'value': 'attachment',
                **_noise(f'gmail-attachment-header-{index}'),
            }
        ],
        'body': {
            'attachmentId': f'attachment-{index}',
            'size': 1000 + index,
            'data': _canary(f'gmail-attachment-wire-{index}'),
            **_noise(f'gmail-attachment-body-{index}'),
        },
        **_noise(f'gmail-attachment-{index}'),
    }


def _gmail_message(*, attachment_count: int = 26, body_length: int = _BODY_LIMIT + 1) -> dict[str, Any]:
    """Build one provider Gmail message with bounded content and nested adversarial noise."""
    body = 'G' * body_length
    return {
        'id': 'gmail-message-1',
        'threadId': 'gmail-thread-1',
        'historyId': _canary('gmail-history'),
        'internalDate': '1785081600000',
        'labelIds': ['INBOX', 'IMPORTANT'],
        'snippet': 'Compact preview',
        'sizeEstimate': 999_999,
        'raw': _canary('gmail-raw'),
        'payload': {
            'partId': '',
            'mimeType': 'multipart/mixed',
            'filename': '',
            'headers': [
                {'name': 'From', 'value': 'Alice <alice@example.com>', **_noise('gmail-header-from')},
                {'name': 'To', 'value': 'Bob <bob@example.com>', **_noise('gmail-header-to')},
                {'name': 'Cc', 'value': 'Carol <carol@example.com>', **_noise('gmail-header-cc')},
                {'name': 'Reply-To', 'value': 'reply@example.com', **_noise('gmail-header-reply')},
                {'name': 'Return-Path', 'value': '<bounce@example.com>', **_noise('gmail-header-return')},
                {'name': 'Subject', 'value': 'Projection contract', **_noise('gmail-header-subject')},
                {'name': 'Message-ID', 'value': '<message@example.com>', **_noise('gmail-header-id')},
                {'name': 'Date', 'value': 'Sun, 26 Jul 2026 20:00:00 +0000', **_noise('gmail-header-date')},
                {
                    'name': 'Authentication-Results',
                    'value': (
                        'mx.google.com; spf=pass smtp.mailfrom=example.com; '
                        'dkim=pass header.d=example.com; dmarc=pass header.from=example.com p=reject'
                    ),
                    **_noise('gmail-header-auth'),
                },
                {'name': 'X-Provider-Auth-Raw', 'value': _canary('gmail-auth-raw')},
            ],
            'body': {'size': 0, 'data': _canary('gmail-container-data'), **_noise('gmail-container-body')},
            'parts': [
                {
                    'partId': 'text-part',
                    'mimeType': 'text/plain',
                    'headers': [
                        {
                            'name': 'Content-Type',
                            'value': 'text/plain; charset=utf-8',
                            **_noise('gmail-text-header'),
                        }
                    ],
                    'body': {
                        'size': len(body),
                        'data': base64.urlsafe_b64encode(body.encode()).decode().rstrip('='),
                        **_noise('gmail-text-body'),
                    },
                    **_noise('gmail-text-part'),
                },
                *[_gmail_attachment(index) for index in range(attachment_count)],
                _canary('gmail-malformed-part'),
            ],
            **_noise('gmail-payload'),
        },
        **_noise('gmail-top'),
    }


def _clickup_person(index: int, branch: str, *, email: bool = True) -> dict[str, Any]:
    """Build one noisy provider person with stable independent identity values."""
    person = {
        'id': 100 + index,
        'username': f'Person {index}',
        **_noise(f'{branch}-person-{index}'),
    }
    if email:
        person['email'] = f'person{index}@example.com'
    return person


def _clickup_attachment(index: int) -> dict[str, Any]:
    """Build one noisy ClickUp attachment with a populated uploader branch."""
    return {
        'title': f'evidence-{index}.txt',
        'type': 'text/plain',
        'extension': 'txt',
        'size': 100 + index,
        'user': _clickup_person(index, 'attachment-uploader', email=False),
        'date': str(1785000000000 + index),
        'url': f'https://files.example/evidence-{index}.txt',
        'url_w_query': f'https://files.example/evidence-{index}.txt?download=1',
        'url_w_host': f'https://files.example/evidence-{index}.txt',
        **_noise(f'clickup-attachment-{index}'),
    }


def _clickup_subtask(index: int) -> dict[str, Any]:
    """Build one noisy ClickUp subtask with populated assignee and priority branches."""
    return {
        'id': f'subtask-{index}',
        'custom_id': f'SUB-{index}',
        'name': f'Child {index}',
        'status': {'status': 'open', **_noise(f'subtask-status-{index}')},
        'assignees': [_clickup_person(index, 'subtask-assignee', email=False)],
        'priority': {'id': '3', 'priority': 'normal', **_noise(f'subtask-priority-{index}')},
        'due_date': str(1786000000000 + index),
        'url': f'https://app.clickup.com/t/subtask-{index}',
        'date_updated': _canary(f'subtask-updated-{index}'),
        'text_content': _canary(f'subtask-body-{index}'),
        **_noise(f'clickup-subtask-{index}'),
    }


def _clickup_comment(index: int) -> dict[str, Any]:
    """Build one noisy overlong ClickUp comment with a populated user branch."""
    comment = {
        'id': f'comment-{index}',
        'comment_text': f'{index:02d}:' + ('C' * (_COMMENT_TEXT_LIMIT + 2)),
        'date': str(index),
        'user': _clickup_person(index, 'comment-user', email=False),
        **_noise(f'clickup-comment-{index}'),
    }
    if index == 10:
        comment['comment'] = [
            {'text': 'Tagged '},
            {'type': 'tag', 'user': _clickup_person(4, 'mention', email=False)},
        ]
    return comment


def _clickup_task(
    *,
    description_length: int = _BODY_LIMIT + 1,
    attachment_count: int = 26,
    attachment_total: int | None = 30,
    subtask_count: int = 26,
    subtask_total: int | None = 30,
) -> dict[str, Any]:
    """Build one full provider task with every retained branch populated and noisy."""
    task: dict[str, Any] = {
        'id': 'clickup-task-1',
        'custom_id': 'OPS-42',
        'name': 'Investigate projection',
        'status': {'status': 'in progress', **_noise('clickup-status')},
        'assignees': [_clickup_person(1, 'summary-assignee')],
        'priority': {'id': '2', 'priority': 'high', **_noise('clickup-priority')},
        'due_date': '1785200000000',
        'url': 'https://app.clickup.com/t/clickup-task-1',
        'date_updated': '1785081600000',
        'text_content': 'D' * description_length,
        'description': _canary('clickup-duplicate-description'),
        'markdown_description': 'M' * description_length,
        'orderindex': _canary('clickup-order'),
        'permission_level': _canary('clickup-permission-level'),
        'list': {'id': 'list-1', 'name': 'Inbox', **_noise('clickup-list')},
        'folder': {'id': 'folder-1', 'name': 'Operations', **_noise('clickup-folder')},
        'space': {'id': 'space-1', 'name': 'Engineering', **_noise('clickup-space')},
        'creator': _clickup_person(2, 'creator'),
        'watchers': [_clickup_person(3, 'watcher')],
        'mentions': [_clickup_person(4, 'mention', email=False)],
        'group_assignees': [],
        'tags': [{'name': 'security', **_noise('clickup-tag')}],
        'start_date': '1785000000000',
        'time_estimate': 3_600_000,
        'points': 3,
        'custom_fields': [
            {
                'id': 'field-1',
                'name': 'Risk',
                'type': 'drop_down',
                **_noise('clickup-custom-field'),
                'type_config': {
                    'options': [
                        {
                            'id': 'risk-high',
                            'name': 'High',
                            'color': '#d33d44',
                            **_noise('clickup-custom-option'),
                        }
                    ],
                    'sorting': 'manual',
                },
                'value': 'risk-high',
            }
        ],
        'parent': 'parent-1',
        'dependencies': [
            {
                'depends_on': 'dependency-1',
                'name': 'Dependency',
                'url': 'https://app.clickup.com/t/dependency-1',
                'status': {'status': 'open', **_noise('clickup-dependency-status')},
                **_noise('clickup-dependency'),
            }
        ],
        'linked_tasks': [
            {
                'task_id': 'linked-1',
                'name': 'Linked',
                'url': 'https://app.clickup.com/t/linked-1',
                'status': {'status': 'done', **_noise('clickup-linked-status')},
                **_noise('clickup-linked'),
            }
        ],
        'checklists': [
            {
                'id': 'checklist-1',
                'name': 'Review',
                'items': [{'id': 'item-1', 'name': 'Inspect output', 'resolved': True, **_noise('clickup-check-item')}],
                **_noise('clickup-checklist'),
            }
        ],
        'attachments': [_clickup_attachment(index) for index in range(attachment_count)],
        'subtasks': [_clickup_subtask(index) for index in range(subtask_count)],
        **_noise('clickup-top'),
    }
    if attachment_total is not None:
        task['attachments_count'] = attachment_total
    if subtask_total is not None:
        task['subtasks_count'] = subtask_total
    return task


class _GmailContractClient:
    """Strict provider fake for Gmail source, read, attachment, and mutation paths."""

    def __init__(
        self,
        *,
        message: dict[str, Any] | None = None,
        expected_config: dict[str, Any],
        token_supplier: Callable[[], str | None],
    ) -> None:
        """Validate client construction and retain expected provider fixtures."""
        assert expected_config in ({'subject': 'me@example.com'}, {'query': 'in:inbox', 'max_results': 7})
        assert token_supplier() == '{"service_account": true}'
        self.message = message or _gmail_message()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def poll_message_metadata(
        self,
        *,
        query: str,
        max_results: int,
        skip_message_ids: frozenset[str] = frozenset(),
    ) -> Iterator[tuple[list[str], Iterator[tuple[str, dict[str, Any]]]]]:
        """Require the exact source query contract and yield one metadata record."""
        assert query == 'in:inbox'
        assert max_results == 7
        assert skip_message_ids == frozenset()
        self.calls.append(
            (
                'poll_message_metadata',
                {'query': query, 'max_results': max_results, 'skip_message_ids': skip_message_ids},
            )
        )
        yield ['gmail-message-1'], iter([('gmail-message-1', self.message)])

    def get_message(self, message_id: str, *, fmt: str = 'metadata') -> dict[str, Any]:
        """Require the exact full-message tool fetch."""
        assert message_id == 'gmail-message-1'
        assert fmt == 'full'
        self.calls.append(('get_message', {'message_id': message_id, 'fmt': fmt}))
        return self.message

    def get_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Require stable attachment identities and return decoded provider bytes."""
        assert message_id == 'gmail-message-1'
        assert attachment_id == 'attachment-0'
        self.calls.append(('get_attachment', {'message_id': message_id, 'attachment_id': attachment_id}))
        return {
            'attachment_id': attachment_id,
            'mime_type': 'application/pdf',
            'data': b'attachment bytes',
            **_noise('gmail-tool-attachment'),
        }

    def modify_labels(
        self,
        message_id: str,
        *,
        add: tuple[str, ...] = (),
        remove: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Require exact mutation arguments and return noisy provider acknowledgement."""
        assert message_id == 'gmail-message-1'
        assert add == ('STARRED',)
        assert remove == ('IMPORTANT',)
        self.calls.append(('modify_labels', {'message_id': message_id, 'add': add, 'remove': remove}))
        return {'id': message_id, 'labelIds': ['STARRED'], **_noise('gmail-mutation')}


class _ClickUpContractClient:
    """Strict provider fake for ClickUp source, read, comment, and mutation paths."""

    def __init__(
        self,
        *,
        task: dict[str, Any] | None = None,
        comments: dict[str, Any] | None = None,
        fail_comments: bool = False,
        expected_config: dict[str, Any],
        token_supplier: Callable[[], str | None],
    ) -> None:
        """Validate client construction and retain expected provider fixtures."""
        assert expected_config in (
            {'team_id': 'team-1'},
            {
                'list_id': 'list-1',
                'max_results': 3,
                'statuses': ['in progress'],
                'include_closed': False,
            },
        )
        assert token_supplier() == 'pk_test'
        self.task = task or _clickup_task()
        self.comments = comments or {
            'comments': [_clickup_comment(index) for index in range(11)],
            'total': 15,
            **_noise('clickup-comments-envelope'),
        }
        self.fail_comments = fail_comments
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tasks_up_to(
        self,
        *,
        list_id: str,
        max_results: int,
        statuses: tuple[str, ...],
        include_closed: bool,
    ) -> list[dict[str, Any]]:
        """Require exact source listing arguments."""
        assert list_id == 'list-1'
        assert max_results == 3
        assert statuses == ('in progress',)
        assert include_closed is False
        self.calls.append(
            (
                'list_tasks_up_to',
                {
                    'list_id': list_id,
                    'max_results': max_results,
                    'statuses': statuses,
                    'include_closed': include_closed,
                },
            )
        )
        return [self.task]

    def list_tasks(self, *, list_id: str, statuses: tuple[str, ...] = ()) -> dict[str, Any]:
        """Require exact tool listing arguments."""
        assert list_id == 'list-1'
        assert statuses == ('in progress',)
        self.calls.append(('list_tasks', {'list_id': list_id, 'statuses': statuses}))
        return {'tasks': [self.task], 'last_page': True, **_noise('clickup-task-page')}

    def get_task(
        self,
        task_id: str,
        *,
        include_subtasks: bool = False,
        include_markdown_description: bool = False,
    ) -> dict[str, Any]:
        """Require exact expanded-task arguments."""
        assert task_id == 'clickup-task-1'
        assert include_subtasks is True
        assert include_markdown_description is True
        self.calls.append(
            (
                'get_task',
                {
                    'task_id': task_id,
                    'include_subtasks': include_subtasks,
                    'include_markdown_description': include_markdown_description,
                },
            )
        )
        return self.task

    def list_comments(self, task_id: str) -> dict[str, Any]:
        """Require the matching task identity and return comments or a typed failure."""
        assert task_id == 'clickup-task-1'
        self.calls.append(('list_comments', {'task_id': task_id}))
        if self.fail_comments:
            raise ClickUpAPIError(_canary('clickup-comment-failure'), status=503)
        return self.comments

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Require exact mutation fields and return a noisy provider acknowledgement."""
        assert task_id == 'clickup-task-1'
        assert fields == {'name': 'Renamed', 'status': 'done'}
        self.calls.append(('update_task', {'task_id': task_id, **fields}))
        return {
            'id': 'provider-returned-id',
            'name': fields['name'],
            'status': {'status': fields['status'], **_noise('clickup-mutation-status')},
            'url': f'https://app.clickup.com/t/{task_id}',
            **_noise('clickup-mutation'),
        }


class TestGmailClickUpProjectionContract(OTestCase):
    """Lock cross-path projection semantics, bounds, and recursive allowlists."""

    def _gmail_invoke(
        self,
        *,
        message: dict[str, Any] | None = None,
    ) -> tuple[Callable[[str, dict[str, Any]], Any], _GmailContractClient]:
        """Bind the real Gmail tool to a strict provider fake."""
        retained: list[_GmailContractClient] = []

        def factory(*, token_supplier: Callable[[], str | None], config: dict[str, Any]) -> _GmailContractClient:
            """Validate tool client construction and retain the strict fake."""
            fake = _GmailContractClient(
                message=message,
                expected_config=config,
                token_supplier=token_supplier,
            )
            retained.append(fake)
            return fake

        context = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: '{"service_account": true}',
            client_factories={'gmail': cast(Callable[..., GmailClient], factory)},
        )
        invoke = GmailTool().bind(
            context,
            ToolInstance(id='gmail', type='gmail', config={'subject': 'me@example.com'}),
        )
        return invoke, retained[0]

    def _clickup_invoke(
        self,
        *,
        task: dict[str, Any] | None = None,
        comments: dict[str, Any] | None = None,
        fail_comments: bool = False,
    ) -> tuple[Callable[[str, dict[str, Any]], Any], _ClickUpContractClient]:
        """Bind the real ClickUp tool to a strict provider fake."""
        retained: list[_ClickUpContractClient] = []

        def factory(*, token_supplier: Callable[[], str | None], config: dict[str, Any]) -> _ClickUpContractClient:
            """Validate tool client construction and retain the strict fake."""
            fake = _ClickUpContractClient(
                task=task,
                comments=comments,
                fail_comments=fail_comments,
                expected_config=config,
                token_supplier=token_supplier,
            )
            retained.append(fake)
            return fake

        context = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: 'pk_test',
            client_factories={'clickup': cast(Callable[..., ClickUpClient], factory)},
        )
        invoke = ClickUpTool().bind(
            context,
            ToolInstance(id='clickup', type='clickup', config={'team_id': 'team-1'}),
        )
        return invoke, retained[0]

    def _gmail_source_data(self) -> tuple[dict[str, Any], _GmailContractClient]:
        """Poll the real Gmail source adapter and return data plus its strict fake."""
        adapter = get_adapter('gmail')
        if adapter is None:
            raise RuntimeError('gmail adapter not registered')
        payloads: list[dict[str, Any]] = []
        retained: list[_GmailContractClient] = []

        def factory(*, token_supplier: Callable[[], str | None], config: dict[str, Any]) -> _GmailContractClient:
            """Validate source client construction and retain the strict fake."""
            fake = _GmailContractClient(expected_config=config, token_supplier=token_supplier)
            retained.append(fake)
            return fake

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Capture the exact source envelope."""
            self.assertEqual(external_id, 'gmail-message-1')
            payloads.append(payload)
            return PutItemResult(item_id=uuid4(), created=True)

        with patch('libs.sources.adapters.gmail.GmailClient', factory):
            adapter.poll(
                config={'query': 'in:inbox', 'max_results': 7},
                put_item=put_item,
                credential_supplier=lambda: '{"service_account": true}',
            )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            payloads[0]['ref'],
            {'service': 'gmail', 'resource_type': 'message', 'resource_id': 'gmail-message-1'},
        )
        return cast(dict[str, Any], payloads[0]['data']), retained[0]

    def _clickup_source_data(self) -> tuple[dict[str, Any], _ClickUpContractClient]:
        """Poll the real ClickUp source adapter and return data plus its strict fake."""
        adapter = get_adapter('clickup')
        if adapter is None:
            raise RuntimeError('clickup adapter not registered')
        payloads: list[dict[str, Any]] = []
        retained: list[_ClickUpContractClient] = []

        def factory(*, token_supplier: Callable[[], str | None], config: dict[str, Any]) -> _ClickUpContractClient:
            """Validate source client construction and retain the strict fake."""
            fake = _ClickUpContractClient(expected_config=config, token_supplier=token_supplier)
            retained.append(fake)
            return fake

        def put_item(*, payload: dict[str, Any], external_id: str) -> PutItemResult:
            """Capture the exact source envelope."""
            self.assertEqual(external_id, 'clickup-task-1')
            payloads.append(payload)
            return PutItemResult(item_id=uuid4(), created=True)

        config = {
            'list_id': 'list-1',
            'max_results': 3,
            'statuses': ['in progress'],
            'include_closed': False,
        }
        with patch('libs.sources.adapters.clickup.ClickUpClient', factory):
            adapter.poll(
                config=config,
                put_item=put_item,
                credential_supplier=lambda: 'pk_test',
            )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            payloads[0]['ref'],
            {'service': 'clickup', 'resource_type': 'task', 'resource_id': 'clickup-task-1'},
        )
        return cast(dict[str, Any], payloads[0]['data']), retained[0]

    def test_gmail_cross_path_semantics_and_recursive_allowlists(self) -> None:
        """Assert independent Gmail semantics before comparing source and tool summaries."""
        source, source_fake = self._gmail_source_data()
        invoke, tool_fake = self._gmail_invoke()
        full = invoke('read', {'message_id': 'gmail-message-1'})

        expected_semantics = {
            'id': 'gmail-message-1',
            'thread_id': 'gmail-thread-1',
            'label_ids': ['INBOX', 'IMPORTANT'],
            'from': 'Alice <alice@example.com>',
            'to': ['Bob <bob@example.com>'],
            'cc': ['Carol <carol@example.com>'],
            'reply_to': 'reply@example.com',
            'return_path': '<bounce@example.com>',
            'subject': 'Projection contract',
            'message_id': '<message@example.com>',
            'snippet': 'Compact preview',
            'has_attachments': True,
        }
        for key, expected in expected_semantics.items():
            with self.subTest(path='source', key=key):
                self.assertEqual(source[key], expected)
            with self.subTest(path='tool', key=key):
                self.assertEqual(full[key], expected)
        self.assertEqual(
            source['authentication'],
            {
                'spf': {'verdict': 'pass', 'domain': 'example.com'},
                'dkim': [{'verdict': 'pass', 'domain': 'example.com'}],
                'dmarc': {'verdict': 'pass', 'policy': 'reject', 'header_from': 'example.com'},
                'arc': {'verdict': 'unknown'},
                'alignment': {
                    'from_domain': 'example.com',
                    'reply_to_domain': 'example.com',
                    'return_path_domain': 'example.com',
                    'from_matches_reply_to': True,
                    'from_matches_return_path': True,
                },
            },
        )
        self.assertEqual(
            source['advisories'],
            [{'code': 'mime_part', 'message': 'Malformed MIME part was omitted.'}],
        )
        self.assertEqual(
            source['attachments'][0],
            {
                'attachment_id': 'attachment-0',
                'filename': 'report-0.pdf',
                'mime_type': 'application/pdf',
                'size': 1000,
            },
        )
        self.assertEqual(source['received_at'], '2026-07-26T16:00:00+00:00')
        self.assertEqual(
            source['attachments_meta'],
            {'truncated': True, 'included': 25, 'total': 26, 'omitted_count': 1},
        )
        self.assertEqual(full['body'], {'text': 'G' * 32_000, 'source': 'plain'})
        self.assertEqual(
            full['body_truncation'],
            {
                'truncated': True,
                'omitted_chars': 1,
                'ref': {
                    'service': 'gmail',
                    'resource_type': 'message',
                    'resource_id': 'gmail-message-1',
                },
            },
        )
        self.assertEqual(source, {key: full[key] for key in source})
        _assert_shape(self, source, _GMAIL_SUMMARY_SHAPE)
        _assert_shape(
            self,
            full,
            {
                **_GMAIL_SUMMARY_SHAPE,
                'body': {'text': None, 'source': None},
                'body_truncation': {
                    'truncated': None,
                    'omitted_chars': None,
                    'ref': {'service': None, 'resource_type': None, 'resource_id': None},
                },
            },
        )
        _assert_no_canary(self, source)
        _assert_no_canary(self, full)
        self.assertEqual(source_fake.calls[0][0], 'poll_message_metadata')
        self.assertEqual(tool_fake.calls, [('get_message', {'message_id': 'gmail-message-1', 'fmt': 'full'})])

    def test_clickup_cross_path_semantics_and_recursive_allowlists(self) -> None:
        """Assert independent ClickUp semantics before comparing source and tool summaries."""
        source, source_fake = self._clickup_source_data()
        invoke, tool_fake = self._clickup_invoke()
        page = invoke('list_tasks', {'list_id': 'list-1', 'statuses': ['in progress']})
        listed = page['tasks'][0]

        expected = {
            'id': 'clickup-task-1',
            'custom_id': 'OPS-42',
            'name': 'Investigate projection',
            'status': 'in progress',
            'assignees': [{'id': '101', 'display_name': 'Person 1', 'email': 'person1@example.com'}],
            'priority': {'id': '2', 'priority': 'high'},
            'due_date': '1785200000000',
            'url': 'https://app.clickup.com/t/clickup-task-1',
            'date_updated': '1785081600000',
        }
        self.assertEqual(source, expected)
        self.assertEqual(listed, expected)
        self.assertEqual(source, listed)
        _assert_shape(self, source, _CLICKUP_SUMMARY_SHAPE)
        _assert_shape(self, page, {'tasks': [_CLICKUP_SUMMARY_SHAPE], 'last_page': None})
        _assert_no_canary(self, source)
        _assert_no_canary(self, page)
        self.assertEqual(source_fake.calls[0][0], 'list_tasks_up_to')
        self.assertEqual(
            tool_fake.calls,
            [('list_tasks', {'list_id': 'list-1', 'statuses': ('in progress',)})],
        )

    def test_gmail_tool_attachment_and_mutation_boundaries(self) -> None:
        """Exercise compact attachment bytes, mutation fields, and exact acknowledgements."""
        invoke, fake = self._gmail_invoke()

        attachment = invoke(
            'get_attachment',
            {'message_id': 'gmail-message-1', 'attachment_id': 'attachment-0'},
        )
        acknowledgement = invoke(
            'label',
            {
                'message_id': 'gmail-message-1',
                'add': ['STARRED'],
                'remove': ['IMPORTANT'],
            },
        )

        self.assertEqual(
            attachment,
            {
                'attachment_id': 'attachment-0',
                'size': 16,
                'mime_type': 'application/pdf',
                'data_base64': 'YXR0YWNobWVudCBieXRlcw==',
            },
        )
        self.assertEqual(
            acknowledgement,
            {'ok': True, 'message_id': 'gmail-message-1', 'label_ids': ['STARRED']},
        )
        _assert_shape(
            self,
            attachment,
            {'attachment_id': None, 'size': None, 'mime_type': None, 'data_base64': None},
        )
        _assert_shape(
            self,
            acknowledgement,
            {'ok': None, 'message_id': None, 'label_ids': None},
        )
        _assert_no_canary(self, (attachment, acknowledgement))
        self.assertEqual([call[0] for call in fake.calls], ['get_attachment', 'modify_labels'])

    def test_gmail_tool_read_enforces_literal_body_and_attachment_limits(self) -> None:
        """Exercise exact and overflowing Gmail body and attachment collections through the tool."""
        cases = (
            (25, _BODY_LIMIT, False, 0),
            (26, _BODY_LIMIT + 1, True, 1),
        )
        for attachment_count, body_length, truncated, omitted in cases:
            with self.subTest(attachment_count=attachment_count, body_length=body_length):
                invoke, _fake = self._gmail_invoke(
                    message=_gmail_message(
                        attachment_count=attachment_count,
                        body_length=body_length,
                    )
                )
                full = invoke('read', {'message_id': 'gmail-message-1'})

                self.assertEqual(len(full['body']['text']), _BODY_LIMIT)
                self.assertEqual(len(full['attachments']), min(attachment_count, _ATTACHMENT_LIMIT))
                self.assertEqual(
                    full['attachments_meta'],
                    {
                        'truncated': truncated,
                        'included': min(attachment_count, _ATTACHMENT_LIMIT),
                        'total': attachment_count,
                        'omitted_count': omitted,
                    },
                )
                if truncated:
                    self.assertEqual(full['body_truncation']['omitted_chars'], 1)
                else:
                    self.assertNotIn('body_truncation', full)
                _assert_no_canary(self, full)

    def test_clickup_full_tool_semantics_and_recursive_allowlists(self) -> None:
        """Populate and structurally lock every retained nested ClickUp branch."""
        invoke, fake = self._clickup_invoke()
        full = invoke('get_task', {'task_id': 'clickup-task-1'})

        self.assertEqual(
            {key: full[key] for key in _CLICKUP_SUMMARY_SHAPE},
            {
                'id': 'clickup-task-1',
                'custom_id': 'OPS-42',
                'name': 'Investigate projection',
                'status': 'in progress',
                'assignees': [{'id': '101', 'display_name': 'Person 1', 'email': 'person1@example.com'}],
                'priority': {'id': '2', 'priority': 'high'},
                'due_date': '1785200000000',
                'url': 'https://app.clickup.com/t/clickup-task-1',
                'date_updated': '1785081600000',
            },
        )
        self.assertEqual(full['description'], 'D' * 32_000)
        self.assertEqual(full['description_truncation'], {'truncated': True, 'omitted_chars': 1})
        self.assertEqual(full['markdown_description'], 'M' * 32_000)
        self.assertEqual(full['markdown_description_truncation'], {'truncated': True, 'omitted_chars': 1})
        self.assertEqual(
            full['location'],
            {
                'list': {'id': 'list-1', 'name': 'Inbox'},
                'folder': {'id': 'folder-1', 'name': 'Operations'},
                'space': {'id': 'space-1', 'name': 'Engineering'},
            },
        )
        self.assertEqual(full['creator'], {'id': '102', 'display_name': 'Person 2', 'email': 'person2@example.com'})
        self.assertEqual(full['watchers'], [{'id': '103', 'display_name': 'Person 3', 'email': 'person3@example.com'}])
        self.assertEqual(full['mentions'], [{'id': '104', 'display_name': 'Person 4'}])
        self.assertEqual(full['tags'], [{'name': 'security'}])
        self.assertEqual(
            full['custom_fields'],
            [{'id': 'field-1', 'name': 'Risk', 'type': 'drop_down', 'value': {'id': 'risk-high', 'name': 'High'}}],
        )
        self.assertEqual(
            full['dependencies'],
            [
                {
                    'id': 'dependency-1',
                    'name': 'Dependency',
                    'url': 'https://app.clickup.com/t/dependency-1',
                    'status': 'open',
                }
            ],
        )
        self.assertEqual(
            full['linked_tasks'],
            [
                {
                    'id': 'linked-1',
                    'name': 'Linked',
                    'url': 'https://app.clickup.com/t/linked-1',
                    'status': 'done',
                }
            ],
        )
        self.assertEqual(
            full['checklists'],
            [
                {
                    'id': 'checklist-1',
                    'name': 'Review',
                    'resolved': 1,
                    'unresolved': 0,
                    'items': [{'id': 'item-1', 'name': 'Inspect output', 'resolved': True}],
                }
            ],
        )
        self.assertEqual(
            full['attachments'][0],
            {
                'filename': 'evidence-0.txt',
                'mime_type': 'text/plain',
                'extension': 'txt',
                'size': 100,
                'uploader': {'id': '100', 'display_name': 'Person 0'},
                'date': '1785000000000',
                'url': 'https://files.example/evidence-0.txt',
                'url_w_query': 'https://files.example/evidence-0.txt?download=1',
                'url_w_host': 'https://files.example/evidence-0.txt',
            },
        )
        self.assertEqual(
            full['attachments_meta'],
            {'included': 25, 'total': 30, 'truncated': True, 'omitted_count': 5},
        )
        self.assertEqual(
            full['subtasks'][0],
            {
                'id': 'subtask-0',
                'custom_id': 'SUB-0',
                'name': 'Child 0',
                'status': 'open',
                'assignees': [{'id': '100', 'display_name': 'Person 0'}],
                'priority': {'id': '3', 'priority': 'normal'},
                'due_date': '1786000000000',
                'url': 'https://app.clickup.com/t/subtask-0',
            },
        )
        self.assertEqual(
            full['subtasks_meta'],
            {'included': 25, 'total': 30, 'truncated': True, 'omitted_count': 5},
        )
        self.assertEqual(
            full['comments'][0],
            {
                'id': 'comment-10',
                'text': '10:' + ('C' * 3997),
                'date': '10',
                'user': {'id': '110', 'display_name': 'Person 10'},
                'text_truncation': {'truncated': True, 'omitted_chars': 5},
            },
        )
        self.assertEqual(
            full['comments_meta'],
            {'included': 10, 'total': 15, 'truncated': True, 'omitted_count': 5},
        )
        self.assertEqual(
            full['advisories'],
            [],
        )
        _assert_shape(self, full, _CLICKUP_FULL_SHAPE)
        _assert_no_canary(self, full)
        self.assertEqual([call[0] for call in fake.calls], ['get_task', 'list_comments'])

    def test_clickup_tool_enforces_literal_description_and_collection_limits(self) -> None:
        """Exercise exact and overflowing full-task bounds with provider totals through the tool."""
        cases = (
            {
                'count': 25,
                'description_length': _BODY_LIMIT,
                'attachment_total': 25,
                'subtask_total': 25,
                'comment_count': 10,
                'comment_total': 10,
                'omitted': 0,
            },
            {
                'count': 26,
                'description_length': _BODY_LIMIT + 1,
                'attachment_total': 30,
                'subtask_total': 30,
                'comment_count': 11,
                'comment_total': 15,
                'omitted': 5,
            },
        )
        for case in cases:
            with self.subTest(count=case['count']):
                task = _clickup_task(
                    description_length=case['description_length'],
                    attachment_count=case['count'],
                    attachment_total=case['attachment_total'],
                    subtask_count=case['count'],
                    subtask_total=case['subtask_total'],
                )
                comments = {
                    'comments': [_clickup_comment(index) for index in range(case['comment_count'])],
                    'total': case['comment_total'],
                    **_noise(f"comments-boundary-{case['count']}"),
                }
                invoke, _fake = self._clickup_invoke(task=task, comments=comments)
                full = invoke('get_task', {'task_id': 'clickup-task-1'})

                self.assertEqual(len(full['description']), _BODY_LIMIT)
                self.assertEqual(len(full['markdown_description']), _BODY_LIMIT)
                self.assertEqual(len(full['attachments']), _ATTACHMENT_LIMIT)
                self.assertEqual(len(full['subtasks']), _SUBTASK_LIMIT)
                self.assertEqual(len(full['comments']), _COMMENT_LIMIT)
                self.assertEqual(
                    full['attachments_meta'],
                    {
                        'included': _ATTACHMENT_LIMIT,
                        'total': case['attachment_total'],
                        'truncated': bool(case['omitted']),
                        'omitted_count': case['omitted'],
                    },
                )
                self.assertEqual(
                    full['subtasks_meta'],
                    {
                        'included': _SUBTASK_LIMIT,
                        'total': case['subtask_total'],
                        'truncated': bool(case['omitted']),
                        'omitted_count': case['omitted'],
                    },
                )
                self.assertEqual(
                    full['comments_meta'],
                    {
                        'included': _COMMENT_LIMIT,
                        'total': case['comment_total'],
                        'truncated': bool(case['omitted']),
                        'omitted_count': case['omitted'],
                    },
                )
                self.assertEqual(len(full['comments'][0]['text']), _COMMENT_TEXT_LIMIT)
                if case['omitted']:
                    self.assertEqual(full['description_truncation'], {'truncated': True, 'omitted_chars': 1})
                    self.assertEqual(full['markdown_description_truncation'], {'truncated': True, 'omitted_chars': 1})
                else:
                    self.assertNotIn('description_truncation', full)
                    self.assertNotIn('markdown_description_truncation', full)
                _assert_no_canary(self, full)

    def test_clickup_mutation_and_optional_comments_failure_are_compact(self) -> None:
        """Lock exact mutation and optional-comment failure envelopes through strict dispatch."""
        mutation_invoke, mutation_fake = self._clickup_invoke()
        acknowledgement = mutation_invoke(
            'update_task',
            {'task_id': 'clickup-task-1', 'name': 'Renamed', 'status': 'done'},
        )
        read_invoke, read_fake = self._clickup_invoke(fail_comments=True)
        full = read_invoke('get_task', {'task_id': 'clickup-task-1'})

        self.assertEqual(
            acknowledgement,
            {
                'ok': True,
                'task_id': 'clickup-task-1',
                'url': 'https://app.clickup.com/t/clickup-task-1',
                'status': 'done',
                'name': 'Renamed',
            },
        )
        _assert_shape(
            self,
            acknowledgement,
            {'ok': None, 'task_id': None, 'url': None, 'status': None, 'name': None},
        )
        self.assertEqual(full['comments'], [])
        self.assertEqual(
            full['comments_meta'],
            {'included': 0, 'total': None, 'truncated': False, 'omitted_count': 0},
        )
        self.assertEqual(
            full['advisories'],
            [{'code': 'comments_unavailable', 'message': 'Comments could not be loaded.'}],
        )
        _assert_no_canary(self, acknowledgement)
        _assert_no_canary(self, full)
        self.assertEqual(mutation_fake.calls[0][0], 'update_task')
        self.assertEqual([call[0] for call in read_fake.calls], ['get_task', 'list_comments'])

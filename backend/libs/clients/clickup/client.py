# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Generic ClickUp API v2 client (Django-free) authenticated by a personal token.

No official ClickUp SDK exists, so this is a thin `httpx` wrapper. The token is supplied
lazily by `token_supplier` and read per request; it is never stored on the client beyond a
single call (secret-retention rule, docs/ARCHITECTURE.md). `transport` is a test seam.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from libs.clients.clickup.errors import (
    ClickUpAPIError,
    ClickUpAuthError,
    ClickUpNotFoundError,
)

_DEFAULT_BASE_URL = 'https://api.clickup.com/api/v2'
_TIMEOUT = 30.0
SleepFn = Callable[[float], None]


class ClickUpClient:
    """Thin wrapper over the ClickUp v2 REST API."""

    def __init__(
        self,
        *,
        token_supplier: Callable[[], str | None],
        config: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._token_supplier = token_supplier
        self._config = config or {}
        self._base_url = self._config.get('base_url', _DEFAULT_BASE_URL)
        self._transport = transport
        self._sleep = sleep_fn or time.sleep

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue one request with a single 429/5xx retry; map non-2xx to typed failures."""
        token = self._token_supplier()
        if not token:
            raise ClickUpAuthError('no clickup credential resolved')
        headers = {'Authorization': token}
        last_resp: httpx.Response | None = None
        for attempt in range(2):
            with httpx.Client(base_url=self._base_url, transport=self._transport, timeout=_TIMEOUT) as client:
                resp = client.request(method, path, params=params, json=json_body, headers=headers)
            last_resp = resp
            if resp.status_code in (401, 403):
                raise ClickUpAuthError(f'clickup auth failed ({resp.status_code})')
            if resp.status_code == 404:
                raise ClickUpNotFoundError(f'clickup resource not found: {path}')
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 0:
                    retry_after = float(resp.headers.get('Retry-After', '1'))
                    self._sleep(retry_after)
                    continue
                raise ClickUpAPIError(f'clickup api failure ({resp.status_code})', status=resp.status_code)
            if resp.status_code >= 400:
                raise ClickUpAPIError(f'clickup api failure ({resp.status_code})', status=resp.status_code)
            return cast(dict[str, Any], resp.json())
        if last_resp is None:
            raise ClickUpAPIError('clickup api failure (no response)')
        raise ClickUpAPIError(f'clickup api failure ({last_resp.status_code})', status=last_resp.status_code)

    def list_teams(self) -> dict[str, Any]:
        """List workspaces (teams) the token can access."""
        return self._request('GET', '/team')

    def list_spaces(self, team_id: str) -> dict[str, Any]:
        """List spaces in a workspace; requires a non-empty *team_id*."""
        if not team_id:
            raise ValueError('team_id is required')
        return self._request('GET', f'/team/{team_id}/space')

    def list_lists(self, space_id: str) -> dict[str, Any]:
        """List folderless lists in a space."""
        return self._request('GET', f'/space/{space_id}/list')

    def list_tasks(
        self,
        *,
        list_id: str,
        statuses: tuple[str, ...] = (),
        updated_gt: int | None = None,
        include_closed: bool = False,
        page: int = 0,
    ) -> dict[str, Any]:
        """List tasks in a list for one page, optionally filtered by status/update time."""
        params: dict[str, Any] = {'page': page, 'include_closed': str(include_closed).lower()}
        if statuses:
            params['statuses[]'] = list(statuses)
        if updated_gt is not None:
            params['date_updated_gt'] = updated_gt
        return self._request('GET', f'/list/{list_id}/task', params=params)

    def list_tasks_up_to(
        self,
        *,
        list_id: str,
        max_results: int,
        statuses: tuple[str, ...] = (),
        updated_gt: int | None = None,
        include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        """Paginate ``list_tasks`` until *max_results* tasks are collected or pages end."""
        tasks: list[dict[str, Any]] = []
        page = 0
        while len(tasks) < max_results:
            resp = self.list_tasks(
                list_id=list_id,
                statuses=statuses,
                updated_gt=updated_gt,
                include_closed=include_closed,
                page=page,
            )
            batch = resp.get('tasks', [])
            tasks.extend(batch)
            if resp.get('last_page', True) or not batch:
                break
            page += 1
        return tasks[:max_results]

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch one task."""
        return self._request('GET', f'/task/{task_id}')

    def create_task(
        self, *, list_id: str, name: str, description: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """Create a task in a list (used for INBOX routing)."""
        body: dict[str, Any] = {'name': name}
        if description is not None:
            body['description'] = description
        if status is not None:
            body['status'] = status
        return self._request('POST', f'/list/{list_id}/task', json_body=body)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """Update task fields (name/status/description/…)."""
        return self._request('PUT', f'/task/{task_id}', json_body=dict(fields))

    def create_comment(self, task_id: str, *, text: str) -> dict[str, Any]:
        """Add a comment to a task."""
        return self._request('POST', f'/task/{task_id}/comment', json_body={'comment_text': text})

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Delete a task (denied by default in example configs)."""
        return self._request('DELETE', f'/task/{task_id}')

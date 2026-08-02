# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Ensure/release Obsidian vault bindings for an agent's `obsidian` tool instances.

Called from `materialize.py` (after a config save) and `delete.py` (after an agent
delete), both from within `transaction.on_commit` — the vault service is a separate
process over HTTP and may be briefly unreachable, so neither ensure nor release ever
raises. A failure here just means the vault service stays out of sync with the agent's
config until the next materialize (e.g. an operator re-saving the config) or a manual
retry; it must never roll back the DB transaction that already committed.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from apps.agents.models import Agent
from apps.keys.exceptions import KeyNotFoundError, KeyTypeMismatchError
from apps.keys.services.queries import make_secret_supplier
from django.conf import settings
from libs.agent_spec import AgentConfigSpec, ToolInstance
from libs.clients.obsidian.client import ObsidianVaultClient
from libs.clients.obsidian.config import parse_obsidian_tool_config
from libs.clients.obsidian.errors import ObsidianConfigError, ObsidianVaultError

logger = logging.getLogger(__name__)

_TOOL_TYPE = 'obsidian'
_CREDENTIAL_TYPE = 'obsidian'


def _parse_credential_json(raw: str, *, agent_id: UUID, tool_id: str) -> dict[str, Any] | None:
    """Parse the resolved secret as the `{"auth_token", "encryption_password"?}` JSON shape.

    Returns None (logging why) instead of raising — one tool instance's bad credential
    must not prevent ensuring the other instances' vaults.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning('agent %s obsidian tool %s credential is not valid JSON', agent_id, tool_id)
        return None
    if not isinstance(parsed, dict):
        logger.warning('agent %s obsidian tool %s credential JSON is not an object', agent_id, tool_id)
        return None
    auth_token = parsed.get('auth_token')
    if not isinstance(auth_token, str) or not auth_token:
        logger.warning('agent %s obsidian tool %s credential JSON missing auth_token', agent_id, tool_id)
        return None
    encryption_password = parsed.get('encryption_password')
    if encryption_password is not None and not isinstance(encryption_password, str):
        logger.warning('agent %s obsidian tool %s credential JSON has invalid encryption_password', agent_id, tool_id)
        return None
    credential: dict[str, Any] = {'auth_token': auth_token}
    if encryption_password:
        credential['encryption_password'] = encryption_password
    return credential


def _build_binding(agent: Agent, tool: ToolInstance) -> dict[str, Any] | None:
    """Resolve one `obsidian` tool instance into an `ensure_vaults` binding, or None to skip it.

    Skips (logging why) rather than raising on bad config, a missing/unresolvable
    credential_ref, or malformed credential JSON, so one misconfigured tool instance
    does not block ensuring the others.
    """
    try:
        config = parse_obsidian_tool_config(tool.config)
    except ObsidianConfigError as exc:
        logger.warning('agent %s obsidian tool %s has invalid config: %s', agent.id, tool.id, exc)
        return None
    if not tool.credential_ref:
        logger.warning('agent %s obsidian tool %s has no credential_ref', agent.id, tool.id)
        return None
    try:
        raw_credential = make_secret_supplier(agent.user_id, name=tool.credential_ref, type=_CREDENTIAL_TYPE)()
    except (KeyNotFoundError, KeyTypeMismatchError) as exc:
        logger.warning('agent %s obsidian tool %s credential unresolved: %s', agent.id, tool.id, exc)
        return None
    if not raw_credential:
        logger.warning('agent %s obsidian tool %s credential resolved empty', agent.id, tool.id)
        return None
    credential = _parse_credential_json(raw_credential, agent_id=agent.id, tool_id=tool.id)
    if credential is None:
        return None
    return {'vault_id': config.vault, 'roots': list(config.roots), 'credential': credential}


def sync_obsidian_vaults(agent: Agent, spec: AgentConfigSpec) -> None:
    """Ensure the vault service holds bindings for every `obsidian` tool instance in *spec*.

    No-ops (logging at info) when there are no `obsidian` tools, or when
    `settings.OBSIDIAN_VAULT_URL` is unset — the latter keeps tests/dev environments
    without the vault service materializing agent configs cleanly. See module docstring
    for why failures are only logged, never raised.
    """
    obsidian_tools = [tool for tool in spec.tools if tool.type == _TOOL_TYPE]
    if not obsidian_tools:
        return
    if not settings.OBSIDIAN_VAULT_URL:
        logger.info('agent %s has obsidian tools but OBSIDIAN_VAULT_URL is unset; skipping vault ensure', agent.id)
        return

    bindings = [binding for binding in (_build_binding(agent, tool) for tool in obsidian_tools) if binding is not None]
    if not bindings:
        return

    try:
        client = ObsidianVaultClient(
            base_url=settings.OBSIDIAN_VAULT_URL,
            token=settings.OBSIDIAN_VAULT_TOKEN,
            agent_id=str(agent.id),
        )
        client.ensure_vaults(bindings)
    except ObsidianVaultError as exc:
        logger.warning('agent %s obsidian vault ensure failed: %s', agent.id, exc)


def release_obsidian_vaults(agent_id: UUID) -> None:
    """Release all vault bindings for a deleted agent; no-op if the vault service isn't configured.

    `agent_id` must be captured before the agent row is deleted — by the time this
    runs (post-commit) the row is already gone. See module docstring for why failures
    are only logged, never raised.
    """
    if not settings.OBSIDIAN_VAULT_URL:
        return
    try:
        client = ObsidianVaultClient(
            base_url=settings.OBSIDIAN_VAULT_URL,
            token=settings.OBSIDIAN_VAULT_TOKEN,
            agent_id=str(agent_id),
        )
        client.release_vaults()
    except ObsidianVaultError as exc:
        logger.warning('agent %s obsidian vault release failed: %s', agent_id, exc)

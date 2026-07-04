# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Async session metadata tasks."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from apps.runner.llm_config import provider_config_from_spec
from apps.sessions.models import AgentSession
from apps.sessions.services.queries import get_first_input_text, get_session_name
from celery import shared_task

# isort: split

from libs.agent_spec import LLMSpec
from libs.algorithms.chat_name import (
    DEFAULT_CHAT_NAME_CONFIG,
    ChatNameConfig,
    generate_chat_name,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True, max_retries=2)
def generate_session_name(self: Any, session_id: str) -> None:
    uid = UUID(session_id)
    if get_session_name(uid) is not None:
        return
    text = get_first_input_text(uid)
    if text is None:
        return
    try:
        session = AgentSession.objects.select_related('agent').get(pk=uid)
        user_id = session.agent.user_id
        llm_cfg = provider_config_from_spec(
            LLMSpec(
                provider=DEFAULT_CHAT_NAME_CONFIG.provider,
                model=DEFAULT_CHAT_NAME_CONFIG.model,
                temperature=DEFAULT_CHAT_NAME_CONFIG.temperature,
            ),
            user_id=user_id,
        )
        name = generate_chat_name(text, config=DEFAULT_CHAT_NAME_CONFIG, llm=llm_cfg)
    except Exception:  # pylint: disable=broad-except
        logger.exception('Chat name generation failed for session %s', session_id)
        name = generate_chat_name(text, config=ChatNameConfig(enabled=False))
    from apps.sessions.services.commands import update_session_name

    update_session_name(uid, name)

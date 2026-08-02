# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for the generic agent lifecycle registry."""

from __future__ import annotations

import logging
from uuid import uuid4

from apps.agents.lifecycle import (
    isolated_lifecycle_handlers,
    notify_agent_deleted,
    notify_agent_materialized,
    register_agent_deleted_handler,
    register_agent_materialized_handler,
)
from libs.agent_spec import AgentConfigSpec, LLMSpec

from olib.py.django.test.cases import OTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems

_LOGGER = 'apps.agents.lifecycle'


class TestAgentLifecycleRegistry(OTestCase):
    def test_materialized_handlers_receive_ids_and_spec(self) -> None:
        """Registered materialize handlers are invoked with agent/user/spec."""
        with isolated_lifecycle_handlers():
            seen: list[tuple[object, object, object]] = []
            register_agent_materialized_handler(lambda a, u, s: seen.append((a, u, s)))
            agent_id = uuid4()
            spec = AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_')

            notify_agent_materialized(agent_id, 7, spec)

            self.assertEqual(seen, [(agent_id, 7, spec)])

    def test_deleted_handlers_receive_agent_id(self) -> None:
        """Registered delete handlers are invoked with the agent id."""
        with isolated_lifecycle_handlers():
            seen: list[object] = []
            register_agent_deleted_handler(seen.append)
            agent_id = uuid4()

            notify_agent_deleted(agent_id)

            self.assertEqual(seen, [agent_id])

    @expectLogItems([ExpectLogItem(_LOGGER, logging.ERROR, r'agent materialize lifecycle handler .* failed', count=1)])
    def test_failing_handler_does_not_block_siblings(self) -> None:
        """One raising handler must not prevent later handlers from running."""
        with isolated_lifecycle_handlers():
            seen: list[str] = []

            def _fail(_agent_id: object, _user_id: object, _spec: object) -> None:
                raise RuntimeError('handler boom')

            register_agent_materialized_handler(_fail)
            register_agent_materialized_handler(lambda *_args: seen.append('ok'))
            spec = AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_')

            notify_agent_materialized(uuid4(), 1, spec)

            self.assertEqual(seen, ['ok'])

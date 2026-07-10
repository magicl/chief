# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Checkpointed agent session step loop."""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from apps.agents.tool_wiring import build_bound_tools
from apps.runner.backends.base import SessionBackend
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.errors import (
    SessionFailure,
    session_failure_from_provider_error,
    session_failure_from_provider_runtime_error,
)
from apps.runner.llm_config import provider_config_from_spec
from apps.runner.tool_definitions import build_tool_definitions
from apps.sessions.models import AgentSession, AgentSessionEventKind, AgentSessionStatus
from django.utils import timezone

# isort: split

from libs.agent_spec import AgentConfigSpec, ToolInstance
from libs.providers.llm.base import LLMProvider, ProviderError, StreamResult
from libs.providers.llm.errors import ProviderConfigurationError
from libs.providers.llm.registry import make_provider
from libs.tools.base import parse_qualified_tool_name

logger = logging.getLogger(__name__)


@dataclass
class LoopControl:
    """Mutable control state drained from the mailbox at checkpoints."""

    abort: bool = False
    pause: bool = False
    pending_inputs: list[str] = field(default_factory=list)


class SessionRunner:
    def __init__(self, backend: SessionBackend, *, emit_restart: bool = False) -> None:
        self.backend = backend
        self.config_spec: AgentConfigSpec = backend.get_spec()
        session = getattr(backend, 'session', None)
        self.bound_tools = build_bound_tools(
            self.config_spec.tools,
            user_id=self.backend.user_id,
            agent_id=getattr(session, 'agent_id', None),
            session_id=backend.session_id,
        )
        self.control = LoopControl()
        self.emit_restart = emit_restart

    @classmethod
    def for_session(cls, session: AgentSession, *, emit_restart: bool = False) -> SessionRunner:
        return cls(DjangoSessionBackend(session), emit_restart=emit_restart)

    def run(self) -> None:
        if self.emit_restart:
            event = self.backend.append_event(AgentSessionEventKind.RESTART, {})
            self.backend.publish_event(event)

        self._drain_mailbox()

        tool_definitions = build_tool_definitions(
            self.config_spec.tools,
            is_allowed=self._is_allowed,
        )
        provider: LLMProvider | None = None

        while not self.control.abort:
            if self.control.pause:
                self.backend.set_status(AgentSessionStatus.PAUSED)
                return

            messages = self.backend.rebuild_messages(system_prompt=self.config_spec.system_prompt)
            if self._needs_user_input(messages):
                self.backend.set_status(AgentSessionStatus.WAITING)
                return

            if provider is None:
                try:
                    user_id = self.backend.user_id
                    provider = make_provider(
                        provider_config_from_spec(
                            self.config_spec.llm,
                            user_id=user_id,
                            credential_ref=self.config_spec.llm.credential_ref,
                        ),
                    )
                except ProviderConfigurationError as exc:
                    self._record_failure(session_failure_from_provider_error(exc))
                    return

            result = provider.collect(messages, tool_definitions)
            if result.error:
                self._record_provider_error(result.error)
                return

            self._emit_output(provider, result)

            if result.tool_calls:
                for call in result.tool_calls:
                    self._handle_tool_call(call)
                self._drain_mailbox()
                continue

            self.backend.set_status(AgentSessionStatus.WAITING)
            return

        self.backend.set_status(AgentSessionStatus.DONE)
        self.backend.set_ended_at(timezone.now())

    def _record_provider_error(self, error: ProviderError) -> None:
        self._record_failure(session_failure_from_provider_runtime_error(error))

    def _record_failure(self, exc: Exception) -> None:
        if isinstance(exc, SessionFailure):
            message = exc.message
            payload: dict[str, Any] = {'message': message, 'code': exc.code}
            logger.info('Session %s failure: %s (%s)', self.backend.session_id, message, exc.code)
        else:
            message = str(exc)
            payload = {'message': message, 'code': 'unexpected_failure', 'traceback': traceback.format_exc()}
            logger.exception('Session %s unexpected failure', self.backend.session_id)
        event = self.backend.append_event(AgentSessionEventKind.FAILURE, payload)
        self.backend.publish_event(event)
        self.backend.set_status(AgentSessionStatus.WAITING)

    def _drain_mailbox(self) -> None:
        for msg in self.backend.drain_mailbox():
            action = msg.get('action')
            if action == 'chat':
                content = msg.get('content', '')
                if content:
                    self.control.pending_inputs.append(content)
                    self.backend.record_input(content)
            elif action == 'pause':
                self.control.pause = True
            elif action == 'abort':
                self.control.abort = True

        self.control.pending_inputs.clear()

    def _emit_output(self, provider: LLMProvider, result: StreamResult) -> None:
        usage = result.usage
        cost = provider.compute_cost_usd(usage, latency_ms=result.latency_ms) if usage else None
        event = self.backend.append_event(
            AgentSessionEventKind.OUTPUT,
            {'content': result.content},
            model=usage.model if usage else self.config_spec.llm.model,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cost_usd=cost,
            latency_ms=result.latency_ms,
        )
        self.backend.publish_event(event)

    def _handle_tool_call(self, call: dict[str, Any]) -> None:
        wire_name = call['name']
        instance_id, function_name = self._parse_tool_name(wire_name)
        arguments = call.get('arguments', {})
        call_id = call.get('id') or str(uuid.uuid4())

        bound = self.bound_tools.get(instance_id)
        tool_type = bound.tool_type if bound is not None else None

        if not self._is_allowed(instance_id, function_name):
            result_content = json.dumps({'failure': f'Permission denied for {instance_id}.{function_name}'})
            tool_latency_ms = 0
        elif bound is None:
            result_content = json.dumps({'failure': f'Unknown tool instance {instance_id!r}'})
            tool_latency_ms = 0
        else:
            started = time.monotonic()
            try:
                raw = bound.invoke(function_name, arguments)
                result_content = raw if isinstance(raw, str) else json.dumps(raw)
            except Exception as exc:  # pylint: disable=broad-except
                result_content = json.dumps({'failure': str(exc)})
            tool_latency_ms = int((time.monotonic() - started) * 1000)

        tc_event = self.backend.append_event(
            AgentSessionEventKind.TOOL_CALL,
            {
                'call_id': call_id,
                'instance_id': instance_id,
                'type': tool_type,
                'function': function_name,
                'arguments': arguments,
            },
        )
        self.backend.publish_event(tc_event)

        tr_event = self.backend.append_event(
            AgentSessionEventKind.TOOL_RESULT,
            {'call_id': call_id, 'content': result_content},
            latency_ms=tool_latency_ms if tool_latency_ms > 0 else None,
        )
        self.backend.publish_event(tr_event)

    def _is_allowed(
        self,
        instance_id: str,
        function_name: str,
        *,
        instance: ToolInstance | None = None,
    ) -> bool:
        if instance is None:
            for inst in self.config_spec.tools:
                if inst.id == instance_id:
                    instance = inst
                    break
        if instance is None:
            return False
        if function_name in instance.deny:
            return False
        if '*' in instance.allow:
            return True
        return function_name in instance.allow

    @staticmethod
    def _needs_user_input(messages: list[dict[str, Any]]) -> bool:
        """Wait for chat input before the first provider call of a turn.

        Mid-turn tool continuations include ``tool`` role messages and should proceed
        even when no new user message was appended in this iteration.
        """
        if any(m.get('role') == 'tool' for m in messages):
            return False
        return not any(m.get('role') == 'user' and str(m.get('content', '')).strip() for m in messages)

    @staticmethod
    def _parse_tool_name(qualified_name: str) -> tuple[str, str]:
        return parse_qualified_tool_name(qualified_name)

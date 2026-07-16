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
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from apps.agents.tool_wiring import build_bound_tools
from apps.keys.services.queries import make_secret_supplier
from apps.runner.backends.base import RecordedEvent, SessionBackend
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.errors import (
    SessionFailure,
    session_failure_from_provider_error,
    session_failure_from_provider_runtime_error,
)
from apps.runner.hooks import HookRegistry, HookSet
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
from libs.tools.context import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class LoopControl:
    """Mutable control state drained from the mailbox at checkpoints."""

    abort: bool = False
    pause: bool = False
    pending_inputs: list[str] = field(default_factory=list)


class SessionRunner:
    def __init__(
        self,
        backend: SessionBackend,
        *,
        emit_restart: bool = False,
        client_factories: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        """Create a runner for one session backend and initialize tool client wiring."""
        self.backend = backend
        self.config_spec: AgentConfigSpec = backend.get_spec()
        session = getattr(backend, 'session', None)

        user_id = self.backend.user_id
        supplier_factory: Callable[[str | None, str], Callable[[], str | None]] | None = None
        if user_id is not None:
            _uid = user_id

            def _make_supplier(cred_ref: str | None, cred_type: str) -> Callable[[], str | None]:
                """Wrap make_secret_supplier into the ToolContext factory signature."""
                return make_secret_supplier(_uid, name=cred_ref, type=cred_type)

            supplier_factory = _make_supplier

        self.ctx = ToolContext(
            spec=self.config_spec,
            user_id=user_id,
            agent_id=getattr(session, 'agent_id', None),
            session_id=backend.session_id,
            secret_supplier_factory=supplier_factory,
            client_factories=client_factories or {},
        )
        self.bound_tools = build_bound_tools(self.config_spec.tools, ctx=self.ctx)
        self.control = LoopControl()
        self.emit_restart = emit_restart
        self.hooks = HookRegistry()

    @classmethod
    def for_session(
        cls,
        session: AgentSession,
        *,
        emit_restart: bool = False,
        client_factories: dict[str, Callable[..., Any]] | None = None,
    ) -> SessionRunner:
        """Build a runner for a persisted Django session with optional tool client factories."""
        return cls(DjangoSessionBackend(session), emit_restart=emit_restart, client_factories=client_factories)

    def add_hook(self, hooks: HookSet) -> None:
        """Attach observability callbacks to this runner instance."""
        self.hooks.add(hooks)

    def run(self) -> None:
        """Run session turns until waiting, paused, aborted, done, or failed."""
        self.hooks.fire('on_run_start')
        try:
            if self.emit_restart:
                event = self._append_event(AgentSessionEventKind.RESTART, {})
                self.backend.publish_event(event)

            self._drain_mailbox()

            tool_definitions = build_tool_definitions(
                self.config_spec.tools,
                ctx=self.ctx,
                is_allowed=self._is_allowed,
            )
            provider: LLMProvider | None = None

            while not self.control.abort:
                if self.control.pause:
                    self._set_status(AgentSessionStatus.PAUSED)
                    return

                messages = self.backend.rebuild_messages(system_prompt=self.config_spec.system_prompt)
                if self._needs_user_input(messages):
                    self._set_status(AgentSessionStatus.WAITING)
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

                self.hooks.fire('on_generate_start', messages, tool_definitions)
                result = provider.collect(messages, tool_definitions)
                self.hooks.fire('on_generate_end', result)
                if result.error:
                    self._record_provider_error(result.error)
                    return

                self._emit_output(provider, result)

                if result.tool_calls:
                    for call in result.tool_calls:
                        self._handle_tool_call(call)
                    self._drain_mailbox()
                    continue

                self._set_status(AgentSessionStatus.WAITING)
                return

            self._set_status(AgentSessionStatus.DONE)
            self.backend.set_ended_at(timezone.now())
        finally:
            self.hooks.fire('on_run_end')

    def _record_provider_error(self, error: ProviderError) -> None:
        """Persist a provider runtime failure as a session failure event."""
        self._record_failure(session_failure_from_provider_runtime_error(error))

    def _record_failure(self, exc: Exception) -> None:
        """Persist a session failure event and move the session back to waiting."""
        if isinstance(exc, SessionFailure):
            message = exc.message
            payload: dict[str, Any] = {'message': message, 'code': exc.code}
            logger.info('Session %s failure: %s (%s)', self.backend.session_id, message, exc.code)
        else:
            message = str(exc)
            payload = {'message': message, 'code': 'unexpected_failure', 'traceback': traceback.format_exc()}
            logger.exception('Session %s unexpected failure', self.backend.session_id)
        event = self._append_event(AgentSessionEventKind.FAILURE, payload)
        self.backend.publish_event(event)
        self._set_status(AgentSessionStatus.WAITING)

    def _drain_mailbox(self) -> None:
        """Apply pending mailbox controls and record incoming user messages."""
        for msg in self.backend.drain_mailbox():
            action = msg.get('action')
            if action == 'chat':
                content = msg.get('content', '')
                if content:
                    self.control.pending_inputs.append(content)
                    self._record_input(content)
            elif action == 'pause':
                self.control.pause = True
            elif action == 'abort':
                self.control.abort = True

        self.control.pending_inputs.clear()

    def _emit_output(self, provider: LLMProvider, result: StreamResult) -> None:
        """Record provider output and usage details as a session event."""
        usage = result.usage
        cost = provider.compute_cost_usd(usage, latency_ms=result.latency_ms) if usage else None
        event = self._append_event(
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
        """Invoke one requested tool call and record its call/result events."""
        wire_name = call['name']
        instance_id, function_name = self._parse_tool_name(wire_name)
        arguments = call.get('arguments', {})
        call_id = call.get('id') or str(uuid.uuid4())
        self.hooks.fire('on_tool_call_start', call)

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

        self.hooks.fire('on_tool_call_end', call, result_content)

        tc_event = self._append_event(
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

        tr_event = self._append_event(
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
        """Return whether the configured tool instance permits the function call."""
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
        """Parse provider tool names into configured instance and function names."""
        return parse_qualified_tool_name(qualified_name)

    def _append_event(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> RecordedEvent:
        """Append an event through the backend and notify event hooks."""
        event = self.backend.append_event(
            kind,
            payload,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self.hooks.fire('on_event', event)
        return event

    def _record_input(self, content: str) -> RecordedEvent:
        """Record user input through the backend and notify event hooks."""
        event = self.backend.record_input(content)
        self.hooks.fire('on_event', event)
        return event

    def _set_status(self, status: str) -> None:
        """Set the backend status and notify status hooks."""
        self.backend.set_status(status)
        self.hooks.fire('on_status', status)

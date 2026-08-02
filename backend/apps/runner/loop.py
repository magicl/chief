# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Checkpointed agent session step loop."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, NoReturn

from apps.agents.tool_wiring import build_bound_tools
from apps.keys.services.queries import make_secret_supplier
from apps.runner.activity_recorder import BackendActivityRecorder
from apps.runner.backends.base import RecordedActivity, SessionBackend
from apps.runner.backends.django import DjangoSessionBackend
from apps.runner.errors import (
    SessionFailure,
    session_failure_from_provider_error,
    session_failure_from_provider_runtime_error,
)
from apps.runner.hooks import HookRegistry, HookSet
from apps.runner.limits import SessionLimitChecker
from apps.runner.llm_config import provider_config_from_spec
from apps.runner.tool_definitions import build_tool_definitions
from apps.sessions.models import (
    AgentSession,
    AgentSessionActivityKind,
    AgentSessionActivityStatus,
    AgentSessionStatus,
)
from django.conf import settings
from django.utils import timezone

# isort: split

from libs.agent_spec import AgentConfigSpec, ToolInstance, TriggerSpec
from libs.providers.llm.base import (
    LLMProvider,
    ProviderError,
    StreamResult,
    provider_request_failed_message,
)
from libs.providers.llm.errors import ProviderConfigurationError
from libs.providers.llm.registry import make_provider
from libs.tools.activity import ActivityRef
from libs.tools.base import parse_qualified_tool_name
from libs.tools.context import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class LoopControl:
    """Mutable control state drained from the mailbox at checkpoints."""

    abort: bool = False
    pause: bool = False
    pending_inputs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMCallMetadata:
    """Best-effort provider metadata plus any collection fault."""

    model: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    latency_ms: int | None
    provider_error: ProviderError | None = None
    collection_fault: BaseException | None = None


class SessionRunner:
    def __init__(
        self,
        backend: SessionBackend,
        *,
        agent_id: uuid.UUID | None = None,
        emit_restart: bool = False,
        client_factories: dict[str, Callable[..., Any]] | None = None,
        _trigger_spec: TriggerSpec | None = None,
        _agent_daily_limit: Decimal | None = None,
        _agent_monthly_limit: Decimal | None = None,
        _user_daily_limit: Decimal | None = None,
        _user_monthly_limit: Decimal | None = None,
    ) -> None:
        """Create a runner for one session backend and initialize tool client wiring."""
        self.backend = backend
        self.hooks = HookRegistry()
        self._accounted_llm_costs: set[uuid.UUID] = set()
        self._terminal_activity_ids: set[uuid.UUID] = set()
        self.recorder = BackendActivityRecorder(
            backend,
            on_created=self._on_activity_created,
            on_updated=self._on_activity_updated,
            on_terminal=self._terminal_activity_ids.add,
        )
        self.config_spec: AgentConfigSpec = backend.get_spec()
        session = getattr(backend, 'session', None)
        user_id = self.backend.user_id

        def _make_supplier(cred_ref: str | None, cred_type: str) -> Callable[[], str | None]:
            """Wrap make_secret_supplier into the ToolContext factory signature."""
            return make_secret_supplier(user_id, name=cred_ref, type=cred_type)

        self.ctx = ToolContext(
            spec=self.config_spec,
            user_id=user_id,
            agent_id=getattr(session, 'agent_id', None),
            session_id=backend.session_id,
            secret_supplier_factory=_make_supplier,
            client_factories=client_factories or {},
            recorder=self.recorder,
        )
        self.bound_tools = build_bound_tools(self.config_spec.tools, ctx=self.ctx)
        self.control = LoopControl()
        self.emit_restart = emit_restart

        self._limit_checker = SessionLimitChecker(
            self.config_spec,
            trigger_spec=_trigger_spec,
            agent_id=agent_id,
            user_id=self.backend.user_id,
            agent_daily_limit=_agent_daily_limit,
            agent_monthly_limit=_agent_monthly_limit,
            user_daily_limit=_user_daily_limit,
            user_monthly_limit=_user_monthly_limit,
        )

    @classmethod
    def for_session(
        cls,
        session: AgentSession,
        *,
        emit_restart: bool = False,
        client_factories: dict[str, Callable[..., Any]] | None = None,
    ) -> SessionRunner:
        """Build a runner for a persisted Django session with optional tool client factories.

        Resolves agent-level spend limits, user-level SpendPolicy (with settings
        fallback), and trigger-level narrowing so the SessionLimitChecker can
        enforce rolling budgets and raise specific failure classes.
        """
        from apps.agents.models import SpendPolicy, Trigger

        agent = session.agent
        agent_id = agent.pk
        user_id = agent.user_id

        # Agent-level rolling limits
        agent_daily_limit = agent.daily_spend_limit_usd
        agent_monthly_limit = agent.monthly_spend_limit_usd

        # User-level rolling limits (SpendPolicy → settings fallback)
        user_daily_limit: Decimal | None = getattr(settings, 'DEFAULT_USER_DAILY_SPEND_LIMIT_USD', None)
        user_monthly_limit: Decimal | None = getattr(settings, 'DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD', None)
        try:
            policy = SpendPolicy.objects.get(user_id=user_id)
            if policy.daily_spend_limit_usd is not None:
                user_daily_limit = policy.daily_spend_limit_usd
            if policy.monthly_spend_limit_usd is not None:
                user_monthly_limit = policy.monthly_spend_limit_usd
        except SpendPolicy.DoesNotExist:
            pass

        # Trigger-level narrowing (max_iterations / max_cost_usd)
        trigger_spec: TriggerSpec | None = None
        if session.trigger_ref:
            try:
                trigger = Trigger.objects.get(pk=session.trigger_ref)
                trigger_spec = TriggerSpec(**trigger.spec)
            except Trigger.DoesNotExist:
                pass

        return cls(
            DjangoSessionBackend(session),
            agent_id=agent_id,
            emit_restart=emit_restart,
            client_factories=client_factories,
            _agent_daily_limit=agent_daily_limit,
            _agent_monthly_limit=agent_monthly_limit,
            _user_daily_limit=user_daily_limit,
            _user_monthly_limit=user_monthly_limit,
            _trigger_spec=trigger_spec,
        )

    def add_hook(self, hooks: HookSet) -> None:
        """Attach observability callbacks to this runner instance."""
        self.hooks.add(hooks)

    def run(self) -> None:
        """Run session turns until waiting, paused, aborted, done, or failed."""
        self.hooks.fire('on_run_start')
        try:
            if self.emit_restart:
                self._create_terminal_activity(
                    kind=AgentSessionActivityKind.RESTART,
                    status=AgentSessionActivityStatus.SUCCEEDED,
                    name='restart',
                    summary='Session restarted',
                    details={},
                )

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

                try:
                    self._limit_checker.check()
                except SessionFailure as exc:
                    self._record_failure(exc)
                    return

                llm_ref = self._start_activity(
                    kind=AgentSessionActivityKind.LLM,
                    name=self.config_spec.llm.model,
                    summary='generate',
                    details={},
                )
                try:
                    self.hooks.fire('on_generate_start', messages, tool_definitions)
                    result = provider.collect(messages, tool_definitions)
                except Exception as exc:  # pylint: disable=broad-except
                    self._fail_activity(
                        llm_ref.id,
                        summary='generate failed',
                        details={'message': 'Provider request failed', 'code': 'provider_runtime_failure'},
                    )
                    self._record_failure(exc)
                    return
                except BaseException as exc:
                    self._fail_activity_preserving_fault(
                        llm_ref.id,
                        exc,
                        summary='generate interrupted',
                        details={
                            'message': 'Provider collection interrupted',
                            'code': 'provider_interrupted',
                        },
                    )
                metadata = self._llm_metadata(provider, result)
                self._account_llm_cost_once(llm_ref.id, metadata.cost_usd)
                if metadata.collection_fault is not None and not isinstance(metadata.collection_fault, Exception):
                    self._fail_activity_preserving_fault(
                        llm_ref.id,
                        metadata.collection_fault,
                        summary='generate interrupted',
                        details={
                            'message': 'Provider metadata collection interrupted',
                            'code': 'provider_metadata_interrupted',
                        },
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )

                try:
                    self.hooks.fire('on_generate_end', result)
                except Exception as exc:  # pylint: disable=broad-except
                    self._fail_activity(
                        llm_ref.id,
                        summary='generate failed',
                        details={'message': 'Provider request failed', 'code': 'provider_runtime_failure'},
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                    self._record_failure(exc)
                    return
                except BaseException as exc:
                    self._fail_activity_preserving_fault(
                        llm_ref.id,
                        exc,
                        summary='generate interrupted',
                        details={
                            'message': 'Provider collection interrupted',
                            'code': 'provider_interrupted',
                        },
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                if metadata.provider_error:
                    self._fail_activity(
                        llm_ref.id,
                        summary='generate failed',
                        details={
                            'message': provider_request_failed_message(
                                status_code=metadata.provider_error.status_code,
                            ),
                            'code': metadata.provider_error.code,
                        },
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                    self._account_llm_cost_once(llm_ref.id, metadata.cost_usd)
                    self._record_provider_error(metadata.provider_error)
                    return

                try:
                    if metadata.collection_fault is not None:
                        raise metadata.collection_fault
                    self._emit_output(result, llm_id=llm_ref.id)
                    with self.recorder.push_parent(llm_ref.id):
                        for call in result.tool_calls:
                            self._handle_tool_call(call)
                    self._complete_activity(
                        llm_ref.id,
                        summary='generate',
                        details={},
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    self._fail_activity(
                        llm_ref.id,
                        summary='generate failed',
                        details={
                            'message': 'Provider response processing failed',
                            'code': 'provider_processing_failure',
                        },
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                    self._account_llm_cost_once(llm_ref.id, metadata.cost_usd)
                    self._record_failure(exc)
                    return
                except BaseException as exc:
                    self._fail_activity_preserving_fault(
                        llm_ref.id,
                        exc,
                        summary='generate interrupted',
                        details={
                            'message': 'Provider response processing interrupted',
                            'code': 'provider_processing_interrupted',
                        },
                        model=metadata.model,
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        cost_usd=metadata.cost_usd,
                        latency_ms=metadata.latency_ms,
                    )
                self._limit_checker.record_iteration()
                self._account_llm_cost_once(llm_ref.id, metadata.cost_usd)

                if result.tool_calls:
                    self._drain_mailbox()
                    continue

                self._set_status(AgentSessionStatus.WAITING)
                return

            self._set_status(AgentSessionStatus.DONE)
            self.backend.set_ended_at(timezone.now())
        finally:
            self.hooks.fire('on_run_end')

    def _record_provider_error(self, error: ProviderError) -> None:
        """Persist a provider runtime failure as a terminal session activity."""
        self._record_failure(session_failure_from_provider_runtime_error(error))

    def _record_failure(self, exc: Exception) -> None:
        """Persist a terminal session failure activity and move back to waiting."""
        if isinstance(exc, SessionFailure):
            message = exc.message
            payload: dict[str, Any] = {'message': message, 'code': exc.code}
            logger.info('Session %s failure: %s (%s)', self.backend.session_id, message, exc.code)
        else:
            message = 'Unexpected session failure'
            payload = {'message': message, 'code': 'unexpected_failure'}
            logger.exception('Session %s unexpected failure', self.backend.session_id)
        self._create_terminal_activity(
            kind=AgentSessionActivityKind.FAILURE,
            status=AgentSessionActivityStatus.FAILED,
            name='failure',
            summary=message[:120],
            details=payload,
        )
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

    def _emit_output(
        self,
        result: StreamResult,
        *,
        llm_id: uuid.UUID,
    ) -> None:
        """Record a usage-free output child for provider reconstruction.

        A turn that only requests tools carries no assistant text. Recording an
        empty output row would render as a blank message card on the session
        page, and provider reconstruction does not need it: rebuild synthesizes
        the assistant tool-call carrier message from the tool activity itself
        (see ``apps.sessions.rebuild._tool_messages``).

        A text-free turn with **no** tool calls still records its empty output.
        Nothing downstream would replace it, so dropping it would leave two
        consecutive user messages in the rebuilt history, which the Anthropic
        provider forwards to the API as-is.
        """
        if result.tool_calls and not result.content.strip():
            return
        with self.recorder.push_parent(llm_id):
            self._create_terminal_activity(
                kind=AgentSessionActivityKind.OUTPUT,
                status=AgentSessionActivityStatus.SUCCEEDED,
                name='output',
                summary=result.content[:120],
                details={'content': result.content},
                parent_id=llm_id,
            )

    def _handle_tool_call(self, call: dict[str, Any]) -> None:
        """Invoke one requested tool inside a single nested lifecycle activity."""
        wire_name = str(call.get('name', ''))
        arguments = call.get('arguments', {})
        call_id = call.get('id') or str(uuid.uuid4())
        self.hooks.fire('on_tool_call_start', call)

        try:
            instance_id, function_name = self._parse_tool_name(wire_name)
            parse_failure = False
        except (TypeError, ValueError):
            instance_id, function_name = '', wire_name
            parse_failure = True
        bound = self.bound_tools.get(instance_id)
        tool_type = bound.tool_type if bound is not None else None
        is_auto = bound is not None and bound.is_auto
        # Tool arguments are retained because the provider supplied them as
        # model-visible conversation state required for exact reconstruction.
        details = {
            'call_id': call_id,
            'instance_id': instance_id,
            'type': tool_type,
            'function': function_name,
            'arguments': arguments,
        }
        tool_ref = self._start_activity(
            kind=AgentSessionActivityKind.TOOL,
            name=wire_name or 'unknown',
            summary=wire_name or 'unknown tool',
            details=details,
        )
        started = time.monotonic()

        failed = True
        if parse_failure:
            result_content = json.dumps({'failure': 'Invalid tool name'})
        elif bound is None:
            result_content = json.dumps({'failure': f'Unknown tool instance {instance_id!r}'})
        elif not is_auto and not self._is_allowed(instance_id, function_name):
            result_content = json.dumps({'failure': f'Permission denied for {instance_id}.{function_name}'})
        else:
            try:
                with self.recorder.push_parent(tool_ref.id):
                    raw = bound.invoke(function_name, arguments)
                    result_content = raw if isinstance(raw, str) else json.dumps(raw)
                # Successful and curated bound-tool failure results are already
                # model-visible contract output, so retain their exact serialization.
                failed = self._tool_result_failed(result_content)
            except Exception:  # pylint: disable=broad-except
                result_content = self._unexpected_tool_failure_result()
            except BaseException:
                tool_latency_ms = int((time.monotonic() - started) * 1000)
                interrupted_result = json.dumps(
                    {
                        'ok': False,
                        'error': {
                            'code': 'tool_interrupted',
                            'message': 'Tool execution interrupted',
                        },
                    }
                )
                self._fail_activity(
                    tool_ref.id,
                    summary=f'{wire_name or "tool"} interrupted',
                    details={**details, 'result': interrupted_result},
                    latency_ms=tool_latency_ms,
                )
                raise
        tool_latency_ms = int((time.monotonic() - started) * 1000)

        # This exact serialized result is provider conversation state; tools are
        # responsible for returning only contract-safe model-visible values.
        completed_details = {**details, 'result': result_content}
        try:
            self.hooks.fire('on_tool_call_end', call, result_content)
        except BaseException as fault:
            self._fail_activity_preserving_fault(
                tool_ref.id,
                fault,
                summary=f'{wire_name or "tool"} observation interrupted',
                details=completed_details,
                latency_ms=tool_latency_ms,
            )
        if failed:
            self._fail_activity(
                tool_ref.id,
                summary=f'{wire_name or "tool"} failed',
                details=completed_details,
                latency_ms=tool_latency_ms,
            )
        else:
            self._complete_activity(
                tool_ref.id,
                summary=f'{wire_name} completed',
                details=completed_details,
                latency_ms=tool_latency_ms,
            )

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

    @staticmethod
    def _tool_result_failed(content: str) -> bool:
        """Recognize the runner's uniform structured tool failure envelope."""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict) and ('failure' in parsed or parsed.get('ok') is False)

    @staticmethod
    def _unexpected_tool_failure_result() -> str:
        """Return stable model-visible output for an unexpected tool failure."""
        return json.dumps(
            {
                'ok': False,
                'error': {
                    'code': 'tool_execution_failed',
                    'message': 'Tool execution failed',
                },
            }
        )

    def _llm_metadata(
        self,
        provider: LLMProvider,
        result: StreamResult,
    ) -> LLMCallMetadata:
        """Collect provider metadata without letting instrumentation strand an LLM."""
        model = self.config_spec.llm.model
        input_tokens: int | None = None
        output_tokens: int | None = None
        cost: Decimal | None = None
        latency_ms: int | None = None
        provider_error: ProviderError | None = None
        collection_fault: BaseException | None = None
        try:
            latency_ms = result.latency_ms
            usage = result.usage
            if usage is not None:
                model = usage.model
                input_tokens = usage.input_tokens
                output_tokens = usage.output_tokens
                try:
                    cost = provider.compute_cost_usd(usage, latency_ms=latency_ms)
                except BaseException as exc:
                    collection_fault = exc
            provider_error = result.error
        except BaseException as exc:
            collection_fault = exc
        return LLMCallMetadata(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            provider_error=provider_error,
            collection_fault=collection_fault,
        )

    def _account_llm_cost_once(self, activity_id: uuid.UUID, cost_usd: Decimal | None) -> None:
        """Account one known provider cost at most once per LLM activity."""
        if cost_usd is None or activity_id in self._accounted_llm_costs:
            return
        self._accounted_llm_costs.add(activity_id)
        try:
            self._limit_checker.record_cost(cost_usd)
        except Exception:  # pylint: disable=broad-except
            logger.debug('Failed provider cost accounting', exc_info=True)

    def _create_terminal_activity(
        self,
        *,
        kind: str,
        status: str,
        name: str,
        summary: str,
        details: dict[str, Any],
        parent_id: uuid.UUID | None = None,
    ) -> RecordedActivity:
        """Create one terminal activity, then notify canonical create observers."""
        activity = self.backend.create_activity(
            kind=kind,
            status=status,
            name=name,
            summary=summary,
            details=details,
            parent_id=parent_id,
        )
        self._terminal_activity_ids.add(activity.id)
        self._on_activity_created(activity)
        return activity

    def _start_activity(
        self,
        *,
        kind: str,
        name: str,
        summary: str,
        details: dict[str, Any],
    ) -> ActivityRef:
        """Start a recorder lifecycle and notify after durable creation."""
        return self.recorder.start(kind=kind, name=name, summary=summary, details=details)

    def _complete_activity(self, activity_id: uuid.UUID, **kwargs: Any) -> ActivityRef:
        """Complete a recorder lifecycle and notify after durable revision."""
        return self._persist_completed_activity(activity_id, **kwargs)

    def _persist_completed_activity(self, activity_id: uuid.UUID, **kwargs: Any) -> ActivityRef:
        """Persist success and remember it before any update hook can interrupt."""
        ref = self.recorder.complete(activity_id, **kwargs)
        self._terminal_activity_ids.add(activity_id)
        return ref

    def _fail_activity(
        self,
        activity_id: uuid.UUID,
        *,
        summary: str,
        details: dict[str, Any],
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
    ) -> ActivityRef:
        """Fail a recorder lifecycle and notify after durable revision."""
        return self._persist_failed_activity(
            activity_id,
            summary=summary,
            details=details,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    def _persist_failed_activity(self, activity_id: uuid.UUID, **kwargs: Any) -> ActivityRef:
        """Persist failure and remember it before any update hook can interrupt."""
        ref = self.recorder.fail(activity_id, **kwargs)
        self._terminal_activity_ids.add(activity_id)
        return ref

    def _fail_activity_preserving_fault(
        self,
        activity_id: uuid.UUID,
        fault: BaseException,
        **kwargs: Any,
    ) -> NoReturn:
        """Terminalize if needed, then re-raise the original base-level fault."""
        if activity_id not in self._terminal_activity_ids:
            try:
                self._fail_activity(activity_id, **kwargs)
            except BaseException:
                if activity_id not in self._terminal_activity_ids:
                    raise
        raise fault.with_traceback(fault.__traceback__) from None

    def _on_activity_created(self, activity: RecordedActivity) -> None:
        """Send one canonical post-persistence create snapshot to observers."""
        self.hooks.fire(
            'on_activity_created',
            activity,
            on_base_exception=lambda fault: self._compensate_created_activity(activity, fault),
        )

    def _compensate_created_activity(
        self,
        activity: RecordedActivity,
        fault: BaseException,
    ) -> None:
        """Fail the running source whose queued create callback was cancelled."""
        del fault  # Exception text is intentionally excluded from persisted details.
        if (
            activity.status
            in {
                AgentSessionActivityStatus.SUCCEEDED,
                AgentSessionActivityStatus.FAILED,
                AgentSessionActivityStatus.CANCELLED,
            }
            or activity.id in self._terminal_activity_ids
        ):
            return
        self._persist_failed_activity(
            activity.id,
            summary=f'{activity.name} observation interrupted',
            details={
                'message': 'Activity instrumentation interrupted',
                'code': 'activity_instrumentation_interrupted',
            },
        )

    def _on_activity_updated(self, activity: RecordedActivity) -> None:
        """Send one canonical post-persistence revision snapshot to observers."""
        self.hooks.fire('on_activity_updated', activity)

    def _record_input(self, content: str) -> RecordedActivity:
        """Record user input through the backend, then notify create observers."""
        activity = self.backend.record_input(content)
        self._terminal_activity_ids.add(activity.id)
        self._on_activity_created(activity)
        return activity

    def _set_status(self, status: str) -> None:
        """Set the backend status and notify status hooks."""
        self.backend.set_status(status)
        self.hooks.fire('on_status', status)

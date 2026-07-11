# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Inbox eval suite and sample runner."""

# pylint: disable=import-error,trailing-newlines,wrong-import-position

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2] / 'backend'
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import django
from django.apps import apps as django_apps

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chief.settings')
os.environ.setdefault('ENV_PATH', '.')
if not django_apps.ready:
    django.setup()

from apps.runner.usecases.scenarios import (
    UsecaseScenario,
    build_mock_client_factories,
    load_usecase_scenario,
)
from apps.runner.usecases.setup import build_memory_session_runner
from apps.sessions.models import AgentSessionEventKind

# isort: split

from libs.agent_spec import AgentConfigSpec, LLMSpec, load_spec
from libs.agent_specs import load_example
from libs.providers.llm.errors import UnsupportedLLMProvider
from libs.providers.llm.registry import make_provider
from libs.providers.llm.types import ProviderLLMConfig

from evals.inbox.scorers import score_inbox_state
from olib.py.eval import EventLogWriter, RunPartition, Sample, Score

SPEC_KEYS = {'schema_version', 'description', 'llm', 'system_prompt', 'triggers', 'tools', 'queues'}


class InboxSampleRunner:
    """Run one inbox scenario through the memory SessionRunner and score mock state."""

    def __init__(self, *, log_root: str | Path = '.output/usecase-logs') -> None:
        """Create a sample runner with a partitioned event log writer."""
        self.log_writer = EventLogWriter(log_root)

    def run_sample(self, sample: Sample, *, model: str, partition: RunPartition) -> Score:
        """Run one sample/model cell with seeded mocks and return the final-state score."""
        scenario = _scenario_from_payload(sample.payload)
        provider_config = _provider_config_from_model_string(model)
        provider = _make_checked_provider(provider_config)
        client_factories, gmail, clickup = build_mock_client_factories(scenario)
        spec = _spec_for_scenario(scenario, provider_config)

        backend, runner = build_memory_session_runner(
            spec=spec,
            client_factories=client_factories,
            partition=partition,
            log_writer=self.log_writer,
            prompt=scenario.prompt,
        )

        with patch('apps.runner.loop.make_provider', return_value=provider):
            runner.run()

        _raise_for_missing_credentials(backend.events())
        return score_inbox_state(
            expect=scenario.expect,
            gmail=gmail,
            clickup=clickup,
            tool_calls=_tool_calls_from_events(backend.events()),
        )


def get_sample_runner() -> InboxSampleRunner:
    """Return the configured inbox sample runner."""
    return InboxSampleRunner()


def _scenario_from_payload(payload: Mapping[str, Any]) -> UsecaseScenario:
    """Load a scenario from a sample payload path or embedded YAML mapping."""
    if 'path' in payload:
        return load_usecase_scenario(Path(str(payload['path'])))

    raw = payload.get('scenario', payload)
    if not isinstance(raw, Mapping):
        raise ValueError('inbox sample payload must be a scenario mapping or contain path')
    return _scenario_from_raw(dict(raw))


def _scenario_from_raw(raw: dict[str, Any]) -> UsecaseScenario:
    """Parse an embedded scenario mapping using the functional scenario contract."""
    scenario_id = raw.get('id')
    prompt = raw.get('prompt')
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError('inbox scenario requires non-empty string id')
    if not isinstance(prompt, str) or not prompt:
        raise ValueError('inbox scenario requires non-empty string prompt')
    return UsecaseScenario(
        id=scenario_id,
        prompt=prompt,
        seed_gmail=raw.get('seed_gmail') or [],
        seed_clickup=raw.get('seed_clickup') or {},
        fake_responses=list(raw.get('fake_responses') or []),
        expect=dict(raw.get('expect') or {}),
        raw=raw,
    )


def _provider_config_from_model_string(model: str) -> ProviderLLMConfig:
    """Parse locked eval model strings of the form provider/model."""
    provider, sep, model_name = model.partition('/')
    if not sep or not provider or not model_name:
        raise RuntimeError(f"Eval model must use locked 'provider/model' format, got {model!r}")
    return ProviderLLMConfig(provider=provider, model=model_name)


def _make_checked_provider(config: ProviderLLMConfig) -> Any:
    """Build a provider and fail early with clear messages for missing env credentials."""
    _check_env_credentials(config.provider)
    try:
        return make_provider(config)
    except UnsupportedLLMProvider as exc:
        raise RuntimeError(f"Unsupported eval provider '{config.provider}' in model string") from exc


def _check_env_credentials(provider: str) -> None:
    """Raise a clear RuntimeError when env credentials required by live providers are absent."""
    required_env = {
        'anthropic': 'ANTHROPIC_API_KEY',
        'openai': 'OPENAI_API_KEY',
    }.get(provider)
    if required_env and not os.environ.get(required_env):
        raise RuntimeError(
            f"Missing credentials for eval provider '{provider}': set {required_env} or rerun with --allow-skip",
        )


def _spec_for_scenario(scenario: UsecaseScenario, provider_config: ProviderLLMConfig) -> AgentConfigSpec:
    """Build the agent spec for a scenario and override its LLM from the eval model."""
    spec = _base_spec_for_scenario(scenario)
    llm = LLMSpec(
        provider=provider_config.provider, model=provider_config.model, temperature=provider_config.temperature
    )
    return spec.model_copy(update={'llm': llm})


def _base_spec_for_scenario(scenario: UsecaseScenario) -> AgentConfigSpec:
    """Load a scenario-provided spec or fall back to the inbox triage example."""
    if SPEC_KEYS.issubset(scenario.raw) or {'llm', 'system_prompt', 'tools'}.issubset(scenario.raw):
        spec_raw = {key: scenario.raw[key] for key in SPEC_KEYS if key in scenario.raw}
        return load_spec(spec_raw)

    spec = load_example('inbox-triage-usecase')
    system_prompt = scenario.raw.get('system_prompt')
    if isinstance(system_prompt, str) and system_prompt.strip():
        spec = spec.model_copy(update={'system_prompt': system_prompt})
    return _permit_inbox_eval_actions(spec)


def _permit_inbox_eval_actions(spec: AgentConfigSpec) -> AgentConfigSpec:
    """Ensure the fallback inbox spec allows the Gmail actions eval scenarios need."""
    tools = []
    for tool in spec.tools:
        if tool.id == 'gmail':
            allow = list(dict.fromkeys([*tool.allow, 'list_labels', 'mark_spam', 'trash']))
            tools.append(tool.model_copy(update={'allow': allow}))
        else:
            tools.append(tool)
    return spec.model_copy(update={'tools': tools})


def _tool_calls_from_events(events: list[Any]) -> list[str]:
    """Extract qualified tool-call names from runner events in execution order."""
    return [
        f"{event.payload['instance_id']}__{event.payload['function']}"
        for event in events
        if event.kind == AgentSessionEventKind.TOOL_CALL
    ]


def _raise_for_missing_credentials(events: list[Any]) -> None:
    """Translate runner-recorded provider credential failures into eval infrastructure misses."""
    for event in events:
        if event.kind != AgentSessionEventKind.FAILURE:
            continue
        code = str(event.payload.get('code', ''))
        if code.startswith('missing_') and code.endswith('_credentials'):
            message = str(event.payload.get('message') or 'missing provider credentials')
            raise RuntimeError(
                f'{message}; set the provider API key or rerun with --allow-skip',
            )

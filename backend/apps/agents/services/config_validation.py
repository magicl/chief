# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Validate raw agent config YAML before persist."""

from __future__ import annotations

from dataclasses import dataclass

import yaml
from apps.agents.ingest import IngestError, validate_spec_tools
from apps.runner.spec_loader import load_agent_config_spec
from libs.agent_spec import AgentConfigSpec
from libs.agent_spec.exceptions import UnsupportedSpecVersionError
from libs.sources.registry import get_adapter
from pydantic import ValidationError


@dataclass(frozen=True, slots=True)
class ValidationErrorItem:
    path: str
    message: str
    line: int | None = None


class ConfigValidationError(Exception):
    """Raised when YAML/spec validation fails; carries structured errors for the UI."""

    def __init__(self, errors: list[ValidationErrorItem]) -> None:
        self.errors = errors
        super().__init__(errors[0].message if errors else 'validation failed')


def _yaml_error_line(exc: yaml.YAMLError) -> int | None:
    """Extract a 1-based line number from a PyYAML error, when available."""
    mark = getattr(exc, 'problem_mark', None)
    if mark is None:
        return None
    return int(mark.line) + 1


def _pydantic_errors(exc: ValidationError) -> list[ValidationErrorItem]:
    """Convert a Pydantic ``ValidationError`` into structured UI error items."""
    items: list[ValidationErrorItem] = []
    for err in exc.errors():
        loc = '.'.join(str(part) for part in err.get('loc', ()))
        items.append(ValidationErrorItem(path=loc, message=err.get('msg', 'invalid')))
    return items


def _validate_queue_sources(spec: AgentConfigSpec) -> list[ValidationErrorItem]:
    """Validate each queue source adapter config; return errors without raising."""
    items: list[ValidationErrorItem] = []
    for queue in spec.queues:
        for source in queue.sources:
            path = f'queues.{queue.id}.sources.{source.id}.config'
            adapter = get_adapter(source.adapter_type)
            if adapter is None:
                items.append(
                    ValidationErrorItem(
                        path=f'queues.{queue.id}.sources.{source.id}.type',
                        message=f'Unknown source adapter {source.adapter_type!r}',
                    ),
                )
                continue
            try:
                adapter.validate_config(source.config)
            except ValueError as exc:
                items.append(ValidationErrorItem(path=path, message=str(exc)))
    return items


def validate_agent_config_spec(spec: AgentConfigSpec) -> AgentConfigSpec:
    """Validate an in-memory spec (tools + queue adapters)."""
    try:
        validate_spec_tools(spec)
    except IngestError as exc:
        raise ConfigValidationError([ValidationErrorItem(path='tools', message=str(exc))]) from exc
    errors = _validate_queue_sources(spec)
    if errors:
        raise ConfigValidationError(errors)
    return spec


def validate_agent_config_yaml(raw: str) -> AgentConfigSpec:
    """Parse and fully validate *raw* YAML; raise ``ConfigValidationError`` on failure."""
    try:
        spec = load_agent_config_spec(raw)
    except UnsupportedSpecVersionError as exc:
        raise ConfigValidationError(
            [ValidationErrorItem(path='', message=str(exc))],
        ) from exc
    except ValidationError as exc:
        raise ConfigValidationError(_pydantic_errors(exc)) from exc
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            [ValidationErrorItem(path='', message=str(exc), line=_yaml_error_line(exc))],
        ) from exc
    except ValueError as exc:
        raise ConfigValidationError([ValidationErrorItem(path='', message=str(exc))]) from exc
    return validate_agent_config_spec(spec)

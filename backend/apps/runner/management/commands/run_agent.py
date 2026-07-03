# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Run a single agent turn headless (no Celery / Redis / DB)."""

from __future__ import annotations

import argparse
from typing import Any

from apps.runner.run_agent import run_agent_from_options
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Run one agent turn locally and print session events to the console.'

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument('input', help='User message for the agent')
        parser.add_argument('--provider', help='LLM provider name (e.g. openai, anthropic, local_openai, repeat)')
        parser.add_argument('--model', help='Model name for the provider')
        parser.add_argument('--temperature', type=float, help='Sampling temperature')
        parser.add_argument('--system-prompt', help='System prompt when using --provider/--model')
        parser.add_argument('--spec', help='Full AgentConfigSpec as JSON or YAML string')
        parser.add_argument('--spec-file', help='Path to AgentConfigSpec JSON or YAML file')
        parser.add_argument(
            '--user-id',
            type=int,
            default=None,
            help='Resolve encrypted credentials for this user (default: env-only, no DB lookup)',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            run_agent_from_options(options, stream=self.stdout)
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc

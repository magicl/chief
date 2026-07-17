# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Tests for ToolContext credential helpers."""

from __future__ import annotations

from collections.abc import Callable

from libs.agent_spec import AgentConfigSpec, LLMSpec
from libs.tools.context import ToolContext, token_supplier_for

from olib.py.django.test.cases import OTestCase


class TestTokenSupplierFor(OTestCase):
    def test_noop_when_tool_has_no_credential_type(self) -> None:
        """Tools without credential_type never call the secret factory."""
        calls: list[tuple[str | None, str]] = []

        def factory(ref: str | None, typ: str) -> Callable[[], str | None]:
            calls.append((ref, typ))
            return lambda: 'secret'

        ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=factory,
        )
        supplier = token_supplier_for(ctx, credential_type=None, credential_ref='named')
        self.assertIsNone(supplier())
        self.assertEqual(calls, [])

    def test_uses_credential_type_even_without_ref(self) -> None:
        """Default secret resolution keys off credential_type alone."""
        ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: f'{ref}:{typ}',
        )
        supplier = token_supplier_for(ctx, credential_type='clickup')
        self.assertEqual(supplier(), 'None:clickup')

    def test_passes_credential_ref_with_type(self) -> None:
        """Named refs are passed through; type still comes from the tool."""
        ctx = ToolContext(
            spec=AgentConfigSpec(llm=LLMSpec(provider='_', model='_'), system_prompt='_'),
            user_id=1,
            secret_supplier_factory=lambda ref, typ: lambda: f'{ref}:{typ}',
        )
        supplier = token_supplier_for(ctx, credential_type='gmail', credential_ref='gmail-personal')
        self.assertEqual(supplier(), 'gmail-personal:gmail')

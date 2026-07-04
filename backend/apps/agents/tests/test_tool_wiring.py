# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from apps.agents.spec import ToolInstance
from apps.agents.tool_wiring import build_bound_tools
from libs.tools.base import Tool, ToolFunction
from libs.tools.registry import register_tool

from olib.py.django.test.cases import OTestCase


class _EchoCredTool(Tool):
    name = 'echo_cred'
    credential_type = 'gmail'

    def functions(self) -> list[ToolFunction]:
        return [
            ToolFunction(
                name='ping',
                description='x',
                parameters={'type': 'object', 'properties': {}},
                handler=self._ping,
            ),
        ]

    def bind(
        self,
        *,
        token_supplier: Callable[[], str | None],
    ) -> Callable[[str, dict[str, Any]], Any]:
        def invoke(function: str, arguments: dict[str, Any]) -> Any:
            if function != 'ping':
                raise ValueError(function)
            token = token_supplier()
            return {'token_set': token is not None}

        return invoke

    @staticmethod
    def _ping(**_kwargs: Any) -> str:
        return 'ok'


class TestBuildBoundTools(OTestCase):
    def setUp(self) -> None:
        register_tool('echo_cred', _EchoCredTool())

    def test_clock_instance_invokes_without_credentials(self) -> None:
        instances = [ToolInstance(id='clock', type='clock', allow=['now'])]
        bound = build_bound_tools(instances, user_id=1)
        self.assertIn('clock', bound)
        result = bound['clock'].invoke('now', {})
        self.assertIsInstance(result, str)

    def test_credential_tool_uses_supplier(self) -> None:
        instances = [ToolInstance(id='gmail-a', type='echo_cred', allow=['ping'])]
        with patch('apps.agents.tool_wiring.make_secret_supplier', return_value=lambda: 'tok'):
            bound = build_bound_tools(instances, user_id=1)
        out = bound['gmail-a'].invoke('ping', {})
        self.assertEqual(out, {'token_set': True})

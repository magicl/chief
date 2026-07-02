# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from libs.tools.registry import get_tool

from olib.py.django.test.cases import OTestCase


class TestToolsWiring(OTestCase):
    def test_clock_tool_registered(self) -> None:
        tool = get_tool('clock')
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool.name, 'clock')

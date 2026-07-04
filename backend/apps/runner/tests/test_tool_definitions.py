# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.agents.spec import ToolInstance
from apps.runner.tool_definitions import build_tool_definitions

from olib.py.django.test.cases import OTestCase


class TestBuildToolDefinitions(OTestCase):
    def test_two_instances_same_type_get_distinct_wire_names(self) -> None:
        instances = [
            ToolInstance(id='clock-a', type='clock', allow=['now']),
            ToolInstance(id='clock-b', type='clock', allow=['now']),
        ]
        defs = build_tool_definitions(instances, is_allowed=lambda *_a, **_k: True)
        names = {d.name for d in defs}
        self.assertEqual(names, {'clock-a__now', 'clock-b__now'})

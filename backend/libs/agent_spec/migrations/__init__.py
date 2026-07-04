# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import importlib

# Python module names cannot start with a digit; re-export as ``tool_instances`` for tests/imports.
tool_instances = importlib.import_module('.001_tool_instances', __name__)

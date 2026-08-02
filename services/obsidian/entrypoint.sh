#!/bin/bash
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Entrypoint for the Obsidian vault service container.

set -euo pipefail

exec uvicorn obsidian_vault.main:app --host 0.0.0.0 --port "$PORT"

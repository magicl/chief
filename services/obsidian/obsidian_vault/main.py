# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Process entrypoint wiring real collaborators for the vault service.

Reads `OBSIDIAN_VAULT_TOKEN` (inter-service bearer token) and
`OBSIDIAN_VAULT_DATA` (working-tree/state data dir) from the environment,
wires the real `ObsidianHeadlessSupervisor` + `VaultBindingStore` +
`VaultFileService`, and exposes the resulting `app` for
`uvicorn obsidian_vault.main:app`. Import-time env lookups are intentional:
this module is only imported by the ASGI server process, never by tests
(which call `create_app` directly with fakes). Fails fast (raises at import
time, before uvicorn binds a port) if the inter-service token is unset or
blank, since an empty token would otherwise silently disable auth.
"""

from __future__ import annotations

import os
from pathlib import Path

from obsidian_vault.app import create_app
from obsidian_vault.bindings import VaultBindingStore
from obsidian_vault.files import VaultFileService
from obsidian_vault.supervisor import ObsidianHeadlessSupervisor

OBSIDIAN_VAULT_TOKEN = os.environ.get('OBSIDIAN_VAULT_TOKEN', '')
if not OBSIDIAN_VAULT_TOKEN:
    raise RuntimeError(
        'OBSIDIAN_VAULT_TOKEN must be set to a non-empty inter-service token; '
        'refusing to start with auth effectively disabled'
    )

OBSIDIAN_VAULT_DATA = Path(os.environ.get('OBSIDIAN_VAULT_DATA', '/data'))

_store = VaultBindingStore()
_supervisor = ObsidianHeadlessSupervisor(OBSIDIAN_VAULT_DATA)
_files = VaultFileService(_store, vault_root_for=_supervisor.vault_dir)

app = create_app(token=OBSIDIAN_VAULT_TOKEN, store=_store, files=_files, supervisor=_supervisor)

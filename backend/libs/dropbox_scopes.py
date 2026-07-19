# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Canonical Django-free Dropbox OAuth scope identifiers."""

FILES_METADATA_READ_SCOPE = 'files.metadata.read'

# Stable provider order is shared by the capability catalog and client allowlist.
DROPBOX_OAUTH_SCOPE_VALUES = (FILES_METADATA_READ_SCOPE,)
DROPBOX_OAUTH_SCOPES = frozenset(DROPBOX_OAUTH_SCOPE_VALUES)

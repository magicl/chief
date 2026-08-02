# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""URL routes for the Obsidian app (internal vault-service API)."""

from __future__ import annotations

from apps.obsidian import views
from django.urls import path

urlpatterns = [
    path(
        'internal/obsidian/vault-bindings/',
        views.vault_bindings_snapshot,
        name='obsidian_vault_bindings_snapshot',
    ),
]

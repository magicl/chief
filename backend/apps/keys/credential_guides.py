# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Operator instructions for adding user credentials in the settings UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.keys.types import SERVICE_TYPES


@dataclass(frozen=True, slots=True)
class CredentialGuide:
    """Setup steps shown after the user picks a credential type."""

    label: str
    find_steps: tuple[str, ...]
    scopes: str | None
    input_label: str
    input_placeholder: str


_GOOGLE_SCOPES = (
    'https://www.googleapis.com/auth/gmail.modify,'
    'https://www.googleapis.com/auth/gmail.send,'
    'https://www.googleapis.com/auth/drive.metadata.readonly'
)

_GUIDES: dict[str, CredentialGuide] = {
    'openai': CredentialGuide(
        label='OpenAI',
        find_steps=(
            'Sign in at https://platform.openai.com/api-keys.',
            'Create an API key (or reuse an existing project key).',
            'Copy the key — it is shown only once at creation.',
        ),
        scopes=None,
        input_label='API key',
        input_placeholder='sk-…',
    ),
    'anthropic': CredentialGuide(
        label='Anthropic',
        find_steps=(
            'Sign in at https://console.anthropic.com/settings/keys.',
            'Create an API key for your workspace.',
            'Copy the key — it is shown only once at creation.',
        ),
        scopes=None,
        input_label='API key',
        input_placeholder='sk-ant-…',
    ),
    'local_openai': CredentialGuide(
        label='Local OpenAI-compatible',
        find_steps=(
            'Use the API key configured on your local OpenAI-compatible server (vLLM, LiteLLM proxy, etc.).',
            'If the server has no auth, use any non-empty placeholder string.',
        ),
        scopes=None,
        input_label='API key',
        input_placeholder='local-openai-key',
    ),
    'google': CredentialGuide(
        label='Google (service account)',
        find_steps=(
            'In Google Cloud Console, create a service account and enable the Gmail API and/or Google Drive API '
            'as needed for the enabled tools.',
            'Create a JSON key for that service account and download it.',
            'Domain-wide delegation is required when Gmail is enabled and whenever Drive impersonates a Google '
            'Workspace user. It is unnecessary only for non-delegated Drive access using the service-account identity.',
            'For domain-wide delegation, use Google Workspace Admin → Security → API controls to authorize '
            'only the union of scopes required by the enabled tools: Gmail scopes only when Gmail is enabled, '
            'and the Drive scope only when Drive is enabled.',
            'Paste the full JSON service-account key below (not just the private key).',
        ),
        scopes=_GOOGLE_SCOPES,
        input_label='Service account JSON',
        input_placeholder='{"type": "service_account", "project_id": "…", …}',
    ),
    'dropbox': CredentialGuide(
        label='Dropbox',
        find_steps=(
            'Static JSON: create a Dropbox API app with the files.metadata.read scope, provision an offline '
            'refresh token externally, and paste the app key, app secret, and refresh token JSON below.',
            'OAuth: save this declaration with the Dropbox metadata capability selected, then use '
            'Authenticate on the Keys page to complete consent — Chief stores the resulting grant.',
        ),
        scopes='files.metadata.read',
        input_label='Dropbox app credential JSON',
        input_placeholder='{"app_key": "…", "app_secret": "…", "refresh_token": "…"}',
    ),
    'clickup': CredentialGuide(
        label='ClickUp',
        find_steps=(
            'In ClickUp, open Settings → Apps (or your profile) → API Token.',
            'Generate a personal API token.',
            'Copy the token — ClickUp shows it once when generated.',
        ),
        scopes='Personal token — no OAuth scopes to configure in ClickUp.',
        input_label='Personal API token',
        input_placeholder='pk_…',
    ),
    'obsidian': CredentialGuide(
        label='Obsidian Sync',
        find_steps=(
            'The Sync auth token is not shown anywhere in the Obsidian apps — the only way to get it is to log '
            'in once with the Obsidian Headless CLI and read the token it stores on disk. You need an active '
            'Obsidian Sync subscription and Node 22+ on the machine you do this from (it can be your laptop; it '
            'does not have to be the server running Chief).',
            'Install the CLI: npm install -g obsidian-headless',
            'Log in as the account that owns the vault: ob login — it prompts for email, password, and 2FA if '
            'enabled. Running ob login again afterwards just prints the logged-in account.',
            'Print the stored token: cat ~/.config/obsidian-headless/auth_token (Linux / XDG_CONFIG_HOME). On '
            'macOS and Windows it is usually ~/.obsidian-headless/auth_token instead. Copy the whole file '
            'contents as auth_token below.',
            'Do not run ob logout until you have copied the token: logout deletes the stored credentials on '
            'that machine. If you need a new token later, log in again and paste a fresh copy into Chief.',
            'While logged in, run ob sync-list-remote to see your remote vaults — the id or name listed there is '
            'what you put in the obsidian tool config (vault field) on the agent.',
            'If the vault uses end-to-end encryption, also note its encryption password; Chief needs it to read '
            'the vault. Vaults using standard (managed) encryption do not need one.',
            'Paste the JSON below with auth_token (required) and encryption_password (optional, only for '
            'E2E-encrypted vaults). Full CLI reference: https://obsidian.md/help/sync/headless',
        ),
        scopes=None,
        input_label='Obsidian Sync credential JSON',
        input_placeholder='{"auth_token": "…", "encryption_password": "…optional…"}',
    ),
}


def credential_guide(type_name: str) -> CredentialGuide | None:
    """Return setup instructions for *type_name*, or ``None`` if unknown."""
    return _GUIDES.get(type_name)


def credential_guides_for_ui() -> dict[str, dict[str, Any]]:
    """Serialize guides for every registered service type (for Alpine/JSON in templates)."""
    out: dict[str, dict[str, Any]] = {}
    for type_name in sorted(SERVICE_TYPES):
        guide = _GUIDES.get(type_name)
        if guide is None:
            out[type_name] = {
                'label': type_name,
                'find_steps': [f"Obtain a credential for type {type_name!r}."],
                'scopes': None,
                'input_label': 'Value',
                'input_placeholder': '',
            }
            continue
        out[type_name] = {
            'label': guide.label,
            'find_steps': list(guide.find_steps),
            'scopes': guide.scopes,
            'input_label': guide.input_label,
            'input_placeholder': guide.input_placeholder,
        }
    return out

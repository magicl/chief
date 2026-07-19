# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Canonical Django-free Google OAuth scope URLs."""

GMAIL_READONLY_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
GMAIL_MODIFY_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'
GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
DRIVE_METADATA_READONLY_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly'
DRIVE_READONLY_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
DOCUMENTS_READONLY_SCOPE = 'https://www.googleapis.com/auth/documents.readonly'
DOCUMENTS_SCOPE = 'https://www.googleapis.com/auth/documents'
SPREADSHEETS_READONLY_SCOPE = 'https://www.googleapis.com/auth/spreadsheets.readonly'
SPREADSHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'

# Stable provider order is shared by the capability catalog and contract tests.
GOOGLE_OAUTH_SCOPE_VALUES = (
    GMAIL_READONLY_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    DRIVE_METADATA_READONLY_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE,
    DRIVE_SCOPE,
    DOCUMENTS_READONLY_SCOPE,
    DOCUMENTS_SCOPE,
    SPREADSHEETS_READONLY_SCOPE,
    SPREADSHEETS_SCOPE,
)
GOOGLE_OAUTH_SCOPES = frozenset(GOOGLE_OAUTH_SCOPE_VALUES)

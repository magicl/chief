# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.exceptions import KeyValidationError
from apps.keys.types import (
    EXTERNAL_SERVICE_TYPES,
    LLM_ENV_FALLBACK,
    SERVICE_TYPES,
    is_registered_type,
    validate_type,
)

from olib.py.django.test.cases import OTransactionTestCase


class TestServiceTypes(OTransactionTestCase):
    def test_registry_contains_canonical_types(self) -> None:
        self.assertEqual(
            SERVICE_TYPES,
            frozenset({'openai', 'anthropic', 'local_openai', 'google', 'dropbox', 'clickup', 'obsidian'}),
        )
        self.assertEqual(EXTERNAL_SERVICE_TYPES, frozenset({'google', 'dropbox', 'clickup', 'obsidian'}))

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(KeyValidationError):
            validate_type('not-a-service')

    def test_gmail_type_gives_rename_guidance(self) -> None:
        with self.assertRaisesMessage(
            KeyValidationError,
            "credential type 'gmail' was renamed to 'google'; update type: google",
        ):
            validate_type('gmail')
        self.assertFalse(is_registered_type('gmail'))

    def test_llm_env_fallback_map(self) -> None:
        self.assertEqual(LLM_ENV_FALLBACK['openai'], 'OPENAI_API_KEY')

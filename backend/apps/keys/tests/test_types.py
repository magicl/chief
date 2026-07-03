# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.keys.exceptions import KeyValidationError
from apps.keys.types import LLM_ENV_FALLBACK, is_registered_type, validate_type

from olib.py.django.test.cases import OTransactionTestCase


class TestServiceTypes(OTransactionTestCase):
    def test_openai_is_registered(self) -> None:
        self.assertTrue(is_registered_type('openai'))

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(KeyValidationError):
            validate_type('not-a-service')

    def test_llm_env_fallback_map(self) -> None:
        self.assertEqual(LLM_ENV_FALLBACK['openai'], 'OPENAI_API_KEY')

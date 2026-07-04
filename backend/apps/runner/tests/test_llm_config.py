# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging
import os
from unittest.mock import MagicMock, patch

from apps.agents.hardcoded import HARDCODED_SPEC
from apps.keys.services import commands
from apps.runner.backends.memory import MemorySessionBackend
from apps.runner.llm_config import provider_config_from_spec
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import override_settings

# isort: split

from libs.agent_spec import LLMSpec
from libs.providers.errors import CredentialStorageMisconfigured

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestProviderConfigFromSpec(OTransactionTestCase):
    def test_includes_user_id_and_secret_supplier(self) -> None:
        llm = LLMSpec(provider='openai', model='gpt-5.4-mini')
        cfg = provider_config_from_spec(llm, user_id=42)
        self.assertEqual(cfg.user_id, 42)
        self.assertIsNotNone(cfg.secret_supplier)
        supplier = MagicMock(return_value='sk-from-store')
        cfg.secret_supplier = supplier
        self.assertEqual(cfg.secret_supplier(), 'sk-from-store')

    def test_no_supplier_when_user_id_none(self) -> None:
        llm = LLMSpec(provider='openai', model='gpt-5.4-mini')
        cfg = provider_config_from_spec(llm, user_id=None)
        self.assertIsNone(cfg.user_id)
        self.assertIsNone(cfg.secret_supplier)

    def test_credential_ref_from_llm_spec(self) -> None:
        llm = LLMSpec(provider='openai', model='m', credential_ref='my-openai')
        cfg = provider_config_from_spec(llm, user_id=1, credential_ref=llm.credential_ref)
        self.assertEqual(cfg.credential_ref, 'my-openai')

    @expectLogItems([ExpectLogItem('apps.keys.crypto', logging.WARNING, r'credential decrypt failed', count=1)])
    def test_supplier_wraps_decrypt_failure(self) -> None:
        key_one = Fernet.generate_key().decode()
        key_two = Fernet.generate_key().decode()
        user = get_user_model().objects.create_user(username='decrypt-fail-user', password='x')
        with override_settings(CREDENTIALS_KEY=key_one):
            commands.set_system_default('openai', 'sk-stored')
        with override_settings(CREDENTIALS_KEY=key_two):
            cfg = provider_config_from_spec(LLMSpec(provider='openai', model='gpt-5.4-mini'), user_id=user.pk)
            supplier = cfg.secret_supplier
            assert supplier is not None
            with self.assertRaises(CredentialStorageMisconfigured):
                supplier()


class TestEnvOnlyCredentialPath(OTransactionTestCase):
    def test_memory_backend_with_no_user_id_skips_stored_credentials(self) -> None:
        key = Fernet.generate_key().decode()
        user = get_user_model().objects.create_user(username='env-only-user', password='x')
        with override_settings(CREDENTIALS_KEY=key):
            commands.set_system_default('openai', 'sk-from-db')
            backend = MemorySessionBackend(HARDCODED_SPEC.model_copy(), user_id=None)
            cfg = provider_config_from_spec(backend.get_spec().llm, user_id=backend.user_id)
            self.assertIsNone(cfg.secret_supplier)
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env'}, clear=False):
                provider_cfg_with_user = provider_config_from_spec(
                    LLMSpec(provider='openai', model='gpt-5.4-mini'),
                    user_id=user.pk,
                )
                supplier = provider_cfg_with_user.secret_supplier
                assert supplier is not None
                self.assertEqual(supplier(), 'sk-from-db')

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import logging

from apps.keys import crypto
from apps.keys.exceptions import KeyStorageMisconfiguredError
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from olib.py.django.test.cases import OTransactionTestCase
from olib.py.utils.logexpect import ExpectLogItem, expectLogItems


class TestCredentialCrypto(OTransactionTestCase):
    def test_round_trip(self) -> None:
        ciphertext = crypto.encrypt('sk-test-secret')
        self.assertEqual(crypto.decrypt(ciphertext), 'sk-test-secret')

    def test_missing_credentials_key_raises(self) -> None:
        with override_settings(CREDENTIALS_KEY=''):
            with self.assertRaises(ImproperlyConfigured):
                crypto.encrypt('sk-test-secret')

    @expectLogItems([ExpectLogItem('apps.keys.crypto', logging.WARNING, r'credential decrypt failed', count=1)])
    def test_wrong_master_key_raises_misconfigured(self) -> None:
        key_one = Fernet.generate_key().decode()
        key_two = Fernet.generate_key().decode()
        with override_settings(CREDENTIALS_KEY=key_one):
            ciphertext = crypto.encrypt('sk-test-secret')
        with override_settings(CREDENTIALS_KEY=key_two):
            with self.assertRaises(KeyStorageMisconfiguredError):
                crypto.decrypt(ciphertext)

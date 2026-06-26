# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from apps.runner.providers.anthropic_provider import AnthropicProvider
from apps.runner.providers.local_openai_provider import LocalOpenAIProvider
from apps.runner.providers.openai_provider import OpenAIProvider
from apps.web.demo_models import list_demo_models

from olib.py.django.test.cases import OTestCase


class TestDemoModels(OTestCase):
    def test_includes_catalog_models_from_providers(self) -> None:
        options = list_demo_models()
        providers = {option.provider for option in options}
        self.assertIn('openai', providers)
        self.assertIn('anthropic', providers)
        self.assertIn('local_openai', providers)
        openai_models = {option.model for option in options if option.provider == 'openai'}
        self.assertEqual(openai_models, set(OpenAIProvider.models.keys()))
        anthropic_models = {option.model for option in options if option.provider == 'anthropic'}
        self.assertTrue(anthropic_models)
        self.assertTrue(anthropic_models.issubset(set(AnthropicProvider.models.keys())))
        local_models = {option.model for option in options if option.provider == 'local_openai'}
        self.assertEqual(local_models, set(LocalOpenAIProvider.models.keys()))

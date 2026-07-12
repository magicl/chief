# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for inbox eval suite model catalog."""

from evals.inbox.suite import get_suite
from olib.py.django.test.cases import OTestCase


class TestInboxEvalSuiteModels(OTestCase):
    """Suite model catalog must include the default model."""

    def test_default_model_in_models(self) -> None:
        """default_model must be a member of models()."""
        suite = get_suite()
        self.assertIn(suite.default_model, suite.models())

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for inbox eval suite model catalog and local overlay discovery."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from evals.inbox.suite import InboxEvalSuite, get_suite, local_inbox_scenario_dir
from olib.py.django.test.cases import OTestCase


class TestInboxEvalSuiteModels(OTestCase):
    """Suite model catalog must include the default model."""

    def test_default_model_in_models(self) -> None:
        """default_model must be a member of models()."""
        suite = get_suite()
        self.assertIn(suite.default_model, suite.models())


class TestInboxEvalSuiteLocalOverlay(OTestCase):
    """Private CHIEF_LOCAL_DIR / .local eval scenarios merge into the suite."""

    def test_samples_include_local_overlay_scenarios(self) -> None:
        """Scenarios under the local overlay dir appear alongside public ones."""
        with TemporaryDirectory() as tmp:
            public_dir = Path(tmp) / 'public'
            local_root = Path(tmp) / 'local'
            local_dir = local_root / 'evals' / 'inbox' / 'scenarios'
            public_dir.mkdir()
            local_dir.mkdir(parents=True)
            (public_dir / 'public-only.yaml').write_text(
                'id: public-only\nprompt: |\n  Public prompt\n',
                encoding='utf-8',
            )
            (local_dir / 'private-newsletter.yaml').write_text(
                'id: private-newsletter\nprompt: |\n  Private prompt\n',
                encoding='utf-8',
            )

            suite = InboxEvalSuite(
                scenario_dir=public_dir,
                local_scenario_dir=local_inbox_scenario_dir(local_root=local_root),
            )
            sample_ids = [sample.id for sample in suite.samples()]

        self.assertEqual(sample_ids, ['private-newsletter', 'public-only'])

    def test_duplicate_scenario_id_raises(self) -> None:
        """The same scenario id in public and local packs fails loudly."""
        with TemporaryDirectory() as tmp:
            public_dir = Path(tmp) / 'public'
            local_dir = Path(tmp) / 'local' / 'evals' / 'inbox' / 'scenarios'
            public_dir.mkdir()
            local_dir.mkdir(parents=True)
            body = 'id: shared-id\nprompt: |\n  Shared\n'
            (public_dir / 'a.yaml').write_text(body, encoding='utf-8')
            (local_dir / 'b.yaml').write_text(body, encoding='utf-8')

            suite = InboxEvalSuite(
                scenario_dir=public_dir,
                local_scenario_dir=local_dir,
            )
            with self.assertRaisesRegex(ValueError, 'shared-id'):
                suite.samples()

    def test_missing_local_overlay_is_optional(self) -> None:
        """A missing local overlay directory leaves public samples unchanged."""
        with TemporaryDirectory() as tmp:
            public_dir = Path(tmp) / 'public'
            public_dir.mkdir()
            (public_dir / 'only-public.yaml').write_text(
                'id: only-public\nprompt: |\n  Public\n',
                encoding='utf-8',
            )
            suite = InboxEvalSuite(
                scenario_dir=public_dir,
                local_scenario_dir=Path(tmp) / 'missing' / 'evals' / 'inbox' / 'scenarios',
            )
            self.assertEqual([s.id for s in suite.samples()], ['only-public'])

    def test_local_inbox_scenario_dir_uses_chief_local_dir_env(self) -> None:
        """CHIEF_LOCAL_DIR selects the personal overlay root for inbox scenarios."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'overlay'
            resolved = local_inbox_scenario_dir(environ={'CHIEF_LOCAL_DIR': str(root)})
        self.assertEqual(resolved, root / 'evals' / 'inbox' / 'scenarios')

    def test_default_suite_reads_overlay_via_chief_local_dir(self) -> None:
        """Default suite construction merges scenarios from CHIEF_LOCAL_DIR when set."""
        with TemporaryDirectory() as tmp:
            public_dir = Path(tmp) / 'public'
            local_root = Path(tmp) / 'local'
            local_dir = local_root / 'evals' / 'inbox' / 'scenarios'
            public_dir.mkdir()
            local_dir.mkdir(parents=True)
            (public_dir / 'public-a.yaml').write_text(
                'id: public-a\nprompt: |\n  Public\n',
                encoding='utf-8',
            )
            (local_dir / 'private-b.yaml').write_text(
                'id: private-b\nprompt: |\n  Private\n',
                encoding='utf-8',
            )
            with patch.dict(os.environ, {'CHIEF_LOCAL_DIR': str(local_root)}, clear=False):
                suite = InboxEvalSuite(scenario_dir=public_dir, local_scenario_dir=None)
                sample_ids = [sample.id for sample in suite.samples()]
        self.assertEqual(sample_ids, ['private-b', 'public-a'])

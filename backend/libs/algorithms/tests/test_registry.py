# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
from libs.algorithms.registry import (
    AlgorithmInfo,
    get_algorithm,
    index_algorithms,
    list_algorithms,
)

from olib.py.django.test.cases import OTestCase


class TestAlgorithmRegistry(OTestCase):
    def test_lists_chat_name(self) -> None:
        """Chat name is always listed, including when there are zero runs."""
        ids = [item.algorithm_id for item in list_algorithms()]
        self.assertEqual(ids, ['chat_name'])
        chat_name = get_algorithm('chat_name')
        self.assertIsNotNone(chat_name)
        assert chat_name is not None
        self.assertEqual(chat_name.display_name, 'Chat name')

    def test_unknown_id_returns_none(self) -> None:
        """Lookup of an unregistered algorithm id returns None."""
        self.assertIsNone(get_algorithm('not_a_registered_algorithm'))

    def test_duplicate_ids_raise(self) -> None:
        """Indexing must fail clearly when two rows share an algorithm_id."""
        duplicates = (
            AlgorithmInfo(algorithm_id='dup', display_name='First'),
            AlgorithmInfo(algorithm_id='dup', display_name='Second'),
        )
        with self.assertRaises(ValueError) as ctx:
            index_algorithms(duplicates)
        self.assertIn('dup', str(ctx.exception))

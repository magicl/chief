# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Test source adapter for dev and automated tests."""

from __future__ import annotations

import itertools
from typing import Any

from libs.sources.base import PollResult, PutItemCallback, SecretSupplier, SourceAdapter
from libs.sources.dedup import dedupe_enabled, should_skip_known, validate_dedupe_config

_counter = itertools.count(1)


class TestSourceAdapter(SourceAdapter):
    adapter_type = 'test'

    def validate_config(self, config: dict[str, Any]) -> None:
        prefix = config.get('prefix', 'test')
        if not isinstance(prefix, str) or not prefix:
            raise ValueError('prefix must be a non-empty string')
        batch_size = config.get('batch_size', 1)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError('batch_size must be a positive integer')
        validate_dedupe_config(config)

    def poll(
        self,
        *,
        config: dict[str, Any],
        put_item: PutItemCallback,
        credential_supplier: SecretSupplier | None,
        known_external_ids: frozenset[str] | None = None,
    ) -> PollResult:
        """Enqueue up to ``batch_size`` synthetic items for integration tests."""
        del credential_supplier
        prefix = config.get('prefix', 'test')
        batch_size = config.get('batch_size', 1)
        dedupe = dedupe_enabled(config)
        enqueued = 0
        for _ in range(batch_size):
            external_id = f'{prefix}-{next(_counter)}'
            if should_skip_known(
                dedupe=dedupe,
                external_id=external_id,
                known_external_ids=known_external_ids,
            ):
                continue
            payload = {'source': 'test', 'external_id': external_id}
            result = put_item(payload=payload, external_id=external_id)
            if result.created:
                enqueued += 1
        return PollResult(items_seen=batch_size, items_enqueued=enqueued)

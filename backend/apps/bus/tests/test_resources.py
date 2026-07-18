# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
import json
from unittest.mock import MagicMock, patch

from apps.bus.resources import (
    publish_resource_update,
    publish_resource_update_after_commit,
    resource_message,
    user_resource_channel,
)
from django.test import override_settings

from olib.py.django.test.cases import OTestCase


@override_settings(CACHE_PREFIX='test:')
class TestResourceEvents(OTestCase):
    """Verify user-scoped resource event primitives."""

    def test_user_resource_channel_is_cache_prefixed(self) -> None:
        """Build the resource channel from the cache prefix and user id."""
        self.assertEqual(user_resource_channel(42), 'test:user:42:resources')

    def test_resource_message_accepts_each_resource(self) -> None:
        """Return the exact generic envelope for every supported resource."""
        for resource in ('agents', 'keys'):
            with self.subTest(resource=resource):
                self.assertEqual(resource_message(resource), {'channel': 'resource_update', 'resource': resource})

    def test_resource_message_rejects_unknown_resource(self) -> None:
        """Reject resource names outside the public allowlist."""
        with self.assertRaises(ValueError):
            resource_message('sessions')  # type: ignore[arg-type]

    @patch('apps.bus.resources.sync_client')
    def test_publish_resource_update_uses_user_channel_and_envelope(self, mock_sync: MagicMock) -> None:
        """Publish the generic envelope as JSON on the user's channel."""
        client = mock_sync.return_value

        publish_resource_update(42, 'agents')

        client.publish.assert_called_once_with(
            'test:user:42:resources',
            json.dumps({'channel': 'resource_update', 'resource': 'agents'}),
        )

    @patch('apps.bus.resources.publish_resource_update')
    def test_after_commit_defers_typed_resource_update(self, publish: MagicMock) -> None:
        """Publish the typed refresh hint only when the transaction commits."""
        with self.captureOnCommitCallbacks(execute=True):
            publish_resource_update_after_commit(42, 'agents')
            publish.assert_not_called()

        publish.assert_called_once_with(42, 'agents')

    @patch('apps.bus.resources.publish_resource_update')
    def test_after_commit_swallows_transport_failure(self, publish: MagicMock) -> None:
        """Keep committed domain writes independent from refresh transport."""
        publish.side_effect = RuntimeError('secret payload must not escape')

        with self.assertLogs('apps.bus.resources', level='DEBUG') as captured:
            with self.captureOnCommitCallbacks(execute=True):
                publish_resource_update_after_commit(42, 'keys')

        self.assertNotIn('secret payload', '\n'.join(captured.output))

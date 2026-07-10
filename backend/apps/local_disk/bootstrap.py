# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bootstrap local disk synchronization from Django process startup."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from django.conf import settings

from .paths import resolve_local_root
from .sync import sync_all
from .watch import start_watcher

logger = logging.getLogger(__name__)

_bootstrap_lock = threading.Lock()
_synced_roots: set[Path] = set()


def _is_runserver_parent() -> bool:
    """Return whether this is Django's autoreloader parent process."""
    return 'runserver' in sys.argv and os.environ.get('RUN_MAIN') != 'true'


def maybe_start_local_disk(*, force_watch: bool | None = None) -> None:
    """Run initial sync and optionally start a process-local directory watcher."""
    root = resolve_local_root()
    if root is None:
        return
    if not root.is_dir():
        logger.warning('Configured local disk root is missing: %s', root)
        return
    if _is_runserver_parent():
        return

    (root / 'keys').mkdir(exist_ok=True)
    (root / 'agents').mkdir(exist_ok=True)
    with _bootstrap_lock:
        if root not in _synced_roots:
            sync_all()
            _synced_roots.add(root)

    should_watch = bool(getattr(settings, 'CHIEF_LOCAL_WATCH', False))
    if force_watch is not None:
        should_watch = force_watch
    if should_watch:
        start_watcher(root)

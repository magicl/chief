# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Celery task registry entrypoint.

olib's celery setup imports ``chief.tasks`` on worker startup (see
``app.conf.imports``). Re-import each app's task module here so their
``@shared_task`` definitions are registered with the worker.

No app tasks yet — the agents/queues domain is still being designed.
"""

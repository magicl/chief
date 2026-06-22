# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Ensure the Celery app is imported when Django starts so that shared_task
# uses this app, and so `celery -A chief` can find `chief.celery_app`.
from .celery import app as celery_app

__all__ = ['celery_app']

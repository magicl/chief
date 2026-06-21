# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Jinja-rendered dashboard.

Placeholder for now: the agents/queues domain is still being designed, so this
just renders a landing page. Build the real overview out once the models exist.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, 'web/dashboard.html', {})

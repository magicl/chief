# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Top-level URL configuration for the chief project."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

from chief.views import check, livez, readyz

urlpatterns = [
    path('admin', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin/', admin.site.urls),
    path('health/livez', livez, name='health_livez'),
    path('health/readyz', readyz, name='health_readyz'),
    path('client/check', check, name='client_check'),
    path('', include('apps.web.urls')),
]

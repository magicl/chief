# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Django settings for the chief project.

Builds on olib's shared `settingsbase` (logging, db/redis wiring from env,
jinja2 template engine, celery glue) and only adds what chief needs on top.
"""

from olib.py.django.app.settingsbase import *  # noqa: F401,F403  pylint: disable=wildcard-import,unused-wildcard-import

###########################################################
# Application definition
###########################################################

INSTALLED_APPS += [  # noqa: F405
    'chief',
    'apps.web',
    'django_extensions',
]

ROOT_URLCONF = 'chief.urls'
WSGI_APPLICATION = 'chief.wsgi.application'

SITE_NAME = env.str('SITE_NAME', default='Chief')  # noqa: F405

# Server-rendered app: keep CSRF/session cookies usable over plain http in dev.
SESSION_COOKIE_SAMESITE = 'Lax'

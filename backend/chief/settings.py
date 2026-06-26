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
    'apps.agents',
    'apps.sessions',
    'apps.bus',
    'apps.runner',
    'apps.web',
    'django_extensions',
]

ROOT_URLCONF = 'chief.urls'
ASGI_APPLICATION = 'chief.asgi.application'
WSGI_APPLICATION = 'chief.wsgi.application'

SITE_NAME = env.str('SITE_NAME', default='Chief')  # noqa: F405

# Server-rendered app: keep CSRF/session cookies usable over plain http in dev.
SESSION_COOKIE_SAMESITE = 'Lax'

# Jinja2 dashboard templates use auth, CSRF, and request context.
for _tpl in TEMPLATES:  # noqa: F405
    if _tpl.get('NAME') == 'jinja2':
        _tpl.setdefault('OPTIONS', {})['context_processors'] = [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.template.context_processors.csrf',
            'django.contrib.messages.context_processors.messages',
        ]
        break

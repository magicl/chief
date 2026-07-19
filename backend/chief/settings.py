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
    'apps.queues',
    'apps.sessions',
    'apps.bus',
    'apps.runner',
    'apps.web',
    'apps.keys',
    'apps.local_sync',
    'django_extensions',
    'django_celery_beat',
]

# This route-specific layer must remain outermost so it runs after all converted
# downstream failures; the callback view keeps its decorator as defense in depth.
MIDDLEWARE = ['apps.web.middleware.OAuthCallbackResponseMiddleware', *MIDDLEWARE]  # noqa: F405

# Per-trigger schedule crons live in the DB; platform beats stay in chief/celery.py.
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

ROOT_URLCONF = 'chief.urls'
ASGI_APPLICATION = 'chief.asgi.application'
WSGI_APPLICATION = 'chief.wsgi.application'

SITE_NAME = env.str('SITE_NAME', default='Chief')  # noqa: F405

CHIEF_LOCAL_DIR = env.str('CHIEF_LOCAL_DIR', default='')  # noqa: F405

# Optional application credentials for user-owned Google OAuth grants.
GOOGLE_OAUTH_CLIENT_ID = env.str('GOOGLE_OAUTH_CLIENT_ID', default='')  # noqa: F405
GOOGLE_OAUTH_CLIENT_SECRET = env.str('GOOGLE_OAUTH_CLIENT_SECRET', default='')  # noqa: F405

# Optional application credentials for user-owned Dropbox OAuth grants.
DROPBOX_OAUTH_APP_KEY = env.str('DROPBOX_OAUTH_APP_KEY', default='')  # noqa: F405
DROPBOX_OAUTH_APP_SECRET = env.str('DROPBOX_OAUTH_APP_SECRET', default='')  # noqa: F405
OAUTH_STATE_MAX_AGE_SECONDS = 600

# Fernet master key for credential encryption at rest (see apps.keys.crypto).
# Required when DEBUG is False; dev default when DEBUG is True.
CREDENTIALS_KEY = env_secret(  # noqa: F405
    'CREDENTIALS_KEY',
    debug_default='9aVIpUljhBqM8r_SWsV6t9fn3Y4oGFRMBuM7-BKCxkk=',
)

# Server-rendered app: keep CSRF/session cookies usable over plain http in dev.
SESSION_COOKIE_SAMESITE = 'Lax'

# The front proxy must overwrite X-Forwarded-Proto rather than append to or trust
# a client-supplied value; the current nginx configuration enforces this boundary.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

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

from decimal import Decimal as _Decimal

# Session limits — global defaults (narrowing hierarchy floor)
DEFAULT_MAX_SESSION_ITERATIONS: int | None = 200
DEFAULT_MAX_SESSION_COST_USD: _Decimal | None = _Decimal('5.00')

# Agent rolling spend defaults
DEFAULT_AGENT_DAILY_SPEND_LIMIT_USD: _Decimal | None = None
DEFAULT_AGENT_MONTHLY_SPEND_LIMIT_USD: _Decimal | None = None

# User rolling spend defaults
DEFAULT_USER_DAILY_SPEND_LIMIT_USD: _Decimal | None = None
DEFAULT_USER_MONTHLY_SPEND_LIMIT_USD: _Decimal | None = None

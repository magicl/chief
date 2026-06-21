#!/bin/bash
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Entrypoint for the chief backend container.

set -euo pipefail

ENTRYPOINT="${ENTRYPOINT:-web-server}"

if [[ $ENTRYPOINT == "web-server" ]]; then
	echo "** Run migrations **"
	./manage.py migrate --noinput
	echo "** Ensure superuser **"
	./manage.py ensure_superuser --no-input || true
	exec ./manage.py runserver 0.0.0.0:8000
elif [[ $ENTRYPOINT == "celery-worker" ]]; then
	exec celery -A chief worker --loglevel=INFO
elif [[ $ENTRYPOINT == "celery-beat" ]]; then
	exec celery -A chief beat --loglevel=INFO --pidfile /tmp/celery-beat.pid -s /tmp/celery-beat-schedule
else
	echo "invalid entrypoint selection for entrypoint.sh: $ENTRYPOINT"
	exit 1
fi

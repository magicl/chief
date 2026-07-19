#!/bin/bash
# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
# Entrypoint for the chief backend container.

set -euo pipefail

ENTRYPOINT="${ENTRYPOINT:-web-server}"
DEBUG="${DEBUG:-true}"

if [[ "$ENTRYPOINT" == "web-server" ]]; then
	if [[ "$DEBUG" == "true" ]]; then
		# Compose owns its disposable database bootstrap and live-reload loop.
		echo "** Run migrations **"
		./manage.py migrate --noinput
		echo "** Ensure superuser **"
		./manage.py ensure_superuser --no-input || true
		exec uvicorn chief.asgi:application --host 0.0.0.0 --port 8000 --workers 1 --lifespan off --reload
	else
		# Hosted migrations run in an Argo hook, before this fixed-size web process starts.
		exec uvicorn chief.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --lifespan off
	fi
elif [[ "$ENTRYPOINT" == "celery-worker" ]]; then
	# Agent sessions are long-lived and I/O-bound; use threads so concurrent
	# sessions don't each occupy a prefork worker slot.
	exec celery -A chief worker --loglevel=WARNING --pool=threads --concurrency=16
elif [[ "$ENTRYPOINT" == "celery-beat" ]]; then
	exec celery -A chief beat --loglevel=WARNING --pidfile /tmp/celery-beat.pid -s /tmp/celery-beat-schedule
else
	echo "invalid entrypoint selection for entrypoint.sh: $ENTRYPOINT"
	exit 1
fi

# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ENV LANG=C.UTF-8
ENV TZ=UTC
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OBSIDIAN_VAULT_DATA=/data
ENV PORT=8100

# Node 22+ and obsidian-headless (`ob` CLI) for headless Sync supervision.
# obsidian-headless is pinned to the latest published release at the time of
# writing (check `npm view obsidian-headless versions` before bumping — it's
# still an open-beta package with no LTS/stability guarantees).
ENV OBSIDIAN_HEADLESS_VERSION=0.0.14
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g "obsidian-headless@${OBSIDIAN_HEADLESS_VERSION}" \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# --create-home makes /app (the app user's home and WORKDIR) exist and be owned
# by app; /data is the mount point for the vault volume, whose initial ownership
# Docker copies from the image when the named volume is first created.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app \
    && mkdir -p /data \
    && chown app:app /data

# Install locked Python deps from the workspace lock (same pattern as backend/Dockerfile.dev).
# Build context is the repository root.
RUN python -m pip install --disable-pip-version-check --root-user-action=ignore uv
WORKDIR /deps
COPY ./pyproject.toml ./uv.lock ./
COPY ./backend/pyproject.toml ./backend/pyproject.toml
COPY ./infra/pyproject.toml ./infra/pyproject.toml
COPY ./olib/pyproject.toml ./olib/pyproject.toml
COPY ./services/obsidian/pyproject.toml ./services/obsidian/pyproject.toml
RUN uv export --frozen --package obsidian-vault --no-hashes --output-file requirements.lock.txt \
    && uv pip install -r requirements.lock.txt --system --no-cache

WORKDIR /app

COPY --chown=app:app services/obsidian/obsidian_vault/ obsidian_vault/
COPY --chown=app:app services/obsidian/entrypoint.sh .
RUN chmod +x entrypoint.sh

USER app

EXPOSE 8100

ENTRYPOINT ["./entrypoint.sh"]

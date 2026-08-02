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

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data \
    && chown app:app /data /app

WORKDIR /app

# Vault HTTP service runtime (FastAPI + uvicorn).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app obsidian_vault/ obsidian_vault/
COPY --chown=app:app entrypoint.sh .
RUN chmod +x entrypoint.sh

USER app

EXPOSE 8100

ENTRYPOINT ["./entrypoint.sh"]

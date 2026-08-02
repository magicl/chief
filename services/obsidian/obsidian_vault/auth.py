# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Bearer-token authentication dependency for the vault service HTTP API.

This is the **inter-service** auth plane (Chief backend/worker to vault
service), a shared static token injected via environment/Compose — never an
`apps.keys` provider credential. A missing or mismatched `Authorization:
Bearer <token>` header is a 401 on every `/v1` route.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status


class BearerTokenAuth:
    """FastAPI dependency requiring `Authorization: Bearer <token>` to match.

    Instantiated once per configured token and wired via `Depends(...)` on
    every route `create_app` registers, so all routes share one auth check.
    """

    def __init__(self, token: str) -> None:
        """Store the expected token that every request must present."""
        self._token = token

    def __call__(self, request: Request) -> None:
        """Raise 401 unless the request's Authorization header is `Bearer <token>`.

        Uses a constant-time comparison to avoid leaking token length/prefix
        via response timing. An empty configured token never authenticates
        (defense in depth alongside `main.py`'s startup check) — a blank
        header credential must never match a misconfigured blank token.
        """
        scheme, _, credential = request.headers.get('authorization', '').partition(' ')
        token_matches = (
            bool(self._token) and scheme.lower() == 'bearer' and hmac.compare_digest(credential, self._token)
        )
        if not token_matches:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

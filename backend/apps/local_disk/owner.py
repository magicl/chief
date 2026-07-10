# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Resolve local disk owner labels to Django users."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser


def resolve_owner(owner: str) -> AbstractBaseUser | None:
    """Resolve an exact username or uniquely matching email to a user."""
    user_model = get_user_model()
    username_match = user_model.objects.filter(username=owner).first()
    if username_match is not None:
        return username_match

    email_matches = list(user_model.objects.filter(email=owner)[:2])
    if len(email_matches) == 1:
        return email_matches[0]
    return None

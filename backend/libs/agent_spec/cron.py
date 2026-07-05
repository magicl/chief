# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Cron validation and minute-level matching for schedule triggers."""

from __future__ import annotations

from dataclasses import dataclass

from croniter import croniter


@dataclass(frozen=True, slots=True)
class CrontabFields:
    """Five-field cron components for django-celery-beat ``CrontabSchedule`` rows."""

    minute: str
    hour: str
    day_of_month: str
    month_of_year: str
    day_of_week: str


def parse_cron_fields(expression: str) -> CrontabFields:
    """Split a validated 5-field cron string into beat schedule components (UTC)."""
    validate_cron_expression(expression)
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(f'cron expression must have 5 fields: {expression!r}')
    return CrontabFields(
        minute=parts[0],
        hour=parts[1],
        day_of_month=parts[2],
        month_of_year=parts[3],
        day_of_week=parts[4],
    )


def validate_cron_expression(expression: str) -> None:
    """Raise ValueError when expression is not a valid 5-field cron string."""
    if not croniter.is_valid(expression):
        raise ValueError(f'invalid cron expression: {expression!r}')

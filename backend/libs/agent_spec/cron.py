# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Cron validation and parsing for schedule triggers (UTC, 5-field crontab)."""

from __future__ import annotations

from dataclasses import dataclass

import crontab as crontab_module
from croniter import croniter


@dataclass(frozen=True, slots=True)
class CrontabFields:
    """Five-field cron components for django-celery-beat ``CrontabSchedule`` rows."""

    minute: str
    hour: str
    day_of_month: str
    month_of_year: str
    day_of_week: str


class _CronSlices(crontab_module.CronSlices):  # type: ignore[misc]
    """Cron slice validation aligned with ``django_celery_beat.validators``."""

    def __init__(self, *args: str) -> None:
        crontab_module.CronSlices.__init__(
            self,
            [_CronSlice(info) for info in crontab_module.S_INFO],
        )
        self.special = None
        self.setall(*args)
        self.is_valid = self.is_self_valid

    @classmethod
    def validate(cls, expression: str) -> None:
        """Raise ``ValueError`` when *expression* is not a valid crontab slice set."""
        try:
            cls(expression)
        except Exception as exc:
            raise ValueError(str(exc)) from exc


class _CronSlice(crontab_module.CronSlice):  # type: ignore[misc]
    """Cron slice with custom range parser (matches django-celery-beat)."""

    def get_range(self, *vrange: str) -> list[crontab_module.CronRange | _CronRange]:
        ret = _CronRange(self, *vrange)
        if ret.dangling is not None:
            return [ret.dangling, ret]
        return [ret]


class _CronRange(crontab_module.CronRange):  # type: ignore[misc]
    """Cron range parser that rejects invalid ranges (matches django-celery-beat)."""

    def parse(self, value: str) -> None:
        if value.count('/') == 1:
            value, seq = value.split('/')
            try:
                self.seq = self.slice.parse_value(seq)
            except crontab_module.SundayError:
                self.seq = 1
                value = '0-0'
            if self.seq < 1 or self.seq > self.slice.max:
                raise ValueError('Sequence can not be divided by zero or max')
        if value.count('-') == 1:
            vfrom, vto = value.split('-')
            self.vfrom = self.slice.parse_value(vfrom, sunday=0)
            try:
                self.vto = self.slice.parse_value(vto)
            except crontab_module.SundayError:
                if self.vfrom == 1:
                    self.vfrom = 0
                else:
                    self.dangling = 0
                self.vto = self.slice.parse_value(vto, sunday=6)
            if self.vto < self.vfrom:
                raise ValueError(f"Bad range '{self.vfrom}-{self.vto}'")
        elif value == '*':
            self.all()
        else:
            raise ValueError(f'Unknown cron range value {value!r}')


def validate_cron_expression(expression: str) -> None:
    """Raise ValueError when *expression* is not a valid 5-field UTC crontab."""
    if not isinstance(expression, str):
        raise ValueError(f'cron expression must be a string, got {type(expression).__name__}')

    stripped = expression.strip()
    if not stripped:
        raise ValueError('cron expression is required')

    if stripped != expression:
        raise ValueError('cron expression must not have leading or trailing whitespace')

    parts = stripped.split()
    if len(parts) != 5:
        raise ValueError(
            'cron expression must have exactly 5 fields '
            f'(minute hour day month weekday), got {len(parts)}: {expression!r}',
        )

    try:
        _CronSlices.validate(stripped)
    except ValueError as exc:
        raise ValueError(f'invalid cron expression {stripped!r}: {exc}') from exc

    if not croniter.is_valid(stripped):
        raise ValueError(f'invalid cron expression: {stripped!r}')

    try:
        croniter(stripped)
    except (ValueError, KeyError) as exc:
        raise ValueError(f'invalid cron expression: {stripped!r}') from exc


def parse_cron_fields(expression: str) -> CrontabFields:
    """Split a validated 5-field cron string into beat schedule components (UTC)."""
    validate_cron_expression(expression)
    parts = expression.split()
    return CrontabFields(
        minute=parts[0],
        hour=parts[1],
        day_of_month=parts[2],
        month_of_year=parts[3],
        day_of_week=parts[4],
    )

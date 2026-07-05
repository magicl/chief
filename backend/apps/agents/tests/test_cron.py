# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~
"""Unit tests for cron expression validation."""

from __future__ import annotations

from libs.agent_spec.cron import parse_cron_fields, validate_cron_expression

from olib.py.django.test.cases import OTestCase


class TestValidateCronExpression(OTestCase):
    def test_accepts_common_expressions(self) -> None:
        for expression in (
            '0 * * * *',
            '*/5 * * * *',
            '0 9 * * *',
            '30 14 * * 1-5',
            '0 0 1 1 *',
            '0 0 * * MON',
        ):
            with self.subTest(expression=expression):
                validate_cron_expression(expression)

    def test_rejects_six_field_expression(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression('0 * * * * *')
        self.assertIn('5 fields', str(ctx.exception))

    def test_rejects_out_of_range_minute(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression('60 * * * *')
        self.assertIn('0-59', str(ctx.exception))

    def test_rejects_out_of_range_hour(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression('0 25 * * *')
        self.assertIn('0-23', str(ctx.exception))

    def test_rejects_out_of_range_day_of_week(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression('0 0 * * 8')
        self.assertIn('0-6', str(ctx.exception))

    def test_rejects_garbage_expression(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression('not * * * *')
        self.assertIn('invalid cron', str(ctx.exception))

    def test_rejects_whitespace_padding(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cron_expression(' 0 * * * *')
        self.assertIn('whitespace', str(ctx.exception))


class TestParseCronFields(OTestCase):
    def test_splits_valid_expression(self) -> None:
        fields = parse_cron_fields('*/2 14 * * 1')

        self.assertEqual(fields.minute, '*/2')
        self.assertEqual(fields.hour, '14')
        self.assertEqual(fields.day_of_month, '*')
        self.assertEqual(fields.month_of_year, '*')
        self.assertEqual(fields.day_of_week, '1')

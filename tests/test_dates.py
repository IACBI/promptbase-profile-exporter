import unittest

from promptbase_exporter.dates import parse_datetime_ms


class FullDateTests(unittest.TestCase):
    """Only a bare YYYY-MM-DD date stretches to the end of the day."""

    def test_a_bare_date_moves_to_the_end_of_the_day(self):
        self.assertGreater(parse_datetime_ms("2026-01-01", end_of_day=True),
                           parse_datetime_ms("2026-01-01", end_of_day=False))

    def test_anything_with_a_time_keeps_it(self):
        for value in ("2026-01-01T00:00", "2026-01-01T00:00:00Z", "2026-01-01 00:00"):
            with self.subTest(value=value):
                self.assertEqual(parse_datetime_ms(value, end_of_day=True),
                                 parse_datetime_ms(value, end_of_day=False))

    def test_not_quite_dates_are_rejected(self):
        for value in ("2026-1-1", "2026/01/01", "2026-01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_datetime_ms(value, end_of_day=False)


class ParseDatetimeMsTests(unittest.TestCase):
    def test_bare_date_uses_utc_start_of_day(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01", end_of_day=False),
            1767225600000,
        )

    def test_bare_date_end_of_day_is_later_than_start(self):
        start = parse_datetime_ms("2026-01-01", end_of_day=False)
        end = parse_datetime_ms("2026-01-01", end_of_day=True)
        self.assertGreater(end, start)
        # End of day stays within the same calendar date.
        self.assertLess(end - start, 24 * 60 * 60 * 1000)

    def test_naive_datetime_assumed_utc(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01T00:00:00", end_of_day=False),
            parse_datetime_ms("2026-01-01", end_of_day=False),
        )

    def test_trailing_z_is_utc(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01T12:00:00Z", end_of_day=False),
            parse_datetime_ms("2026-01-01T12:00:00", end_of_day=False),
        )

    def test_offset_is_converted_to_utc(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01T01:00:00+01:00", end_of_day=False),
            parse_datetime_ms("2026-01-01T00:00:00+00:00", end_of_day=False),
        )

    def test_empty_value_rejected(self):
        with self.assertRaises(ValueError):
            parse_datetime_ms("   ", end_of_day=False)

    def test_an_offset_past_year_1_or_9999_is_invalid_not_a_crash(self):
        # astimezone() raised OverflowError, a traceback in the CLI and a 500 in the web UI.
        for value in ("0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid date"):
                parse_datetime_ms(value, end_of_day=False)

    def test_invalid_value_rejected(self):
        for value in ("not-a-date", "2026-13-01", "2026-01-99"):
            with self.assertRaises(ValueError):
                parse_datetime_ms(value, end_of_day=False)


if __name__ == "__main__":
    unittest.main()

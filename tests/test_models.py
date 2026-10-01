import unittest

from promptbase_exporter.formatting import EXPORT_FORMATS, format_records
from promptbase_exporter.models import PromptRecord, ms_to_iso_or_none


def record(created, **extras):
    return PromptRecord(title="T", description="d", slug="t", prompt_type="gpt",
                        domain="text", created=created, price=1.0, **extras)


class TimestampTests(unittest.TestCase):
    def test_real_times_render_as_before(self):
        self.assertEqual(record(1_700_000_000_123).created_iso,
                         "2023-11-14T22:13:20.123000+00:00")
        self.assertEqual(record(1).created_iso, "1970-01-01T00:00:00.001000+00:00")

    def test_times_before_1970_work_on_every_platform(self):
        # fromtimestamp() refuses negative values on Windows.
        self.assertEqual(record(-86_400_000).created_iso, "1969-12-31T00:00:00+00:00")

    def test_a_time_outside_years_1_to_9999_is_unknown_not_a_crash(self):
        # Found by fuzzing: a corrupt catalog's huge value overflowed time_t.
        for huge in (10**18, -(10**18)):
            with self.subTest(created=huge):
                self.assertEqual(record(huge).created_iso, "")
                self.assertIsNone(ms_to_iso_or_none(huge))
                for export_format in EXPORT_FORMATS:
                    extras = () if export_format == "txt" else ("updated", "last_sale")
                    format_records([record(huge, updated=huge, last_sale=huge)],
                                   export_format, extra_fields=extras)
        self.assertIsNone(ms_to_iso_or_none(None))
        self.assertEqual(record(0).created_iso, "")  # zero means not recorded


if __name__ == "__main__":
    unittest.main()

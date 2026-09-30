"""Seeded fuzzing: what a lossless format writes, its loader must read back exactly.

The seed is fixed, so a failure is reproducible; the subTest message names the
format, the seed, and the record that failed.
"""

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from promptbase_exporter.convert import record_from_dict
from promptbase_exporter.diffing import load_catalog
from promptbase_exporter.formatting import FORMAT_EXTENSIONS, write_export_to_path
from promptbase_exporter.models import EXTRA_FIELDS, ITEM_TYPES, PromptRecord
from tests.scratch import use_scratch_working_directory

SEED = 20260930
ROUNDS = 40
LOSSLESS = ("json", "ndjson", "csv", "html")

# Characters that have broken exporters before: separators, quoting, markup, formula
# prefixes, Unicode line breaks, combining marks, and astral code points.
ALPHABET = (
    list("abcXYZ019 ")
    + list(",;\"'`\\/|<>&=+-@#*_[](){}%")
    + [chr(code) for code in (10, 13, 9, 0x85, 0x2028, 0x2029, 0xA0, 0xFEFF, 0x200B)]
    + [chr(13) + chr(10)]
    + [chr(code) for code in (0xE9, 0x301, 0x4E2D, 0x416, 0x1F4CC)]
    + [chr(0x1F1F9) + chr(0x1F1F7)]
)


def setUpModule():
    use_scratch_working_directory()


def text(rng, *, minimum=0, maximum=40):
    """Text the way the client hands it over: surrounding whitespace already stripped."""
    value = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(minimum, maximum))).strip()
    return value or "x" * minimum


def random_record(rng, index, item_type):
    return PromptRecord(
        title=text(rng, minimum=1),
        description=text(rng, maximum=120),
        slug=f"slug-{index}-{rng.randint(0, 10**6)}",
        prompt_type=rng.choice(["", "gpt", "midjourney", text(rng, maximum=8)]),
        domain=rng.choice(["text", "image"]),
        created=rng.randint(0, 1_900_000_000_000),
        price=rng.choice([0.0, 1.0, 2.5, round(rng.uniform(0, 500), 2)]),
        discount=rng.choice([0.0, 0.2, 0.75]),
        views=rng.randint(0, 10**7),
        sales=rng.randint(0, 10**5),
        downloads=rng.randint(0, 10**5),
        favorites=rng.randint(0, 10**5),
        rating=rng.choice([0.0, 4.5, round(rng.uniform(0, 5), 2)]),
        reviews=rng.randint(0, 10**4),
        tags=tuple(text(rng, minimum=1, maximum=8) for _ in range(rng.randint(0, 4))),
        engine=rng.choice(["", "gpt-5.5", text(rng, maximum=10)]),
        nsfw=rng.choice([True, False]),
        featured=rng.choice([True, False]),
        updated=rng.choice([None, rng.randint(0, 1_900_000_000_000)]),
        last_sale=rng.choice([None, rng.randint(0, 1_900_000_000_000)]),
        unique_sales=rng.randint(0, 10**4),
        item_type=item_type,
    )


class RoundTripFuzzTests(unittest.TestCase):
    def _round_trip(self, export_format, item_type, *, csv_safe=False):
        rng = random.Random(f"{SEED}-{export_format}-{item_type}-{csv_safe}")
        records = [random_record(rng, index, item_type) for index in range(ROUNDS)]
        with TemporaryDirectory() as directory:
            path = write_export_to_path(
                Path(directory) / f"catalog.{FORMAT_EXTENSIONS[export_format]}",
                records, export_format, overwrite=True,
                extra_fields=EXTRA_FIELDS, item_type=item_type, csv_safe=csv_safe,
            )
            loaded = load_catalog(path, csv_safe=csv_safe, strict=True)
        self.assertEqual(len(loaded), len(records))
        for original, row in zip(records, loaded, strict=True):
            with self.subTest(format=export_format, item_type=item_type, slug=original.slug):
                self.assertEqual(record_from_dict(row), original)

    def test_every_lossless_format_reads_back_what_it_wrote(self):
        for export_format in LOSSLESS:
            for item_type in ITEM_TYPES:
                with self.subTest(format=export_format, item_type=item_type):
                    self._round_trip(export_format, item_type)

    def test_protected_csv_reads_back_what_it_wrote(self):
        for item_type in ITEM_TYPES:
            with self.subTest(item_type=item_type):
                self._round_trip("csv", item_type, csv_safe=True)


if __name__ == "__main__":
    unittest.main()

"""Seeded property tests for catalog comparison (``--compare``, ``--update-file``, ``pb-diff``).

Random catalogs are mutated in known ways, and the diff must report exactly those
changes and keep its counts consistent. The seed is fixed, so a failure is
reproducible from the subTest message.
"""

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from promptbase_exporter.diffing import compare_catalog_records, load_catalog
from promptbase_exporter.formatting import FORMAT_EXTENSIONS, record_to_dict, write_export_to_path
from promptbase_exporter.models import ITEM_TYPES, PromptRecord
from tests.scratch import use_scratch_working_directory

SEED = 20261001
ROUNDS = 60
WORDS = ("logo", "poster", "anime", "story", "photo", "cosplay", "menu", "icon", "neon", "map")


def setUpModule():
    use_scratch_working_directory()


def record(rng, number, item_type):
    words = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 4)))
    return PromptRecord(
        title=f"{words.title()} {number}",
        description=" ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 30))),
        slug=f"{words.replace(' ', '-')}-{number}",
        prompt_type=rng.choice(["gpt", "midjourney", "dall-e"]) if item_type != "app" else "",
        domain=rng.choice(["text", "image"]),
        created=1_700_000_000_000 + number,
        price=rng.choice([0.0, 1.99, 2.99, 4.99]),
        views=rng.randint(0, 5000),
        item_type=item_type,
    )


def rows(records):
    return [record_to_dict(item) for item in records]


def scenario(rng, item_type):
    """A previous catalog, a current one, and the slugs that were added, removed, changed."""
    previous = [record(rng, number, item_type) for number in range(rng.randint(0, 40))]
    current, removed, changed = [], set(), set()
    for item in previous:
        roll = rng.random()
        if roll < 0.15:
            removed.add(item.slug)
            continue
        if roll < 0.35:
            field = rng.choice(["title", "description", "price", "domain"])
            value = {
                "title": item.title + " v2",
                "description": item.description + " updated",
                "price": item.price + 1.0,
                "domain": "image" if item.domain == "text" else "text",
            }[field]
            item = PromptRecord(**{**item.__dict__, field: value})
            changed.add(item.slug)
        elif roll < 0.5:
            # Counters move on every run and must never count as a change.
            item = PromptRecord(**{**item.__dict__, "views": item.views + rng.randint(1, 99)})
        elif roll < 0.6:
            # Neither must re-wrapped text: line breaks and runs of spaces are layout.
            spacing = rng.choice(["  ", "\n", "\r\n", " \n  "])
            item = PromptRecord(**{**item.__dict__,
                                   "description": item.description.replace(" ", spacing)})
        current.append(item)
    added = {f"new-{n}" for n in range(rng.randint(0, 8))}
    for n, slug in enumerate(sorted(added)):
        fresh = record(rng, 1000 + n, item_type)
        if previous and rng.random() < 0.4:
            # A new listing with an existing listing's title is still a new listing.
            fresh = PromptRecord(**{**fresh.__dict__, "title": rng.choice(previous).title})
        current.append(PromptRecord(**{**fresh.__dict__, "slug": slug}))
    rng.shuffle(current)
    return previous, current, added, removed, changed


def slugs(items):
    return {item["slug"] for item in items}


class DiffPropertyTests(unittest.TestCase):
    def test_the_diff_reports_exactly_the_changes_that_were_made(self):
        for item_type in ITEM_TYPES:
            rng = random.Random(f"{SEED}-{item_type}")
            for round_number in range(ROUNDS):
                previous, current, added, removed, changed = scenario(rng, item_type)
                with self.subTest(item_type=item_type, round=round_number):
                    diff = compare_catalog_records(rows(previous), rows(current))
                    self.assertEqual(slugs(diff.added), added)
                    self.assertEqual(slugs(diff.removed), removed)
                    self.assertEqual({item.current["slug"] for item in diff.changed}, changed)

    def test_every_record_is_counted_exactly_once_on_each_side(self):
        rng = random.Random(f"{SEED}-counts")
        for round_number in range(ROUNDS):
            previous, current, *_ = scenario(rng, rng.choice(ITEM_TYPES))
            with self.subTest(round=round_number):
                diff = compare_catalog_records(rows(previous), rows(current))
                matched = len(diff.changed) + diff.unchanged
                self.assertEqual(len(diff.added) + matched, len(current))
                self.assertEqual(len(diff.removed) + matched, len(previous))
                self.assertEqual(diff.has_changes, bool(diff.added or diff.removed or diff.changed))

    def test_a_catalog_compared_with_itself_has_no_changes(self):
        rng = random.Random(f"{SEED}-self")
        for round_number in range(ROUNDS):
            previous, current, *_ = scenario(rng, rng.choice(ITEM_TYPES))
            for catalog in (previous, current):
                with self.subTest(round=round_number):
                    diff = compare_catalog_records(rows(catalog), rows(catalog))
                    self.assertFalse(diff.has_changes)
                    self.assertEqual(diff.unchanged, len(catalog))

    def test_swapping_the_sides_swaps_added_and_removed(self):
        rng = random.Random(f"{SEED}-swap")
        for round_number in range(ROUNDS):
            previous, current, *_ = scenario(rng, rng.choice(ITEM_TYPES))
            with self.subTest(round=round_number):
                forward = compare_catalog_records(rows(previous), rows(current))
                backward = compare_catalog_records(rows(current), rows(previous))
                self.assertEqual(slugs(forward.added), slugs(backward.removed))
                self.assertEqual(slugs(forward.removed), slugs(backward.added))
                self.assertEqual(
                    {item.current["slug"] for item in forward.changed},
                    {item.current["slug"] for item in backward.changed},
                )

    def test_a_catalog_without_slugs_pairs_each_title_once(self):
        # A TXT catalog keeps only titles and descriptions, and titles can repeat.
        rng = random.Random(f"{SEED}-titles")
        for round_number in range(ROUNDS):
            catalog = [record(rng, number, "prompt") for number in range(rng.randint(1, 20))]
            copies = rng.randint(1, 3)
            # Same title, different descriptions: only an exact pairing is unchanged.
            catalog += [PromptRecord(**{**catalog[0].__dict__, "slug": f"copy-{n}",
                                        "description": f"copy number {n}"})
                        for n in range(copies)]
            rng.shuffle(catalog)
            bare = [{"title": item.title, "description": item.description} for item in catalog]
            # The old catalog lists the same records in another order.
            rng.shuffle(bare)
            with self.subTest(round=round_number):
                diff = compare_catalog_records(bare, rows(catalog))
                self.assertFalse(diff.has_changes)
                self.assertEqual(diff.unchanged, len(catalog))
                one_short = compare_catalog_records(bare[1:], rows(catalog))
                self.assertEqual((len(one_short.added), one_short.unchanged),
                                 (1, len(catalog) - 1))

    def test_a_catalog_read_back_from_any_format_matches_its_records(self):
        # TXT records only titles and descriptions, so its matches go by title.
        rng = random.Random(f"{SEED}-formats")
        for export_format in ("json", "ndjson", "csv", "html", "markdown", "txt"):
            for item_type in ITEM_TYPES:
                catalog = [record(rng, number, item_type) for number in range(25)]
                with self.subTest(format=export_format, item_type=item_type), \
                        TemporaryDirectory() as directory:
                    path = write_export_to_path(
                        Path(directory) / f"c.{FORMAT_EXTENSIONS[export_format]}",
                        catalog, export_format, overwrite=True, item_type=item_type,
                    )
                    diff = compare_catalog_records(load_catalog(path), rows(catalog))
                    self.assertFalse(diff.has_changes, (export_format, diff))
                    self.assertEqual(diff.unchanged, len(catalog))


if __name__ == "__main__":
    unittest.main()

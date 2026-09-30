import unittest

from promptbase_exporter.cli import build_parser, normalize_options
from promptbase_exporter.models import PromptRecord
from promptbase_exporter.pipeline import (
    SPLIT_MODES,
    Selection,
    split_modes,
    without_description,
)
from promptbase_exporter.web import ExportRequest, build_request_config


def make(slug, *, created, domain="text", prompt_type="gpt", price=0.0, views=0, sales=0,
         rating=0.0, description="d"):
    return PromptRecord(
        title=slug.title(), description=description, slug=slug, prompt_type=prompt_type,
        domain=domain, created=created, price=price, views=views, sales=sales, rating=rating,
    )


RECORDS = [
    make("newest", created=300, domain="image", prompt_type="midjourney", price=5.0, views=1,
         sales=0, rating=4.8),
    make("middle", created=200, domain="text", prompt_type="claude", price=0.0, views=50,
         sales=3, rating=4.0),
    make("oldest", created=100, domain="text", prompt_type="gpt", price=2.5, views=10,
         sales=1, rating=0.0),
]


def slugs(records):
    return [record.slug for record in records]


class SelectionTests(unittest.TestCase):
    def test_default_keeps_everything_newest_first(self):
        shuffled = [RECORDS[2], RECORDS[0], RECORDS[1]]
        self.assertEqual(slugs(Selection().apply(shuffled)), ["newest", "middle", "oldest"])

    def test_limit_is_applied_after_sorting(self):
        selected = Selection(sort="views", limit=2).apply(RECORDS)
        self.assertEqual(slugs(selected), ["middle", "oldest"])  # top two by views, not by date

    def test_limit_is_applied_after_filtering(self):
        selected = Selection(domains=frozenset({"text"}), limit=1).apply(RECORDS)
        self.assertEqual(slugs(selected), ["middle"])

    def test_price_modes(self):
        self.assertEqual(slugs(Selection(price="free").apply(RECORDS)), ["middle"])
        self.assertEqual(slugs(Selection(price="paid").apply(RECORDS)), ["newest", "oldest"])
        self.assertEqual(len(Selection(price="all").apply(RECORDS)), 3)

    def test_each_filter_narrows_the_result(self):
        cases = {
            "domain": (Selection(domains=frozenset({"image"})), ["newest"]),
            "type": (Selection(prompt_types=frozenset({"gpt", "claude"})), ["middle", "oldest"]),
            "min_price": (Selection(min_price=2.5), ["newest", "oldest"]),
            "max_price": (Selection(max_price=2.5), ["middle", "oldest"]),
            "since": (Selection(since_created=200), ["newest", "middle"]),
            "until": (Selection(until_created=200), ["middle", "oldest"]),
            "min_sales": (Selection(min_sales=1), ["middle", "oldest"]),
            "min_rating": (Selection(min_rating=4.0), ["newest", "middle"]),
        }
        for label, (selection, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(slugs(selection.apply(RECORDS)), expected)

    def test_nothing_matching_is_an_empty_list(self):
        self.assertEqual(Selection(domains=frozenset({"video"})).apply(RECORDS), [])
        self.assertEqual(Selection().apply([]), [])

    def test_it_does_not_change_its_input(self):
        original = list(RECORDS)
        Selection(sort="views", limit=1).apply(RECORDS)
        self.assertEqual(RECORDS, original)

    def test_a_selection_is_immutable_and_hashable(self):
        selection = Selection(domains=frozenset({"text"}))
        with self.assertRaises(AttributeError):
            selection.limit = 3  # type: ignore[misc]
        self.assertEqual(hash(selection), hash(Selection(domains=frozenset({"text"}))))

    def test_an_unknown_sort_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Unsupported sort option"):
            Selection(sort="bogus").apply(RECORDS)


class HelperTests(unittest.TestCase):
    def test_split_modes(self):
        self.assertEqual(split_modes("split"), SPLIT_MODES)
        self.assertEqual(SPLIT_MODES, ("all", "text", "image"))
        for mode in ("all", "text", "image"):
            self.assertEqual(split_modes(mode), (mode,))

    def test_without_description(self):
        records = [make("a", created=1, description=""), make("b", created=2)]
        self.assertEqual(slugs(without_description(records)), ["a"])
        self.assertEqual(without_description([]), [])


class SurfacesAgreeTests(unittest.TestCase):
    """The CLI and the web form must mean the same thing by the same filters."""

    def cli_selection(self, *argv):
        return normalize_options(build_parser().parse_args(["@acb", *argv])).selection

    def test_equivalent_inputs_build_equal_selections(self):
        cases = [
            ([], {}),
            (["--sort", "views", "--limit", "5"], {"sort": "views", "limit": "5"}),
            (["--free-only"], {"price_filter": "free"}),
            (["--paid-only", "--sort", "price"], {"price_filter": "paid", "sort": "price"}),
            (["--min-price", "3", "--max-price", "5"], {"min_price": "3", "max_price": "5"}),
            (["--min-sales", "2", "--min-rating", "4.5"],
             {"min_sales": "2", "min_rating": "4.5"}),
            (["--domain", "Text, image", "--type", "GPT"],
             {"domain": "Text, image", "prompt_type": "GPT"}),
            (["--since", "2026-01-01", "--until", "2026-12-31"],
             {"since": "2026-01-01", "until": "2026-12-31"}),
        ]
        for argv, form in cases:
            with self.subTest(argv=argv):
                web_selection = build_request_config({"profile": "acb", **form}).selection()
                self.assertEqual(self.cli_selection(*argv), web_selection)

    def test_a_request_built_directly_still_gets_its_date_bounds(self):
        # build_request_config parses since/until; a caller that builds the
        # request itself only sets the text, and the selection must still use it.
        direct = ExportRequest(profile_input="acb", since="2026-01-01", until="2026-01-02")
        parsed = build_request_config(
            {"profile": "acb", "since": "2026-01-01", "until": "2026-01-02"}
        )
        self.assertIsNone(direct.since_created)
        self.assertEqual(direct.selection(), parsed.selection())
        self.assertIsNotNone(direct.selection().since_created)

    def test_the_date_bounds_cover_the_whole_day(self):
        selection = self.cli_selection("--since", "2026-01-01", "--until", "2026-01-01")
        assert selection.since_created is not None and selection.until_created is not None
        self.assertEqual(selection.until_created - selection.since_created, 86_399_999)


if __name__ == "__main__":
    unittest.main()

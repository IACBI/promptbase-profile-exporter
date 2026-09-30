import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from promptbase_exporter.diffing import (
    compare_catalog_records,
    compare_catalogs,
    diff_to_dict,
    format_diff_report,
    load_catalog,
    write_diff_report,
)
from promptbase_exporter.formatting import write_export
from promptbase_exporter.models import PromptRecord


def record(title, description, slug=None):
    return PromptRecord(
        title=title,
        description=description,
        slug=slug or title.lower().replace(" ", "-"),
        prompt_type="gpt",
        domain="text",
        created=1,
        price=0,
    )


class DiffingTests(unittest.TestCase):
    def test_load_text_catalog_and_compare_by_title_fallback(self):
        previous_text = (
            "1.\n"
            "Title: Alpha\n"
            "Description:\n"
            "Old description\n"
        )

        with TemporaryDirectory() as directory:
            path = Path(directory) / "previous.txt"
            path.write_text(previous_text, encoding="utf-8")

            previous = load_catalog(path)

        diff = compare_catalogs(
            previous,
            [
                record("Alpha", "New description", slug="alpha-slug"),
                record("Beta", "Beta description"),
            ],
        )

        self.assertEqual(len(diff.added), 1)
        self.assertEqual(len(diff.changed), 1)
        self.assertEqual(diff.changed[0].fields, ("description",))

    def test_compare_json_catalog_by_slug(self):
        previous = [
            {
                "title": "Alpha",
                "description": "Same",
                "slug": "alpha",
                "type": "gpt",
                "domain": "text",
                "price": 0,
            }
        ]

        diff = compare_catalogs(previous, [record("Alpha Renamed", "Same", slug="alpha")])

        self.assertEqual(len(diff.added), 0)
        self.assertEqual(len(diff.removed), 0)
        self.assertEqual(diff.changed[0].fields, ("title",))

    def test_format_diff_report_contains_summary(self):
        diff = compare_catalogs([], [record("Alpha", "Description")])

        report = format_diff_report(diff)

        self.assertIn("# PromptBase Catalog Diff", report)
        self.assertIn("- Added: 1", report)
        self.assertIn("## Added", report)

    def test_round_trip_reports_no_changes_for_whole_number_prices(self):
        # CSV stores 2.0 as the text "2.0"; it must not read as a price change.
        records = [
            PromptRecord("Free", "d", "free", "gpt", "text", 3, 0.0),
            PromptRecord("Whole", "d", "whole", "gpt", "text", 2, 2.0),
            PromptRecord("Cents", "d", "cents", "gpt", "text", 1, 1.99),
        ]
        with TemporaryDirectory() as directory:
            for export_format in ("txt", "markdown", "json", "csv"):
                output_path = write_export(Path(directory), "acb", "all", records, export_format)

                diff = compare_catalogs(load_catalog(output_path), records)

                self.assertFalse(diff.has_changes, export_format)

    def test_price_change_is_still_detected_in_csv(self):
        with TemporaryDirectory() as directory:
            output_path = write_export(
                Path(directory),
                "acb",
                "all",
                [PromptRecord("Whole", "d", "whole", "gpt", "text", 1, 2.0)],
                "csv",
            )

            diff = compare_catalogs(
                load_catalog(output_path),
                [PromptRecord("Whole", "d", "whole", "gpt", "text", 1, 3.0)],
            )

        self.assertEqual(diff.changed[0].fields, ("price",))

    def test_html_and_csv_safe_catalogs_round_trip_without_changes(self):
        records = [
            PromptRecord("=Formula title", "-starts with minus", "f", "gpt", "text", 2, 2.0),
            PromptRecord("<b>Markup</b>", "Line\nbreak", "m", "gpt", "image", 1, 0.0),
        ]
        with TemporaryDirectory() as directory:
            html_path = write_export(Path(directory), "acb", "all", records, "html")
            csv_path = write_export(
                Path(directory), "acb", "all", records, "csv", csv_safe=True
            )

            for path in (html_path, csv_path):
                loaded = load_catalog(path)
                self.assertEqual([item["title"] for item in loaded], [r.title for r in records])
                self.assertFalse(compare_catalogs(loaded, records).has_changes, path.suffix)

    def test_load_catalog_rejects_unknown_extension(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.yaml"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_catalog(path)

    def test_load_catalog_round_trips_supported_export_formats(self):
        records = [
            record("Alpha", "Alpha description"),
            record("Beta", "Beta description"),
        ]
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            for export_format in ("txt", "markdown", "json", "csv"):
                output_path = write_export(output_dir, "acb", "all", records, export_format)

                loaded = load_catalog(output_path)

                self.assertEqual([item["title"] for item in loaded], ["Alpha", "Beta"])
                self.assertEqual(
                    [item["description"] for item in loaded],
                    ["Alpha description", "Beta description"],
                )

    def test_load_text_catalog_ignores_header_like_description_lines(self):
        records = [
            record(
                "Alpha",
                "Alpha description\n5.\nStill part of Alpha.",
            )
        ]
        with TemporaryDirectory() as directory:
            output_path = write_export(Path(directory), "acb", "all", records, "txt")

            loaded = load_catalog(output_path)

        self.assertEqual(len(loaded), 1)
        self.assertIn("Still part of Alpha.", loaded[0]["description"])

    def test_load_markdown_catalog_ignores_header_like_description_lines(self):
        records = [
            record(
                "Alpha",
                "Alpha description\n\n## 5. Still part of Alpha\nMore details.",
            )
        ]
        with TemporaryDirectory() as directory:
            output_path = write_export(Path(directory), "acb", "all", records, "markdown")

            loaded = load_catalog(output_path)

        self.assertEqual(len(loaded), 1)
        self.assertIn("## 5. Still part of Alpha", loaded[0]["description"])


def row(title, slug="", description="Body", **metadata):
    return {"title": title, "slug": slug, "description": description, **metadata}


class CatalogRecordComparisonTests(unittest.TestCase):
    def test_metadata_missing_on_either_side_is_not_a_change(self):
        # A TXT catalog records no type/domain/price; that is not a change in
        # either direction.
        rich = [row("Alpha", slug="alpha", type="gpt", domain="text", price=3.0)]
        bare = [row("Alpha")]

        self.assertFalse(compare_catalog_records(rich, bare).has_changes)
        self.assertFalse(compare_catalog_records(bare, rich).has_changes)

    def test_detects_added_removed_and_changed(self):
        previous = [
            row("Kept", slug="kept", price="1.0"),
            row("Gone", slug="gone"),
            row("Old name", slug="renamed", type="gpt"),
        ]
        current = [
            row("Kept", slug="kept", price=1),
            row("Fresh", slug="fresh"),
            row("New name", slug="renamed", type="claude"),
        ]

        diff = compare_catalog_records(previous, current)

        self.assertEqual([item["slug"] for item in diff.added], ["fresh"])
        self.assertEqual([item["slug"] for item in diff.removed], ["gone"])
        self.assertEqual(diff.changed[0].fields, ("title", "type"))
        self.assertEqual(diff.unchanged, 1)

    def test_duplicate_match_is_reported_as_added(self):
        previous = [row("Same", slug="same")]
        current = [row("Same", slug="same"), row("Same", slug="same")]

        diff = compare_catalog_records(previous, current)

        self.assertEqual(diff.unchanged, 1)
        self.assertEqual(len(diff.added), 1)


class DiffReportTests(unittest.TestCase):
    def _diff(self):
        previous = [
            row("Old title", slug="a", description="short", type="gpt", price="2.0"),
            row("Removed one", slug="gone"),
        ]
        current = [
            row("New title", slug="a", description="a longer text", type="gpt", price=3.5),
            row("Added one", slug="new", url="https://promptbase.com/prompt/new"),
        ]
        return compare_catalog_records(previous, current)

    def test_markdown_report_shows_old_and_new_values(self):
        report = format_diff_report(self._diff())

        self.assertIn("  Changed fields: title, description, price", report)
        self.assertIn('  - title: "Old title" -> "New title"', report)
        self.assertIn("  - description: 5 -> 13 characters", report)
        self.assertIn("  - price: 2 -> 3.5", report)
        self.assertIn("- Added one (new)", report)
        self.assertIn("- Removed one (gone)", report)

    def test_empty_value_is_labelled(self):
        diff = compare_catalog_records([row("", slug="a")], [row("Titled", slug="a")])
        self.assertIn('  - title: (empty) -> "Titled"', format_diff_report(diff))

    def test_json_report_structure(self):
        data = diff_to_dict(self._diff())

        self.assertTrue(data["has_changes"])
        self.assertEqual(
            data["summary"], {"added": 1, "removed": 1, "changed": 1, "unchanged": 0}
        )
        self.assertEqual(
            data["added"],
            [{"title": "Added one", "slug": "new", "url": "https://promptbase.com/prompt/new"}],
        )
        self.assertEqual(data["removed"][0]["slug"], "gone")
        fields = data["changed"][0]["fields"]
        self.assertEqual(fields["price"], {"previous": 2.0, "current": 3.5})
        self.assertEqual(fields["title"], {"previous": "Old title", "current": "New title"})
        self.assertEqual(fields["description"]["current"], "a longer text")

    def test_json_report_for_identical_catalogs(self):
        data = diff_to_dict(compare_catalog_records([row("A", slug="a")], [row("A", slug="a")]))
        self.assertFalse(data["has_changes"])
        self.assertEqual(data["changed"], [])

    def test_write_diff_report_picks_format_from_extension(self):
        diff = self._diff()
        with TemporaryDirectory() as directory:
            json_path = write_diff_report(Path(directory) / "nested" / "diff.JSON", diff)
            markdown_path = write_diff_report(Path(directory) / "diff.md", diff)

            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), diff_to_dict(diff))
            self.assertTrue(
                markdown_path.read_text(encoding="utf-8").startswith("# PromptBase Catalog Diff")
            )


if __name__ == "__main__":
    unittest.main()

import json
import random
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.diffing import (
    _changed_fields,
    _pair_records,
    _slug_key,
    _title_key,
    compare_catalog_records,
    compare_catalogs,
    diff_to_dict,
    format_diff_report,
    listing_kind,
    load_catalog,
    write_diff_report,
)
from promptbase_exporter.formatting import (
    FORMAT_EXTENSIONS,
    count_records_in_text,
    format_records,
    format_records_as_text,
    parse_txt_catalog,
    write_export,
    write_export_to_path,
)
from promptbase_exporter.layout import write_markdown_files
from promptbase_exporter.models import ITEM_TYPES, PromptRecord


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

            html_loaded = load_catalog(html_path)
            csv_loaded = load_catalog(csv_path, csv_safe=True)

        for loaded in (html_loaded, csv_loaded):
            self.assertEqual([item["title"] for item in loaded], [r.title for r in records])
            self.assertFalse(compare_catalogs(loaded, records).has_changes)

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

    def test_cleared_metadata_in_rich_catalogs_is_reported(self):
        previous = [row("A", slug="a", type="gpt", domain="text", price=2.0)]
        current = [row("A", slug="a", type="", domain="", price="")]

        diff = compare_catalog_records(previous, current)

        self.assertEqual(diff.changed[0].fields, ("type", "domain", "price"))

    def test_cleared_metadata_is_reported_for_loaded_json_catalogs(self):
        with TemporaryDirectory() as directory:
            old = Path(directory) / "old.json"
            new = Path(directory) / "new.json"
            old.write_text(
                '[{"title": "A", "slug": "a", "description": "d", "type": "gpt"}]',
                encoding="utf-8",
            )
            new.write_text(
                '[{"title": "A", "slug": "a", "description": "d", "type": null}]',
                encoding="utf-8",
            )

            diff = compare_catalog_records(load_catalog(old), load_catalog(new))

        self.assertEqual(diff.changed[0].fields, ("type",))

    def test_txt_catalog_on_either_side_reports_no_metadata_change(self):
        with TemporaryDirectory() as directory:
            records = [PromptRecord("A", "d", "a", "gpt", "text", 1, 2.0)]
            txt = load_catalog(write_export(Path(directory), "x", "all", records, "txt"))
            rich = load_catalog(write_export(Path(directory), "x", "all", records, "json"))

        self.assertNotIn("type", txt[0])
        self.assertFalse(compare_catalog_records(rich, txt).has_changes)
        self.assertFalse(compare_catalog_records(txt, rich).has_changes)

    def test_duplicate_match_is_reported_as_added(self):
        previous = [row("Same", slug="same")]
        current = [row("Same", slug="same"), row("Same", slug="same")]

        diff = compare_catalog_records(previous, current)

        self.assertEqual(diff.unchanged, 1)
        self.assertEqual(len(diff.added), 1)


class CsvApostropheTests(unittest.TestCase):
    """Text that genuinely starts with an apostrophe must survive CSV round trips."""

    TITLES = ("'=SUM(A1:A2)", "''+x", "'plain quote", "=formula", "normal")

    def _records(self, titles):
        return [
            PromptRecord(title, "d", f"s{index}", "gpt", "text", index, 1.0)
            for index, title in enumerate(titles)
        ]

    def test_plain_and_safe_csv_round_trip_without_changes(self):
        records = self._records(self.TITLES)
        with TemporaryDirectory() as directory:
            for safe in (False, True):
                path = write_export(
                    Path(directory) / str(safe), "acb", "all", records, "csv", csv_safe=safe
                )
                diff = compare_catalogs(load_catalog(path, csv_safe=safe), records)
                self.assertFalse(diff.has_changes, f"csv_safe={safe}")

    def test_safe_and_plain_csv_files_compare_equal_both_ways(self):
        records = self._records(self.TITLES)
        with TemporaryDirectory() as directory:
            plain = load_catalog(write_export(Path(directory) / "p", "a", "all", records, "csv"))
            safe = load_catalog(
                write_export(Path(directory) / "s", "a", "all", records, "csv", csv_safe=True),
                csv_safe=True,
            )

        self.assertFalse(compare_catalog_records(plain, safe).has_changes)
        self.assertFalse(compare_catalog_records(safe, plain).has_changes)

    def test_real_title_change_in_csv_is_still_reported(self):
        with TemporaryDirectory() as directory:
            path = write_export(
                Path(directory), "a", "all", self._records(["=old"]), "csv", csv_safe=True
            )
            diff = compare_catalogs(load_catalog(path, csv_safe=True), self._records(["=new"]))

        self.assertEqual(diff.changed[0].fields, ("title",))

    def test_escape_equivalence_is_limited_to_csv_sources(self):
        # Outside CSV an apostrophe is always data.
        diff = compare_catalog_records([row("'=x", slug="a")], [row("=x", slug="a")])
        self.assertEqual(diff.changed[0].fields, ("title",))

    def test_removed_real_apostrophe_is_reported_against_plain_csv(self):
        # Codex: an older "'=x" and a newer plain-CSV "=x" must not look equal.
        with TemporaryDirectory() as directory:
            plain = write_export(Path(directory), "a", "all", self._records(["=x"]), "csv")
            current = load_catalog(plain)

        diff = compare_catalog_records([row("'=x", slug="s0", description="d")], current)
        self.assertEqual(diff.changed[0].fields, ("title",))

    def test_plain_csv_is_read_verbatim(self):
        with TemporaryDirectory() as directory:
            path = write_export(Path(directory), "a", "all", self._records(["'=x"]), "csv")
            self.assertEqual(load_catalog(path)[0]["title"], "'=x")

    def test_safe_csv_matches_slugless_txt_by_title(self):
        # Codex: TXT has no slug, so records are matched by title; the safe
        # CSV's escaped title must still match.
        records = self._records(["=Formula", "normal"])
        with TemporaryDirectory() as directory:
            safe = load_catalog(
                write_export(Path(directory) / "s", "a", "all", records, "csv", csv_safe=True),
                csv_safe=True,
            )
            txt = load_catalog(write_export(Path(directory) / "t", "a", "all", records, "txt"))

        for previous, current in ((safe, txt), (txt, safe)):
            diff = compare_catalog_records(previous, current)
            self.assertFalse(diff.has_changes)
            self.assertEqual(diff.unchanged, 2)


    def test_bom_csv_from_other_tools_is_read_verbatim(self):
        # Codex: a BOM alone (Excel's "CSV UTF-8") must not switch on safe mode.
        with TemporaryDirectory() as directory:
            plain = write_export(Path(directory), "a", "all", self._records(["'=SUM(A1)"]), "csv")
            excel = Path(directory) / "excel.csv"
            excel.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())

            loaded = load_catalog(excel)

        self.assertEqual(loaded[0]["title"], "'=SUM(A1)")
        # The BOM is stripped, so the first column is still "title".
        self.assertNotIn("\ufefftitle", loaded[0])

    def test_safe_csv_without_the_flag_is_read_verbatim(self):
        with TemporaryDirectory() as directory:
            path = write_export(
                Path(directory), "a", "all", self._records(["=x"]), "csv", csv_safe=True
            )
            self.assertEqual(load_catalog(path)[0]["title"], "'=x")
            self.assertEqual(load_catalog(path, csv_safe=True)[0]["title"], "=x")


class MarkdownPlaceholderTests(unittest.TestCase):
    def test_literal_unknown_metadata_survives_markdown(self):
        # Codex: a real type or domain named "unknown" must not read as empty.
        records = [PromptRecord("A", "d", "a", "unknown", "unknown", 1, 2.0)]
        with TemporaryDirectory() as directory:
            markdown = load_catalog(
                write_export(Path(directory), "x", "all", records, "markdown")
            )
            rich = load_catalog(write_export(Path(directory), "x", "all", records, "json"))

        self.assertEqual((markdown[0]["type"], markdown[0]["domain"]), ("unknown", "unknown"))
        self.assertFalse(compare_catalog_records(rich, markdown).has_changes)
        self.assertFalse(compare_catalog_records(markdown, rich).has_changes)

    def test_empty_metadata_is_written_empty_and_compares_equal(self):
        records = [PromptRecord("A", "d", "a", "", "", 1, 2.0)]
        with TemporaryDirectory() as directory:
            markdown_path = write_export(Path(directory), "x", "all", records, "markdown")
            text = markdown_path.read_text(encoding="utf-8")
            markdown = load_catalog(markdown_path)
            rich = load_catalog(write_export(Path(directory), "x", "all", records, "json"))

        self.assertIn("- Domain:\n- Type:\n", text)
        self.assertNotIn("unknown", text.split("- Created:")[0])
        self.assertEqual((markdown[0]["type"], markdown[0]["domain"]), ("", ""))
        self.assertFalse(compare_catalog_records(markdown, rich).has_changes)
        self.assertFalse(compare_catalog_records(rich, markdown).has_changes)

    def test_markdown_type_that_is_cleared_is_reported(self):
        with TemporaryDirectory() as directory:
            before = load_catalog(
                write_export(
                    Path(directory) / "b", "x", "all",
                    [PromptRecord("A", "d", "a", "gpt", "text", 1, 2.0)], "markdown",
                )
            )
            after = load_catalog(
                write_export(
                    Path(directory) / "a", "x", "all",
                    [PromptRecord("A", "d", "a", "", "text", 1, 2.0)], "markdown",
                )
            )

        self.assertEqual(compare_catalog_records(before, after).changed[0].fields, ("type",))


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

    def test_json_report_stays_valid_json_for_non_finite_prices(self):
        diff = compare_catalog_records(
            [row("A", slug="a", price="nan")], [row("A", slug="a", price=2.0)]
        )
        # allow_nan=False raises on NaN/Infinity, as strict JSON parsers would.
        text = json.dumps(diff_to_dict(diff), allow_nan=False)
        fields = json.loads(text)["changed"][0]["fields"]
        self.assertEqual(fields["price"], {"previous": None, "current": 2.0})

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

    def test_a_report_replaces_a_symbolic_link_instead_of_writing_through_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "elsewhere.txt"
            target.write_text("keep", encoding="utf-8")
            try:
                (root / "diff.md").symlink_to(target)
            except OSError:
                self.skipTest("creating symbolic links is not permitted here")
            write_diff_report(root / "diff.md", self._diff())
            self.assertFalse((root / "diff.md").is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")


class StrictLoadTests(unittest.TestCase):
    def test_non_record_entries_are_skipped_unless_strict(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps([{"title": "A"}, None, 3, {"title": "B"}]), "utf-8")
            self.assertEqual([r["title"] for r in load_catalog(path)], ["A", "B"])
            with self.assertRaisesRegex(ValueError, "entry 2 of the catalog is not a record"):
                load_catalog(path, strict=True)


class StrictTextCatalogTests(unittest.TestCase):
    """Strict loading refuses the text a TXT or Markdown reader would drop, and accepts
    every catalog the tool writes: an --update-file rewrite depends on both."""

    def _load(self, name, text, *, strict):
        with TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_text(text, encoding="utf-8")
            return load_catalog(path, strict=strict)

    def test_text_before_the_first_txt_record_is_refused_only_when_strict(self):
        text = "My notes\n1.\nTitle: A\nDescription:\nx\n"
        self.assertEqual([r["title"] for r in self._load("c.txt", text, strict=False)], ["A"])
        with self.assertRaisesRegex(ValueError, "line 1 is text outside any record: My notes"):
            self._load("c.txt", text, strict=True)

    def test_a_txt_header_the_reader_skips_is_refused_only_when_strict(self):
        # "Description: oops" is not a header, so the reader drops record B and what follows.
        text = "1.\nTitle: A\nDescription:\nx\n2.\nTitle: B\nDescription: oops\ny\n"
        self.assertEqual([r["title"] for r in self._load("c.txt", text, strict=False)], ["A"])
        with self.assertRaisesRegex(ValueError, "line 5 is text outside any record"):
            self._load("c.txt", text, strict=True)

    def test_blank_lines_and_an_empty_txt_catalog_are_not_stray(self):
        self.assertEqual(self._load("c.txt", "\n  \n", strict=True), [])
        self.assertEqual(self._load("c.txt", "", strict=True), [])
        text = "\n1.\nTitle: A\nDescription:\nx\n\n\n"
        self.assertEqual([r["title"] for r in self._load("c.txt", text, strict=True)], ["A"])

    def test_markdown_text_before_the_first_record_is_refused_only_when_strict(self):
        text = (
            "# Notes\n\n# PromptBase Prompt Export\n\n## 1. A\n\n"
            "- URL: https://promptbase.com/prompt/a\n\nbody\n"
        )
        self.assertEqual([r["title"] for r in self._load("c.md", text, strict=False)], ["A"])
        with self.assertRaisesRegex(ValueError, "line 1 is text outside any record: # Notes"):
            self._load("c.md", text, strict=True)

    def test_a_second_markdown_title_line_is_stray(self):
        text = (
            "# PromptBase Prompt Export\n# PromptBase Prompt Export\n\n"
            "## 1. A\n\n- URL: https://promptbase.com/prompt/a\n"
        )
        with self.assertRaisesRegex(ValueError, "line 2 is text outside any record"):
            self._load("c.md", text, strict=True)

    def test_text_inside_a_record_is_never_stray(self):
        text = (
            "## 1. A\n\n- URL: https://promptbase.com/prompt/a\n\n"
            "Body\n\n## Not a record\n\nmore\n"
        )
        self.assertEqual(len(self._load("c.md", text, strict=True)), 1)

    def test_every_txt_and_markdown_export_loads_strictly_and_reads_back(self):
        records = [
            PromptRecord("Plain", "Body", "plain", "gpt", "text", 3, 1.0,
                         tags=("a", "b"), engine="v2"),
            PromptRecord("Empty description", "", "empty", "gpt", "text", 2, 0.0),
            PromptRecord("Line one\nLine two", "- bullet\n- item\n\n# Heading\n\nEnd",
                         "multi", "midjourney", "image", 1, 2.5),
        ]
        expected_titles = ["Plain", "Empty description", "Line one Line two"]
        with TemporaryDirectory() as directory:
            for item_type in ITEM_TYPES:
                for export_format in ("txt", "markdown"):
                    with self.subTest(item_type=item_type, format=export_format):
                        extension = FORMAT_EXTENSIONS[export_format]
                        path = write_export_to_path(
                            Path(directory) / f"{item_type}.{extension}",
                            records,
                            export_format,
                            overwrite=True,
                            extra_fields=("tags", "engine") if export_format == "markdown" else (),
                            item_type=item_type,
                        )
                        loaded = load_catalog(path, strict=True)
                        self.assertEqual([r["title"] for r in loaded], expected_titles)
                        self.assertEqual(loaded[0]["description"], "Body")
                        self.assertEqual(loaded[1]["description"], "")
                        self.assertEqual(loaded[2]["description"], records[2].description)

    def test_a_markdown_catalog_saved_with_a_byte_order_mark_loads_strictly(self):
        # Notepad saves UTF-8 with a mark; the title line it precedes is still the writer's.
        text = "﻿" + format_records(
            [PromptRecord("A", "Body", "a", "gpt", "text", 1, 0.0)], "markdown"
        )
        loaded = self._load("c.md", text, strict=True)
        self.assertEqual([r["title"] for r in loaded], ["A"])

    def test_a_txt_catalog_with_a_byte_order_mark_is_refused_when_strict(self):
        # The reader does not match the first header after the mark, so its record would be
        # dropped; strict loading says why instead.
        text = "﻿1.\nTitle: A\nDescription:\nx\n"
        with self.assertRaisesRegex(ValueError, "byte order mark is not skipped"):
            self._load("c.txt", text, strict=True)

    def test_an_empty_export_loads_strictly(self):
        with TemporaryDirectory() as directory:
            for export_format in ("txt", "markdown"):
                path = Path(directory) / f"empty.{FORMAT_EXTENSIONS[export_format]}"
                path.write_text(format_records([], export_format), encoding="utf-8")
                self.assertEqual(load_catalog(path, strict=True), [])

    def test_a_per_prompt_file_from_the_files_layout_is_refused_when_strict(self):
        # Not a catalog, so a rewrite would replace its front matter and body with a list.
        record = PromptRecord("A", "Body", "a", "gpt", "text", 1, 0.0)
        with TemporaryDirectory() as directory:
            folder = write_markdown_files(Path(directory), "acb", "all", [record])
            (only,) = folder.iterdir()
            with self.assertRaisesRegex(ValueError, "line 1 is text outside any record"):
                load_catalog(only, strict=True)


class MalformedCatalogTests(unittest.TestCase):
    def test_a_csv_row_longer_than_its_header_is_an_error_naming_the_line(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.csv"
            path.write_text("title,slug\nA,a\nB,b,extra\n", encoding="utf-8")
            for csv_safe in (False, True):
                with self.subTest(csv_safe=csv_safe), \
                        self.assertRaisesRegex(ValueError, "line 3 has more fields"):
                    load_catalog(path, csv_safe=csv_safe)

    def test_a_repeated_slug_compares_equal_to_itself(self):
        # Found by fuzzing: the second row with a slug used to be left unmatched.
        rows = [{"title": "A", "slug": "dup", "description": "one"},
                {"title": "A", "slug": "dup", "description": "two"},
                {"title": "B", "slug": "b", "description": "x"}]
        for current in (rows, list(reversed(rows))):
            diff = compare_catalog_records(rows, current)
            self.assertEqual((diff.unchanged, len(diff.added), len(diff.removed)), (3, 0, 0))

    def test_records_without_slug_or_title_compare_equal_to_themselves(self):
        rows = [{"title": "", "slug": "", "description": "one"},
                {"title": "", "slug": "", "description": "two"}]
        diff = compare_catalog_records(rows, list(reversed(rows)))
        self.assertEqual((diff.unchanged, len(diff.added), len(diff.removed)), (2, 0, 0))
        other = [{"title": "", "slug": "", "description": "x"}]
        changed = compare_catalog_records(rows[:1], other)
        self.assertEqual((len(changed.added), len(changed.removed)), (1, 1))


class ReportEscapingTests(unittest.TestCase):
    HOSTILE = "Real<!-- hide -->\n## Summary\n- Added: 0\n[Log in](https://evil.example)"

    def _diff(self):
        return compare_catalog_records(
            [row("Old", slug="old"), row("Kept", slug="kept", type="gpt")],
            [row(self.HOSTILE, slug="new"), row("Kept", slug="kept", type="x[y](z)")],
        )

    def test_a_markdown_report_file_renders_remote_text_as_text(self):
        with TemporaryDirectory() as directory:
            text = write_diff_report(Path(directory) / "diff.md", self._diff()).read_text(
                encoding="utf-8"
            )
        self.assertNotIn("<!--", text)
        self.assertNotIn("[Log in](", text)
        self.assertNotIn("\n## Summary", text)
        self.assertIn(r"\<\!-- hide --\>", text)
        self.assertIn(r'"x\[y\](z)"', text)
        self.assertEqual(text.count("\n## "), 3)  # Added, Changed, Removed only

    def test_the_plain_report_keeps_each_record_on_one_line(self):
        report = format_diff_report(self._diff())
        self.assertNotIn("\n## Summary", report)
        self.assertIn("- Real<!-- hide --> ## Summary - Added: 0 [Log in](https://evil.example)",
                      report)


class ListingKindTests(unittest.TestCase):
    """A slug is unique only within a kind, so the kind is part of a listing's identity."""

    def test_a_prompt_and_an_app_with_one_slug_are_not_paired(self):
        prompt = row("Logo maker", slug="same", url="https://promptbase.com/prompt/same")
        app = row("Story app", slug="same", url="https://promptbase.com/app/same", item_type="app")
        diff = compare_catalog_records([prompt], [app])
        self.assertEqual((len(diff.added), len(diff.removed), len(diff.changed)), (1, 1, 0))

    def test_an_app_with_a_prompts_title_is_not_matched_by_title(self):
        # Live data: many apps carry the title of one of the profile's prompts.
        prompt = row("Logo maker", slug="logo", url="https://promptbase.com/prompt/logo")
        app = row("Logo maker", slug="logo-app", item_type="app")
        diff = compare_catalog_records([prompt], [app])
        self.assertEqual((len(diff.added), len(diff.removed), len(diff.changed)), (1, 1, 0))

    def test_a_record_of_unknown_kind_still_matches_by_title(self):
        from_txt = row("Logo maker")  # TXT stores neither a slug nor a URL
        bundle = row("Logo maker", slug="logo", item_type="bundle")
        diff = compare_catalog_records([from_txt], [bundle])
        self.assertEqual((diff.unchanged, len(diff.added)), (1, 0))

    def test_kind_comes_from_item_type_then_the_url_then_defaults_to_prompt(self):
        by_column = row("A", slug="a", item_type="bundle")
        by_url = row("B", slug="a", url="https://promptbase.com/bundle/a")
        plain = row("C", slug="a")
        self.assertFalse(compare_catalog_records([by_column], [by_url]).added)
        self.assertEqual(len(compare_catalog_records([by_column], [plain]).added), 1)

    def test_a_bundle_markdown_catalog_matches_its_json_twin(self):
        bundle = PromptRecord(
            title="Kit", description="d", slug="kit", prompt_type="gpt", domain="text",
            created=1, price=1.0, item_type="bundle",
        )
        with TemporaryDirectory() as directory:
            as_md = load_catalog(write_export(
                Path(directory), "acb", "all", [bundle], "markdown", item_type="bundle"))
            as_json = load_catalog(write_export(
                Path(directory), "acb", "all", [bundle], "json", item_type="bundle"))
        diff = compare_catalog_records(as_md, as_json)
        self.assertEqual((diff.unchanged, len(diff.added), len(diff.removed)), (1, 0, 0))


class CsvLineBreakTests(unittest.TestCase):
    DESCRIPTION = "first\r\nsecond\rthird\nfourth"

    def _written(self, directory, export_format):
        return write_export(
            Path(directory), "acb", "all", [record("Breaks", self.DESCRIPTION)], export_format
        )

    def test_line_breaks_inside_a_cell_are_read_back_exactly(self):
        with TemporaryDirectory() as directory:
            loaded = load_catalog(self._written(directory, "csv"))
        self.assertEqual(loaded[0]["description"], self.DESCRIPTION)

    def test_a_csv_is_not_reported_changed_against_the_same_records_as_json(self):
        with TemporaryDirectory() as directory:
            as_csv = load_catalog(self._written(directory, "csv"))
            as_json = load_catalog(self._written(directory, "json"))
        self.assertFalse(compare_catalog_records(as_json, as_csv).has_changes)


class TxtParserTests(unittest.TestCase):
    """The TXT reader slices between record boundaries; it must read exactly what the
    single regular expression it replaced read, malformed text included."""

    ORACLE = re.compile(
        r"(?ms)^\d+\.\s*\nTitle:[ \t]*(?P<title>.*?)\nDescription:\n"
        r"(?P<description>.*?)(?=^\d+\.\s*\nTitle:|\Z)"
    )

    def oracle(self, text):
        return [
            {"title": m.group("title").strip(), "description": m.group("description").strip()}
            for m in self.ORACLE.finditer(text)
        ]

    def test_matches_the_reference_reader_on_random_text(self):
        rng = random.Random(20261001)
        pieces = ["1.", "12.", "\n", "Title:", "Description:", " ", "a", "\r", "\t",
                  "x\n", "2. \n", "\n1.\nTitle:", "\nDescription:\n"]
        for _ in range(20000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 30)))
            self.assertEqual(parse_txt_catalog(text), self.oracle(text), repr(text))

    def test_a_blank_title_does_not_run_into_the_next_record(self):
        # Regression: "Title:" followed by whitespace and a newline let the title span
        # the next record, so a blank-titled record swallowed its neighbour.
        text = "1.\nTitle: \nDescription:\nd\n\n2.\nTitle: t\nDescription:\nd2\n"
        self.assertEqual(
            parse_txt_catalog(text),
            [{"title": "", "description": "d"}, {"title": "t", "description": "d2"}],
        )

    def test_a_header_like_description_is_counted_as_the_reader_reads_it(self):
        # TXT cannot escape a description, so a line such as "2." then "Title: x" then
        # "Description:" reads as a second record, both to the reader and to the count.
        # The export then fails validation; see the limitation note in formatting.py.
        record = PromptRecord(
            "Text One", "Intro.\n2.\nTitle: x\nDescription:\nmore", "one", "gpt", "text", 1, 0
        )
        text = format_records_as_text([record])
        self.assertEqual(count_records_in_text(text, "txt"), len(parse_txt_catalog(text)))
        self.assertEqual(count_records_in_text(text, "txt"), 2)


class PairingEquivalenceTests(unittest.TestCase):
    """_pair_records must pair exactly what a plain first-fit scan pairs.

    The reference below is that scan, written out without any index. The random
    catalogs use few distinct titles, slugs, and kinds, so records collide on every
    pass and each rule (slug, title, kind, identity, exact, loose) decides some pair.
    """

    TITLES = ["", "A", "a", " A ", "B", "Untitled"]
    DESCRIPTIONS = ["", "d", "d ", "e", "line\nbreak"]
    SLUGS = ["", "", "s1", "S1", "s2"]
    URLS = [
        "",
        "https://promptbase.com/prompt/s1",
        "https://promptbase.com/bundle/s1",
        "https://www.promptbase.com/app/s2",
        "https://evil.example/bundle/s1",
    ]
    ITEM_TYPES = [None, "", "prompt", "bundle", "app"]
    PRICES = ["absent", 0, 0.0, 1.0, "1.0", "nan", None]

    def random_row(self, rng):
        row = {"title": rng.choice(self.TITLES), "description": rng.choice(self.DESCRIPTIONS)}
        if rng.random() < 0.8:
            row["slug"] = rng.choice(self.SLUGS)
        if rng.random() < 0.4:
            row["item_type"] = rng.choice(self.ITEM_TYPES)
        if rng.random() < 0.5:
            row["url"] = rng.choice(self.URLS)
        if rng.random() < 0.5:
            row["type"] = rng.choice([None, "", "gpt", "claude"])
        if rng.random() < 0.5:
            row["domain"] = rng.choice(["", "text", "image"])
        if rng.random() < 0.5:
            price = rng.choice(self.PRICES)
            if price != "absent":
                row["price"] = price
        return row

    def scenario(self, rng, size):
        previous = [self.random_row(rng) for _ in range(rng.randint(0, size))]
        current = []
        for row in previous:
            roll = rng.random()
            if roll < 0.25:
                continue  # removed
            if roll < 0.5:
                current.append(dict(row))  # unchanged
            else:
                key = rng.choice(["title", "description", "slug", "type", "price", "url"])
                current.append({**row, key: self.random_row(rng).get(key, "")})
            if rng.random() < 0.1:
                current.append(dict(row))  # a repeated record
        current += [self.random_row(rng) for _ in range(rng.randint(0, 5))]
        rng.shuffle(current)
        return previous, current

    def test_pairing_matches_the_plain_scan_on_random_catalogs(self):
        rng = random.Random(20261015)
        for trial in range(3000):
            previous, current = self.scenario(rng, 12)
            with self.subTest(trial=trial):
                self.assertEqual(
                    _pair_records(previous, current), reference_pairs(previous, current)
                )
        for trial in range(200):
            previous, current = self.scenario(rng, 80)
            with self.subTest(large=trial):
                self.assertEqual(
                    _pair_records(previous, current), reference_pairs(previous, current)
                )


def reference_pairs(previous, current):
    """The first-fit pairing as a plain scan over every previous record, in order."""

    def same_listing(record, old):
        if _slug_key(record) and _slug_key(old):
            return False
        kinds = {listing_kind(record.get("item_type"), record.get("url")),
                 listing_kind(old.get("item_type"), old.get("url"))}
        return len(kinds - {""}) <= 1

    matches = [None] * len(current)
    used = set()

    def pair(position, candidates):
        for index in candidates:
            if index not in used:
                matches[position] = index
                used.add(index)
                return

    # Slugs first, then titles, then identity-less records; exact matches before loose.
    for exact in (True, False):
        for position, record in enumerate(current):
            key = _slug_key(record)
            if matches[position] is None and key:
                pair(position, (
                    index for index, old in enumerate(previous)
                    if _slug_key(old) == key and index not in used
                    and (not exact or not _changed_fields(old, record))
                ))
    for exact in (True, False):
        for position, record in enumerate(current):
            title = _title_key(record)
            if matches[position] is None and title:
                pair(position, (
                    index for index, old in enumerate(previous)
                    if _title_key(old) == title and index not in used
                    and same_listing(record, old)
                    and (not exact or not _changed_fields(old, record))
                ))
    for position, record in enumerate(current):
        if matches[position] is None and not _slug_key(record) and not _title_key(record):
            pair(position, (
                index for index, old in enumerate(previous)
                if index not in used and not _slug_key(old) and not _title_key(old)
                and not _changed_fields(old, record)
            ))
    return matches


class PairingWorkTests(unittest.TestCase):
    """How often records are compared, which does not depend on the machine's speed."""

    def comparisons(self, previous, current):
        with patch("promptbase_exporter.diffing._changed_fields",
                   wraps=_changed_fields) as spy:
            compare_catalog_records(previous, current)
        return spy.call_count

    def test_many_records_sharing_a_title_are_each_compared_a_bounded_number_of_times(self):
        n = 2000
        previous = [row("Untitled", description=f"old {i}") for i in range(n)]
        current = [row("Untitled", description=f"new {i}") for i in range(n)]
        self.assertLessEqual(self.comparisons(previous, current), 2 * n)

    def test_identical_records_sharing_a_title_pair_without_scanning_each_other(self):
        n = 2000
        previous = [row("Untitled", description="same") for _ in range(n)]
        self.assertLessEqual(self.comparisons(previous, list(previous)), 2 * n)

    def test_many_records_sharing_a_slug_are_each_compared_a_bounded_number_of_times(self):
        n = 2000
        previous = [row("T", slug="dup", description=f"old {i}") for i in range(n)]
        current = [row("T", slug="dup", description=f"new {i}") for i in range(n)]
        self.assertLessEqual(self.comparisons(previous, current), 2 * n)

    def test_slugged_records_that_cannot_pair_by_title_are_not_compared_at_all(self):
        n = 2000
        previous = [row("Untitled", slug=f"a{i}", description=f"old {i}") for i in range(n)]
        current = [row("Untitled", slug=f"b{i}", description=f"new {i}") for i in range(n)]
        self.assertEqual(self.comparisons(previous, current), 0)


class ListingKindUnificationTests(unittest.TestCase):
    """pb-diff and pb-convert read a record's kind through one helper."""

    def test_the_item_type_column_wins_and_comes_back_as_written(self):
        self.assertEqual(listing_kind("bundle", "https://promptbase.com/app/x"), "bundle")
        self.assertEqual(listing_kind("", "https://promptbase.com:443/bundle/x"), "bundle")
        self.assertEqual(listing_kind("", "https://promptbase.com.evil.example/bundle/x"), "")
        self.assertEqual(listing_kind(" app ", None), "app")
        self.assertEqual(listing_kind("Bundle", None), "Bundle")

    def test_the_url_names_the_kind_only_on_the_promptbase_host(self):
        self.assertEqual(listing_kind(None, "https://promptbase.com/bundle/x"), "bundle")
        self.assertEqual(listing_kind(None, "http://promptbase.com/app/x"), "app")
        self.assertEqual(listing_kind(None, "https://www.promptbase.com/prompt/x"), "prompt")
        self.assertEqual(listing_kind(None, "https://evil.example/bundle/x"), "")
        self.assertEqual(listing_kind(None, "https://promptbase.com.evil.example/app/x"), "")
        self.assertEqual(listing_kind(None, "https://promptbase.com/skill/x"), "")
        self.assertEqual(listing_kind(None, "https://promptbase.com/bundle/"), "bundle")

    def test_nothing_stated_is_unknown(self):
        self.assertEqual(listing_kind(None, None), "")
        self.assertEqual(listing_kind("", ""), "")

    def test_a_foreign_url_is_not_a_slug_in_a_markdown_catalog(self):
        # Markdown reads the slug from the URL; a URL on another site must not supply one.
        text = "# Export\n\n## 1. Logo\n\n- URL: https://evil.example/prompt/logo\n\nBody\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.md"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(load_catalog(path)[0]["slug"], "")


if __name__ == "__main__":
    unittest.main()

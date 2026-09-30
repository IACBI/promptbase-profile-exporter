import csv
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.formatting import (
    CSV_SAFE_MARKER,
    HTML_DATA_ELEMENT_ID,
    RECORD_FIELDS,
    count_written_records,
    csv_escape_formula,
    csv_unescape_formula,
    filter_records,
    filter_records_by_metadata,
    format_records_as_csv,
    format_records_as_json,
    format_records_as_markdown,
    format_records_as_text,
    infer_format_from_path,
    load_html_catalog_data,
    record_to_dict,
    sort_records,
    sorted_newest_to_oldest,
    write_export,
    write_export_to_path,
)
from promptbase_exporter.models import PromptRecord


def record(
    title,
    domain,
    created,
    price=0.0,
    prompt_type=None,
    views=0,
    sales=0,
):
    return PromptRecord(
        title=title,
        description=f"{title} description",
        slug=title.lower().replace(" ", "-"),
        prompt_type=prompt_type or ("gpt" if domain == "text" else "chatgpt-image"),
        domain=domain,
        created=created,
        price=price,
        views=views,
        sales=sales,
    )


class FormattingTests(unittest.TestCase):
    def test_filter_records(self):
        records = [
            record("Text One", "text", 3),
            record("Image One", "image", 2),
            record("Video One", "video", 1),
        ]

        self.assertEqual(len(filter_records(records, "all")), 3)
        self.assertEqual([r.title for r in filter_records(records, "text")], ["Text One"])
        self.assertEqual([r.title for r in filter_records(records, "image")], ["Image One"])

    def test_filter_records_by_metadata(self):
        records = [
            record("Free GPT", "text", 4_000, price=0, prompt_type="gpt"),
            record("Paid Claude", "text", 3_000, price=4.99, prompt_type="claude"),
            record("Paid Image", "image", 2_000, price=2.99, prompt_type="chatgpt-image"),
            record("Video", "video", 1_000, price=9.99, prompt_type="sora"),
        ]

        filtered = filter_records_by_metadata(
            records,
            domains={"text"},
            prompt_types={"claude"},
            paid_only=True,
            min_price=4,
            max_price=5,
        )

        self.assertEqual([r.title for r in filtered], ["Paid Claude"])
        self.assertEqual(
            [r.title for r in filter_records_by_metadata(records, free_only=True)],
            ["Free GPT"],
        )
        self.assertEqual(
            [r.title for r in filter_records_by_metadata(records, since_created=2_500)],
            ["Free GPT", "Paid Claude"],
        )
        self.assertEqual(
            [r.title for r in filter_records_by_metadata(records, until_created=2_500)],
            ["Paid Image", "Video"],
        )

    def test_format_records_as_text(self):
        text = format_records_as_text([record("Text One", "text", 1)])

        self.assertIn("1.\nTitle: Text One\nDescription:\nText One description", text)

    def test_format_records_as_markdown(self):
        text = format_records_as_markdown([record("Text One", "text", 1, views=7, sales=2)])

        self.assertIn("# PromptBase Prompt Export", text)
        self.assertIn("## 1. Text One", text)
        self.assertIn("- Domain: text", text)
        self.assertIn("- Price: 0", text)
        self.assertIn("- Views: 7", text)
        self.assertIn("- Sales: 2", text)

    def test_format_records_as_json(self):
        text = format_records_as_json([record("Text One", "text", 1)])

        self.assertIn('"title": "Text One"', text)
        self.assertIn('"domain": "text"', text)
        self.assertIn('"created_iso": "1970-01-01T00:00:00.001000+00:00"', text)
        self.assertIn('"price": 0.0', text)

    def test_format_records_as_csv(self):
        text = format_records_as_csv([record("Text One", "text", 1)])

        self.assertTrue(
            text.startswith(
                "title,description,slug,url,type,domain,created,created_iso,"
                "price,discount,views,sales,downloads,favorites,rating,reviews\n"
            )
        )
        self.assertIn("Text One", text)

    def test_sort_records(self):
        records = [
            record("Beta", "text", 2, price=2, views=5, sales=0),
            record("Alpha", "text", 3, price=1, views=10, sales=2),
            record("Gamma", "text", 1, price=3, views=1, sales=5),
        ]

        self.assertEqual(
            [r.title for r in sort_records(records, "title")],
            ["Alpha", "Beta", "Gamma"],
        )
        self.assertEqual(
            [r.title for r in sort_records(records, "oldest")],
            ["Gamma", "Beta", "Alpha"],
        )
        self.assertEqual(
            [r.title for r in sort_records(records, "price")],
            ["Gamma", "Beta", "Alpha"],
        )
        self.assertEqual(
            [r.title for r in sort_records(records, "views")],
            ["Alpha", "Beta", "Gamma"],
        )

    def test_write_export_counts_records_by_format(self):
        records = [record("Text One", "text", 2), record("Image One", "image", 1)]
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            for export_format in ("txt", "markdown", "json", "csv"):
                output_path = write_export(output_dir, "acb", "all", records, export_format)
                self.assertEqual(count_written_records(output_path, export_format), 2)

    def test_txt_count_ignores_numbered_lines_inside_description(self):
        adversarial = PromptRecord(
            title="Text One",
            description="A description with a numbered-looking line.\n5.\nStill same prompt.",
            slug="text-one",
            prompt_type="gpt",
            domain="text",
            created=1,
            price=0,
        )
        with TemporaryDirectory() as directory:
            output_path = write_export(Path(directory), "acb", "all", [adversarial], "txt")

            self.assertEqual(count_written_records(output_path, "txt"), 1)

    def test_markdown_count_ignores_heading_lines_inside_description(self):
        adversarial = PromptRecord(
            title="Guide",
            description="Steps:\n\n## 1. First step\n## 2. Second step",
            slug="guide",
            prompt_type="gpt",
            domain="text",
            created=1,
            price=0,
        )
        with TemporaryDirectory() as directory:
            output_path = write_export(Path(directory), "acb", "all", [adversarial], "markdown")

            # The ## headings inside the description must not inflate the count.
            self.assertEqual(count_written_records(output_path, "markdown"), 1)

    def test_markdown_count_matches_multiple_records(self):
        records = [
            record("Alpha", "text", 3),
            record("Beta", "image", 2),
            record("Gamma", "text", 1),
        ]
        with TemporaryDirectory() as directory:
            output_path = write_export(Path(directory), "acb", "all", records, "markdown")

            self.assertEqual(count_written_records(output_path, "markdown"), 3)

    def test_write_export_to_path_refuses_existing_without_overwrite(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            output_path.write_text("[]\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                write_export_to_path(
                    output_path,
                    [record("Text One", "text", 1)],
                    "json",
                    overwrite=False,
                )

    def test_record_fields_match_record_to_dict_keys(self):
        self.assertEqual(tuple(record_to_dict(record("A", "text", 1))), RECORD_FIELDS)

    def test_multiline_title_stays_on_one_line_and_validates(self):
        records = [
            PromptRecord(
                title="Line one\nLine two",
                description="Body",
                slug="multi",
                prompt_type="gpt",
                domain="text",
                created=1,
                price=0.0,
            )
        ]
        with TemporaryDirectory() as directory:
            for export_format in ("txt", "markdown"):
                output_path = write_export(Path(directory), "acb", "all", records, export_format)
                self.assertEqual(count_written_records(output_path, export_format), 1)
                self.assertIn("Line one Line two", output_path.read_text(encoding="utf-8"))

    def test_overwrite_leaves_no_temp_files(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            output_path.write_text("[]\n", encoding="utf-8")

            write_export_to_path(
                output_path, [record("Text One", "text", 1)], "json", overwrite=True
            )

            self.assertEqual(count_written_records(output_path, "json"), 1)
            self.assertEqual(os.listdir(directory), ["catalog.json"])

    def test_failed_overwrite_keeps_existing_file_intact(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            output_path.write_text("[]\n", encoding="utf-8")

            with patch(
                "promptbase_exporter.formatting.os.replace", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    write_export_to_path(
                        output_path, [record("Text One", "text", 1)], "json", overwrite=True
                    )

            self.assertEqual(output_path.read_text(encoding="utf-8"), "[]\n")
            self.assertEqual(os.listdir(directory), ["catalog.json"])

    def test_infer_format_from_path(self):
        self.assertEqual(infer_format_from_path(Path("catalog.txt")), "txt")
        self.assertEqual(infer_format_from_path(Path("catalog.md")), "markdown")
        self.assertEqual(infer_format_from_path(Path("catalog.json")), "json")
        self.assertEqual(infer_format_from_path(Path("catalog.csv")), "csv")

    def test_sorted_newest_to_oldest(self):
        self.assertTrue(sorted_newest_to_oldest([record("A", "text", 2), record("B", "text", 1)]))
        self.assertFalse(sorted_newest_to_oldest([record("A", "text", 1), record("B", "text", 2)]))


def hostile_record(title, description, slug="hostile", **metadata):
    return PromptRecord(
        title=title,
        description=description,
        slug=slug,
        prompt_type=metadata.pop("prompt_type", "gpt"),
        domain=metadata.pop("domain", "text"),
        created=metadata.pop("created", 1_767_225_600_000),
        price=metadata.pop("price", 2.5),
        **metadata,
    )


class MetadataThresholdFilterTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            hostile_record("Low", "d", slug="low", sales=0, rating=0.0),
            hostile_record("Mid", "d", slug="mid", sales=5, rating=4.0),
            hostile_record("High", "d", slug="high", sales=12, rating=4.9),
        ]

    def _slugs(self, **kwargs):
        return [item.slug for item in filter_records_by_metadata(self.records, **kwargs)]

    def test_min_sales_is_inclusive(self):
        self.assertEqual(self._slugs(min_sales=5), ["mid", "high"])

    def test_min_rating_is_inclusive(self):
        self.assertEqual(self._slugs(min_rating=4.9), ["high"])

    def test_zero_thresholds_keep_everything(self):
        self.assertEqual(self._slugs(min_sales=0, min_rating=0), ["low", "mid", "high"])

    def test_thresholds_combine(self):
        self.assertEqual(self._slugs(min_sales=1, min_rating=4.5), ["high"])


class CsvSafeTests(unittest.TestCase):
    def test_formula_like_text_cells_are_prefixed(self):
        text = format_records_as_csv(
            [
                hostile_record(
                    '=HYPERLINK("http://evil.example","x")',
                    "@SUM(A1:A2)",
                    slug="-slug",
                    prompt_type="+gpt",
                )
            ],
            safe=True,
        )
        # The BOM marks the file as --csv-safe (and helps Excel pick UTF-8).
        self.assertTrue(text.startswith(CSV_SAFE_MARKER))
        row = next(csv.DictReader(io.StringIO(text.removeprefix(CSV_SAFE_MARKER))))

        self.assertEqual(row["title"], '\'=HYPERLINK("http://evil.example","x")')
        self.assertEqual(row["description"], "'@SUM(A1:A2)")
        self.assertEqual(row["slug"], "'-slug")
        self.assertEqual(row["type"], "'+gpt")
        # Numeric cells are written by the exporter and stay numeric.
        self.assertEqual(row["price"], "2.5")

    def test_tab_and_carriage_return_prefixes_are_escaped(self):
        for prefix in ("\t", "\r"):
            self.assertEqual(csv_escape_formula(f"{prefix}cmd"), f"'{prefix}cmd")

    def test_plain_text_and_numbers_are_untouched(self):
        self.assertEqual(csv_escape_formula("Plain title"), "Plain title")
        self.assertEqual(csv_escape_formula(-1.5), -1.5)
        self.assertEqual(csv_escape_formula(""), "")

    def test_without_safe_flag_cells_are_verbatim(self):
        text = format_records_as_csv([hostile_record("=1+1", "d")])
        self.assertFalse(text.startswith(CSV_SAFE_MARKER))
        self.assertEqual(next(csv.DictReader(io.StringIO(text)))["title"], "=1+1")

    def test_unescape_reverses_escape_only_for_formula_prefixes(self):
        for value in ("=1+1", "+x", "-x", "@x", "\tx", "\rx"):
            self.assertEqual(csv_unescape_formula(str(csv_escape_formula(value))), value)
        # An apostrophe that does not guard a formula is real data.
        self.assertEqual(csv_unescape_formula("'quoted'"), "'quoted'")
        self.assertEqual(csv_unescape_formula("'"), "'")

    def test_escape_is_reversible_for_text_already_starting_with_apostrophes(self):
        self.assertEqual(csv_escape_formula("'=SUM(A1)"), "''=SUM(A1)")
        self.assertEqual(csv_escape_formula("''+x"), "'''+x")
        self.assertEqual(csv_escape_formula("'plain"), "'plain")
        for value in ("'=SUM(A1)", "''+x", "=x", "'plain", "plain"):
            escaped = str(csv_escape_formula(value))
            self.assertEqual(
                csv_unescape_formula(escaped) if escaped != value else escaped, value
            )
        # Distinct inputs never share an escaped form.
        values = ["=x", "'=x", "''=x"]
        self.assertEqual(len({csv_escape_formula(value) for value in values}), len(values))

    def test_write_export_threads_csv_safe_through(self):
        with TemporaryDirectory() as directory:
            path = write_export(
                Path(directory), "acb", "all", [hostile_record("=x", "d")], "csv", csv_safe=True
            )
            self.assertIn("'=x", path.read_text(encoding="utf-8"))
            self.assertEqual(count_written_records(path, "csv"), 1)


class HtmlExportTests(unittest.TestCase):
    ATTACK = '<script>alert("x")</script><img src=x onerror=alert(1)>'

    def _write(self, directory, records):
        return write_export(Path(directory), "acb", "all", records, "html")

    def test_writes_count_and_embeds_full_records(self):
        records = [
            hostile_record("First", "One", slug="first", views=7, sales=2),
            hostile_record("Second", "Two", slug="second", domain="image"),
        ]
        with TemporaryDirectory() as directory:
            path = self._write(directory, records)
            text = path.read_text(encoding="utf-8")

            self.assertEqual(count_written_records(path, "html"), 2)
            self.assertEqual(
                load_html_catalog_data(text), [record_to_dict(item) for item in records]
            )
        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertIn('href="https://promptbase.com/prompt/first"', text)
        self.assertIn("7 views", text)
        self.assertIn("2026-01-01", text)

    def test_record_values_cannot_inject_markup_or_break_the_data_script(self):
        records = [hostile_record(self.ATTACK, f"{self.ATTACK}\n</script><!--", slug="x")]
        with TemporaryDirectory() as directory:
            path = self._write(directory, records)
            text = path.read_text(encoding="utf-8")

            self.assertEqual(count_written_records(path, "html"), 1)
            loaded = load_html_catalog_data(text)
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("<img", text)
        self.assertIn("&lt;script&gt;alert", text)
        # Exactly the writer's own two script elements survive.
        self.assertEqual(text.count("</script>"), 2)
        self.assertEqual(loaded[0]["description"], f"{self.ATTACK}\n</script><!--")

    def test_description_containing_the_item_marker_is_not_counted(self):
        records = [hostile_record("A", '<li class="prompt"> fake', slug="a")]
        with TemporaryDirectory() as directory:
            path = self._write(directory, records)
            self.assertEqual(count_written_records(path, "html"), 1)

    def test_empty_catalog_is_valid(self):
        with TemporaryDirectory() as directory:
            path = self._write(directory, [])
            self.assertEqual(count_written_records(path, "html"), 0)

    def test_tampered_file_fails_validation(self):
        with TemporaryDirectory() as directory:
            path = self._write(directory, [hostile_record("A", "d", slug="a")])
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace('<li class="prompt">', "<li>"), encoding="utf-8")

            with self.assertRaises(ValueError):
                count_written_records(path, "html")

    def test_missing_data_element_is_rejected(self):
        with self.assertRaises(ValueError):
            load_html_catalog_data("<html><body>no data</body></html>")

    def test_non_list_data_is_rejected(self):
        with self.assertRaises(ValueError):
            load_html_catalog_data(
                f'<script type="application/json" id="{HTML_DATA_ELEMENT_ID}">{{}}</script>'
            )

    def test_infers_html_extensions(self):
        self.assertEqual(infer_format_from_path(Path("catalog.html")), "html")
        self.assertEqual(infer_format_from_path(Path("catalog.HTM")), "html")


if __name__ == "__main__":
    unittest.main()

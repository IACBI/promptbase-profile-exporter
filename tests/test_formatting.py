import csv
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.diffing import compare_catalogs, load_catalog
from promptbase_exporter.formatting import (
    EXPORT_FORMATS,
    HTML_DATA_ELEMENT_ID,
    RECORD_FIELDS,
    UTF8_BOM,
    count_written_records,
    csv_escape_formula,
    csv_unescape_formula,
    expected_filename,
    expected_timestamped_filename,
    filter_records,
    filter_records_by_metadata,
    format_records,
    format_records_as_csv,
    format_records_as_html,
    format_records_as_json,
    format_records_as_markdown,
    format_records_as_text,
    infer_format_from_path,
    load_html_catalog_data,
    parse_extra_fields,
    record_to_dict,
    sort_records,
    sorted_newest_to_oldest,
    write_export,
    write_export_to_path,
)
from promptbase_exporter.models import EXTRA_FIELDS, ITEM_TYPES, PromptRecord


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

    def test_failed_exclusive_write_removes_the_partial_file(self):
        # A leftover would make the retry fail with "already exists".
        real_open = Path.open

        def open_then_fail_on_write(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            original_write = handle.write

            def write(text):
                original_write(text[:5])
                raise OSError("disk full")

            handle.write = write
            return handle

        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            with patch.object(Path, "open", open_then_fail_on_write):
                with self.assertRaisesRegex(OSError, "disk full"):
                    write_export_to_path(
                        output_path, [record("Text One", "text", 1)], "json", overwrite=False
                    )
            self.assertEqual(os.listdir(directory), [])

            write_export_to_path(
                output_path, [record("Text One", "text", 1)], "json", overwrite=False
            )
            self.assertEqual(count_written_records(output_path, "json"), 1)

    def test_failed_exclusive_write_keeps_a_file_another_writer_swapped_in(self):
        real_open = Path.open

        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            other = Path(directory) / "other.json"
            other.write_text("[]\n", encoding="utf-8")

            def open_then_lose_the_race(path, *args, **kwargs):
                handle = real_open(path, *args, **kwargs)

                def write(_text):
                    # Closed first: Windows cannot replace a file held open.
                    handle.close()
                    os.replace(other, output_path)
                    raise OSError("disk full")

                handle.write = write
                return handle

            with patch.object(Path, "open", open_then_lose_the_race):
                with self.assertRaisesRegex(OSError, "disk full"):
                    write_export_to_path(
                        output_path, [record("Text One", "text", 1)], "json", overwrite=False
                    )

            self.assertEqual(output_path.read_text(encoding="utf-8"), "[]\n")

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
        # The BOM makes Excel, the target of this mode, read the file as UTF-8.
        self.assertTrue(text.startswith(UTF8_BOM))
        row = next(csv.DictReader(io.StringIO(text.removeprefix(UTF8_BOM))))

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
        self.assertFalse(text.startswith(UTF8_BOM))
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


def extras_record(**overrides):
    values = dict(
        title="Extras", description="d", slug="extras", prompt_type="gpt", domain="text",
        created=1_767_225_600_000, price=2.5,
        tags=("poster", "icons"), engine="gpt-5.5", nsfw=False, featured=True,
        updated=1_788_566_967_782, last_sale=None, unique_sales=3,
    )
    values.update(overrides)
    return PromptRecord(**values)


class ExtraFieldsTests(unittest.TestCase):
    def test_parse_extra_fields(self):
        self.assertEqual(parse_extra_fields(None), ())
        self.assertEqual(parse_extra_fields(""), ())
        self.assertEqual(parse_extra_fields("all"), EXTRA_FIELDS)
        # Canonical order, whatever order was asked for; - and _ interchangeable.
        self.assertEqual(
            parse_extra_fields("unique-sales, TAGS,last_sale"),
            ("tags", "last_sale", "unique_sales"),
        )
        with self.assertRaisesRegex(ValueError, "unknown extra field.*bogus"):
            parse_extra_fields("tags,bogus")

    def test_default_output_has_no_extra_keys(self):
        self.assertEqual(tuple(record_to_dict(extras_record())), RECORD_FIELDS)
        self.assertEqual(tuple(record_to_dict(extras_record(), ())), RECORD_FIELDS)

    def test_extra_keys_follow_canonical_order_and_keep_unknown_as_null(self):
        data = record_to_dict(extras_record(), ("unique_sales", "last_sale", "tags"))
        self.assertEqual(
            tuple(data)[len(RECORD_FIELDS):],
            ("tags", "last_sale", "last_sale_iso", "unique_sales"),
        )
        self.assertEqual(data["tags"], ["poster", "icons"])
        # Not recorded by PromptBase is null, not a recorded zero.
        self.assertIsNone(data["last_sale"])
        self.assertIsNone(data["last_sale_iso"])
        self.assertEqual(data["unique_sales"], 3)
        updated = record_to_dict(extras_record(), ("updated",))
        self.assertEqual(updated["updated"], 1_788_566_967_782)
        self.assertEqual(updated["updated_iso"], "2026-09-05T00:09:27.782000+00:00")

    def test_json_carries_the_extra_fields(self):
        data = json.loads(format_records_as_json([extras_record()], EXTRA_FIELDS))
        self.assertEqual(data[0]["engine"], "gpt-5.5")
        self.assertIs(data[0]["featured"], True)
        self.assertIs(data[0]["nsfw"], False)

    def test_csv_columns_and_cell_formats(self):
        text = format_records_as_csv([extras_record()], extra_fields=EXTRA_FIELDS)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(
            list(rows[0])[len(RECORD_FIELDS):],
            [
                "tags", "engine", "nsfw", "featured", "updated", "updated_iso",
                "last_sale", "last_sale_iso", "unique_sales",
            ],
        )
        row = rows[0]
        self.assertEqual(row["tags"], "poster, icons")
        self.assertEqual((row["nsfw"], row["featured"]), ("false", "true"))
        self.assertEqual((row["last_sale"], row["last_sale_iso"]), ("", ""))
        self.assertEqual(row["unique_sales"], "3")

    def test_csv_without_extras_is_unchanged(self):
        header = format_records_as_csv([extras_record()]).splitlines()[0]
        self.assertEqual(header, ",".join(RECORD_FIELDS))

    def test_csv_safe_escapes_formula_text_in_extra_cells(self):
        record = extras_record(engine="=cmd|' /C calc'!A0", tags=("@SUM(1)", "ok"))
        text = format_records_as_csv([record], safe=True, extra_fields=("tags", "engine"))
        row = next(csv.DictReader(io.StringIO(text.removeprefix(UTF8_BOM))))
        self.assertEqual(row["engine"], "'=cmd|' /C calc'!A0")
        self.assertEqual(row["tags"], "'@SUM(1), ok")

    def test_markdown_lists_the_extra_fields(self):
        text = format_records_as_markdown(
            [extras_record(engine="", tags=("two\nlines", "b"))], ("tags", "engine", "nsfw")
        )
        self.assertIn("- Tags: two lines, b\n", text)
        self.assertIn("- Engine:\n", text)  # empty stays empty
        self.assertIn("- NSFW: false\n", text)
        self.assertNotIn("Featured", text)

    def test_markdown_with_extras_still_round_trips_through_the_diff_loader(self):
        records = [extras_record(), extras_record(title="Second", slug="second")]
        with TemporaryDirectory() as directory:
            path = write_export_to_path(
                Path(directory) / "catalog.md", records, "markdown",
                overwrite=False, extra_fields=EXTRA_FIELDS,
            )
            self.assertEqual(count_written_records(path, "markdown"), 2)
            loaded = load_catalog(path)
        self.assertEqual([item["title"] for item in loaded], ["Extras", "Second"])
        self.assertEqual(loaded[0]["slug"], "extras")

    def test_html_shows_and_embeds_the_extra_fields(self):
        page = format_records_as_html([extras_record(nsfw=True)], EXTRA_FIELDS)
        for fact in ("engine gpt-5.5", "tags poster, icons", "nsfw true", "featured true",
                     "updated 2026-09-05", "3 unique sales"):
            self.assertIn(fact, page)
        embedded = load_html_catalog_data(page)
        self.assertEqual(embedded[0]["engine"], "gpt-5.5")
        plain = format_records_as_html([extras_record()])
        self.assertNotIn("engine gpt-5.5", plain)
        self.assertNotIn('"engine"', plain)

    def test_html_shows_false_and_zero_when_requested(self):
        record = extras_record(nsfw=False, featured=False, unique_sales=0)
        page = format_records_as_html([record], ("nsfw", "featured", "unique_sales"))
        for fact in ("nsfw false", "featured false", "0 unique sales"):
            self.assertIn(fact, page)
        # Not requested, not shown.
        plain = format_records_as_html([record], ("engine",))
        for fact in ("nsfw", "featured", "unique sales"):
            self.assertNotIn(fact, plain.split('<script type="application/json"')[0])

    def test_html_escapes_hostile_extra_values(self):
        page = format_records_as_html(
            [extras_record(engine="<script>alert(1)</script>", tags=("</script>",))],
            ("engine", "tags"),
        )
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertEqual(page.count("</script>"), 2)  # the two real script elements

    def test_txt_refuses_extra_fields_instead_of_dropping_them(self):
        with self.assertRaisesRegex(ValueError, "txt format cannot hold extra fields"):
            format_records([extras_record()], "txt", extra_fields=("tags",))
        self.assertIn("Title: Extras", format_records([extras_record()], "txt"))


def kind_record(item_type, **overrides):
    values = dict(
        title="Kinds", description="d", slug="kinds", prompt_type="gpt", domain="text",
        created=1_767_225_600_000, price=2.5, item_type=item_type,
    )
    values.update(overrides)
    return PromptRecord(**values)


class ItemTypeFormattingTests(unittest.TestCase):
    def test_url_follows_the_item_type(self):
        for item_type in ITEM_TYPES:
            self.assertEqual(
                kind_record(item_type).url, f"https://promptbase.com/{item_type}/kinds"
            )

    def test_filenames_name_the_kind(self):
        self.assertEqual(expected_filename("acb", "all", "json"), "acb_all_prompts.json")
        self.assertEqual(
            expected_filename("acb", "text", "csv", "bundle"), "acb_text_bundles.csv"
        )
        self.assertEqual(
            expected_timestamped_filename("acb", "all", "html", "20260101_000000", "app"),
            "acb_all_apps_20260101_000000.html",
        )

    def test_prompt_catalogs_keep_their_columns(self):
        self.assertEqual(tuple(record_to_dict(kind_record("prompt"))), RECORD_FIELDS)
        header = format_records_as_csv([kind_record("prompt")]).splitlines()[0]
        self.assertEqual(header, ",".join(RECORD_FIELDS))

    def test_other_kinds_say_what_they_are(self):
        for item_type in ("bundle", "app"):
            data = record_to_dict(kind_record(item_type))
            self.assertEqual(data["item_type"], item_type)
            self.assertEqual(tuple(data)[: len(RECORD_FIELDS)], RECORD_FIELDS)
            text = format_records_as_csv([kind_record(item_type)], item_type=item_type)
            row = next(csv.DictReader(io.StringIO(text)))
            self.assertEqual(row["item_type"], item_type)

    def test_item_type_column_comes_before_the_extra_fields(self):
        text = format_records_as_csv(
            [kind_record("bundle")], extra_fields=("tags",), item_type="bundle"
        )
        columns = text.splitlines()[0].split(",")
        self.assertEqual(columns[len(RECORD_FIELDS):], ["item_type", "tags"])

    def test_headings_and_counts_use_the_kind(self):
        records = [kind_record("bundle")]
        self.assertTrue(
            format_records_as_markdown(records, item_type="bundle").startswith(
                "# PromptBase Bundle Export"
            )
        )
        page = format_records_as_html(records, item_type="bundle")
        self.assertIn("<title>PromptBase Bundle Export</title>", page)
        self.assertIn('data-noun="bundles">1 bundles</p>', page)
        self.assertIn('placeholder="Filter bundles"', page)
        prompt_page = format_records_as_html([kind_record("prompt")])
        self.assertIn("<title>PromptBase Prompt Export</title>", prompt_page)
        self.assertIn('data-noun="prompts">1 prompts</p>', prompt_page)

    def test_every_kind_round_trips_through_every_format(self):
        for item_type in ("bundle", "app"):
            records = [kind_record(item_type), kind_record(item_type, title="Two", slug="two")]
            for export_format in EXPORT_FORMATS:
                with self.subTest(item_type=item_type, format=export_format):
                    with TemporaryDirectory() as directory:
                        path = write_export(
                            Path(directory), "acb", "all", records, export_format,
                            item_type=item_type,
                        )
                        self.assertEqual(path.name.split("_")[2].split(".")[0], item_type + "s")
                        self.assertEqual(count_written_records(path, export_format), 2)
                        loaded = load_catalog(path)
                        diff = compare_catalogs(loaded, records)
                    self.assertFalse(diff.has_changes)

    def test_markdown_slug_is_read_from_bundle_and_app_urls(self):
        for item_type in ("bundle", "app"):
            text = format_records_as_markdown([kind_record(item_type)], item_type=item_type)
            with TemporaryDirectory() as directory:
                path = Path(directory) / "catalog.md"
                path.write_text(text, encoding="utf-8")
                self.assertEqual(load_catalog(path)[0]["slug"], "kinds")


if __name__ == "__main__":
    unittest.main()

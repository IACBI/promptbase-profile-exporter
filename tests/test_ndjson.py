import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.cli import main
from promptbase_exporter.convert import main as convert_main
from promptbase_exporter.diffing import compare_catalogs, load_catalog
from promptbase_exporter.formatting import (
    EXPORT_FORMATS,
    FORMAT_EXTENSIONS,
    count_written_records,
    format_records,
    format_records_as_ndjson,
    infer_format_from_path,
    load_ndjson_catalog_data,
    record_to_dict,
    write_export,
    write_export_to_path,
)
from promptbase_exporter.models import EXTRA_FIELDS, Profile, PromptRecord
from promptbase_exporter.web import (
    _EXPORT_FILENAME_RE,
    DOWNLOAD_CONTENT_TYPES,
    build_request_config,
)
from tests.scratch import use_scratch_working_directory

LINE_BREAKS = "\u2028\u2029\u0085"


def setUpModule():
    use_scratch_working_directory()


def sample(slug="one", **overrides):
    values = {
        "title": "Title " + slug, "description": "Line one\nLine two", "slug": slug,
        "prompt_type": "gpt",
        "domain": "text", "created": 1_767_225_600_000, "price": 2.5, "tags": ("a", "b"),
        "engine": "gpt-5",
        "unique_sales": 2,
    }
    values.update(overrides)
    return PromptRecord(**values)


def run(function, argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = function(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class NdjsonFormatTests(unittest.TestCase):
    def test_is_registered_as_a_format(self):
        self.assertIn("ndjson", EXPORT_FORMATS)
        self.assertEqual(FORMAT_EXTENSIONS["ndjson"], "ndjson")

    def test_one_compact_object_per_line_and_no_enclosing_array(self):
        records = [sample("one"), sample("two")]
        text = format_records_as_ndjson(records)
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)  # two records and the empty string after the last newline
        self.assertEqual(lines[2], "")
        self.assertFalse(text.startswith("["))
        self.assertNotIn(", ", lines[0])  # compact separators
        self.assertEqual([json.loads(line) for line in lines[:2]],
                         [record_to_dict(r) for r in records])

    def test_no_records_is_an_empty_file(self):
        self.assertEqual(format_records_as_ndjson([]), "")
        with TemporaryDirectory() as directory:
            path = write_export_to_path(
                Path(directory) / "e.ndjson", [], "ndjson", overwrite=False
            )
            self.assertEqual(count_written_records(path, "ndjson"), 0)

    def test_unicode_line_breaks_cannot_split_a_record(self):
        record = sample(title="a\u2028b", description="c\u0085d\u2029e" + "\r\n" + "f")
        text = format_records_as_ndjson([record, sample("two")])
        self.assertEqual(len(text.splitlines()), 2)  # what a naive line reader would see
        for character in LINE_BREAKS:
            self.assertNotIn(character, text)
        loaded = load_ndjson_catalog_data(text)
        self.assertEqual(loaded[0]["title"], "a\u2028b")
        self.assertEqual(loaded[0]["description"], "c\u0085d\u2029e\r\nf")

    def test_the_loader_splits_on_newlines_only(self):
        # A hand-written file that holds a raw U+2028 inside a string stays one record.
        text = '{"title":"a\u2028b"}\n{"title":"c"}\n'
        self.assertEqual([r["title"] for r in load_ndjson_catalog_data(text)], ["a\u2028b", "c"])

    def test_extras_and_the_item_type_are_carried(self):
        record = sample(item_type="bundle")
        data = json.loads(format_records_as_ndjson([record], EXTRA_FIELDS))
        self.assertEqual(data["item_type"], "bundle")
        self.assertEqual(data["tags"], ["a", "b"])
        self.assertEqual(data["unique_sales"], 2)

    def test_dispatch_and_filename(self):
        self.assertEqual(format_records([sample()], "ndjson"), format_records_as_ndjson([sample()]))
        with TemporaryDirectory() as directory:
            path = write_export(Path(directory), "acb", "all", [sample()], "ndjson")
            self.assertEqual(path.name, "acb_all_prompts.ndjson")
            self.assertEqual(count_written_records(path, "ndjson"), 1)

    def test_the_format_is_inferred_from_either_extension(self):
        for name in ("a.ndjson", "a.jsonl", "A.NDJSON", "A.JSONL"):
            self.assertEqual(infer_format_from_path(Path(name)), "ndjson")


class NdjsonReadingTests(unittest.TestCase):
    def test_blank_lines_are_ignored_and_a_trailing_newline_is_fine(self):
        text = '\n{"a":1}\n\n   \n{"a":2}\n'
        self.assertEqual(load_ndjson_catalog_data(text), [{"a": 1}, {"a": 2}])

    def test_an_invalid_line_is_named(self):
        with self.assertRaisesRegex(ValueError, "line 2 is not valid JSON"):
            load_ndjson_catalog_data('{"a":1}\n{oops\n')

    def test_a_line_that_is_not_an_object(self):
        text = '{"a":1}\n7\n'
        self.assertEqual(load_ndjson_catalog_data(text), [{"a": 1}])
        with self.assertRaisesRegex(ValueError, "line 2 is not a record"):
            load_ndjson_catalog_data(text, strict=True)

    def test_the_written_file_is_validated_strictly(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.ndjson"
            path.write_text('{"a":1}\n[1]\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2 is not a record"):
                count_written_records(path, "ndjson")

    def test_catalogs_load_from_both_extensions_and_compare_equal_to_json(self):
        records = [sample("one"), sample("two", price=0.0)]
        with TemporaryDirectory() as directory:
            for name in ("c.ndjson", "c.jsonl"):
                path = write_export_to_path(Path(directory) / name, records, "ndjson",
                                            overwrite=False)
                loaded = load_catalog(path)
                self.assertEqual([r["slug"] for r in loaded], ["one", "two"])
                self.assertFalse(compare_catalogs(loaded, records).has_changes)

    def test_load_catalog_strictness_reaches_ndjson(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "c.ndjson"
            path.write_text('{"title":"A"}\n3\n', encoding="utf-8")
            self.assertEqual([r["title"] for r in load_catalog(path)], ["A"])
            with self.assertRaisesRegex(ValueError, "line 2 is not a record"):
                load_catalog(path, strict=True)


class NdjsonSurfacesTests(unittest.TestCase):
    def _fetch_patch(self, records):
        return patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile("acb", "u"), records),
        )

    def test_cli_writes_and_updates_an_ndjson_catalog(self):
        records = [sample("one"), sample("two")]
        with TemporaryDirectory() as directory, self._fetch_patch(records):
            out = Path(directory) / "acb_all_prompts.ndjson"
            first, _, _ = run(main, ["@acb", "--mode", "all", "--format", "ndjson",
                                     "--output-file", str(out), "--quiet"])
            second, stdout, _ = run(main, ["@acb", "--mode", "all", "--fail-on-diff",
                                           "--update-file", str(out)])
            count = count_written_records(out, "ndjson")
        self.assertEqual((first, second, count), (0, 0, 2))
        self.assertIn("Unchanged: 2", stdout)

    def test_cli_infers_the_format_from_a_jsonl_output_path(self):
        with TemporaryDirectory() as directory, self._fetch_patch([sample()]):
            out = Path(directory) / "catalog.jsonl"
            code, _, _ = run(main, ["@acb", "--mode", "all", "--output-file", str(out),
                                    "--quiet"])
            first_line = out.read_text(encoding="utf-8").split("\n")[0]
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(first_line)["slug"], "one")

    def test_convert_reads_and_writes_ndjson(self):
        records = [sample("one"), sample("two")]
        with TemporaryDirectory() as directory:
            source = write_export_to_path(Path(directory) / "s.json", records, "json",
                                          overwrite=False, extra_fields=EXTRA_FIELDS)
            direct = write_export_to_path(Path(directory) / "d.ndjson", records, "ndjson",
                                          overwrite=False, extra_fields=EXTRA_FIELDS)
            to_ndjson = Path(directory) / "c.ndjson"
            code, _, _ = run(convert_main, [str(source), "-o", str(to_ndjson), "--quiet"])
            back = Path(directory) / "back.csv"
            code_back, _, _ = run(convert_main, [str(to_ndjson), "-o", str(back), "--quiet"])
            same = to_ndjson.read_bytes() == direct.read_bytes()
        self.assertEqual((code, code_back), (0, 0))
        self.assertTrue(same)

    def test_convert_names_a_bad_line(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "bad.ndjson"
            source.write_text('{"title":"x"}\n[1]\n', encoding="utf-8")
            code, _, stderr = run(convert_main, [str(source), "-f", "csv"])
        self.assertEqual(code, 1)
        self.assertIn("line 2 is not a record", stderr)

    def test_web_accepts_the_format_and_allows_only_its_own_filenames(self):
        self.assertEqual(
            build_request_config({"profile": "acb", "format": "ndjson"}).export_format, "ndjson"
        )
        self.assertEqual(DOWNLOAD_CONTENT_TYPES[".ndjson"], "application/x-ndjson; charset=utf-8")
        for name in ("acb_all_prompts.ndjson", "acb_text_bundles_20260101_120000.ndjson"):
            self.assertTrue(_EXPORT_FILENAME_RE.match(name), name)
        for name in (
            "acb_all_prompts.jsonl",
            "acb_all_prompts.ndjsonx",
            "acb_all_prompts.ndjson.exe",
        ):
            self.assertFalse(_EXPORT_FILENAME_RE.match(name), name)


if __name__ == "__main__":
    unittest.main()

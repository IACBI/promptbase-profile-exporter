import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from promptbase_exporter.convert import (
    main,
    present_extra_fields,
    record_from_dict,
)
from promptbase_exporter.formatting import (
    EXPORT_FORMATS,
    FORMAT_EXTENSIONS,
    record_to_dict,
    write_export_to_path,
)
from promptbase_exporter.models import EXTRA_FIELDS, PromptRecord

LOSSLESS = ("json", "csv", "html")


def sample_records(item_type="prompt"):
    return [
        PromptRecord(
            title="Plain title", description="Line one\nLine two", slug="plain-title",
            prompt_type="gpt", domain="text", created=1_767_225_600_000, price=2.5,
            discount=0.2, views=10, sales=3, downloads=4, favorites=5, rating=4.5, reviews=2,
            tags=("poster", "icons"), engine="gpt-5.5", nsfw=False, featured=True,
            updated=1_788_566_967_782, last_sale=None, unique_sales=3, item_type=item_type,
        ),
        PromptRecord(
            title="=SUM(A1) — üñíçödé \U0001f4cc", description="+cmd, \"quoted\", <b>tag</b>",
            slug="hostile", prompt_type="", domain="image", created=1_700_000_000_000,
            price=0.0, tags=(), engine="", nsfw=True, featured=False,
            last_sale=1_790_000_000_000, unique_sales=0, item_type=item_type,
        ),
        PromptRecord(
            title="Awkward tags", description="d", slug="awkward", prompt_type="gpt",
            domain="text", created=1_600_000_000_000, price=1.0,
            tags=("has,comma", "two words", "[bracket"), engine="gpt-4o", unique_sales=1,
            item_type=item_type,
        ),
        PromptRecord(
            title="Bracketed tags", description="d", slug="bracketed", prompt_type="gpt",
            domain="text", created=1_500_000_000_000, price=1.0, tags=("[x", "y]"),
            item_type=item_type,
        ),
    ]


def write(directory, name, records, export_format, **options):
    return write_export_to_path(
        Path(directory) / name, records, export_format, overwrite=True, **options
    )


def run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class RecordFromDictTests(unittest.TestCase):
    def test_a_written_row_rebuilds_the_record_exactly(self):
        for record in sample_records():
            row = record_to_dict(record, EXTRA_FIELDS)
            self.assertEqual(record_from_dict(row), record)

    def test_csv_text_values_are_read_back(self):
        row = {
            "title": " T ", "description": "d", "slug": "s", "type": "gpt", "domain": "TEXT",
            "created": "1752100806132", "price": "2.5", "discount": "0.0", "views": "7",
            "sales": "3.0", "downloads": "0", "favorites": "1", "rating": "4.5", "reviews": "2",
            "tags": "a, b,, c", "nsfw": "true", "featured": "false", "updated": "",
            "last_sale": "1788566967782", "unique_sales": "4",
        }
        record = record_from_dict(row)
        self.assertEqual(record.title, "T")
        self.assertEqual(record.domain, "text")
        self.assertEqual((record.created, record.sales), (1752100806132, 3))
        self.assertEqual(record.tags, ("a", "b", "c"))
        self.assertEqual((record.nsfw, record.featured), (True, False))
        self.assertIsNone(record.updated)
        self.assertEqual(record.last_sale, 1788566967782)
        self.assertEqual(record.unique_sales, 4)

    def test_kind_comes_from_the_column_then_the_url_then_defaults_to_prompt(self):
        base = record_to_dict(sample_records()[0])
        base.pop("url")
        self.assertEqual(record_from_dict(base).item_type, "prompt")
        self.assertEqual(
            record_from_dict({**base, "url": "https://promptbase.com/bundle/x"}).item_type,
            "bundle",
        )
        self.assertEqual(
            record_from_dict({**base, "url": "https://promptbase.com/bundle/x",
                              "item_type": "app"}).item_type,
            "app",
        )

    def test_missing_or_unreadable_values_are_errors_not_defaults(self):
        good = record_to_dict(sample_records()[0])
        cases = {
            "no views column": ({k: v for k, v in good.items() if k != "views"}, "views column"),
            "empty number": ({**good, "views": ""}, "views is empty"),
            "null number": ({**good, "price": None}, "price is empty"),
            "text number": ({**good, "price": "free"}, "price is not a number"),
            "fractional count": ({**good, "sales": "2.5"}, "sales is not a whole number"),
            "boolean count": ({**good, "sales": True}, "sales is not a whole number"),
            "bad flag": ({**good, "nsfw": "maybe"}, "nsfw is not true or false"),
            "bad kind": ({**good, "item_type": "skill"}, "unknown item_type"),
            "bad time": ({**good, "updated": "yesterday"}, "updated is not a whole number"),
        }
        for label, (row, message) in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, message):
                    record_from_dict(row)

    def test_present_extra_fields_follow_the_columns_in_the_data(self):
        rows = [record_to_dict(sample_records()[0], ("unique_sales", "tags"))]
        self.assertEqual(present_extra_fields(rows), ("tags", "unique_sales"))
        self.assertEqual(present_extra_fields([record_to_dict(sample_records()[0])]), ())


class ConversionMatchesDirectExportTests(unittest.TestCase):
    """A converted catalog must equal the one a direct export would have written."""

    def _assert_every_pair_matches(self, records, *, item_type, **source_options):
        for source_format in LOSSLESS:
            for target in EXPORT_FORMATS:
                if target == source_format:
                    continue
                with self.subTest(item_type=item_type, source=source_format, target=target):
                    with TemporaryDirectory() as directory:
                        extras = () if target == "txt" else EXTRA_FIELDS
                        source = write(
                            directory, f"src.{FORMAT_EXTENSIONS[source_format]}", records,
                            source_format, extra_fields=EXTRA_FIELDS, item_type=item_type,
                            **(source_options if source_format == "csv" else {}),
                        )
                        direct = write(
                            directory, f"direct.{FORMAT_EXTENSIONS[target]}", records, target,
                            extra_fields=extras, item_type=item_type,
                        )
                        converted = Path(directory) / f"out.{FORMAT_EXTENSIONS[target]}"
                        argv = [str(source), "-o", str(converted), "--quiet"]
                        if source_options.get("csv_safe") and source_format == "csv":
                            argv.append("--from-csv-safe")
                        if source_options.get("csv_safe") and target == "csv":
                            argv.append("--csv-safe")
                        exit_code, _, stderr = run(argv)
                        self.assertEqual(exit_code, 0, stderr)
                        if source_options.get("csv_safe") and target == "csv":
                            direct = write(
                                directory, "direct_safe.csv", records, "csv",
                                extra_fields=extras, item_type=item_type, csv_safe=True,
                            )
                        self.assertEqual(
                            converted.read_bytes(), direct.read_bytes(),
                            f"{source_format} -> {target} differs from a direct export",
                        )

    def test_prompts_between_every_pair_of_formats(self):
        self._assert_every_pair_matches(sample_records(), item_type="prompt")

    def test_bundles_and_apps_between_every_pair_of_formats(self):
        for item_type in ("bundle", "app"):
            self._assert_every_pair_matches(sample_records(item_type), item_type=item_type)

    def test_protected_csv_source_and_target(self):
        self._assert_every_pair_matches(sample_records(), item_type="prompt", csv_safe=True)

    def test_a_catalog_without_extras_converts_without_inventing_them(self):
        records = sample_records()
        with TemporaryDirectory() as directory:
            source = write(directory, "src.json", records, "json")
            exit_code, _, _ = run([str(source), "-f", "csv", "--quiet"])
            header = (Path(directory) / "src.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(exit_code, 0)
        self.assertNotIn("tags", header)
        self.assertNotIn("item_type", header)


class ConvertCommandTests(unittest.TestCase):
    def _source(self, directory, name="catalog.json", records=None, export_format="json"):
        return write(directory, name, records or sample_records(), export_format)

    def test_output_defaults_to_the_source_with_the_new_extension(self):
        with TemporaryDirectory() as directory:
            source = self._source(directory)
            exit_code, stdout, _ = run([str(source), "-f", "markdown"])
            self.assertEqual(exit_code, 0)
            self.assertTrue((Path(directory) / "catalog.md").is_file())
        self.assertIn("Converted 4 record(s)", stdout)

    def test_format_is_inferred_from_the_output_path(self):
        with TemporaryDirectory() as directory:
            source = self._source(directory)
            out = Path(directory) / "nested" / "out.html"
            exit_code, _, _ = run([str(source), "-o", str(out), "--quiet"])
            self.assertEqual(exit_code, 0)
            self.assertIn("<!doctype html>", out.read_text(encoding="utf-8"))

    def test_quiet_prints_nothing(self):
        with TemporaryDirectory() as directory:
            exit_code, stdout, stderr = run(
                [str(self._source(directory)), "-f", "csv", "--quiet"]
            )
        self.assertEqual((exit_code, stdout, stderr), (0, "", ""))

    def test_needs_a_format_or_an_output_path(self):
        with TemporaryDirectory() as directory:
            exit_code, _, stderr = run([str(self._source(directory))])
        self.assertEqual(exit_code, 1)
        self.assertIn("give --format, --output-file, or both", stderr)

    def test_refuses_a_source_that_does_not_keep_every_field(self):
        for name, export_format in (("catalog.txt", "txt"), ("catalog.md", "markdown")):
            with self.subTest(name), TemporaryDirectory() as directory:
                source = self._source(directory, name, export_format=export_format)
                exit_code, _, stderr = run([str(source), "-f", "json"])
            self.assertEqual(exit_code, 1)
            self.assertIn("would have to be invented", stderr)

    def test_refuses_to_overwrite_the_source_or_an_existing_file(self):
        with TemporaryDirectory() as directory:
            source = self._source(directory)
            before = source.read_bytes()
            same, _, same_err = run([str(source), "-f", "json"])
            existing = Path(directory) / "catalog.csv"
            existing.write_text("keep me", encoding="utf-8")
            exists, _, exists_err = run([str(source), "-f", "csv"])
            kept = existing.read_text(encoding="utf-8")
            replaced, _, _ = run([str(source), "-f", "csv", "--overwrite", "--quiet"])
            after = source.read_bytes()
        self.assertEqual((same, exists, replaced), (1, 1, 0))
        self.assertIn("the output file is the source file", same_err)
        self.assertIn("Use --overwrite", exists_err)
        self.assertEqual(kept, "keep me")
        self.assertEqual(before, after)

    def test_flag_combinations_are_validated(self):
        with TemporaryDirectory() as directory:
            source = self._source(directory)
            csv_safe, _, csv_err = run([str(source), "-f", "json", "-o", "x.json", "--csv-safe"])
            from_safe, _, from_err = run([str(source), "-f", "csv", "--from-csv-safe"])
        self.assertEqual((csv_safe, from_safe), (1, 1))
        self.assertIn("--csv-safe requires CSV output", csv_err)
        self.assertIn("--from-csv-safe requires a CSV source", from_err)

    def test_an_empty_catalog_is_an_error(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "empty.json"
            source.write_text("[]\n", encoding="utf-8")
            exit_code, _, stderr = run([str(source), "-f", "csv"])
        self.assertEqual(exit_code, 1)
        self.assertIn("the catalog has no records", stderr)

    def test_mixed_kinds_are_an_error(self):
        rows = [record_to_dict(r) for r in sample_records()]
        rows[1]["item_type"] = "bundle"
        with TemporaryDirectory() as directory:
            source = Path(directory) / "mixed.json"
            source.write_text(json.dumps(rows), encoding="utf-8")
            exit_code, _, stderr = run([str(source), "-f", "csv"])
        self.assertEqual(exit_code, 1)
        self.assertIn("mixes kinds of listing: bundle, prompt", stderr)

    def test_a_bad_record_is_named_by_its_position(self):
        rows = [record_to_dict(r) for r in sample_records()]
        rows[1]["views"] = ""
        with TemporaryDirectory() as directory:
            source = Path(directory) / "bad.json"
            source.write_text(json.dumps(rows), encoding="utf-8")
            exit_code, _, stderr = run([str(source), "-f", "csv"])
            written = list(Path(directory).glob("*.csv"))
        self.assertEqual(exit_code, 1)
        self.assertIn("record 2: views is empty", stderr)
        self.assertEqual(written, [])

    def test_a_missing_source_is_reported(self):
        with TemporaryDirectory() as directory:
            exit_code, _, stderr = run([str(Path(directory) / "gone.json"), "-f", "csv"])
        self.assertEqual(exit_code, 1)
        self.assertIn("error:", stderr)


class ReviewFindingsTests(unittest.TestCase):
    """Each of these silently lost or invented data before it was fixed."""

    def _json_source(self, directory, rows, name="src.json"):
        path = Path(directory) / name
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path

    def _rows(self, **extras):
        return [{**record_to_dict(r), **extras} for r in sample_records()[:2]]

    def test_a_failed_check_never_replaces_an_existing_destination(self):
        rows = self._rows()
        rows[0]["title"] = ""  # a blank title cannot be counted back out of a TXT file
        with TemporaryDirectory() as directory:
            source = self._json_source(directory, rows)
            destination = Path(directory) / "out.txt"
            destination.write_text("keep me", encoding="utf-8")
            exit_code, _, stderr = run([str(source), "-o", str(destination), "--overwrite"])
            kept = destination.read_text(encoding="utf-8")
            leftovers = sorted(p.name for p in Path(directory).iterdir())
        self.assertEqual(exit_code, 1)
        self.assertIn("validation failed", stderr)
        self.assertEqual(kept, "keep me")
        self.assertEqual(leftovers, ["out.txt", "src.json"])  # no temp files left behind

    def test_entries_that_are_not_records_are_refused_not_dropped(self):
        for label, export_format, write_source in (
            ("json", "json", lambda d, rows: self._json_source(d, rows)),
            ("html", "html", None),
        ):
            with self.subTest(label), TemporaryDirectory() as directory:
                records = sample_records()[:2]
                if write_source:
                    rows = [record_to_dict(r) for r in records]
                    source = write_source(directory, [rows[0], None, rows[1]])
                else:
                    source = write(directory, "src.html", records, export_format)
                    text = source.read_text(encoding="utf-8")
                    data = json.dumps([record_to_dict(r) for r in records], ensure_ascii=False)
                    broken = json.dumps(
                        [record_to_dict(records[0]), 7, record_to_dict(records[1])],
                        ensure_ascii=False,
                    ).replace("<", "\\u003c")
                    source.write_text(
                        text.replace(data.replace("<", "\\u003c"), broken), encoding="utf-8"
                    )
                exit_code, _, stderr = run([str(source), "-f", "csv", "--quiet"])
                written = list(Path(directory).glob("*.csv"))
            self.assertEqual(exit_code, 1)
            self.assertIn("entry 2 of the catalog is not a record", stderr)
            self.assertEqual(written, [])

    def test_an_extra_field_must_be_present_on_every_record(self):
        rows = self._rows(nsfw=False)
        del rows[1]["nsfw"]
        with TemporaryDirectory() as directory:
            source = self._json_source(directory, rows)
            exit_code, _, stderr = run([str(source), "-f", "csv", "--quiet"])
            written = list(Path(directory).glob("*.csv"))
        self.assertEqual(exit_code, 1)
        self.assertIn("record 2: has no nsfw column, which other records have", stderr)
        self.assertEqual(written, [])

    def test_a_recorded_flag_or_count_cannot_be_empty(self):
        for column in ("nsfw", "featured", "unique_sales"):
            rows = self._rows(nsfw=False, featured=False, unique_sales=0)
            rows[0][column] = None
            with self.subTest(column), TemporaryDirectory() as directory:
                source = self._json_source(directory, rows)
                exit_code, _, stderr = run([str(source), "-f", "csv", "--quiet"])
            self.assertEqual(exit_code, 1)
            self.assertIn(f"record 1: {column} is empty", stderr)

    def test_unknown_times_may_stay_empty(self):
        rows = self._rows(updated=None, updated_iso=None, last_sale=None, last_sale_iso=None)
        with TemporaryDirectory() as directory:
            source = self._json_source(directory, rows)
            exit_code, _, _ = run([str(source), "-f", "csv", "--quiet"])
        self.assertEqual(exit_code, 0)

    def test_booleans_are_not_numbers(self):
        for column in ("price", "discount", "rating"):
            rows = self._rows()
            rows[0][column] = True
            with self.subTest(column), TemporaryDirectory() as directory:
                source = self._json_source(directory, rows)
                exit_code, _, stderr = run([str(source), "-f", "csv", "--quiet"])
            self.assertEqual(exit_code, 1)
            self.assertIn(f"{column} is not a number", stderr)

    def test_tags_with_commas_survive_a_csv_round_trip(self):
        records = [sample_records()[2]]
        with TemporaryDirectory() as directory:
            direct_json = write(directory, "direct.json", records, "json",
                                extra_fields=EXTRA_FIELDS)
            csv_path = write(directory, "catalog.csv", records, "csv", extra_fields=EXTRA_FIELDS)
            cell = csv_path.read_text(encoding="utf-8")
            exit_code, _, stderr = run(
                [str(csv_path), "-o", str(Path(directory) / "back.json"), "--quiet"]
            )
            back = (Path(directory) / "back.json").read_bytes()
            direct = direct_json.read_bytes()
        self.assertEqual(exit_code, 0, stderr)
        self.assertIn('"[""has,comma"", ""two words"", ""[bracket""]"', cell)
        self.assertEqual(back, direct)

    def test_tags_that_only_look_like_brackets_are_still_split_on_commas(self):
        records = [sample_records()[3]]  # tags ("[x", "y]") joined as "[x, y]"
        with TemporaryDirectory() as directory:
            csv_path = write(directory, "catalog.csv", records, "csv", extra_fields=("tags",))
            direct = write(directory, "direct.json", records, "json", extra_fields=("tags",))
            exit_code, _, _ = run([str(csv_path), "-o", str(Path(directory) / "b.json"), "--quiet"])
            back = (Path(directory) / "b.json").read_bytes()
            expected = direct.read_bytes()
        self.assertEqual(exit_code, 0)
        self.assertEqual(back, expected)


if __name__ == "__main__":
    unittest.main()

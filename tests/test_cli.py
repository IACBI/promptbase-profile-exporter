import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.cli import (
    EXIT_DIFF,
    EXIT_ERROR,
    count_by,
    diff_main,
    main,
    parse_datetime_ms,
)
from promptbase_exporter.client import PromptBaseError
from promptbase_exporter.formatting import write_export, write_export_to_path
from promptbase_exporter.models import Profile, PromptRecord


def setUpModule():
    # The default --output-dir is relative to the working directory, so a test
    # that forgets to set one writes exports/ into the repository. Run the
    # module from an empty scratch directory and fail if anything lands in it.
    sandbox = TemporaryDirectory()
    original = os.getcwd()
    os.chdir(sandbox.name)

    def restore():
        leaked = sorted(os.listdir(sandbox.name))
        os.chdir(original)  # Windows cannot remove the current directory
        sandbox.cleanup()
        if leaked:
            raise AssertionError(f"a CLI test wrote into the working directory: {leaked}")

    unittest.addModuleCleanup(restore)


def record(title, domain, prompt_type, price=0.0, created=1):
    return PromptRecord(
        title=title,
        description=f"{title} description",
        slug=title.lower().replace(" ", "-"),
        prompt_type=prompt_type,
        domain=domain,
        created=created,
        price=price,
    )


class CliTests(unittest.TestCase):
    def test_count_by(self):
        records = [
            record("A", "text", "gpt"),
            record("B", "text", "claude"),
            record("C", "image", "chatgpt-image"),
        ]

        self.assertEqual(count_by(records, "domain"), {"text": 2, "image": 1})

    def test_dry_run_does_not_write_files(self):
        records = [record("A", "text", "gpt")]
        with patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), records),
        ), patch("promptbase_exporter.cli.write_export") as write_export:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["@acb", "--dry-run"])

        self.assertEqual(exit_code, 0)
        write_export.assert_not_called()
        self.assertIn("Planned outputs:", stdout.getvalue())
        self.assertIn("all: 1", stdout.getvalue())
        self.assertIn("text: 1", stdout.getvalue())
        self.assertIn("image: 0", stdout.getvalue())
        self.assertIn("Dry run: no files written.", stdout.getvalue())

    def test_list_domains_exits_without_writing(self):
        records = [
            record("A", "text", "gpt"),
            record("B", "image", "chatgpt-image"),
        ]
        with patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), records),
        ), patch("promptbase_exporter.cli.write_export") as write_export:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["@acb", "--list-domains"])

        self.assertEqual(exit_code, 0)
        write_export.assert_not_called()
        self.assertIn("Domains:", stdout.getvalue())
        self.assertIn("text: 1", stdout.getvalue())

    def test_output_file_refuses_existing_without_overwrite(self):
        records = [record("A", "text", "gpt")]
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            output_path.write_text("[]\n", encoding="utf-8")
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile(username="acb", uid="uid-1"), records),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(["@acb", "--mode", "all", "--output-file", str(output_path)])

        self.assertEqual(exit_code, 1)

    def test_output_file_infers_json_and_writes(self):
        records = [record("A", "text", "gpt")]
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile(username="acb", uid="uid-1"), records),
            ):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main(["@acb", "--mode", "all", "--output-file", str(output_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn('"title": "A"', output_path.read_text(encoding="utf-8"))

    def test_update_file_compares_and_rewrites_existing_catalog(self):
        records = [record("New", "text", "gpt")]
        old_text = "1.\nTitle: Old\nDescription:\nOld description\n"
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.txt"
            output_path.write_text(old_text, encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile(username="acb", uid="uid-1"), records),
            ), redirect_stdout(stdout):
                exit_code = main(["@acb", "--mode", "all", "--update-file", str(output_path)])

            self.assertEqual(exit_code, 0)
            self.assertIn("- Added: 1", stdout.getvalue())
            self.assertIn("Title: New", output_path.read_text(encoding="utf-8"))

    def test_compare_can_fail_on_diff(self):
        records = [record("New", "text", "gpt")]
        with TemporaryDirectory() as directory:
            previous_path = Path(directory) / "catalog.json"
            previous_path.write_text("[]\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile(username="acb", uid="uid-1"), records),
            ), redirect_stdout(stdout):
                exit_code = main(
                    [
                        "@acb",
                        "--mode",
                        "all",
                        "--compare",
                        str(previous_path),
                        "--fail-on-diff",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("- Added: 1", stdout.getvalue())

    def test_update_file_rewrites_even_when_fail_on_diff_returns_two(self):
        records = [record("New", "text", "gpt")]
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.txt"
            output_path.write_text("", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile(username="acb", uid="uid-1"), records),
            ), redirect_stdout(stdout):
                exit_code = main(
                    [
                        "@acb",
                        "--mode",
                        "all",
                        "--update-file",
                        str(output_path),
                        "--fail-on-diff",
                    ]
                )

            self.assertEqual(exit_code, 2)
            self.assertIn("- Added: 1", stdout.getvalue())
            self.assertIn("Title: New", output_path.read_text(encoding="utf-8"))

    def test_since_until_and_limit_filter_selected_records(self):
        records = [
            record(
                "Newest",
                "text",
                "gpt",
                created=parse_datetime_ms("2026-03-01", end_of_day=False),
            ),
            record(
                "Middle",
                "text",
                "gpt",
                created=parse_datetime_ms("2026-02-01", end_of_day=False),
            ),
            record(
                "Oldest",
                "text",
                "gpt",
                created=parse_datetime_ms("2026-01-01", end_of_day=False),
            ),
        ]
        with patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), records),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "@acb",
                        "--since",
                        "2026-01-15",
                        "--until",
                        "2026-03-31",
                        "--limit",
                        "1",
                        "--dry-run",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("Selected after filters: 1", stdout.getvalue())

    def test_text_only_mode_alias_writes_text_export(self):
        records = [
            record("A", "text", "gpt"),
            record("B", "image", "chatgpt-image"),
        ]
        with patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), records),
        ), patch("promptbase_exporter.cli.write_export") as write_export, patch(
            "promptbase_exporter.cli.count_written_records",
            return_value=1,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["@acb", "--mode", "text-only"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(write_export.call_args.args[2], "text")
        self.assertEqual([record.title for record in write_export.call_args.args[3]], ["A"])

    def test_image_only_mode_alias_writes_image_export(self):
        records = [
            record("A", "text", "gpt"),
            record("B", "image", "chatgpt-image"),
        ]
        with patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), records),
        ), patch("promptbase_exporter.cli.write_export") as write_export, patch(
            "promptbase_exporter.cli.count_written_records",
            return_value=1,
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["@acb", "--mode", "image-only"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(write_export.call_args.args[2], "image")
        self.assertEqual([record.title for record in write_export.call_args.args[3]], ["B"])

    def test_version_argument_exits(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["--version"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("promptbase-export", stdout.getvalue())


class WriteFailureTests(unittest.TestCase):
    """Filesystem write failures must surface as clean errors, not tracebacks."""

    def _fetch(self):
        return patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), [record("A", "text", "gpt")]),
        )

    def test_directory_export_write_failure_reports_error(self):
        with self._fetch(), patch(
            "promptbase_exporter.cli.write_export",
            side_effect=OSError("disk full"),
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["@acb", "--mode", "all"])

        self.assertEqual(exit_code, 1)
        self.assertIn("could not write export", stderr.getvalue())
        self.assertIn("disk full", stderr.getvalue())

    def test_single_file_write_failure_reports_error(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "catalog.json"
            with self._fetch(), patch(
                "promptbase_exporter.cli.write_export_to_path",
                side_effect=OSError("read-only file system"),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        ["@acb", "--mode", "all", "--output-file", str(output_path)]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("could not write", stderr.getvalue())
        self.assertIn("read-only file system", stderr.getvalue())

    def test_diff_report_write_failure_reports_error(self):
        with TemporaryDirectory() as directory:
            previous_path = Path(directory) / "previous.json"
            previous_path.write_text("[]\n", encoding="utf-8")
            diff_path = Path(directory) / "out" / "diff.md"
            with self._fetch(), patch(
                "promptbase_exporter.cli.write_diff_report",
                side_effect=OSError("permission denied"),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "@acb",
                            "--mode",
                            "all",
                            "--compare",
                            str(previous_path),
                            "--diff-output",
                            str(diff_path),
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("could not write diff report", stderr.getvalue())


class ArgumentValidationTests(unittest.TestCase):
    """Invalid arguments must fail before any network fetch."""

    def _run_expecting_failure(self, argv):
        with patch("promptbase_exporter.cli.fetch_prompts") as fetch:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(argv)
        fetch.assert_not_called()
        return exit_code, stderr.getvalue()

    def test_negative_min_price_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(["@acb", "--min-price", "-1"])
        self.assertEqual(exit_code, 1)
        self.assertIn("--min-price cannot be negative", stderr)

    def test_negative_max_price_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(["@acb", "--max-price", "-2"])
        self.assertEqual(exit_code, 1)
        self.assertIn("--max-price cannot be negative", stderr)

    def test_min_price_greater_than_max_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(
            ["@acb", "--min-price", "5", "--max-price", "1"]
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("--min-price cannot be greater than --max-price", stderr)

    def test_non_finite_price_fails_before_fetch(self):
        for flag in ("--min-price", "--max-price"):
            exit_code, stderr = self._run_expecting_failure(["@acb", flag, "nan"])
            self.assertEqual(exit_code, 1)
            self.assertIn(f"{flag} must be a finite number", stderr)

    def test_non_positive_limit_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(["@acb", "--limit", "0"])
        self.assertEqual(exit_code, 1)
        self.assertIn("--limit must be greater than zero", stderr)


class ParseDatetimeTests(unittest.TestCase):
    def test_empty_value_raises(self):
        with self.assertRaises(ValueError):
            parse_datetime_ms("   ", end_of_day=False)

    def test_invalid_value_raises_with_guidance(self):
        for value in ("not-a-date", "2026-13-01", "2026-02-30"):
            with self.assertRaises(ValueError) as raised:
                parse_datetime_ms(value, end_of_day=False)
            self.assertIn("YYYY-MM-DD", str(raised.exception))

    def test_date_only_start_and_end_of_day_bracket_the_day(self):
        start = parse_datetime_ms("2026-01-01", end_of_day=False)
        end = parse_datetime_ms("2026-01-01", end_of_day=True)
        self.assertLess(start, end)
        # The bracket stays within a single 24h window.
        self.assertLess(end - start, 24 * 60 * 60 * 1000)

    def test_date_only_is_interpreted_as_utc_midnight(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01", end_of_day=False),
            parse_datetime_ms("2026-01-01T00:00:00+00:00", end_of_day=False),
        )

    def test_naive_datetime_is_assumed_utc(self):
        self.assertEqual(
            parse_datetime_ms("2026-01-01T12:00:00", end_of_day=False),
            parse_datetime_ms("2026-01-01T12:00:00Z", end_of_day=False),
        )

    def test_timezone_aware_datetime_is_converted_to_utc(self):
        # 00:00 at +01:00 is the previous day at 23:00 UTC.
        self.assertEqual(
            parse_datetime_ms("2026-01-01T00:00:00+01:00", end_of_day=False),
            parse_datetime_ms("2025-12-31T23:00:00+00:00", end_of_day=False),
        )


class UpdateFileDiffFailureTests(unittest.TestCase):
    """--update-file must not overwrite the catalog when the diff artifact fails,
    while --fail-on-diff stays a distinct, non-fatal exit for the update path."""

    ORIGINAL = "1.\nTitle: Old\nDescription:\nOld description\n"

    def _fetch(self):
        return patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="uid-1"), [record("New", "text", "gpt")]),
        )

    def test_diff_write_failure_does_not_overwrite_update_file(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.txt"
            catalog.write_text(self.ORIGINAL, encoding="utf-8")
            diff_path = Path(directory) / "reports" / "diff.md"
            with self._fetch(), patch(
                "promptbase_exporter.cli.write_diff_report",
                side_effect=OSError("permission denied"),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "@acb",
                            "--mode",
                            "all",
                            "--update-file",
                            str(catalog),
                            "--diff-output",
                            str(diff_path),
                        ]
                    )

            # The diff artifact failed, so this is an operational error, not a
            # success and not the --fail-on-diff signal.
            self.assertEqual(exit_code, EXIT_ERROR)
            self.assertNotEqual(exit_code, EXIT_DIFF)
            self.assertIn("could not write diff report", stderr.getvalue())
            # Critically, the existing catalog must be left untouched.
            self.assertEqual(catalog.read_text(encoding="utf-8"), self.ORIGINAL)

    def test_fail_on_diff_stays_distinct_and_rewrites_update_file(self):
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.txt"
            catalog.write_text(self.ORIGINAL, encoding="utf-8")
            with self._fetch():
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "@acb",
                            "--mode",
                            "all",
                            "--update-file",
                            str(catalog),
                            "--fail-on-diff",
                        ]
                    )

            # --fail-on-diff is its own exit code, distinct from an error, and it
            # leaves the update-file rewrite behavior unchanged.
            self.assertEqual(exit_code, EXIT_DIFF)
            self.assertNotEqual(exit_code, EXIT_ERROR)
            self.assertNotIn("could not write", stderr.getvalue())
            self.assertIn("Title: New", catalog.read_text(encoding="utf-8"))


def _quiet_main(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class NewOptionValidationTests(unittest.TestCase):
    _run_expecting_failure = ArgumentValidationTests._run_expecting_failure

    def test_negative_min_sales_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(["@acb", "--min-sales", "-1"])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("--min-sales cannot be negative", stderr)

    def test_invalid_min_rating_fails_before_fetch(self):
        for value, message in (
            ("nan", "--min-rating must be a finite number"),
            ("-0.5", "--min-rating cannot be negative"),
        ):
            exit_code, stderr = self._run_expecting_failure(["@acb", "--min-rating", value])
            self.assertEqual(exit_code, EXIT_ERROR)
            self.assertIn(message, stderr)

    def test_csv_safe_requires_csv_format(self):
        exit_code, stderr = self._run_expecting_failure(
            ["@acb", "--csv-safe", "--format", "json"]
        )
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("--csv-safe requires --format csv", stderr)

    def test_csv_safe_rejects_inferred_non_csv_output_file(self):
        exit_code, stderr = self._run_expecting_failure(
            ["@acb", "--mode", "all", "--csv-safe", "--output-file", "out.json"]
        )
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("--csv-safe requires --format csv", stderr)

    def test_single_catalog_options_reject_several_profiles(self):
        for option in (["--compare", "a.json"], ["--output-file", "a.json"]):
            exit_code, stderr = self._run_expecting_failure(
                ["@acb", "@other", "--mode", "all", *option]
            )
            self.assertEqual(exit_code, EXIT_ERROR)
            self.assertIn("take a single profile", stderr)

    def test_compare_csv_safe_requires_a_csv_comparison_catalog(self):
        for extra in ([], ["--compare", "old.json"]):
            exit_code, stderr = self._run_expecting_failure(
                ["@acb", "--mode", "all", "--compare-csv-safe", *extra]
            )
            self.assertEqual(exit_code, EXIT_ERROR)
            self.assertIn("--compare-csv-safe requires a CSV", stderr)

    def test_blank_profile_is_rejected(self):
        exit_code, stderr = self._run_expecting_failure(["  "])
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("profile cannot be empty", stderr)

    def test_update_file_rejects_a_format_that_disagrees_with_its_extension(self):
        # JSON written into catalog.csv would be read back as CSV next run.
        with TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.csv"
            catalog.write_text("title\n", encoding="utf-8")
            exit_code, stderr = self._run_expecting_failure(
                ["@acb", "--mode", "all", "--update-file", str(catalog), "--format", "json"]
            )
            original = catalog.read_text(encoding="utf-8")
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("--format json does not match the --update-file extension .csv", stderr)
        self.assertEqual(original, "title\n")

    def test_update_file_accepts_a_matching_format(self):
        with TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.md"
            write_export_to_path(catalog, [record("A", "text", "gpt")], "markdown", overwrite=True)
            stdout = io.StringIO()
            with patch(
                "promptbase_exporter.cli.fetch_prompts",
                return_value=(Profile("acb", "uid"), [record("A", "text", "gpt")]),
            ), redirect_stdout(stdout):
                exit_code = main(
                    ["@acb", "--mode", "all", "--update-file", str(catalog),
                     "--format", "markdown"]
                )
        self.assertEqual(exit_code, 0)
        self.assertIn("Unchanged: 1", stdout.getvalue())


class MultiProfileTests(unittest.TestCase):
    PROFILES = {
        "@acb": (Profile(username="acb", uid="u1"), [record("A text", "text", "gpt")]),
        "@bob": (Profile(username="bob", uid="u2"), [record("B image", "image", "midjourney")]),
    }

    def _fetch(self, profile_input, extra_fields=()):
        if profile_input not in self.PROFILES:
            raise PromptBaseError(f"Profile not found: {profile_input}")
        return self.PROFILES[profile_input]

    def test_exports_every_profile_with_one_timestamp(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts", side_effect=self._fetch
        ) as fetch:
            exit_code, stdout, _ = _quiet_main(
                ["@acb", "@bob", "@acb", "-o", directory, "--mode", "all",
                 "--timestamp-filenames"]
            )
            names = sorted(path.name for path in Path(directory).iterdir())

        self.assertEqual(exit_code, 0)
        # The duplicate @acb is fetched once.
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["@acb", "@bob"])
        self.assertEqual(len(names), 2)
        self.assertTrue(names[0].startswith("acb_all_prompts_"))
        self.assertTrue(names[1].startswith("bob_all_prompts_"))
        self.assertEqual(names[0].removeprefix("acb"), names[1].removeprefix("bob"))
        self.assertIn("Profile: @acb", stdout)
        self.assertIn("Profile: @bob", stdout)

    def test_failed_profile_does_not_stop_the_others(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts", side_effect=self._fetch
        ):
            exit_code, _, stderr = _quiet_main(
                ["@missing", "@bob", "-o", directory, "--mode", "all"]
            )
            written = [path.name for path in Path(directory).iterdir()]

        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("Profile not found: @missing", stderr)
        self.assertEqual(written, ["bob_all_prompts.txt"])


class NewOptionBehaviourTests(unittest.TestCase):
    def test_min_sales_and_min_rating_filter_the_export(self):
        records = [
            PromptRecord("Popular", "d", "popular", "gpt", "text", 3, 1.0, sales=9, rating=4.8),
            PromptRecord("Unsold", "d", "unsold", "gpt", "text", 2, 1.0, sales=0, rating=0.0),
            PromptRecord("Mediocre", "d", "meh", "gpt", "text", 1, 1.0, sales=9, rating=3.0),
        ]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), records),
        ):
            output = Path(directory) / "out.json"
            exit_code, _, _ = _quiet_main(
                ["@acb", "--mode", "all", "--min-sales", "1", "--min-rating", "4",
                 "--output-file", str(output)]
            )
            slugs = [item["slug"] for item in json.loads(output.read_text(encoding="utf-8"))]

        self.assertEqual(exit_code, 0)
        self.assertEqual(slugs, ["popular"])

    def test_update_file_reads_safe_csv_only_when_told(self):
        records = [PromptRecord("=cmd", "d", "x", "gpt", "text", 1, 0.0)]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), records),
        ):
            catalog = Path(directory) / "catalog.csv"
            base = ["@acb", "--mode", "all", "--quiet"]
            first, _, _ = _quiet_main([*base, "--csv-safe", "--output-file", str(catalog)])
            update = [*base, "--fail-on-diff", "--update-file", str(catalog), "--csv-safe"]
            # Declared safe: the escaped "'=cmd" is restored and nothing changed.
            safe_run, _, _ = _quiet_main([*update, "--compare-csv-safe"])
            # Not declared: the cell is read verbatim, so the title differs.
            plain_run, _, _ = _quiet_main(update)

        self.assertEqual((first, safe_run, plain_run), (0, 0, EXIT_DIFF))

    def test_safe_output_compared_with_a_plain_csv_keeps_real_apostrophes(self):
        # Codex: --csv-safe on the output must not change how a plain
        # comparison catalog is read.
        records = [PromptRecord("'=SUM(A1)", "d", "s", "gpt", "text", 1, 1.0)]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), records),
        ):
            plain = write_export(Path(directory), "old", "all", records, "csv")
            exit_code, _, stderr = _quiet_main(
                ["@acb", "--mode", "all", "--format", "csv", "--csv-safe", "--compare",
                 str(plain), "--fail-on-diff", "-o", str(Path(directory) / "out"), "--quiet"]
            )
            written = (Path(directory) / "out" / "acb_all_prompts.csv").read_text(
                encoding="utf-8"
            )

        self.assertEqual(exit_code, 0, stderr)
        # The new file is still protected.
        self.assertIn("''=SUM(A1)", written)

    def test_compare_csv_safe_works_with_non_csv_output(self):
        records = [PromptRecord("=A", "d", "a", "gpt", "text", 1, 1.0)]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), records),
        ):
            safe = write_export(Path(directory), "old", "all", records, "csv", csv_safe=True)
            exit_code, _, stderr = _quiet_main(
                ["@acb", "--mode", "all", "--compare", str(safe), "--compare-csv-safe",
                 "--fail-on-diff", "-o", str(Path(directory) / "out"), "--quiet"]
            )

        self.assertEqual(exit_code, 0, stderr)

    def test_csv_safe_output_file(self):
        records = [PromptRecord("=cmd", "d", "x", "gpt", "text", 1, 0.0)]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), records),
        ):
            output = Path(directory) / "out.csv"
            exit_code, _, _ = _quiet_main(
                ["@acb", "--mode", "all", "--csv-safe", "--output-file", str(output)]
            )
            text = output.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("'=cmd", text)

    def test_repeated_diff_output_writes_markdown_and_json(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), [record("New", "text", "gpt")]),
        ):
            catalog = Path(directory) / "catalog.txt"
            catalog.write_text("1.\nTitle: Old\nDescription:\nOld\n", encoding="utf-8")
            markdown = Path(directory) / "diff.md"
            report = Path(directory) / "diff.json"
            exit_code, stdout, _ = _quiet_main(
                ["@acb", "--mode", "all", "--compare", str(catalog),
                 "--diff-output", str(markdown), "--diff-output", str(report),
                 "--output-dir", str(Path(directory) / "out")]
            )
            data = json.loads(report.read_text(encoding="utf-8"))
            markdown_text = markdown.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(data["summary"], {"added": 1, "removed": 1, "changed": 0, "unchanged": 0})
        self.assertIn("- Added: 1", markdown_text)
        self.assertEqual(stdout.count("Wrote diff report"), 2)

    def test_html_export_validates(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), [record("A", "text", "gpt")]),
        ):
            exit_code, stdout, _ = _quiet_main(["@acb", "-o", directory, "--format", "html"])
            written = sorted(path.name for path in Path(directory).iterdir())

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            written,
            ["acb_all_prompts.html", "acb_image_prompts.html", "acb_text_prompts.html"],
        )
        self.assertIn("Wrote  text:    1 prompts", stdout)

    def test_unreadable_written_file_is_a_validation_error(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), [record("A", "text", "gpt")]),
        ), patch(
            "promptbase_exporter.cli.count_written_records",
            side_effect=ValueError("HTML catalog lists 0 prompts but embeds 1 records"),
        ):
            exit_code, _, stderr = _quiet_main(["@acb", "-o", directory, "--mode", "all"])

        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("validation failed", stderr)
        self.assertIn("embeds 1 records", stderr)


class DiffCommandTests(unittest.TestCase):
    def _catalogs(self, directory, previous, current):
        records_a = [PromptRecord(t, "d", t.lower(), "gpt", "text", 1, p) for t, p in previous]
        records_b = [PromptRecord(t, "d", t.lower(), "gpt", "text", 1, p) for t, p in current]
        old = write_export(Path(directory) / "old", "acb", "all", records_a, "csv")
        new = write_export(Path(directory) / "new", "acb", "all", records_b, "html")
        return old, new

    def _run(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = diff_main(argv)
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_identical_catalogs_across_formats(self):
        with TemporaryDirectory() as directory:
            old, new = self._catalogs(directory, [("A", 2.0)], [("A", 2.0)])
            exit_code, stdout, _ = self._run([str(old), str(new), "--fail-on-diff"])

        self.assertEqual(exit_code, 0)
        self.assertIn("- Unchanged: 1", stdout)

    def test_changes_exit_two_with_fail_on_diff_and_write_json(self):
        with TemporaryDirectory() as directory:
            old, new = self._catalogs(directory, [("A", 2.0), ("B", 1.0)], [("A", 3.0)])
            report = Path(directory) / "diff.json"
            exit_code, stdout, _ = self._run(
                [str(old), str(new), "--fail-on-diff", "--quiet", "--diff-output", str(report)]
            )
            data = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, EXIT_DIFF)
        self.assertEqual(stdout, "")
        self.assertEqual(data["summary"]["removed"], 1)
        self.assertEqual(data["changed"][0]["fields"]["price"], {"previous": 2.0, "current": 3.0})

    def test_changes_without_fail_on_diff_exit_zero(self):
        with TemporaryDirectory() as directory:
            old, new = self._catalogs(directory, [("A", 2.0)], [("B", 2.0)])
            exit_code, stdout, _ = self._run([str(old), str(new)])

        self.assertEqual(exit_code, 0)
        self.assertIn("- Added: 1", stdout)

    def test_per_input_csv_safe_flags(self):
        # Codex: a safe CSV and a plain CSV must be comparable in either order.
        records = [PromptRecord("=A", "d", "a", "gpt", "text", 1, 1.0),
                   PromptRecord("'=B", "d", "b", "gpt", "text", 2, 1.0)]
        with TemporaryDirectory() as directory:
            safe = write_export(Path(directory) / "s", "x", "all", records, "csv", csv_safe=True)
            plain = write_export(Path(directory) / "p", "x", "all", records, "csv")
            runs = {
                "safe->plain": [str(safe), str(plain), "--previous-csv-safe"],
                "plain->safe": [str(plain), str(safe), "--current-csv-safe"],
                "safe->safe": [str(safe), str(safe), "--previous-csv-safe",
                               "--current-csv-safe"],
            }
            codes = {
                name: self._run([*argv, "--fail-on-diff", "--quiet"])[0]
                for name, argv in runs.items()
            }
            undeclared, _, _ = self._run([str(safe), str(plain), "--fail-on-diff", "--quiet"])

        self.assertEqual(codes, {"safe->plain": 0, "plain->safe": 0, "safe->safe": 0})
        self.assertEqual(undeclared, EXIT_DIFF)

    def test_missing_catalog_is_an_error(self):
        with TemporaryDirectory() as directory:
            exit_code, _, stderr = self._run(
                [str(Path(directory) / "none.json"), str(Path(directory) / "none2.json")]
            )

        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("could not load catalog", stderr)

    def test_report_write_failure_is_an_error(self):
        with TemporaryDirectory() as directory:
            old, new = self._catalogs(directory, [("A", 2.0)], [("A", 2.0)])
            with patch(
                "promptbase_exporter.cli.write_diff_report", side_effect=OSError("read-only")
            ):
                exit_code, _, stderr = self._run(
                    [str(old), str(new), "--diff-output", str(Path(directory) / "d.md")]
                )

        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("could not write diff report", stderr)


class ExtraFieldsCliTests(unittest.TestCase):
    _run_expecting_failure = ArgumentValidationTests._run_expecting_failure

    def test_unknown_field_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(
            ["@acb", "--format", "json", "--extra-fields", "tags,bogus"]
        )
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("unknown extra field(s): bogus", stderr)

    def test_txt_format_fails_before_fetch(self):
        for argv in (["--extra-fields", "tags"], ["--format", "txt", "--extra-fields", "all"]):
            exit_code, stderr = self._run_expecting_failure(["@acb", *argv])
            self.assertEqual(exit_code, EXIT_ERROR)
            self.assertIn("--extra-fields needs --format markdown, json, csv, or html", stderr)

    def test_inferred_txt_output_file_fails_before_fetch(self):
        exit_code, stderr = self._run_expecting_failure(
            ["@acb", "--mode", "all", "--output-file", "out.txt", "--extra-fields", "tags"]
        )
        self.assertEqual(exit_code, EXIT_ERROR)
        self.assertIn("--extra-fields needs", stderr)

    def test_fields_are_fetched_and_written(self):
        extra = PromptRecord(
            "A", "d", "a", "gpt", "text", 1, 1.0, tags=("x",), engine="gpt-5", unique_sales=2
        )
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), [extra]),
        ) as fetch:
            output = Path(directory) / "out.json"
            exit_code, _, _ = _quiet_main(
                ["@acb", "--mode", "all", "--extra-fields", "unique-sales,tags",
                 "--output-file", str(output)]
            )
            data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_args.kwargs["extra_fields"], ("tags", "unique_sales"))
        self.assertEqual(data[0]["tags"], ["x"])
        self.assertEqual(data[0]["unique_sales"], 2)
        self.assertNotIn("engine", data[0])

    def test_default_run_requests_no_extra_fields(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile(username="acb", uid="u"), [record("A", "text", "gpt")]),
        ) as fetch:
            exit_code, _, _ = _quiet_main(["@acb", "--mode", "all", "-o", directory])
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_args.kwargs["extra_fields"], ())


if __name__ == "__main__":
    unittest.main()

import importlib
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.cli import build_parser, main
from promptbase_exporter.config import (
    ConfigError,
    check_arguments,
    find_config_path,
    load_config,
    split_config,
)
from promptbase_exporter.models import Profile, PromptRecord

try:
    importlib.import_module("tomllib")
    HAS_TOML = True
except ModuleNotFoundError:
    HAS_TOML = False


def record(slug="a", **overrides):
    values = dict(
        title=slug.title(), description="d", slug=slug, prompt_type="gpt", domain="text",
        created=1, price=1.0, sales=0,
    )
    values.update(overrides)
    return PromptRecord(**values)


def run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


def write_config(directory, name, content):
    path = Path(directory) / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), "utf-8")
    return path


class LoadConfigTests(unittest.TestCase):
    def test_reads_json(self):
        with TemporaryDirectory() as directory:
            path = write_config(directory, "c.json", {"mode": "all"})
            self.assertEqual(load_config(path), {"mode": "all"})

    @unittest.skipUnless(HAS_TOML, "needs Python 3.11+")
    def test_reads_toml(self):
        with TemporaryDirectory() as directory:
            path = write_config(directory, "c.toml", 'mode = "all"\nlimit = 5\n')
            self.assertEqual(load_config(path), {"mode": "all", "limit": 5})

    def test_toml_without_a_toml_reader_says_how_to_fix_it(self):
        real = importlib.import_module

        def without_tomllib(name, *args):
            if name == "tomllib":
                raise ModuleNotFoundError(name)
            return real(name, *args)

        with TemporaryDirectory() as directory:
            path = write_config(directory, "c.toml", 'mode = "all"\n')
            with patch("promptbase_exporter.config.importlib.import_module", without_tomllib):
                with self.assertRaisesRegex(ConfigError, "TOML needs Python 3.11 or newer"):
                    load_config(path)

    def test_refuses_what_it_cannot_read(self):
        with TemporaryDirectory() as directory:
            cases = {
                "an unknown extension": (
                    write_config(directory, "c.yaml", "a: 1"),
                    "must be .json",
                ),
                "invalid JSON": (write_config(directory, "bad.json", "{oops"), "could not read"),
                "a list": (write_config(directory, "list.json", "[1]"), "table of settings"),
                "a missing file": (Path(directory) / "gone.json", "could not read"),
            }
            for label, (path, message) in cases.items():
                with self.subTest(label), self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    @unittest.skipUnless(HAS_TOML, "needs Python 3.11+")
    def test_invalid_toml_is_reported_with_the_path(self):
        with TemporaryDirectory() as directory:
            path = write_config(directory, "bad.toml", "mode = \n")
            with self.assertRaisesRegex(ConfigError, "could not read .*bad.toml"):
                load_config(path)


class SplitConfigTests(unittest.TestCase):
    def split(self, config):
        return split_config(config, build_parser())

    def test_flags_values_and_spellings(self):
        arguments, profiles = self.split(
            {"mode": "all", "output-dir": "out", "limit": 5, "min_price": 2.5, "dry_run": True}
        )
        self.assertEqual(
            arguments,
            ["--mode", "all", "--output-dir", "out", "--limit", "5", "--min-price", "2.5",
             "--dry-run"],
        )
        self.assertEqual(profiles, [])

    def test_false_and_null_leave_an_option_out(self):
        self.assertEqual(self.split({"quiet": False, "limit": None, "mode": None})[0], [])

    def test_comma_separated_options_accept_lists(self):
        arguments, _ = self.split(
            {"domain": ["text", "image"], "type": "gpt", "extra_fields": ["tags", "engine"]}
        )
        self.assertEqual(
            arguments,
            ["--domain", "text,image", "--type", "gpt", "--extra-fields", "tags,engine"],
        )

    def test_a_repeatable_option_takes_one_value_or_several(self):
        self.assertEqual(self.split({"diff_output": "a.md"})[0], ["--diff-output", "a.md"])
        self.assertEqual(
            self.split({"diff_output": ["a.md", "b.json"]})[0],
            ["--diff-output", "a.md", "--diff-output", "b.json"],
        )

    def test_profiles_are_returned_separately(self):
        self.assertEqual(self.split({"profiles": "@acb"}), ([], ["@acb"]))
        self.assertEqual(self.split({"profiles": ["@a", "@b"], "mode": "all"}),
                         (["--mode", "all"], ["@a", "@b"]))

    def test_unusable_settings_are_named(self):
        cases = {
            "unknown": ({"mood": "all"}, "unknown setting 'mood'"),
            "a short option": ({"m": "all"}, "unknown setting 'm'"),
            "config itself": ({"config": "x.json"}, "cannot be set in a configuration file"),
            "help": ({"help": True}, "cannot be set"),
            "version": ({"version": True}, "cannot be set"),
            "a number for a flag": ({"quiet": 1}, "'quiet' must be true or false"),
            "text for a flag": ({"dry_run": "yes"}, "'dry_run' must be true or false"),
            "a boolean for a value": ({"limit": True}, "'limit' must be a text or number"),
            "a list for a scalar": ({"mode": ["all"]}, "'mode' must be a text or number"),
            "a table": ({"mode": {"a": 1}}, "'mode' must be a text or number"),
            "a nested list item": ({"domain": [["x"]]}, "'domain' must be a text or number"),
            "same setting twice": ({"output-dir": "a", "output_dir": "b"}, "are the same setting"),
            "bad profiles": ({"profiles": 3}, "'profiles' must be a profile"),
            "bad profile item": ({"profiles": ["@a", 2]}, "'profiles' must be a profile"),
        }
        for label, (config, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ConfigError, message):
                self.split(config)


class CheckArgumentsTests(unittest.TestCase):
    def test_a_bad_value_is_reported_against_the_file(self):
        cases = {
            "choice": (["--format", "yaml"], "invalid choice: 'yaml'"),
            "number": (["--limit", "many"], "invalid int value: 'many'"),
            "exclusive": (["--free-only", "--paid-only"], "not allowed with argument"),
        }
        for label, (arguments, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ConfigError, f"c.json: .*{message}"):
                check_arguments(build_parser, arguments, Path("c.json"))

    def test_valid_arguments_pass_without_a_profile(self):
        check_arguments(build_parser, ["--mode", "all", "--limit", "3"], Path("c.json"))


class FindConfigPathTests(unittest.TestCase):
    def find(self, *argv):
        return find_config_path(build_parser(), argv)

    def test_finds_the_path_in_any_position_and_either_spelling(self):
        self.assertEqual(self.find("@acb", "--config", "c.json"), Path("c.json"))
        self.assertEqual(self.find("--config=c.json", "@acb"), Path("c.json"))
        self.assertIsNone(self.find("@acb", "--mode", "all"))

    def test_an_abbreviation_the_real_parser_accepts_is_found_too(self):
        self.assertEqual(self.find("@acb", "--conf", "c.json"), Path("c.json"))

    def test_other_options_are_not_mistaken_for_it(self):
        self.assertIsNone(self.find("@acb", "--compare", "x.json"))
        self.assertIsNone(self.find("@acb", "--compare-csv-safe"))


class ConfigInTheCommandTests(unittest.TestCase):
    def fetch(self, *records):
        return patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile("acb", "u"), list(records or [record()])),
        )

    def test_the_file_supplies_the_profile_and_the_options(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            config = write_config(directory, "c.json", {
                "profiles": ["@acb"], "mode": "all", "format": "json",
                "output_dir": str(Path(directory) / "out"), "quiet": True,
            })
            code, stdout, _ = run(["--config", str(config)])
            written = sorted(p.name for p in (Path(directory) / "out").iterdir())
        self.assertEqual((code, stdout), (0, ""))
        self.assertEqual(written, ["acb_all_prompts.json"])
        self.assertEqual(fetch.call_args.args[0], "@acb")

    def test_the_command_line_overrides_the_file(self):
        with TemporaryDirectory() as directory, self.fetch():
            config = write_config(directory, "c.json", {
                "profiles": ["@acb"], "mode": "all", "format": "json",
                "output_dir": str(Path(directory) / "from-file"), "quiet": True,
            })
            code, _, _ = run([
                "--config", str(config), "--format", "csv",
                "--output-dir", str(Path(directory) / "from-cli"),
            ])
            written = sorted(p.name for p in (Path(directory) / "from-cli").iterdir())
            file_dir_exists = (Path(directory) / "from-file").exists()
        self.assertEqual(code, 0)
        self.assertEqual(written, ["acb_all_prompts.csv"])
        self.assertFalse(file_dir_exists)

    def test_profiles_on_the_command_line_replace_the_files(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            config = write_config(directory, "c.json", {"profiles": ["@from-file"], "mode": "all"})
            code, _, _ = run(["@from-cli", "--config", str(config), "--dry-run", "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["@from-cli"])

    def test_no_profile_anywhere_is_the_usual_usage_error(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            config = write_config(directory, "c.json", {"mode": "all"})
            code, _, stderr = run(["--config", str(config)])
        self.assertEqual(code, 2)
        self.assertIn("the following arguments are required: profile", stderr)
        fetch.assert_not_called()

    def test_a_bad_file_stops_before_anything_is_fetched(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            cases = {
                "unknown key": (write_config(directory, "a.json", {"mood": 1}), "unknown setting"),
                "bad value": (
                    write_config(directory, "b.json", {"profiles": ["@a"], "format": "yaml"}),
                    "b.json: argument -f/--format",
                ),
                "missing file": (Path(directory) / "gone.json", "could not read"),
            }
            for label, (path, message) in cases.items():
                with self.subTest(label):
                    code, _, stderr = run(["--config", str(path)])
                    self.assertEqual(code, 1)
                    self.assertIn(message, stderr)
        fetch.assert_not_called()

    def test_settings_the_command_line_validates_still_apply_to_the_file(self):
        with TemporaryDirectory() as directory, self.fetch():
            config = write_config(directory, "c.json",
                                  {"profiles": ["@acb"], "diff_output": "d.md"})
            code, _, stderr = run(["--config", str(config)])
        self.assertEqual(code, 1)
        self.assertIn("--diff-output requires --compare or --update-file", stderr)

    def test_a_repeatable_option_adds_the_files_values_to_the_command_lines(self):
        with TemporaryDirectory() as directory, self.fetch():
            catalog = Path(directory) / "catalog.json"
            catalog.write_text("[]", "utf-8")
            config = write_config(directory, "c.json", {
                "profiles": ["@acb"], "mode": "all", "compare": str(catalog),
                "diff_output": str(Path(directory) / "from-file.md"), "quiet": True,
            })
            code, _, _ = run(["--config", str(config), "--diff-output",
                              str(Path(directory) / "from-cli.json")])
            names = sorted(p.name for p in Path(directory).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(names, ["c.json", "catalog.json", "from-cli.json", "from-file.md"])

    @unittest.skipUnless(HAS_TOML, "needs Python 3.11+")
    def test_a_toml_file_works_end_to_end(self):
        with TemporaryDirectory() as directory, self.fetch():
            out = (Path(directory) / "out").as_posix()
            config = write_config(
                directory, "c.toml",
                f'profiles = ["@acb"]\nmode = "all"\nformat = "csv"\noutput_dir = "{out}"\n'
                'min_sales = 0\nquiet = true\n',
            )
            code, _, _ = run(["--config", str(config)])
            written = sorted(p.name for p in (Path(directory) / "out").iterdir())
        self.assertEqual((code, written), (0, ["acb_all_prompts.csv"]))

    def test_an_abbreviated_option_does_not_silently_drop_the_file(self):
        # The file says quiet; if --conf were not recognised as --config, the run
        # would print its summary and nobody would be told the file was ignored.
        with TemporaryDirectory() as directory, self.fetch():
            config = write_config(directory, "c.json", {"mode": "text", "quiet": True})
            full, full_out, _ = run(["@acb", "--config", str(config), "--dry-run"])
            short, short_out, _ = run(["@acb", "--conf", str(config), "--dry-run"])
        self.assertEqual((full, short), (0, 0))
        self.assertEqual((full_out, short_out), ("", ""))

    def test_help_documents_the_option(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit):
            main(["--help"])
        self.assertIn("--config FILE", stdout.getvalue())
        self.assertIn("[profile ...]", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

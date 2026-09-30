import importlib
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.cli import main
from promptbase_exporter.formatting import record_to_dict
from promptbase_exporter.layout import (
    count_files,
    folder_name,
    render_file,
    safe_stem,
    unique_names,
    write_markdown_files,
)
from promptbase_exporter.models import EXTRA_FIELDS, Profile, PromptRecord

try:
    yaml = importlib.import_module("yaml")
except ModuleNotFoundError:
    yaml = None


def make(slug="one", **overrides):
    values = dict(
        title="Title " + slug, description="Line one\nLine two", slug=slug, prompt_type="gpt",
        domain="text", created=1_767_225_600_000, price=2.5, tags=("a", "b"), engine="gpt-5",
        unique_sales=2,
    )
    values.update(overrides)
    return PromptRecord(**values)


def split_file(text):
    assert text.startswith("---\n")
    front, _, body = text[4:].partition("\n---\n")
    assert body.startswith("\n")  # a blank line separates the front matter from the body
    return front, body[1:]


def parse_front(front):
    """Read the front matter the way it is written: one `key: <JSON value>` per line."""
    data = {}
    for line in front.split("\n"):
        key, _, value = line.partition(": ")
        data[key] = json.loads(value)
    return data


def run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class SafeStemTests(unittest.TestCase):
    def test_real_slugs_are_unchanged(self):
        for slug in ("glassmorphism-ui-elements-2", "a", "x" * 47, "v1.2_beta"):
            self.assertEqual(safe_stem(slug), slug)

    def test_a_slug_can_never_leave_the_directory_or_hide(self):
        cases = {
            "../../etc/passwd": "etc_passwd",
            "..\\..\\windows\\system32": "windows_system32",
            "/absolute": "absolute",
            "a/b": "a_b",
            ".hidden": "hidden",
            "...": "untitled",
            "": "untitled",
            "   ": "untitled",
            "-leading": "leading",
        }
        for slug, expected in cases.items():
            with self.subTest(slug=slug):
                self.assertEqual(safe_stem(slug), expected)
                self.assertNotIn("/", safe_stem(slug))
                self.assertNotIn("\\", safe_stem(slug))
                self.assertFalse(safe_stem(slug).startswith("."))

    def test_characters_a_file_system_dislikes_are_replaced(self):
        self.assertEqual(safe_stem('a:b*c?"d<e>f|g'), "a_b_c_d_e_f_g")
        self.assertEqual(safe_stem("üñí çödé"), "d")  # runs of unsafe characters collapse

    def test_windows_device_names_get_a_suffix(self):
        for slug in ("con", "CON", "nul", "Aux", "com1", "LPT9", "prn"):
            with self.subTest(slug=slug):
                self.assertTrue(safe_stem(slug).endswith("_"))
        self.assertEqual(safe_stem("console"), "console")
        self.assertEqual(safe_stem("com10"), "com10")

    def test_a_long_slug_is_shortened(self):
        self.assertEqual(len(safe_stem("a" * 500)), 100)


class UniqueNamesTests(unittest.TestCase):
    def test_names_are_distinct_even_when_case_folded(self):
        records = [make("a"), make("A"), make("a"), make("a-2")]
        names = unique_names(records)
        self.assertEqual(len({name.casefold() for name in names}), 4)
        self.assertTrue(all(name.endswith(".md") for name in names))
        self.assertEqual(names[0], "a.md")

    def test_distinct_slugs_that_sanitise_alike_do_not_collide(self):
        names = unique_names([make("a/b"), make("a?b"), make("a*b")])
        self.assertEqual(len(set(names)), 3)

    def test_names_are_stable_between_runs(self):
        records = [make("x"), make("X"), make("y")]
        self.assertEqual(unique_names(records), unique_names(records))

    def test_a_name_depends_on_its_own_slug_only(self):
        # Filtering or re-sorting must never move one prompt's file onto another's.
        weird = [make("a/b"), make("a?b"), make("A"), make("a")]
        together = dict(zip((r.slug for r in weird), unique_names(weird), strict=True))
        for subset in ([weird[1]], [weird[1], weird[0]], list(reversed(weird)), weird[2:]):
            alone = dict(zip((r.slug for r in subset), unique_names(subset), strict=True))
            for slug, name in alone.items():
                with self.subTest(slug=slug, subset=len(subset)):
                    self.assertEqual(name, together[slug])
        self.assertEqual(len(set(together.values())), 4)

    def test_a_safe_lowercase_slug_is_its_own_file_name(self):
        self.assertEqual(unique_names([make("glassmorphism-ui-elements-2")]),
                         ["glassmorphism-ui-elements-2.md"])

    def test_a_changed_slug_keeps_its_safe_form_and_gains_a_digest(self):
        for slug in ("a/b", "A", "x" * 300, "con"):
            with self.subTest(slug=slug):
                name = unique_names([make(slug)])[0]
                self.assertRegex(name, r"^[A-Za-z0-9._-]+-[0-9a-f]{8}\.md$")
                self.assertNotEqual(name, f"{slug}.md")


class RenderFileTests(unittest.TestCase):
    def test_front_matter_holds_every_field_but_the_description(self):
        record = make()
        front, body = split_file(render_file(record, EXTRA_FIELDS))
        expected = record_to_dict(record, EXTRA_FIELDS)
        expected.pop("description")
        self.assertEqual(parse_front(front), expected)
        self.assertEqual(body, "# Title one\n\nLine one\nLine two\n")

    def test_default_fields_only_unless_extras_are_requested(self):
        front, _ = split_file(render_file(make()))
        self.assertNotIn("tags:", front)
        self.assertNotIn("engine:", front)
        self.assertIn("slug: \"one\"", front)

    def test_bundles_and_apps_say_what_they_are(self):
        front, _ = split_file(render_file(make(item_type="bundle")))
        self.assertIn('item_type: "bundle"', front)
        self.assertIn("/bundle/one", front)

    def test_hostile_text_stays_a_single_quoted_value(self):
        record = make(title='x: "y"\n---\n# z', description="---\nnot front matter\n---", slug="s")
        text = render_file(record)
        front, body = split_file(text)
        self.assertEqual(parse_front(front)["title"], 'x: "y"\n---\n# z')
        self.assertNotIn("\n", front.split("title: ", 1)[1].split("\n")[0])
        self.assertIn("---\nnot front matter\n---", body)
        self.assertTrue(body.startswith("# x: \"y\" --- # z\n"))

    def test_values_that_look_like_other_types_stay_strings(self):
        record = make(title="yes", prompt_type="null", domain="2026-01-01", slug="123")
        data = parse_front(split_file(render_file(record))[0])
        self.assertEqual((data["title"], data["type"], data["domain"], data["slug"]),
                         ("yes", "null", "2026-01-01", "123"))

    @unittest.skipIf(yaml is None, "PyYAML is not installed")
    def test_a_real_yaml_parser_reads_the_same_values(self):
        record = make(title="yes: no # maybe", prompt_type="on", domain="2026-01-01",
                      slug="123", price=1e-05, tags=("a, b", "true", "null"))
        front, _ = split_file(render_file(record, EXTRA_FIELDS))
        expected = record_to_dict(record, EXTRA_FIELDS)
        expected.pop("description")
        self.assertEqual(yaml.safe_load(front), expected)

    def test_characters_yaml_would_alter_or_refuse_are_escaped(self):
        # U+0085 is folded to a space by a YAML reader, and U+007F..U+009F make a
        # strict reader reject the whole file; the rest are YAML line breaks or
        # outside the characters YAML allows.
        code_points = [*range(0x7F, 0xA0), 0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF]
        for code in code_points:
            character = chr(code)
            with self.subTest(code=hex(code)):
                front, _ = split_file(render_file(make(title="a" + character + "b")))
                self.assertNotIn(character, front)
                self.assertIn(f"\\u{code:04x}", front)
                self.assertEqual(parse_front(front)["title"], "a" + character + "b")

    def test_ordinary_non_ascii_text_is_left_readable(self):
        front, _ = split_file(render_file(make(title="Ünï \U0001f4cc çö")))
        self.assertIn('title: "Ünï \U0001f4cc çö"', front)

    def test_a_lone_surrogate_becomes_the_replacement_character(self):
        front, _ = split_file(render_file(make(title="a" + chr(0xD800) + "b")))
        self.assertEqual(parse_front(front)["title"], "a" + chr(0xFFFD) + "b")
        front.encode("utf-8")  # must be writable

    @unittest.skipIf(yaml is None, "PyYAML is not installed")
    def test_a_real_yaml_parser_reads_back_every_escaped_character(self):
        for code in [*range(0x00, 0x20), *range(0x7F, 0xA0), 0x2028, 0x2029, 0xFEFF]:
            title = "a" + chr(code) + "b"
            with self.subTest(code=hex(code)):
                front, _ = split_file(render_file(make(title=title)))
                self.assertEqual(yaml.safe_load(front)["title"], title)

    def test_awkward_numbers_are_written_in_a_form_yaml_reads_as_numbers(self):
        for value, text in ((1e-05, "1.0e-05"), (2.5, "2.5"), (0.0, "0.0"), (1e22, "1.0e+22")):
            front, _ = split_file(render_file(make(price=value)))
            self.assertIn(f"price: {text}\n", front + "\n")
        front, _ = split_file(render_file(make(price=float("nan"), discount=float("inf"))))
        self.assertIn("price: null", front)
        self.assertIn("discount: null", front)

    def test_a_blank_description_and_odd_newlines(self):
        self.assertTrue(render_file(make(description="")).endswith("# Title one\n\n\n"))
        body = split_file(render_file(make(description="a\r\nb\rc\n\n")))[1]
        self.assertEqual(body, "# Title one\n\na\nb\nc\n")


class WriteFilesTests(unittest.TestCase):
    def test_writes_one_file_per_record_in_a_named_folder(self):
        records = [make("one"), make("two")]
        with TemporaryDirectory() as directory:
            folder = write_markdown_files(Path(directory), "acb", "all", records)
            names = sorted(p.name for p in folder.iterdir())
            count = count_files(folder, unique_names(records))
        self.assertEqual(folder.name, "acb_all_prompts")
        self.assertEqual(names, ["one.md", "two.md"])
        self.assertEqual(count, 2)

    def test_folder_names_follow_the_kind_and_timestamp(self):
        self.assertEqual(folder_name("acb", "text"), "acb_text_prompts")
        self.assertEqual(folder_name("a/b", "all", "bundle"), "a_b_all_bundles")
        self.assertEqual(
            folder_name("acb", "all", "app", "20260101_120000"), "acb_all_apps_20260101_120000"
        )

    def test_a_hostile_slug_stays_inside_the_folder(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = write_markdown_files(root / "out", "acb", "all", [make("../../evil")])
            inside = [p for p in root.rglob("*") if p.is_file()]
            self.assertEqual([p.parent for p in inside], [folder])
            self.assertRegex(inside[0].name, r"^evil-[0-9a-f]{8}\.md$")

    def test_a_second_run_replaces_files_and_leaves_everything_else_alone(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = write_markdown_files(root, "acb", "all", [make("one", views=1)])
            (folder / "my-notes.md").write_text("mine", encoding="utf-8")
            (folder / "old-prompt.md").write_text("stale", encoding="utf-8")
            write_markdown_files(root, "acb", "all", [make("one", views=99)])
            names = sorted(p.name for p in folder.iterdir())
            updated = (folder / "one.md").read_text(encoding="utf-8")
            notes = (folder / "my-notes.md").read_text(encoding="utf-8")
        self.assertEqual(names, ["my-notes.md", "old-prompt.md", "one.md"])  # no temp files
        self.assertIn("views: 99", updated)
        self.assertEqual(notes, "mine")

    def test_count_files_ignores_missing_empty_and_directories(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "ok.md").write_text("x", encoding="utf-8")
            (folder / "empty.md").write_text("", encoding="utf-8")
            (folder / "dir.md").mkdir()
            self.assertEqual(count_files(folder, ["ok.md", "empty.md", "dir.md", "gone.md"]), 1)

    def test_files_are_utf8_with_unix_newlines(self):
        with TemporaryDirectory() as directory:
            folder = write_markdown_files(
                Path(directory), "acb", "all", [make("u", title="Ünï \U0001f4cc", description="é")]
            )
            raw = (folder / "u.md").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn("Ünï \U0001f4cc".encode(), raw)


class LayoutInTheCommandTests(unittest.TestCase):
    def fetch(self, *records):
        return patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile("acb", "u"), list(records or [make("one"), make("two")])),
        )

    def test_layout_files_writes_a_folder_per_catalog(self):
        with TemporaryDirectory() as directory, self.fetch():
            code, stdout, _ = run(["@acb", "--layout", "files", "-o", directory])
            tree = sorted(
                os.path.relpath(p, directory).replace(os.sep, "/")
                for p in Path(directory).rglob("*.md")
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            tree,
            [f"acb_{mode}_prompts/{slug}.md"
             for mode in ("all", "image", "text") for slug in ("one", "two")
             if mode != "image"],
        )
        self.assertIn("Wrote   all:    2 prompts ->", stdout)

    def test_the_default_layout_is_unchanged(self):
        with TemporaryDirectory() as directory, self.fetch():
            code, _, _ = run(["@acb", "--mode", "all", "-o", directory, "--quiet"])
            names = sorted(p.name for p in Path(directory).iterdir())
        self.assertEqual((code, names), (0, ["acb_all_prompts.txt"]))

    def test_extra_fields_bundles_and_timestamps(self):
        bundle = make("kit", item_type="bundle")
        with TemporaryDirectory() as directory, self.fetch(bundle):
            code, _, _ = run(["@acb", "--layout", "files", "--item-type", "bundle", "--mode",
                              "all", "--extra-fields", "tags", "--timestamp-filenames",
                              "-o", directory, "--quiet"])
            folder = next(Path(directory).iterdir())
            text = (folder / "kit.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertRegex(folder.name, r"^acb_all_bundles_\d{8}_\d{6}$")
        self.assertIn('item_type: "bundle"', text)
        self.assertIn('tags: ["a", "b"]', text)

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as directory, self.fetch():
            code, _, _ = run(["@acb", "--layout", "files", "--dry-run", "-o", directory])
            entries = list(Path(directory).iterdir())
        self.assertEqual((code, entries), (0, []))

    def test_incompatible_options_fail_before_any_fetch(self):
        cases = [
            (["--output-file", "x.md"], "cannot be used with --output-file, --compare, or"),
            (["--compare", "x.json", "--mode", "all"], "cannot be used with"),
            (["--update-file", "x.md", "--mode", "all"], "cannot be used with"),
            (["--format", "json"], "--layout files writes Markdown"),
            (["--format", "csv"], "--layout files writes Markdown"),
        ]
        with self.fetch() as fetch:
            for extra, message in cases:
                with self.subTest(extra=extra):
                    code, _, stderr = run(["@acb", "--layout", "files", *extra])
                    self.assertEqual(code, 1)
                    self.assertIn(message, stderr)
        fetch.assert_not_called()

    def test_an_explicit_markdown_format_is_fine(self):
        with TemporaryDirectory() as directory, self.fetch():
            code, _, _ = run(["@acb", "--layout", "files", "--format", "markdown", "--mode",
                              "all", "-o", directory, "--quiet"])
        self.assertEqual(code, 0)

    def test_missing_files_fail_the_check(self):
        with TemporaryDirectory() as directory, self.fetch(), patch(
            "promptbase_exporter.cli.count_files", return_value=1
        ):
            code, _, stderr = run(["@acb", "--layout", "files", "--mode", "all", "-o", directory])
        self.assertEqual(code, 1)
        self.assertIn("validation failed", stderr)
        self.assertIn("expected 2, wrote 1", stderr)

    def test_a_write_error_is_reported_not_raised(self):
        with self.fetch(), patch(
            "promptbase_exporter.cli.write_markdown_files", side_effect=OSError("disk full")
        ):
            code, _, stderr = run(["@acb", "--layout", "files", "--mode", "all"])
        self.assertEqual(code, 1)
        self.assertIn("could not write export", stderr)


if __name__ == "__main__":
    unittest.main()

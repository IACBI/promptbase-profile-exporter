import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter.layout import LAYOUTS
from promptbase_exporter.models import Profile, PromptRecord
from promptbase_exporter.web import (
    ExportRequest,
    WebInputError,
    build_request_config,
    render_form,
    run_export,
)


@contextmanager
def working_directory(path):
    original = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)


def record(slug):
    return PromptRecord(
        title=slug.title(), description="d", slug=slug, prompt_type="gpt", domain="text",
        created=1, price=1.0, tags=("a",),
    )


def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
    return Profile("acb", "u"), [record("one"), record("two")]


class LayoutFormTests(unittest.TestCase):
    def test_layout_defaults_to_catalog_and_is_validated(self):
        self.assertEqual(build_request_config({"profile": "acb"}).layout, "catalog")
        with self.assertRaisesRegex(WebInputError, "Unsupported layout: tree"):
            build_request_config({"profile": "acb", "layout": "tree"})

    def test_files_needs_the_markdown_format(self):
        request = build_request_config(
            {"profile": "acb", "layout": "files", "format": "markdown"}
        )
        self.assertEqual(request.layout, "files")
        for export_format in ("txt", "json", "csv", "html", "ndjson"):
            with self.subTest(export_format), self.assertRaisesRegex(
                WebInputError, "writes Markdown: choose the markdown format"
            ):
                build_request_config(
                    {"profile": "acb", "layout": "files", "format": export_format}
                )

    def test_files_cannot_be_compared(self):
        with self.assertRaisesRegex(WebInputError, "cannot be compared"):
            build_request_config({
                "profile": "acb", "layout": "files", "format": "markdown",
                "mode": "all", "compare_file": "x.json",
            })

    def test_form_offers_every_layout_and_keeps_the_selection(self):
        html = render_form(ExportRequest(profile_input="acb", layout="files"))
        for layout in LAYOUTS:
            self.assertIn(f'<option value="{layout}"', html)
        self.assertIn('<option value="files" selected>files</option>', html)


class LayoutRunTests(unittest.TestCase):
    def test_files_are_written_and_reported_as_a_folder_without_a_download_link(self):
        with TemporaryDirectory() as directory, working_directory(directory):
            request = build_request_config({
                "profile": "acb", "layout": "files", "format": "markdown", "mode": "all",
                "output_dir": "out", "extra_fields": ["tags"],
            })
            result = run_export(request, fetcher=fetcher)
            folder = result.files[0].path
            names = sorted(p.name for p in folder.iterdir())
            text = (folder / "one.md").read_text(encoding="utf-8")
            page = render_form(request, result=result)
        self.assertEqual(folder.name, "acb_all_prompts")
        self.assertEqual(names, ["one.md", "two.md"])
        self.assertEqual((result.files[0].count, result.files[0].mode), (2, "all"))
        self.assertIn('tags: ["a"]', text)
        self.assertIn("acb_all_prompts", page)
        self.assertNotIn("/download?file=", page)
        self.assertIn("<td>—</td>", page)

    def test_split_mode_writes_a_folder_per_catalog(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="split", output_dir=Path(directory),
                export_format="markdown", layout="files",
            )
            result = run_export(request, fetcher=fetcher)
            folders = sorted(item.path.name for item in result.files)
        self.assertEqual(folders, ["acb_all_prompts", "acb_image_prompts", "acb_text_prompts"])

    def test_missing_files_fail_the_check(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory),
                export_format="markdown", layout="files",
            )
            with self.assertRaisesRegex(WebInputError, "expected 2, wrote 0"):
                run_export(
                    request, fetcher=fetcher,
                    files_writer=lambda output_dir, *_a, **_k: Path(directory),
                )

    def test_the_catalog_layout_is_unchanged(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory),
                export_format="json",
            )
            with patch("promptbase_exporter.web.write_markdown_files") as files_writer:
                result = run_export(request, fetcher=fetcher)
            name = result.files[0].path.name
        files_writer.assert_not_called()
        self.assertEqual(name, "acb_all_prompts.json")


if __name__ == "__main__":
    unittest.main()

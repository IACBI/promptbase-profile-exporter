import io
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from promptbase_exporter import convert
from promptbase_exporter.cli import diff_main, main
from promptbase_exporter.console import make_output_safe, printable
from promptbase_exporter.models import Profile, PromptRecord
from promptbase_exporter.web import main as web_main
from tests.scratch import use_scratch_working_directory

EMOJI_TITLE = "Pin \U0001f4cc prompt"


def setUpModule():
    use_scratch_working_directory()


def legacy_stream():
    """A text stream like a Windows pipe: a code page that cannot hold an emoji."""
    return io.TextIOWrapper(
        io.BytesIO(), encoding="cp1254", errors="strict", newline="\n", write_through=True
    )


@contextmanager
def legacy_console():
    out, err = legacy_stream(), legacy_stream()
    with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
        yield out, err


class MakeOutputSafeTests(unittest.TestCase):
    def test_a_legacy_stream_no_longer_raises_on_unencodable_text(self):
        stream = legacy_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write("\U0001f4cc")
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            make_output_safe()
        stream.write("ok \U0001f4cc ok\n")
        self.assertEqual(stream.buffer.getvalue(), b"ok ? ok\n")

    def test_text_the_code_page_can_hold_is_unchanged(self):
        stream = legacy_stream()
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            make_output_safe()
        stream.write("ğüşıöç İ\n")
        self.assertEqual(stream.buffer.getvalue().decode("cp1254"), "ğüşıöç İ\n")

    def test_streams_that_cannot_be_reconfigured_are_left_alone(self):
        detached = legacy_stream()
        detached.detach()  # reconfigure() now raises ValueError
        with patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", detached):
            make_output_safe()  # a StringIO has no reconfigure, a detached stream raises


class EveryEntryPointSurvivesLegacyOutputTests(unittest.TestCase):
    def test_export_error_listing_an_emoji_title(self):
        record = PromptRecord(EMOJI_TITLE, "", "pin", "gpt", "text", 1, 1.0)
        with legacy_console() as (_out, err), patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile("acb", "u"), [record]),
        ):
            exit_code = main(["@acb", "--mode", "all", "--dry-run"])
        self.assertEqual(exit_code, 1)
        self.assertIn(b"Pin ? prompt (pin)", err.buffer.getvalue())

    def test_normal_output_is_unchanged(self):
        record = PromptRecord("A", "d", "a", "gpt", "text", 1, 1.0)
        with legacy_console() as (out, _err), patch(
            "promptbase_exporter.cli.fetch_prompts",
            return_value=(Profile("acb", "u"), [record]),
        ):
            exit_code = main(["@acb", "--mode", "all", "--dry-run"])
        self.assertEqual(exit_code, 0)
        self.assertIn(b"Approved prompts found: 1", out.buffer.getvalue())

    def test_diff_command_error_naming_an_unencodable_path(self):
        with legacy_console() as (_out, err):
            exit_code = diff_main(["\U0001f4cc-old.json", "\U0001f4cc-new.json"])
        self.assertEqual(exit_code, 1)
        self.assertIn(b"could not load catalog ?-old.json", err.buffer.getvalue())

    def test_convert_command_error_naming_an_unencodable_path(self):
        with legacy_console() as (_out, err):
            exit_code = convert.main(["\U0001f4cc.json", "-f", "csv"])
        self.assertEqual(exit_code, 1)
        self.assertIn(b"error:", err.buffer.getvalue())
        self.assertIn(b"?.json", err.buffer.getvalue())

    def test_web_command_prepares_its_output_before_parsing(self):
        with legacy_console() as (out, _err):
            with self.assertRaises(SystemExit):
                web_main(["--version"])
            self.assertEqual(out.errors, "replace")



class PrintableTests(unittest.TestCase):
    def test_bidirectional_controls_are_removed(self):
        rlo, pdf, lri, pdi, rlm = chr(0x202E), chr(0x202C), chr(0x2066), chr(0x2069), chr(0x200F)
        text = f"price {rlo}01${pdf} ok {lri}x{pdi} {rlm}y"
        self.assertEqual(printable(text), "price 01$ ok x y")

    def test_right_to_left_letters_are_kept(self):
        shalom = "".join(map(chr, (0x05E9, 0x05DC, 0x05D5, 0x05DD)))
        self.assertEqual(printable(shalom), shalom)

    def test_remote_text_stays_on_one_line_without_control_characters(self):
        esc, bell = chr(27), chr(7)
        hostile = f"Nice\n::error title=x::fake\r\n## Heading{esc}]8;;https://e{bell}link{esc}[2J"
        text = printable(hostile)
        self.assertNotIn("\n", text)
        self.assertNotIn(esc, text)
        self.assertNotIn(bell, text)
        self.assertEqual(text, "Nice ::error title=x::fake ## Heading]8;;https://elink[2J")

    def test_ordinary_text_is_unchanged(self):
        self.assertEqual(printable(EMOJI_TITLE), EMOJI_TITLE)
        self.assertEqual(printable("  padded  "), "padded")

    def test_missing_descriptions_are_listed_one_per_line(self):
        record = PromptRecord(
            title="Two\n::warning::lines", description="", slug="two", prompt_type="gpt",
            domain="text", created=1, price=0.0,
        )
        stderr = io.StringIO()
        with patch("promptbase_exporter.cli.fetch_prompts",
                   return_value=(Profile("acb", "u"), [record])), \
                patch("sys.stderr", stderr), patch("sys.stdout", io.StringIO()):
            code = main(["@acb", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertIn("  - Two ::warning::lines (two)\n", stderr.getvalue())
        self.assertNotIn("\n::warning", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

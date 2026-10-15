import http.client
import io
import os
import shutil
import socket
import threading
import unittest
from contextlib import redirect_stderr
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from promptbase_exporter.formatting import write_export
from promptbase_exporter.models import EXTRA_FIELDS, ITEM_TYPES, Profile, PromptRecord
from promptbase_exporter.web import (
    _EXPORT_FILENAME_RE,
    _EXPORT_LOCK,
    MAX_FORM_BYTES,
    MAX_HANDLER_THREADS,
    ExportRequest,
    PromptBaseWebHandler,
    WebExportResult,
    WebInputError,
    _ipv6_bind_address,
    _make_server,
    _url_host,
    _warn_if_exposed,
    build_request_config,
    render_form,
    run_export,
)


class _FakeServer:
    def __init__(self, address):
        self.server_address = address


def _make_handler(headers, address=("127.0.0.1", 8765)):
    handler = PromptBaseWebHandler.__new__(PromptBaseWebHandler)
    message = Message()
    for key, value in headers.items():
        message[key] = value
    handler.headers = message
    handler.server = _FakeServer(address)
    return handler


def record(title, domain, prompt_type, created=1, price=0.0, description=None):
    return PromptRecord(
        title=title,
        description=f"{title} description" if description is None else description,
        slug=title.lower().replace(" ", "-"),
        prompt_type=prompt_type,
        domain=domain,
        created=created,
        price=price,
    )


class WebTests(unittest.TestCase):
    def test_build_request_config_normalizes_form_values(self):
        config = build_request_config(
            {
                "profile": ["https://promptbase.com/profile/acb"],
                "mode": ["text"],
                "format": ["json"],
                "sort": ["views"],
                "domain": [" text "],
                "prompt_type": ["gpt"],
                "price_filter": ["paid"],
                "min_price": ["1.5"],
                "max_price": ["5"],
                "limit": ["10"],
                "since": ["2026-01-01"],
                "until": ["2026-12-31"],
                "timestamp_filenames": ["1"],
                "allow_missing_descriptions": ["on"],
                "output_dir": ["exports/acb"],
            }
        )

        self.assertEqual(config.profile_input, "https://promptbase.com/profile/acb")
        self.assertEqual(config.mode, "text")
        self.assertEqual(config.export_format, "json")
        self.assertEqual(config.sort, "views")
        self.assertEqual(config.domain, "text")
        self.assertEqual(config.prompt_type, "gpt")
        self.assertEqual(config.price_filter, "paid")
        self.assertEqual(config.min_price, 1.5)
        self.assertEqual(config.max_price, 5.0)
        self.assertEqual(config.limit, 10)
        self.assertEqual(config.since, "2026-01-01")
        self.assertEqual(config.until, "2026-12-31")
        self.assertIsNotNone(config.since_created)
        self.assertIsNotNone(config.until_created)
        self.assertLess(config.since_created, config.until_created)
        self.assertTrue(config.timestamp_filenames)
        self.assertTrue(config.allow_missing_descriptions)
        self.assertIsInstance(config.output_dir, Path)
        self.assertTrue(config.output_dir.is_absolute())
        self.assertTrue(config.output_dir.is_relative_to(Path.cwd().resolve()))

    def test_build_request_config_rejects_output_dir_escape(self):
        for escape in ("..", "../outside", "../../etc", "exports/../../secrets"):
            with self.assertRaises(WebInputError):
                build_request_config({"profile": "acb", "output_dir": escape})

    def test_build_request_config_rejects_absolute_output_dir(self):
        absolute = "C:\\Windows\\Temp" if Path("C:\\").exists() else "/etc"
        with self.assertRaises(WebInputError):
            build_request_config({"profile": "acb", "output_dir": absolute})

    def test_build_request_config_rejects_unc_output_dir(self):
        with self.assertRaises(WebInputError):
            build_request_config({"profile": "acb", "output_dir": "//attacker.example/share"})

    def test_build_request_config_rejects_null_byte_output_dir(self):
        # Would otherwise pass validation and fail later in mkdir as a 500.
        with self.assertRaises(WebInputError):
            build_request_config({"profile": "acb", "output_dir": "exports\0x"})

    def test_a_tilde_output_dir_is_a_folder_name_not_the_home_directory(self):
        base = Path.cwd().resolve()
        for raw in ("~", "~/exports", "~root/x"):
            with self.subTest(raw=raw):
                config = build_request_config({"profile": "acb", "output_dir": raw})
                self.assertEqual(config.output_dir, base / raw)
                self.assertTrue(config.output_dir.is_relative_to(base))
        # The home directory itself, spelled out, is still outside and refused.
        with self.assertRaises(WebInputError):
            build_request_config({"profile": "acb", "output_dir": str(Path.home())})

    def test_build_request_config_rejects_invalid_prices(self):
        with self.assertRaises(WebInputError):
            build_request_config(
                {
                    "profile": "acb",
                    "min_price": "10",
                    "max_price": "5",
                }
            )

    def test_build_request_config_rejects_non_finite_prices(self):
        for field in ("min_price", "max_price"):
            for value in ("nan", "inf", "-inf"):
                with self.assertRaises(WebInputError):
                    build_request_config({"profile": "acb", field: value})

    def test_build_request_config_rejects_invalid_dates(self):
        # The year-limit values used to escape as OverflowError: a 500, not a 400.
        for field in ("since", "until"):
            for value in ("not-a-date", "9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
                with self.subTest(field=field, value=value), self.assertRaises(WebInputError):
                    build_request_config({"profile": "acb", field: value})

    def test_render_form_contains_expected_controls(self):
        html = render_form(ExportRequest(profile_input="@acb"))

        self.assertIn("PromptBase Profile Exporter", html)
        self.assertIn('name="profile"', html)
        self.assertIn('name="mode"', html)
        self.assertIn('value="@acb"', html)
        self.assertIn('value="split" selected', html)
        self.assertIn('name="price_filter"', html)
        self.assertIn('name="since"', html)
        self.assertIn('name="limit"', html)

    def test_run_export_writes_split_outputs(self):
        records = [
            record("Text One", "text", "gpt", created=3),
            record("Image One", "image", "chatgpt-image", created=2),
        ]

        def fetcher(profile_input, extra_fields=(), item_type="prompt"):
            self.assertEqual(profile_input, "@acb")
            return Profile(username="acb", uid="uid-1"), records

        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="@acb",
                output_dir=Path(directory),
                mode="split",
                export_format="csv",
            )

            result = run_export(request, fetcher=fetcher)

            self.assertEqual(result.username, "acb")
            self.assertEqual(result.total_records, 2)
            self.assertEqual(result.selected_records, 2)
            self.assertEqual([item.mode for item in result.files], ["all", "text", "image"])
            self.assertEqual([item.count for item in result.files], [2, 1, 1])
            for item in result.files:
                self.assertTrue(item.path.exists())

    def test_run_export_applies_filters_and_sorting(self):
        records = [
            record("Cheap", "text", "gpt", created=3, price=1.0),
            record("Expensive", "text", "gpt", created=2, price=9.0),
            record("Image", "image", "chatgpt-image", created=1, price=4.0),
        ]

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), records

        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="@acb",
                output_dir=Path(directory),
                mode="text",
                export_format="txt",
                domain="text",
                price_filter="paid",
                min_price=5.0,
                limit=1,
                sort="price",
            )

            result = run_export(request, fetcher=fetcher)

            self.assertEqual(result.selected_records, 1)
            self.assertEqual(result.files[0].count, 1)
            self.assertIn("Expensive", result.files[0].path.read_text(encoding="utf-8"))

    def test_run_export_requires_descriptions_by_default(self):
        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), [
                record("Missing", "text", "gpt", description="")
            ]

        with TemporaryDirectory() as directory:
            request = ExportRequest(profile_input="@acb", output_dir=Path(directory))

            with self.assertRaises(WebInputError):
                run_export(request, fetcher=fetcher)


class ExposureWarningTests(unittest.TestCase):
    def test_no_warning_for_loopback(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with redirect_stderr(io.StringIO()) as err:
                _warn_if_exposed(host)
            self.assertEqual(err.getvalue(), "")

    def test_warns_for_non_loopback(self):
        # "" binds INADDR_ANY (every interface), so it must warn like 0.0.0.0.
        for host in ("0.0.0.0", ""):
            with redirect_stderr(io.StringIO()) as err:
                _warn_if_exposed(host)
            self.assertIn("WARNING", err.getvalue())


class RequestGuardTests(unittest.TestCase):
    def test_allows_same_origin_sec_fetch_site(self):
        handler = _make_handler(
            {"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"}
        )
        self.assertIsNone(handler._reject_unsafe_request())

    def test_allows_none_sec_fetch_site(self):
        handler = _make_handler({"Host": "localhost:8765", "Sec-Fetch-Site": "none"})
        self.assertIsNone(handler._reject_unsafe_request())

    def test_rejects_cross_site_sec_fetch_site(self):
        handler = _make_handler(
            {"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"}
        )
        self.assertIsNotNone(handler._reject_unsafe_request())

    def test_rejects_foreign_origin(self):
        handler = _make_handler(
            {"Host": "127.0.0.1:8765", "Origin": "http://evil.example"}
        )
        self.assertIsNotNone(handler._reject_unsafe_request())

    def test_allows_matching_origin(self):
        handler = _make_handler(
            {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
        )
        self.assertIsNone(handler._reject_unsafe_request())

    def test_rejects_rebound_host_header(self):
        handler = _make_handler({"Host": "attacker.example:8765"})
        self.assertIsNotNone(handler._reject_unsafe_request())

    def test_allows_non_browser_client_without_headers(self):
        handler = _make_handler({"Host": "127.0.0.1:8765"})
        self.assertIsNone(handler._reject_unsafe_request())

    def test_ipv6_loopback_accepts_bracketed_host_and_origin(self):
        handler = _make_handler(
            {"Host": "[::1]:8765", "Origin": "http://[::1]:8765"},
            address=("::1", 8765, 0, 0),
        )
        self.assertIsNone(handler._reject_unsafe_request())

    def test_ipv6_bind_address_is_bracketed_in_authorities(self):
        # Browsers always bracket an IPv6 literal in the Host header.
        handler = _make_handler({}, address=("fe80::1", 8765, 0, 0))
        authorities = handler._expected_authorities()
        self.assertIn("[fe80::1]:8765", authorities)
        self.assertNotIn("fe80::1", authorities)


class ServerBindTests(unittest.TestCase):
    def test_url_host_brackets_ipv6_only(self):
        self.assertEqual(_url_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(_url_host("localhost"), "localhost")
        self.assertEqual(_url_host("::1"), "[::1]")

    def test_ipv6_bind_address_keeps_the_scope_id(self):
        # A (host, port) pair would bind with scope id 0, which the kernel
        # rejects for a link-local address.
        self.assertEqual(_ipv6_bind_address("::1", 8765), ("::1", 8765, 0, 0))
        try:
            address = _ipv6_bind_address("fe80::1%1", 8765)
        except socket.gaierror as exc:
            self.skipTest(f"numeric IPv6 scope ids are not supported: {exc}")
        self.assertEqual(address, ("fe80::1", 8765, 0, 1))

    def test_binds_ipv4_loopback(self):
        with _make_server("127.0.0.1", 0) as server:
            self.assertEqual(server.address_family, socket.AF_INET)

    def test_binds_ipv6_loopback(self):
        # Probe with a raw socket: the bug under test also raises an OSError
        # (gaierror), so it must not be what decides to skip.
        try:
            with socket.socket(socket.AF_INET6) as probe:
                probe.bind(("::1", 0))
        except OSError as exc:
            self.skipTest(f"IPv6 loopback is not available: {exc}")
        with _make_server("::1", 0) as server:
            self.assertEqual(server.address_family, socket.AF_INET6)
            self.assertEqual(server.server_address[0], "::1")


class ReadFormTests(unittest.TestCase):
    def test_rejects_invalid_content_length(self):
        # A negative length would make rfile.read() block until EOF.
        for value in ("-1", "abc"):
            handler = _make_handler({"Content-Length": value})
            handler.rfile = io.BytesIO(b"profile=acb")
            with self.subTest(value=value), self.assertRaises(WebInputError):
                handler._read_form()

    def test_rejects_oversized_form(self):
        handler = _make_handler({"Content-Length": str(MAX_FORM_BYTES + 1)})
        handler.rfile = io.BytesIO(b"")
        with self.assertRaises(WebInputError):
            handler._read_form()

    def test_reads_form_body(self):
        body = b"profile=acb&mode=text"
        handler = _make_handler({"Content-Length": str(len(body))})
        handler.rfile = io.BytesIO(body)
        self.assertEqual(handler._read_form(), {"profile": ["acb"], "mode": ["text"]})


class DownloadTests(unittest.TestCase):
    def _handler(self, path, headers=None):
        handler = _make_handler(headers or {"Host": "127.0.0.1:8765"})
        handler.path = path
        handler._send_text = MagicMock()
        # Record what would be sent: the body is read while the handler still has the file.
        handler.downloads = []
        handler._send_download = MagicMock(
            side_effect=lambda source, size, name, ctype: handler.downloads.append(
                (source.read(), size, name, ctype)
            )
        )
        return handler

    @staticmethod
    def _status(mock):
        return mock.call_args.kwargs.get("status")

    def test_serves_export_file_as_attachment(self):
        with TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                exports = Path("exports")
                exports.mkdir()
                (exports / "acb_all_prompts.json").write_text(
                    '[{"title": "x"}]', encoding="utf-8"
                )
                handler = self._handler("/download?file=exports/acb_all_prompts.json")
                handler._handle_download()
            finally:
                os.chdir(previous)

        handler._send_text.assert_not_called()
        handler._send_download.assert_called_once()
        data, size, filename, content_type = handler.downloads[0]
        self.assertEqual(filename, "acb_all_prompts.json")
        self.assertIn("application/json", content_type)
        self.assertEqual(data, b'[{"title": "x"}]')
        self.assertEqual(size, len(data))

    def test_streams_the_file_without_reading_it_whole(self):
        payload = bytes(range(256)) * 12_288  # 3 MiB, several copy buffers
        with _in_directory():
            Path("exports").mkdir()
            Path("exports/acb_all_prompts.json").write_bytes(payload)
            handler = _make_handler({"Host": "127.0.0.1:8765"})
            handler.path = "/download?file=exports/acb_all_prompts.json"
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()
            handler.wfile = io.BytesIO()
            handler._send_text = MagicMock()
            real_copy = shutil.copyfileobj
            with patch.object(Path, "read_bytes", side_effect=AssertionError("read whole")), \
                    patch("promptbase_exporter.web.shutil.copyfileobj",
                          side_effect=real_copy) as copy:
                handler._handle_download()

        copy.assert_called_once()
        headers = {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}
        self.assertEqual(headers["Content-Length"], str(len(payload)))
        self.assertEqual(handler.wfile.getvalue(), payload)
        handler._send_text.assert_not_called()

    def test_a_file_that_cannot_be_opened_is_a_404(self):
        with _in_directory():
            Path("exports").mkdir()
            Path("exports/acb_all_prompts.json").write_text("[]", encoding="utf-8")
            handler = self._handler("/download?file=exports/acb_all_prompts.json")
            with patch.object(Path, "open", side_effect=PermissionError(13, "denied")):
                handler._handle_download()
        handler._send_download.assert_not_called()
        self.assertEqual(self._status(handler._send_text), 404)

    def test_rejects_path_traversal(self):
        handler = self._handler("/download?file=../../etc/passwd")
        handler._handle_download()
        handler._send_download.assert_not_called()
        self.assertEqual(self._status(handler._send_text), 404)

    def test_rejects_non_export_files(self):
        # A supported-extension file that the tool did not generate (e.g. a
        # stray secrets.json) and an unsupported file must both be refused, so
        # the endpoint only ever serves real exports.
        for name, contents in (
            ("secrets.json", '{"token": "abc"}'),
            ("secret.env", "token"),
            ("notes.txt", "private"),
        ):
            with TemporaryDirectory() as directory:
                previous = os.getcwd()
                os.chdir(directory)
                try:
                    Path(name).write_text(contents, encoding="utf-8")
                    handler = self._handler(f"/download?file={name}")
                    handler._handle_download()
                finally:
                    os.chdir(previous)

            handler._send_download.assert_not_called()
            self.assertEqual(self._status(handler._send_text), 404)

    def test_rejects_unc_path_without_resolving_it(self):
        # On Windows, resolving //host/share/... opens an SMB connection to
        # that host, so an escaping path must be refused before resolve().
        real_resolve = Path.resolve
        resolved = []

        def tracking_resolve(path, strict=False):
            resolved.append(str(path))
            return real_resolve(path, strict)

        handler = self._handler(
            "/download?file=//attacker.example/share/acb_all_prompts.txt"
        )
        with patch.object(Path, "resolve", tracking_resolve):
            handler._handle_download()

        handler._send_download.assert_not_called()
        self.assertEqual(self._status(handler._send_text), 404)
        self.assertFalse(any("attacker.example" in path for path in resolved))

    def test_serves_timestamped_export_file(self):
        with TemporaryDirectory() as directory:
            previous = os.getcwd()
            os.chdir(directory)
            try:
                name = "acb_text_prompts_20260101_120000.csv"
                Path(name).write_text("title\nx\n", encoding="utf-8")
                handler = self._handler(f"/download?file={name}")
                handler._handle_download()
            finally:
                os.chdir(previous)

        handler._send_text.assert_not_called()
        handler._send_download.assert_called_once()

    def test_requires_file_parameter(self):
        handler = self._handler("/download")
        handler._handle_download()
        handler._send_download.assert_not_called()
        self.assertEqual(self._status(handler._send_text), 400)

    def test_rejects_unrecognized_host(self):
        handler = self._handler(
            "/download?file=exports/acb_all_prompts.json",
            headers={"Host": "attacker.example:8765"},
        )
        handler._handle_download()
        handler._send_download.assert_not_called()
        self.assertEqual(self._status(handler._send_text), 403)

    def test_serves_html_export(self):
        with _in_directory():
            Path("exports").mkdir()
            Path("exports/acb_all_prompts.html").write_text("<!doctype html>", encoding="utf-8")
            handler = self._handler("/download?file=exports/acb_all_prompts.html")
            handler._handle_download()

        _, _, filename, content_type = handler.downloads[0]
        self.assertEqual(filename, "acb_all_prompts.html")
        self.assertEqual(content_type, "text/html; charset=utf-8")


class _in_directory:
    """Run a block with a fresh temporary directory as the working directory."""

    def __enter__(self):
        self._tmp = TemporaryDirectory()
        self._previous = os.getcwd()
        os.chdir(self._tmp.name)
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        os.chdir(self._previous)
        self._tmp.cleanup()


class DownloadHeaderTests(unittest.TestCase):
    def test_download_is_attachment_with_sandbox_csp(self):
        handler = _make_handler({"Host": "127.0.0.1:8765"})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler._send_download(
            io.BytesIO(b"<p>x</p>"), 8, "acb_all_prompts.html", "text/html; charset=utf-8"
        )

        headers = {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}
        self.assertEqual(headers["Content-Security-Policy"], "sandbox")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Content-Length"], "8")
        self.assertEqual(handler.wfile.getvalue(), b"<p>x</p>")

    def test_content_length_is_the_size_the_caller_gives(self):
        # The header is the length the caller promised, so it can never disagree with
        # the bytes that are sent, whatever the source holds.
        handler = _make_handler({"Host": "127.0.0.1:8765"})
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()

        handler._send_download(io.BytesIO(b"0123456789"), 4, "acb_all_prompts.txt", "text/plain")

        headers = {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}
        self.assertEqual(headers["Content-Length"], "4")
        self.assertEqual(handler.wfile.getvalue(), b"0123456789")


class NewWebOptionTests(unittest.TestCase):
    def test_parses_min_sales_min_rating_and_csv_safe(self):
        request = build_request_config(
            {
                "profile": "acb",
                "format": "csv",
                "min_sales": "0",
                "min_rating": "4.5",
                "csv_safe": "1",
            }
        )
        self.assertEqual(request.min_sales, 0)
        self.assertEqual(request.min_rating, 4.5)
        self.assertTrue(request.csv_safe)

    def test_rejects_invalid_min_sales_and_rating(self):
        for field, value in (
            ("min_sales", "-1"),
            ("min_sales", "1.5"),
            ("min_rating", "-1"),
            ("min_rating", "nan"),
            ("min_rating", "abc"),
        ):
            with self.assertRaises(WebInputError, msg=f"{field}={value}"):
                build_request_config({"profile": "acb", field: value})

    def test_limit_still_rejects_zero(self):
        with self.assertRaisesRegex(WebInputError, "greater than zero"):
            build_request_config({"profile": "acb", "limit": "0"})

    def test_csv_safe_requires_csv_format(self):
        with self.assertRaisesRegex(WebInputError, "csv format"):
            build_request_config({"profile": "acb", "format": "json", "csv_safe": "1"})

    def test_render_form_contains_new_controls(self):
        html = render_form()
        for name in ("min_sales", "min_rating", "compare_file", "csv_safe"):
            self.assertIn(f'name="{name}"', html)
        self.assertIn('<option value="html">html</option>', html)


class CompareFileTests(unittest.TestCase):
    def test_accepts_catalog_inside_working_directory(self):
        with _in_directory() as root:
            Path("catalog.json").write_text("[]", encoding="utf-8")
            request = build_request_config(
                {"profile": "acb", "mode": "all", "compare_file": "catalog.json"}
            )
            resolved_root = root.resolve()

        self.assertEqual(request.compare_path, resolved_root / "catalog.json")
        self.assertEqual(request.compare_file, "catalog.json")

    def test_rejects_invalid_compare_files(self):
        cases = [
            ({"mode": "split", "compare_file": "catalog.json"}, "mode all, text, or image"),
            ({"mode": "all", "compare_file": "../catalog.json"}, "working directory"),
            ({"mode": "all", "compare_file": "//attacker.example/share/x.json"},
             "working directory"),
            ({"mode": "all", "compare_file": "notes.yaml"}, "JSON, CSV, TXT"),
            ({"mode": "all", "compare_file": "missing.json"}, "not found"),
        ]
        with _in_directory():
            Path("notes.yaml").write_text("x", encoding="utf-8")
            for form, message in cases:
                with self.assertRaisesRegex(WebInputError, message, msg=str(form)):
                    build_request_config({"profile": "acb", **form})

    def test_an_exposed_server_compares_only_with_exported_catalogs(self):
        with _in_directory():
            Path("secrets.json").write_text('[{"title": "s"}]', encoding="utf-8")
            Path("acb_all_prompts.json").write_text("[]", encoding="utf-8")
            form = {"profile": "acb", "mode": "all", "compare_file": "secrets.json"}
            self.assertEqual(build_request_config(form).compare_path.name, "secrets.json")
            with self.assertRaisesRegex(WebInputError, "file this tool exported"):
                build_request_config(form, exposed=True)
            # Refused before the existence check, so it cannot probe for files either.
            with self.assertRaisesRegex(WebInputError, "file this tool exported"):
                build_request_config({**form, "compare_file": "missing.json"}, exposed=True)
            allowed = build_request_config(
                {**form, "compare_file": "acb_all_prompts.json"}, exposed=True
            )
        self.assertEqual(allowed.compare_path.name, "acb_all_prompts.json")

    def test_the_handler_knows_when_it_is_exposed(self):
        for host, exposed in (("127.0.0.1", False), ("::1", False), ("127.0.0.2", False),
                              ("localhost", False), ("0.0.0.0", True), ("::", True),
                              ("192.168.1.5", True)):
            with self.subTest(host=host):
                handler = _make_handler({}, address=(host, 8765))
                self.assertEqual(handler._exposed(), exposed)

    def test_the_export_name_pattern_matches_the_whole_name(self):
        self.assertTrue(_EXPORT_FILENAME_RE.fullmatch("acb_all_prompts.json"))
        self.assertFalse(_EXPORT_FILENAME_RE.fullmatch("acb_all_prompts.json" + chr(10)))

    def test_run_export_compares_before_overwriting_the_same_file(self):
        records = [record("Fresh", "text", "gpt", created=2, price=2.0)]

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), records

        with _in_directory() as root:
            exports = root / "exports"
            exports.mkdir()
            catalog = exports / "acb_all_prompts.json"
            catalog.write_text(
                '[{"title": "Stale", "slug": "stale", "description": "old"}]', encoding="utf-8"
            )
            request = build_request_config(
                {
                    "profile": "acb",
                    "mode": "all",
                    "format": "json",
                    "compare_file": "exports/acb_all_prompts.json",
                }
            )

            result = run_export(request, fetcher=fetcher)
            written = catalog.read_text(encoding="utf-8")

        self.assertIsNotNone(result.diff)
        self.assertEqual([item["slug"] for item in result.diff.removed], ["stale"])
        self.assertEqual([item["slug"] for item in result.diff.added], ["fresh"])
        self.assertIn('"slug": "fresh"', written)
        page = render_form(request, result=result)
        self.assertIn("<h3>Comparison</h3>", page)
        self.assertIn("- Removed: 1", page)

    def test_comparison_catalog_decoding_is_independent_of_output_protection(self):
        records = [record("=Formula", "text", "gpt", created=2, price=2.0)]

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), records

        with _in_directory() as root:
            write_export(root, "safe", "all", records, "csv", csv_safe=True)
            write_export(root, "plain", "all", records, "csv")
            form = {"profile": "acb", "mode": "all", "format": "csv", "output_dir": "out"}
            # Protected catalog declared, plain output.
            declared = run_export(
                build_request_config(
                    {**form, "compare_file": "safe_all_prompts.csv", "compare_csv_safe": "1"}
                ),
                fetcher=fetcher,
            )
            # Protected output, plain catalog: the catalog is read verbatim.
            protected_output = run_export(
                build_request_config(
                    {**form, "compare_file": "plain_all_prompts.csv", "csv_safe": "1"}
                ),
                fetcher=fetcher,
            )
            # Protected catalog not declared: read verbatim, so it differs.
            undeclared = run_export(
                build_request_config({**form, "compare_file": "safe_all_prompts.csv"}),
                fetcher=fetcher,
            )

        self.assertFalse(declared.diff.has_changes)
        self.assertFalse(protected_output.diff.has_changes)
        self.assertTrue(undeclared.diff.has_changes)

    def test_compare_csv_safe_needs_a_csv_catalog(self):
        with _in_directory():
            Path("old.json").write_text("[]", encoding="utf-8")
            for form in (
                {"compare_csv_safe": "1"},
                {"compare_csv_safe": "1", "compare_file": "old.json"},
            ):
                with self.assertRaisesRegex(WebInputError, "CSV comparison catalog"):
                    build_request_config({"profile": "acb", "mode": "all", **form})

    def test_unreadable_compare_catalog_is_a_400_error(self):
        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), [record("A", "text", "gpt")]

        with _in_directory() as root:
            (root / "broken.json").write_text("{not json", encoding="utf-8")
            request = build_request_config(
                {"profile": "acb", "mode": "all", "compare_file": "broken.json"}
            )
            with self.assertRaisesRegex(WebInputError, "Could not load comparison catalog"):
                run_export(request, fetcher=fetcher)
            self.assertFalse((root / "exports").exists())


    def test_export_errors_do_not_show_the_server_path(self):
        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), [record("A", "text", "gpt")]

        with _in_directory() as root:
            (root / "old.json").write_text("[]", encoding="utf-8")
            request = build_request_config(
                {"profile": "acb", "mode": "all", "compare_file": "old.json"}
            )
            denied = PermissionError(13, "Permission denied", str(root / "old.json"))
            with patch("promptbase_exporter.web.load_catalog", side_effect=denied):
                with self.assertRaises(WebInputError) as caught:
                    run_export(request, fetcher=fetcher)
            self.assertEqual(
                str(caught.exception),
                "Could not load comparison catalog: Permission denied (old.json).",
            )
            request = build_request_config({"profile": "acb", "mode": "all"})
            with self.assertRaises(WebInputError) as caught:
                run_export(request, fetcher=fetcher, counter=lambda _p, _f: 0)
            self.assertNotIn(str(root), str(caught.exception))
            self.assertIn("Validation failed for exports/acb_all_prompts.", str(caught.exception))

class ExportLockTests(unittest.TestCase):
    def test_writes_happen_while_holding_the_export_lock(self):
        observed = []

        def writer(output_dir, username, mode, records, export_format, timestamp=None,
                   overwrite=True, *, csv_safe=False, extra_fields=(), item_type="prompt"):
            observed.append((_EXPORT_LOCK.locked(), csv_safe))
            path = output_dir / f"{username}_{mode}_prompts.txt"
            path.write_text("", encoding="utf-8")
            return path

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            self.assertFalse(_EXPORT_LOCK.locked())
            return Profile(username="acb", uid="uid-1"), [record("A", "text", "gpt")]

        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="@acb", output_dir=Path(directory), mode="all", csv_safe=True
            )
            run_export(request, fetcher=fetcher, writer=writer, counter=lambda _p, _f: 1)

        self.assertEqual(observed, [(True, True)])
        self.assertFalse(_EXPORT_LOCK.locked())

    def test_lock_is_released_when_validation_fails(self):
        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            return Profile(username="acb", uid="uid-1"), [record("A", "text", "gpt")]

        with TemporaryDirectory() as directory:
            request = ExportRequest(profile_input="@acb", output_dir=Path(directory), mode="all")
            with self.assertRaisesRegex(WebInputError, "Validation failed"):
                run_export(request, fetcher=fetcher, counter=lambda _p, _f: 0)

        self.assertFalse(_EXPORT_LOCK.locked())


class ExtraFieldsWebTests(unittest.TestCase):
    def test_checkbox_values_become_canonical_extra_fields(self):
        request = build_request_config(
            {"profile": "acb", "format": "json", "extra_fields": ["unique_sales", "tags"]}
        )
        self.assertEqual(request.extra_fields, ("tags", "unique_sales"))
        self.assertEqual(build_request_config({"profile": "acb"}).extra_fields, ())

    def test_rejects_unknown_fields_and_the_txt_format(self):
        with self.assertRaisesRegex(WebInputError, "unknown extra field"):
            build_request_config(
                {"profile": "acb", "format": "json", "extra_fields": ["tags", "bogus"]}
            )
        with self.assertRaisesRegex(WebInputError, "markdown, json, ndjson, csv, or html"):
            build_request_config({"profile": "acb", "extra_fields": ["tags"]})

    def test_form_renders_one_checkbox_per_field_and_keeps_the_selection(self):
        html = render_form(ExportRequest(profile_input="acb", extra_fields=("engine",)))
        for name in EXTRA_FIELDS:
            self.assertIn(f'name="extra_fields" value="{name}"', html)
        self.assertRegex(html, r'value="engine"\s+checked>')
        self.assertNotRegex(html, r'value="tags"\s+checked>')

    def test_run_export_passes_the_fields_to_the_fetcher_and_writer(self):
        seen = {}

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            seen["fetch"] = tuple(extra_fields)
            return Profile("acb", "u"), [record("A", "text", "gpt")]

        def writer(output_dir, username, mode, records, export_format, timestamp=None,
                   overwrite=True, *, csv_safe=False, extra_fields=(), item_type="prompt"):
            seen["write"] = tuple(extra_fields)
            path = output_dir / f"{username}_{mode}_prompts.json"
            path.write_text("[{}]", encoding="utf-8")
            return path

        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory),
                export_format="json", extra_fields=("tags",),
            )
            run_export(request, fetcher=fetcher, writer=writer, counter=lambda _p, _f: 1)
        self.assertEqual(seen, {"fetch": ("tags",), "write": ("tags",)})


class ItemTypeWebTests(unittest.TestCase):
    def test_item_type_defaults_to_prompt_and_is_validated(self):
        self.assertEqual(build_request_config({"profile": "acb"}).item_type, "prompt")
        request = build_request_config({"profile": "acb", "item_type": "bundle"})
        self.assertEqual(request.item_type, "bundle")
        with self.assertRaisesRegex(WebInputError, "Unsupported item type: skill"):
            build_request_config({"profile": "acb", "item_type": "skill"})

    def test_form_offers_every_kind_and_keeps_the_selection(self):
        html = render_form(ExportRequest(profile_input="acb", item_type="app"))
        for kind in ITEM_TYPES:
            self.assertIn(f'<option value="{kind}"', html)
        self.assertIn('<option value="app" selected>app</option>', html)

    def test_run_export_fetches_and_writes_the_chosen_kind(self):
        seen = {}

        def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
            seen["fetch"] = item_type
            bundle = PromptRecord("Kit", "d", "kit", "gpt", "text", 1, 4.0, item_type=item_type)
            return Profile("acb", "u"), [bundle]

        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory),
                export_format="json", item_type="bundle",
            )
            result = run_export(request, fetcher=fetcher)
            names = [item.path.name for item in result.files]
        self.assertEqual(seen["fetch"], "bundle")
        self.assertEqual(names, ["acb_all_bundles.json"])
        self.assertEqual(result.files[0].count, 1)

    def test_empty_result_names_the_kind(self):
        request = ExportRequest(profile_input="acb", item_type="app")
        with self.assertRaisesRegex(WebInputError, "No approved apps found"):
            run_export(request, fetcher=lambda *_a, **_k: (Profile("acb", "u"), []))

    def test_download_allowlist_covers_all_kinds_and_nothing_else(self):
        for kind in ("prompts", "bundles", "apps"):
            for name in (f"acb_all_{kind}.json", f"acb_text_{kind}_20260101_120000.csv"):
                self.assertTrue(_EXPORT_FILENAME_RE.match(name), name)
        for name in ("acb_all_skills.json", "acb_all_prompt.json", "secrets.json",
                     "acb_all_apps.exe", "acb_other_apps.json"):
            self.assertFalse(_EXPORT_FILENAME_RE.match(name), name)


class ClientDisconnectTests(unittest.TestCase):
    BODY = b"profile=acb&mode=all"
    RESULT = WebExportResult(
        username="acb", total_records=1, selected_records=1, text_records=1,
        image_records=0, other_records=0, files=(),
    )

    def _post(self, export, send_error):
        """Run do_POST with run_export patched; return the handler after the request."""
        handler = _make_handler({"Host": "127.0.0.1:8765", "Content-Length": str(len(self.BODY))})
        handler.path = "/export"
        handler.rfile = io.BytesIO(self.BODY)
        handler._send_html = MagicMock(side_effect=send_error)
        with patch("promptbase_exporter.web.run_export", **export):
            handler.do_POST()
        return handler

    def test_a_disconnect_while_sending_the_result_is_not_sent_again(self):
        for error in (BrokenPipeError(32, "Broken pipe"),
                      ConnectionResetError(104, "Connection reset"),
                      ConnectionAbortedError(103, "Connection aborted")):
            with self.subTest(error=type(error).__name__):
                handler = self._post({"return_value": self.RESULT}, send_error=error)
                self.assertEqual(handler._send_html.call_count, 1)
                self.assertTrue(handler.close_connection)

    def test_a_disconnect_while_sending_an_error_page_is_not_sent_again(self):
        handler = self._post(
            {"side_effect": WebInputError("No approved prompts found.")},
            send_error=BrokenPipeError(32, "Broken pipe"),
        )
        # Once, and as the 400 it was: not re-entered as a 500 "Could not write export".
        self.assertEqual(handler._send_html.call_count, 1)
        self.assertEqual(handler._send_html.call_args.kwargs["status"], 400)

    def test_other_send_failures_still_propagate(self):
        handler = _make_handler({"Host": "127.0.0.1:8765", "Content-Length": "0"})
        handler.path = "/export"
        handler.rfile = io.BytesIO(b"")
        handler._send_html = MagicMock(side_effect=OSError(28, "No space left on device"))
        with patch("promptbase_exporter.web.run_export", return_value=self.RESULT):
            with self.assertRaises(OSError):
                handler.do_POST()
        self.assertEqual(handler._send_html.call_count, 1)


def _guard(bind, headers):
    """The request guard's verdict (None when allowed) for a server bound to ``bind``."""
    address = (bind, 8765, 0, 0) if ":" in bind else (bind, 8765)
    return _make_handler(headers, address=address)._reject_unsafe_request()


class WildcardHostTests(unittest.TestCase):
    def test_wildcard_bind_accepts_ip_literals_and_loopback_names(self):
        hosts = ("192.168.1.5:8765", "192.168.1.5", "10.0.0.2:8765", "[fd00::5]:8765",
                 "127.0.0.1:8765", "localhost:8765", "[::1]:8765")
        for bind in ("0.0.0.0", "::", ""):
            for host in hosts:
                with self.subTest(bind=bind, host=host):
                    self.assertIsNone(_guard(bind, {"Host": host}))

    def test_wildcard_bind_still_refuses_dns_names_and_other_ports(self):
        hosts = ("attacker.example:8765", "lan-box.local:8765", "192.168.1.5:9999",
                 "[fd00::5]:9999", "fd00::5:8765", "192.168.1.5:")
        for bind in ("0.0.0.0", "::", ""):
            for host in hosts:
                with self.subTest(bind=bind, host=host):
                    self.assertIsNotNone(_guard(bind, {"Host": host}))

    def test_wildcard_origin_must_match_scheme_host_and_port(self):
        host = {"Host": "192.168.1.5:8765"}
        self.assertIsNone(_guard("0.0.0.0", {**host, "Origin": "http://192.168.1.5:8765"}))
        for origin in ("http://attacker.example:8765", "http://192.168.1.5:9999",
                       "ftp://192.168.1.5:8765", "null",
                       "http://203.0.113.5:8765"):  # another IP literal is another site
            with self.subTest(origin=origin):
                self.assertIsNotNone(_guard("0.0.0.0", {**host, "Origin": origin}))

    def test_a_specific_lan_bind_answers_only_to_its_own_address(self):
        self.assertIsNone(_guard("192.168.1.5", {"Host": "192.168.1.5:8765"}))
        for host in ("10.0.0.9:8765", "localhost:8765", "127.0.0.1:8765", "0.0.0.0:8765"):
            with self.subTest(host=host):
                self.assertIsNotNone(_guard("192.168.1.5", {"Host": host}))

    def test_a_loopback_bind_refuses_lan_and_wildcard_addresses(self):
        for bind in ("127.0.0.1", "::1"):
            for host in ("192.168.1.5:8765", "0.0.0.0:8765", "[fd00::5]:8765", "[::]:8765"):
                with self.subTest(bind=bind, host=host):
                    self.assertIsNotNone(_guard(bind, {"Host": host}))
            for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
                with self.subTest(bind=bind, host=host):
                    self.assertIsNone(_guard(bind, {"Host": host}))


class LiveHostHeaderTests(unittest.TestCase):
    @staticmethod
    def _download_status(bind, host):
        """Status of a real request for /download (no file, so 400 means the host passed)."""
        with _make_server(bind, 0) as server:
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/download", headers={"Host": f"{host}:{port}"})
                status = conn.getresponse().status
                conn.close()
            finally:
                server.shutdown()
                thread.join(5)
        return status

    def test_a_wildcard_server_answers_a_lan_address_but_not_a_name(self):
        self.assertEqual(self._download_status("0.0.0.0", "192.168.1.5"), 400)
        self.assertEqual(self._download_status("0.0.0.0", "attacker.example"), 403)

    def test_a_loopback_server_refuses_a_lan_address(self):
        self.assertEqual(self._download_status("127.0.0.1", "192.168.1.5"), 403)
        self.assertEqual(self._download_status("127.0.0.1", "localhost"), 400)


class ConnectionCapTests(unittest.TestCase):
    @staticmethod
    def _drain(sock):
        chunks = []
        while chunk := sock.recv(4096):
            chunks.append(chunk)
        return b"".join(chunks)

    def test_connections_beyond_the_cap_are_closed_without_a_response(self):
        with _make_server("127.0.0.1", 0) as server:
            port = server.server_address[1]
            for _ in range(MAX_HANDLER_THREADS):
                self.assertTrue(server._slots.acquire(blocking=False))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                    server.handle_request()
                    self.assertEqual(client.recv(1), b"")
            finally:
                for _ in range(MAX_HANDLER_THREADS):
                    server._slots.release()

    def test_a_served_connection_gives_its_slot_back(self):
        with _make_server("127.0.0.1", 0) as server:
            port = server.server_address[1]
            with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                client.sendall(b"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
                server.handle_request()
                reply = self._drain(client)
            self.assertTrue(reply.endswith(b"\r\n\r\nok\n"), reply)
            # Every slot is free again: the handler thread released its own.
            for _ in range(MAX_HANDLER_THREADS):
                self.assertTrue(server._slots.acquire(timeout=5))


if __name__ == "__main__":
    unittest.main()

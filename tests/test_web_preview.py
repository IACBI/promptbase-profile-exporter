import http.client
import io
import re
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from promptbase_exporter import web
from promptbase_exporter.models import Profile, PromptRecord
from promptbase_exporter.web import (
    PREVIEW_ROWS,
    ExportRequest,
    WebInputError,
    browser_url,
    build_request_config,
    render_form,
    run_export,
    run_preview,
)
from tests.scratch import use_scratch_working_directory


def setUpModule():
    use_scratch_working_directory()


def record(slug, *, created, title=None, description="d", price=1.0, domain="text", sales=0):
    return PromptRecord(
        title=title or slug.title(), description=description, slug=slug, prompt_type="gpt",
        domain=domain, created=created, price=price, sales=sales, views=3,
    )


def fetcher_for(records):
    def fetcher(_profile_input, extra_fields=(), item_type="prompt"):
        return Profile("acb", "u"), list(records)

    return fetcher


RECORDS = [
    record("newest", created=1_767_225_600_000, domain="image", price=5.0, sales=2),
    record("middle", created=1_700_000_000_000, sales=9),
    record("oldest", created=1_600_000_000_000, description="", price=0.0),
]


class RunPreviewTests(unittest.TestCase):
    def test_lists_the_selection_in_request_order_with_counts(self):
        preview = run_preview(
            ExportRequest(profile_input="acb", sort="sales"), fetcher=fetcher_for(RECORDS)
        )
        self.assertEqual([row.title for row in preview.rows], ["Middle", "Newest", "Oldest"])
        self.assertEqual(
            (preview.total_records, preview.selected_records, preview.text_records,
             preview.image_records, preview.other_records, preview.without_description),
            (3, 3, 2, 1, 0, 1),
        )
        first = preview.rows[0]
        self.assertEqual((first.sales, first.views, first.created), (9, 3, "2023-11-14"))
        self.assertEqual(first.url, "https://promptbase.com/prompt/middle")

    def test_filters_apply_like_an_export(self):
        preview = run_preview(
            ExportRequest(profile_input="acb", price_filter="paid", limit=1),
            fetcher=fetcher_for(RECORDS),
        )
        self.assertEqual([row.title for row in preview.rows], ["Newest"])
        self.assertEqual((preview.total_records, preview.selected_records), (3, 1))

    def test_only_the_first_rows_are_listed_but_the_counts_cover_everything(self):
        many = [
            record(f"p{i:03d}", created=2_000_000_000_000 - i) for i in range(PREVIEW_ROWS + 25)
        ]
        preview = run_preview(ExportRequest(profile_input="acb"), fetcher=fetcher_for(many))
        self.assertEqual(len(preview.rows), PREVIEW_ROWS)
        self.assertEqual(preview.selected_records, PREVIEW_ROWS + 25)

    def test_the_same_errors_as_an_export(self):
        cases = [
            (ExportRequest(profile_input="acb"), [], "No approved prompts found for @acb"),
            (ExportRequest(profile_input="acb", domain="video"), RECORDS,
             "No prompts matched the selected filters"),
            (ExportRequest(profile_input="acb"), [RECORDS[2], RECORDS[0]], "not sorted newest"),
        ]
        for request, records, message in cases:
            with self.subTest(message):
                with self.assertRaisesRegex(WebInputError, message):
                    run_preview(request, fetcher=fetcher_for(records))

    def test_the_kind_is_named(self):
        with self.assertRaisesRegex(WebInputError, "No approved bundles found"):
            run_preview(ExportRequest(profile_input="acb", item_type="bundle"),
                        fetcher=fetcher_for([]))

    def test_nothing_is_written(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(profile_input="acb", output_dir=Path(directory) / "out")
            run_preview(request, fetcher=fetcher_for(RECORDS))
            self.assertFalse((Path(directory) / "out").exists())

    def test_a_preview_and_an_export_select_the_same_records(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory), export_format="json",
                sort="oldest", limit=2, allow_missing_descriptions=True,
            )
            preview = run_preview(request, fetcher=fetcher_for(RECORDS))
            result = run_export(request, fetcher=fetcher_for(RECORDS))
        self.assertEqual(preview.selected_records, result.selected_records)
        self.assertEqual(preview.total_records, result.total_records)


class RenderPreviewTests(unittest.TestCase):
    def render(self, records=RECORDS, **request):
        options = {"profile_input": "acb", **request}
        preview = run_preview(ExportRequest(**options), fetcher=fetcher_for(records))
        # The template breaks lines inside sentences; compare as a reader sees them.
        return " ".join(render_form(ExportRequest(**options), preview=preview).split())

    def test_the_page_shows_a_table_and_says_nothing_was_written(self):
        page = self.render()
        self.assertIn("<h2>Preview</h2>", page)
        self.assertIn("3 selected from 3 prompts", page)
        self.assertIn("Nothing has been written.", page)
        self.assertIn('<a href="https://promptbase.com/prompt/newest"', page)
        self.assertIn('rel="noopener noreferrer"', page)
        self.assertEqual(page.count("<tr><td><a href="), 3)

    def test_wide_tables_scroll_inside_their_own_box(self):
        # At phone width a 7-column table would otherwise widen the whole page.
        page = self.render()
        self.assertIn('<div class="tablewrap"><table>', page)
        self.assertIn("</table></div>", page)
        self.assertIn(".tablewrap {", render_form())

    def test_both_buttons_are_offered_with_the_safe_one_first(self):
        page = render_form()
        preview, export = page.find('value="preview"'), page.find('value="export"')
        self.assertTrue(0 < preview < export)  # Enter in a text field previews, never writes

    def test_a_missing_description_is_flagged(self):
        self.assertIn("1 of these have no description", self.render())
        self.assertNotIn("have no description", self.render(RECORDS[:2]))

    def test_the_row_limit_is_announced(self):
        many = [
            record(f"p{i:03d}", created=2_000_000_000_000 - i) for i in range(PREVIEW_ROWS + 3)
        ]
        page = self.render(many)
        self.assertIn(f"Showing the first {PREVIEW_ROWS} of {PREVIEW_ROWS + 3}.", page)
        self.assertNotIn("Showing the first", self.render())

    def test_hostile_text_is_escaped(self):
        hostile = [record("x", created=1, title='<script>alert(1)</script> & "q"\nnext')]
        page = self.render(hostile)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; next", page)

    def test_a_bundle_preview_names_its_kind(self):
        page = self.render(item_type="bundle")
        self.assertIn("3 selected from 3 bundles", page)

    def test_the_export_table_names_the_kind_too(self):
        with TemporaryDirectory() as directory:
            request = ExportRequest(
                profile_input="acb", mode="all", output_dir=Path(directory), export_format="json",
                item_type="app", allow_missing_descriptions=True,
            )
            result = run_export(request, fetcher=fetcher_for(RECORDS))
            page = " ".join(render_form(request, result=result).split())
        self.assertIn("3 apps. Text: 2", page)
        self.assertIn("<th>Apps</th>", page)


class PreviewOverHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = web._make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.port}"
        original = web.run_preview
        patcher = patch.object(
            web, "run_preview", lambda request: original(request, fetcher=fetcher_for(RECORDS))
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, fields, headers=None):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        request = urllib.request.Request(self.base + "/export", data=data, headers=headers or {
            "Origin": self.base, "Sec-Fetch-Site": "same-origin"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read().decode()

    def test_the_preview_button_previews_and_writes_nothing(self):
        # The web form accepts only output directories inside the working directory,
        # which the module runs from an empty scratch folder.
        status, body = self.post({"profile": "acb", "action": "preview", "output_dir": "x"})
        wrote = any(Path.cwd().iterdir())
        self.assertEqual(status, 200)
        self.assertIn("<h2>Preview</h2>", body)
        self.assertIn("Nothing has been written.", body)
        self.assertFalse(wrote)

    def test_a_preview_error_is_a_400(self):
        status, body = self.post({"profile": "acb", "action": "preview", "domain": "video"})
        self.assertEqual(status, 400)
        self.assertIn("No prompts matched the selected filters", body)

    def test_a_cross_site_preview_is_refused_like_an_export(self):
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://evil.example"}):
            status, body = self.post({"profile": "acb", "action": "preview"}, headers)
            self.assertEqual(status, 403)
            self.assertNotIn("Preview", body)

    def test_the_action_field_does_not_change_how_the_form_is_read(self):
        request = build_request_config({"profile": "acb", "action": "preview", "sort": "views"})
        self.assertEqual((request.profile_input, request.sort), ("acb", "views"))
        self.assertTrue(re.search(r'name="action" value="export"', render_form()))


class ServerErrorTests(PreviewOverHttpTests):
    """Responses the http.server module builds itself, and failed writes."""

    SECURITY_HEADERS = ("Content-Security-Policy", "X-Frame-Options",
                        "X-Content-Type-Options", "Referrer-Policy")

    def raw(self, method):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request(method, "/")
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_an_unsupported_method_carries_the_security_headers(self):
        for method in ("PUT", "DELETE", "OPTIONS", "PATCH"):
            with self.subTest(method=method):
                status, headers, body = self.raw(method)
                self.assertEqual(status, 501)
                for name in self.SECURITY_HEADERS:
                    self.assertIn(name, headers)
                self.assertTrue(body.startswith(b"501 "))

    def test_a_head_request_gets_headers_and_no_body(self):
        status, headers, body = self.raw("HEAD")
        self.assertEqual(status, 501)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(body, b"")

    def test_a_failed_write_does_not_show_the_server_path(self):
        error = PermissionError(13, "Permission denied", str(Path.cwd() / "exports" / "x.txt"))
        with patch.object(web, "run_export", side_effect=error):
            status, body = self.post({"profile": "acb", "mode": "all"})
        self.assertEqual(status, 500)
        self.assertIn("Permission denied (exports/x.txt).", body)
        self.assertNotIn(str(Path.cwd()), body)
        self.assertNotIn(Path.cwd().as_posix(), body)


class OpenBrowserTests(unittest.TestCase):
    def test_browser_url(self):
        self.assertEqual(browser_url("127.0.0.1", 8765), "http://127.0.0.1:8765/")
        self.assertEqual(browser_url("localhost", 1), "http://localhost:1/")
        self.assertEqual(browser_url("::1", 8765), "http://[::1]:8765/")
        for wildcard in ("", "0.0.0.0"):  # reached through loopback
            self.assertEqual(browser_url(wildcard, 80), "http://127.0.0.1:80/")
        self.assertEqual(browser_url("::", 80), "http://[::1]:80/")

    def fake_server(self, port=9001):
        server = MagicMock()
        server.__enter__.return_value = server
        server.__exit__.return_value = False
        server.server_address = ("127.0.0.1", port)
        return server

    class SyncThread:
        """A stand-in that runs its target at once, so a test can see the result."""

        def __init__(self, target, args=(), **_kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    def test_open_is_off_by_default(self):
        with patch.object(web, "_make_server", return_value=self.fake_server()), \
                patch.object(web.webbrowser, "open") as opened:
            web.serve("127.0.0.1", 9001)
        opened.assert_not_called()

    def test_a_blocking_browser_cannot_stall_the_server(self):
        # A terminal browser set in $BROWSER returns only when it exits, and it
        # cannot finish until the server answers: open() must not hold serve() up.
        served, released, threads = threading.Event(), threading.Event(), []
        real_thread = threading.Thread

        def record_thread(*args, **kwargs):
            threads.append(kwargs)
            return real_thread(*args, **kwargs)

        server = self.fake_server()
        server.serve_forever.side_effect = served.set

        def blocking_open(_url):
            served.wait(5)
            released.set()

        with patch.object(web, "_make_server", return_value=server), \
                patch.object(web.webbrowser, "open", side_effect=blocking_open), \
                patch.object(web.threading, "Thread", record_thread):
            web.serve("127.0.0.1", 9001, open_browser=True)
            self.assertTrue(released.wait(5))
        self.assertTrue(served.is_set())
        self.assertIs(threads[0]["daemon"], True)

    def test_the_port_the_system_assigned_is_printed_and_opened(self):
        urls = []
        with patch.object(web, "_make_server", return_value=self.fake_server(port=41234)), \
                patch.object(web.webbrowser, "open", side_effect=urls.append), \
                patch.object(web.threading, "Thread", self.SyncThread), \
                redirect_stdout(io.StringIO()) as out:
            web.serve("127.0.0.1", 0, open_browser=True)
        self.assertEqual(urls, ["http://127.0.0.1:41234/"])
        self.assertIn("http://127.0.0.1:41234/", out.getvalue())
        self.assertNotIn(":0/", out.getvalue())

    def test_the_flag_reaches_serve(self):
        with patch.object(web, "serve") as serve:
            self.assertEqual(web.main(["--open", "--port", "9002"]), 0)
            self.assertEqual(web.main([]), 0)
        self.assertEqual(serve.call_args_list[0].args, ("127.0.0.1", 9002))
        self.assertIs(serve.call_args_list[0].kwargs["open_browser"], True)
        self.assertIs(serve.call_args_list[1].kwargs["open_browser"], False)


if __name__ == "__main__":
    unittest.main()

import gzip
import http.client
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, patch

from promptbase_exporter import __version__
from promptbase_exporter.client import (
    MAX_RETRIES,
    PROMPT_DETAIL_FIELDS,
    PROMPT_DETAIL_SCHEMA_FIELDS,
    PROMPT_ITEM_FIELDS,
    PROMPT_ITEM_SCHEMA_FIELDS,
    USER_AGENT,
    PromptBaseError,
    _open_json_with_retry,
    _raise_if_schema_changed,
    _run_query,
    _run_query_all,
    fetch_prompts,
    field_filter,
    resolve_profile,
)
from promptbase_exporter.models import Profile


class PaginationTests(unittest.TestCase):
    def test_run_query_all_requires_order_by(self):
        with self.assertRaises(ValueError):
            _run_query_all(
                "Items",
                [field_filter("uid", "EQUAL", {"stringValue": "uid-1"})],
                order_by=[],
                page_size=2,
            )

    def test_run_query_all_uses_cursor_for_ordered_pages(self):
        page_one = [
            {
                "slug": "a",
                "created": 3,
                "_doc_name": "projects/p/databases/(default)/documents/Items/a",
            },
            {
                "slug": "b",
                "created": 2,
                "_doc_name": "projects/p/databases/(default)/documents/Items/b",
            },
        ]
        page_two = [
            {
                "slug": "c",
                "created": 1,
                "_doc_name": "projects/p/databases/(default)/documents/Items/c",
            }
        ]
        order_by = [
            {"field": {"fieldPath": "created"}, "direction": "DESCENDING"},
            {"field": {"fieldPath": "__name__"}, "direction": "DESCENDING"},
        ]
        calls = []

        def query(*_args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                self.assertIsNone(kwargs["start_after"])
                return page_one
            self.assertEqual(
                kwargs["start_after"],
                [
                    {"integerValue": "2"},
                    {
                        "referenceValue": (
                            "projects/p/databases/(default)/documents/Items/b"
                        )
                    },
                ],
            )
            return page_two

        with patch(
            "promptbase_exporter.client._run_query", side_effect=query
        ), patch("promptbase_exporter.client.time.sleep") as sleep:
            docs = _run_query_all(
                "Items",
                [field_filter("uid", "EQUAL", {"stringValue": "uid-1"})],
                order_by=order_by,
                page_size=2,
            )

        self.assertEqual([doc["slug"] for doc in docs], ["a", "b", "c"])
        # One pause between the two pages, never before the first page.
        self.assertEqual(sleep.call_count, 1)


class RunQueryResponseTests(unittest.TestCase):
    _filters = [field_filter("uid", "EQUAL", {"stringValue": "uid-1"})]

    def test_non_list_response_raises_prompt_base_error(self):
        with patch(
            "promptbase_exporter.client._open_json_with_retry",
            return_value={"error": {"message": "quota"}},
        ):
            with self.assertRaises(PromptBaseError):
                _run_query("Items", self._filters)

    def test_skips_rows_without_documents_and_tolerates_missing_name(self):
        rows = [
            {"readTime": "2026-01-01T00:00:00Z"},
            "garbage",
            {"document": {"fields": {"slug": {"stringValue": "a"}}}},
        ]
        with patch("promptbase_exporter.client._open_json_with_retry", return_value=rows):
            docs = _run_query("Items", self._filters)

        self.assertEqual(docs, [{"slug": "a", "_doc_name": ""}])

    def test_request_identifies_the_tool_honestly(self):
        with patch(
            "promptbase_exporter.client._open_json_with_retry", return_value=[]
        ) as open_json:
            _run_query("Items", self._filters)

        request = open_json.call_args.args[0]
        user_agent = request.get_header("User-agent")
        self.assertEqual(user_agent, USER_AGENT)
        self.assertTrue(user_agent.startswith(f"promptbase-profile-exporter/{__version__} "))
        self.assertNotIn("Mozilla", user_agent)
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data)
        self.assertEqual(body["structuredQuery"]["from"], [{"collectionId": "Items"}])


    def test_request_accepts_gzip_and_projects_requested_fields(self):
        with patch(
            "promptbase_exporter.client._open_json_with_retry", return_value=[]
        ) as open_json:
            _run_query("Items", self._filters, fields=("slug", "title"))
            _run_query("Items", self._filters)

        projected, unprojected = (call.args[0] for call in open_json.call_args_list)
        self.assertEqual(projected.get_header("Accept-encoding"), "gzip")
        # Google APIs also require "gzip" in the User-Agent to compress.
        self.assertIn("gzip", projected.get_header("User-agent"))
        self.assertEqual(
            json.loads(projected.data)["structuredQuery"]["select"],
            {"fields": [{"fieldPath": "slug"}, {"fieldPath": "title"}]},
        )
        self.assertNotIn("select", json.loads(unprojected.data)["structuredQuery"])


class SchemaDriftTests(unittest.TestCase):
    def test_raise_if_schema_changed_reports_missing_fields(self):
        with self.assertRaisesRegex(PromptBaseError, "missing expected field"):
            _raise_if_schema_changed(
                "Items",
                [{"slug": "a"}, {"slug": "b"}],
                {"slug", "title"},
            )

    def test_raise_if_schema_changed_allows_empty_results(self):
        _raise_if_schema_changed("Items", [], {"slug", "title"})

    def test_raise_if_schema_changed_reports_majority_missing_required_field(self):
        docs = [{"slug": "one", "title": "One"}] + [
            {"slug": f"missing-{index}"} for index in range(9)
        ]

        with self.assertRaisesRegex(PromptBaseError, "missing expected field"):
            _raise_if_schema_changed("Items", docs, {"slug", "title"})

    def test_raise_if_schema_changed_tolerates_isolated_missing_required_field(self):
        docs = [{"slug": "missing-title"}] + [
            {"slug": f"ok-{index}", "title": "Title"} for index in range(9)
        ]

        _raise_if_schema_changed("Items", docs, {"slug", "title"})

    def test_raise_if_schema_changed_ignores_optional_fields(self):
        _raise_if_schema_changed(
            "Items",
            [{"slug": "one"}, {"slug": "two"}],
            {"slug"},
        )


class FetchPromptTests(unittest.TestCase):
    def test_fetch_prompts_normalizes_domain_case(self):
        with patch(
            "promptbase_exporter.client.resolve_profile",
            return_value=Profile(username="acb", uid="uid-1"),
        ), patch(
            "promptbase_exporter.client.fetch_prompt_items",
            return_value=[
                {
                    "slug": "text-one",
                    "title": "Text One",
                    "type": "gpt",
                    "domain": "Text",
                    "created": 1,
                },
                {
                    "slug": "image-one",
                    "title": "Image One",
                    "type": "chatgpt-image",
                    "domain": "IMAGE",
                    "created": 1,
                },
            ],
        ), patch(
            "promptbase_exporter.client.fetch_prompt_details",
            return_value={
                "text-one": {"description": "Text description"},
                "image-one": {"description": "Image description"},
            },
        ):
            _profile, records = fetch_prompts("@acb")

        self.assertEqual([record.domain for record in records], ["text", "image"])
        self.assertEqual(sum(record.is_text for record in records), 1)
        self.assertEqual(sum(record.is_image for record in records), 1)


    def test_projection_covers_every_field_fetch_prompts_reads(self):
        # Firestore omits unrequested fields, so a field read in fetch_prompts
        # but missing from the projection would silently turn into 0 or "".
        item = {
            "slug": "one", "title": "One", "type": "gpt", "domain": "text",
            "created": 5, "price": 2.5, "discount": 0.2, "views": 7, "sales": 3,
            "downloads": 4, "favorites": 6, "rating": 4.5, "numReviews": 8,
            "status": "approved", "uid": "uid-1", "itemType": "prompt",
            "unrelated": "x", "_doc_name": "Items/one",
        }
        detail = {
            "slug": "one", "description": "Described", "created": 5, "uid": "uid-1",
            "output": "large example output", "_doc_name": "PromptDetails/one",
        }

        def fake_query_all(collection, _filters, *, fields=None, **_kwargs):
            doc = item if collection == "Items" else detail
            if fields is not None:
                doc = {k: v for k, v in doc.items() if k in fields or k == "_doc_name"}
            return [doc]

        def records(project):
            def query(collection, filters, **kwargs):
                if not project:
                    kwargs.pop("fields", None)
                return fake_query_all(collection, filters, **kwargs)

            with patch(
                "promptbase_exporter.client.resolve_profile",
                return_value=Profile(username="acb", uid="uid-1"),
            ), patch("promptbase_exporter.client._run_query_all", side_effect=query):
                return fetch_prompts("@acb")[1]

        self.assertEqual(records(project=True), records(project=False))
        self.assertEqual(records(project=True)[0].reviews, 8)

    def test_projections_include_schema_and_cursor_fields(self):
        self.assertLessEqual(PROMPT_ITEM_SCHEMA_FIELDS | {"created"}, set(PROMPT_ITEM_FIELDS))
        self.assertLessEqual(
            PROMPT_DETAIL_SCHEMA_FIELDS | {"created"}, set(PROMPT_DETAIL_FIELDS)
        )


class ResolveProfileTests(unittest.TestCase):
    def test_resolve_profile_rejects_ambiguous_profile_uids(self):
        with patch(
            "promptbase_exporter.client._run_query",
            return_value=[
                {"username": "acb", "uid": "uid-1"},
                {"username": "acb", "uid": "uid-2"},
            ],
        ):
            with self.assertRaisesRegex(PromptBaseError, "Ambiguous profile"):
                resolve_profile("@acb")

    def test_resolve_profile_uses_profile_doc_without_fallback(self):
        with patch(
            "promptbase_exporter.client._run_query",
            side_effect=[
                [{"username": "acb", "itemType": "profile", "uid": "uid-1"}],
            ],
        ) as run_query:
            profile = resolve_profile("@acb")

        self.assertEqual(profile, Profile(username="acb", uid="uid-1"))
        # The profile document carried the username, so no fallback query runs.
        self.assertEqual(run_query.call_count, 1)

    def test_resolve_profile_falls_back_when_profile_doc_lacks_username(self):
        # The profile-only query returns nothing because PromptBase left the
        # username field off the profile document; the owner uid is recovered
        # from the prompts/apps the profile owns.
        with patch(
            "promptbase_exporter.client._run_query",
            side_effect=[
                [],
                [
                    {"username": "emanema", "itemType": "prompt", "uid": "uid-9"},
                    {"username": "emanema", "itemType": "app", "uid": "uid-9"},
                ],
            ],
        ) as run_query:
            profile = resolve_profile("https://promptbase.com/profile/emanema")

        self.assertEqual(profile, Profile(username="emanema", uid="uid-9"))
        self.assertEqual(run_query.call_count, 2)

    def test_resolve_profile_raises_when_no_items_found(self):
        with patch(
            "promptbase_exporter.client._run_query",
            side_effect=[[], []],
        ):
            with self.assertRaisesRegex(PromptBaseError, "Profile not found: ghost"):
                resolve_profile("@ghost")


class RetryTests(unittest.TestCase):
    @staticmethod
    def _response(body: bytes, content_encoding: str | None = None) -> MagicMock:
        stream = io.BytesIO(body)
        stream.headers = Message()
        if content_encoding:
            stream.headers["Content-Encoding"] = content_encoding
        cm = MagicMock()
        cm.__enter__.return_value = stream
        cm.__exit__.return_value = False
        return cm

    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url="http://x",
            code=code,
            msg="boom",
            hdrs=None,
            fp=None,
        )

    def test_success_on_first_attempt_returns_decoded_json(self):
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.return_value = self._response(b'[{"document": 1}]')

            result = _open_json_with_retry(MagicMock())

        self.assertEqual(result, [{"document": 1}])
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_transient_http_error_then_success(self):
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [
                self._http_error(503),
                self._response(b'[{"document": 2}]'),
            ]

            result = _open_json_with_retry(MagicMock())

        self.assertEqual(result, [{"document": 2}])
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_non_transient_http_error_raises_immediately(self):
        error = self._http_error(404)
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = error

            with self.assertRaises(PromptBaseError) as ctx:
                _open_json_with_retry(MagicMock())

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertIs(ctx.exception.__cause__, error)

    def test_http_error_responses_are_closed(self):
        errors = [self._http_error(503), self._http_error(404)]
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen", side_effect=errors
        ), patch("promptbase_exporter.client.time.sleep"):
            with self.assertRaises(PromptBaseError):
                _open_json_with_retry(MagicMock())

        for error in errors:
            self.assertTrue(error.fp.closed)

    def test_persistent_transient_http_error_exhausts_retries(self):
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [self._http_error(503) for _ in range(MAX_RETRIES)]

            with self.assertRaises(PromptBaseError):
                _open_json_with_retry(MagicMock())

        self.assertEqual(urlopen.call_count, MAX_RETRIES)
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)

    def test_url_error_then_success_is_retried(self):
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [
                urllib.error.URLError("connection reset"),
                self._response(b'[{"document": 3}]'),
            ]

            result = _open_json_with_retry(MagicMock())

        self.assertEqual(result, [{"document": 3}])
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_dropped_connection_then_success_is_retried(self):
        # urlopen does not wrap errors raised while awaiting or reading the
        # response, so these arrive raw rather than as URLError.
        truncated = self._response(b"")
        truncated.__enter__.return_value = MagicMock(
            read=MagicMock(side_effect=http.client.IncompleteRead(b"[{"))
        )
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [
                http.client.RemoteDisconnected("Remote end closed connection"),
                truncated,
                self._response(b'[{"document": 4}]'),
            ]

            result = _open_json_with_retry(MagicMock())

        self.assertEqual(result, [{"document": 4}])
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_persistent_json_decode_error_exhausts_retries(self):
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [
                self._response(b"not json") for _ in range(MAX_RETRIES)
            ]

            with self.assertRaises(PromptBaseError) as ctx:
                _open_json_with_retry(MagicMock())

        self.assertEqual(urlopen.call_count, MAX_RETRIES)
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        self.assertIsInstance(ctx.exception.__cause__, json.JSONDecodeError)


    def test_gzip_response_is_decompressed(self):
        body = gzip.compress(b'[{"document": 5}]')
        with patch("promptbase_exporter.client.urllib.request.urlopen") as urlopen:
            urlopen.return_value = self._response(body, content_encoding="gzip")

            self.assertEqual(_open_json_with_retry(MagicMock()), [{"document": 5}])

    def test_corrupt_gzip_body_is_retried(self):
        truncated = gzip.compress(b'[{"document": 6}]')[:-6]
        with patch(
            "promptbase_exporter.client.urllib.request.urlopen"
        ) as urlopen, patch(
            "promptbase_exporter.client.time.sleep"
        ) as sleep:
            urlopen.side_effect = [
                self._response(b"not gzip at all", content_encoding="gzip"),
                self._response(truncated, content_encoding="gzip"),
                self._response(gzip.compress(b'[{"document": 6}]'), content_encoding="gzip"),
            ]

            result = _open_json_with_retry(MagicMock())

        self.assertEqual(result, [{"document": 6}])
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()

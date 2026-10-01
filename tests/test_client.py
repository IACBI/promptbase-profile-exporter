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
    DETAIL_COLLECTIONS,
    EXTRA_FIELD_SOURCES,
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
    fetch_prompt_details,
    fetch_prompt_items,
    fetch_prompts,
    field_filter,
    resolve_profile,
)
from promptbase_exporter.models import EXTRA_FIELDS, ITEM_TYPES, Profile


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

    def _open_once(self, body, **headers):
        with patch("promptbase_exporter.client.urllib.request.urlopen") as urlopen, \
                patch("promptbase_exporter.client.time.sleep"), \
                patch("promptbase_exporter.client.MAX_RESPONSE_BYTES", 1000):
            urlopen.return_value = self._response(body, **headers)
            try:
                return _open_json_with_retry(MagicMock())
            finally:
                self.calls = urlopen.call_count

    def test_an_oversized_body_is_refused_without_a_retry(self):
        with self.assertRaisesRegex(PromptBaseError, "over 0 MiB"):
            self._open_once(b"[" + b"1," * 600 + b"1]")
        self.assertEqual(self.calls, 1)

    def test_a_small_gzip_body_that_inflates_past_the_cap_is_refused(self):
        bomb = gzip.compress(b"[" + b" " * 5000 + b"]")
        self.assertLess(len(bomb), 1000)
        with self.assertRaisesRegex(PromptBaseError, "over 0 MiB"):
            self._open_once(bomb, content_encoding="gzip")
        self.assertEqual(self.calls, 1)

    def test_every_gzip_member_is_decoded(self):
        split = gzip.compress(b'[{"document": ') + gzip.compress(b"7}]")
        self.assertEqual(self._open_once(split, content_encoding="gzip"), [{"document": 7}])

    def test_the_cap_covers_all_gzip_members_together(self):
        members = gzip.compress(b"[" + b" " * 600) + gzip.compress(b" " * 600 + b"]")
        with self.assertRaisesRegex(PromptBaseError, "over 0 MiB"):
            self._open_once(members, content_encoding="gzip")

    def test_a_body_at_the_cap_is_read(self):
        body = b"[" + b" " * 998 + b"]"
        self.assertEqual(self._open_once(body), [])
        self.assertEqual(self._open_once(gzip.compress(body), content_encoding="gzip"), [])

    def test_deeply_nested_json_is_an_error_not_a_crash(self):
        # Whether real input this deep overflows the C parser depends on the
        # platform's stack (it does on Windows, not on Linux with Python 3.14), so
        # the parser's RecursionError is raised directly.
        with patch("promptbase_exporter.client.json.loads", side_effect=RecursionError), \
                patch("promptbase_exporter.client.urllib.request.urlopen") as urlopen:
            urlopen.return_value = self._response(b"[[[]]]")
            with self.assertRaisesRegex(PromptBaseError, "nested too deeply"):
                _open_json_with_retry(MagicMock())

    def test_a_deeply_nested_document_is_an_error_not_a_crash(self):
        value = {"stringValue": "x"}
        for _ in range(5000):
            value = {"mapValue": {"fields": {"k": value}}}
        with patch("promptbase_exporter.client._open_json_with_retry",
                   return_value=[{"document": {"fields": {"title": value}}}]), \
                self.assertRaisesRegex(PromptBaseError, "nested too deeply"):
            _run_query("Items", [field_filter("uid", "EQUAL", {"stringValue": "u"})])


class ExtraFieldsFetchTests(unittest.TestCase):
    _profile = Profile(username="acb", uid="uid-1")

    def _fetch_items(self, extra_fields):
        with patch(
            "promptbase_exporter.client._run_query_all", return_value=[]
        ) as run_query_all:
            fetch_prompt_items(self._profile, extra_fields)
        return run_query_all.call_args.kwargs["fields"]

    def test_default_projection_is_unchanged(self):
        self.assertEqual(self._fetch_items(()), PROMPT_ITEM_FIELDS)

    def test_requested_fields_are_added_under_their_firestore_names(self):
        fields = self._fetch_items(("last_sale", "tags"))
        self.assertEqual(fields, (*PROMPT_ITEM_FIELDS, "lastSale", "tags"))

    def test_every_extra_field_has_a_firestore_source(self):
        self.assertEqual(tuple(EXTRA_FIELD_SOURCES), EXTRA_FIELDS)

    def test_unknown_extra_field_fails_before_any_request(self):
        with patch("promptbase_exporter.client.resolve_profile") as resolve:
            with self.assertRaisesRegex(ValueError, "Unknown extra field.*bogus"):
                fetch_prompts("@acb", extra_fields=("tags", "bogus"))
        resolve.assert_not_called()

    def test_records_carry_typed_extra_values(self):
        item = {
            "slug": "a", "title": "A", "type": "gpt", "domain": "text", "created": 5,
            "tags": ["x", " ", "y "], "engine": " gpt-5 ", "nsfw": True, "featured": False,
            "updated": 7, "lastSale": "9", "uniqueSales": 4,
        }
        bare = {"slug": "b", "title": "B", "type": "gpt", "domain": "text", "created": 4,
                "tags": "not-a-list"}
        with patch("promptbase_exporter.client.resolve_profile", return_value=self._profile), \
                patch("promptbase_exporter.client.fetch_prompt_items", return_value=[item, bare]), \
                patch("promptbase_exporter.client.fetch_prompt_details", return_value={}):
            _profile, records = fetch_prompts("@acb", extra_fields=EXTRA_FIELDS)
        first, second = records
        self.assertEqual(first.tags, ("x", "y"))
        self.assertEqual(first.engine, "gpt-5")
        self.assertIs(first.nsfw, True)
        self.assertEqual((first.updated, first.last_sale, first.unique_sales), (7, 9, 4))
        # Missing values stay unknown (None) or empty instead of becoming a recorded zero.
        self.assertEqual(second.tags, ())
        self.assertIsNone(second.updated)
        self.assertIsNone(second.last_sale)
        self.assertEqual(second.unique_sales, 0)

    def test_non_numeric_timestamp_is_reported(self):
        item = {"slug": "a", "title": "A", "type": "gpt", "domain": "text", "created": 5,
                "updated": "yesterday"}
        with patch("promptbase_exporter.client.resolve_profile", return_value=self._profile), \
                patch("promptbase_exporter.client.fetch_prompt_items", return_value=[item]), \
                patch("promptbase_exporter.client.fetch_prompt_details", return_value={}):
            with self.assertRaisesRegex(PromptBaseError, "numeric PromptBase field 'updated'"):
                fetch_prompts("@acb", extra_fields=("updated",))


class ItemTypeFetchTests(unittest.TestCase):
    _profile = Profile(username="acb", uid="uid-1")

    def test_every_kind_has_a_detail_collection(self):
        self.assertEqual(tuple(DETAIL_COLLECTIONS), ITEM_TYPES)

    def test_items_are_filtered_by_the_requested_kind(self):
        for item_type in ITEM_TYPES:
            with patch(
                "promptbase_exporter.client._run_query_all", return_value=[]
            ) as run_query_all:
                fetch_prompt_items(self._profile, (), item_type)
            filters = run_query_all.call_args.args[1]
            self.assertIn(
                field_filter("itemType", "EQUAL", {"stringValue": item_type}), filters
            )

    def test_details_come_from_the_kinds_own_collection(self):
        expected = {"prompt": "PromptDetails", "bundle": "Bundles", "app": "AppDetails"}
        for item_type, collection in expected.items():
            with patch(
                "promptbase_exporter.client._run_query_all", return_value=[]
            ) as run_query_all:
                fetch_prompt_details(self._profile, item_type)
            self.assertEqual(run_query_all.call_args.args[0], collection)

    def test_default_kind_is_prompts(self):
        with patch("promptbase_exporter.client._run_query_all", return_value=[]) as query:
            fetch_prompt_items(self._profile)
            fetch_prompt_details(self._profile)
        self.assertEqual([call.args[0] for call in query.call_args_list],
                         ["Items", "PromptDetails"])

    def test_unknown_kind_fails_before_any_request(self):
        with patch("promptbase_exporter.client.resolve_profile") as resolve:
            with self.assertRaisesRegex(ValueError, "Unknown item type: skill"):
                fetch_prompts("@acb", item_type="skill")
        resolve.assert_not_called()

    def test_records_carry_the_kind_and_join_the_right_details(self):
        item = {"slug": "kit", "title": "Kit", "type": "gpt", "domain": "text", "created": 5}
        with patch("promptbase_exporter.client.resolve_profile", return_value=self._profile), \
                patch("promptbase_exporter.client.fetch_prompt_items",
                      return_value=[item]) as items, \
                patch("promptbase_exporter.client.fetch_prompt_details",
                      return_value={"kit": {"description": "A bundle"}}) as details:
            _profile, records = fetch_prompts("@acb", item_type="bundle")
        items.assert_called_once_with(self._profile, (), "bundle")
        details.assert_called_once_with(self._profile, "bundle")
        self.assertEqual(records[0].item_type, "bundle")
        self.assertEqual(records[0].description, "A bundle")
        self.assertEqual(records[0].url, "https://promptbase.com/bundle/kit")

    def test_a_slug_shared_by_two_kinds_stays_separate(self):
        # Live data: "website" is both a prompt and an app; each kind is fetched
        # with its own itemType filter, so the two never merge.
        prompt = {"slug": "website", "title": "P", "type": "gpt", "domain": "text", "created": 2}
        app = {"slug": "website", "title": "A", "type": "", "domain": "text", "created": 1}
        by_kind = {"prompt": [prompt], "app": [app]}

        def query(collection, filters, **_kwargs):
            wanted = next(
                f["value"]["stringValue"] for f in filters if f["field"]["fieldPath"] == "itemType"
            )
            return by_kind[wanted]

        with patch("promptbase_exporter.client._run_query_all", side_effect=query):
            self.assertEqual(fetch_prompt_items(self._profile, (), "prompt")[0]["title"], "P")
            self.assertEqual(fetch_prompt_items(self._profile, (), "app")[0]["title"], "A")

    def test_apps_without_a_type_field_are_not_schema_drift(self):
        apps = [{"slug": f"a{i}", "title": "A", "domain": "text", "created": i} for i in range(5)]
        with patch("promptbase_exporter.client._run_query_all", return_value=apps):
            self.assertEqual(len(fetch_prompt_items(self._profile, (), "app")), 5)
            # The same documents as prompts or bundles are missing a field they need.
            for item_type in ("prompt", "bundle"):
                with self.subTest(item_type=item_type), self.assertRaisesRegex(
                    PromptBaseError, "type"
                ):
                    fetch_prompt_items(self._profile, (), item_type)


if __name__ == "__main__":
    unittest.main()

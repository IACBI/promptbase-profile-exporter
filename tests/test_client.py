import http.client
import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from promptbase_exporter.client import (
    MAX_RETRIES,
    PromptBaseError,
    _open_json_with_retry,
    _raise_if_schema_changed,
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
    def _response(body: bytes) -> MagicMock:
        cm = MagicMock()
        cm.__enter__.return_value = io.BytesIO(body)
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


if __name__ == "__main__":
    unittest.main()

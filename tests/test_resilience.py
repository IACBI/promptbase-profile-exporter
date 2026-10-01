"""The client against a real local HTTP server that misbehaves the way networks do.

The other client tests mock ``urlopen``; these go through ``urllib``, ``http.client``
and a socket, so status codes, dropped connections, truncated and compressed
bodies, slow responses, and paging are exercised end to end. Nothing leaves
127.0.0.1.
"""

import gzip
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from promptbase_exporter import client
from promptbase_exporter.client import PromptBaseError, field_filter
from tests.scratch import use_scratch_working_directory

FILTERS = [field_filter("uid", "EQUAL", {"stringValue": "u"})]
ORDER = [client._order_by("created", "DESCENDING"), client._order_by("__name__", "DESCENDING")]


def setUpModule():
    use_scratch_working_directory()


def document(number):
    return {"document": {
        "name": f"projects/p/databases/(default)/documents/Items/doc{number:04d}",
        "fields": {"slug": {"stringValue": f"s{number}"},
                   "created": {"integerValue": str(10_000 - number)}},
    }}


class Script:
    """Responses to send, in order; each is a callable taking the handler."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []
        self.lock = threading.Lock()

    def next(self, handler, body):
        with self.lock:
            self.requests.append(json.loads(body))
            step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        step(handler)


def reply(payload, *, status=200, compress=False, cut=None, delay=0.0):
    def send(handler):
        # Not time.sleep: the tests patch the client's sleep, which is the same function.
        threading.Event().wait(delay)
        body = json.dumps(payload).encode()
        if compress:
            body = gzip.compress(body)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        if compress:
            handler.send_header("Content-Encoding", "gzip")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        # ``cut`` sends only part of the promised body, then drops the connection.
        handler.wfile.write(body if cut is None else body[:cut])
        handler.wfile.flush()
        if cut is not None:
            handler.connection.shutdown(socket.SHUT_RDWR)
    return send


def hang_up(handler):
    handler.connection.shutdown(socket.SHUT_RDWR)


class FakeFirestore:
    def __init__(self, script):
        self.script = script
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.script.next(self, self.rfile.read(length))

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        port = self.server.server_address[1]
        self.patches = [
            patch.object(client, "FIRESTORE_RUN_QUERY", f"http://127.0.0.1:{port}/runQuery"),
            patch.object(client, "REQUEST_TIMEOUT_SECONDS", 2),
            patch.object(client, "PAGE_DELAY_SECONDS", 0),
            patch("promptbase_exporter.client.time.sleep"),
        ]
        for item in self.patches:
            item.start()
        return self.script

    def __exit__(self, *exc):
        for item in reversed(self.patches):
            item.stop()
        self.server.shutdown()
        self.server.server_close()


def query(**kwargs):
    return client._run_query("Items", FILTERS, order_by=ORDER, **kwargs)


class TransientFailureTests(unittest.TestCase):
    def test_server_errors_are_retried_until_one_succeeds(self):
        with FakeFirestore(Script(reply({}, status=503), reply({}, status=502),
                                  reply([document(1)]))) as script:
            docs = query()
        self.assertEqual([doc["slug"] for doc in docs], ["s1"])
        self.assertEqual(len(script.requests), 3)

    def test_a_connection_dropped_before_the_response_is_retried(self):
        with FakeFirestore(Script(hang_up, reply([document(2)]))) as script:
            self.assertEqual(query()[0]["slug"], "s2")
        self.assertEqual(len(script.requests), 2)

    def test_a_body_cut_off_mid_transfer_is_retried(self):
        with FakeFirestore(Script(reply([document(3)] * 50, cut=200),
                                  reply([document(3)]))) as script:
            self.assertEqual(query()[0]["slug"], "s3")
        self.assertEqual(len(script.requests), 2)

    def test_a_truncated_compressed_body_is_retried(self):
        with FakeFirestore(Script(reply([document(4)] * 50, compress=True, cut=60),
                                  reply([document(4)], compress=True))) as script:
            self.assertEqual(query()[0]["slug"], "s4")
        self.assertEqual(len(script.requests), 2)

    def test_a_response_slower_than_the_timeout_is_retried(self):
        with FakeFirestore(Script(reply([document(5)], delay=3), reply([document(5)]))) as script:
            self.assertEqual(query()[0]["slug"], "s5")
        self.assertEqual(len(script.requests), 2)

    def test_retries_stop_after_the_limit_with_a_clear_error(self):
        with FakeFirestore(Script(reply({}, status=503))) as script, \
                self.assertRaisesRegex(PromptBaseError, "503"):
            query()
        self.assertEqual(len(script.requests), client.MAX_RETRIES)


class PermanentFailureTests(unittest.TestCase):
    def test_a_client_error_is_not_retried(self):
        for status in (400, 403, 404):
            with self.subTest(status=status), \
                    FakeFirestore(Script(reply({}, status=status))) as script, \
                    self.assertRaisesRegex(PromptBaseError, str(status)):
                query()
            self.assertEqual(len(script.requests), 1)

    def test_a_response_that_is_not_a_list_is_rejected(self):
        with FakeFirestore(Script(reply({"error": "odd"}))), \
                self.assertRaisesRegex(PromptBaseError, "unexpected response shape"):
            query()


class PagingTests(unittest.TestCase):
    def test_pages_are_followed_with_a_cursor_from_the_last_document(self):
        pages = [[document(n) for n in range(0, 3)], [document(n) for n in range(3, 6)],
                 [document(6)]]
        with FakeFirestore(Script(*(reply(page) for page in pages))) as script:
            docs = client._run_query_all("Items", FILTERS, order_by=ORDER, page_size=3)
        self.assertEqual([doc["slug"] for doc in docs], [f"s{n}" for n in range(7)])
        cursors = [request["structuredQuery"].get("startAt") for request in script.requests]
        self.assertIsNone(cursors[0])
        self.assertEqual(cursors[1]["values"][0], {"integerValue": str(10_000 - 2)})
        self.assertTrue(cursors[2]["values"][1]["referenceValue"].endswith("doc0005"))

    def test_a_server_that_ignores_the_cursor_is_stopped_at_once(self):
        same_page = reply([document(n) for n in range(3)])
        with FakeFirestore(Script(same_page)) as script, \
                self.assertRaisesRegex(PromptBaseError, "did not advance"):
            client._run_query_all("Items", FILTERS, order_by=ORDER, page_size=3)
        # Two requests show the cursor did not move; it does not run to MAX_PAGES.
        self.assertEqual(len(script.requests), 2)


if __name__ == "__main__":
    unittest.main()

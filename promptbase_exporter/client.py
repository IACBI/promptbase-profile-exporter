from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Sequence
from typing import Any

from . import __version__
from .models import EXTRA_FIELDS, ITEM_TYPES, Profile, PromptRecord

FIRESTORE_RUN_QUERY = (
    "https://firestore.googleapis.com/v1/projects/"
    "promptbase/databases/(default)/documents:runQuery"
)

# Identify the tool honestly rather than posing as a browser, so the operator
# of the public endpoint can see (and contact) what is making these requests.
# Google APIs document that a gzip response needs "gzip" in the User-Agent as
# well as Accept-Encoding: gzip, hence the "(gzip)" suffix.
USER_AGENT = (
    f"promptbase-profile-exporter/{__version__} "
    "(+https://github.com/IACBI/promptbase-profile-exporter) (gzip)"
)

TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_PAGE_SIZE = 300
MAX_PAGES = 100
MAX_RETRIES = 3
# Far above a real page (300 records with descriptions is a few MB), but a bound,
# so a broken or hostile response cannot exhaust memory, compressed or not.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
# Small pause between successive pages of a paginated query. Single-page
# fetches (the common case) never wait; multi-page fetches stay polite to the
# public Firestore endpoint and reduce the chance of hitting rate limits.
PAGE_DELAY_SECONDS = 0.2
SCHEMA_DRIFT_MISSING_RATIO = 0.8
PROMPT_ITEM_SCHEMA_FIELDS = {"slug", "title", "created", "domain", "type"}
# Apps have no model type: PromptBase sends an empty "type" today, and an app
# without one is not schema drift.
ITEM_SCHEMA_FIELDS = {
    "prompt": PROMPT_ITEM_SCHEMA_FIELDS,
    "bundle": PROMPT_ITEM_SCHEMA_FIELDS,
    "app": PROMPT_ITEM_SCHEMA_FIELDS - {"type"},
}
PROMPT_DETAIL_SCHEMA_FIELDS = {"slug", "description"}
# Fields requested from each collection (a Firestore projection). Documents
# carry many more, including large ones such as example outputs, so asking for
# only what fetch_prompts reads makes the responses over an order of magnitude
# smaller. Every field read below, and every schema-drift and cursor field,
# must be listed here or it will silently come back missing.
PROMPT_ITEM_FIELDS = (
    "slug",
    "title",
    "type",
    "domain",
    "created",
    "price",
    "discount",
    "views",
    "sales",
    "downloads",
    "favorites",
    "rating",
    "numReviews",
)
PROMPT_DETAIL_FIELDS = ("slug", "description", "created")
# The Firestore field behind each optional record field. Requested fields are
# added to the projection, so a default export still downloads only the fields
# above.
EXTRA_FIELD_SOURCES = {
    "tags": "tags",
    "engine": "engine",
    "nsfw": "nsfw",
    "featured": "featured",
    "updated": "updated",
    "last_sale": "lastSale",
    "unique_sales": "uniqueSales",
}
assert tuple(EXTRA_FIELD_SOURCES) == EXTRA_FIELDS  # noqa: S101 - import-time table check
# The public collection that holds each kind's description. They all carry
# slug, description, and created, and are joined to Items by slug (verified:
# every Bundles and AppDetails document matches exactly one Items document).
DETAIL_COLLECTIONS = {
    "prompt": "PromptDetails",
    "bundle": "Bundles",
    "app": "AppDetails",
}
assert tuple(DETAIL_COLLECTIONS) == ITEM_TYPES  # noqa: S101 - import-time table check


class PromptBaseError(RuntimeError):
    """Raised when PromptBase public data cannot be resolved."""


def parse_profile_input(profile_input: str) -> str:
    """Return a PromptBase username from a URL, path, username, or @username."""
    raw = profile_input.strip()
    if not raw:
        raise PromptBaseError("Profile input is empty.")

    raw = raw.rstrip("/")
    # A URL copied without its scheme ("promptbase.com/profile/acb") would
    # otherwise be taken as the username itself.
    if raw.lower().startswith(("promptbase.com/", "www.promptbase.com/")):
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)

    if parsed.scheme and parsed.netloc:
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0].lower() == "profile":
            return path_parts[1].lstrip("@")
        raise PromptBaseError(
            "Expected a PromptBase profile URL like "
            "https://promptbase.com/profile/username."
        )

    if raw.lower().startswith("profile/"):
        return raw.split("/", 1)[1].strip("/").lstrip("@")

    return raw.lstrip("@")


def firestore_value(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "nullValue" in value:
        return None
    if "arrayValue" in value:
        values = value.get("arrayValue", {}).get("values", [])
        return [firestore_value(item) for item in values]
    if "mapValue" in value:
        fields = value.get("mapValue", {}).get("fields", {})
        return {key: firestore_value(item) for key, item in fields.items()}
    return value


def field_filter(field_path: str, op: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"field": {"fieldPath": field_path}, "op": op, "value": value}


def _run_query(
    collection: str,
    filters: list[dict[str, Any]],
    *,
    order_by: list[dict[str, Any]] | None = None,
    limit: int = 500,
    start_after: list[dict[str, Any]] | None = None,
    fields: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not filters:
        raise ValueError("At least one filter is required.")

    if len(filters) == 1:
        where: dict[str, Any] = {"fieldFilter": filters[0]}
    else:
        where = {
            "compositeFilter": {
                "op": "AND",
                "filters": [{"fieldFilter": item} for item in filters],
            }
        }

    structured_query: dict[str, Any] = {
        "from": [{"collectionId": collection}],
        "where": where,
        "limit": limit,
    }
    if fields:
        structured_query["select"] = {"fields": [{"fieldPath": field} for field in fields]}
    if order_by:
        structured_query["orderBy"] = order_by
    if start_after:
        structured_query["startAt"] = {"values": start_after, "before": False}

    request = urllib.request.Request(
        FIRESTORE_RUN_QUERY,
        data=json.dumps({"structuredQuery": structured_query}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            # JSON compresses well: gzip makes responses a few times smaller.
            "Accept-Encoding": "gzip",
        },
    )

    rows = _open_json_with_retry(request)
    if not isinstance(rows, list):
        raise PromptBaseError("PromptBase query returned an unexpected response shape.")

    docs: list[dict[str, Any]] = []
    for row in rows:
        document = row.get("document") if isinstance(row, dict) else None
        if not document:
            continue
        try:
            doc = {
                key: firestore_value(item)
                for key, item in document.get("fields", {}).items()
            }
        except RecursionError:
            raise PromptBaseError("PromptBase returned a document nested too deeply.") from None
        doc["_doc_name"] = document.get("name", "")
        docs.append(doc)
    return docs


def _open_json_with_retry(request: urllib.request.Request) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return _read_json(response)
        except urllib.error.HTTPError as exc:
            # The error doubles as the open HTTP response; release its socket.
            exc.close()
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_STATUS_CODES or attempt == MAX_RETRIES:
                break
        # urlopen only wraps errors from sending the request in URLError; a
        # dropped connection while awaiting or reading the response surfaces
        # raw (e.g. RemoteDisconnected, IncompleteRead), so retry those too.
        except (
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
            http.client.BadStatusLine,
            urllib.error.URLError,
            json.JSONDecodeError,
            # A truncated or corrupt gzip body, the compressed IncompleteRead.
            EOFError,
            zlib.error,
        ) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
        time.sleep(0.8 * attempt)
    raise PromptBaseError(f"PromptBase query failed: {last_error}") from last_error


def _read_json(response: Any) -> Any:
    """Decode a JSON response body, gunzipping it if the server compressed it.

    Both the body and its decompressed form are capped at ``MAX_RESPONSE_BYTES``.
    """
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise PromptBaseError(_too_large())
    if (response.headers.get("Content-Encoding") or "").strip().lower() == "gzip":
        body = _gunzip(body)
    try:
        return json.loads(body)
    except RecursionError:
        raise PromptBaseError("PromptBase returned a response nested too deeply.") from None


def _gunzip(body: bytes) -> bytes:
    """Decompress every gzip member of ``body``, as ``gzip.decompress`` would, but stop
    once the output would exceed ``MAX_RESPONSE_BYTES``."""
    parts: list[bytes] = []
    size = 0
    remaining = body
    while remaining:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        part = decoder.decompress(remaining, MAX_RESPONSE_BYTES + 1 - size)
        size += len(part)
        if size > MAX_RESPONSE_BYTES or decoder.unconsumed_tail:
            raise PromptBaseError(_too_large())
        if not decoder.eof:
            # A truncated body: retried like the IncompleteRead it is.
            raise EOFError("compressed response ended early")
        parts.append(part)
        remaining = decoder.unused_data
    return b"".join(parts)


def _too_large() -> str:
    return f"PromptBase returned a response over {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB."


def _run_query_all(
    collection: str,
    filters: list[dict[str, Any]],
    *,
    order_by: list[dict[str, Any]],
    page_size: int = DEFAULT_PAGE_SIZE,
    fields: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not order_by:
        # Pagination relies on cursors built from the ordering key.
        raise ValueError("order_by is required for paginated queries.")
    docs: list[dict[str, Any]] = []
    start_after: list[dict[str, Any]] | None = None
    for _ in range(MAX_PAGES):
        page_docs = _run_query(
            collection,
            filters,
            order_by=order_by,
            limit=page_size,
            start_after=start_after,
            fields=fields,
        )
        docs.extend(page_docs)
        if len(page_docs) < page_size:
            return docs
        start_after = _cursor_values_for_doc(page_docs[-1], order_by)
        time.sleep(PAGE_DELAY_SECONDS)
    raise PromptBaseError(
        f"Query exceeded the pagination safety limit of {MAX_PAGES * page_size} records."
    )


def _order_by(field_path: str, direction: str) -> dict[str, Any]:
    return {"field": {"fieldPath": field_path}, "direction": direction}


def _cursor_values_for_doc(
    doc: dict[str, Any],
    order_by: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in order_by:
        field_path = item.get("field", {}).get("fieldPath")
        if field_path == "__name__":
            doc_name = str(doc.get("_doc_name") or "")
            if not doc_name:
                raise PromptBaseError("Cannot paginate ordered query: document name is missing.")
            values.append({"referenceValue": doc_name})
            continue
        if field_path not in doc:
            raise PromptBaseError(
                f"Cannot paginate ordered query: field '{field_path}' is missing."
            )
        values.append(_firestore_cursor_value(doc[field_path]))
    return values


def _firestore_cursor_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if value is None:
        return {"nullValue": None}
    return {"stringValue": str(value)}


def resolve_profile(profile_input: str) -> Profile:
    username = parse_profile_input(profile_input)

    # Preferred path: the profile document itself carries the username field
    # (for example @acb). Its document id is the owner uid.
    profile_docs = _run_query(
        "Items",
        [
            field_filter("username", "EQUAL", {"stringValue": username}),
            field_filter("itemType", "EQUAL", {"stringValue": "profile"}),
        ],
        limit=5,
    )
    uid = _distinct_uid_for_username(profile_docs, username)
    if uid:
        return Profile(username=username, uid=uid)

    # Fallback: PromptBase does not always store the username field on the
    # profile document (it can be absent, as for @emanema / @dreamydesigns), so
    # the profile-only query above returns nothing. Every prompt/app/bundle the
    # profile owns does carry the username alongside the same owner uid, so we
    # resolve the uid from any owned item instead.
    owned_docs = _run_query(
        "Items",
        [field_filter("username", "EQUAL", {"stringValue": username})],
        limit=10,
    )
    if not owned_docs:
        raise PromptBaseError(f"Profile not found: {username}")
    uid = _distinct_uid_for_username(owned_docs, username)
    if not uid:
        raise PromptBaseError(f"Profile UID could not be resolved: {username}")
    return Profile(username=username, uid=uid)


def _distinct_uid_for_username(docs: list[dict[str, Any]], username: str) -> str:
    """Return the single owner uid for an exact-username match, or "" if none.

    Raises if the documents disagree on which uid owns the username.
    """
    matching_docs = [doc for doc in docs if str(doc.get("username") or "").strip() == username]
    distinct_uids = sorted(
        {
            str(uid)
            for doc in matching_docs
            if (uid := doc.get("uid") or doc.get("id") or doc.get("itemId"))
        }
    )
    if len(distinct_uids) > 1:
        raise PromptBaseError(
            f"Ambiguous profile lookup for {username}: multiple profile UIDs matched."
        )
    return distinct_uids[0] if distinct_uids else ""


def fetch_prompt_items(
    profile: Profile,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> list[dict[str, Any]]:
    docs = _run_query_all(
        "Items",
        [
            field_filter("status", "EQUAL", {"stringValue": "approved"}),
            field_filter("uid", "EQUAL", {"stringValue": profile.uid}),
            field_filter("itemType", "EQUAL", {"stringValue": item_type}),
        ],
        order_by=[
            _order_by("created", "DESCENDING"),
            _order_by("__name__", "DESCENDING"),
        ],
        fields=PROMPT_ITEM_FIELDS + tuple(EXTRA_FIELD_SOURCES[name] for name in extra_fields),
    )
    _raise_if_schema_changed("Items", docs, ITEM_SCHEMA_FIELDS[item_type])

    seen_slugs: set[str] = set()
    prompts: list[dict[str, Any]] = []
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        title = str(doc.get("title") or "").strip()
        if not slug or not title or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        prompts.append(doc)
    return prompts


def fetch_prompt_details(
    profile: Profile,
    item_type: str = "prompt",
) -> dict[str, dict[str, Any]]:
    collection = DETAIL_COLLECTIONS[item_type]
    docs = _run_query_all(
        collection,
        [field_filter("uid", "EQUAL", {"stringValue": profile.uid})],
        order_by=[_order_by("__name__", "ASCENDING")],
        fields=PROMPT_DETAIL_FIELDS,
    )
    _raise_if_schema_changed(collection, docs, PROMPT_DETAIL_SCHEMA_FIELDS)

    by_slug: dict[str, dict[str, Any]] = {}
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        if not slug:
            continue
        if slug not in by_slug or _int_field(doc, "created") >= _int_field(
            by_slug[slug], "created"
        ):
            by_slug[slug] = doc
    return by_slug


def fetch_prompts(
    profile_input: str,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> tuple[Profile, list[PromptRecord]]:
    """Fetch a profile's approved listings of one kind (prompts by default).

    ``extra_fields`` names optional record fields (see ``models.EXTRA_FIELDS``)
    to download as well; the others keep their defaults. ``item_type`` is one
    of ``models.ITEM_TYPES``.
    """
    unknown = [name for name in extra_fields if name not in EXTRA_FIELD_SOURCES]
    if unknown:
        raise ValueError(f"Unknown extra field(s): {', '.join(unknown)}")
    if item_type not in ITEM_TYPES:
        raise ValueError(f"Unknown item type: {item_type}")
    profile = resolve_profile(profile_input)
    items = fetch_prompt_items(profile, extra_fields, item_type)
    details_by_slug = fetch_prompt_details(profile, item_type)

    records: list[PromptRecord] = []
    for item in items:
        slug = str(item.get("slug") or "").strip()
        details = details_by_slug.get(slug, {})
        records.append(
            PromptRecord(
                title=str(item.get("title") or "").strip(),
                description=str(details.get("description") or "").strip(),
                slug=slug,
                prompt_type=str(item.get("type") or "").strip(),
                domain=str(item.get("domain") or "").strip().lower(),
                created=_int_field(item, "created"),
                price=_float_field(item, "price"),
                discount=_float_field(item, "discount"),
                views=_int_field(item, "views"),
                sales=_int_field(item, "sales"),
                downloads=_int_field(item, "downloads"),
                favorites=_int_field(item, "favorites"),
                rating=_float_field(item, "rating"),
                reviews=_int_field(item, "numReviews"),
                tags=_str_tuple_field(item, "tags"),
                engine=str(item.get("engine") or "").strip(),
                nsfw=bool(item.get("nsfw")),
                featured=bool(item.get("featured")),
                updated=_optional_int_field(item, "updated"),
                last_sale=_optional_int_field(item, "lastSale"),
                unique_sales=_int_field(item, "uniqueSales"),
                item_type=item_type,
            )
        )

    records.sort(key=lambda record: (record.created, record.slug), reverse=True)
    return profile, records


def _raise_if_schema_changed(
    collection: str,
    docs: list[dict[str, Any]],
    expected_fields: set[str],
) -> None:
    if not docs:
        return
    doc_count = len(docs)
    severely_missing = []
    for field in sorted(expected_fields):
        missing_count = sum(1 for doc in docs if field not in doc)
        if missing_count == doc_count or missing_count / doc_count >= SCHEMA_DRIFT_MISSING_RATIO:
            severely_missing.append(f"{field} ({missing_count}/{doc_count})")
    if severely_missing:
        missing = ", ".join(severely_missing)
        raise PromptBaseError(
            f"PromptBase public data schema changed for {collection}: "
            f"missing expected field(s) in most returned documents: {missing}"
        )


def _int_field(item: dict[str, Any], field: str) -> int:
    value = item.get(field)
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PromptBaseError(
            f"Expected numeric PromptBase field '{field}', got {value!r}"
        ) from exc


def _optional_int_field(item: dict[str, Any], field: str) -> int | None:
    """An integer field, or None when PromptBase does not record it."""
    if item.get(field) is None or item.get(field) == "":
        return None
    return _int_field(item, field)


def _str_tuple_field(item: dict[str, Any], field: str) -> tuple[str, ...]:
    value = item.get(field)
    if not isinstance(value, list):
        return ()
    return tuple(str(tag).strip() for tag in value if str(tag).strip())


def _float_field(item: dict[str, Any], field: str) -> float:
    value = item.get(field)
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PromptBaseError(
            f"Expected numeric PromptBase field '{field}', got {value!r}"
        ) from exc

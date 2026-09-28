from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import Profile, PromptRecord

FIRESTORE_RUN_QUERY = (
    "https://firestore.googleapis.com/v1/projects/"
    "promptbase/databases/(default)/documents:runQuery"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_PAGE_SIZE = 300
MAX_PAGES = 100
MAX_RETRIES = 3
# Small pause between successive pages of a paginated query. Single-page
# fetches (the common case) never wait; multi-page fetches stay polite to the
# public Firestore endpoint and reduce the chance of hitting rate limits.
PAGE_DELAY_SECONDS = 0.2
SCHEMA_DRIFT_MISSING_RATIO = 0.8
PROMPT_ITEM_SCHEMA_FIELDS = {"slug", "title", "created", "domain", "type"}
PROMPT_DETAIL_SCHEMA_FIELDS = {"slug", "description"}


class PromptBaseError(RuntimeError):
    """Raised when PromptBase public data cannot be resolved."""


def parse_profile_input(profile_input: str) -> str:
    """Return a PromptBase username from a URL, path, username, or @username."""
    raw = profile_input.strip()
    if not raw:
        raise PromptBaseError("Profile input is empty.")

    raw = raw.rstrip("/")
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
    if order_by:
        structured_query["orderBy"] = order_by
    if start_after:
        structured_query["startAt"] = {"values": start_after, "before": False}

    request = urllib.request.Request(
        FIRESTORE_RUN_QUERY,
        data=json.dumps({"structuredQuery": structured_query}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )

    rows = _open_json_with_retry(request)

    docs: list[dict[str, Any]] = []
    for row in rows:
        document = row.get("document")
        if not document:
            continue
        fields = {
            key: firestore_value(item)
            for key, item in document.get("fields", {}).items()
        }
        fields["_doc_name"] = document["name"]
        docs.append(fields)
    return docs


def _open_json_with_retry(request: urllib.request.Request) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
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
        ) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                break
        time.sleep(0.8 * attempt)
    raise PromptBaseError(f"PromptBase query failed: {last_error}") from last_error


def _run_query_all(
    collection: str,
    filters: list[dict[str, Any]],
    *,
    order_by: list[dict[str, Any]],
    page_size: int = DEFAULT_PAGE_SIZE,
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


def fetch_prompt_items(profile: Profile) -> list[dict[str, Any]]:
    docs = _run_query_all(
        "Items",
        [
            field_filter("status", "EQUAL", {"stringValue": "approved"}),
            field_filter("uid", "EQUAL", {"stringValue": profile.uid}),
            field_filter("itemType", "EQUAL", {"stringValue": "prompt"}),
        ],
        order_by=[
            _order_by("created", "DESCENDING"),
            _order_by("__name__", "DESCENDING"),
        ],
    )
    _raise_if_schema_changed("Items", docs, PROMPT_ITEM_SCHEMA_FIELDS)

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


def fetch_prompt_details(profile: Profile) -> dict[str, dict[str, Any]]:
    docs = _run_query_all(
        "PromptDetails",
        [field_filter("uid", "EQUAL", {"stringValue": profile.uid})],
        order_by=[_order_by("__name__", "ASCENDING")],
    )
    _raise_if_schema_changed("PromptDetails", docs, PROMPT_DETAIL_SCHEMA_FIELDS)

    by_slug: dict[str, dict[str, Any]] = {}
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        if not slug:
            continue
        if slug not in by_slug or int(doc.get("created") or 0) >= int(
            by_slug[slug].get("created") or 0
        ):
            by_slug[slug] = doc
    return by_slug


def fetch_prompts(profile_input: str) -> tuple[Profile, list[PromptRecord]]:
    profile = resolve_profile(profile_input)
    items = fetch_prompt_items(profile)
    details_by_slug = fetch_prompt_details(profile)

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

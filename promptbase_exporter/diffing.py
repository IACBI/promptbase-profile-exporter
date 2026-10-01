from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .console import printable
from .formatting import (
    UTF8_BOM,
    _atomic_write_text,
    csv_unescape_formula,
    escape_markdown,
    load_html_catalog_data,
    load_ndjson_catalog_data,
    record_to_dict,
)
from .models import PromptRecord

COMPARE_FIELDS = ("title", "description", "type", "domain", "price")
NUMERIC_COMPARE_FIELDS = frozenset({"price"})
_KIND_FROM_URL_RE = re.compile(r"/(prompt|bundle|app)/([^/?#]+)")
# File extensions load_catalog can read.
CATALOG_SUFFIXES = frozenset(
    {".json", ".ndjson", ".jsonl", ".csv", ".txt", ".md", ".markdown", ".html", ".htm"}
)


@dataclass(frozen=True)
class ChangedRecord:
    previous: dict[str, Any]
    current: dict[str, Any]
    fields: tuple[str, ...]


@dataclass(frozen=True)
class CatalogDiff:
    added: tuple[dict[str, Any], ...]
    removed: tuple[dict[str, Any], ...]
    changed: tuple[ChangedRecord, ...]
    unchanged: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def load_catalog(
    path: Path,
    *,
    csv_safe: bool = False,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load a catalog file into normalized record dicts.

    ``csv_safe`` says a CSV catalog was written with ``--csv-safe``, so its
    escaped cells are restored exactly. Nothing inside a CSV can say so
    reliably, which is why it comes from the caller and defaults to reading
    every cell verbatim.

    A JSON or HTML catalog entry that is not a record (a number, ``null``) is
    skipped, unless ``strict`` is set: then it raises ValueError, so a caller
    that must not lose data notices.
    """
    suffix = path.suffix.lower()
    # A CSV keeps a description's own "\r\n" inside a quoted cell; reading it with
    # universal newlines would turn that into "\n" and report a change that never happened.
    with path.open(encoding="utf-8", newline="" if suffix == ".csv" else None) as handle:
        text = handle.read()
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON catalog must contain a list of records.")
        return _records(data, strict)
    if suffix in {".ndjson", ".jsonl"}:
        return [
            _normalize_record(item)
            for item in load_ndjson_catalog_data(text, strict=strict)
            if isinstance(item, dict)
        ]
    if suffix == ".csv":
        # Strip a BOM from any CSV (Excel adds one), or the first column name
        # would read as "\ufefftitle".
        rows = csv.DictReader(io.StringIO(text.removeprefix(UTF8_BOM)))
        records = []
        for row in rows:
            if None in row:
                # DictReader files the surplus of a row longer than the header
                # under None; it belongs to no column, so the row is malformed.
                raise ValueError(f"CSV line {rows.line_num} has more fields than the header")
            records.append(_normalize_record({
                key: csv_unescape_formula(value or "") if csv_safe else value or ""
                for key, value in row.items()
            }))
        return records
    if suffix in {".html", ".htm"}:
        return _records(load_html_catalog_data(text), strict)
    if suffix == ".txt":
        return [_normalize_record(item) for item in _parse_text_catalog(text)]
    if suffix in {".md", ".markdown"}:
        return [_normalize_record(item) for item in _parse_markdown_catalog(text)]
    raise ValueError(f"Unsupported catalog file extension: {path.suffix}")


def _records(data: list[Any], strict: bool) -> list[dict[str, Any]]:
    if strict:
        for number, item in enumerate(data, 1):
            if not isinstance(item, dict):
                raise ValueError(f"entry {number} of the catalog is not a record")
    return [_normalize_record(item) for item in data if isinstance(item, dict)]


def compare_catalogs(
    previous: list[dict[str, Any]],
    current_records: list[PromptRecord],
) -> CatalogDiff:
    """Compare a loaded catalog against freshly fetched prompt records."""
    return compare_catalog_records(
        previous, [record_to_dict(record) for record in current_records]
    )


def compare_catalog_records(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> CatalogDiff:
    """Compare two catalogs of record dicts, e.g. two files from load_catalog."""
    matches = _pair_records(previous, current)
    used_previous = {index for index in matches if index is not None}
    added: list[dict[str, Any]] = []
    changed: list[ChangedRecord] = []
    unchanged = 0

    for record, previous_index in zip(current, matches, strict=True):
        if previous_index is None:
            added.append(record)
            continue
        previous_record = previous[previous_index]
        changed_fields = _changed_fields(previous_record, record)
        if changed_fields:
            changed.append(
                ChangedRecord(
                    previous=previous_record,
                    current=record,
                    fields=tuple(changed_fields),
                )
            )
        else:
            unchanged += 1

    removed = [
        record
        for index, record in enumerate(previous)
        if index not in used_previous
    ]
    return CatalogDiff(
        added=tuple(added),
        removed=tuple(removed),
        changed=tuple(changed),
        unchanged=unchanged,
    )


def format_diff_report(diff: CatalogDiff, *, markdown: bool = False) -> str:
    """The diff as readable text; ``markdown`` escapes the titles and values it quotes.

    A ``.md`` report is rendered (a GitHub step summary, a pull request), where a
    remote title could otherwise add links, headings, or hide the rest of the report.
    The terminal and the web UI show the plain text.
    """
    text = escape_markdown if markdown else printable
    lines = [
        "# PromptBase Catalog Diff",
        "",
        "Summary:",
        f"- Added: {len(diff.added)}",
        f"- Removed: {len(diff.removed)}",
        f"- Changed: {len(diff.changed)}",
        f"- Unchanged: {diff.unchanged}",
        "",
    ]

    _append_record_section(lines, "Added", diff.added, text)
    _append_changed_section(lines, diff.changed, text)
    _append_record_section(lines, "Removed", diff.removed, text)
    return "\n".join(lines).rstrip() + "\n"


def diff_to_dict(diff: CatalogDiff) -> dict[str, Any]:
    """Return a JSON-serializable form of ``diff`` for automation."""
    return {
        "has_changes": diff.has_changes,
        "summary": {
            "added": len(diff.added),
            "removed": len(diff.removed),
            "changed": len(diff.changed),
            "unchanged": diff.unchanged,
        },
        "added": [_record_ref(record) for record in diff.added],
        "removed": [_record_ref(record) for record in diff.removed],
        "changed": [
            {
                **_record_ref(item.current),
                "fields": {
                    field: {
                        "previous": _json_value(field, item.previous.get(field)),
                        "current": _json_value(field, item.current.get(field)),
                    }
                    for field in item.fields
                },
            }
            for item in diff.changed
        ],
    }


def format_diff_json(diff: CatalogDiff) -> str:
    return json.dumps(diff_to_dict(diff), ensure_ascii=False, indent=2) + "\n"


def write_diff_report(path: Path, diff: CatalogDiff) -> Path:
    """Write the report as JSON for a ``.json`` path, otherwise as Markdown."""
    if path.suffix.lower() == ".json":
        content = format_diff_json(diff)
    else:
        content = format_diff_report(diff, markdown=path.suffix.lower() in {".md", ".markdown"})
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic like the catalogs: a failed write never leaves half a report, and a
    # symbolic link at the path is replaced, not written through.
    _atomic_write_text(path, content)
    return path


def _parse_text_catalog(text: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"(?ms)^\d+\.\s*\nTitle:\s*(?P<title>.*?)\nDescription:\n"
        r"(?P<description>.*?)(?=^\d+\.\s*\nTitle:|\Z)"
    )
    return [
        {
            "title": match.group("title").strip(),
            "description": match.group("description").strip(),
        }
        for match in pattern.finditer(text)
    ]


def _parse_markdown_catalog(text: str) -> list[dict[str, str]]:
    sections = re.split(r"(?m)^## \d+\. (?=.*\n\n- URL:)", text)
    records: list[dict[str, str]] = []
    for section in sections[1:]:
        lines = section.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        metadata: dict[str, str] = {}
        body_lines: list[str] = []
        in_metadata = True
        for line in lines[1:]:
            if in_metadata and line.startswith("- "):
                key, _, value = line[2:].partition(":")
                metadata[key.strip().lower()] = value.strip()
                continue
            if in_metadata and not line.strip():
                # The writer puts one blank line between the metadata list and the
                # description; after it, "- item" lines are the description's own.
                if metadata:
                    in_metadata = False
                continue
            in_metadata = False
            body_lines.append(line)
        records.append(
            {
                "title": title,
                "description": "\n".join(body_lines).strip(),
                "slug": _slug_from_url(metadata.get("url", "")),
                "url": metadata.get("url", ""),
                "type": metadata.get("type", ""),
                "domain": metadata.get("domain", ""),
                "price": metadata.get("price", ""),
            }
        )
    return records


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if "prompt_type" in normalized and "type" not in normalized:
        normalized["type"] = normalized["prompt_type"]
    for key in ("title", "description", "slug"):
        normalized[key] = str(normalized.get(key) or "").strip()
    # Only normalize metadata the source actually has: whether a key is present
    # is how comparison tells "this format does not record the field" (TXT)
    # apart from "the field was cleared".
    for key in ("type", "domain", "url"):
        if key in normalized:
            normalized[key] = str(normalized[key] or "").strip()
    return normalized


def _pair_records(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[int | None]:
    """For each current record, the index of its previous record, or None if it is new.

    Every slug match is made before any title match, so a new listing that happens
    to share a title cannot claim the previous record that another listing matches
    by slug, whatever order the records are in. Each previous record is used once.
    """
    matches: list[int | None] = [None] * len(current)
    used: set[int] = set()
    by_slug: dict[str, list[int]] = {}
    for index, record in enumerate(previous):
        key = _slug_key(record)
        if key:
            by_slug.setdefault(key, []).append(index)
    # A slug should appear once, but a hand-edited catalog can repeat one; identical
    # records are paired first, so a catalog compared with itself is always clean.
    for exact in (True, False):
        for position, record in enumerate(current):
            key = _slug_key(record)
            if matches[position] is not None or not key:
                continue
            for index in by_slug.get(key, ()):
                if index not in used and (
                    not exact or not _changed_fields(previous[index], record)
                ):
                    matches[position] = index
                    used.add(index)
                    break

    by_title: dict[str, list[int]] = {}
    for index, record in enumerate(previous):
        title = _title_key(record)
        if title and index not in used:
            by_title.setdefault(title, []).append(index)
    # Records that repeat a title are paired with an identical old record first, so
    # their order cannot turn two unchanged records into two changes.
    for exact in (True, False):
        for position, record in enumerate(current):
            title = _title_key(record)
            if matches[position] is not None or not title:
                continue
            for index in by_title.get(title, ()):
                if (
                    index not in used
                    and _same_listing_by_title(record, previous[index])
                    and (not exact or not _changed_fields(previous[index], record))
                ):
                    matches[position] = index
                    used.add(index)
                    break

    # A record with neither slug nor title has no identity; pair it with an identical
    # one, or a catalog compared with itself would report it as added and removed.
    for position, record in enumerate(current):
        if matches[position] is not None or _slug_key(record) or _title_key(record):
            continue
        for index, old in enumerate(previous):
            if (index not in used and not _slug_key(old) and not _title_key(old)
                    and not _changed_fields(old, record)):
                matches[position] = index
                used.add(index)
                break
    return matches


def _same_listing_by_title(record: dict[str, Any], previous: dict[str, Any]) -> bool:
    # Two records that both carry a slug are told apart by it; the title is only a
    # fallback for a side that has none (a TXT catalog). An app often shares a
    # prompt's title, so kinds must agree where both are known.
    if _slug_key(record) and _slug_key(previous):
        return False
    return len({_known_kind(record), _known_kind(previous)} - {""}) <= 1


def _changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for field in COMPARE_FIELDS:
        if field not in {"title", "description"}:
            # Nothing recorded before (TXT, or an empty cell): no baseline.
            if _metadata_missing(previous.get(field)):
                continue
            # The current catalog's format does not store the field at all.
            # A field that is present but empty was cleared, and is reported.
            if field not in current:
                continue
        before, after = previous.get(field), current.get(field)
        if field in NUMERIC_COMPARE_FIELDS:
            if _comparable_number(before) != _comparable_number(after):
                changed.append(field)
        # Equal text needs no normalising; descriptions are long, and this is the
        # common case when nothing changed.
        elif not (isinstance(before, str) and before == after) and (
            _comparable_value(before) != _comparable_value(after)
        ):
            changed.append(field)
    return changed


def _comparable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    # str.split() collapses exactly what the regex \s+ did, line breaks included,
    # several times faster on a multi-kilobyte description.
    return " ".join(str(value).split())


def _comparable_number(value: Any) -> str:
    # A CSV catalog stores 2.0 as the text "2.0" while JSON loads it as a
    # float, so compare numerically rather than by spelling.
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return _comparable_value(value)


def _metadata_missing(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _slug_key(record: dict[str, Any]) -> str:
    """A listing's identity: its slug within its kind.

    Slugs are unique only within a kind (a prompt and an app can share one), so a
    bundle is never paired with a prompt of the same slug.
    """
    slug = str(record.get("slug") or "").strip().casefold()
    return f"{_kind(record)}/{slug}" if slug else ""


def _kind(record: dict[str, Any]) -> str:
    # A record with a slug but no kind is a prompt: only bundles and apps store one.
    return _known_kind(record) or "prompt"


def _known_kind(record: dict[str, Any]) -> str:
    """The listing kind a record states, from ``item_type`` or its URL, else ``""``."""
    kind = str(record.get("item_type") or "").strip().lower()
    if kind:
        return kind
    match = _KIND_FROM_URL_RE.search(str(record.get("url") or ""))
    return match.group(1) if match else ""


def _title_key(record: dict[str, Any]) -> str:
    return _comparable_value(record.get("title")).casefold()


def _slug_from_url(url: str) -> str:
    match = _KIND_FROM_URL_RE.search(url)
    return match.group(2) if match else ""


def _append_record_section(
    lines: list[str],
    title: str,
    records: tuple[dict[str, Any], ...],
    text: Callable[[object], str],
) -> None:
    if not records:
        return
    lines.extend([f"## {title}", ""])
    for record in records:
        lines.append(f"- {text(_record_label(record))}")
    lines.append("")


def _append_changed_section(
    lines: list[str],
    records: tuple[ChangedRecord, ...],
    text: Callable[[object], str],
) -> None:
    if not records:
        return
    lines.extend(["## Changed", ""])
    for record in records:
        lines.append(f"- {text(_record_label(record.current))}")
        lines.append(f"  Changed fields: {', '.join(record.fields)}")
        for field in record.fields:
            change = _describe_change(
                field, record.previous.get(field), record.current.get(field)
            )
            lines.append(f"  - {field}: {text(change)}")
    lines.append("")


def _describe_change(field: str, previous: Any, current: Any) -> str:
    if field == "description":
        # Descriptions are long; the size change is enough to spot the edit.
        before = len(_comparable_value(previous))
        after = len(_comparable_value(current))
        return f"{before} -> {after} characters"
    if field in NUMERIC_COMPARE_FIELDS:
        return f"{_comparable_number(previous)} -> {_comparable_number(current)}"
    return f"{_quoted(previous)} -> {_quoted(current)}"


def _quoted(value: Any) -> str:
    text = _comparable_value(value)
    return f'"{text}"' if text else "(empty)"


def _json_value(field: str, value: Any) -> Any:
    if field in NUMERIC_COMPARE_FIELDS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        # json.dumps would emit a bare NaN/Infinity, which is not valid JSON.
        return number if math.isfinite(number) else None
    return "" if value is None else str(value)


def _record_ref(record: dict[str, Any]) -> dict[str, str]:
    return {
        "title": str(record.get("title") or "").strip(),
        "slug": str(record.get("slug") or "").strip(),
        "url": str(record.get("url") or "").strip(),
    }


def _record_label(record: dict[str, Any]) -> str:
    title = str(record.get("title") or "Untitled").strip()
    slug = str(record.get("slug") or "").strip()
    if slug:
        return f"{title} ({slug})"
    return title

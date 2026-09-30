from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .formatting import (
    UTF8_BOM,
    csv_unescape_formula,
    load_html_catalog_data,
    record_to_dict,
)
from .models import PromptRecord

COMPARE_FIELDS = ("title", "description", "type", "domain", "price")
NUMERIC_COMPARE_FIELDS = frozenset({"price"})
# File extensions load_catalog can read.
CATALOG_SUFFIXES = frozenset({".json", ".csv", ".txt", ".md", ".markdown", ".html", ".htm"})

# Whitespace collapse runs once per compared field of every changed-candidate
# record, so compile it once rather than per call.
_WHITESPACE_RE = re.compile(r"\s+")


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


def load_catalog(path: Path, *, csv_safe: bool = False) -> list[dict[str, Any]]:
    """Load a catalog file into normalized record dicts.

    ``csv_safe`` says a CSV catalog was written with ``--csv-safe``, so its
    escaped cells are restored exactly. Nothing inside a CSV can say so
    reliably, which is why it comes from the caller and defaults to reading
    every cell verbatim.
    """
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON catalog must contain a list of records.")
        return [_normalize_record(item) for item in data if isinstance(item, dict)]
    if suffix == ".csv":
        # Strip a BOM from any CSV (Excel adds one), or the first column name
        # would read as "\ufefftitle".
        rows = csv.DictReader(io.StringIO(text.removeprefix(UTF8_BOM)))
        return [
            _normalize_record(
                {
                    key: csv_unescape_formula(value or "") if csv_safe else value or ""
                    for key, value in row.items()
                }
            )
            for row in rows
        ]
    if suffix in {".html", ".htm"}:
        return [
            _normalize_record(item)
            for item in load_html_catalog_data(text)
            if isinstance(item, dict)
        ]
    if suffix == ".txt":
        return [_normalize_record(item) for item in _parse_text_catalog(text)]
    if suffix in {".md", ".markdown"}:
        return [_normalize_record(item) for item in _parse_markdown_catalog(text)]
    raise ValueError(f"Unsupported catalog file extension: {path.suffix}")


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
    previous_by_slug = {
        _slug_key(record): index
        for index, record in enumerate(previous)
        if _slug_key(record)
    }
    previous_by_title = {
        _title_key(record): index
        for index, record in enumerate(previous)
        if _title_key(record)
    }

    used_previous: set[int] = set()
    added: list[dict[str, Any]] = []
    changed: list[ChangedRecord] = []
    unchanged = 0

    for record in current:
        previous_index = _match_previous(record, previous_by_slug, previous_by_title)
        if previous_index is None or previous_index in used_previous:
            added.append(record)
            continue

        used_previous.add(previous_index)
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


def format_diff_report(diff: CatalogDiff) -> str:
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

    _append_record_section(lines, "Added", diff.added)
    _append_changed_section(lines, diff.changed)
    _append_record_section(lines, "Removed", diff.removed)
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
        content = format_diff_report(diff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
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
                continue
            in_metadata = False
            body_lines.append(line)
        records.append(
            {
                "title": title,
                "description": "\n".join(body_lines).strip(),
                "slug": _slug_from_url(metadata.get("url", "")),
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


def _match_previous(
    record: dict[str, Any],
    previous_by_slug: dict[str, int],
    previous_by_title: dict[str, int],
) -> int | None:
    slug = _slug_key(record)
    if slug and slug in previous_by_slug:
        return previous_by_slug[slug]
    title = _title_key(record)
    if title and title in previous_by_title:
        return previous_by_title[title]
    return None


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
        normalize = _comparable_number if field in NUMERIC_COMPARE_FIELDS else _comparable_value
        if normalize(previous.get(field)) != normalize(current.get(field)):
            changed.append(field)
    return changed


def _comparable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return _WHITESPACE_RE.sub(" ", normalized).strip()


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
    return str(record.get("slug") or "").strip().casefold()


def _title_key(record: dict[str, Any]) -> str:
    return _comparable_value(record.get("title")).casefold()


def _slug_from_url(url: str) -> str:
    match = re.search(r"/(?:prompt|bundle|app)/([^/?#]+)", url)
    return match.group(1) if match else ""


def _append_record_section(
    lines: list[str],
    title: str,
    records: tuple[dict[str, Any], ...],
) -> None:
    if not records:
        return
    lines.extend([f"## {title}", ""])
    for record in records:
        lines.append(f"- {_record_label(record)}")
    lines.append("")


def _append_changed_section(lines: list[str], records: tuple[ChangedRecord, ...]) -> None:
    if not records:
        return
    lines.extend(["## Changed", ""])
    for record in records:
        lines.append(f"- {_record_label(record.current)}")
        lines.append(f"  Changed fields: {', '.join(record.fields)}")
        for field in record.fields:
            change = _describe_change(
                field, record.previous.get(field), record.current.get(field)
            )
            lines.append(f"  - {field}: {change}")
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

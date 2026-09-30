from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from .models import EXTRA_FIELDS, ITEM_TYPE_PLURALS, PromptRecord, ms_to_iso_or_none

EXPORT_FORMATS = ("txt", "markdown", "json", "csv", "html", "ndjson")
SORT_OPTIONS = (
    "newest",
    "oldest",
    "title",
    "price",
    "views",
    "sales",
    "downloads",
    "favorites",
    "rating",
)
# CSV column order; must list exactly the keys record_to_dict produces.
RECORD_FIELDS = (
    "title",
    "description",
    "slug",
    "url",
    "type",
    "domain",
    "created",
    "created_iso",
    "price",
    "discount",
    "views",
    "sales",
    "downloads",
    "favorites",
    "rating",
    "reviews",
)
# The output columns behind each extra field, in the order they are written.
EXTRA_FIELD_COLUMNS = {
    "tags": ("tags",),
    "engine": ("engine",),
    "nsfw": ("nsfw",),
    "featured": ("featured",),
    "updated": ("updated", "updated_iso"),
    "last_sale": ("last_sale", "last_sale_iso"),
    "unique_sales": ("unique_sales",),
}
assert tuple(EXTRA_FIELD_COLUMNS) == EXTRA_FIELDS  # noqa: S101 - import-time table check
FORMAT_EXTENSIONS = {
    "txt": "txt",
    "markdown": "md",
    "json": "json",
    "csv": "csv",
    "html": "html",
    "ndjson": "ndjson",
}
# Characters that str.splitlines() (and many line readers) treat as a line
# break although JSON leaves them raw inside a string. An NDJSON record must
# stay on one physical line, so these are written as \u escapes.
_NDJSON_LINE_BREAKS = {
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
    "\u0085": "\\u0085",
}
# Spreadsheet apps evaluate a cell that starts with one of these as a formula
# (CSV/formula injection). --csv-safe prefixes such text cells with "'".
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# A --csv-safe CSV starts with a UTF-8 byte order mark so Excel, the target of
# that mode, opens it as UTF-8. It is not a safe-mode marker: any tool may save
# a CSV with a BOM, so readers strip it from every CSV and learn about safe
# mode only from the caller (--csv-safe).
UTF8_BOM = "\ufeff"
# The HTML export embeds the full records as JSON in this element so the
# catalog can be loaded back for --compare/--update-file.
HTML_DATA_ELEMENT_ID = "promptbase-catalog-data"
_HTML_DATA_RE = re.compile(
    rf'<script type="application/json" id="{HTML_DATA_ELEMENT_ID}">(?P<data>.*?)</script>',
    re.DOTALL,
)


def parse_csv_option(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def parse_extra_fields(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated ``--extra-fields`` value into canonical order.

    ``all`` selects every extra field; ``-`` and ``_`` are interchangeable.
    """
    requested = {name.replace("-", "_") for name in parse_csv_option(value)}
    if "all" in requested:
        return EXTRA_FIELDS
    unknown = sorted(requested - set(EXTRA_FIELDS))
    if unknown:
        raise ValueError(
            f"unknown extra field(s): {', '.join(unknown)}. "
            f"Use all or any of: {', '.join(EXTRA_FIELDS)}"
        )
    return tuple(name for name in EXTRA_FIELDS if name in requested)


def filter_records(records: list[PromptRecord], mode: str) -> list[PromptRecord]:
    if mode == "all":
        return list(records)
    if mode == "text":
        return [record for record in records if record.is_text]
    if mode == "image":
        return [record for record in records if record.is_image]
    raise ValueError(f"Unsupported mode: {mode}")


def filter_records_by_metadata(
    records: list[PromptRecord],
    *,
    domains: set[str] | None = None,
    prompt_types: set[str] | None = None,
    free_only: bool = False,
    paid_only: bool = False,
    min_price: float | None = None,
    max_price: float | None = None,
    since_created: int | None = None,
    until_created: int | None = None,
    min_sales: int | None = None,
    min_rating: float | None = None,
) -> list[PromptRecord]:
    filtered = list(records)
    if domains:
        filtered = [record for record in filtered if record.domain.lower() in domains]
    if prompt_types:
        filtered = [
            record for record in filtered if record.prompt_type.lower() in prompt_types
        ]
    if free_only:
        filtered = [record for record in filtered if record.is_free]
    if paid_only:
        filtered = [record for record in filtered if not record.is_free]
    if min_price is not None:
        filtered = [record for record in filtered if record.price >= min_price]
    if max_price is not None:
        filtered = [record for record in filtered if record.price <= max_price]
    if since_created is not None:
        filtered = [record for record in filtered if record.created >= since_created]
    if until_created is not None:
        filtered = [record for record in filtered if record.created <= until_created]
    if min_sales is not None:
        filtered = [record for record in filtered if record.sales >= min_sales]
    if min_rating is not None:
        filtered = [record for record in filtered if record.rating >= min_rating]
    return filtered


def sort_records(records: list[PromptRecord], sort_by: str) -> list[PromptRecord]:
    if sort_by == "newest":
        return sorted(records, key=lambda record: (record.created, record.slug), reverse=True)
    if sort_by == "oldest":
        return sorted(records, key=lambda record: (record.created, record.slug))
    if sort_by == "title":
        return sorted(records, key=lambda record: record.title.casefold())
    if sort_by == "price":
        return sorted(records, key=lambda record: (record.price, record.created), reverse=True)
    if sort_by in {"views", "sales", "downloads", "favorites", "rating"}:
        return sorted(
            records,
            key=lambda record: (getattr(record, sort_by), record.created),
            reverse=True,
        )
    raise ValueError(f"Unsupported sort option: {sort_by}")


def format_records_as_text(records: list[PromptRecord]) -> str:
    parts: list[str] = []
    for index, record in enumerate(records, 1):
        description = record.description.replace("\r\n", "\n").replace("\r", "\n")
        parts.append(
            f"{index}.\n"
            f"Title: {_single_line(record.title)}\n"
            f"Description:\n"
            f"{description.strip()}\n"
        )
    return "\n".join(parts)


def format_records_as_markdown(
    records: list[PromptRecord],
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> str:
    parts = [f"# PromptBase {item_type.capitalize()} Export", ""]
    for index, record in enumerate(records, 1):
        description = record.description.replace("\r\n", "\n").replace("\r", "\n")
        parts.extend(
            [
                f"## {index}. {_single_line(record.title)}",
                "",
                f"- URL: {record.url}",
                # Empty stays empty: a placeholder word would be ambiguous with
                # a real value of the same spelling when the catalog is diffed.
                f"- Domain: {record.domain}".rstrip(),
                f"- Type: {record.prompt_type}".rstrip(),
                f"- Price: {record.price:g}",
                f"- Created: {record.created_iso or 'unknown'}",
                f"- Views: {record.views}",
                f"- Sales: {record.sales}",
                *_markdown_extra_lines(record, extra_fields),
                "",
                description.strip(),
                "",
            ]
        )
    return "\n".join(parts).rstrip() + "\n"


def _markdown_extra_lines(record: PromptRecord, extra_fields: Sequence[str]) -> list[str]:
    values = record_to_dict(record, extra_fields)
    labels = {
        "tags": "Tags",
        "engine": "Engine",
        "nsfw": "NSFW",
        "featured": "Featured",
        "updated_iso": "Updated",
        "last_sale_iso": "Last sale",
        "unique_sales": "Unique sales",
    }
    lines = []
    for key, label in labels.items():
        if key in values:
            # Empty stays empty, like Domain and Type above.
            lines.append(f"- {label}: {_single_line(_cell_text(values[key]))}".rstrip())
    return lines


def _cell_text(value: object) -> str:
    """Plain text for a record value: lists joined, booleans as true/false, None empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _single_line(value: str) -> str:
    # TXT and Markdown keep the title on one line; an embedded line break would
    # split the record header and break both validation and diff parsing.
    return " ".join(value.split())


def record_to_dict(
    record: PromptRecord,
    extra_fields: Sequence[str] = (),
) -> dict[str, object]:
    data: dict[str, object] = {
        "title": record.title,
        "description": record.description,
        "slug": record.slug,
        "url": record.url,
        "type": record.prompt_type,
        "domain": record.domain,
        "created": record.created,
        "created_iso": record.created_iso,
        "price": record.price,
        "discount": record.discount,
        "views": record.views,
        "sales": record.sales,
        "downloads": record.downloads,
        "favorites": record.favorites,
        "rating": record.rating,
        "reviews": record.reviews,
    }
    extra_values: dict[str, dict[str, object]] = {
        "tags": {"tags": list(record.tags)},
        "engine": {"engine": record.engine},
        "nsfw": {"nsfw": record.nsfw},
        "featured": {"featured": record.featured},
        "updated": {"updated": record.updated, "updated_iso": ms_to_iso_or_none(record.updated)},
        "last_sale": {
            "last_sale": record.last_sale,
            "last_sale_iso": ms_to_iso_or_none(record.last_sale),
        },
        "unique_sales": {"unique_sales": record.unique_sales},
    }
    if record.item_type != "prompt":
        # A prompt catalog keeps its original columns; the other kinds say
        # what they are, since their slugs are only unique within a kind.
        data["item_type"] = record.item_type
    for name in EXTRA_FIELDS:  # canonical order, whatever order was requested
        if name in extra_fields:
            data.update(extra_values[name])
    return data


def format_records_as_json(
    records: list[PromptRecord],
    extra_fields: Sequence[str] = (),
) -> str:
    data = [record_to_dict(record, extra_fields) for record in records]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def format_records_as_ndjson(
    records: list[PromptRecord],
    extra_fields: Sequence[str] = (),
) -> str:
    """One compact JSON object per line (newline-delimited JSON), no enclosing array.

    Unlike a JSON array it can be read and written one record at a time, which
    suits ``jq -c``, ``pandas.read_json(lines=True)``, and warehouse loaders.
    """
    lines = []
    for record in records:
        line = json.dumps(
            record_to_dict(record, extra_fields), ensure_ascii=False, separators=(",", ":")
        )
        for character, escape in _NDJSON_LINE_BREAKS.items():
            line = line.replace(character, escape)
        lines.append(line + "\n")
    return "".join(lines)


def load_ndjson_catalog_data(text: str, *, strict: bool = False) -> list[object]:
    """Parse NDJSON text into its records; blank lines are ignored.

    Splits on newline characters only, never ``splitlines()``. A line that is not valid
    JSON raises ValueError naming the line; one that is valid but not an object
    does too when ``strict`` is set, and is skipped otherwise.
    """
    records: list[object] = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {number} is not valid JSON: {exc}") from exc
        if isinstance(item, dict):
            records.append(item)
        elif strict:
            raise ValueError(f"line {number} is not a record")
    return records


def format_records_as_csv(
    records: list[PromptRecord],
    *,
    safe: bool = False,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> str:
    columns = RECORD_FIELDS + (() if item_type == "prompt" else ("item_type",)) + tuple(
        column
        for name in EXTRA_FIELDS
        if name in extra_fields
        for column in EXTRA_FIELD_COLUMNS[name]
    )
    rows = [_csv_row(record_to_dict(record, extra_fields)) for record in records]
    if safe:
        rows = [
            {key: csv_escape_formula(value) for key, value in row.items()} for row in rows
        ]
    output = io.StringIO()
    if safe:
        output.write(UTF8_BOM)
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _csv_row(row: dict[str, object]) -> dict[str, object]:
    """Flatten the values a CSV cell cannot hold as-is (lists, booleans, None)."""
    return {
        key: _csv_list_cell(value)
        if isinstance(value, (list, tuple))
        else _cell_text(value)
        if isinstance(value, bool) or value is None
        else value
        for key, value in row.items()
    }


def _csv_list_cell(values: Sequence[object]) -> str:
    """Tags as ``a, b, c``, or as a JSON array if a tag contains a comma.

    The joined form is easy to read and to split, but a comma inside a tag
    would make it ambiguous. JSON text (which a reader recognises by its
    brackets) keeps such a cell exactly reversible.
    """
    if any("," in str(value) for value in values):
        return json.dumps([str(value) for value in values], ensure_ascii=False)
    return _cell_text(list(values))


def csv_escape_formula(value: object) -> object:
    """Prefix a text cell that a spreadsheet would run as a formula with ``'``.

    Only strings are touched: numeric cells such as prices are written by the
    exporter itself and cannot carry a formula. A value that already starts
    with apostrophes before a formula character gets one more, so the escape
    stays reversible for text that genuinely begins with ``'=``.
    """
    if isinstance(value, str) and value.lstrip("'").startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_unescape_formula(value: str) -> str:
    """Reverse :func:`csv_escape_formula` for a value known to be escaped."""
    if value.startswith("'") and value.lstrip("'").startswith(CSV_FORMULA_PREFIXES):
        return value[1:]
    return value


_HTML_STYLE = """
:root { color-scheme: light dark; --bg: #f5f7f9; --panel: #fff; --text: #16202a;
  --muted: #5f6b77; --line: #d9e1e8; --accent: #1f7a5c; }
@media (prefers-color-scheme: dark) { :root { --bg: #11171d; --panel: #19222b;
  --text: #f0f4f8; --muted: #adbac7; --line: #34414f; --accent: #5cc49d; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.55;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
main { width: min(920px, calc(100% - 32px)); margin: 32px auto; }
h1 { margin: 0 0 4px; font-size: 30px; }
.summary { margin: 0 0 16px; color: var(--muted); }
input[type=search] { width: 100%; padding: 10px 12px; margin-bottom: 16px; font: inherit;
  color: var(--text); background: var(--panel); border: 1px solid var(--line);
  border-radius: 6px; }
ol { list-style: none; margin: 0; padding: 0; }
.prompt { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px 20px; margin-bottom: 12px; }
.prompt h2 { margin: 0 0 4px; font-size: 19px; }
.prompt a { color: var(--accent); }
.facts { margin: 0 0 10px; color: var(--muted); font-size: 14px; }
.description { white-space: pre-wrap; overflow-wrap: anywhere; }
"""

# Plain DOM filtering over already-rendered, already-escaped markup: no record
# data is ever inserted as HTML, so a hostile description cannot inject script.
_HTML_SCRIPT = """
(function () {
  var box = document.getElementById("filter");
  var items = Array.prototype.slice.call(document.querySelectorAll("li.prompt"));
  var summary = document.getElementById("summary");
  var total = items.length;
  box.hidden = false;
  box.addEventListener("input", function () {
    var needle = box.value.trim().toLowerCase();
    var shown = 0;
    items.forEach(function (item) {
      var hit = !needle || item.textContent.toLowerCase().indexOf(needle) !== -1;
      item.hidden = !hit;
      if (hit) { shown += 1; }
    });
    var noun = summary.getAttribute("data-noun");
    summary.textContent = needle ? shown + " of " + total + " " + noun : total + " " + noun;
  });
})();
"""


def format_records_as_html(
    records: list[PromptRecord],
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> str:
    """Render a self-contained, searchable HTML catalog.

    Every record value is HTML-escaped. The full records are also embedded as
    JSON (with ``<`` escaped so no value can close the script element) so the
    file can be loaded back by the diff tooling.
    """
    items: list[str] = []
    for record in records:
        description = record.description.replace("\r\n", "\n").replace("\r", "\n").strip()
        facts = " · ".join(
            [
                record.domain or "unknown",
                record.prompt_type or "unknown",
                f"price {record.price:g}",
                record.created_iso[:10] if record.created_iso else "unknown date",
                f"{record.views} views",
                f"{record.sales} sales",
                *_html_extra_facts(record, extra_fields),
            ]
        )
        items.append(
            '<li class="prompt"><article>'
            f'<h2><a href="{_h(record.url)}">{_h(_single_line(record.title))}</a></h2>'
            f'<p class="facts">{_h(facts)}</p>'
            f'<div class="description">{_h(description)}</div>'
            "</article></li>"
        )
    data = json.dumps(
        [record_to_dict(record, extra_fields) for record in records], ensure_ascii=False
    ).replace("<", "\\u003c")
    count = len(records)
    noun = ITEM_TYPE_PLURALS[item_type]
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>PromptBase {item_type.capitalize()} Export</title>\n"
        f"<style>{_HTML_STYLE}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>PromptBase {item_type.capitalize()} Export</h1>\n"
        f'<p class="summary" id="summary" data-noun="{noun}">{count} {noun}</p>\n'
        f'<input type="search" id="filter" placeholder="Filter {noun}" '
        f'aria-label="Filter {noun}" hidden>\n'
        '<ol id="prompts">\n' + "\n".join(items) + "\n</ol>\n</main>\n"
        f'<script type="application/json" id="{HTML_DATA_ELEMENT_ID}">{data}</script>\n'
        f"<script>{_HTML_SCRIPT}</script>\n</body>\n</html>\n"
    )


def _html_extra_facts(record: PromptRecord, extra_fields: Sequence[str]) -> list[str]:
    values = record_to_dict(record, extra_fields)
    facts = []
    if values.get("engine"):
        facts.append(f"engine {values['engine']}")
    if values.get("tags"):
        facts.append(f"tags {_cell_text(values['tags'])}")
    # A recorded false or zero is a value, not an absence: show it, so a
    # requested flag is never indistinguishable from one that was not requested.
    if "nsfw" in values:
        facts.append(f"nsfw {_cell_text(values['nsfw'])}")
    if "featured" in values:
        facts.append(f"featured {_cell_text(values['featured'])}")
    if values.get("updated_iso"):
        facts.append(f"updated {str(values['updated_iso'])[:10]}")
    if values.get("last_sale_iso"):
        facts.append(f"last sale {str(values['last_sale_iso'])[:10]}")
    if "unique_sales" in values:
        facts.append(f"{values['unique_sales']} unique sales")
    return facts


def load_html_catalog_data(text: str) -> list[object]:
    """Return the records embedded in an HTML export by :func:`format_records_as_html`."""
    match = _HTML_DATA_RE.search(text)
    if match is None:
        raise ValueError("HTML catalog has no embedded PromptBase catalog data.")
    data = json.loads(match.group("data"))
    if not isinstance(data, list):
        raise ValueError("HTML catalog data must be a list of records.")
    return data


def _h(value: str) -> str:
    return html.escape(value, quote=True)


def format_records(
    records: list[PromptRecord],
    export_format: str,
    *,
    csv_safe: bool = False,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> str:
    if export_format == "txt":
        if extra_fields:
            # TXT holds only a title and a description; dropping the fields
            # silently would hide the mistake.
            raise ValueError("The txt format cannot hold extra fields.")
        return format_records_as_text(records)
    if export_format == "markdown":
        return format_records_as_markdown(records, extra_fields, item_type)
    if export_format == "json":
        return format_records_as_json(records, extra_fields)
    if export_format == "csv":
        return format_records_as_csv(
            records, safe=csv_safe, extra_fields=extra_fields, item_type=item_type
        )
    if export_format == "html":
        return format_records_as_html(records, extra_fields, item_type)
    if export_format == "ndjson":
        return format_records_as_ndjson(records, extra_fields)
    raise ValueError(f"Unsupported export format: {export_format}")


def _safe_username(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", username).strip("_")


def expected_filename(
    username: str,
    mode: str,
    export_format: str = "txt",
    item_type: str = "prompt",
) -> str:
    extension = FORMAT_EXTENSIONS[export_format]
    kind = ITEM_TYPE_PLURALS[item_type]
    return f"{_safe_username(username)}_{mode}_{kind}.{extension}"


def expected_timestamped_filename(
    username: str,
    mode: str,
    export_format: str,
    timestamp: str | None,
    item_type: str = "prompt",
) -> str:
    if not timestamp:
        return expected_filename(username, mode, export_format, item_type)
    extension = FORMAT_EXTENSIONS[export_format]
    kind = ITEM_TYPE_PLURALS[item_type]
    return f"{_safe_username(username)}_{mode}_{kind}_{timestamp}.{extension}"


def write_export(
    output_dir: Path,
    username: str,
    mode: str,
    records: list[PromptRecord],
    export_format: str,
    timestamp: str | None = None,
    overwrite: bool = True,
    *,
    csv_safe: bool = False,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / expected_timestamped_filename(
        username,
        mode,
        export_format,
        timestamp,
        item_type,
    )
    write_export_to_path(
        output_path,
        records,
        export_format,
        overwrite=overwrite,
        csv_safe=csv_safe,
        extra_fields=extra_fields,
        item_type=item_type,
    )
    return output_path


def write_export_to_path(
    output_path: Path,
    records: list[PromptRecord],
    export_format: str,
    *,
    overwrite: bool,
    csv_safe: bool = False,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
) -> Path:
    content = format_records(
        records,
        export_format,
        csv_safe=csv_safe,
        extra_fields=extra_fields,
        item_type=item_type,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        # Exclusive create: no window between an existence check and the write.
        try:
            handle = output_path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            raise FileExistsError(f"Output file already exists: {output_path}") from None
        created = os.fstat(handle.fileno())
        try:
            with handle:
                handle.write(content)
        except BaseException:
            # A truncated leftover would make every retry fail with "already
            # exists". Remove it only if the path is still the file created
            # above, not one another writer has since swapped in.
            try:
                if os.path.samestat(created, os.stat(output_path)):
                    output_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return output_path
    _atomic_write_text(output_path, content)
    return output_path


def _atomic_write_text(path: Path, content: str) -> None:
    # Write beside the target and swap it in, so a failed write (disk full,
    # interrupted run) never leaves a truncated catalog: --update-file
    # rewrites what may be the user's only copy.
    # Not tempfile.mkstemp: it creates the file 0600, which os.replace would
    # carry over to the catalog. Exclusive create keeps the umask default.
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        if path.exists():
            shutil.copymode(path, temp_path)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def infer_format_from_path(path: Path) -> str:
    extension = path.suffix.lower()
    if extension == ".txt":
        return "txt"
    if extension in {".md", ".markdown"}:
        return "markdown"
    if extension == ".json":
        return "json"
    if extension == ".csv":
        return "csv"
    if extension in {".html", ".htm"}:
        return "html"
    if extension in {".ndjson", ".jsonl"}:
        return "ndjson"
    raise ValueError(f"Cannot infer export format from extension: {path.suffix}")


def count_written_records(path: Path, export_format: str) -> int:
    text = path.read_text(encoding="utf-8")
    if export_format == "txt":
        return len(
            re.findall(
                r"(?m)^\d+\.\nTitle: .+\nDescription:\n",
                text,
            )
        )
    if export_format == "markdown":
        # Only count headings that begin a real record (heading followed by a
        # blank line and the metadata block). A bare "## N." line inside a
        # description must not inflate the count, mirroring how the diff parser
        # in diffing._parse_markdown_catalog splits records.
        return len(re.findall(r"^## \d+\. .*\n\n- URL: ", text, flags=re.MULTILINE))
    if export_format == "json":
        return len(json.loads(text))
    if export_format == "csv":
        return sum(1 for _ in csv.DictReader(io.StringIO(text.removeprefix(UTF8_BOM))))
    if export_format == "html":
        # Descriptions are HTML-escaped, so this marker only ever comes from
        # the writer itself. The rendered list and the embedded data must agree.
        rendered = text.count('<li class="prompt">')
        embedded = len(load_html_catalog_data(text))
        if rendered != embedded:
            raise ValueError(
                f"HTML catalog lists {rendered} prompts but embeds {embedded} records"
            )
        return rendered
    if export_format == "ndjson":
        return len(load_ndjson_catalog_data(text, strict=True))
    raise ValueError(f"Unsupported export format: {export_format}")


def sorted_newest_to_oldest(records: list[PromptRecord]) -> bool:
    return all(records[i].created >= records[i + 1].created for i in range(len(records) - 1))

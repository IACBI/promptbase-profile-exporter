from __future__ import annotations

import argparse
import html
import math
import os
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from . import __version__
from .client import PromptBaseError, fetch_prompts
from .console import make_output_safe
from .dates import parse_datetime_ms
from .diffing import (
    CATALOG_SUFFIXES,
    CatalogDiff,
    compare_catalogs,
    format_diff_report,
    load_catalog,
)
from .formatting import (
    EXPORT_FORMATS,
    SORT_OPTIONS,
    count_written_records,
    filter_records,
    parse_csv_option,
    parse_extra_fields,
    sorted_newest_to_oldest,
    write_export,
)
from .layout import LAYOUTS, count_files, unique_names, write_markdown_files
from .models import EXTRA_FIELDS, ITEM_TYPE_PLURALS, ITEM_TYPES, Profile, PromptRecord
from .pipeline import Selection, split_modes, without_description

WEB_MODES = ("split", "all", "text", "image")
PRICE_FILTERS = ("all", "free", "paid")
DEFAULT_OUTPUT_DIR = "exports"
MAX_FORM_BYTES = 20_000

# Files the /download endpoint is allowed to serve, mapped to their content
# type. Restricting to export extensions keeps the endpoint from being used to
# read arbitrary files even inside the working directory.
DOWNLOAD_CONTENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ndjson": "application/x-ndjson; charset=utf-8",
}

# Names that write_export actually produces:
# "<username>_<mode>_<prompts|bundles|apps>[_YYYYMMDD_HHMMSS].<ext>". The /download endpoint
# only serves files matching this, so it cannot disclose unrelated
# supported-extension files (e.g. a stray secrets.json) in the working
# directory, even when the server is exposed with --host 0.0.0.0.
_EXPORT_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+_(?:all|text|image)_(?:prompts|bundles|apps)(?:_\d{8}_\d{6})?"
    r"\.(?:txt|md|json|csv|html|ndjson)$"
)


# ThreadingHTTPServer handles requests concurrently. Two exports of the same
# profile write the same filenames, so the compare/write/validate phase is
# serialized; the slow network fetch before it still runs in parallel.
_EXPORT_LOCK = threading.Lock()


class WebInputError(ValueError):
    """Raised when a submitted web export request is invalid."""


@dataclass(frozen=True)
class ExportRequest:
    profile_input: str
    mode: str = "split"
    output_dir: Path = Path(DEFAULT_OUTPUT_DIR)
    export_format: str = "txt"
    sort: str = "newest"
    domain: str = ""
    prompt_type: str = ""
    price_filter: str = "all"
    min_price: float | None = None
    max_price: float | None = None
    limit: int | None = None
    since: str = ""
    until: str = ""
    since_created: int | None = None
    until_created: int | None = None
    timestamp_filenames: bool = False
    allow_missing_descriptions: bool = False
    min_sales: int | None = None
    min_rating: float | None = None
    csv_safe: bool = False
    compare_csv_safe: bool = False
    compare_file: str = ""
    compare_path: Path | None = None
    extra_fields: tuple[str, ...] = ()
    item_type: str = "prompt"
    layout: str = "catalog"

    def selection(self) -> Selection:
        """The record selection this request asks for."""
        # build_request_config already parses since/until to validate them; reuse
        # those values and only parse here when a request was constructed directly.
        since_created = self.since_created
        if since_created is None and self.since:
            since_created = parse_datetime_ms(self.since, end_of_day=False)
        until_created = self.until_created
        if until_created is None and self.until:
            until_created = parse_datetime_ms(self.until, end_of_day=True)
        return Selection(
            domains=frozenset(parse_csv_option(self.domain)),
            prompt_types=frozenset(parse_csv_option(self.prompt_type)),
            price=self.price_filter,
            min_price=self.min_price,
            max_price=self.max_price,
            since_created=since_created,
            until_created=until_created,
            min_sales=self.min_sales,
            min_rating=self.min_rating,
            sort=self.sort,
            limit=self.limit,
        )


@dataclass(frozen=True)
class ExportedFile:
    mode: str
    path: Path
    count: int


@dataclass(frozen=True)
class WebExportResult:
    username: str
    total_records: int
    selected_records: int
    text_records: int
    image_records: int
    other_records: int
    files: tuple[ExportedFile, ...]
    diff: CatalogDiff | None = None
    item_type: str = "prompt"


# How many matching records the preview lists; the counts above it cover them all.
PREVIEW_ROWS = 100


@dataclass(frozen=True)
class PreviewRow:
    title: str
    url: str
    domain: str
    prompt_type: str
    price: float
    views: int
    sales: int
    created: str


@dataclass(frozen=True)
class WebPreview:
    """What a request would export, without writing anything."""

    username: str
    item_type: str
    total_records: int
    selected_records: int
    text_records: int
    image_records: int
    other_records: int
    without_description: int
    rows: tuple[PreviewRow, ...]


class FetchPrompts(Protocol):
    def __call__(
        self,
        profile_input: str,
        extra_fields: Sequence[str] = (),
        item_type: str = "prompt",
    ) -> tuple[Profile, list[PromptRecord]]: ...


class WriteExport(Protocol):
    def __call__(
        self,
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
    ) -> Path: ...


CountWrittenRecords = Callable[[Path, str], int]
# Writes one Markdown file per record into a folder and returns the folder.
FilesWriter = Callable[..., Path]


def default_request() -> ExportRequest:
    return ExportRequest(profile_input="")


def build_request_config(
    form_data: Mapping[str, str | Sequence[str]],
) -> ExportRequest:
    """Build and validate an export request from web form data."""
    profile_input = _single_value(form_data, "profile").strip()
    if not profile_input:
        raise WebInputError("Profile is required.")

    mode = _single_value(form_data, "mode", "split")
    if mode not in WEB_MODES:
        raise WebInputError(f"Unsupported mode: {mode}.")

    export_format = _single_value(form_data, "format", "txt")
    if export_format not in EXPORT_FORMATS:
        raise WebInputError(f"Unsupported format: {export_format}.")

    item_type = _single_value(form_data, "item_type", "prompt")
    if item_type not in ITEM_TYPES:
        raise WebInputError(f"Unsupported item type: {item_type}.")

    layout = _single_value(form_data, "layout", "catalog")
    if layout not in LAYOUTS:
        raise WebInputError(f"Unsupported layout: {layout}.")

    sort = _single_value(form_data, "sort", "newest")
    if sort not in SORT_OPTIONS:
        raise WebInputError(f"Unsupported sort option: {sort}.")

    price_filter = _single_value(form_data, "price_filter", "all")
    if price_filter not in PRICE_FILTERS:
        raise WebInputError(f"Unsupported price filter: {price_filter}.")

    min_price = _parse_optional_float(form_data, "min_price")
    max_price = _parse_optional_float(form_data, "max_price")
    min_sales = _parse_optional_int(form_data, "min_sales", minimum=0)
    min_rating = _parse_optional_float(form_data, "min_rating")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise WebInputError("Minimum price cannot be greater than maximum price.")
    limit = _parse_optional_int(form_data, "limit", minimum=1)
    since = _single_value(form_data, "since").strip()
    until = _single_value(form_data, "until").strip()
    since_created = _parse_optional_date(since, "since", end_of_day=False)
    until_created = _parse_optional_date(until, "until", end_of_day=True)
    if (
        since_created is not None
        and until_created is not None
        and since_created > until_created
    ):
        raise WebInputError("Since date cannot be later than until date.")

    output_dir = _single_value(form_data, "output_dir", DEFAULT_OUTPUT_DIR).strip()
    if not output_dir:
        output_dir = DEFAULT_OUTPUT_DIR

    csv_safe = _as_bool(_single_value(form_data, "csv_safe"))
    if csv_safe and export_format != "csv":
        raise WebInputError("CSV formula protection requires the csv format.")

    if layout == "files" and export_format != "markdown":
        raise WebInputError("The files layout writes Markdown: choose the markdown format.")

    compare_file = _single_value(form_data, "compare_file").strip()
    if layout == "files" and compare_file:
        raise WebInputError("The files layout writes a folder, so it cannot be compared.")
    compare_path = _resolve_compare_path(compare_file, mode) if compare_file else None
    selected_extras = form_data.get("extra_fields", [])
    try:
        extra_fields = parse_extra_fields(
            selected_extras
            if isinstance(selected_extras, str)
            else ",".join(selected_extras)
        )
    except ValueError as exc:
        raise WebInputError(f"Extra fields: {exc}.") from exc
    if extra_fields and export_format == "txt":
        raise WebInputError("Extra fields need the markdown, json, ndjson, csv, or html format.")
    compare_csv_safe = _as_bool(_single_value(form_data, "compare_csv_safe"))
    if compare_csv_safe and (compare_path is None or compare_path.suffix.lower() != ".csv"):
        raise WebInputError("The protected-catalog option needs a CSV comparison catalog.")

    return ExportRequest(
        profile_input=profile_input,
        mode=mode,
        output_dir=_resolve_output_dir(output_dir),
        export_format=export_format,
        sort=sort,
        domain=_single_value(form_data, "domain").strip(),
        prompt_type=_single_value(form_data, "prompt_type").strip(),
        price_filter=price_filter,
        min_price=min_price,
        max_price=max_price,
        limit=limit,
        since=since,
        until=until,
        since_created=since_created,
        until_created=until_created,
        timestamp_filenames=_as_bool(_single_value(form_data, "timestamp_filenames")),
        allow_missing_descriptions=_as_bool(
            _single_value(form_data, "allow_missing_descriptions")
        ),
        min_sales=min_sales,
        min_rating=min_rating,
        csv_safe=csv_safe,
        compare_csv_safe=compare_csv_safe,
        compare_file=compare_file,
        compare_path=compare_path,
        extra_fields=extra_fields,
        item_type=item_type,
        layout=layout,
    )


def _fetch_selection(
    request: ExportRequest,
    fetcher: FetchPrompts,
) -> tuple[Profile, list[PromptRecord], list[PromptRecord]]:
    """Fetch a profile and apply a request's selection; the steps export and preview share."""
    profile, records = fetcher(
        request.profile_input,
        extra_fields=request.extra_fields,
        item_type=request.item_type,
    )
    kind = ITEM_TYPE_PLURALS[request.item_type]
    if not records:
        raise WebInputError(f"No approved {kind} found for @{profile.username}.")
    if not sorted_newest_to_oldest(records):
        raise WebInputError(f"The {request.item_type} records are not sorted newest to oldest.")

    selected_records = request.selection().apply(records)
    if not selected_records:
        raise WebInputError(f"No {kind} matched the selected filters.")
    return profile, records, selected_records


def run_preview(
    request: ExportRequest,
    *,
    fetcher: FetchPrompts = fetch_prompts,
) -> WebPreview:
    """List what an export request would select, writing nothing."""
    profile, records, selected_records = _fetch_selection(request, fetcher)
    image_count = len(filter_records(selected_records, "image"))
    text_count = len(filter_records(selected_records, "text"))
    return WebPreview(
        username=profile.username,
        item_type=request.item_type,
        total_records=len(records),
        selected_records=len(selected_records),
        text_records=text_count,
        image_records=image_count,
        other_records=len(selected_records) - image_count - text_count,
        without_description=len(without_description(selected_records)),
        rows=tuple(
            PreviewRow(
                title=" ".join(record.title.split()),
                url=record.url,
                domain=record.domain,
                prompt_type=record.prompt_type,
                price=record.price,
                views=record.views,
                sales=record.sales,
                created=record.created_iso[:10],
            )
            for record in selected_records[:PREVIEW_ROWS]
        ),
    )


def run_export(
    request: ExportRequest,
    *,
    fetcher: FetchPrompts = fetch_prompts,
    writer: WriteExport = write_export,
    counter: CountWrittenRecords = count_written_records,
    files_writer: FilesWriter = write_markdown_files,
) -> WebExportResult:
    """Fetch, filter, sort, write, and validate exports for one web request."""
    profile, records, selected_records = _fetch_selection(request, fetcher)

    missing_descriptions = without_description(selected_records)
    if missing_descriptions and not request.allow_missing_descriptions:
        raise WebInputError(
            "Missing descriptions for "
            f"{len(missing_descriptions)} {request.item_type}(s). "
            "Enable partial exports to write these records."
        )

    modes = split_modes(request.mode)
    timestamp = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        if request.timestamp_filenames
        else None
    )

    exported_files: list[ExportedFile] = []
    diff: CatalogDiff | None = None
    with _EXPORT_LOCK:
        # Compare before writing: the export may overwrite the very catalog
        # being compared against.
        if request.compare_path is not None:
            try:
                previous = load_catalog(
                    request.compare_path, csv_safe=request.compare_csv_safe
                )
            except (OSError, ValueError) as exc:
                raise WebInputError(f"Could not load comparison catalog: {exc}") from exc
            diff = compare_catalogs(previous, filter_records(selected_records, modes[0]))
        for mode in modes:
            filtered = filter_records(selected_records, mode)
            if request.layout == "files":
                folder = files_writer(
                    request.output_dir,
                    profile.username,
                    mode,
                    filtered,
                    extra_fields=request.extra_fields,
                    item_type=request.item_type,
                    timestamp=timestamp,
                )
                present = count_files(folder, unique_names(filtered))
                if present != len(filtered):
                    raise WebInputError(
                        f"Validation failed for {folder}: "
                        f"expected {len(filtered)}, wrote {present}."
                    )
                exported_files.append(ExportedFile(mode=mode, path=folder, count=present))
                continue
            output_path = writer(
                request.output_dir,
                profile.username,
                mode,
                filtered,
                request.export_format,
                timestamp,
                csv_safe=request.csv_safe,
                extra_fields=request.extra_fields,
                item_type=request.item_type,
            )
            try:
                written_count = counter(output_path, request.export_format)
            except ValueError as exc:
                raise WebInputError(f"Validation failed for {output_path}: {exc}") from exc
            if written_count != len(filtered):
                raise WebInputError(
                    f"Validation failed for {output_path}: "
                    f"expected {len(filtered)}, wrote {written_count}."
                )
            exported_files.append(
                ExportedFile(mode=mode, path=output_path, count=written_count)
            )

    image_count = len(filter_records(selected_records, "image"))
    text_count = len(filter_records(selected_records, "text"))
    return WebExportResult(
        username=profile.username,
        total_records=len(records),
        selected_records=len(selected_records),
        text_records=text_count,
        image_records=image_count,
        other_records=len(selected_records) - image_count - text_count,
        files=tuple(exported_files),
        diff=diff,
        item_type=request.item_type,
    )


def render_form(
    request: ExportRequest | None = None,
    *,
    result: WebExportResult | None = None,
    preview: WebPreview | None = None,
    error: str | None = None,
) -> str:
    """Render the local web UI as a complete HTML document."""
    request = request or default_request()
    status_block = ""
    if preview:
        status_block = _render_preview(preview)
    elif error:
        status_block = f'<section class="notice error"><h2>Error</h2><p>{_h(error)}</p></section>'
    elif result:
        rows = "\n".join(_render_file_row(item) for item in result.files)
        kind = ITEM_TYPE_PLURALS[result.item_type]
        status_block = f"""
        <section class="notice success">
          <h2>Export complete</h2>
          <p>
            @{_h(result.username)}: {result.selected_records} selected from
            {result.total_records} {kind}. Text: {result.text_records},
            image: {result.image_records}, other: {result.other_records}.
          </p>
          <div class="tablewrap"><table>
            <thead>
              <tr><th>Mode</th><th>{kind.capitalize()}</th><th>File</th><th>Download</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table></div>
          {_render_diff(result.diff)}
        </section>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PromptBase Profile Exporter</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f5f7f9;
      --panel: #ffffff;
      --text: #16202a;
      --muted: #5f6b77;
      --line: #d9e1e8;
      --accent: #1f7a5c;
      --danger: #a33d3d;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #11171d;
        --panel: #19222b;
        --text: #f0f4f8;
        --muted: #adbac7;
        --line: #34414f;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(980px, calc(100% - 32px));
      margin: 32px auto;
    }}
    header {{
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 32px;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 20px;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    form, .notice {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 16px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .full {{
      grid-column: 1 / -1;
    }}
    label {{
      display: block;
      font-weight: 650;
      margin-bottom: 6px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      background: transparent;
      color: var(--text);
      font: inherit;
    }}
    .checkline {{
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 42px;
    }}
    .checkline input {{
      width: auto;
    }}
    fieldset {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      margin: 0;
    }}
    legend {{
      font-weight: 650;
      padding: 0 6px;
    }}
    .checks {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px 18px;
    }}
    .checks label {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 400;
      margin: 0;
    }}
    .checks input {{
      width: auto;
    }}
    .actions {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 18px;
    }}
    button.secondary {{
      background: transparent;
      color: var(--accent);
      border: 1px solid var(--accent);
    }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 10px 16px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .tablewrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
    }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    code {{
      overflow-wrap: anywhere;
    }}
    pre {{
      margin: 14px 0 0;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    h3 {{
      margin: 18px 0 0;
      font-size: 16px;
    }}
    .success {{
      border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
    }}
    .error {{
      border-color: color-mix(in srgb, var(--danger) 55%, var(--line));
    }}
    .error h2 {{
      color: var(--danger);
    }}
    @media (max-width: 720px) {{
      main {{
        width: min(100% - 20px, 980px);
        margin: 18px auto;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .actions {{
        justify-content: stretch;
      }}
      button {{
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>PromptBase Profile Exporter</h1>
      <p>Run local exports without leaving your browser.</p>
    </header>
    {status_block}
    <form method="post" action="/export">
      <div class="grid">
        <div class="full">
          <label for="profile">PromptBase profile</label>
          <input id="profile" name="profile" value="{_h(request.profile_input)}"
            placeholder="https://promptbase.com/profile/acb" required>
        </div>
        <div>
          <label for="item_type">Kind</label>
          {render_select("item_type", ITEM_TYPES, request.item_type)}
        </div>
        <div>
          <label for="layout">Layout</label>
          {render_select("layout", LAYOUTS, request.layout)}
        </div>
        <div>
          <label for="mode">Mode</label>
          {render_select("mode", WEB_MODES, request.mode)}
        </div>
        <div>
          <label for="format">Format</label>
          {render_select("format", EXPORT_FORMATS, request.export_format)}
        </div>
        <div>
          <label for="sort">Sort</label>
          {render_select("sort", SORT_OPTIONS, request.sort)}
        </div>
        <div>
          <label for="price_filter">Price</label>
          {render_select("price_filter", PRICE_FILTERS, request.price_filter)}
        </div>
        <div>
          <label for="domain">Domain filter</label>
          <input id="domain" name="domain" value="{_h(request.domain)}"
            placeholder="text,image">
        </div>
        <div>
          <label for="prompt_type">Type filter</label>
          <input id="prompt_type" name="prompt_type" value="{_h(request.prompt_type)}"
            placeholder="gpt,claude,chatgpt-image">
        </div>
        <div>
          <label for="min_price">Minimum price</label>
          <input id="min_price" name="min_price" inputmode="decimal"
            value="{_h(_format_optional_float(request.min_price))}" placeholder="0">
        </div>
        <div>
          <label for="max_price">Maximum price</label>
          <input id="max_price" name="max_price" inputmode="decimal"
            value="{_h(_format_optional_float(request.max_price))}" placeholder="10">
        </div>
        <div>
          <label for="since">Since</label>
          <input id="since" name="since" value="{_h(request.since)}" placeholder="2026-01-01">
        </div>
        <div>
          <label for="until">Until</label>
          <input id="until" name="until" value="{_h(request.until)}" placeholder="2026-12-31">
        </div>
        <div>
          <label for="min_sales">Minimum sales</label>
          <input id="min_sales" name="min_sales" inputmode="numeric"
            value="{_h(_format_optional_int(request.min_sales))}" placeholder="0">
        </div>
        <div>
          <label for="min_rating">Minimum rating</label>
          <input id="min_rating" name="min_rating" inputmode="decimal"
            value="{_h(_format_optional_float(request.min_rating))}" placeholder="4.5">
        </div>
        <div>
          <label for="limit">Limit</label>
          <input id="limit" name="limit" inputmode="numeric"
            value="{_h(_format_optional_int(request.limit))}" placeholder="50">
        </div>
        <div class="full">
          <label for="output_dir">Output directory</label>
          <input id="output_dir" name="output_dir" value="{_h(str(request.output_dir))}">
        </div>
        <div class="full">
          <label for="compare_file">Compare with existing catalog (optional)</label>
          <input id="compare_file" name="compare_file" value="{_h(request.compare_file)}"
            placeholder="exports/acb_all_prompts.json">
        </div>
        <fieldset class="full">
          <legend>Extra fields (not for txt)</legend>
          <div class="checks">
            {_render_extra_field_checkboxes(request.extra_fields)}
          </div>
        </fieldset>
        <label class="checkline">
          <input type="checkbox" name="timestamp_filenames" value="1"
            {_checked(request.timestamp_filenames)}>
          Timestamp filenames
        </label>
        <label class="checkline">
          <input type="checkbox" name="allow_missing_descriptions" value="1"
            {_checked(request.allow_missing_descriptions)}>
          Allow partial exports
        </label>
        <label class="checkline">
          <input type="checkbox" name="csv_safe" value="1"
            {_checked(request.csv_safe)}>
          CSV formula protection
        </label>
        <label class="checkline">
          <input type="checkbox" name="compare_csv_safe" value="1"
            {_checked(request.compare_csv_safe)}>
          Comparison catalog is a protected CSV
        </label>
      </div>
      <div class="actions">
        <button type="submit" name="action" value="preview" class="secondary">
          Preview matches
        </button>
        <button type="submit" name="action" value="export">Export</button>
      </div>
    </form>
  </main>
</body>
</html>
"""


def _render_extra_field_checkboxes(selected: Sequence[str]) -> str:
    return "\n".join(
        f'<label><input type="checkbox" name="extra_fields" value="{_h(name)}" '
        f'{_checked(name in selected)}> {_h(name)}</label>'
        for name in EXTRA_FIELDS
    )


def _render_preview(preview: WebPreview) -> str:
    kind = ITEM_TYPE_PLURALS[preview.item_type]
    rows = "\n".join(
        "<tr>"
        f'<td><a href="{_h(row.url)}" rel="noopener noreferrer">{_h(row.title)}</a></td>'
        f"<td>{_h(row.domain)}</td><td>{_h(row.prompt_type)}</td>"
        f"<td>{_h(f'{row.price:g}')}</td><td>{row.views}</td><td>{row.sales}</td>"
        f"<td>{_h(row.created)}</td>"
        "</tr>"
        for row in preview.rows
    )
    note = ""
    if preview.selected_records > len(preview.rows):
        note = f"<p>Showing the first {len(preview.rows)} of {preview.selected_records}.</p>"
    warning = ""
    if preview.without_description:
        warning = (
            f"<p>{preview.without_description} of these have no description: an export "
            "stops unless partial exports are allowed.</p>"
        )
    return f"""
        <section class="notice success">
          <h2>Preview</h2>
          <p>
            @{_h(preview.username)}: {preview.selected_records} selected from
            {preview.total_records} {kind}. Text: {preview.text_records},
            image: {preview.image_records}, other: {preview.other_records}.
            Nothing has been written.
          </p>
          {warning}
          <div class="tablewrap"><table>
            <thead>
              <tr><th>Title</th><th>Domain</th><th>Type</th><th>Price</th><th>Views</th>
                <th>Sales</th><th>Created</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table></div>
          {note}
        </section>
        """


def _render_diff(diff: CatalogDiff | None) -> str:
    if diff is None:
        return ""
    return f"<h3>Comparison</h3><pre>{_h(format_diff_report(diff))}</pre>"


def _render_file_row(item: ExportedFile) -> str:
    href = _download_href(item.path)
    if href is None:
        download_cell = "<td>—</td>"
    else:
        download_cell = f'<td><a href="{_h(href)}" download>Download</a></td>'
    return (
        "<tr>"
        f"<td>{_h(item.mode)}</td>"
        f"<td>{item.count}</td>"
        f"<td><code>{_h(str(item.path))}</code></td>"
        f"{download_cell}"
        "</tr>"
    )


def render_select(name: str, options: Sequence[str], selected: str) -> str:
    items = []
    for option in options:
        selected_attr = " selected" if option == selected else ""
        items.append(
            f'<option value="{_h(option)}"{selected_attr}>{_h(option)}</option>'
        )
    return f'<select id="{_h(name)}" name="{_h(name)}">\n' + "\n".join(items) + "\n</select>"


class PromptBaseWebHandler(BaseHTTPRequestHandler):
    server_version = f"PromptBaseProfileExporter/{__version__}"
    # Socket timeout for reading the request: a client that opens a connection
    # and stalls would otherwise pin a server thread indefinitely.
    timeout = 60

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self._send_text("ok\n")
            return
        if path in {"", "/"}:
            self._send_html(render_form())
            return
        if path == "/download":
            self._handle_download()
            return
        self._send_text("not found\n", status=404)

    def _handle_download(self) -> None:
        """Serve a previously generated export file as an attachment.

        Confined to export files inside the server's working directory so the
        endpoint cannot be turned into an arbitrary file read. A DNS-rebinding
        guard rejects requests whose Host header does not name this server.
        """
        host_header = (self.headers.get("Host") or "").strip()
        if host_header and host_header not in self._expected_authorities():
            self._send_text("Host header not recognized.\n", status=403)
            return

        query = urllib.parse.urlparse(self.path).query
        requested = (urllib.parse.parse_qs(query).get("file") or [""])[0]
        if not requested:
            self._send_text("missing file parameter\n", status=400)
            return

        candidate = _confine_to_cwd(requested)
        if candidate is None or not candidate.is_file():
            self._send_text("not found\n", status=404)
            return
        # Only serve files this tool actually exports, not any supported-
        # extension file that happens to be in the working directory.
        if not _EXPORT_FILENAME_RE.match(candidate.name):
            self._send_text("not found\n", status=404)
            return
        content_type = DOWNLOAD_CONTENT_TYPES[candidate.suffix.lower()]
        self._send_download(candidate.read_bytes(), candidate.name, content_type)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/export":
            self._send_text("not found\n", status=404)
            return

        # /export performs outbound fetches and (except for a preview) writes files, so
        # reject cross-origin (CSRF) and rebound-DNS requests before doing any work.
        rejection = self._reject_unsafe_request()
        if rejection is not None:
            self._send_text(f"{rejection}\n", status=403)
            return

        request: ExportRequest | None = None
        try:
            form = self._read_form()
            request = build_request_config(form)
            if _single_value(form, "action", "export") == "preview":
                self._send_html(render_form(request, preview=run_preview(request)))
            else:
                self._send_html(render_form(request, result=run_export(request)))
        except WebInputError as exc:
            self._send_html(render_form(request, error=str(exc)), status=400)
        except PromptBaseError as exc:
            self._send_html(render_form(request, error=str(exc)), status=502)
        except OSError as exc:
            self._send_html(
                render_form(request, error=f"Could not write export: {exc}"),
                status=500,
            )
        except Exception as exc:  # pragma: no cover - last-resort web boundary
            self._send_html(
                render_form(request, error=f"Unexpected error: {exc}"),
                status=500,
            )

    def _expected_authorities(self) -> set[str]:
        """Host:port authorities this server legitimately answers to."""
        server_address = self.server.server_address
        assert isinstance(server_address, tuple)
        host, port = server_address[0], server_address[1]
        names = {_url_host(host)}
        if host in {"127.0.0.1", "0.0.0.0", "::", "::1"}:
            names |= {"127.0.0.1", "localhost", "[::1]"}
        authorities = set(names)
        authorities |= {f"{name}:{port}" for name in names}
        return authorities

    def _reject_unsafe_request(self) -> str | None:
        """Return an error string if the request is cross-origin or rebound.

        Defends /export against CSRF (a page the user visits auto-submitting
        a form to localhost) and DNS rebinding (a hostile domain re-pointed
        at 127.0.0.1). Returns ``None`` when the request is safe to process.
        """
        authorities = self._expected_authorities()

        # DNS-rebinding guard: the Host header must name this server.
        host_header = (self.headers.get("Host") or "").strip()
        if host_header and host_header not in authorities:
            return "Host header not recognized."

        # CSRF guard: trust Sec-Fetch-Site when modern browsers send it,
        # otherwise require the Origin to match. Non-browser clients (no
        # Origin, no Sec-Fetch-Site, no ambient credentials) are allowed.
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site is not None:
            if fetch_site not in {"same-origin", "none"}:
                return "Cross-origin requests are not allowed."
            return None

        origin = self.headers.get("Origin")
        if origin is not None:
            allowed = {f"http://{authority}" for authority in authorities}
            allowed |= {f"https://{authority}" for authority in authorities}
            if origin not in allowed:
                return "Cross-origin requests are not allowed."
        return None

    def _read_form(self) -> Mapping[str, list[str]]:
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise WebInputError("Invalid Content-Length header.") from exc
        # A negative length would make rfile.read() block until the client
        # closes the connection, bypassing the size cap below.
        if content_length < 0:
            raise WebInputError("Invalid Content-Length header.")
        if content_length > MAX_FORM_BYTES:
            raise WebInputError("Submitted form is too large.")
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        return urllib.parse.parse_qs(body, keep_blank_values=True)

    def _send_html(self, body: str, *, status: int = 200) -> None:
        self._send(body, "text/html; charset=utf-8", status=status)

    def _send_text(self, body: str, *, status: int = 200) -> None:
        self._send(body, "text/plain; charset=utf-8", status=status)

    def _send_download(self, data: bytes, filename: str, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # Force a download rather than inline rendering; the quoted filename
        # keeps any unusual characters from breaking the header.
        disposition = f'attachment; filename="{_content_disposition_name(filename)}"'
        self.send_header("Content-Disposition", disposition)
        # HTML exports are served too; should a browser ever render one
        # inline, a sandbox keeps its script from running on this origin.
        self.send_header("Content-Security-Policy", "sandbox")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, body: str, content_type: str, *, status: int) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # The UI uses only inline CSS and a same-origin form; deny everything
        # else so a future markup mistake cannot load active content.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'",
        )
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)


# An empty host is deliberately absent: socket.bind treats "" as INADDR_ANY,
# i.e. every interface, which is exactly the exposure the warning is for.
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _warn_if_exposed(host: str) -> None:
    """Warn when binding somewhere other than loopback.

    The /export endpoint is unauthenticated, writes files, and makes outbound
    requests. On a non-loopback bind it becomes reachable by other hosts, so
    make the exposure explicit rather than silent.
    """
    if host in LOOPBACK_HOSTS:
        return
    print(
        f"WARNING: binding to {host!r} exposes the unauthenticated web UI "
        "(which writes files and fetches remote data) to other hosts. "
        "Only do this on a trusted network.",
        file=sys.stderr,
    )


class _IPv6HTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _make_server(host: str, port: int) -> ThreadingHTTPServer:
    # ThreadingHTTPServer is IPv4-only; binding "::1" on it fails to resolve.
    if ":" not in host:
        return ThreadingHTTPServer((host, port), PromptBaseWebHandler)
    return _IPv6HTTPServer(_ipv6_bind_address(host, port), PromptBaseWebHandler)


def _ipv6_bind_address(host: str, port: int) -> tuple[Any, ...]:
    """Return the full IPv6 sockaddr for ``host``, keeping any ``%scope``.

    socket.bind() with a (host, port) pair resets the scope id to 0, so a
    link-local literal such as ``fe80::1%eth0`` would fail to bind.
    """
    info = socket.getaddrinfo(
        host, port, socket.AF_INET6, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST
    )
    # (address, port, flowinfo, scope_id) for AF_INET6.
    return tuple(info[0][4])


def _url_host(host: str) -> str:
    """Return ``host`` as it appears in a URL or Host header (IPv6 bracketed)."""
    return f"[{host}]" if ":" in host else host


def browser_url(host: str, port: int) -> str:
    """The address to open for a server bound to ``host``.

    Binding to every interface ("" or 0.0.0.0) is reached through loopback.
    """
    reachable = "127.0.0.1" if host in {"", "0.0.0.0"} else host
    return f"http://{_url_host(reachable)}:{port}/"


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = False) -> None:
    _warn_if_exposed(host)
    with _make_server(host, port) as server:
        # The port actually bound: --port 0 asks the system for a free one.
        bound_port = int(server.server_address[1])
        print(f"Serving PromptBase Profile Exporter at http://{_url_host(host)}:{bound_port}/")
        if open_browser:
            # The socket is already listening, so the page loads as soon as the browser
            # asks. Opened on its own thread: a browser that blocks until it exits (a
            # terminal browser set in $BROWSER) would otherwise keep the server from
            # ever starting to answer it.
            url = browser_url(host, bound_port)
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="promptbase-export-web",
        description="Start the local PromptBase Profile Exporter web UI.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the page in your browser.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    make_output_safe()
    args = parser.parse_args(argv)

    try:
        serve(args.host, args.port, open_browser=args.open)
    except KeyboardInterrupt:
        print("\nServer stopped.", file=sys.stderr)
    return 0


def _parse_optional_date(value: str, name: str, *, end_of_day: bool) -> int | None:
    """Parse an optional ISO date, surfacing bad input as a 400, not a 500."""
    if not value:
        return None
    try:
        return parse_datetime_ms(value, end_of_day=end_of_day)
    except ValueError as exc:
        raise WebInputError(f"{name.title()} date is invalid: {exc}") from exc


def _resolve_compare_path(raw: str, mode: str) -> Path:
    """Validate a web-submitted comparison catalog: a readable file in the cwd."""
    if mode == "split":
        raise WebInputError("Comparing requires mode all, text, or image.")
    path = _confine_to_cwd(raw)
    if path is None:
        raise WebInputError(
            "Comparison catalog must be inside the server's working directory."
        )
    if path.suffix.lower() not in CATALOG_SUFFIXES:
        raise WebInputError(
            "Comparison catalog must be a JSON, CSV, TXT, Markdown, or HTML file."
        )
    if not path.is_file():
        raise WebInputError("Comparison catalog not found.")
    return path


def _resolve_output_dir(raw: str) -> Path:
    """Confine a web-submitted output directory to the server's working tree.

    Unlike the CLI (which trusts the local user with arbitrary paths), the web
    form is reachable by any page the user's browser visits, so absolute paths
    and ``..`` traversal that would escape the working directory are rejected.
    """
    resolved = _confine_to_cwd(Path(raw).expanduser())
    if resolved is None:
        raise WebInputError(
            "Output directory must stay within the server's working directory."
        )
    return resolved


def _confine_to_cwd(raw: str | Path) -> Path | None:
    """Resolve ``raw`` inside the working directory, or ``None`` if it escapes.

    Containment is checked lexically before ``resolve()`` touches the
    filesystem: on Windows, resolving a UNC path such as ``//host/share/x``
    connects to that host over SMB, which would hand the user's NTLM
    credentials to any server a web page chose to name.
    """
    if "\0" in str(raw):
        return None
    base = Path.cwd().resolve()
    joined = base / raw
    if not Path(os.path.abspath(joined)).is_relative_to(base):
        return None
    resolved = joined.resolve()
    return resolved if resolved.is_relative_to(base) else None


def _single_value(
    data: Mapping[str, str | Sequence[str]],
    name: str,
    default: str = "",
) -> str:
    value = data.get(name, default)
    if isinstance(value, str):
        return value
    if not value:
        return default
    return str(value[0])


def _parse_optional_float(
    data: Mapping[str, str | Sequence[str]],
    name: str,
) -> float | None:
    raw = _single_value(data, name).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise WebInputError(f"{name.replace('_', ' ').title()} must be a number.") from exc
    # float() accepts "nan" and "inf"; NaN compares false against every value
    # and would silently filter out all records.
    if not math.isfinite(value):
        raise WebInputError(f"{name.replace('_', ' ').title()} must be a number.")
    if value < 0:
        raise WebInputError(f"{name.replace('_', ' ').title()} cannot be negative.")
    return value


def _parse_optional_int(
    data: Mapping[str, str | Sequence[str]],
    name: str,
    *,
    minimum: int,
) -> int | None:
    raw = _single_value(data, name).strip()
    if not raw:
        return None
    label = name.replace("_", " ").title()
    try:
        value = int(raw)
    except ValueError as exc:
        raise WebInputError(f"{label} must be an integer.") from exc
    if value < minimum:
        if minimum == 1:
            raise WebInputError(f"{label} must be greater than zero.")
        raise WebInputError(f"{label} cannot be less than {minimum}.")
    return value


def _as_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _checked(value: bool) -> str:
    return "checked" if value else ""


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _content_disposition_name(filename: str) -> str:
    """Sanitize a filename for a quoted Content-Disposition header value."""
    cleaned = filename.replace("\\", "_").replace('"', "_")
    cleaned = cleaned.replace("\r", "").replace("\n", "")
    return cleaned or "export"


def _download_href(path: Path) -> str | None:
    """Build a /download link for a file inside the working directory.

    A folder (the files layout) has no download: it is on disk in the output
    directory, and /download serves single export files only.
    """
    if path.is_dir():
        return None
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return None
    return "/download?file=" + urllib.parse.quote(relative.as_posix())


if __name__ == "__main__":
    raise SystemExit(main())

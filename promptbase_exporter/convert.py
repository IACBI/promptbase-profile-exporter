"""``pb-convert``: rewrite a saved catalog in another format, offline."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .console import make_output_safe
from .diffing import load_catalog
from .formatting import (
    EXPORT_FORMATS,
    EXTRA_FIELD_COLUMNS,
    FORMAT_EXTENSIONS,
    _storable,
    count_records_in_text,
    format_records,
    infer_format_from_path,
    write_rendered,
)
from .models import EXTRA_FIELDS, ITEM_TYPES, PromptRecord

EXIT_SUCCESS = 0
EXIT_ERROR = 1

# Only formats that keep every field can be converted. A TXT or Markdown
# catalog lacks columns such as downloads and rating, and filling them in with
# zeros would invent data, so they are refused rather than guessed at.
LOSSLESS_SUFFIXES = frozenset({".json", ".ndjson", ".jsonl", ".csv", ".html", ".htm"})

# The columns a catalog must have, beyond the derived url and created_iso.
REQUIRED_COLUMNS = (
    "title",
    "description",
    "slug",
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
    "reviews",
)
_URL_KIND_RE = re.compile(r"^https?://promptbase\.com/(prompt|bundle|app)/")
_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


def record_from_dict(data: Mapping[str, Any]) -> PromptRecord:
    """Build a record from a loaded catalog row (JSON values or CSV text).

    Raises ValueError for a missing or unreadable required value rather than
    substituting a default, so a conversion never invents data.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in data]
    if missing:
        raise ValueError(f"catalog has no {', '.join(missing)} column(s)")
    return PromptRecord(
        title=_text(data["title"]),
        description=_text(data["description"]),
        slug=_text(data["slug"]),
        prompt_type=_text(data["type"]),
        domain=_text(data["domain"]).lower(),
        created=_required_int(data, "created"),
        price=_required_float(data, "price"),
        discount=_required_float(data, "discount"),
        views=_required_int(data, "views"),
        sales=_required_int(data, "sales"),
        downloads=_required_int(data, "downloads"),
        favorites=_required_int(data, "favorites"),
        rating=_required_float(data, "rating"),
        reviews=_required_int(data, "reviews"),
        tags=_tags(data.get("tags")),
        engine=_text(data.get("engine")),
        nsfw=_flag(data, "nsfw"),
        featured=_flag(data, "featured"),
        updated=_optional_int(data, "updated"),
        last_sale=_optional_int(data, "last_sale"),
        unique_sales=_optional_int(data, "unique_sales") or 0,
        item_type=_item_type(data),
    )


def present_extra_fields(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """The extra fields whose columns the catalog carries, in canonical order."""
    return tuple(
        name
        for name in EXTRA_FIELDS
        if any(EXTRA_FIELD_COLUMNS[name][0] in row for row in rows)
    )


def convert_catalog(
    source: Path,
    destination: Path,
    export_format: str,
    *,
    overwrite: bool = False,
    csv_safe: bool = False,
    from_csv_safe: bool = False,
) -> int:
    """Rewrite ``source`` as ``destination`` in ``export_format``; return the record count."""
    if source.suffix.lower() not in LOSSLESS_SUFFIXES:
        raise ValueError(
            f"cannot convert a {source.suffix or 'extensionless'} catalog: it does not keep "
            "every field, so the missing values would have to be invented. Convert a JSON, "
            "CSV, or HTML catalog, or export again from PromptBase"
        )
    if destination.resolve() == source.resolve():
        raise ValueError("the output file is the source file; choose another --output-file")
    rows = load_catalog(source, csv_safe=from_csv_safe, strict=True)
    if not rows:
        raise ValueError("the catalog has no records")
    records = []
    for number, row in enumerate(rows, 1):
        try:
            records.append(record_from_dict(row))
        except ValueError as exc:
            raise ValueError(f"record {number}: {exc}") from exc
    kinds = {record.item_type for record in records}
    if len(kinds) > 1:
        raise ValueError(f"the catalog mixes kinds of listing: {', '.join(sorted(kinds))}")
    item_type = kinds.pop()
    # TXT keeps only a title and a description, so converting to it drops every
    # other field (price and the rest as well), extra fields included.
    extra_fields = () if export_format == "txt" else present_extra_fields(rows)
    _check_extra_values(rows, extra_fields)
    content = format_records(
        records,
        export_format,
        csv_safe=csv_safe,
        extra_fields=extra_fields,
        item_type=item_type,
    )
    # Check the exact text that will be written before touching the destination:
    # with --overwrite, a failed check after the write would already have replaced
    # the file it was meant to protect.
    written = count_records_in_text(_storable(content), export_format)
    if written != len(records):
        raise ValueError(
            f"validation failed for {destination}: expected {len(records)}, wrote {written}"
        )
    write_rendered(destination, content, overwrite=overwrite)
    return written


# Extra fields whose value is always recorded, so an empty cell is damage, not
# "unknown". updated and last_sale may legitimately be empty; tags and engine
# may legitimately be empty strings.
_ALWAYS_RECORDED = ("nsfw", "featured", "unique_sales")


def _check_extra_values(rows: Sequence[Mapping[str, Any]], extra_fields: Sequence[str]) -> None:
    """Require every detected extra field on every record, instead of defaulting it."""
    for name in extra_fields:
        column = EXTRA_FIELD_COLUMNS[name][0]
        for number, row in enumerate(rows, 1):
            if column not in row:
                raise ValueError(
                    f"record {number}: has no {column} column, which other records have"
                )
            if name in _ALWAYS_RECORDED and (row[column] is None or row[column] == ""):
                raise ValueError(f"record {number}: {column} is empty")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptbase-convert",
        description=(
            "Rewrite a saved PromptBase catalog in another format without fetching "
            "anything. Reads JSON, CSV, and HTML catalogs, which keep every field."
        ),
    )
    parser.add_argument("source", type=Path, help="The catalog to convert.")
    parser.add_argument(
        "-f",
        "--format",
        choices=EXPORT_FORMATS,
        help="Output format. Inferred from --output-file when that is given.",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        type=Path,
        help="Where to write. Defaults to the source path with the new format's extension.",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Allow --output-file to replace a file."
    )
    parser.add_argument(
        "--csv-safe",
        action="store_true",
        help="Protect CSV text cells from spreadsheet formulas. Requires CSV output.",
    )
    parser.add_argument(
        "--from-csv-safe",
        action="store_true",
        help="The source is a CSV written with --csv-safe; restore its escaped cells.",
    )
    parser.add_argument("--quiet", action="store_true", help="Do not print the result.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def resolve_target(args: argparse.Namespace) -> tuple[Path, str]:
    """Work out the output path and format from the arguments, or raise ValueError."""
    if args.output_file is None and args.format is None:
        raise ValueError("give --format, --output-file, or both")
    export_format = args.format or infer_format_from_path(args.output_file)
    destination = args.output_file or args.source.with_suffix(
        f".{FORMAT_EXTENSIONS[export_format]}"
    )
    if args.csv_safe and export_format != "csv":
        raise ValueError("--csv-safe requires CSV output")
    if args.from_csv_safe and args.source.suffix.lower() != ".csv":
        raise ValueError("--from-csv-safe requires a CSV source")
    return destination, export_format


def main(
    argv: Sequence[str] | None = None,
    *,
    converter: Callable[..., int] = convert_catalog,
) -> int:
    """Entry point for ``pb-convert``."""
    make_output_safe()
    args = build_parser().parse_args(argv)
    try:
        destination, export_format = resolve_target(args)
        count = converter(
            args.source,
            destination,
            export_format,
            overwrite=args.overwrite,
            csv_safe=args.csv_safe,
            from_csv_safe=args.from_csv_safe,
        )
    except FileExistsError as exc:
        print(f"error: {exc}. Use --overwrite to replace it.", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not args.quiet:
        print(f"Converted {count} record(s): {args.source} -> {destination}")
    return EXIT_SUCCESS


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    try:
        return int(value)
    except (TypeError, ValueError):
        number = float(value)  # "12.0" in a CSV; raises ValueError if not numeric
        if not number.is_integer():
            raise ValueError(f"expected a whole number, got {value!r}") from None
        return int(number)


def _required_int(data: Mapping[str, Any], column: str) -> int:
    value = data[column]
    if value is None or value == "":
        raise ValueError(f"{column} is empty")
    try:
        return _as_int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} is not a whole number: {value!r}") from exc


def _required_float(data: Mapping[str, Any], column: str) -> float:
    value = data[column]
    if value is None or value == "":
        raise ValueError(f"{column} is empty")
    if isinstance(value, bool):
        raise ValueError(f"{column} is not a number: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} is not a number: {value!r}") from exc


def _optional_int(data: Mapping[str, Any], column: str) -> int | None:
    value = data.get(column)
    if value is None or value == "":
        return None
    try:
        return _as_int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column} is not a whole number: {value!r}") from exc


def _flag(data: Mapping[str, Any], column: str) -> bool:
    value = data.get(column)
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"{column} is not true or false: {value!r}")


def _tags(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        items = [str(tag).strip() for tag in value]
    elif isinstance(value, str):
        items = _split_tag_cell(value)
    else:
        return ()
    return tuple(tag for tag in items if tag)


def _split_tag_cell(cell: str) -> list[str]:
    """Read a CSV tag cell: a JSON array (written when a tag holds a comma), else ``a, b``."""
    stripped = cell.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(tag, str) for tag in parsed):
            return [tag.strip() for tag in parsed]
    return [part.strip() for part in cell.split(",")]


def _item_type(data: Mapping[str, Any]) -> str:
    declared = _text(data.get("item_type"))
    if declared:
        if declared not in ITEM_TYPES:
            raise ValueError(f"unknown item_type {declared!r}")
        return declared
    match = _URL_KIND_RE.match(_text(data.get("url")))
    return match.group(1) if match else "prompt"



if __name__ == "__main__":
    raise SystemExit(main())

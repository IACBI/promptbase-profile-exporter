from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .models import PromptRecord

EXPORT_FORMATS = ("txt", "markdown", "json", "csv")
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
FORMAT_EXTENSIONS = {
    "txt": "txt",
    "markdown": "md",
    "json": "json",
    "csv": "csv",
}


def parse_csv_option(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower() for part in value.split(",") if part.strip()}


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
            f"Title: {record.title}\n"
            f"Description:\n"
            f"{description.strip()}\n"
        )
    return "\n".join(parts)


def format_records_as_markdown(records: list[PromptRecord]) -> str:
    parts = ["# PromptBase Prompt Export", ""]
    for index, record in enumerate(records, 1):
        description = record.description.replace("\r\n", "\n").replace("\r", "\n")
        parts.extend(
            [
                f"## {index}. {record.title}",
                "",
                f"- URL: {record.url}",
                f"- Domain: {record.domain or 'unknown'}",
                f"- Type: {record.prompt_type or 'unknown'}",
                f"- Price: {record.price:g}",
                f"- Created: {record.created_iso or 'unknown'}",
                f"- Views: {record.views}",
                f"- Sales: {record.sales}",
                "",
                description.strip(),
                "",
            ]
        )
    return "\n".join(parts).rstrip() + "\n"


def record_to_dict(record: PromptRecord) -> dict[str, object]:
    return {
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


def format_records_as_json(records: list[PromptRecord]) -> str:
    data = [record_to_dict(record) for record in records]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def format_records_as_csv(records: list[PromptRecord]) -> str:
    fieldnames = [
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
    ]
    rows = [record_to_dict(record) for record in records]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def format_records(records: list[PromptRecord], export_format: str) -> str:
    if export_format == "txt":
        return format_records_as_text(records)
    if export_format == "markdown":
        return format_records_as_markdown(records)
    if export_format == "json":
        return format_records_as_json(records)
    if export_format == "csv":
        return format_records_as_csv(records)
    raise ValueError(f"Unsupported export format: {export_format}")


def _safe_username(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", username).strip("_")


def expected_filename(username: str, mode: str, export_format: str = "txt") -> str:
    extension = FORMAT_EXTENSIONS[export_format]
    return f"{_safe_username(username)}_{mode}_prompts.{extension}"


def expected_timestamped_filename(
    username: str,
    mode: str,
    export_format: str,
    timestamp: str | None,
) -> str:
    if not timestamp:
        return expected_filename(username, mode, export_format)
    extension = FORMAT_EXTENSIONS[export_format]
    return f"{_safe_username(username)}_{mode}_prompts_{timestamp}.{extension}"


def write_export(
    output_dir: Path,
    username: str,
    mode: str,
    records: list[PromptRecord],
    export_format: str,
    timestamp: str | None = None,
    overwrite: bool = True,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / expected_timestamped_filename(
        username,
        mode,
        export_format,
        timestamp,
    )
    write_export_to_path(output_path, records, export_format, overwrite=overwrite)
    return output_path


def write_export_to_path(
    output_path: Path,
    records: list[PromptRecord],
    export_format: str,
    *,
    overwrite: bool,
) -> Path:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_records(records, export_format), encoding="utf-8", newline="\n")
    return output_path


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
        return sum(1 for _ in csv.DictReader(io.StringIO(text)))
    raise ValueError(f"Unsupported export format: {export_format}")


def sorted_newest_to_oldest(records: list[PromptRecord]) -> bool:
    return all(records[i].created >= records[i + 1].created for i in range(len(records) - 1))

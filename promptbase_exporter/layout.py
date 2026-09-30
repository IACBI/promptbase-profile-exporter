"""The ``--layout files`` output: one Markdown file per prompt.

Each file starts with YAML front matter holding the record's fields, then the
description as the body, which suits note tools (Obsidian, Notion import, a
Git-based wiki) that index front matter. The front matter is written with JSON
scalars, which are valid YAML, so every string is quoted and escaped and a value
such as ``"yes"`` or ``"2026-01-01"`` can never be read back as a boolean or a
date.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from pathlib import Path

from .formatting import (
    EXPORT_FORMATS,
    FORMAT_EXTENSIONS,
    _atomic_write_text,
    _safe_username,
    record_to_dict,
)
from .models import ITEM_TYPE_PLURALS, PromptRecord

LAYOUTS = ("catalog", "files")
FILES_FORMAT = "markdown"
assert FILES_FORMAT in EXPORT_FORMATS

# Device names Windows refuses as a file name, with or without an extension.
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_STEM_LENGTH = 100


def safe_stem(slug: str) -> str:
    """A file name stem made only of letters, digits, ``.``, ``_``, and ``-``.

    The slug comes from a remote service, so it is never trusted as a path: a
    separator or ``..`` cannot survive, a leading dot (a hidden file) is dropped,
    and a name Windows reserves gets a suffix.
    """
    stem = _UNSAFE.sub("_", slug).strip("._-")[:MAX_STEM_LENGTH].strip("._-") or "untitled"
    if stem.upper().split(".")[0] in _WINDOWS_RESERVED:
        stem += "_"
    return stem


def unique_names(records: Sequence[PromptRecord]) -> list[str]:
    """One file name per record, distinct even on a case-insensitive file system."""
    used: set[str] = set()
    names = []
    for record in records:
        base = safe_stem(record.slug)
        candidate, number = base, 1
        while candidate.casefold() in used:
            number += 1
            candidate = f"{base}-{number}"
        used.add(candidate.casefold())
        names.append(f"{candidate}.{FORMAT_EXTENSIONS[FILES_FORMAT]}")
    return names


def _scalar(value: object) -> str:
    """A front matter value: JSON text, which YAML reads back as the same value."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        text = repr(value)
        # YAML 1.1 readers want a dot in an exponent form: 1e-05 becomes 1.0e-05.
        if "e" in text and "." not in text.split("e")[0]:
            mantissa, exponent = text.split("e")
            text = f"{mantissa}.0e{exponent}"
        return text
    return json.dumps(value, ensure_ascii=False)


def render_file(
    record: PromptRecord,
    extra_fields: Sequence[str] = (),
) -> str:
    """The Markdown for one prompt: front matter, a heading, then the description."""
    fields = record_to_dict(record, extra_fields)
    fields.pop("description")
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(_scalar(item) for item in value) + "]"
        else:
            rendered = _scalar(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    title = " ".join(record.title.split())
    description = record.description.replace("\r\n", "\n").replace("\r", "\n").strip()
    return "\n".join(lines) + f"\n\n# {title}\n\n{description}\n"


def folder_name(
    username: str,
    mode: str,
    item_type: str = "prompt",
    timestamp: str | None = None,
) -> str:
    """The directory a catalog's files go in: ``<user>_<mode>_<kind>[_<timestamp>]``."""
    name = f"{_safe_username(username)}_{mode}_{ITEM_TYPE_PLURALS[item_type]}"
    return f"{name}_{timestamp}" if timestamp else name


def write_markdown_files(
    output_dir: Path,
    username: str,
    mode: str,
    records: Sequence[PromptRecord],
    *,
    extra_fields: Sequence[str] = (),
    item_type: str = "prompt",
    timestamp: str | None = None,
) -> Path:
    """Write one ``.md`` file per record into its own directory; return the directory.

    Files of the same name are replaced, each atomically. Nothing is deleted: a
    file left from an earlier run for a prompt that no longer exists stays, since
    the directory may hold notes of your own.
    """
    directory = output_dir / folder_name(username, mode, item_type, timestamp)
    directory.mkdir(parents=True, exist_ok=True)
    for record, name in zip(records, unique_names(records), strict=True):
        _atomic_write_text(directory / name, render_file(record, extra_fields))
    return directory


def count_files(directory: Path, names: Sequence[str]) -> int:
    """How many of the expected files exist, are regular files, and are not empty."""
    return sum(
        1
        for name in names
        if (directory / name).is_file() and (directory / name).stat().st_size > 0
    )

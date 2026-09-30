from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .client import PromptBaseError, fetch_prompts
from .console import make_output_safe
from .dates import parse_datetime_ms
from .diffing import (
    CatalogDiff,
    compare_catalog_records,
    compare_catalogs,
    format_diff_report,
    load_catalog,
    write_diff_report,
)
from .formatting import (
    EXPORT_FORMATS,
    SORT_OPTIONS,
    count_written_records,
    filter_records,
    infer_format_from_path,
    parse_csv_option,
    parse_extra_fields,
    sorted_newest_to_oldest,
    write_export,
    write_export_to_path,
)
from .layout import LAYOUTS, count_files, unique_names, write_markdown_files
from .models import EXTRA_FIELDS, ITEM_TYPE_PLURALS, ITEM_TYPES, PromptRecord
from .pipeline import Selection, split_modes, without_description

MODE_ALIASES = {
    "text-only": "text",
    "image-only": "image",
}
MODES = ("split", "all", "text", "image", *MODE_ALIASES)

# Process exit codes. These must stay distinguishable: EXIT_ERROR signals an
# operational failure (bad catalog load, unwritable output) that must never
# fall through to overwriting the existing catalog, whereas EXIT_DIFF is the
# intentional --fail-on-diff signal that the --update-file path tolerates.
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_DIFF = 2


@dataclass(frozen=True)
class RunOptions:
    """The validated, derived form of the command line that export_profile needs."""

    profiles: tuple[str, ...]
    selection: Selection
    export_format: str
    extra_fields: tuple[str, ...]
    output_file: Path | None
    compare_path: Path | None
    overwrite_output: bool
    layout: str = "catalog"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptbase-export",
        description="Export public PromptBase profile prompts to catalog files.",
    )
    parser.add_argument(
        "profiles",
        nargs="+",
        metavar="profile",
        help=(
            "PromptBase profile URL, path, username, or @username. Pass several to "
            "export each profile in one run."
        ),
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=MODES,
        default="split",
        help=(
            "Which export to write. 'split' writes all, text, and image files. "
            "Aliases: text-only, image-only."
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="exports",
        help="Directory where export files will be written.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=EXPORT_FORMATS,
        help="Output file format. Defaults to txt, or inferred from --output-file/--update-file.",
    )
    parser.add_argument(
        "--item-type",
        choices=ITEM_TYPES,
        default="prompt",
        help=(
            "Which kind of listing to export: prompts (default), bundles, or apps. "
            "Filenames say which (for example acb_all_bundles.json)."
        ),
    )
    parser.add_argument(
        "--layout",
        choices=LAYOUTS,
        default="catalog",
        help=(
            "'catalog' (default) writes one file per catalog. 'files' writes one Markdown "
            "file per prompt, with YAML front matter, into a folder per catalog."
        ),
    )
    parser.add_argument(
        "--sort",
        choices=SORT_OPTIONS,
        default="newest",
        help="Sort selected prompts before writing.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--allow-missing-descriptions",
        action="store_true",
        help="Write files even if one or more prompt descriptions are missing.",
    )
    parser.add_argument(
        "--domain",
        help="Comma-separated domain filter, for example: text,image,video.",
    )
    parser.add_argument(
        "--type",
        dest="prompt_type",
        help="Comma-separated PromptBase type filter, for example: gpt,claude.",
    )
    price_group = parser.add_mutually_exclusive_group()
    price_group.add_argument(
        "--free-only",
        action="store_true",
        help="Export only free prompts.",
    )
    price_group.add_argument(
        "--paid-only",
        action="store_true",
        help="Export only paid prompts.",
    )
    parser.add_argument(
        "--min-price",
        type=float,
        help="Export prompts priced at or above this amount.",
    )
    parser.add_argument(
        "--max-price",
        type=float,
        help="Export prompts priced at or below this amount.",
    )
    parser.add_argument(
        "--min-sales",
        type=int,
        help="Export prompts with at least this many sales.",
    )
    parser.add_argument(
        "--min-rating",
        type=float,
        help="Export prompts rated at or above this value.",
    )
    parser.add_argument(
        "--extra-fields",
        help=(
            "Comma-separated extra fields to include, or 'all': "
            f"{', '.join(EXTRA_FIELDS)}. Not available for --format txt."
        ),
    )
    parser.add_argument(
        "--csv-safe",
        action="store_true",
        help=(
            "Prefix CSV text cells that start with =, +, -, or @ with an apostrophe so "
            "spreadsheet apps do not run them as formulas. Requires --format csv."
        ),
    )
    parser.add_argument(
        "--compare-csv-safe",
        action="store_true",
        help=(
            "Read the CSV --compare or --update-file catalog as one written with "
            "--csv-safe, restoring its escaped cells. Independent of --csv-safe, which "
            "only affects the file being written."
        ),
    )
    parser.add_argument(
        "--timestamp-filenames",
        action="store_true",
        help="Append a YYYYMMDD_HHMMSS timestamp to generated filenames.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Write a single export to this exact file path. Requires --mode all, text, or image.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow --output-file to replace an existing file.",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        help="Compare the selected prompts against an existing JSON, CSV, TXT, or Markdown export.",
    )
    parser.add_argument(
        "--diff-output",
        type=Path,
        action="append",
        help=(
            "Write the comparison report to this path: JSON for a .json path, Markdown "
            "otherwise. Repeat to write several. Requires --compare or --update-file."
        ),
    )
    parser.add_argument(
        "--fail-on-diff",
        action="store_true",
        help="Exit with code 2 when --compare finds added, removed, or changed records.",
    )
    parser.add_argument(
        "--update-file",
        type=Path,
        help=(
            "Compare against this existing export and rewrite it with current selected prompts. "
            "Requires --mode all, text, or image."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help=(
            "Export only the first N prompts after filtering and sorting. The limit is "
            "applied before --mode splits prompts into text and image files."
        ),
    )
    parser.add_argument(
        "--since",
        help="Export prompts created on or after this date or datetime, for example 2026-01-01.",
    )
    parser.add_argument(
        "--until",
        help="Export prompts created on or before this date or datetime, for example 2026-12-31.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, filter, and validate records without writing files.",
    )
    parser.add_argument(
        "--list-domains",
        action="store_true",
        help="Print domain counts after filters and exit without writing files.",
    )
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="Print PromptBase type counts after filters and exit without writing files.",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress normal command output.",
    )
    output_group.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra filtering details.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    make_output_safe()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.mode = MODE_ALIASES.get(args.mode, args.mode)

    try:
        options = normalize_options(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # One timestamp for the whole run keeps a multi-profile export's files
    # grouped under the same suffix.
    timestamp = (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.timestamp_filenames
        else None
    )
    exit_codes: list[int] = []
    for index, profile_input in enumerate(options.profiles):
        if index and not args.quiet:
            print()
        exit_codes.append(export_profile(profile_input, args, options, timestamp))
    # A failed profile does not stop the others, but the run still reports it.
    if EXIT_ERROR in exit_codes:
        return EXIT_ERROR
    if EXIT_DIFF in exit_codes:
        return EXIT_DIFF
    return EXIT_SUCCESS


def export_profile(
    profile_input: str,
    args: argparse.Namespace,
    options: RunOptions,
    timestamp: str | None,
) -> int:
    """Fetch, filter, and write the exports for one profile; return its exit code."""
    try:
        profile, records = fetch_prompts(
            profile_input, extra_fields=options.extra_fields, item_type=args.item_type
        )
    except PromptBaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    kind = ITEM_TYPE_PLURALS[args.item_type]
    if not records:
        print(f"error: no approved {kind} found for @{profile.username}", file=sys.stderr)
        return EXIT_ERROR

    if not sorted_newest_to_oldest(records):
        print(f"error: {args.item_type} records are not sorted newest to oldest", file=sys.stderr)
        return EXIT_ERROR

    selected_records = options.selection.apply(records)
    if not selected_records:
        print(f"error: no {kind} matched the selected filters", file=sys.stderr)
        return EXIT_ERROR

    missing_descriptions = without_description(selected_records)
    if missing_descriptions and not args.allow_missing_descriptions:
        print(
            "error: missing descriptions for "
            f"{len(missing_descriptions)} {args.item_type}(s). "
            "Use --allow-missing-descriptions to write partial exports.",
            file=sys.stderr,
        )
        for record in missing_descriptions[:10]:
            print(f"  - {record.title} ({record.slug})", file=sys.stderr)
        return EXIT_ERROR

    modes = split_modes(args.mode)
    output_dir = Path(args.output_dir)

    if not args.quiet:
        print(f"Profile: @{profile.username}")
        print(f"Approved {kind} found: {len(records)}")
        print(f"Selected after filters: {len(selected_records)}")
        if args.verbose:
            if args.item_type != "prompt":
                print(f"Item type: {args.item_type}")
            print(f"Format: {options.export_format}")
            if options.layout != "catalog":
                print(f"Layout: {options.layout}")
            print(f"Sort: {args.sort}")
            print(f"Output directory: {output_dir}")
            if options.output_file:
                print(f"Output file: {options.output_file}")
            if options.extra_fields:
                print(f"Extra fields: {', '.join(options.extra_fields)}")
            if args.domain:
                print(f"Domain filter: {args.domain}")
            if args.prompt_type:
                print(f"Type filter: {args.prompt_type}")
            if args.free_only:
                print("Price filter: free only")
            if args.paid_only:
                print("Price filter: paid only")
            if args.min_price is not None or args.max_price is not None:
                print(f"Price range: {args.min_price}..{args.max_price}")
            if args.since or args.until:
                print(f"Created range: {args.since or '*'}..{args.until or '*'}")
            if args.min_sales is not None:
                print(f"Minimum sales: {args.min_sales}")
            if args.min_rating is not None:
                print(f"Minimum rating: {args.min_rating:g}")
            if args.limit is not None:
                print(f"Limit: {args.limit}")

    if args.list_domains:
        print_counts("Domains", count_by(selected_records, "domain"))
    if args.list_types:
        print_counts("Types", count_by(selected_records, "prompt_type"))
    if args.dry_run and not args.quiet:
        print_planned_outputs(selected_records, modes)

    if args.dry_run or args.list_domains or args.list_types:
        if not args.quiet:
            print("Dry run: no files written.")
        return EXIT_SUCCESS

    diff_exit_code = EXIT_SUCCESS
    if options.compare_path:
        diff_exit_code = handle_compare(
            selected_records,
            modes,
            options.compare_path,
            args.diff_output or [],
            args.fail_on_diff,
            quiet=args.quiet,
            csv_safe=args.compare_csv_safe,
        )
        if diff_exit_code == EXIT_ERROR:
            # An operational failure (catalog load failed, or the requested diff
            # report could not be written) must abort before any export so we
            # never overwrite the existing --update-file catalog.
            return diff_exit_code
        if diff_exit_code and not args.update_file:
            # --fail-on-diff signalled differences. Without --update-file this is
            # terminal; with --update-file the rewrite still proceeds and the
            # EXIT_DIFF code is returned after writing.
            return diff_exit_code

    if options.output_file:
        mode = modes[0]
        filtered = filter_records(selected_records, mode)
        try:
            output_path = write_export_to_path(
                options.output_file,
                filtered,
                options.export_format,
                overwrite=options.overwrite_output,
                csv_safe=args.csv_safe,
                extra_fields=options.extra_fields,
                item_type=args.item_type,
            )
        except FileExistsError as exc:
            print(f"error: {exc}. Use --overwrite to replace it.", file=sys.stderr)
            return EXIT_ERROR
        except OSError as exc:
            print(
                f"error: could not write {options.output_file}: {exc}",
                file=sys.stderr,
            )
            return EXIT_ERROR
        written_count = validate_written(output_path, options.export_format, len(filtered))
        if written_count is None:
            return EXIT_ERROR
        if not args.quiet:
            print(f"Wrote {mode:>5}: {written_count:>4} {kind} -> {output_path}")
            print_summary(selected_records, all_count=len(records))
        return diff_exit_code

    for mode in modes:
        filtered = filter_records(selected_records, mode)
        if options.layout == "files":
            written_count = write_files_for_mode(
                output_dir, profile.username, mode, filtered, options, args, timestamp
            )
            if written_count is None:
                return EXIT_ERROR
            continue
        try:
            output_path = write_export(
                output_dir,
                profile.username,
                mode,
                filtered,
                options.export_format,
                timestamp=timestamp,
                csv_safe=args.csv_safe,
                extra_fields=options.extra_fields,
                item_type=args.item_type,
            )
        except OSError as exc:
            print(f"error: could not write export to {output_dir}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        written_count = validate_written(output_path, options.export_format, len(filtered))
        if written_count is None:
            return EXIT_ERROR
        if not args.quiet:
            print(f"Wrote {mode:>5}: {written_count:>4} {kind} -> {output_path}")

    if not args.quiet:
        print_summary(selected_records, all_count=len(records))
    return EXIT_SUCCESS


def write_files_for_mode(
    output_dir: Path,
    username: str,
    mode: str,
    records: list[PromptRecord],
    options: RunOptions,
    args: argparse.Namespace,
    timestamp: str | None,
) -> int | None:
    """Write one mode's per-prompt files and check them; return the count, or None on error."""
    try:
        directory = write_markdown_files(
            output_dir,
            username,
            mode,
            records,
            extra_fields=options.extra_fields,
            item_type=args.item_type,
            timestamp=timestamp,
        )
    except OSError as exc:
        print(f"error: could not write export to {output_dir}: {exc}", file=sys.stderr)
        return None
    written = count_files(directory, unique_names(records))
    if written != len(records):
        print(
            f"error: validation failed for {directory}: expected {len(records)}, wrote {written}",
            file=sys.stderr,
        )
        return None
    if not args.quiet:
        print(f"Wrote {mode:>5}: {written:>4} {ITEM_TYPE_PLURALS[args.item_type]} -> {directory}")
    return written


def normalize_options(args: argparse.Namespace) -> RunOptions:
    # Order-preserving de-duplication: the same profile twice would only
    # rewrite the same files.
    profiles = list(dict.fromkeys(profile.strip() for profile in args.profiles))
    if not all(profiles):
        raise ValueError("profile cannot be empty")
    if args.layout == "files" and (args.output_file or args.compare or args.update_file):
        # First, so this is the error named rather than one about --mode or a missing file.
        raise ValueError(
            "--layout files writes a folder of files, so it cannot be used with "
            "--output-file, --compare, or --update-file"
        )
    if len(profiles) > 1 and (args.output_file or args.update_file or args.compare):
        raise ValueError(
            "--output-file, --compare, and --update-file take a single profile"
        )
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be greater than zero")
    # argparse's float() accepts "nan"/"inf"; NaN compares false against every
    # value and would silently filter out all records.
    for flag, value in (
        ("--min-price", args.min_price),
        ("--max-price", args.max_price),
        ("--min-rating", args.min_rating),
    ):
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{flag} must be a finite number")
    if args.min_sales is not None and args.min_sales < 0:
        raise ValueError("--min-sales cannot be negative")
    if args.min_rating is not None and args.min_rating < 0:
        raise ValueError("--min-rating cannot be negative")
    if args.min_price is not None and args.min_price < 0:
        raise ValueError("--min-price cannot be negative")
    if args.max_price is not None and args.max_price < 0:
        raise ValueError("--max-price cannot be negative")
    if (
        args.min_price is not None
        and args.max_price is not None
        and args.min_price > args.max_price
    ):
        raise ValueError("--min-price cannot be greater than --max-price")
    if args.output_file and args.update_file:
        raise ValueError("--output-file and --update-file cannot be used together")
    if args.timestamp_filenames and (args.output_file or args.update_file):
        raise ValueError("--timestamp-filenames cannot be used with --output-file or --update-file")
    if args.diff_output and not (args.compare or args.update_file):
        raise ValueError("--diff-output requires --compare or --update-file")
    if args.fail_on_diff and not (args.compare or args.update_file):
        raise ValueError("--fail-on-diff requires --compare or --update-file")
    if (args.output_file or args.compare or args.update_file) and args.mode == "split":
        raise ValueError(
            "--output-file, --compare, and --update-file require --mode all, text, or image"
        )

    output_file = args.output_file
    compare_path = args.compare
    overwrite_output = args.overwrite
    if args.update_file:
        if not args.update_file.exists():
            raise ValueError(f"--update-file does not exist: {args.update_file}")
        # The catalog is read back by its extension on the next run, so writing
        # another format into it would break every later comparison.
        if args.format and args.format != infer_format_from_path(args.update_file):
            raise ValueError(
                f"--format {args.format} does not match the --update-file extension "
                f"{args.update_file.suffix}"
            )
        output_file = args.update_file
        compare_path = args.update_file
        overwrite_output = True

    if output_file and not args.format:
        export_format = infer_format_from_path(output_file)
    else:
        export_format = args.format or "txt"
    if args.layout == "files":
        if args.format not in (None, "markdown"):
            raise ValueError(
                "--layout files writes Markdown: use --format markdown or omit --format"
            )
        export_format = "markdown"
    # Output escaping and input decoding are separate on purpose: a safe export
    # may be compared with a plain catalog and vice versa.
    if args.csv_safe and export_format != "csv":
        raise ValueError("--csv-safe requires --format csv")
    extra_fields = parse_extra_fields(args.extra_fields)
    if extra_fields and export_format == "txt":
        raise ValueError(
            "--extra-fields needs --format markdown, json, ndjson, csv, or html: txt holds only "
            "a title and a description"
        )
    compares_csv = compare_path is not None and compare_path.suffix.lower() == ".csv"
    if args.compare_csv_safe and not compares_csv:
        raise ValueError("--compare-csv-safe requires a CSV --compare or --update-file catalog")

    since_created = parse_datetime_ms(args.since, end_of_day=False) if args.since else None
    until_created = parse_datetime_ms(args.until, end_of_day=True) if args.until else None
    if (
        since_created is not None
        and until_created is not None
        and since_created > until_created
    ):
        raise ValueError("--since cannot be later than --until")

    return RunOptions(
        profiles=tuple(profiles),
        selection=Selection(
            domains=frozenset(parse_csv_option(args.domain)),
            prompt_types=frozenset(parse_csv_option(args.prompt_type)),
            price="free" if args.free_only else "paid" if args.paid_only else "all",
            min_price=args.min_price,
            max_price=args.max_price,
            since_created=since_created,
            until_created=until_created,
            min_sales=args.min_sales,
            min_rating=args.min_rating,
            sort=args.sort,
            limit=args.limit,
        ),
        export_format=export_format,
        extra_fields=extra_fields,
        output_file=output_file,
        compare_path=compare_path,
        overwrite_output=overwrite_output,
        layout=args.layout,
    )


def validate_written(output_path: Path, export_format: str, expected: int) -> int | None:
    """Re-read a written export and return its record count, or None on mismatch."""
    try:
        written_count = count_written_records(output_path, export_format)
    except (OSError, ValueError) as exc:
        print(f"error: validation failed for {output_path}: {exc}", file=sys.stderr)
        return None
    if written_count != expected:
        print(
            f"error: validation failed for {output_path}: "
            f"expected {expected}, wrote {written_count}",
            file=sys.stderr,
        )
        return None
    return written_count


def handle_compare(
    selected_records: list[PromptRecord],
    modes: Sequence[str],
    compare_path: Path,
    diff_outputs: list[Path],
    fail_on_diff: bool,
    *,
    quiet: bool,
    csv_safe: bool = False,
) -> int:
    try:
        previous_records = load_catalog(compare_path, csv_safe=csv_safe)
    except (OSError, ValueError) as exc:
        print(f"error: could not load comparison catalog: {exc}", file=sys.stderr)
        return EXIT_ERROR
    filtered = filter_records(selected_records, modes[0])
    diff = compare_catalogs(previous_records, filtered)
    report = format_diff_report(diff)
    if not quiet:
        print(report.rstrip())
    if not write_diff_reports(diff_outputs, diff, quiet=quiet):
        return EXIT_ERROR
    if fail_on_diff and diff.has_changes:
        return EXIT_DIFF
    return EXIT_SUCCESS


def write_diff_reports(paths: list[Path], diff: CatalogDiff, *, quiet: bool) -> bool:
    """Write the diff report to every path; return False after the first failure."""
    for path in paths:
        try:
            write_diff_report(path, diff)
        except OSError as exc:
            print(f"error: could not write diff report to {path}: {exc}", file=sys.stderr)
            return False
        if not quiet:
            print(f"Wrote diff report -> {path}")
    return True


def build_diff_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptbase-diff",
        description=(
            "Compare two exported PromptBase catalogs without fetching anything. "
            "Reads JSON, CSV, TXT, Markdown, and HTML catalogs."
        ),
    )
    parser.add_argument("previous", type=Path, help="The older catalog.")
    parser.add_argument("current", type=Path, help="The newer catalog.")
    parser.add_argument(
        "--diff-output",
        type=Path,
        action="append",
        help="Write the report to this path: JSON for a .json path, Markdown otherwise.",
    )
    parser.add_argument(
        "--fail-on-diff",
        action="store_true",
        help="Exit with code 2 when the catalogs differ.",
    )
    parser.add_argument(
        "--previous-csv-safe",
        action="store_true",
        help="The previous catalog is a CSV written with --csv-safe; restore its escaped cells.",
    )
    parser.add_argument(
        "--current-csv-safe",
        action="store_true",
        help="The current catalog is a CSV written with --csv-safe; restore its escaped cells.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the report.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def diff_main(argv: list[str] | None = None) -> int:
    """Entry point for ``pb-diff``: compare two catalog files offline."""
    make_output_safe()
    args = build_diff_parser().parse_args(argv)
    loaded: list[list[dict[str, Any]]] = []
    for path, csv_safe in (
        (args.previous, args.previous_csv_safe),
        (args.current, args.current_csv_safe),
    ):
        try:
            loaded.append(load_catalog(path, csv_safe=csv_safe))
        except (OSError, ValueError) as exc:
            print(f"error: could not load catalog {path}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    diff = compare_catalog_records(loaded[0], loaded[1])
    if not args.quiet:
        print(format_diff_report(diff).rstrip())
    if not write_diff_reports(args.diff_output or [], diff, quiet=args.quiet):
        return EXIT_ERROR
    if args.fail_on_diff and diff.has_changes:
        return EXIT_DIFF
    return EXIT_SUCCESS


def count_by(records: list[PromptRecord], attribute: str) -> dict[str, int]:
    counts = Counter((getattr(record, attribute) or "unknown") for record in records)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def print_counts(title: str, counts: dict[str, int]) -> None:
    print(f"{title}:")
    for key, value in counts.items():
        print(f"  {key}: {value}")


def print_planned_outputs(records: list[PromptRecord], modes: Sequence[str]) -> None:
    print("Planned outputs:")
    for mode in modes:
        print(f"  {mode}: {len(filter_records(records, mode))}")


def print_summary(records: list[PromptRecord], all_count: int | None = None) -> None:
    image_count = len(filter_records(records, "image"))
    text_count = len(filter_records(records, "text"))
    other_count = len(records) - image_count - text_count
    total = all_count if all_count is not None else len(records)
    print(
        "Summary: "
        f"text={text_count}, image={image_count}, "
        f"other={other_count}, selected={len(records)}, all={total}"
    )

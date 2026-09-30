"""``pb-history``: keep snapshots of a profile's counters and report what changed.

A snapshot is the views, sales, downloads, favorites, reviews, rating, and price
of every listing of one kind for one profile, at one moment. Snapshots live in a
SQLite file (the standard library's ``sqlite3``), so a scheduled
``pb-history snapshot`` builds a history that ``pb-history report`` turns into
"what moved since last week".
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .client import PromptBaseError, fetch_prompts, parse_profile_input
from .console import make_output_safe
from .models import ITEM_TYPE_PLURALS, ITEM_TYPES, PromptRecord

SCHEMA_VERSION = 1
COUNTERS = ("views", "sales", "downloads", "favorites", "reviews")
REPORT_FORMATS = ("markdown", "json", "html")
EXIT_SUCCESS = 0
EXIT_ERROR = 1

_SCHEMA = """
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at TEXT NOT NULL,
    profile TEXT NOT NULL,
    item_type TEXT NOT NULL,
    record_count INTEGER NOT NULL
);
CREATE INDEX snapshots_lookup ON snapshots (profile, item_type, taken_at);
CREATE TABLE observations (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    discount REAL NOT NULL,
    views INTEGER NOT NULL,
    sales INTEGER NOT NULL,
    downloads INTEGER NOT NULL,
    favorites INTEGER NOT NULL,
    rating REAL NOT NULL,
    reviews INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, slug)
) WITHOUT ROWID;
"""


class HistoryError(RuntimeError):
    """The history file or the request cannot be used."""


@dataclass(frozen=True)
class Snapshot:
    id: int
    taken_at: datetime
    profile: str
    item_type: str
    record_count: int


@dataclass(frozen=True)
class Observation:
    slug: str
    title: str
    price: float
    discount: float
    views: int
    sales: int
    downloads: int
    favorites: int
    rating: float
    reviews: int


@dataclass(frozen=True)
class Mover:
    slug: str
    title: str
    change: dict[str, int]
    price: float


@dataclass(frozen=True)
class Report:
    profile: str
    item_type: str
    baseline: Snapshot
    latest: Snapshot
    metric: str
    totals: dict[str, tuple[float, float]]
    movers: list[Mover]
    new: list[Observation]
    removed: list[Observation]
    price_changes: list[tuple[Observation, Observation]]
    series: list[tuple[datetime, dict[str, int]]] = field(default_factory=list)

    @property
    def days(self) -> float:
        return (self.latest.taken_at - self.baseline.taken_at).total_seconds() / 86400


# -- storage --------------------------------------------------------------------------


def connect(path: Path, *, create: bool) -> sqlite3.Connection:
    """Open a history file; with ``create`` a missing one is made, otherwise it must exist.

    The connection is closed again if the file turns out to be unusable, so a
    refused file is never left locked (on Windows an open file cannot be removed).
    """
    if not create and not path.is_file():
        raise HistoryError(f"history file not found: {path}")
    connection: sqlite3.Connection | None = None
    try:
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        _prepare(connection, path, create)
    except BaseException as exc:
        if connection is not None:
            connection.close()
        if isinstance(exc, sqlite3.Error):
            raise HistoryError(f"could not open {path}: {exc}") from exc
        raise
    return connection


def _prepare(connection: sqlite3.Connection, path: Path, create: bool) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 0:
        raise HistoryError(
            f"{path} uses history format {version}, but this version reads {SCHEMA_VERSION}"
        )
    has_tables = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
    ).fetchone()
    if has_tables:
        raise HistoryError(f"{path} is a SQLite file, but not a pb-history file")
    if not create:
        raise HistoryError(f"{path} is empty: take a snapshot first")
    with connection:
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def record_snapshot(
    connection: sqlite3.Connection,
    profile: str,
    records: Sequence[PromptRecord],
    item_type: str,
    taken_at: datetime | None = None,
) -> Snapshot:
    """Store ``records`` as one snapshot of ``profile``'s ``item_type`` listings."""
    moment = (taken_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = [
        (record.slug, record.title, record.price, record.discount, record.views, record.sales,
         record.downloads, record.favorites, record.rating, record.reviews)
        for record in records
    ]
    with connection:
        cursor = connection.execute(
            "INSERT INTO snapshots (taken_at, profile, item_type, record_count) "
            "VALUES (?, ?, ?, ?)",
            (moment.isoformat(timespec="seconds"), profile, item_type, len(rows)),
        )
        snapshot_id = int(cursor.lastrowid or 0)
        connection.executemany(
            "INSERT INTO observations (snapshot_id, slug, title, price, discount, views, sales, "
            "downloads, favorites, rating, reviews) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(snapshot_id, *row) for row in rows],
        )
    return Snapshot(snapshot_id, moment.replace(microsecond=0), profile, item_type, len(rows))


def _snapshot(row: Sequence[Any]) -> Snapshot:
    return Snapshot(int(row[0]), datetime.fromisoformat(row[1]), row[2], row[3], int(row[4]))


def list_snapshots(
    connection: sqlite3.Connection,
    profile: str | None = None,
    item_type: str | None = None,
) -> list[Snapshot]:
    """Snapshots oldest first, optionally for one profile and kind."""
    query = "SELECT id, taken_at, profile, item_type, record_count FROM snapshots"
    clauses, arguments = [], []
    if profile is not None:
        clauses.append("profile = ?")
        arguments.append(profile)
    if item_type is not None:
        clauses.append("item_type = ?")
        arguments.append(item_type)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY taken_at, id"
    return [_snapshot(row) for row in connection.execute(query, arguments)]


def load_observations(connection: sqlite3.Connection, snapshot_id: int) -> dict[str, Observation]:
    rows = connection.execute(
        "SELECT slug, title, price, discount, views, sales, downloads, favorites, rating, reviews "
        "FROM observations WHERE snapshot_id = ?",
        (snapshot_id,),
    )
    return {row[0]: Observation(*row) for row in rows}


def totals_series(
    connection: sqlite3.Connection,
    profile: str,
    item_type: str,
) -> list[tuple[datetime, dict[str, int]]]:
    """The totals of every counter at every snapshot, oldest first, for a chart."""
    sums = ", ".join(f"SUM(o.{counter})" for counter in COUNTERS)
    rows = connection.execute(
        f"SELECT s.taken_at, COUNT(o.slug), {sums} FROM snapshots s "
        "LEFT JOIN observations o ON o.snapshot_id = s.id "
        "WHERE s.profile = ? AND s.item_type = ? GROUP BY s.id ORDER BY s.taken_at, s.id",
        (profile, item_type),
    )
    return [
        (datetime.fromisoformat(row[0]),
         {"prompts": int(row[1]), **{c: int(row[2 + i] or 0) for i, c in enumerate(COUNTERS)}})
        for row in rows
    ]


# -- reporting ------------------------------------------------------------------------


def choose_baseline(
    snapshots: Sequence[Snapshot],
    *,
    since: date | None = None,
    days: float | None = None,
) -> tuple[Snapshot, Snapshot]:
    """Pick ``(baseline, latest)`` from snapshots of one profile and kind, oldest first.

    By default the baseline is the snapshot before the latest. ``since`` picks the
    first snapshot taken on or after that UTC date; ``days`` the latest one taken
    at least that long before the latest snapshot.
    """
    if len(snapshots) < 2:
        raise HistoryError(
            f"need at least two snapshots to compare, found {len(snapshots)}: "
            "run pb-history snapshot again later"
        )
    latest = snapshots[-1]
    earlier = snapshots[:-1]
    if since is not None:
        start = datetime.combine(since, time.min, tzinfo=timezone.utc)
        candidates = [snap for snap in earlier if snap.taken_at >= start]
        if not candidates:
            raise HistoryError(
                f"no snapshot between {since} and the latest ({latest.taken_at.date()})"
            )
        return candidates[0], latest
    if days is not None:
        cutoff = latest.taken_at - timedelta(days=days)
        candidates = [snap for snap in earlier if snap.taken_at <= cutoff]
        if not candidates:
            oldest = (latest.taken_at - earlier[0].taken_at).total_seconds() / 86400
            raise HistoryError(
                f"no snapshot is {days:g} days older than the latest; "
                f"the oldest is {oldest:.1f} days older"
            )
        return candidates[-1], latest
    return earlier[-1], latest


def _totals(observations: dict[str, Observation]) -> dict[str, float]:
    values: dict[str, float] = {"prompts": float(len(observations))}
    for counter in COUNTERS:
        values[counter] = float(sum(getattr(o, counter) for o in observations.values()))
    rated = [o.rating for o in observations.values() if o.rating > 0]
    values["average rating"] = sum(rated) / len(rated) if rated else 0.0
    return values


def build_report(
    baseline: Snapshot,
    latest: Snapshot,
    before: dict[str, Observation],
    after: dict[str, Observation],
    *,
    metric: str = "sales",
    top: int = 10,
    series: list[tuple[datetime, dict[str, int]]] | None = None,
) -> Report:
    if metric not in COUNTERS:
        raise HistoryError(f"unknown metric {metric!r}: use one of {', '.join(COUNTERS)}")
    totals_before, totals_after = _totals(before), _totals(after)
    totals = {key: (totals_before[key], totals_after[key]) for key in totals_before}
    movers = []
    for slug in sorted(set(before) & set(after)):
        change = {c: getattr(after[slug], c) - getattr(before[slug], c) for c in COUNTERS}
        if change[metric] > 0:
            movers.append(Mover(slug, after[slug].title, change, after[slug].price))
    movers.sort(key=lambda mover: (-mover.change[metric], mover.slug))
    price_changes = [
        (before[slug], after[slug])
        for slug in sorted(set(before) & set(after))
        if (before[slug].price, before[slug].discount) != (after[slug].price, after[slug].discount)
    ]
    return Report(
        profile=latest.profile,
        item_type=latest.item_type,
        baseline=baseline,
        latest=latest,
        metric=metric,
        totals=totals,
        movers=movers[:top],
        new=[after[slug] for slug in sorted(set(after) - set(before))],
        removed=[before[slug] for slug in sorted(set(before) - set(after))],
        price_changes=price_changes,
        series=series or [],
    )


def describe_duration(days: float) -> str:
    """A short human duration: ``45 seconds``, ``3.5 hours``, or ``12.0 days``."""
    seconds = days * 86400
    if seconds < 90:
        return f"{round(seconds)} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{days:.1f} days"


def _number(value: float) -> str:
    return f"{value:g}" if value != int(value) else str(int(value))


def _delta(before: float, after: float) -> str:
    change = after - before
    return "0" if change == 0 else f"{change:+g}"


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _heading(report: Report) -> str:
    return f"@{report.profile} ({ITEM_TYPE_PLURALS[report.item_type]})"


def render_markdown(report: Report) -> str:
    kind = ITEM_TYPE_PLURALS[report.item_type]
    lines = [
        f"# PromptBase trends: {_heading(report)}",
        "",
        f"From snapshot {report.baseline.id} ({report.baseline.taken_at.isoformat()}) to snapshot "
        f"{report.latest.id} ({report.latest.taken_at.isoformat()}): "
        f"{describe_duration(report.days)}.",
        "",
        "| Measure | Before | Now | Change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key, (before, after) in report.totals.items():
        lines.append(f"| {key} | {_number(round(before, 2))} | {_number(round(after, 2))} "
                     f"| {_delta(round(before, 2), round(after, 2))} |")
    lines += ["", f"## Top movers by {report.metric}", ""]
    if report.movers:
        lines += [f"| {report.item_type.capitalize()} | {report.metric} | " + " | ".join(
            c for c in COUNTERS if c != report.metric) + " | price |",
            "| --- | " + " | ".join(["---:"] * (len(COUNTERS) + 1)) + " |"]
        for mover in report.movers:
            others = " | ".join(f"{mover.change[c]:+d}" for c in COUNTERS if c != report.metric)
            lines.append(f"| {_cell(mover.title)} (`{_cell(mover.slug)}`) | "
                         f"{mover.change[report.metric]:+d} | {others} | {_number(mover.price)} |")
    else:
        lines.append(f"No {kind} gained {report.metric} in this period.")
    lines += ["", f"## New {kind} ({len(report.new)})", ""]
    lines += [f"- {_cell(o.title)} (`{_cell(o.slug)}`)" for o in report.new] or ["None."]
    lines += ["", f"## Removed {kind} ({len(report.removed)})", ""]
    lines += [f"- {_cell(o.title)} (`{_cell(o.slug)}`)" for o in report.removed] or ["None."]
    lines += ["", f"## Price changes ({len(report.price_changes)})", ""]
    lines += [
        f"- {_cell(after.title)} (`{_cell(after.slug)}`): price {_number(before.price)} to "
        f"{_number(after.price)}, discount {_number(before.discount)} to {_number(after.discount)}"
        for before, after in report.price_changes
    ] or ["None."]
    return "\n".join(lines) + "\n"


def report_to_dict(report: Report) -> dict[str, Any]:
    def snap(snapshot: Snapshot) -> dict[str, Any]:
        return {"id": snapshot.id, "taken_at": snapshot.taken_at.isoformat(),
                "record_count": snapshot.record_count}

    def obs(observation: Observation) -> dict[str, Any]:
        return {"slug": observation.slug, "title": observation.title}

    return {
        "profile": report.profile,
        "item_type": report.item_type,
        "baseline": snap(report.baseline),
        "latest": snap(report.latest),
        "days": round(report.days, 3),
        "metric": report.metric,
        "totals": {key: {"before": before, "after": after, "change": after - before}
                   for key, (before, after) in report.totals.items()},
        "movers": [{"slug": m.slug, "title": m.title, "price": m.price, "change": m.change}
                   for m in report.movers],
        "new": [obs(o) for o in report.new],
        "removed": [obs(o) for o in report.removed],
        "price_changes": [
            {"slug": after.slug, "title": after.title,
             "price": {"before": before.price, "after": after.price},
             "discount": {"before": before.discount, "after": after.discount}}
            for before, after in report.price_changes
        ],
    }


def render_json(report: Report) -> str:
    return json.dumps(report_to_dict(report), ensure_ascii=False, indent=2) + "\n"


def _chart(series: list[tuple[datetime, dict[str, int]]], counter: str) -> str:
    """A small inline SVG line of one counter's total at each snapshot."""
    width, height, pad = 360, 90, 8
    values = [point[1][counter] for point in series]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = (width - 2 * pad) / (len(values) - 1)
    points = " ".join(
        f"{pad + index * step:.1f},{height - pad - (value - low) / span * (height - 2 * pad):.1f}"
        for index, value in enumerate(values)
    )
    label = html.escape(f"{counter}: {values[0]} to {values[-1]} over {len(values)} snapshots")
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{label}" class="chart">'
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="2"/></svg>'
    )


def _span(series: list[tuple[datetime, dict[str, int]]], counter: str) -> str:
    """``: 85460 to 93086`` for a chart caption, or nothing when there is no line."""
    if len(series) < 2:
        return ""
    return f": {series[0][1][counter]} to {series[-1][1][counter]} over {len(series)} snapshots"


def render_html(report: Report) -> str:
    h = html.escape
    kind = ITEM_TYPE_PLURALS[report.item_type]

    def rows(cells: list[list[str]]) -> str:
        return "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in cells
        )

    def listed(items: Sequence[Observation]) -> str:
        return "".join(
            f"<li>{h(o.title)} <code>{h(o.slug)}</code></li>" for o in items
        ) or "<li>None.</li>"

    totals = rows([
        [h(key), h(_number(round(before, 2))), h(_number(round(after, 2))),
         h(_delta(round(before, 2), round(after, 2)))]
        for key, (before, after) in report.totals.items()
    ])
    movers = rows([
        [f"{h(m.title)} <code>{h(m.slug)}</code>", *(h(f"{m.change[c]:+d}") for c in COUNTERS),
         h(_number(m.price))]
        for m in report.movers
    ]) or f'<tr><td colspan="{len(COUNTERS) + 2}">No {h(kind)} gained {h(report.metric)}.</td></tr>'
    charts = "".join(
        f"<figure><figcaption>{h(c)}{h(_span(report.series, c))}</figcaption>"
        f"{_chart(report.series, c)}</figure>"
        for c in ("views", "sales", "favorites")
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>PromptBase trends: {h(_heading(report))}</title><style>"
        ":root{color-scheme:light dark;--line:#8884}body{font:16px/1.5 system-ui,sans-serif;"
        "max-width:920px;margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}"
        "td,th{border-top:1px solid var(--line);padding:.4rem .5rem;text-align:right}"
        "td:first-child,th:first-child{text-align:left}.chart{width:100%;max-width:360px;height:auto}"
        "figure{display:inline-block;margin:0 1rem 1rem 0}code{font-size:.85em}"
        ".scroll{overflow-x:auto}figcaption{font-size:.85em}"
        "</style></head><body>"
        f"<h1>PromptBase trends: {h(_heading(report))}</h1>"
        f"<p>From snapshot {report.baseline.id} ({h(report.baseline.taken_at.isoformat())}) to "
        f"snapshot {report.latest.id} ({h(report.latest.taken_at.isoformat())}): "
        f"{h(describe_duration(report.days))}.</p>"
        f"{charts}"
        '<div class="scroll"><table><thead><tr><th>Measure</th><th>Before</th><th>Now</th>'
        f"<th>Change</th></tr></thead><tbody>{totals}</tbody></table></div>"
        f"<h2>Top movers by {h(report.metric)}</h2>"
        f'<div class="scroll"><table><thead><tr><th>{h(kind)}</th>'
        + "".join(f"<th>{h(c)}</th>" for c in COUNTERS) + "<th>price</th></tr></thead>"
        f"<tbody>{movers}</tbody></table></div>"
        f"<h2>New {h(kind)} ({len(report.new)})</h2><ul>{listed(report.new)}</ul>"
        f"<h2>Removed {h(kind)} ({len(report.removed)})</h2><ul>{listed(report.removed)}</ul>"
        f"<h2>Price changes ({len(report.price_changes)})</h2><ul>"
        + ("".join(
            f"<li>{h(after.title)} <code>{h(after.slug)}</code>: price {h(_number(before.price))} "
            f"to {h(_number(after.price))}, discount {h(_number(before.discount))} to "
            f"{h(_number(after.discount))}</li>" for before, after in report.price_changes
        ) or "<li>None.</li>")
        + "</ul></body></html>\n"
    )


def render(report: Report, report_format: str) -> str:
    if report_format == "markdown":
        return render_markdown(report)
    if report_format == "json":
        return render_json(report)
    if report_format == "html":
        return render_html(report)
    raise HistoryError(f"unknown report format {report_format!r}")


# -- command line ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptbase-history",
        description="Keep snapshots of a profile's counters and report what changed.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    snapshot = commands.add_parser("snapshot", help="Fetch profiles and record a snapshot of each.")
    snapshot.add_argument("profiles", nargs="+", metavar="profile")
    snapshot.add_argument("--db", type=Path, required=True, help="The history file to add to.")
    snapshot.add_argument("--item-type", choices=ITEM_TYPES, default="prompt")
    snapshot.add_argument("--quiet", action="store_true")

    report = commands.add_parser("report", help="Compare two snapshots.")
    report.add_argument("--db", type=Path, required=True)
    report.add_argument("--profile", help="Needed when the file holds more than one profile.")
    report.add_argument("--item-type", choices=ITEM_TYPES, default="prompt")
    window = report.add_mutually_exclusive_group()
    window.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="Compare with the first snapshot taken on or after this UTC date.")
    window.add_argument("--days", type=float, help="Compare with a snapshot at least this old.")
    report.add_argument("--metric", choices=COUNTERS, default="sales")
    report.add_argument("--top", type=int, default=10)
    report.add_argument("--format", choices=REPORT_FORMATS, default="markdown")
    report.add_argument("-o", "--output", type=Path, help="Write the report here, not to stdout.")

    listing = commands.add_parser("list", help="List the snapshots in a history file.")
    listing.add_argument("--db", type=Path, required=True)
    return parser


def _snapshot_command(args: argparse.Namespace) -> int:
    failed = False
    connection = connect(args.db, create=True)
    try:
        for profile_input in dict.fromkeys(args.profiles):
            try:
                profile, records = fetch_prompts(profile_input, item_type=args.item_type)
            except PromptBaseError as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed = True
                continue
            if not records:
                print(f"error: no approved {ITEM_TYPE_PLURALS[args.item_type]} found for "
                      f"@{profile.username}", file=sys.stderr)
                failed = True
                continue
            snapshot = record_snapshot(connection, profile.username, records, args.item_type)
            if not args.quiet:
                print(f"Recorded snapshot {snapshot.id}: {snapshot.record_count} "
                      f"{ITEM_TYPE_PLURALS[args.item_type]} for @{snapshot.profile} "
                      f"at {snapshot.taken_at.isoformat()}")
    finally:
        connection.close()
    return EXIT_ERROR if failed else EXIT_SUCCESS


def _select(connection: sqlite3.Connection, args: argparse.Namespace) -> list[Snapshot]:
    snapshots = list_snapshots(connection, None, args.item_type)
    profiles = sorted({snap.profile for snap in snapshots})
    if args.profile:
        wanted = parse_profile_input(args.profile)
        if wanted not in profiles:
            raise HistoryError(f"no snapshots of {ITEM_TYPE_PLURALS[args.item_type]} for @{wanted}")
        return [snap for snap in snapshots if snap.profile == wanted]
    if len(profiles) > 1:
        names = ", ".join(f"@{name}" for name in profiles)
        raise HistoryError(f"the file holds several profiles ({names}): choose one with --profile")
    return snapshots


def _report_command(args: argparse.Namespace) -> int:
    connection = connect(args.db, create=False)
    try:
        snapshots = _select(connection, args)
        baseline, latest = choose_baseline(snapshots, since=args.since, days=args.days)
        report = build_report(
            baseline, latest,
            load_observations(connection, baseline.id), load_observations(connection, latest.id),
            metric=args.metric, top=max(args.top, 0),
            series=totals_series(connection, latest.profile, latest.item_type),
        )
    finally:
        connection.close()
    text = render(report, args.format)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.format} report -> {args.output}")
    else:
        sys.stdout.write(text)
    return EXIT_SUCCESS


def _list_command(args: argparse.Namespace) -> int:
    connection = connect(args.db, create=False)
    try:
        snapshots = list_snapshots(connection)
    finally:
        connection.close()
    for snap in snapshots:
        print(f"{snap.id}\t{snap.taken_at.isoformat()}\t@{snap.profile}\t{snap.item_type}\t"
              f"{snap.record_count}")
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``pb-history``."""
    make_output_safe()
    args = build_parser().parse_args(argv)
    handlers = {"snapshot": _snapshot_command, "report": _report_command, "list": _list_command}
    try:
        return handlers[args.command](args)
    except (HistoryError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

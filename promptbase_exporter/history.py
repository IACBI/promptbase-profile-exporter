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
import math
import re
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import __version__
from .client import PromptBaseError, fetch_prompts, parse_profile_input
from .console import make_output_safe, printable
from .formatting import escape_markdown as _cell
from .models import ITEM_TYPE_PLURALS, ITEM_TYPES, PromptRecord

SCHEMA_VERSION = 1
COUNTERS = ("views", "sales", "downloads", "favorites", "reviews")
REPORT_FORMATS = ("markdown", "json", "html")
EXIT_SUCCESS = 0
EXIT_ERROR = 1
# An --alert rule fired: the report was still written, as with pb --fail-on-diff.
EXIT_ALERT = 2
ALERT_EVENTS = ("new", "removed", "price")
_ALERT_RULE = re.compile(
    r"(?P<counter>" + "|".join(COUNTERS) + r")\+(?P<amount>\d+(?:\.\d+)?)(?P<percent>%)?"
)

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

_EXPECTED_COLUMNS = {
    "snapshots": ["id", "taken_at", "profile", "item_type", "record_count"],
    "observations": ["snapshot_id", "slug", "title", "price", "discount", "views", "sales",
                     "downloads", "favorites", "rating", "reviews"],
}


class HistoryError(RuntimeError):
    """The history file or the request cannot be used."""


class NotEnoughHistory(HistoryError):
    """The snapshots do not reach back far enough for the comparison asked for."""


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
class AlertRule:
    """One ``--alert``: a counter that rose enough, or a kind of event."""

    text: str
    counter: str = ""
    amount: float = 0.0
    percent: bool = False


@dataclass(frozen=True)
class AlertHit:
    rule: str
    slug: str
    title: str
    detail: str


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
    alert_rules: tuple[str, ...] = ()
    alerts: list[AlertHit] = field(default_factory=list)

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
    if not create:
        _check(connection, path, create)
        return
    # Two first snapshots at once (two scheduled jobs) must not both find an empty
    # file and create the schema: the check and the creation run under one write
    # lock, and the second sees the first's tables.
    connection.execute("BEGIN IMMEDIATE")
    try:
        _check(connection, path, create)
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


def _check(connection: sqlite3.Connection, path: Path, create: bool) -> None:
    """Accept a pb-history file, or (with ``create``) give an empty one the schema."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        # The version number alone proves nothing: any SQLite file can carry it.
        if not _has_expected_schema(connection):
            raise HistoryError(f"{path} is a SQLite file, but not a pb-history file")
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
    # Statement by statement: executescript() would commit first and drop the lock.
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _has_expected_schema(connection: sqlite3.Connection) -> bool:
    for table, columns in _EXPECTED_COLUMNS.items():
        found = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        if found != columns:
            return False
    return True


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
    try:
        taken_at = datetime.fromisoformat(row[1])
    except (TypeError, ValueError):
        raise HistoryError(f"snapshot {row[0]} has an unreadable time: {row[1]!r}") from None
    return Snapshot(int(row[0]), taken_at, row[2], row[3], int(row[4]))


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
        f"SELECT s.taken_at, COUNT(o.slug), {sums} FROM snapshots s "  # noqa: S608
        "LEFT JOIN observations o ON o.snapshot_id = s.id "
        "WHERE s.profile = ? AND s.item_type = ? GROUP BY s.id ORDER BY s.taken_at, s.id",
        (profile, item_type),
    )
    return [
        (datetime.fromisoformat(row[0]),
         {"listings": int(row[1]), **{c: int(row[2 + i] or 0) for i, c in enumerate(COUNTERS)}})
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
        raise NotEnoughHistory(
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
        try:
            cutoff = latest.taken_at - timedelta(days=days)
        except (OverflowError, ValueError) as exc:
            raise HistoryError(f"--days {days:g} is out of range") from exc
        candidates = [snap for snap in earlier if snap.taken_at <= cutoff]
        if not candidates:
            oldest = (latest.taken_at - earlier[0].taken_at).total_seconds() / 86400
            raise NotEnoughHistory(
                f"no snapshot is {days:g} days older than the latest; "
                f"the oldest is {oldest:.1f} days older"
            )
        return candidates[-1], latest
    return earlier[-1], latest


def _totals(observations: dict[str, Observation]) -> dict[str, float]:
    values: dict[str, float] = {"listings": float(len(observations))}
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
    alerts: Sequence[AlertRule] = (),
) -> Report:
    if metric not in COUNTERS:
        raise HistoryError(f"unknown metric {metric!r}: use one of {', '.join(COUNTERS)}")
    if top < 1:
        raise HistoryError("the number of movers to list must be at least 1")
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
        alert_rules=tuple(rule.text for rule in alerts),
        alerts=evaluate_alerts(alerts, before, after),
    )


def parse_alert(text: str) -> AlertRule:
    """``views+50%``, ``sales+1``, or one of ``new``, ``removed``, ``price``."""
    rule = text.strip().lower()
    if rule in ALERT_EVENTS:
        return AlertRule(rule)
    match = _ALERT_RULE.fullmatch(rule)
    if not match or float(match["amount"]) <= 0:
        raise HistoryError(
            f"invalid alert {text!r}: use COUNTER+N or COUNTER+N% with N above 0 "
            f"(counters: {', '.join(COUNTERS)}), or one of {', '.join(ALERT_EVENTS)}"
        )
    return AlertRule(rule, match["counter"], float(match["amount"]), bool(match["percent"]))


def evaluate_alerts(
    rules: Sequence[AlertRule],
    before: dict[str, Observation],
    after: dict[str, Observation],
) -> list[AlertHit]:
    """Every listing that meets a rule, in rule order, then by slug.

    A percentage needs a baseline above zero: a listing that had none before has no
    meaningful rate of growth, so an absolute rule covers it.
    """
    hits: list[AlertHit] = []
    common = sorted(set(before) & set(after))
    for rule in rules:
        if rule.text == "new":
            hits += [AlertHit(rule.text, slug, after[slug].title, "new listing")
                     for slug in sorted(set(after) - set(before))]
        elif rule.text == "removed":
            hits += [AlertHit(rule.text, slug, before[slug].title, "no longer listed")
                     for slug in sorted(set(before) - set(after))]
        elif rule.text == "price":
            for slug in common:
                old, new = before[slug], after[slug]
                if (old.price, old.discount) != (new.price, new.discount):
                    hits.append(AlertHit(
                        rule.text, slug, new.title,
                        f"price {_number(old.price)} to {_number(new.price)}, "
                        f"discount {_number(old.discount)} to {_number(new.discount)}",
                    ))
        else:
            for slug in common:
                old = getattr(before[slug], rule.counter)
                new = getattr(after[slug], rule.counter)
                gain = new - old
                if gain <= 0:
                    continue
                if rule.percent:
                    if old <= 0 or gain * 100 < rule.amount * old:
                        continue
                elif gain < rule.amount:
                    continue
                rate = f", +{gain * 100 / old:.0f}%" if old > 0 else ""
                hits.append(AlertHit(rule.text, slug, after[slug].title,
                                     f"{rule.counter} {old} to {new} (+{gain}{rate})"))
    return hits


GROWTH_COUNTERS = ("sales", "views", "favorites")


@dataclass(frozen=True)
class ProfileSummary:
    """One profile's latest snapshot, reduced to figures that compare across profiles."""

    profile: str
    taken_at: datetime
    listings: int
    totals: dict[str, int]
    average_rating: float
    median_price: float
    free_share: float
    sales_per_listing: float
    # Gain per day over the window, or None when the history does not reach back.
    growth: dict[str, float] | None = None
    growth_days: float = 0.0


def summarize(
    connection: sqlite3.Connection,
    item_type: str,
    profiles: Sequence[str] = (),
    *,
    days: float | None = None,
) -> list[ProfileSummary]:
    """Every profile in the file (or ``profiles``) side by side, most sales first."""
    kind = ITEM_TYPE_PLURALS[item_type]
    if days is not None:
        try:
            timedelta(days=days)
        except (OverflowError, ValueError) as exc:
            raise HistoryError(f"--days {days:g} is out of range") from exc
    snapshots = list_snapshots(connection, None, item_type)
    known = sorted({snap.profile for snap in snapshots})
    if not known:
        # An empty table would hide a wrong --item-type or file, and overwrite a
        # scheduled output with nothing.
        raise HistoryError(f"the file holds no snapshots of {kind}")
    try:
        wanted = list(dict.fromkeys(parse_profile_input(name) for name in profiles)) or known
    except PromptBaseError as exc:
        raise HistoryError(f"invalid --profile: {exc}") from None
    missing = [name for name in wanted if name not in known]
    if missing:
        raise HistoryError(
            f"no snapshots of {kind} for " + ", ".join(f"@{name}" for name in missing)
        )
    summaries = []
    for name in wanted:
        own = [snap for snap in snapshots if snap.profile == name]
        latest = own[-1]
        observations = load_observations(connection, latest.id)
        totals = {c: int(_totals(observations)[c]) for c in COUNTERS}
        prices = sorted(o.price for o in observations.values())
        rated = [o.rating for o in observations.values() if o.rating > 0]
        count = len(observations)
        growth, growth_days = None, 0.0
        if days is not None:
            try:
                baseline, _ = choose_baseline(own, days=days)
            except NotEnoughHistory:
                pass  # shown as n/a for this profile
            else:
                elapsed = (latest.taken_at - baseline.taken_at).total_seconds() / 86400
                # Two snapshots in the same second (possible with --days 0) give no rate.
                if elapsed > 0:
                    growth_days = elapsed
                    before = _totals(load_observations(connection, baseline.id))
                    growth = {c: (totals[c] - before[c]) / elapsed for c in GROWTH_COUNTERS}
        summaries.append(ProfileSummary(
            profile=name,
            taken_at=latest.taken_at,
            listings=count,
            totals=totals,
            average_rating=sum(rated) / len(rated) if rated else 0.0,
            median_price=_median(prices),
            free_share=sum(1 for price in prices if price == 0) / count if count else 0.0,
            sales_per_listing=totals["sales"] / count if count else 0.0,
            growth=growth,
            growth_days=growth_days,
        ))
    summaries.sort(key=lambda summary: (-summary.totals["sales"], summary.profile))
    return summaries


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _comparison_rows(summaries: Sequence[ProfileSummary], days: float | None) -> list[list[str]]:
    """Header and one row per profile, as text, shared by the Markdown and HTML tables."""
    header = ["Profile", "Listings", "Views", "Sales", "Favorites", "Reviews", "Rating",
              "Median price", "Free", "Sales per listing"]
    if days is not None:
        header += [f"{counter.capitalize()} per day" for counter in GROWTH_COUNTERS]
    rows = [header]
    for summary in summaries:
        row = [
            f"@{summary.profile}", str(summary.listings),
            *(str(summary.totals[c]) for c in ("views", "sales", "favorites", "reviews")),
            f"{summary.average_rating:.2f}", _number(round(summary.median_price, 2)),
            f"{summary.free_share:.0%}", f"{summary.sales_per_listing:.2f}",
        ]
        if days is not None:
            row += ([f"{summary.growth[c]:.1f}" for c in GROWTH_COUNTERS] if summary.growth
                    else ["n/a"] * len(GROWTH_COUNTERS))
        rows.append(row)
    return rows


def render_comparison_markdown(
    summaries: Sequence[ProfileSummary], item_type: str, days: float | None = None,
) -> str:
    rows = _comparison_rows(summaries, days)
    lines = [f"# PromptBase profiles compared ({ITEM_TYPE_PLURALS[item_type]})", "",
             "Each profile's latest snapshot."]
    if days is not None:
        lines[-1] += (f" Per-day figures cover at least {days:g} days; n/a means the "
                      "history does not reach back that far.")
    lines += ["", "| " + " | ".join(rows[0]) + " |",
              "| --- |" + " ---: |" * (len(rows[0]) - 1)]
    lines += ["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows[1:]]
    return "\n".join(lines) + "\n"


def comparison_to_dict(
    summaries: Sequence[ProfileSummary], item_type: str, days: float | None = None,
) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "days": days,
        "profiles": [
            {
                "profile": summary.profile,
                "taken_at": summary.taken_at.isoformat(),
                "listings": summary.listings,
                "totals": summary.totals,
                "average_rating": round(summary.average_rating, 4),
                "median_price": summary.median_price,
                "free_share": round(summary.free_share, 4),
                "sales_per_listing": round(summary.sales_per_listing, 4),
                "per_day": (
                    {c: round(v, 4) for c, v in summary.growth.items()}
                    if summary.growth is not None else None
                ),
                "per_day_over_days": round(summary.growth_days, 3) if summary.growth else None,
            }
            for summary in summaries
        ],
    }


def render_comparison_html(
    summaries: Sequence[ProfileSummary], item_type: str, days: float | None = None,
) -> str:
    def h(value: object) -> str:
        return html.escape(printable(value))

    rows = _comparison_rows(summaries, days)
    title = f"PromptBase profiles compared ({ITEM_TYPE_PLURALS[item_type]})"
    head = "".join(f"<th>{h(cell)}</th>" for cell in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{h(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:]
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{h(title)}</title><style>{_PAGE_STYLE}</style></head><body>"
        f"<h1>{h(title)}</h1><p>Each profile's latest snapshot.</p>"
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div></body></html>\n"
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
    """``2000100``, ``4.95``, ``0.0000004``: in full, never ``2.0001e+06``."""
    if value == int(value):
        return str(int(value))
    # repr is the shortest text that reads back as ``value``; Decimal only drops
    # its exponent, so no digit is rounded away.
    return format(Decimal(repr(value)), "f")


def _delta(before: float, after: float) -> str:
    change = after - before
    if change == 0:
        return "0"
    return ("+" if change > 0 else "-") + _number(abs(change))


def _code(text: object) -> str:
    """A code span: nothing inside is interpreted, so only pipes and backticks matter.

    The fence is one backtick longer than the longest run inside, so no run of
    backticks in a slug can close the span early.
    """
    flat = printable(text).replace("|", "\\|")
    longest = max((len(run) for run in re.findall("`+", flat)), default=0)
    if not longest:
        return f"`{flat}`"
    fence = "`" * (longest + 1)
    return f"{fence} {flat} {fence}"


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
    ]
    if report.alert_rules:
        lines += [f"## Alerts ({len(report.alerts)})", ""]
        lines += [
            f"- {_code(hit.rule)}: {_cell(hit.title)} ({_code(hit.slug)}): {_cell(hit.detail)}"
            for hit in report.alerts
        ] or [f"None of the rules fired ({', '.join(report.alert_rules)})."]
        lines.append("")
    lines += [
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
            lines.append(f"| {_cell(mover.title)} ({_code(mover.slug)}) | "
                         f"{mover.change[report.metric]:+d} | {others} | {_number(mover.price)} |")
    else:
        lines.append(f"No {kind} gained {report.metric} in this period.")
    lines += ["", f"## New {kind} ({len(report.new)})", ""]
    lines += [f"- {_cell(o.title)} ({_code(o.slug)})" for o in report.new] or ["None."]
    lines += ["", f"## Removed {kind} ({len(report.removed)})", ""]
    lines += [f"- {_cell(o.title)} ({_code(o.slug)})" for o in report.removed] or ["None."]
    lines += ["", f"## Price changes ({len(report.price_changes)})", ""]
    lines += [
        f"- {_cell(after.title)} ({_code(after.slug)}): price {_number(before.price)} to "
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
        "alerts": {
            "rules": list(report.alert_rules),
            "hits": [{"rule": hit.rule, "slug": hit.slug, "title": hit.title,
                      "detail": hit.detail} for hit in report.alerts],
        },
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


_PAGE_STYLE = (
    ":root{color-scheme:light dark;--line:#8884}body{font:16px/1.5 system-ui,sans-serif;"
    "max-width:920px;margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;width:100%}"
    "td,th{border-top:1px solid var(--line);padding:.4rem .5rem;text-align:right}"
    "td:first-child,th:first-child{text-align:left}.chart{width:100%;max-width:360px;height:auto}"
    "figure{display:inline-block;margin:0 1rem 1rem 0}code{font-size:.85em}"
    ".scroll{overflow-x:auto}figcaption{font-size:.85em}"
)


def render_html(report: Report) -> str:
    def h(value: object) -> str:
        return html.escape(printable(value))

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
    alerts = ""
    if report.alert_rules:
        alerts = f"<h2>Alerts ({len(report.alerts)})</h2><ul>" + ("".join(
            f"<li><code>{h(hit.rule)}</code>: {h(hit.title)} <code>{h(hit.slug)}</code>: "
            f"{h(hit.detail)}</li>" for hit in report.alerts
        ) or f"<li>None of the rules fired ({h(', '.join(report.alert_rules))}).</li>") + "</ul>"
    charts = "".join(
        f"<figure><figcaption>{h(c)}{h(_span(report.series, c))}</figcaption>"
        f"{_chart(report.series, c)}</figure>"
        for c in ("views", "sales", "favorites")
    )
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>PromptBase trends: {h(_heading(report))}</title><style>"
        f"{_PAGE_STYLE}"
        "</style></head><body>"
        f"<h1>PromptBase trends: {h(_heading(report))}</h1>"
        f"<p>From snapshot {report.baseline.id} ({h(report.baseline.taken_at.isoformat())}) to "
        f"snapshot {report.latest.id} ({h(report.latest.taken_at.isoformat())}): "
        f"{h(describe_duration(report.days))}.</p>"
        f"{alerts}{charts}"
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


def _days(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be a finite number, 0 or more: {text!r}")
    return value


def _alert(text: str) -> AlertRule:
    try:
        return parse_alert(text)
    except HistoryError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more: {text!r}")
    return value


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
    window.add_argument("--days", type=_days, help="Compare with a snapshot at least this old.")
    report.add_argument("--metric", choices=COUNTERS, default="sales")
    report.add_argument("--top", type=_positive_int, default=10,
                        help="How many top movers to list (default 10).")
    report.add_argument("--format", choices=REPORT_FORMATS, default="markdown")
    report.add_argument("-o", "--output", type=Path, help="Write the report here, not to stdout.")
    report.add_argument(
        "--alert", action="append", type=_alert, default=[], metavar="RULE",
        help="Exit with code 2 when a listing meets RULE: views+50%%, sales+1, new, removed, "
             "or price. Repeatable.",
    )

    compare = commands.add_parser("compare", help="Compare profiles side by side.")
    compare.add_argument("--db", type=Path, required=True)
    compare.add_argument("--item-type", choices=ITEM_TYPES, default="prompt")
    compare.add_argument("--profile", action="append", default=[], dest="profiles",
                         help="A profile to include (repeatable); every profile by default.")
    compare.add_argument("--days", type=_days,
                         help="Add per-day growth over at least this many days.")
    compare.add_argument("--format", choices=REPORT_FORMATS, default="markdown")
    compare.add_argument("-o", "--output", type=Path, help="Write it here, not to stdout.")

    listing = commands.add_parser("list", help="List the snapshots in a history file.")
    listing.add_argument("--db", type=Path, required=True)
    return parser


def _snapshot_command(args: argparse.Namespace) -> int:
    failed = False
    connection = connect(args.db, create=True)
    try:
        requested: set[str] = set()
        recorded: set[str] = set()
        for profile_input in args.profiles:
            try:
                # "@acb", "acb" and the profile URL are one profile: one snapshot per run,
                # or the report would compare a run with itself.
                wanted = parse_profile_input(profile_input).lower()
                if wanted in requested:
                    continue
                requested.add(wanted)
                profile, records = fetch_prompts(profile_input, item_type=args.item_type)
            except PromptBaseError as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed = True
                continue
            if profile.username.lower() in recorded:
                continue
            recorded.add(profile.username.lower())
            if not records and not _has_earlier_listings(
                connection, profile.username, args.item_type
            ):
                # With nothing earlier, an empty snapshot is more likely a wrong kind or
                # profile than a real change, and a later report could not use it.
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


def _has_earlier_listings(connection: sqlite3.Connection, profile: str, item_type: str) -> bool:
    return any(
        snap.record_count > 0 for snap in list_snapshots(connection, profile, item_type)
    )


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
            metric=args.metric, top=args.top,
            series=totals_series(connection, latest.profile, latest.item_type),
            alerts=args.alert,
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
    if report.alerts:
        print(f"{len(report.alerts)} alert(s) fired", file=sys.stderr)
        return EXIT_ALERT
    return EXIT_SUCCESS


def _compare_command(args: argparse.Namespace) -> int:
    connection = connect(args.db, create=False)
    try:
        summaries = summarize(connection, args.item_type, args.profiles, days=args.days)
    finally:
        connection.close()
    if args.format == "json":
        data = comparison_to_dict(summaries, args.item_type, args.days)
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    elif args.format == "html":
        text = render_comparison_html(summaries, args.item_type, args.days)
    else:
        text = render_comparison_markdown(summaries, args.item_type, args.days)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.format} comparison -> {args.output}")
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
    handlers = {"snapshot": _snapshot_command, "report": _report_command,
                "compare": _compare_command, "list": _list_command}
    try:
        return handlers[args.command](args)
    except (HistoryError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

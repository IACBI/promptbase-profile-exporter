import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from promptbase_exporter import history
from promptbase_exporter.client import PromptBaseError
from promptbase_exporter.models import Profile, PromptRecord
from tests.scratch import use_scratch_working_directory

START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def setUpModule():
    use_scratch_working_directory()


def rec(slug, *, title=None, price=1.0, discount=0.0, views=0, sales=0, downloads=0,
        favorites=0, rating=0.0, reviews=0):
    return PromptRecord(
        title=title or slug.title(), description="d", slug=slug, prompt_type="gpt",
        domain="text", created=1, price=price, discount=discount, views=views, sales=sales,
        downloads=downloads, favorites=favorites, rating=rating, reviews=reviews,
    )


def run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = history.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


def store(path, profile, steps, item_type="prompt"):
    """Record (days after START, records) snapshots into a history file."""
    connection = history.connect(Path(path), create=True)
    try:
        return [
            history.record_snapshot(connection, profile, records, item_type,
                                    START + timedelta(days=days))
            for days, records in steps
        ]
    finally:
        connection.close()


class StorageTests(unittest.TestCase):
    def test_a_snapshot_round_trips_and_is_listed_oldest_first(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "h.sqlite"
            snaps = store(path, "acb", [
                (2, [rec("b", views=5)]), (0, [rec("a", views=1), rec("c", sales=2)]),
            ])
            with closing(history.connect(path, create=False)) as connection:
                listed = history.list_snapshots(connection)
                observations = history.load_observations(connection, snaps[1].id)
        self.assertEqual([snap.record_count for snap in listed], [2, 1])  # ordered by time
        self.assertEqual(listed[0].taken_at, START)
        self.assertEqual(sorted(observations), ["a", "c"])
        self.assertEqual((observations["a"].views, observations["c"].sales), (1, 2))

    def test_the_file_carries_a_format_version(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            store(path, "acb", [(0, [rec("a")])])
            with closing(sqlite3.connect(path)) as raw:
                version = raw.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, history.SCHEMA_VERSION)

    def test_a_newer_format_is_refused(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            store(path, "acb", [(0, [rec("a")])])
            with closing(sqlite3.connect(path)) as raw:
                raw.execute("PRAGMA user_version = 99")
                raw.commit()
            with self.assertRaisesRegex(history.HistoryError, "history format 99"):
                history.connect(path, create=False)

    def test_a_foreign_sqlite_file_is_refused_not_modified(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "other.sqlite"
            with closing(sqlite3.connect(path)) as other:
                other.execute("CREATE TABLE notes (text TEXT)")
                other.execute("INSERT INTO notes VALUES ('keep me')")
                other.commit()
            for create in (True, False):
                with self.subTest(create=create), self.assertRaisesRegex(
                    history.HistoryError, "not a pb-history file"
                ):
                    history.connect(path, create=create)
            with closing(sqlite3.connect(path)) as other:
                rows = other.execute("SELECT text FROM notes").fetchall()
        self.assertEqual(rows, [("keep me",)])

    def test_missing_empty_and_corrupt_files_are_reported(self):
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "gone.sqlite"
            empty = Path(directory) / "empty.sqlite"
            empty.write_bytes(b"")
            corrupt = Path(directory) / "corrupt.sqlite"
            corrupt.write_bytes(b"this is not a database at all" * 10)
            with self.assertRaisesRegex(history.HistoryError, "not found"):
                history.connect(missing, create=False)
            with self.assertRaisesRegex(history.HistoryError, "take a snapshot first"):
                history.connect(empty, create=False)
            with self.assertRaises(history.HistoryError):
                history.connect(corrupt, create=False)

    def test_a_foreign_file_that_only_carries_our_version_number_is_refused(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "other.sqlite"
            with closing(sqlite3.connect(path)) as other:
                other.execute("CREATE TABLE snapshots (note TEXT)")
                other.execute(f"PRAGMA user_version = {history.SCHEMA_VERSION}")
                other.commit()
            for create in (True, False):
                with self.subTest(create=create), self.assertRaisesRegex(
                    history.HistoryError, "not a pb-history file"
                ):
                    history.connect(path, create=create)
            with closing(sqlite3.connect(path)) as other:
                columns = [row[1] for row in other.execute("PRAGMA table_info(snapshots)")]
        self.assertEqual(columns, ["note"])

    def test_values_with_sql_metacharacters_are_stored_as_data(self):
        nasty = "x'); DROP TABLE observations; --"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            store(path, "acb\"; DROP TABLE snapshots;--", [(0, [rec("a", title=nasty)])])
            with closing(history.connect(path, create=False)) as connection:
                snap = history.list_snapshots(connection)[0]
                title = history.load_observations(connection, snap.id)["a"].title
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(title, nasty)
        self.assertEqual(snap.profile, "acb\"; DROP TABLE snapshots;--")
        self.assertIn("observations", tables)
        self.assertIn("snapshots", tables)

    def test_filters_by_profile_and_kind(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            store(path, "acb", [(0, [rec("a")])])
            store(path, "acb", [(1, [rec("kit")])], item_type="bundle")
            store(path, "other", [(2, [rec("z")])])
            with closing(history.connect(path, create=False)) as connection:
                counts = (
                    len(history.list_snapshots(connection)),
                    len(history.list_snapshots(connection, "acb")),
                    len(history.list_snapshots(connection, "acb", "bundle")),
                    len(history.list_snapshots(connection, None, "prompt")),
                )
        self.assertEqual(counts, (3, 2, 1, 2))

    def test_totals_series_sums_every_counter_per_snapshot(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            store(path, "acb", [
                (0, [rec("a", views=1, sales=1), rec("b", views=2)]),
                (1, [rec("a", views=10, sales=3), rec("b", views=2)]),
            ])
            with closing(history.connect(path, create=False)) as connection:
                series = history.totals_series(connection, "acb", "prompt")
        self.assertEqual([point[1]["views"] for point in series], [3, 12])
        self.assertEqual([point[1]["sales"] for point in series], [1, 3])
        self.assertEqual(series[0][1]["listings"], 2)


class BaselineTests(unittest.TestCase):
    def snaps(self, days):
        return [
            history.Snapshot(i + 1, START + timedelta(days=d), "acb", "prompt", 1)
            for i, d in enumerate(days)
        ]

    def test_default_is_the_snapshot_before_the_latest(self):
        base, latest = history.choose_baseline(self.snaps([0, 3, 9]))
        self.assertEqual((base.id, latest.id), (2, 3))

    def test_since_picks_the_first_snapshot_on_or_after_the_date(self):
        snaps = self.snaps([0, 3, 9, 20])
        base, _ = history.choose_baseline(snaps, since=date(2026, 1, 4))  # 3 days after the start
        self.assertEqual(base.id, 2)
        base, _ = history.choose_baseline(snaps, since=date(2026, 1, 5))
        self.assertEqual(base.id, 3)
        with self.assertRaisesRegex(history.HistoryError, "no snapshot between"):
            history.choose_baseline(snaps, since=date(2026, 3, 1))

    def test_days_picks_the_latest_snapshot_old_enough(self):
        snaps = self.snaps([0, 3, 9, 20])
        self.assertEqual(history.choose_baseline(snaps, days=10)[0].id, 3)  # 11 days older
        self.assertEqual(history.choose_baseline(snaps, days=20)[0].id, 1)
        with self.assertRaisesRegex(history.HistoryError, "oldest is 20.0 days older"):
            history.choose_baseline(snaps, days=25)

    def test_fewer_than_two_snapshots_cannot_be_compared(self):
        for count in (0, 1):
            with self.subTest(count=count), self.assertRaisesRegex(
                history.HistoryError, f"found {count}"
            ):
                history.choose_baseline(self.snaps(range(count)))

    def test_durations_read_naturally(self):
        cases = {45 / 86400: "45 seconds", 10 * 60 / 86400: "10 minutes",
                 3.5 / 24: "3.5 hours", 12.04: "12.0 days"}
        for days, text in cases.items():
            self.assertEqual(history.describe_duration(days), text)


class ReportTests(unittest.TestCase):
    def report(self, before, after, **options):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            base, latest = store(path, "acb", [(0, before), (7, after)])
            with closing(history.connect(path, create=False)) as connection:
                report = history.build_report(
                    base, latest, history.load_observations(connection, base.id),
                    history.load_observations(connection, latest.id), **options,
                )
        return report

    def test_totals_movers_new_removed_and_price_changes(self):
        before = [rec("a", views=10, sales=1, rating=4.0), rec("b", views=5, sales=2),
                  rec("gone", views=1), rec("p", price=5.0)]
        after = [rec("a", views=25, sales=4, rating=5.0), rec("b", views=5, sales=2),
                 rec("new", views=3), rec("p", price=3.0, discount=0.2)]
        report = self.report(before, after)
        self.assertEqual(report.totals["views"], (16.0, 33.0))  # 10+5+1, then 25+5+3
        self.assertEqual(report.totals["sales"], (3.0, 6.0))
        self.assertEqual(report.totals["listings"], (4.0, 4.0))
        self.assertEqual(report.totals["average rating"], (4.0, 5.0))  # rated ones only
        self.assertEqual([m.slug for m in report.movers], ["a"])
        self.assertEqual(report.movers[0].change["sales"], 3)
        self.assertEqual(report.movers[0].change["views"], 15)
        self.assertEqual([o.slug for o in report.new], ["new"])
        self.assertEqual([o.slug for o in report.removed], ["gone"])
        self.assertEqual([(b.price, a.price, a.discount) for b, a in report.price_changes],
                         [(5.0, 3.0, 0.2)])
        self.assertAlmostEqual(report.days, 7.0)

    def test_a_discount_alone_counts_as_a_price_change(self):
        report = self.report([rec("a", price=5.0)], [rec("a", price=5.0, discount=0.3)])
        self.assertEqual([(b.discount, a.discount) for b, a in report.price_changes], [(0.0, 0.3)])
        unchanged = self.report([rec("a", price=5.0)], [rec("a", price=5.0)])
        self.assertEqual(unchanged.price_changes, [])

    def test_movers_are_ranked_by_the_metric_with_ties_broken_by_slug(self):
        before = [rec(s) for s in "abcd"]
        after = [rec("a", sales=2), rec("b", sales=5), rec("c", sales=2),
                 rec("d", sales=0, views=9)]
        by_sales = self.report(before, after, metric="sales")
        self.assertEqual([m.slug for m in by_sales.movers], ["b", "a", "c"])  # d gained none
        by_views = self.report(before, after, metric="views")
        self.assertEqual([m.slug for m in by_views.movers], ["d"])
        self.assertEqual([m.slug for m in self.report(before, after, top=2).movers], ["b", "a"])
        with self.assertRaisesRegex(history.HistoryError, "at least 1"):
            self.report(before, after, top=0)

    def test_a_counter_that_fell_is_not_a_mover_but_shows_in_the_totals(self):
        report = self.report([rec("a", sales=5)], [rec("a", sales=3)])
        self.assertEqual(report.movers, [])
        self.assertEqual(report.totals["sales"], (5.0, 3.0))

    def test_an_unknown_metric_is_an_error(self):
        with self.assertRaisesRegex(history.HistoryError, "unknown metric 'nope'"):
            self.report([rec("a")], [rec("a")], metric="nope")

    def test_nothing_changed(self):
        report = self.report([rec("a", views=3)], [rec("a", views=3)])
        text = history.render_markdown(report)
        self.assertIn("No prompts gained sales in this period.", text)
        self.assertEqual((report.new, report.removed, report.price_changes), ([], [], []))


class RenderTests(unittest.TestCase):
    def make(self, **titles):
        title = titles.get("title", "Plain")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "h.sqlite"
            base, latest, _third = store(path, "acb", [
                (0, [rec("a", title=title, sales=1), rec("gone", title="Gone")]),
                (7, [rec("a", title=title, sales=4, views=9), rec("new", title="New one")]),
                (9, [rec("a", title=title, sales=6, views=12), rec("new", title="New one")]),
            ])
            with closing(history.connect(path, create=False)) as connection:
                report = history.build_report(
                    base, latest, history.load_observations(connection, base.id),
                    history.load_observations(connection, latest.id),
                    series=history.totals_series(connection, "acb", "prompt"),
                )
        return report

    def test_markdown_has_each_section(self):
        text = history.render_markdown(self.make())
        for heading in ("# PromptBase trends: @acb (prompts)", "## Top movers by sales",
                        "## New prompts (1)", "## Removed prompts (1)", "## Price changes (0)"):
            self.assertIn(heading, text)
        self.assertIn("| sales | 1 | 4 | +3 |", text)
        self.assertIn("- New one (`new`)", text)
        self.assertIn("7.0 days", text)

    def test_markdown_table_cells_cannot_break_out(self):
        text = history.render_markdown(self.make(title="evil | title\nwith newline"))
        self.assertIn("evil \\| title with newline", text)
        self.assertNotIn("\nwith newline", text)

    def test_markdown_titles_cannot_become_html_or_links(self):
        title = '<img src=x onerror=alert(1)> [click](javascript:alert(1)) *bold* & more'
        text = history.render_markdown(self.make(title=title))
        self.assertIn(r"\<img src=x onerror=alert(1)\>", text)
        self.assertIn(r"\[click\]", text)
        self.assertIn(r"\*bold\* \& more", text)
        self.assertNotRegex(text, r"(?<!\\)[<>]")  # every angle bracket is escaped

    def test_a_slug_with_backticks_or_pipes_stays_inside_its_code_span(self):
        self.assertEqual(history._code("we`ird|slug"), r"`` we`ird\|slug ``")
        self.assertEqual(history._code("plain  slug"), "`plain slug`")
        # A double backtick inside must not close the span and expose a link.
        self.assertEqual(history._code("a``[x](https://e)``b"), "``` a``[x](https://e)``b ```")

    def test_the_count_row_is_labelled_listings_for_every_kind(self):
        report = self.make()
        self.assertIn("| listings |", history.render_markdown(report))
        self.assertIn("listings", json.loads(history.render_json(report))["totals"])
        self.assertIn("<td>listings</td>", history.render_html(report))
        self.assertNotIn("| prompts |", history.render_markdown(report))

    def test_json_is_valid_and_complete(self):
        data = json.loads(history.render_json(self.make()))
        self.assertEqual(
            (data["profile"], data["item_type"], data["metric"]), ("acb", "prompt", "sales")
        )
        self.assertEqual(data["totals"]["sales"], {"before": 1.0, "after": 4.0, "change": 3.0})
        self.assertEqual(data["movers"][0]["slug"], "a")
        self.assertEqual([item["slug"] for item in data["new"]], ["new"])
        self.assertEqual(data["baseline"]["id"], 1)

    def test_html_is_escaped_and_self_contained(self):
        page = history.render_html(self.make(title='<script>alert(1)</script> & "q"'))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot;", page)
        self.assertIn("<svg", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))  # nothing is fetched
        self.assertNotIn("<script", page)
        self.assertEqual(page.count("<polyline"), 3)  # views, sales, favorites

    def test_html_charts_say_where_they_start_and_end_and_wide_tables_scroll(self):
        page = history.render_html(self.make())
        self.assertIn("views: 0 to 12 over 3 snapshots", page)
        self.assertIn("sales: 1 to 6 over 3 snapshots", page)
        self.assertEqual(page.count('<div class="scroll"><table>'), 2)  # totals and movers
        self.assertIn(".scroll{overflow-x:auto}", page)

    def test_a_single_point_series_draws_no_chart(self):
        one_point = [(START, {"views": 1, "sales": 1, "favorites": 0})]
        self.assertEqual(history._chart(one_point, "views"), "")

    def test_render_dispatch(self):
        report = self.make()
        self.assertTrue(history.render(report, "markdown").startswith("# PromptBase trends"))
        self.assertTrue(history.render(report, "json").lstrip().startswith("{"))
        self.assertTrue(history.render(report, "html").startswith("<!doctype html>"))
        with self.assertRaisesRegex(history.HistoryError, "unknown report format"):
            history.render(report, "pdf")


class AlertTests(unittest.TestCase):
    report = ReportTests.report

    def hits(self, before, after, *rules):
        report = self.report(before, after, alerts=[history.parse_alert(r) for r in rules])
        return [(hit.rule, hit.slug) for hit in report.alerts]

    def test_rules_are_parsed_and_bad_ones_rejected(self):
        rule = history.parse_alert("Views+50%")
        self.assertEqual((rule.counter, rule.amount, rule.percent), ("views", 50.0, True))
        self.assertEqual(history.parse_alert("sales+1").percent, False)
        self.assertEqual(history.parse_alert("new").counter, "")
        for bad in ("views", "views+0", "views-5", "rating+1", "views+5%%", "everything", ""):
            with self.subTest(rule=bad), self.assertRaisesRegex(history.HistoryError, "invalid"):
                history.parse_alert(bad)

    def test_an_absolute_rule_counts_the_gain(self):
        before = [rec("a", sales=1), rec("b", sales=3), rec("c", sales=5)]
        after = [rec("a", sales=2), rec("b", sales=3), rec("c", sales=4)]
        self.assertEqual(self.hits(before, after, "sales+1"), [("sales+1", "a")])
        self.assertEqual(self.hits(before, after, "sales+2"), [])

    def test_a_percentage_needs_a_baseline_above_zero(self):
        before = [rec("a", views=100), rec("b", views=100), rec("c", views=0)]
        after = [rec("a", views=150), rec("b", views=149), rec("c", views=40)]
        self.assertEqual(self.hits(before, after, "views+50%"), [("views+50%", "a")])
        # From zero there is no rate; an absolute rule catches it.
        self.assertIn(("views+40", "c"), self.hits(before, after, "views+40"))

    def test_events_and_rule_order(self):
        before = [rec("a"), rec("gone"), rec("p", price=2.0)]
        after = [rec("a", favorites=1), rec("fresh"), rec("p", price=3.0)]
        self.assertEqual(
            self.hits(before, after, "removed", "new", "price", "favorites+1"),
            [("removed", "gone"), ("new", "fresh"), ("price", "p"), ("favorites+1", "a")],
        )

    def test_each_format_shows_the_alerts_first_and_escaped(self):
        before = [rec("a", sales=1, title="<b>Bold</b> [x](y)")]
        after = [rec("a", sales=3, title="<b>Bold</b> [x](y)")]
        report = self.report(before, after, alerts=[history.parse_alert("sales+1")])
        markdown = history.render_markdown(report)
        self.assertLess(markdown.index("## Alerts (1)"), markdown.index("| Measure |"))
        self.assertIn(r"\<b\>Bold\</b\> \[x\](y)", markdown)
        self.assertIn("sales 1 to 3 (+2, +200%)", markdown)
        data = json.loads(history.render_json(report))
        self.assertEqual(data["alerts"]["rules"], ["sales+1"])
        self.assertEqual(data["alerts"]["hits"][0]["slug"], "a")
        page = history.render_html(report)
        self.assertIn("<h2>Alerts (1)</h2>", page)
        self.assertNotIn("<b>Bold</b>", page)

    def test_rules_that_do_not_fire_are_reported_as_such(self):
        report = self.report([rec("a")], [rec("a")], alerts=[history.parse_alert("new")])
        self.assertIn("None of the rules fired (new).", history.render_markdown(report))
        plain = self.report([rec("a")], [rec("a")])
        self.assertNotIn("Alerts", history.render_markdown(plain))
        self.assertEqual(json.loads(history.render_json(plain))["alerts"],
                         {"rules": [], "hits": []})


class CompareTests(unittest.TestCase):
    def setUp(self):
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db = Path(self._directory.name) / "h.sqlite"
        store(self.db, "small", [
            (0, [rec("a", price=0.0, sales=1, views=10, rating=4.0)]),
            (2, [rec("a", price=0.0, sales=3, views=30, rating=4.0), rec("b", price=4.0)]),
        ])
        store(self.db, "big", [(2, [rec("x", price=1.0, sales=9), rec("y", price=3.0, sales=1),
                                    rec("z", price=5.0, sales=0, rating=5.0)])])

    def summaries(self, *profiles, days=None):
        with closing(history.connect(self.db, create=False)) as connection:
            return history.summarize(connection, "prompt", profiles, days=days)

    def test_profiles_are_summarized_and_ordered_by_sales(self):
        big, small = self.summaries()
        self.assertEqual((big.profile, small.profile), ("big", "small"))
        self.assertEqual((big.listings, big.totals["sales"], big.median_price), (3, 10, 3.0))
        self.assertEqual((small.listings, small.median_price, small.free_share), (2, 2.0, 0.5))
        self.assertAlmostEqual(big.sales_per_listing, 10 / 3)
        self.assertEqual(small.average_rating, 4.0)  # unrated listings are left out

    def test_growth_is_per_day_and_missing_history_is_none(self):
        big, small = self.summaries(days=1)
        self.assertIsNone(big.growth)  # a single snapshot reaches back no time at all
        self.assertEqual(small.growth, {"sales": 1.0, "views": 10.0, "favorites": 0.0})
        self.assertEqual(small.growth_days, 2.0)

    def test_profiles_can_be_chosen_by_any_spelling_and_must_exist(self):
        self.assertEqual([s.profile for s in self.summaries("@small", "small")], ["small"])
        with self.assertRaisesRegex(history.HistoryError, "no snapshots of prompts for @nobody"):
            self.summaries("small", "nobody")

    def test_each_format(self):
        summaries = self.summaries(days=1)
        markdown = history.render_comparison_markdown(summaries, "prompt", 1)
        self.assertIn("| @small | 2 | 30 | 3 |", markdown)
        self.assertIn("| n/a | n/a | n/a |", markdown)
        data = json.loads(json.dumps(history.comparison_to_dict(summaries, "prompt", 1)))
        self.assertEqual([p["profile"] for p in data["profiles"]], ["big", "small"])
        self.assertIsNone(data["profiles"][0]["per_day"])
        self.assertEqual(data["profiles"][1]["per_day"]["sales"], 1.0)
        page = history.render_comparison_html(summaries, "prompt", 1)
        self.assertIn("<td>@small</td>", page)
        self.assertNotIn("<script", page)

    def test_two_snapshots_in_the_same_second_give_no_rate(self):
        store(self.db, "twin", [(0, [rec("t", sales=1)]), (0, [rec("t", sales=2)])])
        twin = next(s for s in self.summaries(days=0) if s.profile == "twin")
        self.assertIsNone(twin.growth)

    def test_bad_input_is_an_error_not_a_traceback_or_an_empty_table(self):
        for profile in ("https://example.com/x", "   "):
            with self.subTest(profile=profile), \
                    self.assertRaisesRegex(history.HistoryError, "invalid --profile"):
                self.summaries(profile)
        with self.assertRaisesRegex(history.HistoryError, "out of range"):
            self.summaries(days=1e308)
        with closing(history.connect(self.db, create=False)) as connection, \
                self.assertRaisesRegex(history.HistoryError, "no snapshots of bundles"):
            history.summarize(connection, "bundle")
        for argv in (["--profile", "https://example.com/x"], ["--days", "1e308"],
                     ["--item-type", "app"]):
            with self.subTest(argv=argv):
                code, _, err = run(["compare", "--db", str(self.db), *argv])
                self.assertEqual(code, 1)
                self.assertIn("error:", err)
                self.assertNotIn("Traceback", err)

    def test_profile_names_are_escaped(self):
        store(self.db, "<b>x</b>|y", [(0, [rec("q")])])
        summaries = self.summaries()
        self.assertNotIn("<b>", history.render_comparison_html(summaries, "prompt"))
        self.assertIn(r"@\<b\>x\</b\>\|y", history.render_comparison_markdown(summaries, "prompt"))

    def test_command(self):
        code, out, _ = run(["compare", "--db", str(self.db), "--days", "1"])
        target = Path(self._directory.name) / "c.json"
        json_code, _, _ = run(["compare", "--db", str(self.db), "--profile", "small",
                               "--format", "json", "-o", str(target)])
        written = json.loads(target.read_text(encoding="utf-8"))
        missing, _, err = run(["compare", "--db", str(self.db), "--profile", "nobody"])
        self.assertEqual((code, json_code, missing), (0, 0, 1))
        self.assertIn("# PromptBase profiles compared (prompts)", out)
        self.assertEqual([p["profile"] for p in written["profiles"]], ["small"])
        self.assertIn("no snapshots", err)


class CommandTests(unittest.TestCase):
    def fetch(self, *records, profile="acb"):
        return patch(
            "promptbase_exporter.history.fetch_prompts",
            return_value=(Profile(profile, "u"), list(records or [rec("a", views=1)])),
        )

    def test_snapshot_records_each_profile_once(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            db = Path(directory) / "h.sqlite"
            code, stdout, _ = run(["snapshot", "@acb", "@acb", "--db", str(db)])
            listed = run(["list", "--db", str(db)])[1]
        self.assertEqual(code, 0)
        self.assertEqual(fetch.call_count, 1)  # the repeated profile is fetched once
        self.assertIn("Recorded snapshot 1: 1 prompts for @acb", stdout)
        self.assertRegex(listed, r"^1\t.+\t@acb\tprompt\t1\n$")

    def test_a_failed_profile_does_not_stop_the_others(self):
        outcomes = [PromptBaseError("Profile not found: ghost"),
                    (Profile("acb", "u"), [rec("a")])]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.history.fetch_prompts", side_effect=outcomes
        ):
            db = Path(directory) / "h.sqlite"
            code, stdout, stderr = run(["snapshot", "@ghost", "@acb", "--db", str(db)])
        self.assertEqual(code, 1)
        self.assertIn("Profile not found: ghost", stderr)
        self.assertIn("Recorded snapshot 1", stdout)

    def test_aliases_of_one_profile_are_recorded_once(self):
        with TemporaryDirectory() as directory, self.fetch() as fetch:
            db = Path(directory) / "h.sqlite"
            code, stdout, _ = run([
                "snapshot", "@acb", "ACB", "https://promptbase.com/profile/acb",
                "--db", str(db),
            ])
            listed = run(["list", "--db", str(db)])[1]
        self.assertEqual(code, 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(stdout.count("Recorded snapshot"), 1)
        self.assertEqual(listed.count("\n"), 1)

    def test_two_names_that_resolve_to_one_profile_are_recorded_once(self):
        outcomes = [(Profile("acb", "u"), [rec("a")]), (Profile("ACB", "u"), [rec("a")])]
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.history.fetch_prompts", side_effect=outcomes
        ):
            db = Path(directory) / "h.sqlite"
            code, stdout, _ = run(["snapshot", "acb", "old-name", "--db", str(db)])
            listed = run(["list", "--db", str(db)])[1]
        self.assertEqual(code, 0)
        self.assertEqual(stdout.count("Recorded snapshot"), 1)
        self.assertEqual(listed.count("\n"), 1)

    def test_a_profile_that_lost_every_listing_is_recorded_as_empty(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a", sales=2)])])
            with patch("promptbase_exporter.history.fetch_prompts",
                       return_value=(Profile("acb", "u"), [])):
                code, stdout, _ = run(["snapshot", "@acb", "--db", db])
                again = run(["snapshot", "@acb", "--db", db])[0]
            code_report, markdown, _ = run(["report", "--db", db])
        self.assertEqual((code, again, code_report), (0, 0, 0))
        self.assertIn("Recorded snapshot 2: 0 prompts", stdout)
        self.assertIn("## Removed prompts (0)", markdown)  # empty against empty: nothing moved

    def test_the_report_marks_every_listing_removed_when_the_profile_empties(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a"), rec("b")]), (1, [])])
            _, markdown, _ = run(["report", "--db", db])
        self.assertIn("## Removed prompts (2)", markdown)

    def test_an_empty_profile_is_an_error(self):
        with TemporaryDirectory() as directory, patch(
            "promptbase_exporter.history.fetch_prompts", return_value=(Profile("acb", "u"), [])
        ):
            code, _, stderr = run(["snapshot", "@acb", "--db", str(Path(directory) / "h.sqlite")])
        self.assertEqual(code, 1)
        self.assertIn("no approved prompts found for @acb", stderr)

    def test_bundles_are_kept_apart_from_prompts(self):
        with TemporaryDirectory() as directory, self.fetch():
            db = str(Path(directory) / "h.sqlite")
            run(["snapshot", "@acb", "--db", db])
            run(["snapshot", "@acb", "--item-type", "bundle", "--db", db])
            code, _, stderr = run(["report", "--db", db, "--item-type", "bundle"])
        self.assertEqual(code, 1)
        self.assertIn("found 1", stderr)  # one bundle snapshot, not two

    def test_report_end_to_end_in_each_format(self):
        with TemporaryDirectory() as directory:
            db = Path(directory) / "h.sqlite"
            store(db, "acb", [(0, [rec("a", sales=1)]), (3, [rec("a", sales=5)])])
            code, markdown, _ = run(["report", "--db", str(db)])
            _, as_json, _ = run(["report", "--db", str(db), "--format", "json"])
            target = Path(directory) / "out" / "trends.html"
            _, note, _ = run(["report", "--db", str(db), "--format", "html", "-o", str(target)])
            written = target.read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertIn("| sales | 1 | 5 | +4 |", markdown)
        self.assertEqual(json.loads(as_json)["totals"]["sales"]["change"], 4.0)
        self.assertIn("Wrote html report ->", note)
        self.assertTrue(written.startswith("<!doctype html>"))

    def test_window_and_metric_options(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a")]), (5, [rec("a", views=2)]), (10, [rec("a", views=9)])])
            _, default, _ = run(["report", "--db", db, "--metric", "views"])
            _, since, _ = run(["report", "--db", db, "--metric", "views", "--since", "2026-01-01"])
            _, days, _ = run(["report", "--db", db, "--metric", "views", "--days", "7"])
        self.assertIn("5.0 days", default)   # the previous snapshot
        self.assertIn("10.0 days", since)    # from the first
        self.assertIn("10.0 days", days)     # the latest snapshot at least 7 days older
        self.assertIn("+7", default)
        self.assertIn("+9", since)

    def test_an_absurdly_large_window_is_an_error_not_a_traceback(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a")]), (1, [rec("a", sales=1)])])
            code, _, stderr = run(["report", "--db", db, "--days", "1e18"])
        self.assertEqual(code, 1)
        self.assertIn("out of range", stderr)

    def test_several_profiles_need_a_choice(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a")]), (1, [rec("a", sales=1)])])
            store(db, "other", [(0, [rec("z")]), (1, [rec("z", sales=2)])])
            code, _, stderr = run(["report", "--db", db])
            chosen, markdown, _ = run(["report", "--db", db, "--profile", "@other"])
            unknown, _, unknown_err = run(["report", "--db", db, "--profile", "nobody"])
        self.assertEqual(code, 1)
        self.assertIn("several profiles (@acb, @other)", stderr)
        self.assertEqual(chosen, 0)
        self.assertIn("@other (prompts)", markdown)
        self.assertEqual(unknown, 1)
        self.assertIn("no snapshots of prompts for @nobody", unknown_err)

    def test_errors_are_reported_not_raised(self):
        with TemporaryDirectory() as directory:
            missing = str(Path(directory) / "gone.sqlite")
            for argv in (["report", "--db", missing], ["list", "--db", missing]):
                with self.subTest(argv=argv):
                    code, _, stderr = run(argv)
                    self.assertEqual(code, 1)
                    self.assertIn("history file not found", stderr)
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a")]), (1, [rec("a")])])
            code, _, stderr = run(["report", "--db", db, "--since", "2027-01-01"])
        self.assertEqual(code, 1)
        self.assertIn("no snapshot between", stderr)

    def test_usage_errors_exit_with_two(self):
        for argv in (
            [],
            ["report"],
            ["snapshot", "@acb"],
            ["report", "--db", "x", "--since", "soon"],
            ["report", "--db", "x", "--since", "2026-01-01", "--days", "3"],
            ["report", "--db", "x", "--metric", "rating"],
            ["report", "--db", "x", "--days", "nan"],
            ["report", "--db", "x", "--days", "inf"],
            ["report", "--db", "x", "--days", "-1"],
            ["report", "--db", "x", "--days", "soon"],
            ["report", "--db", "x", "--top", "0"],
            ["report", "--db", "x", "--top", "-3"],
            ["report", "--db", "x", "--top", "many"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run(argv)[0], 2)

    def test_a_fired_alert_exits_with_two_after_writing_the_report(self):
        with TemporaryDirectory() as directory:
            db = str(Path(directory) / "h.sqlite")
            store(db, "acb", [(0, [rec("a", sales=1)]), (1, [rec("a", sales=2)])])
            fired, out, err = run(["report", "--db", db, "--alert", "sales+1", "--alert", "new"])
            quiet, _, _ = run(["report", "--db", db, "--alert", "sales+5"])
            target = Path(directory) / "r.json"
            to_file, _, _ = run(["report", "--db", db, "--alert", "sales+1",
                                 "--format", "json", "-o", str(target)])
            written = json.loads(target.read_text(encoding="utf-8"))
            bad, _, bad_err = run(["report", "--db", db, "--alert", "views+0"])
        self.assertEqual((fired, quiet, to_file, bad), (2, 0, 2, 2))
        self.assertIn("## Alerts (1)", out)
        self.assertIn("1 alert(s) fired", err)
        self.assertEqual(len(written["alerts"]["hits"]), 1)
        self.assertIn("invalid alert", bad_err)

    def test_version(self):
        code, stdout, _ = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("promptbase-history", stdout)


if __name__ == "__main__":
    unittest.main()

"""scripts/check_release.py: the release rules, on the real checkout and on bad ones."""

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_release.py"
TODAY = date(2026, 10, 1)


def load_script():
    spec = importlib.util.spec_from_file_location("check_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_release"] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

## Unreleased

## 0.2.1 - 2026-09-30

### Fixed

- a fix

## 0.2.0 - 2026-09-29

### Added

- a feature
"""
PIN = "- uses: IACBI/promptbase-profile-exporter@v{v}\n"
SHA_PIN = "- uses: IACBI/promptbase-profile-exporter@<full-commit-sha> # v{v}\n"
TAG = 'git ls-remote https://github.com/IACBI/promptbase-profile-exporter "refs/tags/v{v}^{{}}"\n'


def make_tree(root, *, version="0.2.1", changelog=CHANGELOG, pyproject_version=None,
              readme_en=1, readme_tr=1, docs=None):
    (root / "promptbase_exporter").mkdir()
    (root / "docs").mkdir()
    (root / "promptbase_exporter" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{pyproject_version or version}"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (root / "README.md").write_text(
        '<a id="top"></a>\n<a id="english"></a>\n' + PIN.format(v=version) * readme_en
        + '<a id="turkce"></a>\n' + PIN.format(v=version) * readme_tr,
        encoding="utf-8",
    )
    pins = docs if docs is not None else (
        PIN.format(v=version) + SHA_PIN.format(v=version) + TAG.format(v=version))
    (root / "docs" / "github-action.md").write_text(pins, encoding="utf-8")


class RealCheckoutTests(unittest.TestCase):
    def test_this_checkout_follows_its_own_release_policy(self):
        if not (ROOT / "CHANGELOG.md").is_file():
            self.skipTest("not a source checkout")
        self.assertEqual(load_script().check(ROOT), [])


class ChangelogRuleTests(unittest.TestCase):
    def setUp(self):
        self.script = load_script()

    def problems(self, text, current=(0, 2, 1), today=TODAY, exceptions=None):
        return self.script.check_changelog(text, current, today, exceptions or {})

    def test_a_consistent_changelog_has_no_problems(self):
        self.assertEqual(self.problems(CHANGELOG), [])

    def test_the_newest_release_must_be_the_package_version(self):
        problems = self.problems(CHANGELOG, current=(0, 2, 2))
        self.assertEqual(len(problems), 1)
        self.assertIn("newest release is 0.2.1", problems[0])

    def test_added_changes_need_a_minor_release(self):
        text = CHANGELOG.replace("### Fixed", "### Added")
        problems = self.problems(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("0.2.1 lists Added changes", problems[0])
        self.assertIn("must be a minor release", problems[0])

    def test_removed_changes_need_a_minor_release_too(self):
        problems = self.problems(CHANGELOG.replace("### Fixed", "### Removed"))
        self.assertIn("lists Removed changes", problems[0])

    def test_a_minor_or_major_bump_may_add_features(self):
        for newer in ("0.3.0", "1.0.0"):
            text = CHANGELOG.replace("## 0.2.1", f"## {newer}").replace("### Fixed", "### Added")
            with self.subTest(newer=newer):
                self.assertEqual(
                    self.problems(text, current=self.script.parse_version(newer)), [])

    def test_a_recorded_exception_is_allowed_and_only_that_one(self):
        text = CHANGELOG.replace("### Fixed", "### Added")
        self.assertEqual(self.problems(text, exceptions={"0.2.1": "why"}), [])
        self.assertEqual(len(self.problems(text, exceptions={"0.9.9": "other"})), 1)

    def test_the_real_exception_is_still_a_violation_without_it(self):
        # 0.14.3 added the project site under a patch number; the rule must see that.
        text = "## Unreleased\n\n## 0.14.3 - 2026-10-01\n\n### Added\n\n- x\n\n" \
               "## 0.14.2 - 2026-10-01\n\n### Fixed\n\n- y\n"
        problems = self.script.check_changelog(text, (0, 14, 3), TODAY, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("0.14.3 lists Added changes", problems[0])

    def test_a_release_dated_after_today_in_utc_is_refused(self):
        # v0.15.0 was dated with the local date, a day ahead of UTC.
        text = CHANGELOG.replace("## 0.2.1 - 2026-09-30", "## 0.2.1 - 2026-10-02")
        problems = self.problems(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("is dated 2026-10-02, but it is 2026-10-01 in UTC", problems[0])

    def test_dates_must_not_increase_going_down_the_file(self):
        text = CHANGELOG.replace("## 0.2.0 - 2026-09-29", "## 0.2.0 - 2026-09-30").replace(
            "## 0.2.1 - 2026-09-30", "## 0.2.1 - 2026-09-29")
        problems = self.problems(text)
        self.assertEqual(len(problems), 1)
        self.assertIn("before the older 0.2.0", problems[0])

    def test_dates_must_be_present_and_valid_from_the_first_dated_release(self):
        missing = self.problems(CHANGELOG.replace("## 0.2.1 - 2026-09-30", "## 0.2.1"))
        self.assertEqual(missing, [])  # 0.2.1 predates 0.7.0, which began dating releases
        late = "## Unreleased\n\n## 0.8.1\n\n### Fixed\n\n- x\n\n## 0.8.0 - 2026-09-28\n\n- y\n"
        self.assertIn("has no date", self.script.check_changelog(late, (0, 8, 1), TODAY, {})[0])
        bad = late.replace("## 0.8.1", "## 0.8.1 - 2026-13-45")
        self.assertIn("not YYYY-MM-DD", self.script.check_changelog(bad, (0, 8, 1), TODAY, {})[0])

    def test_versions_must_decrease_down_the_file(self):
        text = CHANGELOG.replace("## 0.2.0 - 2026-09-29", "## 0.2.1 - 2026-09-29")
        self.assertIn("do not decrease", "\n".join(self.problems(text)))

    def test_unreleased_must_come_first_and_only_once(self):
        self.assertIn("first section must be", self.problems("## 0.2.1 - 2026-09-30\n")[0])
        twice = CHANGELOG + "\n## Unreleased\n"
        self.assertIn("more than one", "\n".join(self.problems(twice)))

    def test_an_unrecognised_heading_is_reported(self):
        problems = self.problems(CHANGELOG + "\n## Version two\n")
        self.assertIn("unrecognised heading", "\n".join(problems))

    def test_dates_are_the_dashed_form_only(self):
        # Python 3.11+ reads "20261001" and "2026-W40-4" as dates; the heading must not.
        for written in ("20261001", "2026-W40-4", "2026-10-1"):
            text = (f"## Unreleased\n\n## 0.8.1 - {written}\n\n- x\n\n"
                    "## 0.8.0 - 2026-09-28\n\n- y\n")
            with self.subTest(written=written):
                found = self.script.check_changelog(text, (0, 8, 1), TODAY, {})
                self.assertIn("not YYYY-MM-DD", "\n".join(found))

    def test_a_changelog_with_no_release_is_an_error(self):
        problems = self.problems("# Changelog\n\n## Unreleased\n\n### Added\n\n- x\n")
        self.assertEqual(
            problems, ["CHANGELOG.md: no released section, but the package has a version"]
        )

    def test_a_release_heading_left_empty_is_an_error(self):
        # The entries were not moved out of Unreleased when the release was cut.
        text = ("## Unreleased\n\n### Added\n\n- a feature\n\n## 0.8.1 - 2026-09-30\n\n"
                "## 0.8.0 - 2026-09-28\n\n- y\n")
        problems = self.script.check_changelog(text, (0, 8, 1), TODAY, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("0.8.1 has no entries", problems[0])

    def test_early_releases_may_have_no_entries(self):
        self.assertEqual(self.problems("## Unreleased\n\n## 0.2.1\n\n## 0.2.0\n"), [])

    def test_headings_inside_a_release_do_not_leak_into_the_next(self):
        text = ("## Unreleased\n\n## 0.2.1 - 2026-09-30\n\n### Fixed\n\n- x\n\n"
                "## 0.2.0 - 2026-09-29\n\n### Added\n\n- y\n")
        self.assertEqual(self.script.check_changelog(text, (0, 2, 1), TODAY, {}), [])


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.script = load_script()

    def check(self, **options):
        with TemporaryDirectory() as directory:
            make_tree(Path(directory), **options)
            return self.script.check(Path(directory), TODAY)

    def test_a_consistent_tree_has_no_problems(self):
        self.assertEqual(self.check(), [])

    def test_the_module_and_pyproject_versions_must_match(self):
        problems = self.check(pyproject_version="0.2.0")
        self.assertEqual(len(problems), 1)
        self.assertIn("says 0.2.1 but pyproject.toml says 0.2.0", problems[0])

    def test_a_stale_pin_in_either_file_is_reported_with_its_line(self):
        stale = PIN.format(v="0.2.0") + SHA_PIN.format(v="0.2.1") + TAG.format(v="0.2.1")
        problems = self.check(docs=stale)
        self.assertEqual(
            problems, ["docs/github-action.md:1: refers to v0.2.0, but the version is 0.2.1"]
        )
        for pin in (SHA_PIN, TAG):
            docs = PIN.format(v="0.2.1") + pin.format(v="0.1.0")
            self.assertIn("refers to v0.1.0", "\n".join(self.check(docs=docs)))

    def test_each_readme_language_needs_its_own_pin(self):
        # Two pins in one language must not stand in for the other language's.
        self.assertEqual(
            self.check(readme_en=2, readme_tr=0),
            ["README.md: the Turkish section has no version pin"],
        )
        self.assertEqual(
            self.check(readme_en=0, readme_tr=2),
            ["README.md: the English section has no version pin"],
        )

    def test_a_readme_without_the_section_anchors_is_reported(self):
        with TemporaryDirectory() as directory:
            make_tree(Path(directory))
            (Path(directory) / "README.md").write_text(PIN.format(v="0.2.1"), encoding="utf-8")
            problems = self.script.check(Path(directory), TODAY)
        self.assertEqual(
            problems, ["README.md: the English and Turkish section anchors are missing"]
        )

    def test_a_pin_must_match_the_whole_reference(self):
        for ref in ("0.2.1-broken", "0.2.1.1", "0.2.10", "0.2.1+x"):
            with self.subTest(ref=ref):
                docs = PIN.format(v=ref)
                self.assertEqual(
                    self.check(docs=docs),
                    [f"docs/github-action.md:1: refers to v{ref}, but the version is 0.2.1"],
                )

    def test_third_party_action_pins_are_not_mistaken_for_ours(self):
        docs = (PIN.format(v="0.2.1")
                + "- uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0\n")
        self.assertEqual(self.check(docs=docs), [])

    def test_a_docs_file_with_no_pin_at_all_is_reported(self):
        self.assertIn("expected at least 1 version pins", "\n".join(self.check(docs="none\n")))


if __name__ == "__main__":
    unittest.main()

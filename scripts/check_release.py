"""Check that a checkout's release metadata agrees with itself and with the policy.

Usage: python scripts/check_release.py   (standard library only)

It runs in the test suite, so every pull request is checked. The rules are the ones
RELEASE.md states, which a person has to follow by hand and twice got wrong:

- ``__version__``, ``pyproject.toml``, the newest ``CHANGELOG.md`` section, and every
  ``@vX.Y.Z`` pin in the README and docs name the same version;
- versions go down the changelog in strictly decreasing order, under one
  ``## Unreleased`` heading at the top;
- a release dated after today in UTC is refused (a local date ahead of UTC is how a
  release gets the wrong day), and dates never increase going down the file;
- a release that lists ``### Added`` or ``### Removed`` changes must bump the minor
  version at least ("a minor release when it adds features");
- a release from 0.7.0 on has entries under it, so promoting ``## Unreleased`` cannot
  leave them behind, and the README shows the current pin in both languages.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Version = tuple[int, int, int]

# Releases that broke the policy before this check existed, each with what happened.
# They stay as published, since a tag that users pin cannot be moved.
KNOWN_EXCEPTIONS = {
    "0.14.3": "added the project site under a patch number; 0.15.0 followed",
}
# 0.1.0 to 0.6.0 were written up without dates.
FIRST_DATED: Version = (0, 7, 0)
# Changelog headings whose entries change what a user can do or rely on.
MINOR_HEADINGS = ("Added", "Removed")

_SECTION = re.compile(r"^## (?:Unreleased|(\d+)\.(\d+)\.(\d+)(?: - (\S+))?)\s*$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The whole reference after the "v", so "v0.15.0-broken" or "v0.15.0.1" is a different
# (wrong) pin and not a match for v0.15.0.
_REF = r"v([0-9A-Za-z.+_-]+)"
_PIN = re.compile(
    rf"promptbase-profile-exporter@(?:{_REF}|<full-commit-sha> # {_REF})|refs/tags/{_REF}"
)
_README_LANGUAGES = (("English", '<a id="english"></a>'), ("Turkish", '<a id="turkce"></a>'))


@dataclass
class Section:
    line: int
    version: Version | None  # None for "Unreleased"
    date_text: str | None
    headings: list[str]
    entries: int = 0  # the "- " items under it

    @property
    def name(self) -> str:
        return ".".join(map(str, self.version)) if self.version else "Unreleased"


def parse_version(text: str) -> Version:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def parse_changelog(text: str) -> tuple[list[Section], list[str]]:
    """The ``## `` sections of a changelog, and a problem for each heading not understood."""
    sections: list[Section] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            match = _SECTION.match(line)
            if match is None:
                problems.append(f"CHANGELOG.md:{number}: unrecognised heading {line!r}")
                continue
            version = (int(match[1]), int(match[2]), int(match[3])) if match[1] else None
            sections.append(Section(number, version, match[4], []))
        elif line.startswith("### ") and sections:
            sections[-1].headings.append(line[4:].strip())
        elif line.startswith("- ") and sections:
            sections[-1].entries += 1
    return sections, problems


def check_changelog(
    text: str, current: Version, today: date, exceptions: dict[str, str] | None = None
) -> list[str]:
    exceptions = KNOWN_EXCEPTIONS if exceptions is None else exceptions
    sections, problems = parse_changelog(text)
    if not sections or sections[0].version is not None:
        return [*problems, "CHANGELOG.md: the first section must be '## Unreleased'"]
    if sum(section.version is None for section in sections) > 1:
        problems.append("CHANGELOG.md: more than one '## Unreleased' section")
    released = [section for section in sections if section.version is not None]
    if not released:
        return [*problems, "CHANGELOG.md: no released section, but the package has a version"]
    if released[0].version != current:
        problems.append(
            f"CHANGELOG.md: the newest release is {released[0].name}, "
            f"but the package version is {'.'.join(map(str, current))}"
        )
    for newer, older in pairwise(released):
        assert newer.version and older.version  # noqa: S101 - narrowed by the filter above
        if newer.version <= older.version:
            problems.append(
                f"CHANGELOG.md:{newer.line}: {newer.name} is listed above {older.name}, "
                "so versions do not decrease down the file"
            )
        elif (
            newer.version[:2] <= older.version[:2]
            and any(heading in MINOR_HEADINGS for heading in newer.headings)
            and newer.name not in exceptions
        ):
            kinds = "/".join(h for h in newer.headings if h in MINOR_HEADINGS)
            problems.append(
                f"CHANGELOG.md:{newer.line}: {newer.name} lists {kinds} changes after "
                f"{older.name}, so it must be a minor release (a patch is for fixes only)"
            )
    for section in released:
        assert section.version  # noqa: S101 - narrowed by the filter above
        if section.version >= FIRST_DATED and section.entries == 0:
            problems.append(
                f"CHANGELOG.md:{section.line}: {section.name} has no entries; "
                "were they left under '## Unreleased'?"
            )
    problems += _check_dates(released, today)
    return problems


def _check_dates(released: list[Section], today: date) -> list[str]:
    problems: list[str] = []
    previous: tuple[Section, date] | None = None
    for section in released:  # newest first
        assert section.version  # noqa: S101 - released sections have one
        if section.date_text is None:
            if section.version >= FIRST_DATED:
                problems.append(
                    f"CHANGELOG.md:{section.line}: {section.name} has no date "
                    f"('## {section.name} - YYYY-MM-DD', UTC)"
                )
            continue
        try:
            if not _DATE.fullmatch(section.date_text):
                raise ValueError(section.date_text)  # 3.11+ also reads "20261001"
            when = date.fromisoformat(section.date_text)
        except ValueError:
            problems.append(
                f"CHANGELOG.md:{section.line}: {section.name} has the date "
                f"{section.date_text!r}, not YYYY-MM-DD"
            )
            continue
        if when > today:
            problems.append(
                f"CHANGELOG.md:{section.line}: {section.name} is dated {when}, "
                f"but it is {today} in UTC: use the UTC date"
            )
        if previous is not None and previous[1] < when:
            problems.append(
                f"CHANGELOG.md:{previous[0].line}: {previous[0].name} is dated "
                f"{previous[1]}, before the older {section.name} ({when})"
            )
        previous = (section, when)
    return problems


def package_version(root: Path) -> Version:
    text = (root / "promptbase_exporter" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise ValueError("promptbase_exporter/__init__.py has no __version__")
    return parse_version(match[1])


def project_version(root: Path) -> Version:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml has no version")
    return parse_version(match[1])


def check_pins(root: Path, current: Version) -> list[str]:
    """Every ``@vX.Y.Z`` example in the README (both languages) and the Action guide."""
    expected = ".".join(map(str, current))
    readme = (root / "README.md").read_text(encoding="utf-8")
    problems, _ = _check_pin_text("README.md", readme, expected, at_least=0)
    # The README repeats its examples in English and in Turkish: each language needs one.
    cut = [readme.find(marker) for _, marker in _README_LANGUAGES]
    if -1 in cut or cut != sorted(cut):
        problems.append("README.md: the English and Turkish section anchors are missing")
    else:
        for (language, _), start, end in zip(
            _README_LANGUAGES, cut, [*cut[1:], len(readme)], strict=True
        ):
            if not _PIN.search(readme[start:end]):
                problems.append(f"README.md: the {language} section has no version pin")
    guide = (root / "docs" / "github-action.md").read_text(encoding="utf-8")
    more, _ = _check_pin_text("docs/github-action.md", guide, expected, at_least=1)
    return [*problems, *more]


def _check_pin_text(name: str, text: str, expected: str, at_least: int) -> tuple[list[str], int]:
    problems: list[str] = []
    found = 0
    for number, line in enumerate(text.splitlines(), 1):
        for match in _PIN.finditer(line):
            found += 1
            version = next(group for group in match.groups() if group)
            if version != expected:
                problems.append(
                    f"{name}:{number}: refers to v{version}, but the version is {expected}"
                )
    if found < at_least:
        problems.append(f"{name}: expected at least {at_least} version pins, found {found}")
    return problems, found


def check(root: Path = ROOT, today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    module, project = package_version(root), project_version(root)
    problems = []
    if module != project:
        problems.append(
            f"promptbase_exporter/__init__.py says {'.'.join(map(str, module))} but "
            f"pyproject.toml says {'.'.join(map(str, project))}"
        )
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return [
        *problems,
        *check_changelog(changelog, module, today),
        *check_pins(root, module),
    ]


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} release problem(s); see RELEASE.md", file=sys.stderr)
        return 1
    print("release metadata is consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

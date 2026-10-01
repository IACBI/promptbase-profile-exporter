# Release checklist

How a maintainer publishes a new version. There is no PyPI package: a release
is a version bump, an annotated git tag, and a GitHub Release. Users pin the
GitHub Action to the tag (`@vX.Y.Z`).

Choose the version from the `## Unreleased` section of `CHANGELOG.md`: a patch
release for fixes only, a minor release when it adds features.

## 1. Prepare the release branch

```bash
git checkout main && git pull
git checkout -b release-X.Y.Z
```

Then update:

- `promptbase_exporter/__init__.py`: `__version__ = "X.Y.Z"`
- `pyproject.toml`: `version = "X.Y.Z"`
- `CHANGELOG.md`: rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` (UTC) and
  add a fresh, empty `## Unreleased` above it
- every reference to the previous version in `README.md` (both languages) and
  `docs/github-action.md`: the `@vX.Y.Z` Action pins, the `# vX.Y.Z` comment of the
  commit-SHA example, and its `git ls-remote` tag

## 2. Validate

```bash
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m coverage report
python -m build
python -m twine check dist/*
python -m promptbase_exporter --version
python -m promptbase_exporter @acb --dry-run
```

Remove `build/`, `dist/`, and `*.egg-info` afterwards.

## 3. Merge through a pull request

`main` is protected, so open a pull request titled `chore: release vX.Y.Z` and
merge it once CI is green.

## 4. Tag and publish

```bash
git checkout main && git pull
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

Create the GitHub Release from that tag, using the version's `CHANGELOG.md`
section as the notes:

```bash
awk '/^## X.Y.Z/{f=1;next} /^## [0-9]/{f=0} f' CHANGELOG.md > notes.md
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file notes.md
rm notes.md
```

Publishing the release starts the `release` workflow: it builds the sdist and wheel
from the tag, checks that their version matches the tag, records a signed build
provenance attestation, and attaches both files to the release. Confirm it passed
and that the files are there:

```bash
gh run list --workflow release --limit 1
gh release view vX.Y.Z --json assets --jq '.assets[].name'
```

The `@vX.Y.Z` examples in the docs resolve once the release exists.

## Publishing to PyPI (not set up)

Users install by cloning the repository or by pinning the Action, and neither
needs PyPI. To add PyPI distribution later, configure
[trusted publishing](https://docs.pypi.org/trusted-publishers/), add a `pypi`
GitHub environment, and add a workflow that runs `python -m build` and
`pypa/gh-action-pypi-publish` on `release: published`.

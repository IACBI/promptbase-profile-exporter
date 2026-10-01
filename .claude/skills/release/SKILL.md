---
name: release
description: Cut a new release of promptbase-profile-exporter — bump versions, promote the changelog, sync the pinned Action tag in the docs, validate, tag, and prepare the GitHub Release. Use when the user asks to release, publish a version, or cut vX.Y.Z.
---

# Release a new version

This project has no PyPI step — a release is a version bump plus a git tag and a
GitHub Release that users pin the Action to (`@vX.Y.Z`). Follow these steps in
order. Ask the user for the target version (`X.Y.Z`) if they did not give one;
suggest one from the `## Unreleased` changelog contents (patch for fixes only,
minor for new features).

## 1. Pre-flight

- Ensure you are on an up-to-date `main` (`git checkout main && git pull`) and
  the tree is clean.
- Read `## Unreleased` in `CHANGELOG.md`. If it is empty, stop — there is
  nothing to release.

## 2. Bump versions (must all match)

- `promptbase_exporter/__init__.py` → `__version__ = "X.Y.Z"`
- `pyproject.toml` → `version = "X.Y.Z"`

## 3. Promote the changelog

In `CHANGELOG.md`, rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` (today, UTC)
and add a fresh empty `## Unreleased` heading above it.

## 4. Sync the pinned Action examples

Replace every reference to the previous version (the `@vX.Y.Z` pins, the `# vX.Y.Z`
comment of the commit-SHA example, and its `git ls-remote` tag):
- `README.md` (the Action example in both the English and Türkçe sections)
- `docs/github-action.md` (every example)

`SECURITY.md` needs no change: it supports "the latest release", not a number.

## 5. Validate locally (all must pass)

```
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m coverage report
python -m build
python -m twine check dist/*
```
Then clean build artifacts: remove `build/`, `dist/`, `*.egg-info`.

## 6. Open a release PR

Commit on a `release-X.Y.Z` branch and open a PR to `main`. `main` is protected,
so the release must merge through a green PR — do not push to `main` directly.

## 7. After the PR merges — tag and release (hand off the publish step)

The git tag and GitHub Release are outward-facing. Create the annotated tag and
push it, but let the user create/publish the GitHub Release (it is theirs to
publish):

```
git checkout main && git pull
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

Then tell the user to create a GitHub Release from the `vX.Y.Z` tag and paste the
matching `CHANGELOG.md` section into the notes. Only after the Release exists do
the `@vX.Y.Z` doc examples resolve.

See `RELEASE.md` for the canonical checklist; keep the two in sync.

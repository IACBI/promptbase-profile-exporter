# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Fixed

- Resolve profiles whose PromptBase profile document has no `username` field
  (for example `@emanema` and `@dreamydesigns`), which previously failed with
  "Profile not found" even though the profile and its prompts are public. The
  owner uid is now recovered from any prompt/app/bundle the profile owns when
  the profile document itself lacks the username.

### Added

- Add short `pb` and `pb-web` console-script aliases for `promptbase-export`
  and `promptbase-export-web`. The long names are unchanged, so existing
  scripts and the GitHub Action keep working; the aliases just make the tool
  pleasant to install and run with `uv tool install .` (then `pb @acb`).
- Add a `/download` link for each generated file in the web UI. The endpoint
  only serves files inside the server's working directory whose names match the
  exporter's own pattern, so it cannot read arbitrary paths or unrelated
  supported-extension files (such as a stray `secrets.json`) even when exposed
  with `--host 0.0.0.0`.

### Changed

- Move shared date parsing into `promptbase_exporter.dates` so the web UI no
  longer imports from the CLI module.
- Reuse the dates the web layer already parsed for validation instead of
  parsing `since`/`until` a second time during export.
- Pause briefly between successive pages of a paginated PromptBase query so
  large-profile fetches stay polite and avoid rate limits; single-page fetches
  never wait.

### Removed

- Drop the PyPI publish workflow. The tool is used by cloning the repository or
  pinning the GitHub Action to a release tag, so it no longer ships a failing
  publish job; see `RELEASE.md` for how to re-add PyPI distribution later.

### Fixed

- Build the wheel reliably when a generated `exports/` directory is present.
  setuptools' flat-layout auto-discovery treated `exports/` as a second
  top-level package and aborted with `Multiple top-level packages discovered`,
  breaking `pip install .` / `uv tool install .` from a working tree that had
  already produced exports. The package list is now declared explicitly.

### Internal

- Ship a `py.typed` marker so downstream type checkers see the package's hints.
- Add Python 3.13 to the test matrix and package classifiers.
- Type-check the package with `mypy` and measure coverage in CI; add dedicated
  tests for date parsing and the web download endpoint.
- Add a Dependabot config that keeps the pinned GitHub Actions up to date.

## 0.7.0 - 2026-06-01

### Fixed

- Stop the Markdown export validation check from over-counting prompts whose
  descriptions contain `## N.` heading lines, which previously failed valid
  exports with a spurious record-count error.
- Do not overwrite the existing catalog during an `--update-file` run when the
  requested `--diff-output` report cannot be written; the run now aborts with
  exit code 1 and leaves the catalog untouched.
- Return HTTP 400 (not 500) for invalid `since`/`until` dates in the web UI.

### Changed

- Report file-write failures (directory exports, single-file exports, and diff
  reports) as clear `error:` messages with exit code 1 instead of raw
  tracebacks.
- Validate `--min-price` and `--max-price` before fetching, so invalid values
  fail immediately without a network call.

### Security

- Confine web UI exports to the server's working directory; reject absolute
  paths and `..` traversal in the output directory field.
- Reject cross-origin (CSRF) and rebound-DNS requests on `POST /export` via
  `Host` and `Origin`/`Sec-Fetch-Site` checks.
- Add `Content-Security-Policy`, `X-Frame-Options`, and `Referrer-Policy`
  headers to web responses.
- Warn when the web server binds to a non-loopback host.
- Pin GitHub Actions to commit SHAs and scope the test workflow token to
  `contents: read`.

### Documentation

- Document the CLI exit codes (`0` success, `1` error, `2` catalog drift) and
  the local web UI security model in the README.

### Internal

- Remove the unused offset-pagination path in the Firestore client.
- Add test coverage for the network retry path, date parsing, argument
  validation, and file-write failure handling.

## 0.6.0

- Add catalog comparison with `--compare`, `--diff-output`, and `--fail-on-diff`.
- Add `--update-file` for controlled in-place catalog refreshes.
- Add `--output-file` and `--overwrite` for exact single-file exports.
- Add `--limit`, `--since`, and `--until` filters.
- Add schema drift checks for PromptBase public data fields.
- Add a local web UI through `python -m promptbase_exporter.web` and `promptbase-export-web`.
- Add a composite GitHub Action with artifact upload support.
- Add Ruff lint configuration and CI lint checks.

## 0.5.0

- Add `--sort` with newest, oldest, title, price, views, sales, downloads, favorites, and rating options.
- Add richer JSON/CSV metadata: `created_iso`, `discount`, `views`, `sales`, `downloads`, `favorites`, `rating`, and `reviews`.
- Add `text-only` and `image-only` aliases for `--mode`.
- Show planned per-mode output counts during `--dry-run`.
- Add PyPI publish workflow scaffold.
- Add package build checks to CI.
- Modernize package license metadata for current setuptools builds.

## 0.4.0

- Add `--version`.
- Add `--dry-run`.
- Add `--list-domains` and `--list-types`.
- Add GitHub Actions package installation smoke checks.
- Add contributor and security documentation.

## 0.3.0

- Add metadata filters: `--domain`, `--type`, `--free-only`, `--paid-only`, `--min-price`, and `--max-price`.
- Add timestamped filenames with `--timestamp-filenames`.
- Add `--quiet` and `--verbose`.
- Include `price` in JSON and CSV exports.
- Add GitHub issue and pull request templates.

## 0.2.0

- Add Firestore pagination.
- Add retry/backoff for transient network and PromptBase errors.
- Add Markdown, JSON, and CSV export formats.
- Add `.gitattributes`.

## 0.1.0

- Initial CLI exporter.
- Export all, text-only, and image-only PromptBase prompt catalogs.
- Support TXT output.

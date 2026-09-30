# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Fixed

- Comparing a CSV catalog no longer reports a title or description that
  genuinely starts with an apostrophe before `=`, `+`, `-`, or `@` (such as
  `'=SUM(A1)`) as changed on every run. CSV cells are now loaded exactly as
  stored, and a cell matches both its plain and its `--csv-safe` spelling;
  `--csv-safe` escapes such text reversibly (`'=x` becomes `''=x`).
- `--compare`, `--update-file`, and `pb-diff` again report `type`, `domain`,
  or `price` values that were cleared in the newer catalog. 0.9.0 skipped any
  field that was empty on either side; now a field is skipped only when the
  older catalog has no value or the newer catalog's format (TXT) does not
  store it.
- The GitHub Action no longer needs Bash 4: it parses `profile-url` with a
  read loop instead of `mapfile`, which the Bash 3.2 on macOS runners lacks.
  CI now runs the action on macOS and its export step under `/bin/bash` 3.2.

## 0.9.0 - 2026-09-30

### Added

- HTML export (`--format html`): one self-contained, searchable catalog page
  that works offline and in light or dark mode. Every value is HTML-escaped,
  and the page embeds the full records as JSON, so HTML catalogs work with
  `--compare`, `--update-file`, and `pb-diff`.
- `pb-diff` (`promptbase-diff`, `python -m promptbase_exporter.diff`): compare
  two saved catalogs without fetching anything, across formats, with the same
  `--diff-output`, `--fail-on-diff`, and exit codes as the main command.
- Several profiles per run: `pb @acb @other ...` exports each profile, shares
  one timestamp, reports a failing profile without stopping the others, and
  exits with `1` if any failed.
- `--min-sales` and `--min-rating` filters, in the CLI, web UI, and Action.
- `--csv-safe`: prefix CSV text cells that start with `=`, `+`, `-`, or `@`
  with an apostrophe so spreadsheet apps do not run them as formulas (CSV
  injection). Catalog loading strips the prefix again, so safe and plain
  catalogs compare equal.
- JSON diff reports: `--diff-output` writes JSON for a `.json` path and can be
  repeated to write Markdown and JSON together. The Markdown report now shows
  the old and new value of each changed field.
- Web UI: an optional "Compare with existing catalog" field that shows the
  diff report on the result page, plus minimum sales, minimum rating, and CSV
  formula protection fields.
- GitHub Action: `output-file`, `overwrite`, `update-file`, `csv-safe`,
  `dry-run`, `min-sales`, and `min-rating` inputs; `profile-url` accepts
  several profiles; new `exit-code`, `has-changes`, `added`, `removed`,
  `changed`, `unchanged`, and `diff-json` outputs. CI now runs the action end
  to end against live data, and tests Python 3.14.

### Changed

- Requests to PromptBase now identify the tool
  (`promptbase-profile-exporter/<version>`) instead of posing as a desktop
  Chrome browser.
- The Action's `format` input defaults to empty, so `output-file` and
  `update-file` infer the format from the file extension as the CLI does.
  Leaving it empty still means `txt` for directory exports.
- `--limit` help and docs spell out that the limit applies before `--mode`
  splits prompts into text and image files.
- A diff no longer reports a field as changed when the new catalog lacks it
  (for example comparing a JSON catalog against a TXT one).
- `SECURITY.md` lists 0.9.x as supported and covers the comparison field and
  exported-file injection; `CONTRIBUTING.md` reflects Python 3.14 and the new
  modules.
- Reorganize the documentation. The README is now a concise English and
  Türkçe guide; the full command-line reference, the web UI guide with its
  security model, and the GitHub Action guide live in `docs/`. The Action
  examples pin `@v0.8.0` and `actions/checkout@v7` instead of `@main` and
  `@v4`.
- Refresh `CONTRIBUTING.md`, `SECURITY.md` (supported versions and the private
  vulnerability-reporting link), `RELEASE.md` (the protected-branch flow), and
  the issue and pull request templates.
- Use the ASCII author name `A.C.B` in `pyproject.toml` and `LICENSE`, and add
  documentation, changelog, and `Typing :: Typed` metadata to the package.

### Fixed

- Failed HTTP responses from PromptBase are closed instead of leaking their
  socket until garbage collection.
- Web UI: two exports of the same profile running at once can no longer
  interleave their writes; the compare/write/validate step is serialized.
  Downloads carry `Content-Security-Policy: sandbox`, so a downloaded HTML
  export can never run script on the UI's origin.
- `--compare` and `--update-file` against a CSV catalog no longer report every
  free or whole-number price as changed. CSV stores `2.0` as the text `2.0`,
  which was compared against `2`, so `--fail-on-diff` always exited with 2.
- A prompt title containing a line break no longer breaks TXT and Markdown
  exports; the title is written on one line, and post-write validation and
  diff parsing count the record correctly.
- Rewriting a catalog (`--update-file`, `--output-file --overwrite`, and
  directory exports) is now atomic: the new file is written beside the old one
  and swapped in, so a failed write can no longer leave a truncated catalog.
  `--output-file` without `--overwrite` creates the file exclusively.
- `--min-price`/`--max-price` and the web UI price fields reject `nan` and
  `inf`, which previously passed validation and silently filtered out every
  prompt.
- Web UI: starting the server with an empty `--host ""` now prints the
  non-loopback exposure warning; an empty host binds every interface.
- Web UI: a connection that stalls while sending its request is closed after
  60 seconds instead of holding a server thread indefinitely, and a failure to
  write the export is reported as a clear error.
- A malformed PromptBase response (not a list of rows, or a row without a
  document name) now fails with a clear error instead of a Python traceback.

## 0.8.0 - 2026-09-28

### Fixed

- Resolve profiles whose PromptBase profile document has no `username` field
  (for example `@emanema` and `@dreamydesigns`), which previously failed with
  "Profile not found" even though the profile and its prompts are public. The
  owner uid is now recovered from any prompt/app/bundle the profile owns when
  the profile document itself lacks the username.
- Web UI: check that `/download` and output-directory paths stay inside the
  working directory before resolving them. On Windows, resolving a UNC path
  such as `//host/share/...` connects to that host over SMB, so any web page
  could make the local server send the user's NTLM credentials to a server of
  its choosing through a `/download` link.
- Web UI: reject a negative or non-numeric `Content-Length` on `POST /export`
  with a 400. A negative value previously bypassed the form-size cap and made
  the request thread block until the client disconnected, and a non-numeric
  one produced a 500. An output directory containing a NUL byte is now also
  rejected with a 400 instead of failing later as a 500.
- Retry PromptBase queries when the connection drops while waiting for or
  reading the response (`RemoteDisconnected`, `IncompleteRead`,
  `ConnectionResetError`). These previously escaped the retry loop and crashed
  the CLI with a traceback instead of retrying or reporting a clean error.
- Build the wheel reliably when a generated `exports/` directory is present.
  setuptools' flat-layout auto-discovery treated `exports/` as a second
  top-level package and aborted with `Multiple top-level packages discovered`,
  breaking `pip install .` / `uv tool install .` from a working tree that had
  already produced exports. The package list is now declared explicitly.

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

- Pause briefly between successive pages of a paginated PromptBase query so
  large-profile fetches stay polite and avoid rate limits; single-page fetches
  never wait.

### Removed

- Drop the PyPI publish workflow. The tool is used by cloning the repository or
  pinning the GitHub Action to a release tag, so it no longer ships a failing
  publish job; see `RELEASE.md` for how to re-add PyPI distribution later.

### Internal

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

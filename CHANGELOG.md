# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Fixed

- A comparison no longer depends on the order of the records. A new listing that
  shared an existing listing's title, if it came first, used to be reported as a
  change to that listing, and the unchanged original as added. Now every slug match
  is made before any title match, a title only pairs records when one side has no
  slug (a TXT catalog), and each old record is paired once; two old TXT records
  with the same title used to leave one of them unmatched. Records that share a
  title are paired with an identical old record first, so reordering them is not
  reported as a change. Neither live profile
  checked (@acb, @emanema) has repeated titles today; property tests found it.

### Changed

- Development only: every tool CI installs is pinned by hash and installed with
  `--require-hashes` (`requirements-dev.txt` for the checks,
  `requirements-release.txt` for building), and the exporter is installed with
  `--no-deps`, since it has no dependencies. `scripts/lock_requirements.py` rebuilds
  the files, resolving the dependencies for each Python version CI uses. The
  OpenSSF Scorecard counted these installs as unpinned.
- Each release also carries its signed Sigstore bundle
  (`promptbase_profile_exporter-X.Y.Z.sigstore.json`), so a download can be verified
  against that file with `gh attestation verify --bundle`.

- Development only: property tests compare random catalogs that were changed in
  known ways (new listings, some reusing a title; removals; edits; counter moves;
  re-wrapped text; TXT catalogs with repeated titles) and require the diff to
  report exactly those changes, count each record once, and mirror when the sides
  are swapped.
- Development only: CodeQL scans the Python code and the GitHub Actions workflows
  (security-extended queries) on every pull request, on `main`, and weekly; the
  OpenSSF Scorecard grades the repository's supply-chain practices weekly and
  feeds the new README badge.

## 0.13.0 - 2026-10-01

### Added

- Each release now carries a wheel and an sdist, built by a GitHub Actions workflow
  from the release tag with a signed build provenance attestation;
  `gh attestation verify` checks a downloaded file (see SECURITY.md). There is
  still no PyPI package.

### Deprecated

- Python 3.10 reaches end of life on 31 October 2026 (PEP 619). This is the last
  release line that supports it: the first release after that date will need
  Python 3.11 or newer, which also makes `--config` TOML files work everywhere.

### Security

- Text from PromptBase can no longer change how a report renders. Titles and values
  in a Markdown diff report (`--diff-output report.md`, and the Action's run summary)
  are escaped, so a title cannot add links or headings or hide the rest of the
  report behind `<!--`. Titles printed to the terminal or a CI log are kept on one
  line with control characters removed, so a title cannot fake extra log lines, a
  GitHub Actions workflow command (`::error::`), or a terminal escape sequence.
- Responses from PromptBase are capped at 64 MiB, compressed or not (a real page is
  well under 1 MiB), and a response or document nested too deeply is a clean error
  instead of a traceback.
- On a web server bound beyond loopback, the comparison catalog must be named like
  an export, as downloads already were, so another machine cannot read values from
  other JSON or CSV files in the working directory or probe which files exist.
- The Action passes profiles after `--`, so a profile value that starts with `-`
  is never read as an option, and writes its artifact path output in the delimited
  form, so a newline in a path input cannot add another output.

### Fixed

- A server that keeps returning the same page (a cursor that does not advance) now
  stops the fetch after the second request with a clear error; it used to repeat
  the query up to the 100-page safety limit first.
- Comparisons keep listing kinds apart: a prompt and an app are no longer paired
  because they share a slug or, more often, a title. Comparing @acb's prompts with
  its apps used to report 132 "changed" listings; it now reports 243 removed and 211
  added. A TXT catalog, which records no kind, still matches by title.
- An app is no longer required to carry a `type` field: apps have no model type, so
  a future response without one is not reported as schema drift.
- In a `pb-history` Markdown report, a slug containing backticks can no longer end
  its code span early.
- `pb-web --open` on a server bound to `::` opens `http://[::1]:port/`.
- In the Action, a cut-off run summary now points to the full report in the step's
  log and the `diff-output` input; it used to point to a temporary file that is
  deleted when the job ends.

### Changed

- The pinned `actions/checkout` (v7.0.1) and `actions/setup-python` (v7.0.0) are
  updated in the Action, the workflows, and the documentation examples.
- Development only: resilience tests run the client against a real local HTTP
  server that answers with 5xx and 4xx errors, drops the connection before or in
  the middle of a body, truncates a compressed body, responds after the timeout,
  and pages with and without a working cursor.
- Development only: CI installs exact versions of its tools from
  `requirements-dev.txt` (Dependabot updates them), checks out without keeping the
  token in `.git/config`, and every test job, Windows and macOS included, is now
  required before a pull request can merge.
- The GitHub Action guide shows a scheduled `pb-history` workflow that keeps the
  history in the Actions cache and posts each report to the run's summary page;
  its commands were run as written from a clean install of v0.12.1. The nightly
  canary now also records two live `pb-history` snapshots and reports on them.
- Documentation only: the README (both languages) now lists `pb-history`, the web UI's
  Preview button and `--open`, `--config`, and `ndjson` where it had not; the CLI guide's
  contents list is complete; the coverage floor is stated as 85% everywhere.

## 0.12.1 - 2026-09-30

### Fixed

- A CSV catalog is now read with its line breaks intact: a description or title
  with `\r\n` or `\r` inside a quoted cell used to come back as `\n`, so `pb-convert`
  wrote a different catalog than a direct export. (Comparisons were not affected:
  they already treat every line break alike. An earlier version of this note said
  they were.) None of the profiles checked live (`@acb`, `@emanema`) has such a
  value today; it was found by the new round-trip fuzz test.
- A CSV export now quotes a cell that contains a lone carriage return on every
  supported Python. Python 3.10, 3.11, and 3.13 left it unquoted, so a reader took
  it as the end of the row; the same fuzz test failed there in CI. Output is
  otherwise byte for byte what it was.

### Changed

- Development only: a seeded fuzz test writes random records full of hostile
  characters (quotes, separators, Unicode line breaks, emoji, formula prefixes) in
  every lossless format and requires them to read back exactly; ruff now also
  checks bugbear, comprehension, simplify, ruff-specific, and security rules; the
  coverage gate is 85% (it was 70%, and the suite is at 93%).

## 0.12.0 - 2026-09-30

### Added

- `pb-history` (`promptbase-history`, or `python -m promptbase_exporter.history`)
  keeps snapshots of a profile's views, sales, downloads, favorites, reviews,
  rating, and price in a SQLite file, and reports what moved: `pb-history snapshot
  @acb --db history.sqlite` on a schedule, then `pb-history report --db
  history.sqlite` for the totals before and after, the top movers by a chosen
  counter, new and removed listings, and price changes, as Markdown, JSON, or a
  self-contained HTML page with trend charts (`--since DATE` or `--days N` pick the
  baseline). It uses the standard library's `sqlite3` only, stores values through
  parameterised queries, versions its file, and refuses a file that is not its own.
- The web UI has a **Preview matches** button: it applies your filters, sort, and
  limit and lists what an export would select (the counts, the text and image
  split, how many have no description, and a table of the first 100 with links),
  without writing anything. It is the first button, so Enter in a text field
  previews rather than exports, and it passes the same cross-origin and Host
  checks as an export. `pb-web --open` opens the page in your browser once the
  server is listening.

### Fixed

- In the web UI, the export result table now names the kind it exported ("Bundles",
  "Apps") instead of always saying "Prompts", and a table that is wider than a
  phone screen scrolls inside its own box instead of making the whole page scroll
  sideways.

## 0.11.0 - 2026-09-30

### Added

- `--config FILE` reads options from a `.json` or `.toml` file (TOML needs
  Python 3.11 or newer), with the long option names as keys plus `profiles`, so
  a recurring export is one short command. The file's entries become command-line
  arguments in front of the real ones, so they go through the same validation and
  anything typed on the command line overrides them. A problem in the file stops
  the run before anything is fetched and names the file. The `profile` argument
  is now optional when the file lists `profiles`; with neither, the usual usage
  error (exit code 2) is shown. Profiles and options may now alternate on the
  command line (`pb @a --mode all @b`), which used to be rejected.
- A new `ndjson` format (`--format ndjson`, `.ndjson`; `.jsonl` is accepted on
  input and inferred from `--output-file`): one compact JSON object per line
  with the JSON format's fields and no enclosing array, for `jq -c`,
  `pandas.read_json(lines=True)`, and warehouse loaders. U+2028, U+2029, and
  U+0085, which `str.splitlines()` treats as line breaks, are escaped so a
  record is always one physical line. It works with `--compare`,
  `--update-file`, `pb-diff`, `pb-convert`, the web UI, and the Action, and
  each line of a written file is checked.
- `--layout files` (Action input `layout`) writes one Markdown file per prompt
  instead of one file per catalog, into a folder per catalog
  (`exports/acb_all_prompts/<slug>.md`), for note tools that index front matter.
  The YAML front matter holds the record's fields as JSON scalars, which are valid
  YAML, so every string is quoted and a title such as `yes` or `2026-01-01` is never
  read back as a boolean or a date. File names are the slug made safe (no path
  separators or leading dots, Windows device names suffixed) and, when it had to
  change, carries a digest of the original slug, so a prompt's file never depends on
  which other prompts a run selects; nothing is ever deleted. U+0085, the
  U+007F to U+009F controls, U+2028, U+2029, and U+FEFF are escaped in the front
  matter so a YAML reader reads every value back exactly. It needs Markdown and
  cannot be combined with `--output-file`, `--compare`, or `--update-file`. The web
  UI has a "Layout" list for it.

## 0.10.0 - 2026-09-30

### Added

- `--extra-fields` (Action input `extra-fields`, a checkbox group in the web
  UI) adds `tags`, `engine`, `nsfw`, `featured`, `updated`, `last_sale`, and
  `unique_sales` to the markdown, JSON, CSV, and HTML exports. Only the fields
  you ask for are requested from PromptBase, so a default export is
  byte-identical to before and asking for all of them adds about 8 KB to a
  243-prompt download. A time PromptBase does not record is `null` (empty in
  CSV) rather than a zero, and `txt`, which cannot hold them, is an error. In
  CSV the tags are joined with `, `, or written as a JSON array if a tag
  contains a comma, so the cell can always be read back.
- `--item-type` (Action input `item-type`, a "Kind" list in the web UI) exports
  a profile's `bundle`s or `app`s as well as its `prompt`s (the default).
  Bundles and apps take their descriptions from PromptBase's public `Bundles`
  and `AppDetails` records, are written to `<user>_<mode>_bundles.<ext>` and
  `..._apps.<ext>` so they never overwrite a prompt catalog, link to
  `promptbase.com/bundle/<slug>` and `/app/<slug>`, and carry an `item_type`
  column; a prompt catalog keeps its original columns. Apps mirror the
  profile's prompts, are free, and have no `type`. Skills are not supported.
  The HTML catalog's markup gained a `data-noun` attribute so its heading and
  search count name the right kind.
- `pb-convert` (`promptbase-convert`, or `python -m promptbase_exporter.convert`)
  rewrites a saved catalog in another format without fetching anything. Its
  result is the file a direct export would have written, byte for byte, extra
  fields and bundle or app catalogs included. It reads JSON, CSV, and HTML
  catalogs and refuses TXT and Markdown, which lack columns and would need
  invented values; it stops on a missing column, an empty number, or an
  unreadable value instead of guessing (a boolean where a number belongs, an
  entry that is not a record, or an extra field missing from some records
  included), checks the result in a scratch file before it replaces anything,
  and never overwrites its source. `--csv-safe` and `--from-csv-safe` cover
  protected CSVs.
- With `compare` or `update-file`, the GitHub Action now shows the diff report
  on the workflow run's summary page (`step-summary`, on by default, `false`
  turns it off). A report over 900 KB is cut, with a note to use `diff-json`;
  the summary is still written when `fail-on-diff` fails the step. The Action
  docs gain a recipe that opens a pull request when the catalog changes.

### Fixed

- The commands no longer crash with a `UnicodeEncodeError` when their output is
  redirected to a pipe or a log file on Windows and a message names a prompt
  title (or a path) that the console's legacy code page cannot encode, such as
  one with an emoji. Such characters are printed as `?`; catalogs are still
  written as UTF-8.

## 0.9.3 - 2026-09-30

### Changed

- Exports download far less data. Queries ask Firestore for only the fields the
  exporter reads and accept a gzip-compressed response, so a 243-prompt
  profile transfers about 100 KB instead of 3.8 MB and fetches roughly a third
  faster. The exported catalogs are unchanged.
- A profile URL copied without its scheme, such as
  `promptbase.com/profile/acb`, is now accepted instead of being looked up as
  a username.

### Fixed

- `--update-file` rejects a `--format` that does not match the file's
  extension. Previously `--update-file catalog.csv --format json` wrote JSON
  into `catalog.csv`, which every later run then failed to read as CSV.
- The web UI starts on an IPv6 address such as `--host ::1`, including a
  scoped link-local one such as `fe80::1%eth0`; it used to fail to bind.
  Requests addressed to `[::1]:port` pass the Host/Origin checks.
- A write that fails part-way under `--output-file` without `--overwrite` no
  longer leaves a truncated file behind that blocks the retry with "already
  exists".
- A JSON diff report stays valid JSON when a compared catalog holds a
  non-finite price such as `nan`: the value is reported as `null` rather than
  a bare `NaN`.

## 0.9.2 - 2026-09-30

### Fixed

- A CSV that merely starts with a UTF-8 BOM, such as one saved by Excel, is no
  longer treated as a `--csv-safe` catalog. 0.9.1 used the BOM as the safe-mode
  marker and stripped a real leading apostrophe (`'=SUM(A1)` read as
  `=SUM(A1)`). Nothing inside a CSV can reliably mark safe mode, so reading
  one is now declared per input, independently of how the new file is
  written: `--compare-csv-safe` (Action: `compare-csv-safe`) for the
  `--compare` / `--update-file` catalog, `--previous-csv-safe` and
  `--current-csv-safe` for `pb-diff`, and a "Comparison catalog is a protected
  CSV" option in the web UI. A safe export can therefore be compared with a
  plain CSV and vice versa. Without these flags every CSV is read verbatim;
  refreshing a 0.9.x safe catalog now takes
  `--update-file x.csv --csv-safe --compare-csv-safe`. A BOM is still written
  in safe mode for Excel and is stripped from every CSV that is read, so a CSV
  re-saved by Excel keeps its `title` column.
- The Markdown writer leaves an empty type or domain empty instead of writing
  `unknown`, so a real type or domain named `unknown` survives a Markdown
  round trip. A Markdown catalog written by 0.9.1 or earlier reads an old
  `unknown` placeholder literally, which shows once as a change for prompts
  with an empty type or domain until the catalog is rewritten.

## 0.9.1 - 2026-09-30

### Fixed

- Comparing a CSV catalog no longer misreads text that genuinely starts with
  an apostrophe before `=`, `+`, `-`, or `@` (such as `'=SUM(A1)`), which 0.9.0
  reported as changed on every run. A `--csv-safe` file now starts with a UTF-8
  BOM (which Excel also needs to detect UTF-8); only such a file has its
  escaping undone, and exactly, while a plain CSV is read verbatim. The escape
  is now reversible (`'=x` becomes `''=x`). A safe CSV written by 0.9.0 has no
  BOM and is read as plain, so its escaped cells show as changed once until
  it is rewritten.
- `--compare`, `--update-file`, and `pb-diff` again report `type`, `domain`,
  or `price` values that were cleared in the newer catalog. 0.9.0 skipped any
  field that was empty on either side; now a field is skipped only when the
  older catalog has no value or the newer catalog's format (TXT) does not
  store it. Markdown's `unknown` placeholder for an empty type or domain is
  read as empty, so Markdown and JSON exports of the same prompts compare
  equal.
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

### Added

- Type hints are shipped with the package (a `py.typed` marker), so mypy and other
  checkers use them in projects that import the exporter.
- Python 3.13 is supported and tested.

### Fixed

- Stop the Markdown export validation check from over-counting prompts whose
  descriptions contain `## N.` heading lines, which previously failed valid
  exports with a spurious record-count error.
- Do not overwrite the existing catalog during an `--update-file` run when the
  requested `--diff-output` report cannot be written; the run now aborts with
  exit code 1 and leaves the catalog untouched.
- Return HTTP 400 (not 500) for invalid `since`/`until` dates in the web UI.

### Changed

- Date parsing lives in its own module, shared by the command line and the web UI,
  instead of the web UI importing it from the command-line module.
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

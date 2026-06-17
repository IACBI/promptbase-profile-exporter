# PromptBase Profile Exporter

[![tests](https://github.com/IACBI/promptbase-profile-exporter/actions/workflows/tests.yml/badge.svg)](https://github.com/IACBI/promptbase-profile-exporter/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Export the public prompts on any PromptBase profile into clean catalog files —
TXT, Markdown, JSON, or CSV.

Give it a profile URL (or just a username) and it finds that profile's public
prompts, pulls each prompt's title and description, and writes tidy exports.
It uses only the Python standard library, needs no login or API key, and reads
only the same public data the PromptBase website serves.

**Who it's for:** prompt creators who want to back up, audit, share, or publish
a clear catalog of their PromptBase listings.

```bash
# No dependencies to install — clone the repo, then run:
python -m promptbase_exporter https://promptbase.com/profile/acb
# -> writes exports/acb_all_prompts.txt, acb_text_prompts.txt, acb_image_prompts.txt
```

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Filtering and sorting](#filtering-and-sorting)
- [Output formats](#output-formats)
- [Prompt groups](#prompt-groups)
- [Compare and update catalogs](#compare-and-update-catalogs)
- [Local web UI](#local-web-ui)
- [GitHub Action](#github-action)
- [Options reference](#options-reference)
- [Exit codes](#exit-codes)
- [Validation](#validation)
- [How it works](#how-it-works)
- [Responsible use](#responsible-use)
- [Development](#development)
- [Repository structure](#repository-structure)
- [License](#license)

## Features

- Accepts a full profile URL, profile path, username, or `@username`.
- Resolves the profile automatically through public PromptBase data.
- Exports the title and description of every approved prompt.
- Splits output into `all`, `text`, and `image` views (`text-only` and
  `image-only` aliases included).
- Writes `txt`, `markdown`, `json`, or `csv`.
- Filters by domain, model/type, free/paid status, price range, date, or count.
- Sorts by newest, oldest, title, price, views, sales, downloads, favorites,
  or rating.
- Includes richer JSON/CSV metadata (views, sales, downloads, favorites,
  rating, reviews, and more).
- Compares current exports against a previous catalog, or refreshes one in place.
- Ships a local, no-framework web UI and a reusable GitHub Action.
- Paginates large profiles and retries transient network failures with backoff.
- Validates output before writing, and uses only the Python standard library.

## Installation

You need **Python 3.10 or newer**. There are no third-party dependencies.

```bash
python --version   # confirm 3.10+
git clone https://github.com/IACBI/promptbase-profile-exporter.git
cd promptbase-profile-exporter
```

From the project folder you can run the tool as a module straight away:

```bash
python -m promptbase_exporter --help
```

### Install a command (recommended)

Installing the package puts named commands on your `PATH` so you can drop the
`python -m promptbase_exporter` prefix. Each install method exposes the same
commands: `promptbase-export` / `pb` (the exporter) and
`promptbase-export-web` / `pb-web` (the web UI).

Using [uv](https://docs.astral.sh/uv/) — install it once, run it anywhere:

```bash
uv tool install .        # from the cloned project folder
pb --help                # short alias, available in any directory
pb https://promptbase.com/profile/acb
```

Or with pip:

```bash
python -m pip install -e .
pb --help                # or: promptbase-export --help
```

Working inside the project without installing globally? `uv run` uses the
project's environment:

```bash
uv run pb @acb --dry-run
uv run pb-web            # the local web UI
```

> Throughout this README, `python -m promptbase_exporter ...`,
> `promptbase-export ...`, and `pb ...` are interchangeable. Likewise
> `python -m promptbase_exporter.web`, `promptbase-export-web`, and `pb-web`.

## Quick start

Export a profile with default settings:

```bash
python -m promptbase_exporter https://promptbase.com/profile/acb
```

This creates an `exports/` folder containing three TXT files (the default
`--mode split`):

```text
exports/acb_all_prompts.txt     # every approved prompt
exports/acb_text_prompts.txt    # text-domain prompts only
exports/acb_image_prompts.txt   # image-domain prompts only
```

Preview what would be exported without writing anything:

```bash
python -m promptbase_exporter @acb --dry-run
```

## Usage

The only required argument is the profile, given as a URL, path, username, or
`@username`. Everything else has a sensible default — see the
[Options reference](#options-reference) for the full list.

Choose which files to write with `--mode`:

```bash
python -m promptbase_exporter @acb --mode all      # one combined file
python -m promptbase_exporter @acb --mode text     # text prompts only
python -m promptbase_exporter @acb --mode image    # image prompts only
python -m promptbase_exporter @acb --mode split    # all + text + image (default)
```

`text-only` and `image-only` are accepted aliases for `text` and `image`.

Pick an output format and directory:

```bash
python -m promptbase_exporter @acb --format markdown          # GitHub-readable catalog
python -m promptbase_exporter @acb --mode all --format json   # for another tool
python -m promptbase_exporter @acb --mode text --format csv   # for spreadsheets
python -m promptbase_exporter @acb --mode all --output-dir my_exports
```

Control the console output:

```bash
python -m promptbase_exporter @acb --verbose   # print extra filtering details
python -m promptbase_exporter @acb --quiet      # suppress normal output
```

Inspect a profile without writing files:

```bash
python -m promptbase_exporter @acb --list-domains   # domain counts, then exit
python -m promptbase_exporter @acb --list-types     # type counts, then exit
```

Create repeatable, timestamped backups:

```bash
python -m promptbase_exporter @acb --timestamp-filenames
# -> exports/acb_all_prompts_20260101_120000.txt, ...
```

## Filtering and sorting

Filters run **before** `--mode` splits the output, so they narrow the prompt set
first; sorting is applied afterward, just before writing.

```bash
python -m promptbase_exporter @acb --type claude --mode text       # only Claude text prompts
python -m promptbase_exporter @acb --paid-only --min-price 2 --max-price 6
python -m promptbase_exporter @acb --since 2026-01-01 --until 2026-12-31
python -m promptbase_exporter @acb --sort views --limit 25         # 25 most-viewed
```

Available filters: `--domain`, `--type`, `--free-only`, `--paid-only`,
`--min-price`, `--max-price`, `--since`, `--until`, `--limit`.
`--since`/`--until` accept a date (`YYYY-MM-DD`) or an ISO datetime; a bare date
is treated as UTC.

Sort options for `--sort`: `newest` (default), `oldest`, `title`, `price`,
`views`, `sales`, `downloads`, `favorites`, `rating`.

A combined example — paid GPT prompts as JSON, newest first:

```bash
python -m promptbase_exporter @acb --mode all --format json --type gpt --paid-only
```

## Output formats

The default `.txt` format uses a simple numbered layout:

```text
1.
Title: Example Prompt Title
Description:
The public PromptBase description appears here.

2.
Title: Another Prompt
Description:
Another public description appears here.
```

Markdown output is designed for GitHub-readable catalogs. JSON and CSV output
include the full metadata set, in this field order:

```text
title, description, slug, url, type, domain, created, created_iso, price,
discount, views, sales, downloads, favorites, rating, reviews
```

## Prompt groups

`--mode` groups prompts by their PromptBase `domain`:

- `text` — prompts where `domain` is `text`.
- `image` — prompts where `domain` is `image`.
- `all` — every approved prompt for the profile, including text, image, video,
  or any other domain present.

Each group is sorted newest-to-oldest by creation time by default.

## Compare and update catalogs

Compare the current profile against a previously exported catalog to see what
changed. The comparison report lists added, removed, and changed prompts.

```bash
# Print a diff against an existing export:
python -m promptbase_exporter @acb --mode all --format json \
  --compare exports/acb_all_prompts.json

# Also write the report to a file:
python -m promptbase_exporter @acb --mode all \
  --compare exports/acb_all_prompts.json --diff-output exports/catalog-diff.md
```

Refresh an existing catalog in place — this compares against the current file,
then rewrites it with the latest prompts:

```bash
python -m promptbase_exporter @acb --mode all --update-file exports/acb_all_prompts.json
```

Write a single export to an exact path (instead of the generated filename).
Existing files are protected unless you pass `--overwrite`:

```bash
python -m promptbase_exporter @acb --mode text \
  --output-file exports/text-prompts.csv --format csv
```

For CI, make catalog changes fail the command with [exit code `2`](#exit-codes):

```bash
python -m promptbase_exporter @acb --mode all \
  --compare exports/acb_all_prompts.json --fail-on-diff
```

> `--compare`, `--update-file`, and `--output-file` require an explicit
> single-output `--mode` (`all`, `text`, or `image`), not `split`. When an
> `--update-file` run cannot write its requested `--diff-output` report, it
> aborts with exit code `1` and leaves the existing catalog **unchanged**.

## Local web UI

A small built-in web UI lets you run exports from the browser:

```bash
python -m promptbase_exporter.web      # or: promptbase-export-web
```

Then open <http://127.0.0.1:8765/>. Change the bind address or port with
`--host` and `--port`:

```bash
promptbase-export-web --host 127.0.0.1 --port 9000
```

After an export, each generated file gets a **Download** link so you can save it
straight from the browser without hunting for it on disk.

### Web UI security model

The web UI is intended for **local, single-user** use:

- It binds to `127.0.0.1` (loopback) by default and is unauthenticated. The
  `/export` endpoint fetches remote data and writes files, so do not expose it
  to untrusted networks. Passing `--host 0.0.0.0` prints a warning because it
  makes that endpoint reachable by other hosts.
- Requests to `/export` are checked for a matching `Host` header and a
  same-origin `Origin`/`Sec-Fetch-Site`, mitigating CSRF and DNS-rebinding from
  pages opened in your browser.
- Exports are confined to the directory the server was started in. Absolute
  paths and `..` traversal in the "Output directory" field are rejected. (The
  CLI, which you run yourself, still accepts arbitrary paths.)
- The `/download` endpoint only serves files inside that same directory whose
  names match the exporter's own pattern
  (`<username>_<mode>_prompts[...].{txt,md,json,csv}`). It cannot read arbitrary
  files — not even an unrelated `secrets.json` in the working directory — even
  though it is unauthenticated.

## GitHub Action

This repository is also a composite GitHub Action for scheduled or on-demand
catalog exports:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.7.0
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: split
    format: markdown
    output-dir: exports
```

See [docs/github-action.md](docs/github-action.md) for the full input reference,
scheduled exports, artifact uploads, and workflows that commit updated catalogs
back to a repository.

## Options reference

Run `python -m promptbase_exporter --help` for the authoritative list. The
positional `profile` argument is required; every option below is optional.

| Option | Default | Description |
| --- | --- | --- |
| `profile` (positional) | — | PromptBase profile URL, path, username, or `@username`. **Required.** |
| `-m`, `--mode` | `split` | Which files to write: `split` (all + text + image), `all`, `text`, or `image`. Aliases: `text-only`, `image-only`. |
| `-o`, `--output-dir` | `exports` | Directory where generated files are written. |
| `-f`, `--format` | `txt` | Output format: `txt`, `markdown`, `json`, or `csv`. Inferred from the extension when `--output-file`/`--update-file` is used. |
| `--sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, or `rating`. |
| `--domain` | — | Comma-separated domain filter, e.g. `text,image,video`. |
| `--type` | — | Comma-separated PromptBase type filter, e.g. `gpt,claude`. |
| `--free-only` | — | Keep only free prompts. Mutually exclusive with `--paid-only`. |
| `--paid-only` | — | Keep only paid prompts. Mutually exclusive with `--free-only`. |
| `--min-price` | — | Keep prompts priced at or above this amount. |
| `--max-price` | — | Keep prompts priced at or below this amount. |
| `--since` | — | Keep prompts created on or after this date or ISO datetime. |
| `--until` | — | Keep prompts created on or before this date or ISO datetime. |
| `--limit` | — | Keep only the first N prompts after filtering and sorting. |
| `--allow-missing-descriptions` | off | Write files even if some prompt descriptions are missing. |
| `--timestamp-filenames` | off | Append `YYYYMMDD_HHMMSS` to generated filenames. |
| `--output-file` | — | Write a single export to this exact path. Requires `--mode all`, `text`, or `image`. |
| `--overwrite` | off | Allow `--output-file` to replace an existing file. |
| `--compare` | — | Compare against an existing JSON/CSV/TXT/Markdown export. |
| `--diff-output` | — | Write the comparison report to this path. Requires `--compare` or `--update-file`. |
| `--fail-on-diff` | off | Exit with code `2` when the comparison finds added, removed, or changed records. |
| `--update-file` | — | Compare against an existing export and rewrite it in place. Requires `--mode all`, `text`, or `image`. |
| `--dry-run` | off | Fetch, filter, and validate without writing files. |
| `--list-domains` | off | Print domain counts after filters, then exit. |
| `--list-types` | off | Print PromptBase type counts after filters, then exit. |
| `--quiet` | off | Suppress normal command output. Mutually exclusive with `--verbose`. |
| `--verbose` | off | Print extra filtering details. Mutually exclusive with `--quiet`. |
| `--version` | — | Print the version and exit. |
| `--help` | — | Print help and exit. |

The web UI command, `promptbase-export-web`, accepts `--host` (default
`127.0.0.1`), `--port` (default `8765`), and `--version`.

## Exit codes

The `promptbase-export` command uses these exit codes so it can be scripted in CI:

| Code | Meaning |
| ---- | ------- |
| `0` | Success (including a clean `--compare` with no differences). |
| `1` | Error: invalid arguments, profile not found, no matching prompts, missing descriptions without `--allow-missing-descriptions`, or a write/validation failure (including a diff report that could not be written). |
| `2` | `--fail-on-diff` was set and `--compare`/`--update-file` found added, removed, or changed records. |

Use code `2` to gate a pipeline on catalog drift; the GitHub Action exposes this
through its `fail-on-diff` input. Code `1` and code `2` stay distinct — an
operational failure never masquerades as drift, and a failed diff write never
overwrites an `--update-file` catalog.

## Validation

Before writing files, the tool checks that:

- the profile exists,
- approved prompts were found,
- prompt details were matched by slug when available,
- output records are sorted newest to oldest, and
- written files contain the expected number of records.

If descriptions are missing for any prompt, the command exits non-zero unless
`--allow-missing-descriptions` is used.

The exporter also checks for expected public data fields. If PromptBase changes
its public data model, the command reports a schema-change error instead of
silently producing a misleading catalog.

## How it works

PromptBase is a dynamic site backed by public Firebase/Firestore data. This tool
reads the same public data used by the profile and prompt pages:

1. Extract the username from the profile input.
2. Resolve the profile document and user id.
3. Fetch approved prompt items for that user id.
4. Fetch matching prompt detail documents.
5. Merge title, slug, type, domain, creation time, and description.
6. Apply user-selected filters and sorting.
7. Write clean exports in the requested format.

No login, API key, browser automation, or paid account is required.

## Responsible use

Only export profiles and data you are allowed to use. This project is intended
for public PromptBase listing metadata and personal backup/catalog workflows.
PromptBase can change its public data model at any time, which may require
updates to this tool. See [SECURITY.md](SECURITY.md) for the security policy and
how to report issues.

## Development

Install the dev tools, then run the lint, type, and test checks:

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m coverage report
```

Build the package:

```bash
python -m build
python -m twine check dist/*
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the pull-request checklist and
[RELEASE.md](RELEASE.md) for the release process.

### Host your own copy

To publish your own copy of this project to a new GitHub repository:

```bash
git init
git add .
git commit -m "Initial PromptBase profile exporter"
git branch -M main
git remote add origin https://github.com/your-username/promptbase-profile-exporter.git
git push -u origin main
```

Replace `your-username` with your GitHub username and create the empty
repository on GitHub before pushing.

## Repository structure

```text
.claude/                      # Claude Code project config (settings, skills, agents, hooks)
.github/
  ISSUE_TEMPLATE/             # bug report and feature request templates
  dependabot.yml              # weekly GitHub Actions update checks
  pull_request_template.md
  workflows/
    tests.yml                 # lint, type, test, and packaging checks
promptbase_exporter/
  __init__.py
  __main__.py                 # `python -m promptbase_exporter` entry point
  cli.py                      # command-line interface and argument parsing
  client.py                   # public PromptBase/Firestore data access
  dates.py                    # shared date/datetime parsing
  diffing.py                  # catalog comparison and diff reports
  formatting.py               # filtering, sorting, and output writers
  models.py                   # Profile and PromptRecord data types
  py.typed                    # PEP 561 marker exposing type hints
  web.py                      # local web UI
tests/
  test_cli.py
  test_client.py
  test_dates.py
  test_diffing.py
  test_formatting.py
  test_profile_input.py
  test_web.py
docs/
  github-action.md            # GitHub Action usage guide
action.yml                    # composite GitHub Action definition
CLAUDE.md                     # repo guide for Claude Code
pyproject.toml
requirements.txt
CHANGELOG.md
CONTRIBUTING.md
RELEASE.md
SECURITY.md
LICENSE
```

## License

MIT License. See [LICENSE](LICENSE).

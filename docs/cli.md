# Command-line reference

Everything the `pb` command can do. `pb` and `promptbase-export` are the same
command; without installing the package, use `python -m promptbase_exporter`
from the project folder. `pb --help` always prints the authoritative option
list for your version.

- [Choosing a profile](#choosing-a-profile)
- [Modes: which catalogs to write](#modes-which-catalogs-to-write)
- [Output formats](#output-formats)
- [Filtering and sorting](#filtering-and-sorting)
- [Writing to an exact path](#writing-to-an-exact-path)
- [Comparing and updating catalogs](#comparing-and-updating-catalogs)
- [Comparing two files offline](#comparing-two-files-offline)
- [Inspecting without writing](#inspecting-without-writing)
- [Options](#options)
- [Exit codes](#exit-codes)
- [Validation and safety checks](#validation-and-safety-checks)
- [How it works](#how-it-works)

## Choosing a profile

The only required argument is the profile. All of these are equivalent:

```bash
pb https://promptbase.com/profile/acb
pb profile/acb
pb acb
pb @acb
```

Pass several profiles to export each of them in one run. Each profile gets its
own files, `--timestamp-filenames` stamps them all with the same time, and a
profile that fails is reported without stopping the others (the run then exits
with `1`). Duplicates are exported once.

```bash
pb @acb @dreamydesigns emanema --format json
```

`--output-file`, `--compare`, and `--update-file` each describe one catalog, so
they take a single profile.

## Modes: which catalogs to write

`--mode` decides which files are written. Prompts are grouped by their
PromptBase `domain`:

| Mode | Writes |
| --- | --- |
| `split` (default) | Three files: `all`, `text`, and `image` |
| `all` | Every approved prompt, whatever its domain (text, image, video, ...) |
| `text` | Prompts whose domain is `text` (alias: `text-only`) |
| `image` | Prompts whose domain is `image` (alias: `image-only`) |

Generated files are named `<username>_<mode>_prompts.<ext>` and written to
`--output-dir` (default `exports/`). Add `--timestamp-filenames` to keep a
history of runs side by side:

```bash
pb @acb --timestamp-filenames
# -> exports/acb_all_prompts_20260101_120000.txt, ...
```

## Output formats

Choose with `--format`: `txt` (default), `markdown`, `json`, `csv`, or `html`.

**TXT** is a plain numbered list of titles and descriptions:

```text
1.
Title: Minimalist Logo Designer
Description:
Creates clean, modern logo concepts.
```

**Markdown** is a catalog that renders well on GitHub, with the key metadata
per prompt:

```markdown
# PromptBase Prompt Export

## 1. Minimalist Logo Designer

- URL: https://promptbase.com/prompt/minimalist-logo-designer
- Domain: image
- Type: midjourney
- Price: 3.99
- Created: 2026-01-01T00:00:00+00:00
- Views: 1520
- Sales: 48

Creates clean, modern logo concepts.
```

**JSON** and **CSV** carry the full metadata set, in this field order:

```text
title, description, slug, url, type, domain, created, created_iso, price,
discount, views, sales, downloads, favorites, rating, reviews
```

`created` is milliseconds since the Unix epoch; `created_iso` is the same
moment as an ISO 8601 UTC timestamp.

**CSV and spreadsheets.** A cell that starts with `=`, `+`, `-`, or `@` is run
as a formula when the file is opened in Excel, LibreOffice, or Google Sheets.
Prompt titles and descriptions are written by whoever owns the profile, so if
you open CSV exports of other people's profiles in a spreadsheet, add
`--csv-safe`: such cells get a leading apostrophe, which spreadsheets display
as plain text. Numeric columns are never changed. It is off by default so the
CSV stays byte-for-byte faithful for scripts that parse it. Text that already
starts with an apostrophe before one of those characters gets one more, so the
escape is always reversible.

A `--csv-safe` file starts with a UTF-8 byte order mark (BOM). Excel needs it
to open a UTF-8 CSV with the right encoding, and it tells `--compare`,
`--update-file`, and `pb-diff` to undo the escaping exactly; a CSV without it
is read verbatim. Safe and plain catalogs of the same prompts therefore compare
equal, and a title that genuinely starts with `'=` is never misread. Python
scripts reading a safe file should open it with `encoding="utf-8-sig"`. When
you refresh a safe catalog with `--update-file`, pass `--csv-safe` again to
keep it safe.

```bash
pb @acb --mode all --format csv --csv-safe
```

**HTML** is a single self-contained page: a readable catalog with a search box
that filters prompts as you type, in light or dark mode, with no external
files or network requests. Every value is HTML-escaped, and the page also
embeds the full records as JSON, so it works with `--compare`,
`--update-file`, and `pb-diff` like the JSON and CSV formats.

```bash
pb @acb --mode all --format html    # -> exports/acb_all_prompts.html
```

## Filtering and sorting

Filters narrow the prompt set first; `--mode` then splits what remains, and
sorting is applied just before writing.

```bash
pb @acb --type claude --mode text               # only Claude text prompts
pb @acb --paid-only --min-price 2 --max-price 6
pb @acb --since 2026-01-01 --until 2026-12-31
pb @acb --sort views --limit 25                 # the 25 most-viewed prompts
pb @acb --min-sales 1 --min-rating 4.5          # proven, well-rated prompts
pb @acb --mode all --format json --type gpt --paid-only
```

- `--domain` and `--type` take comma-separated, case-insensitive lists.
- `--free-only` and `--paid-only` cannot be combined.
- `--since` and `--until` accept `YYYY-MM-DD` or an ISO datetime. A bare date
  is read as UTC, and `--until` includes the whole day.
- `--min-sales` and `--min-rating` keep prompts at or above the value. A
  prompt without reviews has a rating of `0`.
- `--limit` keeps the first N prompts *after* sorting, and before `--mode`
  splits them into files. With the default `split` mode, `--limit 10` writes
  the 10 newest prompts to the `all` file and, of those 10, the text ones to
  the `text` file and the image ones to the `image` file. For "10 of each",
  run `--mode text --limit 10` and `--mode image --limit 10`.

Sort keys: `newest` (default), `oldest`, `title`, `price`, `views`, `sales`,
`downloads`, `favorites`, `rating`. Numeric sorts put the highest first.

## Writing to an exact path

`--output-file` writes a single catalog to a path you choose instead of the
generated filename. The format is inferred from the extension unless you pass
`--format`. An existing file is never replaced unless you add `--overwrite`.

```bash
pb @acb --mode text --output-file catalogs/text-prompts.csv
```

## Comparing and updating catalogs

`--compare` checks the current profile against a catalog you exported earlier
and reports what was added, removed, or changed. It reads JSON, CSV, TXT,
Markdown, and HTML catalogs. Records are matched by slug, falling back to title for
formats that do not store one.

```bash
pb @acb --mode all --format json --compare exports/acb_all_prompts.json
pb @acb --mode all --compare exports/acb_all_prompts.json \
  --diff-output exports/catalog-diff.md
```

The report looks like this:

```markdown
# PromptBase Catalog Diff

Summary:
- Added: 0
- Removed: 1
- Changed: 1
- Unchanged: 0

## Changed

- Minimalist Logo Designer (minimalist-logo-designer)
  Changed fields: description, price
  - description: 412 -> 468 characters
  - price: 3.99 -> 4.99

## Removed

- Old prompt (old-prompt)
```

Title, type, domain, and price changes show the old and new value; a
description change shows its length before and after. A field that one of the
catalogs does not record (TXT stores no price, for example) is never reported
as changed.

`--diff-output` writes the report as JSON when the path ends in `.json` and as
Markdown otherwise, and can be repeated to write both. The JSON form is meant
for scripts:

```json
{
  "has_changes": true,
  "summary": {"added": 0, "removed": 1, "changed": 1, "unchanged": 0},
  "added": [],
  "removed": [{"title": "Old prompt", "slug": "old-prompt", "url": "https://promptbase.com/prompt/old-prompt"}],
  "changed": [
    {
      "title": "Minimalist Logo Designer",
      "slug": "minimalist-logo-designer",
      "url": "https://promptbase.com/prompt/minimalist-logo-designer",
      "fields": {"price": {"previous": 3.99, "current": 4.99}}
    }
  ]
}
```

```bash
pb @acb --mode all --compare exports/acb_all_prompts.json \
  --diff-output reports/diff.md --diff-output reports/diff.json
```

`--update-file` compares against an existing catalog and then rewrites it with
the current prompts, keeping its format:

```bash
pb @acb --mode all --update-file exports/acb_all_prompts.json
```

Add `--fail-on-diff` to exit with code `2` when anything changed, which lets a
CI job flag catalog drift. With `--update-file`, the file is still rewritten
before the command exits with `2`.

`--compare`, `--update-file`, and `--output-file` each produce a single
catalog, so they need `--mode all`, `text`, or `image` rather than `split`.
If an `--update-file` run cannot load the old catalog or write its
`--diff-output` report, it stops with exit code `1` and leaves the existing
catalog untouched. The rewrite itself is atomic: the new catalog is written
next to the old one and swapped in, so an interrupted run never leaves a
truncated file.

## Comparing two files offline

`pb-diff` (`promptbase-diff`, or `python -m promptbase_exporter.diff` without
installing) compares two catalogs you already have, with no network access. The
two files may be in different formats, for example last month's CSV and
today's HTML export. It takes the same `--diff-output` and `--fail-on-diff`
options and uses the same exit codes.

```bash
pb-diff old/acb_all_prompts.csv exports/acb_all_prompts.html
pb-diff old.json new.json --fail-on-diff --quiet --diff-output diff.json
```

## Inspecting without writing

```bash
pb @acb --dry-run        # fetch, filter, and validate; list planned outputs
pb @acb --list-domains   # prompt counts per domain
pb @acb --list-types     # prompt counts per PromptBase type
pb @acb --verbose        # also print the active filters and output settings
pb @acb --quiet          # print nothing except errors
```

## Options

| Option | Default | Description |
| --- | --- | --- |
| `profile` (positional) | required | PromptBase profile URL, path, username, or `@username`. Repeat to export several profiles. |
| `-m`, `--mode` | `split` | `split`, `all`, `text`, or `image`. Aliases: `text-only`, `image-only`. |
| `-o`, `--output-dir` | `exports` | Directory for generated files. |
| `-f`, `--format` | `txt` | `txt`, `markdown`, `json`, `csv`, or `html`. Inferred from the extension with `--output-file`/`--update-file`. |
| `--sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, or `rating`. |
| `--domain` | none | Comma-separated domain filter, e.g. `text,image,video`. |
| `--type` | none | Comma-separated PromptBase type filter, e.g. `gpt,claude`. |
| `--free-only` | off | Keep only free prompts. Cannot be combined with `--paid-only`. |
| `--paid-only` | off | Keep only paid prompts. Cannot be combined with `--free-only`. |
| `--min-price` | none | Keep prompts priced at or above this amount. |
| `--max-price` | none | Keep prompts priced at or below this amount. |
| `--since` | none | Keep prompts created on or after this date or ISO datetime. |
| `--until` | none | Keep prompts created on or before this date or ISO datetime. |
| `--min-sales` | none | Keep prompts with at least this many sales. |
| `--min-rating` | none | Keep prompts rated at or above this value. |
| `--limit` | none | Keep only the first N prompts after filtering and sorting, before `--mode` splits them. |
| `--csv-safe` | off | Prefix CSV text cells starting with `=`, `+`, `-`, or `@` with `'` so spreadsheets do not run them. Needs CSV output. |
| `--allow-missing-descriptions` | off | Write files even if some prompt descriptions are missing. |
| `--timestamp-filenames` | off | Append `_YYYYMMDD_HHMMSS` to generated filenames. |
| `--output-file` | none | Write a single catalog to this exact path. Needs `--mode all`, `text`, or `image`. |
| `--overwrite` | off | Allow `--output-file` to replace an existing file. |
| `--compare` | none | Compare against an existing JSON, CSV, TXT, Markdown, or HTML catalog. |
| `--diff-output` | none | Also write the comparison report to this path: JSON for `.json`, Markdown otherwise. Repeatable. Needs `--compare` or `--update-file`. |
| `--fail-on-diff` | off | Exit with code `2` when the comparison finds changes. |
| `--update-file` | none | Compare against an existing catalog and rewrite it in place. The rewrite is atomic, so a failed write leaves the old catalog intact. Needs `--mode all`, `text`, or `image`. |
| `--dry-run` | off | Fetch, filter, and validate without writing files. |
| `--list-domains` | off | Print domain counts after filters, then exit. |
| `--list-types` | off | Print PromptBase type counts after filters, then exit. |
| `--quiet` | off | Suppress normal output. Cannot be combined with `--verbose`. |
| `--verbose` | off | Print extra filtering details. Cannot be combined with `--quiet`. |
| `--version` | | Print the version and exit. |
| `-h`, `--help` | | Print help and exit. |

The web UI command, `pb-web` (`promptbase-export-web`), is described in
[web-ui.md](web-ui.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, including a comparison that found no differences. |
| `1` | Error (for a multi-profile run: in any of the profiles): invalid options, profile not found, no matching prompts, missing descriptions (without `--allow-missing-descriptions`), a network failure, or a write/validation failure. |
| `2` | `--fail-on-diff` was set and the comparison found added, removed, or changed prompts. |

Codes `1` and `2` never overlap: an operational failure is never reported as
drift, so a pipeline can treat `2` as "catalog changed" with confidence. The
GitHub Action exposes this through its `fail-on-diff` input and its
`exit-code` and `has-changes` outputs. `pb-diff` uses the same codes.

## Validation and safety checks

Before and after writing, the exporter checks that:

- the profile exists and has approved prompts,
- every selected prompt has a description, unless
  `--allow-missing-descriptions` is set,
- the records are ordered newest to oldest as fetched, and
- each written file contains exactly the expected number of records.

It also watches for changes to PromptBase's public data. If the fields it
depends on disappear from most records, it stops with a schema-change error
instead of producing an incomplete catalog. Transient network errors and rate
limits are retried with backoff.

## How it works

PromptBase is backed by public Firebase/Firestore data, the same data its
profile and prompt pages load in your browser. The exporter:

1. Extracts the username from the profile input.
2. Resolves the profile's owner id, from the profile document or, when that
   document lacks a username, from any item the profile owns.
3. Fetches the profile's approved prompts, paging through large profiles.
4. Fetches the matching prompt detail documents for descriptions.
5. Merges the two by slug, applies your filters and sort order, and writes the
   catalogs.

No login, API key, cookie, or browser automation is involved.

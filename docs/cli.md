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

Choose with `--format`: `txt` (default), `markdown`, `json`, or `csv`.

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

## Filtering and sorting

Filters narrow the prompt set first; `--mode` then splits what remains, and
sorting is applied just before writing.

```bash
pb @acb --type claude --mode text               # only Claude text prompts
pb @acb --paid-only --min-price 2 --max-price 6
pb @acb --since 2026-01-01 --until 2026-12-31
pb @acb --sort views --limit 25                 # the 25 most-viewed prompts
pb @acb --mode all --format json --type gpt --paid-only
```

- `--domain` and `--type` take comma-separated, case-insensitive lists.
- `--free-only` and `--paid-only` cannot be combined.
- `--since` and `--until` accept `YYYY-MM-DD` or an ISO datetime. A bare date
  is read as UTC, and `--until` includes the whole day.
- `--limit` keeps the first N prompts *after* sorting.

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
and reports what was added, removed, or changed. It reads JSON, CSV, TXT, and
Markdown catalogs. Records are matched by slug, falling back to title for
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

## Removed

- Old prompt (old-prompt)
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
catalog untouched.

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
| `profile` (positional) | required | PromptBase profile URL, path, username, or `@username`. |
| `-m`, `--mode` | `split` | `split`, `all`, `text`, or `image`. Aliases: `text-only`, `image-only`. |
| `-o`, `--output-dir` | `exports` | Directory for generated files. |
| `-f`, `--format` | `txt` | `txt`, `markdown`, `json`, or `csv`. Inferred from the extension with `--output-file`/`--update-file`. |
| `--sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, or `rating`. |
| `--domain` | none | Comma-separated domain filter, e.g. `text,image,video`. |
| `--type` | none | Comma-separated PromptBase type filter, e.g. `gpt,claude`. |
| `--free-only` | off | Keep only free prompts. Cannot be combined with `--paid-only`. |
| `--paid-only` | off | Keep only paid prompts. Cannot be combined with `--free-only`. |
| `--min-price` | none | Keep prompts priced at or above this amount. |
| `--max-price` | none | Keep prompts priced at or below this amount. |
| `--since` | none | Keep prompts created on or after this date or ISO datetime. |
| `--until` | none | Keep prompts created on or before this date or ISO datetime. |
| `--limit` | none | Keep only the first N prompts after filtering and sorting. |
| `--allow-missing-descriptions` | off | Write files even if some prompt descriptions are missing. |
| `--timestamp-filenames` | off | Append `_YYYYMMDD_HHMMSS` to generated filenames. |
| `--output-file` | none | Write a single catalog to this exact path. Needs `--mode all`, `text`, or `image`. |
| `--overwrite` | off | Allow `--output-file` to replace an existing file. |
| `--compare` | none | Compare against an existing JSON, CSV, TXT, or Markdown catalog. |
| `--diff-output` | none | Also write the comparison report to this path. Needs `--compare` or `--update-file`. |
| `--fail-on-diff` | off | Exit with code `2` when the comparison finds changes. |
| `--update-file` | none | Compare against an existing catalog and rewrite it in place. Needs `--mode all`, `text`, or `image`. |
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
| `1` | Error: invalid options, profile not found, no matching prompts, missing descriptions (without `--allow-missing-descriptions`), a network failure, or a write/validation failure. |
| `2` | `--fail-on-diff` was set and the comparison found added, removed, or changed prompts. |

Codes `1` and `2` never overlap: an operational failure is never reported as
drift, so a pipeline can treat `2` as "catalog changed" with confidence. The
GitHub Action exposes this through its `fail-on-diff` input.

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

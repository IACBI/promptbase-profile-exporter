# Command-line reference

Everything the `pb` command can do. `pb` and `promptbase-export` are the same
command; without installing the package, use `python -m promptbase_exporter`
from the project folder. `pb --help` always prints the authoritative option
list for your version.

- [Choosing a profile](#choosing-a-profile)
- [Configuration file](#configuration-file)
- [Prompts, bundles, and apps](#prompts-bundles-and-apps)
- [Modes: which catalogs to write](#modes-which-catalogs-to-write)
- [Output formats](#output-formats)
- [Filtering and sorting](#filtering-and-sorting)
- [Writing to an exact path](#writing-to-an-exact-path)
- [Comparing and updating catalogs](#comparing-and-updating-catalogs)
- [Comparing two files offline](#comparing-two-files-offline)
- [Converting a catalog offline](#converting-a-catalog-offline)
- [Tracking changes over time](#tracking-changes-over-time)
- [Inspecting without writing](#inspecting-without-writing)
- [Options](#options)
- [Exit codes](#exit-codes)
- [Validation and safety checks](#validation-and-safety-checks)
- [How it works](#how-it-works)

## Choosing a profile

The only required argument is the profile. All of these are equivalent:

```bash
pb https://promptbase.com/profile/acb
pb promptbase.com/profile/acb
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

## Configuration file

`--config FILE` reads options from a `.json` or `.toml` file, so a recurring
export is one short command. TOML needs Python 3.11 or newer, which is where the
standard library can read it; on Python 3.10 use JSON. The keys are the long
option names without the leading dashes (`-` or `_`, as you prefer), plus
`profiles`:

```toml
# promptbase.toml
profiles = ["@acb", "@dreamydesigns"]
mode = "all"
format = "json"
output_dir = "catalogs"
extra_fields = ["tags", "unique_sales"]
sort = "sales"
quiet = true
```

```bash
pb --config promptbase.toml                 # the file alone
pb --config promptbase.toml --limit 10      # the command line wins
pb @other --config promptbase.toml          # profiles given here replace the file's
pb @a --config promptbase.toml @b           # profiles and options may alternate
```

- **Values** are text, numbers, or `true`/`false` for on/off options such as
  `dry_run`, and may start with a hyphen (a folder called `-exports`). The comma-separated options (`domain`, `type`, `extra_fields`) also
  accept a list, and so does `diff_output`. `null` is ignored. Nested tables are
  not supported.
- **Precedence:** the file supplies defaults and anything on the command line
  overrides it. `diff_output` is repeatable, so the command line adds to the
  file's reports rather than replacing them. An on/off option set in the file
  cannot be switched off from the command line, and two options that exclude each
  other (`free_only` and `--paid-only`) conflict; edit the file or use another.
- **Paths** in the file are relative to the directory you run `pb` from, as they
  are on the command line, not to the file.
- **Validation:** the file goes through the same checks as the command line
  (choices, number types, exclusive options), and a problem stops the run before
  anything is fetched, with an error that names the file, for example
  `error: promptbase.toml: argument -f/--format: invalid choice: 'yaml'`.
- Only `pb` reads it. The Action has its own inputs, and `pb-web`, `pb-diff`,
  `pb-convert`, and `pb-history` take their options on the command line.

## Prompts, bundles, and apps

A profile lists more than prompts. `--item-type` picks the kind of listing to
export:

| `--item-type` | Exports | Filename | URL |
| --- | --- | --- | --- |
| `prompt` (default) | The profile's approved prompts | `acb_all_prompts.json` | `promptbase.com/prompt/<slug>` |
| `bundle` | Its approved bundles | `acb_all_bundles.json` | `promptbase.com/bundle/<slug>` |
| `app` | Its approved apps | `acb_all_apps.json` | `promptbase.com/app/<slug>` |

Bundles and apps use the same columns as prompts, with descriptions from
PromptBase's public bundle and app records, plus an `item_type` column (a
prompt catalog keeps its original columns). Everything else works as it does
for prompts: modes, formats, filters, `--extra-fields`, `--compare`, and
`--update-file`.

```bash
pb @acb --item-type bundle --mode all --format csv
pb @acb --item-type app --mode all --update-file exports/acb_all_apps.json
```

Two things to know. Apps are published alongside prompts, and a profile's apps
are free and mirror its prompts; they have no `type`, and a few have no
`domain`, so they appear in the `all` catalog only. Skills are not supported:
PromptBase does not publish them for the profiles checked.

## Modes: which catalogs to write

`--mode` decides which files are written. Prompts are grouped by their
PromptBase `domain`:

| Mode | Writes |
| --- | --- |
| `split` (default) | Three files: `all`, `text`, and `image` |
| `all` | Every approved prompt, whatever its domain (text, image, video, ...) |
| `text` | Prompts whose domain is `text` (alias: `text-only`) |
| `image` | Prompts whose domain is `image` (alias: `image-only`) |

Generated files are named `<username>_<mode>_prompts.<ext>` (`bundles` or `apps` for
the other kinds; `.ndjson` for NDJSON) and written to
`--output-dir` (default `exports/`). Add `--timestamp-filenames` to keep a
history of runs side by side:

```bash
pb @acb --timestamp-filenames
# -> exports/acb_all_prompts_20260101_120000.txt, ...
```

## Output formats

Choose with `--format`: `txt` (default), `markdown`, `json`, `ndjson`, `csv`, or `html`.

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

A `--csv-safe` file also starts with a UTF-8 byte order mark (BOM), which
Excel needs to open a UTF-8 CSV with the right encoding; Python scripts should
read it with `encoding="utf-8-sig"`.

Nothing inside a CSV can reliably say that it was written with `--csv-safe`
(any tool may add a BOM), so reading one back is declared separately, per
input, and never follows from how the new file is written:

- `--compare-csv-safe` reads the `--compare` / `--update-file` CSV catalog as
  safe and restores its escaped cells exactly;
- `pb-diff` takes `--previous-csv-safe` and `--current-csv-safe`, so a safe
  and a plain CSV compare correctly in either order.

Without these flags a CSV is read verbatim, so a title that genuinely starts
with `'=` is never misread. `--csv-safe` alone only protects the file being
written.

```bash
# refresh a safe catalog and keep it safe
pb @acb --mode all --update-file exports/acb_all_prompts.csv --csv-safe --compare-csv-safe
# protect a new export while comparing it with an ordinary CSV
pb @acb --mode all --format csv --csv-safe --compare old-plain.csv
pb-diff old-safe.csv new-plain.csv --previous-csv-safe
```

```bash
pb @acb --mode all --format csv --csv-safe
```

**NDJSON** (newline-delimited JSON, `.ndjson`; `.jsonl` is read too) is one
compact JSON object per line, with the same fields as the JSON format and no
enclosing array. It can be read and written a record at a time, which suits
`jq -c`, `pandas.read_json(path, lines=True)`, and warehouse loaders, and it
works with `--compare`, `--update-file`, `pb-diff`, and `pb-convert` like JSON.
The characters U+2028, U+2029, and U+0085, which some line readers treat as
line breaks, are written as `\u` escapes, so a record is always exactly one
physical line.

```bash
pb @acb --mode all --format ndjson   # -> exports/acb_all_prompts.ndjson
jq -c 'select(.sales > 0) | {slug, sales}' exports/acb_all_prompts.ndjson
```

**HTML** is a single self-contained page: a readable catalog with a search box
that filters prompts as you type, in light or dark mode, with no external
files or network requests. Every value is HTML-escaped, and the page also
embeds the full records as JSON, so it works with `--compare`,
`--update-file`, and `pb-diff` like the JSON and CSV formats.

```bash
pb @acb --mode all --format html    # -> exports/acb_all_prompts.html
```

### One file per prompt

`--layout files` writes one Markdown file per prompt instead of one file per
catalog, which suits note tools that index front matter (Obsidian, a Git-based
wiki, a Notion import). Each catalog gets its own folder, named like the catalog
file without the extension:

```bash
pb @acb --layout files --mode all --extra-fields tags
# -> exports/acb_all_prompts/3d-clay-ui-icons-2.md, exports/acb_all_prompts/...
```

```markdown
---
title: "3D Clay UI Icons"
slug: "3d-clay-ui-icons-2"
url: "https://promptbase.com/prompt/3d-clay-ui-icons-2"
price: 4.99
tags: ["3d", "icons", "cute"]
---

# 3D Clay UI Icons

Transform your digital interfaces with ...
```

- The front matter holds every field of the JSON format except the description,
  which is the body. Values are written as JSON scalars, which are valid YAML, so
  every string is quoted and escaped: a title such as `yes` or `2026-01-01` is
  never read back as a boolean or a date.
- **File names** come from the prompt's slug alone, made safe: a lowercase slug
  that is already plain (every real one) is its own file name. Any other slug
  keeps a safe form (anything but letters, digits, `.`, `_`, and `-` becomes `_`,
  a leading dot is dropped, a name Windows reserves such as `CON` or `NUL` gets a
  `_`) plus a short digest of the original, for example `a_b-3f2a1c8d.md`. Two
  slugs that sanitise alike, or differ only by case, therefore never share a
  name, a slug can never write outside its folder, and a prompt's file does not
  change when a later run selects or sorts other prompts.
- **Front matter** escapes the characters a YAML reader would alter or refuse:
  U+0085 and the U+007F to U+009F controls, U+2028, U+2029, and U+FEFF are written
  as `\uXXXX`, so the values read back exactly.
- **Nothing is deleted.** A run replaces the files it writes (each atomically) and
  leaves the rest, including files of your own and files for prompts that have
  since been removed.
- It needs Markdown (`--format` may be left out) and cannot be combined with
  `--output-file`, `--compare`, or `--update-file`, which work on a single
  catalog file. The web UI offers it as "Layout" (it needs the markdown format and
  has no download link for a folder). `--timestamp-filenames` adds the timestamp
  to the folder name.

### Extra fields

`--extra-fields` adds PromptBase values that the default export leaves out.
Pass a comma-separated list, or `all`. Only the fields you ask for are
downloaded, so a default run transfers exactly what it did before.

| Field | What is written |
| --- | --- |
| `tags` | PromptBase's tags for the prompt. They include generated pair tags such as `illustration_poster`. |
| `engine` | The `engine` value, for example `gpt-5.5`. |
| `nsfw` | PromptBase's NSFW flag. |
| `featured` | PromptBase's featured flag. |
| `updated` | PromptBase's `updated` time, as epoch milliseconds plus `updated_iso`. Only some prompts have one. |
| `last_sale` | The time of the last sale, as epoch milliseconds plus `last_sale_iso`. Only prompts that have sold have one. |
| `unique_sales` | The `uniqueSales` count. |

A time PromptBase does not record is `null` in JSON and an empty cell in CSV,
not a zero. In CSV the tags are joined with `, ` (or, if a tag itself contains a
comma, written as a JSON array so the cell can always be read back) and a flag
is `true` or `false`; `--csv-safe` protects these cells like the others. Markdown adds one metadata
line per field, and HTML shows them next to each prompt and embeds them in its
JSON. TXT holds only a title and a description, so `--extra-fields` with
`txt` is an error rather than a silent omission.

```bash
pb @acb --mode all --format json --extra-fields tags,engine,unique_sales
pb @acb --mode all --format csv --extra-fields all
```

Extra fields are written for you to read; `--compare` and `--update-file`
still compare the same fields as before.

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
the current prompts, keeping its format. The format comes from the file's
extension; an explicit `--format` must agree with it, because the next run
reads the catalog back by that extension:

```bash
pb @acb --mode all --update-file exports/acb_all_prompts.json
```

Add `--fail-on-diff` to exit with code `2` when the catalog changed, which lets a
CI job flag catalog drift. A change means a prompt was added or removed, or its
title, description, type, domain, or price changed. Counters (views, sales,
downloads, favorites, rating, reviews) move on almost every run, so they are not
compared; `pb-history` tracks those. With `--update-file`, the file is still rewritten
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
options and uses the same exit codes, plus `--previous-csv-safe` and
`--current-csv-safe` for CSV inputs written with `--csv-safe`.

```bash
pb-diff old/acb_all_prompts.csv exports/acb_all_prompts.html
pb-diff old.json new.json --fail-on-diff --quiet --diff-output diff.json
```

## Converting a catalog offline

`pb-convert` (`promptbase-convert`, or `python -m promptbase_exporter.convert`
without installing) rewrites a catalog you already have in another format, with
no network access. Give it the format to write, or an output path whose
extension says so; without `--output-file` the new file is written next to the
source with the new extension.

```bash
pb-convert exports/acb_all_prompts.json --format csv      # -> exports/acb_all_prompts.csv
pb-convert exports/acb_all_prompts.json -o site/catalog.html
pb-convert exports/acb_all_prompts.csv --from-csv-safe --format json
```

The result is the file a direct export would have written: a converted catalog
matches an exported one byte for byte, including extra fields and bundle or
app catalogs.

- **Sources:** JSON, CSV, and HTML catalogs, which keep every field. A TXT or
  Markdown catalog is refused: it lacks columns such as downloads and rating,
  and filling them in would mean inventing values. Export again from
  PromptBase instead. Converting *to* TXT or Markdown is fine; TXT keeps only a
  title and a description, so it drops every other field.
- **Extra fields** in the source (see `--extra-fields`) are carried over when
  the target format can hold them. Once a source has an extra field, every record
  must have it; a missing or empty value stops the conversion.
- **No guessing:** a missing column, an empty number, a value of the wrong type
  (a boolean where a number belongs), an entry that is not a record, or an
  unreadable value stops the conversion with the record's position, and nothing
  is written.
- **Safety:** it never overwrites the source, and it refuses to replace another
  existing file unless you pass `--overwrite`; the write is atomic. The result is
  rendered and its record count checked in a scratch file first, so a file that
  would fail the check never replaces the one you have.
- `--csv-safe` protects the CSV being written; `--from-csv-safe` says the CSV
  being read was written with `--csv-safe`, so its escaped cells are restored.

## Tracking changes over time

`pb-history` (`promptbase-history`, or `python -m promptbase_exporter.history`
without installing) keeps snapshots of a profile's counters in a SQLite file and
reports what moved between two of them. It uses only the standard library's
`sqlite3`, and nothing is sent anywhere.

```bash
pb-history snapshot @acb --db history.sqlite          # run this on a schedule
pb-history snapshot @acb @dreamydesigns --db history.sqlite
pb-history list --db history.sqlite
pb-history report --db history.sqlite                 # the latest snapshot against the one before
pb-history report --db history.sqlite --days 7 --metric views --top 20
pb-history report --db history.sqlite --since 2026-09-01 --format html -o trends.html
pb-history report --db history.sqlite --alert views+50% --alert sales+1 --alert new
```

- **A snapshot** holds the views, sales, downloads, favorites, reviews, rating,
  price, and discount of every listing of one kind for one profile (`--item-type`
  picks prompts, bundles, or apps, which are kept apart), whatever filters a later
  export would use. A listing is identified by its slug. It takes about 20 ms and
  roughly 100 bytes per listing to store. `@acb`, `acb` and the profile URL are one
  profile and are recorded once per run. A profile whose listings have all gone is
  recorded as an empty snapshot, so the next report shows them as removed; one that
  never had any is an error, since that is more likely the wrong profile or kind.
- **A report** compares the latest snapshot with a baseline: the previous one by
  default, the first taken on or after `--since DATE` (UTC), or the latest taken at
  least `--days N` before the latest. It shows the totals before and after, the top
  movers by `--metric` (`views`, `sales`, `downloads`, `favorites`, or `reviews`;
  only listings that gained; `--top N`, at least 1, limits how many), new and
  removed listings, and price or discount changes. `--days` takes a finite number,
  0 or more. `--format` is `markdown` (the default), `json`, or `html`, a
  self-contained page with small trend charts of the totals across every snapshot;
  `-o` writes it to a file instead of the terminal.
- **Alerts:** `--alert RULE` (repeatable) lists every listing that meets a rule at
  the top of the report, and the command exits with `2` when any rule fires, so a
  scheduled job can act on it (the report is still written). A rule is a counter
  with a gain, absolute or in percent: `sales+1`, `views+50%`, `favorites+10`.
  Alternatively, it is an event: `new`, `removed`, or `price` (a price or discount
  change). A percentage needs a baseline above zero; a listing that had none before
  is caught by an absolute rule. In JSON, the rules and hits are under `alerts`.
- **One profile per report:** if the file holds several, name one with `--profile`.
- **On a schedule in GitHub Actions:** see
  [Track trends on a schedule](github-action.md#track-trends-on-a-schedule), which
  keeps the file in the Actions cache and puts each report on the run's summary page.
- **Safe to keep around:** every value is stored through parameterised queries and
  every value in a report is escaped (HTML) or backslash-escaped so that a title
  cannot become raw HTML or a link (Markdown). The file has a format version and
  its tables are checked against the expected columns, so a file that is not a
  `pb-history` file is refused and left untouched, and a newer format is refused rather than misread.

Exit codes: `0` on success, `1` for an error, such as a missing file, fewer than
two snapshots, or a profile that could not be fetched (the others are still
recorded), and `2` when an `--alert` rule fired.

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
| `profile` (positional) | required, unless `--config` lists `profiles` | PromptBase profile URL (with or without `https://`), path, username, or `@username`. Repeat to export several profiles. |
| `--config` | none | Read options from a `.json` or `.toml` file; the command line overrides it. See [Configuration file](#configuration-file). |
| `-m`, `--mode` | `split` | `split`, `all`, `text`, or `image`. Aliases: `text-only`, `image-only`. |
| `-o`, `--output-dir` | `exports` | Directory for generated files. |
| `--layout` | `catalog` | `catalog` writes one file per catalog; `files` writes one Markdown file per prompt into a folder per catalog. See [One file per prompt](#one-file-per-prompt). |
| `--item-type` | `prompt` | Kind of listing to export: `prompt`, `bundle`, or `app`. |
| `-f`, `--format` | `txt` | `txt`, `markdown`, `json`, `ndjson`, `csv`, or `html`. Inferred from the extension with `--output-file`/`--update-file`; with `--update-file` it must match that extension. |
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
| `--extra-fields` | none | Include extra fields, comma-separated or `all`: `tags`, `engine`, `nsfw`, `featured`, `updated`, `last_sale`, `unique_sales`. Not for `txt`. |
| `--csv-safe` | off | Prefix CSV text cells starting with `=`, `+`, `-`, or `@` with `'` so spreadsheets do not run them. Needs CSV output. |
| `--compare-csv-safe` | off | Read the CSV `--compare` / `--update-file` catalog as written with `--csv-safe`. Independent of `--csv-safe`. |
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

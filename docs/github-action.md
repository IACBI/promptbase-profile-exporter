# GitHub Action

This repository is also a composite GitHub Action. It runs the exporter in a
workflow, writes catalog files, and can upload them as a workflow artifact.
The action installs the exporter from its own checkout, so there is nothing
else to install or publish. Every input maps to a [command-line
option](cli.md#options).

## Basic workflow

Create `.github/workflows/promptbase-export.yml` in your own repository:

```yaml
name: Export PromptBase catalog

on:
  workflow_dispatch:
    inputs:
      profile_url:
        description: PromptBase profile URL, username, or @username
        required: true
        default: https://promptbase.com/profile/acb
  schedule:
    - cron: "0 5 * * 1"

permissions:
  contents: read

jobs:
  export:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - uses: IACBI/promptbase-profile-exporter@v0.8.0
        with:
          profile-url: ${{ github.event.inputs.profile_url || 'https://promptbase.com/profile/acb' }}
          mode: split
          format: markdown
          output-dir: exports
          sort: newest
          artifact-name: promptbase-catalog
```

The workflow runs manually from the Actions tab and every Monday at 05:00 UTC. The generated `exports/` directory is uploaded as the `promptbase-catalog` artifact.

The examples pin the action to a published release tag, which is the
recommended way to use it: a workflow keeps behaving the same until you choose
to upgrade. `@main` also works and always tracks the latest code, but it can
change under you. For the strongest guarantee, pin the full commit SHA of a
release, as GitHub recommends for third-party actions:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
```

## Commit exports back to the repository

Use this when you want the generated catalog to be versioned in your repository:

```yaml
name: Update PromptBase catalog

on:
  workflow_dispatch:
  schedule:
    - cron: "0 5 * * 1"

permissions:
  contents: write

jobs:
  export:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - uses: IACBI/promptbase-profile-exporter@v0.8.0
        with:
          profile-url: https://promptbase.com/profile/acb
          mode: split
          format: markdown
          output-dir: exports
          upload-artifact: false

      - name: Commit updated exports
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add exports/
          git diff --cached --quiet || git commit -m "Update PromptBase catalog"
          git push
```

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `profile-url` | Required | PromptBase profile URL, path, username, or `@username`. Separate several profiles with commas, spaces, or new lines. |
| `mode` | `split` | `split`, `all`, `text`, `image`, `text-only`, or `image-only`. |
| `format` | Empty | `txt`, `markdown`, `json`, `csv`, or `html`. Empty means `txt`, or the format inferred from `output-file` / `update-file`. |
| `output-dir` | `exports` | Directory where files are written. |
| `sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, or `rating`. |
| `domain` | Empty | Optional comma-separated domain filter such as `text,image`. |
| `type` | Empty | Optional comma-separated PromptBase type filter such as `gpt,claude`. |
| `price-mode` | Empty | Leave empty for all prompts, or use `free` / `paid`. |
| `min-price` | Empty | Optional minimum price. |
| `max-price` | Empty | Optional maximum price. |
| `min-sales` | Empty | Optional minimum number of sales. |
| `min-rating` | Empty | Optional minimum rating. |
| `limit` | Empty | Optional maximum number of prompts after filtering and sorting. |
| `since` | Empty | Optional inclusive created-date filter such as `2026-01-01`. |
| `until` | Empty | Optional inclusive created-date filter such as `2026-12-31`. |
| `output-file` | Empty | Write a single catalog to this exact path. Requires `mode` other than `split`. |
| `overwrite` | `false` | Allow `output-file` to replace an existing file. |
| `update-file` | Empty | Compare against this catalog and rewrite it in place. Requires `mode` other than `split`. |
| `compare` | Empty | Existing catalog path to compare against. Requires `mode` other than `split`. |
| `diff-output` | Empty | Optional path for the comparison report: JSON for a `.json` path, Markdown otherwise. |
| `fail-on-diff` | `false` | Exit with code 2 when `compare` or `update-file` finds changes. |
| `csv-safe` | `false` | Protect CSV text cells from spreadsheet formula injection. Requires CSV output. |
| `dry-run` | `false` | Fetch, filter, and validate without writing files. Skips the artifact upload. |
| `timestamp-filenames` | `false` | Add a timestamp to generated filenames. |
| `allow-missing-descriptions` | `false` | Write partial exports when descriptions are missing. |
| `python-version` | `3.12` | Python version used by `actions/setup-python`. |
| `working-directory` | `.` | Directory where the export command runs. |
| `upload-artifact` | `true` | Upload the output directory (or the `output-file` / `update-file` catalog) with `actions/upload-artifact`. |
| `artifact-name` | `promptbase-exports` | Name of the uploaded artifact. |

## Outputs

| Output | Description |
| --- | --- |
| `output-dir` | Directory containing the generated export files (echoes the `output-dir` input). |
| `artifact-name` | Name of the uploaded artifact (echoes the `artifact-name` input). |
| `exit-code` | Exit code of the exporter: `0` success, `1` error, `2` changes found with `fail-on-diff`. |
| `has-changes` | With `compare` or `update-file`: `true` when the catalog changed, otherwise `false`. |
| `added`, `removed`, `changed`, `unchanged` | With `compare` or `update-file`: the number of prompts in each group. |
| `diff-json` | With `compare` or `update-file`: path of the machine-readable JSON diff report. |

The comparison outputs are set even when `fail-on-diff` fails the step, so a
later step with `if: always()` can still read them.

Reference them from later steps via `steps.<step-id>.outputs.output-dir`:

```yaml
- id: export
  uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: https://promptbase.com/profile/acb
- run: ls -R "${{ steps.export.outputs.output-dir }}"
```

## Examples

Export only text prompts to CSV:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: "@acb"
    mode: text
    format: csv
```

Export only paid image prompts, sorted by views:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: image
    format: json
    price-mode: paid
    sort: views
```

Create timestamped backups:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: split
    format: markdown
    timestamp-filenames: true
```

Fail a workflow when the catalog changed:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: all
    format: json
    compare: exports/acb_all_prompts.json
    diff-output: exports/catalog-diff.md
    fail-on-diff: true
```

Keep a catalog in the repository up to date, and act on what changed:

```yaml
- uses: actions/checkout@v7
- id: catalog
  uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: all
    update-file: catalog/acb.json
    upload-artifact: false
- if: steps.catalog.outputs.has-changes == 'true'
  run: |
    echo "Added ${{ steps.catalog.outputs.added }}, removed ${{ steps.catalog.outputs.removed }}, changed ${{ steps.catalog.outputs.changed }}"
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git commit -am "Update PromptBase catalog" && git push
```

Export several profiles as searchable HTML catalogs:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
  with:
    profile-url: |
      @acb
      @dreamydesigns
    mode: all
    format: html
```

Use the local checkout of this repository while developing the action:

```yaml
- uses: actions/checkout@v7
- uses: ./
  with:
    profile-url: https://promptbase.com/profile/acb
```

## Notes

- The exporter uses public PromptBase data and does not need secrets.
- Scheduled workflows use UTC cron times.
- PromptBase can change its public data model. Keep workflows pinned to a release tag and update intentionally.
- If you commit generated files back to a repository, keep `permissions.contents: write` scoped only to that workflow.

# GitHub Action Usage

This repository includes a composite GitHub Action that runs the exporter in a workflow, writes catalog files, and can upload the generated files as a workflow artifact.

The action installs `promptbase-profile-exporter` from the action checkout itself, so users do not need to publish or install the package separately.

## Basic Workflow

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
      - uses: actions/checkout@v4

      - uses: IACBI/promptbase-profile-exporter@main
        with:
          profile-url: ${{ github.event.inputs.profile_url || 'https://promptbase.com/profile/acb' }}
          mode: split
          format: markdown
          output-dir: exports
          sort: newest
          artifact-name: promptbase-catalog
```

The workflow runs manually from the Actions tab and every Monday at 05:00 UTC. The generated `exports/` directory is uploaded as the `promptbase-catalog` artifact.

For stable production workflows, pin the action to a published release tag
instead of `main`:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.8.0
```

## Commit Exports Back To The Repository

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
      - uses: actions/checkout@v4

      - uses: IACBI/promptbase-profile-exporter@main
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
| `profile-url` | Required | PromptBase profile URL, path, username, or `@username`. |
| `mode` | `split` | `split`, `all`, `text`, `image`, `text-only`, or `image-only`. |
| `format` | `txt` | `txt`, `markdown`, `json`, or `csv`. |
| `output-dir` | `exports` | Directory where files are written. |
| `sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, or `rating`. |
| `domain` | Empty | Optional comma-separated domain filter such as `text,image`. |
| `type` | Empty | Optional comma-separated PromptBase type filter such as `gpt,claude`. |
| `price-mode` | Empty | Leave empty for all prompts, or use `free` / `paid`. |
| `min-price` | Empty | Optional minimum price. |
| `max-price` | Empty | Optional maximum price. |
| `limit` | Empty | Optional maximum number of prompts after filtering and sorting. |
| `since` | Empty | Optional inclusive created-date filter such as `2026-01-01`. |
| `until` | Empty | Optional inclusive created-date filter such as `2026-12-31`. |
| `compare` | Empty | Existing catalog path to compare against. Requires `mode` other than `split`. |
| `diff-output` | Empty | Optional path where the comparison report should be written. |
| `fail-on-diff` | `false` | Exit with code 2 when `compare` finds changes. |
| `timestamp-filenames` | `false` | Add a timestamp to generated filenames. |
| `allow-missing-descriptions` | `false` | Write partial exports when descriptions are missing. |
| `python-version` | `3.12` | Python version used by `actions/setup-python`. |
| `working-directory` | `.` | Directory where the export command runs. |
| `upload-artifact` | `true` | Upload the output directory with `actions/upload-artifact`. |
| `artifact-name` | `promptbase-exports` | Name of the uploaded artifact. |

## Outputs

| Output | Description |
| --- | --- |
| `output-dir` | Directory containing the generated export files (echoes the `output-dir` input). |
| `artifact-name` | Name of the uploaded artifact (echoes the `artifact-name` input). |

Reference them from later steps via `steps.<step-id>.outputs.output-dir`:

```yaml
- id: export
  uses: IACBI/promptbase-profile-exporter@main
  with:
    profile-url: https://promptbase.com/profile/acb
- run: ls -R "${{ steps.export.outputs.output-dir }}"
```

## Examples

Export only text prompts to CSV:

```yaml
- uses: IACBI/promptbase-profile-exporter@main
  with:
    profile-url: "@acb"
    mode: text
    format: csv
```

Export only paid image prompts, sorted by views:

```yaml
- uses: IACBI/promptbase-profile-exporter@main
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: image
    format: json
    price-mode: paid
    sort: views
```

Create timestamped backups:

```yaml
- uses: IACBI/promptbase-profile-exporter@main
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: split
    format: markdown
    timestamp-filenames: true
```

Fail a workflow when the catalog changed:

```yaml
- uses: IACBI/promptbase-profile-exporter@main
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: all
    format: json
    compare: exports/acb_all_prompts.json
    diff-output: exports/catalog-diff.md
    fail-on-diff: true
```

Use the local checkout of this repository while developing the action:

```yaml
- uses: actions/checkout@v4
- uses: ./
  with:
    profile-url: https://promptbase.com/profile/acb
```

## Notes

- The exporter uses public PromptBase data and does not need secrets.
- Scheduled workflows use UTC cron times.
- PromptBase can change its public data model. Keep workflows pinned to a release tag and update intentionally.
- If you commit generated files back to a repository, keep `permissions.contents: write` scoped only to that workflow.

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

      - uses: IACBI/promptbase-profile-exporter@v0.14.1
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
change under you. A tag can be moved, though; for the strongest guarantee, pin
the full commit SHA of a release, as GitHub recommends for third-party actions,
with the version in a comment so Dependabot can still update it:

```yaml
- uses: IACBI/promptbase-profile-exporter@<full-commit-sha> # v0.14.1
```

The release's commit SHA is what the tag points to:

```bash
git ls-remote https://github.com/IACBI/promptbase-profile-exporter "refs/tags/v0.14.1^{}"
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

      - uses: IACBI/promptbase-profile-exporter@v0.14.1
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

## Open a pull request when the catalog changes

Use this to review catalog changes instead of committing them straight to your
default branch. The first run needs a catalog to compare against; create it once
with `output-file` (for example `output-file: catalog/acb_all_prompts.json`).

```yaml
name: Refresh PromptBase catalog

on:
  workflow_dispatch:
  schedule:
    - cron: "0 5 * * 1"

permissions:
  contents: write
  pull-requests: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - id: update
        uses: IACBI/promptbase-profile-exporter@v0.14.1
        with:
          profile-url: https://promptbase.com/profile/acb
          mode: all
          update-file: catalog/acb_all_prompts.json
          upload-artifact: false

      - name: Open a pull request with the changes
        if: steps.update.outputs.has-changes == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
          ADDED: ${{ steps.update.outputs.added }}
          REMOVED: ${{ steps.update.outputs.removed }}
          CHANGED: ${{ steps.update.outputs.changed }}
        run: |
          set -euo pipefail
          # The attempt number keeps a re-run from colliding with a branch that an
          # earlier attempt already pushed.
          branch="catalog-refresh-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git switch -c "$branch"
          git add catalog/
          git commit -m "Refresh PromptBase catalog"
          git push -u origin "$branch"
          gh pr create --base "$GITHUB_REF_NAME" --head "$branch" \
            --title "Refresh PromptBase catalog" \
            --body "Added $ADDED, removed $REMOVED, changed $CHANGED. The run's summary page has the full diff."
```

`update-file` rewrites the catalog in the workflow's checkout, so the job
commits that change on a new branch and opens the pull request. Two things to
know: the repository setting "Allow GitHub Actions to create and approve pull
requests" (Settings, Actions, General) must be on, and a pull request opened
with the default `GITHUB_TOKEN` does not start other workflows, so use a
personal access token or a GitHub App token if your checks must run on it.

## Track trends on a schedule

`pb-history` (see [Tracking changes over time](cli.md#tracking-changes-over-time))
needs its SQLite file to survive between runs. A workflow can keep it in the
Actions cache and put the day's report on the run's summary page:

```yaml
name: promptbase-trends

on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  snapshot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: python -m pip install "git+https://github.com/IACBI/promptbase-profile-exporter@v0.14.1"
      - uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: history.sqlite
          key: promptbase-history-${{ github.run_id }}-${{ github.run_attempt }}
          restore-keys: promptbase-history-
      - run: pb-history snapshot @acb --db history.sqlite
      - uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: history.sqlite
          key: promptbase-history-${{ github.run_id }}-${{ github.run_attempt }}
      - name: Report what moved since the previous run
        run: |
          set -euo pipefail
          if [[ "$(pb-history list --db history.sqlite | wc -l)" -ge 2 ]]; then
            pb-history report --db history.sqlite --metric views >> "$GITHUB_STEP_SUMMARY"
          else
            echo "The first snapshot is stored; the report starts with the next run." >> "$GITHUB_STEP_SUMMARY"
          fi
```

How it works and what to know:

- A cache entry cannot be overwritten, so each run, and each re-run of it, saves
  under a new key, and the next run restores the newest one through `restore-keys`. The snapshot is saved
  before the report, so a failed report does not lose it.
- The Markdown report escapes every title and value taken from PromptBase, so it
  is safe to append to the summary page.
- GitHub removes a cache entry nobody has read for 7 days, and the oldest entries
  once a repository's caches pass 10 GB. A daily run keeps the newest entry alive,
  but a long pause loses the history. An artifact is no lasting backup either: it
  is deleted after the repository's retention period (90 days at most for a
  public repository). To keep the history for good, commit `history.sqlite` to a
  branch of its own after each snapshot. That suits a small profile: a snapshot
  takes about 100 bytes per listing, about 9 MB a year of daily snapshots for 250
  listings.
- `--days 7` instead of the default compares with a snapshot at least a week old,
  once the history reaches back that far.
- To be told when something happens, add rules to the report command, for example
  `--alert sales+1 --alert new`. The step then fails with exit code `2` when a rule
  fires, after the report is written, and GitHub notifies you of the failed run;
  the summary page shows which listings fired which rule.

## Publish a dashboard with GitHub Pages

The same schedule can publish what it finds as a small website: a trends page, the
searchable catalog, and an index linking the two. Every page is a single
self-contained file that loads nothing from other sites, so GitHub Pages serves it
as is.

```yaml
name: promptbase-dashboard

on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - run: python -m pip install "git+https://github.com/IACBI/promptbase-profile-exporter@v0.14.1"
      - uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: history.sqlite
          key: promptbase-dashboard-${{ github.run_id }}-${{ github.run_attempt }}
          restore-keys: promptbase-dashboard-
      - run: pb-history snapshot @acb --db history.sqlite
      - uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: history.sqlite
          key: promptbase-dashboard-${{ github.run_id }}-${{ github.run_attempt }}
      - name: Build the site
        run: |
          set -euo pipefail
          mkdir site
          pb @acb --mode all --format html --output-file site/catalog.html --quiet
          if [[ "$(pb-history list --db history.sqlite | wc -l)" -ge 2 ]]; then
            pb-history report --db history.sqlite --format html -o site/trends.html
            trends='<li><a href="trends.html">What moved since the previous snapshot</a></li>'
          else
            trends='<li>Trends start with the second run.</li>'
          fi
          cat > site/index.html <<HTML
          <!doctype html>
          <html lang="en"><head><meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>PromptBase dashboard</title>
          <style>:root{color-scheme:light dark}body{font:16px/1.5 system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem}</style>
          </head><body><h1>PromptBase dashboard</h1><ul>
          $trends
          <li><a href="catalog.html">The full catalog, searchable</a></li>
          </ul></body></html>
          HTML
      - uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0
        with:
          path: site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      pages: write  # publish the site
      id-token: write  # prove the deployment comes from this workflow
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5.0.1
```

Before the first run, set the repository's Pages source to **GitHub Actions**
(Settings, Pages). Then:

- The site is public. That is no exposure here, because every value comes from
  PromptBase's public pages, but publish only profiles you are happy to show.
- The build job holds only read permission. Pages write and the OIDC token stay in
  the deploy job, which runs only GitHub's deploy action.
- The history lives in the Actions cache, with the same limits as in the previous
  section. Its keys start with `promptbase-dashboard-`, not the trends example's
  `promptbase-history-`: the cache is shared by every workflow in a repository, so
  with one prefix for both, each could restore the other's newest snapshot and
  compare snapshots taken minutes apart. Running both, each keeps its own
  history; to keep a single one, use only this workflow.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `profile-url` | Required | PromptBase profile URL, path, username, or `@username`. Separate several profiles with commas, spaces, or new lines. |
| `item-type` | `prompt` | Kind of listing to export: `prompt`, `bundle`, or `app`. |
| `mode` | `split` | `split`, `all`, `text`, `image`, `text-only`, or `image-only`. |
| `format` | Empty | `txt`, `markdown`, `json`, `ndjson`, `csv`, or `html`. Empty means `txt`, or the format inferred from `output-file` / `update-file`. With `update-file`, it must match that file's extension. |
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
| `step-summary` | `true` | With `compare` or `update-file`, show the diff report on the workflow run's summary page. Use `false` to turn it off. |
| `fail-on-diff` | `false` | Exit with code 2 when `compare` or `update-file` finds changes. |
| `layout` | Empty | `files` writes one Markdown file per prompt, with YAML front matter, into a folder per catalog. Empty means one file per catalog. |
| `extra-fields` | Empty | Extra fields to include, comma-separated or `all`: `tags`, `engine`, `nsfw`, `featured`, `updated`, `last_sale`, `unique_sales`. Not for `txt`. |
| `csv-safe` | `false` | Protect CSV text cells from spreadsheet formula injection. Requires CSV output. |
| `compare-csv-safe` | `false` | The CSV `compare` / `update-file` catalog was written with `csv-safe`; restore its escaped cells when comparing. |
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

With `compare` or `update-file`, the action also writes the diff report (the
Markdown form of `diff-json`) to the workflow run's summary page, so you can see
what changed without opening a log. A report over 900 KB is cut, with a note: the full
report is always in the step's log, and the `diff-output` input keeps it as a file
(`diff-json` points to a temporary file that is gone when the job ends). Set `step-summary: false` to turn it off.

Reference them from later steps via `steps.<step-id>.outputs.output-dir`:

```yaml
- id: export
  uses: IACBI/promptbase-profile-exporter@v0.14.1
  with:
    profile-url: https://promptbase.com/profile/acb
- run: ls -R "${{ steps.export.outputs.output-dir }}"
```

## Examples

Export only text prompts to CSV:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.14.1
  with:
    profile-url: "@acb"
    mode: text
    format: csv
```

Export only paid image prompts, sorted by views:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.14.1
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: image
    format: json
    price-mode: paid
    sort: views
```

Create timestamped backups:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.14.1
  with:
    profile-url: https://promptbase.com/profile/acb
    mode: split
    format: markdown
    timestamp-filenames: true
```

Fail a workflow when the catalog changed:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.14.1
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
  uses: IACBI/promptbase-profile-exporter@v0.14.1
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
- uses: IACBI/promptbase-profile-exporter@v0.14.1
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

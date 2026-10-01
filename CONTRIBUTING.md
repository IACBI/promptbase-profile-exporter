# Contributing

Thanks for helping improve PromptBase Profile Exporter. This guide covers the
development setup, the checks every change must pass, and how changes reach
`main`.

## Development setup

```bash
git clone https://github.com/IACBI/promptbase-profile-exporter.git
cd promptbase-profile-exporter
python -m pip install -e ".[dev]"
```

The `dev` extra installs the tooling only (ruff, mypy, coverage, build,
twine), at their newest versions. CI installs exact versions instead, every file
checked against a hash: `requirements-dev.txt` (ruff, mypy, coverage) and
`requirements-release.txt` (build, twine), so a new release of a tool, or a
tampered download, cannot change a run. Dependabot proposes updates weekly. To
change a version yourself, edit its line (one without "# via") and run
`python scripts/lock_requirements.py`, which resolves the dependencies for every
Python version CI uses and rewrites the hashes. To match CI exactly, run
`python -m pip install --require-hashes -r requirements-dev.txt` and then
`python -m pip install --no-deps -e .`. The exporter itself must keep running on
the Python standard library alone.

## Checks

CI runs these on Python 3.10 through 3.14 (Ubuntu), plus the test suite on
Windows and macOS with the oldest and newest Python, and `main` only accepts a
pull request once all of them pass. CodeQL scans the Python code and the workflows on every pull request, and the
OpenSSF Scorecard grades the repository's supply-chain practices each week. A
nightly `canary` workflow repeats
the live export and opens an issue if PromptBase data stops working. Run the
checks locally before you push:

```bash
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m coverage report        # must stay at or above 85%
python -m build
python -m twine check dist/*
```

Code that reads catalogs, config files, or remote text is also fuzzed:
`fuzz/fuzz_parsers.py` runs under [Atheris](https://github.com/google/atheris) in
the `fuzz` workflow (Linux; a minute on pull requests that change the code,
five minutes weekly). Without Atheris, `python fuzz/fuzz_parsers.py --smoke 2000`
runs the same checks on seeded random inputs, and the test suite runs a short
smoke pass. When you add a parser, add a target and a seed for it there.

When you change how data is fetched from PromptBase, also run a real export
against a public profile:

```bash
python -m promptbase_exporter @acb --dry-run
python -m promptbase_exporter @acb --mode all --limit 5 --dry-run
```

## Workflow

`main` is protected. Create a branch, open a pull request, and merge once CI
is green:

```bash
git checkout -b fix/short-description
# ...make and test your change...
git push -u origin fix/short-description
```

## Pull request checklist

- **No runtime dependencies.** The tool runs on the standard library only;
  `requirements.txt` stays empty on purpose.
- **Tests.** Behavior changes come with `unittest` tests (not pytest) in the
  style of the existing suite.
- **Every surface in sync.** A new or changed option belongs in the CLI
  (`cli.py`), the web form (`web.py`), and the GitHub Action (`action.yml`).
- **Docs in the same change.** Add a `## Unreleased` entry to `CHANGELOG.md`,
  and update [docs/cli.md](docs/cli.md) and
  [docs/github-action.md](docs/github-action.md) when user-facing options
  change. The README carries English and Türkçe sections; update both
  together.
- **Web UI security.** Changes to `web.py` must keep the protections described
  in [docs/web-ui.md](docs/web-ui.md#security-model).
- **Pinned actions.** Reference GitHub Actions by full commit SHA with a
  trailing `# vX.Y.Z` comment; Dependabot keeps them current.
- Do not commit generated `exports/` files.

## Project layout

```text
promptbase_exporter/
  cli.py          # argument parsing, option validation, exit codes
  client.py       # public PromptBase/Firestore access, retries, schema checks
  config.py       # `--config`: read options from a JSON or TOML file
  layout.py       # `--layout files`: one Markdown file per prompt
  history.py      # `pb-history`: counter snapshots in SQLite and trend reports
  console.py      # makes stdout/stderr safe for text a legacy code page cannot encode
  convert.py      # `pb-convert`: rewrite a saved catalog in another format
  dates.py        # shared date parsing
  diff.py         # `python -m promptbase_exporter.diff` entry point (pb-diff)
  diffing.py      # catalog loading, comparison, and Markdown/JSON diff reports
  formatting.py   # filtering, sorting, format writers, atomic writes, validation
  models.py       # Profile and PromptRecord
  pipeline.py     # record selection (filter, sort, limit) shared by the CLI and web UI
  web.py          # local web UI
  __main__.py     # `python -m promptbase_exporter` entry point
tests/            # unittest suite
fuzz/             # Atheris fuzz harness (fuzz_parsers.py)
scripts/          # lock_requirements.py (hashed requirements-*.txt), check_release.py (release rules)
site/             # GitHub Pages landing page (published by pages.yml)
docs/             # CLI, web UI, and GitHub Action guides
action.yml        # composite GitHub Action
```

## Releases

Maintainers cut releases following [RELEASE.md](RELEASE.md).

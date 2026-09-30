# CLAUDE.md

Guidance for Claude Code when working in this repository. Keep it accurate; if
you change behavior that contradicts a line here, update this file in the same
change.

## What this is

A dependency-free Python tool that exports a public PromptBase profile's prompts
into TXT / Markdown / JSON / CSV catalogs. It ships three surfaces over one core:
a CLI, a local web UI, and a composite GitHub Action.

## Hard constraints (do not break)

- **Standard library only at runtime.** No third-party runtime dependencies,
  ever. `requirements.txt` is intentionally empty. Dev tools (ruff, mypy,
  coverage, build, twine) live in the `dev` optional-dependencies group only.
- **Python 3.10+.** Match existing style: `from __future__ import annotations`,
  PEP 604 unions, full type hints. The package ships `py.typed`; keep `mypy`
  clean.
- **Public data only.** The tool reads the same public Firestore data the
  website serves — no auth, no cookies, no scraping of private endpoints.
- **The web UI is a local, single-user tool.** Preserve its security model
  (see below) when touching `web.py`.

## Architecture (one line each)

`promptbase_exporter/`
- `cli.py` — argparse CLI, option validation (`normalize_options`, which returns the typed
  `RunOptions`), and the `main()` orchestration + exit codes (0 ok / 1 error /
  2 `--fail-on-diff`).
- `client.py` — public PromptBase/Firestore access: profile resolution,
  paginated queries, retry/backoff, and schema-drift detection.
- `dates.py` — shared `parse_datetime_ms` (used by both `cli.py` and `web.py`).
- `config.py` — `--config`: turns a JSON/TOML file into argv (validated by argparse itself,
  put in front of the real arguments so the command line wins); errors name the file.
- `layout.py` — `--layout files`: one Markdown file per prompt with JSON-scalar (valid YAML)
  front matter; `safe_stem` turns a remote slug into a safe file name. Never deletes files.
- `console.py` — `make_output_safe()`, called first thing by every entry point, so a
  title a legacy Windows code page cannot encode prints as `?` instead of crashing.
- `models.py` — `Profile` and `PromptRecord` (frozen dataclasses + derived
  properties like `url`, `created_iso`, `is_text`/`is_image`/`is_free`).
- `formatting.py` — filtering, sorting, the per-format writers (txt,
  markdown, json, csv with optional `--csv-safe`, html), atomic writes, and
  `count_written_records` (post-write validation).
- `pipeline.py` — `Selection` (filter → sort → limit), `split_modes`, and
  `without_description`, shared by `cli.py` and `web.py`. Add a new filter here once;
  `SurfacesAgreeTests` fails if the CLI and the web form drift apart.
- `diffing.py` — catalog loading and comparison (`--compare`/`--update-file`,
  `pb-diff`) and the Markdown/JSON diff reports.
- `diff.py` — `python -m promptbase_exporter.diff` entry point for `pb-diff`.
- `convert.py` — `pb-convert`: rewrite a saved JSON/CSV/HTML catalog in another
  format offline. It must match a direct export byte for byte and never invent
  missing values (TXT/Markdown sources are refused).
- `web.py` — stdlib `http.server` UI; mirrors CLI options as a form.
- `__main__.py` — `python -m promptbase_exporter` entry point.

`action.yml` is the composite GitHub Action; it must stay in sync with the CLI.

## Commands

Install dev tools once: `python -m pip install -e ".[dev]"`

The local equivalent of CI (run before committing):

```
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m coverage report      # must stay >= 70%
```

Run the tool: `python -m promptbase_exporter @acb --dry-run`
Run the web UI: `python -m promptbase_exporter.web`
Compare two catalogs offline: `python -m promptbase_exporter.diff OLD NEW`

A `PostToolUse` hook lints edited `.py` files with ruff automatically; it skips
silently if ruff is not installed.

## Conventions

- **Branch → PR → main.** `main` is protected and requires green CI. Never push
  to `main` directly; work on a feature branch and open a PR.
- **Tests are required** for behavior changes (`unittest`, no pytest). Mirror
  the existing test style; keep coverage >= 70%.
- **Update docs in the same change:** add a `## Unreleased` entry to
  `CHANGELOG.md`, and update `docs/cli.md` (options table) /
  `docs/github-action.md` when user-facing flags change. `docs/web-ui.md`
  documents the web security model below; keep it in step with `web.py`.
- **README is bilingual** (English, then Türkçe, per the section layout already
  in place). Keep both sections in sync; long material lives in `docs/`,
  English only.
- **Pin GitHub Actions to commit SHAs** with a trailing `# vX.Y.Z` comment.
  Dependabot updates these weekly.
- **No PyPI.** Distribution is via clone or the pinned Action tag. See
  `RELEASE.md`. There is no publish workflow.

## Adding things (cross-cutting checklists)

These touch several files; the `.claude/skills/` skills encode the full steps:
- New CLI flag → `add-cli-option` skill (cli.py + web.py + action.yml + tests +
  README + CHANGELOG).
- New output format → `add-export-format` skill (formatting.py + diffing.py +
  validation + tests + docs).
- Cutting a release → `release` skill (versions + CHANGELOG + tag + GitHub
  Release).

## Web UI security model (preserve when editing `web.py`)

- Binds to `127.0.0.1` by default; warns on non-loopback binds.
- `POST /export` rejects cross-origin (CSRF) and rebound-DNS requests via the
  `Host` header and `Origin`/`Sec-Fetch-Site`.
- Output directories are confined to the server's working directory (no
  absolute paths, no `..`). Route user-supplied paths through
  `_confine_to_cwd`, which checks containment lexically *before* `resolve()`:
  on Windows, resolving a UNC path (`//host/share`) contacts that host over SMB.
- The optional comparison catalog is confined the same way
  (`_resolve_compare_path`), must have a catalog extension, and is only read.
- `GET /download` only serves files inside that directory whose names match the
  exporter's own pattern (`_EXPORT_FILENAME_RE`) — it must not become an
  arbitrary file read. Downloads are attachments with
  `Content-Security-Policy: sandbox`, because HTML exports are served too.
- The compare/write/validate phase of an export runs under `_EXPORT_LOCK` so
  concurrent requests cannot interleave writes to the same files.
- Every response carries the security headers in `_send`. New endpoints must
  keep equivalent protections.

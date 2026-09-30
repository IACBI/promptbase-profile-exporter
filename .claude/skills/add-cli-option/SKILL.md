---
name: add-cli-option
description: Add a new option/flag to the promptbase-profile-exporter CLI end to end, keeping the web UI, GitHub Action, tests, and docs in sync. Use when adding or changing a command-line flag, filter, or export option.
---

# Add a CLI option end to end

A flag is not "done" until every surface and doc reflects it. Work through this
checklist; skipping a step leaves the CLI, web UI, Action, and docs out of sync.

## 1. Define and validate the argument — `promptbase_exporter/cli.py`

- Add the argument in `build_parser()`. Match the existing help-text style and
  grouping (e.g. mutually exclusive groups like `--free-only`/`--paid-only`).
- Put any validation (ranges, conflicts, dependencies) in `normalize_options()`,
  raising `ValueError` with a clear message — `main()` turns that into
  `error: ...` and exit code 1. Do not validate inside `main()`.
- Thread the value into the right stage:
  - record selection → `filter_records_by_metadata` (in `formatting.py`)
  - ordering → `sort_records`
  - output shaping → `filter_records` / the writers
- If the option is informational and should short-circuit (like `--list-domains`),
  follow that pattern and return before writing.

## 2. Mirror it in the web UI — `promptbase_exporter/web.py`

- Add a field to `ExportRequest` and parse/validate it in
  `build_request_config` (raise `WebInputError` for bad input → HTTP 400).
- Use it in `run_export`.
- Add the matching form control in `render_form` and escape any echoed value
  with `_h(...)`.

## 3. Mirror it in the Action — `action.yml`

- Add an `inputs:` entry (with `description`, `required: false`, `default`).
- Plumb it through the "Export PromptBase profile" step's `env:` and `args`
  array. For booleans, follow the existing `normalize_bool` + `case` pattern;
  for values, follow the `if [[ -n "$VAR" ]]; then args+=(...)` pattern.

## 4. Tests (required)

- `tests/test_cli.py` — parsing, validation errors, and effect on selection.
- `tests/test_web.py` — `build_request_config` accepts/normalizes it and
  rejects bad input.
- Keep coverage >= 85%.

## 5. Docs (same change)

- `docs/cli.md` — add a row to the Options table; add a usage example if it is
  notable. Update the relevant section prose (filtering, comparing, ...).
- `README.md` — only if the option belongs in the short Configuration table or
  Usage examples; if so, update the English and Türkçe sections together.
- `docs/github-action.md` — add a row to the Inputs table.
- `CHANGELOG.md` — add a bullet under `## Unreleased` (`### Added`/`### Changed`).

## 6. Verify

```
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
```
Optionally smoke-test: `python -m promptbase_exporter @acb --dry-run <new-flag>`.

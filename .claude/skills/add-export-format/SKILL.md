---
name: add-export-format
description: Add a new output/export format (like YAML or HTML) to promptbase-profile-exporter, wiring up the writer, post-write validation, diff loading, extension inference, and docs. Use when adding or changing a supported export format.
---

# Add an export format end to end

Formats are referenced in several places that must agree, or post-write
validation (`count_written_records`) will reject otherwise-valid exports. Use the
existing five formats (`txt`, `markdown`, `json`, `csv`, `html`) as the template.

## 1. Register the format — `promptbase_exporter/formatting.py`

- Add the name to `EXPORT_FORMATS`.
- Add its file extension to `FORMAT_EXTENSIONS` (e.g. `"yaml": "yaml"`).
- Write `format_records_as_<name>(records) -> str` and dispatch it from
  `format_records()`.
- Add a branch to `count_written_records()` that re-parses the written file and
  returns the record count. This must count exactly what the writer produced —
  `main()` compares it against the expected count and fails on mismatch. For
  text-like formats, mirror the careful regex approach used for `txt`/`markdown`.
- Add a branch to `infer_format_from_path()` so `--output-file foo.<ext>` infers
  the format.

## 2. Make it comparable — `promptbase_exporter/diffing.py`

- If the new format should be usable with `--compare`/`--update-file`, add a
  branch to `load_catalog()` that parses the file back into normalized record
  dicts (see `_parse_text_catalog` / `_parse_markdown_catalog` for structured
  text formats). JSON/CSV-like formats can reuse their stdlib parsers.

## 3. Surface it everywhere

- `web.py` reuses `EXPORT_FORMATS`, so the dropdown updates automatically — no
  change needed unless the format needs special handling.
- `action.yml` — mention the new value in the `format` input description.

## 4. Tests (required)

- `tests/test_formatting.py` — round-trip: write records, then
  `count_written_records` returns the right count; assert key fields appear.
- `tests/test_diffing.py` — if you added a `load_catalog` branch, cover it.
- Keep coverage >= 85%.

## 5. Docs (same change)

- `docs/cli.md` — Output formats section (with a sample) and the `-f/--format`
  row in the Options table.
- `README.md` — the tagline, Features, and Configuration lines that enumerate
  formats, in both the English and Türkçe sections.
- `docs/github-action.md` — the `format` input row.
- `CHANGELOG.md` — a bullet under `## Unreleased`.

## 6. Verify

```
python -m ruff check .
python -m mypy
python -m coverage run -m unittest discover -s tests
python -m promptbase_exporter @acb --mode all --format <name> --dry-run
```

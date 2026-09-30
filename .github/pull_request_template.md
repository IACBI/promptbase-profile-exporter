## Summary

-

## Validation

- [ ] `python -m ruff check .` and `python -m mypy` pass
- [ ] `python -m coverage run -m unittest discover -s tests` passes and coverage stays at or above 85%
- [ ] Tests added or updated for behavior changes
- [ ] Real export tested (`python -m promptbase_exporter @acb --dry-run`) if fetching behavior changed

## Docs

- [ ] `CHANGELOG.md` has an `## Unreleased` entry
- [ ] `docs/` and both README language sections updated if user-facing behavior changed

## Notes

Compatibility, schema, or PromptBase behavior notes.

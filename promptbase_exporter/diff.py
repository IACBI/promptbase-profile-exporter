"""``python -m promptbase_exporter.diff``: compare two catalog files offline."""

from .cli import diff_main

if __name__ == "__main__":
    raise SystemExit(diff_main())

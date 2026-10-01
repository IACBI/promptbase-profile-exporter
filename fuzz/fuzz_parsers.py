"""Coverage-guided fuzzing of the code that reads files and remote text.

Run with Atheris (Linux, Python 3.12+; see .github/workflows/fuzz.yml):

    python fuzz/fuzz_parsers.py -max_total_time=300

or, anywhere and without Atheris, a seeded random smoke run:

    python fuzz/fuzz_parsers.py --smoke 2000

The first byte of each input picks a target; the rest is its data. A target fails
by raising anything other than the errors it is documented to raise, or by
breaking one of the guarantees asserted below.
"""

from __future__ import annotations

import contextlib
import csv
import json
import random
import re
import sys
import tempfile
from pathlib import Path

try:
    import atheris
except ImportError:  # the smoke run and the unit test work without it
    atheris = None

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if atheris is not None:
    with atheris.instrument_imports():
        from promptbase_exporter import (
            client,
            config,
            console,
            convert,
            diffing,
            formatting,
            history,
            layout,
            models,
        )
else:
    from promptbase_exporter import (
        client,
        config,
        console,
        convert,
        diffing,
        formatting,
        history,
        layout,
        models,
    )

CATALOG_SUFFIXES = (".json", ".ndjson", ".csv", ".html", ".txt", ".md")
_WORKDIR = Path(tempfile.mkdtemp(prefix="pb-fuzz-"))
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SAFE_STEM = re.compile(r"[A-Za-z0-9._-]+")


class Data:
    """Takes typed values off the front of a fuzzer input."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.position = 0

    def byte(self) -> int:
        if self.position >= len(self.raw):
            return 0
        self.position += 1
        return self.raw[self.position - 1]

    def rest(self) -> bytes:
        rest = self.raw[self.position:]
        self.position = len(self.raw)
        return rest

    def text(self) -> str:
        return self.rest().decode("utf-8", errors="replace")


def _catalog(data: Data) -> None:
    suffix = CATALOG_SUFFIXES[data.byte() % len(CATALOG_SUFFIXES)]
    csv_safe = bool(data.byte() & 1)
    path = _WORKDIR / f"catalog{suffix}"
    path.write_bytes(data.rest())
    try:
        rows = diffing.load_catalog(path, csv_safe=csv_safe)
    except (ValueError, csv.Error):  # UnicodeDecodeError and JSONDecodeError included
        return
    # Whatever loads must compare equal to itself, with every record counted once.
    diff = diffing.compare_catalog_records(rows, rows)
    assert not diff.has_changes, (suffix, diff)
    assert diff.unchanged == len(rows), (suffix, diff.unchanged, len(rows))


def _record(data: Data) -> None:
    try:
        row = json.loads(data.text())
    except ValueError:
        return
    if not isinstance(row, dict):
        return
    try:
        record = convert.record_from_dict(row)
    except ValueError:
        return
    item_type = record.item_type if record.item_type in models.ITEM_TYPES else "prompt"
    for export_format in formatting.EXPORT_FORMATS:
        formatting.format_records([record], export_format, item_type=item_type)


def _text(data: Data) -> None:
    text = data.text()
    shown = console.printable(text)
    assert not _CONTROL.search(shown) and "\n" not in shown, repr(shown)
    marked = formatting.escape_markdown(text)
    for index, char in enumerate(marked):
        if char in "<>[]`*_|&":
            assert index and marked[index - 1] == "\\", (repr(marked), index)
    stem = layout.safe_stem(text)
    assert _SAFE_STEM.fullmatch(stem) and not stem.startswith("."), repr(stem)
    assert formatting.csv_unescape_formula(formatting.csv_escape_formula(text)) == text


def _inputs(data: Data) -> None:
    text = data.text()
    try:
        name = client.parse_profile_input(text)
        assert isinstance(name, str)
    except client.PromptBaseError:
        pass
    with contextlib.suppress(history.HistoryError):
        history.parse_alert(text)


def _diff(data: Data) -> None:
    try:
        lists = json.loads(data.text())
    except ValueError:
        return
    if not (isinstance(lists, list) and len(lists) == 2):
        return
    previous, current = (
        [row for row in side if isinstance(row, dict)] if isinstance(side, list) else []
        for side in lists
    )
    try:
        diff = diffing.compare_catalog_records(previous, current)
    except (TypeError, AttributeError):
        # Loaders hand over normalised rows; arbitrary JSON values are not one.
        return
    matched = len(diff.changed) + diff.unchanged
    assert len(diff.added) + matched == len(current)
    assert len(diff.removed) + matched == len(previous)


def _config(data: Data) -> None:
    suffix = ".toml" if data.byte() & 1 else ".json"
    path = _WORKDIR / f"options{suffix}"
    path.write_bytes(data.rest())
    with contextlib.suppress(config.ConfigError):
        config.load_config(path)


TARGETS = (_catalog, _record, _text, _inputs, _diff, _config)


def test_one_input(raw: bytes) -> None:
    data = Data(raw)
    TARGETS[data.byte() % len(TARGETS)](data)


def seeds() -> list[bytes]:
    """Valid inputs for every target, from the exporter's own writers."""
    record = models.PromptRecord(
        title="Logo maker", description="Line one\nLine two", slug="logo-maker",
        prompt_type="gpt", domain="text", created=1_700_000_000_000, price=2.5,
        tags=("logo", "brand"), item_type="prompt",
    )
    result = []
    for index, suffix in enumerate(CATALOG_SUFFIXES):
        export_format = {".md": "markdown", ".txt": "txt"}.get(suffix, suffix[1:])
        for csv_safe in (0, 1):
            text = formatting.format_records(
                [record], export_format, csv_safe=bool(csv_safe and suffix == ".csv"),
            )
            result.append(bytes([0, index, csv_safe]) + text.encode("utf-8"))
    row = json.dumps(formatting.record_to_dict(record))
    result += [
        bytes([1]) + row.encode(),
        bytes([2]) + b"=SUM(A1) <b>x</b> ~/a/../b CON.txt",
        bytes([2]) + b"'an apostrophe first, ''twice, '=both",
        bytes([3]) + b"https://promptbase.com/profile/acb",
        bytes([3]) + b"views+50%",
        bytes([4]) + json.dumps([[json.loads(row)], [json.loads(row)]]).encode(),
        # Findings, kept so they stay fixed: a CSV row longer than its header (found by
        # the first Atheris run, and by review), and rows with neither slug nor title.
        bytes([0, 2, 1]) + b"a\n1,2\n",
        bytes([0, 2, 0]) + b"x\n1\n",
        bytes([3]) + b"https://[x/profile/acb",  # urlparse raised ValueError
        bytes([0, 2, 0]) + b"title,slug,description\nA,dup,one\nA,dup,two\n",  # repeated slug
        bytes([5, 0]) + b'{"profiles": ["@acb"], "format": "json"}',
        bytes([5, 1]) + b'profiles = ["@acb"]\nformat = "csv"\n',
    ]
    return result


def _mutate(rng: random.Random, raw: bytearray) -> None:
    """One libFuzzer-style change: a byte, an insertion, or a copied or erased chunk."""
    choice = rng.random()
    if raw and choice < 0.35:
        raw[rng.randrange(len(raw))] = rng.randrange(256)
    elif choice < 0.55:
        raw.insert(rng.randint(0, len(raw)), rng.randrange(256))
    elif len(raw) > 2 and choice < 0.8:
        start = rng.randrange(1, len(raw))
        chunk = raw[start:start + rng.randint(1, 32)]
        at = rng.randint(1, len(raw))
        raw[at:at] = chunk
    elif len(raw) > 2:
        start = rng.randrange(1, len(raw))
        del raw[start:start + rng.randint(1, 16)]


def smoke(runs: int, seed: int = 20261001) -> None:
    """Every seed, then ``runs`` mutations of them, without Atheris."""
    rng = random.Random(seed)
    corpus = seeds()
    for raw in corpus:
        test_one_input(raw)
    for _ in range(runs):
        raw = bytearray(rng.choice(corpus))
        for _ in range(rng.randint(1, 8)):
            _mutate(rng, raw)
        test_one_input(bytes(raw))


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--smoke":
        smoke(int(sys.argv[2]))
        print(f"smoke run passed: {sys.argv[2]} inputs")
        return
    if atheris is None:
        sys.exit("atheris is not installed: use --smoke N, or see .github/workflows/fuzz.yml")
    corpus = _WORKDIR / "seeds"
    corpus.mkdir(exist_ok=True)
    for index, raw in enumerate(seeds()):
        (corpus / f"seed-{index:02d}").write_bytes(raw)
    atheris.Setup([*sys.argv, str(corpus)], test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()

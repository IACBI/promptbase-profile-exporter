"""Console output that cannot crash the tool."""

from __future__ import annotations

import contextlib
import re
import sys


def make_output_safe() -> None:
    """Make stdout and stderr replace characters their encoding cannot hold.

    Output redirected to a pipe or a log file on Windows uses the legacy code
    page (cp1252, cp1254, ...), which cannot encode an emoji or a character
    from another script. A prompt title containing one would otherwise end the
    run with a UnicodeEncodeError in the middle of an error message. A ``?``
    in a log line is better than a traceback. Files are unaffected: catalogs
    are always written as UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a StringIO, or a stream that is not a text file
            continue
        with contextlib.suppress(OSError, ValueError):
            reconfigure(errors="replace")


# C0 and C1 controls, and the bidirectional controls that can make a line display
# in another order than it reads ("Trojan Source").
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")


def printable(text: object) -> str:
    """Remote text made safe to print on one line of a terminal or a CI log.

    Whitespace, newlines included, collapses to single spaces, so a listing title
    cannot fake extra lines or a workflow command (a GitHub Actions log line that
    starts with ``::``); the remaining control characters, such as the escape that
    starts a terminal sequence, and the bidirectional controls are removed.
    """
    return _CONTROL_CHARACTERS.sub("", " ".join(str(text).split()))

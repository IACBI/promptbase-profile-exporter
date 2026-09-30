"""Console output that cannot crash the tool."""

from __future__ import annotations

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
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

"""A guard for test modules that run the commands: a scratch working directory.

The default ``--output-dir`` is relative to the working directory, so a test that
forgets to set one writes ``exports/`` into the repository, where it survives the
run. Call :func:`use_scratch_working_directory` from ``setUpModule``: the module
then runs from an empty scratch directory, and fails if anything lands in it.
"""

from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory


def use_scratch_working_directory() -> None:
    sandbox = TemporaryDirectory()
    original = os.getcwd()
    os.chdir(sandbox.name)

    def restore() -> None:
        leaked = sorted(os.listdir(sandbox.name))
        os.chdir(original)  # Windows cannot remove the current directory
        sandbox.cleanup()
        if leaked:
            raise AssertionError(f"a test wrote into the working directory: {leaked}")

    unittest.addModuleCleanup(restore)

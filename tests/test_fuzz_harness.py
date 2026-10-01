"""The fuzz harness runs in CI under Atheris; this keeps it working in between."""

import importlib.util
import unittest
from pathlib import Path

from tests.scratch import use_scratch_working_directory

HARNESS = Path(__file__).resolve().parent.parent / "fuzz" / "fuzz_parsers.py"


def setUpModule():
    use_scratch_working_directory()


def load_harness():
    spec = importlib.util.spec_from_file_location("fuzz_parsers", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FuzzHarnessTests(unittest.TestCase):
    def test_every_seed_and_a_short_random_run_pass(self):
        harness = load_harness()
        self.assertGreaterEqual(len(harness.seeds()), len(harness.TARGETS))
        harness.smoke(300)

    def test_every_target_has_a_seed(self):
        harness = load_harness()
        chosen = {raw[0] % len(harness.TARGETS) for raw in harness.seeds()}
        self.assertEqual(chosen, set(range(len(harness.TARGETS))))


if __name__ == "__main__":
    unittest.main()

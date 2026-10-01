"""The resolver behind scripts/lock_requirements.py, run against a fake package index."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import packaging
except ImportError:  # the cross-platform test jobs install no tools
    packaging = None

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lock_requirements.py"


def load_script():
    spec = importlib.util.spec_from_file_location("lock_requirements", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index(packages):
    """A fake PyPI: ``{name: {version: [requires_dist, ...]}}``."""
    def metadata(name, version=None):
        releases = packages[name]
        if version is None:
            return {"releases": {v: [{"requires_python": ""}] for v in releases}}
        return {"info": {"requires_dist": releases[version]}, "urls": []}
    return metadata


@unittest.skipIf(packaging is None, "needs packaging (requirements-dev.txt)")
class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.lock = load_script()
        self.env = self.lock.linux("3.12")

    def resolve(self, packages, top):
        with patch.object(self.lock, "metadata", index(packages)):
            found = self.lock.resolve(top, self.env)
        return {name: version for name, (version, _via) in found.items()}

    def test_dependencies_of_an_extra_requested_later_are_included(self):
        packages = {
            # "lib" (no extras) is reached before "plug" asks for lib[speed].
            "app": {"1": ["plug", "lib"]},
            "plug": {"1": ["lib[speed]"]},
            "lib": {"1": ['fast ; extra == "speed"']},
            "fast": {"1": []},
        }
        found = self.resolve(packages, {"app": ("1", set())})
        self.assertEqual(found, {"app": "1", "plug": "1", "lib": "1", "fast": "1"})

    def test_a_later_constraint_narrows_an_earlier_choice(self):
        packages = {
            # "a" is reached first and alone would pick shared 2.0, which "b" rules out.
            "app": {"1": ["b", "a"]},
            "a": {"1": ["shared>=1"]},
            "b": {"1": ["shared<2"]},
            "shared": {"1.0": [], "1.5": [], "2.0": []},
        }
        found = self.resolve(packages, {"app": ("1", set())})
        self.assertEqual(found["shared"], "1.5")

    def test_incompatible_constraints_are_an_error(self):
        packages = {
            "app": {"1": ["a", "b"]},
            "a": {"1": ["shared>=2"]},
            "b": {"1": ["shared<2"]},
            "shared": {"1.0": [], "2.0": []},
        }
        with self.assertRaises(SystemExit):
            self.resolve(packages, {"app": ("1", set())})

    def test_markers_are_evaluated_for_the_target_not_this_machine(self):
        packages = {
            "app": {"1": ['linuxonly ; sys_platform == "linux"',
                          'oldpy ; python_version < "3.11"',
                          'oddrelease ; platform_release == "this-machine"']},
            "linuxonly": {"1": []}, "oldpy": {"1": []}, "oddrelease": {"1": []},
        }
        found = self.resolve(packages, {"app": ("1", set())})
        self.assertEqual(set(found), {"app", "linuxonly"})
        for variable in ("implementation_version", "platform_release", "platform_version"):
            self.assertIn(variable, self.env)

    def test_top_level_entries_are_the_ones_without_via(self):
        text = (
            "mypy==2.3.1 \\\n    --hash=sha256:aa\n"
            "pathspec==1.1.1 \\\n    --hash=sha256:bb\n    # via mypy\n"
            "tomli==2.4.1 ; python_version == \"3.10\" \\\n    --hash=sha256:cc\n    # via mypy\n"
            "ruff==0.16.9 \\\n    --hash=sha256:dd\n"
        )
        self.assertEqual(set(self.lock.top_level(text)), {"mypy", "ruff"})


if __name__ == "__main__":
    unittest.main()

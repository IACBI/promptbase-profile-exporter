import os
import re
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ACTION = Path(__file__).resolve().parent.parent / "action.yml"
BASH = shutil.which("bash")

# A fake `promptbase-export`: it records its arguments and writes the diff
# reports the real one would, sized by $STUB_REPORT_LINES.
STUB = """
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["STUB_ARGS_FILE"], "w", encoding="utf-8") as handle:
    handle.write("\\n".join(args))
lines = int(os.environ.get("STUB_REPORT_LINES", "3"))
for index, arg in enumerate(args):
    if arg != "--diff-output":
        continue
    path = args[index + 1]
    if path.endswith(".md"):
        body = "# PromptBase Catalog Diff\\n\\n" + "- Added: " + "x" * 60 + "\\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body + ("- more\\n" * lines))
    else:
        report = {
            "has_changes": True,
            "summary": {"added": 1, "removed": 0, "changed": 2, "unchanged": 5},
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle)
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


def export_step_script():
    """The bash of the action's "Export PromptBase profile" step, env vars defaulted to empty."""
    text = ACTION.read_text(encoding="utf-8")
    script = textwrap.dedent(re.findall(r"\n      run: \|\n((?:        .*\n|\n)+)", text)[2])
    env_block = text[text.index("id: export"):].split("\n      run: |")[0]
    names = re.findall(r"\n        ([A-Z_]+): ", env_block)
    # An unset input reaches the script as an empty string, never as unset.
    return "".join(f': "${{{name}:=}}"\n' for name in names) + script


@unittest.skipIf(BASH is None or sys.platform == "win32", "needs a POSIX bash")
class ExportStepTests(unittest.TestCase):
    def setUp(self):
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        (self.root / "bin").mkdir()
        (self.root / "stub.py").write_text(STUB, encoding="utf-8")
        stub = self.root / "bin" / "promptbase-export"
        stub.write_text('#!/bin/sh\nexec "$STUB_PYTHON" "$STUB_SCRIPT" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)
        (self.root / "step.sh").write_text(export_step_script(), encoding="utf-8")
        self.summary = self.root / "summary.md"
        self.outputs = self.root / "outputs.txt"
        self.args_file = self.root / "args.txt"
        for path in (self.summary, self.outputs):
            path.write_text("", encoding="utf-8")

    def run_step(self, *, summary=True, **inputs):
        env = {
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "RUNNER_TEMP": str(self.root),
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(self.outputs),
            "STUB_PYTHON": sys.executable,
            "STUB_SCRIPT": str(self.root / "stub.py"),
            "STUB_ARGS_FILE": str(self.args_file),
            "PROFILE_URL": "@acb",
            "MODE": "all",
            "OUTPUT_DIR": "exports",
            "SORT": "newest",
        }
        if summary:
            env["GITHUB_STEP_SUMMARY"] = str(self.summary)
        env.update(inputs)
        return subprocess.run(
            [BASH, str(self.root / "step.sh")], env=env, capture_output=True, text=True
        )

    def passed_args(self):
        return self.args_file.read_text(encoding="utf-8").split("\n")

    def outputs_dict(self):
        lines = self.outputs.read_text("utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def test_a_comparison_writes_the_report_to_the_step_summary(self):
        result = self.run_step(UPDATE_FILE=str(self.root / "catalog.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.summary.read_text("utf-8").startswith("# PromptBase Catalog Diff"))
        outputs = self.outputs_dict()
        self.assertEqual(
            (outputs["has-changes"], outputs["added"], outputs["changed"]), ("true", "1", "2")
        )

    def test_the_summary_is_optional(self):
        off = self.run_step(UPDATE_FILE="c.json", STEP_SUMMARY="false")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertEqual(self.summary.read_text("utf-8"), "")
        self.assertNotIn(".md", " ".join(self.passed_args()))

        bare = self.run_step(UPDATE_FILE="c.json", summary=False)  # no GITHUB_STEP_SUMMARY
        self.assertEqual(bare.returncode, 0, bare.stderr)
        self.assertEqual(self.summary.read_text("utf-8"), "")

    def test_a_plain_export_leaves_the_summary_alone(self):
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.summary.read_text("utf-8"), "")
        self.assertNotIn("--diff-output", self.passed_args())

    def test_an_invalid_step_summary_value_is_rejected(self):
        result = self.run_step(UPDATE_FILE="c.json", STEP_SUMMARY="maybe")
        self.assertEqual(result.returncode, 1)
        self.assertIn("step-summary must be true or false", result.stdout + result.stderr)

    def test_the_summary_is_kept_when_fail_on_diff_exits_2(self):
        result = self.run_step(UPDATE_FILE="c.json", FAIL_ON_DIFF="true", STUB_EXIT="2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("PromptBase Catalog Diff", self.summary.read_text("utf-8"))
        self.assertEqual(self.outputs_dict()["exit-code"], "2")

    def test_an_oversized_report_is_cut_below_the_summary_limit(self):
        result = self.run_step(UPDATE_FILE="c.json", STUB_REPORT_LINES="300000")
        self.assertEqual(result.returncode, 0, result.stderr)
        size = self.summary.stat().st_size
        self.assertLess(size, 1_048_576)  # GitHub's per-step summary limit
        tail = self.summary.read_text("utf-8", errors="replace")[-120:]
        self.assertIn("Report truncated", tail)
        self.assertEqual(self.outputs_dict()["added"], "1")  # the JSON report is complete

    def test_options_are_passed_to_the_exporter_only_when_set(self):
        self.run_step()
        defaults = self.passed_args()
        for flag in ("--extra-fields", "--item-type"):
            self.assertNotIn(flag, defaults)
        self.run_step(EXTRA_FIELDS="tags,engine", ITEM_TYPE="bundle")
        args = self.passed_args()
        self.assertEqual(args[args.index("--extra-fields") + 1], "tags,engine")
        self.assertEqual(args[args.index("--item-type") + 1], "bundle")

    def test_every_action_input_reaches_the_step_environment(self):
        text = ACTION.read_text(encoding="utf-8")
        inputs = set(re.findall(r"\n  ([a-z-]+):\n    description:", text.split("outputs:")[0]))
        used = set(re.findall(r"\$\{\{ inputs\.([a-z-]+) \}\}", text))
        # Inputs the workflow consumes itself rather than through the export step.
        consumed_elsewhere = {"python-version", "working-directory", "upload-artifact",
                              "artifact-name"}
        self.assertEqual(inputs - used - consumed_elsewhere, set())


if __name__ == "__main__":
    unittest.main()

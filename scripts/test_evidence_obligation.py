import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "evidence_obligation.py")
ROOT = os.path.dirname(HERE)

DEFAULT_OBLIGATIONS = {
    "schema": "brother.gate-obligations/v1",
    "default": "REQUIRED_FOR_MERGE",
    "checks": {},
}


def make_repo(temp_dir, obligations):
    repo = os.path.join(temp_dir, "repo")
    scripts = os.path.join(repo, "scripts")
    os.makedirs(scripts, exist_ok=True)
    path = os.path.join(scripts, "gate_obligations.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obligations, handle)
    return repo


def run_transition(repo, stage, lines):
    text = "".join("%s\t%s\n" % item for item in lines)
    return subprocess.run(
        [sys.executable, SCRIPT, "transition", "--stage", stage, "--repo", repo],
        input=text,
        text=True,
        capture_output=True,
    )


def run_check(repo):
    return subprocess.run(
        [sys.executable, SCRIPT, "check", "--repo", repo],
        text=True,
        capture_output=True,
    )


class EvidenceObligationTests(unittest.TestCase):
    def _escape_obligations(self, expected):
        return {
            "schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE",
            "checks": {
                "key-components": {
                    "obligation": "REQUIRED_FOR_MERGE",
                    "reason": "the component registry is not shipped in the public edition",
                    "expected_absent_input": expected,
                }
            },
        }

    def _make_key_components_present(self, repo):
        real = os.path.join(repo, "docs", "plan", "KEY-COMPONENTS.json")
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w", encoding="utf-8") as handle:
            handle.write("{}")
        return real

    def test_all_pass_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, DEFAULT_OBLIGATIONS)
            proc = run_transition(repo, "merge", [("a", 0), ("b", 0)])
            self.assertEqual(proc.returncode, 0)
            self.assertIn("transition: ALLOWED", proc.stdout)

    def test_one_fail_blocked_and_verdict_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, DEFAULT_OBLIGATIONS)
            proc = run_transition(repo, "merge", [("a", 0), ("b", 1)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("b\tFAIL", proc.stdout)

    def test_unlisted_no_data_at_merge_blocked_verdict_no_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, DEFAULT_OBLIGATIONS)
            proc = run_transition(repo, "merge", [("mystery", 2)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("mystery\tNO-DATA", proc.stdout)
            self.assertNotIn("mystery\tPASS", proc.stdout)
            self.assertNotIn("mystery\tFAIL", proc.stdout)

    def test_plugin_manifest_merge_allowed_release_blocked(self):
        obligations = {
            "schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE",
            "checks": {
                "plugin-manifest": {
                    "obligation": "REQUIRED_FOR_RELEASE",
                    "reason": "needs the claude binary, which public CI does not have; the release owner runs it on a machine that does.",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, obligations)
            merge = run_transition(repo, "merge", [("plugin-manifest", 2)])
            release = run_transition(repo, "release", [("plugin-manifest", 2)])
            self.assertEqual(merge.returncode, 0)
            self.assertEqual(release.returncode, 1)

    def test_readiness_board_absent_allowed_present_blocked(self):
        obligations = {
            "schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE",
            "checks": {
                "readiness-board": {
                    "obligation": "REQUIRED_FOR_MERGE",
                    "expected_absent_input": "docs/plan/READINESS-ROADMAP-2026-08-29.json",
                    "reason": "the roadmap file is not shipped in the public edition.",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, obligations)
            absent = run_transition(repo, "merge", [("readiness-board", 2)])
            self.assertEqual(absent.returncode, 0)

            roadmap = os.path.join(repo, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")
            os.makedirs(os.path.dirname(roadmap), exist_ok=True)
            with open(roadmap, "w", encoding="utf-8") as handle:
                handle.write("{}")

            present = run_transition(repo, "merge", [("readiness-board", 2)])
            self.assertEqual(present.returncode, 1)

    def test_absolute_expected_absent_input_blocks_at_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = os.path.join(temp_dir, "outside_absolute_missing")
            repo = make_repo(temp_dir, self._escape_obligations(outside))
            self._make_key_components_present(repo)
            proc = run_transition(repo, "merge", [("key-components", 2)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("BLOCKED", proc.stdout)
            self.assertIn("expected_absent_input escapes the repo", proc.stdout)
            checked = run_check(repo)
            self.assertEqual(checked.returncode, 1)
            self.assertIn("key-components", checked.stdout + checked.stderr)

    def test_dotdot_expected_absent_input_blocks_at_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(
                temp_dir,
                self._escape_obligations("../outside_missing_escape"),
            )
            self._make_key_components_present(repo)
            proc = run_transition(repo, "merge", [("key-components", 2)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("BLOCKED", proc.stdout)
            self.assertIn("expected_absent_input escapes the repo", proc.stdout)
            checked = run_check(repo)
            self.assertEqual(checked.returncode, 1)
            self.assertIn("key-components", checked.stdout + checked.stderr)

    def test_symlink_expected_absent_input_blocks_at_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, self._escape_obligations("link_out"))
            outside_target = os.path.join(temp_dir, "outside_target")
            os.makedirs(outside_target, exist_ok=True)
            os.symlink(outside_target, os.path.join(repo, "link_out"))
            self._make_key_components_present(repo)
            proc = run_transition(repo, "merge", [("key-components", 2)])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("BLOCKED", proc.stdout)
            self.assertIn("expected_absent_input escapes the repo", proc.stdout)

    def test_current_map_requires_plugin_validation_in_ci(self):
        proc = run_transition(ROOT, "merge", [("plugin-manifest", 2)])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("plugin-manifest\tNO-DATA\tREQUIRED_FOR_MERGE\tBLOCKED", proc.stdout)

    def test_measured_public_no_data_checks_together_allowed(self):
        real_path = os.path.join(HERE, "gate_obligations.json")
        with open(real_path, "r", encoding="utf-8") as handle:
            obligations = json.load(handle)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, obligations)
            lines = [
                ("virgin-unit-proof", 2),
                ("key-components", 2),
                ("readiness-board", 2),
                ("board-status", 2),
            ]
            proc = run_transition(repo, "merge", lines)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("transition: ALLOWED", proc.stdout)

    def test_missing_file_empty_stdin_unknown_level(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = os.path.join(temp_dir, "repo")
            os.makedirs(os.path.join(repo, "scripts"), exist_ok=True)
            missing = run_transition(repo, "merge", [("a", 0)])
            self.assertEqual(missing.returncode, 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, DEFAULT_OBLIGATIONS)
            empty = run_transition(repo, "merge", [])
            self.assertEqual(empty.returncode, 2)

        bad = {
            "schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE",
            "checks": {
                "x": {
                    "obligation": "SOMETIMES",
                    "reason": "bad level",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(temp_dir, bad)
            checked = run_check(repo)
            self.assertEqual(checked.returncode, 1)
            self.assertIn("x", checked.stdout + checked.stderr)

    def test_real_obligations_file_passes_check(self):
        proc = run_check(ROOT)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()

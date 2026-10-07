"""ORCH-37 calibration.

THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a precheck that reports NEW
because it could not read the tree or the battery, which is exactly how the
error it exists to prevent happened (nobody looked).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_precheck as P  # noqa: E402

BATTERY = '''#!/bin/bash
run_check "mutation-gate-self" python3 scripts/test_mutation_gate.py -v
run_check "mutation-gate"      python3 scripts/mutation_gate.py
'''


class SpecScoreStaleOutput(unittest.TestCase):
    """spec_score.py --json writes its scores to a file three callers read by name right after the run
    (spec_accept, spec_intake, spec_wave). Before 2026-09-22 a run that died left the PREVIOUS run's file in
    place, and the caller read it as this run's scores. The scorer now removes a pre-existing output first."""

    def test_a_preexisting_output_file_cannot_survive_the_run(self):
        import subprocess
        out = os.path.join(tempfile.mkdtemp(), "scores.json")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write('{"STALE": {"score": 10}}')
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Run it from an EMPTY directory: the scorer dies on the missing plan before it can write, which is
        # exactly the death that used to leave the previous file in place.
        r = subprocess.run([sys.executable, os.path.join(root, "scripts", "loop", "spec_score.py"), "--json", out],
                           capture_output=True, text=True, cwd=tempfile.mkdtemp(), timeout=120)
        self.assertNotEqual(r.returncode, 0, "the scorer was expected to die from an empty directory")
        self.assertFalse(os.path.exists(out), "the previous run's scores survived under this run's name")


class SpecScoreDoneCheckIsTheLandingGates(unittest.TestCase):
    """2026-09-29: the scorer gave the DONE CHECK point to any python3 line the grader's regex accepts, while the
    landing gate runs spec_check.done_check (which joins every line of a fenced block with &&) and refuses anything
    grade_build.OK_CMD does not match. On 1af4d51be 33 of 338 sections scored the point on a check the gate refuses; green
    builds were dropped at landing. The point is now the gate's own verdict; each fixture below differs from the
    accepted one in its done check only."""

    SECTIONS = {
        "X.1": "Done check:\n\n```\npython3 -B scripts/test_a.py\npython3 -B scripts/test_b.py\n```\n",   # chained by the gate
        "X.2": "Done check:\n\n```\npython3 -B scripts/test_a.py\n```\n",                                 # one accepted command
        "X.3": "Run `python3 -B scripts/test_a.py` by hand; no labelled check.\n",                      # a python3 line, no done check
    }

    def score(self):
        import subprocess
        d = tempfile.mkdtemp(prefix="spec-score-dc-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        os.makedirs(os.path.join(d, "docs", "plan", "specs"))
        os.makedirs(os.path.join(d, "home"))
        spec = "# X\n\n## Requirements\nR1 maps to `run_x`.\n\n## Edge cases\nempty, corrupt.\n\n"
        for sub, check in self.SECTIONS.items():
            spec += ("### %s\n`scripts/x_%s.py` NEW.\n\n```python\ndef run_x(n: int) -> int: ...\n```\n\nM-X%s: drop the guard.\n\n%s\n"
                     % (sub, sub[-1], sub[-1], check))
        with open(os.path.join(d, "docs", "plan", "specs", "X.md"), "w", encoding="utf-8") as fh:
            fh.write(spec)
        with open(os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "X", "state": "OPEN", "spec": "docs/plan/specs/X.md", "sub_units": sorted(self.SECTIONS)}]}, fh)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(HOME=os.path.join(d, "home"), GIT_CEILING_DIRECTORIES=os.path.dirname(d))   # no repository above the fixture is read
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(d, "scores.json")
        r = subprocess.run([sys.executable, "-B", os.path.join(root, "scripts", "loop", "spec_score.py"), "--json", out],
                           capture_output=True, text=True, cwd=d, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(out, encoding="utf-8") as fh:
            return {k: [m for m in v["missing"] if m.startswith("DONE CHECK")] for k, v in json.load(fh).items()}

    def test_a_chained_fenced_block_loses_the_point_and_names_the_gate(self):
        dc = self.score()
        self.assertEqual(len(dc["X.1"]), 1, dc)
        self.assertIn("landing gate", dc["X.1"][0])

    def test_one_accepted_command_keeps_the_point(self):
        self.assertEqual(self.score()["X.2"], [])

    def test_a_python3_line_that_is_not_the_labelled_check_loses_the_point(self):
        dc = self.score()
        self.assertEqual(len(dc["X.3"]), 1, dc)


class GateScanHonoursCommandRunners(unittest.TestCase):
    """Owner ruling 2026-09-22: a unit whose plan record names command_runners may order subprocess in its spec;
    the gate scan stops reporting that as a conflict for that unit only, and only for subprocess."""

    def _tree(self, runners):
        d = tempfile.mkdtemp()
        specs = os.path.join(d, "specs"); os.makedirs(specs)
        with open(os.path.join(specs, "R2.md"), "w", encoding="utf-8") as fh:
            fh.write("## R2.1\nimport subprocess\nimport socket\n")
        with open(os.path.join(specs, "X9.md"), "w", encoding="utf-8") as fh:
            fh.write("## X9.1\nimport subprocess\n")
        plan = os.path.join(d, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "R2", "command_runners": runners}, {"id": "X9"}]}, fh)
        return specs, plan

    def _conflicts(self, specs, plan):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = P.main_gate_scan(specs, plan)
        return rc, [l for l in buf.getvalue().splitlines() if l.startswith("CONFLICT")]

    def test_a_runner_unit_keeps_its_socket_conflict_and_loses_only_subprocess(self):
        rc, lines = self._conflicts(*self._tree(["scripts/git_location_guard.py"]))
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(l.split()[1] + ":" + l.split("`")[1] for l in lines), ["R2.md:socket", "X9.md:subprocess"])

    def test_without_runners_every_conflict_is_reported(self):
        rc, lines = self._conflicts(*self._tree([]))
        self.assertEqual(rc, 1)
        self.assertEqual(len(lines), 3)

    def test_a_runner_entry_that_is_not_a_file_path_is_refused_by_name(self):
        # 2026-09-24: all six H units named ["subprocess", "os"]; the screen allows PATHS only, so every build that
        # restated an owned file's subprocess import was refused (H5.a, every round) and the brief told the worker
        # "runners: subprocess, os". The scan names each malformed entry and fails.
        import io, contextlib
        specs, plan = self._tree(["subprocess", "scripts/git_location_guard.py"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = P.main_gate_scan(specs, plan)
        bad = [l for l in buf.getvalue().splitlines() if l.startswith("MALFORMED")]
        self.assertEqual(rc, 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("R2", bad[0]); self.assertIn("'subprocess'", bad[0])
        specs, plan = self._tree(["scripts/git_location_guard.py", "tools/run.sh"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            P.main_gate_scan(specs, plan)
        self.assertNotIn("MALFORMED", out.getvalue())

    def test_an_unreadable_plan_reports_everything(self):
        specs, plan = self._tree(["scripts/git_location_guard.py"])
        self.assertEqual(P.units_with_command_runners(os.path.join(specs, "nope.json")), set())
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.assertEqual(len(self._conflicts(specs, plan)[1]), 3)


class SpecPrecheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = self.tmp.name
        os.makedirs(os.path.join(self.tree, "scripts"))
        with open(os.path.join(self.tree, "scripts", "check_all.sh"), "w") as fh:
            fh.write(BATTERY)
        self.plan = os.path.join(self.tree, "wbs.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write_plan(self, units):
        with open(self.plan, "w") as fh:
            json.dump({"units": units}, fh)
        return ["--plan", self.plan, "--tree", self.tree]

    def touch(self, rel):
        path = os.path.join(self.tree, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# already here\n")

    def test_a_row_naming_a_file_that_exists_is_modify(self):
        self.touch("scripts/already.py")
        args = self.write_plan([{"id": "R1", "owns": ["scripts/already.py"]}])
        self.assertEqual(P.main(args), 1)

    def test_a_row_whose_check_is_registered_is_modify_even_if_the_file_is_gone(self):
        """The real ORCH-19 shape: the battery already decides this row."""
        args = self.write_plan([{"id": "R2", "owns": ["scripts/mutation_gate.py"]}])
        self.assertEqual(P.main(args), 1)

    def test_a_genuinely_new_row_is_new(self):
        args = self.write_plan([{"id": "R3", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args), 0)

    def test_rows_without_owns_are_not_counted(self):
        args = self.write_plan([{"id": "R4"}])
        self.assertEqual(P.main(args), 2, "nothing checked must be NO-DATA, never NEW")

    def test_an_unreadable_plan_is_no_data(self):
        self.assertEqual(P.main(["--plan", os.path.join(self.tree, "gone.json"),
                                 "--tree", self.tree]), 2)

    def test_an_unreadable_battery_is_no_data_not_new(self):
        args = self.write_plan([{"id": "R5", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args + ["--battery", "scripts/missing.sh"]), 2)

    def test_only_the_named_row_is_checked(self):
        self.touch("scripts/already.py")
        args = self.write_plan([{"id": "R1", "owns": ["scripts/already.py"]},
                                {"id": "R3", "owns": ["scripts/brand_new.py"]}])
        self.assertEqual(P.main(args + ["--row", "R3"]), 0)
        self.assertEqual(P.main(args + ["--row", "R1"]), 1)

    def test_the_reason_names_the_existing_file_and_the_registered_check(self):
        checks = P.registered_checks(BATTERY)
        verdict, reasons = P.check_row({"id": "R2", "owns": ["scripts/mutation_gate.py"]},
                                       self.tree, checks)
        self.assertEqual(verdict, "MODIFY")
        self.assertTrue(any("mutation-gate" in r for r in reasons), reasons)

    def test_a_home_relative_owns_path_is_resolved(self):
        """A tilde path is EXPANDED before the existence check.

        This used to assert against ~/.claude/hooks/command_text.py on the developer's own machine, so it
        failed outright in an export tree with an empty HOME: the file is simply not there, check_row correctly
        answered NEW, and the assertion blew up. That is the not-hermetic class, the most expensive one this
        estate records, and it was also testing the wrong thing. The subject is whether check_row EXPANDS a
        tilde, not whether one particular machine happens to hold one particular hook.

        It now builds both worlds itself, which tests the expansion in both directions rather than only one."""
        import tempfile
        home = tempfile.mkdtemp()
        hook = os.path.join(home, ".claude", "hooks", "command_text.py")
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write("# fixture\n")
        row = {"id": "R6", "owns": ["~/.claude/hooks/command_text.py"]}
        old_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = home
            verdict, _ = P.check_row(row, self.tree, {})
            self.assertEqual(verdict, "MODIFY", "a tilde path that resolves to a real file is not new work")
            os.environ["HOME"] = tempfile.mkdtemp()          # a home with no such hook
            verdict, _ = P.check_row(row, self.tree, {})
            self.assertEqual(verdict, "NEW", "the same tilde path with nothing behind it IS new work")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home



class GateScanRefusesAnUnreadableSpec(unittest.TestCase):
    """2026-09-30: an unreadable spec was skipped and the scan could still exit 0, so unchecked read as checked."""

    def test_an_unreadable_spec_fails_the_scan(self):
        import contextlib, io
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            spec = os.path.join(d, "U1.md")
            with open(spec, "w", encoding="utf-8") as f:
                f.write("a spec with nothing refused in it\n")
            os.chmod(spec, 0)
            out = io.StringIO()
            try:
                with mock.patch.object(P, "refused_modules", return_value={"subprocess"}), \
                        contextlib.redirect_stdout(out):
                    rc = P.main_gate_scan(specs_dir=d, plan_path=os.path.join(d, "absent.json"))
            finally:
                os.chmod(spec, 0o600)
            self.assertNotEqual(rc, 0, out.getvalue())
            self.assertIn("UNREADABLE", out.getvalue())

if __name__ == "__main__":
    unittest.main()

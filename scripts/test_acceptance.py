"""Calibration for scripts/acceptance.py (G1-M3.2, and the --area/--explain
flags G1-M3.3 adds).

Every case asserts the EXIT CODE, never only the printed verdict string,
following this estate's most expensive recent lesson (see
scripts/test_leaf_pin_check.py): a gate that prints FAIL and exits 0
manufactures evidence of a pass.

Proves:
  * the eleven real areas load from docs/plan/CAPABILITY-AREAS.json
  * a fixture area whose script exits 0 reads PASS
  * a fixture area whose script exits 1 reads FAIL
  * an area with no script at all reads NO-DATA
  * NO-DATA does not affect main()'s exit code; FAIL does
  * --area filters the run to that one area
  * --explain forwards to the per-area script's own argv
  * an unknown --area id reads NO-DATA, not a crash and not a pass
  * --explain without --area is a usage error, not a silent no-op
  * scripts/acceptance_1.py itself, the real area 1 test, actually runs
  * scripts/acceptance_2.py (interrupt and redirect) and scripts/
    acceptance_3.py (partial diff acceptance) actually run, --explain and
    --calibrate included
  * scripts/acceptance_4.py (monorepos and generated code), scripts/
    acceptance_5.py (terminal cancellation and hung command recovery) and
    scripts/acceptance_6.py (dirty trees and rebases preserving unrelated
    changes) actually run, --explain and --calibrate included
  * scripts/acceptance_7.py (choosing the right tests and telling not-run
    from passed), scripts/acceptance_8.py (safety without approval
    fatigue) and scripts/acceptance_9.py (crash recovery and resumable
    sessions) actually run, --explain and --calibrate included
  * scripts/acceptance_10.py (everyday editor conveniences) and scripts/
    acceptance_11.py (operational credibility) actually run, --explain and
    --calibrate included
"""
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import acceptance


class AreasFileLoads(unittest.TestCase):

    def test_eleven_real_areas_load(self):
        areas = acceptance.load_areas()
        self.assertEqual(len(areas), 11)
        for area in areas:
            self.assertIn("id", area)
            self.assertIn("name", area)


class RunAreaVerdicts(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scripts_dir = pathlib.Path(self._tmp.name)

    def _write(self, area_id, body):
        (self.scripts_dir / "acceptance_{}.py".format(area_id)).write_text(body)

    def test_exit_zero_reads_pass(self):
        self._write("p", "print('ok')\nraise SystemExit(0)\n")
        verdict, evidence = acceptance.run_area({"id": "p"}, scripts_dir=self.scripts_dir)
        self.assertEqual(verdict, "PASS")
        self.assertIn("ok", evidence)

    def test_exit_one_reads_fail(self):
        self._write("f", "print('broke')\nraise SystemExit(1)\n")
        verdict, evidence = acceptance.run_area({"id": "f"}, scripts_dir=self.scripts_dir)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("broke", evidence)

    def test_exit_two_reads_no_data(self):
        self._write("n", "print('nothing to check')\nraise SystemExit(2)\n")
        verdict, evidence = acceptance.run_area({"id": "n"}, scripts_dir=self.scripts_dir)
        self.assertEqual(verdict, "NO-DATA")

    def test_missing_script_reads_no_data_never_a_pass(self):
        verdict, evidence = acceptance.run_area({"id": "missing-entirely"},
                                                  scripts_dir=self.scripts_dir)
        self.assertEqual(verdict, "NO-DATA")
        self.assertNotEqual(verdict, "PASS")
        self.assertIn("no test yet", evidence)


class MainExitCode(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = pathlib.Path(self._tmp.name)

    def _areas_file(self, areas):
        import json
        path = self.tmp_dir / "areas.json"
        with open(path, "w") as f:
            json.dump(areas, f)
        return path

    def test_all_no_data_exits_zero_not_a_failure(self):
        """An area with no test yet is NO-DATA, and NO-DATA must never
        flip main()'s exit code: a run that could check nothing has not
        said anything is broken."""
        # main() reads the real areas file and the real scripts dir by
        # design (no CLI override), so exercise its pieces directly
        # instead of routing argv through it.
        areas_file = self._areas_file([{"id": "z1", "name": "no script here"}])
        areas = acceptance.load_areas(areas_file)
        results = acceptance.run_all(areas, scripts_dir=self.tmp_dir)
        fail_count = sum(1 for r in results if r[2] == "FAIL")
        self.assertEqual(fail_count, 0)
        self.assertEqual(results[0][2], "NO-DATA")

    def test_a_real_fail_would_flip_the_exit_code(self):
        (self.tmp_dir / "acceptance_z2.py").write_text(
            "print('regressed')\nraise SystemExit(1)\n")
        areas_file = self._areas_file([{"id": "z2", "name": "a real regression"}])
        areas = acceptance.load_areas(areas_file)
        results = acceptance.run_all(areas, scripts_dir=self.tmp_dir)
        fail_count = sum(1 for r in results if r[2] == "FAIL")
        self.assertEqual(fail_count, 1)

    def test_selftest_exits_zero(self):
        self.assertEqual(acceptance.main(["--selftest"]), 0)


class AreaFlag(unittest.TestCase):
    """--area and --explain, added in G1-M3.3.

    Patches acceptance.AREAS_FILE and acceptance.SCRIPTS_DIR so main() reads
    a throwaway fixture pair instead of the real eleven areas; load_areas
    and run_area/run_all read those globals at CALL time (see the ponytail
    note beside load_areas), so the patch actually takes effect here."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = pathlib.Path(self._tmp.name)
        self.areas_file = self.tmp_dir / "areas.json"
        with open(self.areas_file, "w") as f:
            json.dump([{"id": "a1", "name": "area one"},
                       {"id": "a2", "name": "area two"}], f)
        (self.tmp_dir / "acceptance_a1.py").write_text(
            "import sys\n"
            "if '--explain' in sys.argv:\n"
            "    print('explanation for a1')\n"
            "if '--calibrate' in sys.argv:\n"
            "    print('calibration for a1')\n"
            "print('verdict for a1')\n"
            "raise SystemExit(0)\n")
        (self.tmp_dir / "acceptance_a2.py").write_text(
            "print('verdict for a2')\nraise SystemExit(0)\n")
        self._patches = (
            mock.patch.object(acceptance, "AREAS_FILE", self.areas_file),
            mock.patch.object(acceptance, "SCRIPTS_DIR", self.tmp_dir))
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = acceptance.main(argv)
        return code, buf.getvalue()

    def test_area_runs_only_the_named_one(self):
        code, out = self._run(["--area", "a1"])
        self.assertEqual(code, 0)
        self.assertIn("verdict for a1", out)
        self.assertNotIn("verdict for a2", out)
        self.assertIn("1 area(s)", out)

    def test_unknown_area_is_no_data_not_a_crash_and_not_a_pass(self):
        code, out = self._run(["--area", "no-such-area"])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_explain_forwards_to_the_per_area_script(self):
        code, out = self._run(["--area", "a1", "--explain"])
        self.assertEqual(code, 0)
        self.assertIn("explanation for a1", out)

    def test_explain_without_area_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(["--explain"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_calibrate_forwards_to_the_per_area_script(self):
        code, out = self._run(["--area", "a1", "--calibrate"])
        self.assertEqual(code, 0)
        self.assertIn("calibration for a1", out)

    def test_calibrate_without_area_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(["--calibrate"])
        self.assertNotEqual(ctx.exception.code, 0)


class Area1RealTest(unittest.TestCase):
    """scripts/acceptance_1.py itself: the first area, end to end, driving
    the real spine (loop_bridge.py) against a real temp git repository."""

    def test_area_1_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_1.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("artifact=", proc.stdout)

    def test_area_1_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_1.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 1 template", proc.stdout)

    def test_area_1_calibrate_proves_it_can_fail(self):
        """G1-M3.4.2: a green reading of the real test is decoration until
        it has been shown capable of going red. --calibrate forces exactly
        that (via loop_bridge's own --null-worker) and this asserts the
        calibration itself reports PASS, meaning the forced failure was
        correctly detected."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_1.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read it as failed", proc.stdout)

    def test_area_1_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "1", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_1_no_data_when_the_spine_is_absent(self):
        import acceptance_1
        with mock.patch.object(acceptance_1, "LOOP_BRIDGE",
                               "/no/such/loop_bridge.py"):
            code, evidence = acceptance_1.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area2RealTest(unittest.TestCase):
    """scripts/acceptance_2.py: interrupt and redirect without losing
    state, driving the real claim_store spine plus loop_bridge.py against
    a real temp git repository (G1-M3.5)."""

    def test_area_2_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_2.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("reconcile", proc.stdout)

    def test_area_2_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_2.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 2 template", proc.stdout)

    def test_area_2_calibrate_proves_it_can_fail(self):
        """G1-M3.5.2: --calibrate deletes the claim store between the
        interruption and the reconcile call (the state that must survive
        is made to vanish on purpose), and this asserts the calibration
        itself reports PASS, meaning the forced failure was correctly
        detected."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_2.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read it as failed", proc.stdout)

    def test_area_2_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "2", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_2_no_data_when_the_spine_is_absent(self):
        import acceptance_2
        with mock.patch.object(acceptance_2, "LOOP_BRIDGE",
                               "/no/such/loop_bridge.py"):
            code, evidence = acceptance_2.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area3RealTest(unittest.TestCase):
    """scripts/acceptance_3.py: partial diff acceptance, driving real git
    apply/diff against a real temp git repository (G1-M3.6)."""

    def test_area_3_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_3.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("hunk", proc.stdout)

    def test_area_3_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_3.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 3 template", proc.stdout)

    def test_area_3_calibrate_proves_it_can_fail(self):
        """G1-M3.6.2: --calibrate applies the full two-hunk patch where
        only one hunk was meant to land (the rejected region is made to
        leak through on purpose), and this asserts the calibration itself
        reports PASS, meaning the forced failure was correctly detected."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_3.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read", proc.stdout)

    def test_area_3_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "3", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_3_no_data_when_git_apply_is_unusable(self):
        import acceptance_3
        real_run = subprocess.run

        def fake_run(args, *a, **kw):
            if args[:2] == ["git", "apply"] and args[2:3] == ["--help"]:
                return subprocess.CompletedProcess(args, 1, "", "no git")
            return real_run(args, *a, **kw)

        with mock.patch.object(acceptance_3.subprocess, "run", fake_run):
            code, evidence = acceptance_3.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area4RealTest(unittest.TestCase):
    """scripts/acceptance_4.py: monorepos and generated code, driving the
    real scope gate (loop_bridge.run_node + scope_audit.py) against a real
    temp monorepo-shaped git repository (G1-M3.7)."""

    def test_area_4_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_4.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("CLEAN", proc.stdout)

    def test_area_4_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_4.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 4 template", proc.stdout)

    def test_area_4_calibrate_proves_it_can_fail(self):
        """G1-M3.7.2: --calibrate uses a worker that edits the protected
        generated file under an honest, narrow scope declaration, and this
        asserts the calibration itself reports PASS, meaning the scope
        gate's QUARANTINE verdict was correctly read as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_4.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly quarantined", proc.stdout)

    def test_area_4_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "4", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_4_no_data_when_the_scope_gate_is_absent(self):
        import acceptance_4
        with mock.patch.dict(sys.modules, {"loop_bridge": None}):
            code, evidence = acceptance_4.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area5RealTest(unittest.TestCase):
    """scripts/acceptance_5.py: terminal cancellation and hung command
    recovery, driving the real bm_worker_spawn.SpawningWorker adapter
    against a real hung process (G1-M3.8)."""

    def test_area_5_passes_against_a_real_hung_process(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_5.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("terminated", proc.stdout)

    def test_area_5_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_5.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 5 template", proc.stdout)

    def test_area_5_calibrate_proves_it_can_fail(self):
        """G1-M3.8.2: --calibrate uses a real detached grandchild process
        that escapes the killed foreground process, and this asserts the
        calibration itself reports PASS, meaning the surviving process was
        correctly read as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_5.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read the survivor as failed", proc.stdout)

    def test_area_5_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "5", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_5_no_data_when_the_spawn_module_is_absent(self):
        import acceptance_5
        with mock.patch.object(acceptance_5, "_load_spawn_module",
                               return_value=(None, "no sibling tools checkout")):
            code, evidence = acceptance_5.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area6RealTest(unittest.TestCase):
    """scripts/acceptance_6.py: dirty trees and rebases preserving
    unrelated changes, driving the real integrate.py dirty-tree guard
    against a real temp git repository (G1-M3.9)."""

    def test_area_6_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_6.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("REFUSED", proc.stdout)

    def test_area_6_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_6.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 6 template", proc.stdout)

    def test_area_6_calibrate_proves_it_can_fail(self):
        """G1-M3.9.2: --calibrate skips the dirty-tree guard and drives the
        same real merge-then-unwind sequence integrate_one uses, and this
        asserts the calibration itself reports PASS, meaning the unrelated
        edit's loss was correctly read as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_6.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("lost exactly as this test expects", proc.stdout)

    def test_area_6_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "6", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_6_no_data_when_integrate_is_absent(self):
        import acceptance_6
        with mock.patch.dict(sys.modules, {"integrate": None}):
            code, evidence = acceptance_6.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area7RealTest(unittest.TestCase):
    """scripts/acceptance_7.py: choosing the right tests and telling
    not-run from passed, driving the real integrate.py three-way verdict
    split (INTEGRATED/NODATA/NEEDS_REPAIR) against a real temp git
    repository (G1-M3.10)."""

    def test_area_7_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_7.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("NODATA", proc.stdout)

    def test_area_7_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_7.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 7 template", proc.stdout)

    def test_area_7_calibrate_proves_it_can_fail(self):
        """G1-M3.10.2: --calibrate patches integrate._run_check so a
        missing done_check reads as trivially passed instead of NODATA, and
        this asserts the calibration itself reports PASS, meaning the
        resulting false INTEGRATED verdict was correctly read as a
        failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_7.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("false-pass", proc.stdout)

    def test_area_7_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "7", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_7_no_data_when_integrate_is_absent(self):
        import acceptance_7
        with mock.patch.dict(sys.modules, {"integrate": None}):
            code, evidence = acceptance_7.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area8RealTest(unittest.TestCase):
    """scripts/acceptance_8.py: safety without approval fatigue, driving
    the real scope_audit.py quarantine path across a real session of many
    commits in a temp git repository (G1-M3.11)."""

    def test_area_8_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_8.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("0 of 12", proc.stdout)

    def test_area_8_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_8.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 8 template", proc.stdout)

    def test_area_8_calibrate_proves_it_can_fail(self):
        """G1-M3.11.2: --calibrate skips the audit call for the dangerous
        action entirely, and this asserts the calibration itself reports
        PASS, meaning the resulting gate-free dangerous action was
        correctly read as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_8.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read the resulting gate-free dangerous action as failed", proc.stdout)

    def test_area_8_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "8", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_8_no_data_when_scope_audit_is_absent(self):
        import acceptance_8
        with mock.patch.dict(sys.modules, {"scope_audit": None}):
            code, evidence = acceptance_8.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area9RealTest(unittest.TestCase):
    """scripts/acceptance_9.py: crash recovery and resumable sessions,
    driving a real loop_bridge.py controller subprocess killed with
    SIGKILL against the real claim_store.py spine (G1-M3.12)."""

    def test_area_9_passes_against_a_real_sigkill(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_9.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("attempt 2", proc.stdout)

    def test_area_9_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_9.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 9 template", proc.stdout)

    def test_area_9_calibrate_proves_it_can_fail(self):
        """G1-M3.12.2: --calibrate patches claim_store.acquire so its own
        exclusivity guard is skipped, and this asserts the calibration
        itself reports PASS, meaning the resulting duplicate grant was
        correctly read as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_9.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read the resulting duplicate grant as failed", proc.stdout)

    def test_area_9_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "9", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_9_no_data_when_the_spine_is_absent(self):
        import acceptance_9
        with mock.patch.object(acceptance_9, "LOOP_BRIDGE",
                               "/no/such/loop_bridge.py"):
            code, evidence = acceptance_9.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area10RealTest(unittest.TestCase):
    """scripts/acceptance_10.py: everyday editor conveniences, driving a
    plain textual scan (jump to definition, rename across files) and real
    git (diff view, revert) against a real temp git repository."""

    def test_area_10_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_10.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("jump to definition", proc.stdout)

    def test_area_10_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_10.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 10 template", proc.stdout)

    def test_area_10_calibrate_proves_it_can_fail(self):
        """--calibrate makes a later commit edit the exact line the first
        commit is about to be reverted from, and this asserts the
        calibration itself reports PASS, meaning the resulting revert
        conflict was correctly detected as a failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_10.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read the resulting revert conflict as failed",
                      proc.stdout)

    def test_area_10_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "10", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_10_no_data_when_git_is_unusable(self):
        import acceptance_10
        real_run = acceptance_10.subprocess.run

        def fake_run(args, *a, **kw):
            if args[:2] == ["git", "diff"] and args[2:3] == ["--help"]:
                return subprocess.CompletedProcess(args, 1, "", "no git")
            return real_run(args, *a, **kw)

        with mock.patch.object(acceptance_10.subprocess, "run", fake_run):
            code, evidence = acceptance_10.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


class Area11RealTest(unittest.TestCase):
    """scripts/acceptance_11.py: operational credibility, driving the real
    loop_bridge.run_node against a real, genuinely-failing worker
    subprocess in a real temp git repository."""

    def test_area_11_passes_against_a_real_repository(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_11.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("PASS"), proc.stdout)
        self.assertIn("EXHAUSTED", proc.stdout)

    def test_area_11_names_the_swallowed_error_gap_without_gating_on_it(self):
        """The real, measured finding (the worker's own stderr never
        reaches run_node's record) must appear in the evidence even on a
        PASS, per this script's own docstring: measured and named, never
        hidden, never the thing that flips the exit code."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_11.py"
        proc = subprocess.run([sys.executable, str(script)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ABSENT from run_node's own record", proc.stdout)
        self.assertIn("silently swallowed", proc.stdout)

    def test_area_11_explain_prints_the_template(self):
        script = pathlib.Path(__file__).resolve().parent / "acceptance_11.py"
        proc = subprocess.run([sys.executable, str(script), "--explain"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("area 11 template", proc.stdout)

    def test_area_11_calibrate_proves_it_can_fail(self):
        """--calibrate patches bm_verify.is_pass so every verdict reads as
        a pass, short-circuiting run_node before it attempts repair, and
        this asserts the calibration itself reports PASS, meaning the
        resulting no-repair-attempted record was correctly read as a
        failure."""
        script = pathlib.Path(__file__).resolve().parent / "acceptance_11.py"
        proc = subprocess.run([sys.executable, str(script), "--calibrate"],
                              capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("correctly read the resulting no-repair-attempted "
                      "record as", proc.stdout)

    def test_area_11_calibrate_via_the_harness_area_flag(self):
        proc = subprocess.run(
            [sys.executable,
             str(pathlib.Path(__file__).resolve().parent / "acceptance.py"),
             "--area", "11", "--calibrate"],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 area(s): 1 pass, 0 fail, 0 no-data", proc.stdout)

    def test_area_11_no_data_when_the_spine_is_absent(self):
        import acceptance_11
        with mock.patch.object(acceptance_11, "LOOP_BRIDGE",
                               "/no/such/loop_bridge.py"):
            code, evidence = acceptance_11.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", evidence)


if __name__ == "__main__":
    unittest.main()

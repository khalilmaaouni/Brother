#!/usr/bin/env python3
"""finish_run: an observed crash always wins, a refused landing is a non-zero exit, and a repair is named so it can land.

THREE FINDINGS OF 2026-09-27 (audit B, cases 3, 7 and 8), each reproduced on hub main before this file was written:
  B3  reexecute() kept a probe set's result only when probe_build exited 0, and probe_build exits 1 exactly when it saw a
      crash, so one clean set masked another set's crash and the build was promoted READY.
  B7  main() dropped land_batch.py's exit code and returned 0 after a refused landing.
  B8  a repair was written as <sub>-finisher-build.json, a name the lander always holds, so no repair could ever land.

ONE CONDITION PER FIXTURE: each probe case changes one probe set's answer; the exit case changes only land_batch's exit
code (its control lands); the naming case runs the real finish() into the real land_batch.py in a scratch landing tree
(test_land_batch_landings.Fixture) with a stubbed grader and a stubbed probe, so the only thing under test is the name.
Run: python3 -B scripts/loop/test_finish_run.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS directory's finish_run and fixture, never another copy on the path
import finish_run as F  # noqa: E402
import loop_hold  # noqa: E402
import test_land_batch_landings as LF  # noqa: E402

CLEAN = "PROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?, 1 REFUSED, 0 RETURNED, 0 NO-DATA\n"


class Answer(object):
    def __init__(self, ok, answer, detail=""):
        self.ok, self.answer, self.detail = ok, answer, detail


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="finish-run-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # the advisor is a paid call: off; the regrade probe switch is the operator's, never read from his shell here (FX-43)
        env = {k: v for k, v in os.environ.items() if k not in ("BROTHER_REPAIR_ADVISOR", "BROTHER_FINISHER_REGRADE_PROBES")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)

    def cache(self, sub, names):
        """One cached probe set per name, in the layout reexecute globs (<cache>/<sub>/<set>/<file>.json)."""
        root = os.path.join(self.root, "cache")
        for n in names:
            os.makedirs(os.path.join(root, sub, n), exist_ok=True)
            with open(os.path.join(root, sub, n, "probe.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
        os.environ["BROTHER_PROBE_CACHE"] = root


class ProbeVerdict(Base):
    """B3: two probe sets, one answer each; the verdict is DIRTY on any observed finding, NO-DATA on any unreadable set."""

    def verdict(self, answers):
        self.cache("U1.a", sorted(answers))
        def run(argv, cwd):
            return answers[os.path.basename(os.path.dirname(argv[-1]))]
        return F.reexecute("U1.a", "/unused/build.json", "/unused/spec.md", run)

    def test_a_crash_one_set_saw_is_dirty_whatever_another_set_said(self):
        crash = (1, "CRASH    none_input TypeError target failed\nPROBES   1 run: 1 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 0 RETURNED, 0 NO-DATA\n")
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": crash})[0], "DIRTY")

    def test_a_crash_seen_before_the_probe_child_died_is_dirty(self):
        died = (1, "CRASH    none_input TypeError target failed\nPROBES-ABORTED  child exit=1 after 1 line(s); counts withheld\n")
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": died})[0], "DIRTY")

    def test_a_crash_seen_before_a_run_ended_at_exit_2_is_dirty(self):
        # the probe lane (2026-09-27): probe_build exits 2 when no sandbox is available; a crash line it printed first wins
        aborted = (2, "CRASH    none_input TypeError x\nPROBES-ABORTED  child exit=2 after 1 line(s); counts withheld\n")
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": aborted})[0], "DIRTY")

    def test_a_run_with_no_sandbox_never_counts_toward_clean(self):
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": (2, "NO-DATA: no sandbox is available for this probe\n")})[0], "NO-DATA")

    def test_a_set_that_answered_no_data_is_never_clean(self):
        nodata = (2, "PROBES   0 run: 0 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 0 RETURNED, 2 NO-DATA\nNO-DATA: the probe fired 2 case(s)\n")
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": nodata})[0], "NO-DATA")

    def test_a_set_that_exited_zero_with_no_counts_is_never_clean(self):
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": (0, "Traceback: the probe printed no counts\n")})[0], "NO-DATA")

    def test_an_empty_cache_asks_probe_wave_for_the_set_then_reexecutes_it(self):
        # a survivor of the generic mutation sweep (2026-09-27): skipping the probe_wave fallback changed no test
        root = os.path.join(self.root, "cache"); os.environ["BROTHER_PROBE_CACHE"] = root
        def run(argv, cwd):
            if argv[1].endswith("probe_wave.py"):
                os.makedirs(os.path.join(root, "U1.a", "w"))
                with open(os.path.join(root, "U1.a", "w", "probe.json"), "w", encoding="utf-8") as fh:
                    fh.write("{}")
                return 0, ""
            return 0, CLEAN
        self.assertEqual(F.reexecute("U1.a", "/unused/round0/out/build.json", "", run)[0], "CLEAN")

    def test_every_set_clean_is_clean(self):
        self.assertEqual(self.verdict({"a": (0, CLEAN), "b": (0, CLEAN)}), ("CLEAN", "2 probe(s) ran, none dirty"))

    def test_the_finisher_never_promotes_nor_lands_a_build_a_set_saw_crash(self):
        self.cache("U1.a", ["a", "b"])
        os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = "crash"   # FX-43: the regrade re-executes the sets only when asked
        answers = {"a": (0, CLEAN), "b": (1, "CRASH    none_input TypeError x\nPROBES   1 run: 1 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 0 RETURNED, 0 NO-DATA\n")}
        run = lambda argv, cwd: (0, "PASS x") if "grade_build" in " ".join(argv) else answers[os.path.basename(os.path.dirname(argv[-1]))]
        promoted, landed = [], []
        cand = {"sub": "U1.a", "build": "/unused/build.json", "probes": "NO-DATA", "applies": "APPLIES", "action": "leave"}
        rows, ready, _ = F.finish([cand], "m", lambda *a: self.fail("no model is called to reprobe"), run, {},
                                  promote=lambda c: promoted.append(c["sub"]), land=lambda b: landed.extend(b))
        self.assertEqual((rows[0][1], promoted, landed, ready), ("NOT SAVED", [], [], []), rows)
        self.assertTrue(rows[0][2].startswith("probes after the regrade: DIRTY"), rows)


class RefusedPromotion(Base):
    """salvage.promote returns '' when it wrote READY and the reason when it refused (a landing's word won, or a newer
    run speaks for the sub unit). A refusal is NOT SAVED with that reason, never READY, never handed to the lander."""

    def test_a_refused_promotion_of_a_clean_build_is_not_saved(self):
        landed = []
        cand = {"sub": "U1.a", "build": "/unused/build.json", "probes": "CLEAN", "applies": "APPLIES", "action": "PROMOTE"}
        grade_only = lambda argv, cwd: (0, "PASS x") if "grade_build" in " ".join(argv) else self.fail("only the grade runs")
        rows, ready, _ = F.finish([cand], "m", lambda *a: self.fail("no model"), grade_only, {},
                                  promote=lambda c: "STATUS changed since selection", land=lambda b: landed.extend(b))
        self.assertEqual((rows[0][1], ready, landed), ("NOT SAVED", [], []), rows)
        self.assertIn("STATUS changed since selection", rows[0][2])

    def test_a_refused_promotion_after_a_clean_reprobe_is_not_saved(self):
        self.cache("U1.a", ["a"])
        os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = "crash"   # so the reprobe in this test's name really runs
        landed = []
        cand = {"sub": "U1.a", "build": "/unused/build.json", "probes": "NO-DATA", "applies": "APPLIES", "action": "leave"}
        run = lambda argv, cwd: (0, "PASS x") if "grade_build" in " ".join(argv) else (0, CLEAN)
        rows, ready, _ = F.finish([cand], "m", lambda *a: self.fail("no model"), run, {},
                                  promote=lambda c: "newest run of U1.a is now READY", land=lambda b: landed.extend(b))
        self.assertEqual((rows[0][1], ready, landed), ("NOT SAVED", [], []), rows)
        self.assertIn("newest run of U1.a is now READY", rows[0][2])


class FinisherExit(Base):
    """B7: the finisher's exit code carries land_batch's."""

    def main_with_landing(self, code, line, alive=0):
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write('{"units": []}')
        def child(argv, **kw):
            if any(str(x).endswith("land_batch.py") for x in argv):
                return subprocess.CompletedProcess(argv, code, line + "\n", "")
            if any(str(x).endswith("grade_build.py") for x in argv):
                return subprocess.CompletedProcess(argv, 0, "PASS x\n", "")   # the fresh grade of a promotion
            return subprocess.CompletedProcess(argv, alive, "", "")   # stop_loop.sh --dry: 0 when nothing of the loop is alive
        cand = dict(sub="U1.a", build="/unused/build.json", probes="CLEAN", applies="APPLIES", action="PROMOTE")
        out = []
        with mock.patch.object(F.salvage, "PLAN", plan), mock.patch.object(F, "candidates_of", return_value=[cand]), \
                mock.patch.object(F.salvage, "promote", return_value=""), mock.patch.object(loop_hold, "gate"), \
                mock.patch.object(F.subprocess, "run", side_effect=child), \
                mock.patch.object(sys, "argv", ["finish_run.py", "--model", "unused"]), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            return F.main(), "\n".join(out)

    def test_a_refused_landing_is_a_nonzero_exit(self):
        rc, out = self.main_with_landing(1, "REFUSED: commit scan exit 1; nothing landed")
        self.assertIn("landing: REFUSED", out)
        self.assertEqual(rc, 1, out)

    def test_a_live_loop_refuses_before_anything_is_landed(self):
        # a survivor of the generic mutation sweep (2026-09-27): the refusal's exit code changed no test
        rc, out = self.main_with_landing(0, "LANDED  U1.a | log x", alive=1)
        self.assertIn("FINISH REFUSED", out)
        self.assertEqual(rc, 1, out)

    def test_a_landing_that_landed_exits_zero(self):
        rc, out = self.main_with_landing(0, "LANDED  U1.a | log x")
        self.assertIn("landing: LANDED", out)   # the build reached the lander, not "nothing to land"
        self.assertEqual(rc, 0, out)


class RepairLands(Base):
    """B8: the repair the finisher writes is a build file the lander accepts, end to end."""

    def test_a_finisher_repair_lands_through_the_real_lander(self):
        code = os.path.join(self.root, "code")
        LF.write(os.path.join(code, "scripts", "close_unit.py"), LF.CLOSE_UNIT_STUB)
        f = LF.Fixture(os.path.join(self.root, "f"), code_root=code)
        self.cache(LF.SUB, ["a"])
        with open(f.status, "rb") as fh:
            seen = fh.read()   # the STATUS bytes selection read (salvage.plan_view carries them as status_seen)
        cand = {"sub": LF.SUB, "build": f.build, "run": os.path.dirname(os.path.dirname(os.path.dirname(f.build))),
                "probes": "DIRTY", "applies": "APPLIES", "action": "leave: probes DIRTY", "checker": "none", "status_seen": seen}
        answer = json.dumps({"edits": [{"path": "docs/landed-u1a.txt", "new_file_content": "repaired\n"}], "tests": []})
        run = lambda argv, cwd: (0, "PASS x") if "grade_build" in " ".join(argv) else (0, CLEAN)
        rows, ready, landed = F.finish([cand], "m", lambda m, p: Answer(True, answer), run, {LF.SUB: os.path.join(f.tree, "docs", "spec-u1.md")},
                                       land=f.land, promote=F.salvage.promote)
        self.assertEqual(rows[0][1], "READY", rows)
        rc, out = landed
        self.assertEqual(rc, 0, out)
        self.assertEqual(f.git("show", LF.BRANCH + ":docs/landed-u1a.txt", cwd=f.hub), "repaired\n")
        self.assertEqual([r["builds"] for r in f.rows()], [ready])


CRASHED = (1, "CRASH    none_input TypeError x\nPROBES   1 run: 1 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 0 RETURNED, 0 NO-DATA\n")
SILENT = (0, "Traceback: the probe printed no counts\n")


class RegradeProbes(Base):
    """FX-43, BROTHER_FINISHER_REGRADE_PROBES: off (unset, the owner's 2026-09-27 rule) promotes a REGRADE after its fresh
    grade; crash re-executes the cached sets and holds an executed crash; strict promotes only a clean re-execution; an
    unknown value is NOT SAVED. Every case runs finish(), the entry point every candidate goes through."""
    CAND = {"sub": "U1.a", "build": "/unused/round0/out/build.json", "probes": "NO-DATA", "applies": "APPLIES", "action": "leave"}

    def go(self, mode, probe=(0, CLEAN), sets=("a",), cand=None):
        """probe: one answer for every set, a dict per set name, or a callable. Returns rows, the call order, promoted, landed."""
        if mode is not None:
            os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = mode
        if sets:
            self.cache("U1.a", list(sets))
        else:
            os.environ["BROTHER_PROBE_CACHE"] = os.path.join(self.root, "empty-cache")
        calls, promoted, landed = [], [], []
        def run(argv, cwd):
            name = next((os.path.basename(x) for x in argv if str(x).endswith(".py")), "?")
            calls.append(name)
            if name == "grade_build.py":
                return 0, "PASS x"
            if name == "probe_build.py":
                if callable(probe): return probe(argv)
                return probe[os.path.basename(os.path.dirname(argv[-1]))] if isinstance(probe, dict) else probe
            return 0, ""
        def promote(c):
            calls.append("promote"); promoted.append(c["sub"]); return ""
        rows, ready, _ = F.finish([dict(cand or self.CAND)], "m", lambda *a: self.fail("no model"), run, {},
                                  promote=promote, land=lambda b: landed.extend(b) or "LANDED")
        return rows, calls, promoted, landed

    def test_default_is_todays_rule_no_probe_runs(self):
        rows, calls, promoted, landed = self.go(None, probe=CRASHED)
        self.assertEqual(rows, [("U1.a", "READY", "the probe stage never answered")])
        self.assertNotIn("probe_build.py", calls)
        self.assertEqual((promoted, landed), (["U1.a"], [self.CAND["build"]]))

    def test_regrade_probes_before_it_promotes(self):
        rows, calls, promoted, _ = self.go("crash")
        self.assertEqual(calls, ["grade_build.py", "probe_build.py", "promote"])
        self.assertEqual(rows[0][1], "READY", rows)

    def test_strict_mode_refuses_a_build_a_set_saw_crash(self):
        rows, _, promoted, landed = self.go("strict", probe={"a": (0, CLEAN), "b": CRASHED}, sets=("a", "b"))
        self.assertEqual((rows[0][1], promoted, landed), ("NOT SAVED", [], []), rows)
        self.assertTrue(rows[0][2].startswith("probes after the regrade: DIRTY"), rows)

    def test_strict_mode_holds_a_build_whose_probes_still_never_answered(self):
        for label, kw in (("exit 0, no counts line", dict(probe=SILENT)), ("no cached set", dict(sets=()))):
            with self.subTest(label):
                rows, _, promoted, landed = self.go("strict", **kw)
                self.assertEqual((rows[0][1], promoted, landed), ("NOT SAVED", [], []), rows)
                self.assertTrue(rows[0][2].startswith("probes after the regrade: NO-DATA"), rows)

    def test_crash_mode_names_the_silent_probe_in_its_row(self):
        rows, _, promoted, _ = self.go("crash", probe={"a": (0, CLEAN), "b": SILENT}, sets=("a", "b"))
        self.assertEqual((rows[0][1], promoted), ("READY", ["U1.a"]), rows)
        self.assertIn("gave no readable result", rows[0][2])
        self.assertIn("no answer, so the landing gates decide", rows[0][2])

    def test_regrade_never_asks_probe_wave_for_a_set(self):
        rows, calls, promoted, _ = self.go("crash", sets=())
        self.assertNotIn("probe_wave.py", calls)
        self.assertFalse(os.path.exists(os.path.join(self.root, "empty-cache")), "the regrade wrote the shared cache")
        self.assertEqual((rows[0][1], promoted), ("READY", ["U1.a"]), rows)
        self.assertIn("no cached probe set", rows[0][2])

    def test_an_unknown_switch_value_is_not_saved(self):
        for value in ("banana", "ON", "cr ash"):
            with self.subTest(value):
                rows, calls, promoted, landed = self.go(value)
                self.assertEqual((rows[0][1], promoted, landed), ("NOT SAVED", [], []), rows)
                self.assertIn("BROTHER_FINISHER_REGRADE_PROBES=%s " % value, rows[0][2])
                self.assertNotIn("probe_build.py", calls)
        rows, calls, _, _ = self.go(" crash ")   # outer spaces only: stripped, a valid mode
        self.assertEqual((rows[0][1], "probe_build.py" in calls), ("READY", True), rows)

    def test_an_unknown_reexecution_verdict_is_not_saved(self):
        for mode in ("crash", "strict"):
            with self.subTest(mode), mock.patch.object(F, "reexecute", return_value=("WEIRD", "x")):
                rows, _, promoted, landed = self.go(mode)
                self.assertEqual((rows[0][1], promoted, landed), ("NOT SAVED", [], []), rows)
                self.assertIn("WEIRD", rows[0][2])

    def test_an_unknown_probe_word_is_left(self):
        cand = dict(self.CAND, probes="BANANA")
        self.assertEqual(F.plan_for(cand)[0], "LEAVE")
        rows, calls, promoted, _ = self.go("strict", cand=cand)
        self.assertEqual((rows[0][1], calls, promoted), ("NOT SAVED", [], []), rows)

    def test_a_clean_promote_never_runs_a_probe(self):
        cand = dict(self.CAND, probes="CLEAN", action="PROMOTE")
        rows, calls, promoted, _ = self.go("strict", probe=lambda argv: self.fail("a PROMOTE ran a probe"), cand=cand)
        self.assertEqual((rows[0][1], calls, promoted), ("READY", ["grade_build.py", "promote"], ["U1.a"]), rows)

    def test_no_candidates_lands_nothing(self):
        os.environ["BROTHER_FINISHER_REGRADE_PROBES"] = "strict"
        self.assertEqual(F.finish([], "m", None, lambda *a: self.fail("no run"), {}, promote=lambda c: self.fail("no promote"),
                                  land=lambda b: self.fail("no landing")), ([], [], ""))

    def test_a_rerun_probes_again_and_decides_the_same(self):
        first = self.go("crash", probe=CRASHED)
        second = self.go("crash", probe=CRASHED)
        self.assertEqual(first[0], second[0])
        self.assertEqual((first[1], second[1]), (["grade_build.py", "probe_build.py"],) * 2)
        self.assertEqual(first[0][0][1], "NOT SAVED")


class BoundedRunner(Base):
    """FX-43.2: the finisher's child runner turns a hang or a failed start into an exit code the grade and probe rules read."""

    def test_a_probe_that_hangs_is_a_row_not_a_crash(self):
        hang = [sys.executable, "-c", "import time; time.sleep(30)"]
        rc, out = F.run_cmd(hang, None, timeout=1)
        self.assertEqual(rc, 124, out)
        self.assertTrue(out.startswith("TIMEOUT after 1 s"), out)
        # at the entry point: the first candidate's grade hangs, the pass goes on to the second
        seen = []
        def run(argv, cwd):
            seen.append(argv[-1])
            return F.run_cmd(hang, None, timeout=1) if argv[-1] == "/unused/a.json" else (0, "PASS x")
        cands = [dict(RegradeProbes.CAND, sub=s, build="/unused/%s.json" % s) for s in ("a", "b")]
        rows, ready, _ = F.finish(cands, "m", None, run, {}, promote=lambda c: "", land=lambda b: "LANDED")
        self.assertEqual([r[1] for r in rows], ["NOT SAVED", "READY"], rows)
        self.assertIn("TIMEOUT", rows[0][2])

    def test_a_command_that_cannot_start_is_127(self):
        rc, out = F.run_cmd([os.path.join(self.root, "no-such-binary")], None)
        self.assertEqual(rc, 127, out)
        self.assertTrue(out.startswith("NO-DATA: could not start"), out)

    def test_a_child_printing_undecodable_bytes_is_read_not_raised(self):
        rc, out = F.run_cmd([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\nPASS x\\n')"], None)
        self.assertEqual((rc, out.splitlines()[-1]), (0, "PASS x"), out)

    def test_main_passes_the_bounded_runner(self):
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write('{"units": []}')
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(F.salvage, "PLAN", plan), mock.patch.object(F, "candidates_of", return_value=[]), \
                mock.patch.object(loop_hold, "gate"), mock.patch.object(F.subprocess, "run", return_value=ok), \
                mock.patch.object(F, "finish", return_value=([], [], "")) as fin, \
                mock.patch.object(sys, "argv", ["finish_run.py", "--model", "unused"]), \
                mock.patch("builtins.print"):
            self.assertEqual(F.main(), 0)
        self.assertIs(fin.call_args[0][3], F.run_cmd)


if __name__ == "__main__":
    unittest.main()

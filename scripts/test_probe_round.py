#!/usr/bin/env python3
"""probe_round's own suite. Every test drives the pure functions with fixtures, so it passes in an export copy
with an empty HOME and never reads the live runs folder or dispatches anything."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_round as P  # noqa: E402


class SilenceIsNotAPass(unittest.TestCase):
    """The rule that matters: a lane nobody probed is never promoted."""

    def test_no_adversary_answered_is_no_data(self):
        self.assertEqual(P.verdict([]), "NO-DATA")

    def test_only_absent_results_are_no_data(self):
        self.assertEqual(P.verdict([None, None]), "NO-DATA")

    def test_one_clean_adversary_is_clean(self):
        self.assertEqual(P.verdict([(0, "PROBES 40 run: 0 CRASH")]), "CLEAN")

    def test_a_crash_makes_it_dirty(self):
        self.assertEqual(P.verdict([(1, "PROBES 40 run: 3 CRASH")]), "DIRTY")

    def test_one_lane_that_found_defects_is_never_overruled(self):
        """A council seat proved the opposite rule wrong: an adversary finding three crashes was overruled
        by one that happened to find nothing, and the build was promoted."""
        self.assertEqual(P.verdict([(1, "3 CRASH"), (0, "clean")]), "DIRTY")
        self.assertEqual(P.verdict([(0, "clean"), (1, "3 CRASH")]), "DIRTY")

    def test_a_silent_lane_blocks_promotion(self):
        """Silence became consent: the lane that would find the defect could just time out."""
        self.assertEqual(P.verdict([(0, "clean")], expected=2), "NO-DATA")

    def test_every_dispatched_lane_answering_clean_is_clean(self):
        self.assertEqual(P.verdict([(0, "a"), (0, "b")], expected=2), "CLEAN")

    def test_a_silent_lane_cannot_hide_a_dirty_one(self):
        self.assertEqual(P.verdict([(1, "3 CRASH")], expected=2), "DIRTY")

    def test_every_answered_lane_clean_is_clean(self):
        self.assertEqual(P.verdict([(0, "a"), (0, "b")]), "CLEAN")

    def test_every_lane_failing_is_dirty(self):
        self.assertEqual(P.verdict([(1, "x"), (2, "y")]), "DIRTY")

    def test_a_probe_that_never_ran_is_no_evidence_either_way(self):
        # X2 finding 6: probe_build exits 2 when no probe ran; that, and any exit but 0 or 1, is an unanswered lane
        self.assertEqual(P.verdict([(2, ""), (2, "")], expected=2), "NO-DATA")
        self.assertEqual(P.verdict([(0, "clean"), (2, "")], expected=2), "NO-DATA")
        self.assertEqual(P.verdict([(0, "clean"), (3, "")]), "NO-DATA")
        self.assertEqual(P.verdict([(None, "timed out")]), "NO-DATA")


class OnlyTheRightLanes(unittest.TestCase):
    def setUp(self):
        self.texts = {}

    def read(self, d):
        return self.texts.get(d, "")

    def test_a_ready_unprobed_lane_is_picked(self):
        self.texts = {"/r/A-100000/": "READY-UNPROBED /b/a.json"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {"A": ("/r/A-100000/", "/b/a.json")})

    def test_an_already_ready_lane_is_not_reprobed(self):
        self.texts = {"/r/A-100000/": "READY /b/a.json"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {})

    def test_a_landed_sub_unit_is_skipped(self):
        self.texts = {"/r/A-100000/": "READY-UNPROBED /b/a.json"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, {"A"}), {})

    def test_the_newest_run_decides(self):
        self.texts = {"/r/A-100000/": "READY-UNPROBED /b/old.json", "/r/A-200000/": "EXHAUSTED nothing"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {})

    def test_a_status_with_no_build_path_is_skipped(self):
        self.texts = {"/r/A-100000/": "READY-UNPROBED"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {})

    def test_a_folder_without_a_stamp_is_ignored(self):
        self.texts = {"/r/junk/": "READY-UNPROBED /b/a.json"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {})

    def test_a_quarantined_lane_is_never_probed(self):
        self.texts = {"/r/A-100000/": "QUARANTINE dropped at landing"}
        self.assertEqual(P.unprobed_builds(list(self.texts), self.read, set()), {})


def _status(tmp, text):
    """A STATUS inside a run folder named <sub>-<HHMMSS> under a runs root, as the writer requires; returns its path."""
    run = os.path.join(tmp, "runs", "A.1-100000")
    os.makedirs(run)
    p = os.path.join(run, "STATUS")
    open(p, "w").write(text)
    return p


class PromotionIsGuarded(unittest.TestCase):
    """promote returns '' when it wrote and the reason when it did not (the salvage.promote convention)."""

    def test_a_ready_unprobed_status_is_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json (round 0)\n")
            self.assertEqual(P.promote(p, "/b/a.json", "probes clean", open(p, "rb").read()), "")
            self.assertTrue(open(p).read().startswith("READY /b/a.json"))

    def test_a_status_that_moved_on_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json (round 0)\n")
            seen = open(p, "rb").read()
            open(p, "w").write("QUARANTINE dropped at landing\n")
            self.assertIn("changed since the probe decision", P.promote(p, "/b/a.json", "probes clean", seen))
            self.assertEqual(open(p).read(), "QUARANTINE dropped at landing\n")

    def test_a_decision_with_no_snapshot_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json\n")
            self.assertTrue(P.promote(p, "/b/a.json", "x", None))
            self.assertEqual(open(p).read(), "READY-UNPROBED /b/a.json\n")

    def test_an_unchanged_status_that_was_not_ready_unprobed_is_never_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "QUARANTINE dropped at landing\n")
            self.assertTrue(P.promote(p, "/b/a.json", "x", open(p, "rb").read()))
            self.assertTrue(P.mark_dirty(p, "/b/a.json", "x", open(p, "rb").read()))
            self.assertEqual(open(p).read(), "QUARANTINE dropped at landing\n")

    def test_a_status_outside_a_named_run_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "runs", "not-a-run"))
            p = os.path.join(tmp, "runs", "not-a-run", "STATUS")
            open(p, "w").write("READY-UNPROBED /b/a.json\n")
            self.assertIn("not named", P.promote(p, "/b/a.json", "x", open(p, "rb").read()))
            self.assertEqual(open(p).read(), "READY-UNPROBED /b/a.json\n")

    def test_a_missing_status_is_not_promoted(self):
        self.assertTrue(P.promote("/no/such/A.1-100000/STATUS", "/b/a.json", "x", b"READY-UNPROBED /b/a.json\n"))


class LandedReading(unittest.TestCase):
    def test_landed_sub_units_are_read_from_the_evidence(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1", "U.2"], "evidence": "U.1 landed today"}]}
        self.assertEqual(P.landed_subs(plan), {"U.1"})

    def test_a_full_stop_breaks_the_match(self):
        plan = {"units": [{"id": "U", "sub_units": ["U.1"], "evidence": "U.1 spec. landed"}]}
        self.assertEqual(P.landed_subs(plan), set())



class ADirtyLaneBecomesARebuild(unittest.TestCase):
    """The livelock fix: a lane whose probes found defects must stop being re-probed forever."""

    def test_a_dirty_lane_is_marked_for_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json (round 0)\n")
            self.assertEqual(P.mark_dirty(p, "/b/a.json", "3 CRASH", open(p, "rb").read()), "")
            self.assertTrue(open(p).read().startswith("DIRTY /b/a.json"))

    def test_a_dirty_lane_is_not_probed_again(self):
        texts = {"/r/A-100000/": "DIRTY /b/a.json executed probes found defects: 3 CRASH"}
        self.assertEqual(P.unprobed_builds(list(texts), lambda d: texts[d], set()), {})

    def test_dirty_is_not_quarantine_so_a_rebuild_can_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json\n")
            P.mark_dirty(p, "/b/a.json", "x", open(p, "rb").read())
            self.assertNotIn("QUARANTINE", open(p).read())

    def test_a_status_that_moved_on_is_not_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _status(tmp, "READY-UNPROBED /b/a.json\n")
            seen = open(p, "rb").read()
            open(p, "w").write("QUARANTINE dropped at landing\n")
            self.assertTrue(P.mark_dirty(p, "/b/a.json", "x", seen))
            self.assertEqual(open(p).read(), "QUARANTINE dropped at landing\n")


class ProbeJobsAreLabelled(unittest.TestCase):
    def test_every_probe_job_carries_a_public_sensitivity_label(self):
        # or_fanout treats a missing label as PRIVATE and refuses the job to a third party model; measured
        # 2026-09-22: every re-probe job was refused for 7 seconds of "work" and nothing was promoted.
        import probe_round as PR
        j = PR.probe_job("D3.4", "a", "deepseek", "/tmp/brief.md", "/tmp/out")
        self.assertEqual(j["sensitivity"], "public")
        self.assertEqual(j["id"], "probe-D3.4-a")
        self.assertTrue(j["out"].endswith("/out/D3.4-a.json"))

    def test_a_partial_fan_out_is_judged_on_who_answered_and_an_empty_one_is_no_data(self):
        import probe_round as PR
        jobs = [PR.probe_job("D3.4", "a", "deepseek", "/b", "/o"), PR.probe_job("D3.4", "b", "muse", "/b", "/o")]
        self.assertEqual(PR.round_answered(jobs, isfile=lambda p: p.endswith("-a.json")), ["probe-D3.4-a"])
        self.assertEqual(PR.round_answered(jobs, isfile=lambda p: False), [])

PROBE_STUB = r'''import os, sys
open(os.environ["PROBE_CALLS"], "a").write("call\n")
st = os.environ["PROBE_STATUS"]
act = os.environ.get("PROBE_ACTION", "")
if act == "rewrite":      # a regrade names another build while this probe runs: still READY-UNPROBED, not the same bytes
    open(st + ".w", "w").write("READY-UNPROBED /elsewhere/X.1-r1-build.json\n"); os.replace(st + ".w", st)
elif act == "newer":      # a newer run of the same sub unit starts while this probe runs
    d = os.path.join(os.path.dirname(os.path.dirname(st)), "X.1-020202")
    os.makedirs(d, exist_ok=True); open(os.path.join(d, "STATUS"), "w").write("RUNNING\n")
print("PROBES stand in, exit " + os.environ["PROBE_EXIT"])
sys.exit(int(os.environ["PROBE_EXIT"]))
'''
FANOUT_STUB = r'''import json, sys
for j in json.load(open(sys.argv[1])):
    open(j["out"], "w").write("{}")
'''


class ReprobeAtItsEntryPoint(unittest.TestCase):
    """main() end to end, with stand ins for the brief writer, the fan out and probe_build: the STATUS it leaves.
    X2 finding 3: reprobe read READY-UNPROBED with no lock and wrote with a plain write, so a landing's QUARANTINE
    written under <runs>/.status.lock could be followed by reprobe's stale READY. X2 finding 6: every non zero
    probe_build exit read DIRTY, but exit 2 is NO-DATA (no probe ran): no evidence either way."""

    def setUp(self):
        import shutil, types
        from unittest import mock
        self.tmp = tempfile.mkdtemp(prefix="probe-round-entry-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        self.runs = os.path.join(t, "runs")
        self.run_dir = os.path.join(self.runs, "X.1-010101")
        os.makedirs(self.run_dir)
        self.build = os.path.join(t, "X.1-r0-build.json")
        open(self.build, "w").write("{}")
        self.status = os.path.join(self.run_dir, "STATUS")
        self.seen = "READY-UNPROBED %s\n" % self.build
        open(self.status, "w").write(self.seen)
        plan = os.path.join(t, "plan.json")
        json.dump({"units": [{"id": "X", "spec": "spec.md", "sub_units": ["X.1"], "evidence": ""}]}, open(plan, "w"))
        bind = os.path.join(t, "bin")
        os.makedirs(bind)
        open(os.path.join(bind, "probe_brief.py"), "w").write("import sys\nopen(sys.argv[4], 'w').write('a brief')\n")
        open(os.path.join(bind, "probe_build.py"), "w").write(PROBE_STUB)
        code = os.path.join(t, "code")
        core = os.path.join(code, "plugin", "runtime", "brother", "core")
        os.makedirs(core)
        for d in ("plugin", "plugin/runtime", "plugin/runtime/brother", "plugin/runtime/brother/core"):
            open(os.path.join(code, d, "__init__.py"), "w").close()
        open(os.path.join(core, "or_fanout.py"), "w").write(FANOUT_STUB)
        self.calls = os.path.join(t, "probe-calls")
        for name, value in (("RUNS", self.runs), ("BIN", bind), ("PLAN", plan)):
            p = mock.patch.object(P, name, value); p.start(); self.addCleanup(p.stop)
        router = types.SimpleNamespace(code_root=lambda: code, Refused=type("Refused", (Exception,), {}))
        p = mock.patch.dict(sys.modules, {"model_router": router,
                                          "grade_build": types.SimpleNamespace(private_hits=lambda text: [])})
        p.start(); self.addCleanup(p.stop)

    def main(self, probe_exit, action=""):
        import contextlib, io
        from unittest import mock
        env = {"PROBE_EXIT": str(probe_exit), "PROBE_ACTION": action, "PROBE_CALLS": self.calls, "PROBE_STATUS": self.status}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            rc = P.main(["--out", os.path.join(self.tmp, "out")])
        return rc, out.getvalue()

    def word(self):
        return open(self.status).read().split()[0]

    # ---- finding 6: the probe runner's exit codes
    def test_exit_2_from_every_probe_is_no_data_and_status_stands(self):
        rc, out = self.main(2)
        self.assertEqual(open(self.status).read(), self.seen, out)
        self.assertIn("NO-DATA", out)

    def test_an_exit_outside_0_1_2_is_no_data_too(self):
        rc, out = self.main(3)
        self.assertEqual(open(self.status).read(), self.seen, out)

    def test_a_probe_that_times_out_is_no_data(self):
        """A probe_build run that outlives its bound gives no exit to read: no evidence either way, never a crash."""
        import subprocess
        from unittest import mock
        real = subprocess.run

        def run(argv, *a, **kw):
            if any(str(x).endswith("probe_build.py") for x in argv):
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
            return real(argv, *a, **kw)
        with mock.patch.object(P.subprocess, "run", run):
            rc, out = self.main(0)
        self.assertEqual(open(self.status).read(), self.seen, out)
        self.assertIn("NO-DATA", out)

    def test_nothing_to_probe_exits_1_and_writes_nothing(self):
        open(self.status, "w").write("READY %s (grader PASS; probes clean)\n" % self.build)
        rc, out = self.main(0)
        self.assertEqual(rc, 1, out)
        self.assertIn("nothing to probe", out)
        self.assertFalse(os.path.isfile(self.calls), out)

    def test_exit_1_is_still_dirty(self):
        rc, out = self.main(1)
        self.assertEqual(self.word(), "DIRTY", out)

    def test_exit_0_is_still_clean_and_promoted(self):
        rc, out = self.main(0)
        self.assertEqual((rc, self.word()), (0, "READY"), out)

    # ---- finding 3: the STATUS write takes the shared lock and compares what the decision read
    def test_a_landing_holding_the_status_lock_is_waited_for_and_its_word_stands(self):
        import fcntl, threading, time
        lock = open(os.path.join(self.runs, ".status.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX)              # a landing is writing its word
        self.addCleanup(lock.close)
        got = {}
        th = threading.Thread(target=lambda: got.update(zip(("rc", "out"), self.main(0))), daemon=True)
        th.start()
        end = time.time() + 30
        while time.time() < end and not (os.path.isfile(self.calls) and len(open(self.calls).read().split()) == 2):
            time.sleep(0.05)
        th.join(1.5)                                   # both probes ran clean: an unlocked write lands here
        self.assertEqual(open(self.status).read(), self.seen, "reprobe wrote STATUS while the landing held the lock")
        open(self.status + ".tmp", "w").write("QUARANTINE dropped at landing, fuzz crashes 1\n")
        os.replace(self.status + ".tmp", self.status)
        fcntl.flock(lock, fcntl.LOCK_UN)
        th.join(30)
        self.assertFalse(th.is_alive())
        self.assertEqual(self.word(), "QUARANTINE", got.get("out"))
        self.assertIn("REFUSED", got.get("out", ""))

    def test_a_status_rewritten_during_the_probe_is_never_overwritten(self):
        """The compare is on the exact bytes the decision read, never on the first word: a regrade naming another build
        is still READY-UNPROBED, and promoting it would name a build nobody probed."""
        rc, out = self.main(0, action="rewrite")
        self.assertEqual(open(self.status).read(), "READY-UNPROBED /elsewhere/X.1-r1-build.json\n", out)
        self.assertIn("REFUSED", out)

    def test_a_newer_run_of_the_sub_unit_keeps_speaking_for_it(self):
        """The atomic replace moves the run folder's mtime and readers take the NEWEST run: a write into the older run
        once a newer one exists would make the older build speak for the sub unit again."""
        rc, out = self.main(0, action="newer")
        newest = max((d for d in os.listdir(self.runs) if d.startswith("X.1-")),
                     key=lambda d: (os.path.getmtime(os.path.join(self.runs, d)), d))
        self.assertEqual(newest, "X.1-020202", out)
        self.assertEqual(open(self.status).read(), self.seen, out)

    def test_a_promotion_replaces_status_atomically(self):
        """A reader sees the old STATUS or the new one, never a torn one: the write is a new file renamed into place."""
        before = os.stat(self.status).st_ino
        self.main(0)
        self.assertEqual(self.word(), "READY")
        self.assertNotEqual(os.stat(self.status).st_ino, before)
        self.assertEqual([n for n in os.listdir(self.run_dir) if n != "STATUS"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)

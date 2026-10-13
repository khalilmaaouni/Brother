#!/usr/bin/env python3
"""CD1 (docs/plan/specs/CD1.md): the cut driver's phases, state, resume, wait
and lock, driven through a fake runner and throwaway git repositories. Nothing
here runs a real cut, rehearsal, export, push, tag or gh call.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

V = "9.9.9"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
REH_READY = (0, "...\nREHEARSAL READY %s abc exit 0 record /ev/rehearsal-%s-abc-1.json\n" % (V, V))
GREEN = [("close_ceremony_check.py", (0, "PASS ceremony\n")),
         ("cut_preflight.py", (0, "CLEAR  x\ncut-preflight: CLEAR\n")),
         ("cut_rehearsal.py", REH_READY)]


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


class Runner(object):
    """Answers by the first needle found in the joined argv; git falls through to the real git."""

    def __init__(self, handlers=()):
        self.handlers = list(handlers)
        self.calls = []

    def __call__(self, cmd, cwd, stream=False):
        self.calls.append(list(cmd))
        line = " ".join(cmd)
        for needle, result in self.handlers:
            if needle in line:
                return result(cmd, cwd) if callable(result) else result
        if cmd[0] == "git":
            return D.real_runner(cmd, cwd)
        return 127, "unhandled %s" % line

    def saw(self, needle):
        return [c for c in self.calls if needle in " ".join(c)]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-driver-")
        self.ev = os.path.join(self.tmp, "ev")
        env = dict(GIT_ENV, BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev,
                   BROTHER_SCRATCH=os.path.join(self.tmp, "scratch"))
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cover = mock.patch.object(CR, "covering_record", return_value=("", "REFUSED: no record"))
        self.cover = cover.start()
        self.addCleanup(cover.stop)
        self.root = os.path.join(self.tmp, "repo")
        hub = os.path.join(self.tmp, "hub.git")
        git(self.tmp, "init", "-q", "-b", "main", self.root)
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.root, "add", "a.txt")
        git(self.root, "commit", "-q", "-m", "candidate")
        git(self.tmp, "init", "-q", "--bare", hub)
        git(self.root, "remote", "add", "hub", hub)
        git(self.root, "push", "-q", "hub", "main")
        self.cand = git(self.root, "rev-parse", "HEAD")
        self.tree = git(self.root, "rev-parse", "HEAD^{tree}")
        self.body = os.path.join(self.tmp, "BODY.md")
        with open(self.body, "w") as fh:
            fh.write("notes\n")

    def drive(self, handlers=GREEN, **kw):
        runner = Runner(handlers)
        out = io.StringIO()
        code = D.run(self.root, V, kw.pop("candidate", ""), self.body, kw.pop("release_branch", "main"),
                     runner=runner, out=out, **kw)
        return code, out.getvalue().splitlines(), runner

    def state(self, phases=None, **over):
        st = D.new_state(V, self.cand, self.tree, "main", time.time())
        st["phases"] = phases or {}
        st.update(over)
        return st

    def passed(self, *names):
        return dict((n, {"verdict": "PASS", "started": 0, "seconds": 1, "detail": "d", "record": "",
                         "head_after": self.cand}) for n in names)


class ThePhaseLinesAndTheStop(Fixture):
    def test_a_failing_preflight_prints_two_lines_and_stops(self):
        handlers = [("cut_preflight.py", (1, "REFUSED  plugin  bump missing\ncut-preflight: REFUSED\n"))] + GREEN
        code, lines, runner = self.drive(handlers)
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("PHASE freeze PASS "), lines)
        self.assertTrue(lines[1].startswith("PHASE preflight FAIL "), lines)
        self.assertIn("REFUSED  plugin  bump missing", lines[1])
        self.assertEqual(runner.saw("cut_rehearsal.py"), [])

    def test_a_no_data_preflight_exits_2(self):
        code, lines, runner = self.drive([("cut_preflight.py", (2, "NO-DATA  x  y\n"))] + GREEN)
        self.assertEqual(code, 2)
        self.assertTrue(lines[-1].startswith("PHASE preflight NO-DATA "))

    def test_the_phase_line_shape(self):
        self.assertEqual(D.phase_line("freeze", "PASS", 3.4, "ok"), "PHASE freeze PASS 3s ok")

    def test_a_dirty_tree_fails_the_freeze(self):
        with open(os.path.join(self.root, "b.txt"), "w") as fh:
            fh.write("b\n")
        code, lines, runner = self.drive()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("PHASE freeze FAIL "))
        self.assertEqual(runner.saw("cut_preflight.py"), [])

    def test_head_not_on_the_hub_fails_the_freeze(self):
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("changed\n")
        git(self.root, "commit", "-q", "-am", "local only")
        code, lines, _ = self.drive()
        self.assertEqual(code, 1)
        self.assertIn("is not hub/main", lines[0])

    def test_an_unreadable_hub_or_ceremony_is_no_data(self):
        code, lines, _ = self.drive(hub_remote="nowhere")
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("PHASE freeze NO-DATA "))
        code, lines, _ = self.drive([("close_ceremony_check.py", (2, "NO-DATA no pack\n"))] + GREEN)
        self.assertEqual(code, 2)
        self.assertTrue(lines[0].startswith("PHASE freeze NO-DATA "))

    def test_the_rehearsal_line_maps_to_the_verdict(self):
        for said, rc, want in (("READY", 0, "PASS"), ("NOT-READY", 1, "FAIL"), ("NO-DATA", 2, "NO-DATA"),
                               ("READY", 1, "NO-DATA")):
            out = (rc, "REHEARSAL %s %s abc exit %d record /ev/r.json\n" % (said, V, rc))
            verdict, detail, record = D.rehearsal_phase(self.root, V, self.cand, 60, Runner([("cut_rehearsal", out)]))
            self.assertEqual(verdict, want, said)
            self.assertEqual(record, "/ev/r.json")
        verdict, _d, record = D.rehearsal_phase(self.root, V, self.cand, 60, Runner([("cut_rehearsal", (0, "hi"))]))
        self.assertEqual((verdict, record), ("NO-DATA", ""))

    def test_a_green_run_records_the_rehearsal_and_stops_at_an_unreadable_record(self):
        code, lines, _ = self.drive()
        self.assertEqual([ln.split()[1:3] for ln in lines],
                         [["freeze", "PASS"], ["preflight", "PASS"], ["rehearsal", "PASS"], ["adopt", "NO-DATA"]])
        self.assertEqual(code, 2)
        with open(D.state_path(V, self.cand)) as fh:
            st = json.load(fh)
        self.assertEqual(st["phases"]["rehearsal"]["record"], "/ev/rehearsal-%s-abc-1.json" % V)
        self.assertEqual(st["schema"], D.SCHEMA)


class TheStateIsResumedOnlyForThisCandidate(Fixture):
    def test_four_states_are_not_resumed(self):
        now = time.time()
        path = D.state_path(V, self.cand)
        cases = (("other candidate", self.state(candidate_sha="f" * 40)),
                 ("other tree", self.state(candidate_tree="f" * 40)),
                 ("old start", self.state(started_at=now - 25 * 3600)))
        for label, st in cases:
            D.save_state(path, st)
            got, why = D.load_state(path, self.cand, self.tree, now)
            self.assertIsNone(got, label)
            self.assertIn("start over", why, label)
        with open(path, "w") as fh:
            fh.write("{trunc")
        got, why = D.load_state(path, self.cand, self.tree, now)
        self.assertIsNone(got)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(len([n for n in os.listdir(self.ev) if ".corrupt-" in n]), 1)
        D.save_state(path, self.state())
        got, why = D.load_state(path, self.cand, self.tree, now)
        self.assertEqual((got["candidate_sha"], why), (self.cand, ""))

    def test_a_state_for_another_tree_starts_over(self):
        D.save_state(D.state_path(V, self.cand), self.state(self.passed("freeze"), candidate_tree="f" * 40))
        code, lines, runner = self.drive()
        self.assertIn("start over", lines[0])
        self.assertTrue(runner.saw("close_ceremony_check.py"))

    def test_resume_starts_at_the_first_phase_without_pass(self):
        D.save_state(D.state_path(V, self.cand), self.state(self.passed("freeze")))
        code, lines, runner = self.drive()
        self.assertEqual(runner.saw("close_ceremony_check.py"), [])
        self.assertEqual(runner.saw("ls-remote"), [])
        self.assertTrue(lines[0].startswith("PHASE preflight PASS"), lines)

    def test_next_phase(self):
        self.assertEqual(D.next_phase(self.state()), "freeze")
        st = self.state(self.passed("freeze"))
        st["phases"]["preflight"] = {"verdict": "FAIL"}
        self.assertEqual(D.next_phase(st), "preflight")
        st = self.state(dict((n, {"verdict": "REUSED"}) for n in D.PHASES))
        self.assertIsNone(D.next_phase(st))

    def test_all_six_done_is_already_complete_and_runs_nothing(self):
        D.save_state(D.state_path(V, self.cand), self.state(self.passed(*D.PHASES)))
        code, lines, runner = self.drive()
        self.assertEqual(code, 0)
        self.assertIn("already complete", lines[0])
        self.assertEqual([c for c in runner.calls if c[0] != "git"], [])

    def test_resume_without_a_state_exits_2(self):
        code, lines, runner = self.drive(require_state=True)
        self.assertEqual(code, 2)
        self.assertEqual([c for c in runner.calls if c[0] != "git"], [])


class TheYIsNeverPassed(Fixture):
    def test_yes_and_an_answer_file_are_refused_before_anything_runs(self):
        for extra in (["--yes"], ["--answer-file", "/x"], ["--y"]):
            runner = Runner(GREEN)
            argv = ["run", "--version", V, "--body", self.body] + extra
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(D.main(argv, root=self.root, runner=runner), 2, extra)
            self.assertEqual(runner.calls, [], extra)


class TheRehearsalIsReused(Fixture):
    def test_a_record_covering_head_is_reused_not_rerun(self):
        self.cover.return_value = ("/ev/rehearsal-9.9.9-x-1.json", "")
        runner = Runner(GREEN)
        verdict, detail, record = D.rehearsal_phase(self.root, V, self.cand, 60, runner)
        self.assertEqual((verdict, record), ("REUSED", "/ev/rehearsal-9.9.9-x-1.json"))
        self.assertEqual(runner.saw("cut_rehearsal.py"), [])


class TheLock(Fixture):
    def test_a_live_lock_refuses_and_a_dead_one_is_replaced(self):
        lock = D.state_path(V, self.cand)[:-5] + ".lock"
        os.makedirs(self.ev, exist_ok=True)
        with open(lock, "w") as fh:
            fh.write("%d\n" % os.getppid())
        code, lines, runner = self.drive()
        self.assertEqual(code, 2)
        self.assertIn("another driver", lines[0])
        self.assertEqual([c for c in runner.calls if c[0] != "git"], [])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with open(lock, "w") as fh:
            fh.write("%d\n" % dead.pid)
        code, lines, _ = self.drive()
        self.assertTrue(lines[0].startswith("PHASE freeze PASS"), lines)
        self.assertFalse(os.path.exists(lock))


class TheWait(Fixture):
    def put(self, verdict):
        path = D.state_path(V, self.cand)
        st = self.state()
        if verdict:
            st["phases"]["rehearsal"] = {"verdict": verdict, "seconds": 7, "detail": "d"}
        D.save_state(path, st)
        os.utime(path, (1000000000, 1000000000))
        return path

    def test_wait_returns_the_verdict_and_never_writes(self):
        for verdict, want in (("PASS", 0), ("REUSED", 0), ("FAIL", 1), ("NO-DATA", 2)):
            path = self.put(verdict)
            out = io.StringIO()
            code = D.wait(V, "rehearsal", sleep=lambda s: None, out=out)
            self.assertEqual(code, want, verdict)
            self.assertEqual(out.getvalue().strip(), "PHASE rehearsal %s 7s d" % verdict)
            self.assertEqual(os.path.getmtime(path), 1000000000)
            self.assertEqual(sorted(os.listdir(self.ev)), [os.path.basename(path)])

    def test_wait_times_out_with_no_data(self):
        path = self.put("")
        ticks = iter([0, 0] + [10 ** 6] * 5)
        out = io.StringIO()
        base = time.mktime((2026, 10, 12, 0, 0, 0, 0, 0, -1))
        code = D.wait(V, "rehearsal", until="00:30", sleep=lambda s: None, clock=lambda: base + next(ticks), out=out)
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out.getvalue())
        self.assertEqual(os.path.getmtime(path), 1000000000)
        code = D.wait("8.8.8", "rehearsal", until="00:30", sleep=lambda s: None,
                      clock=iter([base, base + 3600]).__next__, out=io.StringIO())
        self.assertEqual(code, 2)

    def test_an_unknown_phase_or_bad_until_is_2(self):
        out = io.StringIO()
        self.assertEqual(D.wait(V, "deploy", out=out), 2)
        self.assertIn("freeze, preflight, rehearsal, adopt, cut, after", out.getvalue())
        self.assertEqual(D.wait(V, "cut", until="25:00", out=io.StringIO()), 2)


BUMP = "%s: the version bump and the regenerated manifests" % V
NOTE = "%s: the export manifest that describes the tree and the note it ships" % V
CUT_OK = [("_covering_record", (0, "OK\n")),
          ("scripts/cut.py", (0, "gates...\nDONE: cut %s pushed: a, b\n" % V)),
          ("ls-remote origin", (0, "%s\trefs/tags/v%s\n" % ("1" * 40, V)))]


class TheAdoptAndCutPhases(Fixture):
    def cut_commits(self, *subjects):
        """Commits on top of the candidate, kept reachable by a ref but off main; returns the tip."""
        git(self.root, "checkout", "-q", "--detach")
        for i, subject in enumerate(subjects):
            with open(os.path.join(self.root, "c%d.txt" % i), "w") as fh:
                fh.write(subject)
            git(self.root, "add", "-A")
            git(self.root, "commit", "-q", "-m", subject)
        tip = git(self.root, "rev-parse", "HEAD")
        git(self.root, "update-ref", "refs/rehearsal/tip", tip)
        git(self.root, "checkout", "-q", "main")
        return tip

    def record(self, tip, verdict="READY", lines=None):
        log = os.path.join(self.tmp, "rehearsal.log")
        with open(log, "w") as fh:
            for line in (lines if lines is not None else
                         ["cut_v1.0.0.sh | %s %s" % (tip[:9], BUMP), "cut_v1.0.0.sh | %s %s" % (tip[:9], NOTE)]):
                fh.write(line + "\n")
        return CR.record_rehearsal(V, self.cand, self.tree, 0 if verdict == "READY" else 1, verdict, log, 1.0,
                                   "0" * 64, directory=self.ev,
                                   script_trees=dict((d, "a" * 40) for d in CR.SCRIPT_DIRS))

    def main_at(self):
        return git(self.root, "rev-parse", "refs/heads/main")

    def test_the_tip_is_the_last_cut_line_of_the_log(self):
        text = "x\ncut_v1.0.0.sh | aaaaaaaaa %s\ncut_v1.0.0.sh | bbbbbbbbb %s\n" % (BUMP, NOTE)
        self.assertEqual(D.tip_from_log(text, V), ("bbbbbbbbb", ""))
        tip, why = D.tip_from_log("nothing here\n", V)
        self.assertEqual(tip, "")
        self.assertIn("no cut commit", why)
        self.assertEqual(D.tip_from_log("cut_v1.0.0.sh | aaaaaaaaa 1.2.3: the version bump and the regenerated "
                                        "manifests\n", V)[0], "")

    def test_four_commit_shapes_and_a_foreign_subject(self):
        r = Runner()
        self.assertIn("not the cut's one or two", D.adopt_problem(self.root, self.cand, self.cand, V, r))
        self.assertEqual(D.adopt_problem(self.root, self.cand, self.cut_commits(BUMP), V, r), "")
        self.assertEqual(D.adopt_problem(self.root, self.cand, self.cut_commits(BUMP, NOTE), V, r), "")
        self.assertIn("not the cut's one or two",
                      D.adopt_problem(self.root, self.cand, self.cut_commits(BUMP, NOTE, NOTE), V, r))
        self.assertIn("not the cut's own", D.adopt_problem(self.root, self.cand, self.cut_commits(BUMP, "hand"), V, r))
        other = self.cut_commits(BUMP)
        git(self.root, "checkout", "-q", "--orphan", "lone")
        git(self.root, "commit", "-q", "-m", "lone")
        lone = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "-f", "main")
        self.assertIn("does not descend", D.adopt_problem(self.root, other, lone, V, r))

    def test_adopt_pins_the_tip_in_a_detached_worktree_and_leaves_the_branch(self):
        tip = self.cut_commits(BUMP, NOTE)
        rec = self.record(tip)
        verdict, detail, got_tip, wt = D.adopt_phase(self.root, V, self.cand, rec, Runner(CUT_OK))
        self.assertEqual((verdict, got_tip), ("PASS", tip), detail)
        self.assertEqual(git(self.root, "rev-parse", "refs/heads/" + D.pin_name(V, self.cand)), tip)
        self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "HEAD")
        self.assertTrue(wt.startswith(os.path.join(self.tmp, "scratch", "cut-wt")))
        self.assertEqual(self.main_at(), self.cand)
        self.assertEqual(D.pin_name(V, self.cand), "rehearsed/%s-%s" % (V, self.cand[:9]))
        again = D.adopt_phase(self.root, V, self.cand, rec, Runner(CUT_OK))
        self.assertEqual(again[0], "REUSED", again[1])
        refused = D.adopt_phase(self.root, V, self.cand, rec, Runner([("_covering_record", (0, "REFUSED: x\n"))]))
        self.assertEqual(refused[0], "FAIL")

    def test_adopt_refuses_a_moved_pin_a_dirty_worktree_and_a_bad_record(self):
        tip = self.cut_commits(BUMP, NOTE)
        rec = self.record(tip)
        git(self.root, "branch", D.pin_name(V, self.cand), self.cand)
        verdict, detail, _t, _w = D.adopt_phase(self.root, V, self.cand, rec, Runner(CUT_OK))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("never moved", detail)
        self.assertEqual(git(self.root, "rev-parse", "refs/heads/" + D.pin_name(V, self.cand)), self.cand)
        git(self.root, "branch", "-D", D.pin_name(V, self.cand))
        _v, _d, _t, wt = D.adopt_phase(self.root, V, self.cand, rec, Runner(CUT_OK))
        with open(os.path.join(wt, "dirt.txt"), "w") as fh:
            fh.write("x")
        verdict, detail, _t, _w = D.adopt_phase(self.root, V, self.cand, rec, Runner(CUT_OK))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("never removes it", detail)
        self.assertTrue(os.path.exists(os.path.join(wt, "dirt.txt")))
        self.assertEqual(D.adopt_phase(self.root, V, self.cand, "/no/such.json", Runner(CUT_OK))[0], "NO-DATA")
        nolines = self.record(tip, lines=["no commit here"])
        self.assertEqual(D.adopt_phase(self.root, V, self.cand, nolines, Runner(CUT_OK))[0], "FAIL")
        notready = self.record(tip, verdict="NOT-READY")
        self.assertEqual(D.adopt_phase(self.root, V, self.cand, notready, Runner(CUT_OK))[0], "FAIL")
        ghost = self.record(tip, lines=["cut_v1.0.0.sh | 123456789 %s" % NOTE])
        self.assertIn("not a commit", D.adopt_phase(self.root, V, self.cand, ghost, Runner(CUT_OK))[1])
        self.assertEqual(self.main_at(), self.cand)

    def test_the_cut_command_is_exactly_its_own_flags(self):
        cmd = D.cut_command("/wt", V, "t" * 40, "/b.md")
        self.assertEqual(cmd, [sys.executable, "-u", "/wt/scripts/cut.py", "--version", V, "--resume", "t" * 40,
                               "--release-notes-file", "/b.md"])
        for word in ("--yes", "--answer-file", "--check", "--force"):
            self.assertNotIn(word, cmd)

    def test_the_cut_runs_only_on_a_terminal(self):
        for tty in (False, None, 1):
            runner = Runner(CUT_OK)
            with mock.patch("sys.stdin") as stdin:
                stdin.isatty.return_value = False
                verdict, detail = D.cut_phase(self.tmp, V, "t" * 40, self.body, runner, tty=tty)
            self.assertEqual(verdict, "NO-DATA", tty)
            self.assertEqual(runner.calls, [], tty)

    def test_a_cut_exiting_0_without_a_public_tag_is_fail(self):
        declined = [("scripts/cut.py", (0, "declined, nothing pushed\n"))] + CUT_OK
        self.assertEqual(D.cut_phase(self.tmp, V, "t" * 40, self.body, Runner(declined), tty=True)[0], "FAIL")
        notag = [("ls-remote origin", (0, ""))] + CUT_OK
        self.assertEqual(D.cut_phase(self.tmp, V, "t" * 40, self.body, Runner(notag), tty=True)[0], "FAIL")
        self.assertEqual(D.cut_phase(self.tmp, V, "t" * 40, self.body, Runner(CUT_OK), tty=True)[0], "PASS")
        three = [("scripts/cut.py", (3, "finish: python3 scripts/cut.py --finish-release x\n"))] + CUT_OK
        verdict, detail = D.cut_phase(self.tmp, V, "t" * 40, self.body, Runner(three), tty=True)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("--finish-release", detail)

    def test_the_branch_never_moves_through_adopt_and_cut_whatever_the_cut_exits(self):
        tip = self.cut_commits(BUMP, NOTE)
        rec = self.record(tip)
        for rc in (0, 1, 3):
            handlers = [("cut_rehearsal.py", (0, "REHEARSAL READY %s x exit 0 record %s\n" % (V, rec))),
                        ("scripts/cut.py", (rc, "DONE: cut %s pushed: a\n" % V))] + CUT_OK + GREEN
            st = self.state(self.passed("freeze", "preflight"))
            D.save_state(D.state_path(V, self.cand), st)
            code, lines, runner = self.drive(handlers, tty=True)
            self.assertEqual(self.main_at(), self.cand, (rc, lines))
            self.assertTrue(any(ln.startswith("PHASE cut ") for ln in lines), lines)
            self.assertEqual(runner.saw("--yes"), [])
            self.assertEqual([c for c in runner.calls if c[:2] == ["git", "merge"]], [])


class TheBranchInvariant(Fixture):
    """CR1.b (the 1.1.2 real cut): the hand wrapper fast forwarded the release
    branch onto the cut's own two commits before the push succeeded, so a
    stopped cut left them on the branch. The driver now checks, before every
    phase and right after the cut, that candidate..release branch is empty
    until the after phase has passed."""

    cut_commits = TheAdoptAndCutPhases.cut_commits
    record = TheAdoptAndCutPhases.record

    def ahead(self, n=2):
        """Moves main n commits past the candidate by a hand fast forward, the 1.1.2 move."""
        tip = self.cut_commits(*(["%s: hand commit %d" % (V, i) for i in range(n)]))
        git(self.root, "merge", "-q", "--ff-only", tip)
        return tip

    # REQ-CR1-09, REQ-CR1-10 and the sentence
    def test_the_invariant_itself(self):
        self.assertEqual(D.branch_invariant(self.root, "main", self.cand, False), "")
        self.ahead(1)
        one = D.branch_invariant(self.root, "main", self.cand, False)
        self.assertIn("1 commit", one)
        self.assertIn("hand commit 0", one)
        self.assertEqual(D.branch_invariant(self.root, "main", self.cand, True), "")
        git(self.root, "reset", "-q", "--hard", self.cand)
        self.ahead(4)
        many = D.branch_invariant(self.root, "main", self.cand, False)
        self.assertIn("4 commit", many)
        self.assertEqual([("hand commit %d" % i) in many for i in range(4)], [False, True, True, True], many)

    def test_an_unresolvable_branch_or_candidate_or_count_is_a_violation(self):
        self.assertIn("does not resolve", D.branch_invariant(self.root, "no-such-branch", self.cand, False))
        self.assertIn("does not resolve", D.branch_invariant(self.root, "", self.cand, False))
        self.assertIn("not a commit", D.branch_invariant(self.root, "main", "f" * 40, False))
        self.assertIn("not a commit", D.branch_invariant(self.root, "main", "", False))
        bad = Runner([("rev-list --count", (0, "lots\n"))])
        self.assertIn("count", D.branch_invariant(self.root, "main", self.cand, False, bad))
        dead = Runner([("rev-list --count", (128, "fatal\n"))])
        self.assertIn("count", D.branch_invariant(self.root, "main", self.cand, False, dead))

    # REQ-CR1-07
    def test_a_hand_fast_forward_between_phases_stops_the_driver_before_the_next(self):
        moved = []

        def preflight_then_move(cmd, cwd):
            moved.append(self.ahead(2))
            return 0, "cut-preflight: CLEAR\n"
        handlers = [("cut_preflight.py", preflight_then_move)] + GREEN
        code, lines, runner = self.drive(handlers)
        self.assertEqual(code, 2, lines)
        self.assertTrue(lines[-1].startswith("PHASE rehearsal NO-DATA "), lines)
        self.assertIn("the release branch moved", lines[-1])
        self.assertIn("2 commit", lines[-1])
        self.assertEqual(runner.saw("cut_rehearsal.py"), [], "a phase ran after the branch moved")
        st, _why = D.load_state(D.state_path(V, self.cand), self.cand, self.tree, time.time())
        self.assertEqual(st["phases"]["rehearsal"]["verdict"], "NO-DATA")
        # resumed for the same candidate: still refused before the rehearsal, never run
        code, lines, runner = self.drive(GREEN, candidate=self.cand)
        self.assertEqual(code, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("PHASE rehearsal NO-DATA "), lines)
        self.assertEqual(runner.saw("cut_rehearsal.py"), [])

    def test_a_branch_moved_during_the_cut_is_caught_before_the_after(self):
        tip = self.cut_commits(BUMP, NOTE)
        rec = self.record(tip)
        for rc in (0, 1, 3):
            git(self.root, "reset", "-q", "--hard", self.cand)

            def cut_and_move(cmd, cwd, rc=rc):
                git(self.root, "merge", "-q", "--ff-only", tip)
                return rc, "DONE: cut %s pushed: a\n" % V
            handlers = [("cut_rehearsal.py", (0, "REHEARSAL READY %s x exit 0 record %s\n" % (V, rec))),
                        ("scripts/cut.py", cut_and_move)] + CUT_OK + GREEN
            D.save_state(D.state_path(V, self.cand), self.state(self.passed("freeze", "preflight")))
            code, lines, runner = self.drive(handlers, tty=True)
            self.assertEqual(code, 2, (rc, lines))
            self.assertTrue(any(ln.startswith("PHASE cut ") for ln in lines), lines)
            self.assertTrue(lines[-1].startswith("PHASE after NO-DATA "), (rc, lines))
            self.assertIn("the release branch moved", lines[-1])
            self.assertEqual(runner.saw("post_publish_verify"), [], (rc, lines))

    # REQ-CR1-08
    def test_a_cut_that_stops_after_the_y_leaves_the_branch_at_the_candidate(self):
        tip = self.cut_commits(BUMP, NOTE)
        rec = self.record(tip)
        for rc, out in ((1, "push refused\n"), (3, "finish: python3 scripts/cut.py --finish-release x\n"),
                        (0, "DONE: cut %s pushed: a\n" % V)):
            handlers = [("cut_rehearsal.py", (0, "REHEARSAL READY %s x exit 0 record %s\n" % (V, rec))),
                        ("scripts/cut.py", (rc, out)), ("ls-remote origin", (0, ""))] + CUT_OK + GREEN
            D.save_state(D.state_path(V, self.cand), self.state(self.passed("freeze", "preflight")))
            code, lines, runner = self.drive(handlers, tty=True)
            self.assertEqual(code, 1, (rc, lines))
            self.assertTrue(lines[-1].startswith("PHASE cut FAIL "), (rc, lines))
            self.assertEqual(git(self.root, "rev-list", "--count", "%s..main" % self.cand), "0", rc)


class Clock(object):
    """A clock that only sleep moves."""

    def __init__(self, now=1760000000.0):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


VERIFY_OK = ("post_publish_verify.sh", (0, "PASS    tag\nPASS    main\nfails=0 nodata=0\n"))


def gh(clock, created_offset=5, conclusion="success", finishes=True):
    """Fake gh: the dispatch, a run list whose run was created `created_offset` seconds after the dispatch, and a
    run view that completes with `conclusion` after one poll."""
    seen = {"views": 0, "dispatch": None}

    def answer(cmd, cwd):
        if cmd[1:3] == ["workflow", "run"]:
            seen["dispatch"] = clock()
            return 0, ""
        if cmd[1:3] == ["run", "list"]:
            runs = [{"databaseId": 1, "createdAt": stamp(seen["dispatch"] - 3600), "status": "completed",
                     "conclusion": "success", "headSha": "a"}]
            if created_offset is not None:
                runs.insert(0, {"databaseId": 2, "createdAt": stamp(seen["dispatch"] + created_offset),
                                "status": "queued", "conclusion": "", "headSha": "a"})
            return 0, json.dumps(runs)
        if cmd[1:3] == ["run", "view"]:
            seen["views"] += 1
            if cmd[3] == "1":
                return 0, json.dumps({"status": "completed", "conclusion": "success"})
            done = finishes and seen["views"] > 1
            return 0, json.dumps({"status": "completed" if done else "in_progress",
                                  "conclusion": conclusion if done else ""})
        return 127, "unhandled gh"
    return answer


class TheAfterPhase(Fixture):
    def setUp(self):
        Fixture.setUp(self)
        git(self.root, "checkout", "-q", "-b", "pinned")
        with open(os.path.join(self.root, "n.txt"), "w") as fh:
            fh.write("note\n")
        git(self.root, "add", "n.txt")
        git(self.root, "commit", "-q", "-m", NOTE)
        self.tip = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "main")
        git(self.root, "branch", "-m", "pinned", D.pin_name(V, self.cand))
        self.pin = D.pin_name(V, self.cand)
        self.clock = Clock()

    def after(self, handlers, steps=None):
        runner = Runner(handlers)
        got = D.after_phase(self.root, V, self.body, "main", self.pin, runner, sleep=self.clock.sleep,
                            clock=self.clock, steps=steps)
        return got, runner

    def main_at(self):
        return git(self.root, "rev-parse", "refs/heads/main")

    def test_the_verifier_counts_only_a_clean_summary(self):
        cases = (("", 0, "NO-DATA"), ("PASS a\nfails=0 nodata=0\n", 0, "PASS"),
                 ("FAIL a\nfails=1 nodata=0\n", 1, "FAIL"), ("NO-DATA a\nfails=0 nodata=1\n", 2, "NO-DATA"),
                 ("PASS a\n", 0, "NO-DATA"), ("PASS a\nfails=0 nodata=0\n", 1, "FAIL"),
                 ("FAIL a\nPASS b\nfails=0 nodata=0\n", 0, "FAIL"))
        for text, code, want in cases:
            self.assertEqual(D.verify_result(text, code)[0], want, (text, code))
        self.assertEqual(D.verify_command(V, "/b.md"), ["sh", "scripts/post_publish_verify.sh", V, "/b.md"])

    def test_the_install_proof_needs_a_success_created_after_the_dispatch(self):
        c = self.clock
        self.assertEqual(D.install_proof(V, Runner([("gh ", gh(c))]), c.sleep, c)[0], "PASS")
        stale = D.install_proof(V, Runner([("gh ", gh(c, created_offset=None))]), c.sleep, c)
        self.assertEqual(stale[0], "NO-DATA", stale)
        self.assertEqual(D.install_proof(V, Runner([("gh ", gh(c, conclusion="failure"))]), c.sleep, c)[0], "FAIL")
        hung = D.install_proof(V, Runner([("gh ", gh(c, finishes=False))]), c.sleep, c)
        self.assertEqual(hung[0], "NO-DATA")
        self.assertIn("did not finish", hung[1])
        refused = D.install_proof(V, Runner([("gh workflow", (1, "HTTP 403")), ("gh ", gh(c))]), c.sleep, c)
        self.assertEqual(refused[0], "NO-DATA")

    def test_the_branch_moves_only_after_both_proofs(self):
        (v, d), _r = self.after([("post_publish_verify.sh", (1, "FAIL tag\nfails=1 nodata=0\n")), ("gh ", gh(self.clock))])
        self.assertEqual((v, self.main_at()), ("FAIL", self.cand), d)
        (v, d), _r = self.after([VERIFY_OK, ("gh ", gh(self.clock, conclusion="failure"))])
        self.assertEqual((v, self.main_at()), ("FAIL", self.cand), d)
        (v, d), _r = self.after([("post_publish_verify.sh", (0, "")), ("gh ", gh(self.clock))])
        self.assertEqual((v, self.main_at()), ("NO-DATA", self.cand), d)
        steps = {}
        (v, d), runner = self.after([VERIFY_OK, ("gh ", gh(self.clock))], steps)
        self.assertEqual((v, self.main_at()), ("PASS", self.tip), d)
        self.assertEqual(steps, {"verify": "PASS", "install": "PASS", "fast_forward": "PASS"})
        self.assertIn("git push hub main", d)
        self.assertEqual([c for c in runner.calls if " ".join(c).startswith(("git push", "gh pr"))], [])
        self.assertEqual([c for c in runner.calls if "push" in c or "pr" in c[:2]], [])
        (v, d), _r = self.after([VERIFY_OK, ("gh ", gh(self.clock))])
        self.assertEqual(v, "REUSED", d)

    def test_a_partial_after_repeats_only_what_has_no_pass(self):
        (v, d), runner = self.after([("gh ", gh(self.clock))], steps={"verify": "PASS"})
        self.assertEqual(v, "PASS", d)
        self.assertEqual(runner.saw("post_publish_verify.sh"), [])

    def test_a_diverged_dirty_or_other_branch_checkout_refuses(self):
        green = [VERIFY_OK, ("gh ", gh(self.clock))]
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("dirty\n")
        (v, d), _r = self.after(green)
        self.assertEqual((v, self.main_at()), ("FAIL", self.cand), d)
        git(self.root, "checkout", "-q", "--", "a.txt")
        git(self.root, "checkout", "-q", "-b", "other")
        (v, d), _r = self.after(green)
        self.assertEqual(v, "FAIL", d)
        git(self.root, "checkout", "-q", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "ahead")
        ahead = self.main_at()
        (v, d), _r = self.after(green)
        self.assertEqual((v, self.main_at()), ("FAIL", ahead), d)
        self.assertIn("not an ancestor", d)

    def test_the_verifier_script_is_read_only_and_refuses_without_arguments(self):
        path = os.path.join(HERE, "post_publish_verify.sh")
        with open(path) as fh:
            text = fh.read()
        for word in ("git push", "gh pr create", "gh pr merge", "gh release create", "gh release edit", "git tag",
                     "gh api -X", "--method"):
            self.assertNotIn(word, text)
        out = subprocess.run(["sh", path], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "fails=0 nodata=1")


RUNBOOK = os.path.join(os.path.dirname(HERE), "docs", "plan", "specs", "RUNBOOK-1.1.3.md")


class TheCheckAndTheRunbook(Fixture):
    def put(self, phases, cand=None, mtime=None):
        st = self.state(phases)
        if cand:
            st["candidate_sha"] = cand
        path = D.state_path(V, st["candidate_sha"])
        D.save_state(path, st)
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def run_check(self):
        out = io.StringIO()
        return D.check(V, out=out), out.getvalue()

    def test_check_reads_the_newest_state(self):
        self.assertEqual(self.run_check()[0], 2)
        five = self.passed(*D.PHASES[:-1])
        for last, want in (({"verdict": "FAIL"}, 1), ({"verdict": "NO-DATA"}, 1), (None, 1),
                           ({"verdict": "REUSED"}, 0)):
            phases = dict(five)
            if last is not None:
                phases["after"] = last
            self.put(phases)
            code, said = self.run_check()
            self.assertEqual(code, want, (last, said))
        self.put(dict((n, {"verdict": "REUSED"}) for n in D.PHASES + ("extra",)))
        self.assertEqual(self.run_check()[0], 0)
        older = self.put(self.passed(*D.PHASES), cand="d" * 40, mtime=1000000000)
        self.put(five, mtime=2000000000)
        code, said = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn(self.cand, said)
        os.utime(older, (3000000000, 3000000000))
        code, said = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("d" * 40, said)
        with open(older, "w") as fh:
            fh.write("{")
        os.utime(older, (3000000000, 3000000000))
        self.assertEqual(self.run_check()[0], 2)
        self.assertEqual(D.complete({"phases": five}), (False, ["after"]))
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(D.main(["check", "--version", V]), 2)

    @unittest.skipUnless(os.path.exists(RUNBOOK), "docs/plan/specs/RUNBOOK-1.1.3.md is not shipped here")
    def test_the_runbook_names_every_phase_and_command(self):
        with open(RUNBOOK, encoding="utf-8") as fh:
            text = fh.read()
        for phase in D.PHASES:
            self.assertIn("### %s" % phase, text)
        for word in ("cut_driver.py run --version 1.1.3", "cut_driver.py resume", "cut_driver.py wait",
                     "cut_driver.py check --version 1.1.3", "run_rehearsal_112.sh", "cut_from_rehearsal_112.sh"):
            self.assertIn(word, text)
        self.assertIn("the owner types the y", text)
        for code in (0x2013, 0x2014):
            self.assertNotIn(chr(code), text)


if __name__ == "__main__":
    unittest.main()

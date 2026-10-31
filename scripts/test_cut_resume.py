#!/usr/bin/env python3
"""CUT1's FROZEN harness, to the cut experience audit of 2026-10-30 (cut-experience-audit.json, acceptance 1 to 6):
a cut that fails late restarts without repeating a completed unchanged expensive check; evidence is keyed by its
inputs and an unknown input never hits; the one use approval is spent only when the candidate is ready, once, and a
wrong candidate, an expired or a replayed approval refuse; concurrent consumers cause one supported refresh and an
unrenewable expiry parks once then resumes without lost progress; a network failure after the tag reconciles remote
state without a second tag or a restarted chain; a cold rehearsal and a failure and resume rehearsal are recorded with
their expensive executions and interventions. Written by the session before the code; never edited by its builder.

    python3 -B scripts/test_cut_resume.py Resume Inputs Approval Auth Reconcile Rehearsed

RED BY ASSERTION, one condition per case, hermetic (fixture phase tables, scratch state dirs, fake runners). Python 3.9."""
import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
TREE_A = "a" * 40
TREE_B = "b" * 40
REHEARSAL = os.path.join(ROOT, "docs", "plan", "CUT-REHEARSAL-1.1.9.json")


def _exported():
    """True only on a tree the export built: export_public.EXPORT_OVERRIDES swaps the root PROJECT.md for
    docs/plan/PROJECT.public.md there (the rule scripts/test_notes.py carries). The rehearsal record is a hub record
    (it names the owner's host and its evidence paths) and is not shipped, so on an exported tree its absence is
    NO-DATA, never a pass; in the hub its absence is the failure it always was. Unreadable: not an export."""
    try:
        with open(os.path.join(ROOT, "PROJECT.md"), encoding="utf-8") as a, \
                open(os.path.join(ROOT, "docs", "plan", "PROJECT.public.md"), encoding="utf-8") as b:
            return a.read() == b.read()
    except OSError:
        return False


EXPORTED = _exported()


def need(case, mod, name, what, params=None):
    """The attribute, asserted first so a missing or differently shaped one FAILS by assertion, never errors."""
    case.assertTrue(hasattr(mod, name), "%s defines no %s" % (what, name))
    fn = getattr(mod, name)
    if params:
        import inspect
        try:
            have = list(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            have = []
        for p in params:
            case.assertIn(p, have, "%s.%s takes no %s (it takes %s)" % (what, name, p, have))
    return fn


RUN_PARAMS = ("version", "phases", "state_dir", "inputs", "resume")


def phases(fail_at, tree, calls=None):
    """A fixture phase table for cut_driver.run: each runner answers (verdict, detail, tree); `fail_at` fails once."""
    calls = {} if calls is None else calls

    def runner(name):
        def run(state):
            calls[name] = calls.get(name, 0) + 1
            if name == fail_at and calls[name] == 1:
                return ("FAIL", "induced at %s" % name, tree)
            return ("PASS", "ok", tree)
        return run
    return {n: runner(n) for n in ("ready", "freeze", "preflight", "rehearsal", "adopt", "cut", "after")}, calls


class Resume(unittest.TestCase):
    """Acceptance 1: a late failure restarts at the failed phase; no completed unchanged expensive phase runs again."""

    def test_a_late_failure_restarts_without_repeating_completed_phases(self):
        import cut_driver as D
        run = need(self, D, "run", "cut_driver", RUN_PARAMS)
        state = tempfile.mkdtemp(prefix="cut-state-")
        table, calls = phases("adopt", TREE_A)
        first = dict(run("9.9.9", table, state, inputs={"candidate": TREE_A}))
        self.assertEqual("FAIL", first["adopt"][0], first)
        second = dict(run("9.9.9", table, state, inputs={"candidate": TREE_A}, resume=True))
        for name in ("ready", "freeze", "preflight", "rehearsal"):
            self.assertEqual("REUSED", second[name][0], "%s was rerun: %r" % (name, second[name]))
            self.assertEqual(1, calls[name], "%s ran %d times" % (name, calls[name]))
        self.assertEqual("PASS", second["adopt"][0])
        self.assertEqual(2, calls["adopt"])

    def test_every_attempt_is_recorded_outside_the_candidate(self):
        """Each phase attempt carries its duration, input identity, result and reuse miss reason, in the state dir."""
        import cut_driver as D
        run = need(self, D, "run", "cut_driver", RUN_PARAMS)
        state = tempfile.mkdtemp(prefix="cut-state-")
        table, _c = phases("rehearsal", TREE_A)
        run("9.9.9", table, state, inputs={"candidate": TREE_A})
        run("9.9.9", table, state, inputs={"candidate": TREE_A}, resume=True)
        records = [f for f in os.listdir(state) if f.endswith(".json")]
        self.assertTrue(records, "no attempt record in the state dir")
        doc = json.load(open(os.path.join(state, sorted(records)[0])))
        attempts = doc.get("attempts") or []
        self.assertGreaterEqual(len(attempts), 6, "fewer attempt rows than phases run: %r" % (attempts,))
        for a in attempts:
            for key in ("phase", "seconds", "inputs", "result"):
                self.assertIn(key, a, "an attempt row lacks %s: %r" % (key, a))
        self.assertTrue(any(a.get("reuse_miss") for a in attempts), "the rerun phase names no reuse miss reason")


class Inputs(unittest.TestCase):
    """Acceptance 2: evidence is keyed by candidate, checker, policy, interpreter and environment; unknown never hits."""

    def test_a_changed_input_invalidates_and_unknown_inputs_never_hit(self):
        import cut_driver as D
        run = need(self, D, "run", "cut_driver", RUN_PARAMS)
        state = tempfile.mkdtemp(prefix="cut-state-")
        table, calls = phases("adopt", TREE_A)
        base = {"candidate": TREE_A, "checker": "c1", "policy": "p1", "interpreter": "3.13", "environment": "e1"}
        run("9.9.9", table, state, inputs=base)
        for key in ("checker", "policy", "interpreter", "environment"):
            changed = dict(base, **{key: base[key] + "x"})
            got = dict(run("9.9.9", table, state, inputs=changed, resume=True))
            self.assertNotEqual("REUSED", got["ready"][0], "a changed %s reused the ready entry: %r" % (key, got["ready"]))
            self.assertIn(key, got["ready"][1], "the miss reason does not name %s" % key)
        got = dict(run("9.9.9", table, state, inputs=None, resume=True))
        self.assertNotEqual("REUSED", got["ready"][0], "unknown inputs produced a hit")
        got = dict(run("9.9.9", table, state, inputs=dict(base, candidate=TREE_B), resume=True))
        self.assertNotEqual("REUSED", got["ready"][0])


class Approval(unittest.TestCase):
    """Acceptance 3: the one use approval is verified early but SPENT only when the candidate is ready; a late failure
    spends nothing; a ready candidate asks once; wrong candidate, expired and replayed approvals refuse."""

    def recorder(self):
        rec = {"verified": [], "spent": []}

        class Store(object):
            def verify(self, line, version, head):
                rec["verified"].append((line, head))
                return "" if head == TREE_A and line.startswith("ok") else "refused: %s" % head[:12]

            def spend(self, line, version, head):
                rec["spent"].append((line, head))
                return ""
        return rec, Store()

    def test_a_late_failure_spends_no_approval(self):
        import cut as C
        run_cut = need(self, C, "run_approved_cut", "cut", ("line", "version", "head", "approval", "ready"))
        rec, store = self.recorder()
        got = run_cut("ok-line", "9.9.9", TREE_A, approval=store, ready=lambda: ("FAIL", "late failure in the chain"))
        self.assertEqual([], rec["spent"], "a late failure spent the approval")
        self.assertEqual(1, len(rec["verified"]))
        self.assertNotEqual(0, got)

    def test_a_ready_candidate_spends_once_and_a_replay_refuses(self):
        import cut as C
        run_cut = need(self, C, "run_approved_cut", "cut", ("line", "version", "head", "approval", "ready"))
        rec, store = self.recorder()
        got = run_cut("ok-line", "9.9.9", TREE_A, approval=store, ready=lambda: ("PASS", "ready"))
        self.assertEqual(0, got)
        self.assertEqual(1, len(rec["spent"]), "a ready cut spent the approval %d times" % len(rec["spent"]))
        got = run_cut("ok-line", "9.9.9", TREE_B, approval=store, ready=lambda: ("PASS", "ready"))
        self.assertNotEqual(0, got, "a wrong candidate was accepted")
        self.assertEqual(1, len(rec["spent"]))
        got = run_cut("expired-line", "9.9.9", TREE_A, approval=store, ready=lambda: ("PASS", "ready"))
        self.assertNotEqual(0, got, "an expired or replayed line was accepted")

    def test_main_does_not_spend_before_cut_mode(self):
        """The entry path: main verifies the line and hands the store to cut_mode; nothing is spent in main."""
        import inspect
        import cut as C
        src = inspect.getsource(C.main)
        self.assertNotIn("approval_refusal(", src, "main still spends the approval before cut_mode (scripts/cut.py:4224)")
        self.assertIn("run_approved_cut", inspect.getsource(C.cut_mode), "cut_mode does not route through run_approved_cut")


class Auth(unittest.TestCase):
    """Acceptance 4: concurrent consumers cause one refresh; an unrenewable expiry parks once and resumes."""

    def test_concurrent_consumers_cause_one_refresh(self):
        import brother_login as L
        refresh_once = need(self, L, "refresh_once", "brother_login", ("scope", "refresher", "state_dir", "attempts", "notify"))
        calls = []
        lock = threading.Lock()

        def refresher():
            with lock:
                calls.append(1)
            return ("RENEWED", "ok")
        state = tempfile.mkdtemp(prefix="login-state-")
        results = []

        def consumer():
            results.append(refresh_once("claude", refresher, state_dir=state, attempts=2))
        threads = [threading.Thread(target=consumer) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(1, len(calls), "six consumers caused %d refreshes" % len(calls))
        self.assertTrue(all(r[0] == "RENEWED" for r in results), results)

    def test_an_unrenewable_expiry_parks_once_then_resumes(self):
        import brother_login as L
        refresh_once = need(self, L, "refresh_once", "brother_login", ("scope", "refresher", "state_dir", "attempts", "notify"))
        state = tempfile.mkdtemp(prefix="login-state-")
        prompts = []

        def dead():
            return ("EXPIRED", "unrenewable")
        for _ in range(3):
            got = refresh_once("claude", dead, state_dir=state, attempts=2, notify=prompts.append)
            self.assertEqual("PARKED", got[0], got)
        self.assertEqual(1, len(prompts), "an unrenewable expiry prompted %d times" % len(prompts))
        self.assertIn("brother-login", prompts[0])
        got = refresh_once("claude", lambda: ("RENEWED", "signed in"), state_dir=state, attempts=2, notify=prompts.append)
        self.assertEqual("RENEWED", got[0])
        self.assertEqual(1, len(prompts))


class Reconcile(unittest.TestCase):
    """Acceptance 5: a network failure after the tag reconciles remote state; no second tag, no restarted chain.
    Security review 2026-10-30: a tag is reconciled only when the remote's tag points at THE COMMIT THIS RUN MADE;
    a tag of the same name at another commit is foreign and the publish FAILS closed, never adopts it."""

    COMMIT = "c" * 40
    OTHER = "d" * 40

    def remote(self, fail_first=True, foreign=None):
        remote = {"tags": {}, "pushes": 0, "fail_first": fail_first}
        if foreign:
            remote["tags"]["v9.9.9"] = foreign

        def push(tag, commit):
            remote["pushes"] += 1
            if remote["fail_first"]:
                remote["fail_first"] = False
                remote["tags"][tag] = commit      # the tag reached the remote, the connection then dropped
                raise OSError("connection reset")
            remote["tags"][tag] = commit
            return 0

        def ls_remote(tag):
            return remote["tags"].get(tag)         # the peeled commit the remote tag points at, or None
        return remote, push, ls_remote

    def test_a_push_failure_after_the_tag_reconciles_without_a_second_tag(self):
        import cut as C
        publish = need(self, C, "publish_tag", "cut", ("tag", "commit", "push", "ls_remote", "state_dir"))
        remote, push, ls_remote = self.remote()
        state = tempfile.mkdtemp(prefix="publish-state-")
        got = publish("v9.9.9", self.COMMIT, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("PUSHED", got[0], got)
        self.assertEqual({"v9.9.9": self.COMMIT}, remote["tags"])
        got = publish("v9.9.9", self.COMMIT, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("REUSED", got[0], "a second publish re-pushed or re-tagged: %r" % (got,))
        self.assertEqual(1, remote["pushes"], "the push was retried after the tag already existed remotely")

    def test_a_foreign_tag_is_never_adopted(self):
        """The remote holds a tag of this name at another commit: FAIL closed, no push, no adoption."""
        import cut as C
        publish = need(self, C, "publish_tag", "cut", ("tag", "commit", "push", "ls_remote", "state_dir"))
        remote, push, ls_remote = self.remote(fail_first=False, foreign=self.OTHER)
        state = tempfile.mkdtemp(prefix="publish-state-")
        got = publish("v9.9.9", self.COMMIT, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("FAIL", got[0], "a foreign tag was adopted: %r" % (got,))
        self.assertIn("foreign", got[1].lower())
        self.assertEqual(0, remote["pushes"], "a push was attempted over a foreign tag")
        self.assertEqual(self.OTHER, remote["tags"]["v9.9.9"])

    def test_an_unreadable_remote_fails_closed_after_a_failed_push(self):
        import cut as C
        publish = need(self, C, "publish_tag", "cut", ("tag", "commit", "push", "ls_remote", "state_dir"))
        remote, push, _ls = self.remote()
        state = tempfile.mkdtemp(prefix="publish-state-")
        got = publish("v9.9.9", self.COMMIT, push=push, ls_remote=lambda tag: None, state_dir=state)
        self.assertEqual("FAIL", got[0], "an unreadable remote after a failed push did not fail closed: %r" % (got,))
        self.assertEqual(1, remote["pushes"], "the push was retried without reading the remote")
    def exporter_remote(self):
        """A remote whose push lands the tag at the exporter's own commit whatever the chain passed (the chain learns
        the commit from the exporter's TAGGING line, so it hands the push nothing yet): the real path's shape."""
        remote = {"tags": {}, "pushes": 0}

        def push(tag, commit):
            remote["pushes"] += 1
            remote["tags"][tag] = self.COMMIT
            return 0

        def ls_remote(tag):
            return remote["tags"].get(tag)
        return remote, push, ls_remote

    def test_a_restart_with_a_learnt_commit_reuses_its_own_tag(self):
        """Review 2026-10-30, gap 1: a run that learns its commit during the push RECORDS what it learnt, so the same
        run restarted (a new reader, the same state folder) reads REUSED for its own tag with no second push; a
        restart that claims another commit still FAILS closed, the tag is never adopted by name."""
        import cut as C
        publish = need(self, C, "publish_tag", "cut", ("tag", "commit", "push", "ls_remote", "state_dir"))
        state = tempfile.mkdtemp()
        remote, push, ls_remote = self.exporter_remote()
        got = publish("v9.9.9", lambda: self.COMMIT, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("PUSHED", got[0], got)
        got = publish("v9.9.9", lambda: self.COMMIT, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("REUSED", got[0], "a restarted run read its own tag as foreign or pushed again: %r" % (got,))
        self.assertEqual(1, remote["pushes"], "the restart pushed a second time")
        got = publish("v9.9.9", lambda: self.OTHER, push=push, ls_remote=ls_remote, state_dir=state)
        self.assertEqual("FAIL", got[0], "a run claiming another commit adopted the tag: %r" % (got,))
        self.assertEqual(1, remote["pushes"], "the foreign claim pushed")

    def test_a_pushed_state_that_cannot_be_written_never_reads_pushed(self):
        """Review 2026-10-30, gap 2: the pushed state that cannot be persisted is a durability failure: the answer is
        FAIL naming the record, never PUSHED over a record still saying intent. The push itself lands once."""
        import cut as C
        publish = need(self, C, "publish_tag", "cut", ("tag", "commit", "push", "ls_remote", "state_dir"))
        state = tempfile.mkdtemp()
        remote = {"tags": {}, "pushes": 0}

        def push(tag, commit):
            remote["pushes"] += 1
            remote["tags"][tag] = self.COMMIT
            os.chmod(state, 0o500)                 # the state folder stops taking writes after the intent landed
            return 0
        self.addCleanup(os.chmod, state, 0o700)
        got = publish("v9.9.9", self.COMMIT, push=push, ls_remote=lambda tag: remote["tags"].get(tag), state_dir=state)
        self.assertEqual("FAIL", got[0], "PUSHED was answered over a record that could not be written: %r" % (got,))
        self.assertIn("publish-v9.9.9.json", got[1], "the detail does not name the record: %r" % (got,))
        self.assertEqual(1, remote["pushes"], "the durability failure pushed again")
        os.chmod(state, 0o700)
        with open(os.path.join(state, "publish-v9.9.9.json"), encoding="utf-8") as fh:
            self.assertEqual("intent", json.load(fh)["state"], "the record claims a state that was never written")


class Rehearsed(unittest.TestCase):
    """Acceptance 6: one timed cold rehearsal and one failure and resume rehearsal on the real host, recorded."""

    def test_both_rehearsals_are_recorded_with_their_costs(self):
        if EXPORTED and not os.path.isfile(REHEARSAL):
            self.skipTest("NO-DATA: the rehearsal record is a hub record and is not shipped; the case is judged in the hub")
        self.assertTrue(os.path.isfile(REHEARSAL), "docs/plan/CUT-REHEARSAL-1.1.9.json does not exist yet")
        doc = json.load(open(REHEARSAL, encoding="utf-8"))
        for kind in ("cold", "failure_and_resume"):
            rec = doc.get(kind)
            self.assertIsInstance(rec, dict, "no %s rehearsal recorded" % kind)
            self.assertIn("seconds", rec)
            self.assertIsInstance(rec.get("expensive_executions"), list, kind)
            self.assertIsInstance(rec.get("owner_interventions"), list, kind)
            self.assertRegex(str(rec.get("candidate", "")), r"^[0-9a-f]{40}$")
        self.assertEqual("real", doc.get("host_kind"), "the rehearsals were not on the real host")
        resumed = doc["failure_and_resume"]
        self.assertTrue(resumed.get("reused_phases"), "the resume reused no phase")
        self.assertEqual(0, len([e for e in resumed["expensive_executions"] if e.get("repeated")]),
                         "an expensive check ran twice on the resume")


if __name__ == "__main__":
    unittest.main()

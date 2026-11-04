#!/usr/bin/env python3
"""LOOP1.d: a unit closes on structured landing facts that agree with its prose, verified against git, never on prose alone.

usage: python3 -B scripts/test_landing_facts.py
The frozen pack contract (scripts/test_landing_contract.py, class Landing) tests the helper alone. These cases add the
helper's remaining guards, the writer (land_batch.landing_records), the store (plan_store.update_units and
undo_appended) and, at the ENTRY POINT, scripts/close_unit.py run as a process over a real git repository: a control is
tested where it lives, never only at a helper it calls.
P1 (ruled 2026-10-31): an event's `commit` is the landing's BASE, the commit it was built and graded on; the landing
commit is the one that added the event's evidence_id to the plan, and a revert of that commit refuses the close."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "loop")]
os.environ.pop("BROTHER_WBS", None)   # the fixtures own their plan, at the default path under their own tree
import plan_store  # noqa: E402

CLOSER = os.path.join(HERE, "close_unit.py")
PLAN_REL = os.path.join("docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def eid(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def event(**kw):
    return dict(dict(schema=1, sequence=1, type="landed", unit="U1", sub="U1.a", commit="a" * 40, evidence_id="b" * 64), **kw)


def raw(path):
    with open(path, "rb") as fh:
        return fh.read()


def fact(events, unit="U1", sub="U1.a"):
    return plan_store.landing_facts(events, unit, sub)


class TheHelperGuards(unittest.TestCase):
    def test_relanding_after_revert_lands_on_the_newest_commit(self):
        got = fact([event(), event(type="reverted", sequence=2), event(sequence=3, commit="c" * 40)])
        self.assertTrue(got["landed"], got)
        self.assertEqual(got["commit"], "c" * 40)

    def test_a_duplicate_event_is_idempotent(self):
        self.assertTrue(fact([event(), event()])["landed"])

    def test_a_malformed_event_is_named_never_dropped(self):
        got = fact([event(), event(schema=2, sequence=2)])
        self.assertFalse(got["landed"], got)
        self.assertIn("schema", got["reason"])

    def test_each_malformed_field_names_itself(self):
        """One fixture per field guard: beside a valid landing, a later event broken in exactly one field."""
        for bad in (dict(commit=""), dict(sequence=0), dict(type="landedish"), dict(unit=None), dict(sub=None)):
            with self.subTest(bad=bad):
                got = fact([event(), dict(event(sequence=2), **bad)])
                self.assertFalse(got["landed"], got)
                self.assertIn("malformed", got["reason"])

    def test_another_units_event_never_lands_this_sub_unit(self):
        self.assertFalse(fact([event(unit="U2")])["landed"])

    def test_two_different_events_at_one_sequence_refuse(self):
        got = fact([event(type="reverted"), event()])   # the landing last, so last-wins would read it landed
        self.assertFalse(got["landed"], got)
        self.assertIn("two different events at sequence 1", got["reason"])

    def test_events_that_are_not_a_list_refuse(self):
        self.assertFalse(fact(None)["landed"])
        self.assertFalse(fact({"x": event()})["landed"])

    def test_no_event_is_not_landed(self):
        got = fact([])
        self.assertFalse(got["landed"])
        self.assertIn("no landing event", got["reason"])


class TheWriterAndTheStore(unittest.TestCase):
    def plan(self, unit):
        d = tempfile.mkdtemp(prefix="landing-facts-")
        p = os.path.join(d, "plan.json")
        with open(p, "w") as fh:
            json.dump({"units": [unit]}, fh)
        return p

    def test_a_landing_appends_one_event_per_sub_unit_and_a_refusal_takes_them_back(self):
        import land_batch
        base = "d" * 40
        p = self.plan({"id": "U1", "sub_units": ["U1.a", "U1.b"], "evidence": "",
                       "landing_events": [event(sub="U1.b", sequence=4)]})
        before = raw(p)
        line = " U1.a landed 2026-10-31: suites."
        seen = land_batch.Appended()
        plan_store.update_units(p, land_batch.landing_records({"U1": [line]}, seen, events={"U1": [("U1.a", line)]}, base=base),
                                fields=("evidence", "owns", "landing_events"))
        unit = plan_store.load(p)["units"][0]
        new = unit["landing_events"][-1]
        self.assertEqual((new["sub"], new["commit"], new["evidence_id"], new["sequence"]), ("U1.a", base, eid(line), 5))
        self.assertTrue(fact(unit["landing_events"])["landed"])
        self.assertEqual(plan_store.undo_appended(p, seen, committed=before), [])
        self.assertEqual(raw(p), before, "the refusal left this landing's event behind")

    def land(self, p, seen):
        import land_batch
        line = " U1.a landed 2026-10-31: suites."
        plan_store.update_units(p, land_batch.landing_records({"U1": [line]}, seen, events={"U1": [("U1.a", line)]}, base="d" * 40),
                                fields=("evidence", "owns", "landing_events"))

    def test_a_refused_first_landing_restores_the_plan_byte_for_byte(self):
        """The unit had no landing_events key: the refusal takes the key back too, so the tree reads as committed."""
        import land_batch
        p = self.plan({"id": "U1", "sub_units": ["U1.a"], "evidence": ""})
        before, seen = raw(p), land_batch.Appended()
        self.land(p, seen)
        self.assertIn("landing_events", plan_store.load(p)["units"][0])
        self.assertEqual(plan_store.undo_appended(p, seen, committed=before), [])
        self.assertEqual(raw(p), before)

    def test_unknown_committed_bytes_keep_the_emptied_key(self):
        import land_batch
        p = self.plan({"id": "U1", "sub_units": ["U1.a"], "evidence": ""})
        seen = land_batch.Appended()
        self.land(p, seen)
        self.assertEqual(plan_store.undo_appended(p, seen, committed=None), [])
        self.assertEqual(plan_store.load(p)["units"][0]["landing_events"], [])

    def test_a_landing_events_that_is_not_a_list_is_left_alone(self):
        import land_batch
        p = self.plan({"id": "U1", "sub_units": ["U1.a"], "evidence": "", "landing_events": "torn"})
        self.land(p, land_batch.Appended())
        unit = plan_store.load(p)["units"][0]
        self.assertEqual(unit["landing_events"], "torn")
        self.assertIn("U1.a landed", unit["evidence"])
        self.assertFalse(fact(unit["landing_events"])["landed"])

    def test_a_refusal_whose_lines_were_rewritten_still_takes_its_events_back(self):
        """The evidence restore misses (another writer rewrote the lines), and the events go anyway: left beside the
        prose, the next commit of the plan would become their landing commit."""
        import land_batch
        p = self.plan({"id": "U1", "sub_units": ["U1.a"], "evidence": ""})
        seen = land_batch.Appended()
        self.land(p, seen)
        plan = plan_store.load(p)
        plan["units"][0]["evidence"] = "rewritten by hand: U1.a landed elsewhere."
        with open(p, "w") as fh:
            json.dump(plan, fh)
        self.assertEqual(plan_store.undo_appended(p, seen, committed=None), ["U1"])
        unit = plan_store.load(p)["units"][0]
        self.assertEqual(unit["landing_events"], [])
        self.assertEqual(unit["evidence"], "rewritten by hand: U1.a landed elsewhere.")

    def test_a_stale_copy_cannot_drop_a_newer_event(self):
        p = self.plan({"id": "U1", "sub_units": ["U1.a"], "evidence": "", "landing_events": []})
        stale = plan_store.load(p)["units"][0]
        plan_store.update_units(p, {"U1": lambda u: dict(u, landing_events=u["landing_events"] + [event()])})
        on_disk = raw(p)
        stale["landing_events"] = [event(sub="U1.b", sequence=2)]
        with self.assertRaises(ValueError):
            plan_store.update_units(p, {"U1": stale}, fields=("landing_events",))
        self.assertEqual(raw(p), on_disk)
        plan_store.update_units(p, {"U1": lambda u: dict(u, landing_events=u["landing_events"] + [event(sub="U1.b", sequence=2)])})
        self.assertEqual(len(plan_store.load(p)["units"][0]["landing_events"]), 2)


def sandbox_reason():
    """The positive control runs a done check under the landing sandbox, which cannot nest (the pre-push gate's hermetic
    rerun already runs inside one): that host answer is NO-DATA by name, as scripts/test_close_unit.py reads it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("landing_facts_grade_build", os.path.join(HERE, "loop", "grade_build.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.sandbox_ready()


class RepoFixture(object):
    """A real repository for close_unit.py run as a process: a base commit, then a landing commit carrying the code, the
    prose and the event, exactly as land_batch writes them. Mixed into a TestCase."""

    def git(self, d, *argv):
        r = subprocess.run(GIT + list(argv), cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "git %s: %s" % (" ".join(argv), r.stderr))
        return r.stdout.strip()

    def write_plan(self, d, evidence, events):
        """events None leaves the landing_events key out: a legacy unit, landed before LOOP1.d."""
        unit = {"id": "U1", "state": "PARTIAL", "sub_units": ["U1.a"], "evidence": evidence, "done_check": "python3 -B check.py"}
        if events is not None:
            unit["landing_events"] = events
        with open(os.path.join(d, PLAN_REL), "w") as fh:
            json.dump({"units": [unit]}, fh, indent=1)

    def repo(self, with_event=True, commit=None, legacy=False):
        d = tempfile.mkdtemp(prefix="landing-close-")
        self.addCleanup(subprocess.run, ["rm", "-rf", d])
        os.makedirs(os.path.join(d, "docs", "plan"))
        with open(os.path.join(d, "check.py"), "w") as fh:
            fh.write("print('PASS: the unit check ran')\n")
        self.write_plan(d, "", None if legacy else [])
        self.git(d, "init", "-q", "-b", "main"); self.git(d, "add", "-A"); self.git(d, "commit", "-q", "-m", "base")
        base = self.git(d, "rev-parse", "HEAD")
        line = " U1.a landed 2026-10-31: suites; base %s." % base[:9]
        with open(os.path.join(d, "work.py"), "w") as fh:
            fh.write("WORK = 1\n")
        self.write_plan(d, line, None if legacy else [event(commit=commit or base, evidence_id=eid(line))] if with_event else [])
        self.git(d, "add", "-A"); self.git(d, "commit", "-q", "-m", "land U1.a")
        return d, self.git(d, "rev-parse", "HEAD"), line

    def close(self, d, pure=False):
        """Run the closer. Git confirms a landing only after the done check passes, and the done check runs under the
        landing sandbox, so every case but the pure disagreement needs one: no sandbox here is NO-DATA by name."""
        reason = None if pure else sandbox_reason()
        if reason:
            self.skipTest("NO-DATA: %s" % reason)
        r = subprocess.run([sys.executable, "-B", CLOSER, "U1"], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                           capture_output=True, text=True, timeout=180)
        with open(os.path.join(d, PLAN_REL), encoding="utf-8") as fh:
            return r, json.load(fh)["units"][0]


class TheCloserAtItsEntryPoint(RepoFixture, unittest.TestCase):
    """close_unit.py at its entry point: the clean landing closes, and each refusal comes from the one guard it names."""

    def test_a_clean_landing_closes(self):
        d, _, _ = self.repo()
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(unit["state"], "DONE", r.stdout)
        self.assertNotIn("LEGACY", r.stdout)

    def test_a_legacy_unit_closes_on_its_prose_and_says_so(self):
        """P2b (owner, 2026-10-31): a unit with no landing_events key closes as before, and the close names the gap."""
        d, _, _ = self.repo(legacy=True)
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(unit["state"], "DONE", r.stdout)
        self.assertIn("LEGACY U1: no landing events", r.stdout)

    def test_a_reverted_landing_never_closes_though_the_plan_kept_its_evidence(self):
        """The revert takes the code out and leaves the plan as the landing wrote it: one commit added the event, and the
        revert scan is the only guard that sees the landing is gone."""
        d, landing, _ = self.repo()
        self.git(d, "revert", "--no-commit", landing)
        self.git(d, "checkout", landing, "--", PLAN_REL)
        self.git(d, "commit", "-q", "-m", 'Revert "land U1.a"', "-m", "This reverts commit %s." % landing)
        self.assertFalse(os.path.exists(os.path.join(d, "work.py")), "the fixture's revert did not take the code out")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("was reverted", r.stdout)

    def test_a_reland_after_a_revert_is_no_data(self):
        """The revert takes the event out with the code, and a person puts the evidence back by hand: two commits added
        the event, which the closer reads as NO-DATA (the integrator's tightening, 2026-10-31)."""
        d, landing, line = self.repo()
        self.git(d, "revert", "--no-edit", landing)
        with open(os.path.join(d, PLAN_REL), encoding="utf-8") as fh:
            self.assertNotIn("U1.a landed", fh.read(), "the fixture's revert did not take the evidence out")
        base = self.git(d, "rev-parse", "HEAD~2")
        self.write_plan(d, line, [event(commit=base, evidence_id=eid(line))])
        self.git(d, "commit", "-q", "-am", "restore U1.a evidence")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("NO-DATA U1.a: 2 commits", r.stdout)

    def test_prose_without_an_event_is_a_visible_disagreement(self):
        d, _, _ = self.repo(with_event=False)
        r, unit = self.close(d, pure=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("DISAGREE U1.a", r.stdout)
        self.assertNotIn("running its done_check", r.stdout)

    def test_a_base_outside_this_history_never_closes(self):
        d, _, _ = self.repo(commit="e" * 40)
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("not an ancestor", r.stdout)

    def test_a_base_on_another_line_never_closes(self):
        d, _, line = self.repo(with_event=False)
        other = self.git(d, "commit-tree", "HEAD~1^{tree}", "-m", "another line")   # exists here, never merged
        self.write_plan(d, line, [event(commit=other, evidence_id=eid(line))])
        self.git(d, "commit", "-q", "-am", "an event naming another line's commit")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("not an ancestor", r.stdout)

    def test_an_event_no_commit_carries_never_closes(self):
        d, _, line = self.repo(with_event=False)
        base = self.git(d, "rev-parse", "HEAD~1")
        self.write_plan(d, line, [event(commit=base, evidence_id=eid(line))])   # uncommitted: no landing commit added it
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("NO-DATA U1.a: 0 commits", r.stdout)


class TheClosersReadersInProcess(RepoFixture, unittest.TestCase):
    """The closer's two readers called directly, for the guards no process run can isolate."""

    def refusal(self, d, unit, plan_path=PLAN_REL):
        import close_unit
        here = os.getcwd()
        os.chdir(d)
        try:
            return close_unit.landing_refusal(unit, plan_path)
        finally:
            os.chdir(here)

    def unit(self, d):
        with open(os.path.join(d, PLAN_REL), encoding="utf-8") as fh:
            return json.load(fh)["units"][0]

    def test_both_readers_saying_not_landed_is_still_a_refusal(self):
        import close_unit
        why = close_unit.facts_refusal({"id": "U1", "sub_units": ["U1.a"], "evidence": "", "landing_events": []})
        self.assertIn("U1.a is not landed", why)

    def test_the_git_reader_still_refuses_a_disagreement(self):
        d, _, _ = self.repo(with_event=False)
        self.assertIn("DISAGREE U1.a", self.refusal(d, self.unit(d)))

    def test_a_history_git_cannot_read_is_no_data(self):
        d, _, _ = self.repo()
        self.assertEqual(self.refusal(d, self.unit(d)), "", "the fixture's clean landing must be confirmed first")
        why = self.refusal(d, self.unit(d), os.path.join(tempfile.gettempdir(), "no-such-dir-landing", "plan.json"))
        self.assertIn("NO-DATA: git log exited", why)

    def test_a_base_that_is_not_a_commit_is_no_data(self):
        d, _, line = self.repo()
        blob = self.git(d, "hash-object", "-w", "check.py")
        unit = dict(self.unit(d), landing_events=[event(commit=blob, evidence_id=eid(line))])
        self.assertIn("NO-DATA: git merge-base exited", self.refusal(d, unit))

    def test_an_event_hashing_no_evidence_line_is_a_disagreement(self):
        import close_unit
        line = " U1.a landed 2026-10-31: suites."
        unit = {"id": "U1", "sub_units": ["U1.a"], "evidence": line,
                "landing_events": [event(evidence_id=eid(" U1.a landed somewhere else."))]}
        self.assertIn("hashes no line", close_unit.facts_refusal(unit))
        unit["landing_events"] = [event(evidence_id=eid(line))]
        self.assertEqual(close_unit.facts_refusal(unit), "")

    def test_only_a_unit_with_no_events_key_is_legacy(self):
        import close_unit
        unit = {"id": "U1", "sub_units": ["U1.a"], "evidence": " U1.a landed 2026-10-31: suites."}
        self.assertTrue(close_unit.legacy(unit))
        self.assertEqual(close_unit.facts_refusal(unit), "")
        self.assertFalse(close_unit.legacy(dict(unit, landing_events=[])))
        self.assertIn("DISAGREE U1.a", close_unit.facts_refusal(dict(unit, landing_events=[])))

    def test_a_reverted_merge_that_brought_the_landing_in_refuses(self):
        """The landing came in through a merge, and `git revert -m 1 <merge>` names the merge, never the landing; the
        plan side of the revert was resolved as kept, so only the merge rule can see the landing is gone."""
        d, landing, _ = self.repo()
        self.git(d, "branch", "side", landing)
        self.git(d, "reset", "-q", "--hard", "HEAD~1")
        self.git(d, "commit", "-q", "--allow-empty", "-m", "main moves on")
        self.git(d, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        merge = self.git(d, "rev-parse", "HEAD")
        self.assertEqual(self.refusal(d, self.unit(d)), "", "the merged landing must be confirmed first")
        self.git(d, "revert", "--no-commit", "-m", "1", merge)
        self.git(d, "checkout", merge, "--", PLAN_REL)
        self.git(d, "commit", "-q", "-m", 'Revert "merge side"', "-m", "This reverts commit %s, reversing changes." % merge)
        self.assertIn("the merge %s that brought in" % merge[:12], self.refusal(d, self.unit(d)))

    def test_a_reverted_merge_that_did_not_bring_the_landing_in_is_no_refusal(self):
        """The merge reaches the landing through its first parent: reverting it leaves the landing in place."""
        d, _, _ = self.repo()
        self.git(d, "checkout", "-q", "-b", "side")
        with open(os.path.join(d, "other.py"), "w") as fh:
            fh.write("OTHER = 1\n")
        self.git(d, "add", "-A"); self.git(d, "commit", "-q", "-m", "side work")
        self.git(d, "checkout", "-q", "main")
        self.git(d, "commit", "-q", "--allow-empty", "-m", "main moves on")
        self.git(d, "merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.git(d, "revert", "--no-edit", "-m", "1", "HEAD")
        self.assertEqual(self.refusal(d, self.unit(d)), "")

    def test_a_revert_naming_a_commit_this_repository_lacks_is_no_refusal(self):
        d, _, _ = self.repo()
        self.git(d, "commit", "-q", "--allow-empty", "-m", "Revert elsewhere", "-m", "This reverts commit %s." % ("1" * 40))
        self.assertEqual(self.refusal(d, self.unit(d)), "")


class TheLanderWritesWhatTheCloserAccepts(unittest.TestCase):
    """land_batch.py main, as the loop runs it (the landings harness: a scratch tree, a bare upstream, the real lander):
    the pushed plan carries the landing_events key with one event for the landed sub unit naming the landing's base,
    so a unit landed after LOOP1.d never reads DISAGREE, and the closer's git check accepts it in that tree."""

    def test_a_real_landing_writes_the_event_the_closer_confirms(self):
        reason = sandbox_reason()   # the lander runs its checks under the sandbox, which cannot nest: NO-DATA by name
        if reason:
            self.skipTest("NO-DATA: %s" % reason)
        import close_unit
        import test_land_batch_landings as LL
        root = tempfile.mkdtemp(prefix="landing-facts-lander-")
        self.addCleanup(subprocess.run, ["rm", "-rf", root])
        code = os.path.join(root, "code")
        os.makedirs(os.path.join(code, "scripts"))
        with open(os.path.join(code, "scripts", "close_unit.py"), "w") as fh:
            fh.write(LL.CLOSE_UNIT_STUB)
        f = LL.Fixture(os.path.join(root, "f"), code_root=code)
        rc, out = f.land()
        self.assertEqual(rc, 0, out[-1500:])
        with open(os.path.join(f.tree, PLAN_REL), encoding="utf-8") as fh:
            unit = json.load(fh)["units"][0]
        self.assertIn("landing_events", unit)
        self.assertFalse(close_unit.legacy(unit), "a unit landed after LOOP1.d must never read LEGACY")
        got = plan_store.landing_facts(unit["landing_events"], LL.UNIT, LL.SUB)
        self.assertTrue(got["landed"], unit)
        self.assertEqual(got["commit"], f.base, "the event names the landing's base")
        self.assertEqual(close_unit.facts_refusal(unit), "")
        here = os.getcwd()
        os.chdir(f.tree)
        try:
            self.assertEqual(close_unit.landing_refusal(unit, PLAN_REL), "")
        finally:
            os.chdir(here)


class TheLockedWriteReadsTheEventsAgain(RepoFixture, unittest.TestCase):
    """Facts that change after the closer first read the plan. During the done check: the git confirmation reads the
    plan again after every check, so it sees them. After the git confirmation and before the locked write: only the
    lock's comparison with what git confirmed (landing_key) can see them."""

    def main_with_a_write_in_the_window(self, change, legacy=False):
        """close_unit.main(['U1']) in a committed landing, the done check faked to pass, and `change` applied to the plan
        immediately after the git confirmation returns, the narrowest window a concurrent writer has."""
        import io
        from unittest import mock
        import close_unit as C
        d, _, line = self.repo(legacy=legacy)
        p = os.path.join(d, PLAN_REL)
        real = C.landing_refusal

        def confirm_then_write(unit, *a):
            why = real(unit, *a)
            change(p, line, self.git(d, "rev-parse", "HEAD~1"))
            return why
        here = os.getcwd()
        os.chdir(d)
        try:
            with mock.patch.object(C, "PLAN", p), mock.patch.object(C, "run_done_check", return_value=(0, "PASS: faked")), \
                    mock.patch.object(C, "landing_refusal", confirm_then_write), \
                    mock.patch.object(C.provenance, "journal", return_value=True), \
                    mock.patch.dict(os.environ, {"BROTHER_EVIDENCE": d}), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as said:   # STAB1.f: never the real evidence directory
                code = C.main(["U1"])
        finally:
            os.chdir(here)
        return code, plan_store.load(p)["units"][0], said.getvalue()

    def test_a_revert_event_after_the_git_check_keeps_the_unit_open(self):
        def revert(p, line, base):
            plan_store.update_units(p, {"U1": lambda u: dict(u, landing_events=u["landing_events"] + [
                event(type="reverted", sequence=2, commit=base, evidence_id=eid(line))])})
        code, unit, said = self.main_with_a_write_in_the_window(revert)
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE", said)
        self.assertIn("re-read under the lock", said)

    def test_a_reland_after_the_git_check_names_a_landing_git_never_confirmed(self):
        """Still landed under the lock, so the pure readers agree; only the key differs from the confirmed one."""
        def reland(p, line, base):
            again = line + " Re-landed."
            plan_store.update_units(p, {"U1": lambda u: dict(u, evidence=u["evidence"] + " Re-landed.",
                                                             landing_events=u["landing_events"] + [
                                                                 event(sequence=2, commit=base, evidence_id=eid(again))])})
        code, unit, said = self.main_with_a_write_in_the_window(reland)
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE", said)
        self.assertIn("re-read under the lock", said)

    def test_a_legacy_unit_that_gains_an_events_key_in_the_window_stays_open(self):
        """An empty landing_events list keeps the key landing_key compares the same; only legacy's own comparison sees
        the unit stop being legacy."""
        def gain_key(p, line, base):
            plan_store.update_units(p, {"U1": lambda u: dict(u, landing_events=[])})
        code, unit, said = self.main_with_a_write_in_the_window(gain_key, legacy=True)
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE", said)
        self.assertIn("re-read under the lock", said)

    def test_a_revert_event_appended_during_the_check_keeps_the_unit_open(self):
        import glob, time
        reason = sandbox_reason()
        if reason:
            self.skipTest("NO-DATA: %s" % reason)
        d, _, line = self.repo()
        with open(os.path.join(d, "check.py"), "w") as fh:
            fh.write("import os, time\nopen('started', 'w').close()\nend = time.monotonic() + 30\n"
                     "while not os.path.exists('release'):\n    assert time.monotonic() < end\n    time.sleep(0.02)\n"
                     "print('PASS: waited')\n")
        self.git(d, "commit", "-q", "-am", "a check that waits")
        base = self.git(d, "rev-parse", "HEAD~2")
        p = os.path.join(d, PLAN_REL)
        proc = subprocess.Popen([sys.executable, "-B", CLOSER, "U1"], cwd=d, env=dict(os.environ, HOME=d, TMPDIR=d),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            end = time.monotonic() + 60
            while not glob.glob(os.path.join(d, "land-copy-*", "tree", "started")):
                if proc.poll() is not None:
                    self.fail("the closer ended before its done check started: %s" % "".join(proc.communicate())[-800:])
                self.assertLess(time.monotonic(), end)
                time.sleep(0.02)
            plan_store.update_units(p, {"U1": lambda u: dict(u, landing_events=u["landing_events"] + [
                event(type="reverted", sequence=2, commit=base, evidence_id=eid(line))])})
            copy = os.path.dirname(glob.glob(os.path.join(d, "land-copy-*", "tree", "started"))[0])
            with open(os.path.join(copy, "release"), "w"):
                pass
            out, err = proc.communicate(timeout=120)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        unit = plan_store.load(p)["units"][0]
        self.assertEqual(proc.returncode, 1, out + err)
        self.assertNotEqual(unit["state"], "DONE", out)
        self.assertIn("NOT CLOSED: after its done_check passed, DISAGREE U1.a: prose=landed events=not landed (reverted", out)


if __name__ == "__main__":
    unittest.main()

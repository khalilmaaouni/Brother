"""What serial integration must keep true.

The headline test is the directive's own acceptance scenario: two changes with
no git conflict that become semantically incompatible after the first lands.
Everything else guards the properties that make the unwind safe and the refusals
honest.
"""
import contextlib
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import integrate as I  # noqa: E402
import journal  # noqa: E402
import worktree_lane as W  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def canon(files=None):
    d = tempfile.mkdtemp(prefix="canon-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    for name, body in (files or {"lib.py": 'GREETING = "hello"\n'}).items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    run("add", "-A")
    run("commit", "-q", "-m", "R0")
    return d


def lane_commit(path, files, msg):
    for name, body in files.items():
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-qm", msg], cwd=path, capture_output=True, check=True)


def tip(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()


class TheAdvancingBaseScenario(unittest.TestCase):
    """A and B fork R0, both green locally, NO git conflict. A lands making R1.
    B must be re-verified ON R1, fail there, be unwound, and keep its work."""

    def setUp(self):
        self.repo = canon()
        self.lanes = W.Lanes(self.repo, ["A", "B"])
        lane_commit(self.lanes.path_for("A"),
                    {"lib.py": 'GREETING = "bonjour"\n'}, "A")
        lane_commit(self.lanes.path_for("B"),
                    {"b.py": 'import lib\nassert lib.GREETING == "hello"\n'}, "B")
        self.unitA = {"id": "A", "done_check": "grep -q bonjour lib.py"}
        self.unitB = {"id": "B", "done_check": "python3 b.py"}

    def test_the_full_arc(self):
        r0 = tip(self.repo)
        ra = I.integrate_one(self.repo, "lane/A", self.unitA)
        self.assertEqual(ra["verdict"], I.INTEGRATED)
        r1 = tip(self.repo)
        self.assertNotEqual(r0, r1)

        rb = I.integrate_one(self.repo, "lane/B", self.unitB)
        self.assertEqual(rb["verdict"], I.NEEDS_REPAIR)
        self.assertEqual(tip(self.repo), r1, "canonical must stand at A's revision")
        self.assertIn("clean merge is not semantic compatibility", rb["reason"])

    def test_B_would_have_integrated_first(self):
        """The incompatibility is ORDER-dependent, which is what makes this a
        base problem and not a bad unit: B is a perfectly good change on R0."""
        rb = I.integrate_one(self.repo, "lane/B", self.unitB)
        self.assertEqual(rb["verdict"], I.INTEGRATED)

    def test_the_unwound_work_is_preserved_in_its_lane(self):
        I.integrate_one(self.repo, "lane/A", self.unitA)
        I.integrate_one(self.repo, "lane/B", self.unitB)
        self.assertTrue(os.path.exists(
            os.path.join(self.lanes.path_for("B"), "b.py")))

    def test_the_repair_instruction_names_the_new_base(self):
        I.integrate_one(self.repo, "lane/A", self.unitA)
        rb = I.integrate_one(self.repo, "lane/B", self.unitB)
        self.assertIn(rb["canonical"][:9], rb["reason"])


class GreenMeansVerifiedOnCanonical(unittest.TestCase):
    def test_a_clean_integration_advances_canonical(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        before = tip(repo)
        r = I.integrate_one(repo, "lane/A", {"id": "A",
                                             "done_check": "test -f new.py"})
        self.assertEqual(r["verdict"], I.INTEGRATED)
        self.assertNotEqual(tip(repo), before)
        self.assertEqual(r["canonical"], tip(repo))

    def test_an_integrated_unit_carries_real_evidence(self):
        """Row E1: the record must carry the check itself, not a sentence
        about it. This is the seam brother_run's own refusal reads: the
        command, the captured exit code, the output, and the exact canonical
        revision the check ran against."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        r = I.integrate_one(repo, "lane/A", {"id": "A",
                                             "done_check": "echo checked; "
                                                           "test -f new.py"})
        ev = r["evidence"]
        self.assertEqual(ev["check_command"],
                         "echo checked; test -f new.py")
        self.assertEqual(ev["exit_code"], 0)
        self.assertIn("checked", ev["output"])
        self.assertEqual(ev["canonical_rev"], tip(repo))
        self.assertFalse(ev["output_truncated"])

    def test_output_is_truncated_to_the_tail_never_to_zero(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        check = ("python3 -c \"[print(i) for i in range(80)]\"; "
                "test -f new.py")
        r = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": check})
        ev = r["evidence"]
        self.assertTrue(ev["output_truncated"])
        self.assertEqual(len(ev["output"].splitlines()), 50)
        self.assertIn("79", ev["output"])
        self.assertNotIn("0\n1\n", ev["output"])

    def test_the_check_runs_on_canonical_not_the_lane(self):
        """A check that only passes in the lane must fail here."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        marker = os.path.join(lane_path, "lane-only-marker")
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("x")
        r = I.integrate_one(repo, "lane/A",
                            {"id": "A", "done_check": "test -f lane-only-marker"})
        self.assertEqual(r["verdict"], I.NEEDS_REPAIR)

    def test_a_missing_done_check_is_NO_DATA_and_unwound(self):
        """An unverifiable integration is the failure this module exists to
        prevent, so unknown blocks exactly as red does."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        before = tip(repo)
        r = I.integrate_one(repo, "lane/A", {"id": "A"})
        self.assertEqual(r["verdict"], I.NODATA)
        self.assertEqual(tip(repo), before, "the apply must be unwound")


class ConflictsAndRefusals(unittest.TestCase):
    def test_a_real_conflict_aborts_and_canonical_stands(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"lib.py": 'GREETING = "lane"\n'}, "A")
        # canonical moves on the same line after the lane forked
        with open(os.path.join(repo, "lib.py"), "w", encoding="utf-8") as fh:
            fh.write('GREETING = "canonical moved"\n')
        subprocess.run(["git", "commit", "-qam", "canonical moved"], cwd=repo,
                       capture_output=True, check=True)
        before = tip(repo)
        r = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "true"})
        self.assertEqual(r["verdict"], I.CONFLICT)
        self.assertEqual(tip(repo), before)
        self.assertTrue(os.path.exists(
            os.path.join(lanes.path_for("A"), "lib.py")))

    def test_bytecode_on_canonical_does_not_refuse_the_next_unit(self):
        """Running a unit's check ON canonical leaves __pycache__ behind, and
        the guard refusing the NEXT unit for that starved a correct
        integration live (2026-08-30). Bytecode is not somebody's work."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        os.makedirs(os.path.join(repo, "__pycache__"), exist_ok=True)
        with open(os.path.join(repo, "__pycache__", "junk.cpython-313.pyc"),
                  "wb") as fh:
            fh.write(b"\x00")
        r = I.integrate_one(repo, "lane/A", {"id": "A",
                                             "done_check": "test -f new.py"})
        self.assertEqual(r["verdict"], I.INTEGRATED)

    def test_a_dirty_canonical_is_refused_before_anything_happens(self):
        """Canonical is integration-only ground: a dirty tree is already a rule
        violation, and integrating over it would bury somebody's work."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        with open(os.path.join(repo, "uncommitted.txt"), "w", encoding="utf-8") as fh:
            fh.write("somebody's work\n")
        r = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "true"})
        self.assertEqual(r["verdict"], I.REFUSED)
        self.assertTrue(os.path.exists(os.path.join(repo, "uncommitted.txt")))

    def test_truth_is_serial_a_held_lock_makes_the_second_wait_not_race(self):
        repo = canon()
        lock_path = os.path.join(repo, ".git", I.LOCK_NAME)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("held")
        with self.assertRaises(TimeoutError):
            with I._Lock(repo, timeout=0.2):
                pass
        os.unlink(lock_path)

    def test_the_lock_works_from_a_linked_worktree_and_is_shared(self):
        """In a linked worktree .git is a FILE, so the old repo/.git join died
        with NotADirectoryError (live failure, first loop integration from a
        worktree, 2026-08-30). The lock must both acquire there and be THE
        SAME lock as the primary checkout's, because truth is serial per
        repository, not per checkout."""
        repo = canon()
        wt = repo + "-wt"
        r = subprocess.run(["git", "-C", repo, "worktree", "add", "-b",
                            "lock-wt", wt], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        main_lock = I._Lock(repo)
        wt_lock = I._Lock(wt)
        self.assertEqual(main_lock.path, wt_lock.path)
        with wt_lock:
            self.assertTrue(os.path.exists(wt_lock.path))
        self.assertFalse(os.path.exists(wt_lock.path))


class ADeadIntegrationLockHolderIsReclaimedNotWaitedOut(unittest.TestCase):
    """CONT-0, boundary during_integration: a real SIGKILL mid integrate_one
    (fault_lab.scenario_boundary) left the .integration.lock file behind
    (a SIGKILL cannot run _Lock.__exit__), and a follow-up acquire waited
    out the full 300s LOCK_TIMEOUT instead of reclaiming at once. Same fix,
    same pattern as claim_store.py's own ADeadLockHolderIsReclaimedNotWaitedOut
    (scripts/test_claim_store.py): a lock left by a dead process on THIS
    host is reclaimed within seconds; malformed content or another host's
    pid keeps the original plain-wait behavior unchanged."""

    def _spawn_locked_then_die(self, repo):
        code = ("import sys; sys.path.insert(0, %r);"
               "import integrate as I;"
               "lock = I._Lock(%r);"
               "lock.__enter__();"
               "print('locked', flush=True);"
               "import time; time.sleep(60)"
               % (os.path.dirname(os.path.abspath(__file__)), repo))
        proc = subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.PIPE, text=True)
        self.assertTrue(proc.stdout.readline(), "the lock was never taken")
        proc.stdout.close()
        proc.kill()
        proc.wait(timeout=10)
        return proc.pid

    def test_a_lock_left_by_a_dead_process_is_reclaimed_well_before_timeout(self):
        repo = canon()
        dead_pid = self._spawn_locked_then_die(repo)

        stderr = io.StringIO()
        start = time.time()
        with contextlib.redirect_stderr(stderr):
            with I._Lock(repo, timeout=30.0):
                pass
        elapsed = time.time() - start

        self.assertLess(elapsed, 5.0,
                        "a dead holder's integration lock should be "
                        "reclaimed in seconds, not waited out over the "
                        "full timeout")
        self.assertIn(str(dead_pid), stderr.getvalue())
        self.assertIn("reclaiming", stderr.getvalue())

    def test_a_lock_held_by_a_live_process_is_never_stolen(self):
        repo = canon()
        code = ("import sys, time; sys.path.insert(0, %r);"
               "import integrate as I;"
               "lock = I._Lock(%r);"
               "lock.__enter__();"
               "print('locked', flush=True);"
               "time.sleep(5)"
               % (os.path.dirname(os.path.abspath(__file__)), repo))
        proc = subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertTrue(proc.stdout.readline(), "the lock was never taken")
            with self.assertRaises(TimeoutError):
                with I._Lock(repo, timeout=0.3):
                    pass
        finally:
            proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()

    def test_a_lock_file_with_garbage_content_keeps_the_timeout_behavior(self):
        """Unreadable or malformed content must never be guessed at: fall
        back to the plain wait, exactly as this file's own pre-existing
        test_truth_is_serial_a_held_lock_makes_the_second_wait_not_race
        already pins for its literal "held" content.

        REPAIR C3: retention is never silent either, so an unparseable
        record prints why the lock was kept, matching this module's
        existing "reclaiming is never silent" rule for the opposite
        decision."""
        repo = canon()
        lock_path = os.path.join(repo, ".git", I.LOCK_NAME)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("not a pid at all")
        import io
        from contextlib import redirect_stderr
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(TimeoutError):
                with I._Lock(repo, timeout=0.3):
                    pass
        self.assertTrue(os.path.exists(lock_path),
                        "a lock this code cannot trust must not be removed")
        self.assertIn("retain", stderr.getvalue().lower(),
                      "an unparseable record must print why it was kept")
        os.unlink(lock_path)

    def test_a_lock_naming_another_host_keeps_the_timeout_behavior(self):
        """The dead pid proves the ORDER: pid_alive(dead.pid) would say
        False (reclaim) if it ran first, so a record that is kept here
        proves the hostname check ran BEFORE any pid test."""
        repo = canon()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock_path = os.path.join(repo, ".git", I.LOCK_NAME)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d:some-other-host-entirely" % dead.pid)
        with self.assertRaises(TimeoutError):
            with I._Lock(repo, timeout=0.3):
                pass
        self.assertTrue(os.path.exists(lock_path),
                        "a lock from another host must not be reclaimed by "
                        "pid alone")
        os.unlink(lock_path)


class TheLockClassIsSharedByClaimStoreAndIntegrate(unittest.TestCase):
    """C6 REPAIR consolidation round: integrate._Lock used to be a second,
    verbatim copy of claim_store._Lock's whole O_EXCL/_reclaim_if_dead
    mechanism (both docstrings even said "mirrors ... exactly"). It is now
    a subclass, so this pins that both callers still reach the SAME
    dead-holder decision (reclaim a lock whose owning pid is gone; retain
    one that is live, unreadable, or on another host -- already proven per
    module by this file's own ADeadIntegrationLockHolderIsReclaimedNotWaitedOut
    and test_claim_store.py's ADeadLockHolderIsReclaimedNotWaitedOut) while
    each keeps the one side effect that was always its own: claim_store's
    lock has always written a "claim.reclaimed" journal event on reclaim
    (E59, reclaiming is never silent even after the terminal is gone).

    M-4: integrate's lock used to never journal at all, because nothing
    told it which run directory to write into -- an integration lock lives
    in a repository's .git directory, never inside a run's own journal
    directory. It now CAN journal, exactly like the claim store's lock,
    when a run directory is resolvable (journal.run_dir_from_env(), read
    through the overridden _journal_run_dir hook below), and stays silent
    in the journal (the stderr line still prints either way) when none is
    -- proven by both cases below."""

    def test_integrate_lock_is_a_claim_store_lock(self):
        import claim_store as C
        self.assertTrue(issubclass(I._Lock, C._Lock))

    def test_claim_store_lock_still_journals_its_own_reclaim(self):
        import claim_store as C
        p = os.path.join(tempfile.mkdtemp(prefix="lock-share-claims-"),
                         "claims.json")
        run_dir = os.path.dirname(p)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock_path = p + ".lock"
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d:%s" % (dead.pid, C._hostname()))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with C._Lock(p, timeout=5.0):
                pass
        self.assertIn("reclaiming", stderr.getvalue())
        journal_path = os.path.join(run_dir, "journal.jsonl")
        self.assertTrue(os.path.exists(journal_path),
                        "claim_store's own lock reclaim must still journal, "
                        "unchanged by becoming the shared implementation")
        with open(journal_path, encoding="utf-8") as fh:
            self.assertIn("claim.reclaimed", fh.read())

    def test_integrate_lock_reclaims_the_same_way_and_stays_silent_with_no_run_dir(self):
        """M-4: with no run directory exported, journal.run_dir_from_env()
        reads "" and journal.append() treats that as nothing to write --
        this is the case that used to be "integrate's lock never
        journals", and it still holds, just for a different reason now
        (no resolvable run directory, not a hardcoded JOURNAL_ON_RECLAIM
        of False)."""
        import claim_store as C
        repo = canon()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock_path = os.path.join(repo, ".git", I.LOCK_NAME)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d:%s" % (dead.pid, C._hostname()))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with I._Lock(repo, timeout=5.0):
                pass
        self.assertIn("reclaiming", stderr.getvalue(),
                      "integrate's lock must reach the SAME dead-holder "
                      "decision claim_store's own lock does")
        journal_path = os.path.join(repo, ".git", "journal.jsonl")
        self.assertFalse(os.path.exists(journal_path),
                         "with no run directory exported, integrate's own "
                         "lock reclaim must not journal, and must not "
                         "raise")

    def test_integrate_lock_journals_its_reclaim_when_a_run_directory_is_set(self):
        """M-4: with a run directory exported, integrate's lock reclaim
        must write the same "claim.reclaimed" event the claim store's own
        lock writes, naming the lock it reclaimed -- journal.run_dir_from_env()
        is the same resolver integrate_one's own "integrate.merged" write
        already reads, threaded through _journal_run_dir()."""
        import claim_store as C
        repo = canon()
        run_dir = tempfile.mkdtemp(prefix="integrate-lock-run-dir-")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock_path = os.path.join(repo, ".git", I.LOCK_NAME)
        with open(lock_path, "w", encoding="utf-8") as fh:
            fh.write("%d:%s" % (dead.pid, C._hostname()))
        prior = os.environ.get(journal.RUN_DIR_ENV_VAR)
        os.environ[journal.RUN_DIR_ENV_VAR] = run_dir
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with I._Lock(repo, timeout=5.0):
                    pass
        finally:
            if prior is None:
                os.environ.pop(journal.RUN_DIR_ENV_VAR, None)
            else:
                os.environ[journal.RUN_DIR_ENV_VAR] = prior
        self.assertIn("reclaiming", stderr.getvalue())
        journal_path = os.path.join(run_dir, "journal.jsonl")
        self.assertTrue(os.path.exists(journal_path),
                        "with a run directory exported, integrate's own "
                        "lock reclaim must journal it, same as the claim "
                        "store's lock does")
        with open(journal_path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("claim.reclaimed", body)
        self.assertIn(I.LOCK_NAME, body)


class TheBatchRespectsTheScopeGate(unittest.TestCase):
    def test_a_quarantined_result_is_refused_not_integrated(self):
        repo = canon()
        out = I.integrate(repo,
                          [{"id": "A", "integrable": False,
                            "integration_block": "QUARANTINE: wrote outside scope"}],
                          {"A": "lane/A"}, {"A": {"id": "A"}})
        self.assertEqual(out[0]["verdict"], I.REFUSED)
        self.assertIn("QUARANTINE", out[0]["reason"])

    def test_a_result_with_no_lane_is_NO_DATA(self):
        repo = canon()
        out = I.integrate(repo, [{"id": "A", "integrable": True}], {}, {})
        self.assertEqual(out[0]["verdict"], I.NODATA)

    def test_nothing_in_this_module_deletes_anything_but_a_finished_lane(self):
        """cleanup_lane() (2026-09-02) is the one thing this module now
        deletes, and it must refuse everything that is not a lane branch
        it or worktree_lane created: never main, never a human's branch."""
        for name in ("delete", "remove", "prune", "discard"):
            self.assertFalse(hasattr(I, name), name)
        repo = canon()
        removed, detail = I.cleanup_lane(repo, "main", "A")
        self.assertFalse(removed)
        self.assertIn(I.NODATA, detail)
        removed, detail = I.cleanup_lane(repo, "feature/not-a-lane", "A")
        self.assertFalse(removed)
        self.assertIn(I.NODATA, detail)


class TheRecoveryResolver(unittest.TestCase):
    """The 2026-08-31 crash measurement recorded a wart: the resume re-claimed a unit
    that was already integrated and ran a worker for it again. The re-merge was a no-op
    and the record read clean, but it was clean by luck, because the model happened to
    write nothing. A model writing a different valid implementation would have advanced
    canonical twice for one unit.

    Both directions here. A lane already in canonical must be reported as such and must
    NOT merge again; a lane not yet in canonical must integrate normally, or the
    resolver would have bought safety by refusing real work."""

    def test_a_lane_already_in_canonical_is_reported_not_merged_again(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        first = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED)
        after_first = tip(repo)

        second = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "test -f new.py"})

        self.assertEqual(second["verdict"], I.ALREADY_INTEGRATED)
        self.assertIn("already an ancestor", second["reason"])
        self.assertEqual(tip(repo), after_first,
                         "canonical must not move for a unit that was already in")

    def test_a_lane_not_yet_in_canonical_still_integrates(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        before = tip(repo)
        out = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(out["verdict"], I.INTEGRATED, out.get("reason"))
        self.assertNotEqual(tip(repo), before)

    def test_a_lane_that_has_committed_nothing_is_NOT_already_integrated(self):
        """THE CASE THAT FOOLED THE FIRST VERSION, and it was a live defect for a
        day. brother_run creates lane/<unit> at canonical's tip when it claims the
        unit, so between the claim and the worker's first commit the lane IS the
        tip. The original check asked merge-base --is-ancestor, which is trivially
        true there, so a resume in that window reported ALREADY-INTEGRATED for a
        unit nobody had integrated and the run then failed with no evidence
        recorded."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])          # creates lane/A at the tip
        # No lane_commit: the worker has not written anything yet.
        self.assertFalse(I._already_integrated(repo, "lane/A"),
                         "an empty lane sitting at canonical's tip has integrated "
                         "nothing and must not be reported as already integrated")
        del lanes

    def test_an_empty_lane_beside_a_merged_sibling_is_still_NOT_already_integrated(self):
        """The second false positive (E41's zero-change fixture, 2026-09-03):
        an empty lane's tip IS the fork base, and the base becomes the FIRST
        parent of the first sibling merge that lands after it, so "the lane
        tip is a parent of some reachable commit" read True for a unit nobody
        had merged, released it done with no evidence, and the verifier
        refused it. A --no-ff merge only ever makes a lane tip a SECOND
        parent, which is the exact signal."""
        repo = canon()
        lanes = W.Lanes(repo, ["A", "N"])
        lane_commit(lanes.path_for("A"), {"a.py": "x = 1\n"}, "A")
        # lane/N commits nothing: its tip is the base A forked from too.
        first = I.integrate_one(repo, "lane/A", {"id": "A",
                                                 "done_check": "test -f a.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        self.assertFalse(I._already_integrated(repo, "lane/N"))
        second = I.integrate_one(repo, "lane/N", {"id": "N",
                                                  "done_check": "true"})
        self.assertEqual(second["verdict"], I.INTEGRATED, second.get("reason"))
        self.assertEqual(second["evidence"]["files_changed"], [])
        del lanes

    def test_a_lane_merged_long_ago_is_still_recognized(self):
        """The other half: once merged, the lane must keep reading as integrated
        even after canonical advances past it, or a resume would merge it twice."""
        repo = canon()
        lanes = W.Lanes(repo, ["A", "B"])
        lane_commit(lanes.path_for("A"), {"a.py": "x = 1\n"}, "A")
        lane_commit(lanes.path_for("B"), {"b.py": "y = 2\n"}, "B")
        first = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "test -f a.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        second = I.integrate_one(repo, "lane/B", {"id": "B", "done_check": "test -f b.py"})
        self.assertEqual(second["verdict"], I.INTEGRATED, second.get("reason"))
        # Canonical has moved on past A's merge; A must still read as integrated.
        self.assertTrue(I._already_integrated(repo, "lane/A"))

    def test_an_unreadable_repository_answers_not_integrated(self):
        """The safe direction: answering True on a git error would silently skip an
        integration that never happened."""
        self.assertFalse(I._already_integrated("/no/such/repo", "lane/A"))


class TheStopControl(unittest.TestCase):
    """A human must be able to halt autonomous integration without killing a
    process mid-merge. Driven BOTH ways in one test, because a stop that refuses
    everything forever is not a brake, it is a broken tool: the same lane that is
    refused under the stop file must integrate once the file is gone."""

    def test_a_stop_file_refuses_the_merge_and_canonical_does_not_move(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_commit(lanes.path_for("A"), {"new.py": "x = 1\n"}, "A")
        before = tip(repo)
        stop = os.path.join(repo, I.STOP_FILE)
        with open(stop, "w", encoding="utf-8") as fh:
            fh.write("halted by the founder while the release is cut\n")

        out = I.integrate_one(repo, "lane/A",
                              {"id": "A", "done_check": "test -f new.py"})

        self.assertEqual(out["verdict"], I.REFUSED)
        self.assertIn(I.STOP_FILE, out["reason"])
        self.assertIn("halted by the founder", out["reason"])
        self.assertEqual(tip(repo), before,
                         "canonical must not move while integration is stopped")

        # AND BACK: remove the stop, the very same lane integrates.
        os.unlink(stop)
        out2 = I.integrate_one(repo, "lane/A",
                               {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(out2["verdict"], I.INTEGRATED, out2.get("reason"))
        self.assertNotEqual(tip(repo), before)

    def test_an_unreadable_stop_file_still_stops(self):
        """The presence is the signal, the text is only the explanation, so a
        read error must never read as permission to merge."""
        repo = canon()
        os.mkdir(os.path.join(repo, I.STOP_FILE))
        reason = I._stop_reason(repo)
        self.assertIsNotNone(reason)
        self.assertIn("STOPPED", reason)


def _worktree_paths(repo):
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                         capture_output=True, text=True).stdout
    return [l[len("worktree "):] for l in out.splitlines()
            if l.startswith("worktree ")]


def _branch_exists(repo, branch):
    r = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                        "refs/heads/" + branch], cwd=repo,
                       capture_output=True, text=True)
    return r.returncode == 0


class TheLaneCleanup(unittest.TestCase):
    """The parity fix's other half, 2026-09-02: a git worktree per unit was
    created and never removed, so a finished run left `lane/<unit>` and its
    worktree on disk. A second run of the same unit then found the stale
    branch and could reuse it, contaminating a fresh attempt with a dead
    run's commits. integrate() must retire a unit's lane once its round is
    decided, whichever way it went, without ever changing the verdict."""

    def test_an_integrated_unit_leaves_no_lane_worktree_or_branch(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        out = I.integrate(repo, [{"id": "A", "integrable": True}],
                          {"A": "lane/A"},
                          {"A": {"id": "A", "done_check": "test -f new.py"}})
        self.assertEqual(out[0]["verdict"], I.INTEGRATED, out[0].get("reason"))
        self.assertFalse(_branch_exists(repo, "lane/A"))
        self.assertNotIn(lane_path, _worktree_paths(repo))
        self.assertFalse(os.path.isdir(lane_path))

    def test_a_refused_units_lane_is_cleaned_too(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        out = I.integrate(repo, [{"id": "A", "integrable": False,
                                  "integration_block": "QUARANTINE: scope"}],
                          {"A": "lane/A"}, {"A": {"id": "A"}})
        self.assertEqual(out[0]["verdict"], I.REFUSED)
        self.assertFalse(_branch_exists(repo, "lane/A"))
        self.assertFalse(os.path.isdir(lane_path))

    def test_a_needs_repair_units_unmerged_lane_is_retained(self):
        """A failed revalidation preserves the committed lane for a named
        reconciliation. A fresh retry may later use another lane, but this
        cleanup boundary must not decide that the old work is disposable."""
        repo = canon()
        lanes = W.Lanes(repo, ["A", "B"])
        lane_commit(lanes.path_for("A"),
                   {"lib.py": 'GREETING = "bonjour"\n'}, "A")
        lane_commit(lanes.path_for("B"),
                   {"b.py": 'import lib\nassert lib.GREETING == "hello"\n'}, "B")
        I.integrate_one(repo, "lane/A",
                        {"id": "A", "done_check": "grep -q bonjour lib.py"})
        b_path = lanes.path_for("B")
        out = I.integrate(repo, [{"id": "B", "integrable": True}],
                          {"B": "lane/B"},
                          {"B": {"id": "B", "done_check": "python3 b.py"}})
        self.assertEqual(out[0]["verdict"], I.NEEDS_REPAIR)
        self.assertTrue(_branch_exists(repo, "lane/B"))
        self.assertTrue(os.path.isdir(b_path))

    def test_a_clean_unmerged_lane_is_retained(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        removed, detail = I.cleanup_lane(repo, "lane/A", "A")
        self.assertFalse(removed)
        self.assertIn("not contained", detail)
        self.assertTrue(_branch_exists(repo, "lane/A"))
        self.assertTrue(os.path.isdir(lane_path))

    def test_a_dirty_contained_lane_is_retained(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        with open(os.path.join(lane_path, "lost.txt"), "w", encoding="utf-8") as fh:
            fh.write("not committed\n")
        removed, detail = I.cleanup_lane(repo, "lane/A", "A")
        self.assertFalse(removed)
        self.assertIn("uncommitted", detail)
        self.assertTrue(_branch_exists(repo, "lane/A"))
        self.assertTrue(os.path.isdir(lane_path))

    def test_a_worktree_that_never_got_its_own_branch_is_never_reported_removed(self):
        """REPAIR C6 (2026-09-09 adversarial review of lane/continuity,
        round 2): worktree_lane.acquire sets branch=None when `checkout -b`
        fails right after `worktree add` already succeeded
        (worktree_lane.py, acquire()): the worktree itself is real, on
        disk, detached, and still registered with git. loop_bridge.py's
        own lane_branches dict is computed UNCONDITIONALLY from the unit
        id (its own CONT-0 U3 comment), never from acquire()'s actual
        return, so cleanup_lane is still called with the sanitized branch
        name that was never actually created.

        Before this repair, cleanup_lane's "not path and not
        branch_exists" case (the branch-keyed _lane_worktree_path lookup
        finds nothing for a detached worktree, and the branch genuinely
        never existed) read this as nothing left: it journaled
        lane.cleaned removed=True resumed_teardown=True and printed
        "removed", while the directory, its git registration and any
        uncommitted worker file inside it all remained untouched. A
        NO-DATA had become a PASS on exactly the path a failed lane
        acquisition exists for."""
        repo = canon()
        real = subprocess.run
        # worktree_lane._git() never forwards `cwd` to a caller-supplied
        # runner (only its own DEFAULT runner closes over it), so a custom
        # runner has to know each call's intended cwd itself. root is
        # fixed here so lane_path is known BEFORE acquire() runs, letting
        # this route every call correctly: "checkout -b" is faked (the
        # scenario), the breadcrumb's "rev-parse --git-dir" runs inside
        # the lane path, and everything else (worktree add/list, the
        # stale-lane check) runs in `repo`, exactly where acquire() itself
        # would have run it.
        lane_root = tempfile.mkdtemp(prefix="c6-lane-root-")
        lane_path = os.path.join(lane_root, "U3")

        def checkout_b_fails(cmd, **kw):
            if "checkout" in cmd and "-b" in cmd:
                class _F:
                    returncode, stdout, stderr = (
                        1, "", "monkeypatched checkout -b failure")
                return _F()
            if "--git-dir" in cmd:
                return real(cmd, capture_output=True, text=True, cwd=lane_path)
            return real(cmd, capture_output=True, text=True, cwd=repo)

        lane_path, branch, problem = W.acquire(repo, "U3", root=lane_root,
                                               runner=checkout_b_fails)
        self.assertIsNone(branch, "the scenario needs acquire's own "
                                  "branch=None path: %r" % problem)
        self.assertTrue(os.path.isdir(lane_path),
                        "worktree add must still have succeeded")
        # realpath on both sides: macOS's own /var -> /private/var symlink
        # (the same class of mismatch _transcript_slug's docstring names)
        # means git's own worktree list can print a different string for
        # the identical tree tempfile.mkdtemp() handed back.
        registered = [os.path.realpath(p) for p in _worktree_paths(repo)]
        self.assertIn(os.path.realpath(lane_path), registered,
                     "git must still show the detached worktree registered")

        # loop_bridge.py computes this from the unit id alone, never from
        # acquire()'s real return: the exact mismatch this repair closes.
        computed_branch = W.BRANCH_PREFIX + "U3"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            removed, detail = I.cleanup_lane(repo, computed_branch, "U3")

        self.assertFalse(removed, detail)
        self.assertIn(os.path.realpath(lane_path), detail)
        self.assertTrue(os.path.isdir(lane_path),
                        "the worktree itself must not be reported gone")
        registered_after = [os.path.realpath(p) for p in _worktree_paths(repo)]
        self.assertIn(os.path.realpath(lane_path), registered_after,
                     "git's own registration for it must not be reported "
                     "gone")
        printed = buf.getvalue()
        status_line = next(l for l in printed.splitlines()
                           if "lane-cleanup" in l and "U3" in l)
        # The prose reason legitimately contains the word "removed" ("...
        # reported removed on a guess"), so this reads the STATUS WORD
        # cleanup_lane prints in its own fixed column ("lane-cleanup <id>
        # <status> <detail>"), never a bare substring search over the
        # whole printed line.
        self.assertEqual(status_line.split()[2], "kept", status_line)
        self.assertNotIn("lane-cleanup U3   removed", printed)

    def test_a_cleanup_failure_never_changes_the_verdict_and_names_the_path(self):
        """The estate's own rule: cleanup must never fail a finished proof.
        A monkeypatched git call makes the worktree removal fail; the
        unit's INTEGRATED verdict and its evidence must stand untouched,
        and the failure names the path in the run's own printed log."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")

        real = subprocess.run

        def failing_runner(cmd, **kw):
            if "remove" in cmd:
                class _F:
                    returncode, stdout, stderr = 1, "", "monkeypatched failure"
                return _F()
            return real(cmd, capture_output=True, text=True, cwd=repo, timeout=300)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = I.integrate(repo, [{"id": "A", "integrable": True}],
                              {"A": "lane/A"},
                              {"A": {"id": "A", "done_check": "test -f new.py"}},
                              runner=failing_runner)
        rec = out[0]
        self.assertEqual(rec["verdict"], I.INTEGRATED, rec.get("reason"))
        self.assertEqual(rec["evidence"]["exit_code"], 0)
        self.assertIn(lane_path, buf.getvalue(),
                     "a cleanup failure must log the path it could not clear")
        self.assertIn("kept", buf.getvalue())


class TheKillBetweenTheTwoDeletionStepsLeavesOneNextAction(unittest.TestCase):
    """CONT-0, U3, the reviewer's demand: cleanup_lane's own teardown is two
    git mutations (worktree remove, branch delete). A real kill can land
    between them, exactly the way fault_lab.scenario_boundary("during_
    integration") lands a real SIGKILL mid integrate_one. A retry must read
    the half-finished lane as ONE next action, not the confusing "no
    registered worktree, cannot prove cleanliness" this function used to
    say, and must never attempt the already-finished half again.

    REPAIR C10 (2026-09-09 adversarial review, round 3): "already fully
    removed" is now proven from THIS run's own journal (a real acquire
    mark plus a real completed-teardown mark), not merely inferred from
    "no worktree, no branch" -- so this class runs under a real journal,
    exactly the pattern test_worktree_lane.py's
    TheLaneReuseProofNeedsAllFiveSignals already uses for the same
    reason."""

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="kill-between-run-")
        self._prior_env = os.environ.get(journal.RUN_DIR_ENV_VAR)
        os.environ[journal.RUN_DIR_ENV_VAR] = self.run_dir

    def tearDown(self):
        if self._prior_env is None:
            os.environ.pop(journal.RUN_DIR_ENV_VAR, None)
        else:
            os.environ[journal.RUN_DIR_ENV_VAR] = self._prior_env
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_worktree_already_gone_finishes_the_branch_with_one_action(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        first = I.integrate_one(repo, "lane/A",
                                {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))

        # THE KILL, SIMULATED: the worktree half of the teardown already
        # ran (as a real prior cleanup_lane attempt would leave it), the
        # branch half did not.
        r = subprocess.run(["git", "worktree", "remove", lane_path],
                           cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(_branch_exists(repo, "lane/A"))
        self.assertFalse(os.path.isdir(lane_path))

        # A GUARD RUNNER: a second `worktree remove` for this path would be
        # exactly the "second deletion attempt" this test refuses to allow.
        real = subprocess.run

        def guard_runner(cmd, **kw):
            if "remove" in cmd and lane_path in cmd:
                self.fail("cleanup_lane attempted a second worktree remove "
                         "for a worktree that was already gone: %r" % cmd)
            return real(cmd, capture_output=True, text=True, cwd=repo, timeout=300)

        removed, detail = I.cleanup_lane(repo, "lane/A", "A", runner=guard_runner)
        self.assertTrue(removed, detail)
        self.assertIn("already gone", detail)
        self.assertFalse(_branch_exists(repo, "lane/A"))

    def test_both_halves_already_gone_reads_as_the_same_terminal_fact(self):
        """A retry of a cleanup that already fully finished (or ran twice)
        must never look like a fresh deletion."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        first = I.integrate_one(repo, "lane/A",
                                {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        removed_once, _ = I.cleanup_lane(repo, "lane/A", "A")
        self.assertTrue(removed_once)
        self.assertFalse(_branch_exists(repo, "lane/A"))
        self.assertFalse(os.path.isdir(lane_path))

        removed_again, detail = I.cleanup_lane(repo, "lane/A", "A")
        self.assertTrue(removed_again)
        self.assertIn("already", detail)

    def test_a_branch_that_was_never_acquired_reads_as_no_data_not_removed(self):
        """REPAIR C10 (2026-09-09 adversarial review, round 3): a branch
        that merely carries LANE_BRANCH_PREFIX but was NEVER created by
        worktree_lane.acquire (no worktree, no ref, and -- the fact this
        repair adds -- no journal record of it ever being acquired) is not
        "already fully removed by an earlier attempt": nothing was ever
        there to remove. Driven with lane/NEVER-EXISTED, exactly the
        branch name the adversarial review used: before this repair,
        cleanup_lane called this case done and journaled
        lane.cleaned removed=True."""
        repo = canon()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            removed, detail = I.cleanup_lane(repo, "lane/NEVER-EXISTED",
                                             "NEVER-EXISTED")
        self.assertFalse(removed, detail)
        self.assertIn("no record of this lane", detail)
        self.assertIn(I.NODATA, detail)
        self.assertNotIn("already fully removed", detail)
        printed = buf.getvalue()
        status_line = next(l for l in printed.splitlines()
                           if "lane-cleanup" in l and "NEVER-EXISTED" in l)
        self.assertEqual(status_line.split()[2], "no-data", status_line)
        # The journal itself must agree with the return value: no event
        # this repair writes may claim removed=True for a lane that was
        # never acquired.
        events = journal.read(self.run_dir) or []
        mine = [e for e in events if e.get("unit_id") == "NEVER-EXISTED"]
        self.assertTrue(mine, "the NO-DATA outcome must still be journaled")
        self.assertFalse(mine[-1]["payload"]["removed"])

    def test_a_branch_genuinely_acquired_and_torn_down_still_reads_removed(self):
        """REPAIR C10's other half: the journal-backed proof must not
        make a GENUINE prior teardown unrecognizable. lane/A here really
        was acquired (W.Lanes -> worktree_lane.acquire, journaled because
        this class's setUp now points BROTHER_RUN_DIR at a real journal)
        and really was torn down by the first cleanup_lane call; the
        second call must still read "already fully removed", proven by
        the journal holding both the acquire and the completed-teardown
        marks."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        first = I.integrate_one(repo, "lane/A",
                                {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        events_before = journal.read(self.run_dir) or []
        self.assertTrue(any(e.get("type") == "lane.acquired"
                            and e.get("unit_id") == "A"
                            for e in events_before),
                        "acquire() must have journaled this lane for the "
                        "proof to have anything to find")
        removed_once, _ = I.cleanup_lane(repo, "lane/A", "A")
        self.assertTrue(removed_once)
        removed_again, detail = I.cleanup_lane(repo, "lane/A", "A")
        self.assertTrue(removed_again, detail)
        self.assertIn("already fully removed", detail)

    def test_branch_delete_failure_names_the_flag_actually_run(self):
        """REPAIR C11 (2026-09-09 adversarial review, round 3): the
        ordinary teardown runs `git branch -d`, never `-D`; a failure
        string that said "-D failed" named a flag this code has never
        passed. Driven by monkeypatching that one git call to fail."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        # branch -d only refuses a branch NOT (yet) merged into HEAD, so
        # the branch must actually be contained in canonical (an ordinary
        # integrate_one) before the monkeypatched delete failure below is
        # the thing this test is actually driving, not a merge-base
        # refusal instead.
        first = I.integrate_one(repo, "lane/A",
                                {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        real = subprocess.run

        def branch_delete_fails(cmd, **kw):
            if cmd[:3] == ["git", "branch", "-d"]:
                class _F:
                    returncode, stdout, stderr = (
                        1, "", "monkeypatched branch -d failure")
                return _F()
            return real(cmd, capture_output=True, text=True,
                        cwd=repo, timeout=300)

        removed, detail = I.cleanup_lane(repo, "lane/A", "A",
                                         runner=branch_delete_fails)
        self.assertFalse(removed, detail)
        self.assertIn("branch -d failed", detail)
        self.assertNotIn("-D failed", detail)

    def test_a_worktree_gone_branch_not_yet_contained_is_retained_not_deleted(self):
        """The half-torn-down branch is still subject to the same safety
        rule as an ordinary uncommitted lane: never delete a branch not
        (yet) contained in canonical, even with no working tree left to
        check for dirtiness."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        # NEVER integrated: lane/A's own commit is not in canonical.
        r = subprocess.run(["git", "worktree", "remove", "--force", lane_path],
                           cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(_branch_exists(repo, "lane/A"))

        removed, detail = I.cleanup_lane(repo, "lane/A", "A")
        self.assertFalse(removed)
        self.assertIn("not (yet) contained", detail)
        self.assertTrue(_branch_exists(repo, "lane/A"))

    def test_the_ancestor_check_is_re_verified_at_deletion_time(self):
        """A stale entry-time check must never authorize a branch delete
        that would be wrong by the time it actually runs: canonical can
        move between the two, because cleanup_lane holds no lock of its
        own (integrate_one's own lock is already released by the time
        integrate() calls this). Proven by advancing canonical to a
        SIBLING commit, off lane/A's own history, from inside the runner
        right after the entry-time check passes but before the branch
        delete -- the exact race window this closes."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        first = I.integrate_one(repo, "lane/A",
                                {"id": "A", "done_check": "test -f new.py"})
        self.assertEqual(first["verdict"], I.INTEGRATED, first.get("reason"))
        base_tip = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=repo,
                                  capture_output=True, text=True).stdout.strip()

        real = subprocess.run
        calls = {"ancestor_checks": 0}

        def rewinding_runner(cmd, **kw):
            if "merge-base" in cmd and "--is-ancestor" in cmd:
                calls["ancestor_checks"] += 1
                if calls["ancestor_checks"] == 2:
                    # THE RACE: canonical is reset to a revision that does
                    # NOT contain lane/A's merge, between the entry check
                    # (call 1, which must still pass, so cleanup proceeds
                    # to the worktree removal) and the actual branch
                    # delete this same call gates.
                    real(["git", "reset", "--hard", base_tip], cwd=repo,
                        capture_output=True, text=True)
            return real(cmd, capture_output=True, text=True, cwd=repo, timeout=300)

        removed, detail = I.cleanup_lane(repo, "lane/A", "A",
                                         runner=rewinding_runner)
        self.assertFalse(removed, detail)
        self.assertIn("no longer contained", detail)
        self.assertTrue(_branch_exists(repo, "lane/A"),
                        "a branch canonical moved away from must survive, "
                        "not be deleted on a stale check")
        self.assertGreaterEqual(calls["ancestor_checks"], 2,
                                "the ancestor check must run again at "
                                "deletion time, not only once at entry")


class TranscriptHygieneRetiresOnlyTheVanishedWorktree(unittest.TestCase):
    """CONT-0, U4, option (a) ONLY: after a lane's worktree is gone,
    cleanup_lane also removes the session-transcript folder under the
    config dir's projects/ whose slug is derived from that exact worktree
    path. Every case here runs under a THROWAWAY CLAUDE_CONFIG_DIR; the
    real one is never read or written, proven by snapshotting its
    projects/ listing (names only) before and after this whole class and
    asserting it is unchanged (setUpClass/tearDownClass, once, since a
    per-test snapshot would just repeat the same unchanging assertion)."""

    @classmethod
    def setUpClass(cls):
        real_config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
            os.path.expanduser("~"), ".claude")
        real_projects = os.path.join(real_config, "projects")
        cls._real_projects = real_projects
        cls._before = (sorted(os.listdir(real_projects))
                       if os.path.isdir(real_projects) else None)

    @classmethod
    def tearDownClass(cls):
        after = (sorted(os.listdir(cls._real_projects))
                if os.path.isdir(cls._real_projects) else None)
        assert after == cls._before, (
            "this test class touched the REAL config dir's projects/ "
            "listing: before=%r after=%r" % (cls._before, after))

    def setUp(self):
        self.throwaway = tempfile.mkdtemp(prefix="transcript-hygiene-config-")
        self._old_env = dict(os.environ)
        os.environ["CLAUDE_CONFIG_DIR"] = self.throwaway
        os.environ.pop("BROTHER_CONFIG_DIR", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def _seed_transcript(self, path):
        slug = I._transcript_slug(path)
        folder = os.path.join(self.throwaway, "projects", slug)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "session.jsonl"), "w",
                 encoding="utf-8") as fh:
            fh.write('{"fake": "transcript"}\n')
        return folder

    def test_the_lanes_orphan_folder_is_removed_end_to_end(self):
        """The real, wired path: a real lane integrates (its worktree is
        genuinely removed by cleanup_lane's own ordinary case), and the
        transcript folder seeded for that EXACT vanished path is gone."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        folder = self._seed_transcript(lane_path)
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        out = I.integrate(repo, [{"id": "A", "integrable": True}],
                          {"A": "lane/A"},
                          {"A": {"id": "A", "done_check": "test -f new.py"}})
        self.assertEqual(out[0]["verdict"], I.INTEGRATED, out[0].get("reason"))
        self.assertFalse(os.path.isdir(lane_path))
        self.assertFalse(os.path.isdir(folder),
                         "the orphaned transcript folder must be gone")

    def test_a_folder_whose_path_still_exists_is_untouched(self):
        still_here = tempfile.mkdtemp(prefix="still-here-")
        folder = self._seed_transcript(still_here)
        I._retire_transcript_folder(still_here, "lane/A")
        self.assertTrue(os.path.isdir(folder),
                        "a worktree path that still exists must never have "
                        "its transcript folder removed")

    def test_a_slug_collision_prefix_is_untouched(self):
        """The exact `/private/tmp/foo` vs `/private/tmp/foobar` shape:
        two different real paths whose slugs share every character up to
        where one is simply longer. Deleting by prefix match would destroy
        the second while retiring the first; this asserts the second
        survives because the match is on the FULL slug, not a prefix."""
        base = tempfile.mkdtemp(prefix="brother-lane-slug-collision-")
        vanished = os.path.join(base, "lane-a")
        os.makedirs(vanished)
        survivor = os.path.join(base, "lane-a-extended")
        os.makedirs(survivor)
        # REPAIR C7: the age gate is a DIFFERENT concern (see the class
        # below), so this passes a lane_created_at from before either
        # transcript file was seeded, satisfying it rather than being
        # blocked by it.
        created_at = time.time()
        vanished_folder = self._seed_transcript(vanished)
        survivor_folder = self._seed_transcript(survivor)
        os.rmdir(vanished)  # the lane's worktree path itself is now gone
        I._retire_transcript_folder(vanished, "lane/A",
                                    lane_created_at=created_at)
        self.assertFalse(os.path.isdir(vanished_folder))
        self.assertTrue(os.path.isdir(survivor_folder),
                        "a folder for a different path with a similar "
                        "prefix must never be touched")

    def test_no_config_dir_named_falls_back_but_finds_nothing_here(self):
        """REPAIR C2: with neither override env var set, brother_paths.
        config_dir() now falls back to the real per-client default rather
        than staying dormant -- but the folder this test seeded lives under
        `self.throwaway`, a different directory, so the fallback resolution
        finds nothing there to remove. Nothing is read or written under
        `self.throwaway` in this path, and the class-level snapshot above
        proves the REAL config dir's projects/ listing is untouched too."""
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("BROTHER_CONFIG_DIR", None)
        vanished = tempfile.mkdtemp(prefix="brother-lane-no-config-dir-")
        folder = self._seed_transcript(vanished)  # writes under self.throwaway regardless
        os.rmdir(vanished)
        I._retire_transcript_folder(vanished, "lane/A")  # must not raise, must not touch folder
        self.assertTrue(os.path.isdir(folder))

    def test_an_unregistered_branch_is_refused(self):
        """REPAIR C2, gate 1: a branch without LANE_BRANCH_PREFIX is never a
        registered lane path, whatever `path` looks like -- refused before
        anything else is even read."""
        vanished = tempfile.mkdtemp(prefix="not-a-lane-")
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        I._retire_transcript_folder(vanished, "not-a-lane-branch")
        self.assertTrue(os.path.isdir(folder),
                        "a folder must never be removed for a worktree whose "
                        "branch is not a registered lane branch")

    def test_a_lane_outside_the_temp_root_is_refused(self):
        """FINDING 4 (2026-09-10 security review): the deletion target is
        derived from ANY registered lane branch's worktree path, and until
        now nothing checked that path was actually a lane worktree_lane.py
        created -- only that its branch name started with the lane prefix.
        A path outside the directory worktree_lane.acquire() creates lanes
        under (tempfile.gettempdir(), the base every real caller in this
        estate uses when it does not pass its own root) is refused, in the
        same shape as every other gate here, and nothing is deleted."""
        outside_root = os.path.dirname(os.path.realpath(tempfile.gettempdir()))
        vanished = os.path.join(outside_root, "not-a-real-lane-worktree-xyz")
        self.assertFalse(os.path.exists(vanished))
        folder = self._seed_transcript(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A",
                                        lane_created_at=time.time())
        self.assertTrue(os.path.isdir(folder),
                        "a lane path outside the lane root must never be "
                        "retired")
        self.assertIn("not retiring", buf.getvalue())

    def test_a_lane_inside_the_temp_root_still_retires(self):
        """The companion case for FINDING 4: an ordinary lane path, made
        the same way every real lane in this estate is made (a directory
        under the system temp root), is unaffected by the new gate and
        still retires normally."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-inside-")
        created_at = time.time()
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        I._retire_transcript_folder(vanished, "lane/A",
                                    lane_created_at=created_at)
        self.assertFalse(os.path.isdir(folder),
                         "a lane path inside the lane root must still "
                         "retire normally")

    def test_a_worktree_under_the_temp_root_without_the_prefix_is_refused(self):
        """Tightening of the lane-root gate: containment under
        tempfile.gettempdir() alone used to be enough. A directory sitting
        directly under the temp root but NOT inside a `brother-lane-*`
        directory -- the exact prefix worktree_lane.acquire() passes to
        tempfile.mkdtemp() for every real lane it creates -- is not a
        registered lane worktree, whatever its branch name says, and must
        be refused rather than treated as one."""
        vanished = tempfile.mkdtemp(prefix="not-a-brother-lane-directory-")
        created_at = time.time()
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A",
                                        lane_created_at=created_at)
        self.assertTrue(os.path.isdir(folder),
                        "a worktree directly under the temp root, but not "
                        "inside a brother-lane-* directory, must never be "
                        "retired")
        self.assertIn("not retiring", buf.getvalue())
        self.assertIn(I.LANE_WORKTREE_PARENT_PREFIX, buf.getvalue())

    def test_a_symlinked_projects_directory_is_refused(self):
        """REPAIR C2, gate 3: projects/ itself being a symlink is refused,
        never followed as though it were the real store."""
        real_elsewhere = tempfile.mkdtemp(prefix="elsewhere-projects-")
        vanished = tempfile.mkdtemp(prefix="brother-lane-symlinked-projects-")
        os.rmdir(vanished)
        os.makedirs(os.path.join(real_elsewhere,
                                 I._transcript_slug(vanished)))
        projects_link = os.path.join(self.throwaway, "projects")
        os.symlink(real_elsewhere, projects_link)
        I._retire_transcript_folder(vanished, "lane/A")
        # nothing under real_elsewhere was touched, and the symlink itself
        # was never followed for a deletion
        self.assertTrue(os.path.islink(projects_link))
        self.assertTrue(os.path.isdir(os.path.join(
            real_elsewhere, I._transcript_slug(vanished))))

    def test_a_symlinked_target_folder_is_refused(self):
        """REPAIR C2, gate 4: the target folder itself being a symlink
        (pointing anywhere, including outside projects/) is refused."""
        elsewhere = tempfile.mkdtemp(prefix="symlink-target-elsewhere-")
        vanished = tempfile.mkdtemp(prefix="brother-lane-symlinked-folder-")
        os.rmdir(vanished)
        slug = I._transcript_slug(vanished)
        projects_dir = os.path.join(self.throwaway, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        link_path = os.path.join(projects_dir, slug)
        os.symlink(elsewhere, link_path)
        I._retire_transcript_folder(vanished, "lane/A")
        self.assertTrue(os.path.islink(link_path),
                        "a symlinked target folder must be refused, not "
                        "deleted or followed")
        self.assertTrue(os.path.isdir(elsewhere),
                        "the symlink's target must never be touched")

    def test_a_symlink_inside_the_folder_is_refused(self):
        """REPAIR C2: the removal walks the folder first and refuses the
        WHOLE deletion if anything inside it, at any depth, is a symlink."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-folder-with-symlink-")
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        elsewhere_file = tempfile.mkstemp(prefix="pointed-at-")[1]
        os.symlink(elsewhere_file, os.path.join(folder, "escape.jsonl"))
        I._retire_transcript_folder(vanished, "lane/A")
        self.assertTrue(os.path.isdir(folder),
                        "a folder containing a symlink must be refused "
                        "wholesale, never partially deleted")

    def test_the_folder_is_named_with_its_file_count_and_size_before_deletion(self):
        """REPAIR C7 (2026-09-09 adversarial review of lane/continuity,
        round 2): the second reviewer found this function prints what it
        destroyed only AFTER destroying it, with no size or entry count.
        This asserts the pre-deletion line names the folder, the file
        count and the byte count, and that it is printed BEFORE the
        post-deletion confirmation line, never only after."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-sized-")
        created_at = time.time()
        folder = self._seed_transcript(vanished)
        with open(os.path.join(folder, "second.jsonl"), "w",
                 encoding="utf-8") as fh:
            fh.write('{"another": "line"}\n')
        expected_bytes = sum(
            os.path.getsize(os.path.join(folder, n))
            for n in os.listdir(folder))
        os.rmdir(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A",
                                        lane_created_at=created_at)
        self.assertFalse(os.path.isdir(folder))
        printed = buf.getvalue()
        pre_idx = printed.find("retiring transcript folder")
        post_idx = printed.find("retired transcript folder")
        self.assertNotEqual(pre_idx, -1, printed)
        self.assertNotEqual(post_idx, -1, printed)
        self.assertLess(pre_idx, post_idx,
                        "the pre-deletion line must print BEFORE the "
                        "post-deletion confirmation, never only after")
        self.assertIn("2 file(s)", printed, printed)
        self.assertIn("%d byte(s)" % expected_bytes, printed, printed)

    def test_a_folder_older_than_the_lane_is_never_removed(self):
        """REPAIR C7: a transcript folder that merely shares this lane's
        slug but PREDATES the lane (its newest file is not newer than the
        lane's own recorded creation time) is a pre-existing folder, never
        this lane's own transcript, and must never be removed."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-stale-slug-")
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        # A creation time AFTER the seeded file's own mtime: the folder
        # reads as older than "this lane", exactly the case this repair
        # exists to protect.
        created_at = time.time() + 3600
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A",
                                        lane_created_at=created_at)
        self.assertTrue(os.path.isdir(folder),
                        "a folder older than the lane must survive")
        self.assertIn("not retiring", buf.getvalue())
        self.assertIn("older than", buf.getvalue())

    def test_no_recorded_creation_time_refuses_rather_than_guesses(self):
        """REPAIR C7: 'if no creation time is recorded, refuse and print
        why' -- the literal case, lane_created_at never given at all."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-no-creation-time-")
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A")
        self.assertTrue(os.path.isdir(folder),
                        "with no recorded creation time this must refuse, "
                        "never guess")
        self.assertIn("not retiring", buf.getvalue())
        self.assertIn("no creation time", buf.getvalue())

    def test_a_refused_retirement_prints_only_the_refusal_line(self):
        """REPAIR C9 (2026-09-09 adversarial review, round 3): the
        "retiring transcript folder ... (N files, B bytes)" announcement
        used to print ABOVE the two gates it was meant to be paired with
        (a recorded creation time, a folder newer than the lane), so a
        REFUSED retirement still printed "retiring transcript folder ..."
        before the refusal line said the opposite -- a run log that
        announced a removal that never happened. Driven the same way the
        adversarial review found it: no lane_created_at at all, the
        literal missing-creation-time refusal, checked for the absence of
        the announcement line, not merely the presence of the refusal."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-c9-refused-")
        folder = self._seed_transcript(vanished)
        os.rmdir(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A")
        self.assertTrue(os.path.isdir(folder))
        printed = buf.getvalue()
        self.assertIn("not retiring", printed)
        self.assertNotIn("retiring transcript folder", printed,
                         "a refused retirement must never announce the "
                         "removal it is refusing")
        self.assertNotIn("retired transcript folder", printed)

    def test_a_file_that_cannot_be_stat_ed_refuses_retirement_rather_than_guess_at_the_newest_mtime(self):
        """The stat loop that counts files and tracks the newest mtime used
        to swallow OSError with a bare `continue`: if the one file that
        could not be stat'd was in fact the newest, an incomplete
        newest_mtime could pass the age gate and shutil.rmtree a folder
        that still holds something recent. This must refuse instead, the
        same "any doubt" contract REPAIR C10 already holds for a branch
        that merely looks like a lane."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-unstatable-")
        folder = self._seed_transcript(vanished)
        # os.path.realpath, not a string match: _retire_transcript_folder
        # walks realpath(config_dir)/projects/<slug>, and on macOS that
        # resolves the /var -> /private/var symlink, so the exact string
        # this test built via _seed_transcript never equals the one the
        # walk actually stats even though both name the same file on disk.
        unstatable = os.path.realpath(os.path.join(folder, "session.jsonl"))
        os.rmdir(vanished)
        real_stat = os.stat

        def flaky_stat(path, *a, **kw):
            if os.path.realpath(str(path)) == unstatable:
                raise OSError(13, "Permission denied")
            return real_stat(path, *a, **kw)

        buf = io.StringIO()
        with mock.patch("os.stat", side_effect=flaky_stat):
            with contextlib.redirect_stderr(buf):
                I._retire_transcript_folder(vanished, "lane/A",
                                            lane_created_at=time.time() + 1000)
        self.assertTrue(os.path.isdir(folder),
                        "a file that could not be stat'd must refuse "
                        "retirement, never guess at the newest mtime")
        printed = buf.getvalue()
        self.assertIn("not retiring", printed)
        self.assertIn("could not be stat", printed)
        self.assertNotIn("retiring transcript folder", printed)

    def test_cleanup_lane_itself_records_a_creation_time_end_to_end(self):
        """The real, wired path (not the direct-call unit tests above):
        cleanup_lane must capture the lane's own creation time BEFORE the
        worktree is removed (the breadcrumb and the worktree's own admin
        directory are both gone by the time _retire_transcript_folder
        runs) and pass it through, so the ordinary integrated-and-cleaned
        case still retires its transcript folder exactly as it always
        did."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        folder = self._seed_transcript(lane_path)
        lane_commit(lane_path, {"new.py": "x = 1\n"}, "A")
        out = I.integrate(repo, [{"id": "A", "integrable": True}],
                          {"A": "lane/A"},
                          {"A": {"id": "A", "done_check": "test -f new.py"}})
        self.assertEqual(out[0]["verdict"], I.INTEGRATED, out[0].get("reason"))
        self.assertFalse(os.path.isdir(lane_path))
        self.assertFalse(os.path.isdir(folder),
                         "the wired path must still retire its own "
                         "transcript folder once REPAIR C7's age gate is "
                         "satisfied by a real, captured creation time")

    def test_a_folder_whose_newest_file_exactly_matches_the_lanes_creation_time_is_still_retired(self):
        """REPAIR L1 (2026-09-10, Linux CI runner, runs 9d087045, 49a94a9a,
        84ed6b6c): the lane's own worktree ctime and its transcript
        folder's newest file mtime were measured EQUAL to the fraction of
        a second (1788996795.3214097 both) on Linux's coarser clock, and
        the old `newest_mtime <= lane_created_at` gate refused a folder
        that genuinely belonged to this lane. Same-tick equality must now
        retire, never refuse."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-same-tick-")
        folder = self._seed_transcript(vanished)
        seeded_file = os.path.join(folder, "session.jsonl")
        target = time.time()
        os.utime(seeded_file, (target, target))
        # read back the exact tick this filesystem actually stored, rather
        # than trusting `target` bit-for-bit, so the equality this test
        # drives is real rather than assumed
        created_at = os.stat(seeded_file).st_mtime
        os.rmdir(vanished)
        I._retire_transcript_folder(vanished, "lane/A",
                                    lane_created_at=created_at)
        self.assertFalse(os.path.isdir(folder),
                         "a folder whose newest file exactly matches the "
                         "lane's own creation time must still be retired")

    def test_a_folder_one_second_older_than_the_lane_is_refused_as_older_than(self):
        """REPAIR L1: equality is tolerated, but a folder whose newest file
        is genuinely, strictly OLDER than the lane's own creation time is
        still refused -- it is a pre-existing folder that merely shares
        this lane's slug. The refusal text now says 'older than' rather
        than the old 'not newer than'."""
        vanished = tempfile.mkdtemp(prefix="brother-lane-one-second-older-")
        folder = self._seed_transcript(vanished)
        seeded_file = os.path.join(folder, "session.jsonl")
        base = time.time()
        os.utime(seeded_file, (base, base))
        newest_mtime = os.stat(seeded_file).st_mtime
        created_at = newest_mtime + 1.0
        os.rmdir(vanished)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            I._retire_transcript_folder(vanished, "lane/A",
                                        lane_created_at=created_at)
        self.assertTrue(os.path.isdir(folder),
                        "a folder strictly older than the lane must survive")
        self.assertIn("not retiring", buf.getvalue())
        self.assertIn("older than", buf.getvalue())

    def test_lane_created_at_prefers_the_sidecar_over_the_stat_fallback(self):
        """REPAIR L1: cleanup_lane's own creation-time read
        (worktree_lane.lane_created_at, the seam cleanup_lane now calls
        instead of stat'ing the worktree directory itself) must prefer the
        breadcrumb sidecar's created_at over any stat-based fallback --
        the whole point of writing it once, on every platform, at
        acquire() time. Proven by planting a deliberately different `.git`
        file mtime so a wrong read would be caught, not accidentally
        matched by chance."""
        repo = canon()
        lanes = W.Lanes(repo, ["A"])
        lane_path = lanes.path_for("A")
        admin_dir = subprocess.run(
            ["git", "rev-parse", "--git-dir"], cwd=lane_path,
            capture_output=True, text=True).stdout.strip()
        if not os.path.isabs(admin_dir):
            admin_dir = os.path.normpath(os.path.join(lane_path, admin_dir))
        sidecar_path = os.path.join(admin_dir, W.LANE_SIDECAR_NAME)
        with open(sidecar_path, encoding="utf-8") as fh:
            sidecar = json.load(fh)
        known_created_at = 1700000000.0
        sidecar["created_at"] = known_created_at
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh)
        git_file = os.path.join(lane_path, ".git")
        os.utime(git_file, (known_created_at + 999, known_created_at + 999))
        self.assertEqual(W.lane_created_at(lane_path), known_created_at)


class AnIntegratedUnitCarriesItsOwnChangedFiles(unittest.TestCase):
    """E41 (run 5 critic 3, hole H2, 2026-09-03): the receipt's file list
    was the round's diff, so two units integrated in one round carried each
    other's files. The one place a unit's own contribution is exactly known
    is here, between canonical's tip before this lane merged and the tip
    the merge produced; a lane that committed nothing merges as a no-op
    and reads [], the zero-change fact the receipt refuses to credit."""

    def test_the_evidence_names_exactly_the_lanes_own_files(self):
        repo = canon()
        lanes = W.Lanes(repo, ["A", "B"])
        lane_commit(lanes.path_for("A"), {"a.py": "a = 1\n"}, "A")
        lane_commit(lanes.path_for("B"), {"b.py": "b = 1\n"}, "B")
        ra = I.integrate_one(repo, "lane/A", {"id": "A",
                                              "done_check": "test -f a.py"})
        rb = I.integrate_one(repo, "lane/B", {"id": "B",
                                              "done_check": "test -f b.py"})
        self.assertEqual(ra["verdict"], I.INTEGRATED)
        self.assertEqual(rb["verdict"], I.INTEGRATED)
        self.assertEqual(ra["evidence"]["files_changed"], ["a.py"])
        self.assertEqual(rb["evidence"]["files_changed"], ["b.py"])

    def test_a_lane_that_committed_nothing_reads_an_empty_list(self):
        repo = canon()
        W.Lanes(repo, ["A"])
        r = I.integrate_one(repo, "lane/A", {"id": "A", "done_check": "true"})
        self.assertEqual(r["verdict"], I.INTEGRATED)
        self.assertEqual(r["evidence"]["files_changed"], [])


class TheMergeSaysAMachineMadeIt(unittest.TestCase):
    """E45, run 5 critic 1, section 5, 2026-09-03: the engine merged lanes
    under whoever ran it, with no marker and no run id, so an auditor reading
    the history could not tell a machine merge from a human one. Read back
    from a real merge commit, both with the run named and without it."""

    def setUp(self):
        self.repo = canon()
        self.lanes = W.Lanes(self.repo, ["A"])
        lane_commit(self.lanes.path_for("A"), {"a.py": "a = 1\n"}, "A")
        self.unit = {"id": "A", "done_check": "test -f a.py"}
        for name in (I.RUN_ID_ENV_VAR, I.HARNESS_ENV_VAR):
            had = os.environ.pop(name, None)
            if had is not None:
                self.addCleanup(os.environ.__setitem__, name, had)

    def _body(self):
        return subprocess.run(["git", "log", "--format=%B", "-1"],
                              cwd=self.repo, capture_output=True,
                              text=True).stdout

    def _trailers(self):
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"],
                                cwd=self.repo, input=self._body(),
                                capture_output=True, text=True).stdout
        out = {}
        for line in parsed.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                out[key.strip()] = value.strip()
        return out

    def test_the_merge_commit_carries_the_run_and_harness_trailers(self):
        r = I.integrate_one(self.repo, "lane/A", self.unit,
                            run_id="20260903T120414",
                            harness_revision="4fc610a0")
        self.assertEqual(r["verdict"], I.INTEGRATED)
        body = self._body()
        self.assertIn("Brother integrated A from lane/A", body)
        self.assertIn("Brother-Run: 20260903T120414", body)
        self.assertIn("Brother-Harness: 4fc610a0", body)
        # git's own reader, not a grep: the trailer block is real trailers.
        self.assertEqual(self._trailers(),
                         {"Brother-Run": "20260903T120414",
                          "Brother-Harness": "4fc610a0"})

    def test_a_caller_that_names_neither_stamps_no_data_rather_than_nothing(self):
        """An omitted value is spelled NO-DATA. A missing trailer would say
        nothing about whether anybody ever knew the run, and the merge would
        read like a human's again."""
        r = I.integrate_one(self.repo, "lane/A", self.unit)
        self.assertEqual(r["verdict"], I.INTEGRATED)
        self.assertEqual(self._trailers(),
                         {"Brother-Run": I.NODATA,
                          "Brother-Harness": I.NODATA})

    def test_the_engine_environment_names_the_run_when_no_caller_does(self):
        os.environ[I.RUN_ID_ENV_VAR] = "20260903T235959"
        self.addCleanup(os.environ.pop, I.RUN_ID_ENV_VAR, None)
        I.integrate_one(self.repo, "lane/A", self.unit)
        self.assertEqual(self._trailers()["Brother-Run"], "20260903T235959")

    def test_a_multi_line_value_stays_one_trailer_line(self):
        """brother_run's own harness revision reads NO-DATA by quoting git's
        stderr, which can carry a newline. A trailer is one line, so a break
        inside the value would end the block and git would stop reading the
        rest as trailers."""
        I.integrate_one(self.repo, "lane/A", self.unit, run_id="r",
                        harness_revision="NO-DATA: git rev-parse exited 128\n"
                                         "fatal: not a git repository")
        self.assertEqual(
            self._trailers()["Brother-Harness"],
            "NO-DATA: git rev-parse exited 128 fatal: not a git repository")

    def test_the_marker_is_in_the_message_never_the_author(self):
        """The author stays whoever ran the engine: forging that is a worse
        thing than labelling the commit, and the test pins it."""
        I.integrate_one(self.repo, "lane/A", self.unit, run_id="r")
        who = subprocess.run(["git", "log", "--format=%an <%ae>", "-1"],
                             cwd=self.repo, capture_output=True,
                             text=True).stdout.strip()
        self.assertEqual(who, "t <a@b.c>")


if __name__ == "__main__":
    unittest.main()

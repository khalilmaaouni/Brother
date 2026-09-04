"""What serial integration must keep true.

The headline test is the directive's own acceptance scenario: two changes with
no git conflict that become semantically incompatible after the first lands.
Everything else guards the properties that make the unwind safe and the refusals
honest.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import integrate as I  # noqa: E402
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

    def test_a_needs_repair_units_lane_is_cleaned_too(self):
        """NEEDS-REPAIR-ON-NEW-BASE leaves the unit SCHEDULED for a genuinely
        FRESH claim and a fresh lane next round (brother_run.py's own
        comment on that classification), so nothing a retry needs is lost
        by retiring this lane now."""
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
        self.assertFalse(_branch_exists(repo, "lane/B"))
        self.assertFalse(os.path.isdir(b_path))

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

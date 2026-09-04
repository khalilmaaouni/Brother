"""test_continuity_matrix: E73.3, the hostile resume matrix.

WHAT THIS PROVES AND WHY THIS SHAPE. E73.1 (scripts/continuity.py) builds a
capsule from the journal and the stores; E73.2 wired the engine to write one
at each lifecycle checkpoint (run.opened, dispatch.round, unit.done,
unit.refused x2, run.resumed, the end of main -- scripts/brother_run.py's
own seven _write_capsule call sites, named verbatim in the PR 113 evidence
this row's E73.3 subtask resumes from). Neither proved the capsule stays
SAFE at every point a real SIGKILL could land. This file does: fourteen
kill points, each constructing the exact on-disk footprint (journal.jsonl,
claims.json, the Work document) a kill at that instant would leave -- using
the LOCAL FORM scripts/test_crash_resume.py's own
ACapsuleSurvivesAKillAfterIntegration already uses (claim_store and journal
called directly against a bare run_dir, no subprocess) rather than
loop_bridge.py's subprocess harness, which drives a DIFFERENT scheduler
(graph_loop) that never touches the journal or the capsule at all -- then
calling continuity.capsule(run_dir) exactly as brother_run.py's own
run.resumed checkpoint would (a FRESH recompute against whatever is on
disk, never a cached guess) and asserting, per kill point:

  (a) NO DUPLICATE INTEGRATION -- a unit sits in buckets["integrated"] only
      when the Work document's own row already carries status "DONE" on
      disk (continuity._bucket_for's own rule: a released claim is not
      trusted on its own, per _mark_integrated's re-verification being the
      one signal this estate already treats as authoritative).
  (b) NO LOST UNIT -- every unit id the run started with is reachable
      through capsule()'s own unit_ids union (Work-document rows OR a
      journal claim), so it appears in exactly one bucket.
  (c) A REFUSAL WHEN STATE CANNOT BE TRUSTED -- an unreadable store or an
      unconfirmable lease degrades to "unclear" (or, before any journal
      exists at all, capsule() itself refuses NO-DATA) rather than a guess.

THE FOURTEEN POINTS THEMSELVES ARE NOT LISTED ANYWHERE ON DISK. Read before
writing this file: docs/plan/READINESS-ROADMAP-2026-08-29.json's row E73
and subtask E73.3 (both "what" and "done_check" say "the fourteen listed
points" as though a list exists), scripts/test_crash_resume.py (the "single
kill point already driven" E73.3's own resume_from names), and a repo-wide
grep for "fourteen" and "kill point" across docs/, scripts/ and bundle/.
None of them enumerates fourteen points; NARRATIVE.json's "the original
fourteen" is an unrelated reviewer-round count. So this file's fourteen are
DERIVED, not copied: the seven real _write_capsule checkpoints in
scripts/brother_run.py, each driven at the instant its OWN capsule write
would land (state fresh) plus, where the checkpoint's surrounding code
creates a distinct in-between state a kill could also land in (a claim
released before the Work document is re-stamped; a row pulled out of the
Work document by a refusal before the end-of-run restore folds it back;
worker-alive vs worker-confirmed-dead), a second point at that in-between
state. This is reported to the dispatching session rather than silently
assumed.

A REAL, HONEST ENGINE FINDING SURFACED WHILE WRITING THIS (kill point 09,
marked expectedFailure rather than fixed here, per this lane's own scope:
it owns this test file and scripts/check_all.sh's one registration line,
never scripts/brother_run.py, which two sibling lanes are editing this
same night). _refuse_broken_precheck_units (scripts/brother_run.py) pulls
a refused row out of the Work document's on-disk `rows`/`units` array
(`doc[key] = kept`) BEFORE the drain ends, and
_restore_refused_precheck_units folds it back only at the very end of
main. journal_projection.claims_from_journal only reads claim.acquired and
claim.released events; a unit refused "before any worker started" was
never claimed, so it never appears there either. Between the pull and the
restore, continuity.capsule()'s own `unit_ids = sorted(set(row_by_id) |
set(journal_claims))` has that unit in NEITHER set: it is not merely
mis-bucketed, it is ABSENT from every bucket, in the one window that sits
between two of this row's own seven capsule checkpoints. That is exactly
the "no lost unit" property this matrix exists to hold the engine to, and,
for that window, the engine does not hold it. Kill point 11 drives the
OTHER refusal site (_refuse_exhausted_units, exhausted retries rather than
a broken precheck) with the same row-pulled shape and does NOT reproduce
it: an exhausted unit was claimed at least once, so its claim.acquired
journal event alone keeps it reachable through journal_claims even with
its row pulled. The defect is narrower than a first guess would have it:
only a precheck refusal, which never claims the unit at all, can lose it.

Python 3, standard library only. No network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import brother_run as BR  # noqa: E402
import claim_store as C  # noqa: E402
import continuity  # noqa: E402
import journal  # noqa: E402
import work_record as WR  # noqa: E402


def _dead_pid():
    """A pid guaranteed dead on THIS host: spawn a trivial subprocess and
    wait for it, so os.kill(pid, 0) reads ProcessLookupError afterwards
    (mirrors test_crash_resume.py's own cross-host trick, but same-host,
    since claim_store.live() only distrusts a pid on a hostname match)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=10)
    return p.pid


def _canon():
    """A real, throwaway git repo for the run.opened "cwd" payload, so
    continuity._canonical_revision reads an actual HEAD instead of
    degrading to NO-DATA on every single kill point (mirrors
    test_crash_resume.py's own canon())."""
    d = tempfile.mkdtemp(prefix="continuity-matrix-repo-")
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=d,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "t")
    with open(os.path.join(d, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "R0")
    return d


class TheHostileResumeMatrix(unittest.TestCase):
    """Fourteen kill points against one shared two-unit shape (CR1, CR2),
    each test building its own state from a fresh run_dir -- unittest gives
    no ordering guarantee across methods, so nothing here depends on one
    test's state surviving into another."""

    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="continuity-matrix-")
        self.repo = _canon()
        self.claims_path = os.path.join(self.run_dir, BR.CLAIMS_FILENAME)
        self.rec, problems = WR.create(
            "the hostile resume matrix proof",
            [{"id": "CR1", "done_check": "true", "owns": ["a.txt"]},
             {"id": "CR2", "done_check": "true", "owns": ["b.txt"]}],
            store=self.run_dir)
        self.assertFalse(problems, problems)
        self.record_path = self.rec["path"]

    # -- shared helpers ---------------------------------------------------

    def _journal(self, etype, unit_id=None, payload=None):
        return journal.append(self.run_dir, etype,
                              parent_ids=journal.previous(self.run_dir),
                              unit_id=unit_id, payload=payload or {})

    def _work_doc(self):
        with open(self.record_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_work_doc(self, doc):
        with open(self.record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)

    def _mark_row_done(self, unit_id):
        doc = self._work_doc()
        for row in doc["rows"]:
            if row["id"] == unit_id:
                row["status"] = "DONE"
        self._write_work_doc(doc)

    def _pull_row(self, unit_id):
        """Mirrors brother_run._refuse_broken_precheck_units's own
        `doc[key] = kept`: removes the row from the Work document's array,
        exactly as a refusal does before the end-of-run restore."""
        doc = self._work_doc()
        doc["rows"] = [r for r in doc["rows"] if r["id"] != unit_id]
        self._write_work_doc(doc)

    def _capsule(self):
        cap, problem = continuity.capsule(self.run_dir)
        self.assertIsNotNone(cap, problem)
        return cap

    def _bucket_of(self, cap, unit_id):
        for bucket, ids in cap["buckets"].items():
            if unit_id in ids:
                return bucket
        return None

    # -- kill point 01: before any journal exists --------------------------

    def test_kill_point_01_before_any_journal_write_refuses_untrusted_state(self):
        """Kill before run.opened's own journal.append ever lands: no
        journal.jsonl at all, exactly a pre-E59 run or a run whose first
        write never reached disk. capsule() must refuse rather than guess,
        and the resume screen must say so by name (the "refusal when state
        cannot be trusted" assertion, at its earliest possible instant)."""
        cap, problem = continuity.capsule(self.run_dir)
        self.assertIsNone(cap)
        self.assertIn("NO-DATA", problem)
        self.assertIn(self.run_dir, problem)
        out_path = os.path.join(self.run_dir, "capsule.json")
        self.assertFalse(os.path.isfile(out_path))

    # -- kill point 02: right after run.opened ------------------------------

    def test_kill_point_02_after_run_opened_shows_both_units_pending_none_lost(self):
        """Kill right after run.opened's capsule write: no unit has been
        touched yet. Both CR1 and CR2 must be reachable (no lost unit) and
        neither may read integrated (no duplicate integration, trivially,
        since nothing has happened)."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        cap = self._capsule()
        self.assertEqual({u["id"] for u in cap["units"]}, {"CR1", "CR2"})
        self.assertEqual(cap["buckets"]["integrated"], [])
        self.assertEqual(sorted(cap["buckets"]["pending"]), ["CR1", "CR2"])

    # -- kill point 03: mid dispatch round, before any claim ---------------

    def test_kill_point_03_after_first_dispatch_round_before_any_claim(self):
        """Kill right after dispatch.round's own capsule write, before
        loop_bridge (which this event is journaled BEFORE, per
        run_loop's own comment) has claimed anything. The environment
        block must reflect the round that was announced (slots), and
        both units stay pending: a round that never got to claim anyone
        must not read as having lost or finished either one."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        self._journal("dispatch.round", payload={"slots": 2, "own_tools": False})
        cap = self._capsule()
        self.assertEqual(cap["environment"]["slots"], 2)
        self.assertEqual(sorted(cap["buckets"]["pending"]), ["CR1", "CR2"])
        self.assertEqual(cap["buckets"]["integrated"], [])

    # -- kill point 04: claimed, owner confirmed alive ----------------------

    def test_kill_point_04_after_claim_acquired_worker_confirmed_alive_reads_active(self):
        """Kill mid-worker, right after claim_store.acquire's own
        claim.acquired journal write, with the owning pid still genuinely
        alive (this test process itself). Must read "active", never
        "integrated" (no duplicate integration against a unit that has
        not finished) and never dropped (no lost unit)."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        cap = self._capsule()
        self.assertEqual(self._bucket_of(cap, "CR1"), "active")
        self.assertEqual(self._bucket_of(cap, "CR2"), "pending")

    # -- kill point 05: claimed, owner confirmed dead -----------------------

    def test_kill_point_05_after_claim_acquired_owner_confirmed_dead_reads_abandoned(self):
        """Same instant as kill point 04, except the owning process is
        confirmed dead on this host (Gap 1's own live() check). Must read
        "abandoned", not "active" (a genuinely dead worker must not look
        like it is still working) and not "integrated" (nothing finished)."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        with open(self.claims_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["CR1"]["pid"] = _dead_pid()
        with open(self.claims_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        cap = self._capsule()
        self.assertEqual(self._bucket_of(cap, "CR1"), "abandoned")

    # -- kill point 06: the store itself is unreadable -----------------------

    def test_kill_point_06_claims_store_corrupted_mid_claim_reads_unclear_not_guessed(self):
        """A claim is live in the journal (claim.acquired already
        happened) but claims.json itself is now unreadable -- a torn
        write, disk corruption, anything short of the atomic rename this
        estate's own claim_store._write already protects against in the
        ordinary case. This is the canonical "refusal when state cannot
        be trusted" point: CR1 must read "unclear" with a NO-DATA detail,
        never silently "active" and never silently "abandoned", either of
        which would be a guess this file exists to catch."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        with open(self.claims_path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        cap = self._capsule()
        self.assertEqual(self._bucket_of(cap, "CR1"), "unclear")
        unit = next(u for u in cap["units"] if u["id"] == "CR1")
        self.assertIn("NO-DATA", unit["detail"])

    # -- kill point 07: released done, Work document not yet re-stamped -----

    def test_kill_point_07_after_unit_done_released_before_workdoc_stamped_never_shows_integrated_early(self):
        """Mirrors the exact window inside brother_run._mark_integrated:
        the claim is released state="done" and unit.done is journaled
        (both happen per-unit, inside the loop) before the Work document's
        single end-of-loop json.dump stamps row["status"]="DONE". A kill
        landing here must NEVER read CR1 as "integrated" (that would be
        integration nobody re-verified on disk -- the "no duplicate
        integration" assertion's sharpest edge) even though the claim
        store already says done; continuity._bucket_for's own rule (only
        the row's stamped status is trusted) means it must read pending
        instead, and it must still be present (no lost unit)."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        ok, problem = C.release(self.claims_path, "CR1", "matrix-owner",
                                state="done", evidence={"exit_code": 0})
        self.assertTrue(ok, problem)
        self._journal("unit.done", unit_id="CR1", payload={"files_changed": 1})
        # THE KILL: right here, before the Work document is re-read and
        # re-written with row["status"] = "DONE".
        cap = self._capsule()
        self.assertNotEqual(self._bucket_of(cap, "CR1"), "integrated")
        self.assertIsNotNone(self._bucket_of(cap, "CR1"), "CR1 must not be lost")

    # -- kill point 08: Work document stamped, capsule confirms it ----------

    def test_kill_point_08_after_workdoc_stamped_done_reads_integrated_no_duplicate(self):
        """The instant right after kill point 07's missing step lands:
        row["status"] is now "DONE" on disk. Must read "integrated"
        exactly once, and CR2 (untouched) must still read pending, proving
        one unit finishing never contaminates its sibling's bucket."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        C.release(self.claims_path, "CR1", "matrix-owner", state="done",
                  evidence={"exit_code": 0})
        self._journal("unit.done", unit_id="CR1", payload={"files_changed": 1})
        self._mark_row_done("CR1")
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        cap = self._capsule()
        self.assertEqual(cap["buckets"]["integrated"], ["CR1"])
        self.assertEqual(self._bucket_of(cap, "CR2"), "pending")

    # -- kill point 09: precheck refusal, row pulled, before restore --------

    @unittest.expectedFailure
    def test_kill_point_09_after_precheck_refusal_row_pulled_before_restore_is_a_known_engine_gap(self):
        """DEFECT, found here and NOT fixed in this lane (scope: this file
        and scripts/check_all.sh only; scripts/brother_run.py belongs to
        two other lanes tonight). Mirrors _refuse_broken_precheck_units's
        own on-disk footprint exactly: CR2's row is pulled from the Work
        document (`doc[key] = kept`) and a unit.refused event is journaled
        with the SAME payload shape that function writes (stage "before
        any worker started", why "its check cannot run") -- but it is
        never claimed, so journal_projection.claims_from_journal never
        sees it either. continuity.capsule()'s own `unit_ids = set(row_by_
        id) | set(journal_claims)` has CR2 in NEITHER set here: it is
        absent from every bucket, not merely mis-bucketed. That is a real
        "no lost unit" violation, bounded to the window between the pull
        and _restore_refused_precheck_units's own end-of-run restore
        (kill point 10 proves the restore closes it). Reported to the
        dispatching session; do not silently drop this expectedFailure on
        a future rewrite without re-checking the underlying engine
        behaviour has actually changed."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        self._pull_row("CR2")
        self._journal("unit.refused", unit_id="CR2",
                      payload={"stage": "before any worker started",
                               "why": "its check cannot run"})
        cap = self._capsule()
        self.assertIsNotNone(self._bucket_of(cap, "CR2"), "CR2 must not be lost")

    # -- kill point 10: precheck refusal, after the end-of-run restore ------

    def test_kill_point_10_after_precheck_refusal_capsule_recomputed_post_restore(self):
        """The instant right after brother_run._restore_refused_precheck_
        units folds CR2's row back into the Work document: the window kill
        point 09 exposed is bounded, and by the time the run actually
        ends, the unit is reachable again. Proves the gap is transient,
        not permanent, and that the restore step this lane must not touch
        is doing its job."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        self._pull_row("CR2")
        self._journal("unit.refused", unit_id="CR2",
                      payload={"stage": "before any worker started",
                               "why": "its check cannot run"})
        # THE RESTORE: _restore_refused_precheck_units's own effect, folding
        # the pulled row back into the Work document's rows array.
        doc = self._work_doc()
        doc["rows"].append({"id": "CR2", "title": "CR2", "status": "SCHEDULED",
                            "depends_on": [], "owns": ["b.txt"],
                            "done_check": "true",
                            "integration_refused": "its check cannot run",
                            "refused_before_work": True})
        self._write_work_doc(doc)
        cap = self._capsule()
        self.assertIsNotNone(self._bucket_of(cap, "CR2"), "CR2 must not be lost")
        self.assertNotEqual(self._bucket_of(cap, "CR2"), "integrated")

    # -- kill point 11: exhausted-retry refusal (the OTHER site) ------------

    def test_kill_point_11_after_exhausted_retry_refusal_row_pulled_before_restore_still_finds_the_unit(self):
        """Driven at brother_run._refuse_exhausted_units's own distinct
        call site (the SECOND of the two unit.refused journal.append
        sites in scripts/brother_run.py; kill point 09 drives the first,
        _refuse_broken_precheck_units): a resumed run's retry budget is
        spent, and the row is pulled from the Work document exactly as
        kill point 09's row was, with a different payload shape (why "the
        retry budget is spent", carrying "attempts"). UNLIKE kill point
        09, this does NOT reproduce the lost-unit defect: an exhausted
        unit was, by definition, claimed at least once, so its
        claim.acquired journal event keeps it in journal_claims even
        after its row is pulled, and continuity.capsule()'s union still
        finds it there. The defect kill point 09 found is narrower than
        it first looked: only a unit refused before ever being claimed
        (a precheck refusal) can vanish from every bucket; an
        exhausted-retry refusal cannot, because the claim trail alone is
        enough to keep it reachable."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": True})
        claim, problem = C.acquire(self.claims_path, "CR1", "matrix-owner")
        self.assertTrue(claim, problem)
        with open(self.claims_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["CR1"]["attempt"] = BR.MAX_UNIT_ATTEMPTS
        data["CR1"]["state"] = "claimed"
        with open(self.claims_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self._pull_row("CR1")
        self._journal("unit.refused", unit_id="CR1",
                      payload={"stage": "before any worker started",
                               "why": "the retry budget is spent",
                               "attempts": BR.MAX_UNIT_ATTEMPTS})
        cap = self._capsule()
        self.assertIsNotNone(self._bucket_of(cap, "CR1"), "CR1 must not be lost")

    # -- kill point 12: two units, mixed state, at once ----------------------

    def test_kill_point_12_second_unit_claimed_while_first_integrated_no_cross_contamination(self):
        """CR1 fully integrated, CR2 mid-claim (active) at the same
        instant: a two-unit run's most ordinary kill point, and the one
        that proves the three assertions hold PER UNIT, not just when
        only one unit exists. Neither the "no duplicate integration" nor
        the "no lost unit" checks on CR1 may be affected by CR2 also
        being in flight, and vice versa."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        C.acquire(self.claims_path, "CR1", "matrix-owner")
        C.release(self.claims_path, "CR1", "matrix-owner", state="done",
                  evidence={"exit_code": 0})
        self._journal("unit.done", unit_id="CR1", payload={"files_changed": 1})
        self._mark_row_done("CR1")
        claim, problem = C.acquire(self.claims_path, "CR2", "matrix-owner")
        self.assertTrue(claim, problem)
        cap = self._capsule()
        self.assertEqual(cap["buckets"]["integrated"], ["CR1"])
        self.assertEqual(self._bucket_of(cap, "CR2"), "active")

    # -- kill point 13: at the run.resumed boundary --------------------------

    def test_kill_point_13_at_run_resumed_boundary_preserves_prior_progress_no_lost_unit(self):
        """Kill right after a SECOND run.resumed event is journaled (the
        run.resumed checkpoint's own capsule write, brother_run.py line
        ~2813): CR1's prior integration and CR2's still-pending state must
        both survive being read back across the resume boundary, proving
        a resume-of-a-resume never loses progress a first crash already
        recorded."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        C.acquire(self.claims_path, "CR1", "matrix-owner")
        C.release(self.claims_path, "CR1", "matrix-owner", state="done",
                  evidence={"exit_code": 0})
        self._journal("unit.done", unit_id="CR1", payload={"files_changed": 1})
        self._mark_row_done("CR1")
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        self._journal("run.resumed", payload={"cwd": self.repo})
        cap = self._capsule()
        self.assertEqual(cap["buckets"]["integrated"], ["CR1"])
        self.assertEqual(self._bucket_of(cap, "CR2"), "pending")

    # -- kill point 14: the end of main, terminal state -----------------------

    def test_kill_point_14_at_end_of_main_terminal_state_all_units_accounted_for(self):
        """The last of the seven engine checkpoints (line ~3390's final
        _write_capsule, after the drain has nothing left to do): CR1
        integrated, CR2 refused-and-restored. Every unit the run started
        with must be accounted for in exactly one bucket, none reading
        integrated falsely, and the resume screen (the on-disk capsule.
        json, read exactly as _print_resume_screen reads it) must render
        both units by id with no NO-DATA leaking into an outcome that
        actually finished."""
        self._journal("run.opened", payload={"cwd": self.repo, "resumed": False})
        C.acquire(self.claims_path, "CR1", "matrix-owner")
        C.release(self.claims_path, "CR1", "matrix-owner", state="done",
                  evidence={"exit_code": 0})
        self._journal("unit.done", unit_id="CR1", payload={"files_changed": 1})
        self._mark_row_done("CR1")
        self._journal("unit.refused", unit_id="CR2",
                      payload={"stage": "before any worker started",
                               "why": "its check cannot run"})
        doc = self._work_doc()
        for row in doc["rows"]:
            if row["id"] == "CR2":
                row["integration_refused"] = "its check cannot run"
                row["refused_before_work"] = True
        self._write_work_doc(doc)
        ok, problem = continuity.write_capsule(self.run_dir)
        self.assertTrue(ok, problem)
        cap = self._capsule()
        self.assertEqual({u["id"] for u in cap["units"]}, {"CR1", "CR2"})
        self.assertEqual(cap["buckets"]["integrated"], ["CR1"])
        self.assertNotIn("CR2", cap["buckets"]["integrated"])
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            BR._print_resume_screen(self.run_dir, "the hostile resume matrix proof")
        printed = out.getvalue()
        self.assertIn("CR1", printed)
        self.assertIn("CR2", printed)
        self.assertNotIn("NO-DATA: 'the hostile resume matrix proof", printed)


if __name__ == "__main__":
    unittest.main()

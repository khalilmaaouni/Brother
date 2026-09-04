#!/usr/bin/env python3
"""Tests for tools/bm_reconcile.py: the fourteen Z2.6 fault fixtures from
BROTHER_ZOO_HARVEST_ACCELERATION_PLAN_2026-08-23.md section 9, plus the
idempotency and NO-DATA-degrade cases the design calls out separately.

Every fixture is a real, throwaway BrotherMode store built through
`bm_store.Store`'s own public methods, the same in-process technique
tools/test_bm_store.py uses (no subprocess needed for the store itself;
BROTHERMODE_ROOT env juggling is unnecessary because the root is passed
explicitly). Two cases need a raw sqlite write to reach a shape the
Store API itself refuses to produce, mirroring tools/test_bm_stall.py's
own `_do_backdate` and `_do_insert_overlapping_claim` technique. See
docs/RECOVERY-TRUTH.md for why cases 2 and 8 are `skipTest` here rather
than built.

Python 3.9, standard library only. Run:
  python3 tools/test_bm_reconcile.py
"""
import datetime
import hashlib
import importlib.util as _ilu
import io
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = _ilu.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
st = _load("bm_stall")
RC = _load("bm_reconcile")

NOW = datetime.datetime(2026, 8, 23, 12, 0, 0, tzinfo=datetime.timezone.utc)
STALE_AGO = NOW - datetime.timedelta(hours=30)  # past a 4h staleness window


def _iso(dt):
    return dt.strftime(bs._ISO_STAMP_FORMAT)


def _backdate(db_path, lifecycle_uuid, when):
    """Same technique tools/test_bm_stall.py's own _do_backdate uses: the
    only way to make an otherwise-healthy fence look like it has been
    sitting untouched since yesterday."""
    stamp = _iso(when)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE records SET updated_at=? WHERE lifecycle_uuid=?",
                    (stamp, lifecycle_uuid))
        conn.execute("UPDATE transitions SET at=? WHERE lifecycle_uuid=?",
                    (stamp, lifecycle_uuid))
        conn.commit()
    finally:
        conn.close()


def _insert_overlapping_claim(db_path, lifecycle_uuid, path):
    """Same technique tools/test_bm_stall.py's own
    _do_insert_overlapping_claim uses: a raw INSERT bypasses claim()'s own
    Python-level overlap refusal, the only way to reproduce two DIFFERENT
    active records whose claimed paths disagree."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO claims (lifecycle_uuid, path) VALUES (?, ?)",
                    (lifecycle_uuid, path))
        conn.commit()
    finally:
        conn.close()


def _flip_state_no_transition_row(db_path, lifecycle_uuid, new_state):
    """Fault case 11: changes records.state directly, bypassing
    Store.transition() entirely, so no matching transitions row is ever
    written. transition() always writes both in the same commit; this
    reproduces the one shape its own API cannot produce, the "the UPDATE
    landed, the paired history row did not" half of a partially applied
    write."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE records SET state=? WHERE lifecycle_uuid=?",
                    (new_state, lifecycle_uuid))
        conn.commit()
    finally:
        conn.close()


def _actor(name="tester"):
    return {"actor_type": "model", "actor_name": name}


def _project(pid="p1", **kw):
    d = {"project_id": pid, "name": "Project One",
         "created_at": "2026-08-01T00:00:00Z",
         "updated_at": "2026-08-01T00:00:00Z"}
    d.update(kw)
    return d


def _seed(store, pid="p1"):
    store.upsert_project(_project(pid), _actor())
    return pid


def _sign(store, project_id="p1"):
    return store.sign_contract(
        project_id, "ship it", "tests green", ["."], [],
        ["file-edit", "read-only-inspect"], None, None,
        "Khalil Maaouni", "sess1", _actor("controller"))


def _open_and_plan(store, project_id="p1"):
    """Seed a project, sign a contract, open a run and drive it to
    PLANNING, mirroring tools/test_bm_store.py's own _open_and_plan."""
    _seed(store, project_id)
    _sign(store, project_id)
    actor = _actor("controller")
    run = store.open_run(project_id, "ctrl1", 1, "ship it", "tests green",
                         "fence-ctrl-1", "sess1", actor)
    store.set_run_state(run["run_id"], "ORIENTING", actor, "begin", "sess1")
    store.set_run_state(run["run_id"], "PLANNING", actor, "planned", "sess1")
    return run


def _unit(unit_id, **kw):
    d = {"unit_id": unit_id, "objective": "do the thing", "dependencies": [],
         "read_scope": [], "write_scope": [], "role": "builder",
         "risk_class": "file-edit", "lane": "default", "done_check": "true",
         "done_check_expect_exit": 0, "verifier": "true"}
    d.update(kw)
    return d


class _RootFixture(unittest.TestCase):
    """One throwaway root per test method: small stores, cheap to build
    fresh each time, and it keeps every fixture's raw-sqlite step (where
    used) free of interference from any other test's rows."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_reconcile_test_")
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(self.root)
        self.db_path = os.path.join(self.root, ".brothermode", "store.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self, **kw):
        kw.setdefault("now", NOW)
        kw.setdefault("stale_after_seconds", 14400)
        return RC.reconcile(bs, st, self.root, **kw)

    def _row_for(self, rows, kind, subject_substr):
        for r in rows:
            if r["kind"] == kind and subject_substr in r["subject"]:
                return r
        return None


# ---------------------------------------------------------------------------
# Case 1: an ownership claim persisted, nothing edited, owner still fresh.
# ---------------------------------------------------------------------------

class TestCase1Valid(_RootFixture):
    def test_fresh_active_claim_with_no_drift_is_valid(self):
        store = bs.Store(self.root, create=True)
        try:
            store.claim("case1-fence", "ephemeral", "objective", ["a.py"],
                       session_id="sess-fresh")
        finally:
            store.close()
        rows = self._rows(now=datetime.datetime.now(datetime.timezone.utc))
        row = self._row_for(rows, "record", "case1-fence")
        self.assertIsNotNone(row, "no row for case1-fence: %r" % rows)
        self.assertEqual(row["class"], RC.VALID)


# ---------------------------------------------------------------------------
# Case 3: a dispatch attempt started, nothing heard back since.
# ---------------------------------------------------------------------------

class TestCase3OpenDispatchNoData(_RootFixture):
    def test_open_dispatch_with_no_result_is_no_data(self):
        store = bs.Store(self.root, create=True)
        try:
            run = _open_and_plan(store)
            store.upsert_units(run["run_id"], [_unit("u1")], _actor("controller"))
            store.claim_unit("u1", "fence-u1", _actor("controller"))
            store.record_dispatch("u1", 1, 1, "fence-u1", "s1",
                                  _actor("controller"))
        finally:
            store.close()
        rows = self._rows()
        row = self._row_for(rows, "controller-unit", "u1")
        self.assertIsNotNone(row, "no row for unit u1: %r" % rows)
        self.assertEqual(row["class"], RC.NO_DATA)
        self.assertIn("no outcome", row["reason"])


# ---------------------------------------------------------------------------
# Case 4: a dead owner's provisional record, never promoted or cancelled.
# ---------------------------------------------------------------------------

class TestCase4ProvisionalRecoverable(_RootFixture):
    def test_dead_owner_provisional_record_is_recoverable(self):
        store = bs.Store(self.root, create=True)
        try:
            prov = store.create_provisional_record(
                "case4-idea", session_id="dead-session")
            uuid_ = prov.lifecycle_uuid
        finally:
            store.close()
        _backdate(self.db_path, uuid_, STALE_AGO)
        rows = self._rows()
        row = self._row_for(rows, "record", "case4-idea")
        self.assertIsNotNone(row, "no row for case4-idea: %r" % rows)
        self.assertEqual(row["class"], RC.RECOVERABLE)
        self.assertTrue(row["next_action"],
                        "a RECOVERABLE row must propose a next action")


# ---------------------------------------------------------------------------
# Case 5: delivery marked ready (state=complete), then a later edit.
# ---------------------------------------------------------------------------

class TestCase5SettledThenEditedStale(_RootFixture):
    def test_completed_record_edited_afterward_is_stale(self):
        target = os.path.join(self.root, "out.txt")
        with io.open(target, "w", encoding="utf-8") as fh:
            fh.write("v1")
        store = bs.Store(self.root, create=True)
        try:
            rec = store.claim("case5-fence", "ephemeral", "objective",
                             ["out.txt"], session_id="sess1")
            store.transition(rec.lifecycle_uuid, rec.version, "complete",
                            session_id="sess1", evidence="checked, exit 0")
        finally:
            store.close()
        # The file changes AFTER completion: an explicit future mtime,
        # never a sleep, so the comparison is deterministic regardless of
        # how fast this test runs.
        future = datetime.datetime.now().timestamp() + 3600
        os.utime(target, (future, future))
        rows = self._rows()
        row = self._row_for(rows, "record", "case5-fence")
        self.assertIsNotNone(row, "no row for case5-fence: %r" % rows)
        self.assertEqual(row["class"], RC.STALE)
        self.assertIn("out.txt", row["reason"])

    def test_completed_record_with_no_later_edit_is_valid(self):
        target = os.path.join(self.root, "out.txt")
        with io.open(target, "w", encoding="utf-8") as fh:
            fh.write("v1")
        store = bs.Store(self.root, create=True)
        try:
            rec = store.claim("case5b-fence", "ephemeral", "objective",
                             ["out.txt"], session_id="sess1")
            store.transition(rec.lifecycle_uuid, rec.version, "complete",
                            session_id="sess1", evidence="checked, exit 0")
        finally:
            store.close()
        rows = self._rows()
        row = self._row_for(rows, "record", "case5b-fence")
        self.assertIsNotNone(row, "no row for case5b-fence: %r" % rows)
        self.assertEqual(row["class"], RC.VALID)


# ---------------------------------------------------------------------------
# Case 6: a fence owner record for a dead session.
# ---------------------------------------------------------------------------

class TestCase6DeadOwnerStale(_RootFixture):
    def test_dead_session_fence_is_stale(self):
        store = bs.Store(self.root, create=True)
        try:
            rec = store.claim("case6-fence", "ephemeral", "objective",
                             ["b.py"], session_id="dead-sess")
            uuid_ = rec.lifecycle_uuid
        finally:
            store.close()
        _backdate(self.db_path, uuid_, STALE_AGO)
        rows = self._rows()
        row = self._row_for(rows, "record", "case6-fence")
        self.assertIsNotNone(row, "no row for case6-fence: %r" % rows)
        self.assertEqual(row["class"], RC.STALE)
        self.assertTrue(row["next_action"])


# ---------------------------------------------------------------------------
# Case 7: two registries (the store's own claim() checks versus what the
# rows actually contain) disagree about who owns a path.
# ---------------------------------------------------------------------------

class TestCase7OverlappingClaimsConflict(_RootFixture):
    def test_overlapping_active_claims_are_conflict(self):
        store = bs.Store(self.root, create=True)
        try:
            a = store.claim("case7-a", "ephemeral", "objective",
                           ["shared/x.py"], session_id="s-a")
            b = store.claim("case7-b", "ephemeral", "objective",
                           ["shared/y.py"], session_id="s-b")
            uuid_b = b.lifecycle_uuid
        finally:
            store.close()
        _insert_overlapping_claim(self.db_path, uuid_b, "shared/x.py")
        rows = self._rows()
        conflicts = [r for r in rows if r["kind"] == "record"
                    and r["class"] == RC.CONFLICT
                    and ("case7-a" in r["subject"]
                         or "case7-b" in r["subject"])]
        self.assertTrue(conflicts,
                        "expected at least one CONFLICT row for the "
                        "overlapping case7 claims: %r" % rows)


# ---------------------------------------------------------------------------
# Case 9: status says the work is finished, but no attempt was ever
# reviewed and passed.
# ---------------------------------------------------------------------------

class TestCase9SettledNoReviewNoData(_RootFixture):
    def test_settled_unit_with_no_passing_review_is_no_data(self):
        store = bs.Store(self.root, create=True)
        try:
            run = _open_and_plan(store)
            actor = _actor("controller")
            store.upsert_units(run["run_id"], [_unit("u1")], actor)
            store.claim_unit("u1", "fence-u1", actor)
            did = store.record_dispatch("u1", 1, 1, "fence-u1", "s1", actor)
            store.record_result(did, "claimed finished", [], actor)
            cp = store.record_checkpoint("p1", "ctrl1", "unit-green",
                                         "u1", "s1", actor)
            store.mark_unit_done("u1", cp, actor)
        finally:
            store.close()
        rows = self._rows()
        row = self._row_for(rows, "controller-unit", "u1")
        self.assertIsNotNone(row, "no row for unit u1: %r" % rows)
        self.assertEqual(row["class"], RC.NO_DATA)
        self.assertIn("reviewed", row["reason"])


# ---------------------------------------------------------------------------
# Case 10: an attempt passed review, but a later attempt exists for the
# same unit and it is that later attempt the settled status describes.
# ---------------------------------------------------------------------------

class TestCase10ReviewPredatesLaterAttemptStale(_RootFixture):
    def test_passing_review_predating_a_later_attempt_is_stale(self):
        store = bs.Store(self.root, create=True)
        try:
            run = _open_and_plan(store)
            actor = _actor("controller")
            store.upsert_units(run["run_id"], [_unit("u1")], actor)
            store.claim_unit("u1", "fence-u1", actor)

            did1 = store.record_dispatch("u1", 1, 1, "fence-u1", "s1", actor)
            store.record_result(did1, "attempt one", [], actor)
            store.record_verification(did1, 0, "pass", True, actor)

            # A second attempt happens after the first was reviewed and
            # passed: record_dispatch always reopens the unit, the real
            # engine's own way of saying work resumed on it.
            did2 = store.record_dispatch("u1", 2, 1, "fence-u1", "s1", actor)
            store.record_result(did2, "attempt two", [], actor)
            # attempt two is never itself reviewed before the unit is
            # settled: the exact hollow claim this case is about.
            cp = store.record_checkpoint("p1", "ctrl1", "unit-green",
                                         "u1", "s1", actor)
            store.mark_unit_done("u1", cp, actor)
        finally:
            store.close()
        rows = self._rows()
        row = self._row_for(rows, "controller-unit", "u1")
        self.assertIsNotNone(row, "no row for unit u1: %r" % rows)
        self.assertEqual(row["class"], RC.STALE)
        self.assertIn("attempt 1", row["reason"])
        self.assertIn("attempt 2", row["reason"])


# ---------------------------------------------------------------------------
# Case 11: the store was partially updated (a state changed with no
# matching history row), the shape verify() already exists to catch.
# ---------------------------------------------------------------------------

class TestCase11PartialUpdateConflict(_RootFixture):
    def test_state_with_no_matching_transition_row_is_conflict(self):
        store = bs.Store(self.root, create=True)
        try:
            rec = store.claim("case11-fence", "ephemeral", "objective",
                             ["c.py"], session_id="s1")
            uuid_ = rec.lifecycle_uuid
        finally:
            store.close()
        _flip_state_no_transition_row(self.db_path, uuid_, "complete")
        rows = self._rows()
        integrity = [r for r in rows if r["kind"] == "store-integrity"
                    and r["class"] == RC.CONFLICT]
        self.assertTrue(integrity,
                        "expected a store-integrity CONFLICT row: %r" % rows)
        self.assertTrue(any("case11-fence" in r["reason"] for r in integrity),
                        "the CONFLICT row should name the affected record: "
                        "%r" % integrity)


# ---------------------------------------------------------------------------
# Case 12: running reconciliation twice against an unchanged store changes
# nothing and yields the identical classification both times.
# ---------------------------------------------------------------------------

class TestCase12Idempotent(_RootFixture):
    # SQLite's shared-memory index changes on CONNECTION alone, including a
    # read-only one, so hashing it makes an idempotence check whose own
    # measurement is not idempotent. Measured on this machine 2026-08-24:
    # a162be18cc427a45 after the creating connection closed, then
    # fd4c9fda9cd3f9ae during and after a later READ-ONLY connect, with no
    # write of any kind in between. That second value is exactly what this
    # test used to report as evidence that reconcile() had written bytes.
    #
    # The WAL is NOT excluded and must never be. In WAL mode a real write
    # lands there first and may not be checkpointed into the database file at
    # all, so dropping it would turn this test into one that cannot see the
    # very thing it exists to catch. Excluding -shm removes noise; excluding
    # -wal would remove the signal.
    _IGNORED_SUFFIXES = ("-shm",)

    def _snapshot(self):
        out = {}
        for base, _dirs, files in os.walk(self.root):
            for name in files:
                if name.endswith(self._IGNORED_SUFFIXES):
                    continue
                full = os.path.join(base, name)
                rel = os.path.relpath(full, self.root)
                with io.open(full, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def test_the_snapshot_still_sees_a_real_write(self):
        """Calibration: the exclusion above must not blind this check.

        A check that cannot fail cannot verify, so this proves the snapshot
        still catches a byte actually written to the store, which is the
        property test_second_run... asserts the absence of."""
        store = bs.Store(self.root, create=True)
        try:
            before = self._snapshot()
            store.claim("calibration-fence", "ephemeral", "objective",
                        ["cal.py"], session_id="cal-sess")
        finally:
            store.close()
        after = self._snapshot()
        self.assertNotEqual(before, after,
                            "a real write must change the snapshot; if this "
                            "passes, the ignore list has blinded the check")

    def test_second_run_reports_zero_changes_and_identical_classification(self):
        store = bs.Store(self.root, create=True)
        try:
            rec = store.claim("case12-fence", "ephemeral", "objective",
                             ["d.py"], session_id="dead-sess")
            uuid_ = rec.lifecycle_uuid
        finally:
            store.close()
        _backdate(self.db_path, uuid_, STALE_AGO)

        before = self._snapshot()
        first = self._rows()
        after_first = self._snapshot()
        second = self._rows()
        after_second = self._snapshot()

        self.assertEqual(before, after_first,
                         "reconcile() must write zero bytes (first run)")
        self.assertEqual(after_first, after_second,
                         "reconcile() must write zero bytes (second run)")
        self.assertEqual(first, second,
                         "a second run against an unchanged store must "
                         "yield the identical classification")


# ---------------------------------------------------------------------------
# Case 13: a malformed store is NO-DATA, naming the file, never a crash
# and never a false healthy verdict.
# ---------------------------------------------------------------------------

class TestCase13MalformedStoreNoData(_RootFixture):
    def test_corrupt_store_file_is_no_data_naming_the_path(self):
        store = bs.Store(self.root, create=True)
        store.close()
        with io.open(self.db_path, "wb") as fh:
            fh.write(b"this is not a sqlite database, on purpose")
        rows = self._rows()
        self.assertEqual(len(rows), 1,
                         "an unreadable store must short-circuit the "
                         "whole pass to one row: %r" % rows)
        self.assertEqual(rows[0]["class"], RC.NO_DATA)
        self.assertIn(self.db_path, rows[0]["reason"])


# ---------------------------------------------------------------------------
# Case 14: whether the work has been pushed anywhere is unobservable
# without an upstream remote.
# ---------------------------------------------------------------------------

class TestCase14PushStateUnobservable(_RootFixture):
    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.root,
                              capture_output=True, text=True, timeout=15)

    def test_repo_with_no_upstream_is_no_data(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed on this machine")
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        # Store construction is AFTER git init on purpose: its own
        # containment check provisions the ignore rule against a git
        # context that already exists, the natural real-world order.
        store = bs.Store(self.root, create=True)
        store.close()
        # A real repo needs one real tracked file, or there is nothing
        # for `git commit` to commit (the store itself is gitignored by
        # design): the store's own ignore rule doing its job is exactly
        # why this fixture cannot rely on the store to provide one.
        with io.open(os.path.join(self.root, "README.md"), "w",
                    encoding="utf-8") as fh:
            fh.write("fixture repo for bm_reconcile's push-state test\n")
        self._git("add", "-A")
        commit = self._git("commit", "-q", "-m", "init")
        if commit.returncode != 0:
            self.skipTest("git commit failed in this environment: %s"
                          % (commit.stderr or commit.stdout))
        rows = self._rows()
        row = self._row_for(rows, "push-state", self.root)
        self.assertIsNotNone(row, "no push-state row: %r" % rows)
        self.assertEqual(row["class"], RC.NO_DATA)
        self.assertIn("upstream", row["reason"])

    def test_non_git_root_has_no_push_state_row(self):
        store = bs.Store(self.root, create=True)
        store.close()
        rows = self._rows()
        row = self._row_for(rows, "push-state", self.root)
        self.assertIsNone(row,
                          "a non-git root should say nothing about push "
                          "state, not manufacture a finding: %r" % rows)


# ---------------------------------------------------------------------------
# Cases 2 and 8: honest skips. See docs/RECOVERY-TRUTH.md, "Cases 2 and 8:
# why they are skipped, not stubbed" for the full reasoning; the short
# version is in each skip message below.
# ---------------------------------------------------------------------------

class TestCase2Skipped(unittest.TestCase):
    def test_edit_made_post_write_audit_missing(self):
        self.skipTest(
            "no persisted signal distinguishes a real edit under a still-"
            "open claim whose audit is legitimately overdue from ordinary "
            "in-progress work, without a staleness threshold or a new "
            "persisted field this pass is not positioned to invent; see "
            "docs/RECOVERY-TRUTH.md, cases 2 and 8")


class TestCase8Skipped(unittest.TestCase):
    def test_worktree_changed_outside_the_recorded_writer(self):
        self.skipTest(
            "bm_stall.foreign_commit_base_finding exists for this shape "
            "but takes the claimed base commit sha as a caller-supplied "
            "parameter; no table in today's schema records which commit a "
            "claim was made against, so there is nowhere to source a real "
            "value from for this fixture; see docs/RECOVERY-TRUTH.md, "
            "cases 2 and 8")


if __name__ == "__main__":
    unittest.main()

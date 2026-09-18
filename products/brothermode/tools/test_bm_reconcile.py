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
import json
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


# ---------------------------------------------------------------------------
# Owner, liveness, route and lineage (2026-09-17), against a REAL temporary
# store driven through the real CLI: `git init` in a fresh directory,
# `bm_store.py init` there, never the estate's own store. The same pattern
# scripts/test_cut.py's PrecedenceAgainstARealTemporaryStore uses: a
# far-future --now makes every owner's heartbeat DEAD for bm_stall.
# ---------------------------------------------------------------------------

class OwnerRouteAgainstARealTemporaryStore(unittest.TestCase):
    FUTURE = "2030-01-01T00:00:00Z"
    HARNESS = "harness-session-for-reconcile-test"

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed on this machine")
        # realpath: macOS's /var is a symlink to /private/var and the
        # fence hook refuses a token directory reached through one.
        self.tmp = os.path.realpath(
            tempfile.mkdtemp(prefix="bm_reconcile_realstore_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        self.store_cli = os.path.join(HERE, "bm_store.py")
        self.reconcile_cli = os.path.join(HERE, "bm_reconcile.py")
        self._store(["init"])
        # The session's OWN label, derived from a real token file at
        # token_path(root, harness id); the test may create it (this is
        # a throwaway root), bm_reconcile itself never does.
        self.fh = _load("bm_fence_hook")
        self.my_label = self.fh.session_label(self.tmp, self.HARNESS)
        self._store(["claim", "my-lane", "--session", self.my_label,
                     "--lifetime", "ephemeral", "--objective", "mine",
                     "--files", "scripts/mine.py"])
        self._store(["claim", "their-lane", "--session", "cli-someone-else",
                     "--lifetime", "ephemeral", "--objective", "theirs",
                     "--files", "scripts/theirs.py"])

    def _store(self, args):
        p = subprocess.run([_e100_sys.executable, self.store_cli] + args,
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def _rows(self, *extra):
        p = subprocess.run([_e100_sys.executable, self.reconcile_cli,
                            "--root", self.tmp, "--json"] + list(extra),
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        return {r["subject"].split(" (")[0]: r
                for r in json.loads(p.stdout)["rows"] if r["kind"] == "record"}

    def test_my_derived_label_routes_mine_and_a_dead_foreign_owner_founder(self):
        rows = self._rows("--session-id", self.HARNESS, "--now", self.FUTURE)
        mine = rows["my-lane"]
        self.assertEqual(mine["owner_session"], self.my_label)
        self.assertEqual(mine["owner_confidence"], RC.CONF_DERIVED)
        self.assertEqual(mine["route"], RC.ROUTE_MINE)
        self.assertEqual(mine["decision_class"], 1)
        theirs = rows["their-lane"]
        self.assertEqual(theirs["class"], RC.STALE)
        self.assertEqual(theirs["owner_session"], "cli-someone-else")
        self.assertEqual(theirs["owner_confidence"], RC.CONF_DECLARED)
        self.assertEqual(theirs["owner_liveness"], st.DEAD)
        self.assertEqual(theirs["route"], RC.ROUTE_FOUNDER)
        self.assertEqual(theirs["decision_class"], 2)
        for r in (mine, theirs):
            self.assertTrue(r["lineage"], "lineage must not be empty: %r" % r)
            self.assertEqual(r["lineage"][0]["kind"], "transition")
            self.assertTrue(r["anchor"].startswith("record:"), r["anchor"])
            self.assertTrue(r["observed_ref"] == "" or len(r["observed_ref"]) == 40)
            self.assertEqual(len(r["fingerprint"]), 16)
        self.assertNotEqual(mine["fingerprint"], theirs["fingerprint"])

    def test_a_cli_style_id_with_no_token_is_declared_never_mine(self):
        # Asking as the very id the foreign record carries: no token file
        # exists for it, so no label derives, so it is still not mine.
        rows = self._rows("--session-id", "cli-someone-else")
        theirs = rows["their-lane"]
        self.assertEqual(theirs["owner_confidence"], RC.CONF_DECLARED)
        self.assertNotEqual(theirs["route"], RC.ROUTE_MINE)

    def test_a_live_foreign_owner_routes_owner_and_is_left_alone(self):
        rows = self._rows("--session-id", self.HARNESS)
        theirs = rows["their-lane"]
        self.assertEqual(theirs["class"], RC.VALID)
        self.assertEqual(theirs["owner_liveness"], st.LIVE)
        self.assertEqual(theirs["route"], RC.ROUTE_OWNER)

    def test_no_session_id_means_nothing_is_mine(self):
        rows = self._rows()
        self.assertNotEqual(rows["my-lane"]["route"], RC.ROUTE_MINE)

    def test_a_hand_typed_bm1_label_is_declared_not_derived(self):
        # Checker finding 1 (2026-09-17): a bm1- prefix alone proves
        # nothing; derived means THIS sweep recomputed the label from a
        # token file it read. Only the calling session's own label can be.
        self._store(["claim", "lane-hand", "--session",
                     "bm1-0000000000000000000000ab", "--lifetime",
                     "ephemeral", "--objective", "typed", "--files",
                     "scripts/hand.py"])
        rows = self._rows("--session-id", self.HARNESS)
        self.assertEqual(rows["lane-hand"]["owner_confidence"],
                         RC.CONF_DECLARED)
        self.assertNotEqual(rows["lane-hand"]["route"], RC.ROUTE_MINE)
        self.assertEqual(rows["my-lane"]["owner_confidence"], RC.CONF_DERIVED)
        # Without a session id nothing was recomputed, so even my own
        # label is only declared.
        self.assertEqual(self._rows()["my-lane"]["owner_confidence"],
                         RC.CONF_DECLARED)


# ---------------------------------------------------------------------------
# Unpushed hook dependency (2026-09-17): a commit touching a path an
# installed hook reads, on no remote branch, is a CONFLICT owned by its git
# author; the same commit reachable from a remote-tracking ref is not.
# ---------------------------------------------------------------------------

class UnpushedHookDependency(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed on this machine")
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bm_reconcile_unpushed_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test Author")
        os.makedirs(os.path.join(self.tmp, "scripts"))
        with io.open(os.path.join(self.tmp, "scripts", "decide.py"), "w",
                     encoding="utf-8") as fh:
            fh.write("# stamps the screen intake_gate.py reads\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "decide.py checkpoint")

    def _git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.tmp,
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_unpushed_commit_touching_a_hook_read_path_is_conflict(self):
        rows = RC.classify_unpushed_hook_deps(self.tmp, ref="abc")
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["class"], RC.CONFLICT)
        self.assertEqual(row["kind"], "unpushed-hook-dependency")
        self.assertEqual(row["subject"], "scripts/decide.py")
        self.assertIn("intake_gate.py", row["reason"])
        self.assertEqual(row["owner_session"], "Test Author")
        self.assertEqual(row["owner_confidence"], RC.CONF_DECLARED)
        self.assertEqual(row["route"], RC.ROUTE_FOUNDER)
        self.assertEqual(row["anchor"], "file:scripts/decide.py")
        self.assertEqual([c["kind"] for c in row["lineage"]], ["commit"])
        self.assertEqual(row["observed_ref"], "abc")

    def test_same_commit_on_a_remote_branch_is_not_a_finding(self):
        head = self._git("rev-parse", "HEAD").strip()
        self._git("update-ref", "refs/remotes/origin/main", head)
        self.assertEqual(RC.classify_unpushed_hook_deps(self.tmp), [])

    def test_same_patch_upstream_under_another_sha_is_not_a_finding(self):
        # Checker finding 3: a squash or cherry-pick puts the identical
        # change on the remote under a different sha; the hook then
        # depends on nothing unpushed. Patch-equivalence, not sha identity.
        head = self._git("rev-parse", "HEAD").strip()
        # Rebuild HEAD's tree on the same (empty) ancestry with another
        # message: a different sha carrying the identical patch.
        tree = self._git("rev-parse", "HEAD^{tree}").strip()
        other = self._git("commit-tree", tree, "-m", "squashed elsewhere").strip()
        self.assertNotEqual(other, head)
        self._git("update-ref", "refs/remotes/origin/main", other)
        self.assertEqual(RC.classify_unpushed_hook_deps(self.tmp), [],
                         "identical patch upstream under another sha")
        # And a genuinely new change on top is still one finding.
        with io.open(os.path.join(self.tmp, "scripts", "decide.py"), "a",
                     encoding="utf-8") as fh:
            fh.write("# newer\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "decide.py again")
        rows = RC.classify_unpushed_hook_deps(self.tmp)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(len(rows[0]["lineage"]), 1, rows[0]["lineage"])

    def test_root_below_the_toplevel_still_finds_the_commit(self):
        # Checker finding 4: a --root inside a subdirectory made the
        # pathspec relative to that directory and the detector went blind.
        sub = os.path.join(self.tmp, "scripts")
        rows = RC.classify_unpushed_hook_deps(sub)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["subject"], "scripts/decide.py")

    def test_unrelated_unpushed_commit_is_not_a_finding(self):
        head = self._git("rev-parse", "HEAD").strip()
        self._git("update-ref", "refs/remotes/origin/main", head)
        with io.open(os.path.join(self.tmp, "other.txt"), "w",
                     encoding="utf-8") as fh:
            fh.write("unrelated\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "unrelated")
        self.assertEqual(RC.classify_unpushed_hook_deps(self.tmp), [])


# ---------------------------------------------------------------------------
# file, the one write (2026-09-17), against a REAL temporary store: filed
# once per fingerprint, a resolved note stays filed, records untouched.
# ---------------------------------------------------------------------------

class FileVerbAgainstARealTemporaryStore(unittest.TestCase):
    FUTURE = "2030-01-01T00:00:00Z"

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed on this machine")
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="bm_reconcile_file_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        self.store_cli = os.path.join(HERE, "bm_store.py")
        self.learn_cli = os.path.join(HERE, "bm_learn.py")
        self.reconcile_cli = os.path.join(HERE, "bm_reconcile.py")
        self._cli(self.store_cli, "init")
        self._cli(self.store_cli, "claim", "dead-lane", "--session",
                  "cli-dead-owner", "--lifetime", "ephemeral", "--objective",
                  "old work", "--files", "scripts/old.py")

    def _cli(self, tool, *args):
        p = subprocess.run([_e100_sys.executable, tool] + list(args),
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def _file(self):
        out = json.loads(self._cli(self.reconcile_cli, "file", "--root",
                                   self.tmp, "--now", self.FUTURE, "--json"))
        return out["filed"], out["already_filed"], out["unfileable"]

    def _alerts(self, state="active"):
        # --raw: an ordinary dump withholds note bodies (default-deny
        # redaction), and the fingerprint this test reads lives in the body.
        d = json.loads(self._cli(self.store_cli, "dump", "--raw"))
        self.assertEqual([r["state"] for r in d["records"]], [state],
                         "file must never touch a record")
        return [n for n in d["notes"] if n["kind"] == "alert"]

    def test_file_twice_writes_once_per_fingerprint_and_a_resolved_note_stays_filed(self):
        filed, already, unfileable = self._file()
        self.assertGreaterEqual(filed, 1, (filed, already, unfileable))
        self.assertEqual((already, unfileable), (0, []))
        first = self._alerts()
        self.assertEqual(len(first), filed)
        self.assertTrue(all(n["resolved_at"] is None for n in first))
        fps = [json.loads(n["body"])["fingerprint"] for n in first]
        self.assertEqual(len(fps), len(set(fps)), "one open alert per fingerprint")
        record_notes = [n for n in first if n["anchor_type"] == "record"]
        self.assertEqual(len(record_notes), 1, first)
        self.assertEqual(record_notes[0]["session_id"], "")
        self.assertEqual(record_notes[0]["severity"], "warning")

        self.assertEqual(self._file(), (0, filed, []))
        self.assertEqual(len(self._alerts()), filed, "second file wrote zero")

        # Resolve through the store's existing resolve path (bm_learn
        # resolve-note), with a real receipt minted for that note.
        note_id = record_notes[0]["note_uuid"][:8]
        because = "founder parked the dead lane"
        rec = json.loads(self._cli(
            self.learn_cli, "grant-state-receipt", "resolve-note", note_id,
            "--answer", "parked it by hand", "--because", because, "--json"))
        self._cli(self.learn_cli, "resolve-note", note_id, "--because",
                  because, "--receipt", rec["token"])
        # Checker finding 2 (2026-09-17): a resolved note is an answered
        # question; the SAME finding occurring again is a new question,
        # so it files again. Dedupe is against OPEN notes only.
        self.assertEqual(self._file(), (1, filed - 1, []),
                         "a new occurrence after resolution files again")
        after = self._alerts()
        self.assertEqual(len(after), filed + 1)
        self.assertEqual(len([n for n in after if n["resolved_at"]]), 1)

    def _git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.tmp,
                           capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_fingerprint_is_stable_across_commits_and_changes_with_the_finding(self):
        # Checker finding 2: the id must survive a new commit (observed_ref
        # moves, the finding does not) and must change when the finding's
        # class or reason category changes, or a later distinct finding
        # on the same record and owner is silently never filed.
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "T")
        with io.open(os.path.join(self.tmp, "scripts", "old.py"), "w",
                     encoding="utf-8") as fh:
            fh.write("v1\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "one")
        filed, _already, _un = self._file()
        record_notes = [n for n in self._alerts() if n["anchor_type"] == "record"]
        self.assertEqual(len(record_notes), 1)
        first = json.loads(record_notes[0]["body"])
        self.assertEqual(first["class"], RC.STALE)

        # Same finding, new commit: files nothing new.
        with io.open(os.path.join(self.tmp, "README"), "w",
                     encoding="utf-8") as fh:
            fh.write("two\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "two")
        self.assertEqual(self._file()[0], 0, "unchanged finding across commits")

        # Same record, same owner, a different reason category: the dead
        # fence is completed, then its claimed file is edited afterward
        # (case 5), a distinct STALE finding that must file as a new note.
        d = json.loads(self._cli(self.store_cli, "dump"))
        rec = [r for r in d["records"] if r["name"] == "dead-lane"][0]
        self._cli(self.store_cli, "complete", rec["lifecycle_uuid"],
                  "--session", "cli-dead-owner", "--version",
                  str(rec["version"]), "--evidence", "done")
        future = datetime.datetime.now().timestamp() + 3600
        os.utime(os.path.join(self.tmp, "scripts", "old.py"), (future, future))
        self.assertEqual(self._file()[0], 1, "a changed reason files anew")
        notes = [json.loads(n["body"]) for n in self._alerts(state="complete")
                 if n["anchor_type"] == "record"]
        self.assertEqual(len(notes), 2)
        self.assertNotEqual(notes[0]["fingerprint"], notes[1]["fingerprint"])
        self.assertNotEqual(notes[0]["category"], notes[1]["category"])
        self.assertEqual(self._file()[0], 0, "and only once")

    def test_file_refuses_without_a_store_and_writes_nothing(self):
        empty = os.path.realpath(tempfile.mkdtemp(prefix="bm_reconcile_nostore_"))
        self.addCleanup(shutil.rmtree, empty, True)
        p = subprocess.run([_e100_sys.executable, self.reconcile_cli, "file",
                            "--root", empty], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("NO-DATA", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(empty, ".brothermode")),
                         "file must never create a store")


class Night0912BmReconcile(unittest.TestCase):
    def test_git_unavailable_yields_no_data(self):
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
            rows = RC.classify_push_state(d)
            self.assertTrue(any(r["class"] == RC.NO_DATA for r in rows),
                            "expected NO-DATA row when git unavailable")
        finally:
            os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()

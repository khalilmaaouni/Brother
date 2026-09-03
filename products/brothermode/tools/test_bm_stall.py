#!/usr/bin/env python3
"""Tests for tools/bm_stall.py, the active stall/dead-owner/orphan sweep.

WHAT THIS SUITE PROVES
  1. SD1 -- owner_liveness() judges synthetic live and dead owners
     correctly, both directions, including the PID-reuse caveat: a live
     pid with a stale heartbeat and no controller registration is DEAD,
     never LIVE, by name.
  2. SD2 -- sweep(), run against a REAL throwaway store (built the same
     way tools/test_bm_effects.py's own fixture is: bm_store.py init plus
     real claim() calls, backdated by hand where a scenario needs a
     scar older than "just now"), finds all of: the five 2026-08-10
     stale-fence shapes, duplicate/contradictory claims across two
     distinct active fences, and a PID-reused-by-unrelated-process
     fence (a live pid, but a stale heartbeat and no controller
     registration -- still DEAD).
  3. NO-DATA DISCIPLINE -- a hollowed (truncated) store is reported
     NO-DATA (exit 2), never a healthy PASS (exit 0).
  4. PURITY -- sweep() itself changes zero bytes of the store, snapshot
     compared before and after (the same technique
     tools/test_bm_effects.py's TestPurity uses).
  5. SD5 -- the exact command a Finding prints, run as its own real
     subprocess (the ONLY place in this suite that ever executes one),
     clears that finding on the next sweep; the sweep never ran it
     itself.

ISOLATION: every subprocess here runs with a from-scratch environment
(HOME, BROTHERMODE_VAULT, BROTHERSBE_VAULT, BROTHERMODE_ROOT all pinned
inside one throwaway tempfile tree), matching
tools/test_bm_effects.py's own ISOLATION note and for the same reason:
BROTHERMODE_VAULT is exported ambient on the machine this project usually
runs on and wins over HOME, so a HOME-only override would not isolate a
subprocess from a real founder vault.

Python 3.9, standard library only. Run:
  python3 tools/test_bm_stall.py
"""
import datetime
import hashlib
import importlib.util as _ilu
import io
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = HERE

_stall_spec = _ilu.spec_from_file_location("bm_stall", os.path.join(HERE, "bm_stall.py"))
ST = _ilu.module_from_spec(_stall_spec)
_stall_spec.loader.exec_module(ST)

_bs_spec = _ilu.spec_from_file_location("bm_store_for_bm_stall_tests", os.path.join(HERE, "bm_store.py"))
BS = _ilu.module_from_spec(_bs_spec)
_bs_spec.loader.exec_module(BS)

NOW = datetime.datetime(2026, 8, 11, 12, 0, 0, tzinfo=datetime.timezone.utc)
STALE_AGO = NOW - datetime.timedelta(hours=30)  # well past a 4h window
FRESH_AGO = NOW - datetime.timedelta(minutes=2)  # well within a 4h window


def _iso(dt):
    return dt.strftime(BS._ISO_STAMP_FORMAT)


# ---------------------------------------------------------------------------
# Plain functions (no `self`, no TestCase machinery) so setUpClass -- which
# runs before any instance exists -- can build fixture data without the
# fragile cls.__new__(cls) shape unittest instance methods would otherwise
# need. Every _StoreFixture instance method below is a thin wrapper over
# one of these, for the ordinary case (inside a real test method, with a
# real `self` to assert through).
# ---------------------------------------------------------------------------

def _run_module(root, env, module, argv, timeout=30):
    path = os.path.join(TOOLS_DIR, module)
    return subprocess.run([sys.executable, path] + list(argv), cwd=root,
                          env=env, capture_output=True, text=True,
                          timeout=timeout)


def _row_by_name(db_path, name):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM records WHERE name=? AND state='active'",
            (name,)).fetchone()
        if row is None:
            raise AssertionError("no active record named %r" % name)
        return dict(row)
    finally:
        conn.close()


def _do_claim(root, env, db_path, name, session_id,
              files=("probe/target-%s.py",)):
    # lifetime=ephemeral, not persistent: MAX_ACTIVE_PERSISTENT
    # (tools/bm_store.py:84) caps a store at 3 simultaneous active
    # persistent records, and this fixture needs more than that to
    # reproduce five stale fences plus the duplicate/pid-reuse cases at
    # once. Ephemeral is also the closer match for what these fixture
    # rows represent (transient work claims), not a schema workaround.
    files = [f % name if "%s" in f else f for f in files]
    result = _run_module(root, env, "bm_store.py", [
        "claim", name, "--lifetime", "ephemeral",
        "--objective", "bm_stall fixture: %s" % name,
        "--files"] + files + ["--session", session_id])
    if result.returncode != 0:
        raise AssertionError("fixture claim %r failed: %s%s"
                             % (name, result.stdout, result.stderr))
    row = _row_by_name(db_path, name)
    return row["lifecycle_uuid"], row["version"]


def _do_backdate(db_path, lifecycle_uuid, when):
    """Set a record's own updated_at AND every transition row it owns to
    `when`, directly against the sqlite file: the only way to make a real,
    otherwise-healthy fence look like it has been sitting untouched since
    yesterday, the exact shape of the 2026-08-10 scars this suite
    reproduces."""
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


def _do_insert_overlapping_claim(db_path, lifecycle_uuid, path):
    """A raw INSERT into claims, bypassing Store.claim()'s own Python-level
    overlap refusal entirely: the only way to reproduce 'contradictory
    duplicate fence lines' between two DIFFERENT active fences, since
    claim() itself would refuse it live. The claims table carries no
    DB-level overlap constraint (only records.name has a partial unique
    index), so this insert succeeds where a same-name duplicate record
    could not."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO claims (lifecycle_uuid, path) VALUES (?, ?)",
                    (lifecycle_uuid, path))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SD1: the oracle, pure unit tests, no store at all.
# ---------------------------------------------------------------------------

class TestOwnerLiveness(unittest.TestCase):
    def test_fresh_heartbeat_is_live(self):
        signals = ST.OwnerSignals(pid=None, pid_alive=None,
                                   heartbeat_age_seconds=60.0,
                                   session_registered_active=False)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.LIVE, verdict)
        self.assertIn("heartbeat age", reason)

    def test_registered_controller_session_is_live_despite_stale_heartbeat(self):
        signals = ST.OwnerSignals(pid=None, pid_alive=None,
                                   heartbeat_age_seconds=99999.0,
                                   session_registered_active=True)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.LIVE, verdict)
        self.assertIn("controller run", reason)

    def test_no_heartbeat_evidence_at_all_is_unknown(self):
        signals = ST.OwnerSignals(pid=None, pid_alive=None,
                                   heartbeat_age_seconds=None,
                                   session_registered_active=False)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.UNKNOWN, verdict)

    def test_stale_heartbeat_no_pid_no_registration_is_dead(self):
        signals = ST.OwnerSignals(pid=None, pid_alive=None,
                                   heartbeat_age_seconds=99999.0,
                                   session_registered_active=False)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.DEAD, verdict)

    def test_confirmed_dead_pid_strengthens_dead_verdict(self):
        signals = ST.OwnerSignals(pid=999999999, pid_alive=False,
                                   heartbeat_age_seconds=99999.0,
                                   session_registered_active=False)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.DEAD, verdict)
        self.assertIn("no longer exists", reason)

    def test_pid_reuse_caveat_a_live_pid_with_stale_heartbeat_is_still_dead(self):
        """THE load-bearing case: a pid that answers alive, on its own,
        must never flip a stale, unregistered owner to LIVE. This is SD.1's
        own sentence: 'PID reuse must not be treated as sufficient proof
        of liveness.'"""
        signals = ST.OwnerSignals(pid=os.getpid(), pid_alive=True,
                                   heartbeat_age_seconds=99999.0,
                                   session_registered_active=False)
        verdict, reason = ST.owner_liveness(signals, stale_after_seconds=14400)
        self.assertEqual(ST.DEAD, verdict,
                         "a live pid alone flipped a stale owner to non-DEAD")
        self.assertIn("PID REUSE", reason)


# ---------------------------------------------------------------------------
# A real throwaway store, built the way tools/test_bm_effects.py's own
# fixture is (bm_store.py init, real bm_store.py claim calls), then
# selectively backdated by hand for the scars that need to look old.
# ---------------------------------------------------------------------------

class _StoreFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_stall_test_")
        cls.root = os.path.join(cls.tmp, "project")
        os.makedirs(cls.root)
        home = os.path.join(cls.tmp, "home")
        os.makedirs(home)
        vault = os.path.join(cls.tmp, "vault")
        cls.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "BROTHERMODE_VAULT": vault,
            "BROTHERSBE_VAULT": vault,
            "BROTHERMODE_ROOT": cls.root,
        }
        init = cls._run("bm_store.py", ["init"])
        if init.returncode != 0:
            raise AssertionError("fixture setup: bm_store.py init failed "
                                 "(exit %d): %s%s"
                                 % (init.returncode, init.stdout, init.stderr))
        cls.db_path = os.path.join(cls.root, ".brothermode", "store.sqlite3")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _run(cls, module, argv, timeout=30):
        path = os.path.join(TOOLS_DIR, module)
        return subprocess.run([sys.executable, path] + list(argv),
                              cwd=cls.root, env=cls.env, capture_output=True,
                              text=True, timeout=timeout)

    def _claim(self, name, session_id, files=("probe/target-%s.py",)):
        return _do_claim(self.root, self.env, self.db_path, name, session_id,
                         files=files)

    def _row(self, name):
        return _row_by_name(self.db_path, name)

    def _backdate(self, lifecycle_uuid, when):
        _do_backdate(self.db_path, lifecycle_uuid, when)

    def _insert_overlapping_claim(self, lifecycle_uuid, path):
        _do_insert_overlapping_claim(self.db_path, lifecycle_uuid, path)

    def _open_readonly(self):
        return BS.ReadOnlyStore(self.root)

    def _snapshot(self):
        out = {}
        for base, _dirs, files in os.walk(self.root):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, self.root)
                with io.open(full, "rb") as fh:
                    data = fh.read()
                out[rel] = hashlib.sha256(data).hexdigest()
        return out


# ---------------------------------------------------------------------------
# SD2: the seeded corpus. RED FIRST: run against the fixture below before
# sweep() has real finding logic (see this session's report for the
# quoted RED), then GREEN once it does.
# ---------------------------------------------------------------------------

class TestSeededCorpus(_StoreFixture):
    @classmethod
    def setUpClass(cls):
        super(TestSeededCorpus, cls).setUpClass()
        # The five real 2026-08-10 stale-fence cases (README.md, SKILL.md
        # twice, the findings ledger, the narrowing work itself): five
        # distinct active records, five distinct dead sessions, all
        # backdated well past any reasonable staleness window and with no
        # controller registration and no pid known for any of them.
        cls.stale_names = ["readme-fence", "skill-fence-a", "skill-fence-b",
                          "findings-ledger-fence", "narrowing-work-fence"]
        cls.stale_uuids = {}
        for name in cls.stale_names:
            uuid_, _version = _do_claim(cls.root, cls.env, cls.db_path, name,
                                        "dead-session-" + name)
            cls.stale_uuids[name] = uuid_
            _do_backdate(cls.db_path, uuid_, STALE_AGO)

        # Duplicate/contradictory fence lines: two DIFFERENT active
        # records whose claimed paths overlap.
        cls.dup_a_uuid, _ = _do_claim(cls.root, cls.env, cls.db_path,
                                      "dup-fence-a", "dup-session-a",
                                      files=("shared/overlap.py",))
        cls.dup_b_uuid, _ = _do_claim(cls.root, cls.env, cls.db_path,
                                      "dup-fence-b", "dup-session-b",
                                      files=("shared/other.py",))
        _do_insert_overlapping_claim(cls.db_path, cls.dup_b_uuid,
                                     "shared/overlap.py")

        # PID reused by an unrelated process: a stale, unregistered
        # session whose pid_hint points at a REAL live process (this test
        # process itself), which must still be judged DEAD.
        cls.pidreuse_uuid, _ = _do_claim(cls.root, cls.env, cls.db_path,
                                         "pid-reuse-fence", "pid-reuse-session")
        _do_backdate(cls.db_path, cls.pidreuse_uuid, STALE_AGO)

    def _sweep(self, pid_hints=None):
        store = self._open_readonly()
        try:
            return ST.sweep(BS, store, now=NOW, stale_after_seconds=14400,
                            pid_hints=pid_hints)
        finally:
            store.close()

    def test_all_five_stale_fence_cases_are_found(self):
        findings = self._sweep()
        found_uuids = {f["lifecycle_uuid"] for f in findings
                      if f["kind"] in (ST.STALE_FENCE, ST.DEAD_WATCHDOG)}
        missing = [name for name, u in self.stale_uuids.items()
                  if u not in found_uuids]
        self.assertEqual(
            [], missing,
            "sweep() did not report a stale-fence finding for: %s "
            "(found kinds: %s)" % (missing, sorted(f["kind"] for f in findings)))

    def test_duplicate_fence_lines_are_found(self):
        findings = self._sweep()
        dup_findings = [f for f in findings if f["kind"] == ST.DUPLICATE_FENCE_LINES]
        self.assertTrue(
            dup_findings,
            "sweep() did not report any duplicate-fence-lines finding for "
            "the overlapping claim between dup-fence-a and dup-fence-b")
        overlap_hit = any("overlap" in f["message"] for f in dup_findings)
        self.assertTrue(overlap_hit,
                        "no duplicate-fence-lines finding mentioned the overlap")

    def test_pid_reused_by_unrelated_process_is_still_dead(self):
        findings = self._sweep(pid_hints={"pid-reuse-session": os.getpid()})
        hit = [f for f in findings if f["lifecycle_uuid"] == self.pidreuse_uuid]
        self.assertTrue(
            hit, "sweep() did not report the PID-reused fence as stale at all")
        self.assertIn("PID REUSE", hit[0]["message"],
                      "the finding did not name the PID-reuse caveat")

    def test_every_finding_carries_a_printable_clearing_command_never_run(self):
        findings = self._sweep()
        self.assertTrue(findings)
        for f in findings:
            self.assertTrue(f["actions"], "finding %r has no actions" % f["kind"])
            for action in f["actions"]:
                self.assertIn("command", action)
                # P17: the printed command must be resolved for the reader's real
        # layout (a console script, or python3 plus an absolute path),
        # never the repo-relative form that lies in a packaged install.
        self.assertNotIn("python3 tools/", action["command"])
        self.assertTrue(
            action["command"].startswith(("bm-store", "bm-learn", "python3 /")),
            action["command"])


# ---------------------------------------------------------------------------
# NO-DATA discipline: a hollowed store must never look healthy.
# ---------------------------------------------------------------------------

class TestNoDataDiscipline(_StoreFixture):
    def test_truncated_store_is_no_data_never_a_pass(self):
        hollow_root = os.path.join(self.tmp, "hollow")
        os.makedirs(hollow_root)
        env = dict(self.env)
        env["BROTHERMODE_ROOT"] = hollow_root
        init = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, "bm_store.py"), "init"],
            cwd=hollow_root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, init.returncode)
        db = os.path.join(hollow_root, ".brothermode", "store.sqlite3")
        with io.open(db, "wb") as fh:
            fh.write(b"")  # hollowed: zero bytes, the exact shape a
                           # truncated write-in-progress leaves behind
        result = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, "bm_stall.py"), "sweep"],
            cwd=hollow_root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(2, result.returncode,
                         "a hollowed store must exit 2 (NO-DATA), got %d: %s%s"
                         % (result.returncode, result.stdout, result.stderr))
        self.assertTrue(
            result.stdout.startswith("NO-DATA:"),
            "a hollowed store's sweep must start with NO-DATA:, got: %r"
            % result.stdout)
        self.assertNotIn("0 finding(s)", result.stdout,
                         "a hollowed store must never be reported as a "
                         "clean 0-finding pass")


# ---------------------------------------------------------------------------
# Purity: sweep() itself writes nothing.
# ---------------------------------------------------------------------------

class TestPurity(_StoreFixture):
    def test_sweep_changes_zero_bytes(self):
        self._claim("purity-probe-fence", "purity-probe-session")
        before = self._snapshot()
        store = self._open_readonly()
        try:
            ST.sweep(BS, store, now=NOW, stale_after_seconds=14400)
        finally:
            store.close()
        after = self._snapshot()
        self.assertEqual(before, after,
                         "sweep() changed the store: %s"
                         % (set(before) ^ set(after)
                            or [k for k in before if before[k] != after[k]]))

    def test_cli_sweep_subcommand_changes_zero_bytes(self):
        # No --now here, deliberately: the claim below is written at the
        # REAL wall-clock time, so the sweep must judge it against that
        # same clock (its own default) to see a fresh heartbeat. Passing
        # the fixture's fixed NOW constant here would compare a real
        # timestamp against an unrelated fictional one and could report a
        # false finding depending only on what real time this suite
        # happens to run at -- a test bug this file had until it was
        # caught here, not a property of bm_stall.py itself.
        self._claim("purity-probe-cli-fence", "purity-probe-cli-session")
        before = self._snapshot()
        result = self._run("bm_stall.py", ["sweep"])
        after = self._snapshot()
        self.assertEqual(
            0, result.returncode,
            "bm_stall.py sweep (CLI) reported findings for a fence claimed "
            "moments ago: %s%s" % (result.stdout, result.stderr))
        self.assertEqual(before, after,
                         "bm_stall.py sweep (CLI) changed the store: %s"
                         % (set(before) ^ set(after)
                            or [k for k in before if before[k] != after[k]]))


# ---------------------------------------------------------------------------
# SD5: the printed clearing command actually clears the finding, and the
# sweep never ran it. The ONE place this suite executes a printed command.
# ---------------------------------------------------------------------------

class TestSD5ClearingCommand(_StoreFixture):
    def test_printed_release_command_clears_the_finding(self):
        uuid_, _version = self._claim("clearable-fence", "clearable-session")
        self._backdate(uuid_, STALE_AGO)

        store = self._open_readonly()
        try:
            before_findings = ST.sweep(BS, store, now=NOW, stale_after_seconds=14400)
        finally:
            store.close()
        hit = [f for f in before_findings if f["lifecycle_uuid"] == uuid_]
        self.assertTrue(hit, "the clearable fence was not found stale")
        # adopt, not park: park refuses cross-session unconditionally
        # (tools/bm_store.py's own 'not-owner' guard), so the command this
        # sweep actually recommends to a THIRD PARTY recovering a dead
        # fence is adopt --adopt-from-live-session (see
        # _release_and_adopt_actions's own docstring for why).
        adopt_action = next(a for a in hit[0]["actions"]
                            if a["command"].split()[2] == "adopt")
        command = adopt_action["command"].replace(
            "<YOUR_SESSION_ID>", "clearing-session")
        argv = shlex.split(command)
        # argv[0]="python3" argv[1]="tools/bm_store.py" argv[2:]=the rest
        result = self._run(os.path.basename(argv[1]), argv[2:])
        self.assertEqual(0, result.returncode,
                         "the printed clearing command itself failed: %s%s"
                         % (result.stdout, result.stderr))

        store = self._open_readonly()
        try:
            after_findings = ST.sweep(BS, store, now=NOW, stale_after_seconds=14400)
        finally:
            store.close()
        still_there = [f for f in after_findings if f["lifecycle_uuid"] == uuid_
                      and f["kind"] in (ST.STALE_FENCE, ST.DEAD_WATCHDOG)]
        self.assertEqual(
            [], still_there,
            "the finding did not clear after running the exact command the "
            "sweep printed for it")


# ---------------------------------------------------------------------------
# Foreign commit base: a standalone pure function, git facts handed in.
# ---------------------------------------------------------------------------

class TestForeignCommitBase(unittest.TestCase):
    def test_matching_shas_produce_no_finding(self):
        record = {"lifecycle_uuid": "abc123", "name": "x", "version": 1, "session_id": "sess-1"}
        self.assertIsNone(
            ST.foreign_commit_base_finding(record, "deadbeef", "deadbeef"))

    def test_mismatched_shas_produce_a_finding(self):
        record = {"lifecycle_uuid": "abc123", "name": "x", "version": 1, "session_id": "sess-1"}
        finding = ST.foreign_commit_base_finding(record, "deadbeef" * 5,
                                                  "cafebabe" * 5)
        self.assertIsNotNone(finding)
        self.assertEqual(ST.FOREIGN_COMMIT_BASE, finding["kind"])

    def test_unknown_shas_produce_no_finding(self):
        record = {"lifecycle_uuid": "abc123", "name": "x", "version": 1, "session_id": "sess-1"}
        self.assertIsNone(ST.foreign_commit_base_finding(record, "", "cafebabe"))
        self.assertIsNone(ST.foreign_commit_base_finding(record, "deadbeef", ""))


if __name__ == "__main__":
    unittest.main()

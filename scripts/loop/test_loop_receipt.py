#!/usr/bin/env python3
"""Unit tests for scripts/loop/loop_receipt.py, the receipt writer AGENTS.md's receipt contract
names and audit issue F23 asked for: "a run that changes anything owes a receipt ... every run
that reaches the end writes receipt/receipt.json inside its own run directory".

Every case here drives the module's real functions against a real temp git repository or a real
temp filesystem: no field is asserted from reading the source, because a check that reads source
text is evidence about a file, never about a run (the sibling lifecycle suite's own rule, restated
here for the same reason).

Run: python3 scripts/loop/test_loop_receipt.py
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import loop_receipt as lr


def _git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, "git %s failed in %s: %s" % (" ".join(args), cwd, r.stderr)
    return r.stdout


def make_repo():
    d = tempfile.mkdtemp(prefix="loop-receipt-repo-")
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.com")
    _git(d, "config", "user.name", "T")
    return d


def commit(d, name, when=None):
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(name + "\n")
    _git(d, "add", name)
    env_args = []
    if when:
        env_args = ["--date", when]
    r = subprocess.run(["git", "commit", "-q", "-m", name] + env_args, cwd=d,
                        env=dict(os.environ, GIT_AUTHOR_DATE=when or "", GIT_COMMITTER_DATE=when or ""),
                        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _git(d, "rev-parse", "HEAD").strip()


class NumTests(unittest.TestCase):
    def test_empty_and_non_numeric_are_none(self):
        self.assertIsNone(lr._num(""))
        self.assertIsNone(lr._num(None))
        self.assertIsNone(lr._num("banana"))

    def test_a_real_number_parses(self):
        self.assertEqual(lr._num("3.50"), 3.5)


class OpenrouterSpendTests(unittest.TestCase):
    def test_both_readable_subtracts(self):
        v, note = lr.openrouter_spend("1.00", "4.60")
        self.assertAlmostEqual(v, 3.60)
        self.assertIsNone(note)

    def test_missing_before_is_no_data_with_a_reason(self):
        v, note = lr.openrouter_spend("", "4.60")
        self.assertIsNone(v)
        self.assertTrue(note.startswith("NO-DATA:"))

    def test_missing_after_is_no_data_with_a_reason(self):
        v, note = lr.openrouter_spend("1.00", "")
        self.assertIsNone(v)
        self.assertTrue(note.startswith("NO-DATA:"))


class ClaudeWindowTests(unittest.TestCase):
    """Outside a proof phase the receipt tallies the live Claude ledger over [start, end] at write time, after the
    final pass (U9, B5-19), and never uses the driver's --claude-spent."""

    def receipt(self, rows, spent="9.99"):
        from unittest import mock
        d = tempfile.mkdtemp(prefix="loop-receipt-claude-")
        ledger = os.path.join(d, "calls.jsonl")
        if rows is not None:
            with open(ledger, "w") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in rows))
        else:
            os.mkdir(ledger)   # a directory: the ledger cannot be read
        a = BuildReceiptTests._args(BuildReceiptTests(), run_dir=d, claude_spent=spent,
                                    start="2026-01-01T00:00:00+00:00", end="2026-01-01T01:00:00+00:00")
        with mock.patch.dict(os.environ, BROTHER_CLAUDE_CALLS_LEDGER=ledger):
            os.environ.pop("BROTHER_PROOF_PHASE", None)
            return lr.build_receipt(a)["claude_spend_usd"]

    def test_the_window_tally_is_the_figure_and_the_argument_is_ignored(self):
        rows = [{"call": "a", "at": "2026-01-01T00:10:00+00:00"},
                {"call": "a", "event": "done", "cost_usd": 0.5, "at": "2026-01-01T00:11:00+00:00"},
                # call b is whole (a terminal with no START is NO-DATA, finding 5), and all of it after the window
                {"call": "b", "at": "2026-01-01T01:59:00+00:00"},
                {"call": "b", "event": "done", "cost_usd": 7.0, "at": "2026-01-01T02:00:00+00:00"}]
        self.assertEqual(self.receipt(rows), 0.5)

    def test_a_call_pending_in_the_window_is_no_data(self):
        rows = [{"call": "a", "at": "2026-01-01T00:10:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00"}]
        self.assertTrue(str(self.receipt(rows)).startswith("NO-DATA"))

    def test_an_unreadable_ledger_is_no_data(self):
        self.assertTrue(str(self.receipt(None)).startswith("NO-DATA"))


class LogSha256Tests(unittest.TestCase):
    def test_digest_matches_a_real_file(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-log-")
        p = os.path.join(d, "x.log")
        with open(p, "wb") as fh:
            fh.write(b"hello world\n")
        digest, note = lr.log_sha256(p)
        self.assertIsNone(note)
        self.assertEqual(digest, hashlib.sha256(b"hello world\n").hexdigest())

    def test_a_missing_file_is_no_data(self):
        digest, note = lr.log_sha256("/no/such/file/anywhere")
        self.assertIsNone(digest)
        self.assertTrue(note.startswith("NO-DATA:"))

    def test_no_log_path_at_all_is_no_data_not_a_crash(self):
        digest, note = lr.log_sha256(None)
        self.assertIsNone(digest)
        self.assertTrue(note.startswith("NO-DATA:"))


class RuntimeRevisionTests(unittest.TestCase):
    def test_head_of_a_real_repo(self):
        d = make_repo()
        sha = commit(d, "a.txt")
        rev, note = lr.runtime_revision(d)
        self.assertIsNone(note)
        self.assertEqual(rev, sha)

    def test_a_non_repo_directory_is_no_data(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-norepo-")
        rev, note = lr.runtime_revision(d)
        self.assertIsNone(rev)
        self.assertTrue(note.startswith("NO-DATA:"))


class BuildReceiptTests(unittest.TestCase):
    class _Args(object):
        pass

    def _args(self, **kw):
        a = self._Args()
        defaults = dict(run_dir="/tmp/run-x", pid="123", start="2026-01-01T00:00:00",
                         end="2026-01-01T01:00:00", state="FINISHED", reason="done",
                         deadline="18:00", budget="10.00", spent_before="1.00",
                         spent_after="2.00", claude_spent="0.50", claude_note="",
                         log_path="/no/such/log", cwd="/no/such/repo")
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(a, k, v)
        return a

    def test_every_required_key_is_present(self):
        receipt = lr.build_receipt(self._args())
        required = {"run_id", "driver_pid", "start", "end", "end_state", "end_reason", "deadline",
                    "budget_usd", "openrouter_spend_usd", "claude_spend_usd", "landings",
                    "driver_log_path", "driver_log_sha256", "runtime_revision"}
        self.assertTrue(required.issubset(receipt.keys()), receipt.keys())

    def test_run_id_is_the_run_dirs_basename(self):
        receipt = lr.build_receipt(self._args(run_dir="/tmp/loop-runs/run-20260926-120000-999"))
        self.assertEqual(receipt["run_id"], "run-20260926-120000-999")

    def test_unreadable_fields_are_no_data_strings_never_missing(self):
        # log_path and cwd both point nowhere real, so their fields must be NO-DATA strings,
        # present in the dict, not absent from it.
        receipt = lr.build_receipt(self._args())
        for key in ("driver_log_sha256", "runtime_revision", "landings"):
            self.assertIn(key, receipt)
            self.assertIsInstance(receipt[key], str)
            self.assertTrue(receipt[key].startswith("NO-DATA:"), (key, receipt[key]))

    def test_readable_fields_are_numbers(self):
        from unittest import mock
        missing = os.path.join(tempfile.mkdtemp(prefix="loop-receipt-noledger-"), "calls.jsonl")
        with mock.patch.dict(os.environ, BROTHER_CLAUDE_CALLS_LEDGER=missing):
            os.environ.pop("BROTHER_PROOF_PHASE", None)
            receipt = lr.build_receipt(self._args())
        self.assertEqual(receipt["budget_usd"], 10.0)
        self.assertAlmostEqual(receipt["openrouter_spend_usd"], 1.0)
        self.assertEqual(receipt["claude_spend_usd"], 0.0)   # a missing ledger is a measured empty one, not 0.50


class WriteReceiptTests(unittest.TestCase):
    def test_writes_the_file_and_returns_its_path(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-write-")
        path, err = lr.write_receipt({"a": 1}, d)
        self.assertIsNone(err)
        self.assertEqual(path, os.path.join(d, "receipt", "receipt.json"))
        self.assertEqual(json.load(open(path, encoding="utf-8")), {"a": 1})

    def test_no_leftover_temp_file_after_a_successful_write(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-write2-")
        lr.write_receipt({"a": 1}, d)
        names = os.listdir(os.path.join(d, "receipt"))
        self.assertEqual(names, ["receipt.json"])

    def test_a_second_write_replaces_the_first_atomically(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-write3-")
        lr.write_receipt({"a": 1}, d)
        path, err = lr.write_receipt({"a": 2}, d)
        self.assertIsNone(err)
        self.assertEqual(json.load(open(path, encoding="utf-8")), {"a": 2})
        names = os.listdir(os.path.join(d, "receipt"))
        self.assertEqual(names, ["receipt.json"])

    def test_an_unwritable_run_directory_is_reported_never_raised(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-write4-")
        os.chmod(d, stat.S_IREAD | stat.S_IEXEC)
        try:
            path, err = lr.write_receipt({"a": 1}, d)
        finally:
            os.chmod(d, stat.S_IRWXU)
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertFalse(os.path.isdir(os.path.join(d, "receipt")))

    def test_an_unwritable_run_directory_leaves_no_partial_receipt(self):
        d = tempfile.mkdtemp(prefix="loop-receipt-write5-")
        os.makedirs(os.path.join(d, "receipt"))
        os.chmod(os.path.join(d, "receipt"), stat.S_IREAD | stat.S_IEXEC)
        try:
            path, err = lr.write_receipt({"a": 1}, d)
        finally:
            os.chmod(os.path.join(d, "receipt"), stat.S_IRWXU)
        self.assertIsNone(path)
        self.assertIsNotNone(err)
        self.assertFalse(os.path.isfile(os.path.join(d, "receipt", "receipt.json")))


class MainCliTests(unittest.TestCase):
    def _run(self, args, cwd=None):
        return subprocess.run([sys.executable, os.path.join(HERE, "loop_receipt.py")] + args,
                               cwd=cwd, capture_output=True, text=True)

    def test_write_end_to_end_writes_a_readable_receipt(self):
        run_dir = tempfile.mkdtemp(prefix="loop-receipt-cli-")
        log_path = os.path.join(run_dir, "d.log")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("RUN START 2026-01-01T00:00:00\nRUN END 2026-01-01T01:00:00\n")
        r = self._run(["write", "--run-dir", run_dir, "--pid", "42",
                        "--start", "2026-01-01T00:00:00", "--end", "2026-01-01T01:00:00",
                        "--state", "FINISHED", "--reason", "done",
                        "--deadline", "18:00", "--budget", "10.00",
                        "--spent-before", "1.00", "--spent-after", "2.50",
                        "--claude-spent", "0.10", "--claude-note", "",
                        "--log-path", log_path, "--cwd", run_dir])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = r.stdout.strip()
        self.assertTrue(os.path.isfile(path))
        receipt = json.load(open(path, encoding="utf-8"))
        self.assertEqual(receipt["end_state"], "FINISHED")
        self.assertEqual(receipt["driver_pid"], "42")
        self.assertAlmostEqual(receipt["openrouter_spend_usd"], 1.5)
        self.assertEqual(receipt["driver_log_sha256"],
                          hashlib.sha256(open(log_path, "rb").read()).hexdigest())

    def test_a_missing_required_flag_refuses_rather_than_writing_garbage(self):
        run_dir = tempfile.mkdtemp(prefix="loop-receipt-cli2-")
        r = self._run(["write", "--run-dir", run_dir, "--pid", "1"])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.isdir(os.path.join(run_dir, "receipt")))

    def test_an_unwritable_run_dir_fails_loud_on_the_cli_too(self):
        run_dir = tempfile.mkdtemp(prefix="loop-receipt-cli3-")
        os.chmod(run_dir, stat.S_IREAD | stat.S_IEXEC)
        try:
            r = self._run(["write", "--run-dir", run_dir, "--pid", "1",
                            "--start", "2026-01-01T00:00:00", "--end", "2026-01-01T01:00:00",
                            "--state", "FINISHED", "--reason", "done",
                            "--deadline", "18:00", "--budget", "10.00",
                            "--log-path", "/no/such/log", "--cwd", "/no/such/repo"])
        finally:
            os.chmod(run_dir, stat.S_IRWXU)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("RECEIPT FAILED:"), r.stdout)


class ProofRecordingTests(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.mock = mock
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = os.path.join(self.temp.name, "RB-run")
        os.makedirs(self.run)
        self.start = "2026-09-26T00:00:00+00:00"
        self.end = "2026-09-26T08:00:00+00:00"

    def test_missing_manifest_is_recorded_as_unknown(self):
        record = lr.proof_start(self.run, "RB", "^H", "", self.start)
        self.assertEqual(record["freeze"]["start"]["verdict"], "NO-DATA")
        self.assertTrue(os.path.isfile(os.path.join(self.run, "proof", "start.json")))

    def test_finish_collects_both_boundaries_and_does_not_invent_liability(self):
        with self.mock.patch.object(lr, "freeze_boundary", return_value={"verdict":"PASS","manifest_sha256":"a"*64}), self.mock.patch.object(lr, "sandbox_observation", return_value={"enforced":True}):
            lr.proof_start(self.run, "RB", "^H", "manifest", self.start)
            result = lr.proof_finish(self.run, self.end)
        self.assertEqual(result["run_id"], "RB-run")
        self.assertEqual(result["phase"], "RB")
        self.assertEqual(set(result["freeze"]), {"start", "end"})
        self.assertTrue(str(result["liability"]).startswith("NO-DATA"))
        self.assertTrue(result["unattended"])

    def test_pause_event_makes_run_attended(self):
        lr.proof_start(self.run, "RB", "^H", "", self.start)
        lr.proof_event(self.run, "pause", "owner pause", self.start)
        result = lr.proof_finish(self.run, self.end)
        self.assertFalse(result["unattended"])
        self.assertEqual(result["interventions"][0]["kind"], "pause")

    def test_missing_start_never_becomes_unattended(self):
        result = lr.proof_finish(self.run, self.end)
        self.assertTrue(str(result["unattended"]).startswith("NO-DATA"))

    def test_corrupt_events_never_become_unattended(self):
        lr.proof_start(self.run, "RB", "^H", "", self.start)
        with open(os.path.join(self.run,"proof","events.jsonl"),"w") as f:f.write("broken")
        result = lr.proof_finish(self.run, self.end)
        self.assertTrue(str(result["unattended"]).startswith("NO-DATA"))

    def test_liability_distinguishes_missing_empty_abandoned_and_open(self):
        path = os.path.join(self.temp.name, "ledger.jsonl")
        self.assertTrue(str(lr.proof_liability(path)).startswith("NO-DATA"))
        with open(path,"w") as f:f.write("")
        self.assertEqual(lr.proof_liability(path)["unknown_cost_calls"],0)
        rows=[{"type":"RESERVE","reservation_id":"a","estimated_cost":2}, {"type":"ABANDONED","reservation_id":"a"}, {"type":"RESERVE","reservation_id":"b","estimated_cost":3}]
        with open(path,"w") as f:f.write("\n".join(json.dumps(r) for r in rows))
        result=lr.proof_liability(path)
        self.assertEqual(result["abandoned_unsettled_calls"],1)
        self.assertEqual(result["unknown_cost_calls"],2)
        self.assertEqual(result["reserved_liability_usd"],5)

    def test_malformed_liability_never_looks_empty(self):
        path=os.path.join(self.temp.name,"ledger.jsonl")
        for text in ("broken",json.dumps({"type":"RELEASE","reservation_id":"x"}),json.dumps({"type":"RESERVE","reservation_id":"x","estimated_cost":float('nan')})):
            with open(path,"w") as f:f.write(text)
            self.assertTrue(str(lr.proof_liability(path)).startswith("NO-DATA"))

    def test_sandbox_requires_a_real_blocked_attempt_and_allowed_control(self):
        # The real helper uses only scratch writes and a loopback bind, never a provider call.
        result=lr.sandbox_observation(self.run)
        if sys.platform == 'darwin' and __import__('shutil').which('sandbox-exec'):
            self.assertTrue(result['enforced'], result)
            self.assertEqual(result['blocked_attempts'],2)
            self.assertEqual(result['escape_attempts'],2)
            self.assertTrue(os.path.isfile(result['evidence_path']))
        else:
            self.assertTrue(str(result).startswith('NO-DATA'))

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _sandbox(run_dir, attempt_id):
    """The sandbox observation proof-start records, written where acceptance reads it (the real one needs sandbox-exec)."""
    p = os.path.join(run_dir, "proof", "sandbox.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(dict(schema="loop-sandbox-observation-v1", run_id=os.path.basename(run_dir), attempt_id=attempt_id,
                       exit_code=0, observation=dict(control=True, escape_attempts=2, blocked_attempts=2)), fh)
    return dict(enforced=True, escape_attempts=2, blocked_attempts=2, evidence_path=p,
                evidence_sha256=_sha(open(p, "rb").read()))


class ProofRun(unittest.TestCase):
    """One RB proof run driven through loop_receipt's own verbs (proof-start, proof-work-start, proof-ending,
    proof-settle, proof-end, write) on a scratch OpenRouter state root, a scratch pair Claude ledger and the real launch
    registry beside the run. Claude calls go through claude_ledger.start and finish, so admission (proof_ledger) reads
    what proof-start recorded. Only the freeze verify and the sandbox observation are stubbed: neither is tested here."""

    def setUp(self):
        from pathlib import Path
        from unittest import mock
        import proof_ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        state = self.root / "state"
        state.mkdir()
        self.ledger = state / "openrouter-ledger.jsonl"
        self.ledger.write_text("")
        base = self.root / "baseline.json"
        digest = proof_ledger.capture(self.ledger, base)
        self.claude = self.root / "pair" / "claude-calls.jsonl"
        self.claude.parent.mkdir()
        self.run = self.root / "runs" / "run-RB-x"
        self.run.mkdir(parents=True)
        self.deadline = time.time() + 9 * 3600
        self.env = dict(BROTHER_PROOF_PHASE="RB", BROTHER_OR_STATE_ROOT=str(state), BROTHER_PROOF_BASELINE=str(base),
                        BROTHER_PROOF_BASELINE_SHA256=digest, BROTHER_CLAUDE_CALLS_LEDGER=str(self.claude),
                        BROTHER_RUN_DIR=str(self.run), BROTHER_PROOF_RUN_DIR=str(self.run), BROTHER_STOP_HOUR="6",
                        BROTHER_CODE_ROOT=HERE)
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("BROTHER_PROOF_MIN_WINDOW_S", None)   # restored by the patch above
        for p in (mock.patch.object(lr, "freeze_boundary", side_effect=self.frozen),
                  mock.patch.object(lr, "sandbox_observation", side_effect=_sandbox)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def frozen(manifest, run_id, phase):
        return dict(schema="loop-freeze-check-v1", run_id=run_id, phase=phase, verdict="PASS", observed_at=lr.utc_now(),
                    checked_files=1, manifest_sha256="a" * 64, findings=[])

    def cli(self, *argv):
        import contextlib, io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lr.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def begin(self, work=True):
        code, text = self.cli("proof-start", "--run-dir", str(self.run), "--deadline-epoch", str(self.deadline))
        self.assertEqual(code, 0, text)
        if work:
            self.work = lr.proof_work_start(str(self.run), str(self.deadline))
        self.start = lr.proof_read(self.run / "proof" / "start.json")

    def call(self, cid, cost, done=True, timeout=60):
        import claude_ledger
        started = claude_ledger.start(str(self.claude), {"call": cid}, timeout)
        if done:
            row = {"call": cid, "cost_usd": cost}
            row.update((k, started[k]) for k in ("run", "attempt") if k in started)
            claude_ledger.finish(str(self.claude), row)
        return started

    def events(self):
        return [json.loads(l) for l in (self.run / "proof" / "events.jsonl").read_text().splitlines() if l.strip()]

    def write(self, *extra, **kw):
        end = kw.get("end") or lr.utc_now()
        code, text = self.cli("write", "--run-dir", str(self.run), "--pid", "1", "--start", self.work, "--end", end,
                              "--state", "DEADLINE", "--reason", "fixture", "--deadline", "23:59", "--budget", "10",
                              "--log-path", "/no/such/log", "--cwd", kw.get("cwd", str(self.root)), *extra)
        self.assertEqual(code, 0, text)
        return json.loads((self.run / "receipt" / "receipt.json").read_text())

    def land(self, commit, verdict="LANDED", remote_sha=None, log=b""):
        import land_batch
        observed = verdict != "UNVERIFIED"
        line = land_batch.record_landing(str(self.run), commit, ["U1.a"], ["/b.json"], verdict, "hub/main",
                                         (remote_sha or commit) if observed else None,
                                         lr.utc_now() if observed else None, log if observed else None)
        self.assertIn("RECORD  commit", line)


def _repo_with(*messages):
    """A scratch repository with one commit per message, returned as (dir, [sha, ...])."""
    d = make_repo()
    shas = []
    for i, m in enumerate(messages):
        with open(os.path.join(d, "f%d" % i), "w") as fh:
            fh.write(m)
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", m)
        shas.append(_git(d, "rev-parse", "HEAD").strip())
    return d, shas


class LaunchRecordTests(ProofRun):
    """Codex check-in 2 #1 and U1: the launch writer records the deadline admission reads, and the volatile names."""

    def test_launch_record_carries_run_dir_and_deadline_epoch(self):
        import proof_ledger
        self.begin(work=False)
        launched = json.loads((proof_ledger.launch_dir(str(self.run)) / "launch.json").read_text())
        self.assertEqual(launched["run_dir"], str(self.run))
        self.assertEqual(launched["deadline_epoch"], self.deadline)

    def test_a_claude_call_is_admitted_against_the_recorded_deadline(self):
        # the admission reader (proof_ledger.admit_locked) at its real entry point, claude_ledger.start
        self.begin()
        started = self.call("c1", 0.5, done=False)
        self.assertEqual((started["run"], started["attempt"]), (self.run.name, self.start["attempt_id"]))

    def test_volatile_names_are_recorded_by_digest_in_start_and_launch(self):
        import proof_ledger
        self.begin(work=False)
        expected = {k: _sha(self.env[k].encode()) for k in
                    ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_RUN_DIR", "BROTHER_RUN_DIR", "BROTHER_STOP_HOUR")}
        launched = json.loads((proof_ledger.launch_dir(str(self.run)) / "launch.json").read_text())
        self.assertEqual(self.start["volatile_env"], expected)
        self.assertEqual(launched["volatile_env"], expected)

    def test_work_start_refuses_a_deadline_other_than_the_launched_one(self):
        self.begin(work=False)
        with self.assertRaises(ValueError):
            lr.proof_work_start(str(self.run), str(self.deadline + 3600))
        self.assertNotIn("work_start", lr.proof_read(self.run / "proof" / "start.json"))


class ClaudeEndTests(ProofRun):
    """U9 (B5-19): the receipt's Claude spend is tallied from the retained end bytes, by run tag, never handed in."""

    def test_b5_19_receipt_claude_spend_is_the_retained_end_tally(self):
        self.begin()
        self.call("c1", 1.0)
        self.call("c2", 2.0)
        receipt = self.write("--claude-spent", "0", "--claude-note", "")   # the driver's stale figure is ignored
        self.assertEqual(receipt["claude_spend_usd"], 3.0)
        ev = lr.proof_read(self.run / "proof" / "evidence.json")
        self.assertEqual(ev["claude_snapshot"]["sha256"], _sha((self.run / "proof" / "claude-end.jsonl").read_bytes()))

    def test_a_call_pending_at_the_end_makes_the_claude_figure_no_data(self):
        self.begin()
        self.call("c1", 1.0)
        self.call("c2", None, done=False)
        receipt = self.write("--claude-spent", "1", "--claude-note", "")
        self.assertTrue(str(receipt["claude_spend_usd"]).startswith("NO-DATA"), receipt["claude_spend_usd"])

    def test_a_call_closed_after_the_end_snapshot_is_not_in_the_receipt(self):
        self.begin()
        self.call("c1", 1.0)
        end = lr.utc_now()
        lr.proof_finish(str(self.run), end)
        self.call("c2", 5.0)
        receipt = self.write("--claude-spent", "6", "--claude-note", "", end=end)
        self.assertEqual(receipt["claude_spend_usd"], 1.0)

    def finished(self):
        self.begin()
        self.call("c1", 1.0)
        end = lr.utc_now()
        lr.proof_finish(str(self.run), end)
        return end

    def test_a_symlinked_claude_snapshot_is_no_data(self):
        end = self.finished()
        p = self.run / "proof" / "claude-end.jsonl"
        outside = self.root / "outside.jsonl"
        outside.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(outside)
        claude = self.write(end=end)["claude_spend_usd"]
        self.assertTrue(str(claude).startswith("NO-DATA"), claude)

    def test_a_snapshot_record_naming_another_run_is_no_data(self):
        end = self.finished()
        p = self.run / "proof" / "evidence.json"
        ev = json.loads(p.read_text())
        ev["claude_snapshot"]["run_id"] = "run-RC-x"
        p.write_text(json.dumps(ev))
        claude = self.write(end=end)["claude_spend_usd"]
        self.assertTrue(str(claude).startswith("NO-DATA"), claude)

    def test_an_edited_claude_snapshot_makes_the_figure_no_data(self):
        self.begin()
        self.call("c1", 1.0)
        end = lr.utc_now()
        lr.proof_finish(str(self.run), end)
        (self.run / "proof" / "claude-end.jsonl").write_text("")
        receipt = self.write("--claude-spent", "1", "--claude-note", "", end=end)
        self.assertTrue(str(receipt["claude_spend_usd"]).startswith("NO-DATA"), receipt["claude_spend_usd"])


class LandingRecordTests(ProofRun):
    """Codex check-in 3 #3 and U10: landings come from proof/landings.jsonl and its retained history, never git log."""

    def test_codex_3_one_landing_among_a_closure_and_a_foreign_commit_counts_one(self):
        self.begin()
        repo, shas = _repo_with("landing", "closure", "foreign")
        self.land(shas[0])
        receipt = self.write(cwd=repo)
        self.assertEqual(receipt["landings"], 1)

    def test_a_landing_reverted_in_the_observed_range_is_not_counted(self):
        self.begin()
        repo, shas = _repo_with("landing one", "landing two")
        _git(repo, "revert", "--no-edit", shas[0])
        log = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..HEAD" % shas[0]], cwd=repo,
                             capture_output=True, check=True).stdout
        self.land(shas[0], "FAST-FORWARD", _git(repo, "rev-parse", "HEAD").strip(), log)
        # the history the lander retains for a FAST-FORWARD is its range up to the observed remote, which names that
        # remote; an empty one stops short of the last fetch and reads UNKNOWN (proof_accept.reaches_horizon, 2026-09-27)
        log_two = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..HEAD" % shas[1]], cwd=repo,
                                 capture_output=True, check=True).stdout
        self.land(shas[1], "FAST-FORWARD", _git(repo, "rev-parse", "HEAD").strip(), log_two)
        self.assertEqual(self.write(cwd=repo)["landings"], 1)

    def test_a_landing_with_no_observed_remote_is_no_data(self):
        self.begin()
        repo, shas = _repo_with("landing")
        self.land(shas[0], "UNVERIFIED")
        landings = self.write(cwd=repo)["landings"]
        self.assertTrue(str(landings).startswith("NO-DATA"), landings)

    def test_a_landing_record_changed_after_the_end_is_no_data(self):
        self.begin()
        repo, shas = _repo_with("landing")
        self.land(shas[0])
        end = lr.utc_now()
        lr.proof_finish(str(self.run), end)
        with open(self.run / "proof" / "landings.jsonl", "a") as fh:
            fh.write(open(self.run / "proof" / "landings.jsonl").readline())
        landings = self.write(cwd=repo, end=end)["landings"]
        self.assertTrue(str(landings).startswith("NO-DATA"), landings)

    def test_a_landing_record_that_appears_after_an_absent_end_is_no_data(self):
        self.begin()
        repo, shas = _repo_with("landing")
        end = lr.utc_now()
        lr.proof_finish(str(self.run), end)
        self.land(shas[0])
        landings = self.write(cwd=repo, end=end)["landings"]
        self.assertTrue(str(landings).startswith("NO-DATA"), landings)

    def test_no_landing_record_is_zero_landings(self):
        self.begin()
        repo, _ = _repo_with("foreign")
        self.assertEqual(self.write(cwd=repo)["landings"], 0)


class HistoryMarkerTests(ProofRun):
    """U11 and objection 14: start and end markers bound the intervention history; an unappendable one is NO-DATA."""

    def finish(self):
        return lr.proof_finish(str(self.run), lr.utc_now())

    def test_start_and_end_markers_bound_the_history(self):
        self.begin()
        ev = self.finish()
        kinds = [r["kind"] for r in self.events()]
        self.assertEqual((kinds[0], kinds[-1], len(kinds)), ("start-marker", "end-marker", 2))
        self.assertIs(ev["unattended"], True)
        self.assertEqual(ev["history_sha256"], _sha((self.run / "proof" / "events.jsonl").read_bytes()))

    def test_codex_case_readable_unwritable_empty_history_is_no_data(self):
        self.begin()
        p = self.run / "proof" / "events.jsonl"
        p.write_text("")
        p.chmod(0o444)
        self.addCleanup(p.chmod, 0o644)
        self.assertIsNot(self.finish()["unattended"], True)

    def test_history_without_its_start_marker_is_no_data(self):
        self.begin()
        (self.run / "proof" / "events.jsonl").write_text("")
        ev = self.finish()
        self.assertTrue(str(ev["unattended"]).startswith("NO-DATA"), ev["unattended"])

    def test_history_that_cannot_take_its_end_marker_is_no_data(self):
        self.begin()
        p = self.run / "proof" / "events.jsonl"
        p.chmod(0o444)
        self.addCleanup(p.chmod, 0o644)
        ev = self.finish()
        self.assertTrue(str(ev["unattended"]).startswith("NO-DATA"), ev["unattended"])

    def test_a_failed_event_append_elsewhere_is_no_data(self):
        self.begin()
        (self.run / "proof-events-failed").write_text("")
        ev = self.finish()
        self.assertTrue(str(ev["unattended"]).startswith("NO-DATA"), ev["unattended"])

    def test_an_intervention_between_the_markers_is_recorded(self):
        self.begin()
        lr.proof_event(str(self.run), "pause", "owner pause")
        ev = self.finish()
        self.assertIs(ev["unattended"], False)
        self.assertEqual([r["kind"] for r in ev["interventions"]], ["pause"])


class EndingSettleTests(ProofRun):
    """S6 and S7: the ending marker refuses new calls; settle waits for this run's open calls and names leftovers."""

    def reserve(self, rid, run=None, attempt=None):
        with open(self.ledger, "a") as fh:
            for row in (dict(type="RESERVE", reservation_id=rid, at=time.time(), estimated_cost=0.1,
                             run_id=run or self.run.name, attempt_id=attempt or self.start["attempt_id"]),
                        dict(type="DISPATCH_START", reservation_id=rid, at=time.time(),
                             run_id=run or self.run.name, attempt_id=attempt or self.start["attempt_id"])):
                fh.write(json.dumps(row) + "\n")

    def test_proof_ending_marks_the_run_and_refuses_a_new_call(self):
        import proof_ledger
        self.begin()
        code, text = self.cli("proof-ending", "--run-dir", str(self.run), "--state", "DEADLINE")
        self.assertEqual(code, 0, text)
        self.assertEqual(lr.proof_read(self.run / "proof" / "ending.json")["state"], "DEADLINE")
        with self.assertRaises(proof_ledger.DrainRefused):
            self.call("late", 0.1, done=False)

    def test_a_second_ending_keeps_the_first_record(self):
        self.begin()
        self.cli("proof-ending", "--run-dir", str(self.run), "--state", "DEADLINE")
        code, text = self.cli("proof-ending", "--run-dir", str(self.run), "--state", "BUDGET")
        self.assertEqual(code, 0, text)
        self.assertEqual(lr.proof_read(self.run / "proof" / "ending.json")["state"], "DEADLINE")

    def test_settle_with_nothing_open_prints_settled(self):
        self.begin()
        self.call("c1", 1.0)
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "1")
        self.assertEqual((code, text.strip()), (0, "SETTLED"))

    def test_settle_names_an_open_reservation_and_exits_2(self):
        self.begin()
        self.reserve("open-1")
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "1")
        self.assertEqual(code, 2, text)
        self.assertIn("open-1", text)

    def test_settle_names_a_pending_claude_call(self):
        self.begin()
        self.call("c1", None, done=False)
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "1")
        self.assertEqual(code, 2, text)
        self.assertIn("Claude", text)

    def test_settle_waits_for_a_call_that_closes_inside_its_bound(self):
        import threading, claude_ledger
        self.begin()
        started = self.call("c1", None, done=False)
        closer = threading.Timer(1.0, claude_ledger.finish, (str(self.claude), {
            "call": "c1", "cost_usd": 0.1, "run": started["run"], "attempt": started["attempt"]}))
        closer.start()
        self.addCleanup(closer.cancel)
        t0 = time.monotonic()
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "20")
        self.assertEqual((code, text.strip()), (0, "SETTLED"))
        self.assertLess(time.monotonic() - t0, 20)

    def test_settle_with_an_unreadable_claude_ledger_is_no_data(self):
        self.begin()
        self.call("c1", 1.0)
        self.claude.unlink()
        self.claude.mkdir()
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "1")
        self.assertEqual(code, 2, text)

    def test_settle_refuses_a_negative_bound(self):
        self.begin()
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "-1")
        self.assertEqual(code, 2, text)

    def test_settle_ignores_another_runs_open_reservation(self):
        self.begin()
        self.reserve("rc-open", run="run-RC-x", attempt="b" * 32)
        code, text = self.cli("proof-settle", "--run-dir", str(self.run), "--max-seconds", "1")
        self.assertEqual((code, text.strip()), (0, "SETTLED"))


class CodeRootBoundaryTests(unittest.TestCase):
    """U3 item 6: in a proof phase the freeze boundary FAILs when BROTHER_CODE_ROOT is outside the frozen runtime."""

    def boundary(self, **env):
        from unittest import mock
        import freeze_manifest
        d = tempfile.mkdtemp(prefix="loop-receipt-coderoot-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        manifest = os.path.join(d, "manifest.json")
        with open(manifest, "w") as fh:
            json.dump({"runtime": os.path.join(d, "bin"), "files": {"x": {}}}, fh)
        env = {k: v.replace("{d}", d) for k, v in env.items()}
        with mock.patch.dict(os.environ, env), mock.patch.object(freeze_manifest, "verify", return_value=(0, [])):
            for k in ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE"):
                if k not in env:
                    os.environ.pop(k, None)
            return lr.freeze_boundary(manifest, "run", "start")

    def test_a_code_root_outside_the_runtime_fails(self):
        row = self.boundary(BROTHER_PROOF_PHASE="RB", BROTHER_CODE_ROOT="{d}/elsewhere")
        self.assertEqual(row["verdict"], "FAIL")
        self.assertTrue(any("code root outside frozen runtime" in f for f in row["findings"]), row["findings"])

    def test_a_code_root_inside_the_runtime_passes(self):
        self.assertEqual(self.boundary(BROTHER_PROOF_PHASE="RB", BROTHER_CODE_ROOT="{d}/bin/candidate")["verdict"], "PASS")

    def test_a_proof_phase_without_a_code_root_fails(self):
        self.assertEqual(self.boundary(BROTHER_PROOF_PHASE="RB")["verdict"], "FAIL")

    def test_outside_a_proof_phase_the_code_root_is_not_judged(self):
        self.assertEqual(self.boundary(BROTHER_CODE_ROOT="{d}/elsewhere")["verdict"], "PASS")


class WindowKnobTests(unittest.TestCase):
    """BROTHER_PROOF_MIN_WINDOW_S shortens the admission window for a rehearsal; acceptance ignores it (and FAILs a
    manifest that carries it, in test_proof_accept)."""

    def check(self, knob, remaining):
        from unittest import mock
        from datetime import datetime, timezone
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        with mock.patch.dict(os.environ, {} if knob is None else {"BROTHER_PROOF_MIN_WINDOW_S": knob}):
            if knob is None:
                os.environ.pop("BROTHER_PROOF_MIN_WINDOW_S", None)
            lr.proof_deadline("RB", now.timestamp() + remaining, now.isoformat())

    def test_the_rehearsal_knob_shortens_the_window(self):
        self.check("120", 300)

    def test_without_the_knob_the_window_is_eight_hours(self):
        with self.assertRaises(ValueError):
            self.check(None, 300)

    def test_a_malformed_knob_refuses(self):
        for bad in ("0", "abc", "-5", "1.5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.check(bad, 9 * 3600)


if __name__ == "__main__":
    unittest.main()

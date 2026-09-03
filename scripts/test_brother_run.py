"""brother_run.py, driven as the real command line: an outcome in, a
delivery report out, through the real door, the real loop_bridge and the
real (stubbed) model worker. No network, no real claude: the decomposer and
the model each come from a tiny stub script pointed at by DOOR_MODEL_CMD and
MODEL_WORKER_CMD, the same seam test_door.py and test_model_worker.py each
already use on their own.
"""
import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import shutil
import tempfile
import textwrap
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
import brother_run as _br  # noqa: E402
import claim_store  # noqa: E402
import decide  # noqa: E402
import door  # noqa: E402
import receipt_door as RD  # noqa: E402
import work_record as WR  # noqa: E402


class DeliveryReportProvesItself(unittest.TestCase):
    """The harsh EVAD 2026-08-31 finding: the report named units and revisions
    but never the files that changed nor what verified each unit. A skeptic
    could not tell perfect work from empty work. The report now carries both,
    from git and the Work document, never a worker's self-report."""

    def _rec(self):
        return {"outcome": "add retry", "work_id": "w1", "rows": [
            {"id": "U1", "done_check": "python3 -m pytest tests/test_x.py"},
            {"id": "U2", "done_check": "python3 a.py"}]}

    def test_report_names_changed_files_and_the_verifying_command(self):
        rec = self._rec()
        # The verifier's stamp, as _mark_integrated writes it: build_report
        # integrates on the row's own DONE, never on the claim state alone.
        rec["rows"][0]["status"] = "DONE"
        report, integ, ref = _br.build_report(
            rec, {"U1": {"state": "done"},
                              "U2": {"state": "failed"}},
            "abc123", "def456", changed=["src/api.py", "tests/test_x.py"])
        self.assertIn("files changed (2): src/api.py, tests/test_x.py", report)
        self.assertIn("verified by: python3 -m pytest tests/test_x.py", report)
        self.assertEqual(integ, ["U1"])

    def test_a_long_check_prints_whole_in_the_summary_never_cut(self):
        """The zero-context critic reading the README's quoted run, 2026-09-03:
        the summary line used to cut each check at 140 characters
        (`check[:140]`), and that cut the guard's discriminating
        `| grep -q '^TypeError: .'` clause off the real run's line, leaving
        the full command visible only further down under "what this run
        proved". The summary line is the first place a reader sees the
        check, so it must print the whole thing."""
        rec = self._rec()
        long_check = ("python3 -m pytest tests/test_guard.py -k typeerror "
                      "&& python3 -c \"import subprocess; out = "
                      "subprocess.run(['brother_run', '--bad-input'], "
                      "capture_output=True, text=True).stderr\" "
                      "| grep -q '^TypeError: .'")
        self.assertGreater(len(long_check), 140)
        rec["rows"][0]["done_check"] = long_check
        rec["rows"][0]["status"] = "DONE"
        report, integ, ref = _br.build_report(
            rec, {"U1": {"state": "done"}, "U2": {"state": "failed"}},
            "abc123", "def456", changed=["src/api.py"])
        self.assertIn("verified by: " + long_check, report)

    def test_a_done_claim_without_the_verifiers_stamp_is_refused(self):
        """EVAD run 5 trial 2: a do-nothing unit with a vacuous check read
        delivered at exit 0 because the report trusted state == "done" while
        the verifier's refusal reached only the on-disk record. A done claim
        on a row the verifier never stamped DONE is refused, with the row's
        own recorded refusal reason when one exists."""
        rec = self._rec()
        rec["rows"][0]["integration_refused"] = (
            "declared artifact(s) not present in the repository")
        report, integ, refused = _br.build_report(
            rec, {"U1": {"state": "done"}, "U2": {"state": "done"}},
            "abc123", "def456", changed=[])
        self.assertEqual(integ, [])
        reasons = dict(refused)
        self.assertIn("declared artifact(s) not present",
                      reasons["U1"])
        self.assertIn("never marked this row integrated", reasons["U2"])
        self.assertIn("refused (2):", report)

    def test_no_changed_files_when_the_revision_did_not_move(self):
        report, _, _ = _br.build_report(
            self._rec(), {}, "abc123", "abc123", changed=[])
        self.assertIn("files changed (0): none", report)

    def test_an_unreadable_range_is_no_data_not_zero(self):
        report, _, _ = _br.build_report(
            self._rec(), {}, "abc123", "def456", changed=None)
        self.assertIn("files changed: NO-DATA", report)

    def test_the_report_prints_one_receipt_sentence_per_unit(self):
        """A critic's mutation drove this dark: truncating
        receipt_door.receipts_for to its first receipt left every other test
        here green, because they all assert from the integrated/refused
        lists, never from the receipts section itself. Count the sentences
        under the report's own "what this run proved" header directly: two
        units must print two lines there, not one."""
        rec = self._rec()
        rec["rows"][0]["status"] = "DONE"
        report, integ, _ = _br.build_report(
            rec, {"U1": {"state": "done", "evidence": {
                "check_command": "python3 -m pytest tests/test_x.py",
                "exit_code": 0}},
                  "U2": {"state": "failed"}},
            "abc123", "def456", changed=[])
        self.assertEqual(integ, ["U1"])
        lines = report.splitlines()
        start = lines.index(
            "  what this run proved, one line per piece of work:") + 1
        end = lines.index("", start)
        receipt_lines = lines[start:end]
        self.assertEqual(len(receipt_lines), 2, receipt_lines)


class DeliveryReportPrintsTheReadmesThreeVerdicts(unittest.TestCase):
    """README.md lines 41-45 make PASS, FAIL and NO-DATA the product's
    headline vocabulary ("unknown does not become green"), but the
    documented first command never printed those three words: the door
    prints its own verdict, and the engine's delivery report printed
    integrated/refused only. Each unit's verdict word here is looked up
    from the same receipt state receipt_door.receipts_for() already
    computes for this report, never a second judgement."""

    def _rec(self):
        return {"outcome": "add retry", "work_id": "w1", "rows": [
            {"id": "U1", "done_check": "python3 -m pytest tests/test_x.py",
             "check_passed_before": False,
             "files_changed_by_unit": ["tests/test_x.py"]},
            {"id": "U2", "done_check": "python3 a.py"}]}

    def test_a_check_that_reexecuted_and_exited_0_prints_pass(self):
        rec = self._rec()
        rec["rows"][0]["status"] = "DONE"
        report, integ, _ = _br.build_report(
            rec, {"U1": {"state": "done", "evidence": {
                "check_command": "python3 -m pytest tests/test_x.py",
                "exit_code": 0}},
                  "U2": {"state": "failed"}},
            "abc123", "def456", changed=[])
        self.assertEqual(integ, ["U1"])
        line = next(l for l in report.splitlines() if "U1 delivered" in l)
        self.assertIn("verdict: PASS", line)

    def test_a_refused_unit_prints_fail(self):
        report, integ, refused = _br.build_report(
            self._rec(), {}, "abc123", "def456", changed=[])
        self.assertEqual(integ, [])
        self.assertEqual({uid for uid, _ in refused}, {"U1", "U2"})
        line = next(l for l in report.splitlines()
                    if l.strip().startswith("U1 was refused"))
        self.assertIn("verdict: FAIL", line)

    def test_a_unit_with_no_reexecutable_check_prints_no_data_never_pass(self):
        """An integrated row whose claim carries no evidence (the recorded
        check never re-ran) is receipt state "no-data", not "verified": the
        report must call it NO-DATA, and must never call it PASS."""
        rec = self._rec()
        rec["rows"][0]["status"] = "DONE"
        report, integ, _ = _br.build_report(
            rec, {"U1": {"state": "done"}}, "abc123", "def456", changed=[])
        self.assertEqual(integ, ["U1"])
        line = next(l for l in report.splitlines() if "U1 is NO-DATA" in l)
        self.assertIn("verdict: NO-DATA", line)
        self.assertNotIn("verdict: PASS", line)

    def test_the_summary_line_counts_units_by_verdict(self):
        rec = {"outcome": "add retry", "work_id": "w3", "rows": [
            {"id": "U1", "done_check": "python3 -m pytest tests/test_x.py",
             "status": "DONE", "check_passed_before": False,
             "files_changed_by_unit": ["tests/test_x.py"]},
            {"id": "U2", "done_check": "python3 a.py"},
            {"id": "U3", "done_check": "python3 b.py", "status": "DONE"}]}
        claims = {
            "U1": {"state": "done", "evidence": {
                "check_command": "python3 -m pytest tests/test_x.py",
                "exit_code": 0}},
            "U3": {"state": "done"}}
        report, integ, _ = _br.build_report(
            rec, claims, "abc123", "def456", changed=[])
        self.assertEqual(sorted(integ), ["U1", "U3"])
        self.assertIn("verdicts: 1 PASS, 1 FAIL, 1 NO-DATA", report)


class TheExitCodeFollowsTheEstatesConvention(unittest.TestCase):
    """rule 3, the zero-context critic, 2026-09-03: `return 1 if refused
    else 0` let a run whose every receipt was NO-DATA exit 0, and a CI
    consumer reads that as success. Driven directly against _exit_code_for,
    the same receipts (RD.receipts_for) every other test in this file
    already builds from a Work document and claims."""

    def test_zero_only_when_something_verified_and_nothing_refused(self):
        rows = [{"id": "U1", "done_check": "true", "status": "DONE",
                "check_passed_before": False,
                "files_changed_by_unit": ["x.py"]}]
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "abc"}}}
        receipts = RD.receipts_for({"rows": rows}, claims, [])
        code, reason = _br._exit_code_for(receipts, [])
        self.assertEqual(code, 0, reason)
        self.assertIn("verified", reason)

    def test_one_when_any_unit_is_refused(self):
        rows = [{"id": "U1", "done_check": "false"}]
        refused = [("U1", "it was started and ended failed")]
        receipts = RD.receipts_for({"rows": rows}, {}, refused)
        code, reason = _br._exit_code_for(receipts, refused)
        self.assertEqual(code, 1, reason)
        self.assertIn("U1", reason)

    def test_two_when_nothing_refused_but_nothing_verified(self):
        """The exact hole rule 3 closes: every receipt NO-DATA, refused
        list empty, and the old code returned 0 for this."""
        rows = [{"id": "U1", "done_check": "true", "status": "DONE"}]
        claims = {"U1": {"state": "done"}}
        receipts = RD.receipts_for({"rows": rows}, claims, [])
        code, reason = _br._exit_code_for(receipts, [])
        self.assertEqual(code, 2, reason)
        self.assertIn("NO-DATA", reason)

    def test_two_when_there_are_no_units_at_all(self):
        code, reason = _br._exit_code_for([], [])
        self.assertEqual(code, 2, reason)
        self.assertIn("no units", reason)


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def _git_repo_with_file(tmp, name, body="content\n"):
    """A tiny real git repo with one committed file, for the evidence checks
    below to resolve real canonical revisions and real artifacts against."""
    repo = os.path.join(tmp, "canon-%d" % len(os.listdir(tmp)))
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(body)
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    rev = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    return repo, rev


def _write_doc(tmp, rows):
    path = os.path.join(tmp, "work.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"work_id": "W1", "outcome": "o", "rows": rows}, fh)
    return path


class DeliveryEvidenceOrRefusal(unittest.TestCase):
    """Row E1: the delivery record must carry the check itself, not a
    sentence about it, and a claim that cannot prove its own delivery must be
    REFUSED rather than stamped DONE. The harsh EVAD 2026-08-31 found
    _mark_integrated wrote its canned sentence onto every id in done_now
    regardless of what actually happened; these pin the fix both ways."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="e1-evidence-")

    def test_real_evidence_integrates_and_the_record_carries_it(self):
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "test -f one.txt",
                                     "owns": ["one.txt"]}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "test -f one.txt", "exit_code": 0,
            "output": "", "output_truncated": False, "canonical_rev": rev}}}
        changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertTrue(changed)
        self.assertEqual(refusals, {})
        with open(doc, encoding="utf-8") as fh:
            row = json.load(fh)["rows"][0]
        self.assertEqual(row["status"], "DONE")
        self.assertIn("test -f one.txt", row["evidence"])
        self.assertIn(rev, row["evidence"])

    def test_no_evidence_at_all_is_refused_not_stamped(self):
        """The direct-attack shape: a claims.json state flipped to "done" by
        hand, with nothing behind it."""
        repo, _rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "false", "owns": []}])
        changed, refusals = _br._mark_integrated(
            doc, {"U1"}, {"U1": {"state": "done"}}, repo)
        self.assertFalse(changed)
        self.assertIn("U1", refusals)
        self.assertIn("no evidence", refusals["U1"])
        with open(doc, encoding="utf-8") as fh:
            row = json.load(fh)["rows"][0]
        self.assertNotEqual(row.get("status"), "DONE")

    def test_the_skeptics_attack_a_forged_canonical_hash_is_refused(self):
        """The exact finding: driven directly, a unit whose done_check was
        literally `false` and whose canonical_rev was the unvalidated literal
        deadbeef must NOT produce an integrated record. Even a fully forged
        evidence dict (claiming exit code 0) is refused, because deadbeef
        does not resolve via git cat-file -t in the repo the record
        describes."""
        repo, _rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "false", "owns": []}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "false", "exit_code": 0, "output": "",
            "output_truncated": False, "canonical_rev": "deadbeef"}}}
        changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertFalse(changed)
        self.assertIn("does not resolve", refusals["U1"])
        with open(doc, encoding="utf-8") as fh:
            row = json.load(fh)["rows"][0]
        self.assertNotEqual(row.get("status"), "DONE")

    def test_a_nonzero_exit_code_is_refused(self):
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "false", "owns": []}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "false", "exit_code": 1, "output": "",
            "output_truncated": False, "canonical_rev": rev}}}
        _changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertIn("exited 1", refusals["U1"])

    def test_a_missing_declared_artifact_is_refused(self):
        """Row E1's contract: a unit whose declared artifacts are absent from
        the repo at the recorded revision refuses, even with a real exit code
        0 and a real, resolving canonical revision."""
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "test -f ghost.txt",
                                     "owns": ["ghost.txt"]}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "test -f ghost.txt", "exit_code": 0,
            "output": "", "output_truncated": False, "canonical_rev": rev}}}
        _changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertIn("ghost.txt", refusals["U1"])

    def test_e1_4_a_forged_zero_exit_next_to_a_resolving_rev_is_refused(self):
        """The proof skeptic's exact driven attack: check_command `false`,
        exit_code 0, and a CANONICAL REV THAT ACTUALLY RESOLVES (unlike the
        deadbeef test above, which was caught earlier by rev resolution
        alone and never reached re-execution). Shape checks all pass here;
        only running `false` for real and seeing it exit 1 catches the
        forgery."""
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "false", "owns": []}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "false", "exit_code": 0, "output": "PASS",
            "output_truncated": False, "canonical_rev": rev}}}
        changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertFalse(changed)
        self.assertIn("does not reproduce", refusals["U1"])
        self.assertIn("exited 1", refusals["U1"])
        with open(doc, encoding="utf-8") as fh:
            row = json.load(fh)["rows"][0]
        self.assertNotEqual(row.get("status"), "DONE")

    def test_e1_4_a_check_naming_an_absent_file_is_refused_on_rerun(self):
        """The skeptic's second driven attack: a claim certifying
        `python3 -m pytest test_max.py` where test_max.py was never
        committed. `owns` is empty (the attack's own shape: only OWNED
        artifacts were checked before this fix, never the files the check
        command itself touches), so the old verifier had nothing to catch
        this with; re-running the command for real does, because pytest
        cannot collect a file that is not there and exits nonzero."""
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "SCHEDULED",
                                     "done_check": "python3 -m pytest test_max.py",
                                     "owns": []}])
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "python3 -m pytest test_max.py", "exit_code": 0,
            "output": "PASS", "output_truncated": False,
            "canonical_rev": rev}}}
        changed, refusals = _br._mark_integrated(doc, {"U1"}, claims, repo)
        self.assertFalse(changed)
        self.assertIn("does not reproduce", refusals["U1"])
        with open(doc, encoding="utf-8") as fh:
            row = json.load(fh)["rows"][0]
        self.assertNotEqual(row.get("status"), "DONE")

    def test_an_already_done_row_is_left_alone(self):
        repo, rev = _git_repo_with_file(self.tmp, "one.txt")
        doc = _write_doc(self.tmp, [{"id": "U1", "status": "DONE",
                                     "done_check": "false", "owns": [],
                                     "evidence": "old"}])
        changed, refusals = _br._mark_integrated(doc, {"U1"}, {}, repo)
        self.assertFalse(changed)
        self.assertEqual(refusals, {})
        with open(doc, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["rows"][0]["evidence"], "old")


def write_stub(tmpdir, name, body):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)
    return path


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    return repo


# A "model" that reads the whole prompt off argv, finds which file(s) it was
# told to write, and writes them. Standing in for `claude -p`, at the exact
# seam MODEL_WORKER_CMD exists for.
WRITER_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""

# A "model" that always exits nonzero, standing in for an unavailable or
# refusing real model.
FAILING_MODEL = """
    import sys
    sys.stdin.read() if not sys.stdin.isatty() else None
    sys.exit(1)
"""


class TwoUnitsIntegrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "A2", "objective": "create file two",
                 "done_check": "test -f two.txt", "writes": ["two.txt"],
                 "deps": []},
            ]))
        """)
        self.model = write_stub(self.tmp, "writer_model.py", WRITER_MODEL)
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)

    def test_a_python_done_check_is_resolved_end_to_end(self):
        """The harsh EVAD killer, end to end: a decomposer that emits a
        `python` done_check on this python3-only machine must still integrate,
        because the door rewrites the interpreter before the plan is accepted.
        Before the fix this unit's check exited 127 forever."""
        if shutil.which("python") is not None:
            self.skipTest("this machine has python; the 127 trap needs its "
                          "absence")
        dec = write_stub(self.tmp, "decomposer_py.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "P1", "objective": "create marker",
                 "done_check": "python -c \\\"import os;assert os.path.exists('m.txt')\\\"",
                 "writes": ["m.txt"], "deps": []},
            ]))
        """)
        env = dict(self.env)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, dec)
        proc = sh([sys.executable, BROTHER_RUN, "a marker file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotIn("command not found", out, out)
        self.assertIn("integrated (1):", out, out)
        self.assertEqual(proc.returncode, 0, out)

    def test_outcome_reaches_integrated_canonical_through_one_command(self):
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)

        self.assertTrue(os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "two.txt")), out)

        log = sh(["git", "log", "--oneline"], cwd=self.repo).stdout
        # E45: the merge subject is the engine's own, not git's default, so
        # the history says a machine made these two commits.
        self.assertEqual(log.count("Brother integrated "), 2, log)

        self.assertIn("integrated (2):", out, out)
        self.assertIn("A1", out, out)
        self.assertIn("A2", out, out)
        self.assertIn("refused (0):", out, out)

    def test_the_merge_trailers_name_this_run_and_this_engine(self):
        """E45 in production, end to end: the engine exports the run id and
        its own revision before any round, so the merge commits in the TARGET
        repository name the run directory a reader can open and the exact
        engine sha, rather than the NO-DATA both fields read when nothing
        threads them through loop_bridge."""
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)

        runs = os.path.join(self.tmp, "docs", "plan", "runs")
        names = sorted(os.listdir(runs))
        self.assertEqual(len(names), 1, names)
        body = sh(["git", "log", "--format=%B", "-1"], cwd=self.repo).stdout
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"],
                                cwd=self.repo, input=body,
                                capture_output=True, text=True).stdout
        trailers = dict(
            (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
            for line in parsed.splitlines() if ":" in line)
        self.assertEqual(trailers.get("Brother-Run"), names[0], parsed)
        harness = trailers.get("Brother-Harness", "")
        self.assertNotIn(_br.NODATA, harness, parsed)
        self.assertRegex(harness, r"^[0-9a-f]{40}$", parsed)
        # The run directory the trailer names is one a reader can open.
        self.assertTrue(os.path.isdir(os.path.join(runs, names[0])))

    def test_a_do_nothing_unit_with_a_vacuous_check_is_refused_at_exit(self):
        """The gauntlet's own fixture, EVAD run 5 trial 2: a model that does
        no work beside a done_check that passes vacuously. The verifier's
        refusal (declared artifact absent) must reach the report AND the
        process exit code, never only the on-disk record."""
        dec = write_stub(self.tmp, "decomposer_vacuous.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "N1", "objective": "do nothing",
                 "done_check": "true", "writes": ["ghost.txt"],
                 "deps": []},
            ]))
        """)
        do_nothing = write_stub(self.tmp, "do_nothing_model.py", """
            import sys
            print("stub model did no work on purpose")
        """)
        env = dict(self.env)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, dec)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, do_nothing)
        proc = sh([sys.executable, BROTHER_RUN, "a ghost file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("refused", out, out)
        self.assertNotIn("integrated (1):", out, out)


class DecomposerAlwaysInvalid(unittest.TestCase):
    def test_a_refusing_decomposer_stops_the_run_before_the_loop(self):
        tmp = tempfile.mkdtemp(prefix="brother-run-refuse-")
        repo = make_repo(tmp)
        # missing done_check on every attempt: work_record refuses it every
        # time, so door gives up after its own retries and this never reaches
        # loop_bridge at all.
        decomposer = write_stub(tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "Z1", "objective": "no check at all",
                 "writes": ["z.txt"], "deps": []},
            ]))
        """)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)

        proc = sh([sys.executable, BROTHER_RUN, "an outcome nobody can schedule",
                  "--cwd", repo, "--runs-root", tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("REFUSED", out)
        self.assertIn("done_check", out)
        # door.py's own contract: it only creates the store directory on
        # SUCCESS (WR.create), so a refusal must leave no run directory at
        # all, not merely an empty Work document inside one.
        runs_dir = os.path.join(tmp, "docs", "plan", "runs")
        self.assertFalse(os.path.exists(runs_dir), runs_dir)


class WorkerModelFails(unittest.TestCase):
    def test_a_failing_model_reports_the_unit_failed_not_integrated(self):
        tmp = tempfile.mkdtemp(prefix="brother-run-fail-")
        repo = make_repo(tmp)
        decomposer = write_stub(tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "F1", "objective": "create a file",
                 "done_check": "test -f f1.txt", "writes": ["f1.txt"],
                 "deps": []},
            ]))
        """)
        model = write_stub(tmp, "failing_model.py", FAILING_MODEL)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)

        proc = sh([sys.executable, BROTHER_RUN, "a file that will never appear",
                  "--cwd", repo, "--runs-root", tmp], env=env)
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertFalse(os.path.exists(os.path.join(repo, "f1.txt")), out)
        self.assertIn("integrated (0):", out, out)
        self.assertIn("refused (1):", out, out)
        self.assertIn("F1", out, out)

        # A decided round removes its lane (integrate.cleanup_lane), so no
        # lane branch lingers after a failed worker.
        branches = sh(["git", "branch", "--list", "lane/*"], cwd=repo).stdout
        self.assertNotIn("lane/", branches, branches)
        worktrees = sh(["git", "worktree", "list"], cwd=repo).stdout
        self.assertEqual(len(worktrees.strip().splitlines()), 1, worktrees)


# T2: a done_check that tells canonical from a lane by comparing os.getcwd()
# against the canonical repo path baked in at test setup, and fails the
# FIRST TIME ONLY it is ever asked on canonical, passing every canonical ask
# after that (and every lane ask, always). This is a real
# NEEDS-REPAIR-ON-NEW-BASE unit: green immediately in its own lane, red the
# first time integrate.py revalidates it on canonical, green again once
# reclaimed and re-merged, which is exactly what brother_run.py's own
# MAX_UNIT_ATTEMPTS outer retry exists to carry across rounds (see
# brother_run.py's own top-of-file docstring, "BOUNDED REPAIR"). No repair-
# loop internals are touched or stubbed; this drives the real door, the real
# loop_bridge, the real integrate.py, across two real rounds of the real
# process.
CHECK_LOGGER = """
    import os, sys
    CANON = %r
    COUNTER = %r
    if os.path.realpath(os.getcwd()) != CANON:
        sys.exit(0)
    n = 0
    if os.path.exists(COUNTER):
        n = int(open(COUNTER).read() or "0")
    n += 1
    with open(COUNTER, "w") as fh:
        fh.write(str(n))
    # invocation 1 is brother_run's own CHECK DISCRIMINATION precheck
    # (_stamp_prechecks, run before any worker claims this unit): it must
    # pass quietly here so the FIRST REAL WORKER ATTEMPT is still the one
    # this fixture means to fail.
    if n == 2:
        print("CANARY-CHECK-FAILED-ONCE on canonical invocation " + str(n))
        sys.exit(1)
    print("check_logger: canonical invocation " + str(n) + ", pass")
"""

DECOMPOSER_WITH_GIVEN_CHECK = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "R1", "objective": "create r1",
         "done_check": %r,
         "writes": ["r1.txt"], "deps": []},
    ]))
"""


class RetryLeavesAPerAttemptTrace(unittest.TestCase):
    """T2's own done-check: a unit that fails once and is retried across an
    outer round must leave BOTH attempts on disk, in their own directories,
    so the failed attempt's real check output survives being reclaimed.

    Without this, the failure is provably lost: claims.json keeps only the
    LATEST claim per unit id (claim_store.acquire overwrites it on every
    reclaim) and the final run.log's per-unit dump (brother_run.py, "THE
    OUTPUT THE RECEIPTS POINT AT") only ever reads that same final claims
    dict, so attempt 1's own check output was nowhere on disk once attempt 2
    finished, before this fix."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-retry-")
        self.repo = make_repo(self.tmp)
        # realpath, not abspath: on this platform tempfile.mkdtemp() hands
        # back a path through a symlink (/var -> /private/var), and a
        # subprocess's own os.getcwd() resolves that symlink away. Comparing
        # against the unresolved abspath would never match, so the canary
        # below would "pass" everywhere, lane and canonical alike, and never
        # seed a failure at all.
        self.canon = os.path.realpath(self.repo)
        self.counter = os.path.join(self.tmp, "canon_fail_counter.txt")
        checker = write_stub(self.tmp, "check_logger.py",
                             CHECK_LOGGER % (self.canon, self.counter))
        check_cmd = "%s %s" % (sys.executable, checker)
        self.decomposer = write_stub(self.tmp, "decomposer.py",
                                     DECOMPOSER_WITH_GIVEN_CHECK % check_cmd)
        self.model = write_stub(self.tmp, "writer_model.py", WRITER_MODEL)
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)

    def test_the_failed_attempts_check_output_survives_the_retry(self):
        proc = sh([sys.executable, BROTHER_RUN, "r1 exists",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        # exit 2, not 0: CHECK_LOGGER's own invocation 1 IS brother_run's
        # CHECK DISCRIMINATION precheck (_stamp_prechecks), and it passes
        # quietly by design (the comment on CHECK_LOGGER above), which
        # stamps check_passed_before True on R1's row. Under rule 3 (the
        # zero-context critic, 2026-09-03) that receipt is NO-DATA, never
        # PASS, so this run correctly exits 2 (refused nothing, proved
        # nothing) rather than the old blanket 0. The retry itself, this
        # test's own point, is unaffected and still asserted below.
        self.assertEqual(proc.returncode, 2, out)
        # THE RETRY REALLY HAPPENED, across two rounds of the real drain, not
        # a fabricated setup: round 1 finishes nothing, round 2 finishes it.
        self.assertIn("round 1 done, 0 of 1", out, out)
        self.assertIn("round 2 done, 1 of 1", out, out)
        self.assertIn("integrated (1):", out, out)

        runs_dir = os.path.join(self.tmp, "docs", "plan", "runs")
        run_dirs = [os.path.join(runs_dir, d) for d in os.listdir(runs_dir)]
        self.assertEqual(len(run_dirs), 1, run_dirs)
        run_dir = run_dirs[0]

        # The attempt directory is named from the SANITIZED uid, not the raw
        # "R1" (see _safe_uid_segment / AttemptTraceUidIsSanitized below);
        # reuse the real function rather than a second, brittle copy of its
        # rule.
        r1_dir = _br._safe_uid_segment("R1")
        attempt1 = os.path.join(run_dir, "attempts", r1_dir, "attempt-1")
        attempt2 = os.path.join(run_dir, "attempts", r1_dir, "attempt-2")
        # BESIDE THE PASSING ATTEMPT, NEVER OVER IT: both directories exist.
        self.assertTrue(os.path.isdir(attempt1), out)
        self.assertTrue(os.path.isdir(attempt2), out)
        for d in (attempt1, attempt2):
            for name in ("claim.json", "engine_output.txt", "tree_state.txt"):
                self.assertTrue(os.path.isfile(os.path.join(d, name)),
                               "%s missing in %s" % (name, d))

        with open(os.path.join(attempt1, "claim.json"), encoding="utf-8") as fh:
            claim1 = json.load(fh)
        self.assertEqual(claim1["attempt"], 1, claim1)
        self.assertEqual(claim1["state"], "failed", claim1)
        self.assertEqual(claim1["evidence"]["exit_code"], 1, claim1)
        self.assertIn("CANARY-CHECK-FAILED-ONCE", claim1["evidence"]["output"])

        with open(os.path.join(attempt2, "claim.json"), encoding="utf-8") as fh:
            claim2 = json.load(fh)
        self.assertEqual(claim2["attempt"], 2, claim2)
        self.assertEqual(claim2["state"], "done", claim2)
        self.assertEqual(claim2["evidence"]["exit_code"], 0, claim2)

        # claims.json itself, the durable store, proves the loss this fix
        # closes: it carries ONLY the latest attempt, attempt 2, once the
        # unit is done, so it alone could never answer what attempt 1 saw.
        with open(os.path.join(run_dir, "claims.json"), encoding="utf-8") as fh:
            final_claims = json.load(fh)
        self.assertEqual(final_claims["R1"]["attempt"], 2, final_claims)

        # GREP OVER THE RUN DIRECTORY finds the failed attempt's check
        # output, driven for real rather than asserted: the done-check's own
        # words.
        grep = sh(["grep", "-rl", "CANARY-CHECK-FAILED-ONCE", run_dir])
        self.assertEqual(grep.returncode, 0, grep.stdout + grep.stderr)
        hits = grep.stdout.strip().splitlines()
        self.assertIn(os.path.join(attempt1, "claim.json"), hits, hits)
        # and it is truly the failed attempt's own trace, not a copy that
        # also leaked into the passing one
        self.assertNotIn(os.path.join(attempt2, "claim.json"), hits, hits)


class CheckDiscriminationPreRunProbe(unittest.TestCase):
    """_check_passes_now: the primitive CHECK DISCRIMINATION is built on
    (brother_run.py, the toy-repo finding 2026-09-03: a unit's own
    done_check that was already true of the untouched repository still
    scored a verified receipt for work that never happened). Mirrors
    _reexecute_check's own contract one level earlier: True/False is a real
    measurement, None is NO-DATA, and NO-DATA never blocks."""

    def test_a_check_that_already_passes_is_named_true(self):
        passed, exit_before, broken, note = _br._check_passes_now(
            "true", tempfile.gettempdir())
        self.assertIs(passed, True)
        self.assertEqual(exit_before, 0)
        self.assertFalse(broken)
        self.assertIsNone(note)

    def test_a_check_that_fails_is_named_false(self):
        passed, exit_before, broken, note = _br._check_passes_now(
            "false", tempfile.gettempdir())
        self.assertIs(passed, False)
        self.assertEqual(exit_before, 1)
        self.assertFalse(broken)
        self.assertIsNone(note)

    def test_no_command_is_no_data(self):
        passed, exit_before, broken, note = _br._check_passes_now(
            "", tempfile.gettempdir())
        self.assertIsNone(passed)
        self.assertIsNone(exit_before)
        self.assertFalse(broken)
        self.assertIn("no done_check", note)

    def test_a_command_that_cannot_run_is_no_data_not_a_crash(self):
        def boom(cmd, **kw):
            raise OSError("nope")
        passed, exit_before, broken, note = _br._check_passes_now(
            "true", tempfile.gettempdir(), runner=boom)
        self.assertIsNone(passed)
        self.assertIsNone(exit_before)
        self.assertFalse(broken)
        self.assertIn("could not be run", note)

    def test_a_syntax_error_is_named_false_and_looks_broken(self):
        """rule 4 (the zero-context critic, 2026-09-03): a check that cannot
        run at all still reports check_passed_before False today, exactly
        the same as an ordinary failing assertion. This is the new fact
        that tells them apart: the stderr the shell itself prints."""
        passed, exit_before, broken, _note = _br._check_passes_now(
            "python3 -c 'this is not python('", tempfile.gettempdir())
        self.assertIs(passed, False)
        self.assertIsNotNone(exit_before)
        self.assertNotEqual(exit_before, 0)
        self.assertTrue(broken)

    def test_an_ordinary_failing_assertion_does_not_look_broken(self):
        passed, _exit_before, broken, _note = _br._check_passes_now(
            "false", tempfile.gettempdir())
        self.assertIs(passed, False)
        self.assertFalse(broken)

    def test_the_same_timeout_budget_as_reexecution(self):
        """"Keep the timeout for the pre-run check the same as the
        re-execution's" (this row's own brief): the DEFAULT runner (no
        `runner` override, the shape a real caller uses) reads the one
        constant _reexecute_check itself reads, so the two can never drift
        apart by an edit to only one."""
        seen = {}
        orig_run = _br.subprocess.run

        def spy(cmd, **kw):
            seen["timeout"] = kw.get("timeout")
            return orig_run(cmd, **kw)
        _br.subprocess.run = spy
        try:
            _br._check_passes_now("true", tempfile.gettempdir())
        finally:
            _br.subprocess.run = orig_run
        self.assertEqual(seen["timeout"], _br.CHECK_RUN_TIMEOUT_SECONDS)


class StampPrechecksMarksUnitsBeforeAnyWorker(unittest.TestCase):
    """_stamp_prechecks: the Work-document-level wiring around
    _check_passes_now. Driven directly against a real Work document on
    disk, the same load/mutate/write shape _mark_integrated already uses,
    so this proves the stamp actually survives the round trip a later
    receipt reads it back through."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stamp-prechecks-")
        self.repo, _rev = _git_repo_with_file(self.tmp, "one.txt")

    def test_pass_fail_and_a_done_row_left_alone(self):
        doc = _write_doc(self.tmp, [
            {"id": "U1", "status": "SCHEDULED", "done_check": "true"},
            {"id": "U2", "status": "SCHEDULED", "done_check": "false"},
            {"id": "U3", "status": "DONE", "done_check": "true"},
        ])
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return subprocess.run(cmd, shell=True, cwd=self.repo,
                                  capture_output=True, text=True, timeout=30)
        rows = _br._stamp_prechecks(doc, self.repo, runner=runner)
        by_id = {r["id"]: r for r in rows}
        self.assertIs(by_id["U1"]["check_passed_before"], True)
        self.assertIs(by_id["U2"]["check_passed_before"], False)
        # U3 is already DONE: left alone, never re-run, never stamped.
        self.assertNotIn("check_passed_before", by_id["U3"])
        self.assertEqual(sorted(calls), ["false", "true"])

        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        saved_by_id = {r["id"]: r for r in saved["rows"]}
        self.assertIs(saved_by_id["U1"]["check_passed_before"], True)
        self.assertIs(saved_by_id["U2"]["check_passed_before"], False)
        # THE BOOKKEEPING KEY NEVER ROUND-TRIPS: _stamp_prechecks loads and
        # rewrites the document directly rather than trusting a caller's
        # in-memory record carrying "path".
        self.assertNotIn("path", saved)

    def test_check_exit_before_and_looks_broken_are_stamped_too(self):
        """rule 4 (the zero-context critic, 2026-09-03): the raw exit code
        and the broken-check signal ride along with check_passed_before,
        the same load/mutate/write round trip as the rest of this class."""
        doc = _write_doc(self.tmp, [
            {"id": "U1", "status": "SCHEDULED", "done_check": "false"},
            {"id": "U2", "status": "SCHEDULED",
             "done_check": "python3 -c 'this is not python('"},
        ])
        rows = _br._stamp_prechecks(doc, self.repo)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["U1"]["check_exit_before"], 1)
        self.assertNotIn("check_looks_broken", by_id["U1"])
        self.assertNotEqual(by_id["U2"]["check_exit_before"], 0)
        self.assertIs(by_id["U2"]["check_looks_broken"], True)


class TheUnitCheckLinesTheIntentScreenShows(unittest.TestCase):
    """_unit_check_lines: check-authorship-v1's Option A, the decision this
    whole row implements: "the intent screen ... lists each unit's id,
    objective and done_check verbatim". Driven directly, before any HTML
    rendering, on the exact strings a person or a grep would look for."""

    def test_id_objective_and_done_check_appear_verbatim(self):
        rows = [{"id": "U1", "objective": "add a guard",
                "done_check": "python3 -m pytest test_x.py -q"}]
        lines = _br._unit_check_lines(rows)
        self.assertEqual(len(lines), 1)
        self.assertIn("U1", lines[0])
        self.assertIn("add a guard", lines[0])
        self.assertIn("python3 -m pytest test_x.py -q", lines[0])
        self.assertNotIn("WARNING", lines[0])

    def test_title_is_the_fallback_objective(self):
        lines = _br._unit_check_lines(
            [{"id": "U2", "title": "fallback to title", "done_check": "true"}])
        self.assertIn("fallback to title", lines[0])

    def test_a_check_measured_true_before_any_work_carries_the_warning(self):
        lines = _br._unit_check_lines(
            [{"id": "U3", "objective": "x", "done_check": "true",
             "check_passed_before": True}])
        self.assertIn("WARNING", lines[0])
        self.assertIn("cannot prove the work", lines[0])

    def test_a_check_measured_false_carries_no_warning(self):
        lines = _br._unit_check_lines(
            [{"id": "U4", "objective": "x", "done_check": "false",
             "check_passed_before": False}])
        self.assertNotIn("WARNING", lines[0])

    def test_a_check_that_looks_broken_carries_the_distinct_cannot_run_warning(self):
        """rule 4 (the zero-context critic, 2026-09-03): a syntax error is
        not the same finding as an already-passing check, and the intent
        screen must say which one it is."""
        lines = _br._unit_check_lines(
            [{"id": "U5", "objective": "x", "done_check": "python3 -c '('",
             "check_passed_before": False, "check_looks_broken": True}])
        self.assertIn("WARNING", lines[0])
        self.assertIn("cannot run", lines[0])
        self.assertNotIn("already passes", lines[0])

    def test_already_passes_outranks_looks_broken_when_both_are_set(self):
        """Cannot both be true of a real run (a check that already exited 0
        never looks broken), but the elif order still must not print two
        warnings on the same line if a row carries both flags by mistake."""
        lines = _br._unit_check_lines(
            [{"id": "U6", "objective": "x", "done_check": "true",
             "check_passed_before": True, "check_looks_broken": True}])
        self.assertEqual(lines[0].count("WARNING"), 1)
        self.assertIn("already passes", lines[0])


class TheFactSpecCanCarryARefuseOptionWithoutOutranking(unittest.TestCase):
    """_fact_spec's new `extra_options`: check-authorship-v1's "Refuse:
    nothing is claimed", the second option the intent screen now carries.
    Every existing caller (test_intent_and_forcing_condition_specs_are_
    weighted_and_computed, above) passes nothing and must still get exactly
    one option back; a refuse option that names no scores must never
    outrank the measured one, which is what keeps an unattended run
    unattended."""

    def test_no_extra_options_keeps_exactly_one_option(self):
        spec = _br._fact_spec("t", "e", "p", "q", "x", "X", "one",
                              {"k": (1.0, 5.0, "w")})
        self.assertEqual(len(spec["options"]), 1)

    def test_a_refuse_option_scores_zero_and_never_outranks_proceed(self):
        refuse = {"id": "refuse", "name": "Refuse: nothing is claimed",
                 "one_liner": "stop here"}
        spec = _br._fact_spec("t", "e", "p", "q", "proceed", "Proceed", "one",
                              {"k": (1.0, 8.0, "w")}, extra_options=[refuse])
        self.assertEqual(len(spec["options"]), 2)
        _c, _n, scored, _close = decide.rank(spec)
        self.assertEqual(scored[0]["option"]["id"], "proceed", scored)
        self.assertEqual(scored[1]["option"]["id"], "refuse", scored)
        self.assertEqual(scored[1]["total"], 0.0, scored)
        # THE AUTO RESOLVER IS UNCHANGED: the top-ranked option, unattended.
        choice = _br._auto_resolver("intent", spec, scored, False)
        self.assertEqual(choice["choice"], "proceed", choice)


class TheIntentScreenCanRefuseBeforeAnyClaim(unittest.TestCase):
    """End to end, the same --resume/--interactive fixture
    TheScreenLoomFiresAtAllFourMomentsOfARealRun already uses: a live
    person at the intent screen who types "refuse" stops the run before
    loop_bridge is ever called, with a clear line and a non-zero exit, and
    nothing lands in the repository or the claim store."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="intent-refuse-repo-")
        for args in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            sh(["git"] + args, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)
        self.log_before = sh(["git", "log", "--oneline"], self.repo).stdout

        self.run_dir = tempfile.mkdtemp(prefix="intent-refuse-run-")
        rec, problems = WR.create(
            "one plain piece of work", [{"id": "A1", "title": "create a1",
                                        "done_check": "true",
                                        "owns": ["A1.txt"]}],
            store=self.run_dir)
        self.assertEqual(problems, [])
        self._orig_run_loop = _br.run_loop
        self._orig_stdin = _br.sys.stdin
        self._loop_calls = []
        _br.run_loop = self._counting_fake_loop

    def _counting_fake_loop(self, plan_path, claims_path, cwd, slots):
        self._loop_calls.append(True)
        return 1, "should never be reached"

    def tearDown(self):
        _br.run_loop = self._orig_run_loop
        _br.sys.stdin = self._orig_stdin

    def test_refusing_at_intent_stops_before_any_claim(self):
        read_fd, write_fd = os.pipe()
        reader, writer = os.fdopen(read_fd, "r"), os.fdopen(write_fd, "w")
        _br.sys.stdin = reader
        writer.write("refuse\n")   # THE SCRIPTED ANSWER
        writer.flush()
        writer.close()

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _br.main(["ignored", "--resume", self.run_dir, "--cwd",
                             self.repo, "--interactive"])
        reader.close()
        text = out.getvalue() + err.getvalue()

        self.assertEqual(code, 1, text)
        self.assertIn("refused at the intent screen; nothing was claimed "
                      "or run", text)
        # LOOP_BRIDGE WAS NEVER CALLED: no round of the drain ever ran.
        self.assertEqual(self._loop_calls, [])
        # THE REPOSITORY DID NOT MOVE.
        log_after = sh(["git", "log", "--oneline"], self.repo).stdout
        self.assertEqual(log_after, self.log_before)
        # NOTHING WAS CLAIMED: the claim store was never written.
        self.assertFalse(
            os.path.isfile(os.path.join(self.run_dir, "claims.json")))


class ZeroChangeAndCheckDiscriminationEndToEnd(unittest.TestCase):
    """The toy-repo finding, driven through the real round loop rather than
    only against receipt_door.receipts_for directly (that half is pinned in
    test_receipt_door.py's CheckDiscriminationRefusesACheckThatAlreadyPassed):
    _mark_integrated must actually STAMP `files_changed_by_unit` from the
    claim's own evidence (integrate_one's measured list, [] here), and
    _stamp_prechecks must actually run before
    the fake loop's first call, for a real run of main() to end with the
    right receipt."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="zero-change-repo-")
        for args in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            sh(["git"] + args, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)
        self._orig_run_loop = _br.run_loop

    def tearDown(self):
        _br.run_loop = self._orig_run_loop

    def test_a_unit_that_changes_no_file_is_no_data_on_the_report(self):
        """A unit whose own done_check is already true (`true`, a check
        that passes on any tree) and whose fake round commits nothing:
        BOTH new facts fire, and the receipt reads NO-DATA, though the
        engine still calls it integrated (a plain change, never refused)."""
        run_dir = tempfile.mkdtemp(prefix="zero-change-run-")
        rec, problems = WR.create(
            "a unit that changes nothing",
            [{"id": "Z1", "title": "already true", "done_check": "true",
             "owns": ["base.txt"]}], store=run_dir)
        self.assertEqual(problems, [])

        def _fake_loop(plan_path, claims_path, cwd, slots):
            claim, _problem = claim_store.acquire(claims_path, "Z1", "t")
            claim_store.release(
                claims_path, "Z1", "t", state="done",
                evidence={"check_command": "true", "exit_code": 0,
                         "output": "ok", "output_truncated": False,
                         "canonical_rev": _br._head(self.repo),
                         "files_changed": []})
            return 0, "Z1 done scope=CLEAN integrated=True"
        _br.run_loop = _fake_loop

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _br.main(["ignored", "--resume", run_dir, "--cwd",
                             self.repo])
        text = out.getvalue() + err.getvalue()
        # exit 2, not 0: this fixture's own point is that Z1's receipt is
        # NO-DATA (asserted below), and under rule 3 (the zero-context
        # critic, 2026-09-03) a run that refused nothing but proved nothing
        # is exit 2, never the old blanket 0.
        self.assertEqual(code, 2, text)
        self.assertIn("Z1 is NO-DATA:", text, text)
        self.assertIn("verdicts: 0 PASS, 0 FAIL, 1 NO-DATA", text, text)


GUARDED_MATHLIB = """def add(a, b):
    for name, value in (("a", a), ("b", b)):
        if not isinstance(value, (int, float)):
            raise TypeError("add() argument %s must be numbers" % name)
    return a + b
"""

#: A test that only asks for a TypeError: stock Python raises one for
#: 'a' + 1 on its own, so this passes with the guard deleted.
TEST_ANY_TYPEERROR = """import mathlib, sys
try:
    mathlib.add('a', 1)
except TypeError:
    sys.exit(0)
sys.exit(1)
"""

#: A test that asks for the guard's OWN message: red without the guard.
TEST_THE_MESSAGE = """import mathlib, sys
try:
    mathlib.add('a', 1)
except TypeError as exc:
    sys.exit(0 if "must be numbers" in str(exc) else 1)
sys.exit(1)
"""


class DependencyMutationStampsTheRealFact(unittest.TestCase):
    """Rule 5 (EVAD run 4 trial 2, 2026-09-03), driven against a real git
    repository shaped like the toy's run 5: a guard commit on mathlib.py,
    then a test commit that depends on it. _stamp_dependency_mutations must
    re-run the test's check at the test's own revision with mathlib.py put
    back to how it stood before the guard, in a throwaway worktree it then
    removes, and stamp the real exit code: 0 for a test that only asks for
    Python's own TypeError, non-zero for a test that asks for the guard's
    message. The receipt half is pinned in test_receipt_door.py."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dep-mutation-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            sh(["git"] + args, self.repo)
        self._commit("mathlib.py", "def add(a, b):\n    return a + b\n", "R0")
        self.guard_rev = self._commit("mathlib.py", GUARDED_MATHLIB, "guard")

    def _commit(self, name, body, message):
        with open(os.path.join(self.repo, name), "w", encoding="utf-8") as fh:
            fh.write(body)
        sh(["git", "add", name], self.repo)
        sh(["git", "commit", "-q", "-m", message], self.repo)
        return sh(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def _doc_and_claims(self, test_body):
        test_rev = self._commit("test_it.py", test_body, "test")
        doc = _write_doc(self.tmp, [
            {"id": "guard", "status": "DONE", "depends_on": [],
             "done_check": "python3 -c 'import mathlib'",
             "check_passed_before": False, "files_changed_by_unit": ["mathlib.py"]},
            {"id": "test", "status": "DONE", "depends_on": ["guard"],
             "done_check": "python3 test_it.py",
             "check_passed_before": False, "files_changed_by_unit": ["test_it.py"]}])
        claims = {
            "guard": {"state": "done", "evidence": {
                "check_command": "python3 -c 'import mathlib'", "exit_code": 0,
                "output": "", "canonical_rev": self.guard_rev}},
            "test": {"state": "done", "evidence": {
                "check_command": "python3 test_it.py", "exit_code": 0,
                "output": "", "canonical_rev": test_rev}}}
        return doc, claims, test_rev

    def test_a_test_that_passes_without_the_guard_is_stamped_exit_0(self):
        doc, claims, test_rev = self._doc_and_claims(TEST_ANY_TYPEERROR)
        stamped = _br._stamp_dependency_mutations(doc, claims, self.repo)
        self.assertEqual(stamped["test"], [{
            "unit": "guard", "files": ["mathlib.py"], "revision": test_rev,
            "exit_code": 0, "stderr": "", "note": ""}])
        with open(doc, encoding="utf-8") as fh:
            rows = {r["id"]: r for r in json.load(fh)["rows"]}
        self.assertNotIn(_br.CHECK_WITHOUT_FIELD, rows["guard"])
        receipts = RD.receipts_for({"rows": list(rows.values())}, claims, [])
        self.assertEqual([r["state"] for r in receipts],
                         ["verified", "no-data"])
        self.assertIn("guard's change to mathlib.py reverted",
                      receipts[1]["reason"])

    def test_a_test_that_needs_the_guard_is_stamped_non_zero_and_verifies(self):
        doc, claims, _rev = self._doc_and_claims(TEST_THE_MESSAGE)
        stamped = _br._stamp_dependency_mutations(doc, claims, self.repo)
        self.assertEqual(stamped["test"][0]["exit_code"], 1)
        with open(doc, encoding="utf-8") as fh:
            record = json.load(fh)
        receipts = RD.receipts_for(record, claims, [])
        self.assertEqual([r["state"] for r in receipts],
                         ["verified", "verified"])

    def test_the_throwaway_worktree_is_gone_and_canonical_is_untouched(self):
        doc, claims, test_rev = self._doc_and_claims(TEST_ANY_TYPEERROR)
        _br._stamp_dependency_mutations(doc, claims, self.repo)
        listed = sh(["git", "worktree", "list", "--porcelain"], self.repo).stdout
        self.assertEqual(listed.count("worktree "), 1, listed)
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], self.repo)
                         .stdout.strip(), test_rev)
        self.assertEqual(sh(["git", "status", "--porcelain"], self.repo)
                         .stdout.strip(), "")
        with open(os.path.join(self.repo, "mathlib.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), GUARDED_MATHLIB)

    def test_a_dependency_with_no_recorded_revision_is_stamped_none_with_why(self):
        doc, claims, _rev = self._doc_and_claims(TEST_ANY_TYPEERROR)
        del claims["guard"]
        stamped = _br._stamp_dependency_mutations(doc, claims, self.repo)
        entry = stamped["test"][0]
        self.assertIsNone(entry["exit_code"])
        self.assertIn("guard's integrated revision is not recorded",
                      entry["note"])
        with open(doc, encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual(RD.receipts_for(record, claims, [])[1]["state"],
                         "no-data")

    def test_a_row_already_stamped_is_left_alone(self):
        doc, claims, _rev = self._doc_and_claims(TEST_ANY_TYPEERROR)
        first = _br._stamp_dependency_mutations(doc, claims, self.repo)
        self.assertIn("test", first)
        self.assertEqual(_br._stamp_dependency_mutations(doc, claims, self.repo),
                         {})


class DependencyMutationEndToEnd(unittest.TestCase):
    """The same rule through main(): a unit D and a unit U that depends on
    it, each with a check that only asks whether its own file exists, so
    U's check still passes once D's file is put back to absent. The report
    must read D PASS and U NO-DATA naming D.txt, 1 PASS, 0 FAIL, 1 NO-DATA,
    and the run still exits 0 because one unit was proven and nothing was
    refused."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="dep-e2e-repo-")
        for args in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            sh(["git"] + args, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)
        self.run_dir = tempfile.mkdtemp(prefix="dep-e2e-run-")
        rec, problems = WR.create(
            "a unit and a test that does not need it",
            [{"id": "D", "title": "create d", "done_check": "test -f D.txt",
             "owns": ["D.txt"]},
            {"id": "U", "title": "create u", "done_check": "test -f U.txt",
             "owns": ["U.txt"], "depends_on": ["D"]}],
            store=self.run_dir)
        self.assertEqual(problems, [])
        self._orig_run_loop = _br.run_loop

    def tearDown(self):
        _br.run_loop = self._orig_run_loop

    def test_the_report_reads_the_dependent_unit_no_data_naming_the_file(self):
        _br.run_loop = _fake_multi_unit_loop(self.repo, {"D": ["done"],
                                                         "U": ["done"]})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _br.main(["ignored", "--resume", self.run_dir, "--cwd",
                             self.repo])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("D delivered:", text)
        self.assertIn("U is NO-DATA: the check still passes with D's change "
                      "to D.txt reverted, so it does not exercise that "
                      "change", text)
        self.assertIn("verdicts: 1 PASS, 0 FAIL, 1 NO-DATA", text)
        with open(os.path.join(self.run_dir, "run.log"), encoding="utf-8") as fh:
            logged = fh.read()
        self.assertIn("U's check re-run at", logged)
        self.assertIn("with D's change to D.txt reverted: exited 0", logged)
        listed = sh(["git", "worktree", "list", "--porcelain"], self.repo).stdout
        self.assertEqual(listed.count("worktree "), 1, listed)


class TheGovernorLineDuringAWait(unittest.TestCase):
    """The round loop in main() is a poll: dispatch a batch, check what came
    back, repeat. A person watching it must be told once that a wait has
    begun and once that it is over, never once per poll. Driven directly
    against the two governor helpers rather than a full run, because a real
    multi-round fixture would only prove the same two calls fire once each,
    slower."""

    def test_three_polls_produce_one_line_and_one_closing_line(self):
        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            start = _br._governor_wait_line(
                log, "U1, U2", clock=datetime.datetime(2026, 1, 1, 10, 0, 0))
            for _poll in range(3):
                pass  # the fake wait: three polls, nothing said in between
            _br._governor_wait_close(
                log, start, clock=datetime.datetime(2026, 1, 1, 10, 0, 7))
        printed = out.getvalue()
        self.assertEqual(printed.count("waiting on"), 1, printed)
        self.assertEqual(printed.count("done waiting"), 1, printed)
        self.assertIn("brother_run: waiting on U1, U2 since 10:00:00; "
                      "nothing else is running", printed)
        self.assertIn("brother_run: done waiting, 7.0s elapsed", printed)


class CostBlockCarriesAllEightFields(unittest.TestCase):
    """T1: a delivery record names tokens in, out and cached, turns, wall
    clock, cache hit rate, a failure category and the harness version. A
    field is only "absent" when its KEY is missing from the block; NO-DATA
    (plus a reason) is a present, honest value and is never confused with a
    missing key."""

    def test_a_normal_run_carries_all_eight_fields(self):
        claims = {"U1": {"state": "done", "attempt": 2,
                        "evidence": {"check_command": "true", "exit_code": 0,
                                     "output": "", "canonical_rev": "abc"}}}
        block = _br.build_cost_block(claims, [], "", 1.5, "v1.2-3-gdeadbee")
        ok, missing = _br.validate_cost_block(block)
        self.assertTrue(ok, missing)
        self.assertEqual(missing, [])
        for field in _br.COST_FIELDS:
            self.assertIn(field, block)
        self.assertEqual(block["turns"], 2)
        self.assertEqual(block["wall_clock_seconds"], 1.5)
        self.assertEqual(block["harness_version"], "v1.2-3-gdeadbee")
        self.assertEqual(block["failure_category"], "none")

    def test_absent_token_data_is_no_data_never_a_zero(self):
        """No claim in this run carries a "usage" dict (model_worker.py
        never records one), so every token field and the cache hit rate must
        say NO-DATA and why, never fold into a zero that would read as "no
        tokens were used"."""
        block = _br.build_cost_block({}, [], "", 0.1, "v1")
        for field in ("tokens_in", "tokens_out", "tokens_cached",
                     "cache_hit_rate"):
            self.assertTrue(str(block[field]).startswith(_br.NODATA),
                            "%s: %r" % (field, block[field]))

    def test_real_usage_is_summed_and_a_cache_hit_rate_is_computed(self):
        claims = {"U1": {"attempt": 1, "usage": {"tokens_in": 100,
                                                 "tokens_out": 40,
                                                 "tokens_cached": 25}},
                 "U2": {"attempt": 3, "usage": {"tokens_in": 50,
                                                "tokens_out": 10,
                                                "tokens_cached": 25}}}
        block = _br.build_cost_block(claims, [], "", 2.0, "v1")
        self.assertEqual(block["tokens_in"], 150)
        self.assertEqual(block["tokens_out"], 50)
        self.assertEqual(block["tokens_cached"], 50)
        self.assertEqual(block["turns"], 4)
        self.assertAlmostEqual(block["cache_hit_rate"], 50 / 150, places=4)

    def test_failure_category_reads_the_engines_own_words(self):
        cases = [
            ([], "", "none"),
            ([("U1", "QUARANTINE: 1 path(s) changed that U1 never "
                     "declared")], "", "scope-violation"),
            ([("U1", "its own check did not pass on the current base")],
            "", "check-failed"),
            ([("U1", "model command timed out after 900s")], "", "timeout"),
            ([("U1", "the repository being worked on had uncommitted "
                     "changes")], "", "crashed"),
        ]
        for refused, loop_text, want in cases:
            self.assertEqual(_br._failure_category(refused, loop_text), want,
                             refused)

    def test_a_field_deleted_from_a_good_block_is_refused_driven_backwards(self):
        """Driven backwards, T1's own done-check: a validator that never
        fails proves nothing. Delete one required field at a time from an
        otherwise-complete block and validate_cost_block must refuse it,
        naming exactly that field."""
        good = _br.build_cost_block({}, [], "", 1.0, "v1")
        ok, missing = _br.validate_cost_block(good)
        self.assertTrue(ok, missing)
        for field in _br.COST_FIELDS:
            broken = dict(good)
            del broken[field]
            ok, missing = _br.validate_cost_block(broken)
            self.assertFalse(
                ok, "validate_cost_block passed a block missing %r" % field)
            self.assertEqual(missing, [field])

    def test_the_printed_delivery_report_carries_the_cost_block(self):
        rec = {"outcome": "x", "work_id": "w1",
              "rows": [{"id": "U1", "done_check": "true", "status": "DONE"}]}
        block = _br.build_cost_block({}, [], "", 3.25, "v9-dirty")
        report, _integ, _ref = _br.build_report(
            rec, {"U1": {"state": "done"}}, "a", "b", changed=[],
            cost_block=block)
        for field in _br.COST_FIELDS:
            self.assertIn("    %s:" % field, report)
        self.assertIn("wall_clock_seconds: 3.25", report)
        self.assertIn("harness_version: v9-dirty", report)


class UsageSidecarReachesTheClaimsBeforeTheCostBlock(unittest.TestCase):
    """T1 follow-up: model_worker.py now reads real tokens_in/tokens_out/
    tokens_cached off the claude CLI's own answer, and loop_bridge.py writes
    them to a sidecar beside the claim store (loop_bridge.usage_sidecar_path)
    rather than into claim_store.py, which this task does not own the scope
    to widen. _merge_usage_sidecar is the small seam that folds the sidecar
    back into the claims dict before build_cost_block sums it, so this
    proves the whole path: a real sidecar reaches a real cost block."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage-seam-")
        self.claims_path = os.path.join(self.tmp, "claims.json")
        self.sidecar_path = os.path.join(self.tmp, "claims_usage.json")

    def test_sidecar_usage_is_summed_into_a_real_cost_block(self):
        claims = {"U1": {"attempt": 1}, "U2": {"attempt": 2}}
        with open(self.sidecar_path, "w", encoding="utf-8") as fh:
            json.dump({"U1": {"tokens_in": 100, "tokens_out": 40,
                              "tokens_cached": 25},
                      "U2": {"tokens_in": 50, "tokens_out": 10,
                            "tokens_cached": 25}}, fh)
        merged = _br._merge_usage_sidecar(claims, self.claims_path)
        block = _br.build_cost_block(merged, [], "", 2.0, "v1")
        self.assertEqual(block["tokens_in"], 150)
        self.assertEqual(block["tokens_out"], 50)
        self.assertEqual(block["tokens_cached"], 50)

    def test_no_sidecar_leaves_usage_as_no_data_never_a_zero(self):
        """The backwards case: no sidecar was ever written (today's default
        worker still reports no usage), so the cost block must say NO-DATA
        for every token field, exactly as it did before this seam existed."""
        claims = {"U1": {"attempt": 1}}
        merged = _br._merge_usage_sidecar(claims, self.claims_path)
        self.assertNotIn("usage", merged["U1"])
        block = _br.build_cost_block(merged, [], "", 1.0, "v1")
        for field in ("tokens_in", "tokens_out", "tokens_cached"):
            self.assertTrue(str(block[field]).startswith(_br.NODATA))

    def test_a_claims_own_usage_is_never_overwritten_by_the_sidecar(self):
        claims = {"U1": {"attempt": 1, "usage": {"tokens_in": 9, "tokens_out": 1,
                                                 "tokens_cached": 0}}}
        with open(self.sidecar_path, "w", encoding="utf-8") as fh:
            json.dump({"U1": {"tokens_in": 999, "tokens_out": 999,
                              "tokens_cached": 999}}, fh)
        merged = _br._merge_usage_sidecar(claims, self.claims_path)
        self.assertEqual(merged["U1"]["usage"]["tokens_in"], 9)


class AnInstalledCopyNamesItsEngineFromTheManifest(unittest.TestCase):
    """harness-identity-v1 (the zero-context critic reading a fresh clone of
    the public v1.0.0, 2026-09-03): the documented install puts the engine in
    a plugin CACHE, which is a copy and holds no .git, so `git rev-parse`
    exits 128 there and every receipt an installed Brother produced read
    "harness NO-DATA: git rev-parse exited 128". Driven the way the defect
    was found: this repository's own bundle/runtime, copied to a directory
    that is not inside any checkout, asked for its own identity."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-installed-")
        # The COPY is what an install ships: same bytes, no .git anywhere
        # above it. If the temp root happens to sit inside a checkout the
        # premise of this test is gone, so it reports NO-DATA rather than
        # measuring the wrong thing.
        if sh(["git", "rev-parse", "HEAD"], cwd=self.tmp).returncode == 0:
            self.skipTest("NO-DATA: the temp root is itself inside a git "
                          "checkout, so a copy placed here is not the "
                          "installed case this test is about")
        self.runtime = os.path.join(self.tmp, "runtime")
        shutil.copytree(os.path.join(_br.REPO_ROOT, "bundle", "runtime"),
                        self.runtime)
        self.manifest_path = os.path.join(self.runtime, "RUNTIME-MANIFEST.json")

    def _ask(self, name):
        """One identity string out of the COPIED engine, in its own process,
        so HERE (and the manifest it reads) is the copy's directory and not
        this repository's scripts/."""
        proc = sh([sys.executable, "-c",
                   "import sys; sys.path.insert(0, sys.argv[1]);"
                   " import brother_run;"
                   " sys.stdout.write(getattr(brother_run, sys.argv[2])())",
                   self.runtime, name], cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def test_the_revision_comes_from_the_manifest_and_says_so(self):
        with open(self.manifest_path, encoding="utf-8") as fh:
            stamped = json.load(fh)["source_revision"]
        self.assertRegex(stamped, r"^[0-9a-f]{40}$",
                         "the packaged manifest must carry a real source "
                         "revision for an installed copy to read")
        got = self._ask("_harness_revision")
        self.assertNotIn(_br.NODATA, got, got)
        self.assertEqual(got, stamped + _br.MANIFEST_SOURCE_NOTE)

    def test_the_version_comes_from_the_manifest_and_says_so(self):
        with open(self.manifest_path, encoding="utf-8") as fh:
            stamped = json.load(fh)["source_describe"]
        self.assertNotIn(_br.NODATA, stamped, stamped)
        got = self._ask("_harness_version")
        self.assertEqual(got, stamped + _br.MANIFEST_SOURCE_NOTE)

    def test_neither_git_nor_a_manifest_is_still_no_data(self):
        os.remove(self.manifest_path)
        for name in ("_harness_revision", "_harness_version"):
            got = self._ask(name)
            self.assertTrue(got.startswith(_br.NODATA),
                            "%s with no git and no manifest must stay "
                            "NO-DATA, never a guess: %s" % (name, got))


class HarnessVersionIsGitDescribe(unittest.TestCase):
    """harness_version is `git describe --always --dirty` of the tree that
    ran, captured at run time, never a fabricated string."""

    def test_this_checkout_reports_a_real_describe_string(self):
        """Proves only inside a real git checkout (a worktree's .git FILE
        counts, os.path.exists catches both shapes): a tarball from `git
        archive` carries no .git entry at all, so git describe cannot run
        there and the honest answer is NO-DATA, not a test failure."""
        repo_root = os.path.dirname(HERE)
        if not os.path.exists(os.path.join(repo_root, ".git")):
            self.skipTest("NO-DATA: not a git checkout, so git describe "
                          "cannot run here (no .git entry, e.g. a tree "
                          "extracted from `git archive`)")
        version = _br._harness_version(repo_root)
        self.assertFalse(version.startswith(_br.NODATA), version)

    def test_a_non_git_directory_is_no_data(self):
        tmp = tempfile.mkdtemp(prefix="not-a-repo-")
        version = _br._harness_version(tmp)
        self.assertTrue(version.startswith(_br.NODATA), version)


class TheScreenLoomPosesWeightedOptionsAtEveryMoment(unittest.TestCase):
    """I3's own contract: every one of the four human moments renders a
    decide.py screen whose options carry COMPUTED weights and marks, never
    a typed opinion, and decide.py's own arithmetic produces a
    recommendation from them. Driven directly against each moment's real
    spec shape: _fact_spec for intent and forcing-condition (the two this
    file adds), receipt_door.acceptance_spec/release_spec for the two
    receipt_door already covers, reused here rather than re-typed."""

    def test_intent_and_forcing_condition_specs_are_weighted_and_computed(self):
        cases = (
            dict(title="Proceed with this outcome", eyebrow="Intent",
                plain_summary="p", question="q", option_id="proceed",
                option_name="Proceed", one_liner="one",
                marks={"matches_the_settled_outcome": (0.5, 9.0, "fact a"),
                      "already_progressed": (0.5, 3.0, "fact b")}),
            dict(title="Stop retrying, or keep guessing",
                eyebrow="Forcing condition", plain_summary="p", question="q",
                option_id="stop-here", option_name="Stop", one_liner="one",
                marks={"retry_budget_spent": (1.0, 10.0, "fact c")}),
        )
        for kwargs in cases:
            spec = _br._fact_spec(**kwargs)
            for c in spec["criteria"]:
                self.assertGreater(c["weight"], 0, spec)
            _c, _n, scored, _close = decide.rank(spec)
            self.assertEqual(len(scored), 1, scored)
            expected = sum(weight * mark for weight, mark, _why
                          in kwargs["marks"].values())
            self.assertAlmostEqual(scored[0]["total"], expected, places=6,
                                   msg=spec)

    def test_release_and_acceptance_specs_are_weighted_and_computed(self):
        rows = [{"id": "U1", "title": "delete the abandoned cache",
                "done_check": "true", "owns": [], "check_passed_before": False,
                "files_changed_by_unit": ["cache.tmp"]}]
        claims = {"U1": {"state": "done", "evidence": {
            "check_command": "true", "exit_code": 0, "output": "",
            "canonical_rev": "x"}}}
        receipts = RD.receipts_for({"rows": rows}, claims, [])
        triggers = RD.risk_triggers(rows)
        self.assertTrue(triggers, "the fixture must actually trip a risk "
                                  "class, or this proves nothing")
        for spec in (RD.acceptance_spec({"rows": rows, "outcome": "o"},
                                        receipts),
                    RD.release_spec({"rows": rows, "outcome": "o"}, receipts,
                                    triggers)):
            for c in spec["criteria"]:
                self.assertGreater(c["weight"], 0, spec)
            _c, _n, scored, _close = decide.rank(spec)
            self.assertEqual(scored[0]["total"], 10.0, spec)


class TheScreenLoomBlocksUntilTheResolverReturns(unittest.TestCase):
    """"The run does not proceed past a human moment until a choice is
    recorded" (I3's own brief). In a single-threaded call, that is exactly
    the ordinary guarantee a function call already gives its caller: code
    after _human_moment cannot run before _human_moment's own call to
    resolver() returns. Driven two ways: a resolver whose own internal
    steps must all have happened before the seam hands back a choice, and
    a resolver that never decides (it raises) proving the seam does not
    swallow that into a silent proceed."""

    def _spec(self):
        return _br._fact_spec("t", "e", "p", "q", "x", "X", "one",
                              {"k": (1.0, 5.0, "w")})

    def test_nothing_after_the_call_runs_before_the_resolver_decides(self):
        order = []

        def slow_resolver(moment, spec, scored, close):
            order.append("resolver-step-1")
            order.append("resolver-step-2")
            return {"choice": "x", "name": "X", "by": "test", "auto": False}

        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            choice = _br._human_moment(log, "intent", self._spec(),
                                       resolver=slow_resolver)
        order.append("seam-returned-to-caller")
        self.assertEqual(order, ["resolver-step-1", "resolver-step-2",
                                 "seam-returned-to-caller"])
        self.assertEqual(choice["choice"], "x")

    def test_a_resolver_that_cannot_decide_yet_is_never_swallowed(self):
        """A resolver standing in for "no answer exists yet" raises rather
        than returning a fake choice; the seam must propagate that, never
        catch it and invent a proceed on the caller's behalf."""
        def undecided_resolver(moment, spec, scored, close):
            raise RuntimeError("not decided yet")

        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(RuntimeError):
                _br._human_moment(log, "intent", self._spec(),
                                  resolver=undecided_resolver)

    def test_the_recorded_default_never_blocks_a_non_interactive_run(self):
        """The other half of "a passed-in resolver or a recorded default":
        with no resolver at all, _auto_resolver names the top-ranked
        option immediately rather than hanging, which is what lets every
        test that predates this seam keep running unattended."""
        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            choice = _br._human_moment(log, "intent", self._spec())
        self.assertEqual(choice["choice"], "x")
        self.assertTrue(choice["auto"])

    def test_release_and_acceptance_never_auto_choose(self):
        """loom.py's own rule, read back rather than reimplemented: "no
        default acceptor". _recorded_answer_resolver must never invent an
        accept or a hold when nobody has answered, unlike _auto_resolver."""
        tmp = tempfile.mkdtemp(prefix="no-default-acceptor-")
        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            choice = _br._human_moment(
                log, "release", self._spec(),
                resolver=_br._recorded_answer_resolver(tmp, "release"))
        self.assertIsNone(choice["choice"])
        self.assertIn("no default acceptor", choice["by"])


class TheScreenLoomSplitsMachineryFromTheChatStream(unittest.TestCase):
    """"Sends the machinery to a log, and emits exactly one echo line and
    one proof line to the chat stream" (I3's own brief), pinned exactly."""

    def test_exactly_one_echo_and_one_proof_line_reach_stdout(self):
        tmp = tempfile.mkdtemp(prefix="human-moment-log-")
        log = _br.RunLog()
        log.to(tmp)
        spec = _br._fact_spec(
            "A title only the log should ever see", "Test", "plain",
            "question?", "opt", "The recommended option", "one liner",
            {"a": (0.5, 8.0, "why a"), "b": (0.5, 2.0, "why b")})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            choice = _br._human_moment(log, "intent", spec)
        printed = out.getvalue()
        lines = [line for line in printed.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2, printed)
        self.assertIn("option(s) considered", lines[0])
        self.assertIn("recommended at", lines[0])
        self.assertIn("The recommended option", lines[0])
        self.assertIn("resolved: chose", lines[1])
        self.assertEqual(choice["choice"], "opt")

        with open(log.path, encoding="utf-8") as fh:
            logged = fh.read()
        # THE MACHINERY (decide.py's own rendered screen, including its
        # title, which the two short chat-stream lines above never carry)
        # reached the log and never the chat stream.
        self.assertNotIn("<style>", printed)
        self.assertIn("<style>", logged)
        self.assertNotIn("A title only the log should ever see", printed)
        self.assertIn("A title only the log should ever see", logged)


class TheScreenLoomHasALiveInteractiveResolver(unittest.TestCase):
    """I3's OWED half, closed here: intent and forcing-condition wired to a
    REAL live resolver (_interactive_resolver), off by default and wired in
    by main() only via --interactive or BROTHER_INTERACTIVE=1 (proven
    end-to-end in TheScreenLoomFiresAtAllFourMomentsOfARealRun, below).

    Driven with a real os.pipe standing in for a human, never a mock: its
    read end genuinely blocks on .readline() until a writer sends a line,
    the same block a live terminal's stdin would apply. So "the run PAUSED
    until the scripted answer arrived" is proven the same way any blocking
    call is proven: start it on its own thread, prove the thread is still
    alive well after a non-blocking call would have returned, then supply
    the answer and prove the thread finishes with the chosen option."""

    def _spec(self, option_id, option_name):
        return _br._fact_spec("t", "e", "p", "q", option_id, option_name,
                              "one", {"k": (1.0, 7.0, "w")})

    def test_pauses_until_the_scripted_answer_arrives_then_proceeds(self):
        # intent matched by typing the option's id; forcing-condition
        # matched by typing its number, exercising both ways
        # _interactive_resolver accepts an answer.
        for moment, option_id, answer in (
                ("intent", "proceed", "proceed"),
                ("forcing-condition", "stop-here", "1")):
            with self.subTest(moment=moment):
                run_dir = tempfile.mkdtemp(prefix="live-resolver-")
                log = _br.RunLog()
                log.to(run_dir)
                read_fd, write_fd = os.pipe()
                reader, writer = os.fdopen(read_fd, "r"), os.fdopen(write_fd, "w")
                resolver = _br._interactive_resolver(
                    reader, prompt_stream=io.StringIO())
                spec = self._spec(option_id, option_id.title())
                result = {}

                def _call():
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        result["choice"] = _br._human_moment(
                            log, moment, spec, resolver=resolver)
                    result["stdout"] = out.getvalue()

                t = threading.Thread(target=_call)
                t.start()
                # PAUSED: a non-blocking (_auto_resolver) call would have
                # returned almost instantly; 0.3s proves this one did not.
                t.join(timeout=0.3)
                self.assertTrue(
                    t.is_alive(),
                    "%s resolved before the scripted answer arrived; the "
                    "interactive seam did not block" % moment)
                self.assertNotIn("choice", result)

                writer.write(answer + "\n")   # THE SCRIPTED ANSWER
                writer.flush()
                writer.close()
                t.join(timeout=5)
                self.assertFalse(
                    t.is_alive(),
                    "%s never returned once the scripted answer arrived"
                    % moment)
                reader.close()

                choice = result["choice"]
                self.assertEqual(choice["choice"], option_id, choice)
                self.assertFalse(choice["auto"], choice)
                self.assertIn("live", choice["by"])

                # EXACTLY ONE ECHO AND ONE PROOF LINE reached the chat
                # stream; the resolver's own prompt went to prompt_stream,
                # not stdout, so it never shows up in this count.
                printed = [ln for ln in result["stdout"].splitlines()
                          if ln.strip()]
                self.assertEqual(len(printed), 2, result["stdout"])
                self.assertIn("option(s) considered", printed[0])
                self.assertIn("resolved: chose", printed[1])

                # THE MACHINERY reached the log, never the chat stream.
                with open(log.path, encoding="utf-8") as fh:
                    logged = fh.read()
                self.assertIn("---- %s screen ----" % moment, logged, logged)
                self.assertNotIn("---- %s screen ----" % moment,
                                result["stdout"])

    def test_a_closed_stream_with_no_answer_is_reported_not_invented(self):
        """EOF before any line arrives (the human's stream closed, nobody
        answered) is named, exactly as _recorded_answer_resolver names "no
        default acceptor"; it is never read as a silent proceed."""
        read_fd, write_fd = os.pipe()
        reader, writer = os.fdopen(read_fd, "r"), os.fdopen(write_fd, "w")
        writer.close()  # closes the only writer: reader.readline() now EOFs
        resolver = _br._interactive_resolver(reader,
                                             prompt_stream=io.StringIO())
        log = _br.RunLog()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            choice = _br._human_moment(log, "intent", self._spec("x", "X"),
                                       resolver=resolver)
        reader.close()
        self.assertIsNone(choice["choice"])
        self.assertIn("closed", choice["by"])


def _fake_multi_unit_loop(repo, plans):
    """A stand-in for one loop_bridge.main() round, mirroring
    test_repair_drain.py's own _fake_run_loop but for more than one unit:
    claims every unit not yet finished through the REAL claim_store (so
    attempt counts and state transitions are authentic) and releases each
    with this round's scripted state, repeating the last entry once a
    unit's own script is exhausted. `plans` is {unit_id: [states...]}."""
    calls = {uid: 0 for uid in plans}
    finished = set()

    def _run(plan_path, claims_path, cwd, slots):
        lines, any_done = [], False
        for uid, states in plans.items():
            if uid in finished:
                continue
            claim, problem = claim_store.acquire(claims_path, uid,
                                                  "test-owner")
            if claim is None:
                continue
            i = calls[uid]
            calls[uid] += 1
            state = states[i] if i < len(states) else states[-1]
            evidence = None
            if state == "done":
                fname = "%s.txt" % uid
                with open(os.path.join(repo, fname), "w",
                         encoding="utf-8") as fh:
                    fh.write("done\n")
                sh(["git", "add", fname], repo)
                sh(["git", "commit", "-q", "-m", "%s lands" % uid], repo)
                rev = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()
                evidence = {"check_command": "true", "exit_code": 0,
                           "output": "ok", "output_truncated": False,
                           "canonical_rev": rev, "files_changed": [fname]}
                any_done = True
                finished.add(uid)
            claim_store.release(claims_path, uid, "test-owner", state=state,
                                evidence=evidence)
            lines.append("  %s %-8s scope=CLEAN integrated=%s"
                         % (uid, state, state == "done"))
        return (0 if any_done else 1), "\n".join(lines)
    return _run


class TheScreenLoomFiresAtAllFourMomentsOfARealRun(unittest.TestCase):
    """The row's own done-check, driven for real (in-process, run_loop
    faked the same lazy way test_repair_drain.py already proved reliable,
    so this stays fast and needs no real worktrees or a real model): one
    plain unit that integrates (acceptance), one unit whose own declared
    scope names a risk class and also integrates (release), and one unit
    that never repairs and exhausts its bound (forcing-condition). intent
    fires on every run by construction. This proves the wiring poses all
    four, with real evidence, in one run, with the recorded default
    resolving intent and forcing-condition; the LIVE-RUN proof this row's
    done-check also asks for (a wired resolver actually driving a choice at
    each pause) is the next test below,
    test_interactive_mode_blocks_for_a_live_answer_then_proceeds, same
    fixture, --interactive on."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="screen-loom-repo-")
        for args in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "a@b.c"],
                    ["config", "user.name", "t"]):
            sh(["git"] + args, self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("base\n")
        sh(["git", "add", "-A"], self.repo)
        sh(["git", "commit", "-q", "-m", "R0"], self.repo)

        self.run_dir = tempfile.mkdtemp(prefix="screen-loom-run-")
        rec, problems = WR.create(
            "a plain piece, a risky piece and a piece that never repairs",
            [{"id": "A1", "title": "create a1", "done_check": "true",
             "owns": ["A1.txt"]},
            {"id": "A2", "title": "delete the abandoned cache file",
             "done_check": "true", "owns": ["A2.txt"]},
            {"id": "F1", "title": "a unit that never repairs",
             "done_check": "true", "owns": ["F1.txt"]}],
            store=self.run_dir)
        self.assertEqual(problems, [])
        self._orig_run_loop = _br.run_loop
        self._orig_stdin = _br.sys.stdin

    def tearDown(self):
        _br.run_loop = self._orig_run_loop
        _br.sys.stdin = self._orig_stdin

    def test_all_four_moments_are_posed_with_the_machinery_kept_in_the_log(self):
        _br.run_loop = _fake_multi_unit_loop(self.repo, {
            "A1": ["done"], "A2": ["done"], "F1": ["failed"]})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _br.main(["ignored", "--resume", self.run_dir, "--cwd",
                             self.repo])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, 1, text)  # F1 never delivers

        run_log_path = os.path.join(self.run_dir, "run.log")
        with open(run_log_path, encoding="utf-8") as fh:
            logged = fh.read()

        for moment in _br.MOMENTS:
            self.assertIn("---- %s screen ----" % moment, logged, logged)
            self.assertNotIn("---- %s screen ----" % moment, text,
                            "the %s screen's machinery reached the chat "
                            "stream" % moment)

        # intent and forcing-condition: no resolver was wired, so the
        # recorded default (the top-ranked option) resolves them at once.
        self.assertIn("brother_run: intent:", text)
        self.assertIn("brother_run: intent resolved:", text)
        self.assertIn("brother_run: forcing-condition:", text)
        self.assertIn("brother_run: forcing-condition resolved:", text)
        # release and acceptance: loom.py's own rule holds, nobody
        # answered either screen in this fixture, so both say so plainly.
        self.assertIn("brother_run: release:", text)
        self.assertIn("brother_run: release: not yet recorded", text)
        self.assertIn("brother_run: acceptance:", text)
        self.assertIn("brother_run: acceptance: not yet recorded", text)

    def test_interactive_mode_blocks_for_a_live_answer_then_proceeds(self):
        """I3's OWED half, closed here: --interactive wires the real
        _interactive_resolver into intent and forcing-condition instead of
        the recorded default, driven through this class's own real run. A
        real os.pipe stands in for sys.stdin so the pause proven here is
        the same block a live terminal's stdin would apply, not a mocked
        one: the run is started on its own thread, proven still alive
        (paused) well past when the sibling test above (no --interactive)
        already finished, then fed one scripted answer per pause, in
        order, exactly as a human typing them in would. Release and
        acceptance are untouched by the flag: loom's own no-default-
        acceptor path still holds for both, exactly as the sibling test
        above."""
        _br.run_loop = _fake_multi_unit_loop(self.repo, {
            "A1": ["done"], "A2": ["done"], "F1": ["failed"]})
        read_fd, write_fd = os.pipe()
        reader, writer = os.fdopen(read_fd, "r"), os.fdopen(write_fd, "w")
        _br.sys.stdin = reader
        result = {}

        def _call():
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
                result["code"] = _br.main(
                    ["ignored", "--resume", self.run_dir, "--cwd", self.repo,
                    "--interactive"])
            result["text"] = out.getvalue() + err.getvalue()

        t = threading.Thread(target=_call)
        t.start()
        # PAUSED at intent: the sibling test above (identical fixture, no
        # --interactive) completes the WHOLE run in well under this
        # window, so a thread still alive here is genuinely blocked, not
        # merely slow.
        t.join(timeout=0.3)
        self.assertTrue(t.is_alive(),
                       "the run resolved intent before a live answer "
                       "arrived; --interactive did not block")
        self.assertNotIn("code", result)

        writer.write("proceed\n")   # THE SCRIPTED ANSWER for intent
        writer.flush()
        t.join(timeout=3)
        self.assertTrue(t.is_alive(),
                       "the run finished before forcing-condition's own "
                       "live answer arrived; F1 never repairs, so a "
                       "second pause is expected")

        writer.write("stop-here\n")  # THE SCRIPTED ANSWER for forcing-condition
        writer.flush()
        writer.close()
        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "the run never returned")
        reader.close()

        text = result["text"]
        self.assertEqual(result["code"], 1, text)  # F1 still never delivers

        run_log_path = os.path.join(self.run_dir, "run.log")
        with open(run_log_path, encoding="utf-8") as fh:
            logged = fh.read()

        for moment in ("intent", "forcing-condition"):
            self.assertIn("---- %s screen ----" % moment, logged, logged)
            self.assertNotIn("---- %s screen ----" % moment, text,
                            "the %s screen's machinery reached the chat "
                            "stream" % moment)
            # EXACTLY ONE ECHO AND ONE PROOF LINE per moment reached the
            # chat stream; the resolver's own prompt went to stderr
            # (prompt_stream defaults there), never into this count.
            self.assertEqual(text.count("brother_run: %s:" % moment), 1,
                            text)
            self.assertEqual(
                text.count("brother_run: %s resolved:" % moment), 1, text)

        # THE LIVE ANSWERS, never the recorded default, resolved intent and
        # forcing-condition; "a human, live" is _interactive_resolver's own
        # `by` string, and the recorded default's own `by` string never
        # appears at all.
        self.assertEqual(text.count("recorded by a human, live"), 2, text)
        self.assertNotIn("recorded default", text)

        # RELEASE AND ACCEPTANCE ARE UNTOUCHED by --interactive: nobody
        # answered either loom screen in this fixture, so both still say
        # so plainly, exactly as the sibling test without --interactive.
        self.assertIn("brother_run: release: not yet recorded", text)
        self.assertIn("brother_run: acceptance: not yet recorded", text)


class AttemptTraceUidIsSanitized(unittest.TestCase):
    """Adversarial security review finding: _write_attempt_trace built
    os.path.join(run_dir, ATTEMPTS_DIRNAME, uid, "attempt-N") straight from
    the RAW unit id, on every run, and the caller never checked it for path
    characters (only non-empty and uniqueness). A hostile id like
    "../../../../tmp/x" escaped run_dir entirely, and _write_attempt_trace's
    own OSError handler swallowed the failure silently. Not only theoretical:
    a real id already in use, "token-shield:docs/reconcile-backlog-2026-09-03"
    (see v3_receipts.py), contains a literal "/" and already broke the
    per-attempt-directory guarantee on ordinary input.

    Driven directly against _write_attempt_trace (a plain function; no need
    to run a whole drain through door.py/loop_bridge.py for a path-safety
    check), proving every uid's trace lands under run_dir via realpath plus
    commonpath, exactly as a hostile-input check should, and that sanitizing
    never collapses two different ids onto one directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-uidsan-")
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)

    def _write_and_locate(self, uid):
        """Writes attempt 1 for uid and returns its attempt directory,
        located the same way a real caller would: via the sanitizer."""
        _br._write_attempt_trace(self.run_dir, uid, 1, {"state": "done"},
                                 "this round's loop output", "tree state text")
        return os.path.join(self.run_dir, "attempts",
                            _br._safe_uid_segment(uid), "attempt-1")

    def _assert_inside_run_dir(self, path, uid):
        # THE ACTUAL DONE-CHECK: os.path.realpath resolves any remaining
        # ".." or symlink before comparing, and os.path.commonpath proves
        # containment rather than a brittle string prefix test.
        real_run_dir = os.path.realpath(self.run_dir)
        real_path = os.path.realpath(path)
        self.assertEqual(
            os.path.commonpath([real_run_dir, real_path]), real_run_dir,
            "uid %r escaped run_dir: %s is not under %s"
            % (uid, real_path, real_run_dir))

    def test_a_uid_with_a_real_slash_stays_inside_run_dir(self):
        uid = "token-shield:docs/reconcile-backlog-2026-09-03"
        attempt_dir = self._write_and_locate(uid)
        self.assertTrue(os.path.isdir(attempt_dir), attempt_dir)
        self._assert_inside_run_dir(attempt_dir, uid)
        # the FILES land where the isdir check says, all three, same as any
        # other unit's trace
        for name in ("claim.json", "engine_output.txt", "tree_state.txt"):
            self.assertTrue(os.path.isfile(os.path.join(attempt_dir, name)),
                            "%s missing for uid %r" % (name, uid))
        # the RECORDED uid inside the files stays the real, unsanitized id;
        # only the path segment was ever touched
        with open(os.path.join(attempt_dir, "engine_output.txt"),
                 encoding="utf-8") as fh:
            self.assertIn(uid, fh.read())

    def test_a_hostile_dotdot_uid_stays_inside_run_dir(self):
        uid = "../../../../tmp/x"
        attempt_dir = self._write_and_locate(uid)
        self.assertTrue(os.path.isdir(attempt_dir), attempt_dir)
        self._assert_inside_run_dir(attempt_dir, uid)
        with open(os.path.join(attempt_dir, "engine_output.txt"),
                 encoding="utf-8") as fh:
            self.assertIn(uid, fh.read())

    def test_two_distinct_ids_do_not_collide_on_the_same_directory(self):
        # worktree_lane.py's own sanitizer (mirrored here) keeps alnum, "-"
        # and "_", and replaces everything else with "-", so "a/b" and
        # "a.b" both reduce to "a-b"; the fix must still tell them apart.
        dir_a = self._write_and_locate("a/b")
        dir_b = self._write_and_locate("a.b")
        self.assertNotEqual(dir_a, dir_b, (dir_a, dir_b))
        self.assertTrue(os.path.isdir(dir_a), dir_a)
        self.assertTrue(os.path.isdir(dir_b), dir_b)


class RefuseBrokenPrecheckUnitsPullsThemFromThePlan(unittest.TestCase):
    """_refuse_broken_precheck_units / _restore_refused_precheck_units: THE
    FIX for the defect measured live 2026-09-03 (a unit _stamp_prechecks had
    already stamped check_looks_broken True was still claimed and
    dispatched; its worker spent up to the full 1200 second timeout twice
    before the caller's own timeout finally hit). Driven directly against a
    real Work document on disk, the same load/mutate/write shape
    _stamp_prechecks itself uses, so this proves the pull and the restore
    each survive the round trip a real run reads them back through."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="refuse-broken-")

    def test_a_broken_row_is_pulled_a_healthy_row_is_kept(self):
        doc = _write_doc(self.tmp, [
            {"id": "B1", "status": "SCHEDULED",
             "done_check": "python3 -c '('", "check_looks_broken": True,
             "check_exit_before": 1},
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(set(refused), {"B1"})
        row, reason = refused["B1"]
        self.assertEqual(row["id"], "B1")
        self.assertIn("exit 1 before the work", reason)
        self.assertIn("fix the check and run again", reason)
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        # NEVER CLAIMED: the document a scheduler would read next no longer
        # names B1 at all.
        self.assertEqual([r["id"] for r in saved["rows"]], ["A1"])

    def test_a_done_row_flagged_broken_is_left_alone(self):
        """A stale check_looks_broken flag on an already-DONE row (left
        over from before a later run finished it for real) must never be
        pulled: _stamp_prechecks itself never re-stamps a DONE row, and
        pulling one here would remove real, delivered work from the plan."""
        doc = _write_doc(self.tmp, [
            {"id": "D1", "status": "DONE", "done_check": "true",
             "check_looks_broken": True},
        ])
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(refused, {})
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual([r["id"] for r in saved["rows"]], ["D1"])

    def test_nothing_broken_leaves_the_document_byte_for_byte_untouched(self):
        doc = _write_doc(self.tmp, [
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        with open(doc, encoding="utf-8") as fh:
            before = fh.read()
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(refused, {})
        with open(doc, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_restore_puts_the_row_back_at_its_original_position(self):
        doc = _write_doc(self.tmp, [
            {"id": "B1", "status": "SCHEDULED",
             "done_check": "python3 -c '('", "check_looks_broken": True,
             "check_exit_before": 1},
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        order = ["B1", "A1"]
        refused = _br._refuse_broken_precheck_units(doc)
        _br._restore_refused_precheck_units(doc, refused, order)
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual([r["id"] for r in saved["rows"]], ["B1", "A1"])
        self.assertIn("integration_refused", saved["rows"][0])

    def test_restore_of_nothing_refused_is_a_no_op(self):
        doc = _write_doc(self.tmp, [
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        with open(doc, encoding="utf-8") as fh:
            before = fh.read()
        _br._restore_refused_precheck_units(doc, {}, ["A1"])
        with open(doc, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)


class RewriteBrokenChecksAsksThePlannerOnce(unittest.TestCase):
    """_rewrite_broken_checks: THE FIX, the second follow-through (rule 4,
    2026-09-03). Runs strictly between _stamp_prechecks and
    _refuse_broken_precheck_units, driven directly against a real Work
    document on disk, the same load/mutate/write shape both of those already
    use, with the planner stubbed exactly the way test_door.py stubs it
    (--model-cmd / here, the equivalent `model_cmd` argument, pointed at a
    tiny script reading the whole prompt off stdin)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rewrite-check-")
        self.checkcwd = tempfile.mkdtemp(prefix="rewrite-check-cwd-")
        self.log = _br.RunLog()
        self.log.to(self.tmp)

    def _doc(self, stderr_before="python3: SyntaxError: unexpected EOF "
                                  "while parsing"):
        return _write_doc(self.tmp, [
            {"id": "B1", "status": "SCHEDULED", "objective": "fix the thing",
             "done_check": "python3 -c 'this is not python('",
             "check_looks_broken": True, "check_exit_before": 1,
             "check_stderr_before": stderr_before},
        ])

    def test_a_broken_check_gets_one_rewrite_request_carrying_the_stderr(self):
        log_path = os.path.join(self.tmp, "invocations.log")
        stub = write_stub(self.tmp, "rewriter.py", """
            import json, os, sys
            prompt = sys.stdin.read()
            with open(%r, "a") as fh:
                fh.write("---\\n" + prompt)
            print(json.dumps({"done_check": "test -f fixed.txt"}))
        """ % log_path)
        doc = self._doc()
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="%s %s" % (sys.executable, stub))
        with open(log_path, encoding="utf-8") as fh:
            captured = fh.read()
        # ASKED EXACTLY ONCE for this unit.
        self.assertEqual(captured.count("---"), 1, captured)
        # CARRYING THE STDERR: the exact text _stamp_prechecks captured.
        self.assertIn("SyntaxError", captured, captured)
        self.assertIn("B1", rows[0]["id"])

    def test_a_runnable_replacement_is_adopted_and_the_unit_proceeds(self):
        stub = write_stub(self.tmp, "rewriter_ok.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps({"done_check": "test -f fixed.txt"}))
        """)
        doc = self._doc()
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="%s %s" % (sys.executable, stub))
        row = rows[0]
        self.assertTrue(row["check_rewritten"])
        self.assertEqual(row["check_original"],
                         "python3 -c 'this is not python('")
        self.assertEqual(row["done_check"], "test -f fixed.txt")
        # RE-STAMPED: a fresh _check_passes_now against the untouched cwd.
        # "test -f fixed.txt" fails cleanly (no such file yet), which is
        # neither broken nor already-true, exactly what a unit whose work
        # has not happened yet should show.
        self.assertFalse(row["check_looks_broken"])
        self.assertIs(row["check_passed_before"], False)
        # PROCEEDS: _refuse_broken_precheck_units no longer pulls it.
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(refused, {})
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual([r["id"] for r in saved["rows"]], ["B1"])

    def test_a_still_broken_replacement_is_refused_as_before(self):
        stub = write_stub(self.tmp, "rewriter_still_broken.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps(
                {"done_check": "python3 -c 'still not python('"}))
        """)
        doc = self._doc()
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="%s %s" % (sys.executable, stub))
        row = rows[0]
        self.assertTrue(row["check_rewritten"])
        self.assertEqual(row["check_original"],
                         "python3 -c 'this is not python('")
        self.assertTrue(row["check_looks_broken"])
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(set(refused), {"B1"})
        _row, reason = refused["B1"]
        # SAME REFUSAL WORDING as the no-rewrite path.
        self.assertIn("fix the check and run again", reason)

    def test_a_parse_failure_keeps_the_original_check_and_refuses(self):
        stub = write_stub(self.tmp, "rewriter_bad_json.py", """
            import sys
            sys.stdin.read()
            print("not json at all")
        """)
        doc = self._doc()
        original = ("python3 -c 'this is not python('")
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="%s %s" % (sys.executable, stub))
        row = rows[0]
        self.assertEqual(row["done_check"], original)
        self.assertNotIn("check_rewritten", row)
        self.assertTrue(row["check_looks_broken"])
        refused = _br._refuse_broken_precheck_units(doc)
        self.assertEqual(set(refused), {"B1"})
        _row, reason = refused["B1"]
        self.assertIn("fix the check and run again", reason)

    def test_the_planner_is_never_asked_twice_for_one_unit(self):
        log_path = os.path.join(self.tmp, "count.log")
        # Always refuses (bad JSON), the case most likely to tempt a second
        # ask; still only ever invoked once.
        stub = write_stub(self.tmp, "rewriter_counting.py", """
            import sys
            with open(%r, "a") as fh:
                fh.write("x")
            sys.stdin.read()
            print("still not json")
        """ % log_path)
        doc = self._doc()
        _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="%s %s" % (sys.executable, stub))
        with open(log_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "x")

    def test_no_decomposer_available_leaves_broken_rows_untouched(self):
        doc = self._doc()
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="/no/such/decomposer/binary --flag")
        self.assertNotIn("check_rewritten", rows[0])
        self.assertEqual(rows[0]["done_check"],
                         "python3 -c 'this is not python('")

    def test_a_done_row_flagged_broken_is_never_asked_about(self):
        doc = _write_doc(self.tmp, [
            {"id": "D1", "status": "DONE", "done_check": "true",
             "check_looks_broken": True},
        ])
        rows = _br._rewrite_broken_checks(
            doc, self.checkcwd, self.log,
            model_cmd="/no/such/decomposer/binary --flag")
        self.assertNotIn("check_rewritten", rows[0])


class CheckRewritePromptCarriesTheNewRequirements(unittest.TestCase):
    """door.py's own prompts, both of them, must state the one-line and
    fail-before-pass-after requirements: the first attempt should more often
    be right, and the single-unit retry must ask for the same shape."""

    def test_the_original_decomposition_prompt_states_both_requirements(self):
        prompt = door.build_prompt("an outcome")
        self.assertIn("ONE LINE", prompt)
        self.assertIn("FAIL", prompt)
        self.assertIn("PASS once the work is done", prompt)

    def test_the_rewrite_prompt_carries_the_stderr_and_the_requirements(self):
        prompt = door.build_check_rewrite_prompt(
            "fix the thing", "python3 -c 'broken('", "SyntaxError: boom")
        self.assertIn("SyntaxError: boom", prompt)
        self.assertIn("ONE LINE", prompt)
        self.assertIn("FAIL", prompt)
        self.assertIn('{"done_check"', prompt)


# A "model" that logs every write scope it was asked to fill into a shared
# invocation log (WORKER_INVOCATION_LOG) before writing the declared files,
# same shape as WRITER_MODEL above: standing in for `claude -p`, but with a
# durable trace of every unit it was actually invoked for, which is exactly
# what "no worker was ever invoked" needs to prove itself against, rather
# than inferring it from a file's absence alone.
INVOCATION_LOGGING_MODEL = """
    import os, re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    scope = m.group(1) if m else "(nothing declared)"
    log_path = os.environ.get("WORKER_INVOCATION_LOG")
    if log_path:
        with open(log_path, "a") as fh:
            fh.write(scope + "\\n")
    for path in (p.strip() for p in (scope.split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % scope)
"""


class BrokenCheckUnitsAreRefusedBeforeAnyWorker(unittest.TestCase):
    """THE FIX, end to end: the defect measured live 2026-09-03, where a
    unit whose done_check was a syntax error (a python -c string with a
    literal backslash-n) still got claimed and dispatched after
    _stamp_prechecks had already marked it unable to prove anything. B1's
    check is that exact shape. A1 is an ordinary sibling; C1's check is
    already true before any work (the OTHER, unrelated precheck finding),
    included to prove this fix touches only a broken check, never an
    already-passing one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-broken-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "B1", "objective": "a unit whose check is broken",
                 "done_check": "python3 -c 'this is not python('",
                 "writes": ["broken.txt"], "deps": []},
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "C1", "objective": "a check already true before work",
                 "done_check": "test -f base.txt", "writes": ["c1.txt"],
                 "deps": []},
            ]))
        """)
        self.model = write_stub(self.tmp, "invocation_logging_model.py",
                                INVOCATION_LOGGING_MODEL)
        self.log_path = os.path.join(self.tmp, "worker_invocations.log")
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)
        self.env["WORKER_INVOCATION_LOG"] = self.log_path

    def _invocations(self):
        if not os.path.exists(self.log_path):
            return ""
        with open(self.log_path, encoding="utf-8") as fh:
            return fh.read()

    def test_broken_unit_refused_before_claim_sibling_integrates(self):
        proc = sh([sys.executable, BROTHER_RUN, "three units, one broken",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr

        # NEVER CLAIMED: no worker was ever invoked with B1's write scope,
        # and the file it would have written was never created.
        self.assertNotIn("broken.txt", self._invocations(), out)
        self.assertFalse(
            os.path.exists(os.path.join(self.repo, "broken.txt")), out)

        # THE REPORT: B1 lists under refused, with this fix's own reason.
        self.assertIn("refused (1):", out, out)
        self.assertIn("fix the check and run again", out, out)

        # THE RECEIPT: B1's own line reads refused, verdict FAIL.
        line = next(l for l in out.splitlines()
                    if l.strip().startswith("B1 was refused"))
        self.assertIn("verdict: FAIL", line, out)

        # THE SIBLING: A1 was actually claimed, ran, and integrated.
        self.assertIn("one.txt", self._invocations(), out)
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, "one.txt")), out)
        self.assertIn("A1", out, out)

    def test_an_already_true_check_is_still_dispatched_and_reads_no_data(self):
        """Not this fix's own finding (that is check_passed_before, landed
        earlier the same night), but the fix must never over-reach onto it:
        C1's check already passes before any work, and the existing rule
        for that (dispatch it, then read its receipt as NO-DATA rather than
        PASS) must survive unchanged."""
        proc = sh([sys.executable, BROTHER_RUN, "three units, one broken",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        # STILL DISPATCHED, unlike B1: the worker really ran for C1.
        self.assertIn("c1.txt", self._invocations(), out)
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, "c1.txt")), out)
        line = next(l for l in out.splitlines() if "C1 is NO-DATA" in l)
        self.assertIn("verdict: NO-DATA", line, out)


class ARewrittenCheckLetsTheUnitProceedEndToEnd(unittest.TestCase):
    """THE FIX, end to end: a decomposer that first writes a broken
    done_check, then, asked once more with the captured stderr, writes a
    runnable replacement, must see its unit actually claimed, run and
    integrated, never refused. The stub decomposer answers the two prompt
    shapes differently, exactly as door.build_prompt and
    door.build_check_rewrite_prompt differ for a real model."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-rewrite-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "two_shape_decomposer.py", """
            import json, sys
            prompt = sys.stdin.read()
            if "done_check that cannot run at all" in prompt:
                print(json.dumps({"done_check": "test -f fixed.txt"}))
            else:
                print(json.dumps([
                    {"id": "B1", "objective": "a unit whose check is broken",
                     "done_check": "python3 -c 'this is not python('",
                     "writes": ["fixed.txt"], "deps": []},
                ]))
        """)
        self.model = write_stub(self.tmp, "writer_model.py", WRITER_MODEL)
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)

    def test_the_rewritten_unit_is_claimed_run_and_integrated(self):
        proc = sh([sys.executable, BROTHER_RUN, "one unit, a broken check "
                  "the planner then fixes", "--cwd", self.repo,
                  "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, "fixed.txt")), out)
        self.assertIn("integrated (1):", out, out)
        self.assertIn("B1", out, out)
        self.assertNotIn("refused (1):", out, out)


# A "model" that answers in the claude CLI's own --output-format json shape
# (a "result" string beside a "usage" object with the CLI's field names,
# measured live 2026-09-03), and writes its declared files first so the unit
# really integrates. This is the seam model_worker._parse_model_output reads
# real usage from; a plain-text model (WRITER_MODEL above) is the other way.
USAGE_MODEL = """
    import json, re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the usage stub\\n")
    print(json.dumps({"result": "wrote %s" % (m.group(1) if m else ""),
                      "usage": {"input_tokens": 30, "output_tokens": 7,
                                "cache_read_input_tokens": 12,
                                "cache_creation_input_tokens": 5}}))
"""

# A decomposer that records every time it is asked, so a test can prove the
# door was NEVER asked (a refusal before the door) rather than merely that
# no plan came out of it.
LOGGING_DECOMPOSER = """
    import json, os, sys
    sys.stdin.read()
    with open(os.environ["DECOMPOSER_LOG"], "a") as fh:
        fh.write("asked\\n")
    print(json.dumps([
        {"id": "A1", "objective": "create file one",
         "done_check": "test -f one.txt", "writes": ["one.txt"],
         "deps": []},
    ]))
"""


def _hub_head():
    return sh(["git", "rev-parse", "HEAD"], cwd=_br.REPO_ROOT).stdout.strip()


def _only_run_dir(runs_root):
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    names = os.listdir(runs_dir) if os.path.isdir(runs_dir) else []
    return os.path.join(runs_dir, names[0]) if len(names) == 1 else None


class TheDirtyTreeIsRefusedBeforeAnyClaim(unittest.TestCase):
    """Finding 4 of the toy's run record (2026-09-03): the clean-tree
    prerequisite stood in prose while integrate.py enforced it only at
    merge time, after every worker had spent its attempts. The engine now
    checks it once the door has said what the run writes and before
    anything is claimed, by integrate.py's own rule (bytecode never
    counts). TWO KINDS OF DIRT (battery round 9, product-acceptance area 6):
    dirt inside the run's write set is refused in one line naming the count
    and the first three paths with their owning unit; dirt outside it (an
    unrelated uncommitted edit) is named and left untouched while the run
    proceeds, integration still refusing to merge over it, so the person can
    commit their edit and resume. The first version of this class refused
    every dirty tree before the door; that was the too-broad rule."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-dirty-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py",
                                     LOGGING_DECOMPOSER)
        self.model = write_stub(self.tmp, "writer_model.py", WRITER_MODEL)
        self.decomposer_log = os.path.join(self.tmp, "decomposer.log")
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)
        self.env["DECOMPOSER_LOG"] = self.decomposer_log
        # Bytecode in both cases: the exemption must hold on the refusing
        # run (never counted, never named) and on the clean run (never a
        # reason to refuse).
        os.makedirs(os.path.join(self.repo, "__pycache__"))
        with open(os.path.join(self.repo, "__pycache__", "x.pyc"), "wb") as fh:
            fh.write(b"\x00")

    def _run(self):
        proc = sh([sys.executable, BROTHER_RUN, "one file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        return proc, proc.stdout + proc.stderr

    def test_dirt_inside_the_write_set_is_refused_before_any_claim(self):
        # one.txt is what the unit A1 owns; an uncommitted one.txt already
        # sitting there is exactly the work a merge would bury.
        with open(os.path.join(self.repo, "one.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("somebody's uncommitted one.txt\n")
        for name in ("u1.txt", "u2.txt", "u3.txt"):
            with open(os.path.join(self.repo, name), "w",
                     encoding="utf-8") as fh:
                fh.write("stray\n")
        proc, out = self._run()
        self.assertEqual(proc.returncode, 1, out)
        line = next((l for l in out.splitlines()
                     if "is dirty inside this run's write set" in l), "")
        self.assertIn("1 uncommitted path(s) a unit owns "
                      "(one.txt (owned by A1))", line, out)
        self.assertNotIn("u1.txt", line, line)     # unrelated dirt not named
        self.assertNotIn("__pycache__", line, line)
        self.assertIn("nothing was claimed or run", line, line)
        # AFTER THE DOOR (the write set comes from it), BEFORE ANY CLAIM: a
        # run directory exists, the claim store was never written, no
        # worker ran, and the uncommitted file is byte for byte intact.
        self.assertTrue(os.path.exists(self.decomposer_log), out)
        run_dir = _only_run_dir(self.tmp)
        self.assertIsNotNone(run_dir, out)
        self.assertFalse(os.path.isfile(os.path.join(run_dir, "claims.json")))
        with open(os.path.join(self.repo, "one.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "somebody's uncommitted one.txt\n")

    def test_unrelated_dirt_proceeds_and_the_edit_survives(self):
        """The acceptance harness's area 6 shape: an uncommitted edit to a
        file no unit owns. The run proceeds, integration refuses to merge
        over the dirty tree by name, and the edit and HEAD are untouched."""
        with open(os.path.join(self.repo, "base.txt"), "a",
                 encoding="utf-8") as fh:
            fh.write("unrelated, uncommitted, must survive\n")
        head_before = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout
        proc, out = self._run()
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("is dirty on 1 uncommitted path(s) outside this run's "
                      "write set (base.txt); they are left untouched", out, out)
        self.assertNotIn("nothing was claimed or run", out, out)
        self.assertIn("dirty", out.lower(), out)
        self.assertIn("refused (1):", out, out)
        with open(os.path.join(self.repo, "base.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(),
                             "base\nunrelated, uncommitted, must survive\n")
        self.assertEqual(sh(["git", "rev-parse", "HEAD"],
                            cwd=self.repo).stdout, head_before)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "one.txt")))

    def test_bytecode_alone_is_not_dirty_and_the_run_proceeds(self):
        proc, out = self._run()
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("is not clean", out, out)
        self.assertIn("integrated (1):", out, out)
        self.assertTrue(os.path.exists(self.decomposer_log), out)

    def test_the_refusal_helper_reads_integrates_own_rule(self):
        """Unit level, both ways, no model: dirty_paths is the same rule
        integrate.py refuses a merge by, so the door and the merge can never
        disagree about what clean means; the write-set overlap decides
        refusal against notice."""
        rows = [{"id": "A1", "status": "SCHEDULED", "owns": ["other.txt"]}]
        self.assertEqual(_br._dirty_tree_lines(self.repo, rows), ("", ""))
        with open(os.path.join(self.repo, "stray.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("x\n")
        refusal, notice = _br._dirty_tree_lines(self.repo, rows)
        self.assertEqual(refusal, "")
        self.assertIn("1 uncommitted path(s) outside this run's write set "
                      "(stray.txt)", notice)
        owning = [{"id": "A1", "status": "SCHEDULED", "owns": ["stray.txt"]}]
        refusal, notice = _br._dirty_tree_lines(self.repo, owning)
        self.assertIn("(stray.txt (owned by A1))", refusal)
        self.assertEqual(notice, "")
        # A DONE row's paths are not this run's write set anymore.
        done = [{"id": "A1", "status": "DONE", "owns": ["stray.txt"]}]
        self.assertEqual(_br._dirty_tree_lines(self.repo, done)[0], "")
        # Overlap reads directories both ways, trailing slash or not.
        self.assertTrue(_br._path_overlaps("pkg/x.txt", "pkg"))
        self.assertTrue(_br._path_overlaps("pkg/", "pkg/x.txt"))
        self.assertFalse(_br._path_overlaps("pkgs/x.txt", "pkg"))
        not_a_repo = tempfile.mkdtemp(prefix="not-a-repo-")
        self.assertIn("git status could not read",
                      _br._dirty_tree_lines(not_a_repo, rows)[0])


class RealUsageReachesTheCostBlock(unittest.TestCase):
    """Finding 1 of the toy's run record (2026-09-03): every token field
    read NO-DATA on a genuine success. The chain already existed
    (model_worker reads the CLI's usage, the adapter forwards it,
    loop_bridge writes a sidecar, _merge_usage_sidecar folds it in) and
    broke at adapter selection: loop_bridge.load_parts prefers the
    installed plugin cache, whose brothermode 3.4.4 adapter predates the
    passthrough, over this checkout's own products tree. run_loop now hands
    loop_bridge the source adapter when it sits beside the engine
    (_source_tools_dir), and a run that still records nothing says why in
    the adapter's own terms."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-usage-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "A2", "objective": "create file two",
                 "done_check": "test -f two.txt", "writes": ["two.txt"],
                 "deps": []},
            ]))
        """)
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)

    def _run(self, model_body, name):
        model = write_stub(self.tmp, name, model_body)
        env = dict(self.env)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        return proc, proc.stdout + proc.stderr

    def test_the_source_adapter_is_the_one_beside_this_engine(self):
        tools = _br._source_tools_dir()
        self.assertIsNotNone(tools)
        self.assertEqual(os.path.dirname(os.path.dirname(tools)),
                         os.path.join(_br.REPO_ROOT, "products"))
        parts, why = _br.loop_bridge.load_parts(tools)
        self.assertIsNotNone(parts, why)
        self.assertTrue(hasattr(parts["spawn"], "USAGE_FIELDS"))
        # A bundle has no products tree beside it: None, never a guess.
        self.assertIsNone(_br._source_tools_dir(tempfile.mkdtemp()))
        # loop_bridge's own override outranks the source preference: the
        # acceptance harness's area 5 points it at a five-second adapter to
        # reap a hung worker, and this must not put the 900 second one back.
        self.assertIsNone(_br._source_tools_dir(
            env={_br.loop_bridge.RUNTIME_ENV_VAR: "/some/runtime"}))
        self.assertEqual(_br._source_tools_dir(
            env={_br.loop_bridge.RUNTIME_ENV_VAR: "  "}), tools)

    def test_the_workers_real_usage_is_summed_end_to_end(self):
        proc, out = self._run(USAGE_MODEL, "usage_model.py")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        # 30 + 30, 7 + 7, 12 + 12 across the two units; the rate is a share.
        self.assertIn("tokens_in: 60", out, out)
        self.assertIn("tokens_out: 14", out, out)
        self.assertIn("tokens_cached: 24", out, out)
        self.assertIn("cache_hit_rate: 0.4", out, out)
        run_dir = _only_run_dir(self.tmp)
        self.assertTrue(os.path.isfile(
            os.path.join(run_dir, "claims_usage.json")), out)
        # FINDING 5 ON THE SAME RUN: the bound is on the intent screen (the
        # run log holds the rendered screen) and on the chat surface,
        # beside the attempt cap, read from the loaded adapter.
        with open(os.path.join(run_dir, _br.LOG_FILENAME),
                  encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn("at most %d attempt(s)" % _br.MAX_UNIT_ATTEMPTS, log)
        self.assertIn("stopped after %d seconds" % _br.WORKER_TIME_LIMIT_SECONDS,
                      log)
        self.assertIn("Each piece gets at most %d attempt(s), and one "
                      "attempt's worker is stopped after %d seconds."
                      % (_br.MAX_UNIT_ATTEMPTS, _br.WORKER_TIME_LIMIT_SECONDS),
                      out, out)
        # FINDING 2'S FRESH HALF ON THE SAME RUN: the creator is stamped on
        # disk, the receipts name it, and nothing says resumed.
        with open(_br._find_work_doc(run_dir), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc.get("harness_revision"), _hub_head(), doc)
        self.assertNotIn("harness_revision_resumed", doc)
        self.assertIn("harness %s." % _hub_head()[:12], out, out)
        self.assertNotIn("Resumed by harness", out, out)

    def test_a_plain_text_model_reads_no_data_naming_the_adapter(self):
        proc, out = self._run(WRITER_MODEL, "writer_model.py")
        self.assertEqual(proc.returncode, 0, out)
        line = next(l for l in out.splitlines()
                    if l.strip().startswith("tokens_in:"))
        self.assertIn("NO-DATA: no worker in this run recorded tokens_in; "
                      "the adapter at ", line, out)
        self.assertIn("forwards usage, so the worker's own answer carried "
                      "none", line, out)
        self.assertNotIn("claims_usage.json",
                         os.listdir(_only_run_dir(self.tmp)))

    def test_the_gap_reason_names_an_adapter_without_the_passthrough(self):
        class _OldAdapter(object):
            __file__ = "/somewhere/3.4.4/tools/bm_worker_spawn.py"
        reason = _br._usage_gap_reason(_OldAdapter())
        self.assertIn("predates the usage passthrough", reason)
        self.assertIn("/somewhere/3.4.4/tools/bm_worker_spawn.py", reason)
        self.assertIn("no worker adapter could be loaded",
                      _br._usage_gap_reason(None))
        block = _br.build_cost_block({"U1": {"attempt": 1}}, [], "", 1.0,
                                     "v1", "deadbeef", usage_gap=reason)
        for field in ("tokens_in", "tokens_out", "tokens_cached",
                      "cache_hit_rate"):
            self.assertTrue(str(block[field]).startswith(_br.NODATA))
            self.assertIn("predates the usage passthrough", str(block[field]))

    def test_a_rate_above_one_is_refused_with_the_measured_reason(self):
        """The live CLI's own numbers (2026-09-03): input_tokens 2 beside
        cache_read_input_tokens 22972. Cached over tokens_in would print
        11486.0; the block refuses with the reason instead, and keeps the
        two real counts."""
        claims = {"U1": {"attempt": 1, "usage": {"tokens_in": 2,
                                                 "tokens_out": 4,
                                                 "tokens_cached": 22972}}}
        block = _br.build_cost_block(claims, [], "", 6.3, "v1", "deadbeef")
        self.assertEqual(block["tokens_in"], 2)
        self.assertEqual(block["tokens_cached"], 22972)
        rate = str(block["cache_hit_rate"])
        self.assertTrue(rate.startswith(_br.NODATA), rate)
        self.assertIn("tokens_cached (22972) exceeds tokens_in (2)", rate)
        self.assertIn("cache-creation count", rate)


class TheUsageSidecarDoesNotHideTheWorkDocument(unittest.TestCase):
    """Found by the end to end usage test above on its first run: the
    sidecar claims_usage.json is a third *.json in the run directory, and
    _find_work_doc (which --resume, --continue and find_unfinished_runs all
    read the document through) took "one json that is neither claims nor
    target" literally, so the first run that ever recorded usage could not
    be resumed. Driven both ways: the sidecar is bookkeeping; a real fourth
    json still makes the directory ambiguous."""

    def test_the_sidecar_is_bookkeeping_a_stranger_json_is_not(self):
        run_dir = tempfile.mkdtemp(prefix="sidecar-doc-")
        doc = os.path.join(run_dir, "W-1.json")
        for name in ("W-1.json", "claims.json", "target.json",
                     "claims_usage.json"):
            with open(os.path.join(run_dir, name), "w",
                     encoding="utf-8") as fh:
                fh.write("{}")
        self.assertEqual(_br._find_work_doc(run_dir), doc)
        self.assertEqual(
            os.path.basename(_br.loop_bridge.usage_sidecar_path(
                os.path.join(run_dir, "claims.json"))), "claims_usage.json")
        with open(os.path.join(run_dir, "stranger.json"), "w",
                 encoding="utf-8") as fh:
            fh.write("{}")
        self.assertIsNone(_br._find_work_doc(run_dir))


class TheIntentScreenNamesTheTimeBound(unittest.TestCase):
    """Finding 5 of the toy's run record (2026-09-03): the per-attempt time
    limit was a constant found in the code. It is now WORKER_TIME_LIMIT_
    SECONDS beside MAX_UNIT_ATTEMPTS, and what the intent screen prints is
    read from the loaded adapter (the module that enforces it), so the
    constant can never silently disagree with the real bound. The end to
    end print is asserted in RealUsageReachesTheCostBlock above."""

    def test_the_constant_matches_the_adapter_that_enforces_it(self):
        parts, why = _br.loop_bridge.load_parts(_br._source_tools_dir())
        self.assertIsNotNone(parts, why)
        self.assertEqual(parts["spawn"].DEFAULT_TIMEOUT_SECONDS,
                         _br.WORKER_TIME_LIMIT_SECONDS)
        self.assertEqual(_br._worker_time_limit(parts["spawn"]),
                         (_br.WORKER_TIME_LIMIT_SECONDS, ""))

    def test_a_disagreeing_adapter_is_printed_with_its_own_value_and_named(self):
        class _Adapter(object):
            DEFAULT_TIMEOUT_SECONDS = 42
        seconds, note = _br._worker_time_limit(_Adapter())
        self.assertEqual(seconds, 42)
        self.assertIn("differs from this engine's WORKER_TIME_LIMIT_SECONDS "
                      "of %d" % _br.WORKER_TIME_LIMIT_SECONDS, note)

    def test_no_adapter_prints_the_constant_with_a_no_data_note(self):
        seconds, note = _br._worker_time_limit(None)
        self.assertEqual(seconds, _br.WORKER_TIME_LIMIT_SECONDS)
        self.assertTrue(note.startswith(_br.NODATA), note)


class AResumedRunRerunsAnAbandonedClaimUpToTheBound(unittest.TestCase):
    """Finding 2 of the toy's run record (2026-09-03), end to end through
    the real loop_bridge and claim store on a real repository: a resumed
    run whose claim store holds an ABANDONED claim (state claimed, lease
    expired, owner gone) re-runs that unit, attempt count carried over,
    and stamps the resuming engine beside the creating one so a receipt
    names both; the same run refuses the unit before any worker starts
    once MAX_UNIT_ATTEMPTS were already spent."""

    CREATOR = "c" * 40

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-resume-")
        self.repo = make_repo(self.tmp)
        self.run_dir = os.path.join(self.tmp, "run")
        rec, problems = WR.create(
            "a1 exists", [{"id": "A1", "title": "create a1",
                           "done_check": "test -f A1.txt",
                           "owns": ["A1.txt"]}],
            store=self.run_dir)
        self.assertEqual(problems, [])
        self.doc_path = _br._find_work_doc(self.run_dir)
        # A record created by an earlier engine: its creator stamp is what
        # the resumed run must leave untouched.
        with open(self.doc_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["harness_revision"] = self.CREATOR
        with open(self.doc_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
        _br._write_run_target(self.run_dir, self.repo)
        self.claims_path = os.path.join(self.run_dir, "claims.json")
        self.model = write_stub(self.tmp, "invocation_logging_model.py",
                                INVOCATION_LOGGING_MODEL)
        self.log_path = os.path.join(self.tmp, "worker_invocations.log")
        self.env = dict(os.environ)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)
        self.env["WORKER_INVOCATION_LOG"] = self.log_path

    def _abandon(self, attempt):
        """A claim taken 5000 seconds ago by an owner that never released
        it: expired by the lease alone, whatever pid the store recorded."""
        claim, problem = claim_store.acquire(
            self.claims_path, "A1", "dead-owner", attempt=attempt,
            clock=lambda: time.time() - 5000)
        self.assertEqual(problem, "", problem)
        found, why = claim_store.reconcile(self.claims_path)
        self.assertEqual([f["status"] for f in found], ["abandoned"], found)

    def _invocations(self):
        if not os.path.exists(self.log_path):
            return ""
        with open(self.log_path, encoding="utf-8") as fh:
            return fh.read()

    def _run(self):
        proc = sh([sys.executable, BROTHER_RUN, "--resume", self.run_dir,
                  "--cwd", self.repo], env=self.env)
        return proc, proc.stdout + proc.stderr

    def _claims(self):
        with open(self.claims_path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_an_abandoned_claim_under_the_bound_is_rerun_and_both_engines_named(self):
        self._abandon(attempt=1)
        proc, out = self._run()
        self.assertEqual(proc.returncode, 0, out)
        # RE-RUN: the worker was really invoked for A1 and the unit landed.
        self.assertIn("A1.txt", self._invocations(), out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "A1.txt")), out)
        self.assertIn("integrated (1):", out, out)
        claim = self._claims()["A1"]
        self.assertEqual(claim["attempt"], 2, claim)       # carried over
        self.assertEqual(claim["reclaimed_from"], "dead-owner", claim)
        self.assertEqual(claim["state"], "done", claim)
        # BOTH ENGINES: the creator untouched on disk, the resumer beside it.
        with open(self.doc_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["harness_revision"], self.CREATOR, doc)
        self.assertEqual(doc["harness_revision_resumed"], _hub_head(), doc)
        self.assertIn("resumed under harness %s; the record was created "
                      "under harness %s" % (_hub_head()[:12], "c" * 12),
                      out, out)
        line = next(l for l in out.splitlines()
                    if l.strip().startswith("A1 delivered"))
        self.assertIn("harness %s." % ("c" * 12), line, out)
        self.assertIn("Resumed by harness %s." % _hub_head()[:12], line, out)
        self.assertIn("verdict: PASS", line, out)

    def test_an_abandoned_claim_at_the_bound_is_refused_before_any_worker(self):
        self._abandon(attempt=_br.MAX_UNIT_ATTEMPTS)
        proc, out = self._run()
        self.assertEqual(proc.returncode, 1, out)
        self.assertEqual(self._invocations(), "", out)      # never a worker
        self.assertFalse(os.path.exists(os.path.join(self.repo, "A1.txt")))
        self.assertIn("refusing A1 before any worker starts: it was already "
                      "given %d attempt(s)" % _br.MAX_UNIT_ATTEMPTS, out, out)
        self.assertIn("the retry budget of %d is spent"
                      % _br.MAX_UNIT_ATTEMPTS, out, out)
        # FINDING 3 ON THE SAME RUN: one line, no empty rounds, no empty
        # verified section, exit 1 kept.
        self.assertIn("nothing was claimed or run: all 1 remaining piece(s) "
                      "were refused before any worker started", out, out)
        self.assertIn("nothing was verified: all 1 piece(s) were refused "
                      "before any worker started", out, out)
        self.assertNotIn("integrated (0):", out, out)
        self.assertNotIn("round 1 done", out, out)
        self.assertNotIn("waiting on", out, out)
        claim = self._claims()["A1"]
        self.assertEqual(claim["attempt"], _br.MAX_UNIT_ATTEMPTS, claim)
        self.assertEqual(claim["state"], "claimed", claim)   # untouched


class RefuseExhaustedUnitsPullsOnlySpentClaims(unittest.TestCase):
    """_refuse_exhausted_units and _stamp_harness at the unit level, the
    same on-disk load/mutate/write shape the drain reads back through."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exhausted-")
        self.claims = os.path.join(self.tmp, "claims.json")

    def _claims(self, **per_unit):
        with open(self.claims, "w", encoding="utf-8") as fh:
            json.dump({uid: {"attempt": attempt, "state": state}
                       for uid, (attempt, state) in per_unit.items()}, fh)

    def test_a_spent_abandoned_claim_is_pulled_a_live_one_is_kept(self):
        """S1 is abandoned at the bound (pulled); K1 is under it (kept); F1
        is a RELEASED failure at the bound (kept: the acceptance harness's
        area 6 resumes exactly such a unit after committing the edit
        integration refused over, and it must land); D1 is done."""
        doc = _write_doc(self.tmp, [
            {"id": "S1", "status": "SCHEDULED", "done_check": "true"},
            {"id": "K1", "status": "SCHEDULED", "done_check": "true"},
            {"id": "F1", "status": "SCHEDULED", "done_check": "true"},
            {"id": "D1", "status": "DONE", "done_check": "true"},
        ])
        self._claims(S1=(_br.MAX_UNIT_ATTEMPTS, "claimed"),
                     K1=(_br.MAX_UNIT_ATTEMPTS - 1, "failed"),
                     F1=(_br.MAX_UNIT_ATTEMPTS, "failed"),
                     D1=(_br.MAX_UNIT_ATTEMPTS, "done"))
        refused = _br._refuse_exhausted_units(doc, self.claims)
        self.assertEqual(set(refused), {"S1"})
        row, reason = refused["S1"]
        self.assertIn("already given %d attempt(s)" % _br.MAX_UNIT_ATTEMPTS,
                      reason)
        self.assertTrue(row.get("refused_before_work"))
        self.assertEqual(row.get("integration_refused"), reason)
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual([r["id"] for r in saved["rows"]], ["K1", "F1", "D1"])
        # AND BACK, at its original position, through the shared restore.
        _br._restore_refused_precheck_units(doc, refused,
                                            ["S1", "K1", "F1", "D1"])
        with open(doc, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual([r["id"] for r in saved["rows"]],
                         ["S1", "K1", "F1", "D1"])

    def test_no_claim_store_leaves_the_document_byte_for_byte_untouched(self):
        doc = _write_doc(self.tmp, [
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        with open(doc, encoding="utf-8") as fh:
            before = fh.read()
        self.assertEqual(_br._refuse_exhausted_units(doc, self.claims), {})
        with open(doc, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_the_creator_stamp_is_never_overwritten_the_resumer_always_is(self):
        doc = _write_doc(self.tmp, [
            {"id": "A1", "status": "SCHEDULED", "done_check": "true"},
        ])
        _br._stamp_harness(doc, "harness_revision", "first", overwrite=False)
        saved = _br._stamp_harness(doc, "harness_revision", "second",
                                   overwrite=False)
        self.assertEqual(saved["harness_revision"], "first")
        _br._stamp_harness(doc, "harness_revision_resumed", "r1", overwrite=True)
        saved = _br._stamp_harness(doc, "harness_revision_resumed", "r2",
                                   overwrite=True)
        self.assertEqual(saved["harness_revision_resumed"], "r2")
        self.assertEqual(saved["harness_revision"], "first")
        self.assertEqual([r["id"] for r in saved["rows"]], ["A1"])


class EveryUnitRefusedBeforeWorkIsOneLine(unittest.TestCase):
    """Finding 3 of the toy's run record (2026-09-03): a run whose every
    unit was refused before work printed two empty rounds and an empty
    verified section. Every check here is broken, so every unit is refused
    at intake (rule 4), and the report says so in one line, exit 1 kept.
    The other way, one broken unit beside healthy siblings, keeps the
    ordinary integrated listing: BrokenCheckUnitsAreRefusedBeforeAnyWorker
    above pins it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-all-refused-")
        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "B1", "objective": "a unit whose check is broken",
                 "done_check": "python3 -c 'this is not python('",
                 "writes": ["b1.txt"], "deps": []},
                {"id": "B2", "objective": "another broken check",
                 "done_check": "python3 -c 'nor is this('",
                 "writes": ["b2.txt"], "deps": []},
            ]))
        """)
        self.model = write_stub(self.tmp, "invocation_logging_model.py",
                                INVOCATION_LOGGING_MODEL)
        self.log_path = os.path.join(self.tmp, "worker_invocations.log")
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)
        self.env["WORKER_INVOCATION_LOG"] = self.log_path

    def test_all_refused_before_work_is_one_line_and_exit_1(self):
        proc = sh([sys.executable, BROTHER_RUN, "two units, both broken",
                  "--cwd", self.repo, "--runs-root", self.tmp], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertFalse(os.path.exists(self.log_path), out)   # no worker
        self.assertIn("nothing was claimed or run: all 2 remaining piece(s) "
                      "were refused before any worker started", out, out)
        self.assertIn("nothing was verified: all 2 piece(s) were refused "
                      "before any worker started", out, out)
        self.assertNotIn("integrated (0):", out, out)
        self.assertNotIn("round 1 done", out, out)
        self.assertNotIn("moved nothing forward", out, out)
        self.assertIn("refused (2):", out, out)
        self.assertIn("verdicts: 0 PASS, 2 FAIL, 0 NO-DATA", out, out)


class TheIntentScreenShowsEachUnitsDependencies(unittest.TestCase):
    """E40 (run 5 critic 3, hole H1, 2026-09-03): the intent screen listed
    title and done_check only, so nobody could refuse a missing coverage
    declaration, while _stamp_dependency_mutations' own docstring claimed
    the screen showed it (grep -c depends on a real run log printed 0).
    Every unit line now carries its depends_on, "none" when empty."""

    def test_no_dependency_prints_depends_on_none(self):
        lines = _br._unit_check_lines(
            [{"id": "U1", "objective": "x", "done_check": "true"}])
        self.assertIn("depends on: none", lines[0])

    def test_a_declared_dependency_is_printed_by_id(self):
        lines = _br._unit_check_lines(
            [{"id": "U2", "objective": "x", "done_check": "true",
             "depends_on": ["U1", "U0"]}])
        self.assertIn("depends on: U1, U0", lines[0])
        self.assertNotIn("none", lines[0])


class ATestOnlyUnitMustNameTheUnitItCovers(unittest.TestCase):
    """E40's refusal, pure and both ways: a plan holding a test-only unit
    (every owned path a test file) beside a non-test unit, where that test
    unit names no non-test unit in depends_on, is refused before any claim
    with one line naming the unit and the reason, the dirty-tree refusal's
    own shape; a plan whose units are all test-only has nothing to cover
    and is not refused."""

    def test_which_paths_count_as_test_files(self):
        for path in ("test_x.py", "pkg/test_y.txt", "x_test.py",
                     "tests/anything.txt", "src/tests/deep/x.py"):
            self.assertTrue(_br._is_test_path(path), path)
        for path in ("mathlib.py", "src/x.py", "testing.py", "contest.py",
                     "tests", "latest_test.pyc"):
            self.assertFalse(_br._is_test_path(path), path)

    def test_a_test_only_unit_naming_no_code_unit_beside_one_is_refused(self):
        line = _br._uncovered_test_unit_line([
            {"id": "helper", "owns": ["helper.py"], "depends_on": []},
            {"id": "cover", "owns": ["test_helper.py"], "depends_on": []}])
        self.assertTrue(line.startswith("brother_run: "), line)
        self.assertIn("refused at the intent screen", line)
        self.assertIn("cover owns only test files (test_helper.py)", line)
        self.assertIn("the test unit names no unit it covers", line)
        self.assertIn("nothing was claimed or run", line)

    def test_a_test_only_unit_that_depends_on_a_code_unit_is_not_refused(self):
        self.assertEqual(_br._uncovered_test_unit_line([
            {"id": "helper", "owns": ["helper.py"], "depends_on": []},
            {"id": "cover", "owns": ["test_helper.py"],
             "depends_on": ["helper"]}]), "")

    def test_depending_only_on_another_test_unit_covers_nothing(self):
        line = _br._uncovered_test_unit_line([
            {"id": "helper", "owns": ["helper.py"]},
            {"id": "t1", "owns": ["test_a.py"], "depends_on": ["helper"]},
            {"id": "t2", "owns": ["tests/b.py"], "depends_on": ["t1"]}])
        self.assertIn("t2 owns only test files", line)

    def test_a_plan_of_only_test_units_has_nothing_to_cover(self):
        self.assertEqual(_br._uncovered_test_unit_line([
            {"id": "t1", "owns": ["test_a.py"]},
            {"id": "t2", "owns": ["tests/b.txt"]}]), "")

    def test_a_finished_test_unit_is_left_alone(self):
        self.assertEqual(_br._uncovered_test_unit_line([
            {"id": "helper", "owns": ["helper.py"]},
            {"id": "cover", "owns": ["test_helper.py"], "status": "DONE"}]),
            "")

    def test_a_unit_mixing_test_and_code_paths_is_a_code_unit(self):
        self.assertEqual(_br._uncovered_test_unit_line([
            {"id": "helper", "owns": ["helper.py"]},
            {"id": "both", "owns": ["test_helper.py", "helper2.py"]}]), "")


#: A "model" that writes its declared scope like WRITER_MODEL but never
#: overwrites a file that already exists, so a unit owning an existing file
#: changes nothing: the zero-change shape E41 and the run 7 record
#: (2026-09-03) needed a real, integrating run of.
NON_OVERWRITING_MODEL = """
    import os, re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path and not os.path.exists(path):
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote only what did not exist")
"""

#: A check that fails exactly once, on its first invocation ever (the
#: engine's own precheck), and passes on every later one, wherever it is
#: asked: the shape of a unit whose check discriminates its own existence
#: while the unit itself changes no file.
FAILS_FIRST_TIME_ONLY = """
    import os, sys
    COUNTER = %r
    n = 0
    if os.path.exists(COUNTER):
        with open(COUNTER) as fh:
            n = int(fh.read() or "0")
    n += 1
    with open(COUNTER, "w") as fh:
        fh.write(str(n))
    sys.exit(1 if n == 1 else 0)
"""


def _work_rows(run_dir):
    """{unit_id: row} from the run's own Work document, found the way
    brother_run finds it: the one .json in the run directory carrying
    rows (claims.json and the usage sidecar carry none)."""
    for name in sorted(os.listdir(run_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(run_dir, name), encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
            return {r.get("id"): r for r in doc["rows"]}
    return {}


class StubRunFixture(unittest.TestCase):
    """One real repository, one real brother_run.py process, a decomposer
    and a model stub supplied per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-run-e40e41-")
        self.repo = make_repo(self.tmp)

    def _run(self, outcome, decomposer_body, model_body=WRITER_MODEL):
        dec = write_stub(self.tmp, "decomposer.py", decomposer_body)
        model = write_stub(self.tmp, "writer_model.py", model_body)
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, dec)
        env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
        proc = sh([sys.executable, BROTHER_RUN, outcome,
                  "--cwd", self.repo, "--runs-root", self.tmp], env=env)
        return proc, proc.stdout + proc.stderr

    def _log(self):
        with open(os.path.join(_only_run_dir(self.tmp), "run.log"),
                  encoding="utf-8") as fh:
            return fh.read()


class ACoverageGapIsRefusedBeforeAnyClaim(StubRunFixture):
    """E40 end to end, the critic's own driven shape: a decomposer emitting
    `helper` and a `cover` unit whose every owned path is a test file and
    whose deps are empty. Before: exit 0 and 2 PASS on a check that proved
    nothing about helper. Now: refused at the intent screen, before any
    claim, in one line naming cover and the reason."""

    def test_a_test_only_unit_naming_no_dependency_beside_a_code_unit_is_refused(self):
        log_before = sh(["git", "log", "--oneline"], cwd=self.repo).stdout
        proc, out = self._run("a helper covered by a test", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "helper", "objective": "create the helper",
                 "done_check": "test -f helper.txt", "writes": ["helper.txt"],
                 "deps": []},
                {"id": "cover", "objective": "a test covering the helper",
                 "done_check": "test -f test_helper.txt",
                 "writes": ["test_helper.txt"], "deps": []},
            ]))
        """)
        self.assertEqual(proc.returncode, 1, out)
        line = next((l for l in out.splitlines()
                     if "names no unit it covers" in l), "")
        self.assertIn("refused at the intent screen", line, out)
        self.assertIn("cover owns only test files (test_helper.txt)", line, out)
        self.assertIn("nothing was claimed or run", line, out)
        self.assertNotIn("verdicts:", out, out)
        run_dir = _only_run_dir(self.tmp)
        self.assertIsNotNone(run_dir, out)
        self.assertFalse(os.path.isfile(os.path.join(run_dir, "claims.json")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "helper.txt")))
        self.assertEqual(sh(["git", "log", "--oneline"], cwd=self.repo).stdout,
                         log_before)

    def test_a_plan_of_only_test_units_proceeds_and_each_receipt_says_what_it_proves(self):
        proc, out = self._run("two test files exist", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "t1", "objective": "a first test file",
                 "done_check": "test -f test_one.txt",
                 "writes": ["test_one.txt"], "deps": []},
                {"id": "t2", "objective": "a second test file",
                 "done_check": "test -f two_test.py",
                 "writes": ["two_test.py"], "deps": []},
            ]))
        """)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertEqual(out.count("(no dependency declared: this check "
                                   "proves its own change only)"), 2, out)
        # THE SCREEN SHOWS THE DECLARATION, once per unit, and the intent
        # resolution line carries the field by name.
        logged = self._log()
        self.assertGreaterEqual(logged.count("depends on: none"), 2, logged)
        self.assertIn("depends_on per unit: t1: none; t2: none", logged)
        self.assertIn("depends_on per unit: t1: none; t2: none", out)


class EachUnitCarriesItsOwnFileList(StubRunFixture):
    """E41 end to end: _mark_integrated stamped the ROUND's diff on every
    row it marked DONE, so both units of one round carried the same two
    files (measured by the critic). Now each unit's list is what its own
    lane merge changed on canonical, read at the merge and carried in the
    claim's evidence."""

    def test_two_units_in_one_round_carry_different_lists(self):
        proc, out = self._run("two files exist", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "A2", "objective": "create file two",
                 "done_check": "test -f two.txt", "writes": ["two.txt"],
                 "deps": []},
            ]))
        """)
        self.assertEqual(proc.returncode, 0, out)
        rows = _work_rows(_only_run_dir(self.tmp))
        self.assertEqual(rows["A1"]["files_changed_by_unit"], ["one.txt"], out)
        self.assertEqual(rows["A2"]["files_changed_by_unit"], ["two.txt"], out)

    def test_a_unit_that_changed_nothing_beside_a_sibling_reads_no_file_changed(self):
        """N1 owns base.txt (already there), a model that never overwrites
        leaves it alone, and N1's check fails only on the precheck: the
        unit integrates as a no-op merge in the same round A1 lands one.txt.
        Under the round diff N1 read PASS on A1's file; under its own list
        it reads NO-DATA no file changed."""
        counter = os.path.join(self.tmp, "n1.count")
        check = write_stub(self.tmp, "fails_once.py",
                           FAILS_FIRST_TIME_ONLY % counter)
        proc, out = self._run("one file and a no-op", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "A1", "objective": "create file one",
                 "done_check": "test -f one.txt", "writes": ["one.txt"],
                 "deps": []},
                {"id": "N1", "objective": "touch nothing",
                 "done_check": %r, "writes": ["base.txt"],
                 "deps": []},
            ]))
        """ % ("%s %s" % (sys.executable, check)), NON_OVERWRITING_MODEL)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("A1 delivered:", out, out)
        self.assertIn("N1 is NO-DATA: no file changed, so nothing here "
                      "proves the work was done", out, out)
        self.assertIn("verdicts: 1 PASS, 0 FAIL, 1 NO-DATA", out, out)
        rows = _work_rows(_only_run_dir(self.tmp))
        self.assertEqual(rows["A1"]["files_changed_by_unit"], ["one.txt"])
        self.assertEqual(rows["N1"]["files_changed_by_unit"], [])


class ADependencyThatChangedNoFileIsNoDataWithoutAReRun(StubRunFixture):
    """Run 7 at the clean HEAD (2026-09-03, exit 2): the dependency unit
    had changed nothing (its check already passed, the engine integrated a
    no-op for it) and the dependent unit's mutation still tried to revert
    it, then failed on git. When the dependency's own file list is empty
    the re-run is meaningless: the dependent reads NO-DATA with the reason,
    the log says it was not re-run, and git is never asked."""

    def test_the_receipts_and_the_log(self):
        proc, out = self._run("a guard and a test of it", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "guard", "objective": "the guard already in base.txt",
                 "done_check": "true", "writes": ["base.txt"], "deps": []},
                {"id": "cover", "objective": "a test covering the guard",
                 "done_check": "test -f test_helper.txt",
                 "writes": ["test_helper.txt"], "deps": ["guard"]},
            ]))
        """, NON_OVERWRITING_MODEL)
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("guard is NO-DATA: the check already passed before the "
                      "work began, so it cannot prove the work", out, out)
        self.assertIn("cover is NO-DATA: its dependency guard changed no "
                      "file, so nothing shows the check exercises it", out, out)
        self.assertIn("verdicts: 0 PASS, 0 FAIL, 2 NO-DATA", out, out)
        logged = self._log()
        self.assertIn("cover's check was not re-run", logged, logged)
        self.assertNotIn("git could not read what", logged, logged)
        self.assertNotIn("reverted: NO-DATA", logged, logged)
        rows = _work_rows(_only_run_dir(self.tmp))
        self.assertEqual(rows["guard"]["files_changed_by_unit"], [])
        self.assertEqual(rows["cover"]["files_changed_by_unit"],
                         ["test_helper.txt"])
        self.assertEqual(rows["cover"][_br.CHECK_WITHOUT_FIELD][0]["files"], [])
        self.assertIsNone(rows["cover"][_br.CHECK_WITHOUT_FIELD][0]["exit_code"])


class ADependencyStampedWithNoFileIsNeverReRun(unittest.TestCase):
    """The same rule at the unit level, driven with git forbidden: a
    dependency row whose files_changed_by_unit is [] produces the NO-DATA
    entry from the stamp alone; neither the changed-files reader nor the
    throwaway worktree is ever reached."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dep-nofile-")
        self._reader, self._without = _br._first_parent_files, _br._check_without
        _br._first_parent_files = lambda *a, **k: self.fail(
            "git was asked what the dependency changed")
        _br._check_without = lambda *a, **k: self.fail(
            "a throwaway worktree was made for a dependency that changed nothing")

    def tearDown(self):
        _br._first_parent_files, _br._check_without = self._reader, self._without

    def test_the_stamp_reads_no_data_naming_the_dependency(self):
        doc = _write_doc(self.tmp, [
            {"id": "guard", "status": "DONE", "depends_on": [],
             "done_check": "true", "check_passed_before": True,
             "files_changed_by_unit": []},
            {"id": "test", "status": "DONE", "depends_on": ["guard"],
             "done_check": "python3 test_it.py", "check_passed_before": False,
             "files_changed_by_unit": ["test_it.py"]}])
        claims = {
            "guard": {"state": "done", "evidence": {"canonical_rev": "abc"}},
            "test": {"state": "done", "evidence": {"canonical_rev": "def"}}}
        stamped = _br._stamp_dependency_mutations(doc, claims, self.tmp)
        self.assertEqual(stamped["test"], [{
            "unit": "guard", "files": [], "revision": "def",
            "exit_code": None, "stderr": "",
            "note": "its dependency guard changed no file, so nothing shows "
                    "the check exercises it"}])
        with open(doc, encoding="utf-8") as fh:
            record = json.load(fh)
        receipts = RD.receipts_for(record, claims, [])
        self.assertEqual(receipts[1]["state"], "no-data")
        self.assertEqual(receipts[1]["reason"],
                         "its dependency guard changed no file, so nothing "
                         "shows the check exercises it")


class TheChangedFilesReaderHandlesARootCommit(unittest.TestCase):
    """Run 7's other failure: the dependency's recorded revision was the
    target repository's ROOT commit (a one-commit repository, the toy's
    own shape), and `<sha>^1` does not resolve there, so the reader failed
    with "unknown revision". A root commit's change is its whole tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="root-commit-")
        self.repo = make_repo(self.tmp)
        self.root = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()

    def test_a_root_commit_reads_its_own_files_never_an_error(self):
        self.assertEqual(_br._first_parent_files(self.repo, self.root),
                         (["base.txt"], ""))

    def test_a_commit_landing_directly_on_the_root_reads_only_its_change(self):
        with open(os.path.join(self.repo, "D.txt"), "w", encoding="utf-8") as fh:
            fh.write("d\n")
        sh(["git", "add", "D.txt"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "D lands"], cwd=self.repo)
        rev = sh(["git", "rev-parse", "HEAD"], cwd=self.repo).stdout.strip()
        self.assertEqual(_br._first_parent_files(self.repo, rev), (["D.txt"], ""))

    def test_an_unknown_revision_is_none_with_why(self):
        files, note = _br._first_parent_files(self.repo, "0" * 40)
        self.assertIsNone(files)
        self.assertIn("could not read", note)


if __name__ == "__main__":
    unittest.main()

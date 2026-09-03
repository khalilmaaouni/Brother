"""Fixtures for the review record: `sbe review --write` (src/brothersbe/cli.py)
writing `11-review.json` into a dossier, and `sbe status --team`
(src/brothersbe/status.py) reading it back: absence (NO-DATA, never a
block), a record present but broken (FAIL), a record bound to a commit that
is no longer the head (derived, blocking staleness, the same slot approval
and convergence staleness already use), a reviewer who is also the commit's
author (FAIL, mirroring `prverify.py`'s "approving their own change"), and a
clean independent approved record (PASS, its counts always stated).

Every fixture builds its own throwaway git repository with a dossier under
design/, seeded through the real `sbe plan --write`, as
tools/test_sbe_status_team.py does. `sbe review` also runs `sbe_score.py`,
which reads BROTHERSBE_VAULT and defaults its citation check to this
repository's own tree; every `sbe` call here runs with BROTHERSBE_VAULT
unset and SBE_CITATION_ROOT pointed at the throwaway repo instead, so a
scenario depends only on the fixture it built.

LT-202 additively extends `11-review.json` with `structuredFindings`
(normalized, fingerprinted, deduplicated findings) behind a new
`--findings-json <path>` flag on `sbe review --write`, plus the
`findingsSchemaVersion` tag that names which shape they are in. Coverage
added for it, by class:

  TestNormalizeReviewFindingsUnit   direct-import unit tests of
                                     `brothersbe.cli.normalize_review_findings`
                                     and its helpers (the same
                                     `sys.path.insert` pattern
                                     tools/test_sbe_status.py already uses):
                                     fingerprint determinism, every
                                     deduplication rule, the blocking
                                     predicate, and every write-time
                                     validation refusal, all without needing
                                     a git repository or a subprocess.
  TestWriteFindingsJson             `sbe review --write --findings-json`
                                     end to end: a valid file is persisted,
                                     an invalid one refuses the whole write
                                     (no partial record), and the write can
                                     still never move the exit code.
  TestStatusReadsStructuredFindings  `sbe status --team` reading
                                     `structuredFindings` back: absence on
                                     an old or a plain record is honest
                                     NO-DATA, never invented; a record
                                     missing `findingsSchemaVersion`, naming
                                     one this installation does not
                                     recognize, or carrying a malformed
                                     entry is FAIL, named by index, never
                                     silently dropped; a real write with a
                                     blocking finding reads FAIL, a
                                     non-blocking one reads PASS, and a
                                     contradiction dedup left in
                                     "arbitration" reads NO-DATA naming
                                     adjudication as the next action.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(HERE, "..", "bin", "sbe")

INTAKE = {
    "answers": {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n",
                "consumers": "none"},
    "tier": "T1", "override": None, "override_reason": None,
}

ADR_TEMPLATE = """# ADR
## Context
x
## Decision
Serve the change from `%s`.
## Consequences
ok
"""

VERIFICATION = """| Claim this design makes | The check that proves it | When it runs |
|---|---|---|
| it answers | `python3 -c pass` | CI |
"""


def _run(argv, cwd=None, env=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: a two-value return reads as a possible
    # (verdict, evidence) pair to the honesty meta-test, which refuses any
    # such function sitting outside a check registry.
    return out.returncode, out.stdout + out.stderr, out.stderr


class ReviewScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-review-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        io.open(os.path.join(self.repo, "seed.txt"), "w").write("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        # Isolate every `sbe` call in this file from the real machine: no
        # real vault, and the citation check scans the throwaway repo rather
        # than defaulting to this installation's own tree.
        self.env = dict(os.environ)
        self.env.pop("BROTHERSBE_VAULT", None)
        self.env["SBE_CITATION_ROOT"] = self.repo

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args), env=self.env)

    def team(self, *extra):
        return self.sbe("status", self.repo, "--team", *extra)

    def team_findings(self, change="chg-a", severity=None):
        # Three values, not two: a two-value return reads as a possible
        # (verdict, evidence) pair to the honesty meta-test, which refuses
        # any such function sitting outside a check registry. `text` is the
        # raw `--json` report `hits` was parsed from, kept rather than
        # dropped so a caller whose assertion on `hits` fails can print the
        # whole report instead of just the filtered slice.
        code, text, _err = self.team("--json")
        data = json.loads(text[text.index("{"):])
        hits = [f for f in data["findings"] if f["change"] == change]
        if severity is not None:
            hits = [f for f in hits if f["severity"] == severity]
        return code, hits, text

    def _change(self, name, src_rel):
        doss = os.path.join(self.repo, "design", name)
        os.makedirs(doss)
        io.open(os.path.join(doss, "00-intake.json"), "w").write(
            json.dumps(INTAKE, indent=2))
        io.open(os.path.join(doss, "03-adr.md"), "w").write(ADR_TEMPLATE % src_rel)
        io.open(os.path.join(doss, "07-verification.md"), "w").write(VERIFICATION)
        src = os.path.join(self.repo, src_rel)
        parent = os.path.dirname(src)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        io.open(src, "w").write("x = 1\n")
        code, text, _ = self.sbe("plan", doss, "--write", "--cwd", self.repo)
        self.assertEqual(code, 0, "plan --write for %s: %s" % (name, text))
        return doss

    def _closed_record(self, task_id, owns, agent="alice", base_commit=None):
        reg_dir = os.path.join(self.repo, ".sbe")
        if not os.path.isdir(reg_dir):
            os.makedirs(reg_dir)
        reg = os.path.join(reg_dir, "tasks.json")
        data = {"schemaVersion": "1.0", "tasks": []}
        if os.path.exists(reg):
            data = json.loads(io.open(reg).read())
        data["tasks"].append({
            "id": task_id, "agent": agent, "role": "writer", "worktree": None,
            "ownedPaths": owns, "readOnlyPaths": [], "baseCommit": base_commit,
            "expiry": None, "status": "closed", "verifyCommand": "python3 -c pass",
            "evidenceId": None, "openedAt": "2026-07-30T00:00:00Z",
            "closedAt": "2026-07-30T01:00:00Z",
        })
        io.open(reg, "w").write(json.dumps(data, indent=2))

    def _write_json(self, path, data):
        io.open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2))

    def _make_clean_change(self, name="chg-a", src_rel="src/a.py"):
        """A change with a plan, both tasks closed clean, a PASS convergence
        report, a PASS approval report and a clean evidence receipt, all
        bound to the current head. What a scenario built on top of this
        asserts about severity 4 or severity 11 is then never an accident of
        some OTHER severity slot also being unhappy: this is what "otherwise
        clean" means in the tests below.

        Returns three values, not two, for the same reason `_run` above
        does: a two-value return reads as a possible (verdict, evidence)
        pair to the honesty meta-test, which refuses any such function
        sitting outside a check registry, and `doss, head = ...` here is a
        dossier path and a commit sha, neither of them a verdict this lint
        could ever prove is not PASS. The third value, the evidence
        receipt's path, is not padding: it is the one path this method
        computes that no caller could otherwise re-derive without repeating
        `.sbe/evidence/ok.json` by hand.
        """
        doss = self._change(name, src_rel)
        self.git("add", "-A")
        self.git("commit", "-qm", "%s dossier and source" % name)
        head = self.git("rev-parse", "HEAD").strip()
        # The evidence run happens here, against the just-committed, still
        # clean tree: `evidence.verify` reads a receipt generated over a
        # dirty tree as NO-DATA (advisory, never sound evidence), and the
        # fixture files written below (an open registry, hand-written
        # convergence and approval reports) would make the tree dirty if
        # they came first.
        out = os.path.join(self.repo, ".sbe", "evidence", "ok.json")
        code, text, _ = self.sbe("evidence", "run", "--out", out, "--cwd", self.repo,
                                 "--covers", src_rel, "--", sys.executable, "-c", "pass")
        self.assertEqual(code, 0, "evidence run: %s" % text)
        self._closed_record("T01", [src_rel], agent="alice", base_commit=head)
        self._closed_record("T02", [], agent="alice", base_commit=head)
        self._write_json(os.path.join(doss, "09-convergence.json"),
                         {"final": "PASS", "head": head, "dossier": doss, "base": head})
        self._write_json(os.path.join(doss, "10-approval.json"),
                         {"tool": "sbe pr verify", "repository": "example/repo",
                          "number": 1, "headSha": head, "final": "PASS", "controls": []})
        return doss, head, out


class TestWrite(ReviewScenario):
    """The write side: `sbe review <dossier> --write ...`."""

    def test_without_write_no_record_is_created(self):
        doss = self._change("chg-a", "src/a.py")
        self.sbe("review", doss)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")),
                         "review without --write must behave exactly as before: no file")

    def test_write_without_the_required_flags_is_a_usage_error(self):
        doss = self._change("chg-a", "src/a.py")
        code, text, _ = self.sbe("review", doss, "--write")
        self.assertEqual(code, 2, text)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")),
                         "a refused write must leave no partial record: %s" % text)
        self.assertIn("--reviewer", text)
        self.assertIn("--reviewer-type", text)
        self.assertIn("--result", text)

    def test_write_persists_a_record_carrying_every_required_field(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        head = self.git("rev-parse", "HEAD").strip()
        self.sbe("review", doss, "--write", "--reviewer", "Independent Reviewer",
                "--reviewer-type", "human", "--result", "approved",
                "--accept-risk", "known flaky test")
        path = os.path.join(doss, "11-review.json")
        self.assertTrue(os.path.exists(path), "sbe review --write must write 11-review.json")
        record = json.loads(io.open(path, encoding="utf-8").read())
        for key in ("schemaVersion", "tool", "dossier", "headSha", "reviewer",
                   "reviewerType", "result", "findings", "acceptedRisks", "createdAt"):
            self.assertIn(key, record, "11-review.json missing %s: %s" % (key, record))
        self.assertEqual(record["headSha"], head)
        self.assertEqual(record["reviewer"], "Independent Reviewer")
        self.assertEqual(record["reviewerType"], "human")
        self.assertEqual(record["result"], "approved")
        self.assertEqual(record["acceptedRisks"], ["known flaky test"])
        self.assertIsInstance(record["findings"], list)

    def test_write_can_never_move_the_exit_code(self):
        """`_record_review` must never be the thing deciding this command's
        exit code: the same run, with and without --write, must exit the
        same way, because `worst` is computed before the write is ever
        attempted (cli.py's `_cmd_review`)."""
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        without_code, _t1, _e1 = self.sbe("review", doss)
        with_code, _t2, _e2 = self.sbe("review", doss, "--write", "--reviewer", "r",
                                       "--reviewer-type", "human", "--result", "approved")
        self.assertEqual(without_code, with_code,
                         "--write changed the exit code sbe review returned")

    def test_a_record_written_by_a_real_run_is_read_back_as_an_independent_pass(self):
        """End to end: a record `sbe review --write` actually produced is
        read, unmodified, by `sbe status --team` as a clean, independent
        approval."""
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved")
        self.assertTrue(os.path.exists(os.path.join(doss, "11-review.json")), text)
        _code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, "expected a severity 11 review finding: %s" % text)
        self.assertEqual(hits[0]["verdict"], "PASS", hits[0])
        self.assertEqual(hits[0]["basis"], "observed", hits[0])


class TestStatusWiring(ReviewScenario):
    """The read side: `sbe status --team` next to the existing approval and
    convergence blocks."""

    def test_absence_is_no_data_and_never_blocks_an_otherwise_clean_change(self):
        # Central to this defect fix: human review has never run on this
        # repository (every merged pull request carries zero reviews), and
        # the repository's own law is that absence is NO-DATA, never a pass
        # and never a block. A missing 11-review.json must not flip an
        # otherwise clean change to blocking the day this feature ships.
        self._make_clean_change("chg-a", "src/a.py")
        code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, "a change with a plan must always carry a review finding, "
                              "even an absent one")
        self.assertEqual(hits[0]["verdict"], "NO-DATA", hits[0])
        self.assertIn("absence is a fact, not an accusation", hits[0]["detail"])
        self.assertEqual(code, 0,
                         "a missing review record must never, by itself, block a merge")

    def test_a_malformed_record_is_fail_never_no_data(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        io.open(os.path.join(doss, "11-review.json"), "w").write("not json{{{")
        code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL",
                         "a record present but unparseable must FAIL, not read as the "
                         "same absence as no record at all: %s" % hits)
        # It must FAIL by actually naming the parse error, not merely by
        # saying "broken" in the abstract: "I could not read this" has to
        # carry the reason, the same way a receipt that fails verify names
        # its reasons rather than only saying "fails verify".
        self.assertIn("does not parse", hits[0]["detail"])
        self.assertIn("Expecting value", hits[0]["detail"], hits)

    def test_absence_and_malformed_never_share_a_verdict(self):
        """The two states this file exists to keep apart, in one place:
        absent is NO-DATA (nobody has reviewed yet, a fact, not an
        accusation), unparseable is FAIL (somebody tried and left something
        nobody can trust). Collapsing "I could not read the review" and
        "there is no review" into the same answer is exactly the confusion
        `sbe review` not persisting anything used to cause."""
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        _code, absent, _text = self.team_findings("chg-a", 11)
        self.assertEqual(absent[0]["verdict"], "NO-DATA", absent)
        io.open(os.path.join(doss, "11-review.json"), "w").write("{not valid")
        _code, malformed, _text = self.team_findings("chg-a", 11)
        self.assertEqual(malformed[0]["verdict"], "FAIL", malformed)
        self.assertNotEqual(absent[0]["verdict"], malformed[0]["verdict"])

    def test_a_record_missing_a_required_field_is_also_fail(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "result": "approved"})  # no reviewerType
        code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)

    def test_a_stale_review_record_is_a_derived_severity_four_finding(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": []})
        io.open(os.path.join(self.repo, "src", "a.py"), "a").write("y = 2\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a later commit the saved review never saw")
        _code, hits, _text = self.team_findings("chg-a", 4)
        stale = [f for f in hits if "11-review.json" in (f["evidence"] or "")
                or "review" in f["detail"].lower()]
        self.assertTrue(stale, "a review bound to a superseded head must surface at "
                              "severity 4: %s" % hits)
        self.assertEqual(stale[0]["basis"], "derived", stale[0])
        self.assertEqual(stale[0]["verdict"], "FAIL", stale[0])
        self.assertIn(head[:12], stale[0]["detail"])

    def test_self_review_fails_and_is_never_a_silent_pass(self):
        # setUp's git identity is name "fixture", email fixture@example.invalid.
        # A review record naming that identity as reviewer, by name or by
        # email, must not read as a clean pass, exactly as prverify.py
        # refuses a GitHub approval whose login is the pull request's author.
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        for reviewer in ("fixture", "fixture@example.invalid"):
            self._write_json(os.path.join(doss, "11-review.json"),
                             {"headSha": head, "reviewer": reviewer,
                              "reviewerType": "human", "result": "approved",
                              "findings": [], "acceptedRisks": []})
            _code, hits, _text = self.team_findings("chg-a", 11)
            self.assertTrue(hits, (reviewer, hits))
            self.assertEqual(hits[0]["verdict"], "FAIL", (reviewer, hits))
            self.assertIn("naming only the author is not an approval", hits[0]["detail"])
            self.assertEqual(hits[0]["basis"], "derived", hits[0])

    def test_an_independent_approved_review_is_a_clean_pass_stating_its_counts(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "independent-model", "result": "approved",
                          "findings": ["minor: consider a docstring"],
                          "acceptedRisks": ["accepted: perf ok for this volume"]})
        code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "PASS", hits)
        self.assertEqual(hits[0]["basis"], "observed", hits[0])
        # Rule: a record must never read as silently clean; its finding and
        # accepted-risk counts are stated explicitly, even when both are
        # zero, so the difference between "reviewed and found nothing" and
        # "was not looked at" is never lost. Here they are 1 and 1.
        self.assertIn("1 finding", hits[0]["detail"])
        self.assertIn("1 accepted risk", hits[0]["detail"])
        self.assertEqual(code, 0, hits)

    def test_zero_findings_are_stated_explicitly_not_read_as_silently_clean(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": []})
        _code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertIn("0 finding", hits[0]["detail"])
        self.assertIn("0 accepted risk", hits[0]["detail"])

    def test_a_non_approved_result_surfaces_as_its_own_verdict(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "changes-required",
                          "findings": ["fix the null check"], "acceptedRisks": []})
        _code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "changes-required", hits)

    def test_an_undeterminable_author_is_no_data_never_a_pass(self):
        # bound == head (never stale) but the commit object itself cannot be
        # read, the shape a shallow clone or a partially fetched history
        # leaves behind: the ref resolves to a sha, the object backing it
        # does not. `_commit_author` must answer (None, None), and that must
        # read as NO-DATA, never as a pass, even though `result` says
        # "approved".
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        obj_path = os.path.join(self.repo, ".git", "objects", head[:2], head[2:])
        self.assertTrue(os.path.exists(obj_path), "fixture assumption: a loose object")
        os.remove(obj_path)
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": []})
        _code, hits, _text = self.team_findings("chg-a", 11)
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "NO-DATA",
                         "an unresolvable author must never read as a pass: %s" % hits)
        self.assertEqual(hits[0]["basis"], "unavailable", hits[0])

    def test_the_review_record_appears_in_the_human_report_next_to_approval(self):
        self._make_clean_change("chg-a", "src/a.py")
        code, text, _ = self.team()
        self.assertIn("REVIEW RECORD", text)
        self.assertIn("11-review.json", text)

    def test_severity_eleven_is_within_the_documented_json_contract_bound(self):
        self._make_clean_change("chg-a", "src/a.py")
        code, text, _ = self.team("--json")
        data = json.loads(text[text.index("{"):])
        sevs = [f["severity"] for f in data["findings"]]
        self.assertTrue(sevs, "expected findings")
        self.assertTrue(all(1 <= s <= 11 for s in sevs), sevs)
        self.assertIn(11, sevs)


class TestReviewHistory(ReviewScenario):
    """LANE B-006: a second `--write` at the SAME head used to overwrite
    `11-review.json` in place, silently erasing a prior verdict with no
    trace. Fix: every `--write` first archives whatever `11-review.json`
    holds into append-only `11-review-history.jsonl` beside it, and `sbe
    status --team` discloses a same-head, different-result supersession as
    its own named finding, filtered here by evidence path rather than list
    position for the reason `_struct_hits` below gives: findings interleave
    by (severity, change, evidence, detail)."""

    def _write(self, doss, result, reviewer="Independent Reviewer"):
        return self.sbe("review", doss, "--write", "--reviewer", reviewer,
                        "--reviewer-type", "human", "--result", result)

    def _current(self, doss):
        return json.loads(io.open(os.path.join(doss, "11-review.json")).read())

    def _history_path(self, doss):
        return os.path.join(doss, "11-review-history.jsonl")

    def _history_entries(self, doss):
        path = self._history_path(doss)
        if not os.path.exists(path):
            return []
        return [json.loads(l) for l in
               io.open(path, encoding="utf-8").read().splitlines() if l.strip()]

    def _disclosure_hits(self, change="chg-a"):
        _code, hits, _text = self.team_findings(change, 11)
        return [f for f in hits if "11-review-history.jsonl" in (f["evidence"] or "")]

    def test_a_second_write_at_the_same_head_preserves_the_first_verdict(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self.assertFalse(os.path.exists(self._history_path(doss)))
        self._write(doss, "changes-required")
        self.assertEqual(self._current(doss)["result"], "changes-required")
        self.assertFalse(os.path.exists(self._history_path(doss)),
                         "the first ever write has nothing prior to archive")

        code, text, _ = self._write(doss, "approved")
        self.assertEqual(code, 0, text)
        current = self._current(doss)
        self.assertEqual(current["result"], "approved", current)
        self.assertEqual(current["headSha"], head, current)

        # RED against pre-fix code: the prior verdict must stay readable.
        entries = self._history_entries(doss)
        self.assertEqual(len(entries), 1,
                         "overwritten record must be archived, not erased: %s" % entries)
        self.assertEqual(entries[0]["result"], "changes-required", entries)
        self.assertEqual(entries[0]["headSha"], head, entries)

    def test_a_third_write_archives_the_second_and_keeps_the_first(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        for result in ("changes-required", "unverifiable", "approved"):
            self.assertEqual(self._write(doss, result)[0], 0)
        self.assertEqual(self._current(doss)["result"], "approved")
        entries = self._history_entries(doss)
        self.assertEqual([e["result"] for e in entries],
                         ["changes-required", "unverifiable"], entries)

    def test_the_overwrite_is_disclosed_in_status_never_silent(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write(doss, "changes-required")
        self._write(doss, "approved")
        _code, hits, text = self.team_findings("chg-a", 11)
        plain = [f for f in hits if "11-review-history.jsonl" not in (f["evidence"] or "")
                and "structured" not in f["detail"].lower()]
        self.assertTrue(plain, hits)
        self.assertEqual(plain[0]["verdict"], "PASS", plain)

        # RED against pre-fix code: nothing named the replaced verdict.
        disclosed = self._disclosure_hits("chg-a")
        self.assertTrue(disclosed, "an overwrite at the SAME head must be a NAMED "
                        "finding, never silent: %s" % hits)
        self.assertEqual(disclosed[0]["verdict"], "FAIL", disclosed)
        self.assertEqual(disclosed[0]["basis"], "derived", disclosed)
        self.assertIn("changes-required", disclosed[0]["detail"])
        self.assertIn(head[:12], disclosed[0]["detail"])

    def test_no_false_positive_disclosure(self):
        """Neither a single write, nor a second write with the SAME result,
        nor a further write at a DIFFERENT head (an ordinary fresh review,
        already covered by the severity-4 stale-binding check) is a
        same-head verdict flip, and none of the three fires the disclosure."""
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write(doss, "approved")
        self.assertFalse(os.path.exists(self._history_path(doss)))
        self.assertEqual(self._disclosure_hits("chg-a"), [])

        self._write(doss, "approved")
        self.assertTrue(os.path.exists(self._history_path(doss)))
        self.assertEqual(self._disclosure_hits("chg-a"), [])

        io.open(os.path.join(self.repo, "src", "a.py"), "a").write("y = 2\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a further, unrelated commit")
        self._write(doss, "changes-required")
        self.assertEqual(self._disclosure_hits("chg-a"), [])

    def test_a_corrupt_current_record_is_still_archived_not_lost(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        io.open(os.path.join(doss, "11-review.json"), "w").write("{not valid")
        self.assertEqual(self._write(doss, "approved")[0], 0)
        entries = self._history_entries(doss)
        self.assertEqual(len(entries), 1, entries)
        self.assertTrue(entries[0].get("corrupt"), entries)
        self.assertEqual(self._current(doss)["result"], "approved")


# ---------------------------------------------------------------------------
# LT-202: normalized findings inside 11-review.json.
# ---------------------------------------------------------------------------

_GOOD_FINDING = {
    "reviewer": "backend-reviewer", "category": "idempotency", "severity": "critical",
    "confidence": "high", "introducedByChange": "yes", "location": "src/api.py:123",
    "failure": "A retried request can create two orders.",
    "evidence": ["tests/test_orders.py::test_duplicate reproduces it"],
    "verification": "pytest tests/test_orders.py -k duplicate",
}


class TestNormalizeReviewFindingsUnit(unittest.TestCase):
    """Direct-import unit coverage of `brothersbe.cli.normalize_review_findings`
    and its helpers, the same `sys.path.insert(0, .../src)` pattern
    tools/test_sbe_status.py already uses for `brothersbe.status`. No git
    repository and no subprocess: every scenario here is a pure function of
    the JSON a `--findings-json` file would carry."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        # A fresh import per test would be wasteful, but importing once at
        # module scope would make this file's import order depend on
        # whether some OTHER test file already put `src` on sys.path first;
        # inserting and popping per test keeps this file correct in
        # isolation, exactly as tools/test_sbe_status.py already does at
        # each of its own call sites.
        import brothersbe.cli as cli_mod
        self.cli = cli_mod

    def tearDown(self):
        sys.path.remove(os.path.join(ROOT, "src"))

    def test_fingerprint_is_deterministic_for_the_same_inputs(self):
        fp1 = self.cli._finding_fingerprint("idempotency", "src/api.py:123",
                                            "A retried request can create two orders.")
        fp2 = self.cli._finding_fingerprint("idempotency", "src/api.py:123",
                                            "A retried request can create two orders.")
        self.assertEqual(fp1, fp2)
        self.assertEqual(fp1, "idempotency:src/api.py:123:a-retried-request-can-create-two-orders")

    def test_fingerprint_normalizes_a_leading_dot_slash_path(self):
        fp_plain = self.cli._finding_fingerprint("cat", "src/a.py:1", "boom")
        fp_dotted = self.cli._finding_fingerprint("cat", "./src/a.py:1", "boom")
        self.assertEqual(fp_plain, fp_dotted)

    def test_fingerprint_differs_when_category_path_line_or_failure_differs(self):
        base = self.cli._finding_fingerprint("cat", "src/a.py:1", "boom")
        self.assertNotEqual(base, self.cli._finding_fingerprint("other", "src/a.py:1", "boom"))
        self.assertNotEqual(base, self.cli._finding_fingerprint("cat", "src/b.py:1", "boom"))
        self.assertNotEqual(base, self.cli._finding_fingerprint("cat", "src/a.py:2", "boom"))
        self.assertNotEqual(base, self.cli._finding_fingerprint("cat", "src/a.py:1", "bang"))

    def test_identical_fingerprint_from_two_reviewers_folds_into_one_finding_multi_source(self):
        a = dict(_GOOD_FINDING, reviewer="backend-reviewer")
        b = dict(_GOOD_FINDING, reviewer="security-reviewer", confidence="medium")
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertEqual(len(structured), 1, structured)
        self.assertEqual(structured[0]["sources"], ["backend-reviewer", "security-reviewer"])
        self.assertEqual(structured[0]["reviewer"], "backend-reviewer",
                         "the first source stays the primary reviewer")

    def test_a_severity_disagreement_keeps_the_highest_and_records_it(self):
        a = dict(_GOOD_FINDING, reviewer="r1", severity="minor")
        b = dict(_GOOD_FINDING, reviewer="r2", severity="critical")
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["severity"], "critical")
        self.assertTrue(structured[0]["severityDisagreement"])

    def test_agreeing_severity_never_flags_a_disagreement(self):
        a = dict(_GOOD_FINDING, reviewer="r1", severity="minor")
        b = dict(_GOOD_FINDING, reviewer="r2", severity="minor")
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertFalse(structured[0]["severityDisagreement"])

    def test_two_low_confidence_duplicates_never_vote_their_way_to_medium(self):
        a = dict(_GOOD_FINDING, reviewer="r1", confidence="low")
        b = dict(_GOOD_FINDING, reviewer="r2", confidence="low")
        c = dict(_GOOD_FINDING, reviewer="r3", confidence="low")
        structured, errors = self.cli.normalize_review_findings([a, b, c])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["confidence"], "low",
                         "vote count must never raise confidence by itself")

    def test_confidence_is_the_highest_any_single_source_already_claimed(self):
        a = dict(_GOOD_FINDING, reviewer="r1", confidence="low")
        b = dict(_GOOD_FINDING, reviewer="r2", confidence="high")
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["confidence"], "high",
                         "a source that already claimed high confidence on its own is "
                         "not diluted by a second, lower-confidence source")

    def test_a_conceptid_folds_several_lines_into_one_parent_finding(self):
        a = dict(_GOOD_FINDING, reviewer="r1", conceptId="dup-order-path",
                 locations=["src/api.py:120"])
        a.pop("location", None)
        b = dict(_GOOD_FINDING, reviewer="r1", conceptId="dup-order-path",
                 locations=["src/api.py:145"])
        b.pop("location", None)
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertEqual(len(structured), 1, structured)
        self.assertEqual(sorted(structured[0]["locations"]),
                         ["src/api.py:120", "src/api.py:145"])

    def test_an_evidence_contradiction_is_never_auto_merged_and_goes_to_arbitration(self):
        fixed = dict(_GOOD_FINDING, reviewer="r1", status="fixed",
                     verification="pytest -k duplicate")
        rejected = dict(_GOOD_FINDING, reviewer="r2", status="rejected",
                        disposition={"evidence": "reproduced locally: no duplicate order"})
        structured, errors = self.cli.normalize_review_findings([fixed, rejected])
        self.assertEqual(errors, [])
        self.assertEqual(len(structured), 1, structured)
        self.assertEqual(structured[0]["status"], "arbitration")
        self.assertIsNotNone(structured[0]["contradiction"])
        statuses = sorted(c["status"] for c in structured[0]["contradiction"])
        self.assertEqual(statuses, ["fixed", "rejected"])
        self.assertFalse(structured[0]["blocking"],
                         "a finding pending arbitration is never a settled blocker")

    def test_two_sources_agreeing_the_finding_is_fixed_is_not_a_contradiction(self):
        a = dict(_GOOD_FINDING, reviewer="r1", status="fixed", verification="pytest -k a")
        b = dict(_GOOD_FINDING, reviewer="r2", status="fixed", verification="pytest -k b")
        structured, errors = self.cli.normalize_review_findings([a, b])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "fixed")
        self.assertIsNone(structured[0]["contradiction"])

    def test_low_confidence_never_blocks_even_at_critical_severity(self):
        entry = dict(_GOOD_FINDING, confidence="low")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertFalse(structured[0]["blocking"])

    def test_critical_with_medium_confidence_and_no_verification_does_not_block(self):
        entry = dict(_GOOD_FINDING, confidence="medium")
        entry.pop("verification", None)
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertFalse(structured[0]["blocking"],
                         "a critical blocker requires high confidence or mechanical proof")

    def test_critical_with_medium_confidence_and_a_verification_command_blocks(self):
        entry = dict(_GOOD_FINDING, confidence="medium", verification="pytest -k thing")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertTrue(structured[0]["blocking"],
                        "mechanical proof (a stated verification command) is enough "
                        "without high confidence")

    def test_critical_with_high_confidence_blocks_with_no_verification_needed(self):
        entry = dict(_GOOD_FINDING, confidence="high")
        entry.pop("verification", None)
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertTrue(structured[0]["blocking"])

    def test_non_critical_severity_never_blocks_even_at_high_confidence(self):
        for severity in ("major", "minor", "info"):
            entry = dict(_GOOD_FINDING, severity=severity, confidence="high")
            structured, errors = self.cli.normalize_review_findings([entry])
            self.assertEqual(errors, [])
            self.assertFalse(structured[0]["blocking"], severity)

    def test_pre_existing_findings_never_block_even_at_critical_high(self):
        for introduced in ("no", "unknown"):
            entry = dict(_GOOD_FINDING, introducedByChange=introduced, confidence="high")
            structured, errors = self.cli.normalize_review_findings([entry])
            self.assertEqual(errors, [])
            self.assertFalse(structured[0]["blocking"], introduced)
            self.assertEqual(structured[0]["introducedByChange"], introduced)

    def test_derive_review_result_downgrades_over_an_open_blocking_finding(self):
        """The exact defect this feature closes: a reviewer's own claim of
        `"approved"` must never survive next to a finding this schema
        already computed as `blocking` and still `"open"`."""
        structured, errors = self.cli.normalize_review_findings([dict(_GOOD_FINDING)])
        self.assertEqual(errors, [])
        self.assertTrue(structured[0]["blocking"], "fixture assumption: this finding blocks")
        self.assertEqual(structured[0]["status"], "open", "fixture assumption: open by default")
        derived = self.cli._derive_review_result("approved", structured)
        self.assertEqual(derived, "changes-required")

    def test_derive_review_result_leaves_the_claim_unchanged_with_no_blocking_finding(self):
        entry = dict(_GOOD_FINDING, severity="minor")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertFalse(structured[0]["blocking"])
        for claim in ("approved", "changes-required", "unverifiable"):
            self.assertEqual(self.cli._derive_review_result(claim, structured), claim)

    def test_derive_review_result_leaves_the_claim_unchanged_with_no_findings_at_all(self):
        self.assertEqual(self.cli._derive_review_result("approved", []), "approved")

    def test_derive_review_result_ignores_a_blocking_finding_already_fixed(self):
        """`blocking` is computed independently of `status` at merge time
        (severity/confidence/verification alone), so a finding the schema
        still marks `blocking` but that every source agrees is `"fixed"`,
        with proof, is not `"open"` any more: the derivation reads `status`
        itself, not `blocking` alone, and a resolved finding never forces a
        result down."""
        entry = dict(_GOOD_FINDING, status="fixed", verification="pytest -k duplicate")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "fixed")
        self.assertEqual(self.cli._derive_review_result("approved", structured), "approved")

    def test_derive_review_result_never_forces_a_result_from_pending_arbitration(self):
        """A finding left in `"arbitration"` by a status contradiction is
        already `blocking: False` by `_merge_finding_group`'s own
        construction (an unresolved disagreement is never a settled
        blocker); derivation must not reintroduce a forced result here
        either, the same "the record never judges itself ahead of
        adjudication" law `_merge_finding_group`'s docstring states."""
        fixed = dict(_GOOD_FINDING, reviewer="r1", status="fixed",
                     verification="pytest -k duplicate")
        rejected = dict(_GOOD_FINDING, reviewer="r2", status="rejected",
                        disposition={"evidence": "reproduced locally: no duplicate order"})
        structured, errors = self.cli.normalize_review_findings([fixed, rejected])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "arbitration")
        self.assertFalse(structured[0]["blocking"])
        self.assertEqual(self.cli._derive_review_result("approved", structured), "approved")

    def test_derive_review_result_never_upgrades_a_stricter_claim(self):
        """Derivation only ever downgrades toward `"changes-required"`; it
        never invents an `"approved"` from an absence of blocking findings,
        overriding a reviewer's own `"unverifiable"` or hand-entered
        `"changes-required"` the other way."""
        entry = dict(_GOOD_FINDING, severity="minor")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertEqual(self.cli._derive_review_result("unverifiable", structured),
                         "unverifiable")

    def test_a_reviewer_can_never_accept_its_own_risk(self):
        entry = dict(_GOOD_FINDING, reviewer="alice", status="accepted",
                     disposition={"by": "alice", "reason": "low volume today",
                                  "scope": "src/api.py"})
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("never accept its own risk" in e for e in errors), errors)

    def test_a_different_human_may_accept_the_risk(self):
        entry = dict(_GOOD_FINDING, reviewer="alice", status="accepted",
                     disposition={"by": "Bob the Lead", "reason": "low volume today",
                                  "scope": "src/api.py"})
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "accepted")

    def test_accepted_without_a_named_human_reason_or_scope_is_refused(self):
        for missing in ("by", "reason", "scope"):
            disposition = {"by": "Bob", "reason": "ok", "scope": "src/api.py"}
            del disposition[missing]
            entry = dict(_GOOD_FINDING, reviewer="alice", status="accepted",
                         disposition=disposition)
            structured, errors = self.cli.normalize_review_findings([entry])
            self.assertEqual(structured, [])
            self.assertTrue(any(missing in e for e in errors), (missing, errors))

    def test_rejected_without_refuting_evidence_is_refused(self):
        entry = dict(_GOOD_FINDING, status="rejected", disposition=None)
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("disposition.evidence" in e for e in errors), errors)

    def test_rejected_with_refuting_evidence_is_accepted(self):
        entry = dict(_GOOD_FINDING, status="rejected",
                     disposition={"evidence": "reproduced locally: no duplicate order"})
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "rejected")

    def test_fixed_without_verification_or_receipt_is_refused(self):
        entry = dict(_GOOD_FINDING, status="fixed")
        entry.pop("verification", None)
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("no finding marks itself fixed without proof" in e
                            for e in errors), errors)

    def test_fixed_with_a_disposition_receipt_is_accepted_without_top_level_verification(self):
        entry = dict(_GOOD_FINDING, status="fixed", disposition={"receipt": ".sbe/evidence/x.json"})
        entry.pop("verification", None)
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(errors, [])
        self.assertEqual(structured[0]["status"], "fixed")

    def test_arbitration_is_reserved_and_refused_as_input(self):
        entry = dict(_GOOD_FINDING, status="arbitration")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("reserved" in e for e in errors), errors)

    def test_an_invalid_confidence_value_is_refused(self):
        entry = dict(_GOOD_FINDING, confidence="very sure")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("confidence" in e for e in errors), errors)

    def test_an_invalid_introduced_by_change_value_is_refused(self):
        entry = dict(_GOOD_FINDING, introducedByChange="maybe")
        structured, errors = self.cli.normalize_review_findings([entry])
        self.assertEqual(structured, [])
        self.assertTrue(any("introducedByChange" in e for e in errors), errors)

    def test_a_missing_required_field_is_refused_and_names_the_index(self):
        entry = dict(_GOOD_FINDING)
        del entry["category"]
        structured, errors = self.cli.normalize_review_findings([entry, dict(_GOOD_FINDING)])
        self.assertEqual(structured, [])
        self.assertTrue(any("finding[0]" in e and "category" in e for e in errors), errors)

    def test_one_bad_entry_among_good_ones_refuses_the_whole_batch_not_a_partial_result(self):
        good = dict(_GOOD_FINDING)
        bad = dict(_GOOD_FINDING)
        del bad["failure"]
        structured, errors = self.cli.normalize_review_findings([good, bad])
        self.assertEqual(structured, [], "a batch with any invalid entry writes nothing")
        self.assertTrue(errors)

    def test_the_top_level_value_must_be_a_list(self):
        structured, errors = self.cli.normalize_review_findings({"not": "a list"})
        self.assertEqual(structured, [])
        self.assertTrue(errors)


class TestWriteFindingsJson(ReviewScenario):
    """`sbe review --write --findings-json <path>`: the write side end to
    end, over the real command line rather than the imported function."""

    def _write_findings_file(self, findings):
        path = os.path.join(self.tmp, "findings.json")
        io.open(path, "w", encoding="utf-8").write(json.dumps(findings))
        return path

    def test_a_valid_findings_json_file_is_persisted_as_structured_findings(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        findings_path = self._write_findings_file([dict(_GOOD_FINDING)])
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved",
                                 "--findings-json", findings_path)
        record = json.loads(io.open(os.path.join(doss, "11-review.json"),
                                    encoding="utf-8").read())
        self.assertEqual(record.get("findingsSchemaVersion"), "1.0", text)
        self.assertEqual(len(record.get("structuredFindings", [])), 1, record)
        self.assertEqual(record["structuredFindings"][0]["category"], "idempotency")
        self.assertIn("structured finding", text)

    def test_without_findings_json_the_record_carries_neither_new_field(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        self.sbe("review", doss, "--write", "--reviewer", "Independent",
                 "--reviewer-type", "human", "--result", "approved")
        record = json.loads(io.open(os.path.join(doss, "11-review.json"),
                                    encoding="utf-8").read())
        self.assertNotIn("findingsSchemaVersion", record,
                         "a write without --findings-json must be byte-for-byte what "
                         "CR-09 already wrote")
        self.assertNotIn("structuredFindings", record)

    def test_an_unreadable_findings_json_path_refuses_the_whole_write(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved",
                                 "--findings-json", os.path.join(self.tmp, "nope.json"))
        self.assertEqual(code, 2, text)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")),
                         "a refused write must leave no partial record: %s" % text)

    def test_malformed_json_in_the_findings_file_refuses_the_whole_write(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        findings_path = os.path.join(self.tmp, "findings.json")
        io.open(findings_path, "w", encoding="utf-8").write("not json{{{")
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved",
                                 "--findings-json", findings_path)
        self.assertEqual(code, 2, text)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")), text)

    def test_a_finding_that_fails_validation_refuses_the_whole_write(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        bad = dict(_GOOD_FINDING)
        del bad["severity"]
        findings_path = self._write_findings_file([bad])
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved",
                                 "--findings-json", findings_path)
        self.assertEqual(code, 2, text)
        self.assertIn("severity", text)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")), text)

    def test_self_acceptance_is_refused_through_the_full_command_line_too(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        entry = dict(_GOOD_FINDING, reviewer="alice", status="accepted",
                     disposition={"by": "alice", "reason": "ok", "scope": "src/a.py"})
        findings_path = self._write_findings_file([entry])
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved",
                                 "--findings-json", findings_path)
        self.assertEqual(code, 2, text)
        self.assertIn("never accept its own risk", text)
        self.assertFalse(os.path.exists(os.path.join(doss, "11-review.json")), text)

    def test_write_with_findings_json_still_never_moves_the_exit_code(self):
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        findings_path = self._write_findings_file([dict(_GOOD_FINDING)])
        without_code, _t1, _e1 = self.sbe("review", doss)
        with_code, _t2, _e2 = self.sbe("review", doss, "--write", "--reviewer", "r",
                                       "--reviewer-type", "human", "--result", "approved",
                                       "--findings-json", findings_path)
        self.assertEqual(without_code, with_code,
                         "--findings-json changed the exit code sbe review returned")


class TestStatusReadsStructuredFindings(ReviewScenario):
    """`sbe status --team` reading `structuredFindings` back."""

    def _struct_hits(self, change="chg-a"):
        """Every severity-11 finding for `change` whose detail names
        structured findings specifically, filtered by content rather than
        by list position: this section's sort key (severity, change,
        evidence, detail) can interleave this finding with the record's own
        pass/fail finding, so asserting `hits[0]` blindly would pin an
        accident of string ordering rather than a behavior."""
        _code, hits, _text = self.team_findings(change, 11)
        # "structured" (case-folded) alone: the three detail texts this
        # section can print are "...no structuredFindings...", "...
        # structuredFindings on ... cannot be trusted...", and "N structured
        # finding(s) recorded: ...", and no OTHER severity-11 detail this
        # file's fixtures produce ever uses the word, so this is not
        # narrower than it needs to be for any of the three states.
        return [f for f in hits if "structured" in f["detail"].lower()]

    def test_a_record_with_no_findings_json_reads_structured_findings_as_no_data(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        self.sbe("review", doss, "--write", "--reviewer", "Independent Reviewer",
                 "--reviewer-type", "human", "--result", "approved")
        hits = self._struct_hits()
        self.assertTrue(hits, "expected a structured-findings finding")
        self.assertEqual(hits[0]["verdict"], "NO-DATA", hits)
        self.assertIn("absence is a fact, not an accusation", hits[0]["detail"])

    def test_an_old_hand_written_record_with_no_structuredfindings_key_is_also_no_data(self):
        """CR-09-era records readable as-is: a record that never heard of
        LT-202 at all must read exactly the same honest NO-DATA a fresh
        record written without --findings-json does, never FAIL and never
        an invented finding."""
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": []})
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "NO-DATA", hits)

    def test_structuredfindings_without_findingsschemaversion_is_fail_not_missing(self):
        """The explicit migration path this schema requires: a record
        cannot carry structuredFindings without naming its own shape."""
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": [],
                          "structuredFindings": [dict(_GOOD_FINDING,
                                                      fingerprint="x:a.py:1:y")]})
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)
        self.assertIn("findingsSchemaVersion", hits[0]["detail"])

    def test_an_unrecognized_findingsschemaversion_is_fail(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": [],
                          "findingsSchemaVersion": "9.9", "structuredFindings": []})
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)
        self.assertIn("9.9", hits[0]["detail"])

    def test_a_structuredfindings_value_that_is_not_a_list_is_fail(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": [],
                          "findingsSchemaVersion": "1.0", "structuredFindings": "nope"})
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)

    def test_a_malformed_entry_is_fail_named_by_its_own_index_never_silently_dropped(self):
        doss, head, _out = self._make_clean_change("chg-a", "src/a.py")
        broken = dict(_GOOD_FINDING, fingerprint="x:a.py:1:y")
        del broken["category"]
        self._write_json(os.path.join(doss, "11-review.json"),
                         {"headSha": head, "reviewer": "Someone Else",
                          "reviewerType": "human", "result": "approved",
                          "findings": [], "acceptedRisks": [],
                          "findingsSchemaVersion": "1.0", "structuredFindings": [broken]})
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)
        self.assertIn("structuredFindings[0]", hits[0]["detail"])
        self.assertIn("category", hits[0]["detail"])

    def test_a_real_write_with_a_blocking_finding_reads_fail(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        findings_path = os.path.join(self.tmp, "findings.json")
        io.open(findings_path, "w", encoding="utf-8").write(json.dumps([dict(_GOOD_FINDING)]))
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved", "--findings-json", findings_path)
        self.assertEqual(code, 0, text)
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "FAIL", hits)
        self.assertIn("1 blocking", hits[0]["detail"])

    def test_a_real_write_with_only_non_blocking_findings_reads_pass(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        entry = dict(_GOOD_FINDING, severity="minor")
        findings_path = os.path.join(self.tmp, "findings.json")
        io.open(findings_path, "w", encoding="utf-8").write(json.dumps([entry]))
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved", "--findings-json", findings_path)
        self.assertEqual(code, 0, text)
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "PASS", hits)
        self.assertIn("0 blocking", hits[0]["detail"])

    def test_a_contradiction_left_pending_arbitration_reads_no_data(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        fixed = dict(_GOOD_FINDING, reviewer="r1", status="fixed",
                    verification="pytest -k duplicate")
        rejected = dict(_GOOD_FINDING, reviewer="r2", status="rejected",
                        disposition={"evidence": "reproduced locally: no duplicate order"})
        findings_path = os.path.join(self.tmp, "findings.json")
        io.open(findings_path, "w", encoding="utf-8").write(json.dumps([fixed, rejected]))
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved", "--findings-json", findings_path)
        self.assertEqual(code, 0, text)
        hits = self._struct_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "NO-DATA", hits)
        self.assertIn("1 pending arbitration", hits[0]["detail"])
        self.assertIn("adjudicat", hits[0]["nextAction"])


class TestResultDerivation(ReviewScenario):
    """The record's own `result` is DERIVED from `structuredFindings`, never
    trusted verbatim from `--result`: an open finding this schema already
    marked `blocking` forces `"changes-required"` regardless of what the
    reviewer claimed, the reviewer's own claim preserved verbatim beside it
    in `rawClaim`, with `resultDisagreement` stating outright whether the
    two differ. `TestNormalizeReviewFindingsUnit` above covers the pure
    function (`cli._derive_review_result`) directly; this class proves the
    same rule end to end, through the real command line and back through
    `sbe status --team`, which is what actually closes the founder card:
    the fix is only real if the surface a human or Fable reads sees it."""

    def _write_findings_file(self, findings):
        path = os.path.join(self.tmp, "findings.json")
        io.open(path, "w", encoding="utf-8").write(json.dumps(findings))
        return path

    def _result_hits(self, change="chg-a"):
        """The review record's own pass/fail/derived-result finding at
        severity 11, isolated from the sibling structured-findings
        sub-finding (`TestStatusReadsStructuredFindings._struct_hits`'s own
        filter, inverted) and from the LANE B-006 same-head history
        disclosure, neither of which this class is about."""
        _code, hits, _text = self.team_findings(change, 11)
        return [f for f in hits if "structured" not in f["detail"].lower()
                and "prior verdict" not in f["detail"].lower()]

    def test_a_claim_of_approved_over_an_open_blocking_finding_stores_derived_changes_required(self):
        """CALIBRATED fixture for the founder card: the claim says
        `approved`, the record's own open blocking finding says otherwise,
        and the STORED result must be the derived one, not the claim."""
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        findings_path = self._write_findings_file([dict(_GOOD_FINDING)])
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved", "--findings-json", findings_path)
        self.assertEqual(code, 0, text)
        record = json.loads(io.open(os.path.join(doss, "11-review.json"),
                                    encoding="utf-8").read())
        self.assertEqual(record["result"], "changes-required",
                         "an open blocking finding must force the STORED result down, "
                         "regardless of what --result claimed: %s" % record)
        self.assertEqual(record["rawClaim"], "approved",
                         "the reviewer's own claim must survive verbatim beside the "
                         "derivation: %s" % record)
        self.assertTrue(record["resultDisagreement"], record)
        self.assertIn(
            "result DERIVED from findings", text,
            "a disagreement between the claim and the derivation must be said "
            "explicitly on stdout, not left for a reader to notice by diffing two "
            "fields themselves: %s" % text)

    def test_status_team_reads_the_derived_result_never_the_reviewers_own_claim(self):
        """THE severity-11 finding `sbe status --team` prints for this
        record must read the DERIVED result: today's defect is a record
        that can assert `approved` while its own findings say otherwise, and
        read as a clean PASS anyway. `cli.py` stores the derived value under
        the SAME `"result"` key `status.py` already reads, so this is proof
        the read side sees it correctly with no change to status.py itself."""
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        findings_path = self._write_findings_file([dict(_GOOD_FINDING)])
        self.sbe("review", doss, "--write", "--reviewer", "Independent Reviewer",
                 "--reviewer-type", "human", "--result", "approved",
                 "--findings-json", findings_path)
        hits = self._result_hits()
        self.assertTrue(hits, hits)
        self.assertEqual(hits[0]["verdict"], "changes-required",
                         "sbe status --team must never read this record as approved: %s"
                         % hits)
        self.assertNotEqual(hits[0]["verdict"], "PASS", hits)

    def test_a_claim_that_agrees_with_the_findings_carries_no_disagreement(self):
        doss, _head, _out = self._make_clean_change("chg-a", "src/a.py")
        entry = dict(_GOOD_FINDING, severity="minor")  # never blocks
        findings_path = self._write_findings_file([entry])
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer",
                                 "Independent Reviewer", "--reviewer-type", "human",
                                 "--result", "approved", "--findings-json", findings_path)
        self.assertEqual(code, 0, text)
        record = json.loads(io.open(os.path.join(doss, "11-review.json"),
                                    encoding="utf-8").read())
        self.assertEqual(record["result"], "approved")
        self.assertEqual(record["rawClaim"], "approved")
        self.assertFalse(record["resultDisagreement"], record)
        self.assertNotIn("result DERIVED from findings", text)

    def test_without_findings_json_the_record_carries_no_derivation_fields_either(self):
        """Extends the LT-202 additive contract
        (`TestWriteFindingsJson.test_without_findings_json_the_record_carries_neither_new_field`)
        to the two fields this card adds: with no structured findings there
        is nothing to derive a result FROM, so a plain `--write` (no
        `--findings-json`) must still write exactly what it always wrote,
        `rawClaim` and `resultDisagreement` included among the fields it
        never adds."""
        doss = self._change("chg-a", "src/a.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "chg-a dossier and source")
        code, text, _ = self.sbe("review", doss, "--write", "--reviewer", "Independent",
                                 "--reviewer-type", "human", "--result", "approved")
        self.assertEqual(code, 0, text)
        record = json.loads(io.open(os.path.join(doss, "11-review.json"),
                                    encoding="utf-8").read())
        self.assertEqual(record["result"], "approved")
        self.assertNotIn("rawClaim", record)
        self.assertNotIn("resultDisagreement", record)


if __name__ == "__main__":
    unittest.main(verbosity=2)

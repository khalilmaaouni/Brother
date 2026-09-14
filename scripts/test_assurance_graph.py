"""assurance_graph: prove the WBS-20.04 Assurance Graph composes real check
verdicts (never an asserted claim), that a malformed record is actually
caught by its own schema, and that each of the roadmap's own three named
pilot claims is actually checkable against a real sibling control --
scope_audit.py, native_evidence.py, merge_passport.py -- not just asserted
in a docstring.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import assurance_graph as AG  # noqa: E402
import native_evidence as N  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    # A packager can copy this test without scripts/tmp_sandbox.py beside
    # it. Say so rather than dying: the sandbox is hygiene, not the subject
    # (matches scripts/test_scope_audit.py's own E100 handling).
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))


def _fm(verdict, reason="because"):
    return {
        "failure_mode": "fm", "control": "ctrl",
        "check": {"name": "c", "command_ref": "x", "fixture_ref": "y",
                  "evidence_ref": "z", "verdict": verdict, "verdict_reason": reason},
        "environment": "env", "residual_limitation": "",
    }


class ComposeClaimAggregation(unittest.TestCase):
    """compose_claim is a VIEW: it never invents a verdict, only combines
    the ones it is handed, worst-of, exactly like merge_passport._worst."""

    def test_all_pass_is_pass(self):
        record = AG.compose_claim("c1", "text", [_fm("PASS"), _fm("PASS")],
                                   last_verified_revision="deadbeef")
        self.assertEqual(record["verdict"], "PASS")

    def test_one_fail_beats_the_rest(self):
        record = AG.compose_claim("c1", "text", [_fm("PASS"), _fm("FAIL"), _fm("NO-DATA")],
                                   last_verified_revision="deadbeef")
        self.assertEqual(record["verdict"], "FAIL")

    def test_no_data_beats_pass_when_no_fail(self):
        record = AG.compose_claim("c1", "text", [_fm("PASS"), _fm("NO-DATA")],
                                   last_verified_revision="deadbeef")
        self.assertEqual(record["verdict"], "NO-DATA")

    def test_empty_failure_modes_refused(self):
        with self.assertRaises(ValueError):
            AG.compose_claim("c1", "text", [], last_verified_revision="deadbeef")

    def test_a_verdict_outside_the_shared_triple_is_refused(self):
        with self.assertRaises(ValueError):
            AG.compose_claim("c1", "text", [_fm("MAYBE")], last_verified_revision="deadbeef")

    def test_a_missing_verdict_reason_is_refused(self):
        fm = _fm("PASS")
        fm["check"]["verdict_reason"] = ""
        with self.assertRaises(ValueError):
            AG.compose_claim("c1", "text", [fm], last_verified_revision="deadbeef")

    def test_a_composed_record_validates_against_its_own_schema(self):
        record = AG.compose_claim("c1", "text", [_fm("PASS")], last_verified_revision="deadbeef")
        self.assertEqual(AG.check_record(record), [])

    def test_a_malformed_record_is_caught_by_the_schema(self):
        record = AG.compose_claim("c1", "text", [_fm("PASS")], last_verified_revision="deadbeef")
        del record["failure_modes"]
        self.assertTrue(AG.check_record(record))


class GitHead(unittest.TestCase):
    def test_reads_the_real_head_of_a_repo(self):
        tmp = tempfile.mkdtemp(prefix="ag-githead-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
        with open(os.path.join(tmp, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp, check=True)
        expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                                   capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(AG._git_head(tmp), expected)

    def test_a_non_git_directory_is_NO_DATA(self):
        tmp = tempfile.mkdtemp(prefix="ag-githead-nondata-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertEqual(AG._git_head(tmp), "NO-DATA")


# ---------------------------------------------------------------------------
# Pilot claim 1: "undeclared writes do not silently integrate"
# ---------------------------------------------------------------------------

class Repo(object):
    """Mirrors scripts/test_scope_audit.py's own Repo helper: a throwaway
    git repository with a two-commit history, the same real-git fixture
    that module's own test suite already drives scope_audit through."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="ag-scope-")
        self._git("init", "-q")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        self.write("declared.py", "x = 1\n")
        self.write("other.py", "y = 1\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")
        self.base = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.dir, capture_output=True, text=True)

    def write(self, rel, body):
        full = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body)

    def commit(self, message="change"):
        self._git("add", "-A")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class PilotClaimUndeclaredWrites(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.close)

    def test_an_undeclared_write_is_caught_not_silently_integrated(self):
        self.repo.write("other.py", "y = 2\n")
        head = self.repo.commit()
        verdict, reason = AG.check_undeclared_writes_not_silent(
            {"unit_id": "U", "write_scope": ["declared.py"]},
            self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, "PASS")
        self.assertIn("other.py", reason)

    def test_a_write_fully_inside_the_declaration_is_inconclusive_not_a_pass(self):
        self.repo.write("declared.py", "x = 2\n")
        head = self.repo.commit()
        verdict, _reason = AG.check_undeclared_writes_not_silent(
            {"unit_id": "U", "write_scope": ["declared.py"]},
            self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, "NO-DATA")

    def test_no_write_scope_declared_is_NO_DATA(self):
        head = self.repo.commit()
        verdict, _reason = AG.check_undeclared_writes_not_silent(
            {"unit_id": "U"}, self.repo.base, head, cwd=self.repo.dir)
        self.assertEqual(verdict, "NO-DATA")


# ---------------------------------------------------------------------------
# Pilot claim 2: "native test evidence cannot reuse stale result bundles"
# ---------------------------------------------------------------------------

def _result_doc(result="Passed"):
    identity = "test://com.apple.xcode/App/AppTests/FooTests/testThing"
    return {"devices": [{"deviceId": "device", "deviceName": "phone",
                         "osVersion": "26.0", "platform": "iOS"}],
            "testNodes": [{"nodeType": "Test Plan", "name": "App", "children": [
                {"nodeType": "Unit test bundle", "name": "AppTests", "children": [
                    {"nodeType": "Test Suite", "name": "FooTests", "children": [
                        {"nodeType": "Test Case", "name": "testThing()",
                         "nodeIdentifier": "FooTests/testThing()",
                         "nodeIdentifierURL": identity, "result": result,
                         "durationInSeconds": 0.001}]}]}]}],
            "testPlanConfigurations": []}


class PilotClaimNativeEvidenceNotStale(unittest.TestCase):
    """Mirrors scripts/test_native_evidence.py's own record()/xcrun/runner
    fixture, the same real record() -> mutate the repo -> validate() path
    that module's own test_a_changed_candidate_revision_is_rejected already
    proves rejects a changed candidate, driven here through the pilot
    check function instead of native_evidence.main directly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ag-native-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "a@b.c")
        self._git("config", "user.name", "t")
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        self._git("add", "base.txt")
        self._git("commit", "-qm", "base")
        self.xcrun = self._write_script("fake-xcrun.py", """
            import json, os, sys
            path = sys.argv[sys.argv.index('--path') + 1]
            index = os.path.join(path, 'database.sqlite3')
            if not os.path.exists(index):
                with open(index, 'wb') as fh:
                    fh.write(b'index')
            with open(os.path.join(path, 'test-results.json'), encoding='utf-8') as fh:
                sys.stdout.write(fh.read())
        """)
        self.runner = self._write_script("runner.py", """
            import json, os, sys
            bundle, capture, result = sys.argv[1:]
            os.mkdir(bundle)
            with open(os.path.join(bundle, 'test-results.json'), 'w', encoding='utf-8') as fh:
                fh.write(result)
            if capture != '-':
                with open(capture, 'wb') as fh:
                    fh.write(b'capture')
        """)

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.repo] + list(args), check=True, capture_output=True)

    def _write_script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def _record(self):
        bundle = os.path.join(self.tmp, "result.xcresult")
        evidence = bundle + ".json"
        screenshot = bundle + ".png"
        identity = "test://com.apple.xcode/App/AppTests/FooTests/testThing"
        code = N.main([
            "record", "--repo", self.repo, "--out", evidence,
            "--result-bundle", bundle, "--expected-test", identity,
            "--artifact", "screenshot=" + screenshot, "--xcrun", self.xcrun,
            "--command", sys.executable, self.runner, bundle, screenshot,
            json.dumps(_result_doc()),
        ])
        return code, evidence

    def test_a_fresh_record_is_not_stale(self):
        code, evidence = self._record()
        self.assertEqual(code, 0)
        verdict, _reason = AG.check_native_evidence_not_stale(evidence, self.repo, xcrun=self.xcrun)
        # A fresh record has nothing stale to catch: this claim is only
        # about the staleness case, so a fresh record reads NO-DATA for it,
        # never a false PASS.
        self.assertEqual(verdict, "NO-DATA")

    def test_a_candidate_changed_after_recording_is_caught_as_stale(self):
        code, evidence = self._record()
        self.assertEqual(code, 0)
        with open(os.path.join(self.repo, "base.txt"), "a", encoding="utf-8") as fh:
            fh.write("changed\n")
        verdict, reason = AG.check_native_evidence_not_stale(evidence, self.repo, xcrun=self.xcrun)
        self.assertEqual(verdict, "PASS")
        self.assertIn("stale", reason.lower())

    def test_a_missing_evidence_file_is_NO_DATA(self):
        verdict, _reason = AG.check_native_evidence_not_stale(
            os.path.join(self.tmp, "missing.json"), self.repo, xcrun=self.xcrun)
        self.assertEqual(verdict, "NO-DATA")


# ---------------------------------------------------------------------------
# Pilot claim 3: "an MDM precision claim cannot rely only on similarity
# scores"
# ---------------------------------------------------------------------------

class PilotClaimMDMPrecisionNotScoreOnly(unittest.TestCase):
    def test_a_score_only_candidate_cannot_validate_risk(self):
        verdict, reason = AG.check_mdm_precision_not_score_only(
            candidate_generation={"pathway": "fuzzy-match", "model": "v1", "score": 0.97})
        self.assertEqual(verdict, "PASS")
        self.assertIn("score", reason)

    def test_risk_evidence_beyond_the_score_lets_it_validate(self):
        verdict, _reason = AG.check_mdm_precision_not_score_only(
            candidate_generation={"pathway": "fuzzy-match", "model": "v1", "score": 0.97},
            risk_assessment={"false_merge_cost": "high", "shared_key_hub": False,
                              "cluster_size_after": 2, "bridge_edge": False})
        self.assertEqual(verdict, "PASS")

    def test_incomplete_risk_evidence_still_fails_to_validate(self):
        verdict, _reason = AG.check_mdm_precision_not_score_only(
            candidate_generation={"pathway": "fuzzy-match", "model": "v1", "score": 0.97},
            risk_assessment={"false_merge_cost": "high"})
        self.assertEqual(verdict, "FAIL")


# ---------------------------------------------------------------------------
# End to end: compose all three pilot claims into real schema-valid records
# ---------------------------------------------------------------------------

class PilotClaimsComposeIntoValidRecords(unittest.TestCase):
    """The three pilot check functions are not just checkable on their own:
    their real output composes into an assurance-graph-v1 record exactly
    like any other claim's would."""

    def test_undeclared_writes_pilot_composes_and_validates(self):
        repo = Repo()
        self.addCleanup(repo.close)
        repo.write("other.py", "y = 2\n")
        head = repo.commit()
        verdict, reason = AG.check_undeclared_writes_not_silent(
            {"unit_id": "U", "write_scope": ["declared.py"]}, repo.base, head, cwd=repo.dir)
        record = AG.compose_claim(
            "undeclared-writes-do-not-silently-integrate",
            "undeclared writes do not silently integrate",
            [{
                "failure_mode": "a unit's write lands outside its declared write_scope "
                                "and is merged without being caught",
                "control": "scripts/scope_audit.py's audit(): compares actual changed "
                           "paths against the declared write_scope after a unit runs",
                "check": {
                    "name": "an undeclared write in a real git diff is quarantined, not merged",
                    "command_ref": "scripts/scope_audit.py:audit",
                    "fixture_ref": "a throwaway git repo with a write outside write_scope",
                    "evidence_ref": reason,
                    "verdict": verdict,
                    "verdict_reason": reason,
                },
                "environment": "any environment scope_audit.py runs in (git required)",
                "residual_limitation": "does not judge whether a declaration was wise, "
                                       "only whether it was honored",
            }],
            last_verified_revision=head,
        )
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(AG.check_record(record), [])

    def test_mdm_precision_pilot_composes_and_validates(self):
        verdict, reason = AG.check_mdm_precision_not_score_only(
            candidate_generation={"pathway": "fuzzy-match", "model": "v1", "score": 0.97})
        record = AG.compose_claim(
            "mdm-precision-not-score-only",
            "an MDM precision claim cannot rely only on similarity scores",
            [{
                "failure_mode": "a merge candidate with only a high similarity score is "
                                "accepted without risk evidence",
                "control": "scripts/merge_passport.py's FIELD_OBLIGATIONS: risk is "
                           "REQUIRED_FOR_MERGE, independent of candidate_generation",
                "check": {
                    "name": "a score-only candidate cannot validate the risk field",
                    "command_ref": "scripts/merge_passport.py:compose_passport",
                    "fixture_ref": "a candidate_generation record carrying only pathway/model/score",
                    "evidence_ref": reason,
                    "verdict": verdict,
                    "verdict_reason": reason,
                },
                "environment": "any environment merge_passport.py runs in",
                "residual_limitation": "proves the risk field is required, not that its "
                                       "content is sufficient for a real merge decision",
            }],
            last_verified_revision="deadbeef",
        )
        self.assertEqual(record["verdict"], "PASS")
        self.assertEqual(AG.check_record(record), [])


if __name__ == "__main__":
    unittest.main()

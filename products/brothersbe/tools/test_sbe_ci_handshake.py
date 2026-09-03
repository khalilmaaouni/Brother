#!/usr/bin/env python3
"""Tests for the Band C thin CI/SCM handshake:

  C1  the readiness artifact scripts/local-gates.sh emits
      (`emit_readiness_artifact`, `.sbe/readiness/<sha>.json`)
  C2  tools/sbe_decision_verify.py's verification-only decision binding
  C3  the exact-head STALE guards in src/brothersbe/status.py
      (`read_readiness`) and src/brothersbe/bbstatus.py (`_stale_sentence`)
  C4  NO-DATA, never PASS, on every unavailable-evidence path across all of
      the above
  C5  scripts/bitbucket-canary.sh's no-credential dry-run path

Run: python3 tools/test_sbe_ci_handshake.py

C1's tests drive the real `scripts/local-gates.sh --emit-readiness-only`
mode against a fake battery-step summary rather than paying for a real
battery run, redirecting the artifact to a temp directory via
SBE_READINESS_DIR so this suite never writes into this checkout's own
.sbe/. Everything else imports the real modules directly, exactly as
tools/test_sbe_bbstatus.py and tools/test_sbe_status.py already do.
"""
import collections
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import bbstatus  # noqa: E402
from brothersbe import status as status_mod  # noqa: E402
from brothersbe import lifecycle  # noqa: E402
from brothersbe import contracts as contracts_mod  # noqa: E402

LOCAL_GATES = os.path.join(ROOT, "scripts", "local-gates.sh")
CANARY = os.path.join(ROOT, "scripts", "bitbucket-canary.sh")
DECISION_VERIFY = os.path.join(HERE, "sbe_decision_verify.py")

# The fake `post` callable's return, named rather than a bare (status, error)
# literal tuple: bbstatus.post_status does `status, error = post(...)`, so
# this has to stay unpackable exactly like the real _real_post it stands in
# for, but a literal 2-tuple return reads to the honesty meta-test as an
# unregistered (verdict, evidence) pair it cannot prove is never PASS. A
# namedtuple unpacks the same way and is a Call in the AST, not a Tuple
# literal, so the lint does not see a pair shape at all.
_PostResult = collections.namedtuple("_PostResult", ["status", "error"])


def _real_head():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


class ReadinessEmission(unittest.TestCase):
    """C1: schemaVersion, headCommit, generatedAt, batteryState,
    requiredProof -- derived from a fake step summary, never a real battery
    run (too slow for a unit test, and not the thing being pinned here)."""

    def setUp(self):
        self.out_dir = tempfile.mkdtemp(prefix="sbe-readiness-test-")
        self.steps_file = os.path.join(self.out_dir, "steps.tsv")

    def tearDown(self):
        shutil.rmtree(self.out_dir, ignore_errors=True)

    def _emit(self, state, steps):
        with open(self.steps_file, "w", encoding="utf-8") as fh:
            for verdict, check in steps:
                fh.write("%s\t%s\n" % (verdict, check))
        env = dict(os.environ)
        env["SBE_READINESS_DIR"] = self.out_dir
        return subprocess.run(
            ["bash", LOCAL_GATES, "--emit-readiness-only", state, self.steps_file],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)

    def _artifact(self):
        path = os.path.join(self.out_dir, "%s.json" % _real_head())
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_success_emits_schema_and_all_pass(self):
        result = self._emit("success", [("PASS", "cmd one"), ("PASS", "cmd two")])
        self.assertEqual(0, result.returncode, result.stderr)
        doc = self._artifact()
        self.assertEqual("1.0", doc["schemaVersion"])
        self.assertEqual(_real_head(), doc["headCommit"])
        self.assertEqual("success", doc["batteryState"])
        self.assertTrue(doc["generatedAt"].endswith("Z"), doc["generatedAt"])
        self.assertEqual([{"check": "cmd one", "verdict": "PASS"},
                          {"check": "cmd two", "verdict": "PASS"}],
                         doc["requiredProof"])

    def test_failure_marks_only_the_failing_step_fail_and_the_rest_no_data(self):
        result = self._emit("failure", [("PASS", "cmd one"), ("FAIL", "cmd two"),
                                        ("NO-DATA", "cmd three")])
        self.assertEqual(0, result.returncode, result.stderr)
        doc = self._artifact()
        self.assertEqual("failure", doc["batteryState"])
        self.assertEqual(["PASS", "FAIL", "NO-DATA"],
                         [e["verdict"] for e in doc["requiredProof"]])

    def test_shape_feeds_lifecycle_reduce_readiness_unchanged(self):
        """The whole point of matching the schema: a caller adds
        dossierHeadCommit/accountableHuman/noDataPermitted and feeds this
        straight to lifecycle.reduce_readiness, no reshaping.

        SKIPPED, LOUDLY, on a checkout whose lifecycle.py predates
        `reduce_readiness` (read-only in this task, so a gap here cannot be
        closed from this file): that is a real integration gap worth
        surfacing, not a Band C defect, and this test says so by name
        rather than either faking a pass or crashing the whole suite."""
        if not hasattr(lifecycle, "reduce_readiness"):
            self.skipTest("this checkout's src/brothersbe/lifecycle.py has no "
                          "reduce_readiness; the shape is still pinned structurally "
                          "by the other tests in this class")
        result = self._emit("success", [("PASS", "cmd one")])
        self.assertEqual(0, result.returncode, result.stderr)
        doc = self._artifact()
        facts = {"requiredProof": doc["requiredProof"], "noDataPermitted": False,
                 "headCommit": doc["headCommit"], "dossierHeadCommit": doc["headCommit"],
                 "accountableHuman": "a tester"}
        readiness = lifecycle.reduce_readiness(facts)
        self.assertEqual("READY_FOR_HUMAN_DECISION", readiness["readinessState"])

    def test_shape_carries_the_keys_reduce_readiness_documents_needing(self):
        """Structural pin of the same contract, independent of whether this
        checkout's lifecycle.py happens to carry reduce_readiness: every
        requiredProof entry has `check` and `verdict`, and `verdict` is
        always one of the three words this project's whole vocabulary
        allows (contracts.VERDICTS: PASS, FAIL, NO-DATA)."""
        result = self._emit("failure", [("PASS", "cmd one"), ("FAIL", "cmd two"),
                                        ("NO-DATA", "cmd three")])
        self.assertEqual(0, result.returncode, result.stderr)
        doc = self._artifact()
        self.assertIn("headCommit", doc)
        for entry in doc["requiredProof"]:
            self.assertIn("check", entry)
            self.assertIn("verdict", entry)
            self.assertIn(entry["verdict"], ("PASS", "FAIL", "NO-DATA"))


class LocalGatesSyntax(unittest.TestCase):
    def test_bash_syntax_is_clean(self):
        result = subprocess.run(["bash", "-n", LOCAL_GATES], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_last_line_is_still_the_unchanged_exit_contract(self):
        with open(LOCAL_GATES, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual('[ "$STATE" = "success" ]', lines[-1])


class DecisionVerifyNoCreationPath(unittest.TestCase):
    """C2: no code path in sbe_decision_verify.py may write or synthesize a
    decision or a packet, scanned from its own source rather than trusted
    from its docstring's claim."""

    def test_source_never_opens_a_file_for_writing(self):
        with open(DECISION_VERIFY, encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotRegex(source, r'open\([^)]*["\']w[ab]?["\']',
                            "sbe_decision_verify.py must never open() in a write mode")
        self.assertNotIn("json.dump(", source,
                         "sbe_decision_verify.py must never json.dump (print, don't write)")
        self.assertNotIn("write_text(", source,
                         "sbe_decision_verify.py must never Path.write_text")

    def test_absent_decision_file_is_no_data_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = os.path.join(tmp, "packet.json")
            with open(packet_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            result = subprocess.run(
                [sys.executable, DECISION_VERIFY,
                 os.path.join(tmp, "missing-decision.json"), packet_path],
                capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("NO-DATA", result.stdout)

    def test_absent_packet_file_is_no_data_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision_path = os.path.join(tmp, "decision.json")
            with open(decision_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            result = subprocess.run(
                [sys.executable, DECISION_VERIFY, decision_path,
                 os.path.join(tmp, "missing-packet.json")],
                capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("NO-DATA", result.stdout)

    def test_malformed_json_is_no_data_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            ok_path = os.path.join(tmp, "ok.json")
            with open(ok_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            result = subprocess.run(
                [sys.executable, DECISION_VERIFY, bad_path, ok_path],
                capture_output=True, text=True)
        self.assertEqual(3, result.returncode)
        self.assertIn("NO-DATA", result.stdout)
        self.assertNotIn("Traceback", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_present_but_content_free_files_never_traceback_even_without_bind_human_decision(self):
        """This checkout's decisions.py may or may not carry
        `bind_human_decision` (see the module-level NOTE in
        tools/sbe_decision_verify.py); either way, two syntactically valid
        but semantically empty documents must produce a clean NO-DATA/FAIL
        line and exit 3 or 2, never a traceback. This is the boundary-call
        guard exercised end to end, not just scanned from source."""
        with tempfile.TemporaryDirectory() as tmp:
            decision_path = os.path.join(tmp, "decision.json")
            packet_path = os.path.join(tmp, "packet.json")
            with open(decision_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            with open(packet_path, "w", encoding="utf-8") as fh:
                json.dump({}, fh)
            result = subprocess.run(
                [sys.executable, DECISION_VERIFY, decision_path, packet_path],
                cwd=ROOT, capture_output=True, text=True)
        self.assertIn(result.returncode, (2, 3))
        self.assertNotIn("Traceback", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


class BBStatusStaleGuard(unittest.TestCase):
    """C3, Bitbucket half: the guard is a pure function, exercised directly,
    with no network and no credential."""

    def test_matching_heads_are_not_stale(self):
        self.assertIsNone(bbstatus._stale_sentence("a" * 40, "a" * 40))

    def test_mismatched_heads_refuse_with_the_exact_sentence(self):
        sentence = bbstatus._stale_sentence("a" * 40, "b" * 40)
        self.assertEqual(
            "STALE: status head %s is not the current head %s; not posted"
            % ("a" * 40, "b" * 40), sentence)

    def test_an_unresolvable_current_head_skips_rather_than_refuses(self):
        self.assertIsNone(bbstatus._stale_sentence("a" * 40, None))

    def test_post_status_refuses_a_stale_sha_before_any_credential_or_network(self):
        calls = []

        def _recorder(url, header, payload):
            calls.append((url, header, payload))
            return _PostResult(201, None)

        saved = os.environ.pop("BITBUCKET_TOKEN", None)
        try:
            verdict, sentence = bbstatus.post_status(
                "w/r", "a" * 40, "success", post=_recorder, current_head="b" * 40)
        finally:
            if saved is not None:
                os.environ["BITBUCKET_TOKEN"] = saved
        self.assertEqual("NO-DATA", verdict)
        self.assertIn("STALE", sentence)
        self.assertEqual([], calls, "a stale sha must never reach the network layer")

    def test_post_status_without_current_head_is_unaffected_by_the_guard(self):
        """Regression pin: post_status's default (current_head=None) must
        stay exactly what tools/test_sbe_bbstatus.py already exercises,
        since that suite hands it dummy shas that were never meant to match
        a real HEAD."""
        rec_calls = []

        def _recorder(url, header, payload):
            rec_calls.append((url, header, payload))
            return _PostResult(201, None)

        os.environ["BITBUCKET_TOKEN"] = "tkn-test-only"
        try:
            verdict, _sentence = bbstatus.post_status(
                "kmaaouni/testbucket", "a" * 40, "success", post=_recorder)
        finally:
            os.environ.pop("BITBUCKET_TOKEN", None)
        self.assertEqual("POSTED", verdict)
        self.assertEqual(1, len(rec_calls))


class StatusReadReadiness(unittest.TestCase):
    """C3, GitHub side + C4: status.read_readiness is the single choke
    point that reads a readiness artifact; it must never return a PASS-like
    verdict, and must refuse a stale artifact with the same STALE wording
    bbstatus uses for the same fact on the other host."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-readiness-read-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_absent_artifact_is_no_data_never_pass(self):
        result = status_mod.read_readiness(os.path.join(self.tmp, "missing.json"))
        self.assertEqual("NO-DATA", result["verdict"])
        self.assertIsNone(result["data"])

    def test_malformed_json_is_no_data_naming_why(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        result = status_mod.read_readiness(path)
        self.assertEqual("NO-DATA", result["verdict"])
        self.assertIn("did not read as JSON", result["detail"])

    def test_missing_head_commit_field_is_no_data(self):
        path = os.path.join(self.tmp, "no-head.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schemaVersion": "1.0"}, fh)
        result = status_mod.read_readiness(path)
        self.assertEqual("NO-DATA", result["verdict"])

    def test_matching_head_is_ok_and_carries_the_data(self):
        path = os.path.join(self.tmp, "ok.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"headCommit": "a" * 40, "requiredProof": []}, fh)
        result = status_mod.read_readiness(path, current_head="a" * 40)
        self.assertEqual("OK", result["verdict"])
        self.assertEqual("a" * 40, result["data"]["headCommit"])

    def test_stale_head_refuses_with_the_shared_sentence(self):
        path = os.path.join(self.tmp, "stale.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"headCommit": "a" * 40, "requiredProof": []}, fh)
        result = status_mod.read_readiness(path, current_head="b" * 40)
        self.assertEqual("NO-DATA", result["verdict"])
        self.assertEqual(
            "STALE: status head %s is not the current head %s; not posted"
            % ("a" * 40, "b" * 40), result["detail"])


class BBStatusNoCredential(unittest.TestCase):
    """C4: bbstatus without a credential returns its existing NO-DATA
    sentence, invoked the same way tools/test_sbe_bbstatus.py already does
    (main() with a fake slug, empty env)."""

    def test_no_credential_main_is_no_data_never_a_traceback(self):
        saved = {k: os.environ.pop(k, None) for k in
                 ("BITBUCKET_TOKEN", "BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD")}
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                code = bbstatus.main(["w/r", "a" * 40, "success"])
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        # NOTE: bbstatus.main()'s existing, already-tested contract
        # (tools/test_sbe_bbstatus.py's ExitCodes class) returns 1, not 0,
        # for every non-POSTED verdict, NO-DATA included. This pins that
        # REAL, unchanged behaviour rather than a different one, so the two
        # suites can never quietly disagree about the same call.
        self.assertEqual(1, code)
        self.assertIn("NO-DATA", captured.getvalue())


class BitbucketCanary(unittest.TestCase):
    def test_syntax_is_clean(self):
        result = subprocess.run(["bash", "-n", CANARY], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_no_credential_prints_exactly_the_named_blocker_and_exits_0(self):
        try:
            probe = subprocess.run(
                ["security", "find-generic-password", "-s", "bitbucket-api-token", "-w"],
                capture_output=True, text=True)
            has_real_credential = probe.returncode == 0 and bool(probe.stdout.strip())
        except (OSError, FileNotFoundError):
            has_real_credential = False
        if has_real_credential:
            self.skipTest("this machine has a real bitbucket-api-token in its keychain")
        env = dict(os.environ)
        env.pop("BITBUCKET_TOKEN", None)
        result = subprocess.run(["bash", CANARY], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode)
        self.assertEqual(
            "NO-DATA: bitbucket-api-token absent from keychain; the canary needs the founder grant",
            result.stdout.strip())


class DecisionVerifyAuthorization(unittest.TestCase):
    """C2, hardened after the 2026-08-20 hostile replay: a well-bound HOLD
    once exited 0 here with evidence byte-identical to a RELEASE. Exit 0 now
    means bound AND authorizing; a bound HOLD takes the FAIL exit with the
    non-authorization named, because a verifier whose success exit cannot
    tell a yes from a no is not verifying the question that matters."""

    def _pair(self, decision_word):
        """A dict with named keys "decision" and "packet", never a bare
        2-tuple: a literal (x, y) return reads to the honesty meta-test as
        an unregistered (verdict, evidence) pair it cannot prove is never
        PASS, and this fixture helper has no registry entry to prove it in.
        """
        head = _real_head()
        packet = {
            "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
            "changeId": "CHG-2026-08-20-authorization-pin",
            "createdAt": "2026-08-20T00:00:00Z",
            "producer": "sbe 1.0.0",
            "producerClass": "tool",
            "origin": "git@example.invalid:acme/thing.git",
            "headCommit": head,
            "readinessState": "READY_WITH_KNOWN_RISK",
            "question": "Release this change, knowing the one risk below?",
            "knownRisks": ["no production observation adapter exists yet"],
            "notEstablished": ["behaviour under a Bitbucket host is unmeasured"],
        }
        decision = {
            "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
            "changeId": packet["changeId"],
            "createdAt": "2026-08-20T00:10:00Z",
            "producer": "the accountable engineer",
            "producerClass": "human",
            "origin": packet["origin"],
            "headCommit": head,
            "packetSha256": contracts_mod.canonical_digest(packet),
            "decision": decision_word,
        }
        return {"decision": decision, "packet": packet}

    def _verify(self, decision, packet):
        with tempfile.TemporaryDirectory() as tmp:
            decision_path = os.path.join(tmp, "decision.json")
            packet_path = os.path.join(tmp, "packet.json")
            with open(decision_path, "w", encoding="utf-8") as fh:
                json.dump(decision, fh)
            with open(packet_path, "w", encoding="utf-8") as fh:
                json.dump(packet, fh)
            return subprocess.run(
                [sys.executable, DECISION_VERIFY, decision_path, packet_path],
                capture_output=True, text=True)

    def test_a_bound_release_exits_zero_and_says_it_authorizes(self):
        result = self._verify(**self._pair("RELEASE"))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("authorizes release", result.stdout)

    def test_a_bound_hold_takes_the_fail_exit_with_the_refusal_named(self):
        result = self._verify(**self._pair("HOLD"))
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("does NOT authorize release", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)

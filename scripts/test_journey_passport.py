#!/usr/bin/env python3
"""Tests for journey_passport.py (WBS-30.06).

Feeds real outputs from the real sibling modules (mobile_journey_contract,
mobile_reference_lock, mobile_design, native_evidence_v2, device_matrix)
into compose_passport(), rather than hand-typing fake evidence dicts that do
not match what those modules actually return. Mirrors
test_merge_passport.py's own test shape.
"""
import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import journey_passport as jp  # noqa: E402
import mobile_journey_contract as MJC  # noqa: E402
import mobile_reference_lock as MRL  # noqa: E402
import mobile_design as MD  # noqa: E402
import native_evidence as N  # noqa: E402
import native_evidence_v2 as NEV2  # noqa: E402
import device_matrix as DM  # noqa: E402
import contract_check as CC  # noqa: E402
from evidence_obligation import VERDICTS  # noqa: E402
from test_mobile_journey_contract import VALID_OUTCOME, BASE_JOURNEY  # noqa: E402
from test_mobile_reference_lock import SYNTHETIC_OBSERVATION  # noqa: E402
from test_native_evidence_v2 import IDENTITY, result_doc  # noqa: E402
from test_device_matrix import REAL_DEVICECTL_OUTPUT  # noqa: E402

SCRIPT = os.path.join(HERE, "journey_passport.py")


class ComposeFixtureMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="journey-passport-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # -- journey contract fixture (WBS-30.01), mirrors
        # test_mobile_journey_contract.py's own fixture shape.
        self.outcome_path = os.path.join(self.tmp, "outcome.json")
        with open(self.outcome_path, "w", encoding="utf-8") as fh:
            json.dump(VALID_OUTCOME, fh)
        CC.load_json(MJC.DEFAULT_SCHEMA, "journey schema")  # fails loud if the schema file moved

        # -- native_evidence_v2 fixture (WBS-30.05), mirrors
        # test_native_evidence_v2.py's own git-repo + fake-xcrun builder.
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "a@b.c")
        self.git("config", "user.name", "t")
        with open(os.path.join(self.repo, "base.txt"), "w", encoding="utf-8") as fh:
            fh.write("base\n")
        self.git("add", "base.txt")
        self.git("commit", "-qm", "base")
        self.xcrun = self.write_script("fake-xcrun.py", """
            import json, os, sys
            path = sys.argv[sys.argv.index('--path') + 1]
            index = os.path.join(path, 'database.sqlite3')
            if not os.path.exists(index):
                with open(index, 'wb') as fh:
                    fh.write(b'index')
            with open(os.path.join(path, 'test-results.json'), encoding='utf-8') as fh:
                sys.stdout.write(fh.read())
        """)
        self.runner = self.write_script("runner.py", """
            import json, os, sys
            bundle, capture, result = sys.argv[1:]
            os.mkdir(bundle)
            with open(os.path.join(bundle, 'test-results.json'), 'w', encoding='utf-8') as fh:
                fh.write(result)
            if capture != '-':
                with open(capture, 'wb') as fh:
                    fh.write(b'capture')
        """)

        # -- mobile_design board fixture (WBS-30.03), mirrors
        # test_mobile_design.py's DesignEvidenceAdapterTests setUp.
        self.asset = Path(self.tmp) / "screen.png"
        self.asset.write_bytes(b"fixture media")
        self.board_path = Path(self.tmp) / "board.json"
        self.board_data = {"schema": MD.SCHEMA, "screens": [
            {"id": "first", "app": "Sample", "title": "Open reflection", "flow": "reflection", "step": 1,
             "source": "file:///owned/reference.png", "observed_at": "2026-09-12", "notes": "Quiet entry",
             "elements": ["sheet"], "media": MD.digest(self.asset), "device_class": "phone",
             "locale": "en-US", "accessibility_observation": "VoiceOver reads the sheet title first",
             "rationale": "Anchors the entry-state layout decision"}]}
        self.board_path.write_text(json.dumps(self.board_data))

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo] + list(args), check=True, capture_output=True)

    def write_script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def journey_contract(self, **overrides):
        rec = dict(BASE_JOURNEY, outcome_contract_ref=self.outcome_path)
        rec.update(overrides)
        return rec

    def reference_lock(self, bundle_id="com.example.App"):
        obs_path = os.path.join(self.tmp, "obs.json")
        with open(obs_path, "w", encoding="utf-8") as fh:
            json.dump(SYNTHETIC_OBSERVATION, fh)
        return MRL.partial_reference(obs_path, bundle_id)

    def native_evidence_v2(self, result=None, tag="a"):
        bundle = os.path.join(self.tmp, "result-%s.xcresult" % tag)
        evidence = bundle + ".json"
        screenshot = bundle + ".png"
        result = result if result is not None else result_doc()
        args = ["record", "--repo", self.repo, "--out", evidence,
                "--result-bundle", bundle, "--expected-test", IDENTITY,
                "--artifact", "screenshot=" + screenshot, "--xcrun", self.xcrun,
                "--command", sys.executable, self.runner, bundle, screenshot, json.dumps(result)]
        code = N.main(args)
        return code, NEV2.wrap_v2(evidence, repo=self.repo, xcrun=self.xcrun)

    def physical_device_evidence(self, present=True):
        if present:
            with patch("device_matrix.run", return_value=(REAL_DEVICECTL_OUTPUT, None)):
                return DM.physical_device_evidence(DM.ADAPTER_LOCAL)
        return DM.physical_device_evidence(DM.ADAPTER_FARM)

    def design_evidence(self):
        return MD.evidence_record(str(self.board_path), "first")

    def design_staleness(self, evidence=None):
        evidence = evidence or self.design_evidence()
        evidence_path = Path(self.tmp) / "evidence.json"
        evidence_path.write_text(json.dumps(evidence))
        return MD.check_staleness(str(evidence_path), str(self.board_path))

    def accessibility(self):
        return {"checks": [{"name": "voiceover_labels", "status": "PASS"}]}

    def performance(self):
        return {"checks": [{"name": "cold_start_ms", "status": "PASS"}]}

    def human_acceptance(self):
        return {"checks": [{"name": "reviewer_signoff", "status": "PASS"}]}

    def release(self):
        # release_state_tracker.py's real shape: "states", not "state" --
        # this used to be the wrong flat shape, caught by
        # scripts/canary_pipeline_smoke.py running the real pipeline end to
        # end. Real states can never all be PASS live (4 of 5 need an App
        # Store Connect credential this session doesn't have), so this
        # fixture is synthetic on purpose, for the all-PASS composition case.
        import release_state_tracker as _RST
        return {
            "schema": "brother-release-state-record-v1",
            "states": {name: {"schema": "brother-release-state-evidence-v1",
                               "state": name, "verdict": "PASS",
                               "evidence": {"reason": "synthetic fixture"},
                               "observed_at": "2026-09-13T00:00:00+00:00"}
                       for name in _RST.STATES},
            "order": list(_RST.STATES),
            "caveat": "synthetic fixture, all states forced PASS for this test only",
            "observed_at": "2026-09-13T00:00:00+00:00",
        }

    def production(self):
        return {"claim_ids": ["claim-001"]}

    def compose_all(self, **overrides):
        _, native_v2 = self.native_evidence_v2()
        kwargs = dict(
            journey_contract=self.journey_contract(),
            reference_lock=self.reference_lock(),
            native_evidence_v2_record=native_v2,
            physical_device_evidence=self.physical_device_evidence(),
            design_evidence=self.design_evidence(),
            design_staleness=self.design_staleness(),
            accessibility_evidence=self.accessibility(),
            performance_evidence=self.performance(),
            human_acceptance_evidence=self.human_acceptance(),
            release_evidence=self.release(),
            production_claims=self.production(),
        )
        kwargs.update(overrides)
        return jp.compose_passport(**kwargs)


class FullCompositionTests(ComposeFixtureMixin, unittest.TestCase):
    def test_full_composition_from_real_sibling_outputs_is_all_pass(self):
        passport = self.compose_all()
        for field in ("contract", "build_identity", "functional", "accessibility", "performance",
                      "physical_device", "release", "production"):
            self.assertEqual(passport[field]["verdict"], "PASS", field)
        self.assertEqual(passport["visual"]["capture_integrity"]["verdict"], "PASS")
        self.assertEqual(passport["visual"]["human_acceptance"]["verdict"], "PASS")
        completeness = passport["completeness"]
        self.assertEqual(completeness["total_dimensions"], 10)
        self.assertEqual(completeness["failed_count"], 0)
        self.assertEqual(completeness["no_data_count"], 0)
        self.assertEqual(completeness["headline"], "ALL 10 DIMENSION(S) ASSESSED: PASS.")

    def test_journey_and_candidate_revision_are_derived_never_bare_parameters(self):
        passport = self.compose_all()
        self.assertEqual(passport["journey"], BASE_JOURNEY["journey_id"])
        self.assertEqual(len(passport["candidate_revision"]), 40)  # a real git revision sha
        params = jp.compose_passport.__code__.co_varnames[:jp.compose_passport.__code__.co_argcount]
        self.assertNotIn("journey", params)
        self.assertNotIn("candidate_revision", params)

    def test_contract_reads_from_the_real_mobile_journey_contract_check(self):
        passport = self.compose_all()
        self.assertEqual(passport["contract"]["journey_id"], "j1")
        self.assertEqual(passport["contract"]["verdict"], "PASS")

    def test_build_identity_reflects_the_honest_partial_shape(self):
        passport = self.compose_all()
        record = passport["build_identity"]["record"]
        self.assertEqual(record["artifact_identity"]["status"], "observed_installed")
        self.assertEqual(record["source_provenance"]["status"], "UNRESOLVED")
        self.assertEqual(passport["build_identity"]["bundle_id"], "com.example.App")
        self.assertEqual(passport["build_identity"]["verdict"], "PASS")

    def test_functional_reads_the_real_native_evidence_v2_verdict(self):
        _, native_v2 = self.native_evidence_v2(tag="fn")
        passport = self.compose_all(native_evidence_v2_record=native_v2)
        self.assertEqual(passport["functional"]["verdict"], native_v2["verdict"])
        self.assertEqual(passport["functional"]["proof_scope"], native_v2["proof_scope"])


class MissingRequiredEvidenceTests(ComposeFixtureMixin, unittest.TestCase):
    def test_missing_journey_contract_is_no_data_not_silent_pass(self):
        passport = self.compose_all(journey_contract=None)
        self.assertEqual(passport["contract"]["verdict"], "NO-DATA")
        self.assertEqual(passport["journey"], "NO-DATA")

    def test_missing_reference_lock_is_no_data_not_silent_pass(self):
        passport = self.compose_all(reference_lock=None)
        self.assertEqual(passport["build_identity"]["verdict"], "NO-DATA")

    def test_missing_native_evidence_v2_is_no_data_and_candidate_revision_is_no_data(self):
        passport = self.compose_all(native_evidence_v2_record=None)
        self.assertEqual(passport["functional"]["verdict"], "NO-DATA")
        self.assertEqual(passport["candidate_revision"], "NO-DATA")

    def test_missing_physical_device_evidence_is_no_data(self):
        passport = self.compose_all(physical_device_evidence=None)
        self.assertEqual(passport["physical_device"]["verdict"], "NO-DATA")

    def test_missing_design_evidence_is_no_data_capture_integrity(self):
        passport = self.compose_all(design_evidence=None, design_staleness=None)
        self.assertEqual(passport["visual"]["capture_integrity"]["verdict"], "NO-DATA")

    def test_several_no_data_dimensions_cannot_be_mistaken_for_all_pass(self):
        """The marquee defense against the Muse hostile review's own named
        failure mode: a reader skimming PASS/PASS/PASS must not be able to
        miss that several dimensions carry no evidence at all."""
        passport = self.compose_all(
            accessibility_evidence=None, performance_evidence=None, release_evidence=None)
        self.assertEqual(passport["accessibility"]["verdict"], "NO-DATA")
        self.assertEqual(passport["performance"]["verdict"], "NO-DATA")
        self.assertEqual(passport["release"]["verdict"], "NO-DATA")

        completeness = passport["completeness"]
        self.assertEqual(completeness["no_data_count"], 3)
        for name in ("accessibility", "performance", "release"):
            self.assertIn(name, completeness["no_data"])
            self.assertNotIn(name, completeness["passed"])
            # named in the headline itself, not just counted in a total
            self.assertIn(name, completeness["headline"])

        # a genuinely still-good dimension must not be swallowed into
        # looking incomplete, and the headline must not be readable as PASS
        self.assertEqual(passport["functional"]["verdict"], "PASS")
        self.assertIn("functional", completeness["passed"])
        self.assertTrue(completeness["headline"].startswith("INCOMPLETE"))
        self.assertNotIn("ASSESSED: PASS.", completeness["headline"])
        self.assertEqual(completeness["failed_count"], 0)

    def test_a_failed_dimension_takes_headline_priority_over_no_data(self):
        passport = self.compose_all(
            accessibility_evidence=None,
            reference_lock=dict(self.reference_lock(), schema="not-the-right-schema"))
        self.assertEqual(passport["build_identity"]["verdict"], "FAIL")
        self.assertEqual(passport["accessibility"]["verdict"], "NO-DATA")
        completeness = passport["completeness"]
        self.assertTrue(completeness["headline"].startswith("BLOCKING FAILURE"))
        self.assertIn("build_identity", completeness["headline"])
        # the NO-DATA dimension must still be named even though a FAIL exists
        self.assertIn("accessibility", completeness["headline"])


class MalformedEvidenceTests(ComposeFixtureMixin, unittest.TestCase):
    def test_reference_lock_with_resolved_provenance_fails_not_a_silent_pass(self):
        tampered = copy.deepcopy(self.reference_lock())
        tampered["source_provenance"]["status"] = "VERIFIED"
        passport = self.compose_all(reference_lock=tampered)
        self.assertEqual(passport["build_identity"]["verdict"], "FAIL")
        self.assertIn("UNRESOLVED", passport["build_identity"]["verdict_reason"])

    def test_journey_contract_missing_required_field_fails(self):
        broken = self.journey_contract()
        del broken["required_native_tests"]
        passport = self.compose_all(journey_contract=broken)
        self.assertEqual(passport["contract"]["verdict"], "FAIL")

    def test_accessibility_missing_checks_key_fails(self):
        passport = self.compose_all(accessibility_evidence={"note": "no checks list"})
        self.assertEqual(passport["accessibility"]["verdict"], "FAIL")

    def test_production_missing_claim_ids_key_fails(self):
        passport = self.compose_all(production_claims={"note": "forgot claim_ids"})
        self.assertEqual(passport["production"]["verdict"], "FAIL")


class HollowPassDefenseTests(ComposeFixtureMixin, unittest.TestCase):
    """The concrete defense against a passport that looks complete but
    misrepresents what it actually composed from."""

    def test_unchanged_evidence_verifies_as_a_match(self):
        contract = self.journey_contract()
        passport = self.compose_all(journey_contract=contract)
        result = jp.verify_passport_evidence(passport, journey_contract=contract)
        self.assertEqual(result["checks"]["contract"], "MATCH")
        self.assertEqual(result["verdict"], "PASS")

    def test_tampered_journey_contract_is_refused(self):
        contract = self.journey_contract()
        passport = self.compose_all(journey_contract=contract)
        forged = dict(contract, human_outcome="a different outcome entirely")
        result = jp.verify_passport_evidence(passport, journey_contract=forged)
        self.assertIn("MISMATCH", result["checks"]["contract"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_tampered_reference_lock_is_refused(self):
        reference = self.reference_lock()
        passport = self.compose_all(reference_lock=reference)
        forged = copy.deepcopy(reference)
        forged["artifact_identity"]["version"] = "99.9"
        result = jp.verify_passport_evidence(passport, reference_lock=forged)
        self.assertIn("MISMATCH", result["checks"]["build_identity"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_tampered_native_evidence_v2_is_refused(self):
        _, native_v2 = self.native_evidence_v2(tag="tamper")
        passport = self.compose_all(native_evidence_v2_record=native_v2)
        forged = copy.deepcopy(native_v2)
        forged["proof_scope"] = forged["proof_scope"] + ["a claim that was never actually proven"]
        result = jp.verify_passport_evidence(passport, native_evidence_v2_record=forged)
        self.assertIn("MISMATCH", result["checks"]["functional"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_tampered_accessibility_evidence_is_refused(self):
        accessibility = self.accessibility()
        passport = self.compose_all(accessibility_evidence=accessibility)
        forged = {"checks": [{"name": "voiceover_labels", "status": "FAIL"}]}
        result = jp.verify_passport_evidence(passport, accessibility_evidence=forged)
        self.assertIn("MISMATCH", result["checks"]["accessibility"])
        self.assertEqual(result["verdict"], "FAIL")

    def test_no_evidence_supplied_to_verify_is_no_data_not_a_pass(self):
        passport = self.compose_all()
        result = jp.verify_passport_evidence(passport)
        self.assertEqual(result["verdict"], "NO-DATA")

    def test_digest_recorded_at_compose_time_is_sha256_hex(self):
        passport = self.compose_all()
        digest = passport["accessibility"]["digest"]
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # raises ValueError if not hex


class CLITests(ComposeFixtureMixin, unittest.TestCase):
    def write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True)

    def test_cli_full_composition_exits_zero(self):
        _, native_v2 = self.native_evidence_v2(tag="cli")
        contract_path = self.write("contract.json", self.journey_contract())
        reference_path = self.write("reference.json", self.reference_lock())
        native_v2_path = self.write("native_v2.json", native_v2)
        device_path = self.write("device.json", self.physical_device_evidence())
        design_path = self.write("design.json", self.design_evidence())
        staleness_path = self.write("staleness.json", self.design_staleness())
        accessibility_path = self.write("accessibility.json", self.accessibility())
        performance_path = self.write("performance.json", self.performance())
        acceptance_path = self.write("acceptance.json", self.human_acceptance())
        release_path = self.write("release.json", self.release())
        production_path = self.write("production.json", self.production())

        result = self.run_cli(
            "--journey-contract", contract_path, "--reference-lock", reference_path,
            "--native-evidence-v2", native_v2_path, "--physical-device-evidence", device_path,
            "--design-evidence", design_path, "--design-staleness", staleness_path,
            "--accessibility", accessibility_path, "--performance", performance_path,
            "--human-acceptance", acceptance_path, "--release", release_path,
            "--production", production_path,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertEqual(passport["completeness"]["failed_count"], 0)
        self.assertEqual(passport["completeness"]["no_data_count"], 0)

    def test_cli_missing_contract_file_is_no_data(self):
        result = self.run_cli(
            "--journey-contract", os.path.join(self.tmp, "missing.json"),
            "--reference-lock", os.path.join(self.tmp, "missing.json"),
            "--native-evidence-v2", os.path.join(self.tmp, "missing.json"),
            "--physical-device-evidence", os.path.join(self.tmp, "missing.json"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO-DATA", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Calibration for tools/bm_vault_assertions.py, WBS row VB3-05.

The property under test is the row's own sentence, driven backwards:
conflicting source-of-record and casual assertions coexist (neither is ever
deleted); a query for current scoped truth returns the resolution winner
while exposing the conflict; a different scope returns the other winner
when its own resolution says so; an as-of query returns the PRIOR
resolution once a newer one has landed; a resolution carries a recorded
approval, and a tampered one invalidates and falls back to the authority
comparator, saying so; an unresolved entity reference fails check; either
missing store is NO-DATA.

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_assertions as ba  # noqa: E402

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

ENTITY_A = "n-aaaaaaaaaaaaaaaa"
ENTITY_B = "n-bbbbbbbbbbbbbbbb"


def entity_note(entity_id, etype="project"):
    return "---\nid: %s\nentity: %s\n---\n\n# an entity\n" % (entity_id, etype)


def run(argv):
    """(exit_code, stdout), mirroring test_bm_vault_cite.py's own run()."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ba.main(argv)
    return code, buf.getvalue()


class AssertionsCalibration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-assertions-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "30-Entities"))
        with open(os.path.join(self.vault, "30-Entities", "a.md"),
                  "w", encoding="utf-8") as fh:
            fh.write(entity_note(ENTITY_A))
        with open(os.path.join(self.vault, "30-Entities", "b.md"),
                  "w", encoding="utf-8") as fh:
            fh.write(entity_note(ENTITY_B))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mint_assertion(self, subject=ENTITY_A, predicate="hq_location",
                        value="Tokyo", authority="casual", source="a.md",
                        **extra):
        argv = ["mint-assertion", "--vault", self.vault, "--subject", subject,
                "--predicate", predicate, "--value", value,
                "--authority", authority, "--source", source]
        for k, v in extra.items():
            argv += ["--" + k.replace("_", "-"), v]
        code, out = run(argv)
        self.assertEqual(code, 0, out)
        return out.strip().split()[1]  # "minted <id> -> <path>"

    def _mint_resolution(self, winner, subject=ENTITY_A, predicate="hq_location",
                         scope="APAC", valid_from="2026-01-01", valid_to=None,
                         approver="khalil", role="founder", reason="ruling",
                         policy_version="v1"):
        argv = ["mint-resolution", "--vault", self.vault, "--subject", subject,
                "--predicate", predicate, "--winner", winner, "--scope", scope,
                "--valid-from", valid_from, "--approver", approver,
                "--role", role, "--reason", reason,
                "--policy-version", policy_version]
        if valid_to:
            argv += ["--valid-to", valid_to]
        code, out = run(argv)
        self.assertEqual(code, 0, out)
        return out.strip().split()[1]

    def _tamper_resolution(self, resolution_id, **fields):
        path = ba.resolutions_path(self.vault)
        with open(path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        for rec in records:
            if rec["id"] == resolution_id:
                rec.update(fields)
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    # -- coexistence ---------------------------------------------------

    def test_conflicting_assertions_coexist_neither_deleted(self):
        id_a = self._mint_assertion(value="Tokyo", authority="casual")
        id_b = self._mint_assertion(value="Osaka", authority="source_of_record")
        records = ba._read_records(ba.assertions_path(self.vault))
        ids = {r["id"] for r in records}
        self.assertIn(id_a, ids)
        self.assertIn(id_b, ids)
        self.assertEqual(len(records), 2, "neither conflicting assertion was deleted")

    # -- scoped truth exposes the conflict ------------------------------

    def test_truth_returns_winner_and_exposes_conflict(self):
        self._mint_assertion(value="Tokyo", authority="casual")
        self._mint_assertion(value="Osaka", authority="source_of_record")
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location"])
        self.assertEqual(code, 0, out)
        self.assertIn("WINNER value='Osaka'", out)
        self.assertIn("value='Tokyo'", out, "the losing side must still be exposed")
        self.assertIn("value='Osaka'", out)
        self.assertIn("CONFLICT", out)

    # -- a resolution wins even against a higher-authority assertion ----

    def test_resolution_winner_beats_authority_in_its_own_scope(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        self._mint_assertion(value="Osaka", authority="source_of_record")
        self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location", "--scope", "APAC"])
        self.assertEqual(code, 0, out)
        self.assertIn("WINNER value='Tokyo'", out)
        self.assertIn("via resolution", out)
        self.assertIn("value='Osaka'", out, "conflict stays exposed under a resolution too")

    # -- a different scope returns the OTHER winner ---------------------

    def test_different_scope_returns_the_other_winner(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        id_osaka = self._mint_assertion(value="Osaka", authority="source_of_record")
        self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        self._mint_resolution(id_osaka, scope="EMEA", valid_from="2026-01-01")
        code_a, out_a = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                             "--predicate", "hq_location", "--scope", "APAC"])
        code_b, out_b = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                             "--predicate", "hq_location", "--scope", "EMEA"])
        self.assertEqual((code_a, code_b), (0, 0))
        self.assertIn("WINNER value='Tokyo'", out_a)
        self.assertIn("WINNER value='Osaka'", out_b)

    # -- as-of returns the PRIOR resolution ------------------------------

    def test_as_of_returns_the_prior_resolution_after_a_newer_one_lands(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        id_osaka = self._mint_assertion(value="Osaka", authority="source_of_record")
        self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        self._mint_resolution(id_osaka, scope="APAC", valid_from="2026-08-01")
        # Before the newer resolution's own valid_from: the prior one answers.
        code_before, out_before = run(
            ["truth", "--vault", self.vault, "--subject", ENTITY_A,
             "--predicate", "hq_location", "--scope", "APAC",
             "--as-of", "2026-03-01"])
        # On/after: the newer resolution answers, and the prior one is never
        # deleted, only superseded for later dates.
        code_after, out_after = run(
            ["truth", "--vault", self.vault, "--subject", ENTITY_A,
             "--predicate", "hq_location", "--scope", "APAC",
             "--as-of", "2026-08-01"])
        self.assertEqual((code_before, code_after), (0, 0))
        self.assertIn("WINNER value='Tokyo'", out_before)
        self.assertIn("WINNER value='Osaka'", out_after)
        records = ba._read_records(ba.resolutions_path(self.vault))
        self.assertEqual(len(records), 2, "the prior resolution is never deleted")

    # -- a resolution carries a recorded, verifiable approval ------------

    def test_resolution_carries_a_verified_approval(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01",
                              approver="khalil", role="founder", reason="ruling")
        records = ba._read_records(ba.resolutions_path(self.vault))
        self.assertEqual(len(records), 1)
        sib = ba._siblings()
        self.assertTrue(sib["lifecycle"].verify_approval(records[0]["approval"]))
        self.assertEqual(records[0]["approval"]["approver"], "khalil")

    # -- a tampered approval invalidates and falls back ------------------

    def test_tampered_resolution_falls_back_to_authority_comparator(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        id_osaka = self._mint_assertion(value="Osaka", authority="source_of_record")
        res_id = self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        # Hand-edit the winner after approval: the approval's own artifact_hash
        # was computed over the ORIGINAL winner, so it no longer matches.
        self._tamper_resolution(res_id, winner=id_osaka)
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location", "--scope", "APAC"])
        self.assertEqual(code, 0, out)
        self.assertIn("APPROVAL INVALID", out)
        self.assertIn("falling back to the authority comparator", out)
        self.assertIn("WINNER value='Osaka'", out,
                      "the comparator, not the tampered resolution, must decide")
        check_code, check_out = run(["check", "--vault", self.vault])
        self.assertEqual(check_code, 1)
        self.assertIn("does not match its approved artifact_hash", check_out)

    def test_tampered_approval_record_itself_fails_verification(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        res_id = self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        path = ba.resolutions_path(self.vault)
        with open(path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        for rec in records:
            if rec["id"] == res_id:
                rec["approval"]["approver"] = "someone-else"
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location", "--scope", "APAC"])
        self.assertEqual(code, 0, out)
        self.assertIn("APPROVAL INVALID", out)
        self.assertIn("record_hash mismatch", out)

    # -- an unresolved entity reference fails check ----------------------

    def _touch_empty_resolutions_store(self):
        """check requires BOTH stores to exist (NO-DATA otherwise); an empty
        resolutions store is a legitimate zero-record state, distinct from
        an absent one, so tests that only exercise assertions touch it."""
        path = ba.resolutions_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass

    def test_unresolved_entity_reference_fails_check(self):
        self._mint_assertion(subject="n-deadbeefdeadbeef", value="x", authority="casual")
        self._touch_empty_resolutions_store()
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 1)
        self.assertIn("unresolved entity reference", out)

    def test_subject_pointing_at_a_document_not_an_entity_fails_check(self):
        with open(os.path.join(self.vault, "plain.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nid: n-cccccccccccccccc\ntype: reference\n---\n\nplain\n")
        self._mint_assertion(subject="n-cccccccccccccccc", value="x", authority="casual")
        self._touch_empty_resolutions_store()
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 1)
        self.assertIn("unresolved entity reference", out)

    def test_clean_store_passes_check(self):
        id_tokyo = self._mint_assertion(value="Tokyo", authority="casual")
        self._mint_resolution(id_tokyo, scope="APAC", valid_from="2026-01-01")
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, out)
        self.assertNotIn("FINDINGS", out)

    # -- NO-DATA on missing stores ---------------------------------------

    def test_check_is_no_data_on_missing_stores(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2)

    def test_truth_is_no_data_on_missing_assertions_store(self):
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location"])
        self.assertEqual(code, 2)

    def test_check_is_no_data_when_only_resolutions_missing(self):
        self._mint_assertion(value="Tokyo", authority="casual")
        os.remove(ba.resolutions_path(self.vault)) if os.path.exists(
            ba.resolutions_path(self.vault)) else None
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2)

    # -- an unrankable authority is excluded, never silently ranked -----

    def test_unrankable_authority_is_excluded_from_the_comparator(self):
        self._mint_assertion(value="Tokyo", authority="casual")
        # Hand-craft a garbage-authority assertion directly (the CLI itself
        # refuses an unknown --authority at mint time).
        path = ba.assertions_path(self.vault)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": "as-0000000000000000", "subject": ENTITY_A,
                "predicate": "hq_location", "value": "Kyoto",
                "authority": "made-up", "lifecycle": "candidate",
                "source_locator": "x",
            }) + "\n")
        code, out = run(["truth", "--vault", self.vault, "--subject", ENTITY_A,
                         "--predicate", "hq_location"])
        self.assertEqual(code, 0, out)
        self.assertIn("unrankable authority", out)
        self.assertIn("WINNER value='Tokyo'", out)


if __name__ == "__main__":
    unittest.main()

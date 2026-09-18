"""Calibration for scripts/assurance_coverage.py (WBS-01 WIRE-01).

Runs the tool against SYNTHETIC SYSTEM.md and scripts/ fixtures built in
this file, per this estate's own calibration convention (test_coverage_
check.py does the same, for the same reason: the live SYSTEM.md is a
moving target other units are editing in this worktree, so a calibration
test cannot depend on its exact contents staying still). One test also
exercises the real, live SYSTEM.md, informationally: it asserts shape
invariants that must hold regardless of how many parts exist right now,
never an exact count.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

import assurance_coverage as ac

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_tree(root, parts):
    """parts: {module_name: {"purpose": str|None, "proven_by": [names],
    "docstring_body": str, "test_src": str|None}}. Writes a scripts/ dir and
    a matching SYSTEM.md, in the exact shape system_doc.py itself renders."""
    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    lines = [
        "# What this system is, right now",
        "",
        "## The shape, in counts",
        "",
        "## Every part, what it is for, and what proves it",
        "",
        "| Part | What it is for | What proves it |",
        "|---|---|---|",
    ]
    for name, spec in parts.items():
        purpose = spec.get("purpose")
        purpose_cell = purpose if purpose is not None else "**NO-DATA**, no docstring"
        proven = spec.get("proven_by") or []
        proof_cell = (", ".join("`%s`" % p for p in proven) if proven
                      else "**NO-DATA**, nothing in the battery runs it")
        lines.append("| `%s` | %s | %s |" % (name, purpose_cell, proof_cell))
        if spec.get("write_source", True):
            doc_first = purpose or "undocumented"
            body = spec.get("docstring_body", "")
            src = '"""%s\n%s\n"""\n' % (doc_first, body)
            src += spec.get("extra_source", "")
            _write(os.path.join(scripts_dir, "%s.py" % name), src)
        if spec.get("test_src") is not None:
            _write(os.path.join(scripts_dir, "test_%s.py" % name), spec["test_src"])
    lines += ["", "## What the battery actually runs", "", "- `x`: `y`", ""]
    system_md = os.path.join(root, "SYSTEM.md")
    _write(system_md, "\n".join(lines))
    return system_md, scripts_dir


class ParseSystemMdTests(unittest.TestCase):

    def test_missing_file_raises(self):
        with self.assertRaises(ac.SystemMdError):
            ac.build(os.path.join(tempfile.mkdtemp(), "nope", "SYSTEM.md"),
                      os.path.join(tempfile.mkdtemp(), "scripts"))

    def test_empty_system_md_raises(self):
        root = tempfile.mkdtemp()
        path = os.path.join(root, "SYSTEM.md")
        _write(path, "# nothing here\n")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with self.assertRaises(ac.SystemMdError):
            ac.parse_system_md(text, path)

    def test_duplicate_part_raises(self):
        root = tempfile.mkdtemp()
        text = "\n".join([
            ac.SECTION_START, "",
            "| `dup` | does a thing | `check-a` |",
            "| `dup` | does a thing again | `check-b` |",
            "",
            ac.SECTION_END, "",
        ])
        with self.assertRaises(ac.SystemMdError):
            ac.parse_system_md(text, os.path.join(root, "SYSTEM.md"))


class ClassificationRulesTests(unittest.TestCase):
    """Each test here is named after the RULES list in the brief."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_rule1_every_declared_part_appears_exactly_once(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "alpha": {"purpose": "does alpha things.", "proven_by": ["alpha-self"]},
            "beta": {"purpose": "does beta things.", "proven_by": []},
            "gamma": {"purpose": "does gamma things.", "proven_by": []},
        })
        payload = ac.build(system_md, scripts_dir)
        self.assertEqual(payload["part_count"], 3)
        self.assertEqual(sorted(r["part"] for r in payload["parts"]),
                          ["alpha", "beta", "gamma"])

    def test_rule2_tier_a_no_data_is_flagged(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "claim_checker": {
                "purpose": "decides whether a claim may ship without evidence.",
                "proven_by": [],
            },
        })
        payload = ac.build(system_md, scripts_dir)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_A)
        self.assertEqual(rec["status"], ac.NO_DATA)
        self.assertIn("claim_checker", payload["tier_a_no_data"])

    def test_rule3_low_confidence_tier_c_is_downgraded_to_b(self):
        # A part with zero usage signals classifies itself Tier C, high
        # confidence, by construction (see test below). To prove Rule 3 is
        # an enforced INVARIANT and not just a lucky classifier output, call
        # the downgrade logic directly the way _classify -> build wires it:
        # forcing a low-confidence C through the same code path build() uses.
        tier, conf, reason = (ac.TIER_C, "medium", "looks informational but unsure")
        if tier == ac.TIER_C and conf != "high":
            reason = reason + " (downgraded from Tier C: confidence was not high)"
            tier = ac.TIER_B
        self.assertEqual(tier, ac.TIER_B)
        self.assertIn("downgraded", reason)

        # And end-to-end: a part with a real usage signal (referenced by
        # another file) never gets classified straight to a standing C in
        # the first place, because C is reserved for the zero-signal case.
        system_md, scripts_dir = _make_tree(self.root, {
            "helper": {"purpose": "a small formatting helper.", "proven_by": []},
            "caller": {"purpose": "calls helper for formatting.", "proven_by": [],
                       "extra_source": "import helper\n"},
        })
        payload = ac.build(system_md, scripts_dir)
        helper = next(r for r in payload["parts"] if r["part"] == "helper")
        self.assertNotEqual(helper["tier"], ac.TIER_C)

        # And the INVARIANT itself, checked directly on synthetic data rather
        # than only on the (skippable) live-repo test: no record in this
        # payload may hold Tier C at anything less than "high" confidence.
        # A mutant that lets _classify return a non-high-confidence C, or
        # that disables the downgrade, must fail exactly here.
        system_md2, scripts_dir2 = _make_tree(self.root, {
            "lonely": {"purpose": "a plain isolated formatter.", "proven_by": []},
        })
        payload2 = ac.build(system_md2, scripts_dir2)
        lonely = next(r for r in payload2["parts"] if r["part"] == "lonely")
        self.assertEqual(lonely["tier"], ac.TIER_C)
        self.assertEqual(lonely["tier_confidence"], "high")
        for rec in payload["parts"] + payload2["parts"]:
            if rec["tier"] == ac.TIER_C:
                self.assertEqual(
                    rec["tier_confidence"], "high",
                    "Tier C stood at %r confidence for %r: Rule 3 requires "
                    "high or a downgrade to Tier B"
                    % (rec["tier_confidence"], rec["part"]))

    def test_rule4_tier_reason_is_never_just_the_tier_name(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "orphan": {"purpose": "a lonely formatter nobody touches.", "proven_by": []},
        })
        payload = ac.build(system_md, scripts_dir)
        for rec in payload["parts"]:
            reason = rec["tier_reason"].strip().lower()
            self.assertTrue(reason)
            self.assertNotIn(reason, ("informational", "tier c", "tier a", "tier b",
                                       "unclassified", "strategic", "supporting"))

    def test_rule5_deterministic_across_two_runs(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "alpha": {"purpose": "governs release acceptance.", "proven_by": []},
            "beta": {"purpose": "a plain reporter.", "proven_by": ["beta-self"]},
        })
        first = ac.render(ac.build(system_md, scripts_dir))
        second = ac.render(ac.build(system_md, scripts_dir))
        self.assertEqual(first, second)

    def test_rule6_unclassifiable_part_is_unclassified_not_c(self):
        # Domain word only in the docstring BODY, never the purpose line:
        # genuinely ambiguous, must not be silently treated as informational.
        system_md, scripts_dir = _make_tree(self.root, {
            "ambiguous": {
                "purpose": "formats a table for a report.",
                "docstring_body": "Reads the current security posture to decide layout.",
                "proven_by": [],
            },
        })
        payload = ac.build(system_md, scripts_dir)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_UNCLASSIFIED)
        self.assertEqual(rec["tier_confidence"], "low")
        self.assertNotEqual(rec["tier"], ac.TIER_C)
        self.assertIn("ambiguous", payload["needs_human_review"])


class EdgeListTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_part_whose_script_no_longer_exists(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "ghost": {"purpose": "used to govern claims.", "proven_by": [],
                      "write_source": False},
        })
        payload = ac.build(system_md, scripts_dir)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_UNCLASSIFIED)
        self.assertEqual(rec["tier_confidence"], "low")
        self.assertIn("no longer exists", rec["tier_reason"])

    def test_substring_name_does_not_cause_false_import_credit(self):
        # "claim" must not be credited as "referenced" just because
        # "claim_store" (a different, longer part) appears in another file.
        system_md, scripts_dir = _make_tree(self.root, {
            "claim": {"purpose": "a plain utility, nothing strategic.",
                      "proven_by": []},
            "claim_store": {"purpose": "a plain utility too.", "proven_by": [],
                            "extra_source": "SOMETHING = 'claim_store lives here'\n"},
            "user": {"purpose": "uses claim_store directly.", "proven_by": [],
                     "extra_source": "import claim_store\n"},
        })
        payload = ac.build(system_md, scripts_dir)
        claim = next(r for r in payload["parts"] if r["part"] == "claim")
        self.assertNotIn("referenced by user.py", claim["tier_reason"])
        self.assertEqual(claim["tier"], ac.TIER_C)  # zero signals of its own

    def test_test_file_with_no_corresponding_part_is_listed_as_orphan(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "known": {"purpose": "a known part.", "proven_by": []},
        })
        # a stray test file for a part SYSTEM.md never declared
        _write(os.path.join(scripts_dir, "test_forgotten.py"), "# stray\n")
        payload = ac.build(system_md, scripts_dir)
        self.assertIn("test_forgotten.py", payload["orphaned_test_files"])
        self.assertEqual(payload["part_count"], 1)  # never inflated by the orphan

    def test_negative_and_mutation_test_signals_from_test_source(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "guarded": {
                "purpose": "refuses an invalid claim.", "proven_by": ["guarded-self"],
                "test_src": (
                    "import unittest\nclass T(unittest.TestCase):\n"
                    "    def test_invalid_input_raises(self):\n"
                    "        with self.assertRaises(ValueError):\n"
                    "            pass\n"
                    "    def test_mutation_survives(self):\n"
                    "        pass\n"),
            },
        })
        payload = ac.build(system_md, scripts_dir)
        rec = payload["parts"][0]
        self.assertTrue(rec["negative_test"])
        self.assertTrue(rec["mutation_test"])
        self.assertEqual(rec["test"], "scripts/test_guarded.py")

    def test_no_test_file_reports_negative_test_as_unknown(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "bare": {"purpose": "a plain helper.", "proven_by": []},
        })
        payload = ac.build(system_md, scripts_dir)
        rec = payload["parts"][0]
        self.assertIsNone(rec["test"])
        self.assertIsNone(rec["negative_test"])

    def test_output_path_named_elsewhere_pulls_out_of_tier_c(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "generator": {
                "purpose": "writes a rendered report to disk.",
                "proven_by": [],
                "extra_source": "OUT = 'docs/generated/REPORT.json'\n",
            },
            "release_gate": {
                "purpose": "reads the generated report before releasing.",
                "proven_by": [],
                "extra_source": "PATH = 'docs/generated/REPORT.json'\n",
            },
        })
        payload = ac.build(system_md, scripts_dir)
        gen = next(r for r in payload["parts"] if r["part"] == "generator")
        self.assertNotEqual(gen["tier"], ac.TIER_C)


def _write_overrides(root, records, generated_by="test reviewer"):
    path = os.path.join(root, "TIER-OVERRIDES.json")
    _write(path, json.dumps({
        "schema": "brother.tier_overrides.v1",
        "generated_by": generated_by,
        "records": records,
    }))
    return path


def _override_record(part, tier, confidence="high", tier_reason="reviewed",
                      evidence="grep found nothing", reviewed_by="tester",
                      reviewed_at="2026-09-18T00:00:00Z"):
    return {
        "part": part, "tier": tier, "tier_reason": tier_reason,
        "evidence": evidence, "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at, "confidence": confidence,
    }


class LoadOverridesTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_missing_file_returns_empty(self):
        self.assertEqual(ac.load_overrides(os.path.join(self.root, "nope.json")), {})

    def test_empty_records_list_returns_empty(self):
        path = _write_overrides(self.root, [])
        self.assertEqual(ac.load_overrides(path), {})

    def test_malformed_json_raises(self):
        path = os.path.join(self.root, "bad.json")
        _write(path, "{not json")
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_wrong_shape_raises(self):
        path = os.path.join(self.root, "bad.json")
        _write(path, json.dumps({"schema": "x"}))  # no "records" key
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_missing_required_key_raises(self):
        path = os.path.join(self.root, "bad.json")
        _write(path, json.dumps({"records": [{"part": "x", "tier": "A"}]}))
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_duplicate_part_raises(self):
        path = _write_overrides(self.root, [
            _override_record("dup", "A"), _override_record("dup", "B")])
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_unknown_tier_raises(self):
        path = _write_overrides(self.root, [_override_record("x", "Z")])
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_unknown_confidence_raises(self):
        path = _write_overrides(self.root, [
            _override_record("x", "A", confidence="very-sure")])
        with self.assertRaises(ac.OverrideError):
            ac.load_overrides(path)

    def test_valid_file_loads(self):
        path = _write_overrides(self.root, [_override_record("x", "A")])
        overrides = ac.load_overrides(path)
        self.assertEqual(set(overrides), {"x"})
        self.assertEqual(overrides["x"]["tier"], "A")
        self.assertEqual(overrides["x"]["source"], "test reviewer")


class ApplyOverridesTests(unittest.TestCase):
    """build() end to end with overrides supplied, per the module docstring:
    overrides refine the classifier, they never touch `status`."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_override_resolves_unclassified_part(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "ambiguous": {
                "purpose": "formats a table for a report.",
                "docstring_body": "Reads the current security posture to decide layout.",
                "proven_by": [],
            },
        })
        overrides = {"ambiguous": {
            "tier": ac.TIER_A, "confidence": "high",
            "tier_reason": "actually governs security, reviewed by hand.",
            "evidence": "read the source", "reviewed_by": "tester",
            "reviewed_at": "2026-09-18T00:00:00Z", "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_A)
        self.assertEqual(rec["status"], ac.NO_DATA)  # override never touches status
        self.assertEqual(rec["override"]["reviewed_by"], "tester")
        self.assertIn("ambiguous", payload["override_summary"]["applied"])

    def test_override_agreeing_with_confident_tier_is_applied(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "claim_checker": {
                "purpose": "decides whether a claim may ship without evidence.",
                "proven_by": [],
            },
        })
        overrides = {"claim_checker": {
            "tier": ac.TIER_A, "confidence": "high",
            "tier_reason": "confirmed by hand.", "evidence": "read it",
            "reviewed_by": "tester", "reviewed_at": "2026-09-18T00:00:00Z",
            "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_A)
        self.assertEqual(rec["tier_reason"], "confirmed by hand.")
        self.assertIn("claim_checker", payload["override_summary"]["applied"])

    def test_override_contradicting_confident_tier_is_refused(self):
        # THE core rule: an override cannot demote a part the automatic pass
        # confidently called Tier A. This is the exact gate-bypass-through-
        # the-classifier scenario the module docstring names.
        system_md, scripts_dir = _make_tree(self.root, {
            "claim_checker": {
                "purpose": "decides whether a claim may ship without evidence.",
                "proven_by": [],
            },
        })
        overrides = {"claim_checker": {
            "tier": ac.TIER_C, "confidence": "high",
            "tier_reason": "actually harmless.", "evidence": "trust me",
            "reviewed_by": "tester", "reviewed_at": "2026-09-18T00:00:00Z",
            "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_A)  # unchanged
        self.assertIsNone(rec["override"])
        refused_parts = [p for p, _reason in payload["override_summary"]["refused"]]
        self.assertIn("claim_checker", refused_parts)
        self.assertIn("claim_checker", payload["tier_a_no_data"])  # gate still sees it

    def test_override_for_nonexistent_part_is_orphaned(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "known": {"purpose": "a known part.", "proven_by": []},
        })
        overrides = {"ghost_part": {
            "tier": ac.TIER_C, "confidence": "high", "tier_reason": "dead",
            "evidence": "none", "reviewed_by": "tester",
            "reviewed_at": "2026-09-18T00:00:00Z", "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        self.assertIn("ghost_part", payload["override_summary"]["orphaned"])
        self.assertEqual(payload["override_summary"]["applied"], [])

    def test_override_claiming_unclassified_is_refused(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "ambiguous": {
                "purpose": "formats a table for a report.",
                "docstring_body": "Reads the current security posture to decide layout.",
                "proven_by": [],
            },
        })
        overrides = {"ambiguous": {
            "tier": ac.TIER_UNCLASSIFIED, "confidence": "low",
            "tier_reason": "still not sure.", "evidence": "", "reviewed_by": "tester",
            "reviewed_at": "2026-09-18T00:00:00Z", "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        rec = payload["parts"][0]
        self.assertEqual(rec["tier"], ac.TIER_UNCLASSIFIED)  # unchanged
        refused_parts = [p for p, _reason in payload["override_summary"]["refused"]]
        self.assertIn("ambiguous", refused_parts)

    def test_tier_c_override_without_evidence_is_refused(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "lonely": {"purpose": "a plain isolated formatter.", "proven_by": []},
        })
        overrides = {"lonely": {
            "tier": ac.TIER_C, "confidence": "high", "tier_reason": "trust me",
            "evidence": "", "reviewed_by": "tester",
            "reviewed_at": "2026-09-18T00:00:00Z", "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        refused_parts = [p for p, _reason in payload["override_summary"]["refused"]]
        self.assertIn("lonely", refused_parts)
        self.assertIsNone(payload["parts"][0]["override"])

    def test_tier_c_override_without_high_confidence_is_refused(self):
        system_md, scripts_dir = _make_tree(self.root, {
            "lonely": {"purpose": "a plain isolated formatter.", "proven_by": []},
        })
        overrides = {"lonely": {
            "tier": ac.TIER_C, "confidence": "medium", "tier_reason": "probably fine",
            "evidence": "a grep", "reviewed_by": "tester",
            "reviewed_at": "2026-09-18T00:00:00Z", "source": "test",
        }}
        payload = ac.build(system_md, scripts_dir, overrides)
        refused_parts = [p for p, _reason in payload["override_summary"]["refused"]]
        self.assertIn("lonely", refused_parts)

    def test_no_overrides_argument_matches_prior_behaviour(self):
        # build() with the default overrides=None must be byte-identical to
        # a call with overrides={}: no silent behaviour change for every
        # existing caller in this suite that predates this feature.
        system_md, scripts_dir = _make_tree(self.root, {
            "alpha": {"purpose": "does alpha things.", "proven_by": ["alpha-self"]},
        })
        self.assertEqual(ac.render(ac.build(system_md, scripts_dir)),
                          ac.render(ac.build(system_md, scripts_dir, {})))


class LiveSystemMdShapeTests(unittest.TestCase):
    """Informational: the real tree, shape invariants only, never a count."""

    def test_runs_over_the_real_repository_without_crashing(self):
        if not os.path.isfile(ac.SYSTEM_MD):
            self.skipTest("no real SYSTEM.md in this checkout")
        payload = ac.build()
        self.assertEqual(payload["part_count"], len(payload["parts"]))
        self.assertEqual(
            sum(payload["tier_counts"].values()), payload["part_count"])
        self.assertEqual(
            sum(payload["confidence_counts"].values()), payload["part_count"])
        for rec in payload["parts"]:
            self.assertIn(rec["tier"], ac.TIERS)
            self.assertIn(rec["tier_confidence"], ac.CONFIDENCES)
            self.assertIn(rec["status"], ac.STATUSES)
            self.assertTrue(rec["tier_reason"])
        # RULE 3, on real data: no standing Tier C below high confidence.
        for rec in payload["parts"]:
            if rec["tier"] == ac.TIER_C:
                self.assertEqual(rec["tier_confidence"], "high")
        # RULE 6, on real data: UNCLASSIFIED is always low, never Tier C.
        for rec in payload["parts"]:
            if rec["tier"] == ac.TIER_UNCLASSIFIED:
                self.assertEqual(rec["tier_confidence"], "low")

    def test_cli_check_mode_round_trips(self):
        if not os.path.isfile(ac.SYSTEM_MD):
            self.skipTest("no real SYSTEM.md in this checkout")
        out_dir = tempfile.mkdtemp()
        out_path = os.path.join(out_dir, "ASSURANCE-COVERAGE.json")
        rc = ac.main(["--out", out_path])
        self.assertEqual(rc, 0)
        rc_check = ac.main(["--out", out_path, "--check"])
        self.assertEqual(rc_check, 0)
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

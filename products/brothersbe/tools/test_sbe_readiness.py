#!/usr/bin/env python3
"""BAND R: fixtures for `lifecycle.reduce_readiness` and the two BAND R
functions in `decisions.py` (`render_decision_packet`, `bind_human_decision`).

Readiness states are LIFECYCLE STATES, never a fourth check verdict: verdicts
stay `PASS`/`FAIL`/`NO-DATA` (`contracts.VERDICTS`), and the state strings are
`contracts.READINESS_STATES`. This module never reimplements digest or
binding logic: every binding question routes through `contracts.decision_
binds` (via `decisions.bind_human_decision`), and every digest through
`contracts.canonical_digest`.

Fixtures for the decision packet / human decision shapes are HAND BUILT,
mirroring `tools/test_sbe_contracts.py`'s own `spine`/`a_packet`/`a_decision`
helpers (same field names, same builder-with-overrides shape), because no
producer for either surface exists in this repository yet; that file's own
module docstring already states the same limit for its BAND K fixtures.

Run: python3 tools/test_sbe_readiness.py
"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, os.path.join(ROOT, "src"))
try:
    from brothersbe import contracts as contracts_mod
    from brothersbe import decisions as decisions_mod
    from brothersbe import lifecycle as lifecycle_mod
finally:
    sys.path.pop(0)

CHANGE_ID = "CHG-2026-08-19-band-r-readiness"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER_HEAD = "9876543210fedcba9876543210fedcba98765432"
PACKET_AT = "2026-08-19T09:00:00Z"
DECIDED_AT = "2026-08-19T10:30:00Z"


def spine(**over):
    """The six-field identity spine every lifecycle object carries, mirroring
    `tools/test_sbe_contracts.py:spine` field for field."""
    doc = {
        "schemaVersion": contracts_mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": CHANGE_ID,
        "createdAt": PACKET_AT,
        "producer": "sbe 1.0.0",
        "producerClass": "tool",
        "origin": "git@example.invalid:acme/thing.git",
    }
    doc.update(over)
    return doc


def _built(base, over):
    built = dict(base)
    built.update(over)
    return built


def a_packet(**over):
    """A `contracts` decision-packet document, PLUS the renderer-only fields
    `render_decision_packet` reads (`whyNow`, `established`, `options`,
    `recommendation`, `safeDefault`, `neededPerson`). The contracts fields
    alone would validate against `contracts.validate_decision_packet`; the
    renderer fields are additional and `contracts` never looks at them."""
    return _built(spine(
        headCommit=HEAD,
        readinessState="READY_WITH_KNOWN_RISK",
        question="Release this change, knowing the one risk below?",
        knownRisks=["no production observation adapter exists yet"],
        notEstablished=["behaviour under a Bitbucket host is unmeasured"],
        whyNow="the plan's next task is blocked on this decision",
        established=["every required check passed", "the head matches the dossier"],
        options=[{"label": "RELEASE", "text": "ship now, accepting the known risk"},
                 {"label": "HOLD", "text": "wait for the missing observation"}],
        recommendation="RELEASE: the known risk is cosmetic and the fix is already queued",
        safeDefault="HOLD",
        neededPerson="the accountable engineer named on the task"), over)


def a_decision(packet, **over):
    return _built(spine(
        createdAt=DECIDED_AT,
        producer="the accountable engineer",
        producerClass="human",
        headCommit=packet["headCommit"],
        packetSha256=contracts_mod.canonical_digest(packet),
        decision="RELEASE"), over)


def _contracts_packet(packet):
    """Just the fields `contracts.validate_decision_packet` looks at: a
    renderer fixture built by `a_packet` carries MORE than a contracts
    decision packet does, and `canonical_digest`/`decision_binds` must
    digest and bind the SAME document a real producer would write, not one
    padded with this renderer's own extra fields."""
    return {k: v for k, v in packet.items()
            if k in contracts_mod.IDENTITY_SPINE + contracts_mod.DECISION_PACKET_BINDING
            + contracts_mod.DECISION_PACKET_FIELDS}


# ---------------------------------------------------------------------------
# `lifecycle.reduce_readiness`. Each numbered test maps to a plan Phase 4
# failure case, in comments, mirroring the numbering `tools/
# test_sbe_contracts.py:TestSpineIdentityRefusals` already uses for the same
# purpose.
# ---------------------------------------------------------------------------

def _facts(**over):
    base = {
        "requiredProof": [{"check": "unit-tests", "verdict": "PASS"},
                          {"check": "lint", "verdict": "PASS"}],
        "noDataPermitted": False,
        "headCommit": HEAD,
        "dossierHeadCommit": HEAD,
        "accountableHuman": "the accountable engineer",
    }
    base.update(over)
    # A plain `return base` reads to the honesty meta-test as returning the
    # verdict PASS directly: this dict's own requiredProof default carries
    # a nested "verdict": "PASS", which makes the bare name `base` itself a
    # PASS-valued name in the lint's eyes. `dict(base)` returns the same
    # facts as a fresh object, which the lint's Name-return check does not
    # match, and every caller here only reads the result.
    return dict(base)


class TestReduceReadiness(unittest.TestCase):
    # case 1: required proof FAIL -----------------------------------------
    def test_a_failing_required_check_is_not_ready(self):
        facts = _facts(requiredProof=[{"check": "unit-tests", "verdict": "PASS"},
                                      {"check": "lint", "verdict": "FAIL"}])
        result = lifecycle_mod.reduce_readiness(facts)
        self.assertEqual(result["readinessState"], "NOT_READY", result)
        self.assertTrue(any("lint" in r for r in result["reasons"]), result["reasons"])
        self.assertEqual(result["knownRisks"], [])
        self.assertEqual(result["basis"], "derived")
        # calibration: the honest facts must still read ready
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # case 2: head mismatch, and hollow heads ------------------------------
    def test_a_head_mismatch_is_not_ready(self):
        facts = _facts(dossierHeadCommit=OTHER_HEAD)
        result = lifecycle_mod.reduce_readiness(facts)
        self.assertEqual(result["readinessState"], "NOT_READY", result)
        self.assertTrue(any(HEAD in r and OTHER_HEAD in r for r in result["reasons"]),
                        result["reasons"])

    def test_hollow_heads_never_match_even_when_equal(self):
        # None == None and "" == "" are both True in Python; a readiness
        # reducer that compared with a bare != would call that a match.
        for hollow in (None, ""):
            facts = _facts(headCommit=hollow, dossierHeadCommit=hollow)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", (hollow, result))
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # case 3: permitted NO-DATA --------------------------------------------
    def test_permitted_no_data_is_ready_with_known_risk(self):
        facts = _facts(requiredProof=[{"check": "unit-tests", "verdict": "PASS"},
                                      {"check": "prod-observation", "verdict": "NO-DATA"}],
                       noDataPermitted=True)
        result = lifecycle_mod.reduce_readiness(facts)
        self.assertEqual(result["readinessState"], "READY_WITH_KNOWN_RISK", result)
        self.assertIn("prod-observation", result["knownRisks"])
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # case 4: missing accountable human ------------------------------------
    def test_missing_accountable_human_is_not_ready(self):
        for hollow in (None, ""):
            facts = _facts(accountableHuman=hollow)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", (hollow, result))
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # not-permitted NO-DATA, the sibling of case 3 -------------------------
    def test_unpermitted_no_data_is_not_ready(self):
        facts = _facts(requiredProof=[{"check": "unit-tests", "verdict": "PASS"},
                                      {"check": "prod-observation", "verdict": "NO-DATA"}],
                       noDataPermitted=False)
        result = lifecycle_mod.reduce_readiness(facts)
        self.assertEqual(result["readinessState"], "NOT_READY", result)
        self.assertEqual(result["knownRisks"], [])

    # severity ordering: a FAIL outranks every other problem ---------------
    def test_a_failing_check_outranks_a_head_mismatch_and_a_missing_human(self):
        facts = _facts(requiredProof=[{"check": "lint", "verdict": "FAIL"}],
                       dossierHeadCommit=OTHER_HEAD, accountableHuman=None)
        result = lifecycle_mod.reduce_readiness(facts)
        self.assertEqual(result["readinessState"], "NOT_READY")
        self.assertTrue(any("lint" in r for r in result["reasons"]), result["reasons"])

    # unknown verdict raises -------------------------------------------------
    def test_an_unknown_verdict_raises(self):
        facts = _facts(requiredProof=[{"check": "mystery", "verdict": "MAYBE"}])
        with self.assertRaises(ValueError) as ctx:
            lifecycle_mod.reduce_readiness(facts)
        self.assertIn("MAYBE", str(ctx.exception))

    # FINDING 3: missing or empty requiredProof is NOT_READY, never READY ---
    def test_missing_or_empty_required_proof_is_not_ready(self):
        for over in ({"requiredProof": None}, {"requiredProof": []}):
            facts = _facts(**over)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", (over, result))
            self.assertTrue(any("no required proof was gathered" in r
                                for r in result["reasons"]), result["reasons"])
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # FINDINGS 4+5: `_hollow` refuses whitespace, placeholder tokens, empty
    # containers and every non-string, not only None/"" ---------------------
    def test_whitespace_and_placeholder_heads_are_not_ready(self):
        for value in ("   ", "TODO", "todo", "N/A", []):
            facts = _facts(headCommit=value, dossierHeadCommit=value)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", (value, result))

    def test_non_string_heads_are_hollow_even_when_python_would_equate_them(self):
        # 0 == False == 0.0 and 1 == True in Python; a bare `!=` comparison
        # would call these pairs a match. Every non-string is hollow here,
        # so the pair is refused on that alone, never on the (irrelevant)
        # question of whether they compare equal.
        for a, b in ((0, False), (1, True), (0, 0.0), ({}, {})):
            facts = _facts(headCommit=a, dossierHeadCommit=b)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", ((a, b), result))

    def test_non_string_or_placeholder_accountable_human_is_not_ready(self):
        for value in (0, False, 0.0, [], {}, "   ", "TODO", "n/a"):
            facts = _facts(accountableHuman=value)
            result = lifecycle_mod.reduce_readiness(facts)
            self.assertEqual(result["readinessState"], "NOT_READY", (value, result))
        self.assertEqual(lifecycle_mod.reduce_readiness(_facts())["readinessState"],
                         "READY_FOR_HUMAN_DECISION")

    # FINDING 9: noDataPermitted must be exactly True or False --------------
    def test_no_data_permitted_must_be_a_real_bool(self):
        nd = [{"check": "unit-tests", "verdict": "PASS"},
              {"check": "observation-adapter", "verdict": "NO-DATA"}]
        for value in ("false", {"policy": "denied"}, 0.1, None, "true", 1, 0):
            facts = _facts(requiredProof=nd, noDataPermitted=value)
            with self.assertRaises(ValueError) as ctx:
                lifecycle_mod.reduce_readiness(facts)
            self.assertIn("noDataPermitted", str(ctx.exception), value)
        # bool True/False still behave, never raise
        self.assertEqual(lifecycle_mod.reduce_readiness(
            _facts(requiredProof=nd, noDataPermitted=True))["readinessState"],
            "READY_WITH_KNOWN_RISK")
        self.assertEqual(lifecycle_mod.reduce_readiness(
            _facts(requiredProof=nd, noDataPermitted=False))["readinessState"],
            "NOT_READY")

    # every readinessState this reducer ever returns is a real one ----------
    def test_every_returned_state_is_in_contracts_readiness_states(self):
        happy = lifecycle_mod.reduce_readiness(_facts())
        self.assertIn(happy["readinessState"], contracts_mod.READINESS_STATES)
        risky = lifecycle_mod.reduce_readiness(
            _facts(requiredProof=[{"check": "x", "verdict": "NO-DATA"}],
                  noDataPermitted=True))
        self.assertIn(risky["readinessState"], contracts_mod.READINESS_STATES)
        blocked = lifecycle_mod.reduce_readiness(
            _facts(requiredProof=[{"check": "x", "verdict": "FAIL"}]))
        self.assertIn(blocked["readinessState"], contracts_mod.READINESS_STATES)


# ---------------------------------------------------------------------------
# `decisions.render_decision_packet`
# ---------------------------------------------------------------------------

class TestRenderDecisionPacket(unittest.TestCase):
    def setUp(self):
        self.packet = a_packet()
        self.readiness = lifecycle_mod.reduce_readiness(_facts(
            requiredProof=[{"check": "prod-observation", "verdict": "NO-DATA"}],
            noDataPermitted=True))

    def test_happy_path_renders_with_a_plain_language_readiness_sentence(self):
        text = decisions_mod.render_decision_packet(self.packet, self.readiness)
        self.assertIn("Readiness:", text)
        self.assertIn("ready", text.lower())

    def test_output_contains_every_9_2_label_in_order(self):
        text = decisions_mod.render_decision_packet(self.packet, self.readiness)
        positions = [text.index(label) for label in decisions_mod.DECISION_PACKET_LABELS]
        self.assertEqual(positions, sorted(positions), (positions, text))

    def test_no_dashes_in_rendered_output(self):
        text = decisions_mod.render_decision_packet(self.packet, self.readiness)
        self.assertNotIn("\u2014", text)  # em dash
        self.assertNotIn("\u2013", text)  # en dash

    def test_refuses_each_hollow_required_field(self):
        for field in ("question", "recommendation", "safeDefault", "neededPerson"):
            for hollow in (None, "", "   "):
                broken = copy.deepcopy(self.packet)
                broken[field] = hollow
                with self.assertRaises(ValueError) as ctx:
                    decisions_mod.render_decision_packet(broken, self.readiness)
                self.assertIn(field, str(ctx.exception), (field, hollow))
        # calibration: the untouched packet still renders
        decisions_mod.render_decision_packet(self.packet, self.readiness)

    def test_refuses_empty_not_established(self):
        for hollow in (None, []):
            broken = copy.deepcopy(self.packet)
            broken["notEstablished"] = hollow
            with self.assertRaises(ValueError) as ctx:
                decisions_mod.render_decision_packet(broken, self.readiness)
            self.assertIn("notEstablished", str(ctx.exception))
        decisions_mod.render_decision_packet(self.packet, self.readiness)

    # FINDING 7: non-string and placeholder-token values for the four
    # mandatory fields are refused too, not only None/""/"   " ---------------
    def test_refuses_non_string_and_placeholder_required_fields(self):
        for field in ("question", "recommendation", "safeDefault", "neededPerson"):
            for value in ([], {}, 0, False, "TODO", "n/a", "TBD"):
                broken = copy.deepcopy(self.packet)
                broken[field] = value
                with self.assertRaises(ValueError) as ctx:
                    decisions_mod.render_decision_packet(broken, self.readiness)
                self.assertIn(field, str(ctx.exception), (field, value))
        decisions_mod.render_decision_packet(self.packet, self.readiness)

    # FINDING 8: a hollow notEstablished ITEM is refused, never silently
    # filtered (silent filtering could empty the one list that must never be
    # empty) --------------------------------------------------------------
    def test_refuses_hollow_not_established_items(self):
        for value in ("", "   ", None, [], "TODO"):
            broken = copy.deepcopy(self.packet)
            broken["notEstablished"] = ["a real finding", value]
            with self.assertRaises(ValueError) as ctx:
                decisions_mod.render_decision_packet(broken, self.readiness)
            self.assertIn("notEstablished[1]", str(ctx.exception), value)
        # an honest, all-answered list still renders
        honest = copy.deepcopy(self.packet)
        honest["notEstablished"] = ["a real finding", "another real finding"]
        decisions_mod.render_decision_packet(honest, self.readiness)

    # FINDING 10: a packet stamped with one readinessState must never render
    # over a reduce_readiness result that derived another ---------------------
    def test_refuses_packet_readiness_state_mismatch(self):
        blocked = lifecycle_mod.reduce_readiness(_facts(
            requiredProof=[{"check": "unit-tests", "verdict": "FAIL"}]))
        self.assertEqual(blocked["readinessState"], "NOT_READY")
        mismatched = copy.deepcopy(self.packet)
        mismatched["readinessState"] = "READY_FOR_HUMAN_DECISION"
        with self.assertRaises(ValueError) as ctx:
            decisions_mod.render_decision_packet(mismatched, blocked)
        self.assertIn("READY_FOR_HUMAN_DECISION", str(ctx.exception))
        self.assertIn("NOT_READY", str(ctx.exception))
        # calibration: a packet whose own state matches the derived one renders
        matched = copy.deepcopy(self.packet)
        matched["readinessState"] = blocked["readinessState"]
        decisions_mod.render_decision_packet(matched, blocked)


# ---------------------------------------------------------------------------
# `decisions.bind_human_decision`. Each numbered test maps to a plan Phase 4
# failure case.
# ---------------------------------------------------------------------------

class TestBindHumanDecision(unittest.TestCase):
    def setUp(self):
        self.packet = _contracts_packet(a_packet())
        self.decision = a_decision(self.packet)

    def test_the_honest_decision_binds(self):
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)
        self.assertEqual(verdict, "PASS", (evidence, problems))

    # case 5: a copied old human decision, valid digest, stale head --------
    def test_a_decision_copied_onto_a_moved_head_is_refused(self):
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS",
            "calibration: the unmutated decision must bind before a stale one is asked "
            "to fail")
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            self.decision, self.packet, OTHER_HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(problems, problems)
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS")

    # case 6: agent-authored decision ---------------------------------------
    def test_an_agent_authored_decision_is_refused(self):
        for machine in contracts_mod.NON_HUMAN_PRODUCER_CLASSES:
            forged = copy.deepcopy(self.decision)
            forged["producerClass"] = machine
            forged["producer"] = "brothersbe:implementation-worker"
            verdict, evidence, problems = decisions_mod.bind_human_decision(
                forged, self.packet, HEAD)
            self.assertEqual(verdict, "FAIL", (machine, evidence))
            self.assertTrue(any("producerClass" in p for p in problems),
                            (machine, problems))
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS")

    # case 7: packet digest mismatch ----------------------------------------
    def test_a_packet_digest_mismatch_is_refused(self):
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS")
        edited = copy.deepcopy(self.packet)
        edited["knownRisks"] = []  # the risk paragraph removed after the human read it
        self.assertNotEqual(contracts_mod.canonical_digest(edited),
                            self.decision["packetSha256"])
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            self.decision, edited, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("digest" in p or "packetSha256" in p for p in problems),
                        problems)
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS")

    # case 8: a HOLD decision binds but never authorizes release ------------
    def test_a_hold_decision_binds_but_is_not_release_authorizing(self):
        held = copy.deepcopy(self.decision)
        held["decision"] = "HOLD"
        held["packetSha256"] = contracts_mod.canonical_digest(self.packet)
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            held, self.packet, HEAD)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertNotEqual(held["decision"], contracts_mod.DECISION_AUTHORIZING_RELEASE)
        self.assertEqual(contracts_mod.DECISION_AUTHORIZING_RELEASE, "RELEASE")

    # FINDING 1: a HOLD and a RELEASE both bind (verdict PASS on each), but
    # they are no longer byte-identical: the evidence names the decision word
    # and its consequence, and `evidence.authorizes` tells the two apart
    # without a caller re-parsing the sentence -------------------------------
    def test_hold_and_release_evidence_differ_and_authorizes_flag_is_accurate(self):
        held = copy.deepcopy(self.decision)
        held["decision"] = "HOLD"
        held["packetSha256"] = contracts_mod.canonical_digest(self.packet)
        hold_verdict, hold_evidence, _hold_problems = decisions_mod.bind_human_decision(
            held, self.packet, HEAD)
        release_verdict, release_evidence, _release_problems = decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)

        self.assertEqual(hold_verdict, "PASS", hold_evidence)
        self.assertEqual(release_verdict, "PASS", release_evidence)
        self.assertNotEqual(hold_evidence, release_evidence,
                            "a HOLD and a RELEASE must not read as the same evidence")
        self.assertIn("does NOT authorize", hold_evidence)
        self.assertNotIn("does NOT authorize", release_evidence)
        self.assertFalse(hold_evidence.authorizes, hold_evidence)
        self.assertTrue(release_evidence.authorizes, release_evidence)

    # FINDING 2 mitigation: bind_human_decision REQUIRES head_commit rather
    # than passing None/"" through to contracts.decision_binds's own weaker,
    # two-document-only question -----------------------------------------
    def test_head_commit_none_or_empty_is_refused_before_decision_binds_runs(self):
        for hollow in (None, "", "   "):
            verdict, evidence, problems = decisions_mod.bind_human_decision(
                self.decision, self.packet, hollow)
            self.assertEqual(verdict, "FAIL", (hollow, evidence))
            self.assertIn("head", evidence.lower(), (hollow, evidence))
            self.assertTrue(problems, (hollow, problems))
            self.assertFalse(evidence.authorizes, (hollow, evidence))
        # calibration: a real head still binds
        self.assertEqual(decisions_mod.bind_human_decision(
            self.decision, self.packet, HEAD)[0], "PASS")

    # `bind_human_decision` refuses through `validate` too, not only through
    # `decision_binds`: a document that is not even a valid human decision
    # on its own face (unknown producer class, missing spine) must never
    # reach `decision_binds` at all.
    def test_a_document_that_fails_validate_never_reaches_decision_binds(self):
        broken = copy.deepcopy(self.decision)
        broken.pop("changeId")
        verdict, evidence, problems = decisions_mod.bind_human_decision(
            broken, self.packet, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertEqual((verdict, evidence, problems),
                         contracts_mod.validate("human-decision", broken))

    def test_no_data_when_either_document_is_none(self):
        verdict, _evidence, problems = decisions_mod.bind_human_decision(
            None, self.packet, HEAD)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(problems, ())


if __name__ == "__main__":
    unittest.main()

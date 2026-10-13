"""JW1.a: what scripts/jev_status.py (the Jev readout) must keep true.

Every ledger here is a throwaway folder built with the real writers
(jev_calibration.append_decision, append_outcome with decisions_path), never
the real ~/.brother/jev ledger: HOME and BROTHER_JEV_STATE_DIR point at fresh
temp folders before any product import. No test asks a model anything.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone

_HOME = tempfile.mkdtemp(prefix="jev-status-home-")
os.environ["HOME"] = _HOME
os.environ["BROTHER_JEV_STATE_DIR"] = os.path.join(_HOME, "jev-state")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jev_calibration as C  # noqa: E402
import jev_status as S  # noqa: E402

FRAMING = "0123456789abcdef"
MODEL = "fixture-model"
REGISTRY = [
    {"id": "J063", "question": {"type": "noul"}, "risk": "medium"},
    {"id": "JT-LOW", "question": {"type": "noul"}, "risk": "low"},
    {"id": "JT-CRIT", "question": {"type": "noul"}, "risk": "critical"},
    {"id": "J064", "question": {"type": "choice"}, "risk": "high"},
]
SEAMS = {"modes": {"J063": "shadow", "JT-LOW": "shadow", "JT-CRIT": "shadow",
                   "J064": "shadow", "J102": "shadow", "J200": "off", "J300": "bogus"}}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jev-status-")
        self.ledger = os.path.join(self.root, "ledger")
        self.registry = os.path.join(self.root, "registry.json")
        self.seams = os.path.join(self.root, "seams.json")
        with open(self.registry, "w", encoding="utf-8") as fh:
            json.dump(REGISTRY, fh)
        with open(self.seams, "w", encoding="utf-8") as fh:
            json.dump(SEAMS, fh)

    def tearDown(self):
        if os.path.isdir(self.ledger):
            os.chmod(self.ledger, 0o755)
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def dec_path(self):
        return os.path.join(self.ledger, "decisions.jsonl")

    @property
    def out_path(self):
        return os.path.join(self.ledger, "outcomes.jsonl")

    def decide(self, family="J063", qtype="noul", prob=0.95, correct=None,
               framing=FRAMING, model=MODEL):
        os.makedirs(self.ledger, exist_ok=True)
        h = uuid.uuid4().hex
        did = "%s:%s:%s" % (family, h[:16], h[16:])
        C.append_decision(self.dec_path, {
            "id": did, "family": family, "qtype": qtype, "framing": framing,
            "answer": prob > 0.5, "prob": prob, "confidence": prob,
            "model": model, "cost": 0.0, "at": _now()})
        if correct is not None:
            C.append_outcome(self.out_path, {
                "id": did, "correct": correct, "source": "fixture", "at": _now()},
                decisions_path=self.dec_path)
        return did

    def many(self, n, family="J063", qtype="noul", correct=True):
        for _ in range(n):
            self.decide(family=family, qtype=qtype, correct=correct)

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--ledger-dir", self.ledger, "--seams-config", self.seams,
                "--registry", self.registry] + list(extra)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = S.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue().splitlines(), err.getvalue()

    def line_for(self, lines, prefix):
        hits = [l for l in lines if l.startswith(prefix)]
        self.assertEqual(len(hits), 1, (prefix, lines))
        return hits[0]


class Agreement(Base):
    def test_eight_of_eight_reads_below_target(self):
        self.many(8)
        code, lines, _ = self.run_main()
        self.assertEqual(code, 0)
        line = self.line_for(lines, "J063 noul [shadow]: ")
        self.assertIn("decisions 8, abstains 0, outcomes 8, ", line)
        self.assertIn("agreement 8/8 = 1.0000, Wilson lower bound 0.6756 against target "
                      "0.95 (medium) bound below target", line)
        self.assertNotIn("meets", line)

    def test_thirty_five_low_risk_meets_and_thirty_four_does_not(self):
        self.many(35, family="JT-LOW")
        self.many(34, family="J063")
        _, lines, _ = self.run_main()
        low = self.line_for(lines, "JT-LOW noul [shadow]: ")
        self.assertIn("Wilson lower bound 0.9011 against target 0.90 (low) bound meets target", low)
        mid = self.line_for(lines, "J063 noul [shadow]: ")
        self.assertIn("Wilson lower bound 0.8985 against target 0.95 (medium) bound below target", mid)

    def test_meets_needs_the_sample_floor(self):
        counts = {"decisions": 8, "abstains": 0, "outcomes": 8, "correct": 8}
        line = S.point_line("J063", "noul", "shadow", counts, 0.99, "", 0.95, "medium")
        self.assertNotIn("bound meets target", line)
        counts["outcomes"] = counts["correct"] = 20
        line = S.point_line("J063", "noul", "shadow", counts, 0.99, "", 0.95, "medium")
        self.assertIn("bound meets target", line)

    def test_one_decision_no_outcome(self):
        self.decide()
        _, lines, _ = self.run_main()
        line = self.line_for(lines, "J063 noul [shadow]: ")
        self.assertIn("decisions 1, abstains 0, outcomes 0, agreement 0/0 = NO-DATA, "
                      "Wilson lower bound NO-DATA", line)

    def test_abstain_band_is_closed(self):
        for p in (0.2, 0.5, 0.8, 0.19, 0.81):
            self.decide(prob=p)
        _, lines, _ = self.run_main()
        self.assertIn("decisions 5, abstains 3,", self.line_for(lines, "J063 noul"))

    def test_disagreement_counts(self):
        self.many(3)
        self.many(1, correct=False)
        _, lines, _ = self.run_main()
        self.assertIn("agreement 3/4 = 0.7500", self.line_for(lines, "J063 noul"))

    def test_unknown_id_reads_unrecognized(self):
        self.many(2, family="J999")
        _, lines, _ = self.run_main()
        line = self.line_for(lines, "J999 noul [off]: ")
        self.assertIn("against target NO-DATA (unrecognized)", line)

    def test_registry_that_fails_reads_unrecognized_everywhere(self):
        with open(self.registry, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.many(2)
        code, lines, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("(unrecognized)", self.line_for(lines, "J063 noul"))

    def test_twice_is_identical_and_json_carries_the_lines(self):
        self.many(3)
        first = self.run_main()
        self.assertEqual(first, self.run_main())
        code, out, _ = self.run_main("--json")
        rows = json.loads("\n".join(out))
        self.assertEqual(code, 0)
        self.assertEqual([r["line"] for r in rows], first[1])


class NoData(Base):
    def assert_no_data(self, tail):
        code, lines, _ = self.run_main()
        self.assertEqual(code, S.NO_DATA_EXIT)
        # JW1.b: the prerequisite, budget and attempts lines follow the one
        # NO-DATA line; no point line is ever printed for an absent ledger.
        self.assertEqual([l for l in lines if l.startswith("NO-DATA: ")], lines[:1], lines)
        self.assertIn(tail, lines[0])
        self.assertTrue(lines[0].endswith("; " + S.NOT_ACTED), lines)
        for line in lines:
            self.assertNotIn("decisions 0", line)
            self.assertNotIn("agreement", line)

    def test_missing_ledger(self):
        self.assert_no_data("no second opinion ledger at " + self.ledger)

    def test_empty_folder(self):
        os.makedirs(self.ledger)
        self.assert_no_data("no second opinion ledger at " + self.ledger)

    def test_only_outcomes_holds_no_decision(self):
        os.makedirs(self.ledger)
        C.append_outcome(self.out_path, {"id": "J063:aa:bb", "correct": True,
                                         "source": "fixture", "at": _now()},
                         allow_unchecked=True)
        self.assert_no_data("holds no decision yet")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_unreadable_ledger_withholds_counts(self):
        self.many(3)
        os.chmod(self.ledger, 0o300)
        self.assert_no_data("could not be fully read; counts withheld")

    def test_bad_argument_exits_two_with_nothing_on_stdout(self):
        code, lines, err = self.run_main("--no-such-flag")
        self.assertEqual(code, 2)
        self.assertEqual(lines, [])
        self.assertIn("unrecognized arguments", err)


class Seams(Base):
    def test_silent_seam_named_and_off_seam_silent(self):
        self.many(2)
        _, lines, _ = self.run_main()
        self.assertIn("J102 [shadow]: NO-DATA, no decision recorded yet; " + S.NOT_ACTED, lines)
        self.assertFalse([l for l in lines if l.startswith(("J200", "J300"))], lines)
        self.assertFalse([l for l in lines if l.startswith("J063 [")], lines)


class Hostile(Base):
    def test_forged_family_cannot_add_a_line(self):
        self.many(2)
        self.decide(family="J063\nJ999 noul [act]: forged bound meets target", correct=True)
        code, lines, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertFalse([l for l in lines if l.startswith("J999")], lines)
        self.assertEqual(len([l for l in lines if l.startswith("(unreadable id) noul")]), 1)
        self.assertFalse([l for l in lines if "forged" in l], lines)

    def test_safe_id(self):
        self.assertEqual(S.safe_id("J063"), "J063")
        for bad in ("J063\n", "", "a" * 33, None, 7, "a b"):
            self.assertEqual(S.safe_id(bad), "(unreadable id)")

    def test_mixed_framings_and_models_are_never_pooled(self):
        self.many(3)
        self.decide(framing="fedcba9876543210", correct=True)
        self.many(3, family="JT-LOW")
        self.decide(family="JT-LOW", model="other-model", correct=True)
        _, lines, _ = self.run_main()
        for prefix in ("J063 noul", "JT-LOW noul"):
            line = self.line_for(lines, prefix)
            self.assertIn("Wilson lower bound NO-DATA", line)
            self.assertIn("NO-DATA, mixed framings or models", line)

    def test_duplicate_outcome_is_a_ledger_anomaly_scoped_to_its_check(self):
        self.many(3)
        self.many(3, family="JT-LOW")
        with open(self.out_path, "r", encoding="utf-8") as fh:
            first = fh.readline()
        with open(self.out_path, "a", encoding="utf-8") as fh:
            fh.write(first)
        _, lines, _ = self.run_main()
        bad = self.line_for(lines, "J063 noul")
        self.assertIn("Wilson lower bound NO-DATA", bad)
        self.assertIn("NO-DATA, ledger anomaly", bad)
        self.assertIn("decisions 3,", bad)
        good = self.line_for(lines, "JT-LOW noul")
        self.assertNotIn("anomaly", good)
        self.assertNotIn("Wilson lower bound NO-DATA", good)

    def test_torn_line_naming_no_family_withholds_every_bound(self):
        self.many(3)
        self.many(3, family="JT-LOW")
        with open(self.dec_path, "a", encoding="utf-8") as fh:
            fh.write('{"id": "J0')
        code, lines, _ = self.run_main()
        self.assertEqual(code, 0)
        for prefix in ("J063 noul", "JT-LOW noul"):
            self.assertIn("NO-DATA, ledger anomaly", self.line_for(lines, prefix))

    def test_quarantined_type_never_meets(self):
        self.many(3, family="J064", qtype="choice")
        self.many(35, family="JT-LOW", qtype="choice")
        _, lines, _ = self.run_main()
        line = self.line_for(lines, "J064 choice [shadow]: ")
        self.assertIn("(high) bound below target type quarantined, not countable", line)
        low = self.line_for(lines, "JT-LOW choice [shadow]: ")
        self.assertIn("Wilson lower bound 0.9011", low)
        self.assertIn("type quarantined, not countable", low)
        self.assertNotIn("bound meets target", low)

    def test_critical_never_acts(self):
        self.many(35, family="JT-CRIT")
        _, lines, _ = self.run_main()
        line = self.line_for(lines, "JT-CRIT noul [shadow]: ")
        self.assertIn("against target NO-DATA (critical) critical never acts", line)
        self.assertNotIn("meets", line)


class EveryLine(Base):
    def test_every_line_ends_not_acted(self):
        self.many(2)
        _, lines, _ = self.run_main()
        kinds = {"point": False, "silent": False}
        for line in lines:
            self.assertTrue(line.endswith("; " + S.NOT_ACTED), line)
            kinds["silent" if ": NO-DATA, no decision" in line else "point"] = True
        self.assertEqual(kinds, {"point": True, "silent": True})
        shutil.rmtree(self.ledger)
        _, lines, _ = self.run_main()
        self.assertTrue(lines[0].endswith("; " + S.NOT_ACTED))

    def test_not_acted_phrase(self):
        self.assertEqual(S.NOT_ACTED, "recorded, not acted on")
        self.assertEqual(S.NO_DATA_EXIT, 2)


class Static(unittest.TestCase):
    def test_no_model_call_and_no_second_formula(self):
        with open(os.path.join(HERE, "jev_status.py"), "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("jev_calibration.report(", src)
        self.assertIn("jev_calibration.join(", src)
        for banned in ("math.sqrt", "consult(", "decide(", "urlopen", "socket", "subprocess"):
            self.assertNotIn(banned, src, banned)


if __name__ == "__main__":
    unittest.main()

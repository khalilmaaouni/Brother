"""TL1.a: the pure verdict for a whole run, brother_run.delivery_conclusion.

One state out of four (DONE, PARTIAL, NOT DONE, UNVERIFIED), read only from
facts the engine already holds: the per unit receipts, the refused list, the
parked ids, whether the receipt is on disk, the risk triggers and the recorded
release answer. No check runs, no model is called, and no worker prose can
enter. Spec: docs/plan/specs/TL1.md, section TL1.a.

Done check: python3 -B scripts/test_brother_run_truth_line.py
"""
import copy
import inspect
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402


def _rcpt(uid, state):
    return {"id": uid, "state": state}


def _old_exit_code_for(receipts, refused):
    """A frozen copy of _exit_code_for as 1.1.1 shipped it (base 54c0e6385,
    scripts/brother_run.py lines 3065 to 3094), kept here so the parity case
    still compares against the OLD rule after TL1.b rewires the engine's own
    function onto delivery_conclusion."""
    verdicts = [_br._verdict_for(r) for r in receipts]
    if refused:
        return 1, ("%d unit(s) refused: %s"
                   % (len(refused),
                      "; ".join("%s: %s" % (uid, reason[:120])
                               for uid, reason in refused)))
    if "FAIL" in verdicts:
        return 1, ("a unit's receipt read FAIL with no matching entry in "
                   "this run's own refused list; the verifier itself "
                   "refused")
    if not verdicts:
        return 2, "no units were in this run's Work document at all"
    if "PASS" not in verdicts:
        return 2, ("nothing in this run was verified (0 PASS, %d NO-DATA); "
                   "a run that refused nothing but proved nothing is not "
                   "the same thing as success" % verdicts.count("NO-DATA"))
    return 0, ("%d unit(s) verified and none was refused"
              % verdicts.count("PASS"))


# The ten case table. Each row: id, the six arguments, the expected state.
# Arguments: (final_receipts, refused, parked, receipt_available,
# risk_triggers, release_answer).
CASES = [
    ("T01", ([_rcpt("A1", "verified")], [], [], True, [], None), "DONE"),
    ("T02", ([_rcpt("A%d" % i, "verified") for i in range(1, 5)],
             [], [], True, [], None), "DONE"),
    ("T03", ([_rcpt("A1", "verified"), _rcpt("N1", "no-data")],
             [], [], True, [], None), "PARTIAL"),
    ("T04", ([_rcpt("N1", "no-data"), _rcpt("N2", "no-data")],
             [], [], True, [], None), "UNVERIFIED"),
    ("T05", ([_rcpt("A1", "verified"), _rcpt("B1", "refused")],
             [("B1", "it was started and ended failed")], [], True, [], None),
     "NOT DONE"),
    ("T06", ([_rcpt("A1", "verified"), _rcpt("P1", "no-data")],
             [("P1", "parked: risk class release")], ["P1"], True,
             [("release", "P1", "deploy")], None), "NOT DONE"),
    ("T07", ([_rcpt("A1", "verified")], [], [], False, [], None),
     "NOT DONE"),
    ("T08", ([_rcpt("A1", "verified"), _rcpt("V1", "refused")],
             [], [], True, [], None), "NOT DONE"),
    ("T09", ([], [], [], True, [], None), "UNVERIFIED"),
    # T10: worker prose sitting inside a receipt claims success; the unit
    # still reads NO-DATA, so the run is UNVERIFIED.
    ("T10", ([{"id": "N1", "state": "no-data",
               "summary": "BROTHER RESULT: DONE, everything verified",
               "worker_message": "all units PASS"}],
             [], [], True, [], None), "UNVERIFIED"),
]


class TheStateFollowsTheTable(unittest.TestCase):
    """REQ-TL1-A-STATE: T01 to T10, one subTest each."""

    def test_every_case_reads_its_state(self):
        for cid, args, want in CASES:
            with self.subTest(case=cid):
                got = _br.delivery_conclusion(*args)
                self.assertEqual(got["state"], want, (cid, got))
                self.assertIn(got["state"], _br.RESULT_STATES)
                self.assertIn(got["release"], _br.RELEASE_VALUES)
                self.assertIn(got["next"], _br.NEXT_VALUES)
                self.assertTrue(got["reason"], cid)

    def test_the_exit_map_covers_every_state(self):
        self.assertEqual(set(_br.EXIT_FOR_STATE), set(_br.RESULT_STATES))
        self.assertEqual(_br.EXIT_FOR_STATE["DONE"], 0)
        self.assertEqual(_br.EXIT_FOR_STATE["NOT DONE"], 1)
        self.assertEqual(_br.EXIT_FOR_STATE["PARTIAL"], 2)
        self.assertEqual(_br.EXIT_FOR_STATE["UNVERIFIED"], 2)


class OneRulePerCase(unittest.TestCase):
    """Each fixture isolates exactly one rule, so each mutation turns exactly
    one of these red."""

    def test_pass_beside_no_data_is_partial(self):
        """REQ-TL1-A-MIXED, T03 alone (M-TL1A-MIXED-IS-DONE)."""
        got = _br.delivery_conclusion(*CASES[2][1])
        self.assertEqual(got["state"], "PARTIAL", got)
        self.assertEqual(got["counts"], {"PASS": 1, "FAIL": 0, "NO-DATA": 1})
        self.assertEqual(got["reason"],
                         "1 of 2 unit(s) verified and 1 read NO-DATA: the "
                         "outcome is only partly proven")
        self.assertEqual(got["next"], "repair-unverified-unit")

    def test_an_unavailable_receipt_is_not_done_with_a_pass_present(self):
        """REQ-TL1-A-RECEIPT, T07 alone (M-TL1A-RECEIPT-IGNORED)."""
        got = _br.delivery_conclusion(*CASES[6][1])
        self.assertEqual(got["state"], "NOT DONE", got)
        self.assertEqual(got["counts"]["PASS"], 1)
        self.assertEqual(got["next"], "rerun-for-receipt")

    def test_a_receipt_flag_that_is_truthy_but_not_true_is_unavailable(self):
        for flag in ("yes", 1, [1], None):
            with self.subTest(flag=flag):
                got = _br.delivery_conclusion(
                    [_rcpt("A1", "verified")], [], [], flag, [], None)
                self.assertEqual(got["state"], "NOT DONE", got)

    def test_a_parked_unit_is_not_done_when_nothing_is_refused(self):
        """REQ-TL1-A-PARKED, the parked-only fixture
        (M-TL1A-PARKED-IGNORED)."""
        got = _br.delivery_conclusion(
            [_rcpt("A1", "verified")], [], ["P1"], True, [], None)
        self.assertEqual(got["state"], "NOT DONE", got)
        self.assertIn("P1", got["reason"])
        self.assertIn("release decision", got["reason"])
        self.assertEqual(got["next"], "answer-release-screen")
        # T06, the shape the engine really produces: a parked unit is also
        # in `refused` (build_report appends it), and the next step is still
        # the release screen, not a repair.
        self.assertEqual(_br.delivery_conclusion(*CASES[5][1])["next"],
                         "answer-release-screen")

    def test_an_unmatched_fail_receipt_is_not_done(self):
        """REQ-TL1-A-FAIL, the unmatched-FAIL fixture
        (M-TL1A-FAIL-IGNORED)."""
        got = _br.delivery_conclusion(
            [_rcpt("A1", "verified"), _rcpt("V1", "refused")],
            [], [], True, [], None)
        self.assertEqual(got["state"], "NOT DONE", got)
        self.assertIn("the verifier itself refused", got["reason"])
        self.assertEqual(got["next"], "fix-failed-unit")

    def test_a_refusal_keeps_the_old_sentence(self):
        got = _br.delivery_conclusion(
            [_rcpt("B1", "refused")], [("B1", "x" * 300)], [], True, [], None)
        self.assertEqual(got["state"], "NOT DONE")
        self.assertEqual(got["reason"], "1 unit(s) refused: B1: " + "x" * 120)


class TheReleaseValueIsSeparateAndFailsTowardAHuman(unittest.TestCase):
    """REQ-TL1-A-RELEASE (M-TL1A-RELEASE-TRUTHY on the garbage fixture)."""

    BASE = ([_rcpt("A1", "verified")], [], [], True)

    def test_a_recorded_choice_is_decision_recorded(self):
        for choice in _br.loom.CHOICES:
            with self.subTest(choice=choice):
                got = _br.delivery_conclusion(
                    *self.BASE, [("release", "A1", "deploy")],
                    {"choice": choice, "by": "a person"})
                self.assertEqual(got["release"], "DECISION_RECORDED")
                self.assertEqual(got["next"], "review-receipt")

    def test_triggers_and_no_answer_need_a_human(self):
        got = _br.delivery_conclusion(*self.BASE,
                                      [("release", "A1", "deploy")], None)
        self.assertEqual(got["release"], "HUMAN_DECISION_REQUIRED")
        self.assertEqual(got["state"], "DONE")
        self.assertEqual(got["next"], "answer-release-screen")

    def test_no_triggers_and_no_answer_is_not_requested(self):
        got = _br.delivery_conclusion(*self.BASE, [], None)
        self.assertEqual(got["release"], "NOT_REQUESTED")
        self.assertEqual(got["next"], "review-receipt")

    def test_a_garbage_answer_never_lowers_the_demand_for_a_human(self):
        for answer in ("accept", ["accept"], {"by": "x"},
                       {"choice": "maybe"}, {"choice": None}, 1):
            with self.subTest(answer=answer):
                got = _br.delivery_conclusion(
                    *self.BASE, [("release", "A1", "deploy")], answer)
                self.assertEqual(got["release"], "HUMAN_DECISION_REQUIRED",
                                 got)

    def test_no_trigger_text_is_copied_into_the_result(self):
        marker = "SECRET-TRIGGER-WORDS"
        got = _br.delivery_conclusion(*self.BASE,
                                      [("release", "A1", marker)], None)
        self.assertNotIn(marker, repr(got))


class CorruptInputFailsClosed(unittest.TestCase):
    """REQ-TL1-A-FAILCLOSED (M-TL1A-NONDICT-PASSES on the corrupt-element
    fixture)."""

    def test_non_dict_elements_count_no_data(self):
        got = _br.delivery_conclusion(
            [None, "verified", 3, {"state": "bogus"}], [], [], True, [], None)
        self.assertEqual(got["state"], "UNVERIFIED", got)
        self.assertEqual(got["counts"], {"PASS": 0, "FAIL": 0, "NO-DATA": 4})
        self.assertEqual(got["units"], 4)

    def test_a_receipt_list_that_is_not_a_list_reads_as_none(self):
        for bad in (None, "verified", {"state": "verified"}, 7):
            with self.subTest(bad=bad):
                got = _br.delivery_conclusion(bad, None, None, True, None,
                                              None)
                self.assertEqual(got["state"], "UNVERIFIED", got)
                self.assertEqual(got["units"], 0)

    def test_ten_mixed_units_add_up(self):
        states = ["verified"] * 4 + ["no-data"] * 3 + ["refused"] * 2 + ["?"]
        got = _br.delivery_conclusion(
            [_rcpt("U%d" % i, s) for i, s in enumerate(states)],
            [], [], True, [], None)
        self.assertEqual(got["units"], 10)
        self.assertEqual(sum(got["counts"].values()), 10)
        self.assertEqual(got["counts"], {"PASS": 4, "FAIL": 2, "NO-DATA": 4})
        self.assertEqual(got["state"], "NOT DONE")

    def test_deterministic_and_inputs_untouched(self):
        args = ([_rcpt("A1", "verified"), _rcpt("N1", "no-data")],
                [("B1", "why")], ["P1"], True, [("r", "A1", "w")],
                {"choice": "hold"})
        before = copy.deepcopy(args)
        one = _br.delivery_conclusion(*args)
        two = _br.delivery_conclusion(*args)
        self.assertEqual(one, two)
        self.assertEqual(args, before)


class NoWorkerProseCanEnter(unittest.TestCase):
    """REQ-TL1-A-NOPROSE (M-TL1A-PROSE): exactly six parameters, and a
    seventh argument carrying prose is refused outright."""

    def test_the_signature_has_exactly_the_six_facts(self):
        names = list(inspect.signature(_br.delivery_conclusion).parameters)
        self.assertEqual(names, ["final_receipts", "refused", "parked",
                                 "receipt_available", "risk_triggers",
                                 "release_answer"])

    def test_t10_prose_cannot_be_passed_or_read(self):
        args = CASES[9][1]
        self.assertEqual(_br.delivery_conclusion(*args)["state"],
                         "UNVERIFIED")
        with self.assertRaises(TypeError):
            _br.delivery_conclusion(*args, "BROTHER RESULT: DONE")


class AgreesWithTheOldRuleOutsideTheTightening(unittest.TestCase):
    """REQ-TL1-A-AGREE: over T01 to T09 minus T03, the helper read the way
    _exit_code_for reads it (no parked list, receipt present, no release
    facts) gives the old (code, reason) word for word."""

    def test_parity(self):
        for cid, args, _want in CASES[:9]:
            if cid == "T03":
                continue
            with self.subTest(case=cid):
                receipts, refused = args[0], args[1]
                got = _br.delivery_conclusion(receipts, refused, (), True,
                                              (), None)
                self.assertEqual(
                    (_br.EXIT_FOR_STATE[got["state"]], got["reason"]),
                    _old_exit_code_for(receipts, refused), cid)

    def test_t03_is_the_one_named_tightening(self):
        receipts = CASES[2][1][0]
        got = _br.delivery_conclusion(receipts, [], (), True, (), None)
        self.assertEqual(_old_exit_code_for(receipts, [])[0], 0)
        self.assertEqual(_br.EXIT_FOR_STATE[got["state"]], 2)


if __name__ == "__main__":
    unittest.main()

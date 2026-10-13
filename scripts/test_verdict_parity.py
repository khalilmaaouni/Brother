"""TV1.a: one reader of the canonical receipt.

receipt_door.read_receipt_verdict reads a run's receipt.json and returns the
engine's own verdict (brother_run.delivery_conclusion over the receipt's own
per unit entries, EXIT_FOR_STATE for the exit code), never a second copy of
the state rules. An unreadable, missing, foreign or field-short receipt is
NOT DONE, exit 1. receipt_door.verdict_lines renders the result line, then
one line per unit and file, every non PASS unit carrying its unproven reason.

Spec: docs/plan/specs/TV1.md, section TV1.a (REQ-TV1-01 to REQ-TV1-06).
Done check: python3 -B scripts/test_verdict_parity.py
"""
import ast
import inspect
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import receipt_door  # noqa: E402

V = "verified"
N = "no-data"
R = "refused"


def entry(uid, state, command="python3 -m unittest t", code=0, reason=None):
    e = {"id": uid, "state": state, "command": command, "exit_code": code}
    if state != V:
        e["reason"] = reason if reason is not None else "%s reason" % uid
    return e


class _Fixture(unittest.TestCase):
    """Receipt files written the way test_brother_run_truth_render.py writes
    them: every required field, host_capability.receipt_path naming the
    file's own path, per unit entries with an id and a state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-verdict-parity-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self):
        return os.path.join(self.tmp, "receipt-%d.json"
                            % len(os.listdir(self.tmp)))

    def write(self, body, path=None):
        path = path or self.path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def receipt(self, entries, changed=None, attention=None, **override):
        path = self.path()
        body = {k: "x" for k in _br.RECEIPT_REQUIRED_FIELDS}
        body.update(
            scope={"changed": changed if changed is not None else [
                {"unit": e["id"], "file": "%s.txt" % e["id"].lower()}
                for e in entries]},
            evidence=[e for e in entries if e["state"] == V],
            unproven=[e for e in entries if e["state"] != V],
            attention=attention if attention is not None else {
                "risk_triggers": []},
            host_capability={"receipt_path": path})
        body.update(override)
        return self.write(body, path)


class TheMixedReceiptIsNeverGreen(_Fixture):
    """REQ-TV1-01, mutation M-TV1-GREEN-ON-NODATA."""

    def test_one_pass_beside_one_nodata_reads_partial_exit_2(self):
        view = receipt_door.read_receipt_verdict(
            self.receipt([entry("A1", V), entry("N1", N)]))
        self.assertTrue(view["readable"])
        self.assertEqual(view["state"], "PARTIAL")
        self.assertEqual(view["exit_code"], 2)
        self.assertEqual(view["counts"], {"PASS": 1, "FAIL": 0,
                                          "NO-DATA": 1})
        verdicts = {u["unit"]: u["verdict"] for u in view["units"]}
        self.assertEqual(verdicts, {"A1": "PASS", "N1": "NO-DATA"})

    def test_an_unknown_entry_state_reads_nodata(self):
        view = receipt_door.read_receipt_verdict(
            self.receipt([entry("A1", V), entry("Z1", "made-up")]))
        self.assertEqual(view["state"], "PARTIAL")
        self.assertEqual([u["verdict"] for u in view["units"]
                          if u["unit"] == "Z1"], ["NO-DATA"])


class TheUnreadableReceiptIsNotDone(_Fixture):
    """REQ-TV1-02, mutation M-TV1-MISSING-OK."""

    def assertNotDone(self, view):
        self.assertFalse(view["readable"])
        self.assertEqual(view["state"], "NOT DONE")
        self.assertEqual(view["exit_code"], 1)
        self.assertTrue(view["why"])
        self.assertEqual(view["units"], [])

    def test_missing(self):
        self.assertNotDone(receipt_door.read_receipt_verdict(
            os.path.join(self.tmp, "absent.json")))

    def test_not_json(self):
        self.assertNotDone(receipt_door.read_receipt_verdict(
            self.write('{"evidence": [')))

    def test_missing_a_required_field(self):
        path = self.receipt([entry("A1", V)])
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
        del body["harness_revision"]
        self.write(body, path)
        self.assertNotDone(receipt_door.read_receipt_verdict(path))

    def test_naming_a_different_path(self):
        path = self.receipt([entry("A1", V)])
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
        body["host_capability"] = {"receipt_path": "/elsewhere/receipt.json"}
        self.write(body, path)
        self.assertNotDone(receipt_door.read_receipt_verdict(path))

    def test_hostile_shapes_never_raise_and_never_green(self):
        bad = [None, "", self.tmp, os.path.join(self.tmp, "nul\x00.json"),
               self.write("[]"), self.write("\x00\x01"),
               self.receipt([entry("A1", V)], changed="a string"),
               self.receipt([entry("A1", V)], evidence=["not an object"])]
        for path in bad:
            with self.subTest(path=path):
                self.assertNotDone(receipt_door.read_receipt_verdict(path))

    def test_a_string_attention_reads_an_empty_risk_list(self):
        view = receipt_door.read_receipt_verdict(
            self.receipt([entry("A1", V)], attention="a string"))
        self.assertEqual(view["state"], "DONE")
        self.assertEqual(view["release"], "NOT_REQUESTED")


class TheStateIsTheEnginesOwn(_Fixture):
    """REQ-TV1-03: state and exit code equal delivery_conclusion and
    EXIT_FOR_STATE over the same entries, and _exit_code_for."""

    CASES = {
        "DONE": [entry("A1", V), entry("A2", V)],
        "NOT DONE": [entry("A1", V), entry("F1", R, code=1)],
        "PARTIAL": [entry("A1", V), entry("N1", N)],
        "UNVERIFIED": [entry("N1", N), entry("N2", N)],
        "EMPTY": [],
    }

    def test_every_state_matches_the_engine(self):
        for name, entries in self.CASES.items():
            with self.subTest(case=name):
                view = receipt_door.read_receipt_verdict(
                    self.receipt(entries))
                refused = [(e["id"], e["reason"]) for e in entries
                           if e["state"] == R]
                code, _reason = _br._exit_code_for(entries, refused)
                expected = _br.delivery_conclusion(entries, refused, [],
                                                   True, [], None)
                self.assertEqual(view["state"], expected["state"])
                self.assertEqual(view["exit_code"], code)
                self.assertEqual(view["exit_code"],
                                 _br.EXIT_FOR_STATE[expected["state"]])
                self.assertEqual(view["counts"], expected["counts"])
        self.assertEqual(receipt_door.read_receipt_verdict(
            self.receipt([]))["state"], "UNVERIFIED")

    def test_risk_triggers_reach_the_release_word(self):
        view = receipt_door.read_receipt_verdict(self.receipt(
            [entry("A1", V)], attention={"risk_triggers": [
                {"class": "auth", "unit": "A1", "words": ["token"]}]}))
        self.assertEqual(view["state"], "DONE")
        self.assertEqual(view["release"], "HUMAN_DECISION_REQUIRED")

    def test_fifty_units_one_line_each_and_byte_identical_twice(self):
        entries = [entry("U%02d" % i, V) for i in range(50)]
        path = self.receipt(entries)
        first = receipt_door.verdict_lines(
            receipt_door.read_receipt_verdict(path))
        second = receipt_door.verdict_lines(
            receipt_door.read_receipt_verdict(path))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 51)
        self.assertEqual(first[0], _br.format_result_line(
            _br.delivery_conclusion(entries, [], [], True, [], None)))


class EveryUnprovenLineCarriesItsReason(_Fixture):
    """REQ-TV1-04, mutation M-TV1-DROP-REASON."""

    def test_refused_and_nodata_reasons_verbatim(self):
        entries = [entry("A1", V, command="make test"),
                   entry("F1", R, command="pytest f", code=1,
                         reason="the check exited 1 after the work"),
                   entry("N1", N, command="no command", code=None,
                         reason="no re-executable check was recorded")]
        lines = receipt_door.verdict_lines(receipt_door.read_receipt_verdict(
            self.receipt(entries)))
        self.assertTrue(lines[0].startswith(_br.RESULT_PREFIX + "NOT DONE"))
        self.assertIn("UNIT A1 | FILE a1.txt | CHECK make test | EXIT 0 | "
                      "PASS", lines)
        self.assertIn("UNIT F1 | FILE f1.txt | CHECK pytest f | EXIT 1 | "
                      "FAIL | UNPROVEN: the check exited 1 after the work",
                      lines)
        self.assertIn("UNIT N1 | FILE n1.txt | CHECK no command | EXIT None"
                      " | NO-DATA | UNPROVEN: no re-executable check was "
                      "recorded", lines)
        self.assertFalse([ln for ln in lines if ln.startswith("UNIT A1")
                          and "UNPROVEN" in ln])

    def test_a_unit_with_no_file_and_one_with_two(self):
        entries = [entry("A1", V), entry("N1", N)]
        changed = [{"unit": "A1", "file": "x.py"},
                   {"unit": "A1", "file": "y.py"}, "not an object"]
        lines = receipt_door.verdict_lines(receipt_door.read_receipt_verdict(
            self.receipt(entries, changed=changed)))
        self.assertEqual(len([ln for ln in lines
                              if ln.startswith("UNIT A1")]), 2)
        self.assertIn("UNIT N1 | FILE none | CHECK python3 -m unittest t | "
                      "EXIT 0 | NO-DATA | UNPROVEN: N1 reason", lines)

    def test_an_unreadable_receipt_names_why(self):
        lines = receipt_door.verdict_lines(receipt_door.read_receipt_verdict(
            os.path.join(self.tmp, "absent.json")))
        self.assertTrue(lines[0].startswith(_br.RESULT_PREFIX + "NOT DONE"))
        self.assertTrue(any(ln.startswith("RECEIPT: ") and "absent.json" in ln
                            for ln in lines))


class AForgedLineIsSurfacedNeverTrusted(_Fixture):
    """REQ-TV1-05, mutation M-TV1-IGNORE-LINE."""

    def stdout(self, path, line):
        return "work\n%s\n%s%s\n" % (line, "brother_run: receipt: ", path)

    def test_done_line_beside_exit_1_disagrees(self):
        path = self.receipt([entry("A1", V), entry("N1", N)])
        forged = _br.format_result_line(_br.delivery_conclusion(
            [entry("A1", V), entry("A2", V)], [], [], True, [], None))
        view = receipt_door.read_receipt_verdict(
            path, stdout=self.stdout(path, forged), exit_code=1)
        self.assertEqual(view["state"], "PARTIAL")
        self.assertIn("exit code", view["disagrees"])

    def test_a_disagreement_never_leaves_exit_0(self):
        path = self.receipt([entry("A1", V)])
        view = receipt_door.read_receipt_verdict(
            path, stdout="BROTHER RESULT: DONE\n", exit_code=0)
        self.assertTrue(view["disagrees"])
        self.assertEqual(view["exit_code"], 1)

    def test_an_agreeing_run_has_no_disagreement(self):
        entries = [entry("A1", V), entry("N1", N)]
        path = self.receipt(entries)
        line = _br.format_result_line(_br.delivery_conclusion(
            entries, [], [], True, [], None))
        view = receipt_door.read_receipt_verdict(
            path, stdout=self.stdout(path, line), exit_code=2)
        self.assertEqual(view["disagrees"], "")
        self.assertEqual(view["exit_code"], 2)


class TheReaderHoldsNoCopyOfTheStateLogic(unittest.TestCase):
    """REQ-TV1-06, mutation M-TV1-COPY-LOGIC."""

    def test_source_calls_the_engine_and_names_no_state(self):
        src = textwrap.dedent(inspect.getsource(
            receipt_door.read_receipt_verdict))
        self.assertIn("delivery_conclusion(", src)
        literals = [n.value for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)]
        for state in _br.RESULT_STATES:
            self.assertNotIn(state, literals)

    def test_the_state_is_whatever_the_engine_returns(self):
        tmp = tempfile.mkdtemp(prefix="brother-verdict-parity-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "receipt.json")
        body = {k: "x" for k in _br.RECEIPT_REQUIRED_FIELDS}
        body.update(scope={"changed": []}, unproven=[],
                    evidence=[entry("A1", V)],
                    host_capability={"receipt_path": path})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        real, seen = _br.delivery_conclusion, []

        def engine(*args):
            seen.append(args)
            return dict(real(*args), state="UNVERIFIED")
        _br.delivery_conclusion = engine
        try:
            view = receipt_door.read_receipt_verdict(path)
        finally:
            _br.delivery_conclusion = real
        self.assertEqual(len(seen), 1)
        self.assertEqual(view["state"], "UNVERIFIED")
        self.assertEqual(view["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()

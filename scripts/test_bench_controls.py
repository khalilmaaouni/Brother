"""BK1.a tests: the frozen controls, the cross arm refusal and the scoring script digest (scripts/bench_controls.py).

Every failure fixture is a copy of one complete synthetic fixture with exactly one control removed, so a deleted
guard turns exactly its own case red. The scoring script is a byte copy of the real one under a temporary root.
Run from the repository root: python3 -B scripts/test_bench_controls.py
"""
import contextlib
import copy
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import bench_controls as BC  # noqa: E402

REAL_CONTROLS = os.path.join(REPO, "benchmarks", "competitive", "CONTROLS.json")
OLD_KEYS = ("spec_version", "source", "gate_before_use", "model_id", "start_commit", "token_budget",
            "intervention_budget", "machine", "competitors", "raw_artifacts", "publish_rule")
BASE_NAMES = ("intervention_ceiling", "machine", "model", "result_root", "source_commit", "token_ceiling")
HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = BC.main(argv)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bench_controls_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.script = os.path.join(self.root, BC.SCORING_SCRIPT)
        os.makedirs(os.path.dirname(self.script))
        shutil.copyfile(os.path.join(REPO, BC.SCORING_SCRIPT), self.script)
        self.digest = BC.sha256_file(self.script)

    def result(self, digest=None):
        return {"dir": "benchmarks/results/round/arm", "recorded_at": "2026-10-12T00:00:00Z",
                "scoring_sha256": digest or self.digest,
                "dimensions": {"tests": "PASS", "scope_creep": "FAIL", "evidence_quality": "PASS",
                               "false_claims": "PASS", "interventions": 2, "tokens": 1234.5}}

    def complete(self):
        fields = ["hostname", "os", "os_version", "cpu_model", "cpu_count", "memory_gb", "measured_at"]
        return {
            "model_id": "model-under-test",
            "model_exception": "NO-DATA: none disclosed",
            "start_commit": "ce3f05034d593e784fdaf75e07200024c375f13c",
            "token_budget": 200000,
            "intervention_budget": 3,
            "machine": {"descriptor_fields": fields,
                        "captured": {"hostname": "h", "os": "Darwin", "os_version": "27.0", "cpu_model": "c",
                                     "cpu_count": 8, "memory_gb": 16, "measured_at": "2026-10-12"}},
            "raw_artifacts": {"root": "benchmarks/results/round"},
            "scoring": {"script": BC.SCORING_SCRIPT, "sha256": self.digest},
            "arms": {a: {"installed_ref": "v1.0.0", "result": self.result()} for a in BC.REQUIRED_ARMS},
        }

    def write(self, doc, name="CONTROLS.json"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        return path


class TestShippedFile(Base):
    def test_req_bk1_01_shipped_controls_keys(self):
        with open(REAL_CONTROLS, encoding="utf-8") as fh:
            doc = json.load(fh)
        for k in OLD_KEYS:
            self.assertIn(k, doc)
        self.assertEqual(sorted(doc["arms"]), sorted(BC.REQUIRED_ARMS))
        self.assertEqual(len(doc["arms"]), 8)
        for entry in doc["arms"].values():
            self.assertTrue(entry["installed_ref"].startswith("NO-DATA"))
            self.assertIsNone(entry["result"])
        self.assertEqual(doc["scoring"]["script"], BC.SCORING_SCRIPT)
        self.assertTrue(doc["scoring"]["sha256"].startswith("NO-DATA"))
        self.assertTrue(doc["model_exception"].startswith("NO-DATA"))
        self.assertIsNone(doc["machine"]["captured"])
        self.assertEqual(doc["machine"]["descriptor_fields"][0], "hostname")
        self.assertTrue(doc["model_id"].startswith("NO-DATA"))
        self.assertTrue(doc["raw_artifacts"]["root"].startswith("NO-DATA"))

    def test_req_bk1_08_real_file_refused_without_score(self):
        code, out, _ = run_main(["--check", REAL_CONTROLS])
        self.assertEqual(code, 2)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^COMPOSITE: refused, \d+ reason\(s\)$")
        for name in list(BASE_NAMES) + ["installed_ref:" + a for a in BC.REQUIRED_ARMS]:
            self.assertIn("control missing: %s" % name, out)
        for a in BC.REQUIRED_ARMS:
            self.assertIn("arm %s has no result" % a, out)
        # no number that could be read as a score or a ranking, beyond the reason count of the header line
        rest = "\n".join(lines[1:])
        for token in list(BC.REQUIRED_ARMS) + [BC.SCORING_SCRIPT, "sha256"]:
            rest = rest.replace(token, "")
        self.assertNotRegex(rest, r"\d")
        self.assertNotRegex(out.lower(), r"\brank|\bscore\b|composite: \d|%")


class TestControls(Base):
    def test_req_bk1_02_complete_fixture_allowed(self):
        self.assertEqual(BC.missing_controls(self.complete()), [])
        self.assertEqual(BC.composite_decision(self.complete(), self.root), ("PASS", []))

    def test_req_bk1_02_main_allowed_with_real_digest(self):
        doc = self.complete()
        code, out, _ = run_main(["--check", self.write(doc)])
        # every control is set and the copied script matches the real one byte for byte
        self.assertEqual(out.splitlines()[0], "COMPOSITE: allowed")
        self.assertEqual(code, 0)

    def single_fault(self, mutate, name):
        doc = self.complete()
        mutate(doc)
        self.assertEqual(BC.missing_controls(doc), [name])
        verdict, reasons = BC.composite_decision(doc, self.root)
        self.assertEqual((verdict, reasons), ("NO-DATA", ["control missing: " + name]))

    def test_req_bk1_02_missing_model(self):
        self.single_fault(lambda d: d.update(model_id="NO-DATA: unnamed"), "model")

    def test_req_bk1_02_model_exception_is_enough(self):
        doc = self.complete()
        doc["model_id"] = None
        doc["model_exception"] = "arm bmad ran on another model because the named one was unavailable"
        self.assertEqual(BC.missing_controls(doc), [])
        code, out, _ = run_main(["--check", self.write(doc)])
        self.assertEqual(code, 0)
        self.assertIn("DISCLOSED COMPARABILITY EXCEPTION: arm bmad ran", out)

    def test_req_bk1_02_missing_source_commit(self):
        self.single_fault(lambda d: d.pop("start_commit"), "source_commit")

    def test_req_bk1_02_missing_token_ceiling(self):
        self.single_fault(lambda d: d.update(token_budget="10"), "token_ceiling")

    def test_req_bk1_02_missing_intervention_ceiling(self):
        self.single_fault(lambda d: d.update(intervention_budget=None), "intervention_ceiling")

    def test_req_bk1_02_missing_machine(self):
        self.single_fault(lambda d: d["machine"]["captured"].update(cpu_model=""), "machine")
        self.single_fault(lambda d: d["machine"].update(captured=None), "machine")

    def test_req_bk1_02_missing_result_root(self):
        self.single_fault(lambda d: d["raw_artifacts"].update(root="NO-DATA: later"), "result_root")

    def test_req_bk1_03_missing_installed_ref_each_arm(self):
        for arm in BC.REQUIRED_ARMS:
            with self.subTest(arm=arm):
                self.single_fault(lambda d: d["arms"][arm].update(installed_ref="NO-DATA: unread"),
                                  "installed_ref:" + arm)

    def test_empty_and_corrupt_controls_miss_everything(self):
        every = sorted(list(BASE_NAMES) + ["installed_ref:" + a for a in BC.REQUIRED_ARMS])
        self.assertEqual(BC.missing_controls([]), every)
        self.assertEqual(BC.missing_controls({}), every)
        self.assertEqual(BC.composite_decision([], self.root)[0], "NO-DATA")
        bad = os.path.join(self.root, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        self.assertEqual(run_main(["--check", bad])[0], 2)
        self.assertEqual(run_main(["--check", self.write([1, 2])])[0], 2)
        self.assertEqual(run_main(["--check", os.path.join(self.root, "absent.json")])[0], 2)


class TestResults(Base):
    def test_req_bk1_04_no_data_dimension(self):
        doc = self.complete()
        doc["arms"]["gsd"]["result"]["dimensions"]["tokens"] = "NO-DATA"
        self.assertEqual(BC.composite_decision(doc, self.root),
                         ("NO-DATA", ["arm gsd dimension tokens is NO-DATA"]))

    def test_unrecognized_dimension_verdict(self):
        doc = self.complete()
        doc["arms"]["bmad"]["result"]["dimensions"]["tests"] = "GREEN"
        verdict, reasons = BC.composite_decision(doc, self.root)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(reasons, ["arm bmad dimension tests holds an unrecognized verdict"])
        doc["arms"]["bmad"]["result"]["dimensions"]["tests"] = float("nan")
        self.assertEqual(BC.composite_decision(doc, self.root)[0], "NO-DATA")

    def test_req_bk1_05_missing_arm_result(self):
        doc = self.complete()
        doc["arms"]["vanilla_claude_code"]["result"] = None
        self.assertEqual(BC.composite_decision(doc, self.root),
                         ("NO-DATA", ["arm vanilla_claude_code has no result"]))

    def test_one_arm_filled(self):
        doc = self.complete()
        for a in BC.REQUIRED_ARMS[1:]:
            doc["arms"][a]["result"] = None
        verdict, reasons = BC.composite_decision(doc, self.root)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(len(reasons), 7)

    def test_extra_arm_ignored_and_listed(self):
        doc = self.complete()
        doc["arms"]["another_tool"] = {"installed_ref": "NO-DATA", "result": None}
        self.assertEqual(BC.composite_decision(doc, self.root), ("PASS", []))
        code, out, _ = run_main(["--check", self.write(doc)])
        self.assertIn("EXTRA ARM, ignored for the decision: another_tool", out)

    def edit_one_byte(self):
        with open(self.script, "ab") as fh:
            fh.write(b" ")

    def test_req_bk1_06_script_changed_is_fail(self):
        self.edit_one_byte()
        verdict, reasons = BC.composite_decision(self.complete(), self.root)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("scoring script %s digest differs from the frozen scoring.sha256" % BC.SCORING_SCRIPT,
                      reasons)
        doc = self.complete()   # frozen digest of the old bytes, and no result recorded yet: still FAIL
        for a in BC.REQUIRED_ARMS:
            doc["arms"][a]["result"] = None
        verdict, reasons = BC.composite_decision(doc, self.root)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(reasons,
                         ["scoring script %s digest differs from the frozen scoring.sha256" % BC.SCORING_SCRIPT])

    def test_unreadable_script_is_fail(self):
        os.remove(self.script)
        verdict, reasons = BC.composite_decision(self.complete(), self.root)
        self.assertEqual(verdict, "FAIL")
        self.assertIn(BC.SCORING_SCRIPT, reasons[0])

    def test_req_bk1_07_stale_result_is_fail(self):
        doc = self.complete()
        doc["arms"]["compound"]["result"]["scoring_sha256"] = "0" * 64
        self.assertEqual(BC.composite_decision(doc, self.root),
                         ("FAIL", ["scoring script changed after arm compound was recorded"]))
        code, out, _ = run_main(["--check", self.write(doc)])
        self.assertEqual(code, 1)

    def test_digest_stable(self):
        self.assertEqual(BC.sha256_file(self.script), self.digest)
        self.assertEqual(len(self.digest), 64)
        self.assertEqual(BC.sha256_file(os.path.join(self.root, "absent")), "")


class TestFreeze(Base):
    def test_req_bk1_09_freeze_writes_only_digest(self):
        doc = self.complete()
        doc["scoring"]["sha256"] = "NO-DATA: not frozen"
        for a in BC.REQUIRED_ARMS:
            doc["arms"][a]["result"] = None
        path = self.write(doc)
        self.assertEqual(BC.freeze_scoring_digest(path, self.root), self.digest)
        with open(path, encoding="utf-8") as fh:
            after = json.load(fh)
        expected = copy.deepcopy(doc)
        expected["scoring"]["sha256"] = self.digest
        self.assertEqual(after, expected)
        with open(path, "rb") as fh:
            first = fh.read()
        self.assertEqual(BC.freeze_scoring_digest(path, self.root), self.digest)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), first)
        self.assertEqual([n for n in os.listdir(self.root) if n.endswith(".tmp")], [])

    def test_req_bk1_09_freeze_refused_over_result(self):
        doc = self.complete()
        doc["arms"]["gsd"]["result"]["scoring_sha256"] = "f" * 64
        path = self.write(doc)
        with open(path, "rb") as fh:
            before = fh.read()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(BC.freeze_scoring_digest(path, self.root), "")
        self.assertIn("gsd", err.getvalue())
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(run_main(["--freeze-digest", path])[0], 1)

    def test_freeze_refuses_corrupt(self):
        bad = os.path.join(self.root, "bad.json")
        with open(bad, "w") as fh:
            fh.write("[1]")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(BC.freeze_scoring_digest(bad, self.root), "")
            self.assertEqual(BC.freeze_scoring_digest(self.write(self.complete()), os.path.join(self.root, "no")),
                             "")


class TestHostile(unittest.TestCase):
    def test_hostile_inputs_refused_or_denied(self):
        allowed = (ValueError, LookupError, SystemExit)
        sink = io.StringIO()
        for v in HOSTILE:
            with self.subTest(value=repr(v)):
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    for fn, args in ((BC.sha256_file, (v,)), (BC.missing_controls, (v,)),
                                     (BC.composite_decision, (v, v)), (BC.freeze_scoring_digest, (v, v)),
                                     (BC.main, (v,))):
                        try:
                            got = fn(*args)
                        except allowed:
                            continue
                        if fn is BC.composite_decision:
                            self.assertNotEqual(got[0], "PASS")
                        if fn is BC.freeze_scoring_digest:
                            self.assertEqual(got, "")
                        if fn is BC.main:
                            self.assertNotEqual(got, 0)
        self.assertNotEqual(BC.composite_decision({"token_budget": True, "arms": [1]}, REPO)[0], "PASS")
        self.assertIn("token_ceiling", BC.missing_controls({"token_budget": True}))
        self.assertIn("token_ceiling", BC.missing_controls({"token_budget": math.inf}))
        self.assertIn("machine", BC.missing_controls({"machine": {"descriptor_fields": [["x"]], "captured": {}}}))


if __name__ == "__main__":
    unittest.main()

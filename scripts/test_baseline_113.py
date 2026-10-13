#!/usr/bin/env python3
"""BL1.a: the frozen 1.1.2 baseline and its checker. Every requirement REQ-BL1-01 to 07 has a case here."""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import baseline_113 as B  # noqa: E402

ROOT = os.path.dirname(HERE)
REAL = os.path.join(ROOT, "benchmarks", "results", "1.1.3-preflight", "baseline.json")


def _doc(root):
    logs = os.path.join(root, B.LOGS)
    os.makedirs(logs, exist_ok=True)
    entries = []
    for n in B.ENTRY_NAMES:
        p = os.path.join(logs, n + ".log")
        with open(p, "w") as fh:
            fh.write("log of %s\npass 59   fail 1   no-data 0\n" % n)
        counts = {"pass": 59, "fail": 1, "no_data": 0} if n == "required_fast" else None
        rc = 1 if n == "required_fast" else 0
        entries.append({"name": n, "command": "c", "exit_code": rc, "counts": counts, "log": n + ".log",
                        "log_sha256": B.sha256_file(p), "captured_at": "2026-10-12T02:35:00", "provenance": "EXECUTED",
                        "verdict": B.derive_verdict(rc, counts, "EXECUTED")})
    return {"schema": B.SCHEMA, "v112_tag_commit": "a" * 40, "hub_release_commit": "b" * 40, "base_commit": "NO-DATA",
            "python3": "Python 3.13", "usr_bin_python3": "Python 3.9", "macos": "27.0", "entries": entries}


class TheChecker(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bl1-")
        self.doc = _doc(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_valid_baseline_has_no_problem(self):
        self.assertEqual(B.check_baseline(self.doc, self.root), [])

    def test_every_missing_key_is_named(self):   # REQ-BL1-01
        for k in B.TOP_KEYS:
            d = copy.deepcopy(self.doc); del d[k]
            self.assertTrue(any(k in p for p in B.check_baseline(d, self.root)), k)
        for k in B.ENTRY_KEYS:
            d = copy.deepcopy(self.doc); del d["entries"][1][k]
            self.assertTrue(any(k in p for p in B.check_baseline(d, self.root)), k)
        d = copy.deepcopy(self.doc); d["entries"] = d["entries"][:1]
        self.assertEqual(sum("entry missing" in p for p in B.check_baseline(d, self.root)), 3)

    def test_unknown_values_are_refused(self):   # REQ-BL1-02
        for v in ("GREEN", "pass", ""):
            d = copy.deepcopy(self.doc); d["entries"][1]["verdict"] = v
            self.assertTrue(B.check_baseline(d, self.root), v)
        d = copy.deepcopy(self.doc); d["entries"][1]["provenance"] = "LIVE"
        self.assertTrue(B.check_baseline(d, self.root))
        d = copy.deepcopy(self.doc); d["entries"].append(dict(d["entries"][0], name="fifth"))
        self.assertTrue(any("unrecognized" in p for p in B.check_baseline(d, self.root)))
        self.assertEqual(B.check_baseline([], self.root), ["the baseline is not a JSON object"])

    def test_a_fail_cannot_be_stored_as_a_pass(self):   # REQ-BL1-03
        d = copy.deepcopy(self.doc); d["entries"][0]["verdict"] = "PASS"
        self.assertTrue(any("derive FAIL" in p for p in B.check_baseline(d, self.root)))
        self.assertEqual(B.derive_verdict(0, {"pass": 59, "fail": 1, "no_data": 0}, "EXECUTED"), "FAIL")
        self.assertEqual(B.derive_verdict(0, {"no_data": -1, "set": -1}, "EXECUTED"), "NO-DATA")
        self.assertEqual(B.derive_verdict(None, None, "EXECUTED"), "NO-DATA")
        self.assertEqual(B.derive_verdict("0", None, "EXECUTED"), "NO-DATA")
        self.assertEqual(B.derive_verdict(0, None, "NO-DATA"), "NO-DATA")
        self.assertEqual(B.derive_verdict(0, {"pass": 3, "fail": 0, "no_data": 0}, "EXECUTED"), "PASS")

    def test_a_tampered_log_is_refused(self):   # REQ-BL1-04
        p = os.path.join(self.root, B.LOGS, "version_truth.log")
        with open(p, "a") as fh:
            fh.write("x")
        self.assertTrue(any("sha256" in q for q in B.check_baseline(self.doc, self.root)))

    def test_the_home_path_is_redacted(self):   # REQ-BL1-05
        self.assertEqual(B.redact("/Users/someone/x and /Users/someone", "/Users/someone"), "<HOME>/x and <HOME>")
        self.assertIsNone(B.parse_counts("no counts here"))
        self.assertEqual(B.parse_counts("pass 59   fail 1   no-data 0"), {"pass": 59, "fail": 1, "no_data": 0})

    def test_an_absent_baseline_is_no_data(self):   # REQ-BL1-07
        with redirect_stdout(io.StringIO()) as out:
            rc = B.main(["--check", os.path.join(self.root, "absent.json")])
        self.assertEqual(rc, 2)
        self.assertIn("NO-DATA", out.getvalue())

    def test_a_second_capture_never_overwrites(self):
        with open(os.path.join(self.root, "baseline.json"), "w") as fh:
            fh.write("{}")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(B.capture(self.root, {}), 1)
        self.assertIn("never rewritten", out.getvalue())


class TheFrozenFile(unittest.TestCase):   # REQ-BL1-06
    def test_the_committed_baseline_passes_and_holds_the_measured_fail(self):
        with open(REAL, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(B.check_baseline(doc, ROOT), [])
        rf = [e for e in doc["entries"] if e["name"] == "required_fast"][0]
        self.assertEqual((rf["verdict"], rf["counts"]), ("FAIL", {"pass": 59, "fail": 1, "no_data": 0}))
        home = os.path.expanduser("~")
        for n in os.listdir(os.path.join(ROOT, B.LOGS)):
            with open(os.path.join(ROOT, B.LOGS, n), encoding="utf-8", errors="replace") as fh:
                self.assertNotIn(home, fh.read(), n)


if __name__ == "__main__":
    unittest.main()

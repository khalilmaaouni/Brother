#!/usr/bin/env python3
"""FR0.e: the handover's Public gate summary reads the cut ready record, for the candidate's tree, with exact counts;
the 1.1.4 runbook starts with cut_ready. Reuses the fixtures of scripts/test_rc_handover.py (read only)."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rc_handover as R  # noqa: E402
from test_rc_handover import Base, git  # noqa: E402

RUNBOOK = os.path.join(ROOT, "docs", "plan", "specs", "RUNBOOK-1.1.4.md")
PHASES = ("ready", "freeze", "preflight", "rehearsal", "adopt", "cut", "after")


def record(tree, rows=("PASS", "PASS", "PASS"), verdict=None):
    rows = [{"name": "row%d" % i, "verdict": v} for i, v in enumerate(rows)]
    computed = "FAIL" if any(r["verdict"] == "FAIL" for r in rows) else (
        "NO-DATA" if any(r["verdict"] == "NO-DATA" for r in rows) else "PASS")
    return {"schema": R.READY_SCHEMA, "version": "1.1.4", "candidate_tree": tree, "verdict": verdict or computed,
            "epoch": 1, "rows": rows}


class TestFR0E(Base, unittest.TestCase):  # unittest.TestCase named so the plan lint sees a test class
    LABEL = "Public gate summary"

    def setUp(self):
        super().setUp()
        self.tree = git(self.root, "rev-parse", "HEAD^{tree}")

    def ready(self, rec, value="PASS pass 3 fail 0 no-data 0", text=None):
        self.write_source("ready.json", text if text is not None else json.dumps(rec) + "\n")
        return self.check(self.with_field(self.LABEL, value + " (source=ready.json)"))

    def test_the_handover_field_reads_the_cut_ready_record(self) -> None:
        self.assertEqual(self.ready(record(self.tree)), [])
        self.assertEqual(R.ready_counts(record(self.tree, ("PASS", "FAIL", "NO-DATA"))), (1, 1, 1))

    def test_a_record_for_another_tree_is_refused(self) -> None:
        self.assertRefused(self.ready(record("b" * 40)), "describes another tree")

    def test_a_pass_beside_failing_rows_or_wrong_counts_is_refused(self) -> None:
        self.assertRefused(self.ready(record(self.tree, ("PASS", "FAIL", "PASS"), verdict="PASS")),
                           "says PASS but its source says NO-DATA")
        self.assertRefused(self.ready(record(self.tree, ("PASS", "FAIL", "PASS"))), "says PASS but its source says FAIL")
        self.assertRefused(self.ready(record(self.tree), value="PASS pass 4 fail 0 no-data 0"), "differ from the record's rows")
        self.assertRefused(self.ready(record(self.tree), value="PASS all green"), "differ from the record's rows")

    def test_a_torn_record_never_supports_a_pass(self) -> None:
        whole = json.dumps(record(self.tree), indent=1)
        torn = whole[: len(whole) // 2] + "\nsummary pass 3 fail 0 no-data 0\n"
        self.assertEqual(R.source_verdict(torn), "NO-DATA")
        self.assertRefused(self.ready(None, text=torn), "says PASS but its source says NO-DATA")
        for bad in (dict(record(self.tree), rows=[]), dict(record(self.tree), candidate_tree=None),
                    dict(record(self.tree), rows=[{"verdict": "GREEN"}]), dict(record(self.tree), schema="cv1.ready.0")):
            self.assertIsNone(R.ready_record_of(json.dumps(bad)), bad)
        self.assertEqual(R.source_verdict(json.dumps(dict(record(self.tree), rows=[{"verdict": "OK"}]))), "NO-DATA")

    def test_the_wbs_flag_reads_the_named_plan(self) -> None:
        with open(os.path.join(self.root, "docs", "plan", "BROTHER-1.1.4-WBS.json"), "w") as f:
            json.dump({"units": [{"id": "ZZ9"}]}, f)
        p = os.path.join(self.tmp, "HANDOVER.md")
        f = self.with_field("Landed U-items and linked tests", "ZZ9: python3 -B scripts/test_x.py")
        f = dict(f, **{"Reverted/refused items and reasons": "ZZ9: refused, a reason"})
        with open(p, "w") as fh:
            fh.write(self.render(f))
        self.assertRefused(R.check_handover(p, self.root, self.ev), "landed unit ZZ9 is not in the WBS")
        self.assertEqual(R.check_handover(p, self.root, self.ev, wbs="docs/plan/BROTHER-1.1.4-WBS.json"), [])

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
    def test_the_1_1_4_runbook_starts_with_cut_ready_and_names_every_phase(self) -> None:
        with open(RUNBOOK, "rb") as fh:
            raw = fh.read()
        text = raw.decode("ascii")
        self.assertNotRegex(text, "[\u2013\u2014]")
        steps = [l for l in text.splitlines() if "python3 scripts/" in l]
        self.assertIn("python3 scripts/cut_ready.py --version 1.1.4", steps[0])
        self.assertIn("python3 scripts/cut_driver.py run --version 1.1.4", text)
        for phase in PHASES:
            self.assertRegex(text, r"(?m)^### %s\b" % phase)
        self.assertIn("python3 scripts/rc_handover.py --check", text)
        self.assertIn("--wbs docs/plan/BROTHER-1.1.4-WBS.json", text)
        self.assertIn("the owner types the y", text)
        self.assertTrue(re.search(r"Public gate summary.*cut ready record", text), "the field must quote the record")


if __name__ == "__main__":
    unittest.main()

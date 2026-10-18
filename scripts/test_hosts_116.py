#!/usr/bin/env python3
"""XH1: every 1.1.6 unit the WBS marks DONE has a row in docs/plan/specs/1.1.6/HOSTS-1.1.6.md.

The record is the host half of the register's cross cutting acceptance: each landed done check was run once
through Codex (codex exec, workspace-write with a worktree local TMPDIR, because the read-only sandbox
cannot run a Python test). Row shape, one per unit:

    | unit | command | exit | verdict | quoted last line |

verdict is PASS, FAIL or NO-DATA. PASS needs exit 0 and an OK in the quote; FAIL needs a nonzero exit;
NO-DATA (Codex did not run it) needs exit "-". Every row quotes a line, NO-DATA included (the reason).
This test refuses a missing DONE unit, a blank quote, a foreign verdict or a verdict that contradicts its
own exit, and counts NO-DATA apart from PASS, never inside it.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = os.path.join(ROOT, "docs", "plan", "specs", "1.1.6", "HOSTS-1.1.6.md")
WBS = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.6-WBS.json")
VERDICTS = ("PASS", "FAIL", "NO-DATA")


def parse_rows(text):
    """unit -> dict(command, exit, verdict, quote) for each table row; header and rule lines skipped."""
    rows = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0].lower() == "unit" or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = dict(command=cells[1], exit=cells[2], verdict=cells[3],
                              quote=cells[4].strip("` ").strip())
    return rows


def problems(rows, landed):
    out = []
    for u in landed:
        r = rows.get(u)
        if r is None:
            out.append("%s: no row" % u)
            continue
        if r["verdict"] not in VERDICTS:
            out.append("%s: verdict %r is not one of %s" % (u, r["verdict"], "/".join(VERDICTS)))
        elif not r["quote"]:
            out.append("%s: no quoted line" % u)
        elif r["verdict"] == "PASS" and not (r["exit"] == "0" and "OK" in r["quote"]):
            out.append("%s: PASS without exit 0 and OK in the quote" % u)
        elif r["verdict"] == "FAIL" and r["exit"] in ("0", "-", ""):
            out.append("%s: FAIL without a nonzero exit" % u)
        elif r["verdict"] == "NO-DATA" and r["exit"] != "-":
            out.append("%s: NO-DATA with an exit" % u)
    return out


def counts(rows, landed):
    return {v: sum(1 for u in landed if rows.get(u, {}).get("verdict") == v) for v in VERDICTS}


def landed_units():
    with open(WBS, encoding="utf-8") as f:
        return [u["id"] for u in json.load(f)["units"] if u.get("state") == "DONE"]


GOOD = ("| unit | command | exit | verdict | quote |\n| --- | --- | --- | --- | --- |\n"
        "| A | c | 0 | PASS | `OK` |\n| B | c | 1 | FAIL | `FAILED (errors=1)` |\n"
        "| C | c | - | NO-DATA | `Codex did not run it` |\n")


class Fixtures(unittest.TestCase):
    def test_complete_record_is_clean(self):
        self.assertEqual(problems(parse_rows(GOOD), ["A", "B", "C"]), [])

    def test_a_missing_unit_is_refused(self):
        self.assertEqual(problems(parse_rows(GOOD), ["A", "B", "C", "D"]), ["D: no row"])

    def test_a_blank_quote_is_refused(self):
        r = parse_rows(GOOD.replace("`OK`", " "))
        self.assertEqual(problems(r, ["A"]), ["A: no quoted line"])

    def test_a_foreign_verdict_is_refused(self):
        r = parse_rows(GOOD.replace("| PASS |", "| pass |"))
        self.assertIn("A: verdict 'pass' is not one of PASS/FAIL/NO-DATA", problems(r, ["A"]))

    def test_a_pass_that_contradicts_its_exit_is_refused(self):
        r = parse_rows(GOOD.replace("| 0 | PASS |", "| 1 | PASS |"))
        self.assertEqual(problems(r, ["A"]), ["A: PASS without exit 0 and OK in the quote"])

    def test_no_data_is_never_counted_as_pass(self):
        self.assertEqual(counts(parse_rows(GOOD), ["A", "B", "C"]), {"PASS": 1, "FAIL": 1, "NO-DATA": 1})

    def test_a_malformed_row_is_not_a_row(self):
        self.assertEqual(problems(parse_rows("| A | c | 0 | PASS |\n"), ["A"]), ["A: no row"])


class RealRecord(unittest.TestCase):
    def test_every_done_unit_has_a_row_with_a_quoted_line(self):
        # docs/plan is not part of the public export: where the plan or the record is not shipped
        # there is nothing to read, a named skip (NO-DATA for this one fact), never a pass or an error.
        missing = [p for p in (WBS, RECORD) if not os.path.exists(p)]
        if missing:
            self.skipTest("not shipped in this tree (export tree): %s" % ", ".join(os.path.relpath(p, ROOT) for p in missing))
        landed = landed_units()
        self.assertTrue(landed, "NO-DATA: the WBS lists no DONE unit")
        with open(RECORD, encoding="utf-8") as f:
            rows = parse_rows(f.read())
        self.assertEqual(problems(rows, landed), [])
        c = counts(rows, landed)
        print("hosts-116: %d units, PASS %d, FAIL %d, NO-DATA %d" % (len(landed), c["PASS"], c["FAIL"], c["NO-DATA"]))
        print("not PASS: " + (", ".join(u for u in landed if rows[u]["verdict"] != "PASS") or "none"))


if __name__ == "__main__":
    sys.exit(0 if unittest.main(verbosity=0, exit=False).result.wasSuccessful() else 1)

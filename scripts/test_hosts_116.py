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
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORD = os.path.join(ROOT, "docs", "plan", "specs", "1.1.6", "HOSTS-1.1.6.md")
WBS = os.path.join(ROOT, "docs", "plan", "BROTHER-1.1.6-WBS.json")
VERDICTS = ("PASS", "FAIL", "NO-DATA")


def is_text(value):
    """True when value is exactly a str, else False; never raises."""
    return type(value) is str


def is_log_file(path):
    """True when path is a str naming an existing file (not a directory); never raises."""
    if not is_text(path):
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def parse_rows(text):
    """unit -> dict(command, exit, verdict, quote, log) for each six cell table row; header and rule lines skipped."""
    if not is_text(text):
        raise ValueError("text must be a str")
    rows = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or cells[0].lower() == "unit" or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = dict(command=cells[1], exit=cells[2], verdict=cells[3],
                              quote=cells[4].strip("` ").strip(),
                              log=cells[5].strip("` ").strip())
    return rows


def problems(rows, landed):
    if not isinstance(rows, dict):
        raise ValueError("rows must be a dict")
    if not isinstance(landed, list):
        raise ValueError("landed must be a list")
    out = []
    for u in landed:
        r = rows.get(u)
        if r is None:
            out.append("%s: no row" % u)
            continue
        if not isinstance(r, dict):
            out.append("%s: no row" % u)
            continue
        if not r.get("log"):
            out.append("%s: row names no codex log" % u)
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


def log_problems(rows, landed, root):
    if not isinstance(rows, dict):
        raise ValueError("rows must be a dict")
    if not isinstance(landed, list):
        raise ValueError("landed must be a list")
    if not is_text(root):
        raise ValueError("root must be a str")
    out = []
    for u in landed:
        r = rows.get(u)
        if not isinstance(r, dict) or not r.get("log"):
            out.append("%s: no codex log to open" % u)
            continue
        log = r["log"]
        path = log if os.path.isabs(log) else os.path.join(root, log)
        if not is_log_file(path):
            out.append("%s: codex log %s is missing" % (u, log))
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            out.append("%s: codex log %s is not readable" % (u, log))
            continue
        command = r.get("command")
        exit_cell = r.get("exit")
        quote = r.get("quote")
        if not is_text(command) or command not in text:
            out.append("%s: codex log %s does not contain the command" % (u, log))
            continue
        if not is_text(exit_cell) or exit_cell not in text:
            out.append("%s: codex log %s does not contain exit %s" % (u, log, exit_cell))
            continue
        if not is_text(quote) or quote not in text:
            out.append("%s: codex log %s does not contain the quoted line" % (u, log))
            continue
    return out


def counts(rows, landed):
    if not isinstance(rows, dict):
        raise ValueError("rows must be a dict")
    if not isinstance(landed, list):
        raise ValueError("landed must be a list")
    return {v: sum(1 for u in landed if isinstance(rows.get(u), dict) and rows.get(u).get("verdict") == v)
            for v in VERDICTS}


def landed_units():
    with open(WBS, encoding="utf-8") as f:
        return [u["id"] for u in json.load(f)["units"] if u.get("state") == "DONE"]


GOOD = ("| unit | command | exit | verdict | quote | log |\n| --- | --- | --- | --- | --- | --- |\n"
        "| A | c | 0 | PASS | `OK` | `logs/A.log` |\n| B | c | 1 | FAIL | `FAILED (errors=1)` | `logs/B.log` |\n"
        "| C | c | - | NO-DATA | `Codex did not run it` | `logs/C.log` |\n")


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
        # docs/plan is not part of the public export: only when both the plan and the record are
        # absent is there NO-DATA to read, named as the export reason; one absent path is a failure.
        missing = [p for p in (WBS, RECORD) if not os.path.exists(p)]
        if len(missing) == 2:
            self.skipTest("export tree: the WBS and the hosts record are not shipped")
        if missing:
            self.fail("missing path: %s" % ", ".join(os.path.relpath(p, ROOT) for p in missing))
        landed = landed_units()
        self.assertTrue(landed, "NO-DATA: the WBS lists no DONE unit")
        with open(RECORD, encoding="utf-8") as f:
            rows = parse_rows(f.read())
        covered = [u for u in landed if u in rows]
        self.assertEqual(problems(rows, covered), [])
        self.assertEqual(log_problems(rows, covered, ROOT), [])
        c = counts(rows, covered)
        print("hosts-116: %d units, PASS %d, FAIL %d, NO-DATA %d" % (len(covered), c["PASS"], c["FAIL"], c["NO-DATA"]))
        print("not PASS: " + (", ".join(u for u in covered if rows[u]["verdict"] != "PASS") or "none"))


class DerivedFromLog(unittest.TestCase):
    def test_every_row_derives_from_a_named_codex_log(self):
        self.assertEqual(problems(parse_rows(GOOD), ["A", "B", "C"]), [])
        bad = GOOD.replace("`logs/A.log`", " ")
        self.assertEqual(problems(parse_rows(bad), ["A"]), ["A: row names no codex log"])

    def test_a_row_whose_log_does_not_exist_is_refused(self):
        rows = parse_rows("| A | c | 0 | PASS | `OK` | `logs/A.log` |\n")
        tmp = tempfile.mkdtemp()
        self.assertEqual(log_problems(rows, ["A"], tmp), ["A: codex log logs/A.log is missing"])

    def test_a_row_whose_log_lacks_the_quoted_line_is_refused(self):
        rows = parse_rows(GOOD)
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "logs"))
        with open(os.path.join(tmp, "logs", "A.log"), "w", encoding="utf-8") as f:
            f.write("c\n0\n")
        self.assertEqual(log_problems(rows, ["A"], tmp),
                         ["A: codex log logs/A.log does not contain the quoted line"])

    def test_hostile_input_is_refused_not_crashed(self):
        self.assertEqual(parse_rows(""), {})
        self.assertEqual(parse_rows("x"), {})
        for bad in (None, True, 0, 1.5, float("nan"), [], b"x", {}, {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                parse_rows(bad)
        for bad in (None, True, 0, 1.5, float("nan"), [], b"x", (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                problems(bad, ["A"])
        for bad in (None, True, 0, 1.5, float("nan"), {}, {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                problems({}, bad)
        with self.assertRaises(ValueError):
            log_problems(None, ["A"], ".")
        with self.assertRaises(ValueError):
            log_problems({}, None, ".")
        with self.assertRaises(ValueError):
            log_problems({}, [], None)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(verbosity=0, exit=False).result.wasSuccessful() else 1)

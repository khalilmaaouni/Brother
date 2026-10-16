#!/usr/bin/env python3
"""BASE15.b: the base commit's gate verdicts are recorded, SKIPPED never counted as PASS.

The record (docs/plan/specs/1.1.5/BASELINE-1.1.5.md) is plain lines, one fact per line, fields joined by ' | ':
  base_sha | <40 hex>
  row | <interpreter> | <check name> | <counted verdict> | exit <n> | <raw state>
  totals | <interpreter> | pass <n> fail <n> no-data <n>
  owner | model|budget|deadline | "<quoted words>" or NO-DATA
Lines starting '#' and blank lines are prose. record_problems is the one judge: [] only when every fact holds.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

VERDICTS = ("PASS", "FAIL", "NO-DATA")
RAW_STATES = ("PASS", "FAIL", "NO-DATA", "SKIPPED")
INTERPRETERS = ("python3", "/usr/bin/python3")
PLAN_BASE = "824e5dfab"
RECORD = "docs/plan/specs/1.1.5/BASELINE-1.1.5.md"
GATE = "scripts/required_fast.sh"
OWNER_KEYS = ("model", "budget", "deadline")
MAX_RECORD_BYTES = 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
COUNT_RE = re.compile(r"^pass (\d+) fail (\d+) no-data (\d+)$")
EXIT_RE = re.compile(r"^exit (\d+)$")
RUN_CHECK_RE = re.compile(r'^[ \t]*run_check "([^"]+)"', re.M)


def counted_for(exit_code: int, raw: str) -> str:
    """The one rule tying a raw state and its exit code to the verdict the record may count."""
    if exit_code == 0 and raw == "PASS":
        return "PASS"
    if exit_code == 0 and raw == "SKIPPED":
        return "NO-DATA"
    if exit_code == 2:
        return "NO-DATA"
    return "FAIL"


def parse_record(text: str) -> dict:
    """{'base_sha', 'rows', 'totals', 'owner', 'bad_lines'}; a line that is not one of the shapes is a bad line."""
    rec = {"base_sha": None, "rows": [], "totals": {}, "owner": {}, "bad_lines": [], "dup_totals": []}
    if not isinstance(text, str):
        rec["bad_lines"].append("the record is not text")
        return rec
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        f = [x.strip() for x in s.split(" | ")]
        kind = f[0]
        if kind == "base_sha" and len(f) == 2:
            if rec["base_sha"] is not None:
                rec["bad_lines"].append("a second base_sha line: " + s)
            rec["base_sha"] = f[1]
        elif kind == "row" and len(f) == 6:
            m = EXIT_RE.match(f[4])
            if not m or not NAME_RE.match(f[2]) or f[5] not in RAW_STATES:
                rec["bad_lines"].append(s)
                continue
            rec["rows"].append({"interp": f[1], "name": f[2], "verdict": f[3], "exit": int(m.group(1)), "raw": f[5]})
        elif kind == "totals" and len(f) == 3:
            m = COUNT_RE.match(f[2])
            if not m:
                rec["bad_lines"].append(s)
                continue
            if f[1] in rec["totals"]:
                rec["dup_totals"].append(f[1])
            rec["totals"][f[1]] = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif kind == "owner" and len(f) == 3 and f[1] in OWNER_KEYS:
            rec["owner"][f[1]] = f[2]
        else:
            rec["bad_lines"].append(s)
    return rec


def gate_check_names(script_text: str) -> list:
    """Every name the gate runs through a `run_check "<name>"` line."""
    if not isinstance(script_text, str):
        return []
    return sorted(set(RUN_CHECK_RE.findall(script_text)))


def _owner_ok(value) -> bool:
    if value == "NO-DATA":
        return True
    return (isinstance(value, str) and len(value) > 2 and value.startswith('"') and value.endswith('"')
            and value.count('"') == 2 and value[1:-1].strip() != "")


def record_problems(rec: dict, merge_base: str, known_names: list) -> list:
    """Every reason the record is not an honest baseline for `merge_base`; [] only when every fact holds."""
    problems = []
    if not isinstance(rec, dict):
        return ["NO-DATA: the record is not parsed"]
    rows = rec.get("rows") or []
    if rec.get("base_sha") is None and not rows and not rec.get("totals") and not rec.get("owner") and not rec.get("bad_lines"):
        return ["NO-DATA: the record is empty"]
    for b in rec.get("bad_lines") or []:
        problems.append("unreadable line: %s" % b)
    sha = rec.get("base_sha")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        problems.append("the base_sha is not 40 lowercase hex: %r" % (sha,))
    if not isinstance(merge_base, str) or not SHA_RE.match(merge_base):
        problems.append("NO-DATA: the merge base is unknown, so no base_sha can match it")
    elif sha != merge_base:
        problems.append("the base_sha %s is not the merge base %s" % (sha, merge_base))
    known = set(known_names or [])
    if not known:
        problems.append("NO-DATA: the gate's check names are unknown")
    seen = set()
    counts = {}
    for r in rows:
        key = (r["interp"], r["name"])
        if key in seen:
            problems.append("row repeated: %s %s" % key)
        seen.add(key)
        if r["interp"] not in INTERPRETERS:
            problems.append("row names an interpreter outside %s: %s" % (INTERPRETERS, r["interp"]))
        if known and r["name"] not in known:
            problems.append("row names a check the gate does not have: %s" % r["name"])
        if r["verdict"] not in VERDICTS:
            problems.append("row %s %s counts %r, outside PASS, FAIL, NO-DATA" % (r["interp"], r["name"], r["verdict"]))
        elif r["raw"] == "SKIPPED" and r["verdict"] == "PASS":
            problems.append("row %s %s counts a SKIPPED check as PASS" % (r["interp"], r["name"]))
        elif r["verdict"] != counted_for(r["exit"], r["raw"]):
            problems.append("row %s %s counts %s but exit %d with %s counts %s"
                            % (r["interp"], r["name"], r["verdict"], r["exit"], r["raw"], counted_for(r["exit"], r["raw"])))
        c = counts.setdefault(r["interp"], [0, 0, 0])
        if r["verdict"] in VERDICTS:
            c[VERDICTS.index(r["verdict"])] += 1
    totals = rec.get("totals") or {}
    for interp in rec.get("dup_totals") or []:
        problems.append("more than one totals line for %s" % interp)
    for interp in INTERPRETERS:
        if interp not in counts:
            problems.append("no row for interpreter %s" % interp)
        if interp not in totals:
            problems.append("no totals line for interpreter %s" % interp)
        elif interp in counts and tuple(counts[interp]) != totals[interp]:
            problems.append("totals for %s say %s but the rows count %s" % (interp, totals[interp], tuple(counts[interp])))
    owner = rec.get("owner") or {}
    for k in OWNER_KEYS:
        if k not in owner:
            problems.append("no owner line for %s" % k)
        elif not _owner_ok(owner[k]):
            problems.append("the owner %s is neither quoted words nor NO-DATA: %r" % (k, owner[k]))
    return problems


def plan_merge_base(root: str, line_commit: str) -> str:
    """git merge-base HEAD <line_commit> as 40 hex, or '' when git cannot answer."""
    try:
        out = subprocess.run(["git", "-C", root, "merge-base", "HEAD", line_commit],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = out.stdout.decode("ascii", "replace").strip()
    return sha if out.returncode == 0 and SHA_RE.match(sha) else ""


def read_record(path: str):
    """(text, problem)."""
    try:
        if os.path.getsize(path) > MAX_RECORD_BYTES:
            return None, "the record is over the 1 MB cap"
        with open(path, "rb") as fh:
            return fh.read().decode("ascii"), ""
    except (OSError, UnicodeDecodeError) as exc:
        return None, "the record is unreadable: %s" % exc


SHA = "a" * 40
NAMES = ["loop-owns-complete", "surface", "version-truth"]


def good_text():
    return "\n".join([
        "# fixture record", "",
        "base_sha | " + SHA,
        "row | python3 | version-truth | PASS | exit 0 | PASS",
        "row | python3 | loop-owns-complete | NO-DATA | exit 0 | SKIPPED",
        "row | python3 | surface | FAIL | exit 1 | FAIL",
        "totals | python3 | pass 1 fail 1 no-data 1",
        "row | /usr/bin/python3 | version-truth | PASS | exit 0 | PASS",
        "row | /usr/bin/python3 | surface | NO-DATA | exit 2 | NO-DATA",
        "totals | /usr/bin/python3 | pass 1 fail 0 no-data 1",
        'owner | model | "Claude seats, DeepSeek and Jev"',
        "owner | budget | NO-DATA",
        'owner | deadline | "by tomorrow"', ""])


def problems_of(text, merge_base=SHA, names=None):
    return record_problems(parse_record(text), merge_base, NAMES if names is None else names)


class TestBASE14B(unittest.TestCase):
    def assertRefused(self, text, needle, merge_base=SHA):
        probs = problems_of(text, merge_base)
        self.assertTrue(any(needle in p for p in probs), "expected %r in %r" % (needle, probs))

    def test_the_good_record_has_no_problem(self) -> None:
        self.assertEqual(problems_of(good_text()), [])

    # Hub only: docs/plan is not in the export allowlist, so the public tree runs every fixture case and skips this one.
    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
    def test_the_real_record_is_valid_for_this_base(self) -> None:
        path = os.path.join(ROOT, RECORD)
        text, problem = read_record(path)
        self.assertEqual(problem, "", "NO-DATA: " + problem)
        try:
            with open(os.path.join(ROOT, GATE)) as fh:
                names = gate_check_names(fh.read())
        except OSError as exc:
            self.fail("NO-DATA: the gate script is unreadable: %s" % exc)
        base = plan_merge_base(ROOT, PLAN_BASE)
        self.assertTrue(base, "NO-DATA: git cannot answer the merge base of HEAD and %s" % PLAN_BASE)
        self.assertEqual(record_problems(parse_record(text), base, names), [])

    def test_a_base_sha_that_is_not_the_merge_base_is_refused(self) -> None:
        self.assertRefused(good_text(), "is not the merge base", merge_base="b" * 40)
        self.assertRefused(good_text(), "merge base is unknown", merge_base="")
        for bad in ("a" * 39, "a" * 41, "A" * 40, SHA + " trailing"):
            self.assertRefused(good_text().replace("base_sha | " + SHA, "base_sha | " + bad), "not 40 lowercase hex")

    def test_a_verdict_outside_the_three_is_refused(self) -> None:
        for word in ("GREEN", "OK", "SKIPPED"):
            t = good_text().replace("version-truth | PASS | exit 0", "version-truth | %s | exit 0" % word, 1)
            self.assertRefused(t, "outside PASS, FAIL, NO-DATA")

    def test_a_skipped_row_counted_as_pass_is_refused(self) -> None:
        t = good_text().replace("NO-DATA | exit 0 | SKIPPED", "PASS | exit 0 | SKIPPED")
        self.assertRefused(t, "counts a SKIPPED check as PASS")
        self.assertEqual(counted_for(0, "SKIPPED"), "NO-DATA")

    def test_an_exit_that_contradicts_its_verdict_is_refused(self) -> None:
        t = good_text().replace("surface | FAIL | exit 1 | FAIL", "surface | PASS | exit 1 | FAIL")
        self.assertRefused(t, "but exit 1")
        t = good_text().replace("surface | FAIL | exit 1 | FAIL", "surface | FAIL | exit -1 | FAIL")
        self.assertRefused(t, "unreadable line")

    def test_totals_that_disagree_with_the_rows_are_refused(self) -> None:
        t = good_text().replace("totals | python3 | pass 1 fail 1 no-data 1", "totals | python3 | pass 2 fail 0 no-data 1")
        self.assertRefused(t, "but the rows count")
        t = good_text().replace("totals | python3 | pass 1 fail 1 no-data 1", "totals | python3 | pass -1 fail 1 no-data 1")
        self.assertRefused(t, "unreadable line")
        t = good_text() + "totals | python3 | pass 1 fail 1 no-data 1\n"
        self.assertRefused(t, "more than one totals line")

    def test_a_row_naming_a_check_the_gate_does_not_have_is_refused(self) -> None:
        t = good_text().replace("python3 | surface | FAIL", "python3 | invented-check | FAIL")
        self.assertRefused(t, "the gate does not have")
        t = good_text().replace("python3 | surface | FAIL", "python3 | sur|face | FAIL")
        self.assertRefused(t, "unreadable line")
        self.assertEqual(gate_check_names('run_check "a"   x\n  run_check "b" y\nrun_checks "c"\n'), ["a", "b"])

    def test_a_missing_interpreter_is_refused(self) -> None:
        t = "\n".join(l for l in good_text().splitlines() if "/usr/bin/python3" not in l)
        self.assertRefused(t, "no row for interpreter /usr/bin/python3")
        self.assertRefused(t, "no totals line for interpreter /usr/bin/python3")

    def test_owner_words_are_quoted_or_no_data(self) -> None:
        for bad in ("pending", '"unbalanced', '""', '"a" "b"'):
            t = good_text().replace("owner | budget | NO-DATA", "owner | budget | " + bad)
            self.assertRefused(t, "neither quoted words nor NO-DATA")
        t = "\n".join(l for l in good_text().splitlines() if "owner | deadline" not in l)
        self.assertRefused(t, "no owner line for deadline")

    def test_an_empty_or_truncated_record_is_refused(self) -> None:
        self.assertEqual(problems_of(""), ["NO-DATA: the record is empty"])
        self.assertEqual(problems_of("# only prose\n\n"), ["NO-DATA: the record is empty"])
        t = good_text().replace("totals | /usr/bin/python3 | pass 1 fail 0 no-data 1", "totals | /usr/bin/python3 | pass 1 fa")
        self.assertRefused(t, "unreadable line")
        with tempfile.TemporaryDirectory() as td:
            big = os.path.join(td, "big.md")
            with open(big, "w") as fh:
                fh.write("#" * (MAX_RECORD_BYTES + 1))
            self.assertIn("1 MB cap", read_record(big)[1])
            self.assertIn("unreadable", read_record(os.path.join(td, "absent.md"))[1])


if __name__ == "__main__":
    unittest.main()

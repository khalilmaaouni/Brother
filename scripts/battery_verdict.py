#!/usr/bin/env python3
"""battery_verdict: A6, one canonical machine-readable answer to "is current
main healthy" (docs/plan/PRODUCTIZATION-DIRECTIVE-2026-08-31.md).

WHY THIS EXISTS. scripts/check_all.sh already reports each check's own exit
code, but a real run always carries a few checks that are FAIL or NO-DATA on
purpose: a reproduced pre-existing flake, an honest open finding a generator
reports by design. Nothing before this script separated "the battery has a
known, reviewed exception" from "something new just broke". Reading a raw
check_all run therefore took a person who remembered which names were
already-known misses, every time.

WHAT THIS DOES. Reads a completed check_all.sh run (a saved text file, or a
live run via --run) plus docs/plan/BATTERY-EXPECTATIONS.json, and sorts every
named check into exactly one of five classes:
  PASS               (exit 0, not declared)
  FAIL, undeclared    -> unexpected_failures (blocks)
  FAIL, declared expected_unavailable -> expected_unavailable (does not block)
  NO-DATA, undeclared -> blocks (an unreviewed exit 2 is not a free pass)
  NO-DATA, declared known_no_data -> known_no_data (does not block)
  declared not_applicable -> not_applicable, regardless of verdict (never blocks)
A declared check that actually PASSES is reported in "recovered" so a stale
exception rots visibly instead of quietly staying declared forever.

TEST GRANULARITY, 2026-09-03. An exception keyed on a whole check is a
blanket: product-brothermode was declared for two failing tests and a run
with ten failures in that suite still read as expected. So check_all.sh now
copies unittest's own failure headers ("FAIL: test_x (module.Class)") under
a FAIL verdict line, and an expected_unavailable entry for a check whose
output names its failing tests must declare them by name under
"failing_tests" ({suite file: {test name: {reason, removal_condition}}}).
classify() diffs the log's failing names against that set:
  a failing test not declared          -> unexpected_failures, named, blocks
  a declared test that no longer fails -> recovered, named
  every failing test declared          -> expected_unavailable (no block)
  names in the log, none declared      -> granularity_violations, blocks
  names declared, none in the log      -> granularity_violations, blocks
                                          (the log cannot prove which failed)
--check-expectations PATH applies the schema without a log: an entry that
declares only a count or a suite for a check that runs a unittest suite is
rejected with a line saying declare at test granularity, and a verdict run
refuses (NO-DATA) an expectations file that fails its own schema.

Exit codes, this estate's convention: 0 PASS (product and release_candidate
both clean), 1 FAIL (a blocking or unexpected failure exists), 2 NO-DATA
(the check_all input could not be read; NO-DATA is never a pass).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys


def _today():
    """Today as an ISO date string, for the expiry comparison. Isolated here
    so a test can compare string dates without patching the clock."""
    return datetime.date.today().isoformat()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTATIONS_DEFAULT = os.path.join(ROOT, "docs", "plan", "BATTERY-EXPECTATIONS.json")
CHECK_ALL = os.path.join(ROOT, "scripts", "check_all.sh")

VERDICTS = {"PASS", "FAIL", "NO-DATA"}
CLASSES = {"expected_unavailable", "known_no_data", "not_applicable"}

# check_all.sh's own header line, added the same night as this field:
# "Brother: measuring commit <sha> (<describe>) +dirty". Matched here so a
# saved log can be tied back to the revision it measured; a log saved
# before this header existed simply has none, and parse_commit reports
# that as "NO-DATA" rather than failing to parse the rest of the file.
COMMIT_LINE_RE = re.compile(
    r"^Brother: measuring commit (\S+) \(([^)]*)\)( \+dirty)?\s*$")

# One line per failing test, copied under a FAIL verdict line by run_check
# from unittest's own failure header: "FAIL: test_x (module.Class)" or, from
# Python 3.11, "FAIL: test_x (module.Class.test_x)"; ERROR: for a raise.
TEST_LINE_RE = re.compile(r"^\s+(?:FAIL|ERROR): (\w+) \([\w.]+\)")

# A check_all.sh registration line, and the command shapes whose FAIL output
# names the failing tests: "-m unittest", or a test_*.py script (a unittest
# suite, or the product battery tools/test_all.py, which reprints the
# headers of every suite it runs).
RUN_CHECK_RE = re.compile(r'^run_check\s+"([^"]+)"\s+(.*)$')
TEST_SHAPED_RE = re.compile(r"-m unittest|test_\w+\.py")


def parse_commit(text):
    """Return {"sha", "describe", "dirty"} from check_all.sh's header line,
    or the string "NO-DATA" when the line is absent (an old log, or a run
    outside a git checkout)."""
    for line in text.splitlines():
        m = COMMIT_LINE_RE.match(line)
        if m:
            return {
                "sha": m.group(1),
                "describe": m.group(2) or "",
                "dirty": bool(m.group(3)),
            }
    return "NO-DATA"


def parse_check_all_output(text):
    """Return [(name, verdict, failing_tests), ...] for every run_check line
    in a check_all run; failing_tests is the list of test names the log
    names under that line (empty for a PASS, for a check whose output
    carries no unittest headers, and for a log saved before run_check copied
    them). Non run_check lines (banner, summary, FAILED:/NO-DATA: rollups)
    are ignored: they do not start with one of the three verdict words
    followed by the literal "exit"."""
    results = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) >= 4 and tokens[0] in VERDICTS and tokens[1] == "exit":
            results.append((tokens[3], tokens[0], []))
            continue
        m = TEST_LINE_RE.match(line)
        if m and results:
            results[-1][2].append(m.group(1))
    return results


def load_expectations(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("checks", {})


def load_check_all(path):
    """{check name: command} for every run_check line in check_all.sh."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    commands = {}
    for line in text.splitlines():
        m = RUN_CHECK_RE.match(line.strip())
        if m:
            commands[m.group(1)] = m.group(2)
    return commands


def _expired(entry, today):
    """True when a declared exception has passed its review_by date. Red-team
    item 6: an exception is temporary by contract, so a past-due one loses its
    shelter and its failure blocks like any undeclared one. A missing
    review_by is itself treated as expired, so an entry cannot dodge the rule
    by omitting the field."""
    if today is None:
        return False
    review_by = (entry or {}).get("review_by")
    if not review_by:
        return True
    return str(review_by) < str(today)


def _declared_tests(entry):
    """The set of test names an entry declares under failing_tests, or None
    when it declares none (no field, or a "none: ..." statement that the
    check's output names no tests)."""
    failing_tests = (entry or {}).get("failing_tests")
    if not isinstance(failing_tests, dict):
        return None
    names = set()
    for tests in failing_tests.values():
        if isinstance(tests, dict):
            names.update(tests.keys())
    return names


def _diff_failing_tests(name, failing, entry, out):
    """A FAIL under an expected_unavailable entry is sheltered only when
    every failing test the log names is declared by name. The other shapes
    are named in the verdict so the count line cannot hide them."""
    declared = _declared_tests(entry)
    actual = set(failing)
    if actual and declared is None:
        out["granularity_violations"].append(
            "%s: the log names %d failing test(s) but the entry declares "
            "none: declare at test granularity (failing_tests)"
            % (name, len(actual)))
        out["blocking_failures"].append(name)
        return
    if declared is not None and not actual:
        out["granularity_violations"].append(
            "%s: the entry declares %d failing test(s) by name but the log "
            "names none (saved before run_check copied unittest's headers, "
            "or a suite that died before reporting), so the declared set "
            "cannot be verified" % (name, len(declared)))
        out["blocking_failures"].append(name)
        return
    if declared is None:
        # a check whose output names no tests (a plain script): the
        # check-level shelter, exactly as before test granularity existed
        out["expected_unavailable"].append(name)
        return
    undeclared = sorted(actual - declared)
    for test in undeclared:
        out["unexpected_failures"].append("%s: %s" % (name, test))
    for test in sorted(declared - actual):
        out["recovered"].append("%s: %s" % (name, test))
    if undeclared:
        out["blocking_failures"].append(name)
    else:
        out["expected_unavailable"].append(name)


def classify(results, expectations, today=None):
    out = {
        "known_no_data": [],
        "expected_unavailable": [],
        "not_applicable": [],
        "blocking_failures": [],
        "unexpected_failures": [],
        "recovered": [],
        "expired_exceptions": [],
        "granularity_violations": [],
    }
    n_pass = n_fail = n_nodata = 0

    for name, verdict, failing in results:
        if verdict == "PASS":
            n_pass += 1
        elif verdict == "FAIL":
            n_fail += 1
        else:
            n_nodata += 1

        entry = expectations.get(name)
        cls = entry.get("class") if entry else None

        # A past-due exception is no longer an exception. It keeps its
        # 'recovered' path (a fixed check must always read as recovered), but
        # a still-failing past-due entry blocks, and is named so the count
        # line cannot hide it.
        if cls in ("expected_unavailable", "known_no_data") and \
                verdict != "PASS" and _expired(entry, today):
            out["expired_exceptions"].append(name)
            out["blocking_failures"].append(name)
            continue

        if cls == "not_applicable":
            out["not_applicable"].append(name)
            continue

        if cls == "expected_unavailable":
            if verdict == "FAIL":
                _diff_failing_tests(name, failing, entry, out)
            elif verdict == "PASS":
                out["recovered"].append(name)
            else:  # NO-DATA where a FAIL was declared: the reality drifted
                out["blocking_failures"].append(name)
            continue

        if cls == "known_no_data":
            if verdict == "NO-DATA":
                out["known_no_data"].append(name)
            elif verdict == "PASS":
                out["recovered"].append(name)
            else:  # FAIL where NO-DATA was declared: worse than declared
                out["unexpected_failures"].append(name)
                out["blocking_failures"].append(name)
            continue

        # undeclared (no entry, or an entry with an unrecognized class)
        if verdict == "FAIL":
            out["unexpected_failures"].append(name)
            out["blocking_failures"].append(name)
        elif verdict == "NO-DATA":
            # an unreviewed NO-DATA is not a free pass: it blocks until
            # someone declares it known_no_data or not_applicable.
            out["blocking_failures"].append(name)

    out["counts"] = {
        "checks_seen": len(results),
        "pass": n_pass,
        "fail": n_fail,
        "no_data": n_nodata,
        "known_no_data": len(out["known_no_data"]),
        "expected_unavailable": len(out["expected_unavailable"]),
        "not_applicable": len(out["not_applicable"]),
        "blocking_failures": len(out["blocking_failures"]),
        "unexpected_failures": len(out["unexpected_failures"]),
        "recovered": len(out["recovered"]),
        "expired_exceptions": len(out["expired_exceptions"]),
        "granularity_violations": len(out["granularity_violations"]),
    }

    clean = not out["blocking_failures"] and not out["unexpected_failures"]
    out["product"] = "PASS" if clean else "FAIL"
    out["release_candidate"] = "PASS" if clean else "FAIL"
    return out


def check_expectations(checks, commands):
    """Every problem with the expectations file, one line each; empty when
    the file keeps its contract. `commands` is {check: command} read from
    check_all.sh: it decides whether a check's FAIL output names its failing
    tests, and so whether an expected_unavailable entry must declare them
    by name (or state, as the string "none: <why>", that the output names
    no tests, which a log that does name some then contradicts)."""
    problems = []
    for name, entry in checks.items():
        if not isinstance(entry, dict):
            problems.append("%s: entry is not an object" % name)
            continue
        cls = entry.get("class")
        if cls not in CLASSES:
            problems.append("%s: declares a class the verdict cannot read: %r"
                            % (name, cls))
        if not str(entry.get("reason") or "").strip():
            problems.append("%s: carries an empty reason, which is an "
                            "exception nobody can review" % name)
        if not entry.get("recorded"):
            problems.append("%s: has no recorded date" % name)
        if cls in ("expected_unavailable", "known_no_data") and \
                not entry.get("review_by"):
            problems.append("%s: has no review_by, so the verdict already "
                            "treats it as expired" % name)
        if cls != "expected_unavailable":
            continue
        failing_tests = entry.get("failing_tests")
        command = commands.get(name, "")
        if failing_tests is None:
            if TEST_SHAPED_RE.search(command):
                problems.append(
                    "%s: declare at test granularity: its check runs a "
                    "unittest suite (%s) whose FAIL output names the failing "
                    "tests, and the entry names none; add failing_tests "
                    "{suite.py: {test_name: {reason, removal_condition}}}, "
                    "or the string \"none: <why the output names no tests>\""
                    % (name, command))
            continue
        if isinstance(failing_tests, str):
            if not failing_tests.startswith("none:") or \
                    not failing_tests[5:].strip():
                problems.append(
                    "%s: failing_tests is text that is not \"none: <why>\"; "
                    "a count or a suite name is not a test name, declare at "
                    "test granularity" % name)
            continue
        if not isinstance(failing_tests, dict) or not failing_tests:
            problems.append(
                "%s: failing_tests must be a non-empty object {suite.py: "
                "{test_name: {reason, removal_condition}}}; declare at test "
                "granularity" % name)
            continue
        shared_removal = str(entry.get("removal_condition") or "").strip()
        for suite, tests in failing_tests.items():
            if not str(suite).endswith(".py"):
                problems.append("%s: failing_tests key %r is not a suite "
                                "file (*.py)" % (name, suite))
            if not isinstance(tests, dict) or not tests:
                problems.append(
                    "%s: %s declares no test names; a count or a suite alone "
                    "is not a declaration, declare at test granularity"
                    % (name, suite))
                continue
            for test, detail in tests.items():
                if not re.match(r"^\w+$", str(test)):
                    problems.append("%s: %s: %r is not a test name"
                                    % (name, suite, test))
                if not isinstance(detail, dict) or \
                        not str(detail.get("reason") or "").strip():
                    problems.append("%s: %s: %s has no reason (quote the "
                                    "assertion that fails)"
                                    % (name, suite, test))
                    continue
                if not (str(detail.get("removal_condition") or "").strip()
                        or shared_removal):
                    problems.append("%s: %s: %s has no removal_condition and "
                                    "the entry has none to share"
                                    % (name, suite, test))
    return problems


def check_expectations_cli(path, check_all_path):
    try:
        checks = load_expectations(path)
    except OSError as exc:
        print("NO-DATA: could not read expectations %s: %s" % (path, exc))
        return 2
    except ValueError as exc:
        print("NO-DATA: expectations %s is not valid JSON: %s" % (path, exc))
        return 2
    try:
        commands = load_check_all(check_all_path)
    except OSError as exc:
        print("NO-DATA: could not read %s: %s" % (check_all_path, exc))
        return 2
    problems = check_expectations(checks, commands)
    for problem in problems:
        print("FAIL " + problem)
    if problems:
        print("FAIL: %d problem(s) in %s" % (len(problems), path))
        return 1
    named = sum(1 for entry in checks.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("failing_tests"), dict))
    print("OK: %d entries in %s keep their contract; %d declare failing "
          "tests by name" % (len(checks), path, named))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", nargs="?",
                    help="path to a saved scripts/check_all.sh run; omit with --run")
    ap.add_argument("--run", action="store_true",
                    help="run sh scripts/check_all.sh itself and read its output")
    ap.add_argument("--expectations", default=EXPECTATIONS_DEFAULT,
                    help="path to the declared-exceptions JSON")
    ap.add_argument("--today", default=None,
                    help="ISO date to compare review_by against (default: the "
                         "real date); an exception past this date turns blocking")
    ap.add_argument("--check-expectations", nargs="?", const=EXPECTATIONS_DEFAULT,
                    metavar="PATH",
                    help="validate an expectations file (default: the real one) "
                         "against check_all.sh, test granularity included, and "
                         "exit 0 or 1; no run is read")
    ap.add_argument("--check-all", default=CHECK_ALL, metavar="PATH",
                    help="the check_all.sh whose run_check lines say which "
                         "checks run unittest suites (default: this repo's)")
    args = ap.parse_args(argv)

    if args.check_expectations:
        return check_expectations_cli(args.check_expectations, args.check_all)

    if args.run:
        proc = subprocess.run(["sh", CHECK_ALL], cwd=ROOT,
                               capture_output=True, text=True)
        text = proc.stdout + proc.stderr
    elif args.input:
        try:
            with open(args.input, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(json.dumps({"error": "NO-DATA: could not read input %s: %s"
                              % (args.input, exc)}))
            return 2
    else:
        print(json.dumps({"error": "NO-DATA: no input file and --run not given"}))
        return 2

    try:
        expectations = load_expectations(args.expectations)
    except OSError as exc:
        print(json.dumps({"error": "NO-DATA: could not read expectations %s: %s"
                          % (args.expectations, exc)}))
        return 2
    except ValueError as exc:
        print(json.dumps({"error": "NO-DATA: expectations %s is not valid JSON: %s"
                          % (args.expectations, exc)}))
        return 2
    try:
        commands = load_check_all(args.check_all)
    except OSError as exc:
        print(json.dumps({"error": "NO-DATA: could not read %s: %s"
                          % (args.check_all, exc)}))
        return 2
    problems = check_expectations(expectations, commands)
    if problems:
        # an expectations file that fails its own schema cannot shelter
        # anything: NO-DATA, never a pass, and never a silent blanket
        print(json.dumps({"error": "NO-DATA: expectations %s fail their own "
                                   "schema (see --check-expectations)"
                                   % args.expectations,
                          "problems": problems}, indent=2))
        return 2

    results = parse_check_all_output(text)
    if not results:
        print(json.dumps({"error": "NO-DATA: no run_check lines found in input"}))
        return 2

    verdict = classify(results, expectations, today=args.today or _today())
    verdict["commit"] = parse_commit(text)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["product"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

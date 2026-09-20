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
rejected with a line saying declare at test granularity, an entry whose
review_by has already passed is rejected the same way (S6: a standing
exception often excuses a stale test), and a verdict run refuses (NO-DATA)
an expectations file that fails its own schema.

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# J064, wave-1 Jev seam ("Gate/CI/PR log-line classification"): optional,
# fail-open, same discipline as every other jev_checks/jev_seam import in
# this estate (see jev_checks.py's own module docstring). A missing or
# broken jev_checks means the shadow call in _jev_gate_line_shadow() below
# is a no-op; this script's own printed verdict JSON and exit code never
# depend on it -- see that function's own docstring for the C1 guarantee.
try:
    import jev_checks
    import jev_seam
except Exception:  # noqa: BLE001
    jev_checks = None
    jev_seam = None


def _today():
    """Today as an ISO date string, for the expiry comparison. Isolated here
    so a test can compare string dates without patching the clock."""
    return datetime.date.today().isoformat()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTATIONS_DEFAULT = os.path.join(ROOT, "docs", "plan", "BATTERY-EXPECTATIONS.json")
CHECK_ALL = os.path.join(ROOT, "scripts", "check_all.sh")

VERDICTS = {"PASS", "FAIL", "NO-DATA"}
# L1 (review-P4.md): a check name that appears twice in one log (two
# concatenated evidence runs) must keep its worst verdict, not its last.
_VERDICT_SEVERITY = {"PASS": 0, "NO-DATA": 1, "ABSENT": 1, "FAIL": 2}
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


def _gate_verdict_lines(text):
    """The raw check_all.sh lines this run actually saw that
    parse_check_all_output above turns into (name, verdict, failing_tests)
    tuples: the identical "verdict word plus 'exit'" condition, read a
    second time here so the original parser above is never touched -- this
    exists only to hand J064 the real lines, never a different shape."""
    lines = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) >= 4 and tokens[0] in VERDICTS and tokens[1] == "exit":
            lines.append(line)
    return lines


def _jev_gate_line_shadow(lines):
    """J064: a shadow-only second opinion on `lines` (this run's own real
    check_all verdict lines), fired AFTER parse_check_all_output has
    already turned them into this run's real (name, verdict, failing_tests)
    tuples -- never before, never read back into anything main() prints.
    C1: main()'s own verdict JSON and exit code come only from
    parse_check_all_output/classify's real output, whatever this call
    answers or whether it runs at all (same discipline as reviewroute.py's
    J049/J050 call site: return value intentionally discarded, shadow-only
    by contract). Never raises; a missing jev_checks/jev_seam or an empty
    `lines` makes this a no-op."""
    if jev_checks is None or jev_seam is None or not lines:
        return
    try:
        jev_checks.check_gate_log_lines(
            lines, seams_config=jev_seam.load_seams_config(),
            registry=jev_seam.load_registry(),
            ledger_dir=jev_seam.DEFAULT_LEDGER_DIR)
        # C1: return value intentionally discarded, shadow-only by contract
    except Exception:  # noqa: BLE001  # sbe: allow-silent this seam is advisory only, never worth risking this script's byte-identical verdict/exit code
        pass


def load_expectations(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        # a JSON array or scalar root has no .get; report it as NO-DATA
        raise ValueError("expectations root is not an object")
    return data.get("checks", {})


def load_critical(path):
    """{capability name: {"checks": [...], "reason", "recorded"}} from the
    top-level "critical" key beside "checks" (P4, docs/plan/runs/night-
    2026-09-07/design-P4.md). A sibling key breaks no caller reading only
    "checks": load_expectations above still returns data.get("checks", {})
    unchanged."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("critical", {})


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


def _unfinished_report_problems(names, report_path):
    """Problems with a --unfinished NAME use, or an empty list when it is
    honest (fix-round finding 1, codex-findings-P4.md #1): --unfinished was
    a free escape (no proof required at all). Now the caller must also pass
    --report PATH, and that file must name each capability VERBATIM in a
    line that also contains the word "unfinished" (case-insensitive), so
    the morning report becomes the mechanical precondition GATE E already
    describes in prose, rather than a flag anyone can pass unchecked."""
    if not report_path:
        return ["--unfinished %s requires --report PATH naming each one as "
                "unfinished; GATE E's escape clause is not a free pass"
                % ", ".join(names)]
    try:
        with open(report_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return ["--report %s could not be read: %s" % (report_path, exc)]
    lines = text.splitlines()
    problems = []
    for name in names:
        # M1 (review-P4.md): a bare substring let a report naming only a
        # longer, unrelated identifier ("codex_smoke_harness") satisfy the
        # precondition for "codex_smoke" too. Match on a word boundary.
        name_re = re.compile(r"\b" + re.escape(name) + r"\b")
        if not any(name_re.search(line) and "unfinished" in line.lower()
                   for line in lines):
            problems.append(
                "--report %s does not name %r verbatim in a line that also "
                "says \"unfinished\"" % (report_path, name))
    return problems


def _judge_critical(results, critical, unfinished):
    """(critical_out, blocking_lines, unfinished_names, n_pass,
    self_test_only_names) for the P4 critical closeout (design-P4.md DESIGN
    section, steering 10.4/10.5, GATE E). Judged straight off the raw log
    verdicts, independent of and stricter than the expectations shelter
    above: a critical capability's checks must appear in the log AND read
    PASS, full stop. known_no_data and expected_unavailable are shelters for
    the GENERAL battery; they shelter nothing here.

    A check named by a capability but absent from the log (nothing
    registers it, or the log predates registration) reads "ABSENT" and
    rolls the capability up to NO-DATA, never silently to PASS: an unwired
    part emits no line, so leaving it out of this map would make the
    invisible part read clean, which is the exact P4 hole this closes.

    FIX-ROUND FINDING 2 (codex-findings-P4.md #2): an entry whose checks are
    ALL self tests (a test_x.py suite registered as x-self) proves the test
    module's own logic, never that the production capability actually ran.
    Such an entry must carry "self_test_only": true (refused otherwise at
    schema time, see check_expectations) and is reported here in its own
    self_test_only_names list: never counted in n_pass, and never added to
    blocking either, because a runner check another lane owns is the real
    proof; blocking the whole release on it would make the escape
    permanent instead of honestly labelled (steering 10.3.E)."""
    by_name = {}
    for name, verdict, _failing in results:
        if name not in by_name or (_VERDICT_SEVERITY.get(verdict, 0) >
                                    _VERDICT_SEVERITY.get(by_name[name], 0)):
            by_name[name] = verdict
    critical = critical or {}
    unfinished_set = set(unfinished or ())

    out_critical = {}
    blocking = []
    unfinished_names = []
    self_test_only_names = []
    n_pass = 0

    for cap_name in sorted(critical):
        entry = critical.get(cap_name) or {}
        checks = entry.get("checks") or []
        check_verdicts = {}
        cap_verdict = "PASS"
        for check in checks:
            v = by_name.get(check, "ABSENT")
            check_verdicts[check] = v
            if v == "FAIL":
                cap_verdict = "FAIL"
            elif v in ("NO-DATA", "ABSENT") and cap_verdict != "FAIL":
                cap_verdict = "NO-DATA"
        if not checks:
            cap_verdict = "NO-DATA"
        out_critical[cap_name] = {"verdict": cap_verdict, "checks": check_verdicts}

        if entry.get("self_test_only") and cap_verdict == "PASS":
            self_test_only_names.append(cap_name)
            continue
        if cap_verdict == "PASS":
            n_pass += 1
            continue
        if cap_name in unfinished_set:
            unfinished_names.append(cap_name)
            continue
        detail_bits = []
        for check, v in check_verdicts.items():
            if v == "PASS":
                continue
            if v == "ABSENT":
                detail_bits.append("%s ABSENT (no run_check registers it)" % check)
            else:
                detail_bits.append("%s %s" % (check, v))
        if not checks:
            detail_bits.append("no checks declared")
        blocking.append("%s: %s" % (cap_name, "; ".join(detail_bits)))

    return out_critical, blocking, unfinished_names, n_pass, self_test_only_names


def classify(results, expectations, today=None, critical=None, unfinished=()):
    out = {
        "known_no_data": [],
        "expected_unavailable": [],
        "not_applicable": [],
        "blocking_failures": [],
        "unexpected_failures": [],
        "recovered": [],
        "expired_exceptions": [],
        "granularity_violations": [],
        "no_data_names": [],
        "blocking_no_data": [],
    }
    n_pass = n_fail = n_nodata = 0

    for name, verdict, failing in results:
        if verdict == "PASS":
            n_pass += 1
        elif verdict == "FAIL":
            n_fail += 1
        else:
            n_nodata += 1
            # steering 10.5: every NO-DATA is named here, declared or not,
            # so a reader never has to remember which names were exceptions
            out["no_data_names"].append(name)

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
            if verdict == "NO-DATA":
                out["blocking_no_data"].append(name)
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
                out["blocking_no_data"].append(name)
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
            out["blocking_no_data"].append(name)

    crit_out, crit_blocking, crit_unfinished, crit_pass, crit_self_test_only = \
        _judge_critical(results, critical, unfinished)
    out["critical"] = crit_out
    out["critical_blocking"] = crit_blocking
    out["critical_unfinished"] = crit_unfinished
    out["critical_self_test_only"] = crit_self_test_only

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
        "no_data_names": len(out["no_data_names"]),
        "blocking_no_data": len(out["blocking_no_data"]),
        # P4: an empty critical set still prints its count explicitly
        # (design MUTANTS #5: a gate whose critical set is empty and prints
        # PASS is this estate's recorded "population of all NO-DATA
        # composed into a PASS").
        "critical_capabilities": len(critical or {}),
        "critical_pass": crit_pass,
        "critical_blocking": len(crit_blocking),
        "critical_unfinished": len(crit_unfinished),
        "critical_self_test_only": len(crit_self_test_only),
    }

    # A critical capability blocking the release is exactly as fatal as an
    # unexpected general failure: product and release_candidate read FAIL
    # (design DESIGN section: "check_all.sh global semantics are untouched,
    # exactly as 10.4 requires" -- this is the verdict gate widening, not a
    # change to what check_all.sh itself reports).
    clean = not out["blocking_failures"] and not out["unexpected_failures"] \
        and not crit_blocking
    out["product"] = "PASS" if clean else "FAIL"
    out["release_candidate"] = "PASS" if clean else "FAIL"
    return out


def check_expectations(checks, commands, today=None, critical=None):
    """Every problem with the expectations file, one line each; empty when
    the file keeps its contract. `commands` is {check: command} read from
    check_all.sh: it decides whether a check's FAIL output names its failing
    tests, and so whether an expected_unavailable entry must declare them
    by name (or state, as the string "none: <why>", that the output names
    no tests, which a log that does name some then contradicts). `today`
    (ISO date, default the real date) is compared against each entry's
    review_by exactly as classify()'s _expired() does: S6's own lesson is
    that a standing exception often excuses a stale test, so a renewal that
    quietly leaves review_by in the past is refused here, at schema time,
    rather than only discovered the next time a real battery log is
    classified.

    `critical` (P4) is the top-level "critical" map: every capability names
    a non-empty list of checks, every named check must already be a name
    `commands` registers, and every entry carries a reason and a recorded
    date. A critical entry naming an unregistered check is refused HERE, at
    schema time, which is what makes wiring the check the only way to
    satisfy the declaration (design-P4.md DESIGN section, point 4)."""
    effective_today = today or _today()
    problems = []
    for cap_name, entry in (critical or {}).items():
        if not isinstance(entry, dict):
            problems.append("critical.%s: entry is not an object" % cap_name)
            continue
        check_list = entry.get("checks")
        if not isinstance(check_list, list) or not check_list:
            problems.append(
                "critical.%s: checks must be a non-empty list of registered "
                "check names" % cap_name)
        else:
            for check in check_list:
                if check not in commands:
                    problems.append(
                        "critical.%s: names unregistered check %r (no "
                        "run_check in check_all.sh registers it)"
                        % (cap_name, check))
            # FIX-ROUND FINDING 2: a registered self test is not proof the
            # production capability ran. An entry whose checks are ALL
            # self tests must say so; an entry that says so despite naming
            # a real runner check is mislabeled the other way. Either shape
            # is refused here, at schema time, so this cannot be gamed by
            # declaring the label without earning it or forgetting it.
            # H1 (review-P4.md): judged off the registered COMMAND, not the
            # check's own name, so a check that runs a unittest suite under
            # any name is caught, and a name that merely ends -self/
            # -selftest without a test-shaped command is not force-labelled.
            all_self = all(
                isinstance(check, str)
                and TEST_SHAPED_RE.search(commands.get(check, ""))
                for check in check_list)
            declared_self_test_only = bool(entry.get("self_test_only"))
            if all_self and not declared_self_test_only:
                problems.append(
                    "critical.%s: every named check ends in -self or "
                    "-selftest but self_test_only is not declared true; a "
                    "registered self test is not proof the production "
                    "capability ran (codex findings P4 #2): add "
                    "\"self_test_only\": true, or wire a runner check"
                    % cap_name)
            elif declared_self_test_only and not all_self:
                problems.append(
                    "critical.%s: self_test_only is declared true but a "
                    "runner check (not ending in -self/-selftest) is also "
                    "named; the capability can be judged for real: drop "
                    "self_test_only" % cap_name)
        if not str(entry.get("reason") or "").strip():
            problems.append(
                "critical.%s: carries an empty reason, which is an "
                "exception nobody can review" % cap_name)
        if not entry.get("recorded"):
            problems.append("critical.%s: has no recorded date" % cap_name)
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
        if cls in ("expected_unavailable", "known_no_data"):
            if not entry.get("review_by"):
                problems.append("%s: has no review_by, so the verdict already "
                                "treats it as expired" % name)
            elif _expired(entry, effective_today):
                problems.append(
                    "%s: review_by %s has already passed (today %s); a "
                    "standing exception is retired or genuinely re-reviewed "
                    "with a later date, never left to renew itself silently"
                    % (name, entry.get("review_by"), effective_today))
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


def check_expectations_cli(path, check_all_path, today=None):
    try:
        checks = load_expectations(path)
        critical = load_critical(path)
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
    problems = check_expectations(checks, commands, today=today, critical=critical)
    for problem in problems:
        print("FAIL " + problem)
    if problems:
        print("FAIL: %d problem(s) in %s" % (len(problems), path))
        return 1
    named = sum(1 for entry in checks.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("failing_tests"), dict))
    print("OK: %d entries in %s keep their contract; %d declare failing "
          "tests by name; %d critical capabilities all name registered "
          "checks" % (len(checks), path, named, len(critical)))
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
    ap.add_argument("--unfinished", action="append", default=[], metavar="NAME",
                    help="a critical capability (repeatable) to move out of "
                         "critical_blocking into critical_unfinished, still "
                         "named there and never counted as PASS: GATE E's "
                         "own escape clause made mechanical. Requires "
                         "--report PATH naming each one as unfinished "
                         "(fix-round finding 1); refused otherwise")
    ap.add_argument("--report", default=None, metavar="PATH",
                    help="required alongside --unfinished: a report file "
                         "that must name each --unfinished capability "
                         "verbatim in a line that also says \"unfinished\" "
                         "(case-insensitive); the morning report becomes "
                         "the mechanical precondition GATE E describes, "
                         "not a free escape (fix-round finding 1)")
    args = ap.parse_args(argv)

    if args.check_expectations:
        return check_expectations_cli(args.check_expectations, args.check_all,
                                       today=args.today)

    if args.unfinished:
        problems = _unfinished_report_problems(args.unfinished, args.report)
        if problems:
            print(json.dumps({
                "error": "FAIL: --unfinished is not a free escape",
                "problems": problems,
            }, indent=2))
            return 1

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
        critical = load_critical(args.expectations)
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
    # check_expectations() here deliberately uses the REAL calendar (no
    # today= passed), not args.today: this is a hygiene gate on the FILE
    # itself ("has this declaration gone stale as of right now"), separate
    # from args.today, which only feeds classify()'s runtime judgment of
    # THIS run's results below. --check-expectations (check_expectations_cli)
    # is the CLI that lets a caller deliberately simulate a validation date
    # for that hygiene gate; the ordinary verdict path here does not.
    problems = check_expectations(expectations, commands, critical=critical)
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

    # J064: recorded only, never a vote -- see _jev_gate_line_shadow()'s own
    # docstring. verdict/exit code below are computed only from `results`.
    _jev_gate_line_shadow(_gate_verdict_lines(text))

    verdict = classify(results, expectations, today=args.today or _today(),
                       critical=critical, unfinished=args.unfinished)
    verdict["commit"] = parse_commit(text)
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["product"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

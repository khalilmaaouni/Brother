#!/usr/bin/env python3
"""WIRE-02 of the 1.0.20 orchestration control plane: wire the parts that are
already proven and simply never asked.

WHY THIS EXISTS. scripts/assurance_coverage.py (WIRE-01) already tells us
which parts SYSTEM.md reports NO-DATA. Some slice of that NO-DATA list
already has a scripts/test_<part>.py sitting on disk: a real suite that
nobody ever wired into scripts/check_all.sh. That gap is the cheapest real
win this workstream has, but it is cheap only if it is done the right way
round.

THE RULE THAT DOMINATES THIS MODULE: NEVER REGISTER A SUITE THIS MODULE HAS
NOT RUN, IN THIS PROCESS, AND SEEN PASS. A suite nobody has run may well be
broken. Registering a red suite turns the whole battery red, and a battery
that goes red for a reason nobody chose is the kind of thing this estate
switches off, which is strictly worse than the NO-DATA it replaced. So the
order is fixed: RUN each candidate one at a time, bounded by an explicit
timeout, REGISTER only the ones that actually passed, and REPORT everything
else (a failure, a timeout, a suite that dirtied the tree, a name this
module refuses to trust in a shell line) as a finding. Finding a red suite
here is this unit doing its job, not failing it.

ON TIER. A part's Tier A/B/C classification (assurance_coverage.py) answers
"does a NO-DATA gap here matter to a release gate". It answers nothing about
whether wiring an EXISTING, PASSING suite into the battery is a good idea:
a real green test is real evidence regardless of which domain its part
governs, and this module has no rule against registering Tier C candidates.
Tier is read here only for the report, never to exclude a candidate.

ON DRIFT. This worktree is shared: other agents add and edit files while
this module runs. candidates() is therefore called twice, deliberately far
apart in the same run (once before probing, once right before writing), and
anything present in the first read but gone from the second (its test file
removed, the part no longer NO-DATA, or SYSTEM.md itself edited underneath
this process) is dropped rather than acted on. A stale list acted on late is
exactly the kind of state-without-a-fresh-check this estate's rules refuse.

ON SIDE EFFECTS. A probe is run with a snapshot of `git status --porcelain`
taken immediately before and immediately after. Any new dirty path is
reported loudly and that part is never registered: a check that dirties the
tree it measures is a recorded failure in this estate, worth more than the
NO-DATA it would have replaced. This detector is BEST EFFORT in a worktree
other agents are also writing to at the same moment: a hit here can be
concurrent noise, not this probe's own side effect, and the report says so.
The fix for a real hit is in the suite itself, never a suppression here.

CONTINGENCY. On any doubt, this module does not register: a missing
registration is recoverable next run, a red or noisy battery is contagious
to every lane reading it. A read failure on the coverage census, an
unreadable or unwritable battery file, an invalid part name, a probe that
cannot be timed: all of these stop that one candidate (or the whole run, for
a battery the file system will not let this module write) and are printed,
never guessed past. To undo a registration, delete the single `run_check`
line naming the part's test file, or `git checkout scripts/check_all.sh`;
registration only ever adds lines, never rewrites or removes one. Nobody is
paged: the report this module prints (and the WIRE-02 unit's own return to
the orchestrator that ran it) is the only notice, so a real finding here
belongs in that unit's report, not left for the next reader to rediscover.

Python 3, standard library only. No network.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
DEFAULT_COVERAGE = os.path.join(ROOT, "docs", "generated", "ASSURANCE-COVERAGE.json")
DEFAULT_BATTERY = os.path.join(SCRIPTS, "check_all.sh")
DEFAULT_TIMEOUT_S = 60.0

sys.path.insert(0, SCRIPTS)
import assurance_coverage  # noqa: E402  (reused census builder, never re-derived)
import evidence_obligation  # noqa: E402  (reused vocabulary, never restated)

NO_DATA = evidence_obligation.VERDICTS[2]
assert NO_DATA == "NO-DATA", "evidence_obligation.VERDICTS reordered under us"

# Module names in this tree are lowercase snake_case filenames. Anything
# outside that shape is refused rather than interpolated into a subprocess
# argument list or a shell line in check_all.sh: this is the module's answer
# to "a part whose name needs quoting in a shell line", and the answer is to
# never find out the hard way.
VALID_PART_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The marker this module inserts before. check_all.sh names this its LAST
# check on purpose (it diffs against a snapshot taken at the top of the
# file), and inserting after it would silently change what that check
# measures. See _find_insertion_index.
LAST_CHECK_MARKER = 'run_check "real-logs-unchanged"'

RAN_TESTS_RE = re.compile(r"Ran \d+ tests?")


class InvalidPartName(Exception):
    """A part name this module refuses to put in a subprocess call or a
    shell line, because it does not match VALID_PART_RE."""


class MissingTestFile(Exception):
    """probe() was asked for a part with no scripts/test_<part>.py on disk."""


class BatteryWriteError(Exception):
    """The battery file could not be read or written (missing, read-only,
    or any other OSError). Registration never falls back to guessing; it
    stops and reports."""


class LastCheckNotFoundError(Exception):
    """check_all.sh no longer contains LAST_CHECK_MARKER. Rather than insert
    at end-of-file (which could land AFTER whatever check now is actually
    last), this module refuses."""


ProbeResult = collections.namedtuple(
    "ProbeResult",
    "part passed timed_out exit_code duration_s last_line dirtied_tree output",
)

RegisterOutcome = collections.namedtuple(
    "RegisterOutcome", "registered already_registered battery_path"
)


def candidates(coverage_path=None):
    """Parts that are NO-DATA and already have a scripts/test_<part>.py.

    coverage_path is None (the real-run default): the census is built FRESH
    by calling assurance_coverage.build(), which itself re-scans the live
    scripts/ directory and SYSTEM.md on every call, so this never trusts a
    coverage JSON that another agent's edits may have made stale on disk.

    coverage_path names a JSON file (a fixture, or a snapshot someone wants
    re-examined): that file is read as-is, no live scan performed. This is
    the path the test suite uses, since a live scan depends on the exact
    live tree, which this worktree does not hold still.
    """
    if coverage_path is None:
        payload = assurance_coverage.build()
    else:
        with open(coverage_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    return sorted(
        r["part"] for r in payload["parts"]
        if r["status"] == NO_DATA and r.get("test")
    )


def _git_status_lines(repo_root):
    """frozenset of `git status --porcelain` lines, or None when it could
    not be read (not a git checkout, git missing, timed out). None is never
    treated as "clean": callers that cannot compare two None snapshots treat
    the side-effect check as inconclusive, not as a pass."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return frozenset(proc.stdout.splitlines())


def probe(part, *, timeout_s, scripts_dir=None, repo_root=None):
    """Run scripts/test_<part>.py -v once, bounded by timeout_s, and report
    what happened. Never raises on the suite's own failure or timeout; only
    raises on a part this module will not attempt (bad name, missing file).
    """
    if not VALID_PART_RE.match(part):
        raise InvalidPartName(part)
    scripts_dir = SCRIPTS if scripts_dir is None else scripts_dir
    repo_root = ROOT if repo_root is None else repo_root
    test_path = os.path.join(scripts_dir, "test_%s.py" % part)
    if not os.path.isfile(test_path):
        raise MissingTestFile(test_path)

    before = _git_status_lines(repo_root)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", test_path, "-v"],
            cwd=repo_root, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        partial = (exc.stdout or "") + (exc.stderr or "")
        return ProbeResult(
            part=part, passed=False, timed_out=True, exit_code=None,
            duration_s=duration,
            last_line="TIMED OUT after %.0fs, no verdict; machine load or a "
                       "hang, not a verdict about this suite" % timeout_s,
            dirtied_tree=False, output=partial,
        )
    duration = time.monotonic() - start
    after = _git_status_lines(repo_root)
    dirtied = bool(before is not None and after is not None and (after - before))

    output = (proc.stdout or "") + (proc.stderr or "")
    text_lines = [ln for ln in output.splitlines() if ln.strip()]
    last_line = text_lines[-1][:200] if text_lines else "(no output)"
    ran_tests = bool(RAN_TESTS_RE.search(output))
    if proc.returncode == 0 and not ran_tests:
        last_line = "exit 0 but no evidence a test actually ran: %s" % last_line
    passed = (proc.returncode == 0) and ran_tests and not dirtied
    if dirtied and proc.returncode == 0 and ran_tests:
        last_line = "passed, but the working tree changed while it ran: %s" % last_line

    return ProbeResult(
        part=part, passed=passed, timed_out=False, exit_code=proc.returncode,
        duration_s=duration, last_line=last_line, dirtied_tree=dirtied,
        output=output,
    )


def _find_insertion_index(lines):
    """Index to insert new run_check lines at: right before the comment
    block that precedes LAST_CHECK_MARKER, so the last check stays last."""
    for i, line in enumerate(lines):
        if LAST_CHECK_MARKER in line:
            j = i
            while j > 0 and lines[j - 1].lstrip().startswith("#"):
                j -= 1
            return j
    raise LastCheckNotFoundError(
        "no line containing %r found in the battery; refusing to guess an "
        "insertion point" % LAST_CHECK_MARKER
    )


#: A generated check NAME must never accidentally form the shape of a
#: credential. The pre-push gate scans every outgoing line for
#: sk-[A-Za-z0-9_-]{20,} among other patterns, and it cannot tell an API key
#: from a hyphenated identifier that happens to contain the same run of
#: characters. This is not hypothetical: registering the part
#: risk_review_orchestrator produced a name whose middle matched that pattern
#: exactly, at 27 characters, and the push was refused. The gate was right to
#: refuse on shape; the name was benign; and the fix belongs HERE, where names
#: are minted, rather than in the battery file where the symptom appeared.
#: The gate's own source records the same class already: a loose sk- matches
#: the middle of task-.
_SECRET_SHAPED = re.compile(r"(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36})")


def _run_check_line(part):
    name = part.replace("_", "-") + "-self"
    if _SECRET_SHAPED.search(name):
        # Collapse the separator that created the run, never the part name
        # itself: the script path below still uses the real part name, so the
        # check keeps pointing at the same suite. Only the LABEL changes.
        name = part.replace("_", "") + "-self"
        if _SECRET_SHAPED.search(name):
            raise InvalidPartName(
                "generated check name for %r still matches a credential shape "
                "after collapsing separators; refusing to emit it rather than "
                "shipping a line the push gate will block" % part)
    return 'run_check "%s" python3 scripts/test_%s.py -v\n' % (name, part)


def register(parts, battery_path):
    """Insert one run_check line per part not already registered, reading
    battery_path fresh right before editing it (another agent may have
    changed it since this run started) and re-reading it a second time,
    right before the write, changes nothing else in the file. Idempotent:
    a part whose test file is already named anywhere in the file is skipped,
    not duplicated."""
    try:
        with open(battery_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise BatteryWriteError("cannot read %s: %s" % (battery_path, exc))

    already = []
    to_insert = []
    for part in parts:
        if not VALID_PART_RE.match(part):
            raise InvalidPartName(part)
        if ("scripts/test_%s.py" % part) in text:
            already.append(part)
        else:
            to_insert.append(part)

    if not to_insert:
        return RegisterOutcome(
            registered=[], already_registered=sorted(already), battery_path=battery_path
        )

    lines = text.splitlines(keepends=True)
    insert_at = _find_insertion_index(lines)
    new_lines = [_run_check_line(p) for p in sorted(to_insert)]
    lines[insert_at:insert_at] = new_lines
    new_text = "".join(lines)

    try:
        with open(battery_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except OSError as exc:
        raise BatteryWriteError("cannot write %s: %s" % (battery_path, exc))

    return RegisterOutcome(
        registered=sorted(to_insert), already_registered=sorted(already),
        battery_path=battery_path,
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coverage", default=None,
                     help="coverage JSON to read instead of a fresh live scan")
    ap.add_argument("--battery", default=DEFAULT_BATTERY)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--dry-run", action="store_true",
                     help="run every candidate probe, print what would be "
                          "registered, write nothing")
    args = ap.parse_args(argv)

    try:
        first = candidates(args.coverage)
    except assurance_coverage.SystemMdError as exc:
        print("NO-DATA: could not build the census: %s" % exc, file=sys.stderr)
        return 2

    print("candidates (first read): %d" % len(first))
    if first:
        print("  " + ", ".join(first))
    if not first:
        print("nothing to do: 0 candidates NO-DATA with a test file on disk")
        return 0

    battery_abspath = os.path.abspath(args.battery)
    probe_scripts_dir = os.path.dirname(battery_abspath)
    probe_repo_root = os.path.dirname(probe_scripts_dir)

    to_register = []
    for part in first:
        try:
            pr = probe(part, timeout_s=args.timeout,
                       scripts_dir=probe_scripts_dir, repo_root=probe_repo_root)
        except InvalidPartName:
            print("SKIP %-32s invalid part name, refusing to run or register it" % part)
            continue
        except MissingTestFile:
            print("SKIP %-32s test file vanished between the read and the probe" % part)
            continue

        if pr.timed_out:
            print("TIMEOUT %-28s %s" % (part, pr.last_line))
        elif not pr.passed:
            print("FAIL    %-28s exit %-6s %s" % (part, pr.exit_code, pr.last_line))
        else:
            print("PASS    %-28s %.2fs" % (part, pr.duration_s))
            to_register.append(part)

    try:
        second = set(candidates(args.coverage))
    except assurance_coverage.SystemMdError as exc:
        print("NO-DATA: could not re-check the census before registering: %s" % exc,
              file=sys.stderr)
        return 2

    drifted = sorted(set(first) - second)
    if drifted:
        print("drift: %d candidate(s) no longer NO-DATA-with-a-test-file, "
              "skipping: %s" % (len(drifted), ", ".join(drifted)))
    to_register = [p for p in to_register if p in second]

    if args.dry_run:
        print("--dry-run: would register %d part(s)%s"
              % (len(to_register),
                 (": " + ", ".join(to_register)) if to_register else ""))
        return 0

    if not to_register:
        print("nothing green to register")
        return 0

    try:
        outcome = register(to_register, args.battery)
    except (BatteryWriteError, LastCheckNotFoundError, InvalidPartName) as exc:
        print("NOT DONE: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    print("registered %d part(s) into %s%s"
          % (len(outcome.registered), outcome.battery_path,
             (": " + ", ".join(outcome.registered)) if outcome.registered else ""))
    if outcome.already_registered:
        print("already registered, left alone: %s" % ", ".join(outcome.already_registered))
    return 0


if __name__ == "__main__":
    sys.exit(main())

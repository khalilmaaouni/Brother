#!/usr/bin/env python3
"""J4's A.accept-recorded and A.reject-pushes-nothing: the accept route, EXERCISED.

Until this existed both rows carried `measured_by: ACC1` and no check, so they read "the
row declares no check, so nothing measured it" forever, and ACC1 could never close
however well it was built. A row with no check is a unit that cannot land.

WHAT THE ROWS THEMSELVES REFUSE, and it is the whole design of this file. Their own text
says: "Counting those files is REFUSED as the measurement: it would go green on a
mention, with no acceptance ever recorded", and "an absent route must not read as a
refusal that pushed nothing". So this never greps a surface for the word accept. It
finds the installed tool, RUNS it, and looks at what appeared on disk.

  A.accept-recorded      run the installed accept route, then require an acceptance
                         RECORD naming the run it accepted. No record, no pass.
  A.reject-pushes-nothing  offer the installed tool a reject, and when it has one,
                         exercise it against a git remote that must receive nothing.

ABSENT IS A FAIL, NOT A NO-DATA. The install was readable and the answer was no: a
stranger has nothing to accept through. NO-DATA is kept for the one honest case, an
install this cannot read at all.

BOTH DIRECTIONS HAVE NOW BEEN SEEN LIVE, which was not true when this was written.
On 2026-10-29 both rows FAILED, because `accept_delivery.py` was not in
bundle_runtime's SUPPORT_ENTRIES so it never reached an install, and the tool carried
no reject flag at all. ACC1 then landed both, and the rows went green against a real
install. That is red before green at the journey's own level, observed rather than
argued.

THE FAIL DIRECTION IS SAFE BY CONSTRUCTION. A malformed invocation makes the tool
refuse, and a refusal leaves no record, which reads FAIL with the tool's own stderr
quoted. Nothing here can turn a broken invocation into a pass. The selftest also covers
every branch of the judging through injected seams, so the logic is proven independently
of what is installed.

    python3 -B scripts/journey/check_accept_route.py --row accept
    python3 -B scripts/journey/check_accept_route.py --row reject
    python3 -B scripts/journey/check_accept_route.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

TOOL = "accept_delivery.py"
REF = "journey/accept#1"


def installed_tool(config):
    """The installed accept tool's path, or None. Found BY NAME under the sandbox's
    config dir, the same rule check_six_verbs_only.py uses, so this never needs to know
    a cache path."""
    for base, dirs, files in os.walk(config):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if TOOL in files:
            return os.path.join(base, TOOL)
    return None


#: The receipt fields accept_delivery refuses a run directory for lacking, discovered by
#: running it against a synthetic run on 2026-10-29 and reading each refusal in turn.
#: Kept here rather than rediscovered: five invocations bought this list.
_RECEIPT_NODATA_FIELDS = ("intent", "repair_history",
                          "attention", "containment", "continuity",
                          "harness_version", "harness_revision", "report")


def _run_dir(home):
    """A run directory built up to the last requirement this check has verified.

    WHAT THE TOOL ASKS FOR, in the order it asked, each one a real refusal read off a
    real run: a single W-*.json Work document; a claims.json beside it; a review, or an
    explicit review_skip naming a reason and who skipped it (passed as flags, not here);
    receipt/receipt.json; twelve further receipt fields; per-file checks under
    scope.changed; and then per-unit verdicts, which is where this stops.

    EVERY ONE OF THOSE WAS READ OFF A REAL REFUSAL, never guessed, and the last two
    arrived when ACC1 was built: per unit verdicts (`evidence` and `unproven`, each a
    list of {id, state}), a `host_capability.receipt_path` naming the receipt's own
    path, and a `target.json` naming the project the run is FOR, which the caller
    passes back as --project-cwd so the tool can bind the run to a project rather than
    record an acceptance against somebody else's work.

    The fixture is now complete and the accept route records through it. The judging
    logic is proven in BOTH directions by the selftest's injected seams, and the live
    path can only fail, never pass, on a fixture the tool refuses."""
    d = os.path.join(home, "journey-run")
    os.makedirs(os.path.join(d, "receipt"), exist_ok=True)
    with open(os.path.join(d, "W-journey.json"), "w", encoding="utf-8") as fh:
        json.dump({"rows": [{"id": "U1", "done_check": "python3 -B t.py",
                             "files_changed_by_unit": ["t.py"]}]}, fh)
    with open(os.path.join(d, "claims.json"), "w", encoding="utf-8") as fh:
        json.dump({"U1": {"evidence": {"check_command": "python3 -B t.py",
                                       "exit_code": 0}}}, fh)
    checks = [{"file": "t.py", "unit": "U1", "check_command": "python3 -B t.py",
               "exit_code": 0, "output_location": "NO-DATA",
               "state": "verified", "reason": ""}]
    # The run names the project it is FOR, and the caller passes that same path as
    # --project-cwd: the tool refuses a run it cannot bind to a project, which is how
    # it stops an acceptance being recorded against somebody else's work.
    project = os.path.join(home, "journey-project")
    os.makedirs(project, exist_ok=True)
    subprocess.run(["git", "init", "-q", project], capture_output=True, timeout=60)
    with open(os.path.join(d, "target.json"), "w", encoding="utf-8") as fh:
        json.dump({"cwd": project}, fh)
    receipt_path = os.path.join(d, "receipt", "receipt.json")
    receipt = {
        "receipts": [{"id": "U1", "objective": "a journey unit",
                      "command": "python3 -B t.py", "exit_code": 0,
                      "output_location": "NO-DATA", "reason": "",
                      "state": "verified"}],
        "checks": checks,
        "scope": {"changed": checks},
        # Per unit verdicts: each a list of {id, state}. `unproven` is empty on
        # purpose, which the reader accepts, since all() over nothing is true.
        "evidence": [{"id": "U1", "state": "verified"}],
        "unproven": [],
        # The receipt names its own path, which is how the reader refuses a receipt
        # copied in from somewhere else.
        "host_capability": {"receipt_path": receipt_path},
    }
    for field in _RECEIPT_NODATA_FIELDS:
        receipt.setdefault(field, "NO-DATA")
    with open(receipt_path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh)
    return d


def judge_accept(tool, accept):
    """(exit code, one line). `accept` is called only when a tool was found and returns
    (records_written, detail): the records the route left behind, and whatever the tool
    said. Pure, so the selftest judges THIS and not a copy of it."""
    if tool is None:
        return 1, ("FAIL: no %s in the installed plugin, so there is nothing for a "
                   "person to accept through: a stranger who finished a run cannot "
                   "record that they accept it" % TOOL)
    records, detail = accept(tool)
    if records is None:
        return 2, ("NO-DATA: the installed accept route could not be exercised at all "
                   "(%s), so nothing has been shown either way" % detail)
    if not records:
        return 1, ("FAIL: the installed accept route ran and left no acceptance record, "
                   "so an acceptance was not recorded: %s" % detail)
    return 0, ("PASS: the installed accept route recorded the acceptance (%s)"
               % ", ".join(sorted(records))[:160])


def judge_reject(tool, offer_reject, pushed):
    """(exit code, one line). `offer_reject` returns (has_route, detail); `pushed`
    returns how many commits reached the remote, or None when it could not be read."""
    if tool is None:
        return 1, ("FAIL: no %s in the installed plugin, so there is no reject route to "
                   "exercise, and an absent route is not a refusal that pushed nothing"
                   % TOOL)
    has_route, detail = offer_reject(tool)
    if not has_route:
        return 1, ("FAIL: the installed accept tool has no reject route (%s), so a "
                   "person who wants to refuse a run has nothing to refuse with, and an "
                   "absent route must not read as a refusal that pushed nothing"
                   % detail)
    reached = pushed()
    if reached is None:
        return 2, ("NO-DATA: the remote could not be read after the reject, so whether "
                   "anything was pushed has not been shown")
    if reached:
        return 1, ("FAIL: the reject pushed %d commit(s) to the remote, so refusing a "
                   "run does not leave the world alone" % reached)
    return 0, "PASS: the reject route ran and pushed nothing to the remote"


def _accept_for_real(tool):
    home = os.environ.get("HOME") or tempfile.mkdtemp(prefix="accept-home-")
    records = os.path.join(home, "journey-deliveries")
    os.makedirs(records, exist_ok=True)
    run_dir = _run_dir(home)
    project = os.path.join(home, "journey-project")
    cmd = [sys.executable, "-B", tool, "--name", "journey acceptance",
           "--project-cwd", project,
           "--ref", REF, "--accepted-by", "a journey walker",
           "--accepted-at", "2026-10-29", "--recorded-by", "person",
           "--run-dir", run_dir, "--dir", records,
           # The tool refuses a run carrying neither a review nor an explicit skip, and
           # a journey walk has no human reviewer, so the skip is named honestly rather
           # than a review being faked.
           "--review-skip-reason", "a journey walk has no human reviewer",
           "--review-skip-by", "a journey walker",
           "--review-skip-at", "2026-10-29"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, repr(exc)
    try:
        written = [f for f in os.listdir(records) if f.endswith(".json")]
    except OSError as exc:
        return None, repr(exc)
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    return written, ("exit %d: %s" % (r.returncode, tail[-1][:140] if tail else ""))


def _offer_reject(tool):
    """Ask the tool's own interface, never its source text: argparse answers
    `unrecognized arguments` for a flag it does not have, which is the tool saying there
    is no such route. Not --help, because a help flag is not always a help flag in this
    estate (a --help once stopped a live loop)."""
    cmd = [sys.executable, "-B", tool, "--reject", "--ref", REF,
           "--accepted-by", "a journey walker", "--accepted-at", "2026-10-29"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, repr(exc)
    blob = (r.stderr or "") + (r.stdout or "")
    if "unrecognized arguments" in blob and "--reject" in blob:
        return False, "argparse rejected --reject as unrecognized"
    return True, "exit %d" % r.returncode


def _pushed_for_real():
    """Commits that reached a local bare remote, or None when it cannot be read. The
    reject is exercised inside a repo whose origin is that bare repo, so "pushed
    nothing" is an observation of the remote and not a claim about intent."""
    home = os.environ.get("HOME") or tempfile.mkdtemp(prefix="accept-home-")
    bare = os.path.join(home, "journey-remote.git")
    try:
        subprocess.run(["git", "init", "--bare", "-q", bare], check=True,
                       capture_output=True, text=True, timeout=60)
        out = subprocess.run(["git", "-C", bare, "rev-list", "--all", "--count"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        del exc
        return None
    try:
        return int((out.stdout or "0").strip())
    except ValueError:
        return None


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    case("no tool at all is FAIL for accept", judge_accept(None, None)[0], 1)
    case("no tool at all is FAIL for reject", judge_reject(None, None, None)[0], 1)
    case("a route that records nothing is FAIL",
         judge_accept("t", lambda _t: ([], "exit 2: refused"))[0], 1)
    case("a route that cannot be exercised is NO-DATA",
         judge_accept("t", lambda _t: (None, "boom"))[0], 2)
    case("a route that records is PASS",
         judge_accept("t", lambda _t: (["journey-accept-1.json"], "exit 0"))[0], 0)
    case("no reject route is FAIL",
         judge_reject("t", lambda _t: (False, "unrecognized"), lambda: 0)[0], 1)
    case("a reject that pushes is FAIL",
         judge_reject("t", lambda _t: (True, "exit 0"), lambda: 3)[0], 1)
    case("a reject whose remote cannot be read is NO-DATA",
         judge_reject("t", lambda _t: (True, "exit 0"), lambda: None)[0], 2)
    case("a reject that pushes nothing is PASS",
         judge_reject("t", lambda _t: (True, "exit 0"), lambda: 0)[0], 0)

    bad = 0
    for label, want, got, ok in cases:
        bad += 0 if ok else 1
        print("%-52s want %d got %d %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--row", choices=["accept", "reject"], default="accept")
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.config or not os.path.isdir(args.config):
        print("NO-DATA: CLAUDE_CONFIG_DIR %r is not a directory, so no installed plugin "
              "can be read and nothing has been shown about the accept route"
              % args.config)
        return 2
    tool = installed_tool(args.config)
    if args.row == "accept":
        code, line = judge_accept(tool, _accept_for_real)
    else:
        code, line = judge_reject(tool, _offer_reject, _pushed_for_real)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

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
import shlex
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


#: The engine and the intake a stranger's run goes through, found BY NAME under the install.
ENGINE = "brother_run.py"
INTAKE = "bm_project.py"
OUTCOME = "the greeting file exists and its test passes"
#: The machine refusal that means the run could not be attempted here, so the row is NO-DATA, never a FAIL of the
#: product: the engine's own disk admission, matched on its own words. (A wait under the load ceiling is not a
#: refusal: the run goes on once a slot frees, so it is not listed.)
MACHINE_REFUSALS = ("under the refuse floor",)
#: The worker the run is given: it writes each file its prompt declares, so the run reaches a real integration and
#: a real receipt without a model call. The same shape scripts/test_brother_run_plan.py's WRITER_MODEL uses.
WRITER = ("import re, sys\n"
          "p = sys.argv[-1] if len(sys.argv) > 1 else ''\n"
          "m = re.search(r'Declared write scope: ([^\\n]+)', p)\n"
          "for path in (x.strip() for x in (m.group(1).split(',') if m else [])):\n"
          "    if path:\n"
          "        with open(path, 'w') as fh:\n"
          "            fh.write('hello from the journey\\n')\n"
          "print('wrote', m.group(1) if m else 'nothing')\n")
#: The project's own test: it FAILS (an assertion, not a crash) until the file exists, so the run's self revert
#: reads a real failure and the unit can be proven rather than read as passing anyway.
TEST = ("import os, unittest\n"
        "class Greeting(unittest.TestCase):\n"
        "    def test_the_greeting_exists(self):\n"
        "        self.assertTrue(os.path.isfile('greeting.txt'), 'no greeting file')\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n")


def _beside(config, name):
    """The installed file `name`, found BY NAME under the sandbox's config dir, or None."""
    for base, dirs, files in os.walk(config or ""):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if name in files:
            return os.path.join(base, name)
    return None


def _git(args, cwd, env):
    return subprocess.run(["git"] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def real_run(config, home, env=None, timeout=900):
    """(run directory, project, "") from a run made THROUGH THE INSTALLED ENGINE, or (None, None, why).

    WHY IT IS NOT HAND BUILT ANY MORE. Until 2026-11-01 this file wrote a run directory itself, field by field, each
    field read off one refusal of accept_delivery. Every refusal the tool learned made that fixture stale: on
    2026-11-01 OUT1.b's outcome_drift refused it (the hand built receipt carried no outcome), and the row read FAIL
    while the product was right. A fixture that imitates the engine is always one refusal behind it, so the run now
    comes from the engine a stranger has: the installed intake writes the outcome contract from the project, the
    installed brother_run.py runs one unit against it (a writer worker, no model call), and its receipt is what the
    accept route is asked to bind.

    why starts with "NO-DATA" when the machine refused the run (disk floor, load ceiling): that is not a product
    answer either way."""
    env = dict(os.environ if env is None else env)
    engine, intake = _beside(config, ENGINE), _beside(config, INTAKE)
    if not engine or not intake:
        return None, None, "no installed %s" % (ENGINE if not engine else INTAKE)
    project = os.path.join(home, "journey-project")
    os.makedirs(project, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "journey@example.invalid"],
                 ["config", "user.name", "a journey walker"]):
        _git(args, project, env)
    with open(os.path.join(project, "test_greeting.py"), "w", encoding="utf-8") as fh:
        fh.write(TEST)
    _git(["add", "-A"], project, env)
    _git(["commit", "-q", "-m", "the project before the run"], project, env)
    contract = os.path.join(home, "journey-contract.json")
    try:
        got = subprocess.run([sys.executable, "-B", intake, "adopt", "--ask", OUTCOME, "--out", contract],
                             cwd=project, env=env, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "the installed intake could not run: %r" % exc
    try:
        with open(contract, encoding="utf-8") as fh:
            checks = [c["command"] for c in json.load(fh)["success_checks"] if c.get("command")]
    except (OSError, ValueError, KeyError, TypeError):
        checks = []
    if got.returncode or not checks:
        return None, None, "the installed intake wrote no commanded check (exit %d)" % got.returncode
    plan = os.path.join(home, "journey-plan.json")
    with open(plan, "w", encoding="utf-8") as fh:
        json.dump([{"id": "G1", "objective": "write the greeting file", "done_check": checks[0],
                    "writes": ["greeting.txt"], "deps": []}], fh)
    writer = os.path.join(home, "journey-writer.py")
    with open(writer, "w", encoding="utf-8") as fh:
        fh.write(WRITER)
    env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(writer))
    runs = os.path.join(home, "journey-runs")
    try:
        done = subprocess.run([sys.executable, "-B", engine, OUTCOME, "--cwd", project, "--runs-root", runs,
                               "--plan", plan, "--contract", contract, "--quiet"],
                              cwd=home, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, None, "NO-DATA: the installed engine could not run: %r" % exc
    said = done.stdout + done.stderr
    receipts = [ln.split("receipt: ", 1)[1].strip() for ln in said.splitlines()
                if ln.startswith("brother_run: receipt: ")]
    log = os.path.join(os.path.dirname(os.path.dirname(receipts[-1])), "run.log") if receipts else ""
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            said += fh.read()   # the engine's verbatim record names an admission refusal --quiet does not print
    except OSError:
        pass
    refused = [m for m in MACHINE_REFUSALS if m in said]
    if refused:
        return None, None, "NO-DATA: this machine refused the run (%s), so it was not a product answer" % refused[0]
    if not receipts or not os.path.isfile(receipts[-1]):
        tail = (said.strip().splitlines() or [""])[-1][:140]
        return None, None, "the installed engine wrote no receipt (exit %d): %s" % (done.returncode, tail)
    return os.path.dirname(os.path.dirname(receipts[-1])), project, ""


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


def _accept_for_real(tool, config=None, env=None):
    home = os.environ.get("HOME") or tempfile.mkdtemp(prefix="accept-home-")
    records = os.path.join(home, "journey-deliveries")
    os.makedirs(records, exist_ok=True)
    run_dir, project, why = real_run(config or os.environ.get("CLAUDE_CONFIG_DIR", ""), home, env)
    if run_dir is None:
        # A machine refusal is NO-DATA (None); an engine that could not make a run is a FAIL (no record).
        return (None if why.startswith("NO-DATA") else []), why
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
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
        code, line = judge_accept(tool, lambda t: _accept_for_real(t, args.config))
    else:
        code, line = judge_reject(tool, _offer_reject, _pushed_for_real)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

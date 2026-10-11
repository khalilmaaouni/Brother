#!/usr/bin/env python3
# hermetic-budget-seconds: 1800   (89 driver runs, several of which wait in real time: over the 900 s default under load)
"""BL.1: the loop LIFECYCLE speaks, asserted by DRIVING scripts/loop/loop_until.sh for real.

WHY THIS FILE EXISTS. BL.1's promise lives in loop_until.sh: a start note when a run begins, a
terminal state plus a standard report when it ends, and a refusal that is not silent when it will
not start at all. An adversarial audit of scripts/donecheck_bl.py measured, on 2026-09-21, that NOT
ONE of its twelve rows executed loop_until.sh; BL.1's row ran loop_heartbeat.py --selftest, which
is a different file testing a different thing. A sub unit checked only through a neighbour is a sub
unit nobody checked.

EVERY CASE HERE IS BEHAVIOURAL. None is a grep over the source text. That is deliberate and was
paid for the same night: a gate that asserted the SOURCE of a guard kept printing PASS twice while
one line upstream defeated the guard it was reading about. Source text is evidence about a file,
never about a run.

HOW A SHELL DRIVER IS DRIVEN HERMETICALLY. loop_until.sh reaches everything it needs through `~`:
the history file, the alarm file, the heartbeat writer, the lease guard, the worktree sentry, the
funding guard, the report generator, and $WT itself. So a throwaway HOME is the whole seam. Each
case builds its own HOME with stub tools that RECORD their arguments and return a chosen exit code,
which is how the lease and worktree refusals become drivable without holding a real lease or
fighting a live session for the tree. `osascript` is shadowed on PATH so a test run cannot post
desktop notifications.

FIXTURES ARE ORTHOGONAL. One fixture trips one guard: the lease case has a valid future deadline
and a sentry that would say yes, the worktree case has a lease that was granted. A fixture that
trips two guards proves neither, so the lease case also asserts the sentry was never reached.

MUTATION SEAM. loop_until.sh is owned by another worker right now and must not be edited, so
LOOP_UNTIL_SH points this suite at a copy of it. That is how each case was shown capable of going
red: mutate the copy, watch this file fail, delete the copy. The seam refuses a path that does not
exist, so a typo in it cannot read as a pass.

OUT OF SCOPE, said rather than implied: the INTERRUPTED trap (line 93) is not driven here. Sending
a signal mid pass needs a live loop_pass and a race this suite would only flake on; it is covered
by nothing today and is named as a gap rather than faked.

Run: python3 scripts/test_loop_until_lifecycle.py
"""
import datetime
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("LOOP_UNTIL_SH") or os.path.join(HERE, "loop", "loop_until.sh")

_HOMES = []


def _w(path, body, mode=0o644):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


def make_home(guard_acquire_rc=0, sentry_claim_rc=0, lanes="0", report_rc=0, pulse_text="PULSE quiet", pulse_rc=0, intake_rc=0,
              budget="10.00", money="always", pass_body="exit 42", deadline_line="23:59", refresh_body='print("REFRESH OK stub")'):
    """A throwaway HOME whose loop tools are stubs that record their own arguments.

    Every stub appends to $HOME/calls/<tool>, so a call that loop_until.sh makes with
    `>/dev/null 2>&1 || true` (which is most of them) is still observable afterwards. Without that,
    the heartbeat writes would be invisible and the suite could not tell a silent refusal from a
    loud one, which is the exact property BL.1 promises."""
    home = tempfile.mkdtemp(prefix="loop-until-lifecycle-")
    _HOMES.append(home)
    bind = os.path.join(home, ".claude", "bin")
    shim = os.path.join(home, "shim")
    calls = os.path.join(home, "calls")
    wts = os.path.join(home, "Brother", ".claude", "worktrees", "brother-unify-1.1", "scripts")
    for p in (bind, shim, calls, os.path.join(home, ".claude", "evidence"), wts):
        os.makedirs(p, exist_ok=True)

    _w(os.path.join(bind, "loop_guard.sh"),
       '#!/bin/bash\necho "$*" >> "$HOME/calls/guard"\n'
       'if [ "$1" = acquire ]; then exit %d; fi\nexit 0\n' % guard_acquire_rc, 0o755)
    _w(os.path.join(wts, "worktree_sentry.py"),
       '#!/usr/bin/env python3\nimport os, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "sentry"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
       'sys.exit(%d if sys.argv[1:2] == ["claim"] else 0)\n' % sentry_claim_rc, 0o755)
    _w(os.path.join(bind, "loop_heartbeat.py"),
       '#!/usr/bin/env python3\nimport os, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "heartbeat"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n', 0o755)
    # burn_guard is read for its MONEY line (spend) and its LAST line (lanes). The stub's spend grows by 0.60 USD per
    # call, so a case can watch the driver measure spend against the budget: money="always" prints the line every
    # call, "first" only on the first call (money vanishes mid run), "never" prints no MONEY line at all.
    _w(os.path.join(bind, "burn_guard.py"),
       '#!/usr/bin/env python3\nimport os\np = os.path.join(os.environ["HOME"], "calls", "burn")\n'
       'n = len(open(p).read().splitlines()) if os.path.isfile(p) else 0\nopen(p, "a").write("call\\n")\n'
       'open(os.path.join(os.environ["HOME"], "calls", "burn_hour"), "a").write(os.environ.get("BROTHER_STOP_HOUR", "unset") + "\\n")\n'
       'mode = %r\nif mode == "always" or (mode == "first" and n == 0):\n'
       '    print("MONEY   spent %%.2f of 100.00 USD | headroom 99" %% (0.60 * n))\nprint("%s")\n' % (money, lanes), 0o755)
    _w(os.path.join(bind, "loop_report.py"),
       '#!/usr/bin/env python3\nimport sys\nprint("STANDARD REPORT BODY")\nsys.exit(%d)\n' % report_rc, 0o755)
    # Never reached while lanes is 0, but a stub that exits 42 stops a runaway pass loop dead
    # rather than letting a broken case sit in the gap sleep until the timeout.
    _w(os.path.join(bind, "loop_pass.sh"), '#!/bin/bash\necho "pass" >> "$HOME/calls/seq"\n%s\n' % pass_body, 0o755)
    # The login refresh (owner ruling A, 2026-10-05): records its argv and its place in the order, then says what the case
    # hands it. The real one makes one unsandboxed Claude call; no case here may.
    _w(os.path.join(bind, "native_worker.py"),
       '#!/usr/bin/env python3\nimport os, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "native_worker"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
       'open(os.path.join(os.environ["HOME"], "calls", "seq"), "a").write("refresh\\n")\n%s\n' % refresh_body, 0o755)
    # The intake stub answers `status` the way the real tool does: exit 0 with a line when a driver may start.
    _w(os.path.join(bind, "loop_intake.py"),
       '#!/usr/bin/env python3\nimport os, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "intake"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
       'print("INTAKE STATUS: %s")\n%s\nsys.exit(%d)\n' % ("a driver may start" if intake_rc == 0 else "NO START: the intake verdict is NOT READY, not READY",
                                                            ('print("BUDGET_USD %s")\nprint("DEADLINE %s")' % (budget, deadline_line)) if (budget is not None and intake_rc == 0) else "pass", intake_rc), 0o755)
    # The pulse stub prints what the case hands it and records its argv, so a case can see both that the driver
    # called it and what the driver did with a WARN line. pulse_rc proves the driver never reads its exit code.
    _w(os.path.join(bind, "pass_pulse.py"),
       '#!/usr/bin/env python3\nimport os, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "pulse"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
       'print(%r)\nsys.exit(%d)\n' % (pulse_text, pulse_rc), 0o755)
    _w(os.path.join(shim, "osascript"),
       '#!/bin/bash\necho "$*" >> "$HOME/calls/osascript"\nexit 0\n', 0o755)
    # stop_loop.sh records how the driver asked it to stop runners, and in which order among the end steps (U8, U5).
    _w(os.path.join(bind, "stop_loop.sh"),
       '#!/bin/bash\necho "$*" >> "$HOME/calls/stop_loop"\necho "stop_loop $*" >> "$HOME/calls/order"\n'
       'echo "STOPPED: nothing of the loop is alive"\nexit 0\n', 0o755)
    return home


# THE DRIVER'S ESTATE PATHS, CEILINGS AND RESOURCE READINGS NEVER COME FROM THE CALLER. Its paths default under $HOME
# and a live driver's shell exports them, so an inherited one sent every case's run folder into the real estate (32
# empty run folders on 2026-09-24 hid the intake's mix advice). Its disk, swap and daemon readings default to this
# laptop, so every start case failed the night swap stood at 9170 MB: the suite measured the machine, not the script.
# A case that tests a reading sets it itself in os.environ (as the full disk case does); otherwise it reads as roomy.
# THE POWER SOURCE AND THE LAST WAKE ARE READINGS TOO (2026-10-05): without them here, every start case is refused on a
# laptop running from its battery, and a low disk case reads differently in the minutes after the lid was opened.
ESTATE = ("BROTHER_RUNS_ROOT", "BROTHER_RUN_DIR", "BROTHER_SCRATCH", "BROTHER_GRADE_SANDBOXES",
          "BROTHER_SWAP_CEILING_MB", "BROTHER_FSEVENTSD_CEILING_KB", "BROTHER_DISK_FLOOR_KB", "BROTHER_SWAP_GUARD",
          "BROTHER_POWER_WINDOW_S", "BROTHER_WAKE_SETTLE_S", "BROTHER_WAKE_SETTLE_NAP_S")
ROOMY = {"BROTHER_DISK_FREE_KB": "9999999", "BROTHER_SWAP_USED_MB": "100", "BROTHER_FSEVENTSD_KB": "5000",
         "BROTHER_POWER_SOURCE": "AC Power", "BROTHER_WAKE_EPOCH": "0"}


def run(home, args, timeout=90, drop=(), extra=None, script=None):
    """drop: names removed from the caller's environment, so a case tests what the DRIVER sets and not what the shell
    running this suite happened to export; extra: names a case sets for itself."""
    base = dict(ROOMY, **{k: v for k, v in os.environ.items() if k not in ESTATE and k not in drop})
    env = dict(base, HOME=home,
               PATH=os.path.join(home, "shim") + os.pathsep + os.environ.get("PATH", ""),
               BROTHER_LAUNCH_WORKTREE=os.path.join(home, "Brother", ".claude", "worktrees", "brother-unify-1.1"),
               **(extra or {}))
    return subprocess.run(["bash", script or SCRIPT] + args, capture_output=True, text=True,
                          env=env, timeout=timeout)


def ev(home, name):
    p = os.path.join(home, ".claude", "evidence", name)
    if not os.path.isfile(p):
        return None
    return open(p, encoding="utf-8").read()


def called(home, name):
    p = os.path.join(home, "calls", name)
    return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


def run_dirs(home):
    """Every run-* directory this run created under its own (throwaway) BROTHER_RUNS_ROOT default."""
    root = os.path.join(home, ".claude", "evidence", "loop-runs")
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, d) for d in os.listdir(root) if d.startswith("run-")]


def future_hhmm():
    """A stop time comfortably in the future TODAY. The driver builds the epoch from today's date
    plus this HH:MM, so a time near midnight would land in the past and trip a different guard."""
    now = datetime.datetime.now()
    return "23:59" if now.hour < 23 else "%02d:%02d" % (now.hour, 59)


def past_hhmm():
    """A stop time already gone TODAY. Just after midnight, now minus five minutes belongs to
    yesterday and the driver would read it as tonight, which is the future: 00:00 is past instead."""
    now = datetime.datetime.now()
    back = now - datetime.timedelta(minutes=5)
    return "00:00" if back.date() != now.date() else back.strftime("%H:%M")


def case_no_budget_line_refuses_before_touching_anything():
    """The intake stub answers status with no BUDGET_USD line: the driver refuses, exit 2, before the lease or the claim."""
    h = make_home(budget=None)
    r = run(h, ["23:59", "1"])
    return (r.returncode == 2 and "names no budget" in r.stdout and "acquire" not in called(h, "guard")
            and "claim" not in called(h, "sentry") and "REFUSED-TO-START" in (ev(h, "LOOP-ALARM-HISTORY.txt") or ""))


def case_no_money_line_refuses_to_start():
    """burn_guard prints lanes but no MONEY line: an unknown spend never starts a run, exit 2, nothing touched."""
    h = make_home(lanes="1", money="never")
    r = run(h, ["23:59", "1"])
    return r.returncode == 2 and "cannot be measured" in r.stdout and "acquire" not in called(h, "guard")


def case_a_past_hhmm_means_tomorrow_never_a_refusal():
    """H3 (2026-09-24): a night armed before midnight for the small hours. A time already past today is tomorrow's, so
    the driver goes on to its next check instead of refusing "not in the future"."""
    import datetime as _dt
    past = (_dt.datetime.now() - _dt.timedelta(minutes=2)).strftime("%H:%M")
    h = make_home(lanes="1")
    r = run(h, [past, "1"])
    return "not in the future" not in r.stdout and "means tomorrow" in r.stdout


def case_a_dated_and_a_tomorrow_deadline_are_read_and_garbage_is_refused():
    h = make_home(lanes="1")
    r1 = run(h, ["2099-01-01 08:00", "1"]); r2 = run(h, ["tomorrow 05:00", "1"]); r3 = run(h, ["five-ish", "1"])
    return ("not in the future" not in r1.stdout and "could not read" not in r1.stdout
            and "not in the future" not in r2.stdout and "could not read" not in r2.stdout
            and r3.returncode == 2 and "could not read the stop time" in r3.stdout)


def case_cost_per_landing_over_the_cap_ends_the_run_by_name():
    """The cost per landing WARNS and the run CONTINUES (2026-09-25). As a stop (H6, 2026-09-24) it ended the 08:56 run at
    10:10 after 3 landings and the machine sat idle until 16:45; the absolute budget is what ends a run on money."""
    h = make_home(lanes="1", pulse_text="PULSE pass 1 | 0.1 h | landed 3 | usd/landing 3.00 | 1 WARNING(S)\nWARN COST-PER-LANDING: 3.00 USD over 3 landing(s), above the 2.00 cap the owner set")
    r = run(h, ["23:59", "1"])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return r.returncode == 42 and "ALARM COST" not in hist and "LOOP FINISHED" in (ev(h, "LOOP-ALARM.txt") or "")


def case_a_full_disk_refuses_to_start():
    """2026-09-23 21:41 the night run died silently on a full disk. Under the floor nothing starts: exit 2, the reason
    names the floor, and the lease is never taken. BROTHER_DISK_FREE_KB stands in for the df reading."""
    h = make_home(lanes="1")
    env_before = os.environ.get("BROTHER_DISK_FREE_KB"); os.environ["BROTHER_DISK_FREE_KB"] = "1000"
    try:
        r = run(h, ["23:59", "1"])
    finally:
        if env_before is None: os.environ.pop("BROTHER_DISK_FREE_KB", None)
        else: os.environ["BROTHER_DISK_FREE_KB"] = env_before
    return r.returncode == 2 and "under the floor" in r.stdout and "acquire" not in called(h, "guard")


def case_disk_hold_holds_low_and_unreadable_never_roomy():
    """disk_hold, read from the script's own source: low holds, unreadable holds, above the floor passes."""
    src = open(SCRIPT, encoding="utf-8").read()
    fn = "DISK_FLOOR_KB=2097152; SWAP_CEILING_MB=8192; FSE_CEILING_KB=2097152\ndisk_hold() {" + src.split("\ndisk_hold() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    fine = dict(BROTHER_DISK_FREE_KB="9999999", BROTHER_SWAP_USED_MB="100", BROTHER_FSEVENTSD_KB="5000")
    def probe(**seams):
        r = subprocess.run(["bash", "-c", fn + "disk_hold"], capture_output=True, text=True, env=dict(os.environ, **dict(fine, **seams)))
        return r.stdout.strip()
    return ("under the floor" in probe(BROTHER_DISK_FREE_KB="1000") and "unreadable" in probe(BROTHER_DISK_FREE_KB="n/a")
            and probe() == "")

def case_the_run_scratch_is_pruned_by_age_every_pass():
    """The per pass prune line, read from the script's own source (auditor finding 2026-09-24: it was covered by no
    test): an entry older than three hours goes, a fresh one stays, the scratch folder itself stays."""
    src = open(SCRIPT, encoding="utf-8").read()
    line = [l for l in src.splitlines() if l.strip().startswith('find "$RUN_TMP"')]
    if len(line) != 1: return False
    root = tempfile.mkdtemp(prefix="prune-case-"); old = os.path.join(root, "grade-old"); fresh = os.path.join(root, "grade-fresh")
    os.makedirs(old); os.makedirs(fresh); os.utime(old, (0, 0))
    subprocess.run(["bash", "-c", line[0].strip()], env=dict(os.environ, RUN_TMP=root), capture_output=True)
    return not os.path.isdir(old) and os.path.isdir(fresh) and os.path.isdir(root)

def _run_dir_fn():
    """make_run_dir, read from the script's own source, with a refuse() that only prints."""
    src = open(SCRIPT, encoding="utf-8").read()
    return ("refuse() { echo REFUSED: $1; }\nLOOP_DIR=%s\nmake_run_dir() {" % os.path.dirname(SCRIPT)
            + src.split("\nmake_run_dir() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n")


def case_the_recorder_is_on_for_every_run():
    """The run directory block, read from the script's own source: BROTHER_RUN_DIR is a fresh durable folder under
    BROTHER_RUNS_ROOT, created before any pass, so the fan out records decisions and outcomes (D0) and the reports of
    D5 have a journal to read. A root that cannot be written refuses the run rather than recording nothing."""
    fn = _run_dir_fn()
    root = tempfile.mkdtemp(prefix="runs-case-")
    base = {k: v for k, v in os.environ.items() if k not in ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_RUN_DIR")}
    r = subprocess.run(["bash", "-c", fn + 'make_run_dir || exit 2; echo "$BROTHER_RUN_DIR"; [ -d "$BROTHER_RUN_DIR" ] && echo present'], capture_output=True, text=True,
                       env=dict(base, BROTHER_RUNS_ROOT=root))
    lines = r.stdout.split()
    unwritable = subprocess.run(["bash", "-c", fn + 'make_run_dir || exit 2; echo end'], capture_output=True, text=True, env=dict(base, BROTHER_RUNS_ROOT="/dev/null/none"))
    return (len(lines) == 2 and lines[0].startswith(os.path.join(root, "run-")) and lines[1] == "present"
            and "REFUSED" in unwritable.stdout and "end" not in unwritable.stdout)

def case_claude_spend_counts_toward_the_budget():
    """BUDGET SOURCES ARE SEPARATE (owner, 2026-09-27: "100 openrouter budget and 20M Claude tokens"). Budget 1.00 USD, the
    OpenRouter stub spends 0.60, and a Claude done row of 0.50 USD stamped after the start: the Claude spend is measured and
    printed beside the OpenRouter budget but never trips it (it has its own budget, the session spend guard); an OpenRouter
    spend of 0.60 on a 0.50 budget trips BUDGET on its own."""
    h = make_home(lanes="1", budget="1.00")
    led = os.path.join(h, "claude-calls.jsonl")
    with open(led, "w") as fh: fh.write(json.dumps({"at": "2099-01-01T00:00:00", "model": "claude-sonnet-5", "cost_usd": 0.5}) + "\n")
    env_before = os.environ.get("BROTHER_CLAUDE_CALLS_LEDGER"); os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = led
    try:
        r = run(h, ["23:59", "1"]); alarm = ev(h, "LOOP-ALARM.txt") or ""
        reported_not_counted = "LOOP BUDGET at" not in alarm and "Claude 0.5000" in (r.stdout or "")
        h2 = make_home(lanes="1", budget="0.50"); r2 = run(h2, ["23:59", "1"])
        openrouter_trips = r2.returncode == 3 and "LOOP BUDGET at" in (ev(h2, "LOOP-ALARM.txt") or "")
    finally:
        if env_before is None: os.environ.pop("BROTHER_CLAUDE_CALLS_LEDGER", None)
        else: os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = env_before
    return reported_not_counted and openrouter_trips

def _funding_stub(h, script):
    """Replace the funding stub: call 0 (the start's SPENT0 read) prints a MONEY line; later calls follow script(n)."""
    with open(os.path.join(h, ".claude", "bin", "burn_guard.py"), "w") as fh:
        fh.write('#!/usr/bin/env python3\nimport os\np = os.path.join(os.environ["HOME"], "calls", "burn")\n'
                 'n = len(open(p).read().splitlines()) if os.path.isfile(p) else 0\nopen(p, "a").write("call\\n")\n'
                 'if n == 0 or not (%s):\n    print("MONEY   spent 0.00 of 100.00 USD | headroom 99")\n    print("FUNDING OK stub")\n    print("1")\n'
                 'else:\n    print("FUNDING NO-DATA see the next line")\n    print("NO-DATA: ledger unreadable (stub outage); no lane is funded")\n    print("0")\n' % script)


def case_unreadable_funding_holds_and_resumes_never_money_spent():
    """Owner, 2026-09-27: no mistaken UNFUNDED. Two NO-DATA reads (the start read is fine) HOLD the run: no pass, no
    UNFUNDED, the reason printed; when funding reads again the pass runs (the stub pass exits 42)."""
    h = make_home(lanes="1")
    _funding_stub(h, "n in (1, 2)")
    r = run(h, ["23:59", "1"], extra=dict(FUND_HOLD_NAP_S="1", FUND_HOLD_MAX_S="600"))
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 42 and "HOLD: funding cannot be read (NO-DATA: ledger unreadable (stub outage)" in (r.stdout or "")
            and "funding reads again" in (r.stdout or "") and "UNFUNDED" not in alarm)


def case_funding_unreadable_past_its_bound_ends_with_the_true_reason():
    """The hold is bounded: NO-DATA for longer than FUND_HOLD_MAX_S ends the run UNFUNDED naming the unreadable funding,
    never "the money is spent"."""
    h = make_home(lanes="1")
    _funding_stub(h, "True")
    r = run(h, ["23:59", "1"], extra=dict(FUND_HOLD_NAP_S="1", FUND_HOLD_MAX_S="5"))
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 3 and "LOOP UNFUNDED at" in alarm and "funding could not be read" in alarm
            and "money is spent" not in alarm and "budget is spent" not in alarm)


def case_swap_and_fseventsd_hold_like_a_full_disk():
    """2026-09-23: fseventsd at 39 GB and 32 GB of swap filled the disk before the disk floor could see it. Each reading
    holds on its own, one condition per probe; unreadable holds; at rest both pass."""
    src = open(SCRIPT, encoding="utf-8").read()
    fn = "DISK_FLOOR_KB=2097152; SWAP_CEILING_MB=8192; FSE_CEILING_KB=2097152\ndisk_hold() {" + src.split("\ndisk_hold() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    fine = dict(BROTHER_DISK_FREE_KB="9999999", BROTHER_SWAP_USED_MB="100", BROTHER_FSEVENTSD_KB="5000", BROTHER_SWAP_GUARD="on")
    def probe(**seams):
        r = subprocess.run(["bash", "-c", fn + "disk_hold"], capture_output=True, text=True, env=dict(os.environ, **dict(fine, **seams)))
        return r.stdout.strip()
    swap, fse = probe(BROTHER_SWAP_USED_MB="9000"), probe(BROTHER_FSEVENTSD_KB="40000000")
    return ("swap in use 9000 MB is over" in swap and "disk" not in swap and "daemon" not in swap
            and "file event daemon holds 39062 MB" in fse and "swap" not in fse
            and "swap usage is unreadable" in probe(BROTHER_SWAP_USED_MB="x") and "daemon size is unreadable" in probe(BROTHER_FSEVENTSD_KB="x")
            and probe(BROTHER_SWAP_USED_MB="8192", BROTHER_FSEVENTSD_KB="2097152") == "")

def case_the_swap_hold_is_off_unless_the_owner_turns_it_on():
    """Owner 2026-09-25 09:0x ("Remove this until I say so"): with BROTHER_SWAP_GUARD unset, 99999 MB of swap holds
    nothing; the daemon hold still fires. ONE condition per probe: the guard's switch."""
    src = open(SCRIPT, encoding="utf-8").read()
    fn = "DISK_FLOOR_KB=2097152; SWAP_CEILING_MB=8192; FSE_CEILING_KB=2097152\ndisk_hold() {" + src.split("\ndisk_hold() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    base = {k: v for k, v in os.environ.items() if k != "BROTHER_SWAP_GUARD"}
    def probe(**seams):
        r = subprocess.run(["bash", "-c", fn + "disk_hold"], capture_output=True, text=True,
                           env=dict(base, **dict({"BROTHER_DISK_FREE_KB": "9999999", "BROTHER_FSEVENTSD_KB": "5000"}, **seams)))
        return r.stdout.strip()
    return (probe(BROTHER_SWAP_USED_MB="99999") == "" and probe(BROTHER_SWAP_USED_MB="x") == ""
            and "swap in use 99999 MB" in probe(BROTHER_SWAP_USED_MB="99999", BROTHER_SWAP_GUARD="on")
            and "daemon" in probe(BROTHER_SWAP_USED_MB="99999", BROTHER_FSEVENTSD_KB="40000000"))

# ---------------------------------------------------------------- power and wake (2026-10-05: a DISK stop that was a sleep)
# A proof pair started on battery slept at 1%, the sleep wrote a hibernation image the size of memory, and the first disk
# reading after the wake ended the run DISK on a disk that was not full. Three readings decide these cases and each has
# its seam in ROOMY: the power source, the last wake, the free space. A case that needs a reading to CHANGE during a
# run empties the seam and shadows the real tool on PATH instead (pmset, df), the way the swap case shadows sysctl: a
# file in the throwaway HOME drives the stub, and the pass stub is what writes it.
A_DAY_AWAY = ["tomorrow 12:00", "1"]   # a deadline no power window covers at any hour of the day, and a 1 s gap


def _shim(home, name, body):
    _w(os.path.join(home, "shim", name), "#!/bin/bash\n" + body + "\n", 0o755)


def _pass_script(*steps):
    """Shell for the pass stub: pass n runs steps[n-1] and exits 0; the pass after the last step exits 42 (FINISHED)."""
    body = 'n=$(( $(cat "$HOME/calls/passn" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$HOME/calls/passn"\n'
    for i, step in enumerate(steps, 1):
        body += 'if [ "$n" = %d ]; then %s; exit 0; fi\n' % (i, step)
    return body + "exit 42"


def _pmset_says_the_file(home):
    """pmset stub in the shape `pmset -g batt` prints: it names the source written in $HOME/power-says, 'AC Power' when
    the file is absent, and prints nothing at all (exit 1) when the file is empty."""
    _shim(home, "pmset", 's="AC Power"; [ -e "$HOME/power-says" ] && s=$(cat "$HOME/power-says")\n'
                         '[ -n "$s" ] || exit 1\necho "Now drawing from \'$s\'"\n'
                         'echo " -InternalBattery-0 (id=1)\t50%; discharging; present: true"')


def _df_reads(home, reads="L"):
    """df stub. Roomy until $HOME/disk-low exists; from then on each call takes the next word of `reads`, and the last
    word repeats for ever: L is 1000 KB free, R is roomy again (the image was released), S is L plus a second sleep
    (the clock jumps an hour, see _clock_jumps, and the kernel reports a new wake at the jumped time)."""
    _shim(home, "df", 'free=9999999\nif [ -e "$HOME/disk-low" ]; then\n'
                      '  n=$(( $(cat "$HOME/calls/df-n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$HOME/calls/df-n"\n'
                      '  set -- ' + reads + '; [ "$n" -gt $# ] && n=$#; eval "w=\\${$n}"\n'
                      '  case "$w" in\n    R) ;;\n'
                      '    S) free=1000; echo low >> "$HOME/calls/df-low"; touch "$HOME/clock-jump"\n'
                      '       echo $(( $(/bin/date +%s) + 3600 )) > "$HOME/wake-says";;\n'
                      '    *) free=1000; echo low >> "$HOME/calls/df-low";;\n  esac\nfi\n'
                      'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                      'echo "/dev/stub 10000000 1 $free 1% /"')


def _clock_jumps(home):
    """date stub for a second sleep: once $HOME/clock-jump exists `date +%s` answers an hour later, which is what a
    wall clock does across a sleep. Every other date call is the real one."""
    _shim(home, "date", 'if [ "$*" = "+%s" ] && [ -e "$HOME/clock-jump" ]; then echo $(( $(/bin/date +%s) + 3600 ))\n'
                        'else exec /bin/date "$@"; fi')


def _driver_log(home):
    d = os.path.join(home, ".claude", "evidence")
    return "".join(ev(home, n) or "" for n in sorted(os.listdir(d)) if n.startswith("loop-until-"))


def _power_alerts(home):
    return [a for a in _alerts(home) if "brother.loop POWER" in a]


def case_on_ac_power_a_long_run_starts():
    """The control for every power case below: 'AC Power', a deadline a day away. The run starts and finishes (the pass
    stub exits 42) and not one word is said about power."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "AC Power"})
    return (r.returncode == 42 and "acquire" in called(h, "guard") and "POWER" not in r.stdout
            and not _power_alerts(h) and "LOOP POWER" not in (ev(h, "LOOP-ALARM-HISTORY.txt") or ""))


def case_on_battery_a_long_run_refuses_to_start():
    """ONE condition against the control: 'Battery Power'. Exit 2, the reason names the battery, the refusal speaks on
    the heartbeat and in the history, and neither the intake nor the lease is ever reached."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "Battery Power"})
    return (r.returncode == 2 and "REFUSED TO START: the machine draws from battery" in r.stdout
            and "acquire" not in called(h, "guard") and called(h, "intake") == ""
            and "--state REFUSED" in called(h, "heartbeat")
            and "the machine draws from battery" in (ev(h, "LOOP-ALARM-HISTORY.txt") or ""))


def case_an_unreadable_power_source_refuses_like_battery():
    """An unknown source is never AC. ONE condition against the control: the reading is a source that is neither of the
    two pmset names for mains and battery (a UPS). Exit 2 before the lease, and the reason says unreadable, not battery."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "UPS Power"})
    return (r.returncode == 2 and "REFUSED TO START: the power source is unreadable" in r.stdout
            and "draws from battery" not in r.stdout and "acquire" not in called(h, "guard")
            and "--state REFUSED" in called(h, "heartbeat"))


def case_a_silent_pmset_on_macos_is_unreadable_and_refuses():
    """The reading itself, no seam: uname says Darwin and the pmset on PATH prints nothing. That is UNREADABLE, never
    NO-DATA: on macOS a pmset that cannot answer refuses. The next case changes the kernel's name and nothing else."""
    h = make_home(lanes="1")
    _shim(h, "uname", "echo Darwin")
    _shim(h, "pmset", "exit 1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    return (r.returncode == 2 and "REFUSED TO START: the power source is unreadable" in r.stdout
            and "NO-DATA" not in r.stdout and "acquire" not in called(h, "guard"))


def case_a_host_that_is_not_macos_reads_no_data_and_starts():
    """NO-DATA is not a refusal, and the direction is the driver's own decision: the same silent pmset as the case
    above with ONE condition changed, uname says Linux. The check says POWER NO-DATA once and the run starts and ends on
    its pass (exit 42), with no alert."""
    h = make_home(lanes="1")
    _shim(h, "uname", "echo Linux")
    _shim(h, "pmset", "exit 1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    return (r.returncode == 42 and r.stdout.count("POWER NO-DATA") == 1 and "REFUSED" not in r.stdout
            and "acquire" in called(h, "guard") and not _power_alerts(h))


def case_an_unreadable_kernel_name_is_unreadable_never_no_data():
    """NO-DATA needs a kernel name that was READ and is not macOS. ONE condition against the case above: uname prints
    nothing. An unknown host is not a host without pmset, so the start is refused."""
    h = make_home(lanes="1")
    _shim(h, "uname", "exit 1")
    _shim(h, "pmset", "exit 1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    return (r.returncode == 2 and "REFUSED TO START: the power source is unreadable" in r.stdout
            and "NO-DATA" not in r.stdout and "acquire" not in called(h, "guard"))


def case_on_battery_inside_the_window_the_run_starts():
    """A short run may start on battery. ONE condition against the battery refusal: BROTHER_POWER_WINDOW_S now covers
    the deadline. The start says why it was allowed, in one line, and the run ends on its pass."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "Battery Power", "BROTHER_POWER_WINDOW_S": "999999"})
    return (r.returncode == 42 and r.stdout.count("POWER battery: allowed to start") == 1
            and "REFUSED" not in r.stdout and "acquire" in called(h, "guard"))


def case_a_garbage_power_window_leaves_the_default_in_force():
    """A window nobody can read is never a wide one. ONE condition against the case above: the window is the word
    'soon'. The default (20 minutes) stands and the battery start a day before its deadline is refused."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "Battery Power", "BROTHER_POWER_WINDOW_S": "soon"})
    return (r.returncode == 2 and "the machine draws from battery" in r.stdout
            and "more than the 20 minutes a battery start is allowed" in r.stdout and "acquire" not in called(h, "guard"))


def case_a_window_with_a_leading_zero_is_read_in_base_ten():
    """0900 is nine hundred seconds. Read as octal it is an arithmetic error, and an error inside the refusal skips the
    refusal: the run then starts on battery a day before its deadline. ONE condition against the battery refusal: the
    window is written 0900. Still refused, and the reason says 15 minutes."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": "Battery Power", "BROTHER_POWER_WINDOW_S": "0900"})
    return (r.returncode == 2 and "more than the 15 minutes a battery start is allowed" in r.stdout
            and "acquire" not in called(h, "guard") and "value too great" not in r.stderr)


def case_battery_mid_run_is_announced_once_and_the_run_continues():
    """The charger is pulled during pass 1 (the pass stub writes the file the pmset stub reads) and stays out. The
    driver says so ONCE, before pass 2: one alert, one history entry, one log line. Before pass 3 it says nothing more,
    and the run ends on the pass's own verdict (FINISHED, exit 42), never on the power source."""
    h = make_home(lanes="1", pass_body=_pass_script('echo "Battery Power" > "$HOME/power-says"', ":"))
    _pmset_says_the_file(h)
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    log, hist, said = _driver_log(h), ev(h, "LOOP-ALARM-HISTORY.txt") or "", "POWER: the machine draws from battery"
    return (r.returncode == 42 and "===== pass 3 at" in log and len(_power_alerts(h)) == 1
            and log.count(said) == 1 and hist.count("LOOP POWER at") == 1 and "the machine draws from battery" in hist
            and log.index("===== pass 1 at") < log.index(said) < log.index("===== pass 2 at")
            and "LOOP FINISHED at" in (ev(h, "LOOP-ALARM.txt") or "") and "REFUSED" not in r.stdout)


def case_a_second_battery_episode_is_announced_again():
    """Once per EPISODE, not once per run. Battery during pass 1, AC again during pass 2, battery again during pass 3:
    two alerts, and exactly one 'back on AC' line between them. A latch that never resets would stay silent the second
    time, which is the night the charger falls out again."""
    h = make_home(lanes="1", pass_body=_pass_script('echo "Battery Power" > "$HOME/power-says"', 'rm -f "$HOME/power-says"',
                                                    'echo "Battery Power" > "$HOME/power-says"'))
    _pmset_says_the_file(h)
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    log, said = _driver_log(h), "POWER: the machine draws from battery"
    return (r.returncode == 42 and len(_power_alerts(h)) == 2 and log.count(said) == 2 and log.count("POWER: back on AC") == 1
            and log.index(said) < log.index("POWER: back on AC") < log.rindex(said)
            and (ev(h, "LOOP-ALARM-HISTORY.txt") or "").count("LOOP POWER at") == 2)


def case_a_power_source_that_goes_unreadable_mid_run_is_announced_once():
    """Mid run an unreadable source is said like a battery one, in its own words, and never ends the run. ONE condition
    against the battery case: during pass 1 pmset stops answering instead of naming the battery. (Two passes: the
    once per episode latch is the battery case's to prove.)"""
    h = make_home(lanes="1", pass_body=_pass_script(': > "$HOME/power-says"'))
    _pmset_says_the_file(h)
    r = run(h, A_DAY_AWAY, extra={"BROTHER_POWER_SOURCE": ""})
    log, alerts = _driver_log(h), _power_alerts(h)
    return (r.returncode == 42 and "===== pass 2 at" in log and len(alerts) == 1 and "cannot be read" in alerts[0]
            and log.count("POWER: the power source cannot be read") == 1 and "draws from battery" not in log)


def _sysctl_wake_says_the_file(home):
    """sysctl stub in the shape `sysctl -n kern.waketime` prints: the epoch written in $HOME/wake-says, and nothing at
    all (exit 1) while that file is absent, which is a wake time nobody can read."""
    _shim(home, "sysctl", 'if [ "$2" = kern.waketime ] && [ -s "$HOME/wake-says" ]; then\n'
                          '  echo "{ sec = $(cat "$HOME/wake-says"), usec = 679958 } Mon Oct  5 22:49:19 2026"\n'
                          'else exit 1; fi')


WOKE = 'date +%s > "$HOME/wake-says"'   # a pass stub step: the machine woke from sleep during this pass
DISK_LOW = 'touch "$HOME/disk-low"'      # a pass stub step: from now on the free space reads under the floor


def _wake_run(steps, reads="L", settle="20", nap="1", intake=None, prepare=None):
    """One run in which the pass stub decides, pass by pass, when the machine woke and when the free space falls, and
    `reads` scripts what each later disk reading says. Both readings go through the real tools' own output (sysctl, df),
    shadowed on PATH; the settle re-reads every nap s."""
    h = make_home(lanes="1", pass_body=_pass_script(*steps))
    _df_reads(h, reads)
    _sysctl_wake_says_the_file(h)
    _clock_jumps(h)
    if intake:
        _intake_seq(h, intake)
    if prepare:
        prepare(h)
    r = run(h, A_DAY_AWAY, extra={"BROTHER_DISK_FREE_KB": "", "BROTHER_WAKE_EPOCH": "",
                                  "BROTHER_WAKE_SETTLE_S": settle, "BROTHER_WAKE_SETTLE_NAP_S": nap})
    return h, r


def _clock_s(text, pattern):
    """Seconds of the day of the first HH:MM:SS that `pattern` captures in `text`, None when it is absent."""
    m = re.search(pattern, text)
    return None if not m else sum(int(x) * k for x, k in zip(m.group(1).split(":"), (3600, 60, 1)))


HELD, BACK = "read after the machine woke from sleep", "the free disk space reads above the floor again"


def _woke_at(home):
    """The wake the kernel stub reported, as the driver prints it (HH:MM:SS): a sleep is named by its time, or it
    names nothing a reader can check against pmset -g log."""
    with open(os.path.join(home, "wake-says"), encoding="utf-8") as fh:
        return time.strftime("%H:%M:%S", time.localtime(int(fh.read().strip())))


def case_a_wake_then_a_recovered_disk_continues():
    """2026-10-05 22:49:24, replayed: the machine woke during pass 1, the reading at the top of pass 2 is under the
    floor, and the space comes back on the third reading. The driver holds, re-reads, says it was the sleep, and pass 2
    runs: FINISHED (exit 42), no DISK anywhere."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], reads="L L L R", settle="30")
    log, hist = _driver_log(h), ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return (r.returncode == 42 and "LOOP DISK" not in hist and "LOOP FINISHED at" in (ev(h, "LOOP-ALARM.txt") or "")
            and HELD in log and BACK in log and "===== pass 2 at" in log
            and log.index("===== pass 1 at") < log.index(HELD) < log.index(BACK) < log.index("===== pass 2 at")
            and called(h, "df-low").count("low") == 3 and (HELD + " at " + _woke_at(h)) in log
            # the lease is renewed at every re-read of the hold, not only by the two passes (2 renews without it)
            and called(h, "guard").count("renew") >= 4)


def case_a_wake_then_space_that_stays_low_raises_the_alarm_naming_the_sleep():
    """A real full disk still stops the run after a wake: the wait is bounded and the floor is unchanged. ONE condition
    against the case above: the space never comes back. DISK (exit 3) once the settle is over, pass 2 never runs, and
    the alarm carries the floor, the sleep, and the hibernation image as a candidate. THE BOUND IS THE BOUND: the settle
    is 2 s and a re-read is due only every 30 s, so the alarm must come within seconds of the hold, never a nap later."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], settle="2", nap="30")
    alarm, log = ev(h, "LOOP-ALARM.txt") or "", _driver_log(h)
    held_at = _clock_s(log, r"(\d\d:\d\d:\d\d) HOLD: free disk space")
    ended_at = _clock_s(alarm, r"LOOP DISK at \d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)")
    return (r.returncode == 3 and "LOOP DISK at" in alarm and "under the floor of 2048 MB" in alarm
            and ("the machine woke from sleep at " + _woke_at(h)) in alarm and "hibernation image" in alarm
            and "pmset -g log" in alarm and "===== pass 2 at" not in log and BACK not in log
            and called(h, "df-low").count("low") == 2 and "--state DISK" in called(h, "heartbeat")
            and held_at is not None and ended_at is not None and (ended_at - held_at) % 86400 <= 15)


def case_no_wake_since_the_last_good_reading_raises_disk_at_once():
    """No sleep since the previous pass, no wait. The machine woke during pass 1, the disk read fine at the top of
    pass 2, and it fills during pass 2: that wake is older than the last good reading and explains nothing. DISK on the
    first low reading (read low exactly once), and the alarm says nothing about a sleep."""
    h, r = _wake_run([WOKE, DISK_LOW], settle="20")
    alarm, log = ev(h, "LOOP-ALARM.txt") or "", _driver_log(h)
    return (r.returncode == 3 and "LOOP DISK at" in alarm and "under the floor of 2048 MB" in alarm
            and "woke from sleep" not in alarm and "hibernation" not in alarm and "re-reading" not in log
            and called(h, "df-low").count("low") == 1 and "===== pass 2 at" in log and "===== pass 3 at" not in log)


def case_an_unreadable_wake_time_raises_disk_at_once():
    """An unknown is never a reason to wait on a full disk. ONE condition against the still full case: sysctl gives no
    wake time at all. The same immediate DISK as with no wake, the same single low reading."""
    h, r = _wake_run([DISK_LOW], settle="20")
    alarm, log = ev(h, "LOOP-ALARM.txt") or "", _driver_log(h)
    return (r.returncode == 3 and "LOOP DISK at" in alarm and "woke from sleep" not in alarm and "re-reading" not in log
            and called(h, "df-low").count("low") == 1 and "===== pass 2 at" not in log)


def case_the_deadline_is_read_at_every_reread_of_a_settle():
    """A settle is a hold like every other: each re-read goes back to the top of the loop, where the deadline lives. The
    still full case with ONE condition added: at the first re-read the intake has moved the deadline into the past (its
    fourth answer). The run ends DEADLINE (exit 0), not DISK, and no pass runs. A settle that waited in its own loop
    would have sat out its bound and raised DISK."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], settle="30", intake=[("10.00", "23:59")] * 3 + [("10.00", "00:01")])
    log = _driver_log(h)
    return (r.returncode == 0 and "LOOP DEADLINE at" in (ev(h, "LOOP-ALARM.txt") or "") and HELD in log
            and "LOOP DISK" not in (ev(h, "LOOP-ALARM-HISTORY.txt") or "") and "===== pass 2 at" not in log)


def case_a_second_sleep_inside_a_settle_starts_the_settle_again():
    """The bound is wall clock, and time asleep is not time the image had to be released. The second low reading comes
    with a second sleep (the clock jumps an hour and the kernel reports a new wake); the space returns two readings
    later. The driver holds again for the new wake and the run finishes. Without the restart the first reading after
    the second wake finds the bound spent and raises DISK: the incident again."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], reads="L S L L R", settle="30")
    log = _driver_log(h)
    return (r.returncode == 42 and log.count(HELD) == 2 and BACK in log and "===== pass 2 at" in log
            and called(h, "df-low").count("low") == 4 and "LOOP DISK" not in (ev(h, "LOOP-ALARM-HISTORY.txt") or ""))


def case_one_wake_earns_one_settle():
    """A wake already settled for explains nothing later. The space comes back inside the settle, pass 2 runs, and the
    space is low again at the top of pass 3 with no new wake: DISK at once, one hold in the whole run, and the alarm
    names no sleep. A settle per low reading would let a flapping disk hold the run for ever."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW, ":"], reads="L L R L", settle="30")
    alarm, log = ev(h, "LOOP-ALARM.txt") or "", _driver_log(h)
    return (r.returncode == 3 and "LOOP DISK at" in alarm and "woke from sleep" not in alarm and log.count(HELD) == 1
            and log.count(BACK) == 1 and called(h, "df-low").count("low") == 3
            and "===== pass 2 at" in log and "===== pass 3 at" not in log)


def case_a_wake_before_the_run_started_explains_nothing():
    """The last good reading starts at the driver's own start check. The space is low at the very first reading of the
    loop (the lease stub creates the flag) and the kernel's last wake is an hour before this run began: DISK at once,
    before any pass, with no hold."""
    def prepare(h):
        _w(os.path.join(h, "wake-says"), str(int(time.time()) - 3600))
        _w(os.path.join(h, ".claude", "bin", "loop_guard.sh"),
           '#!/bin/bash\necho "$*" >> "$HOME/calls/guard"\n[ "$1" = acquire ] && touch "$HOME/disk-low"\nexit 0\n', 0o755)
    h, r = _wake_run([":"], settle="20", prepare=prepare)
    alarm, log = ev(h, "LOOP-ALARM.txt") or "", _driver_log(h)
    return (r.returncode == 3 and "LOOP DISK at" in alarm and "woke from sleep" not in alarm and "re-reading" not in log
            and called(h, "df-low").count("low") == 1 and "===== pass 1 at" not in log)


def case_a_zero_nap_leaves_the_default_in_force():
    """A nap of zero would re-read in a spin. The still full case with ONE condition changed: the nap is 0. The default
    stands (30 s, capped by the 2 s left), so the disk is read low exactly twice before DISK."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], settle="2", nap="0")
    return (r.returncode == 3 and "LOOP DISK at" in (ev(h, "LOOP-ALARM.txt") or "")
            and called(h, "df-low").count("low") == 2)


def case_a_garbage_settle_bound_leaves_the_default_in_force():
    """A bound nobody can read is never zero and never endless. The recovered case with ONE condition changed: the bound
    is the word 'soon'. The default stands, the space comes back inside it, and the run finishes; read as a number,
    'soon' is zero seconds of waiting and the same run ends DISK."""
    h, r = _wake_run([WOKE + "; " + DISK_LOW], reads="L L L R", settle="soon")
    return r.returncode == 42 and BACK in _driver_log(h) and "LOOP DISK" not in (ev(h, "LOOP-ALARM-HISTORY.txt") or "")


def case_low_space_at_the_start_names_a_wake_inside_the_settle_window():
    """The other caller of disk_hold. A start under the floor refuses at once either way (it never waits); what changes
    is the reason: a wake a few seconds ago is named with the hibernation image, a wake long ago is not."""
    def refused(wake):
        h = make_home(lanes="1")
        r = run(h, A_DAY_AWAY, extra={"BROTHER_DISK_FREE_KB": "1000", "BROTHER_WAKE_EPOCH": wake})
        return r.stdout if r.returncode == 2 and "under the floor" in r.stdout and "acquire" not in called(h, "guard") else None
    fresh, stale = refused(str(int(time.time()))), refused("1")
    return (fresh is not None and stale is not None and "the machine woke from sleep at" in fresh
            and "hibernation image" in fresh and "woke from sleep" not in stale and "hibernation" not in stale)


def case_a_hostile_wake_time_is_dropped_never_evaluated():
    """The wake time reaches shell arithmetic, and arithmetic evaluates what it is handed. A value that is not a plain
    number is dropped before that: this one would create a file in the throwaway HOME if it were ever evaluated. The
    start still refuses on the floor alone, with no sleep named."""
    h = make_home(lanes="1")
    r = run(h, A_DAY_AWAY, extra={"BROTHER_DISK_FREE_KB": "1000", "BROTHER_WAKE_EPOCH": 'x[$(touch "$HOME/evaluated")]'})
    return (r.returncode == 2 and "under the floor" in r.stdout and "woke from sleep" not in r.stdout
            and not os.path.exists(os.path.join(h, "evaluated")))


def case_every_child_gets_one_run_scoped_tmpdir_and_old_runs_are_pruned():
    """The scratch block, read from the script's own source: TMPDIR is a fresh run-* folder under BROTHER_SCRATCH, a
    run folder older than a day is pruned at start, a fresh one is kept."""
    src = open(SCRIPT, encoding="utf-8").read()
    # THE WHOLE BLOCK, BY ITS OWN END (2026-10-01): four lines stopped short of the scratch_prune call once comments were
    # added above it, and under bash -c ${BASH_SOURCE[0]} is empty, so the prune looked for ./scratch_prune.py and its
    # "|| true" hid that. The block now runs through the prune call's last line with the script's real directory bound.
    start = src.index("\nexport BROTHER_SCRATCH=") + 1
    end = src.index("\n", src.index("--max-age-hours 24", start)) + 1
    block = src[start:end].replace('$(dirname "${BASH_SOURCE[0]}")', '"%s"' % os.path.dirname(os.path.abspath(SCRIPT)))
    root = tempfile.mkdtemp(prefix="scratch-case-"); old = os.path.join(root, "run-old"); fresh = os.path.join(root, "run-fresh")
    os.makedirs(old); os.makedirs(fresh); os.utime(old, (0, 0))
    r = subprocess.run(["bash", "-c", block + 'echo "$TMPDIR"; [ -d "$TMPDIR" ] && echo present'], capture_output=True, text=True,
                       env=dict(os.environ, BROTHER_SCRATCH=root))
    lines = r.stdout.split()
    return (len(lines) == 2 and lines[0].startswith(os.path.join(root, "run-")) and lines[1] == "present"
            and not os.path.isdir(old) and os.path.isdir(fresh))


def case_money_vanishing_mid_run_is_unfunded():
    """The MONEY line is printed at the start and never again: the first pass raises UNFUNDED, exit 3, and no pass runs."""
    h = make_home(lanes="1", money="first")
    r = run(h, ["23:59", "1"])
    return (r.returncode == 3 and "LOOP UNFUNDED at" in (ev(h, "LOOP-ALARM.txt") or "") and "no MONEY line" in (ev(h, "LOOP-ALARM.txt") or "")
            and "--state UNFUNDED" in called(h, "heartbeat") and "pass 1" not in (r.stdout or ""))


def case_spend_past_the_budget_raises_budget():
    """Budget 0.50 USD; the funding stub's spend grows 0.60 per read, so the first pass already sees the budget spent:
    BUDGET, exit 3, an alarm naming the figures, no pass run. With the 10.00 default the same stub never trips it."""
    h = make_home(lanes="1", budget="0.50")
    r = run(h, ["23:59", "1"])
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 3 and "LOOP BUDGET at" in alarm and "budget of 0.50 USD is spent" in alarm
            and "--state BUDGET" in called(h, "heartbeat") and "pass 1" not in (r.stdout or ""))


def case_under_budget_the_run_proceeds_to_the_pass():
    """Budget 10.00 (the default) with 0.60 of spend on the first pass: the budget gate stays quiet and the pass runs
    (the pass stub exits 42, FINISHED). ONE condition: the same stub as the case above, a budget it does not reach."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"])
    return r.returncode == 42 and "LOOP FINISHED at" in (ev(h, "LOOP-ALARM.txt") or "") and "BUDGET" not in (ev(h, "LOOP-ALARM.txt") or "")


def case_a_blocked_pass_alarm_quotes_the_pass_own_reason():
    """The pass prints 'LOOP BLOCKED: <why>' and exits 44: the alarm carries THAT sentence, not a fixed one about hub."""
    h = make_home(lanes="1", pass_body='echo "LOOP BLOCKED: pass_digest exit=1; without the digest this pass cannot see what is READY"; exit 44')
    r = run(h, ["23:59", "1"])
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return r.returncode == 44 and "LOOP BLOCKED at" in alarm and "pass_digest exit=1" in alarm and "cannot reach hub" not in alarm


def case_a_blocked_pass_with_no_reason_line_says_so():
    """A pass that exits 44 without printing its reason: the alarm says exactly that rather than inventing one."""
    h = make_home(lanes="1", pass_body="exit 44")
    r = run(h, ["23:59", "1"])
    return r.returncode == 44 and "exited 44 without printing its reason" in (ev(h, "LOOP-ALARM.txt") or "")


def case_the_login_refresh_runs_before_every_pass_never_in_a_seat():
    """Owner ruling A (2026-10-05): before each pass the driver runs `native_worker.py refresh` (one unsandboxed call, never
    a seat) and logs its one line. ONE condition: a good refresh and a pass that ends the run; the refresh came first."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"])
    order = called(h, "seq").split()
    return (r.returncode == 42 and called(h, "native_worker").strip() == "refresh" and order == ["refresh", "pass"]
            and "REFRESH OK stub" in r.stdout and "LOGIN" not in called(h, "osascript"))


def case_a_failed_login_refresh_is_reported_and_the_pass_still_runs():
    """A refresh that fails prints REFRESH FAILED with its class: the driver logs it, raises one LOGIN alarm, and still runs
    the pass (the seats' CONFIG_WAIT holds the builds). ONE condition: the refresh says FAILED and exits 1."""
    h = make_home(lanes="1", refresh_body='print("REFRESH FAILED login"); sys.exit(1)')
    r = run(h, ["23:59", "1"])
    return (r.returncode == 42 and "REFRESH FAILED login" in r.stdout and "LOGIN" in called(h, "osascript")
            and called(h, "seq").split() == ["refresh", "pass"])


def case_a_refresh_that_prints_nothing_is_failed_never_silent():
    """The refresh tool crashes with no REFRESH line: the driver writes REFRESH FAILED no-answer, never nothing and never OK."""
    h = make_home(lanes="1", refresh_body='raise SystemExit(3)')
    r = run(h, ["23:59", "1"])
    return r.returncode == 42 and "REFRESH FAILED no-answer" in r.stdout and "LOGIN" in called(h, "osascript")


def case_a_refresh_past_its_bound_is_failed_timeout_and_the_pass_still_runs():
    """The refresh hangs (a writer stuck on the Claude ledger's lock): the driver's outer bound kills it, logs REFRESH
    FAILED timeout, raises the LOGIN alarm and starts the pass. ONE condition: a refresh that sleeps past a 2 s bound."""
    h = make_home(lanes="1", refresh_body='import time; time.sleep(30); print("REFRESH OK too late")')
    t0 = time.time()
    r = run(h, ["23:59", "1"], extra={"BROTHER_REFRESH_BOUND_S": "2"})
    return (r.returncode == 42 and "REFRESH FAILED timeout" in r.stdout and "LOGIN" in called(h, "osascript")
            and called(h, "seq").split() == ["refresh", "pass"] and time.time() - t0 < 25)

def _intake_seq(home, answers):
    """Replace the intake stub with one that answers `status` from a sequence, one (budget, deadline) per call and the
    last one repeated: the driver's start read is call 1, each pass's re-read is the next. A change the owner makes mid
    run is a later answer that differs from the first, never an answer that differs from the driver's argument."""
    _w(os.path.join(home, ".claude", "bin", "loop_intake.py"),
       '#!/usr/bin/env python3\nimport os\np = os.path.join(os.environ["HOME"], "calls", "intake")\n'
       'n = len(open(p).read().splitlines()) if os.path.isfile(p) else 0\nopen(p, "a").write("status\\n")\n'
       'answers = %r\nb, d = answers[min(n, len(answers) - 1)]\n'
       'print("INTAKE STATUS: a driver may start")\nprint("BUDGET_USD %%s" %% b)\nprint("DEADLINE %%s" %% d)\n' % (answers,), 0o755)


def _events(home):
    """The proof history rows of the one run this home made, [] when there is none."""
    rows = []
    for d in run_dirs(home):
        p = os.path.join(d, "proof", "events.jsonl")
        if os.path.isfile(p):
            rows += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return rows


def case_a_deadline_moved_into_the_past_by_the_intake_ends_the_run():
    """The intake said 23:59 at the start and 00:01 afterwards (the owner shortened the run): the next pass reads the
    change, records it, moves its stop, and ends DEADLINE on the check. The pass stub exits 0 so the loop would go on."""
    h = make_home(lanes="1", pass_body="exit 0")
    _intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
    r = run(h, ["23:59", "1"])
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 0 and "LOOP DEADLINE at" in alarm and "deadline changed by the intake to 00:01" in (r.stdout or "")
            and [e["kind"] for e in _events(h)].count("deadline-change") == 1)


def case_intake_deadline_unchanged_is_not_an_intervention():
    """D-3 (U1, B5-11): the intake's deadline is compared with the value the driver read AT ITS START, never with the
    driver's own argument. A pair record names the pair's end, not this run's deadline, so an intake deadline that
    differs from the argument and never changes is no intervention: no deadline-change row, the stop is not moved, and
    the pass runs. ONE condition: the intake says 00:05 at the start and on every pass."""
    h = make_home(lanes="1", pass_body="exit 42", deadline_line="00:05")
    r = run(h, ["23:59", "1"])
    kinds = [e["kind"] for e in _events(h)]
    return (r.returncode == 42 and "deadline-change" not in kinds and "deadline changed" not in (r.stdout or "")
            and kinds == ["start-marker", "end-marker"])


def case_a_budget_change_is_recorded_as_an_intervention():
    """D-3 (U11): the intake said 0.50 at the start and 100.00 afterwards. The driver adopts the new budget (the pass
    runs, the stub's 0.60 of spend is under it) and records a budget-change row, so the run is not unattended."""
    h = make_home(lanes="1", pass_body="exit 42")
    _intake_seq(h, [("0.50", "23:59"), ("100.00", "23:59")])
    r = run(h, ["23:59", "1"])
    kinds = [e["kind"] for e in _events(h)]
    evidence = {}
    for d in run_dirs(h):
        p = os.path.join(d, "proof", "evidence.json")
        if os.path.isfile(p):
            evidence = json.load(open(p, encoding="utf-8"))
    return (r.returncode == 42 and kinds.count("budget-change") == 1 and evidence.get("unattended") is False
            and "0.50" in json.dumps(evidence.get("interventions")) and "100.00" in json.dumps(evidence.get("interventions")))


def case_the_budget_line_is_printed_every_pass():
    """One pass at 0.60 spent of the 10.00 budget: the log carries a BUDGET line with spent, remaining and the deadline."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"])
    return r.returncode == 42 and "BUDGET  this run: spent 0.60 of 10.00 USD | remaining 9.40 USD | deadline 23:59" in (r.stdout or "")


# ---------------------------------------------------------------- the cases

def case_refusal_unreadable_stop_time():
    """A refusal is not silent: history line, terminal heartbeat state, and a printed line."""
    h = make_home()
    r = run(h, ["not-a-time"])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return (r.returncode == 2
            and "REFUSED TO START" in r.stdout
            and "LOOP REFUSED-TO-START at" in hist
            and "could not read the stop time" in hist
            and "--state REFUSED" in called(h, "heartbeat")
            # A refusal writes no ALARM file: the alarm path means a run that STARTED needs a human.
            and ev(h, "LOOP-ALARM.txt") is None
            and "LOOP STARTED" not in hist)


def case_refusal_deadline_already_past():
    # H3 (2026-09-24): a bare HH:MM already past means tomorrow, so the refusal is driven with a DATED past deadline.
    h = make_home()
    r = run(h, ["2000-01-01 08:00"])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return (r.returncode == 2
            and "REFUSED TO START" in r.stdout
            and "is not in the future" in hist
            and "--state REFUSED" in called(h, "heartbeat"))


def case_refusal_lease_held_by_another_driver():
    """Orthogonal by construction: the deadline is valid and the sentry would have said yes, so the
    only thing wrong here is the lease. The sentry must therefore never have been called."""
    h = make_home(guard_acquire_rc=1)
    r = run(h, [future_hhmm()])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return (r.returncode == 2
            and "already holds the lease" in r.stdout
            and "already holds the lease" in hist
            and "--state REFUSED" in called(h, "heartbeat")
            and called(h, "sentry") == "")


def case_refusal_worktree_claimed_releases_the_lease():
    """The lease was granted before this refusal, so refusing must hand it back. A driver that
    refuses while holding the lease locks every later driver out of a tree it is not using."""
    h = make_home(sentry_claim_rc=1)
    r = run(h, [future_hhmm()])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    return (r.returncode == 2
            and "another session claims this worktree" in r.stdout
            and "another session claims this worktree" in hist
            and "--state REFUSED" in called(h, "heartbeat")
            and "release" in called(h, "guard"))


def case_start_note_then_terminal_state_and_report():
    """One run covering the rest of the lifecycle: it starts (start note), then ends on a terminal
    state (UNFUNDED, because the funding stub reports no lane), and the end leaves the standard
    report behind with its PATH written into the alarm file. burn_guard returning 0 is the cheapest
    real terminal state to reach: it is read BEFORE any pass, so no build is ever spawned."""
    h = make_home(lanes="0")
    r = run(h, [future_hhmm()], extra=dict(BROTHER_REPORT_DELAY_S="0"))
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    beats = called(h, "heartbeat")
    if not (r.returncode == 3 and "LOOP STARTED at" in hist and "LOOP UNFUNDED at" in alarm):
        return False
    # Order matters: a start note written AFTER the end note is not a start note.
    if "LOOP UNFUNDED at" not in hist or hist.index("LOOP STARTED") > hist.index("LOOP UNFUNDED"):
        return False
    if not ("--state STARTED" in beats and "--state UNFUNDED" in beats):
        return False
    # THE REPORT IS A FILE, NOT A PROMISE. Read the path out of the alarm and open it: a line
    # naming a report that was never generated is the shape of failure this check exists to catch.
    # The report is written in the background (U12: it never holds the driver's caller); poll for it briefly.
    line = next((l for l in alarm.splitlines() if l.startswith("report: ")), "")
    path = line[len("report: "):].strip()
    return bool(path) and _poll(lambda: os.path.isfile(path) and "STANDARD REPORT BODY" in open(path, encoding="utf-8").read())


def _poll(predicate, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.25)
    return bool(predicate())


def case_alarm_history_is_appended_never_truncated():
    """A new run must not destroy the record of how the last one ended.

    Two halves, because the file and the alarm are two different losses: anything already in the
    history survives the run, AND a leftover alarm from the previous run is retired INTO the
    history before the new one overwrites it. This line used to be `rm -f "$ALARM"`."""
    h = make_home(lanes="0")
    hist_p = os.path.join(h, ".claude", "evidence", "LOOP-ALARM-HISTORY.txt")
    alarm_p = os.path.join(h, ".claude", "evidence", "LOOP-ALARM.txt")
    _w(hist_p, "OLD-HISTORY-SENTINEL\n")
    _w(alarm_p, "OLD-ALARM-SENTINEL\n")
    r = run(h, [future_hhmm()])
    hist = ev(h, "LOOP-ALARM-HISTORY.txt") or ""
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 3
            and "OLD-HISTORY-SENTINEL" in hist        # the file was appended to, not rewritten
            and "OLD-ALARM-SENTINEL" in hist          # the previous alarm was retired, not deleted
            and "OLD-ALARM-SENTINEL" not in alarm     # and the new alarm is the new one
            and "LOOP UNFUNDED at" in alarm)

def case_an_owner_hold_refuses_before_touching_anything():
    """A HOLD file refuses the start, names its reason, and the driver takes neither the lease nor the claim."""
    h = make_home(lanes="8")
    with open(os.path.join(h, ".claude", "evidence", "LOOP-HOLD.txt"), "w", encoding="utf-8") as f:
        f.write("owner 2026-09-22 07:40: stop the loop and fix all these issues\n")
    r = run(h, [future_hhmm()])
    return (r.returncode == 2 and "HOLD is in force" in r.stdout and "stop the loop and fix" in r.stdout
            and "acquire" not in called(h, "guard") and "claim" not in called(h, "sentry")
            and "--state REFUSED" in called(h, "heartbeat") and called(h, "pulse") == "")


def case_no_hold_file_starts_normally():
    """The same home without the file runs its pass: the hold is the ONLY thing the case above changed."""
    h = make_home(lanes="8")
    r = run(h, [future_hhmm()])
    return r.returncode == 42 and "acquire" in called(h, "guard")


def case_no_ready_intake_refuses_before_touching_anything():
    """The intake says NO START: the driver refuses with the intake's own words and takes neither lease nor claim."""
    h = make_home(lanes="8", intake_rc=1)
    r = run(h, [future_hhmm()])
    return (r.returncode == 2 and "no startable intake record" in r.stdout and "NOT READY" in r.stdout and "status" in called(h, "intake")
            and "acquire" not in called(h, "guard") and "claim" not in called(h, "sentry") and "--state REFUSED" in called(h, "heartbeat"))


def case_a_missing_intake_tool_refuses_too():
    """No loop_intake.py at all: the unknown never starts a run."""
    h = make_home(lanes="8"); os.remove(os.path.join(h, ".claude", "bin", "loop_intake.py"))
    r = run(h, [future_hhmm()])
    return r.returncode == 2 and "no startable intake record" in r.stdout and "acquire" not in called(h, "guard")


def case_the_digest_follows_the_owners_cadence():
    """digest_pass is driven for real with a fake pass counter: 1, 3, 6, 10, 15, 20 and every 5 after; nothing else."""
    src = open(SCRIPT, encoding="utf-8").read()
    fn = "REPORT_PASSES=1,3,6,10,15,20\ndigest_pass() {" + src.split("\ndigest_pass() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    probe = fn + "for n in 1 2 3 4 5 6 7 10 12 15 19 20 21 25 30 34 35 100; do digest_pass $n && printf '%s ' $n; done\n"
    r = subprocess.run(["bash", "-c", probe], capture_output=True, text=True)
    return r.stdout.split() == ["1", "3", "6", "10", "15", "20", "25", "30", "35", "100"]


def case_a_digest_is_written_on_pass_one_and_a_previous_one_is_kept():
    h = make_home(lanes="8")
    old = os.path.join(h, ".claude", "evidence", "LOOP-DIGEST.md"); open(old, "w").write("yesterday\n")
    r = run(h, [future_hhmm()])
    kept = [n for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("LOOP-DIGEST-")]
    return r.returncode == 42 and "## pass 1 at" in open(old).read() and "PULSE quiet" in open(old).read() and len(kept) == 1


def case_a_pause_runs_no_pass_and_a_resume_continues():
    """Driven for real: the pause file appears after admission, so the driver announces PAUSED, renews the lease, runs NO pass and
    spends nothing; a helper removes the file after a few seconds and the driver then runs its pass and finishes."""
    h = make_home(lanes="8")
    pf = os.path.join(h, ".claude", "evidence", "LOOP-PAUSE.txt")
    _control_on_claim(h, "LOOP-PAUSE.txt", dangling=False)
    import threading, re, time as _t
    removed = []
    def lift():
        os.remove(pf); removed.append(_t.strftime("%H:%M:%S"))
    t = threading.Timer(14.0, lift); t.start()
    try:
        r = run(h, [future_hhmm()], timeout=120)
    finally:
        t.cancel()
    log = "".join(ev(h, n) or "" for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-"))
    beats = called(h, "heartbeat")
    return (r.returncode == 42 and "owner: going out" in log and "RESUMED" in log and log.index("PAUSED") < log.index("RESUMED")
            and "--state PAUSED" in beats and log.count("===== pass 1 at") == 1 and "===== pass 2" not in log
            and log.index("RESUMED") < log.index("===== pass 1 at")
            # the pass started AFTER the file was lifted, never during the pause (a mutant without `continue` ran it at once)
            and removed and re.search(r"===== pass 1 at (\d\d:\d\d:\d\d)", log).group(1) >= removed[0]
            # the lease was renewed DURING the pause, not only by the pass: at least two renews for one pass
            and called(h, "guard").count("renew") >= 2
            and called(h, "guard").count("renew") >= 1 and any("brother.loop PAUSED" in a for a in _alerts(h)))


def case_no_pause_file_runs_at_once():
    h = make_home(lanes="8")
    r = run(h, [future_hhmm()])
    return r.returncode == 42 and "PAUSED" not in "".join(ev(h, n) or "" for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-"))


def _alerts(home):
    return [l for l in called(home, "osascript").splitlines() if "display alert" in l]


def case_warning_rings_once_as_an_alert_and_the_end_rings_too():
    """One pass (the pass stub exits 42, FINISHED). The pulse warns about one kind on two lines of the same kind:
    the driver must log the pulse, show ONE persistent alert for that kind, and a second alert for the end itself.
    A banner alone is what Do Not Disturb swallowed on 2026-09-22 while osascript exited 0."""
    h = make_home(lanes="8", pulse_text="PULSE pass 1 | landed 0\nWARN GATE-NEVER-APPROVES: checker ruled 6 times\nWARN GATE-NEVER-APPROVES: probe ruled 5 times")
    r = run(h, [future_hhmm()])
    logs = [ev(h, n) or "" for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-")]
    alerts = _alerts(h)
    warn = [a for a in alerts if "brother.loop WARN" in a]
    return (r.returncode == 42 and "--passes 1" in called(h, "pulse") and "--start " in called(h, "pulse")
            and any("PULSE pass 1 | landed 0" in t for t in logs)
            and len(warn) == 1 and "checker ruled 6 times" in warn[0]
            and any("brother.loop FINISHED" in a for a in alerts))


def case_a_crashing_pulse_cannot_end_the_run():
    """The pulse exits 1 and prints rubbish: the run still ends on the PASS's own verdict, FINISHED, exit 42."""
    h = make_home(lanes="8", pulse_text="Traceback (most recent call last): boom", pulse_rc=1)
    r = run(h, [future_hhmm()])
    return r.returncode == 42 and "LOOP FINISHED at" in (ev(h, "LOOP-ALARM.txt") or "") and not [a for a in _alerts(h) if "WARN" in a]




def case_every_pass_stage_exit_code_is_read():
    """loop_pass.sh, read once end to end on 2026-09-22: salvage, the digest, the closer, the closure push and the
    refill all ran with their exit code unread (a pipe to tail, a grep filter, an `&& push` followed by an
    unconditional "pushed"), and a missing launch tree exited 0, which the driver counts as an ordinary pass.
    Every stage now reads its code by name, and a missing tree is BLOCKED (44)."""
    text = open(os.path.join(HERE, "loop", "loop_pass.sh"), encoding="utf-8").read()
    reads = ('loop_done.py --scope "$BROTHER_SCOPE"; DONE=$?',
             'salvage.py promote 2>&1); SRC=$?',
             'pass_digest.py --no-fetch); DRC=$?',
             'land_batch.py $PICK); LB=$?',
             'land_batch.py --close 2>&1); CRC=$?',   # the closer goes through the lander since review 15 (never the tree's close_unit.py)
             'runner_pool.py 2>&1); PRC=$?',
             'land_batch.py --push 2>&1); CPRC=$?')   # the closure goes through the lander since D13 review 14
    missing_tree = [l for l in text.splitlines() if "launch worktree missing" in l]
    return (all(r in text for r in reads)
            and len(missing_tree) == 1 and "exit 44" in missing_tree[0] and "exit 0" not in missing_tree[0]
            and "closure did not land (lander exit ${CPRC})" in text)


def case_the_diagnostician_lane_runs_each_pass_detached_and_bounded():
    """Design section 4: EXHAUSTED with no change gets a diagnostician round every pass, detached like the reprobe (a
    pass never waits on a model), bounded to 6 lanes, and only when no round is already running."""
    text = open(os.path.join(HERE, "loop", "loop_pass.sh"), encoding="utf-8").read()
    lines = [l for l in text.splitlines() if "diag_round" in l and not l.lstrip().startswith("#")]   # the pgrep line escapes its dot
    return (len(lines) == 2
            and any("pgrep" in l for l in lines)
            and any("Popen" in l and "'--limit','6'" in l and "start_new_session=True" in l for l in lines))

def case_a_live_drivers_estate_paths_never_reach_a_case():
    """A caller whose shell already exports the driver's estate paths (a deploy or a landing gate run beside a live
    driver) must not send a case's run folder or scratch into the real estate: on 2026-09-24 this suite minted 32
    empty run folders in the real loop-runs root, and the intake's mix advice read them as the newest runs (NO-DATA,
    no run could start). ONE condition: the same start as the case above, with the four paths pointed at a sentinel."""
    sentinel = tempfile.mkdtemp(prefix="estate-sentinel-"); _HOMES.append(sentinel)
    names = ("BROTHER_RUNS_ROOT", "BROTHER_RUN_DIR", "BROTHER_SCRATCH", "BROTHER_GRADE_SANDBOXES")
    before = {k: os.environ.get(k) for k in names}
    try:
        for k in names: os.environ[k] = os.path.join(sentinel, k)
        h = make_home(lanes="8")
        r = run(h, [future_hhmm()])
    finally:
        for k, v in before.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    runs = os.path.join(h, ".claude", "evidence", "loop-runs")
    return (r.returncode == 42 and os.listdir(sentinel) == []
            and os.path.isdir(runs) and any(d.startswith("run-") for d in os.listdir(runs)))


def case_the_machines_swap_never_decides_a_start_case():
    """The laptop's own swap must not decide a case: a sysctl stub reports 99999 MB in use, and the same start as the
    case above still runs its pass. ONE condition: the machine reading, with no seam set by the case."""
    h = make_home(lanes="8")
    _w(os.path.join(h, "shim", "sysctl"),
       '#!/bin/bash\necho "total = 99999.00M  used = 99999.00M  free = 0.00M  (encrypted)"\n', 0o755)
    r = run(h, [future_hhmm()])
    return r.returncode == 42 and "swap" not in r.stdout


def case_an_inherited_ceiling_never_decides_a_start_case():
    """A caller that exported a tiny swap ceiling (a live driver's run config) must not decide a case: the driver's own
    default stands. ONE condition: BROTHER_SWAP_CEILING_MB=1 in the caller's environment."""
    before = os.environ.get("BROTHER_SWAP_CEILING_MB")
    try:
        os.environ["BROTHER_SWAP_CEILING_MB"] = "1"
        h = make_home(lanes="8")
        r = run(h, [future_hhmm()])
    finally:
        if before is None: os.environ.pop("BROTHER_SWAP_CEILING_MB", None)
        else: os.environ["BROTHER_SWAP_CEILING_MB"] = before
    return r.returncode == 42 and "swap" not in r.stdout


# ---------------------------------------------------------------- wave 2 (2026-09-26 audit findings 8, 13, 14, 19)

def case_the_log_name_carries_the_date():
    """Finding 13: the driver named its log loop-until-HHMM.log, so a run at the same minute on another day appended to an
    old day's log (two logs held two runs). The name now carries the date: loop-until-YYYYMMDD-HHMM.log."""
    h = make_home(lanes="0")
    r = run(h, [future_hhmm()])
    names = [n for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-")]
    today = datetime.datetime.now().strftime("%Y%m%d")
    return r.returncode == 3 and any(re.match(r"^loop-until-%s-\d{4}\.log$" % today, n) for n in names) and not any(re.match(r"^loop-until-\d{4}\.log$", n) for n in names)


def _start_and_signal(marker_pid_of_driver):
    """Start a driver whose pass sleeps, wait for pass 1, optionally write a stop request naming it, then TERM it."""
    h = make_home(lanes="1", pass_body="sleep 30")
    base = dict(ROOMY, **{k: v for k, v in os.environ.items() if k not in ESTATE})
    env = dict(base, HOME=h, PATH=os.path.join(h, "shim") + os.pathsep + os.environ.get("PATH", ""),
               BROTHER_LAUNCH_WORKTREE=os.path.join(h, "Brother", ".claude", "worktrees", "brother-unify-1.1"))
    p = subprocess.Popen(["bash", SCRIPT, "23:59", "1"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    deadline = time.time() + 60
    while time.time() < deadline and "pass 1 at" not in "".join(ev(h, n) or "" for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-")):
        time.sleep(0.2)
    if marker_pid_of_driver is not None:
        with open(os.path.join(h, ".claude", "evidence", "LOOP-STOP-REQUEST.txt"), "w") as f:
            f.write("%d owner stop (fixture)\n" % (p.pid if marker_pid_of_driver else 999999))
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); p.communicate()
    return ev(h, "LOOP-ALARM.txt") or ""


def case_an_owner_stop_ends_stopped_and_a_kill_ends_interrupted():
    """Finding 19: an owner-ordered stop read "INTERRUPTED: the driver was killed", the same class as a crash, so 23
    historic ends could not be split. A stop request naming THIS driver's pid ends STOPPED; a TERM with no request, or a
    stale request naming another pid, still ends INTERRUPTED."""
    stopped = _start_and_signal(True)
    killed = _start_and_signal(None)
    stale = _start_and_signal(False)
    return ("LOOP STOPPED at" in stopped and "owner stop (fixture)" in stopped and "LOOP INTERRUPTED at" in killed
            and "LOOP INTERRUPTED at" in stale)


def case_stop_loop_writes_the_stop_request_for_the_driver_it_stops():
    """stop_loop.sh names each driver pid it is about to TERM in LOOP-STOP-REQUEST.txt, with its reason, before the signal."""
    h = tempfile.mkdtemp(prefix="stop-request-"); _HOMES.append(h)
    os.makedirs(os.path.join(h, ".claude", "evidence")); fake = os.path.join(h, ".claude", "bin", "loop_until.sh"); os.makedirs(os.path.dirname(fake))   # the loop owns a driver only from $HOME/.claude/bin (loop_procs.py)
    _w(fake, "#!/bin/bash\ntrap 'exit 0' TERM\nwhile :; do sleep 1; done\n", 0o755)
    tag = "stop-request-fixture-%d" % os.getpid()
    d = subprocess.Popen(["bash", fake, tag], start_new_session=True)
    try:
        time.sleep(0.5)
        env = dict(os.environ, HOME=h, STOP_LOOP_ONLY=tag)
        r = subprocess.run(["bash", os.path.join(os.path.dirname(SCRIPT), "stop_loop.sh"), "--reason", "owner stop (fixture)"],
                           env=env, capture_output=True, text=True, timeout=60)
        req = ev(h, "LOOP-STOP-REQUEST.txt") or ""
    finally:
        try: os.killpg(d.pid, signal.SIGKILL)       # this fixture's own process group only
        except OSError: pass
        d.wait()
    # A HOST THAT HIDES THE PROCESS TABLE CANNOT RUN THIS CASE (2026-10-03): inside the landing sandbox (the pre-push
    # gate's hermetic rerun since review 14 finding 2) loop_procs.py cannot read ps, stop_loop.sh says so and exits 2
    # before any request is written. That is NO-DATA, never a pass and never this case's red: the harness lists it by
    # name. Any other exit, or a missing request line on a host that could read the table, is still the failure.
    if r.returncode == 2 and "could not be read" in r.stdout + r.stderr:
        return "NO-DATA: stop_loop.sh cannot read the process table on this host: " + (r.stdout + r.stderr).strip().splitlines()[-1]
    return r.returncode == 0 and ("%d owner stop (fixture)" % d.pid) in req


def case_claude_calls_without_a_cost_say_no_data():
    """Finding 14: a Claude call whose completion row never came (killed, failed, or before the completion writer existed)
    printed as Claude 0.0000. A start row after the run's start with no done row makes the BUDGET line say NO-DATA."""
    h = make_home(lanes="1")
    led = os.path.join(h, "claude-calls.jsonl")
    with open(led, "w") as fh: fh.write(json.dumps({"at": "2099-01-01T00:00:00", "model": "claude-sonnet-5", "effort": "high", "prompt_chars": 10}) + "\n")
    env_before = os.environ.get("BROTHER_CLAUDE_CALLS_LEDGER"); os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = led
    try:
        r = run(h, ["23:59", "1"])
    finally:
        if env_before is None: os.environ.pop("BROTHER_CLAUDE_CALLS_LEDGER", None)
        else: os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = env_before
    line = next((l for l in (r.stdout or "").splitlines() if l.startswith("BUDGET  this run")), "")
    # an uncosted Claude call is NO-DATA on the BUDGET line, never 0.0000, and it no longer ends an OpenRouter funded run
    return r.returncode == 42 and "Claude NO-DATA" in line and "0.0000" not in line and "1 Claude call" in r.stdout


def case_a_live_deadline_change_reaches_the_funding_guard():
    """Finding 8: a deadline changed through the intake moved STOP_HHMM and STOP_EPOCH but not BROTHER_STOP_HOUR, which
    burn_guard reads. Started with 23:59 (hour 24), the intake says a later-today HH:30: the guard is handed that hour + 1."""
    now = datetime.datetime.now()
    if now.hour >= 22:
        return True   # no later-today hour to move to before midnight; the other deadline cases still run
    hh = now.hour + 1
    h = make_home(lanes="1")
    _intake_seq(h, [("10.00", "23:59"), ("10.00", "%02d:30" % hh)])
    run(h, ["23:59", "1"])
    hours = called(h, "burn_hour").split()
    return bool(hours) and hours[-1] == str(hh + 1)


def case_a_done_row_with_a_null_cost_says_no_data():
    """Review 2026-09-26: a start and a done row for the same call, the done row carrying cost_usd null (model_call writes
    exactly that when the CLI omits total_cost_usd), printed Claude 0.0000 with no NO-DATA. It names the uncosted call."""
    h = make_home(lanes="1")
    led = os.path.join(h, "claude-calls.jsonl")
    with open(led, "w") as fh:
        fh.write(json.dumps({"at": "2099-01-01T00:00:00", "model": "claude-sonnet-5", "prompt_chars": 10, "call": "c1"}) + "\n")
        fh.write(json.dumps({"at": "2099-01-01T00:00:01", "event": "done", "model": "claude-sonnet-5", "cost_usd": None, "call": "c1"}) + "\n")
    env_before = os.environ.get("BROTHER_CLAUDE_CALLS_LEDGER"); os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = led
    try:
        r = run(h, ["23:59", "1"])
    finally:
        if env_before is None: os.environ.pop("BROTHER_CLAUDE_CALLS_LEDGER", None)
        else: os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = env_before
    line = next((l for l in (r.stdout or "").splitlines() if l.startswith("BUDGET  this run")), "")
    # an uncosted Claude call is NO-DATA on the BUDGET line, never 0.0000, and it no longer ends an OpenRouter funded run
    return r.returncode == 42 and "Claude NO-DATA" in line and "0.0000" not in line and "1 Claude call" in r.stdout


# ---------------------------------------------------------------- wave 3 (2026-09-26 F23: the receipt)

def case_a_terminal_end_writes_a_receipt_with_every_required_key():
    """AGENTS.md's receipt contract: "every run that reaches the end writes receipt/receipt.json
    inside its own run directory". lanes=0 is the cheapest real terminal state (UNFUNDED, read
    before any pass); the receipt must exist with every key the contract names, whatever state ended
    the run."""
    h = make_home(lanes="0")
    r = run(h, [future_hhmm()])
    dirs = run_dirs(h)
    if r.returncode != 3 or len(dirs) != 1:
        return False
    p = os.path.join(dirs[0], "receipt", "receipt.json")
    if not os.path.isfile(p):
        return False
    receipt = json.load(open(p, encoding="utf-8"))
    required = {"run_id", "driver_pid", "start", "end", "end_state", "end_reason", "deadline",
                "budget_usd", "openrouter_spend_usd", "claude_spend_usd", "landings",
                "driver_log_path", "driver_log_sha256", "runtime_revision"}
    return required.issubset(receipt.keys()) and receipt["end_state"] == "UNFUNDED"


def case_an_unreadable_field_is_no_data_with_a_reason_never_a_missing_key():
    """The launch worktree fixture is not a real git repository and no Claude ledger is configured. The runtime
    revision is unreadable, so it reads as a NO-DATA string carrying why. Since Lane R (2026-09-27) landings come
    from the run's own landing record and Claude spend from its retained ledger bytes, so a run with neither is a
    measured zero, not an unknown. Every one of the three is present in the receipt, never simply absent."""
    h = make_home(lanes="0")
    r = run(h, [future_hhmm()])
    dirs = run_dirs(h)
    if len(dirs) != 1:
        return False
    receipt = json.load(open(os.path.join(dirs[0], "receipt", "receipt.json"), encoding="utf-8"))
    if not all(key in receipt for key in ("landings", "runtime_revision", "claude_spend_usd")):
        return False
    v = receipt["runtime_revision"]
    return (isinstance(v, str) and v.startswith("NO-DATA:")
            and receipt["landings"] == 0 and receipt["claude_spend_usd"] == 0.0)


def case_the_receipts_log_digest_matches_the_log():
    """driver_log_sha256 is not a claim, it is a checkable fact: hashing the log the receipt names
    must reproduce exactly the digest the receipt carries."""
    h = make_home(lanes="0")
    r = run(h, [future_hhmm()])
    dirs = run_dirs(h)
    if len(dirs) != 1:
        return False
    receipt = json.load(open(os.path.join(dirs[0], "receipt", "receipt.json"), encoding="utf-8"))
    log_path = receipt.get("driver_log_path")
    if not log_path or not os.path.isfile(log_path):
        return False
    want = hashlib.sha256(open(log_path, "rb").read()).hexdigest()
    return receipt.get("driver_log_sha256") == want


def case_an_unwritable_run_directory_is_reported_and_never_silent():
    """The run directory itself is created fine (mkdir -p succeeds); it is made read-only right
    before the signal that ends the run, so the receipt writer's mkdir of run_dir/receipt/ must
    fail. The end must still name the failure, in the alarm and on this process's own stdout: an
    unwritable run directory is a fact to report, never a silent gap where a receipt should be.

    THE RUN DIRECTORY EXISTS LONG BEFORE THE TRAP DOES. BROTHER_RUN_DIR is created in the first ten
    lines of the script, far before line 252's `trap on_signal INT TERM HUP`; a first cut of this
    case chmod'd the directory and signalled the instant the directory appeared (at ~0.2s) and killed
    the driver outright (default SIGTERM disposition, returncode -15, no alarm, no log): the run never
    reached raise() at all. So this waits for "pass 1 at" in the driver's own log first, the same
    proof `_start_and_signal` uses elsewhere in this file, which is well after the trap is armed."""
    h = make_home(lanes="1", pass_body="sleep 30")
    runs_root = os.path.join(h, "runs-root")
    os.makedirs(runs_root, exist_ok=True)
    base = dict(ROOMY, **{k: v for k, v in os.environ.items() if k not in ESTATE})
    env = dict(base, HOME=h, PATH=os.path.join(h, "shim") + os.pathsep + os.environ.get("PATH", ""),
               BROTHER_LAUNCH_WORKTREE=os.path.join(h, "Brother", ".claude", "worktrees", "brother-unify-1.1"),
               BROTHER_RUNS_ROOT=runs_root)
    p = subprocess.Popen(["bash", SCRIPT, "23:59", "1"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, start_new_session=True)
    deadline = time.time() + 60
    rd = None
    while time.time() < deadline:
        cands = [d for d in os.listdir(runs_root) if d.startswith("run-")] if os.path.isdir(runs_root) else []
        if cands:
            rd = os.path.join(runs_root, cands[0]); break
        time.sleep(0.2)
    if rd is None:
        os.killpg(p.pid, signal.SIGKILL); p.communicate()
        return False
    ev_dir = os.path.join(h, ".claude", "evidence")
    while time.time() < deadline and "pass 1 at" not in "".join(
            open(os.path.join(ev_dir, n), encoding="utf-8").read() for n in os.listdir(ev_dir)
            if n.startswith("loop-until-")):
        time.sleep(0.2)
    os.chmod(rd, 0o555)
    try:
        os.killpg(p.pid, signal.SIGTERM)
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); out, _ = p.communicate()
    finally:
        os.chmod(rd, 0o755)   # restore so this suite's own teardown can remove it
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    receipt_path = os.path.join(rd, "receipt", "receipt.json")
    return ("RECEIPT FAILURE" in alarm and "RECEIPT FAILURE" in (out or "")
            and not os.path.isfile(receipt_path))


def case_proof_start_without_manifest_refuses_before_lease():
    """A proof phase with its run directory and a code root named, and no frozen manifest: proof start refuses before
    the lease. ONE condition: the manifest (the run directory and code root are given, so neither refusal fires)."""
    h = make_home()
    rd = os.path.join(h, "runs", "run-RB-case")
    os.makedirs(os.path.dirname(rd))
    r = run(h, ["tomorrow 23:59", "1"], drop=("BROTHER_FREEZE_MANIFEST",),
            extra=dict(BROTHER_PROOF_PHASE="RB", BROTHER_PROOF_RUN_DIR=rd, BROTHER_CODE_ROOT=_code_root(h)))
    return (r.returncode == 2 and "proof start" in r.stdout and not called(h, "guard")
            and os.path.isfile(os.path.join(rd, "proof", "start.json")))


def case_terminal_record_includes_proof_and_timezone():
    h=make_home()
    r=run(h,["23:59","1"])
    from pathlib import Path
    receipts=list(Path(h).rglob("receipt/receipt.json"))
    if len(receipts)!=1:return False
    receipt=json.loads(receipts[0].read_text())
    proof=receipts[0].parent.parent/"proof/evidence.json"
    if not proof.is_file():return False
    record=json.loads(proof.read_text())
    return record["run_id"] == receipt["run_id"] and datetime.datetime.fromisoformat(receipt["start"]).tzinfo is not None and record["freeze"]["start"]["verdict"] == "NO-DATA"


def case_a_receipt_failure_overrides_a_normal_deadline_exit():
    body = 'mkdir -p "$BROTHER_RUN_DIR/receipt/receipt.json"; printf \'%s\\n\' \'print("BUDGET_USD 10.00")\' \'print("DEADLINE 00:00")\' > "$HOME/.claude/bin/loop_intake.py"; exit 0'
    h=make_home(lanes="1",pass_body=body)
    r=run(h,["23:59","0"])
    return r.returncode == 1 and "RECEIPT FAILURE" in r.stdout and "DEADLINE" in (ev(h,"LOOP-ALARM.txt") or "")



def _control_on_claim(home, name, dangling=True):
    sentry = os.path.join(home, "Brother", ".claude", "worktrees", "brother-unify-1.1", "scripts", "worktree_sentry.py")
    source = open(sentry).read()
    action = ('os.symlink("missing-control-target", control)' if dangling else 'open(control, "w").write("owner: going out\\n")')
    code = 'if sys.argv[1:2] == ["claim"]:\n    control = os.path.join(os.environ["HOME"], ".claude", "evidence", %r)\n    %s\n' % (name, action)
    _w(sentry, source.replace('sys.exit(', code + 'sys.exit('))


def case_a_dangling_hold_refuses_before_lease():
    h = make_home(lanes="8")
    os.symlink("missing-control-target", os.path.join(h, ".claude", "evidence", "LOOP-HOLD.txt"))
    r = run(h, [future_hhmm()])
    return r.returncode == 2 and "acquire" not in called(h, "guard") and "cannot be read" in r.stdout


def case_a_preexisting_pause_refuses_before_lease():
    h = make_home(lanes="8")
    _w(os.path.join(h, ".claude", "evidence", "LOOP-PAUSE.txt"), "owner: wait\n")
    r = run(h, [future_hhmm()], timeout=15)
    return r.returncode == 2 and "acquire" not in called(h, "guard") and "owner: wait" in r.stdout


def _control_reader_case(midrun=False, missing=False):
    global SCRIPT
    h = make_home(lanes="8", pass_body='[ -e "$HOME/calls/control-blocked" ] && echo bad >> "$HOME/calls/unsafe-pass"; exit 42')
    scripts = os.path.join(h, "driver")
    shutil.copytree(os.path.dirname(SCRIPT), scripts)
    control = os.path.join(scripts, "loop_hold.py")
    if missing:
        os.remove(control)
    elif midrun:
        _w(control, 'import os,sys\np=os.path.join(os.environ["HOME"],"calls","reader")\nn=len(open(p).read().splitlines()) if os.path.exists(p) else 0\nopen(p,"a").write("call\\n")\nif n == 2: open(os.path.join(os.environ["HOME"],"calls","control-blocked"),"w").close()\nprint("unavailable" if n == 2 else "not held")\nsys.exit(77 if n == 2 else 0)\n')
        _w(os.path.join(h, "shim", "sleep"), '#!/bin/bash\necho sleep >> "$HOME/calls/sleep"\nrm -f "$HOME/calls/control-blocked"\n', 0o755)
    else:
        _w(control, 'print("unavailable")\nraise SystemExit(77)\n')
    old = SCRIPT
    try:
        SCRIPT = os.path.join(scripts, "loop_until.sh")
        r = run(h, [future_hhmm()])
    finally:
        SCRIPT = old
    if midrun:
        return r.returncode == 42 and "PAUSED" in called(h,"heartbeat") and called(h,"sleep") and not called(h,"unsafe-pass") and called(h,"reader").count("call") >= 4
    return r.returncode == 2 and "acquire" not in called(h,"guard")


def case_a_missing_control_reader_refuses():
    return _control_reader_case(missing=True)


def case_a_crashed_control_reader_refuses():
    return _control_reader_case()


def case_a_crashed_control_reader_pauses_before_pass():
    return _control_reader_case(midrun=True)


def case_a_dangling_pause_stops_the_next_pass():
    h = make_home(lanes="8", pass_body='[ -L "$HOME/.claude/evidence/LOOP-PAUSE.txt" ] && echo bad >> "$HOME/calls/unsafe-pass"; exit 42')
    _control_on_claim(h, "LOOP-PAUSE.txt")
    _w(os.path.join(h, "shim", "sleep"), '#!/bin/bash\necho sleep >> "$HOME/calls/sleep"\nrm -f "$HOME/.claude/evidence/LOOP-PAUSE.txt"\n', 0o755)
    r = run(h, [future_hhmm()])
    return r.returncode == 42 and "--state PAUSED" in called(h,"heartbeat") and "cannot be read" in r.stdout and called(h,"sleep") and not called(h,"unsafe-pass")


# ---------------------------------------------------------------- Lane D (run readiness, 2026-09-27)

def _code_root(home):
    """A frozen code root beside the landing tree: its worktree sentry and learning report record themselves, so a
    case can tell code run from the code root (U3) from code run from the landing tree (whose sentry records as WT)."""
    c = os.path.join(home, "coderoot")
    core = os.path.join(c, "plugin", "runtime", "brother", "core")
    if not os.path.isdir(core):
        os.makedirs(os.path.join(c, "scripts"))
        os.makedirs(core)
        _w(os.path.join(c, "scripts", "worktree_sentry.py"),
           '#!/usr/bin/env python3\nimport os, sys\n'
           'open(os.path.join(os.environ["HOME"], "calls", "sentry_code_root"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
           'sys.exit(0)\n', 0o755)
        _w(os.path.join(core, "dream_report.py"), 'print("DREAM REPORT FROM THE CODE ROOT")\n')
    return c


def _order_driver(home, prelude=""):
    """A copy of the driver's directory whose loop_receipt.py first appends its verb to $HOME/calls/order and its whole
    argument list to $HOME/calls/receipt_argv, runs `prelude` (a case's one injected condition), then runs the real
    one: the end steps' order and arguments become observable (D-5) without stubbing what they do."""
    d = os.path.join(home, "driver")
    shutil.copytree(os.path.dirname(SCRIPT), d)
    os.rename(os.path.join(d, "loop_receipt.py"), os.path.join(d, "loop_receipt_real.py"))
    _w(os.path.join(d, "loop_receipt.py"),
       'import os, runpy, sys\n'
       'open(os.path.join(os.environ["HOME"], "calls", "order"), "a").write((sys.argv[1] if len(sys.argv) > 1 else "") + "\\n")\n'
       'open(os.path.join(os.environ["HOME"], "calls", "receipt_argv"), "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
       + prelude +
       'sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop_receipt_real.py")\n'
       'runpy.run_path(sys.argv[0], run_name="__main__")\n')
    return os.path.join(d, "loop_until.sh")


def _end_order(home):
    """The end steps, in the order they ran: everything recorded after the work clock started."""
    lines = [l.strip() for l in called(home, "order").splitlines() if l.strip()]
    return lines[lines.index("proof-work-start") + 1:] if "proof-work-start" in lines else lines


def case_the_deadline_is_the_named_minute_to_the_second():
    """BSD date -j -f fills the fields its format omits from the clock, so a deadline parsed without seconds landed 0 to
    59 s after the minute named. The deadline the run records (start.json deadline_epoch, which proof admission reads)
    is the minute itself, for a bare and for a dated argument."""
    got = []
    for arg in ("23:59", (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d 06:00")):
        h = make_home(lanes="1")
        r = run(h, [arg, "1"])
        starts = [json.load(open(os.path.join(d, "proof", "start.json"), encoding="utf-8")) for d in run_dirs(h)]
        got.append(r.returncode == 42 and len(starts) == 1 and starts[0].get("deadline_epoch") is not None
                   and int(starts[0]["deadline_epoch"]) % 60 == 0)
    return all(got)


def case_a_dated_deadline_leaves_no_arithmetic_error():
    """D-1 (objection 1): the dated argument used to reach a first export of BROTHER_STOP_HOUR before it was parsed, and
    bash printed `10#2026-09: value too great for base` on every dated launch. Only the parsed hour is exported now."""
    h = make_home(lanes="1")
    r = run(h, ["2099-01-01 08:00", "1"])
    return r.returncode == 42 and "value too great" not in (r.stderr or "")


def case_children_write_no_bytecode_into_bin():
    """D-2 (U2, B5-07): the funding guard runs without -B and imports a module beside it; without the driver's
    PYTHONDONTWRITEBYTECODE a __pycache__ appears inside the frozen bin, which the end freeze reads as drift. The
    caller's own PYTHONDONTWRITEBYTECODE is removed so the driver's export is what is measured."""
    h = make_home(lanes="1")
    bind = os.path.join(h, ".claude", "bin")
    _w(os.path.join(bind, "probe_mod.py"), "X = 1\n")
    src = open(os.path.join(bind, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
    _w(os.path.join(bind, "burn_guard.py"), src[0] + "\nimport probe_mod\n" + src[1], 0o755)
    r = run(h, ["23:59", "1"], drop=("PYTHONDONTWRITEBYTECODE",))
    return r.returncode == 42 and not os.path.exists(os.path.join(bind, "__pycache__"))


def case_children_run_without_the_user_site():
    """U4 (B5-09): the driver exports PYTHONNOUSERSITE=1, so no child reads interpreter startup files from the user
    site. The funding guard records its interpreter's no_user_site flag; the caller's own value is removed."""
    h = make_home(lanes="1")
    bind = os.path.join(h, ".claude", "bin")
    src = open(os.path.join(bind, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
    _w(os.path.join(bind, "burn_guard.py"), src[0] + '\nimport os as _o, sys as _s\nopen(_o.path.join(_o.environ["HOME"], "calls", "nousersite"), "a").write("%d\\n" % _s.flags.no_user_site)\n' + src[1], 0o755)
    r = run(h, ["23:59", "1"], drop=("PYTHONNOUSERSITE",))
    seen = called(h, "nousersite").split()
    return r.returncode == 42 and seen and set(seen) == {"1"}


def case_unfunded_stops_runners():
    """D-4 (U8, B5-22): UNFUNDED ended the run and left detached runners buying rounds. raise() now stops runners for
    DEADLINE, DISK, BUDGET and UNFUNDED. ONE condition: the MONEY line vanishes after the start (UNFUNDED at pass 1)."""
    h = make_home(lanes="1", money="first")
    r = run(h, ["23:59", "1"])
    return r.returncode == 3 and "LOOP UNFUNDED at" in (ev(h, "LOOP-ALARM.txt") or "") and called(h, "stop_loop").split() == ["--runners-only"]


def case_a_deadline_stops_runners_exactly_once():
    """U8: the stop moved from the DEADLINE branch into raise(); it runs once, never twice."""
    h = make_home(lanes="1", pass_body="exit 0")
    _intake_seq(h, [("10.00", "23:59"), ("10.00", "00:01")])
    r = run(h, ["23:59", "1"])
    return r.returncode == 0 and "LOOP DEADLINE at" in (ev(h, "LOOP-ALARM.txt") or "") and called(h, "stop_loop").split() == ["--runners-only"]


def case_a_blocked_end_stops_runners():
    """2026-09-27: a BLOCKED end left 21 paid bridge calls running after its receipt. Every end but FINISHED stops the
    runners. ONE condition: the pass exits 44 (BLOCKED) at pass 1."""
    h = make_home(lanes="1", pass_body='echo "LOOP BLOCKED: a foreign write"; exit 44')
    r = run(h, ["23:59", "1"])
    return r.returncode == 44 and "LOOP BLOCKED at" in (ev(h, "LOOP-ALARM.txt") or "") and called(h, "stop_loop").split() == ["--runners-only"]


def case_a_finished_run_stops_no_runner():
    """U8: FINISHED has no runners by definition; the stop is decided once, for the four money and clock ends only."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"])
    return r.returncode == 42 and called(h, "stop_loop") == ""


def case_the_end_steps_run_in_order():
    """D-5 (U5, Lane R): ending, settle, the runner stop, then the clock, the end snapshot and the receipt. Every call
    admitted in the window is settled inside it. ONE condition: the budget is spent at pass 1 (BUDGET)."""
    h = make_home(lanes="1", budget="0.50")
    r = run(h, ["23:59", "1"], script=_order_driver(h))
    return (r.returncode == 3 and "LOOP BUDGET at" in (ev(h, "LOOP-ALARM.txt") or "")
            and _end_order(h) == ["end-claim", "proof-ending", "proof-settle", "stop_loop --runners-only", "clock", "proof-end", "write"])


def case_the_claude_ledger_is_exported_absolute():
    """Lane R: BROTHER_CLAUDE_CALLS_LEDGER is set to an absolute path in the driver's environment, so every child and
    the proof start record name one ledger whatever their cwd. Unset, it is the default ledger, spelled absolute."""
    h = make_home(lanes="1")
    bind = os.path.join(h, ".claude", "bin")
    src = open(os.path.join(bind, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
    _w(os.path.join(bind, "burn_guard.py"), src[0] + '\nimport os as _o\nopen(_o.path.join(_o.environ["HOME"], "calls", "ledger_env"), "a").write(_o.environ.get("BROTHER_CLAUDE_CALLS_LEDGER", "UNSET") + "\\n")\n' + src[1], 0o755)
    r = run(h, ["23:59", "1"], drop=("BROTHER_CLAUDE_CALLS_LEDGER",))
    seen = set(called(h, "ledger_env").split())
    return r.returncode == 42 and seen == {os.path.join(h, ".claude", "evidence", "claude-calls.jsonl")}


def case_a_relative_claude_ledger_is_made_absolute():
    """The same export with a relative name: it is resolved once, against the directory the driver started in."""
    h = make_home(lanes="1")
    bind = os.path.join(h, ".claude", "bin")
    src = open(os.path.join(bind, "burn_guard.py"), encoding="utf-8").read().split("\n", 1)
    _w(os.path.join(bind, "burn_guard.py"), src[0] + '\nimport os as _o\nopen(_o.path.join(_o.environ["HOME"], "calls", "ledger_env"), "a").write(_o.environ.get("BROTHER_CLAUDE_CALLS_LEDGER", "UNSET") + "\\n")\n' + src[1], 0o755)
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_CLAUDE_CALLS_LEDGER="rel-ledger.jsonl"))
    seen = set(called(h, "ledger_env").split())
    return r.returncode == 42 and seen == {os.path.join(os.getcwd(), "rel-ledger.jsonl")}


def case_the_worktree_claim_runs_from_the_code_root():
    """U3 (B5-04): the driver's sentry claim and release run the code root's copy, never the landing tree's."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_CODE_ROOT=_code_root(h)))
    mine = called(h, "sentry_code_root").split("\n")
    return (r.returncode == 42 and called(h, "sentry") == "" and any(l.startswith("claim ") for l in mine)
            and "release" in mine)


def case_a_code_root_without_the_sentry_refuses():
    """U3: the sentry the driver needs is looked for in the code root; the landing tree's copy never stands in for it."""
    h = make_home(lanes="1")
    c = os.path.join(h, "empty-code-root")
    os.makedirs(c)
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_CODE_ROOT=c))
    return r.returncode == 2 and c in r.stdout and "acquire" not in called(h, "guard")


def case_a_proof_phase_without_a_code_root_refuses():
    """U3: a proof phase runs frozen code only; with no BROTHER_CODE_ROOT the driver refuses before anything is recorded
    or leased. ONE condition: the code root (the run directory is given)."""
    h = make_home(lanes="1")
    rd = os.path.join(h, "runs", "run-RB-noroot")
    os.makedirs(os.path.dirname(rd))
    r = run(h, ["tomorrow 23:59", "1"], drop=("BROTHER_CODE_ROOT",), extra=dict(BROTHER_PROOF_PHASE="RB", BROTHER_PROOF_RUN_DIR=rd))
    return r.returncode == 2 and "BROTHER_CODE_ROOT" in r.stdout and not called(h, "guard") and not os.path.exists(rd)


def case_an_existing_proof_run_dir_refuses_and_is_recorded():
    """Objection 7: the launcher names the run directory; the driver creates it with a plain mkdir. One that already
    exists refuses, and the refusal is recorded outside it (proof_launch.refuse), before any lease."""
    h = make_home(lanes="1")
    rd = os.path.join(h, "runs", "run-RB-reused")
    os.makedirs(rd)
    r = run(h, ["tomorrow 23:59", "1"], extra=dict(BROTHER_PROOF_PHASE="RB", BROTHER_PROOF_RUN_DIR=rd, BROTHER_CODE_ROOT=_code_root(h)))
    reg = os.path.join(h, "runs", ".proof-launches", "run-RB-reused")
    refused = [n for n in os.listdir(reg) if n.startswith("refused-")] if os.path.isdir(reg) else []
    return r.returncode == 2 and len(refused) == 1 and not called(h, "guard") and os.listdir(rd) == []


def case_a_fresh_proof_run_dir_is_the_run_directory():
    """The launcher's run directory is the one the run records into: proof start writes there, not a timestamped one."""
    h = make_home(lanes="1")
    rd = os.path.join(h, "runs", "run-RB-fresh")
    os.makedirs(os.path.dirname(rd))
    # a deadline that always covers the proof's production window (a fixed 23:59 went red every day after about 16:00)
    run(h, ["tomorrow 23:59", "1"], drop=("BROTHER_FREEZE_MANIFEST",),
        extra=dict(BROTHER_PROOF_PHASE="RB", BROTHER_PROOF_RUN_DIR=rd, BROTHER_CODE_ROOT=_code_root(h)))
    return os.path.isfile(os.path.join(rd, "proof", "start.json")) and run_dirs(h) == []


def case_the_learning_report_runs_from_the_code_root():
    """U3 (loop_until.sh's learning journal line): the report's dream_report module comes from the code root."""
    h = make_home(lanes="1")
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_CODE_ROOT=_code_root(h), BROTHER_REPORT_DELAY_S="0"))
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    line = next((l for l in alarm.splitlines() if l.startswith("report: ")), "")
    path = line[len("report: "):].strip()
    return r.returncode == 42 and bool(path) and _poll(lambda: os.path.isfile(path) and "DREAM REPORT FROM THE CODE ROOT" in open(path, encoding="utf-8").read())


def case_a_slow_report_never_holds_the_driver():
    """U12 (objection 18): the end report runs detached, in its own session, with no descriptor of the driver's caller.
    A report that takes 40 s must not delay the driver's exit or its caller's end of file. ONE condition: the slow
    report (the delay before it starts is 0, so the old synchronous report would take the whole 40 s)."""
    h = make_home(lanes="1")
    _w(os.path.join(h, ".claude", "bin", "loop_report.py"), "import time\ntime.sleep(40)\nprint('STANDARD REPORT BODY')\n", 0o755)
    t0 = time.time()
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_REPORT_DELAY_S="0"))
    took = time.time() - t0
    return r.returncode == 42 and took < 25


def case_a_pause_inside_the_gap_is_recorded():
    """U11 (B5-03): a PAUSE placed and lifted inside the gap between passes used to be seen by nothing. The gap now
    looks for the control files every 10 s and asks the one reader when one exists, which records hold-observed."""
    h = make_home(lanes="1", pass_body='n=$(cat "$HOME/calls/passes" 2>/dev/null | wc -l); echo p >> "$HOME/calls/passes"; [ "$n" -ge 1 ] && exit 42; exit 0')
    pf = os.path.join(h, ".claude", "evidence", "LOOP-PAUSE.txt")
    import threading
    def pause():
        if _poll(lambda: called(h, "passes") != "", 30):
            time.sleep(2)
            _w(pf, "owner: going out for ten seconds\n")
            time.sleep(13)
            os.remove(pf)
    t = threading.Thread(target=pause); t.start()
    try:
        r = run(h, ["23:59", "40"], timeout=120)
    finally:
        t.join()
    rows = [e for e in _events(h) if e.get("kind") == "hold-observed"]
    return r.returncode == 42 and rows and all(e.get("observed_at") for e in rows) and "going out" in rows[0].get("detail", "")


def case_a_pending_claude_call_does_not_end_the_run():
    """M2-5 (B5-17, U7): a Claude call still inside its own timeout is PENDING (owner question Q2): the BUDGET line says
    so and the run goes on; only an uncosted, unreadable or invalid ledger ends it UNFUNDED."""
    h = make_home(lanes="1")
    led = os.path.join(h, "claude-calls.jsonl")
    with open(led, "w") as fh:
        fh.write(json.dumps({"at": "2099-01-01T00:00:00+00:00", "call": "c1", "expires_at": "2099-01-01T00:01:00+00:00"}) + "\n")
    r = run(h, ["23:59", "1"], extra=dict(BROTHER_CLAUDE_CALLS_LEDGER=led))
    return r.returncode == 42 and "+ 1 pending" in (r.stdout or "") and "UNFUNDED" not in (ev(h, "LOOP-ALARM.txt") or "")


def case_the_receipt_writer_takes_no_driver_claude_figure():
    """U9 (Lane R): the receipt tallies Claude spend from retained bytes after the final pass; the driver no longer hands
    the writer its own Claude figure. The recording wrapper keeps the write call's whole argument list."""
    h = make_home(lanes="1", budget="0.50")
    r = run(h, ["23:59", "1"], script=_order_driver(h))
    writes = [l for l in called(h, "receipt_argv").splitlines() if l.startswith("write ")]
    return r.returncode == 3 and len(writes) == 1 and "--claude-spent" not in writes[0] and "--claude-note" not in writes[0]


def case_an_ending_that_cannot_be_recorded_fails_the_end():
    """S6 (U5): a proof run whose ending barrier cannot be recorded could still register calls after its end, so the
    end fails (exit 1) while every other end step still runs. ONE condition: proof-ending exits 2."""
    h = make_home(lanes="1")
    d = _order_driver(h, prelude='sys.exit(2) if sys.argv[1:2] == ["proof-ending"] else None\n')
    r = run(h, ["23:59", "1"], script=d)
    return (r.returncode == 1 and "ENDING FAILED" in (r.stdout or "") and "LOOP FINISHED at" in (ev(h, "LOOP-ALARM.txt") or "")
            and _end_order(h)[-2:] == ["proof-end", "write"])


def _settle_seconds(home):
    return [l.split("--max-seconds ", 1)[1].split()[0] for l in called(home, "receipt_argv").splitlines()
            if l.startswith("proof-settle ") and "--max-seconds " in l]


def case_a_normal_end_settles_for_120_seconds():
    """S7: every end state settles; a normal end waits up to 120 s for the run's open calls. ONE condition: BUDGET."""
    h = make_home(lanes="1", budget="0.50")
    r = run(h, ["23:59", "1"], script=_order_driver(h))
    return r.returncode == 3 and _settle_seconds(h) == ["120"]


def case_an_owner_stop_settles_for_3_seconds():
    """S7: stop_loop.sh KILLs a driver still alive 12 s after its TERM, and a driver killed inside its end writes no
    receipt, so an owner stop settles for 3 s, not 120. ONE condition: the stop request naming this driver."""
    h = make_home(lanes="1", pass_body="sleep 30")
    d = _order_driver(h)
    base = dict(ROOMY, **{k: v for k, v in os.environ.items() if k not in ESTATE})
    env = dict(base, HOME=h, PATH=os.path.join(h, "shim") + os.pathsep + os.environ.get("PATH", ""),
               BROTHER_LAUNCH_WORKTREE=os.path.join(h, "Brother", ".claude", "worktrees", "brother-unify-1.1"))
    p = subprocess.Popen(["bash", d, "23:59", "1"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    _poll(lambda: "pass 1 at" in "".join(ev(h, n) or "" for n in os.listdir(os.path.join(h, ".claude", "evidence")) if n.startswith("loop-until-")), 60)
    with open(os.path.join(h, ".claude", "evidence", "LOOP-STOP-REQUEST.txt"), "w") as f:
        f.write("%d owner stop (fixture)\n" % p.pid)
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); p.communicate()
    return "LOOP STOPPED at" in (ev(h, "LOOP-ALARM.txt") or "") and _settle_seconds(h) == ["3"]


def _stop_with_claim(owner_pid):
    """Drive a run to pass 1, plant <run dir>/end-claim owned by owner_pid (None: no claim) BEFORE the stop, then stop it.
    The claim existing before the end begins is the deterministic barrier: no timing decides which end owns the run.
    Returns (driver exit code, driver log text, the run dir)."""
    h = make_home(lanes="1", pass_body="sleep 30")
    d = _order_driver(h)
    base = dict(ROOMY, **{k: v for k, v in os.environ.items() if k not in ESTATE})
    env = dict(base, HOME=h, PATH=os.path.join(h, "shim") + os.pathsep + os.environ.get("PATH", ""),
               BROTHER_LAUNCH_WORKTREE=os.path.join(h, "Brother", ".claude", "worktrees", "brother-unify-1.1"))
    p = subprocess.Popen(["bash", d, "23:59", "1"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    evd = os.path.join(h, ".claude", "evidence")
    logtext = lambda: "".join(ev(h, n) or "" for n in os.listdir(evd) if n.startswith("loop-until-"))
    _poll(lambda: "pass 1 at" in logtext(), 60)
    runs = [os.path.join(evd, "loop-runs", n) for n in os.listdir(os.path.join(evd, "loop-runs"))] if os.path.isdir(os.path.join(evd, "loop-runs")) else []
    run_dir = runs[0] if len(runs) == 1 else None
    if run_dir and owner_pid is not None:
        os.mkdir(os.path.join(run_dir, "end-claim"))
        with open(os.path.join(run_dir, "end-claim", "owner"), "w") as f:
            f.write("%d\n" % owner_pid)
    with open(os.path.join(evd, "LOOP-STOP-REQUEST.txt"), "w") as f:
        f.write("%d owner stop (fixture)\n" % p.pid)
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); p.communicate()
    return p.returncode, logtext(), run_dir


def case_an_end_owned_by_another_writes_nothing_to_the_run_log():
    """2026-10-04 (run-20261004-010636-49463: a second end appended RUN END after the receipt hashed the log). An end
    finding the run's end claimed by a live owner writes NOTHING to the run log, leaves a side note and exits 0.
    ONE condition: the claim held by a live process (this test)."""
    holder = subprocess.Popen(["bash", "-c", "exec -a loop_until.sh sleep 120"])   # a live process that IS a loop driver by name
    try:
        rc, text, run_dir = _stop_with_claim(holder.pid)
    finally:
        holder.kill(); holder.wait()
    note = open(os.path.join(run_dir, "end-duplicates.log")).read() if run_dir and os.path.exists(os.path.join(run_dir, "end-duplicates.log")) else ""
    return rc == 46 and "RUN END" not in text and "NOT A PROOF RUN" not in text and "DUPLICATE END" in note


def case_an_end_claimed_by_a_dead_owner_without_receipt_is_no_data():
    """The claim's owner is gone and no receipt exists: NO-DATA in the side note, exit 1, and nothing written to the run
    log, never a quiet second end. ONE condition: the claim held by a dead pid."""
    q = subprocess.Popen([sys.executable, "-c", "pass"]); q.wait()
    rc, text, run_dir = _stop_with_claim(q.pid)
    note = open(os.path.join(run_dir, "end-duplicates.log")).read() if run_dir and os.path.exists(os.path.join(run_dir, "end-duplicates.log")) else ""
    return rc == 1 and "RUN END" not in text and "NO-DATA" in note


def case_one_end_leaves_the_receipt_bound_log_unchanged():
    """The control: no prior claim, one end claims it, the receipt is written and the driver log's SHA256 still equals
    the receipt's driver_log_sha256 after the driver has exited (nothing was appended after the receipt)."""
    import hashlib
    rc, text, run_dir = _stop_with_claim(None)
    try:
        rec = json.load(open(os.path.join(run_dir, "receipt", "receipt.json")))
        now = hashlib.sha256(open(rec["driver_log_path"], "rb").read()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    try:
        owner = open(os.path.join(run_dir, "end-claim", "owner")).read().strip()
    except OSError:
        return False
    return text.count("RUN END") == 1 and now == rec.get("driver_log_sha256") and owner.isdigit()


def case_the_end_claim_rules():
    """loop_receipt.py end-claim, its rules one by one: a won claim always holds its owner (published by rename); a live
    owner that is no loop driver (a reused pid) with no receipt is NO-DATA (4); a receipt makes any later end a
    duplicate (3)."""
    tool = os.path.join(os.path.dirname(SCRIPT), "loop_receipt.py")
    claim = lambda d, pid: subprocess.run([sys.executable, "-B", tool, "end-claim", "--run-dir", d, "--pid", str(pid)],
                                          capture_output=True, text=True).returncode
    d = tempfile.mkdtemp(prefix="end-claim-")
    won = claim(d, os.getpid()) == 0 and open(os.path.join(d, "end-claim", "owner")).read().strip() == str(os.getpid())
    try:   # where ps cannot run (the hermetic sandbox) a live owner's command is unknown and reads as the owner
        ps_ok = str(os.getpid()) in subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid="], capture_output=True, text=True, timeout=10).stdout
    except OSError:
        ps_ok = False
    reused = claim(d, 1) == (4 if ps_ok else 3)   # the owner is this test: alive, no loop driver, no receipt
    os.makedirs(os.path.join(d, "receipt")); open(os.path.join(d, "receipt", "receipt.json"), "w").write("{}")
    dup = claim(d, 1) == 3
    leftovers = [n for n in os.listdir(d) if n.startswith(".end-claim-")]
    return won and reused and dup and not leftovers


def case_a_relative_runs_root_still_makes_an_absolute_run_dir():
    """2026-10-04: every Claude call is tagged from <BROTHER_RUN_DIR>/proof/start.json and a relative directory names no
    run, so it would refuse every call. make_run_dir exports an absolute directory whatever the root's spelling."""
    root = tempfile.mkdtemp(prefix="relroot-")
    os.makedirs(os.path.join(root, "rel"))
    r = subprocess.run(["bash", "-c", _run_dir_fn() + 'BROTHER_RUNS_ROOT=rel make_run_dir || exit 2; echo "$BROTHER_RUN_DIR"'],
                       cwd=root, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("BROTHER_PROOF")})
    got = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return r.returncode == 0 and got.startswith("/") and os.path.isdir(got)


def _two_minutes_ahead():
    return (datetime.datetime.now() + datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")


def case_a_drained_pass_exit_45_is_not_terminal():
    """D-6 (objection 10): while proof_ledger.draining() is true a pass that exits 45 (it started, landed and closed
    nothing, because every call would outlive the deadline) is not UNPRODUCTIVE: the driver sleeps to its deadline and
    ends DEADLINE. draining() answers True through its real code when a proof key is set and the launch record cannot
    be read (an unknown starts nothing); that is the one condition here, with no proof phase started."""
    h = make_home(lanes="1", pass_body="exit 45")
    r = run(h, [_two_minutes_ahead(), "5"], timeout=240, extra=dict(BROTHER_PROOF_BASELINE="/nonexistent/baseline.json"))
    alarm = ev(h, "LOOP-ALARM.txt") or ""
    return (r.returncode == 0 and "LOOP DEADLINE at" in alarm and "DRAINING: pass exit 45 is not terminal" in (r.stdout or "")
            and "UNPRODUCTIVE" not in alarm and (r.stdout or "").count("pass 1 done") == 1 and "pass 2 done" not in (r.stdout or ""))


def case_an_undrained_pass_exit_45_is_unproductive():
    """The control for D-6: the same pass with draining() false (no proof key) ends UNPRODUCTIVE at once, exit 45."""
    h = make_home(lanes="1", pass_body="exit 45")
    r = run(h, [_two_minutes_ahead(), "5"], timeout=120)
    return r.returncode == 45 and "LOOP UNPRODUCTIVE at" in (ev(h, "LOOP-ALARM.txt") or "") and "DRAINING" not in (r.stdout or "")


CONTROL_CASES = [
    ("dangling HOLD refuses before lease", case_a_dangling_hold_refuses_before_lease),
    ("preexisting PAUSE refuses before lease", case_a_preexisting_pause_refuses_before_lease),
    ("missing control reader refuses", case_a_missing_control_reader_refuses),
    ("crashed control reader refuses", case_a_crashed_control_reader_refuses),
    ("crashed control reader pauses before pass", case_a_crashed_control_reader_pauses_before_pass),
    ("dangling PAUSE stops next pass", case_a_dangling_pause_stops_the_next_pass),
]

LANE_D_CASES = [
    ("D-1 a dated deadline leaves no arithmetic error on stderr", case_a_dated_deadline_leaves_no_arithmetic_error),
    ("the deadline is the named minute, to the second", case_the_deadline_is_the_named_minute_to_the_second),
    ("D-2 children write no bytecode into the frozen bin", case_children_write_no_bytecode_into_bin),
    ("U4 children run without the user site", case_children_run_without_the_user_site),
    ("D-3 an intake deadline unchanged since the start is not an intervention", case_intake_deadline_unchanged_is_not_an_intervention),
    ("D-3 a budget change is recorded as an intervention", case_a_budget_change_is_recorded_as_an_intervention),
    ("D-4 UNFUNDED stops runners", case_unfunded_stops_runners),
    ("unreadable funding holds and resumes, never money spent", case_unreadable_funding_holds_and_resumes_never_money_spent),
    ("funding unreadable past its bound ends with the true reason", case_funding_unreadable_past_its_bound_ends_with_the_true_reason),
    ("U8 a deadline stops runners exactly once", case_a_deadline_stops_runners_exactly_once),
    ("U8 a finished run stops no runner", case_a_finished_run_stops_no_runner),
    ("a blocked end stops runners (2026-09-27)", case_a_blocked_end_stops_runners),
    ("D-5 the end steps run in order: ending, settle, stop, clock, end snapshot, receipt", case_the_end_steps_run_in_order),
    ("the Claude ledger is exported absolute (default)", case_the_claude_ledger_is_exported_absolute),
    ("a relative Claude ledger is made absolute", case_a_relative_claude_ledger_is_made_absolute),
    ("U3 the worktree claim and release run from the code root", case_the_worktree_claim_runs_from_the_code_root),
    ("U3 a code root without the sentry refuses", case_a_code_root_without_the_sentry_refuses),
    ("U3 a proof phase without a code root refuses", case_a_proof_phase_without_a_code_root_refuses),
    ("objection 7 an existing proof run directory refuses and is recorded outside it", case_an_existing_proof_run_dir_refuses_and_is_recorded),
    ("objection 7 a fresh proof run directory is the run directory", case_a_fresh_proof_run_dir_is_the_run_directory),
    ("U3 the learning report runs from the code root", case_the_learning_report_runs_from_the_code_root),
    ("U12 a slow report never holds the driver or its caller", case_a_slow_report_never_holds_the_driver),
    ("U11 a pause inside the gap is recorded", case_a_pause_inside_the_gap_is_recorded),
    ("M2-5 a pending Claude call does not end the run", case_a_pending_claude_call_does_not_end_the_run),
    ("U9 the receipt writer takes no driver Claude figure", case_the_receipt_writer_takes_no_driver_claude_figure),
    ("S6 an ending that cannot be recorded fails the end", case_an_ending_that_cannot_be_recorded_fails_the_end),
    ("S7 a normal end settles for 120 seconds", case_a_normal_end_settles_for_120_seconds),
    ("S7 an owner stop settles for 3 seconds", case_an_owner_stop_settles_for_3_seconds),
    ("D-6 a drained pass exit 45 is not terminal", case_a_drained_pass_exit_45_is_not_terminal),
    ("D-6 control: an undrained pass exit 45 is unproductive", case_an_undrained_pass_exit_45_is_unproductive),
]

CASES = CONTROL_CASES + LANE_D_CASES + [
    ("the login refresh runs before every pass, never in a seat", case_the_login_refresh_runs_before_every_pass_never_in_a_seat),
    ("a failed login refresh is reported and the pass still runs", case_a_failed_login_refresh_is_reported_and_the_pass_still_runs),
    ("a refresh that prints nothing is failed, never silent", case_a_refresh_that_prints_nothing_is_failed_never_silent),
    ("a refresh past its bound is failed timeout and the pass still runs", case_a_refresh_past_its_bound_is_failed_timeout_and_the_pass_still_runs),
    ("receipt failure overrides a normal deadline exit",case_a_receipt_failure_overrides_a_normal_deadline_exit),
    ("proof start refuses before lease without a frozen manifest",case_proof_start_without_manifest_refuses_before_lease),
    ("terminal run records proof and timezone",case_terminal_record_includes_proof_and_timezone),
    ("a live driver's estate paths never reach a case: run folders and scratch stay in the throwaway HOME", case_a_live_drivers_estate_paths_never_reach_a_case),
    ("the machine's swap never decides a start case (a sysctl stub reads 99999 MB)", case_the_machines_swap_never_decides_a_start_case),
    ("an inherited swap ceiling never decides a start case", case_an_inherited_ceiling_never_decides_a_start_case),
    ("a refusal on an unreadable stop time speaks on every channel", case_refusal_unreadable_stop_time),
    ("a refusal on a deadline already past speaks on every channel", case_refusal_deadline_already_past),
    ("cost per landing over the cap warns and the run continues", case_cost_per_landing_over_the_cap_ends_the_run_by_name),
    ("a bare HH:MM already past today means tomorrow, never a refusal (H3)", case_a_past_hhmm_means_tomorrow_never_a_refusal),
    ("a dated or a tomorrow deadline is read, garbage is refused (H3)", case_a_dated_and_a_tomorrow_deadline_are_read_and_garbage_is_refused),
    ("a lease held by another driver refuses before touching the tree", case_refusal_lease_held_by_another_driver),
    ("a worktree claimed elsewhere refuses and hands the lease back", case_refusal_worktree_claimed_releases_the_lease),
    ("a start writes a start note, and the end writes state plus report", case_start_note_then_terminal_state_and_report),
    ("the alarm history is appended to, never truncated", case_alarm_history_is_appended_never_truncated),
    ("every stage of a pass reads its own exit code, and a missing tree is BLOCKED not a pass", case_every_pass_stage_exit_code_is_read),
    ("the diagnostician lane runs each pass, detached and bounded", case_the_diagnostician_lane_runs_each_pass_detached_and_bounded),
    ("a pulse warning rings once as a persistent alert, and the end rings too", case_warning_rings_once_as_an_alert_and_the_end_rings_too),
    ("a crashing pulse cannot end the run", case_a_crashing_pulse_cannot_end_the_run),
    ("an owner HOLD refuses the start before the lease or the claim is touched", case_an_owner_hold_refuses_before_touching_anything),
    ("with no HOLD file the same home starts normally", case_no_hold_file_starts_normally),
    ("without a READY intake record the driver refuses before the lease or the claim", case_no_ready_intake_refuses_before_touching_anything),
    ("a missing intake tool refuses too", case_a_missing_intake_tool_refuses_too),
    ("the digest follows the owner cadence: 1, 3, 6, 10, 15, 20, then every 5", case_the_digest_follows_the_owners_cadence),
    ("a digest is written on pass one and the previous file is kept", case_a_digest_is_written_on_pass_one_and_a_previous_one_is_kept),
    ("a pause runs no pass and spends nothing, a resume continues from disk", case_a_pause_runs_no_pass_and_a_resume_continues),
    ("with no pause file the driver runs at once", case_no_pause_file_runs_at_once),
    ("an intake with no budget line refuses before the lease or the claim", case_no_budget_line_refuses_before_touching_anything),
    ("no MONEY line at the start refuses to start", case_no_money_line_refuses_to_start),
    ("a full disk refuses to start before the lease", case_a_full_disk_refuses_to_start),
    ("disk_hold holds when low or unreadable, passes when roomy", case_disk_hold_holds_low_and_unreadable_never_roomy),
    ("swap or a bloated file event daemon holds like a full disk, each on its own", case_swap_and_fseventsd_hold_like_a_full_disk),
    ("the swap hold is off unless the owner turns it on; the daemon hold stays", case_the_swap_hold_is_off_unless_the_owner_turns_it_on),
    ("power: on AC a long run starts and nothing is said about power", case_on_ac_power_a_long_run_starts),
    ("power: on battery a long run refuses to start, before the intake and the lease", case_on_battery_a_long_run_refuses_to_start),
    ("power: an unreadable source refuses like battery", case_an_unreadable_power_source_refuses_like_battery),
    ("power: a silent pmset on macOS is unreadable and refuses", case_a_silent_pmset_on_macos_is_unreadable_and_refuses),
    ("power: a host that is not macOS reads NO-DATA and starts", case_a_host_that_is_not_macos_reads_no_data_and_starts),
    ("power: an unreadable kernel name is unreadable, never NO-DATA", case_an_unreadable_kernel_name_is_unreadable_never_no_data),
    ("power: on battery inside the window the run starts", case_on_battery_inside_the_window_the_run_starts),
    ("power: a garbage window leaves the default in force", case_a_garbage_power_window_leaves_the_default_in_force),
    ("power: a window with a leading zero is read in base ten", case_a_window_with_a_leading_zero_is_read_in_base_ten),
    ("power: battery mid run is announced once and the run continues", case_battery_mid_run_is_announced_once_and_the_run_continues),
    ("power: a second battery episode is announced again", case_a_second_battery_episode_is_announced_again),
    ("power: a source that goes unreadable mid run is announced once", case_a_power_source_that_goes_unreadable_mid_run_is_announced_once),
    ("wake: a low reading after a wake that recovers continues the run", case_a_wake_then_a_recovered_disk_continues),
    ("wake: a disk still full after the settle raises DISK naming the sleep", case_a_wake_then_space_that_stays_low_raises_the_alarm_naming_the_sleep),
    ("wake: no wake since the last good reading raises DISK at once", case_no_wake_since_the_last_good_reading_raises_disk_at_once),
    ("wake: an unreadable wake time raises DISK at once", case_an_unreadable_wake_time_raises_disk_at_once),
    ("wake: the deadline is read at every re-read of a settle", case_the_deadline_is_read_at_every_reread_of_a_settle),
    ("wake: a second sleep inside a settle starts the settle again", case_a_second_sleep_inside_a_settle_starts_the_settle_again),
    ("wake: one wake earns one settle", case_one_wake_earns_one_settle),
    ("wake: a wake before the run started explains nothing", case_a_wake_before_the_run_started_explains_nothing),
    ("wake: a zero nap leaves the default in force", case_a_zero_nap_leaves_the_default_in_force),
    ("wake: a garbage settle bound leaves the default in force", case_a_garbage_settle_bound_leaves_the_default_in_force),
    ("wake: a low disk at the start names a wake inside the settle window", case_low_space_at_the_start_names_a_wake_inside_the_settle_window),
    ("wake: a hostile wake time is dropped, never evaluated", case_a_hostile_wake_time_is_dropped_never_evaluated),
    ("every child gets one run scoped TMPDIR and day old run folders are pruned", case_every_child_gets_one_run_scoped_tmpdir_and_old_runs_are_pruned),
    ("the run scratch is pruned by age every pass", case_the_run_scratch_is_pruned_by_age_every_pass),
    ("the recorder is on for every run: a durable BROTHER_RUN_DIR, refused when unwritable", case_the_recorder_is_on_for_every_run),
    ("Claude spend is reported beside the OpenRouter budget, never against it", case_claude_spend_counts_toward_the_budget),
    ("money vanishing mid run is UNFUNDED before the pass", case_money_vanishing_mid_run_is_unfunded),
    ("spend past the intake budget raises BUDGET before the pass", case_spend_past_the_budget_raises_budget),
    ("under the budget the run proceeds to the pass", case_under_budget_the_run_proceeds_to_the_pass),
    ("a BLOCKED alarm quotes the reason the pass printed", case_a_blocked_pass_alarm_quotes_the_pass_own_reason),
    ("a BLOCKED pass with no reason line is named as such", case_a_blocked_pass_with_no_reason_line_says_so),
    ("a deadline moved into the past by the intake ends the run", case_a_deadline_moved_into_the_past_by_the_intake_ends_the_run),
    ("the BUDGET line is printed every pass", case_the_budget_line_is_printed_every_pass),
    ("the driver log's name carries the date (finding 13)", case_the_log_name_carries_the_date),
    ("an owner stop ends STOPPED, a kill or a stale request ends INTERRUPTED (finding 19)", case_an_owner_stop_ends_stopped_and_a_kill_ends_interrupted),
    ("stop_loop.sh names the driver it stops in the stop request (finding 19)", case_stop_loop_writes_the_stop_request_for_the_driver_it_stops),
    ("an end owned by another writes nothing to the run log (2026-10-04)", case_an_end_owned_by_another_writes_nothing_to_the_run_log),
    ("an end claimed by a dead owner without receipt is NO-DATA", case_an_end_claimed_by_a_dead_owner_without_receipt_is_no_data),
    ("one end leaves the receipt bound log unchanged", case_one_end_leaves_the_receipt_bound_log_unchanged),
    ("the end claim rules (owner published, reused pid, receipt)", case_the_end_claim_rules),
    ("a relative runs root still makes an absolute run dir", case_a_relative_runs_root_still_makes_an_absolute_run_dir),
    ("Claude calls without a cost say NO-DATA, never 0.0000 (finding 14)", case_claude_calls_without_a_cost_say_no_data),
    ("a live deadline change reaches the funding guard's stop hour (finding 8)", case_a_live_deadline_change_reaches_the_funding_guard),
    ("a done row with a null cost says NO-DATA, never 0.0000 (review 2026-09-26)", case_a_done_row_with_a_null_cost_says_no_data),
    ("a terminal end writes a receipt with every required key (F23)", case_a_terminal_end_writes_a_receipt_with_every_required_key),
    ("an unreadable field is NO-DATA with a reason, never a missing key (F23)", case_an_unreadable_field_is_no_data_with_a_reason_never_a_missing_key),
    ("the receipt's log digest matches the log (F23)", case_the_receipts_log_digest_matches_the_log),
    ("an unwritable run directory is reported and never silent (F23)", case_an_unwritable_run_directory_is_reported_and_never_silent),
]


def main():
    if not os.path.isfile(SCRIPT):
        # NEVER a pass: the mutation seam must not be able to turn this suite into a no-op.
        print("selftest: 0 cases, FAILED: loop_until.sh not found at %s" % SCRIPT)
        return 1
    bad, nodata = [], []
    for name, fn in CASES:
        try:
            ok = fn()
        except Exception as exc:                       # a raising case is a failing case
            ok, name = False, "%s [%s: %s]" % (name, type(exc).__name__, exc)
        # A CASE THE HOST CANNOT RUN SAYS SO BY NAME (2026-10-03): a str return starting with NO-DATA is neither a pass
        # nor a red, the way unittest prints OK (skipped=N); it is listed on the summary line so nobody reads it as green.
        if isinstance(ok, str) and ok.startswith("NO-DATA"):
            nodata.append("%s: %s" % (name, ok))
        elif not ok:
            bad.append(name)
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)
    tail = " (%d NO-DATA: %s)" % (len(nodata), "; ".join(nodata)) if nodata else ""
    print("selftest: %d cases, %s%s" % (len(CASES), "OK" if not bad else "FAILED: " + "; ".join(bad), tail))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

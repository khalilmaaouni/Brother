#!/usr/bin/env python3
"""Task watchdog: per-task drift and block detection with the unlock printed.

Founder direction 2026-08-28: a smart watchdog, specific to each task, that
keeps the project on track, and when something drifts or blocks it FINDS THE
WAY TO UNLOCK rather than only adding an alarm to the pile.

What makes it task-specific rather than general: it invents no configuration
of its own. Every probe is derived from the task's OWN record in the sbe task
registry, which every write in this repository already has to open under the
authority-file guard. Opening a task IS arming the watchdog for that task,
with that task's owned paths, that task's verify command, that task's age.
Nothing else to install, nothing to remember.

What makes it adaptive rather than a resource burden:
- No daemon, no cron, no polling. One shot, on demand, at session start, or
  before a push. An empty registry exits immediately at zero cost.
- Verify commands run ONLY for tasks showing live activity (dirty owned
  paths), only when on the read-only allowlist shared with
  blocker_freshness.py. A finished-looking task gets a cheap age check, never
  a command run. So cost scales with how much is actually in flight.
- Silence means healthy, per the stall detector v2 discipline. A healthy
  registry prints one line and exits 0.

The four conditions, each with its unlock:

  STALE-OPEN     every owned path is clean against HEAD and the task has been
                 open past the threshold: the work looks landed and the open
                 record is now pure burden (19 of these existed the morning
                 this was written, 6 from an agent gone five days).
                 UNLOCK: the exact `sbe task close <id>` to run.
  LONG-DIRTY     owned paths still dirty past the threshold: work possibly
                 stuck mid-flight.
                 UNLOCK: the task's own verify command to re-anchor, and the
                 commit path.
  DRIFT          dirty tracked paths in the working tree that NO open task
                 owns: writes happening outside every declared fence, the
                 exact state the authority guard blocks at edit time for
                 authority files and nothing watched for ordinary ones.
                 UNLOCK: the exact `sbe task open` naming those paths, or
                 commit them under the task they belong to.
  BLOCKED        a live task's own verify command (read-only allowlist only)
                 exits nonzero: the task's own definition of healthy is red.
                 UNLOCK: the failing command verbatim plus its last output
                 line, which is where the fix starts.

Verdicts follow the estate's law: silence per condition is PASS, a finding is
a finding, and a registry that cannot be read at all is NO-DATA (exit 2),
never a pass. Exit 0 healthy, 1 findings, 2 NO-DATA.

Read-only by design: this tool never closes a task, never commits, never
edits. It proposes the one command; a person or the owning session runs it.
No em or en dashes anywhere.

origin: two confirmed callers of main(), both invoking this module's own CLI
rather than importing its write functions. (1) .claude/settings.json wires a
SessionStart hook that runs `python3 "$wd" --triage` on every session start
(the `wd` variable resolves to this file's path), so most runs are automatic,
fired by the harness at session start. (2) A human or session runs it
directly, without --triage, as step 3 of the task-close ritual named in
gen_command_center.py's LOOP_CLOSE_RING constant: "python3
scripts/task_watchdog.py exit 0". Confirmed by grep: night_tick.py,
lifecycle_hooks.py and handover_ceremony.py import and call read-only
functions of this module (read_registry, read_day_plan_rows,
ready_set_summary, age_hours) but none of them import or call main(),
save_quarantine_state(), or save_triage_offset(), so neither of those
writers is reached except through this file's own CLI entry point.

PRODUCER: this module is the sole producer of its two state files. The write
to .sbe/watchdog-state.json happens inside save_quarantine_state() at the
`with open(path, "w") as f: json.dump(state, f)` call (lines 325-326), called
unconditionally from main() (line 689) on every run that reaches a registry
read. The write to .sbe/watchdog-triage-offset happens inside
save_triage_offset() at the `with open(path, "w") as f: f.write(str(int(offset)))`
call (lines 349-350), called only from the `--triage` branch of main()'s
inner finish() (line 636).
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Importable both from scripts/ and as scripts.task_watchdog from the
# repository root, same fix as handover_ceremony's sibling import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from autonomy_dial import gate as autonomy_gate  # noqa: E402

SBE_CANDIDATES = (
    os.path.expanduser(
        "~/.claude/plugins/cache/brothersbe/brothersbe/3.2.0/bin/sbe"),
    "sbe",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
QUARANTINE_STATE_PATH = REPO_ROOT / ".sbe" / "watchdog-state.json"
# Its own file, never a key inside the quarantine state: apply_quarantine
# deletes every key not naming a task that fired this run, so an offset
# parked there would be wiped on the first quiet run.
TRIAGE_OFFSET_PATH = REPO_ROOT / ".sbe" / "watchdog-triage-offset"
LIVE_STATE_PATH = REPO_ROOT / "docs" / "plan" / "LIVE-STATE.json"

STALE_HOURS = 24
VERIFY_TIMEOUT = 60
# Whole-run ceiling for --triage's verify runs, because it fires from a
# SessionStart hook: a stalled session start is worse than a partial triage.
TRIAGE_BUDGET_S = 25
QUARANTINE_THRESHOLD = 3
LANE_CAP = 2

TASK_FINDING_RE = re.compile(r"^(STALE-OPEN|LONG-DIRTY|BLOCKED) (\S+):")

# Shared shape with blocker_freshness.py's ALLOW: only read-only verify
# commands are ever run. Anything else is skipped silently, because running an
# unknown command to check for a block would BE the burden this tool exists
# to avoid.
ALLOW = (
    re.compile(r"^git (ls-remote|log|rev-parse|cat-file|status|branch) "),
    re.compile(r"^(python3|/usr/bin/python3) -m unittest "),
    re.compile(r"^(bash|sh) scripts/[a-z_.-]+\.sh$"),
    re.compile(r"^python3 scripts/[a-z_]+\.py(\s+--[a-z-]+)*$"),
    re.compile(r"^test -f [A-Za-z0-9_./-]+$"),
)


def read_registry():
    """The open tasks, from `sbe task list --json`, or None on NO-DATA."""
    for candidate in SBE_CANDIDATES:
        try:
            out = subprocess.run(
                [candidate, "task", "list", "--json"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode != 0:
            continue
        try:
            return json.loads(out.stdout).get("openTasks", [])
        except (ValueError, AttributeError):
            continue
    return None


def dirty_paths():
    """Tracked paths dirty against HEAD plus untracked files, from porcelain.

    Returns None on NO-DATA (not a git tree). Ignores the .sbe/ and
    .brothermode/ stores: registry state churns by design and watching it
    would make every run noisy.
    """
    try:
        out = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    paths = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if path.startswith(".sbe/") or path.startswith(".brothermode/"):
            continue
        paths.append(path)
    return paths


def owns(task, path):
    """Does this task's declaration cover this path? Prefix semantics for
    directories, exact for files, matching the authority guard's reading."""
    for owned in task.get("ownedPaths", []):
        owned = owned.rstrip("/")
        if path == owned or path.startswith(owned + "/"):
            return True
    return False


def age_hours(opened_at, now=None):
    """Hours since openedAt, or None when the stamp cannot be read."""
    try:
        opened = datetime.strptime(opened_at, "%Y-%m-%dT%H:%M:%SZ")
        opened = opened.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    now = now or datetime.now(timezone.utc)
    return (now - opened).total_seconds() / 3600.0


def verify_runnable(cmd):
    return bool(cmd) and any(p.match(cmd) for p in ALLOW)


def task_threshold(task, stale_hours=STALE_HOURS):
    """WD-02, Airflow per-task SLA shape: a task's own `expectedHours` field
    overrides the flat staleness default, in either direction. Absent or
    unreadable keeps the flat default; nothing is ever written back."""
    raw = task.get("expectedHours")
    if raw is None:
        return stale_hours
    try:
        return float(raw)
    except (TypeError, ValueError):
        return stale_hours


def examine(tasks, dirty, now=None, run_verify=None, stale_hours=STALE_HOURS):
    """The whole judgement, pure over its inputs so the tests can feed it.

    run_verify(cmd) returns (exit_code, last_output_line) or None to skip.
    Returns a list of finding strings, each carrying its UNLOCK.
    """
    findings = []
    dirty = dirty or []

    for task in tasks:
        tid = task.get("id", "?")
        task_dirty = [p for p in dirty if owns(task, p)]
        hours = age_hours(task.get("openedAt", ""), now)
        threshold = task_threshold(task, stale_hours)
        old = hours is not None and hours >= threshold

        if not task_dirty and old:
            findings.append(
                "STALE-OPEN %s: open %d hours, every owned path clean against "
                "HEAD; the work looks landed and the record is now burden. "
                "UNLOCK: sbe task close %s" % (tid, int(hours), tid))
            continue

        if task_dirty and old:
            findings.append(
                "LONG-DIRTY %s: open %d hours with %d owned path(s) still "
                "dirty (%s). UNLOCK: run its own check `%s`, then commit or "
                "hand over." % (tid, int(hours), len(task_dirty),
                                ", ".join(task_dirty[:3]),
                                task.get("verifyCommand", "")))

        if task_dirty and run_verify is not None:
            cmd = task.get("verifyCommand", "")
            if verify_runnable(cmd):
                result = run_verify(cmd)
                if result is not None and result[0] != 0:
                    findings.append(
                        "BLOCKED %s: its own verify `%s` exits %d (%s). "
                        "UNLOCK: start from that output; the task's own "
                        "definition of healthy is red."
                        % (tid, cmd, result[0], result[1]))

    unowned = [p for p in dirty if not any(owns(t, p) for t in tasks)]
    if unowned:
        # DIAL-01: the autonomy dial's real call site. Opening a task to
        # cover unowned dirty paths is itself an action with an observable
        # shape (one path or several, named or not), so it is classified
        # and gated exactly like any other action rather than always
        # auto-proposed. The dial (docs/plan/AUTONOMY-POLICY-V1.md, read
        # from BROTHER_AUTONOMY_DIAL) decides whether this finding reads as
        # something the watchdog would execute on its own recognizance or
        # something it would stop and ask about first; either way this
        # function still only proposes the UNLOCK, it never runs it.
        dial_decision = autonomy_gate({
            "single_file_or_named_target": len(unowned) == 1,
            "contract_change": "none",
            "crosses_boundary": len(unowned) > 1,
            "reversible_under_hour": True,
            "blast_radius": ("small_and_named" if len(unowned) <= 3
                             else "large_or_unnamed"),
        })
        findings.append(
            "DRIFT: %d dirty path(s) no open task owns (%s). DIAL: %s. "
            "UNLOCK: sbe task "
            "open --id <id> --agent <you> --role writer --base $(git "
            "rev-parse HEAD) --verify <check> %s   or commit them under the "
            "task they belong to."
            % (len(unowned), ", ".join(unowned[:5]), dial_decision,
               " ".join("--owns %s" % p for p in unowned[:5])))

    return findings


def apply_quarantine(findings, tasks, state, threshold=QUARANTINE_THRESHOLD):
    """WD-01, SQS dead-letter shape: a task whose findings fire across
    `threshold` consecutive watchdog runs stops printing its retry-style
    unlock and gets ONE escalation line addressed to the founder instead.

    state maps task id to its consecutive-run count and is mutated in
    place (and returned) so the caller persists it between runs; a task
    silent this run has its count cleared. Pure otherwise, so the tests
    can drive it across simulated runs without touching a file.
    """
    tasks_by_id = {t.get("id"): t for t in tasks}
    seen = set()
    for f in findings:
        m = TASK_FINDING_RE.match(f)
        if m:
            seen.add(m.group(2))

    for tid in list(state):
        if tid not in seen:
            del state[tid]
    for tid in seen:
        state[tid] = state.get(tid, 0) + 1

    out = []
    escalated = set()
    for f in findings:
        m = TASK_FINDING_RE.match(f)
        if not m:
            out.append(f)
            continue
        tid = m.group(2)
        if tid in escalated:
            continue
        if state.get(tid, 0) >= threshold:
            owned = tasks_by_id.get(tid, {}).get("ownedPaths") or []
            out.append(
                "QUARANTINE %s: stuck across %d consecutive watchdog runs; "
                "escalating to the founder instead of repeating the unlock. "
                "Evidence lives at %s."
                % (tid, state[tid],
                   ", ".join(owned) if owned else "no owned paths on record"))
            escalated.add(tid)
        else:
            out.append(f)
    return out


def load_quarantine_state(path=QUARANTINE_STATE_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_quarantine_state(state, path=QUARANTINE_STATE_PATH):
    """Persist quarantine/escalation state. A failed write used to vanish
    through a bare `except OSError: pass`: the caller believed the state was
    saved, the next run re-derived escalations from a stale or missing file,
    and nobody was ever told the write did not happen. Reported to stderr
    instead, by path and reason, without raising: this runs from a
    SessionStart hook and one bad write must not stall every session."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)
    except OSError as e:
        print("task_watchdog: could not save quarantine state to %s: %s"
              % (path, e), file=sys.stderr)


def load_triage_offset(path=TRIAGE_OFFSET_PATH):
    try:
        with open(path) as f:
            return max(0, int(f.read().strip()))
    except (OSError, ValueError):
        return 0


def save_triage_offset(offset, path=TRIAGE_OFFSET_PATH):
    """Persist the rotating triage offset (fixed-order-under-a-fixed-budget
    starves without this: the offset is what stops the same tail of tasks
    being scanned every run). A failed write used to vanish through a bare
    `except OSError: pass`, so the rotation silently reset to zero on every
    call and nobody was told. Reported to stderr instead, without raising,
    for the same SessionStart-hook reason as save_quarantine_state above."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(int(offset)))
    except OSError as e:
        print("task_watchdog: could not save triage offset to %s: %s"
              % (path, e), file=sys.stderr)


def ready_ids_local(rows):
    """Local mirror of gen_command_center.ready_state's SCHEDULED-row rule
    (READY-SET-STANDARD-2026-08-28.md): a row is READY when every id in its
    depends_on names a DONE row and it names no pending event. A small local
    copy rather than an import, matching gen_command_center.py's own
    standalone, no-shared-lib pattern. Returns the READY row ids."""
    status_of = {r.get("id"): str(r.get("status") or "").strip().upper()
                 for r in rows}
    ready = []
    for r in rows:
        if str(r.get("status") or "").strip().upper() != "SCHEDULED":
            continue
        unmet = any(status_of.get(dep) != "DONE"
                    for dep in (r.get("depends_on") or []))
        event = r.get("event")
        if not unmet and not (isinstance(event, str) and event.strip()):
            ready.append(r.get("id"))
    return ready


def check_lane_cap(path=LIVE_STATE_PATH):
    """WD-03, Temporal worker-concurrency shape: the two-lane cap as a
    mechanical condition over docs/plan/LIVE-STATE.json day_plan rows.
    Flags a cap breach (more than LANE_CAP rows IN-FLIGHT) and an
    idle-lane breach (zero rows IN-FLIGHT while at least one SCHEDULED row
    is READY). Missing or unparsable LIVE-STATE is NO-DATA, never a pass
    and never a crash."""
    try:
        with open(path) as f:
            live_state = json.load(f)
    except (OSError, ValueError):
        return ["NO-DATA: docs/plan/LIVE-STATE.json could not be read; the "
                "two-lane cap was not checked, never a pass"]
    rows = (live_state.get("day_plan") or {}).get("rows")
    if not isinstance(rows, list):
        return ["NO-DATA: docs/plan/LIVE-STATE.json has no day_plan.rows; "
                "the two-lane cap was not checked, never a pass"]

    findings = []
    in_flight = [r.get("id") for r in rows
                 if str(r.get("status") or "").strip().upper() == "IN-FLIGHT"]
    if len(in_flight) > LANE_CAP:
        findings.append(
            "LANE-CAP-BREACH: %d row(s) IN-FLIGHT (%s), cap is %d. UNLOCK: "
            "park or close a lane before pulling another node."
            % (len(in_flight), ", ".join(in_flight), LANE_CAP))

    if not in_flight:
        ready = ready_ids_local(rows)
        if ready:
            findings.append(
                "IDLE-LANE: 0 rows IN-FLIGHT while %d row(s) are READY (%s). "
                "UNLOCK: pull the READY node with the most downstream "
                "dependents next." % (len(ready), ", ".join(ready)))

    return findings


def audit_idempotency(tasks):
    """WD-05, Temporal's idempotency discipline read onto done-checks: a
    done_check safe to re-run is one whose command sits on the read-only
    ALLOW list, because a read-only command has no side effect to repeat.
    Everything else is NAMED rather than trusted: an empty verify is
    NO-CHECK, and a command off the allowlist is UNPROVEN (it may well be
    safe, but nothing here proved it, and NO-DATA is never a pass). Pure
    over its input; prints nothing itself."""
    lines = []
    for t in tasks:
        tid = t.get("id", "?")
        cmd = (t.get("verifyCommand") or "").strip()
        if not cmd:
            lines.append(
                "NO-CHECK %s: no verify command on record, so re-verification "
                "has nothing to run. UNLOCK: re-open with --verify naming a "
                "read-only check." % tid)
        elif not verify_runnable(cmd):
            lines.append(
                "UNPROVEN %s: verify `%s` is off the read-only allowlist, so "
                "a re-run's safety is unproven, never assumed. UNLOCK: point "
                "the check at a read-only form, or leave it and accept the "
                "watchdog will never auto-run it." % (tid, cmd))
    return lines


def live_verify(cmd, pick="last", timeout=VERIFY_TIMEOUT):
    try:
        out = subprocess.run(cmd.split(), capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (out.stdout + out.stderr).strip().splitlines()
    if not lines:
        return (out.returncode, "")
    return (out.returncode, lines[0] if pick == "first" else lines[-1])


def triage_lines(tasks, now=None, run_verify=None, quarantine_state=None,
                 stale_hours=STALE_HOURS, start_offset=0):
    """Founder direction 2026-08-28: --triage's per-task line. Age vs this
    task's own threshold, a DUE/NOT-DUE verdict, WHY-NOT-DELIVERED (its own
    verify command run if it sits on the read-only allowlist, quoting PASS
    or the first failing line; NOT-RUN with the reason otherwise), and
    BLOCKER (the quarantine state if quarantined, the verify failure if
    red, otherwise a plain admission that nothing was recorded). Pure over
    its inputs so the tests can drive it without a live registry or a real
    subprocess."""
    quarantine_state = quarantine_state or {}
    # Founder ask, night run 2026-08-28: watchdogs for EVERY task. The
    # budget below is real (this fires from a SessionStart hook), but a
    # fixed run order under a fixed budget starves the same tail tasks on
    # every run rather than merely delaying them. Rotating where the run
    # starts bounds the delay; the REPORT stays in registry order, because
    # queue position is scheduling state and not the reader's model.
    total = len(tasks)
    offset = (start_offset % total) if total else 0
    order = list(range(offset, total)) + list(range(0, offset))
    by_index = {}
    for index in order:
        t = tasks[index]
        tid = t.get("id", "?")
        hours = age_hours(t.get("openedAt", ""), now)
        threshold = task_threshold(t, stale_hours)
        due = "DUE" if hours is not None and hours >= threshold else "NOT-DUE"
        age_str = ("%dh" % int(hours)) if hours is not None else "unknown"
        cmd = (t.get("verifyCommand") or "").strip()

        result = None
        if not cmd:
            why = "NOT-RUN: no verify command on record"
        elif not verify_runnable(cmd):
            why = "NOT-RUN: verify `%s` is off the read-only allowlist" % cmd
        elif run_verify is None:
            why = "NOT-RUN: no verify runner supplied"
        else:
            result = run_verify(cmd)
            if result is None:
                why = "NOT-RUN: verify `%s` could not be executed" % cmd
            elif result[0] is None:
                why = ("NOT-RUN: triage's time budget was spent before this "
                       "one; re-run `python3 scripts/task_watchdog.py "
                       "--triage` when the machine is quieter")
                result = None
            elif result[0] == 0:
                why = "PASS `%s`" % cmd
            else:
                why = "FAILS `%s`: %s" % (cmd, result[1])

        if quarantine_state.get(tid, 0) >= QUARANTINE_THRESHOLD:
            blocker = ("quarantined: stuck across %d consecutive watchdog "
                       "runs, escalated to the founder instead of the "
                       "unlock" % quarantine_state[tid])
        elif result is not None and result[0] != 0:
            blocker = "verify failure: %s" % result[1]
        else:
            blocker = "none recorded, needs its owner's report"

        by_index[index] = (
            "TRIAGE %s: age %s vs expected %gh (%s) | WHY-NOT-DELIVERED: %s "
            "| BLOCKER: %s" % (tid, age_str, threshold, due, why, blocker))
    return [by_index[i] for i in range(total)]


def ready_set_summary(rows):
    """Local mirror of gen_command_center.ready_state's classification
    (READY-SET-STANDARD-2026-08-28.md), reduced to what --triage prints:
    READY and IN-FLIGHT ids, and the EVENT-WAIT rows with their event text.
    Same standalone, no-shared-lib pattern as ready_ids_local above, so
    this file never imports gen_command_center."""
    status_of = {r.get("id"): str(r.get("status") or "").strip().upper()
                 for r in rows}
    ready, in_flight, event_wait = [], [], []
    for r in rows:
        rid = r.get("id")
        status = str(r.get("status") or "").strip().upper()
        if status == "IN-FLIGHT":
            in_flight.append(rid)
            continue
        if status != "SCHEDULED":
            continue
        unmet = any(status_of.get(dep) != "DONE"
                    for dep in (r.get("depends_on") or []))
        if unmet:
            continue
        event = r.get("event")
        if isinstance(event, str) and event.strip():
            event_wait.append((rid, event.strip()))
        else:
            ready.append(rid)
    return {"ready": ready, "in_flight": in_flight, "event_wait": event_wait}


def read_day_plan_rows(path=LIVE_STATE_PATH):
    try:
        with open(path) as f:
            live_state = json.load(f)
    except (OSError, ValueError):
        return None
    rows = (live_state.get("day_plan") or {}).get("rows")
    return rows if isinstance(rows, list) else None


def format_ready_summary(rows):
    """The parallel-streams glance: N READY, M IN-FLIGHT, K EVENT-WAIT with
    the event names. NO-DATA, never a pass, when the rows cannot be read."""
    if rows is None:
        return ("NO-DATA: docs/plan/LIVE-STATE.json day_plan.rows could not "
                "be read; the ready set was not shown")
    summary = ready_set_summary(rows)
    events = ", ".join("%s (%s)" % (rid, ev)
                       for rid, ev in summary["event_wait"])
    return (
        "ready set: %d READY (%s), %d IN-FLIGHT (%s), %d EVENT-WAIT%s"
        % (len(summary["ready"]), ", ".join(summary["ready"]) or "none",
           len(summary["in_flight"]), ", ".join(summary["in_flight"]) or "none",
           len(summary["event_wait"]), (": " + events) if events else ""))


def _parse_float_arg(value, flag, default):
    """Parse one CLI float value, or warn to stderr and fall back to
    default. Shared by --stale-hours and --budget below: both used to fall
    back to their default through a bare `except ValueError: pass`, so a
    typo'd value read as "the threshold was set" when the run silently used
    the default the whole time. One helper means one fix covers both call
    sites and any later flag of the same shape."""
    try:
        return float(value)
    except ValueError:
        print("task_watchdog: %s %r is not a number, using the default %s"
              % (flag, value, default), file=sys.stderr)
        return default


def main(argv):
    stale_hours = STALE_HOURS
    for i, arg in enumerate(argv):
        if arg == "--stale-hours" and i + 1 < len(argv):
            stale_hours = _parse_float_arg(argv[i + 1], "--stale-hours", STALE_HOURS)

    triage = "--triage" in argv
    triage_budget = TRIAGE_BUDGET_S
    for i, arg in enumerate(argv):
        if arg == "--budget" and i + 1 < len(argv):
            # 0 means no ceiling: the deliberate, attended run that wants
            # every task covered in one pass.
            triage_budget = max(0.0, _parse_float_arg(argv[i + 1], "--budget", TRIAGE_BUDGET_S))

    def finish(code, tasks_for_triage=None, quarantine_state=None):
        """Founder direction 2026-08-28: under --triage, print the per-task
        triage lines and the ready-set summary AFTER the normal findings,
        never instead of them, then return the same exit code the normal
        run would have returned."""
        if triage:
            # This runs from a SessionStart hook, so it must never stall a
            # session: on a loaded machine one suite alone can outlast the
            # whole hook. Verifies run until the budget is spent, then the
            # rest report NOT-RUN with that reason rather than blocking.
            tasks_for_triage = tasks_for_triage or []
            deadline = time.time() + triage_budget
            ran = [0]

            def budgeted(cmd):
                if triage_budget:
                    left = deadline - time.time()
                    if left <= 0:
                        return (None, "")
                else:
                    left = VERIFY_TIMEOUT
                ran[0] += 1
                return live_verify(cmd, pick="first", timeout=left)

            offset = load_triage_offset()
            for line in triage_lines(
                    tasks_for_triage,
                    run_verify=budgeted,
                    quarantine_state=quarantine_state,
                    start_offset=offset):
                print("task-watchdog: %s" % line)
            # Resume where this run gave up, so the tail is delayed rather
            # than starved. A run that covered everything wraps to itself.
            if tasks_for_triage:
                save_triage_offset((offset + ran[0]) % len(tasks_for_triage))
            print("task-watchdog: %s" % format_ready_summary(
                read_day_plan_rows()))
        return code

    if "--audit-idempotency" in argv:
        tasks = read_registry()
        if tasks is None:
            print("task-watchdog: NO-DATA: the sbe task registry could not "
                  "be read; nothing here is a pass")
            return 2
        named = audit_idempotency(tasks)
        if not named:
            print("task-watchdog: idempotency audit: %d open task(s), every "
                  "done-check on the read-only allowlist, safe to re-run by "
                  "construction" % len(tasks))
            return 0
        for line in named:
            print("task-watchdog: %s" % line)
        print("task-watchdog: idempotency audit: %d of %d done-check(s) "
              "named; a named check is not proven unsafe, it is unproven "
              "safe" % (len(named), len(tasks)))
        return 1

    tasks = read_registry()
    if tasks is None:
        print("task-watchdog: NO-DATA: the sbe task registry could not be "
              "read; nothing here is a pass")
        return finish(2, [])

    lane_findings = check_lane_cap()  # WD-03: independent of the registry

    if not tasks:
        if not lane_findings:
            print("task-watchdog: 0 open tasks, nothing to watch, cost "
                  "nothing")
            return finish(0, tasks)
        for f in lane_findings:
            print("task-watchdog: %s" % f)
        print("task-watchdog: %d finding(s) over 0 open task(s); each names "
              "its unlock, none was executed" % len(lane_findings))
        return finish(1, tasks)

    dirty = dirty_paths()
    if dirty is None:
        print("task-watchdog: NO-DATA: git status failed; nothing here is "
              "a pass")
        return finish(2, tasks)

    findings = examine(tasks, dirty, run_verify=live_verify,
                       stale_hours=stale_hours)
    state = load_quarantine_state()
    findings = apply_quarantine(findings, tasks, state)  # WD-01
    save_quarantine_state(state)
    findings = findings + lane_findings
    healthy = len(tasks) - sum(1 for f in findings if not f.startswith("DRIFT"))
    if not findings:
        print("task-watchdog: %d open task(s), all healthy; silence per "
              "condition means healthy" % len(tasks))
        return finish(0, tasks, state)
    for f in findings:
        print("task-watchdog: %s" % f)
    print("task-watchdog: %d finding(s) over %d open task(s); each names its "
          "unlock, none was executed" % (len(findings), len(tasks)))
    return finish(1, tasks, state)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""LIMIT-01: make usage-limit detection RESIDENT.

limit_watch.py classifies one transcript's last record and, in --arm mode,
schedules a restart -- but nothing invokes it on a cadence. A session hit its
account usage limit at 07:54 and idled 79 minutes because a one-shot check
that nobody re-runs detects nothing between runs. This module is the resident
half: tick() does what a person running limit_watch.py by hand every few
minutes would do, and --print-plist emits the launchd unit that runs tick()
on a cadence.

WHAT TICK() DOES, in order:
  1. finds the newest session transcript across this repo's own Brother
     project directories under ~/.claude/projects (main checkout plus any
     worktree, same prefix convention as scripts/cost_per_unit.py's
     BROTHER_PROJECT_PREFIX -- mirrored here rather than imported, since
     cost_per_unit.py does not export it as a public constant);
  2. classifies its last record with limit_watch.classify() (imported, not
     shelled out: classify(), read_last_record(), newest_transcript() and
     arm() were already public functions, so no seam was needed in
     limit_watch.py for this unit);
  3. on a real limit class (not NORMAL, not NO-DATA), calls limit_watch.arm()
     exactly once per LIMIT EPISODE. An episode is identified by
     (session id, class, reset time) when a reset time exists (seven_day),
     or (session id, class, the record's own "timestamp" field) when it does
     not (five_hour, monthly-spend, fallback-model all carry resets_at=None
     per limit_watch.py's own measured contract, so resets_at alone cannot
     tell two real rejections apart). The last-armed key is kept in a small
     state file so a second tick on the same episode, or a tick that starts
     after a missed one, arms nothing.

FAILURE DIRECTION (named per the estate's own contingency law): an
unreadable or corrupt transcript, or an unreadable-but-present and corrupt
state file, is NO-DATA. tick() prints it, arms nothing, and never crashes --
the launchd cadence would otherwise die on the first bad read and the whole
point of being resident is lost. A state file that is simply ABSENT is not
corrupt: it means no episode has been armed yet, and tick() proceeds.

RUN_DIR, the ponytail simplification: arm() needs a run directory to build
its resume instruction. This resident monitor is not tied to one particular
brother_run.py invocation (that would need reversing a project directory's
slug back to a cwd, which is lossy), so it defaults to this repo's own root.
The resume command then names the repo root rather than a specific run;
that is honest, not fabricated, and is exactly what the docstring says.
# ponytail: run_dir defaults to REPO_ROOT rather than resolving a specific
# in-flight run. Upgrade path: pass --run-dir, or wire
# brother_run.find_unfinished_runs(REPO_ROOT, cwd) once a specific run needs
# to be resolved automatically.

FLAG PATH, FIXED 2026-09-18: tick() used to default flag_path to
limit_watch.DEFAULT_FLAG_PATH, the single shared armed.flag, so two runs
polled by the same resident monitor could silently overwrite each other's
resume prompt. That constant is gone. tick() now leaves flag_path/run_id
untouched and hands both to limit_watch.arm(), which resolves the per-run
slot armed.d/<run_id>.flag itself and REFUSES (arms nothing) rather than
falling back to a shared flag when no run id is known. See RUN_DIR above:
this monitor is not tied to one run either, so run_id is an optional
pass-through (--run-id, or the caller's environment), refused rather than
guessed when absent.

Exit 0 on any tick, including NO-DATA and NORMAL (nothing to act on is not a
failure). No em or en dashes.
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_watch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")
DEFAULT_STATE_PATH = os.path.expanduser(
    "~/.claude/state/limit-monitor/state.json")

#: mirrors scripts/cost_per_unit.py's BROTHER_PROJECT_PREFIX: Claude Code
#: slugifies a session's cwd by replacing "/" with "-", so this repo's
#: checkout and every worktree nested under it share this prefix.
BROTHER_PROJECT_PREFIX = "-Users-khalil-maaouni-Brother"

MONITOR_PLIST_LABEL = "com.brother.limit-monitor"
MONITOR_START_INTERVAL = 300

MONITOR_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%(label)s</string>
  <key>ProgramArguments</key>
  <array>
    <string>python3</string>
    <string>%(script_path)s</string>
    <string>--tick</string>
  </array>
  <key>StartInterval</key><integer>%(interval)d</integer>
</dict>
</plist>
"""


def _brother_project_dirs(projects_root):
    """Subdirectories of `projects_root` that are this repo's own project
    slug or one of its worktrees. [] (never raises) when projects_root does
    not exist, which newest_known_transcript() below folds into NO-DATA."""
    try:
        names = os.listdir(projects_root)
    except OSError:  # sbe: allow-silent an absent projects root means no transcript exists, the documented NO-DATA case
        return []
    return [os.path.join(projects_root, n) for n in names
            if n == BROTHER_PROJECT_PREFIX
            or n.startswith(BROTHER_PROJECT_PREFIX + "-")]


def newest_known_transcript(projects_root=DEFAULT_PROJECTS_ROOT):
    """The most recently modified *.jsonl across every Brother project
    directory this machine knows about, or None. Reuses
    limit_watch.newest_transcript() per directory rather than
    reimplementing the *.jsonl scan."""
    candidates = [t for t in
                  (limit_watch.newest_transcript(d)
                   for d in _brother_project_dirs(projects_root))
                  if t]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _episode_key(record, classification):
    """A string identifying ONE limit rejection, stable across ticks for
    the same rejection and different across two real rejections. Uses the
    measured reset time when the class carries one (seven_day); falls back
    to the record's own "timestamp" field for the three classes that carry
    resets_at=None on every measured record (five_hour, monthly-spend,
    fallback-model), since resets_at alone cannot tell those apart."""
    session_id = record.get("sessionId") or record.get("uuid") or "unknown-session"
    marker = classification.get("resets_at")
    if marker is None:
        marker = record.get("timestamp")
    return "%s|%s|%s" % (session_id, classification.get("class"), marker)


def _load_state(path):
    """(state_dict, corrupt). An absent file is {} / not corrupt: no
    episode has been armed yet. A present-but-unparseable file is {} /
    corrupt, and never raises."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:  # sbe: allow-silent a missing state file is the documented "nothing armed yet" case
        return {}, False
    try:
        data = json.loads(raw)
    except ValueError:  # sbe: allow-silent a corrupt state file is the documented fail-closed case, never a crash
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data, False


def _save_state(path, data):
    """Atomic write (temp file + os.replace) so a killed tick never leaves
    the next one reading a half-written state file."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".limit-monitor-",
                                     suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:  # sbe: allow-silent best-effort cleanup of our own temp file, never masks the real failure being raised
            pass
        raise


def _result(cls, armed, reason, classification=None, arm_result=None):
    return {"class": cls, "armed": armed, "reason": reason,
            "classification": classification, "arm_result": arm_result}


def tick(projects_root=None, state_path=None, run_dir=None, flag_path=None,
         run_id=None, until=None, run_plan=None, margin=120, schedule_fn=None,
         pack_fn=None, arm_fn=None):
    """One resident check. Never raises: any unreadable/corrupt transcript
    or state file is NO-DATA, arms nothing. Returns a dict; see _result().

    RUN_ID, the ponytail simplification paired with RUN_DIR above: this
    monitor scans every Brother project directory, not one particular run,
    so it has no run of its own to name. run_id (or its absence) passes
    straight through to arm_fn/limit_watch.arm(), which resolves it against
    the run_id argument or BROTHER_RUN_ID and REFUSES (armed False, arms
    nothing) rather than falling back to the old shared armed.flag when
    neither is set. Passing an explicit flag_path (as every test here
    does) bypasses run_id resolution entirely.

    UNTIL/RUN_PLAN, added 2026-09-18 with limit_watch.arm()'s own expiry
    fix: pass straight through the same way. With no flag_path override,
    arm() also refuses (armed False) when neither until nor a readable,
    not-yet-expired run_plan is available, since restart.sh disarms any
    per-run slot whose .until is missing or past."""
    projects_root = projects_root or DEFAULT_PROJECTS_ROOT
    state_path = state_path or DEFAULT_STATE_PATH
    run_dir = run_dir or REPO_ROOT
    arm_fn = arm_fn or limit_watch.arm

    path = newest_known_transcript(projects_root)
    if not path:
        return _result("NO-DATA", False,
                        "NO-DATA: no session transcript found under %s"
                        % projects_root)

    record = limit_watch.read_last_record(path)
    if record is None:
        return _result("NO-DATA", False,
                        "NO-DATA: transcript unreadable or empty: %s" % path)

    classification = limit_watch.classify(record)
    cls = classification["class"]
    if cls in ("NORMAL", "NO-DATA"):
        return _result(cls, False, "nothing to arm: class is %s" % cls,
                        classification=classification)

    state, corrupt = _load_state(state_path)
    if corrupt:
        return _result(cls, False,
                        "NO-DATA: state file %s is corrupt, arms nothing"
                        % state_path, classification=classification)

    episode_key = _episode_key(record, classification)
    if state.get("last_armed_episode") == episode_key:
        return _result(cls, False, "already armed for this episode",
                        classification=classification)

    try:
        arm_result = arm_fn(run_dir, classification, flag_path=flag_path,
                             run_id=run_id, until=until, run_plan=run_plan,
                             margin=margin, schedule_fn=schedule_fn,
                             pack_fn=pack_fn)
    except OSError as e:  # sbe: allow-silent the arm path's own documented failure surface (flag/plist write); never crashes the resident cadence
        return _result(cls, False,
                        "NO-DATA: arm failed: %s" % e,
                        classification=classification)

    # FIXED 2026-09-18: this used to report armed=True and mark the episode
    # handled in state on ANY non-raising arm_fn call, never reading
    # arm_result["armed"] at all. arm() could only return armed=False for
    # NORMAL/NO-DATA, already filtered out above, so the bug was invisible
    # until arm() gained a real refusal for a real limit class (no run id
    # known). A refusal here must not be recorded as an armed episode: the
    # next tick has to try again, not believe this one already succeeded.
    if not arm_result.get("armed"):
        return _result(cls, False, arm_result.get("reason") or "arm refused",
                        classification=classification, arm_result=arm_result)

    state["last_armed_episode"] = episode_key
    state["last_armed_at"] = time.time()
    _save_state(state_path, state)
    return _result(cls, True, "armed", classification=classification,
                   arm_result=arm_result)


def render_monitor_plist(script_path=None, label=MONITOR_PLIST_LABEL,
                          interval=MONITOR_START_INTERVAL):
    script_path = script_path or os.path.abspath(__file__)
    return MONITOR_PLIST_TEMPLATE % {
        "label": label, "script_path": script_path, "interval": interval}


def main(argv):
    if "--print-plist" in argv:
        print(render_monitor_plist())
        return 0

    if "--tick" in argv:
        run_id = None
        until = None
        run_plan = None
        if "--run-id" in argv:
            idx = argv.index("--run-id")
            if idx + 1 < len(argv):
                run_id = argv[idx + 1]
        if "--until" in argv:
            idx = argv.index("--until")
            if idx + 1 < len(argv):
                until = int(argv[idx + 1])
        if "--run-plan" in argv:
            idx = argv.index("--run-plan")
            if idx + 1 < len(argv):
                run_plan = argv[idx + 1]
        result = tick(run_id=run_id, until=until, run_plan=run_plan)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0

    print("limit_monitor: usage: --tick [--run-id ID] [--until EPOCH] "
         "[--run-plan PATH] | --print-plist")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

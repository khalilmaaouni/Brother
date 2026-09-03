#!/usr/bin/env python3
"""R25 as a runnable harness: drive a session into each of the three limit
classes and measure the three things the row's own done-check names.

The done-check reads: "a session driven into each of the three limit classes
(session ceiling, daily ceiling, simulated account limit) pauses itself, runs
the ceremony, and the restart resumes the same work with zero re-decisions".
Each of those three is measured here, per class, against the REAL machinery:

  PAUSED    the session ceiling and the daily ceiling are the spend guard's
            own dimensions, so the pause is spend_guard.handle_pre_tool_use()
            returning permissionDecision "deny" on a dispatch. The account
            limit is a 429 the API itself returns, so the pause is
            limit_watch.classify() putting the transcript's last record in a
            limit class rather than NORMAL.
  CEREMONY  handover_ceremony.main() is invoked exactly as a closing session
            invokes it, with --collect --emit-handover --limit-state, and the
            emitted markdown must carry the priority sections.
  RESUME    limit_watch.arm() writes the restart flag; the drill then reads
            that flag back, resolves the run directory it names through
            brother_run's OWN resolver (_find_work_doc, the function
            `brother_run --resume` uses), and compares the run's decisions
            against the snapshot taken before the pause. Zero re-decisions is
            therefore a comparison, not a claim. WHAT IT DOES NOT PROVE, said
            here rather than left for a reader to discover: the drill resumes
            the run's own record, it does not launch a fresh session, so this
            leg is a regression guard that nothing in the pause, ceremony or
            arm path rewrites the interrupted run's decisions. It is not
            evidence about what a live restarted session would ask.

DRIVEN BOTH WAYS. Every class also runs a control under the same harness that
must NOT pause: spend under the ceiling, a daily total under the ceiling, an
ordinary assistant record. A drill that can only report a pause proves
nothing, so a control that pauses fails the class just as a scenario that
does not.

NOTHING LIVE IS TOUCHED. Every run happens against fixtures in a temporary
directory. The spend guard is imported for its contract and then has its
CONFIG, LEDGER, CACHE_DIR and PROJECTS repointed at fixture paths, which
assert_isolated() then checks BEFORE the first call: any constant still
pointing inside ~/.claude, or anywhere outside the fixture directory, refuses
the run outright. So the guard's real config, real telemetry ledger and real
measurement cache are never read and never written. This drill never opens
~/.claude/spend-guard.json at all, not even to hash it.

Exit 0 when every class passes, 1 when any class fails, 2 (NO-DATA, never a
pass) when a class could not be driven at all. No em or en dashes.
"""

import contextlib
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brother_run
import handover_ceremony
import limit_watch

SPEND_GUARD = os.path.expanduser("~/.claude/hooks/spend_guard.py")

HARD_OUT = 600000
DAILY_HARD_OUT = 2000000

# The decisions the interrupted run had already taken. The whole point of the
# restart leg is that these come back unchanged, so they are the fixture.
DECISIONS = [
    {"id": "D1", "question": "which host holds the branch", "choice": "hub"},
    {"id": "D2", "question": "who merges", "choice": "the orchestrator"},
]

HANDOVER_SECTIONS = ("# START HERE", "## Uncommitted work", "## Open sbe tasks",
                     "## Day-plan ready set", "## Open pull requests")


# --------------------------------------------------------------------------
# Read-only handling of the live guard
# --------------------------------------------------------------------------

LIVE_DIR = os.path.expanduser("~/.claude") + os.sep


def assert_isolated(mod, fixture_dir):
    """Prove, in process and before any call, that the guard cannot reach a
    live path. Raises RuntimeError naming the offending constant otherwise.

    This is deliberately an assertion about where the guard POINTS, not a
    hash of the live config taken afterwards. Two reasons. A hash has to read
    the live file, which this drill is not allowed to do at all; and a hash
    compares machine state, so an unrelated edit by the founder would turn
    the battery red for a reason that has nothing to do with the code. Where
    the constants point is entirely the drill's own doing, so it is the thing
    the drill can honestly assert."""
    for name in ("CONFIG", "LEDGER", "CACHE_DIR", "PROJECTS"):
        value = os.path.abspath(getattr(mod, name))
        if value.startswith(LIVE_DIR):
            raise RuntimeError(
                "REFUSING TO RUN: the guard's %s still points inside the live "
                "%s (%s). A drill that touched a live brake to prove the brake "
                "works would be a catastrophe, not a proof."
                % (name, LIVE_DIR, value))
        if not value.startswith(os.path.abspath(fixture_dir) + os.sep):
            raise RuntimeError(
                "REFUSING TO RUN: the guard's %s (%s) is outside the fixture "
                "directory %s" % (name, value, fixture_dir))


def load_guard(fixture_dir):
    """Import the live guard for its contract, then immediately repoint every
    path constant it reads or writes at fixture paths. Returns None when the
    guard is not installed, which is the class's NO-DATA case.

    Repointing works because the guard reads CONFIG, LEDGER and CACHE_DIR
    inside its function bodies, never as default arguments bound at
    definition time."""
    if not os.path.isfile(SPEND_GUARD):
        return None
    spec = importlib.util.spec_from_file_location("spend_guard_drill", SPEND_GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CONFIG = os.path.join(fixture_dir, "spend-guard.json")
    mod.LEDGER = os.path.join(fixture_dir, "ledger.jsonl")
    mod.CACHE_DIR = os.path.join(fixture_dir, "spend-cache")
    mod.PROJECTS = os.path.join(fixture_dir, "projects")
    assert_isolated(mod, fixture_dir)
    return mod


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def write_run_dir(root):
    """A run directory in brother_run's own shape: exactly one Work document
    beside target.json, so brother_run's resolver can find it."""
    run_dir = os.path.join(root, "run-limit-drill")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "work.json"), "w", encoding="utf-8") as f:
        json.dump({"outcome": "the work the limit interrupted",
                   "decisions": DECISIONS}, f)
    with open(os.path.join(run_dir, brother_run.TARGET_FILENAME), "w",
              encoding="utf-8") as f:
        json.dump({"cwd": root}, f)
    return run_dir


def write_spend_config(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"default": {"soft_out": 400000, "hard_out": HARD_OUT,
                               "daily_soft_out": 1600000,
                               "daily_hard_out": DAILY_HARD_OUT}}, f)


def write_session_transcript(path, output_tokens):
    """One assistant record carrying usage, which is all the guard's
    measure() reads."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant",
            "message": {"id": "drill-msg-1",
                        "usage": {"output_tokens": output_tokens,
                                  "cache_read_input_tokens": 0}},
        }) + "\n")


def write_ledger(path, project, totals):
    """One row per session id, each carrying that session's cumulative total
    (the ledger's as-flushed basis), timestamped now so it falls inside the
    guard's 24 hour window."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        for i, total in enumerate(totals):
            f.write(json.dumps({"project": project,
                                "session_id": "drill-ledger-%d" % i,
                                "ts": now,
                                "gen_ai.usage.output_tokens": total}) + "\n")


def api_limit_record(text, quota_limits=None):
    """The measured shape of every real rate_limit rejection, copied from the
    fixtures limit_watch.py itself was built against."""
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]},
            "quotaLimits": quota_limits, "error": "rate_limit",
            "isApiErrorMessage": True, "apiErrorStatus": 429}


def write_records(path, record):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------
# The three measurements, shared by every class
# --------------------------------------------------------------------------

def run_ceremony(root, limit_result):
    """Invoke the ceremony exactly as a closing session does. Returns
    (ran, note). `ran` is True only when the handover markdown was written
    AND carries every priority section: the ceremony's exit code is not the
    test, because a NO-DATA from one collector is its documented contract and
    does not mean the ceremony failed to run."""
    limit_json = os.path.join(root, "limit-state.json")
    with open(limit_json, "w", encoding="utf-8") as f:
        json.dump(limit_result, f)
    handover = os.path.join(root, "START-HERE.md")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = handover_ceremony.main(
            ["--collect", "--repo", os.path.dirname(HERE),
             "--emit-handover", handover, "--limit-state", limit_json])
    if not os.path.isfile(handover):
        return False, "no handover written (ceremony exit %d)" % code
    with open(handover, encoding="utf-8") as f:
        text = f.read()
    missing = [s for s in HANDOVER_SECTIONS if s not in text]
    if missing:
        return False, "handover missing section(s): %s" % ", ".join(missing)
    collected = buf.getvalue()
    if "limit_state" not in collected:
        return False, "the collected state does not carry limit_state"
    note = "handover written, all %d sections" % len(HANDOVER_SECTIONS)
    if "NO-DATA" in collected:
        note += "; one or more collectors reported NO-DATA (their own contract)"
    return True, note


def run_restart(root, run_dir, limit_result, snapshot):
    """Arm the restart against fixture paths, then read the flag back and
    resume from it. Returns (resumed, note). Zero re-decisions is measured by
    comparing the resumed run's decisions against the snapshot taken before
    the pause."""
    flag = os.path.join(root, "armed.flag")
    plist = os.path.join(root, "restart.plist")
    armed = limit_watch.arm(
        run_dir, limit_result, flag_path=flag,
        # reload_fn=None: the drill writes a fixture plist and never calls
        # launchctl, so the machine's real restart schedule is untouched.
        schedule_fn=lambda resets_at, margin: limit_watch.restart_schedule.schedule(
            resets_at, margin=margin, plist_path=plist, reload_fn=None),
        pack_fn=lambda roots: (os.path.join(root, "pack.zip"), False))
    if not armed.get("armed"):
        return False, "arm refused: %s" % armed.get("reason")
    if not os.path.isfile(flag):
        return False, "arm reported armed but wrote no flag"

    with open(flag, encoding="utf-8") as f:
        instruction = f.read().strip()
    if os.path.abspath(run_dir) not in instruction:
        return False, "the restart flag does not name the interrupted run"

    # Resume through brother_run's OWN resolver, the one --resume uses.
    doc = brother_run._find_work_doc(os.path.abspath(run_dir))
    if doc is None:
        return False, "brother_run could not resolve a Work document to resume"
    with open(doc, encoding="utf-8") as f:
        resumed = json.load(f)
    if resumed.get("decisions") != snapshot:
        return False, ("decisions changed across the restart: %r became %r"
                       % (snapshot, resumed.get("decisions")))

    sched = armed.get("schedule") or {}
    if sched.get("scheduled"):
        note = "resumed %d decision(s) unchanged; restart scheduled for %s" % (
            len(snapshot), sched.get("fire_local"))
    else:
        # Honest, not hidden: a class whose limit carries no reset time cannot
        # be woken by a timer. The flag still resumes the work when a session
        # next starts; only the automatic wake is missing.
        note = ("resumed %d decision(s) unchanged; NO automatic wake (%s), so "
                "the restart is flag-driven only" % (len(snapshot),
                                                     sched.get("error")))
    return True, note


# --------------------------------------------------------------------------
# The three classes
# --------------------------------------------------------------------------

def _dispatch(guard, transcript, cwd, session_id):
    """One dispatch through the guard's real PreToolUse handler. A deny is
    the pause."""
    payload = {"tool_name": "Task", "transcript_path": transcript,
               "session_id": session_id, "cwd": cwd}
    out = guard.handle_pre_tool_use(payload)
    if not out:
        return False, "allowed"
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    return decision == "deny", decision or "no decision"


def drill_guard_class(root, name, over_tokens, under_tokens, ledger_over,
                      ledger_under):
    """The session ceiling and the daily ceiling differ only in which number
    is pushed over, so they share one driver."""
    guard = load_guard(root)
    if guard is None:
        return {"class": name, "verdict": "NO-DATA",
                "needs": "the spend guard at %s; it is not installed, and the "
                         "drill will not simulate a brake it cannot read"
                         % SPEND_GUARD}
    write_spend_config(guard.CONFIG)
    project_dir = os.path.join(root, "DrillProject")
    os.makedirs(project_dir, exist_ok=True)

    over = os.path.join(root, "over.jsonl")
    under = os.path.join(root, "under.jsonl")
    write_session_transcript(over, over_tokens)
    write_session_transcript(under, under_tokens)

    write_ledger(guard.LEDGER, "DrillProject", ledger_over)
    paused, how = _dispatch(guard, over, project_dir, "drill-over")

    write_ledger(guard.LEDGER, "DrillProject", ledger_under)
    control_paused, control_how = _dispatch(guard, under, project_dir,
                                            "drill-under")

    run_dir = write_run_dir(root)
    snapshot = json.loads(json.dumps(DECISIONS))
    # A spend-guard refusal carries no reset time: only a fresh session (or,
    # for the daily ceiling, tomorrow) clears it. resets_at is therefore null
    # by measurement, never a guessed timer.
    result = {"class": name, "resets_at": None, "message_url": None,
              "raw_text_excerpt": how, "remedy": "hand over and restart"}
    ceremony, ceremony_note = run_ceremony(root, result)
    resumed, resume_note = run_restart(root, run_dir, result, snapshot)

    return _verdict(name, paused, how, control_paused, control_how,
                    ceremony, ceremony_note, resumed, resume_note)


def drill_account_limit(root):
    """The simulated account limit: a 429 rate_limit record of the shape
    measured on this machine, classified by the real limit_watch."""
    over = os.path.join(root, "limit.jsonl")
    under = os.path.join(root, "normal.jsonl")
    write_records(over, api_limit_record(
        "You've hit your weekly limit, resets Sep 3 at 4am (Asia/Tokyo)",
        quota_limits={"rateLimitType": "seven_day",
                      "resetsAt": int(datetime.datetime.now(
                          datetime.timezone.utc).timestamp()) + 3600}))
    write_records(under, {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "ordinary turn, no limit"}]}})

    result = limit_watch.watch(transcript_path=over)
    paused = result["class"] not in ("NORMAL", "NO-DATA")
    how = result["class"]

    control = limit_watch.watch(transcript_path=under)
    control_paused = control["class"] not in ("NORMAL", "NO-DATA")
    control_how = control["class"]
    # Second control on the same leg: arm() must refuse a NORMAL result, so a
    # pause cannot be manufactured downstream of the classifier either.
    if limit_watch.arm(root, control,
                       flag_path=os.path.join(root, "must-not-exist.flag")
                       ).get("armed"):
        control_paused = True
        control_how = "NORMAL but arm() still armed"

    run_dir = write_run_dir(root)
    snapshot = json.loads(json.dumps(DECISIONS))
    ceremony, ceremony_note = run_ceremony(root, result)
    resumed, resume_note = run_restart(root, run_dir, result, snapshot)

    return _verdict("account-limit", paused, how, control_paused, control_how,
                    ceremony, ceremony_note, resumed, resume_note)


def _verdict(name, paused, how, control_paused, control_how, ceremony,
             ceremony_note, resumed, resume_note):
    legs = {
        "paused": (paused, "dispatch %s" % how if paused
                   else "NOT paused (%s)" % how),
        "control-did-not-pause": (not control_paused,
                                  "control %s" % control_how),
        "ceremony": (ceremony, ceremony_note),
        "restart-resumes": (resumed, resume_note),
    }
    ok = all(v[0] for v in legs.values())
    return {"class": name, "verdict": "PASS" if ok else "FAIL",
            "legs": {k: {"ok": v[0], "note": v[1]} for k, v in legs.items()}}


CLASSES = ("session-ceiling", "daily-ceiling", "account-limit")


def drill():
    results = []
    for name in CLASSES:
        with tempfile.TemporaryDirectory(prefix="limit-drill-") as root:
            if name == "session-ceiling":
                results.append(drill_guard_class(
                    root, name, over_tokens=HARD_OUT + 1, under_tokens=1000,
                    ledger_over=[1000], ledger_under=[1000]))
            elif name == "daily-ceiling":
                results.append(drill_guard_class(
                    root, name, over_tokens=1000, under_tokens=1000,
                    ledger_over=[DAILY_HARD_OUT // 2 + 1,
                                 DAILY_HARD_OUT // 2 + 1],
                    ledger_under=[1000, 1000]))
            else:
                results.append(drill_account_limit(root))
    return results


def main(argv):
    print("limit-drill: R25, three limit classes, fixtures only")
    print("isolation: the guard's CONFIG, LEDGER, CACHE_DIR and PROJECTS are "
          "repointed into a temporary directory and asserted to be outside "
          "%s before any call" % LIVE_DIR)
    print("scope: restart-resumes compares the interrupted run's own record "
          "across the pause; it does not launch a session, so it is a "
          "regression guard, not evidence about a live restart")
    print("")

    results = drill()
    for r in results:
        print("%-16s %s" % (r["class"], r["verdict"]))
        if "needs" in r:
            print("    NO-DATA needs: %s" % r["needs"])
        for leg, v in (r.get("legs") or {}).items():
            print("    %-22s %-4s %s" % (leg, "ok" if v["ok"] else "FAIL",
                                         v["note"]))
        print("")

    failed = [r["class"] for r in results if r["verdict"] == "FAIL"]
    nodata = [r["class"] for r in results if r["verdict"] == "NO-DATA"]
    print("pass %d   fail %d   no-data %d"
          % (len(results) - len(failed) - len(nodata), len(failed), len(nodata)))
    if failed:
        print("FAILED: %s" % " ".join(failed))
        return 1
    if nodata:
        print("NO-DATA: %s  (not a pass, and not a failure)" % " ".join(nodata))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

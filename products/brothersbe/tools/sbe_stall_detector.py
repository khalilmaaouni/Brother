#!/usr/bin/env python3
"""Stall detector v2: nine conditions, report-only, every alert names its fix.

Replaces the first-generation watchdog and the one-night bash draft. Python
because the two host traps that made the draft lie were bash-native (`find
-newermt` rejected with stderr discarded; `grep -c || echo 0` yielding a
two-line value), and stdlib-only like every other tool here.

THE CONTRACT, from docs/specs/2026-08-11-stall-detector-v2-design.md:
- Report-only. This process changes nothing: no fence is released, no patch
  applied, no worktree removed. Every alert names the exact remediation and a
  human or the orchestrating session runs it. A monitor writing to a control
  is a control defect, not a feature.
- Silence must mean healthy. Each condition re-arms only after a healthy
  pass, so one episode is one line, never a stream.
- Fence counts come from the fence hook's OWN `fences` subcommand, never a
  private grep of the registry: two parsers of one format disagreeing
  silently for hours is mistake 6 of 2026-08-10.
- Liveness names its answerer (heartbeat ledger first, process table second,
  per the ratified two-source design) so a rotted pgrep pattern is visible
  instead of silently load-bearing.
- Inputs may be injected for tests (paths, commands, thresholds); verdict
  logic is never stubbed. A condition that has never been seen firing does
  not ship: tools/test_stall_detector.py forces all nine live.

Usage: python3 tools/sbe_stall_detector.py [--root DIR] [--once] ...
Alerts go to stdout AND .sbe/runtime/alerts.jsonl; diagnostics to stderr.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

GIB = 1024 ** 3

#: Process-table patterns that mean a verification or build is genuinely in
#: progress. The second liveness source; the heartbeat ledger answers first.
BUSY_PATTERNS = "evals/|tools/sbe_|tools/test_|pytest|python3 .*test_"

#: Directories never walked for the recent-change probe: .git is not work,
#: .sbe holds this detector's own ledgers (a detector that counts its own
#: output as activity can never see a stall), and dead workers' worktrees
#: must not read as main-tree activity.
PRUNE_DIRS = (".git", ".sbe", "__pycache__", "node_modules", ".claude")

BANNER_CLASSES = ("DEAD_WORKER_HOLDING_WORK", "DISK_FLOOR", "HANDOVER_OWED")


def one_line(text):
    """Flatten to exactly one physical line. Every stdout report line goes
    through this choke point, because this tool's stdout is parsed (by its
    tests and by the session forwarding alerts), and a value carrying a
    newline would forge a second report line. The rule is the choke point,
    not the values."""
    return " ".join(str(text).split())


def say(text):
    sys.stdout.write(one_line(text) + "\n")
    sys.stdout.flush()


def warn(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


class Liveness(object):
    """Deliberately an object and not a (busy, source) 2-tuple: the honesty
    meta-test reads a 2-tuple return as a possible verdict source, and this
    is a probe result, not a check verdict. Same reasoning as the fence
    hook's Decision."""

    def __init__(self, busy, source):
        self.busy = busy
        self.source = source


class Config(object):
    def __init__(self, ns):
        self.root = os.path.abspath(ns.root)
        self.stall_min = ns.stall_min
        self.dirty_min = ns.dirty_min
        self.disk_floor_gib = ns.disk_floor_gib
        self.disk_free_gib_override = getattr(ns, "disk_free_gib_override", None)
        self.context_warn_bytes = ns.context_warn_bytes
        self.poll_seconds = ns.poll_seconds
        self.heartbeat = ns.heartbeat or os.path.join(
            self.root, ".sbe", "runtime", "heartbeat.jsonl")
        self.alerts = ns.alerts or os.path.join(
            self.root, ".sbe", "runtime", "alerts.jsonl")
        self.spend_ledger = ns.spend_ledger
        self.worktrees = ns.worktrees or os.path.join(
            self.root, ".claude", "worktrees")
        # The fence hook ships BESIDE this detector, not necessarily inside
        # the watched root: a portable watcher pointed at a foreign estate
        # still asks BrotherSBE's own parser about that estate's registry.
        self.fence_cmd = ns.fence_cmd or (
            "%s %s fences %s" % (
                json.dumps(sys.executable),
                json.dumps(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "sbe_fence_hook.py")),
                json.dumps(self.root)))
        self.pgrep_patterns = ns.pgrep_patterns
        self.banners = ns.banners
        self.repeat_fail_count = 5
        self.api_streak_count = 4
        self.handover_dir = ns.handover_dir or os.path.join(
            self.root, "docs", "handover")
        self.handover_grace_min = ns.handover_grace_min
        self.handover_max_steps = 4

    def describe(self):
        return ("config: root=%s stall_min=%d dirty_min=%d disk_floor_gib=%d "
                "context_warn_bytes=%d poll=%ds heartbeat=%s spend_ledger=%s "
                "(thresholds measured on one estate; every firing is logged "
                "for retuning from data)"
                % (self.root, self.stall_min, self.dirty_min,
                   self.disk_floor_gib, self.context_warn_bytes,
                   self.poll_seconds, self.heartbeat,
                   self.spend_ledger or "NONE (TOKEN_BURN reports NO-DATA)"))


class Alert(object):
    def __init__(self, condition, message, remediation, source):
        self.condition = condition
        self.message = message
        self.remediation = remediation
        self.source = source

    def line(self):
        return "%s: %s Remediation: %s [liveness source: %s]" % (
            self.condition, self.message, self.remediation, self.source)


# ---------------------------------------------------------------------------
# Input probes. Each returns data plus the name of what answered, and any
# probe failure is LOUD on stderr, never discarded: a monitor that cannot
# fail loudly is a monitor that lies quietly.
# ---------------------------------------------------------------------------

def read_heartbeats(cfg, max_lines=4000):
    try:
        with open(cfg.heartbeat) as fh:
            lines = fh.readlines()[-max_lines:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:  # sbe: allow-silent a corrupt ledger line is skipped so one bad write cannot blind the whole monitor; liveness still reads the good lines
            continue
        if isinstance(row, dict) and isinstance(row.get("epoch"), int):
            out.append(row)
    return out


def latest_heartbeat_epoch(beats, session=None):
    epochs = [b["epoch"] for b in beats
              if session is None or b.get("session") == session]
    return max(epochs) if epochs else None


def process_table_busy(cfg):
    try:
        done = subprocess.run(["pgrep", "-f", cfg.pgrep_patterns],
                              capture_output=True, text=True, timeout=15)
        return done.returncode == 0
    except Exception as e:
        warn("stall-detector: pgrep probe failed (%s); process-table liveness "
             "unavailable this pass" % e)
        return False


def liveness(cfg, beats, now):
    """A Liveness. The heartbeat ledger answers first, the process table
    second, and the caller records which one it was."""
    latest = latest_heartbeat_epoch(beats)
    if latest is not None and now - latest < cfg.stall_min * 60:
        return Liveness(True, "heartbeat")
    if process_table_busy(cfg):
        return Liveness(True, "process-table")
    if latest is not None:
        return Liveness(False, "heartbeat")
    return Liveness(False, "none")


def fence_count_and_sessions(cfg):
    """(count, sessions, ok). Asks the fence hook itself; never greps the
    registry privately. On any failure returns (0, [], False) and says why."""
    try:
        # shlex, never shell=True: the fence command is operator config, but a
        # config string that reaches a shell is one config file away from
        # injection, and nothing here needs shell features.
        done = subprocess.run(shlex.split(cfg.fence_cmd), capture_output=True,
                              text=True, timeout=30)
    except Exception as e:
        warn("stall-detector: fence probe failed to run (%s); fence-dependent "
             "conditions are NO-DATA this pass" % e)
        return 0, [], False
    text = done.stderr + done.stdout
    m = re.search(r"(\d+) live fence line\(s\)", text)
    if m is None and "no fence is enforceable here" in text:
        # The hook says this plainly when it read a registry and found no
        # enforceable claim: that is a real ZERO, not an unreadable probe.
        # Treating it as NO-DATA would silence every fence condition on the
        # healthy case, which is the common case.
        return 0, [], True
    if not m:
        warn("stall-detector: fence probe output carried no count (exit %s); "
             "fence-dependent conditions are NO-DATA this pass. Last line: %s"
             % (done.returncode,
                text.strip().splitlines()[-1] if text.strip() else "(empty)"))
        return 0, [], False
    sessions = re.findall(r"\| session (\S+) \|", text)
    return int(m.group(1)), [s for s in sessions if s != "(none)"], True


def recent_change_epoch(cfg):
    """Newest mtime under root, pruned per PRUNE_DIRS. Returns epoch or None."""
    newest = None
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for name in filenames:
            try:
                ts = os.path.getmtime(os.path.join(dirpath, name))
            except OSError:  # sbe: allow-silent a file deleted mid-walk simply stops being evidence of recent change; nothing is decided on its absence
                continue
            if newest is None or ts > newest:
                newest = ts
    return newest


def git_dirty_paths(path):
    try:
        done = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                              capture_output=True, text=True, timeout=30)
        if done.returncode != 0:
            warn("stall-detector: git status failed in %s: %s"
                 % (path, done.stderr.strip()[:200]))
            return None
        return [ln[3:] for ln in done.stdout.splitlines() if len(ln) > 3]
    except Exception as e:
        warn("stall-detector: git probe failed in %s (%s)" % (path, e))
        return None


# ---------------------------------------------------------------------------
# The nine conditions. Each takes (cfg, state, now, probes) and returns an
# Alert or None. `state` carries the armed flags and once-per-session marks.
# ---------------------------------------------------------------------------

def cond_true_stall(cfg, state, now, p):
    quiet = (p["recent"] is None or now - p["recent"] > cfg.stall_min * 60)
    fired = quiet and not p["busy"] and p["fence_ok"] and p["fences"] > 0
    if fired:
        return Alert(
            "TRUE_STALL",
            "nothing changed for %dm, no verification running, %d fence(s) "
            "OPEN." % (cfg.stall_min, p["fences"]),
            "identify the fence owner in STATE.md; if its agent is gone, mark "
            "that line RELEASED with where the work stopped, then continue or "
            "re-dispatch.", p["liveness_source"])
    return None


def cond_dead_worker(cfg, state, now, p):
    holding = []
    if os.path.isdir(cfg.worktrees):
        for name in sorted(os.listdir(cfg.worktrees)):
            w = os.path.join(cfg.worktrees, name)
            if not os.path.isdir(w):
                continue
            if os.path.exists(os.path.join(w, ".salvaged")):
                # Salvaged work is deliberately NOT deleted (never-lose-work
                # outranks tidiness); the marker is how an acknowledged copy
                # stops being an alert, or the first real save mutes forever.
                continue
            dirty = git_dirty_paths(w)
            # This detector's OWN ledgers live under .sbe/runtime, and the
            # heartbeat hook writes them inside every worktree an agent runs
            # in. Counting them as the worker's unfinished work made the very
            # first armed run report a dead worker holding nothing but the
            # monitor's own output. A monitor whose output triggers itself
            # cries wolf forever and gets muted.
            dirty = [p for p in (dirty or []) if not p.startswith(".sbe/")]
            if not dirty:
                continue
            frag = name[len("agent-"):] if name.startswith("agent-") else name
            try:
                alive = subprocess.run(["pgrep", "-f", frag],
                                       capture_output=True, timeout=15
                                       ).returncode == 0
            except Exception:
                alive = False
            if not alive:
                holding.append("%s(%d)" % (name, len(dirty)))
    if holding:
        return Alert(
            "DEAD_WORKER_HOLDING_WORK",
            "%s hold uncommitted work with no live process." % " ".join(holding),
            "do NOT restart from zero. git -C <worktree> diff > salvage.patch, "
            "git apply --check it, inspect for weakened assertions, then apply "
            "and finish inline. Touch <worktree>/.salvaged once saved.",
            p["liveness_source"])
    return None


def cond_orphaned_fence(cfg, state, now, p):
    if not p["fence_ok"] or p["fences"] == 0 or p["busy"]:
        return None
    active = (p["recent"] is not None and now - p["recent"] < cfg.stall_min * 60)
    if not active:
        return None
    stale = []
    for s in p["fence_sessions"]:
        latest = latest_heartbeat_epoch(p["beats"], session=s)
        if latest is None or now - latest > cfg.stall_min * 60:
            stale.append(s)
    if stale or not p["fence_sessions"]:
        named = ", ".join(stale) if stale else "(fence lines name no session)"
        return Alert(
            "ORPHANED_FENCE",
            "%d fence(s) OPEN while files change, and these fence sessions "
            "show no recent heartbeat: %s." % (p["fences"], named),
            "confirm each fence owner is the session actually writing; a "
            "stale claim refuses the real writer and the hook's refusal will "
            "look like a bug.", p["liveness_source"])
    return None


def cond_ageing_uncommitted(cfg, state, now, p):
    dirty = p["root_dirty"]
    if not dirty:
        return None
    newest = None
    for rel in dirty:
        try:
            ts = os.path.getmtime(os.path.join(cfg.root, rel))
        except OSError:  # sbe: allow-silent a dirty file deleted before its mtime was read cannot age; the remaining dirty files still decide the alert
            continue
        if newest is None or ts > newest:
            newest = ts
    if newest is not None and now - newest > cfg.dirty_min * 60:
        return Alert(
            "AGEING_UNCOMMITTED",
            "%d file(s) uncommitted and untouched for over %dm."
            % (len(dirty), cfg.dirty_min),
            "commit on the working branch, even as explicit work in progress, "
            "so a crash cannot erase it.", p["liveness_source"])
    return None


def cond_disk_floor(cfg, state, now, p):
    if cfg.disk_free_gib_override is not None:
        free_gib = cfg.disk_free_gib_override
    else:
        try:
            free_gib = shutil.disk_usage(cfg.root).free // GIB
        except OSError as e:
            warn("stall-detector: disk probe failed (%s)" % e)
            return None
    if free_gib < cfg.disk_floor_gib:
        return Alert(
            "DISK_FLOOR",
            "%d GiB free, under the %d GiB floor."
            % (free_gib, cfg.disk_floor_gib),
            "clear derived data before any build; never place build output "
            "under Documents.", p["liveness_source"])
    return None


def cond_token_burn(cfg, state, now, p):
    if not cfg.spend_ledger:
        return None  # NO-DATA, said once at startup.
    try:
        ledger_m = os.path.getmtime(cfg.spend_ledger)
    except OSError:  # sbe: allow-silent a configured ledger that is absent right now means no spend evidence this pass, not an error worth a nightly stream of stderr
        return None
    spend_fresh = now - ledger_m < cfg.stall_min * 60
    latest = latest_heartbeat_epoch(p["beats"])
    beats_quiet = latest is None or now - latest > cfg.stall_min * 60
    files_quiet = p["recent"] is None or now - p["recent"] > cfg.stall_min * 60
    if spend_fresh and beats_quiet and files_quiet:
        return Alert(
            "TOKEN_BURN_NO_PROGRESS",
            "the spend ledger is growing while no tool event and no file "
            "change has landed for %dm." % cfg.stall_min,
            "find the burning session (its transcript is growing without "
            "edits); interrupt it and read its last turns before restarting "
            "anything.", p["liveness_source"])
    return None


def cond_repeat_command(cfg, state, now, p):
    tail = [b for b in p["beats"] if b.get("thash")][-cfg.repeat_fail_count:]
    if len(tail) == cfg.repeat_fail_count:
        hashes = set(b["thash"] for b in tail)
        if len(hashes) == 1 and all(not b.get("ok", True) for b in tail):
            return Alert(
                "REPEAT_COMMAND_LOOP",
                "the same command failed %d times in a row (hash %s)."
                % (cfg.repeat_fail_count, tail[0]["thash"]),
                "stop the retry loop; the global rule already bans a 4th "
                "identical attempt. Revert to last green and re-diagnose "
                "with added logging.", p["liveness_source"])
    return None


def cond_context_fill(cfg, state, now, p):
    for b in reversed(p["beats"]):
        size = b.get("transcript_bytes")
        session = b.get("session") or ""
        if not isinstance(size, int) or not session:
            continue
        if size > cfg.context_warn_bytes and session not in state["context_warned"]:
            state["context_warned"].add(session)
            return Alert(
                "CONTEXT_FILL_APPROACHING",
                "session %s transcript is %d bytes, over the %d-byte warning "
                "line (UNMEASURED default; retune from data)."
                % (session, size, cfg.context_warn_bytes),
                "write the handover message NOW, while there is room: what is "
                "finished with its proving command, what is in flight and "
                "where it stopped, what is untouched, live fences, open "
                "founder asks. Vault first, then chat.", p["liveness_source"])
        break
    return None


def cond_api_error_streak(cfg, state, now, p):
    tail = [b for b in p["beats"]
            if b.get("event") != "Stop"][-cfg.api_streak_count:]
    if len(tail) == cfg.api_streak_count and all(not b.get("ok", True) for b in tail):
        if len(set(b.get("thash") for b in tail)) > 1:
            return Alert(
                "API_ERROR_STREAK",
                "%d consecutive tool failures across different commands; the "
                "session is stuck on the provider or the harness, not the "
                "work." % cfg.api_streak_count,
                "pause dispatches; check provider status and rate limits "
                "before burning further attempts.", p["liveness_source"])
    return None


def newest_mtime_under(path):
    """Newest mtime of any regular file under `path`, or None when the tree
    does not exist or holds no file. None is NO-DATA, never "nothing new".
    """
    newest = None
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            try:
                m = os.path.getmtime(os.path.join(dirpath, name))
            except OSError:  # sbe: allow-silent a file that vanished between the walk and the stat is not a record being dropped, it is a file that is not there; the newest-mtime answer is unaffected by skipping it, and raising here would make a watchdog die on an ordinary race with whoever it is watching
                continue
            if newest is None or m > newest:
                newest = m
    return newest


def cond_handover_owed(cfg, state, now, p):
    """The handover was ordered by the context line and has not landed.

    CONTEXT_FILL_APPROACHING fires ONCE when a transcript crosses the warning
    line, which is the correct shape for a warning and the wrong shape for an
    obligation: a session that ignores it goes quiet and the founder learns at
    the next window that nothing was handed over. This condition watches the
    obligation instead of the moment, and escalates on a doubling clock
    (grace, then 2x, then 4x, capped) until a handover pack file appears under
    the handover directory with an mtime AFTER the obligation began.

    Between escalation steps it returns None, which re-arms the framework's
    one-line-per-episode rule. That is deliberate and is the only place in
    this file where None means "not yet" rather than "healthy"; a real
    handover, or a transcript back under the line, ends the episode by
    clearing the session's state.
    """
    for b in reversed(p["beats"]):
        size = b.get("transcript_bytes")
        session = b.get("session") or ""
        if not isinstance(size, int) or not session:
            continue
        if size <= cfg.context_warn_bytes:
            state["handover_due"].pop(session, None)
            state["handover_steps"].pop(session, None)
            return None
        # The obligation begins when the line was CROSSED, which is the beat's
        # own epoch, not when this watcher happened to look: anchoring on now
        # would let a pack written seconds before the first pass read as older
        # than the obligation it satisfies.
        crossed = b.get("epoch") if isinstance(b.get("epoch"), int) else now
        due_since = state["handover_due"].setdefault(session, crossed)
        waited = now - due_since
        grace = cfg.handover_grace_min * 60
        newest = newest_mtime_under(cfg.handover_dir)
        if newest is not None and newest >= due_since:
            state["handover_due"].pop(session, None)
            state["handover_steps"].pop(session, None)
            return None
        step = state["handover_steps"].get(session, 0)
        threshold = grace * (2 ** step)
        if waited < threshold or step >= cfg.handover_max_steps:
            return None
        state["handover_steps"][session] = step + 1
        where = ("no file under %s is newer than the obligation"
                 % cfg.handover_dir if newest is not None
                 else "%s holds no file at all (NO-DATA, not a pass)"
                 % cfg.handover_dir)
        return Alert(
            "HANDOVER_OWED",
            "session %s crossed the context line %d minute(s) ago and no "
            "handover pack has landed since: %s. Escalation step %d."
            % (session, int(waited // 60), where, step + 1),
            "write the pack NOW, then hand the baton: what is finished with "
            "its proving command, what is in flight and exactly where it "
            "stopped, what is untouched, live fences, uncommitted paths, open "
            "founder asks. Vault and pack first, chat second. A pack file "
            "under the handover directory ends this alert; nothing else does.",
            p["liveness_source"])
    return None


CONDITIONS = (
    cond_true_stall,
    cond_dead_worker,
    cond_orphaned_fence,
    cond_ageing_uncommitted,
    cond_disk_floor,
    cond_token_burn,
    cond_repeat_command,
    cond_context_fill,
    cond_api_error_streak,
    cond_handover_owed,
)


# ---------------------------------------------------------------------------
# Emission: stdout line + alerts ledger + macOS banner for the two classes
# worth interrupting a human. Push is session-forwarded, not watcher-native,
# and the spec says so.
# ---------------------------------------------------------------------------

def emit(cfg, alert):
    say(alert.line())
    try:
        d = os.path.dirname(cfg.alerts)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(cfg.alerts, "a") as fh:
            fh.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "condition": alert.condition,
                "message": alert.message,
                "remediation": alert.remediation,
                "liveness_source": alert.source,
            }, sort_keys=True) + "\n")
    except OSError as e:
        warn("stall-detector: could not append to alerts ledger (%s)" % e)
    if cfg.banners and alert.condition in BANNER_CLASSES and sys.platform == "darwin":
        try:
            banner = subprocess.run(
                ["osascript", "-e",
                 'display notification %s with title "BrotherSBE stall detector"'
                 % json.dumps(alert.message[:180])],
                capture_output=True, timeout=15)
            if banner.returncode != 0:
                warn("stall-detector: banner exited %d" % banner.returncode)
        except Exception as e:
            warn("stall-detector: banner failed (%s)" % e)


def one_pass(cfg, state):
    now = time.time()
    beats = read_heartbeats(cfg)
    live = liveness(cfg, beats, now)
    fences, fence_sessions, fence_ok = fence_count_and_sessions(cfg)
    probes = {
        "beats": beats,
        "busy": live.busy,
        "liveness_source": live.source,
        "fences": fences,
        "fence_sessions": fence_sessions,
        "fence_ok": fence_ok,
        "recent": recent_change_epoch(cfg),
        "root_dirty": git_dirty_paths(cfg.root) or [],
    }
    fired = []
    for cond in CONDITIONS:
        name = cond.__name__
        alert = cond(cfg, state, now, probes)
        if alert is not None:
            if state["armed"].get(name, True):
                emit(cfg, alert)
                fired.append(alert)
                state["armed"][name] = False
        else:
            state["armed"][name] = True
    return fired


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--once", action="store_true",
                    help="one evaluation pass, then exit (the test entry)")
    ap.add_argument("--poll-seconds", type=int, default=180)
    ap.add_argument("--stall-min", type=int, default=12)
    ap.add_argument("--dirty-min", type=int, default=45)
    ap.add_argument("--disk-floor-gib", type=int, default=15)
    ap.add_argument("--disk-free-gib-override", type=int, default=None,
                    help="skip the real shutil.disk_usage probe and use this "
                         "value instead; test-only, so a fixture's health "
                         "does not depend on the host machine's free space")
    ap.add_argument("--context-warn-bytes", type=int, default=20 * 1024 * 1024)
    ap.add_argument("--handover-dir", default=None,
                    help="directory a handover pack lands in; a file there "
                         "newer than the obligation ends HANDOVER_OWED")
    ap.add_argument("--handover-grace-min", type=int, default=20,
                    help="minutes after the context line before the first "
                         "HANDOVER_OWED alert (UNMEASURED default)")
    ap.add_argument("--heartbeat", default=None)
    ap.add_argument("--alerts", default=None)
    ap.add_argument("--spend-ledger", default=None)
    ap.add_argument("--worktrees", default=None)
    ap.add_argument("--fence-cmd", default=None,
                    help="command whose output carries 'N live fence line(s)'; "
                         "defaults to the fence hook's own fences subcommand")
    ap.add_argument("--pgrep-patterns", default=BUSY_PATTERNS)
    ap.add_argument("--no-banners", dest="banners", action="store_false")
    ns = ap.parse_args(argv)
    cfg = Config(ns)
    warn("stall-detector: " + cfg.describe())
    if not cfg.spend_ledger:
        warn("stall-detector: NO-DATA for TOKEN_BURN_NO_PROGRESS: no "
             "--spend-ledger configured, so that condition stays quiet. This "
             "line is the loud version of a silent skip.")
    state = {"armed": {}, "context_warned": set(),
             "handover_due": {}, "handover_steps": {}}
    if ns.once:
        one_pass(cfg, state)
        return 0
    while True:
        one_pass(cfg, state)
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())

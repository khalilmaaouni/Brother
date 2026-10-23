#!/usr/bin/env python3
"""Keep one unit runner alive for EVERY in scope unit that has a next sub unit. Idempotent: run it as often as you like.
usage (repo root): runner_pool.py [--dry]
The queue is three stages with different limits, which is the whole optimisation (learned 2026-09-20):
  MODEL stage   (brief, build, write probes): free and parallel, so one runner per unit, as wide as the plan allows;
  MACHINE stage (grade, execute probes):      bounded by grade_build.local_slot, LOCAL_SLOTS shared by all runners;
  LANDING stage (gates, commit, push):        one lane, worked in batches by the orchestrator.
A unit gets NO new runner while: a runner for it is alive; or one of its builds has the status word exactly READY and waits to land (its next sub unit must be built
on the tree AFTER that landing, or its patch will not apply); or its last run ended EXHAUSTED or WITHHELD (needs a human fact).
Order of starts follows the council ranking first, then plan order.
A unit the plan lint flags is skipped only when BROTHER_RUNFLOW_LINT=block; in report mode its findings are printed and admission is unchanged."""
import hashlib
import fcntl, glob, json, os, re, subprocess, sys, time
import importlib.util   # ACC3.b: the one loading route the safety screen and the proof freeze both admit
from typing import List, Optional, Set, Tuple   # ACC3.b: this block's typed contract
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's copy
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed, X3 finding 5)
import plan_lint  # noqa: E402  (FX-13.6: the plan lint, run on the one sub unit this pass is about to start)
import spec_check  # noqa: E402  (the landing gate's own done check verdict: spec_check.gate_refusal, 2026-09-29)
import builder_claim  # noqa: E402  (LC1.a: one claim store, so a builder the session dispatches is a live runner to the pool)
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
# LA5.b: scripts/loop/load_rule.py, whose machine_busy is the estate's ONE load rule (perturb_pool asks it too). It lives
# BESIDE this file on purpose: the bin deploy copies scripts/loop whole, so the deployed pool finds it; one a directory up
# (scripts/load_rule.py, 2026-10-15 and before) resolved to ~/.claude/load_rule.py in the deployed bin, which nothing
# installs, so the deployed pool held with NO-DATA on every pass.
LOAD_RULE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_rule.py")
# ONE definition of scope (review 2026-09-22): loop_done reads BROTHER_SCOPE, this file had its own regex, and the
# two could disagree about what "in scope" means. The env var wins when set; the literal below is the default both use.
# A DEFAULT NEVER NARROWS THE WORK (owner, 2026-09-27: "This is a hard lesson you should always remember"). The default
# used to be one night's list, ^(D\d|L1b?$|L2|L3|C0|R3$|R2$|R4$|L5a$|L6b$|H[1-7]$), from the owner's rulings of 2026-09-20
# and 2026-09-24; a launch that named no scope silently worked only those units, and 18 fully specified units never
# started. A narrower scope is named at launch (BROTHER_SCOPE), and every pass prints what it leaves out.
SCOPE_SRC = os.environ.get("BROTHER_SCOPE") or "."
SCOPE = re.compile(SCOPE_SRC)
COUNCIL = ["D3", "D10", "D4", "D11", "D12", "D13", "C0"]
HOLD = {"C0.1": "lands only after the cut session's 34 row comparison", "C0.2": "owned by the cut session", "C0.4a": "owned by the cut session"}


def hold_reason(sub):
    """Why `sub` takes no runner whatever its scores, or '': the cut session's HOLD above, then the owner's proof supply
    (plan_store.supply_hold, 2026-10-04: of a widened unit only the named sub units are the loop's). Every site that
    picks a unit's next sub unit asks this, and admissible() refuses the same sub units for every other caller."""
    return ("held: " + HOLD[sub]) if sub in HOLD else plan_store.supply_hold(sub)
def alive_runners(runs_dir, plan, kill=os.kill):
    """{(unit, sub)} for every run folder under runs_dir that has a PID file naming a live process and no STATUS
    yet. Until 2026-09-22 this grepped the machine wide process table (R4: no tool reads real machine state), so
    runners of any other checkout counted against this pool and the guard suite failed whenever one was alive.
    FAIL DIRECTION: an unreadable PID is not counted (fewer starts, never more); a pid the kernel refuses to
    signal (EPERM) is counted alive, since alive and unknown read the same."""
    if not isinstance(runs_dir, str) or not runs_dir:
        raise ValueError("runs_dir must be a non-empty string")
    if not isinstance(plan, dict):
        raise ValueError("plan must be a dict")
    if not callable(kill):
        raise ValueError("kill must be callable")
    _units = plan.get("units")
    if not isinstance(_units, list):
        raise ValueError("plan['units'] must be a list")
    by_sub = {}
    for _u in _units:
        if not isinstance(_u, dict):
            raise ValueError("every plan unit must be a dict")
        _uid = _u.get("id")
        if not isinstance(_uid, str) or not _uid:
            raise ValueError("unit id must be a non-empty string")
        _subs = _u.get("sub_units")
        if _subs is None:
            _subs = []
        if not isinstance(_subs, list):
            raise ValueError("unit['sub_units'] must be a list")
        for _s in _subs:
            if not isinstance(_s, str) or not _s:
                raise ValueError("sub unit id must be a non-empty string")
            by_sub[_s] = _uid
    out = set()
    for d in glob.glob(os.path.join(runs_dir, "*-*/")):
        pidf = os.path.join(d, "PID")
        if not os.path.isfile(pidf) or os.path.isfile(os.path.join(d, "STATUS")): continue
        try:
            with open(pidf, encoding="utf-8") as fh: pid = int(fh.read().strip())
            kill(pid, 0)
        except ValueError:
            pass                       # sbe: allow-silent an empty or half written PID is a runner between mkdir and its write: counted alive, fewer starts. present but empty or half written (the runner is between mkdir and its write): alive, fewer starts
        except OSError as exc:
            if not isinstance(exc, PermissionError): continue
        # A RUN WITH NO STATUS FOR SIX HOURS IS DEAD whatever its pid says (executed attack 2026-09-22): a PID file
        # naming a reused or unsignalable pid (pid 1 is EPERM) held its unit out of the pool forever. Five rounds at
        # the measured stage times fit inside six hours with margin; a runner alive that long is wedged anyway.
        try:
            if time.time() - os.path.getmtime(d.rstrip("/")) > 6 * 3600: continue
        except OSError: continue
        sub = re.sub(r"-\d{6}$", "", os.path.basename(d.rstrip("/")))
        out.add((by_sub.get(sub, sub.split(".")[0].split("-")[0]), sub))
    return out


def _pid_alive(pid):
    """A pid the kernel refuses to signal (EPERM) reads as alive, since alive and unknown read the same.
    A pid that is not a positive integer cannot be a real process: refuse it as corrupt input."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid must be a positive integer")
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if isinstance(exc, PermissionError):
            return True
        return False
    return True


def _write_status_atomic(path, text, seen=None):
    """Write text to path plus '.tmp' then os.replace, so no reader ever sees a torn file, and only while the file still
    holds `seen` (bytes; None means no file at all). True when written, False when it changed first. The comparison and
    the write both happen under the runs root's lock, <runs>/.status.lock, which every STATUS writer takes (RR lane C,
    2026-09-27: reconcile_dead saw no STATUS, the runner then wrote READY and exited, and STALLED replaced the READY)."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if seen is not None and not isinstance(seen, bytes):
        raise ValueError("seen must be bytes or None")
    _tmp = path + ".tmp"
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(path)), ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
            fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes
            try:
                with open(path, "rb") as fh:
                    _now = fh.read()
            except FileNotFoundError:
                _now = None
            if _now != seen:
                return False
            with open(_tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(_tmp, path)
    except OSError as exc:
        raise ValueError("could not write STATUS: %s" % exc)
    return True


def reconcile_dead(run_dir, pid_alive):
    """A run folder with a PID whose process is not alive and no STATUS gets STATUS
    'STALLED dead runner pid N', written atomically; returns the word written or ''. A folder with no
    PID file is left alone. Corrupt or unreadable input raises ValueError: unknown is never safe."""
    if not isinstance(run_dir, str) or not run_dir:
        raise ValueError("run_dir must be a non-empty string")
    if not callable(pid_alive):
        raise ValueError("pid_alive must be callable")
    if not os.path.isdir(run_dir):
        raise ValueError("run_dir must be an existing directory: %r" % run_dir)
    _status = os.path.join(run_dir, "STATUS")
    if os.path.exists(_status):
        if not os.path.isfile(_status):
            raise ValueError("STATUS exists but is not a file: %r" % _status)
        return ""
    _pidf = os.path.join(run_dir, "PID")
    if not os.path.exists(_pidf):
        return ""
    if not os.path.isfile(_pidf):
        raise ValueError("PID exists but is not a file: %r" % _pidf)
    try:
        with open(_pidf, "rb") as fh:
            _raw = fh.read()
    except OSError as exc:
        raise ValueError("PID file unreadable: %s" % exc)
    try:
        _txt = _raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("PID file is not valid utf-8: %s" % exc)
    if not _txt:
        raise ValueError("PID file is empty")
    try:
        _pid = int(_txt)
    except ValueError:
        raise ValueError("PID file is not an integer: %r" % _txt)
    if _pid <= 0:
        raise ValueError("PID must be positive: %d" % _pid)
    if pid_alive(_pid):
        return ""
    # decided on "no STATUS yet" above; the write re-checks that under the lock, so a word written since stands
    return "STALLED" if _write_status_atomic(_status, "STALLED dead runner pid %d" % _pid, None) else ""


def _dir_time(path, mtime=None):
    """When a run folder was last active. The folder name carries only HHMMSS, which WRAPS AT MIDNIGHT: sorting
    those strings puts yesterday's 220142 after today's 023623, so a run made after midnight reads as older than
    the previous evening's and the tool answers with a stale status. Measured 2026-09-21: D1.5, L3.1 and L1.1
    each had a READY build from this morning while the name sort returned a WITHHELD or EXHAUSTED folder from the
    night before, so finished work was invisible and the units were reported as needing a human fact. The
    filesystem knows the real time, so ask it, and fall back to nothing rather than to the stamp.
    Same defect, same fix, as dir_time() in scripts/brother_pass.py and the mtime path in loop_done.py."""
    try:
        return (mtime or os.path.getmtime)(path)
    except OSError:
        return 0.0


def _start_time(path, stat=None):
    """When a run folder was CREATED, the attempt's start (D31, register row L18). _dir_time is the folder's LAST
    write, which moves until the run writes STATUS at its end, so a diagnosis note written while the attempt ran
    read as older than the run and never re-seated it (2026-10-14: note 13h46, start 13h44, quarantine 13h56).
    The birth time when the platform reports a finite one above 0, else _dir_time (today's value); an unreadable
    folder answers 0.0, which still_parked reads as unknown and parks."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if stat is not None and not callable(stat):
        raise ValueError("stat must be callable or None")
    try:
        _born = getattr((stat or os.stat)(path), "st_birthtime", None)
    except OSError:
        return 0.0
    if isinstance(_born, bool) or not isinstance(_born, (int, float)) or _born != _born or _born in (float("inf"), float("-inf")) or _born <= 0:
        return _dir_time(path)
    return float(_born)

# A PARKED SUB UNIT COMES BACK WHEN A FACT CHANGES, WITHOUT A HUMAN (2026-09-22). EXHAUSTED and WITHHELD park a sub
# unit "until a fact", and the only way to supply one was a human with RUNNER_HINT. With one sub unit per unit that
# is a drain with no refill: last night all 15 units in scope parked one by one and the run ended UNPRODUCTIVE at
# 07:34 with three hours and 90 USD unspent, while 13 of their specs HAD been repaired and landed at 04:46. A changed
# spec, or a changed judge (the runner, the grader, the probe tools, the checker), is a new fact by any reading, so
# the sub unit is admitted again. It is bounded by construction: the retry's own run folder is newer than the fact,
# so if it exhausts again it parks again until the NEXT change. Nothing is retried on a timer.
# ponytail: mtime, not a content hash. A merge or a deploy only rewrites files that differ, so mtime moves when the
# content does; a `touch` would also readmit, which costs one run of cents. Record a hash in the run folder if that bites.
# THE BRIEF SHAPERS ARE JUDGES TOO (owner 2026-09-23 06:1x): a changed probe brief, build plan or repair advisor is a new fact
# for a parked sub unit as much as a changed grader, so a rule landed for the whole board readmits every lane it parked.
# build_brief.py and slicer.py decide what a worker SEES (2026-09-24 12:5x: D3.7 and D1.8 parked EXHAUSTED on briefs
# that showed the test file and none of the modules it imports; the builder was fixed, and without these two names the
# deploy would have readmitted nothing, since the stamp copies with mtimes preserved and only a JUDGE's mtime counts).
# THE RESOLVED PROGRAM IS A FACT TOO (2026-09-30): a sub unit parked by a configuration fault (the program did not know
# the model) comes back when the program the loop resolves changes, path or version. model_reachability.py rewrites this
# record only when the resolved set changes, so its mtime is the time of that change; never a timer, never a touch of
# the parked runs. PROGRAM_RECORD_ENV and the default path are the same two model_reachability.record_path() reads.
PROGRAM_RECORD_ENV = "BROTHER_PROGRAM_RECORD"
def program_record():
    return os.environ.get(PROGRAM_RECORD_ENV) or os.path.expanduser("~/.claude/evidence/loop-programs.json")
JUDGES = ("unit_runner.py", "grade_build.py", "probe_wave.py", "probe_build.py", "check_wave.py", "probe_brief.py", "build_plan.py", "repair_advisor.py", "spec_wave.py", "build_brief.py", "slicer.py", "brief_check.py")
def fact_time(unit_id, mtime=os.path.getmtime, specs="docs/plan/specs", bin_dir=None, spec_path=None):
    """The newest change to this unit's spec or to a tool that judges its builds; 0.0 when none can be read.
    spec_path is the unit's own `spec` field, the file unit_runner actually reads (2026-09-30: every FX unit keeps its
    spec under docs/plan/specs/fixes/, so the guessed specs/<id>.md never existed and no FX spec repair readmitted
    a parked FX sub unit; FX-13.1 sat WITHHELD after its section was repaired). Both paths are read."""
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id must be a non-empty string")
    if not callable(mtime):
        raise ValueError("mtime must be callable")
    if not isinstance(specs, str):
        raise ValueError("specs must be a string")
    if bin_dir is not None and not isinstance(bin_dir, str):
        raise ValueError("bin_dir must be a string or None")
    if spec_path is not None and not isinstance(spec_path, str):
        raise ValueError("spec_path must be a string or None")
    bin_dir = bin_dir or os.path.expanduser("~/.claude/bin"); newest = 0.0
    # the unit's diagnosis note is a fact: diag_apply rewrites it only when its content changes (plan E step 2b)
    import diag_notes
    note = diag_notes.note_path(unit_id)
    for p in [os.path.join(specs, "%s.md" % unit_id)] + ([spec_path] if spec_path else []) + [os.path.join(bin_dir, t) for t in JUDGES] + [program_record(), note]:
        try:
            _v = mtime(p)
        except OSError:
            continue   # sbe: allow-silent a path that cannot be stat'd is missing, not a fact
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            raise ValueError("mtime must return a number for %s" % p)
        if _v != _v or _v == float("inf") or _v == float("-inf"):
            raise ValueError("mtime must return a finite number for %s" % p)
        newest = max(newest, float(_v))
    return newest


def _seat_env_get(env, name):
    """(value, readable) for `name` in `env` (a mapping) or the process environment when env is None. readable is
    False when the read cannot be taken at all: env has no get(), or get() raised. A readable read of a missing
    name answers (None, True)."""
    if env is None:
        return (os.environ.get(name), True)
    try:
        getter = env.get
    except AttributeError:
        return (None, False)
    if not callable(getter):
        return (None, False)
    try:
        return (getter(name), True)
    except (KeyError, TypeError, AttributeError, ValueError):  # sbe: allow-silent an unreadable mapping read is the fingerprint's ? and the path readers' default
        return (None, False)


def seat_root(env=None):
    """The driver-controlled seat root, as a realpath: the SEAT_ROOT_ENV value when it is a string holding a
    non-space character, else SEAT_ROOT_DEFAULT. "" when the resolved path is the runs directory RUNS or sits under
    it, because a root the unit runtimes write is not driver-controlled."""
    value, readable = _seat_env_get(env, SEAT_ROOT_ENV)
    if readable and isinstance(value, str) and value.strip():
        chosen = value
    else:
        chosen = SEAT_ROOT_DEFAULT
    try:
        resolved = os.path.realpath(chosen)
    except (OSError, ValueError, TypeError):  # sbe: allow-silent a root that cannot be resolved is no root
        return ""
    runs_real = os.path.realpath(RUNS)
    if resolved == runs_real or resolved.startswith(runs_real + os.sep):
        return ""
    return resolved


def launch_stamp_path(env=None):
    """The realpath of the stamp file, or "" when seat_root(env) is "" or the resolved candidate escapes the seat
    root. The LAUNCH_STAMP_ENV value when it is a string holding a non-space character is the candidate, a relative
    one joined under the root; else the LAUNCH_STAMP_NAME entry under the root."""
    root = seat_root(env)
    if not root:
        return ""
    value, readable = _seat_env_get(env, LAUNCH_STAMP_ENV)
    if readable and isinstance(value, str) and value.strip():
        chosen = value
    else:
        chosen = LAUNCH_STAMP_NAME
    joined = chosen if os.path.isabs(chosen) else os.path.join(root, chosen)
    try:
        resolved = os.path.realpath(joined)
    except (OSError, ValueError, TypeError):  # sbe: allow-silent a stamp path that cannot be resolved is no stamp path
        return ""
    if not resolved.startswith(root + os.sep):
        return ""
    return resolved


def seat_fingerprint(env=None):
    """A lower case sha256 hex digest of the seat launch environment: the SEAT_ENV_NAMES lines name=value joined
    with newlines, UTF-8 encoded. A missing name reads as the empty string; a read that raises reads as ?. The same
    environment always yields the same digest, and any change to one of the four names yields a different one."""
    lines = []
    for name in SEAT_ENV_NAMES:
        value, readable = _seat_env_get(env, name)
        if not readable or (value is not None and not isinstance(value, str)):
            lines.append("%s=?" % name)
        elif value is None:
            lines.append("%s=" % name)
        else:
            lines.append("%s=%s" % (name, value))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest().lower()


def write_launch_stamp(env=None):
    """The production writer and the only place the stamp file is created. A "" stamp path writes nothing and
    answers "". The current text (a missing, unreadable or non-file path reads as "") equal to the current
    fingerprint leaves the file and its mtime alone; anything else creates the parent directory, writes the
    fingerprint to a sibling temporary file named by appending .tmp, renames it over the stamp path and answers the
    path. With env None the process environment's LAUNCH_STAMP_ENV is set to the path; a caller that passed its own
    mapping leaves the process environment alone. Any OSError or ValueError while writing answers ""."""
    path = launch_stamp_path(env)
    if not path:
        return ""
    fingerprint = seat_fingerprint(env)
    try:
        with open(path, "rb") as fh:
            current = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):  # sbe: allow-silent a missing, unreadable or non-file path is no stamp yet
        current = ""
    if current != fingerprint:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(fingerprint)
            os.replace(tmp, path)
        except (OSError, ValueError):  # sbe: allow-silent a write that failed leaves no stamp and the pass unchanged
            return ""
    if env is None:
        os.environ[LAUNCH_STAMP_ENV] = path
    return path


def launch_stamp_time(mtime=os.path.getmtime, env=None):
    """The stamp's mtime as a float, or 0.0 for every doubtful input: an empty path, a path that is not a file, and
    any OSError, ValueError or TypeError from `mtime`."""
    path = launch_stamp_path(env)
    if not path:
        return 0.0
    if not os.path.isfile(path):
        return 0.0
    try:
        return float(mtime(path))
    except (OSError, ValueError, TypeError):  # sbe: allow-silent an mtime that cannot be read is no fact
        return 0.0


RESEAT_MIN_S = 600   # a sub unit is not re-seated within ten minutes of its last start unless that run reached READY


def reseated_too_soon(word, run_at, now):
    """True when the last run started under RESEAT_MIN_S ago and did not reach READY: the pool waits. Measured 2026-09-22:
    D11.d started three runs in six minutes on refused rounds. An unknown run time (0.0) never counts as too soon."""
    # Only a run that ENDED in a parked word is rate limited: a dead RUNNING folder frees its unit at once (test_a_dead_runner_frees_its_unit).
    if word is not None and not isinstance(word, str):
        raise ValueError("word must be a string or None")
    for _name, _v in (("run_at", run_at), ("now", now)):
        if isinstance(_v, bool) or not isinstance(_v, (int, float)):
            raise ValueError("%s must be a number" % _name)
        if _v != _v or _v == float("inf") or _v == float("-inf"):
            raise ValueError("%s must be a finite number" % _name)
    return run_at > 0.0 and (now - run_at) < RESEAT_MIN_S and word in ("EXHAUSTED", "WITHHELD", "UNFUNDED", "DRAINED", "CONFIG_WAIT")


def cooldown_line(unit_id, sub, run_at):
    """The line loop_pass.sh reads to tell a timed wait from a pass that did nothing (D32, register row L28: on
    2026-10-14 23h11 a run whose only unit was inside RESEAT_MIN_S stopped UNPRODUCTIVE on its first pass).
    `COOLDOWN <unit_id> <sub> until <epoch seconds>`; a run time that is not a finite number above 0 is refused."""
    for _name, _v in (("unit_id", unit_id), ("sub", sub)):
        if not isinstance(_v, str) or not _v or _v != "".join(_v.split()):
            raise ValueError("%s must be a non-empty string with no whitespace" % _name)
    if isinstance(run_at, bool) or not isinstance(run_at, (int, float)) or run_at != run_at or run_at in (float("inf"), float("-inf")) or run_at <= 0:
        raise ValueError("run_at must be a finite number above 0")
    return "COOLDOWN %s %s until %d" % (unit_id, sub, int(run_at + RESEAT_MIN_S))


def still_parked(word, run_at, fact_at):
    """True when a sub unit whose last run ended with this status word must stay out of the pool. An unknown run time
    or an unknown fact time (0.0) PARKS: a retry is only granted on evidence that something changed after the run."""
    return word in PARKED and not (run_at > 0.0 and fact_at > run_at)


# CONFIG_WAIT parks like EXHAUSTED and WITHHELD: no attempt was spent. Its facts are fact_time's, and one more (attack
# R1 F2): a fresh proof that CLOSED a configuration breaker. A fault no program change can fix (a transient "No endpoints
# found", lost access, a registry id repaired) is proven gone by that close, whatever the program record says.
PARKED = ("EXHAUSTED", "WITHHELD", "CONFIG_WAIT")
# I3.a: the driver writes a seat root stamp under a driver-controlled root and the pass names it in the process
# environment. SEAT_ROOT_ENV names the root; SEAT_ROOT_DEFAULT is the fallback under the driver's evidence area,
# home expanded at import time. LAUNCH_STAMP_ENV names the stamp file and LAUNCH_STAMP_NAME is the entry the writer
# uses under the seat root. SEAT_ENV_NAMES are the four launch names the seat caps read and the fingerprint hashes.
SEAT_ROOT_ENV = "BROTHER_SEAT_ROOT"
SEAT_ROOT_DEFAULT = os.path.expanduser("~/.claude/evidence/seat-root")
LAUNCH_STAMP_ENV = "BROTHER_LAUNCH_STAMP"
LAUNCH_STAMP_NAME = "launch-stamp"
SEAT_ENV_NAMES = ("BROTHER_SEAT_ROOT", "BROTHER_PIN_MODEL", "BROTHER_NATIVE_SEATS", "WORKERS_PER_ROUND")


def seat_root_fact(word: str, spec_at: float) -> float:
    """The fact time a parked unit is judged against. A spec_at that is a bool, a string, None, NaN or any other
    non-number is refused with ValueError. For a word that is not the string WITHHELD the spec and judge fact
    stands unchanged; for WITHHELD the seat root stamp joins the maximum, so a changed seat root readmits it."""
    if isinstance(spec_at, bool) or not isinstance(spec_at, (int, float)):
        raise ValueError("spec_at must be a number, not %r" % (spec_at,))
    if spec_at != spec_at or spec_at == float("inf") or spec_at == float("-inf"):
        raise ValueError("spec_at must be a number, not %r" % (spec_at,))
    if word != "WITHHELD":
        return float(spec_at)
    return max(float(spec_at), launch_stamp_time())


def parked_fact(word, unit_id, spec_path=None):
    """The newest fact for a sub unit whose last run ended `word`: fact_time, and for CONFIG_WAIT also the newest
    configuration breaker close. An unreadable breaker adds no fact (the unit stays parked)."""
    fact_at = fact_time(unit_id, spec_path=spec_path)
    if word == "CONFIG_WAIT":
        try:
            import breaker
            fact_at = max(fact_at, breaker.last_config_close())
        except (ImportError, ValueError, OSError):
            pass   # sbe: allow-silent no readable close is no fact; the unit stays parked, the safe side
    return seat_root_fact(word, fact_at)


# A SEAT LOST MID RUN PARKS ONLY ITS TRANSPORT'S UNITS, AND SAYS SO ONCE (1.1.6 S3). Until S3 a lost Claude login parked
# every unit one CONFIG_WAIT at a time, whatever host would build it, and nobody was told. A sub unit's transport is its
# building role's: the role in loop-roles.json whose optional "builds" list names the sub unit or its unit, else
# "worker"; that role's setting (else its default) names a registry model, whose row names the transport. Anything
# unreadable answers "", and "" PARKS whenever any seat is lost: an unknown transport never runs (fail direction).
KNOWN_TRANSPORTS = ("bridge", "codex", "claude")   # model_router.KNOWN_TRANSPORTS; the S3 suite holds the two equal
CONTENT_ORDER = {"public": 0, "internal": 1, "private": 2}   # model_router.ORDER
from fanout_verdict import LOGIN_SCAN, LOGIN_HINTS, login_class  # noqa: E402,F401  (one login class rule, S2 round 3)


def sub_role(sub, roles):
    """The name of the role that builds `sub`: the first role (by name) whose "builds" list names the sub unit or its
    unit, else "worker"; "" when sub or roles is not the expected shape."""
    if not isinstance(sub, str) or not sub or not isinstance(roles, dict):
        return ""
    unit = sub.rsplit(".", 1)[0]
    for name in sorted(k for k in roles if isinstance(k, str)):
        builds = roles[name].get("builds") if isinstance(roles[name], dict) else None
        if isinstance(builds, list) and (sub in builds or unit in builds):
            return name
    return "worker"


def role_model(spec, env=None):
    """The registry name a role resolves to: its setting's value in env when non blank, else its default; ''."""
    if not isinstance(spec, dict):
        return ""
    e = os.environ if env is None else env
    setting = spec.get("setting")
    chosen = e.get(setting, "") if isinstance(setting, str) and setting else ""
    name = chosen.strip() if isinstance(chosen, str) and chosen.strip() else spec.get("default")
    return name.strip() if isinstance(name, str) and name.strip() else ""


def _row_transport(name, reg):
    row = reg.get(name) if isinstance(reg, dict) and name else None
    t = row.get("transport") if isinstance(row, dict) else None
    return t if isinstance(t, str) and t in KNOWN_TRANSPORTS else ""


def sub_transport(sub: str, roles: dict, reg: dict) -> str:
    """The transport of the model the role building `sub` resolves to, or "" when the role, its model or the
    registry row cannot be read. "" parks as today."""
    role = sub_role(sub, roles)
    return _row_transport(role_model(roles.get(role)), reg) if role else ""


def transport_parked(subs: list, lost: set, roles: dict, reg: dict) -> set:
    """The sub units of `subs` whose transport is in `lost`, plus every one whose transport is unknown ("") once
    any seat is lost. Nothing lost, nothing parks. Units of another known transport stay admissible."""
    if not isinstance(subs, list):
        raise ValueError("subs must be a list, not %s" % type(subs).__name__)
    if not isinstance(lost, (set, frozenset)):
        raise ValueError("lost must be a set, not %s" % type(lost).__name__)
    if not lost:
        return set()
    return {s for s in subs if sub_transport(s, roles, reg) in lost | {""}}


def role_inputs():
    """(roles, registry, note) for sub_transport, read once per pass: loop_roles.load_roles() and
    model_router.load_registry(). An unreadable one is {} and the note names it ('' when both read)."""
    notes, roles, reg = [], {}, {}
    try:
        import loop_roles
        roles = loop_roles.load_roles() or {}
    except Exception as exc:   # sbe: allow-silent an unreadable roles file knows no transport: every unit parks on a loss, named in the note
        notes.append("the loop roles cannot be read (%s)" % type(exc).__name__)
    if not roles and not notes:
        notes.append("the loop roles cannot be read")
    try:
        import model_router
        reg = model_router.load_registry()
    except Exception as exc:   # sbe: allow-silent an unreadable registry knows no transport: every unit parks on a loss, named in the note
        notes.append("the model registry cannot be read (%s)" % str(exc)[:120])
    return roles, reg, "; ".join(notes)


def wait_cause(run_dir):
    """A run's STATUS text (its first 4096 bytes) plus the errors its newest round's results.json carries (read to
    LOGIN_SCAN): the status names the word, the round names the cause (unit_runner's CONFIG_WAIT sentence does not)."""
    parts = []
    try:
        with open(os.path.join(run_dir, "STATUS"), "rb") as fh:
            parts.append(fh.read(4096).decode("utf-8", "replace"))   # the word and its sentence; a huge STATUS never hides the round's cause
        rounds = sorted(glob.glob(os.path.join(run_dir, "round*", "results.json")), key=_dir_time)
        if rounds:
            with open(rounds[-1], "rb") as fh:
                rows = json.loads(fh.read(LOGIN_SCAN).decode("utf-8", "replace"))
            for r in (rows if isinstance(rows, list) else []):
                if isinstance(r, dict):
                    parts.append(str(r.get("error") or "")[:2000])
    except (OSError, ValueError):
        pass   # sbe: allow-silent an unreadable round names no cause; the status alone decides, the wait parks itself as before
    return "\n".join(parts)


def seat_proof_at(transport):
    """The epoch of the intake record (loop-intake/CURRENT.json, written by prepare after the seat gate) when its reach
    holds an OK binding on `transport`; 0.0 when none or unreadable, which clears no loss (the safe side)."""
    path = os.path.join(os.environ.get("INTAKE_EVIDENCE") or os.path.expanduser("~/.claude/evidence"), "loop-intake", "CURRENT.json")
    try:
        if os.path.getsize(path) > (1 << 20):
            return 0.0
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return 0.0
    at = rec.get("epoch") if isinstance(rec, dict) else None
    reach = rec.get("reach") if isinstance(rec, dict) else None
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at != at or not isinstance(reach, list):
        return 0.0
    return float(at) if any(isinstance(b, dict) and b.get("transport") == transport for b in reach) else 0.0


def codex_role(roles, reg):
    """(name, model) of the first inside build role (by name) resolving to a codex model, else ("", "")."""
    for name in sorted(k for k in (roles if isinstance(roles, dict) else {}) if isinstance(k, str)):
        spec = roles[name]
        if isinstance(spec, dict) and spec.get("when") == "inside" and spec.get("kind") == "build":
            model = role_model(spec)
            if _row_transport(model, reg) == "codex":
                return name, model
    return "", ""


def status_word(path):
    """The first word of a STATUS file: RUNNING when there is no file yet (unit_runner writes STATUS at its end),
    NO-DATA when the file is empty, torn or unreadable. H3 (2026-09-24, backend review finding 6): an empty STATUS
    left by a full disk raised IndexError here and killed the whole pool on every pass until a human fixed the file;
    a run this pool cannot read is a run it does not restart and does not count, never a crash."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not os.path.exists(path):
        return "RUNNING"
    if not os.path.isfile(path):
        return "NO-DATA"
    try:
        with open(path, "rb") as fh:
            _raw = fh.read()
    except OSError:
        return "NO-DATA"
    try:
        _text = _raw.decode("utf-8")
    except UnicodeDecodeError:
        return "NO-DATA"
    _words = _text.split()
    return _words[0] if _words else "NO-DATA"


def waits_to_land(state):
    """True only when a sub unit's last status word is EXACTLY READY, meaning a graded and probed build is on
    disk waiting for the landing lane. Its unit then gets no new runner, because the next sub unit must be built
    on the tree AFTER that landing or its patch will not apply.

    EXACTLY READY, NEVER A PREFIX. Until 2026-09-21 this read `last.get(s, "").startswith("READY")`, which is
    also true of READY-UNPROBED, and that one letter of slack was a deadlock with no exit. A sub unit whose
    adversaries produced no runnable probe was held here as "a READY build waits to land", while land_batch's
    own gate admits a status whose first word is exactly READY and refuses every other, so the build could never
    land and the unit could never be rebuilt. One such sub unit removed its whole unit from the work pool
    permanently. At the measured probe yield of 24 percent that is not a corner case.

    THE NEW BEHAVIOUR, PLAINLY: READY-UNPROBED is RETRIED, never held. Two stages clear it and the cheap one
    runs first, since brother_pass puts the probe rung before the build rung: scripts/probe_round.py re-probes
    the same build and promotes it to READY, and if that keeps returning nothing, this pool starts a fresh
    runner that rebuilds the sub unit from the current tree. The cost of being wrong is one rebuilt build, a
    few cents, against a deadlock with no bound, which is why the unknown fails toward starting work.
    QUARANTINE, written by land_batch when it drops a build, already behaved this way and still does."""
    if state is not None and not isinstance(state, str):
        raise ValueError("state must be a string or None")
    return (state or "").split()[:1] == ["READY"]


# FILE TOUCH EDGES (row 17, 2026-09-22). Two sub units built in the same window that patch one file leave the second
# unappliable the moment the first lands: D4.d and D10.b were measured unappliable that way, and every such build is a
# round paid for nothing. The touch set of a sub unit is every backticked path in its spec section (the scorer's own
# path shape, so PATHS TRUE and this read the same files) plus every edits and tests path of its newest build. Paths
# regenerated at landing (bundle, SYSTEM.md, the battery, the plan) are dropped: an edge through them is an artefact.
# FAIL DIRECTION: a sub unit whose touch set cannot be read (no path in its section, no build on disk) BLOCKS: it is
# refused, and a live runner with an unreadable set refuses every start, since unknown and touching everything read
# the same. Fewer starts, never more; the remedy is the spec repair lane naming the files.
import plan_path as _PP  # noqa: E402  (the one plan selector: BROTHER_WBS or the default, an invalid value refuses; test_unit_runner_probe_gate lifts this ALL CAPS assign, so its loader carries _PP)
GENERATED = ("bundle/", "SYSTEM.md", "scripts/check_all.sh", _PP.selected())
from brief_check import SPEC_PATH as PATH_RE  # noqa: E402  (the one reader of a file a spec names; a label before or after the path)
def touch_set(sub, spec_path, runs_dir=RUNS, mtime=None):
    """{path} this sub unit patches, or None when nothing readable names one."""
    if not isinstance(sub, str) or not sub:
        raise ValueError("sub must be a non-empty string")
    if not isinstance(spec_path, str):
        raise ValueError("spec_path must be a string")
    if not isinstance(runs_dir, str):
        raise ValueError("runs_dir must be a string")
    if mtime is not None and not callable(mtime):
        raise ValueError("mtime must be callable or None")
    paths = set()
    try:
        with open(spec_path, encoding="utf-8") as fh: spec = fh.read()
        h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec, flags=re.M)
        m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec, flags=re.M | re.S) if h else None
        if m: paths.update(PATH_RE.findall(m.group(0)))
    except (OSError, TypeError): pass
    builds = sorted(glob.glob(os.path.join(runs_dir, sub + "-*", "round*", "out", sub + "-r*-build.json")), key=lambda b: _dir_time(b, mtime))
    if builds:
        try:
            with open(builds[-1], encoding="utf-8") as fh: b = json.load(fh)
            paths.update(e["path"] for k in ("edits", "tests") for e in (b.get(k) or []) if isinstance(e, dict) and isinstance(e.get("path"), str))
        except (OSError, ValueError, AttributeError): pass
    paths = {os.path.normpath(p) for p in paths}   # `./scripts/x.py` and `scripts/x.py` are one key (review 2026-09-23)
    paths = {p for p in paths if os.path.basename(p) != ".status.lock"}   # the loop's own lock, carried by builds made before 2026-10-03
    return {p for p in paths if not any(p == g or p.startswith(g) for g in GENERATED)} or None   # only generated paths: unknown, never empty


WRITE_HEAD = re.compile(r"^(?:Owns|Files):", re.M)
READ_ONLY = re.compile(r"(?i)read[ -]only|unchanged|^\W*reads\b")   # "reads" only when it opens the annotation (review round 2)


def write_set(sub, spec_path, runs_dir=RUNS, mtime=None):
    """{path} this sub unit WRITES, or None when nothing readable says (review 2026-10-04: touch_set collects every path
    a section mentions, Reads lines, prose and examples, so screening it routed 33 of 57 open sub units, PR1.f on a
    prose mention of system_doc.py among them). Read from the section's Owns: or Files: paragraph only, up to a blank
    line or a Reads: label; a path whose own annotation (the text up to the next path) says read only or unchanged, or
    opens with reads, is left out; the newest build's edit and test paths count only when the spec states no write
    set. GENERATED paths are the loop's own."""
    if not isinstance(sub, str) or not sub:
        raise ValueError("sub must be a non-empty string")
    if not isinstance(spec_path, str):
        raise ValueError("spec_path must be a string")
    paths, found = set(), False
    try:
        with open(spec_path, encoding="utf-8") as fh: spec = fh.read()
        h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), spec, flags=re.M)
        m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), spec, flags=re.M | re.S) if h else None
        for head in (WRITE_HEAD.finditer(m.group(0)) if m else ()):
            found = True
            para = re.split(r"\n\s*\n|\bReads?:", m.group(0)[head.end():], maxsplit=1)[0]
            hits = list(PATH_RE.finditer(para))
            for i, hit in enumerate(hits):
                note = para[hit.end():hits[i + 1].start() if i + 1 < len(hits) else len(para)]
                if not READ_ONLY.search(note):
                    paths.add(hit.group(1))
    except (OSError, TypeError):
        pass
    # the newest build's paths only when the spec states no write set (review round 2: one stray build that wrote a
    # file its spec never asked for would otherwise route the sub unit away on every pass until the folder is pruned)
    builds = sorted(glob.glob(os.path.join(runs_dir, sub + "-*", "round*", "out", sub + "-r*-build.json")), key=lambda b: _dir_time(b, mtime)) if not found else []
    if builds:
        try:
            with open(builds[-1], encoding="utf-8") as fh: b = json.load(fh)
            got = {e["path"] for k in ("edits", "tests") for e in (b.get(k) or []) if isinstance(e, dict) and isinstance(e.get("path"), str)}
            found = bool(got); paths |= got
        except (OSError, ValueError, AttributeError): pass
    paths = {os.path.normpath(p) for p in paths}
    paths = {p for p in paths if not any(p == g or p.startswith(g) for g in GENERATED)}
    return paths if found else None


def reviewed_route(ts, root="."):
    """Why a native build of this touch set would be refused AFTER it ran, or '' (2026-10-04, Astra review lever 3):
    CV1.d's spec named scripts/cut_preflight.py, a path the landing trusts, so the builder's paid session ran to DONE
    and the adapter refused every byte. The adapter's own rule (grade_build.dest) is asked HERE, before any session
    is paid for, so admission and the adapter can never disagree. Such a sub unit is built on the reviewed route
    (a strong model writes, an independent one reviews, the merge window lands), never by the loop. An unknown set
    (None) is left to touch_conflict, which already refuses it. Limit, stated: only the paths touch_set reads are
    screened (the spec reader's extensions; GENERATED paths are the loop's own); any other path still meets the
    adapter, which refuses it after the run, so a gap here costs a session and never lands a protected write. The set
    screened is write_set (what the sub unit writes), never touch_set (everything its section mentions)."""
    if ts is None:
        return ""
    if isinstance(ts, (str, bytes)):
        raise ValueError("ts must be a set of paths or None")
    try:
        import grade_build   # THIS directory's copy (sys.path above): the adapter's own rule, never a second list
    except ImportError as exc:   # one sub unit held with the reason, never the whole pass crashed (review 2026-10-04)
        return "REVIEWED-ROUTE: NO-DATA, the adapter's rule could not be loaded (%s)" % exc
    for p in sorted(ts):
        if not isinstance(p, str):
            raise ValueError("ts must contain only strings")
        full, why = grade_build.dest(root, p)
        if full is None:
            return "REVIEWED-ROUTE: %s; a native build would be refused after it ran, so it is built on the reviewed route" % why
    return ""


def busy_paths(pairs, spec_of, touch=None):
    """(path -> sub unit patching it, [subs whose touch set is unknown]) over (unit, sub) pairs in flight.

    LS1.c (2026-10-11): in flight means live runners AND builds READY to land. The pool built `busy` from live runners
    only, so a sibling could start on a file a READY build changes, be built against a tree that moves when that build
    lands, and stop applying (rule S3)."""
    touch = touch or touch_set
    busy, unknown = {}, []
    for _u, _s in pairs:
        _ts = touch(_s, spec_of.get(_u))
        if _ts is None:
            unknown.append(_s)
        else:
            busy.update({p: _s for p in _ts})
    return busy, unknown


def touch_conflict(ts, busy, unknown):
    """Why this touch set may not start beside the live ones: '' when it may."""
    if ts is not None:
        if isinstance(ts, (str, bytes)):
            raise ValueError("ts must be a set of paths or None")
        try:
            _items = list(ts)
        except TypeError:
            raise ValueError("ts must be an iterable of paths or None")
        for _p in _items:
            if not isinstance(_p, str):
                raise ValueError("ts must contain only strings")
    if not isinstance(busy, dict):
        raise ValueError("busy must be a dict")
    if not isinstance(unknown, list):
        raise ValueError("unknown must be a list")
    if ts is None: return "TOUCH unknown: no path in its spec section and no build on disk"
    if unknown: return "TOUCH: live runner %s has an unknown touch set" % unknown[0]
    hit = sorted(p for p in ts if p in busy)
    return "TOUCH: shares %s with %s" % (hit[0], busy[hit[0]]) if hit else ""


def remaining_of(u):
    """How many sub units this unit still needs, read from its own evidence the same way the start loop reads it.
    A unit whose evidence is unreadable counts as FAR from done, never near: guessing near would put it first."""
    if not isinstance(u, dict):
        raise ValueError("u must be a dict")
    _subs = u.get("sub_units")
    if not isinstance(_subs, list):
        raise ValueError("u['sub_units'] must be a list")
    for _s in _subs:
        if not isinstance(_s, str) or not _s:
            raise ValueError("sub unit id must be a non-empty string")
    ev = u.get("evidence") or ""
    if not isinstance(ev, str):
        raise ValueError("u['evidence'] must be a string")
    return sum(1 for s in _subs if not plan_store.sub_landed(s, ev))


# NEAREST FIRST, owner order 2026-09-21 after the measurement: the previous key was the council ranking then plan
# order, which is a THEME order, and over 12.9 hours it produced 36 landings and ZERO closed units. Ordering by
# distance to done is Little's law applied: with work in progress capped, the units nearest the end finish and
# leave the system instead of every unit advancing one step and none arriving. Ties keep the council's ranking,
# so the owner's own priority still decides between two units equally close.
def unit_landed(u):
    """True when unit `u` is DONE, or every one of its sub units is recorded landed in its evidence. A unit with no
    sub units and no DONE state is not landed; unreadable fields read as not landed (the caller holds, never admits)."""
    if not isinstance(u, dict):
        return False
    if u.get("state") == "DONE":
        return True
    subs = u.get("sub_units")
    ev = u.get("evidence")
    if not isinstance(subs, list) or not subs or not isinstance(ev, str):
        return False
    ids = [s.get("id") if isinstance(s, dict) else s for s in subs]
    try:
        return all(plan_store.sub_landed(s, ev) for s in ids)
    except ValueError:
        return False


def after_held(sub, unit, units):
    """The hold reason when `unit` names an after predecessor that has not landed, else ''. An after entry the
    plan does not hold, a self reference, or an unreadable after list holds with its reason; a RETIRED
    predecessor is skipped; a DEFERRED predecessor holds with its reason. A predecessor counts landed when
    unit_landed says so (state DONE or every sub unit recorded landed). Every unreadable value holds, never
    admits."""
    if not isinstance(sub, str) or not sub:
        raise ValueError("sub must be a non-empty string")
    if not isinstance(unit, dict):
        raise ValueError("unit must be a dict")
    if not isinstance(units, list):
        raise ValueError("units must be a list")
    names = unit.get("after") or []
    if not isinstance(names, list) or any(not isinstance(d, str) or not d.strip() for d in names):
        return "NO-DATA unreadable after on unit %s" % unit.get("id")
    uid = unit.get("id")
    if not isinstance(uid, str) or not uid:
        return "NO-DATA unreadable after on unit %s" % uid
    by_id = {u.get("id"): u for u in units if isinstance(u, dict)}
    for dep in names:
        if dep == uid:
            return "NO-DATA unit %s names itself in after" % uid
        d = by_id.get(dep)
        if d is None:
            return "%s held: after %s, which the plan does not hold" % (sub, dep)
        state = d.get("state")
        if state == "RETIRED":
            continue
        if state == "DEFERRED":
            return "%s held: after %s, DEFERRED to a later release" % (sub, dep)
        if not unit_landed(d):
            return "%s held: after %s (%s), not yet landed" % (sub, dep, state)
    return ""


def depends_held(sub, unit, units):
    """The hold reason when `unit` names a predecessor that has not landed, else ''. A predecessor is anything in the
    UNION of depends_on and after (2026-10-14: two release plans in a row filled only `after`, and the pool read
    only depends_on, so units were admitted out of order on shared files; plan_lint AFTER-ORDER refuses that plan
    shape, this is the second end). Every predecessor must be a unit the plan holds and unit_landed; an unreadable
    list or an unknown name holds with its reason (never admits)."""
    deps = []
    for key in ("depends_on", "after"):
        names = unit.get(key) or []
        if not isinstance(names, list) or any(not isinstance(d, str) or not d.strip() for d in names):
            return "NO-DATA unreadable %s on unit %s" % (key, unit.get("id"))
        deps.extend(d for d in names if d not in deps)
    by_id = {u.get("id"): u for u in units if isinstance(u, dict)}
    for dep in deps:
        d = by_id.get(dep)
        if d is None:
            return "%s held: unit %s depends on %r, which the plan does not hold" % (sub, unit.get("id"), dep)
        if d.get("state") == "RETIRED":   # withdrawn by the owner: there is nothing to wait for, the dependent is released
            continue
        if d.get("state") == "DEFERRED":  # moved to a later release: the dependent waits for that release, and says so
            return "%s held: unit %s depends on %s, DEFERRED to a later release" % (sub, unit.get("id"), dep)
        if not unit_landed(d):
            return "%s held: unit %s depends on %s (%s), not yet landed" % (sub, unit.get("id"), dep, d.get("state"))
    return ""


def _owns_judgeable(unit):
    """True when the unit carries an owns list the OWNS rule can judge path by path."""
    owns = unit.get("owns") if isinstance(unit, dict) else None
    return isinstance(owns, list) and bool(owns) and all(isinstance(o, str) and o.strip() for o in owns)


def lint_refusal(unit, sub, spec_text):
    """LL1.c: "" or "LINT <RULE>: <text> | FIX: <fix>" for the first BLOCKING or NO-DATA finding the plan lint's own
    hard_findings returns for ONE section. ADVISORY findings admit. A lint that cannot run refuses with NO-DATA: doubt
    never admits. START is changed to Start as plan_lint._line does, so loop_pass.sh never counts a lint line as a
    started unit. This refusal is independent of BROTHER_RUNFLOW_LINT: every other lint rule keeps its mode.
    A unit whose owns is absent or unreadable leaves the OWNS rule nothing to judge path by path; that plan level
    finding is the lint report's own, so the pool still refuses every OWNS finding about a path a section names
    outside a readable owns, plus every PROTECTED-TOUCH finding."""
    try:
        findings = plan_lint.hard_findings(unit.get("id") if isinstance(unit, dict) else None, sub, unit, spec_text)
    except Exception as exc:
        return "NO-DATA the lint could not run (%s)" % type(exc).__name__
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") not in ("BLOCKING", "NO-DATA"):
            continue
        if finding.get("rule") == plan_lint.RULE_OWNS and not _owns_judgeable(unit):
            continue
        line = "LINT %s: %s | FIX: %s" % (finding.get("rule"), finding.get("text"), finding.get("fix"))
        return line.replace("START", "Start")
    return ""


def admissible(plan, sub):
    """Empty only for one known, open owner and a subunit not recorded landed."""
    if not isinstance(sub, str) or not sub or not isinstance(plan, dict):
        return "NO-DATA invalid plan or subunit"
    units = plan.get("units")
    if not isinstance(units, list) or any(not isinstance(u, dict) for u in units):
        return "NO-DATA unreadable units"
    owners = []
    for unit in units:
        subs = unit.get("sub_units", [])
        if not isinstance(subs, list):
            return "NO-DATA unreadable subunits"
        ids = [s.get("id") if isinstance(s, dict) else s for s in subs]
        if any(not isinstance(s, str) or not s for s in ids):
            return "NO-DATA invalid subunit id"
        owners.extend((unit, s) for sid, s in zip(ids, subs) if sid == sub)
    if len(owners) != 1:
        return "NO-DATA unknown or ambiguous subunit %s" % sub
    unit, entry = owners[0]
    state = unit.get("state")
    if not isinstance(state, str) or not state.strip():
        return "NO-DATA missing unit state"
    state_word = state.strip()
    if state_word != state:
        return "NO-DATA corrupt unit state %r" % state
    if state_word == "DONE":
        return "%s unit state DONE" % sub
    if plan_store.release_refusal(state_word):   # DEFERRED, RETIRED: the plan moved it out of this release (2026-09-29)
        return "%s %s" % (sub, plan_store.release_refusal(state_word))
    # THE S4 HOLD (Codex and Fable, 2026-09-28): a unit whose execution_stage is S4 waits for the proof pair, and the
    # plan releases it by changing the field, never a spec score crossing the floor. Measured: all 16 RL sub units
    # were admissible while their parents carried S4. A stage this reader does not know refuses; none admits.
    stage = unit.get("execution_stage")
    if stage == "S4":
        return "%s held: unit %s is execution stage S4, after the proof pair" % (sub, unit.get("id"))
    if stage is not None:
        return "NO-DATA unknown execution stage %r on unit %s" % (stage, unit.get("id"))
    # DEPENDS ON IS HONOURED HERE, BETWEEN UNITS (2026-09-30): the pool started RL4 while RL3 had not landed, so the
    # owner's return of RL3 to RL5 (9223e2922) had to park RL4 and RL5 under S4 by hand and release each in a window.
    # A predecessor counts as landed when its state is DONE or when every one of its sub units is recorded landed
    # (the code exists before the closer runs). A predecessor the plan does not hold, or an unreadable list, HOLDS:
    # an unknown never admits. Inside a unit the sub unit list order is the contract (the first unlanded starts).
    held = depends_held(sub, unit, units)
    if held:
        return held
    held = plan_store.supply_hold(sub)   # the owner's proof supply (2026-10-04): salvage, diag_apply and the pool all route here
    if held:
        return held
    if isinstance(entry, dict) and entry.get("state") in ("DONE", "LANDED"):
        return "%s already landed (%s)" % (sub, state_word)
    evidence = unit.get("evidence", "")
    if not isinstance(evidence, str):
        return "NO-DATA unreadable landing evidence"
    if plan_store.sub_landed(sub, evidence):
        return "%s already landed (%s)" % (sub, state_word)
    # THE LANDING GATE'S DONE CHECK, BEFORE ANY RUNNER (2026-09-29): the SPEC floor is 8, and a refused done check costs
    # only one point, so a 9 whose check land_batch refuses was admitted and every green build it made was dropped at
    # landing. Every admission path (this pass, salvage promote, diag_apply) routes here. No readable spec refuses.
    spec = unit.get("spec")
    try:
        with open(spec, encoding="utf-8") as fh: spec_text = fh.read()
    except (OSError, TypeError, ValueError, UnicodeDecodeError):
        return "NO-DATA unit %s spec %r cannot be read: its done check is unknown" % (unit.get("id"), spec)
    refusal = spec_check.gate_refusal(spec_text, sub)
    if refusal:
        return "DONE CHECK refused by the landing gate: %s; route: spec repair" % refusal
    # LL1.c: PROTECTED-TOUCH and OWNS refuse whatever BROTHER_RUNFLOW_LINT says (the mode still governs every other
    # lint rule). ONE place: the pass and diag_apply.py and salvage.py inherit it with no edit.
    refusal = lint_refusal(unit, sub, spec_text)
    if refusal:
        return refusal
    return ""


START_WAIT_S = 30.0   # a new runner that is still alive this long after its spawn counts as started, PID file or not


def spawned(proc, sub, runs_dir=RUNS, wait_s=START_WAIT_S, sleep=None):
    """'' once the runner `proc` is known to have started, else why it is no runner (X2 finding 5, 2026-09-27).

    unit_runner refuses at startup, exit 2 and no run folder, when its sub unit's lock cannot be taken (another runner
    holds it, or the lock or folder cannot be made), and a held loop stops it there too. The pool counted every spawn
    as started, so the pass read the refusal as productive work. Started means a run folder of this sub unit holds a
    PID file naming it (what unit_runner writes right after its lock), or it is still alive after wait_s; an exit
    before either is a refusal, reported and never counted. FAIL DIRECTION: an unreadable PID file is not a start."""
    sleep = sleep or time.sleep   # resolved at call time: a test that lifts this def compiles it without the module's imports
    end = time.monotonic() + wait_s
    while True:
        for pid_file in glob.glob(os.path.join(runs_dir, glob.escape(sub) + "-*", "PID")):
            try:
                with open(pid_file, encoding="utf-8") as fh:
                    if fh.read().strip() == str(proc.pid): return ""
            except OSError:
                continue   # sbe: allow-silent another run's PID file that cannot be read never names this runner
        rc = proc.poll()
        if rc is not None:
            return "runner exit %d before it started" % rc
        if time.monotonic() >= end:
            return ""
        sleep(0.1)


# PRIORITY: FINISH FIRST, BY EXPECTED COST TO FINISH (Codex and Opus, 2026-09-27). Read once per pass from the ledger the
# runners already write (unit_ledger.last_rows, the one dedupe). A unit with history ranks by sub units left x grades per
# pass; a unit with none cannot be ranked, so EXPLORE slots go to fresh units first and the rest follow the ranked ones.
# A unit at SUNK_MIN or more grades under SUNK_RATE passes buys no builds until its spec or a judging tool changes: more
# attempts at an agreed failure buy nothing, and the skip line names the failure CLASS so the right fix is routed to it.
SUNK_MIN, SUNK_RATE = 50, 0.05
ROUTE = {"CONTRACT": "refused before any test ran: fix the contract or the screen at the source, no build buys this",
         "PROOF": "its tests pass without the code or miss mutations: first check whether the behaviour already exists (then close it by its done check), else diagnose the missing fact",
         "SUITE": "suite red: diagnose the first failing test; a requirement no build can meet is a spec repair, a varying failure gets one build with the failing lines"}


from unit_ledger import fail_class  # noqa: E402  (the one reader of a grader refusal's class)


def unit_record(rows):
    """{unit: [grades, passes, last graded at, {class: fails}]} over graded ledger rows. No rows: {} (every unit fresh)."""
    rec = {}
    for r in rows:
        if not isinstance(r, dict) or r.get("grade") not in ("PASS", "FAIL") or not isinstance(r.get("unit"), str): continue
        x = rec.setdefault(r["unit"], [0, 0, 0.0, {}])
        x[0] += 1; x[1] += r["grade"] == "PASS"
        x[2] = max(x[2], r.get("run_at") if isinstance(r.get("run_at"), (int, float)) else 0.0)
        if r["grade"] == "FAIL":
            c = fail_class(r.get("refusal")); x[3][c] = x[3].get(c, 0) + 1
    return rec


def ranked(units, rec, in_progress, explore, buildable=lambda u: True):
    """Admission order: units with history one sub unit from closing, then up to `explore` fresh units (minus fresh ones already in progress), then units with history by
    sub units left x grades per pass (lowest first), then the other fresh units. Input order breaks ties. Buildable fresh
    units come first, so an exploration slot is never spent on a unit its spec floor or its council will refuse."""
    fresh = sorted((u for u in units if u["id"] not in rec), key=lambda u: not buildable(u))
    known = sorted((u for u in units if u["id"] in rec),
                   key=lambda u: remaining_of(u) * rec[u["id"]][0] / max(rec[u["id"]][1], 0.5))
    k = max(0, explore - sum(1 for x in in_progress if x not in rec))
    # A UNIT ONE SUB UNIT FROM CLOSING GOES BEFORE EXPLORATION (2026-09-27, measured on run 10's first pass): the
    # exploration slot gave L5d.a the shared scripts/required_fast.sh and L5f-f, L5f's last sub unit, waited behind it.
    closing = [u for u in known if remaining_of(u) == 1]
    return closing + fresh[:k] + [u for u in known if remaining_of(u) != 1] + fresh[k:]


def sunk(rec_u):
    """True when a unit's own grades say more attempts buy nothing: SUNK_MIN or more grades, under SUNK_RATE passes."""
    return bool(rec_u) and rec_u[0] >= SUNK_MIN and rec_u[1] < SUNK_RATE * rec_u[0]


# THE FINISH-FIRST LIMITS, READ BY THE ADMISSION CONTROLLER (ACC3.b). scripts/loop/wip_status.py prints work in progress
# against the owner's limits, but nothing acted on it: the pool started new units however far over the estate was.
# This block loads that reader from exactly the file the module constant names, renders its verdict for the pass
# digest, writes the one dated record the report reads, and under BROTHER_WIP_GATE=enforce only holds a unit that
# has no landed sub unit and no live or waiting build, so work under way keeps its runners and the estate finishes
# the oldest work first. The default is "report": it measures, prints and holds nothing, so landing this changes no
# admission. A reading that cannot be taken is NO-DATA, and under enforce NO-DATA holds exactly as OVER does, so an
# unread estate never reads as the safe case.
# THE READER SHIPS BESIDE THE POOL (2026-10-01): it lived in scripts/, two levels up, a path the deploy never staged
# (its bin stage holds scripts/loop only) and the freeze resolved to ~/.claude/wip_status.py, a file that never exists:
# every deployed pass read the WIP status as NO-DATA and the deploy canary was red. A sibling path is staged by the
# deploy, traced by the freeze, and found the same way in a checkout and in a deploy.
WIP_STATUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wip_status.py")
WIP_RECORD = os.path.expanduser("~/.claude/evidence/wip-status.json")
WIP_MAX_AGE_S = 1800.0
WIP_MODES = ("report", "enforce", "off")
WIP_WORD = {0: "PASS", 1: "OVER", 2: "NO-DATA"}


def wip_mode(raw: Optional[str]) -> str:
    """The BROTHER_WIP_GATE value, stripped and lowered: unset or empty is "report"; "report", "enforce" and "off"
    map to themselves; any other value is "enforce", so a switch somebody set and nobody can read never reads as
    the safe case. A value that is neither None nor a string is a caller defect and raises ValueError."""
    if raw is None:
        return "report"
    if not isinstance(raw, str):
        raise ValueError("BROTHER_WIP_GATE must be a string or None, not %s" % type(raw).__name__)
    value = raw.strip().lower()
    if not value:
        return "report"
    if value in WIP_MODES:
        return value
    return "enforce"


def _wip_finite(value, name: str) -> float:
    """The number as a float, or ValueError: a bool, a string, NaN and both infinities are refused, never read as a
    time, so a hostile argument is a refusal and never a TypeError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a finite number" % name)
    if value != value or value == float("inf") or value == float("-inf"):
        raise ValueError("%s must be a finite number" % name)
    return float(value)


def _wip_rows_ok(rows, code) -> bool:
    """True only for a list of three-string rows and an integer code in 0, 1, 2. A reader that answers any other
    shape is NO-DATA, never a pass this pool did not read."""
    if isinstance(code, bool) or not isinstance(code, int) or code not in (0, 1, 2):
        return False
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return False
        for cell in row:
            if not isinstance(cell, str):
                return False
    return True


def _wip_args(rows, code, mode) -> None:
    """The one argument validation wip_line and write_wip_record both route through: a list of three-string rows, an
    integer code in 0, 1, 2 and one of the three modes. Anything else is a caller defect and raises ValueError."""
    if not _wip_rows_ok(rows, code):
        raise ValueError("rows must be a list of three-string rows and code 0, 1 or 2")
    if not isinstance(mode, str) or mode not in WIP_MODES:
        raise ValueError("mode must be report, enforce or off, not %r" % (mode,))


def wip_reading(loop_ref: str, main_ref: str) -> Tuple[List[Tuple[str, str, str]], int]:
    """The reader's rows and code. scripts/loop/wip_status.py is loaded from exactly WIP_STATUS with the three loading
    lines below (the one route the safety screen and the proof freeze both admit), never through an import by name:
    the module is never put in sys.modules, so a cached copy from another path cannot answer and no later import
    picks this one up. WIP_STATUS's own directory is inserted at sys.path[0] before exec_module and kept there
    through the measure call (wip_status imports heavy_slot from scripts/ at call time), and sys.path is restored
    exactly in a finally, so no scripts copy outranks a loop copy afterwards. Every failure (a missing file, an
    import error, a reader crash, a malformed return) is NO-DATA naming WIP_STATUS, never a pass this pool did not
    read. A loop_ref or main_ref that is not a non-empty string is a caller defect: ValueError, before anything is
    loaded."""
    if not isinstance(loop_ref, str) or not loop_ref:
        raise ValueError("loop_ref must be a non-empty string")
    if not isinstance(main_ref, str) or not main_ref:
        raise ValueError("main_ref must be a non-empty string")
    saved_path = list(sys.path)
    try:
        sys.path.insert(0, os.path.dirname(WIP_STATUS))
        spec = importlib.util.spec_from_file_location("wip_status", WIP_STATUS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows, code = module.measure(loop_ref, main_ref)
        if not _wip_rows_ok(rows, code):
            raise ValueError("measure answered a shape that is not a list of three-string rows and a code in 0, 1, 2")
        return (rows, code)
    except Exception as exc:   # sbe: allow-silent a reader that is missing, crashes or answers a malformed shape is NO-DATA naming WIP_STATUS, never a pass this pool did not read
        return (["wip-status", "NO-DATA"] and [("wip-status", "NO-DATA", "%s: %s (%s)" % (type(exc).__name__, exc, WIP_STATUS))], 2)
    finally:
        sys.path[:] = saved_path


def finish_first_hold(u: dict, code: int, mode: str, in_progress: Set[str]) -> str:
    """The reason a NEW unit may not start while the finish-first limits are OVER or unread, beginning
    "FINISH-FIRST:", or "" when it may start. "" when the reading passed, when the gate is report or off, when the
    unit already has a live runner or a waiting build (it is in in_progress), or when at least one of its sub units
    is recorded landed, so work under way keeps its runners and the oldest work is finished first."""
    if not isinstance(u, dict):
        raise ValueError("u must be a dict")
    if isinstance(code, bool) or not isinstance(code, int) or code not in (0, 1, 2):
        raise ValueError("code must be an integer in 0, 1, 2, not %r" % (code,))
    if not isinstance(mode, str) or mode not in WIP_MODES:
        raise ValueError("mode must be report, enforce or off, not %r" % (mode,))
    if not isinstance(in_progress, set):
        raise ValueError("in_progress must be a set")
    if code == 0 or mode != "enforce":
        return ""
    unit_id = u.get("id")
    if isinstance(unit_id, str) and unit_id in in_progress:
        return ""
    subs = u.get("sub_units")
    if not isinstance(subs, list):
        raise ValueError("u['sub_units'] must be a list")
    if remaining_of(u) < len(subs):
        return ""
    if code in (1, 2):
        return ("FINISH-FIRST: the finish-first limits read %s under gate enforce: finish the oldest work "
                "before starting a new unit" % WIP_WORD[code])
    return ""


def wip_line(rows: List[Tuple[str, str, str]], code: int, mode: str) -> str:
    """One line for the pass digest, starting "WIP     ", with the verdict word (PASS for 0, OVER for 1, NO-DATA for
    2), the gate mode, and every row that is not PASS with its own name, verdict and detail."""
    _wip_args(rows, code, mode)
    detail = "; ".join("%s %s %s" % (row[0], row[1], row[2]) for row in rows if row[1] != "PASS")
    line = "WIP     %s gate %s" % (WIP_WORD[code], mode)
    return line + (": " + detail if detail else "")


def write_wip_record(rows: List[Tuple[str, str, str]], code: int, mode: str,
                     path: str = WIP_RECORD, now: Optional[float] = None) -> None:
    """One complete, dated reading written to a sibling temporary file and moved over `path` with os.replace, so no
    reader ever sees a torn file and two pools on one machine simply leave the last complete record. The same
    argument checks as wip_line, plus path a non-empty string and now None or a finite number that is not a bool,
    all run before any file is touched: a hostile argument is a refusal, never a TypeError."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    _wip_args(rows, code, mode)
    at = time.time() if now is None else _wip_finite(now, "now")
    record = {"at": at, "mode": mode, "code": code, "rows": [list(row) for row in rows]}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass   # sbe: allow-silent the temporary is already gone or cannot be removed; the write itself still raises
        raise


def read_wip_record(path: str = WIP_RECORD, now: Optional[float] = None,
                    max_age_s: float = WIP_MAX_AGE_S) -> Tuple[Optional[dict], str]:
    """(record, "") for the reading write_wip_record left, else (None, why) with why starting NO-DATA: the file is
    missing or unreadable, it is not a JSON object with a numeric at, a code in 0, 1, 2, a mode string and a list of
    three-item rows, it is dated more than 60 s in the future, or it is older than max_age_s. A path that is not a
    non-empty string, or a now or max_age_s that is not a finite number (a bool included), raises ValueError: a
    hostile argument is a refusal, never a TypeError."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    at_now = time.time() if now is None else _wip_finite(now, "now")
    age_limit = _wip_finite(max_age_s, "max_age_s")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        return (None, "NO-DATA record unreadable at %s: %s" % (path, exc))
    except ValueError as exc:
        return (None, "NO-DATA record is not JSON: %s" % exc)
    why = _wip_record_refusal(raw, at_now, age_limit)
    if why:
        return (None, why)
    return (raw, "")


def _wip_record_refusal(raw, at_now: float, age_limit: float) -> str:
    """Why this parsed record is no reading, or "": every field the report and the gate read is checked here, and a
    value that cannot all be true (a NaN at, a bool code) is corrupt input, never a reading."""
    if not isinstance(raw, dict):
        return "NO-DATA record is not a JSON object"
    at = raw.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at != at or at == float("inf") or at == float("-inf"):
        return "NO-DATA record has no numeric at"
    code = raw.get("code")
    if isinstance(code, bool) or not isinstance(code, int) or code not in (0, 1, 2):
        return "NO-DATA record has no code in 0, 1, 2"
    if not isinstance(raw.get("mode"), str):
        return "NO-DATA record has no mode string"
    rows = raw.get("rows")
    if not isinstance(rows, list):
        return "NO-DATA record has no list of rows"
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return "NO-DATA record has a row that is not three items"
        for cell in row:
            if not isinstance(cell, str):
                return "NO-DATA record has a row cell that is not a string"
    if at - at_now > 60.0:
        return "NO-DATA record is dated more than 60 s in the future"
    if at_now - at > age_limit:
        return "NO-DATA record is older than %.0f s" % age_limit
    return ""


def _seat_facts(env, which=None):
    """(native seats, workers per round, seat cap) when this pass builds with native sessions, else None.
    Native needs loop_switches.native_on(env, which) true and a non blank BROTHER_PIN_MODEL, the two facts
    native_intent starts from; native_worker is imported here, only when on, exactly as the runner does.
    seats(env) must be a positive whole number and WORKERS_PER_ROUND (default "5", the runner's own
    default, unit_runner line 684) a whole number of at least 1; anything else raises ValueError. The floor
    of 1 is deliberate: on the launcher defaults of 2 seats and 6 workers a plain division is 0 and the loop
    would never start. Every unreadable value admits nothing, so the caller sets the seat cap to 0."""
    try:
        env.get("BROTHER_PIN_MODEL")
    except AttributeError:
        raise ValueError("env must be a mapping, not %s" % type(env).__name__)
    if which is not None and not isinstance(which, str):
        raise ValueError("which must be a string or None, not %s" % type(which).__name__)
    try:
        import loop_switches
    except ImportError as exc:
        raise ValueError("loop_switches cannot be imported, so the native switch is unknown: %s" % exc)
    try:
        on = loop_switches.native_on(env, which)
    except (ValueError, KeyError) as exc:
        raise ValueError("the native switch cannot be read: %s" % exc)
    if not on:
        return None
    pin = env.get("BROTHER_PIN_MODEL")
    if not isinstance(pin, str) or not pin.strip():
        return None
    try:
        import native_worker
    except ImportError as exc:
        raise ValueError("native_worker cannot be imported, so the native seat count is unknown: %s" % exc)
    try:
        count = native_worker.seats(env)
    except ValueError as exc:
        raise ValueError("the native seat count is unreadable: %s" % exc)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("native seats must be a positive whole number, not %r" % (count,))
    raw = env.get("WORKERS_PER_ROUND", "5")
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError("WORKERS_PER_ROUND must be a whole number, not %r" % (raw,))
    text = str(raw).strip()
    workers = int(text) if text.isdigit() else 0
    if workers < 1:
        raise ValueError("WORKERS_PER_ROUND must be a whole number of at least 1, not %r" % (raw,))
    return (count, workers, max(1, count // workers))


def seat_cap(env: dict, which=None) -> Optional[int]:
    """The native seat cap for one pass, or None when this pass is not native (REQ-LA5-A-01, REQ-LA5-A-03):
    max(1, native_worker.seats(env) // WORKERS_PER_ROUND). Every unreadable value raises ValueError, and an
    unreadable value admits nothing (the caller sets the cap to 0)."""
    facts = _seat_facts(env, which)
    return None if facts is None else facts[2]


def load_hold(reading=None) -> str:
    """The load hold for one pass, or "" when the machine is free (REQ-LA5-B-01, REQ-LA5-B-04, REQ-LA5-B-05).

    Loads load_rule.py from exactly LOAD_RULE (a test swaps the module constant, never an argument: the
    deploy freeze must resolve every dynamic loader to one literal path) with the three loading lines
    wip_reading uses (spec_from_file_location, module_from_spec, exec_module, never sys.modules), takes
    `reading` (default the loaded module's own load_reading: the seam a caller or a test drives), asks the
    loaded module's machine_busy, and answers the busy line when the one minute load is at or above the core
    count. Any failure (module missing, reading raised, value refused) is NO-DATA and HOLDS, never admits.
    `reading` is a callable returning (load1, ncpu); anything else is a caller defect and raises ValueError."""
    if reading is not None and not callable(reading):
        raise ValueError("reading must be a callable or None, not %s" % type(reading).__name__)
    try:
        spec = importlib.util.spec_from_file_location("load_rule", LOAD_RULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        read = reading or module.load_reading
        load1, ncpu = read()
        if module.machine_busy(load1, ncpu):
            return "machine busy: one minute load %s is at or above %s core(s)" % (load1, ncpu)
        return ""
    except Exception as exc:   # sbe: allow-silent a missing module, a reading that raised or a value it refused is NO-DATA and HOLDS, never a free machine
        return "NO-DATA load reading unavailable: %s: %s" % (type(exc).__name__, exc)


def room_of(burn_free: int, wip_free: int, seat_free: Optional[int] = None, busy: str = "") -> Tuple[int, str]:
    """(room, why): room is the smallest of the three free counts, clamped at 0, and why names the binding
    cap (REQ-LA5-A-06): one of "budget cap", "WIP cap", "no seat", ties naming the earlier of that order.
    seat_free is None when the pass has no seat cap. A non blank `busy` (the load hold's own text,
    REQ-LA5-B-01) forces room 0 and is the reason, before any cap is compared, so a busy machine admits no
    new runner whoever caused the load. burn_free and wip_free must be ints and not bool, seat_free an int or
    None and busy a string; anything else raises ValueError."""
    for name, value in (("burn_free", burn_free), ("wip_free", wip_free)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s must be an int, not %s" % (name, type(value).__name__))
    if seat_free is not None:
        if isinstance(seat_free, bool) or not isinstance(seat_free, int):
            raise ValueError("seat_free must be an int or None, not %s" % type(seat_free).__name__)
    if not isinstance(busy, str):
        raise ValueError("busy must be a string, not %s" % type(busy).__name__)
    if busy:
        return (0, busy)
    caps = []
    for name, value in (("burn_free", burn_free), ("wip_free", wip_free)):
        caps.append((value, "budget cap" if name == "burn_free" else "WIP cap"))
    if seat_free is not None:
        caps.append((seat_free, "no seat"))
    smallest = min(value for value, _w in caps)
    why = next(w for value, w in caps if value == smallest)
    return (max(0, smallest), why)


def main():
    os.makedirs(RUNS, exist_ok=True)
    dry = "--dry" in sys.argv
    write_launch_stamp()   # I3.a: the pass writes the seat root stamp before it ranks or admits any unit; a refused stamp leaves the pass as it was
    # THE RUN'S LOGIN HOLDER (1.1.6 S2): taken from this process's environment, so only the unit runners it starts are
    # told it, each with the descriptor itself (brother_login.take_hold). Nothing changes when no holder is named.
    hold_env, hold_fds = {}, ()
    if os.environ.get("BROTHER_LOGIN_HOLD_FD"):
        import brother_login
        hold_env, hold_fds = brother_login.take_hold()
    plan = json.load(open(_PP.selected(), encoding="utf-8"))

    for _d in sorted(glob.glob(os.path.join(RUNS, "*-*/"))):
        try:
            reconcile_dead(_d, _pid_alive)
        except (ValueError, OSError) as exc:
            print("RECONCILE NO-DATA: %s %s" % (_d, exc))

    # A DRAINING PROOF RUN STARTS NOTHING (objection 10): its ending marker exists, or even the shortest call would
    # outlive its deadline, so every call a new runner made would be refused at registration. An unreadable launch record
    # reads as draining. Dead runners are still reconciled above; the next pass after the drain re-seats DRAINED units.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import proof_ledger
    if proof_ledger.draining():
        print("DRAINING the proof run is ending or too close to its deadline for any call: no runner starts this pass")
        return

    alive = alive_runners(RUNS, plan)
    alive_units = {u for u, _ in alive}

    last = {}; last_at = {}; start_at = {}; last_dir = {}   # D31: start_at is the attempt start the parked test compares a fact with
    for d in sorted(glob.glob(os.path.join(RUNS, "*-*/")),
                   key=lambda p: (_dir_time(p), os.path.basename(p.rstrip("/")))):
        sub = re.sub(r"-\d{6}$", "", os.path.basename(d.rstrip("/")))
        last[sub] = status_word(os.path.join(d, "STATUS"))
        last_at[sub] = _dir_time(d)
        start_at[sub] = _start_time(d)
        last_dir[sub] = d

    # A unit the plan moved out of this release (plan_store.release_refusal: DEFERRED, RETIRED) is LEFT OUT with its
    # reason, exactly as an out of scope one is; admissible() refuses its sub units too, for every other caller.
    _open = [u["id"] for u in plan["units"] if u["state"] != "DONE" and u.get("sub_units") and u.get("spec")]
    _later = {u["id"]: plan_store.release_refusal(u["state"]) for u in plan["units"] if u["id"] in _open and plan_store.release_refusal(u["state"])}
    _out = [x for x in _open if not SCOPE.match(x)] + ["%s (%s)" % (x, why) for x, why in _later.items() if SCOPE.match(x)]
    print("SCOPE   %s | admits %d of %d open units%s" % (SCOPE_SRC, len(_open) - len(_out), len(_open),
                                                         ("; left out: " + ", ".join(_out[:12]) + (" ..." if len(_out) > 12 else "")) if _out else ""))
    units = sorted((u for u in plan["units"] if u["state"] != "DONE" and u["id"] not in _later and SCOPE.match(u["id"]) and u.get("sub_units") and u.get("spec")),
                   key=lambda u: (remaining_of(u), COUNCIL.index(u["id"]) if u["id"] in COUNCIL else 99, u["id"]))
    # THE SCORER'S EXIT CODE IS READ (assurance lint 2026-09-22): a scorer that crashed left the pool admitting on the PREVIOUS
    # score file, silently. The score file is the record and the scorer refreshes it, so a failed refresh is SAID on the pass
    # digest (the pulse and the digest carry it) and the previous file is used; a missing file still holds everything.
    # THE JUDGE CALIBRATOR runs every pass (owner 2026-09-23): its file is what lets a model judge gate; a crash here is one printed line, never a held pool.
    _jc = subprocess.run([sys.executable, os.path.expanduser("~/.claude/bin/judge_calibrate.py")], capture_output=True, text=True, timeout=120)
    print((_jc.stdout.strip().splitlines() or ["CALIB   NO-DATA: calibrator printed nothing (exit %d)" % _jc.returncode])[-1][:160])
    _sc = subprocess.run([sys.executable, os.path.expanduser("~/.claude/bin/spec_score.py"), "--json", os.path.expanduser("~/.claude/evidence/spec-scores.json")], capture_output=True, text=True, timeout=120)
    if _sc.returncode != 0:
        print("SPECS   NO-DATA: spec_score.py exit %d this pass; admission reads the PREVIOUS score file (look at the scorer before trusting a new spec)" % _sc.returncode)
    try:
        SCORES = json.load(open(os.path.expanduser("~/.claude/evidence/spec-scores.json")))
    except (OSError, ValueError):
        SCORES = {}  # unreadable scores hold everything: never a pass
    try:
        COUNCIL_STATE = json.load(open(os.path.expanduser("~/.claude/evidence/spec-council.json")))
    except (OSError, ValueError):
        COUNCIL_STATE = {}
    started = 0
    spec_of = {u["id"]: u.get("spec") for u in plan["units"]}   # lowercase on purpose: computed from the plan at import, not a constant a test loader may lift (test_unit_runner_probe_gate lifts ALL CAPS assigns)
    busy, unknown_alive = busy_paths(sorted(alive), spec_of)   # path -> sub unit patching it: live runners, READY builds (below), then this pass's starts
    # owner budget 2026-09-20: the night's OpenRouter money is a real ceiling, so lane width is sized by what it funds.
    # The landing stage is serial anyway, so a cap here costs no delivery: it stops buying builds the night cannot land.
    try:
        # BROTHER_LANES, owner order 2026-09-22 ("as many lanes and workers as this CPU can take"): the planned width
        # the money guard caps at. Only a plain digit string is passed through; anything else leaves the guard's own
        # default in place, because a width nobody can read is not a width.
        _lanes = os.environ.get("BROTHER_LANES", "")
        BURN_CAP = int(subprocess.run([sys.executable, os.path.expanduser("~/.claude/bin/burn_guard.py")]
                                      + (["--planned", _lanes] if _lanes.isdigit() else []),
                                      capture_output=True, text=True, timeout=120).stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        # AN UNREADABLE GUARD ADMITS NOTHING (money review, 2026-09-27): the old floor of 2 started paid runners on
        # money nobody had measured; the driver's own read names the cause and holds or stops the run.
        BURN_CAP = 0
    # WORK IN PROGRESS CAP, owner order 2026-09-21. Little's law, measured on this board: 38 units in flight holding
    # 140 sub units at 2.79 sub units an hour is 50 hours before ANY of them can be expected to arrive, whatever the
    # order. Capping the units in flight is what converts throughput into closures, and it is free: it buys fewer
    # builds, not slower ones. The cap binds TOGETHER with the money cap, and the smaller of the two wins, because a
    # lane the money cannot fund and a lane the cap will not open are both lanes that must not start.
    # A READY BUILD WAITING TO LAND IS WORK IN PROGRESS (Codex, 2026-09-27): delivery backlog counts against the cap, so
    # the pool does not open new units while finished ones queue for landing.
    WIP = int(os.environ.get("BROTHER_WIP", "5"))
    SPEC_FLOOR = int(os.environ.get("BROTHER_SPEC_FLOOR", "8"))   # target 9, floor 8 (owner, 2026-09-21)
    _ready = {u["id"] for u in units if any(waits_to_land(last.get(s, "")) and not plan_store.sub_landed(s, u.get("evidence") or "")
                                            for s in u["sub_units"])}
    in_progress = alive_units | _ready
    _b, _unk = busy_paths([(x["id"], _s) for x in units if x["id"] in _ready - alive_units for _s in x["sub_units"]
                           if waits_to_land(last.get(_s, "")) and not plan_store.sub_landed(_s, x.get("evidence") or "")], spec_of)
    busy.update(_b); unknown_alive.extend(_unk)   # LS1.c: a READY build's files stay busy until it lands
    # LA5.a: the room is also capped by the native seats over the workers per round, and the BUDGET line prints
    # the seat count. The seat facts are read once per pass; an unreadable seat count or workers value admits
    # nothing (cap 0) and says NO-DATA, never a seat cap a reader could trust. os.environ is a mapping, not a
    # dict, so _seat_facts checks it has get() rather than isinstance(env, dict). The binding cap is kept in
    # cap_why, NOT in why: `why` is the per unit skip reason and the loop below overwrites it.
    _seat_n = 0; _seat_w = 0; _seat_cap = None; _seat_word = "native seats off"
    try:
        _seat_facts_now = _seat_facts(os.environ)
    except ValueError as exc:
        _seat_cap = 0
        _seat_word = "native seats NO-DATA %s" % exc
    else:
        if _seat_facts_now is not None:
            _seat_n, _seat_w, _seat_cap = _seat_facts_now
            _seat_word = "native seats %d, %d worker(s) per round, seat cap %d runner(s)" % (_seat_n, _seat_w, _seat_cap)
    # LA5.b: the load hold, read ONCE per pass beside the seat read. THE SEAM: BROTHER_LOAD_CAP unset or
    # empty means this pass reads no load at all, so admission is exactly as it was before LA5.b and the
    # existing pool suites stay green on a busy machine. "auto" reads the real load through the pool's own
    # load_reading; any other value is the load1 a test drives the whole pass with, paired with the host's
    # core count. Anything unreadable holds with NO-DATA, never admits. A dry pass reads the load too, so a
    # dry pass predicts the real one (REQ-LA5-B-01, REQ-LA5-B-04, REQ-LA5-B-06).
    _load_raw = os.environ.get("BROTHER_LOAD_CAP")
    busy_text = ""
    if _load_raw is not None and _load_raw != "":
        if _load_raw == "auto":
            busy_text = load_hold()
        else:
            busy_text = load_hold(reading=lambda _raw=_load_raw: (float(_raw.strip()), os.cpu_count() or 1))
    room, cap_why = room_of(BURN_CAP - len(alive_units), WIP - len(in_progress), _seat_cap, busy_text)
    print("BUDGET cap %d runner(s) | WIP cap %d unit(s) | %d alive, %d READY to land | %s | room for %d"
          % (BURN_CAP, WIP, len(alive_units), len(_ready - alive_units), _seat_word, room))
    # ACC3.b: the finish-first limits are read once per pass, before any unit is admitted (wiring steps 1 to 3).
    mode = wip_mode(os.environ.get("BROTHER_WIP_GATE"))
    if mode == "off":
        print("WIP     OFF: BROTHER_WIP_GATE=off, the finish-first limits were not read this pass")
        wip_code = 0
    else:
        wip_rows, wip_code = wip_reading("HEAD", os.environ.get("BROTHER_WIP_MAIN_REF") or "hub/main")
        print(wip_line(wip_rows, wip_code, mode))
        try:
            write_wip_record(wip_rows, wip_code, mode)
        except OSError as exc:
            print("WIP     record not written: %s" % exc)
    # FX-13.6: the plan lint's mode, read once per pass. report (the default) prints a unit's findings and admits it as
    # before; block skips it on a BLOCKING or NO-DATA finding; a value nobody can read is block, never off.
    try:
        lint_mode_now, lint_note = plan_lint.lint_mode()
    except ValueError as exc:
        lint_mode_now, lint_note = "block", "NO-DATA: %s" % exc
    print(("LINT    mode %s%s" % (lint_mode_now, "  (%s)" % lint_note if lint_note else "")).replace("START", "Start"))
    import unit_ledger
    REC = unit_record(unit_ledger.last_rows(os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl")))
    def _buildable(u):
        nxt = next((x for x in u["sub_units"] if not plan_store.sub_landed(x, u.get("evidence") or "") and not hold_reason(x)), None)
        return (nxt is not None and (SCORES.get(nxt, {}).get("score") or 0) >= SPEC_FLOOR
                and COUNCIL_STATE.get(u["id"], {}).get("state") != "DO-NOT-BUILD")
    units = ranked(units, REC, in_progress, int(os.environ.get("BROTHER_EXPLORE", "2")), _buildable)
    print("ORDER   %s" % " ".join("%s(%s)" % (u["id"], "%d/%d" % tuple(REC[u["id"]][:2]) if u["id"] in REC else "new") for u in units[:12]))
    # 1.1.6 S3: A SEAT LOST MID RUN. A transport is lost when the newest run of an open sub unit ended in a login class
    # CONFIG_WAIT; the loss is cleared by a seat proof newer than it (prepare again after signing in), which is also the
    # fact that re-seats the sub unit that waited. One NOTIFY-REQUEST row per (cause, run), never one per pass.
    _roles, _reg, _role_note = role_inputs()
    _lost, _refact = {}, {}   # transport -> when its login was seen lost; sub unit -> the seat proof that re-seats it
    for _s in [s for u in units for s in u["sub_units"] if not plan_store.sub_landed(s, u.get("evidence") or "")]:
        if last.get(_s) == "CONFIG_WAIT" and login_class(wait_cause(last_dir[_s])):
            _t = sub_transport(_s, _roles, _reg)
            _proved = seat_proof_at(_t) if _t else 0.0
            _seen = _dir_time(os.path.join(last_dir[_s], "STATUS")) or time.time()
            if _proved > _seen:
                _refact[_s] = _proved
                print("SEATS   %s re-seated: a seat proof at %s is newer than the loss on %s" % (_t, time.strftime("%Hh%M", time.localtime(_proved)), _s))
            else:
                _lost[_t] = max(_lost.get(_t, 0.0), _seen)
    if _lost and _role_note:
        print("SEATS   NO-DATA: %s, so no unit's transport is known and every unit parks, as before S3" % _role_note)
    for _t in sorted(_lost):
        _key = "seat-lost:login:%s" % (_t or "unknown")
        if dry:
            _said = "a run would write the row %s" % _key
        elif not os.environ.get("BROTHER_RUN_DIR"):
            _said = "NO-DATA: BROTHER_RUN_DIR is unset, so no row was written; this line is the only notice"
        else:
            try:
                import loop_intake
                _said = "row written" if loop_intake.notify_once(os.environ["BROTHER_RUN_DIR"], _key, "seat", time.time()) else "row already there"
            except (ImportError, OSError) as exc:
                _said = "NO-DATA: the notify row could not be written (%s)" % type(exc).__name__
        print("SEATS   %s lost its login at %s: its units park until a newer seat proof (sign in, then prepare again); notify %s"
              % (_t or "an unknown transport", time.strftime("%Hh%M", time.localtime(_lost[_t])), _said))
    _lost_set = set(_lost)
    _cx_role, _cx_model = codex_role(_roles, _reg)
    _cx_busy = any(sub_transport(_s, _roles, _reg) == "codex" for _u, _s in alive)
    _handover = []   # (unit, sub) parked by a lost claude seat, in pass order: the codex role may take the first

    def _launch(u, sub, env=None):
        """True when a runner for sub started (or would, on a dry pass); a refusal at startup is printed."""
        if not dry:
            log = os.path.join(RUNS, sub + ".log")
            proc = subprocess.Popen([sys.executable, os.path.expanduser("~/.claude/bin/unit_runner.py"), u["id"], sub],   # sbe: allow-silent detached by design; its outcome is its STATUS file, read by the next pass
                                    stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True,
                                    env=dict(env or os.environ, **hold_env) if hold_env else env, pass_fds=hold_fds)
            refused = spawned(proc, sub)
            if refused:
                try:
                    with open(log, encoding="utf-8", errors="replace") as fh: said = (fh.read().strip().splitlines() or ["no output"])[-1]
                except OSError as exc:
                    said = "its log cannot be read (%s)" % exc
                print("%-5s %-8s REFUSED AT STARTUP: %s: %s" % (u["id"], sub, refused, said[:160])); return False
        print("%-5s %-8s START" % (u["id"], sub))
        return True
    for u in units:
        ev = u.get("evidence") or ""
        todo = [s for s in u["sub_units"] if not plan_store.sub_landed(s, ev)]
        if not todo:
            print("%-5s every sub unit landed: run the unit done-check and mark DONE" % u["id"]); continue
        sub = next((s for s in todo if not hold_reason(s)), None)
        waiting = [s for s in u["sub_units"] if waits_to_land(last.get(s, "")) and s in todo]
        claimed = builder_claim.claim_hold(RUNS, sub) if sub is not None else ""
        why = claimed or ("runner alive" if u["id"] in alive_units else "READY build of %s waits to land" % waiting[0] if waiting
               else hold_reason(todo[0]) if sub is None else "last run of %s ended %s: needs a fact, then RUNNER_HINT" % (sub, last[sub]) if sub is not None and still_parked(last.get(sub), start_at.get(sub, 0.0), max(parked_fact(last.get(sub), u["id"], spec_path=u.get("spec")), _refact.get(sub, 0.0))) else "")
        if not why and sub is not None and transport_parked([sub], _lost_set, _roles, _reg):
            _t = sub_transport(sub, _roles, _reg)
            why = "parked: the %s seat lost its login; comes back on a newer seat proof (sign in, then prepare again)" % (_t or "unknown transport's")
            if _t == "claude":   # a claimed sub unit never gets here: its claim is its `why` above
                _handover.append((u, sub))
        if not why and sub is not None and sunk(REC.get(u["id"])) and not fact_time(u["id"], spec_path=u.get("spec")) > REC[u["id"]][2]:
            _c = max(REC[u["id"]][3].items(), key=lambda kv: kv[1])[0] if REC[u["id"]][3] else "SUITE"
            why = "SUNK %d grades, %d passes, mostly %s: %s; comes back when its spec or a judging tool changes" % (
                REC[u["id"]][0], REC[u["id"]][1], _c, ROUTE[_c])
        if not why and sub is not None and reseated_too_soon(last.get(sub, ""), last_at.get(sub, 0.0), time.time()):
            why = "skip: " + sub + " started under ten minutes ago and has not reached READY"
            try:
                print(cooldown_line(u["id"], sub, last_at.get(sub, 0.0)))   # D32: loop_pass.sh reads this as a wait, not UNPRODUCTIVE
            except ValueError:
                pass   # sbe: allow-silent an unknown time is no cooldown, so the pass keeps its alarm (the safe side)
        if not why and sub is not None and last.get(sub) == "NO-DATA":
            why = "skip: " + sub + " has an unreadable STATUS (empty or torn): unknown state is never restarted; read or remove the file"
        if not why and sub is not None and last.get(sub) in PARKED:
            print("%-5s %-8s RETRY: its spec, a judging tool or the resolved program changed after its last run ended %s" % (u["id"], sub, last[sub]))
        if not why and COUNCIL_STATE.get(u["id"], {}).get("state") == "DO-NOT-BUILD":
            why = "COUNCIL says DO NOT BUILD (%d blockers, min safety %s): repair the spec with them, re-attack, then build" % (len(COUNCIL_STATE[u["id"]].get("blockers", [])), COUNCIL_STATE[u["id"]].get("min_safety"))
        if not why and sub is not None:
            why = finish_first_hold(u, wip_code, mode, in_progress)
        if not why and sub is not None:
            sc = SCORES.get(sub, {}).get("score")
            # Owner rule 2026-09-20: no work starts on a spec under 9 of 10, and an unscored spec is held too, since
            # an absent score is not a pass. Amended 2026-09-21 by his order about the 28 sections a repair wave
            # could not lift: "Finish these minimum at 8 if 9 is impossible". 9 remains the TARGET that spec_accept
            # enforces by default; 8 is the floor below which no worker starts, so a section deliberately accepted
            # at 8 can still be built while nothing weaker ever can.
            if sc is None or sc < SPEC_FLOOR:
                why = "SPEC %s/10 (needs %d): %s" % (sc if sc is not None else "unscored", SPEC_FLOOR,
                                                     "; ".join(SCORES.get(sub, {}).get("missing", []))[:150])
        ts = touch_set(sub, u.get("spec")) if not why and sub is not None else None
        if not why and sub is not None:
            why = reviewed_route(write_set(sub, u.get("spec")))
        if not why and sub is not None:
            why = touch_conflict(ts, busy, unknown_alive)
        if not why and sub is not None:
            why = admissible(plan, sub)
        if not why and sub is not None:
            lint_lines, lint_why = plan_lint.admission(plan, u, sub, ".", os.environ, council=os.path.expanduser(plan_lint.COUNCIL_PATH))
            for line in lint_lines: print(("%-5s %-8s %s" % (u["id"], sub, line)).replace("START", "Start"))
            if lint_mode_now == "block" and lint_why:
                why = lint_why
        if why:
            print("%-5s %-8s skip: %s" % (u["id"], sub or todo[0], why)); continue
        if started >= room:
            if busy_text and cap_why == busy_text:
                print("%-5s %-8s skip: %s; no new runner this pass, remedy: wait for the load to fall or stop other heavy work"
                      % (u["id"], sub, busy_text))
            elif cap_why == "no seat":
                print("%-5s %-8s skip: no seat (%d native seat(s), %d worker(s) per round, cap %d runner(s), %d alive)"
                      % (u["id"], sub, _seat_n, _seat_w, _seat_cap, len(alive_units)))
            else:
                print("%-5s %-8s skip: budget cap reached (%d new this pass)" % (u["id"], sub, room))
            continue
        busy.update({p: sub for p in ts})
        if not _launch(u, sub):
            continue
        started += 1
        _cx_busy = _cx_busy or sub_transport(sub, _roles, _reg) == "codex"
    # 1.1.6 S3.b, D-S5 ("Codex takes any role"): an open codex role (none of its own sub units alive or started this
    # pass, its seat not lost) takes ONE sub unit parked by a lost claude seat, with that unit's role setting pinned to
    # the codex model in the child's environment. Content the codex model may not receive is never handed over.
    if _handover and _cx_role and "codex" not in _lost_set and not _cx_busy and started < room:
        u, sub = _handover[0]
        _spec = _roles.get(sub_role(sub, _roles)) or {}
        _row = _reg.get(_cx_model) or {}
        _setting = _spec.get("setting")
        _content = _spec.get("content") if isinstance(_spec.get("content"), str) else ""   # a list or a number is unhashable: unknown, never handed
        if CONTENT_ORDER.get(_content, 99) > CONTENT_ORDER.get(_row.get("privacy"), -1):
            print("%-5s %-8s HANDOVER refused: its role's content is %s and the codex model %s may carry only %s" % (u["id"], sub, _spec.get("content"), _cx_model, _row.get("privacy")))
        elif not isinstance(_setting, str) or not _setting:
            print("%-5s %-8s HANDOVER refused: its role has no setting to pin the codex model %s through" % (u["id"], sub, _cx_model))
        else:
            print("%-5s %-8s HANDOVER %s to the codex role %s (%s=%s)" % (u["id"], sub, sub, _cx_role, _setting, _cx_model))
            if _launch(u, sub, env=dict(os.environ, **{_setting: _cx_model})):
                started += 1
    nospec = [u["id"] for u in plan["units"] if u["state"] != "DONE" and not plan_store.release_refusal(u["state"]) and SCOPE.match(u["id"]) and not u.get("sub_units")]
    print("started %d | runners alive before this pass %d | in scope units with no sub units yet (need a spec lane): %s" % (started, len(alive), nospec))


if __name__ == "__main__":
    main()

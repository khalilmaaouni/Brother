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
import fcntl, glob, json, os, re, subprocess, sys, time
import importlib.util   # ACC3.b: the one loading route the safety screen and the proof freeze both admit
from typing import List, Optional, Set, Tuple   # ACC3.b: this block's typed contract
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's copy
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed, X3 finding 5)
import plan_lint  # noqa: E402  (FX-13.6: the plan lint, run on the one sub unit this pass is about to start)
import spec_check  # noqa: E402  (the landing gate's own done check verdict: spec_check.gate_refusal, 2026-09-29)
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
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


def still_parked(word, run_at, fact_at):
    """True when a sub unit whose last run ended with this status word must stay out of the pool. An unknown run time
    or an unknown fact time (0.0) PARKS: a retry is only granted on evidence that something changed after the run."""
    return word in PARKED and not (run_at > 0.0 and fact_at > run_at)


# CONFIG_WAIT parks like EXHAUSTED and WITHHELD: no attempt was spent. Its facts are fact_time's, and one more (attack
# R1 F2): a fresh proof that CLOSED a configuration breaker. A fault no program change can fix (a transient "No endpoints
# found", lost access, a registry id repaired) is proven gone by that close, whatever the program record says.
PARKED = ("EXHAUSTED", "WITHHELD", "CONFIG_WAIT")


def parked_fact(word, unit_id, spec_path=None):
    """The newest fact for a sub unit whose last run ended `word`: fact_time, and for CONFIG_WAIT also the newest
    configuration breaker close. An unreadable breaker adds no fact (the unit stays parked)."""
    fact = fact_time(unit_id, spec_path=spec_path)
    if word == "CONFIG_WAIT":
        try:
            import breaker
            fact = max(fact, breaker.last_config_close())
        except (ImportError, ValueError, OSError):
            pass   # sbe: allow-silent no readable close is no fact; the unit stays parked, the safe side
    return fact


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
GENERATED = ("bundle/", "SYSTEM.md", "scripts/check_all.sh", "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")
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


def depends_held(sub, unit, units):
    """The hold reason when `unit` names a depends_on that has not landed, else ''. Every predecessor must be a unit
    the plan holds and unit_landed; an unreadable list or an unknown name holds with its reason (never admits)."""
    deps = unit.get("depends_on") or []
    if not isinstance(deps, list) or any(not isinstance(d, str) or not d.strip() for d in deps):
        return "NO-DATA unreadable depends_on on unit %s" % unit.get("id")
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


def main():
    os.makedirs(RUNS, exist_ok=True)
    dry = "--dry" in sys.argv
    plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))

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

    last = {}; last_at = {}
    for d in sorted(glob.glob(os.path.join(RUNS, "*-*/")),
                   key=lambda p: (_dir_time(p), os.path.basename(p.rstrip("/")))):
        sub = re.sub(r"-\d{6}$", "", os.path.basename(d.rstrip("/")))
        last[sub] = status_word(os.path.join(d, "STATUS"))
        last_at[sub] = _dir_time(d)

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
    busy = {}; unknown_alive = []          # path -> sub unit patching it, over live runners then this pass's starts
    for _u, _s in sorted(alive):
        _ts = touch_set(_s, spec_of.get(_u))
        if _ts is None: unknown_alive.append(_s)
        else: busy.update({p: _s for p in _ts})
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
    room = max(0, min(BURN_CAP - len(alive_units), WIP - len(in_progress)))
    print("BUDGET cap %d runner(s) | WIP cap %d unit(s) | %d alive, %d READY to land | room for %d"
          % (BURN_CAP, WIP, len(alive_units), len(_ready - alive_units), room))
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
    for u in units:
        ev = u.get("evidence") or ""
        todo = [s for s in u["sub_units"] if not plan_store.sub_landed(s, ev)]
        if not todo:
            print("%-5s every sub unit landed: run the unit done-check and mark DONE" % u["id"]); continue
        sub = next((s for s in todo if not hold_reason(s)), None)
        waiting = [s for s in u["sub_units"] if waits_to_land(last.get(s, "")) and s in todo]
        why = ("runner alive" if u["id"] in alive_units else "READY build of %s waits to land" % waiting[0] if waiting
               else hold_reason(todo[0]) if sub is None else "last run of %s ended %s: needs a fact, then RUNNER_HINT" % (sub, last[sub]) if sub is not None and still_parked(last.get(sub), last_at.get(sub, 0.0), parked_fact(last.get(sub), u["id"], spec_path=u.get("spec"))) else "")
        if not why and sub is not None and sunk(REC.get(u["id"])) and not fact_time(u["id"], spec_path=u.get("spec")) > REC[u["id"]][2]:
            _c = max(REC[u["id"]][3].items(), key=lambda kv: kv[1])[0] if REC[u["id"]][3] else "SUITE"
            why = "SUNK %d grades, %d passes, mostly %s: %s; comes back when its spec or a judging tool changes" % (
                REC[u["id"]][0], REC[u["id"]][1], _c, ROUTE[_c])
        if not why and sub is not None and reseated_too_soon(last.get(sub, ""), last_at.get(sub, 0.0), time.time()):
            why = "skip: " + sub + " started under ten minutes ago and has not reached READY"
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
            print("%-5s %-8s skip: budget cap reached (%d new this pass)" % (u["id"], sub, room)); continue
        busy.update({p: sub for p in ts})
        if not dry:
            log = os.path.join(RUNS, sub + ".log")
            proc = subprocess.Popen([sys.executable, os.path.expanduser("~/.claude/bin/unit_runner.py"), u["id"], sub],   # sbe: allow-silent detached by design; its outcome is its STATUS file, read by the next pass
                                    stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True)
            refused = spawned(proc, sub)
            if refused:
                try:
                    with open(log, encoding="utf-8", errors="replace") as fh: said = (fh.read().strip().splitlines() or ["no output"])[-1]
                except OSError as exc:
                    said = "its log cannot be read (%s)" % exc
                print("%-5s %-8s REFUSED AT STARTUP: %s: %s" % (u["id"], sub, refused, said[:160])); continue
        print("%-5s %-8s START" % (u["id"], sub)); started += 1
    nospec = [u["id"] for u in plan["units"] if u["state"] != "DONE" and not plan_store.release_refusal(u["state"]) and SCOPE.match(u["id"]) and not u.get("sub_units")]
    print("started %d | runners alive before this pass %d | in scope units with no sub units yet (need a spec lane): %s" % (started, len(alive), nospec))


if __name__ == "__main__":
    main()

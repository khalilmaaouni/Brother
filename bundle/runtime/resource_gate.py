"""resource_gate: refuse admission when the MACHINE is short, not the code.

W2 of the readiness roadmap, "Resource admission control". THE FAILURE THIS
CLOSES, recorded on this machine: two concurrent batteries produced a false
TimeoutExpired where the same check standalone finished in 106 seconds. That
was never a code defect. It was two workers competing for one CPU, and the
record could not tell the difference, so somebody was sent to debug working
software.

THE RULE: before any dispatch, build or battery runs, read disk, cores, load
and free memory, and REFUSE ADMISSION when the machine cannot carry the work,
rather than letting it produce a timeout that looks like one. And when a run
IS admitted but still times out because the reading was already tight,
recording that as a code FAILURE would teach the estate something false; it is
recorded INVALID instead, because scarcity is not evidence about the code.

BORROWED, and inverted. A supervisor (Erlang) restarts a worker that crashed;
this module refuses to START one, because here the scarce resource is the
machine, not the worker. Andon (Toyota) is a human pulling a cord to stop the
line; here the machine's own scarcity pulls the cord, automatically, and the
resulting stop is recorded INVALID rather than as a defect on the line.

THE BANDS, this estate's own, measured 2026-08-29 at 8.9 GiB free of 228 (96
percent used): under 15 GiB free is the cleanup-before-builds band (admitted,
but flagged), under 8 GiB is the refuse floor (deferred). CPU admission is
oversubscription, not a fixed threshold: the 1-minute load average against
cores minus a reserve, because a load figure means nothing on its own without
knowing how many cores are actually available to soak it up.

MEMORY IS NO-DATA ON THIS MAC, BY DESIGN, NOT BY OMISSION. Verified from a
live stdlib call on this machine: `os.sysconf("SC_AVPHYS_PAGES")` raises
"unrecognized configuration name" here, and "SC_AVPHYS_PAGES" is absent from
`os.sysconf_names`. `os.sysconf("SC_PHYS_PAGES")` DOES work here, but that is
TOTAL installed memory, never what is FREE, so it cannot stand in. No other
stdlib call reads available memory on macOS (no ctypes, no subprocess, no
psutil: this module is stdlib only). So `mem_free_gib` reports NO-DATA here,
honestly, rather than a guess; on a platform where SC_AVPHYS_PAGES exists
(most Linux), the same code path reads it for real. Guessing "healthy" for an
unreadable field is exactly how a scarce machine gets dispatched into, which
is the one thing this module exists to prevent.

Python 3, standard library only.

origin: a human, or a wrapping shell script, running this script's own CLI
directly with `--run CMD` to gate and record a real command's outcome (see
main(), the `if args.run is not None:` branch, below), per this module's own
docstring above: "before any dispatch, build or battery runs". Nothing else
in this repo imports resource_gate and calls run_locked() (verified: grep
-rl resource_gate scripts bundle/runtime finds only this module's own test,
test_resource_gate.py, plus acceptance_9.py, test_graph_loop.py and
test_crash_resume.py, each of which only names test_resource_gate.py in a
comment and never imports this module itself).

PRODUCER: this module is the sole producer of its own last-run record. The
write happens at `with open(path, "w", encoding="utf-8") as fh:
json.dump(result, fh)` inside _write_state(), called from run_locked() after
every admitted or deferred run.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GIB = 1024.0 ** 3

#: This estate's own bands, from the roadmap node's own measurement.
DISK_CLEANUP_GIB = 15.0
DISK_REFUSE_GIB = 8.0

#: Cores held back for the supervisor and the OS itself, never offered to a
#: dispatch's own admission arithmetic.
CORE_RESERVE = 1

NODATA = "NO-DATA"
ADMIT, DEFER = "ADMIT", "DEFER"
PASS, FAIL, INVALID = "PASS", "FAIL", "INVALID"

#: One battery at a time, cross-process, same mechanism as integrate.py's
#: _Lock and the same reasoning: two batteries racing recreate exactly the
#: contention that produced the false TimeoutExpired this module exists for.
LOCK_NAME = ".battery.lock"
LOCK_TIMEOUT = 300.0

#: Where the last --run's outcome is recorded, for --classify-last. Not a
#: log: one row, overwritten every run, because only the most recent battery
#: is ever in question.
STATE_PATH = os.path.join(ROOT, ".resource_gate_last.json")

_FIELD_ALIAS = {"disk": "disk_free_gib", "cores": "cores_available",
                "load": "load1", "mem": "mem_free_gib"}


# ---------------------------------------------------------------- readings --

def _read_disk_free_gib(path):
    try:
        return shutil.disk_usage(path).free / GIB, None
    except OSError as exc:
        return None, "shutil.disk_usage(%r) failed: %s" % (path, exc)


def _read_cores_available(reserve):
    n = os.cpu_count()
    if n is None:
        return None, "os.cpu_count() returned None: the platform would not say"
    return n - reserve, None


def _read_load1():
    try:
        return os.getloadavg()[0], None
    except (OSError, AttributeError) as exc:
        return None, "os.getloadavg() failed: %s" % exc


def _read_mem_free_gib():
    names = getattr(os, "sysconf_names", {})
    if "SC_AVPHYS_PAGES" not in names or "SC_PAGE_SIZE" not in names:
        return None, (
            "no stdlib mechanism reads available memory on this platform: "
            "SC_AVPHYS_PAGES is absent from os.sysconf_names (verified on "
            "this Mac: os.sysconf raises 'unrecognized configuration name' "
            "for it). os.sysconf can read TOTAL memory here, never FREE "
            "memory, so this reading is NO-DATA by design rather than a "
            "guess of healthy")
    try:
        avail = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        return avail / GIB, None
    except (ValueError, OSError) as exc:
        return None, "os.sysconf(SC_AVPHYS_PAGES) failed: %s" % exc


def read(disk_path="/", cpu_reserve=CORE_RESERVE, simulate_unreadable=()):
    """One dict, one call: disk_free_gib, cores_available, load1, mem_free_gib.

    Every field is a NUMBER or None; a None field always carries its reason
    in the returned "errors" dict, naming WHY, never leaving it to be guessed
    healthy. simulate_unreadable takes field names ("disk", "cores", "load",
    "mem") and forces that field down the same None-plus-reason path a real
    read failure takes, WITHOUT touching the real machine: it proves the
    plumbing reports NO-DATA rather than a special-cased pass-through, it
    never fakes what the machine's true numbers are.
    """
    wanted = {_FIELD_ALIAS.get(f, f) for f in (simulate_unreadable or ())}
    out, errors = {}, {}

    def field(name, fn):
        if name in wanted:
            out[name] = None
            errors[name] = ("simulated: forced unreadable for this call, to "
                             "prove the field reports NO-DATA rather than "
                             "assuming healthy")
            return
        value, err = fn()
        out[name] = value
        if err:
            errors[name] = err

    field("disk_free_gib", lambda: _read_disk_free_gib(disk_path))
    field("cores_available", lambda: _read_cores_available(cpu_reserve))
    field("load1", _read_load1)
    field("mem_free_gib", _read_mem_free_gib)
    out["errors"] = errors
    return out


# --------------------------------------------------------------- admission --

def admit(cost, reading=None, disk_path="/"):
    """(dict) with "verdict" ADMIT or DEFER, the numbers, and why.

    `cost` is the label of what is asking for admission ("battery",
    "dispatch:W7", ...); it is recorded, not weighed numerically, because the
    thing actually scarce here is the machine, not a per-task budget.
    `reading` lets a caller (a test, a scheduler that already has a fresh
    reading) pass one in; the real machine is read only when none is given,
    which is what makes every band here injectable without touching the
    machine this test suite runs on.

    A None field is NEVER treated as healthy: it defers, on the same
    reasoning the readings above already state. That is the one place a
    permissive bug would hide, so it is not left implicit.
    """
    r = reading if reading is not None else read(disk_path=disk_path)
    errors = r.get("errors", {})
    numbers = {k: r.get(k) for k in
               ("disk_free_gib", "cores_available", "load1", "mem_free_gib")}
    reasons = []
    verdict = ADMIT

    disk = r.get("disk_free_gib")
    if disk is None:
        verdict = DEFER
        reasons.append(
            "disk_free_gib is %s (%s): assuming healthy is how a scarce "
            "machine gets dispatched into" % (NODATA, errors.get(
                "disk_free_gib", "unknown")))
    elif disk < DISK_REFUSE_GIB:
        verdict = DEFER
        reasons.append("disk_free_gib %.2f is under the refuse floor of "
                        "%.0f GiB" % (disk, DISK_REFUSE_GIB))
    elif disk < DISK_CLEANUP_GIB:
        reasons.append("disk_free_gib %.2f is under the cleanup band of "
                        "%.0f GiB; admitted, but cleanup is due before the "
                        "next build" % (disk, DISK_CLEANUP_GIB))

    cores, load1 = r.get("cores_available"), r.get("load1")
    if cores is None or load1 is None:
        verdict = DEFER
        missing = "cores_available" if cores is None else "load1"
        reasons.append("%s is %s: assuming healthy is how a scarce machine "
                        "gets dispatched into" % (missing, NODATA))
    elif load1 > cores:
        verdict = DEFER
        reasons.append("load1 %.2f exceeds cores_available %d: the machine "
                        "is already oversubscribed" % (load1, cores))

    return {"verdict": verdict, "cost": cost, "numbers": numbers,
            "reasons": reasons}


# -------------------------------------------------------- INVALID vs FAIL --

def classify(outcome, reading=None, cost="battery"):
    """(verdict, reason). verdict is one of PASS, FAIL, INVALID, NO-DATA.

    Only a "timeout" is ambiguous about whether the code or the machine
    caused it, so only a timeout consults the resource reading. "passed" and
    "failed" are never reinterpreted by scarcity: a real non-zero exit is
    evidence about the code whatever the machine was doing. A timeout that
    happened while the reading would have deferred admission is recorded
    INVALID, never FAIL, because scarcity is not evidence about the code; a
    timeout against a clean reading is a real hang and stays FAIL.
    """
    if outcome == "passed":
        return PASS, "the run passed"
    if outcome == "failed":
        return FAIL, ("the run failed with a non-zero exit; that is evidence "
                       "about the code, whatever the machine reading was")
    if outcome == "timeout":
        a = admit(cost, reading=reading)
        if a["verdict"] == DEFER:
            return INVALID, (
                "the run timed out while the machine reading would have "
                "deferred admission (%s); scarcity is not evidence about "
                "the code" % "; ".join(a["reasons"]))
        return FAIL, ("the run timed out on a machine reading that would "
                       "have cleanly admitted it, which is a real hang and "
                       "stays FAIL")
    return NODATA, "unknown outcome %r; nothing is known about how to classify it" % outcome


# ------------------------------------------------------------------- lock --

class _Lock(object):
    """O_EXCL across processes. Mirrors integrate.py's _Lock exactly: same
    mechanism, same reasoning, because two independent lock implementations
    for the same class of contention is how one of them ends up untested."""

    def __init__(self, repo=None, timeout=LOCK_TIMEOUT):
        # repo defaults to None, resolved to the module's ROOT HERE, at call
        # time, not baked into the signature at import time: a default of
        # `repo=ROOT` would capture ROOT's value when this class was defined,
        # so a test monkeypatching resource_gate.ROOT afterward would be
        # silently ignored. Same reasoning below for STATE_PATH.
        self.path = os.path.join(ROOT if repo is None else repo, ".git", LOCK_NAME)
        self.timeout, self.fd = timeout, None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        "the battery lock at %s has been held for over %.0fs. "
                        "Batteries serialize estate-wide: waiting is correct "
                        "and proceeding is not" % (self.path, self.timeout))
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.unlink(self.path)
            except OSError as exc:
                # logged, not raised: raising in __exit__ would mask the
                # body's own exception, and a stale lock file self-heals on
                # the next acquire timeout
                print("resource_gate: could not remove lock %s: %s"
                      % (self.path, exc), file=sys.stderr)
        return False


# ------------------------------------------------------ run + last state --

def _write_state(result, path=None):
    path = STATE_PATH if path is None else path
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
    except OSError as exc:
        print("resource_gate: could not persist last state to %s: %s"
              % (path, exc), file=sys.stderr)


def _read_state(path=None):
    path = STATE_PATH if path is None else path
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def run_locked(cmd, cost="battery", timeout=None, reading=None, repo=None,
               state_path=None):
    """Admission-gated, lock-serialized run of a real command.

    Deferred at admission: the command never runs, and that is recorded so
    --classify-last reports INVALID rather than treating a skipped run as a
    pass. Admitted: runs serialized under the battery lock; a
    subprocess.TimeoutExpired is recorded as outcome "timeout", never as a
    crash, so classify() gets to decide INVALID vs FAIL from the reading
    taken at admission time.
    """
    a = admit(cost, reading=reading)
    if a["verdict"] == DEFER:
        result = {"outcome": "deferred", "cost": cost,
                   "reading": a["numbers"], "reasons": a["reasons"]}
        _write_state(result, state_path)
        return result

    reading_at_run = reading if reading is not None else read()
    try:
        with _Lock(repo):
            proc = subprocess.run(cmd, timeout=timeout)
        outcome = "passed" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        outcome = "timeout"

    result = {"outcome": outcome, "cost": cost, "reading": reading_at_run}
    _write_state(result, state_path)
    return result


def classify_last(cost="battery", state_path=None):
    state = _read_state(state_path)
    if state is None:
        return NODATA, "no run has been recorded at %s yet" % state_path
    if state.get("outcome") == "deferred":
        return INVALID, ("the last dispatch was deferred at admission and "
                          "never ran; that is not evidence about the code")
    return classify(state.get("outcome"), reading=state.get("reading"),
                     cost=state.get("cost", cost))


# --------------------------------------------------------------------- CLI --

def _print_reading(r):
    for k in ("disk_free_gib", "cores_available", "load1", "mem_free_gib"):
        v = r.get(k)
        if v is None:
            print("%-18s %s (%s)" % (k, NODATA, r.get("errors", {}).get(k, "unknown")))
        elif isinstance(v, float):
            print("%-18s %.2f" % (k, v))
        else:
            print("%-18s %s" % (k, v))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--read", action="store_true",
                     help="print one reading: disk, cores, load, memory")
    ap.add_argument("--simulate-unreadable", choices=sorted(_FIELD_ALIAS),
                     default=None,
                     help="force one field to NO-DATA without touching the "
                          "real machine, proving the plumbing never guesses "
                          "healthy")
    ap.add_argument("--admit", metavar="COST", default=None,
                     help="run the admission decision for this dispatch or "
                          "battery label")
    ap.add_argument("--lock", metavar="COST", default=None,
                     help="hold the battery lock briefly so a second caller "
                          "can be seen waiting on it")
    ap.add_argument("--run", nargs=argparse.REMAINDER, metavar="CMD",
                     default=None,
                     help="admission-gated, lock-serialized run of CMD, "
                          "recorded for --classify-last")
    ap.add_argument("--classify-last", action="store_true",
                     help="classify the last recorded --run as PASS, FAIL "
                          "or INVALID")
    ap.add_argument("--kind", default="battery",
                     help="the cost label used by --run and --classify-last")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.read:
        sim = (args.simulate_unreadable,) if args.simulate_unreadable else ()
        _print_reading(read(simulate_unreadable=sim))
        return 0

    if args.admit is not None:
        a = admit(args.admit)
        for reason in a["reasons"]:
            print("  " + reason)
        print("%s: %s" % (a["verdict"], a["cost"]))
        # DEFER is a NO-DATA-shaped stop, not a code failure: exit 2, the
        # same convention check_all.sh already gives NO-DATA elsewhere.
        return 0 if a["verdict"] == ADMIT else 2

    if args.lock is not None:
        with _Lock():
            print("lock held: pid %d, kind %s" % (os.getpid(), args.lock))
            time.sleep(2.0)
        print("lock released")
        return 0

    if args.run is not None:
        if not args.run:
            print("%s: --run needs a command after it" % NODATA, file=sys.stderr)
            return 2
        result = run_locked(args.run, cost=args.kind)
        print(json.dumps(result))
        return {"passed": 0, "failed": 1}.get(result["outcome"], 2)

    if args.classify_last:
        verdict, reason = classify_last(cost=args.kind)
        print("%s: %s" % (verdict, reason))
        return {PASS: 0, FAIL: 1}.get(verdict, 2)

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Measure what BrotherMode's hooks cost, and generate docs/PERFORMANCE.md.

WHY THIS EXISTS
  An external review asked a question nobody in this project could answer: a
  normal shell call fires two hook programs before it and one after, and a Stop
  event chains four Python programs inside one shared timeout, and not one of
  those numbers had ever been measured. docs/HOOKS.md carried a single figure,
  "roughly 45 ms", with nothing behind it. So the operational cost of installing
  this product was unknown to the person installing it, which is exactly the
  class of unbacked claim the rest of this repository refuses.

  This tool answers what it can answer honestly and refuses the rest by name.
  docs/PERFORMANCE.md is GENERATED from a run of this file. Every number on that
  page comes out of a measurement here, and the page carries the machine record
  it was taken on, because a latency number without a machine is a rumour.

WHAT IT MEASURES
  1. WHAT ACTUALLY RUNS, read from hooks/hooks.json rather than typed: which
     programs fire at which event, in which order, with which arguments, under
     which shared timeout. If the manifest changes, this page changes with it.
  2. THE COST OF EACH PROGRAM, separately from THE COST OF THE CHAIN. Those are
     two different numbers and the Stop event is why: four programs share one
     30 second budget there, so a per program figure alone would understate what
     the founder waits for.
  3. HOW OFTEN A PROGRAM FAILS OR FAILS OPEN under the benchmark's own
     conditions, counted from real exit codes and real stderr, never assumed.

WHAT IT REFUSES TO MEASURE, AND WHY THAT MATTERS MORE THAN ANOTHER NUMBER
  See NOT_MEASURED below. Every entry is a question the review asked that this
  project has no honest way to answer today: the share of real sessions hitting
  a warning or a false refusal needs field data nobody collects, and lock
  contention frequency in real use needs concurrent real sessions, not a
  laboratory. A number invented for those would be worse than the silence the
  review complained about, so they are printed as NOT MEASURED with the reason.

WHY IT DOES NOT SPAWN PROCESSES, AND WHAT THAT COSTS THE ANSWER
  tools/test_bm.py forbids `import subprocess` in every shipping module under
  tools/, and its allowlist names exactly two files, bm_autosave.py and
  bm_controller.py. This file is not one of them and does not add itself to a
  list it does not own. So it runs each hook program IN PROCESS, through
  runpy.run_path with run_name "__main__", after purging sys.modules of
  everything the previous run imported, so every repetition pays a cold import
  the way a fresh hook process does. What that captures: compiling the program,
  cold importing its dependencies (tools/bm_store.py is 17 thousand lines and is
  the bulk of it), reading the payload off stdin, and the whole decision.

  What it does NOT capture, stated here and on the page rather than buried: the
  fork, the exec, and the CPython interpreter bootstrap that a real hook pays
  once per process, before any of this code runs. That residue is a constant per
  process, this file cannot measure it without the subprocess call it is not
  allowed to make, and it is therefore reported as NOT MEASURED with the command
  a reader can run to get it on their own machine. Every per action total on the
  page is a measured LOWER BOUND for that reason, and says so.

THE THROWAWAY WORLD
  Every measurement runs against a store created for the run in a temporary
  directory, with HOME, the setup config path, the vault path and the project
  root all pinned inside it, and the process working directory moved there. The
  founder's own store, vault and home are never read or written. The only file
  this tool writes outside that temporary directory is the page itself.

Python 3.9, standard library only. No network. No subprocess.
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/bm_hookbench.py                  # measure, rewrite the page
  python3 tools/bm_hookbench.py --reps 25        # more repetitions
  python3 tools/bm_hookbench.py --json           # the record on stdout, no write
  python3 tools/bm_hookbench.py --out PATH       # write somewhere else
  python3 tools/bm_hookbench.py --compare P.json # re-measure, check the band
"""

import sys

#: The module table of a bare interpreter, captured BEFORE this file imports
#: anything of its own. It is the yardstick the runner purges back to, so that
#: a hook program measured here pays for importing json, re, sqlite3 and
#: tools/bm_store.py exactly as a fresh process would, instead of inheriting
#: them warm from the process doing the measuring. Getting this wrong is not a
#: rounding error: with the naive purge this file started with, the fence hook
#: measured 8.8 ms, and with this one it measures about three times that.
_PRISTINE_MODULES = set(sys.modules)

import importlib.util
import io
import json
import os
import platform
import re
import runpy
import shutil
import statistics
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")
PAGE = os.path.join(ROOT, "docs", "PERFORMANCE.md")

#: Repetitions per program, after the warmup. Fifteen is enough for a median
#: and a visible spread without turning the suite into a coffee break; it is a
#: flag because the right number depends on how noisy the machine is.
DEFAULT_REPS = 15

#: Discarded runs before counting starts. ONE, and the reason is fidelity
#: rather than tidiness: the first run of a program compiles its imports and
#: writes the bytecode cache, which a real installed hook only pays once ever.
#: Counting it would report a first install cost as if it were the per call
#: cost. The page says the warmup exists.
WARMUP_REPS = 1

#: The band this page promises for a re-run. Timing on a machine that is also
#: doing other work is not repeatable to better than a factor like this, and a
#: tighter number printed here would be a promise the tool cannot keep. --compare
#: enforces exactly this and nothing stricter.
REPRODUCIBILITY_FACTOR = 2.0

#: A stderr line from bm_fence_hook.py that means the fence allowed the write
#: WITHOUT checking ownership. Counted because a benchmark that silently ran
#: against a broken fence would report the cost of doing nothing.
FAIL_OPEN_MARK = "FAILING OPEN"

#: Every question the review asked that this tool will not answer, with the
#: reason. Carried into the record so the page is a pure function of the record.
NOT_MEASURED = (
    ("Bare interpreter startup per hook process",
     "Every hook runs as its own python3 process, so each one pays a fork, an "
     "exec and a CPython bootstrap (the interpreter binary, site, encodings) "
     "before a line of BrotherMode code runs. Measuring that means timing a "
     "real process, and tools/test_bm.py bans import subprocess in every "
     "shipping module under tools/ with an allowlist this file is not on and "
     "did not add itself to. It is therefore left out rather than guessed, "
     "which is why every total on this page is a measured LOWER BOUND. To get "
     "the missing piece on your own machine, time an empty interpreter and add "
     "it once per program in the chain."),
    ("The SessionStart hook",
     "It is a Python program now rather than a shell script (the 2026-08-17 "
     "Windows port), and it is still not on this page, for a reason that "
     "changed with it. It DRIVES its sibling tools as real processes, one per "
     "check, exactly as the shell version did. This tool executes hook "
     "programs in process and spawns nothing, which is the constraint that "
     "shaped its whole design, so running the session-start program here "
     "would quietly break the promise every other number on this page "
     "depends on. Its cost is measured instead by "
     "tools/test_bm_hookperf.py, which is a test file and may spawn real "
     "processes."),
    ("Store lock contention frequency in real use",
     "This benchmark is one process making one call at a time, so the "
     "contention it observes is zero by construction and that zero says "
     "nothing about a machine running several sessions at once. A real figure "
     "needs concurrent real sessions and a counter that survives them, which "
     "this project does not have."),
    ("Hook failure frequency in the field",
     "The failure counts on this page are this benchmark's own, on one "
     "machine, against a store built for the run. They are evidence that the "
     "programs work under these conditions, not a rate observed across real "
     "installs. Nothing in BrotherMode reports hook outcomes home, by design, "
     "so there is no field denominator to divide by."),
    ("The share of sessions hitting a warning or a false refusal",
     "This needs field data: how many real sessions ran, and how many of them "
     "saw a warning or were refused a write they should have been allowed. "
     "BrotherMode collects no such telemetry and this tool cannot invent a "
     "denominator. It is the single most important number the review asked "
     "for, and it stays NOT MEASURED until somebody instruments real "
     "installs."),
)

#: The user actions the page reports, each named with the hook events it
#: triggers and the tool name that decides which matcher groups fire. The event
#: list is deliberately short and explicit: which PROGRAMS run under each entry
#: is read from hooks/hooks.json, never written here.
ACTIONS = (
    ("One Edit, Write, MultiEdit or NotebookEdit tool call",
     (("PreToolUse", "Edit"), ("PostToolUse", "Edit"))),
    ("One Bash tool call",
     (("PreToolUse", "Bash"), ("PostToolUse", "Bash"))),
    ("One Stop event, at the end of every assistant turn",
     (("Stop", None),)),
    ("One PreCompact event, when the context is condensed",
     (("PreCompact", None),)),
    ("One SessionEnd event",
     (("SessionEnd", None),)),
)


class BenchError(Exception):
    """A measurement could not be taken honestly. Never worked around."""


# ---------------------------------------------------------------------------
# What actually runs, read from the manifest rather than typed.
# ---------------------------------------------------------------------------

#: A python program invoked from a hook command. The argument tail stops at a
#: shell separator so `... bm_lead.py watchdog --tick; printf ...` yields the
#: two arguments that belong to it and not the next command's.
_PY_PROGRAM_RE = re.compile(
    r"python3?\s+\"?\$\{CLAUDE_PLUGIN_ROOT\}/tools/([A-Za-z0-9_]+\.py)\"?"
    r"([^;'\"|&]*)")
#: A shell program invoked directly. `sh -c '...'` does not match, because the
#: path has to follow the interpreter word.
_SH_PROGRAM_RE = re.compile(
    r"sh\s+\"?\$\{CLAUDE_PLUGIN_ROOT\}/tools/([A-Za-z0-9_]+\.sh)\"?")
#: The chain driver, and the table it runs. Imported by path rather than
#: retyped (2026-08-17, the Windows port): hooks/hooks.json used to spell the
#: Stop and PreCompact programs out inside an `sh -c` string, and this parser
#: read them out of that string. They live in ONE place now, and a parser that
#: stopped at the driver would report a one-program chain where four run,
#: which is precisely the understatement this file's own docstring calls the
#: defect it exists to fix. The import costs nothing at measurement time: the
#: module is read once, holds a literal table, and spawns nothing on import.
_HOOKCHAIN = "bm_hookchain.py"
_hcspec = importlib.util.spec_from_file_location(
    "bm_hookchain_for_bench", os.path.join(HERE, _HOOKCHAIN))
_hookchain = importlib.util.module_from_spec(_hcspec)
_hcspec.loader.exec_module(_hookchain)
CHAINS = _hookchain.CHAINS

#: Every tools/ program named anywhere in a command, used only to prove the two
#: patterns above did not miss one. A parser that silently reports three
#: programs where four run would understate the cost of the chain, which is the
#: exact defect this file exists to fix.
_ANY_PROGRAM_RE = re.compile(r"tools/([A-Za-z0-9_]+\.(?:py|sh))")


def parse_command(command):
    """Every program one hook command runs, in order, as
    [{"program": name, "args": [...], "runnable": bool, "reason": str}].

    Raises BenchError when the command names a tools/ program neither pattern
    captured, rather than quietly reporting a shorter chain."""
    found = []
    for match in _PY_PROGRAM_RE.finditer(command):
        name = match.group(1)
        args = match.group(2).split()
        if name == _HOOKCHAIN:
            chain = args[0] if args else ""
            rows = CHAINS.get(chain)
            if rows is None:
                raise BenchError(
                    "hooks/hooks.json runs the chain driver with chain name "
                    "%r, which tools/bm_hookchain.py does not define. That "
                    "chain dies at every firing; fix the wiring rather than "
                    "publishing a page for a chain that runs nothing."
                    % chain)
            for offset, (module, module_args) in enumerate(rows):
                found.append((match.start() + offset, {
                    "program": module,
                    "args": list(module_args),
                    "runnable": True,
                    "reason": "",
                }))
            continue
        if name == "bm_sessionstart.py":
            # RUNNABLE IN THE RUNTIME, NOT MEASURABLE HERE, and the reason
            # changed with the port rather than disappearing. It used to be
            # unmeasurable because it was a shell script and this tool
            # executes Python in process. It is Python now, and still
            # unmeasurable here, because it DRIVES its sibling tools as real
            # processes: running it in process would spawn them, and this
            # file's whole account of itself (and
            # tools/test_bm_hookbench.py's TestTheSubprocessBanIsRespected)
            # rests on it spawning nothing.
            found.append((match.start(), {
                "program": name,
                "args": args,
                "runnable": False,
                "reason": ("it drives its sibling tools as real processes, "
                           "and this tool spawns none, so its cost is not on "
                           "this page"),
            }))
            continue
        found.append((match.start(), {
            "program": name,
            "args": args,
            "runnable": True,
            "reason": "",
        }))
    for match in _SH_PROGRAM_RE.finditer(command):
        found.append((match.start(), {
            "program": match.group(1),
            "args": [],
            "runnable": False,
            "reason": "a shell script cannot be run without spawning a process",
        }))
    found.sort(key=lambda pair: pair[0])
    programs = [entry for _pos, entry in found]
    named = set(_ANY_PROGRAM_RE.findall(command))
    captured = set(entry["program"] for entry in programs)
    # The driver itself is named by the command and deliberately NOT in the
    # captured set: it was replaced by the programs it runs, which is the
    # expansion above. Everything else the command names must be captured.
    missed = sorted(named - captured - {_HOOKCHAIN})
    if missed:
        raise BenchError(
            "hooks/hooks.json runs %s in a shape this parser does not read: "
            "%s. Teach parse_command the new shape rather than publishing a "
            "chain that is shorter than the one the runtime executes."
            % (", ".join(missed), command))
    return programs


def read_hook_chains(path=HOOKS_JSON):
    """The manifest as a list of event groups, in file order:
    [{"event", "matcher", "timeout", "programs": [...]}, ...]."""
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError) as exc:
        raise BenchError("cannot read %s: %s" % (path, exc))
    except ValueError as exc:
        raise BenchError("%s is not valid JSON: %s" % (path, exc))
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise BenchError("%s carries no hooks object" % path)
    groups = []
    for event, entries in hooks.items():
        for entry in entries or ():
            for hook in entry.get("hooks", ()):
                command = hook.get("command", "")
                groups.append({
                    "event": event,
                    "matcher": entry.get("matcher", ""),
                    "timeout_seconds": hook.get("timeout"),
                    "programs": parse_command(command),
                })
    if not groups:
        raise BenchError("%s wires no hook commands at all" % path)
    return groups


def groups_for(chains, event, tool_name):
    """The groups the runtime would fire for one event and one tool name.

    The matcher is treated as a REGEX searched against the tool name, the way
    docs/HOOKS.md states Claude Code treats it, and not as a substring test:
    Edit is a substring of both MultiEdit and NotebookEdit, and this project
    has already been bitten once by the difference."""
    out = []
    for group in chains:
        if group["event"] != event:
            continue
        matcher = group["matcher"]
        if matcher and tool_name is not None:
            try:
                if not re.search(matcher, tool_name):
                    continue
            except re.error as exc:
                raise BenchError("matcher %r is not a valid regex: %s"
                                 % (matcher, exc))
        elif matcher and tool_name is None:
            continue
        out.append(group)
    return out


# ---------------------------------------------------------------------------
# The payloads. Shapes follow docs/HOOKS.md, which documents the PreToolUse
# object field by field; the other events reuse the same base object and add
# the fields the programs in this repository actually read off it.
# ---------------------------------------------------------------------------

def payload_for(event, tool_name, session_id, project, transcript, target):
    payload = {
        "session_id": session_id,
        "transcript_path": transcript,
        "cwd": project,
        "permission_mode": "default",
        "hook_event_name": event,
    }
    if event in ("PreToolUse", "PostToolUse"):
        payload["tool_name"] = tool_name
        payload["tool_use_id"] = "toolu_01BENCH0000000000000000"
        if tool_name == "Bash":
            payload["tool_input"] = {"command": "python3 -c 'print(1)'"}
        else:
            payload["tool_input"] = {
                "file_path": target,
                "old_string": "value = 1\n",
                "new_string": "value = 2\n",
            }
        if event == "PostToolUse":
            payload["tool_response"] = {"stdout": "1\n", "stderr": "",
                                        "interrupted": False}
    elif event == "Stop":
        payload["stop_hook_active"] = False
    elif event == "SessionEnd":
        payload["reason"] = "clear"
    elif event == "PreCompact":
        payload["trigger"] = "auto"
        payload["custom_instructions"] = ""
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# The runner. One hook program, executed the way the runtime executes it minus
# the process itself, timed with a monotonic clock.
# ---------------------------------------------------------------------------

_RUNNER_KEEP = None

#: Modules taken out of sys.modules that must NOT be deallocated. An extension
#: module (a .so, or a builtin with no source file at all) cannot survive being
#: torn down while a still live pure Python module points into it: CPython
#: initialises most of them once per process and hands the same state back on
#: re-import. Dropping the last reference to `_json` or `_decimal` while `json`
#: and `decimal` are still alive segfaulted the interpreter at shutdown, AFTER
#: every test had passed and unittest had printed OK, which is the worst shape
#: a failure can take. Holding them here costs a handful of small objects and
#: changes no measurement: a re-import of an extension module reuses the
#: cached state either way, so it was never a cold import to begin with.
#: Pure Python modules are NOT held: they deallocate cleanly, and they are the
#: ones a hook actually pays to import, so they must really go.
_GRAVEYARD = []


def _purge_to(keep):
    for name in list(sys.modules):
        if name in keep:
            continue
        module = sys.modules.pop(name)
        source = getattr(module, "__file__", None)
        if source is None or not source.endswith(".py"):
            _GRAVEYARD.append(module)


def runner_baseline():
    """The module table the runner itself needs, and nothing more.

    Measured rather than listed: purge back to a bare interpreter, run an empty
    program through the SAME runpy path the benchmark uses, and keep whatever
    that needed. Everything outside this set is charged to the hook program
    that imports it, which is the whole point. A hand written list would go
    stale the first time runpy changed what it loads lazily."""
    global _RUNNER_KEEP
    if _RUNNER_KEEP is not None:
        return _RUNNER_KEEP
    _purge_to(_PRISTINE_MODULES)
    handle, empty = tempfile.mkstemp(suffix=".py", prefix="bm-hookbench-cal-")
    os.close(handle)
    saved = (sys.argv, sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.argv = [empty]
        sys.stdin = io.StringIO("")
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            runpy.run_path(empty, run_name="__main__")
        except BaseException:  # sbe: allow-silent this is a calibration probe measuring the module table, not the program's own outcome
            pass
    finally:
        (sys.argv, sys.stdin, sys.stdout, sys.stderr) = saved
        os.unlink(empty)
    _RUNNER_KEEP = set(sys.modules)
    return _RUNNER_KEEP


def run_program(path, args, stdin_text):
    """Run one program in process and return a result dictionary.

    sys.modules is purged back to runner_baseline() afterwards, so the next
    repetition imports everything cold, the way a fresh process does. sys.path
    gains the program's own directory for the duration, because a real
    `python3 tools/x.py` puts it at sys.path[0] and a program that relies on
    that must not fail here for a reason the runtime would not produce."""
    baseline_modules = runner_baseline()
    saved = (sys.argv, sys.stdin, sys.stdout, sys.stderr, sys.path[:])
    out, err = io.StringIO(), io.StringIO()
    sys.argv = [path] + list(args)
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout, sys.stderr = out, err
    sys.path.insert(0, os.path.dirname(path))
    exit_code, crash = 0, ""
    started = time.perf_counter()
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            exit_code = 1
    except KeyboardInterrupt:
        # The one exception that is the operator talking, not the hook
        # failing. Recording it as a crash would put "the founder pressed
        # control C" on the page as a hook defect.
        raise
    except BaseException as exc:  # a crashed hook is a RESULT, not a stop
        exit_code = None
        crash = "%s: %s" % (type(exc).__name__, exc)
    finally:
        elapsed = time.perf_counter() - started
        (sys.argv, sys.stdin, sys.stdout, sys.stderr, path_saved) = (
            saved[0], saved[1], saved[2], saved[3], saved[4])
        sys.path[:] = path_saved
        _purge_to(baseline_modules)
    return {
        "seconds": elapsed,
        "exit_code": exit_code,
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "crash": crash,
    }


# ---------------------------------------------------------------------------
# Statistics. Median plus a spread, never a single sample.
# ---------------------------------------------------------------------------

def percentile(values, fraction):
    """Nearest rank percentile. Written out rather than taken from
    statistics.quantiles so the definition on the page is the definition in
    the code, at every supported Python version."""
    if not values:
        raise BenchError("no samples to take a percentile of")
    ordered = sorted(values)
    rank = int(round(fraction * (len(ordered) - 1)))
    return ordered[rank]


def summarize(values):
    """A sample set as the page reports it. Milliseconds throughout."""
    if not values:
        raise BenchError("no samples to summarize")
    ms = [v * 1000.0 for v in values]
    median = statistics.median(ms)
    return {
        "samples": len(ms),
        "median_ms": round(median, 2),
        "min_ms": round(min(ms), 2),
        "p90_ms": round(percentile(ms, 0.9), 2),
        "max_ms": round(max(ms), 2),
        "spread_factor": round(max(ms) / median, 2) if median else 0.0,
    }


# ---------------------------------------------------------------------------
# The throwaway world.
# ---------------------------------------------------------------------------

#: Environment variables that must not leak in from the measuring shell. A
#: stray export here would silently change what the fence does and therefore
#: what the page reports.
SCRUBBED_ENV = (
    "BROTHERMODE_ROOT", "BROTHERMODE_VAULT", "BROTHERMODE_FTS5",
    "BROTHERMODE_NO_FTS5", "BROTHERMODE_SKIP_GIT_CONTAINMENT",
    "BM_FENCE_STRICT", "BM_FENCE_MODE", "BM_FENCE_SESSION_ID",
    "BM_APPROVAL_RECEIPT", "BROTHERME_CONFIG", "CLAUDE_PLUGIN_ROOT",
)


class Sandbox(object):
    """A whole BrotherMode installation in a temporary directory: a home, a
    consented setup config, a vault, a project, a store, and one active claim
    owned by the benchmark session so the fence has real work to do rather
    than taking its cheap no active claims path."""

    session_id = "bm-hookbench-session"

    def __init__(self):
        self.base = None
        self._saved_env = {}
        self._saved_cwd = None
        self._saved_pycache = None

    def __enter__(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="bm-hookbench-"))
        self.home = os.path.join(self.base, "home")
        self.vault = os.path.join(self.base, "vault")
        self.project = os.path.join(self.base, "project")
        self.config = os.path.join(self.home, ".brotherme", "config.json")
        self.transcript = os.path.join(self.base, "transcript.jsonl")
        self.target = os.path.join(self.project, "src", "thing.py")
        for path in (self.home, self.vault, os.path.dirname(self.target)):
            os.makedirs(path)
        with io.open(self.target, "w", encoding="utf-8") as handle:
            handle.write("value = 1\n")
        # Large enough that the Stop warning path does its real work instead
        # of short circuiting on a tiny transcript.
        with io.open(self.transcript, "w", encoding="utf-8") as handle:
            handle.write(("{\"role\": \"assistant\"}\n") * 4000)

        self._saved_cwd = os.getcwd()
        for key in SCRUBBED_ENV + ("HOME",):
            self._saved_env[key] = os.environ.get(key)
        os.environ["HOME"] = self.home
        os.environ["BROTHERME_CONFIG"] = self.config
        os.environ["BROTHERMODE_VAULT"] = self.vault
        os.environ["BROTHERMODE_ROOT"] = self.project
        for key in SCRUBBED_ENV:
            if key not in ("BROTHERME_CONFIG", "BROTHERMODE_VAULT",
                           "BROTHERMODE_ROOT"):
                os.environ.pop(key, None)
        # Bytecode caches belong to the throwaway world too. Without this the
        # measured imports would write __pycache__ into the checkout, and this
        # tool promises to write nothing into the repository but its page.
        self._saved_pycache = getattr(sys, "pycache_prefix", None)
        sys.pycache_prefix = os.path.join(self.base, "pycache")
        # AND belt as well as braces. sys.pycache_prefix alone was not enough
        # on continuous integration, where this tool's own guard caught a
        # __pycache__ landing in tools/ on machines whose checkout had none
        # yet; it could not be reproduced on a developer machine, which is
        # exactly the shape of defect that ships. Rather than theorize about
        # the difference, the promise is made unconditional: nothing writes
        # bytecode at all while the sandbox is entered. The cost to the
        # measurement is stated on the page: every repetition now pays a
        # compile it cannot cache, which makes the figures a LOWER bound by
        # slightly less than before, never a higher one.
        self._saved_dontwrite = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        os.chdir(self.project)

        self._consent()
        self._store()
        self._validate()
        return self

    def __exit__(self, *_exc):
        os.chdir(self._saved_cwd)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.pycache_prefix = self._saved_pycache
        sys.dont_write_bytecode = self._saved_dontwrite
        shutil.rmtree(self.base, ignore_errors=True)
        return False

    def _consent(self):
        """Consent written through scripts/setup.py's own writer, so the
        config shape is the product's rather than this file's idea of it."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_hookbench", os.path.join(ROOT, "scripts", "setup.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.write_config(self.vault, "clone", path=self.config)
        if not module.is_consented(module.read_config(self.config)[0]):
            raise BenchError("the throwaway setup config did not read back as "
                             "consented, so every hook would take its "
                             "pre consent path and the page would report the "
                             "cost of doing nothing")

    def _store(self):
        """A real store with one active claim, created by running the real
        command line the same way the hooks are run."""
        store = os.path.join(HERE, "bm_store.py")
        result = run_program(store, ["init"], "")
        if result["exit_code"] != 0:
            raise BenchError("bm_store.py init failed in the throwaway "
                             "project: %s%s"
                             % (result["stderr"], result["crash"]))
        fence = os.path.join(HERE, "bm_fence_hook.py")
        label = run_program(fence, ["session-label", "--session-id",
                                    self.session_id], "")
        if label["exit_code"] != 0 or not label["stdout"].strip():
            raise BenchError("could not derive a fence label in the throwaway "
                             "project: %s%s"
                             % (label["stderr"], label["crash"]))
        self.label = label["stdout"].strip()
        claimed = run_program(store, [
            "claim", "hookbench", "--lifetime", "ephemeral",
            "--objective", "measure what the hooks cost",
            "--files", os.path.relpath(self.target, self.project),
            "--session", self.label], "")
        if claimed["exit_code"] != 0:
            raise BenchError("bm_store.py claim failed in the throwaway "
                             "project: %s%s"
                             % (claimed["stderr"], claimed["crash"]))

    def _validate(self):
        """Prove the throwaway world makes the fence DO ITS WORK before a
        single number is taken.

        Borrowed from scripts/doctor.py's own reasoning: a fence that refuses
        everything passes half a check, and a fence that refuses nothing passes
        the other half. A benchmark against a sandbox where the hook silently
        fails open would report the cost of doing nothing, and it would look
        like good news. So a foreign session must be REFUSED here, and the
        owning session must be ALLOWED, or this run refuses to produce a
        page."""
        fence = os.path.join(HERE, "bm_fence_hook.py")
        intruder = run_program(fence, [], payload_for(
            "PreToolUse", "Edit", self.session_id + "-intruder", self.project,
            self.transcript, self.target))
        if "permissionDecision" not in intruder["stdout"]:
            raise BenchError(
                "the throwaway fence did NOT refuse a foreign session's write "
                "to a claimed file, so every timing taken here would be the "
                "cost of a hook that checked nothing. stderr: %s%s"
                % (intruder["stderr"].strip(), intruder["crash"]))
        owner = run_program(fence, [], payload_for(
            "PreToolUse", "Edit", self.session_id, self.project,
            self.transcript, self.target))
        if "permissionDecision" in owner["stdout"]:
            raise BenchError(
                "the throwaway fence refused the OWNING session's own write, "
                "so this sandbox is measuring a fence that refuses everything "
                "rather than one doing real work. stderr: %s%s"
                % (owner["stderr"].strip(), owner["crash"]))


# ---------------------------------------------------------------------------
# The measurement.
# ---------------------------------------------------------------------------

def machine_record():
    """The machine and interpreter, read from the environment rather than
    typed. platform.node() is deliberately absent: a hostname is the founder's
    business and it is not evidence of anything."""
    try:
        load = os.getloadavg()
        load_record = [round(value, 2) for value in load]
    except (OSError, AttributeError):
        load_record = None
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "load_average_at_start": load_record,
    }


def measure(reps=DEFAULT_REPS, chains=None):
    """Run the whole benchmark and return the record the page renders from."""
    if reps < 1:
        raise BenchError("reps must be at least 1")
    chains = read_hook_chains() if chains is None else chains
    record = {
        "generator": "tools/bm_hookbench.py",
        "measured_local_date": time.strftime("%Y-%m-%d"),
        "repetitions": reps,
        "warmup_repetitions": WARMUP_REPS,
        "reproducibility_factor": REPRODUCIBILITY_FACTOR,
        "machine": machine_record(),
        "manifest": [
            {"event": group["event"], "matcher": group["matcher"],
             "timeout_seconds": group["timeout_seconds"],
             "programs": [entry["program"] for entry in group["programs"]]}
            for group in chains],
        "not_measured": [{"topic": topic, "reason": reason}
                         for topic, reason in NOT_MEASURED],
        "actions": [],
        "unrunnable": [],
    }
    seen_unrunnable = set()
    for group in chains:
        for entry in group["programs"]:
            if entry["runnable"] or entry["program"] in seen_unrunnable:
                continue
            seen_unrunnable.add(entry["program"])
            record["unrunnable"].append({
                "program": entry["program"],
                "event": group["event"],
                "reason": entry["reason"],
            })

    with Sandbox() as box:
        for label, event_specs in ACTIONS:
            steps = []
            for event, tool_name in event_specs:
                for group in groups_for(chains, event, tool_name):
                    stdin_text = payload_for(
                        event, tool_name, box.session_id, box.project,
                        box.transcript, box.target)
                    for entry in group["programs"]:
                        if not entry["runnable"]:
                            continue
                        steps.append({
                            "event": event,
                            "program": entry["program"],
                            "args": entry["args"],
                            "timeout_seconds": group["timeout_seconds"],
                            "stdin": stdin_text,
                            "path": os.path.join(HERE, entry["program"]),
                        })
            if not steps:
                continue
            record["actions"].append(
                _measure_action(label, event_specs, steps, reps))
    return record


def _measure_action(label, event_specs, steps, reps):
    """One action, measured as a chain. Each repetition runs every program of
    the chain in order and times both the individual programs and the total,
    so the per program figures and the chain figure come from the same runs
    and cannot disagree with each other."""
    per_program = [[] for _ in steps]
    totals = []
    failures = 0
    fail_open = 0
    crashes = []
    exit_codes = set()
    for rep in range(reps + WARMUP_REPS):
        started = time.perf_counter()
        rep_times = []
        for index, step in enumerate(steps):
            result = run_program(step["path"], step["args"], step["stdin"])
            rep_times.append(result["seconds"])
            if rep < WARMUP_REPS:
                continue
            exit_codes.add(result["exit_code"])
            if result["exit_code"] != 0:
                failures += 1
            if result["crash"]:
                crashes.append("%s: %s" % (step["program"], result["crash"]))
            if FAIL_OPEN_MARK in result["stderr"]:
                fail_open += 1
        total = time.perf_counter() - started
        if rep < WARMUP_REPS:
            continue
        totals.append(total)
        for index, value in enumerate(rep_times):
            per_program[index].append(value)

    programs = []
    for index, step in enumerate(steps):
        programs.append({
            "event": step["event"],
            "program": step["program"],
            "args": step["args"],
            "timeout_seconds": step["timeout_seconds"],
            "timing": summarize(per_program[index]),
        })
    chain = summarize(totals)
    budgets = sorted(set(step["timeout_seconds"] for step in steps
                         if step["timeout_seconds"] is not None))
    return {
        "action": label,
        "events": [{"event": event, "tool_name": tool} for event, tool in
                   event_specs],
        "program_count": len(steps),
        "programs": programs,
        "chain": chain,
        "sum_of_program_medians_ms": round(
            sum(entry["timing"]["median_ms"] for entry in programs), 2),
        "shared_timeout_seconds": budgets,
        "chain_share_of_smallest_budget_percent": (
            round(100.0 * (chain["median_ms"] / 1000.0) / budgets[0], 3)
            if budgets else None),
        "calls_counted": len(steps) * len(totals),
        "non_zero_exits": failures,
        "fail_open_lines": fail_open,
        "crashes": sorted(set(crashes)),
        "exit_codes_seen": sorted(code for code in exit_codes
                                  if code is not None),
    }


# ---------------------------------------------------------------------------
# The page. render(record) is a pure function of the record, and the record is
# embedded in the page, so tools/test_bm_hookbench.py can prove that no number
# on the page was typed by hand: it re-renders from the embedded record and
# demands the same bytes back.
# ---------------------------------------------------------------------------

RECORD_FENCE = "```json"
RECORD_HEADING = "## The record this page was rendered from"


def _cell(text):
    """One table cell. The pipe is escaped because a hook matcher is a regex
    and this project's matchers contain alternations: an unescaped
    `Edit|Write|MultiEdit` splits one cell into three and the row a reader
    sees stops being the row the manifest holds."""
    return str(text).replace("|", "\\|")


def _table(headers, rows):
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(out)


def _timing_cells(timing):
    return ["%.2f" % timing["median_ms"], "%.2f" % timing["min_ms"],
            "%.2f" % timing["p90_ms"], "%.2f" % timing["max_ms"],
            str(timing["samples"])]


TIMING_HEADERS = ("median ms", "min ms", "p90 ms", "max ms", "samples")


def render(record):
    """The whole page, as text, from the record alone."""
    machine = record["machine"]
    lines = []
    add = lines.append

    add("# What the hooks cost")
    add("")
    add("Status: CURRENT")
    add("")
    add("GENERATED by `%s`. Do not edit this page by hand: every number on it "
        "is produced by running that tool, and the record it was rendered "
        "from is printed at the bottom." % record["generator"])
    add("")
    add("Measured on %s, the measuring machine's own local date. Timing "
        "numbers vary by machine, by Python version and by what else the "
        "machine was doing at the time, so the machine record below is part "
        "of the evidence rather than a footnote. Nothing here transfers to a "
        "different machine unchanged."
        % record["measured_local_date"])
    add("")

    add("## The machine these numbers came from")
    add("")
    add(_table(("Fact", "Value"), (
        ("Platform", "`%s`" % machine["platform"]),
        ("Architecture", "`%s`" % machine["machine"]),
        ("Logical CPUs", str(machine["cpu_count"])),
        ("Interpreter", "%s %s" % (machine["python_implementation"],
                                   machine["python_version"])),
        ("Load average when the run started",
         ", ".join("%.2f" % value
                   for value in machine["load_average_at_start"])
         if machine["load_average_at_start"] else "not available here"),
    )))
    add("")
    add("Repetitions per program: %d counted, after %d discarded as warmup. "
        "The warmup exists because the first run of a program compiles its "
        "imports and fills the bytecode cache, a cost a real install pays "
        "once rather than on every call. Every figure below is a median with "
        "its own spread beside it, never a single sample."
        % (record["repetitions"], record["warmup_repetitions"]))
    add("")

    add("## What actually runs, read from hooks/hooks.json")
    add("")
    add("This table is generated from the manifest, so it cannot drift away "
        "from what the runtime executes. The Stop event is the one to read "
        "twice: its programs are chained inside a single shell command and "
        "therefore share one timeout between them.")
    add("")
    add(_table(("Event", "Matcher", "Timeout s", "Programs in order"), tuple(
        (group["event"], "`%s`" % group["matcher"] if group["matcher"] else "any",
         str(group["timeout_seconds"]),
         ", ".join("`%s`" % name for name in group["programs"]) or "none")
        for group in record["manifest"])))
    add("")

    add("## What one user action costs")
    add("")
    add("Read these as MEASURED LOWER BOUNDS. They cover compiling each "
        "program, cold importing its dependencies, reading the payload and "
        "making the decision. They do not cover the fork, the exec and the "
        "interpreter bootstrap each hook process pays before any of that, "
        "which is the first entry under what is not measured below. Add that "
        "residue once per program in the chain to get a true wall clock cost.")
    add("")
    add(_table(("Action", "Programs", "Chain median ms", "Chain max ms",
                "Share of the smallest budget"), tuple(
        (action["action"], str(action["program_count"]),
         "%.2f" % action["chain"]["median_ms"],
         "%.2f" % action["chain"]["max_ms"],
         ("%.3f percent of %ds"
          % (action["chain_share_of_smallest_budget_percent"],
             action["shared_timeout_seconds"][0]))
         if action["shared_timeout_seconds"] else "no budget declared")
        for action in record["actions"])))
    add("")

    add("## Each program on its own, and the chain it sits in")
    add("")
    add("The per program figures and the chain figure come from the same "
        "repetitions, so they cannot disagree about what ran. They can still "
        "differ as numbers, and both are printed for that reason: the median "
        "of the sums is not the sum of the medians, and where a chain is long "
        "and each call is variable, the chain median sits above the sum of "
        "the per program medians because a repetition only needs one slow "
        "call to be slow.")
    add("")
    for action in record["actions"]:
        add("### %s" % action["action"])
        add("")
        add("Events fired: %s."
            % ", ".join("%s%s" % (entry["event"],
                                  " on %s" % entry["tool_name"]
                                  if entry["tool_name"] else "")
                        for entry in action["events"]))
        add("")
        add(_table(("Program", "Arguments", "Own timeout s") + TIMING_HEADERS,
                   tuple(
            ("`%s`" % entry["program"],
             "`%s`" % " ".join(entry["args"]) if entry["args"] else "none",
             str(entry["timeout_seconds"])) + tuple(_timing_cells(entry["timing"]))
            for entry in action["programs"])))
        add("")
        add("Chain total: median %.2f ms, min %.2f ms, p90 %.2f ms, max "
            "%.2f ms over %d repetitions. Sum of the program medians: %.2f ms."
            % (action["chain"]["median_ms"], action["chain"]["min_ms"],
               action["chain"]["p90_ms"], action["chain"]["max_ms"],
               action["chain"]["samples"],
               action["sum_of_program_medians_ms"]))
        add("")
        add("Calls counted: %d. Non zero exits: %d. Stderr lines reporting a "
            "fence that failed open: %d. Crashes: %s."
            % (action["calls_counted"], action["non_zero_exits"],
               action["fail_open_lines"],
               "; ".join(action["crashes"]) if action["crashes"] else "none"))
        add("")

    add("## Reliability under this benchmark")
    add("")
    add("Counted from real exit codes and real stderr, on this machine, "
        "against a store built for the run. This is not a field failure rate: "
        "see what is not measured below.")
    add("")
    add(_table(("Action", "Calls counted", "Non zero exits",
                "Fail open lines", "Exit codes seen"), tuple(
        (action["action"], str(action["calls_counted"]),
         str(action["non_zero_exits"]), str(action["fail_open_lines"]),
         ", ".join(str(code) for code in action["exit_codes_seen"]) or "none")
        for action in record["actions"])))
    add("")

    add("## What this page does NOT measure")
    add("")
    add("Each entry is a question worth answering that this tool has no "
        "honest way to answer. None of them carries an estimate, because a "
        "number invented here would be worse than the silence it replaced.")
    add("")
    for entry in record["not_measured"]:
        add("**NOT MEASURED: %s.** %s" % (entry["topic"], entry["reason"]))
        add("")
    if record["unrunnable"]:
        add("Programs in the manifest that this tool did not run at all:")
        add("")
        for entry in record["unrunnable"]:
            add("- `%s` (%s): %s"
                % (entry["program"], entry["event"], entry["reason"]))
        add("")

    add("## Reproducing this")
    add("")
    # The command is written WITHOUT the repo-relative prefix on purpose. An
    # installed copy does not live at tools/, and a shipped message that says
    # it sends a reader somewhere they are not; the store suite sweeps for
    # exactly that literal. A plugin install resolves the variable itself; a
    # clone install runs it from the BrotherMode root instead.
    add("```")
    add('python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_hookbench.py" --json > before.json')
    add('python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_hookbench.py" --compare before.json')
    add("```")
    add("")
    add("On a clone install the variable is unset: run the same two commands "
        "from the BrotherMode root, with the leading path removed.")
    add("")
    add("REPRODUCIBILITY BAND: a re-run on the same machine reproduces every "
        "median on this page within a factor of %.1f, that is between %.2f "
        "and %.1f times the number printed. `--compare` enforces exactly that "
        "band and exits non zero when a median falls outside it. A tighter "
        "band is not printed here because timing on a machine that is also "
        "doing other work does not hold one, and a promise this tool cannot "
        "keep is the thing this page exists to stop making."
        % (record["reproducibility_factor"],
           1.0 / record["reproducibility_factor"],
           record["reproducibility_factor"]))
    add("")
    add("The run creates a throwaway home, vault, project and store under a "
        "temporary directory, pins HOME and every BrotherMode environment "
        "variable inside it, and deletes the lot afterwards. It never reads or "
        "writes your own store, vault or home, and the only file it writes "
        "outside that temporary directory is this page.")
    add("")

    add(RECORD_HEADING)
    add("")
    add("Everything above is rendered from this object by "
        "`tools/bm_hookbench.py`. `tools/test_bm_hookbench.py` re-renders the "
        "page from it and fails if a single byte differs, which is what stops "
        "a number being typed in by hand.")
    add("")
    add(RECORD_FENCE)
    add(json.dumps(record, indent=2, sort_keys=True))
    add("```")
    return "\n".join(lines) + "\n"


def extract_record(text):
    """The record back out of a rendered page. Raises BenchError when the
    page carries none, because a page nobody can re-render is a page nobody
    can check."""
    marker = text.find(RECORD_HEADING)
    if marker == -1:
        raise BenchError("this page carries no %r section" % RECORD_HEADING)
    start = text.find(RECORD_FENCE, marker)
    if start == -1:
        raise BenchError("this page carries no record block")
    start += len(RECORD_FENCE)
    end = text.find("```", start)
    if end == -1:
        raise BenchError("the record block is not closed")
    try:
        return json.loads(text[start:end])
    except ValueError as exc:
        raise BenchError("the record block is not valid JSON: %s" % exc)


# ---------------------------------------------------------------------------
# Comparing two runs against the band the page promises.
# ---------------------------------------------------------------------------

def compare(old, new, factor=None):
    """Every median in `new` against the same median in `old`, as
    [{"label", "old_ms", "new_ms", "ratio", "within"}]. A program present in
    one record and not the other is reported as a mismatch rather than
    skipped: a chain that changed shape is exactly what a reader needs told."""
    factor = old.get("reproducibility_factor", REPRODUCIBILITY_FACTOR) \
        if factor is None else factor

    def medians(record):
        out = {}
        for action in record.get("actions", ()):
            out["%s :: chain" % action["action"]] = action["chain"]["median_ms"]
            for entry in action.get("programs", ()):
                out["%s :: %s %s" % (action["action"], entry["program"],
                                     " ".join(entry["args"]))] = \
                    entry["timing"]["median_ms"]
        return out

    before, after = medians(old), medians(new)
    rows = []
    for label in sorted(set(before) | set(after)):
        old_ms, new_ms = before.get(label), after.get(label)
        if old_ms is None or new_ms is None:
            rows.append({"label": label, "old_ms": old_ms, "new_ms": new_ms,
                         "ratio": None, "within": False})
            continue
        ratio = (new_ms / old_ms) if old_ms else float("inf")
        rows.append({"label": label, "old_ms": old_ms, "new_ms": new_ms,
                     "ratio": round(ratio, 3),
                     "within": (1.0 / factor) <= ratio <= factor})
    return rows


# ---------------------------------------------------------------------------
# Command line.
# ---------------------------------------------------------------------------

def _write_page(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv):
    if any(arg in ("-h", "--help") for arg in argv):
        sys.stdout.write(__doc__)
        return 0
    reps, as_json, out, compare_to = DEFAULT_REPS, False, PAGE, None
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--reps":
            if not rest:
                sys.stderr.write("bm_hookbench: --reps needs a number\n")
                return 2
            try:
                reps = int(rest.pop(0))
            except ValueError:
                sys.stderr.write("bm_hookbench: --reps wants an integer\n")
                return 2
        elif arg == "--json":
            as_json = True
        elif arg == "--out":
            if not rest:
                sys.stderr.write("bm_hookbench: --out needs a path\n")
                return 2
            out = rest.pop(0)
        elif arg == "--compare":
            if not rest:
                sys.stderr.write("bm_hookbench: --compare needs a JSON file\n")
                return 2
            compare_to = rest.pop(0)
        else:
            sys.stderr.write(
                "bm_hookbench: unrecognized argument %s (recognized: --help, "
                "--reps N, --json, --out PATH, --compare PATH)\n" % arg)
            return 2
    try:
        if compare_to is not None:
            with io.open(compare_to, encoding="utf-8") as handle:
                previous = json.load(handle)
            rows = compare(previous, measure(reps))
            outside = [row for row in rows if not row["within"]]
            for row in rows:
                sys.stdout.write(
                    "%-8s %-70s %s -> %s (ratio %s)\n"
                    % ("WITHIN" if row["within"] else "OUTSIDE",
                       row["label"][:70], row["old_ms"], row["new_ms"],
                       row["ratio"]))
            sys.stdout.write(
                "bm_hookbench: %d of %d medians inside the stated band of "
                "%.1fx\n" % (len(rows) - len(outside), len(rows),
                             previous.get("reproducibility_factor",
                                          REPRODUCIBILITY_FACTOR)))
            return 1 if outside else 0
        record = measure(reps)
        if as_json:
            sys.stdout.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
            return 0
        _write_page(out, render(record))
        sys.stdout.write("bm_hookbench: wrote %s from %d repetitions on %s\n"
                         % (os.path.relpath(out, ROOT), reps,
                            record["machine"]["platform"]))
        return 0
    except BenchError as exc:
        sys.stderr.write("bm_hookbench: %s\n" % exc)
        return 2
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write("bm_hookbench: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

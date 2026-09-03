#!/usr/bin/env python3
"""Real subprocess measurement of what BrotherMode's hooks cost, for FENCE V7
(agent hook-performance, v3 gap-closure, WAVE-B-BRIEFS.md). Standard library
only. Run: python3 tools/test_bm_hookperf.py

WHY THIS FILE EXISTS
  tools/bm_hookbench.py measures each hook PROGRAM in process, through
  runpy.run_path, because tools/test_bm.py bans `import subprocess` in every
  shipping module under tools/ (two named exceptions, neither this one), so
  that tool cannot fork a real process. It says so itself, twice: "Bare
  interpreter startup per hook process" and "The SessionStart hook" (a shell
  script it cannot execute in process at all) are both filed under its own
  NOT MEASURED section for exactly that reason.

  This file is a TEST file. tools/test_bm.py's own subprocess ban carries a
  standing exemption for test files (see that suite's
  test_no_network_claim_is_mechanically_true: "the test files themselves may
  import subprocess to drive the CLI they are testing"), so this file is
  free to spawn the REAL sh and python3 processes hooks/hooks.json actually
  wires: the fork, the exec, the interpreter bootstrap, and the shell
  wrapper's own cat/printf pipeline that bm_hookbench.py's in-process
  approach skips entirely. It fills bm_hookbench.py's two stated gaps with
  real numbers, and it is the measurement half of FENCE V7's mission:
  "measure the CURRENT per-event process fan-out first... repetitions and
  medians... never a single sample" (WAVE-B-BRIEFS.md).

WHY NO CONSOLIDATION WAS APPLIED TO hooks/hooks.json
  FENCE V7's mission also asks to "consolidate multi-process chains into one
  deterministic dispatch per event ONLY where behavior equivalence is
  provable." Investigation before writing any code here found that it is
  NOT provable within this fence's ownership (hooks/, plus this file, plus
  docs/evidence/v3/hook-performance.md; nothing else), because three
  currently green files this fence does not own hard pin the current
  multi-process shape:

  1. tools/test_bm_consent.py: TelemetryEveryHookProgramPreConsentCase
     declares MIN_WIRED_COMMAND_STRINGS = 7 and MIN_WIRED_PROGRAMS = 11 as
     floors, in its own words "raising a floor is the only direction this
     number is ever allowed to move." The live count of both, read fresh
     from hooks/hooks.json by TestBlockersAreReal below, is EXACTLY 7 and
     EXACTLY 11: zero headroom. Any hooks.json edit that removes or merges a
     wired python3/sh invocation drops one of these counts and fails that
     suite.
  2. tools/test_bm_hookbench.py: test_the_stop_event_chains_four_programs_
     under_one_shared_timeout asserts the Stop event wires at least 2
     programs, read fresh from hooks/hooks.json by its own parser (which
     only recognizes tools/ and scripts/ prefixed invocations, so a
     consolidating dispatch script placed anywhere, including inside
     hooks/, would not even register as a program and would fail this
     assertion at 0, not merely drop below the floor).
  3. The "four-copy law": scripts/install.py's hook_commands() function
     documents itself, in its own comments, as a hand-maintained duplicate
     of hooks/hooks.json's wiring that "must move in the same change", and
     tools/test_bm_docs.py::TestHandWiringBlocksMatchInstaller parses the
     hand-wiring JSON blocks in docs/QUICKSTART.md and docs/SETUP.md and
     compares them, command text included, against scripts/install.py's
     hook_groups(). None of those three files (install.py, QUICKSTART.md,
     SETUP.md) are in this fence's ownership, so a real wiring change here
     would leave them inconsistent with the documented invariant even where
     no single automated assertion goes red today.

  TestBlockersAreReal re-derives all three checks against the live files on
  every run, so this reasoning is verified mechanically rather than trusted
  as prose that can go stale.

WHAT "PROJECTED" MEANS IN THE RECORD BELOW
  Since no wiring change was safe to apply, this file cannot report a real
  measured "after". What it reports instead, clearly labeled and never
  conflated with a real measurement, is a PROJECTION: bare interpreter
  startup (measured for real, once, by this file) plus the sum of
  bm_hookbench.py's own already-validated per-program in-process medians
  for the programs in one chain, as an estimate of what ONE process paying
  the fork/exec/bootstrap cost once, instead of once per program, would
  cost. It is arithmetic over two separately and honestly measured
  quantities, not a real end-to-end measurement of a script that was ever
  written to hooks/ or run as a hook: no dispatch script was created here,
  because the three blockers above stood. The projection is conservative
  (a lower bound on the likely benefit): a real consolidated process could
  also share warm imports across its calls (bm_store.py alone is 17
  thousand lines per bm_hookbench.py's own docstring) and save more than
  this projection credits, but this file does not claim that saving,
  because nothing here actually shares those imports.

Python 3.9, standard library only. No network (the one `claude --version`
probe is a local CLI call, not a network request, and is wrapped so a
missing binary degrades the field to null rather than failing the suite).
No em or en dashes anywhere in this file or its output.

Usage:
  python3 tools/test_bm_hookperf.py                  # run the regression suite
  python3 tools/test_bm_hookperf.py --measure         # print a full JSON record
  python3 tools/test_bm_hookperf.py --measure --reps 8 --out FILE
"""

import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(ROOT, "hooks", "hooks.json")
SESSIONSTART = os.path.join(HERE, "bm_sessionstart.py")
CONSENT_TEST = os.path.join(HERE, "test_bm_consent.py")
HOOKBENCH_TEST = os.path.join(HERE, "test_bm_hookbench.py")
INSTALL_PY = os.path.join(ROOT, "scripts", "install.py")

# The chain table, imported rather than retyped, for the same reason
# tools/bm_hookbench.py imports it: after the Windows port the Stop and
# PreCompact commands name a driver, and the process arithmetic below has to
# know how many programs that driver runs.
_hcspec = importlib.util.spec_from_file_location(
    "bm_hookchain_for_perf", os.path.join(HERE, "bm_hookchain.py"))
hookchain = importlib.util.module_from_spec(_hcspec)
_hcspec.loader.exec_module(hookchain)

# bm_hookbench.py, imported rather than re-implemented: its Sandbox,
# payload_for, groups_for, read_hook_chains, parse_command, summarize,
# percentile and machine_record are already written, already tested by
# tools/test_bm_hookbench.py, and reusing them keeps the two files honest
# about measuring against the SAME manifest reader and the SAME throwaway
# world, rather than two hand-written copies that could quietly drift.
_spec = importlib.util.spec_from_file_location(
    "bm_hookbench", os.path.join(HERE, "bm_hookbench.py"))
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)
sys.modules["bm_hookbench"] = hb

DEFAULT_REPS = 15
WARMUP_REPS = 1
#: Real process spawn timing carries OS scheduler and fork/exec variance on
#: top of everything bm_hookbench.py's in-process figures already carry, so
#: the band this file promises for a re-run is wider than bm_hookbench.py's
#: 2.0x. Enforced nowhere in this file (there is no --compare mode here);
#: stated because plan section 25 asks for a reproducibility figure and a
#: number this file cannot back with a compare command would be worse than
#: not printing one, so it is documented instead as a stated widening of
#: bm_hookbench.py's own figure, not a separately validated constant.
REPRODUCIBILITY_FACTOR = 3.0


class PerfError(Exception):
    """A measurement, or a blocker claim, could not be taken or verified
    honestly. Never worked around."""


# ---------------------------------------------------------------------------
# The manifest, read with the raw command text kept. bm_hookbench.py's own
# read_hook_chains() throws the raw string away once parse_command() has
# pulled the programs out of it; this file needs the raw string too, because
# it execs the WHOLE command through a real shell, cat/printf/pipe and all,
# rather than the individual programs bm_hookbench.py extracted.
# ---------------------------------------------------------------------------

def read_hook_chains_raw(path=HOOKS_JSON):
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError) as exc:
        raise PerfError("cannot read %s: %s" % (path, exc))
    except ValueError as exc:
        raise PerfError("%s is not valid JSON: %s" % (path, exc))
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        raise PerfError("%s carries no hooks object" % path)
    groups = []
    for event, entries in hooks.items():
        for entry in entries or ():
            for hook in entry.get("hooks", ()):
                command = hook.get("command", "")
                groups.append({
                    "event": event,
                    "matcher": entry.get("matcher", ""),
                    "timeout_seconds": hook.get("timeout"),
                    "raw_command": command,
                    "programs": hb.parse_command(command),
                })
    if not groups:
        raise PerfError("%s wires no hook commands at all" % path)
    return groups


def resolve_command(raw_command, root=ROOT):
    return raw_command.replace("${CLAUDE_PLUGIN_ROOT}", root).replace(
        "$CLAUDE_PLUGIN_ROOT", root)


#: The user actions this file reports on, matched to bm_hookbench.py's own
#: ACTIONS tuple by IDENTICAL label text so a record from each file can be
#: paired up by action name. SessionStart is added at the front: it is the
#: one event bm_hookbench.py's docs name as entirely unmeasured because it
#: is a shell script, and a real subprocess call is the only way to time it
#: at all.
ACTIONS = (
    ("One SessionStart event, at the start of every session",
     (("SessionStart", None),)),
) + hb.ACTIONS


# ---------------------------------------------------------------------------
# A conservative, text based process count for hooks/hooks.json's OWN
# command strings. Not a general shell parser (this file does not own an
# arbitrary shell interpreter's semantics): it recognizes exactly the two
# shapes this manifest uses today, a bare command and a `sh -c
# 'stmt; stmt; ...'` script whose statements are either `p=$(cat)` or a
# `printf ... | PROGRAM` pipe, and refuses rather than guesses on anything
# else, the same discipline bm_hookbench.py's own parse_command() uses.
# ---------------------------------------------------------------------------

_SH_C_RE = re.compile(r"^sh -c '(.*)'$", re.DOTALL)


_CHAIN_RE = re.compile(r"bm_hookchain\.py[\"']?\s+([a-z][a-z0-9-]*)")


def count_real_processes(raw_command):
    """How many real OS processes ONE hooks.json command string spawns:
    one for a bare command; one for the chain driver PLUS one per program in
    the chain it names; or one for the outer `sh -c` plus one per statement
    inside it (one more for each `|` a statement contains, since every stage
    of a POSIX pipeline forks even when the left side is a shell builtin like
    printf attaching to the pipe).

    The chain-driver arm arrived with the 2026-08-17 Windows port and is the
    reason the Stop count dropped from ten to five: the same four programs
    now cost one driver plus four children instead of a shell, a cat, and
    four two-stage pipes. The `sh -c` arm is KEPT rather than deleted with
    its last caller, so that a hook which reaches for a shell again is
    counted honestly rather than reported as one process."""
    stripped = raw_command.strip()
    chain = _CHAIN_RE.search(stripped)
    if chain and not stripped.startswith("sh -c"):
        rows = hookchain.CHAINS.get(chain.group(1))
        if rows is None:
            raise PerfError(
                "hooks/hooks.json names chain %r, which "
                "tools/bm_hookchain.py does not define; it dies at every "
                "firing" % chain.group(1))
        return 1 + len(rows)
    match = _SH_C_RE.match(stripped)
    if not match:
        return 1
    count = 1  # the outer sh
    statements = [s.strip() for s in match.group(1).split(";") if s.strip()]
    if not statements:
        raise PerfError("sh -c wraps no statements at all: %r" % raw_command)
    for statement in statements:
        if statement == "p=$(cat)":
            count += 1
            continue
        pipes = statement.count("|")
        if pipes:
            count += pipes + 1
        else:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Real process timing.
# ---------------------------------------------------------------------------

def run_real(command_text, payload, cwd, timeout_seconds):
    """Run ONE hooks.json command string through a REAL shell: shell=True is
    required and is the point, exactly as tools/test_bm_consent.py's
    test_no_wired_command_of_any_module_writes_before_consent already
    established for this exact manifest (Claude Code hands these exact
    strings to a shell too), timed with a monotonic clock around the real
    fork and exec."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command_text, shell=True, cwd=cwd, input=payload,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=(timeout_seconds or 60) + 30)
        elapsed = time.perf_counter() - started
        return {"seconds": elapsed, "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        out = exc.stdout or ""
        err = exc.stderr or ""
        return {"seconds": elapsed, "exit_code": None,
                "stdout": out if isinstance(out, str) else "",
                "stderr": err if isinstance(err, str) else "",
                "timed_out": True}


def bare_interpreter_startup(reps, warmup):
    """A real `python3 -c pass`, repeated: the fork, the exec and the
    CPython bootstrap ONLY, no BrotherMode code at all. This is
    bm_hookbench.py's own "Bare interpreter startup per hook process" NOT
    MEASURED entry, filled in with a real number."""
    samples = []
    for rep in range(reps + warmup):
        started = time.perf_counter()
        subprocess.run(  # sbe: allow-silent this is a pure timing probe of interpreter startup, not a correctness check; "python3 -c pass" is not expected to fail
            [sys.executable, "-c", "pass"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        elapsed = time.perf_counter() - started
        if rep < warmup:
            continue
        samples.append(elapsed)
    return hb.summarize(samples)


def full_machine_record():
    """bm_hookbench.py's own machine_record(), plus the Claude Code CLI
    version plan section 25 asks for and bm_hookbench.py cannot add itself
    (it spawns no process). A missing or erroring `claude` binary degrades
    this one field to null rather than failing the whole measurement: the
    CLI's presence is not something this file can guarantee about the
    machine it runs on."""
    record = dict(hb.machine_record())
    try:
        result = subprocess.run(["claude", "--version"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=10)
        text = (result.stdout or result.stderr or "").strip()
        record["claude_code_version"] = text or None
    except (OSError, subprocess.TimeoutExpired):
        record["claude_code_version"] = None
    return record


def _measure_action_real(label, event_specs, steps, cwd, reps, warmup):
    per_command = [[] for _ in steps]
    totals = []
    failures = 0
    timeouts = 0
    exit_codes = set()
    for rep in range(reps + warmup):
        started = time.perf_counter()
        rep_times = []
        for index, step in enumerate(steps):
            result = run_real(step["command"], step["payload"], cwd,
                              step["timeout_seconds"])
            rep_times.append(result["seconds"])
            if rep < warmup:
                continue
            if result["timed_out"]:
                timeouts += 1
            elif result["exit_code"] != 0:
                failures += 1
            exit_codes.add(result["exit_code"])
        total = time.perf_counter() - started
        if rep < warmup:
            continue
        totals.append(total)
        for index, value in enumerate(rep_times):
            per_command[index].append(value)
    commands = []
    for index, step in enumerate(steps):
        commands.append({
            "event": step["event"], "matcher": step["matcher"],
            "timeout_seconds": step["timeout_seconds"],
            "timing": hb.summarize(per_command[index]),
        })
    chain = hb.summarize(totals)
    return {
        "action": label,
        "events": [{"event": e, "tool_name": t} for e, t in event_specs],
        "command_count": len(steps),
        "commands": commands,
        "chain": chain,
        "calls_counted": len(steps) * len(totals),
        "non_zero_exits": failures,
        "timeouts": timeouts,
        "exit_codes_seen": sorted(c for c in exit_codes if c is not None),
    }


def measure_real(reps=DEFAULT_REPS, warmup=WARMUP_REPS, chains=None):
    """The real, whole-process measurement: every action in ACTIONS, run as
    the real shell command(s) hooks/hooks.json wires for it, inside
    bm_hookbench.py's own throwaway Sandbox (a real consented world with a
    real fence claim, so the fence does real work rather than failing open
    the cheap way)."""
    chains = read_hook_chains_raw() if chains is None else chains
    record = {
        "generator": "tools/test_bm_hookperf.py",
        "measured_local_date": time.strftime("%Y-%m-%d"),
        "repetitions": reps,
        "warmup_repetitions": warmup,
        "reproducibility_factor": REPRODUCIBILITY_FACTOR,
        "machine": full_machine_record(),
        "manifest": [
            {"event": g["event"], "matcher": g["matcher"],
             "timeout_seconds": g["timeout_seconds"],
             "raw_command": g["raw_command"],
             "real_process_count": count_real_processes(g["raw_command"])}
            for g in chains],
        "bare_interpreter_startup_ms": bare_interpreter_startup(reps, warmup),
        "actions": [],
    }
    with hb.Sandbox() as box:
        for label, event_specs in ACTIONS:
            steps = []
            for event, tool_name in event_specs:
                for group in hb.groups_for(chains, event, tool_name):
                    payload = hb.payload_for(
                        event, tool_name, box.session_id, box.project,
                        box.transcript, box.target)
                    steps.append({
                        "event": event, "matcher": group["matcher"],
                        "timeout_seconds": group["timeout_seconds"],
                        "command": resolve_command(group["raw_command"]),
                        "payload": payload,
                    })
            if not steps:
                continue
            record["actions"].append(_measure_action_real(
                label, event_specs, steps, box.project, reps, warmup))
    return record


# ---------------------------------------------------------------------------
# Projections. Every number that feeds a projection is measured the SAME
# way, real subprocess against real subprocess, on purpose: an earlier draft
# of this file computed the projection from bm_hookbench.py's own in-process
# per-program medians instead, and it produced a projection LARGER than the
# real chain it was supposed to be smaller than. The cause was methodology,
# not noise (reproduced at 5 repetitions, spread factor under 1.05 on both
# sides): bm_hookbench.py's Sandbox sets `sys.dont_write_bytecode = True` so
# its own measurement never writes into this repository, which means every
# one of its in-process calls RECOMPILES from source, on every repetition,
# by design. A real `python3 tools/bm_telemetry.py` subprocess in a checkout
# that already has bytecode cached from earlier test runs pays no such
# compile cost. The two figures are both honest about what they measure, and
# neither is wrong, but they measure different regimes and are not
# addable. So this projection uses ONLY numbers measured the real-subprocess
# way, standalone timings of each individual program (bypassing the sh -c
# wrapper) taken in the SAME run as the chain they project against.
#
# Applied only at EVENT boundaries a real dispatch script could actually
# span: never across PreToolUse and PostToolUse, which straddle the tool
# call itself and cannot be merged into one process no matter how the wiring
# changes.
# ---------------------------------------------------------------------------

def measure_standalone_programs(reps, warmup, chains=None):
    """Every program belonging to the Stop group, the PreCompact group, and
    both PreToolUse groups (fence_hook, bash_audit pre), run as its OWN real
    subprocess with the sh -c/cat/printf wrapper bypassed entirely: just
    `python3 <path> <args>` fed the same payload. Returns
    {(event, matcher): [{"program", "args", "timing"}, ...]}."""
    chains = read_hook_chains_raw() if chains is None else chains
    wanted = [g for g in chains
             if g["event"] in ("Stop", "PreCompact", "PreToolUse")]
    out = {}
    with hb.Sandbox() as box:
        for group in wanted:
            tool_name = "Bash" if group["event"] == "PreToolUse" else None
            payload = hb.payload_for(group["event"], tool_name,
                                     box.session_id, box.project,
                                     box.transcript, box.target)
            entries = []
            for prog in group["programs"]:
                if not prog["runnable"]:
                    continue
                path = os.path.join(HERE, prog["program"])
                cmd = "python3 %s %s" % (
                    shlex.quote(path),
                    " ".join(shlex.quote(a) for a in prog["args"]))
                samples = []
                for rep in range(reps + warmup):
                    result = run_real(cmd, payload, box.project, 30)
                    if rep < warmup:
                        continue
                    samples.append(result["seconds"])
                entries.append({"program": prog["program"],
                                "args": prog["args"],
                                "timing": hb.summarize(samples)})
            # MERGE, never overwrite. read_hook_chains_raw emits one group
            # per wired hook command, so two commands under the same
            # matcher (PreToolUse Bash gained bm_session_cap.py beside
            # bm_bash_audit.py on 2026-08-09) share this key, and plain
            # assignment silently dropped every group but the last: the
            # audit hook vanished from the measurements and projections()
            # died on StopIteration hunting for it. Silent data loss in a
            # measurement suite is the exact defect class this project
            # lints other code for.
            out.setdefault((group["event"], group["matcher"]),
                           []).extend(entries)
    return out


def projections(real_record, standalone):
    """Stop, PreCompact (both single event chains, fully collapsible), and
    the PreToolUse half of a Bash tool call (collapsible on its own;
    PostToolUse cannot join it, see the section docstring above). Returns a
    list of {label, real_chain_median_ms, projected_single_process_ms,
    reduction_percent}, every figure real-subprocess-measured."""
    bare_ms = real_record["bare_interpreter_startup_ms"]["median_ms"]
    out = []

    def _real_chain(label):
        for action in real_record["actions"]:
            if action["action"] == label:
                return action
        raise PerfError("no real action named %r" % label)

    def _project(entries):
        """bare interpreter startup, paid once instead of once per program,
        plus each program's own standalone cost with ITS OWN bare-startup
        share subtracted back out (it already paid one, folded into its
        standalone median): bare + sum(program - bare) == sum(program) -
        (n-1)*bare. Every term here is a real subprocess measurement from
        the same run, so this is arithmetic over one consistent regime."""
        medians = [e["timing"]["median_ms"] for e in entries]
        return sum(medians) - (len(medians) - 1) * bare_ms

    for label, event in (
        ("One Stop event, at the end of every assistant turn", "Stop"),
        ("One PreCompact event, when the context is condensed", "PreCompact"),
    ):
        real_action = _real_chain(label)
        entries = standalone[(event, "")]
        projected = _project(entries)
        real_median = real_action["chain"]["median_ms"]
        out.append({
            "label": label,
            "programs_collapsed": len(entries),
            "real_chain_median_ms": real_median,
            "projected_single_process_ms": round(projected, 2),
            "reduction_percent": round(
                100.0 * (real_median - projected) / real_median, 2)
                if real_median else None,
        })

    # Bash: PreToolUse's two groups (fence_hook, bash_audit pre) collapse
    # into one projected process; PostToolUse (bash_audit post) is real,
    # unchanged, and added back afterward, because it fires only after the
    # real Bash tool call has already run and cannot share a process with
    # anything that ran before that call.
    bash_label = "One Bash tool call"
    real_action = _real_chain(bash_label)
    fence_matcher = next(m for e, m in standalone
                         if e == "PreToolUse" and standalone[(e, m)]
                         and any("fence_hook" in p["program"]
                                for p in standalone[(e, m)]))
    audit_matcher = next(m for e, m in standalone
                         if e == "PreToolUse" and standalone[(e, m)]
                         and any("bash_audit" in p["program"]
                                for p in standalone[(e, m)]))
    pre_entries = (standalone[("PreToolUse", fence_matcher)]
                  + standalone[("PreToolUse", audit_matcher)])
    projected_pre = _project(pre_entries)
    post_real = next(c["timing"]["median_ms"] for c in real_action["commands"]
                     if c["event"] == "PostToolUse")
    projected_bash = round(projected_pre + post_real, 2)
    real_median = real_action["chain"]["median_ms"]
    out.append({
        "label": bash_label,
        "programs_collapsed": len(pre_entries),
        "real_chain_median_ms": real_median,
        "projected_single_process_ms": projected_bash,
        "reduction_percent": round(
            100.0 * (real_median - projected_bash) / real_median, 2)
            if real_median else None,
        "note": ("PreToolUse's two groups collapse to one projected "
                 "process; PostToolUse (bash_audit post) is real, "
                 "unchanged, and added back because it fires after the "
                 "tool call and cannot share a process with anything "
                 "that ran before it."),
    })
    return out


# ---------------------------------------------------------------------------
# The blockers, re-derived from the live files rather than trusted as prose.
# ---------------------------------------------------------------------------

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect_blockers():
    """The three reasons no hooks/hooks.json wiring change was applied,
    each checked against the actual file on disk right now. Raises
    PerfError (a refusal, not a guess) if a file this depends on to explain
    itself has changed shape in a way this cannot read."""
    blockers = []
    chains = read_hook_chains_raw()

    # 1. tools/test_bm_consent.py's zero headroom floor.
    consent_mod = _load_module("bm_test_consent_for_hookperf", CONSENT_TEST)
    case = consent_mod.TelemetryEveryHookProgramPreConsentCase
    live_command_strings = len(chains)
    # Counted THROUGH the chain driver, using the consent suite's own
    # counter rather than a second copy of the rule: after the 2026-08-17
    # Windows port the Stop and PreCompact commands name one driver each,
    # and a naive count would report 8 programs where 12 run, which would
    # make this blocker's headroom look like it had opened up when nothing
    # about the invariant changed.
    live_programs = case.count_wired_programs(
        [(g["event"], g["raw_command"]) for g in chains])
    blockers.append({
        "owner_files": ["tools/test_bm_consent.py"],
        "invariant": ("MIN_WIRED_COMMAND_STRINGS=%d, MIN_WIRED_PROGRAMS=%d, "
                     "documented as floors that may only move up"
                     % (case.MIN_WIRED_COMMAND_STRINGS,
                        case.MIN_WIRED_PROGRAMS)),
        "live_value": ("hooks/hooks.json currently wires %d command "
                       "string(s) and %d program(s)"
                       % (live_command_strings, live_programs)),
        "headroom": min(live_command_strings - case.MIN_WIRED_COMMAND_STRINGS,
                        live_programs - case.MIN_WIRED_PROGRAMS),
        "would_break": ("test_no_wired_command_of_any_module_writes_before_"
                        "consent's floor assertions"),
    })

    # 2. tools/test_bm_hookbench.py's Stop >= 2 programs pin.
    stop_groups = [g for g in hb.read_hook_chains() if g["event"] == "Stop"]
    stop_programs = len(stop_groups[0]["programs"]) if stop_groups else 0
    blockers.append({
        "owner_files": ["tools/test_bm_hookbench.py"],
        "invariant": ("test_the_stop_event_chains_four_programs_under_one_"
                     "shared_timeout requires len(programs) >= 2"),
        "live_value": "the Stop event currently wires %d program(s)" % stop_programs,
        "headroom": stop_programs - 2,
        "would_break": ("that assertion, and a dispatch script placed under "
                        "hooks/ instead of tools/ would not even be counted "
                        "by that suite's parser, reporting 0 rather than "
                        "merely fewer"),
    })

    # 3. The four copy law.
    with io.open(INSTALL_PY, encoding="utf-8") as handle:
        install_text = handle.read()
    present = "four-copy law" in install_text or "four copy law" in install_text
    blockers.append({
        "owner_files": ["scripts/install.py", "docs/QUICKSTART.md",
                        "docs/SETUP.md"],
        "invariant": ("scripts/install.py's hook_commands()/hook_groups() "
                     "hand-duplicate hooks/hooks.json's wiring and are "
                     "documented as needing to move together; "
                     "tools/test_bm_docs.py::TestHandWiringBlocksMatchIn"
                     "staller pins docs/QUICKSTART.md and docs/SETUP.md's "
                     "hand-wiring blocks against install.py's hook_groups() "
                     "command text"),
        "live_value": ("the \"four-copy law\" comment is %s in "
                       "scripts/install.py right now"
                       % ("present" if present else
                          "ABSENT; this reasoning may be stale and should "
                          "be re-verified before being trusted")),
        "headroom": None,
        "would_break": ("no single automated assertion today for a "
                        "shell-wrapper-only edit, but leaves install.py and "
                        "the two docs pages inconsistent with the "
                        "documented invariant, and breaks "
                        "TestHandWiringBlocksMatchInstaller for any edit "
                        "that changes which programs are wired or in what "
                        "order"),
    })
    return blockers


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------

class TestManifestFanoutStatic(unittest.TestCase):

    def test_the_raw_reader_agrees_with_bm_hookbench_on_every_program(self):
        raw = read_hook_chains_raw()
        canonical = hb.read_hook_chains()
        self.assertEqual(len(raw), len(canonical))
        for mine, theirs in zip(raw, canonical):
            self.assertEqual(mine["event"], theirs["event"])
            self.assertEqual(
                [p["program"] for p in mine["programs"]],
                [p["program"] for p in theirs["programs"]])

    def test_the_stop_chain_is_counted_as_five_real_processes(self):
        """One chain driver plus the four programs it runs: 1 + 4 = 5.

        RE-PINNED 2026-08-17, from 10, and the old number is worth keeping
        in this docstring because the drop IS the port's result rather than
        a relaxed assertion. The Stop entry used to be `sh -c` wrapping a
        script: one outer sh, one cat, and four printf-into-python3 pipe
        stages at two processes each, 1 + 1 + 4*2 = 10. It is now one
        python3 driver that reads stdin once and runs four children, so the
        same four programs cost half the processes. The pin exists so a
        change to the Stop wiring's SHAPE is caught here, where the
        arithmetic is spelled out, rather than silently changing what
        docs/evidence/v3/hook-performance.md's fan-out table claims."""
        stop = [g for g in read_hook_chains_raw() if g["event"] == "Stop"]
        self.assertEqual(len(stop), 1)
        self.assertEqual(count_real_processes(stop[0]["raw_command"]), 5)

    def test_the_precompact_chain_is_counted_as_three_real_processes(self):
        """One chain driver plus the two programs it runs: 1 + 2 = 3, down
        from 6 for the same reason the Stop count above dropped from 10."""
        pc = [g for g in read_hook_chains_raw() if g["event"] == "PreCompact"]
        self.assertEqual(len(pc), 1)
        self.assertEqual(count_real_processes(pc[0]["raw_command"]), 3)

    def test_a_shell_wrapped_chain_would_still_be_counted_the_old_way(self):
        """The sh -c arithmetic is KEPT rather than deleted with the last
        caller, and this test is why it can be: if a future hook reaches for
        a shell again, this file still counts its real process fan-out
        correctly instead of reporting one. tools/test_bm_hooks.py refuses
        such a hook outright; this makes sure the measurement would not lie
        about it in the meantime."""
        legacy = ("sh -c 'p=$(cat); printf %s \"$p\" | python3 a.py; "
                  "printf %s \"$p\" | python3 b.py'")
        self.assertEqual(count_real_processes(legacy), 6)

    def test_every_bare_command_counts_as_one_process(self):
        bare_events = ("SessionStart", "SessionEnd", "PostToolUse")
        for group in read_hook_chains_raw():
            if group["event"] in bare_events:
                self.assertEqual(count_real_processes(group["raw_command"]), 1,
                                 group["event"])

    def test_a_sh_c_block_with_no_statements_is_refused(self):
        with self.assertRaises(PerfError):
            count_real_processes("sh -c ''")


class TestBareInterpreterStartup(unittest.TestCase):
    """Small reps here (this is the regression suite, not the evidence
    run): enough to prove the measurement is well formed, not enough to
    make `python3 tools/test_bm_hookperf.py` slow."""

    @classmethod
    def setUpClass(cls):
        cls.record = bare_interpreter_startup(reps=3, warmup=1)

    def test_samples_and_positive_median(self):
        self.assertEqual(self.record["samples"], 3)
        self.assertGreater(self.record["median_ms"], 0.0)
        self.assertGreaterEqual(self.record["max_ms"], self.record["median_ms"])


class TestRealChainMeasurement(unittest.TestCase):
    """One real measurement, small reps, of every action including the one
    (SessionStart) bm_hookbench.py's docs say it cannot measure at all."""

    #: Directories this suite promises not to leave anything new in.
    WATCH = (".", "tools", "docs", "hooks", "scripts")

    @classmethod
    def setUpClass(cls):
        cls.before = cls._tree_snapshot()
        cls.record = measure_real(reps=2, warmup=1)
        cls.after = cls._tree_snapshot()

    @staticmethod
    def _tree_snapshot():
        snapshot = {}
        for rel in TestRealChainMeasurement.WATCH:
            path = os.path.join(ROOT, rel)
            snapshot[rel] = sorted(os.listdir(path))
        return snapshot

    def test_the_run_wrote_nothing_into_this_repository(self):
        self.assertEqual(self.before, self.after,
                         "a real measurement changed the contents of this "
                         "checkout; it is supposed to work entirely inside "
                         "bm_hookbench.py's throwaway Sandbox")

    def test_every_action_measured_including_sessionstart(self):
        labels = [a["action"] for a in self.record["actions"]]
        self.assertIn("One SessionStart event, at the start of every session",
                      labels,
                      "the one action bm_hookbench.py's own docs say it "
                      "cannot measure at all must appear here, real")
        self.assertEqual(len(labels), len(ACTIONS))

    def test_every_action_reports_a_positive_chain_median(self):
        for action in self.record["actions"]:
            self.assertGreater(action["chain"]["median_ms"], 0.0, action["action"])
            for command in action["commands"]:
                self.assertGreater(command["timing"]["median_ms"], 0.0,
                                   action["action"])

    def test_every_real_command_exited_zero_and_did_not_time_out(self):
        for action in self.record["actions"]:
            self.assertEqual(action["timeouts"], 0, action["action"])
            self.assertEqual(action["non_zero_exits"], 0, action["action"])

    def test_the_machine_record_carries_a_claude_code_version_field(self):
        self.assertIn("claude_code_version", self.record["machine"])

    def test_the_record_carries_no_home_directory(self):
        blob = json.dumps(self.record)
        self.assertNotIn(os.path.expanduser("~"), blob)


class TestProjectedConsolidation(unittest.TestCase):
    """That the projection is well FORMED against a real measurement: three
    rows, every published figure positive, every row attributable to a real
    measured event. Never how big the saving is (whatever the number is,
    docs/evidence/v3/hook-performance.md publishes it, not this assertion),
    and never a comparison BETWEEN the two measurement passes: the
    projected-versus-real invariant lives in
    TestProjectionFormulaOnFixedInputs below, on inputs no runner can
    change, for the reason its section comment gives."""

    @classmethod
    def setUpClass(cls):
        cls.real = measure_real(reps=2, warmup=1)
        cls.standalone = measure_standalone_programs(reps=2, warmup=1)
        cls.rows = projections(cls.real, cls.standalone)

    def test_three_rows_are_produced(self):
        labels = [r["label"] for r in self.rows]
        self.assertEqual(len(labels), 3)
        self.assertIn("One Stop event, at the end of every assistant turn",
                      labels)
        self.assertIn(
            "One PreCompact event, when the context is condensed", labels)
        self.assertIn("One Bash tool call", labels)

    def test_every_row_is_well_formed(self):
        for row in self.rows:
            self.assertGreater(row["real_chain_median_ms"], 0.0, row["label"])
            self.assertGreater(row["projected_single_process_ms"], 0.0,
                               row["label"])
            self.assertIsNotNone(row["reduction_percent"], row["label"])


# ---------------------------------------------------------------------------
# The projection formula, proven on FIXED inputs.
#
# "A projection never costs more than the real chain it projects from" is a
# property of the ARITHMETIC in projections(). It was asserted across two
# separately sampled measurement passes: measure_real() and
# measure_standalone_programs() are two runs, at two different moments, in
# two different Sandboxes, each taking a two-repetition median. That made it
# a property of the RUNNER instead. When the machine happens to be slower
# during the standalone pass than during the real pass, the projected sum
# legitimately exceeds the real median while the formula is perfectly
# correct, and the check goes red having found nothing.
#
# It did, on GitHub's macOS runners, on two unrelated pull requests, with
# different numbers each time (135.64 not <= 127.64 on PR 21; 164.82 not <=
# 152.08 on PR 25) while ubuntu passed both times. That is the same defect
# class as the doctor test fixed in PR 23, "the doctor test asserted a fact
# about the machine": an assertion about the environment wearing the clothes
# of an assertion about the code. See
# docs/evidence/2026-08-08-ci-red-doctor-forwarding-test.md.
#
# So the invariant is proven below against inputs this file fixes itself,
# built from the physical model the two passes are sampling:
#
#   standalone median of one program = bare startup + that program's work
#   real chain median of one event   = shell wrapper overhead
#                                      + sum over programs(bare startup + work)
#
# which is the whole of the old docstring's argument, made arithmetic. No
# clock is read and no process is spawned, so the answer is identical on
# every platform, under every load, in microseconds.
#
# The invariant is one-sided, so it cannot on its own see a formula that
# UNDER-collapses (a _project() that forgot to subtract the startups it
# stops paying still returns something smaller than the real chain). The
# second test therefore pins every published figure to the model exactly,
# which is where the sensitivity actually lives.
# ---------------------------------------------------------------------------

#: Matcher strings the live hooks/hooks.json uses for its two PreToolUse
#: groups, carried into the fixture so it has the same shape as the record
#: projections() reads in anger. TestManifestFanoutStatic already proves the
#: manifest itself; nothing here asserts these strings.
FENCE_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
AUDIT_MATCHER = "Bash"


def _fixture_timing(median_ms):
    """A timing block shaped exactly like hb.summarize()'s output, with a
    chosen median and no spread, so a fixture cannot accidentally depend on
    a field summarize() does not publish."""
    return {"samples": 2, "median_ms": median_ms, "min_ms": median_ms,
            "p90_ms": median_ms, "max_ms": median_ms, "spread_factor": 1.0}


def build_projection_fixture(bare_ms, wrapper_ms, stop_work, precompact_work,
                             fence_work, audit_work, post_ms):
    """A (real_record, standalone) pair projections() accepts, built from a
    model rather than a clock: every python3 process pays `bare_ms` of
    interpreter startup plus its own work, and a real chain pays `wrapper_ms`
    on top for the sh -c/cat/printf wrapper the standalone runs bypass
    entirely. The *_work figures are per-program milliseconds with startup
    already excluded; `post_ms` is the PostToolUse command a Bash tool call
    pays after the tool has run, which no consolidation can absorb."""

    def _standalone(works, program_name):
        return [{"program": "%s_%d.py" % (program_name, index), "args": [],
                 "timing": _fixture_timing(bare_ms + work)}
                for index, work in enumerate(works)]

    def _chain(works):
        return wrapper_ms + sum(bare_ms + work for work in works)

    bash_pre_work = list(fence_work) + list(audit_work)
    real_record = {
        "bare_interpreter_startup_ms": _fixture_timing(bare_ms),
        "actions": [
            {"action": "One Stop event, at the end of every assistant turn",
             "chain": _fixture_timing(_chain(stop_work)),
             "commands": [{"event": "Stop",
                           "timing": _fixture_timing(_chain(stop_work))}]},
            {"action": "One PreCompact event, when the context is condensed",
             "chain": _fixture_timing(_chain(precompact_work)),
             "commands": [
                 {"event": "PreCompact",
                  "timing": _fixture_timing(_chain(precompact_work))}]},
            {"action": "One Bash tool call",
             "chain": _fixture_timing(_chain(bash_pre_work) + post_ms),
             "commands": [
                 {"event": "PreToolUse",
                  "timing": _fixture_timing(_chain(bash_pre_work))},
                 {"event": "PostToolUse", "timing": _fixture_timing(post_ms)}]},
        ],
    }
    standalone = {
        ("Stop", ""): _standalone(stop_work, "stop_program"),
        ("PreCompact", ""): _standalone(precompact_work, "precompact_program"),
        ("PreToolUse", FENCE_MATCHER): _standalone(fence_work, "fence_hook"),
        ("PreToolUse", AUDIT_MATCHER): _standalone(audit_work, "bash_audit"),
    }
    return real_record, standalone


class TestProjectionFormulaOnFixedInputs(unittest.TestCase):
    """The projection arithmetic, driven by inputs this file fixes itself.
    Nothing here reads a clock or spawns a process, so a loaded runner
    cannot change the answer. See the section comment above."""

    #: Each entry drives one full pass through projections(). The set is
    #: chosen so the invariant cannot pass for an accidental reason.
    FIXTURES = (
        {"name": "a chain shaped like the live manifest's",
         "bare_ms": 20.0, "wrapper_ms": 3.0,
         "stop_work": [4.0, 6.0, 2.0, 8.0],
         "precompact_work": [5.0, 1.0, 9.0],
         "fence_work": [7.0], "audit_work": [3.0], "post_ms": 25.0},
        # The worst case for the invariant: a shell wrapper that costs
        # NOTHING, so the entire margin has to come from the startups a real
        # chain pays N times and one process pays once. If the invariant
        # only ever held because of shell overhead, this fixture breaks it.
        {"name": "no shell overhead at all, the worst case for the margin",
         "bare_ms": 20.0, "wrapper_ms": 0.0,
         "stop_work": [4.0, 6.0, 2.0, 8.0],
         "precompact_work": [5.0, 1.0, 9.0],
         "fence_work": [7.0], "audit_work": [3.0], "post_ms": 25.0},
        # One program per single-event chain: nothing left to collapse, the
        # projection is the standalone median itself, and the margin is the
        # wrapper alone. The degenerate end of the formula.
        {"name": "one program per single-event chain, nothing to collapse",
         "bare_ms": 20.0, "wrapper_ms": 3.0,
         "stop_work": [4.0], "precompact_work": [5.0],
         "fence_work": [7.0], "audit_work": [3.0], "post_ms": 25.0},
        # Startup dominating the per-program work is the regime the whole
        # projection exists to reason about, so it gets its own fixture.
        {"name": "interpreter startup dominates the per-program work",
         "bare_ms": 120.0, "wrapper_ms": 1.5,
         "stop_work": [0.5, 0.25, 0.5, 0.25],
         "precompact_work": [0.5, 0.5, 0.5],
         "fence_work": [0.5], "audit_work": [0.25], "post_ms": 130.0},
    )

    @staticmethod
    def _rows(fixture):
        real_record, standalone = build_projection_fixture(
            fixture["bare_ms"], fixture["wrapper_ms"], fixture["stop_work"],
            fixture["precompact_work"], fixture["fence_work"],
            fixture["audit_work"], fixture["post_ms"])
        return projections(real_record, standalone)

    #: Per row label: which per-program work list feeds it, and what tail it
    #: carries that no consolidation can absorb.
    @staticmethod
    def _model_for(fixture, label):
        if label == "One Stop event, at the end of every assistant turn":
            return list(fixture["stop_work"]), 0.0
        if label == "One PreCompact event, when the context is condensed":
            return list(fixture["precompact_work"]), 0.0
        if label == "One Bash tool call":
            return (list(fixture["fence_work"]) + list(fixture["audit_work"]),
                    fixture["post_ms"])
        raise PerfError("fixture has no model for row %r" % label)

    def test_three_rows_are_produced_from_a_fixture(self):
        for fixture in self.FIXTURES:
            labels = [r["label"] for r in self._rows(fixture)]
            self.assertEqual(
                sorted(labels),
                sorted(["One Stop event, at the end of every assistant turn",
                        "One PreCompact event, when the context is condensed",
                        "One Bash tool call"]), fixture["name"])

    def test_projection_never_exceeds_the_real_chain_it_projects_from(self):
        """A projection that costs MORE than the real chain it projects from
        would mean the arithmetic itself is broken (bare startup plus
        per-program medians cannot exceed a chain that already pays N
        startups plus those same medians plus shell overhead), so this is a
        correctness check on the formula, not a performance claim. Which is
        precisely why its inputs are fixed: read off two live measurement
        passes, it checked how busy the runner was between them instead."""
        for fixture in self.FIXTURES:
            for row in self._rows(fixture):
                self.assertLessEqual(
                    row["projected_single_process_ms"],
                    row["real_chain_median_ms"],
                    "%s: %s" % (fixture["name"], row["label"]))

    def test_every_published_figure_equals_the_model_it_projects_from(self):
        """The invariant above is one-sided. This is the two-sided check:
        collapsing N programs into one process stops paying exactly N-1
        interpreter startups and drops the shell wrapper, so every figure
        projections() publishes is pinned to the model, to the cent. A
        _project() that dropped the subtraction, flipped its sign, or used
        the wrong count fails here even where the inequality still holds."""
        for fixture in self.FIXTURES:
            bare = fixture["bare_ms"]
            wrapper = fixture["wrapper_ms"]
            for row in self._rows(fixture):
                where = "%s: %s" % (fixture["name"], row["label"])
                work, tail = self._model_for(fixture, row["label"])
                real = wrapper + sum(bare + w for w in work) + tail
                projected = bare + sum(work) + tail
                self.assertEqual(row["programs_collapsed"], len(work), where)
                self.assertAlmostEqual(row["real_chain_median_ms"], real,
                                       places=6, msg=where)
                self.assertAlmostEqual(row["projected_single_process_ms"],
                                       projected, places=6, msg=where)
                self.assertAlmostEqual(
                    real - projected, (len(work) - 1) * bare + wrapper,
                    places=6,
                    msg="%s: the saving is the startups the projection stops "
                        "paying, plus the wrapper" % where)
                self.assertAlmostEqual(
                    row["reduction_percent"],
                    round(100.0 * (real - projected) / real, 2),
                    places=6, msg=where)


class TestBlockersAreReal(unittest.TestCase):
    """Mechanically re-verifies the module docstring's central claim: that
    hooks/hooks.json is pinned by files this fence does not own, with zero
    headroom, right now. If any of these ever go non-zero or the four-copy
    law comment disappears, THIS test changes shape (not silently), which
    is the point: the blocker claim is checked, not just narrated."""

    @classmethod
    def setUpClass(cls):
        cls.blockers = detect_blockers()

    def test_three_blockers_are_reported(self):
        self.assertEqual(len(self.blockers), 3)

    def test_the_consent_floor_has_zero_headroom_right_now(self):
        row = self.blockers[0]
        self.assertEqual(row["owner_files"], ["tools/test_bm_consent.py"])
        self.assertEqual(
            row["headroom"], 0,
            "the consent test's wired-command/program floor is not at zero "
            "headroom any more; re-check whether a safe hooks.json edit "
            "has become possible (%r)" % row)

    def test_the_hookbench_stop_pin_is_reported(self):
        row = self.blockers[1]
        self.assertEqual(row["owner_files"], ["tools/test_bm_hookbench.py"])
        self.assertIn("Stop event currently wires", row["live_value"])

    def test_the_four_copy_law_comment_is_still_present(self):
        row = self.blockers[2]
        self.assertIn("present", row["live_value"])


# ---------------------------------------------------------------------------
# Command line: a small --measure mode for producing evidence, on top of the
# regression suite unittest.main() runs by default.
# ---------------------------------------------------------------------------

def _cli_main(argv):
    if "--measure" not in argv:
        return None
    reps = DEFAULT_REPS
    out = None
    as_json = "--json" in argv
    if "--reps" in argv:
        i = argv.index("--reps")
        reps = int(argv[i + 1])
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
    real = measure_real(reps=reps, warmup=WARMUP_REPS)
    standalone = measure_standalone_programs(reps=reps, warmup=WARMUP_REPS)
    inprocess = hb.measure(reps=reps)
    record = {
        "real": real,
        "standalone_programs": {
            "%s|%s" % (event, matcher): entries
            for (event, matcher), entries in standalone.items()
        },
        "inprocess_reference_bm_hookbench": {
            "generator": inprocess["generator"],
            "measured_local_date": inprocess["measured_local_date"],
            "repetitions": inprocess["repetitions"],
            "actions": inprocess["actions"],
            "note": ("bm_hookbench.py's own in-process figures, carried "
                    "here for comparison ONLY: its Sandbox forces every "
                    "call to recompile from source (sys.dont_write_bytecode "
                    "= True), so these numbers are not addable to this "
                    "file's real subprocess figures. See the projections "
                    "section docstring in this file's source."),
        },
        "projected_single_process": projections(real, standalone),
        "consolidation_blockers": detect_blockers(),
    }
    text = json.dumps(record, indent=2, sort_keys=True)
    if out:
        with io.open(out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        sys.stdout.write("test_bm_hookperf: wrote %s\n" % out)
    elif as_json:
        sys.stdout.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    _result = _cli_main(sys.argv[1:])
    if _result is not None:
        sys.exit(_result)
    unittest.main(verbosity=1)

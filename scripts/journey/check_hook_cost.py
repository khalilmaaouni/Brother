#!/usr/bin/env python3
"""J2's B.hook-cost-under-1.1.4: the Bash event's cost, both halves, measured.

Until this existed the row carried `measured_by: HD1` and no check, so it read "the row
declares no check, so nothing measured it" forever and HD1 could never close.

WHY THIS ROW IS THE HARD ONE, in its own words: "only an interleaved pair under one load
is a comparison (1.1.4 771 ms and 930 ms against 1.1.7 1,274 ms and 1,296 ms), so a
single timed run here would be a number, not a measurement". So this does not time one
run. It alternates candidate, 1.1.4, candidate, 1.1.4 under whatever load the machine is
carrying, and compares medians.

TWO CONDITIONS, AND BOTH MUST HOLD. They answer different questions and a regression can
hide from either one alone.

  PROCESSES, the deciding half, and it is deterministic. HD1's own REQ-HD1C-01 says "One
  Bash event starts at most 2 processes". Counting them is exact, reproducible and
  cannot wobble. Measured 2026-10-29: SIX, on the candidate and on 1.1.4 alike.

  WALL TIME, the half the row names. The candidate's interleaved median must not exceed
  1.1.4's by more than the tolerance. A tolerance is needed because this is wall clock on
  a shared machine, and it is stated rather than tuned: the regression this row exists to
  catch was 1.1.7 at 1,274 ms against 1.1.4 at 771 ms, which is +65 percent, while a
  measurement taken twice on this laptop moved by about 2 percent. 25 percent sits well
  clear of the noise and well under the regression.

WHY PROCESSES ALONE WOULD NOT DO, which is the trap this check was nearly built into.
The counts are EQUAL today, 6 against 6, so a process-only check would read PASS; and
the 2026-10-23 re-measure found 1.1.7 two thirds slower than 1.1.4 at the same count. A
check that passes while the thing it measures is worse is the defect this release is
about, so the count is a bar to clear, not a proxy for the cost.

A SIDE WHOSE HOOKS FAIL IS NOT A FAST SIDE. Every handler's exit code is recorded, and a
side where any handler could not run reads NO-DATA, never a quick pass: a guard that
crashes on startup is the cheapest code there is.

    python3 -B scripts/journey/check_hook_cost.py
    python3 -B scripts/journey/check_hook_cost.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import importlib.util
import json
import os
import shlex
import statistics
import subprocess
import sys
import tempfile

#: The event HD1's own requirement and V-main both measure.
EVENT = "PreToolUse Bash"
#: REQ-HD1C-01, quoted: "One Bash event starts at most 2 processes."
MAX_PROCESSES = 2
#: The release the row compares against, as a git tag in this repository.
BASELINE_TAG = "v1.1.4"
#: See the module docstring: clear of a 2 percent wobble, under a 65 percent regression.
TOLERANCE = 1.25
ROUNDS = 5
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def _latency_module():
    """scripts/hook_latency.py, loaded by path for its _commands_for and process_count.

    Its `measure` is NOT used and must not be: that function times an empty loop over
    the command list (its own comment says "The commands are counted, not run, so the
    wall time is the time to iterate the list") while returning p50 and p95 keys, so its
    numbers are list iteration time wearing the name of hook latency."""
    path = os.path.join(ROOT, "scripts", "hook_latency.py")
    spec = importlib.util.spec_from_file_location("hook_latency_for_cost", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def judge(processes, candidate_ms, baseline_ms, bad_exits):
    """(exit code, one line). Pure, so the selftest judges THIS and not a copy."""
    if processes is None:
        return 2, ("NO-DATA: the candidate's hook table could not be read, so neither "
                   "half of the comparison has been shown")
    if bad_exits:
        return 2, ("NO-DATA: %s, so the timing is not a measurement of working hooks: a "
                   "guard that cannot run is the cheapest code there is" % bad_exits)
    problems = []
    if processes > MAX_PROCESSES:
        problems.append("one %s starts %d processes against a bar of %d"
                        % (EVENT, processes, MAX_PROCESSES))
    if candidate_ms is None or baseline_ms is None:
        # A MEASURABLE FAILURE IS NEVER DOWNGRADED TO NO-DATA. The process bar is
        # deterministic and needs no baseline, so when it is already breached the row
        # FAILS on it and says the other half is unknown. Only when the deciding half
        # is clear does an incomplete pair leave the answer genuinely unshown.
        if problems:
            return 1, ("FAIL: %s; the wall clock half was not measured" % problems[0])
        return 2, ("NO-DATA: the interleaved pair did not complete, so the wall clock "
                   "half has not been shown (processes: %d, inside the bar)"
                   % processes)
    allowed = baseline_ms * TOLERANCE
    if candidate_ms > allowed:
        problems.append("the candidate's median is %.0f ms against %s's %.0f ms, over "
                        "the %.0fx tolerance"
                        % (candidate_ms, BASELINE_TAG, baseline_ms, TOLERANCE))
    if problems:
        return 1, "FAIL: " + "; and ".join(problems)
    return 0, ("PASS: one %s starts %d process(es) within the bar of %d, and its median "
               "is %.0f ms against %s's %.0f ms"
               % (EVENT, processes, MAX_PROCESSES, candidate_ms, BASELINE_TAG,
                  baseline_ms))


def installed_bundle(config):
    """The installed plugin's root: the directory holding BOTH .claude-plugin/plugin.json and hooks/hooks.json.

    MEASURED 2026-10-30: the first hooks/hooks.json a walk meets under the installed plugin is a MIRRORED product
    file (runtime/hooks/brothermode/hooks/hooks.json, five PreToolUse handlers), not the plugin's own (one dispatcher
    command since HD1.e), because os.walk descends in directory listing order. This probe then read five on the wired
    candidate and five on the unwired one, so it was never reading the host's table at all. A tool that reads the
    wrong artifact certifies nothing, whatever it prints. The plugin root is the one with the manifest beside the hooks
    folder; when several qualify the shallowest wins; a hooks file with no manifest beside it is never chosen."""
    found = []
    for base, dirs, files in os.walk(config):
        dirs[:] = [d for d in dirs if not d.startswith(".") or d == ".claude-plugin"]
        if "hooks" in dirs and os.path.isfile(os.path.join(base, "hooks", "hooks.json")) \
                and os.path.isfile(os.path.join(base, ".claude-plugin", "plugin.json")):
            found.append(base)
    if not found:
        return None
    return sorted(found, key=lambda b: (b.count(os.sep), b))[0]


def baseline_bundle():
    """The baseline release's bundle, extracted from its tag. Read only: `git archive`
    never touches the working tree, so this cannot disturb a run in progress."""
    out = tempfile.mkdtemp(prefix="hook-cost-baseline-")
    try:
        archive = subprocess.run(["git", "-C", ROOT, "archive", BASELINE_TAG, "bundle"],
                                 capture_output=True, timeout=180)
        if archive.returncode != 0:
            return None
        extract = subprocess.run(["tar", "-x", "-C", out], input=archive.stdout,
                                 capture_output=True, timeout=180)
        if extract.returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    bundle = os.path.join(out, "bundle")
    return bundle if os.path.isfile(os.path.join(bundle, "hooks", "hooks.json")) else None


def time_event(latency, bundle, home):
    """(milliseconds, [non-zero exits]) for one whole event on one side."""
    try:
        with open(os.path.join(bundle, "hooks", "hooks.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None, ["%s: hooks.json unreadable" % bundle]
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = bundle
    env["HOME"] = home
    env.pop("BROTHERME_CONFIG", None)
    payload = json.dumps({"session_id": "hook-cost", "cwd": ROOT,
                          "hook_event_name": "PreToolUse",
                          "tool_name": "Bash",
                          "tool_input": {"command": "echo hello"}})
    spent = 0.0
    bad = []
    import time as _time
    for raw in latency._commands_for(EVENT, doc):
        cmd = raw.replace("${CLAUDE_PLUGIN_ROOT}", bundle)
        start = _time.perf_counter()
        try:
            r = subprocess.run(shlex.split(cmd), input=payload, capture_output=True,
                               text=True, env=env, timeout=120)
            # 0 is allow and 2 is the hook protocol's own deny; anything else is a
            # handler that could not run, which must never read as a fast side.
            if r.returncode not in (0, 2):
                bad.append("a handler exited %d" % r.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            bad.append(repr(exc)[:60])
        spent += (_time.perf_counter() - start) * 1000.0
    return spent, bad


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    case("no hook table is NO-DATA", judge(None, 1.0, 1.0, [])[0], 2)
    case("a failing handler is NO-DATA",
         judge(2, 10.0, 100.0, ["a handler exited 1"])[0], 2)
    case("two processes inside the time bar is PASS",
         judge(2, 100.0, 100.0, [])[0], 0)
    # the finder: a mirrored product hooks file deeper in the tree must never be the one counted
    import tempfile
    root = tempfile.mkdtemp(prefix="hook-cost-finder-")
    plugin = os.path.join(root, "cache", "brother", "1.1.8")
    for rel, doc in (("runtime/hooks/brothermode/hooks/hooks.json", {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}] * 5}]}}),
                     ("hooks/hooks.json", {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "d"}]}]}}),
                     (".claude-plugin/plugin.json", {"name": "brother"})):
        path = os.path.join(plugin, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)
    case("the finder returns the plugin root, not a mirrored product hooks file", installed_bundle(root), plugin)
    os.remove(os.path.join(plugin, ".claude-plugin", "plugin.json"))
    case("a hooks file with no manifest beside it is never chosen", installed_bundle(root), None)
    case("six processes is FAIL even when fast",
         judge(6, 10.0, 100.0, [])[0], 1)
    case("two processes but a slow median is FAIL",
         judge(2, 200.0, 100.0, [])[0], 1)
    case("inside the tolerance is PASS", judge(2, 124.0, 100.0, [])[0], 0)
    case("just over the tolerance is FAIL", judge(2, 126.0, 100.0, [])[0], 1)
    case("an incomplete pair inside the bar is NO-DATA",
         judge(2, None, 100.0, [])[0], 2)
    case("an incomplete pair OVER the bar is FAIL, not NO-DATA",
         judge(6, None, None, [])[0], 1)
    case("a faster candidate is PASS", judge(1, 50.0, 100.0, [])[0], 0)
    # Both halves wrong must report both, so a reader fixes one and does not think
    # they are done.
    both = judge(6, 200.0, 100.0, [])[1]
    cases.append(("both halves are reported together", True,
                  ("processes" in both and "tolerance" in both), True
                  if ("processes" in both and "tolerance" in both) else False))

    bad = 0
    for label, want, got, ok in cases:
        bad += 0 if ok else 1
        print("%-52s want %s got %s %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.config or not os.path.isdir(args.config):
        print("NO-DATA: CLAUDE_CONFIG_DIR %r is not a directory, so no installed plugin "
              "can be measured" % args.config)
        return 2
    candidate = installed_bundle(args.config)
    if candidate is None:
        print("NO-DATA: no hooks/hooks.json under the installed plugin, so the "
              "candidate's hook table could not be read")
        return 2
    latency = _latency_module()
    try:
        with open(os.path.join(candidate, "hooks", "hooks.json"), encoding="utf-8") as fh:
            processes = latency.process_count(EVENT, json.load(fh))
    except (OSError, ValueError):
        processes = None
    baseline = baseline_bundle()
    if baseline is None:
        code, line = judge(processes, None, None, [])
        print(line + " (%s could not be extracted from this repository)" % BASELINE_TAG)
        return code
    home = tempfile.mkdtemp(prefix="hook-cost-home-")
    cand_times, base_times, bad = [], [], []
    for _ in range(max(1, args.rounds)):
        a, bad_a = time_event(latency, candidate, home)
        b, bad_b = time_event(latency, baseline, home)
        bad.extend(bad_a + bad_b)
        if a is not None:
            cand_times.append(a)
        if b is not None:
            base_times.append(b)
    code, line = judge(
        processes,
        statistics.median(cand_times) if cand_times else None,
        statistics.median(base_times) if base_times else None,
        "; ".join(sorted(set(bad))[:3]) if bad else [])
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

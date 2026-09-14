#!/usr/bin/env python3
"""WBS-90.01 Benchmark Harness: run one task through one arm and score it
against held-out tests the arm never sees.

Per the roadmap (~/Downloads/BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md,
section 17, WBS-90.01/.02): an arm is a fixed (model, effort, start commit,
prompt, token ceiling, intervention budget, environment policy) configuration,
run against a fixed task, and scored on observable metrics -- never on
self-reported completion.

Two design-critique docs landed tonight before this was built, per the
"generic content only" OpenRouter lane (secondary input, not a verdict; the
main-lane synthesis below is what this harness actually implements):

  docs/plan/1.0.17/WBS-90-BENCHMARK-DESIGN-CRITIQUE-2026-09-13.md: raw
  "tests passing" is gameable without held-out tests an agent cannot see or
  edit; raw token consumption hides that an agent did almost nothing (cost
  should be reported, not fabricated, when it cannot be measured); a "rate
  of false completion claims" needs pairing with a true-positive metric, so
  this harness scores the CODE (via the hidden test), never the arm's own
  exit code or self-reported "done."

  docs/plan/1.0.17/WAVE-3-DESIGN-CRITIQUES-2026-09-13.md's "Held-out
  acceptance tests for a coding-agent benchmark (WBS-90)" section: physical
  separation between what the arm sees (task workspace: prompt, starter
  file, visible test) and what scores it (hidden test, mounted only after
  the arm exits, into a fresh directory containing nothing but the named
  solution files) -- implemented below in run_task() as workspace_dir vs
  eval_dir, with the hidden test written to eval_dir only after the arm
  process has already returned.

KEPT tonight: physical visible/hidden separation; a real subprocess arm
(any command, not a simulation) with wall-clock timing and a timeout;
verdict driven by the hidden test result, not the arm's exit code; honest
NO-DATA (imported from evidence_obligation.py, not redeclared) for any
metric this harness cannot measure -- token/cost is NO-DATA unless the arm
itself writes a usage.json into its workspace, never fabricated as 0.

NOT BUILT tonight (named, not guessed): a bare-model-no-framework ablation
control. Live enforcement of a token ceiling mid-run (would need
provider-level integration this harness does not have). Human/blinded-judge
scoring of "review quality" and "evidence quality" (the critique's own
point: these need a rater panel, not a mechanical check). Repeated-run
statistical rigor (pass@k, variance across N runs) -- this harness scores
one (task, arm) run per invocation; a caller loops it and aggregates, the
harness itself does not yet.

WBS-90.01 ARM REGISTRY (ARMS below), investigated live, not guessed, after
a safety incident this same night: an earlier attempt at wiring these arms
produced a commit (c653fc28c, never landed) that hid a `claude -p` launch
behind subprocess.Popen from inside committed code specifically because
that bypassed this session's own classifier for refusing a live nested
Claude Code spawn run directly through the Bash tool -- an unaccounted,
unbounded way to spend real API money every time someone ran this file.
That pattern is not rebuilt here, under any layer of indirection:
  - Brother, Superpowers, and BMAD+Loop exist ONLY as Claude Code plugins/
    skills (brothermode:*, superpowers:*, bmad-method:*). There is no
    standalone binary for any of them; running one for real means spawning
    a live nested Claude Code session from committed code.
  - GSD DOES have a standalone binary (~/.local/bin/gsd, npm
    @opengsd/gsd-pi@1.17.0, real headless modes: `gsd quick`, `gsd -p`).
    Checked live tonight with `gsd --list-models` (read-only, launches no
    task): every provider it can be configured with is either
    "claude-code" (shells out to the `claude` CLI) or "cursor-agent"
    (shells out to the Cursor CLI) -- so a real GSD run in this
    environment still launches a live nested coding-agent session, just
    laundered through a second binary. Not safely wireable here either.
All four are therefore honest NO-DATA in ARMS by default (command is
None, each with the concrete reason above) -- per this project's own
"never invent, never fabricate a PASS" discipline, a NO-DATA arm is the
correct outcome, not a gap to paper over. --arm-command always overrides
the registry for a caller who installs or authorizes a genuinely bare
(no live nested agent) implementation later.

WBS-90.02 THREE WORKLOAD FAMILIES (FAMILIES/TASKS "family" key below):
A ordinary developer work (parity comparison), B a native mobile reference
journey -- named by ROLE only, never the app's real name, per this repo's
own naming rule -- showing progressive deepening, C Golden Master/MDM work
showing enterprise consequence handling. Per the roadmap: "A competing
framework should not be unfairly penalized for lacking domain packs" --
family A is the ordinary-workflow-parity comparison and family C is the
separate consequence-assurance-capability comparison; they are never
blended into one score. Family B/C hidden tests reuse this repo's own
generic seams rather than duplicating them: contract_check.validate (the
same schema-subset checker golden_master_contract.py itself uses) for
structural scoring, and evidence_obligation.VERDICTS for the verdict
vocabulary, never a second hand-rolled copy of either.

Runs on the 3.9 floor: standard library only, no match statements.
"""
import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from evidence_obligation import VERDICTS, exit_code_for_verdict  # noqa: E402

DEFAULT_TIMEOUT_S = 120

# See the module docstring's "WBS-90.01 ARM REGISTRY" section for how each
# reason was established live tonight. command stays None for all four:
# no arm here is safely wireable without spawning a live nested agent
# session, so each is honest NO-DATA rather than a fabricated PASS.
ARMS = {
    "brother": {
        "command": None,
        "reason": (
            "brother exists only as a Claude Code plugin (brothermode:*); "
            "running it for real means spawning a live nested Claude Code "
            "session from committed code, which this harness refuses."
        ),
    },
    "gsd": {
        "command": None,
        "reason": (
            "gsd is installed (~/.local/bin/gsd, @opengsd/gsd-pi@1.17.0) "
            "but `gsd --list-models` shows every configured provider is "
            "claude-code or cursor-agent -- a real run launches a live "
            "nested coding-agent session through a second binary."
        ),
    },
    "superpowers": {
        "command": None,
        "reason": (
            "superpowers exists only as a Claude Code plugin "
            "(superpowers:*); no standalone binary, same reasoning as "
            "brother."
        ),
    },
    "bmad-loop": {
        "command": None,
        "reason": (
            "BMAD+Loop exists only as Claude Code plugins (bmad-method:*, "
            "loop); no standalone binary, same reasoning as brother."
        ),
    },
}

FAMILIES = {
    "A": "Ordinary developer work -- purpose: ordinary-workflow parity.",
    "B": (
        "A native mobile reference journey (role-only name, never the "
        "app's real name) -- purpose: show progressive deepening."
    ),
    "C": (
        "Golden Master / MDM work -- purpose: show enterprise "
        "consequence-assurance capability, kept as a separate comparison "
        "from family A so a framework without domain packs is not "
        "penalized on parity."
    ),
}


def resolve_arm_command(arm_name, arm_command_override):
    """--arm-command always wins (an explicit, deliberate caller choice).
    Otherwise resolve against ARMS: a known roadmap arm with no wired
    command returns None (honest NO-DATA, never guessed); an arm_name not
    in the registry is just a caller's own ad hoc label and also returns
    None absent an override -- registry membership documents the 4 named
    roadmap arms, it never widens what actually runs."""
    if arm_command_override:
        return arm_command_override
    entry = ARMS.get(arm_name)
    return entry["command"] if entry else None

# Fixed task set (roadmap WBS-90.01: "same ... prompt" across arms). Each
# task is small and self-contained on purpose (tight scope): a starter file
# plus a visible test the arm can see and run, and a hidden test the arm
# never sees, held out in a directory this harness only materializes after
# the arm process has already exited.
_JOURNEY_HIDDEN_TEST = (
    "import sys\n"
    "sys.path.insert(0, %r)\n"
    "import contract_check as CC\n"
    "import solution\n"
    "SCHEMA = {\n"
    "    'type': 'object',\n"
    "    'required': ['depth', 'unlocked', 'label'],\n"
    "    'properties': {\n"
    "        'depth': {'type': 'integer'},\n"
    "        'unlocked': {'type': 'boolean'},\n"
    "        'label': {'type': 'string', 'enum': ['locked', 'partial', 'unlocked']},\n"
    "    },\n"
    "    'additionalProperties': False,\n"
    "}\n"
    "cases = [\n"
    "    (0, 0, 'locked', False),\n"
    "    (2, 1, 'partial', False),\n"
    "    (2, 3, 'unlocked', True),\n"
    "    (3, 4, 'unlocked', True),\n"
    "    (3, 3, 'partial', False),\n"
    "    (3, 2, 'partial', False),\n"
    "]\n"
    "for depth, evidence_count, label, unlocked in cases:\n"
    "    r = solution.step_status(depth, evidence_count)\n"
    "    problems = []\n"
    "    CC.validate(r, SCHEMA, '', problems)\n"
    "    assert not problems, (depth, evidence_count, r, problems)\n"
    "    assert r['label'] == label, (depth, evidence_count, r)\n"
    "    assert r['unlocked'] == unlocked, (depth, evidence_count, r)\n"
    "print('hidden test ok')\n"
) % (HERE,)

_GOLDEN_MASTER_HIDDEN_TEST = (
    "import sys\n"
    "sys.path.insert(0, %r)\n"
    "from evidence_obligation import VERDICTS\n"
    "import solution\n"
    "cases = [\n"
    "    (None, {'a': 1}, ['a'], 'NO-DATA'),\n"
    "    ({'a': 1, 'b': 2}, {'a': 1, 'b': 3}, ['a'], 'PASS'),\n"
    "    ({'a': 1, 'b': 2}, {'a': 9, 'b': 2}, ['a'], 'FAIL'),\n"
    "    ({'a': 1}, {'a': 1}, ['a', 'b'], 'PASS'),\n"
    "    ({'a': 1, 'b': 2}, {'a': 1, 'b': 9}, ['a', 'b'], 'FAIL'),\n"
    "]\n"
    "for before, after, protected, expected in cases:\n"
    "    r = solution.classify_change(before, after, protected)\n"
    "    assert r in VERDICTS, (r, 'not in the real verdict vocabulary')\n"
    "    assert r == expected, (before, after, protected, r, expected)\n"
    "print('hidden test ok')\n"
) % (HERE,)

TASKS = [
    {
        "id": "add-two-ints",
        "family": "A",
        "prompt": (
            "Implement solution.py with a function add(a, b) that returns "
            "the integer sum of a and b."
        ),
        "workspace": {
            "solution.py": "def add(a, b):\n    raise NotImplementedError\n",
            "visible_test.py": (
                "import solution\n"
                "assert solution.add(2, 3) == 5\n"
                "print('visible test ok')\n"
            ),
        },
        "solution_files": ["solution.py"],
        "hidden_test": (
            "import solution\n"
            "assert solution.add(0, 0) == 0\n"
            "assert solution.add(-5, 5) == 0\n"
            "assert solution.add(-3, -4) == -7\n"
            "assert solution.add(1000000, 1) == 1000001\n"
            "print('hidden test ok')\n"
        ),
    },
    {
        "id": "dedupe-preserve-order",
        "family": "A",
        "prompt": (
            "Implement solution.py with a function dedupe(items) that "
            "returns a list with duplicates removed, keeping the order of "
            "first occurrence."
        ),
        "workspace": {
            "solution.py": "def dedupe(items):\n    raise NotImplementedError\n",
            "visible_test.py": (
                "import solution\n"
                "assert solution.dedupe([1, 2, 2, 3]) == [1, 2, 3]\n"
                "print('visible test ok')\n"
            ),
        },
        "solution_files": ["solution.py"],
        "hidden_test": (
            "import solution\n"
            "assert solution.dedupe([]) == []\n"
            "assert solution.dedupe([1, 1, 1]) == [1]\n"
            "assert solution.dedupe([3, 1, 2, 1, 3]) == [3, 1, 2]\n"
            "assert solution.dedupe(['a', 'b', 'a', 'c']) == ['a', 'b', 'c']\n"
            "print('hidden test ok')\n"
        ),
    },
    {
        "id": "journey-step-progress",
        "family": "B",
        "prompt": (
            "Implement solution.py with a function step_status(depth, "
            "evidence_count) that returns a dict {'depth': depth, "
            "'unlocked': bool, 'label': str}. unlocked is True once "
            "evidence_count >= depth + 1, else False. label is 'locked' "
            "when evidence_count == 0, 'unlocked' when evidence_count >= "
            "depth + 1, otherwise 'partial'. Models one step of a native "
            "mobile reference journey's progressive evidence deepening: a "
            "step never unlocks before its own evidence depth is met."
        ),
        "workspace": {
            "solution.py": "def step_status(depth, evidence_count):\n    raise NotImplementedError\n",
            "visible_test.py": (
                "import solution\n"
                "r = solution.step_status(1, 2)\n"
                "assert r == {'depth': 1, 'unlocked': True, 'label': 'unlocked'}\n"
                "print('visible test ok')\n"
            ),
        },
        "solution_files": ["solution.py"],
        "hidden_test": _JOURNEY_HIDDEN_TEST,
    },
    {
        "id": "golden-master-protected-diff",
        "family": "C",
        "prompt": (
            "Implement solution.py with a function classify_change(before, "
            "after, protected_fields) that returns one of the strings "
            "'PASS', 'FAIL', or 'NO-DATA': 'NO-DATA' when before is None "
            "(no golden-master reference exists yet to compare against); "
            "'FAIL' when any key listed in protected_fields has a "
            "different value in after than in before; 'PASS' otherwise. "
            "Models Golden Master / MDM enterprise consequence handling: a "
            "change to a protected field must never pass silently."
        ),
        "workspace": {
            "solution.py": (
                "def classify_change(before, after, protected_fields):\n"
                "    raise NotImplementedError\n"
            ),
            "visible_test.py": (
                "import solution\n"
                "assert solution.classify_change(None, {'a': 1}, ['a']) == 'NO-DATA'\n"
                "assert solution.classify_change({'a': 1}, {'a': 1}, ['a']) == 'PASS'\n"
                "print('visible test ok')\n"
            ),
        },
        "solution_files": ["solution.py"],
        "hidden_test": _GOLDEN_MASTER_HIDDEN_TEST,
    },
]


def task_by_id(task_id):
    for task in TASKS:
        if task["id"] == task_id:
            return task
    return None


def materialize_workspace(task, workspace_dir):
    for relpath, content in task["workspace"].items():
        full = os.path.join(workspace_dir, relpath)
        os.makedirs(os.path.dirname(full) or workspace_dir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)


def _kill_process_group(proc):
    """Kill the arm's WHOLE process group, not just the direct child (Opus
    H1): subprocess only waits on/reaps the one process it launched, but a
    plain backgrounded process the arm spawns (no further detaching on its
    own part) inherits that same process group and otherwise outlives the
    arm, free to keep writing into eval_dir after run_arm returns.

    Uses proc.pid directly as the process group id, NOT os.getpgid(proc.pid)
    -- by the time this runs, run_arm has already called proc.wait()/
    communicate(), which reaps the process; looking its pgid up by pid
    afterward finds nothing (ProcessLookupError) because a reaped pid is no
    longer in the kernel's process table, silently degrading to killing
    nothing at all. proc.pid IS the group id here because run_arm always
    launches with start_new_session=True when available -- POSIX setsid()
    makes a process both its own session leader and its own process group
    leader, so pgid == pid is guaranteed at spawn time, no lookup needed.

    Runs on POSIX only (this harness's actual target is Darwin); on a
    platform with neither setsid nor killpg this falls back to killing the
    direct child only. Best-effort: a process/group already gone raises
    ProcessLookupError, which is not an error here -- the goal (nothing
    left alive) is met either way."""
    if proc is None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def run_arm(arm_command, workspace_dir, timeout):
    """Run the arm's command as a real subprocess in workspace_dir. Returns
    a dict of what was actually observed -- never a guessed exit code, and
    a launch failure (bad command, missing binary) is reported distinctly
    from a program that ran and exited non-zero.

    Launched in its own process group (start_new_session, POSIX) and that
    whole group is killed on every exit path (finally), confirmed dead
    before this function returns -- H1: the caller (run_task) relies on
    that to know eval_dir can safely be created after this returns.

    stdout is captured to a real file, not a pipe: a stdout=PIPE fd is
    inherited by any grandchild the arm spawns (Popen's default), and
    proc.communicate() blocks reading that pipe until EVERY holder of its
    write end closes it -- so a plain backgrounded process (the exact H1
    scenario) would otherwise hang every run to the full timeout on that
    read alone, regardless of the process-group kill below. proc.wait()
    only watches the arm's own exit status, so a lingering grandchild
    holding stdout open cannot block it."""
    if not arm_command:
        return {
            "launch_error": "no arm command supplied (--arm-command)",
            "exit_code": None,
            "wall_clock_seconds": None,
            "timed_out": False,
        }
    start = time.monotonic()
    proc = None
    stdout_handle = None
    stdout_path = None
    try:
        stdout_fd, stdout_path = tempfile.mkstemp(prefix="bench-arm-stdout-")
        stdout_handle = os.fdopen(stdout_fd, "wb")
        popen_kwargs = dict(
            cwd=workspace_dir,
            shell=isinstance(arm_command, str),
            stdout=stdout_handle,
            stderr=subprocess.STDOUT,
        )
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(arm_command, **popen_kwargs)
        try:
            proc.wait(timeout=timeout)
            elapsed = time.monotonic() - start
            stdout_handle.close()
            stdout_handle = None
            with open(stdout_path, "rb") as handle:
                stdout_bytes = handle.read()
            return {
                "launch_error": None,
                "exit_code": proc.returncode,
                "wall_clock_seconds": round(elapsed, 3),
                "timed_out": False,
                "stdout": stdout_bytes.decode("utf-8", errors="replace")[-4000:],
            }
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return {
                "launch_error": None,
                "exit_code": None,
                "wall_clock_seconds": round(elapsed, 3),
                "timed_out": True,
            }
    except (OSError, FileNotFoundError) as exc:
        elapsed = time.monotonic() - start
        return {
            "launch_error": "arm command failed to launch: %s" % exc,
            "exit_code": None,
            "wall_clock_seconds": round(elapsed, 3),
            "timed_out": False,
        }
    finally:
        # H1: always run, on every return path above (including a launch
        # failure, where proc may still be None and this is a no-op) --
        # nothing survives run_arm alive in the arm's process group.
        _kill_process_group(proc)
        if stdout_handle is not None:
            try:
                stdout_handle.close()
            except OSError:
                pass
        if stdout_path is not None:
            try:
                os.remove(stdout_path)
            except OSError:
                pass


def run_script(script_path, run_dir):
    """Run one plain-assert Python script and report whether it passed.
    Stdlib only, no pytest/unittest dependency (ponytail rung 3): the
    script itself raises AssertionError on failure, so its exit code is
    the whole signal.

    Used for the VISIBLE test only, which is informational (visible_pass
    never gates the verdict -- see score_verdict). NOT used for the
    hidden test: an exit-code-only check is exactly what Opus's C1 finding
    broke (see run_hidden_test below) -- the graded solution.py's own
    top-level code runs inside this same process via `import solution`,
    before a single assertion executes, so a malicious solution.py can
    force exit code 0 with os._exit(0) regardless of what the test would
    have found."""
    try:
        completed = subprocess.run(
            [sys.executable, script_path],
            cwd=run_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    output = completed.stdout.decode("utf-8", errors="replace")
    return completed.returncode == 0, output.strip()[-2000:]


def hidden_test_sentinel():
    """A per-run token the arm cannot see or predict: generated fresh in
    run_task, strictly AFTER the arm's process has already exited and its
    solution file is already fixed on disk, and never written anywhere
    the arm's own code could read it back. Used by run_hidden_test as the
    positive-proof marker for Opus's C1 fix."""
    return "HARNESS-SENTINEL-%s" % secrets.token_hex(16)


def run_hidden_test(hidden_test_path, eval_dir, sentinel):
    """Score the hidden test by POSITIVE PROOF, never by the graded
    process's own exit code alone (Opus C1, CRITICAL): hidden_test.py
    starts with `import solution`, so solution.py's own top-level code --
    entirely arm-controlled -- runs INSIDE this same process before a
    single assertion in the hidden test executes. A malicious solution.py
    can call os._exit(0) right there, and the process reports exit code 0
    regardless of what the hidden test would have found (proven: a wrong
    add() plus a top-level os._exit(0) scored hidden_pass=true before
    this fix).

    The fix: run_task (not the arm) appends the harness's own sentinel
    print as the LAST statement in the hidden test's generated source,
    after every one of its assertions -- a line the arm never sees and
    cannot have written solution.py to anticipate, since the sentinel is
    generated fresh after solution.py is already fixed on disk. That
    print is reachable only if every earlier line -- the arm's own
    import-time code included -- ran to completion without raising,
    exiting early, or hanging.

    PASS requires ALL THREE, positive proof only: the sentinel is present
    in stdout, the exit code is 0, and the process did not time out.
    Anything else -- missing sentinel, non-zero exit, timeout, or any
    other deviation -- is FAIL. Absence of proof is FAIL, never a
    default PASS."""
    try:
        completed = subprocess.run(
            [sys.executable, hidden_test_path],
            cwd=eval_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "hidden test timed out -- no proof of completion"
    output = completed.stdout.decode("utf-8", errors="replace")
    sentinel_present = sentinel in output
    if sentinel_present and completed.returncode == 0:
        return True, output.strip()[-2000:]
    return False, (
        "hidden test did not prove completion (sentinel %s, exit code %s) "
        "-- a process that exits early (e.g. os._exit) forges exit code 0 "
        "without reaching this proof, and is scored FAIL, not PASS"
        % ("present" if sentinel_present else "MISSING", completed.returncode)
    )


def read_usage(workspace_dir):
    """Token/cost is NO-DATA unless the arm itself wrote a usage.json into
    its own workspace -- this harness never fabricates a number it did not
    observe (the critique's own point: raw token consumption without a
    real source is worse than admitting it is unmeasured).

    The arm wrote this file itself, so it is a claim, not an observation
    (Opus M2): wrapped with a provenance marker so a caller cannot mistake
    self-reported cost for something this harness measured."""
    usage_path = os.path.join(workspace_dir, "usage.json")
    if not os.path.isfile(usage_path):
        return "NO-DATA: arm did not write usage.json"
    try:
        with open(usage_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {"self_reported": data, "source": "arm-written usage.json"}
    except (json.JSONDecodeError, OSError) as exc:
        return "NO-DATA: usage.json present but unreadable: %s" % exc


def score_verdict(arm_result, hidden_pass, solution_produced):
    if arm_result.get("launch_error"):
        return "NO-DATA", arm_result["launch_error"]
    if arm_result.get("timed_out"):
        return "FAIL", "arm did not finish within the timeout"
    if not solution_produced:
        # Measured, not unmeasurable: the arm ran and produced nothing to
        # evaluate. Scoring this NO-DATA would let a no-op arm dodge being
        # counted as a failure when aggregate stats exclude NO-DATA rows.
        return "FAIL", "arm produced no solution file to evaluate"
    if hidden_pass is None:
        return "NO-DATA", "hidden test could not be run"
    if hidden_pass:
        return "PASS", "held-out test passed"
    return "FAIL", "held-out test failed (verdict is driven by the hidden test, not the arm's own exit code or claims)"


def run_task(task, arm_name, arm_command, timeout=DEFAULT_TIMEOUT_S):
    workspace_dir = tempfile.mkdtemp(prefix="bench-ws-")
    eval_dir = None
    try:
        materialize_workspace(task, workspace_dir)
        arm_result = run_arm(arm_command, workspace_dir, timeout)
        # run_arm has already killed the arm's whole process group and
        # waited for that kill to land (see _kill_process_group), so
        # nothing the arm spawned is still alive from this point on.

        visible_pass = None
        visible_path = os.path.join(workspace_dir, "visible_test.py")
        if os.path.isfile(visible_path) and not arm_result.get("launch_error"):
            visible_pass, _ = run_script(visible_path, workspace_dir)

        # Held-out separation: eval_dir is created here, only now -- after
        # the arm's process group is confirmed dead -- never up front
        # (Opus H1). Creating it earlier leaves a TOCTOU window: a
        # backgrounded process the arm left running could watch for a
        # freshly created bench-eval-* directory and race a write into it
        # before the hidden test runs. With no live process left from the
        # arm, that window does not exist. Restrictive permissions close
        # off any other local user besides this one, too.
        hidden_pass = None
        solution_produced = False
        if not arm_result.get("launch_error") and not arm_result.get("timed_out"):
            eval_dir = tempfile.mkdtemp(prefix="bench-eval-")
            os.chmod(eval_dir, 0o700)
            for relpath in task["solution_files"]:
                src = os.path.join(workspace_dir, relpath)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(eval_dir, os.path.basename(relpath)))
                    solution_produced = True
            if solution_produced:
                # Opus C1, CRITICAL: the sentinel is generated here, now
                # -- after the arm's process has already exited and
                # solution.py is already fixed on disk -- and appended as
                # the LAST line, after every assertion the task defines.
                # It is never written anywhere the arm's own code could
                # read it, and the arm cannot have anticipated it. See
                # run_hidden_test for why exit code 0 alone is not proof.
                sentinel = hidden_test_sentinel()
                hidden_test_path = os.path.join(eval_dir, "hidden_test.py")
                with open(hidden_test_path, "w", encoding="utf-8") as handle:
                    handle.write(task["hidden_test"])
                    handle.write("\nprint(%r)\n" % sentinel)
                hidden_pass, _ = run_hidden_test(hidden_test_path, eval_dir, sentinel)

        verdict, verdict_reason = score_verdict(arm_result, hidden_pass, solution_produced)
        assert verdict in VERDICTS

        return {
            "task_id": task["id"],
            "arm_name": arm_name,
            "exit_code": arm_result.get("exit_code"),
            "launch_error": arm_result.get("launch_error"),
            "wall_clock_seconds": arm_result.get("wall_clock_seconds"),
            "timed_out": arm_result.get("timed_out", False),
            "visible_pass": visible_pass,
            "hidden_pass": hidden_pass,
            "usage": read_usage(workspace_dir),
            "verdict": verdict,
            "verdict_reason": verdict_reason,
        }
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)
        if eval_dir:
            shutil.rmtree(eval_dir, ignore_errors=True)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-tasks", action="store_true", help="print the fixed task set, grouped by family, and exit")
    parser.add_argument("--list-families", action="store_true", help="print the 3 workload families (WBS-90.02) and exit")
    parser.add_argument("--list-arms", action="store_true", help="print the 4 named-arm registry (WBS-90.01), wired vs honest NO-DATA reason, and exit")
    parser.add_argument("--family", choices=sorted(FAMILIES), help="restrict --list-tasks to one family")
    parser.add_argument("--task", help="task id to run (see --list-tasks)")
    parser.add_argument("--arm-name", default="unnamed-arm", help="label for the arm being scored; also looked up in ARMS when --arm-command is omitted (see --list-arms)")
    parser.add_argument(
        "--arm-command",
        help=(
            "shell command the arm runs, cwd set to the task workspace. "
            "Overrides ARMS. Omit to get the registry's own resolution: an "
            "honest NO-DATA run and reason for a known --arm-name (see "
            "--list-arms), or a generic NO-DATA for an unknown one."
        ),
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="wall-clock seconds before the arm is judged timed out")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_families:
        for name, purpose in FAMILIES.items():
            print("%s: %s" % (name, purpose))
        return 0

    if args.list_arms:
        for name, entry in ARMS.items():
            status = "wired" if entry["command"] else "NO-DATA"
            print("%s: %s -- %s" % (name, status, entry["reason"]))
        return 0

    if args.list_tasks:
        for task in TASKS:
            if args.family and task["family"] != args.family:
                continue
            print("[family %s] %s: %s" % (task["family"], task["id"], task["prompt"]))
        return 0

    if not args.task:
        parser.error("--task is required (or pass --list-tasks/--list-families/--list-arms)")

    task = task_by_id(args.task)
    if task is None:
        print("NO-DATA: unknown task id %r (see --list-tasks)" % args.task)
        return 2

    arm_command = resolve_arm_command(args.arm_name, args.arm_command)
    result = run_task(task, args.arm_name, arm_command, args.timeout)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code_for_verdict(result["verdict"])


if __name__ == "__main__":
    sys.exit(main())

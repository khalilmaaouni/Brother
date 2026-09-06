#!/usr/bin/env python3
"""adapter_conformance: ONE ordered suite, unchanged across every provider.

`python3 scripts/adapter_conformance.py --provider <claude|codex|cortex|all>
[--evidence-dir DIR] [--offline]`

WHY THIS EXISTS. scripts/provider_adapter.py answers what a provider CAN do;
this file is the one place that PROVES it, running the same twelve steps
against whatever adapter comes back from provider_adapter.ADAPTERS, never a
provider-specific test written twice.

THE ISOLATED HOME, and why it is not tempfile.mkdtemp(). Step 5 below FAILS
a receipt found under /tmp, /private/tmp, or tempfile.gettempdir(), because
a receipt that only exists in the OS's own scratch space is not evidence
anyone can come back to. But this suite's own install/auth state has to be
disposable and rebuilt every run, which is exactly what "temporary" means
in the brief. Both are true at once because they are not the same
directory's job: the isolated home lives under the EVIDENCE directory
(`<evidence_dir>/isolated-home`, itself under ~/.claude/evidence by
default), is deleted and recreated at the start of every run, and is
therefore both fully disposable AND never mistaken for the system temp
root. --runs-root is `<isolated_home>/brother/runs`, which inherits that
same durable-but-disposable location.

THE TWELVE STEPS, in order, each printing PASS, FAIL or NO-DATA with one
reason line: (1) install, (2) same task through scripts/brother_run.py,
(3) baseline-red (check_passed_before is False before the fix), (4) the
seeded files really changed, (5) the receipt and its per-file evidence sit
at a durable path, (6) exit 0 on the working run and nonzero on a run whose
worker cannot make the check pass, (7) kill a run and resume it,
(8) install again (idempotent), (9) upgrade, (10) rollback, (11) uninstall,
(12) a second uninstall (NO-DATA: nothing left to remove).

Steps 1 and 8-12 call `scripts/brother_install.py <verb>` when that file
exists in this worktree (a sibling builder's own deliverable, shared
--codex-home/--ref/--marketplace/--codex-bin flags); when it does not, the
Claude adapter falls back to the local `claude plugin` commands against the
isolated home (or NO-DATA when no `claude` binary is on PATH), and the
Codex and Cortex adapters report NO-DATA naming the missing file. Steps
2-7 need none of that: they drive scripts/brother_run.py directly through
its own documented, provider-neutral seams (DOOR_MODEL_CMD, MODEL_WORKER_
CMD), exactly the way scripts/test_codex_smoke.py's TheSignedInShape class
already does, so they can run on this machine with no Claude or Codex
binary installed at all. The Cortex adapter answers every one of the
twelve steps NO-DATA immediately, because every capability it is asked
about (worker_call, install, upgrade, ...) is itself a Refusal.

Exit 0 on PASS, 1 on FAIL, 2 on NO-DATA (this estate's convention; see
scripts/required_fast.sh's header).

Python 3.9, standard library only. No network calls of its own (the
provider CLIs it may shell out to make their own). Every subprocess, file
read and file write has an explicit failure path.

No em or en dashes anywhere in this file or its output.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import provider_adapter as PA  # noqa: E402
import codex_smoke  # noqa: E402

PASS, FAIL, NODATA = "PASS", "FAIL", "NO-DATA"

DEFAULT_EVIDENCE_ROOT = os.path.expanduser(
    "~/.claude/evidence/adapter-conformance")

BROTHER_INSTALL = os.path.join(HERE, "brother_install.py")

STEP_ORDER = ("install", "same-task", "baseline-red", "changed-files",
             "receipt-evidence", "exit-semantics", "resume",
             "install-again", "upgrade", "rollback", "uninstall",
             "uninstall-again")

#: The toy's guarded add(), and the rejection test appended alongside it.
#: Mirrors scripts/test_codex_smoke.py's SIGNED_IN_FIX_WORKER_STUB (same
#: shape, written fresh here rather than imported from a test module).
FIX_WORKER_STUB = '''import io

GUARD = ("def add(a, b):\\n"
         "    for value in (a, b):\\n"
         "        if isinstance(value, bool) or not isinstance(value, "
         "(int, float)):\\n"
         "            raise TypeError('add() needs numbers, got %r' % "
         "(value,))\\n"
         "    return a + b\\n")
EXTRA = ("\\n\\n"
         "class RejectTest(unittest.TestCase):\\n"
         "    def test_rejects_non_numeric(self):\\n"
         "        with self.assertRaises(TypeError):\\n"
         "            add('a', 'b')\\n")
with io.open("mathlib.py", "w", encoding="utf-8") as fh:
    fh.write(GUARD)
with io.open("test_mathlib.py", "a", encoding="utf-8") as fh:
    fh.write(EXTRA)
print("worker wrote mathlib.py and appended a test")
'''

#: A worker that changes nothing: the done_check stays red, for the "the
#: check can fail" half of step 6.
NOOP_WORKER_STUB = "import sys\nsys.stdout.write('noop')\n"

#: A worker slow enough to kill mid-flight, for step 7 (resume). It writes
#: the same fix as FIX_WORKER_STUB, only after a sleep long enough for the
#: suite to send SIGTERM first.
SLOW_WORKER_STUB = FIX_WORKER_STUB.replace(
    "import io\n", "import io\nimport time\ntime.sleep(30)\n", 1)

#: The done_check, red before the fix (add() does not yet reject strings)
#: and green after. Mirrors scripts/test_codex_smoke.py's own
#: SIGNED_IN_RED_CHECK (same shape, written fresh here).
#: ONE plain command with no `;`, `&&` or `|`: a record resumed from disk
#: treats its checks as repository-supplied content and refuses any command
#: separator (measured 2026-09-06: the `;` form was refused on --continue),
#: so the same check has to survive both the fresh run and the resume.
DONE_CHECK = (
    'python3 -c "__import__(\'unittest\').TestCase().assertRaises('
    'TypeError, __import__(\'mathlib\').add, \'a\', \'b\')"')


class Step(object):
    def __init__(self, name, verdict, reason):
        self.name = name
        self.verdict = verdict
        self.reason = reason

    def line(self):
        return "%-16s %-8s %s" % (self.name, self.verdict, self.reason)


def _write_evidence(evidence_dir, name, text):
    """The path written, or "" when it could not be (never raises: a full
    disk or an unwritable evidence dir must not take down the step whose
    output it was trying to save)."""
    if not evidence_dir:
        return ""
    path = os.path.join(evidence_dir, name + ".log")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text or "")
    except OSError:
        return ""
    return path


def _proc_text(proc):
    if proc is None:
        return ""
    return ("$ exit %s\n--- stdout ---\n%s\n--- stderr ---\n%s"
           % (proc.returncode, proc.stdout or "", proc.stderr or ""))


def _write_stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def _build_toy(path):
    """codex_smoke.build_toy, reused rather than duplicated: the same
    mathlib.py/test_mathlib.py toy in its own git repository."""
    return codex_smoke.build_toy(path)


def _write_plan(path, objective, done_check, writes):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([{"id": "U1", "objective": objective,
                   "done_check": done_check, "writes": writes, "deps": []}],
                  fh)
    return path


def _run_brother(toy, runs_root, worker_cmd, outcome, extra_argv=None,
                 timeout=90, env_extra=None):
    """One scripts/brother_run.py invocation, the documented seams set the
    way scripts/test_codex_smoke.py's TheSignedInShape already proves them:
    DOOR_MODEL_CMD as a plain `cat <plan.json>` and MODEL_WORKER_CMD as a
    python3 stub script. (proc_or_None, err)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    env["MODEL_WORKER_CMD"] = worker_cmd
    argv = [sys.executable, os.path.join(HERE, "brother_run.py"), outcome,
           "--cwd", toy, "--runs-root", runs_root, "--slots", "1"]
    argv += extra_argv or []
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True,
                              timeout=timeout, cwd=REPO)
    except subprocess.TimeoutExpired as exc:
        return None, "brother_run.py timed out after %ss: %s" % (timeout, exc)
    except OSError as exc:
        return None, "brother_run.py failed to run: %s" % exc
    return proc, ""


def _popen_brother(toy, runs_root, worker_cmd, outcome, timeout_hint=90):
    """Like _run_brother but non-blocking, for step 7's kill-and-resume."""
    env = dict(os.environ)
    env["MODEL_WORKER_CMD"] = worker_cmd
    argv = [sys.executable, os.path.join(HERE, "brother_run.py"), outcome,
           "--cwd", toy, "--runs-root", runs_root, "--slots", "1"]
    try:
        return subprocess.Popen(argv, env=env, cwd=REPO,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True), ""
    except OSError as exc:
        return None, "brother_run.py failed to start: %s" % exc


def _plan_env(plan_path):
    return {"DOOR_MODEL_CMD": "cat %s" % plan_path}


def _worker_cmd(path, body):
    _write_stub(path, body)
    return "%s %s" % (sys.executable, path)


def _run_dir_for(runs_root):
    return codex_smoke.newest_run_dir(
        os.path.join(runs_root, "docs", "plan", "runs"))


def _read_receipt(run_dir):
    """(body, "") or (None, why). Never raises: a missing or unreadable
    receipt is reported, not thrown, since more than one step reads it."""
    if not run_dir:
        return None, "no run directory was found"
    path = os.path.join(run_dir, "receipt", "receipt.json")
    if not os.path.isfile(path):
        return None, "no receipt at %s" % path
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except (OSError, ValueError) as exc:
        return None, "%s could not be read: %s" % (path, exc)


def _under_temp(path):
    if not path:
        return False
    real = os.path.realpath(path)
    for base in (tempfile.gettempdir(), "/tmp", "/private/tmp"):
        try:
            base_real = os.path.realpath(base)
        except OSError:
            continue
        if real == base_real or real.startswith(base_real + os.sep):
            return True
    return False


class Context(dict):
    """A plain dict with attribute-style holes filled as steps run: toy2,
    toy3, run_dir, run_dir3 and so on are added by the step that first
    needs them, read by any step after it."""


def _isolated_home(evidence_dir):
    """A durable-but-disposable home under the evidence directory: deleted
    and rebuilt at the start of every conformance run. See the module
    docstring for why this is not tempfile.mkdtemp()."""
    home = os.path.join(evidence_dir, "isolated-home")
    shutil.rmtree(home, ignore_errors=True)
    os.makedirs(home, exist_ok=True)
    return home


def _lifecycle_verb(ctx, verb):
    """(verdict, reason) for install/upgrade/rollback/uninstall, the shared
    logic behind steps 1, 8, 9, 10 and 11. NO-DATA immediately when the
    adapter itself refuses the capability (this is the whole of Cortex's
    answer); otherwise NO-DATA offline; otherwise brother_install.py when
    it exists; otherwise the Claude adapter's own local marketplace path,
    or NO-DATA naming the missing file for Codex and Cortex."""
    adapter, provider = ctx["adapter"], ctx["provider"]
    cap = getattr(adapter, verb)()
    if isinstance(cap, PA.Refusal):
        return NODATA, cap.reason
    if ctx["offline"]:
        return NODATA, "offline: %s not attempted" % verb
    if os.path.isfile(BROTHER_INSTALL):
        argv = [sys.executable, BROTHER_INSTALL, verb,
               "--codex-home", ctx["isolated_home"],
               "--marketplace", REPO]
        if provider == "codex" and adapter.bin:
            argv += ["--codex-bin", adapter.bin]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=180, cwd=REPO)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return FAIL, "brother_install.py %s failed to run: %s" % (
                verb, exc)
        _write_evidence(ctx["evidence_dir"], "%s-brother_install" % verb,
                        _proc_text(proc))
        if proc.returncode == 0:
            return PASS, "brother_install.py %s exited 0" % verb
        return FAIL, "brother_install.py %s exited %d" % (
            verb, proc.returncode)
    if provider == "claude":
        claude_bin = shutil.which("claude")
        if not claude_bin:
            return NODATA, ("no scripts/brother_install.py in this "
                           "worktree and no claude CLI on PATH")
        return _claude_local_marketplace(ctx, verb, claude_bin)
    return NODATA, ("no scripts/brother_install.py in this worktree (a "
                   "sibling builder is writing it)")


_CLAUDE_VERB_ARGV = {
    "install": [["marketplace", "add", REPO], ["install", "brother@brother"]],
    "upgrade": [["update", "brother@brother"]],
    "uninstall": [["uninstall", "brother@brother"]],
}


def _claude_local_marketplace(ctx, verb, claude_bin):
    if verb == "rollback":
        return NODATA, ("no documented single rollback command for the "
                       "local Claude marketplace path; not measured")
    steps = _CLAUDE_VERB_ARGV.get(verb)
    if steps is None:
        return NODATA, "unrecognised lifecycle verb %r" % verb
    home = ctx["isolated_home"]
    env = dict(os.environ)
    env["HOME"] = home
    env["CLAUDE_CONFIG_DIR"] = os.path.join(home, ".claude")
    outputs = []
    for words in steps:
        argv = [claude_bin, "plugin"] + words
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=90, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            outputs.append("%s failed to run: %s" % (" ".join(argv), exc))
            _write_evidence(ctx["evidence_dir"], "%s-claude-local" % verb,
                            "\n".join(outputs))
            return FAIL, "%s failed to run: %s" % (" ".join(argv), exc)
        outputs.append(_proc_text(proc))
        if proc.returncode != 0:
            _write_evidence(ctx["evidence_dir"], "%s-claude-local" % verb,
                            "\n".join(outputs))
            return FAIL, "%s exited %d" % (" ".join(argv), proc.returncode)
    _write_evidence(ctx["evidence_dir"], "%s-claude-local" % verb,
                    "\n".join(outputs))
    return PASS, "claude local marketplace %s exited 0" % verb


def step_install(ctx):
    verdict, reason = _lifecycle_verb(ctx, "install")
    return Step("install", verdict, reason)


def step_same_task(ctx):
    toy = os.path.join(ctx["isolated_home"], "toy")
    why = _build_toy(toy)
    if why:
        return Step("same-task", FAIL, "could not build the toy: %s" % why)
    ctx["toy"] = toy
    plan_path = os.path.join(ctx["isolated_home"], "plan.json")
    _write_plan(plan_path, "make add() refuse non-numeric input", DONE_CHECK,
               ["mathlib.py", "test_mathlib.py"])
    worker = _worker_cmd(os.path.join(ctx["isolated_home"], "fix_worker.py"),
                        FIX_WORKER_STUB)
    proc, err = _run_brother(toy, ctx["runs_root"], worker,
                             "make add() refuse non-numeric input",
                             env_extra=_plan_env(plan_path))
    if proc is None:
        return Step("same-task", FAIL, err)
    _write_evidence(ctx["evidence_dir"], "same-task", _proc_text(proc))
    ctx["run_dir"] = _run_dir_for(ctx["runs_root"])
    if proc.returncode != 0:
        return Step("same-task", FAIL,
                    "brother_run.py exited %d: %s" % (
                        proc.returncode, (proc.stderr or "")[-300:]))
    if not ctx["run_dir"]:
        return Step("same-task", FAIL,
                    "brother_run.py exited 0 but left no run directory "
                    "under %s" % ctx["runs_root"])
    return Step("same-task", PASS,
               "brother_run.py exited 0, run directory %s" % ctx["run_dir"])


def _receipt_changed_entry(ctx, filename="mathlib.py"):
    body, err = _read_receipt(ctx.get("run_dir"))
    if body is None:
        return None, err
    changed = ((body.get("scope") or {}).get("changed")) or []
    for entry in changed:
        if entry.get("file") == filename:
            return entry, ""
    return None, "the receipt names no changed entry for %s" % filename


def step_baseline_red(ctx):
    if not ctx.get("run_dir"):
        return Step("baseline-red", NODATA,
                   "no run directory from the same-task step to read")
    entry, err = _receipt_changed_entry(ctx)
    if entry is None:
        return Step("baseline-red", FAIL, err)
    if entry.get("check_passed_before") is not False:
        return Step("baseline-red", FAIL,
                    "check_passed_before is %r, not False" %
                    (entry.get("check_passed_before"),))
    return Step("baseline-red", PASS,
               "check_passed_before is False for mathlib.py")


def step_changed_files(ctx):
    toy = ctx.get("toy")
    if not toy:
        return Step("changed-files", NODATA, "no toy directory to compare")
    try:
        with open(os.path.join(toy, "mathlib.py"), encoding="utf-8") as fh:
            lib_now = fh.read()
        with open(os.path.join(toy, "test_mathlib.py"),
                 encoding="utf-8") as fh:
            test_now = fh.read()
    except OSError as exc:
        return Step("changed-files", FAIL, "could not read the toy: %s" % exc)
    if lib_now == codex_smoke.TOY_MATHLIB:
        return Step("changed-files", FAIL,
                    "mathlib.py is byte-identical to the seeded copy")
    if test_now == codex_smoke.TOY_TEST:
        return Step("changed-files", FAIL,
                    "test_mathlib.py is byte-identical to the seeded copy")
    return Step("changed-files", PASS,
               "mathlib.py and test_mathlib.py both differ from the "
               "seeded bytes")


def step_receipt_evidence(ctx):
    run_dir = ctx.get("run_dir")
    if not run_dir:
        return Step("receipt-evidence", NODATA, "no run directory to check")
    receipt_path = os.path.join(run_dir, "receipt", "receipt.json")
    if not os.path.isfile(receipt_path):
        return Step("receipt-evidence", FAIL,
                    "no receipt at %s" % receipt_path)
    if _under_temp(receipt_path):
        return Step("receipt-evidence", FAIL,
                    "%s sits under a system temp root" % receipt_path)
    body, err = _read_receipt(run_dir)
    if body is None:
        return Step("receipt-evidence", FAIL, err)
    changed = ((body.get("scope") or {}).get("changed")) or []
    if not changed:
        return Step("receipt-evidence", FAIL,
                    "the receipt's scope.changed is empty")
    for entry in changed:
        for field in ("check_command", "exit_code"):
            if field not in entry:
                return Step("receipt-evidence", FAIL,
                            "%s is missing %r" % (entry.get("file"), field))
        loc = entry.get("output_location")
        if not loc or loc == "NO-DATA":
            return Step("receipt-evidence", FAIL,
                        "%s carries no output_location" % entry.get("file"))
        if not os.path.exists(loc):
            return Step("receipt-evidence", FAIL,
                        "output_location %s does not exist" % loc)
    return Step("receipt-evidence", PASS,
               "receipt at a durable path, every changed file carries a "
               "check_command, exit_code and an existing output_location")


def step_exit_semantics(ctx):
    if not ctx.get("run_dir"):
        return Step("exit-semantics", NODATA,
                   "no successful run from the same-task step to compare "
                   "against")
    toy2 = os.path.join(ctx["isolated_home"], "toy2")
    why = _build_toy(toy2)
    if why:
        return Step("exit-semantics", FAIL,
                    "could not build the second toy: %s" % why)
    plan_path = os.path.join(ctx["isolated_home"], "plan2.json")
    _write_plan(plan_path, "make add() refuse non-numeric input (unmet)",
               DONE_CHECK, ["mathlib.py", "test_mathlib.py"])
    worker = _worker_cmd(os.path.join(ctx["isolated_home"], "noop_worker.py"),
                        NOOP_WORKER_STUB)
    proc, err = _run_brother(toy2, ctx["runs_root"], worker,
                             "make add() refuse non-numeric input (unmet)",
                             env_extra=_plan_env(plan_path))
    if proc is None:
        return Step("exit-semantics", FAIL, err)
    _write_evidence(ctx["evidence_dir"], "exit-semantics", _proc_text(proc))
    if proc.returncode == 0:
        return Step("exit-semantics", FAIL,
                    "a worker that leaves the check red still exited 0")
    return Step("exit-semantics", PASS,
               "the working run exited 0 and the check-still-red run "
               "exited %d" % proc.returncode)


def step_resume(ctx):
    toy3 = os.path.join(ctx["isolated_home"], "toy3")
    why = _build_toy(toy3)
    if why:
        return Step("resume", FAIL, "could not build the third toy: %s" % why)
    outcome = "resume test: make add() refuse non-numeric input"
    plan_path = os.path.join(ctx["isolated_home"], "plan3.json")
    _write_plan(plan_path, outcome, DONE_CHECK, ["mathlib.py", "test_mathlib.py"])
    slow_worker = _worker_cmd(
        os.path.join(ctx["isolated_home"], "slow_worker.py"), SLOW_WORKER_STUB)
    env = dict(os.environ)
    env["MODEL_WORKER_CMD"] = slow_worker
    env.update(_plan_env(plan_path))
    argv = [sys.executable, os.path.join(HERE, "brother_run.py"), outcome,
           "--cwd", toy3, "--runs-root", ctx["runs_root"], "--slots", "1"]
    try:
        proc = subprocess.Popen(argv, env=env, cwd=REPO,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return Step("resume", FAIL, "could not start the run: %s" % exc)
    time.sleep(3.0)
    run_dir = _run_dir_for(ctx["runs_root"])
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)
    if not run_dir:
        run_dir = _run_dir_for(ctx["runs_root"])
    if not run_dir:
        return Step("resume", NODATA,
                   "the run left no run directory before it was killed, "
                   "so nothing could be resumed")
    fix_worker = _worker_cmd(
        os.path.join(ctx["isolated_home"], "resume_fix_worker.py"),
        FIX_WORKER_STUB)
    env2 = dict(os.environ)
    env2["MODEL_WORKER_CMD"] = fix_worker
    argv2 = [sys.executable, os.path.join(HERE, "brother_run.py"),
            "--cwd", toy3, "--runs-root", ctx["runs_root"], "--slots", "1",
            "--continue"]
    try:
        proc2 = subprocess.run(argv2, env=env2, cwd=REPO,
                               capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Step("resume", FAIL, "--continue failed to run: %s" % exc)
    _write_evidence(ctx["evidence_dir"], "resume", _proc_text(proc2))
    if proc2.returncode != 0:
        return Step("resume", FAIL,
                    "--continue exited %d: %s" % (
                        proc2.returncode, (proc2.stderr or "")[-300:]))
    return Step("resume", PASS,
               "the killed run at %s resumed with --continue and exited 0"
               % run_dir)


def step_install_again(ctx):
    verdict, reason = _lifecycle_verb(ctx, "install")
    return Step("install-again", verdict, reason)


def step_upgrade(ctx):
    verdict, reason = _lifecycle_verb(ctx, "upgrade")
    return Step("upgrade", verdict, reason)


def step_rollback(ctx):
    verdict, reason = _lifecycle_verb(ctx, "rollback")
    return Step("rollback", verdict, reason)


def step_uninstall(ctx):
    verdict, reason = _lifecycle_verb(ctx, "uninstall")
    return Step("uninstall", verdict, reason)


def step_uninstall_again(ctx):
    cap = ctx["adapter"].uninstall()
    if isinstance(cap, PA.Refusal):
        return Step("uninstall-again", NODATA, cap.reason)
    return Step("uninstall-again", NODATA,
               "a second uninstall has nothing left to remove")


STEP_FUNCS = {
    "install": step_install, "same-task": step_same_task,
    "baseline-red": step_baseline_red, "changed-files": step_changed_files,
    "receipt-evidence": step_receipt_evidence,
    "exit-semantics": step_exit_semantics, "resume": step_resume,
    "install-again": step_install_again, "upgrade": step_upgrade,
    "rollback": step_rollback, "uninstall": step_uninstall,
    "uninstall-again": step_uninstall_again,
}


def run_conformance(provider, evidence_dir, offline):
    """The twelve Step results for one provider, in STEP_ORDER. Never
    raises: a step function that cannot even attempt its work returns a
    NO-DATA or FAIL Step rather than propagating."""
    cls = PA.ADAPTERS.get(provider)
    if cls is None:
        raise ValueError("unknown provider %r" % (provider,))
    try:
        os.makedirs(evidence_dir, exist_ok=True)
    except OSError as exc:
        return [Step(name, NODATA,
                    "could not create evidence dir %s: %s" %
                    (evidence_dir, exc)) for name in STEP_ORDER]
    isolated_home = _isolated_home(evidence_dir)
    runs_root = os.path.join(isolated_home, "brother", "runs")
    os.makedirs(runs_root, exist_ok=True)
    adapter = cls(env=os.environ)
    # An adapter that refuses INVOCATION cannot run the task at all, so every
    # step is NO-DATA carrying that refusal: never FAIL (nothing was tried)
    # and never PASS (nothing was measured). This is the whole of Cortex's
    # answer until somebody measures it.
    invocation = adapter.invocation()
    if isinstance(invocation, PA.Refusal):
        return [Step(name, NODATA, invocation.reason) for name in STEP_ORDER]
    ctx = Context(provider=provider, adapter=adapter,
                 evidence_dir=evidence_dir, offline=offline,
                 isolated_home=isolated_home, runs_root=runs_root)
    results = []
    for name in STEP_ORDER:
        try:
            results.append(STEP_FUNCS[name](ctx))
        except Exception as exc:  # noqa: BLE001 - a step must never abort the suite
            results.append(Step(name, FAIL,
                                "the step itself raised %s: %s" %
                                (type(exc).__name__, exc)))
    return results


def _verdict_for(results):
    if any(r.verdict == FAIL for r in results):
        return FAIL
    if any(r.verdict == NODATA for r in results):
        return NODATA
    return PASS


def _summary_line(provider, results):
    counts = {PASS: 0, FAIL: 0, NODATA: 0}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return "provider=%s pass=%d fail=%d no-data=%d verdict=%s" % (
        provider, counts[PASS], counts[FAIL], counts[NODATA],
        _verdict_for(results))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", required=True,
                   choices=sorted(PA.ADAPTERS) + ["all"])
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--offline", action="store_true",
                   help="skip every step that would install, upgrade, "
                        "roll back or uninstall for real; those steps "
                        "read NO-DATA")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    providers = sorted(PA.ADAPTERS) if args.provider == "all" else \
        [args.provider]
    overall = FAIL if False else PASS  # placeholder replaced below
    verdicts = []
    for provider in providers:
        base = args.evidence_dir or os.path.join(DEFAULT_EVIDENCE_ROOT,
                                                 provider)
        results = run_conformance(provider, base, args.offline)
        print("== %s ==" % provider)
        for r in results:
            print(r.line())
        summary = _summary_line(provider, results)
        print(summary)
        print("")
        verdicts.append(_verdict_for(results))

    if FAIL in verdicts:
        return 1
    if NODATA in verdicts:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

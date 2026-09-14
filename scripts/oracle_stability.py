#!/usr/bin/env python3
"""WBS-80.02 Oracle stability: run a benchmark_harness.py task/arm oracle
multiple times and refuse to trust a fixture that cannot reproduce the
same verdict every time.

Exact roadmap quote (WBS-80.02):
  "For new benchmark/fixture tests:
   - run oracle multiple times;
   - exact expected verdict;
   - no dependence on residual temp state;
   - no real user config mutation;
   - stable across clean clones where applicable.
   Flaky fixture = NO-DATA for benchmark comparison until repaired."

This module does not invent a new oracle: it drives the real one,
scripts/benchmark_harness.py (run_task), which already isolates each run
in its own tempdir and separates the visible workspace from the hidden
eval dir. What oracle_stability.py adds is repetition and the checks the
roadmap names around that repetition:

  - exact expected verdict: every run must equal --expected-verdict, not
    just agree with each other (a fixture that consistently produces the
    WRONG verdict is broken, not flaky, and is scored FAIL, not NO-DATA).
  - flaky (runs disagree with each other) is scored NO-DATA, per the
    roadmap's own wording, never a fabricated PASS.
  - no dependence on residual temp state: checked by scanning the system
    tempdir for benchmark_harness's own bench-ws-*/bench-eval-* prefixes
    before and after the run loop (ponytail: this only catches THAT
    harness's own naming convention, not arbitrary leaks elsewhere --
    upgrade if a second harness with different temp naming needs this).
  - no real user config mutation: checked by comparing `git status
    --porcelain` on --repo-root before and after the run loop (ponytail:
    this is a proxy scoped to the repo's own tracked/untracked state, not
    a full-filesystem audit of $HOME -- upgrade if a fixture is found
    that mutates state outside the repo undetected).
  - stable across clean clones where applicable: optional
    (--check-clean-clone), since not every fixture depends on repo state.
    When requested, `git clone --local` this repo into a tempdir and
    re-run the same task/arm there once via the clone's OWN copy of
    benchmark_harness.py, as a subprocess. Not requested by default
    (a clone plus a subprocess run costs real time); when skipped this is
    reported honestly as NOT-REQUESTED, never silently omitted.

Repetition count is not specified by the roadmap; DEFAULT_RUNS below is
an explicit inference (5), overridable with --runs.

Runs on the 3.9 floor: standard library only, no match statements.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import benchmark_harness as bh  # noqa: E402
from evidence_obligation import VERDICTS, exit_code_for_verdict  # noqa: E402

DEFAULT_RUNS = 5
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# benchmark_harness's own tempdir prefixes (materialize_workspace / run_task).
# Not redeclared there as a constant, so mirrored here by the exact literals
# it passes to tempfile.mkdtemp.
_HARNESS_TEMP_PREFIXES = ("bench-ws-", "bench-eval-")


def _leftover_harness_temp_dirs():
    tmp = tempfile.gettempdir()
    try:
        names = os.listdir(tmp)
    except OSError:
        return set()
    return {n for n in names if n.startswith(_HARNESS_TEMP_PREFIXES)}


def _git_status_porcelain(repo_root):
    """None means "could not check" (not a git repo, git unavailable) --
    distinct from an empty string, which means "checked, and it is clean."
    A caller must not treat None as clean."""
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def check_clean_clone(repo_root, task, arm_name, arm_command, expected_verdict, timeout):
    """Re-run the same task/arm from a fresh `git clone --local` of
    repo_root, via that clone's OWN scripts/benchmark_harness.py as a
    subprocess (not an in-process import) -- the point is to prove the
    fixture does not depend on anything in THIS checkout that a clean
    clone would not have.

    Ponytail ceiling: arm_command is round-tripped through the CLI's
    single shell-string --arm-command flag via shlex.join (quoting each
    token), same as benchmark_harness.py's own CLI already requires as a
    shell string -- a list arm_command with embedded environment-relative
    assumptions (not just spaces/quotes, which shlex.join handles) is
    still a known limitation of that existing CLI surface, not a new one
    introduced here. Off by default (--check-clean-clone); skipped runs
    report NOT-REQUESTED honestly."""
    is_repo = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if is_repo.returncode != 0 or is_repo.stdout.strip() != "true":
        return {"verdict": "NO-DATA", "reason": "repo_root is not a git repository -- clean-clone check does not apply here"}

    clone_dir = tempfile.mkdtemp(prefix="oracle-stability-clone-")
    try:
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--local", "--no-hardlinks", "--depth", "1", repo_root, clone_dir],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        if cloned.returncode != 0:
            return {"verdict": "NO-DATA", "reason": "git clone failed: %s" % cloned.stderr.strip()[-500:]}
        harness_path = os.path.join(clone_dir, "scripts", "benchmark_harness.py")
        if not os.path.isfile(harness_path):
            return {"verdict": "NO-DATA", "reason": "clean clone has no scripts/benchmark_harness.py -- nothing to run there"}
        cmd = [sys.executable, harness_path, "--task", task["id"], "--arm-name", arm_name]
        if arm_command:
            cmd += ["--arm-command", arm_command if isinstance(arm_command, str) else shlex.join(arm_command)]
        cmd += ["--timeout", str(timeout)]
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, cwd=clone_dir,
                timeout=timeout + 30, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return {"verdict": "NO-DATA", "reason": "clean-clone run timed out"}
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, ValueError):
            return {"verdict": "NO-DATA", "reason": "clean-clone run produced unparseable output: %s" % completed.stdout[-500:]}
        clone_verdict = payload.get("verdict")
        if clone_verdict == expected_verdict:
            return {"verdict": "PASS", "reason": "clean clone reproduced the expected verdict %r" % expected_verdict, "clone_verdict": clone_verdict}
        return {"verdict": "FAIL", "reason": "clean clone produced %r, expected %r" % (clone_verdict, expected_verdict), "clone_verdict": clone_verdict}
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def classify_stability(verdicts, expected_verdict):
    """Exact-expected-verdict logic straight from the roadmap quote:
    disagreement across runs is flaky (NO-DATA); agreement on the wrong
    verdict is broken, not flaky (FAIL); agreement on the expected verdict
    is stable (PASS)."""
    if not verdicts:
        return "NO-DATA", "no runs were performed"
    unique = sorted(set(verdicts))
    if len(unique) > 1:
        return "NO-DATA", (
            "flaky fixture: %d run(s) produced differing verdicts %s -- "
            "NO-DATA for benchmark comparison until repaired" % (len(verdicts), unique)
        )
    only = verdicts[0]
    if only == expected_verdict:
        return "PASS", "stable: all %d run(s) produced the expected verdict %r" % (len(verdicts), expected_verdict)
    return "FAIL", "stable but wrong: all %d run(s) consistently produced %r, expected %r" % (len(verdicts), only, expected_verdict)


def run_stability_check(task, arm_name, arm_command, expected_verdict, runs=DEFAULT_RUNS,
                         timeout=bh.DEFAULT_TIMEOUT_S, repo_root=REPO_ROOT, check_clone=False):
    assert expected_verdict in VERDICTS, "expected_verdict must be one of %s" % (VERDICTS,)

    temp_before = _leftover_harness_temp_dirs()
    status_before = _git_status_porcelain(repo_root)

    run_results = [bh.run_task(task, arm_name, arm_command, timeout) for _ in range(runs)]
    verdicts = [r["verdict"] for r in run_results]

    temp_after = _leftover_harness_temp_dirs()
    status_after = _git_status_porcelain(repo_root)

    leaked_temp = sorted(temp_after - temp_before)
    if status_before is None or status_after is None:
        repo_mutation = None
    else:
        repo_mutation = status_before != status_after

    verdict, reason = classify_stability(verdicts, expected_verdict)

    # Residual state and config mutation are real defects the roadmap
    # names explicitly, not the same failure mode as verdict disagreement
    # -- but a caller who trusts a stability PASS should not also have to
    # separately check these two booleans, so either one downgrades a
    # would-be PASS to NO-DATA (never silently upgraded past them).
    if verdict == "PASS" and leaked_temp:
        verdict, reason = "NO-DATA", "verdicts were stable, but leftover temp state was found after the run loop: %s" % leaked_temp
    elif verdict == "PASS" and repo_mutation:
        verdict, reason = "NO-DATA", "verdicts were stable, but the repo's git status changed across the run loop (real user config mutation)"

    clone_result = "NOT-REQUESTED"
    if check_clone:
        clone_result = check_clean_clone(repo_root, task, arm_name, arm_command, expected_verdict, timeout)
        if verdict == "PASS" and clone_result.get("verdict") == "FAIL":
            verdict, reason = "FAIL", "in-repo runs were stable, but the clean-clone run disagreed: %s" % clone_result["reason"]

    assert verdict in VERDICTS
    return {
        "task_id": task["id"],
        "arm_name": arm_name,
        "expected_verdict": expected_verdict,
        "runs_requested": runs,
        "run_verdicts": verdicts,
        "temp_state_leak": leaked_temp,
        "repo_mutation_detected": repo_mutation,
        "clean_clone": clone_result,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-tasks", action="store_true", help="print benchmark_harness's fixed task set and exit")
    parser.add_argument("--task", help="task id to run (see --list-tasks)")
    parser.add_argument("--arm-name", default="unnamed-arm", help="label for the arm being tested for stability")
    parser.add_argument("--arm-command", help="shell command the arm runs, cwd set to the task workspace")
    parser.add_argument("--expected-verdict", choices=VERDICTS, help="the verdict every run must reproduce exactly")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="how many times to run the oracle (default %d; not specified by the roadmap)" % DEFAULT_RUNS)
    parser.add_argument("--timeout", type=int, default=bh.DEFAULT_TIMEOUT_S, help="wall-clock seconds per run before it is judged timed out")
    parser.add_argument("--repo-root", default=REPO_ROOT, help="repo to check for config mutation and to clone for --check-clean-clone")
    parser.add_argument("--check-clean-clone", action="store_true", help="also re-run once from a fresh git clone of --repo-root")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_tasks:
        for task in bh.TASKS:
            print("%s: %s" % (task["id"], task["prompt"]))
        return 0

    if not args.task:
        parser.error("--task is required (or pass --list-tasks)")
    if not args.expected_verdict:
        parser.error("--expected-verdict is required, one of %s" % (VERDICTS,))

    task = bh.task_by_id(args.task)
    if task is None:
        print("NO-DATA: unknown task id %r (see --list-tasks)" % args.task)
        return 2

    result = run_stability_check(
        task, args.arm_name, args.arm_command, args.expected_verdict,
        runs=args.runs, timeout=args.timeout, repo_root=args.repo_root,
        check_clone=args.check_clean_clone,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code_for_verdict(result["verdict"])


if __name__ == "__main__":
    sys.exit(main())

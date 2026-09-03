#!/usr/bin/env python3
"""The comparative outcome benchmark: does a system deliver the OUTCOME?

WHY THIS EXISTS
  The founder's north-star brief (2026-08-10) names one thing as the P0: there
  is no measurement of whether BrotherMode makes work better. The repository
  already had a benchmark, tools/bm_hookbench.py, but that one measures what
  the hooks COST in milliseconds. Cost is not outcome. Nothing here measured
  whether a system actually delivers what was asked.

  This file is that missing measurement, at the smallest honest scale.

THE ONE DESIGN DECISION THAT MATTERS
  THE ACCEPTANCE TEST IS HIDDEN FROM THE AGENT.

  Each task ships two things: a PROMPT the agent sees, and an acceptance test
  the agent NEVER sees. The harness copies the test in only after the agent has
  finished and stopped running.

  That is deliberate and it is the whole point. The brief's section 3.5
  describes the failure this prevents: an implementer writes a test that checks
  a token exists, the test passes, the delivery packet truthfully reports a
  passing test, and the actual requirement is still unmet. Nobody lied and the
  work is still wrong. An agent that can read the acceptance test optimises for
  the test. An agent that cannot must optimise for the requirement, which is the
  only thing worth measuring.

  Corollary, stated because it constrains every future task: a task whose
  acceptance test the agent could plausibly GUESS in full is a weak task. The
  good tasks are the ones where the obvious implementation passes an obvious
  test and still fails the hidden one.

WHAT IT MEASURES, and what it refuses to measure
  MEASURED: whether the hidden acceptance test passes, wall-clock seconds, and
  whether the run crashed. Those are facts a command produced.

  NOT MEASURED, on purpose: token cost and money. The runners report those
  inconsistently across systems and a number that means different things in
  different columns is worse than no number. The result file records them as
  null rather than guessing, per this project's rule that an invented number may
  never enter the record.

  NOT MEASURED YET: the brief's full VADR, which has fifteen conditions. This
  harness measures condition 9 (deterministic checks ran after the final change)
  and the acceptance outcome. The other conditions need the work-item ledger
  that does not exist yet. Saying so is the point; a partial metric labelled
  partial is useful, a partial metric labelled VADR is a lie.

DETERMINISM, which is the acceptance gate for the harness itself
  The brief's Loop 1 gate is that the corpus runs TWICE from identical
  snapshots and produces mechanically comparable output. `--verify-determinism`
  runs the whole corpus twice and reports whether the SHAPE of the output is
  stable: same tasks, same systems, same fields. It deliberately does NOT
  require identical verdicts, because a language model is not deterministic and
  a harness that demanded identical results would only ever be testing
  temperature. What must be stable is the measurement apparatus.
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(ROOT, "benchmark", "tasks")

#: Wall-clock ceiling for one agent run. A task that has not finished by here is
#: recorded as a timeout, which is a RESULT, not an error: a system that cannot
#: finish inside the budget has failed the task in the way that matters to a
#: user waiting for it.
DEFAULT_TIMEOUT = 600


class Runner(object):
    """How to invoke one system under test.

    `preamble` is the only difference between the BrotherMode column and the
    bare column: it is the instruction text prepended to the task prompt. That
    keeps the comparison honest, because the model, the repository state, the
    task and the hidden test are all identical, and the ONLY variable is the
    working discipline the system asks for.
    """

    def __init__(self, name, argv, preamble=""):
        self.name = name
        self.argv = argv
        self.preamble = preamble

    def run(self, prompt, cwd, timeout):
        full = (self.preamble + "\n\n" + prompt) if self.preamble else prompt
        started = time.time()
        try:
            proc = subprocess.run(
                self.argv + [full], cwd=cwd, capture_output=True, text=True,
                timeout=timeout)
            return {
                "exit_code": proc.returncode,
                "wall_seconds": round(time.time() - started, 1),
                "timed_out": False,
                "stderr_tail": (proc.stderr or "")[-400:],
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": None,
                "wall_seconds": round(time.time() - started, 1),
                "timed_out": True,
                "stderr_tail": "timed out after %ds" % timeout,
            }


BROTHERMODE_PREAMBLE = """Work to this discipline, which is the system under test:

1. Before writing code, state in one line what DONE means for this task, in
   terms a check could confirm.
2. Consider the edge cases the request implies but does not spell out. The
   obvious implementation is usually the one that misses them.
3. After your last edit, RUN a check that proves the behaviour, and quote its
   output. Do not report success from reading your own code.
4. If a check fails, fix the code rather than weakening the check.
"""


def runners(engine):
    """The two columns. Same engine, same task, same hidden test."""
    if engine == "codex":
        # --sandbox workspace-write is REQUIRED and is not a convenience.
        # Codex exec defaults to read-only, so the first real run of this
        # harness produced four rejected cells with the agent exiting 0 and no
        # file on disk: "cannot import dedupe.py". That looked exactly like a
        # finding (both systems score zero) and was a plumbing bug. It is
        # recorded here because the failure mode is silent and would have been
        # published as a result.
        #
        # workspace-write, not --dangerously-bypass-approvals-and-sandbox: the
        # agent needs to write its own throwaway directory and nothing else,
        # and a benchmark is the last place to hand out full disk access.
        argv = ["codex", "exec", "--skip-git-repo-check",
                "--sandbox", "workspace-write"]
    elif engine == "claude":
        argv = ["claude", "-p"]
    else:
        raise SystemExit("unknown engine: %s" % engine)
    return [
        Runner("bare", argv),
        Runner("brothermode", argv, BROTHERMODE_PREAMBLE),
    ]


def load_tasks():
    if not os.path.isdir(CORPUS):
        raise SystemExit("no corpus at %s" % CORPUS)
    tasks = []
    for name in sorted(os.listdir(CORPUS)):
        d = os.path.join(CORPUS, name)
        spec = os.path.join(d, "task.json")
        if not os.path.isfile(spec):
            continue
        with io.open(spec, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["id"] = name
        meta["dir"] = d
        tasks.append(meta)
    if not tasks:
        raise SystemExit("corpus at %s contains no task.json" % CORPUS)
    return tasks


def score(task, workdir):
    """Copy the HIDDEN acceptance test in and run it.

    This happens only after the agent process has exited. The test never exists
    inside the working directory while the agent can see it.
    """
    hidden = os.path.join(task["dir"], "acceptance_test.py")
    if not os.path.isfile(hidden):
        return {"accepted": None, "reason": "NO-DATA: task ships no acceptance test"}
    dest = os.path.join(workdir, "_acceptance_test.py")
    shutil.copyfile(hidden, dest)
    try:
        proc = subprocess.run(
            [sys.executable, dest], cwd=workdir, capture_output=True,
            text=True, timeout=120)
        return {
            "accepted": proc.returncode == 0,
            "exit_code": proc.returncode,
            "reason": (proc.stdout + proc.stderr).strip()[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"accepted": False, "exit_code": None,
                "reason": "acceptance test timed out"}
    finally:
        if os.path.isfile(dest):
            os.remove(dest)


def run_one(task, runner, timeout):
    """One task, one system, in a throwaway directory that starts identical."""
    tmp = tempfile.mkdtemp(prefix="bmbench_")
    try:
        seed = os.path.join(task["dir"], "seed")
        work = os.path.join(tmp, "work")
        if os.path.isdir(seed):
            shutil.copytree(seed, work)
        else:
            os.makedirs(work)
        agent = runner.run(task["prompt"], work, timeout)
        verdict = score(task, work)
        return {
            "task": task["id"],
            "system": runner.name,
            "agent": agent,
            "acceptance": verdict,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_corpus(engine, timeout, only=None):
    tasks = load_tasks()
    if only:
        tasks = [t for t in tasks if t["id"] in only]
    results = []
    for task in tasks:
        for runner in runners(engine):
            sys.stderr.write("  %-28s %-12s ... " % (task["id"], runner.name))
            sys.stderr.flush()
            r = run_one(task, runner, timeout)
            acc = r["acceptance"].get("accepted")
            sys.stderr.write(
                "%s  %.0fs\n" % (
                    {True: "ACCEPTED", False: "rejected", None: "NO-DATA"}[acc],
                    r["agent"]["wall_seconds"]))
            results.append(r)
    return results


def shape(results):
    """The comparable SHAPE of a run: what determinism is checked against."""
    return sorted((r["task"], r["system"], sorted(r["acceptance"].keys()))
                  for r in results)


def summarise(results):
    systems = sorted({r["system"] for r in results})
    tasks = sorted({r["task"] for r in results})
    lines = ["", "TASK".ljust(30) + "".join(s.ljust(16) for s in systems)]
    for t in tasks:
        row = t.ljust(30)
        for s in systems:
            hit = [r for r in results if r["task"] == t and r["system"] == s]
            acc = hit[0]["acceptance"].get("accepted") if hit else None
            row += {True: "ACCEPTED", False: "rejected",
                    None: "NO-DATA"}[acc].ljust(16)
        lines.append(row)
    lines.append("")
    for s in systems:
        got = [r for r in results if r["system"] == s]
        ok = len([r for r in got if r["acceptance"].get("accepted") is True])
        lines.append("%-14s accepted %d of %d" % (s, ok, len(got)))
    lines.append("")
    lines.append("Token and money cost: NOT MEASURED. The runners report these")
    lines.append("inconsistently across systems, and a number meaning different")
    lines.append("things per column is worse than no number.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", default="codex", choices=["codex", "claude"])
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--task", action="append", dest="only")
    ap.add_argument("--out", default=None, help="write the result JSON here")
    ap.add_argument("--verify-determinism", action="store_true",
                    help="run the corpus twice and compare output SHAPE")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for t in load_tasks():
            print("%-28s %s" % (t["id"], t.get("title", "")))
        return 0

    sys.stderr.write("bm_bench: engine=%s\n" % args.engine)
    first = run_corpus(args.engine, args.timeout, args.only)

    if args.verify_determinism:
        sys.stderr.write("bm_bench: second pass for determinism\n")
        second = run_corpus(args.engine, args.timeout, args.only)
        stable = shape(first) == shape(second)
        print(summarise(first))
        print("DETERMINISM: output shape %s across two passes"
              % ("STABLE" if stable else "UNSTABLE"))
        print("Verdicts are NOT required to match: a language model is not")
        print("deterministic, and demanding identical results would only test")
        print("temperature. What must be stable is the apparatus.")
        if not stable:
            return 1
        first = {"pass1": first, "pass2": second, "shape_stable": True}

    else:
        print(summarise(first))

    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as fh:
            json.dump(first, fh, indent=2, sort_keys=True)
        sys.stderr.write("bm_bench: wrote %s\n" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

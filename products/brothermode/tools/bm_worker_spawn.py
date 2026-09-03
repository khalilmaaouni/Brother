"""bm_worker_spawn: the WorkerAdapter that actually starts something.

W9.1. Until this existed, every adapter this engine shipped was
RecordIntentWorker, whose own docstring says it "never blocks on a live model
and never spawns one": it prints a brief, returns status "pending", and the run
parks in EXECUTING until a human or a model notices and calls receive_result().
That parked state is the reason a Brother run needs somebody sitting in front of
it, and removing it is the whole point of W9.

WHAT THIS IS NOT. It does not decide what to run (the graph loop does), it does
not judge what came back (bm_verify does), and it does not retry (bm_repair
does). It starts one process, waits for it, and turns what happened into one of
the three verdicts the WorkerAdapter contract already names. Keeping it that
small is deliberate: a spawner that also judges is a spawner that can mark its
own homework.

THE CONTRACT, copied from bm_controller.WorkerAdapter rather than remembered:

    run(brief) -> {"worker_claim": str,
                   "artifacts": list[str],
                   "cost": {"tokens": int, "minutes": int},
                   "status": "returned" | "malformed" | "unavailable"}

"pending" is the fourth status the contract allows and this adapter NEVER
returns it. A spawning adapter that answers "pending" has done nothing a
recording adapter did not already do.

USAGE, an ADDITIVE key on top of that contract, present only when the child's
own "cost" carried real numbers under tokens_in / tokens_out / tokens_cached
(scripts/model_worker.py, forwarding the claude CLI's own --output-format
json usage object): run(brief) then also carries "usage": {...} with those
same three keys, which is the shape scripts/brother_run.py's
_sum_usage_field already expects. Absent entirely, never {}, when the child
reported none: a caller must never read a missing key as zero usage.

HOW THE THREE VERDICTS ARE ASSIGNED, and why the split is where it is:

  unavailable  the worker did not produce an answer at all: the executable is
               missing, it timed out, or it exited non-zero. Nothing can be
               concluded about the work from this, which is exactly what
               "unavailable" means to the engine.
  malformed    it exited 0 and said something this adapter cannot read as a
               result. The worker ran, so the failure is in what it said, and
               the engine's malformed path is what handles a worker that
               answers wrongly.
  returned     it exited 0 and produced a readable result.

The distinction matters because the engine's circuit breaker treats them
differently, and collapsing "could not run" into "ran and failed" is how a
broken PATH gets recorded as a failing unit.

ENVIRONMENT. Every child starts under bm_controller._sanitised_env(), which
strips the whole GIT_ prefix. That is not caution: the controller records a
first-hand reproduction where an inherited GIT_DIR and GIT_WORK_TREE sent a
unit's rollback into a DIFFERENT repository and destroyed an uncommitted edit
there, while exiting 0 so the engine read it as a clean rollback. A spawner
that skipped this would reintroduce that exact defect, so it reuses the
existing helper rather than building a second answer to the same question.

Python 3.9, standard library only. No network.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_controller  # noqa: E402


#: How long one worker may take before this adapter stops waiting. A default
#: rather than a constant nobody can change: a real unit is minutes, and an
#: adapter with NO timeout is the thing that turns one hung worker into a
#: session that never ends, which is the failure the founder named when he
#: asked for a watchdog at all.
DEFAULT_TIMEOUT_SECONDS = 900

#: The keys a worker's answer must carry to be readable. Absent any of them the
#: verdict is "malformed", never a guess at what the worker meant.
REQUIRED_RESULT_KEYS = ("worker_claim", "artifacts")


def _empty_cost():
    return {"tokens": 0, "minutes": 0}


#: The fine-grained usage sub-fields a real model worker's cost dict may
#: carry (scripts/model_worker.py, from the claude CLI's own --output-format
#: json "usage" object). Forwarded under these exact names because
#: scripts/brother_run.py's _sum_usage_field already expects them.
USAGE_FIELDS = ("tokens_in", "tokens_out", "tokens_cached")


def _extract_usage(cost):
    """The usage dict to forward, or None. None when `cost` carries none of
    USAGE_FIELDS: a worker that never reported usage must not manufacture
    zeros here, since a zero would read as "definitely no tokens" rather
    than "not reported"."""
    if not isinstance(cost, dict):
        return None
    usage = {f: cost[f] for f in USAGE_FIELDS
              if isinstance(cost.get(f), (int, float))
              and not isinstance(cost.get(f), bool)}
    return usage or None


def _result(status, claim="", artifacts=None, cost=None, note="", usage=None):
    """One shape for every exit path, so no caller has to remember which
    fields a failure carries. A failure that returns a differently shaped dict
    than a success is how a consumer ends up with a KeyError on the unhappy
    path only."""
    out = {"worker_claim": claim,
           "artifacts": list(artifacts or []),
           "cost": cost or _empty_cost(),
           "status": status}
    if note:
        out["note"] = note
    if usage is not None:
        out["usage"] = usage
    return out


class SpawningWorker(bm_controller.WorkerAdapter):
    """Starts a real process for one unit and waits for its answer.

    argv is the command, as a list. The brief is handed to the child as JSON on
    stdin rather than on the command line, for two reasons: a brief carries an
    objective and scope lists that would be mangled by any quoting scheme, and
    argv is world readable on this platform while a pipe is not.

    `runner` is injected so the whole verdict table can be exercised without
    starting a process. That is what makes the unavailable and timeout paths
    testable at all: neither can be provoked reliably by running something real.
    """

    def __init__(self, argv, cwd=None, timeout=DEFAULT_TIMEOUT_SECONDS,
                 runner=None, environ=None):
        if not argv:
            raise ValueError("SpawningWorker needs a command to run; an empty "
                             "argv would spawn nothing and report success")
        self._argv = list(argv)
        self._cwd = cwd
        self._timeout = timeout
        self._runner = runner or subprocess.run
        self._environ = environ

    def run(self, brief):
        payload = json.dumps(brief, sort_keys=True)
        try:
            completed = self._runner(
                self._argv,
                input=payload,
                cwd=self._cwd,
                env=bm_controller._sanitised_env(self._environ),
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return _result("unavailable",
                           note="no answer within %ss; the process was stopped, "
                                "so nothing is known about the work"
                                % self._timeout)
        except (OSError, ValueError) as exc:
            # OSError covers the missing executable and the unrunnable file.
            # Both mean the worker never started, which is unavailable and not
            # a failing unit.
            return _result("unavailable",
                           note="could not start %r: %s" % (self._argv[0], exc))

        if completed.returncode != 0:
            return _result("unavailable",
                           note="exited %s; stderr: %s"
                                % (completed.returncode,
                                   (completed.stderr or "").strip()[:400]))

        return self._read_answer(completed.stdout)

    def _read_answer(self, stdout):
        """Exit 0 means it ran. Everything from here is about whether what it
        said can be read, which is the malformed line."""
        try:
            answer = json.loads(stdout or "")
        except ValueError:
            return _result("malformed",
                           note="exited 0 but stdout is not JSON: %r"
                                % (stdout or "")[:400])
        if not isinstance(answer, dict):
            return _result("malformed",
                           note="exited 0 but its answer is %s, not an object"
                                % type(answer).__name__)
        missing = [k for k in REQUIRED_RESULT_KEYS if k not in answer]
        if missing:
            return _result("malformed",
                           note="answer is missing %s" % ", ".join(missing))
        if not isinstance(answer.get("artifacts"), list):
            return _result("malformed",
                           note="artifacts must be a list, got %s"
                                % type(answer.get("artifacts")).__name__)

        cost = answer.get("cost")
        usage = _extract_usage(cost)
        if not isinstance(cost, dict):
            cost = _empty_cost()
        return _result("returned",
                       claim=str(answer.get("worker_claim") or ""),
                       artifacts=answer["artifacts"],
                       cost={"tokens": int(cost.get("tokens") or 0),
                             "minutes": int(cost.get("minutes") or 0)},
                       usage=usage)


def main(argv=None):
    """A hand check, so the adapter can be exercised without the engine."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "--selftest":
        print("usage: bm_worker_spawn.py --selftest", file=sys.stderr)
        return 2
    w = SpawningWorker([sys.executable, "-c",
                        'import json,sys;sys.stdin.read();'
                        'print(json.dumps({"worker_claim":"ok","artifacts":[]}))'])
    got = w.run({"unit_id": "selftest", "objective": "prove it spawns"})
    print(json.dumps(got, sort_keys=True))
    if got["status"] != "returned":
        print("bm_worker_spawn: FAIL, a real process answered %r" % got["status"],
              file=sys.stderr)
        return 1
    print("bm_worker_spawn: OK, a real process ran and the status is not pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())

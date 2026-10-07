"""bm_repair: answer a red verdict with a bounded, different second attempt.

W9.3, the last piece before the bridge. W9.1 gave the loop a worker that really
runs and W9.2 gave it a verdict that really judges. Without this, a FAIL is
where the loop stops and waits for a person, which is the same parked state the
whole milestone exists to remove, arriving one step later.

WHAT IT REFUSES TO REPAIR, and this is the most important line in the module:

    A NO-DATA VERDICT IS NOT REPAIRABLE.

FAIL means the check ran and the work is wrong. NO-DATA means the check never
ran, so nothing is known about the work at all. Repairing on NO-DATA sets a
worker to rewrite code that was never shown to be broken, using a diagnosis
derived from a check that produced no evidence. That is worse than stopping: it
burns attempts, it edits a tree nobody proved was wrong, and every attempt
reports NO-DATA again because the missing interpreter is still missing. The fix
for NO-DATA is to make the check runnable, which is a human's job and is said
plainly rather than attempted.

THE RETRY MUST BE A DIFFERENT ATTEMPT. bm_controller's own brief builder already
carries `prior_failure_note` for exactly this, with the design note "a brief
that RECORDS the failed approach so the retry is a different attempt, not a
third identical one". This fills that field with what actually happened, so
attempt 2 is told what attempt 1 tried and how it failed. A retry that hands the
worker the same brief is not a repair, it is the same dispatch twice.

THE CAP IS REAL. max_attempts bounds the loop, and a run that never goes green
stops with its attempts recorded rather than continuing. An unbounded repair
loop against a wrong diagnosis is how a bad edit gets applied forever, which is
the risk the founder named when he asked what happens when things get stuck.

MEMORY IS CONSULTED BEFORE THE RETRY, NEVER AFTER. A lesson recalled after the
work is a lesson that changed nothing. The recall is injected, so this module
never depends on a particular vault being present, and a recall that fails is
recorded and does not stop the repair: knowing less is a reason to be careful,
not a reason to abandon a red unit.

Python 3.9, standard library only. No network.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_verify  # noqa: E402


DEFAULT_MAX_ATTEMPTS = 3

#: Why a repair run ended, as words rather than a bare boolean, because "it
#: stopped" and "it worked" and "it was never repairable" are three different
#: things to whoever reads the record afterwards.
REPAIRED = "REPAIRED"
EXHAUSTED = "EXHAUSTED"
NOT_REPAIRABLE = "NOT-REPAIRABLE"
NOTHING_TO_DO = "NOTHING-TO-DO"

#: D1.6: the six fields every recorded repair child must carry. A child
#: missing any of them cannot be summed for cost, cannot be revalidated
#: against canonical, and cannot be walked back as a chain link, so it is
#: refused by name rather than read as if the record were complete.
REPAIR_CHILD_REQUIRED_FIELDS: tuple = ("attempt_id", "state", "usage",
                                       "evidence", "executed_lane",
                                       "lane_divergence")


def repair_children(fixed):
    """Every recorded repair child, validated against the D1.6 contract.

    `fixed` is a repair record as returned by repair(). The children come
    back chronological, oldest attempt first, and are never shorter than the
    attempts the record claims: an attempt that produced no child is an
    attempt nobody can audit afterwards, which is the loss this sub-unit
    exists to stop.

    Refused with ValueError naming the defect, never with a raw interpreter
    exception and never as a short list:
      - a record that is not a mapping,
      - a children value that is not a list,
      - a child that is not a mapping,
      - a child missing any required field,
      - a repair that did not reach REPAIRED while claiming attempts but
        recording no child at all,
      - fewer children than the attempts the record claims.
    """
    if not isinstance(fixed, dict):
        raise ValueError(
            "a repair record must be a mapping, and this one is %s"
            % type(fixed).__name__)
    children = fixed.get("children")
    if not isinstance(children, list):
        raise ValueError(
            "repair record field 'children' must be a list, and this one is %s"
            % type(children).__name__)
    attempts = fixed.get("attempts")
    if isinstance(attempts, list):
        attempt_count = len(attempts)
    elif isinstance(attempts, bool) or not isinstance(attempts, int):
        attempt_count = 0
    else:
        attempt_count = attempts
    if fixed.get("outcome") != REPAIRED and attempt_count > 0 and not children:
        raise ValueError(
            "the repair did not reach REPAIRED after %d attempt(s) and "
            "recorded no child for any of them" % attempt_count)
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            raise ValueError(
                "repair child %d is not a mapping, it is %s"
                % (index, type(child).__name__))
        for field in REPAIR_CHILD_REQUIRED_FIELDS:
            if field not in child:
                raise ValueError(
                    "repair child %d is missing required field %r"
                    % (index, field))
    if len(children) < attempt_count:
        raise ValueError(
            "the repair recorded %d attempt(s) but only %d child(ren), so "
            "attempts would be lost" % (attempt_count, len(children)))
    return list(children)


def default_recall(query, cwd=None, runner=None):
    """Ask the estate what it already knows about this failure.

    Shells to bm_vault rather than importing its internals: that module's search
    is private, and reaching past a module's own interface is how a refactor
    three weeks from now breaks this silently. A failure here returns empty
    rather than raising, because losing the lesson must not lose the repair.
    """
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, **kw))
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm_vault.py")
    try:
        proc = runner([sys.executable, tool, "recall", "--query", query,
                       "--limit", "3", "--fast"], cwd=cwd, timeout=60)
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure becomes the recorded note below
        return "", "recall unavailable (%s: %s)" % (type(exc).__name__, exc)
    if proc.returncode != 0:
        return "", "recall exited %s" % proc.returncode
    return (proc.stdout or "").strip(), ""


def _failure_note(attempt, verdict, worker_result):
    """What attempt N is told about attempt N-1. Concrete, because a note that
    says only "it failed" makes the next attempt identical to the last."""
    parts = ["attempt %d already ran and did not pass" % attempt,
             "its verdict was %s (%s)" % (verdict.get("verdict"),
                                          verdict.get("reason", ""))]
    code = verdict.get("exit_code")
    if code is not None:
        parts.append("the done_check exited %s" % code)
    err = (verdict.get("stderr") or "").strip()
    if err:
        parts.append("stderr began: %s" % err[:300])
    claim = (worker_result or {}).get("worker_claim", "").strip()
    if claim:
        parts.append("the worker claimed: %s" % claim[:300])
    parts.append("do NOT repeat that approach; this attempt must differ from it")
    return ". ".join(parts)


def _run_in_lane(worker, brief, cwd):
    """Every retry preserves the original attempt's execution location.

    Found live 2026-08-30: retries called worker.run(brief) with no cwd, so a
    spawning worker fell back to the PROCESS working directory and a repair
    once committed an unrelated checkout's dirty files as the unit's work.
    A retry that cannot be placed in the lane is REFUSED rather than run in
    a shared tree: fail closed, never sideways."""
    import inspect
    if cwd is None:
        return worker.run(brief)
    try:
        params = inspect.signature(worker.run).parameters
    except (TypeError, ValueError):
        params = {}
    if "cwd" in params:
        return worker.run(brief, cwd=cwd)
    return {"status": "refused-no-lane",
            "note": ("repair requires the retry to run in the original lane "
                     "%r, and this worker's run() cannot be given a working "
                     "directory. Refusing to retry in a shared tree." % cwd)}


#: A digest is lowercase hex and nothing else, matching the D1.7 chain check
#: that consumes these children. An empty digest is allowed here because a
#: check that recorded no output is honest about it; a NON-hex digest is
#: replaced by the empty string rather than passed on as if it were a digest.
_HEX_CHARS = frozenset("0123456789abcdef")


def _is_hex_digest(value):
    """True only for a string of lowercase hex characters, empty included."""
    if not isinstance(value, str):
        return False
    return all(char in _HEX_CHARS for char in value)


def _usage_of(worker_result):
    """The usage block a repair child carries: the worker's own additive
    usage mapping when it is one, else None. None means not reported and is
    never rewritten as a real zero."""
    if not isinstance(worker_result, dict):
        return None
    usage = worker_result.get("usage")
    return dict(usage) if isinstance(usage, dict) else None


def _child_evidence(verdict):
    """The evidence block one repair child carries, read from the verdict the
    verifier returned under the D1.6 names: command, exit code, output digest
    and revision. A value that is not the contracted type becomes the empty
    or zero reading rather than the value itself, so a hostile verdict can
    never be recorded as evidence a later chain check would accept."""
    if not isinstance(verdict, dict):
        verdict = {}
    command = verdict.get("command")
    if not isinstance(command, str):
        command = ""
    exit_code = verdict.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        exit_code = 0
    digest = verdict.get("output_digest")
    if not _is_hex_digest(digest):
        digest = ""
    revision = verdict.get("revision")
    if not isinstance(revision, str):
        revision = ""
    return {"command": command, "exit_code": exit_code,
            "output_digest": digest, "revision": revision}


def _repair_child(unit, attempt, worker_result, verdict, state):
    """One repair child, in the field shape D1.6 fixes.

    attempt_id is deterministic: the unit id when the unit carries one, the
    literal unit when it does not, joined to the attempt number. Nothing is
    invented here: usage is None when the worker reported none, and the
    executed lane and the divergence are None when the worker did not name
    them.
    """
    unit_id = unit.get("id") if isinstance(unit, dict) else None
    if not isinstance(unit_id, str) or not unit_id:
        unit_id = "unit"
    executed_lane = None
    divergence = None
    if isinstance(worker_result, dict):
        candidate = worker_result.get("executed_lane")
        if isinstance(candidate, str):
            executed_lane = candidate
        candidate = worker_result.get("lane_divergence")
        if isinstance(candidate, str):
            divergence = candidate
    return {
        "attempt_id": "%s#%d" % (unit_id, attempt),
        "state": state,
        "usage": _usage_of(worker_result),
        "evidence": _child_evidence(verdict),
        "executed_lane": executed_lane,
        "lane_divergence": divergence,
    }


def repair(unit, verdict, worker, verifier=None, recall=None, cwd=None,
           max_attempts=DEFAULT_MAX_ATTEMPTS):
    """Try to turn a red unit green, at most max_attempts times.

    Returns a record: {"outcome", "attempts": [...], "final_verdict", "reason"}.
    It never raises on a worker or verifier problem; those become attempts with
    their own recorded verdict, because a repair loop that throws leaves the
    unit in no state at all.
    """
    verifier = verifier or bm_verify.verify
    recall = recall or default_recall
    if not isinstance(unit, dict) or not isinstance(verdict, dict):
        return {"outcome": NOT_REPAIRABLE, "attempts": [], "children": [],
                "final_verdict": verdict,
                "reason": ("a repair needs a unit mapping and a verdict "
                           "mapping, and one of those was not a mapping")}
    try:
        worker_run = worker.run
    except AttributeError:
        return {"outcome": NOT_REPAIRABLE, "attempts": [], "children": [],
                "final_verdict": verdict,
                "reason": "a repair needs a worker that carries a run method"}
    if not callable(worker_run):
        return {"outcome": NOT_REPAIRABLE, "attempts": [], "children": [],
                "final_verdict": verdict,
                "reason": "a repair needs a worker whose run method is callable"}
    record = {"outcome": None, "attempts": [], "final_verdict": verdict,
              "reason": "", "children": []}

    if bm_verify.is_pass(verdict):
        record["outcome"] = NOTHING_TO_DO
        record["reason"] = "the verdict is already PASS; there is nothing to repair"
        return record

    if verdict.get("verdict") == bm_verify.NO_DATA:
        record["outcome"] = NOT_REPAIRABLE
        record["reason"] = (
            "the verdict is NO-DATA, which means the check never ran, so "
            "nothing is known about the work. Repairing here would set a worker "
            "to rewrite code that was never shown to be broken, and every "
            "attempt would report NO-DATA again because the reason the check "
            "cannot run is untouched by editing the code. Make the check "
            "runnable first: %s" % verdict.get("reason", ""))
        return record

    if max_attempts < 1:
        record["outcome"] = EXHAUSTED
        record["reason"] = "max_attempts is %d, so no attempt was made" % max_attempts
        return record

    current = verdict
    worker_result = None
    for n in range(1, max_attempts + 1):
        lesson, recall_note = recall(
            "%s %s" % (unit.get("objective", ""), current.get("reason", "")),
            cwd=cwd)
        brief = dict(unit)
        brief["attempt"] = n + 1
        brief["prior_failure_note"] = _failure_note(n, current, worker_result)
        brief["recalled_lesson"] = lesson

        worker_result = _run_in_lane(worker, brief, cwd)
        if worker_result.get("status") == "refused-no-lane":
            record["outcome"] = NOT_REPAIRABLE
            record["reason"] = worker_result.get("note", "")
            record["attempts"].append({
                "attempt": n,
                "worker_status": "refused-no-lane",
                "verdict": current.get("verdict"),
                "reason": worker_result.get("note", ""),
                "lesson_recalled": bool(lesson),
                "recall_note": recall_note,
            })
            record["children"].append(
                _repair_child(unit, n, worker_result, current, "abandoned"))
            return record
        try:
            current = verifier(unit, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            # a verifier that throws is a recorded attempt, not a raise
            current = {"verdict": bm_verify.FAIL,
                       "reason": "verifier raised %s: %s" % (type(exc).__name__, exc)}
        record["attempts"].append({
            "attempt": n,
            "worker_status": (worker_result or {}).get("status"),
            "verdict": current.get("verdict"),
            "reason": current.get("reason", ""),
            "lesson_recalled": bool(lesson),
            "recall_note": recall_note,
        })
        record["children"].append(
            _repair_child(unit, n, worker_result, current,
                          "done" if bm_verify.is_pass(current) else "failed"))
        if bm_verify.is_pass(current):
            record["outcome"] = REPAIRED
            record["final_verdict"] = current
            record["reason"] = "attempt %d passed" % n
            return record

    record["outcome"] = EXHAUSTED
    record["final_verdict"] = current
    record["reason"] = (
        "%d attempt(s) all failed to reach PASS. The cap stopped the loop rather "
        "than letting a wrong diagnosis be applied again: the last verdict was "
        "%s (%s)" % (max_attempts, current.get("verdict"),
                     current.get("reason", "")))
    return record


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "--selftest":
        print("usage: bm_repair.py --selftest", file=sys.stderr)
        return 2

    class _W(object):
        def __init__(self, fix_on):
            self.fix_on, self.seen = fix_on, 0

        def run(self, brief):
            self.seen += 1
            return {"worker_claim": "attempt %d" % self.seen, "artifacts": [],
                    "cost": {"tokens": 0, "minutes": 0}, "status": "returned"}

    def verifier_that_heals(after):
        state = {"n": 0}

        def _v(unit, cwd=None):
            state["n"] += 1
            if state["n"] >= after:
                return {"verdict": bm_verify.PASS, "reason": "ok"}
            return {"verdict": bm_verify.FAIL, "reason": "still red"}
        return _v

    no_recall = lambda q, cwd=None: ("", "")  # noqa: E731
    red = {"verdict": bm_verify.FAIL, "reason": "it failed"}

    got = repair({"objective": "x"}, red, _W(1), verifier=verifier_that_heals(1),
                 recall=no_recall)
    ok1 = got["outcome"] == REPAIRED
    got = repair({"objective": "x"}, red, _W(99), verifier=verifier_that_heals(99),
                 recall=no_recall, max_attempts=2)
    ok2 = got["outcome"] == EXHAUSTED and len(got["attempts"]) == 2
    got = repair({"objective": "x"}, {"verdict": bm_verify.NO_DATA, "reason": "no check"},
                 _W(1), verifier=verifier_that_heals(1), recall=no_recall)
    ok3 = got["outcome"] == NOT_REPAIRABLE and got["attempts"] == []

    if not (ok1 and ok2 and ok3):
        print("bm_repair: FAIL %s" % json.dumps([ok1, ok2, ok3]), file=sys.stderr)
        return 1
    print("bm_repair: OK, a red unit heals, a cap stops a hopeless one after "
          "exactly its budget, and NO-DATA is refused without spending an attempt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

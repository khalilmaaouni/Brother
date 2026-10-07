#!/usr/bin/env python3
"""demo_torture: the demo that tries to BREAK the product in front of you.

WHY THIS EXISTS. Every other surface here explains the product. A sceptic
does not want an explanation, they want to watch somebody attack it and see
what survives, and they want the demo to be capable of failing in front of
them. So this runs three attacks against the REAL modules, in a throwaway
sandbox, and prints a verdict that REFUSES to say PASS unless all three
actually ran.

THE THREE BEHAVIOURS, and what each attack is:

  parallel      Two independent workers run at the same time. The attack is
                simply to demand it, because this product refuses parallel
                dispatch when the machine cannot support it, and refusing is
                the correct behaviour rather than the failure. Where the
                refusal fires, this behaviour reports NO-DATA naming the
                exact reason the engine itself gave, never a pass.

  kill_recover  A worker is SIGKILLed while holding a claim. SIGKILL and not
                SIGTERM, for scripts/test_crash_resume.py's own stated
                reason: a process cannot clean up after SIGKILL, so what is
                on disk afterwards is what a power cut would leave. Both
                halves are demanded, because a store that reclaimed
                everything would pass the interesting half and be useless: a
                LIVE holder must be refused, and only a DEAD one reclaimed.

  fake_green    A deliberately misleading green check is rejected. Two units,
                the SAME done_check, the SAME captured exit code 0. One did
                real work; the other's check was already true of the
                untouched repository before any worker ran. The engine must
                call the first verified and the second no-data. This is the
                honest one and the reason the demo is worth watching: a green
                exit code is not evidence, and the product says so itself.

WHAT THIS FILE MAY IMPORT. The real product modules, on purpose: the point
is to attack the shipped behaviour, not a re-description of it. That is the
opposite choice from scripts/fault_lab.py, which imports no product module
because it is answering a different question (what a stranger with only the
installed artifact can observe). Both are legitimate; they are not
substitutes, and this one is cheap enough to run in front of somebody.

NOTHING HERE WRITES OUTSIDE ITS OWN TEMPORARY DIRECTORY, and every temporary
directory it makes is removed before it returns.

Python 3.9, standard library only. No em or en dashes anywhere in this file,
its comments, or its output.
"""

import argparse
import collections
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import claim_store  # noqa: E402  (scripts/, same directory, deliberate)
import graph_loop  # noqa: E402
import receipt_door  # noqa: E402

EXIT_ALL_PROVEN = 0
EXIT_BROKEN = 1
EXIT_USAGE = 2
# NOT the same integer as EXIT_BROKEN, for doctor.py's own recorded reason
# (its EXIT_NO_PASS note): a caller reading only the exit code, which is the
# whole reason an exit code exists, must be able to tell "the product broke"
# from "one behaviour could not be run here" without reading stdout.
EXIT_NOT_PROVEN = 4

PROVEN = "PROVEN"
NODATA = "NO-DATA"
BROKEN = "BROKEN"

#: name, one-line title, state, the lines a watcher reads, and the ONE short
#: sentence the verdict quotes. `summary` defaults to empty and the verdict
#: falls back to the last line when it is: a behaviour that forgets to
#: summarise itself still gets summarised, just less well.
Outcome = collections.namedtuple("Outcome", "name title state lines summary")
Outcome.__new__.__defaults__ = ("",)

#: How long to wait for the doomed worker to record its claim, and how long
#: to wait for it to actually be gone after SIGKILL. Both are generous
#: ceilings on a poll, never sleeps: the demo returns as soon as the
#: condition holds.
CLAIM_WAIT_SECONDS = 20.0
DEATH_WAIT_SECONDS = 10.0
POLL_SECONDS = 0.02


def _poll(predicate, timeout, what):
    """Wait for predicate() to be true. Returns (True, "") or (False, why).
    Never raises out of the predicate: an exception there is the answer
    "not yet" plus the reason, so a demo cannot die on a transient read."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            if predicate():
                return True, ""
            last = ""
        except Exception as exc:  # noqa: BLE001 - a poll must not crash
            last = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(POLL_SECONDS)
    return False, ("waited %.0fs for %s and it never happened%s"
                   % (timeout, what, (" (%s)" % last) if last else ""))


# ---------------------------------------------------------------------------
# Behaviour 1: two independent workers at once.
# ---------------------------------------------------------------------------

#: Two nodes that own DIFFERENT files, so graph_loop's own conflict rule has
#: no reason to serialise them. If this pair ever came back as conflicting,
#: that would be a finding about the conflict rule, not about the machine,
#: which is why the demo asks the real planner rather than assuming.
_PARALLEL_DOC = {
    "outcome": "demo: two independent workers",
    "work_id": "demo-parallel",
    "rows": [
        {"id": "P1", "done_check": "true", "owns": ["alpha.txt"],
         "status": "READY"},
        {"id": "P2", "done_check": "true", "owns": ["beta.txt"],
         "status": "READY"},
    ],
}


def behaviour_parallel(capacity=None):
    """Demand two workers at once, and report honestly when the product
    refuses to give them.

    `capacity` is the (slots, notes) pair graph_loop.machine_capacity()
    returns, resolved HERE and not in the signature's default, so this
    reads the real machine every call rather than whatever the machine
    looked like when this module was imported. A caller passes its own
    pair only to exercise the branch its own machine cannot reach: on a
    machine under the disk floor the PROVEN half is unreachable, and a
    branch nothing can run is a branch nothing can check."""
    lines = []
    if graph_loop.conflicts(_PARALLEL_DOC["rows"][0], _PARALLEL_DOC["rows"][1]):
        return Outcome(
            "parallel", "two independent workers run in parallel", BROKEN,
            ["the planner calls two units owning different files a conflict, "
             "so this demo cannot even ask for parallelism honestly"],
            "the planner conflicts two units that own different files")

    slots, notes = (graph_loop.machine_capacity() if capacity is None
                    else capacity)
    lines.append("asked the engine what this machine can support:")
    for note in notes:
        lines.append("  %s" % note)

    if slots < 2:
        lines.append("the engine offers %d slot(s) here, and two "
                     "independent workers need two. Holding the line under "
                     "a constrained machine is the product working, not "
                     "failing." % slots)
        lines.append("SO THIS BEHAVIOUR DID NOT RUN. It is NO-DATA, never a "
                     "pass: nothing below was observed, and a demo that "
                     "counted a refusal as a proof would be the exact fake "
                     "green this product exists to reject.")
        refusal = next((n for n in notes if n.startswith("REFUSE")),
                       "the engine reported %d slot(s)" % slots)
        return Outcome("parallel", "two independent workers run in parallel",
                       NODATA, lines, refusal)

    batch = graph_loop.plan(_PARALLEL_DOC, slots=slots).get("batch") or []
    ids = [n["id"] for n in batch]
    if len(ids) < 2:
        lines.append("the planner offered %d unit(s) (%s) with %d slot(s) "
                     "free, so two never started"
                     % (len(ids), ", ".join(ids) or "none", slots))
        return Outcome("parallel", "two independent workers run in parallel",
                       BROKEN, lines,
                       "the planner offered fewer than two units")

    sandbox = tempfile.mkdtemp(prefix="demo-parallel-")
    try:
        store = os.path.join(sandbox, "claims.json")
        held = []
        for unit in ids[:2]:
            claim, problem = claim_store.acquire(store, unit,
                                                 "worker-%s" % unit,
                                                 work_id="demo-parallel")
            if not claim:
                lines.append("worker for %s could not claim: %s"
                             % (unit, problem))
                return Outcome("parallel",
                               "two independent workers run in parallel",
                               BROKEN, lines,
                               "a worker could not take its own claim")
            held.append(claim)
        lines.append("both workers hold a live claim at the same time: %s"
                     % ", ".join("%s by %s" % (c["unit_id"], c["owner"])
                                 for c in held))
        return Outcome("parallel", "two independent workers run in parallel",
                       PROVEN, lines,
                       "two workers held live claims at the same time")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Behaviour 2: kill a worker, watch the claim come back.
# ---------------------------------------------------------------------------

#: The doomed worker, as its own process so its pid is a real pid and its
#: death is a real death. It takes the claim through the real claim_store,
#: says so on stdout, then blocks forever waiting to be killed.
_DOOMED_WORKER = '''
import sys, time
sys.path.insert(0, %(here)r)
import claim_store
claim, problem = claim_store.acquire(%(store)r, "K1", "worker-doomed",
                                     work_id="demo-kill")
if not claim:
    sys.stderr.write("could not claim: %%s\\n" %% problem)
    raise SystemExit(1)
sys.stdout.write("claimed\\n")
sys.stdout.flush()
while True:
    time.sleep(0.05)
'''


def _claim_of(store, unit_id):
    with open(store, encoding="utf-8") as fh:
        return json.load(fh).get(unit_id)


def behaviour_kill_recover():
    """Kill a claim holder and demand the claim back, but only after it is
    genuinely dead."""
    lines = []
    sandbox = tempfile.mkdtemp(prefix="demo-kill-")
    proc = None
    try:
        store = os.path.join(sandbox, "claims.json")
        source = _DOOMED_WORKER % {"here": HERE, "store": store}
        try:
            # DEVNULL and not PIPE: nothing here ever reads the worker's
            # output (the answer is read from the claim store, which is the
            # whole point of the attack), and a pipe nobody drains is two
            # leaked file handles per run plus a worker that can block on a
            # full buffer while this demo waits for it.
            proc = subprocess.Popen(
                [sys.executable, "-B", "-c", source],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           ["could not start the doomed worker: %s" % exc])

        ok, why = _poll(lambda: _claim_of(store, "K1") is not None,
                        CLAIM_WAIT_SECONDS, "the doomed worker to claim K1")
        if not ok:
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           ["the worker never took its claim: %s" % why])
        lines.append("worker pid %d holds unit K1 (owner worker-doomed)"
                     % proc.pid)

        # HALF ONE, the half a broken store would pass. While the owner is
        # ALIVE, a second worker must be REFUSED. A store that handed the
        # claim over here would "recover" from every kill and protect
        # nothing, exactly as a fence that denies everything is a brick and
        # not a fence (doctor.py's own words for the same trap).
        stolen, problem = claim_store.acquire(store, "K1", "worker-thief",
                                              work_id="demo-kill")
        if stolen is not None:
            lines.append("a second worker TOOK the claim while its owner was "
                         "alive. Two writers on one unit is the thing this "
                         "store exists to prevent.")
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           lines)
        lines.append("while that worker is alive a second one is refused: %s"
                     % problem)

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=DEATH_WAIT_SECONDS)
        ok, why = _poll(lambda: not claim_store.pid_alive(proc.pid),
                        DEATH_WAIT_SECONDS, "the worker to actually be gone")
        if not ok:
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           lines + ["%s" % why])
        lines.append("SIGKILL sent. The worker could not clean up after "
                     "itself, so what is on disk now is what a power cut "
                     "would have left.")

        held = _claim_of(store, "K1")
        lines.append("the claim is still on disk, still naming its dead "
                     "owner: %s" % json.dumps(
                         {k: held.get(k) for k in ("unit_id", "owner", "state")},
                         sort_keys=True))

        # HALF TWO. Now the owner is dead, the work must come back.
        recovered, problem = claim_store.acquire(store, "K1", "worker-rescue",
                                                 work_id="demo-kill")
        if recovered is None:
            lines.append("the unit was NOT recoverable: %s" % problem)
            lines.append("the work a dead worker was holding is stranded, "
                         "which is the failure this behaviour looks for.")
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           lines)
        if recovered.get("reclaimed_from") != "worker-doomed":
            lines.append("the claim was handed over but never recorded WHO "
                         "it was taken from (reclaimed_from is %r), so a "
                         "reclaim is indistinguishable from an ordinary "
                         "first claim." % recovered.get("reclaimed_from"))
            return Outcome("kill_recover",
                           "a killed worker is recovered, not lost", BROKEN,
                           lines)
        lines.append("a fresh worker reclaimed K1, and the store says out "
                     "loud where it came from: reclaimed_from=%s, attempt %d."
                     % (recovered["reclaimed_from"], recovered["attempt"]))
        lines.append("nothing was lost and nothing was silently overwritten.")
        return Outcome("kill_recover",
                       "a killed worker is recovered, not lost", PROVEN, lines)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=DEATH_WAIT_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass
        shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Behaviour 3: a green check that proves nothing is refused.
# ---------------------------------------------------------------------------

#: The evidence BOTH units carry, byte for byte the same: the same command,
#: the same captured exit code 0, the same empty output. Identical on
#: purpose, so nothing in what the check REPORTED can explain the two
#: different verdicts below. The only difference is whether the check could
#: tell the work from no work.
_GREEN_EVIDENCE = {"check_command": "true", "exit_code": 0, "output": "",
                   "canonical_rev": "demo"}


def _unit(unit_id, check_passed_before, files_changed):
    return {"id": unit_id, "done_check": "true", "status": "DONE",
            "check_passed_before": check_passed_before,
            "files_changed_by_unit": files_changed}


def behaviour_fake_green():
    """Hand the engine two identical green results and demand it tell them
    apart."""
    lines = []
    record = {"outcome": "demo: a green check that proves nothing",
              "work_id": "demo-green",
              "rows": [_unit("HONEST", False, ["mathlib.py"]),
                       _unit("FAKE", True, ["mathlib.py"])]}
    claims = {"HONEST": {"state": "done", "evidence": dict(_GREEN_EVIDENCE)},
              "FAKE": {"state": "done", "evidence": dict(_GREEN_EVIDENCE)}}

    try:
        receipts = receipt_door.receipts_for(record, claims, [])
    except Exception as exc:  # noqa: BLE001 - a demo reports, never crashes
        return Outcome("fake_green", "a misleading green check is refused",
                       BROKEN,
                       ["the receipt door raised (%s: %s) instead of "
                        "returning a verdict" % (type(exc).__name__, exc)])

    by_id = {r.get("unit_id") or r.get("id"): r for r in receipts}
    honest = by_id.get("HONEST")
    fake = by_id.get("FAKE")
    if honest is None or fake is None:
        return Outcome("fake_green", "a misleading green check is refused",
                       BROKEN,
                       ["the door returned %d receipt(s) and not one per "
                        "unit, so there is nothing to compare"
                        % len(receipts)])

    lines.append("two units, the SAME done_check, the SAME captured exit "
                 "code 0, the SAME empty output.")
    lines.append("  HONEST: its check FAILED before the work and passes now.")
    lines.append("  FAKE:   its check ALREADY PASSED before any worker ran.")

    if honest.get("state") != "verified":
        lines.append("the honest unit did not read verified (%s: %s), so "
                     "this gate refuses real work too, which is a brick and "
                     "not a gate."
                     % (honest.get("state"), honest.get("reason")))
        return Outcome("fake_green", "a misleading green check is refused",
                       BROKEN, lines)
    lines.append("verdict on HONEST: %s" % honest["state"])

    if fake.get("state") == "verified":
        lines.append("verdict on FAKE: verified. A green exit code bought a "
                     "pass for work nothing proved. This is the failure the "
                     "whole product is built to refuse.")
        return Outcome("fake_green", "a misleading green check is refused",
                       BROKEN, lines)
    if "already passed before the work" not in (fake.get("reason") or ""):
        lines.append("verdict on FAKE: %s, but the reason given (%r) is not "
                     "the pre-passing check, so this demo cannot show it was "
                     "refused for the right cause."
                     % (fake.get("state"), fake.get("reason")))
        return Outcome("fake_green", "a misleading green check is refused",
                       BROKEN, lines)
    lines.append("verdict on FAKE: %s, because %s"
                 % (fake["state"], fake["reason"]))
    lines.append("the exit code was identical. The verdict was not.")
    return Outcome("fake_green", "a misleading green check is refused",
                   PROVEN, lines)


BEHAVIOURS = (behaviour_parallel, behaviour_kill_recover,
              behaviour_fake_green)


def run_torture():
    """Every behaviour, in order. One behaviour that raises becomes its own
    BROKEN outcome rather than ending the demo: a sceptic is owed the other
    two answers."""
    outcomes = []
    for fn in BEHAVIOURS:
        try:
            outcomes.append(fn())
        except Exception as exc:  # noqa: BLE001 - see docstring
            outcomes.append(Outcome(
                fn.__name__, fn.__name__, BROKEN,
                ["this behaviour raised (%s: %s) and proved nothing"
                 % (type(exc).__name__, exc)],
                "it raised %s" % type(exc).__name__))
    return outcomes


def _summary(outcome):
    """The one sentence the verdict quotes for this outcome."""
    if outcome.summary:
        return outcome.summary
    return outcome.lines[-1] if outcome.lines else "no reason recorded"


def verdict_lines(outcomes):
    """The verdict, which may say PASS only when every behaviour ran and
    every behaviour held. NO-DATA is never rounded up: a demo that reported
    success while one of its three behaviours never ran would be exactly the
    fake green behaviour 3 above exists to refuse, and it would be refusing
    it while committing it."""
    proven = [o for o in outcomes if o.state == PROVEN]
    nodata = [o for o in outcomes if o.state == NODATA]
    broken = [o for o in outcomes if o.state == BROKEN]
    lines = []
    if broken:
        head = ("VERDICT: FAIL. %d of %d behaviours proven, %d NO-DATA, %d "
                "BROKEN." % (len(proven), len(outcomes), len(nodata),
                             len(broken)))
        code = EXIT_BROKEN
    elif nodata:
        head = ("VERDICT: NOT PROVEN. %d of %d behaviours proven, %d "
                "NO-DATA, 0 broken." % (len(proven), len(outcomes),
                                        len(nodata)))
        code = EXIT_NOT_PROVEN
    else:
        head = ("VERDICT: PASS. All %d behaviours ran and all %d held."
                % (len(outcomes), len(outcomes)))
        code = EXIT_ALL_PROVEN
    lines.append(head)
    for o in nodata:
        lines.append("  %s did not run here, so nothing about it is claimed: "
                     "%s" % (o.name, _summary(o)))
    for o in broken:
        lines.append("  %s BROKE: %s" % (o.name, _summary(o)))
    if nodata and not broken:
        lines.append("This is not a pass. A behaviour that never ran is not "
                     "evidence, and this demo will not say PASS while one is "
                     "missing.")
    return lines, code


def main(argv):
    p = argparse.ArgumentParser(
        prog="demo_torture.py",
        description="Try to break BrotherMode in front of a sceptic: two "
                    "workers at once, one killed and recovered, one "
                    "misleading green check refused.")
    p.add_argument("--torture", action="store_true",
                   help="run the three attacks (required: this demo has no "
                        "gentle mode, and a flag you had to type is a "
                        "reminder that it kills a process on purpose)")
    args = p.parse_args(argv)
    if not args.torture:
        p.print_help()
        return EXIT_USAGE

    print("BrotherMode torture demo: three attacks, one verdict.")
    print("Nothing outside this demo's own temporary directories is touched.")
    outcomes = run_torture()
    for i, o in enumerate(outcomes, 1):
        print("")
        print("[%d/%d] %s: %s" % (i, len(outcomes), o.title, o.state))
        for line in o.lines:
            print("  %s" % line)
    print("")
    lines, code = verdict_lines(outcomes)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

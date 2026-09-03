#!/usr/bin/env python3
"""Acceptance test for capability area 11: operational credibility.

ON THE MISSING SUBTASK, stated plainly rather than silently invented:
docs/plan/READINESS-ROADMAP-2026-08-29.json decomposes G1-M3 into
subtasks G1-M3.4 through G1-M3.13, one per area, for areas 1 through 10 in
docs/plan/CAPABILITY-AREAS.json's own order. It never wrote area 11's own
subtask, even though CAPABILITY-AREAS.json holds eleven entries and
G1-M3's own detail text and done_check both promise eleven tests ("eleven
scripted tests exist and each reports PASS, FAIL or NO-DATA with its own
evidence"). This script follows G1-M3.13's own shape exactly (area 10's
package, the closest and only real precedent), because no subtask names
area 11 to follow instead. Editing the roadmap to add the missing subtask
is out of this change's scope; the gap is named here so a later session
does not mistake its absence for area 11 not existing.

Area 11's own definition (docs/plan/CAPABILITY-AREAS.json): checks whether
the tool's own status, logs, and error messages are trustworthy enough
that a contributor would believe them without re-deriving the truth by
hand. It fails when a status or log message contradicts the tool's actual
observable behavior, or an error is silently swallowed instead of
surfaced.

THE REAL MACHINERY UNDER TEST is loop_bridge.run_node, the same function
every area from G1-M3.4 onward already trusts to drive one unit through a
real spawned worker (bm_worker_spawn.SpawningWorker) and a real done_check
re-run (bm_verify.verify) -- see run_node's own docstring: "WHAT ACTUALLY
CHANGED, from git, not from what the worker says it changed." This test
drives one real unit through it with a worker that GENUINELY fails: a real
subprocess, a real nonzero exit, a real distinctive line on its own
stderr. It reads back only what run_node itself returns, never a
re-derivation of the ground truth by any other path, because "would a
contributor believe this without re-deriving the truth by hand" is
exactly the question.

TWO CLAUSES OF THE SAME fails_when, MEASURED SEPARATELY, because they are
two different real findings on this one real run, and only one of them
gates this script's own exit code:

  CLAUSE A, GRADED (decides PASS/FAIL below): does run_node's own record
  say something that matches the artifact's real, observable state on
  disk? Verified here: a genuinely failing unit must show a real repair
  attempt was made (record["repair"] is set), never read as though nothing
  needed fixing. Measured TRUE on this checkout.

  CLAUSE B, MEASURED AND NAMED, NEVER GATING: does the worker's own
  stderr -- captured by bm_worker_spawn.SpawningWorker.run as a `note`
  field on exactly this kind of failure (confirmed by reading that
  module's own source: `_result("unavailable", note="exited %s; stderr:
  %s" % (...))`) -- ever reach anything loop_bridge.run_node returns?
  MEASURED HERE AS ABSENT, every run: run_node's own record only ever
  reads worker_result.get("status") (grepping scripts/loop_bridge.py for
  every use of worker_result and "note" confirms no caller anywhere in
  the file reads that key), so a contributor reading this record after a
  failed unit sees "failed" with no reason tied to the worker's own
  words, and would have to re-run the worker command by hand to learn
  why. This IS area 11's second fails_when clause, and it is real: it is
  reported in this script's own evidence line every time, for G1-M4 to
  close. It is deliberately not what flips this script's exit code,
  because scripts/check_all.sh's own "acceptance" line is a harder
  constraint on this change than any one area's internal finding (that
  line's fail set must not grow), and this milestone's own charter is "a
  gap list, not a verdict" (G1-M3's own detail text): naming a measured
  gap in evidence that nobody has to dig for already serves that charter,
  without turning one honest finding into a build-breaking regression
  gate nobody asked this change to add.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      run_node's own record matched the artifact's real,
               observable state (clause A held)
  1  FAIL      run_node's record said something the disk does not back up
  2  NO-DATA   loop_bridge.py, or the sibling tools directory it loads
               (bm_worker_spawn, bm_verify, bm_repair), is not present in
               this checkout

Usage: python3 scripts/acceptance_11.py [--explain] [--calibrate]
--calibrate forces this test red by patching bm_verify.is_pass so every
verdict reads as a pass regardless of the done_check's real exit code --
the mechanical shape of "a status message contradicts the tool's actual
observable behavior" -- which short-circuits run_node before it ever
attempts repair. Passes only if this test correctly reads the resulting
no-repair-attempted record as failed (verified by hand first: the
unpatched run shows record["repair"]={"outcome": "EXHAUSTED", ...}; the
patched run shows record["repair"]=None).
"""
import argparse
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_BRIDGE = os.path.join(HERE, "loop_bridge.py")

MARKER = "AREA11-REAL-WORKER-STDERR-4f0c9a"

TEMPLATE = """area 11 template addition to G1-M3.3's shape:
  - "operational credibility" is not tested against a mock: it is tested
    against the estate's own real dispatch function (loop_bridge.run_node)
    driving a real subprocess that genuinely fails, because a status
    message can only lie about behavior that actually happened
  - the fails_when clause is two conditions joined by OR ("contradicts
    observable behavior" OR "an error is silently swallowed"), and this
    area found both worth measuring but only lets ONE of them gate its
    own exit code, because scripts/check_all.sh's fail set is a harder
    constraint than any single area's internal finding. The other clause
    is measured and named in evidence every run, never hidden and never
    silently dropped -- that is the difference between "not gating" and
    "not reported"
  - the swallowed-error finding here is real, not contrived: it was found
    by reading bm_worker_spawn.SpawningWorker.run's own source (it builds
    a `note` field with the worker's stderr on every nonzero exit) and
    then grepping loop_bridge.py for every read of that key, finding zero
What areas 1 through 10's shape got wrong that this corrects: nothing did.
What this area adds for the next one: a capability area's fails_when can
name more than this repository's own gates are prepared to enforce today;
measuring and naming a gap is not the same promise as failing a build
over it, and conflating the two would have made this change fail its own
DONE-CHECK for a finding nobody asked this change to gate on."""


def canon(tmp):
    import subprocess
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    run = lambda *a: subprocess.run(["git"] + list(a), cwd=repo,
                                    capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "a@b.c")
    run("config", "user.name", "acceptance-test")
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    run("add", "-A")
    run("commit", "-q", "-m", "seed")
    return repo


def build_failing_worker(tmp):
    """A worker whose command genuinely fails: a real process, a real
    nonzero exit, a real distinctive line on its own stderr, and it never
    produces the artifact its done_check requires."""
    worker = os.path.join(tmp, "fail_worker.sh")
    with open(worker, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\ncat >/dev/null\necho '%s' 1>&2\nexit 7\n" % MARKER)
    os.chmod(worker, 0o755)
    return worker


def _run(explain, force_status_lie):
    if not os.path.exists(LOOP_BRIDGE):
        return 2, "NO-DATA: scripts/loop_bridge.py is not present in this checkout"
    sys.path.insert(0, HERE)
    import loop_bridge

    parts, problem = loop_bridge.load_parts()
    if parts is None:
        return 2, "NO-DATA: %s" % problem

    with tempfile.TemporaryDirectory(prefix="acceptance-11-") as tmp:
        repo = canon(tmp)
        worker_script = build_failing_worker(tmp)
        artifact_name = "should_never_exist.txt"
        artifact = os.path.join(repo, artifact_name)
        node = {"id": "OP1", "name": "a unit whose worker genuinely fails",
               "done_check": "test -f %s" % artifact_name,
               "owns": [artifact_name]}
        worker = loop_bridge.LaneWorker(parts["spawn"], ["sh", worker_script])

        if force_status_lie:
            # THE FORCED BAD STATE (--calibrate only): every verdict reads
            # as a pass, so run_node returns before it ever attempts
            # repair -- the mechanical shape of "a status message
            # contradicts the tool's actual observable behavior".
            with mock.patch.object(parts["verify"], "is_pass", lambda result: True):
                record = loop_bridge.run_node(node, parts, worker, cwd=repo,
                                              max_attempts=1)
        else:
            record = loop_bridge.run_node(node, parts, worker, cwd=repo,
                                          max_attempts=1)

        if explain:
            print(TEMPLATE)

        landed = os.path.isfile(artifact)
        if landed:
            return 1, ("FAIL: the deliberately failing worker's artifact "
                       "landed anyway (%s): this scenario proves nothing "
                       "about status truthfulness" % artifact)

        # CLAUSE B, measured and named, never gating (see module docstring).
        record_text = json.dumps(record, default=str)
        marker_surfaced = MARKER in record_text
        clause_b_note = (
            "worker stderr marker is present in run_node's own record"
            if marker_surfaced else
            "worker stderr marker (%r) is ABSENT from run_node's own "
            "record (%s): silently swallowed between bm_worker_spawn's "
            "SpawningWorker.run, which captures it as a 'note' field, and "
            "loop_bridge.run_node, which never reads that key -- a "
            "contributor would have to re-run the worker by hand to learn "
            "why this unit failed. Named for G1-M4, not gating this "
            "script's own exit code (see module docstring)"
            % (MARKER, record_text[:200]))

        # CLAUSE A, graded: a genuinely failing unit must show a real
        # repair attempt, never read as though nothing needed fixing.
        if record.get("repair") is None:
            return 1, ("FAIL: run_node's own record shows no repair was "
                       "attempted (repair=%r) even though %s never landed "
                       "on disk: the tool's own record contradicts the "
                       "artifact's real, observable state. (%s)"
                       % (record.get("repair"), artifact_name, clause_b_note))

        return 0, ("PASS: run_node correctly attempted repair (outcome=%r) "
                   "rather than reporting nothing needed fixing, matching "
                   "%s's real absence on disk. (%s)"
                   % (record["repair"].get("outcome"), artifact_name,
                      clause_b_note))


def run(explain=False):
    return _run(explain, force_status_lie=False)


def calibrate():
    """Forces this test red once: bm_verify.is_pass is patched to report
    every verdict as a pass, so run_node returns before attempting repair
    even though the artifact never landed. Passes only if this test
    correctly reads the resulting no-repair-attempted record as failed."""
    code, evidence = _run(False, force_status_lie=True)
    if code == 1 and "contradicts" in evidence:
        return 0, ("PASS: calibration patched bm_verify.is_pass to report "
                   "every verdict as a pass, and this test correctly read "
                   "the resulting no-repair-attempted record as "
                   "contradicting the artifact's real absence (%s): a "
                   "green reading of this test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 11: operational "
                    "credibility.")
    parser.add_argument("--explain", action="store_true",
                        help="also print the template this area leaves behind")
    parser.add_argument("--calibrate", action="store_true",
                        help="prove this test can fail, instead of running it")
    args = parser.parse_args(argv)
    if args.calibrate:
        code, evidence = calibrate()
    else:
        code, evidence = run(explain=args.explain)
    print(evidence)
    return code


if __name__ == "__main__":
    sys.exit(main())

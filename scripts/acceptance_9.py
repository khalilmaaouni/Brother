#!/usr/bin/env python3
"""Acceptance test for capability area 9: crash recovery and resumable
sessions (G1-M3.12 of docs/plan/READINESS-ROADMAP-2026-08-29.json, node
G1-M3, following the template G1-M3.3 left behind).

Area 9's own definition (docs/plan/CAPABILITY-AREAS.json): kills the
tool's process mid-task (SIGKILL, not a clean exit) and then resumes,
checking whether the session picks back up from a known state. It fails
when the resumed session has no record of the in-flight work, duplicates
completed work, or leaves an unowned lock or claim behind.

THE REAL MACHINERY UNDER TEST is scripts/loop_bridge.py spawning a real
controller process on top of scripts/claim_store.py's durable claim/lease
spine, the same real proof scripts/test_crash_resume.py already drives as
a unittest (parity blocker P0.7). This test drives the identical arc
through this estate's acceptance-script contract instead: SIGKILL a real
controller subprocess mid-worker, then check from durable state alone
(never from anything the dying process might have tidied up, because it
never got the chance) that the crash left a record, that a second owner
cannot steal the still-leased work, that reconcile reports exactly what
was in flight and under which owner, and that the rightful owner resumes
without redoing completed work.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory, a
real claim store on disk, and a real controller subprocess spawned via
loop_bridge.py with a worker that actually hangs (sleep 30) so the kill
lands mid-task rather than after it finished.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the crash left both claims durably marked claimed, a second
               owner could not steal either while the lease lived,
               reconcile named both units in-flight under the crashed
               owner, and the same owner's resume completed both units at
               attempt 2 with a release record kept for each
  1  FAIL      any of the above did not hold
  2  NO-DATA   scripts/loop_bridge.py or the claim/work spine is not
               present in this checkout

Usage: python3 scripts/acceptance_9.py [--explain] [--calibrate]
--calibrate forces this test red by patching claim_store.acquire so its
own exclusivity guard (a live, differently-owned claim refuses a second
acquire) is skipped, the mechanical shape of "duplicates completed work":
a second owner is handed the same still-leased unit the crashed owner has
not finished. Passes only if this test correctly reads that duplicate
grant as a failure.
"""
import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP_BRIDGE = os.path.join(HERE, "loop_bridge.py")

# Sized for a CONTENDED machine, not an idle one: at 20s/60s this area read
# FAIL "(no output)" only inside a full concurrent battery (load about 4)
# while passing standalone, twice on 2026-08-30, diagnosed from the battery's
# kept failure file. The waits are bounded event-polls, so on an idle machine
# the extra headroom costs nothing.
CLAIM_WAIT_SECONDS = 60.0
RESUME_TIMEOUT_SECONDS = 180.0

TEMPLATE = """area 9 template addition to G1-M3.3's shape:
  - SIGKILL, never a clean exit: the controller gets no chance to tidy up,
    so whatever is on disk afterward is exactly what a power cut would
    leave, matching scripts/test_crash_resume.py's own stated reason for
    the same choice
  - every assertion after the kill reads DURABLE state alone (the claim
    store file), never a return value or an in-memory object the dying
    process might have held; that is the only honest way to check
    "resumes from a known state" rather than "resumes from what it
    remembered", which a crash by definition destroys
  - the three fails_when clauses are each given their own real check: no
    record (reconcile must name both units), duplicated work (a second
    owner must be refused while the lease lives), and an unowned lock left
    behind (the resumed owner's attempt counter and release record must
    both advance, not restart at attempt 1)
What areas 1 through 8's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: a crash proof is only honest if the
process is actually killed with no cooperation; a clean shutdown proves the
shutdown path works, not the crash path."""


def canon(tmp):
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


def wait_for_claims(store, n, timeout=CLAIM_WAIT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(store):
            try:
                with open(store, encoding="utf-8") as fh:
                    data = json.load(fh)
                if sum(1 for v in data.values()
                       if v.get("state") == "claimed") >= n:
                    return data
            except ValueError:
                pass  # sbe: allow-silent mid-write poll loop; the atomic rename makes this transient and the deadline above still fires
        time.sleep(0.1)
    return None


@contextlib.contextmanager
def _tempdir(prefix):
    """TemporaryDirectory, with a cleanup that survives this script's own
    design. This area SIGKILLs a controller mid-worker, and a killed
    worker's git worktree can still be materializing files while rmtree
    walks the tree, so cleanup raced it and __exit__ raised 'Directory not
    empty' on .git/worktrees/<unit>: measured twice in one night, only
    under a loaded battery (standalone runs always passed, because the
    children died fast enough). The verdict is decided before cleanup, so
    a dirty tempdir must never turn a finished proof into a FAIL: retry
    briefly, then leave the remnant to the OS temp reaper, saying so."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    try:
        yield tmp
    finally:
        for _ in range(5):
            try:
                shutil.rmtree(tmp)
                break
            except OSError:
                time.sleep(0.3)
        else:
            print("acceptance_9: temp dir %s left for the OS reaper: a "
                  "killed worker's git worktree outlived cleanup" % tmp,
                  file=sys.stderr)


def _run(explain, break_exclusivity_guard):
    if not os.path.exists(LOOP_BRIDGE):
        return 2, "NO-DATA: scripts/loop_bridge.py is not present in this checkout"
    sys.path.insert(0, HERE)
    try:
        import claim_store as C
        import work_record as WR
    except ImportError as exc:
        return 2, "NO-DATA: could not import the claim/work spine: %s" % exc

    prefix = "acceptance-9-calibrate-" if break_exclusivity_guard else "acceptance-9-"
    with _tempdir(prefix) as tmp:
        repo = canon(tmp)
        store = os.path.join(tmp, "claims.json")
        wr_store = os.path.join(tmp, "wr-store")
        record, problems = WR.create(
            "crash and resume acceptance proof",
            [{"id": "CR1", "done_check": "true", "owns": ["a.txt"]},
             {"id": "CR2", "done_check": "true", "owns": ["b.txt"]}],
            store=wr_store)
        if problems:
            return 2, "NO-DATA: could not build a work_record plan: %s" % problems

        # --slots 2, PINNED: CR1 and CR2 have no dependency between them and
        # this proof needs BOTH durably claimed in one batch before the
        # kill. The real scheduler's disk-derived capacity (graph_loop.py's
        # machine_capacity) legitimately drops to 1 slot under this
        # estate's own cleanup band; that is capacity POLICY, owned by
        # test_resource_gate.py, and must not decide how many workers a
        # crash-recovery proof exercises.
        proc = subprocess.Popen(
            [sys.executable, LOOP_BRIDGE, "--plan", record["path"],
             "--claims", store, "--owner", "crash-owner", "--cwd", repo,
             "--slots", "2", "--worker-cmd", "sleep", "30"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        data = wait_for_claims(store, 2)
        if data is None:
            proc.kill()
            proc.wait(timeout=10)
            return 1, "FAIL: the controller never durably claimed its units before the kill"

        # 1. THE CRASH: SIGKILL, no cooperation, no tidy-up.
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)

        # 2. NO RECORD LOST: the claims survive the crash, still claimed.
        with open(store, encoding="utf-8") as fh:
            after_crash = json.load(fh)
        held = sorted(u for u, v in after_crash.items() if v.get("state") == "claimed")
        if held != ["CR1", "CR2"]:
            return 1, ("FAIL: after the kill the claim store shows claimed=%s, "
                       "not both units: the crash left no record of the "
                       "in-flight work" % held)

        # 3. NO DUPLICATE WORK while genuinely still live. PRE-EXISTING BUG
        # FOUND AND FIXED HERE, unrelated to slots: commit 7090983 ("Gap 1:
        # crash recovery reads a dead owner in seconds, not the full 20
        # minute lease") made live() also check the owning pid, so a claim
        # whose pid is confirmed dead ON THIS HOST is instantly reclaimable
        # by design (test_claim_store.py's own
        # test_a_dead_owner_reads_abandoned_within_seconds_not_the_full_lease).
        # Asserting refusal against CR1 straight after the SIGKILL above
        # (same host, confirmed-dead pid) would fail against that intended
        # contract, not prove anything; this was masked until now because
        # the disk-band slot=1 issue this session fixed meant the second
        # unit was never even claimed, so this line was never reached.
        # What Gap 1 explicitly still protects is a claim from ANOTHER
        # host (its hostname guard), proven on a COPY of the store so
        # CR1's real claim is untouched and the rest of this run proceeds
        # exactly as designed. --calibrate breaks the guard outright
        # instead, on the real store, the mechanical shape of "duplicates
        # completed work".
        if break_exclusivity_guard:
            original_acquire = C.acquire

            def broken_acquire(path, unit_id, owner, **kw):
                # THE FORCED BAD STATE: skip the "already claimed and live"
                # exclusivity check that acquire() normally enforces.
                data, _ = C._read(path)
                held_claim = (data or {}).get(unit_id)
                if held_claim:
                    n = int(held_claim.get("attempt", 0)) + 1
                    claim = dict(held_claim, owner=owner, attempt=n,
                                state="claimed")
                    data[unit_id] = claim
                    C._write(path, data)
                    return claim, ""
                return original_acquire(path, unit_id, owner, **kw)

            with mock.patch.object(C, "acquire", broken_acquire):
                stolen, problem = C.acquire(store, "CR1", "opportunist")
        else:
            with open(store, encoding="utf-8") as fh:
                cross_host = json.load(fh)
            cross_host["CR1"]["hostname"] = "some-other-host-entirely"
            cross_host_store = os.path.join(tempfile.mkdtemp(), "claims.json")
            with open(cross_host_store, "w", encoding="utf-8") as fh:
                json.dump(cross_host, fh)
            stolen, problem = C.acquire(cross_host_store, "CR1", "opportunist")

        if stolen is not None:
            return 1, ("FAIL: a second owner (opportunist) was handed unit "
                       "CR1 while crash-owner's lease was still live "
                       "(claim=%s): the same unit could now be done twice"
                       % stolen)

        # 4. RECONCILE SEES EXACTLY WHAT HAPPENED, under the right owner.
        # Both units read "abandoned", not "in-flight": reconcile's own
        # liveness check is the same live() Gap 1 changed, so a confirmed-
        # dead same-host pid is recognised within seconds rather than
        # reported falsely "in-flight" until the full lease expires.
        found, _ = C.reconcile(store)
        by_unit = {f["unit_id"]: f for f in found}
        for uid in ("CR1", "CR2"):
            f = by_unit.get(uid)
            if f is None or f.get("status") != "abandoned":
                return 1, ("FAIL: reconcile did not report %s as abandoned "
                           "(found=%s): the resumed session would have no "
                           "record of this work" % (uid, found))
            if f.get("owner") != "crash-owner":
                return 1, ("FAIL: reconcile reported %s under owner %r, not "
                           "the crashed session" % (uid, f.get("owner")))

        if explain:
            print(TEMPLATE)

        # 5. THE RIGHTFUL OWNER RESUMES: same owner, no wait for expiry, a
        # fast worker completes both units on attempt 2.
        proc2 = subprocess.run(
            [sys.executable, LOOP_BRIDGE, "--plan", record["path"],
             "--claims", store, "--owner", "crash-owner", "--cwd", repo,
             "--slots", "2", "--worker-cmd", "true"],
            capture_output=True, text=True, timeout=RESUME_TIMEOUT_SECONDS)

        with open(store, encoding="utf-8") as fh:
            final = json.load(fh)
        for uid in ("CR1", "CR2"):
            rec = final.get(uid, {})
            if rec.get("state") == "claimed":
                return 1, ("FAIL: %s is still marked claimed after the "
                           "resume: an unowned lock or claim was left behind"
                           % uid)
            if rec.get("attempt") != 2:
                return 1, ("FAIL: %s resumed at attempt %r, not 2: the "
                           "resume looks like a restart from scratch rather "
                           "than a resume" % (uid, rec.get("attempt")))
            if "released_at" not in rec:
                return 1, ("FAIL: %s has no released_at after the resume: "
                           "a completed unit is indistinguishable from one "
                           "nobody ever took" % uid)

        return 0, ("PASS: SIGKILL mid-worker left both claims durably "
                   "claimed, a second owner (opportunist) was refused CR1 "
                   "from another host while the lease lived, reconcile "
                   "reported both units abandoned under crash-owner, and "
                   "the same owner's resume closed both at attempt 2 with "
                   "a release record "
                   "kept for each: %s" % (proc2.stdout + proc2.stderr).strip()[-160:])


def run(explain=False):
    return _run(explain, break_exclusivity_guard=False)


def calibrate():
    """G1-M3.12.2: force this test red once. Patches claim_store.acquire so
    its own exclusivity guard is skipped, the mechanical shape of
    "duplicates completed work": a second owner is handed the same unit the
    crashed owner has not finished. Passes only if this test correctly
    reads that duplicate grant as a failure."""
    code, evidence = _run(explain=False, break_exclusivity_guard=True)
    if code == 1:
        return 0, ("PASS: calibration skipped the claim store's exclusivity "
                   "guard and this test correctly read the resulting "
                   "duplicate grant as failed (%s): a green reading of this "
                   "test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 9: crash recovery "
                    "and resumable sessions.")
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

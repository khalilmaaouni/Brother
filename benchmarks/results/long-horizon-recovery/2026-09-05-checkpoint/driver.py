#!/usr/bin/env python3
"""Long-Horizon Recovery gauntlet, morning partial run (temporal arm pending).

Drives the REAL engine (brother_run.py -> door.py -> loop_bridge.py ->
integrate.py) on a throwaway repository, using the stub decomposer/model seam
scripts/product_acceptance.py and scripts/test_brother_run.py already use to
stand in for the real `claude` CLI at the DOOR_MODEL_CMD/MODEL_WORKER_CMD
seam. This exercises the engine's crash-recovery mechanics (claims, journal,
continuity capsule), which is what workload family 7 (crash during
execution) tests; it is honest about NOT being a real-model run and NOT
covering the 24-72 hour drift arm (family 8), which needs a real clock gap.

Sequence: build repo -> start brother_run on a two-unit, A2-depends-on-A1
outcome -> wait for A1 to reach DONE (first integration) -> wait for A2 to be
durably claimed -> capture canonical state -> SIGKILL the whole process
group (the crash) -> capture the continuity capsule as printed after the
kill -> write a driver log with pids and timestamps.
"""
import json
import os
import shutil
import sys
import tempfile
import time

SCRIPTS = "<BROTHER_SCRIPTS>"
sys.path.insert(0, SCRIPTS)
import test_brother_run as tbr  # noqa: E402
import product_acceptance as pa  # noqa: E402

BROTHER_RUN = os.path.join(SCRIPTS, "brother_run.py")
CONTINUITY = os.path.join(SCRIPTS, "continuity.py")

WORK = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="lhr-")
os.makedirs(WORK, exist_ok=True)
DRIVER_LOG = os.path.join(WORK, "driver.log")


def log(msg):
    line = "%.3f %s" % (time.time(), msg)
    print(line)
    with open(DRIVER_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    started_at = time.time()
    log("STARTED long-horizon-recovery morning partial run")

    tmp = tempfile.mkdtemp(prefix="lhr-fixture-", dir=WORK)
    repo = tbr.make_repo(tmp)
    log("built throwaway git repository at %s" % repo)

    outcome = "two files exist, the second after the first"
    env = pa.stub_env(tmp, pa.TWO_DEPENDENT_DECOMPOSER,
                      pa.slow_model_body(2.0, "two.txt"))
    runs_root = tmp

    cmd = [sys.executable, BROTHER_RUN, outcome,
           "--cwd", repo, "--runs-root", runs_root]
    log("launch: %s" % " ".join(cmd))
    proc = pa._popen_group(cmd, cwd=repo, env=env)
    pgid = os.getpgid(proc.pid)
    log("pid=%s pgid=%s" % (proc.pid, pgid))

    deadline = time.time() + 30
    run_dir = pa._find_run_dir(runs_root, deadline)
    if run_dir is None:
        pa._killpg(proc)
        log("FAIL: brother_run never created a run directory before the deadline")
        return 1
    log("run_dir=%s" % run_dir)

    if not pa._wait_for_status(run_dir, "A1", "DONE", deadline):
        pa._killpg(proc)
        log("FAIL: A1 never reached DONE; there was no first integration to interrupt after")
        return 1
    log("A1 reached DONE: first integration performed")

    claims_path = os.path.join(run_dir, "claims.json")
    claim = pa._wait_for_claim(claims_path, "A2", deadline)
    if claim is None:
        pa._killpg(proc)
        log("FAIL: A2 was never claimed before the deadline; the crash could not be staged mid-unit")
        return 1
    log("A2 claimed: %s" % json.dumps(claim))

    # Capture canonical state BEFORE the interruption: claims, the store,
    # the receipts (attempt traces so far), the integration record (the
    # Work document, which already shows A1 as DONE).
    canonical = os.path.join(WORK, "canonical_state_before_kill")
    os.makedirs(canonical, exist_ok=True)
    for name in ("claims.json", "claims_usage.json", "target.json", "capsule.json"):
        src = os.path.join(run_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(canonical, name))
    work_doc = pa._work_doc_path(run_dir)
    if work_doc:
        shutil.copy2(work_doc, os.path.join(canonical, os.path.basename(work_doc)))
    attempts_dir = os.path.join(run_dir, "attempts")
    if os.path.isdir(attempts_dir):
        shutil.copytree(attempts_dir, os.path.join(canonical, "attempts"))
    log("captured canonical state (claims, store, receipts, integration record) before interruption: %s" % canonical)

    kill_time = time.time()
    log("INJECTING INTERRUPTION per long-horizon-recovery.json "
        "seeded_conditions_note: 'a crash is a SIGKILL of the whole run's "
        "process group, the shape "
        "docs/plan/runs/live-autonomous-adversity-2026-09-04/ already "
        "drove' -- SIGKILL pgid=%s pid=%s" % (pgid, proc.pid))
    pa._killpg(proc)
    log("kill issued and process group reaped at %.3f (elapsed %.3fs since kill)"
        % (time.time(), time.time() - kill_time))

    after_kill = pa._read_claims(claims_path)
    a2_after = after_kill.get("A2")
    log("A2 claim state after kill: %s" % json.dumps(a2_after))
    landed_mid_unit = bool(a2_after) and a2_after.get("state") == "claimed"
    if not landed_mid_unit:
        log("WARNING: A2 was not left mid-claim as staged: %r" % a2_after)

    # The continuity capsule as printed after the kill (raw_artifacts.contents).
    import subprocess
    cap = subprocess.run([sys.executable, CONTINUITY, run_dir, "--json"],
                         capture_output=True, text=True)
    cap_path = os.path.join(WORK, "continuity_capsule_after_kill.json")
    with open(cap_path, "w", encoding="utf-8") as fh:
        fh.write(cap.stdout)
    log("continuity capsule captured after the kill (exit=%s) -> %s"
        % (cap.returncode, cap_path))
    screen = subprocess.run([sys.executable, CONTINUITY, run_dir],
                            capture_output=True, text=True)
    with open(os.path.join(WORK, "continuity_screen_after_kill.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(screen.stdout)

    result = {
        "outcome": outcome,
        "started_at": started_at,
        "kill_time": kill_time,
        "pid": proc.pid,
        "pgid": pgid,
        "tmp": tmp,
        "repo": repo,
        "run_dir": run_dir,
        "a2_claim_after_kill": a2_after,
        "landed_mid_unit": landed_mid_unit,
        "resume_command": [sys.executable, BROTHER_RUN, outcome,
                           "--cwd", repo, "--runs-root", runs_root],
    }
    with open(os.path.join(WORK, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    log("DONE: driver finished, result written to %s/result.json" % WORK)
    return 0


if __name__ == "__main__":
    sys.exit(main())

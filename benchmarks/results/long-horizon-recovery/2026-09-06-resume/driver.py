#!/usr/bin/env python3
"""Long-Horizon Recovery gauntlet, 2026-09-06: the resume of the frozen
2026-09-05 checkpoint (benchmarks/results/long-horizon-recovery/
2026-09-05-checkpoint/RECORD.md and MANIFEST.json).

ROUTE TAKEN, stated plainly: the checkpoint's own throwaway fixture and runs
root were created by tempfile.mkdtemp() in the 2026-09-05 session and do not
survive between sessions (MANIFEST.json's own "resume_command" already names
them with bracketed placeholders, because every real path was scrubbed
before that directory was committed). So there is nothing left on disk to
literally resume. The honest route, per this lane's own brief, is to REPLAY
the checkpoint's frozen phases (build the fixture, start brother_run, wait
for A1 to integrate, wait for A2 to be claimed, capture canonical state,
SIGKILL the process group, capture the post-kill continuity capsule) into a
FRESH mkdtemp using the exact same stub seam
(product_acceptance.stub_env / TWO_DEPENDENT_DECOMPOSER / slow_model_body),
then extend it with the resume phases the checkpoint deliberately left
undone: a real wall-clock gap, a repository-drift commit, the documented
resume re-invocation, and the post-resume observations.

PHASES 1-7 below (build the repo .. capture the post-kill capsule) are the
2026-09-05-checkpoint's own driver.py, UNCHANGED in logic. The one
mechanical difference: SCRIPTS is resolved from this file's own location on
disk instead of the frozen "<BROTHER_SCRIPTS>" placeholder, because this
copy actually has to run rather than sit frozen for record-keeping. PHASES
8 onward are new; each is marked "RESUME PHASE, ADDED 2026-09-06" and is not
present in the 2026-09-05-checkpoint/driver.py this file was copied from.

REVISED 2026-09-06 per the opus evidence audit
(~/.claude/evidence/audit-383-lhr-2026-09-06.md) of the first version of
this record, which found the PASS held only with seven named limits. Seven
fixes, each marked "AUDIT FIX" at its site: (1) the killed run's own
run.log and journal.jsonl are now copied into killed_run/ before the
resume can overwrite run.log; (2) the lease is no longer backdated, since
claim_store.dead_reason() already reclaims on a dead pid regardless of
remaining lease time; (3) false_claims_after_recovery is compared against
git's own merges (product_acceptance._merge_ids), never claims.json, and a
report-parse miss is NO-DATA rather than an empty list; (4)
recovery_time_seconds now measures time to the recovered, verified state
(the resumed unit's own integrate.merged journal event), with the old
acknowledgment-line measurement kept as a separate field; (5) lost_decisions
now compares the intent-resolution record before and after the resume, by
content; (6) drift detection is reported honestly, NO-DATA unless the
engine's own output or journal names the repository change; (7) the crash
is now delayed a few seconds into the worker's slow body, so repeated work
was actually possible, and the exposure window is stated.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
for _ in range(6):
    if os.path.isdir(os.path.join(ROOT, "scripts")):
        break
    ROOT = os.path.dirname(ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import test_brother_run as tbr  # noqa: E402
import product_acceptance as pa  # noqa: E402

BROTHER_RUN = os.path.join(SCRIPTS, "brother_run.py")
CONTINUITY = os.path.join(SCRIPTS, "continuity.py")

WORK = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="lhr-resume-")
os.makedirs(WORK, exist_ok=True)
DRIVER_LOG = os.path.join(WORK, "driver.log")


def log(msg):
    line = "%.3f %s" % (time.time(), msg)
    print(line)
    with open(DRIVER_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main():
    started_at = time.time()
    log("STARTED long-horizon-recovery 2026-09-06 resume (replay then resume)")

    # ---- PHASES 1-7: the 2026-09-05-checkpoint's own driver, replayed ----
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
    claim_observed_time = time.time()
    log("A2 claimed: %s" % json.dumps(claim))

    # AUDIT FIX (7): the previous run killed 20ms after the claim, before
    # the worker had spawned, so repeated_work=0 was true by construction
    # and proved nothing. slow_model_body sleeps 2.0s before writing
    # two.txt; waiting here lands the kill solidly inside that window, with
    # margin on both sides, so a worker is genuinely mid-flight when it
    # dies and repeated_work=0 (if that is what the rerun shows) means the
    # engine did not redo real work, not that none was ever possible.
    WORKER_MIDFLIGHT_WAIT = 1.0
    log("waiting %.1fs after the claim so the worker is genuinely mid-flight "
        "inside slow_model_body's 2.0s sleep before the kill lands"
        % WORKER_MIDFLIGHT_WAIT)
    time.sleep(WORKER_MIDFLIGHT_WAIT)

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

    # AUDIT FIX (1): the killed run's own run.log and journal.jsonl do not
    # survive the resume (RunLog._flush opens run.log with "w", so the
    # resumed process's own buffer replaces it from line 1). The
    # 2026-09-05-checkpoint captured this by hand as killed_run/; this
    # record now does it in the driver, before anything can overwrite it.
    killed_run = os.path.join(WORK, "killed_run")
    shutil.copytree(run_dir, killed_run)
    log("captured the killed run directory (run.log, journal.jsonl, claims, "
        "attempts, screens, all of it) before the resume can overwrite "
        "run.log in place: %s" % killed_run)

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

    canon_claims = pa._read_claims(claims_path)
    a1_evidence_at_kill = (canon_claims.get("A1") or {}).get("evidence") or {}
    a1_canonical_rev_at_kill = a1_evidence_at_kill.get("canonical_rev")
    log("A1's recorded canonical_rev at integration time: %s" % a1_canonical_rev_at_kill)

    # -----------------------------------------------------------------
    # RESUME PHASE, ADDED 2026-09-06. Nothing above this line differs in
    # logic from 2026-09-05-checkpoint/driver.py (which stopped here, at
    # "freeze state", per its own RECORD.md "What was NOT done").
    # -----------------------------------------------------------------
    human_interventions = 0

    GAP_SECONDS = 60
    log("WAIT: honest substitute for the temporal arm's 24-72 hour gap. "
        "The 2026-09-05-checkpoint carries the real calendar gap for the "
        "record (started 2026-09-04T21:22:39Z per its MANIFEST.json, "
        "verdict 'resume not before 2026-09-05T21:22:39Z', actually resumed "
        "2026-09-06); this replay's fixture is a fresh mkdtemp built seconds "
        "ago in THIS session, so the only honest gap available to it is a "
        "real wall-clock sleep, named here as exactly what it is: %ds, not "
        "24-72 hours." % GAP_SECONDS)
    gap_start = time.time()
    time.sleep(GAP_SECONDS)
    gap_elapsed = time.time() - gap_start
    log("gap elapsed: %.3fs" % gap_elapsed)

    # Change repository state: one recorded drift commit on the canonical
    # branch, the shape the gauntlet's fairness_notes calls for ("one
    # recorded drift commit applied at the same point in every arm") and its
    # seeded_conditions_note names as "a branch change ... landing on the
    # canonical branch between the kill and the resume".
    drift_path = os.path.join(repo, "repo-drift-2026-09-06.txt")
    with open(drift_path, "w", encoding="utf-8") as fh:
        fh.write("repository drift applied between kill and resume, 2026-09-06\n")
    subprocess.run(["git", "add", "repo-drift-2026-09-06.txt"], cwd=repo,
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m",
                    "repository drift: a change the killed run never saw"],
                   cwd=repo, check=True, capture_output=True, text=True)
    drift_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                               capture_output=True, text=True).stdout.strip()
    log("repository drift commit applied: %s (canonical was %s at kill time)"
        % (drift_rev, a1_canonical_rev_at_kill))

    cap_drift = subprocess.run([sys.executable, CONTINUITY, run_dir, "--json"],
                               capture_output=True, text=True)
    with open(os.path.join(WORK, "continuity_capsule_before_resume.json"), "w",
              encoding="utf-8") as fh:
        fh.write(cap_drift.stdout)
    drift_seen_live = None
    try:
        drift_cap = json.loads(cap_drift.stdout)
        drift_seen_live = drift_cap.get("canonical_revision") == drift_rev
    except ValueError:
        drift_cap = {}
    log("continuity capsule re-read live from disk after the drift commit, "
        "before resume: canonical_revision=%r (drift_rev=%s) -> matches=%s"
        % (drift_cap.get("canonical_revision"), drift_rev, drift_seen_live))

    # AUDIT FIX (2): the previous run backdated A2's lease with
    # pa._edit_expires_at before resuming. The audit found this unnecessary
    # and misleading: scripts/claim_store.py's dead_reason() (near line 289)
    # reclaims a claim when EITHER its lease expired OR its owning pid is
    # gone on this host, checked as an OR, not gated on the lease first. The
    # process group was SIGKILLed above, so A2's owning pid is genuinely
    # dead on this host; the engine's own pid check fires on resume with no
    # help from the harness and no wait. Backdating the lease masked that
    # path and let the run claim a TTL-expiry recovery it never exercised.
    # No claim-store write happens here at all, so nothing here counts as a
    # human intervention beyond the resume re-invocation below.
    log("no lease backdating and no wait: A2's owning pid is dead on this "
        "host (SIGKILLed above), and claim_store.dead_reason() reclaims on "
        "a dead pid regardless of remaining lease time, so the resume's own "
        "reconcile should reclaim it unaided")

    resume_cmd = [sys.executable, BROTHER_RUN, outcome,
                  "--cwd", repo, "--runs-root", runs_root]
    log("RESUME. Documented invocation, copied verbatim from "
        "2026-09-05-checkpoint/MANIFEST.json's own \"resume_command\": a "
        "bare re-invocation of the same outcome sentence, no --resume, no "
        "--continue. This is the ONE counted human intervention, and it is "
        "also the gauntlet's own \"context loss\" seeded condition: %s"
        % " ".join(resume_cmd))
    human_interventions += 1

    resume_env = dict(env, PYTHONUNBUFFERED="1")
    resume_issue_time = time.time()
    proc2 = subprocess.Popen(resume_cmd, cwd=repo, env=resume_env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    resume_lines = []
    first_correct_line = None
    first_correct_time = None
    deadline2 = time.time() + 90
    for line in proc2.stdout:
        stripped = line.rstrip("\n")
        resume_lines.append(stripped)
        if first_correct_line is None and (
                "resuming" in stripped or "unfinished run" in stripped):
            first_correct_line = stripped
            first_correct_time = time.time()
        if time.time() > deadline2:
            break
    proc2.wait(timeout=30)
    resume_wall = time.time() - resume_issue_time
    # AUDIT FIX (4): this is the time to the resume's first ACKNOWLEDGING
    # line ("resuming"/"unfinished run"), not to a recovered, verified
    # state. Kept and reported separately as resume_acknowledgment_seconds;
    # the corrected recovery_time_seconds measure (time to the resumed
    # unit's own integrate.merged journal event) is computed further down,
    # once the post-resume journal is back on disk.
    resume_acknowledgment_seconds = ((first_correct_time - resume_issue_time)
                                     if first_correct_time else None)
    receipt_text = "\n".join(resume_lines) + "\n"
    log("resume exited %s in %.3fs; resume_acknowledgment_seconds=%s; "
        "first correct line: %r"
        % (proc2.returncode, resume_wall, resume_acknowledgment_seconds,
           first_correct_line))

    with open(os.path.join(WORK, "receipt.txt"), "w", encoding="utf-8") as fh:
        fh.write(receipt_text)

    # ---- post-resume observations -------------------------------------
    cap_after = subprocess.run([sys.executable, CONTINUITY, run_dir, "--json"],
                               capture_output=True, text=True)
    with open(os.path.join(WORK, "continuity_capsule_after_resume.json"), "w",
              encoding="utf-8") as fh:
        fh.write(cap_after.stdout)
    screen_after = subprocess.run([sys.executable, CONTINUITY, run_dir],
                                  capture_output=True, text=True)
    with open(os.path.join(WORK, "continuity_screen_after_resume.txt"), "w",
              encoding="utf-8") as fh:
        fh.write(screen_after.stdout)

    resumed_run = os.path.join(WORK, "resumed_run")
    os.makedirs(resumed_run, exist_ok=True)
    for name in ("claims.json", "run.log", "journal.jsonl", "target.json", "capsule.json"):
        src = os.path.join(run_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(resumed_run, name))
    work_doc_after = pa._work_doc_path(run_dir)
    if work_doc_after:
        shutil.copy2(work_doc_after, os.path.join(resumed_run, os.path.basename(work_doc_after)))

    def _read_if_exists(path):
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        return ""

    killed_run_log = _read_if_exists(os.path.join(killed_run, "run.log"))
    resumed_run_log = _read_if_exists(os.path.join(resumed_run, "run.log"))
    resumed_journal_text = _read_if_exists(os.path.join(resumed_run, "journal.jsonl"))

    # AUDIT FIX (5): compare the intent-resolution decision record before
    # the kill and after the resume BY CONTENT, instead of the old proxy
    # (whether A1's claim state reads "done", which lives in a different
    # variable and cannot see a decision at all). run.log is overwritten in
    # place on every invocation (RunLog._flush opens it with "w"), so an
    # "intent resolved" line appearing in BOTH the killed run's own log and
    # the resumed run's own log means the decision was RE-TAKEN from
    # scratch on resume, not carried forward: the record of who/what
    # resolved intent the first time is gone, replaced by a second,
    # independent auto-resolution.
    intent_line_re = re.compile(r"^brother_run: intent resolved:.*$", re.MULTILINE)
    pre_kill_intent_lines = intent_line_re.findall(killed_run_log)
    post_resume_intent_lines = intent_line_re.findall(resumed_run_log)
    intent_retaken = bool(pre_kill_intent_lines) and bool(post_resume_intent_lines)
    lost_decisions = 1 if intent_retaken else 0
    log("intent-resolution record: pre-kill=%r post-resume=%r -> "
        "re-taken=%s" % (pre_kill_intent_lines, post_resume_intent_lines,
                         intent_retaken))

    # AUDIT FIX (3, second half): the report can print a unit as "verified
    # by" in its integrated list while that same unit's own per-unit verdict
    # reads NO-DATA (the check passed but proved nothing independence-wise).
    # Flagged here by name rather than silently counted as a clean
    # integration.
    verdict_by_unit = dict(re.findall(
        r"^\s+(\S+)\s+(?:delivered|is NO-DATA):.*?verdict:\s*(\S+)\s*$",
        receipt_text, re.MULTILINE))
    log("per-unit verdicts read from the report: %s" % verdict_by_unit)

    # AUDIT FIX (6): drift detection. Search the engine's own output for a
    # line that NAMES the repository change, never inventing a detection
    # the engine did not itself report. TWO ROUNDS OF FALSE POSITIVES here
    # while writing this check are worth keeping as a record of why it
    # ended up this narrow: round 1 matched the intent screen's own
    # "Rollback: %s" % before (brother_run.py's rollback_line), a plain HEAD
    # readback at screen-render time that equals the drift hash purely
    # because that IS current HEAD by then, the same shape the audit's own
    # sentence 2 named for "canonical revision before/after:". Round 2
    # matched journal.jsonl's own integrate.merged payload ("canonical"/
    # "onto" fields), which is exactly as mechanical: every merge logs
    # whatever HEAD it built on, drift or not, and is never a comparison.
    # So journal.jsonl is EXCLUDED from this search entirely (it is pure
    # structured bookkeeping, never narrative), and the known run.log
    # readback shapes are excluded by name. What is left, a free-text
    # sentence in run.log naming the hash or saying "changed since",
    # is the only shape left that could mean the engine is describing a
    # change rather than just stating a current value.
    def _is_known_head_readback(line):
        s = line.strip()
        return (s.startswith("Rollback:")
                or s.startswith("canonical revision before:")
                or s.startswith("canonical revision after:"))

    drift_hash_hits = [ln for ln in resumed_run_log.splitlines()
                       if (drift_rev in ln or drift_rev[:12] in ln)
                       and not _is_known_head_readback(ln)]
    changed_since_hits = [ln for ln in resumed_run_log.splitlines()
                          if "changed since" in ln.lower()]
    if drift_hash_hits:
        drift_detection = "the engine named the drift commit: %r" % drift_hash_hits[0]
    elif changed_since_hits:
        drift_detection = ("the engine named the repository change: %r"
                           % changed_since_hits[0])
    else:
        drift_detection = (
            "NO-DATA: no engine output or journal event names the "
            "repository change. The run proves drift tolerance, that the "
            "resumed unit built on the new HEAD, not drift detection.")
    log("DRIFT DETECTION: %s" % drift_detection)

    # AUDIT FIX (2, evidence): quote whatever the resumed run's own
    # reconcile/claim machinery actually recorded about A2's reclaim, so
    # RECORD.md can cite real evidence instead of the earlier record's
    # backdated-and-therefore-silent path.
    reclaim_lines = [ln for ln in resumed_run_log.splitlines()
                     if "A2" in ln and ("abandoned" in ln or "dead on this host" in ln
                                        or "reclaimed_from" in ln)]
    reclaim_journal_lines = [ln for ln in resumed_journal_text.splitlines()
                             if '"A2"' in ln and "reclaimed_from" in ln
                             and "claim.acquired" in ln]
    log("reclaim evidence in run.log: %r; in journal: %r"
        % (reclaim_lines, reclaim_journal_lines))

    final_claims = pa._read_claims(claims_path)
    a1_final = final_claims.get("A1") or {}
    a2_final = final_claims.get("A2") or {}
    a2_evidence = a2_final.get("evidence") or {}
    a2_canonical_rev = a2_evidence.get("canonical_rev")

    merges = pa._merge_ids(repo)
    dup_counts = {m: merges.count(m) for m in set(merges) if merges.count(m) > 1}
    repeated_work = sum(c - 1 for c in dup_counts.values())

    ancestor_check = None
    if a2_canonical_rev:
        ancestor_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", drift_rev, a2_canonical_rev],
            cwd=repo, capture_output=True, text=True)
    ran_on_drifted_tree = bool(ancestor_check and ancestor_check.returncode == 0)
    a1_untouched = (a1_final.get("state") == "done" and
                   (a1_final.get("evidence") or {}).get("canonical_rev")
                   == a1_canonical_rev_at_kill)
    wrong_resumed_state = not (ran_on_drifted_tree and a1_untouched)

    files_present = (os.path.exists(os.path.join(repo, "one.txt")) and
                     os.path.exists(os.path.join(repo, "two.txt")) and
                     os.path.exists(drift_path))

    # lost_decisions is now computed above (AUDIT FIX 5), from the
    # intent-resolution record's own content, not from A1's claim state.
    a1_receipt_dir = None
    attempts_after = os.path.join(run_dir, "attempts")
    if os.path.isdir(attempts_after):
        for d in os.listdir(attempts_after):
            if d.startswith("A1-"):
                a1_receipt_dir = os.path.join(attempts_after, d, "attempt-1")
                break
    lost_evidence = 0 if (a1_receipt_dir and os.path.isdir(a1_receipt_dir)) else 1

    # AUDIT FIX (3): compared against git's own merges
    # (product_acceptance._merge_ids, area 11's own comparator), never
    # claims.json, which is an engine-side view exactly like the report
    # itself. A report-parse miss is NO-DATA, never silently an empty list
    # (an empty list used to mean both "nothing integrated" and "could not
    # parse the report" identically).
    report_parse_ok = bool(re.search(r"^\s+\S+\s+verified by:", receipt_text,
                                     re.MULTILINE)
                           or re.search(r"integrated \(\d+\):", receipt_text))
    reported_ids = pa._integrated_ids_from_report(receipt_text)
    git_merges = set(merges)
    if not reported_ids and not report_parse_ok:
        false_claims_after_recovery = ("NO-DATA: could not parse an "
                                       "integrated-units line from the "
                                       "delivery report")
    else:
        false_claims_list = sorted(reported_ids - git_merges)
        printed_verified_but_nodata = sorted(
            uid for uid in reported_ids if verdict_by_unit.get(uid) == "NO-DATA")
        for uid in printed_verified_but_nodata:
            false_claims_list.append(
                "%s (printed as verified by, but its own verdict reads "
                "NO-DATA)" % uid)
        if not files_present:
            false_claims_list.append("files missing after resume")
        false_claims_after_recovery = sorted(set(false_claims_list))
    log("false_claims_after_recovery: %s (reported=%s git_merges=%s)"
        % (false_claims_after_recovery, sorted(reported_ids), sorted(git_merges)))

    # AUDIT FIX (4): recovery_time_seconds now measures time to the
    # recovered, VERIFIED state: the resumed unit's own integrate.merged
    # journal event, read back from the journal itself (append-only, so it
    # survives the resume unlike run.log), never from a stdout line's
    # arrival time at this driver.
    recovery_time_seconds = None
    recovery_time_reason = None
    last_integrate_at = None
    for line in resumed_journal_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # sbe: allow-silent reader over the append-only journal, one malformed line is skipped, never rewritten
        if event.get("type") == "integrate.merged" and event.get("unit_id") == "A2":
            last_integrate_at = event.get("at")
    if last_integrate_at:
        try:
            event_epoch = datetime.datetime.fromisoformat(last_integrate_at).timestamp()
            recovery_time_seconds = event_epoch - resume_issue_time
        except ValueError as exc:
            recovery_time_reason = ("NO-DATA: A2's integrate.merged event's "
                                    "\"at\" field %r did not parse: %s"
                                    % (last_integrate_at, exc))
    else:
        recovery_time_reason = ("NO-DATA: no integrate.merged journal event "
                                "for A2 was found after the resume")
    if recovery_time_seconds is None and recovery_time_reason is None:
        recovery_time_reason = "NO-DATA: recovery time could not be computed"
    log("recovery_time_seconds=%s (reason=%s); resume_acknowledgment_seconds=%s"
        % (recovery_time_seconds, recovery_time_reason,
           resume_acknowledgment_seconds))

    exposure_window_seconds = kill_time - claim_observed_time

    measures = {
        "repeated_work": repeated_work,
        "lost_decisions": lost_decisions,
        "lost_evidence": lost_evidence,
        "wrong_resumed_state": wrong_resumed_state,
        "human_interventions": human_interventions,
        "recovery_time_seconds": (recovery_time_seconds
                                  if recovery_time_seconds is not None
                                  else recovery_time_reason),
        "false_claims_after_recovery": false_claims_after_recovery,
    }
    log("MEASURES: %s" % json.dumps(measures))

    # Per the fix directive: this record's verdict is PARTIAL, not PASS or
    # FAIL. The 24-72 hour temporal arm (workload family 8) stays NO-DATA
    # (this replay's gap is a real but short wall-clock sleep, named as
    # exactly that, never a stand-in for 24-72 hours), and drift detection
    # is NO-DATA unless the engine itself named the repository change
    # above. A crash-recovery arm this thorough does not by itself clear
    # the gauntlet; it narrows what remains unproven.
    verdict = "PARTIAL"
    verdict_reason = (
        "the 24 to 72 hour temporal arm (workload family 8) stays NO-DATA "
        "per the gauntlet's own scoring rubric; drift_detection is %s"
        % ("NO-DATA" if drift_detection.startswith("NO-DATA") else "present"))
    log("VERDICT: %s (%s)" % (verdict, verdict_reason))

    result = {
        "outcome": outcome,
        "started_at": started_at,
        "kill_time": kill_time,
        "gap_seconds": gap_elapsed,
        "drift_commit": drift_rev,
        "resume_command": resume_cmd,
        "resume_returncode": proc2.returncode,
        "resume_wall_seconds": resume_wall,
        "first_correct_line": first_correct_line,
        "a2_canonical_rev_after_resume": a2_canonical_rev,
        "a1_canonical_rev_at_kill": a1_canonical_rev_at_kill,
        "ran_on_drifted_tree": ran_on_drifted_tree,
        "drift_seen_live_before_resume": drift_seen_live,
        "merges": merges,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "measures": measures,
        "resume_acknowledgment_seconds": resume_acknowledgment_seconds,
        "exposure_window_seconds": exposure_window_seconds,
        "drift_detection": drift_detection,
        "intent_resolution_retaken": intent_retaken,
        "pre_kill_intent_lines": pre_kill_intent_lines,
        "post_resume_intent_lines": post_resume_intent_lines,
        "reclaim_evidence_run_log": reclaim_lines,
        "reclaim_evidence_journal": reclaim_journal_lines,
        "per_unit_verdicts": verdict_by_unit,
        "tmp": tmp,
        "repo": repo,
        "run_dir": run_dir,
        "killed_run_dir": killed_run,
    }
    with open(os.path.join(WORK, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    log("DONE: driver finished, result written to %s/result.json" % WORK)
    return 0


if __name__ == "__main__":
    sys.exit(main())

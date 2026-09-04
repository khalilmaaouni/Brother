#!/usr/bin/env python3
"""S32, the review pass: one existing reviewer per high-tier unit, and every
finding it returns carries a check THIS RUN re-executes.

Design: docs/plan/REVIEW-DEPTH-DESIGN-2026-09-05.md. The short version, and
the three properties that keep this from being a generic code review:

  A FINDING MUST SURVIVE A RE-RUN. The reviewer proposes; the exit code
  disposes. A finding whose own verification command re-runs at the
  delivered revision and FAILS is `confirmed`. One that runs and passes is
  `not_reproduced`, because a check that already passes proves nothing. One
  with no command at all is `no-data`, which is visible and worth nothing.

  IT FIRES ON HIGH-TIER UNITS ONLY, decided by receipt_door.unit_tier over
  the unit's declared words, its changed paths and its own diff. A run whose
  units cross no risk boundary dispatches nobody and says so.

  IT BLOCKS NOTHING. The pass runs after the drain, its result rides on the
  receipt, and no exit code anywhere depends on it. A model-proposed blocker
  with no check attached is exactly the false blocking this estate refuses.

THE MODEL SEAM is the environment variable REVIEW_MODEL_CMD, split with
shlex, the identical shape door.py uses for DOOR_MODEL_CMD and
model_worker.py for MODEL_WORKER_CMD, so the existing stub harnesses extend
by one line rather than learning a new seam. UNSET IS NO-DATA, NEVER A PASS:
the pass reports that no reviewer was reachable and names the variable.

Standard library only, and no product import beyond receipt_door (for the
tier) and integrate (for the one check runner the estate already trusts).
"""
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import integrate  # noqa: E402
import receipt_door  # noqa: E402

NODATA = receipt_door.NODATA

#: The seam. Named here once so the pass, the tests and any stub harness
#: spell it the same way.
MODEL_CMD_ENV = "REVIEW_MODEL_CMD"

#: How long one reviewer gets. A reviewer that has not answered in ten
#: minutes is a NO-DATA, not a run that hangs until the watchdog kills it.
REVIEW_TIMEOUT = 600

#: How many units one run will pay a reviewer for. THREE: the whole reason
#: the tier gate exists is that Proportional ceremony is measured in the
#: same round as Review depth, and a pipeline that runs regardless buys one
#: by spending the other. A unit past the cap is stamped NO-DATA naming the
#: cap, never dropped silently.
MAX_REVIEWED_UNITS = 3

#: The finding schema, verbatim from products/brothersbe/skills/review/
#: SKILL.md section 4. Spelled here rather than read from that file because
#: bundle/runtime/ ships no brothersbe package, and a prompt that only works
#: inside the hub is a prompt that scores zero in the round that measures it.
FINDING_SCHEMA = """{
  "reviewer": "<the agent name given above>",
  "category": "<one word, e.g. idempotency, encoding, error-handling>",
  "severity": "critical|major|minor",
  "confidence": "high|medium|low",
  "introducedByChange": "yes|no|unknown",
  "location": "<path:line>",
  "failure": "<what breaks, in one sentence>",
  "evidence": ["<the quoted line or behaviour you pointed at>"],
  "verification": "<a shell command that FAILS on this tree because of this
                    defect and passes once it is fixed; omit if you have
                    none>"
}"""

PROMPT = """You are the %(reviewer)s, reviewing one delivered unit of work.

The unit's objective: %(objective)s
The check that decided it: %(done_check)s
The risk class that selected you: %(risk_class)s

The unit's own diff, at the delivered revision:

%(diff)s

Report only defects you can point at in this diff. A defect OUTSIDE the
task's literal scope still counts; a style preference does not.

For every defect, name a verification command that fails on this tree
BECAUSE of the defect and would pass once it is fixed. This run re-executes
that command and records its real exit code, so a command that already
passes is recorded as proving nothing.

Answer with a JSON array of findings and NOTHING else. An empty array is a
correct and useful answer. Each entry:

%(schema)s
"""


def resolve_cmd(model_cmd=None):
    """[argv, ...] for the reviewer, or [] when none is configured. The
    same resolution door.resolve_cmd uses, minus a default: there is no
    reviewer this pass will invent, because inventing one is how a run
    reports a review it never made."""
    if model_cmd:
        return shlex.split(model_cmd)
    env_cmd = os.environ.get(MODEL_CMD_ENV)
    if env_cmd:
        return shlex.split(env_cmd)
    return []


def canonical_rev(claims, uid):
    """The revision `uid` was integrated at, off the claim store's own
    evidence, or "". Same read as brother_run._stamp_dependency_mutations,
    which is where this field is written."""
    claim = (claims or {}).get(uid) or {}
    evidence = claim.get("evidence")
    if not isinstance(evidence, dict):
        return ""
    return str(evidence.get("canonical_rev") or "").strip()


def unit_diff(cwd, rev, files, runner=None):
    """(diff_text, problem). The unit's OWN change: `git diff rev^1 rev`
    restricted to the files that unit changed. A revision with no first
    parent, an unreadable repository or a git that exits nonzero is a
    problem sentence, never an exception and never an empty diff passed off
    as a clean one."""
    if not rev:
        return "", "this unit's integrated revision is not recorded"
    if not files:
        return "", "this unit changed no file, so there is nothing to review"
    cmd = ["git", "diff", "%s^1" % rev, rev, "--"] + [str(f) for f in files]
    runner = runner or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, cwd=cwd, timeout=120))
    try:
        proc = runner(cmd)
    except Exception as exc:  # noqa: BLE001
        return "", "the unit's diff could not be read: %s" % exc
    if proc.returncode != 0:
        return "", ("git refused to read %s^1..%s: %s"
                    % (rev[:12], rev[:12], (proc.stderr or "").strip()))
    return proc.stdout or "", ""


def build_prompt(row, diff_text, reviewer, risk_class):
    return PROMPT % {
        "reviewer": reviewer,
        "objective": str(row.get("objective") or row.get("title")
                         or NODATA),
        "done_check": str(row.get("done_check") or NODATA),
        "risk_class": risk_class,
        "diff": diff_text,
        "schema": FINDING_SCHEMA,
    }


def ask_reviewer(cmd, prompt, timeout=REVIEW_TIMEOUT):
    """(stdout, problem). The one place a model is invoked. Every failure a
    subprocess can have is a problem sentence: the pass reports NO-DATA and
    the run carries on, because a reviewer that could not be reached must
    never look like a reviewer that found nothing."""
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", "the reviewer command %r could not run: %s" % (cmd[0], exc)
    if proc.returncode != 0:
        return "", ("the reviewer command %r exited %d: %s"
                    % (cmd[0], proc.returncode, (proc.stderr or "").strip()))
    return proc.stdout or "", ""


_ARRAY = re.compile(r"\[.*\]", re.S)


def parse_findings(text):
    """([finding, ...], [dropped reason, ...]). STRICT: the answer must be a
    JSON array of objects, and an entry missing `location` or `failure` is
    dropped with its reason recorded rather than repaired. A batch that
    parses to nothing is NO-DATA at the caller, never a clean pass.

    One tolerance, and only one: a fenced or prefaced answer is searched for
    its outermost array, because every model wraps JSON in prose sooner or
    later and refusing that measures the wrapper rather than the review."""
    raw = str(text or "").strip()
    if not raw:
        return [], ["the reviewer answered with nothing at all"]
    try:
        parsed = json.loads(raw)
    except ValueError:
        match = _ARRAY.search(raw)
        if not match:
            return [], ["the reviewer's answer held no JSON array"]
        try:
            parsed = json.loads(match.group(0))
        except ValueError as exc:
            return [], ["the reviewer's answer did not parse as JSON: %s"
                        % exc]
    if not isinstance(parsed, list):
        return [], ["the reviewer answered with %s, not an array"
                    % type(parsed).__name__]
    kept, dropped = [], []
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            dropped.append("finding %d is not an object" % i)
            continue
        if not str(entry.get("location") or "").strip():
            dropped.append("finding %d names no location" % i)
            continue
        if not str(entry.get("failure") or "").strip():
            dropped.append("finding %d names no failure" % i)
            continue
        kept.append(entry)
    return kept, dropped


def _file_of(location):
    """The path half of a `path:line` location. A location with no colon is
    already a path."""
    return str(location or "").split(":")[0].strip()


def verify_finding(finding, cwd, runner=None):
    """(check_command, exit_code, state) for one finding, with its own check
    RE-EXECUTED at the delivered revision through the same runner
    integrate.py uses for every other check in this estate.

    This is the whole design in four lines: a check that ran and failed
    confirms the finding, a check that ran and passed discriminates nothing,
    and no check at all is NO-DATA."""
    command = str(finding.get("verification") or "").strip()
    if not command:
        return NODATA, None, receipt_door.FINDING_NO_DATA
    code, _detail, _truncated = integrate._run_check(command, cwd,
                                                     runner=runner)
    if code is None:
        return command, None, receipt_door.FINDING_NO_DATA
    if code == 0:
        return command, code, receipt_door.FINDING_NOT_REPRODUCED
    return command, code, receipt_door.FINDING_CONFIRMED


def review_unit(row, rev, files, cwd, cmd, ask=None, runner=None,
                diff_runner=None):
    """The stamp for ONE unit: {"tier", "class", "reviewer",
    "unmeasured_classes", "state", "findings"}. `state` is "ran" only when a
    reviewer really answered; every other value is a NO-DATA sentence naming
    what stopped it."""
    diff_text, problem = unit_diff(cwd, rev, files, runner=diff_runner)
    classes = receipt_door.unit_classes(row, files, diff_text)
    tier, risk_class, reviewer = receipt_door.unit_tier(row, files, diff_text)
    stamp = {"tier": tier, "class": risk_class, "reviewer": reviewer,
             "unmeasured_classes": classes[1:], "state": "", "findings": []}
    if tier != "high":
        stamp["state"] = ("%s: this unit crossed no risk boundary, so no "
                          "reviewer was dispatched" % NODATA)
        return stamp
    if problem:
        stamp["state"] = "%s: %s" % (NODATA, problem)
        return stamp
    if not cmd:
        stamp["state"] = ("%s: %s is not set, so no reviewer was reachable"
                          % (NODATA, MODEL_CMD_ENV))
        return stamp
    prompt = build_prompt(row, diff_text, reviewer, risk_class)
    answer, problem = (ask or ask_reviewer)(cmd, prompt)
    if problem:
        stamp["state"] = "%s: %s" % (NODATA, problem)
        return stamp
    found, dropped = parse_findings(answer)
    if dropped:
        stamp["dropped"] = dropped
    if not found and dropped:
        stamp["state"] = ("%s: the reviewer answered and nothing in it "
                          "parsed as a finding: %s"
                          % (NODATA, "; ".join(dropped)))
        return stamp
    stamp["state"] = "ran"
    for i, finding in enumerate(found):
        command, code, state = verify_finding(finding, cwd, runner=runner)
        stamp["findings"].append({
            "id": "%s-%d" % (row.get("id"), i + 1),
            "unit": row.get("id"),
            "file": _file_of(finding.get("location")),
            "reviewer": reviewer,
            "severity": str(finding.get("severity") or "").lower(),
            "failure": str(finding.get("failure") or ""),
            "check_command": command,
            "check_exit_code": code,
            "state": state,
            "repaired": False if state == receipt_door.FINDING_CONFIRMED
            else None,
        })
    return stamp


def review_rows(rows, claims, cwd, cmd=None, ask=None, runner=None,
                diff_runner=None, cap=MAX_REVIEWED_UNITS):
    """{unit id: stamp} for every DONE row this pass considered. Rows that
    are not DONE are not considered at all, and a row already carrying a
    stamp is left alone, so a resumed run does not pay for a second
    review."""
    cmd = cmd if cmd is not None else resolve_cmd()
    out, paid = {}, 0
    for row in rows or []:
        uid = row.get("id")
        if row.get("status") != "DONE":
            continue
        if isinstance(row.get(receipt_door.REVIEW_FIELD), dict):
            continue
        files = [str(p) for p in (row.get("files_changed_by_unit") or [])]
        if paid >= cap:
            classes = receipt_door.unit_classes(row, files, "")
            out[uid] = {
                "tier": "high" if classes else "low",
                "class": classes[0] if classes else "",
                "reviewer": "", "unmeasured_classes": classes[1:],
                "state": ("%s: this run had already paid for %d reviewer(s), "
                          "which is the per-run cap" % (NODATA, cap)),
                "findings": []}
            continue
        stamp = review_unit(row, canonical_rev(claims, uid), files, cwd, cmd,
                            ask=ask, runner=runner, diff_runner=diff_runner)
        out[uid] = stamp
        if stamp["state"] == "ran":
            paid += 1
    return out


def main(argv=None):
    """Read a Work document and a claim store, print the stamps as JSON.
    Exists so the pass can be driven by hand on a finished run; the engine
    calls review_rows directly from brother_run._stamp_review_findings."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: review_pass.py WORK_DOCUMENT CLAIMS_JSON [CWD]",
              file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            doc = json.load(fh)
        with open(argv[1], encoding="utf-8") as fh:
            claims = json.load(fh)
    except (OSError, ValueError) as exc:
        print("review_pass: %s: %s" % (NODATA, exc), file=sys.stderr)
        return 1
    cwd = argv[2] if len(argv) > 2 else os.getcwd()
    rows = doc.get("rows") or doc.get("units") or []
    print(json.dumps(review_rows(rows, claims, cwd), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

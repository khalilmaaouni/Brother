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

WBS-10.04's BRIDGE (added after this module's first landing): a CONFIRMED
finding outside the roadmap's five human-only exceptions is handed to
products/brothermode/tools/bm_repair.py's own bounded repair loop, and this
run re-verifies the result before ever setting "repaired": True. Same
seam discipline as the reviewer above (REPAIR_WORKER_CMD, unset is NO-DATA,
never a silent repair) and it STILL BLOCKS NOTHING: a repair changes no exit
code either, it only lets a finding's own "repaired" field read true instead
of staying an unflipped False forever. See repair_confirmed_finding and
guard_repair_patch below for the shape and the one guard that matters most.

Standard library only for the review half. The repair half needs one more
product import, bm_repair (reached through loop_bridge.load_parts(), the
existing portable resolver, imported lazily and only when a confirmed
finding actually needs repairing) beyond receipt_door (for the tier) and
integrate (for the one check runner the estate already trusts).
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

#: WBS-10.04's bridge: the SECOND seam, for the worker a confirmed finding
#: is repaired by. Same discipline as MODEL_CMD_ENV above (unset is
#: NO-DATA, never a silent repair): a repair worker edits files, so nothing
#: here invents one. Split with shlex; the command is handed one JSON brief
#: on stdin and is expected to edit `cwd` and exit 0.
REPAIR_WORKER_CMD_ENV = "REPAIR_WORKER_CMD"

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


def resolve_cmd(model_cmd=None, env_var=MODEL_CMD_ENV):
    """[argv, ...] for the reviewer (or, with env_var=REPAIR_WORKER_CMD_ENV,
    the repair worker below), or [] when none is configured. The same
    resolution door.resolve_cmd uses, minus a default: there is no reviewer
    or repair worker this pass will invent, because inventing one is how a
    run reports work it never did."""
    if model_cmd:
        return shlex.split(model_cmd)
    env_cmd = os.environ.get(env_var)
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


# ---------------------------------------------------------------------------
# WBS-10.04's bridge: implement -> verify -> bounded repair -> tests rerun
# (products/brothermode/tools/bm_repair.py) and independent review ->
# findings classified -> tests rerun (review_unit/review_rows, above) are
# two real loops that do not connect. review_unit set "repaired": False on
# every CONFIRMED finding and nothing ever flipped it. This section is that
# flip, and only for the classes BROTHER_1.0.17_CONVERGENCE_ROADMAP_
# 2026-09-14.md's WBS-10.04 "Stop conditions" itself leaves eligible: every
# OTHER confirmed finding stays human-only:
#   intent ambiguity; a security-sensitive design choice; a destructive or
#   external action; release authority; an untestable business decision.
# ---------------------------------------------------------------------------

EXCEPTION_INTENT_AMBIGUITY = "intent ambiguity"
EXCEPTION_SECURITY_DESIGN = "security-sensitive design choice"
EXCEPTION_DESTRUCTIVE = "destructive/external action"
EXCEPTION_RELEASE = "release authority"
EXCEPTION_UNTESTABLE = "untestable business decision"

#: WBS-10.04's own list, verbatim, named here so a caller can read the whole
#: set without grepping the keyword table below.
EXCEPTION_CLASSES = (EXCEPTION_INTENT_AMBIGUITY, EXCEPTION_SECURITY_DESIGN,
                     EXCEPTION_DESTRUCTIVE, EXCEPTION_RELEASE,
                     EXCEPTION_UNTESTABLE)

#: receipt_door.unit_tier's risk classes are a REAL routing fact (the unit
#: was already sent to that reviewer for that reason), so a finding on a
#: unit in one of these classes is mapped straight to the matching
#: exception, no text guessing needed.
_EXCEPTION_BY_RISK_CLASS = {
    "auth": EXCEPTION_SECURITY_DESIGN,
    "dependency manifest": EXCEPTION_SECURITY_DESIGN,
    "money": EXCEPTION_DESTRUCTIVE,
    "migration": EXCEPTION_DESTRUCTIVE,
    "irreversibility": EXCEPTION_DESTRUCTIVE,
}

#: A keyword scan of the finding's OWN text, for the risk classes above
#: that name nothing (encoding, public API, parsing, concurrency) and for
#: the two exceptions (release authority, untestable business decision)
#: unit_tier has no risk class for at all. HEURISTIC AND DELIBERATELY
#: OVER-INCLUSIVE (ponytail: a keyword scan, not a model judgement; upgrade
#: path is a real classifier if this routes real findings to a human that
#: did not need one) -- the failure mode this whole bridge exists to avoid
#: is auto-repairing something that needed a human, not the reverse, so a
#: genuinely unclear finding names a class rather than falling through to
#: "eligible".
_EXCEPTION_KEYWORDS = (
    (EXCEPTION_INTENT_AMBIGUITY, re.compile(
        r"\bambiguous|unclear (intent|what|whether)|underspecified|"
        r"multiple interpretat|not sure what|could mean either\b", re.I)),
    (EXCEPTION_SECURITY_DESIGN, re.compile(
        r"\bauth\w*|credential|permission|secret|encrypt|\btoken\b", re.I)),
    (EXCEPTION_DESTRUCTIVE, re.compile(
        r"\brm -rf|drop table|delete from|force.?push|force.?merge|"
        r"payment|external api|webhook|irreversib|destructive\b", re.I)),
    (EXCEPTION_RELEASE, re.compile(
        r"\brelease|publish|changelog|version bump|manifest\b", re.I)),
    (EXCEPTION_UNTESTABLE, re.compile(
        r"\bbusiness (rule|decision|policy)|pricing|legal|compliance|"
        r"stakeholder|subjective\b", re.I)),
)


def finding_exception_class(finding, risk_class=""):
    """The one WBS-10.04 exception this finding falls under, or "" when it
    is eligible for the auto-repair bridge below. The unit's own risk class
    is checked first (a fact, not a guess); a keyword scan of the finding's
    own category/failure/location/check_command follows."""
    mapped = _EXCEPTION_BY_RISK_CLASS.get(risk_class or "")
    if mapped:
        return mapped
    text = " ".join(str((finding or {}).get(k) or "") for k in
                    ("category", "failure", "location", "check_command"))
    for cls, pattern in _EXCEPTION_KEYWORDS:
        if pattern.search(text):
            return cls
    return ""


#: A path that IS the check rather than the code a check verifies. Touching
#: one of these in a repair's own diff is refused outright, whatever the
#: re-verification says: this is how a repair could look successful by
#: loosening the check instead of fixing the code (the single biggest risk
#: in this bridge, named by an external hostile-review pass on this exact
#: design, 2026-09-13).
_CHECK_FILE_RE = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py|[^/]*\.spec\.[a-z]+|"
    r"tests?/.*)$", re.I)

#: A marker whose ADDITION in a repair's diff is a weakened check, never a
#: real fix: a skip/suppression swapped in for a real assertion.
_WEAKENING_ADD_RE = re.compile(
    r"\b(pytest\.mark\.skip|unittest\.skip|SkipTest|xfail|"
    r"# ?type: ?ignore|# ?noqa)\b", re.I)

#: A line mentioning an assertion or expectation, read on a diff's own +/-
#: lines. Used only to COUNT (removed vs added), never to read meaning.
_ASSERT_LINE_RE = re.compile(
    r"\bassert\b|\.expect\(|assertEqual|assertTrue|assertFalse|"
    r"assertRaises", re.I)


def _diff_files(diff_text):
    """The set of paths a git diff touches, read off its own `diff --git`
    headers. Empty for an unreadable or empty diff, never an exception."""
    return {m.group(1) for m in re.finditer(
        r"^diff --git a/(\S+) b/\S+", str(diff_text or ""), re.M)}


def _assert_delta(diff_text):
    """(removed, added): how many assertion-like lines a diff's OWN +/-
    lines remove versus add. A repair that removes more than it adds is
    narrowing what the code proves, not fixing what it found."""
    removed = added = 0
    for line in str(diff_text or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") and _ASSERT_LINE_RE.search(line):
            added += 1
        elif line.startswith("-") and _ASSERT_LINE_RE.search(line):
            removed += 1
    return removed, added


def guard_repair_patch(diff_text, verification_command=""):
    """"" when a repair's own diff is safe to trust; a reason sentence when
    it is not. THE cheapest and most important guard in this bridge: the
    most likely way an automatic repair looks successful without being one
    is by weakening the CHECK rather than fixing the CODE it checks --
    loosening an assertion, adding a skip, narrowing a regex, mocking the
    failing branch. A repair earns "repaired": True only when its diff
    touches none of the paths a check lives in (including the exact file
    named in its own verification command) and removes no more assertions
    than it adds. Fails CLOSED: an unreadable diff is refused, never
    assumed safe."""
    if not str(diff_text or "").strip():
        return "the repair's diff could not be read, so it cannot be trusted"
    touched = _diff_files(diff_text)
    named_check_files = {p for p in re.findall(r"[\w./-]+\.\w+",
                                               str(verification_command or ""))
                         if _CHECK_FILE_RE.search(p)}
    hit = {p for p in touched if _CHECK_FILE_RE.search(p)} | (touched & named_check_files)
    if hit:
        return ("the repair's own diff touches %s, which is a test, spec or "
                "check file, not the code the check verifies"
                % ", ".join(sorted(hit)))
    removed, added = _assert_delta(diff_text)
    if removed > added:
        return ("the repair's diff removes %d assertion-like line(s) and "
                "adds only %d, which narrows what the check proves rather "
                "than fixing what it found" % (removed, added))
    if _WEAKENING_ADD_RE.search(diff_text):
        return "the repair's diff adds a skip/xfail/suppression marker"
    return ""


def _git_head(cwd, runner=None):
    """(sha, problem). Read the defensive way unit_diff already reads a
    revision: a git failure is a problem sentence, never an exception."""
    runner = runner or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, cwd=cwd, timeout=30))
    try:
        proc = runner(["git", "rev-parse", "HEAD"])
    except Exception as exc:  # noqa: BLE001
        return "", "could not read HEAD: %s" % exc
    if proc.returncode != 0:
        return "", "git rev-parse HEAD failed: %s" % (proc.stderr or "").strip()
    return proc.stdout.strip(), ""


def _repair_diff(cwd, before_head, runner=None):
    """(diff_text, problem): what a repair actually changed, whichever way
    its worker left it. Checked both ways because bm_repair's worker
    contract makes no promise about committing, and a guard that only reads
    one of them is a guard a committing worker walks straight past."""
    runner = runner or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, cwd=cwd, timeout=30))
    after_head, problem = _git_head(cwd, runner=runner)
    if problem:
        return "", problem
    cmd = (["git", "diff", before_head, after_head]
          if after_head and after_head != before_head else
          ["git", "diff", "HEAD"])
    try:
        proc = runner(cmd)
    except Exception as exc:  # noqa: BLE001
        return "", "could not read the repair's diff: %s" % exc
    if proc.returncode != 0:
        return "", "git diff failed: %s" % (proc.stderr or "").strip()
    return proc.stdout or "", ""


class _BridgeWorker(object):
    """Adapts REPAIR_WORKER_CMD to the `worker.run(brief) -> {"status", ...}`
    contract bm_repair.repair() expects, WITH cwd forwarded on every call:
    kept local and minimal rather than importing bm_worker_spawn's full
    SpawningWorker, because a repair worker edits files and this bridge only
    ever repairs one tree (the merged one this review pass ran against),
    never a per-unit lane. `run` accepts an optional cwd kwarg so bm_repair's
    own `_run_in_lane` (which refuses a worker it cannot place in a lane)
    reads this as lane-aware rather than refusing it."""

    def __init__(self, cmd, cwd, timeout=REVIEW_TIMEOUT):
        self._cmd, self._cwd, self._timeout = cmd, cwd, timeout

    def run(self, brief, cwd=None):
        payload = json.dumps(brief, sort_keys=True)
        target = cwd or self._cwd
        try:
            proc = subprocess.run(self._cmd, input=payload, cwd=target,
                                  capture_output=True, text=True,
                                  timeout=self._timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "unavailable",
                   "note": "the repair worker command %r could not run: %s"
                   % (self._cmd[0], exc)}
        if proc.returncode != 0:
            return {"status": "unavailable",
                   "note": "the repair worker command %r exited %d: %s"
                   % (self._cmd[0], proc.returncode,
                      (proc.stderr or "").strip())}
        return {"status": "returned",
               "worker_claim": (proc.stdout or "").strip()[:2000]}


def repair_confirmed_finding(row, finding, risk_class, cwd, cmd=None,
                             worker=None, tools=None, runner=None,
                             max_attempts=None, recall=None, verifier=None):
    """Mutate one CONFIRMED finding's "repaired" field in place, honestly,
    and return it. Six ways this leaves "repaired" False or None rather than
    True, each named in finding["repair_note"]:

      1. the finding is not CONFIRMED (not this bridge's job; left alone)
      2. it falls under one of WBS-10.04's five human-only exceptions
      3. REPAIR_WORKER_CMD is not set, so no repair worker is reachable
      4. the repair loop (products/brothermode/tools/bm_repair.py) is not
         importable from this checkout
      5. bm_repair.repair() tried and never reached PASS inside its own
         bound
      6. it reached PASS, but the repair's OWN diff fails guard_repair_patch
         above: touches a test/check/verification file, or removes more
         assertions than it adds. "repaired" stays False even though
         bm_repair itself reported REPAIRED, because a check that now
         passes because it was loosened is not a fixed defect.

    Only the seventh path sets "repaired": True: bm_repair reports
    REPAIRED, the diff passes the guard, AND this run RE-EXECUTES the
    finding's own verification command again (verify_finding, the same
    function judging every other finding here) and it now exits 0. A repair
    is never trusted on bm_repair's own say-so.

    `worker` and `tools` are injectable for testing (a fake worker, or a
    stub {"repair", "verify"} pair) without a real subprocess or a real
    loop_bridge resolution; `recall` and `verifier` pass straight through to
    bm_repair.repair(), mainly so a test can skip its default vault recall
    rather than shelling out for real."""
    if (finding or {}).get("state") != receipt_door.FINDING_CONFIRMED:
        return finding

    exc_class = finding_exception_class(finding, risk_class)
    if exc_class:
        finding["repaired"] = False
        finding["repair_note"] = (
            "not auto-repaired: this finding falls under the '%s' "
            "exception, which WBS-10.04 names human-only" % exc_class)
        return finding

    if worker is None:
        argv = cmd if cmd is not None else resolve_cmd(
            env_var=REPAIR_WORKER_CMD_ENV)
        if not argv:
            finding["repaired"] = False
            finding["repair_note"] = (
                "not auto-repaired: %s is not set, so no repair worker was "
                "reachable" % REPAIR_WORKER_CMD_ENV)
            return finding
        worker = _BridgeWorker(argv, cwd)

    if tools is None:
        try:
            import loop_bridge
        except ImportError as exc:
            finding["repaired"] = False
            finding["repair_note"] = (
                "not auto-repaired: the repair loop is not importable: %s"
                % exc)
            return finding
        tools, problem = loop_bridge.load_parts()
        if tools is None:
            finding["repaired"] = False
            finding["repair_note"] = "not auto-repaired: %s" % problem
            return finding

    before_head, head_problem = _git_head(cwd, runner=runner)
    if head_problem:
        finding["repaired"] = False
        finding["repair_note"] = "not auto-repaired: %s" % head_problem
        return finding

    unit = {"id": finding.get("id"), "objective": finding.get("failure"),
           "done_check": finding.get("check_command")}
    verdict = {"verdict": tools["verify"].FAIL,
              "reason": finding.get("failure", ""),
              "exit_code": finding.get("check_exit_code")}
    attempts = (max_attempts if max_attempts is not None else
               tools["repair"].DEFAULT_MAX_ATTEMPTS)
    result = tools["repair"].repair(unit, verdict, worker, cwd=cwd,
                                    max_attempts=attempts, recall=recall,
                                    verifier=verifier)
    finding["repair_outcome"] = result.get("outcome")
    if result.get("outcome") != tools["repair"].REPAIRED:
        finding["repaired"] = False
        finding["repair_note"] = ("not auto-repaired: %s"
                                  % result.get("reason", ""))
        return finding

    diff_text, diff_problem = _repair_diff(cwd, before_head, runner=runner)
    if diff_problem:
        finding["repaired"] = False
        finding["repair_note"] = (
            "not auto-repaired: bm_repair reported REPAIRED but its own "
            "diff could not be read, so it cannot be trusted: %s"
            % diff_problem)
        return finding
    guard_reason = guard_repair_patch(diff_text, finding.get("check_command", ""))
    if guard_reason:
        finding["repaired"] = False
        finding["repair_note"] = "not auto-repaired: %s" % guard_reason
        return finding

    recheck = {"verification": finding.get("check_command")}
    command, code, state = verify_finding(recheck, cwd, runner=runner)
    if state != receipt_door.FINDING_NOT_REPRODUCED:
        finding["repaired"] = False
        finding["repair_note"] = (
            "not auto-repaired: bm_repair reported REPAIRED and its diff "
            "passed the check-file/assertion guard, but re-running the "
            "finding's own check %r at the repaired tree still does not "
            "pass (exit %s)" % (command, code))
        return finding

    finding["repaired"] = True
    finding["check_exit_code"] = code
    finding["repair_note"] = (
        "repaired: bm_repair reached PASS, its diff passed the "
        "check-file/assertion guard, and the finding's own check was "
        "re-run at the repaired tree and now exits 0")
    return finding


def review_unit(row, rev, files, cwd, cmd, ask=None, runner=None,
                diff_runner=None, repair_worker=None, repair_tools=None,
                repair_cmd=None, repair_max_attempts=None):
    """The stamp for ONE unit: {"tier", "class", "reviewer",
    "unmeasured_classes", "state", "findings"}. `state` is "ran" only when a
    reviewer really answered; every other value is a NO-DATA sentence naming
    what stopped it."""
    diff_text, problem = unit_diff(cwd, rev, files, runner=diff_runner)
    classes = receipt_door.unit_classes(row, files, diff_text)
    tier, risk_class, reviewer = receipt_door.unit_tier(row, files, diff_text)
    # accept_delivery.review_from_run_dir's own acceptance gate
    # (_validate_review) requires reviewed_revision and scope on any
    # review.json shape it accepts; both are already known here (`rev`
    # is the revision this diff was read at, `files` is the unit's own
    # scope), so the stamp carries them from the start rather than
    # leaving accept_delivery to invent them.
    stamp = {"tier": tier, "class": risk_class, "reviewer": reviewer,
             "unmeasured_classes": classes[1:], "state": "", "findings": [],
             "reviewed_revision": rev, "scope": list(files or [])}
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
        entry = {
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
        }
        if state == receipt_door.FINDING_CONFIRMED:
            # WBS-10.04's bridge: only a CONFIRMED finding (its own check
            # re-ran and failed) is a candidate for auto-repair at all. See
            # repair_confirmed_finding above for the five exceptions and the
            # guard that keeps a passing re-verification from being trusted
            # on its own.
            repair_confirmed_finding(row, entry, risk_class, cwd,
                                     cmd=repair_cmd, worker=repair_worker,
                                     tools=repair_tools, runner=runner,
                                     max_attempts=repair_max_attempts)
        stamp["findings"].append(entry)
    return stamp


def review_rows(rows, claims, cwd, cmd=None, ask=None, runner=None,
                diff_runner=None, cap=MAX_REVIEWED_UNITS, repair_worker=None,
                repair_tools=None, repair_cmd=None, repair_max_attempts=None):
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
                            ask=ask, runner=runner, diff_runner=diff_runner,
                            repair_worker=repair_worker,
                            repair_tools=repair_tools, repair_cmd=repair_cmd,
                            repair_max_attempts=repair_max_attempts)
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

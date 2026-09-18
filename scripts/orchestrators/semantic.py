#!/usr/bin/env python3
"""ORCH-07 concrete sibling of scripts/orchestrators/execution.py: the
semantic-role orchestrator adapter.

WHY THIS EXISTS. A night run's orchestrating decision splits across two
roles: an EXECUTION role (scripts/orchestrators/execution.py, built and
measured against the app-bundled Codex binary) and a SEMANTIC role, which
owns decomposition, architecture choices, risk and evidence classification,
review, failure triage and re-planning. This module is that second role's
adapter, satisfying the exact same contract as its sibling
(base.OrchestratorAdapter): the per-call deadline, empty-is-failure, retry
once and never identically, failure classes drawn from
scripts/orchestrator_invariants.py, the answering-model identity check, no
full-access bypass, no canonical write, and health measured rather than
inferred.

THE ONE RULE THAT DOMINATES THIS MODULE IS ABOUT HONESTY, NOT CODE. Unlike
execution.py, whose binary path, sandbox flags and failure modes this
estate has already paid to learn (its own docstring cites the app-bundled
Codex path and the missing `timeout` command as measured facts), NOBODY
HAS MEASURED how the semantic role is actually invoked on this machine.
Every fact execution.py states as settled, this module must instead treat
as unknown:

  - The invocation command comes from ONE environment variable, named by
    the module constant SEMANTIC_ORCHESTRATOR_CMD (its value is the string
    "SEMANTIC_ORCHESTRATOR_CMD", the env var key itself). There is no
    hard-coded binary path here, because there is no measured binary to
    name, unlike CODEX_BIN_DEFAULT in execution.py.
  - health() returns UNAVAILABLE, never HEALTHY, whenever that variable is
    absent, empty, or whitespace-only, and names the reason through
    health_reason() (see below for why the reason lives in a second method
    rather than widening health()'s own return type). It is never upgraded
    to HEALTHY on the assumption that the execution role's model is "close
    enough" for a semantic job: a semantic decision made by an unmeasured
    stand-in is worth less than an honest NO-DATA.
  - invoke() with no command configured RAISES (AdapterRefused), rather
    than returning a failed ActionResult. A returned result, even a failed
    one, is a claim that this adapter tried to reach a model and reports
    what happened; with nothing configured, no attempt was made, and
    returning anything but an exception would be exactly the kind of
    fabricated data point this estate's NO FABRICATION rule forbids
    (docs/decisions/... governs prose and reports; the same discipline
    binds a return value here). A command that IS configured but fails to
    start (missing path, not executable) is a different, ordinary case:
    that goes through the normal classified-failure path
    ("tool_unavailable"), never a raise, exactly like execution.py's own
    missing-binary handling.

WHY health_reason() IS A SEPARATE METHOD FROM health(). base.health()'s
contract, shared with execution.py, is "a member of
orchestrator_invariants.HEALTH_STATES", a bare string every caller already
relies on. Widening that return type here (to a tuple, or a dict) would
diverge this adapter from the shared contract the whole point of base.py
is to hold constant across families. The reason a health() call returned
what it returned is real information an operator needs (module docstring's
CONTINGENCY paragraph below), so it is exposed through health_reason(),
set as a side effect of the most recent health() call, rather than forcing
every caller of health() to unpack a richer shape it never asked for.

DECOMPOSITION PROPOSALS ARE THE ONE THING THIS ROLE NEEDS THAT ITS SIBLING
DOES NOT. orchestrator-event-v1's PROPOSE-DECOMPOSITION action (validated
by the inherited invoke()/`_classify` path exactly like any other action)
is deliberately a thin signal: task_id and a free-text reason, nothing
more, because docs/schema/orchestrator-event-v1.json fixes
additionalProperties: false on every branch and is not this unit's to
widen. The actual proposed split, with real children the orchestrator can
turn into graph nodes, is a SEPARATE document this module defines and
validates itself: propose_decomposition() below, returning a
DecompositionProposal (accepted, carrying validated children; or refused,
carrying why), matching the same shape the worker contract that dispatched
this very unit already uses for the identical situation
(`{"status": "NEEDS-DECOMPOSITION", "reason": "...", "proposed_children":
[...]}`). A proposal missing a title, an owns list, or a done_check for
any child is refused outright: a child without a done check is not a unit,
it is a wish, and this module never lets that wish reach the orchestrator
dressed as a plan.

propose_decomposition() makes exactly ONE bounded model call, never a
retry loop of its own. This is a deliberate, narrower choice than
invoke()'s retry-once policy, made for two reasons: first, the worker
contract this very module exists under forbids building "a second
scheduler ... a second integration engine" for what base.py's shared
retry-once-never-identically machinery already exists to do once, and a
decomposition-specific retry loop would be exactly that, duplicated for a
document shape base.py was never asked to know about; second, a
decomposition proposal is advisory input to a human-supervised graph edit
(the worker contract: "The orchestrator ... decides whether those children
become real graph nodes"), not an automated action applied to canonical
state, so a single honest failure (reported as a refused proposal naming
why) is a smaller risk here than the complexity of a second retry engine
would be worth. The deadline is still enforced, and empty and malformed
answers are still refused, never treated as ok.

A NOTE ON A NAME THAT DOES NOT MATCH THIS BUILD'S OWN WBS ENTRY.
docs/plan/ORCH-1020-WBS.json's ORCH-07 objective text names the env var
FABLE_ORCHESTRATOR_CMD; the worker brief that actually dispatched this
unit named it SEMANTIC_ORCHESTRATOR_CMD and was explicit that this is the
constant's name. This module follows the dispatching brief, both because
it is the more specific, later instruction and because the whole point of
this module is to stop assuming a specific vendor model fills this role
before that is measured: naming the constant after the ROLE (semantic)
rather than a specific model brand (Fable) is consistent with that. This
discrepancy is recorded here rather than silently resolved, per this
estate's own rule that an assumed reconciliation is not a checked one; the
orchestrator that dispatched this unit should reconcile the WBS text.

CONTINGENCY. How this adapter fails, and in which direction: every
uncertain path (unset variable, unresolvable command, a launch failure, a
timeout, an empty or malformed answer, a wrong task_id, an invalid
decomposition proposal) fails CLOSED, toward UNAVAILABLE / a raised
AdapterRefused / a refused ActionResult or DecompositionProposal, never
toward a fabricated HEALTHY or a best-effort guess at what the model
meant. A caller that catches AdapterRefused from invoke() or
propose_decomposition() recovers by either configuring
SEMANTIC_ORCHESTRATOR_CMD or routing that unit's work to the execution
role instead (scripts/orchestrators/execution.py); it must never catch the
exception and silently proceed as if a decision had been made. An operator
who sees health() report UNAVAILABLE with health_reason() naming "not
configured" has exactly two correct responses: set the environment
variable to a real invocation, or route the work elsewhere. Pretending the
role is present is not a third option.

NO FULL-ACCESS BYPASS, NO CANONICAL WRITE. This module builds no sandbox
flag of its own (unlike execution.py's SANDBOX_MODE = "workspace-write":
there is nothing measured to base such a flag on for an arbitrary
configured command). What it DOES do, on every invocation, is call
base.assert_safe_invocation() on the exact argv it is about to run,
reusing the sibling's own frozen forbidden-fragment list rather than
restating it: an operator-configured command containing "danger-full-
access", "merge", "push" or "--force" is refused before it is ever run.
This is not a claim that a clean argv is thereby proven safe; it is only
not proven unsafe by this one check, exactly as base.py's own docstring
for that function says.

Python 3.9 floor, standard library only, no network beyond the subprocess
call itself, and no shell: SEMANTIC_ORCHESTRATOR_CMD is split with
shlex.split() into a plain argv, never handed to a shell, so it can never
carry a shell-only construct (a pipe, a redirect) as a hidden bypass.
"""

import json
import os
import shlex
import shutil
import subprocess

from orchestrators import base

import orchestrator_invariants  # noqa: E402 (path already set up by base's import)

#: The environment variable naming this adapter's invocation. Its value IS
#: this string: the constant names the variable, it does not hold an
#: already-resolved command. See the module docstring for why this is
#: role-named (SEMANTIC) rather than model-named (FABLE), and why it is a
#: bare required variable rather than a default with a fallback binary:
#: there is no measured binary to fall back to.
SEMANTIC_ORCHESTRATOR_CMD = "SEMANTIC_ORCHESTRATOR_CMD"


def semantic_cmd(env=None):
    """The configured argv (a list of str) for the semantic role's
    invocation, or None when SEMANTIC_ORCHESTRATOR_CMD is absent, empty,
    or holds only whitespace. Split with shlex.split(), never handed to a
    shell: a caller wanting a pipeline or redirection must wrap it in a
    script of their own and name that script here, not rely on shell
    syntax slipping through this function.

    Read fresh from `env` (os.environ by default) every time this is
    called, never cached at import time: a caller that sets the variable
    after this module is imported must still be honoured.
    """
    env = os.environ if env is None else env
    raw = env.get(SEMANTIC_ORCHESTRATOR_CMD)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return shlex.split(raw)


def _resolves(argv):
    """True when argv[0] looks like something actually runnable: an
    absolute or relative path that exists and is executable, or a bare
    name resolvable via PATH. False otherwise. Never raises: this is a
    best-effort disk check for health() to report, not a guarantee the
    process will actually launch (a same-named directory, a race with
    something deleting the file) is why _run_attempt still has its own
    OSError handling regardless of what this returns."""
    exe = argv[0]
    if os.sep in exe or (os.altsep and os.altsep in exe):
        return os.path.isfile(exe) and os.access(exe, os.X_OK)
    return shutil.which(exe) is not None


class DecompositionProposal(object):
    """The outcome of one propose_decomposition() call, win or lose, never
    partially filled, mirroring base.ActionResult's own discipline for the
    same reason: "close enough" does not exist here.

    ok: True only when every child in the proposal carries a title, a
        non-empty owns list, and a done_check.
    children: the validated list of child dicts when ok is True, else
        None.
    reason: why this proposal was refused, when ok is False, else None.
        Always a human-readable string, never blank: a refused proposal
        with no stated reason is as useless to the orchestrator that
        reads it as an unexplained action would be.
    raw: the model's own raw text this attempt produced, kept even on
        refusal, for audit.
    """

    def __init__(self, ok, children, reason, raw):
        ok = bool(ok)
        if ok:
            if reason is not None:
                raise base.AdapterRefused(
                    "an accepted DecompositionProposal carries no reason, "
                    "got %r" % (reason,))
            if not children:
                raise base.AdapterRefused(
                    "an accepted DecompositionProposal must carry at "
                    "least one validated child")
        else:
            if children is not None:
                raise base.AdapterRefused(
                    "a refused DecompositionProposal carries no children, "
                    "got %r" % (children,))
            if not reason:
                raise base.AdapterRefused(
                    "a refused DecompositionProposal must name a reason")
        self.ok = ok
        self.children = children
        self.reason = reason
        self.raw = raw

    def __repr__(self):
        if self.ok:
            return "DecompositionProposal(ok=True, children=%r)" % (self.children,)
        return "DecompositionProposal(ok=False, reason=%r)" % (self.reason,)


def _validate_children(children, parent_owns, parent_done_check):
    """None when `children` is a clean, acceptable list; else a
    human-readable reason string naming the first problem found. Checked
    in order so the reason always points at the FIRST thing wrong, never
    a summary that hides which child failed.

    Refuses: anything that is not a list; an empty list (zero children is
    not a decomposition of anything); any child that is not an object, or
    is missing a non-blank title, a non-empty owns list, or a non-blank
    done_check; and the degenerate case of exactly one child whose owns
    and done_check exactly match the parent unit's own (a single child
    that owns the same paths and is checked the same way is a relabeling,
    not a decomposition).
    """
    if not isinstance(children, list):
        return "proposal carries no 'children' list"
    if len(children) == 0:
        return "a decomposition proposal with zero children is not a decomposition"
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            return "child %d is not an object" % index
        title = child.get("title")
        owns = child.get("owns")
        done_check = child.get("done_check")
        if not isinstance(title, str) or not title.strip():
            return "child %d is missing a title" % index
        if not isinstance(owns, list) or not owns:
            return "child %d is missing a non-empty owns list" % index
        if not isinstance(done_check, str) or not done_check.strip():
            return ("child %d is missing a done_check: a child without one "
                     "is not a unit, it is a wish" % index)
    if len(children) == 1:
        only = children[0]
        if (set(only.get("owns") or []) == set(parent_owns or [])
                and only.get("done_check") == parent_done_check):
            return ("the single proposed child owns the same paths and "
                    "carries the same done_check as the parent unit: this "
                    "is not a decomposition")
    return None


def _reported_model(stdout):
    """The model identity a JSONL-shaped answer reports for this turn, or
    None when no line says so. Never raises on unparsable input: a stdout
    that is not JSONL at all is the "prose" case the classification path
    already turns into a failure, and this function's job is limited to
    extracting an identity when one is genuinely present. Mirrors
    execution.py's own `_reported_model`, kept as a small, separate copy
    rather than an import from that module: the two roles' answer shapes
    are independently unmeasured, and this module is not entitled to
    assume they will ever match."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            model = event.get("model") or event.get("model_reported")
            if isinstance(model, str) and model:
                return model
    return None


def _build_action_prompt(capsule, attempt_number, prior_note):
    """The prompt for one standard action-envelope attempt, built only
    from what the capsule already carries (never a repository walk,
    mirroring execution.py's own discipline), with attempt_number and
    prior_note folded in so a retry is provably different from the first
    attempt."""
    lines = [
        "unit_id: %s" % capsule.get("unit_id", ""),
        "objective: %s" % capsule.get("objective", ""),
        "contract_fragment: %s" % capsule.get("contract_fragment", ""),
        "attempt: %d" % attempt_number,
    ]
    if prior_note:
        lines.append("prior_note: %s" % prior_note)
    lines.append(
        "Answer with exactly one JSON object matching "
        "docs/schema/orchestrator-event-v1.json. No prose, no markdown "
        "fences, nothing before or after the object.")
    return "\n".join(lines)


def _build_decomposition_prompt(capsule):
    """The prompt for the one bounded decomposition attempt. Deliberately
    distinct wording from _build_action_prompt so the two request shapes
    are never confused by whatever is on the other end of
    SEMANTIC_ORCHESTRATOR_CMD."""
    lines = [
        "unit_id: %s" % capsule.get("unit_id", ""),
        "objective: %s" % capsule.get("objective", ""),
        "contract_fragment: %s" % capsule.get("contract_fragment", ""),
        "This unit is too broad to dispatch as one piece of work. Propose "
        "how to split it into smaller units.",
        'Answer with exactly one JSON object of shape {"children": '
        '[{"title": str, "owns": [str, ...], "done_check": str}, ...]}. '
        "No prose, no markdown fences, nothing before or after the "
        "object. A child missing any of the three fields will be "
        "refused.",
    ]
    return "\n".join(lines)


class SemanticAdapter(base.OrchestratorAdapter):
    """Drives whatever SEMANTIC_ORCHESTRATOR_CMD names, one bounded call
    at a time, through base.OrchestratorAdapter's shared retry, deadline
    and classification machinery, plus this module's own decomposition
    proposal path. See the module docstring for the honesty rules this
    class exists to enforce: never HEALTHY without a configured command,
    never a returned result from invoke() when nothing was configured to
    call."""

    name = "semantic"

    def __init__(self, requested_model=None, cmd=None, runner=None, env=None):
        super(SemanticAdapter, self).__init__(requested_model=requested_model)
        self._env = os.environ if env is None else env
        #: An explicit `cmd` overrides the environment for testing; a real
        #: caller leaves this as None and gets semantic_cmd(self._env).
        #: None (not falsy-but-truthy) is the only value indistinguishable
        #: from "not configured" here, matching semantic_cmd()'s own
        #: contract.
        self._cmd = cmd if cmd is not None else semantic_cmd(self._env)
        #: Injected so tests never spawn a real process; a real caller
        #: leaves this as subprocess.run.
        self._runner = runner or subprocess.run
        #: Set by _run_attempt() before each attempt, read by _classify()
        #: to catch an answer naming a different task_id than the one
        #: this call actually asked about. None outside a call in flight.
        self._expected_task_id = None
        #: The human-readable reason behind the most recent health() call.
        #: See the module docstring for why this is a separate method
        #: rather than part of health()'s own return value.
        self._health_reason = "health() has not been called yet"

    def _require_configured(self):
        """Raise AdapterRefused unless SEMANTIC_ORCHESTRATOR_CMD resolved
        to a non-empty argv. Called at the start of every entry point
        that would otherwise attempt to build an invocation: invoke() and
        propose_decomposition() both call this before doing anything
        else, so an unconfigured adapter never reaches build_argv() with
        nothing to build from."""
        if not self._cmd:
            raise base.AdapterRefused(
                "SEMANTIC_ORCHESTRATOR_CMD is not configured: refusing to "
                "invoke the semantic role rather than fabricate a result "
                "or fall back to the execution role's model. Configure "
                "the environment variable, or route this unit's work to "
                "scripts/orchestrators/execution.py instead.")

    def health(self):
        """UNAVAILABLE whenever SEMANTIC_ORCHESTRATOR_CMD is not
        configured, or configured to something that does not resolve to
        a runnable command; STARTING when it resolves, meaning "not
        provably unavailable for the one reason this method can check",
        exactly as execution.py's own health() reports for its binary.
        NEVER HEALTHY: this adapter has no long-running process or
        heartbeat file to poll, only a configuration and a disk check,
        and neither of those is evidence the model on the other end is
        actually answering well. See health_reason() for why."""
        if not self._cmd:
            self._health_reason = (
                "SEMANTIC_ORCHESTRATOR_CMD is not configured")
            return "UNAVAILABLE"
        if _resolves(self._cmd):
            self._health_reason = (
                "configured command resolves on disk: %r" % (self._cmd,))
            return "STARTING"
        self._health_reason = (
            "configured command does not resolve to a runnable "
            "executable: %r" % (self._cmd,))
        return "UNAVAILABLE"

    def health_reason(self):
        """The human-readable reason behind the most recent health()
        call's verdict. Call health() first; this only reports on that
        call, it never measures anything itself."""
        return self._health_reason

    def build_argv(self):
        """The argv this adapter would run: the configured command,
        unmodified (there is no measured sandbox flag to add, unlike
        execution.py's SANDBOX_MODE). Always checked against
        base.assert_safe_invocation() before it is returned, so a
        configured command containing a forbidden fragment is refused
        here rather than at the point it would have been run."""
        argv = list(self._cmd)
        base.assert_safe_invocation(argv)
        return argv

    def _call_model(self, prompt, budget_s):
        """One bounded subprocess call, shared by the standard action
        path (_run_attempt) and the decomposition path
        (propose_decomposition). Never raises for an ordinary model
        failure: a timeout or a launch OSError are both reported through
        the returned dict, exactly as execution.py's own _run_attempt
        does for its role. Raises only for a genuinely unsafe configured
        argv, via build_argv() -> assert_safe_invocation(), which is a
        configuration defect, not a model failure, and belongs outside
        this method's try/except for the same reason execution.py builds
        its own argv outside its try/except."""
        argv = self.build_argv()
        try:
            completed = self._runner(
                argv, input=prompt, capture_output=True, text=True,
                timeout=budget_s)
        except subprocess.TimeoutExpired:
            return {"raw": "", "timed_out": True, "model_reported": None}
        except OSError as exc:
            return {"raw": "", "timed_out": False, "model_reported": None,
                    "failure_class": "tool_unavailable", "os_error": str(exc)}
        return {
            "raw": completed.stdout or "",
            "timed_out": False,
            "model_reported": _reported_model(completed.stdout or ""),
        }

    def _run_attempt(self, capsule, *, budget_s, attempt_number, prior_note):
        self._expected_task_id = capsule.get("unit_id")
        prompt = _build_action_prompt(capsule, attempt_number, prior_note)
        return self._call_model(prompt, budget_s)

    def invoke(self, capsule, *, deadline_s, now=None):
        """Same contract as base.OrchestratorAdapter.invoke(), with one
        addition checked before anything else: SEMANTIC_ORCHESTRATOR_CMD
        must be configured, or this raises AdapterRefused instead of
        attempting a call (module docstring). The deadline is still
        checked here first, ahead of the configuration check, so a caller
        passing both a bad deadline and no command sees the same
        deadline-shaped refusal base.py would give on its own, rather
        than a configuration error masking it."""
        if deadline_s is None or deadline_s <= 0:
            raise base.AdapterRefused(
                "deadline_s must be a positive number, got %r" % (deadline_s,))
        self._require_configured()
        return super(SemanticAdapter, self).invoke(
            capsule, deadline_s=deadline_s, now=now)

    def _classify(self, raw_attempt):
        """base's own classification, plus one check base has no way to
        make: that the answering model's action names the SAME task_id
        this attempt actually asked about. A task-scoped action carrying
        a different task_id is not a valid answer to this call, even
        though it might validate cleanly against the schema on its own;
        classified missing_evidence, the same class base.py already uses
        for an unmatched model identity, because an answer about the
        wrong task is evidence this estate has no valid basis to act on
        here, for the same reason. NOOP and CLOSEOUT carry no task_id at
        all (the schema forbids it), so this check only ever fires on a
        task-scoped action."""
        result = super(SemanticAdapter, self)._classify(raw_attempt)
        if result.ok and self._expected_task_id is not None:
            answered_task_id = result.action.get("task_id")
            if answered_task_id is not None and answered_task_id != self._expected_task_id:
                return base.ActionResult(
                    False, None, "missing_evidence", result.raw,
                    result.model_reported)
        return result

    def propose_decomposition(self, capsule, *, deadline_s, now=None):
        """One bounded call asking the semantic role how to split
        `capsule`'s unit, returning a DecompositionProposal: accepted,
        carrying validated children, or refused, carrying why. See the
        module docstring for why this makes exactly one attempt rather
        than reusing invoke()'s retry-once loop.

        Raises AdapterRefused, before any attempt, for the same two
        reasons invoke() does: a non-positive deadline, or no configured
        command. Never returns a DecompositionProposal for either of
        those cases: a proposal, even a refused one, is a claim that this
        adapter tried and reports what happened, and neither case
        involved a real attempt.
        """
        if deadline_s is None or deadline_s <= 0:
            raise base.AdapterRefused(
                "deadline_s must be a positive number, got %r" % (deadline_s,))
        self._require_configured()
        self.last_invoked_at = self._now(now)
        prompt = _build_decomposition_prompt(capsule)
        raw_attempt = self._call_model(prompt, float(deadline_s))
        return self._classify_decomposition(
            raw_attempt, capsule.get("owns") or capsule.get("write_scope"),
            capsule.get("done_check"))

    def _classify_decomposition(self, raw_attempt, parent_owns, parent_done_check):
        raw = raw_attempt.get("raw") or ""
        if raw_attempt.get("timed_out"):
            return DecompositionProposal(
                False, None,
                "timed out waiting for a decomposition proposal", raw)
        override = raw_attempt.get("failure_class")
        if override:
            # Validated against the frozen taxonomy exactly like base's
            # own _classify: an unrecognised class raises rather than
            # being accepted as a plausible-looking reason string.
            orchestrator_invariants.classify_failure(override)
            return DecompositionProposal(
                False, None, "tool failure: %s" % override, raw)
        if not raw.strip():
            return DecompositionProposal(
                False, None,
                "empty answer to a decomposition request", raw)
        try:
            parsed = json.loads(raw)
        except ValueError:
            return DecompositionProposal(
                False, None, "answer was not valid JSON", raw)
        if not isinstance(parsed, dict):
            return DecompositionProposal(
                False, None, "answer was not a JSON object", raw)
        problem = _validate_children(
            parsed.get("children"), parent_owns, parent_done_check)
        if problem:
            return DecompositionProposal(False, None, problem, raw)
        return DecompositionProposal(True, parsed["children"], None, raw)

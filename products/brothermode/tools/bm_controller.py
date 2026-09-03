#!/usr/bin/env python3
"""tools/bm_controller.py: U2, the durable Full-Auto controller ENGINE
(design docs/superpowers/specs/2026-08-05-l03-controller-design.md; store
schema 15, tools/bm_store.py section "U2: the durable Full-Auto
controller").

WHAT THIS IS
  The resumable, nineteen-step loop (design section 3) that sequences a
  controller run from an outcome to a checked deliverable: ORIENTING ->
  PLANNING -> READY -> EXECUTING -> VERIFYING -> CHECKPOINTED -> ...  ->
  DELIVERABLE_READY, holding NO state in memory across a crash. Every
  forward move is committed to tools/bm_store.py's schema-15 tables
  BEFORE the externally visible effect it authorises (the durability
  contract, design section 1), so a fresh ControllerEngine constructed
  against the same store reconstructs exactly where the prior one was and
  resumes without repeating completed work.

  This file issues NO SQL of its own (the same house law bm_autonomy.py
  states for itself): every persistence call goes through one of the
  seventeen Store methods tools/bm_store.py's U2 section defines
  (open_run, set_run_state, upsert_units, select_ready_units, claim_unit,
  release_claimed_unit, record_dispatch, record_result,
  record_verification, mark_unit_done, mark_unit_failed, block_lane_units,
  unblock_lane_units, get_run, list_units, get_dispatch,
  list_dispatches) or a store primitive U2 REUSES verbatim
  from U1 (gate_check, spend_totals, latest_contract, record_spend,
  record_checkpoint, record_interruption, queue_human_step,
  list_human_steps, claim, transition). A structural guard,
  TestNoSQLGuard in tools/test_bm_controller.py, copied from
  tools/bm_autonomy.py's own (itself copied from tools/bm_project.py's),
  parses this file with ast and fails the build if a SQL-shaped string
  literal or a sqlite3 import ever appears here.

WHAT THIS IS NOT
  Not a process that spawns real subagents and blocks on them. The
  controller is a HARNESS the orchestrating model drives, not an
  independent worker pool (design's own load-bearing honesty statement,
  section 0). "Dispatch" means record-intent-and-await: the production
  WorkerAdapter (RecordIntentWorker below) writes a unit's brief to
  stdout for the orchestrating model to pick up and returns control
  immediately; the run parks in EXECUTING until a result arrives through
  receive_result(), which Writer B's `bm-controller record-result`
  command calls. This file never calls a model and never spawns one.

THE HARNESS SEAM (design section 4)
  Two boundaries are inherently impure: the worker's cognition (whether
  the model made the right change) and the subprocess that runs a
  done-check, a verifier, or a rollback command. BOTH are isolated behind
  a single, narrow, injected interface each (WorkerAdapter, CheckRunner),
  so ControllerEngine itself is deterministic and fully testable with a
  FakeWorker and a FakeCheckRunner (tools/test_bm_controller.py), no live
  model and no network. SubprocessCheckRunner below is the ONLY place in
  this file that runs a FOUNDER-AUTHORED command (a done_check, a
  verifier, or a rollback). The unattended preflight's fence canary
  (_default_fence_canary_runner, see "THE UNATTENDED PREFLIGHT" below) is
  the one other place `subprocess` is reached, and it never runs anything
  founder- or model-authored: its argv is the fixed literal
  [sys.executable, tools/bm_fence_hook.py, "hook"]. Both are pinned by
  name in tools/test_bm_controller.py's _EXEC_PRIMITIVE_HOMES; a third
  call site added anywhere else still fails that guard.

AT-MOST-ONCE, HONESTLY (design section 5's own honesty note, restated)
  SQLite writes are transactional and idempotent by the store's own
  UNIQUE keys (controller_dispatches.UNIQUE(unit_id, attempt) is the
  exactly-once spine), so the RECORDING of an accepted result is
  exactly-once. A real worker's real file edit or real git commit is NOT
  something this file can make exactly-once; what this engine provides
  is at-most-once RE-DISPATCH (the dispatch UNIQUE key plus the held
  fence), idempotent recording, and a post-crash re-verification through
  the SAME independent done-check rather than trusting that a prior side
  effect happened. docs/FULL-AUTO.md (Writer B) states this limit for
  the founder.

USER-FACING STRINGS NAME NO REPO-RELATIVE PATH
  The one string this file writes (the unit brief RecordIntentWorker
  prints to stdout) carries only structural fields already stored on the
  unit's own definition; nothing here composes a path relative to this
  repository's checkout location.

THE UNATTENDED PREFLIGHT (see the section of the same name below)
  `--unattended` on `start` or `step` refuses the run unless eight
  preconditions hold. It changes NOTHING without that flag: interactive
  behaviour is identical, pinned by
  TestUnattendedPreflightLeavesInteractiveUseAlone.

  One of those eight, the fence canary, actually RUNS
  tools/bm_fence_hook.py as a real subprocess against a throwaway store,
  rather than only reading the two environment variables the other fence
  preconditions read. That closes an audited gap (Codex, 2026-08-10):
  under a runtime that never wires the hook into PreToolUse at all, both
  variables can read exactly right while nothing is ever checked. What it
  proves is narrow and stated as narrow everywhere it appears: the hook
  BINARY refuses a foreign write when it is actually run. It cannot prove,
  and is never worded to imply, that the runtime about to go unattended
  will call that hook on any write it makes; that is a separate question
  this file has no way to see.

  Three of those preconditions are repository facts, and they are answered
  by tools/bm_autosave.py, the only OTHER shipping module this repository
  lets import `subprocess` (tools/test_bm.py's allowlist names exactly the
  two). So the sentence below stays exactly true of THIS file, and here is
  the part it does not say: under `--unattended`, and only then, the
  preflight calls a sibling module that starts git processes of its own.
  That is the boundary tools/test_bm_controller.py's own
  _execution_primitive_offences docstring already declares it cannot see,
  used deliberately and named here rather than discovered later. Reusing
  bm_autosave.py is the point: one snapshot mechanism, not two stories
  about what is recoverable.

Python 3.9, standard library only. No network. `subprocess` is reached in
exactly two places: SubprocessCheckRunner.run, which runs the founder's OWN
done_check/verifier/rollback commands and never anything this file invents,
and _default_fence_canary_runner, which runs the fixed, hardcoded fence-hook
probe described above and never anything founder- or model-authored.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import datetime
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the exact technique tools/
    bm_autonomy.py and tools/bm_project.py use for tools/bm_store.py: this
    file may be imported from arbitrary working directories and must not
    depend on whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
# Loaded at module level: by the time this file exists, Writer A's own
# schema-15 landing has already happened in this same working tree, the
# same reasoning bm_autonomy.py's own module-level load states for
# schema 14. The class-identity trap tools/bm_project.py documents still
# applies: every bs.OwnershipRefused caught below is from THIS load,
# never a second independent one.

bm_plan = _load("bm_plan")
# F2 and R7 (the plan gate this file's own docstring above does not yet
# describe because it predates this row of the readiness board): a plan
# is a versioned JSON artifact tools/bm_plan.py owns end to end, with no
# SQL and no dependency on tools/bm_store.py's schema. This file reads it
# through PlanGate/StorePlanGate below and nothing else here ever touches
# a plan file directly.

def _load_messages():
    """bm_messages.py, cached in sys.modules rather than re-executed on
    every call the way the generic _load() above deliberately is for
    bm_store (that file's own "class-identity trap" note explains why a
    FRESH bm_store load matters elsewhere in this project).

    RF-4 needs the opposite property here: tools/bm_visual.py loads this
    same file independently, by the same by-path technique, and its
    REFUSAL_HELP entries must be the SAME objects this file's
    UNATTENDED_REFUSAL_HELP holds, or tools/test_bm_visual.py's identity
    check could never pass even when both files import correctly, since
    two independent by-path loads of any module never produce `is`-equal
    objects. Caching is safe here specifically because bm_messages.py is
    constants only: no state a fresh reload would ever need to reset."""
    cached = sys.modules.get("bm_messages")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "bm_messages", os.path.join(HERE, "bm_messages.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["bm_messages"] = mod
    return mod


bm_msgs = _load_messages()
# RF-4 (GAP-19): the founder-facing unattended-refusal wording lives in
# tools/bm_messages.py, a constants-only module, exactly once. This file
# assigns UNATTENDED_REFUSAL_HELP from it below instead of defining its own
# dict, so tools/bm_visual.py's copy of the same entries can never drift
# from this one: both surfaces now read the same object.


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _default_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(ts):
    """Inverse of bs.now_iso(): UTC, second precision. Never called on a
    timestamp this file did not itself read back from the store, so a
    malformed value here would be a store defect, not user input; no
    degrade-to-unknown branch the way bm_autonomy.py's own display-only
    _elapsed_since needs one."""
    return datetime.datetime.strptime(
        ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# The harness seam (design section 4): two Protocol-shaped base classes.
# Not typing.Protocol: this codebase uses plain duck typing throughout
# (tools/bm_store.py and tools/bm_autonomy.py import no `typing` at all),
# and a bare base class with a documented single method is the same
# contract without a new dependency pattern. Neither is ever instantiated
# directly; both exist so an implementation can `isinstance()`-check
# itself against the seam it fills if it ever wants to.
# ---------------------------------------------------------------------------

class WorkerAdapter(object):
    """The one method every worker adapter implements.

    run(brief) -> WorkerResult, where brief is a UnitBrief dict (see
    ControllerEngine._build_brief) and WorkerResult is a dict:
        {"worker_claim": str, "artifacts": list[str],
         "cost": {"tokens": int, "minutes": int},
         "status": "returned" | "malformed" | "unavailable" | "pending"}

    "pending" is this implementation's own addition beyond the three the
    design's section 4 comment names (returned/malformed/unavailable): it
    is what a genuinely asynchronous adapter (RecordIntentWorker) returns
    to mean "no synchronous answer; the run parks, a result arrives later
    through receive_result()". A synchronous FakeWorker in tests never
    needs it unless it is deliberately simulating that same parked
    state."""

    def run(self, brief):
        raise NotImplementedError


class CheckRunner(object):
    """The one method every check runner implements.

    run(command, cwd) -> CheckOutcome:
        {"exit_code": int, "stdout": str, "stderr": str}"""

    def run(self, command, cwd):
        raise NotImplementedError


class PlanGate(object):
    """R7's THIRD injected harness seam, the same shape as WorkerAdapter
    and CheckRunner above: one method, check(project_id), returning None
    when tools/bm_plan.py's four clause invariant holds and raising
    bm_plan.PlanError naming which clause failed when it does not.
    ControllerEngine calls exactly this method and nothing else, so a
    fake gate can drive the calibration tests with no filesystem at all,
    the same way FakeWorker and FakeCheckRunner already do for their own
    seams.

    OPT IN, THE SAME WAY --unattended IS OPT IN, AND FOR THE SAME REASON.
    ControllerEngine's own plan_gate constructor parameter defaults to
    None, and a None gate means this file checks nothing new: every one
    of the roughly nine thousand lines of tools/test_bm_controller.py
    written before this row of the readiness board existed constructs an
    engine with no plan_gate at all, and making the check unconditional
    here would refuse every one of them, which is not what "the refusal
    IS the feature" means. It means the refusal is real and unskippable
    ONCE ENGAGED, not that engaging it retroactively breaks every run
    that predates it. Wiring this on by default for every controller run
    is a follow up decision for the CLI's own defaults (see
    _REQUIRE_PLAN_GATE_FLAGS below), not made here."""

    def check(self, project_id):
        raise NotImplementedError


class StorePlanGate(PlanGate):
    """Production plan gate: reads project_id's current plan from an
    injected tools/bm_plan.py PlanStore and runs bm_plan.check_plan_gate
    over it. THE CEILING, stated once here rather than three times: this
    checks that an APPROVED plan VERSION MATCHES what is about to
    execute. It cannot and does not check that the plan was a GOOD plan:
    that the options considered were the right options, that the
    recommendation was sound, or that a founder actually read it before
    approving. Those stay human judgements this file has no way to see."""

    def __init__(self, plan_store):
        self.plan_store = plan_store

    def check(self, project_id):
        self.plan_store.check(project_id)


class RecordIntentWorker(WorkerAdapter):
    """Production adapter (design section 4). Does NOT call a model: it
    writes the brief to stdout in a fixed schema for the orchestrating
    model to pick up, and returns a 'pending' result so the engine parks
    the run in EXECUTING. The run resumes when receive_result() is
    called, which Writer B's `bm-controller record-result` command wraps.
    This is precisely "dispatch means record-intent-and-await": this
    process never blocks on a live model and never spawns one."""

    def __init__(self, out=None):
        self._out = out or _out

    def run(self, brief):
        self._out(json.dumps({"controller_brief": brief}, sort_keys=True))
        return {"worker_claim": "", "artifacts": [],
                "cost": {"tokens": 0, "minutes": 0}, "status": "pending"}


#: THE ENVIRONMENT PREFIX A CHILD OF THIS ENGINE MAY NOT INHERIT
#: (CROSS-FAMILY REFUTATION finding 3, HIGH, DATA DESTRUCTION).
#:
#: git honours GIT_DIR and GIT_WORK_TREE OVER cwd, so a shipped
#: `bm-controller record-result` invoked in an environment where those are
#: set (a git hook, a wrapper script, another repository's tooling) ran the
#: unit's rollback against a DIFFERENT repository and exited 0, which this
#: engine reads as a clean rollback and therefore warns nobody about. The
#: orchestrator reproduced it first hand: two throwaway repositories P and
#: Q, an uncommitted edit in Q, `GIT_DIR=../Q/.git GIT_WORK_TREE=../Q git
#: restore -- a.py` run from inside P, and Q's edit was destroyed.
#:
#: A PREFIX rather than a list, and that is the whole point. The names were
#: enumerated on this machine (git 2.50.1) as the union of three sources
#: rather than from memory: the ENVIRONMENT VARIABLES section of git(1),
#: the environment section of git-config(1), and `strings` over the shipped
#: git binary. That union is 205 distinct GIT_ names, and the ones that
#: really move where git operates are NOT all in the documentation:
#: GIT_CONFIG, GIT_CONFIG_COUNT, GIT_CONFIG_PARAMETERS, GIT_ATTR_GLOBAL,
#: GIT_ATTR_SYSTEM, GIT_GRAFT_FILE, GIT_SHALLOW_FILE, GIT_QUARANTINE_PATH,
#: GIT_IMPLICIT_WORK_TREE, GIT_EXEC_PATH, GIT_TEMPLATE_DIR,
#: GIT_REPLACE_REF_BASE and GIT_PREFIX all appear in git(1)'s own
#: environment section NOWHERE. A hand-kept list would have shipped with
#: those thirteen holes in it and would have grown a fourteenth the next
#: time git added one. The prefix covers every name git reads today and
#: every name it adds later, at the cost of the small, disclosed set below.
#:
#: WHAT THIS COSTS, stated rather than buried: a command that RELIED on an
#: inherited GIT_AUTHOR_NAME, GIT_SSH_COMMAND or GIT_PAGER now sees git's
#: own configured defaults instead. That is the correct direction for this
#: product (a done_check that needs an ambient git identity is one that
#: behaves differently for every founder who runs it), and it is the whole
#: known cost of the rule.
_GIT_ENV_PREFIX = "GIT_"


def _sanitised_env(environ=None):
    """The environment every subprocess this engine starts really gets: a
    copy of the caller's, minus the whole git redirection class.

    Takes `environ` so the rule can be exercised directly on a dictionary,
    which is what makes it testable without a process at all. Everything
    else is passed through untouched, deliberately: PATH, HOME and the
    founder's own tooling variables are what make a legitimate done_check
    work, and an empty environment would break every real check for
    reasons that have nothing to do with safety."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items()
            if not k.startswith(_GIT_ENV_PREFIX)}


class SubprocessCheckRunner(CheckRunner):
    """Production runner. The ONE place in this file that runs a
    FOUNDER-AUTHORED command: the done-check, the verifier command, the
    rollback command, and the final done-definition check, all shell
    command strings stored on the unit or the contract, never text this
    file composes itself. (The unattended preflight's fence canary,
    _default_fence_canary_runner further down, is the other place
    `subprocess` is reached; its argv is a fixed literal this file writes,
    never founder- or model-authored text, which is why it is not gated
    the same way.)

    Every one of them runs in a SANITISED environment (_sanitised_env
    above, CROSS-FAMILY REFUTATION finding 3): the git variables that
    redirect where git reads and writes are stripped before anything
    starts, so no command this engine runs can be pointed at another
    repository by whatever the process that invoked `bm-controller`
    happened to export.

    This is the ONE place the strip can live and be total. The engine has
    exactly one FOUNDER-COMMAND subprocess primitive, pinned structurally
    by tools/test_bm_controller.py's _execution_primitive_offences (see
    _EXEC_PRIMITIVE_HOMES there for the one other named exception), so a
    command site added later inherits the sanitised environment by
    construction rather than by remembering to ask for it."""

    def run(self, command, cwd):
        proc = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True,
            env=_sanitised_env())
        return {"exit_code": proc.returncode, "stdout": proc.stdout,
                "stderr": proc.stderr}


# ---------------------------------------------------------------------------
# The engine.
# ---------------------------------------------------------------------------

#: Run states with an empty CONTROLLER_STATE_TRANSITIONS entry: nothing
#: forward is legal from here (bs.CONTROLLER_STATE_TRANSITIONS is the one
#: source of truth this set is derived from, never hand-duplicated).
_TERMINAL_STATES = frozenset(
    s for s, legal in bs.CONTROLLER_STATE_TRANSITIONS.items() if not legal)

#: The EXACT edges this engine's own pre-result walk traverses
#: (_walk_to_executing then _ensure_verifying), as data rather than as
#: control flow, so the set below can be DERIVED instead of hand-listed
#: and drifting from the walk it claims to describe.
#:
#: FAILED_RECOVERABLE joined this map on 2026-08-05 (REFUTATION-3 SM I).
#: It was already in _RESULT_WALKABLE_STATES, because FAILED_TERMINAL is
#: legal FROM it, but no edge carried it to VERIFYING, so a result was
#: judged from a run state that never reached VERIFYING and the run was
#: left in FAILED_RECOVERABLE with the unit DONE and no delivery
#: attempted. This is not a new edge, a new permission or a widening: the
#: engine ALREADY takes FAILED_RECOVERABLE to READY unconditionally at
#: the top of every step (_resume_result_in_and_orphans), and the store's
#: own table has allowed it all along. It is the walk DATA catching up
#: with what the engine already does.
_RESULT_WALK_EDGES = {"CHECKPOINTED": "READY", "WAITING_HUMAN": "READY",
                      "FAILED_RECOVERABLE": "READY",
                      "READY": "EXECUTING", "EXECUTING": "VERIFYING"}

#: The edges the pre-DISPATCH walk traverses (_walk_to_executing alone),
#: and the edges _deliver_or_hold's own converge-before-delivery walk
#: traverses. Both are derived the same way _RESULT_WALK_EDGES is, for the
#: same reason: a source set that is hand-listed drifts from the walk it
#: claims to describe, and both of these replace a LEGALITY test that was
#: asking the wrong question (REFUTATION-3 SM A and SM B: PAUSED to READY
#: is perfectly legal and is exactly the founder-only edge
#: `bm-controller resume` exists to take).
_DISPATCH_WALK_EDGES = {"CHECKPOINTED": "READY", "WAITING_HUMAN": "READY",
                        "READY": "EXECUTING"}

_DELIVERY_WALK_EDGES = {"EXECUTING": "VERIFYING", "VERIFYING": "CHECKPOINTED",
                        "WAITING_HUMAN": "READY"}

#: The two states delivery itself is legal from (tools/bm_store.py's own
#: table: READY and CHECKPOINTED both list DELIVERABLE_READY).
_DELIVERY_TARGETS = frozenset(("READY", "CHECKPOINTED"))

#: The states CONTROLLER_STATE_TRANSITIONS allows FAILED_TERMINAL from
#: (VERIFYING and FAILED_RECOVERABLE today), derived from the store's own
#: table and never restated: a rejection whose rollback ALSO failed has to
#: be standing on one of these before it attempts that move.
_REJECTABLE_STATES = frozenset(
    s for s, legal in bs.CONTROLLER_STATE_TRANSITIONS.items()
    if "FAILED_TERMINAL" in legal)


def _walk_reaches(state, targets, edges):
    """Does `edges` carry `state` into `targets` without a cycle. The
    generalisation of _walk_reaches_rejectable below, extracted so the
    three source sets this file derives are all derived the SAME way from
    their own edge map (REFUTATION-3: two of the three were a legality
    test against the store's table instead, which answers a different
    question).

    Deliberately NOT general reachability over
    CONTROLLER_STATE_TRANSITIONS, and that restriction is the whole point:
    the store's table would also let PAUSED and DELIVERABLE_READY reach
    EXECUTING, and this engine takes neither edge. Legality is not the
    question; OWNERSHIP of the edge is."""
    seen = set()
    while state not in targets:
        if state in seen or state not in edges:
            return False
        seen.add(state)
        state = edges[state]
    return True


def _walk_reaches_rejectable(state):
    """True when the pre-result walk carries `state` to a state a
    rejection can legally move out of. Deliberately NOT general
    reachability over CONTROLLER_STATE_TRANSITIONS: the store's table
    would also let DELIVERABLE_READY reach VERIFYING through READY, and
    PAUSED reach it directly, but this engine never takes either path (a
    delivered run does not un-deliver itself to swallow a late result, and
    un-pausing is a founder action, design section 1's own words for
    `resume`). Both are therefore LATE RESULT states here, handled
    explicitly rather than walked."""
    return _walk_reaches(state, _REJECTABLE_STATES, _RESULT_WALK_EDGES)


#: Run states from which an arriving result can be walked to a position
#: where judging it, and rejecting it, are both legal moves. Every OTHER
#: run state is a LATE RESULT state (R-L03-refutation-2 F1): the result is
#: real and must still be recorded, verified, rejected and its fence
#: parked, but NO run-state move may be attempted from there, because the
#: store would refuse it and the refusal would swallow the founder warning
#: and the fence release along with it.
_RESULT_WALKABLE_STATES = frozenset(
    s for s in bs.CONTROLLER_STATES if _walk_reaches_rejectable(s))

#: Run states from which this engine may START new work. EXECUTING is in
#: it because a walk already standing on its target reaches it. OUT of it,
#: on purpose and each for its own reason: PAUSED (the founder's edge,
#: REFUTATION-3 SM A), DELIVERABLE_READY (a delivered run is un-delivered
#: by a founder's re-plan, which upsert_units performs, never by this
#: engine walking backwards, SM B), FAILED_RECOVERABLE (reversed to READY
#: by _resume_result_in_and_orphans before any dispatch is considered),
#: and NEW, ORIENTING, PLANNING, VERIFYING, STOPPING plus the three
#: terminal states, none of which any engine edge carries to EXECUTING.
_DISPATCH_SOURCE_STATES = frozenset(
    s for s in bs.CONTROLLER_STATES
    if _walk_reaches(s, frozenset(("EXECUTING",)), _DISPATCH_WALK_EDGES))

#: Run states from which _deliver_or_hold may converge to a state delivery
#: is legal from. PAUSED and FAILED_RECOVERABLE are deliberately absent,
#: which is REFUTATION-3 SM B closed by construction rather than by a
#: legality accident.
_DELIVERY_SOURCE_STATES = frozenset(
    s for s in bs.CONTROLLER_STATES
    if _walk_reaches(s, _DELIVERY_TARGETS, _DELIVERY_WALK_EDGES))

# A walk edge the store's own law does not allow would be a defect in THIS
# file, not in the store, and it must surface at import rather than as an
# OwnershipRefused in the middle of a founder's run. All THREE edge maps
# are checked, not just the result walk: each of them is the sole
# justification for one derived source set above.
for _map_name, _edges in sorted((("_RESULT_WALK_EDGES", _RESULT_WALK_EDGES),
                                 ("_DISPATCH_WALK_EDGES",
                                  _DISPATCH_WALK_EDGES),
                                 ("_DELIVERY_WALK_EDGES",
                                  _DELIVERY_WALK_EDGES))):
    for _from_state, _to_state in sorted(_edges.items()):
        if _to_state not in bs.CONTROLLER_STATE_TRANSITIONS.get(
                _from_state, ()):
            raise RuntimeError(
                "bm_controller's %s walks %s to %s, which the store's own "
                "CONTROLLER_STATE_TRANSITIONS does not allow"
                % (_map_name, _from_state, _to_state))

#: Why a wave stopped, as a CLOSED SET the loop drivers branch on. The
#: whole point (REFUTATION-3 law L3): control flow reads this enum and
#: never the prose. Round 3's stop read a NOTE STRING, so three
#: zero-progress shapes it had not enumerated spun through every max_steps
#: (the soft spend stop, a failing done-definition re-running the
#: founder's whole suite once per wasted step, and the ORDINARY async park
#: the production RecordIntentWorker always produces), while the two it
#: did cover were transient contention it then described to the founder as
#: needing them.
#:
#:   TERMINAL          COMPLETE, STOPPED or FAILED_TERMINAL: it is over
#:   DELIVERED         DELIVERABLE_READY: run `bm-controller complete`
#:   FOUNDER_WAITING   PAUSED, every remaining unit waits on a human, or
#:                     the founder's own done-definition fails
#:   SPEND_STOP        soft spend stop on, nothing in flight
#:   OUTAGE            the worker answered "unavailable"; retry later, no
#:                     founder action
#:   CONTENTION        a fence overlap or a double contract amend deferred
#:                     every unit; NO founder action, and the note may not
#:                     say otherwise
#:   IN_FLIGHT         at least one dispatch is open, awaiting record-result
#:   NOTHING_SELECTABLE  nothing selectable, in flight or founder-gated
#:   CONTRACT_PAUSED   the CONTRACT is paused, which is the only route the
#:                     shipped CLI has to a PAUSED run; `bm-controller
#:                     resume` cannot clear it and `bm-autonomy resume` can
#:   CONTRACT_NOT_LIVE the contract is stopped, revoked or absent, so
#:                     NOTHING was executed and nothing was authorised
#:
#: The last two joined the enum on 2026-08-05 (REFUTATION-4 LV L4-F2 and AZ
#: F5). They are not a re-decision of the eight: FOUNDER_WAITING was being
#: used for a contract pause AND for a run pause, and the note that carried
#: the difference named the wrong command, which is precisely the shape law
#: L3 exists to forbid (control flow reads the enum, never the prose).
#: PLAN_GATE_REFUSED joined the enum with R7 (2026-08-29): the ONE stop
#: reason a plan gate refusal writes, distinct from the generic TERMINAL
#: a gate_check authorisation refusal already writes, so a CLI caller (or
#: a test) can tell "this run's contract stopped authorising it" apart
#: from "this run's plan was never approved, or drifted after it was".
#: See PlanGate's own docstring in the harness seam section above.
STOP_REASONS = ("FOUNDER_WAITING", "CONTENTION", "SPEND_STOP", "OUTAGE",
                "NOTHING_SELECTABLE", "IN_FLIGHT", "DELIVERED", "TERMINAL",
                "CONTRACT_PAUSED", "CONTRACT_NOT_LIVE", "PLAN_GATE_REFUSED")

#: The contract change_kinds Store.set_contract_state writes, every one of
#: which "copies every authorisation column forward verbatim" (that
#: method's own docstring: outcome, done_definition, allowed_paths,
#: allowed_surfaces, risk_classes, both ceilings, signed_by, signed_at). A
#: revision of one of these kinds therefore CANNOT change what a dispatch
#: was authorised to do; only 'sign' and 'amend' can. Whether the
#: authorisation is still LIVE is a separate question, asked separately and
#: earlier by the contract gate (REFUTATION-4 AZ F4: a pause and a resume
#: moved the revision integer by two and the staleness re-read destroyed a
#: held answer that the identical authorisation still covered).
_LIFECYCLE_CHANGE_KINDS = frozenset(("pause", "resume", "stop", "revoke"))

#: The two notes step() writes when a wave achieved nothing at all.
_NOTHING_SELECTABLE_NOTE = ("no unit is currently selectable (dependencies "
                            "unmet or in flight); waiting")
_NOTHING_CLAIMABLE_NOTE = ("no selectable unit could be claimed this wave "
                           "(fence overlap, or gate_check refused and the "
                           "circuit breaker absorbed it); tried again next "
                           "step")

#: One fixed note per stop reason. The loop drivers append NOTHING to
#: these any more (REFUTATION-3 LV 5: run_to_completion used to append
#: "nothing is in flight either, so the run is parked until a founder
#: acts" to whatever note the wave wrote, including the two transient
#: contention notes, so the founder was told to act on a run that another
#: agent's fence release or a quiet contract would unblock on its own).
_NOTE_CONTENTION = ("another writer holds a fence over this unit's files, "
                    "or the contract was amended twice while the unit was "
                    "being checked; nothing was burned and nothing was "
                    "drained, so no founder action is needed for a "
                    "transient overlap and the next step tries the same "
                    "unit again. If this repeats on every step, the fence "
                    "is held by a writer that is not coming back: find the "
                    "active claim over those files and release it")
_NOTE_NOTHING_SELECTABLE = (_NOTHING_SELECTABLE_NOTE +
                            "; nothing is in flight either, so the run is "
                            "parked until a founder acts")
_NOTE_IN_FLIGHT = ("waiting for %d open dispatch(es); record a result "
                   "with bm-controller record-result")
_NOTE_SPEND_STOP = ("soft-stop: %d unit(s) selectable but no new dispatch; "
                    "raise the ceiling or accept the stop")
_NOTE_OUTAGE = ("the worker reported itself unavailable; the dispatch "
                "stays open and the same attempt is retried on the next "
                "step, no founder action is needed")
_NOTE_RUN_PAUSED = ("the controller run is PAUSED; only bm-controller "
                    "resume leaves that state, and this engine will not "
                    "dispatch, judge or deliver until it does")

#: The run is PAUSED *because the contract is*, which is the ONLY route the
#: shipped CLI has to a PAUSED run (there is no `bm-controller pause`).
#: _NOTE_RUN_PAUSED is wrong for it: `bm-controller resume` moves the run
#: to READY and the very next step() re-reads the contract, finds it still
#: paused and writes PAUSED again, so a founder following that note loops
#: forever (REFUTATION-4 LV L4-F2, reproduced through four shipped
#: commands). BOTH commands are named here, in the order they must be run.
_NOTE_CONTRACT_PAUSED = ("the CONTRACT is paused, and that is what pauses "
                         "this run; bm-controller resume alone cannot "
                         "clear it, because the next step re-reads the "
                         "contract and pauses the run again. Clear the "
                         "contract first with bm-autonomy resume, then "
                         "bm-controller resume this run")

#: THE EXECUTION LEDGER, and the three clauses every founder-facing
#: "nothing ran" sentence in this file is built from (REFUTATION-6 F2).
#:
#: Round 6 fixed one such sentence by giving one branch its own constant,
#: and the defect came straight back in the constant this round INTRODUCED:
#: `_NOTE_SPEND_BREAKER` hard-codes "NO command was run for this result:
#: not the unit's done_check, not its verifier, not the rollback and not
#: the whole done-definition", and two of its three use sites are reached
#: in calls that just ran a done_check, a verifier and a `git restore`.
#: docs/FULL-AUTO.md promises the opposite in as many words.
#:
#: A per-branch constant cannot fix that class, because the branch is
#: exactly the wrong thing to derive the sentence from: it says which test
#: refused, not what already executed. So the sentence is derived from the
#: LEDGER ControllerEngine._run_command writes, which is the one funnel
#: every command in this module passes through. A site that runs a command
#: without naming itself to the ledger is a test failure
#: (test_every_command_site_names_itself_to_the_ledger).
#:
#: SCOPE OF THE WORD "this call": the ledger is reset at every PUBLIC entry
#: point, so on `bm-controller record-result`, which handles exactly one
#: result, "this call" and "this result" are the same set. On a `step` or
#: `check_timeouts` wave that judges several units it is the whole wave,
#: which is coarser and still true. docs/KNOWN-LIMITS.md states that.


def _nothing_ran_clause(executed):
    """The clause for a sentence about a brake that stopped the work."""
    if not executed:
        return ("NO command was run for this result: not the unit's "
                "done_check, not its verifier, not the rollback and not "
                "the whole done-definition")
    return ("no command was run after that point, and what this call HAD "
            "already run, each under an authorisation that was live at the "
            "moment it started, is: " + ", ".join(executed))


def _nothing_executed_clause(executed):
    """The same fact in the words the dispatch-closing branch uses."""
    if not executed:
        return ("NO command was executed (no done_check, no verifier, no "
                "rollback)")
    return ("nothing further was executed; this call had already run: "
            + ", ".join(executed))


def _step_execution_clause(executed):
    """The same fact again, in the founder STEP, which is the copy a
    founder still has days later when the summary is long gone."""
    if not executed:
        return ("Nothing was executed for it: not its done_check, not its "
                "verifier and not its rollback, so its write_scope is in "
                "whatever state the worker left it and needs manual "
                "inspection.")
    return ("Nothing further was executed for it; this call had already "
            "run: %s. Its write_scope is in whatever state that left it "
            "and needs manual inspection." % (", ".join(executed),))


#: The contract is stopped, revoked or gone. NOTHING is authorised, and in
#: particular nothing further is EXECUTED: the kill switch stops the unit's
#: own model-authored done_check, its verifier and the git rollback, not
#: just new dispatches (REFUTATION-4 AZ F5).
def _contract_not_live_note(state_word, executed):
    return ("the contract is %s, not live: nothing is authorised, so the "
            "result was recorded and rejected and %s. The fence is parked "
            "and a founder step names the unit"
            % (state_word, _nothing_executed_clause(executed)))

#: A dispatch is open but the run stands where this engine may not start
#: work. `bm-controller resume` no-ops unless the run is PAUSED and
#: `bm-controller complete` is refused by the store from VERIFYING, so
#: naming those two sent a founder toward `stop`, which destroys a run one
#: record-result would have finished (REFUTATION-4 LV L4-F4).
_NOTE_DISPATCH_OPEN_UNMOVABLE = (
    "a dispatch is open but the run is %s, which this engine may not start "
    "work from. The recovery is to record the open dispatch's result: "
    "bm-controller record-result --dispatch-id %s (bm-controller status "
    "lists it). bm-controller resume only moves a PAUSED run and "
    "bm-controller complete is refused from here")

#: The SETTLE path's own sentence (REFUTATION-5 S3, second half). It used
#: to reuse _NOTE_CONTRACT_NOT_LIVE, whose text hard-codes "the result was
#: recorded and rejected and NO command was executed". On the settle path
#: that is a statement about a DIFFERENT branch: by the time a wave
#: settles, this engine may well have run the unit's done_check under a
#: contract that was live when the command started and dead when it
#: returned. One note constant serving two branches whose facts differ is
#: how a founder was told, in the same command, that nothing ran and that
#: their unit was accepted.
_NOTE_SETTLE_CONTRACT_NOT_LIVE = (
    "the contract is %s, not live: this wave was not settled and nothing "
    "was delivered. Whatever this wave already judged keeps its own "
    "verdict, and a founder step names any unit that was rejected; the "
    "next bm-controller step drains the run")

#: The founder pulled the kill switch WHILE a command was running
#: (REFUTATION-5 S3). Round 5 read a stop or a revoke as an
#: authorisation-preserving lifecycle change, so a unit was accepted under
#: a killed contract. It is now rejected exactly as a stale one is, and
#: this sentence is deliberately the one note in this file that says a
#: command DID run.
_NOTE_CONTRACT_DIED_MIDCOMMAND = (
    "the contract became %s WHILE this unit was being checked. The check "
    "had already started under a live authorisation, so it ran; the "
    "authorisation it ran under no longer exists, so the result is "
    "rejected exactly as a stale one is, the fence is parked and the "
    "unit's write_scope needs manual inspection")

#: The founder's SECOND brake (REFUTATION-5 S2). Round 5 gated every
#: command on the contract's LIVENESS and on nothing else, so after
#: `bm-autonomy gate-check` answered REFUSED-BREAKER one `bm-controller
#: record-result` still ran the unit's model-authored done_check AND the
#: founder's whole done_definition, accepted the unit and declared the run
#: DELIVERABLE_READY.
#:
#: REFUTATION-6 F2 turned it from a constant into a function of the
#: LEDGER: two of its three use sites (_settle_after_wave and
#: _deliver_or_hold) are reached in calls that may just have executed the
#: unit's done_check, its verifier and a `git restore` under the round-6
#: carve-out, and the constant told the founder that none of them ran.
def _spend_breaker_note(reason, executed):
    return ("the founder's SPEND CEILING has tripped (%s), so nothing "
            "further is authorised and %s. Raise the ceiling with "
            "bm-autonomy sign --supersede, or accept the stop; the run "
            "drains on the next bm-controller step"
            % (reason, _nothing_ran_clause(executed)))

#: A run whose meter never saw an accepted unit's cost is not a
#: deliverable (REFUTATION-5 S7). See _unreconciled_uncharged_spend.
_NOTE_SPEND_UNRECONCILED = (
    "the done-definition was not run and nothing was delivered: %d "
    "accepted unit(s) cost spend this engine could NOT charge, so the "
    "breaker reads LOW and a ceiling cannot mean anything until that is "
    "fixed. bm-autonomy human-steps --project %s --open names each gap, "
    "the exact numbers and the two commands that close it")

#: The lane the uncharged-spend disclosure is queued into. Deliberately a
#: lane NO unit is ever planned into: Store.select_ready_units skips a
#: whole lane that holds an open human step, and round 5 chose a
#: checkpoint over a founder step for exactly that reason ("disclosing a
#: bookkeeping gap that way would block the very lane the founder is about
#: to resume"). A reserved lane keeps the founder-visible, enumerable,
#: resolvable step AND blocks no work: _reconcile_lane_blocks only ever
#: writes lanes that units are actually in.
SPEND_RECONCILE_LANE = "spend-reconciliation"

#: What makes a step in that lane a SPEND DISCLOSURE rather than merely a
#: step that shares its lane (REFUTATION-6 F5). Round 6 called the lane
#: reserved and reserved nothing: `plan` accepted a unit into it, and
#: _unreconciled_uncharged_spend selected by LANE alone, so ANY step there
#: blocked the whole run's delivery carrying a sentence that named a cause
#: that had not happened ("N accepted unit(s) cost spend this engine could
#: NOT charge") while the meter showed nothing uncharged at all.
#:
#: BOTH halves of the refuter's choice are taken, and they close different
#: routes. _validated_units refuses the lane NAME, which is what makes the
#: shipped documentation true and stops a unit ever being gated by the
#: disclosure. This marker is what stops a step queued into that lane by
#: anything OTHER than the disclosure (an SDK caller, a future tool, a
#: hand-written row) from blocking delivery in the disclosure's name: the
#: block is on the fact the disclosure itself wrote, not on where it sits.
_SPEND_RECONCILE_MARKER = "UNCHARGED SPEND: "

#: git reads a LEADING COLON as pathspec MAGIC, not as a file name
#: (gitglossary(7), "pathspec"): ':/' is the repository root, ':!x' and
#: ':(exclude)x' are "everything except x", ':(icase)X' is a
#: case-insensitive match. None of those characters is a glob character,
#: so the round-5 literal-write-scope rule (which tested only '*', '?' and
#: '[') let them through, and `git restore -- :/` then restored the WHOLE
#: working tree and EXITED 0, so the engine read the rollback as a success
#: and queued no dirty-write-scope warning (REFUTATION-5 S1, the round-6
#: push blocker). ':(literal)' is git's own escape for the whole family;
#: verified against git 2.50.1 in a real repository, where every magic
#: spelling under it becomes a non-existent literal path (exit 1, both
#: tracked files left untouched) and a plain path under it behaves exactly
#: as it did before.
_PATHSPEC_MAGIC_PREFIX = ":"
_PATHSPEC_LITERAL_PREFIX = ":(literal)"


def _pathspec_literal(path):
    """`path` in a form git cannot read as pathspec magic.

    ONLY an entry that could be read as magic is escaped, and that is a
    deliberate choice rather than laziness: a path with no leading colon
    is not magic under any git version, so escaping it would change the
    founder-facing rollback command for every ordinary unit in the product
    (and every reader of it, this repository's own test fixtures included)
    to buy nothing. What is bought by escaping the rest is that the
    composed command is safe by CONSTRUCTION, independently of the
    refusals in _unsafe_scope_entry: if a future edit ever weakened those,
    git would still be handed a file name."""
    if path.startswith(_PATHSPEC_MAGIC_PREFIX):
        return _PATHSPEC_LITERAL_PREFIX + path
    return path


def _git_prefix(root):
    """The `git ...` the rollback starts with: the plain binary, or the
    binary told EXACTLY which repository it is acting on.

    CROSS-FAMILY REFUTATION finding 3, the second of two independent
    defences (the first is _sanitised_env above). A command-line
    --git-dir/--work-tree beats the environment, verified against git
    2.50.1, so even a git variable that survived the strip cannot move
    this command: with GIT_DIR and GIT_WORK_TREE both pointing at an
    unrelated repository Q, the named command restored a.py in THIS
    project and left Q's uncommitted edit exactly where it was.

    The naming is conditional because it has to be honest: a repository
    that is not at the project root is one this engine cannot name, and
    guessing at an ancestor would be a worse answer than git's own
    discovery. So when there is no .git at the root the command stays byte
    for byte the one this engine has always composed, git discovers the
    repository from cwd exactly as before, and _sanitised_env is what
    protects that case. Both branches are pinned by tests.

    A .git FILE (a linked worktree, a submodule) is named the same way as
    a .git directory: git 2.50.1 follows the `gitdir:` indirection through
    --git-dir, verified in a real linked worktree. A root whose .git is
    neither makes git exit non-zero, which is the dirty-write-scope
    warning path, so the founder hears about it instead of the engine
    silently restoring somewhere else."""
    marker = os.path.join(root, ".git")
    if not os.path.exists(marker):
        return "git "
    # bs._quote_path_for_local_shell, NOT shlex.quote. shlex implements POSIX
    # shell quoting and nothing else, and a Windows path is full of
    # backslashes, none of which are in its safe set, so shlex.quote of
    # C:\p\.git returns that path wrapped in SINGLE quotes. cmd.exe treats a
    # single quote as part of the filename, so the rollback would run
    # `git --git-dir='C:\p\.git'`, fail to find the repository, and RESTORE
    # NOTHING on Windows while looking like an ordinary git error. This
    # project already found and retired that pattern once (CI, Windows,
    # 2026-07-31; the reason lives in that helper's own docstring). This call
    # site reintroduced it and an audit caught it before it reached a user.
    return "git --git-dir=%s --work-tree=%s " % (
        bs._quote_path_for_local_shell(marker),
        bs._quote_path_for_local_shell(root))


def _unsafe_scope_entry(entry):
    """None when `entry` is a plain relative path this engine may hand to
    git, to the fence store and to a worker's brief, or a SHORT PHRASE
    naming what is wrong with it, for a founder-facing refusal to quote.

    A phrase rather than a raise, because this is asked in three places
    with three different consequences: `plan` turns it into an
    OwnershipRefused before anything is written, `_authorise_dispatch`
    turns it into a failed unit plus a founder step, and `_rollback_plan`
    turns it into "no rollback was composed at all, and here is why".

    The order of the tests is the order of the damage. Pathspec magic
    first, because that is the one that destroys founder data rather than
    merely misbehaving.

    This is the EXECUTION side of a defence tools/bm_store.py also mounts
    at the DECLARATION side (literal_scope_entry). The duplication is
    deliberate: the store refuses what enters it, and this refuses what
    this engine ACTS on, so a row that reached the engine by any other
    route (a store written by an older version, a hand-written row, an SDK
    caller that never ran `bm-controller plan`) still cannot become a
    `git restore` over the founder's whole working tree."""
    if not isinstance(entry, str):
        return ("is a %s, not a string; a scope entry is one path"
                % type(entry).__name__)
    if not entry.strip():
        return "is empty"
    if entry.startswith(_PATHSPEC_MAGIC_PREFIX):
        return ("begins with %r, which git reads as PATHSPEC MAGIC rather "
                "than as a file name (%r is the repository root and ':!x' "
                "is 'everything except x'), so a rollback naming it would "
                "restore files this unit never declared"
                % (_PATHSPEC_MAGIC_PREFIX, _PATHSPEC_MAGIC_PREFIX + "/"))
    if entry.startswith("-"):
        return "begins with '-', which a command line reads as an option"
    if entry.startswith("~"):
        return "begins with '~', which a shell expands to a home directory"
    if entry.startswith("/") or entry.startswith("\\") or os.path.isabs(entry):
        return "is an absolute path, not a path inside this project"
    if any(ch in entry for ch in ("\0", "\n", "\r")):
        return "contains a NUL byte or a line break"
    try:
        bs.literal_scope_entry(entry)
    except bs.OwnershipRefused as exc:
        return "is refused by the store's own write-scope rule (%s)" % (exc,)
    except (ValueError, TypeError) as exc:
        return "cannot be read as a path (%s: %s)" % (type(exc).__name__, exc)
    return None


def _unsafe_scope_container(field, value):
    """None when `value` is a shape this engine may ITERATE as a list of
    paths, or a founder-facing sentence.

    REFUTATION-5 S4 and S6. A Python string is iterable, so a scope emitted
    as a bare JSON string was walked CHARACTER BY CHARACTER: "a.py" became
    ['a', '.', 'p', 'y'], '.' canonicalises to the project ROOT, the brief
    handed the worker the whole project and the unit's fence held it. A
    scalar was not iterable at all and left an uncaught TypeError out of
    the shipped `plan`. Both are ordinary malformed-output shapes from a
    model that authors the unit graph."""
    if value is None:
        return None
    if isinstance(value, str):
        return ("%s is the bare string %r. A bare string is ONE path, not "
                "an iterable of characters: iterating it would fence and "
                "roll back one character at a time, and '.' is the whole "
                "project root. Write it as a list of one path."
                % (field, value))
    if isinstance(value, (bytes, bytearray)):
        return ("%s is a %s, not a list of paths"
                % (field, type(value).__name__))
    if not isinstance(value, (list, tuple)):
        return ("%s is a %s, not a list of paths"
                % (field, type(value).__name__))
    return None

#: This file's OWN verdict word, never one gate_check returns: the
#: per-path loop could not judge the whole write_scope under one stable
#: contract revision, because an amend landed inside it twice (design step
#: 13's TOCTOU window, seen from the checking side). Contention, not an
#: authorisation gap: the unit is deferred to a later wave, burning no
#: retry and draining nothing.
_DEFERRED_CONTENTION = "DEFERRED-CONTENTION"

#: This file's OTHER own verdict word, and for the same reason: the unit's
#: write_scope is not a SHAPE this engine can iterate as a list of paths,
#: so no path in it can be judged and gate_check is never asked
#: (REFUTATION-6 F4). The DISPATCH route asks _unsafe_write_scope before
#: anything else; the JUDGING route round 6 added asked gate_check
#: straight away and iterated `unit["write_scope"] or []`, so a
#: non-iterable row left an uncaught TypeError out of receive_result, which
#: main() does not catch (it catches bs.BMStoreError and ValueError). A
#: refusal, not a traceback: the population this is for is the one
#: _unsafe_scope_entry's docstring names, a store written by an older
#: version, a hand-written row, an SDK caller that never ran `plan`.
_REFUSED_SCOPE_SHAPE = "REFUSED-SCOPE-SHAPE"

#: Dependency statuses no controller action can ever move forward again:
#: select_ready_units requires every dependency to be DONE, FAILED means
#: the circuit breaker is exhausted (design step 17), and SKIPPED means a
#: re-plan dropped the unit (Store.upsert_units). A dependent of either is
#: unreachable, not merely waiting (R-L03-refutation-2 F4).
_DEAD_DEPENDENCY_STATUSES = ("FAILED", "SKIPPED")

#: Default deadline before a DISPATCHED unit with no result is considered
#: abandoned (design section 5 fault 4, "worker hangs"). Thirty minutes,
#: not derived from any measurement in this repository, matching the
#: honesty AUTONOMY_SOFT_STOP_PCT/AUTONOMY_HARD_STOP_PCT's own comment
#: gives for THEIR own thresholds.
DEFAULT_DISPATCH_TIMEOUT_SECONDS = 1800


def _loop_stops(summary):
    """The ONE predicate both loop drivers use (law L3): a loop continues
    only while a wave reported no reason to stop. Round 3 had
    run_to_completion and _drive_until_parked stopping on DIFFERENT,
    hand-written conditions, which is how the park stop round 3 added to
    run_to_completion turned out to be unreachable from the shipped CLI
    (REFUTATION-3 LV 6a's own corollary: _drive_until_parked already
    stopped earlier on a broader condition).

    The second clause is belt and braces, never the primary test:
    _RUN_TO_COMPLETION_STOP_STATES is KEPT exactly as it was, so a stop
    reason this change forgot to set somewhere still cannot produce a spin
    on a delivered, waiting or terminal run."""
    return (summary.get("stop_reason") is not None
            or summary["state"] in
            ControllerEngine._RUN_TO_COMPLETION_STOP_STATES)


class ControllerEngine(object):
    """One controller run, one project, driven through an injected
    WorkerAdapter and CheckRunner so a crash resumes from the last
    durable state (design section 0). Every public method opens no
    transaction of its own; each is a sequence of Store calls, and the
    STORE is what makes each individual call durable-before-effect. A
    crash between two Store calls in the same engine method is exactly
    what the ten fault tests exercise: discard the engine object and
    construct a fresh one against the SAME store, then call step() again;
    resume must reconstruct position from the store alone, never from
    anything held in `self`."""

    #: DESIGN DELTA (report it, do not silently diverge): the design's own
    #: prose (section 8 and section 3 step 6) writes fence names as
    #: "controller:<project_id>" and "unit:<unit_id>", colon-separated.
    #: tools/bm_store.py's valid_name (line ~394) refuses ':' as one of
    #: eight reserved characters ('/\\:?*"<>|'), a fact only checkable
    #: against the landed code, not the design prose. The code wins: these
    #: two prefixes use '-', matching the store's own naming convention
    #: elsewhere (task-x, exec-update in tools/test_bm_store.py's own
    #: fixtures). Functionally identical: a unique, stable, human-
    #: readable fence name per project or per unit.
    CONTROLLER_FENCE_PREFIX = "controller-"
    UNIT_FENCE_PREFIX = "unit-"

    def __init__(self, store, worker, checker, controller_id, actor,
                 session_id=None, dispatch_timeout_seconds=None,
                 now_fn=None, plan_gate=None):
        self.store = store
        self.worker = worker
        self.checker = checker
        self.controller_id = controller_id
        self.actor = actor
        #: R7's third harness seam (see PlanGate's own docstring above).
        #: None by default, matching every engine tools/test_bm_controller.py
        #: constructed before this row of the readiness board: with no
        #: gate injected, _authorise_dispatch below checks nothing new and
        #: every pre-existing test is byte for byte unaffected. Pass a
        #: PlanGate (StorePlanGate in production) to make R7 live for this
        #: engine.
        self.plan_gate = plan_gate
        self.session_id = session_id or ("controller-" + uuid.uuid4().hex)
        self.dispatch_timeout_seconds = (
            DEFAULT_DISPATCH_TIMEOUT_SECONDS if dispatch_timeout_seconds
            is None else dispatch_timeout_seconds)
        self._now_fn = now_fn or _default_now
        #: The execution ledger for the call in progress. See
        #: _nothing_ran_clause and _open_call: it is the ONLY thing this
        #: engine keeps on `self`, it is reset at every public entry, it
        #: feeds founder-facing PROSE and nothing else, and no control flow
        #: anywhere reads it. That is what keeps it inside the class
        #: docstring's rule rather than an exception to it: a resumed
        #: engine reconstructs position from the store exactly as before,
        #: and the worst a stale ledger could ever do is name a command in
        #: a sentence.
        self._executed = []

    def _now(self):
        return self._now_fn()

    def _open_call(self):
        """Start this public call's execution ledger. Called at the top of
        every entry point that can reach a command (step, receive_result,
        check_timeouts), so the sentence "what this call ran" is answered
        from this call and never from the last one."""
        self._executed = []

    # -- the run guard and the one exit funnel -----------------------------

    def _run_or_refuse(self, project_id):
        """The run row, or the refusal step() used to raise inline. Lifted
        so every entry point shares ONE copy: a project with no run is the
        same answer whichever door it is asked through."""
        run = self.store.get_run(project_id, raw=True)
        if run is None:
            raise bs.OwnershipRefused(
                "no-run",
                "project %r has no controller run; call begin() first."
                % (project_id,))
        return run

    def _is_paused(self, run):
        return run["state"] == "PAUSED"

    def _refuse_if_paused(self, run, action):
        """PAUSED belongs to the founder (REFUTATION-3 law L1). No engine
        path dispatches, judges, verifies, delivers, abandons or un-pauses
        a PAUSED run; only `bm-controller resume` leaves that state. This
        is the loud half of the law, for entry points where returning a
        summary would be a lie about what happened (`plan` in particular:
        upsert_units ALWAYS flips a run to READY, and PAUSED to READY is
        legal, so `bm-controller plan` un-paused a paused run as a side
        effect of writing a unit graph)."""
        if self._is_paused(run):
            raise bs.OwnershipRefused(
                "run-paused",
                "controller run %r is PAUSED, so %s is refused: only "
                "`bm-controller resume` leaves that state, and it is a "
                "founder action. Resume the run first, then %s again."
                % (run["run_id"], action, action))

    def _refuse_foreign_driver(self, run, action):
        """A run has ONE driver: the session recorded on the run row at
        begin(), or by the last explicit adoption (L09 GAP 2, founder
        decision 2026-08-05). begin() already enforces this for STARTING
        a run, through the controller fence; this is the same law for
        CONTINUING one, at the three doors that used to skip it (step,
        receive_result, stop), so a second process can no longer drive a
        run it never started just by not calling begin().

        The guard mirrors the fence store's own ownership law
        (Store.transition, BLOCKER 2): a NON-EMPTY recorded session
        blocks a foreign move, an empty one (a run row written before
        this law) has no live driver to protect, and the callers only
        ask this question about a run that is still live (a terminal
        run's summary is a read anyone may take). The one deliberate
        takeover path is adopt(), which records the handover, exactly as
        STATE.md fence adoption does; it is named in the refusal so the
        founder is told the legal next step rather than left guessing."""
        driver = run["session_id"] or ""
        if driver and driver != self.session_id:
            raise bs.OwnershipRefused(
                "not-driver",
                "controller run %r is driven by session %r, and this "
                "engine is session %r, so %s is refused: a run has one "
                "driver, the same law the controller fence enforces at "
                "begin(). If that session is gone, or this takeover is "
                "deliberate, run `bm-controller adopt` for this project "
                "(engine: adopt()), which records the handover and makes "
                "this session the driver; then %s again."
                % (run["run_id"], driver, self.session_id, action, action),
                details={"run_id": run["run_id"],
                         "driver_session_id": driver,
                         "caller_session_id": self.session_id})

    # -- the contract gate in front of every command this engine runs ------

    def _contract_state(self, project_id):
        """The contract's state RIGHT NOW, read fresh, or None when the
        project has no contract at all. Never cached and never passed
        around: the whole point of REFUTATION-4 AZ F5 is that the state at
        the MOMENT of execution is what authorises the execution."""
        contract = self.store.latest_contract(project_id, raw=True)
        return contract["state"] if contract is not None else None

    def _contract_is_live(self, project_id):
        return self._contract_state(project_id) == "live"

    def _breaker_is_this_results_own_cost(self, totals, charged):
        """True when the ONLY reason the breaker reads hard-stop is the
        spend row THIS result-handling call just wrote for the unit being
        judged, that is, when subtracting it puts both meters back under
        their ceilings.

        This is the one carve-out in _authorisation_refusal, and it is a
        computation rather than a switch, which matters: `charged` is never
        a caller's opinion, it is what Store.record_spend DURABLY WROTE a
        few statements earlier for this unit's own already-authorised work
        (see _record_spend's return value). Nobody can pass a number to
        loosen the gate without the meter carrying that same number.

        Why it is right rather than convenient: the founder's ceiling is a
        limit on what may be SPENT, and this money is already spent. The
        unit was dispatched under a verdict that was ALLOWED, it did the
        work, and its answer is in hand. Refusing to read that answer
        destroys it and burns a retry without saving a single token, and
        the spend row is what stops the run anyway: step()'s own step-3
        breaker gate drains the whole run on the very next call, and
        _deliver_or_hold will not declare a deliverable. So exactly ONE
        already-paid unit is judged past the ceiling, its cost is on the
        meter where a founder can see it, and no NEW work starts.

        The refuted sequence is unaffected, which is the point: in
        REFUTATION-5 S2 the ceiling was blown by a separate
        `bm-autonomy spend` BEFORE `bm-controller record-result` was run,
        and the record-result supplied no cost at all, so there is nothing
        to subtract and the refusal stands.

        Round-6 disclosure, stated rather than hidden: a caller that
        self-reports an enormous cost therefore buys the judgement of the
        one unit already in flight. It buys nothing else, and it pays the
        meter in full to do it."""
        if not charged:
            return False
        token_ceiling = totals.get("token_ceiling")
        minutes_ceiling = totals.get("minutes_ceiling")
        tokens = totals["tokens"] - int(charged.get("tokens") or 0)
        minutes = totals["minutes"] - int(charged.get("minutes") or 0)
        if token_ceiling is not None and tokens >= token_ceiling:
            return False
        if minutes_ceiling is not None and minutes >= minutes_ceiling:
            return False
        return True

    def _authorisation_refusal(self, project_id, unit=None, charged=None):
        """None when EVERY question the founder's authorisation asks is
        answered ALLOWED right now, or the refusing verdict dict.

        REFUTATION-5 S2 (HIGH). Round 5 asked ONE of gate_check's eight
        questions, "is the contract row live", and called that the gate. So
        after the founder's SPEND CEILING tripped, and `bm-autonomy
        gate-check` answered REFUSED-BREAKER in the founder's own terminal,
        one `bm-controller record-result` still ran the unit's
        model-authored done_check AND the founder's whole done_definition,
        accepted the unit and declared the run DELIVERABLE_READY. The
        second of the founder's two brakes stopped no command at all, and
        the most expensive command in the system was the one it left
        ungated.

        The three questions asked here are exactly the three gate_check
        would refuse for at this moment:

          STATE    no contract, or one that is paused, stopped or revoked.
                   Same answer as round 5, reached through the same read.
          BREAKER  spend at or over 100 percent of either ceiling. This is
                   gate_check's own check 7, and _spend_totals_from is the
                   same computation, so the engine and the founder's
                   `gate-check` cannot disagree.
          SCOPE    when a UNIT is in hand, its whole write_scope under one
                   contract revision, through the SAME
                   _gate_check_write_scope the dispatch route uses. A
                   command run for a unit whose paths the live contract no
                   longer admits is a command run outside the contract,
                   whichever direction it writes in.

        CLASS and FLOOR are deliberately NOT re-asked without a unit: they
        are per-unit questions, already asked and answered at dispatch, and
        the founder's own done_definition is not a model action bounded by
        the risk classes the founder granted the model. Asking them of the
        done_definition would refuse delivery on contracts that are
        perfectly valid.

        A READ, never a write: this must be safe to call immediately before
        every command without changing anything it is judging."""
        contract = self.store.latest_contract(project_id, raw=True)
        if contract is None:
            return {"verdict": "REFUSED-NO-CONTRACT", "floor": None,
                    "revision": None, "contract_id": None,
                    "reason": "project %r has no contract at all; nothing "
                              "is authorised." % (project_id,)}
        if contract["state"] != "live":
            return {"verdict": "REFUSED-STATE", "floor": None,
                    "revision": contract["revision"],
                    "contract_id": contract["contract_id"],
                    "reason": "project %r contract is %s (revision %s); "
                              "nothing is authorised while it is not live."
                              % (project_id, contract["state"],
                                 contract["revision"])}
        totals = self.store.spend_totals(project_id)
        breaker_exempt = self._breaker_is_this_results_own_cost(totals,
                                                                charged)
        if totals["verdict"] == "hard-stop" and not breaker_exempt:
            return {"verdict": "REFUSED-BREAKER", "floor": None,
                    "revision": contract["revision"],
                    "contract_id": contract["contract_id"],
                    "reason": "spend is at or over 100 percent of a ceiling "
                              "(%s token(s) of %s, %s minute(s) of %s); the "
                              "breaker has tripped."
                              % (totals["tokens"], totals["token_ceiling"],
                                 totals["minutes"],
                                 totals["minutes_ceiling"])}
        if unit is not None:
            verdict, _refused_path = self._gate_check_write_scope(
                project_id, unit)
            # gate_check asks the breaker LAST (its own check 7, after the
            # path check at 5), so a REFUSED-BREAKER verdict here means
            # every path already passed and the breaker is the only
            # objection left. That objection was answered three statements
            # above, exemption included, so honouring it again here would
            # re-impose the carve-out's opposite through the back door.
            if (verdict["verdict"] != "ALLOWED"
                    and not (verdict["verdict"] == "REFUSED-BREAKER"
                             and breaker_exempt)):
                return verdict
        return None

    def _run_command(self, project_id, command, unit=None, charged=None,
                     label=None):
        """THE ONE place this engine hands a founder-authored or
        model-authored command to the CheckRunner, and it refuses to run
        one at all unless the WHOLE authorisation allows it at this
        instant (_authorisation_refusal above), not merely the contract
        row's liveness.

        REFUTATION-4 AZ F5 (HIGH, SAFETY). After the 3am kill switch
        (`bm-autonomy stop`, or `revoke`), one `bm-controller record-result`
        still executed the unit's own done_check AND a `git restore`
        rollback in the project root, and one plain `bm-controller step`
        did the same through the RESULT_IN resume branch, which by design
        runs BEFORE the live-contract read. In full auto the unit graph,
        including done_check and verifier, is MODEL-authored, so those two
        commands are the most consequential thing the kill switch has to
        stop, and `git restore` mutates the founder's working tree. Round
        4's section 10.6 is what added the rollback to that branch.

        `unit` is the unit row this command belongs to, when there is one,
        so the SCOPE half of the verdict is asked as well. Only the
        founder's own done_definition passes None, and only because it
        belongs to no unit.

        Returns the CheckOutcome, or None meaning NOTHING RAN. Every
        caller must treat None as "no command was executed" and must not
        invent an exit code for it: an unrun check has no verdict. That
        rule is UNCHANGED by the wider gate; what changed is how many
        reasons there now are for None.

        The gate is deliberately here, at the call site, rather than only
        at the branch points that route into it, so a command site added
        later is gated by construction. The structural half of the same
        property is tools/test_bm_controller.py's
        test_every_checker_call_sits_behind_one_gated_call_site and, since
        round 6, _execution_primitive_offences beside it, which asserts
        that no subprocess primitive is called anywhere in this module
        outside SubprocessCheckRunner.run and that `self.checker` is bound
        once and called once (REFUTATION-5 S5: the round-5 guard passed
        with `subprocess.run`, `os.system`, `getattr(self.checker, "run")`
        and an aliased `runner = self.checker` added inline).

        `label` is what this command is CALLED in the sentence a founder
        reads, and it is required of every call site by
        test_every_command_site_names_itself_to_the_ledger
        (REFUTATION-6 F2). It is appended to the ledger only AFTER the
        runner returns, so the ledger holds what really executed rather
        than what was about to be attempted; a runner that raises records
        nothing, which is the conservative direction (a sentence that
        under-claims what ran is corrected by the founder's own eyes on
        the tree, one that over-claims is not)."""
        if self._authorisation_refusal(project_id, unit,
                                       charged) is not None:
            return None
        outcome = self.checker.run(command, cwd=self.store.root)
        self._executed.append(label or command)
        return outcome

    def _authorisation_window(self, claimed, project_id, run_id, unit,
                              exit_code, summary=None, charged=None):
        """THE re-ask, and the ONE place its answer is turned into a
        rejection. None when the whole authorisation still covers this
        unit's work at this instant; otherwise the outcome word of a
        rejection that has already been written down durably.

        REFUTATION-6 F1 (HIGH, the founder's kill switch). Round 6 asked
        this question in exactly one place, after the done_check, and
        called it "after every command". It is not: in full auto the unit's
        VERIFIER is model-authored shell of unbounded duration, and a stop
        or a revoke landing while it ran was never re-asked, so the unit
        was accepted under a killed contract, its fence released as sound
        work, its dispatch recorded 'pass', and ZERO founder steps named
        any of it. The founder's own done-definition had the same window in
        front of DELIVERABLE_READY.

        ONE helper rather than one copy per window, deliberately: two
        copies of a question this consequential drift apart, and round 6's
        own defect was exactly a second site that never got the first
        site's fix. The complete set of windows is in this round's report
        and is pinned structurally by _R7_COMMAND_SITES in
        tools/test_bm_controller.py.

        The rejection goes through _reject_as_stale, the SAME sequence the
        done_check window and the ordinary staleness branch use, never a
        lookalike: rejected verification carrying the real exit code, the
        unit back through the circuit breaker, the rollback (itself gated,
        so a dead contract composes it and runs nothing), the fence parked,
        and a founder step."""
        refusal = self._authorisation_refusal(project_id, unit, charged)
        if refusal is None:
            return None
        unit_id = claimed["unit_id"]
        state_word = self._contract_state(project_id) or "missing"
        if refusal["verdict"] == "REFUSED-BREAKER":
            reason = ("the founder's spend ceiling tripped while unit %s was "
                      "being checked (%s): what had already started ran "
                      "under a live authorisation, and nothing further is "
                      "authorised, so the result is rejected rather than "
                      "accepted" % (unit_id, refusal["reason"]))
            stop_reason = "SPEND_STOP"
            note = _spend_breaker_note(refusal["reason"], self._executed)
        elif state_word != "live":
            # Kept WORD FOR WORD from round 6's done_check window, which
            # this method absorbed: the sentence a founder may already have
            # seen for that window is the sentence they see for the others.
            reason = ("the contract became %s while unit %s was being "
                      "checked: the check had already started under a live "
                      "authorisation so it RAN, and the authorisation it ran "
                      "under no longer exists, so the result is rejected "
                      "rather than accepted" % (state_word, unit_id))
            stop_reason = "CONTRACT_NOT_LIVE"
            note = _NOTE_CONTRACT_DIED_MIDCOMMAND % (state_word,)
        else:
            reason = ("the live contract stopped authorising unit %s's write "
                      "scope while it was being checked (%s), so the result "
                      "is rejected rather than accepted"
                      % (unit_id, refusal["reason"]))
            stop_reason = "FOUNDER_WAITING"
            note = ("nothing was accepted: the live contract stopped "
                    "authorising unit %s's write scope while it was being "
                    "checked (%s). Widen the contract or re-plan the unit"
                    % (unit_id, refusal["reason"]))
        return self._reject_as_stale(
            claimed, project_id, run_id, unit, exit_code, reason, summary,
            stop_reason=stop_reason, note=note, charged=charged)

    def _refusal_stop(self, project_id, refusal, not_live_note):
        """(stop_reason, note) for a refusal on a path that will now run
        NOTHING further and accept nothing. `not_live_note` is the one
        sentence only the CALLER knows, taking the contract's state word;
        the other two branches are identical everywhere and are therefore
        written once (REFUTATION-6 F2: three hand-copied if/elif chains
        were how the breaker's false sentence reached three call sites)."""
        if refusal["verdict"] == "REFUSED-BREAKER":
            return "SPEND_STOP", _spend_breaker_note(refusal["reason"],
                                                     self._executed)
        contract_state = self._contract_state(project_id)
        if contract_state == "paused":
            return "CONTRACT_PAUSED", _NOTE_CONTRACT_PAUSED
        return ("CONTRACT_NOT_LIVE",
                not_live_note % (contract_state or "missing",))

    def _authorisation_moved(self, project_id, stamped_revision):
        """(moved, latest_revision) for the staleness protocol: did the
        contract's AUTHORISATION change since `stamped_revision`, as
        opposed to its revision INTEGER moving.

        Store.set_contract_state appends a new revision for every pause,
        resume, stop and revoke, "copying every authorisation column
        forward verbatim" (its own docstring). Comparing integers therefore
        called a held answer stale because the founder had pressed pause
        and then un-pressed it: two revisions, zero change to allowed_paths,
        risk_classes or either ceiling, and the engine ran `git restore`
        over the founder's files and burned a retry for it (REFUTATION-4 AZ
        F4, whose own supersession argument in design 18.1 is that the hold
        exists so "the real answer survives the pause and is accepted
        afterwards").

        Liveness is NOT folded in here. Whether a stopped or revoked
        contract may be acted on at all is asked earlier, by _run_command
        and by the branch that closes a dispatch under a dead contract, and
        answered there with a refusal and zero executed commands. This
        method answers only "is the authorisation this dispatch was granted
        still the authorisation in force".

        CONSERVATIVE ON DOUBT: an unreadable chain, or one longer than the
        window read back, reports moved=True, which is the round-3
        behaviour and rejects."""
        latest = self.store.latest_contract(project_id, raw=True)
        latest_revision = latest["revision"] if latest is not None else None
        if latest_revision == stamped_revision:
            return False, latest_revision
        if latest_revision is None or stamped_revision is None:
            return True, latest_revision
        rows = self.store.contract_revisions(project_id, limit=2000,
                                             raw=True)
        seen_latest = False
        for row in rows:
            if row["revision"] == latest_revision:
                seen_latest = True
            if row["revision"] <= stamped_revision:
                continue
            if row["change_kind"] not in _LIFECYCLE_CHANGE_KINDS:
                return True, latest_revision
        if not seen_latest:
            return True, latest_revision
        return False, latest_revision

    def _finish(self, summary, project_id, stop_reason=None, note=None):
        """The ONE exit from step(). Re-reads the run row and writes the
        state it ACTUALLY found into the summary, so summary['state'] is a
        store read at return time and never a prediction (law L2).

        REFUTATION-3 SM E and LV 3: _handle_no_ready_units assigned
        summary['state'] = 'WAITING_HUMAN' unconditionally while the store
        move beside it was guarded on the run being READY or CHECKPOINTED,
        and step() handed that summary straight back, so
        `bm-controller step --json` printed one state and
        `bm-controller status` printed another a second later. The lie was
        also load bearing: run_to_completion stopped on the state the
        SUMMARY claimed, so the loop terminated because of a state the run
        was not in.

        A stop_reason outside STOP_REASONS raises here, deliberately: this
        is engine-internal vocabulary, so a typo must fail loudly in tests
        rather than silently disable a loop stop, exactly the reason the
        import-time walk-edge guard above exists."""
        if stop_reason is not None:
            if stop_reason not in STOP_REASONS:
                raise ValueError(
                    "%r is not a controller stop reason (known: %s)"
                    % (stop_reason, ", ".join(STOP_REASONS)))
            summary["stop_reason"] = stop_reason
        summary.setdefault("stop_reason", None)
        if note is not None:
            summary["note"] = note
        summary["state"] = self.store.get_run(project_id, raw=True)["state"]
        return summary

    def _set_reason(self, summary, stop_reason, note):
        """The one place a reason is written by anything other than
        _finish, so the field is never assigned by hand and never assigned
        a word outside the enum. _finish LEAVES an already-set reason
        alone, which is how _handle_no_ready_units and _deliver_or_hold get
        their answer out through a funnel that takes no argument from
        them."""
        if stop_reason not in STOP_REASONS:
            raise ValueError(
                "%r is not a controller stop reason (known: %s)"
                % (stop_reason, ", ".join(STOP_REASONS)))
        summary["stop_reason"] = stop_reason
        summary["note"] = note

    # -- begin / plan -----------------------------------------------------

    def begin(self, project_id, outcome, done_definition,
              workflow_version=1):
        """Design's `begin` transition (section 1) plus the fence claim
        section 8 states: claim the controller:<project_id> fence, THEN
        open_run in state NEW, fence claim committed before any
        orientation read. Raises bs.OwnershipRefused('name-active', ...)
        if a live controller already holds the fence (duplicate-
        controller refusal, reused verbatim from the fence store, no new
        lock), and bs.OwnershipRefused('run-exists', ...) if a
        non-terminal run already exists for the project even with no live
        fence (the store's own second, independent guard; see
        Store.open_run's docstring)."""
        fence_name = self.CONTROLLER_FENCE_PREFIX + project_id
        record = self.store.claim(
            name=fence_name, lifetime="persistent",
            objective="Full-Auto controller run for %s" % project_id,
            owner=self.controller_id, session_id=self.session_id)
        run = self.store.open_run(
            project_id, self.controller_id, workflow_version, outcome,
            done_definition, record.lifecycle_uuid, self.session_id,
            self.actor)
        return run

    def plan(self, project_id, run_id, units):
        """Design's `oriented` and `planned` transitions collapsed into
        one call: ORIENTING -> PLANNING (idempotent: a resumed engine
        that is already PLANNING does not re-walk NEW -> ORIENTING) ->
        upsert_units, which itself flips PLANNING -> READY in the SAME
        transaction (Store.upsert_units's own docstring). `units` is the
        unit graph the orchestrating model already produced; building its
        CONTENT is the one judgement the design leaves to the builder,
        not this engine (design section 11's closing line).

        Returns upsert_units' own four-key result unchanged, and performs
        the two things that result now EXPECTS a caller to perform
        (REFUTATION-3 LV 4): parking the fences a dropped unit was still
        holding, and reconciling the lane blocks so a re-plan's effect on
        BLOCKED units is visible immediately rather than on the next step.
        The fences are released HERE and not inside upsert_units because
        releasing one means Store.transition, which opens its own
        transaction, and SQLite refuses a nested BEGIN; the crash window
        between the two calls is closed by _resume_result_in_and_orphans'
        own SKIPPED branch."""
        run = self._run_or_refuse(project_id)
        # The driver's own gate (L09 refute round 2, finding B2). `plan`
        # is the single most powerful mutation (it defines what work runs
        # and rewrites the unit graph, cascading DONE units), and round 1
        # scoped the driver guard to step/receive_result/stop, leaving
        # this door open: a foreign session injected the unit graph the
        # honest driver's next step then dispatched. Guarded in the same
        # shape step uses, and BEFORE _refuse_if_paused, so a foreign
        # session learns "not yours" before it learns anything about the
        # run's pause state. A legitimate crash-resume replan is the SAME
        # driver (or a session that ran adopt first), which passes.
        self._refuse_foreign_driver(run, "plan")
        self._refuse_if_paused(run, "plan")
        units = self._validated_units(units)
        # EVERY store write below names the project the caller said it was
        # working on, so the store refuses 'run-not-in-project' before it
        # touches anything (REFUTATION-4 AZ F2). `project_id` and `run_id`
        # arrive as INDEPENDENT arguments, and `bm-controller plan --run
        # RUN_ID` lets a founder supply the second one; round 4 applied its
        # PAUSED guard to the project's CURRENT run and then wrote
        # whichever run the argument named, so one command aimed at p1
        # un-paused a PAUSED run of p2 (law L1), cancelled its open
        # dispatch, parked its fence and replaced its unit graph, leaving
        # p2 READY under a PAUSED contract. Reproduced through the shipped
        # CLI. The round-5 STORE half added the ownership check to eleven
        # write entry points and its report names wiring the shipped
        # commands to pass the project as the CONTROLLER's change; the
        # keyword is threaded through EVERY store write this file makes,
        # not only this command's, so no route into the store is left on
        # the opt-out side of the guard.
        if run["state"] == "NEW":
            self.store.set_run_state(run_id, "ORIENTING", self.actor,
                                     "begin", self.session_id,
                                     project_id=project_id)
        run = self.store.get_run(project_id, raw=True)
        if run["state"] == "ORIENTING":
            self.store.set_run_state(run_id, "PLANNING", self.actor,
                                     "planned", self.session_id,
                                     project_id=project_id)
        result = self.store.upsert_units(run_id, units, self.actor,
                                         project_id=project_id)
        for unit_id, fence_uuid in result["orphaned_fences"]:
            self._release_fence(
                fence_uuid, "parked",
                "a re-plan dropped unit %s from the unit graph" % unit_id)
        self._reconcile_lane_blocks(project_id, run_id)
        return result

    def _validated_units(self, units):
        """The unit graph, with the two engine-side preconditions
        upsert_units does not know about applied BEFORE anything is
        written. Returns a new list of shallow-copied unit dicts; the
        caller's own dicts are never mutated.

        ONE, the unit_id must be a legal FENCE name once this engine's own
        UNIT_FENCE_PREFIX is on the front (REFUTATION-4 AZ F6). upsert_units
        validates a unit_id only as "a non-empty string"; Store.claim runs
        valid_name, which raises ValueError for whitespace, for the eight
        reserved characters, for a leading dot and for anything over 60
        characters. That raise happened AFTER step() had walked the run to
        EXECUTING, and _authorise_dispatch catches only OwnershipRefused,
        so every later `step` exited 1 and `plan`, the documented recovery,
        is refused from EXECUTING: a permanent wedge from `unit_id:
        "api/pay"`, which is exactly the naming a planner produces for a
        unit that owns a directory. Refusing HERE, before the ORIENTING
        walk and before upsert_units, is the version of the fix that leaves
        no wedge to recover from: nothing is written and the run has not
        moved.

        TWO, read_scope is canonicalised and refused on escape exactly as
        write_scope is (REFUTATION-4 AZ F9). upsert_units canonicalises
        every write_scope entry and stores read_scope with a bare
        json.dumps, and _build_brief hands it to the worker unchanged, so a
        brief could hand a worker '/etc', '../../../Users' and
        '~/.ssh/id_rsa' as its read scope while the identical entry in
        write_scope was refused 'path-escape'. In full auto the read scope
        is model-authored. The same two store primitives the store's own
        write_scope path uses are used here, so the two scopes cannot
        drift apart in what they accept.

        THREE, both scopes must be a LIST of paths, and every write_scope
        entry must be a plain relative path (REFUTATION-5 S1, S4 and S6).
        A scope emitted as a bare JSON string was iterated CHARACTER BY
        CHARACTER, so `"a.py"` became `['a', '.', 'p', 'y']`, '.' is the
        project ROOT, and the brief handed the worker the whole project
        while the unit's fence held it; a scalar was not iterable at all
        and left an uncaught TypeError out of the shipped `plan`; and an
        entry that is a git PATHSPEC (`:/`, `:!x`, `:(exclude)x`) survived
        every check round 5 had and turned the engine's own rollback into
        a `git restore` over the founder's whole working tree.

        REFUSING HERE, BEFORE THE ORIENTING WALK, IS THE POINT. The store
        refuses all three too (that is the declaration side, and it is the
        store writer's), but it refuses INSIDE upsert_units, which this
        method's own AZ F6 paragraph above explains is one state walk too
        late: the run is left in PLANNING with nothing planned. Refused
        here, nothing is written and the run has not moved, so `plan`
        again is a real recovery rather than a wedge.

        read_scope is still deliberately NOT gate-checked against
        allowed_paths; see _gate_check_write_scope's docstring for why that
        remains right. Canonicalising it and keeping it inside the project
        root is a different question, and it was not being asked."""
        out = []
        for unit in units or []:
            if not isinstance(unit, dict):
                out.append(unit)
                continue
            unit_id = unit.get("unit_id")
            if isinstance(unit_id, str) and unit_id:
                try:
                    bs.valid_name(self.UNIT_FENCE_PREFIX + unit_id)
                except ValueError as exc:
                    raise bs.OwnershipRefused(
                        "bad-unit-id",
                        "unit id %r cannot be used: this engine fences each "
                        "unit under the name %r, and the fence store "
                        "refuses that name (%s). Nothing was written and "
                        "the run has not moved; rename the unit and plan "
                        "again."
                        % (unit_id, self.UNIT_FENCE_PREFIX + unit_id, exc),
                        details={"unit_id": unit_id,
                                 "fence_name": self.UNIT_FENCE_PREFIX
                                 + str(unit_id)})
            lane = unit.get("lane")
            if isinstance(lane, str) and lane.strip() == SPEND_RECONCILE_LANE:
                raise bs.OwnershipRefused(
                    "reserved-lane",
                    "unit %r cannot be planned into lane %r: that lane is "
                    "RESERVED for this engine's own uncharged-spend "
                    "disclosure, whose founder step blocks delivery until "
                    "the meter is reconciled, so a unit there would be "
                    "gated by a bookkeeping step that has nothing to do "
                    "with it. Nothing was written and the run has not "
                    "moved; rename the lane and plan again."
                    % (unit_id, SPEND_RECONCILE_LANE),
                    details={"unit_id": unit_id, "lane": lane})
            self._refuse_bad_scopes(unit, unit_id)
            read_scope = unit.get("read_scope")
            if read_scope:
                canonical = [
                    bs.canonicalize_path(
                        self.store.root, bs._coerce_path_entry(entry))
                    for entry in read_scope]
                unit = dict(unit)
                unit["read_scope"] = canonical
            out.append(unit)
        return out

    def _refuse_bad_scopes(self, unit, unit_id):
        """Both of `unit`'s scopes, or an OwnershipRefused naming the field,
        the value and what is wrong with it. Returns None; it exists to
        raise (REFUTATION-5 S1, S4, S6).

        The write scope is held to the stricter rule of the two, because
        it is the one three more readers act on: the fence claim, the
        brief handed to a worker, and the engine's `git restore --`
        rollback. The read scope is held to the container rule only, since
        _validated_units canonicalises its entries two lines below and a
        read boundary is deliberately not gate-checked."""
        for field in ("write_scope", "read_scope"):
            problem = _unsafe_scope_container(field, unit.get(field))
            if problem is not None:
                raise bs.OwnershipRefused(
                    "bad-scope",
                    "unit %r cannot be planned: %s Nothing was written and "
                    "the run has not moved; fix the unit graph and plan "
                    "again." % (unit_id, problem),
                    details={"unit_id": unit_id, "field": field})
        for entry in unit.get("write_scope") or []:
            problem = _unsafe_scope_entry(entry)
            if problem is not None:
                raise bs.OwnershipRefused(
                    "bad-scope",
                    "unit %r cannot be planned: its write_scope entry %r %s. "
                    "Nothing was written and the run has not moved; name "
                    "the files, or name the directory they live in, which "
                    "grants its whole subtree." % (unit_id, entry, problem),
                    details={"unit_id": unit_id, "field": "write_scope",
                             "entry": bs._safe_repr(entry)})
            bs.canonicalize_path(self.store.root, entry)

    # -- the resumable step -------------------------------------------------

    def step(self, project_id):
        """One pass of the resumable loop (design section 3). Returns a
        summary dict: {'run_id', 'state', 'dispatched': [unit_id, ...],
        'completed': [unit_id, ...], 'note': str, 'stop_reason': str or
        None}. Never holds state in `self` across the call: every fact it
        needs is re-read from the store at the top (step 1), so a fresh
        ControllerEngine against the same store resumes identically to a
        continuing one.

        EVERY return goes through _finish, with no exceptions, so
        summary['state'] is always a store read at return time (law L2),
        and every one of them names its stop reason from STOP_REASONS or
        leaves it None to mean "the wave made progress, call step()
        again" (law L3)."""
        self._open_call()
        run = self._run_or_refuse(project_id)
        run_id = run["run_id"]
        summary = {"run_id": run_id, "state": run["state"],
                  "dispatched": [], "completed": [], "note": "",
                  "stop_reason": None}
        if run["state"] in _TERMINAL_STATES:
            return self._finish(summary, project_id, "TERMINAL",
                                "run is terminal; nothing to do")

        # -- the driver's own gate (L09 GAP 2): AFTER the terminal read
        # above (a finished run has no driver to protect, and the summary
        # is a read), BEFORE every branch below (the pause read included:
        # a foreign process is told "not yours" before it is told
        # anything about the run it does not drive).
        self._refuse_foreign_driver(run, "step")

        # -- the founder's own gate, checked ONCE, before any read of the
        # contract, any unit list and any resume branch (law L1). It must
        # precede _resume_result_in_and_orphans, which verifies a RESULT_IN
        # dispatch and reverses FAILED_RECOVERABLE: both are the engine
        # judging a run the founder paused. No store write of any kind
        # happens on this path.
        if self._is_paused(run):
            # WHICH pause, and therefore WHICH command clears it. The only
            # route the shipped CLI has to a PAUSED run is the contract
            # being paused (there is no `bm-controller pause`), and for
            # that route `bm-controller resume` alone loops forever
            # (REFUTATION-4 LV L4-F2). The enum carries the difference so
            # a loop driver reads it, and the note carries the two
            # commands so a founder does.
            if self._contract_state(project_id) == "paused":
                return self._finish(summary, project_id, "CONTRACT_PAUSED",
                                    _NOTE_CONTRACT_PAUSED)
            return self._finish(summary, project_id, "FOUNDER_WAITING",
                                _NOTE_RUN_PAUSED)

        # -- step 1: resume UNCONDITIONAL in-flight work before anything
        # else (design's own literal step-1 branches: RESULT_IN and the
        # orphan fence). A DISPATCHED re-await is this implementation's
        # own addition, deliberately gated behind steps 2/3 below; see
        # _resume_dispatched's own docstring for why.
        resumed = self._resume_result_in_and_orphans(project_id, run_id)
        if resumed is not None:
            summary["completed"] = resumed["completed"]
            # The resume branch settles the wave, and the settle path is
            # where delivery, the founder-waiting park and the whole
            # founder_gated block are decided. Round 4 forwarded only the
            # note, so a delivery reached through a crash resume reported
            # "the wave made progress, call step() again" (law L3's own
            # meaning for a None reason) about a run that was finished
            # (REFUTATION-4 LV L4-F1).
            if resumed.get("founder_gated") is not None:
                summary["founder_gated"] = resumed["founder_gated"]
            return self._finish(summary, project_id,
                                resumed.get("stop_reason"), resumed["note"])

        # -- step 2: read the live contract -------------------------------
        contract = self.store.latest_contract(project_id, raw=True)
        if contract is None or contract["state"] != "live":
            reason = "no contract at all" if contract is None else (
                "contract is %s" % contract["state"])
            if contract is not None and contract["state"] == "paused":
                self.store.set_run_state(run_id, "PAUSED", self.actor,
                                         reason, self.session_id,
                                         project_id=project_id)
                return self._finish(summary, project_id, "CONTRACT_PAUSED",
                                    _NOTE_CONTRACT_PAUSED)
            self._begin_stopping(project_id, run_id, reason)
            return self._finish(summary, project_id, "TERMINAL",
                                "draining: %s" % reason)

        # -- step 3: spend gate --------------------------------------------
        spend = self.store.spend_totals(project_id)
        if spend["verdict"] == "hard-stop":
            self._begin_stopping(project_id, run_id,
                                 "spend breaker tripped (hard-stop)")
            return self._finish(summary, project_id, "TERMINAL",
                                "draining: spend breaker tripped")
        may_start_new = spend["verdict"] != "soft-stop"
        if spend["verdict"] == "no-data":
            summary["note"] = ("no spend ceiling on file (no-data); "
                               "starting units and saying so")

        # Only NOW, with a live contract and no hard-stop confirmed, is
        # it safe to re-await an already-DISPATCHED unit: this is new
        # interaction with the worker, not a passive reconstruction of
        # position.
        resumed = self._resume_dispatched(project_id, run_id)
        if resumed is not None:
            summary["completed"] = resumed["completed"]
            if resumed.get("founder_gated") is not None:
                summary["founder_gated"] = resumed["founder_gated"]
            return self._finish(summary, project_id,
                                resumed.get("stop_reason"), resumed["note"])

        # -- BLOCKED is a VIEW, recomputed before anything reads it (law
        # L4). Deliberately BEFORE select_ready_units, so a lane ungated a
        # moment ago is selectable in the SAME wave rather than the next.
        self._reconcile_lane_blocks(project_id, run_id)

        # -- step 4: select ready units --------------------------------
        ready = self.store.select_ready_units(run_id)
        if not ready:
            self._handle_no_ready_units(project_id, run_id, summary)
            return self._finish(summary, project_id)

        if not may_start_new:
            return self._finish(summary, project_id, "SPEND_STOP",
                                _NOTE_SPEND_STOP % len(ready))

        # -- the dispatch source law. _walk_to_executing is a SILENT no-op
        # from a state it does not know (REFUTATION-3 SM A: from PAUSED it
        # did nothing and every line below ran anyway, claiming a fence,
        # writing a dispatch row and handing a worker a brief while the
        # run's durable state said PAUSED). Asking the question here, once,
        # is the structural closure of that whole class.
        state_now = self.store.get_run(project_id, raw=True)["state"]
        if not self._may_dispatch(state_now):
            return self._finish(
                summary, project_id, "FOUNDER_WAITING",
                "%d unit(s) are selectable but the run is %s, which only a "
                "founder can move (bm-controller resume, complete or stop)"
                % (len(ready), state_now))

        self._walk_to_executing(project_id, run_id)

        # -- step 7: heartbeat, before any dispatch is recorded -----------
        self.store.record_checkpoint(
            project_id, self.controller_id, "heartbeat",
            "state=EXECUTING ready=%s"
            % ",".join(u["unit_id"] for u in ready),
            self.session_id, self.actor)

        # -- steps 5/6/8/9: topology, claim, dispatch intent, hand off ----
        dispatched, outcomes = [], []
        for unit in ready:
            claimed, outcome = self._authorise_dispatch(
                project_id, run_id, unit)
            outcomes.append(outcome)
            if claimed is not None:
                dispatched.append(claimed)
        summary["dispatched"] = [c["unit_id"] for c in dispatched]
        if not dispatched:
            # This wave walked the run to EXECUTING and then claimed
            # nothing, so leaving it there is a false statement about it
            # AND a dead end (REFUTATION-3 LV 1: from EXECUTING neither
            # WAITING_HUMAN nor DELIVERABLE_READY is legal, so a run whose
            # only unit was gate-refused could never be delivered by any
            # later step).
            self._unwind_empty_wave(project_id, run_id)
            if "DRAIN-PLAN-GATE" in outcomes:
                return self._finish(
                    summary, project_id, "PLAN_GATE_REFUSED",
                    "draining: the plan gate refused this run's dispatch "
                    "(R7); see the refused unit's own dispatch history for "
                    "which clause failed")
            if "DRAIN" in outcomes:
                return self._finish(summary, project_id, "TERMINAL",
                                    "draining: gate_check refused the run's "
                                    "own authorisation state")
            if "DEFER" in outcomes:
                return self._finish(summary, project_id, "CONTENTION",
                                    _NOTE_CONTENTION)
            # Every candidate was failed through the circuit breaker: a
            # retry count moved, which IS progress, so the loop may
            # continue and converge on the escalation.
            return self._finish(summary, project_id,
                                note=_NOTHING_CLAIMABLE_NOTE)

        # -- steps 10 to 16 for whichever units the worker answers NOW ---
        any_recorded, park_reason = False, None
        for claimed in dispatched:
            outcome, reason = self._handle_worker_result(
                claimed, self.worker.run(claimed["brief"]), project_id,
                run_id, summary)
            if reason is not None and park_reason != "OUTAGE":
                park_reason = reason
            if outcome is not None:
                any_recorded = True
                if outcome not in ("rejected", "held"):
                    summary["completed"].append(outcome)

        if any_recorded:
            # The wave's OWN summary goes into the settle path, never a
            # throwaway: _settle_after_wave ends in _handle_no_ready_units,
            # which is where delivery, the founder-waiting park, the
            # in-flight park and the escalation all happen, and where
            # summary['founder_gated'] is the only place a delivered run's
            # remaining FAILED units and open founder steps are ever
            # attached to anything (REFUTATION-4 LV L4-F1).
            self._settle_after_wave(project_id, run_id, summary)
            return self._finish(summary, project_id)
        if park_reason == "OUTAGE":
            return self._finish(summary, project_id, "OUTAGE", _NOTE_OUTAGE)
        return self._finish(
            summary, project_id, "IN_FLIGHT",
            _NOTE_IN_FLIGHT % len(self._open_dispatch_units(run_id)))

    #: DELIVERABLE_READY is not in _TERMINAL_STATES (its own legal moves
    #: include READY, so the loop can still resume more work later, e.g.
    #: a founder-gated step gets resolved), but it IS a state with no
    #: further AUTONOMOUS progress possible right now: only a founder
    #: action (`bm-controller complete`) or a fresh unit graph moves it.
    #: run_to_completion() stops here for the same practical reason it
    #: stops at WAITING_HUMAN.
    _RUN_TO_COMPLETION_STOP_STATES = frozenset(
        _TERMINAL_STATES | {"WAITING_HUMAN", "DELIVERABLE_READY", "PAUSED"})

    def run_to_completion(self, project_id, max_steps=500):
        """Convenience loop: `bm-controller start` performs begin() then
        loops step() until a terminal state, WAITING_HUMAN, PAUSED, or
        DELIVERABLE_READY (design section 3's own description of `start`,
        extended by the two practical stops above: neither one has any
        further autonomous progress available). Not itself the CLI
        command; Writer B's command surface calls this or step()
        directly. Returns the list of summaries, one per step() call, so
        a caller (or a test) can inspect the whole trajectory.

        ALSO RETURNS ON A PARKED RUN, not only on a stop state
        (R-L03-refutation-2 F4, generalised by REFUTATION-3): a step that
        made no autonomous progress writes exactly the same summary on
        every remaining iteration. Doing that max_steps times is not
        persistence, it is the stall symptom this loop is supposed to
        surface. Round 3 detected it by comparing NOTE STRINGS against a
        two-member set, which missed the soft spend stop, a failing
        done-definition, the EXECUTING wedge and the ordinary async park
        the production worker ALWAYS produces. The wave now names its own
        reason and _loop_stops reads it."""
        trace = []
        for _ in range(max_steps):
            summary = self.step(project_id)
            trace.append(summary)
            if _loop_stops(summary):
                break
        return trace

    def _open_dispatch_units(self, run_id):
        """[(unit_row, dispatch_row), ...] for every dispatch of this run
        whose status is DISPATCHED or RESULT_IN. The ONE definition of
        "work is in flight" in this file (REFUTATION-3 LV 7: two helpers
        added in one round disagreed about CLAIMED, so a unit stranded in
        the claim_unit-to-record_dispatch crash window was simultaneously
        in flight, which made the escalation abstain, and not in flight,
        which made the loop park, with an active fence and no founder step
        naming it).

        A unit's STATUS is never consulted, because a status can drift (a
        rejected late result leaves RESULT_IN behind a closed dispatch, and
        a re-plan leaves SKIPPED in front of an open one) while a dispatch
        row cannot: it is the exactly-once spine. A CANCELLED dispatch is
        correctly invisible here, which is what stops a dropped unit's
        answer from being waited for."""
        out = []
        for unit in self.store.list_units(run_id, raw=True):
            for dispatch in self.store.list_dispatches(unit["unit_id"],
                                                       raw=True):
                if dispatch["status"] in ("DISPATCHED", "RESULT_IN"):
                    out.append((unit, dispatch))
        return out

    def _open_dispatches(self, unit_id):
        """Every dispatch of ONE unit that is still open (DISPATCHED or
        RESULT_IN), by the same rule _open_dispatch_units applies to a
        whole run: a dispatch row is open or it is not, and a unit STATUS
        is never consulted. Used by the orphan branches, which exist
        precisely because a unit status and its dispatch rows can disagree
        after a crash."""
        return [d for d in self.store.list_dispatches(unit_id, raw=True)
                if d["status"] in ("DISPATCHED", "RESULT_IN")]

    def _anything_in_flight(self, run_id):
        return bool(self._open_dispatch_units(run_id))

    def _may_dispatch(self, state):
        """Whether this engine may START work from where the run stands.
        See _DISPATCH_SOURCE_STATES for what is deliberately out of that
        set and why."""
        return state in _DISPATCH_SOURCE_STATES

    def _unwind_empty_wave(self, project_id, run_id):
        """A wave that ended with NOTHING in flight has nothing left to
        verify, so leaving the run in EXECUTING is both a false statement
        about it and a dead end: EXECUTING's only forward moves are
        VERIFYING, STOPPING, PAUSED and FAILED_RECOVERABLE, so neither
        WAITING_HUMAN nor DELIVERABLE_READY can ever be reached again
        (REFUTATION-3 LV 1, where the deliverable was ready and permanently
        unreachable, with no founder step saying so). Walk it back to a
        resting state along the SAME legal edges _settle_after_wave uses,
        from which both delivery and WAITING_HUMAN are legal. Returns True
        when it moved the run.

        Passing THROUGH VERIFYING with nothing to verify does not violate
        the step-10 invariant _ensure_verifying states: that invariant is
        "a recorded result implies VERIFYING before verification", a
        one-way implication, not "VERIFYING implies a recorded result"."""
        if self._anything_in_flight(run_id):
            return False
        if self.store.get_run(project_id, raw=True)["state"] != "EXECUTING":
            return False
        self.store.set_run_state(run_id, "VERIFYING", self.actor,
                                 "the wave ended with nothing in flight",
                                 self.session_id, project_id=project_id)
        self.store.set_run_state(run_id, "CHECKPOINTED", self.actor,
                                 "the wave ended with nothing in flight",
                                 self.session_id, project_id=project_id)
        return True

    # -- async result arrival (design step 9/10) -----------------------

    def receive_result(self, project_id, dispatch_id, worker_claim,
                       artifacts, cost=None, summary=None):
        """The entry point for a result arriving OUT OF BAND, after
        RecordIntentWorker parked the run (design: "the run resumes when
        bm-controller record-result is invoked"). Writer B's CLI wraps
        this directly. Performs steps 10 to 19 for this ONE dispatch:
        guard the shape (Store.record_result's own 'already-resulted'
        refusal on a duplicate), walk the run's own state exactly as the
        synchronous path does, record spend, then verify and finish.
        Raises bs.OwnershipRefused if `dispatch_id` names no dispatch or
        one that already has a result (at-most-once recording, fault 2).

        Raises NOTHING from a state walk. A result arriving for a run that
        has meanwhile been stopped, paused or delivered is a REAL
        situation, not a state-machine accident (a founder ran
        `bm-controller stop`; a re-plan dropped the unit and the run
        delivered without it), and it is handled by _handle_late_result
        below rather than by attempting a move the store must refuse
        (R-L03-refutation-2 F1).

        THREE outcome words, not two: a unit_id on acceptance, "rejected",
        and "held" for a result that arrives while the founder has the run
        PAUSED. A pause is reversible, so destroying a real answer because
        of one is not something this engine does (REFUTATION-3 SM A
        consequence 3): the answer is RECORDED, the meter is charged, and
        nothing else happens until the founder resumes, after which the
        RESULT_IN resume branch judges it on its own merits.

        THE HOLD NOW COVERS THE ONLY ROUTE THE SHIPPED CLI HAS TO A PAUSE
        (REFUTATION-4 AZ F4). Round 4 held only on `run["state"] ==
        "PAUSED"`, which no shipped command reaches without a step() in
        between; a result arriving after `bm-autonomy pause` but before
        that step was judged in full, done_check and rollback included.
        The CONTRACT being paused is now the same hold.

        `summary`, when given, is an OUT parameter: the settle path writes
        its stop_reason, its note and its founder_gated block into it, so
        the caller (`bm-controller record-result`) can tell the founder
        that the result it just recorded also delivered the run, or that
        their own done-definition failed. Round 4 computed all three and
        dropped them (REFUTATION-4 LV L4-F1)."""
        self._open_call()
        run = self._run_or_refuse(project_id)
        run_id = run["run_id"]
        summary = {} if summary is None else summary
        # The driver's own gate (L09 GAP 2, widened in refute round 2,
        # finding B3), before ANYTHING is recorded or charged: recording a
        # result and charging the meter are driving the run, and this
        # entry point used to be the widest open door (any process holding
        # a dispatch id could judge, roll back and re-queue another
        # driver's unit). Round 1 guarded only the non-terminal path, so a
        # foreign session recorded a late result on a STOPPED run; the
        # guard is now UNCONDITIONAL. The legitimate late-result-after-stop
        # case comes from the run's OWN driver, whose work was in flight
        # when stop landed and whose session matches, so it passes; only a
        # foreign session is refused.
        self._refuse_foreign_driver(run, "receive_result")
        # The dispatch is looked up and CHECKED AGAINST THIS RUN before
        # anything is recorded or charged (REFUTATION-3 SM K and AZ F-A3):
        # project_id and dispatch_id arrive as independent arguments, and
        # a dispatch belonging to another project, or to an earlier
        # terminal run of this one, used to record its result and charge
        # its spend against a contract that never authorised the work
        # before dereferencing a None unit row and raising an uncaught
        # TypeError that main() does not catch.
        dispatch = self.store.get_dispatch(dispatch_id, raw=True)
        if dispatch is None:
            raise bs.OwnershipRefused(
                "not-found",
                "no dispatch %r in this store." % (dispatch_id,))
        if dispatch["run_id"] != run_id:
            raise bs.OwnershipRefused(
                "foreign-dispatch",
                "dispatch %r belongs to run %r of project %r, not to "
                "project %r's current run %r; nothing was recorded and no "
                "meter was charged. Record it against its own project."
                % (dispatch_id, dispatch["run_id"], dispatch["project_id"],
                   project_id, run_id),
                details={"dispatch_id": dispatch_id,
                         "owner_run_id": dispatch["run_id"],
                         "owner_project_id": dispatch["project_id"]})
        unit_id = dispatch["unit_id"]
        unit = self._unit_row(run_id, unit_id)
        if unit is None:
            # Unreachable after the run_id check above; it is the
            # defensive floor under the TypeError both reports landed on,
            # and a refusal main() catches rather than a traceback.
            raise bs.OwnershipRefused(
                "not-found",
                "dispatch %r names unit %r, which is not in run %r."
                % (dispatch_id, unit_id, run_id))

        contract_state = self._contract_state(project_id)
        if self._is_paused(run) or contract_state == "paused":
            self.store.record_result(
                dispatch_id, worker_claim, artifacts, self.actor,
                project_id=project_id)
            self._record_spend(project_id, unit_id, cost)
            if contract_state == "paused":
                self._set_reason(summary, "CONTRACT_PAUSED",
                                 _NOTE_CONTRACT_PAUSED)
            else:
                self._set_reason(summary, "FOUNDER_WAITING",
                                 _NOTE_RUN_PAUSED)
            return "held"

        self.store.record_result(
            dispatch_id, worker_claim, artifacts, self.actor,
            project_id=project_id)
        # Spend is charged HERE, before the walkability branch, not after
        # it (REFUTATION-3 SM C, a regression: the block used to sit past
        # the late-result return, so every late result silently dropped its
        # spend and the circuit breaker under-counted by exactly that
        # amount, reachable with four shipped commands and no re-plan).
        charged = self._record_spend(project_id, unit_id, cost)
        state = self.store.get_run(project_id, raw=True)["state"]
        if state not in _RESULT_WALKABLE_STATES:
            return self._handle_late_result(
                project_id, run_id, dispatch_id, unit, state, summary,
                charged)
        # The SAME state walk _handle_worker_result performs on the
        # synchronous path, and the RESULT_IN resume branch of
        # _resume_result_in_and_orphans performs on the crash-resume path
        # (its own comment above states the identical reason): a result is
        # recorded, so the run moves to VERIFYING BEFORE any verification
        # runs (design step 10). Omitting it is not cosmetic. A rejection
        # whose rollback ALSO fails must reach FAILED_TERMINAL, which
        # CONTROLLER_STATE_TRANSITIONS allows only from VERIFYING or
        # FAILED_RECOVERABLE; attempted from EXECUTING the store refuses
        # it as illegal and the whole call aborts BEFORE the founder is
        # warned about the dirty write scope and BEFORE the fence is
        # released, leaving the run EXECUTING, holding a fence, and free
        # to re-dispatch the unit (fault 10, broken on this path only).
        # _walk_to_executing first, for the reason the resume branch gives:
        # an out-of-band result can arrive while the run sits in
        # CHECKPOINTED (an earlier unit of the same wave already settled),
        # and VERIFYING is reachable from EXECUTING alone.
        self._walk_to_executing(project_id, run_id)
        self._ensure_verifying(project_id, run_id)
        unit = self._unit_row(run_id, unit_id)
        claimed = {"unit_id": unit_id, "dispatch_id": dispatch_id,
                  "fence_uuid": unit["fence_uuid"],
                  "contract_revision": dispatch["contract_revision"]}
        outcome = self._verify_and_finish(claimed, project_id, run_id,
                                          summary, charged)
        self._settle_after_wave(project_id, run_id, summary)
        return outcome

    def _record_spend(self, project_id, unit_id, cost):
        """The ONE place a result path charges the meter, so late, held and
        ordinary results all charge it exactly once under exactly one rule
        (REFUTATION-3 SM C). The live-contract guard is unchanged and moves
        here with the block: record_spend refuses on a contract that is no
        longer live, and a revoke landing between dispatch and
        record-result must SKIP bookkeeping against an authorisation that
        no longer exists, never crash the result-handling path around it.
        Nothing is lost by skipping it there: _verify_and_finish's own
        staleness re-read is what REJECTS the result a moment later."""
        cost = cost or {}
        tokens = int(cost.get("tokens") or 0)
        minutes = int(cost.get("minutes") or 0)
        if not (tokens or minutes):
            return None
        contract_now = self.store.latest_contract(project_id, raw=True)
        if contract_now is None or contract_now["state"] != "live":
            self._disclose_uncharged_spend(project_id, unit_id, tokens,
                                           minutes, contract_now)
            return None
        self.store.record_spend(
            project_id, tokens, minutes, "unit %s" % unit_id,
            self.session_id, self.actor)
        # The amounts DURABLY CHARGED, for _breaker_is_this_results_own_cost
        # (REFUTATION-5 S2). Returned rather than remembered on self,
        # because this engine holds no state across a call; the value
        # travels down the ONE call chain that judges this one result.
        return {"tokens": tokens, "minutes": minutes}

    def _disclose_uncharged_spend(self, project_id, unit_id, tokens,
                                  minutes, contract_now):
        """The skip above is no longer SILENT (REFUTATION-4 AZ F4). The
        guard itself is design 10.1's and stays: Store.record_spend refuses
        'no-live-contract' outright, so on the shipped route to a PAUSED
        run, where the CONTRACT is what is paused, the meter CANNOT be
        charged at the moment the answer is held. What was wrong was that
        the drop left no trace anywhere, so the breaker under-counted by
        exactly the held unit's cost with nothing to reconcile against,
        which is REFUTATION-3 SM C's own defect reopened for the new "held"
        outcome word.

        A checkpoint is the disclosure surface used rather than a founder
        step ON PURPOSE: queue_human_step gates the unit's whole LANE
        through select_ready_units, so disclosing a bookkeeping gap that
        way would block the very lane the founder is about to resume.
        record_checkpoint needs only SOME contract in any state and gates
        nothing.

        LIMIT, stated rather than hidden: this DISCLOSES the charge, it
        does not defer it. Charging it later is not implementable from this
        file alone, because Store.record_result carries no cost column, so
        the number is gone by the time the held answer is judged. See
        docs/KNOWN-LIMITS.md and this round's report for the exact store
        change that would close it.

        WHAT ROUND 6 ADDED (REFUTATION-5 S7). The checkpoint alone was a
        disclosure nobody had to read, and the reachable magnitude was not
        "one unit's cost": pause, record-result, resume, step, once per
        unit, drove a whole run to DELIVERABLE_READY with 270 claimed
        tokens against a 100 token ceiling, metered 0, verdict `ok`, and
        ZERO open founder steps. A founder step is queued beside the
        checkpoint now, and _deliver_or_hold refuses to declare a
        deliverable while one is open, so a breaker that reads low cannot
        be reached through the shipped commands at all.

        The step goes in SPEND_RECONCILE_LANE, a lane no unit is ever
        planned into, which is what answers the objection above: it is
        founder-visible, enumerable and resolvable through
        `bm-autonomy human-steps`, and it gates no lane the founder is
        about to resume."""
        if contract_now is None:
            return None
        checkpoint_id = self.store.record_checkpoint(
            project_id, self.controller_id, "spend-uncharged",
            "unit %s cost %d token(s) and %d minute(s) that could NOT be "
            "charged: the contract is %s, and spend is only recordable "
            "against a live authorisation. The breaker under-counts by "
            "exactly this much until a founder reconciles it."
            % (unit_id, tokens, minutes, contract_now["state"]),
            self.session_id, self.actor)
        self.store.queue_human_step(
            project_id, "", SPEND_RECONCILE_LANE,
            _SPEND_RECONCILE_MARKER +
            "unit %s cost %d token(s) and %d minute(s) that could NOT be "
            "charged: the contract was %s when the answer arrived, and "
            "spend is only recordable against a live authorisation, so the "
            "breaker reads LOW by exactly this much. This run will NOT be "
            "declared deliverable until it is reconciled. Charge it with "
            "`bm-autonomy spend --project %s --tokens %d --minutes %d`, "
            "then close this with `bm-autonomy human-steps --project %s "
            "--resolve <this step id> --resolution reconciled` "
            "(`bm-autonomy human-steps --project %s --open` lists the id). "
            "Disclosure checkpoint: %s."
            % (unit_id, tokens, minutes, contract_now["state"], project_id,
               tokens, minutes, project_id, project_id, checkpoint_id),
            "", [], self.session_id, self.actor)
        return checkpoint_id

    def _unreconciled_uncharged_spend(self, project_id):
        """Every OPEN uncharged-spend disclosure for this project, oldest
        first (REFUTATION-5 S7).

        Read through list_human_steps rather than through the checkpoint
        trail on purpose: list_human_steps takes no LIMIT, so this answer
        cannot go wrong on a long run, while Store.recent_checkpoints has a
        window a run with enough heartbeats would push an old disclosure
        out of. A question whose answer decides whether a founder's
        ceiling means anything is not a question to answer through a
        window."""
        return [step for step in self.store.list_human_steps(
            project_id, lane=SPEND_RECONCILE_LANE, resolved=False, raw=True)
            if (step["what"] or "").startswith(_SPEND_RECONCILE_MARKER)]

    def _handle_late_result(self, project_id, run_id, dispatch_id, unit,
                            state, summary=None, charged=None):
        """A result whose run state cannot be walked to a judgeable
        position (see _RESULT_WALKABLE_STATES). Every consequence a
        rejection owes the founder is delivered HERE, in this order, and
        the run state is the one thing deliberately left untouched:

          1. the verification is recorded, so the dispatch ends REJECTED
             instead of sitting open forever;
          2. the rejection is recorded against the unit;
          3. the founder is told, UNCONDITIONALLY, in the unit's OWN lane;
          4. the rollback runs, and the founder is warned when it left the
             write scope dirty (fault 10's warning, which the illegal
             state move used to swallow before it was ever written);
          5. the fence is parked, never left ACTIVE;
          6. an interruption names the late result and the run state, and
             is now genuinely best-effort, because step 3 has already told
             the founder when a stopped or revoked contract silences it.

        STEP 3 IS UNCONDITIONAL, and that is the change (REFUTATION-3 SM
        D). Round 3 put the founder step behind `if outcome['status'] !=
        'FAILED'`, and _record_interruption returns None whenever the
        contract is not live, which is one of the commonest reasons a run
        is in a late-result state at all. A unit at its retry ceiling
        therefore got NEITHER, and a real answer was discarded with
        `before=(0, 0) after=(0, 0)`. queue_human_step needs only SOME
        contract in any state, and a contract always exists once a run
        does, so nothing can silence this.

        BLOCK_LANE_UNITS IS GONE FROM THIS PATH (REFUTATION-3 SM F and LV
        2). The founder step in the unit's own lane is the whole mechanism:
        select_ready_units' blocked-lane query makes that lane unselectable
        on its own, so a stopped, paused or delivered run still gains no
        new selectable work. What is removed is BLOCKED written as an
        INDEPENDENT fact, which had no reverse gear anywhere in production
        and so was a one-way door. _reconcile_lane_blocks derives the same
        status from the step this method just queued, and reverses it the
        moment a founder resolves that step.

        Returns "rejected", the same word every other rejection path
        returns, so no caller needs to know this branch exists."""
        unit_id = unit["unit_id"]
        reason = ("late result: the run is %s, a state no result can be "
                  "judged from, so the result is recorded and rejected "
                  "without moving the run" % state)
        self.store.record_verification(
            dispatch_id, None, reason, False, self.actor,
            project_id=project_id)
        outcome = self.store.mark_unit_failed(unit_id, self.actor, reason,
                                              project_id=project_id)
        breaker = ("its retry ceiling is now spent, so it will not be "
                   "attempted again"
                   if outcome["status"] == "FAILED" else
                   "it is re-queued for one more attempt, which a %s run "
                   "will not start" % state)
        self.store.queue_human_step(
            project_id, "", unit["lane"],
            "unit %s returned a result after the run reached %s. The result "
            "was rejected because no result can be judged from %s, and %s. "
            "Re-plan or start a fresh run to attempt it again, then resolve "
            "this step; the controller will not start work in lane %r while "
            "it is open."
            % (unit_id, state, state, breaker, unit["lane"]),
            "", [], self.session_id, self.actor)
        rollback_cmd, rb_refusal = self._rollback_plan(run_id, unit_id)
        if rb_refusal is not None:
            self._warn_unrollbackable_scope(project_id, rb_refusal,
                                            unit["lane"])
        elif rollback_cmd is not None:
            # Gated: a rollback is a real mutation of the founder's working
            # tree, and the kill switch stops it like every other command
            # (REFUTATION-4 AZ F5). None means nothing ran, which is not an
            # exit code and must never be read as one.
            rb = self._run_command(project_id, rollback_cmd, unit, charged,
                                   label="the unit's rollback (git restore)")
            if rb is not None and rb["exit_code"] != 0:
                self._warn_dirty_write_scope(project_id, unit_id,
                                             rb["exit_code"], unit["lane"])
        self._release_fence(unit["fence_uuid"], "parked",
                           "late result on a %s run" % state)
        self._record_interruption(
            project_id, "contradiction",
            "unit %s delivered a late result while the run was %s; the "
            "result was recorded and rejected, its fence parked and the "
            "run left where it was, because no result can be judged from "
            "%s" % (unit_id, state, state))
        # The view catches up with the fact this method just created. A
        # late result can land on a run no later step() will ever drive (a
        # STOPPED one), so deriving the status here rather than "on the
        # next wave" is what keeps the view from being permanently stale;
        # the same reconcile reverses it when the step above is resolved.
        self._reconcile_lane_blocks(project_id, run_id)
        if summary is not None:
            contract_state = self._contract_state(project_id)
            if contract_state != "live":
                self._set_reason(
                    summary, "CONTRACT_NOT_LIVE",
                    _contract_not_live_note(contract_state or "missing",
                                            self._executed))
        return "rejected"

    # -- timeouts (design section 5 fault 4, "worker hangs") -----------

    def check_timeouts(self, project_id, summary=None):
        """A DISPATCHED unit whose open dispatch is older than
        dispatch_timeout_seconds is ABANDONED: recorded as an empty,
        rejected result (Store.record_result then
        Store.record_verification(accepted=False)) and re-queued through
        the normal circuit breaker (mark_unit_failed via _reject). Never
        called automatically inside step(): the caller (the production
        CLI's own scheduler, or a test advancing the injected clock)
        decides when a deadline has genuinely passed. Returns the list of
        abandoned unit_ids.

        Walks the run's own state exactly as receive_result does before
        rejecting anything, and routes an unwalkable state to the SAME
        late-result handler (R-L03-refutation-2 F1: this path recorded a
        result and a verification and then attempted FAILED_TERMINAL
        straight from EXECUTING, which the store refuses, so the founder
        warning and the fence release were lost and every subsequent
        step() raised forever). The wave is then settled the way every
        other result-handling path settles it, converging along legal
        CONTROLLER_STATE_TRANSITIONS edges instead of leaving the run
        parked in EXECUTING for _deliver_or_hold to trip over.

        Two changes this round, and nothing else: the walk, the
        late-result route, the _reject call and the single settle are
        untouched, because twenty-eight of twenty-eight matrix cells held
        under the state-machine lens.

        ONE, a PAUSED run is left entirely alone (law L1): abandoning a
        dispatch records a result AND a rejected verification AND runs the
        founder's own rollback command against their files, which is the
        engine acting on a run the founder paused.

        TWO, the iteration is driven by the DISPATCH rows rather than by a
        unit-status filter. A dispatch is open or it is not; that is a
        dispatch-row fact. A unit whose STATUS drifted (the SKIPPED case
        LV's p14 case B found) is no longer invisible to the timeout, and a
        CANCELLED dispatch is correctly invisible to it."""
        self._open_call()
        run = self.store.get_run(project_id, raw=True)
        if run is None:
            return []
        if self._is_paused(run):
            return []
        summary = {"note": "", "stop_reason": None} if summary is None \
            else summary
        abandoned = []
        now = self._now()
        for unit, dispatch in self._open_dispatch_units(run["run_id"]):
            if dispatch["status"] != "DISPATCHED":
                continue
            age = (now - _parse_iso(dispatch["created_at"])).total_seconds()
            if age < self.dispatch_timeout_seconds:
                continue
            try:
                self.store.record_result(
                    dispatch["dispatch_id"], "", [], self.actor,
                    project_id=project_id)
            except bs.OwnershipRefused:
                # A real result landed between the read above and this
                # call, so record_result refuses 'already-resulted'
                # (REFUTATION-3 SM L). The refuter also proved it self
                # heals on the very next step() through the RESULT_IN
                # resume branch, so the only thing worth fixing is that one
                # raced unit used to cost every OTHER unit in the same call
                # its settle. Not closed further: doing so means one store
                # transaction spanning this whole loop, which is a Store
                # method running a CheckRunner subprocess, and the harness
                # seam exists to forbid exactly that.
                continue
            state = self.store.get_run(project_id, raw=True)["state"]
            if state not in _RESULT_WALKABLE_STATES:
                self._handle_late_result(
                    project_id, run["run_id"], dispatch["dispatch_id"],
                    unit, state, summary)
                abandoned.append(unit["unit_id"])
                continue
            self._walk_to_executing(project_id, run["run_id"])
            self._ensure_verifying(project_id, run["run_id"])
            self.store.record_verification(
                dispatch["dispatch_id"], None,
                "abandoned: no result within %ds"
                % self.dispatch_timeout_seconds, False, self.actor,
                project_id=project_id)
            self._reject(
                {"unit_id": unit["unit_id"],
                 "dispatch_id": dispatch["dispatch_id"],
                 "fence_uuid": unit["fence_uuid"]},
                project_id, run["run_id"],
                "dispatch abandoned: no result within deadline")
            abandoned.append(unit["unit_id"])
        if abandoned:
            self._settle_after_wave(project_id, run["run_id"], summary)
        return abandoned

    # -- internal: resume ------------------------------------------------

    def _resume_result_in_and_orphans(self, project_id, run_id):
        """Design step 1's UNCONDITIONAL resume branches, run BEFORE the
        live-contract check (step 2): (a) FAILED_RECOVERABLE reverses to
        READY first (the design's own reverse-edge note under the
        `fault` transition); (b) a DONE unit whose fence is still ACTIVE
        is an orphan fence from a crash between checkpoint and release,
        released idempotently; (c) a RESULT_IN dispatch resumes straight
        to verification ("go straight to step 12"), literally
        unconditional in the design's own words -- a result that already
        arrived is judged on its own merits (the staleness re-read inside
        _verify_and_finish), never silently dropped because a contract
        happens to have moved. Returns None when there was nothing to
        resume, so the caller's normal step() flow continues; otherwise
        {'completed': [...], 'note': str}.

        TWO MORE ORPHAN SHAPES joined branch (b) in round 4, both of them
        crash windows a refuter walked through:

          * a SKIPPED unit whose fence is still ACTIVE. upsert_units
            reports what it orphaned and ControllerEngine.plan parks it a
            moment later, so this closes the window between those two
            calls (REFUTATION-3 LV 4).
          * a CLAIMED unit with NO dispatch row at all, which is the crash
            window between claim_unit and record_dispatch (LV 7). Its
            fence is parked and Store.release_claimed_unit returns it to
            PENDING or READY by dependency satisfaction. Deliberately NOT
            mark_unit_failed: nothing was ever dispatched, so there was no
            attempt to fail, and burning a retry the unit never earned
            would walk it toward FAILED for a crash."""
        run = self.store.get_run(project_id, raw=True)
        if run["state"] == "FAILED_RECOVERABLE":
            self.store.set_run_state(
                run_id, "READY", self.actor,
                "resuming after a recoverable fault", self.session_id,
                project_id=project_id)

        units = self.store.list_units(run_id, raw=True)
        orphan_note = None
        for unit in units:
            if unit["status"] == "DONE" and unit["fence_uuid"]:
                record = self.store.get(unit["fence_uuid"])
                if record is not None and record.state == "active":
                    self._release_fence(
                        unit["fence_uuid"], "complete",
                        "orphan-fence release on resume",
                        evidence="unit %s already DONE; releasing a fence "
                                "left active by a crash between checkpoint "
                                "and release" % unit["unit_id"])
                    orphan_note = "released an orphan fence on resume"
            elif unit["status"] == "SKIPPED" and unit["fence_uuid"]:
                record = self.store.get(unit["fence_uuid"])
                if record is not None and record.state == "active":
                    self._release_fence(
                        unit["fence_uuid"], "parked",
                        "a re-plan dropped this unit; releasing the fence "
                        "it still held")
                    orphan_note = ("released the fence a dropped unit still "
                                   "held")
            elif (unit["status"] == "CLAIMED"
                  and not self._open_dispatches(unit["unit_id"])):
                if unit["fence_uuid"]:
                    self._release_fence(
                        unit["fence_uuid"], "parked",
                        "a crash between the file claim and the dispatch "
                        "record left this unit holding a fence it never "
                        "used")
                self.store.release_claimed_unit(unit["unit_id"], self.actor,
                                                project_id=project_id)
                orphan_note = ("re-queued a unit stranded between its file "
                               "claim and its dispatch record, burning no "
                               "retry")
            elif (unit["status"] in ("DISPATCHED", "RESULT_IN")
                  and not self._open_dispatches(unit["unit_id"])):
                # THE FOURTH ORPHAN SHAPE (REFUTATION-4 LV L4-F5): a unit
                # left DISPATCHED or RESULT_IN behind a dispatch that is
                # already CLOSED. record_verification writes the dispatch
                # status in its own transaction and mark_unit_failed is a
                # separate one, so a crash between them sits in five
                # two-call windows, three of them added in round 4
                # (_authorise_dispatch's re-await refusal, its stale stamp
                # and its dead fence, plus _verify_and_finish's staleness
                # branch and check_timeouts). _open_dispatch_units cannot
                # see a closed dispatch, check_timeouts cannot see it, the
                # unreachability walk does not consider a DISPATCHED unit,
                # and _is_founder_waiting returns False for it, so the run
                # parked as NOTHING_SELECTABLE forever with the fence held
                # over the founder's files and no founder step naming it:
                # LV 7's damage verbatim, one unit status later.
                #
                # mark_unit_failed rather than release_claimed_unit,
                # deliberately: unlike a CLAIMED unit this one DID have an
                # attempt, so the circuit breaker is what owns the decision
                # to retry it or escalate it.
                if unit["fence_uuid"]:
                    self._release_fence(
                        unit["fence_uuid"], "parked",
                        "a crash between the verification record and the "
                        "unit record left this unit holding a fence behind "
                        "a dispatch that is already closed")
                self.store.mark_unit_failed(
                    unit["unit_id"], self.actor,
                    "the dispatch for this unit was already closed while "
                    "the unit itself was still %s; returned through the "
                    "circuit breaker on resume" % unit["status"],
                    project_id=project_id)
                orphan_note = ("recovered a unit stranded behind a closed "
                               "dispatch and parked the fence it still held")

        contract_state = self._contract_state(project_id)
        for unit in units:
            if unit["status"] != "RESULT_IN":
                continue
            open_d = [d for d in self.store.list_dispatches(
                unit["unit_id"], raw=True) if d["status"] == "RESULT_IN"]
            if not open_d:
                continue
            dispatch = open_d[-1]
            claimed = {"unit_id": unit["unit_id"],
                      "dispatch_id": dispatch["dispatch_id"],
                      "fence_uuid": unit["fence_uuid"],
                      "contract_revision": dispatch["contract_revision"]}
            # THE CONTRACT IS READ BEFORE ANYTHING IS JUDGED, and this
            # branch runs BEFORE step()'s own contract read by design, so
            # it must ask for itself (REFUTATION-4 AZ F5, probe p11: after
            # `bm-autonomy stop` or `revoke`, one plain `bm-controller
            # step` executed the unit's own model-authored done_check and a
            # `git restore` over the founder's files through exactly here).
            if contract_state == "paused":
                # A pause is reversible and the answer is real: hold it
                # exactly where it is, judge nothing, touch nothing, and
                # let step 2 pause the RUN and name the two commands that
                # clear it (AZ F4).
                break
            if contract_state != "live":
                settled = {"note": "", "stop_reason": None}
                self._close_without_running(
                    project_id, run_id, claimed, unit, contract_state,
                    settled)
                # No settle: a run whose authorisation is dead has nothing
                # to settle toward, and step 2 drains it on the next call.
                return {"completed": [], "note": settled["note"],
                        "stop_reason": settled["stop_reason"]}
            # A RESULT_IN dispatch means record_result ALREADY ran (in the
            # crashed prior call), so the run must already be walked all
            # the way to VERIFYING, not just EXECUTING, before verifying:
            # a rejection here can still legally reach FAILED_TERMINAL.
            self._walk_to_executing(project_id, run_id)
            self._ensure_verifying(project_id, run_id)
            settled = {"note": "", "stop_reason": None}
            outcome = self._verify_and_finish(claimed, project_id, run_id,
                                              settled)
            self._settle_after_wave(project_id, run_id, settled)
            completed = ([outcome]
                        if outcome not in (None, "rejected", "held") else [])
            # The settle path's own verdict travels out with the resume,
            # instead of being computed and dropped (REFUTATION-4 LV
            # L4-F1).
            return {"completed": completed,
                    "note": settled["note"] or (
                        "resumed a RESULT_IN dispatch straight to "
                        "verification (crash between result and commit)"),
                    "stop_reason": settled["stop_reason"],
                    "founder_gated": settled.get("founder_gated")}

        if orphan_note is not None:
            return {"completed": [], "note": orphan_note}
        return None

    def _resume_dispatched(self, project_id, run_id):
        """A DISPATCHED unit with no result yet is RE-AWAITED on the SAME
        dispatch_id, never a new one (at-most-once re-dispatch), which
        covers both a genuine crash-before-result and a provider-outage
        recovery (fault 9) identically. UNLIKE RESULT_IN above, this is
        this implementation's OWN addition beyond design step 1's literal
        text (the design only names RESULT_IN and the orphan fence as
        step-1 resume branches), so it is deliberately called from step()
        AFTER the live-contract and spend checks (steps 2 and 3) rather
        than before them: re-awaiting a still-open dispatch is new
        interaction with the worker, and a contract that moved to
        stopped/revoked or a spend breaker that tripped must be able to
        drain instead of blindly re-asking a worker for an answer (fault
        7's own invariant: stop starts no new unit, and a re-await is,
        from the worker's perspective, indistinguishable from a fresh
        ask). Returns None when there was nothing to resume.

        THE BRIEF NOW COMES FROM _authorise_dispatch, like every other
        brief (law L5). Round 3's version built it here and called the
        worker with no gate_check on this route at all, so a founder who
        NARROWED a still-live contract had the worker told a SECOND time
        to write a path they had just forbidden, with the fence over that
        path left active (REFUTATION-3 AZ F-A1, four shipped commands, no
        concurrency). Rejecting the answer afterwards, which the staleness
        re-read does, cannot un-write the file, which is the whole reason
        the gate sits AHEAD of the worker rather than behind it."""
        open_units = [(unit, dispatch) for unit, dispatch
                      in self._open_dispatch_units(run_id)
                      if dispatch["status"] == "DISPATCHED"]
        if not open_units:
            return None
        state = self.store.get_run(project_id, raw=True)["state"]
        if not self._may_dispatch(state):
            # Defence in depth. After the SKIPPED lifecycle closes at the
            # source a delivered run cannot hold an open dispatch, but a
            # re-await is a fresh ask from the worker's point of view and
            # the guard costs one comparison.
            #
            # THE NOTE NAMES THE ONE COMMAND THAT ACTUALLY MOVES IT
            # (REFUTATION-4 LV L4-F4). Round 4 named three: `resume`
            # no-ops unless the run is PAUSED, `complete` is refused by the
            # store from VERIFYING, and only `stop` worked, which destroys
            # a run one `record-result` would have finished.
            return {"completed": [], "stop_reason": "FOUNDER_WAITING",
                    "note": _NOTE_DISPATCH_OPEN_UNMOVABLE
                            % (state, open_units[0][1]["dispatch_id"])}
        unit, dispatch = open_units[0]
        claimed, outcome = self._authorise_dispatch(
            project_id, run_id, unit, open_dispatch=dispatch)
        if claimed is None:
            # DEFER burned nothing and drained nothing; DRAIN already
            # walked the run to STOPPED; every REFUSED word moved a retry
            # count, which IS progress, so the loop may continue and
            # converge.
            #
            # THREE REFUSAL WORDS, NOT ONE (REFUTATION-4 AZ F12). Round 4
            # told every one of them "the live contract no longer
            # authorises the in-flight unit's write scope", including the
            # two that have nothing to do with the contract, so a founder
            # whose fence had been released under an open dispatch was sent
            # to look at an authorisation that never moved.
            reason = {"DEFER": "CONTENTION", "DRAIN": "TERMINAL",
                     "DRAIN-PLAN-GATE": "PLAN_GATE_REFUSED"}.get(outcome)
            note = {
                "DEFER": _NOTE_CONTENTION,
                "DRAIN": "draining: gate_check refused the run's own "
                         "authorisation state",
                "DRAIN-PLAN-GATE":
                    "draining: the plan gate refused this run's dispatch "
                    "(R7); see the refused unit's own dispatch history for "
                    "which clause failed",
                "REFUSED-STALE":
                    "the contract's authorisation moved between this "
                    "unit's dispatch and this re-await, so its dispatch "
                    "was closed and no worker was re-asked; the next step "
                    "opens a fresh attempt under the current revision",
                "REFUSED-FENCE":
                    "the fence this unit's dispatch held no longer holds "
                    "its files (it was released, emptied or taken over "
                    "while the dispatch was open), so the dispatch was "
                    "closed and no worker was re-asked; the contract did "
                    "not move. The next step claims a fresh fence",
            }.get(outcome,
                  "the live contract no longer authorises the in-flight "
                  "unit's write scope; its dispatch was closed and no "
                  "worker was re-asked")
            return {"completed": [], "stop_reason": reason, "note": note}
        self._walk_to_executing(project_id, run_id)
        settled = {"note": "", "stop_reason": None}
        outcome, reason = self._handle_worker_result(
            claimed, self.worker.run(claimed["brief"]), project_id, run_id,
            settled)
        if outcome is not None:
            self._settle_after_wave(project_id, run_id, settled)
        completed = ([outcome]
                    if outcome not in (None, "rejected", "held") else [])
        stop_reason = ("OUTAGE" if reason == "OUTAGE" else
                       "IN_FLIGHT" if reason == "IN_FLIGHT" else
                       settled["stop_reason"])
        note = (_NOTE_OUTAGE if stop_reason == "OUTAGE" else
                _NOTE_IN_FLIGHT % len(self._open_dispatch_units(run_id))
                if stop_reason == "IN_FLIGHT" else
                settled["note"] or
                "re-awaited an in-flight dispatch (crash resume or "
                "provider-outage recovery)")
        return {"completed": completed, "stop_reason": stop_reason,
                "note": note, "founder_gated": settled.get("founder_gated")}

    # -- internal: dispatch -----------------------------------------------

    def _gate_check_write_scope(self, project_id, unit):
        """Step 2's gate_check for ONE unit, over its WHOLE write_scope,
        under ONE contract revision. Returns (verdict, refused_path), where
        refused_path is the path that earned a non-ALLOWED verdict, or None
        when the verdict was earned by the risk class alone (an empty
        write_scope, or a class/floor/state/breaker refusal on the first
        path checked).

        THE PROPERTY THIS ESTABLISHES, stated so a reader can hold the code
        to it: when this returns ALLOWED, EVERY path in the unit's
        write_scope was judged ALLOWED under the SAME contract revision,
        and that is the revision returned, which is the revision the
        dispatch is stamped with and the staleness re-read at step 13
        compares against. Two things are needed for that sentence to be
        true, and both were false before this round:

        ONE, the revision must not straddle the loop. Each gate_check is
        its own store read, so a `sign --supersede` amend from a second
        process can land BETWEEN two of them, leaving path 1 judged under
        the old contract while the dispatch is stamped with the new
        revision, at which point the staleness protocol sees no movement
        and accepts work the live contract forbids (R-L03-refutation-2 F2,
        probe_f2_revision_straddle). So: capture the FIRST verdict's
        revision, re-run the WHOLE loop once against the newer contract if
        any later verdict reports a different one, and if the revision
        moves again during that re-run, return _DEFERRED_CONTENTION so the
        caller defers the unit to a later wave. Deferral, not failure: a
        contract being amended twice inside one loop is contention, not an
        authorisation gap for this unit, and burning its retry or draining
        the run for it would punish the wrong thing.

        TWO, gate_check's own path comparison must be CONTAINMENT rather
        than overlap, or a unit could declare the PARENT of an allowed path
        and be authorised over every sibling the contract refuses by name
        (the same report's probe_f2b_ancestor_scope). That half of the
        property lives in the store, in Store.gate_check and
        bs.path_within_allowed; this method's claim depends on it.

        WHY EVERY PATH, not just the first: the design states this
        precondition twice and the two statements disagree. Section 1's
        `select` transition (design line 163) says "gate_check ALLOWED for
        the chosen unit's risk_class and write_scope", the whole scope;
        section 3 step 2's shorthand says `path=<write_scope[0]>`. The
        STRICTER reading wins, because allowed_paths is the founder's
        boundary on what the autonomous system may touch and gate_check is
        the ONLY component that ever compares a unit's scope against it:
        the fence claim (step 6) and the brief (_build_brief) both carry
        the FULL write_scope, so a forbidden path listed second would be
        claimed and handed to a worker as authorised, and the advisory
        after-the-fact net (bm_bash_audit) cannot catch it either, since
        the write IS inside the unit's own declared write_scope.

        read_scope is deliberately NOT gate-checked: the design's own
        precondition names risk_class and write_scope only, and
        gate_check's path check reads allowed_paths, a WRITE boundary.
        Gating reads on it would refuse units the contract permits.

        One call per DISTINCT path, in declaration order, short-circuiting
        on the first refusal (which is also what stops a REFUSED-STATE or
        REFUSED-BREAKER verdict from being followed by pointless further
        checks before the caller starts its drain).

        THE CONTAINER QUESTION IS ASKED FIRST, HERE, and not only by the
        dispatch route (REFUTATION-6 F4). `for path in unit["write_scope"]
        or []` is a TypeError for a scalar and a character shredder for a
        bare string, and round 6 put this method on the JUDGING path
        without carrying _authorise_dispatch's own first line across, so
        the SCOPE half of the authorisation refusal was not total: a
        non-iterable row left a traceback out of receive_result, and
        main() catches bs.BMStoreError and ValueError only.

        The ENTRY question (a pathspec, an option-looking path, a glob)
        is deliberately NOT asked here: it is asked where it is ACTED on,
        in _rollback_plan, which composes no command for such a row and
        tells the founder the scope was left as the worker left it. Moving
        it here would refuse the done_check of a unit whose answer is
        already in hand, and an existing test pins that behaviour
        (test_a_scope_that_goes_bad_after_dispatch_is_never_rolled_back).
        The container question is different in kind: without it there is
        no loop to run at all."""
        problem = _unsafe_scope_container("write_scope",
                                          unit.get("write_scope"))
        if problem is not None:
            return {"verdict": _REFUSED_SCOPE_SHAPE, "floor": None,
                    "revision": None, "contract_id": None,
                    "reason": "unit %s cannot be judged against the "
                              "contract: %s"
                              % (unit["unit_id"], problem)}, None
        seen, paths = set(), []
        for path in unit["write_scope"] or []:
            if path not in seen:
                seen.add(path)
                paths.append(path)
        # Two passes at most: the second exists only to re-judge every path
        # under a revision that moved during the first, and a revision that
        # moves during the second is contention this wave cannot resolve.
        for _pass in (1, 2):
            verdict, refused_path, straddled = self._gate_check_one_pass(
                project_id, unit, paths)
            if not straddled:
                return verdict, refused_path
        return {"verdict": _DEFERRED_CONTENTION, "floor": None,
                "revision": None, "contract_id": None,
                "reason": "the contract revision moved twice while unit %s "
                          "was being checked path by path; deferring the "
                          "unit rather than judging its paths under two "
                          "different authorisations"
                          % unit["unit_id"]}, None

    def _gate_check_one_pass(self, project_id, unit, paths):
        """ONE pass of the per-path loop. Returns (verdict, refused_path,
        straddled), where straddled is True when a verdict came back under
        a DIFFERENT contract revision than the first verdict of this same
        pass, which makes the whole pass unusable: some paths were judged
        under an authorisation that is no longer the live one. The straddle
        check runs BEFORE the ALLOWED check, so a refusal earned under the
        newer contract does not get reported as this pass's answer while
        earlier paths were judged under the older one."""
        verdict, first_revision, have_first = None, None, False
        for path in (paths or [None]):
            verdict = self.store.gate_check(
                project_id, unit["risk_class"], path=path)
            if not have_first:
                have_first, first_revision = True, verdict["revision"]
            elif verdict["revision"] != first_revision:
                return verdict, path, True
            if verdict["verdict"] != "ALLOWED":
                return verdict, path, False
        return verdict, None, False

    def _authorise_dispatch(self, project_id, run_id, unit,
                            open_dispatch=None):
        """THE ONE route from a unit row to a brief a worker may be handed
        (law L5). Returns (claimed, outcome):

          claimed  {'unit_id','dispatch_id','fence_uuid',
                    'contract_revision','brief'} or None
          outcome  None      claimed is not None, hand the brief over
                   'DEFER'   contention: nothing burned, nothing drained,
                             the unit is tried again next wave
                   'DRAIN'   REFUSED-STATE, REFUSED-BREAKER, or R7's plan
                             gate refused (see plan_gate below): the whole
                             run is draining
                   'REFUSED' this unit was failed through the circuit
                             breaker

        R7, CHECKED FIRST, BEFORE ANY OF THE ROUTE-SPECIFIC WORK BELOW.
        This is THE ONE route from a unit row to a brief a worker may be
        handed, on BOTH the fresh route and the re-await route (the
        docstring line above), which makes it the single funnel R7 needs:
        "the controller refuses to enter the first consequential
        operation" is enforced here once rather than duplicated at every
        caller. self.plan_gate is None by default (see PlanGate's own
        docstring), so this costs nothing until an engine is
        deliberately constructed with one.

        open_dispatch is None on the FRESH route and the open dispatch row
        on the RE-AWAIT route. Both routes run the SAME gate check over the
        SAME whole write_scope under ONE contract revision, which is the
        property _gate_check_write_scope's docstring states and which the
        re-await route falsified for as long as it existed beside this one
        (REFUTATION-3 AZ F-A1).

        Steps 2 (per-unit gate_check over the unit's whole write_scope,
        capturing the revision the staleness re-read at step 13 compares
        against), 6 (claim files before dispatch) and 8 (record dispatch
        intent, durable before the worker runs). Never raises: a fence
        overlap is design step 5's parallel-fenced demotion ("any overlap
        demotes the pair to pipeline", realised here as "defer to a later
        wave"), a revision that moved twice inside the per-path gate check
        is the same deferral for contention rather than for an overlap, and
        a REFUSED-STATE or REFUSED-BREAKER verdict starts the whole-run
        drain, matching step 2's "a revoke that lands between check and
        act".

        BEFORE ANY OF THAT, the unit's write_scope must be a list of plain
        relative paths (REFUTATION-5 S1). gate_check judges a path against
        the founder's boundary; it does not ask whether the string is a
        path at all, and `:!keep.txt` is inside `allowed_paths ['.']` by
        every check the store makes while being 'everything except
        keep.txt' to git. So the shape is asked here, on the row as the
        engine actually reads it, and a bad one is a NAMED founder-visible
        refusal rather than a silent skip."""
        if self.plan_gate is not None:
            try:
                self.plan_gate.check(project_id)
            except bm_plan.PlanError as exc:
                # Whole-run drain, the same shape a REFUSED-STATE or
                # REFUSED-BREAKER gate_check verdict already uses a few
                # lines below: a plan gate refusal is not this unit's
                # problem to retry, it is a statement about whether this
                # RUN may dispatch anything at all right now. A DISTINCT
                # outcome word, 'DRAIN-PLAN-GATE' rather than the plain
                # 'DRAIN' gate_check already uses, so step()'s wave
                # unwind and _resume_dispatched's re-await handling (both
                # below) can write the distinct PLAN_GATE_REFUSED stop
                # reason instead of the generic TERMINAL one, which is
                # what lets the CLI turn this specific refusal into a
                # real non-zero exit code rather than a stop_reason a
                # caller has to parse out of a JSON blob. STATED CEILING
                # (PlanGate's own docstring carries the long version):
                # this proves the approved plan version matches what is
                # about to run, never that the plan was good.
                self._begin_stopping(
                    project_id, run_id,
                    "plan gate refused dispatch (%s): %s"
                    % (exc.reason, exc))
                return None, "DRAIN-PLAN-GATE"
        scope_problem = self._unsafe_write_scope(unit)
        if scope_problem is not None:
            return None, self._refuse_unsafe_scope(
                project_id, unit, scope_problem, open_dispatch)
        verdict, refused_path = self._gate_check_write_scope(
            project_id, unit)
        if verdict["verdict"] == _DEFERRED_CONTENTION:
            # Not an authorisation gap and not a TOCTOU drain: two amends
            # landed inside one per-path loop, so no single revision can be
            # stamped on this dispatch honestly. Deferred exactly the way a
            # fence overlap is deferred, leaving the unit READY with its
            # retry count untouched for the next wave to try again. On the
            # re-await route nothing is touched at all: the dispatch stays
            # open and the fence stays held, which is what "deferred to a
            # later wave" means for work already in flight.
            return None, "DEFER"
        if verdict["verdict"] in ("REFUSED-STATE", "REFUSED-BREAKER"):
            self._begin_stopping(
                project_id, run_id,
                "gate_check for unit %s: %s"
                % (unit["unit_id"], verdict["reason"]))
            return None, "DRAIN"
        if verdict["verdict"] != "ALLOWED":
            if open_dispatch is not None:
                # The unit is IN FLIGHT over a path the founder has just
                # forbidden. Close the dispatch and park the fence rather
                # than leaving either open over that path; the circuit
                # breaker below then does exactly what it does on the fresh
                # route.
                self.store.record_verification(
                    open_dispatch["dispatch_id"], None,
                    "the live contract no longer authorises this unit's "
                    "write scope: %s" % verdict["reason"], False, self.actor,
                    project_id=project_id)
                self._release_fence(
                    unit["fence_uuid"], "parked",
                    "the live contract no longer authorises this unit's "
                    "write scope")
            # REFUSED-CLASS/SCOPE/FLOOR: not a transient TOCTOU (that is
            # REFUSED-STATE/BREAKER above), a genuine authorisation gap
            # for THIS unit. Routed through the SAME circuit breaker a
            # dispatch rejection uses (mark_unit_failed), rather than
            # silently re-selecting the same ungrantable unit forever:
            # without this, select_ready_units would keep returning it
            # every wave and the run would spin with no progress and no
            # visible escalation.
            # Name the PATH only when the path is what earned the
            # refusal: REFUSED-SCOPE is the one path-caused verdict, and
            # a class or floor refusal on a unit that happens to have
            # paths was never about any of them.
            scope_note = (
                "path %s" % refused_path
                if refused_path and verdict["verdict"] == "REFUSED-SCOPE"
                else "risk class %s" % unit["risk_class"])
            outcome = self.store.mark_unit_failed(
                unit["unit_id"], self.actor,
                "gate_check refused (%s) for %s: %s"
                % (verdict["verdict"], scope_note, verdict["reason"]),
                project_id=project_id)
            if outcome["status"] == "FAILED":
                self._record_interruption(
                    project_id, "hard-gate-collision",
                    "unit %s (%s) is not authorised by the live contract "
                    "after %d attempt(s), refused on %s: %s"
                    % (unit["unit_id"], unit["risk_class"],
                       outcome["retry_count"], scope_note,
                       verdict["reason"]))
            return None, "REFUSED"

        if open_dispatch is not None:
            # -- the RE-AWAIT route. ALLOWED, so the live contract still
            # authorises this unit's whole write scope. Two things can
            # still make re-asking the worker wrong, and both close the
            # dispatch rather than opening a second one.
            moved, latest_revision = self._authorisation_moved(
                project_id, open_dispatch["contract_revision"])
            if moved:
                # The answer this worker would give is already doomed by
                # the staleness re-read at step 13, so close it now with
                # the SAME sentence that re-read uses. The next wave opens
                # a fresh dispatch under the current revision. A pause and
                # a resume are NOT such a move, for the reason
                # _authorisation_moved states.
                self.store.record_verification(
                    open_dispatch["dispatch_id"], None,
                    "stale: contract moved from revision %s to %s between "
                    "dispatch and re-await"
                    % (open_dispatch["contract_revision"], latest_revision),
                    False, self.actor, project_id=project_id)
                self.store.mark_unit_failed(
                    unit["unit_id"], self.actor,
                    "contract revision moved between dispatch and re-await; "
                    "re-queued rather than re-asked under authorisation "
                    "that no longer holds", project_id=project_id)
                self._release_fence(unit["fence_uuid"], "parked",
                                    "stale contract revision")
                return None, "REFUSED-STALE"
            record = (self.store.get(unit["fence_uuid"])
                      if unit["fence_uuid"] else None)
            fence_problem = self._fence_no_longer_holds(record, unit)
            if fence_problem is not None:
                # The fence stopped holding this unit's files while the
                # dispatch was open. Do NOT re-claim and do NOT flip a
                # DISPATCHED unit back to CLAIMED: the next wave takes the
                # fresh route and opens attempt N+1, so at-most-once
                # re-dispatch is preserved by
                # controller_dispatches.UNIQUE(unit_id, attempt), not by
                # trust.
                self.store.record_verification(
                    open_dispatch["dispatch_id"], None,
                    "the fence this dispatch held no longer holds this "
                    "unit's files (%s), so they are not this unit's to "
                    "write" % fence_problem, False, self.actor,
                    project_id=project_id)
                self.store.mark_unit_failed(
                    unit["unit_id"], self.actor,
                    "the fence held for this dispatch stopped holding this "
                    "unit's files while it was open (%s); re-queued for a "
                    "fresh claim" % fence_problem, project_id=project_id)
                self._release_fence(
                    unit["fence_uuid"], "parked",
                    "the fence no longer holds this unit's files")
                return None, "REFUSED-FENCE"
            brief = self._build_brief(unit, open_dispatch["attempt"])
            # No second dispatch row is EVER opened on this route.
            return {"unit_id": unit["unit_id"],
                    "dispatch_id": open_dispatch["dispatch_id"],
                    "fence_uuid": unit["fence_uuid"], "brief": brief,
                    "contract_revision": open_dispatch["contract_revision"]}, \
                None

        # -- the FRESH route.
        fence_name = self.UNIT_FENCE_PREFIX + unit["unit_id"]
        try:
            record = self.store.claim(
                name=fence_name, lifetime="ephemeral",
                files=unit["write_scope"], objective=unit["objective"],
                owner=self.controller_id, session_id=self.session_id)
        except bs.OwnershipRefused:  # sbe: allow-silent OwnershipRefused here is ordinary claim contention; the caller already returns the documented DEFER sentinel for it
            return None, "DEFER"
        try:
            self.store.claim_unit(unit["unit_id"], record.lifecycle_uuid,
                                  self.actor, project_id=project_id)
        except bs.OwnershipRefused as exc:
            if exc.reason != "unit-not-claimable":
                raise
            # THE STALE SELECTION, closed on this side too (cross-family
            # refuter, finding 5). `unit` came from select_ready_units at
            # step 4, several store transactions ago, and the run was
            # walked to EXECUTING in between; a second process running
            # `plan` in that window commits its removal as SKIPPED, and the
            # claim this engine is holding is built on a fact that has
            # stopped being true. The store now refuses that claim instead
            # of overwriting the re-plan's decision, and this is what the
            # refusal means HERE: contention, deferred to a later wave,
            # exactly as a fence overlap two lines above is deferred.
            #
            # NOT mark_unit_failed: nothing was dispatched, nothing was
            # judged and the unit did not fail, so burning a retry would
            # walk a unit toward FAILED for someone else's re-plan. NOT a
            # drain either: the re-plan IS the founder's own intent, and
            # the very next wave re-selects from the graph they left.
            #
            # The fence is released because it was claimed one call
            # earlier, over the files of a unit this engine is now not
            # dispatching: leaving it active would block every later wave
            # over those paths with nothing naming it, which is the orphan
            # class release_claimed_unit exists for on the crash route.
            self._release_fence(
                record.lifecycle_uuid, "parked",
                "unit %s was no longer claimable when the claim landed "
                "(%s); the unit is deferred to a later wave and its files "
                "are released" % (unit["unit_id"],
                                  exc.details.get("status", "status moved")))
            return None, "DEFER"
        attempt = self._next_attempt(unit["unit_id"])
        dispatch_id = self.store.record_dispatch(
            unit["unit_id"], attempt, verdict["revision"],
            record.lifecycle_uuid, self.session_id, self.actor,
            project_id=project_id)
        brief = self._build_brief(unit, attempt)
        return {"unit_id": unit["unit_id"], "dispatch_id": dispatch_id,
                "fence_uuid": record.lifecycle_uuid, "brief": brief,
                "contract_revision": verdict["revision"]}, None

    def _unsafe_write_scope(self, unit):
        """None when every entry of `unit`'s STORED write_scope is a plain
        relative path, or a phrase naming the first one that is not.

        Asked of the row the engine actually holds, not of the graph a
        founder typed, so it covers a row that arrived by any route."""
        scope = unit.get("write_scope")
        problem = _unsafe_scope_container("write_scope", scope)
        if problem is not None:
            return problem
        for entry in scope or []:
            problem = _unsafe_scope_entry(entry)
            if problem is not None:
                return "its write_scope entry %r %s" % (entry, problem)
        return None

    def _refuse_unsafe_scope(self, project_id, unit, problem,
                             open_dispatch=None):
        """Fail one unit whose write_scope this engine may not act on, and
        TELL THE FOUNDER. Returns the outcome word _authorise_dispatch's
        caller reads.

        The founder step is what makes this a refusal rather than a skip:
        it names the unit and the offending entry verbatim, and (because
        Store.select_ready_units skips a lane holding an open step) it also
        stops the same unit being re-selected and re-refused on every
        later wave. An in-flight dispatch is closed and its fence parked
        first, for the same reason the REFUSED-SCOPE branch below closes
        one: neither may be left open over paths this engine has just said
        it cannot act on."""
        unit_id = unit["unit_id"]
        reason = ("unit %s cannot be dispatched: %s. Nothing was claimed, "
                  "nothing was handed to a worker and no rollback can be "
                  "composed for it." % (unit_id, problem))
        if open_dispatch is not None:
            self.store.record_verification(
                open_dispatch["dispatch_id"], None, reason, False,
                self.actor, project_id=project_id)
            self._release_fence(
                unit["fence_uuid"], "parked",
                "the unit's write_scope is not a list of plain relative "
                "paths")
        self.store.mark_unit_failed(unit_id, self.actor, reason,
                                    project_id=project_id)
        self.store.queue_human_step(
            project_id, "", unit.get("lane") or "default",
            "%s A write scope is what a fence claims, what a worker's brief "
            "authorises and what `git restore --` names, so this engine "
            "will not act on it. Re-plan the unit with a list of plain "
            "relative paths inside the project, then resolve this step."
            % (reason,), "", [], self.session_id, self.actor)
        self._record_interruption(
            project_id, "hard-gate-collision",
            "unit %s declares a write_scope this engine cannot act on: %s"
            % (unit_id, problem))
        return "REFUSED"

    def _fence_no_longer_holds(self, record, unit):
        """None when `record` is still THIS controller's active fence over
        EVERY path in the unit's write_scope, or a short phrase naming what
        is wrong with it.

        REFUTATION-4 AZ F8. The re-await route compared ONE field, the
        fence's state, and design 6.2 step 5b calls that "the fence check".
        A same-session reclaim (Store.claim's own documented "files=[] is a
        deliberate release, always honored") EMPTIES a fence and changes
        its owner while leaving it active, so the worker was handed a brief
        for a.py under a fence that held nothing, and design step 6's
        promise ("claim files before dispatch, durable before the worker
        runs") was never re-established on this route: only the existence
        of a row was.

        Containment, never overlap, and in the same direction gate_check
        uses: every declared path must be INSIDE something the fence holds,
        so a fence narrowed to a subdirectory does not pass for a unit that
        declared the parent."""
        if record is None:
            return "it no longer exists"
        if record.state != "active":
            return "it is %s, not active" % record.state
        if record.owner != self.controller_id:
            return ("it is now held by %r, not by this controller"
                    % (record.owner,))
        held = list(record.files or [])
        for path in unit["write_scope"] or []:
            if not any(bs.path_within_allowed(f, path) for f in held):
                return "it no longer holds %s" % path
        return None

    def _build_brief(self, unit, attempt, failure_note=""):
        """UnitBrief: what the WorkerAdapter receives. `failure_note`
        carries the circuit breaker's own instruction (design step 17: "a
        brief that RECORDS the failed approach so the retry is a
        different attempt, not a third identical one")."""
        return {"unit_id": unit["unit_id"], "objective": unit["objective"],
                "read_scope": unit["read_scope"],
                "write_scope": unit["write_scope"], "role": unit["role"],
                "risk_class": unit["risk_class"], "attempt": attempt,
                "prior_failure_note": failure_note}

    # -- internal: receiving and judging a result --------------------

    def _handle_worker_result(self, claimed, result, project_id, run_id,
                              summary=None):
        """Steps 10/11: receive the untrusted result, guard its shape.
        Returns (outcome, reason):

          outcome  a unit_id on acceptance, "rejected" on a rejection this
                   method itself resolved (malformed), or None when
                   nothing was recorded yet
          reason   'IN_FLIGHT' for a pending async park, 'OUTAGE' for a
                   worker that reported itself unavailable, None otherwise

        The SECOND value is the round-4 addition (REFUTATION-3 LV 6c and
        SM J): round 3 returned None for BOTH the ordinary async park and a
        provider outage, so no caller could tell them apart, and a driver
        re-asked the same unavailable worker max_steps times (15 of 15
        steps, 15 asks) while the shape the production RecordIntentWorker
        ALWAYS produces spun for the same reason.

        THE SHAPE QUESTION IS ASKED FIRST (CROSS-FAMILY REFUTATION finding
        2). This method used to read `result.get("status")` on its first
        line, BEFORE the malformed-result branch below, so an SDK
        WorkerAdapter returning None, a list, a string or an int raised
        AttributeError straight out of the engine: the unit was left
        DISPATCHED, the run EXECUTING and the fence ACTIVE, which is worse
        than the malformed result it should have been, because the next
        step finds an orphaned dispatch rather than a rejection it can
        retry. `result` is UNTRUSTED worker output and the very first
        question about untrusted input is what shape it is; a result that
        is not a dict now takes exactly the same recorded rejection every
        other malformed result takes."""
        shapeless = not isinstance(result, dict)
        status = None if shapeless else result.get("status")
        dispatch_id = claimed["dispatch_id"]
        unit_id = claimed["unit_id"]

        if status == "pending":
            return None, "IN_FLIGHT"

        if status == "unavailable":
            # Provider outage (fault 9): the dispatch stays DISPATCHED,
            # NOTHING is recorded against it, and the run goes
            # FAILED_RECOVERABLE so the run drains new work but keeps
            # this dispatch alive for an idempotent retry on resume.
            self.store.set_run_state(
                run_id, "FAILED_RECOVERABLE", self.actor,
                "worker unavailable for unit %s" % unit_id, self.session_id,
                project_id=project_id)
            return None, "OUTAGE"

        if shapeless or status == "malformed" or "artifacts" not in result:
            # Untrusted worker output never crashes the controller
            # (fault 5): treated as a rejected result, not an exception.
            # `shapeless` is checked FIRST so the two reads below are only
            # ever made of something that really is a dict.
            self.store.record_result(
                dispatch_id,
                "" if shapeless else result.get("worker_claim", ""), [],
                self.actor, project_id=project_id)
            self._ensure_verifying(project_id, run_id)
            self.store.record_verification(
                dispatch_id, None, "malformed worker output", False,
                self.actor, project_id=project_id)
            self._reject(claimed, project_id, run_id,
                        "malformed worker output")
            return "rejected", None

        worker_claim = result.get("worker_claim", "")
        artifacts = result.get("artifacts") or []
        self.store.record_result(dispatch_id, worker_claim, artifacts,
                                 self.actor, project_id=project_id)
        self._ensure_verifying(project_id, run_id)
        charged = self._record_spend(project_id, unit_id,
                                     result.get("cost"))
        return (self._verify_and_finish(claimed, project_id, run_id, summary,
                                        charged),
                None)

    def _verify_and_finish(self, claimed, project_id, run_id, summary=None,
                           charged=None):
        """Steps 12 to 15: the controller's OWN done-check (INDEPENDENT of
        the worker: a worker that lies about tests is caught here because
        the real exit code is re-read, never the worker's claim), the
        staleness re-read (step 13, the TOCTOU window U1 leaves to U2),
        the verifier, and on acceptance the checkpoint-then-release
        sequence, each its own committed store call, in the exact order
        design step 15 states.

        THE CONTRACT IS READ FIRST, BEFORE ANY COMMAND (REFUTATION-4 AZ
        F5). This method is the single place both refuted sequences landed:
        `bm-controller record-result` after `bm-autonomy stop` (probe p12)
        and `bm-controller step` on a RESULT_IN dispatch after a revoke
        (probe p11), both of which executed the unit's own model-authored
        done_check and then a `git restore` over the founder's files. A
        contract that is not live authorises nothing, and judging is a
        store read and a verdict, not a subprocess.

        THE WHOLE AUTHORISATION IS READ, NOT ONLY THE CONTRACT ROW'S STATE
        (REFUTATION-5 S2). See _authorisation_refusal: a tripped spend
        breaker, and a write scope the live contract no longer admits, now
        stop every command here exactly as a stopped contract does.

        AND IT IS READ AGAIN AFTER THE COMMAND (REFUTATION-5 S3). The
        window between the read and the verdict is exactly as long as the
        founder's done_check, which in production is a test suite. A stop
        or a revoke landing inside it was classified by
        _authorisation_moved as an authorisation-PRESERVING lifecycle
        change, so the staleness branch was skipped and the unit was
        accepted under a contract the founder had already killed. Round 4
        rejected that same sequence; the re-read below restores that, and
        keeps _LIFECYCLE_CHANGE_KINDS exactly as it is, because the
        question "did the authorisation CHANGE" and the question "is there
        still an authorisation" are different questions and this method
        now asks both."""
        unit_id = claimed["unit_id"]
        dispatch_id = claimed["dispatch_id"]
        unit = self._unit_row(run_id, unit_id)

        refusal = self._authorisation_refusal(project_id, unit, charged)
        if refusal is not None:
            return self._close_without_running(
                project_id, run_id, claimed, unit,
                self._contract_state(project_id), summary, refusal=refusal)

        outcome = self._run_command(project_id, unit["done_check"] or "true",
                                    unit, charged,
                                    label="the unit's done_check")
        if outcome is None:
            # The authorisation died between the read above and this call.
            # The same answer as the branch above, reached one statement
            # later.
            return self._close_without_running(
                project_id, run_id, claimed, unit,
                self._contract_state(project_id), summary,
                refusal=self._authorisation_refusal(project_id, unit,
                                                    charged))
        exit_ok = outcome["exit_code"] == unit["done_check_expect_exit"]

        # -- WINDOW 1 of 3 on this path (REFUTATION-5 S3, REFUTATION-6 F1):
        # the kill switch can land DURING the done_check above.
        stale = self._authorisation_window(
            claimed, project_id, run_id, unit, outcome["exit_code"], summary,
            charged)
        if stale is not None:
            return stale

        moved, latest_revision = self._authorisation_moved(
            project_id, claimed["contract_revision"])
        if moved:
            return self._reject_as_stale(
                claimed, project_id, run_id, unit, outcome["exit_code"],
                "stale: contract moved from revision %s to %s between "
                "dispatch and verification"
                % (claimed["contract_revision"], latest_revision),
                summary, charged=charged)

        verifier_ok = True
        if unit["verifier"]:
            v_outcome = self._run_command(project_id, unit["verifier"],
                                          unit, charged,
                                          label="the unit's verifier")
            if v_outcome is None:
                return self._close_without_running(
                    project_id, run_id, claimed, unit,
                    self._contract_state(project_id), summary,
                    refusal=self._authorisation_refusal(project_id, unit,
                                                        charged))
            verifier_ok = v_outcome["exit_code"] == 0
            # -- WINDOW 2 of 3. The verifier is MODEL-authored shell in
            # full auto, so its duration is unbounded and it is the window
            # REFUTATION-6 F1 walked through. Round 6 asked nothing between
            # here and the acceptance below.
            stale = self._authorisation_window(
                claimed, project_id, run_id, unit, outcome["exit_code"],
                summary, charged)
            if stale is not None:
                return stale
        accepted = exit_ok and verifier_ok
        verdict_text = ("pass" if accepted else
                        "fail: done_check_exit=%s (wanted %s) verifier_ok=%s"
                        % (outcome["exit_code"],
                           unit["done_check_expect_exit"], verifier_ok))
        # -- WINDOW 3 of 3, and the one that holds whatever a later edit
        # puts above it: NOTHING is written about this unit's fate until
        # the authorisation has been asked one last time, immediately
        # before the write. Window 2 is adjacent to it today and is kept
        # anyway, because "re-asked after every command" and "re-asked
        # before the acceptance" are two different promises and the second
        # one must not depend on nothing ever being inserted between them.
        stale = self._authorisation_window(
            claimed, project_id, run_id, unit, outcome["exit_code"], summary,
            charged)
        if stale is not None:
            return stale
        self.store.record_verification(
            dispatch_id, outcome["exit_code"], verdict_text, accepted,
            self.actor, project_id=project_id)

        if accepted:
            checkpoint_id = self.store.record_checkpoint(
                project_id, self.controller_id, "unit-green",
                "unit %s" % unit_id, self.session_id, self.actor)
            self.store.mark_unit_done(unit_id, checkpoint_id, self.actor,
                                      project_id=project_id)
            self._release_fence(
                claimed["fence_uuid"], "complete", "unit green",
                evidence="done_check exit=%s (wanted %s)"
                        % (outcome["exit_code"],
                           unit["done_check_expect_exit"]))
            return unit_id

        self._reject(claimed, project_id, run_id, verdict_text, charged)
        return "rejected"

    def _reject_as_stale(self, claimed, project_id, run_id, unit, exit_code,
                         reason, summary=None, stop_reason=None, note=None,
                         charged=None):
        """The staleness rejection, factored so the round-6 mid-command
        kill (REFUTATION-5 S3) rejects through the IDENTICAL sequence
        rather than a lookalike of it: record the rejected verification
        carrying the real exit code, return the unit through the circuit
        breaker, roll back and warn, park the fence.

        The rollback here is gated like every other command, so under a
        contract that has just died it composes and then runs NOTHING,
        which is the kill switch still working. The founder learns about
        the half-written write scope from the note and the step instead.

        `stop_reason`/`note` are written into the caller's summary only
        when given: the ORDINARY staleness path deliberately leaves the
        summary to the settle path, exactly as it did in round 5."""
        self.store.record_verification(
            claimed["dispatch_id"], exit_code, reason, False, self.actor,
            project_id=project_id)
        self.store.mark_unit_failed(
            claimed["unit_id"], self.actor,
            "authorisation no longer holds mid-unit (%s); re-queued rather "
            "than accepted" % reason, project_id=project_id)
        # The staleness rejection ALSO rolls back and warns, like every
        # other rejection path (REFUTATION-3 SM observation 3): this branch
        # used to park the fence and return, so a founder whose contract
        # moved mid-unit was the one founder who got no warning about a
        # half-written write scope.
        rollback_cmd, rb_refusal = self._rollback_plan(run_id,
                                                       claimed["unit_id"])
        lane = (unit or {}).get("lane") or "default"
        if rb_refusal is not None:
            self._warn_unrollbackable_scope(project_id, rb_refusal, lane)
        elif rollback_cmd is not None:
            rb = self._run_command(project_id, rollback_cmd, unit, charged,
                                   label="the unit's rollback (git restore)")
            if rb is not None and rb["exit_code"] != 0:
                self._warn_dirty_write_scope(
                    project_id, claimed["unit_id"], rb["exit_code"], lane)
        self._release_fence(claimed["fence_uuid"], "parked",
                           "authorisation no longer holds: %s" % reason)
        if stop_reason is not None:
            # The founder step is owed whether or not a caller passed a
            # summary to write into: a summary is a REPORTING channel and
            # a queued step is a durable one, and round 5's own defect
            # class (REFUTATION-3 SM D) was exactly a founder step put
            # behind a condition that was sometimes false. The ORDINARY
            # staleness path passes no stop_reason and queues nothing,
            # which is round 5's behaviour unchanged.
            self.store.queue_human_step(
                project_id, "", lane,
                "unit %s answered, this call RAN %s, and the authorisation "
                "they ran under was gone by the time the verdict was read "
                "(%s). The result is recorded and rejected and its "
                "write_scope is in whatever state they left it, so it needs "
                "manual inspection. Sign a fresh contract and re-plan to "
                "attempt it again, then resolve this step."
                % (claimed["unit_id"],
                   ", ".join(self._executed) or "no command at all", reason),
                "", [], self.session_id, self.actor)
            if summary is not None:
                self._set_reason(summary, stop_reason, note)
        return "rejected"

    def _close_without_running(self, project_id, run_id, claimed, unit,
                               contract_state, summary=None, refusal=None):
        """A result that arrived under a contract that is not live, closed
        with ZERO commands executed (REFUTATION-4 AZ F5). Two answers,
        because the two conditions are not the same thing:

        PAUSED is REVERSIBLE, so the answer is HELD: nothing is recorded
        against the dispatch, no retry is burned, the fence stays where it
        is, and the run resumes judging it the moment the founder clears
        the contract. That is design 4.3's hold, reached from the one route
        that actually pauses a shipped run (AZ F4).

        STOPPED, REVOKED or GONE is not reversible by the founder pressing
        one key, and the authorisation this dispatch was stamped under no
        longer exists. The result is already recorded (it is real, and
        at-most-once recording still holds), so what is left is to say so
        durably and stop: a rejected verification carrying the reason, the
        unit returned through the circuit breaker, the fence PARKED rather
        than left active over the founder's files, and a founder step
        naming the unit and the dead contract, because a founder who pulled
        the kill switch mid-unit is owed the sentence "u1 answered after
        you stopped, and nothing was run".

        A THIRD ANSWER JOINED THEM IN ROUND 6 (REFUTATION-5 S2): the
        contract can be perfectly live and the FOUNDER'S OTHER BRAKE, the
        spend ceiling, can be what refuses. `refusal` carries the verdict
        _authorisation_refusal returned, so the sentence the founder reads
        names the brake that actually stopped the work rather than the one
        that did not. A breaker refusal is treated like the dead-contract
        branch, not like the pause: `step` already drains a run whose
        breaker has tripped, so holding an answer for a run that is about
        to drain would be a hold nothing ever collects.

        The verification text keeps the word "stale": the authorisation the
        dispatch carried is exactly that."""
        unit_id = claimed["unit_id"]
        verdict = (refusal or {}).get("verdict")
        if contract_state == "paused" and verdict != "REFUSED-BREAKER":
            if summary is not None:
                self._set_reason(summary, "CONTRACT_PAUSED",
                                 _NOTE_CONTRACT_PAUSED)
            return "held"
        state_word = contract_state or "missing"
        # DERIVED from what this call executed, never from which branch is
        # writing it (REFUTATION-6 F2): this method is reached from the
        # verifier's own refusal too, where the done_check HAS run.
        nothing_ran = ("so the result was recorded and rejected and %s"
                       % (_nothing_executed_clause(self._executed),))
        if verdict == "REFUSED-BREAKER":
            reason = ("the founder's spend ceiling has tripped (%s), so the "
                      "authorisation this dispatch was stamped under "
                      "(revision %s) permits nothing further, %s"
                      % (refusal["reason"],
                         claimed.get("contract_revision"), nothing_ran))
            stop_reason = "SPEND_STOP"
            note = _spend_breaker_note(refusal["reason"], self._executed)
            step_cause = ("the founder's spend ceiling had tripped (%s)"
                          % (refusal["reason"],))
            fence_note = ("the spend breaker has tripped; nothing was "
                          "judged and %s"
                          % (_nothing_executed_clause(self._executed),))
        elif verdict == _REFUSED_SCOPE_SHAPE:
            # REFUTATION-6 F4. Not "the contract refuses this scope": the
            # scope is not a shape this engine can read AS a scope, so no
            # path in it was ever judged and no rollback can be composed
            # for it either. Saying the contract refused it would send a
            # founder to widen a contract that is not the problem.
            reason = ("this unit's write_scope cannot be read as a list of "
                      "paths (%s), so no path in it could be judged against "
                      "the contract, %s" % (refusal["reason"], nothing_ran))
            stop_reason = "FOUNDER_WAITING"
            note = ("nothing was judged and nothing was accepted: unit %s's "
                    "write_scope is not a list of paths (%s). Re-plan the "
                    "unit with a list of plain relative paths inside the "
                    "project" % (unit_id, refusal["reason"]))
            step_cause = ("its write_scope could not be read as a list of "
                          "paths (%s)" % (refusal["reason"],))
            fence_note = ("this unit's write_scope is not a list of paths, "
                          "so nothing was judged and nothing was run")
        elif contract_state == "live" and refusal is not None:
            reason = ("the live contract no longer authorises this unit's "
                      "write scope (%s), %s" % (refusal["reason"],
                                                nothing_ran))
            stop_reason = "FOUNDER_WAITING"
            note = ("the done_check was not run and nothing was accepted: "
                    "the live contract no longer authorises this unit's "
                    "write scope (%s). Widen the contract or re-plan the "
                    "unit" % (refusal["reason"],))
            step_cause = ("the live contract no longer authorised its write "
                          "scope (%s)" % (refusal["reason"],))
            fence_note = ("the contract no longer authorises this unit's "
                          "write scope")
        else:
            reason = (
                "the contract is %s, not live: the authorisation this "
                "dispatch was stamped under (revision %s) is stale, %s"
                % (state_word, claimed.get("contract_revision"),
                   nothing_ran))
            stop_reason = "CONTRACT_NOT_LIVE"
            note = _contract_not_live_note(state_word, self._executed)
            step_cause = "the contract was %s" % (state_word,)
            fence_note = ("the contract is %s; nothing was judged and %s"
                          % (state_word,
                             _nothing_executed_clause(self._executed)))
        self.store.record_verification(
            claimed["dispatch_id"], None, reason, False, self.actor,
            project_id=project_id)
        self.store.mark_unit_failed(unit_id, self.actor, reason,
                                    project_id=project_id)
        self._release_fence(claimed["fence_uuid"], "parked", fence_note)
        self.store.queue_human_step(
            project_id, "", (unit or {}).get("lane") or "default",
            "unit %s returned a result and %s. %s The result is "
            "recorded and rejected and the fence is parked. Sign a fresh "
            "contract and re-plan to attempt it again, then resolve this "
            "step." % (unit_id, step_cause,
                       _step_execution_clause(self._executed)),
            "", [], self.session_id, self.actor)
        # NO reconcile here, deliberately, unlike _handle_late_result: the
        # founder step in the unit's own lane IS the mechanism
        # (select_ready_units' blocked-lane query makes the lane
        # unselectable on its own), and BLOCKED is a VIEW of it that the
        # next wave derives (law L4). Writing the view here would make a
        # unit BLOCKED on a run whose very next step drains it, which is a
        # status nobody reverses.
        if summary is not None:
            self._set_reason(summary, stop_reason, note)
        return "rejected"

    def _reject(self, claimed, project_id, run_id, reason, charged=None):
        """Step 15's rejection branch and step 17 (the circuit breaker):
        mark_unit_failed decides retry-or-escalate on its own
        (retry_ceiling), and a partial write in the unit's write_scope is
        rolled back via the CheckRunner. If the rollback ITSELF fails,
        the run goes FAILED_TERMINAL with a queued founder step
        describing the dirty state (fault 10), and no further unit is
        dispatched from this run.

        ORDER IS LOAD-BEARING on the dirty-rollback branch (R-L03-
        refutation-2 F1): the founder warning and the fence release are
        written BEFORE the FAILED_TERMINAL move is attempted, never after.
        A dirty write scope is the single most important thing this engine
        ever has to tell a founder, and writing it after a state move
        meant one refused move destroyed it. The move itself is now also
        attempted only when CONTROLLER_STATE_TRANSITIONS allows it from
        wherever the run actually stands; when it does not, the halt is
        recorded as an interruption instead, because a caller that already
        walked the run (every caller does) can only get here from a
        state that allows it, and guessing is not a thing to do with a
        founder's run."""
        unit_id = claimed["unit_id"]
        outcome = self.store.mark_unit_failed(unit_id, self.actor, reason,
                                              project_id=project_id)
        rollback_cmd, rb_refusal = self._rollback_plan(run_id, unit_id)
        if rb_refusal is not None:
            unit = self._unit_row(run_id, unit_id)
            self._warn_unrollbackable_scope(
                project_id, rb_refusal,
                unit["lane"] if unit else "default")
        elif rollback_cmd is not None:
            # Gated like every other command (REFUTATION-4 AZ F5): None
            # means the authorisation does not allow it and NOTHING ran,
            # which is not a failed rollback and must not be reported as
            # one.
            rb = self._run_command(project_id, rollback_cmd,
                                   self._unit_row(run_id, unit_id), charged,
                                   label="the unit's rollback (git restore)")
            if rb is not None and rb["exit_code"] != 0:
                unit = self._unit_row(run_id, unit_id)
                self._warn_dirty_write_scope(
                    project_id, unit_id, rb["exit_code"],
                    unit["lane"] if unit else "default")
                self._release_fence(claimed["fence_uuid"], "parked",
                                   "rollback failed")
                cur = self.store.get_run(project_id, raw=True)["state"]
                if "FAILED_TERMINAL" in bs.CONTROLLER_STATE_TRANSITIONS.get(
                        cur, ()):
                    self.store.set_run_state(
                        run_id, "FAILED_TERMINAL", self.actor,
                        "rollback failed for unit %s" % unit_id,
                        self.session_id, project_id=project_id)
                else:
                    self._record_interruption(
                        project_id, "contradiction",
                        "the rollback for unit %s failed while the run was "
                        "%s, a state FAILED_TERMINAL is not reachable from; "
                        "the founder step above stands and the run was left "
                        "where it was" % (unit_id, cur))
                return
        if outcome["status"] == "FAILED":
            self._record_interruption(
                project_id, "contradiction",
                "unit %s failed twice (%s); escalating rather than "
                "retrying a third identical attempt" % (unit_id, reason))
        self._release_fence(claimed["fence_uuid"], "parked",
                           "unit rejected: %s" % reason)

    def _warn_dirty_write_scope(self, project_id, unit_id, exit_code, lane):
        """Fault 10's founder warning, in ONE place so the synchronous
        rejection path, the staleness path and the late-result path cannot
        drift apart in what they tell the founder about the same condition.

        `lane` is the UNIT'S OWN lane, passed by every caller, never the
        hard-coded "default" this used to write (REFUTATION-3 SM
        observation 4): queue_human_step gates a LANE through
        select_ready_units' blocked-lane query, so a dirty rollback for a
        unit in lane "build" used to make lane "default" unselectable
        project wide while leaving "build" itself running."""
        return self.store.queue_human_step(
            project_id, "", lane,
            "the rollback command for unit %s failed (exit %s); its "
            "write_scope may be left dirty and needs manual inspection "
            "before any further work in that scope"
            % (unit_id, exit_code), "", [], self.session_id, self.actor)

    def _record_interruption(self, project_id, condition, question):
        """record_interruption REFUSES on a project with no LIVE contract
        ("a question nobody is running to answer", Store's own words), and
        every rejection path can legitimately be running under a revoked
        or stopped one: the founder revokes mid-unit, and the result then
        arrives. Skipping the interruption there is the honest degrade,
        because the rejection, the founder step and the fence release are
        already durable by that point and none of them may be lost to a
        refusal raised by the LAST bookkeeping call on the path. Returns
        the interruption id, or None when it was skipped."""
        contract = self.store.latest_contract(project_id, raw=True)
        if contract is None or contract["state"] != "live":
            return None
        return self.store.record_interruption(
            project_id, condition, question, self.session_id, self.actor)

    def _release_fence(self, fence_uuid, to_state, note, evidence=""):
        """transition() needs the fence's CURRENT version (optimistic
        concurrency); read it fresh rather than trust anything cached on
        `claimed`, since the fence may have been re-read or re-claimed
        since this engine last touched it (a resumed engine holds no
        cross-call state at all, see the class docstring).

        session_id passed to transition() is the FENCE'S OWN current
        session_id, read back from the record itself, NEVER self.session_id:
        a resumed engine (a fresh ControllerEngine instance, per the class
        docstring) is very likely constructed with a DIFFERENT session_id
        than whichever engine originally claimed this fence, and
        transition()'s ownership guard refuses 'not-owner' the instant the
        two disagree (store.transition's own not-owner refusal). Releasing
        with the record's own session_id, which by definition always
        matches, is what makes fence release idempotent and resumable
        across a crash without requiring every caller to thread a stable
        session_id through by hand. This is NOT cross-session adoption
        (design section 8): it never displaces a DIFFERENT session's still
        -active claim, it releases a fence THIS controller's own prior
        engine instance already owned.

        `evidence` is REQUIRED, non-empty, by transition() itself when
        to_state is 'complete' ("active -> complete requires non-empty
        evidence, the check_cmd result"); callers releasing into
        'complete' always pass one, releasing into 'parked' never needs
        to."""
        record = self.store.get(fence_uuid)
        if record is None or record.state != "active":
            return  # already released; idempotent no-op (orphan recovery)
        self.store.transition(
            fence_uuid, record.version, to_state,
            session_id=record.session_id, note=note,
            evidence=evidence or note)

    def _rollback_plan(self, run_id, unit_id):
        """(command, refusal) for ONE unit's rollback.

          command  the `git restore` this engine will run, or None when
                   there is nothing to roll back or nothing safe to
                   compose
          refusal  None, or a founder-facing phrase naming the entry that
                   stopped a rollback from being composed at all

        REFUTATION-5 S1 (PUSH BLOCKER). `shlex.quote` protects the SHELL
        and does nothing about GIT: `git restore -- ':/'` is a perfectly
        quoted command that restores the WHOLE working tree and exits 0,
        so every uncommitted founder edit in the project was destroyed and
        the zero exit meant no dirty-write-scope warning was ever queued.
        Two independent defences, either of which is enough:

          1. every emitted path goes through _pathspec_literal, so a magic
             spelling reaches git as a file name (and, being a file name
             nothing matches, exits 1, which IS the warning path);
          2. a scope this engine may not act on composes no command at
             all, and says so, so the founder learns the write scope was
             left exactly as the worker left it instead of being told
             nothing.

        The command emitted for an ordinary plain-path unit in a project
        that is not itself a git repository root is byte for byte the one
        this engine has always composed.

        A THIRD defence joined the two above (CROSS-FAMILY REFUTATION
        finding 3, HIGH, DATA DESTRUCTION), and it guards a different
        thing: not WHICH PATHS the command names, but WHICH REPOSITORY it
        acts on. git honours GIT_DIR and GIT_WORK_TREE over cwd, so this
        command, run from an environment carrying either, restored files
        in an unrelated repository and exited 0. _sanitised_env strips
        that whole class at the one subprocess site; _git_prefix makes
        this command name its own repository as well, so the two defences
        do not share a single point of failure."""
        unit = self._unit_row(run_id, unit_id)
        write_scope = unit["write_scope"] if unit else []
        if not write_scope:
            return None, None
        problem = self._unsafe_write_scope(unit)
        if problem is not None:
            return None, ("unit %s was NOT rolled back: %s"
                          % (unit_id, problem))
        # The SAME quoting rule the prefix above uses, and for the same
        # reason: shlex is POSIX-only, so a write-scope entry carrying a
        # space or a cmd.exe metacharacter would come back single-quoted and
        # cmd.exe would read the quote as part of the filename, leaving the
        # entry unrestored on Windows. Byte-identical to shlex.quote on
        # POSIX, where the helper IS shlex.quote, so the green legs cannot
        # move. Fixing the prefix and leaving this half would have made ONE
        # command half-correct on one platform, which is worse than either
        # end of the choice.
        return _git_prefix(self.store.root) + "restore -- " + " ".join(
            bs._quote_path_for_local_shell(_pathspec_literal(p))
            for p in write_scope), None

    def _warn_unrollbackable_scope(self, project_id, refusal, lane):
        """The founder step for a rollback that was never composed. A
        sibling of _warn_dirty_write_scope, and separate from it on
        purpose: "the rollback failed" and "there was no rollback to run"
        are different facts, and rounds 3 and 5 both landed a defect that
        was one note constant describing two branches."""
        return self.store.queue_human_step(
            project_id, "", lane or "default",
            "%s, so whatever it wrote is still there and needs manual "
            "inspection before any further work in that scope."
            % (refusal,), "", [], self.session_id, self.actor)

    # -- internal: run-level bookkeeping ----------------------------------

    def _walk_to_executing(self, project_id, run_id):
        """CONTROLLER_STATE_TRANSITIONS makes EXECUTING legal from exactly
        one state, READY (tools/bm_store.py's own table). A caller about
        to dispatch (the normal step() path) or resume in-flight work
        (RESULT_IN or DISPATCHED) can find the run in READY, CHECKPOINTED
        (READY -> EXECUTING is legal, but CHECKPOINTED -> EXECUTING is
        NOT, so CHECKPOINTED must detour through READY first),
        WAITING_HUMAN (same detour), or FAILED_RECOVERABLE (same detour
        again, added 2026-08-05 for REFUTATION-3 SM I: that state was in
        _RESULT_WALKABLE_STATES but no edge carried it, so a result
        arriving on it was judged from a run that never reached VERIFYING;
        the engine already takes FAILED_RECOVERABLE to READY at the top of
        every step, so this is the walk catching up with itself). Called
        defensively before ANY code path that is about to act on a
        dispatch, so _settle_after_wave's own forward walk (EXECUTING ->
        VERIFYING -> CHECKPOINTED) always has EXECUTING to start from, and
        a rejection's FAILED_TERMINAL move (legal only from VERIFYING or
        FAILED_RECOVERABLE) is never attempted from a state that cannot
        reach it."""
        run = self.store.get_run(project_id, raw=True)
        state = run["state"]
        if state in ("CHECKPOINTED", "WAITING_HUMAN", "FAILED_RECOVERABLE"):
            self.store.set_run_state(run_id, "READY", self.actor,
                                     "converging before dispatch",
                                     self.session_id, project_id=project_id)
            state = "READY"
        if state == "READY":
            self.store.set_run_state(run_id, "EXECUTING", self.actor,
                                     "dispatching a wave", self.session_id,
                                     project_id=project_id)

    def _ensure_verifying(self, project_id, run_id):
        """EXECUTING -> VERIFYING, committed the moment a result is
        recorded, BEFORE any verification runs (design step 10, and the
        precondition a rejection's eventual FAILED_TERMINAL move needs:
        that move is legal only from VERIFYING or FAILED_RECOVERABLE,
        never from EXECUTING directly). Idempotent: a no-op once the run
        is already past EXECUTING."""
        run = self.store.get_run(project_id, raw=True)
        if run["state"] == "EXECUTING":
            self.store.set_run_state(run_id, "VERIFYING", self.actor,
                                     "result recorded", self.session_id,
                                     project_id=project_id)

    def _settle_after_wave(self, project_id, run_id, summary):
        """After at least one unit in a wave resolved (green or
        rejected), walk the run state forward from EXECUTING through
        VERIFYING and CHECKPOINTED (design's own named states for
        exactly this: a result was recorded, then judged) and decide
        what is next: more selectable work -> READY; only blocked lanes
        remain -> WAITING_HUMAN; nothing left -> attempt delivery.

        `summary` IS THE CALLER'S OWN, and is now required (REFUTATION-4 LV
        L4-F1). This method used to end with
        `self._handle_no_ready_units(project_id, run_id, {"note": ""})`, a
        dict nobody read, and _handle_no_ready_units is the only caller of
        _deliver_or_hold and the only place summary['founder_gated'] is
        ever written. So on EVERY wave that recorded a result, which is
        every synchronous worker wave and every `bm-controller
        record-result`, three load-bearing things were computed and dropped:
        the stop_reason law L3 says control flow reads, the note the
        founder reads, and the block naming the run's remaining FAILED
        units and open founder steps. `bm-controller start` on a delivered
        run with a FAILED unit never mentioned it, and no later step could
        recover it, because _deliver_or_hold's already-delivered early
        return re-sets the reason and the note but never re-attaches
        founder_gated."""
        run = self.store.get_run(project_id, raw=True)
        if run["state"] == "EXECUTING":
            self.store.set_run_state(run_id, "VERIFYING", self.actor,
                                     "results judged", self.session_id,
                                     project_id=project_id)
        run = self.store.get_run(project_id, raw=True)
        if run["state"] == "VERIFYING":
            self.store.set_run_state(run_id, "CHECKPOINTED", self.actor,
                                     "checkpointed", self.session_id,
                                     project_id=project_id)
        run = self.store.get_run(project_id, raw=True)
        if run["state"] != "CHECKPOINTED":
            return  # PAUSED/STOPPING/FAILED_* already claimed this run
        refusal = self._authorisation_refusal(project_id)
        if refusal is not None:
            # Nothing to settle TOWARD: delivery is refused under a dead,
            # suspended or over-spent authorisation, and step 2 or step 3
            # drains or pauses the run on the next call. Saying WHICH of
            # the three it is beats overwriting it with a founder-gating
            # verdict computed under an authorisation that no longer
            # applies (REFUTATION-4 AZ F5, REFUTATION-5 S2).
            # NOT _contract_not_live_note (REFUTATION-5 S3, second half):
            # that sentence is a statement about a DIFFERENT branch. By the
            # time a wave settles, this engine may have run a done_check
            # that was authorised when it started, which is why the settle
            # path has its own third-branch sentence and why the breaker
            # sentence _refusal_stop builds reads the ledger
            # (REFUTATION-6 F2).
            self._set_reason(summary, *self._refusal_stop(
                project_id, refusal, _NOTE_SETTLE_CONTRACT_NOT_LIVE))
            return
        ready = self.store.select_ready_units(run_id)
        if ready:
            self.store.set_run_state(run_id, "READY", self.actor,
                                     "more work is selectable",
                                     self.session_id, project_id=project_id)
            return
        self._handle_no_ready_units(project_id, run_id, summary)

    def _reconcile_lane_blocks(self, project_id, run_id, units=None):
        """BLOCKED means "this lane holds an open founder step", and
        nothing else (law L4). Reconciles BOTH directions once per wave: a
        lane that gained a step gets its PENDING/READY units BLOCKED; a
        lane whose last step was resolved gets its BLOCKED units returned
        to PENDING or READY by dependency satisfaction.

        This is the FIRST production caller of Store.unblock_lane_units,
        whose complete absence is what made every BLOCKED status round 3
        wrote permanent (REFUTATION-3 SM F and LV 2): resolving the founder
        step did not make the lane selectable again, a healthy unit that
        merely shared the lane was lost as collateral, and a run could rest
        in WAITING_HUMAN reporting "only founder-gated lanes remain" with
        ZERO open founder steps and nothing a founder could resolve.

        Making BLOCKED a VIEW of that one fact rather than an independent
        fact is what removes the one-way door. It is also what
        block_lane_units' own docstring already says it is for ("paired
        with queue_human_step"), and what select_ready_units already
        enforces independently through its blocked-lane query, so this
        writes down a rule the store was enforcing anyway.

        A lane is only written to when the write would CHANGE something,
        so a quiet wave writes no attribution rows."""
        gated = {s["lane"] for s in self.store.list_human_steps(
            project_id, resolved=False, raw=True) if s["lane"]}
        if units is None:
            units = self.store.list_units(run_id, raw=True)
        by_lane = {}
        for unit in units:
            by_lane.setdefault(unit["lane"], set()).add(unit["status"])
        for lane in sorted(by_lane):
            statuses = by_lane[lane]
            if lane in gated:
                if statuses & {"PENDING", "READY"}:
                    self.store.block_lane_units(run_id, lane, self.actor,
                                                project_id=project_id)
            elif "BLOCKED" in statuses:
                self.store.unblock_lane_units(run_id, lane, self.actor,
                                              project_id=project_id)

    def _handle_no_ready_units(self, project_id, run_id, summary):
        """Design steps 4 and 19: nothing is currently selectable. In
        order: unwind an empty wave; report work that is genuinely in
        flight; deliver when every unit is terminal; escalate a remainder
        that can NEVER become selectable; then judge whether what is left
        is founder-gated.

        THE IN-FLIGHT CHECK READS DISPATCH ROWS AND COMES FIRST
        (REFUTATION-3 LV 4). Round 3 judged by unit STATUS, and a unit a
        re-plan had marked SKIPPED was filtered out of `non_terminal` while
        its dispatch stayed open, so the run reported "only founder-gated
        lanes remain" with a worker still mid-unit, which is exactly the
        statement _is_founder_waiting's own docstring says it will never
        make.

        THE ESCALATION NOW RUNS BEFORE THE FOUNDER-WAITING JUDGEMENT, not
        after it (LV 2). Round 3 judged first, so a remainder that was
        already BLOCKED reported founder-gating forever, including after
        every human step had been resolved, and no fresh step was ever
        queued to replace them. The one-shot `escalate_unreachable`
        re-entry parameter is gone with it: nothing re-enters, because the
        escalation writes no unit status this method then has to re-judge.

        THE WHOLE-RUN JUDGEMENT IS BY SELECTABILITY, NOT BY STATUS ALONE
        (R-L03-refutation-2 F4). Asking only whether every non-terminal
        unit is BLOCKED misses the other half of the design's own gating
        pair: queue_human_step blocks a LANE, and select_ready_units skips
        that whole lane, so a unit sitting in a founder-gated lane is just
        as unselectable while keeping status READY."""
        # An empty wave that walked the run to EXECUTING and claimed
        # nothing has nothing left to verify, and EXECUTING can reach
        # neither WAITING_HUMAN nor DELIVERABLE_READY. Idempotent: returns
        # False unless the run is EXECUTING with nothing in flight.
        self._unwind_empty_wave(project_id, run_id)

        open_units = self._open_dispatch_units(run_id)
        if open_units:
            self._set_reason(summary, "IN_FLIGHT",
                             _NOTE_IN_FLIGHT % len(open_units))
            return

        units = self.store.list_units(run_id, raw=True)
        non_terminal = [u for u in units
                        if u["status"] not in ("DONE", "FAILED", "SKIPPED")]
        if not non_terminal:
            self._deliver_or_hold(project_id, run_id, summary)
            return

        unreachable = self._unreachable_units(units)
        gated_lanes = {s["lane"] for s in self.store.list_human_steps(
            project_id, resolved=False, raw=True) if s["lane"]}
        if self._escalate_unreachable_units(project_id, run_id, units,
                                            unreachable, gated_lanes):
            self._reconcile_lane_blocks(project_id, run_id)
            units = self.store.list_units(run_id, raw=True)
            non_terminal = [
                u for u in units
                if u["status"] not in ("DONE", "FAILED", "SKIPPED")]
            unreachable = self._unreachable_units(units)
            gated_lanes = {s["lane"] for s in self.store.list_human_steps(
                project_id, resolved=False, raw=True) if s["lane"]}

        gated_units = self._founder_gated_units(units, gated_lanes,
                                                unreachable)
        if all(u["unit_id"] in gated_units for u in non_terminal):
            cur = self.store.get_run(project_id, raw=True)["state"]
            if cur in ("READY", "CHECKPOINTED"):
                self.store.set_run_state(
                    run_id, "WAITING_HUMAN", self.actor,
                    "only founder-gated lanes remain", self.session_id,
                    project_id=project_id)
            # The move above is now ALWAYS legal when it is wanted, because
            # the unwind above guarantees this method runs from READY,
            # CHECKPOINTED or WAITING_HUMAN. _finish still re-reads the
            # state, so the summary and the store agree in FACT and not
            # merely by re-reading (SM E and LV 3 closed twice over).
            self._set_reason(
                summary, "FOUNDER_WAITING",
                self._founder_gated_note(project_id, gated_units))
            return
        self._set_reason(summary, "NOTHING_SELECTABLE",
                         _NOTE_NOTHING_SELECTABLE)

    def _founder_gated_units(self, units, gated_lanes, unreachable):
        """unit_id -> the LANE whose open founder step is what actually
        blocks it (or None when the block is a dead dependency rather than
        a lane), for every unit that cannot move without a founder.

        THE TRANSITIVE HALF IS THE FIX (REFUTATION-4 LV L4-F3).
        _is_founder_waiting asks only about the unit ITSELF: is it BLOCKED,
        unreachable, or selectable-shaped in a gated lane. A run in which
        every path forward runs through one gated UPSTREAM lane therefore
        fell through to NOTHING_SELECTABLE, the one enum value whose
        documented meaning is "nothing selectable, nothing in flight,
        NOTHING FOUNDER-GATED ... inspect the graph", and the WAITING_HUMAN
        store move was skipped with it, so `bm-controller status` reported
        READY for a run that one `resolve` unwedges. The prose was honest
        and the enum was wrong, which is the exact inversion of law L3.

        A dependent inherits its blocker rather than getting one of its
        own, so the note can name the lane a founder must actually act in,
        not the lane the stalled unit happens to sit in.

        PURE: no store read, no store write. Recomputed every wave from the
        graph (law L4), never remembered."""
        blocked = {}
        for unit in units:
            if self._is_founder_waiting(unit, gated_lanes, unreachable):
                blocked[unit["unit_id"]] = (
                    unit["lane"] if unit["lane"] in gated_lanes else None)
        changed = True
        while changed:
            changed = False
            for unit in units:
                unit_id = unit["unit_id"]
                if (unit_id in blocked
                        or unit["status"] not in ("PENDING", "READY",
                                                  "BLOCKED")):
                    continue
                for dep in unit["dependencies"]:
                    if dep in blocked:
                        blocked[unit_id] = blocked[dep]
                        changed = True
                        break
        return blocked

    def _founder_gated_note(self, project_id, gated_units):
        """The founder-facing half of the same finding: name the step that
        actually blocks the run, so a founder reading "only founder-gated
        lanes remain" about a unit whose own lane is ungated is not left
        hunting for it."""
        base = "only founder-gated lanes remain"
        lanes = sorted({lane for lane in gated_units.values() if lane})
        if not lanes:
            return base
        steps = [s for s in self.store.list_human_steps(
                     project_id, resolved=False, raw=True)
                 if s["lane"] in lanes]
        return ("%s; what blocks this run is %d open founder step(s) in "
                "lane(s) %s: %s. Resolve them (or re-plan around them) and "
                "step again."
                % (base, len(steps), ", ".join(lanes),
                   "; ".join((s["what"] or "").strip()[:70] for s in steps)
                   or "(no step text)"))

    def _is_founder_waiting(self, unit, gated_lanes, unreachable):
        """True when this unit cannot move without a founder: it is
        BLOCKED, it is UNREACHABLE (its dependency chain ends in a dead
        unit, which no controller action can move), or it is
        selectable-shaped (PENDING/READY) and sitting in a lane an open
        human step holds. A unit whose work is IN FLIGHT is never
        founder-waiting no matter which lane it is in: its result may still
        arrive and change what is selectable, and calling the run
        founder-gated while a worker is mid-unit would be a false statement
        about the run. That half of the predicate is preserved by
        _handle_no_ready_units' own IN_FLIGHT early return, which consults
        the DISPATCH rows before any unit status is read."""
        if unit["status"] == "BLOCKED":
            return True
        if unit["unit_id"] in unreachable:
            return True
        return (unit["status"] in ("PENDING", "READY")
                and unit["lane"] in gated_lanes)

    def _unreachable_units(self, units):
        """unit_id -> (the dependency it waits on, the DEAD unit at the end
        of that chain), for every PENDING, READY or BLOCKED unit whose
        dependency chain ends in a unit that is FAILED or SKIPPED. The two
        entries differ for a unit blocked only TRANSITIVELY, and the
        founder step says so rather than calling an unattempted middle unit
        dead.

        PURE: no store write, no status write. Unreachability is RECOMPUTED
        from the graph every time it is asked (law L4), never remembered,
        which is what makes reviving a dead dependency remove it without
        any reversal step of its own.

        BLOCKED units are candidates too (REFUTATION-3 SM F): round 3
        itself became the main producer of BLOCKED units and then did not
        recognise one as a dead dependency, so a run whose only blocker was
        a BLOCKED unit had no escalation naming it at all."""
        status_by_id = {u["unit_id"]: u["status"] for u in units}
        blocked_by = {}
        changed = True
        while changed:
            changed = False
            for unit in units:
                if (unit["status"] not in ("PENDING", "READY", "BLOCKED")
                        or unit["unit_id"] in blocked_by):
                    continue
                for dep in unit["dependencies"]:
                    if status_by_id.get(dep) in _DEAD_DEPENDENCY_STATUSES:
                        blocked_by[unit["unit_id"]] = (dep, dep)
                        changed = True
                        break
                    if dep in blocked_by:
                        blocked_by[unit["unit_id"]] = (
                            dep, blocked_by[dep][1])
                        changed = True
                        break
        return blocked_by

    def _escalate_unreachable_units(self, project_id, run_id, units,
                                    unreachable, gated_lanes):
        """Queue ONE founder step per unreachable unit whose lane was not
        ALREADY gated when this wave began, and mark those lanes gated so
        no LATER wave queues a second round. Returns True when it queued
        anything. Writes NO unit status: _reconcile_lane_blocks is what
        marks the newly gated lane BLOCKED, as a view.

        THE IDEMPOTENCY MARKER IS AN OPEN FOUNDER STEP IN THE UNIT'S OWN
        LANE, which is state-derived, needs no new column, and self-heals
        in both directions:

          * first wave: unreachable, lane ungated, one step per unreachable
            unit, the lane becomes gated, the reconcile marks it BLOCKED;
          * every later wave: the lane is gated, so nothing is queued. No
            spam, whatever drives step();
          * founder resolves without repairing: the lane ungates, the
            reconcile unblocks it, the unit is unreachable again and its
            lane ungated again, so ONE fresh round is queued. That is not
            spam, it is the condition still being true, restated once per
            founder action. LV 2's dead end (WAITING_HUMAN with ZERO open
            steps and nothing to resolve) is exactly this hole;
          * founder repairs and resolves: the lane ungates, the reconcile
            unblocks it including any healthy collateral, the unit is no
            longer unreachable, nothing is queued, and select_ready_units
            returns it.

        DEVIATION FROM DESIGN-round4 section 8.5, stated rather than
        silent: that section gates within the LOOP ("add that lane to
        gated_lanes so a second unit in the same lane does not queue a
        second step"), which would collapse two unreachable units sharing
        one lane into one step. A refutation-born assertion the design does
        not authorise changing (tools/test_bm_controller.py's
        test_a_transitively_blocked_unit_is_told_which_unit_actually_failed)
        requires each unreachable unit to get its OWN step naming its OWN
        chain, and that step text is the founder-facing value here: "unit
        u3 can never run: it depends on unit u2, and that chain ends at
        unit u1". So the gate is applied at WAVE granularity instead, which
        preserves the anti-spam property in full (the marker is still
        state-derived and still checked at the start of every wave) while
        keeping the per-unit explanation."""
        status_by_id = {u["unit_id"]: u["status"] for u in units}
        newly_gated, queued = set(), False
        for unit in units:
            pair = unreachable.get(unit["unit_id"])
            if pair is None or unit["lane"] in gated_lanes:
                continue
            dep, dead = pair
            # The founder is told the dead unit's STATUS as well as its id:
            # "repair the unit that failed" and "restore the unit your
            # re-plan dropped" are different actions, and the escalation is
            # useless if it does not say which one this is.
            dead_note = "%s, which is %s (%s)" % (
                dead, status_by_id.get(dead),
                "its retry ceiling is exhausted"
                if status_by_id.get(dead) == "FAILED"
                else "a re-plan dropped it from the unit graph")
            chain = ("it depends on unit %s" % dead_note if dep == dead else
                     "it depends on unit %s, and that chain ends at unit %s"
                     % (dep, dead_note))
            self.store.queue_human_step(
                project_id, "", unit["lane"],
                "unit %s can never run: %s. Re-plan or repair unit %s and "
                "then resolve this step, or stop the run; the controller "
                "will not start %s while this step is open."
                % (unit["unit_id"], chain, dead, unit["unit_id"]),
                "", [], self.session_id, self.actor)
            newly_gated.add(unit["lane"])
            queued = True
        gated_lanes |= newly_gated
        return queued

    def _deliver_or_hold(self, project_id, run_id, summary):
        """Design step 19's deliver branch: run the WHOLE done_definition
        once more via CheckRunner, independently of any per-unit green (a
        final edit can invalidate prior evidence, fault 3), AFTER the
        last edit. On pass: record_checkpoint(kind='deliverable-ready')
        then DELIVERABLE_READY, naming exactly what remains founder-
        gated (open human steps, failed units). On failure: the run
        stays in place for inspection rather than lying about being
        ready.

        THE SOURCE TEST IS OWNERSHIP OF THE EDGE, NOT LEGALITY
        (REFUTATION-3 SM B). Round 3 asked only whether READY was a LEGAL
        move from wherever the run stood and then took it. PAUSED to READY
        is legal, and it is precisely the founder-only edge
        `bm-controller resume` exists to perform, so the engine reversed a
        founder's pause and declared the deliverable ready, after which
        `bm-controller complete` worked. DELIVERABLE_READY to READY is
        legal too, so a delivered run was walked backwards and forwards
        again on every later step, re-running the whole done_definition and
        writing two extra state rows each time. _DELIVERY_SOURCE_STATES is
        derived from this engine's OWN edges and contains neither.

        summary['state'] is NOT written here: _finish reads it back from
        the store, so the summary can never claim a delivery the store does
        not hold."""
        cur = self.store.get_run(project_id, raw=True)["state"]
        if cur == "DELIVERABLE_READY":
            # Already delivered. Writing NOTHING is the point: the founder
            # acts next (`bm-controller complete`), and re-running their
            # whole done_definition once per step is a real cost with the
            # production SubprocessCheckRunner.
            self._set_reason(summary, "DELIVERED", "deliverable ready")
            return
        refusal = self._authorisation_refusal(project_id)
        if refusal is not None:
            # The founder's own done-definition is a command like any
            # other, and a deliverable declared ready under an
            # authorisation that is stopped, revoked, paused or over its
            # ceiling would be a claim nothing stands behind (REFUTATION-4
            # AZ F5, REFUTATION-5 S2: this line, `_deliver_or_hold`'s call
            # to the founder's WHOLE test suite, is the most expensive
            # command in the system and was the one the spend breaker left
            # ungated). Nothing is written and nothing is run.
            self._set_reason(summary, *self._refusal_stop(
                project_id, refusal,
                "the done-definition was not run and nothing was delivered: "
                "the contract is %s, not live"))
            return
        # -- REFUTATION-5 S7. A run whose meter never saw an accepted
        # unit's cost cannot be declared a deliverable. See
        # _unreconciled_uncharged_spend for why this is the half of the
        # choice that is implementable from this file.
        outstanding = self._unreconciled_uncharged_spend(project_id)
        if outstanding:
            self._set_reason(
                summary, "SPEND_STOP",
                _NOTE_SPEND_UNRECONCILED % (len(outstanding), project_id))
            return
        if cur not in _DELIVERY_SOURCE_STATES:
            self._set_reason(
                summary, "FOUNDER_WAITING",
                "the done-definition was not run: the run is %s, which only "
                "a founder can move (bm-controller resume, complete or "
                "stop it)" % cur)
            return
        run = self.store.get_run(project_id, raw=True)
        done_definition = run["done_definition"]
        if done_definition:
            # unit=None on purpose: the founder's own done-definition
            # belongs to no unit, so there is no write scope to judge it
            # against, and it is not a model action bounded by the risk
            # classes the founder granted the model. State and breaker are
            # asked, and they are the two that apply.
            outcome = self._run_command(
                project_id, done_definition,
                label="the founder's whole done-definition")
            if outcome is None:
                self._set_reason(
                    summary, "CONTRACT_NOT_LIVE",
                    "the done-definition was not run and nothing was "
                    "delivered: the contract stopped being live while this "
                    "wave was settling")
                return
            # -- THE LAST WINDOW (REFUTATION-6 F1). The founder's whole
            # test suite is the longest-running command in the system, and
            # round 6 wrote the deliverable checkpoint and walked the run
            # to DELIVERABLE_READY straight after it with nothing asked in
            # between: a `bm-autonomy stop` pressed during the suite was
            # followed by the run being declared ready. Declaring a
            # deliverable is an ACCEPTANCE, so it gets the same treatment
            # the unit acceptance gets.
            refusal = self._authorisation_refusal(project_id)
            if refusal is not None:
                self._set_reason(summary, *self._refusal_stop(
                    project_id, refusal,
                    "the done-definition RAN and nothing was delivered: the "
                    "contract became %s while it was running, so no "
                    "deliverable is declared and the run stays where it is"))
                return
            if outcome["exit_code"] != 0:
                # Bounded to ONCE per park decision, because the loop stops
                # on the reason (REFUTATION-3 LV 6b: 15 executions of the
                # founder's whole suite in one run_to_completion call, 500
                # at the default max_steps). FOUNDER_WAITING is the honest
                # word of the eight: every unit is DONE, no autonomous
                # progress is possible, and the founder's own check is what
                # fails, so a human must look.
                self._set_reason(
                    summary, "FOUNDER_WAITING",
                    "final done-definition check failed (exit %d); the run "
                    "stays in place for inspection" % outcome["exit_code"])
                return
        self.store.record_checkpoint(
            project_id, self.controller_id, "deliverable-ready",
            "final done-definition passed", self.session_id, self.actor)
        # Converge along THIS ENGINE's own edges, one at a time. The walk
        # terminates because _DELIVERY_SOURCE_STATES was derived by walking
        # exactly this map to exactly these targets.
        while cur not in _DELIVERY_TARGETS:
            nxt = _DELIVERY_WALK_EDGES[cur]
            self.store.set_run_state(run_id, nxt, self.actor,
                                     "converging before delivery",
                                     self.session_id, project_id=project_id)
            cur = nxt
        self.store.set_run_state(
            run_id, "DELIVERABLE_READY", self.actor,
            "every non-founder-gated unit is done and the whole "
            "done-definition passes", self.session_id, project_id=project_id)
        open_steps = self.store.list_human_steps(project_id, resolved=False)
        failed_units = [u["unit_id"] for u in
                        self.store.list_units(run_id, raw=True)
                        if u["status"] == "FAILED"]
        summary["founder_gated"] = {
            "human_steps": [s["step_id"] for s in open_steps],
            "failed_units": failed_units}
        self._set_reason(summary, "DELIVERED", "deliverable ready")

    def _begin_stopping(self, project_id, run_id, reason):
        """Design's `stop` transition: STARTS NO new unit; drains
        (releases every held fence, including the controller's own,
        since a deliberate stop IS a completed controller lifecycle,
        unlike a unit's own incomplete work, which is PARKED not
        COMPLETEd), writes a final checkpoint, then STOPPED. Any result
        already recorded before this was called stays recorded; nothing
        here un-records it."""
        run = self.store.get_run(project_id, raw=True)
        cur = run["state"]
        if cur not in _TERMINAL_STATES and cur != "STOPPING":
            self.store.set_run_state(run_id, "STOPPING", self.actor, reason,
                                     self.session_id, project_id=project_id)
        for unit in self.store.list_units(run_id, raw=True):
            if (unit["status"] in ("CLAIMED", "DISPATCHED", "RESULT_IN")
                    and unit["fence_uuid"]):
                self._release_fence(unit["fence_uuid"], "parked",
                                   "controller stopping: %s" % reason)
        self.store.record_checkpoint(
            project_id, self.controller_id, "stopped", reason,
            self.session_id, self.actor)
        self._release_fence(run["fence_uuid"], "complete",
                           "controller run ended: %s" % reason)
        self.store.set_run_state(run_id, "STOPPED", self.actor, reason,
                                 self.session_id, project_id=project_id)

    def stop(self, project_id, reason="founder requested stop"):
        """Public entry point for `bm-controller stop` (Writer B's CLI).
        The no-run and already-terminal cases stay the quiet no-op they
        have always been; a LIVE run is the driver's to stop (L09 GAP 2:
        stopping drains fences and ends the run, which is driving it),
        so a foreign session is refused by name and pointed at adopt()."""
        run = self.store.get_run(project_id, raw=True)
        if run is None or run["state"] in _TERMINAL_STATES:
            return
        self._refuse_foreign_driver(run, "stop")
        self._begin_stopping(project_id, run["run_id"], reason)

    def adopt(self, project_id, note=""):
        """Make THIS engine's session the recorded driver of the
        project's current run: the one deliberate takeover path the
        not-driver refusal names (L09 GAP 2), mirroring STATE.md fence
        adoption (bm-fence adopt): explicit, displacing by name, and
        recorded durably in the same transaction (attribution event
        'controller.run.adopted'). Thin by design: Store.adopt_run holds
        the whole rule, including the no-run and run-terminal refusals
        and the idempotent already-the-driver no-op."""
        return self.store.adopt_run(project_id, self.session_id,
                                    self.actor, note=note)

    def inject_redirect(self, project_id, note, actor):
        """F1 (mid-stream steering): a course correction mid-run, routed
        into the plan as a VERSION BUMP rather than a message that
        scrolled past and was never looked at again (tools/bm_plan.py's
        own BORROWED FROM section: Brother's adaptation of Kimi Code's
        Ctrl-S). Drops a liveness checkpoint alongside the bump so the
        run's own timeline shows where it turned and why. Touches no
        unit row and calls no mark_unit_done, mark_unit_failed or
        set_run_state: a redirect is the plan's business, never the unit
        graph's, which is exactly what lets a completed unit survive it
        untouched.

        `actor` is deliberately a PARAMETER here, not self.actor: the
        engine's own driving actor (whoever last called begin/step) is
        very likely not the person watching the run and typing the
        correction, and the whole point of the feature is that a human
        can steer a run they are not driving.

        Refuses 'no-run' (via _run_or_refuse) if the project has never
        had a controller run, and 'run-terminal' if its most recent run
        already ended (COMPLETE, STOPPED or FAILED_TERMINAL): there is
        nothing left running to steer."""
        run = self._run_or_refuse(project_id)
        if run["state"] in _TERMINAL_STATES:
            raise bs.OwnershipRefused(
                "run-terminal",
                "controller run %r for project %r is already %s; there "
                "is nothing running left to redirect"
                % (run["run_id"], project_id, run["state"]))
        plan_store = bm_plan.PlanStore(self.store.root)
        try:
            new_plan = plan_store.record_redirect(
                project_id, note, actor=actor.get("actor_name", ""))
        except bm_plan.PlanError as exc:
            raise bs.OwnershipRefused(exc.reason, str(exc))
        checkpoint_id = self.store.record_checkpoint(
            project_id, self.controller_id, "redirect", note,
            actor.get("session_id", self.session_id), actor)
        return {"new_plan_version": new_plan["version"],
               "checkpoint_id": checkpoint_id}

    def restore_checkpoint(self, project_id, checkpoint_id):
        """F3 (durable checkpoints): reconstruct where a run stood at a
        named checkpoint, from a FRESH engine instance that shares
        nothing with whichever engine recorded it (the class docstring's
        own resume contract: position comes from the store alone, never
        from anything held in `self`). Calls no write method: a restore
        is a read, never a rollback.

        Raises ValueError if the checkpoint does not exist, or exists but
        belongs to a different project (a caller must not be handed
        another project's state by passing a checkpoint id it never
        issued)."""
        checkpoint = self.store.get_checkpoint(checkpoint_id)
        if checkpoint is None:
            raise ValueError(
                "no checkpoint %r" % (checkpoint_id,))
        if checkpoint["project_id"] != project_id:
            raise ValueError(
                "checkpoint %r belongs to project %r, not %r"
                % (checkpoint_id, checkpoint["project_id"], project_id))
        run = self.store.get_run(project_id, raw=True)
        units = (self.store.list_units(run["run_id"], raw=True)
                if run is not None else [])
        return {"checkpoint": checkpoint,
               "run_state": run["state"] if run is not None else None,
               "units": units}

    # -- internal: small lookups -------------------------------------------

    def _unit_row(self, run_id, unit_id):
        for unit in self.store.list_units(run_id, raw=True):
            if unit["unit_id"] == unit_id:
                return unit
        return None

    def _run_id_for(self, project_id):
        run = self.store.get_run(project_id, raw=True)
        return run["run_id"] if run is not None else None

    def _next_attempt(self, unit_id):
        """The next attempt number for `unit_id`, derived from the
        HIGHEST attempt any dispatch has ever recorded for it, NEVER from
        unit.retry_count: retry_count is the circuit breaker's own
        counter (design step 17, at most 1 by policy) and resets to 0
        when upsert_units redefines a unit's content (a changed
        definition is a new attempt at THAT breaker, not a continuation
        of the old one's count). controller_dispatches.UNIQUE(unit_id,
        attempt) requires attempt numbers to be monotonically increasing
        for the unit's WHOLE lifetime regardless of how many times its
        definition has been redefined; deriving from retry_count directly
        would collide with a prior dispatch's attempt number the moment a
        redefinition resets the breaker."""
        dispatches = self.store.list_dispatches(unit_id, raw=True)
        if not dispatches:
            return 1
        return max(d["attempt"] for d in dispatches) + 1


# ---------------------------------------------------------------------------
# The command line (Writer B, loop L03): a THIN dispatch over the
# ControllerEngine methods above and the schema-15 Store methods they
# share, shaped exactly like tools/bm_autonomy.py's own main()/cli() split
# (the same EXIT_OK/EXIT_REFUSED/EXIT_USAGE law, the same _out/_err funnel,
# the same _parse/_actor helper shapes). This section still issues NO SQL
# of its own: every command below either constructs a ControllerEngine and
# calls one of its public methods (begin, plan, step, run_to_completion,
# receive_result, stop, restore_checkpoint; get_run is read straight off
# the store, the same way bm_autonomy.py's status reads latest_contract
# straight off the store), or reads/writes through a schema-15 Store
# method directly.
#
# THREE COMMANDS CALL THE STORE DIRECTLY RATHER THAN THE ENGINE, ON
# PURPOSE: `resume` (PAUSED -> READY) and `complete` (DELIVERABLE_READY ->
# COMPLETE) are founder actions, never a controller self-move (design
# section 1's own words for the `complete` transition, and the same
# reasoning applies to reversing a pause); no ControllerEngine method
# performs either move, so these two commands call store.set_run_state
# directly and let the store's own CONTROLLER_STATE_TRANSITIONS legality
# check answer whether the move is allowed, the identical thin-CLI law
# tools/bm_autonomy.py's own pause/resume/stop/revoke already follow for
# the contract. `adopt` (L09 GAP 2) is the third for the same reason: a
# driver handover is a founder-facing ownership move over a run, and
# Store.adopt_run holds the whole rule.
#
# TEN COMMANDS: the design's own eight (file-plan section 7 and Writer
# A's report, Cross-writer dependencies 1: start, step, plan,
# record-result, status, stop, resume, complete) plus `adopt`, added
# 2026-08-06 (L09 GAP 2) as the CLI door for the run-driver handover the
# engine's not-driver refusal names, plus `restore`, added for F3
# (durable checkpoints): the CLI door for restore_checkpoint, a read no
# other command performs. check_timeouts is a real ControllerEngine
# method but is not wired to a subcommand here: a scheduler-driven
# deadline check is a later door on this same dispatch table, not a gap
# the design or this loop's own build instructions name.
#
# EXIT CODES, identical law to bm_autonomy.py: 0 success (including every
# idempotent no-op: complete-on-already-COMPLETE, stop-on-no-run, resume-
# on-not-PAUSED), 1 a refusal (the store or the engine looked at a real
# question and said no), 2 a usage error caught by THIS FILE before any
# store is ever opened.
#
# USER-FACING STRINGS NAME NO REPO-RELATIVE PATH: every printed invocation
# hint goes through _cmd(), the same bs.invocation() resolver
# tools/bm_autonomy.py uses, never a bare "tools/bm_controller.py".
# ---------------------------------------------------------------------------

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

_ACTOR_FLAGS = ("actor-type", "actor-name", "session-id")


def _exit_for_stop_reason(stop_reason):
    """R7's exit code, the whole reason this function exists rather than
    every command returning EXIT_OK the way `step` and `start` did for
    every OTHER stop reason before this row of the readiness board. A
    stop_reason a caller has to parse out of a JSON blob is a PRINTED
    VERDICT: a script that wants to know whether its dispatch was
    refused has to grep stdout, and a hook that forgets to grep sees
    green. PLAN_GATE_REFUSED is the one stop reason this file makes into
    a real non-zero exit code, because it is the one this row of the
    readiness board exists to make un-skippable; every other stop reason
    (FOUNDER_WAITING, IN_FLIGHT, a plain park, and so on) is a normal
    pause in a resumable loop, not a refusal, and keeps returning
    EXIT_OK exactly as it always has."""
    return EXIT_REFUSED if stop_reason == "PLAN_GATE_REFUSED" else EXIT_OK


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _cmd():
    """This tool's own invocation, for user-facing instruction text (see
    the section note above: never a bare repo-relative path)."""
    return bs.invocation("bm-controller", __file__)


def _root():
    root, _source = bs.require_root()
    return root


def _store():
    # Matching bm_autonomy.py's own CLI convention: only bm_store.py init
    # may pass create=True. Every command here refuses "no-store" instead.
    return bs.Store(_root(), create=False)


def _read_store():
    """A read-only handle for `status`, the one command that never
    writes, matching tools/bm_autonomy.py's own _read_store discipline."""
    return bs.ReadOnlyStore(_root())


def _engine(store, controller_id, actor, session_id, require_plan_gate=False):
    """One ControllerEngine, wired to the production seam: RecordIntentWorker
    (writes the unit brief to stdout and parks, design section 4) and
    SubprocessCheckRunner (this file's only subprocess call site,
    unchanged by this CLI section).

    require_plan_gate is False by default: opt in, the same way
    --unattended is opt in (see PlanGate's own docstring for why an
    unconditional default would refuse every pre-existing test and every
    run that predates this row of the readiness board). True wires a
    StorePlanGate reading tools/bm_plan.py's PlanStore rooted at this
    same project root, so `--require-plan-gate` makes R7 live for this
    one CLI call."""
    plan_gate = None
    if require_plan_gate:
        plan_gate = StorePlanGate(bm_plan.PlanStore(store.root))
    return ControllerEngine(store, RecordIntentWorker(),
                            SubprocessCheckRunner(), controller_id, actor,
                            session_id=session_id, plan_gate=plan_gate)


def _parse(argv, known, wants_value=(), repeatable=()):
    """Flags into (positional, kv), refusing anything unrecognized.
    Structurally identical to tools/bm_autonomy.py's own _parse."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            _out("recognized flags: %s"
                 % ", ".join("--" + k for k in sorted(known)))
            if wants_value or repeatable:
                _out("flags taking a value: %s"
                     % ", ".join("--" + k for k in
                                 sorted(set(wants_value) | set(repeatable))))
            sys.exit(EXIT_OK)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_controller: unrecognized flag --%s (recognized: "
                     "%s)" % (name, ", ".join("--" + k for k in
                                              sorted(known))))
                sys.exit(EXIT_USAGE)
            if name in wants_value or name in repeatable:
                if i + 1 >= len(argv):
                    _err("bm_controller: --%s needs a value" % name)
                    sys.exit(EXIT_USAGE)
                if name in repeatable:
                    kv.setdefault(name, []).append(argv[i + 1])
                else:
                    kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _require(kv, name, usage):
    val = kv.get(name)
    if not val:
        _err(usage)
        _err("bm_controller: --%s is required" % name)
        sys.exit(EXIT_USAGE)
    return val


def _actor(kv, usage):
    """Build the actor dict every mutating command passes to the store or
    the engine, structurally identical to tools/bm_autonomy.py's own
    _actor: actor_type restricted to human|model, actor_name required
    with no sensible default, session_id a fresh unguessable id per
    process when omitted."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_controller: --actor-type must be 'human' or 'model', "
             "got %r" % actor_type)
        sys.exit(EXIT_USAGE)
    actor_name = _require(kv, "actor-name", usage)
    session_id = kv.get("session-id") or ("cli-" + uuid.uuid4().hex)
    return {"actor_type": actor_type, "actor_name": actor_name,
            "session_id": session_id}


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True))


def _int_flag(kv, name, usage, default, minimum=None):
    """Parse an optional integer flag, or `default` when the flag was not
    given. A malformed or out of range value is exit 2 (usage): a typo in
    the invocation, never a situation for the store or the engine to
    judge, the same law tools/bm_autonomy.py's own _ceiling_flag states."""
    raw = kv.get(name)
    if raw is None:
        return default
    try:
        n = int(raw)
    except ValueError:
        _err(usage)
        _err("bm_controller: --%s must be a whole number, got %r"
             % (name, raw))
        sys.exit(EXIT_USAGE)
    if minimum is not None and n < minimum:
        _err(usage)
        _err("bm_controller: --%s must be at least %d, got %d"
             % (name, minimum, n))
        sys.exit(EXIT_USAGE)
    return n


def _load_units(kv, usage):
    """The unit graph `plan` upserts: a JSON array of unit objects, from
    --units-file PATH or --units-json TEXT, mutually exclusive. This file
    adds no shape of its own for a unit; upsert_units (tools/bm_store.py)
    is what validates shape, so a malformed unit is still a store refusal
    (exit 1), never a silent acceptance here."""
    raw_text = kv.get("units-json")
    path = kv.get("units-file")
    if not raw_text and not path:
        _err(usage)
        _err("bm_controller: plan needs --units-file PATH or --units-json "
             "TEXT naming the unit graph")
        sys.exit(EXIT_USAGE)
    if raw_text and path:
        _err(usage)
        _err("bm_controller: --units-file and --units-json are mutually "
             "exclusive")
        sys.exit(EXIT_USAGE)
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                raw_text = fh.read()
        except (IOError, OSError) as exc:
            _err("bm_controller: could not read --units-file %s: %s"
                 % (path, exc))
            sys.exit(EXIT_USAGE)
    try:
        units = json.loads(raw_text)
    except ValueError as exc:
        _err("bm_controller: the unit graph is not valid JSON: %s" % exc)
        sys.exit(EXIT_USAGE)
    if not isinstance(units, list):
        _err("bm_controller: the unit graph must be a JSON array of unit "
             "objects, got %s" % type(units).__name__)
        sys.exit(EXIT_USAGE)
    return units


def _report_trace(trace, project_id):
    """The human-readable summary of a run_to_completion() call: every
    step()'s own summary dict, folded into one line per field rather than
    printed as one JSON blob per step."""
    if not trace:
        _out("no steps taken")
        return
    dispatched = sum(len(s.get("dispatched") or []) for s in trace)
    completed = sum(len(s.get("completed") or []) for s in trace)
    last = trace[-1]
    _out("project %s: run %s, %d step(s), now %s"
         % (project_id, last["run_id"], len(trace), last["state"]))
    _out("dispatched %d unit(s), completed %d unit(s) this call"
         % (dispatched, completed))
    if last.get("note"):
        _out("note: %s" % last["note"])
    if last.get("stop_reason"):
        _out("reason: %s" % last["stop_reason"])
    founder_gated = last.get("founder_gated")
    if founder_gated:
        _out("founder-gated remainder: %d open human step(s), %d failed "
             "unit(s)" % (len(founder_gated.get("human_steps") or []),
                         len(founder_gated.get("failed_units") or [])))


def _drive_until_parked(engine, project_id, max_steps):
    """Call engine.step() up to `max_steps` times, stopping at the FIRST
    point that either reaches one of ControllerEngine's own
    _RUN_TO_COMPLETION_STOP_STATES, or makes no further progress THIS
    wave without outside help.

    WHY NOT engine.run_to_completion() DIRECTLY, reproduced rather than
    assumed: run_to_completion()'s own stop set does not include a wave
    that DISPATCHED a unit but recorded nothing synchronously ('pending';
    design section 4's RecordIntentWorker always returns this, since
    dispatch means record-intent-and-await, never a live model call from
    this process). Driving that through run_to_completion(project_id,
    max_steps=500) against the real production worker was reproduced
    printing the SAME unit brief to stdout up to 500 times in one `start`
    call, because step() re-awaits an open dispatch on every call and the
    run's own STATE stays EXECUTING throughout, which is not in the stop
    set. This wrapper stops the FIRST time a wave dispatches without a
    synchronous completion (the exact 'awaiting an out-of-band record-
    result call' case) or makes no progress at all, and only keeps
    looping while a wave is actually completing work (which can only
    happen against a synchronous test worker, or against a real, already
    -parked dispatch whose result has meanwhile arrived by some other
    channel), so a real `bm-controller start` call always returns
    promptly instead of busy-looping on its own printed brief.

    That reproduced reasoning still holds; what changed in round 4 is that
    the condition is no longer written out here by hand. Both drivers now
    stop on the SAME predicate, _loop_stops, which removes the divergence
    REFUTATION-3 LV 6a's own corollary names: the park stop round 3 added
    to run_to_completion was unreachable from the shipped CLI, because
    this wrapper already stopped earlier on a broader condition, so the
    two drivers disagreed about when a run was parked and only one of them
    was ever exercised. The case this docstring reproduces is now called
    IN_FLIGHT."""
    trace = []
    for _ in range(max_steps):
        summary = engine.step(project_id)
        trace.append(summary)
        if _loop_stops(summary):
            break
    return trace


# ---------------------------------------------------------------------------
# THE UNATTENDED PREFLIGHT: what must be true BEFORE a run nobody is
# watching may start.
#
# Founder posture, 2026-08-06. docs/FULL-AUTO.md's own "READ THIS BEFORE ANY
# UNATTENDED RUN" block stated this target in prose and told the founder to
# do it by hand; this section is that prose turned into a gate. The goal it
# serves is BOUNDED DAMAGE, not more capability: an unattended run may only
# start on a machine where the harm it could do can be seen and undone.
#
# INTERACTIVE USE IS UNTOUCHED, and that is the load-bearing property of
# this whole section. Nothing below runs unless `--unattended` is passed on
# the command line. No default changes, no environment variable is read
# unless that flag is present, and no existing code path gains a branch that
# can fire without it, so a founder sitting at the keyboard gets byte for
# byte the behaviour they had before. The regression guard is
# TestUnattendedPreflightLeavesInteractiveUseAlone in
# tools/test_bm_controller.py.
#
# THE TWO FENCE CONDITIONS REFUSE, THEY DO NOT SET (the choice stated, here
# and in unattended_preflight's own docstring). BM_FENCE_MODE and
# BM_FENCE_STRICT are read by tools/bm_fence_hook.py, and that hook runs as
# a SEPARATE PROCESS spawned by the runtime from the runtime's own
# environment (hooks/hooks.json), never as a child of this one. A value this
# process wrote into its own os.environ would therefore reach nothing at
# all, and a controller that "turned enforcement on" while the hook kept
# reading the old value would be worse than one that refuses: it would
# report a protection nobody has. So the controller refuses, and names the
# exact command that really does change what the hook reads.
# ---------------------------------------------------------------------------

#: The eight reason codes an unattended start can refuse with, one per
#: precondition, as NAMED CONSTANTS rather than literals at each raise site.
#: One place to enumerate them from is what lets UNATTENDED_REFUSAL_HELP
#: below and the tests both be checked against the same list: a reason code
#: that reaches a founder with no plain-language rewrite is this
#: repository's own twice-recorded defect (docs/mistakes/
#: M08-new-refusals-had-no-plain-language-twice.md).
UNATTENDED_NO_IDENTITY = "unattended-no-identity"
UNATTENDED_FENCE_ADVISORY = "unattended-fence-advisory"
UNATTENDED_FENCE_NOT_STRICT = "unattended-fence-not-strict"
#: The EIGHTH precondition (added 2026-08-10, closing a CRITICAL finding
#: from an independent Codex audit). The two codes above prove only that
#: BM_FENCE_MODE and BM_FENCE_STRICT are SET to the values
#: tools/bm_fence_hook.py reads; neither one proves the runtime spawns that
#: hook at all. Under Codex exec, confirmed, both variables can read
#: exactly right while the hook never runs once. This code is what
#: _unattended_fence_canary returns when it cannot show, by actually
#: running the hook, that it refuses a foreign write. See that function's
#: docstring for the exact, narrow thing a pass here does and does not
#: prove.
UNATTENDED_FENCE_NOT_PROVEN = "unattended-fence-not-proven"
UNATTENDED_NO_REPOSITORY = "unattended-no-repository"
UNATTENDED_DIRTY_TREE = "unattended-dirty-tree"
UNATTENDED_FOREIGN_CLAIM = "unattended-foreign-claim"
UNATTENDED_NO_SNAPSHOT = "unattended-no-snapshot"

#: The exact remediation for the two fence conditions, as the founder would
#: type it. Sourced from tools/bm_messages.py (RF-4), the catalog these two
#: constants now share with tools/bm_visual.py's rewrite, rather than
#: defined here twice: each is quoted in two places here and once again in
#: docs/FULL-AUTO.md, and a drifted copy would send somebody to the wrong
#: variable.
UNATTENDED_FENCE_MODE_COMMAND = bm_msgs.UNATTENDED_FENCE_MODE_COMMAND
UNATTENDED_FENCE_STRICT_COMMAND = bm_msgs.UNATTENDED_FENCE_STRICT_COMMAND

#: reason code -> (what we were doing, what is wrong, what to do next), the
#: same three-part shape tools/bm_visual.py's REFUSAL_HELP uses, so a
#: founder meets a sentence rather than a code. RF-4 (GAP-19): sourced from
#: tools/bm_messages.py rather than defined here, so tools/bm_visual.py's
#: copy of these eight entries is the SAME object as this one and the two
#: surfaces cannot drift apart. The reason-code CONSTANTS above stay
#: defined in THIS file, unmoved: tools/test_bm_visual.py's scanner
#: (_controller_reason_codes) discovers them by reading this file's own
#: module-level source with ast, matching a literal string assignment, and
#: moving them to bm_messages.py would blind that scanner.
UNATTENDED_REFUSAL_HELP = bm_msgs.UNATTENDED_REFUSAL_HELP


class UnattendedGitFacts(object):
    """The four repository facts the preflight needs, and the one snapshot
    it may take, all answered by tools/bm_autosave.py.

    WHY THAT MODULE AND NOT A SECOND ONE. tools/test_bm.py's own
    no-subprocess scan names exactly two shipping modules that may import
    `subprocess`: tools/bm_autosave.py and this file. bm_autosave.py already
    owns the recovery snapshot, already runs every git call through one
    sanitised-environment funnel (its own `_run_git`), and already resolves
    the repository root once so a call made from a subdirectory still covers
    the whole tree. A second snapshot mechanism would be a second story
    about what is recoverable, which is exactly the thing a founder cannot
    check at 3am. So this class is a thin reader over that module and
    invents nothing.

    WHY IT IS A CLASS AND NOT FOUR FUNCTIONS. It is the seam the tests
    replace. Every condition below takes its repository facts through one
    injected object, so tools/test_bm_controller.py exercises all eight
    refusals against a fake with no repository, no git binary and no
    snapshot, deterministically, in the same hermetic style the engine tests
    already use for the worker and the check runner.

    The sibling module is loaded LAZILY, on first use. An interactive
    command must not pay for, or be exposed to, a module it will never
    reach: without `--unattended` nothing here is ever constructed, and
    nothing here is ever loaded."""

    def __init__(self, autosave=None):
        self._autosave = autosave

    def _module(self):
        if self._autosave is None:
            self._autosave = _load("bm_autosave")
        return self._autosave

    def toplevel(self, start_dir):
        """The repository root containing `start_dir`, or None when there is
        no repository. bm_autosave.resolve_toplevel's own answer, which
        also covers git being unavailable at all: its runner answers rc 127
        rather than raising."""
        return self._module().resolve_toplevel(start_dir)

    def branch(self, toplevel):
        """The checked-out branch NAME, or None when there is not one.

        A detached HEAD answers the literal string 'HEAD', and that is a
        None here rather than a name: commits made on a detached HEAD are
        reachable from no branch, so an unattended run that made twenty of
        them would leave the founder with nothing to check out in the
        morning."""
        result = self._module()._run_git(
            toplevel, "rev-parse", "--abbrev-ref", "HEAD")
        if result.returncode != 0:
            return None
        name = (result.stdout or "").strip()
        if not name or name == "HEAD":
            return None
        return name

    def modified_paths(self, toplevel):
        """Every path `git status --porcelain` reports as staged, changed,
        untracked or conflicted, or None when git could not answer at all
        (which is a refusal below, never an all-clear).

        Untracked files COUNT. The snapshot captures them, and a founder who
        left a scratch file in the tree has left something an unattended run
        can trip over; both facts point the same way.

        ONE HONEST LIMIT, stated rather than implied: git quotes a path
        whose name needs escaping (`core.quotePath`, on by default), and the
        surrounding quotes are stripped here without un-escaping what is
        inside them. Such a path is therefore compared, and must be
        acknowledged, in the spelling this function returns. The refusal
        prints that exact spelling, so it stays actionable rather than
        becoming a guessing game."""
        result = self._module()._run_git(toplevel, "status", "--porcelain")
        if result.returncode != 0:
            return None
        paths = []
        for line in (result.stdout or "").splitlines():
            if len(line) < 4:
                continue
            entry = line[3:].strip()
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1].strip()
            if len(entry) > 1 and entry.startswith('"') and entry.endswith('"'):
                entry = entry[1:-1]
            if entry:
                paths.append(entry)
        return paths

    def recovery_point(self, toplevel, session_id):
        """(ok, detail): is there a snapshot this run could be rewound to,
        AFTER this call.

        Precondition 5 is "a recovery snapshot exists, or is created before
        the first write", and this is that sentence in order: take one now,
        and only if taking one failed, ask whether an earlier one is still
        on record. A snapshot whose answer is 'clean' is a success and not a
        failure: it means the tree already matches the last commit, so the
        commit itself is the recovery point."""
        mod = self._module()
        result = mod.snapshot(toplevel, session_id, "unattended preflight")
        if result.get("ok"):
            return True, (result.get("reason") or "saved")
        existing = mod.list_snapshot_refs(toplevel,
                                          mod.worktree_id_for(toplevel))
        if existing:
            return True, ("an earlier snapshot is still on record (%s)"
                          % (existing[-1][0],))
        return False, (result.get("reason") or "the snapshot did not publish")


def _unattended_identity(store, project_id, session_id):
    """Precondition 7: the records are readable, and this run has a name it
    will still have on the next invocation.

    Both halves are ONE condition because they fail the same way. A run with
    no stable session identity gets a fresh `cli-<random>` on every process
    (see _actor), so the driver guard sees a foreign session on the second
    call, the recovery snapshot it took is filed under a name nothing looks
    for again, and every fence it holds looks like somebody else's. Reading
    the contract is the store probe: it is the read every later step depends
    on, so a store that cannot answer it cannot run anything anyway."""
    if not (session_id or "").strip():
        return (UNATTENDED_NO_IDENTITY,
                "an unattended run needs a stable --session-id, because "
                "without one every invocation invents a fresh identity: the "
                "next process would be refused as a foreign driver, and the "
                "recovery snapshot this one takes would be filed under a "
                "name nothing looks for again. Pass the SAME --session-id on "
                "every call of this run.")
    try:
        store.latest_contract(project_id, raw=True)
    except Exception as exc:
        return (UNATTENDED_NO_IDENTITY,
                "this project's records could not be read (%s: %s), so "
                "nothing here can be judged. Run the doctor before starting "
                "anything unattended."
                % (type(exc).__name__, exc))
    return None


def _unattended_fence_mode(env):
    """Precondition 1: fence enforcement is really on for this run.

    fence_mode and enforced_mode in tools/bm_fence_hook.py read exactly this
    variable, lowercased and stripped, with anything unrecognised falling
    back to ADVISORY. The comparison here is deliberately that same one,
    spelled the same way, so the two cannot come to disagree about what
    'enforced' means."""
    raw = (env.get("BM_FENCE_MODE") or "").strip().lower()
    if raw == "enforced":
        return None
    return (UNATTENDED_FENCE_ADVISORY,
            "the write fence is ADVISORY for this run (BM_FENCE_MODE=%r), so "
            "a write it cannot check is allowed through with a warning "
            "nobody is awake to read. Run this in the same shell that starts "
            "the run, then start again:\n    %s"
            % (env.get("BM_FENCE_MODE") or "", UNATTENDED_FENCE_MODE_COMMAND))


def _unattended_fence_strict(env):
    """Precondition 2: claim-before-edit is being enforced too.

    tools/bm_fence_hook.py reads BM_FENCE_STRICT as SET unless it is empty
    or '0'; the same reading is used here, for the same reason the mode
    check above uses that file's own."""
    raw = (env.get("BM_FENCE_STRICT") or "").strip()
    if raw not in ("", "0"):
        return None
    return (UNATTENDED_FENCE_NOT_STRICT,
            "BM_FENCE_STRICT is not set, so an edit to a file no active "
            "record has claimed is allowed through. That is the ordinary "
            "shape of an unwatched run's mistake. Run this in the same shell "
            "that starts the run, then start again:\n    %s"
            % (UNATTENDED_FENCE_STRICT_COMMAND,))


# ---------------------------------------------------------------------------
# PRECONDITION 8: the fence canary. Added 2026-08-10 after an independent
# Codex audit rated _unattended_fence_mode above CRITICAL: it proves the
# fence is "on" by reading BM_FENCE_MODE, and never checks that anything
# actually SPAWNS tools/bm_fence_hook.py. Under Codex exec, confirmed, the
# hook is never invoked at all, so an unattended run can pass both fence
# preconditions above with zero enforcement behind them. This is the same
# failure class the whole product exists to close (a promise recorded and
# never checked), now one level up, inside the gate that is supposed to
# guard unattended runs from it.
# ---------------------------------------------------------------------------

#: The fixed, hardcoded command the canary runs. Never founder- or
#: model-authored text: `argv` built from this constant is the same three
#: tokens on every call, which is what lets it be the second (and only
#: other) place in this module `subprocess` is reached without reopening
#: the FOUNDER-COMMAND gate SubprocessCheckRunner exists to be the one door
#: for (see that class's docstring).
_FENCE_HOOK_FILE = os.path.join(HERE, "bm_fence_hook.py")

#: Two DIFFERENT session ids for the canary's own throwaway store, so the
#: claim really is somebody else's from the invoking call's point of view.
#: Neither is ever this run's own --session-id: the canary's store and the
#: real run's store are never the same database, so there is no identity
#: to protect by matching them.
_FENCE_CANARY_CLAIMANT_SESSION = "bm-fence-canary-claimant"
_FENCE_CANARY_INVOKER_SESSION = "bm-fence-canary-invoker"
_FENCE_CANARY_PROBE_PATH = "canary-probe.txt"


def _default_fence_canary_runner(argv, cwd, env, stdin_text):
    """The REAL process spawn behind _unattended_fence_canary's default
    runner=None: `python3 tools/bm_fence_hook.py hook`, with a JSON
    PreToolUse payload on real stdin, in a caller-built, fully isolated
    environment.

    This and SubprocessCheckRunner.run above are the ONLY two places in
    this module that reach `subprocess`, both pinned by qualified name in
    tools/test_bm_controller.py's _EXEC_PRIMITIVE_HOMES; a third call site
    added anywhere else in this file still fails that structural guard.
    Unlike the checker's command, `argv` here is never founder- or
    model-authored: _unattended_fence_canary builds it from the fixed
    literal _FENCE_HOOK_FILE, so there is no injection surface the way
    there is for a done_check string, and no gate is being reopened by
    giving this its own call site rather than routing through the
    checker."""
    proc = subprocess.run(argv, cwd=cwd, env=env, input=stdin_text,
                          capture_output=True, text=True)
    return {"stdout": proc.stdout, "stderr": proc.stderr,
            "returncode": proc.returncode}


def _unattended_fence_canary(env, runner=None):
    """Precondition 8: the write fence actually REFUSES, proved by running
    it, not by reading a variable it is supposed to obey.

    WHY THIS EXISTS. _unattended_fence_mode and _unattended_fence_strict
    above prove exactly one thing each: that BM_FENCE_MODE and
    BM_FENCE_STRICT are set to the values tools/bm_fence_hook.py reads.
    Neither one, nor both together, proves that anything actually SPAWNS
    that hook. An independent Codex audit rated this CRITICAL: under Codex
    exec the hook never runs at all, so an unattended run passes every
    other precondition with the fence completely unenforced. This function
    NARROWS that gap by one layer and closes nothing: it demonstrates the
    hook binary's own refusal when spawned, and stays silent, on purpose,
    about whether any runtime spawns it. The 2026-08-11 cross-family audit
    read the earlier wording here ("closes that one gap") as a claim the
    pass semantics cannot support, and it was right.

    WHAT IT DOES. Builds a THROWAWAY BrotherMode project under
    tempfile.TemporaryDirectory(), with its own isolated environment
    (BROTHERMODE_ROOT, HOME, BROTHERMODE_VAULT and BROTHERSBE_VAULT all
    pinned inside it; BROTHERMODE_VAULT is exported ambient on this
    machine and WINS over HOME if only HOME is overridden, so all four are
    pinned together, the same isolation tools/test_bm_packaging_install.py
    already documents and this mirrors). It claims one probe path there
    under _FENCE_CANARY_CLAIMANT_SESSION, then runs the REAL
    tools/bm_fence_hook.py as a REAL SUBPROCESS, with a real PreToolUse
    JSON payload on real stdin naming an Edit of that path under the
    DIFFERENT _FENCE_CANARY_INVOKER_SESSION, and reads what it printed
    back.

    THREE OUTCOMES.
      1. The hook denied the write: PROVEN. Returns None, same as every
         other passing precondition.
      2. The hook ran and did NOT deny it: a real, demonstrated defect in
         tools/bm_fence_hook.py, not a missing proof, and the finding says
         so. The fence did not refuse a write to a path another session
         owns.
      3. The hook could not be run at all, or its stdout could not be read
         as the deny shape: the finding says the refusal could not be
         DEMONSTRATED, in words that distinguish that from outcome 2. Not
         knowing whether a fence works is not the same claim as knowing it
         is broken, and the two must never be said the same way.

    THE HONESTY BOUNDARY, stated as plainly as this file can state
    anything. Outcome 1 proves that the hook BINARY refuses ONE foreign
    Edit-shaped write, the single payload shape this canary sends. It does
    not generalize even one step further: a shell write crosses a fence
    unrefused by design (docs/KNOWN-LIMITS.md), so a pass here says nothing
    about non-Edit paths. And it proves that only for a foreign
    write WHEN IT IS ACTUALLY RUN. It cannot prove, and must never be
    worded anywhere (here, in the reason text, in UNATTENDED_REFUSAL_HELP)
    to imply, that the RUNTIME about to run unattended will INVOKE that
    hook on any write it makes. That second question is exactly the Codex
    gap this function exists to close one layer down from, and this
    function has no way to see the answer to it: it can watch the hook
    refuse in isolation, and it can watch nothing about whether the
    runtime ever calls it for real. Describing a pass here with any
    end-to-end wording that reaches past the hook itself, to the runtime
    or to the write it would guard, is FORBIDDEN, precisely because it
    claims the half this function cannot see. The only honest sentence is
    the narrow one: the hook refuses when it is run.

    ENTERING THROUGH A REAL SUBPROCESS WITH REAL STDIN, rather than
    calling tools/bm_fence_hook.decide() in-process, is the point and not
    an implementation detail. This repository once shipped nine green
    tests that all called decide() directly while the live defect sat in
    cmd_hook, the stdin-reading entry point in front of it, for six days:
    decide() was correct and nothing that skipped the door in front of it
    could have caught what was wrong with the door. This canary goes in
    through the same door the runtime does.

    NEVER RAISES. Every failure path here (an unspawnable process,
    unreadable stdout, unparseable JSON, the throwaway store itself
    misbehaving, anything unforeseen) is caught and turned into the
    'could not be demonstrated' finding. This runs in front of every
    unattended start, so a bug in this safety check must never become a
    new way for an unsafe run to begin.

    `runner(argv, cwd, env, stdin_text) -> {"stdout", "stderr",
    "returncode"}` is the injection seam tests use to supply a fake
    outcome, or to raise, without spawning a real process. The default,
    runner=None, really spawns via _default_fence_canary_runner. The
    throwaway directory is cleaned up unconditionally (the `with
    tempfile.TemporaryDirectory()` block covers the claim, the spawn, and
    the read; nothing about the finding this function returns depends on
    the throwaway still existing)."""
    spawn = _default_fence_canary_runner if runner is None else runner
    try:
        with tempfile.TemporaryDirectory(
                prefix="bm-fence-canary-") as throwaway:
            fake_home = os.path.join(throwaway, "home")
            os.makedirs(fake_home, exist_ok=True)
            canary_env = {
                "PATH": (env.get("PATH") or ""),
                "HOME": fake_home,
                "BROTHERMODE_ROOT": throwaway,
                "BROTHERMODE_VAULT": os.path.join(throwaway, "vault"),
                "BROTHERSBE_VAULT": os.path.join(throwaway, "vault"),
            }
            with bs.Store(throwaway) as store:
                store.claim(name="canary-probe", lifetime="ephemeral",
                            files=[_FENCE_CANARY_PROBE_PATH],
                            session_id=_FENCE_CANARY_CLAIMANT_SESSION)
            payload = json.dumps({
                "session_id": _FENCE_CANARY_INVOKER_SESSION,
                "tool_name": "Edit",
                "tool_input": {"file_path": _FENCE_CANARY_PROBE_PATH},
                "cwd": throwaway,
            })
            result = spawn([sys.executable, _FENCE_HOOK_FILE, "hook"],
                           throwaway, canary_env, payload)
            stdout = (result["stdout"] if isinstance(result, dict)
                      else result.stdout)
            stderr = ((result["stderr"] if isinstance(result, dict)
                       else result.stderr) or "")
            returncode = (result["returncode"] if isinstance(result, dict)
                          else result.returncode)
            decision = (json.loads(stdout) if (stdout or "").strip()
                       else None)
            denied = (returncode == 0
                      and isinstance(decision, dict)
                      and isinstance(decision.get("hookSpecificOutput"), dict)
                      and decision["hookSpecificOutput"].get(
                          "hookEventName") == "PreToolUse"
                      and decision["hookSpecificOutput"].get(
                          "permissionDecision") == "deny")
            if denied:
                return None
            if returncode != 0:
                # The 2026-08-10 night refute pass found the empty stdout of
                # a process that never really ran (absent file, crash) being
                # read as outcome 2, so a hook that was never demonstrated
                # got reported as a demonstrated defect. Under the
                # PreToolUse contract a nonzero exit is a non-blocking error
                # whose stdout is not honored, so nothing was established
                # either way: this is outcome 3, never outcome 2.
                first_err = (stderr.strip().splitlines()[0]
                             if stderr.strip() else "(no stderr)")
                return (UNATTENDED_FENCE_NOT_PROVEN,
                        "the fence hook's refusal could not be DEMONSTRATED "
                        "here: the spawned process exited with status %d "
                        "rather than answering (first stderr line: %s). "
                        "That is different from a fence known to be broken: "
                        "nothing about whether it refuses a foreign write "
                        "was established either way, and a decision printed "
                        "by a run that exits nonzero is not honored by the "
                        "runtime. Confirm `%s tools/bm_fence_hook.py hook` "
                        "runs here, then start again."
                        % (returncode, first_err, sys.executable))
            return (UNATTENDED_FENCE_NOT_PROVEN,
                    "the fence hook was actually run, as a real subprocess, "
                    "against a throwaway claim owned by a different "
                    "session, and it did NOT refuse a write to a path that "
                    "other session owns. This is a real, demonstrated "
                    "defect in tools/bm_fence_hook.py, found by running it, "
                    "never a missing proof. This says nothing about "
                    "whether the runtime you are about to run unattended "
                    "under would even call the hook on a real write; that "
                    "is a separate question this canary cannot see.")
    except Exception as exc:
        return (UNATTENDED_FENCE_NOT_PROVEN,
                "the fence hook's refusal could not be DEMONSTRATED here "
                "(%s: %s). That is different from a fence known to be "
                "broken: tools/bm_fence_hook.py could not be run and read "
                "in this environment at all, so nothing about whether it "
                "refuses a foreign write was established either way. "
                "Confirm `%s tools/bm_fence_hook.py hook` runs here, then "
                "start again."
                % (type(exc).__name__, exc, sys.executable))


def _unattended_repository(git, root):
    """Precondition 3: a repository is detected and a branch is named.

    Returns (finding_or_None, toplevel_or_None), so the two later conditions
    that need the repository root do not ask for it a second time and cannot
    end up judging a different tree."""
    toplevel = git.toplevel(root)
    if not toplevel:
        return ((UNATTENDED_NO_REPOSITORY,
                 "%s is not inside a git repository, or git could not be run "
                 "here, so there is no history an unattended run could be "
                 "undone from. Start it from a tracked project."
                 % (root,)), None)
    branch = git.branch(toplevel)
    if not branch:
        return ((UNATTENDED_NO_REPOSITORY,
                 "the repository at %s has no branch checked out (a detached "
                 "HEAD). Commits made from there are reachable from no "
                 "branch, so a night of them would be unfindable in the "
                 "morning. Check a branch out by name first."
                 % (toplevel,)), toplevel)
    return None, toplevel


def _unattended_clean_tree(git, toplevel, acknowledged):
    """Precondition 4: the working tree is clean, OR every existing
    modification was named on the command line.

    Named PER PATH, not blanket-acknowledged. A single flag meaning "yes, I
    know it is dirty" becomes a habit, and a habit acknowledges the file the
    founder forgot about just as readily as the one they meant. Naming each
    path is evidence the acknowledgement was written after reading the list,
    and a file that appears after that list was read still stops the run."""
    if toplevel is None:
        return None
    modified = git.modified_paths(toplevel)
    if modified is None:
        return (UNATTENDED_DIRTY_TREE,
                "git could not report the state of the working tree at %s, "
                "so it cannot be called clean. An unattended run does not "
                "start on an unknown tree." % (toplevel,))
    known = set(acknowledged or ())
    unacknowledged = [p for p in modified if p not in known]
    if not unacknowledged:
        return None
    return (UNATTENDED_DIRTY_TREE,
            "the working tree at %s already carries %d change(s) nobody has "
            "acknowledged, so this run's work could not be told apart from "
            "yours afterwards. Either clean the tree, or name each one "
            "exactly as printed here:\n%s"
            % (toplevel, len(unacknowledged),
               "\n".join("    --acknowledge-modified %s" % p
                         for p in unacknowledged)))


def _unattended_write_paths(store, project_id):
    """The paths this run may write: the live contract's own allowed_paths,
    which is the only boundary that exists before a unit graph does.

    An empty list when there is no contract at all, and that is honest
    rather than lenient: with no contract every gate_check refuses, so such
    a run writes nothing and there is nothing for a foreign claim to collide
    with."""
    contract = store.latest_contract(project_id, raw=True)
    if contract is None:
        return []
    return list(contract.get("allowed_paths") or [])


def _unattended_foreign_claim(store, root, session_id, project_id):
    """Precondition 6: no OTHER session holds an active claim over a path
    this run may write.

    THE OVERLAP RULE IS NOT COPIED. bs.paths_overlap is the same primitive
    Store._find_overlap uses, so "do these two paths collide?" keeps one
    implementation in this product and this is a caller of it, never a
    second opinion. What is done here and not there is the ENUMERATION:
    Store._find_overlap returns the FIRST colliding record and stops, which
    is right for a claim that is about to be written and wrong here, because
    the first hit is often this same run's own fence from the previous
    invocation, and stopping there would hide a real foreign holder behind
    it. So every active record is walked, and the ones belonging to this
    session are skipped by name.

    store.dump(raw=True) is the read: the store's own public export, which
    works on a read-only handle and which tools/bm_autosave.py and
    tools/bm_threads.py already use to enumerate records. Nothing read here
    is printed; only the collision answer leaves this function."""
    write_paths = _unattended_write_paths(store, project_id)
    if not write_paths:
        return None
    try:
        norm = bs._normalize_files(write_paths, root)
    except Exception as exc:
        return (UNATTENDED_FOREIGN_CLAIM,
                "the contract's allowed paths could not be resolved against "
                "%s (%s: %s), so no claim over them could be ruled out."
                % (root, type(exc).__name__, exc))
    tables = store.dump(raw=True)
    holders = {}
    for row in tables.get("records") or []:
        if row.get("state") == "active":
            holders[row.get("lifecycle_uuid")] = row
    for claim in tables.get("claims") or []:
        holder = holders.get(claim.get("lifecycle_uuid"))
        if holder is None:
            continue
        if (holder.get("session_id") or "") == session_id:
            continue
        for path in norm:
            if bs.paths_overlap(path, claim.get("path") or ""):
                return (UNATTENDED_FOREIGN_CLAIM,
                        "the active record %r (session %r) already claims "
                        "%r, which overlaps %r in this project's allowed "
                        "paths. Two writers over one path is the failure "
                        "this product exists to prevent, so the run does not "
                        "start. Wait for that record to be parked, completed "
                        "or adopted."
                        % (holder.get("name"),
                           holder.get("session_id") or "",
                           claim.get("path"), path))
    return None


def _unattended_snapshot(git, toplevel, session_id):
    """Precondition 5: a recovery snapshot exists, or one is taken now.

    Evaluated LAST of the eight, deliberately. It is the only condition
    with an effect on disk, and a preflight that refuses should leave
    nothing behind: the seven read-only conditions (the fence canary
    included: it writes only inside its own throwaway directory, cleaned
    up unconditionally, never anywhere this run's own preflight is
    judging) all have to pass before this one writes a single ref."""
    if toplevel is None:
        return None
    ok, detail = git.recovery_point(toplevel, session_id)
    if ok:
        return None
    return (UNATTENDED_NO_SNAPSHOT,
            "no recovery snapshot could be taken or found for %s (%s), so "
            "there would be nothing to rewind to. An unattended run does not "
            "start without one." % (toplevel, detail))


def unattended_preflight(store, root, project_id, session_id,
                         acknowledged=(), env=None, git=None,
                         fence_canary_runner=None):
    """The eight conditions an unattended run must meet, as a list of
    (reason_code, sentence) findings. An empty list means "start it".

    THE CHOICE, STATED. Conditions 1 and 2 (BM_FENCE_MODE=enforced and
    BM_FENCE_STRICT=1) REFUSE; this function never sets the effective policy
    for the run itself. Those variables are read by tools/bm_fence_hook.py,
    which the RUNTIME spawns as its own process from its own environment, so
    nothing this process writes into its own os.environ can reach it.
    Setting them here would produce a controller that believes enforcement
    is on while the hook doing the enforcing reads the old value, which is
    worse than refusing: it would report a protection nobody has. The
    refusal names the exact command instead.

    CONDITION 8, THE FENCE CANARY, is a different kind of check: it does
    not read a setting, it RUNS tools/bm_fence_hook.py for real (see
    _unattended_fence_canary's own docstring for the full honesty
    statement). It proves the hook binary refuses when it is actually
    invoked; it cannot prove, and is never worded to claim, that the
    runtime about to run unattended will invoke it on any real write.

    EVERY READ-ONLY CONDITION IS EVALUATED, not only the first, because the
    founder running this is about to walk away and a preflight that reveals
    one problem per attempt costs them the evening. The findings come back
    in a fixed order and the CLI raises on the first one's code, so each
    condition is still a single named refusal with a single named reason.

    `store`, `env` and `git` are three of the seams: the tests hand this a
    throwaway store, a plain dictionary and a fake repository object, so all
    eight refusals are exercised with no real environment, no git binary and
    no snapshot anywhere. `fence_canary_runner` is the fourth, threaded
    straight to _unattended_fence_canary's own `runner` parameter: None (the
    default, and what every real caller gets) really spawns the hook as a
    subprocess; a test hands in a fake to stay hermetic and fast."""
    env = os.environ if env is None else env
    git = UnattendedGitFacts() if git is None else git
    findings = []
    identity = _unattended_identity(store, project_id, session_id)
    if identity is not None:
        findings.append(identity)
    for finding in (_unattended_fence_mode(env),
                    _unattended_fence_strict(env),
                    _unattended_fence_canary(env, runner=fence_canary_runner)):
        if finding is not None:
            findings.append(finding)
    repository, toplevel = _unattended_repository(git, root)
    if repository is not None:
        findings.append(repository)
    dirty = _unattended_clean_tree(git, toplevel, acknowledged)
    if dirty is not None:
        findings.append(dirty)
    if identity is None:
        # A store that could not be read cannot be asked about claims
        # either, and asking anyway would report a second failure that is
        # really the first one wearing a different name.
        foreign = _unattended_foreign_claim(store, root, session_id,
                                            project_id)
        if foreign is not None:
            findings.append(foreign)
    if findings:
        # Nothing is written while anything else is wrong: the snapshot is
        # the one condition with an effect, so it never runs on a machine
        # this function is already about to refuse.
        return findings
    snapshot = _unattended_snapshot(git, toplevel, session_id)
    if snapshot is not None:
        findings.append(snapshot)
    return findings


def unattended_refusal_text(findings):
    """The founder-facing block for a refused unattended start: every
    finding, each one rewritten through UNATTENDED_REFUSAL_HELP so a reason
    code never reaches a person on its own (defect M08), each one carrying
    the engine's own sentence underneath it."""
    lines = ["this run was asked to start UNATTENDED, and %d of the eight "
             "safety conditions %s not met"
             % (len(findings), "is" if len(findings) == 1 else "are")]
    for code, sentence in findings:
        context, hint, next_action = UNATTENDED_REFUSAL_HELP[code]
        lines.append("")
        lines.append("  [%s] while %s" % (code, context))
        lines.append("  what is wrong: %s." % (hint,))
        lines.append("  what to do: %s" % (next_action,))
        lines.append("  detail: %s" % (sentence,))
    return "\n".join(lines)


def _refuse_unless_unattended_ready(kv, store, project_id):
    """The one call site shape both `start` and `step` use. A no-op unless
    --unattended was passed, which is what keeps interactive use identical.

    `step` is gated as well as `start` because an unattended driver is a
    loop, and a loop calling `step` directly would walk straight past a gate
    that only guarded `start`."""
    if not kv.get("unattended"):
        return
    findings = unattended_preflight(
        store, _root(), project_id, kv.get("session-id") or "",
        acknowledged=kv.get("acknowledge-modified") or [])
    if not findings:
        return
    raise bs.OwnershipRefused(
        findings[0][0], unattended_refusal_text(findings),
        details={"reasons": [code for code, _sentence in findings]})


#: The two flags the preflight adds, and the one of them that repeats. Named
#: once and spliced into both commands' flag tuples, so `start` and `step`
#: cannot drift into offering different halves of the same gate.
_UNATTENDED_FLAGS = ("unattended", "acknowledge-modified")
_UNATTENDED_REPEATABLE = ("acknowledge-modified",)
_UNATTENDED_USAGE = ("[--unattended (needs --session-id)] "
                     "[--acknowledge-modified PATH ...]")

#: R7's opt in CLI switch (PlanGate's own docstring states why it is opt
#: in). A bare boolean, no value, the same shape --unattended's own flag
#: takes; --require-plan-gate makes tools/bm_plan.py's four clause
#: invariant live for this one `start` or `step` call.
_PLAN_GATE_FLAGS = ("require-plan-gate",)
_PLAN_GATE_USAGE = "[--require-plan-gate]"


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

_START_FLAGS = ("project", "outcome", "done-definition", "workflow-version",
                "controller-id", "max-steps", "json") + _ACTOR_FLAGS \
               + _UNATTENDED_FLAGS + _PLAN_GATE_FLAGS


def _start_usage():
    return ("usage: start --project ID [--outcome T --done-definition T] "
            "[--workflow-version N] --controller-id ID [--max-steps N] "
            "--actor-name NAME [--actor-type human|model] "
            "[--session-id SID] [--json] " + _UNATTENDED_USAGE + " "
            + _PLAN_GATE_USAGE)


def cmd_start(argv):
    """Begins when the project has no controller run at all (get_run
    returns None); resumes otherwise, whatever state that run is in,
    including a terminal one (step()'s own first call reports 'run is
    terminal; nothing to do' and _drive_until_parked stops there).
    Matches Writer A's own cross-writer note: start should call
    get_run(project_id) first and resume if a non-terminal run already
    exists, calling begin() only when it returns None. The resume path
    drives through _drive_until_parked, not engine.run_to_completion()
    directly; see that helper's own docstring for the reproduced reason."""
    _pos, kv = _parse(
        argv, _START_FLAGS,
        wants_value=("project", "outcome", "done-definition",
                     "workflow-version", "controller-id",
                     "max-steps") + _ACTOR_FLAGS,
        repeatable=_UNATTENDED_REPEATABLE)
    usage = _start_usage()
    project_id = _require(kv, "project", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    max_steps = _int_flag(kv, "max-steps", usage, default=500, minimum=1)
    store = _store()
    try:
        # The unattended gate, BEFORE the run row is read and long before
        # anything is dispatched. A no-op without --unattended, which is
        # what leaves interactive `start` byte for byte what it was.
        _refuse_unless_unattended_ready(kv, store, project_id)
        run = store.get_run(project_id, raw=True)
        engine = _engine(store, controller_id, actor, actor["session_id"],
                         require_plan_gate=bool(kv.get("require-plan-gate")))
        if run is None:
            outcome = _require(kv, "outcome", usage)
            done_definition = _require(kv, "done-definition", usage)
            workflow_version = _int_flag(
                kv, "workflow-version", usage, default=1, minimum=1)
            run = engine.begin(project_id, outcome, done_definition,
                               workflow_version)
            if kv.get("json"):
                _print_json(run)
                return EXIT_OK
            _out("controller run %s started for project %s (state %s)"
                 % (run["run_id"], project_id, run["state"]))
            _out("plan the unit graph next: %s plan --project %s "
                 "--units-file FILE --controller-id %s --actor-name %s"
                 % (_cmd(), project_id, controller_id, actor["actor_name"]))
            return EXIT_OK
        trace = _drive_until_parked(engine, project_id, max_steps)
    finally:
        store.close()
    exit_code = _exit_for_stop_reason(trace[-1]["stop_reason"] if trace
                                      else None)
    if kv.get("json"):
        _print_json(trace)
        return exit_code
    _report_trace(trace, project_id)
    return exit_code


# ---------------------------------------------------------------------------
# step
# ---------------------------------------------------------------------------

_STEP_FLAGS = ("project", "controller-id", "json") + _ACTOR_FLAGS \
              + _UNATTENDED_FLAGS + _PLAN_GATE_FLAGS


def cmd_step(argv):
    """One pass of the resumable loop through the CLI. Refuses ('no-run')
    if the project has no controller run yet, the same refusal
    ControllerEngine.step() itself raises; this command adds no pre-check
    of its own to the ordinary path, matching the thin-CLI law.

    The ONE exception, and it is opt-in by flag: `--unattended` runs the
    preflight first (see _refuse_unless_unattended_ready). `step` is gated
    as well as `start` because an unattended driver is a loop, and a loop
    calling `step` directly would walk past a gate that only guarded
    `start`. Without the flag this line is a return and nothing else, so
    the sentence above stays true of every interactive call."""
    _pos, kv = _parse(argv, _STEP_FLAGS,
                      wants_value=("project", "controller-id") + _ACTOR_FLAGS,
                      repeatable=_UNATTENDED_REPEATABLE)
    usage = ("usage: step --project ID --controller-id ID --actor-name "
             "NAME [--actor-type human|model] [--session-id SID] [--json] "
             + _UNATTENDED_USAGE + " " + _PLAN_GATE_USAGE)
    project_id = _require(kv, "project", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        _refuse_unless_unattended_ready(kv, store, project_id)
        engine = _engine(store, controller_id, actor, actor["session_id"],
                         require_plan_gate=bool(kv.get("require-plan-gate")))
        summary = engine.step(project_id)
    finally:
        store.close()
    exit_code = _exit_for_stop_reason(summary.get("stop_reason"))
    if kv.get("json"):
        _print_json(summary)
        return exit_code
    _out("run %s: state %s" % (summary["run_id"], summary["state"]))
    _out("dispatched: %s" % (", ".join(summary["dispatched"]) or "(none)"))
    _out("completed: %s" % (", ".join(summary["completed"]) or "(none)"))
    if summary.get("note"):
        _out("note: %s" % summary["note"])
    if summary.get("stop_reason"):
        # The one machine-readable word for why this wave stopped. --json
        # already prints the whole summary, so the field appears there for
        # free; this is the line a founder reads.
        _out("reason: %s" % summary["stop_reason"])
    return exit_code


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

_PLAN_FLAGS = ("project", "run", "units-file", "units-json", "controller-id",
              "json") + _ACTOR_FLAGS


def cmd_plan(argv):
    """The unit graph is the one judgement the design leaves to the
    orchestrating model, never to this harness (design section 11's own
    closing line); this command only carries the model's already-built
    graph into engine.plan(), which flips PLANNING -> READY inside
    upsert_units' own transaction. Reads get_run FIRST, always, so a
    project with no controller run at all is a clean refusal here rather
    than a crash inside engine.plan() (which assumes a run exists)."""
    _pos, kv = _parse(
        argv, _PLAN_FLAGS,
        wants_value=("project", "run", "units-file", "units-json",
                     "controller-id") + _ACTOR_FLAGS)
    usage = ("usage: plan --project ID [--run RUN_ID] (--units-file PATH | "
             "--units-json TEXT) --controller-id ID --actor-name NAME "
             "[--actor-type human|model] [--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    units = _load_units(kv, usage)
    store = _store()
    try:
        run = store.get_run(project_id, raw=True)
        if run is None:
            _err("bm_controller: refused. project %r has no controller "
                 "run yet. Begin one first: %s start --project %s "
                 "--outcome T --done-definition T --controller-id ID "
                 "--actor-name NAME" % (project_id, _cmd(), project_id))
            return EXIT_REFUSED
        run_id = kv.get("run") or run["run_id"]
        engine = _engine(store, controller_id, actor, actor["session_id"])
        result = engine.plan(project_id, run_id, units)
    finally:
        store.close()
    if kv.get("json"):
        _print_json({"run_id": run_id, "count": result["count"],
                     "skipped": result["skipped"],
                     "cancelled_dispatches": result["cancelled_dispatches"]})
        return EXIT_OK
    _out("planned %d unit(s) for run %s" % (result["count"], run_id))
    # A founder who drops a unit needs to see that its open dispatch was
    # cancelled, because the result they may already have asked a worker
    # for can no longer be recorded against it.
    if result["skipped"]:
        _out("dropped %d unit(s) from the graph: %s"
             % (len(result["skipped"]), ", ".join(result["skipped"])))
    if result["cancelled_dispatches"]:
        _out("cancelled %d open dispatch(es) belonging to those units; a "
             "result for one of them will now be refused"
             % len(result["cancelled_dispatches"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# record-result
# ---------------------------------------------------------------------------

_RECORD_RESULT_FLAGS = ("project", "dispatch-id", "worker-claim", "artifact",
                        "tokens", "minutes", "controller-id",
                        "json") + _ACTOR_FLAGS


def cmd_record_result(argv):
    """The far end of RecordIntentWorker's own 'dispatch means
    record-intent-and-await' (design section 4): the orchestrating model,
    having done the unit's work, calls this to hand the result back.
    Wraps ControllerEngine.receive_result(). Reads get_run FIRST for the
    same reason `plan` does: receive_result assumes a run exists and
    would otherwise crash uncleanly for a project with none."""
    _pos, kv = _parse(
        argv, _RECORD_RESULT_FLAGS,
        wants_value=("project", "dispatch-id", "worker-claim", "tokens",
                     "minutes", "controller-id") + _ACTOR_FLAGS,
        repeatable=("artifact",))
    usage = ("usage: record-result --project ID --dispatch-id ID "
             "[--worker-claim T] [--artifact PATH ...] [--tokens N] "
             "[--minutes N] --controller-id ID --actor-name NAME "
             "[--actor-type human|model] [--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    dispatch_id = _require(kv, "dispatch-id", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    tokens = _int_flag(kv, "tokens", usage, default=0, minimum=0)
    minutes = _int_flag(kv, "minutes", usage, default=0, minimum=0)
    artifacts = kv.get("artifact") or []
    cost = ({"tokens": tokens, "minutes": minutes} if (tokens or minutes)
           else None)
    store = _store()
    try:
        if store.get_run(project_id, raw=True) is None:
            _err("bm_controller: refused. no controller run for project "
                 "%r yet; there is nothing to record a result against."
                 % (project_id,))
            return EXIT_REFUSED
        engine = _engine(store, controller_id, actor, actor["session_id"])
        # The settle path's own verdict comes back in `settled`, so this
        # command can tell the founder that the result it just recorded
        # also delivered the run, or that their own done-definition failed.
        # Round 4 computed all of that inside _settle_after_wave and threw
        # it away, so `record-result` printed "unit u1 accepted" and
        # nothing else (REFUTATION-4 LV L4-F1).
        settled = {}
        outcome = engine.receive_result(
            project_id, dispatch_id, kv.get("worker-claim") or "",
            artifacts, cost=cost, summary=settled)
    finally:
        store.close()
    if kv.get("json"):
        payload = {"outcome": outcome}
        payload.update(settled)
        _print_json(payload)
        return EXIT_OK
    if outcome == "rejected":
        _out("dispatch %s rejected; the unit re-queues for one retry or "
             "escalates, per the circuit breaker" % dispatch_id)
    elif outcome == "held":
        _out("dispatch %s recorded and HELD: nothing was judged, no "
             "rollback ran and the fence is still held" % dispatch_id)
    else:
        _out("unit %s accepted (dispatch %s)" % (outcome, dispatch_id))
    if settled.get("note"):
        _out("note: %s" % settled["note"])
    if settled.get("stop_reason"):
        _out("reason: %s" % settled["stop_reason"])
    founder_gated = settled.get("founder_gated")
    if founder_gated:
        _out("founder-gated remainder: %d open human step(s), %d failed "
             "unit(s)" % (len(founder_gated.get("human_steps") or []),
                         len(founder_gated.get("failed_units") or [])))
    return EXIT_OK


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

_STATUS_FLAGS = ("project", "json", "raw")


def cmd_status(argv):
    """One screen, read only: run id and state, the outcome and done
    definition, a unit count by status, the OPEN dispatch id for any unit
    currently DISPATCHED or RESULT_IN (the piece of information a caller
    otherwise has no way to recover: RecordIntentWorker's printed brief
    carries no dispatch_id of its own, since a brief is the WORKER's own
    input and dispatch_id is bookkeeping the controller keeps; this is
    where record-result's own --dispatch-id comes from), open human
    steps, the spend verdict, and the most recent checkpoint. Mirrors
    tools/bm_autonomy.py's own status shape: raw local display by
    default, redacted by default under --json unless --raw is also
    passed."""
    _pos, kv = _parse(argv, _STATUS_FLAGS, wants_value=("project",))
    usage = "usage: status --project ID [--json] [--raw]"
    project_id = _require(kv, "project", usage)
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    store = _read_store()
    try:
        run = store.get_run(project_id, raw=want_raw)
        if run is None:
            _err("bm_controller: refused. no controller run for project "
                 "%r yet. Run start first." % (project_id,))
            return EXIT_REFUSED
        units = store.list_units(run["run_id"], raw=want_raw)
        by_status = {}
        for u in units:
            by_status.setdefault(u["status"], []).append(u["unit_id"])
        open_dispatches = {}
        for u in units:
            if u["status"] not in ("DISPATCHED", "RESULT_IN"):
                continue
            dispatches = store.list_dispatches(u["unit_id"], raw=want_raw)
            open_d = [d for d in dispatches
                     if d["status"] in ("DISPATCHED", "RESULT_IN")]
            if open_d:
                open_dispatches[u["unit_id"]] = open_d[-1]["dispatch_id"]
        open_steps = store.list_human_steps(project_id, resolved=False,
                                            raw=want_raw)
        checkpoints = store.recent_checkpoints(project_id, limit=1,
                                               raw=want_raw)
        spend = store.spend_totals(project_id)
    finally:
        store.close()
    if kv.get("json"):
        _print_json({
            "run": run,
            "units_by_status": dict((k, len(v)) for k, v in
                                    by_status.items()),
            "open_dispatches": open_dispatches,
            "open_human_steps": len(open_steps),
            "spend": spend,
            "last_checkpoint": checkpoints[0] if checkpoints else None,
        })
        return EXIT_OK
    _out("project %s: run %s, state %s (workflow version %s)"
         % (project_id, run["run_id"], run["state"],
            run.get("workflow_version")))
    _out("outcome: %s" % (run.get("outcome") or "(none)"))
    _out("done definition: %s" % (run.get("done_definition") or "(none)"))
    if by_status:
        _out("units: %d total" % len(units))
        for status in bs.CONTROLLER_UNIT_STATES:
            if status in by_status:
                _out("  %s: %d" % (status, len(by_status[status])))
    else:
        _out("units: (none planned yet)")
    if open_dispatches:
        _out("open dispatches (record a result for each):")
        for unit_id in sorted(open_dispatches):
            _out("  %s: dispatch %s" % (unit_id, open_dispatches[unit_id]))
    _out("spend verdict: %s" % spend["verdict"])
    _out("open human steps: %d" % len(open_steps))
    if checkpoints:
        cp = checkpoints[0]
        _out("last checkpoint: %s kind=%s by controller %s"
             % (cp.get("created_at"), cp.get("kind") or "(none)",
                cp.get("controller_id")))
    else:
        _out("last checkpoint: (none recorded)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

_STOP_FLAGS = ("project", "reason", "controller-id", "json") + _ACTOR_FLAGS


def cmd_stop(argv):
    """The founder's kill switch on the controller run itself (distinct
    from bm-autonomy stop, which ends the CONTRACT): drains in-flight
    work and releases every held fence via ControllerEngine.stop(), never
    a hard refusal, an idempotent no-op when there is no run at all or it
    is already terminal, matching bm_autonomy.py's own stop-needs-no-
    drama posture."""
    _pos, kv = _parse(
        argv, _STOP_FLAGS,
        wants_value=("project", "reason", "controller-id") + _ACTOR_FLAGS)
    usage = ("usage: stop --project ID [--reason T] --controller-id ID "
             "--actor-name NAME [--actor-type human|model] "
             "[--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    reason = kv.get("reason") or "founder requested stop"
    store = _store()
    try:
        run_before = store.get_run(project_id, raw=True)
        engine = _engine(store, controller_id, actor, actor["session_id"])
        engine.stop(project_id, reason=reason)
        run_after = store.get_run(project_id, raw=True)
    finally:
        store.close()
    if run_before is None:
        _out("project %s has no controller run; nothing to stop"
             % project_id)
        return EXIT_OK
    if kv.get("json"):
        _print_json(run_after)
        return EXIT_OK
    _out("project %s controller run %s: %s -> %s"
         % (project_id, run_after["run_id"], run_before["state"],
            run_after["state"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------

_ADOPT_FLAGS = ("project", "note", "json") + _ACTOR_FLAGS


def cmd_adopt(argv):
    """The one deliberate takeover path the not-driver refusal names
    (L09 GAP 2): make THIS invocation's session the recorded driver of
    the project's current run, displacing the previous one by name and
    recording the handover durably, mirroring `bm-store adopt` for a
    fence record. Calls the store directly rather than the engine, the
    same thin-CLI law `resume` and `complete` follow: adoption is a
    founder-facing ownership move, never a controller self-move."""
    _pos, kv = _parse(argv, _ADOPT_FLAGS,
                      wants_value=("project", "note") + _ACTOR_FLAGS)
    usage = ("usage: adopt --project ID --actor-name NAME "
             "[--actor-type human|model] [--session-id SID] "
             "[--note TEXT] [--json]")
    project_id = _require(kv, "project", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        out = store.adopt_run(project_id, actor["session_id"], actor,
                              note=kv.get("note") or "")
    finally:
        store.close()
    if kv.get("json"):
        _print_json(out)
        return EXIT_OK
    if out["adopted"]:
        _out("project %s controller run %s: driver handed over from "
             "session %s to session %s"
             % (project_id, out["run_id"],
                out["previous_session_id"] or "(none recorded)",
                out["session_id"]))
    else:
        _out("project %s controller run %s: session %s already drives "
             "this run; nothing to adopt"
             % (project_id, out["run_id"], out["session_id"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------

_RESUME_FLAGS = ("project", "reason", "json") + _ACTOR_FLAGS


def cmd_resume(argv):
    """PAUSED -> READY, the founder's own reverse of a pause (design
    section 1: 'the reverse edge restores the prior running state'). Calls
    set_run_state directly rather than constructing an engine: see the
    section note above this block for why."""
    _pos, kv = _parse(argv, _RESUME_FLAGS,
                      wants_value=("project", "reason") + _ACTOR_FLAGS)
    usage = ("usage: resume --project ID [--reason T] --actor-name NAME "
             "[--actor-type human|model] [--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        run = store.get_run(project_id, raw=True)
        if run is None:
            _err("bm_controller: refused. no controller run for project "
                 "%r yet. Nothing to resume." % (project_id,))
            return EXIT_REFUSED
        if run["state"] != "PAUSED":
            _out("project %s controller run %s is already %s, not "
                 "PAUSED. Nothing to do."
                 % (project_id, run["run_id"], run["state"]))
            return EXIT_OK
        result = store.set_run_state(
            run["run_id"], "READY", actor,
            kv.get("reason") or "founder resume", actor["session_id"])
    finally:
        store.close()
    if kv.get("json"):
        _print_json(result)
        return EXIT_OK
    _out("project %s controller run %s: PAUSED -> %s"
         % (project_id, run["run_id"], result["state"]))
    _out("continue it with: %s start --project %s --controller-id ID "
         "--actor-name NAME" % (_cmd(), project_id))
    return EXIT_OK


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

_RESTORE_FLAGS = ("project", "checkpoint", "controller-id", "json") \
    + _ACTOR_FLAGS


def cmd_restore(argv):
    """F3 (durable checkpoints): reconstruct a run's position from one
    named checkpoint. Read-only, so this is safe to call from a session
    that shares nothing with whichever one recorded the checkpoint -- the
    controller-id and actor below only satisfy ControllerEngine's
    constructor and are never used to write anything. Opens the store
    through _read_store() (bs.ReadOnlyStore), not _store(): constructing
    a writable Store is itself a write (WAL/SHM sidecars, a possible
    schema migration), and restore_checkpoint only ever calls the three
    read methods ReadOnlyStore already exposes for it (get_checkpoint,
    get_run, list_units), so pure_read in tools/bm_effects.py is a
    measured claim here, not an aspiration."""
    _pos, kv = _parse(argv, _RESTORE_FLAGS,
                      wants_value=("project", "checkpoint", "controller-id")
                      + _ACTOR_FLAGS)
    usage = ("usage: restore --project ID --checkpoint ID --controller-id "
             "ID --actor-name NAME [--actor-type human|model] "
             "[--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    checkpoint_id = _require(kv, "checkpoint", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    store = _read_store()
    try:
        engine = _engine(store, controller_id, actor, actor["session_id"])
        result = engine.restore_checkpoint(project_id, checkpoint_id)
    finally:
        store.close()
    if kv.get("json"):
        _print_json(result)
        return EXIT_OK
    cp = result["checkpoint"]
    _out("checkpoint %s (%s) recorded %s by controller %s"
         % (checkpoint_id, cp.get("kind") or "(none)",
            cp.get("created_at"), cp.get("controller_id")))
    _out("run state at restore time: %s"
         % (result["run_state"] or "(no run)"))
    _out("units: %d" % len(result["units"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# redirect
# ---------------------------------------------------------------------------

_REDIRECT_FLAGS = ("project", "note", "controller-id", "json") + _ACTOR_FLAGS


def cmd_redirect(argv):
    """F1 (mid-stream steering): inject a course correction into a LIVE
    run without discarding anything already finished, by routing it into
    the plan as a version bump (ControllerEngine.inject_redirect, tools/
    bm_plan.py's PlanStore.record_redirect). --controller-id and the
    actor flags satisfy ControllerEngine's constructor the same way
    `restore` above needs them; the actor named here is who is STEERING
    the run, which may well be a different person than whoever is
    driving it."""
    _pos, kv = _parse(argv, _REDIRECT_FLAGS,
                      wants_value=("project", "note", "controller-id")
                      + _ACTOR_FLAGS)
    usage = ("usage: redirect --project ID --note TEXT --controller-id ID "
             "--actor-name NAME [--actor-type human|model] "
             "[--session-id SID] [--json]")
    project_id = _require(kv, "project", usage)
    note = _require(kv, "note", usage)
    controller_id = _require(kv, "controller-id", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        engine = _engine(store, controller_id, actor, actor["session_id"])
        result = engine.inject_redirect(project_id, note, actor)
    finally:
        store.close()
    if kv.get("json"):
        _print_json(result)
        return EXIT_OK
    _out("project %s redirected: plan now at version %s (checkpoint %s)"
         % (project_id, result["new_plan_version"], result["checkpoint_id"]))
    return EXIT_OK


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

_COMPLETE_FLAGS = ("project", "reason", "json") + _ACTOR_FLAGS


def cmd_complete(argv):
    """DELIVERABLE_READY -> COMPLETE, a founder action, never a controller
    self-move (design section 1's own words for this exact transition):
    calls set_run_state directly, the same reasoning `resume` states
    above. Idempotent: calling this again once already COMPLETE writes
    nothing new and still exits 0, matching bm_autonomy.py's own
    repeated-stop convention."""
    _pos, kv = _parse(argv, _COMPLETE_FLAGS,
                      wants_value=("project", "reason") + _ACTOR_FLAGS)
    usage = ("usage: complete --project ID [--reason T] --actor-name "
             "NAME [--actor-type human|model] [--session-id SID] "
             "[--json]")
    project_id = _require(kv, "project", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        run = store.get_run(project_id, raw=True)
        if run is None:
            _err("bm_controller: refused. no controller run for project "
                 "%r yet." % (project_id,))
            return EXIT_REFUSED
        before_state = run["state"]
        result = store.set_run_state(
            run["run_id"], "COMPLETE", actor,
            kv.get("reason") or "founder accepted the deliverable",
            actor["session_id"])
    finally:
        store.close()
    if kv.get("json"):
        _print_json(result)
        return EXIT_OK
    if not result["changed"]:
        _out("project %s controller run %s is already COMPLETE. Nothing "
             "to do." % (project_id, run["run_id"]))
        return EXIT_OK
    _out("project %s controller run %s: %s -> COMPLETE"
         % (project_id, run["run_id"], before_state))
    return EXIT_OK


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "start": cmd_start,
    "step": cmd_step,
    "plan": cmd_plan,
    "record-result": cmd_record_result,
    "status": cmd_status,
    "stop": cmd_stop,
    "adopt": cmd_adopt,
    "resume": cmd_resume,
    "complete": cmd_complete,
    "restore": cmd_restore,
    "redirect": cmd_redirect,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("bm_controller.py: the command line for U2, the durable "
             "Full-Auto controller (schema 15, tools/bm_store.py).")
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return EXIT_OK
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_controller: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return EXIT_USAGE
    try:
        return COMMANDS[cmd](argv[1:])
    except bs.BMStoreError as e:
        # Covers OwnershipRefused, the whole store and engine refusal
        # vocabulary (no-run, no-live-contract, run-exists, illegal-
        # state-move, dispatch-exists, already-resulted, unit-not-
        # verifiable, dangling-dependency, dependency-cycle, and every
        # other refusal named above), never restated in this file's own
        # words: the store's message already names the fix.
        _err("bm_controller: refused: %s" % e)
        return EXIT_REFUSED
    except ValueError as e:
        _err("bm_controller: refused: %s" % e)
        return EXIT_REFUSED


def cli():
    """Console-script entry point; see tools/bm_autonomy.py's cli() for
    why this exists beside main() rather than duplicating the
    sys.exit(main(...)) line: a packaging entry point takes no
    arguments."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

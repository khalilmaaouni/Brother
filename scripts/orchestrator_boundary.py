#!/usr/bin/env python3
"""ORCH-18 of the 1.0.20 orchestration control plane: a mechanical boundary
for invariant 6 and invariant 8, in front of code this module does not own
and does not change.

WHY THIS EXISTS. An advisory pass asked which invariant the plan only
ASSERTS rather than ENFORCES and found two, both verified against this
repository's own code:

  INVARIANT 6, no direct agent merge to canonical. scripts/integrate.py's
  integrate_one() takes a cooperative lock and runs `git merge`, which
  serialises callers who go through that one function. It does nothing
  about a process that runs `git merge`, `git push`, `git update-ref`, or
  any other ref-mutating command directly: worktrees isolate working
  FILES but share repository administration, and scripts/worktree_lane.py's
  own lane lock is a threading.Lock, which is in-process only and cannot
  exclude a second process at all. The invariant held, until now, only by
  everyone's good behaviour.

  INVARIANT 8, no RED action automated. scripts/fable_authority.py's
  decide() classifies and queues a RED decision correctly, but nothing
  OBLIGES a caller to invoke it before acting. An available helper is not
  a gate.

WHAT THIS MODULE IS. The single choke point every side effect in this run
is meant to pass through: authorize() grants a single-use Authorization
tied to one epoch, guard_canonical() refuses a git invocation that would
mutate a canonical ref unless the caller holds the integrator capability.
Both compose orchestrator_authority.py and fable_authority.py rather than
reimplementing either: authority liveness is that module's own question,
answered by its own check() function, and RED/AMBER/GREEN classification
is fable_authority's own question, answered by its own classify(). This
module adds no third copy of either judgement, only the obligation to ask.

HONESTY REQUIREMENT. This is a Brother-level control, not an operating
system sandbox. It proves who was authorized, at what epoch, and refuses
the specific paths that route through authorize() and guard_canonical(). It
CANNOT stop a process that shells out to git directly and never calls
either function: nothing short of an OS-level permission boundary can do
that, and this module does not claim to be one. Every caller in this
estate's own dispatch and integration path is expected to route through
here; a caller that skips this module entirely defeats it exactly as
completely as a caller that skips any other boundary in this repository
skips it. Overclaiming this as airtight would be worse than the real gap:
it would give a reader a reason to stop looking for the paths that still
bypass it.

Python 3.9 floor, standard library only, no network.
"""
import subprocess
import time

import fable_authority
import orchestrator_authority
import orchestrator_invariants

#: This module's own action vocabulary is the dispatcher's ACTIONS
#: (orchestrator_invariants.ACTIONS, imported rather than retyped) plus the
#: one action this boundary adds meaning for: a canonical ref write. An
#: action name outside this set is refused by authorize(), never treated
#: as permitted by default.
CANONICAL_WRITE = "CANONICAL-WRITE"
KNOWN_ACTIONS = orchestrator_invariants.ACTIONS | frozenset({CANONICAL_WRITE})

#: The only branch name this module treats as canonical. A single name,
#: not a discovery of the repository's default branch, because guessing a
#: canonical branch name is exactly the kind of default this estate
#: refuses everywhere else.
# ponytail: one hardcoded branch name; make it a parameter if this estate
# ever integrates onto something other than main.
CANONICAL_BRANCH_NAMES = frozenset({"main", "refs/heads/main"})

#: git subcommands that mutate a ref whenever they run at all, whatever
#: their target: push and update-ref always write, and merge always moves
#: the current branch (the one case this boundary exists to catch is the
#: caller running it directly against the canonical checkout).
_ALWAYS_MUTATING_SUBCOMMANDS = frozenset({"push", "merge", "update-ref"})


class BoundaryRefused(Exception):
    """A gate in this module refused to grant or honour an action.
    `.invariant` names which one; `.reason` is the human-readable why. This
    is the normal, expected outcome of a refused RED action or a denied
    direct write, never a bug: a caller records it and moves past, it does
    not treat this as reason to stop the whole run."""

    def __init__(self, invariant, reason):
        self.invariant = invariant
        self.reason = reason
        super().__init__("%s: %s" % (invariant, reason))


class BoundaryUnreadable(Exception):
    """A check this module depends on (the authority store, the RED queue,
    a `git rev-parse` this module ran to answer its own question) could not
    be read or written. Never treated as "so the action is fine": see
    orchestrator_authority's own AuthorityUnreadable, which this module
    wraps rather than lets an unreadable store read as a free scope."""


class IntegratorToken:
    """The integrator capability, as an object, not a boolean flag or a
    comment. Only canonical_write_token() constructs one; nothing else in
    this module, or anywhere that has not imported this exact function,
    can produce a value is_integrator() accepts. Holding one and passing
    it into authorize() is what makes a caller 'the integrator' for
    guard_canonical()'s purposes; everything else, orchestrators
    included, may only propose through scripts/integrate.py."""

    __slots__ = ("instance",)

    def __init__(self, instance):
        self.instance = str(instance)


def canonical_write_token(instance):
    """Issue the integrator capability to `instance`. This function does
    not decide WHO should be the integrator for a real run; wiring the
    one real integration process to be the only caller of this function
    is a deployment decision outside this module's two owned files."""
    return IntegratorToken(instance)


def is_integrator(token, instance=None):
    """True only for a genuine IntegratorToken (never a lookalike object,
    a string, or a truthy value someone hoped would pass), and, when
    `instance` is given, only when that token was issued to exactly this
    instance."""
    if not isinstance(token, IntegratorToken):
        return False
    if instance is not None and token.instance != str(instance):
        return False
    return True


class Authorization:
    """A single-use grant returned by authorize(). Using it, through
    guard_canonical() or by calling its own _consume() directly, RE-CHECKS
    that the epoch it was granted under is still the current one AT THE
    MOMENT OF USE, not merely at grant time: a stale-epoch check run only
    once, when the authorization is handed out, is exactly the gap
    invariant 6's own docstring describes a woken, stale-epoch caller
    exploiting. A second use of the same Authorization is refused, whether
    or not the epoch is still live: 'single use' means once, not 'valid
    until the epoch changes'."""

    __slots__ = ("action", "run_id", "scope", "instance", "epoch",
                 "granted_at", "store_path", "integrator_token", "_used")

    def __init__(self, action, run_id, scope, instance, epoch, granted_at,
                 store_path, integrator_token=None):
        self.action = action
        self.run_id = run_id
        self.scope = scope
        self.instance = instance
        self.epoch = epoch
        self.granted_at = granted_at
        self.store_path = store_path
        self.integrator_token = integrator_token
        self._used = False

    def _consume(self, now=None):
        """Mark this authorization used, after proving it is still good.
        Raises BoundaryRefused, invariant 'single use', on a second call.
        Raises BoundaryRefused, invariant 'no stale authority', when the
        epoch this authorization carries is no longer the current one.
        Raises BoundaryUnreadable when the authority store cannot be read
        at all: an unreadable store is never read as 'still holds it'."""
        if self._used:
            raise BoundaryRefused(
                "single use",
                "this authorization for action %r (run %s, scope %s, "
                "instance %s) was already used once; authorize() again "
                "for another action, a granted authorization is not "
                "reusable" % (self.action, self.run_id, self.scope,
                              self.instance))
        try:
            live = orchestrator_authority.check(
                self.store_path, self.run_id, self.scope, self.instance,
                self.epoch, now=now)
        except orchestrator_authority.AuthorityUnreadable as exc:
            raise BoundaryUnreadable(
                "could not re-check authority at the moment of use for "
                "run %s scope %s: %s" % (self.run_id, self.scope, exc)
            ) from exc
        if not live:
            raise BoundaryRefused(
                "no stale authority",
                "the epoch this authorization was granted under (%s) for "
                "instance %s over scope %s of run %s is no longer the "
                "current, live epoch at the moment of use; an "
                "authorization that outlives its epoch is refused here, "
                "it is never honoured on the strength of when it was "
                "granted" % (self.epoch, self.instance, self.scope,
                             self.run_id))
        self._used = True


def _action_parts(action):
    """(name, text): the action's identifier, checked against
    KNOWN_ACTIONS, and the free-text description fable_authority.classify()
    reads. `action` may be a plain string, used as both, or a mapping with
    'name' and optionally 'text' (defaulting to 'name')."""
    if isinstance(action, dict):
        name = action.get("name")
        text = action.get("text", name)
    else:
        name = action
        text = action
    return name, text


def authorize(action, *, store_path, run_id, scope, instance, epoch,
              task_state=None, now=None, integrator_token=None,
              red_queue_path=None):
    """The single gate every side effect in this run passes through
    before acting. Grants a single-use Authorization, or raises
    BoundaryRefused naming the invariant it enforces:

      'unknown action'          action names something outside
                                 KNOWN_ACTIONS (or task_state names
                                 something outside orchestrator_invariants.
                                 TASK_STATES); an unrecognised name is
                                 refused, never defaulted to permitted.
      'no stale authority'      `instance` does not hold `scope` of
                                 `run_id` at exactly `epoch`, right now,
                                 checked atomically with the grant through
                                 orchestrator_authority.check().
      'no RED action automated' fable_authority.classify() reads this
                                 action's text as RED. The action is
                                 queued through fable_authority.queue_red()
                                 for the founder and never executed. This
                                 refusal is scoped to this one action: it
                                 never halts or parks any other,
                                 independent authorize() call.

    Raises BoundaryUnreadable when the authority store cannot be read, or
    when a RED action could not even be queued (the refusal still stands;
    an unqueued RED record is a second, worse fact, not a reason to let
    the action through)."""
    name, text = _action_parts(action)
    if name not in KNOWN_ACTIONS:
        raise BoundaryRefused(
            "unknown action",
            "action %r is not a recognised action name; an unrecognised "
            "action is refused, it is never treated as permitted by "
            "default" % (name,))
    if task_state is not None and task_state not in orchestrator_invariants.TASK_STATES:
        raise BoundaryRefused(
            "unknown action",
            "task_state %r is not a recognised task state; an "
            "unrecognised state is refused the same way an unrecognised "
            "action name is" % (task_state,))
    try:
        live = orchestrator_authority.check(store_path, run_id, scope,
                                            instance, epoch, now=now)
    except orchestrator_authority.AuthorityUnreadable as exc:
        raise BoundaryUnreadable(
            "could not confirm authority before granting an authorization "
            "for run %s scope %s: %s" % (run_id, scope, exc)) from exc
    if not live:
        raise BoundaryRefused(
            "no stale authority",
            "instance %r does not hold scope %r of run %r at epoch %r "
            "right now; authorize() grants nothing on a stale or unheld "
            "epoch" % (instance, scope, run_id, epoch))
    label, reason = fable_authority.classify(text)
    # J091 (wave-3 Jev seam, high risk): a second opinion on this action's
    # risk framing, beside fable_authority.classify()'s own keyword list,
    # which stays sole authority -- see check_founder_decision_risk()'s
    # own docstring. Shadow-only, off by default; any failure here must
    # never affect the real RED/AMBER decision this function's callers
    # rely on, so it is wrapped exactly like every other optional seam
    # call site in this codebase.
    try:
        import jev_checks
        import jev_seam
        jev_checks.check_founder_decision_risk(
            text, label,
            seams_config=jev_seam.load_seams_config(),
            registry=jev_seam.load_registry(),
            ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
        )  # C1: return value intentionally discarded, shadow-only by contract
    except Exception:
        pass  # sbe: allow-silent this seam is advisory only, never worth delaying the real RED/AMBER gate
    if label == fable_authority.RED:
        try:
            fable_authority.queue_red(text, reason, session=instance,
                                      path=red_queue_path)
        except OSError as exc:
            raise BoundaryUnreadable(
                "action %r classified RED (%s) and is refused, but the "
                "queued record for the founder could not be written: %s"
                % (text, reason, exc)) from exc
        raise BoundaryRefused(
            "no RED action automated",
            "action %r classified RED (%s); queued for the founder, "
            "never executed. This refusal is scoped to this one action, "
            "it does not stop any unrelated action from being authorized"
            % (text, reason))
    granted_at = time.time() if now is None else now
    return Authorization(action=name, run_id=run_id, scope=scope,
                         instance=instance, epoch=epoch,
                         granted_at=granted_at, store_path=store_path,
                         integrator_token=integrator_token)


def _positional(args):
    return [a for a in args if not a.startswith("-")]


def _current_branch(cwd):
    """The checked-out branch name in `cwd`, via `git rev-parse
    --abbrev-ref HEAD`. Raises BoundaryUnreadable on any failure: this
    exists only to answer the 'commit directly onto canonical' question,
    and an unanswerable question there is never read as 'not canonical'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd,
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundaryUnreadable(
            "could not determine the current branch in %s: %s"
            % (cwd, exc)) from exc
    if result.returncode != 0:
        raise BoundaryUnreadable(
            "git rev-parse --abbrev-ref HEAD failed in %s: %s"
            % (cwd, (result.stderr or result.stdout or "").strip()))
    return result.stdout.strip()


def _is_canonical_mutation(argv, cwd=None):
    """True when this git invocation would mutate a canonical ref.

    Matches the exact subcommand (argv[0]) and its flags, never a
    substring anywhere in argv: `git merge-base a b` and `git log --merge`
    both contain the letters 'merge' and are both read-only, so a
    substring test on the word 'merge' would misclassify them. Comparing
    argv[0] by exact equality is what refuses that trap.

    'commit' is only checked when `cwd` is given: without it, this
    function cannot ask what branch is checked out, and it does not guess.
    See the module docstring's HONESTY REQUIREMENT: this is a known,
    named gap, not a silent one.
    # ponytail: no cwd means 'commit' is never flagged here; pass cwd
    # when the caller actually knows the canonical checkout's path.
    """
    args = list(argv)
    if args and args[0] == "git":
        args = args[1:]
    if not args:
        return False
    sub = args[0]
    rest = args[1:]
    if sub in _ALWAYS_MUTATING_SUBCOMMANDS:
        return True
    if sub == "reset" and "--hard" in rest:
        return True
    if sub == "branch" and ("-f" in rest or "--force" in rest):
        return True
    if sub == "tag" and ("-f" in rest or "--force" in rest):
        return True
    if sub == "symbolic-ref":
        return len(_positional(rest)) >= 2
    if sub == "commit" and cwd is not None:
        return _current_branch(cwd) in CANONICAL_BRANCH_NAMES
    return False


def guard_canonical(argv, *, authorization, now=None, cwd=None):
    """Refuse `argv` (a git subcommand and its arguments, with or without
    a leading 'git') when it would mutate a canonical ref and the caller
    is not the integrator. Consumes `authorization`: see
    Authorization._consume for the single-use and stale-epoch checks this
    runs before ever looking at argv.

    Raises BoundaryRefused, invariant 'single use' or 'no stale
    authority', from the authorization check; invariant 'no direct agent
    merge to canonical' when argv mutates a canonical ref and
    `authorization.integrator_token` does not hold the integrator
    capability for `authorization.instance`. Returns None, and does
    nothing else, when the command is allowed: this function is a gate,
    not a runner, it never executes argv itself."""
    authorization._consume(now=now)
    if _is_canonical_mutation(argv, cwd=cwd):
        if not is_integrator(authorization.integrator_token,
                             instance=authorization.instance):
            raise BoundaryRefused(
                "no direct agent merge to canonical",
                "argv %r would mutate a canonical ref; caller %r holds no "
                "integrator capability for this authorization, and only "
                "the integrator may write to canonical directly. Route "
                "this through scripts/integrate.py's integrate_one() "
                "instead" % (list(argv), authorization.instance))
    return None

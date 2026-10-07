#!/usr/bin/env python3
"""The expected value gate: is the next repair round worth its cost?
usage as a library:  ev_gate.should_continue(rounds_used, passes_so_far, round_cost=0.16, value=1.0) -> (bool, reason)
usage as a check:    python3 -B ev_gate.py --selftest
Owner 2026-09-22 (row 2, under 2 USD per unit): a sub unit burned five rounds of eight builds per exhaustion. The next round runs
only while its expected value, P(pass) times the value of a landing, exceeds its cost. P is the sub unit's own record with a
Beta(1, 2) prior: (passes + 1) / (rounds + 3), so a fresh sub unit starts at a third and each failed round lowers it. Round cost
0.16 USD (8 builds at 0.02) and value 1.00 USD by default (BROTHER_ROUND_COST, BROTHER_VALUE_PER_LANDING); unreadable numbers
refuse the round, never allow it.
The runner's pricing lives here too (FX-13.4), so the runner and the plan lint price a round identically:
round_cost_from_env(env) prices the configured worker mix per arm (BROTHER_ARM_COST, BROTHER_WORKER_MIX), and
landing_value(open_subs, env) is the value per landing, tripled for a unit's final open sub unit."""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import worker_mix as WM  # noqa: E402  (parse and DEFAULT_MIX price the round's arms)
import model_router  # noqa: E402  (the registry is the one source of the per arm cost, FX-31.6)


def p_pass(rounds_used, passes_so_far):
    return (passes_so_far + 1.0) / (rounds_used + 3.0)


# A LANDING IS WORTH 5 USD (2026-09-27): the A/B arms ran at BROTHER_VALUE_PER_LANDING=5 while the default stayed 1.0, and at
# 1.0 against a 0.24 round a fresh sub unit got two rounds; 88 runs ended "not worth another round" on builds that cost cents.
DEFAULT_VALUE = 5.0


def should_continue(rounds_used, passes_so_far, round_cost=None, value=None, env=None):
    e = os.environ if env is None else env
    try:
        rounds_used = int(rounds_used); passes_so_far = int(passes_so_far)
        round_cost = float(e.get("BROTHER_ROUND_COST", 0.16)) if round_cost is None else float(round_cost)
        value = float(e.get("BROTHER_VALUE_PER_LANDING", DEFAULT_VALUE)) if value is None else float(value)
        if rounds_used < 0 or passes_so_far < 0 or round_cost <= 0 or value <= 0 or round_cost != round_cost or value != value: return False, "unreadable figures: the round is refused"
    except (TypeError, ValueError):
        return False, "unreadable figures: the round is refused"
    p = p_pass(rounds_used, passes_so_far); ev = p * value
    if ev >= round_cost: return True, "EV %.3f (p %.2f x %.2f) >= cost %.2f" % (ev, p, value, round_cost)
    return False, "EV %.3f (p %.2f x %.2f) < cost %.2f: not worth another round" % (ev, p, value, round_cost)


_ABSENT = object()   # "the caller set nothing", distinct from a caller setting None


# PER ARM COST (owner 2026-09-23 07:5x): USD per build for each arm, the Claude arm priced as subscription quota.
def default_arm_cost():
    """"arm:usd,..." from each registry arm row's `arm.cost_usd` (FX-31.6): derived, never typed here, same figures as
    before. READ WHEN A ROUND IS PRICED, NEVER AT IMPORT (review of 2026-10-04): computed at load, a registry the router
    refused made this module fail to import and took unit_runner and plan_lint down with a traceback, where before it
    was a constant. Raises model_router.Refused; round_cost_from_env turns that into its None."""
    return model_router.derive_model_views(model_router.registry())["arm_cost"]


def __getattr__(name):
    """DEFAULT_ARM_COST stays readable as a module attribute (the plan lint asserts it), resolved on the read (PEP 562)."""
    if name == "DEFAULT_ARM_COST":
        return default_arm_cost()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def round_cost_from_env(env=None):
    """The round's cost: the configured mix (BROTHER_WORKER_MIX) priced per arm (BROTHER_ARM_COST, 0.02 for an arm it
    does not price). None when either figure is unreadable, the registry behind a default refuses (NO-DATA), or the sum
    is zero, so should_continue falls back to its own BROTHER_ROUND_COST default exactly as the runner always did.
    Never raises."""
    e = os.environ if env is None else env
    try:
        # absent reads the registry default; present with any value, None included, is the caller's figure and is read
        # as given; an env that is not a mapping has no .get and is unreadable, exactly as before
        arm_text = e.get("BROTHER_ARM_COST", _ABSENT)
        mix_text = e.get("BROTHER_WORKER_MIX", _ABSENT)
        if arm_text is _ABSENT:
            arm_text = default_arm_cost()
        if mix_text is _ABSENT:
            mix_text = WM.default_mix()
        costs = dict((k.strip(), float(v)) for k, v in (x.split(":") for x in arm_text.split(",")))
        return sum(c * costs.get(name, 0.02) for name, c in WM.parse(mix_text)) or None
    except (ValueError, AttributeError, TypeError, model_router.Refused):
        return None


def landing_value(open_subs, env=None):
    """The value of a landing: BROTHER_VALUE_PER_LANDING (DEFAULT_VALUE when unset), three times that when at most one
    sub unit is open (the last landing closes the unit). An unreadable value or a count that is not an int raises
    ValueError, so the runner dies at startup rather than guessing, as float() did inline."""
    if isinstance(open_subs, bool) or not isinstance(open_subs, int):
        raise ValueError("landing_value: open_subs must be an int, not %s" % type(open_subs).__name__)
    e = os.environ if env is None else env
    try:
        value = float(e.get("BROTHER_VALUE_PER_LANDING", DEFAULT_VALUE))
    except (AttributeError, TypeError) as exc:
        raise ValueError("landing_value: unreadable BROTHER_VALUE_PER_LANDING (%s)" % type(exc).__name__)
    return value * (3.0 if open_subs <= 1 else 1.0)


def _ledger_root(env=None):
    """Where the OpenRouter ledger is written: the same lookup burn_guard.py's own state_root()
    uses, so this reader and the dispatcher can never disagree about where the money is."""
    e = os.environ if env is None else env
    return e.get("BROTHER_OR_STATE_ROOT") or os.path.expanduser("~/.claude/brother-or-dispatch-state")


def _real_actual_costs_for_model():
    """The real lazy import of plugin/'s per-model cost history (RS1's actual_costs_for_model),
    or (None, None) when plugin/ cannot be imported: the public export tree ships scripts/ but
    not plugin/ (MECHANISM-AUDIT item 21), and that must read as an honest NO-DATA, never a
    silently guessed flat cost. Same shape as model_call.py's own admission=None refusal when a
    boundary import fails: a missing dependency is answered once, here, not re-guessed by every
    caller.

    Imports from model_router.code_root(), the frozen candidate the loop runs (U3, B5-08), never
    from the landing tree the cwd sits in: repo_root() answered the checkout, so a proof run read
    the ledger through landing tree code. A proof phase with no BROTHER_CODE_ROOT refuses there,
    and that refusal is the same (None, None) NO-DATA, never a fall back to the landing tree."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import model_router as _MR
    except ImportError:   # sbe: allow-silent (None, None) is this function's NO-DATA answer, read by every caller
        return None, None
    try:
        root = _MR.code_root()
    except _MR.Refused:   # sbe: allow-silent a refused code root is NO-DATA, never the landing tree's ledger code
        return None, None
    if sys.path[:1] != [root]:
        sys.path.insert(0, root)
    try:
        from plugin.runtime.brother.core.openrouter_ledger import actual_costs_for_model, LedgerError
    except ImportError:
        return None, None
    return actual_costs_for_model, LedgerError


class CostUnreadable(Exception):
    """The ledger exists but cannot be read or is corrupt: the round's cost is unknown, and unknown refuses."""


def model_round_cost(model, limit=200, env=None, root=None, _resolver=_real_actual_costs_for_model):
    """The measured round cost for ONE model: the median of its real, reconciled costs from the
    OpenRouter ledger (median, not mean, so one outlier call does not move the estimate as far as
    a straight average would). None (NO-DATA), never a guessed flat number, when: plugin/ cannot
    be imported, the model name is not a real string, or the ledger has no measured cost yet for
    this model. A ledger that exists but is corrupt or unreadable raises CostUnreadable (2026-09-30):
    it is not a cold start, so it must not be priced at the cold start fallback. The caller decides the safe fallback in each of those
    cases (repair_wave.py currently falls back to the flat hand estimate, matching the existing,
    already reviewed cold-start behaviour of should_continue's own env default)."""
    actual_costs_for_model, LedgerError = _resolver()
    if actual_costs_for_model is None:
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    root = root or _ledger_root(env)
    try:
        costs = actual_costs_for_model(root, model, limit=limit)
    except LedgerError as exc:
        raise CostUnreadable("the cost ledger for %s is corrupt: %s" % (model, exc))
    except OSError as exc:
        raise CostUnreadable("the cost ledger for %s cannot be read: %s: %s" % (model, type(exc).__name__, exc))
    if not costs:
        return None
    costs = sorted(costs)
    return costs[len(costs) // 2]


def history(sub, ledger_path=os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl"), since=0.0, exclude_run=None):
    """(rounds, passing rounds) this sub unit has had across runs newer than `since` (its spec's last change: a repaired
    spec is a new process), the run `exclude_run` left out (the current one counts itself). Review 2026-09-23: an unbounded
    record refused four sub units at round 0 for ever. Unreadable ledger or junk rows: (0, 0), the prior."""
    seen, passed = set(), set()
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                try: r = json.loads(line)
                except ValueError: continue
                if not isinstance(r, dict) or r.get("sub") != sub or r.get("grade") not in ("PASS", "FAIL"): continue
                if (since and (r.get("run_at") or 0) <= since) or (exclude_run and r.get("run") == exclude_run): continue
                k = (r.get("run"), r.get("round")); seen.add(k)
                if r.get("grade") == "PASS": passed.add(k)
    except OSError: pass
    return len(seen), len(passed)


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="ev-hist-"); lp = os.path.join(d, "l.jsonl")
    with open(lp, "w") as fh:
        for run, rn, b, g in (("A-1", 0, "b0", "FAIL"), ("A-1", 0, "b1", "FAIL"), ("A-1", 1, "b0", "PASS"), ("A-2", 0, "b0", "FAIL"), ("A-2", 0, "b1", "NO-DATA"), ("A-2", 1, "b0", "NO-DATA")):
            fh.write(json.dumps({"sub": "D1.1", "run": run, "round": rn, "build": b, "grade": g, "run_at": 100.0 if run == "A-1" else 200.0}) + "\n")
        fh.write(json.dumps({"sub": "D9.9", "run": "Z", "round": 0, "build": "b", "grade": "PASS"}) + "\n"); fh.write("junk\n")
    # THE LOOP SURVIVES A REFUSED REGISTRY (review of 2026-10-04): the projections are read when asked, never at import,
    # so with a registry the router refuses every importer still imports, the round prices NO-DATA (None), no arm is
    # credited, the owner's words read as not certain (None), the mix falls to the base picks and SAYS so.
    import subprocess, tempfile
    _rd = tempfile.mkdtemp(prefix="ev-gate-registry-")
    _corrupt = os.path.join(_rd, "registry.json")
    with open(_corrupt, "w", encoding="utf-8") as _fh:
        _fh.write("{not json")
    _code = ("import sys; sys.path.insert(0, %r)\nimport ev_gate, worker_mix, loop_intake, plan_lint\n"
             "print(repr(ev_gate.round_cost_from_env({})), repr(worker_mix.arm_of('deepseek')), repr(loop_intake.read_model('deep seek')), "
             "repr(worker_mix.picks(2, ['x'], ['x'], env={})))" % os.path.dirname(os.path.abspath(__file__)))
    _p = subprocess.run([sys.executable, "-B", "-c", _code], capture_output=True, text=True, timeout=120,
                        env=dict(os.environ, BROTHER_MODEL_REGISTRY=_corrupt))
    import shutil
    shutil.rmtree(_rd, ignore_errors=True)
    cases = [("with a registry the router refuses, ev_gate, worker_mix, loop_intake and plan_lint still import, the round "
              "prices NO-DATA, no arm or spoken name is known, and the mix says it fell to the base picks",
              _p.returncode == 0 and "None None None ['x', 'x']" in _p.stdout and "the registry refuses" in _p.stdout),
             ("a fresh sub unit runs round 0 (p a third)", should_continue(0, 0)[0]),
             ("at the old value of 1.0, after three failed rounds it still runs (p a sixth, EV 0.167 vs 0.16)", should_continue(3, 0, value=1.0)[0]),
             ("at the old value of 1.0, after four failed rounds it stops (p a seventh)", not should_continue(4, 0, value=1.0)[0] and "not worth" in should_continue(4, 0, value=1.0)[1]),
             ("at the default value of 5 a sub unit gets five rounds at a 0.24 round cost, and stops once p falls under 0.048", should_continue(4, 0, round_cost=0.24)[0] and not should_continue(18, 0, round_cost=0.24)[0]),
             ("a grader pass raises p and buys more rounds", should_continue(4, 1)[0]),
             ("a higher value per landing buys more rounds, a lower one fewer", should_continue(6, 0, value=2.0)[0] and not should_continue(2, 0, value=0.3)[0]),
             ("history counts rounds and passing rounds of this sub unit only, NO-DATA rows and junk skipped", history("D1.1", lp) == (3, 1) and history("D9.9", lp) == (1, 1)),
             ("an absent ledger is the prior, never a guess", history("D1.1", os.path.join(d, "none")) == (0, 0)),
             ("runs before the spec's last change and the current run itself are left out; a scalar line is skipped",
              history("D1.1", lp, since=150.0) == (1, 0) and history("D1.1", lp, exclude_run="A-1") == (1, 0) and (open(lp, "a").write("null\n"), history("D1.1", lp))[1] == (3, 1)),
             ("a sub unit with a passing record buys a round that a flat prior would refuse", should_continue(4 + 6, 0 + 5, value=1.0)[0] and not should_continue(4, 0, value=1.0)[0]),
             ("the runner's round cost: the default mix priced per arm, an unreadable figure is None, never a guess",
              abs(round_cost_from_env({}) - 0.24) < 1e-9 and round_cost_from_env({"BROTHER_ARM_COST": "deepseek=0.02"}) is None
              and round_cost_from_env({"BROTHER_WORKER_MIX": "deepseek:1", "BROTHER_ARM_COST": "deepseek:0.05"}) == 0.05),
             ("the runner's landing value: tripled for the last open sub unit, an unreadable value raises",
              landing_value(1, {}) == 3 * DEFAULT_VALUE and landing_value(2, {"BROTHER_VALUE_PER_LANDING": "2"}) == 2.0
              and _raises(landing_value, 1, {"BROTHER_VALUE_PER_LANDING": "x"}) and _raises(landing_value, True, {})),
             ("unreadable, negative, zero or nan figures refuse", not should_continue("x", 0)[0] and not should_continue(1, 0, round_cost=0)[0] and not should_continue(1, 0, value=float("nan"))[0] and not should_continue(-1, 0)[0])]
    # F21 (MECHANISM-AUDIT item 21, 2026-09-26): the round cost must vary by model, from the
    # ledger's own measured history (RS1's actual_costs_for_model), not one flat number for every
    # model alike. A lazy import: plugin/ is absent in the public export tree (ships scripts/, not
    # plugin/), and that must read as NO-DATA, never a silently guessed flat cost (same shape as
    # model_call.py's own admission=None refusal when a boundary import fails).
    cases.append(("a plugin/-absent environment (public export) is NO-DATA, never a guessed cost",
                  model_round_cost("muse", _resolver=lambda: (None, None)) is None))
    _real_resolver = _real_actual_costs_for_model
    cases.append(("the real resolver finds the real plugin/ package in THIS repository",
                  _real_resolver()[0] is not None))

    class _FakeLedgerError(Exception):
        pass

    def _fake_costs(root, model, limit=200):
        # dict.get's default argument is evaluated EAGERLY (every Python call evaluates its
        # arguments before the call runs), so a raising expression there would fire on every
        # invocation regardless of which key matched: an if/elif/else is required for real laziness.
        if model == "muse":
            return [0.10, 0.30, 0.20]
        if model == "empty":
            return []
        # A DISTINCT NON RAISING ANSWER for a blank/None model (99.0, never a real cost this
        # fixture uses elsewhere): mutation proof that the model validation guard in
        # model_round_cost is what refuses this, not a coincidental fall through to the
        # LedgerError catch below, which would mask the guard's own removal.
        if model is None or (isinstance(model, str) and not model.strip()):
            return [99.0]
        raise _FakeLedgerError("corrupt")
    _fake_resolver = lambda: (_fake_costs, _FakeLedgerError)
    cases.append(("the median of measured per-model costs is used, never the mean or the last",
                  model_round_cost("muse", _resolver=_fake_resolver) == 0.20))
    cases.append(("a model with no measured cost yet is NO-DATA, never zero",
                  model_round_cost("empty", _resolver=_fake_resolver) is None))
    def _refused(model):
        try:
            model_round_cost(model, _resolver=_fake_resolver)
        except CostUnreadable:
            return True
        return False
    cases.append(("a corrupt or unreadable ledger refuses (CostUnreadable), never reads as a cold start",
                  _refused("deepseek")))
    cases.append(("a non string or blank model name is refused, never looked up",
                  model_round_cost(None, _resolver=_fake_resolver) is None and model_round_cost("  ", _resolver=_fake_resolver) is None))
    # A real, temp-rooted ledger (never the live one): reserve and reconcile two calls for one
    # model, prove the real resolver's own plumbing reads them back as history.
    import tempfile
    from plugin.runtime.brother.core import openrouter_ledger as _L
    _root = tempfile.mkdtemp(prefix="ev-gate-cost-")
    _r1 = _L.reserve(_root, 10.0, 1.0, "h", model="muse-live-test")
    _L.reconcile(_root, _r1, 0.05)
    _r2 = _L.reserve(_root, 10.0, 1.0, "h2", model="muse-live-test")
    _L.reconcile(_root, _r2, 0.15)
    cases.append(("a real temp-rooted ledger (never the live one) round trips through model_round_cost",
                  model_round_cost("muse-live-test", root=_root) == 0.15))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else 2)

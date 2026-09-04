"""loop_bridge: the scheduler's ready set becomes real dispatches.

W9.4. graph_loop.py has computed a correct dispatch plan since W5 and nothing
has ever consumed it: a person read "DISPATCH NOW" off a terminal and decided
what to do about it. This closes that gap, and it is the last structural piece
before the loop can run unattended.

THE ONE PROPERTY THIS MUST NEVER LOSE:

    ONLY plan()['batch'] MAY BE DISPATCHED.

Everything the scheduler put in 'deferred' or 'blocked' was refused for a
reason that is still true at dispatch time: a founder gate, an undeclared write
set, a write set overlapping something already in flight, an unmet dependency,
or no free slot. A bridge that widens the batch by even one node throws away
the whole admission decision, and the failure it produces is the one the founder
named, two agents discovering a shared file by corrupting it. W5 buys safety by
refusing admission BEFORE dispatch; this module is where that purchase is either
honoured or quietly spent.

So the batch is not filtered here, not re-sorted here, and not topped up here.
It is consumed exactly as given, and the test suite asserts that every deferred
and blocked node stays undispatched.

WHAT IT DOES WITH EACH NODE, in order, reusing the three pieces already built
rather than reimplementing any of them:

    spawn (bm_worker_spawn) -> verify (bm_verify) -> repair if red (bm_repair)

and it records what happened per node. It does not judge, retry or recover on
its own: each of those lives in the module that owns it, and a bridge that grew
its own copy of any of them would be a second answer to a settled question.

THE SIBLING DEPENDENCY, stated rather than hidden. Those three modules live in
the BrotherModeUp repository, not this one. That is a real seam and this estate
has already been bitten by it once today, when an agent searched one repository
for a file that lives in the other and reported a true claim as false. So the
import is explicit, its path is printed on failure, and an absent sibling is
NO-DATA with the path named, never a crash and never a silent skip.

Python 3, standard library only. No network.

origin: the file this module writes to disk (a throwaway worker script) is
created only inside prove_slice(), which runs when a human or CI invokes this
file directly with `python3 scripts/loop_bridge.py --prove-slice` (see
main(), the `if args.prove_slice:` branch, below). Nothing else in this repo
calls prove_slice() (verified: grep -rn prove_slice scripts bundle/runtime
finds no caller outside this file), so it is reached only through this
module's own CLI, never as a library call from another script.

PRODUCER: this module is the sole producer of the file it writes there. The
write happens at `with open(script, "w", encoding="utf-8") as fh:
fh.write(SLICE_WORKER)` inside prove_slice(), a few lines below the
SLICE_WORKER string constant.
"""
import argparse
import glob
import json
import subprocess
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
import graph_loop  # noqa: E402
import run_heartbeat  # noqa: E402

#: WHERE THE LOOP'S THREE MOVING PARTS ARE FOUND, in order, and the order is the
#: whole point. The first version of this file hardcoded one developer's home
#: directory, which works on exactly one machine and fails cryptically everywhere
#: else. A peer review named it the same day it was written: a hardcoded sibling
#: checkout cannot survive public usage, because a stranger who installs Brother
#: has no such path and never will.
#:
#: So resolution is: the INSTALLED runtime first (what a stranger actually has),
#: then an explicit override for development (what this machine has), then
#: NO-DATA naming every place it looked. Never a silent default to somebody's
#: home directory.
RUNTIME_ENV_VAR = "BROTHER_RUNTIME_ROOT"

#: Where an installed bundle puts them, relative to HERE (this file's own
#: install location), never HOME: not config-dir-relative, so this one
#: candidate stays a plain module constant.
BUNDLED_CANDIDATE = os.path.join(HERE, "..", "bundle", "tools")

#: The development fallback, kept LAST and named as such. It is this estate's own
#: layout and it is not a contract anybody else inherits.
DEV_CANDIDATE = os.path.expanduser("~/Documents/BrotherModeUp/tools")

#: The plugin cache is VERSIONED: cache/brother/brothermode/<version>/tools,
#: measured on a real install (3.4.2 on this machine). The unversioned cache
#: candidate above never matches a real install, which is EVAD run 5 trial 2's
#: finding; these segments are joined against the resolved config root at
#: call time so a test can drive the resolution without touching this
#: machine.
INSTALLED_VERSIONED_SEGMENTS = ("plugins", "cache", "brother", "brothermode")

#: The claude CLI's own env var for relocating its whole config directory
#: (used by bundle-install-smoke.sh, clean_install_e2e.sh and fault_lab.py to
#: sandbox an install). When it is set, THAT is where the CLI actually puts
#: an installed plugin's cache, not $HOME/.claude, so the versioned-install
#: candidate below must be built from it. Root cause of a real failure: a
#: virgin Linux CI run (and a HOME-isolated local repro) reported "no worker
#: adapter could be loaded" because the brothermode plugin installed under
#: CLAUDE_CONFIG_DIR while this file only ever looked under HOME; it only
#: ever looked right on this developer's own machine because a real
#: brothermode install already sat under the real $HOME/.claude from
#: ordinary daily use, never because the resolution was correct.
CLI_CONFIG_ENV_VAR = "CLAUDE_CONFIG_DIR"


def _config_root(env):
    """The coding client's actual config directory: BROTHER_CONFIG_DIR or
    CLAUDE_CONFIG_DIR when set (an install sandboxed or relocated on
    purpose), else $HOME/.claude under Claude and $HOME/.codex under Codex.

    C3: the client comes from brother_paths.client(env), and the Claude
    answer is byte for byte what this function returned before that seam
    existed. HOME is still read from `env` rather than from the process,
    because this file's own tests move the home directory that way and a
    resolver that ignored them would be resolving a different machine."""
    override = ((env.get(brother_paths.CONFIG_DIR_ENV) or "").strip()
                or (env.get(CLI_CONFIG_ENV_VAR) or "").strip())
    if override:
        return override
    home = (env.get("HOME") or "").strip() or os.path.expanduser("~")
    if brother_paths.client(env) == brother_paths.CODEX:
        codex_home = (env.get("CODEX_HOME") or "").strip()
        return codex_home or os.path.join(home, ".codex")
    return os.path.join(home, ".claude")


def _version_key(tools_path):
    """Numeric ordering for a versioned install dir (…/<version>/tools), so
    1.10.0 outranks 1.2.3; a non-numeric name sorts last, not crashes."""
    name = os.path.basename(os.path.dirname(tools_path))
    try:
        return tuple(int(x) for x in name.split("."))
    except ValueError:
        return (-1,)


def runtime_candidates(env=None):
    """Every place to look, in order, with the override honoured if set.

    Returned as a list rather than resolved here so the NO-DATA message can name
    all of them: a reader who has none of these needs to know what was expected,
    not merely that something was missing."""
    env = os.environ if env is None else env
    override = (env.get(RUNTIME_ENV_VAR) or "").strip()
    out = []
    if override:
        out.append(os.path.join(override, "tools") if not override.endswith("tools")
                   else override)
    out.append(os.path.normpath(BUNDLED_CANDIDATE))
    # config_root is CLAUDE_CONFIG_DIR when set, else $HOME/.claude: the
    # claude CLI's ACTUAL config directory, which is where it places an
    # installed plugin's cache. Building these from bare HOME (the previous
    # shape) matched only when CLAUDE_CONFIG_DIR was unset or happened to
    # equal $HOME/.claude, which is every session on this developer's own
    # machine and no session anywhere else: a sandboxed or relocated
    # install (this repo's own smoke and e2e scripts, any CI runner, any
    # user who sets CLAUDE_CONFIG_DIR) was invisible to this resolution.
    config_root = _config_root(env)
    out.append(os.path.normpath(
        os.path.join(config_root, "skills", "brothermode", "tools")))
    out.append(os.path.normpath(
        os.path.join(config_root, *INSTALLED_VERSIONED_SEGMENTS, "tools")))
    versioned = glob.glob(os.path.join(
        config_root, *INSTALLED_VERSIONED_SEGMENTS, "*", "tools"))
    out.extend(os.path.normpath(c) for c in
               sorted(versioned, key=_version_key, reverse=True))
    out.append(DEV_CANDIDATE)
    return out


def _import_parts(tools_dir):
    """(parts, problem) from ONE directory, leaving no half-loaded state: a
    failed attempt removes what it inserted from sys.path and drops any of
    the three modules from sys.modules, so the next candidate cannot end up
    with a worker from one install and a verifier from another."""
    inserted = tools_dir not in sys.path
    if inserted:
        sys.path.insert(0, tools_dir)
    try:
        import bm_worker_spawn
        import bm_verify
        import bm_repair
    except ImportError as exc:
        if inserted:
            sys.path.remove(tools_dir)
        for name in ("bm_worker_spawn", "bm_verify", "bm_repair"):
            sys.modules.pop(name, None)
        return None, ("could not import from %s: %s" % (tools_dir, exc))
    return {"spawn": bm_worker_spawn, "verify": bm_verify,
            "repair": bm_repair}, ""


def load_parts(tools_dir=None, env=None):
    """The three modules, or a reason. Returns (parts, problem).

    Resolution means the first candidate that actually LOADS, not the first
    directory that exists: a real versioned install can predate these modules
    (measured: brothermode 3.4.2 in the plugin cache has no bm_worker_spawn),
    and stopping there would hide a later candidate that works."""
    if tools_dir is not None:
        if not os.path.isdir(tools_dir):
            return None, ("the sibling tools directory is not at %s, so the "
                          "worker, the verifier and the repair loop cannot be "
                          "loaded" % tools_dir)
        return _import_parts(tools_dir)
    looked = runtime_candidates(env)
    skipped = []
    for candidate in looked:
        if not os.path.isdir(candidate):
            continue
        parts, problem = _import_parts(candidate)
        if parts is not None:
            return parts, ""
        skipped.append(problem)
    detail = ("; ".join(skipped) + ". ") if skipped else ""
    return None, ("the loop's worker, verifier and repair modules were not "
                  "found. %sLooked, in order: %s. Set %s to a checkout to "
                  "override." % (detail, ", ".join(looked), RUNTIME_ENV_VAR))


def dispatchable(plan):
    """Exactly the batch, as given. Not filtered, not re-sorted, not topped up.

    Written as a named function with nothing in it so that any future change
    which widens the set has to happen HERE, in front of this docstring, rather
    than by quietly appending to a list somewhere in the run loop."""
    return list(plan.get("batch") or [])


def refused(plan):
    """Every node the scheduler declined, with its reason. Returned so a caller
    can SAY what was not dispatched: silent truncation reads as full coverage,
    which is how a deferred node looks identical to a node nobody had."""
    out = [(n["id"], why) for n, why in (plan.get("deferred") or [])]
    out += [(n["id"], "BLOCKED-BY " + ", ".join(unmet))
            for n, unmet in (plan.get("blocked") or [])]
    return out


# ---------------------------------------------------------------------------
# T1 FOLLOW-UP: per-unit usage, beside the claim store rather than inside it.
#
# claim_store.py's own docstring names it the sole producer of the claim
# store file, and its release() takes a fixed set of named fields (state,
# evidence). Widening that contract is a change to a module this task does
# not own the scope to touch, so real usage (bm_worker_spawn's additive
# "usage" key on a dispatched record, see run_node above) is written to a
# small sidecar file next to the claim store instead: additive telemetry,
# not claim state, kept out of the file whose docstring makes that
# distinction structural. scripts/brother_run.py reads this sidecar and
# folds it into the claims dict it already builds before summing usage into
# the delivery record's cost block.
# ---------------------------------------------------------------------------

def usage_sidecar_path(store):
    """Where per-unit usage lives, beside the claim store at `store`."""
    base = store[:-5] if store.endswith(".json") else store
    return base + "_usage.json"


def read_usage_sidecar(path):
    """{unit_id: usage_dict}, or {} when the file is absent or unreadable.
    Never raises: a sidecar that cannot be read means no usage was recorded,
    not that the run failed."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_usage_sidecar(path, data):
    """Best effort: usage is telemetry, and a run must never fail because
    this file could not be written."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True, indent=1)
    except OSError as exc:
        print("loop_bridge: could not write usage sidecar %s: %s"
              % (path, exc), file=sys.stderr)


def _head(cwd):
    """The commit a unit started from, or None. Never raises."""
    if not cwd:
        return None
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001  # sbe: allow-silent becomes NO-DATA below
        return None
    return (proc.stdout or "").strip() if proc.returncode == 0 else None


def _audit_scope(unit, before, cwd):
    """What git says the unit changed, against what it declared. Never raises.

    NO-DATA when there is no baseline, which is the honest reading of "I could
    not tell" and is deliberately NOT integrable: an unauditable change reaching
    canonical is the failure this exists to prevent."""
    if scope_audit is None:
        return {"verdict": "NO-DATA",
                "reason": "the scope audit module could not be loaded, so nothing "
                          "was compared. That is not a pass"}
    if not before or not cwd:
        return {"verdict": scope_audit.NO_DATA,
                "reason": "no baseline commit was available, so the change could "
                          "not be compared with what was declared"}
    try:
        verdict, detail = scope_audit.audit(unit, before, None, cwd=cwd)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent NO-DATA, not a pass
        return {"verdict": "NO-DATA",
                "reason": "the scope audit raised %s: %s" % (type(exc).__name__, exc)}
    out = {"verdict": verdict, "reason": detail.get("reason", "")}
    if detail.get("undeclared"):
        out["undeclared"] = detail["undeclared"]
    return out


def run_node(node, parts, worker, cwd=None, max_attempts=3):
    """One node through spawn, verify and (if red) repair. Never raises."""
    verify, repair = parts["verify"], parts["repair"]
    unit = {"unit_id": node["id"],
            "objective": node.get("name") or node.get("title") or node["id"],
            "done_check": node.get("done_check") or "",
            "write_scope": node.get("owns") or [],
            "read_scope": [], "role": "builder", "risk_class": "normal",
            "attempt": 1, "prior_failure_note": ""}

    # PARITY BLOCKER P0.3, and the directive is explicit that this is a WIRING
    # job rather than a building one: the mechanism already existed with its own
    # tests and nothing called it, which a grep confirmed the same day.
    #
    # The reference is taken BEFORE the worker runs, because an audit needs a
    # baseline and taking it afterwards would compare the tree to itself.
    before = _head(cwd)

    # E46, THE ONLY PLACE THAT KNOWS WHAT A UNIT IS DOING. Every phase below
    # is a real blocking call (a spawned worker, a git range, a done_check
    # subprocess, a repair loop), and until now a person watching a run saw
    # none of them: run() returns only when the whole batch is finished, so
    # the entire wait was one silent gap. The heartbeat is a module-level
    # seam (run_heartbeat.current()) rather than a parameter because this
    # function is called from a thread pool three layers under the caller
    # that starts it, and because a run that is not narrating gets a silent
    # heartbeat, so there is nothing to branch on here.
    beat = run_heartbeat.current()
    beat.phase(node["id"], "the worker is running",
               worker=run_heartbeat.worker_name(worker))

    # THE WORKER RUNS IN THE LANE, not beside it. Found 2026-08-29 while
    # proving the spine end to end: the spawning worker takes ONE cwd at
    # construction, so every worker wrote wherever that pointed and the lanes
    # isolated only verify and repair. A worker that supports a per-run cwd
    # gets the lane; one that does not keeps its old behaviour, so every
    # existing caller stays green.
    try:
        worker_result = worker.run(unit, cwd=cwd)
    except TypeError:
        worker_result = worker.run(unit)

    # WHAT ACTUALLY CHANGED, from git, not from what the worker says it changed.
    # A worker reporting "I only touched X" is a claim; the diff is evidence.
    beat.phase(node["id"], "reading what actually changed")
    scope = _audit_scope(unit, before, cwd)

    beat.phase(node["id"], "running the done check")
    verdict = verify.verify(unit, cwd=cwd)
    record = {"id": node["id"], "worker_status": worker_result.get("status"),
              "verdict": verdict.get("verdict"), "reason": verdict.get("reason"),
              "repair": None, "scope": scope}
    # THE REAL COUNT, when the worker gave one (bm_worker_spawn's additive
    # "usage" key, sourced from model_worker.py's own reading of the claude
    # CLI's --output-format json usage object). Only this FIRST attempt's
    # usage is captured here: a retry inside repair.repair() below runs the
    # worker again but that module lives in the sibling BrotherModeUp
    # checkout and is out of this file's reach, so a repaired unit's cost
    # block undercounts by whatever repair spent. Never set to {}: absent
    # means "not reported", not "zero".
    worker_usage = worker_result.get("usage")
    if isinstance(worker_usage, dict) and worker_usage:
        record["usage"] = worker_usage

    # A UNIT THAT WROTE OUTSIDE ITS DECLARED SCOPE IS NOT INTEGRABLE, whatever
    # its own check says. The verdict below can be a clean PASS and the change
    # still must not reach canonical, because the thing that passed is not the
    # thing that was authorised. QUARANTINE holds it; it is not rejection and
    # the work is preserved for a person to look at.
    if scope and scope.get("verdict") in (scope_audit.QUARANTINE,
                                          scope_audit.NO_DATA):
        record["integrable"] = False
        record["integration_block"] = (
            "%s: %s. This unit does not reach canonical integration whatever its "
            "own verification says, because what passed is not what was "
            "authorised" % (scope["verdict"], scope.get("reason", "")))
    else:
        record["integrable"] = True

    if verify.is_pass(verdict):
        beat.done(node["id"], "done, its check passed")
        return record
    beat.phase(node["id"], "the check was red, repairing")
    fixed = repair.repair(unit, verdict, worker, cwd=cwd,
                          max_attempts=max_attempts)
    record["repair"] = {"outcome": fixed["outcome"],
                        "attempts": len(fixed["attempts"]),
                        "reason": fixed["reason"]}
    record["verdict"] = fixed["final_verdict"].get("verdict")
    beat.done(node["id"], "done after repair: %s" % (record["verdict"] or "?"))
    return record


#: How many admitted nodes may be in flight at once. The batch is ALREADY
#: conflict-free when it arrives here (graph_loop builds a greedy maximal batch
#: whose write sets are disjoint from each other and from everything in flight),
#: so this is a resource limit and NOT a safety limit, and saying which it is
#: matters. Field research 2026-08-29 found that merge-conflict risk, the reason
#: usually given for a cap like this, appears in ZERO vendor documents: where a
#: reason is given at all it is machine capacity or host safety. So this number
#: is defensible on what this machine can run and is not defensible as collision
#: avoidance, because W5 already did that.
try:
    import integrate as integrate_mod
except Exception:  # noqa: BLE001
    integrate_mod = None

try:
    import claim_store
except Exception:  # noqa: BLE001
    claim_store = None

try:
    import scope_audit
except Exception:  # noqa: BLE001  # sbe: allow-silent absence becomes NO-DATA
    scope_audit = None

try:
    import worktree_lane
except Exception:  # noqa: BLE001
    worktree_lane = None

MAX_IN_FLIGHT = 3


class LaneWorker(object):
    """A spawning worker that runs each unit IN ITS LANE.

    The underlying worker binds cwd at construction, which is right for a
    single-tree world and wrong here: the whole point of a lane is that the
    worker's writes land in it. So this builds one spawning worker per run,
    at the lane the dispatcher hands it."""

    def __init__(self, spawn_module, argv, environ=None):
        self._spawn, self._argv, self._environ = spawn_module, list(argv), environ

    def run(self, unit, cwd=None):
        inner = self._spawn.SpawningWorker(self._argv, cwd=cwd,
                                           environ=self._environ)
        return inner.run(unit)


def run(plan, parts, worker, cwd=None, max_attempts=3, max_in_flight=None,
        isolate=True):
    """Every dispatchable node, and an explicit account of every refused one.

    CONCURRENT, and safe BECAUSE of the scheduler rather than despite it. Until
    2026-08-29 this was a list comprehension: spawn, wait, spawn, wait, which is
    the textbook anti-pattern and made the whole loop as slow as the sum of its
    parts while the admission decision that permits parallelism sat unused one
    layer up.

    Threads rather than processes on purpose: every worker is already its own
    subprocess, so these threads only WAIT on them. The work happens in the
    children and the interpreter lock is never the constraint.

    Results come back in the batch's own order, not completion order, so a
    reader can line them up against the plan they came from. A node whose worker
    raises becomes a recorded failure for that node and never takes the batch
    down with it: one bad unit must not cost the other two.
    """
    import concurrent.futures

    batch = dispatchable(plan)
    cap = MAX_IN_FLIGHT if max_in_flight is None else max_in_flight

    # PHYSICAL ISOLATION, parity blocker P0.2. Admission is PREDICTIVE safety:
    # it depends on every write set being declared correctly. Isolation is
    # CONTAINMENT safety and does not. Until 2026-08-29 every writer here shared
    # one tree, which measured level 0 against the competitors this is judged by.
    #
    # THE FAIL-CLOSED RULE IS THE POINT: when a lane cannot be created, writer
    # concurrency drops to ONE. It never degrades into shared-tree concurrent
    # writing, because a system that silently falls back to the unsafe thing
    # under load fails exactly when nobody is watching.
    lanes, lane_note = None, ""
    if isolate and batch and cwd:
        try:
            import worktree_lane
            lanes = worktree_lane.Lanes(cwd, [n.get("id") for n in batch])
            cap = lanes.safe_concurrency(cap)
            lane_note = lanes.why()
        except Exception as exc:  # noqa: BLE001
            # sbe: allow-silent isolation is unavailable, so concurrency closes
            lanes, cap = None, 1
            lane_note = ("isolation is unavailable (%s), so writer concurrency "
                         "is 1 rather than a shared tree" % exc)

    def _cwd_for(node):
        if lanes is not None and lanes.isolated:
            return lanes.path_for(node.get("id")) or cwd
        return cwd

    results = [None] * len(batch)
    if batch and cap > 0:
        with concurrent.futures.ThreadPoolExecutor(max_workers=cap) as pool:
            futures = {pool.submit(run_node, n, parts, worker, _cwd_for(n),
                                   max_attempts): i
                       for i, n in enumerate(batch)}
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    # sbe: allow-silent the exception becomes this node's record
                    results[i] = {"id": batch[i].get("id"),
                                  "worker_status": "unavailable",
                                  "verdict": "NO-DATA",
                                  "reason": "the worker raised %s: %s"
                                            % (type(exc).__name__, exc),
                                  "repair": None}
    # THE LANES ARE NOT RELEASED HERE, deliberately. Each one holds the work a
    # worker just produced, and that is exactly what serial integration has to
    # read next: releasing them at the end of dispatch would destroy the thing
    # the next stage exists to consume. So ownership passes to the caller, and
    # this says so rather than leaving it to be discovered.
    #
    # An unreleased lane IS a real hazard: this estate's own push gate warns
    # about abandoned worktrees, and the fence check exists because a claim that
    # cannot retire itself holds its paths forever. The seam is named so the
    # hazard is somebody's rather than nobody's.
    isolation = {"isolated": bool(lanes is not None and lanes.isolated),
                 "lanes": {uid: lane["path"]
                           for uid, lane in (lanes.lanes.items() if lanes else [])},
                 "note": lane_note,
                 "ownership": ("the caller owns these lanes and must release them "
                               "after integration; loop_bridge does not, because "
                               "they hold the work integration reads")
                 if lanes is not None and lanes.isolated else ""}
    return {"dispatched": [r for r in results if r is not None],
            "not_dispatched": refused(plan),
            "in_flight_cap": cap,
            "isolation": isolation}



# ---------------------------------------------------------------------------
# W9.5: the proof slice.
#
# Everything above is machinery. This is the claim: one unit goes from the
# scheduler's ready set to a closed, verified state, through a REAL spawned
# process, across a DELIBERATELY SEEDED failure, with no model turn between any
# two transitions.
#
# WHY A SEEDED FAILURE RATHER THAN A HAPPY PATH. A slice that only ever passes
# proves the pieces are connected and nothing else. The whole difficulty of an
# unattended loop is what happens when something goes wrong, because that is the
# moment a person is normally required. So the worker here fails its first
# attempt on purpose and succeeds on its second, and the run is only a proof if
# the repair happened without anybody being asked.
#
# HOW "NO MODEL TURN" IS PROVEN rather than asserted in prose: the entire slice
# runs inside ONE process, and the trace records that process's pid against
# every transition. A model turn cannot happen inside a single uninterrupted
# process, so a trace whose transitions all carry one pid, in order, with no
# gap where input was read, is the mechanical form of the claim. If the loop
# ever grew a step that waited for a human, that step would have to return to
# the caller, and the pid chain would break.
# ---------------------------------------------------------------------------

SLICE_WORKER = r"""
import json, os, sys
brief = json.loads(sys.stdin.read())
target = os.environ["SLICE_TARGET"]
# THE SEEDED FAILURE: attempt 1 does nothing at all, which leaves the unit's
# done_check red. Attempt 2 reads the prior failure note it was handed and only
# then does the work. A worker that ignored the note would loop forever, which
# is what the note exists to prevent.
if brief.get("attempt", 1) >= 2 and brief.get("prior_failure_note"):
    with open(target, "w") as fh:
        fh.write("fixed on attempt %s" % brief.get("attempt"))
print(json.dumps({"worker_claim": "attempt %s" % brief.get("attempt", 1),
                  "artifacts": [target], "cost": {"tokens": 0, "minutes": 0}}))
"""


def prove_slice(tools=None, workdir=None):
    """Run one unit end to end across a seeded failure. Returns (ok, trace)."""
    import tempfile
    parts, problem = load_parts(tools)
    if parts is None:
        return False, [{"step": "load-parts", "ok": False, "detail": problem}]

    tmp = workdir or tempfile.mkdtemp(prefix="slice-")
    target = os.path.join(tmp, "fixed")
    script = os.path.join(tmp, "worker.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(SLICE_WORKER)

    pid = os.getpid()
    trace = []

    def step(name, detail):
        trace.append({"step": name, "pid": pid, "detail": detail})

    # A unit whose done_check is genuinely red right now, and genuinely green
    # once the work is done. Nothing here is stubbed.
    unit = {"unit_id": "SLICE-1", "objective": "create the target file",
            "done_check": "test -f %s" % target,
            "write_scope": [target], "read_scope": [], "role": "builder",
            "risk_class": "normal", "attempt": 1, "prior_failure_note": ""}
    step("ready", "one unit, done_check is %r" % unit["done_check"])

    env = dict(os.environ)
    env["SLICE_TARGET"] = target
    worker = parts["spawn"].SpawningWorker([sys.executable, script], environ=env)
    step("claimed", "unit SLICE-1 claimed by this process")

    first = worker.run(unit)
    step("dispatched", "a real process ran, status %r" % first.get("status"))

    verdict = parts["verify"].verify(unit)
    step("verified", "%s (%s)" % (verdict.get("verdict"), verdict.get("reason")))
    if parts["verify"].is_pass(verdict):
        return False, trace + [{"step": "seed", "pid": pid, "ok": False,
                                "detail": "the seeded failure did not occur, so "
                                          "this run proves nothing about repair"}]
    step("failed", "the seeded failure occurred, as designed")

    fixed = parts["repair"].repair(unit, verdict, worker, max_attempts=3)
    step("repaired", "%s after %d attempt(s)"
         % (fixed["outcome"], len(fixed["attempts"])))
    step("reverified", fixed["final_verdict"].get("verdict"))

    ok = (fixed["outcome"] == parts["repair"].REPAIRED
          and parts["verify"].is_pass(fixed["final_verdict"])
          and os.path.exists(target))
    step("closed" if ok else "not-closed",
         "target exists: %s" % os.path.exists(target))
    return ok, trace


def assert_unattended(trace):
    """Every transition ran inside one uninterrupted process.

    A model turn cannot happen inside a single process: it would have to return
    to the caller first. So one pid across every step, in order, IS the claim,
    and this is the mechanical form of it rather than a sentence asserting it."""
    pids = {t.get("pid") for t in trace if "pid" in t}
    if len(pids) != 1:
        return False, ("transitions ran under %d different processes (%s), so "
                       "something returned to a caller between them"
                       % (len(pids), sorted(pids)))
    want = ["ready", "claimed", "dispatched", "verified", "failed",
            "repaired", "reverified", "closed"]
    got = [t["step"] for t in trace]
    if got != want:
        return False, ("the transition sequence was %s, not the required %s"
                       % (got, want))
    return True, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be claimed, and claim nothing")
    ap.add_argument("--tools", help="override the sibling tools directory")
    ap.add_argument("--slots", type=int)
    ap.add_argument("--prove-slice", action="store_true",
                    help="run one unit end to end across a seeded failure")
    ap.add_argument("--assert-unattended", action="store_true",
                    help="with --prove-slice, also require one process across "
                         "every transition")
    ap.add_argument("--plan", help="a canonical Work document; defaults to the "
                                   "estate's own roadmap")
    ap.add_argument("--claims", help="the durable claim store")
    ap.add_argument("--owner", help="who is claiming, defaults to this pid")
    ap.add_argument("--work-id", dest="work_id", default="",
                    help="the work these units belong to")
    ap.add_argument("--cwd", help="the canonical repository")
    ap.add_argument("--max-attempts", dest="max_attempts", type=int, default=3)
    ap.add_argument("--worker-cmd", dest="worker_cmd", nargs="+",
                    help="the command that runs one unit")
    ap.add_argument("--null-worker", action="store_true",
                    help="claim and release without doing work, for proving the "
                         "claim path itself")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.prove_slice:
        ok, trace = prove_slice(args.tools)
        for t in trace:
            print("  %-12s %s" % (t["step"], t.get("detail", "")))
        if not ok:
            print("PROOF FAILED: the slice did not close", file=sys.stderr)
            return 1
        if args.assert_unattended:
            unattended, why = assert_unattended(trace)
            if not unattended:
                print("PROOF FAILED: %s" % why, file=sys.stderr)
                return 1
            print("PROVEN: one unit went from ready to closed across a seeded "
                  "failure, and every transition ran inside one process, so no "
                  "model turn sits between any two of them")
            return 0
        print("PROVEN: one unit went from ready to closed across a seeded failure")
        return 0

    doc = graph_loop.load(args.plan) if args.plan else graph_loop.load()
    plan = graph_loop.plan(doc, slots=args.slots)

    if args.dry_run:
        print("WOULD CLAIM (%d):" % len(dispatchable(plan)))
        for n in dispatchable(plan):
            print("  %-8s %s" % (n["id"], (n.get("name") or n.get("title") or "")[:60]))
        print("WOULD NOT CLAIM (%d), each with the scheduler's own reason:"
              % len(refused(plan)))
        for nid, why in refused(plan):
            print("  %-8s %s" % (nid, why[:90]))
        print("nothing was claimed: this is a dry run")
        return 0

    parts, problem = load_parts(args.tools)
    if parts is None:
        print("NO-DATA: %s" % problem, file=sys.stderr)
        return 2

    # THE LIVE CLAIM PATH, parity blocker P0.1. This printed a NO-DATA refusal
    # until 2026-08-29, and the refusal was honest: every other piece existed
    # and the one that makes autonomous execution safe to leave alone did not.
    # Without a durable exclusive claim, two sessions reading the same ready set
    # both start the same unit and the first anybody knows is a conflict at
    # integration.
    store = args.claims or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "docs", "plan",
        "claims.json")
    owner = args.owner or ("pid-%d" % os.getpid())

    # RECONCILE FIRST, ALWAYS. A controller that starts by claiming, rather than
    # by looking at what a previous run left behind, is the one that produces
    # duplicate work after a crash.
    found, why = claim_store.reconcile(store)
    if found is None:
        print("NO-DATA: %s. Nothing was claimed, because a claim store that "
              "cannot be read might be holding every unit" % why, file=sys.stderr)
        return 2
    for f in found:
        print("%-11s %-10s %s" % (f["status"], f["unit_id"], f["detail"][:96]))

    # ORPHAN WORKTREES, beside claim reconcile because it answers the other half
    # of the same crash: reconcile names an abandoned CLAIM, this names the
    # LANE a SIGKILLed run left on disk that nobody is naming. Reported, never
    # acted on, exactly like reconcile above: deciding a lane is safe to remove
    # is a judgement about its side effects this bridge cannot make.
    if worktree_lane is None:
        print("NO-DATA: the worktree lane module could not be loaded, so "
              "orphaned lanes were not checked", file=sys.stderr)
    elif not args.cwd:
        print("NO-DATA: no --cwd was given, so there is no repository to check "
              "for orphaned lane worktrees", file=sys.stderr)
    else:
        orphans, orphan_why = worktree_lane.orphan_report(args.cwd, store)
        if orphans is None:
            print("NO-DATA: %s" % orphan_why, file=sys.stderr)
        else:
            for o in orphans:
                print("%-10s %-10s %-50s owner=%s  %s"
                      % (o["classification"], o["unit_id"] or worktree_lane.NODATA,
                         o["path"], o.get("owner") or worktree_lane.NODATA,
                         o["detail"]))

    batch = dispatchable(plan)
    claimed, blocked = [], []
    for node in batch:
        claim, problem = claim_store.acquire(store, node["id"], owner,
                                             work_id=args.work_id)
        if claim is None:
            blocked.append((node["id"], problem))
            continue
        claimed.append((node, claim))

    for nid, problem in blocked:
        print("NOT CLAIMED %-10s %s" % (nid, problem[:100]))
    if not claimed:
        print("nothing was claimable: %d unit(s) were ready and every one is "
              "held elsewhere or unclaimable" % len(batch), file=sys.stderr)
        return 2

    print("CLAIMED (%d): %s" % (len(claimed),
                                ", ".join(c["worker_id"] for _n, c in claimed)))

    # P0.2, the composition wave: the real coding-model worker is the DEFAULT,
    # not an opt-in a caller has to remember. Until now an omitted --worker-cmd
    # silently fell back to ["true"], which claims and releases every unit
    # without doing anything, so a run looked complete while nothing was
    # written. --worker-cmd still overrides for anyone who wants a different
    # or stubbed worker, exactly as before.
    default_worker_cmd = [sys.executable, os.path.join(HERE, "model_worker.py")]
    worker = (LaneWorker(parts["spawn"], [sys.executable, "-c", "pass"])
              if args.null_worker
              else LaneWorker(parts["spawn"], args.worker_cmd or default_worker_cmd))

    outcome = run({"batch": [n for n, _c in claimed],
                   "refused": plan.get("refused", [])},
                  parts, worker, cwd=args.cwd, max_attempts=args.max_attempts)

    # RELEASE WITH THE STATE IT ENDED IN, so the record says what happened
    # rather than merely that somebody once held it.
    by_id = {r.get("id"): r for r in outcome.get("dispatched", [])}
    # THE JOIN, 2026-08-29: dispatch and integration were both real and not
    # yet connected, so a green unit was a green unit and never a merged one.
    # Now a unit that is green AND scope-clean goes through serial integration,
    # one at a time, each verified ON the canonical revision the previous one
    # produced. A unit is "done" only when it INTEGRATED: green-in-lane but
    # failing on the advanced base releases as failed, with the new base named,
    # because closed-but-not-landed is the lie the whole spine exists to stop.
    iso = outcome.get("isolation") or {}
    integrated = {}
    if integrate_mod is not None and args.cwd and iso.get("isolated"):
        lane_branches = {uid: "lane/" + "".join(
            ch if ch.isalnum() or ch in "-_" else "-" for ch in uid)[:48]
            for uid in iso.get("lanes", {})}
        units_by_id = {n["id"]: n for n, _c in claimed}
        results = [by_id.get(n["id"]) or {"id": n["id"]} for n, _c in claimed]
        for verdict in integrate_mod.integrate(args.cwd, results, lane_branches,
                                               units_by_id):
            integrated[verdict["unit"]] = verdict
            print("  integrate %-10s %-26s %s"
                  % (verdict["unit"], verdict["verdict"],
                     str(verdict.get("reason", ""))[:80]))
    elif integrate_mod is None:
        print("NO-DATA: the integration module could not be loaded, so green "
              "units were NOT merged and are released as failed rather than "
              "silently closed", file=sys.stderr)

    # T1 FOLLOW-UP: fold this round's real usage into the sidecar beside the
    # claim store (read-merge-write, so an earlier round's units are not
    # lost when this round releases different ones; see usage_sidecar_path
    # above for why this is a sidecar rather than a claim_store.py field).
    usage_path = usage_sidecar_path(store)
    usage_data = read_usage_sidecar(usage_path)
    for node, _claim in claimed:
        usage = (by_id.get(node["id"]) or {}).get("usage")
        if isinstance(usage, dict) and usage:
            usage_data[node["id"]] = usage
    if usage_data:
        write_usage_sidecar(usage_path, usage_data)

    for node, _claim in claimed:
        rec = by_id.get(node["id"]) or {}
        int_verdict = integrated.get(node["id"]) or {}
        # ALREADY-INTEGRATED counts as merged, and getting this wrong would have
        # been worse than the defect the resolver closes: a resumed unit whose lane
        # was already in canonical would be released as FAILED, so a crash recovery
        # would end by marking finished work broken. Both verdicts mean the same
        # thing about the tree, which is the only thing this line asks.
        merged = int_verdict.get("verdict") in ("INTEGRATED", "ALREADY-INTEGRATED")
        state = "done" if merged else "failed"
        # ROW E1: the claim carries what integrate.py actually observed, not
        # only the state string. A record that later reads this claim (see
        # brother_run._mark_integrated) refuses to call a unit integrated
        # unless this evidence is here and independently checks out.
        claim_store.release(store, node["id"], owner, state=state,
                            evidence=int_verdict.get("evidence"))
        print("  %-10s %-8s scope=%-10s integrated=%s"
              % (node["id"], state,
                 (rec.get("scope") or {}).get("verdict"), merged))

    print("isolation: %s%s" % ("per-writer worktrees" if iso.get("isolated")
                               else "NOT established", 
                               (", " + iso["note"]) if iso.get("note") else ""))
    failed = [r for r in outcome.get("dispatched", []) if r.get("verdict") != "PASS"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

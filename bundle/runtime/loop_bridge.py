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
import hashlib
import math
import tempfile
import json
import re
import subprocess
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402
import fault_barrier  # noqa: E402
import graph_loop  # noqa: E402
import journal  # noqa: E402
import run_heartbeat  # noqa: E402


def _fault_barrier(name):
    """Call the shared, gated fault-injection barrier."""
    fault_barrier.wait(name)


def _lane_branches(iso):
    """Return the canonical lane names for every recorded lane id.

    This remains the compatibility helper for lane cleanup. Integration uses
    ``integrable_branches`` below, which refuses a unit whose acquisition
    record says that it never actually received this branch.
    """
    return {uid: worktree_lane.branch_for(uid) for uid in iso.get("lanes", {})}

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

#: The hub checkout's own copy of the engine, so a developer running
#: scripts/ directly needs no configuration. It lives only in this repo's
#: own working tree (products/brothermode/tools) and never exists inside an
#: installed plugin, so it is skipped there.
HUB_CANDIDATE = os.path.normpath(
    os.path.join(HERE, "..", "products", "brothermode", "tools"))

#: The development fallback, kept LAST and named as such. It is this estate's own
#: layout and it is not a contract anybody else inherits.
DEV_CANDIDATE = os.path.expanduser("~/Documents/BrotherModeUp/tools")

#: The plugin cache is VERSIONED: cache/brother/brothermode/<version>/tools.
#: brothermode is the SIBLING plugin from the same marketplace that carries
#: the loop engine (bm_worker_spawn, bm_verify, bm_repair): it is not
#: bundled into brother itself because the closure is too large a slice of
#: brothermode to duplicate (bm_worker_spawn alone pulls in bm_controller,
#: bm_store, bm_plan, bm_messages and bm_fence_hook). Claude installs it
#: through the dependency declared in bundle/.claude-plugin/plugin.json.
#: Codex has no dependency resolution, so README.md's Codex install adds it
#: by name (`codex plugin add brothermode@brother --json`). config_root is
#: CLAUDE_CONFIG_DIR or ~/.claude under Claude and CODEX_HOME or ~/.codex
#: under Codex, resolved by _config_root(). A 3.4.2 install predates these
#: modules (measured: brothermode 3.4.2 in the plugin cache has no
#: bm_worker_spawn), which is why resolution walks every versioned install
#: rather than stopping at the first directory that exists.
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
    _fault_barrier("after_claim_before_edit")
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
    # Portability release (2026-09-06): the brother plugin carries the
    # brothermode tools itself, mirrored by scripts/bundle_runtime.py to
    # <plugin>/runtime/hooks/brothermode/tools beside this very file once
    # installed. A Codex or Claude home holding ONLY brother@brother (no
    # brothermode plugin, no standalone skill) finds its worker adapter
    # here, which is the E84 class of failure closed for the one-plugin
    # end state. In the hub checkout this path does not exist and is
    # simply skipped like every other absent candidate.
    out.append(os.path.normpath(
        os.path.join(HERE, "hooks", "brothermode", "tools")))
    out.append(HUB_CANDIDATE)
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


def not_found_message(looked, skipped):
    """The NO-DATA sentence for when none of `looked` loaded the three
    modules, `skipped` carrying an import-failure detail per directory that
    existed but did not work. Names the fix, not just the failure: the
    engine ships in the brothermode plugin from the same marketplace, so a
    reader who hits this knows the next command to run rather than only
    that something was missing."""
    detail = ("; ".join(skipped) + ". ") if skipped else ""
    return ("the loop's worker, verifier and repair modules were not "
            "found. %sLooked, in order: %s. The engine ships in the "
            "brothermode plugin from the same marketplace: run `claude plugin "
            "install brothermode@brother` or `codex plugin add "
            "brothermode@brother --json`, or set %s to a checkout to "
            "override." % (detail, ", ".join(looked), RUNTIME_ENV_VAR))


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
    return None, not_found_message(looked, skipped)


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


def machine_wide_refusal(plan):
    """The one sentence a human needs when NOTHING got dispatched, or "".

    WHY THIS EXISTS. On 2026-08-31 eleven units were ready, none was claimed,
    and every refusal said 'no free slot: capacity is 0' because free disk had
    fallen under graph_loop's floor. The scheduler was right and it printed its
    reasons per unit, but the run READ as a normal quiet round, so the watchdog
    looked asleep, the agents looked unassigned, and the whole EVAD follow-up
    track sat untouched for a day before a human asked why. The founder found
    it, not the tooling.

    The distinction that matters, and the reason this is not just a louder
    print: a unit deferred BLOCKED-BY another unit is the plan working, and a
    quiet round then is correct. A round where nothing dispatched and the
    refusals are NOT dependencies is the machine refusing, which is actionable
    and belongs in front of a person. Reports only the second case, so the
    alert cannot become noise that gets tuned out.
    """
    if dispatchable(plan):
        return ""
    reasons = [why for _nid, why in refused(plan)]
    if not reasons:
        return ""
    non_dependency = [r for r in reasons if not r.startswith("BLOCKED-BY")]
    if not non_dependency:
        return ""
    # The shared reason, when there is one, is the actionable sentence.
    unique = sorted(set(non_dependency))
    if len(unique) == 1:
        return ("%d unit(s) were ready and NONE could start, every one refused "
                "for the same reason: %s. This is the machine refusing, not the "
                "plan waiting, so it needs a person rather than another round."
                % (len(reasons), unique[0]))
    return ("%d unit(s) were ready and NONE could start. %d of the refusals are "
            "not dependency waits: %s. This is the machine refusing, not the "
            "plan waiting, so it needs a person rather than another round."
            % (len(reasons), len(non_dependency), "; ".join(unique[:3])))


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
    #
    # FX-A: a node can carry its OWN baseline, and one route needs it. On the
    # session route the lane was opened in an earlier process and the session
    # wrote in it before this one started, so reading HEAD here would take the
    # baseline AFTER the work and compare the tree to itself, which is exactly
    # the mistake the line above refuses to make on the spawned route. The
    # handoff records the revision each lane was opened at and stamps it here,
    # so the scope audit measures what the session actually changed.
    before = node.get("base_revision") or _head(cwd)

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

    if worker_result.get("retry_safe") is False:
        reason = worker_result.get("note", "worker replay requires review")
        beat.done(node["id"], reason)
        return {"id": node["id"], "worker_status": worker_result.get("status"),
                "verdict": "NO-DATA", "reason": reason, "repair": None,
                "scope": None, "integrable": False, "integration_block": reason,
                "failure_class": failure_class_of(worker_result)}

    # WHAT ACTUALLY CHANGED, from git, not from what the worker says it changed.
    # A worker reporting "I only touched X" is a claim; the diff is evidence.
    beat.phase(node["id"], "reading what actually changed")
    scope = _audit_scope(unit, before, cwd)

    beat.phase(node["id"], "running the done check")
    _fault_barrier("after_edit_before_check")
    verdict = verify.verify(unit, cwd=cwd)
    # SR-4: the breaker reads ONLY this token, from the worker's own failure
    # text, never from status or verdict. See failure_class_of() below.
    record = {"id": node["id"], "worker_status": worker_result.get("status"),
              "verdict": verdict.get("verdict"), "reason": verdict.get("reason"),
              "repair": None, "scope": scope,
              "failure_class": failure_class_of(worker_result)}
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
    refusal_reader = getattr(worker, "replay_refusal", lambda uid: None)
    refused = refusal_reader(node["id"])
    if refused:
        reason = refused.get("note", "worker replay requires review")
        # A SCOPE VIOLATION OUTRANKS A BUDGET REFUSAL (WBS-80, 2026-09-13):
        # record["integration_block"] was already set above, from the
        # scope audit taken BEFORE repair ever ran, and it names the exact
        # undeclared file ("QUARANTINE: ... never declared: requirements
        # .txt"). Overwriting it with the worker's own generic "ran out of
        # attempts" note (this block, unconditionally, until now) threw
        # that specific fact away for any unit whose check never went
        # green on the first try -- exactly the unit a person most needs
        # the file name for. Kept as the leading clause when one was
        # already recorded; the budget note still follows, since both are
        # true and a person reading the receipt should see why no further
        # repair was attempted too.
        if record.get("integrable") is False and record.get("integration_block"):
            reason = "%s (%s)" % (record["integration_block"], reason)
        record.update(verdict="NO-DATA", reason=reason, integrable=False,
                      integration_block=reason,
                      failure_class=failure_class_of(refused))
        beat.done(node["id"], reason)
        return record
    record["verdict"] = fixed["final_verdict"].get("verdict")
    beat.done(node["id"], "done after repair: %s" % (record["verdict"] or "?"))
    return record


#: SR-4: the recorded night six lanes died on one account limit, each
#: burning its own three attempts before anyone noticed it was the same
#: failure six times over. Three consecutive rate_limit/overloaded outcomes,
#: anywhere across lanes, open the breaker.
BREAKER_OPEN_AFTER = 3
#: Seconds a freshly-opened breaker refuses new dispatch before granting one
#: half-open trial.
BREAKER_COOLDOWN_SECONDS = 120
#: Run-wide ceiling on how many times the breaker may (re)open in one run.
#: Past this it is dead for good: a breaker that keeps reopening forever is
#: not protecting the run, it is the run.
BREAKER_MAX_FAILOVERS = 10

#: The only vocabulary a failure class is ever read from: the worker's own
#: failure text, exactly as SR-1 emits it on its own branch. Never inferred
#: from status or verdict; absent token reads as "other".
_FAILURE_CLASS_RE = re.compile(
    r"failure_class=(rate_limit|overloaded|timeout|empty|other)")
#: The only two classes the breaker counts. Every other outcome, a clean
#: success included, resets the consecutive count: one timeout is not the
#: account being down.
_BREAKER_CLASSES = ("rate_limit", "overloaded")


def failure_class_of(worker_result):
    """The failure_class token from a worker result's own 'note', or
    'other' when none is present."""
    text = (worker_result or {}).get("note") or ""
    m = _FAILURE_CLASS_RE.search(text)
    return m.group(1) if m else "other"


class Breaker(object):
    """Cross-lane circuit breaker over rate_limit / overloaded outcomes.

    CLOSED admits everything. BREAKER_OPEN_AFTER consecutive rate_limit-or-
    overloaded outcomes OPEN it: no new unit is admitted until
    BREAKER_COOLDOWN_SECONDS has elapsed, at which point exactly one
    HALF-OPEN trial is admitted. That trial's outcome decides what is next:
    any other class CLOSES the breaker; another rate_limit/overloaded
    REOPENS it and counts one failover. Past BREAKER_MAX_FAILOVERS the
    breaker is DEAD for the rest of the run, whatever the clock says.

    clock and sleep are injected so a test can freeze time and observe the
    pause without waiting on it; production leaves both at their real
    stdlib defaults.
    """

    def __init__(self, open_after=BREAKER_OPEN_AFTER,
                cooldown=BREAKER_COOLDOWN_SECONDS,
                max_failovers=BREAKER_MAX_FAILOVERS,
                clock=time.time, sleep=time.sleep):
        self.open_after = open_after
        self.cooldown = cooldown
        self.max_failovers = max_failovers
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self.state = "closed"  # closed | open | dead
        self.consecutive = 0
        self.failover_count = 0
        self.opened_at = None
        self._trial_active = False
        self._open_consecutive = 0
        self._open_class = ""

    def admit(self):
        """True if a NEW unit may be dispatched right now."""
        with self._lock:
            if self.state == "dead":
                return False
            if self.state == "closed":
                return True
            if self._trial_active or self.clock() - self.opened_at < self.cooldown:
                return False
            self._trial_active = True  # the one half-open trial
            return True

    def record(self, failure_class):
        """The outcome of one dispatched unit (a run_node record's own
        'failure_class', 'other' for a clean success)."""
        with self._lock:
            if self.state == "dead":
                return
            was_trial = self._trial_active
            self._trial_active = False
            if failure_class not in _BREAKER_CLASSES:
                self.consecutive = 0
                if self.state == "open" and was_trial:
                    self.state = "closed"
                    print("breaker closed: the half-open trial returned %s"
                         % failure_class, file=sys.stderr)
                return
            self.consecutive += 1
            # A failed half-open trial reopens at once with a fresh cooldown;
            # without it the stale opened_at let the next admit() grant
            # another trial immediately (PASS3 QA, 2026-09-11).
            if was_trial or self.consecutive >= self.open_after:
                self._open(failure_class)

    def _open(self, failure_class):
        self.state = "open"
        self.opened_at = self.clock()
        self.failover_count += 1
        self._open_consecutive = self.consecutive
        self._open_class = failure_class
        self.consecutive = 0
        print("NO-DATA: breaker open on %s (consecutive=%d), cooling down "
             "%ss (failover %d/%d)"
             % (failure_class, self._open_consecutive, self.cooldown,
                self.failover_count, self.max_failovers), file=sys.stderr)
        self.sleep(self.cooldown)
        if self.failover_count >= self.max_failovers:
            self.state = "dead"
            print("NO-DATA: breaker dead for good: %d failovers reached "
                 "the run-wide cap of %d"
                 % (self.failover_count, self.max_failovers),
                 file=sys.stderr)

    def refusal_reason(self):
        if self.state == "dead":
            return ("breaker dead for good: %d failovers reached the "
                    "run-wide cap of %d"
                    % (self.failover_count, self.max_failovers))
        return ("breaker open: %d consecutive %s failures, cooling down "
                "for %ss" % (self._open_consecutive, self._open_class,
                            self.cooldown))


#: The module-wide default: a caller that never names its own Breaker still
#: gets cross-lane protection, and every test that cares about isolation
#: resets this in its own setUp() rather than relying on one never firing.
_BREAKER = Breaker()


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

try:
    import unit_trace
except Exception:  # noqa: BLE001
    unit_trace = None

MAX_IN_FLIGHT = 3


def worker_budget_path(root, unit_id):
    key = hashlib.sha256(str(unit_id).encode("utf-8")).hexdigest()
    return os.path.join(root, "worker-budgets", key + ".json")


def worker_budget_refusal(root, unit_id):
    """A resume must not settle ambiguous writes before worker admission."""
    if not root:
        return None
    path = worker_budget_path(root, unit_id)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:  # sbe: allow-silent no prior worker wrote a budget file, so there is nothing to refuse
        return None
    except (OSError, ValueError):
        return "worker budget is unreadable; inspect the lane before resuming"
    if not isinstance(state, dict) or not isinstance(state.get("in_flight"), bool):
        return "worker budget is malformed; inspect the lane before resuming"
    if state["in_flight"]:
        return "earlier worker may have written; inspect the lane before resuming"
    return None


class LaneWorker(object):
    """A spawning worker that runs each unit IN ITS LANE.

    The underlying worker binds cwd at construction, which is right for a
    single-tree world and wrong here: the whole point of a lane is that the
    worker's writes land in it. So this builds one spawning worker per run,
    at the lane the dispatcher hands it.

    P1, night-hardening-2026-09-07: THIS IS WHERE A BROTHER-MANAGED WORKER
    BECOMES SAFE BY CONSTRUCTION rather than by an operator remembering a
    setup ritual. Before a worker is spawned, managed_safety.materialize()
    creates (or reuses) the lane's own store and claims the unit's declared
    write scope under a fresh session, THEN the worker is spawned with that
    session wired into its environment. No worker starts before its claim
    exists (docs/plan/runs/night-2026-09-07/design-P1.md section 3). The
    managed_safety import is local to this method, not at module scope,
    because managed_safety.py itself imports this module (to reuse its
    sibling-tools resolution) and a module-level import here would be a
    cycle; importing it lazily inside the one method that needs it is the
    standard way out of that without inventing a third module."""

    def __init__(self, spawn_module, argv, environ=None):
        self._spawn, self._argv, self._environ = spawn_module, list(argv), environ
        self._budgets = {}
        self._refusals = {}

    def replay_refusal(self, unit_id):
        return self._refusals.get(unit_id)

    def run(self, unit, cwd=None):
        """One shared time/attempt allowance, including repair and fallback.

        Save the in-flight marker before dispatch. A restart cannot prove an
        interrupted process did not write, so it must not replay that unit.
        Rate-limit parking spends no attempt; only worker execution consumes
        the time allowance, not time parked waiting for a provider reset.
        """
        uid = str(unit.get("unit_id") or "")
        root = journal.run_dir_from_env()
        path = None
        if root:
            path = worker_budget_path(root, uid)
        def held(why, failure="other"):
            result = {"status": "held", "worker_claim": "", "artifacts": [],
                      "retry_safe": False,
                      "note": "failure_class=%s; %s" % (failure, why)}
            self._refusals[uid] = result
            return result
        def save(state):
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(state, fh)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, path)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
            self._budgets[uid] = state
        try:
            state = self._budgets.get(uid)
            if path and os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    state = json.load(fh)
            if state is None:
                state = {"remaining": float(getattr(self._spawn,
                             "DEFAULT_TIMEOUT_SECONDS", 900)),
                         "attempts": 0, "in_flight": False}
            remaining = state["remaining"]
            attempts = state["attempts"]
            if (not isinstance(remaining, (int, float))
                    or isinstance(remaining, bool) or not math.isfinite(remaining)
                    or not isinstance(attempts, int) or isinstance(attempts, bool)
                    or attempts < 0 or not isinstance(state["in_flight"], bool)):
                raise ValueError("invalid worker budget")
            if state["in_flight"]:
                # A1 FIX (fast-route orphan safety, WBS-10.07): in_flight
                # only ever means "the last attempt never came back to say
                # what happened" -- a live worker OR a dead one, and this
                # budget file alone cannot tell those apart. brother_run.py's
                # _settle_units_already_delivered writes "claim.orphaned_by_
                # kill" to this run's own journal the moment it proves the
                # claim is still "claimed" with its owner dead (a real kill,
                # never an ordinary release), before the round loop ever
                # makes a new claim. worktree_lane._crash_orphaned_claim
                # reads that same event for the same reason worktree_lane.py
                # already trusts it to decide lane reuse; reusing it here
                # closes the other half of the same crash: without this, a
                # unit killed mid-worker refuses every future attempt
                # forever, spending its whole retry budget on held() results
                # that never spawn a worker, and a bare resume never lands
                # it (the exact orphan this state exists to prevent).
                orphaned = bool(worktree_lane) and \
                    worktree_lane._crash_orphaned_claim(uid)
                if not orphaned:
                    return held("earlier worker may have written; inspect "
                                "the lane before any replay", "timeout")
            if remaining <= 0 or attempts >= 3:
                return held("whole-unit time or attempt allowance exhausted")
            state.update(attempts=attempts + 1, in_flight=True)
            save(state)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return held("worker budget could not be read or recorded: %s" % exc)
        self._refusals.pop(uid, None)
        started = time.monotonic()
        result = self._run(unit, cwd=cwd, timeout=remaining)
        elapsed = max(0.0, time.monotonic() - started)
        note = result.get("note") or ""
        timed_out = (failure_class_of(result) == "timeout"
                     or "no answer within" in note or "timed out" in note
                     or elapsed >= remaining)
        state["remaining"] = max(0.0, remaining - elapsed)
        state["in_flight"] = timed_out
        if failure_class_of(result) == "rate_limit" and not timed_out:
            state["attempts"] -= 1
        try:
            save(state)
        except OSError as exc:
            return held("worker ended but its budget could not be recorded: %s" % exc)
        if timed_out:
            return held("worker timed out; writes are unknown, so automatic "
                        "repair and replay are refused", "timeout")
        if result.get("status") == "held":
            return held(result.get("note") or "worker was held before dispatch")
        return result

    def _run(self, unit, cwd=None, timeout=900):
        # VN3b, THE UNIT ATTRIBUTION: this method is the ONLY place in this
        # estate that starts a process for exactly one unit, so it is the
        # only place a unit id is honestly known to a child. Everything
        # brother_run exports is run-scoped (journal.RUN_DIR_ENV_VAR, set
        # once for the whole run), which is why every event a hook inside a
        # worker journalled until now carried unit_id None and could never
        # be picked up by _recalled_records_for_unit's own unit match.
        # Exported on BOTH branches below, and by name rather than by
        # keyword, because the variable's name lives in journal.py beside
        # the run directory's own. A unit with no id exports nothing: an
        # absent variable reads as None, which is what it means.
        #
        # dict(self._environ or os.environ) on the no-lane branch below is
        # behaviour-neutral, not a widening: bm_controller._sanitised_env
        # already reads os.environ when it is handed None, so a copy of
        # os.environ is the same environment that branch always passed.
        deadline = time.monotonic() + timeout
        environ = dict(self._environ or os.environ)
        def launch():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"status": "held", "note": "failure_class=timeout; "
                        "unit allowance expired before worker launch"}
            environ["BROTHER_UNIT_TIME_LEFT_S"] = str(remaining)
            inner = self._spawn.SpawningWorker(self._argv, cwd=cwd,
                                               environ=environ, timeout=remaining)
            return inner.run(unit)
        unit_id = str(unit.get("unit_id") or "").strip()
        if unit_id:
            environ[journal.UNIT_ID_ENV_VAR] = unit_id
        if not cwd:
            # No lane at all (isolation disabled AND no shared cwd either,
            # theoretical but not impossible): nothing to materialize a
            # store against, so this falls back to the pre-P1 behaviour
            # rather than crashing on Store(None).
            return launch()
        import managed_safety  # local: see the class docstring for why
        session_id, why = managed_safety.materialize(cwd, unit)
        run_dir = journal.run_dir_from_env()
        journal.append(run_dir, "safety.claim",
                       parent_ids=journal.previous(run_dir),
                       unit_id=unit.get("unit_id"),
                       payload={"cwd": cwd, "held": session_id is None,
                                "session": session_id, "why": why})
        if session_id is None:
            # HELD, not started: the claim step itself is the mutation
            # boundary, so nothing has touched the lane yet. Shaped exactly
            # like bm_worker_spawn._result()'s contract (worker_claim,
            # artifacts, cost, status) so every downstream reader that
            # already handles "unavailable" handles "held" the same way.
            return {"worker_claim": "", "artifacts": [],
                   "cost": {"tokens": 0, "minutes": 0},
                   "status": "held", "note": why}
        # BM_FENCE_STRICT=1 alongside enforced mode, not enforced mode
        # alone: the fence's DEFAULT rule only denies a write that collides
        # with ANOTHER session's active claim, and allows a write to any
        # path nobody has claimed at all (products/brothermode/tools/
        # test_bm_fence_hook.py's own StrictMode.test_strict_mode_is_off_by_
        # default proves this). A managed claim that only blocks collisions
        # is not what "claim before worker launch" is for; strict mode is
        # what makes the claim the OUTER BOUND of what this worker may
        # touch, denying anything the unit did not declare, not only
        # anything a rival session already holds.
        environ = dict(environ, BM_FENCE_MODE="enforced",
                      BM_FENCE_STRICT="1", BROTHERMODE_ROOT=cwd,
                      BM_FENCE_SESSION_ID=session_id)
        return launch()



#: FX-A: the file the session route hands across two processes, written under
#: the run directory in a SUBDIRECTORY rather than beside claims.json,
#: because brother_run picks a run's Work document as "the one *.json that is
#: neither claims nor target" and a third json at that level would break
#: --resume and --continue (the same reason the receipt lives in a directory).
HANDOFF_VERSION = 1

#: The one refusal word this file already prints inline everywhere below; named
#: once here so the two classes that follow spell it the same way.
NODATA = "NO-DATA"


class PreparedLanes(object):
    """Lanes somebody else already opened, in the shape run() expects.

    Deliberately NOT a worktree_lane.Lanes: that class ACQUIRES in its own
    constructor, and acquiring over an existing lane/<unit> branch destroys
    it. This holds what a previous process recorded and nothing else, so a
    lane holding a session's work is read, never recreated.

    A lane whose directory is gone since the handoff was written is a
    problem, not a silent omission: isolated goes False, why() names it, and
    run() drops writer concurrency to 1 exactly as it does for a lane that
    could not be created in the first place.
    """

    def __init__(self, lanes, missing=None):
        self.lanes = dict(lanes)
        self.problems = dict(missing or {})

    @property
    def isolated(self):
        return not self.problems and bool(self.lanes)

    def safe_concurrency(self, requested):
        if self.isolated:
            return max(1, min(int(requested), len(self.lanes)))
        return 1

    def why(self):
        if self.isolated:
            return "lanes prepared by an earlier process, reused as they are"
        if not self.lanes and not self.problems:
            return "%s: the handoff named no lanes" % NODATA
        return ("%d prepared lane(s) could not be used, so writer concurrency "
                "drops to 1: %s"
                % (len(self.problems),
                   "; ".join("%s (%s)" % (k, v)
                             for k, v in sorted(self.problems.items()))))

    def path_for(self, uid):
        lane = self.lanes.get(uid)
        return lane["path"] if lane else None



#: FX-A: the exit code that means "the units are claimed and the work is
#: yours". Distinct from 0 (this round finished) and from 1 (something in it
#: failed) because it is neither: the lanes are open, nothing has been
#: verified, and the caller is expected to do the work and come back.
EXIT_UNITS_ARE_YOURS = 3


def write_handoff(path, batch, cwd):
    """Open one lane per claimed unit and record them. Returns an exit code.

    The lane root is a directory beside `path`, so a run owns its lanes and
    they are findable from the run directory rather than from a temporary
    directory only the process that made them knew about. That is the whole
    difference from the spawned route, where a lane lives exactly as long as
    the process that opened it.

    NO PARTIAL HANDOFF. A batch where any lane failed to open is refused
    outright rather than handed over half-formed: a person told to work in
    two worktrees, one of which does not exist, is worse off than one told
    nothing opened. The lanes that DID open are named in the refusal so they
    can be looked at, never silently removed.
    """
    if worktree_lane is None:
        print("NO-DATA: the worktree lane module could not be loaded, so no "
              "lane was opened and nothing was handed over", file=sys.stderr)
        return 2
    root = os.path.join(os.path.dirname(os.path.abspath(path)), "lanes")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        print("NO-DATA: the lane root %s could not be created: %s"
              % (root, exc), file=sys.stderr)
        return 2
    lanes = worktree_lane.Lanes(cwd, [n["id"] for n in batch], root=root)
    if not lanes.isolated:
        print("NO-DATA: %s. Nothing was handed over" % lanes.why(),
              file=sys.stderr)
        return 2
    base = _head(cwd)
    record = {"version": HANDOFF_VERSION, "cwd": os.path.abspath(cwd or ""),
              "base_revision": base, "units": {}}
    for node in batch:
        uid = node["id"]
        lane = lanes.lanes[uid]
        record["units"][uid] = {
            "worktree": lane["path"], "branch": lane["branch"],
            "objective": node.get("name") or node.get("title") or uid,
            "done_check": node.get("done_check") or "",
            "writes": list(node.get("owns") or []),
            # The revision the lane was opened at, so a later process can
            # audit what the session changed against it rather than against
            # the lane's own tip after the fact (see run_node).
            "base_revision": base}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
    for uid in sorted(record["units"]):
        print("HANDED OVER %-10s %s" % (uid, record["units"][uid]["worktree"]))
    return EXIT_UNITS_ARE_YOURS


def read_handoff(path, batch):
    """(PreparedLanes, problem) for the units in `batch`, or (None, problem).

    Every unit in the batch must have a lane that still exists on disk. A
    missing one is refused rather than quietly re-acquired, because
    re-acquiring is what would destroy the branch holding the session's own
    work; the caller is told which lane is gone and can look before anything
    is written."""
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "the handoff at %s could not be read: %s" % (path, exc)
    units = record.get("units")
    if not isinstance(units, dict):
        return None, "the handoff at %s names no units" % path
    lanes, missing = {}, {}
    for node in batch:
        uid = node["id"]
        lane = units.get(uid)
        if not isinstance(lane, dict) or not lane.get("worktree"):
            missing[uid] = "the handoff names no lane for it"
            continue
        if not os.path.isdir(lane["worktree"]):
            missing[uid] = "its lane %s is gone" % lane["worktree"]
            continue
        lanes[uid] = {"path": lane["worktree"], "branch": lane.get("branch")}
        # The baseline the scope audit measures against, carried on the node
        # itself because run_node takes no place else to put it.
        node["base_revision"] = lane.get("base_revision")
    prepared = PreparedLanes(lanes, missing)
    if not prepared.isolated:
        return None, prepared.why()
    return prepared, ""


class SessionWorker(object):
    """The worker for a unit the SESSION did, which spawns nothing.

    FX-A. model_worker.py resolves its command from MODEL_WORKER_CMD or falls
    back to the host's own headless client, and inside a coding session that
    client cannot be reached: it hangs or exits with an empty error. D-001
    closed that for the decomposer; this closes it for the worker. Inside a
    session the units are handed to the session's own model in their own
    worktrees, and this stands where the spawned worker stood so that
    everything after it (the scope audit, the check, the integration, the
    receipt) is the SAME code on both routes rather than a second path that
    could drift.

    Its one real job is the commit. Nothing else in the spine commits a
    unit's work and integrate.py merges the lane BRANCH rather than its
    working tree, so a session that only edited files would integrate as
    nothing at all. model_worker.commit_changes is that step, reused
    outright: same staging rules, same exclusion of bytecode, same message
    shape. A session that already committed reaches it as a no-op.
    """

    def __init__(self, commit=None):
        if commit is None:
            import model_worker
            commit = model_worker.commit_changes
        self._commit = commit

    def run(self, unit, cwd=None):
        unit_id = str(unit.get("unit_id") or unit.get("id") or "?")
        if not cwd:
            return {"worker_claim": "", "artifacts": [],
                    "cost": {"tokens": 0, "minutes": 0}, "status": "unavailable",
                    "note": "%s: this unit has no lane, so there is no tree the "
                            "session could have written in" % NODATA}
        committed, detail = self._commit(cwd, unit_id)
        return {"worker_claim": "the session did this unit in its own lane: %s"
                                % detail,
                "artifacts": [], "cost": {"tokens": 0, "minutes": 0},
                "status": "ok" if committed else "unavailable", "note": detail}


def run(plan, parts, worker, cwd=None, max_attempts=3, max_in_flight=None,
        isolate=True, lanes=None, breaker=None):
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
    # FX-A: LANES THE CALLER ALREADY HOLDS are used as they are, never
    # re-acquired. worktree_lane.acquire() deliberately DESTROYS a leftover
    # lane/<unit> branch before creating a new one (its own STALE LANE
    # REFUSAL clause), which is right for a crashed run and catastrophic for
    # the session route, where that branch holds the work the session was
    # just asked to do. So a prepared Lanes-shaped object skips acquisition
    # entirely; nothing else about this function changes.
    lane_note = "" if lanes is None else lanes.why()
    if lanes is not None:
        cap = lanes.safe_concurrency(cap)
    elif isolate and batch and cwd:
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

    # SR-4: read at call time, not as a default parameter, so a test's own
    # `B._BREAKER = B.Breaker()` reassignment is seen here.
    brk = breaker if breaker is not None else _BREAKER

    def _dispatch_one(node, cwd_n):
        """run_node, gated by the breaker: an open breaker refuses before
        the worker is ever asked, so a refused unit burns no attempt."""
        if not brk.admit():
            return {"id": node.get("id"), "worker_status": None,
                    "verdict": "NO-DATA", "reason": brk.refusal_reason(),
                    "repair": None}
        rec = run_node(node, parts, worker, cwd_n, max_attempts)
        brk.record(rec.get("failure_class", "other"))
        return rec

    results = [None] * len(batch)
    if batch and cap > 0:
        with concurrent.futures.ThreadPoolExecutor(max_workers=cap) as pool:
            futures = {pool.submit(_dispatch_one, n, _cwd_for(n)): i
                       for i, n in enumerate(batch)}
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    # sbe: allow-silent the exception becomes this node's record
                    brk.record("other")
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
                 # H2: the branch each lane ACTUALLY got, not a name rebuilt
                 # from the unit id. acquire() returns branch=None when its
                 # own `git checkout -b` failed ("a lane without its own
                 # branch is still isolated"), and a caller that reconstructs
                 # the name anyway can pick up a stale branch of the same
                 # name left by an abandoned attempt. See integrable_branches().
                 "branches": {uid: lane["branch"]
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


def integrable_branches(isolation):
    """(branches, refused) from an isolation record's own `branches` map.

    H2: a lane's branch must be carried, never rebuilt from the unit id.
    acquire() returns branch=None when its own `git checkout -b` failed ("a
    lane without its own branch is still isolated"), and reconstructing the
    name anyway risks merging a stale branch of the same name left by an
    abandoned attempt. So only a truthy branch is kept; every other unit is
    refused by name, with a NO-DATA reason, rather than guessed.

    An isolation record carrying no `branches` map at all (an older shape,
    or none given) yields nothing here, on either side: there is nothing
    real to integrate from and nothing invented to stand in for it."""
    branches_in = (isolation or {}).get("branches") or {}
    branches, refused_units = {}, {}
    for uid, branch in branches_in.items():
        if branch:
            branches[uid] = branch
        else:
            refused_units[uid] = ("NO-DATA: unit %s has no lane branch on "
                                  "record (its own checkout failed), so "
                                  "nothing was merged for it" % uid)
    return branches, refused_units


def _reclaim_unmerged_lanes(cwd, lane_branches, why):
    """Retire clean, already-landed lanes after a failed batch isolation."""
    if not cwd or not lane_branches:
        return
    print("NO-DATA: %s; each lane created for this round is still retired "
          "through cleanup_lane" % why, file=sys.stderr)
    for uid, branch in sorted(lane_branches.items()):
        integrate_mod.cleanup_lane(cwd, branch, uid)


def conflict_note(int_verdict):
    """One clear line naming a CONFLICT, or None when `int_verdict` is not
    one. WBS-10.06 (clear conflict handling): integrate.integrate_one
    already detects a lane that no longer applies to canonical, aborts the
    merge, and returns a reason -- but the caller below folded CONFLICT
    into the same generic "failed" state a broken done_check produces, so
    a person reading that state could not tell a stale worktree from a
    real bug. This is the missing name, kept as a pure function so it is
    testable without a real git tree: given the dict integrate_one (or
    merge_queue's own mapping of it) returns, say whether it was this."""
    if not int_verdict or integrate_mod is None:
        return None
    if int_verdict.get("verdict") != integrate_mod.CONFLICT:
        return None
    return ("CONFLICT, not a failed check: unit %s's lane no longer "
            "applies to canonical; rebase or re-plan it. %s"
            % (int_verdict.get("unit", "?"), int_verdict.get("reason", "")))


def rolling_dispatch(plan_ready, start, wait_any, integrate, cap,
                     live_view=None, breaker=None):
    """PLAN, START, WAIT, INTEGRATE, with everything injected so its
    guarantees test without git, models or sleeping. PURE ORCHESTRATION: not
    wired into main() or run() by this unit; this lands the seam and its
    proof only, and nothing in the live path changes here.

    `plan_ready(live_ids)` returns the unit ids admissible right now, given
    what is live; `start(uid)` begins one and returns a handle; `wait_any
    (list(live))` blocks for exactly one live handle to finish and returns
    (handle, result); `integrate(unit, result)` is called SERIALLY, on this
    thread, strictly between the wait that produced a result and the next
    fill, so two integrations can never overlap.

    `live` maps a handle to the unit id it is running. Never exceeds `cap`,
    never starts the same id twice in this run (even a planner that keeps
    re-offering a live id is refused by the `started` guard below, not
    trusted to police itself). When nothing is live, this returns rather
    than asking `plan_ready` forever, so a blocked graph terminates instead
    of spinning. `live_view["live"]`, when `live_view` is given, is kept as
    the current live id set after every start and every completion.

    WIRED, W9.5b: rolling_run() below assembles the four callables from the
    real claim store, real worktree lanes, a real spawned worker per unit
    and real serial integration, and calls this unchanged. Nothing about
    this function's own contract moved to make that possible."""
    live = {}
    started = set()
    records = []
    # SR-4: read at call time, not as a default parameter, so a test's own
    # `B._BREAKER = B.Breaker()` reassignment is seen here.
    brk = breaker if breaker is not None else _BREAKER

    def _report():
        if live_view is not None:
            live_view["live"] = set(live.values())

    while True:
        while len(live) < cap:
            ready = [uid for uid in (plan_ready(list(live.values())) or [])
                    if uid not in started]
            if not ready:
                break
            for uid in ready:
                if len(live) >= cap:
                    break
                # An open (or dead) breaker refuses admission before start()
                # is ever called, so a refused unit never spawns a worker.
                # Marked started so a constant plan_ready() cannot re-offer
                # it forever.
                if not brk.admit():
                    started.add(uid)
                    records.append({"id": uid,
                                    "record": {"verdict": "NO-DATA",
                                              "reason": brk.refusal_reason()}})
                    continue
                handle = start(uid)
                live[handle] = uid
                started.add(uid)
                _report()
        if not live:
            return records
        handle, result = wait_any(list(live))
        uid = live.pop(handle)
        _report()
        records.append(integrate({"id": uid}, result))


def _row_for(doc, uid):
    """The live row/feature dict inside `doc` for `uid`, or None.

    graph_loop.plan() derives its `done` set fresh from `doc` on every call
    (n['status'] in ('DONE', ...)), so mutating the SAME dict object rolling_
    run() was handed, rather than a copy, is what lets a unit's integration
    unlock a dependent on the very next plan_ready() call. `nodes(doc)`
    builds fresh dicts and is useless for this; this walks `doc` itself."""
    for r in doc.get("rows", []) + doc.get("features", []):
        if r.get("id") == uid:
            return r
    return None


def rolling_run(doc, parts, worker, cwd, cap, store, owner=None, work_id="",
                max_attempts=3, isolate=True, live_view=None, run_id=None,
                harness_revision=None):
    """The live wiring for rolling_dispatch(): real durable claims, real
    isolated lanes, a real spawned worker per unit, real serial integration,
    one unit dispatched the instant any other finishes rather than one whole
    batch at a time.

    THE GAIN OVER run(): run() computes ONE batch, dispatches all of it, and
    only then integrates; a unit whose sole blocker sat inside that same
    batch waits for the WHOLE batch to finish before it is even offered,
    which is a wave, not a rolling front. Here `plan_ready` is asked again
    the instant any one live unit completes, with the current live set
    folded into graph_loop.plan() via `also_in_flight` (its own H3
    parameter) on every call, so a dependent can start the moment its one
    dependency lands, beside unrelated siblings still running.

    LANES ARE PRE-OPENED ONCE, for every unit `doc` could dispatch this run
    (every node not already DONE, SUPERSEDED, ADDRESSED or IN-FLIGHT),
    through worktree_lane.Lanes exactly as run() opens them for its one
    known batch. `cap` is passed straight through Lanes.safe_concurrency():
    a lane that could not be created drops writer concurrency to 1 rather
    than ever sharing a tree, decided BEFORE the first unit is claimed. The
    batch that will actually run is not known in advance the way run()'s is
    (it grows as dependencies clear), so this opens lanes for the whole
    candidate set up front rather than reinventing the fail-closed rule
    per-unit; a unit whose lane failed to open is never offered by
    `plan_ready` (see `branch_refused` below), the same as a batch member
    run() could not isolate.

    CLAIM BEFORE SPAWN still holds although the claim is acquired inside the
    worker thread this function submits, immediately before run_node() can
    spawn anything: the invariant is about ORDER within one unit's own
    execution, not about which thread performs the acquire, and no worker
    for a unit starts before that unit's claim exists.

    Serial integration is a property of rolling_dispatch() itself (called
    unmodified, below): it runs `integrate_fn` on its own thread, strictly
    between the wait that produced a result and the next fill, so no thread
    pool is placed around it here."""
    import concurrent.futures

    owner = owner or ("pid-%d" % os.getpid())
    all_nodes = graph_loop.nodes(doc)
    by_id = {n["id"]: n for n in all_nodes}
    eligible = [n["id"] for n in all_nodes
               if n["status"] not in ("DONE", "SUPERSEDED", "ADDRESSED",
                                      "IN-FLIGHT")]

    lanes, lane_note = None, ""
    if isolate and eligible and cwd and worktree_lane is not None:
        lanes = worktree_lane.Lanes(cwd, eligible)
        cap = lanes.safe_concurrency(cap)
        lane_note = lanes.why()
    else:
        cap = 1
        lane_note = ("isolation is unavailable, so writer concurrency is 1 "
                     "rather than a shared tree")
    isolated = bool(lanes is not None and lanes.isolated)
    # H2, reused rather than rebuilt: only a branch a lane actually got is
    # ever merged from. A unit missing one here is simply never offered by
    # plan_ready below, exactly as a batch member run() could not isolate is
    # never dispatched.
    lane_branches, branch_refused = integrable_branches(
        {"branches": {uid: lane["branch"]
                     for uid, lane in (lanes.lanes.items() if lanes else [])}})

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, cap))
    claims = {}

    def _node_cwd(uid):
        if isolated:
            return lanes.path_for(uid) or cwd
        return cwd

    def _claim_and_run(node):
        # CLAIM BEFORE SPAWN, on this worker thread: the claim is acquired
        # and only then does run_node() have any chance to spawn a process.
        uid = node["id"]
        claim, problem = claim_store.acquire(store, uid, owner,
                                             work_id=work_id)
        if claim is None:
            return {"claim": None,
                    "record": {"id": uid, "verdict": "NO-DATA",
                              "reason": "could not claim: %s" % problem,
                              "integrable": False}}
        claims[uid] = claim
        # "CLAIMED (" is the claim-announcement substring every consumer of
        # this log greps for (test_receipt_door.py's MACHINERY, fault_lab.py,
        # tiny_task_cost.py, test_spine.py, test_repair_drain.py), a
        # convention run()'s own batch announcement set. Rolling dispatch
        # claims one unit at a time rather than a whole batch, so each unit
        # is its own batch of 1 here, never a count it did not actually
        # claim at once.
        print("CLAIMED (1): %s" % claim["worker_id"])
        print("CLAIMED    %-10s %s" % (uid, claim["worker_id"]))
        record = run_node(node, parts, worker, _node_cwd(uid), max_attempts)
        return {"claim": claim, "record": record}

    def plan_ready(live_ids):
        # H3: the live set goes straight to graph_loop.plan()'s own
        # also_in_flight parameter, on every re-plan, so a unit still
        # running (and therefore still holding its write set) is never
        # admitted a second time and never collides with a sibling.
        live_nodes = [by_id[u] for u in live_ids if u in by_id]
        p = graph_loop.plan(doc, slots=cap, also_in_flight=live_nodes)
        return [n["id"] for n in dispatchable(p) if n["id"] not in branch_refused]

    def start(uid):
        return pool.submit(_claim_and_run, by_id[uid])

    def wait_any(handles):
        done, _pending = concurrent.futures.wait(
            handles, return_when=concurrent.futures.FIRST_COMPLETED)
        fut = next(iter(done))
        try:
            result = fut.result()
        except Exception as exc:  # noqa: BLE001
            # sbe: allow-silent the exception becomes this unit's NO-DATA record
            result = {"claim": None,
                     "record": {"verdict": "NO-DATA",
                               "reason": "the worker raised %s: %s"
                                         % (type(exc).__name__, exc)}}
        return fut, result

    def integrate_fn(unit, result):
        uid = unit["id"]
        node = by_id[uid]
        claim = result.get("claim")
        record = result.get("record") or {}
        # FL-1.4: every dispatched unit leaves exactly one trace line, written
        # here (before claim_store.release() below) rather than in run_node(),
        # because this is the one place both the claim id and the final
        # verdict (post repair) are bound in scope for EVERY unit, not only
        # ones that go on to integrate. tier, effort and wall_ms are NO-DATA:
        # the 2026-09-11 spike (docs/... FL-1-SPIKE-2026-09.md) found nothing
        # in the dispatch path supplies them today. This must never raise and
        # never change the integration outcome, so both a reported problem
        # and a raised exception are printed to stderr and swallowed.
        if unit_trace is not None:
            try:
                usage = record.get("usage") or {}
                _trace_row, _trace_problem = unit_trace.record(
                    uid,
                    tokens_in=usage.get("tokens_in"),
                    tokens_out=usage.get("tokens_out"),
                    cache_read=usage.get("tokens_cached"),
                    verdict=record.get("verdict"))
                if _trace_problem:
                    sys.stderr.write("unit-trace: NO-DATA: %s\n"
                                     % _trace_problem)
            except Exception as exc:  # noqa: BLE001
                # sbe: allow-silent the trace must never block integration; the problem is printed above
                sys.stderr.write("unit-trace: NO-DATA: %s\n" % exc)
        branch = lane_branches.get(uid)
        merged, int_verdict, _note = False, {}, None
        # QUARANTINE (or NO-DATA scope) never integrates even on a PASS
        # verdict: run_node() already set integrable=False for those, so
        # gating on it here is the same rule this estate already enforces
        # in run(), not a second copy of the judgement.
        if isolated and branch and integrate_mod is not None and cwd:
            if record.get("integrable") and record.get("verdict") == "PASS":
                int_verdict = integrate_mod.integrate_one(
                    cwd, branch, node, run_id=run_id,
                    harness_revision=harness_revision)
                merged = int_verdict.get("verdict") in (
                    integrate_mod.INTEGRATED, integrate_mod.ALREADY_INTEGRATED)
                # WBS-10.06: name a conflict distinctly from a failed check
                # the moment it happens, on the same stderr a person already
                # watches, rather than leaving it indistinguishable inside
                # the generic "failed" state set below.
                _note = conflict_note(int_verdict)
                if _note:
                    print("loop_bridge: %s" % _note, file=sys.stderr)
            integrate_mod.cleanup_lane(cwd, branch, uid)
        # T1 FOLLOW-UP, same sidecar run() feeds: read-merge-write so an
        # earlier unit's usage is not lost when this one releases. Rolling
        # dispatch has no "round" to fold a batch into, so this runs once
        # per unit instead of once per round; the sidecar shape and path
        # are identical either way (usage_sidecar_path, read/write_usage_sidecar).
        usage = record.get("usage")
        if isinstance(usage, dict) and usage:
            usage_path = usage_sidecar_path(store)
            usage_data = read_usage_sidecar(usage_path)
            usage_data[uid] = usage
            write_usage_sidecar(usage_path, usage_data)

        state = "done" if merged else "failed"
        if claim is not None:
            claim_store.release(store, uid, owner, state=state,
                                evidence=int_verdict.get("evidence"),
                                attempt=claim.get("attempt"))
        if merged:
            # THE WORK DOCUMENT UNLOCKS THE NEXT plan_ready() CALL: see
            # _row_for()'s own docstring for why this must be the same dict
            # graph_loop.plan() reads, not a copy.
            row = _row_for(doc, uid)
            if row is not None:
                row["status"] = "DONE"
        return {"id": uid, "state": state, "record": record,
               "integration": int_verdict, "conflict_note": _note}

    try:
        records = rolling_dispatch(plan_ready, start, wait_any, integrate_fn,
                                   cap, live_view=live_view)
    finally:
        pool.shutdown(wait=True)

    return {"records": records, "in_flight_cap": cap,
           "isolation": {"isolated": isolated, "note": lane_note,
                         "branches": lane_branches},
           "branch_refused": branch_refused}


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
    # FX-A, the two halves of the session route. --handoff claims the batch,
    # opens its lanes and STOPS, writing the file that names them; --lanes
    # reads that file back in a later process and runs the round over the
    # lanes the session has since worked in, with no worker spawned at all.
    ap.add_argument("--handoff", metavar="FILE",
                    help="claim the ready batch, open one lane per unit, write "
                         "FILE naming them, and stop at exit 3 without running "
                         "any worker")
    ap.add_argument("--lanes", metavar="FILE",
                    help="run the round over the lanes a --handoff FILE "
                         "already opened, committing and verifying what the "
                         "session left in each, spawning no worker")
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
        alert = machine_wide_refusal(plan)
        if alert:
            print("")
            print("ALERT: %s" % alert)
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
        # Same alert as the dry run, on the path that actually matters: a real
        # round that dispatched nothing is the one a human never sees.
        alert = machine_wide_refusal(plan)
        if alert:
            print("ALERT: %s" % alert, file=sys.stderr)
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
    # FX-A, THE HANDOFF, and it stops here on purpose. Everything above is
    # the round's own opening (reconcile, orphan report, the exclusive
    # claim); everything below runs a worker. Inside a coding session no
    # worker can be run, so this is where the two routes part: the lanes are
    # opened, the file naming them is written, and the caller is told the
    # work is its own. Exit 3, never 0 and never 1: nothing failed and
    # nothing finished.
    if args.handoff:
        return write_handoff(args.handoff, [n for n, _c in claimed], args.cwd)

    prepared = None
    if args.lanes:
        prepared, problem = read_handoff(args.lanes, [n for n, _c in claimed])
        if prepared is None:
            print("NO-DATA: %s. Nothing was run, because a lane that cannot be "
                  "found might be holding the work" % problem, file=sys.stderr)
            return 2

    default_worker_cmd = [sys.executable, os.path.join(HERE, "model_worker.py")]
    if args.lanes:
        # The session already did the work; this commits it and nothing else.
        worker = SessionWorker()
    elif args.null_worker:
        worker = LaneWorker(parts["spawn"], [sys.executable, "-c", "pass"])
    else:
        worker = LaneWorker(parts["spawn"],
                            args.worker_cmd or default_worker_cmd)

    # D5: nothing renewed this bridge's own claims on the standalone path.
    # brother_run.run_loop guards its ONE blocking call into loop_bridge.main()
    # with claim_store.BackgroundRenewal, started right before the call and
    # stopped right after (see run_loop's own docstring in brother_run.py,
    # the file this mirrors). Run standalone, as the estate's own documented
    # command does (python3 scripts/loop_bridge.py --cwd <dir> --worker-cmd
    # <cmd>), nothing wrapped the equivalent blocking call HERE: the wait on
    # every worker in the batch, below. A unit whose worker outlived the
    # lease read abandoned under a still-live run. Same class, same fix,
    # same primitive: no second renewal mechanism gets written.
    renewal = claim_store.BackgroundRenewal(store, owner).start()
    try:
        outcome = run({"batch": [n for n, _c in claimed],
                       "refused": plan.get("refused", [])},
                      parts, worker, cwd=args.cwd, max_attempts=args.max_attempts,
                      lanes=prepared)
    finally:
        renewal_failures = renewal.stop()
    for unit_id, why in renewal_failures:
        print("NO-DATA: claim renewal failed for %s: %s"
              % (unit_id or "(store)", why), file=sys.stderr)

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
    lane_branches, branch_refused = integrable_branches(iso)
    if integrate_mod is not None and args.cwd and iso.get("isolated"):
        # H2: the branch each lane ACTUALLY got, never a name rebuilt from
        # the unit id; see integrable_branches() for why that reconstruction
        # was a defect. A refused unit is named to stderr and excluded from
        # what integration is even asked to look at.
        for uid, why in sorted(branch_refused.items()):
            print("  %s" % why, file=sys.stderr)
        _fault_barrier("after_check_before_integration")
        units_by_id = {n["id"]: n for n, _c in claimed}
        results = [by_id.get(n["id"]) or {"id": n["id"]} for n, _c in claimed
                   if n["id"] not in branch_refused]
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
    elif args.cwd and lane_branches:
        _reclaim_unmerged_lanes(
            args.cwd, lane_branches,
            "isolation was not established for this batch (%s), so nothing "
            "here was merged" % iso.get("note", ""))

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

    for node, claim in claimed:
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
        # H1: this round's own attempt number goes along, so a hung earlier
        # attempt's late completion (racing in on the same unit_id) cannot
        # satisfy the owner check and close a record this round no longer
        # owns the outcome of.
        claim_store.release(store, node["id"], owner, state=state,
                            evidence=int_verdict.get("evidence"),
                            attempt=claim.get("attempt"))
        print("  %-10s %-8s scope=%-10s integrated=%s"
              % (node["id"], state,
                 (rec.get("scope") or {}).get("verdict"), merged))

    print("isolation: %s%s" % ("per-writer worktrees" if iso.get("isolated")
                               else "NOT established", 
                               (", " + iso["note"]) if iso.get("note") else ""))
    failed = [r for r in outcome.get("dispatched", []) if r.get("verdict") != "PASS"]
    _fault_barrier("after_integration_before_receipt")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

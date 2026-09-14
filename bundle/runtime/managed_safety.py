"""managed_safety: Brother-managed execution is safe by construction, not by
an operator remembering a setup ritual.

P1, night-hardening-2026-09-07. Brother already has claims, file fences,
lifecycle hooks and an enforced fence mode; what it did not have is a
launch-time seam that puts them in front of a worker BEFORE that worker
writes anything. Reproduced before this file existed: a clean throwaway
repository ran `/brother "one file exists"` end to end, the worker mutated
the repository, and nothing in the run ever created a BrotherMode store
or a claim (docs/plan/runs/night-2026-09-07/design-P1.md section 2).

THE FACT THAT DECIDES THIS FILE'S SHAPE: a dispatched worker runs in a
throwaway lane (an isolated worktree, or the shared cwd when isolation is
unavailable), never in the canonical repository the operator typed. So the
BrotherMode store this file creates is PER LANE, materialized at the one
place both the lane path and the unit's write scope are already known:
`loop_bridge.LaneWorker.run`, immediately before the worker is spawned.
Pointing a store at the canonical repository instead would make every lane
write fall outside it, and an enforced fence with nothing inside its own
root to enforce is enforcement in name only.

THREE THINGS THIS FILE DOES, and nothing else:

  probe(cwd, runs_root)      -- what the machine can actually do right now,
                                 not what somebody hopes it can do. Seven
                                 capabilities, each PRESENT, MISSING or
                                 NO-DATA, never guessed.
  capability_floor(result)   -- the strictest of autonomy_dial's A0..A3
                                 classes the measured capabilities can
                                 honestly support. Fed straight into
                                 autonomy_dial.effective_class as the floor;
                                 this file adds nothing to that policy.
  materialize(lane_path, unit) -- claim BEFORE worker launch. Creates (or
                                 reuses) the lane's own store, claims the
                                 unit's declared write scope under a fresh
                                 session, and reads the claim back through
                                 the SAME path the fence hook itself reads
                                 (bm_fence_hook.active_claims), rather than
                                 trusting the transaction that just
                                 committed. Refuses rather than guesses.

WHAT THIS FILE DELIBERATELY DOES NOT DO. It does not unify claim_store.py's
claims.json, bm_store.py's own store and BrotherSBE's `sbe task
list` into one registry: three claim registries exist tonight and only one
(bm_store) feeds the fence, and the design record names merging them as a
change nobody should make under tonight's time box. It does not touch
receipt_door.py's frozen schema: the safety state is durable in the run
journal (a free-form `type` string, no schema to extend) and in bm_store's
own transitions table, not in a new receipt field. It does not widen
capability_probe.py's six existing machine-tooling probes, which answer a
different question (is a tool installed on this machine) from the one this
file answers (can THIS run, right now, actually enforce a fence).

CLOSED 2026-09-12 (605e0d4e7): a PreToolUse payload whose `tool_name` is
not a string, or is malformed, is now denied under enforced mode rather
than bypassing bm_fence_hook's mode check (products/brothermode/tools/
bm_fence_hook.py, `_FailOpen("bad-payload")` handled as a refusal; drilled
by scripts/fence_enforced_drill.py). This file still answers a different
question (can THIS run, right now, actually enforce a fence) than that
fix; nothing here duplicates or replaces it.

Python 3, standard library only. No network.
"""
import os
import shlex
import shutil
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claim_store  # noqa: E402
import loop_bridge  # noqa: E402
import model_worker  # noqa: E402

PRESENT, MISSING, NODATA = "PRESENT", "MISSING", "NO-DATA"

#: The exact recovery commands for a Codex home with no wired hooks, named
#: rather than composed as prose (steering 7.7: "the recovery command is
#: read from the probe's own remedy field").
CODEX_HOOKS_INSTALL = os.path.join(HERE, "codex_hooks_install.py")

#: capability_floor's own order of preference, worst-first, so the caller
#: can name the ONE capability that most limits the floor rather than
#: printing all seven every time.
_FLOOR_ORDER = ("hooks", "fence", "claims", "git", "worktree", "worker",
                "receipt_path")


def _detail(state, text, remedy=None):
    return {"state": state, "detail": text, "remedy": remedy or ""}


def _tools_dir(env=None):
    """The first candidate loop_bridge.py already resolves that actually
    holds bm_store.py. Reuses loop_bridge's own sibling-dependency search
    (the estate has already been bitten once by a private copy of this
    logic drifting from the original) rather than hardcoding one path."""
    for candidate in loop_bridge.runtime_candidates(env):
        if os.path.isdir(candidate) and os.path.isfile(
                os.path.join(candidate, "bm_store.py")):
            return candidate
    return None


def _load_fence_modules(env=None):
    """(bm_store, bm_fence_hook, problem). Never raises; problem is set and
    the first two are None on any failure to locate or import either."""
    tools_dir = _tools_dir(env)
    if tools_dir is None:
        return None, None, ("no products/brothermode/tools directory holding "
                            "bm_store.py was found (looked: %s)"
                            % ", ".join(loop_bridge.runtime_candidates(env)))
    inserted = tools_dir not in sys.path
    if inserted:
        sys.path.insert(0, tools_dir)
    try:
        import bm_store
        import bm_fence_hook
    except ImportError as exc:
        if inserted:
            sys.path.remove(tools_dir)
        for name in ("bm_store", "bm_fence_hook"):
            sys.modules.pop(name, None)
        return None, None, ("could not import bm_store/bm_fence_hook from "
                            "%s: %s" % (tools_dir, exc))
    return bm_store, bm_fence_hook, None


def _check_git(cwd):
    if shutil.which("git") is None:
        return _detail(MISSING, "git is not on PATH", "install git")
    try:
        proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                              cwd=cwd, capture_output=True, text=True,
                              timeout=10)
    except OSError as exc:
        return _detail(NODATA, "could not run git in %s: %s" % (cwd, exc))
    if proc.returncode != 0:
        return _detail(MISSING, "git rev-parse failed in %s: %s"
                       % (cwd, (proc.stderr or "").strip()),
                       "run this from inside a git repository")
    return _detail(PRESENT, "git is available and %s is a work tree" % cwd)


def _check_worktree(cwd):
    try:
        proc = subprocess.run(["git", "worktree", "list"], cwd=cwd,
                              capture_output=True, text=True, timeout=10)
    except OSError as exc:
        return _detail(NODATA, "could not run git worktree list: %s" % exc)
    if proc.returncode != 0:
        return _detail(MISSING, "git worktree list failed in %s: %s"
                       % (cwd, (proc.stderr or "").strip()),
                       "check the git installation")
    return _detail(PRESENT, "git worktree list succeeded in %s" % cwd)


def _check_worker(env):
    try:
        client = model_worker.model_client(env)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent becomes NO-DATA
        return _detail(NODATA, "model_worker.model_client() raised %s: %s"
                       % (type(exc).__name__, exc))
    cmd = (env.get("MODEL_WORKER_CMD") or "").strip()
    if cmd:
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            return _detail(NODATA, "MODEL_WORKER_CMD could not be parsed: %s"
                           % exc)
    elif client == "codex":
        argv = list(model_worker.CODEX_ARGV)
    elif client == "cursor":
        argv = list(model_worker.CURSOR_ARGV)
    else:
        argv = list(model_worker.CLAUDE_ARGV)
    if not argv:
        return _detail(NODATA, "no worker command is configured")
    found = shutil.which(argv[0])
    if not found:
        return _detail(MISSING, "%r is not on PATH (client=%s)"
                       % (argv[0], client),
                       "install the %s CLI, or set MODEL_WORKER_CMD" % client)
    return _detail(PRESENT, "%s resolves to %s (client=%s)"
                   % (argv[0], found, client))


def _writable_without_creating(path):
    """Whether `path` is writable, or WOULD become writable once created,
    without actually creating it: night-hardening-2026-09-07 regression
    (test_brother_run.py's DecomposerAlwaysInvalid) found probe() itself
    materializing docs/plan/runs under a throwaway --runs-root just by
    being asked whether a receipt COULD be written there, which broke
    door.py's own contract that a refused outcome leaves no run directory
    behind. A probe answers a question; it does not act on it. Walks up to
    the nearest existing ancestor and checks that one instead."""
    p = os.path.abspath(path)
    while True:
        if os.path.exists(p):
            return os.access(p, os.W_OK)
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent


def _check_claims(runs_root):
    probe_dir = os.path.join(runs_root, ".managed_safety_probe")
    if not _writable_without_creating(probe_dir):
        return _detail(MISSING,
                       "%s is not writable and could not be created"
                       % probe_dir,
                       "check write permission on %s" % runs_root)
    return _detail(PRESENT,
                   "claim_store is importable (%s) and %s would be writable"
                   % (claim_store.__file__, probe_dir))


def _check_receipt_path(runs_root):
    receipts_dir = os.path.join(runs_root, "docs", "plan", "runs")
    if not _writable_without_creating(receipts_dir):
        return _detail(MISSING,
                       "%s is not writable and could not be created"
                       % receipts_dir,
                       "check write permission on %s" % runs_root)
    return _detail(PRESENT, "%s would be writable" % receipts_dir)


def _check_fence(env):
    bs, fh, problem = _load_fence_modules(env)
    if bs is None or fh is None:
        return _detail(NODATA, problem or "bm_store/bm_fence_hook absent")
    try:
        on = fh.enforced_mode({"BM_FENCE_MODE": "enforced"})
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent becomes NO-DATA
        return _detail(NODATA, "bm_fence_hook.enforced_mode raised %s: %s"
                       % (type(exc).__name__, exc))
    if on is not True:
        return _detail(NODATA,
                       "bm_fence_hook.enforced_mode({'BM_FENCE_MODE':"
                       "'enforced'}) answered %r, not True, so this copy of "
                       "the hook cannot be trusted to enforce" % (on,))
    return _detail(PRESENT, "bm_store and bm_fence_hook are importable from "
                   "%s and enforced_mode() answers correctly" % bs.__file__)


def _claude_config_root(env):
    override = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if override:
        return override
    home = (env.get("HOME") or "").strip() or os.path.expanduser("~")
    return os.path.join(home, ".claude")


def _claude_hook_sources(env):
    """Every place the Claude client's own PreToolUse hook for
    bm_fence_hook.py could be registered, in the order checked. A user
    settings.json entry is one route (docs/HOOKS.md's own documented
    manual install); the far more common route on this estate is a
    brothermode PLUGIN's own hooks/hooks.json under the plugin cache
    (measured on this machine: multiple versions under
    <config_root>/plugins/cache/*/*/*/hooks/hooks.json each register it).
    Checking settings.json alone reports every plugin-installed session as
    MISSING, which is false: the fix (2026-09-08) adds the plugin-cache
    glob rather than narrowing the claim to "settings.json only"."""
    config_root = _claude_config_root(env)
    out = [os.path.join(config_root, "settings.json")]
    import glob
    out.extend(sorted(glob.glob(os.path.join(
        config_root, "plugins", "cache", "*", "*", "*", "hooks",
        "hooks.json"))))
    return out


def _check_hooks(env):
    try:
        client = model_worker.model_client(env)
    except Exception:  # noqa: BLE001  # sbe: allow-silent, default to claude below
        client = "claude"
    if client == "codex":
        codex_home = (env.get("CODEX_HOME") or "").strip() \
            or os.path.expanduser("~/.codex")
        try:
            proc = subprocess.run(
                [sys.executable, CODEX_HOOKS_INSTALL, "--check",
                 "--codex-home", codex_home],
                capture_output=True, text=True, timeout=30)
        except OSError as exc:
            return _detail(NODATA, "could not run codex_hooks_install.py "
                           "--check: %s" % exc)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode == 0:
            return _detail(PRESENT, out or "Codex hooks check passed "
                           "(--codex-home %s)" % codex_home)
        remedy = ("python3 %s --check --codex-home %s\n"
                  "python3 %s --trust --allow-default-home"
                  % (CODEX_HOOKS_INSTALL, codex_home, CODEX_HOOKS_INSTALL))
        state = NODATA if "NO-DATA" in out else MISSING
        return _detail(state, out or ("codex_hooks_install.py --check exited "
                                      "%d against %s" % (proc.returncode,
                                                         codex_home)),
                       remedy)
    checked = _claude_hook_sources(env)
    for candidate in checked:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as fh_:
                text = fh_.read()
        except OSError:  # sbe: allow-silent an unreadable optional hook config cannot prove registration, so the next configured source is checked
            continue
        if "bm_fence_hook.py" in text:
            return _detail(PRESENT, "%s registers bm_fence_hook.py" % candidate)
    return _detail(MISSING, "none of %d checked location(s) register "
                   "bm_fence_hook.py as a PreToolUse hook (looked: %s)"
                   % (len(checked), ", ".join(checked)),
                   "see products/brothermode/docs/HOOKS.md for the "
                   "PreToolUse entry to add, or install the brothermode "
                   "plugin")


def probe(cwd, runs_root, env=None):
    """Every capability a Brother-managed run's preflight needs, measured
    rather than assumed. `cwd` is the repository being worked on;
    `runs_root` is where this run's own bookkeeping lives (brother_run.py's
    own --runs-root). Returns a dict of seven entries, each
    {"state": PRESENT|MISSING|NO-DATA, "detail": str, "remedy": str}."""
    env = os.environ if env is None else env
    return {
        "claims": _check_claims(runs_root),
        "git": _check_git(cwd),
        "worktree": _check_worktree(cwd),
        "worker": _check_worker(env),
        "receipt_path": _check_receipt_path(runs_root),
        "fence": _check_fence(env),
        "hooks": _check_hooks(env),
    }


def worst_capability(probe_result):
    """(name, entry) for the capability that most limits the floor, in the
    same worst-first order capability_floor reads, or (None, None) when
    every capability is PRESENT."""
    for name in _FLOOR_ORDER:
        entry = probe_result.get(name)
        if entry and entry.get("state") != PRESENT:
            return name, entry
    return None, None


def capability_floor(probe_result):
    """The strictest autonomy_dial class the measured capabilities can
    honestly support. Fed straight into autonomy_dial.effective_class as
    the floor: that function's own "stricter always wins" rule does the
    rest, and nothing here adds to the policy it already states.

    "A0" when every capability reads PRESENT. "A2" when the fence itself
    cannot be trusted to enforce (bm_store/bm_fence_hook missing, or
    misbehaving), or when any of the five supporting capabilities is not
    PRESENT: something is not right and the run should ask rather than
    barrel ahead. "A3" when hooks are MISSING or NO-DATA: steering 7.8 is
    explicit that missing required enforcement for higher-autonomy work is
    a refusal, not a downgrade, because a hook that was never wired can
    never fire, however carefully a claim is taken."""
    if probe_result.get("hooks", {}).get("state") != PRESENT:
        return "A3"
    if probe_result.get("fence", {}).get("state") != PRESENT:
        return "A2"
    for name in ("claims", "git", "worktree", "worker", "receipt_path"):
        if probe_result.get(name, {}).get("state") != PRESENT:
            return "A2"
    return "A0"


def materialize(lane_path, unit):
    """Claim BEFORE worker launch. Creates (or reuses) the store at
    `lane_path`, claims `unit`'s declared write scope under a fresh session,
    and reads the claim back through bm_fence_hook.active_claims (the exact
    path the fence hook itself reads at write time) before trusting it.

    Returns (session_id, why): on success, session_id is a fresh RAW harness
    session id (never a public label) suitable for BM_FENCE_SESSION_ID, and
    why is "". On any refusal, session_id is None and why names the reason;
    nothing was left half-claimed."""
    bs, fh, problem = _load_fence_modules()
    if bs is None or fh is None:
        return None, "the fence modules could not be loaded: %s" % (problem or "")
    unit_id = str(unit.get("unit_id") or "").strip()
    if not unit_id:
        return None, "the unit carries no unit_id, so nothing could be claimed"
    write_scope = list(unit.get("write_scope") or [])
    if not write_scope:
        return None, ("%s declares no write scope, so nothing was claimed "
                      "and the worker must not start" % unit_id)
    objective = str(unit.get("objective") or unit_id)
    # realpath ONCE, used everywhere below: on macOS a freshly minted
    # tempfile.mkdtemp() lane (and a git-worktree path built from one) sits
    # under /var, which is itself a symlink to /private/var. Store.claim's
    # OWN symlink-escape guard, GATE B, refuses a fence directory that
    # resolves outside the root it was given, and a caller that hands this
    # function the un-resolved form (measured: worktree_lane.py's lanes are
    # exactly this) trips that guard on every call, never mid-run. One
    # resolved path, held for init_project, session_label, Store and
    # active_claims alike, is what test_bm_fence_hook.py's own
    # FenceHookBase.setUp already does for the identical reason.
    lane_path = os.path.realpath(lane_path)
    token = "brother-managed-" + uuid.uuid4().hex
    try:
        bs.init_project(lane_path).close()
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent, refused below
        return None, ("the store at %s could not be initialized: %s: %s"
                      % (lane_path, type(exc).__name__, exc))
    try:
        label = fh.session_label(lane_path, token)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent, refused below
        return None, ("this claim's session label could not be derived: %s: "
                      "%s" % (type(exc).__name__, exc))
    try:
        store = bs.Store(lane_path, create=False)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent, refused below
        return None, ("the store at %s could not be opened: %s: %s"
                      % (lane_path, type(exc).__name__, exc))
    try:
        store.claim(unit_id, "ephemeral", objective=objective,
                   files=write_scope, session_id=label)
    except bs.OwnershipRefused as exc:
        return None, "the claim for %s was refused: %s" % (unit_id, exc)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent, refused below
        return None, ("the claim for %s raised %s: %s"
                      % (unit_id, type(exc).__name__, exc))
    finally:
        store.close()
    # Read it back through the SAME path the fence hook itself reads at
    # write time, rather than trusting the transaction that just committed:
    # a claim this run cannot see through active_claims is a claim the
    # fence cannot enforce either, whatever the transaction returned.
    try:
        rows = fh.active_claims(lane_path)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent, refused below
        return None, ("the claim for %s could not be read back: %s: %s"
                      % (unit_id, type(exc).__name__, exc))
    held = {r["path"] for r in rows if r["name"] == unit_id}
    missing = [p for p in write_scope
              if fh.canonical_target(lane_path, p) not in held]
    if missing:
        return None, ("the claim for %s does not hold every declared path "
                      "after being read back (missing %s)"
                      % (unit_id, ", ".join(missing)))
    return token, ""

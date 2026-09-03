#!/usr/bin/env python3
"""scripts/bench_e2e_lifecycle.py: the installed-plugin LIFECYCLE canary.

WHAT THIS IS
  FENCE V10 (BrotherModeUp-handovers/v3/WAVE-CD-BRIEFS.md), agent
  e2e-canary: the v3 gap-closure plan's Agent 7 mission ("End-to-End and
  Release Canary Engineer"), adapted to what exists tonight rather than
  built from zero. It extends scripts/benchmark_comparative.py's
  probe_installed canary (the install-the-shipped-way asserts, the
  BM_BENCH_AUTH_CONFIG persistent-auth law, the honest Skip discipline)
  from "one hook fired" into a full installed-plugin LIFECYCLE: a
  throwaway config first proves the migration story ruling B4 requires
  (V3-FREEZE-2026-08-07.md: a v2.1.1 install, uninstalled FIRST, before
  the v3 install, so the two plugins' hooks are never wired at the same
  time), then drives the deterministic public runtime boundary itself
  (tools/brothermode_cli.py: start, status, next, view, a REFUSED
  deliver, doctor), then uninstalls and proves the config carries no
  plugin remnant.

WHAT IT IS NOT
  Not a second probe_installed: this file drives NO live `claude -p`
  session. WAVE-CD-BRIEFS.md's FENCE V10 brief says so directly: "Live
  claude -p turns are NOT required beyond what probe_installed already
  proves; the lifecycle canary drives the CLI boundary and the plugin
  CLI, staying deterministic." Ruling H6 (V3-FREEZE-2026-08-07.md)
  already accepted the renamed probe_installed run as Wave A's live-hook
  proof; this file's job is the CLI boundary and the plugin CLI, nothing
  the hook itself already proved. Not a rewrite of
  tools/test_brothermode_cli.py's contract tests either: those pin
  behavior at the function-call level; this file proves the same
  boundary survives a REAL install/uninstall cycle and a REAL subprocess
  invocation, from a throwaway HOME nobody has touched before.

WHY THIS CANARY BUILDS ITS OWN THROWAWAY CLAUDE_CONFIG_DIR RATHER THAN
REUSING BM_BENCH_AUTH_CONFIG (a deliberate deviation, stated plainly)
  probe_installed's persistent-auth folder exists for exactly one reason:
  a live `claude -p` session needs a real login, and a throwaway HOME
  cannot carry one (measured 2026-08-07, that function's own docstring).
  This canary drives no `claude -p` session at all, so no login is ever
  needed: `claude plugin marketplace add/install/uninstall/list/details`
  are local mechanics with no auth requirement at all (scripts/release-
  smoke-install.sh proves exactly this, daily, entirely inside a
  throwaway CLAUDE_CONFIG_DIR, no BM_BENCH_AUTH_CONFIG in sight, "No
  sign-in is needed: marketplace add and plugin install are local
  mechanics."). Reusing the persistent folder here would also be
  actively unsafe rather than merely unnecessary: this canary's own last
  act is UNINSTALLING the plugin and REMOVING its marketplace to prove
  the config comes back clean, and scripts/benchmark_comparative.py's
  arm B per-cell environment (protocol v2, _arm_b_environment) depends
  on that SAME persistent folder keeping the v3 plugin installed between
  cells for speed. Running this canary against that folder would tear
  down another canary's fixture out from under it mid-benchmark. Every
  directory this file builds is therefore its own fresh mkdtemp, deleted
  on exit, every run, matching the (simpler, unauthenticated) arm A
  posture rather than probe_installed's (authenticated) one.

THE EIGHTEEN STEPS, in order (STEP_NAMES below is the pinned contract;
tools/test_bm_e2e_pins.py asserts its exact content without running the
canary for real)
  1. confirm a claude binary exists at all (a missing one is SKIP)
  2. build a throwaway HOME, CLAUDE_CONFIG_DIR and fixture directory
  3. clone the v2.1.1 tag into its own throwaway directory (the
     upgrade-leg baseline: a real historical release, not a synthetic
     fixture)
  4. install v2.1.1's plugin (brotherme@brotherme-marketplace) into the
     throwaway CLAUDE_CONFIG_DIR
  5. uninstall v2 FIRST (ruling B4's documented migration instruction:
     uninstall the v2 plugin and remove its marketplace BEFORE
     installing v3, so the two plugins' hooks are never both wired)
  6. install v3 (this tree) into the SAME CLAUDE_CONFIG_DIR the shipped
     way, reusing scripts/benchmark_comparative.py's own
     _install_plugin_shipped_way so the two canaries can never install
     this tree two different ways
  7. verify the installed version and namespace: `claude plugin details
     brothermode` must name every one of the nine canonical public
     skills (V3-FREEZE-2026-08-07.md ruling B5; docs/brand/
     IDENTITY-CONTRACT.md section 8, "the nine v3 canonical skills")
  8. prove a single hook chain: `claude plugin list --json` names
     exactly ONE installed plugin (brothermode@brothermode-marketplace)
     after the migration, proving the uninstall-v2-first order actually
     prevents the double-hook-chain risk ruling B4 named, not merely
     asserts it in prose
  9. grant consent the shipped way (scripts/setup.py flag mode), exactly
     as probe_installed and scripts/rehearse_fresh_install.py already do
  10. initialize a throwaway project store in its own fixture directory
      (tools/bm_store.py init, fixture BUILD machinery, the same
      internal-adapter role probe_installed's own fixture builders play;
      not part of the measured boundary)
  11. drive tools/brothermode_cli.py start (creates the project row a
      task needs to reference before one can be seeded)
  12. seed one ready-state task (tools/bm_project.py task add, fixture
      build machinery, the same reason step 10 uses an internal tool)
  13. drive tools/brothermode_cli.py status
  14. drive tools/brothermode_cli.py next
  15. drive tools/brothermode_cli.py view
  16. drive tools/brothermode_cli.py deliver and prove it REFUSES: the
      seeded task never reached the terminal state 'closed', which is
      what incomplete evidence looks like at the deliver boundary
      (review is what would have supplied evidence and moved it there,
      and review is deliberately never called on this fixture)
  17. drive tools/brothermode_cli.py doctor (its own ten checks, read
      and printed verbatim; this canary does NOT require every doctor
      check to PASS here, since a throwaway plugin config is not a
      founder's live install and several checks are honestly expected to
      differ, for example the vault directory this canary never
      populates with a template; the thing being proven is that the CLI
      boundary reaches doctor and doctor returns a real, parseable
      verdict, not that the throwaway fixture is a healthy installation)
  18. uninstall v3 and remove its marketplace, then prove the config
      carries no remnant: `claude plugin list --json` is empty and
      settings.json's enabledPlugins and extraKnownMarketplaces are both
      empty

Exits 0 printing every step's verdict on a full pass. Exits 1 printing
"SKIP: <reason>" when this machine cannot check something here (a missing
claude binary, a failed install, a clone or mechanical subprocess
problem), the same Skip discipline probe_installed uses: a SKIP is never a
pass and never a silent success. A harness defect (an unexpected
exception) is also reported as SKIP, never as a crash with no explanation.

H-4 (v3 gap-closure Claude-family review): SKIP and FAIL are two different
words for two different facts, and this canary keeps them apart. Exits 2
printing "FAIL: <reason>" when the product itself did the wrong thing and
this canary CAUGHT it: deliver accepting incomplete evidence instead of
refusing, the expected refusal wording missing, more than one plugin
registered after the uninstall-v2-first migration, or a plugin remnant
left behind after uninstall. Those are the safety properties this canary
exists to prove; a FAIL there is a real regression, never conflated with
"this machine has no claude binary" under one shared exit code.

USAGE
  python3 scripts/bench_e2e_lifecycle.py

Python 3.9, standard library only. Drives real `claude`, `git` and
`python3` subprocesses, which is why this lives under scripts/ and not
tools/, matching scripts/benchmark_comparative.py's own placement rule.

No em or en dashes anywhere in this file or its output.
"""

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load_benchmark_comparative():
    """scripts/benchmark_comparative.py, imported by PATH (the same
    technique tools/bm_project.py uses to load tools/bm_store.py): this
    file reuses that module's _install_plugin_shipped_way, _probe_env,
    _init_brothermode_store, _init_repo, _write, _run, _ascii, Skip and
    its PROBE_MARKETPLACE_NAME / PROBE_PLUGIN_SPEC constants, rather than
    keeping a second copy of the v3 install sequence that could quietly
    drift from the one probe_installed already proved live. Strictly
    read-only: nothing here calls anything that writes into
    scripts/benchmark_comparative.py itself, and this file is not in
    that module's own writer lane (WAVE-CD-BRIEFS.md's FENCE V10 owns
    only this file and tools/test_bm_e2e_pins.py)."""
    spec = importlib.util.spec_from_file_location(
        "bm_e2e_lifecycle_bc", os.path.join(HERE, "benchmark_comparative.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BC = _load_benchmark_comparative()
Skip = BC.Skip
_run = BC._run
_ascii = BC._ascii


class Fail(Exception):
    """A genuine product defect this canary CAUGHT, distinct from Skip
    ("this machine cannot check that here"). H-4 (v3 gap-closure
    Claude-family review): SKIP was doing two incompatible jobs (a missing
    claude binary and an accepted delivery on incomplete evidence read the
    same, exit 1). main() turns a Fail into exit 2 and a Skip into exit 1;
    the two are never conflated."""

#: Wall clock caps, one per subprocess family. All local mechanics (no
#: network beyond `git clone` of THIS repository's own tag, which is also
#: local), so these stay far below scripts/benchmark_comparative.py's own
#: PROBE_TIMEOUT_SECONDS (300s, sized for a live model turn this file never
#: runs).
CLAUDE_TIMEOUT_SECONDS = 120
TOOL_TIMEOUT_SECONDS = 60
CLONE_TIMEOUT_SECONDS = 120

#: The historical release ruling B4's upgrade leg installs first. A real
#: tag, not a synthetic fixture: the point is proving the migration story
#: against an actual v2 release's actual manifest.
V2_TAG = "v2.1.1"
V2_PLUGIN_NAME = "brotherme"
V2_MARKETPLACE_NAME = "brotherme-marketplace"
V2_PLUGIN_SPEC = "%s@%s" % (V2_PLUGIN_NAME, V2_MARKETPLACE_NAME)
V2_EXPECTED_VERSION = "2.1.1"

#: The nine public skills a v3 install must advertise (V3-FREEZE-2026-08-07.md
#: ruling B5; docs/brand/IDENTITY-CONTRACT.md section 8, "the nine v3
#: canonical skills"). The full-auto trio, the founder-mode quartet, and the
#: brotherme guided-beginner skill all still exist on disk (ruling B5: hidden,
#: not retired) and so still appear in `claude plugin details`'s Skills(...)
#: line alongside these nine; this tuple is deliberately the NINE, not the
#: whole roster, because what step 7 proves is the PUBLIC surface a
#: stranger's README promises, not the full internal inventory.
NINE_CANONICAL_SKILLS = ("start", "status", "next", "review", "deliver",
                         "view", "help", "doctor", "update")

PROJECT_ID = "bm-e2e-lifecycle-canary"
PROJECT_NAME = "E2E lifecycle canary fixture"
PROJECT_GOAL = "prove the deterministic CLI boundary end to end"
TASK_TITLE = "prove the deterministic boundary drives a real task"
ACTOR_NAME = "e2e-lifecycle-canary"

#: The exact substring of tools/bm_project.py's own cmd_deliver refusal for
#: a task short of the terminal state ('closed'). Copied as a substring
#: from that file's own text, never reconstructed, so a wording change
#: there breaks tools/test_bm_e2e_pins.py before it can break silently
#: here (that pin test greps bm_project.py's own source for this string).
DELIVER_REFUSAL_MARKER = "have not reached the terminal state"

#: The pinned, ordered step list. _step() below reads STEP_NAMES[len(verdicts)]
#: rather than taking a name argument, so the printed verdict list can never
#: drift out of sync with this tuple: tools/test_bm_e2e_pins.py imports this
#: constant directly and pins its exact content and order.
STEP_NAMES = (
    "claude binary present",
    "throwaway directories built",
    "v2.1.1 pinned clone built (upgrade-leg baseline)",
    "v2.1.1 installed into the throwaway config",
    "v2 uninstalled first (documented migration instruction: uninstall "
    "v2, then install v3)",
    "v3 installed the shipped way",
    "installed version and namespace verified (nine canonical skills)",
    "single hook chain proven (one plugin registered after migration)",
    "consent granted the shipped way",
    "fixture project store initialized",
    "brothermode_cli start",
    "ready-state task seeded",
    "brothermode_cli status",
    "brothermode_cli next",
    "brothermode_cli view",
    "brothermode_cli deliver refused (incomplete evidence blocks delivery)",
    "brothermode_cli doctor",
    "brothermode_cli uninstalled, config carries no plugin remnant",
)
TOTAL_STEPS = len(STEP_NAMES)


def _step(verdicts, detail=""):
    """Record and print the NEXT step in STEP_NAMES order. verdicts is the
    list this call appends to; its length before the append is the index
    of the step just completed. A caller can never print a step out of
    order or under the wrong name: the name comes from the pinned tuple,
    positionally, not from a string the caller retypes each time."""
    i = len(verdicts)
    name = STEP_NAMES[i]
    verdicts.append((name, detail))
    print("[%d/%d] %s%s" % (i + 1, TOTAL_STEPS, name,
                            (": %s" % detail) if detail else ""))


def _bm_project_bin():
    """tools/bm_project.py, the checkout's OWN copy: deliberately unchanged
    by codex finding 6's fix. Steps 12 (task seeding) and the store-init
    fixture machinery this file's own module docstring already names as
    "fixture build machinery... not part of the measured boundary" (see
    THE EIGHTEEN STEPS above, steps 10/12); the measured CLI boundary is
    tools/brothermode_cli.py alone (steps 11, 13 to 17), which
    _installed_cli_bin below resolves against the INSTALLED plugin."""
    return os.path.join(ROOT, "tools", "bm_project.py")


def _installed_plugin_root(claude_config_dir, plugin_spec):
    """The installed plugin's own root directory, read from Claude Code's
    OWN bookkeeping file (plugins/installed_plugins.json's installPath),
    never guessed from the plugins/cache/<marketplace>/<name>/<version>/
    directory naming convention. Measured 2026-08-08 against a real
    throwaway `claude plugin install`: that file's shape is
    {"plugins": {"<spec>": [{"scope", "installPath", "version", ...}]}}.
    Returns None if the file is missing, unparseable, or names no entry
    for this plugin spec whose installPath actually exists on disk; the
    caller turns that into an honest SKIP naming the fact, never a silent
    fall-through to the source tree (codex finding 6, v3 gap-closure
    cross-family review)."""
    reg_path = os.path.join(claude_config_dir, "plugins",
                            "installed_plugins.json")
    try:
        with open(reg_path) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return None
    entries = (reg.get("plugins") or {}).get(plugin_spec) or []
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        install_path = entry.get("installPath")
        if install_path and os.path.isdir(install_path):
            return install_path
    return None


def _installed_cli_bin(claude_config_dir):
    """tools/brothermode_cli.py under the INSTALLED plugin's own root
    (_installed_plugin_root), not the checkout this canary's own source
    lives in. Codex finding 6 (v3 gap-closure cross-family review):
    `_cli_run` used to always resolve to the checkout's copy, so every
    CLI-boundary step (start, status, next, view, deliver, doctor)
    exercised source-tree adapters even after "installing" the plugin,
    and a missing, corrupt or stale installed CLI could still receive the
    advertised lifecycle PASS. Returns None when the installed plugin
    exposes no such file (no root resolved, or the file is absent under
    that root); the caller raises Skip naming that fact rather than
    silently driving the checkout instead."""
    root = _installed_plugin_root(claude_config_dir, BC.PROBE_PLUGIN_SPEC)
    if root is None:
        return None
    candidate = os.path.join(root, "tools", "brothermode_cli.py")
    return candidate if os.path.isfile(candidate) else None


def _clone_v2_tag(env, dirs_made):
    """A throwaway --local clone of THIS repository checked out at V2_TAG
    (git honours --local for a same-filesystem source path; --no-hardlinks
    so the clone can be deleted independently of ROOT's own object store).
    A real historical release, not a synthetic fixture: VERSION inside the
    clone is checked against V2_EXPECTED_VERSION so a moved or re-pointed
    tag is caught here rather than silently installing the wrong plugin
    for the rest of the upgrade leg."""
    clone_dir = os.path.realpath(tempfile.mkdtemp(prefix="bm-e2e-v2clone-"))
    dirs_made.append(clone_dir)
    r = _run(["git", "clone", "--local", "--no-hardlinks", "--branch", V2_TAG,
             ROOT, clone_dir], cwd=ROOT, env=env, timeout=CLONE_TIMEOUT_SECONDS)
    if r.returncode != 0:
        raise Skip("could not clone the %s tag for the upgrade-leg "
                   "baseline: exit %s, output: %s"
                   % (V2_TAG, r.returncode,
                      _ascii((r.stdout or "") + (r.stderr or ""), 300)))
    version_path = os.path.join(clone_dir, "VERSION")
    if not os.path.isfile(version_path):
        raise Skip("the %s clone at %s has no VERSION file; the clone did "
                   "not check out cleanly" % (V2_TAG, clone_dir))
    with open(version_path) as fh:
        version = fh.read().strip()
    if version != V2_EXPECTED_VERSION:
        raise Skip("the %s clone's own VERSION file says %r, not %r; the "
                   "tag may have moved" % (V2_TAG, version, V2_EXPECTED_VERSION))
    return clone_dir


def _install_v2_plugin(claude_bin, env, clone_dir):
    r_add = _run([claude_bin, "plugin", "marketplace", "add", clone_dir],
                cwd=ROOT, env=env, timeout=CLAUDE_TIMEOUT_SECONDS)
    add_out = (r_add.stdout or "") + (r_add.stderr or "")
    if r_add.returncode != 0 or "Successfully added marketplace" not in add_out:
        raise Skip("the v2.1.1 marketplace add did not succeed: exit %s, "
                   "output: %s" % (r_add.returncode, _ascii(add_out, 300)))
    r_install = _run([claude_bin, "plugin", "install", V2_PLUGIN_SPEC],
                    cwd=ROOT, env=env, timeout=CLAUDE_TIMEOUT_SECONDS)
    install_out = (r_install.stdout or "") + (r_install.stderr or "")
    if (r_install.returncode != 0
            or "Successfully installed plugin" not in install_out):
        raise Skip("the v2.1.1 plugin install did not succeed: exit %s, "
                   "output: %s" % (r_install.returncode,
                                   _ascii(install_out, 300)))
    r_list = _run([claude_bin, "plugin", "list", "--json"], cwd=ROOT, env=env,
                  timeout=CLAUDE_TIMEOUT_SECONDS)
    try:
        entries = json.loads(r_list.stdout or "[]")
    except ValueError:
        entries = None
    if not isinstance(entries, list) or not any(
            isinstance(e, dict) and e.get("id") == V2_PLUGIN_SPEC
            and e.get("version") == V2_EXPECTED_VERSION for e in entries):
        raise Skip("v2.1.1 install did not register %s at version %s in "
                   "`claude plugin list --json`: %s"
                   % (V2_PLUGIN_SPEC, V2_EXPECTED_VERSION,
                      _ascii(r_list.stdout or "", 300)))


def _uninstall_v2_plugin(claude_bin, env):
    """The documented migration instruction this canary encodes: uninstall
    the v2 plugin (and remove its marketplace) BEFORE installing v3, so
    the two plugins' hooks are never both wired at once. This is the
    order the founder-facing morning report names per ruling B4; this
    function is that instruction, executable rather than only written
    down."""
    r_un = _run([claude_bin, "plugin", "uninstall", V2_PLUGIN_NAME],
               cwd=ROOT, env=env, timeout=CLAUDE_TIMEOUT_SECONDS)
    un_out = (r_un.stdout or "") + (r_un.stderr or "")
    if r_un.returncode != 0:
        raise Skip("uninstalling the v2.1.1 plugin did not succeed: exit "
                   "%s, output: %s" % (r_un.returncode, _ascii(un_out, 300)))
    r_rm = _run([claude_bin, "plugin", "marketplace", "remove",
                V2_MARKETPLACE_NAME], cwd=ROOT, env=env,
                timeout=CLAUDE_TIMEOUT_SECONDS)
    rm_out = (r_rm.stdout or "") + (r_rm.stderr or "")
    if r_rm.returncode != 0:
        raise Skip("removing the v2.1.1 marketplace did not succeed: exit "
                   "%s, output: %s" % (r_rm.returncode, _ascii(rm_out, 300)))


def _verify_v3_identity(claude_bin, env):
    """Step 7: `claude plugin details brothermode` names this tree's own
    VERSION and lists every one of the nine canonical public skills in
    its Skills(...) line. Exact-token matching (split on ", ") so a
    legacy shim like "brotherme-start" can never be mistaken for the
    canonical "start" by substring accident."""
    with open(os.path.join(ROOT, "VERSION")) as fh:
        version = fh.read().strip()
    r_details = _run([claude_bin, "plugin", "details", "brothermode"],
                    cwd=ROOT, env=env, timeout=CLAUDE_TIMEOUT_SECONDS)
    details_out = (r_details.stdout or "") + (r_details.stderr or "")
    if ("brothermode %s" % version) not in details_out:
        raise Skip("claude plugin details brothermode does not name this "
                   "tree's own version (%s): %s"
                   % (version, _ascii(details_out, 300)))
    m = re.search(r"Skills \(\d+\)\s+(.*)", details_out)
    if not m:
        raise Skip("claude plugin details brothermode printed no "
                   "Skills(...) line: %s" % _ascii(details_out, 300))
    names = set(n.strip() for n in m.group(1).split(",") if n.strip())
    missing = [n for n in NINE_CANONICAL_SKILLS if n not in names]
    if missing:
        raise Skip("claude plugin details brothermode's Skills(...) line "
                   "is missing canonical public skill(s) %s: %s"
                   % (missing, _ascii(details_out, 300)))
    # Verdict-line honesty (v3 gap-closure G2): the found count printed at
    # the call site must come from what was actually parsed out of
    # details_out, not from NINE_CANONICAL_SKILLS compared against itself
    # (which would always read N/N regardless of what this function
    # measured). By this point every canonical skill IS present (the
    # `missing` check above already raised otherwise), so found always
    # equals the total here; the point is that the number printed is a
    # real count of `names`, not two copies of the same constant.
    found = len(NINE_CANONICAL_SKILLS) - len(missing)
    return version, found, len(NINE_CANONICAL_SKILLS)


def _verify_single_plugin(claude_bin, env):
    """Step 8: after the uninstall-v2-first migration, `claude plugin list
    --json` must show EXACTLY one plugin, this tree's own
    brothermode@brothermode-marketplace. This is the measured proof
    behind ruling B4's prose claim, not a restatement of it.

    H-4: "two plugins registered after migration" is one of the two named
    safety properties this canary exists to prove, so a REAL measurement
    that disagrees (the command ran, JSON parsed, and it names more than
    one plugin or the wrong one) is a Fail. A command that could not be
    checked here at all (a subprocess or JSON-parse problem, entries is
    None) stays Skip: this canary genuinely could not tell, which is a
    different fact from telling and not liking the answer."""
    r_list = _run([claude_bin, "plugin", "list", "--json"], cwd=ROOT, env=env,
                  timeout=CLAUDE_TIMEOUT_SECONDS)
    try:
        entries = json.loads(r_list.stdout or "null")
    except ValueError:
        entries = None
    if entries is None or not isinstance(entries, list):
        raise Skip("claude plugin list --json produced no parseable list "
                   "after the uninstall-v2-first migration step: %s"
                   % _ascii(r_list.stdout or "", 300))
    if len(entries) != 1 or entries[0].get("id") != BC.PROBE_PLUGIN_SPEC:
        raise Fail("claude plugin list --json does not show exactly one "
                   "plugin (%s) registered after the uninstall-v2-first "
                   "migration step: %s"
                   % (BC.PROBE_PLUGIN_SPEC, _ascii(r_list.stdout or "", 300)))


def _grant_consent(env, home_dir):
    """scripts/setup.py flag mode against BROTHERME_CONFIG inside the
    throwaway HOME, exactly as probe_installed and
    scripts/rehearse_fresh_install.py's own step 4 already do: without
    this, doctor's "setup has been completed" check would rightly FAIL
    for a reason this canary caused, not one it is trying to measure."""
    vault_dir = os.path.join(home_dir, "BrotherModeVault")
    r_setup = _run([sys.executable, os.path.join(ROOT, "scripts", "setup.py"),
                   "--vault", vault_dir, "--mode", "plugin", "--accept-notice"],
                   cwd=ROOT, env=env, timeout=TOOL_TIMEOUT_SECONDS)
    broth_config = env.get("BROTHERME_CONFIG", "")
    if r_setup.returncode != 0 or not (broth_config
                                       and os.path.isfile(broth_config)):
        raise Skip("scripts/setup.py flag-mode consent did not complete: "
                   "exit %s, output: %s"
                   % (r_setup.returncode,
                      _ascii((r_setup.stdout or "") + (r_setup.stderr or ""),
                            300)))


def _seed_ready_task(env, fixture_dir):
    """Fixture build machinery (tools/bm_project.py directly, the internal
    adapter), not part of the measured CLI boundary: one task, seeded
    straight into the 'ready' state so step 14 (next) has a real
    candidate and step 16 (deliver) has a real, non-terminal task to
    refuse on. tools/brothermode_cli.py exposes no 'task add' verb of its
    own (it is not one of the ten boundary commands freeze answer 4
    names), so this step necessarily uses the internal tool, exactly the
    role probe_installed's own _rival_claim plays for ITS fixture."""
    r = _run([sys.executable, _bm_project_bin(), "task", "add",
             "--project-id", PROJECT_ID, "--title", TASK_TITLE,
             "--status", "ready", "--actor-type", "model",
             "--actor-name", ACTOR_NAME],
             cwd=fixture_dir, env=env, timeout=TOOL_TIMEOUT_SECONDS)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "added task" not in out:
        raise Skip("seeding the fixture's ready-state task did not "
                   "succeed: exit %s, output: %s"
                   % (r.returncode, _ascii(out, 300)))


def _cli_run(env, fixture_dir, cli_bin, *args):
    """Drives the INSTALLED plugin's own tools/brothermode_cli.py
    (cli_bin, resolved once by _installed_cli_bin and threaded through
    every step below), never the checkout's copy (codex finding 6)."""
    argv = [sys.executable, cli_bin] + list(args)
    return _run(argv, cwd=fixture_dir, env=env, timeout=TOOL_TIMEOUT_SECONDS)


def _drive_start(env, fixture_dir, cli_bin):
    r = _cli_run(env, fixture_dir, cli_bin, "start", "--project-id", PROJECT_ID,
                "--name", PROJECT_NAME, "--goal", PROJECT_GOAL,
                "--actor-type", "model", "--actor-name", ACTOR_NAME)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or ("started project %s" % PROJECT_ID) not in out:
        raise Skip("brothermode_cli start did not succeed: exit %s, "
                   "output: %s" % (r.returncode, _ascii(out, 300)))
    return out.strip()


def _drive_status(env, fixture_dir, cli_bin):
    r = _cli_run(env, fixture_dir, cli_bin, "status", "--project-id", PROJECT_ID)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "Goal:" not in out:
        raise Skip("brothermode_cli status did not succeed: exit %s, "
                   "output: %s" % (r.returncode, _ascii(out, 300)))
    return out.strip()


def _drive_next(env, fixture_dir, cli_bin):
    r = _cli_run(env, fixture_dir, cli_bin, "next", "--project-id", PROJECT_ID)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or TASK_TITLE not in out:
        raise Skip("brothermode_cli next did not recommend the seeded "
                   "ready-state task: exit %s, output: %s"
                   % (r.returncode, _ascii(out, 300)))
    return out.strip()


def _drive_view(env, fixture_dir, cli_bin):
    r = _cli_run(env, fixture_dir, cli_bin, "view", "--project-id", PROJECT_ID,
                "--actor-type", "model", "--actor-name", ACTOR_NAME)
    out = (r.stdout or "") + (r.stderr or "")
    page = os.path.join(fixture_dir, "PROJECT-VIEW.html")
    if r.returncode != 0 or not os.path.isfile(page):
        raise Skip("brothermode_cli view did not write PROJECT-VIEW.html: "
                   "exit %s, output: %s" % (r.returncode, _ascii(out, 300)))
    return out.strip()


def _drive_deliver_refused(env, fixture_dir, cli_bin):
    """H-4: this is one of the safety properties the canary exists to
    prove, so its violation is a FAIL (a real regression this canary
    caught), never a SKIP. Both named shapes of that violation (deliver
    ACCEPTING incomplete evidence instead of refusing, and the expected
    refusal wording missing from an otherwise-nonzero exit) raise Fail."""
    r = _cli_run(env, fixture_dir, cli_bin, "deliver", "--project-id", PROJECT_ID)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 or DELIVER_REFUSAL_MARKER not in out:
        raise Fail("brothermode_cli deliver did not refuse the way "
                   "incomplete evidence should make it refuse (wanted a "
                   "nonzero exit and %r in the output): exit %s, "
                   "output: %s" % (DELIVER_REFUSAL_MARKER, r.returncode,
                                   _ascii(out, 300)))
    return out.strip()


def _drive_doctor(env, fixture_dir, cli_bin, settings_path):
    r = _cli_run(env, fixture_dir, cli_bin, "doctor", "--settings", settings_path)
    out = (r.stdout or "") + (r.stderr or "")
    if "BrotherMode doctor:" not in out or "proven," not in out:
        raise Skip("brothermode_cli doctor did not produce a real, "
                   "parseable verdict: exit %s, output: %s"
                   % (r.returncode, _ascii(out, 300)))
    summary = next((ln for ln in out.splitlines() if "proven," in ln), "")
    return summary.strip() or out.strip()[:120]


def _uninstall_v3_and_verify_clean(claude_bin, env, claude_config_dir):
    """H-4: "settings.json still carries a plugin remnant after uninstall"
    is the second named safety property this canary exists to prove, so a
    REAL, checked measurement that finds a remnant is a Fail. The
    mechanical steps that get there (the uninstall/remove commands
    themselves, reading back `plugin list --json`, reading settings.json
    off disk) stay Skip when they cannot be completed at all: that is
    "cannot check this here", not "checked, and it is wrong"."""
    r_un = _run([claude_bin, "plugin", "uninstall", "brothermode"],
               cwd=ROOT, env=env, timeout=CLAUDE_TIMEOUT_SECONDS)
    if r_un.returncode != 0:
        raise Skip("uninstalling brothermode did not succeed: exit %s, "
                   "output: %s"
                   % (r_un.returncode,
                      _ascii((r_un.stdout or "") + (r_un.stderr or ""), 300)))
    r_rm = _run([claude_bin, "plugin", "marketplace", "remove",
                BC.PROBE_MARKETPLACE_NAME], cwd=ROOT, env=env,
                timeout=CLAUDE_TIMEOUT_SECONDS)
    if r_rm.returncode != 0:
        raise Skip("removing the brothermode-marketplace marketplace did "
                   "not succeed: exit %s, output: %s"
                   % (r_rm.returncode,
                      _ascii((r_rm.stdout or "") + (r_rm.stderr or ""), 300)))
    r_list = _run([claude_bin, "plugin", "list", "--json"], cwd=ROOT, env=env,
                  timeout=CLAUDE_TIMEOUT_SECONDS)
    try:
        entries = json.loads(r_list.stdout or "null")
    except ValueError:
        entries = None
    if not isinstance(entries, list):
        raise Skip("claude plugin list --json produced no parseable list "
                   "after uninstall: %s" % _ascii(r_list.stdout or "", 300))
    if entries != []:
        raise Fail("claude plugin list --json is not empty after "
                   "uninstall: %s" % _ascii(r_list.stdout or "", 300))
    settings_path = os.path.join(claude_config_dir, "settings.json")
    try:
        with open(settings_path) as fh:
            settings = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Skip("settings.json at %s could not be read after uninstall: "
                   "%s: %s" % (settings_path, type(exc).__name__, exc))
    remnants = {}
    if settings.get("enabledPlugins"):
        remnants["enabledPlugins"] = settings["enabledPlugins"]
    if settings.get("extraKnownMarketplaces"):
        remnants["extraKnownMarketplaces"] = settings["extraKnownMarketplaces"]
    if remnants:
        raise Fail("settings.json at %s still carries a plugin remnant "
                   "after uninstall: %s" % (settings_path, remnants))
    return "enabledPlugins={}, extraKnownMarketplaces={}"


def run_lifecycle():
    """The full eighteen-step canary. Raises Skip on the first honest
    non-pass; returns the verdict list on a full pass. Every throwaway
    directory this function creates is deleted in the finally block,
    win or lose."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise Skip("no claude binary on PATH; install Claude Code or run "
                   "from a machine that has it")

    verdicts = []
    dirs_made = []
    try:
        _step(verdicts, claude_bin)

        home_dir = os.path.realpath(tempfile.mkdtemp(prefix="bm-e2e-home-"))
        dirs_made.append(home_dir)
        claude_config_dir = os.path.realpath(
            tempfile.mkdtemp(prefix="bm-e2e-claude-config-"))
        dirs_made.append(claude_config_dir)
        fixture_dir = os.path.realpath(
            tempfile.mkdtemp(prefix="bm-e2e-fixture-"))
        dirs_made.append(fixture_dir)
        broth_config = os.path.join(home_dir, ".brotherme", "config.json")
        # ONE env for the whole canary (every claude/git/python3 call below
        # shares it), the same design probe_installed itself uses: a
        # throwaway HOME and CLAUDE_CONFIG_DIR nobody has touched before,
        # with every GIT_ name and every pre-existing BrotherMode-recognized
        # variable stripped so nothing about this machine's own dogfood
        # install leaks in. See _probe_env's own docstring.
        env = BC._probe_env(home_dir, claude_config_dir, broth_config)
        _step(verdicts, "home=%s claude_config=%s fixture=%s"
             % (home_dir, claude_config_dir, fixture_dir))

        clone_dir = _clone_v2_tag(env, dirs_made)
        _step(verdicts, clone_dir)

        _install_v2_plugin(claude_bin, env, clone_dir)
        _step(verdicts, V2_PLUGIN_SPEC)

        _uninstall_v2_plugin(claude_bin, env)
        _step(verdicts)

        BC._install_plugin_shipped_way(
            claude_bin, env, claude_config_dir, "",
            "the e2e lifecycle canary's v3 install step")
        _step(verdicts, BC.PROBE_PLUGIN_SPEC)

        # codex 6: resolve the INSTALLED plugin's own tools/brothermode_cli.py
        # now, once, right after the install step establishes it, and drive
        # every CLI-boundary step below against THIS path, never the
        # checkout's. If the installed plugin exposes no CLI entry at all,
        # say so plainly rather than silently falling back to the source
        # tree and proving something else.
        cli_bin = _installed_cli_bin(claude_config_dir)
        if cli_bin is None:
            raise Skip(
                "the installed plugin (%s, under CLAUDE_CONFIG_DIR %s) "
                "exposes no tools/brothermode_cli.py at its own installPath "
                "(read from plugins/installed_plugins.json); this canary "
                "cannot drive the CLI boundary against the installed copy, "
                "and will not silently fall back to the checkout's own "
                "copy to prove it instead"
                % (BC.PROBE_PLUGIN_SPEC, claude_config_dir))

        version, found, total = _verify_v3_identity(claude_bin, env)
        _step(verdicts, "version=%s, %d/%d canonical skills present"
             % (version, found, total))

        _verify_single_plugin(claude_bin, env)
        _step(verdicts, "exactly one plugin (%s) registered"
             % BC.PROBE_PLUGIN_SPEC)

        _grant_consent(env, home_dir)
        _step(verdicts, broth_config)

        BC._write(fixture_dir, "README.md", "e2e lifecycle canary fixture\n")
        BC._init_repo(fixture_dir)
        BC._init_brothermode_store(fixture_dir)
        _step(verdicts, fixture_dir)

        start_out = _drive_start(env, fixture_dir, cli_bin)
        _step(verdicts, start_out)

        _seed_ready_task(env, fixture_dir)
        _step(verdicts, TASK_TITLE)

        status_out = _drive_status(env, fixture_dir, cli_bin)
        _step(verdicts, status_out.splitlines()[0] if status_out else "")

        next_out = _drive_next(env, fixture_dir, cli_bin)
        _step(verdicts, next_out.splitlines()[0] if next_out else "")

        view_out = _drive_view(env, fixture_dir, cli_bin)
        _step(verdicts, view_out.splitlines()[0] if view_out else "")

        deliver_out = _drive_deliver_refused(env, fixture_dir, cli_bin)
        _step(verdicts, deliver_out)

        settings_path = os.path.join(claude_config_dir, "settings.json")
        doctor_summary = _drive_doctor(env, fixture_dir, cli_bin, settings_path)
        _step(verdicts, doctor_summary)

        clean = _uninstall_v3_and_verify_clean(claude_bin, env,
                                               claude_config_dir)
        _step(verdicts, clean)

        return verdicts
    except Skip:
        raise
    except Fail:
        raise
    except Exception as exc:
        raise Skip("the e2e lifecycle canary hit an unexpected error "
                   "before it could reach a verdict, at step %d/%d (%s): "
                   "%s: %s"
                   % (len(verdicts) + 1, TOTAL_STEPS,
                      STEP_NAMES[len(verdicts)] if len(verdicts) < TOTAL_STEPS
                      else "(past the last named step)",
                      type(exc).__name__, exc))
    finally:
        for d in dirs_made:
            shutil.rmtree(d, ignore_errors=True)


def main(argv):
    if argv:
        print("bench_e2e_lifecycle: this canary takes no arguments")
        return 2
    try:
        verdicts = run_lifecycle()
    except Fail as exc:
        print("FAIL: %s" % exc)
        return 2
    except Skip as exc:
        print("SKIP: %s" % exc)
        return 1
    print("")
    print("PASSED: all %d steps proven." % len(verdicts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

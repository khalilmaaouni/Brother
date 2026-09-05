#!/usr/bin/env python3
"""virgin_unit_proof: the gate the v1.0.6 defect proved was missing.

The night of 2026-09-05, the first real signed-in Codex run on the public
plugin printed "NO-DATA: the loop's worker, verifier and repair modules
were not found" and refused its only unit. The cause: the public export
shipped bundle/runtime/loop_bridge.py without the three modules it imports
(bm_worker_spawn, bm_verify, bm_repair), which lived only under
products/brothermode/tools in the private hub.

PR 367 (wbs/codex-runtime-dependency) is the packaging fix: the engine
modules stay under products/brothermode/tools, and a real install gets them
as the SIBLING brothermode plugin from the same marketplace (Claude through
the dependency in bundle/.claude-plugin/plugin.json, Codex by installing
brothermode by name, since Codex has no dependency resolution). So this
gate now proves the packaging shape the fix actually ships, not the shape
the original defect described:

  LEG 1, the main proof. Builds the public export the documented way into a
  throwaway directory, then runs the app-bundled Codex CLI, isolated under
  a fresh HOME and CODEX_HOME, to add the export tree as a LOCAL
  marketplace and install BOTH plugins (`codex plugin add brother@brother`,
  `codex plugin add brothermode@brother`, exactly the commands
  docs/codex/SMOKE-RUNBOOK.md documents and scripts/codex_smoke.py drives).
  It then proves, BEFORE running anything, that loop_bridge.runtime_
  candidates() resolves ONLY inside that throwaway directory: no hub
  checkout, no development tree, no real ~/.claude, no real ~/.codex. Only
  then does it run ONE unit on a toy through the INSTALLED brother plugin's
  engine and the stub model worker, and require the receipt to show at
  least one integrated file and zero refused ones.

  LEG 2, "bundle alone". The original shape, kept as a labelled second
  check: carve ONLY bundle/ out of the export (what a plugin install's own
  `source` resolves to) with no marketplace and no sibling install. Under
  this packaging shape that is EXPECTED to find nothing, on purpose,
  because the engine now ships as a separate plugin: this leg's own
  verdict is NO-DATA, never FAIL, unless it deviates (finds the engine
  anyway, meaning something leaked, or breaks for an unrelated reason).

Verdicts, for the run as a whole: PASS, FAIL (naming the first refusal or
the NO-DATA line the engine printed), or NO-DATA (exit 2) when the export
could not be built, no Python interpreter is available, or no Codex binary
is available to smoke the install with. NO-DATA is never a pass.

Every log this run produces is read whole (never a tail) and its path is
printed; the throwaway directory is removed only after the verdict is
decided, and a cleanup failure never turns a decided verdict into a crash.

Python 3, standard library only.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402
import codex_smoke  # noqa: E402  # reuse: the toy shape, the stub seam and the app-bundled codex binary this script needs already live here

#: The outcome sentence the toy's decomposer stub is written for (reused
#: verbatim from codex_smoke.py's own DECOMPOSER_STUB, which parses no
#: argument other than that a unit exists; the text itself is documentation
#: for a reader, not something either script parses out of the other).
OUTCOME_SENTENCE = "make add() refuse non-numeric input"

#: The receipt door's own path convention (confirmed by reading
#: bundle/runtime/brother_run.py: RECEIPT_DIRNAME = "receipt",
#: RECEIPT_FILENAME = "receipt.json", LOG_FILENAME = "run.log"), read here
#: as literal constants rather than re-derived, because they are a stable,
#: already-shipped file contract, not something this script owns.
RECEIPT_DIRNAME = "receipt"
RECEIPT_FILENAME = "receipt.json"
RUN_LOG_FILENAME = "run.log"

#: The exact NO-DATA sentence loop_bridge.load_parts() prints when none of
#: its runtime candidates load (bundle/runtime/loop_bridge.py, load_parts).
#: This is the calibration signature: today's main must print this, and the
#: packaging fix must make it never appear on leg 1 (it is EXPECTED on leg
#: 2, which never installs the sibling plugin on purpose).
NODATA_SIGNATURE = ("the loop's worker, verifier and repair modules were "
                    "not found")

#: Environment markers cleared before the engine ever runs, so client
#: detection (scripts/brother_paths.py: CONFIG_DIR_ENV, CLIENT_ENV,
#: CLAUDE_MARKER_VARS, CODEX_MARKER_VARS) and the runtime override cannot
#: point at anything this session already has on disk. Read by name from
#: their own source rather than retyped as a second copy of the truth,
#: except for the four brother_paths.py constants: importing brother_paths
#: from the hub to compute them would run the RISK of computing the hub's
#: own values while THIS script's job is to prove the EXPORTED tree cannot
#: reach the hub at all, so these four are copied from a grep against
#: scripts/brother_paths.py (CLIENT_ENV, CONFIG_DIR_ENV, CLAUDE_MARKER_VARS,
#: CODEX_MARKER_VARS) rather than imported.
STATIC_ENV_MARKERS_TO_CLEAR = (
    "BROTHER_CLIENT",              # brother_paths.CLIENT_ENV
    "BROTHER_CONFIG_DIR",          # brother_paths.CONFIG_DIR_ENV
    "CLAUDE_CONFIG_DIR",
    "CLAUDECODE",                  # brother_paths.CLAUDE_MARKER_VARS
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SESSION_ID",            # brother_paths.CODEX_MARKER_VARS
    "CODEX_THREAD_ID",
    "CODEX_SANDBOX",
    "PYTHONPATH",
)

#: brother_paths.client() falls back to reading the plugin manifest beside
#: the resolved plugin root when no marker env var is set, and treats
#: finding BOTH .claude-plugin and .codex-plugin there as ambiguous ("").
#: The installed brother plugin ships both (one bundle/ serves both hosts:
#: measured on a real install, brother 1.0.6's own root carries
#: .claude-plugin/ and .codex-plugin/ side by side), so leg 1 must set an
#: explicit marker itself to answer "codex" and resolve CODEX_HOME rather
#: than falling back to a Claude-shaped config root. A real `codex exec`
#: turn exports CODEX_SESSION_ID to every command it runs (measured,
#: 2026-09-05, see brother_paths.client's own docstring); leg 1 is not run
#: inside one, so it sets the same marker by hand.
CODEX_SESSION_MARKER = "virgin-unit-proof-leg1"

_RUNTIME_ENV_VAR_RE = re.compile(r'^RUNTIME_ENV_VAR\s*=\s*"([^"]+)"', re.M)


def runtime_env_var_name(loop_bridge_path):
    """The exported loop_bridge.py's own env var name for the DEV_CANDIDATE
    override, read from its source rather than hardcoded. None when the
    file is absent or the constant cannot be found, which the caller turns
    into NO-DATA rather than guessing "BROTHER_RUNTIME_ROOT" and being wrong
    the day it is renamed."""
    try:
        with open(loop_bridge_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    m = _RUNTIME_ENV_VAR_RE.search(text)
    return m.group(1) if m else None


def build_export(dest, root=EP.ROOT):
    """(dest, "") or (None, why). The documented exporter, never a second
    one: export_public.build_export_tree, over export_public's own loaded
    allowlist."""
    allowlist = EP.load_allowlist()
    if allowlist is None:
        return None, ("the export allowlist could not be read from %s"
                      % EP.DEFAULT_ALLOWLIST)
    try:
        EP.build_export_tree(dest, allowlist, root=root)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "the export tree could not be built: %s" % exc
    if not os.path.isdir(dest):
        return None, "the exporter reported success but built nothing at %s" % dest
    return dest, ""


def build_install(export_dir, install_root):
    """Carve ONLY bundle/ out of the export tree into `install_root`: what a
    plugin install's own `source` resolves to for the "brother" plugin
    (docs/codex/SMOKE-RUNBOOK.md step 3: "<repo root>/bundle"). Used by leg
    2 only, "bundle alone": no marketplace, no sibling brothermode install,
    so the engine is expected to be unreachable from here by design.
    Returns (bundle_dir, "") or (None, why). Nothing under products/ is
    ever read here: the source of this copy is export_dir/bundle alone."""
    src = os.path.join(export_dir, "bundle")
    if not os.path.isdir(src):
        return None, "the export tree carries no bundle/ directory at %s" % src
    dst = os.path.join(install_root, "bundle")
    try:
        shutil.copytree(src, dst)
    except OSError as exc:
        return None, "the bundle could not be copied into the install tree: %s" % exc
    return dst, ""


def child_env(home, codex_home, runtime_env_var, as_codex=False):
    """The environment the exported engine runs under: HOME and CODEX_HOME
    inside the throwaway root, every client marker and the runtime override
    cleared, PYTHONPATH cleared. `as_codex` sets CODEX_SESSION_MARKER so
    brother_paths.client() answers "codex" instead of the ambiguous "" the
    installed plugin's own dual manifest would otherwise produce (see
    CODEX_SESSION_MARKER above). Never mutates os.environ."""
    env = dict(os.environ)
    for name in STATIC_ENV_MARKERS_TO_CLEAR:
        env.pop(name, None)
    if runtime_env_var:
        env.pop(runtime_env_var, None)
    env["HOME"] = home
    env["CODEX_HOME"] = codex_home
    if as_codex:
        env["CODEX_SESSION_ID"] = CODEX_SESSION_MARKER
    return env


#: Written once into the throwaway directory and run with the child env, so
#: the import happens in a FRESH interpreter under the exact environment the
#: engine itself will run under, never in this process (which already has
#: this hub's own scripts/ on sys.path and would answer a different
#: question). Reads the runtime directory from an env var rather than a
#: templated string, so no path ever needs shell- or Python-string escaping.
CANDIDATES_PROBE = """
import os, sys
sys.path.insert(0, os.environ["VUP_RUNTIME_DIR"])
import loop_bridge
for c in loop_bridge.runtime_candidates():
    print(c)
"""


def runtime_candidates(runtime_dir, env, python=sys.executable):
    """[candidates] as the EXPORTED loop_bridge.py itself would resolve
    them, under `env`, or (None, why). Run in a fresh subprocess: importing
    the exported module in this process would leave it on sys.modules and
    could shadow or be shadowed by this hub's own scripts/loop_bridge.py."""
    probe_env = dict(env)
    probe_env["VUP_RUNTIME_DIR"] = runtime_dir
    try:
        proc = subprocess.run([python, "-c", CANDIDATES_PROBE], cwd=runtime_dir,
                              env=probe_env, capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "the candidate probe could not be run: %s" % exc
    if proc.returncode != 0:
        return None, ("the candidate probe exited %d: %s" % (
            proc.returncode, (proc.stderr or proc.stdout or "").strip()[:400]))
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return lines, ""


def _under_root(path, root):
    """True when `path` is `root` itself or sits below it, resolved through
    realpath so a symlinked temp root (macOS /tmp -> /private/tmp) is never
    a false positive."""
    rp = os.path.realpath(path)
    rr = os.path.realpath(root)
    return rp == rr or rp.startswith(rr + os.sep)


def check_candidates(candidates, throwaway):
    """(True, "") when every one of `candidates` resolves inside
    `throwaway`; (False, why) naming every one that does not. This is the
    proof's own isolation assertion: a candidate list that could resolve to
    a hub checkout, a development tree, or a real ~/.claude or ~/.codev
    would mean the "virgin machine" this script builds could still see the
    private tree it exists to keep unreachable."""
    bad = [c for c in candidates if not _under_root(c, throwaway)]
    if bad:
        return False, ("the runtime candidate list resolves outside the "
                       "throwaway directory %s: %s" % (throwaway, ", ".join(bad)))
    return True, ""


def _find_line(text, needle):
    """The first line of `text` containing `needle`, stripped, or ""."""
    for line in (text or "").splitlines():
        if needle in line:
            return line.strip()
    return ""


def _read_json(path):
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), ""
    except (OSError, ValueError) as exc:
        return None, "%s could not be read: %s" % (path, exc)


def find_receipt_path(stdout_text, run_dir):
    """The receipt path the engine itself named on its last line
    ("brother_run: receipt: <path>"), or, failing that, the receipt door's
    own path convention under `run_dir`. Both are accepted, in that order."""
    for line in reversed((stdout_text or "").splitlines()):
        line = line.strip()
        if line.startswith("brother_run: receipt:"):
            return line.split("brother_run: receipt:", 1)[1].strip()
    if run_dir:
        candidate = os.path.join(run_dir, RECEIPT_DIRNAME, RECEIPT_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def decide(receipt_body, log_text, receipt_problem=""):
    """(status, message). status is "PASS" or "FAIL". Pure: no I/O, so this
    is the piece scripts/test_virgin_unit_proof.py drives with fixtures.

    receipt_body is the parsed receipt.json (receipt_door.receipt_record's
    shape: "evidence" is every unit in state "verified", "unproven" is
    every unit in state "no-data" or "refused"), or None when no receipt
    could be read at all. log_text is the run's own run.log, read whole, so
    the loop_bridge NO-DATA sentence this gate calibrates against is always
    available to quote even when the receipt itself carries a different,
    laundered reason (measured: a unit that was never claimed at all reads
    "no-data: the files this unit changed were not recorded" in its own
    receipt row, never the loop_bridge sentence, because that sentence
    never reached the Work document; it only ever reached the run log)."""
    if not isinstance(receipt_body, dict):
        line = _find_line(log_text, NODATA_SIGNATURE)
        if line:
            return "FAIL", "no receipt was written; the engine reported: %s" % line
        return "FAIL", ("no receipt was written: %s"
                        % (receipt_problem or "no reason was captured"))
    evidence = receipt_body.get("evidence")
    unproven = receipt_body.get("unproven")
    if not isinstance(evidence, list) or not isinstance(unproven, list):
        return "FAIL", "the receipt at hand carries no evidence/unproven lists to read"
    integrated = len(evidence)
    refused = [e for e in unproven if isinstance(e, dict)
              and e.get("state") == "refused"]
    if integrated >= 1 and not refused:
        return "PASS", "%d unit(s) integrated, 0 refused" % integrated
    # THE LOG'S OWN NO-DATA SIGNATURE OUTRANKS A UNIT'S OWN REFUSAL REASON,
    # even when one is on file. Measured on the calibration run itself
    # (today's main, the exact defect this gate exists for): the unit's own
    # receipt row read "it was never started this run, because a
    # dependency, a full slot or the scheduler's own admission check held
    # it back", a true but LAUNDERED description, because the scheduler
    # never even reached admission: loop_bridge.load_parts() failed first
    # and the round exited before any node was claimed. The root cause only
    # ever reached the run log (receipts_for reads the Work document and
    # the claim store, neither of which loop_bridge's own stdout/stderr
    # ever touches), so a caller that stopped at the first refused entry
    # would report the symptom and never quote the cause this gate is
    # calibrated to catch.
    line = _find_line(log_text, NODATA_SIGNATURE)
    if line:
        return "FAIL", "the engine reported NO-DATA: %s" % line
    if refused:
        first = refused[0]
        return "FAIL", ("%s was refused: %s"
                        % (first.get("id", "?"), first.get("reason", "?")))
    if unproven:
        first = unproven[0]
        return "FAIL", ("no unit integrated (0 of %d); %s: %s"
                        % (len(unproven), first.get("id", "?"),
                           first.get("reason", "?")))
    return "FAIL", "no unit integrated, and the receipt names no reason"


def classify_bundle_alone(status, message):
    """Leg 2's own verdict, translated for a packaging shape where the
    engine is a SIBLING plugin by design: a plain calibration FAIL that
    quotes NODATA_SIGNATURE is the EXPECTED shape and reads as NO-DATA
    here, never a failure of the whole run. Anything else (an unexpected
    PASS, meaning the private engine leaked into the public bundle; or a
    FAIL for an unrelated reason, meaning the calibration itself broke) is
    a real FAIL. Pure, so scripts/test_virgin_unit_proof.py can drive it
    without running an export or a Codex binary."""
    if status == "PASS":
        return "FAIL", ("bundle alone unexpectedly integrated a unit; the "
                        "private engine may have leaked into the public bundle")
    if status == "FAIL" and NODATA_SIGNATURE in (message or ""):
        return "NO-DATA", ("bundle alone finds nothing, as designed: %s" % message)
    return "FAIL", ("bundle alone failed for a reason other than the "
                    "expected NO-DATA signature: %s" % message)


def install_plugins(codex_bin, export_dir, env):
    """Run the three documented Codex plugin commands against `export_dir`
    as a local marketplace: the exact commands docs/codex/SMOKE-RUNBOOK.md
    and scripts/codex_smoke.py drive on the 367 branch. [(step, proc), ...],
    never raising: a failed "plugin add brothermode" is exactly the
    calibration shape pre-367 main is expected to produce (the marketplace
    there does not list brothermode at all), and the caller decides what a
    given failure means rather than this function aborting on it."""
    return [
        ("marketplace add", codex_smoke.sh(
            [codex_bin, "plugin", "marketplace", "add", export_dir], env=env)),
        ("plugin add brother", codex_smoke.sh(
            [codex_bin, "plugin", "add", "brother@brother", "--json"], env=env)),
        ("plugin add brothermode", codex_smoke.sh(
            [codex_bin, "plugin", "add", "brothermode@brother", "--json"],
            env=env)),
    ]


def _install_version_key(brother_run_path):
    """Numeric ordering for an installed …/<version>/runtime/brother_run.py,
    so 1.0.10 correctly outranks 1.0.9 (a plain string sort would not: "10"
    sorts before "6" character by character). Mirrors loop_bridge's own
    _version_key; a non-numeric version name sorts last, not crashes."""
    version = os.path.basename(os.path.dirname(os.path.dirname(brother_run_path)))
    try:
        return tuple(int(x) for x in version.split("."))
    except ValueError:
        return (-1,)


def find_installed_brother_run(codex_home):
    """The installed brother plugin's own runtime/brother_run.py under
    `codex_home`'s plugin cache, or None when nothing was installed there.
    The highest version wins on the rare chance more than one is present."""
    matches = glob.glob(os.path.join(
        codex_home, "plugins", "cache", "brother", "brother", "*",
        "runtime", "brother_run.py"))
    if not matches:
        return None
    return sorted(matches, key=_install_version_key)[-1]


def run_toy_unit(brother_run_py, runtime_dir, env, leg_root):
    """Build the toy, wire the stub model seam, run `brother_run_py` against
    it, and decide() the receipt. Returns (status, exit_code, lines): the
    piece shared by both legs, differing only in which engine and which
    environment they hand it."""
    lines = []
    say = lines.append

    toy_dir = os.path.join(leg_root, "toy")
    why = codex_smoke.build_toy(toy_dir)
    if why:
        say("NO-DATA: %s" % why)
        return "NO-DATA", 2, lines

    stubs_dir = os.path.join(leg_root, "stubs")
    os.makedirs(stubs_dir, exist_ok=True)
    decomposer = codex_smoke.write_stub(
        os.path.join(stubs_dir, "decomposer.py"), codex_smoke.DECOMPOSER_STUB)
    model = codex_smoke.write_stub(
        os.path.join(stubs_dir, "model.py"), codex_smoke.MODEL_STUB)
    run_env = dict(env)
    run_env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
    run_env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)

    runs_root = os.path.join(leg_root, "runs")
    os.makedirs(runs_root, exist_ok=True)

    cmd = [sys.executable, brother_run_py, OUTCOME_SENTENCE, "--cwd", toy_dir,
           "--runs-root", runs_root, "--quiet"]
    say("$ %s" % " ".join(cmd))
    start = time.time()
    try:
        proc = subprocess.run(cmd, cwd=runtime_dir, env=run_env,
                              capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired as exc:
        say("FAIL: the engine did not finish within 240s: %s" % exc)
        return "FAIL", 1, lines
    except OSError as exc:
        say("NO-DATA: the exported engine could not even be started: %s" % exc)
        return "NO-DATA", 2, lines
    elapsed = time.time() - start
    say("engine exit %d after %.1fs" % (proc.returncode, elapsed))
    stdout_text = proc.stdout or ""
    for line in stdout_text.splitlines():
        say("  %s" % line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            say("  stderr: %s" % line)

    run_dir = (codex_smoke.newest_run_dir(
        os.path.join(runs_root, "docs", "plan", "runs"))
        or codex_smoke.newest_run_dir(runs_root))
    if run_dir is None:
        say("FAIL: the run left no run directory under %s" % runs_root)
        return "FAIL", 1, lines
    say("run directory: %s" % run_dir)

    log_path = os.path.join(run_dir, RUN_LOG_FILENAME)
    try:
        with open(log_path, encoding="utf-8") as fh:
            log_text = fh.read()
        say("run log (kept whole): %s" % log_path)
    except OSError:
        log_text = ""
        say("(no run log at %s)" % log_path)

    receipt_path = find_receipt_path(stdout_text, run_dir)
    receipt_body, receipt_problem = (None, "no receipt path was found")
    if receipt_path:
        say("receipt: %s" % receipt_path)
        receipt_body, receipt_problem = _read_json(receipt_path)

    status, message = decide(receipt_body, log_text, receipt_problem)
    say("%s: %s" % (status, message))
    return status, (0 if status == "PASS" else 1), lines


def run_install_leg(export_dir, throwaway, codex_bin, runtime_var):
    """Leg 1: the export tree as a local Codex marketplace, both plugins
    installed into an isolated CODEX_HOME, the unit run through whichever
    engine that install actually produced. Returns (exit_code, lines)."""
    lines = []
    say = lines.append
    say("--- leg 1: fresh export tree as a local Codex marketplace, both "
       "plugins installed into an isolated CODEX_HOME ---")

    leg_root = os.path.join(throwaway, "leg-install")
    codex_home_dir = os.path.join(leg_root, "codex-home")
    home_dir = os.path.join(leg_root, "home")
    for d in (codex_home_dir, home_dir):
        os.makedirs(d, exist_ok=True)
    env = child_env(home_dir, codex_home_dir, runtime_var, as_codex=True)

    for step_name, proc in install_plugins(codex_bin, export_dir, env):
        say("$ codex %s -> exit %d" % (step_name, proc.returncode))
        body = ((proc.stdout or "") + (proc.stderr or "")).strip()
        for ln in body.splitlines()[-6:]:
            say("  %s" % ln)
        if proc.returncode != 0:
            if step_name == "plugin add brothermode":
                # EXPECTED pre-367: the marketplace there does not list
                # brothermode at all, and the proof still has a job to do,
                # honestly running whatever a virgin machine actually got.
                say("  (brothermode not installable here; continuing, "
                   "since that is exactly what pre-fix main should show)")
                continue
            say("NO-DATA: %s failed at exit %d, so the marketplace install "
               "could not be exercised" % (step_name, proc.returncode))
            return 2, lines

    brother_run_py = find_installed_brother_run(codex_home_dir)
    if not brother_run_py:
        say("NO-DATA: no runtime/brother_run.py under the installed brother "
           "plugin cache at %s" % codex_home_dir)
        return 2, lines
    runtime_dir = os.path.dirname(brother_run_py)
    say("installed engine: %s" % brother_run_py)

    candidates, why = runtime_candidates(runtime_dir, env)
    if candidates is None:
        say("NO-DATA: %s" % why)
        return 2, lines
    say("loop_bridge runtime candidates (installed, CODEX_HOME=%s, HOME=%s):"
       % (codex_home_dir, home_dir))
    for c in candidates:
        say("  %s" % c)
    ok, why = check_candidates(candidates, leg_root)
    if not ok:
        say("FAIL: %s" % why)
        return 1, lines

    status, code, extra = run_toy_unit(brother_run_py, runtime_dir, env, leg_root)
    lines.extend(extra)
    return code, lines


def run_bundle_alone_leg(export_dir, throwaway, runtime_var):
    """Leg 2: "bundle alone", the original shape. Carve only bundle/ out of
    the export, no marketplace, no sibling brothermode install. Expected
    NO-DATA under this packaging shape: the engine now ships as a separate
    plugin, so bundle alone finding nothing is the design working, not a
    defect. Returns (exit_code, lines), where exit_code follows
    classify_bundle_alone (0 for the expected NO-DATA, 1 for a real
    deviation, 2 if the leg could not even be set up)."""
    lines = []
    say = lines.append
    say("--- leg 2: bundle alone (no marketplace, no sibling install; "
       "expected NO-DATA, the engine is a sibling plugin by design) ---")

    leg_root = os.path.join(throwaway, "leg-bundle-alone")
    install_root = os.path.join(leg_root, "install")
    home_dir = os.path.join(leg_root, "home")
    codex_home_dir = os.path.join(leg_root, "codex-home")
    for d in (install_root, home_dir, codex_home_dir):
        os.makedirs(d, exist_ok=True)

    bundle_dir, why = build_install(export_dir, install_root)
    if bundle_dir is None:
        say("NO-DATA: %s" % why)
        return 2, lines
    runtime_dir = os.path.join(bundle_dir, "runtime")
    brother_run_py = os.path.join(runtime_dir, "brother_run.py")
    if not os.path.isfile(brother_run_py):
        say("NO-DATA: the exported bundle carries no runtime/brother_run.py"
           " at %s" % brother_run_py)
        return 2, lines
    say("install tree (bundle/ only, nothing from products/): %s" % bundle_dir)

    env = child_env(home_dir, codex_home_dir, runtime_var)
    candidates, why = runtime_candidates(runtime_dir, env)
    if candidates is None:
        say("NO-DATA: %s" % why)
        return 2, lines
    say("loop_bridge runtime candidates (bundle alone, %s unset, HOME=%s, "
       "CODEX_HOME=%s):" % (runtime_var, home_dir, codex_home_dir))
    for c in candidates:
        say("  %s" % c)
    ok, why = check_candidates(candidates, leg_root)
    if not ok:
        say("FAIL: %s" % why)
        return 1, lines

    status, _code, extra = run_toy_unit(brother_run_py, runtime_dir, env, leg_root)
    lines.extend(extra)
    message = extra[-1] if extra else ""
    verdict, explanation = classify_bundle_alone(status, message)
    say("%s: %s" % (verdict, explanation))
    return {"PASS": 1, "FAIL": 1, "NO-DATA": 0}[verdict], lines


def run(work=None, keep=False, root=None):
    """Run the whole proof once, exporting from `root` (default: this
    checkout, export_public.ROOT). scripts/release_closeout.py's X1 leg
    passes the tag's own local clone here, so the proof runs against the
    exact tree a release candidate would ship, not against whatever
    checkout happens to be running this script. Returns (exit_code, lines)
    where lines is everything this run wants printed, in order. exit_code:
    0 PASS, 1 FAIL, 2 NO-DATA. Driven by leg 1; leg 2 deviating from its own
    expected NO-DATA also fails the run, since that means the calibration
    itself broke."""
    lines = []

    def say(text):
        lines.append(text)

    if not (os.path.isfile(sys.executable) and os.access(sys.executable, os.X_OK)):
        say("NO-DATA: no usable Python interpreter at %s" % sys.executable)
        return 2, lines

    codex_bin = codex_smoke.DEFAULT_CODEX
    if not os.path.isfile(codex_bin) or not os.access(codex_bin, os.X_OK):
        say("NO-DATA: no executable Codex binary at %s, so the marketplace "
           "install leg could not be run" % codex_bin)
        return 2, lines

    throwaway = work or tempfile.mkdtemp(prefix="virgin-unit-proof-")
    say("throwaway directory: %s" % throwaway)
    export_dir = os.path.join(throwaway, "export")
    os.makedirs(export_dir, exist_ok=True)

    try:
        built, why = build_export(export_dir, root=root or EP.ROOT)
        if built is None:
            say("NO-DATA: %s" % why)
            return 2, lines
        say("export tree built at %s" % export_dir)

        loop_bridge_src = os.path.join(export_dir, "bundle", "runtime", "loop_bridge.py")
        runtime_var = runtime_env_var_name(loop_bridge_src)
        if not runtime_var:
            say("NO-DATA: could not read RUNTIME_ENV_VAR out of %s" % loop_bridge_src)
            return 2, lines

        leg1_code, leg1_lines = run_install_leg(export_dir, throwaway, codex_bin,
                                                runtime_var)
        lines.extend(leg1_lines)

        leg2_code, leg2_lines = run_bundle_alone_leg(export_dir, throwaway,
                                                     runtime_var)
        lines.extend(leg2_lines)

        overall = leg1_code
        if leg2_code != 0 and overall == 0:
            # Leg 2 was supposed to answer NO-DATA (0 in its own exit-code
            # mapping); anything else means the calibration itself broke,
            # and a clean leg 1 must not paper over that.
            overall = 1
        say("OVERALL: leg1=%s leg2=%s"
           % (("PASS" if leg1_code == 0 else "FAIL" if leg1_code == 1 else "NO-DATA"),
              ("NO-DATA(expected)" if leg2_code == 0
               else "FAIL" if leg2_code == 1 else "NO-DATA(unexpected)")))
        return overall, lines
    finally:
        if not keep:
            shutil.rmtree(throwaway, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true",
                    help="keep the throwaway directory instead of removing it")
    ap.add_argument("--work", default=None,
                    help="use this directory instead of a fresh tempdir "
                         "(created if absent; not removed even without --keep, "
                         "since the caller named it)")
    args = ap.parse_args(argv)
    keep = args.keep or bool(args.work)
    code, lines = run(work=args.work, keep=keep)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

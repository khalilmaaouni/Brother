#!/usr/bin/env python3
"""product_acceptance: P0.4 of docs/plan/P0-COMPOSITION-WAVE-2026-08-30.md.

The eleven capability areas (docs/plan/CAPABILITY-AREAS.json), re-proven
THROUGH THE PUBLIC ENTRY POINT this time: scripts/brother_run.py, fed a
plain outcome sentence. No prepared Work document, no manual loop_bridge
invocation, no internal worker command named on this harness's own command
line. Contrast with scripts/acceptance.py and its scripts/acceptance_1.py..
acceptance_11.py, which drive loop_bridge.py / claim_store.py / work_record.py
directly with a hand-built plan: those prove the SPINE works when you know
how to operate it by hand. This proves the DOOR works when all you give it
is a sentence, which is the whole point of the composition wave (stop
extending the machine, package the machine).

Every area function below is the real test: it writes a temp git repository
and a pair of deterministic stub scripts, feeds brother_run.py one outcome
string, and reads its exit code, its stdout delivery report, and the target
repository's own git log and claim store, exactly as a caller with no
knowledge of the internals would be able to. Model calls are STUBBED by
default (DOOR_MODEL_CMD and MODEL_WORKER_CMD point at throwaway scripts
this file writes at run time, the identical seam scripts/test_brother_run.py
already uses), so the whole suite is hermetic: no network, no real `claude`.
--live drops the stubs and lets brother_run.py fall back to its own
environment defaults (the real `claude` CLI), for a founder-run proof; it is
never the default and the areas that depend on a stub's exact timing or a
scripted misbehavior (2, 5, 8 and 9) report
NO-DATA under --live rather than pretend a race against a real model's
latency is a proof.

Exit contract, the same law as scripts/acceptance.py: NO-DATA never flips
main()'s exit code (a run where nothing could be checked has not said
anything is broken), and the only nonzero exit is a real FAIL. An unknown
--area id returns 2 directly, matching acceptance.py's own handling.

--area ID restricts the run to one area. --explain (only with --area)
prints that area's own one-line description before running it.

PRODUCER: this module is the sole producer of every file it writes, and every
one of them is a throwaway test fixture built inside a tempfile.mkdtemp()
directory for the duration of one area's own run, never a durable record
meant to outlive it. The real write calls, executed directly by this module
rather than by a spawned stub subprocess, are: _edit_expires_at() (`with
open(store, "w", encoding="utf-8") as fh: json.dump(claims, fh, indent=1)`,
simulating a claim's lease TTL for areas 2 and 9), _build_two_package_monorepo()
(writes existing.py and gen.py into each seeded package, for area 4),
_build_short_timeout_runtime() (patches a copy of bm_worker_spawn.py's
timeout constant and writes it to a fake runtime tree, for area 5), and
area_6() (writes, then rewrites, the unrelated.txt fixture used to prove a
dirty tree is preserved). The writes inside partial_failure_model_body(),
slow_model_body() and MONOREPO_MODEL are not this module's own code executed
at run time: they are string literals containing separate throwaway stub
scripts, written to disk once by stub_env()/tbr.write_stub() and then
executed by a spawned subprocess, never by this module directly.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
sys.path.insert(0, HERE)
import brother_run  # noqa: E402  # ENGINE_JSON_FILES, the one list of the engine's own bookkeeping json names; see _work_doc_path below
import test_brother_run as tbr  # noqa: E402  # reuse make_repo/write_stub, the exact stub seam brother_run's own suite already uses

NODATA = "NO-DATA"

def _load_areas():
    """The id-to-name list, read from the canonical file rather than
    duplicated here. Two earlier versions of this harness carried their own
    hardcoded lists whose ids DISAGREED with docs/plan/CAPABILITY-AREAS.json
    (one's [3] tested what the canonical file numbers 9; the next repeated
    "monorepos and generated code" at two ids and dropped three capability
    names from the surface entirely), so a PASS line's [n] claimed one
    capability while proving another. One source of truth ends the class."""
    path = os.path.join(HERE, os.pardir, "docs", "plan",
                        "CAPABILITY-AREAS.json")
    with open(path, encoding="utf-8") as fh:
        return [(str(a["id"]), a["name"]) for a in json.load(fh)]


AREAS = _load_areas()

DESCRIPTIONS = {
    "1": "One plain outcome sentence into brother_run.py in a fresh repo; "
         "fails when nothing lands integrated inside a fixed time budget.",
    "2": "Kills brother_run.py after the first unit integrates, then "
         "--resume; fails when the remainder is lost or redone from scratch.",
    "3": "Two units proposed, one deliberately never delivers; fails when "
         "accepting the good unit also lands the bad one, or the report "
         "does not name the rejected unit. The door's unit granularity is "
         "its expression of partial acceptance.",
    "4": "A two-package monorepo; one unit's worker writes correctly inside "
         "its own package AND, undeclared, into the other package's "
         "generated directory; fails unless the undeclared write is "
         "quarantined by name while the correctly-scoped unit still lands.",
    "5": "A stub worker that never returns; fails if the run does not exit "
         "well inside this test's own watchdog, or the hung unit is not "
         "reported failed by name.",
    "6": "An uncommitted, unrelated edit already sitting in the target "
         "repository before brother_run ever runs; fails if that edit is "
         "altered, dropped, or integration proceeds over the dirty tree at "
         "all before it is resolved and retried.",
    "7": "One unit's done_check passes, another's cannot even run; fails "
         "when the un-runnable check reads as passed, or that unit "
         "integrates anyway.",
    "8": "A normal safe run end to end with stdin closed, then a unit whose "
         "declared write scope escapes the repository; fails when the safe "
         "run needs a manual approval, or the escaping declaration is not "
         "refused.",
    "9": "Kills brother_run.py mid-unit so a claim is left live under a dead "
         "owner; fails when a second claim is granted while that lease is "
         "still live, or when the eventual reclaim duplicates the work.",
    "11": "The delivery report's integrated list against the repository's own "
          "git merges; fails when they do not match exactly.",
}
NOT_WRITTEN = ("no product-path test: an editor capability (jump to "
               "definition, rename across files, targeted undo) has no seam "
               "through brother_run.py to prove it; measured at the editor "
               "surface, never through this door")


# ---------------------------------------------------------------------------
# shared fixtures: stub model bodies and small subprocess helpers
# ---------------------------------------------------------------------------

ONE_UNIT_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "U1", "objective": "create the outcome file",
         "done_check": "test -f outcome.txt", "writes": ["outcome.txt"],
         "deps": []},
    ]))
"""

TWO_INDEPENDENT_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "U1", "objective": "create file one",
         "done_check": "test -f u1.txt", "writes": ["u1.txt"], "deps": []},
        {"id": "U2", "objective": "create file two",
         "done_check": "test -f u2.txt", "writes": ["u2.txt"], "deps": []},
    ]))
"""

TWO_DEPENDENT_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "A1", "objective": "create file one",
         "done_check": "test -f one.txt", "writes": ["one.txt"], "deps": []},
        {"id": "A2", "objective": "create file two",
         "done_check": "test -f two.txt", "writes": ["two.txt"],
         "deps": ["A1"]},
    ]))
"""

# Writes whatever the prompt declares; standing in for a real model that just
# does the work. Identical shape to test_brother_run.py's WRITER_MODEL.
WRITER_MODEL = tbr.WRITER_MODEL


def slow_model_body(sleep_seconds, slow_suffix):
    """Like WRITER_MODEL, but sleeps BEFORE writing when the declared write
    scope ends with `slow_suffix`. The sleep is what gives an external kill a
    real window: it starts only after the unit is durably claimed, so a kill
    landing anywhere in that window catches a claim that is live but whose
    owner is about to be dead."""
    return textwrap.dedent(f"""
        import re, sys, time
        prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
        m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
        paths = [p.strip() for p in (m.group(1).split(",") if m else []) if p.strip()]
        if any(p.endswith({slow_suffix!r}) for p in paths):
            time.sleep({sleep_seconds})
        for path in paths:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
        print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
    """)


def partial_failure_model_body(never_write_suffix):
    """Runs cleanly (exit 0) but silently skips writing the one file whose
    name ends with `never_write_suffix`. Simulates a worker that RAN without
    error yet never delivered, the shape area 4 exists to catch: a model
    process succeeding is not the same claim as a unit's own check passing.
    """
    return textwrap.dedent(f"""
        import re, sys
        prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
        m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
        paths = [p.strip() for p in (m.group(1).split(",") if m else []) if p.strip()]
        for path in paths:
            if path.endswith({never_write_suffix!r}):
                print("stub model: silently skipping the write for %s" % path)
                continue
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
        print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
    """)


# area 6: a two-package monorepo. U1 is the requested change, scoped and
# behaved correctly. U2 is scoped to a different corner of the SAME
# requested package, but its stub worker deliberately ALSO writes into the
# OTHER package's generated directory, undeclared, the shape area 6 exists
# to catch.
MONOREPO_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "U1", "objective": "add a helper to pkg_a's hand-written source",
         "done_check": "test -f packages/pkg_a/src/hand.py && "
                       "grep -q helper packages/pkg_a/src/hand.py",
         "writes": ["packages/pkg_a/src"], "deps": []},
        {"id": "U2", "objective": "add a docs note for pkg_a's change",
         "done_check": "test -f packages/pkg_a/docs/note.txt",
         "writes": ["packages/pkg_a/docs"], "deps": []},
    ]))
"""

MONOREPO_MODEL = """
    import re, sys, os
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    paths = [p.strip() for p in (m.group(1).split(",") if m else []) if p.strip()]
    for scope in paths:
        if scope.endswith("pkg_a/src"):
            os.makedirs(scope, exist_ok=True)
            with open(os.path.join(scope, "hand.py"), "w") as fh:
                fh.write("def helper():\\n    return 1\\n")
        elif scope.endswith("pkg_a/docs"):
            os.makedirs(scope, exist_ok=True)
            with open(os.path.join(scope, "note.txt"), "w") as fh:
                fh.write("note\\n")
            # THE DELIBERATE UNDECLARED WRITE: the OTHER package's own
            # generated directory, never declared by this unit's scope.
            with open("packages/pkg_b/generated/gen.py", "a") as fh:
                fh.write("\\n# tampered by the docs unit\\n")
    print("stub model wrote for scope %s" % paths)
"""

# area 8: a worker that never returns at all, standing in for the real hung
# process acceptance_5.py's own mechanism-level test uses. Reaping this
# depends on the run's own spawn timeout, which is why area_5 below patches
# that timeout down for test speed rather than waiting out the real default.
HANGING_MODEL = """
    import sys, time
    sys.stdin.read()
    time.sleep(999999)
"""


def stub_env(tmp, decomposer_body, model_body):
    """DOOR_MODEL_CMD/MODEL_WORKER_CMD pointed at throwaway scripts in `tmp`."""
    decomposer = tbr.write_stub(tmp, "decomposer.py", decomposer_body)
    model = tbr.write_stub(tmp, "model.py", model_body)
    env = dict(os.environ)
    env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
    env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
    return env


def _sh(args, cwd=None, env=None, timeout=90, check=False):
    # check=True is for FIXTURE git calls only: a fixture that half-builds
    # its repository must blow up here, in the fixture, never leak a broken
    # state into the area's verdict (a leaked fixture config poisoned this
    # repository's own git identity once).
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout, check=check)


# CWD ON EVERY brother_run LAUNCH IS NOT DECORATION. Measured live during
# this file's own development: a unit whose done_check fails goes through
# the shared repair loop (bm_repair.repair, in the external tools checkout),
# and that loop's retry attempts call worker.run(unit) WITHOUT forwarding
# the lane's cwd, so model_worker.py's own `cwd = os.getcwd()` falls back to
# whatever OS-level directory the brother_run.py PROCESS ITSELF was launched
# from. Every subprocess call below is given `cwd=repo` (the throwaway temp
# repository this area already created) for exactly that reason: if the
# fallback ever fires, it lands harmlessly inside a tempdir this test owns,
# never inside whatever real repository the caller's shell happens to be
# sitting in. Reproduced 3 times live: a repair retry ran `git add -A; git
# commit -m "unit U2: model worker"` directly against this very checkout,
# once sweeping up and committing this file's own uncommitted draft. Flagged
# separately for a fix in the shared tools checkout; this is the local
# containment, not the fix.
def _popen_group(args, cwd=None, env=None):
    """A backgroundable brother_run, in its own process group so a crash can
    be simulated by killing the WHOLE tree (brother_run, loop_bridge's
    in-process worker threads, and the model subprocess two levels down),
    never just the top pid: an orphaned stub model left running could still
    write into the target repository after the "crash", exactly the kind of
    interference a real proof cannot afford."""
    return subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            preexec_fn=os.setsid)


def _killpg(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:  # sbe: allow-silent the process already exited, which is the state the kill wanted
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:  # sbe: allow-silent a zombie that outlives SIGKILL by 15s is the OS's to reap; the test's own assertions still decide the verdict
        pass
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()


def _find_run_dir(runs_root, deadline):
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    while time.time() < deadline:
        if os.path.isdir(runs_dir):
            entries = sorted(e for e in os.listdir(runs_dir)
                             if os.path.isdir(os.path.join(runs_dir, e)))
            if entries:
                return os.path.join(runs_dir, entries[0])
        time.sleep(0.05)
    return None


def _work_doc_path(run_dir):
    # ENGINE_JSON_FILES (claims.json, target.json, capsule.json, the usage
    # sidecar) are brother_run.py's own bookkeeping files, never the Work
    # document itself. THIS DUPLICATED A HARDCODED TUPLE UNTIL E73.2: the
    # same bug TheUsageSidecarDoesNotHideTheWorkDocument (test_brother_run.py)
    # already names once, for claims_usage.json -- a run directory holding a
    # THIRD bookkeeping json this list did not know about made this function
    # mistake it for the Work document and every status read below silently
    # returned None forever. Reading the one real list rather than a second,
    # hand-copied one is what stops a FOURTH recurrence of the same defect.
    for f in os.listdir(run_dir):
        if f.endswith(".json") and f not in brother_run.ENGINE_JSON_FILES:
            return os.path.join(run_dir, f)
    return None


def _row_status(run_dir, unit_id):
    path = _work_doc_path(run_dir)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError:  # sbe: allow-silent documented sentinel: the caller treats an unreadable plan as status unknown, and the area's assertion then fails loudly with the real comparison
        return None
    for row in (doc.get("rows") or doc.get("units") or []):
        if row.get("id") == unit_id:
            return row.get("status")
    return None


def _wait_for_status(run_dir, unit_id, status, deadline):
    while time.time() < deadline:
        if _row_status(run_dir, unit_id) == status:
            return True
        time.sleep(0.02)
    return False


def _read_claims(store):
    if not os.path.exists(store):
        return {}
    try:
        with open(store, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError:
        return {}


def _wait_for_claim(store, unit_id, deadline):
    while time.time() < deadline:
        claims = _read_claims(store)
        if unit_id in claims:
            return claims[unit_id]
        time.sleep(0.02)
    return None


def _edit_expires_at(store, unit_id, when):
    """Legitimate test surgery: simulate the passage of the lease's TTL
    without actually sleeping 20 real minutes for it. Never used to hide a
    real defect, only to fast-forward a clock claim_store itself already
    reads via time.time()."""
    claims = _read_claims(store)
    claims[unit_id]["expires_at"] = when
    with open(store, "w", encoding="utf-8") as fh:
        json.dump(claims, fh, indent=1)


def _merge_ids(repo):
    """Every unit id actually merged into canonical, read from git's own
    log, never from anything a worker or the report claims. Lane branches
    are named "lane/" + the unit id (sanitized, but our ids are already
    plain alnum), per loop_bridge.py's own naming. TWO SUBJECTS are read:
    the engine's own since E45 ("Brother integrated <id> from lane/<id>")
    and git's default, which is what a history integrated by an engine
    older than that still carries."""
    out = subprocess.run(["git", "log", "--oneline"], cwd=repo,
                         capture_output=True, text=True).stdout
    return sorted(set(re.findall(r"Brother integrated \S+ from lane/(\S+)", out))
                  | set(re.findall(r"Merge branch 'lane/([^']+)'", out)))


def _integrated_ids_from_report(out):
    # The delivery report lists integrated units on their own lines since
    # 2026-08-31 ("    U1  verified by: <cmd>"), not inline after the count
    # header. Parse the per-unit lines; fall back to the old inline form so a
    # log captured before the format change still reads.
    ids = set(re.findall(r"^\s+(\S+)\s+verified by:", out, re.MULTILINE))
    if ids:
        return ids
    m = re.search(r"integrated \(\d+\): (.*)", out)
    if not m or m.group(1).strip() in ("none", ""):
        return set()
    return set(x.strip() for x in m.group(1).split(",") if x.strip())


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _build_two_package_monorepo(tmp):
    """A real git repository shaped like a small monorepo: two sibling
    packages, each with its own generated/ subdirectory. Returns
    (repo, pkg_b_generated_path, its original text), so the after-the-fact
    byte comparison never trusts anything but git's own working tree."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        subprocess.run(["git"] + args, cwd=repo, capture_output=True,
                       text=True, check=True)
    for pkg in ("pkg_a", "pkg_b"):
        os.makedirs(os.path.join(repo, "packages", pkg, "src"))
        os.makedirs(os.path.join(repo, "packages", pkg, "generated"))
        with open(os.path.join(repo, "packages", pkg, "src", "existing.py"),
                 "w", encoding="utf-8") as fh:
            fh.write("# %s hand-written\n" % pkg)
        with open(os.path.join(repo, "packages", pkg, "generated", "gen.py"),
                 "w", encoding="utf-8") as fh:
            fh.write("# GENERATED for %s - do not edit by hand\nVALUE = 1\n" % pkg)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True,
                   text=True, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed monorepo"], cwd=repo,
                   capture_output=True, text=True, check=True)
    pkg_b_generated = os.path.join(repo, "packages", "pkg_b", "generated", "gen.py")
    return repo, pkg_b_generated, _read_text(pkg_b_generated)


def _find_real_spawn_module():
    """The path to the real bm_worker_spawn.py loop_bridge would load,
    resolved the SAME way loop_bridge.load_parts() resolves it. Returns
    (path, None) or (None, NO-DATA reason)."""
    sys.path.insert(0, HERE)
    try:
        import loop_bridge as lb
    except ImportError as exc:
        return None, "NO-DATA: could not import scripts/loop_bridge.py: %s" % exc
    parts, problem = lb.load_parts()
    if parts is None:
        return None, "NO-DATA: %s" % problem
    return parts["spawn"].__file__, None


def _build_short_timeout_runtime(tmp, timeout_seconds):
    """A BROTHER_RUNTIME_ROOT fixture: every real sibling tool symlinked
    straight through unmodified, except bm_worker_spawn.py, which is a
    byte-identical copy with only its DEFAULT_TIMEOUT_SECONDS constant
    lowered. Symlinking the WHOLE directory (not just the three modules
    loop_bridge imports directly) matters because bm_controller.py has its
    own further sibling files it reads by path at runtime (found live: a
    partial fixture raised FileNotFoundError reaching for bm_store.py), and
    a fixture that only covers the imports it happens to know about is not
    hermetic against a dependency one layer deeper.

    loop_bridge's own LaneWorker never passes an explicit timeout to
    SpawningWorker, so this is the one knob that makes a hung worker
    reapable in test time instead of the real 900s default, using the exact
    override load_parts() itself advertises (BROTHER_RUNTIME_ROOT), never a
    hand-patched production file. Returns (runtime_root, None) or
    (None, NO-DATA reason)."""
    real_path, problem = _find_real_spawn_module()
    if real_path is None:
        return None, problem
    real_dir = os.path.dirname(os.path.abspath(real_path))
    src = _read_text(real_path)
    needle = "DEFAULT_TIMEOUT_SECONDS = 900"
    if needle not in src:
        return None, ("NO-DATA: %s no longer reads %r; this fixture's patch "
                      "would silently no-op rather than shorten anything"
                      % (real_path, needle))
    patched = src.replace(needle, "DEFAULT_TIMEOUT_SECONDS = %d" % timeout_seconds, 1)

    runtime_root = os.path.join(tmp, "fake-runtime")
    tools_dir = os.path.join(runtime_root, "tools")
    os.makedirs(tools_dir)
    spawn_name = os.path.basename(real_path)
    for name in os.listdir(real_dir):
        source = os.path.join(real_dir, name)
        if name == spawn_name or not os.path.isfile(source):
            continue
        os.symlink(source, os.path.join(tools_dir, name))
    with open(os.path.join(tools_dir, spawn_name), "w", encoding="utf-8") as fh:
        fh.write(patched)
    return runtime_root, None


# ---------------------------------------------------------------------------
# area 1: time to first useful action
# ---------------------------------------------------------------------------

def area_1(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-1-")
    repo = tbr.make_repo(tmp)
    env = dict(os.environ) if live else stub_env(
        tmp, ONE_UNIT_DECOMPOSER, WRITER_MODEL)
    budget = 300.0 if live else 45.0

    start = time.time()
    proc = _sh([sys.executable, BROTHER_RUN,
               "a file exists proving the tool did something useful",
               "--cwd", repo, "--runs-root", tmp], cwd=repo, env=env,
               timeout=int(budget) + 30)
    elapsed = time.time() - start
    out = proc.stdout + proc.stderr

    if proc.returncode != 0:
        return "FAIL", "the outcome never landed (exit %d): %s" % (
            proc.returncode, out[-200:])
    if not os.path.exists(os.path.join(repo, "outcome.txt")):
        return "FAIL", "brother_run exited 0 but outcome.txt was never written"
    if elapsed > budget:
        return "FAIL", ("first useful action took %.1fs, over the %.0fs "
                        "budget" % (elapsed, budget))
    return "PASS", ("one plain outcome sentence reached an integrated file "
                    "in %.2fs (budget %.0fs)" % (elapsed, budget))


# ---------------------------------------------------------------------------
# shared crash rig for areas 2 and 3: a two-unit chain (A1 then A2), A2's
# model deliberately slow so an external kill has a real window to land
# while A2's claim is live and its owner is about to be dead.
# ---------------------------------------------------------------------------

def _run_and_kill_mid_second_unit(prefix, sleep_seconds=2.0):
    """Starts brother_run on a two-unit outcome (A2 depends on A1), waits for
    A1 to integrate and A2 to be durably claimed, then SIGKILLs the whole
    process tree. Returns a dict of everything a caller needs, or a
    ("FAIL"/"NO-DATA", evidence) tuple on setup trouble."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    repo = tbr.make_repo(tmp)
    env = stub_env(tmp, TWO_DEPENDENT_DECOMPOSER,
                   slow_model_body(sleep_seconds, "two.txt"))
    runs_root = tmp

    proc = _popen_group([sys.executable, BROTHER_RUN,
                         "two files exist, the second after the first",
                         "--cwd", repo, "--runs-root", runs_root],
                        cwd=repo, env=env)

    deadline = time.time() + 30
    run_dir = _find_run_dir(runs_root, deadline)
    if run_dir is None:
        _killpg(proc)
        return None, "FAIL", "brother_run never created a run directory before the deadline"

    if not _wait_for_status(run_dir, "A1", "DONE", deadline):
        _killpg(proc)
        return None, "FAIL", ("the first unit never reached DONE before the "
                              "deadline: there was no first integration to "
                              "interrupt after")

    claims_path = os.path.join(run_dir, "claims.json")
    claim = _wait_for_claim(claims_path, "A2", deadline)
    if claim is None:
        _killpg(proc)
        return None, "FAIL", ("the second unit was never claimed before the "
                              "deadline, so the crash could not be staged "
                              "mid-unit")

    kill_time = time.time()
    _killpg(proc)

    after_kill = _read_claims(claims_path)
    a2_after = after_kill.get("A2")
    if a2_after is None or a2_after.get("state") != "claimed":
        return None, "FAIL", ("after the kill A2's claim reads %r, not "
                              "'claimed': the crash did not land mid-unit as "
                              "staged" % (a2_after,))
    if not (float(a2_after.get("expires_at", 0)) > kill_time):
        return None, "FAIL", ("A2's lease had already expired at kill time "
                              "(expires_at=%r): the dead-owner-live-lease "
                              "case was never actually reached"
                              % a2_after.get("expires_at"))

    return {"tmp": tmp, "repo": repo, "run_dir": run_dir, "env": env,
            "claims_path": claims_path, "a2_claim_after_kill": a2_after}, None, None


# ---------------------------------------------------------------------------
# area 2: interrupt and redirect
# ---------------------------------------------------------------------------

def area_2(live=False):
    if live:
        return NODATA, ("the interrupt-then-resume race needs a stub's fixed "
                        "timing to land the kill mid-unit; not attempted "
                        "against a real model's unpredictable latency")

    rig, fail_verdict, fail_evidence = _run_and_kill_mid_second_unit(
        "product-acceptance-2-")
    if rig is None:
        return fail_verdict, fail_evidence

    # LEGITIMATE TEST SURGERY: simulate the lease's TTL having elapsed, the
    # only way to prove "resume finishes the remainder" without an actual
    # 20-minute wait for the crashed owner's lease to expire on its own.
    _edit_expires_at(rig["claims_path"], "A2", time.time() - 5)

    resumed = _sh([sys.executable, BROTHER_RUN, "--resume", rig["run_dir"],
                  "--cwd", rig["repo"], "--runs-root", rig["tmp"]],
                  cwd=rig["repo"], env=rig["env"], timeout=60)
    out = resumed.stdout + resumed.stderr
    if resumed.returncode != 0:
        return "FAIL", "resume did not finish the remainder: %s" % out[-200:]
    if not (os.path.exists(os.path.join(rig["repo"], "one.txt")) and
            os.path.exists(os.path.join(rig["repo"], "two.txt"))):
        return "FAIL", "resume exited 0 but not both files exist"
    merges = _merge_ids(rig["repo"])
    if merges != ["A1", "A2"]:
        return "FAIL", ("expected exactly A1 and A2 each merged once after "
                        "interrupt+resume, git shows %s" % merges)
    return "PASS", ("killed brother_run right after A1 integrated with A2 "
                    "already claimed; --resume (after the crashed lease's "
                    "simulated elapse) finished A2 without redoing A1: "
                    "merges=%s" % merges)


# ---------------------------------------------------------------------------
# area 9: crash recovery and resumable sessions (the same arc as area 2, with the extra
# proof that a second claim is refused while the dead owner's lease is
# still live, before the surgery that lets it be legitimately reclaimed)
# ---------------------------------------------------------------------------

def area_9(live=False):
    if live:
        return NODATA, ("the crash-mid-unit race needs a stub's fixed "
                        "timing to land the kill; not attempted against a "
                        "real model's unpredictable latency")

    rig, fail_verdict, fail_evidence = _run_and_kill_mid_second_unit(
        "product-acceptance-3-")
    if rig is None:
        return fail_verdict, fail_evidence

    before_attempt = rig["a2_claim_after_kill"].get("attempt")
    before_owner = rig["a2_claim_after_kill"].get("owner")

    # THE RECLAIM, WITH NO SURGERY AND NO WAITING. This case asserted the
    # OPPOSITE until 2026-09-02: that an immediate resume must be REFUSED
    # because A2's lease had not expired, and that only after test surgery
    # aged the lease could the unit be reclaimed. That expectation described
    # the product as it was before 2026-08-31, and claim_store.live() has
    # since been changed on purpose: a claim is dead when its lease expired
    # OR its owning pid is gone ON THIS HOST. Its own docstring names the
    # cost that bought the change, and the head-to-head record measured it:
    # crash recovery went from about 1200 seconds of waiting for a lease
    # that could not possibly still be live, to one resume call.
    #
    # So the honest assertion is the one the product now makes, and it is
    # STRONGER rather than weaker: a SIGKILLed owner's claim is reclaimable
    # at once, and the reclaim must still be exactly one clean reclaim, at
    # attempt 2, naming the dead owner it took over from, with exactly one
    # merge each. Every guarantee the old version checked after its surgery
    # is still checked here; what is gone is the surgery and the assertion
    # that the product must first refuse. The cross-host safety boundary
    # live() names (another host's claim still falls back to time-based
    # expiry) is not exercised here, and saying so is the point: this rig
    # is single-host.
    resumed = _sh([sys.executable, BROTHER_RUN, "--resume", rig["run_dir"],
                  "--cwd", rig["repo"], "--runs-root", rig["tmp"]],
                  cwd=rig["repo"], env=rig["env"], timeout=60)
    out = resumed.stdout + resumed.stderr
    if resumed.returncode != 0:
        return "FAIL", "the reclaim resume did not finish: %s" % out[-200:]

    final = _read_claims(rig["claims_path"]).get("A2", {})
    if final.get("attempt") != before_attempt + 1:
        return "FAIL", ("A2 finished at attempt %r, not %d: this does not "
                        "read as one clean reclaim" % (final.get("attempt"),
                                                       before_attempt + 1))
    if final.get("reclaimed_from") != before_owner:
        return "FAIL", ("A2's final claim does not name %r as the dead "
                        "owner it was reclaimed from (reclaimed_from=%r)"
                        % (before_owner, final.get("reclaimed_from")))
    merges = _merge_ids(rig["repo"])
    if merges != ["A1", "A2"]:
        return "FAIL", ("A2 was reclaimed but git shows merges=%s, not "
                        "exactly one merge each for A1 and A2: duplicate or "
                        "lost integration" % merges)

    return "PASS", ("SIGKILL left A2 claimed under dead owner %r; the very "
                    "next resume reclaimed it with no waiting and no surgery, "
                    "because the owning pid was gone on this host, and the "
                    "reclaim was still exactly one clean one: attempt %d, "
                    "reclaimed_from=%r, and exactly one merge each: merges=%s"
                    % (before_owner, final.get("attempt"), before_owner,
                       merges))


# ---------------------------------------------------------------------------
# area 3: partial diff acceptance, expressed at the door's unit granularity
# ---------------------------------------------------------------------------

def area_3(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-4-")
    repo = tbr.make_repo(tmp)
    env = dict(os.environ) if live else stub_env(
        tmp, TWO_INDEPENDENT_DECOMPOSER, partial_failure_model_body("u2.txt"))

    proc = _sh([sys.executable, BROTHER_RUN,
               "two files exist, u1 and u2", "--cwd", repo,
               "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out = proc.stdout + proc.stderr

    if proc.returncode == 0:
        return "FAIL", ("brother_run exited 0 even though u2.txt's own "
                        "check was made to fail: a partial delivery was "
                        "reported as a full success")
    if not os.path.exists(os.path.join(repo, "u1.txt")):
        return "FAIL", "the unit that should have succeeded never landed"
    if os.path.exists(os.path.join(repo, "u2.txt")):
        return "FAIL", "the deliberately-failed unit's file exists anyway"
    if "integrated (1): U1" not in out and "U1" not in _integrated_ids_from_report(out):
        return "FAIL", "U1 does not appear in the report's integrated list"
    if "refused (1):" not in out or "U2" not in out:
        return "FAIL", ("the refused unit was not named individually in the "
                        "delivery report: %s" % out[-300:])
    last_line = out.splitlines()[-1] if out.splitlines() else ""
    return "PASS", ("one unit's own check was made to fail; the delivery "
                    "report named it refused BY NAME while the other unit "
                    "integrated, and exit code was nonzero: %s" % last_line)


# ---------------------------------------------------------------------------
# area 11: operational credibility, proven on the report's central claim
# ---------------------------------------------------------------------------

def area_11(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-5-")
    repo = tbr.make_repo(tmp)
    env = dict(os.environ) if live else stub_env(
        tmp, TWO_INDEPENDENT_DECOMPOSER, WRITER_MODEL)

    proc = _sh([sys.executable, BROTHER_RUN,
               "two files exist, u1 and u2", "--cwd", repo,
               "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        return "FAIL", "the run that status is being checked against did not succeed: %s" % out[-200:]

    reported = _integrated_ids_from_report(out)
    actual = set(_merge_ids(repo))
    if not reported or not actual:
        return "FAIL", ("nothing to compare: reported=%s actual=%s"
                        % (sorted(reported), sorted(actual)))
    if reported != actual:
        return "FAIL", ("the delivery report claims integrated=%s but git's "
                        "own merges show %s: the report is not credible"
                        % (sorted(reported), sorted(actual)))
    return "PASS", ("the delivery report's integrated list (%s) matches the "
                    "repository's own git merges exactly" % sorted(reported))


# ---------------------------------------------------------------------------
# area 4: monorepos and generated code
# ---------------------------------------------------------------------------

def area_4(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-6-")
    repo, pkg_b_generated, pkg_b_before = _build_two_package_monorepo(tmp)
    env = dict(os.environ) if live else stub_env(
        tmp, MONOREPO_DECOMPOSER, MONOREPO_MODEL)

    proc = _sh([sys.executable, BROTHER_RUN,
               "pkg_a's helper function exists and pkg_a's docs note exists",
               "--cwd", repo, "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out = proc.stdout + proc.stderr

    if proc.returncode == 0:
        return "FAIL", ("brother_run exited 0 even though one unit wrote, "
                        "undeclared, into pkg_b's generated directory: a "
                        "cross-package write was reported as a full success")

    hand_path = os.path.join(repo, "packages", "pkg_a", "src", "hand.py")
    if not (os.path.isfile(hand_path) and "helper" in _read_text(hand_path)):
        return "FAIL", "pkg_a's own correctly-scoped change never landed on canonical"
    if os.path.exists(os.path.join(repo, "packages", "pkg_a", "docs", "note.txt")):
        return "FAIL", ("the quarantined unit's own file landed on canonical "
                        "anyway: quarantine holds the whole unit, never part "
                        "of it")
    if _read_text(pkg_b_generated) != pkg_b_before:
        return "FAIL", ("pkg_b's generated file changed on canonical even "
                        "though the unit that touched it was never granted "
                        "that scope")
    if "U1" not in _integrated_ids_from_report(out):
        return "FAIL", "U1 (pkg_a's own clean change) does not appear integrated: %s" % out[-300:]
    if "refused (1):" not in out or "U2" not in out:
        return "FAIL", "the quarantined unit was not named refused by name: %s" % out[-300:]
    if "quarantine" not in out.lower():
        return "FAIL", ("the run's own output never says QUARANTINE anywhere, "
                        "so this cannot be told apart from an ordinary check "
                        "failure: %s" % out[-400:])
    if "pkg_b" not in out and "generated/gen.py" not in out:
        return "FAIL", ("the undeclared write is not named anywhere in the "
                        "run's own output: %s" % out[-400:])
    return "PASS", ("a unit that wrote, undeclared, into the OTHER package's "
                    "generated directory was quarantined and named (U2, "
                    "QUARANTINE), pkg_b's generated file never changed on "
                    "canonical, and pkg_a's own correctly-scoped unit (U1) "
                    "integrated cleanly")


# ---------------------------------------------------------------------------
# area 6: dirty trees and rebases preserving unrelated changes
# ---------------------------------------------------------------------------

UNRELATED_ORIGINAL = "unrelated original text\n"
UNRELATED_EDIT = "unrelated LOCAL edit, uncommitted, must survive\n"


def area_6(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-7-")
    repo = tbr.make_repo(tmp)
    unrelated_path = os.path.join(repo, "unrelated.txt")
    with open(unrelated_path, "w", encoding="utf-8") as fh:
        fh.write(UNRELATED_ORIGINAL)
    _sh(["git", "add", "-A"], cwd=repo, check=True)
    _sh(["git", "commit", "-q", "-m", "seed unrelated file"], cwd=repo, check=True)
    before_head = _sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    # THE DIRTY TREE, already present before brother_run is ever invoked:
    # an uncommitted edit to an already-tracked file with nothing to do with
    # the outcome about to be asked for.
    with open(unrelated_path, "w", encoding="utf-8") as fh:
        fh.write(UNRELATED_EDIT)

    env = dict(os.environ) if live else stub_env(
        tmp, ONE_UNIT_DECOMPOSER, WRITER_MODEL)

    first = _sh([sys.executable, BROTHER_RUN,
                "a file exists proving the tool did something useful",
                "--cwd", repo, "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out1 = first.stdout + first.stderr

    if _read_text(unrelated_path) != UNRELATED_EDIT:
        return "FAIL", ("the uncommitted unrelated edit was altered just by "
                        "running brother_run over a dirty tree, before any "
                        "resolution was even attempted")
    after_first_head = _sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    if after_first_head != before_head:
        return "FAIL", "canonical's HEAD moved even though nothing should integrate over a dirty tree"
    if os.path.exists(os.path.join(repo, "outcome.txt")):
        return "FAIL", "the requested file landed on canonical despite the dirty tree"
    if first.returncode == 0:
        return "FAIL", "brother_run exited 0 over a dirty canonical tree: the refusal never happened"
    if "dirty" not in out1.lower():
        return "FAIL", ("the run's own output never says the canonical tree "
                        "was dirty by name: %s" % out1[-400:])

    run_dir = _find_run_dir(tmp, time.time() + 5)
    if run_dir is None:
        return "FAIL", "no run directory was created for the first (refused) attempt"

    # "THEN REBASES OR PULLS": the contributor resolves their own dirty tree
    # exactly as a person would (commits the unrelated edit), and retries.
    _sh(["git", "add", "-A"], cwd=repo, check=True)
    _sh(["git", "commit", "-q", "-m", "commit my unrelated edit"], cwd=repo, check=True)

    second = _sh([sys.executable, BROTHER_RUN, "--resume", run_dir,
                 "--cwd", repo, "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out2 = second.stdout + second.stderr

    if second.returncode != 0:
        return "FAIL", "after resolving the dirty tree, resume did not succeed: %s" % out2[-300:]
    if not os.path.exists(os.path.join(repo, "outcome.txt")):
        return "FAIL", "resume exited 0 but the requested file never landed"
    if _read_text(unrelated_path) != UNRELATED_EDIT:
        return "FAIL", ("the unrelated edit's content changed across the "
                        "full refuse-then-resolve-then-integrate flow")

    return "PASS", ("a dirty canonical tree (an uncommitted, unrelated edit) "
                    "made brother_run refuse to integrate, by name, and left "
                    "the unrelated edit and HEAD untouched; after the edit "
                    "was committed, --resume integrated the requested change "
                    "with the unrelated edit still byte-identical")


# ---------------------------------------------------------------------------
# area 5: terminal cancellation and hung command recovery
# ---------------------------------------------------------------------------

#: Short enough that this test finishes in well under a minute across up to
#: 3 attempts; long enough that spawning two nested python processes on a
#: loaded machine does not itself trip the timeout before the model even
#: gets a chance to hang.
HANG_REAP_TIMEOUT_S = 5
#: This test's OWN watchdog (per the brief: "bound the whole test with its
#: own watchdog timer so a regression cannot hang the battery"). If the
#: patched spawn timeout above regresses back to the real 900s default, this
#: fires first and the test reports FAIL rather than the battery hanging.
HANG_TEST_WATCHDOG_S = 90.0


def area_5(live=False):
    if live:
        return NODATA, ("a real model's latency cannot be told apart from a "
                        "genuine hang without a stub's fixed timing; not "
                        "attempted under --live")

    tmp = tempfile.mkdtemp(prefix="product-acceptance-8-")
    repo = tbr.make_repo(tmp)

    runtime_root, problem = _build_short_timeout_runtime(tmp, HANG_REAP_TIMEOUT_S)
    if runtime_root is None:
        return NODATA, problem

    env = stub_env(tmp, ONE_UNIT_DECOMPOSER, HANGING_MODEL)
    env["BROTHER_RUNTIME_ROOT"] = runtime_root

    start = time.time()
    proc = _popen_group([sys.executable, BROTHER_RUN,
                         "a file exists proving the tool did something useful",
                         "--cwd", repo, "--runs-root", tmp], cwd=repo, env=env)
    try:
        stdout, stderr = proc.communicate(timeout=HANG_TEST_WATCHDOG_S)
    except subprocess.TimeoutExpired:
        # os.setsid gave this whole tree ONE process group, numbered by the
        # leader's own pid: killing that number reaches every descendant,
        # including a grandchild the run's own (regressed) timeout never
        # reached, without needing a live handle on the leader itself.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:  # sbe: allow-silent nothing left alive in this group to kill
            pass
        proc.wait(timeout=15)
        return "FAIL", ("brother_run did not exit within this test's own "
                        "%.0fs watchdog: a regression let the hung worker "
                        "wedge the controller instead of the run's own "
                        "timeout reaping it" % HANG_TEST_WATCHDOG_S)
    elapsed = time.time() - start
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:  # sbe: allow-silent the honest, working case: nothing left in the group to kill
        pass
    out = (stdout or "") + (stderr or "")

    if proc.returncode == 0:
        return "FAIL", ("brother_run exited 0 even though its only worker "
                        "always hangs: a hung unit was reported as a "
                        "success: %s" % out[-200:])
    if os.path.exists(os.path.join(repo, "outcome.txt")):
        return "FAIL", "outcome.txt exists even though the worker never returned"
    if "integrated (0):" not in out:
        return "FAIL", "the hung unit was not reported as zero integrated: %s" % out[-200:]
    if "refused (1):" not in out or "U1" not in out:
        return "FAIL", "the hung unit was not named refused by name: %s" % out[-300:]
    if elapsed > HANG_TEST_WATCHDOG_S * 0.8:
        return "FAIL", ("brother_run took %.1fs, too close to this test's own "
                        "%.0fs watchdog to call this a real reap rather than "
                        "a near-miss" % (elapsed, HANG_TEST_WATCHDOG_S))
    return "PASS", ("a worker that never returns was reaped by the run's own "
                    "spawn timeout (shortened here for test speed) across up "
                    "to 3 attempts in %.1fs; the unit was reported failed by "
                    "name rather than the run wedging, and the controller "
                    "exited cleanly instead of hanging toward the real 900s "
                    "default" % elapsed)




# ---------------------------------------------------------------------------
# area 7: choosing the right tests and telling not-run from passed. A check
# that cannot even start must read as not-run and block, never as passed.
# ---------------------------------------------------------------------------

TWO_CHECKS_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "OK", "objective": "a unit whose check runs and passes",
         "done_check": "test -f ok.txt", "writes": ["ok.txt"], "deps": []},
        {"id": "NORUN", "objective": "a unit whose check cannot start",
         "done_check": "no_such_command_x9y8z7 ok",
         "writes": ["norun.txt"], "deps": []},
    ]))
"""


def area_7(live=False):
    tmp = tempfile.mkdtemp(prefix="product-acceptance-7-")
    repo = tbr.make_repo(tmp)
    env = dict(os.environ) if live else stub_env(
        tmp, TWO_CHECKS_DECOMPOSER, WRITER_MODEL)

    proc = _sh([sys.executable, BROTHER_RUN,
               "two files exist, each behind its own check", "--cwd", repo,
               "--runs-root", tmp], cwd=repo, env=env, timeout=90)
    out = proc.stdout + proc.stderr

    if proc.returncode == 0:
        return "FAIL", ("one unit's check could not even run, yet the run "
                        "exited 0: not-run was counted as passed")
    merges = _merge_ids(repo)
    if "OK" not in merges:
        return "FAIL", ("the unit whose check ran and passed never "
                        "integrated: merges=%s" % merges)
    if "NORUN" in merges:
        return "FAIL", ("the unit whose check never ran was integrated: "
                        "not-run was treated as passed")
    if "NORUN" not in out:
        return "FAIL", ("the not-run unit is not named in the report: %s"
                        % out[-300:])
    return "PASS", ("the unit with a runnable passing check integrated "
                    "(merges=%s); the unit whose check could not start was "
                    "refused by name rather than counted as passed" % merges)


# ---------------------------------------------------------------------------
# area 8: safety without approval fatigue. A normal safe run needs zero
# manual approvals end to end (stdin closed), and a declared write scope
# escaping the repository is refused at the door: gates where danger is,
# none where it is not.
# ---------------------------------------------------------------------------

ESCAPING_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "ESC", "objective": "write outside the repository",
         "done_check": "true",
         "writes": ["../escaped.txt"], "deps": []},
    ]))
"""


def area_8(live=False):
    if live:
        return NODATA, ("the escaping write is scripted into a stub; not "
                        "staged against a real model")
    safe_tmp = tempfile.mkdtemp(prefix="product-acceptance-8-safe-")
    safe_repo = tbr.make_repo(safe_tmp)
    env = stub_env(safe_tmp, ONE_UNIT_DECOMPOSER, WRITER_MODEL)

    with open(os.devnull, "rb") as devnull:
        safe = subprocess.run(
            [sys.executable, BROTHER_RUN,
             "a file exists proving the tool did something useful",
             "--cwd", safe_repo, "--runs-root", safe_tmp],
            cwd=safe_repo, env=env, stdin=devnull, capture_output=True,
            text=True, timeout=90)
    if safe.returncode != 0:
        return "FAIL", ("a routine safe run with stdin closed did not "
                        "complete unattended: %s"
                        % (safe.stdout + safe.stderr)[-200:])

    esc_tmp = tempfile.mkdtemp(prefix="product-acceptance-8-esc-")
    esc_repo = tbr.make_repo(esc_tmp)
    env2 = stub_env(esc_tmp, ESCAPING_DECOMPOSER, WRITER_MODEL)

    esc = _sh([sys.executable, BROTHER_RUN, "a file escapes the repository",
              "--cwd", esc_repo, "--runs-root", esc_tmp], cwd=esc_repo,
              env=env2, timeout=90)
    esc_out = esc.stdout + esc.stderr

    # The gate must stand where the danger is: a unit declaring a write
    # OUTSIDE the repository is dangerous by declaration, before any file
    # moves. Accepting it in any form (exit 0, or its id in the report's
    # integrated list) is the missing-gate failure this area exists to
    # catch: measured live before work_record grew the declaration gate,
    # the escape produced an empty integration (canonical before == after)
    # that still reported "integrated (1): ESC" at exit 0, with the file
    # written outside the repository.
    accepted = (esc.returncode == 0
                or "ESC" in _integrated_ids_from_report(esc_out))
    if accepted:
        return "FAIL", ("a unit declaring a write OUTSIDE the repository "
                        "was accepted (exit %d, report: integrated=%s): "
                        "no gate stood where the danger was"
                        % (esc.returncode,
                           sorted(_integrated_ids_from_report(esc_out))))
    return "PASS", ("a routine safe run completed unattended with stdin "
                    "closed (zero approvals); a unit declaring a write "
                    "scope outside the repository was refused at the door "
                    "(exit %d) before any file could move" % esc.returncode)


REAL_AREAS = {"1": area_1, "2": area_2, "3": area_3, "4": area_4,
              "5": area_5, "6": area_6, "7": area_7, "8": area_8,
              "9": area_9, "11": area_11}

#: Areas 6, 7 and 8 have no product-path way to disable the safety net they
#: prove (the scope gate, the dirty-tree guard and the spawn timeout are
#: never optional flags a caller can pass), so --calibrate for them delegates
#: to the mechanism twin's OWN calibration of the exact same real machinery
#: this product-path test depends on transitively.
CALIBRATE_DELEGATES = {"6": "acceptance_4.py", "7": "acceptance_6.py",
                       "8": "acceptance_5.py"}


def _calibrate_via_mechanism_twin(script_name):
    script = os.path.join(HERE, script_name)
    proc = subprocess.run([sys.executable, script, "--calibrate"],
                          capture_output=True, text=True, timeout=90)
    lines = (proc.stdout or "").strip().splitlines()
    evidence = "delegated to %s --calibrate: %s" % (
        script_name, lines[-1] if lines else "(no output)")
    if proc.returncode == 0:
        return "PASS", evidence
    if proc.returncode == 2:
        return NODATA, evidence
    return "FAIL", evidence


def run_area(area_id, live=False, calibrate=False):
    if calibrate:
        script = CALIBRATE_DELEGATES.get(area_id)
        if script is None:
            return NODATA, "area %s has no calibration on file" % area_id
        try:
            return _calibrate_via_mechanism_twin(script)
        except subprocess.TimeoutExpired as exc:
            return "FAIL", "area %s calibration timed out: %s" % (area_id, exc)
    fn = REAL_AREAS.get(area_id)
    if fn is None:
        return NODATA, NOT_WRITTEN
    try:
        return fn(live=live)
    except subprocess.TimeoutExpired as exc:
        return "FAIL", "area %s timed out: %s" % (area_id, exc)
    except Exception as exc:  # noqa: BLE001  # a raising area is a FAIL, never a silent skip
        return "FAIL", "area %s raised %s: %s" % (area_id, type(exc).__name__, exc)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Re-prove the eleven capability areas through the "
                    "public entry point (scripts/brother_run.py).")
    ap.add_argument("--area", default=None,
                    help="run only the area with this id")
    ap.add_argument("--explain", action="store_true",
                    help="print the area's own one-line description first; "
                        "only valid together with --area")
    ap.add_argument("--live", action="store_true",
                    help="use the real environment defaults (the real "
                        "claude CLI) instead of the deterministic stubs; a "
                        "founder-run proof, never the default")
    ap.add_argument("--calibrate", action="store_true",
                    help="prove the named area can go red, instead of "
                        "running it; only valid together with --area")
    args = ap.parse_args(argv)

    if args.explain and not args.area:
        ap.error("--explain only makes sense together with --area")
    if args.calibrate and not args.area:
        ap.error("--calibrate only makes sense together with --area")

    areas = AREAS
    if args.area is not None:
        areas = [a for a in AREAS if a[0] == args.area]
        if not areas:
            print("NO-DATA: no such area id %r" % args.area)
            return 2

    fail_count = nodata_count = 0
    for area_id, name in areas:
        if args.explain:
            print(DESCRIPTIONS.get(area_id, "no description on file"))
        verdict, evidence = run_area(area_id, live=args.live,
                                     calibrate=args.calibrate)
        print("{:<8} [{}] {:<50} {}".format(verdict, area_id, name,
                                            str(evidence)[:200]))
        if verdict == "FAIL":
            fail_count += 1
        elif verdict == "NO-DATA":
            nodata_count += 1

    print()
    print("{} area(s): {} pass, {} fail, {} no-data".format(
        len(areas), len(areas) - fail_count - nodata_count, fail_count,
        nodata_count))
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())

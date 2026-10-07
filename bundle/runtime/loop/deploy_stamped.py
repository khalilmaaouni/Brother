#!/usr/bin/env python3
"""Deploy frozen loop tools with a verified backup, staged canary and parity rollback.

All copying finishes before publication. A symlink target switches in one
rename. A plain directory needs a first migration (two renames while stopped);
a crash between them leaves no target, and restore recreates it from the snapshot.
The sibling config files are individually renamed; an exception or failed
parity restores the snapshot. This is not a power-loss transaction spanning
four paths: the printed restore command is the recovery route after a crash.
"""
import fcntl
import hashlib
import json
import os
import pwd
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tool_stamp  # noqa: E402, the canary runner beside this deploy tool
import freeze_manifest  # noqa: E402, its resolver decides the candidate, so the freeze reads what the stage chose

CONFIGS = ("model-registry.json", "loop-roles.json", "loop-canary.json")
STAMP = ".deploy-stamp.json"


def config_dir(source, target):
    """Directory the deploy reads CONFIGS from: <source>/docs/plan by default, exactly as before. A test-only
    seam, BROTHER_DEPLOY_CONFIG_DIR, overrides it when set, so a hermetic test can point a deploy at a fixture
    folder instead. A config genuinely missing from the chosen directory still refuses the deploy either way;
    this never adds a fallback of its own. The seam REFUSES the live bin (the real user's ~/.claude/bin, found
    from the password database, never from HOME): a variable left set in a shell must never install fixture
    configs, with placeholder models and prices, into the deployment the loop runs from."""
    override = os.environ.get("BROTHER_DEPLOY_CONFIG_DIR")
    if not override:
        return source / "docs/plan"
    live = Path(os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude", "bin")))
    if Path(os.path.realpath(str(target))) == live:
        raise RuntimeError("BROTHER_DEPLOY_CONFIG_DIR is a test seam and refuses the live bin %s" % live)
    return Path(override)
# The closed list of code the loop runs by path from outside bin (U3 item 1, B5-04): the pass evals, launches
# and closes with these scripts, and the paid fan out and the learning report run these plugin modules.
CANDIDATE_ENTRIES = (
    "scripts/adaptive_sizing.py", "scripts/probe_round.py", "scripts/diag_round.py", "scripts/worktree_sentry.py",
    "scripts/close_unit.py", "scripts/jev_decide.py", "scripts/failure_ledger.py",
    "plugin/runtime/brother/core/or_fanout.py", "plugin/runtime/brother/core/dream_report.py",
    # the lander's D13 gate order runs these from the code root in a boxed child (land_batch.GATE_ORDER_CHILD, review 17):
    # a string the import closure cannot read, so they are named here or the proof's order is always the fixed one
    "plugin/runtime/brother/core/dream_gate.py", "plugin/runtime/brother/core/dream_gate_policy.py")
CANDIDATE_OPTIONAL = ("duckdb=rollups",)
CANDIDATE_WORK_INPUTS = ("scripts/loop/land_apply.py:main=build-receipt-covers-new-modules",)


def candidate_work_inputs(candidate):
    """The work-input declarations, relative to the bin holding CANDIDATE, for every CANDIDATE_WORK_INPUTS file the
    stage copied into it: a freeze over that bin passes each with --work-input. None staged, none declared."""
    return ["candidate/" + w for w in CANDIDATE_WORK_INPUTS if (Path(candidate) / w.partition(":")[0]).is_file()]
#: DATA THE STAGED TOOLS READ BESIDE THEMSELVES (review 15, 2026-10-03): the import closure stages Python only, so the
#: candidate carried every grader module and not the sandbox profile grade_build reads from its own directory
#: (SANDBOX_PROFILE); the closer's done check under a proof's code root then refused NO-DATA ("sandbox profile is
#: missing") on every unit. The bin deploy copies the whole of scripts/loop and never saw this. A declared data file is
#: copied when the source holds it and listed among the staged paths; a source without it stages the tools alone.
CANDIDATE_DATA = ("scripts/loop/sandbox.sb",)


def hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def write_json(path, value):
    with path.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())


def quiet():
    env = dict(os.environ)
    env.pop("STOP_LOOP_ONLY", None)  # A test scope must never hide a real runner.
    result = subprocess.run(["bash", str(Path(HERE) / "stop_loop.sh"), "--dry"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=30)
    print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        raise RuntimeError("loop alive or stop check failed; deploy/restore refused")


def snapshot(target, run):
    backup = run / "rollback"
    saved = backup / "files"
    saved.mkdir(parents=True)
    # Dereference file links so recovery owns the bytes, not a mutable reference.
    shutil.copytree(str(target), str(saved / "bin"))
    for name in CONFIGS:
        p = target.parent / name
        if p.exists():
            shutil.copy2(str(p), str(saved / name))
    manifest = {"schema": 1, "target": str(target),
                "kind": "symlink" if target.is_symlink() else "directory",
                "files": hashes(saved)}
    write_json(backup / "manifest.json", manifest)
    return backup


def verify_backup(backup, target):
    manifest = json.loads((backup / "manifest.json").read_text())
    if (manifest.get("schema") != 1 or manifest.get("target") != str(target)
            or manifest.get("kind") not in ("symlink", "directory")):
        raise RuntimeError("rollback manifest does not describe this target")
    if hashes(backup / "files") != manifest.get("files"):
        raise RuntimeError("rollback hash mismatch; refusing before any change")
    if not (backup / "files/bin").is_dir():
        raise RuntimeError("rollback bin snapshot is missing")
    return manifest


def prepare_switch(target, payload, configs, work):
    """Prepare undo bytes for every rename before changing any destination."""
    entries = []
    for name in CONFIGS:
        dst = target.parent / name
        old = work / ("old-" + name)
        if dst.is_symlink():
            old.symlink_to(os.readlink(str(dst)))
        elif dst.exists():
            shutil.copy2(str(dst), str(old))
        entries.append((dst, configs.get(name), old))
    old = work / "old-bin"
    if target.is_symlink():
        old.symlink_to(os.readlink(str(target)))
    entries.append((target, payload, old))
    return entries


def switch(target, payload, configs, work):
    """Rename prepared paths, undoing every completed rename if one fails."""
    entries = prepare_switch(target, payload, configs, work)
    touched = []
    try:
        for dst, new, old in entries:
            parked = False
            if dst.is_dir() and not dst.is_symlink():
                os.replace(str(dst), str(old))
                touched.append((dst, old))
                parked = True
            elif new is not None and new.is_dir() and not new.is_symlink():
                # A directory cannot rename over a symlink when restoring the
                # original plain target. The loop is frozen for this migration.
                if os.path.lexists(str(dst)):
                    dst.unlink()
                    touched.append((dst, old))
                    parked = True
            if new is None:
                if os.path.lexists(str(dst)):
                    dst.unlink()
            else:
                os.replace(str(new), str(dst))
            if not parked:
                touched.append((dst, old))
    except OSError:
        for dst, old in reversed(touched):
            if os.path.lexists(str(dst)):
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(str(dst))
                else:
                    dst.unlink()
            if os.path.lexists(str(old)):
                os.replace(str(old), str(dst))
        raise


def restore(backup, target):
    manifest = verify_backup(backup, target)
    work = Path(tempfile.mkdtemp(prefix="restore-", dir=str(backup.parent)))
    restored = work / "bin"
    shutil.copytree(str(backup / "files/bin"), str(restored))
    configs = {}
    for name in CONFIGS:
        if (backup / "files" / name).exists():
            shutil.copy2(str(backup / "files" / name), str(work / name))
            configs[name] = work / name
    payload = restored
    if manifest["kind"] == "symlink":
        payload = work / "next-bin"
        payload.symlink_to(restored)
    quiet()
    switch(target, payload, configs, work)
    print("PASS: restored verified rollback %s" % backup)


def stage_candidate(source_dir, dest):
    """Copy into DEST (which must not exist) the code the loop runs from outside bin: every CANDIDATE_ENTRIES
    file plus every file of SOURCE_DIR reached from an entry or from a non test scripts/loop tool, resolved by
    freeze_manifest.closure against SOURCE_DIR, its scripts and scripts/loop directories and the hooks root of
    the pinned HOME. Refuses, naming the file and staging nothing, when an entry is missing, when anything in the
    closure refuses the freeze's static reading. A declared work input that is reached (native_worker imports
    land_apply for the build seat's fuzz, 2026-10-05) is staged and runs from the candidate; the freeze over the bin
    then needs its relocated declaration, candidate_work_inputs(), which proof_pair.sh passes, and refuses the
    relocated copy's computed import without it. Returns the staged paths, relative to DEST."""
    source = Path(source_dir).absolute()
    loop = source / "scripts/loop"
    missing = [rel for rel in CANDIDATE_ENTRIES if not (source / rel).is_file()]
    if missing:
        raise freeze_manifest.NoData("candidate entry missing from %s: %s" % (source, ", ".join(missing)))
    tools = [(str(loop / n), n[:-3]) for n in sorted(os.listdir(str(loop)))
             if n.endswith(".py") and not tool_stamp._skip(n) and (loop / n).is_file()]
    seeds = tools + [(str(source / rel), rel[:-3].replace("/", ".")) for rel in CANDIDATE_ENTRIES]
    work = [w for w in CANDIDATE_WORK_INPUTS if (source / w.partition(":")[0]).is_file()]
    home = freeze_manifest.home_for()
    files, imports, _, _, _, _, sites = freeze_manifest.closure(
        str(source), [str(source / "scripts"), str(loop), os.path.join(home, ".claude/hooks")],
        freeze_manifest._validate_optional(CANDIDATE_OPTIONAL), freeze_manifest._validate_work_inputs(work),
        home, seeds=seeds)
    reached = set(imports.values()) | {site["target"] for site in sites}
    only_run_from_bin = {path for path, _ in tools} - reached
    chosen = sorted(os.path.relpath(p, str(source)) for p in files
                    if p.startswith(str(source) + os.sep) and p not in only_run_from_bin)
    dest = Path(dest)
    dest.mkdir(parents=True)
    chosen += [rel for rel in CANDIDATE_DATA if (source / rel).is_file() and rel not in chosen]
    for rel in chosen:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source / rel), str(dest / rel))
    return chosen


def deploy(source, target):
    if not (source / "scripts/loop").is_dir():
        raise RuntimeError("source has no scripts/loop directory")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                       text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True))
    runs = target.parent / "brother-deploys"
    runs.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="deploy-", dir=str(runs)))
    backup = snapshot(target, run)
    verify_backup(backup, target)
    print("ROLLBACK %s" % backup, flush=True)
    command = ["env", "BROTHER_DEPLOY_TARGET=" + str(target),
               "BROTHER_DEPLOY_PYTHON=" + sys.executable,
               "bash", str(Path(HERE) / "deploy_stamped.sh"), "restore", str(backup)]
    print("RESTORE " + " ".join(shlex.quote(a) for a in command), flush=True)

    # This layout gives the staged canary its own docs/plan config, without
    # changing HOME or overwriting any installed config before it is proven.
    stage = run / "tree/scripts/loop"
    plan = run / "tree/docs/plan"
    stage.mkdir(parents=True)
    plan.mkdir(parents=True)
    for directory in (target, source / "scripts/loop"):
        # files and package directories alike, through the one copier every site shares (tool_stamp.copy_tools)
        tool_stamp.copy_tools(str(directory), str(stage), skip=(STAMP,))
    source_plan = config_dir(source, target)
    for name in CONFIGS:
        src = source_plan / name
        if name == "loop-canary.json" and not src.exists():
            src = target.parent / name  # The original script kept an existing optional spec.
        if src.exists() or name != "loop-canary.json":
            shutil.copy2(str(src), str(plan / name))

    # The candidate comes from the committed revision, never the working tree (B5-10): a revision that ships no
    # plugin runtime has no loop candidate, and a proof phase then refuses at model_router.code_root().
    if subprocess.run(["git", "-C", str(source), "cat-file", "-e", revision + ":plugin/runtime"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        tree = run / "source"
        tree.mkdir()
        archive = subprocess.run(["git", "-C", str(source), "archive", "--format=tar", revision, "--",
                                  "scripts", "plugin/runtime"], stdout=subprocess.PIPE, check=True)
        subprocess.run(["tar", "-x", "-f", "-", "-C", str(tree)], input=archive.stdout, check=True)
        try:
            staged = stage_candidate(str(tree), str(stage / "candidate"))
        finally:
            shutil.rmtree(str(tree))
        print("CANDIDATE %d files staged from %s" % (len(staged), revision), flush=True)
    else:
        print("CANDIDATE none: revision %s ships no plugin/runtime" % revision, flush=True)

    line, code = tool_stamp.prove(str(stage), cwd=str(source))
    print(line, flush=True)
    if code != 0:
        raise RuntimeError("staged canary refused; target unchanged")
    deployed = {"bin/" + name: sha for name, sha in hashes(stage).items()}
    deployed.update(hashes(plan))
    write_json(stage / STAMP, {"schema": 1, "revision": revision, "source_dirty": dirty,
                              "files": deployed, "rollback": str(backup)})
    work = run / "publish"
    work.mkdir()
    configs = {}
    for name in CONFIGS:
        if (plan / name).exists():
            shutil.copy2(str(plan / name), str(work / name))
            configs[name] = work / name
    payload = work / "next-bin"
    payload.symlink_to(stage)
    quiet()  # Check again after the canary, before publication.
    switch(target, payload, configs, work)
    try:
        env = dict(os.environ, BROTHER_DEPLOY_TARGET=str(target))
        result = subprocess.run([sys.executable, "-B", str(source / "scripts/test_loop_tool_parity.py")],
                                cwd=str(source), env=env, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError("parity refused after swap")
        actual = {"bin/" + n: s for n, s in hashes(target).items() if n != STAMP}
        actual.update({n: hashlib.sha256((target.parent / n).read_bytes()).hexdigest()
                       for n in configs})
        if actual != deployed:
            raise RuntimeError("deployed stamp hash mismatch")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        restore(backup, target)
        raise
    print("PASS: deployed %s; stamp %s" % (revision, target / STAMP))


def main():
    args = sys.argv[1:]
    if args not in ([], ["deploy"]) and not (len(args) == 2 and args[0] == "restore"):
        print("usage: deploy_stamped.sh [deploy | restore <rollback-directory>]", file=sys.stderr)
        return 2
    target = Path(os.path.abspath(os.path.expanduser(
        os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin"))))
    source = Path(os.environ.get("BROTHER_DEPLOY_SOURCE", str(Path(HERE).parents[1]))).expanduser().resolve()
    try:
        if os.path.lexists(str(target)) and not target.is_dir():
            raise RuntimeError("target exists but is not a directory or directory symlink")
        if not target.is_dir() and args[:1] != ["restore"]:
            # Only a verified snapshot may recreate an absent target: a crash between the plain
            # directory migration's two renames leaves none, and restore is the way back from it.
            raise RuntimeError("target must be an existing directory or directory symlink")
        with (target.parent / ".deploy-stamped.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            quiet()
            if args[:1] == ["restore"]:
                restore(Path(args[1]).resolve(), target)
            else:
                deploy(source, target)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("FAIL: deploy/restore refused: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

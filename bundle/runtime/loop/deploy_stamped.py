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
    "plugin/runtime/brother/core/dream_gate.py", "plugin/runtime/brother/core/dream_gate_policy.py",
    # land_apply starts the fuzz child by path (nothing imports it), and its one computed import is a declared work input
    "scripts/loop/land_fuzz_child.py",
    # A2.b: a deploy run from bin derives the next generation's kernel with THIS generation's closure tool (kernel_tool)
    "scripts/kernel_closure.py")
CANDIDATE_OPTIONAL = ("duckdb=rollups",)
CANDIDATE_WORK_INPUTS = ("scripts/loop/land_fuzz_child.py:main=build-receipt-covers-new-modules",)


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
    frozen_check()
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
    only_run_from_bin = {path for path, _ in tools} - reached - {str(source / rel) for rel in CANDIDATE_ENTRIES}   # a declared entry is staged even when nothing imports it
    chosen = sorted(os.path.relpath(p, str(source)) for p in files
                    if p.startswith(str(source) + os.sep) and p not in only_run_from_bin)
    dest = Path(dest)
    dest.mkdir(parents=True)
    chosen += [rel for rel in CANDIDATE_DATA if (source / rel).is_file() and rel not in chosen]
    for rel in chosen:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source / rel), str(dest / rel))
    return chosen


#: A2.b, THE STAMP CARRIES THE KERNEL AT N (COUNCIL D1, D2 as amended 2026-10-18). Every deploy copies the kernel of the
#: deployed revision (kernel_closure over that revision's archive, never the working tree) into run/tree/kernel, hashed
#: under the stamp's `kernel`. THE PENDING SET is the name match, kernel_closure.ROOTS (STABILIZE A2: it stands until
#: the owner rules on the derived closure, A2.md A2.a options A, B, C): a root whose bytes differ from the activated
#: generation's copy keeps N's bytes in run/tree/kernel, in bin and in the candidate, and is recorded under
#: `pending_kernel` {path: {deployed_sha256, landed_sha256, landed_at_revision}}; only `activate` moves it.
#: THE OVERLAY (`kernel_overlay`) is what a judge tree takes from run/tree/kernel: the pending set plus the files the
#: activated gate's rows execute (its shell rows, never their imports). A class 1 call of the session, 2026-10-19:
#: overlaying the whole closure would put N's copy of every module the tests import over the code under test, and the
#: gate would judge N, not N+1. Flip: the owner's ruling on the kernel's scope, or a measured row weakened through a
#: helper module it imports. FAIL DIRECTION: a previous kernel this deploy cannot read refuses the deploy.
def kernel_tool():
    """The path of this generation's kernel_closure.py: the checkout layout (scripts/kernel_closure.py beside
    scripts/loop) or the bin layout (candidate/scripts/kernel_closure.py). The next revision's own copy never decides
    its kernel. Raises RuntimeError when neither is there."""
    for path in (Path(HERE).parent / "kernel_closure.py", Path(HERE) / "candidate/scripts/kernel_closure.py"):
        if path.is_file():
            return str(path)
    raise RuntimeError("kernel_closure.py is not beside this deploy tool, so no kernel can be derived")


def derive_with(tool):
    """derive(dir) -> {"roots", "kernel", "rows"} by running `tool` (kernel_tool's path) isolated over dir: a
    subprocess, never an import by a computed path (the bin's freeze refuses that). Raises RuntimeError."""
    def derive(directory):
        r = subprocess.run([sys.executable, "-I", "-B", tool, "--tree", str(directory)], capture_output=True, text=True,
                           timeout=600)
        try:
            doc = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or not all(isinstance(doc.get(k), list) for k in ("roots", "kernel", "rows")):
            raise RuntimeError("the kernel of %s cannot be derived: %s" % (directory, (r.stdout + r.stderr).strip()[-300:]))
        return doc
    return derive


def read_stamp(target):
    """The deployed stamp as a dict, or None when the target carries none (a first deploy). An unreadable stamp
    raises RuntimeError: an unknown previous generation is never read as no generation."""
    path = Path(target) / STAMP
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("the deployed stamp %s cannot be read: %s" % (path, exc))
    if not isinstance(doc, dict):
        raise RuntimeError("the deployed stamp %s is not an object" % path)
    return doc


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def stage_kernel(tree, kernel_dir, previous, revision, derive, approved=False):
    """Copy the kernel of `tree` (an archive of `revision`) into kernel_dir and return (kernel, pending, overlay).
    derive: dir -> {"roots", "kernel", "rows"} (derive_with(kernel_tool()), this generation's tool).
    kernel: {path: sha256 of the bytes staged, or None for a member absent at the activated generation}.
    previous: the deployed stamp (read_stamp) or None. A previous stamp with no `kernel` is the bootstrap: everything
    activates. Otherwise a pending set member (a root) whose bytes differ from the activated copy keeps the
    activated bytes unless `approved`; an existing pending entry whose landed bytes no longer match `tree` under an
    approval is refused (the file moved again without its own landing). Raises RuntimeError on any refusal."""
    tree, kernel_dir = Path(tree), Path(kernel_dir)
    report = derive(tree)
    members, roots = report["kernel"], set(report["roots"])
    before = previous.get("kernel") if isinstance(previous, dict) else None
    old_dir = Path(previous["kernel_dir"]) if isinstance(before, dict) and previous.get("kernel_dir") else None
    if isinstance(before, dict) and (old_dir is None or not old_dir.is_dir()):
        raise RuntimeError("the activated kernel copy %s is not there, so generation N's bytes are unknown" % old_dir)
    old_pending = (previous.get("pending_kernel") or {}) if isinstance(before, dict) else {}
    kernel, pending = {}, {}
    for rel in members:
        new = source = tree / rel
        landed = _sha(new)
        if isinstance(before, dict) and rel in roots:
            old = old_dir / rel
            deployed = _sha(old)
            if deployed != before.get(rel):
                raise RuntimeError("the activated kernel copy of %s does not match its stamp" % rel)
            entry = old_pending.get(rel) or {}
            if landed != deployed and approved and entry.get("landed_sha256") != landed:
                raise RuntimeError("%s differs from generation N as %s, and no landing deployed those bytes (pending: %s): "
                                   "it moved without its own landing" % (rel, landed, entry.get("landed_sha256")))
            if landed != deployed and not approved:
                at = entry.get("landed_at_revision") if entry.get("landed_sha256") == landed else revision
                pending[rel] = {"deployed_sha256": deployed, "landed_sha256": landed, "landed_at_revision": at}
                source = old
        if source.is_file():
            (kernel_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(kernel_dir / rel))
        kernel[rel] = _sha(kernel_dir / rel)
    rows = set(derive(kernel_dir)["rows"])     # the rows of the gate this stamp activates, read from its own copy
    overlay = sorted(p for p in kernel if kernel[p] is not None and (p in roots or p in rows))
    return kernel, pending, overlay


def hold_pending(stage, kernel_dir, pending):
    """Put generation N's bytes of every pending file where the staged bin runs it: a scripts/loop tool at the bin's
    top, and the candidate's copy when the candidate holds one. A file absent at N is removed from both."""
    stage, kernel_dir = Path(stage), Path(kernel_dir)
    for rel, entry in sorted(pending.items()):
        spots = [stage / "candidate" / rel]
        if rel.startswith("scripts/loop/") and "/" not in rel[len("scripts/loop/"):]:
            spots.append(stage / rel[len("scripts/loop/"):])
        for spot in spots:
            if entry["deployed_sha256"] is None:
                if spot.is_file():
                    spot.unlink()
            elif spot.is_file() or spot.parent == stage:
                shutil.copy2(str(kernel_dir / rel), str(spot))


def archive_revision(source, revision, dest):
    """`revision`'s scripts and plugin folders, as the RAW committed blobs, written into dest (which must exist): git
    ls-tree for the entries, git cat-file --batch for the bytes, the executable bit from the mode; a link refuses.
    Never git archive (security review 2026-10-19, MEDIUM, parser differential: it applies the tree's own .gitattributes,
    so export-ignore drops a file and export-subst rewrites one). Raises CalledProcessError or RuntimeError."""
    listed = subprocess.run(["git", "-C", str(source), "ls-tree", "-r", "-z", "--full-tree", revision, "--",
                             "scripts", "plugin"], capture_output=True, check=True).stdout
    entries = []
    for record in listed.split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, kind, sha = meta.split()
            if kind == b"blob":
                entries.append((mode, sha, path.decode("utf-8", "surrogateescape")))
    out = subprocess.run(["git", "-C", str(source), "cat-file", "--batch"], input=b"".join(s + b"\n" for _, s, _ in entries),
                         capture_output=True, check=True).stdout
    links = [path for mode, _sha, path in entries if mode == b"120000"]
    if links:
        # the tree under scripts and plugin holds no link (measured 0, 2026-10-19); a link could carry a later write out
        # of the destination (review 4, HIGH), so a revision holding one is refused whole, nothing extracted
        raise RuntimeError("the revision holds a link under scripts or plugin (%s); refused, nothing extracted" % links[0])
    at, dest = 0, Path(dest)
    for mode, sha, path in entries:
        header_end = out.index(b"\n", at)
        got_sha, _kind, size = out[at:header_end].split()
        if got_sha != sha:
            raise RuntimeError("git cat-file answered %s for %s" % (got_sha, sha))
        body = out[header_end + 1:header_end + 1 + int(size)]
        at = header_end + 1 + int(size) + 1
        write_entry(dest, path, body, mode)


def write_entry(dest, path, body, mode):
    """Write one regular file at dest/path: never outside dest (an absolute path or a .. part refuses), never through a
    link (each folder is made or checked as a real folder, the file opened O_CREAT|O_EXCL|O_NOFOLLOW), never over an
    existing file (two entries that meet on a case insensitive disk refuse). Raises RuntimeError or OSError."""
    dest = Path(os.path.realpath(str(dest)))
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise RuntimeError("the revision names a path outside its tree: %r" % path)
    folder = dest
    for part in rel.parts[:-1]:
        folder = folder / part
        if os.path.islink(str(folder)):
            raise RuntimeError("%s is a link; nothing is written through one" % folder)
        if not folder.is_dir():
            folder.mkdir()
    fd = os.open(str(folder / rel.parts[-1]), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o755 if mode == b"100755" else 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)


def staging_sources(source, target, tree, revision, approved):
    """(tools dir, configs dir) a stage copies from. APPROVED, both come from `tree`, the archive of the approved
    revision, the configs written there from that revision by git show (security review 2026-10-19, MEDIUM: git status
    can be blinded by an ignored file, skip-worktree or assume-unchanged, so the working tree is never what an approval
    deploys). Unapproved, the working tree's scripts/loop and config_dir, exactly as before."""
    if not approved:
        return source / "scripts/loop", config_dir(source, target)
    plan = tree / "docs/plan"
    plan.mkdir(parents=True, exist_ok=True)
    for name in CONFIGS:
        shown = subprocess.run(["git", "-C", str(source), "cat-file", "blob", "%s:docs/plan/%s" % (revision, name)],
                               capture_output=True)   # the raw blob: git show can run a textconv filter
        if shown.returncode == 0:
            (plan / name).write_bytes(shown.stdout)
    return tree / "scripts/loop", plan


def stage_run(source, target, approved=False, expect_head=None):
    """Stage the next generation beside the target without touching it: snapshot, bin tools, configs, candidate and
    the kernel at this revision (A2.b), then the staged canary. Returns the run directory; its stage carries the stamp
    publish() switches to. approved: the owner's approval has been verified (activate), so the pending set moves;
    expect_head: the commit that approval named, which HEAD must still be (an approval never moves another commit)."""
    if not (source / "scripts/loop").is_dir():
        raise RuntimeError("source has no scripts/loop directory")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                       text=True).strip()
    if approved and revision != expect_head:
        raise RuntimeError("HEAD %s moved since the approval named %s; nothing staged" % (revision, expect_head))
    dirty = bool(subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True))
    previous = read_stamp(target)
    derive = derive_with(kernel_tool())
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
    # The candidate and the kernel come from the committed revision, never the working tree (B5-10), and so do the bin
    # tools and the configs of an approved stage (staging_sources); a revision that ships no plugin runtime has no loop
    # candidate, and a proof phase then refuses at model_router.code_root().
    tree = run / "source"
    tree.mkdir()
    try:
        archive_revision(source, revision, tree)
        tools, source_plan = staging_sources(source, target, tree, revision, approved)
        for directory in (target, tools):
            # files and package directories alike, through the one copier every site shares (tool_stamp.copy_tools)
            tool_stamp.copy_tools(str(directory), str(stage), skip=(STAMP,))
        for name in CONFIGS:
            src = source_plan / name
            if name == "loop-canary.json" and not src.exists():
                src = target.parent / name  # The original script kept an existing optional spec.
            if src.exists() or name != "loop-canary.json":
                shutil.copy2(str(src), str(plan / name))
        if (tree / "plugin/runtime").is_dir():
            staged = stage_candidate(str(tree), str(stage / "candidate"))
            print("CANDIDATE %d files staged from %s" % (len(staged), revision), flush=True)
        else:
            print("CANDIDATE none: revision %s ships no plugin/runtime" % revision, flush=True)
        kernel_dir = run / "tree/kernel"
        kernel, pending, overlay = stage_kernel(tree, kernel_dir, previous, revision, derive, approved)
    finally:
        shutil.rmtree(str(tree))
    hold_pending(stage, kernel_dir, pending)
    print("KERNEL %d files at %s, %d pending activation%s" % (
        len(kernel), revision, len(pending), "".join("\nPENDING %s" % p for p in sorted(pending))), flush=True)

    line, code = tool_stamp.prove(str(stage), cwd=str(source))
    print(line, flush=True)
    if code != 0:
        raise RuntimeError("staged canary refused; target unchanged")
    deployed = {"bin/" + name: sha for name, sha in hashes(stage).items()}
    deployed.update(hashes(plan))
    # "source" is the checkout the bin's programs resolve their code root from (model_router.stamp_root, C13)
    write_json(stage / STAMP, {"schema": 1, "revision": revision, "source_dirty": dirty, "source": str(source),
                              "files": deployed, "rollback": str(backup), "kernel": kernel,
                              "kernel_dir": str(kernel_dir), "pending_kernel": pending, "kernel_overlay": overlay})
    print("STAGED %s" % run, flush=True)
    return run


def publish(run, target, source):
    """Switch the target to the stage of `run` (stage_run), then prove parity and the stamp's hashes on the switched
    target; any failure there restores the snapshot the stage took."""
    run = Path(run)
    stage, plan = run / "tree/scripts/loop", run / "tree/docs/plan"
    stamp = read_stamp(stage)
    if not stamp or not isinstance(stamp.get("files"), dict) or not stamp.get("rollback"):
        raise RuntimeError("%s holds no staged stamp to publish" % run)
    deployed, backup = stamp["files"], Path(stamp["rollback"])
    if {"bin/" + n: s for n, s in hashes(stage).items() if n != STAMP} != {
            k: v for k, v in deployed.items() if k.startswith("bin/")}:
        raise RuntimeError("the stage of %s changed after its canary; nothing published" % run)
    work = run / "publish"
    work.mkdir()
    configs = {}
    for name in CONFIGS:
        if (plan / name).exists():
            shutil.copy2(str(plan / name), str(work / name))
            configs[name] = work / name
    payload = work / "next-bin"
    payload.symlink_to(stage)
    frozen_check()  # Check again after the canary, before publication.
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
    print("PASS: deployed %s; stamp %s" % (stamp.get("revision"), target / STAMP))


def deploy(source, target, approved=False, expect_head=None):
    publish(stage_run(source, target, approved, expect_head), target, source)


#: THE IN-PASS MODE (A2.d step 6). A deploy refuses while a loop is alive (quiet), and the lander runs inside the live
#: loop's pass. --in-pass replaces that check with this one: the live driver's lease holder (loop_guard.sh owner) is an
#: ancestor of this process, so only the driver's own pass switches the bin. No holder, or a holder outside this
#: process's ancestry, refuses.
IN_PASS = False


def ancestors(pid=None, ps=None):
    """This process's ancestor pids, nearest first, read from ps; stops at pid 1 or an unreadable parent."""
    out, pid = [], os.getpid() if pid is None else pid
    for _ in range(64):
        r = (ps or subprocess.run)(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=30)
        parent = (r.stdout or "").strip()
        if r.returncode != 0 or not parent.isdigit() or int(parent) <= 1:
            return out
        pid = int(parent)
        out.append(pid)
    return out


def in_pass_check(owner=None, chain=None):
    """Refuse (RuntimeError) unless the loop lease names a holder that is an ancestor of this process."""
    if owner is None:
        r = subprocess.run(["bash", str(Path(HERE) / "loop_guard.sh"), "owner"], capture_output=True, text=True,
                           timeout=30)
        owner = (r.stdout or "").strip() if r.returncode == 0 else ""
    if not owner.isdigit():
        raise RuntimeError("--in-pass: no live loop holds the lease, so this is not a pass; deploy refused")
    if int(owner) not in (ancestors() if chain is None else chain):
        raise RuntimeError("--in-pass: the lease holder %s is not an ancestor of this process; deploy refused" % owner)


def frozen_check():
    """The check before any switch: no live loop, or, in the in-pass mode, the caller is that loop's own pass."""
    if IN_PASS:
        in_pass_check()
        print("IN-PASS: the lease holder is an ancestor of this deploy", flush=True)
    else:
        quiet()


#: THE APPROVAL IS CHECKED FROM THE ANCHOR, NEVER FROM THE TREE IT APPROVES (security review of 2a41a7d85..c2c70acbd,
#: 2026-10-19, MEDIUM: the check imported cut.py from the source tree, which a loop landing can write, so a landed
#: change could approve itself). The cut's own approval_refusal and version_source run from APPROVAL_ANCHOR's copy, the
#: ref the cut pins its approver key to and which only the owner merges, archived into a temp folder and run under
#: python -I; the source tree is data only (its version and the commit approved). A missing anchor refuses.
APPROVAL_ANCHOR = "hub/main"
APPROVAL_CHILD = r"""import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1] + "/scripts", sys.argv[1]]
import cut, version_source
version, _doc = version_source.read_source(Path(sys.argv[2]))
why = cut.approval_refusal(sys.stdin.read().strip(), version, sys.argv[3], sys.argv[2])
print(why or "APPROVED")
sys.exit(1 if why else 0)
"""


def approval_head(source, line):
    """Verify the owner's approval line through the cut's own CY1 path (approval_refusal, which spends it), run from the
    anchor's copy, for the source's version and HEAD; return that HEAD, which the stage must still find (stage_run's
    expect_head). A dirty source refuses (the line names a commit, and the stage copies the working tree's tools). The
    line travels on stdin, never in argv. Raises RuntimeError."""
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("activate: the source checkout is not clean, so the commit approved is not what would deploy")
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if subprocess.run(["git", "-C", str(source), "rev-parse", "--verify", "-q", APPROVAL_ANCHOR + "^{commit}"],
                      capture_output=True).returncode != 0:
        raise RuntimeError("activate: %s is not readable in %s, so no trusted copy of the approval check exists"
                           % (APPROVAL_ANCHOR, source))
    anchor = Path(tempfile.mkdtemp(prefix="approval-anchor-"))
    try:
        archive_revision(source, APPROVAL_ANCHOR, anchor)
        r = subprocess.run([sys.executable, "-I", "-B", "-c", APPROVAL_CHILD, str(anchor), str(source), head],
                           input=line, capture_output=True, text=True, timeout=300)
    finally:
        shutil.rmtree(str(anchor), ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError("activate: %s" % ((r.stdout or r.stderr).strip().splitlines() or ["the check said nothing"])[-1])
    return head


USAGE = ("usage: deploy_stamped.sh [deploy [--in-pass] | stage | publish <run> [--in-pass] | activate --approval <line> "
         "| restore <rollback-directory> [--in-pass]]")


def main():
    global IN_PASS
    args = sys.argv[1:]
    IN_PASS = "--in-pass" in args
    args = [a for a in args if a != "--in-pass"]
    verb = args[0] if args else "deploy"
    shapes = {"deploy": 1, "stage": 1, "publish": 2, "restore": 2, "activate": 3}
    if verb not in shapes or len(args or ["deploy"]) != shapes[verb] or (verb == "activate" and args[1] != "--approval") \
            or (IN_PASS and verb not in ("deploy", "publish", "restore")):
        print(USAGE, file=sys.stderr)
        return 2
    target = Path(os.path.abspath(os.path.expanduser(
        os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin"))))
    source = Path(os.environ.get("BROTHER_DEPLOY_SOURCE", str(Path(HERE).parents[1]))).expanduser().resolve()
    try:
        if os.path.lexists(str(target)) and not target.is_dir():
            raise RuntimeError("target exists but is not a directory or directory symlink")
        if not target.is_dir() and verb != "restore":
            # Only a verified snapshot may recreate an absent target: a crash between the plain
            # directory migration's two renames leaves none, and restore is the way back from it.
            raise RuntimeError("target must be an existing directory or directory symlink")
        with (target.parent / ".deploy-stamped.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if verb != "stage":   # staging touches no target, so a live loop may stage beside itself
                frozen_check()
            if verb == "restore":
                restore(Path(args[1]).resolve(), target)
            elif verb == "stage":
                stage_run(source, target)
            elif verb == "publish":
                publish(Path(args[1]).resolve(), target, source)
            elif verb == "activate":
                deploy(source, target, approved=True, expect_head=approval_head(source, args[2]))
            else:
                deploy(source, target)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print("FAIL: deploy/restore refused: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

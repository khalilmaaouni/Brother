#!/usr/bin/env python3
"""bundle_runtime: package brother_run.py's own execution engine into
bundle/runtime/, so an installed Brother plugin ships the spine instead of
only its commands and skills.

WHY. bundle/ today holds MANIFEST.json, commands/ and skills/: the /brother
BUILD IT route tells a session to run scripts/brother_run.py, a file that
does not exist anywhere under bundle/. An install has no checkout of this
repository sitting next to it, so that route is dead on an installed
machine. This is the one place that copies the engine in; nothing under
bundle/runtime is ever hand edited, only regenerated here.

THE CLOSURE, computed from the real files rather than typed by hand.
Starting from scripts/brother_run.py, every LOCAL module it imports (AST
`import x` / `from x import y`, where x.py lives in scripts/) and every
LOCAL script it names as a bare string literal (subprocess and path targets
like `os.path.join(HERE, "door.py")`, which are never a Python import) is
walked to a fixed point. A source edit that adds or drops an edge changes
the closure on the next run without anyone updating a list.

OUTPUT. bundle/runtime/<each closure file>, byte-identical to its scripts/
source; bundle/runtime/brother-run, a small launcher this script writes
directly (nothing under scripts/ needs to run from an installed plugin's
own directory, so there is no scripts/ source to copy for it); and
bundle/runtime/RUNTIME-MANIFEST.json, a sorted file list with sha256 content
hashes and no timestamp, so an unchanged source tree regenerates
byte-identical output.

THE SOURCE STAMP (harness-identity-v1, the zero-context critic reading a
fresh clone of v1.0.0, 2026-09-03). An installed plugin is a COPY, never a
checkout: there is no .git anywhere near bundle/runtime, so brother_run.py's
`git rev-parse` cannot name the engine that produced a receipt and every
installed run's receipt read "harness NO-DATA". The manifest therefore also
carries `source_revision` (the full sha of the hub revision these bytes were
copied from) and `source_describe` (`git describe --tags --always` of the
same), both the NO-DATA strings below when git cannot answer, never a
fabricated string. brother_run.py falls back to these two fields when git
fails; nothing else reads them.

THE STAMP IS NOT PART OF THE CHECK, deliberately. The manifest's statement
about BYTES is its file list and their sha256 hashes; the stamp is a note
about where those bytes came from. So --check compares the file hashes (and
the launcher, and the closure) and IGNORES the two stamp fields, because a
tip that moved with no source edit must not turn the check red. It does
require both keys to be present, since a manifest without them was written
by a generator older than this one. generate() carries the existing stamp
forward unchanged when the hashes did not change, so regenerating on a
moved tip rewrites nothing at all.

WHAT THE STAMP CAN AND CANNOT SAY. It is read at GENERATION time, so a
bundle generated beside uncommitted edits (the normal case: the engine
change and its regenerated bundle land in one commit) names that commit's
PARENT, whose scripts/ is not what was packaged. That is why source_describe
carries git's own `--dirty` marker: a stamp ending in "-dirty" is provisional,
says so, and is the ONE case generate() refreshes instead of carrying
forward, so the next generation on a clean tree converges the stamp onto the
commit whose scripts/ really does equal these bytes. The exact identity is
and stays the sha256 list; the stamp is the pointer beside it.

--check reads scripts/ and bundle/runtime/ and reports drift without
writing anything: exit 0 means every closure file's bytes in bundle/runtime
match its scripts/ source, the launcher is current, and the manifest names
the same closure with the same hashes; exit 1 names what is stale or
missing.

Python 3, standard library only. No network.

PRODUCER: this module is the sole producer of its own records. generate()
(around line 194) writes every bundle/runtime/<name> file, the brother-run
launcher, and RUNTIME-MANIFEST.json, all through _write_if_changed()
(defined at line 180), whose actual write is open(path, "wb") plus
fh.write(data) at lines 189-190.
"""
import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCRIPTS_DIR = HERE
RUNTIME_DIR = os.path.join(REPO_ROOT, "bundle", "runtime")
ENTRY = "brother_run.py"
MANIFEST_NAME = "RUNTIME-MANIFEST.json"
LAUNCHER_NAME = "brother-run"

NODATA = "NO-DATA"
#: The two manifest fields that name where the bytes came from rather than
#: what the bytes are. Excluded from every comparison --check makes; see the
#: module docstring.
STAMP_FIELDS = ("source_revision", "source_describe")
NO_REVISION = ("%s: git could not name the source revision when this runtime "
               "was generated" % NODATA)
NO_DESCRIBE = ("%s: git could not describe the source revision when this "
               "runtime was generated" % NODATA)

#: Written directly rather than copied: this is the ONE file in bundle/runtime
#: with no scripts/ source, because it exists only to be run from an installed
#: plugin's own directory, a concern scripts/brother_run.py itself does not
#: have.
LAUNCHER_SOURCE = '''#!/usr/bin/env python3
"""brother-run: the installed entry point for brother_run.py.

Runs brother_run.py FROM THIS LAUNCHER'S OWN DIRECTORY (bundle/runtime/ once
installed), never the caller's current directory, so one launcher works
pointed at any target repository with no Brother checkout anywhere near it.

    brother-run "an outcome" --cwd /path/to/target/repo

Written by scripts/bundle_runtime.py; never hand edited.
"""
import os
import subprocess
import sys

LAUNCHER_DIR = os.path.dirname(os.path.abspath(__file__))
BROTHER_RUN = os.path.join(LAUNCHER_DIR, "brother_run.py")


def default_runs_root(launcher_dir=LAUNCHER_DIR, env=None):
    """Where a run's Work document and claim store live when the caller does
    not say with --runs-root. A dev checkout keeps them inside its own
    repository, exactly as brother_run.py does by default when it is run
    from scripts/ directly. An installed plugin has no such writable
    repository beside it (the plugin cache is replaced on update, so writing
    run state there would lose it at the next upgrade), so that case falls
    back to a per-user state directory instead."""
    env = os.environ if env is None else env
    override = (env.get("BROTHER_RUNS_ROOT") or "").strip()
    if override:
        return override
    try:
        proc = subprocess.run(
            ["git", "-C", launcher_dir, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10)
    except OSError:
        proc = None
    if proc is not None and proc.returncode == 0:
        top = proc.stdout.strip()
        if top and os.access(top, os.W_OK):
            return top
    return os.path.expanduser(os.path.join("~", ".claude", "brother-run"))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--runs-root" not in args:
        args = args + ["--runs-root", default_runs_root()]
    return subprocess.call([sys.executable, BROTHER_RUN] + args)


if __name__ == "__main__":
    sys.exit(main())
'''


def _script_files(scripts_dir):
    return {f for f in os.listdir(scripts_dir) if f.endswith(".py")}


def compute_closure(entry=ENTRY, scripts_dir=SCRIPTS_DIR):
    """BFS from `entry` over local imports and local script-path string
    literals, both read from the same AST walk of each file as it is
    visited. Returns a sorted list of scripts/ basenames (with .py)."""
    existing = _script_files(scripts_dir)
    seen = set()
    queue = [entry]
    while queue:
        current = queue.pop(0)
        if current in seen or current not in existing:
            continue
        seen.add(current)
        path = os.path.join(scripts_dir, current)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            candidate = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    cand = alias.name.split(".")[0] + ".py"
                    if cand in existing and cand not in seen:
                        queue.append(cand)
                continue
            if isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    candidate = node.module.split(".")[0] + ".py"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # A bare 'name.py' string constant is how a subprocess or
                # path target names a sibling script (brother_run.py's own
                # os.path.join(HERE, "door.py"), loop_bridge.py's
                # os.path.join(HERE, "model_worker.py")): never a Python
                # import, so ast.Import/ImportFrom never sees it. Matched
                # against the real file list, not a suffix check, so a long
                # docstring cannot be mistaken for a reference.
                if node.value in existing:
                    candidate = node.value
            if candidate and candidate in existing and candidate not in seen:
                queue.append(candidate)
    return sorted(seen)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _git_line(args, cwd, nodata):
    """One line of git output from `cwd`, or `nodata` when git is absent,
    `cwd` is not a checkout, git exits non-zero, or it prints nothing. Never
    raises and never fabricates: a bundle generated outside a checkout says
    so in the manifest rather than carrying a guess."""
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return nodata
    if proc.returncode != 0:
        return nodata
    return proc.stdout.strip() or nodata


def build_manifest(closure, scripts_dir=SCRIPTS_DIR):
    files = [{"path": name, "sha256": _sha256(_read_bytes(os.path.join(scripts_dir, name)))}
             for name in closure]
    files.append({"path": LAUNCHER_NAME,
                  "sha256": _sha256(LAUNCHER_SOURCE.encode("utf-8"))})
    files.sort(key=lambda f: f["path"])
    return {"generated_by": "scripts/bundle_runtime.py", "entry": ENTRY,
            "files": files,
            "source_revision": _git_line(["rev-parse", "HEAD"], scripts_dir,
                                         NO_REVISION),
            "source_describe": _git_line(
                ["describe", "--tags", "--always", "--dirty"],
                scripts_dir, NO_DESCRIBE)}


def _content(manifest):
    """The manifest minus its stamp: everything that is a statement about the
    packaged BYTES. This, never the whole document, is what --check compares."""
    return {k: v for k, v in manifest.items() if k not in STAMP_FIELDS}


def _read_manifest(path):
    """The manifest at `path` as a dict, or None when it is missing or not
    readable JSON (which --check reports as staleness, never as a pass)."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _manifest_bytes(closure, scripts_dir=SCRIPTS_DIR, keep_stamp_from=None):
    """The manifest as it should be written. `keep_stamp_from` is an existing
    manifest path whose stamp is carried forward UNCHANGED when the byte
    content did not change, so running this generator again on a moved tip
    rewrites nothing; the stamp then names the revision the packaged bytes
    were last actually copied from, which is the true answer."""
    manifest = build_manifest(closure, scripts_dir)
    previous = _read_manifest(keep_stamp_from) if keep_stamp_from else None
    provisional = str((previous or {}).get("source_describe", "")).endswith(
        "-dirty")
    if (previous and not provisional
            and _content(previous) == _content(manifest)
            and all(isinstance(previous.get(k), str) for k in STAMP_FIELDS)):
        for field in STAMP_FIELDS:
            manifest[field] = previous[field]
    return (json.dumps(manifest, indent=1, sort_keys=True)
            + "\n").encode("utf-8")


def _write_if_changed(path, data):
    """Returns True if `path` was created or its bytes differed from `data`.
    A file already holding these exact bytes is left untouched (not even its
    mtime), which is what makes a repeat run report no changes."""
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            if fh.read() == data:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return True


def generate(scripts_dir=SCRIPTS_DIR, runtime_dir=RUNTIME_DIR):
    closure = compute_closure(scripts_dir=scripts_dir)
    changed = []
    for name in closure:
        data = _read_bytes(os.path.join(scripts_dir, name))
        if _write_if_changed(os.path.join(runtime_dir, name), data):
            changed.append(name)
    launcher_path = os.path.join(runtime_dir, LAUNCHER_NAME)
    if _write_if_changed(launcher_path, LAUNCHER_SOURCE.encode("utf-8")):
        changed.append(LAUNCHER_NAME)
    os.chmod(launcher_path, 0o755)
    manifest_path = os.path.join(runtime_dir, MANIFEST_NAME)
    if _write_if_changed(manifest_path,
                         _manifest_bytes(closure, scripts_dir,
                                         keep_stamp_from=manifest_path)):
        changed.append(MANIFEST_NAME)
    return closure, changed


def check(scripts_dir=SCRIPTS_DIR, runtime_dir=RUNTIME_DIR):
    """Read-only: does bundle/runtime match scripts/ right now? Returns
    (ok, problems, closure); never writes anything."""
    closure = compute_closure(scripts_dir=scripts_dir)
    problems = []
    for name in closure:
        dst = os.path.join(runtime_dir, name)
        if not os.path.isfile(dst):
            problems.append("%s: missing from bundle/runtime" % name)
        elif _read_bytes(os.path.join(scripts_dir, name)) != _read_bytes(dst):
            problems.append("%s: bundle/runtime copy does not match its "
                            "scripts/ source" % name)
    launcher_path = os.path.join(runtime_dir, LAUNCHER_NAME)
    if not os.path.isfile(launcher_path):
        problems.append("%s: missing from bundle/runtime" % LAUNCHER_NAME)
    elif _read_bytes(launcher_path) != LAUNCHER_SOURCE.encode("utf-8"):
        problems.append("%s: does not match this generator's current "
                        "launcher source" % LAUNCHER_NAME)
    manifest_path = os.path.join(runtime_dir, MANIFEST_NAME)
    on_disk = _read_manifest(manifest_path)
    if not os.path.isfile(manifest_path):
        problems.append("%s: missing" % MANIFEST_NAME)
    elif on_disk is None:
        problems.append("%s: unreadable, so what this runtime ships is not "
                        "written down" % MANIFEST_NAME)
    elif not all(isinstance(on_disk.get(f), str) for f in STAMP_FIELDS):
        problems.append("%s: carries no %s, so an installed copy cannot name "
                        "the engine it was built from; regenerate"
                        % (MANIFEST_NAME, " and no ".join(STAMP_FIELDS)))
    elif _content(on_disk) != _content(build_manifest(closure, scripts_dir)):
        problems.append("%s: stale, its file list or hashes do not match a "
                        "fresh generation" % MANIFEST_NAME)
    return (not problems), problems, closure


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift without writing anything; exit 1 if "
                         "bundle/runtime does not match scripts/")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.check:
        ok, problems, closure = check()
        if ok:
            print("bundle_runtime: bundle/runtime matches scripts/ for all "
                  "%d closure file(s)" % len(closure))
            return 0
        for problem in problems:
            print("bundle_runtime: DRIFT: %s" % problem, file=sys.stderr)
        return 1

    closure, changed = generate()
    if changed:
        print("bundle_runtime: wrote %d file(s): %s"
              % (len(changed), ", ".join(changed)))
    else:
        print("bundle_runtime: no changes; bundle/runtime already matches "
              "scripts/ for %d file(s)" % len(closure))
    return 0


if __name__ == "__main__":
    sys.exit(main())

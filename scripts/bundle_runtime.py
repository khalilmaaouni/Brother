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

DATA DIRECTORIES. Some closure files read data, not code, at run time
(door.py's own scripts/packs/*.json manifests). Each name in DATA_DIRS is a
directory under scripts/ pulled in by that same bare-string rule: a closure
file naming it (e.g. `PACKS_DIR = os.path.join(HERE, "packs")`) is what
makes it referenced. A referenced directory is mirrored into bundle/runtime/
recursively, listed in RUNTIME-MANIFEST.json with its own per-file sha256
hashes, and checked by --check exactly like a closure file.

OUTPUT. bundle/runtime/<each closure file> and bundle/runtime/<data dir>/,
byte-identical to their scripts/ sources; bundle/runtime/brother-run, a
small launcher this script writes directly (nothing under scripts/ needs to
run from an installed plugin's own directory, so there is no scripts/
source to copy for it); and bundle/runtime/RUNTIME-MANIFEST.json, a sorted
file list with sha256 content hashes and no timestamp, so an unchanged
source tree regenerates byte-identical output.

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
(around line 346) writes every bundle/runtime/<name> file, every data
directory file, the brother-run launcher, and RUNTIME-MANIFEST.json, all
through _write_if_changed() (defined at line 332), whose actual write is
open(path, "wb") plus fh.write(data) at lines 341-342.

HOOKS (portability A1, 2026-09-06). Codex 0.153 runs plugin-delivered hooks
straight from the installed plugin's own hooks/hooks.json (measured on a
real signed-in run: <CODEX_HOME>/plugins/cache/brother/brothermode/3.4.4/
hooks/hooks.json fired SessionStart, PreToolUse, PostToolUse and Stop). The
brother bundle ships no hooks/ at all today, so a Codex home holding only
brother@brother has zero Brother hooks. This module now also mirrors every
hook TOOL that products/brothermode/hooks/hooks.json and
products/brothersbe/hooks/hooks.json name, plus every local module or
sibling script each one reaches (the same closure walk compute_closure
already does for brother_run.py, generalized in _closure_from_entries to
start from more than one entry file), into bundle/runtime/hooks/<product>/
tools/, and writes bundle/hooks/hooks.json: the union of both products'
hooks.json, brothermode's own event order first then brothersbe's, every
`${CLAUDE_PLUGIN_ROOT}/tools/` rewritten to
`${CLAUDE_PLUGIN_ROOT}/runtime/hooks/<product>/tools/` so the mirrored copy
is what actually runs.

WHY A SEPARATE MANIFEST (bundle/runtime/hooks/HOOKS-MANIFEST.json) RATHER
THAN ADDING TO RUNTIME-MANIFEST.json's OWN "files" LIST: ManifestMatchesThe
Closure's own regression asserts that RUNTIME-MANIFEST.json's "files" name
EXACTLY the scripts/ closure plus the launcher and the verifier, "no more
and no less". Folding the hook mirror in there would break a true claim
about the engine's own manifest in order to serve a second, unrelated
question (does the hook mirror match products/). Same file family, same
technique (a sorted file list with sha256 hashes, no timestamp, checked by
--check exactly like the closure files), separate manifest.

Python 3, standard library only. No network.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SCRIPTS_DIR = HERE
PRODUCTS_DIR = os.path.join(REPO_ROOT, "products")
RUNTIME_DIR = os.path.join(REPO_ROOT, "bundle", "runtime")
ENTRY = "brother_run.py"
#: A reusable done_check support tool ships beside the runner. It is a second
#: closure root, not a pretend import from brother_run.py: evidence units call
#: it directly, and an installed runtime must carry the same guard a checkout
#: can name in its done_check.
SUPPORT_ENTRIES = ("native_evidence.py", "mobile_workflow.py", "mobile_design.py")
MANIFEST_NAME = "RUNTIME-MANIFEST.json"
LAUNCHER_NAME = "brother-run"
VERIFIER_NAME = "verify_runtime.py"
#: The two products whose hooks.json a Codex-only or Claude-only install
#: must still carry, in the order their commands appear in the merged
#: bundle/hooks/hooks.json (brothermode first, per the brief).
HOOK_PRODUCTS = ("brothermode", "brothersbe")
HOOKS_JSON_NAME = "hooks.json"
HOOKS_MANIFEST_NAME = "HOOKS-MANIFEST.json"
#: Matches the exact command shape every hook in both products uses:
#: `... "${CLAUDE_PLUGIN_ROOT}/tools/<name>.py" ...`.
_HOOK_TOOL_RE = re.compile(
    r"\$\{CLAUDE_PLUGIN_ROOT\}/tools/([A-Za-z0-9_.\-]+\.py)")
#: Named data directories under scripts/ (no .py inside) that a closure file
#: reads at run time. Mirrored only when a closure file references the
#: directory by a bare string constant, the same rule the closure already
#: uses for sibling .py names.
DATA_DIRS = ("packs",)
#: Single files OUTSIDE scripts/ that a closure module reads at run time,
#: as {the closure module that reads it: (repository-relative source, the
#: name it takes inside bundle/runtime)}. Mirrored FLAT, beside the reader,
#: and only when that reader is actually in the closure.
#:
#: WHY THIS EXISTS BESIDE DATA_DIRS. DATA_DIRS walks a directory UNDER
#: scripts/, which is where door.py's packs live. The outcome contract
#: schema does not: docs/schema/ is the single source the whole estate
#: reads, and bundle/runtime has no docs/ tree above it, so
#: contract_check.py's own docs/schema path resolves to nothing in an
#: installed plugin and every `--contract` run would read NO-DATA. The
#: reader finds the flat copy through contract_check.default_schema().
#: A declared source that is not there contributes nothing, exactly as an
#: absent DATA_DIRS directory does.
DATA_FILES = {
    "contract_check.py": ("docs/schema/outcome-contract-v1.json",
                          "outcome-contract-v1.json"),
}

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


#: The second file with no scripts/ source, added by E80 (2026-09-04). An
#: external release integrity trial found that NOTHING on an installed plugin
#: ever read the sha256 values in RUNTIME-MANIFEST.json: the manifest was a
#: claim about the shipped bytes that no shipped code could check.
#: scripts/bundle_runtime.py --check cannot do it, because it compares
#: bundle/runtime against scripts/ and an installed plugin has no scripts/.
#: This file ships INSIDE bundle/runtime, needs nothing beside it, and is
#: what the README's verification line names.
VERIFIER_SOURCE = '''#!/usr/bin/env python3
"""verify_runtime.py: do the bytes in this directory match RUNTIME-MANIFEST.json?

Run it from anywhere, with nothing else installed:

    python3 <this file>

It reads RUNTIME-MANIFEST.json out of ITS OWN directory, re-hashes every file
that manifest names, and reports one verdict:

    PASS      every manifested file is present and its sha256 matches
    FAIL      a file is missing, unreadable, or its bytes differ (exit 1)
    NO-DATA   the manifest is missing or unreadable, so nothing was checked
              and this is NEVER a pass (exit 2)

What it does NOT answer: whether the manifest itself is the one the release
published. That is a different question, answered by comparing this file tree
against the published tag (scripts/release_invariant.py does that on a
checkout). Here the manifest is the reference, so a tamper that rewrites both
a file and its manifest line passes: say so plainly rather than implying more.

Written by scripts/bundle_runtime.py; never hand edited.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_NAME = "RUNTIME-MANIFEST.json"


def load_manifest(path):
    """The manifest as a dict, or None when it is absent or not readable
    JSON. None is NO-DATA at the call site, never an empty file list."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        return None
    return doc


def verify(runtime_dir=HERE):
    """(verdict, lines): verdict is "PASS", "FAIL" or "NO-DATA"; lines are
    the human readable detail, most important first."""
    manifest = load_manifest(os.path.join(runtime_dir, MANIFEST_NAME))
    if manifest is None:
        return "NO-DATA", ["%s is missing or unreadable in %s; nothing was "
                           "checked" % (MANIFEST_NAME, runtime_dir)]
    entries = manifest["files"]
    if not entries:
        return "NO-DATA", ["%s names no file, so there is nothing to check"
                           % MANIFEST_NAME]
    bad = []
    for entry in entries:
        if not isinstance(entry, dict):
            bad.append("manifest entry is not an object: %r" % (entry,))
            continue
        rel = entry.get("path")
        want = entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(want, str):
            bad.append("manifest entry has no usable path/sha256: %r" % (entry,))
            continue
        full = os.path.join(runtime_dir, *rel.split("/"))
        if os.path.islink(full):
            bad.append("%s: a symlink on disk, but the manifest attests a "
                       "regular file" % rel)
            continue
        try:
            with open(full, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            bad.append("%s: missing or unreadable (%s)" % (rel, exc.strerror))
            continue
        if got != want:
            bad.append("%s: sha256 %s, manifest says %s" % (rel, got, want))
    if bad:
        return "FAIL", bad
    return "PASS", ["all %d manifested file(s) match their sha256 in %s"
                    % (len(entries), MANIFEST_NAME)]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    runtime_dir = args[0] if args else HERE
    verdict, lines = verify(runtime_dir)
    print("verify_runtime: %s: %s" % (verdict, lines[0]))
    for line in lines[1:]:
        print("verify_runtime:   %s" % line)
    return {"PASS": 0, "FAIL": 1, "NO-DATA": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
'''


def _script_files(scripts_dir):
    """Every local .py file `_closure_from_entries` can name as a sibling,
    test_*.py included: a hook can genuinely depend on one at runtime
    (bm_fence_hook.py's own os.path.join(HERE, "test_all.py"), loaded as its
    battery gate module, is exactly this), so excluding the whole test_*.py
    class here would make that reference undiscoverable by the general rule
    and force a hand-listed exception at the call site instead. The risk a
    blanket inclusion would otherwise open, a test_*.py file's OWN body
    naming a long list of sibling test files (test_all.py enumerates the
    whole suite by name to run each one), is closed in
    _closure_from_entries: a file whose name starts with test_ is still
    walked for real imports, but its bare string literals are not read as
    further sibling references, so being swept in as a dependency never
    cascades into sweeping in everything IT happens to mention."""
    return {f for f in os.listdir(scripts_dir) if f.endswith(".py")}


def _closure_from_entries(entries, files_dir):
    """BFS from every name in `entries` over local imports and local
    script-path string literals, both read from the same AST walk of each
    file as it is visited. Returns a sorted list of files_dir basenames
    (with .py). Generalized out of compute_closure (portability A1,
    2026-09-06) so a hooks.json naming several independent tool files, none
    of which import each other, seeds the walk from all of them at once
    rather than losing every entry but the first."""
    existing = _script_files(files_dir)
    seen = set()
    queue = list(entries)
    while queue:
        current = queue.pop(0)
        if current in seen or current not in existing:
            continue
        seen.add(current)
        path = os.path.join(files_dir, current)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        # A test_*.py file (test_all.py, swept in below because
        # bm_fence_hook.py loads it as its gate module) is walked for real
        # imports like any other file, but its OWN bare string literals are
        # not read as further sibling references: test_all.py's body names
        # every suite in the battery (test_bm_store.py, test_bm_docs.py, and
        # around ninety more) as plain string constants, and none of those
        # are something test_all.py needs beside it to run as a module, only
        # names it later hands to a subprocess. Without this guard, sweeping
        # in one legitimately-needed test_*.py sibling would cascade into
        # sweeping in the whole suite.
        read_bare_strings = not current.startswith("test_")
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
            elif (read_bare_strings and isinstance(node, ast.Constant)
                  and isinstance(node.value, str)):
                # A bare 'name.py' string constant is how a subprocess or
                # path target names a sibling script (brother_run.py's own
                # os.path.join(HERE, "door.py"), loop_bridge.py's
                # os.path.join(HERE, "model_worker.py"), bm_fence_hook.py's
                # own os.path.join(HERE, "bm_store.py") loaded through
                # importlib.util.spec_from_file_location rather than a plain
                # import): never a Python import, so ast.Import/ImportFrom
                # never sees it. Matched against the real file list, not a
                # suffix check, so a long docstring cannot be mistaken for a
                # reference.
                if node.value in existing:
                    candidate = node.value
            if candidate and candidate in existing and candidate not in seen:
                queue.append(candidate)
    return sorted(seen)


def compute_closure(entry=ENTRY, scripts_dir=SCRIPTS_DIR):
    """BFS from the runner and shipped done_check support roots over local
    imports and local script-path string literals. Returns sorted script
    basenames (with .py)."""
    entries = [entry]
    if entry == ENTRY:
        entries += SUPPORT_ENTRIES
    return _closure_from_entries(entries, scripts_dir)


def _load_hooks_json(product, products_dir=PRODUCTS_DIR):
    """The hooks.json `product` ships, or None when it is absent or not
    readable JSON. Never raises: a product carrying no hooks/hooks.json
    contributes nothing to the mirror rather than failing the whole run."""
    path = os.path.join(products_dir, product, "hooks", HOOKS_JSON_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _hook_tool_names(hooks_doc):
    """Ordered, de-duplicated tool basenames named by every
    `${CLAUDE_PLUGIN_ROOT}/tools/<name>` command in `hooks_doc`, in
    first-seen order (event, then matcher group, then command)."""
    names = []
    seen = set()
    for groups in hooks_doc.get("hooks", {}).values():
        for group in groups:
            for h in group.get("hooks", []):
                m = _HOOK_TOOL_RE.search(h.get("command", ""))
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    names.append(m.group(1))
    return names


def count_hook_commands(hooks_doc):
    """Every leaf hook command object across every event and matcher group
    of `hooks_doc`: what actually fires, never the count of distinct tool
    files (bm_bash_audit.py alone fires twice, pre and post)."""
    return sum(len(group.get("hooks", []))
              for groups in hooks_doc.get("hooks", {}).values()
              for group in groups)


def _mirrored_tool_names(product, runtime_dir=RUNTIME_DIR):
    """Basenames of every .py file currently present (top level) under
    bundle/runtime/hooks/<product>/tools/, or [] when that directory does
    not exist yet. These are bytes the plugin ALREADY SHIPS, however they
    first got there: a hooks.json closure walk, or (bm_project.py and three
    siblings, F-mirror-drift 2026-09-11) a one-time hand copy at the
    Portability release commit that the hooks.json-only closure below never
    accounted for again. bm_project.py drifted enough to lose cmd_adopt
    entirely while brother_run.py's own ADOPT_COMMAND still told users to
    run "bm_project.py adopt", exit 2, because nothing regenerated or even
    LOOKED AT this file once it fell outside the closure. Folded into
    compute_hook_closure's own entries so every file the mirror ships is
    regenerated and checked against its product source, not only the
    hooks.json subset."""
    tools_dir = os.path.join(runtime_dir, "hooks", product, "tools")
    if not os.path.isdir(tools_dir):
        return []
    return sorted(f for f in os.listdir(tools_dir) if f.endswith(".py"))


def compute_hook_closure(product, products_dir=PRODUCTS_DIR,
                         runtime_dir=RUNTIME_DIR):
    """(tools_dir, closure) for `product`: the tool files its own
    hooks.json commands name, UNION every .py file the mirror already ships
    (_mirrored_tool_names, see its docstring for why), plus every local
    module or sibling script each one reaches, via the same walk
    compute_closure uses for brother_run.py. closure is [] when the product
    carries no hooks.json and the mirror ships nothing yet."""
    tools_dir = os.path.join(products_dir, product, "tools")
    hooks_doc = _load_hooks_json(product, products_dir)
    entries = _hook_tool_names(hooks_doc) if hooks_doc is not None else []
    entries = list(dict.fromkeys(
        entries + _mirrored_tool_names(product, runtime_dir)))
    if not entries:
        return tools_dir, []
    return tools_dir, _closure_from_entries(entries, tools_dir)


def _package_join_targets(path):
    """Relative POSIX paths (e.g. "brotherme/core/schema.py") found in
    path own source as the tail of an os.path.join(...)-shaped call
    whose trailing arguments are all string constants ending in ".py".
    bm_store.py own _schema() is the case this exists for:
    os.path.join(candidate_root, "brotherme", "core", "schema.py") loads
    a sibling PACKAGE that lives outside tools/ (F-002), the same kind of
    load-by-path the existing closure walk already follows for a bare
    sibling SCRIPT name inside tools/. Read from the AST, never a hand
    list, so a future path-join gets followed automatically. Existence is
    the caller job; this only reports what the source names."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    tails = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "join"):
            continue
        if "path" not in ast.dump(func.value):
            continue
        if len(node.args) < 2:
            continue
        tail_parts = []
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                tail_parts.append(arg.value)
            else:
                tail_parts = None
                break
        if tail_parts and tail_parts[-1].endswith(".py"):
            tails.add("/".join(tail_parts))
    return tails


def compute_hook_package_files(product, tools_dir, closure,
                               products_dir=PRODUCTS_DIR):
    """Relative POSIX paths under products/<product>/ (NOT under tools/,
    e.g. "brotherme/core/schema.py") that closure own tool files load
    by a path computed from their own location and that climbs OUT of
    tools/ into a sibling package. bm_store.py _schema() docstring names
    the checkout layout it supports: this file lives in the repo tools/,
    the package is a sibling of tools/ one level up, i.e. a sibling of
    tools/ under the PRODUCT directory, exactly where this mirrors it,
    so the installed copy satisfies that same layout. Walked to a fixed
    point exactly like _closure_from_entries, in case a mirrored file
    itself references a further sibling by the same pattern. A tail that
    resolves to a file directly inside tools_dir itself (e.g.
    vault_recall_hook.py own os.path.join(_ROOT, "tools",
    "bm_vault.py")) is SKIPPED: that file is already a flat tools/
    sibling the bare-constant closure walk already found, so a second
    copy would double-list one manifest path rather than name a real
    gap. Returns a sorted list; empty when nothing in the closure
    references a genuine sibling outside tools/."""
    product_dir = os.path.dirname(tools_dir)
    tools_dir_norm = os.path.normpath(tools_dir)
    found = set()
    visited = set()
    queue = [os.path.join(tools_dir, name) for name in closure]
    while queue:
        src = queue.pop(0)
        if src in visited or not os.path.isfile(src):
            continue
        visited.add(src)
        for tail in _package_join_targets(src):
            candidate = os.path.join(product_dir, *tail.split("/"))
            if os.path.normpath(os.path.dirname(candidate)) == tools_dir_norm:
                continue
            if tail not in found and os.path.isfile(candidate):
                found.add(tail)
                queue.append(candidate)
    return sorted(found)


def _rewrite_plugin_root_command(command, product):
    """`${CLAUDE_PLUGIN_ROOT}/tools/` moved to where this module mirrors
    `product`'s tools once installed, so the command that actually ships
    points at bytes that actually exist in the plugin."""
    return command.replace(
        "${CLAUDE_PLUGIN_ROOT}/tools/",
        "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/%s/tools/" % product)


def merged_hooks_doc(products=HOOK_PRODUCTS, products_dir=PRODUCTS_DIR):
    """bundle/hooks/hooks.json's content: the union of every named
    product's own hooks.json, each command rewritten to its mirrored
    location, `products`' own order preserved (brothermode's event order
    first, then any event brothersbe alone carries); within one event,
    brothermode's matcher groups first, then brothersbe's. A product
    carrying no hooks.json contributes nothing."""
    docs = [(p, d) for p, d in
           ((p, _load_hooks_json(p, products_dir)) for p in products)
           if d is not None]
    order = []
    for _, doc in docs:
        for event in doc.get("hooks", {}):
            if event not in order:
                order.append(event)
    merged = {}
    for event in order:
        groups = []
        for product, doc in docs:
            for group in doc.get("hooks", {}).get(event, []):
                new_group = {k: v for k, v in group.items() if k != "hooks"}
                new_group["hooks"] = [
                    dict(h, command=_rewrite_plugin_root_command(
                        h.get("command", ""), product))
                    for h in group.get("hooks", [])]
                groups.append(new_group)
        merged[event] = groups
    return {"hooks": merged}


def _hooks_manifest_bytes(products=HOOK_PRODUCTS, products_dir=PRODUCTS_DIR,
                          runtime_dir=RUNTIME_DIR):
    files = []
    for product in products:
        tools_dir, closure = compute_hook_closure(product, products_dir,
                                                  runtime_dir)
        for name in closure:
            data = _read_bytes(os.path.join(tools_dir, name))
            files.append({"path": "%s/tools/%s" % (product, name),
                         "sha256": _sha256(data)})
        for tail in compute_hook_package_files(product, tools_dir, closure,
                                               products_dir):
            data = _read_bytes(os.path.join(os.path.dirname(tools_dir),
                                            *tail.split("/")))
            files.append({"path": "%s/%s" % (product, tail),
                         "sha256": _sha256(data)})
    files.sort(key=lambda f: f["path"])
    return (json.dumps({"generated_by": "scripts/bundle_runtime.py",
                        "products": list(products), "files": files},
                       indent=1, sort_keys=True) + "\n").encode("utf-8")


def _hooks_json_bytes(products=HOOK_PRODUCTS, products_dir=PRODUCTS_DIR):
    return (json.dumps(merged_hooks_doc(products, products_dir), indent=2)
           + "\n").encode("utf-8")


def generate_hooks(products=HOOK_PRODUCTS, products_dir=PRODUCTS_DIR,
                   runtime_dir=RUNTIME_DIR):
    """Mirrors every named product's hook-tool closure into
    bundle/runtime/hooks/<product>/tools/, writes their manifest
    (bundle/runtime/hooks/HOOKS-MANIFEST.json), and writes
    bundle/hooks/hooks.json. Returns (hook_counts, changed): hook_counts is
    {product: command_count reported by that product's own hooks.json},
    changed is the list of paths (relative to the bundle root) written or
    updated."""
    changed = []
    hook_counts = {}
    for product in products:
        tools_dir, closure = compute_hook_closure(product, products_dir,
                                                  runtime_dir)
        doc = _load_hooks_json(product, products_dir)
        hook_counts[product] = count_hook_commands(doc) if doc else 0
        for name in closure:
            data = _read_bytes(os.path.join(tools_dir, name))
            dst = os.path.join(runtime_dir, "hooks", product, "tools", name)
            if _write_if_changed(dst, data):
                changed.append("runtime/hooks/%s/tools/%s" % (product, name))
        for tail in compute_hook_package_files(product, tools_dir, closure,
                                               products_dir):
            data = _read_bytes(os.path.join(os.path.dirname(tools_dir),
                                            *tail.split("/")))
            dst = os.path.join(runtime_dir, "hooks", product,
                               *tail.split("/"))
            if _write_if_changed(dst, data):
                changed.append("runtime/hooks/%s/%s" % (product, tail))
    manifest_path = os.path.join(runtime_dir, "hooks", HOOKS_MANIFEST_NAME)
    if _write_if_changed(manifest_path,
                         _hooks_manifest_bytes(products, products_dir,
                                               runtime_dir)):
        changed.append("runtime/hooks/" + HOOKS_MANIFEST_NAME)
    bundle_dir = os.path.dirname(runtime_dir)
    hooks_json_path = os.path.join(bundle_dir, "hooks", HOOKS_JSON_NAME)
    if _write_if_changed(hooks_json_path,
                         _hooks_json_bytes(products, products_dir)):
        changed.append("hooks/" + HOOKS_JSON_NAME)
    return hook_counts, changed


def check_hooks(products=HOOK_PRODUCTS, products_dir=PRODUCTS_DIR,
               runtime_dir=RUNTIME_DIR):
    """Read-only: do bundle/runtime/hooks/ and bundle/hooks/hooks.json
    match products/*/hooks/ right now? Returns (ok, problems); never
    writes anything."""
    problems = []
    for product in products:
        tools_dir, closure = compute_hook_closure(product, products_dir,
                                                  runtime_dir)
        for name in closure:
            hook_src = os.path.join(tools_dir, name)
            dst = os.path.join(runtime_dir, "hooks", product, "tools", name)
            if not os.path.isfile(dst):
                problems.append("runtime/hooks/%s/tools/%s: missing from "
                                "bundle/runtime" % (product, name))
            elif _read_bytes(hook_src) != _read_bytes(dst):
                problems.append("runtime/hooks/%s/tools/%s: bundle/runtime "
                                "copy does not match its products/ source"
                                % (product, name))
        # A file the mirror already ships but whose products/ source has
        # since been deleted entirely (never in `closure`, because
        # _closure_from_entries silently drops a name that is not among the
        # real files it can walk): the ultimate drift, since it cannot be
        # regenerated from any source at all. F-mirror-drift, 2026-09-11.
        for name in _mirrored_tool_names(product, runtime_dir):
            if name not in closure and not os.path.isfile(
                    os.path.join(tools_dir, name)):
                problems.append("runtime/hooks/%s/tools/%s: ships in "
                                "bundle/runtime but products/%s/tools/%s no "
                                "longer exists" % (product, name, product,
                                                   name))
        for tail in compute_hook_package_files(product, tools_dir, closure,
                                               products_dir):
            pkg_src = os.path.join(os.path.dirname(tools_dir),
                                   *tail.split("/"))
            dst = os.path.join(runtime_dir, "hooks", product,
                               *tail.split("/"))
            if not os.path.isfile(dst):
                problems.append("runtime/hooks/%s/%s: missing from "
                                "bundle/runtime" % (product, tail))
            elif _read_bytes(pkg_src) != _read_bytes(dst):
                problems.append("runtime/hooks/%s/%s: bundle/runtime copy "
                                "does not match its products/ source"
                                % (product, tail))
    manifest_path = os.path.join(runtime_dir, "hooks", HOOKS_MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        problems.append("runtime/hooks/%s: missing" % HOOKS_MANIFEST_NAME)
    elif _read_bytes(manifest_path) != _hooks_manifest_bytes(
            products, products_dir, runtime_dir):
        problems.append("runtime/hooks/%s: stale, does not match a fresh "
                        "generation" % HOOKS_MANIFEST_NAME)
    bundle_dir = os.path.dirname(runtime_dir)
    hooks_json_path = os.path.join(bundle_dir, "hooks", HOOKS_JSON_NAME)
    if not os.path.isfile(hooks_json_path):
        problems.append("hooks/%s: missing" % HOOKS_JSON_NAME)
    elif _read_bytes(hooks_json_path) != _hooks_json_bytes(products,
                                                            products_dir):
        problems.append("hooks/%s: stale, does not match a fresh "
                        "generation from the products' own hooks.json"
                        % HOOKS_JSON_NAME)
    return (not problems), problems


def compute_data_files(closure, scripts_dir=SCRIPTS_DIR):
    """Relative paths (POSIX "/" separators, e.g. "packs/core.json") under
    every DATA_DIRS directory that a closure file references by a bare
    string-constant name, the same rule compute_closure uses for sibling
    .py files. A directory that no closure file names, or that does not
    exist under scripts_dir, contributes nothing. Files are walked with
    os.walk, dirnames and filenames sorted for determinism, skipping only
    __pycache__ directories and .pyc files."""
    referenced = set()
    for name in closure:
        path = os.path.join(scripts_dir, name)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value in DATA_DIRS):
                referenced.add(node.value)

    files = []
    for name in DATA_DIRS:
        if name not in referenced:
            continue
        data_dir = os.path.join(scripts_dir, name)
        if not os.path.isdir(data_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(data_dir):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for filename in sorted(filenames):
                if filename.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, scripts_dir)
                files.append(rel.replace(os.sep, "/"))
    return sorted(files)


def compute_extra_files(closure, scripts_dir=SCRIPTS_DIR):
    """[(name inside bundle/runtime, absolute source path)] for every
    DATA_FILES entry whose reader is in `closure` and whose source exists,
    sorted by name. The repository root is taken as the parent of
    `scripts_dir`, so a temp-tree generation resolves against that tree
    rather than against this checkout."""
    repo_root = os.path.dirname(os.path.abspath(scripts_dir))
    out = []
    for reader, (source, dest) in sorted(DATA_FILES.items()):
        if reader not in closure:
            continue
        src = os.path.join(repo_root, *source.split("/"))
        if os.path.isfile(src):
            out.append((dest, src))
    return sorted(out)


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
    files.append({"path": VERIFIER_NAME,
                  "sha256": _sha256(VERIFIER_SOURCE.encode("utf-8"))})
    for rel in compute_data_files(closure, scripts_dir):
        src = os.path.join(scripts_dir, *rel.split("/"))
        files.append({"path": rel, "sha256": _sha256(_read_bytes(src))})
    for dest, src in compute_extra_files(closure, scripts_dir):
        files.append({"path": dest, "sha256": _sha256(_read_bytes(src))})
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
    for rel in compute_data_files(closure, scripts_dir):
        data = _read_bytes(os.path.join(scripts_dir, *rel.split("/")))
        if _write_if_changed(os.path.join(runtime_dir, *rel.split("/")), data):
            changed.append(rel)
    for dest, src in compute_extra_files(closure, scripts_dir):
        if _write_if_changed(os.path.join(runtime_dir, dest),
                             _read_bytes(src)):
            changed.append(dest)
    launcher_path = os.path.join(runtime_dir, LAUNCHER_NAME)
    if _write_if_changed(launcher_path, LAUNCHER_SOURCE.encode("utf-8")):
        changed.append(LAUNCHER_NAME)
    os.chmod(launcher_path, 0o755)
    verifier_path = os.path.join(runtime_dir, VERIFIER_NAME)
    if _write_if_changed(verifier_path, VERIFIER_SOURCE.encode("utf-8")):
        changed.append(VERIFIER_NAME)
    os.chmod(verifier_path, 0o755)
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
    data_files = compute_data_files(closure, scripts_dir)
    for rel in data_files:
        dst = os.path.join(runtime_dir, *rel.split("/"))
        if not os.path.isfile(dst):
            problems.append("%s: missing from bundle/runtime" % rel)
        elif (_read_bytes(os.path.join(scripts_dir, *rel.split("/")))
              != _read_bytes(dst)):
            problems.append("%s: bundle/runtime copy does not match its "
                            "scripts/ source" % rel)
    for dest, src in compute_extra_files(closure, scripts_dir):
        dst = os.path.join(runtime_dir, dest)
        if not os.path.isfile(dst):
            problems.append("%s: missing from bundle/runtime, so an "
                            "installed copy has no schema to check a record "
                            "against" % dest)
        elif _read_bytes(src) != _read_bytes(dst):
            problems.append("%s: bundle/runtime copy does not match its "
                            "source outside scripts/" % dest)
    launcher_path = os.path.join(runtime_dir, LAUNCHER_NAME)
    if not os.path.isfile(launcher_path):
        problems.append("%s: missing from bundle/runtime" % LAUNCHER_NAME)
    elif _read_bytes(launcher_path) != LAUNCHER_SOURCE.encode("utf-8"):
        problems.append("%s: does not match this generator's current "
                        "launcher source" % LAUNCHER_NAME)
    verifier_path = os.path.join(runtime_dir, VERIFIER_NAME)
    if not os.path.isfile(verifier_path):
        problems.append("%s: missing from bundle/runtime, so an installed "
                        "copy has no way to check its own manifest"
                        % VERIFIER_NAME)
    elif _read_bytes(verifier_path) != VERIFIER_SOURCE.encode("utf-8"):
        problems.append("%s: does not match this generator's current "
                        "verifier source" % VERIFIER_NAME)
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

    # bundle/codex-skills is the OTHER generated thing under bundle/, and it
    # is generated from bundle/skills rather than from scripts/, so it lives
    # in its own sibling module (scripts/codex_skills.py) instead of being
    # bolted into this file's scripts/-closure logic. It is driven from here
    # so that the one command every lane already runs, and the one the common
    # brief names as a done-check, covers both mirrors: a generated directory
    # nobody's routine check regenerates goes stale silently.
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import codex_skills as CS  # noqa: E402  (sibling, same directory)

    if args.check:
        ok, problems, closure = check()
        cs_ok, cs_problems = CS.check()
        hooks_ok, hooks_problems = check_hooks()
        if ok and cs_ok and hooks_ok:
            total_hook_commands = sum(
                count_hook_commands(_load_hooks_json(p) or {"hooks": {}})
                for p in HOOK_PRODUCTS)
            print("bundle_runtime: bundle/runtime matches scripts/ for all "
                  "%d closure file(s) and %d data file(s), bundle/codex-skills "
                  "matches bundle/skills, and bundle/hooks/hooks.json matches "
                  "%d hook command(s) across %d product(s)"
                  % (len(closure), len(compute_data_files(closure)),
                     total_hook_commands, len(HOOK_PRODUCTS)))
            return 0
        for problem in problems + cs_problems + hooks_problems:
            print("bundle_runtime: DRIFT: %s" % problem, file=sys.stderr)
        return 1

    cs_changed, cs_problems = CS.generate()
    if cs_problems:
        for problem in cs_problems:
            print("bundle_runtime: FAIL: %s" % problem, file=sys.stderr)
        return 1

    closure, changed = generate()
    hook_counts, hooks_changed = generate_hooks()
    changed = (changed + ["codex-skills/" + c for c in cs_changed]
              + hooks_changed)
    if changed:
        print("bundle_runtime: wrote %d file(s): %s"
              % (len(changed), ", ".join(changed)))
    else:
        print("bundle_runtime: no changes; bundle/runtime already matches "
              "scripts/ for %d closure file(s) and %d data file(s)"
              % (len(closure), len(compute_data_files(closure))))
    print("bundle_runtime: bundle/hooks/hooks.json carries %d hook "
          "command(s) across %d product(s) (%s)"
          % (sum(hook_counts.values()), len(hook_counts),
             ", ".join("%s=%d" % (p, n) for p, n in hook_counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

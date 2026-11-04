#!/usr/bin/env python3
"""test_ship_every_route: every capability the door routes ships inside bundle/runtime (SHIP1.a).

Owner ruling 2026-11-01, his words: "routed but not shipped: this is unacceptable all the modules have to actually
work and be shipped properly". The marketplace installs bundle/ alone, so a target the door launches that is not
under bundle/runtime answers NO-DATA on every installed machine however well it works in a checkout.

THE ROUTE TABLE is read, never typed here: scripts/door.py is imported for its own rows and tool finders (the
mobile scripts of MOBILE_CAPABILITIES, the host tools of HOST_CAPABILITIES, vault_tool, data_tool and
assurance_tool), and docs/plan/REACH.json for the command each capability routes a verb to and the source owners of
every inventory group it marks routed. A target ships when a byte identical copy sits where the installed plugin
carries that tree: scripts/X at runtime/X, scripts/loop/X at runtime/loop/X, products/<p>/X at
runtime/products/<p>/X or runtime/hooks/<p>/X, data/X at runtime/data/X, AND the shipped manifest for that tree
(RUNTIME-, HOOKS- or LOOP-MANIFEST.json) attests that copy's sha256, since an unlisted leftover is a file no install
can verify. A command that is instructions rather than
code (a SKILL.md) ships with the skills, not the runtime, and is not counted here.

    python3 scripts/test_ship_every_route.py [--repo DIR]
    exit 0 PASS (every routed target ships), 1 FAIL (one line per routed group or door row whose target is absent
    or stale), 2 NO-DATA (the door cannot be imported or REACH.json cannot be read, so the route table is unknown)

Before the real tree it runs its own fixtures, each refusable by one guard only (a missing owner, a stale copy, a
copy no manifest attests, a missing door row target, a missing capability command, an unreadable registry, a door
that will not import); a fixture that answers wrongly is a
FAIL of this row, never a skip. Python 3.9, standard library, writes only under a temp folder it removes."""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NODATA = "NO-DATA"
#: export_public.NOT_IN_THIS_EDITION, the reason the export writes on a row whose owner it withholds.
NOT_IN_EDITION = "not in this edition"
CODE_SUFFIXES = (".py", ".sh")


class RouteTableUnreadable(Exception):
    """The door or REACH.json could not be read: the answer is NO-DATA, never a pass."""


def load_door(repo):
    path = os.path.join(repo, "scripts", "door.py")
    try:
        spec = importlib.util.spec_from_file_location("_ship_every_route_door", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows = (mod.MOBILE_CAPABILITIES, mod.HOST_CAPABILITIES, mod.vault_tool, mod.data_tool, mod.assurance_tool,
                tuple(getattr(mod, "MAINTAINER_ONLY_ROUTES", ()) or ()))
    except (Exception, SystemExit) as exc:  # any failure to read the door is an unknown route table
        raise RouteTableUnreadable("scripts/door.py could not be imported (%s: %s)" % (type(exc).__name__, exc))
    return rows


def load_reach(repo):
    path = os.path.join(repo, "docs", "plan", "REACH.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RouteTableUnreadable("docs/plan/REACH.json could not be read (%s)" % exc)
    if not (isinstance(doc, dict) and isinstance(doc.get("capabilities"), list)
            and isinstance(doc.get("inventory"), list)):
        raise RouteTableUnreadable("docs/plan/REACH.json has no capabilities and inventory lists")
    return doc


def is_code(rel):
    base = rel.rsplit("/", 1)[-1]
    return rel.endswith(CODE_SUFFIXES) or "." not in base


def shipped_copies(rel):
    """Where an installed plugin carries the repository file `rel`, as paths under bundle/; [] when no tree does."""
    parts = rel.split("/")
    if parts[0] == "bundle":
        return [rel]
    if parts[0] == "runtime":
        return ["bundle/" + rel]
    if parts[0] == "scripts":
        return ["bundle/runtime/" + "/".join(parts[1:])]
    if parts[0] == "data":
        return ["bundle/runtime/" + rel]
    if parts[0] == "products" and len(parts) > 2:
        tail = "/".join(parts[2:])
        return ["bundle/runtime/products/%s/%s" % (parts[1], tail), "bundle/runtime/hooks/%s/%s" % (parts[1], tail)]
    if parts[0] == "plugin" and rel.endswith(".py"):
        return ["bundle/runtime/" + parts[-1]]
    return []


def _read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


#: (bundle relative manifest, the folder its entries are relative to, the key naming an entry's path)
MANIFESTS = (("bundle/runtime/RUNTIME-MANIFEST.json", "bundle/runtime", "path"),
             ("bundle/runtime/hooks/HOOKS-MANIFEST.json", "bundle/runtime/hooks", "path"),
             ("bundle/runtime/loop/LOOP-MANIFEST.json", "bundle/runtime/loop", "name"))


def load_attested(repo):
    """{bundle relative path: sha256} for every file a shipped manifest attests, plus each manifest itself (it is
    what an install verifies against). A manifest that is missing or unreadable attests nothing, so every target it
    should cover reads as not shipped, never as shipped."""
    attested = {}
    for manifest, base, key in MANIFESTS:
        data = _read(os.path.join(repo, *manifest.split("/")))
        try:
            doc = json.loads(data.decode("utf-8")) if data is not None else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
            continue
        attested[manifest] = hashlib.sha256(data).hexdigest()
        for entry in doc["files"]:
            if isinstance(entry, dict) and isinstance(entry.get(key), str) and isinstance(entry.get("sha256"), str):
                attested[base + "/" + entry[key]] = entry["sha256"]
    return attested


def copy_problem(repo, rel, copy, attested):
    """None when the bundle file `copy` is a real file (never a symlink, never empty unless its source is), byte
    identical to the repository file `rel` (a bundle/ or runtime/ path is its own source) AND attested by a shipped
    manifest (so an installed verify_runtime.py can check it), else why not."""
    path = os.path.join(repo, *copy.split("/"))
    if os.path.islink(path):
        return "%s: the shipped copy %s is a symlink, which an install does not carry" % (rel, copy)
    got = _read(path)
    if got is None:
        return "%s: absent from bundle/runtime" % rel
    source = _read(os.path.join(repo, *rel.split("/"))) if not rel.startswith(("bundle/", "runtime/")) else None
    if not got and (source is None or source):
        return "%s: the shipped copy %s is empty" % (rel, copy)
    if source is not None and got != source:
        return "%s: the shipped copy %s is stale" % (rel, copy)
    if attested.get(copy) != hashlib.sha256(got).hexdigest():
        return "%s: %s is on disk but no shipped manifest attests it" % (rel, copy)
    return None


def ship_problem(repo, rel, attested):
    """None when `rel` ships where an installed plugin carries its tree (copy_problem), else why not."""
    copies = shipped_copies(rel)
    if not copies:
        return "%s: no installed tree carries this path" % rel
    for copy in copies:
        if os.path.lexists(os.path.join(repo, *copy.split("/"))):
            return copy_problem(repo, rel, copy, attested)
    return "%s: absent from bundle/runtime" % rel


def load_installed_door(repo):
    """(the door the plugin installs, bundle/runtime/door.py, imported where it ships, or None, why not). An
    installed door that is absent or will not import routes nothing, which is a ship defect: FAIL, not NO-DATA."""
    path = os.path.join(repo, "bundle", "runtime", "door.py")
    if not os.path.isfile(path):
        return None, "bundle/runtime/door.py is absent"
    try:
        spec = importlib.util.spec_from_file_location("_ship_every_route_installed_door", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (Exception, SystemExit) as exc:
        return None, "bundle/runtime/door.py does not import (%s: %s)" % (type(exc).__name__, exc)
    return mod, None


def installed_problem(repo, rel, door, finder):
    """None when the INSTALLED door, asked its own way (`finder(door)`), finds a file inside the installed plugin
    (bundle/), else why not. A copy that ships where the door never looks is routed but not reached."""
    try:
        found = finder(door)
    except (Exception, SystemExit) as exc:
        return "%s: the installed door's finder raised %s: %s" % (rel, type(exc).__name__, exc)
    if not found or not os.path.isfile(found):
        return "%s: the installed door finds no copy of it (%s)" % (rel, found or "none")
    root = os.path.realpath(os.path.join(repo, "bundle"))
    if not os.path.realpath(found).startswith(root + os.sep):
        return "%s: the installed door finds it outside the installed plugin, at %s" % (rel, found)
    source = _read(os.path.join(repo, *rel.split("/")))
    if source is not None and _read(found) != source:
        return "%s: the installed door finds %s, which is not a copy of it" % (rel, found)
    return None


def reach_finder(rel):
    """How the installed door resolves a registry path: its _route_file (OUT1.n: an install keeps scripts flat beside
    the door) when it has one, else its repo_path."""
    return lambda door: getattr(door, "_route_file", door.repo_path)(rel)


def door_targets(repo, rows):
    """[(row label, repository relative target, finder asking the installed door for it, or None)] from the door's
    own tables and finders. A host row's tool is a path inside the installed plugin, so it has no finder to ask."""
    mobile, host, vault_tool, data_tool, assurance_tool = rows[:5]
    out = [("door %s" % r["id"], "scripts/%s.py" % r["script"],
            (lambda script: lambda door: door.mobile_tool(script))(r["script"])) for r in mobile]
    out += [("door %s" % r["id"], r["tool"], None) for r in host]
    for label, finder, name in (("door vault.*", vault_tool, "vault_tool"), ("door data.*", data_tool, "data_tool"),
                                ("door assurance.*", assurance_tool, "assurance_tool")):
        path = finder()
        rel = os.path.relpath(path, repo) if path else None
        if rel is None or rel.startswith(".."):
            out.append((label, "(%s: the door finds no tool inside this tree)" % (path or "none"), None))
        else:
            out.append((label, rel.replace(os.sep, "/"), (lambda n: lambda door: getattr(door, n)())(name)))
    return out


def check(repo):
    """(problem lines, counted targets, non code capability commands). Raises RouteTableUnreadable. Every target must ship (ship_problem) AND,
    where the door launches it, be found by the installed door itself (installed_problem): its mobile_tool,
    vault_tool, data_tool and assurance_tool, and its registry path resolution for a capability's scripts/ command.
    An inventory owner ships in its own tree; that is its test."""
    rows, doc = load_door(repo), load_reach(repo)
    attested = load_attested(repo)
    door, why_not = load_installed_door(repo)
    problems, counted = [], 0
    if door is None:
        problems.append("FAIL the installed door: %s" % why_not)

    def problem(rel, finder):
        why = ship_problem(repo, rel, attested) if not rel.startswith("(") else rel
        if not why and door is not None and finder is not None:
            why = installed_problem(repo, rel, door, finder)
        return why

    # An inventory owner is reached by whatever launches it (the loop mirror, a product launcher, a flat script the
    # door or another owner runs), so shipping in its own tree is its test; a capability's command is resolved by the
    # door's registry rule (recommend, _route_entry_ok), so every code command, scripts/ or products/, asks the
    # installed door.
    out_of_scope = []
    for group in doc["inventory"]:
        if not (isinstance(group, dict) and group.get("routed")):
            continue
        if group["routed"] in rows[5]:
            # A group a maintainer-only route reaches is maintainer tooling too: none of its owners may ship.
            shipped = [o for o in group.get("source_owners") or []
                       if isinstance(o, str) and is_code(o) and ship_problem(repo, o, attested) is None]
            if shipped:
                problems.append("FAIL %s (routed by maintainer-only %s): ships %s"
                                % (group.get("id"), group["routed"], ", ".join(shipped)))
            continue
        owners = [o for o in group.get("source_owners") or [] if isinstance(o, str) and is_code(o)]
        if group.get("code_exists_reason") == NOT_IN_EDITION:
            # The export's mark (export_public.mark_unexported_owners): an owner this edition does not carry has
            # nothing to ship. Only the owner actually absent is excused; a present one still has to ship.
            owners = [o for o in owners if os.path.exists(os.path.join(repo, *o.split("/")))]
        why = [p for p in (problem(o, None) for o in owners) if p]
        counted += len(owners)
        if why:
            problems.append("FAIL %s (routed by %s): %s" % (group.get("id"), group["routed"], "; ".join(why)))
    for cap in doc["capabilities"]:
        command = cap.get("command") if isinstance(cap, dict) else None
        if isinstance(cap, dict) and cap.get("id") in rows[5] and isinstance(command, str):
            # Maintainer tooling (door.MAINTAINER_ONLY_ROUTES): its absence from the install IS the property.
            if is_code(command) and ship_problem(repo, command, attested) is None:
                problems.append("FAIL capability %s: maintainer-only, but %s ships in the install"
                                % (cap.get("id"), command))
            else:
                out_of_scope.append("%s (maintainer only, not shipped)" % cap.get("id"))
            continue
        if isinstance(command, str) and is_code(command):
            counted += 1
            why = problem(command, reach_finder(command))
            if why:
                problems.append("FAIL capability %s: %s" % (cap.get("id"), why))
        elif isinstance(command, str):
            out_of_scope.append("%s (%s)" % (cap.get("id"), command))
    by_target = {}
    for label, rel, finder in door_targets(repo, rows):
        by_target.setdefault((rel, finder is not None), []).append((label, finder))
    for (rel, _asked), entries in sorted(by_target.items(), key=lambda kv: kv[0]):
        counted += 1
        why = problem(rel, entries[0][1])
        if why:
            problems.append("FAIL %s: %s" % (", ".join(sorted(set(l for l, _f in entries))), why))
    return problems, counted, out_of_scope


def run(repo):
    """(exit code, lines). The summary line names every capability whose command is not code (a SKILL.md ships with
    the skills, not the runtime), so nothing is left out without saying so."""
    try:
        problems, counted, out_of_scope = check(repo)
    except RouteTableUnreadable as exc:
        return 2, ["%s: the route table is unknown: %s" % (NODATA, exc)]
    scope = ("; %d non code command(s) out of scope, not checked here: %s" % (len(out_of_scope), ", ".join(
        out_of_scope))) if out_of_scope else ""
    if problems:
        return 1, problems + ["FAIL: %d routed target(s) checked, %d row(s) not shipped%s"
                              % (counted, len(problems), scope)]
    return 0, ["PASS: all %d routed target(s) ship inside bundle/runtime%s" % (counted, scope)]


# ---- fixtures: each one isolates the single guard that must refuse it ----

FIXTURE_DOOR = '''import os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MOBILE_CAPABILITIES = ({"id": "mobile.doctor", "script": "mobile_workflow"},)
HOST_CAPABILITIES = ({"id": "host.verify", "tool": "runtime/verify_runtime.py"},)
def _first(*paths):
    return next((p for p in paths if os.path.isfile(p)), None)
def vault_tool():
    return _first(os.path.join(ROOT, "products", "brothermode", "tools", "bm_vault.py"),
                  os.path.join(HERE, "hooks", "brothermode", "tools", "bm_vault.py"))
def data_tool():
    return _first(os.path.join(ROOT, "products", "brotherds", "bds.py"),
                  os.path.join(HERE, "products", "brotherds", "bds.py"))
def assurance_tool():
    return _first(os.path.join(ROOT, "products", "brothersbe", "bin", "sbe"),
                  os.path.join(HERE, "products", "brothersbe", "bin", "sbe"))
def mobile_tool(script):
    return _first(os.path.join(HERE, script + ".py"))
def repo_path(rel):
    return os.path.realpath(os.path.join(ROOT, rel))
def _route_file(cmd):
    full = repo_path(cmd)
    if os.path.isfile(full):
        return full
    if cmd.startswith("scripts/") and "/" not in cmd[len("scripts/"):]:
        return _first(os.path.join(HERE, cmd[len("scripts/"):]))
    parts = cmd.split("/")
    if parts[0] == "products" and len(parts) > 2:
        return _first(os.path.join(HERE, *parts), os.path.join(HERE, "hooks", *parts[1:]))
    return None
'''
FIXTURE_REACH = {
    "capabilities": [{"id": "cut-release", "command": "scripts/cut.py"},
                     {"id": "verify-change", "command": "products/brothersbe/tools/sbe_gate.py"},
                     {"id": "what-next", "command": "products/brothermode/skills/next/SKILL.md"}],
    "inventory": [{"id": "loop-close", "routed": "overnight-loop", "source_owners": ["scripts/close_unit.py"]},
                  {"id": "unrouted", "routed": None, "source_owners": ["scripts/never_shipped.py"]},
                  {"id": "withheld", "routed": "check-a-number", "source_owners": ["scripts/withheld.py"],
                   "code_exists": False, "code_exists_reason": NOT_IN_EDITION}],
}
FIXTURE_FILES = {
    "scripts/mobile_workflow.py": "bundle/runtime/mobile_workflow.py",
    "scripts/cut.py": "bundle/runtime/cut.py",
    "scripts/close_unit.py": "bundle/runtime/close_unit.py",
    "products/brothermode/tools/bm_vault.py": "bundle/runtime/hooks/brothermode/tools/bm_vault.py",
    "products/brotherds/bds.py": "bundle/runtime/products/brotherds/bds.py",
    "products/brothersbe/bin/sbe": "bundle/runtime/products/brothersbe/bin/sbe",
    "products/brothersbe/tools/sbe_gate.py": "bundle/runtime/hooks/brothersbe/tools/sbe_gate.py",
    "bundle/runtime/verify_runtime.py": None,
    "scripts/never_shipped.py": None,
    "scripts/door.py": "bundle/runtime/door.py",
}


def _write(root, rel, data):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)


def _fixture(root):
    _write(root, "docs/plan/REACH.json", json.dumps(FIXTURE_REACH))
    rows = {base: [] for _m, base, _k in MANIFESTS}
    for src, dst in FIXTURE_FILES.items():
        body = FIXTURE_DOOR if src == "scripts/door.py" else "# %s\n" % src
        _write(root, src, body)
        for copy in [dst] if dst else ([src] if src.startswith("bundle/") else []):
            _write(root, copy, body)
            base = max((b for b in rows if copy.startswith(b + "/")), key=len)
            rows[base].append((copy[len(base) + 1:], hashlib.sha256(body.encode()).hexdigest()))
    for manifest, base, key in MANIFESTS:
        _write(root, manifest, json.dumps({"files": [{key: n, "sha256": h} for n, h in rows[base]]}))


def _unattest(root, manifest, name):
    path = os.path.join(root, *manifest.split("/"))
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["files"] = [f for f in doc["files"] if f.get("path") != name]
    _write(root, manifest, json.dumps(doc))


def _replace(root, copy, data):
    """Rewrite the shipped file `copy` AND re-attest it, so only the guard the new bytes trip can refuse it."""
    _write(root, copy, data)
    manifest, base, key = max((m for m in MANIFESTS if copy.startswith(m[1] + "/")), key=lambda m: len(m[1]))
    path = os.path.join(root, *manifest.split("/"))
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for entry in doc["files"]:
        if entry.get(key) == copy[len(base) + 1:]:
            entry["sha256"] = hashlib.sha256(data.encode()).hexdigest()
    _write(root, manifest, json.dumps(doc))


def _installed_door(root, old, new):
    _replace(root, "bundle/runtime/door.py", FIXTURE_DOOR.replace(old, new))


def _symlink_copy(root):
    path = os.path.join(root, "bundle", "runtime", "cut.py")
    os.remove(path)
    os.symlink(os.path.join(root, "scripts", "cut.py"), path)


def fixtures():
    """[failure lines]; empty when every fixture answered as it must."""
    cases = (
        ("all ship", lambda r: None, 0,
         "; 1 non code command(s) out of scope, not checked here: what-next (products/brothermode/skills/next/SKILL.md)"),
        ("a routed owner missing", lambda r: os.remove(os.path.join(r, "bundle/runtime/close_unit.py")),
         1, "FAIL loop-close"),
        ("a stale shipped copy", lambda r: _write(r, "bundle/runtime/products/brotherds/bds.py", "# old\n"),
         1, "door data.*: products/brotherds/bds.py: the shipped copy"),
        ("a door row target missing", lambda r: os.remove(os.path.join(r, "bundle/runtime/mobile_workflow.py")),
         1, "FAIL door mobile.doctor"),
        ("a copy no manifest attests",
         lambda r: _unattest(r, "bundle/runtime/RUNTIME-MANIFEST.json", "close_unit.py"),
         1, "bundle/runtime/close_unit.py is on disk but no shipped manifest attests it"),
        ("a capability command missing", lambda r: os.remove(os.path.join(r, "bundle/runtime/cut.py")),
         1, "FAIL capability cut-release"),
        ("an edition-marked owner this tree still carries, unshipped",
         lambda r: _write(r, "scripts/withheld.py", "# present\n"), 1, "FAIL withheld"),
        ("an installed door that never looks where its copy ships",
         lambda r: _installed_door(r, ',\n                  os.path.join(HERE, "products", "brotherds", "bds.py"))', ')'),
         1, "door data.*: products/brotherds/bds.py: the installed door finds no copy"),
        ("an installed door that finds the checkout's copy",
         lambda r: _installed_door(r, 'os.path.join(HERE, "products", "brotherds", "bds.py")',
                                   'os.path.join(os.path.dirname(ROOT), "products", "brotherds", "bds.py")'),
         1, "door data.*: products/brotherds/bds.py: the installed door finds it outside the installed plugin"),
        ("an installed door that finds a missing file inside the plugin",
         lambda r: _installed_door(r, 'return _first(os.path.join(ROOT, "products", "brotherds", "bds.py"),',
                                   'return os.path.join(HERE, "products", "brotherds", "gone.py")\n    return ('),
         1, "door data.*: products/brotherds/bds.py: the installed door finds no copy"),
        ("an installed door that finds another shipped file",
         lambda r: _installed_door(r, 'os.path.join(HERE, "products", "brotherds", "bds.py")',
                                   'os.path.join(HERE, "products", "brothersbe", "bin", "sbe")'),
         1, "door data.*: products/brotherds/bds.py: the installed door finds"),
        ("an installed door whose registry rule misses products/",
         lambda r: _installed_door(r, 'if parts[0] == "products"', 'if False'),
         1, "FAIL capability verify-change: products/brothersbe/tools/sbe_gate.py: the installed door finds no copy"),
        ("an installed door with no flat registry resolver", lambda r: _installed_door(r, "def _route_file", "def _gone"),
         1, "FAIL capability cut-release: scripts/cut.py: the installed door finds no copy", 2),
        ("an installed door that will not import", lambda r: _replace(r, "bundle/runtime/door.py", "raise SystemExit(3)\n"),
         1, "FAIL the installed door: bundle/runtime/door.py does not import"),
        ("a symlinked shipped copy", _symlink_copy, 1, "bundle/runtime/cut.py is a symlink"),
        ("an empty shipped host tool", lambda r: _replace(r, "bundle/runtime/verify_runtime.py", ""),
         1, "bundle/runtime/verify_runtime.py is empty"),
        ("an unreadable registry", lambda r: _write(r, "docs/plan/REACH.json", "{not json"), 2, NODATA),
        ("a door that will not import", lambda r: _write(r, "scripts/door.py", "raise SystemExit(3)\n"), 2, NODATA),
    )
    bad = []
    for name, break_it, want_rc, want_text, *rest in cases:
        want_rows = rest[0] if rest else 1
        root = tempfile.mkdtemp(prefix="ship-every-route-")
        try:
            _fixture(root)
            break_it(root)
            rc, lines = run(root)
            text = "\n".join(lines)
            if rc != want_rc or want_text not in text or (want_rc == 1 and len(lines) != want_rows + 1):
                bad.append("fixture %r answered exit %d: %s" % (name, rc, text))
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=REPO, help="repository root (default: this checkout)")
    args = ap.parse_args(argv)
    bad = fixtures()
    if bad:
        for line in bad:
            print("FAIL %s" % line)
        print("FAIL: this row's own fixtures did not answer as they must, so its verdict on the tree is not trusted")
        return 1
    rc, lines = run(os.path.abspath(args.repo))
    for line in lines:
        print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())

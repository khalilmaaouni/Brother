#!/usr/bin/env python3
"""Tests for tools/bm_effects.py, the effect-class registry.

WHAT THIS SUITE PROVES
  1. Every REGISTRY value is one of the five declared classes (a typo in
     a class string fails loudly, not silently).
  2. declared() raises on an undeclared (module, command) pair rather
     than defaulting to anything.
  3. COMPLETENESS: every `def cmd_<name>` this suite can mechanically
     discover across every tools/bm_*.py and tools/brothermode_cli.py is
     declared in REGISTRY. A module whose dispatch is a plain if/elif
     chain rather than a lookup dict (tools/bm_ledger.py,
     tools/bm_telemetry.py, tools/bm_autosave.py) cannot be resolved to a
     verb by this mechanical scan; those commands are named as NO-DATA
     rather than silently passed over, and are not required to appear in
     REGISTRY, because inventing a verb mapping this suite cannot verify
     is exactly the kind of guess REGISTRY exists to refuse.
  4. PURITY: every command declared pure_read is run as a real
     subprocess against a real, freshly initialized throwaway project,
     and the project directory (store file, WAL/SHM sidecars, every
     generated file) must come out byte-identical. A command documented
     as read-only that actually writes fails HERE, which is the point:
     this suite exists to catch the eighth instance of that bug before
     it merges.
  5. --HELP PURITY: `<module> --help` (the bare form) for every module in
     REGISTRY, and `<module> <verb> --help` for every pure_read command
     (see TestHelpPurity's own docstring for why the verb-level form is
     also necessary to catch tools/bm_threads.py's documented "ignores
     --help entirely" defect), must both write nothing.

ISOLATION (load-bearing, read before touching this file)
  BROTHERMODE_VAULT is exported ambient on the machine this project
  usually runs on, and it WINS over HOME in every module that resolves a
  vault path. A HOME-only override does NOT isolate a subprocess from a
  real founder vault; this is the exact incident
  tools/test_bm_packaging_install.py's setUpClass documents and guards
  against, and this suite copies that pattern rather than re-deriving it:
  every subprocess here runs with a from-scratch environment that pins
  HOME, BROTHERMODE_VAULT, BROTHERSBE_VAULT and BROTHERMODE_ROOT inside
  one throwaway tempfile tree, never inheriting the invoking shell's
  environment.

No wall-clock timing assertions anywhere in this file (see
tools/bm_lint_walltime.py, which this file itself must pass clean).

Python 3.9, standard library only. Run:
  python3 tools/test_bm_effects.py
"""
import ast
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS_DIR = HERE

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("bm_effects", os.path.join(HERE, "bm_effects.py"))
E = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(E)
sys.modules["bm_effects"] = E

# Loaded the same way, for its SCHEMA_VERSION constant only (see
# TestPurityUnderAStoreThatIsBehind's put_it_behind(), which sets the
# throwaway store to exactly SCHEMA_VERSION - 1 rather than decrementing
# whatever value it finds there). Reading it fresh from the module under
# test, rather than hardcoding today's number here, is the whole point:
# a hardcoded 17 would silently stop meaning "one behind" the next time
# SCHEMA_VERSION bumps.
_bs_spec = _ilu.spec_from_file_location(
    "bm_store_for_bm_effects_tests", os.path.join(HERE, "bm_store.py"))
_BS = _ilu.module_from_spec(_bs_spec)
_bs_spec.loader.exec_module(_BS)


# ---------------------------------------------------------------------------
# Mechanical discovery: every `def cmd_<name>` in a module, mapped to the
# verb (or verbs) a founder would type to reach it, by reading the
# module's own module-level dict-dispatch tables. Handles one level of
# nested routing (tools/bm_project.py's `task`/`forecast`/`alert` ->
# TASK_COMMANDS/FORECAST_COMMANDS/ALERT_COMMANDS), which is the only
# nested shape this codebase actually uses. A module with no such dict at
# all (an if/elif chain on sys.argv) yields nothing discoverable; every
# cmd_ function in it is reported as NO-DATA instead of being silently
# skipped.
# ---------------------------------------------------------------------------

def _own_scope_nodes(body):
    """Every descendant node reachable from `body` without crossing into a
    nested function/lambda/class, which owns its own scope. Mirrors
    tools/bm_lint_walltime.py's own `_own_scope_nodes`."""
    stack = list(body)
    out = []
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _cmd_functions(tree):
    """{name: FunctionDef} for every module-level `def cmd_<x>`."""
    return {node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_")}


def _dispatch_tables(tree, cmd_funcs):
    """{dict_var_name: {key_string: funcname}} for every module-level dict
    literal whose keys are all string constants and whose values are all
    Name references to a known `cmd_<x>` function. This is the actual
    shape every dispatch table in this codebase uses
    (COMMANDS/_COMMANDS/TASK_COMMANDS/... alike); anything else (a dict
    built with dict(), a comprehension, a value that is not a bare name)
    is not treated as a dispatch table, on purpose: guessing at a shape
    this scan cannot verify is worse than reporting NO-DATA."""
    tables = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
            continue
        d = node.value
        if not d.keys:
            continue
        entries = {}
        ok = True
        for k, v in zip(d.keys, d.values):
            if k is None or not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                ok = False
                break
            if not (isinstance(v, ast.Name) and v.id in cmd_funcs):
                ok = False
                break
            entries[k.value] = v.id
        if not ok or not entries:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                tables[target.id] = entries
    return tables


def _router_of(cmd_funcs, table_names):
    """{funcname: table_name} for every cmd_ function whose own body
    dispatches into one of the known tables, e.g. `TASK_COMMANDS[argv[0]]
    (argv[1:])` inside cmd_task. Matches both the inline subscript-call
    shape and the `.get(...)` shape."""
    router = {}
    for name, node in cmd_funcs.items():
        for sub in _own_scope_nodes(node.body):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if (isinstance(func, ast.Subscript) and isinstance(func.value, ast.Name)
                    and func.value.id in table_names):
                router[name] = func.value.id
                break
            if (isinstance(func, ast.Attribute) and func.attr == "get"
                    and isinstance(func.value, ast.Name) and func.value.id in table_names):
                router[name] = func.value.id
                break
    return router


def _expand(table_name, tables, router_of, prefix, seen):
    """Every (verb, funcname) leaf reachable from `table_name`, expanding
    one level of nested routing into a space-joined compound verb
    ("task add"). `seen` guards against a self-referential table rather
    than looping forever; none of this codebase's real tables are
    self-referential, this is a refusal-shaped safety net, not a
    reachable path today."""
    if table_name in seen:
        return []
    seen = seen | {table_name}
    out = []
    for key in sorted(tables[table_name]):
        funcname = tables[table_name][key]
        target = router_of.get(funcname)
        if target and target in tables and target not in seen:
            out.extend(_expand(target, tables, router_of, prefix + [key], seen))
        else:
            out.append((" ".join(prefix + [key]), funcname))
    return out


def discover_module(path):
    """(discovered, no_data) for one module file.

    discovered: list of (verb, funcname) pairs resolved from this
    module's own dict-dispatch tables.
    no_data: sorted list of `cmd_<x>` function names this scan found but
    could not resolve to any verb (reported, never silently dropped)."""
    with io.open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    cmd_funcs = _cmd_functions(tree)
    tables = _dispatch_tables(tree, cmd_funcs)
    router_of = _router_of(cmd_funcs, set(tables))
    consumed = set(router_of.values())
    top_level = sorted(t for t in tables if t not in consumed)
    discovered = []
    for t in top_level:
        discovered.extend(_expand(t, tables, router_of, [], set()))
    mapped = {f for _, f in discovered}
    no_data = sorted(name for name in cmd_funcs if name not in mapped)
    return discovered, no_data


def _target_files():
    """Every tools/bm_*.py plus tools/brothermode_cli.py, this suite's own
    file excluded (it is not a command module) and every test_*.py
    excluded (this is a scan of the shipped commands, not their tests)."""
    names = []
    for filename in sorted(os.listdir(TOOLS_DIR)):
        if not filename.endswith(".py") or filename.startswith("test_"):
            continue
        if filename in ("bm_effects.py",):
            continue
        if filename == "brothermode_cli.py" or filename.startswith("bm_"):
            names.append(filename)
    return names


# ---------------------------------------------------------------------------
# Test 1: every REGISTRY value is one of the five classes.
# ---------------------------------------------------------------------------

class TestRegistryClassesAreValid(unittest.TestCase):
    def test_every_registry_value_is_one_of_the_five_classes(self):
        bad = []
        for module in sorted(E.REGISTRY):
            for command in sorted(E.REGISTRY[module]):
                cls = E.REGISTRY[module][command]
                if cls not in E.CLASSES:
                    bad.append("%s %r -> %r" % (module, command, cls))
        self.assertEqual(
            [], bad,
            "REGISTRY values outside the five declared classes (%s): %s"
            % (", ".join(E.CLASSES), "; ".join(bad)))

    def test_classes_tuple_matches_the_five_constants(self):
        self.assertEqual(
            (E.PURE_READ, E.LEDGER_WRITE, E.PROJECT_WRITE, E.EXTERNAL_WRITE,
             E.DESTRUCTIVE_EXTERNAL_ACTION),
            E.CLASSES)


# ---------------------------------------------------------------------------
# Test 2: declared() raises, never defaults.
# ---------------------------------------------------------------------------

class TestDeclaredNeverDefaults(unittest.TestCase):
    def test_declared_raises_on_an_undeclared_command(self):
        with self.assertRaises(E.UndeclaredCommand):
            E.declared("bm_project.py", "this-command-does-not-exist")

    def test_declared_raises_on_an_undeclared_module(self):
        with self.assertRaises(E.UndeclaredCommand):
            E.declared("bm_this_module_does_not_exist.py", "status")

    def test_declared_returns_the_registered_class_for_a_real_pair(self):
        self.assertEqual(E.PURE_READ, E.declared("bm_project.py", "status"))
        self.assertEqual(E.LEDGER_WRITE, E.declared("bm_project.py", "start"))


# ---------------------------------------------------------------------------
# Test 3: completeness. This is the class-closing test: a new command
# cannot merge without a REGISTRY entry, because this test discovers it
# mechanically and fails, naming it, if one is missing.
# ---------------------------------------------------------------------------

class TestCompleteness(unittest.TestCase):
    def test_every_discovered_command_is_declared_in_the_registry(self):
        missing = []
        no_data_all = []
        for filename in _target_files():
            path = os.path.join(TOOLS_DIR, filename)
            discovered, no_data = discover_module(path)
            no_data_all.extend("%s:%s" % (filename, name) for name in no_data)
            registered = E.REGISTRY.get(filename, {})
            for verb, funcname in discovered:
                if verb not in registered:
                    missing.append("%s %r (-> %s)" % (filename, verb, funcname))
        message = ""
        if no_data_all:
            message += ("NO-DATA (dispatch could not be mechanically parsed "
                        "into a verb, not required in REGISTRY): %s\n"
                        % ", ".join(sorted(no_data_all)))
        if missing:
            message += ("undeclared commands (discovered but missing from "
                        "tools/bm_effects.py's REGISTRY): %s"
                        % "; ".join(sorted(missing)))
        # NO-DATA is always visible, pass or fail, per the spec's own
        # instruction not to silently pass over an unparseable module.
        sys.stderr.write(
            "bm_effects completeness: %d command(s) NO-DATA: %s\n"
            % (len(no_data_all), ", ".join(sorted(no_data_all)) or "(none)"))
        self.assertEqual([], missing, message)


# ---------------------------------------------------------------------------
# Tests 4 and 5: purity, against a real throwaway project.
# ---------------------------------------------------------------------------

def _diff_snapshots(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
    parts = []
    if added:
        parts.append("NEW FILE(S): %s" % ", ".join(added))
    if removed:
        parts.append("REMOVED FILE(S): %s" % ", ".join(removed))
    if changed:
        parts.append("CHANGED FILE(S): %s" % ", ".join(changed))
    return "; ".join(parts) if parts else "(no difference found)"


class _SandboxFixture(unittest.TestCase):
    """A real, freshly initialized throwaway project, built once per
    TestCase class. See this file's module docstring, ISOLATION, for why
    all four environment variables are pinned rather than only HOME."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm_effects_test_")
        cls.project_root = os.path.join(cls.tmp, "project")
        os.makedirs(cls.project_root)
        fake_home = os.path.join(cls.tmp, "home")
        os.makedirs(fake_home)
        vault = os.path.join(cls.tmp, "vault")
        cls.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": fake_home,
            "BROTHERMODE_VAULT": vault,
            "BROTHERSBE_VAULT": vault,
            "BROTHERMODE_ROOT": cls.project_root,
        }
        cls.project_id = "p1"
        init = cls._run("bm_store.py", ["init"])
        if init.returncode != 0:
            raise AssertionError(
                "fixture setup: bm_store.py init failed (exit %d): %s%s"
                % (init.returncode, init.stdout, init.stderr))
        start = cls._run("bm_project.py", [
            "start", "--project-id", cls.project_id, "--name", "Test Project",
            "--actor-name", "bm-effects-test-harness"])
        if start.returncode != 0:
            raise AssertionError(
                "fixture setup: bm_project.py start failed (exit %d): %s%s"
                % (start.returncode, start.stdout, start.stderr))
        capture = cls._run("bm_learn.py", [
            "capture", "--trigger", "a founder correction",
            "--action", "do the thing this way instead", "--json"])
        if capture.returncode != 0:
            raise AssertionError(
                "fixture setup: bm_learn.py capture failed (exit %d): %s%s"
                % (capture.returncode, capture.stdout, capture.stderr))
        try:
            cls.candidate_id = json.loads(capture.stdout)["candidate_uuid"]
        except (ValueError, KeyError) as exc:
            raise AssertionError(
                "fixture setup: could not read candidate_uuid from "
                "bm_learn.py capture --json output %r: %s"
                % (capture.stdout, exc))
        # A real active claim, load-bearing for tools/bm_fence_hook.py hook
        # (see _stdin_for's docstring): decide() only calls session_label(),
        # the write codex-audit-split-2026-08-10-night.md's HIGH #2 names,
        # once active_claims() returns at least one row. A store with zero
        # claims would let that command's real defect hide behind an early
        # _FailOpen("no-active-claims") that never reaches the write.
        claim = cls._run("bm_store.py", [
            "claim", "bm-effects-hook-probe", "--lifetime", "ephemeral",
            "--objective", "purity probe fixture", "--files", "probe/target.py",
            "--session", "bm-effects-fixture-claimant"])
        if claim.returncode != 0:
            raise AssertionError(
                "fixture setup: bm_store.py claim failed (exit %d): %s%s"
                % (claim.returncode, claim.stdout, claim.stderr))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _run(cls, module, argv, stdin_text=""):
        path = os.path.join(TOOLS_DIR, module)
        return subprocess.run(
            [sys.executable, path] + list(argv),
            cwd=cls.project_root, env=cls.env, input=stdin_text,
            capture_output=True, text=True, timeout=60)

    @classmethod
    def _snapshot(cls):
        """{relpath: (size, sha256)} for every file under project_root,
        the store's own WAL/SHM sidecars included: a new one appearing IS
        a write and must fail the purity check, not be filtered out."""
        out = {}
        for root, _dirs, files in os.walk(cls.project_root):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, cls.project_root).replace(os.sep, "/")
                try:
                    with io.open(full, "rb") as fh:
                        data = fh.read()
                except (IOError, OSError) as exc:
                    out[rel] = ("UNREADABLE:%s:%s" % (type(exc).__name__, exc), None)
                    continue
                out[rel] = (len(data), hashlib.sha256(data).hexdigest())
        return out


#: Extra argv a pure_read command needs to actually reach its own body
#: (past a required-flag usage check) rather than exit before ever
#: touching a store. Built from each command's own required-flag list,
#: read directly out of its source (see tools/bm_effects.py's REGISTRY
#: comments for the file:line evidence this suite is exercising).
#: Keyed by (module, verb); `fixture` only has to carry `.project_id` and
#: `.candidate_id` attributes (a real _SandboxFixture for TestPurity and
#: TestHelpPurity, a stand-in with made-up strings for
#: TestPurityUnderAStoreThatIsBehind, whose throwaway root never creates a
#: project or a candidate -- see _DummyIdentity below).
#:
#: codex-audit-split-2026-08-10-night.md HIGH #2 for this file: this map
#: used to omit bm_autonomy.py's gate-check/show/status, bm_controller.py's
#: status, bm_lead.py's decisions/status, and brothermode_cli.py's status,
#: so all six exited on a missing --project/--project-id before ever
#: opening a store and neither purity test could see what they actually do.
#: Each entry below is read straight from that command's own _require()
#: call, not guessed:
#:   bm_autonomy.py show/gate-check/status: --project (not --project-id)
#:   bm_controller.py status:               --project
#:   bm_lead.py status/decisions:           --project-id
#:   brothermode_cli.py status:             --project-id (delegates to
#:                                           bm_lead.py status verbatim)
def _argv_for(fixture, module, verb):
    overrides = {
        ("bm_project.py", "status"): ["status", "--project-id", fixture.project_id],
        ("bm_project.py", "next"): ["next", "--project-id", fixture.project_id],
        ("bm_project.py", "forecast show"):
            ["forecast", "show", "--project-id", fixture.project_id],
        ("brothermode_cli.py", "next"): ["next", "--project-id", fixture.project_id],
        ("brothermode_cli.py", "status"):
            ["status", "--project-id", fixture.project_id],
        ("bm_learn.py", "lookup"): ["lookup", "--query", "a test query"],
        # Added 2026-08-29 with bm_plan.py's registry entry, for exactly the
        # reason the bm_escalate.py pair below documents: both read verbs take
        # a required --project, so a bare argv exits 2 on usage and the verb
        # body never runs. This suite correctly refuses to read that as a
        # pure_read pass, and it named the missing map entry in its own failure
        # message rather than leaving it to be guessed.
        ("bm_plan.py", "show"): ["show", "--project", "a-bm-effects-test-project"],
        ("bm_plan.py", "check"): ["check", "--project", "a-bm-effects-test-project"],
        # Added 2026-08-17 with bm_escalate.py's registry entry. Both verbs
        # take a required --objective, so a bare argv exits 2 on usage and the
        # verb never reaches its body, which this suite correctly refuses to
        # read as a pure_read pass. That refusal is why the entry could not be
        # declared without these two lines.
        ("bm_escalate.py", "check"):
            ["check", "--objective", "a bm-effects-test objective"],
        ("bm_escalate.py", "packet"):
            ["packet", "--objective", "a bm-effects-test objective"],
        ("bm_packs.py", "stakes"): ["stakes", fixture.candidate_id],
        ("bm_view.py", "explain"): ["explain", "--reason", "absolute-write-scope"],
        ("bm_lint_walltime.py", "main"):
            [os.path.join(TOOLS_DIR, "bm_lint_walltime.py")],
        ("bm_autonomy.py", "show"): ["show", "--project", fixture.project_id],
        ("bm_autonomy.py", "gate-check"):
            ["gate-check", "--project", fixture.project_id,
             "--action-class", "bm-effects-test-class"],
        ("bm_autonomy.py", "status"): ["status", "--project", fixture.project_id],
        ("bm_controller.py", "status"): ["status", "--project", fixture.project_id],
        # Added 2026-08-30 with cmd_restore's pure_read declaration, for the
        # same reason as the bm_plan.py pair above: restore takes four
        # required flags, so a bare argv exits 2 on usage and the verb body
        # never runs. The checkpoint id names nothing on purpose: the
        # expected shape is a real refusal ("no checkpoint ..."), which is
        # proof the command opened the store read-only and reached its body.
        ("bm_controller.py", "restore"):
            ["restore", "--project", fixture.project_id,
             "--checkpoint", "bm-effects-nonexistent-checkpoint",
             "--controller-id", "bm-effects-test-controller",
             "--actor-name", "bm-effects-test-actor"],
        ("bm_lead.py", "status"): ["status", "--project-id", fixture.project_id],
        ("bm_lead.py", "decisions"): ["decisions", "--project-id", fixture.project_id],
        ("bm_project.py", "receipt list"):
            ["receipt", "list", "--project-id", fixture.project_id],
    }
    key = (module, verb)
    if key in overrides:
        return list(overrides[key])
    if verb == "main":
        return []
    return verb.split(" ")


class _DummyIdentity(object):
    """Stand-in for _argv_for's `fixture` parameter inside
    TestPurityUnderAStoreThatIsBehind, whose throwaway root (see
    _throwaway_root_behind_by_one_schema) is a bare `bm_store.py init` plus
    one claim: no project and no learning candidate ever exist in it. The
    values below only have to be well-formed STRINGS that get a command
    PAST its own argument parsing; every command that receives one is
    expected to fail at "no such project"/"no such candidate" (a real,
    usage-valid exit, not 2), which is exactly what proves the command
    reached its own body instead of bailing out early."""
    project_id = "bm-effects-behind-nonexistent-project"
    candidate_id = "bm-effects-behind-nonexistent-candidate"


def _stdin_for(module, verb, root):
    """Real stdin for a pure_read command whose own body reads one, keyed
    by (module, verb); every other command gets None, meaning "no special
    stdin needed" (callers fall back to their own default).

    codex-audit-split-2026-08-10-night.md HIGH #3 for this file:
    tools/bm_fence_hook.py's cmd_hook BLOCKS reading an empty or closed
    stdin rather than treating that as "no decision" (_read_stdin_json
    returns the "stdin was empty" error and cmd_hook returns before ever
    calling decide()), so feeding it "" (the regular runner) or
    subprocess.DEVNULL (the behind-schema runner) meant decide() -- and the
    active_claims()/session_label() calls inside it that HIGH #2 for this
    same file is about -- was never reached by either purity test. A real
    write-shaped payload (an Edit-like tool_input, a session_id, and an
    explicit cwd so root resolution cannot wander off into whatever
    directory the test process happens to be running from) is what
    actually exercises that path."""
    if (module, verb) == ("bm_fence_hook.py", "hook"):
        return json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "unclaimed/target.py"},
            "session_id": "bm-effects-hook-purity-probe",
            "cwd": root,
        })
    return None


class TestPurity(_SandboxFixture):
    """Test 4. Every pure_read command, run as a real subprocess against
    the real throwaway project, must leave it byte-identical. THIS IS
    WHERE THE ROOT CAUSE SURFACES: constructing a writable
    bm_store.Store is itself a write (WAL/SHM sidecars, git-excludes,
    chmod, possible migration), so any pure_read command that opens one
    instead of bm_store.ReadOnlyStore fails here, by construction, before
    a single line of its own logic runs."""

    def test_every_pure_read_command_writes_nothing(self):
        impure = []
        for module, verb in E.commands_of_class(E.PURE_READ):
            with self.subTest(module=module, verb=verb):
                argv = _argv_for(self, module, verb)
                stdin_text = _stdin_for(module, verb, self.project_root) or ""
                before = self._snapshot()
                result = self._run(module, argv, stdin_text=stdin_text)
                after = self._snapshot()
                if before != after:
                    impure.append("%s %r" % (module, verb))
                self.assertEqual(
                    before, after,
                    "%s %r is declared pure_read in tools/bm_effects.py "
                    "but changed the sandbox (exit %d): %s\nstdout=%r "
                    "stderr=%r"
                    % (module, verb, result.returncode,
                       _diff_snapshots(before, after),
                       result.stdout[-1000:], result.stderr[-1000:]))
        sys.stderr.write(
            "bm_effects purity: %d pure_read command(s) actually wrote: %s\n"
            % (len(impure), ", ".join(impure) or "(none)"))


class TestHelpPurity(_SandboxFixture):
    """Test 5. `<module> --help` (bare, the literal reading of the spec)
    for every module in the registry must write nothing, and it does for
    every module here except by construction it CANNOT exercise
    tools/bm_threads.py's actual documented defect: its main() treats an
    unrecognized top-level token, including a literal "--help", as a
    plain usage request and returns before ever dispatching to a command
    (tools/bm_threads.py's own main(), the `fn is None` branch), so bare
    `bm_threads.py --help` never reaches cmd_dashboard at all. The
    documented defect is that a SUBCOMMAND ignores --help
    (tools/bm_threads.py:871-899, `dashboard` never inspects its own
    argv), which only the verb-level form below, `<module> <verb>
    --help`, can reach and therefore catch. Both forms run here so the
    suite is honest about which one is the real regression guard."""

    def test_bare_module_help_writes_nothing(self):
        impure = []
        for module in sorted(E.REGISTRY):
            with self.subTest(module=module):
                before = self._snapshot()
                result = self._run(module, ["--help"])
                after = self._snapshot()
                if before != after:
                    impure.append(module)
                self.assertEqual(
                    before, after,
                    "%s --help is not pure (exit %d): %s"
                    % (module, result.returncode, _diff_snapshots(before, after)))
        sys.stderr.write(
            "bm_effects --help purity (bare module): %d impure: %s\n"
            % (len(impure), ", ".join(impure) or "(none)"))

    def test_pure_read_command_help_writes_nothing(self):
        impure = []
        for module, verb in E.commands_of_class(E.PURE_READ):
            with self.subTest(module=module, verb=verb):
                argv = _argv_for(self, module, verb) + ["--help"]
                before = self._snapshot()
                result = self._run(module, argv)
                after = self._snapshot()
                if before != after:
                    impure.append("%s %r" % (module, verb))
                self.assertEqual(
                    before, after,
                    "%s %r --help is not pure (exit %d): %s"
                    % (module, verb, result.returncode,
                       _diff_snapshots(before, after)))
        sys.stderr.write(
            "bm_effects --help purity (verb): %d impure: %s\n"
            % (len(impure), ", ".join(impure) or "(none)"))


class TestPurityUnderAStoreThatIsBehind(unittest.TestCase):
    """The condition under which the defect is actually REACHABLE, added
    2026-08-10 after the first purity design could not see it.

    WHY THIS CLASS EXISTS, and it is the more useful half of this file.
    The purity test above snapshots a throwaway tree before and after a
    command and asserts byte equality. Against a HEALTHY store that test
    passes for every command, including the ones documented as read-only
    that open a WRITABLE Store. Measured, not assumed: sqlite auto
    checkpoints and removes the -wal and -shm files when the last
    connection closes cleanly, _ensure_git_excludes is idempotent and
    already satisfied, and a pure SELECT leaves the main database file
    byte identical. So the whole-process snapshot observes nothing.

    That is a probe that cannot reach the defect, which is a failure class
    this repository has recorded twice before, and it nearly went down as
    evidence that the commands were clean.

    The reachable condition is a store that is BEHIND. Store.__init__
    calls _verify_schema_or_raise(migrate=True), so any command that
    constructs a writable Store against an out of date database MIGRATES
    IT. Demonstrated by hand on 2026-08-10 before this test was written:
    a store forced to schema_version 17 was handed to
    `bm_project.py status --project-id nosuch`, a command documented as a
    read accessor, which exited 1 having found no such project, and the
    database came back at schema_version 18 with a different md5. A read
    only command migrated a database while failing.

    So this class is the one that gives the pure_read declaration meaning.
    It is EXPECTED TO FAIL for every command still opening a writable
    Store, and each failure names a real defect rather than a test
    artefact."""

    def _throwaway_root_behind_by_one_schema(self):
        """A real store with one real active claim. Schema downgrading is
        NOT done here any more (MEDIUM finding, codex-audit-split-2026-08-
        10-night.md, for this file): it used to decrement here AND again
        inside put_it_behind() before the very first command, which is the
        accumulating bug that finding names. put_it_behind() alone now
        owns setting the schema version, to an ABSOLUTE target, before
        every command including the first, so this method's only job is to
        hand back a healthy, current, claim-bearing store.

        The claim is load-bearing for tools/bm_fence_hook.py hook, exactly
        as it is in _SandboxFixture (see _stdin_for's docstring): without
        one, active_claims() returns no rows and decide() never reaches
        session_label(), the write HIGH #2 is about."""
        root = tempfile.mkdtemp(prefix="bm-effects-behind-")
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q", root], check=False,  # sbe: allow-silent throwaway fixture repo; the store init two lines below is the real, separately-checked step
                       capture_output=True)
        home = os.path.join(root, "home"); os.makedirs(home)
        vault = os.path.join(root, "vault")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home,
               "BROTHERMODE_VAULT": vault, "BROTHERSBE_VAULT": vault,
               "BROTHERMODE_ROOT": root}
        subprocess.run([sys.executable,  # sbe: allow-silent result verified two lines below via db existence, which skipTests as NO-DATA on failure
                        os.path.join(TOOLS_DIR, "bm_store.py"), "init"],
                       env=env, capture_output=True, check=False)
        db = os.path.join(root, ".brothermode", "store.sqlite3")
        if not os.path.exists(db):
            self.skipTest("could not build a store here, so this is NO-DATA "
                          "rather than a pass: nothing was checked")
        claim = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, "bm_store.py"), "claim",
             "bm-effects-behind-probe", "--lifetime", "ephemeral",
             "--objective", "purity probe fixture", "--files",
             "probe/target.py", "--session", "bm-effects-behind-claimant"],
            cwd=root, env=env, capture_output=True, text=True, check=False)
        if claim.returncode != 0:
            self.skipTest(
                "could not claim a record in the throwaway store, so this "
                "is NO-DATA rather than a pass (exit %d): %s%s"
                % (claim.returncode, claim.stdout, claim.stderr))
        return root, env, db

    def test_a_pure_read_command_does_not_migrate_a_store_that_is_behind(self):
        root, env, db = self._throwaway_root_behind_by_one_schema()
        def put_it_behind():
            """Set the store's schema_version to an ABSOLUTE target, exactly
            SCHEMA_VERSION - 1, before EVERY command, rather than
            decrementing whatever value is found there.

            MEDIUM finding, codex-audit-split-2026-08-10-night.md, for this
            file, at this method and at _throwaway_root_behind_by_one_schema
            above: the old code decremented a relative CURRENT value in both
            places, so command 1 saw schema_version - 2 (the setup's
            decrement plus this one), not schema_version - 1, and every
            later command in the 33-pure-command loop saw one version
            further behind than the one before it, whether or not the prior
            command actually migrated anything. By command 17 or so the
            version went unsupported (negative or below the oldest known
            schema), and _TABLES_BY_VERSION.get(fv) returning None routes
            into quarantine/corruption handling instead of the migration
            path this test exists to catch, making coverage depend on which
            command happened to run first.

            Setting an ABSOLUTE target fixes both bugs at once and is
            idempotent regardless of what the previous command did to the
            store: an offender that migrated it back to SCHEMA_VERSION gets
            put right back to SCHEMA_VERSION - 1; a genuine pure_read
            command that left it untouched gets set there too, a no-op."""
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "update meta set value=? where key='schema_version'",
                    (str(_BS.SCHEMA_VERSION - 1),))
                conn.commit()
            finally:
                conn.close()
            with io.open(db, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        offenders = []
        usage_invalid = []
        for module, command in E.commands_of_class(E.PURE_READ):
            before = put_it_behind()
            path = os.path.join(TOOLS_DIR, module)
            if not os.path.exists(path):
                continue
            # HIGH finding, codex-audit-split-2026-08-10-night.md, for this
            # file: `command.split()` used to stand in for real argv, so
            # every command needing a required flag (bm_learn.py lookup,
            # bm_packs.py stakes, bm_project.py status/next/forecast show,
            # brothermode_cli.py next, and more) exited on usage validation
            # before ever opening a store, and this loop's check=False threw
            # that nonzero exit away, so an unchanged database counted as a
            # pass. _argv_for is the same map TestPurity already trusts to
            # reach each command's real body.
            argv = _argv_for(_DummyIdentity, module, command)
            stdin_payload = _stdin_for(module, command, root)
            try:
                if stdin_payload is None:
                    # stdin CLOSED and a timeout, both load-bearing: at least
                    # one pure_read command is a hook entry point that
                    # BLOCKS reading a payload from stdin, so an inherited
                    # terminal makes this test hang forever rather than
                    # fail. A hang is the worst failure shape available to a
                    # gate: it reports nothing.
                    result = subprocess.run(
                        [sys.executable, path] + argv, env=env,
                        capture_output=True, check=False, text=True,
                        stdin=subprocess.DEVNULL, timeout=30)
                else:
                    result = subprocess.run(
                        [sys.executable, path] + argv, env=env,
                        capture_output=True, check=False,
                        input=stdin_payload, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                offenders.append("%s %s (TIMED OUT, not measured)"
                                 % (module, command))
                continue
            # Returncode 2 is overloaded across this codebase: every
            # _require()-shaped usage error (bm_project.py, bm_autonomy.py's
            # EXIT_USAGE, bm_controller.py, bm_lead.py) sys.exit(2) on a
            # missing required flag, but so does a LEGITIMATE business
            # refusal (bm_store.py's OwnershipRefused "schema-behind", the
            # exact refusal this test wants a healthy pure_read command to
            # reach and print -- proof it opened the store and read through
            # bm_store.ReadOnlyStore rather than silently succeeding).
            # Collapsing both into "any exit 2 is a hole in the argv map"
            # would fail commands (bm_store.py dump/handovers/verify,
            # bm_threads.py dashboard) that take no required arguments at
            # all and are refusing CORRECTLY. The two are told apart by
            # shape, not by exit code alone: every usage error this
            # codebase prints goes through `_err(usage)` or an equivalent
            # `_out("usage: ...")`, always the literal substring "usage: ";
            # every OwnershipRefused/PackError/StaleIdentity refusal prints
            # "refused (<reason>): ..." and never that substring.
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode == 2 and "usage: " in combined:
                usage_invalid.append(
                    "%s %s (exit 2 with argv %r, _argv_for's map is still "
                    "incomplete for this command)" % (module, command, argv))
            after = hashlib.sha256(io.open(db, "rb").read()).hexdigest()
            if after != before:
                offenders.append("%s %s" % (module, command))
                before = after
        self.assertEqual(
            usage_invalid, [],
            "pure_read command(s) never reached their own body: %s"
            % "; ".join(usage_invalid))
        self.assertEqual(
            offenders, [],
            "command(s) declared pure_read MIGRATED a database that was one "
            "schema version behind: %s. Constructing bm_store.Store runs "
            "_verify_schema_or_raise(migrate=True), so a read accessor that "
            "opens the writable class rewrites the store the moment it is "
            "not current. Route these through bm_store.ReadOnlyStore."
            % ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()



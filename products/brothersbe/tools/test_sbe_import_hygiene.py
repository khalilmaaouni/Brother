#!/usr/bin/env python3
"""Fixtures for `src/brothersbe/_toolspath.py` and the import-hygiene rule it
enforces. Run: python3 tools/test_sbe_import_hygiene.py

Six modules under `src/brothersbe/` (`converge.py`, `evidence.py`,
`impact.py`, `reviewroute.py`, `handover.py`, `decisions.py`) each used to
compute the path to `tools/` and mount it onto `sys.path` itself: six
separate copies of the same three lines. This suite proves the fix: those
six now share one mount function, `_toolspath.mount()`, none of them
touches `sys.path` directly any more, and every one of them still resolves
the `tools/`-flat module it imports afterward, so the migration changed
nothing a caller can observe.

Source is read with `ast`, not a text search, so a comment or a docstring
that MERELY MENTIONS `sys.path` in prose (`decisions.py` explains why its
own mount has to be lazy) never counts as a hit: `ast` only ever sees
string content as a `Constant`, never as an `Attribute` node, so prose is
structurally invisible to the walk below.

`KNOWN_UNCONVERTED` holds exactly one module, for a load-bearing reason
discovered when the fold-in was attempted: `tasks.py` is loaded STANDALONE
by `tools/sbe_authority_hook.py` through `spec_from_file_location`, with no
package parent, so a package-relative `from ._toolspath import mount` there
raises ImportError inside the hook and the authority guard fails open
(`tools/test_sbe_authority_hook.py` went red on exactly that during the
2026-08-05 integration, which is how this reason was proven rather than
assumed). `work.py` was folded in the same night; nothing file-loads it.
"""
import ast
import io
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PKG = os.path.join(ROOT, "src", "brothersbe")
SRC = os.path.join(ROOT, "src")

#: The six modules this migration converts, mapped to the tools/-flat module
#: each one imports afterward -- documentation of WHY each is on this list,
#: not read by any assertion below.
CONVERTED = {
    "converge.py": "sbe_plan",
    "evidence.py": "sbe_checks and sbe_telemetry",
    "impact.py": "sbe_intake",
    "reviewroute.py": "sbe_telemetry",
    "handover.py": "sbe_gate",
    "decisions.py": "sbe_checks (lazily, inside registries())",
    "work.py": "sbe_plan and sbe_checks",
}

#: See the module docstring: the one standalone-loaded exception, by proof.
KNOWN_UNCONVERTED = frozenset(("tasks.py",))


def _parse(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    return ast.parse(source, filename=path)


def _touches_sys_path(tree):
    """True if `tree` contains a real `sys.path` attribute access anywhere
    in its code: an assignment target, an `insert(...)` receiver, a
    membership test, anything. Only a genuine `Attribute(value=Name('sys'),
    attr='path')` node counts; text that merely says "sys.path" inside a
    string or a comment can never produce one."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "path"
                and isinstance(node.value, ast.Name) and node.value.id == "sys"):
            return True
    return False


def _imports_mount_from_toolspath(tree):
    """True if `tree` has `from ._toolspath import mount` (or `from
    brothersbe._toolspath import mount`, the absolute spelling of the same
    thing), the one sanctioned way to reach the shared mount function."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in (
                "_toolspath", "brothersbe._toolspath"):
            if any(alias.name == "mount" for alias in node.names):
                return True
    return False


class TestOneMountMechanism(unittest.TestCase):
    """`_toolspath.py` is the only file under `src/brothersbe/` allowed to
    touch `sys.path`, among the files this migration owns."""

    def test_toolspath_module_itself_mounts(self):
        tree = _parse(os.path.join(PKG, "_toolspath.py"))
        self.assertTrue(_touches_sys_path(tree),
                        "_toolspath.py should be the module that actually touches "
                        "sys.path; nothing else can be delegating to it if it does not")

    def test_converted_modules_no_longer_touch_sys_path_directly(self):
        for filename in sorted(CONVERTED):
            tree = _parse(os.path.join(PKG, filename))
            self.assertFalse(_touches_sys_path(tree),
                             "%s still has its own sys.path mount; it should delegate "
                             "to _toolspath.mount() instead" % filename)

    def test_converted_modules_import_the_shared_mount(self):
        for filename in sorted(CONVERTED):
            tree = _parse(os.path.join(PKG, filename))
            self.assertTrue(_imports_mount_from_toolspath(tree),
                            "%s does not `from ._toolspath import mount`" % filename)

    def test_no_stray_ninth_mount_anywhere_in_the_package(self):
        # Every .py file under src/brothersbe/ is accounted for: it is either
        # _toolspath.py itself, one of the six CONVERTED modules (proven clean
        # above), one of the two KNOWN_UNCONVERTED pre-existing exceptions, or
        # it must not touch sys.path either. A new file added later that
        # copies the old inline pattern fails here instead of quietly
        # becoming a ninth copy nobody notices.
        accounted_for = set(CONVERTED) | KNOWN_UNCONVERTED | {"_toolspath.py"}
        for filename in sorted(os.listdir(PKG)):
            if not filename.endswith(".py") or filename in accounted_for:
                continue
            tree = _parse(os.path.join(PKG, filename))
            self.assertFalse(_touches_sys_path(tree),
                             "%s touches sys.path directly; route it through "
                             "_toolspath.mount(), or if this is a deliberate new "
                             "exception outside this change's scope, add it to "
                             "KNOWN_UNCONVERTED with a reason" % filename)


class TestSixImportsStillResolve(unittest.TestCase):
    """The migration is supposed to change zero behavior: each of the six
    modules must still be able to reach the tools/-flat module(s) it
    imports, now that it mounts through `_toolspath` instead of mounting
    itself. Each check runs in a fresh interpreter (not an in-process
    import) so one module's sys.modules cache can never mask another's
    resolution failure, and so a broken mount surfaces as the real
    ImportError a caller would see, not as a false pass from caching."""

    def _run(self, code):
        return subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True)

    def test_converge_resolves_sbe_plan(self):
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.converge\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_evidence_resolves_sbe_checks_and_sbe_telemetry(self):
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.evidence\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_impact_resolves_sbe_intake(self):
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.impact\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_reviewroute_resolves_sbe_telemetry(self):
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.reviewroute\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_handover_resolves_sbe_gate(self):
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.handover\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_decisions_resolves_sbe_checks_lazily(self):
        # decisions.py mounts INSIDE registries(), not at import time, so
        # importing the module alone proves nothing about its own mount;
        # the accessor that actually triggers it has to be called.
        proc = self._run("import sys; sys.path.insert(0, %r)\n"
                         "import brothersbe.decisions as d\n"
                         "d.registries()\n" % SRC)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()

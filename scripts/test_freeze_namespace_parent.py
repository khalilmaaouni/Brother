#!/usr/bin/env python3
"""A namespace package below the top level resolves in the freeze, without importing its parent.

Measured 2026-09-26 on a flat copy of the loop bin: plugin/runtime/brother/core/openrouter_ledger.py
does `from scripts.loop import proof_ledger`, where neither scripts/ nor scripts/loop/ has an
__init__.py, and `freeze_manifest.py write` stopped with "NO-DATA: 'scripts'". resolve() probes each
level with PathFinder.find_spec and never imports the parent; for a namespace hit the finder builds a
_NamespacePath whose constructor reads sys.modules[parent].__path__, so the bare KeyError leaked out.
The parent's own kind does not matter: a regular parent (with __init__.py) under a namespace child
failed the same way. The CLI cases run the entry point in fixture directories; the in-process cases
exist because loop_receipt.py calls freeze_manifest.verify inside its own process, where the probe
must leave sys.modules exactly as it found it.
"""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_freeze_manifest import Freeze, TOOL


def leaf(root, *parts):
    """root/parts[0]/.../leaf.py with no __init__.py anywhere: every level is a namespace package."""
    d = root.joinpath(*parts); d.mkdir(parents=True)
    path = d / 'leaf.py'; path.write_text('VALUE=1\n')
    return path


class NamespaceParent(Freeze):
    def frozen_with_drift(self, entry, path, name):
        (self.bin / 'entry.py').write_text(entry)
        d = self.freeze()
        self.assertEqual(d['imports'][name], str(path))
        self.assertIn(str(path), d['files'])
        self.run_tool('verify', str(self.out))
        path.write_text('VALUE=2\n')
        self.run_tool('verify', str(self.out), code=1)
        return d

    def test_two_level_namespace_resolves_and_tracks_drift(self):
        path = leaf(self.lib, 'nsouter', 'nsinner')
        self.frozen_with_drift('from nsouter.nsinner import leaf\n', path, 'nsouter.nsinner.leaf')

    def test_regular_parent_with_namespace_child_resolves(self):
        (self.lib / 'regouter').mkdir(); (self.lib / 'regouter' / '__init__.py').write_text('VALUE=1\n')
        path = leaf(self.lib, 'regouter', 'nsinner')
        d = self.frozen_with_drift('import regouter.nsinner.leaf\n', path, 'regouter.nsinner.leaf')
        self.assertIn(str(self.lib / 'regouter' / '__init__.py'), d['files'])

    def test_three_level_namespace_resolves(self):
        path = leaf(self.lib, 'nsouter', 'nsmiddle', 'nsinner')
        self.frozen_with_drift('import nsouter.nsmiddle.nsinner.leaf\n', path, 'nsouter.nsmiddle.nsinner.leaf')


class InProcessProbe(Freeze):
    NAMES = ('nsouter', 'nsouter.nsinner', 'nsouter.nsinner.leaf')

    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location('freeze_manifest_under_test', str(TOOL))
        self.fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.fm)
        self.path = leaf(self.lib, 'nsouter', 'nsinner')
        saved = {n: sys.modules[n] for n in self.NAMES if n in sys.modules}
        def restore():
            for n in self.NAMES:
                sys.modules.pop(n, None)
            sys.modules.update(saved)
        self.addCleanup(restore)

    def resolved(self, name):
        found = self.fm.resolve(name, [str(self.lib)])
        self.assertIsNotNone(found)
        return found

    def test_probe_leaves_no_parent_in_sys_modules(self):
        found = self.resolved('nsouter.nsinner.leaf')
        self.assertEqual(found[-1][1].origin, str(self.path))
        self.assertEqual([n for n in self.NAMES if n in sys.modules], [])

    def test_probe_restores_an_existing_parent_entry(self):
        sentinel = types.ModuleType('nsouter'); sentinel.__path__ = [str(self.r / 'elsewhere')]
        sys.modules['nsouter'] = sentinel
        self.assertEqual(self.resolved('nsouter.nsinner.leaf')[-1][1].origin, str(self.path))
        self.assertIs(sys.modules['nsouter'], sentinel)

    def test_probe_resolves_under_a_parent_blocked_as_none(self):
        sys.modules['nsouter'] = None
        self.assertEqual(self.resolved('nsouter.nsinner.leaf')[-1][1].origin, str(self.path))
        self.assertIsNone(sys.modules['nsouter'])

    def test_returned_namespace_spec_iterates_after_the_probe(self):
        name, spec = self.resolved('nsouter.nsinner')[-1]
        self.assertEqual(name, 'nsouter.nsinner')
        self.assertEqual(list(spec.submodule_search_locations), [str(self.path.parent)])


if __name__ == '__main__':
    # Only this file's own cases: the inherited Freeze cases run in test_freeze_manifest.py.
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(cls(n) for cls in (NamespaceParent, InProcessProbe) for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())

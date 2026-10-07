"""Behavioral regressions for the allocated static import closure fixes."""
from pathlib import Path
import sys, unittest, subprocess
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_freeze_manifest import Freeze

class Resolution(Freeze):
 def entry(self, text): (self.bin/'entry.py').write_text(text)
 def require_drift(self, code, child='helper.py'):
  self.entry(code);manifest=self.freeze()
  self.assertIn(str(self.lib/child),manifest['files'])
  self.run_tool('verify',str(self.out))
  (self.lib/child).write_text('VALUE=2\n')
  self.run_tool('verify',str(self.out),code=1)
 def package(self):
  p=self.lib/'pkg';p.mkdir();(p/'__init__.py').write_text('VALUE=1\n');(p/'child.py').write_text('VALUE=1\n')
 def refusal(self, code):
  self.entry(code);args=['write',str(self.out),'--bin',str(self.bin),'--module-root',str(self.lib)]
  for k,v in self.configs.items():args+=['--config',k+'='+v]
  self.run_tool(*args,code=2);self.assertFalse(self.out.exists())
 def test_imported_callable_alias_tracks_drift(self):
  self.require_drift("from importlib import import_module as load\nload('helper')\n")
 def test_module_alias_tracks_drift(self):
  self.require_drift("import importlib as loader\nloader.import_module('helper')\n")
 def test_assignment_alias_tracks_drift(self):
  self.require_drift("from importlib import import_module\nload=import_module\nload('helper')\n")
 def test_aliased_computed_name_refuses(self):
  self.refusal("from importlib import import_module as load\nload(name)\n")
 def test_aliased_loader_escape_refuses(self):
  self.refusal("from importlib import import_module as load\ncallback(load)\n")
 def test_keyword_fromlist_tracks_child_drift(self):
  self.package();self.require_drift("__import__('pkg', fromlist=['child'])\n",'pkg/child.py')
 def test_positional_fromlist_tracks_child_drift(self):
  self.package();self.require_drift("__import__('pkg', {}, {}, ['child'], 0)\n",'pkg/child.py')
 def test_builtin_import_alias_tracks_child_drift(self):
  self.package();self.require_drift("from builtins import __import__ as load\nload('pkg', fromlist=['child'])\n",'pkg/child.py')
 def test_computed_fromlist_refuses(self):
  self.package();self.refusal("__import__('pkg', fromlist=names)\n")
 def test_unresolved_star_fromlist_refuses(self):
  self.package();self.refusal("__import__('pkg', fromlist=['*'])\n")
 def test_package_attribute_fromlist_is_not_required_submodule(self):
  self.package();self.entry("__import__('pkg', fromlist=['VALUE'])\n");self.freeze();self.run_tool('verify',str(self.out))
 def test_relative_dynamic_import_without_context_refuses(self):
  self.package();self.refusal("__import__('child', fromlist=['VALUE'], level=1)\n")
 def test_stdlib_os_path_is_supported(self):
  self.entry('import os.path\n');self.freeze();self.run_tool('verify',str(self.out))


class StandardLibraryAlias(unittest.TestCase):
    """2026-09-28: on Python 3.13 collections.abc exists only as an alias the collections package installs, so the
    deploy refused a tool importing it. A standard library alias resolves; an alias that is not standard library
    does not, even under a standard library parent."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent / "loop"))
        import freeze_manifest
        self.F = freeze_manifest

    def test_collections_abc_resolves(self):
        found = self.F.resolve("collections.abc", sys.path)
        self.assertIsNotNone(found)
        self.assertEqual(found[-1][0], "collections.abc")

    def test_a_local_alias_under_a_stdlib_parent_is_refused(self):
        import tempfile, types, importlib.util
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "planted.py"
            path.write_text("X = 1\n")
            spec = importlib.util.spec_from_file_location("collections.planted_alias", str(path))
            module = types.ModuleType("collections.planted_alias"); module.__spec__ = spec
            sys.modules["collections.planted_alias"] = module
            try:
                self.assertIsNone(self.F.resolve("collections.planted_alias", sys.path))
            finally:
                del sys.modules["collections.planted_alias"]

if __name__=='__main__':
 suite=unittest.TestSuite([Resolution(n) for n in Resolution.__dict__ if n.startswith('test_')]
                         + [StandardLibraryAlias(n) for n in StandardLibraryAlias.__dict__ if n.startswith('test_')])
 result=unittest.TextTestRunner().run(suite)
 sibling=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name('test_freeze_manifest.py'))])
 sys.exit(not result.wasSuccessful() or sibling.returncode!=0)

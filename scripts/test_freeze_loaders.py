#!/usr/bin/env python3
"""D-21: constant file loaders resolve against frozen roots instead of refusing every dynamic loader
call, and land_apply.py's fuzz-step import_module of a freshly built module is a narrow, evidenced
work-input boundary rather than a blanket exemption for the whole file.

Two refusal classes closed here (brother_night_tick.py's CAP_HOOK = os.path.expanduser(...) passed to
spec_from_file_location, and land_apply.py's importlib.import_module(name) of a new build's modules
inside main()); everything outside those exact shapes keeps today's refusal. Every case below runs the
CLI entry point in fixture directories, mirroring test_freeze_native_closure.py.
"""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_freeze_manifest import Freeze
from test_freeze_native_closure import FAKE_OTOOL, SUFFIX


class Loaders(Freeze):
    def entry(self, text):
        (self.bin / 'entry.py').write_text(text)

    def write_args(self, *extra):
        args = ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib)] + list(extra)
        for k, v in self.configs.items():
            args += ['--config', k + '=' + v]
        return args

    def refuses(self, *extra):
        p = self.run_tool(*self.write_args(*extra), code=2)
        self.assertFalse(self.out.exists(), 'a refused freeze must not leave a manifest')
        self.assertIn('NO-DATA', p.stdout)
        return p

    def frozen(self, *extra):
        p = self.run_tool(*self.write_args(*extra))
        return json.loads(self.out.read_text()), p

    # A constant loader (D-21): a literal module name and a literal path resolve, hash and drift-check.
    def test_literal_path_loader_resolves_target_hashed_and_drift_caught(self):
        target = self.bin / 'hook.py'
        target.write_text('VALUE=1\n')
        self.entry("import importlib.util\n"
                    "spec = importlib.util.spec_from_file_location('hook', %r)\n" % str(target))
        d, _ = self.frozen()
        self.assertEqual(len(d['resolved_loaders']), 1)
        site = d['resolved_loaders'][0]
        self.assertEqual(site['target'], str(target))
        self.assertEqual(site['name'], 'hook')
        self.assertIn(str(target), d['files'])
        self.run_tool('verify', str(self.out))
        target.write_text('VALUE=2\n')
        p = self.run_tool('verify', str(self.out), code=1)
        self.assertIn('hook.py', p.stdout)

    # The scanned file's own directory, os.path.join and a module level single assignment (D-21).
    def test_module_level_constant_built_from_own_directory_is_resolved(self):
        target = self.bin / 'hook.py'
        target.write_text('VALUE=1\n')
        self.entry("import os, importlib.util\n"
                    "HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook.py')\n"
                    "spec = importlib.util.spec_from_file_location('hook', HOOK)\n")
        d, _ = self.frozen()
        self.assertEqual(d['resolved_loaders'][0]['target'], str(target))
        self.run_tool('verify', str(self.out))

    # CAP_HOOK's actual shape: expanduser of a literal, resolved against the pinned home (D-21), with
    # SourceFileLoader rather than spec_from_file_location.
    def test_expanduser_loader_resolved_against_pinned_home(self):
        (self.r / 'hook.py').write_text('VALUE=1\n')
        self.entry("import os\n"
                    "from importlib.machinery import SourceFileLoader\n"
                    "CAP_HOOK = os.path.expanduser('~/hook.py')\n"
                    "loader = SourceFileLoader('hook', CAP_HOOK)\n")
        d, _ = self.frozen()
        self.assertEqual(d['home'], str(self.r))
        self.assertEqual(d['resolved_loaders'][0]['target'], str(self.r / 'hook.py'))
        self.run_tool('verify', str(self.out))

    # A different HOME at verify time is drift on its own, with no expanduser loader in play at all:
    # isolates the pinned 'home' field from the loader-target resolution it would otherwise also disturb.
    def test_home_change_is_drift(self):
        self.entry('import helper\n')
        self.frozen()
        other_home = self.r / 'otherhome'; other_home.mkdir()
        changed = dict(self.env, HOME=str(other_home))
        p = self.run_tool('verify', str(self.out), env=changed, code=1)
        self.assertIn('FAIL drift in home', p.stdout)

    # A path built from anything outside the whitelist (here, string concatenation) still refuses.
    def test_computed_path_still_refuses(self):
        self.entry("import importlib.util\n"
                    "name = 'hook'\n"
                    "spec = importlib.util.spec_from_file_location('hook', name + '.py')\n")
        self.refuses()

    # Every other opaque loader keeps today's refusal even with a resolvable-looking literal path.
    def test_extension_file_loader_still_refuses(self):
        target = self.bin / 'hook.so'; target.write_bytes(b'x')
        self.entry("from importlib.machinery import ExtensionFileLoader\n"
                    "loader = ExtensionFileLoader('hook', %r)\n" % str(target))
        self.refuses()

    # land_apply.py's shape: importlib.import_module(name) inside main(), declared as work input.
    def test_declared_function_is_accepted(self):
        self.entry("import importlib\n"
                    "def main():\n"
                    "    name = 'x'\n"
                    "    importlib.import_module(name)\n")
        _, p = self.frozen('--work-input', 'entry.py:main=fuzz')
        self.assertIn('WORK-INPUT entry.py:main (fuzz)', p.stdout)
        d = json.loads(self.out.read_text())
        self.assertEqual(d['work_inputs'], ['entry.py:main=fuzz'])
        self.run_tool('verify', str(self.out))

    # Only the declared function's computed loader is accepted; a sibling function's stays refused.
    def test_computed_import_outside_declared_function_still_refuses(self):
        self.entry("import importlib\n"
                    "def main():\n"
                    "    name = 'x'\n"
                    "    importlib.import_module(name)\n"
                    "def other():\n"
                    "    other_name = 'y'\n"
                    "    importlib.import_module(other_name)\n")
        self.refuses('--work-input', 'entry.py:main=fuzz')

    # A declared function with no computed loader at all is stale, not a free pass.
    def test_stale_declaration_refuses(self):
        self.entry("def main():\n    pass\n")
        p = self.refuses('--work-input', 'entry.py:main=fuzz')
        self.assertIn('stale', p.stdout)

    # A declaration naming a file the runtime does not contain refuses.
    def test_declaration_outside_runtime_refuses(self):
        self.entry("def main():\n    pass\n")
        p = self.refuses('--work-input', 'nope.py:main=fuzz')
        self.assertIn('outside the runtime', p.stdout)

    # Malformed declarations refuse: missing separators, empty parts, bad identifiers, escaping paths.
    def test_malformed_declarations_refuse(self):
        self.entry("import importlib\ndef main():\n    importlib.import_module(x)\n")
        for bad in ('', 'entry.py=fuzz', 'entry.py:main', 'entry.py:=fuzz', 'entry.py:main=',
                    'entry.py:1bad=fuzz', '/entry.py:main=fuzz', '../entry.py:main=fuzz',
                    'entry.py:main=bad space'):
            self.refuses('--work-input', bad)


LOAD = "spec = importlib.util.spec_from_file_location('hook', TARGET)"
REFUSED = 'dynamic loader requires a resolved import trace'


class Bindings(Loaders):
    """The constant path resolver (2026-09-26 finding): it read a name's single plain assignment and missed
    every other way the file can bind that name, so `TARGET = '/frozen.py'` followed by `TARGET, =
    ('/unfrozen.py',)` froze /frozen.py while Python loads /unfrozen.py. A name the resolver reads now
    resolves only when it has exactly one binding of any kind anywhere in the file, and that binding is a
    plain module level assignment. Each case adds ONE binding form to the file test_one_binding_resolves
    freezes, so its refusal can only come from counting that form."""

    def hooked(self, *lines):
        hook = self.bin / 'hook.py'; hook.write_text('VALUE=1\n')
        self.entry('\n'.join(['import importlib.util', 'TARGET = %r' % str(hook)] + list(lines)) + '\n')
        return hook

    def rebound(self, *lines):
        self.hooked(*lines)
        p = self.refuses()
        self.assertIn(REFUSED, p.stdout)

    def rebound_each(self, cases):
        for lines in cases:
            with self.subTest(lines[0]):
                self.rebound(*lines)

    # Control: one plain module level binding resolves, read at module level and inside a function.
    def test_one_binding_resolves(self):
        for lines in ([LOAD], ['def load():', '    ' + LOAD]):
            with self.subTest(lines[-1]):
                hook = self.hooked(*lines)
                d, _ = self.frozen()
                self.assertEqual(d['resolved_loaders'][0]['target'], str(hook))

    # Guard: a Name stored or deleted anywhere is a binding.
    def test_plain_reassignment_refuses(self):
        self.rebound("TARGET = '/unfrozen.py'", LOAD)

    def test_tuple_unpacking_refuses(self):
        self.rebound("TARGET, = ('/unfrozen.py',)", LOAD)

    def test_list_unpacking_refuses(self):
        self.rebound("[TARGET] = ['/unfrozen.py']", LOAD)

    def test_starred_unpacking_refuses(self):
        self.rebound("first, *TARGET = ['/a.py', '/unfrozen.py']", LOAD)

    def test_annotated_assignment_refuses(self):
        self.rebound("TARGET: str = '/unfrozen.py'", LOAD)

    def test_augmented_assignment_refuses(self):
        self.rebound("TARGET += ''", LOAD)

    def test_for_target_refuses(self):
        self.rebound("for TARGET in ['/unfrozen.py']:", "    pass", LOAD)

    def test_with_target_refuses(self):
        self.rebound("with open(__file__) as TARGET:", "    pass", LOAD)

    def test_walrus_refuses(self):
        self.rebound("if (TARGET := '/unfrozen.py'):", "    pass", LOAD)

    def test_comprehension_target_refuses(self):
        self.rebound("NAMES = [TARGET for TARGET in ['/unfrozen.py']]", LOAD)

    def test_del_refuses(self):
        self.rebound("del TARGET", LOAD)

    # Guard: an import binds its alias, or the head of its dotted name.
    def test_import_binding_refuses(self):
        (self.lib / 'provider.py').write_text("TARGET = '/unfrozen.py'\n")
        (self.lib / 'TARGET.py').write_text("VALUE = 1\n")
        self.rebound_each([("import json as TARGET", LOAD), ("from os import sep as TARGET", LOAD),
                           ("from provider import TARGET", LOAD), ("import TARGET", LOAD)])

    # Guard: a function or lambda parameter shadows the module name inside its body.
    def test_function_parameter_refuses(self):
        self.rebound_each([(head, '    ' + LOAD) for head in (
            'def load(TARGET):', 'def load(TARGET, /):', 'def load(*, TARGET):', 'def load(*TARGET):',
            'def load(**TARGET):', "def load(TARGET='/unfrozen.py'):", 'async def load(TARGET):')])

    def test_lambda_parameter_refuses(self):
        self.rebound("load = lambda TARGET: importlib.util.spec_from_file_location('hook', TARGET)")

    # Guard: a global or nonlocal declaration of the name. (A nonlocal needs an enclosing binding, which is
    # itself counted, so only the global declaration can be isolated in a fixture.)
    def test_global_declaration_refuses(self):
        self.rebound("def rebind():", "    global TARGET", LOAD)

    # Guard: a node carrying the bound identifier in a name or rest field: def, class, except.
    def test_def_class_and_except_names_refuse(self):
        self.rebound_each([("def TARGET():", "    pass", LOAD), ("async def TARGET():", "    pass", LOAD),
                           ("class TARGET:", "    pass", LOAD),
                           ("try:", "    pass", "except Exception as TARGET:", "    pass", LOAD)])

    @unittest.skipUnless(sys.version_info >= (3, 10), 'match statements parse from Python 3.10')
    def test_match_captures_refuse(self):
        self.rebound_each([("match 0:", "    case TARGET:", "        pass", LOAD),
                           ("match []:", "    case [*TARGET]:", "        pass", LOAD),
                           ("match {}:", "    case {**TARGET}:", "        pass", LOAD)])

    @unittest.skipUnless(sys.version_info >= (3, 12), 'type parameters parse from Python 3.12')
    def test_type_parameters_refuse(self):
        self.rebound_each([("def f[TARGET]():", "    pass", LOAD), ("class C[*TARGET]:", "    pass", LOAD),
                           ("type Alias[**TARGET] = int", LOAD)])

    # Guard: a namespace the resolver cannot read resolves no name at all.
    def test_star_import_refuses(self):
        self.rebound("from os.path import *", LOAD)

    def test_namespace_writers_refuse(self):
        self.rebound_each([
            ("globals()['TARGET'] = '/unfrozen.py'", LOAD), ("vars()['TARGET'] = '/unfrozen.py'", LOAD),
            ("locals()['TARGET'] = '/unfrozen.py'", LOAD), ("exec(\"TARGET = '/unfrozen.py'\")", LOAD),
            ("eval(\"(TARGET := '/unfrozen.py')\")", LOAD),
            ("import sys", "setattr(sys.modules[__name__], 'TARGET', '/unfrozen.py')", LOAD),
            ("import sys", "delattr(sys.modules[__name__], 'TARGET')", LOAD),
            ("import builtins", "builtins.exec(\"TARGET = '/unfrozen.py'\")", LOAD),
            ("from builtins import exec as run", "run(\"TARGET = '/unfrozen.py'\")", LOAD)])

    def test_dict_access_refuses(self):
        self.rebound("import sys", "sys.modules[__name__].__dict__['TARGET'] = '/unfrozen.py'", LOAD)

    # Guard: an attribute store under the name rebinds it through the module object.
    def test_attribute_rebinding_refuses(self):
        self.rebound("import sys", "sys.modules[__name__].TARGET = '/unfrozen.py'", LOAD)

    # Guard: an attribute store under a name the resolver interprets (os.path.join and its siblings).
    def test_patched_path_function_refuses(self):
        self.rebound("import os", "os.path.join = lambda *parts: '/unfrozen.py'",
                     "spec = importlib.util.spec_from_file_location('hook', os.path.join(TARGET))")

    # The builtins the resolver reads by name hold only while the file never rebinds them.
    def own_directory_hook(self, *lines):
        (self.bin / 'hook.py').write_text('VALUE=1\n')
        self.entry('\n'.join(['import os, importlib.util'] + list(lines)
                             + ["spec = importlib.util.spec_from_file_location('hook', HOOK)"]) + '\n')

    def test_path_class_resolves(self):
        self.own_directory_hook("from pathlib import Path", "HOOK = Path(__file__).resolve().parent / 'hook.py'")
        d, _ = self.frozen()
        self.assertEqual(d['resolved_loaders'][0]['target'], str(self.bin / 'hook.py'))

    # Guard (Codex check-in 2, finding 7): the trusted names obey the attribute store rule too, and a module can
    # be swapped under an import through sys.modules.
    def test_file_rebound_through_the_module_object_refuses(self):
        self.own_directory_hook("import sys", "sys.modules[__name__].__file__ = '/elsewhere/entry.py'",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_os_rebound_through_the_module_object_refuses(self):
        self.own_directory_hook("import sys", "sys.modules[__name__].os = None",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_a_module_swapped_in_sys_modules_refuses(self):
        self.own_directory_hook("import sys", "sys.modules['os'] = sys.modules['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    # Codex check-in 3, finding 2: the sys.modules guard read only that literal spelling.
    def test_a_module_swapped_through_an_aliased_sys_refuses(self):
        self.own_directory_hook("import sys as s", "s.modules['os'] = s.modules['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_a_module_swapped_through_an_imported_modules_name_refuses(self):
        self.own_directory_hook("from sys import modules", "modules['os'] = modules['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    # Codex check-in 4, finding 2: matching the subscript target by name still depended on spelling.
    def test_modules_imported_from_sys_under_any_name_refuses(self):
        self.own_directory_hook("from sys import modules as cache", "cache['os'] = cache['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_sys_modules_bound_to_a_name_then_stored_into_refuses(self):
        self.own_directory_hook("import sys", "m = sys.modules", "m['os'] = m['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    # Codex check-in 5, finding 1: getattr(sys, "modules") reaches sys.modules through a string.
    def test_modules_reached_through_a_string_refuses(self):
        self.own_directory_hook("import sys", "cache = getattr(sys, 'modules')", "cache['os'] = cache['posixpath']",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_rebound_file_refuses(self):
        self.own_directory_hook("__file__ = '/elsewhere/entry.py'",
                                "HOOK = os.path.join(os.path.dirname(__file__), 'hook.py')")
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_rebound_os_refuses(self):
        self.own_directory_hook("from types import SimpleNamespace",
                                "os = SimpleNamespace(path=SimpleNamespace(join=lambda *parts: '/unfrozen.py'))",
                                "HOOK = os.path.join(%r, 'hook.py')" % str(self.bin))
        self.assertIn(REFUSED, self.refuses().stdout)

    def test_rebound_path_refuses(self):
        self.own_directory_hook("from pathlib import Path", "def Path(text):", "    return '/unfrozen.py'",
                                "HOOK = Path(%r)" % str(self.bin / 'hook.py'))
        self.assertIn(REFUSED, self.refuses().stdout)


class LoaderKinds(Loaders):
    """A resolved loader target (2026-09-26 finding) was scanned for its own imports only when its name ended
    in .py, but SourceFileLoader compiles its file as Python source whatever the suffix, and
    spec_from_file_location picks its loader from importlib's own suffix lists. The scan decision now follows
    the loader: SourceFileLoader is always source; spec_from_file_location is source on a source suffix, the
    native closure on an extension suffix, and NO-DATA on bytecode or anything else. Targets live outside the
    runtime directory, so only the loader site can bring them into the closure; each imports viahook, which
    lands in the manifest only when the target was scanned as source."""

    def target(self, name, text='import viahook\n'):
        (self.lib / 'viahook.py').write_text('VALUE=1\n')
        hooks = self.r / 'hooks'; hooks.mkdir(exist_ok=True)
        path = hooks / name
        path.write_bytes(text) if isinstance(text, bytes) else path.write_text(text)
        return path

    def source_file_loader(self, target):
        self.entry("from importlib.machinery import SourceFileLoader\n"
                   "loader = SourceFileLoader('hook', %r)\n" % str(target))

    def spec_from(self, target, extra=''):
        self.entry("import importlib.util\n"
                   "spec = importlib.util.spec_from_file_location('hook', %r%s)\n" % (str(target), extra))

    def test_source_file_loader_scans_any_suffix(self):
        for name in ('hook.txt', 'hook', 'hook' + SUFFIX, 'hook.pyc'):
            with self.subTest(name):
                target = self.target(name); self.source_file_loader(target)
                d, _ = self.frozen()
                self.assertIn(str(target), d['files'])
                self.assertIn(str(self.lib / 'viahook.py'), d['files'], 'SourceFileLoader runs %s as source' % name)

    def test_source_file_loader_target_with_missing_import_refuses(self):
        self.source_file_loader(self.target('hook.txt', 'import missing_dependency_for_review_probe\n'))
        self.assertIn('unresolved import missing_dependency_for_review_probe', self.refuses().stdout)

    def test_spec_from_file_location_source_suffix_is_scanned(self):
        target = self.target('hook.py'); self.spec_from(target)
        d, _ = self.frozen()
        self.assertIn(str(self.lib / 'viahook.py'), d['files'])

    def test_spec_from_file_location_extension_suffix_takes_the_native_closure(self):
        fake = self.r / 'fakebin'; fake.mkdir()
        tool = fake / 'otool'; tool.write_text(FAKE_OTOOL); tool.chmod(0o755)
        target = self.target('hook' + SUFFIX, b'\xcf\xfa\xed\xfe fixture extension')
        dep = target.parent / 'libdep.dylib'; dep.write_bytes(b'dep v1')
        (self.r / 'otool-spec.json').write_text(json.dumps({target.name: {
            'dylibs': ['@loader_path/libdep.dylib', '/usr/lib/libSystem.B.dylib'], 'rpaths': []}}))
        self.env.update(PATH=str(fake) + os.pathsep + os.environ.get('PATH', ''),
                        FAKE_OTOOL_SPEC=str(self.r / 'otool-spec.json'))
        self.spec_from(target)
        d, _ = self.frozen()
        self.assertEqual(d['native'][str(target)]['linked'], [str(dep)])
        self.assertIn(str(dep), d['files'])
        self.run_tool('verify', str(self.out))
        dep.write_bytes(b'dep v2')
        self.run_tool('verify', str(self.out), code=1)

    def test_spec_from_file_location_bytecode_or_other_suffix_refuses(self):
        for name in ('hook.pyc', 'hook.txt', 'hook'):
            with self.subTest(name):
                self.spec_from(self.target(name))
                self.assertIn('loader target runs neither as source nor as an extension', self.refuses().stdout)

    def test_spec_from_file_location_loader_keywords_refuse(self):
        for extra in (', loader=object()', ', submodule_search_locations=[]'):
            with self.subTest(extra):
                self.spec_from(self.target('hook.py'), extra)
                self.assertIn(REFUSED, self.refuses().stdout)


if __name__ == '__main__':
    # Only this file's own cases: the inherited Freeze cases run in test_freeze_manifest.py.
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(cls(n) for cls in (Loaders, Bindings, LoaderKinds)
                               for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())

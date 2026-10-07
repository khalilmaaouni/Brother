#!/usr/bin/env python3
"""D-20: the freeze resolves native extension modules instead of refusing every one.

Three findings on the staged candidate (evidence d20/SURVEY-AND-N4-REPRO.txt, 2026-09-26):
N1 an extension module (duckdb's _duckdb .so) stopped the freeze as 'binary import dependency
closure unavailable'; N2 a name the extension creates at init (_duckdb._sqltypes) was an
unresolved required import; N3 an import the caller treats as optional (unit_ledger's lazy duckdb)
stopped the freeze on the interpreter that lacks it. Every case below runs the CLI entry point in
fixture directories. The native load closure is read from `otool -l` load commands found on PATH,
so all but one case use a fake otool that prints the same shape as the real tool; the last case
uses the real otool on a real extension binary copied from this interpreter.
"""
import importlib.machinery as machinery
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_freeze_manifest import Freeze

SUFFIX = machinery.EXTENSION_SUFFIXES[0]
FAKE_OTOOL = r'''#!/usr/bin/env python3
import json, os, sys
spec = json.load(open(os.environ['FAKE_OTOOL_SPEC']))
if os.environ.get('FAKE_OTOOL_FAIL'):
    print('fake otool failure', file=sys.stderr); sys.exit(1)
if sys.argv[1:2] != ['-l'] or len(sys.argv) != 3:
    print('fake otool: only -l <file> is supported', file=sys.stderr); sys.exit(64)
path = sys.argv[2]
entry = spec.get(os.path.basename(path), {'dylibs': ['/usr/lib/libSystem.B.dylib'], 'rpaths': []})
print(path + ':')
n = 0
print('Load command %d' % n); print('      cmd LC_SEGMENT_64'); print('  cmdsize 72'); n += 1
if entry.get('id'):
    print('Load command %d' % n); print('          cmd LC_ID_DYLIB'); print('      cmdsize 48')
    print('         name %s (offset 24)' % entry['id']); n += 1
for kind, name in [('LC_LOAD_DYLIB', d) for d in entry['dylibs']] + [('LC_LOAD_WEAK_DYLIB', d) for d in entry.get('weak', [])]:
    print('Load command %d' % n); print('          cmd %s' % kind); print('      cmdsize 56')
    print('         name %s (offset 24)' % name); print('   time stamp 2 Thu Jan  1 09:00:02 1970'); n += 1
for rp in entry['rpaths']:
    print('Load command %d' % n); print('          cmd LC_RPATH'); print('      cmdsize 32')
    print('         path %s (offset 12)' % rp); n += 1
'''


def real_extension():
    """A real Mach-O extension from this interpreter whose load commands name only system libraries."""
    dynload = Path(sysconfig.get_path('platstdlib')) / 'lib-dynload'
    for p in sorted(dynload.glob('*' + SUFFIX)):
        out = subprocess.run(['otool', '-l', str(p)], capture_output=True, text=True)
        names = [l.split()[1] for l in out.stdout.splitlines() if l.strip().startswith('name ')]
        if out.returncode == 0 and names and all(n.startswith(('/usr/lib/', '/System/Library/')) for n in names):
            return p, names
    return None, []


class NativeClosure(Freeze):
    def setUp(self):
        super().setUp()
        self.fake = self.r / 'fakebin'; self.fake.mkdir()
        tool = self.fake / 'otool'; tool.write_text(FAKE_OTOOL); tool.chmod(0o755)
        self.spec = {}
        self.env.update(PATH=str(self.fake) + os.pathsep + os.environ.get('PATH', ''),
                        FAKE_OTOOL_SPEC=str(self.r / 'otool-spec.json'))
        self.write_spec()

    def write_spec(self):
        (self.r / 'otool-spec.json').write_text(json.dumps(self.spec))

    def extension(self, name='nativefix', where=None):
        """An extension module file the import finders resolve. The freeze never executes it."""
        path = (where or self.lib) / (name + SUFFIX)
        path.write_bytes(b'\xcf\xfa\xed\xfe fixture extension ' + name.encode())
        return path

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
        self.run_tool(*self.write_args(*extra))
        return json.loads(self.out.read_text())

    # N1: an extension module and its load closure are frozen, not refused.
    def test_extension_module_is_frozen_with_its_bytes(self):
        so = self.extension(); self.entry('import nativefix\n')
        d = self.frozen()
        self.assertEqual(d['imports']['nativefix'], str(so))
        self.assertIn(str(so), d['files'])
        self.run_tool('verify', str(self.out))
        so.write_bytes(so.read_bytes() + b'changed')
        self.run_tool('verify', str(self.out), code=1)

    def test_system_libraries_are_recorded_by_install_name(self):
        so = self.extension(); self.entry('import nativefix\n')
        self.spec[so.name] = {'dylibs': ['/usr/lib/libc++.1.dylib', '/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation'], 'rpaths': []}
        self.write_spec()
        d = self.frozen()
        native = d['native'][str(so)]
        self.assertEqual(sorted(native['system']), ['/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation', '/usr/lib/libc++.1.dylib'])
        self.assertEqual(native['linked'], [])
        self.assertTrue(d['platform'], 'system libraries are only meaningful with the OS identity recorded')
        self.run_tool('verify', str(self.out))

    def test_loader_path_dependency_is_hashed_and_drift_is_caught(self):
        so = self.extension(); self.entry('import nativefix\n')
        dep = self.lib / 'libdep.dylib'; dep.write_bytes(b'dep v1')
        self.spec[so.name] = {'dylibs': ['@loader_path/libdep.dylib', '/usr/lib/libSystem.B.dylib'], 'rpaths': []}
        self.spec['libdep.dylib'] = {'id': '@rpath/libdep.dylib', 'dylibs': ['/usr/lib/libSystem.B.dylib'], 'rpaths': []}
        self.write_spec()
        d = self.frozen()
        self.assertIn(str(dep), d['files'])
        self.assertEqual(d['native'][str(so)]['linked'], [str(dep)])
        self.assertNotIn('@rpath/libdep.dylib', json.dumps(d['native']), 'LC_ID_DYLIB is the library naming itself, not a dependency')
        self.run_tool('verify', str(self.out))
        dep.write_bytes(b'dep v2')
        self.run_tool('verify', str(self.out), code=1)

    def test_rpath_dependency_resolves_through_lc_rpath(self):
        so = self.extension(); self.entry('import nativefix\n')
        vendor = self.r / 'vendor'; vendor.mkdir(); dep = vendor / 'libtcl.dylib'; dep.write_bytes(b'tcl')
        self.spec[so.name] = {'dylibs': ['@rpath/libtcl.dylib'], 'rpaths': ['@loader_path/../vendor']}
        self.write_spec()
        d = self.frozen()
        self.assertIn(str(dep.resolve()), [str(Path(p).resolve()) for p in d['native'][str(so)]['linked']])
        self.run_tool('verify', str(self.out))

    def test_transitive_native_dependency_is_followed(self):
        so = self.extension(); self.entry('import nativefix\n')
        a = self.lib / 'liba.dylib'; a.write_bytes(b'a'); b = self.lib / 'libb.dylib'; b.write_bytes(b'b')
        self.spec[so.name] = {'dylibs': ['@loader_path/liba.dylib'], 'rpaths': []}
        self.spec['liba.dylib'] = {'dylibs': ['@loader_path/libb.dylib'], 'rpaths': []}
        self.write_spec()
        d = self.frozen()
        self.assertIn(str(b), d['files'])
        b.write_bytes(b'b2')
        self.run_tool('verify', str(self.out), code=1)

    def test_absolute_non_system_dependency_is_hashed_not_trusted(self):
        so = self.extension(); self.entry('import nativefix\n')
        brew = self.r / 'opt'; brew.mkdir(); dep = brew / 'libfoo.dylib'; dep.write_bytes(b'foo')
        self.spec[so.name] = {'dylibs': [str(dep)], 'rpaths': []}
        self.spec[dep.name] = {'dylibs': [], 'rpaths': []}   # system names aggregate over the whole closure
        self.write_spec()
        d = self.frozen()
        self.assertIn(str(dep), d['files'])
        self.assertEqual(d['native'][str(so)]['system'], [])

    def test_absolute_library_dependencies_are_followed(self):
        # Homebrew style: libraries link each other by absolute path; the second library is part of the closure.
        so = self.extension(); self.entry('import nativefix\n')
        brew = self.r / 'opt'; brew.mkdir(); a = brew / 'liba.dylib'; a.write_bytes(b'a'); b = brew / 'libb.dylib'; b.write_bytes(b'b')
        self.spec[so.name] = {'dylibs': [str(a)], 'rpaths': []}
        self.spec[a.name] = {'dylibs': [str(b)], 'rpaths': []}
        self.spec[b.name] = {'dylibs': [], 'rpaths': []}
        self.write_spec()
        d = self.frozen()
        self.assertIn(str(b), d['files'])
        b.write_bytes(b'b2')
        self.run_tool('verify', str(self.out), code=1)

    def test_unresolvable_dependency_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        self.spec['nativefix' + SUFFIX] = {'dylibs': ['@rpath/libmissing.dylib'], 'rpaths': ['@loader_path/nowhere']}
        self.write_spec()
        self.assertIn('libmissing', self.refuses().stdout)

    def test_missing_weak_dependency_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        self.spec['nativefix' + SUFFIX] = {'dylibs': ['/usr/lib/libSystem.B.dylib'], 'weak': ['@loader_path/libweak.dylib'], 'rpaths': []}
        self.write_spec()
        self.assertIn('libweak', self.refuses().stdout)

    def test_failing_otool_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        self.env['FAKE_OTOOL_FAIL'] = '1'
        self.assertIn('otool', self.refuses().stdout)

    def test_absent_otool_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        (self.fake / 'otool').unlink()
        self.env['PATH'] = str(self.fake)
        self.assertIn('otool', self.refuses().stdout)

    def test_otool_that_names_no_load_command_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        (self.fake / 'otool').write_text('#!/bin/sh\nexit 0\n')
        self.assertIn('otool', self.refuses().stdout)

    def test_native_closure_change_is_drift_even_with_equal_bytes(self):
        so = self.extension(); self.entry('import nativefix\n')
        self.frozen()
        self.spec[so.name] = {'dylibs': ['/usr/lib/libSystem.B.dylib', '/usr/lib/libz.1.dylib'], 'rpaths': []}
        self.write_spec()
        self.run_tool('verify', str(self.out), code=1)

    def test_tampered_native_record_is_drift(self):
        self.extension(); self.entry('import nativefix\n')
        d = self.frozen(); d['native'] = {}
        self.out.write_text(json.dumps(d))
        self.run_tool('verify', str(self.out), code=1)

    # N2: a name inside a frozen extension is provided by that binary.
    def test_name_created_by_extension_is_provided_by_its_binary(self):
        so = self.extension('_nat'); self.entry('from _nat._types import Thing\nimport _nat._inner\n')
        d = self.frozen()
        self.assertEqual(d['provided'].get('_nat._types'), '_nat')
        self.assertEqual(d['provided'].get('_nat._inner'), '_nat')
        so.write_bytes(so.read_bytes() + b'x')
        self.run_tool('verify', str(self.out), code=1)

    def test_python_module_has_no_invented_submodules(self):
        (self.lib / 'plainmod.py').write_text('VALUE=1\n'); self.entry('from plainmod.sub import thing\n')
        self.assertIn('plainmod.sub', self.refuses().stdout)

    # N3: an import the operator declares optional freezes its absence.
    def test_absent_import_without_declaration_still_refuses(self):
        self.entry('import absentmod\n')
        self.assertIn('absentmod', self.refuses().stdout)

    def test_declared_optional_absent_import_freezes_its_absence(self):
        self.entry('def view():\n    import absentmod\n')
        d = self.frozen('--optional-import', 'absentmod=rollups')
        self.assertEqual(d['absent'], ['absentmod'])
        self.assertEqual(d['optional_imports'], ['absentmod=rollups'])
        self.run_tool('verify', str(self.out))
        (self.lib / 'absentmod.py').write_text('VALUE=1\n')
        p = self.run_tool('verify', str(self.out), code=1)
        self.assertIn('FAIL', p.stdout)

    def test_optional_covers_its_submodules_only_on_a_dotted_boundary(self):
        self.entry('def view():\n    import absentmod.sub\n')
        d = self.frozen('--optional-import', 'absentmod=rollups')
        self.assertEqual(d['absent'], ['absentmod.sub'])
        self.out.unlink()
        self.entry('def view():\n    import absentmodx\n')
        self.refuses('--optional-import', 'absentmod=rollups')

    def test_optional_declaration_masks_no_other_missing_import(self):
        self.entry('def view():\n    import absentmod\n    import othermissing\n')
        self.assertIn('othermissing', self.refuses('--optional-import', 'absentmod=rollups').stdout)

    def test_present_optional_import_is_frozen_normally(self):
        (self.lib / 'presentmod.py').write_text('VALUE=1\n'); self.entry('import presentmod\n')
        d = self.frozen('--optional-import', 'presentmod=extra')
        self.assertEqual(d['absent'], [])
        self.assertIn(str(self.lib / 'presentmod.py'), d['files'])

    def test_invalid_optional_names_refuse(self):
        self.entry('import absentmod\n')
        for bad in ('', '.absentmod=x', 'absentmod.=x', 'a b=x', 'absent..mod=x', 'absentmod', 'absentmod=', 'absentmod=a b'):
            self.refuses('--optional-import', bad)

    def test_removing_the_declaration_from_the_manifest_is_not_a_pass(self):
        self.entry('def view():\n    import absentmod\n')
        d = self.frozen('--optional-import', 'absentmod=rollups'); del d['optional_imports']
        self.out.write_text(json.dumps(d))
        p = self.run_tool('verify', str(self.out), code=2)
        self.assertNotIn('PASS', p.stdout)

    def test_verify_reuses_the_recorded_declaration(self):
        self.entry('def view():\n    import absentmod\n')
        self.frozen('--optional-import', 'absentmod=rollups')
        self.run_tool('verify', str(self.out))

    # D-21: optional absence only for a proven optional capability, and the host identity is explicit.
    def test_module_level_absent_import_is_not_optional(self):
        self.entry('import absentmod\n')
        self.assertIn('absentmod', self.refuses('--optional-import', 'absentmod=rollups').stdout)

    def test_import_error_guarded_absent_import_is_optional(self):
        self.entry('try:\n    import absentmod\nexcept ImportError:\n    absentmod = None\n')
        self.assertEqual(self.frozen('--optional-import', 'absentmod=rollups')['absent'], ['absentmod'])

    def test_guard_that_catches_something_else_is_not_proof(self):
        self.entry('try:\n    import absentmod\nexcept KeyError:\n    absentmod = None\n')
        self.refuses('--optional-import', 'absentmod=rollups')

    def test_present_package_with_a_missing_submodule_refuses(self):
        pkg = self.lib / 'presentpkg'; pkg.mkdir(); (pkg / '__init__.py').write_text('VALUE=1\n')
        self.entry('def view():\n    import presentpkg.missing\n')
        self.assertIn('presentpkg.missing', self.refuses('--optional-import', 'presentpkg=extra').stdout)

    def test_absent_capability_is_reported_no_data_while_the_freeze_passes(self):
        self.entry('def view():\n    import absentmod\n')
        self.frozen('--optional-import', 'absentmod=rollups')
        p = self.run_tool('verify', str(self.out))
        self.assertIn('NO-DATA', p.stdout); self.assertIn('rollups', p.stdout)

    def test_platform_identity_is_explicit_and_drift_is_caught(self):
        d = self.frozen()
        for key in ('machine', 'os_build', 'python_version'):
            self.assertTrue(d['platform'].get(key), key)
        d['platform']['os_build'] = 'another-build'
        self.out.write_text(json.dumps(d))
        self.run_tool('verify', str(self.out), code=1)

    # Hostile read of the first builds (2026-09-26): each case below passed all three graded builds or broke them.
    def test_install_name_escaping_the_system_prefix_is_hashed_not_trusted(self):
        so = self.extension(); self.entry('import nativefix\n')
        evil = self.r / 'evil'; evil.mkdir(); dep = evil / 'libevil.dylib'; dep.write_bytes(b'evil')
        sneaky = '/usr/lib/../..' + str(dep)
        self.spec[so.name] = {'dylibs': [sneaky], 'rpaths': []}
        self.spec[dep.name] = {'dylibs': [], 'rpaths': []}
        self.write_spec()
        d = self.frozen()
        self.assertEqual(d['native'][str(so)]['system'], [], 'a path that leaves /usr/lib is not a system library')
        self.assertIn(os.path.normpath(sneaky), d['native'][str(so)]['linked'])
        self.assertIn(os.path.normpath(sneaky), d['files'])
        dep.write_bytes(b'evil v2')
        self.run_tool('verify', str(self.out), code=1)

    def test_bare_loader_path_rpath_is_the_binary_directory(self):
        so = self.extension(); self.entry('import nativefix\n')
        dep = self.lib / 'libdep.dylib'; dep.write_bytes(b'dep')
        self.spec[so.name] = {'dylibs': ['@rpath/libdep.dylib'], 'rpaths': ['@loader_path']}
        self.write_spec()
        d = self.frozen()
        self.assertEqual(d['native'][str(so)]['linked'], [str(dep)])

    def test_relative_rpath_never_resolves_against_the_working_directory(self):
        # The tool runs from the repository root, where scripts/test_freeze_manifest.py exists: a relative rpath
        # resolved against the working directory would freeze an arbitrary file and call it a library.
        self.extension(); self.entry('import nativefix\n')
        self.spec['nativefix' + SUFFIX] = {'dylibs': ['@rpath/test_freeze_manifest.py'], 'rpaths': ['scripts']}
        self.write_spec()
        self.assertIn('test_freeze_manifest.py', self.refuses().stdout)

    def test_relative_install_name_refuses(self):
        self.extension(); self.entry('import nativefix\n')
        self.spec['nativefix' + SUFFIX] = {'dylibs': ['scripts/test_freeze_manifest.py'], 'rpaths': []}
        self.write_spec()
        self.assertIn('test_freeze_manifest.py', self.refuses().stdout)


class RealOtool(Freeze):
    """The one case on the real otool: a real extension binary, copied, frozen and drifted."""
    def test_real_extension_binary(self):
        if sys.platform != 'darwin':
            (self.lib / ('realnative' + SUFFIX)).write_bytes(b'not darwin')
            (self.bin / 'entry.py').write_text('import realnative\n')
            args = ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib)]
            for k, v in self.configs.items():
                args += ['--config', k + '=' + v]
            p = self.run_tool(*args, code=2)
            self.assertIn('native', p.stdout)
            return
        self.assertTrue(shutil.which('otool'), 'otool is required on Darwin for the native closure')
        src, names = real_extension()
        self.assertIsNotNone(src, 'no system-only extension found in lib-dynload to use as a fixture')
        so = self.lib / ('realnative' + SUFFIX); shutil.copy2(str(src), str(so))
        (self.bin / 'entry.py').write_text('import realnative\n')
        d = self.freeze()
        self.assertEqual(sorted(d['native'][str(so)]['system']), sorted(set(names)))
        self.run_tool('verify', str(self.out))
        so.write_bytes(so.read_bytes() + b'\0')
        self.run_tool('verify', str(self.out), code=1)


if __name__ == '__main__':
    # Only this file's own cases: the inherited Freeze cases run in test_freeze_manifest.py.
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(cls(n) for cls in (NativeClosure, RealOtool) for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
    sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())

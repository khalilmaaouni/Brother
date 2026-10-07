#!/usr/bin/env python3
"""Freeze verification through the CLI, entirely in fixture directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL=Path(__file__).resolve().parent/'loop/freeze_manifest.py'
class Freeze(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        self.r=Path(self.t.name); self.bin=self.r/'bin'; self.bin.mkdir()
        self.lib=self.r/'lib'; self.lib.mkdir()
        (self.bin/'entry.py').write_text('import helper\n')
        (self.lib/'helper.py').write_text('import json\nVALUE=1\n')
        self.configs={}
        for kind in ('cap','burn','registry','roles','intake'):
            p=self.r/(kind+'.json');p.write_text('{}');self.configs[kind]=str(p)
        self.out=self.r/'manifest.json'
        self.env={k:v for k,v in os.environ.items() if not k.startswith('BROTHER_')}
        self.env.update(HOME=str(self.r),BROTHER_MODEL='fixture/model')

    def run_tool(self,*args,code=0,env=None,cwd=None):
        self.assertTrue(TOOL.is_file(),'freeze implementation required')
        p=subprocess.run([sys.executable,'-B',str(TOOL)]+list(args),env=env or self.env,text=True,capture_output=True,cwd=cwd)
        self.assertEqual(p.returncode,code,p.stdout+p.stderr);return p

    def freeze(self):
        args=['write',str(self.out),'--bin',str(self.bin),'--module-root',str(self.lib)]
        for k,v in self.configs.items():args+=['--config',k+'='+v]
        self.run_tool(*args)
        return json.loads(self.out.read_text())

    def test_proof_phase_and_run_identity_do_not_drift(self):
        self.env.update(BROTHER_PROOF_PHASE='RB',BROTHER_RUN_DIR=str(self.r/'RB'))
        self.freeze()
        changed=dict(self.env,BROTHER_PROOF_PHASE='RC',BROTHER_RUN_DIR=str(self.r/'RC'))
        self.run_tool('verify',str(self.out),env=changed)
        self.run_tool('verify',str(self.out),env=dict(changed,BROTHER_MODEL='changed'),code=1)

    def test_identical_and_external_import_recorded(self):
        d=self.freeze();self.run_tool('verify',str(self.out))
        self.assertIn(str(self.lib/'helper.py'),d['files'])
        self.assertEqual(d['imports']['helper'],str(self.lib/'helper.py'))
        self.assertEqual(set(d['configs']),set(self.configs))

    def test_byte_drift_external_dependency(self):
        self.freeze();(self.lib/'helper.py').write_text('VALUE=2\n')
        p=self.run_tool('verify',str(self.out),code=1);self.assertIn('helper.py',p.stdout)

    def test_new_runtime_file_is_drift(self):
        self.freeze();(self.bin/'new.py').write_text('VALUE=2')
        self.run_tool('verify',str(self.out),code=1)

    def test_config_and_env_drift(self):
        self.freeze();Path(self.configs['cap']).write_text('{"cap":2}')
        self.run_tool('verify',str(self.out),code=1)
        Path(self.configs['cap']).write_text('{}')
        self.run_tool('verify',str(self.out),code=1,env=dict(self.env,BROTHER_MODEL='different'))
        self.run_tool('verify',str(self.out),code=1,env=dict(self.env,BROTHER_NEW_MODEL='new'))

    def test_missing_file_no_data(self):
        self.freeze();(self.lib/'helper.py').unlink()
        self.run_tool('verify',str(self.out),code=2)

    def test_empty_or_malformed_manifest_no_data(self):
        for value in ({},{'schema':'loop-freeze-v1','files':{}},[],None):
            self.out.write_text(json.dumps(value));self.run_tool('verify',str(self.out),code=2)

    def test_import_shadowing_is_drift(self):
        self.freeze();(self.bin/'helper.py').write_text('VALUE=1\n')
        self.run_tool('verify',str(self.out),code=1)

    def test_computed_external_import_refuses_freeze(self):
        (self.bin/'entry.py').write_text('import importlib\nimportlib.import_module(module_name)\n')
        args=['write',str(self.out),'--bin',str(self.bin),'--module-root',str(self.lib)]
        for k,v in self.configs.items():args+=['--config',k+'='+v]
        self.run_tool(*args,code=2);self.assertFalse(self.out.exists())

    def test_transitive_import_and_package_initializer(self):
        pkg=self.lib/'pkg';pkg.mkdir();(pkg/'__init__.py').write_text('from . import child\n')
        (pkg/'child.py').write_text('VALUE=1\n');(self.lib/'helper.py').write_text('import pkg\n')
        d=self.freeze();self.assertIn(str(pkg/'child.py'),d['files']);self.assertIn(str(pkg/'__init__.py'),d['files'])

    def test_verification_receipt_bound_to_manifest(self):
        self.freeze();receipt=self.r/'check.json'
        self.run_tool('verify',str(self.out),'--receipt',str(receipt),'--run-id','RB','--phase','start')
        d=json.loads(receipt.read_text());self.assertEqual(d['verdict'],'PASS');self.assertEqual(d['run_id'],'RB')
        self.assertEqual(len(d['manifest_sha256']),64)

class ExtraFreeze(Freeze):
 def test_valid_shape_empty_files_is_no_data(self):
  d=self.freeze();d['files']={};self.out.write_text(json.dumps(d));self.run_tool('verify',str(self.out),code=2)
 def test_missing_required_category_no_data(self):
  d=self.freeze();del d['configs']['intake'];self.out.write_text(json.dumps(d));self.run_tool('verify',str(self.out),code=2)
 def test_symlink_retarget_is_drift(self):
  a=self.r/'a';b=self.r/'b';a.write_text('same');b.write_text('same');p=self.bin/'link';p.symlink_to(a)
  self.freeze();p.unlink();p.symlink_to(b);self.run_tool('verify',str(self.out),code=1)

class PairFreeze(Freeze):
    """B5-06, B5-09, B5-11 and the optional configs: one condition per case, each driven through the CLI.
    Not imported by the other freeze suites (they import Freeze only), so these run once."""

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith('PYTHON')]:
            del self.env[key]
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def write_args(self):
        args = ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib)]
        for k, v in self.configs.items():
            args += ['--config', k + '=' + v]
        return args

    def failures(self, p):
        return [l for l in p.stdout.splitlines() if l.startswith(('FAIL', 'NO-DATA'))]

    def run_with(self, python, *args, code=0, env=None):
        p = subprocess.run([str(python), '-B', str(TOOL)] + list(args), env=env or self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr); return p

    def user_site(self):
        p = subprocess.run([sys.executable, '-c', 'import site; print(site.getusersitepackages())'],
                           env=self.env, text=True, capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return Path(p.stdout.strip())

    # Pinned because a sweep of this module lands its first mutations here: each refusal names its cause.
    def test_a_missing_runtime_directory_refuses_by_name(self):
        args = self.write_args(); args[args.index('--bin') + 1] = str(self.r / 'no-such-bin')
        p = self.run_tool(*args, code=2)
        self.assertIn('runtime directory unavailable', p.stdout)

    def test_a_spec_with_no_origin_is_never_the_standard_library(self):
        from types import SimpleNamespace
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'loop'))
        try:
            import freeze_manifest as F
        finally:
            sys.path.remove(str(Path(__file__).resolve().parent / 'loop'))
        self.assertFalse(F.is_stdlib(SimpleNamespace(origin=None)))
        self.assertFalse(F.is_stdlib(SimpleNamespace(origin='')))

    # F-1: the per run values the driver derives are run identity, recorded per run, never frozen.
    def test_stop_hour_and_proof_run_dir_are_run_identity(self):
        self.env.update(BROTHER_STOP_HOUR='4', BROTHER_SCOPE='x')
        self.freeze()
        rc = dict(self.env, BROTHER_STOP_HOUR='12', BROTHER_PROOF_RUN_DIR='/a', BROTHER_PROOF_PHASE='RC')
        p = self.run_tool('verify', str(self.out), env=rc)
        self.assertNotIn('FAIL drift in environment', p.stdout)

    def test_a_frozen_setting_beside_the_run_identity_still_drifts(self):
        self.env.update(BROTHER_STOP_HOUR='4', BROTHER_SCOPE='x')
        self.freeze()
        p = self.run_tool('verify', str(self.out), env=dict(self.env, BROTHER_SCOPE='y'), code=1)
        self.assertEqual(self.failures(p), ['FAIL drift in environment'])

    # F-2: what finds the tools and steers the interpreter is frozen.
    def test_path_is_frozen(self):
        self.freeze()
        moved = dict(self.env, PATH=self.env.get('PATH', '') + os.pathsep + str(self.r / 'elsewhere'))
        p = self.run_tool('verify', str(self.out), env=moved, code=1)
        self.assertEqual(self.failures(p), ['FAIL drift in environment'])

    def test_every_python_variable_is_frozen(self):
        self.freeze()
        p = self.run_tool('verify', str(self.out), env=dict(self.env, PYTHONNOUSERSITE='1'), code=1)
        self.assertEqual(self.failures(p), ['FAIL drift in environment'])

    # F-3: executable interpreter startup refuses; a path only .pth is frozen by its bytes.
    def test_a_usercustomize_in_the_user_site_refuses_the_write(self):
        site = self.user_site(); site.mkdir(parents=True)
        (site / 'usercustomize.py').write_text('VALUE = 1\n')
        p = self.run_tool(*self.write_args(), code=2)
        self.assertIn('executable interpreter startup is not frozen', p.stdout)
        self.assertIn('usercustomize', p.stdout)
        self.assertFalse(self.out.exists())

    def test_an_executable_pth_in_the_user_site_refuses_the_write(self):
        site = self.user_site(); site.mkdir(parents=True)
        (site / 'evil.pth').write_text('import os\n')
        p = self.run_tool(*self.write_args(), code=2)
        self.assertIn('executable interpreter startup is not frozen', p.stdout)
        self.assertIn('evil.pth', p.stdout)

    def test_a_path_only_pth_added_after_the_write_is_startup_drift(self):
        site = self.user_site(); site.mkdir(parents=True)
        self.freeze()
        (site / 'paths.pth').write_text(str(self.r / 'no-such-dir') + '\n')
        p = self.run_tool('verify', str(self.out), code=1)
        self.assertEqual(self.failures(p), ['FAIL drift in startup'])

    def test_a_sitecustomize_on_the_python_path_refuses_the_write(self):
        inject = self.r / 'inject'; inject.mkdir()
        (inject / 'sitecustomize.py').write_text('import startup_helper\n')
        (inject / 'startup_helper.py').write_text('VALUE = 1\n')
        p = self.run_tool(*self.write_args(), env=dict(self.env, PYTHONPATH=str(inject)), code=2)
        self.assertIn('executable interpreter startup is not frozen', p.stdout)
        self.assertIn('sitecustomize', p.stdout)

    def test_an_executable_pth_the_loop_user_cannot_write_is_frozen_by_its_bytes(self):
        # Measured 2026-09-26: /usr/bin/python3 (3.9.6) ships distutils-precedence.pth, an `import os` line, in a
        # root owned site-packages. Nothing the loop runs as can change it, so it is frozen like any byte, not refused.
        venv = self.r / 'venv'
        made = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], env=self.env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        python = venv / 'bin/python3'
        sp = Path(subprocess.run([str(python), '-c', 'import site; print(site.getsitepackages()[0])'],
                                 env=self.env, capture_output=True, text=True).stdout.strip())
        pth = sp / 'vendor.pth'; pth.write_text('import os\n')
        pth.chmod(0o444); sp.chmod(0o555)
        self.addCleanup(sp.chmod, 0o755)
        self.run_with(python, *self.write_args())
        d = json.loads(self.out.read_text())
        self.assertIn('vendor.pth', d['startup'][str(sp)])
        self.run_with(python, 'verify', str(self.out))

    # F-4: the three optional configs the money path reads are frozen, their absence included.
    def default_write(self):
        return ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib)]

    def default_configs(self):
        state = self.r / 'state'; intake = self.r / '.claude/evidence/loop-intake'
        for p in (state, intake):
            p.mkdir(parents=True, exist_ok=True)
        for p in (state / 'cap-grant.json', self.r / 'model-registry.json', self.r / 'loop-roles.json',
                  self.r / 'loop-canary.json', intake / 'CURRENT.json', intake / 'launch-env.sh'):
            p.write_text('{}\n')
        return dict(self.env, BROTHER_OR_STATE_ROOT=str(state)), state

    def test_an_optional_config_that_appears_after_the_write_is_drift(self):
        for name in ('dispatch-limits.json', 'dispatch-policy.json', 'openrouter-models.json'):
            with self.subTest(config=name):
                env, state = self.default_configs()
                self.run_tool(*self.default_write(), env=env)
                (state / name).write_text('{}\n')
                p = self.run_tool('verify', str(self.out), env=env, code=1)
                self.assertEqual(self.failures(p), ['FAIL changed: ' + str(state / name)])
                (state / name).unlink()

    def test_an_optional_config_changed_after_the_write_is_drift(self):
        for name in ('dispatch-limits.json', 'dispatch-policy.json', 'openrouter-models.json'):
            with self.subTest(config=name):
                env, state = self.default_configs()
                (state / name).write_text('{}\n')
                self.run_tool(*self.default_write(), env=env)
                (state / name).write_text('{}\n\n')
                p = self.run_tool('verify', str(self.out), env=env, code=1)
                self.assertEqual(self.failures(p), ['FAIL changed: ' + str(state / name)])
                (state / name).unlink()

    def test_an_optional_config_removed_after_the_write_is_drift(self):
        env, state = self.default_configs()
        (state / 'dispatch-limits.json').write_text('{}\n')
        self.run_tool(*self.default_write(), env=env)
        (state / 'dispatch-limits.json').unlink()
        p = self.run_tool('verify', str(self.out), env=env, code=1)
        self.assertEqual(self.failures(p), ['FAIL changed: ' + str(state / 'dispatch-limits.json')])

    def test_a_manifest_without_a_startup_record_is_no_data(self):
        d = self.freeze(); del d['startup']; self.out.write_text(json.dumps(d))
        p = self.run_tool('verify', str(self.out), code=2)
        self.assertEqual(self.failures(p), ['NO-DATA missing startup'])

    def test_a_required_config_that_is_absent_still_refuses(self):
        env, state = self.default_configs()
        (state / 'cap-grant.json').unlink()
        p = self.run_tool(*self.default_write(), env=env, code=2)
        self.assertIn('cap-grant.json', p.stdout)

    # The flat install fallback `try: from . import x / except ImportError: import x` resolves x beside its file.
    def fallback_package(self, body):
        pkg = self.lib / 'pkg'; pkg.mkdir()
        (pkg / '__init__.py').write_text('')
        (pkg / 'sibling.py').write_text('VALUE = 1\n')
        (pkg / 'user.py').write_text(body)
        (self.bin / 'entry.py').write_text('import pkg.user\n')
        return pkg

    def test_a_flat_install_fallback_resolves_its_sibling(self):
        pkg = self.fallback_package('try:\n    from . import sibling\nexcept ImportError:\n    import sibling\n')
        d = self.freeze()
        self.assertIn(str(pkg / 'sibling.py'), d['files'])
        self.run_tool('verify', str(self.out))

    def test_a_sibling_wins_over_an_earlier_roots_module_of_the_same_name(self):
        # review 15, 2026-10-03: six names live in both scripts/ and scripts/loop/; a scripts/loop tool that imports one by
        # its bare name gets its SIBLING at run time (its own directory is sys.path[0]), and the freeze must stage that copy
        lib2 = self.r / 'lib2'; lib2.mkdir()
        (self.lib / 'dup.py').write_text('VALUE = "root one"\n')
        (lib2 / 'dup.py').write_text('VALUE = "sibling"\n')
        (lib2 / 'user.py').write_text('import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nimport dup\n')
        (self.bin / 'entry.py').write_text('import user\n')
        args = ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib), '--module-root', str(lib2)]
        for k, v in self.configs.items():
            args += ['--config', k + '=' + v]
        self.run_tool(*args)
        d = json.loads(self.out.read_text())
        self.assertEqual(d['imports']['dup'], str(lib2 / 'dup.py'), d['imports'])
        self.assertIn(str(lib2 / 'dup.py'), d['files'])
        self.assertNotIn(str(self.lib / 'dup.py'), d['files'])

    def test_an_unguarded_bare_sibling_import_still_refuses(self):
        self.fallback_package('import sibling\n')
        p = self.run_tool(*self.write_args(), code=2)
        self.assertIn('unresolved import sibling', p.stdout)

    def test_a_fallback_for_a_different_name_still_refuses(self):
        self.fallback_package('try:\n    from . import other\nexcept ImportError:\n    import sibling\n')
        p = self.run_tool(*self.write_args(), code=2)
        self.assertIn('unresolved import sibling', p.stdout)


class ModelExecutables(Freeze):
    """The loop runs the Claude and Codex executables model_router selects (claude_bin, codex_bin) by path, so their
    bytes are code the proof runs. Replacing either at its unchanged path kept verification PASS (Codex audit D1,
    2026-09-27: selected binaries frozen [False, False]). The freeze asks the frozen router which executables it
    selects and hashes each; an answer it cannot freeze refuses the write. One condition per case."""

    ROUTER = ('import os\n'
              'def claude_bin():\n    return os.environ["BROTHER_CLAUDE_BIN"]\n'
              'def codex_bin():\n    return os.environ["BROTHER_CODEX_BIN"]\n')

    def setUp(self):
        super().setUp()
        (self.bin / 'entry.py').write_text('import helper\nimport model_router\n')
        (self.bin / 'model_router.py').write_text(self.ROUTER)
        (self.r / 'cli').mkdir()
        self.claude = self.stub('claude', 'CLAUDE_OLD')
        self.codex = self.stub('codex', 'CODEX_OLD')
        self.env.update(BROTHER_CLAUDE_BIN=str(self.claude), BROTHER_CODEX_BIN=str(self.codex))

    def stub(self, name, text, mode=0o755, pad=0):
        p = self.r / 'cli' / name
        p.write_text('#!/bin/sh\n' + '#' * pad + '\nprintf "%%s\\n" %s\n' % text)
        p.chmod(mode)
        return p

    def write_args(self):
        args = ['write', str(self.out), '--bin', str(self.bin), '--module-root', str(self.lib)]
        for k, v in self.configs.items():
            args += ['--config', k + '=' + v]
        return args

    def refused(self, why, **kw):
        p = self.run_tool(*self.write_args(), code=2, **kw)
        self.assertTrue(p.stdout.startswith('NO-DATA: ' + why), p.stdout)
        self.assertFalse(self.out.exists())

    def test_each_selected_executable_is_frozen_by_its_bytes(self):
        import hashlib
        self.stub('claude', 'CLAUDE_OLD', pad=3 * 1024 * 1024)     # several read chunks, as a real CLI is
        d = self.freeze()
        self.assertEqual(d['files'][str(self.claude)]['size'], self.claude.stat().st_size)
        self.assertEqual(d['model_executables'], {'claude': str(self.claude), 'codex': str(self.codex)})
        for p in (self.claude, self.codex):
            self.assertEqual(d['files'][str(p)]['sha256'], hashlib.sha256(p.read_bytes()).hexdigest())
        self.run_tool('verify', str(self.out))

    def test_a_replaced_claude_at_the_same_path_is_drift(self):
        self.freeze(); self.stub('claude', 'CLAUDE_NEW')
        p = self.run_tool('verify', str(self.out), code=1)
        self.assertIn('FAIL changed: %s' % self.claude, p.stdout.splitlines())

    def test_a_replaced_codex_at_the_same_path_is_drift(self):
        self.freeze(); self.stub('codex', 'CODEX_NEW')
        p = self.run_tool('verify', str(self.out), code=1)
        self.assertIn('FAIL changed: %s' % self.codex, p.stdout.splitlines())

    def test_a_selected_executable_that_does_not_exist_refuses_the_write(self):
        self.env['BROTHER_CLAUDE_BIN'] = str(self.r / 'cli/absent')
        self.refused('model executable claude cannot be frozen')

    def test_a_selected_directory_refuses_the_write(self):
        # A directory passes an execute permission check; only the file check refuses it.
        self.env['BROTHER_CLAUDE_BIN'] = str(self.r / 'cli')
        self.refused('model executable claude cannot be frozen')

    def test_the_selection_is_asked_under_the_environment_being_frozen(self):
        sys.path.insert(0, str(TOOL.parent))
        try:
            import freeze_manifest as F
        finally:
            sys.path.remove(str(TOOL.parent))
        other = self.stub('other', 'OTHER')
        d = F.build(str(self.bin), [str(self.lib)], self.configs, env=dict(self.env, BROTHER_CLAUDE_BIN=str(other)))
        self.assertEqual(d['model_executables']['claude'], str(other))

    def test_a_selected_file_that_cannot_run_refuses_the_write(self):
        self.stub('codex', 'CODEX_OLD', mode=0o644)
        self.refused('model executable codex cannot be frozen')

    def test_a_relative_answer_refuses_the_write(self):
        # A relative path names a different file for every working directory the loop runs in.
        self.env['BROTHER_CODEX_BIN'] = 'cli/codex'
        self.refused('model executable codex cannot be frozen', cwd=str(self.r))

    def test_a_router_that_refuses_refuses_the_write(self):
        del self.env['BROTHER_CODEX_BIN']
        self.refused('model router %s gave no answer' % (self.bin / 'model_router.py'))

    def test_a_router_that_answers_then_fails_refuses_the_write(self):
        (self.bin / 'model_router.py').write_text(
            'import atexit, os, sys\natexit.register(lambda: (sys.stdout.flush(), os._exit(3)))\n' + self.ROUTER)
        self.refused('model router %s gave no answer' % (self.bin / 'model_router.py'))

    def test_a_router_whose_answer_is_not_the_answer_refuses_the_write(self):
        (self.bin / 'model_router.py').write_text('print("noise")\n' + self.ROUTER)
        self.refused('model router %s gave no answer' % (self.bin / 'model_router.py'))


if __name__=='__main__':unittest.main()

"""D-21 N4: the DEPLOYED burn guard funds a proof run from a flat bin, the way loop_until.sh runs it.

The deploy copies scripts/loop/*.py into one flat directory (~/.claude/bin) and the driver runs
`python3 -B ~/.claude/bin/burn_guard.py` with no PYTHONPATH. In proof phase burn_guard imported
plugin.runtime.brother.core and scripts.loop.proof_ledger, which a flat bin cannot resolve, so it printed
"NO-DATA: proof funding unreadable (No module named 'plugin')" and funded 0 lanes on both interpreters
(evidence d20/SURVEY-AND-N4-REPRO.txt). The existing CLI case sets PYTHONPATH to the repository root, which
hides exactly this. Every case here runs a flat copy with PYTHONPATH removed.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile, unittest
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE), str(HERE / 'loop'), str(REPO)]
import test_proof_burn_accounting as T


class FlatBin(T.Funding):
    def setUp(self):
        super().setUp()
        self.flat = Path(tempfile.mkdtemp(prefix='flat-bin-')); self.addCleanup(shutil.rmtree, str(self.flat), True)
        for src in (HERE / 'loop').glob('*.py'):
            shutil.copy2(str(src), str(self.flat / src.name))
        self.elsewhere = Path(tempfile.mkdtemp(prefix='not-a-repo-')); self.addCleanup(shutil.rmtree, str(self.elsewhere), True)

    def flat_cli(self, cwd, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'BROTHER_REPO_ROOT', 'BROTHER_CODEX_ROOT')}
        env.update(BROTHER_OR_STATE_ROOT=str(self.state), **extra)
        return subprocess.run([sys.executable, '-B', str(self.flat / 'burn_guard.py')], cwd=str(cwd), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_flat_bin_with_a_code_root_funds_from_any_directory(self):
        # U3 (B5-04): the plugin runtime is imported from the frozen code root the launcher names, never from the cwd
        self.paid(); r = self.flat_cli(self.elsewhere, BROTHER_CODE_ROOT=str(REPO))
        self.assertNotIn('No module named', r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('MONEY   spent 0.25000000', r.stdout)
        self.assertEqual(r.stdout.strip().splitlines()[-1], '8')

    def test_flat_bin_inside_a_worktree_without_a_code_root_refuses(self):
        # The driver runs inside its git worktree. Before U3 the guard imported that worktree's plugin runtime, code
        # the freeze never saw; now an unset code root in a proof phase funds nothing, wherever the guard runs.
        wt = Path(tempfile.mkdtemp(prefix='flat-wt-')); self.addCleanup(shutil.rmtree, str(wt), True)
        subprocess.run(['git', 'init', '-q', str(wt)], check=True, timeout=60)
        (wt / 'plugin').symlink_to(REPO / 'plugin')
        (wt / 'scripts').symlink_to(REPO / 'scripts')
        self.paid(); r = self.flat_cli(wt, BROTHER_CODE_ROOT='')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn('BROTHER_CODE_ROOT', r.stdout)
        self.assertNotIn('MONEY   spent', r.stdout)
        self.assertEqual(r.stdout.strip().splitlines()[-1], '0')

    def test_flat_bin_imports_the_colocated_proof_ledger(self):
        # The copy installed beside burn_guard is the one frozen with it; a repository copy must not win.
        p = self.flat / 'proof_ledger.py'
        p.write_text(p.read_text() + "\n\ndef configuration(*a, **k):\n    raise ValueError('colocated proof_ledger used')\n")
        r = self.flat_cli(self.elsewhere, BROTHER_CODE_ROOT=str(REPO))
        self.assertIn('colocated proof_ledger used', r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], '0')

    def test_flat_bin_with_a_code_root_that_is_not_a_directory_refuses_without_money(self):
        self.paid(); r = self.flat_cli(self.elsewhere, BROTHER_CODE_ROOT=str(self.elsewhere / 'missing'))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn('MONEY   spent', r.stdout)
        self.assertEqual(r.stdout.strip().splitlines()[-1], '0')


if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(FlatBin(n) for n in loader.getTestCaseNames(FlatBin) if n in FlatBin.__dict__)
    sys.exit(not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful())

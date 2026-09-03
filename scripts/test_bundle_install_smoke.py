"""Calibration for scripts/bundle-install-smoke.sh, the one-install proof.

The property under test is NOT that the install passes. It is that the
versions the umbrella PROMISES and the versions it DELIVERS are read from
the same place, because on 2026-08-24 they were not: in --github mode the
script installed from the remote and read the promise from the local tree.
It failed correctly against the published surface, a local repin was made,
and the same check then PASSED before anything was pushed. A pass over a
published surface that was still wrong.

So the cases here poison the LOCAL manifest with a version that cannot occur
(999.0.0) and watch which number the script reports.

No real install happens. A stub `claude` on PATH answers the binary check and
then fails the marketplace add, which is AFTER the promises line is printed
and is all these cases need. Keeping the network out entirely would mean
stubbing curl too, and the reachable case is deliberately left real: the
refusal case is the one that must never depend on a mock, and it uses a dead
path under the real host.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'scripts', 'bundle-install-smoke.sh')

POISON = '999.0.0'
LOCAL_MANIFEST = (
    '{"plugins": ['
    '{"name": "brothermode", "version": "%s"},'
    '{"name": "brothersbe", "version": "%s"}]}' % (POISON, POISON)
)

STUB_CLAUDE = "#!/bin/sh\necho 'stub claude: refusing on purpose' >&2\nexit 1\n"


def build_tree(tmp, dead_url=False):
    """A throwaway tree holding the script, a POISONED local manifest, and a
    stub claude on PATH."""
    os.makedirs(os.path.join(tmp, 'scripts'))
    os.makedirs(os.path.join(tmp, '.claude-plugin'))
    os.makedirs(os.path.join(tmp, 'bin'))

    with open(SCRIPT, encoding='utf-8') as fh:
        text = fh.read()
    if dead_url:
        text = text.replace('main/.claude-plugin/marketplace.json',
                            'main/.claude-plugin/NO-SUCH-FILE-FOR-TESTS.json')
    dest = os.path.join(tmp, 'scripts', 'bundle-install-smoke.sh')
    with open(dest, 'w', encoding='utf-8') as fh:
        fh.write(text)

    with open(os.path.join(tmp, '.claude-plugin', 'marketplace.json'), 'w',
              encoding='utf-8') as fh:
        fh.write(LOCAL_MANIFEST)

    stub = os.path.join(tmp, 'bin', 'claude')
    with open(stub, 'w', encoding='utf-8') as fh:
        fh.write(STUB_CLAUDE)
    os.chmod(stub, 0o755)
    return dest


def run(tmp, dest, *args):
    env = dict(os.environ)
    env['PATH'] = os.path.join(tmp, 'bin') + os.pathsep + env.get('PATH', '')
    return subprocess.run(['sh', dest, *args], capture_output=True, text=True,
                          env=env, timeout=180)


class PromiseAndDeliveryComeFromOnePlace(unittest.TestCase):

    def test_github_mode_ignores_the_local_manifest(self):
        """The regression. If the script ever reads the local manifest again in
        --github mode, the poisoned 999.0.0 surfaces in the promises line."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = build_tree(tmp)
            r = run(tmp, dest, '--github')
            self.assertIn('promises read from: the published manifest at main',
                          r.stdout, r.stdout + r.stderr)
            self.assertIn('umbrella promises', r.stdout, r.stdout + r.stderr)
            self.assertNotIn(POISON, r.stdout,
                             'the local manifest was read in --github mode')

    def test_path_mode_does_read_the_local_manifest(self):
        """The other half. Path mode proves the tree it is run in, so there the
        local manifest is the correct source and the poison MUST appear."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = build_tree(tmp)
            r = run(tmp, dest)
            self.assertIn('promises read from: this tree', r.stdout)
            self.assertIn(POISON, r.stdout)

    def test_unfetchable_published_manifest_refuses_instead_of_falling_back(self):
        """The important one. Falling back to the local manifest would restore
        the original defect exactly, so absence of the published manifest must
        stop the run rather than quietly change what is being compared."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = build_tree(tmp, dead_url=True)
            r = run(tmp, dest, '--github')
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('refusing to fall back', r.stdout + r.stderr)
            self.assertNotIn('umbrella promises', r.stdout,
                             'it reported promises despite having no published manifest')
            self.assertNotIn(POISON, r.stdout)


if __name__ == '__main__':
    unittest.main()

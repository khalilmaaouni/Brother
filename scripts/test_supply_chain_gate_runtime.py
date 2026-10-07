"""Executable-position regressions for the runtime install scanner."""

import pathlib
import tempfile
import unittest

try:
    from . import supply_chain_gate
except ImportError:
    import supply_chain_gate


class RuntimePositionsTests(unittest.TestCase):
    def scan(self, source, name='task.py'):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8')
            return supply_chain_gate.scan_runtime_installs(root)

    def test_python_prose_is_not_a_finding(self):
        for source in (
            '"""Run pip install x before using this module."""\n',
            '# pip install x\n',
            'EXAMPLE = "pip install x"\n',
            'PATTERN = r"pip install"\n',
            'def example():\n    """pip install x"""\n',
            'print("pip install x")\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [])

    def test_literal_process_calls_block(self):
        for source in (
            'subprocess.run(["pip", "install", "x"])',
            'subprocess.run(\n    ["pip",\n     "install", "x"],\n    check=True)',
            'subprocess.check_call(args=[sys.executable, "-m", "pip", "install", package])',
            'subprocess.Popen(("npm", "install", "x"))',
            'subprocess.getoutput("pip install x")',
            'os.system("pip install x")',
            'os.popen("pip install x")',
            'os.execvp("pip", ["pip", "install", "x"])',
            'os.execvp(file="pip", args=["pip", "install", "x"])',
            'os.execve("pip", ["pip", "install", "x"], env=env)',
            'os.execlp("pip", "pip", "install", "x")',
            'os.spawnvpe(os.P_WAIT, "pip", ["pip", "install", "x"], env)',
            'os.spawnlp(os.P_WAIT, "pip", "pip", "install", "x")',
            'import subprocess as sp\nsp.run(["pip", "install", "x"])',
            'from os import system as execute\nexecute("pip install x")',
        ):
            with self.subTest(source=source):
                findings = self.scan(source)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]['classification'], 'runtime_install')
                self.assertEqual(findings[0]['severity'], 'BLOCK')
                self.assertGreater(findings[0]['line_no'], 0)

    def test_unparseable_python_is_line_scanned(self):
        # Python cannot run it, but a pasted install line is still caught; nothing else is a finding
        findings = self.scan('pip install x')
        self.assertEqual([f['severity'] for f in findings], ['BLOCK'])
        for source in ('def broken(:\n', 'x = "\x00"'):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [])

    def test_toml_comment_is_not_a_finding(self):
        self.assertEqual(self.scan('# pip install x\n[project]\nname = "x"\n', 'pyproject.toml'), [])

    def test_known_data_suffixes_are_not_unscanned(self):
        for suffix in ('.toml', '.json', '.md', '.yaml', '.csv', '.jsonl', '.diff', '.log'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.scan('pip install x', 'data' + suffix), [])
        for suffix in ('.html', '.out', '.gitkeep'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.scan('pip install x', 'page' + suffix), [])
        for suffix in ('.xyz', '.swift', '.env'):
            with self.subTest(suffix=suffix):
                findings = self.scan('pip install x', 'code' + suffix)
                self.assertEqual(findings[0]['classification'], 'runtime_install')
                self.assertEqual(findings[0]['severity'], 'BLOCK')

    def test_script_comments_ignored_and_commands_preserved(self):
        for name, comment in (('task.sh', '#'), ('task.js', '//')):
            with self.subTest(name=name):
                findings = self.scan('  ' + comment + ' pip install x\npip install x\n', name)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]['line_no'], 2)
                self.assertEqual(findings[0]['severity'], 'BLOCK')

    def test_temporary_test_installs_are_info(self):
        source = ('import tempfile, subprocess\n'
                  'with tempfile.TemporaryDirectory() as tmp:\n'
                  '    subprocess.run(["pip", "install", "--target", tmp, "x"])\n')
        for name in ('tests/install.py', 'test_install.py', 'install_test.py'):
            with self.subTest(name=name):
                findings = self.scan(source, name)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]['classification'], 'test_install_allowed')
                self.assertEqual(findings[0]['severity'], 'INFO')
        self.assertEqual(self.scan(source, 'contest.py')[0]['severity'], 'BLOCK')

    def test_only_command_arguments_are_scanned(self):
        for source in (
            'subprocess.run(["echo", "ok"], cwd="pip install x")',
            'subprocess.run(["echo", "ok"], env={"EXAMPLE": "pip install x"})',
            'subprocess.run(make_command("pip install x"))',
            'subprocess.CompletedProcess("pip install x", 0)',
            'subprocess.run([sys.executable, "-m", "venv", "--without-pip", target])',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [])

    def test_scanner_tests_have_no_filename_exemption(self):
        findings = self.scan('os.system("pip install x")', 'scripts/test_supply_chain_gate.py')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['severity'], 'INFO')


if __name__ == '__main__':
    unittest.main()

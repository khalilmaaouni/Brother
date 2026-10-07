import contextlib
import datetime
import io
import json
import os
import pathlib
import re
import runpy
import shutil
import sys
import subprocess
import tempfile
import unittest

try:
    from . import supply_chain_gate
except ImportError:
    import supply_chain_gate


class ManifestDiscoveryTests(unittest.TestCase):
    def test_hostile_inputs_refused(self):
        for bad in (None, "repo", 1, True, 1.0, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    supply_chain_gate.list_manifest_paths(bad)

    def test_sorted_and_exact_absent_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            paths = supply_chain_gate.list_manifest_paths(root)
            self.assertEqual(paths, sorted(paths))
            self.assertIn(root / "pyproject.toml", paths)
            self.assertIn(root / "setup.py", paths)

    def test_glob_patterns_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "requirements-dev.txt").write_text("x")
            (root / "requirements").mkdir()
            (root / "requirements" / "base.txt").write_text("x")
            (root / "Dockerfile").write_text("x")
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text("x")
            (root / "plugin" / "a").mkdir(parents=True)
            (root / "plugin" / "a" / "pyproject.toml").write_text("x")
            (root / "plugin" / "b").mkdir(parents=True)
            (root / "plugin" / "b" / "package.json").write_text("x")
            paths = supply_chain_gate.list_manifest_paths(root)
            for rel in (
                "requirements-dev.txt",
                "requirements/base.txt",
                "Dockerfile",
                ".github/workflows/ci.yml",
                "plugin/a/pyproject.toml",
                "plugin/b/package.json",
            ):
                self.assertIn(root / rel, paths)

    def test_newline_byte_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bad = root / "requirements\nbad.txt"
            bad.write_text("x")
            with self.assertRaises(ValueError):
                supply_chain_gate.list_manifest_paths(root)


    def test_a_root_that_is_not_a_folder_blocks_instead_of_listing_absent_manifests(self):
        # a missing checkout must never read as "nothing to audit"
        with tempfile.TemporaryDirectory() as d:
            gone = pathlib.Path(d) / "never-cloned"
            a_file = pathlib.Path(d) / "file"
            a_file.write_text("x")
            for bad in (gone, a_file):
                with self.assertRaises(ValueError, msg=str(bad)):
                    supply_chain_gate.list_manifest_paths(bad)

class RuntimeInstallTests(unittest.TestCase):
    def _write(self, root, rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + os.linesep, encoding='utf-8')
        return path

    def test_hostile_inputs_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for bad in (None, 'repo', 1, True, 1.0, float('nan'), b'x', [], {}, set()):
                with self.subTest(repo_root=bad):
                    with self.assertRaises(ValueError):
                        supply_chain_gate.scan_runtime_installs(bad)
                    with self.assertRaises(ValueError):
                        supply_chain_gate.iter_scan_files(bad)
                    with self.assertRaises(ValueError):
                        supply_chain_gate.reconcile_imports_against_rows([], bad)
            for bad_rows in (None, 'rows', 1, True, 1.0, float('nan'), {}, (1, 2)):
                with self.subTest(rows=bad_rows):
                    with self.assertRaises(ValueError):
                        supply_chain_gate.reconcile_imports_against_rows(bad_rows, root)
            for bad_row in ('not a dict', 1, None, True):
                with self.subTest(row=bad_row):
                    with self.assertRaises(ValueError):
                        supply_chain_gate.reconcile_imports_against_rows([bad_row], root)
            with self.assertRaises(ValueError):
                supply_chain_gate.scan_runtime_installs(root / 'never-cloned')
            with self.assertRaises(ValueError):
                supply_chain_gate.iter_scan_files(root / 'never-cloned')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit(None, 1, 'x')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit('a.py', 1, 'x')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit(pathlib.Path('a.py'), '1', 'x')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit(pathlib.Path('a.py'), True, 'x')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit(pathlib.Path('a.py'), -1, 'x')
            with self.assertRaises(ValueError):
                supply_chain_gate.classify_runtime_hit(pathlib.Path('a.py'), 1, None)

    def test_scan_newline_manifest_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'requirements\nbad.txt').write_text('x', encoding='utf-8')
            with self.assertRaises(ValueError):
                supply_chain_gate.list_manifest_paths(root)
            with self.assertRaises(ValueError):
                supply_chain_gate.scan_runtime_installs(root)

    def test_scan_newline_code_path_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'bad\nname.py').write_text('pip install x', encoding='utf-8')
            with self.assertRaises(ValueError):
                supply_chain_gate.iter_scan_files(root)
            with self.assertRaises(ValueError):
                supply_chain_gate.scan_runtime_installs(root)
            with self.assertRaises(ValueError):
                supply_chain_gate.reconcile_imports_against_rows([], root)

    def test_classify_newline_path_refused(self):
        with self.assertRaises(ValueError):
            supply_chain_gate.classify_runtime_hit(pathlib.Path('bad\nname.py'), 1, 'pip install x')

    def test_scan_venv_embed_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, '.venv-embed/run.py', 'os.system("pip install x")')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == '.venv-embed/run.py']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'runtime_install')
            self.assertEqual(hits[0]['severity'], 'BLOCK')

    def test_scan_threads_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'threads/evil.py', 'os.system("pip install x")')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'threads/evil.py']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'runtime_install')
            self.assertEqual(hits[0]['severity'], 'BLOCK')

    def test_scan_subprocess_pip_blocks(self):
        q = chr(34)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            line = 'subprocess.run([' + q + 'pip' + q + ', ' + q + 'install' + q + ', ' + q + 'x' + q + '])'
            self._write(root, 'task.py', line)
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['classification'] == 'runtime_install']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['file_path'], 'task.py')

    def test_scan_gitignore_still_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, '.gitignore', 'secret.py')
            self._write(root, 'secret.py', 'os.system("pip install x")')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'secret.py']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'runtime_install')

    def test_scan_unknown_text_suffix_is_line_scanned(self):
        # an unknown suffix used to read NO-DATA forever; text is now scanned, so an install BLOCKS
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'data.xyz', 'pip install x')
            self._write(root, 'notes.md', 'pip install x')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'data.xyz']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'runtime_install')
            self.assertEqual(hits[0]['severity'], 'BLOCK')
            self.assertEqual([f for f in findings if f['file_path'] == 'notes.md'], [])

    def test_scan_unknown_binary_suffix_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'blob.xyz').write_bytes(b'\xff\xfe\x00\x00pip install x')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'blob.xyz']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'unscanned_suffix')
            self.assertEqual(hits[0]['severity'], 'NO-DATA')

    def test_scan_words_inside_words_are_not_installers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'smoke.sh', 'say "FAIL: bundle-install"\necho "a pipe; install tree"\n')
            self.assertEqual(supply_chain_gate.scan_runtime_installs(root), [])

    def test_python_shebang_without_suffix_is_parsed_not_line_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'bin/tool', '#!/usr/bin/env python3\n"""No `pip install` step."""\nimport os\n')
            self._write(root, 'bin/bad', '#!/usr/bin/env python3\nimport os\nos.system("pip install x")\n')
            findings = supply_chain_gate.scan_runtime_installs(root)
            self.assertEqual([f['file_path'] for f in findings], ['bin/bad'])
            self.assertEqual(findings[0]['severity'], 'BLOCK')

    def test_scan_reads_only_tracked_files_in_a_git_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'shipped.sh', 'pip install shipped\n')
            self._write(root, 'stray.sh', 'pip install stray\n')
            git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@t']
            subprocess.run(git + ['init', '-q'], check=True)
            subprocess.run(git + ['add', 'shipped.sh'], check=True)
            findings = supply_chain_gate.scan_runtime_installs(root)
            self.assertEqual([f['file_path'] for f in findings], ['shipped.sh'])

    def test_scan_unknown_installer_family_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'x.py', 'os.system("cargo add foo")')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'x.py']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'unknown_installer_family')
            self.assertEqual(hits[0]['severity'], 'NO-DATA')

    def test_scan_unreadable_bytes_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'bad.py').write_bytes(b'\xff\xfe\x00\x00')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == 'bad.py']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'unparseable_python')
            self.assertEqual(hits[0]['severity'], 'NO-DATA')

    def test_build_time_allow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, '.github/workflows/ci.yml', 'pip install x')
            findings = supply_chain_gate.scan_runtime_installs(root)
            hits = [f for f in findings if f['file_path'] == '.github/workflows/ci.yml']
            self.assertTrue(hits)
            self.assertEqual(hits[0]['classification'], 'build_time_install_allowed')
            self.assertNotEqual(hits[0]['severity'], 'BLOCK')

    def test_excluded_roots_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, '.git/hooks/evil.py', 'pip install x')
            self._write(root, '__pycache__/evil.py', 'pip install x')
            self._write(root, 'node_modules/evil.py', 'pip install x')
            self.assertEqual(supply_chain_gate.scan_runtime_installs(root), [])

    def test_reconcile_bad_row_name_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'app.py', 'import json')
            findings = supply_chain_gate.reconcile_imports_against_rows([{'name': '!!bad name!!'}], root)
            blocks = [f for f in findings if f['severity'] == 'BLOCK']
            self.assertTrue(blocks)
            self.assertTrue(any(f['code'] == 'import_bad_row_name' for f in blocks))

    def test_reconcile_import_not_in_rows_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'app.py', 'import json\nimport zzz_l5fd_absent_package')
            findings = supply_chain_gate.reconcile_imports_against_rows([], root)
            blocks = [f for f in findings if f['code'] == 'import_not_in_rows']
            self.assertTrue(any('zzz_l5fd_absent_package' in f['message'] for f in blocks))
            self.assertEqual([f for f in blocks if 'json' in f['message']], [])

    def test_reconcile_stdlib_and_first_party_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'l5fd_local_mod.py', 'value = 1')
            self._write(root, 'app.py', 'import json\nimport l5fd_local_mod')
            findings = supply_chain_gate.reconcile_imports_against_rows([], root)
            self.assertEqual([f for f in findings if f['code'] == 'import_not_in_rows'], [])

    def test_reconcile_js_specifier_blocks(self):
        q = chr(34)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'app.js', 'import x from ' + q + 'zzz-js-absent-pkg' + q)
            findings = supply_chain_gate.reconcile_imports_against_rows([], root)
            blocks = [f for f in findings if f['code'] == 'import_not_in_rows']
            self.assertTrue(any('zzz-js-absent-pkg' in f['message'] for f in blocks))

    def test_reconcile_row_unreferenced_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'app.py', 'import json')
            findings = supply_chain_gate.reconcile_imports_against_rows([{'name': 'zzz_unused_row'}], root)
            nodata = [f for f in findings if f['code'] == 'row_unreferenced']
            self.assertTrue(nodata)
            self.assertEqual(nodata[0]['severity'], 'NO-DATA')

    def test_reconcile_unparseable_source_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._write(root, 'bad.py', 'def broken(:')
            findings = supply_chain_gate.reconcile_imports_against_rows([], root)
            nodata = [f for f in findings if f['code'] == 'unparseable_source']
            self.assertTrue(nodata)
            self.assertEqual(nodata[0]['severity'], 'NO-DATA')


_HERE = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_OBLIGATIONS_PATH = _REPO_ROOT / "scripts" / "gate_obligations.json"
_GATE_LINE_LITERAL = (
    'run_check "supply-chain-gate"   '
    'python3 scripts/supply_chain_gate.py --offline --repo .'
)


def _run_script_in_process(repo_path):
    """Run supply_chain_gate.py as __main__ in this process and return
    (SystemExit code, printed output); the code is None when the module
    carries no `if __name__` guard.
    subprocess is forbidden by the safety screen so runpy is the runner."""
    script = _HERE / "supply_chain_gate.py"
    saved_argv = list(sys.argv)
    sys.argv = [str(script), "--offline", "--repo", str(repo_path)]
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0, buffer.getvalue()
        if isinstance(code, int):
            return code, buffer.getvalue()
        return 1, buffer.getvalue()
    finally:
        sys.argv = saved_argv
    return None, buffer.getvalue()


class GateIntegrationTests(unittest.TestCase):

    def test_required_fast_has_the_supply_chain_gate_line(self):
        text = supply_chain_gate.required_fast_text(_REPO_ROOT)
        self.assertIn(_GATE_LINE_LITERAL, text)
        supply_chain_gate.assert_gate_placement(text)

    def test_shipped_gate_line_is_well_formed(self):
        text = supply_chain_gate.required_fast_text(_REPO_ROOT)
        matches = [
            line for _n, line in supply_chain_gate.find_run_check_lines(text)
            if "supply-chain-gate" in line
        ]
        self.assertEqual(len(matches), 1, "exactly one supply-chain-gate run_check line")
        line = matches[0]
        self.assertIn("--offline", line)
        self.assertIn("--repo .", line)

    def test_required_fast_text_refuses_non_path(self):
        for bad in (None, 42, "scripts", [], {}, True, b"x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    supply_chain_gate.required_fast_text(bad)

    def test_required_fast_text_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                supply_chain_gate.required_fast_text(pathlib.Path(tmp))

    def test_find_run_check_lines_refuses_non_str(self):
        for bad in (None, 42, [], {}, b"x", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    supply_chain_gate.find_run_check_lines(bad)

    def test_find_run_check_lines_finds_exactly_one_gate(self):
        text = supply_chain_gate.required_fast_text(_REPO_ROOT)
        names = []
        for _number, line in supply_chain_gate.find_run_check_lines(text):
            match = re.search(r'run_check\s+"([^"]+)"', line)
            if match:
                names.append(match.group(1))
        self.assertEqual(names.count("supply-chain-gate"), 1)

    def test_assert_gate_placement_refuses_missing_line(self):
        text = supply_chain_gate.required_fast_text(_REPO_ROOT)
        stripped = "\n".join(
            line for line in text.splitlines()
            if "supply-chain-gate" not in line
        )
        with self.assertRaises(ValueError):
            supply_chain_gate.assert_gate_placement(stripped)

    def test_assert_gate_placement_refuses_duplicate(self):
        text = supply_chain_gate.required_fast_text(_REPO_ROOT)
        gate_line = None
        for _number, line in supply_chain_gate.find_run_check_lines(text):
            if "supply-chain-gate" in line:
                gate_line = line
                break
        self.assertIsNotNone(gate_line, "required_fast.sh must declare the gate line")
        with self.assertRaises(ValueError):
            supply_chain_gate.assert_gate_placement(text + "\n" + gate_line + "\n")

    def test_assert_gate_placement_refuses_missing_offline(self):
        broken = (
            'run_check "supply-chain-gate"   python3 scripts/supply_chain_gate.py --repo .\n'
            'echo "pass $pass   fail $fail   no-data $nodata"\n'
        )
        with self.assertRaises(ValueError):
            supply_chain_gate.assert_gate_placement(broken)

    def test_assert_gate_placement_refuses_non_str(self):
        for bad in (None, 42, [], {}, b"x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    supply_chain_gate.assert_gate_placement(bad)

    def test_obligation_map_entry(self):
        self.assertTrue(_OBLIGATIONS_PATH.is_file(), "scripts/gate_obligations.json must ship")
        data = json.loads(_OBLIGATIONS_PATH.read_bytes().decode("utf-8"))
        self.assertIsInstance(data, dict)
        checks = data.get("checks")
        self.assertIsInstance(checks, dict)
        entry = checks.get("supply-chain-gate")
        self.assertIsInstance(entry, dict, "supply-chain-gate must be an explicit entry")
        self.assertEqual(entry.get("obligation"), "REQUIRED_FOR_MERGE")
        self.assertNotEqual(entry.get("obligation"), "OPTIONAL")
        self.assertNotEqual(entry.get("obligation"), "REQUIRED_FOR_RELEASE")
        self.assertNotIn("expected_absent_input", entry)
        reason = entry.get("reason")
        self.assertIsInstance(reason, str)
        self.assertTrue(reason.strip())

    def test_run_offline_gate_refuses_bad_repo(self):
        now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        for bad in (None, 42, "x", [], {}, True, b"x"):
            with self.subTest(bad=bad):
                code, summary = supply_chain_gate.run_offline_gate(bad, now)
                self.assertEqual(code, 2)
                self.assertTrue(summary.startswith("NO-DATA"))

    def test_run_offline_gate_refuses_bad_now(self):
        repo = pathlib.Path(tempfile.gettempdir())
        for bad in (None, 42, "2026-01-01", [], {}, True):
            with self.subTest(bad=bad):
                code, summary = supply_chain_gate.run_offline_gate(repo, bad)
                self.assertEqual(code, 2)
                self.assertTrue(summary.startswith("NO-DATA"))

    def test_run_offline_gate_refuses_naive_datetime(self):
        repo = pathlib.Path(tempfile.gettempdir())
        code, summary = supply_chain_gate.run_offline_gate(
            repo, datetime.datetime(2026, 1, 1)
        )
        self.assertEqual(code, 2)
        self.assertTrue(summary.startswith("NO-DATA"))

    def test_run_offline_gate_no_data_when_doc_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, summary = supply_chain_gate.run_offline_gate(
                pathlib.Path(tmp),
                datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            )
            self.assertEqual(code, 2)
            self.assertTrue(summary.startswith("NO-DATA"))

    def test_cli_exit_codes_via_main_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, printed = _run_script_in_process(tmp)
            self.assertIsNotNone(
                code,
                "supply_chain_gate.py must carry an `if __name__ == '__main__'` guard",
            )
            self.assertNotEqual(code, 0)
            self.assertEqual(code, 2)
            # 2026-09-28: the guard once sat above functions the gate calls, so the script died with NameError, printed
            # "NO-DATA: NameError", exited 2 and passed this test. Script mode must print what import mode computes.
            _, expected = supply_chain_gate.run_offline_gate(
                pathlib.Path(tmp), datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
            self.assertEqual(printed.strip(), expected)
        with tempfile.TemporaryDirectory() as tmp:
            # The same comparison past the audit doc, where the gate calls the functions defined last in the module.
            doc = pathlib.Path("docs") / "architecture" / "L5F-SUPPLY-CHAIN-AUDIT.md"
            (pathlib.Path(tmp) / doc).parent.mkdir(parents=True)
            (pathlib.Path(tmp) / doc).write_bytes((_HERE.parent / doc).read_bytes())
            code, printed = _run_script_in_process(tmp)
            _, expected = supply_chain_gate.run_offline_gate(
                pathlib.Path(tmp), datetime.datetime.now(datetime.timezone.utc))
            self.assertNotIn("NameError", printed)
            self.assertEqual(printed.strip(), expected)
            # 2026-09-28: verify_pins and verify_freshness were called with too few arguments; each TypeError became a
            # quiet NO-DATA finding, so neither verifier ever ran. A programming error is never a verdict.
            self.assertNotIn("TypeError", expected)
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            doc_path = repo / "docs" / "architecture" / "L5F-SUPPLY-CHAIN-AUDIT.md"
            doc_path.parent.mkdir(parents=True)
            doc_path.write_bytes(b"\xff\xfe\xfd not utf-8")
            code, _ = _run_script_in_process(tmp)
            self.assertIsNotNone(code)
            self.assertNotEqual(code, 0, "an uncaught exception must exit 2, not 0")
            self.assertEqual(code, 2)



class TestL3bDeferral(unittest.TestCase):
    """REQ-L3B after the owner deferral of 2026-09-29: a header that says L3b has not landed reads
    NO-DATA (exit 2) unless it also names the release the unit is deferred to, in which case the gate
    passes over the rows it has and names the deferral. Fixture repository, never this tree."""

    BEGIN = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 BEGIN'
    END = 'FROZEN MANIFEST l5f-supply-chain-audit-v1 END'

    def _repo(self, header_extra):
        import datetime
        import json
        import tempfile
        root = pathlib.Path(tempfile.mkdtemp(prefix='l3b-gate-'))
        self.addCleanup(shutil.rmtree, root, True)
        header = {'schema': 'l5f-supply-chain-audit-v1', 'manifests_searched': ['pyproject.toml'],
                  'l3b_landed': False}
        header.update(header_extra)
        block = json.dumps({'header': header, 'rows': [], 'runtime_findings': [],
                            'verdict': {'result': 'NO-DATA'}}, indent=2)
        doc = root / 'docs' / 'architecture' / 'L5F-SUPPLY-CHAIN-AUDIT.md'
        doc.parent.mkdir(parents=True)
        doc.write_text('# audit\n' + self.BEGIN + '\n' + block + '\n' + self.END + '\n', encoding='utf-8')
        return root, datetime.datetime.now(datetime.timezone.utc)

    def test_not_landed_and_no_deferral_is_no_data(self):
        root, now = self._repo({})
        code, summary = supply_chain_gate.run_offline_gate(root, now)
        self.assertEqual((code, summary), (2, 'NO-DATA: l3b not landed'))

    def test_not_landed_but_deferred_to_a_release_passes_naming_it(self):
        root, now = self._repo({'l3b_deferred_to': '1.1.2'})
        code, summary = supply_chain_gate.run_offline_gate(root, now)
        self.assertEqual(code, 0, summary)
        self.assertIn('deferred to 1.1.2', summary)

    def test_a_blank_deferral_is_no_deferral(self):
        root, now = self._repo({'l3b_deferred_to': '  '})
        code, summary = supply_chain_gate.run_offline_gate(root, now)
        self.assertEqual(code, 2, summary)


if __name__ == "__main__":
    unittest.main()

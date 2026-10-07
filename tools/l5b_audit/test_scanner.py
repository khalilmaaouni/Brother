import ast
import os
import tempfile
import unittest
from tools.l5b_audit import scanner

class TestScanner(unittest.TestCase):
    def test_T_UNKNOWN_SYMBOL_NOT_CALL(self):
        src = 'def f():\n    mystery(1)\n'
        calls = scanner.scan_source('x.py', src)
        self.assertEqual(calls, ())

    def test_T_SCAN_RESOLVES_IMPORT_ALIAS(self):
        src = 'import json as j\ndef f():\n    j.loads(\"{}\")\n'
        calls = scanner.scan_source('x.py', src)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kind, 'JSON')
        self.assertEqual(calls[0].symbol, 'json.loads')
        self.assertEqual(calls[0].enclosing_function, 'f')

    def test_file_io_and_json_direct(self):
        src = 'import os, json\ndef f():\n    open(\"a\")\n    os.path.exists(\"a\")\n    json.load(None)\n'
        calls = scanner.scan_source('x.py', src)
        kinds = [c.kind for c in calls]
        self.assertEqual(kinds, ['FILE_IO', 'FILE_IO', 'JSON'])

    def test_build_import_map_forms(self):
        tree = ast.parse('import x\nimport y as z\nfrom a import b\nfrom c import d as e\n')
        m = scanner.build_import_map(tree)
        self.assertEqual(m['x'], 'x')
        self.assertEqual(m['z'], 'y')
        self.assertEqual(m['b'], 'a.b')
        self.assertEqual(m['e'], 'c.d')

    def test_call_ids_are_keyed_on_function_and_ordinal_never_the_line(self):
        src = 'import os\nclass C:\n    def m(self):\n        open("a")\n        def inner():\n            open("b")\n        open("c")\nopen("d")\n'
        calls = scanner.scan_source('x.py', src)
        ids = [scanner.call_id(c.file, c.qualname, c.symbol, c.ordinal) for c in calls]
        self.assertEqual(ids, ['x.py:C.m:open#1', 'x.py:C.m.inner:open#1', 'x.py:C.m:open#2', 'x.py:<module>:open#1'])
        moved = scanner.scan_source('x.py', '\n\n\n' + src)
        self.assertEqual(ids, [scanner.call_id(c.file, c.qualname, c.symbol, c.ordinal) for c in moved])
        self.assertEqual(len(set(ids)), len(ids))
        for bad in [('', 'f', 's', 1), ('x', '', 's', 1), ('x', 'f', '', 1), ('x', 'f', 's', 0), ('x', 'f', 's', True)]:
            with self.assertRaises(ValueError):
                scanner.call_id(*bad)

    def test_syntax_error_empty(self):
        self.assertEqual(scanner.scan_source('bad.py', 'def f(:\n'), ())

    def test_classify_symbol(self):
        self.assertEqual(scanner.classify_symbol('open'), 'FILE_IO')
        self.assertEqual(scanner.classify_symbol('json.loads'), 'JSON')
        self.assertIsNone(scanner.classify_symbol('mystery'))

    def test_hostile_input_refused(self):
        for bad in (None, 1, True, float('nan'), ['x']):
            with self.assertRaises(ValueError):
                scanner.classify_symbol(bad)
        with self.assertRaises(ValueError):
            scanner.scan_source(None, 'x=1')
        with self.assertRaises(ValueError):
            scanner.scan_source('x.py', None)
        with self.assertRaises(ValueError):
            scanner.scan_tree(None)
        with self.assertRaises(ValueError):
            scanner.build_import_map(None)

    def test_scan_tree_on_temp(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'm.py')
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('import json\njson.loads(\"{}\")\n')
            calls = scanner.scan_tree(td)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].kind, 'JSON')
            self.assertEqual(calls[0].file, 'm.py')


class TestTestFilesAreNotProduct(unittest.TestCase):
    """Owner decision 2026-10-02 (option A): L5b measures product boundary calls only."""

    def test_is_test_file_names_both_conventions_and_nothing_else(self):
        for name in ('test_a.py', 'core/test_a.py', 'b_test.py', 'core/b_test.py'):
            self.assertTrue(scanner.is_test_file(name), name)
        for name in ('a.py', 'testing.py', 'contest.py', 'latest_test_data.json', 'test_a.txt', 'attest.py'):
            self.assertFalse(scanner.is_test_file(name), name)
        with self.assertRaises(ValueError):
            scanner.is_test_file(None)

    def test_a_tree_scan_leaves_out_test_files_and_keeps_product_files(self):
        body = 'def f(p):\n    return open(p).read()\n'
        with tempfile.TemporaryDirectory() as d:
            for rel in ('m.py', 'test_m.py', 'core/n_test.py', 'core/n.py'):
                path = os.path.join(d, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(body)
            files = sorted({c.file for c in scanner.scan_tree(d)})
        self.assertEqual(files, ['core/n.py', 'm.py'])

if __name__ == '__main__':
    unittest.main()

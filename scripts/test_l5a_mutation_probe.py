"""The L5a mutation probe answers RED only for an assertion failure of the named test that was green
unmutated; everything unknown is NO-DATA. Hermetic: a throwaway git repository per test."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import l5a_mutation_probe as probe  # noqa: E402

_MOD = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
_TEST = ("import unittest\nfrom pk import mod\n\n\nclass T(unittest.TestCase):\n"
         "    def test_add(self):\n        self.assertEqual(mod.add(1, 2), 3)\n\n"
         "    def test_mul(self):\n        self.assertEqual(mod.mul(2, 3), 6)\n")


class TestProbe(unittest.TestCase):

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="l5a-probe-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.repo)
        os.makedirs("pk")
        os.makedirs(probe.ARTIFACT_DIR)
        open("pk/__init__.py", "w").close()
        with open("pk/mod.py", "w") as fh:
            fh.write(_MOD)
        with open("pk/test_mod.py", "w") as fh:
            fh.write(_TEST)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for argv in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-q", "-m", "fixture"]):
            subprocess.run(argv, check=True, capture_output=True, env=env)

    def _artifact(self, name="T1", test="pk.test_mod.T.test_add", edits=None):
        edits = edits if edits is not None else [{"file": "pk/mod.py", "find": "    return a + b\n", "replace": "    return a - b\n"}]
        path = os.path.join(probe.ARTIFACT_DIR, name + ".json")
        with open(path, "w") as fh:
            json.dump({"id": "M-" + name, "test": test, "edits": edits}, fh)
        return path

    def _run(self, path):
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            code = probe.main([path])
        finally:
            sys.stdout = old
        return code, out.getvalue()

    def test_a_real_mutation_that_fails_the_test_is_red(self):
        code, out = self._run(self._artifact())
        self.assertEqual(code, 0, out)
        self.assertIn("FAIL: test_add", out)
        self.assertIn("MUTATION M-T1: RED", out)

    def test_a_no_op_edit_and_an_unknown_test_name_is_no_data(self):
        path = self._artifact(test="pk.test_mod.T.test_no_such_test",
                              edits=[{"file": "pk/mod.py", "find": "    return a + b\n", "replace": "    return a + b  # touched\n"}])
        code, out = self._run(path)
        self.assertEqual(code, 3, out)
        self.assertIn("not green unmutated", out)

    def test_an_unrelated_error_is_no_data_never_red(self):
        path = self._artifact(edits=[{"file": "pk/mod.py", "find": "    return a + b\n", "replace": "    raise KeyError('x')\n"}])
        code, out = self._run(path)
        self.assertEqual(code, 3, out)
        self.assertIn("ERROR line", out)

    def test_a_vanished_find_string_is_no_data(self):
        path = self._artifact(edits=[{"file": "pk/mod.py", "find": "    return a / b\n", "replace": "    return 0\n"}])
        code, out = self._run(path)
        self.assertEqual(code, 3, out)
        self.assertIn("find occurs 0 times", out)

    def test_a_mutation_the_test_survives_is_survived(self):
        path = self._artifact(edits=[{"file": "pk/mod.py", "find": "def add(a, b):\n", "replace": "def add(a, b):  # touched\n"}])
        code, out = self._run(path)
        self.assertEqual(code, 1, out)
        self.assertIn("SURVIVED", out)

    def test_an_artifact_outside_the_directory_is_no_data(self):
        path = os.path.join(self.repo, "T1.json")
        with open(path, "w") as fh:
            json.dump({"id": "M", "test": "pk.test_mod.T.test_add", "edits": [{"file": "pk/mod.py", "find": "x", "replace": "y"}]}, fh)
        self.assertEqual(self._run(path)[0], 3)
        self.assertEqual(self._run("T1.json")[0], 3)

    def test_a_failure_of_another_test_is_not_red(self):
        # the named test stays green while a sibling test goes red: that is a survived mutation, never a red
        path = self._artifact(edits=[{"file": "pk/mod.py", "find": "    return a * b\n", "replace": "    return 0\n"}])
        code, out = self._run(path)
        self.assertEqual(code, 1, out)
        self.assertNotIn("RED", out)
        self.assertNotIn("FAIL: test_mul", out)

    def test_a_class_or_module_id_is_no_data(self):
        # one method per artifact: a class or module id would let another test's failure stand in for the named one
        for test_id in ("pk.test_mod.T", "pk.test_mod"):
            code, out = self._run(self._artifact(test=test_id))
            self.assertEqual(code, 3, (test_id, out))
            self.assertIn("ONE test method", out)

    def test_the_live_tree_is_never_written(self):
        before = open("pk/mod.py").read()
        self._run(self._artifact())
        self.assertEqual(open("pk/mod.py").read(), before)


if __name__ == "__main__":
    sys.exit(unittest.main())

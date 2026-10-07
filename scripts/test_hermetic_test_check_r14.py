"""R1.4 hostile input bashed at scripts/hermetic_test_check.py.

Every entry point this slice owns (tests_for, changed_paths, scrub_git_env,
check) refuses a wrong type with ValueError or a returned refusal, never a
raw interpreter exception and never a silent accept. The red team shapes:
None, a str where a list is expected, an int in a list of strings, a
non-directory root, a bool or NaN timeout, a non-string env key.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hermetic_test_check as H  # noqa: E402


class _R14DeadRunner(object):
    """A runner whose call returns None, the shape P._run gives when it has
    no result at all. changed_paths must turn that into a problem, never an
    AttributeError into the caller."""

    def __call__(self, *args, **kwargs):
        return None


class R14HostileInputIsRefused(unittest.TestCase):
    """R1.4: every entry point this slice owns refuses a wrong type with
    ValueError or a returned refusal, never a raw interpreter exception and
    never a silent accept. An empty list is still the honest empty change
    answer; None and a bare string are not."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-r14-hostile-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "test_door.py"), "w") as fh:
            fh.write('"""doc"""\n')

    def test_tests_for_refuses_none(self):
        with self.assertRaises(ValueError):
            H.tests_for(None, self.root)

    def test_tests_for_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.tests_for("scripts/test_door.py", self.root)

    def test_tests_for_refuses_a_non_string_path(self):
        with self.assertRaises(ValueError):
            H.tests_for([123], self.root)

    def test_tests_for_refuses_a_non_directory_root(self):
        with self.assertRaises(ValueError):
            H.tests_for(["scripts/door.py"], os.path.join(self.root, "missing"))

    def test_tests_for_still_accepts_its_honest_input(self):
        self.assertEqual(H.tests_for(["scripts/door.py"], self.root),
                         ["scripts/test_door.py"])

    def test_changed_paths_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.changed_paths(self.root, "a..b", _R14DeadRunner())

    def test_changed_paths_refuses_a_non_string_revision(self):
        with self.assertRaises(ValueError):
            H.changed_paths(self.root, [123], _R14DeadRunner())

    def test_changed_paths_a_dead_runner_is_a_problem_not_a_crash(self):
        paths, problem = H.changed_paths(self.root, ["a..b"], _R14DeadRunner())
        self.assertIsNone(paths)
        self.assertTrue(problem)

    def test_scrub_git_env_refuses_a_non_mapping(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env(42)

    def test_scrub_git_env_refuses_a_string(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env("GIT_DIR=/x")

    def test_scrub_git_env_refuses_a_non_string_key(self):
        with self.assertRaises(ValueError):
            H.scrub_git_env({1: "x", "GIT_DIR": "/x"})

    def test_scrub_git_env_still_scrubs_a_dict(self):
        env = {"GIT_DIR": "/x", "HOME": "/h"}
        H.scrub_git_env(env)
        self.assertEqual(env, {"HOME": "/h"})

    def test_check_refuses_none_tests(self):
        with self.assertRaises(ValueError):
            H.check(self.root, None, build=lambda dest: None)

    def test_check_refuses_a_str_where_a_list_is_expected(self):
        with self.assertRaises(ValueError):
            H.check(self.root, "scripts/test_door.py", build=lambda dest: None)

    def test_check_refuses_a_non_string_test(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [123], build=lambda dest: None)

    def test_check_refuses_a_non_directory_root(self):
        with self.assertRaises(ValueError):
            H.check(os.path.join(self.root, "missing"), ["scripts/test_door.py"],
                    build=lambda dest: None)

    def test_check_refuses_a_bool_timeout(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [], timeout=True)

    def test_check_refuses_a_nan_timeout(self):
        with self.assertRaises(ValueError):
            H.check(self.root, [], timeout=float("nan"))

    def test_check_still_returns_the_empty_change_answer(self):
        res = H.check(self.root, [])
        self.assertEqual([r[0] for r in res], [H.OK])

    def test_main_help_returns_zero(self):
        from contextlib import redirect_stdout
        from io import StringIO
        out = StringIO()
        with redirect_stdout(out):
            code = H.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.getvalue())


if __name__ == "__main__":
    unittest.main()

"""ORCH-36 calibration: consumers are found, and an incomplete scan refuses.

THE BAD STATE A GREEN CHECK WOULD ALSO PASS, named so the cases can aim at
it: a scan that silently skips an unreadable file, or a root that does not
exist, and then prints CLEAR. That reads exactly like "nothing depends on
this" and is how a deletion takes its consumer with it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchestrator_dependents as D  # noqa: E402


class FindConsumers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.target = os.path.join(self.root, "lane")
        self.other = os.path.join(self.root, "other")
        os.makedirs(self.target)
        os.makedirs(self.other)
        with open(os.path.join(self.target, "helper.py"), "w") as fh:
            fh.write("def x():\n    return 1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.other, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def kinds(self, **kw):
        return sorted({k for k, _, _ in D.find_consumers(self.target, [self.other], **kw)})

    def test_no_consumer_is_an_empty_list(self):
        self.write("unrelated.py", "print('hello')\n")
        self.assertEqual(D.find_consumers(self.target, [self.other]), [])

    def test_a_file_naming_the_absolute_path_is_a_consumer(self):
        self.write("job.sh", "cd %s && ./run.sh\n" % self.target)
        self.assertEqual(self.kinds(), ["path"])

    def test_the_symlinked_spelling_of_the_path_also_counts(self):
        """On this machine /tmp is a symlink to /private/tmp, so a consumer
        usually names the spelling it was given, not the canonical one."""
        canonical = os.path.realpath(self.target)
        self.assertNotEqual(canonical, self.target,
                            "this case needs a target whose path is a symlink")
        self.write("job.sh", "cd %s && ./run.sh\n" % self.target)
        self.assertEqual(self.kinds(), ["path"])

    def test_a_python_import_of_a_module_the_target_provides(self):
        self.write("consumer.py", "import helper\nhelper.x()\n")
        self.assertEqual(self.kinds(), ["import"])

    def test_a_plist_naming_the_path_counts(self):
        self.write("com.example.job.plist", "<string>%s/run.sh</string>" % self.target)
        self.assertEqual(self.kinds(), ["path"])

    def test_a_symlink_pointing_into_the_target(self):
        os.symlink(os.path.join(self.target, "helper.py"),
                   os.path.join(self.other, "link.py"))
        self.assertIn("symlink", self.kinds())

    def test_a_registered_worktree_counts(self):
        found = D.find_consumers(self.target, [self.other],
                                 worktrees=[os.path.join(self.target, "wt")])
        self.assertEqual([k for k, _, _ in found], ["worktree"])

    def test_files_inside_the_target_are_not_its_own_consumers(self):
        with open(os.path.join(self.target, "self.py"), "w") as fh:
            fh.write("import helper\n%s\n" % self.target)
        self.assertEqual(D.find_consumers(self.target, [self.target]), [])

    def test_a_missing_root_is_incomplete_never_clear(self):
        with self.assertRaises(D.ScanIncomplete):
            D.find_consumers(self.target, [os.path.join(self.root, "gone")])

    def test_an_unreadable_file_is_incomplete_never_clear(self):
        if os.geteuid() == 0:
            self.skipTest("root can read anything, so this case cannot be driven")
        path = self.write("secret.py", "import helper\n")
        os.chmod(path, 0o000)
        try:
            with self.assertRaises(D.ScanIncomplete):
                D.find_consumers(self.target, [self.other])
        finally:
            os.chmod(path, 0o600)  # restored here, not in a cleanup that runs
            # after tearDown has already removed the temp tree

    def test_a_binary_file_is_skipped_without_failing_the_scan(self):
        with open(os.path.join(self.other, "blob.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02\xff")
        self.assertEqual(D.find_consumers(self.target, [self.other]), [])


class ExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "lane")
        self.other = os.path.join(self.tmp.name, "other")
        os.makedirs(self.target)
        os.makedirs(self.other)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clear_is_zero_refused_is_one_no_data_is_two(self):
        self.assertEqual(D.main(["--path", self.target, "--root", self.other]), 0)
        with open(os.path.join(self.other, "job.sh"), "w") as fh:
            fh.write(self.target)
        self.assertEqual(D.main(["--path", self.target, "--root", self.other]), 1)
        self.assertEqual(D.main(["--path", self.target,
                                 "--root", os.path.join(self.tmp.name, "gone")]), 2)

    def test_no_root_at_all_refuses_rather_than_clearing(self):
        self.assertEqual(D.main(["--path", self.target]), 2)


class GitWorktrees(unittest.TestCase):
    def test_a_git_failure_is_incomplete_not_an_empty_list(self):
        class Proc:
            returncode, stdout = 128, ""
        with self.assertRaises(D.ScanIncomplete):
            D.git_worktrees("/nope", run=lambda cmd: Proc())

    def test_porcelain_paths_are_read(self):
        class Proc:
            returncode = 0
            stdout = "worktree /a/b\nHEAD abc\n\nworktree /c/d\n"
        self.assertEqual(D.git_worktrees("/r", run=lambda cmd: Proc()), ["/a/b", "/c/d"])


if __name__ == "__main__":
    unittest.main()

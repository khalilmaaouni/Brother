#!/usr/bin/env python3
"""commit_scan.py --log <rev-range> and exit status 3, driven at the entry point (a subprocess in a throwaway repo).

Each case isolates one condition. A secret shaped fixture is assembled from parts and nothing here prints one.

    python3 -B scripts/test_commit_scan_range.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
SCAN = os.path.join(LOOP, "commit_scan.py")


def token():
    return "gh" + "p_" + "Zk3Qm9Xv2Lr8Tb5Wn7Yc4Hd6Jf1Ps0Ga8Ue"


class Range(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="commit-scan-range-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        with open(os.path.join(self.home, ".gitconfig"), "w") as fh:
            fh.write("[user]\n\temail = a@b.c\n\tname = t\n")
        self.env = {"HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.git("init", "-q", "-b", "main")
        self.make("base", {"base.txt": "base\n"})

    def git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(0, p.returncode, p.stderr)
        return p.stdout.strip()

    def make(self, message, files):
        for name, text in files.items():
            path = os.path.join(self.repo, name)
            if text is None:
                os.remove(path)
            else:
                with open(path, "w") as fh:
                    fh.write(text)
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def scan(self, *args, names=None):
        if names is not None:
            with open(os.path.join(self.home, ".brothersbe-private-names"), "w") as fh:
                fh.write(names)
        p = subprocess.run([sys.executable, "-I", SCAN] + list(args), cwd=self.repo, env=self.env,
                           capture_output=True, text=True)
        return p.returncode, p.stdout

    def test_a_secret_added_then_removed_is_named_by_its_commit(self):
        shas = [self.make("one", {"a.txt": "a\n"}), self.make("two", {"leak.txt": "t = %r\n" % token()}),
                self.make("three", {"leak.txt": None})]
        code, out = self.scan("--log", "HEAD~3..HEAD", names="zzhdone-term\n")
        self.assertEqual(1, code, out)
        self.assertIn("refused commit " + shas[1][:12], out)
        self.assertNotIn(shas[0][:12], out)
        self.assertNotIn(shas[2][:12], out)
        self.assertNotIn(token(), out)
        # the net staged diff of the same history is clean: this is the hole the mode closes
        self.git("checkout", "-q", "HEAD~3")
        self.git("merge", "--no-commit", "--no-ff", "main")
        self.assertEqual(0, self.scan(names="zzhdone-term\n")[0])

    def commit_bytes(self, message, name, data):
        with open(os.path.join(self.repo, name), "wb") as fh:
            fh.write(data)
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_a_secret_behind_a_nul_byte_is_still_seen(self):
        sha = self.commit_bytes("binary looking", "x.dat", b"\0" + token().encode())
        code, out = self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")
        self.assertEqual((1, True), (code, ("refused commit " + sha[:12]) in out), out)

    def test_a_diff_attribute_that_marks_a_file_binary_does_not_hide_it(self):
        self.make("rules", {".gitattributes": "*.dat -diff\n"})
        sha = self.commit_bytes("hidden by attribute", "x.dat", ("t = %s\n" % token()).encode())
        code, out = self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")
        self.assertEqual((1, True), (code, ("refused commit " + sha[:12]) in out), out)

    def test_a_textconv_driver_does_not_replace_the_content_that_is_scanned(self):
        self.git("config", "diff.hide.textconv", "echo hidden #")
        self.make("rules", {".gitattributes": "*.dat diff=hide\n"})
        sha = self.commit_bytes("hidden by textconv", "x.dat", ("t = %s\n" % token()).encode())
        code, out = self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")
        self.assertEqual((1, True), (code, ("refused commit " + sha[:12]) in out), out)

    def test_a_binary_files_line_that_survives_is_no_data_never_a_pass(self):
        sys.path.insert(0, LOOP)
        import commit_scan as C
        real = C._git_text
        fake = {"rev-list": ("abc123def456\n", None), "log": ("message\n", None),
                "show": ("diff --git a/x b/x\nBinary files a/x and b/x differ\n", None)}
        C._git_text = lambda args: fake[args[0]]
        try:
            import io
            import contextlib
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = C.log_main("HEAD~1..HEAD")
        finally:
            C._git_text = real
        self.assertEqual((2, True), (code, "abc123def456" in out.getvalue()), out.getvalue())

    def test_an_attribution_line_in_a_message_is_refused(self):
        sha = self.make("note\n\n" + "Co-Authored" + "-By: someone <x@y.z>", {"a.txt": "a\n"})
        code, out = self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")
        self.assertEqual((1, True), (code, ("refused commit " + sha[:12]) in out), out)

    def test_a_clean_range_with_a_list_is_zero(self):
        self.make("one", {"a.txt": "a\n"})
        self.assertEqual(0, self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")[0])

    def test_a_term_on_the_list_is_one_not_three(self):
        self.make("one", {"a.txt": "mentions zzhdone-term here\n"})
        self.assertEqual(1, self.scan("--log", "HEAD~1..HEAD", names="zzhdone-term\n")[0])

    def test_no_list_and_a_clean_range_is_three_and_says_so(self):
        self.make("one", {"a.txt": "a\n"})
        code, out = self.scan("--log", "HEAD~1..HEAD")
        self.assertEqual((3, True), (code, "NO-DATA" in out), out)

    def test_no_list_does_not_hide_a_secret_in_the_range(self):
        self.make("one", {"leak.txt": "t = %r\n" % token()})
        self.assertEqual(1, self.scan("--log", "HEAD~1..HEAD")[0])

    def test_an_empty_range_a_bad_range_and_a_dash_are_no_data(self):
        for rng in ("HEAD..HEAD", "nope..HEAD", "--all", "-x", ""):
            with self.subTest(rng=rng):
                self.assertEqual(2, self.scan("--log", rng, names="zzhdone-term\n")[0])
        self.assertEqual(2, self.scan("--log", names="zzhdone-term\n")[0])

    def test_the_staged_mode_reads_three_without_a_list(self):
        with open(os.path.join(self.repo, "b.txt"), "w") as fh:
            fh.write("b\n")
        self.git("add", "b.txt")
        code, out = self.scan()
        self.assertEqual((3, True), (code, "NO-DATA" in out), out)


if __name__ == "__main__":
    unittest.main()

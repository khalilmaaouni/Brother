#!/usr/bin/env python3
"""commit_scan's GATE, driven at its entry point: every name a staged change carries is scanned.

WHY (finding 5 of the landing audit, 2026-09-27). Paths were read only from the +++ and --- headers,
and git prints those only for a file entry that has a text hunk. A pure rename, a copy, an empty new
file and a binary file carry none, so a private term in the new name committed clean at exit 0.
Enumerating the same edge found more doors into the same room: a colour setting in the repository's
own config turned every added line into escape codes the extractor could not see (an added
credential scanned secrets=0), an external diff driver in that config replaced the patch outright,
git's default path quoting turned a non ASCII name into octal escapes no term can match, and a git
that failed after printing a fragment had that fragment scanned as though it were the whole change.

Each case stages ONE real git shape in a throwaway repository, under a scratch HOME whose private
names file holds synthetic terms only. The owner's real list is never read, and neither term below is
spelled whole in this file, so the file itself cannot trip the list it tests.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, "loop", "commit_scan.py")
TERM = "zqcanary" + "termword"                    # synthetic, never an entry of the real list
WIDE = "zq" + chr(0xE9) + "canary" + "wide"       # synthetic, non ASCII
SECRET = "\x61pi_key=" + "abcdefgh12345678"       # built from pieces so this file stays clean


def counts(out):
    """{'secrets': n, ...} from the gate's one line of counts. The gate never prints a hit itself."""
    line = [l for l in out.splitlines() if "private terms=" in l][-1]
    pairs = line.replace("long dashes", "long_dashes").replace("private terms", "private_terms").split()
    return {k: int(v) for k, v in (p.split("=") for p in pairs)}


class CommitScanNames(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="commit-scan-names-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        with open(os.path.join(self.home, ".brothersbe-private-names"), "w", encoding="utf-8") as fh:
            fh.write(TERM + "\n" + WIDE + "\n")
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        self.env = dict(os.environ, HOME=self.home, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.t",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t.t")
        self.git("init", "-q", ".")
        self.write("ordinary.txt", "l1\nl2\nl3\nl4\nl5\nl6\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        r = subprocess.run(["git"] + list(args), cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def write(self, name, data):
        with open(os.path.join(self.repo, name), "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)

    def scan(self, env=None):
        r = subprocess.run([sys.executable, "-B", SCAN], cwd=self.repo, env=env or self.env,
                           capture_output=True, text=True, timeout=120)
        self.assertNotIn(TERM, r.stdout + r.stderr)     # counts only, never the matched text
        return r.returncode, r.stdout

    def assertRefusedForName(self):
        code, out = self.scan()
        self.assertEqual(code, 1, out)
        self.assertGreaterEqual(counts(out)["private_terms"], 1, out)

    # the control: the fixture itself is clean, so every refusal below is the name and nothing else
    def test_an_ordinary_rename_is_clean(self):
        self.git("mv", "ordinary.txt", "plain.txt")
        code, out = self.scan()
        self.assertEqual((code, counts(out)),
                         (0, {"secrets": 0, "long_dashes": 0, "attribution": 0, "private_terms": 0}), out)

    def test_a_pure_rename_into_a_private_name_is_refused(self):
        self.git("mv", "ordinary.txt", TERM + ".txt")
        self.assertRefusedForName()

    def test_a_pure_rename_out_of_a_private_name_is_refused(self):
        self.write(TERM + ".txt", "kept\n")
        self.git("add", ".")
        self.git("commit", "-qm", "seed")
        self.git("mv", TERM + ".txt", "plain.txt")
        self.assertRefusedForName()

    def test_a_copy_into_a_private_name_is_refused(self):
        self.git("config", "diff.renames", "copies")
        shutil.copy(os.path.join(self.repo, "ordinary.txt"), os.path.join(self.repo, TERM + ".txt"))
        self.write("ordinary.txt", "l1\nl2\nl3\nl4\nl5\nl6\nl7\n")   # git reports a copy only from a changed source
        self.git("add", ".")
        self.assertRefusedForName()

    def test_an_empty_new_file_with_a_private_name_is_refused(self):
        self.write(TERM + ".md", "")
        self.git("add", ".")
        self.assertRefusedForName()

    def test_a_binary_new_file_with_a_private_name_is_refused(self):
        self.write(TERM + ".bin", b"\x00\x01\x02")
        self.git("add", ".")
        self.assertRefusedForName()

    def test_a_non_ascii_private_name_is_refused(self):
        self.write(WIDE + ".txt", "ordinary\n")
        self.git("add", ".")
        self.assertRefusedForName()

    def test_a_colour_setting_cannot_hide_an_added_secret(self):
        self.git("config", "color.ui", "always")
        self.write("s.txt", SECRET + "\n")
        self.git("add", ".")
        code, out = self.scan()
        self.assertEqual(code, 1, out)
        self.assertGreaterEqual(counts(out)["secrets"], 1, out)

    def test_a_colour_setting_cannot_turn_a_removed_secret_into_a_refusal(self):
        # the other half of the colour pin: escaped lines are unknown, and an unknown line is scanned,
        # so without the pin a REMOVED line would be read as added and cleaning a secret up refused
        self.write("s.txt", SECRET + "\nkept\n")
        self.git("add", ".")
        self.git("commit", "-qm", "seed")
        self.git("config", "color.ui", "always")
        self.write("s.txt", "kept\n")
        self.git("add", ".")
        code, out = self.scan()
        self.assertEqual((code, counts(out)["secrets"]), (0, 0), out)

    def test_an_external_diff_driver_cannot_replace_the_patch(self):
        driver = os.path.join(self.root, "driver.sh")
        with open(driver, "w") as fh:
            fh.write("#!/bin/sh\necho clean\n")
        os.chmod(driver, 0o755)
        self.git("config", "diff.external", driver)
        self.write("s.txt", SECRET + "\n")
        self.git("add", ".")
        code, out = self.scan()
        self.assertEqual(code, 1, out)
        self.assertGreaterEqual(counts(out)["secrets"], 1, out)

    def test_a_missing_git_is_no_data(self):
        code, out = self.scan(dict(self.env, PATH=os.path.join(self.root, "empty-path")))
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_an_undecodable_staged_change_is_no_data_never_clean(self):
        self.write("latin.txt", b"caf\xe9\n")
        self.git("add", ".")
        code, out = self.scan()
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_a_failed_read_of_the_staged_diff_is_no_data_not_a_clean_scan(self):
        # a git that prints a clean partial diff and then fails: scanning the fragment reads clean
        fake = os.path.join(self.root, "fakebin")
        os.makedirs(fake)
        with open(os.path.join(fake, "git"), "w") as fh:
            fh.write("#!/bin/sh\nprintf 'diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n+ok\\n'\nexit 128\n")
        os.chmod(os.path.join(fake, "git"), 0o755)
        code, out = self.scan(dict(self.env, PATH=fake + os.pathsep + self.env["PATH"]))
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()

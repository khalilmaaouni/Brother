"""Proof for scripts/next_cut.py (row S29, docs/plan/READINESS-ROADMAP-
2026-08-29.json): a policy naming a cut weekday prints the next cut date,
the version it would be and the closeout command; a policy naming none
reads NO-DATA and exits 3, never a pass.

Exit contract, same shape as this estate's other suites: 0 all assertions
pass, 1 an assertion failed.

Python 3, stdlib only. No network. No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NEXT_CUT = os.path.join(HERE, "next_cut.py")

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

FRIDAY_POLICY = """# Release policy

## Cadence

Cuts run on a fixed weekday: Friday, closeout matrix mandatory.
"""

NO_WEEKDAY_POLICY = """# Release policy

Cuts land whenever a lane finishes; there is no fixed schedule yet.
"""


class NextCutTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="next-cut-test-")
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.manifest = os.path.join(self.scratch, "plugin.json")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": "1.0.3"}, fh)
        # F9 (2026-09-30): the version is read against the repository's tags, so every test owns a
        # throwaway repository whose only tag is the manifest's cut, v1.0.3.
        # M2 (attack 2026-09-30): tags live on the remote the release publishes to, never in a local
        # cache, so the fixture is a bare "remote" holding v1.0.3 and a working clone that holds NO tag.
        self.remote = os.path.join(self.scratch, "remote.git")
        self.repo = os.path.join(self.scratch, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True, capture_output=True)
        for cmd in (["git", "init", "-q"], ["git", "-c", "user.name=t", "-c", "user.email=t@t",
                                            "commit", "-q", "--allow-empty", "-m", "seed"],
                    # a lightweight tag, whatever this machine's tag.gpgSign says
                    ["git", "-c", "tag.gpgSign=false", "-c", "tag.forceSignAnnotated=false",
                     "tag", "v1.0.3"],
                    ["git", "push", "-q", self.remote, "v1.0.3"],
                    ["git", "tag", "-d", "v1.0.3"]):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)

    def _set_manifest_version(self, version):
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": version}, fh)

    def _write_policy(self, text):
        path = os.path.join(self.scratch, "RELEASE-POLICY.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _run(self, policy_path, today):
        return subprocess.run(
            [sys.executable, NEXT_CUT,
             "--policy", policy_path,
             "--manifest", self.manifest,
             "--tag-source", self.remote,
             "--today", today],
            capture_output=True, text=True)

    def test_an_empty_local_tag_cache_never_reads_as_untagged(self):
        """M2: the clone holds no tag at all; the remote holds v1.0.3, so 1.0.3 is released and the next
        cut is 1.0.4. Reading the local cache would have called 1.0.3 the cut in flight."""
        local = subprocess.run(["git", "tag", "-l"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(local.stdout.strip(), "")
        proc = self._run(self._write_policy(FRIDAY_POLICY), "2026-09-07")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut version: 1.0.4", proc.stdout)
        self.assertNotIn("cut in flight", proc.stdout)

    def test_an_unreadable_tag_source_is_no_data_and_exits_3(self):
        policy = self._write_policy(FRIDAY_POLICY)
        proc = subprocess.run(
            [sys.executable, NEXT_CUT, "--policy", policy, "--manifest", self.manifest,
             "--tag-source", os.path.join(self.scratch, "no-such-remote.git"),
             "--today", "2026-09-07"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA", proc.stdout)
        self.assertNotIn("next cut version", proc.stdout)

    def test_a_git_that_raises_or_times_out_is_no_data(self):
        """2f: the OSError and timeout paths of the tag read, exercised through an injected runner."""
        sys.path.insert(0, HERE)
        import next_cut as N

        def raises(*a, **k):
            raise OSError("no git")

        def times_out(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git", timeout=1)

        self.assertIsNone(N.tag_exists(self.remote, "1.0.3", run=raises))
        self.assertIsNone(N.tag_exists(self.remote, "1.0.3", run=times_out))
        self.assertIs(N.tag_exists(self.remote, "1.0.3"), True)
        self.assertIs(N.tag_exists(self.remote, "1.0.4"), False)

    def test_a_manifest_ahead_of_every_tag_names_the_cut_in_flight(self):
        """F9 (architecture review 2026-09-30): the manifests were bumped to 1.1.0 before v1.1.0
        was tagged, and this tool printed 1.1.1, which cut.py's read_next_version then took for a
        bare cut. The version whose tag does not exist yet IS the next cut."""
        self._set_manifest_version("1.1.0")
        policy = self._write_policy(FRIDAY_POLICY)
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut version: 1.1.0", proc.stdout)
        self.assertNotIn("1.1.1", proc.stdout)
        self.assertIn("cut in flight: v1.1.0 is not tagged yet", proc.stdout)
        self.assertIn("--version 1.1.0", proc.stdout)

    def test_a_tagged_manifest_version_bumps_the_patch(self):
        """The other side of F9: once v1.0.3 exists, the next cut is 1.0.4, as before."""
        policy = self._write_policy(FRIDAY_POLICY)
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut version: 1.0.4", proc.stdout)
        self.assertNotIn("cut in flight", proc.stdout)

    def test_a_named_friday_prints_the_next_friday_version_and_command(self):
        policy = self._write_policy(FRIDAY_POLICY)
        # 2026-09-07 is a Monday; the next Friday is 2026-09-11.
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut weekday: Friday", proc.stdout)
        self.assertIn("next cut date: 2026-09-11", proc.stdout)
        self.assertIn("next cut version: 1.0.4", proc.stdout)
        self.assertIn(
            "closeout command: python3 scripts/release_closeout.py all "
            "--version 1.0.4", proc.stdout)

    def test_today_being_the_named_weekday_returns_today(self):
        policy = self._write_policy(FRIDAY_POLICY)
        # 2026-09-11 is itself a Friday.
        proc = self._run(policy, "2026-09-11")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("next cut date: 2026-09-11", proc.stdout)

    def test_a_policy_naming_no_weekday_is_no_data_and_exits_3(self):
        policy = self._write_policy(NO_WEEKDAY_POLICY)
        proc = self._run(policy, "2026-09-07")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn(
            "NO-DATA: RELEASE-POLICY.md names no cut weekday (S29, founder)",
            proc.stdout)

    def test_a_missing_policy_file_is_no_data_and_exits_3(self):
        proc = self._run(os.path.join(self.scratch, "absent.md"), "2026-09-07")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("NO-DATA:"), proc.stdout)


class Night0912NextCut(unittest.TestCase):
    def test_non_integer_patch_returns_nodata(self):
        with tempfile.TemporaryDirectory() as d:
            policy = os.path.join(d, "policy.md")
            manifest = os.path.join(d, "plugin.json")
            with open(policy, "w", encoding="utf-8") as fh:
                fh.write(FRIDAY_POLICY)
            with open(manifest, "w", encoding="utf-8") as fh:
                fh.write('{"version": "1.0.x"}')

            proc = subprocess.run(
                [sys.executable, NEXT_CUT, "--policy", policy,
                 "--manifest", manifest, "--today", "2026-01-01"],
                capture_output=True, text=True)

        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)


class TheTagSourceDefault(unittest.TestCase):
    """M2 (second attack 2026-09-30): nothing pinned the default tag source, so changing it to the local
    checkout (which carries no release tags) survived every test."""

    def test_main_asks_the_release_remote_by_default(self):
        sys.path.insert(0, HERE)
        import next_cut as N
        import export_public
        seen = []
        saved = N.tag_exists
        try:
            N.tag_exists = lambda source, version, run=None: seen.append(source) or False
            d = tempfile.mkdtemp(prefix="next-cut-default-")
            policy, manifest = os.path.join(d, "RELEASE-POLICY.md"), os.path.join(d, "plugin.json")
            with open(policy, "w", encoding="utf-8") as fh:
                fh.write(FRIDAY_POLICY)
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump({"name": "brother", "version": "1.1.0"}, fh)
            with open(os.devnull, "w") as sink:
                saved_out, sys.stdout = sys.stdout, sink
                try:
                    # its own policy and manifest (the export tree ships neither), never the tag source
                    N.main(["--policy", policy, "--manifest", manifest, "--today", "2026-09-30"])
                finally:
                    sys.stdout = saved_out
        finally:
            N.tag_exists = saved
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(seen, [export_public.DEFAULT_REMOTE])


if __name__ == "__main__":
    unittest.main()

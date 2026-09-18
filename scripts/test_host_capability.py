"""What scripts/host_capability.py must keep true: it is imported directly
by scripts/brother_run.py, the real run entrypoint, and builds the Host
Capability Receipt (fourteen facts) that a run relies on to know what its
host can actually be trusted to enforce. The property under test is NOT
"the receipt has the right keys" (a shape test that a defaulting module
would also pass). It is that a fact this module cannot establish -- an
unrecognised host, a version nobody printed, a certification file with no
git history, an unreadable repository -- comes back as NO-DATA naming the
gap, never guessed and never defaulted to a comfortable "yes". A module
that quietly assumed "yes" for an unknown host would make a real run trust
enforcement nobody ever measured.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import host_capability as HC  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


class UnrecognisedHostDegradesEveryFactToNoData(unittest.TestCase):
    """The core refusal: a host this receipt has no CAPABILITY_TABLE row for
    must not borrow another host's answers, or a "usually true" default. It
    must say, per field, that nothing is known."""

    def test_unrecognised_host_string_never_gets_a_comfortable_default(self):
        with mock.patch.object(HC.brother_paths, "client",
                                return_value="windsurf-9000"):
            row = HC.host_capability_receipt(env={})
        self.assertEqual(row["host"], "windsurf-9000")
        for field in HC._TABLE_FIELDS:
            self.assertTrue(
                row[field].startswith(HC.NODATA),
                "field %r defaulted to %r instead of NO-DATA for an "
                "unrecognised host" % (field, row[field]))
            self.assertIn("windsurf-9000", row[field])

    def test_receipt_always_carries_the_full_field_set_regardless_of_host(self):
        with mock.patch.object(HC.brother_paths, "client",
                                return_value="windsurf-9000"):
            row = HC.host_capability_receipt(env={})
        self.assertEqual(set(row), set(HC.RECEIPT_FIELDS))

    def test_empty_env_with_no_markers_at_all_is_a_nodata_host(self):
        # "empty input": client() itself falls through to "" when nothing in
        # the given env identifies a host and no plugin manifest resolves
        # from an isolated env; forced here rather than trusted to this
        # machine's real filesystem state.
        with mock.patch.object(HC.brother_paths, "client", return_value=""):
            row = HC.host_capability_receipt(env={})
        self.assertEqual(row["host"], HC.NODATA)
        for field in HC._TABLE_FIELDS:
            self.assertTrue(row[field].startswith(HC.NODATA))


class KnownHostStillReadsFromTheMeasuredTable(unittest.TestCase):
    """Contrast case: a host host_capability DOES recognise must still come
    from CAPABILITY_TABLE, proving the refusal above is host-based and not
    just "always NO-DATA"."""

    def test_claude_host_is_forced_by_broker_client_env_and_reads_real_facts(self):
        row = HC.host_capability_receipt(env={"BROTHER_CLIENT": "claude"})
        self.assertEqual(row["host"], "claude")
        self.assertIn("PreToolUse", row["pre_tool_hook"])
        self.assertFalse(row["pre_tool_hook"].startswith(HC.NODATA))


class HostVersionNeverInventsAVersion(unittest.TestCase):
    """_host_version(): only a version the host itself printed, or NO-DATA
    naming exactly why not."""

    def test_env_var_wins_when_the_host_actually_exported_one(self):
        v = HC._host_version("claude", {"CLAUDE_CODE_DESKTOP_APP_VERSION":
                                         "1.2.3"})
        self.assertEqual(v, "1.2.3")

    def test_unknown_host_has_no_version_source_at_all(self):
        v = HC._host_version("some-host-nobody-wrote-a-rule-for", {})
        self.assertTrue(v.startswith(HC.NODATA))
        self.assertIn("no version source known", v)

    def test_missing_binary_is_nodata_not_a_crash(self):
        with mock.patch.object(HC.subprocess, "run",
                                side_effect=FileNotFoundError("no such file")):
            v = HC._host_version("codex", {})
        self.assertTrue(v.startswith(HC.NODATA))
        self.assertIn("could not be run", v)

    def test_a_command_that_prints_nothing_is_nodata_not_an_empty_version(self):
        completed = subprocess.CompletedProcess(
            args=["codex", "--version"], returncode=0, stdout="", stderr="")
        with mock.patch.object(HC.subprocess, "run", return_value=completed):
            v = HC._host_version("codex", {})
        self.assertTrue(v.startswith(HC.NODATA))
        self.assertIn("printed nothing", v)


class CertificationFreshnessNeverInventsADate(unittest.TestCase):
    """_certification_freshness(): only a date git itself reports, or
    NO-DATA naming why not (unknown host, unreadable repo, no history)."""

    def test_unknown_host_has_no_certification_file_at_all(self):
        f = HC._certification_freshness("some-host-with-no-row")
        self.assertTrue(f.startswith(HC.NODATA))
        self.assertIn("no certification evidence file known", f)

    def test_an_unreadable_or_nonexistent_repo_path_is_nodata_not_a_crash(self):
        # "corrupt input": a repo path that does not exist at all, so the
        # subprocess call itself cannot even start.
        f = HC._certification_freshness(
            "claude", repo="/definitely/does/not/exist/anywhere/xyz")
        self.assertTrue(f.startswith(HC.NODATA))

    def test_git_log_failing_cleanly_is_nodata_not_a_guessed_date(self):
        completed = subprocess.CompletedProcess(
            args=["git", "log"], returncode=128, stdout="", stderr="fatal")
        with mock.patch.object(HC.subprocess, "run", return_value=completed):
            f = HC._certification_freshness("claude", repo=HERE)
        self.assertTrue(f.startswith(HC.NODATA))
        self.assertIn("no commit history", f)


class NoStateLeaksBetweenCalls(unittest.TestCase):
    """host_capability computes a fresh snapshot every call; it holds no
    store, so two calls in a row with different hosts must not contaminate
    each other. This stands in for "many" in the standing edge list, since
    there is no queue or store here for "many items" to mean anything else."""

    def test_two_sequential_calls_with_different_hosts_do_not_leak(self):
        row1 = HC.host_capability_receipt(env={"BROTHER_CLIENT": "claude"})
        row2 = HC.host_capability_receipt(env={"BROTHER_CLIENT": "codex"})
        self.assertEqual(row1["host"], "claude")
        self.assertEqual(row2["host"], "codex")
        self.assertNotEqual(row1["pre_tool_hook"], row2["pre_tool_hook"])


# Standing edge list, the rest: "a concurrent second actor", "expired or
# stale", "already done", "partially done" and "the actor is the same as
# last time" are declared OUT OF SCOPE for this module. host_capability
# holds no store, no lock and no persisted state of its own: every call
# reads live evidence (an env dict, a subprocess, a git log) and returns a
# fresh dict, so there is nothing to be "done", "partial" or "stale" other
# than the certification_freshness date itself, which is exactly the fact
# the receipt reports, not a corruption for this suite to catch (covered
# above by the missing-history and unreadable-repo cases). Two processes
# calling this module at once share no mutable resource, so there is no
# concurrent-actor property to break.


if __name__ == "__main__":
    unittest.main()

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


class TableSaysDocumentedNeverMeasured(unittest.TestCase):
    """HC1.a: the real table separates the documented mechanism from current
    certification. Nothing in it claims a deny was seen on this host
    version, and the fence limits are stated per mode and path, never as a
    blanket allow."""

    T = HC.CAPABILITY_TABLE

    def _texts(self):
        for host, facts in self.T.items():
            for field, text in facts.items():
                yield host, field, text

    def test_no_row_says_yes_measured(self):
        for host, field, text in self._texts():
            self.assertNotIn("yes, measured", text, (host, field))

    def test_no_row_says_none_named_beyond(self):
        for host, field, text in self._texts():
            self.assertNotIn("none named beyond", text, (host, field))

    def test_claude_deny_reads_no_data(self):
        self.assertTrue(self.T["claude"]["enforceable_deny"].startswith(
            HC.NODATA + ": no deny is shown to block a write"))

    def test_codex_deny_reads_no_data(self):
        self.assertTrue(self.T["codex"]["enforceable_deny"].startswith(
            HC.NODATA + ": no deny is shown to block a write"))

    def test_hook_rows_are_labelled_documented(self):
        for host in ("claude", "codex"):
            text = self.T[host]["pre_tool_hook"]
            self.assertTrue(text.startswith("yes, documented: PreToolUse"),
                            (host, text))
            self.assertIn("not a signed-in end-to-end test", text)

    def test_certification_evidence_is_labelled_documentation_only(self):
        for host in ("claude", "codex"):
            self.assertTrue(self.T[host]["certification_evidence"].startswith(
                "documentation only, not a live test: "
                "products/brothermode/docs/RUNTIMES.md"), host)

    def test_claude_limitations_name_the_established_limits(self):
        text = self.T["claude"]["known_limitations"]
        for part in ("no signed-in end-to-end proof exists",
                     "EXPECTED_GUARDED_WRITE: unguarded",
                     "not an operating system sandbox",
                     "receipt_door.SCOPING_SENTENCE"):
            self.assertIn(part, text)

    def test_codex_limitations_add_the_two_new_limits(self):
        text = self.T["codex"]["known_limitations"]
        self.assertTrue(text.startswith("${CLAUDE_PLUGIN_ROOT} expands empty"))
        self.assertIn("no signed-in end-to-end proof exists", text)
        self.assertIn("EXPECTED_GUARDED_WRITE: unguarded", text)

    def test_fence_limits_distinguish_modes_and_paths(self):
        # HC1-WORDING: bm_fence_hook.py denies a foreign-owned path and an
        # unreadable apply_patch envelope in every mode, and fails closed on
        # its own errors under BM_FENCE_MODE=enforced; "fails open" is true
        # only of the default advisory mode, so no row may say it bare.
        for host in ("claude", "codex"):
            text = self.T[host]["known_limitations"]
            for part in ("another live record owns",
                         "apply_patch envelope whose file directives "
                         "cannot be read",
                         "default advisory mode",
                         "BM_FENCE_MODE=enforced",
                         "BM_FENCE_STRICT",
                         "outside the project root"):
                self.assertIn(part, text, (host, part))
            self.assertNotIn("fail open on their own errors", text, host)

    def test_no_row_claims_live_enforcement(self):
        for host, field, text in self._texts():
            low = text.lower()
            for phrase in ("measured live", "enforced live", "live deny",
                           "certified"):
                self.assertNotIn(phrase, low, (host, field, phrase))

    def test_freshness_is_labelled_documentation(self):
        env = {"BROTHER_CLIENT": "claude"}
        with mock.patch.object(HC, "_certification_freshness",
                               return_value="2026-09-14T10:00:00+07:00"):
            row = HC.host_capability_receipt(env=env)
        self.assertEqual(row["certification_freshness"],
                         "documentation changed 2026-09-14T10:00:00+07:00; "
                         "not a live test")
        nd = HC.NODATA + ": x has no commit history in this checkout"
        with mock.patch.object(HC, "_certification_freshness",
                               return_value=nd):
            row = HC.host_capability_receipt(env=env)
        self.assertEqual(row["certification_freshness"], nd)

    def test_claude_parity_reads_the_new_text(self):
        try:
            import claude_parity as CP
        except ImportError:
            self.skipTest("NO-DATA: claude_parity is absent from this tree")
        table = self.T["claude"]
        self.assertEqual(CP._verdict_for_table_field(
            table["pre_tool_hook"])[0], CP.PASS)
        self.assertEqual(CP._verdict_for_table_field(
            table["enforceable_deny"])[0], CP.NODATA)


def _row(host="codex", version="codex-cli 0.150.0", run_id="R1",
         stamp="2026-10-01T09:00:00.000000Z", **extra):
    row = {"schema": "hp1.v1", "host": host, "run_id": run_id,
           "probe": "door", "verdict": "allow", "recorded_at": stamp,
           "host_version": version}
    row.update(extra)
    return row


class LiveEvidenceIsLookedForSealedAndNeverUpgraded(unittest.TestCase):
    """HC1.b: _live_evidence finds a sealed evidence row for this host and
    this exact version, or says NO-DATA naming why. Even a match is a
    pointer to the verifier, never a verdict, and never moves the deny."""

    V = "codex-cli 0.150.0"

    def setUp(self):
        import tempfile
        self.repo = tempfile.mkdtemp(prefix="hc1b-")
        self.dir = os.path.join(self.repo, "docs", "plan", "evidence")
        self.path = os.path.join(self.dir, "HP1-host-live.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write(self, lines, seal=True):
        import hashlib
        import json
        os.makedirs(self.dir, exist_ok=True)
        data = "".join((l if isinstance(l, str) else json.dumps(l)) + "\n"
                       for l in lines).encode("utf-8")
        with open(self.path, "wb") as fh:
            fh.write(data)
        if seal:
            with open(self.path + ".sha256", "wb") as fh:
                fh.write((hashlib.sha256(data).hexdigest() + "\n").encode())

    def ev(self, host="codex", version=None):
        return HC._live_evidence(host, self.V if version is None else version,
                                 self.repo)

    def test_absent_file_is_no_data_saying_so(self):
        out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        self.assertIn("is absent", out)

    def test_unsealed_file_is_not_trusted(self):
        self._write([_row()], seal=False)
        out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        self.assertIn("seal cannot be read", out)

    def test_file_edited_after_sealing_is_not_trusted(self):
        self._write([_row()])
        with open(self.path, "ab") as fh:
            fh.write(b" ")
        out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        self.assertIn("does not match its seal", out)

    def test_one_corrupt_line_voids_the_file_without_raising(self):
        for bad in ("not json at all", "[1, 2]", "[" * 100000):
            self._write([_row(), bad])
            out = self.ev()
            self.assertTrue(out.startswith(HC.NODATA + ": CORRUPT: line 2"),
                            (bad[:10], out))

    def test_too_large_file_is_not_read(self):
        self._write([_row()])
        with mock.patch.object(HC, "EVIDENCE_CAP", 10):
            out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        self.assertIn("larger than 10 bytes", out)

    def test_other_host_has_no_row(self):
        self._write([_row(host="antigravity")])
        out = self.ev()
        self.assertEqual(out, HC.NODATA + ": " + HC.EVIDENCE_REL
                         + " holds no row for codex")

    def test_other_schema_is_ignored(self):
        self._write([_row(schema="hp0")])
        self.assertIn("holds no row for codex", self.ev())

    def test_other_version_is_no_data_naming_both(self):
        self._write([_row(version="codex-cli 0.149.0")])
        out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        self.assertIn("'codex-cli 0.149.0'", out)
        self.assertIn("this run's 'codex-cli 0.150.0'", out)

    def test_unknown_version_sentinels_never_match(self):
        # HC1-READER: a row recorded as no_data is listed, never matched,
        # and a receipt whose own version is a sentinel matches nothing.
        self._write([_row(version="no_data"), _row(version="  ")])
        out = self.ev()
        self.assertTrue(out.startswith(HC.NODATA), out)
        for receipt_version in ("no_data", "NO-DATA: no version source",
                                "  ", "", None, "unknown"):
            out = HC._live_evidence("codex", receipt_version, self.repo)
            self.assertEqual(out, HC.NODATA + ": this run's host or its "
                             "version is unknown, so no live evidence row "
                             "can match it", receipt_version)

    def test_blank_or_unparseable_identifiers_never_match(self):
        for bad in ({"run_id": ""}, {"run_id": "  "}, {"run_id": 7},
                    {"recorded_at": ""}, {"recorded_at": "yesterday"},
                    {"recorded_at": "2026-10-01 09:00:00"},
                    {"recorded_at": "2026-13-45T99:00:00Z"}):
            self._write([_row(**bad)])
            out = self.ev()
            self.assertTrue(out.startswith(HC.NODATA), (bad, out))

    def test_matching_sealed_row_is_a_pointer_never_a_verdict(self):
        self._write([_row(run_id="R1"), _row(run_id="R1", probe="verb")])
        out = self.ev()
        self.assertTrue(out.startswith(
            "recorded: 2 sealed row(s) for codex codex-cli 0.150.0, newest "
            "run R1 at 2026-10-01T09:00:00.000000Z; this receipt does not "
            "judge them"), out)
        self.assertIn("python3 scripts/host_live_verify.py " + HC.EVIDENCE_REL,
                      out)
        self.assertNotIn("certif", out.lower())
        self.assertFalse(out.lower().startswith("yes"))

    def test_newest_run_is_chronological_not_textual(self):
        # Textually "...00Z" sorts after "...00.000001Z"; in time it is
        # earlier. The newest run must be the later instant.
        self._write([_row(run_id="A", stamp="2026-10-01T09:00:00Z"),
                     _row(run_id="B", stamp="2026-10-01T09:00:00.000001Z"),
                     _row(run_id="A", stamp="2026-10-01T08:59:59Z")])
        out = self.ev()
        self.assertIn("recorded: 1 sealed row(s)", out)
        self.assertIn("newest run B at 2026-10-01T09:00:00.000001Z", out)

    def test_hostile_arguments_never_raise(self):
        for bad in (None, 5, b"codex"):
            for args in ((bad, self.V, self.repo), ("codex", bad, self.repo),
                         ("codex", self.V, bad)):
                out = HC._live_evidence(*args)
                self.assertTrue(out.startswith(HC.NODATA), (args, out))

    def test_receipt_carries_the_live_sentence_on_the_real_tree(self):
        row = HC.host_capability_receipt(env={"BROTHER_CLIENT": "claude"})
        self.assertTrue(row["certification_evidence"].startswith(
            "documentation only, not a live test"))
        self.assertIn(" Live end-to-end evidence: " + HC.NODATA,
                      row["certification_evidence"])

    def test_unknown_host_gets_no_live_sentence(self):
        with mock.patch.object(HC.brother_paths, "client",
                               return_value="windsurf-9000"):
            row = HC.host_capability_receipt(env={})
        self.assertNotIn("Live end-to-end", row["certification_evidence"])

    def test_matching_evidence_never_upgrades_the_deny(self):
        self._write([_row()])
        with mock.patch.object(HC, "_host_version", return_value=self.V):
            row = HC.host_capability_receipt(env={"BROTHER_CLIENT": "codex"},
                                             repo=self.repo)
        self.assertTrue(row["enforceable_deny"].startswith(HC.NODATA))
        self.assertTrue(row["certification_evidence"].startswith(
            "documentation only, not a live test"))
        self.assertIn("Live end-to-end evidence: recorded: 1 sealed row(s)",
                      row["certification_evidence"])

    def test_constants_equal_the_collectors(self):
        try:
            import host_live_proof as P
        except ImportError:
            self.skipTest("NO-DATA: host_live_proof is absent from this tree")
        self.assertEqual(HC.EVIDENCE_REL, P.EVIDENCE_REL.replace(os.sep, "/"))
        self.assertEqual(HC.MANIFEST_SUFFIX, P.MANIFEST_SUFFIX)
        self.assertEqual(HC.EVIDENCE_SCHEMA, P.EVIDENCE_SCHEMA)


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

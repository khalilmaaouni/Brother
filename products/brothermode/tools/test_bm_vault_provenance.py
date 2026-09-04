#!/usr/bin/env python3
"""Calibration for tools/bm_vault_provenance.py, benchmark row D07.

The property under test is the row's own sentence: a claim line names its
own evidence locator, and the checker fails when that locator points at
nothing. Two shapes are guards rather than features and matter more than the
happy path: a URL locator is never resolved and never a pass, and a corpus
with zero claims is NO-DATA rather than a silent clean pass, because that is
the exact false-pass shape this estate's 2026-08-29 benchmark already
produced three times.

No em or en dashes anywhere in this file.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_provenance as prov  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def note(body):
    return "---\ntype: reference\nstatus: standing\n---\n\n# a note\n\n" + body + "\n"


def git_object_bytes(obj_type, content=b"x"):
    """Real zlib-compressed git loose-object bytes: "<type> <len>\\0<content>"
    inflated. Used to prove resolve_commit reads the object's own type
    header rather than trusting filename presence alone."""
    header = ("%s %d\0" % (obj_type, len(content))).encode("ascii")
    return zlib.compress(header + content)


class ClassifyingLocators(unittest.TestCase):
    def test_a_bare_path_is_a_path(self):
        self.assertEqual(prov.classify_locator("50-Reference/x.md"), "path")

    def test_a_path_with_a_heading_is_still_a_path(self):
        self.assertEqual(prov.classify_locator("50-Reference/x.md#A Heading"), "path")

    def test_a_stable_id_is_an_id(self):
        self.assertEqual(prov.classify_locator("n-0123456789abcdef"), "id")

    def test_repo_prefixed_sha_is_a_commit(self):
        self.assertEqual(prov.classify_locator("repo:abc1234"), "commit")

    def test_http_and_https_are_urls(self):
        self.assertEqual(prov.classify_locator("https://example.com/a"), "url")
        self.assertEqual(prov.classify_locator("http://example.com/a"), "url")


class FindingClaims(unittest.TestCase):
    def test_a_claim_line_is_found_with_its_locator(self):
        text = note("claim: the gate refuses at 07:00 JST [evidence: 50-Reference/gate.md]")
        self.assertEqual(prov.find_claims(text),
                         [("the gate refuses at 07:00 JST", "50-Reference/gate.md")])

    def test_a_note_with_no_claim_line_finds_nothing(self):
        self.assertEqual(prov.find_claims(note("just prose, no claim syntax here")), [])

    def test_multiple_claims_are_all_found(self):
        text = note("claim: one thing [evidence: a.md]\n"
                     "claim: another thing [evidence: n-0123456789abcdef]\n")
        self.assertEqual(len(prov.find_claims(text)), 2)

    def test_a_bulleted_claim_line_is_found(self):
        text = note("- claim: the gate closes [evidence: a.md]")
        self.assertEqual(prov.find_claims(text), [("the gate closes", "a.md")])

    def test_an_indented_claim_line_is_found(self):
        text = note("  claim: the gate closes [evidence: a.md]")
        self.assertEqual(prov.find_claims(text), [("the gate closes", "a.md")])

    def test_a_locator_containing_spaces_already_parses(self):
        text = note("claim: the section exists [evidence: 50-Reference/some note.md]")
        self.assertEqual(prov.find_claims(text),
                         [("the section exists", "50-Reference/some note.md")])

    def test_trailing_text_after_the_bracket_is_malformed_not_dropped(self):
        """Trailing prose used to vanish silently (the line failed to match
        at all, reading as zero claims). It must now surface as malformed,
        never as a plain resolved claim and never as nothing."""
        text = note("claim: the gate closes [evidence: a.md] and also on Tuesdays")
        self.assertEqual(prov.find_claims(text), [])
        malformed = prov.find_malformed_claims(text)
        self.assertEqual(len(malformed), 1)
        self.assertEqual(malformed[0][:2], ("the gate closes", "a.md"))

    def test_two_evidence_markers_on_one_line_first_wins_as_malformed(self):
        """Defined behaviour: the first [evidence: ...] bracket is captured
        as the locator, everything after it (including a second bracket) is
        trailing text, so the line reports malformed rather than silently
        picking one marker and hiding the other."""
        text = note("claim: x [evidence: a.md] [evidence: b.md]")
        self.assertEqual(prov.find_claims(text), [])
        malformed = prov.find_malformed_claims(text)
        self.assertEqual(len(malformed), 1)
        self.assertEqual(malformed[0][1], "a.md")


class TheCheckReadsARealTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-provenance-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        path = os.path.join(self.vault, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_resolving_path_locator_passes(self):
        self._write("target.md", note("the target"))
        self._write("claimer.md", note("claim: the target exists [evidence: target.md]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)

    def test_a_resolving_path_with_a_matching_heading_passes(self):
        self._write("target.md", note("## A Named Section\n\nbody"))
        self._write("claimer.md",
                     note("claim: the section exists [evidence: target.md#A Named Section]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)

    def test_a_path_with_a_missing_heading_is_dangling(self):
        self._write("target.md", note("## A Different Section\n\nbody"))
        self._write("claimer.md",
                     note("claim: the section exists [evidence: target.md#No Such Section]"))
        self.assertEqual(prov.cmd_check(self.vault), 1)

    def test_a_dangling_path_locator_fails_naming_the_claim(self):
        self._write("claimer.md",
                     note("claim: this points nowhere [evidence: no-such-file.md]"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_check(self.vault)
        self.assertEqual(code, 1)
        output = buf.getvalue()
        self.assertIn("this points nowhere", output)
        self.assertIn("no-such-file.md", output)

    def test_a_dangling_id_fails(self):
        self._write("claimer.md",
                     note("claim: this id does not exist [evidence: n-0000000000000000]"))
        self.assertEqual(prov.cmd_check(self.vault), 1)

    def test_a_resolving_id_passes(self):
        self._write("target.md", "---\nid: n-0123456789abcdef\ntype: reference\n---\n\nbody\n")
        self._write("claimer.md",
                     note("claim: the note exists [evidence: n-0123456789abcdef]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)

    def test_a_url_is_unverifiable_never_pass_never_fail(self):
        self._write("claimer.md",
                     note("claim: something external is true [evidence: https://example.com/a]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)
        rows = prov.scan(self.vault)
        self.assertEqual(rows[0][4], "unverifiable")

    def test_zero_claims_reports_NO_DATA_never_a_pass(self):
        self._write("plain.md", note("no claim syntax anywhere in this note"))
        self.assertEqual(prov.cmd_check(self.vault), 2)

    def test_an_empty_vault_also_reports_NO_DATA(self):
        self.assertEqual(prov.cmd_check(self.vault), 2)

    def test_a_locator_that_escapes_the_vault_root_is_dangling(self):
        self._write("claimer.md",
                     note("claim: reaches outside [evidence: ../../etc/passwd]"))
        self.assertEqual(prov.cmd_check(self.vault), 1)

    def test_a_bulleted_claim_resolves_through_check(self):
        """The exact false NO-DATA this row exists to prevent: a bulleted
        claim line used to be found zero times, which read as 'this
        capability is not in use' rather than 'this claim resolves'."""
        self._write("target.md", note("the target"))
        self._write("claimer.md", note("- claim: the target exists [evidence: target.md]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)

    def test_an_indented_claim_resolves_through_check(self):
        self._write("target.md", note("the target"))
        self._write("claimer.md", note("  claim: the target exists [evidence: target.md]"))
        self.assertEqual(prov.cmd_check(self.vault), 0)

    def test_a_malformed_claim_line_fails_and_is_named_through_check(self):
        self._write("target.md", note("the target"))
        self._write("claimer.md",
                     note("claim: trailing text case [evidence: target.md] extra words"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_check(self.vault)
        self.assertEqual(code, 1)
        self.assertIn("MALFORMED", buf.getvalue())
        self.assertIn("trailing text case", buf.getvalue())


class CommitLocators(unittest.TestCase):
    """Loose-object resolution only, stdlib only. A vault with no .git and a
    vault whose object is genuinely absent are different findings: the first
    is unverifiable (this checker cannot tell), the second is dangling
    (there is nowhere else the object could be, no pack files exist)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-provenance-commit-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_git_directory_is_unverifiable(self):
        status, _detail = prov.resolve_commit(self.vault, "abc1234")
        self.assertEqual(status, "unverifiable")

    def test_an_unpacked_repo_with_no_matching_loose_object_is_dangling(self):
        os.makedirs(os.path.join(self.vault, ".git", "objects", "ab"))
        status, _detail = prov.resolve_commit(self.vault, "ab" + "0" * 38)
        self.assertEqual(status, "dangling")

    def test_a_matching_loose_commit_object_resolves(self):
        obj_dir = os.path.join(self.vault, ".git", "objects", "ab")
        os.makedirs(obj_dir)
        sha = "ab" + "1" * 38
        with open(os.path.join(obj_dir, sha[2:]), "wb") as fh:
            fh.write(git_object_bytes("commit", b"a fake commit body"))
        status, _detail = prov.resolve_commit(self.vault, sha)
        self.assertEqual(status, "ok")

    def test_a_loose_blob_at_that_sha_does_not_pass_as_a_commit(self):
        """The property this closes: presence of a file under the sha's
        prefix directory used to be enough to pass, whatever object type it
        actually held."""
        obj_dir = os.path.join(self.vault, ".git", "objects", "ab")
        os.makedirs(obj_dir)
        sha = "ab" + "2" * 38
        with open(os.path.join(obj_dir, sha[2:]), "wb") as fh:
            fh.write(git_object_bytes("blob", b"just file content, not a commit"))
        status, _detail = prov.resolve_commit(self.vault, sha)
        self.assertNotEqual(status, "ok")
        self.assertEqual(status, "dangling")

    def test_a_packed_repo_with_no_loose_match_is_unverifiable_not_dangling(self):
        """The property that keeps this checker honest: it must never turn
        'I cannot read a packfile' into a false FAIL."""
        pack_dir = os.path.join(self.vault, ".git", "objects", "pack")
        os.makedirs(pack_dir)
        with open(os.path.join(pack_dir, "pack-deadbeef.pack"), "wb") as fh:
            fh.write(b"")
        status, _detail = prov.resolve_commit(self.vault, "ab" + "0" * 38)
        self.assertEqual(status, "unverifiable")


class EnterpriseLocatorClassification(unittest.TestCase):
    def test_a_query_id_locator_classifies_as_query_id(self):
        self.assertEqual(
            prov.classify_locator("query:snowflake|q-42|2026-08-30T00:00:00Z|abc123"),
            "query_id")

    def test_a_docspan_locator_classifies_as_document_span(self):
        self.assertEqual(
            prov.classify_locator("docspan:contract.pdf|" + "a" * 64 + "|3|10|40"),
            "document_span")

    def test_a_capture_locator_classifies_as_capture(self):
        self.assertEqual(
            prov.classify_locator("capture:screens/a.png|2026-08-30T00:00:00Z|" + "b" * 64),
            "capture")


class EnterpriseLocatorResolution(unittest.TestCase):
    """Driven backwards per the brief: for each new kind, one fixture that
    resolves, one that is UNAVAILABLE naming the missing piece, and (for
    capture) one that is TAMPERED naming the mismatch. document_span also
    proves it never resolves against a version that has since changed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-provenance-enterprise-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- query_id --

    def test_query_id_resolves_against_a_matching_ledger_record(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write('{"system": "snowflake", "query_id": "q-42", '
                     '"executed_at": "2026-08-30T00:00:00Z", "result_hash": "abc123"}\n')
        locator = "query:snowflake|q-42|2026-08-30T00:00:00Z|abc123"
        status, detail = prov.resolve_query(locator, ledger)
        self.assertEqual((status, detail), ("ok", None))

    def test_query_id_with_no_ledger_is_unavailable_naming_the_query(self):
        locator = "query:snowflake|q-42|2026-08-30T00:00:00Z|abc123"
        status, detail = prov.resolve_query(locator, None)
        self.assertEqual(status, "unavailable")
        self.assertIn("q-42", detail)

    def test_query_id_with_a_differing_result_hash_is_unavailable_naming_the_drift(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write('{"system": "snowflake", "query_id": "q-42", '
                     '"executed_at": "2026-08-30T00:00:00Z", "result_hash": "different"}\n')
        locator = "query:snowflake|q-42|2026-08-30T00:00:00Z|abc123"
        status, detail = prov.resolve_query(locator, ledger)
        self.assertEqual(status, "unavailable")
        self.assertIn("drift", detail)

    def test_a_malformed_query_locator_is_dangling(self):
        status, _detail = prov.resolve_query("query:onlytwo|fields", None)
        self.assertEqual(status, "dangling")

    # -- document_span --

    def test_document_span_resolves_when_the_store_holds_the_bound_version(self):
        store = os.path.join(self.tmp, "docstore")
        os.makedirs(store)
        doc_path = os.path.join(store, "contract.pdf")
        with open(doc_path, "wb") as fh:
            fh.write(b"the contract, version one")
        version = prov._sha256_file(doc_path)
        locator = "docspan:contract.pdf|%s|3|10|40" % version
        status, detail = prov.resolve_document_span(self.vault, locator, store)
        self.assertEqual((status, detail), ("ok", None))

    def test_document_span_with_no_store_is_unavailable_naming_the_document(self):
        locator = "docspan:contract.pdf|" + "a" * 64 + "|3|10|40"
        status, detail = prov.resolve_document_span(self.vault, locator, None)
        self.assertEqual(status, "unavailable")
        self.assertIn("contract.pdf", detail)

    def test_document_span_refuses_to_resolve_against_a_changed_version(self):
        """A source change after linking leaves the bound version authoritative:
        the store file now hashes differently, so this must never silently
        resolve against the new bytes -- it reports unavailable and names
        both the old and the new hash (the drift)."""
        store = os.path.join(self.tmp, "docstore")
        os.makedirs(store)
        doc_path = os.path.join(store, "contract.pdf")
        with open(doc_path, "wb") as fh:
            fh.write(b"the contract, version one")
        bound_version = prov._sha256_file(doc_path)
        with open(doc_path, "wb") as fh:
            fh.write(b"the contract, version two, edited after linking")
        locator = "docspan:contract.pdf|%s|3|10|40" % bound_version
        status, detail = prov.resolve_document_span(self.vault, locator, store)
        self.assertEqual(status, "unavailable")
        self.assertIn("changed", detail)
        self.assertIn(bound_version[:12], detail)

    # -- capture --

    def test_capture_resolves_when_the_hash_matches(self):
        capture_root = os.path.join(self.tmp, "captures")
        os.makedirs(capture_root)
        cap_path = os.path.join(capture_root, "screens", "a.png")
        os.makedirs(os.path.dirname(cap_path))
        with open(cap_path, "wb") as fh:
            fh.write(b"pretend png bytes")
        sha = prov._sha256_file(cap_path)
        locator = "capture:screens/a.png|2026-08-30T00:00:00Z|%s" % sha
        status, detail = prov.resolve_capture(self.vault, locator, capture_root)
        self.assertEqual((status, detail), ("ok", None))

    def test_capture_missing_file_is_unavailable_naming_the_path(self):
        capture_root = os.path.join(self.tmp, "captures")
        os.makedirs(capture_root)
        locator = "capture:screens/missing.png|2026-08-30T00:00:00Z|" + "a" * 64
        status, detail = prov.resolve_capture(self.vault, locator, capture_root)
        self.assertEqual(status, "unavailable")
        self.assertIn("screens/missing.png", detail)

    def test_capture_flipped_byte_is_tampered_naming_both_hashes(self):
        capture_root = os.path.join(self.tmp, "captures")
        os.makedirs(capture_root)
        cap_path = os.path.join(capture_root, "a.bin")
        with open(cap_path, "wb") as fh:
            fh.write(b"\x00" * 32)
        bound_sha = prov._sha256_file(cap_path)
        with open(cap_path, "wb") as fh:
            fh.write(b"\x01" + b"\x00" * 31)  # one flipped byte
        locator = "capture:a.bin|2026-08-30T00:00:00Z|%s" % bound_sha
        status, detail = prov.resolve_capture(self.vault, locator, capture_root)
        self.assertEqual(status, "tampered")
        self.assertIn(bound_sha[:12], detail)


class TheCheckCountsEnterpriseStatuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-provenance-check-enterprise-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_an_unresolvable_enterprise_claim_fails_the_check(self):
        self._write("claimer.md",
                     note("claim: the query ran [evidence: query:sys|q1|t1|h1]"))
        code = prov.cmd_check(self.vault)
        self.assertEqual(code, 1)

    def test_a_resolving_enterprise_claim_passes_the_check(self):
        ledger = os.path.join(self.tmp, "ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as fh:
            fh.write('{"system": "sys", "query_id": "q1", "executed_at": "t1", '
                     '"result_hash": "h1"}\n')
        self._write("claimer.md",
                     note("claim: the query ran [evidence: query:sys|q1|t1|h1]"))
        code = prov.cmd_check(self.vault, query_ledger=ledger)
        self.assertEqual(code, 0)


class KindsListing(unittest.TestCase):
    def test_kinds_lists_all_seven_kinds(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_kinds(False)
        self.assertEqual(code, 0)
        output = buf.getvalue()
        for kind in ("path", "id", "commit", "url", "query_id", "document_span", "capture"):
            self.assertIn(kind, output)

    def test_kinds_json_is_machine_readable_and_names_every_kind(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prov.cmd_kinds(True)
        payload = __import__("json").loads(buf.getvalue())
        kinds = {row["kind"] for row in payload}
        self.assertEqual(
            kinds,
            {"path", "id", "commit", "url", "query_id", "document_span", "capture"})
        for row in payload:
            self.assertTrue(row["fields"], "%s declares no fields" % row["kind"])


class GetEvidenceSubcommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-provenance-get-evidence-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, text):
        with open(os.path.join(self.vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_get_evidence_resolves_a_plain_path_claim(self):
        self._write("target.md", note("the target"))
        self._write("claimer.md", note("claim: the target exists [evidence: target.md]"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_get_evidence(self.vault, "claimer.md", "the target exists")
        self.assertEqual(code, 0)
        self.assertIn("RESOLVED", buf.getvalue())

    def test_get_evidence_reports_unavailable_for_an_unresolvable_capture(self):
        self._write(
            "claimer.md",
            note("claim: the screenshot was taken "
                 "[evidence: capture:missing.png|2026-08-30T00:00:00Z|" + "a" * 64 + "]"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_get_evidence(self.vault, "claimer.md", "the screenshot was taken")
        self.assertEqual(code, 1)
        self.assertIn("UNAVAILABLE", buf.getvalue())

    def test_get_evidence_reports_tampered_for_a_flipped_capture(self):
        cap_path = os.path.join(self.vault, "a.bin")
        with open(cap_path, "wb") as fh:
            fh.write(b"\x00" * 32)
        sha = prov._sha256_file(cap_path)
        with open(cap_path, "wb") as fh:
            fh.write(b"\x01" + b"\x00" * 31)
        self._write("claimer.md",
                     note("claim: the capture matches "
                          "[evidence: capture:a.bin|2026-08-30T00:00:00Z|%s]" % sha))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = prov.cmd_get_evidence(self.vault, "claimer.md", "the capture matches")
        self.assertEqual(code, 1)
        self.assertIn("TAMPERED", buf.getvalue())

    def test_get_evidence_on_an_unknown_assertion_exits_nonzero_naming_it(self):
        self._write("claimer.md", note("claim: something [evidence: target.md]"))
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = prov.cmd_get_evidence(self.vault, "claimer.md", "no such claim text")
        self.assertNotEqual(code, 0)
        self.assertIn("UNKNOWN-ASSERTION", buf.getvalue())
        self.assertIn("no such claim text", buf.getvalue())

    def test_get_evidence_on_an_unknown_note_is_no_data(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = prov.cmd_get_evidence(self.vault, "no-such-note.md", "anything")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

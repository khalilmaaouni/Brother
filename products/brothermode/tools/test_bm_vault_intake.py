#!/usr/bin/env python3
"""Calibration for tools/bm_vault_intake.py, WBS row VB6-01.

The row's own done_check, verbatim: a fixture intake run admits a dirty note
as candidate with its dirt classes recorded, hard-rejects a planted
credential-bearing file with the class named at exit 1, quarantines a
restricted item that recall then withholds, and a spreadsheet capsule carries
all five interpretation fields; calibrate by removing one gate and watching
its named test fail.

No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INTAKE = os.path.join(HERE, "bm_vault_intake.py")
VAULT_TOOL = os.path.join(HERE, "bm_vault.py")

sys.path.insert(0, HERE)
import bm_vault_intake as intake  # noqa: E402


def run_admit(args, cwd=None, env=None, intake_path=INTAKE):
    p = subprocess.run([sys.executable, intake_path, "admit"] + args, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace")


def _copy_tools_to(dest):
    """Copy every non-test tools/*.py file into dest: how a calibration test removes
    or renames a "gate" module WITHOUT ever touching this checkout's own tracked file
    (TestLoopP9NoSuiteMutatesThisCheckout in test_bm.py forbids exactly that -- a
    per-suite timeout SIGKILLs the child, which runs no finally clause, so any test
    that moves a real module aside and restores it in a finally can leave a
    production module deleted). The copy lives entirely under the caller's own
    tempdir and is discarded with it; nothing here ever writes into HERE."""
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(HERE):
        if name.endswith(".py") and not name.startswith("test_"):
            shutil.copy2(os.path.join(HERE, name), os.path.join(dest, name))
    return dest


FAKE_KEY = "AKIA" + "1234567890ABCDEF"  # assembled so history scans never hit a key-shaped literal

ZW_SPACE = u"​"


def _zero_width_salted(s):
    """s with a zero width space between every character, assembled at
    runtime so the salted shape is never a scannable literal in source."""
    return ZW_SPACE.join(list(s))


def _fullwidth_homoglyph(s):
    """s with every ASCII letter/digit swapped for its fullwidth Unicode
    homoglyph (NFKC-collapses back to the ASCII form), assembled at runtime
    for the same reason."""
    return u"".join(chr(0xFEE0 + ord(c)) if ("A" <= c <= "Z" or "0" <= c <= "9") else c
                     for c in s)


CJK_TERM = u"機密情報"  # an ordinary deny-list fixture term, not a secret shape

# Bytes that fail to decode under every candidate in bm_vault_intake.DECODE_CANDIDATES
# (utf-8, cp932, shift_jis, euc_jp, utf-16), found by brute-force search and pinned
# here as a fixture rather than re-searched at test time.
UNDECODABLE_BYTES = bytes.fromhex("eb8ac2fce7")

class DirtClassificationDirect(unittest.TestCase):
    """classify_dirt() and duplicate_suspects() called directly, no subprocess,
    no filesystem beyond what the function itself takes."""

    def test_encoding_suspect_on_invalid_utf8(self):
        raw = b"\xff\xfe not valid utf8 \x80\x81"
        dirt = intake.classify_dirt(raw, raw.decode("utf-8", "replace"),
                                     "x.txt", "sys", "actor")
        self.assertIn("encoding-suspect", dirt)

    def test_encoding_suspect_on_mojibake_heuristic(self):
        text = "the cafÃ© receipt"  # a real mojibake fragment (Ã©)
        dirt = intake.classify_dirt(text.encode("utf-8"), text, "x.txt", "sys", "actor")
        self.assertIn("encoding-suspect", dirt)

    def test_clean_ascii_is_not_encoding_suspect(self):
        text = "ordinary clean content"
        dirt = intake.classify_dirt(text.encode("utf-8"), text, "x.txt", "sys", "actor")
        self.assertNotIn("encoding-suspect", dirt)

    def test_missing_provenance_when_source_absent(self):
        dirt = intake.classify_dirt(b"x", "x", "x.txt", None, "actor")
        self.assertIn("missing-provenance", dirt)

    def test_missing_provenance_when_by_absent(self):
        dirt = intake.classify_dirt(b"x", "x", "x.txt", "sys", None)
        self.assertIn("missing-provenance", dirt)

    def test_no_missing_provenance_when_both_given(self):
        dirt = intake.classify_dirt(b"x", "x", "x.txt", "sys", "actor")
        self.assertNotIn("missing-provenance", dirt)

    def test_stale_copy_suspect_from_filename(self):
        dirt = intake.classify_dirt(b"x", "x", "report copy final v2.txt", "sys", "actor")
        self.assertIn("stale-copy-suspect", dirt)

    def test_no_stale_copy_suspect_on_an_ordinary_name(self):
        dirt = intake.classify_dirt(b"x", "x", "quarterly report.txt", "sys", "actor")
        self.assertNotIn("stale-copy-suspect", dirt)

    def test_duplicate_suspects_over_threshold(self):
        existing = [("zorbly payroll ledger", "a.md", "n-aaaaaaaaaaaaaaaa")]
        hits = intake.duplicate_suspects(_load_distill(), "zorbly payroll ledger detail", existing)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0], "n-aaaaaaaaaaaaaaaa")

    def test_duplicate_suspects_below_threshold_is_empty(self):
        existing = [("something entirely unrelated", "a.md", None)]
        hits = intake.duplicate_suspects(_load_distill(), "zorbly payroll ledger", existing)
        self.assertEqual(hits, [])


def _load_distill():
    return intake._load_sibling("bm_vault_distill")


class CapsuleFieldsDirect(unittest.TestCase):
    class _Args(object):
        locale = None
        as_of = None

    def test_csv_carries_all_five_interpretation_fields(self):
        fields = dict(intake.capsule_fields(".csv", b"a,b\n1,2\n", self._Args()))
        for key in ("capsule_encoding", "capsule_locale", "capsule_as_of",
                    "capsule_display_vs_stored", "capsule_reproducible"):
            self.assertIn(key, fields, key)

    def test_xlsx_is_noted_unparsed_with_no_data_encoding(self):
        fields = dict(intake.capsule_fields(".xlsx", b"PK\x03\x04binary", self._Args()))
        self.assertIn("NO-DATA", fields["capsule_encoding"])
        self.assertEqual(fields["capsule_format"], "xlsx-unparsed")

    def test_locale_and_as_of_flow_through_when_given(self):
        args = self._Args()
        args.locale = "en-US"
        args.as_of = "2026-08-01"
        fields = dict(intake.capsule_fields(".csv", b"a,b\n1,2\n", args))
        self.assertEqual(fields["capsule_locale"], "en-US")
        self.assertEqual(fields["capsule_as_of"], "2026-08-01")


class NeverWritesOutsideInbox(unittest.TestCase):
    """Constraint 4's own mechanical proof: instrument a fixture vault, admit
    several files (plain, restricted, non-utf8), and assert every path that
    changed on disk sits under 00-Inbox/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        with open(os.path.join(self.vault, "40-Failures", "existing.md"), "w") as f:
            f.write("---\nid: n-0000000000000001\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# an existing note\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _snapshot(self):
        snap = {}
        for dirpath, _dirnames, filenames in os.walk(self.vault):
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                snap[p] = os.path.getmtime(p)
        return snap

    def test_only_00_inbox_ever_gains_or_changes_a_file(self):
        before = self._snapshot()
        plain = os.path.join(self.tmp, "plain.txt")
        with open(plain, "w") as f:
            f.write("some ordinary content admitted into the airlock")
        restricted = os.path.join(self.tmp, "restricted.txt")
        with open(restricted, "w") as f:
            f.write("quarantine this please")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", "--restricted", restricted])
        self.assertEqual(code, 0, out + err)
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", plain])
        self.assertEqual(code, 0, out + err)
        after = self._snapshot()
        for p in before:
            self.assertEqual(before[p], after.get(p), "%s changed outside 00-Inbox" % p)
        new_paths = set(after) - set(before)
        self.assertTrue(new_paths, "admit wrote nothing at all")
        for p in new_paths:
            rel = os.path.relpath(p, self.vault)
            self.assertTrue(rel.split(os.sep)[0] == "00-Inbox",
                            "wrote outside 00-Inbox: %s" % rel)


PRIVATE_TITLE_STEM = "quarantined-payroll-doc"


class TheAirlockContract(unittest.TestCase):
    """The row's own done_check, driven end to end against one fixture vault."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-intake-airlock-")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(cls.vault)
        os.makedirs(os.path.join(cls.tmp, ".claude"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _src(self, name, content, binary=False):
        path = os.path.join(self.tmp, name)
        mode = "wb" if binary else "w"
        with open(path, mode) as f:
            f.write(content)
        return path

    def test_01_admits_a_dirty_note_as_candidate_with_dirt_classes_recorded(self):
        src = self._src("quarterly report copy final v2.txt",
                        "an ordinary note about last quarter's numbers")
        code, out, err = run_admit(["--vault", self.vault, src])
        self.assertEqual(code, 0, out + err)
        self.assertIn("ADMITTED", out)
        self.assertIn("dirt=", out)
        self.assertIn("stale-copy-suspect", out)
        self.assertIn("missing-provenance", out)  # no --source/--by given
        note_path = None
        inbox = os.path.join(self.vault, "00-Inbox")
        for fn in os.listdir(inbox):
            if fn.endswith(".md"):
                note_path = os.path.join(inbox, fn)
        self.assertIsNotNone(note_path)
        with open(note_path) as f:
            body = f.read()
        self.assertIn("promotion: candidate", body)
        self.assertIn("dirt_classes:", body)

    def test_01b_author_of_record_matches_by_when_by_is_given(self):
        # V14.1: bm_vault_promotions.cmd_promote refuses fail-closed when a
        # candidate carries no author: field. --by is that principal here.
        src = self._src("attributed note.txt", "an ordinary attributed note")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src])
        self.assertEqual(code, 0, out + err)
        note_line = [l for l in out.splitlines() if "attributed note" in l][0]
        note_rel = note_line.split("-> ")[1].split("  id=")[0]
        with open(os.path.join(self.vault, note_rel)) as f:
            body = f.read()
        self.assertIn("author: actor", body)

    def test_02_hard_rejects_a_credential_bearing_file_named_at_exit_1(self):
        before = set(os.listdir(os.path.join(self.vault, "00-Inbox")))
        src = self._src("creds.txt", FAKE_KEY + " is a live key")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=credential-shape", out)
        self.assertNotIn(FAKE_KEY, out)
        after = set(os.listdir(os.path.join(self.vault, "00-Inbox")))
        self.assertEqual(before, after, "a hard-rejected file must write nothing")

    def test_03_quarantines_a_restricted_item_that_recall_then_withholds(self):
        src = self._src(PRIVATE_TITLE_STEM + ".txt",
                        "the quarantined payroll figures nobody unprivileged should read")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", "--restricted", src])
        self.assertEqual(code, 0, out + err)
        quarantine_dir = os.path.join(self.vault, "00-Inbox", "quarantine")
        self.assertTrue(os.path.isdir(quarantine_dir))
        note_names = [fn for fn in os.listdir(quarantine_dir) if fn.endswith(".md")]
        self.assertTrue(note_names)

        policy_dir = os.path.join(self.vault, "99-System")
        os.makedirs(policy_dir, exist_ok=True)
        policy_path = os.path.join(policy_dir, "access-policy.json")
        with open(policy_path, "w") as f:
            json.dump({"rules": [{"identity": "bob", "path": "00-Inbox/quarantine/*",
                                  "action": "deny"}]}, f)
        try:
            env = dict(os.environ)
            env["HOME"] = self.tmp
            env["BROTHERMODE_ROOT"] = self.tmp
            env["BM_VAULT_ROOT"] = self.vault
            env["BM_FRESHNESS_ROOTS"] = self.tmp
            env["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness.sqlite3")
            env.pop("BM_IDENTITY", None)
            idx = subprocess.run([sys.executable, VAULT_TOOL, "index", "--vault", self.vault],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(idx.returncode, 0, idx.stdout.decode() + idx.stderr.decode())
            rec = subprocess.run([sys.executable, VAULT_TOOL, "recall", "--query",
                                  "quarantined payroll figures", "--limit", "5", "--fast",
                                  "--identity", "bob"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out = rec.stdout.decode("utf-8", "replace")
            self.assertNotIn(PRIVATE_TITLE_STEM, out)
            self.assertIn("withheld by access policy", out)
        finally:
            os.unlink(policy_path)

    def test_04_spreadsheet_capsule_carries_all_five_interpretation_fields(self):
        src = self._src("figures.csv", "a,b,c\n1,2,3\n")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys", "--by", "actor",
                                    "--locale", "en-US", "--as-of", "2026-08-01", src])
        self.assertEqual(code, 0, out + err)
        note_line = [l for l in out.splitlines() if "figures.csv" in l][0]
        note_rel = note_line.split("-> ")[1].split("  id=")[0]
        with open(os.path.join(self.vault, note_rel)) as f:
            body = f.read()
        for key in ("capsule_encoding", "capsule_locale", "capsule_as_of",
                    "capsule_display_vs_stored", "capsule_reproducible"):
            self.assertIn(key + ":", body, key)
        self.assertIn("capsule_locale: en-US", body)
        self.assertIn("capsule_as_of: 2026-08-01", body)


class GateRemovalCalibration(unittest.TestCase):
    """Drives the credential gate BACKWARDS in an isolated COPY of tools/, never this
    checkout: the module the gate reuses is removed from the copy, and the exact
    assertion test_02 above relies on (the message names class=credential-shape) is
    proven to fail once the gate cannot run. Fails CLOSED either way (still exit 1)
    is the correct security shape, but a NO-DATA refusal is not a named-class
    rejection, so the specific claim test_02 makes is the one that goes false, which
    is the point of this calibration."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-calibration-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.toolscopy = _copy_tools_to(os.path.join(self.tmp, "toolscopy"))
        self.intake_copy = os.path.join(self.toolscopy, "bm_vault_intake.py")
        self.telemetry_copy = os.path.join(self.toolscopy, "bm_telemetry.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_with_the_gate_present_the_class_is_named(self):
        src = os.path.join(self.tmp, "creds.txt")
        with open(src, "w") as f:
            f.write(FAKE_KEY + " is a live key")
        code, out, _err = run_admit(["--vault", self.vault, "--source", "sys",
                                     "--by", "actor", src], intake_path=self.intake_copy)
        self.assertEqual(code, 1)
        self.assertIn("class=credential-shape", out)

    def test_with_the_gate_removed_that_same_assertion_fails(self):
        src = os.path.join(self.tmp, "creds.txt")
        with open(src, "w") as f:
            f.write(FAKE_KEY + " is a live key")
        self.assertTrue(os.path.isfile(self.telemetry_copy),
                         "fixture assumption: gate module exists in the copy")
        os.remove(self.telemetry_copy)
        code, out, _err = run_admit(["--vault", self.vault, "--source", "sys",
                                     "--by", "actor", src], intake_path=self.intake_copy)
        # Still fails closed (exit 1: NO-DATA, never silently admitted)...
        self.assertEqual(code, 1)
        # ...but the NAMED test_02 assertion ("class=credential-shape") is
        # exactly what stops holding once the gate cannot run.
        self.assertNotIn("class=credential-shape", out)
        self.assertIn("NO-DATA", out)


class NormalizationBypassRegression(unittest.TestCase):
    """MAJOR security fix: a zero-width space or a fullwidth homoglyph
    planted inside a credential shape must not defeat the plain-ASCII gate
    regexes. Both fixtures are assembled at runtime from FAKE_KEY so no
    key-shaped literal (plain, salted, or homoglyphed) sits in source."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-normalize-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _src(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_zero_width_space_salted_key_hard_rejects(self):
        src = self._src("zw.txt", _zero_width_salted(FAKE_KEY) + " is a live key")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=credential-shape", out)

    def test_fullwidth_homoglyph_key_hard_rejects(self):
        src = self._src("fw.txt", _fullwidth_homoglyph(FAKE_KEY) + " is a live key")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=credential-shape", out)


class NormalizationBypassCalibrationDirect(unittest.TestCase):
    """Drives MAJOR 1's fix backwards, in process: proves the raw ASCII
    patterns alone (the pre-fix behavior) miss both salted shapes, and that
    credential_hit's normalized-view addition is what catches them. This is
    the "drop the normalized view, watch it fail" step, done by calling the
    raw scan directly rather than editing and restoring the source."""

    def test_raw_view_alone_misses_the_zero_width_salted_key(self):
        salted = _zero_width_salted(FAKE_KEY)
        telemetry = intake._load_sibling("bm_telemetry")
        self.assertFalse(any(pat.search(salted) for pat in telemetry.SECRET_PATTERNS),
                          "a zero-width-salted key must NOT match the raw patterns alone; "
                          "if it does, this fixture stopped proving anything")
        hit, no_data = intake.credential_hit(salted)
        self.assertIsNone(no_data)
        self.assertTrue(hit, "credential_hit must still catch it via the normalized view")

    def test_raw_view_alone_misses_the_fullwidth_homoglyph_key(self):
        fullwidth = _fullwidth_homoglyph(FAKE_KEY)
        telemetry = intake._load_sibling("bm_telemetry")
        self.assertFalse(any(pat.search(fullwidth) for pat in telemetry.SECRET_PATTERNS),
                          "a fullwidth-homoglyph key must NOT match the raw patterns alone; "
                          "if it does, this fixture stopped proving anything")
        hit, no_data = intake.credential_hit(fullwidth)
        self.assertIsNone(no_data)
        self.assertTrue(hit, "credential_hit must still catch it via the normalized view")


class EncodingBlindnessRegression(unittest.TestCase):
    """MAJOR security fix: a deny-listed non-ASCII term inside a non-UTF-8
    encoded file must still hard-reject, and a byte blob no candidate codec
    can decode must be refused rather than silently admitted ungated."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-encoding-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.deny_list = os.path.join(self.tmp, "deny.txt")
        # The deny-list file is read as utf-8 (bm_private_scan's own loader);
        # the SOURCE fixture below is encoded as cp932. Two different byte
        # encodings for the same CJK term is the whole point of the test.
        with open(self.deny_list, "w", encoding="utf-8") as f:
            f.write(CJK_TERM + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cp932_encoded_deny_term_hard_rejects(self):
        src = os.path.join(self.tmp, "sjis.txt")
        with open(src, "wb") as f:
            f.write((CJK_TERM + " confidential").encode("cp932"))
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", "--deny-list", self.deny_list, src])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=deny-list-term", out)

    def test_undecodable_bytes_refuse_with_the_class_named(self):
        src = os.path.join(self.tmp, "blob.bin")
        with open(src, "wb") as f:
            f.write(UNDECODABLE_BYTES)
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=unscannable-encoding", out)

    def test_encoding_flag_lets_a_cp932_file_scan_cleanly(self):
        src = os.path.join(self.tmp, "sjis_clean.txt")
        with open(src, "wb") as f:
            f.write("ordinary prose with no denied term".encode("cp932"))
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", "--encoding", "cp932", src])
        self.assertEqual(code, 0, out + err)


class EncodingBlindnessCalibrationDirect(unittest.TestCase):
    """Drives MAJOR 2's fix backwards, in process: the pre-fix behavior only
    ever looked at raw.decode("utf-8", errors="replace"), which mangles a
    cp932-encoded CJK term into unmatchable garbage; the fix's multi-decode
    scan is what actually catches it. Cheap to prove directly, no subprocess
    or source-editing round trip needed."""

    def test_utf8_only_view_would_miss_the_cp932_term(self):
        raw = (CJK_TERM + " confidential").encode("cp932")
        pre_fix_text = raw.decode("utf-8", errors="replace")  # the old, only view
        tmp = tempfile.mkdtemp(prefix="bm-intake-calib-deny-old-")
        try:
            deny_list = os.path.join(tmp, "deny.txt")
            with open(deny_list, "w", encoding="utf-8") as f:
                f.write(CJK_TERM + "\n")
            hit, no_data = intake.deny_list_hit(pre_fix_text, deny_list)
            self.assertIsNone(no_data)
            self.assertFalse(hit, "the old utf-8-only view must NOT catch a cp932 term; "
                                   "if it does, this fixture stopped proving anything")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_multi_decode_catches_what_the_utf8_only_view_misses(self):
        raw = (CJK_TERM + " confidential").encode("cp932")
        decodes = intake._decode_candidates(raw)
        self.assertTrue(any(enc == "cp932" for enc, _text in decodes),
                         "fixture assumption: cp932 is one of the candidates tried")
        tmp = tempfile.mkdtemp(prefix="bm-intake-calib-deny-new-")
        try:
            deny_list = os.path.join(tmp, "deny.txt")
            with open(deny_list, "w", encoding="utf-8") as f:
                f.write(CJK_TERM + "\n")
            hit_any = False
            for _enc, dtext in decodes:
                hit, no_data = intake.deny_list_hit(dtext, deny_list)
                self.assertIsNone(no_data)
                hit_any = hit_any or hit
            self.assertTrue(hit_any, "at least one candidate decode must catch the term")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SelfEchoProvenance(unittest.TestCase):
    """VB6-06's own done_check, verbatim: a served answer re-ingested through the
    intake fixture is classified echo with its source event id, and a corroboration
    count over the fixture excludes it; a genuinely independent note with similar
    text still counts; both directions tested.

    Driven end to end: bm_vault.py recall is actually run against a fixture vault,
    its real printed output (marker line included) is fed back through
    bm_vault_intake.py admit, exactly the shape a downstream re-ingestion would take."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-echo-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        os.makedirs(os.path.join(self.tmp, ".claude"))
        # The id is a mixed hex string, not a run of 16 plain digits, on purpose: an
        # all-digit id (like the OTHER fixture classes in this file use) reads as a
        # credit-card-shaped number to bm_telemetry's own credential gate once it is
        # printed into a recall's output and re-ingested here, which would hard-reject
        # the echo fixture on an unrelated gate before VB6-06's own logic ever runs.
        with open(os.path.join(self.vault, "40-Failures", "zorbly-payroll-ledger.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f678\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly payroll ledger drift\n\n"
                    "Root cause of the zorbly payroll ledger drift, written down once.\n")
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BROTHERMODE_ROOT"] = self.tmp
        self.env["BM_VAULT_ROOT"] = self.vault
        self.env["BM_FRESHNESS_ROOTS"] = self.tmp
        self.env["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness.sqlite3")
        self.env.pop("BM_IDENTITY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _recall_raw(self, query):
        idx = subprocess.run([sys.executable, VAULT_TOOL, "index", "--vault", self.vault],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(idx.returncode, 0, idx.stdout.decode() + idx.stderr.decode())
        rec = subprocess.run([sys.executable, VAULT_TOOL, "recall", "--query", query,
                              "--limit", "5", "--fast"],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = rec.stdout.decode("utf-8", "replace")
        self.assertEqual(rec.returncode, 0, out + rec.stderr.decode())
        return out

    @staticmethod
    def _note_rel(out):
        note_line = [l for l in out.splitlines() if l.startswith("ADMITTED")][0]
        return note_line.split("-> ")[1].split("  id=")[0]

    def test_a_served_answer_reingested_is_classified_echo_with_its_source_event_id(self):
        served = self._recall_raw("zorbly payroll ledger drift")
        marker_lines = [l for l in served.splitlines()
                        if l.startswith("derived-from-vault: event=")]
        self.assertTrue(marker_lines, "fixture assumption: recall printed its own "
                                       "VB6-06 provenance marker")
        event_id = marker_lines[-1].split("event=")[1].strip()

        echo_src = os.path.join(self.tmp, "reingested-answer.txt")
        with open(echo_src, "w") as f:
            f.write(served)
        # MAJOR 1 fix: has_event() looks the event id up in bm_vault_audit's own
        # log, so admit must resolve AUDIT_PATH under the SAME HOME the recall
        # above wrote it under, or a genuine echo would misclassify as forged.
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", echo_src], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("echo-of-vault-answer", out)
        self.assertNotIn("duplicate-suspect", out)

        with open(os.path.join(self.vault, self._note_rel(out))) as f:
            body = f.read()
        self.assertIn("echo_of_vault_event: %s" % event_id, body)
        self.assertNotIn("duplicate_of:", body)

        ids_mod = intake._load_sibling("bm_vault_ids")
        self.assertEqual(intake.corroboration_count(self.vault, ids_mod), 0,
                          "a re-ingested served answer must never count as corroboration")

    def test_a_genuinely_independent_note_with_similar_text_still_counts(self):
        indep_src = os.path.join(self.tmp, "zorbly payroll ledger seen again.txt")
        with open(indep_src, "w") as f:
            f.write("zorbly payroll ledger drift seen independently on a different day.\n")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", indep_src])
        self.assertEqual(code, 0, out + err)
        self.assertIn("duplicate-suspect", out)
        self.assertNotIn("echo-of-vault-answer", out)

        ids_mod = intake._load_sibling("bm_vault_ids")
        self.assertEqual(intake.corroboration_count(self.vault, ids_mod), 1,
                          "a genuinely independent overlapping note must still count")


class SelfEchoDetectionCalibration(unittest.TestCase):
    """Drives VB6-06's echo detector BACKWARDS in an isolated COPY of tools/, never
    this checkout: bm_vault_audit.py (the sole owner of the marker's shape, per its
    own module docstring) is removed from the copy, and the named assertion (a
    marked file whose event id is confirmed in the access log is classified
    echo-of-vault-answer, never duplicate-suspect) is proven to fail once the
    detector cannot run at all -- the exact MAJOR 3 degradation that must be named
    on stderr and in the note's own frontmatter rather than passed over silently.

    The marker used here is a REAL one (an actual recall run against a fixture
    vault, same as SelfEchoProvenance above), not a hand-assembled event id: MAJOR
    1's fix means a marker is only trusted once bm_vault_audit.has_event() confirms
    it in the log, so a fabricated id would only ever prove the forged-vault-marker
    path (covered separately by SelfEchoForgeryClassification), not this one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-echo-calib-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        os.makedirs(os.path.join(self.tmp, ".claude"))
        with open(os.path.join(self.vault, "40-Failures", "zorbly-payroll-ledger.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f680\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly payroll ledger drift\n\n"
                    "Root cause of the zorbly payroll ledger drift, written down once.\n")
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BROTHERMODE_ROOT"] = self.tmp
        self.env["BM_VAULT_ROOT"] = self.vault
        self.env["BM_FRESHNESS_ROOTS"] = self.tmp
        self.env["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness.sqlite3")
        self.env.pop("BM_IDENTITY", None)
        self.toolscopy = _copy_tools_to(os.path.join(self.tmp, "toolscopy"))
        self.intake_copy = os.path.join(self.toolscopy, "bm_vault_intake.py")
        self.audit_copy = os.path.join(self.toolscopy, "bm_vault_audit.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _marked_src(self):
        idx = subprocess.run([sys.executable, VAULT_TOOL, "index", "--vault", self.vault],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(idx.returncode, 0, idx.stdout.decode() + idx.stderr.decode())
        rec = subprocess.run([sys.executable, VAULT_TOOL, "recall", "--query",
                              "zorbly payroll ledger drift", "--limit", "5", "--fast"],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out = rec.stdout.decode("utf-8", "replace")
        self.assertEqual(rec.returncode, 0, out + rec.stderr.decode())
        marker_lines = [l for l in out.splitlines() if l.startswith("derived-from-vault: event=")]
        self.assertTrue(marker_lines, "fixture assumption: recall printed its own marker")
        src = os.path.join(self.tmp, "zorbly payroll ledger echo.txt")
        with open(src, "w") as f:
            f.write("zorbly payroll ledger drift, root cause below.\n" + marker_lines[-1] + "\n")
        return src

    @staticmethod
    def _note_rel(out):
        note_line = [l for l in out.splitlines() if l.startswith("ADMITTED")][0]
        return note_line.split("-> ")[1].split("  id=")[0]

    def test_with_the_detector_present_a_marked_file_is_never_duplicate_suspect(self):
        src = self._marked_src()
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("echo-of-vault-answer", out)
        self.assertNotIn("duplicate-suspect", out)

    def test_with_the_detector_removed_the_degradation_is_named_in_both_channels(self):
        src = self._marked_src()
        self.assertTrue(os.path.isfile(self.audit_copy),
                         "fixture assumption: detector module exists in the copy")
        os.remove(self.audit_copy)
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src], env=self.env,
                                   intake_path=self.intake_copy)
        self.assertEqual(code, 0, out + err)
        # The named test_with_the_detector_present assertion stops holding: with no
        # detector at all, the marker cannot even be read, so the file is scored for
        # overlap like any ordinary note and misclassified as a second, independent
        # source agreeing.
        self.assertNotIn("echo-of-vault-answer", out)
        self.assertIn("duplicate-suspect", out)
        # MAJOR 3: the degradation itself must never be silent. Named on stderr...
        self.assertIn("bm_vault_audit.py unavailable", err)
        # ...and recorded in the admitted note's own frontmatter.
        with open(os.path.join(self.vault, self._note_rel(out))) as f:
            body = f.read()
        self.assertIn("echo_detection: NO-DATA", body)


class SelfEchoForgeryClassification(unittest.TestCase):
    """MAJOR 1 fix: detect_marker() alone only proves a marker-SHAPED line is
    present, not that a real recall produced it, so intake now verifies the
    detected event id against bm_vault_audit.has_event() before trusting it.
    Both outcomes below are still classified, still admitted, still flagged, and
    still counted as independent duplicate-suspect corroboration -- the exact
    opposite of the pre-fix behaviour, where a hand-written hex32 line silently
    excluded a note from corroboration the same as a genuine echo would."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-forgery-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        os.makedirs(os.path.join(self.tmp, ".claude"))
        with open(os.path.join(self.vault, "40-Failures", "zorbly-payroll-ledger.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f681\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly payroll ledger drift\n\n"
                    "Root cause of the zorbly payroll ledger drift, written down once.\n")
        self.env = dict(os.environ)
        self.env["HOME"] = self.tmp
        self.env["BROTHERMODE_ROOT"] = self.tmp
        self.env["BM_VAULT_ROOT"] = self.vault
        self.env["BM_FRESHNESS_ROOTS"] = self.tmp
        self.env["BM_FRESHNESS_STATE"] = os.path.join(self.tmp, "freshness.sqlite3")
        self.env.pop("BM_IDENTITY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_marker(self):
        # A hand-written hex32 of the marker's exact shape, never produced by a
        # real recall and never appended to any audit log.
        audit = intake._load_sibling("bm_vault_audit")
        return audit.marker_line("ab" * 16)

    def test_forged_marker_with_a_readable_audit_stays_duplicate_suspect(self):
        # A real recall first, so this fixture's audit log exists and is
        # readable; the fabricated event id below is never one it wrote.
        idx = subprocess.run([sys.executable, VAULT_TOOL, "index", "--vault", self.vault],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(idx.returncode, 0, idx.stdout.decode() + idx.stderr.decode())
        rec = subprocess.run([sys.executable, VAULT_TOOL, "recall", "--query",
                              "zorbly payroll ledger drift", "--limit", "5", "--fast"],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(rec.returncode, 0, rec.stdout.decode() + rec.stderr.decode())

        src = os.path.join(self.tmp, "zorbly payroll ledger forged.txt")
        with open(src, "w") as f:
            f.write("zorbly payroll ledger drift, root cause below.\n" + self._fake_marker() + "\n")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("forged-vault-marker", out)
        self.assertIn("duplicate-suspect", out)
        self.assertNotIn("echo-of-vault-answer", out)

        ids_mod = intake._load_sibling("bm_vault_ids")
        self.assertEqual(intake.corroboration_count(self.vault, ids_mod), 1,
                          "a forged marker must be counted as independent, never excluded")

    def test_unverifiable_marker_when_the_audit_cannot_be_read_stays_duplicate_suspect(self):
        # No recall has ever run in this fixture: the audit log itself does not
        # exist, so the event id can be checked neither way.
        src = os.path.join(self.tmp, "zorbly payroll ledger unverifiable.txt")
        with open(src, "w") as f:
            f.write("zorbly payroll ledger drift, root cause below.\n" + self._fake_marker() + "\n")
        code, out, err = run_admit(["--vault", self.vault, "--source", "sys",
                                    "--by", "actor", src], env=self.env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("unverifiable-vault-marker", out)
        self.assertIn("duplicate-suspect", out)
        self.assertNotIn("echo-of-vault-answer", out)

        ids_mod = intake._load_sibling("bm_vault_ids")
        self.assertEqual(intake.corroboration_count(self.vault, ids_mod), 1,
                          "an unverifiable marker must be counted as independent, never excluded")


class CaptureVerbContract(unittest.TestCase):
    """VB6-09's own done_check, verbatim: a capture lands as candidate with
    provenance and expiry; a near-duplicate capture arrives linked to its
    sibling; an expired capture appears in the staleness census (that third
    leg is calibrated in test_bm_vault_staleness.py, the module that owns
    the census)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-capture-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_capture(self, args, env=None, input_bytes=None):
        p = subprocess.run([sys.executable, INTAKE, "capture"] + args, env=env,
                           input=input_bytes,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")

    def test_a_capture_lands_as_candidate_with_provenance_and_expiry(self):
        env = dict(os.environ)
        env["BM_SESSION_ID"] = "session-zorbly-1"
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "--row", "VB6-09",
             "--expiry-class", "scratch", "--today", "2026-08-30",
             "a quick scratch thought about the zorbly export"], env=env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("CAPTURED", out)
        inbox = os.path.join(self.vault, "00-Inbox")
        notes = [fn for fn in os.listdir(inbox) if fn.endswith(".md")]
        self.assertEqual(len(notes), 1, notes)
        with open(os.path.join(inbox, notes[0])) as f:
            body = f.read()
        self.assertIn("type: capture", body)
        self.assertIn("lifecycle: candidate", body)
        self.assertIn("captured_by: actor", body)
        self.assertIn("wbs_row: VB6-09", body)
        self.assertIn("session_context: session-zorbly-1", body)
        self.assertIn("expiry_class: scratch", body)
        self.assertIn("expiry_at: 2026-09-06", body)  # 2026-08-30 + 7 days

    def test_missing_row_and_session_are_named_no_data_not_invented(self):
        env = dict(os.environ)
        env.pop("BM_SESSION_ID", None)
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "a plain scratch thought"], env=env)
        self.assertEqual(code, 0, out + err)
        inbox = os.path.join(self.vault, "00-Inbox")
        notes = [fn for fn in os.listdir(inbox) if fn.endswith(".md")]
        with open(os.path.join(inbox, notes[0])) as f:
            body = f.read()
        self.assertIn("wbs_row: NO-DATA", body)
        self.assertIn("session_context: NO-DATA", body)
        self.assertIn("expiry_class: lesson-candidate", body)  # the stated default

    def test_stdin_text_is_accepted_when_no_trailing_words_given(self):
        code, out, err = self._run_capture(["--vault", self.vault, "--by", "actor"],
                                           input_bytes=b"captured from stdin instead")
        self.assertEqual(code, 0, out + err)

    def test_a_near_duplicate_capture_arrives_linked_to_its_sibling(self):
        existing_dir = os.path.join(self.vault, "40-Failures")
        os.makedirs(existing_dir)
        with open(os.path.join(existing_dir, "zorbly-export-timeout.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f699\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly export timeout\n\n"
                    "root cause of the zorbly export timeout.\n")
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "--title", "zorbly export timeout",
             "seen the zorbly export timeout again today"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("duplicate_of=n-00a1b2c3d4e5f699", out)
        inbox = os.path.join(self.vault, "00-Inbox")
        notes = [fn for fn in os.listdir(inbox) if fn.endswith(".md")]
        with open(os.path.join(inbox, notes[0])) as f:
            body = f.read()
        self.assertIn("duplicate_of: n-00a1b2c3d4e5f699", body)

    def test_an_unrelated_capture_arrives_unlinked(self):
        existing_dir = os.path.join(self.vault, "40-Failures")
        os.makedirs(existing_dir)
        with open(os.path.join(existing_dir, "zorbly-export-timeout.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f698\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly export timeout\n\n"
                    "root cause of the zorbly export timeout.\n")
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "--title", "an unrelated matter",
             "something with no overlap at all"])
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("duplicate_of=", out)

    def test_unknown_expiry_class_is_rejected_by_argparse(self):
        code, out, err = self._run_capture(["--vault", self.vault, "--by", "actor",
                                            "--expiry-class", "bogus", "text"])
        self.assertNotEqual(code, 0, out + err)

    def test_no_vault_is_no_data(self):
        code, out, err = self._run_capture(["--vault", "/nowhere/at/all",
                                            "--by", "actor", "text"])
        self.assertEqual(code, 2, out + err)
        self.assertIn("NO-DATA", err)


class CaptureHardGateRegression(unittest.TestCase):
    """MAJOR fix: cmd_capture used to write user text into 00-Inbox/ without
    ever running credential_hit or deny_list_hit, contradicting the module's
    own hard-rejection-before-write guarantee (see hard_gate's docstring,
    shared with admit). Same class-named-only, nothing-written contract as
    admit's own hard rejection, now proven for capture too."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-capture-gate-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_capture(self, args):
        p = subprocess.run([sys.executable, INTAKE, "capture"] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")

    def _inbox_notes(self):
        inbox = os.path.join(self.vault, "00-Inbox")
        if not os.path.isdir(inbox):
            return []
        return [fn for fn in os.listdir(inbox) if fn.endswith(".md")]

    def test_a_runtime_assembled_credential_shape_refuses_named_nothing_written(self):
        # Assembled at call time, same rule as FAKE_KEY above: never a
        # scannable literal in this file's own source.
        key = "AKIA" + "1234567890ABCDEF"
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor",
             "a note carrying %s is a live key" % key])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=credential-shape", err)
        self.assertNotIn(key, out + err, "the matched value must never be printed")
        self.assertEqual(self._inbox_notes(), [], "a rejected capture must write nothing")

    def test_a_deny_listed_term_refuses_named_nothing_written(self):
        deny_list = os.path.join(self.tmp, "deny.txt")
        with open(deny_list, "w", encoding="utf-8") as f:
            f.write(CJK_TERM + "\n")
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "--deny-list", deny_list,
             "a note carrying %s in the body" % CJK_TERM])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=deny-list-term", err)
        self.assertEqual(self._inbox_notes(), [], "a rejected capture must write nothing")

    def test_a_clean_capture_still_lands(self):
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "an ordinary clean capture"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self._inbox_notes()), 1, out + err)

    def test_the_title_alone_can_also_hard_reject(self):
        # The gate covers title AND text combined, per the module docstring;
        # a credential shape only in an explicit --title must still refuse.
        key = "AKIA" + "1234567890ABCDEF"
        code, out, err = self._run_capture(
            ["--vault", self.vault, "--by", "actor", "--title", key,
             "ordinary clean body text"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("class=credential-shape", err)
        self.assertEqual(self._inbox_notes(), [])


class CaptureHardGateCalibration(unittest.TestCase):
    """Drives the MAJOR fix BACKWARDS in an isolated COPY of tools/ (never
    this checkout, same _copy_tools_to rule as CaptureDuplicateLinkageCalibration
    above): bypass the hard_gate() call inside cmd_capture and watch the named
    regression test above fail against that copy; restore is implicit since
    only the copy is ever touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-capture-gate-calib-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.toolscopy = _copy_tools_to(os.path.join(self.tmp, "toolscopy"))
        self.intake_copy = os.path.join(self.toolscopy, "bm_vault_intake.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bypass_gate(self):
        with open(self.intake_copy, encoding="utf-8") as f:
            src = f.read()
        marker = "    ok, reject = hard_gate(title + \"\\n\" + text, args.deny_list)\n"
        self.assertIn(marker, src, "fixture assumption: the gate call line is present")
        bypassed = src.replace(marker, "    ok, reject = True, None\n")
        self.assertNotEqual(bypassed, src)
        with open(self.intake_copy, "w", encoding="utf-8") as f:
            f.write(bypassed)

    def _run(self):
        key = "AKIA" + "1234567890ABCDEF"
        return subprocess.run(
            [sys.executable, self.intake_copy, "capture", "--vault", self.vault,
             "--by", "actor", "a note carrying %s is a live key" % key],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_with_the_gate_present_the_credential_capture_refuses(self):
        p = self._run()
        self.assertEqual(p.returncode, 1, p.stdout.decode() + p.stderr.decode())
        self.assertIn("class=credential-shape", p.stderr.decode())

    def test_with_the_gate_bypassed_that_same_assertion_fails(self):
        self._bypass_gate()
        p = self._run()
        # The bypassed copy now admits the credential-bearing capture cleanly:
        # proof the gate call, not something else, is what makes the test above
        # pass. This is the "watch the named test fail" leg, driven directly
        # against the copy rather than asserting on the real suite's own runner.
        self.assertEqual(p.returncode, 0, p.stdout.decode() + p.stderr.decode())
        self.assertNotIn("class=credential-shape", p.stderr.decode())


class CaptureDuplicateLinkageCalibration(unittest.TestCase):
    """Drives VB6-09's dedup linkage BACKWARDS in an isolated COPY of tools/,
    never this checkout: bm_vault_distill.py (the sole owner of the overlap
    finder duplicate_suspects reuses, same rule admit already keeps) is
    removed from the copy, and the named linkage assertion is proven to fail
    once the finder cannot run. Capture still succeeds either way (never
    blocked by a missing finder, same shape as admit's own gate
    degradations); it just files blind instead of linked, and that
    degradation is named on stderr rather than passed over silently."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-capture-calib-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "40-Failures"))
        with open(os.path.join(self.vault, "40-Failures", "zorbly-export-timeout.md"), "w") as f:
            f.write("---\nid: n-00a1b2c3d4e5f6aa\ntype: finding\nstatus: open\n"
                    "created: 2026-08-01\n---\n\n# zorbly export timeout\n\n"
                    "root cause of the zorbly export timeout.\n")
        self.toolscopy = _copy_tools_to(os.path.join(self.tmp, "toolscopy"))
        self.intake_copy = os.path.join(self.toolscopy, "bm_vault_intake.py")
        self.distill_copy = os.path.join(self.toolscopy, "bm_vault_distill.py")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, intake_path):
        return subprocess.run(
            [sys.executable, intake_path, "capture", "--vault", self.vault,
             "--by", "actor", "--title", "zorbly export timeout",
             "seen the zorbly export timeout again today"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_with_the_finder_present_the_sibling_is_linked(self):
        p = self._run(self.intake_copy)
        out = p.stdout.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, out + p.stderr.decode())
        self.assertIn("duplicate_of=n-00a1b2c3d4e5f6aa", out)

    def test_with_the_finder_removed_that_same_assertion_fails(self):
        self.assertTrue(os.path.isfile(self.distill_copy),
                         "fixture assumption: finder module exists in the copy")
        os.remove(self.distill_copy)
        p = self._run(self.intake_copy)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        # Still succeeds (never blocked)...
        self.assertEqual(p.returncode, 0, out + err)
        # ...but the named linkage assertion stops holding: it files blind.
        self.assertNotIn("duplicate_of=", out)
        self.assertIn("bm_vault_distill.py unavailable", err)


class ProvenanceSourceSanitization(unittest.TestCase):
    """MINOR/defense-in-depth fix: --source lands verbatim in a raw
    'provenance_source: %s' frontmatter line (_build_note). A caller that
    passes (or forwards, as bm_vault_exchange does with an unauthenticated
    bundle_id) a value carrying a newline must never get to inject a new
    frontmatter key that way. bm_vault_exchange now also validates bundle_id
    itself before this is ever reached; this is the backstop for every other
    admission path."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-intake-provenance-sanitize-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_helper_strips_newlines_and_leading_dashes(self):
        self.assertEqual(intake._sanitize_frontmatter_scalar("exchange:xchg-abc\ninjected: x"),
                          "exchange:xchg-abc injected: x")
        self.assertEqual(intake._sanitize_frontmatter_scalar("--- injected"), "injected")
        self.assertEqual(intake._sanitize_frontmatter_scalar("plain-source"), "plain-source")

    def test_newline_bearing_source_injects_no_new_frontmatter_key(self):
        src = os.path.join(self.tmp, "note.txt")
        with open(src, "w") as f:
            f.write("an ordinary note body")
        malicious_source = "exchange:xchg-deadbeefcafebabe\nprovenance_actor: injected-actor"
        code, out, err = run_admit(["--vault", self.vault, "--source", malicious_source,
                                    "--by", "real-actor", src])
        self.assertEqual(code, 0, out + err)
        inbox = os.path.join(self.vault, "00-Inbox")
        note_names = [fn for fn in os.listdir(inbox) if fn.endswith(".md")]
        self.assertEqual(len(note_names), 1)
        with open(os.path.join(inbox, note_names[0])) as f:
            body = f.read()
        # The injected key must never appear as its own frontmatter line with
        # the attacker's value; it is folded, harmless, into the one
        # provenance_source line instead.
        self.assertNotIn("\nprovenance_actor: injected-actor\n", body)
        self.assertIn("provenance_actor: real-actor", body)
        self.assertIn("provenance_source: exchange:xchg-deadbeefcafebabe "
                       "provenance_actor: injected-actor", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

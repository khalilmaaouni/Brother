#!/usr/bin/env python3
"""Calibration for tools/bm_vault_exchange.py, WBS row VB8-02.

MEASURED ON THIS MACHINE: `age` and `age-keygen` are both absent from PATH
(command -v exits 1 for both). Every live-encryption test below therefore
runs through the module's own injected seam (age_encrypt/age_decrypt
replaced in process with a reversible fake), exactly as the module's own
docstring says a machine without age must do. A real round trip with a
throwaway age-keygen identity is also written, guarded to skip itself when
age is actually on PATH, so it runs unattended on a machine that has it.

The row's own done_check: export writes only ciphertext plus a plaintext-free
manifest into the relay directory (no note title or body text is ever found
there); import verifies the ciphertext sha256 against that manifest and
refuses a tampered bundle without decrypting it; a decrypted bundle's notes
land THROUGH bm_vault_intake.admit with provenance naming the sending bundle;
a literal age secret key passed as --identity is refused before anything
else runs; age absent is NO-DATA at exit 2 naming the install command.

No em or en dashes anywhere in this file.
"""
import base64
import contextlib
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
EXCHANGE = os.path.join(HERE, "bm_vault_exchange.py")

sys.path.insert(0, HERE)
import bm_vault_exchange as exch  # noqa: E402
import bm_vault_ids as ids_mod  # noqa: E402

AGE_PRESENT = bool(shutil.which("age") and shutil.which("age-keygen"))

MARKER = "ZQVAULTMARKER-" + uuid.uuid4().hex[:8]

# Assembled at runtime, same rule bm_vault_intake's own tests already follow:
# an aws-key-shaped literal never sits whole in source.
FAKE_KEY = "AKIA" + "1234567890ABCDEF"


def _export_fake_bundle(tmp, notes):
    """Exports `notes` through the fake age seam (age must already be
    monkeypatched by the caller's setUp) and returns (bundle_path,
    manifest_path, manifest_dict)."""
    vault, ids = make_vault(tmp, notes)
    relay = os.path.join(tmp, "relay")
    export_args = ExchangeArgs(vault=vault, recipient=["age1x"], out=relay,
                               notes=ids, since=None)
    rc = exch.cmd_export(export_args)
    assert rc == 0, "fixture setup: export must succeed"
    bundle = glob.glob(os.path.join(relay, "*.age"))[0]
    manifest_path = bundle[:-4] + ".manifest.json"
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return bundle, manifest_path, manifest


def _write_manifest(manifest_path, manifest):
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")


def run_cli(args, cwd=None):
    p = subprocess.run([sys.executable, EXCHANGE] + args,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def make_vault(tmp, notes):
    """A vault directory holding one .md file per (filename, title, body) in
    notes, each with a minted stable id, so select_notes(--notes ...) has
    something real to resolve against."""
    vault = os.path.join(tmp, "vault")
    os.makedirs(vault, exist_ok=True)
    ids = []
    for name, title, body in notes:
        nid = ids_mod.mint(set(ids))
        ids.append(nid)
        text = "---\nid: %s\n---\n\n# %s\n\n%s\n" % (nid, title, body)
        with open(os.path.join(vault, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return vault, ids


def fake_encrypt(recipients, data, out_path):
    """A reversible stand-in for age -r ... -o out_path: base64 with a fixed
    header, never the plaintext bytes verbatim. Proves the export/import
    plumbing without needing the real binary; NOT a real cipher."""
    with open(out_path, "wb") as fh:
        fh.write(b"FAKEAGE1" + base64.b64encode(data))
    return True, None


def fake_decrypt(identity_path, in_path, out_path):
    with open(in_path, "rb") as fh:
        raw = fh.read()
    if not raw.startswith(b"FAKEAGE1"):
        return False, "fake decrypt: not a fake-age payload"
    with open(out_path, "wb") as fh:
        fh.write(base64.b64decode(raw[8:]))
    return True, None


def broken_identity_encrypt(recipients, data, out_path):
    """A deliberately BROKEN stand-in that writes plaintext verbatim, used
    only to prove the ciphertext-only scan test is not vacuous: if export
    ever regressed to this, the scan test below must catch it."""
    with open(out_path, "wb") as fh:
        fh.write(data)
    return True, None


class ExchangeArgs(object):
    """A plain attribute bag matching argparse.Namespace's shape, since
    cmd_export/cmd_import read attributes, not a dict."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestAgeAbsentIsNoData(unittest.TestCase):
    """Measured: age is absent on this machine. The real (non-seam) code path
    must say so at exit 2 and name the install command, never silently skip
    or silently succeed."""

    def test_export_without_age_is_no_data(self):
        if AGE_PRESENT:
            self.skipTest("age is installed on this machine; this path is not reachable")
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("a.md", "A", "body a")])
            out = os.path.join(tmp, "relay")
            rc, stdout, stderr = run_cli(["export", "--vault", vault,
                                          "--recipient", "age1fakerecipient",
                                          "--out", out, "--notes", ids[0]])
            self.assertEqual(rc, 2, stderr)
            self.assertIn("brew install age", stderr)
            self.assertEqual(glob.glob(os.path.join(out, "*")), [])


class TestLiteralKeyRefusal(unittest.TestCase):
    """A key-shaped --identity value must be refused before age is ever
    invoked, so it never lands in argv history. The prefix is assembled at
    runtime, never a literal in this file."""

    def test_identity_looking_like_a_key_is_refused(self):
        fake_key = "AGE-SECRET-KEY-" + ("1" * 59)
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "vault")
            os.makedirs(vault)
            rc, stdout, stderr = run_cli(["import", "--vault", vault,
                                          "--identity", fake_key,
                                          "--bundle", os.path.join(tmp, "nope.age")])
            self.assertEqual(rc, 2, stderr)
            self.assertIn("looks like a literal age secret key", stderr)

    def test_helper_function_agrees(self):
        self.assertTrue(exch.looks_like_literal_age_key("AGE-SECRET-KEY-" + "x" * 10))
        self.assertFalse(exch.looks_like_literal_age_key("/tmp/identity.txt"))


class TestSelection(unittest.TestCase):
    def test_by_notes_resolves_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("a.md", "A", "x"), ("b.md", "B", "y")])
            selected, missing = exch.select_notes(vault, ids_mod, [ids[0]], None)
            self.assertEqual(selected, ["a.md"])
            self.assertEqual(missing, [])

    def test_unknown_id_is_reported_missing_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("a.md", "A", "x")])
            selected, missing = exch.select_notes(vault, ids_mod, ["n-0000000000000000"], None)
            self.assertEqual(selected, [])
            self.assertEqual(missing, ["n-0000000000000000"])

    def test_by_since_walks_the_vault(self):
        import datetime
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("a.md", "A", "x")])
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            selected, missing = exch.select_notes(vault, ids_mod, None,
                                                   exch._parse_date(yesterday))
            self.assertEqual(selected, ["a.md"])


class TestRoundTripAndCiphertextOnly(unittest.TestCase):
    """The seam-driven variants, since age is absent on this machine."""

    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        self.addCleanup(self._restore)

    def _restore(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt

    def test_round_trip_lands_in_inbox_with_provenance(self):
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        with tempfile.TemporaryDirectory() as tmp:
            src_vault, ids = make_vault(tmp, [("note.md", MARKER + " title", MARKER + " body")])
            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            relay = os.path.join(tmp, "relay")

            export_args = ExchangeArgs(vault=src_vault, recipient=["age1recipientfake"],
                                       out=relay, notes=[ids[0]], since=None)
            rc = exch.cmd_export(export_args)
            self.assertEqual(rc, 0)

            bundle = glob.glob(os.path.join(relay, "*.age"))
            self.assertEqual(len(bundle), 1)
            identity = os.path.join(tmp, "identity.txt")
            with open(identity, "w") as fh:
                fh.write("fake identity, never read by the fake decryptor\n")

            import_args = ExchangeArgs(vault=dst_vault, identity=identity, bundle=bundle[0],
                                       by="test-actor", restricted=False, deny_list=None)
            rc = exch.cmd_import(import_args)
            self.assertEqual(rc, 0)

            inbox = os.path.join(dst_vault, "00-Inbox")
            admitted = [f for f in os.listdir(inbox) if f.endswith(".md")]
            self.assertEqual(len(admitted), 1)
            with open(os.path.join(inbox, admitted[0]), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("provenance_source: exchange:", text)
            self.assertIn(MARKER, text)

    def test_relay_dir_carries_no_plaintext_marker(self):
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("note.md", MARKER + " secret title", "body")])
            relay = os.path.join(tmp, "relay")
            export_args = ExchangeArgs(vault=vault, recipient=["age1x"], out=relay,
                                       notes=[ids[0]], since=None)
            self.assertEqual(exch.cmd_export(export_args), 0)

            for name in os.listdir(relay):
                with open(os.path.join(relay, name), "rb") as fh:
                    blob = fh.read()
                self.assertNotIn(MARKER.encode("ascii"), blob,
                                 "plaintext marker leaked into relay file %s" % name)

    def test_calibration_a_broken_encryptor_would_be_caught(self):
        """Driven backwards: if export ever used an encryptor that writes
        plaintext (the bug this test exists to catch), the scan above must
        find the marker. Proves the scan test is not vacuously passing."""
        exch.age_encrypt = broken_identity_encrypt
        exch.age_decrypt = fake_decrypt
        with tempfile.TemporaryDirectory() as tmp:
            vault, ids = make_vault(tmp, [("note.md", MARKER + " leaked title", "body")])
            relay = os.path.join(tmp, "relay")
            export_args = ExchangeArgs(vault=vault, recipient=["age1x"], out=relay,
                                       notes=[ids[0]], since=None)
            self.assertEqual(exch.cmd_export(export_args), 0)
            found = False
            for name in os.listdir(relay):
                with open(os.path.join(relay, name), "rb") as fh:
                    if MARKER.encode("ascii") in fh.read():
                        found = True
            self.assertTrue(found, "calibration failed: the broken encryptor should have "
                                    "leaked the marker, which is exactly what the real test "
                                    "above must never observe")


class TestTamperRefuses(unittest.TestCase):
    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        self.addCleanup(self._restore)

    def _restore(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt

    def _export_bundle(self, tmp):
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        vault, ids = make_vault(tmp, [("note.md", "T", "b")])
        relay = os.path.join(tmp, "relay")
        export_args = ExchangeArgs(vault=vault, recipient=["age1x"], out=relay,
                                   notes=[ids[0]], since=None)
        self.assertEqual(exch.cmd_export(export_args), 0)
        return glob.glob(os.path.join(relay, "*.age"))[0]

    def test_flipped_byte_refuses_and_imports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._export_bundle(tmp)
            with open(bundle, "r+b") as fh:
                fh.seek(0)
                b = fh.read(1)
                fh.seek(0)
                fh.write(bytes([b[0] ^ 0xFF]))

            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            identity = os.path.join(tmp, "identity.txt")
            with open(identity, "w") as fh:
                fh.write("x\n")
            import_args = ExchangeArgs(vault=dst_vault, identity=identity, bundle=bundle,
                                       by=None, restricted=False, deny_list=None)
            rc = exch.cmd_import(import_args)
            self.assertEqual(rc, 1)
            inbox = os.path.join(dst_vault, "00-Inbox")
            self.assertFalse(os.path.isdir(inbox) and os.listdir(inbox),
                             "a tampered bundle must never admit anything")

    def test_calibration_the_check_itself_distinguishes_match_from_mismatch(self):
        """Driven backwards at the function level: the sha256 comparison this
        refusal rests on must actually differ between an untouched file and a
        tampered one, or the refusal above would be catching nothing real."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._export_bundle(tmp)
            manifest_path = bundle[:-4] + ".manifest.json"
            with open(manifest_path, encoding="utf-8") as fh:
                expected = json.load(fh)["sha256_ciphertext"]
            self.assertEqual(exch._sha256_file(bundle), expected)
            with open(bundle, "ab") as fh:
                fh.write(b"\x00")
            self.assertNotEqual(exch._sha256_file(bundle), expected)


class TestMalformedBundleIdRefused(unittest.TestCase):
    """MAJOR fix: bundle_id flows straight into a raw provenance_source
    frontmatter line at admit time. A newline-bearing bundle_id must never
    reach that line: it is refused, class named, before any decrypt."""

    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        self.addCleanup(self._restore)

    def _restore(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt

    def test_newline_bearing_bundle_id_refuses_named_nothing_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, manifest_path, manifest = _export_fake_bundle(
                tmp, [("a.md", "A", "body")])
            injected = manifest["bundle_id"] + "\nprovenance_actor: injected-actor"
            manifest["bundle_id"] = injected
            _write_manifest(manifest_path, manifest)

            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            identity = os.path.join(tmp, "identity.txt")
            with open(identity, "w") as fh:
                fh.write("x\n")
            import_args = ExchangeArgs(vault=dst_vault, identity=identity, bundle=bundle,
                                       by="tester", restricted=False, deny_list=None)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = exch.cmd_import(import_args)
            self.assertEqual(rc, 1)
            self.assertIn("class=malformed-manifest", buf.getvalue())
            inbox = os.path.join(dst_vault, "00-Inbox")
            self.assertFalse(os.path.isdir(inbox) and os.listdir(inbox),
                             "a malformed bundle_id must never admit anything")

    def test_calibration_the_regex_actually_discriminates(self):
        """Driven backwards: a real minted bundle_id must match, and the same
        id with an injected newline must not, or the refusal above would be
        catching nothing real."""
        with tempfile.TemporaryDirectory() as tmp:
            _bundle, _manifest_path, manifest = _export_fake_bundle(
                tmp, [("a.md", "A", "body")])
            real_id = manifest["bundle_id"]
            self.assertRegex(real_id, exch.BUNDLE_ID_RE)
            self.assertNotRegex(real_id + "\nx: y", exch.BUNDLE_ID_RE)
            # A bare trailing newline is the classic Python `$`-anchor trap
            # (it matches just before a trailing newline, not only at the
            # true end of string); BUNDLE_ID_RE uses \Z specifically to close
            # that hole, so this must still refuse.
            self.assertNotRegex(real_id + "\n", exch.BUNDLE_ID_RE)


class TestManifestTamperRefused(unittest.TestCase):
    """MAJOR fix: sha256_ciphertext alone only authenticates the ciphertext
    bytes, leaving bundle_id/created_at/count free to be edited without
    tripping it. sha256_manifest folds those fields in."""

    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        self.addCleanup(self._restore)

    def _restore(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt

    def test_tampered_manifest_field_refuses_named_nothing_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, manifest_path, manifest = _export_fake_bundle(
                tmp, [("a.md", "A", "body")])
            # Edit count without touching sha256_manifest or sha256_ciphertext,
            # exactly the edit the old, ciphertext-only hash could not catch.
            manifest["count"] = manifest["count"] + 41
            _write_manifest(manifest_path, manifest)

            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            identity = os.path.join(tmp, "identity.txt")
            with open(identity, "w") as fh:
                fh.write("x\n")
            import_args = ExchangeArgs(vault=dst_vault, identity=identity, bundle=bundle,
                                       by="tester", restricted=False, deny_list=None)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                rc = exch.cmd_import(import_args)
            self.assertEqual(rc, 1)
            self.assertIn("class=manifest-tamper", buf.getvalue())
            inbox = os.path.join(dst_vault, "00-Inbox")
            self.assertFalse(os.path.isdir(inbox) and os.listdir(inbox),
                             "a tampered manifest must never admit anything")

    def test_calibration_the_hash_actually_depends_on_every_field(self):
        """Driven backwards at the function level: the manifest hash must
        differ once any covered field changes, or the refusal above would be
        catching nothing real."""
        with tempfile.TemporaryDirectory() as tmp:
            _bundle, _manifest_path, manifest = _export_fake_bundle(
                tmp, [("a.md", "A", "body")])
            before = exch._manifest_integrity_hash(manifest)
            self.assertEqual(before, manifest["sha256_manifest"])
            tampered = dict(manifest)
            tampered["count"] = tampered["count"] + 1
            self.assertNotEqual(exch._manifest_integrity_hash(tampered), before)


class TestTarEscapeRefused(unittest.TestCase):
    """MAJOR fix: previously-untested control. A crafted pack whose member
    escapes the extraction directory (via '..' or an absolute path) must
    refuse and write nothing outside that directory."""

    def _malicious_tar(self, name, payload=b"pwned"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        buf.seek(0)
        return buf

    def test_dotdot_member_refused_writes_nothing_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "extract")
            os.makedirs(dest)
            buf = self._malicious_tar("../escaped-dotdot.txt")
            with tarfile.open(fileobj=buf, mode="r") as tar:
                with self.assertRaises(ValueError):
                    exch._safe_extract(tar, dest)
            self.assertFalse(os.path.exists(os.path.join(tmp, "escaped-dotdot.txt")),
                              "the canary parent directory must never receive the file")

    def test_absolute_path_member_refused_writes_nothing_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "extract")
            os.makedirs(dest)
            outside_target = os.path.join(tmp, "escaped-abs.txt")
            buf = self._malicious_tar(outside_target)
            with tarfile.open(fileobj=buf, mode="r") as tar:
                with self.assertRaises(ValueError):
                    exch._safe_extract(tar, dest)
            self.assertFalse(os.path.exists(outside_target),
                              "the canary parent directory must never receive the file")

    def test_calibration_a_neutered_guard_would_have_let_this_through(self):
        """Driven backwards in an isolated COPY of bm_vault_exchange.py, never
        this checkout: the two escape-refusal checks inside _safe_extract are
        stripped out of the copy, and the exact assertions the two tests above
        rely on (nothing written outside dest) are proven to go false once the
        guard cannot run. This is what proves those tests are not vacuous."""
        with tempfile.TemporaryDirectory() as tmp:
            src = exch.__file__
            with open(src, encoding="utf-8") as fh:
                original = fh.read()
            guarded = ('        if os.path.isabs(name) or name.startswith(".." ) '
                       'or "/../" in ("/" + name):\n'
                       '            raise ValueError("REFUSE: bundle member %r escapes '
                       'the extraction directory" % name)\n'
                       '        target = os.path.normpath(os.path.join(dest, name))\n'
                       '        if not (target == dest or target.startswith(dest + os.sep)):\n'
                       '            raise ValueError("REFUSE: bundle member %r escapes '
                       'the extraction directory" % name)\n')
            self.assertIn(guarded, original,
                           "fixture assumption: the guard text this calibration strips "
                           "must match the real source verbatim")
            neutered_source = original.replace(
                guarded, '        target = os.path.normpath(os.path.join(dest, name))\n')
            copy_path = os.path.join(tmp, "bm_vault_exchange_neutered.py")
            with open(copy_path, "w", encoding="utf-8") as fh:
                fh.write(neutered_source)

            import importlib.util
            spec = importlib.util.spec_from_file_location("bm_vault_exchange_neutered", copy_path)
            neutered = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(neutered)

            dest = os.path.join(tmp, "extract")
            os.makedirs(dest)
            outside_target = os.path.join(tmp, "escaped-neutered.txt")
            payload = b"pwned"
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                info = tarfile.TarInfo(name=outside_target)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r") as tar:
                neutered._safe_extract(tar, dest)  # no ValueError this time
            self.assertTrue(os.path.exists(outside_target),
                             "calibration failed: the neutered guard should have let "
                             "the escape through, which is exactly what the real guard "
                             "above must never allow")


class TestCredentialShapeRefusedThroughImport(unittest.TestCase):
    """MAJOR2: exchange-to-intake credential-gate test. A bundle note carrying
    a runtime-assembled credential shape must be REFUSED by intake through
    the import path, never a second, weaker door."""

    def setUp(self):
        self._real_encrypt = exch.age_encrypt
        self._real_decrypt = exch.age_decrypt
        exch.age_encrypt = fake_encrypt
        exch.age_decrypt = fake_decrypt
        self.addCleanup(self._restore)

    def _restore(self):
        exch.age_encrypt = self._real_encrypt
        exch.age_decrypt = self._real_decrypt

    def test_credential_bearing_note_refused_nothing_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _manifest_path, _manifest = _export_fake_bundle(
                tmp, [("creds.md", "Creds", FAKE_KEY + " is a live key")])
            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            identity = os.path.join(tmp, "identity.txt")
            with open(identity, "w") as fh:
                fh.write("x\n")
            import_args = ExchangeArgs(vault=dst_vault, identity=identity, bundle=bundle,
                                       by="tester", restricted=False, deny_list=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = exch.cmd_import(import_args)
            self.assertEqual(rc, 1)
            self.assertIn("class=credential-shape", out.getvalue())
            self.assertNotIn(FAKE_KEY, out.getvalue())
            inbox = os.path.join(dst_vault, "00-Inbox")
            self.assertFalse(os.path.isdir(inbox) and os.listdir(inbox),
                             "a credential-bearing note must never be admitted")


@unittest.skipUnless(AGE_PRESENT, "age/age-keygen not on PATH on this machine")
class TestRealAgeRoundTrip(unittest.TestCase):
    """Runs only on a machine that actually has age installed. Generates a
    throwaway keypair with age-keygen, never touches a real vault or key."""

    def test_real_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = os.path.join(tmp, "identity.txt")
            p = subprocess.run(["age-keygen", "-o", identity],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr.decode())
            pub = None
            with open(identity, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("# public key:"):
                        pub = line.split(":", 1)[1].strip()
            self.assertIsNotNone(pub)

            vault, ids = make_vault(tmp, [("note.md", "Real", "content")])
            dst_vault = os.path.join(tmp, "dst-vault")
            os.makedirs(dst_vault)
            relay = os.path.join(tmp, "relay")
            rc, out, err = run_cli(["export", "--vault", vault, "--recipient", pub,
                                    "--out", relay, "--notes", ids[0]])
            self.assertEqual(rc, 0, err)
            bundle = glob.glob(os.path.join(relay, "*.age"))[0]
            rc, out, err = run_cli(["import", "--vault", dst_vault, "--identity", identity,
                                    "--bundle", bundle])
            self.assertEqual(rc, 0, err)
            inbox = os.path.join(dst_vault, "00-Inbox")
            self.assertEqual(len([f for f in os.listdir(inbox) if f.endswith(".md")]), 1)


if __name__ == "__main__":
    unittest.main()

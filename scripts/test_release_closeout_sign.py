#!/usr/bin/env python3
"""test_release_closeout_sign: row S5, the tag signature leg of gate X7
(`git tag -v <tag>` run inside the published tag's own clone).

WHAT IS TESTED HERE. tag_signature_verified() must never call an unsigned
tag a FAIL: S5 is founder gated, the signing key is his alone, so a tag
with no signature at all is NO-DATA. FAIL is reserved for a signature that
IS present but does not verify, which is the only shape that means
something actually went wrong. The unsigned and NO-DATA cases run on any
machine; the PASS and FAIL cases need a real gpg binary and skip, naming
why, when this machine has none.

Every key made here is thrown away: generated inside a temp GNUPGHOME for
this test alone, imported into no real keyring, never the founder's, and
deleted with the temp directory that holds it.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import release_closeout as rc  # noqa: E402

GPG_BIN = shutil.which("gpg") or shutil.which("gpg2")

#: An unattended key generation batch: no passphrase (a throwaway key held
#: only inside a throwaway GNUPGHOME needs none), expires never (the test
#: run is the whole of its lifetime).
GPG_BATCH_KEY = """%no-protection
Key-Type: RSA
Key-Length: 2048
Name-Real: Brother Test Signer
Name-Email: signer@example.invalid
Expire-Date: 0
%commit
"""


def _gpg_env(gnupghome):
    env = dict(os.environ)
    env["GNUPGHOME"] = gnupghome
    return env


def _make_throwaway_key(gnupghome):
    """Generates one throwaway gpg secret key inside `gnupghome` and
    returns its key id. GNUPGHOME is a fresh temp directory made only for
    this test; gpg reads and writes nothing outside it."""
    os.makedirs(gnupghome, exist_ok=True)
    os.chmod(gnupghome, 0o700)
    env = _gpg_env(gnupghome)
    gen = subprocess.run([GPG_BIN, "--batch", "--gen-key"],
                         input=GPG_BATCH_KEY, capture_output=True,
                         text=True, env=env, timeout=120)
    if gen.returncode != 0:
        raise RuntimeError("gpg --gen-key failed: %s" % gen.stderr)
    listing = subprocess.run(
        [GPG_BIN, "--list-secret-keys", "--with-colons"],
        capture_output=True, text=True, env=env, timeout=60)
    for line in listing.stdout.splitlines():
        if line.startswith("sec:"):
            return line.split(":")[4]
    raise RuntimeError("no secret key found after gen-key: %s" %
                       listing.stdout)


def _repo_with_commit(root):
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "Test"],
                   check=True)
    subprocess.run(["git", "-C", root, "config", "user.email",
                    "test@example.invalid"], check=True)
    # THE FIXTURE PINS ITS OWN SIGNING, and this is not decoration. On a
    # machine whose global config sets tag.gpgSign true (this one, measured
    # 2026-09-05), the "unsigned" tag below is SIGNED, so the unsigned leg
    # read PASS instead of NO-DATA and the lightweight leg died outright
    # with "fatal: no tag message?" because gpgSign forces an annotation.
    # Local config wins over global, so these two fixtures mean what their
    # names say on any machine, not only on one without a signing key.
    for key in ("tag.gpgSign", "commit.gpgsign"):
        subprocess.run(["git", "-C", root, "config", key, "false"],
                       check=True)
    with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("hi\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"],
                   check=True)


def _gate(work):
    return (rc.Gate("X7", "public-artifact", "the published tag"),
            rc.Evidence(os.path.join(work, "evidence")))


class TheTagSignatureLegNeverFailsOnMerelyUnsigned(unittest.TestCase):
    """Row S5: NO-DATA is never a pass, but an absent signature is never a
    FAIL either. Runs on any machine, gpg or none."""

    def test_an_unsigned_annotated_tag_is_no_data_naming_s5(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = os.path.join(work, "tag")
            _repo_with_commit(checkout)
            subprocess.run(["git", "-C", checkout, "tag", "-a", "v9.9.9",
                             "-m", "test"], check=True)
            gate, ev = _gate(work)
            verdict, why = rc.tag_signature_verified(gate, ev, checkout,
                                                      "v9.9.9")
            self.assertEqual(verdict, "NO-DATA", why)
            self.assertIn("S5, founder", why)

    def test_a_lightweight_tag_is_also_no_data_never_a_fail(self):
        # git tag -v on a lightweight tag (no annotation object at all)
        # refuses the same way ("not a valid tag object"): still NO-DATA.
        with tempfile.TemporaryDirectory() as work:
            checkout = os.path.join(work, "tag")
            _repo_with_commit(checkout)
            subprocess.run(["git", "-C", checkout, "tag", "v9.9.9"],
                           check=True)
            gate, ev = _gate(work)
            verdict, why = rc.tag_signature_verified(gate, ev, checkout,
                                                      "v9.9.9")
            self.assertEqual(verdict, "NO-DATA", why)


@unittest.skipUnless(
    GPG_BIN, "NO-DATA: no gpg or gpg2 on PATH on this machine, so the "
             "signed and bad-signature legs of row S5 cannot run here")
class TheTagSignatureLegReadsARealSignature(unittest.TestCase):
    """These two need a real gpg binary; skip entirely, naming why, on a
    machine that has none (measured 2026-09-05: absent on this one)."""

    def test_a_good_signature_is_a_pass(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = os.path.join(work, "tag")
            gnupghome = os.path.join(work, "gnupg")
            _repo_with_commit(checkout)
            key_id = _make_throwaway_key(gnupghome)
            env = _gpg_env(gnupghome)
            subprocess.run(["git", "-C", checkout, "config",
                            "user.signingkey", key_id], check=True)
            tag = subprocess.run(
                ["git", "-C", checkout, "tag", "-s", "v9.9.9", "-m", "test"],
                capture_output=True, text=True, env=env)
            self.assertEqual(tag.returncode, 0, tag.stderr)
            gate, ev = _gate(work)
            with mock.patch.dict(os.environ, {"GNUPGHOME": gnupghome}):
                verdict, why = rc.tag_signature_verified(gate, ev, checkout,
                                                          "v9.9.9")
            self.assertEqual(verdict, "PASS", why)

    def test_a_tampered_signature_is_a_fail_not_no_data(self):
        with tempfile.TemporaryDirectory() as work:
            checkout = os.path.join(work, "tag")
            gnupghome = os.path.join(work, "gnupg")
            _repo_with_commit(checkout)
            key_id = _make_throwaway_key(gnupghome)
            env = _gpg_env(gnupghome)
            subprocess.run(["git", "-C", checkout, "config",
                            "user.signingkey", key_id], check=True)
            tag = subprocess.run(
                ["git", "-C", checkout, "tag", "-s", "v9.9.9", "-m", "test"],
                capture_output=True, text=True, env=env)
            self.assertEqual(tag.returncode, 0, tag.stderr)
            # Corrupt one byte inside the base64 signature body, leaving
            # the PGP armor structure intact so gpg still attempts to
            # verify rather than refusing to parse it at all.
            obj = subprocess.run(
                ["git", "-C", checkout, "cat-file", "tag", "v9.9.9"],
                capture_output=True, text=True, check=True).stdout
            lines = obj.splitlines()
            body_idx = None
            for i, line in enumerate(lines):
                if i > 0 and lines[i - 1].startswith(
                        "-----BEGIN PGP SIGNATURE-----") and line.strip():
                    body_idx = i
                    break
            self.assertIsNotNone(body_idx, obj)
            first = lines[body_idx][0]
            lines[body_idx] = ("A" if first != "A" else "B") + \
                lines[body_idx][1:]
            new_obj = "\n".join(lines) + "\n"
            write = subprocess.run(
                ["git", "-C", checkout, "hash-object", "-t", "tag", "-w",
                 "--stdin"], input=new_obj, capture_output=True, text=True)
            self.assertEqual(write.returncode, 0, write.stderr)
            new_sha = write.stdout.strip()
            subprocess.run(["git", "-C", checkout, "update-ref",
                            "refs/tags/v9.9.9", new_sha], check=True)
            gate, ev = _gate(work)
            with mock.patch.dict(os.environ, {"GNUPGHOME": gnupghome}):
                verdict, why = rc.tag_signature_verified(gate, ev, checkout,
                                                          "v9.9.9")
            self.assertEqual(verdict, "FAIL", why)


if __name__ == "__main__":
    unittest.main()

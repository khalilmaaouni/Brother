#!/usr/bin/env python3
"""Tests for scripts/receipt_attest.py.

The whole test class is skipped when ssh-keygen is not on PATH. All keys
and artifacts are created under a throwaway temporary directory.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "receipt_attest.py")


def _run(args, input_bytes=None):
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _have_ssh_keygen():
    return shutil.which("ssh-keygen") is not None


@unittest.skipUnless(
    _have_ssh_keygen(),
    "ssh-keygen is not on PATH; cannot exercise the SSH signing convention",
)
class ReceiptAttestTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brother-receipt-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.key = os.path.join(self.tmp, "k")
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "test",
                "-f",
                self.key,
            ],
            check=True,
        )
        self.key2 = os.path.join(self.tmp, "k2")
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "test2",
                "-f",
                self.key2,
            ],
            check=True,
        )

        with open(self.key + ".pub", "r", encoding="utf-8") as f:
            pub_line = f.read().strip()
        parts = pub_line.split()
        self.keytype = parts[0]
        self.keydata = parts[1]

        self.allowed = os.path.join(self.tmp, "allowed_signers")
        with open(self.allowed, "w", encoding="utf-8") as f:
            f.write("test@example %s %s\n" % (self.keytype, self.keydata))
        self.identity = "test@example"

    # Helpers --------------------------------------------------------------

    def _artifact(self, name="artifact.bin", content=b"hello world"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def _write_claim(self, path, verdict="PASS", claim_text=None):
        if claim_text is None:
            claim_text = "the build is reproducible"
        claim = {
            "claim": claim_text,
            "verdict": verdict,
            "obligation": "REQUIRED_FOR_MERGE",
            "evidence": [
                {
                    "check": "rebuild",
                    "family": "build",
                    "independence": "cross-derived",
                    "discriminated": "yes",
                    "command": "make build",
                    "exit_code": 0,
                }
            ],
            "source": {
                "repository": "https://github.com/khalilmaaouni/Brother",
                "commit": "0" * 40,
            },
            "produced_by": {
                "tool": "brother receipt_attest",
                "version": "0.1.0",
            },
            "decided_at": "2024-01-01T00:00:00Z",
            "related_predicates": [
                "https://in-toto.io/attestation/human-review/v0.1"
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(claim, f)
        return claim

    def _emit(self, claim_path, out_path, artifact_paths, previous=None):
        args = ["emit", "--claim-file", claim_path, "--out", out_path]
        for a in artifact_paths:
            args += ["--artifact", a]
        if previous is not None:
            args += ["--previous", previous]
        return _run(args)

    def _sign(self, statement_path, out_path, key=None):
        if key is None:
            key = self.key
        return _run(
            [
                "sign",
                "--statement",
                statement_path,
                "--key",
                key,
                "--pubkey",
                key + ".pub",
                "--out",
                out_path,
            ]
        )

    def _verify(
        self,
        envelope_path,
        artifact_paths=None,
        previous=None,
        allowed_signers=None,
        identity=None,
    ):
        args = [
            "verify",
            "--envelope",
            envelope_path,
            "--allowed-signers",
            allowed_signers if allowed_signers else self.allowed,
            "--identity",
            identity if identity else self.identity,
        ]
        if artifact_paths:
            for a in artifact_paths:
                args += ["--artifact", a]
        if previous is not None:
            args += ["--previous", previous]
        return _run(args)

    def _full_pipeline(self, verdict="PASS"):
        art = self._artifact()
        claim = os.path.join(self.tmp, "claim.json")
        self._write_claim(claim, verdict=verdict)
        stmt = os.path.join(self.tmp, "statement.json")
        r = self._emit(claim, stmt, [art])
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        env = os.path.join(self.tmp, "envelope.json")
        r = self._sign(stmt, env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return art, stmt, env

    # Tests ----------------------------------------------------------------

    def test_emit_sign_verify_pass(self):
        art, stmt, env = self._full_pipeline(verdict="PASS")
        r = self._verify(env, artifact_paths=[art])
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode("utf-8", "replace"))
        self.assertIn("PASS signature", out)
        self.assertIn("PASS artifact", out)
        self.assertIn("verify: PASS", out)
        self.assertIn("claim verdict: PASS", out)

    def test_flipped_artifact_fails(self):
        art, stmt, env = self._full_pipeline()
        with open(art, "r+b") as f:
            f.seek(0)
            first = f.read(1)
            f.seek(0)
            f.write(b"\x00" if first != b"\x00" else b"\x01")
        r = self._verify(env, artifact_paths=[art])
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("FAIL artifact", out)
        self.assertIn("artifact", out)
        self.assertIn("verify: FAIL", out)
        # The signature itself must still be valid.
        self.assertIn("PASS signature", out)

    def test_edited_payload_breaks_signature(self):
        art, stmt, env = self._full_pipeline(verdict="FAIL")
        with open(env, "r", encoding="utf-8") as f:
            envelope = json.load(f)
        payload_bytes = base64.b64decode(envelope["payload"])
        statement = json.loads(payload_bytes.decode("utf-8"))
        self.assertEqual(statement["predicate"]["verdict"], "FAIL")
        statement["predicate"]["verdict"] = "PASS"
        new_payload = json.dumps(
            statement, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        envelope["payload"] = base64.b64encode(new_payload).decode("ascii")
        # The signature is intentionally left untouched.
        with open(env, "w", encoding="utf-8") as f:
            json.dump(envelope, f)

        r = self._verify(env, artifact_paths=[art])
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("FAIL signature", out)
        self.assertIn("verify: FAIL", out)

    def test_signature_by_unknown_key_fails(self):
        art = self._artifact()
        claim = os.path.join(self.tmp, "claim.json")
        self._write_claim(claim)
        stmt = os.path.join(self.tmp, "statement.json")
        r = self._emit(claim, stmt, [art])
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        env = os.path.join(self.tmp, "envelope.json")
        r = self._sign(stmt, env, key=self.key2)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        # Verify against allowed_signers that only knows key1.
        r = self._verify(env, artifact_paths=[art])
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("FAIL signature", out)
        self.assertIn("verify: FAIL", out)

    def test_chain_of_two_statements(self):
        art = self._artifact(content=b"chained payload")
        claim1 = os.path.join(self.tmp, "claim1.json")
        self._write_claim(claim1, claim_text="first step")
        stmt1 = os.path.join(self.tmp, "statement1.json")
        r = self._emit(claim1, stmt1, [art])
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

        claim2 = os.path.join(self.tmp, "claim2.json")
        self._write_claim(claim2, claim_text="second step")
        stmt2 = os.path.join(self.tmp, "statement2.json")
        r = self._emit(claim2, stmt2, [art], previous=stmt1)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

        env = os.path.join(self.tmp, "envelope2.json")
        r = self._sign(stmt2, env)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

        r = self._verify(env, artifact_paths=[art], previous=stmt1)
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("PASS previous", out)
        self.assertIn("verify: PASS", out)

        other = os.path.join(self.tmp, "other.json")
        with open(other, "w", encoding="utf-8") as f:
            f.write("{}")
        r = self._verify(env, artifact_paths=[art], previous=other)
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 1, out)
        self.assertIn("FAIL previous", out)
        self.assertIn("verify: FAIL", out)

    def test_no_data_verdict_still_verifies(self):
        art, stmt, env = self._full_pipeline(verdict="NO-DATA")
        r = self._verify(env, artifact_paths=[art])
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("verify: PASS", out)
        self.assertIn(
            "claim verdict: NO-DATA "
            "(the signature proves who said it, not that it passed)",
            out,
        )

    def test_invalid_enum_fails_emit(self):
        art = self._artifact()
        claim_path = os.path.join(self.tmp, "claim.json")
        claim = self._write_claim(claim_path)
        claim["verdict"] = "MAYBE"
        with open(claim_path, "w", encoding="utf-8") as f:
            json.dump(claim, f)
        stmt = os.path.join(self.tmp, "statement.json")
        r = self._emit(claim_path, stmt, [art])
        self.assertEqual(r.returncode, 1)
        err = r.stderr.decode("utf-8", "replace")
        self.assertIn("verdict", err)
        self.assertFalse(os.path.exists(stmt))

    def test_garbage_envelope_exits_2(self):
        env = os.path.join(self.tmp, "bad.json")
        with open(env, "w", encoding="utf-8") as f:
            f.write("this is not a DSSE envelope")
        r = self._verify(env)
        self.assertEqual(r.returncode, 2, r.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()

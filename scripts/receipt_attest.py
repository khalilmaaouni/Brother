#!/usr/bin/env python3
"""Brother verification receipt attestation tool (experimental v0.1).

Subcommands:

    emit    build an in-toto Statement v1 from a claim JSON file
    sign    wrap a statement in a DSSE envelope with an ssh-keygen signature
    verify  verify a signed envelope offline and check artifact digests

See docs/reference/attestation.md for the exact wire format. This tool
never touches the network and cleans up every temporary file it creates.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import evidence_obligation


PREDICATE_TYPE = (
    "https://github.com/khalilmaaouni/Brother/blob/main/docs/reference/"
    "attestation.md#verification-v0.1"
)
INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
SSH_NAMESPACE = "brother-receipt-v1"
SSH_TOOL_NAME = "brother receipt_attest"
DEFAULT_RELATED = ["https://in-toto.io/attestation/human-review/v0.1"]

# WBS-20.02 (docs/decisions/evidence-vocabulary-2026-09-13.json, EV-3):
# defined once in evidence_obligation.py, the module actually wired to the
# enforced merge gate. Imported, not redeclared, so the two can no longer
# silently drift the way they had (identical membership, no import between
# them, until this change).
VERDICTS = evidence_obligation.VERDICTS
OBLIGATIONS = evidence_obligation.LEVELS
INDEPENDENCE = (
    "self-authored",
    "cross-derived",
    "human-specified",
    "external",
    "unverified",
)
DISCRIMINATED = ("yes", "no", "NO-DATA")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_DATA = 2

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RFC3339_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$"
)


class ValidationError(Exception):
    """Raised when a predicate field is missing or has a bad value."""

    def __init__(self, field, detail):
        super().__init__("%s: %s" % (field, detail))
        self.field = field
        self.detail = detail


def sha256_file(path):
    """Return the sha256 hex digest of a file, streaming it."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(obj):
    """Return canonical JSON bytes (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_pae(payload_type, body):
    """Build DSSE v1 Pre-Authentication Encoding bytes.

    PAE(type, body) = b"DSSEv1" + SP + LEN(type) + SP + type + SP
                      + LEN(body) + SP + body
    """
    return (
        b"DSSEv1"
        + b" "
        + str(len(payload_type)).encode("ascii")
        + b" "
        + payload_type.encode("utf-8")
        + b" "
        + str(len(body)).encode("ascii")
        + b" "
        + body
    )


def validate_predicate(pred):
    """Validate every required predicate field and enum.

    Raises ValidationError naming the offending field.
    """
    if not isinstance(pred, dict):
        raise ValidationError("predicate", "must be a JSON object")
    if "claim" not in pred:
        raise ValidationError("claim", "missing")
    if not isinstance(pred["claim"], str):
        raise ValidationError("claim", "must be a string")
    if "verdict" not in pred:
        raise ValidationError("verdict", "missing")
    if pred["verdict"] not in VERDICTS:
        raise ValidationError("verdict", "must be one of %s" % (list(VERDICTS),))
    if "obligation" not in pred:
        raise ValidationError("obligation", "missing")
    if pred["obligation"] not in OBLIGATIONS:
        raise ValidationError(
            "obligation", "must be one of %s" % (list(OBLIGATIONS),)
        )
    if "evidence" not in pred:
        raise ValidationError("evidence", "missing")
    ev_list = pred["evidence"]
    if not isinstance(ev_list, list):
        raise ValidationError("evidence", "must be a list")
    for i, ev in enumerate(ev_list):
        prefix = "evidence[%d]" % i
        if not isinstance(ev, dict):
            raise ValidationError(prefix, "must be an object")
        for key, typ in (
            ("check", str),
            ("family", str),
            ("independence", str),
            ("discriminated", str),
            ("command", str),
            ("exit_code", int),
        ):
            if key not in ev:
                raise ValidationError(prefix + "." + key, "missing")
            val = ev[key]
            if typ is int:
                if isinstance(val, bool) or not isinstance(val, int):
                    raise ValidationError(prefix + "." + key, "must be an integer")
            else:
                if not isinstance(val, typ):
                    raise ValidationError(prefix + "." + key, "must be a string")
        if ev["independence"] not in INDEPENDENCE:
            raise ValidationError(
                prefix + ".independence",
                "must be one of %s" % (list(INDEPENDENCE),),
            )
        if ev["discriminated"] not in DISCRIMINATED:
            raise ValidationError(
                prefix + ".discriminated",
                "must be one of %s" % (list(DISCRIMINATED),),
            )
    if "source" not in pred:
        raise ValidationError("source", "missing")
    src = pred["source"]
    if not isinstance(src, dict):
        raise ValidationError("source", "must be an object")
    if "repository" not in src:
        raise ValidationError("source.repository", "missing")
    if not isinstance(src["repository"], str):
        raise ValidationError("source.repository", "must be a string")
    if "commit" not in src:
        raise ValidationError("source.commit", "missing")
    if not isinstance(src["commit"], str) or not COMMIT_RE.match(src["commit"]):
        raise ValidationError(
            "source.commit", "must be 40 lowercase hex characters"
        )
    if "produced_by" not in pred:
        raise ValidationError("produced_by", "missing")
    pb = pred["produced_by"]
    if not isinstance(pb, dict):
        raise ValidationError("produced_by", "must be an object")
    if pb.get("tool") != SSH_TOOL_NAME:
        raise ValidationError(
            "produced_by.tool", "must be %r" % (SSH_TOOL_NAME,)
        )
    if "version" not in pb:
        raise ValidationError("produced_by.version", "missing")
    if not isinstance(pb["version"], str):
        raise ValidationError("produced_by.version", "must be a string")
    if "decided_at" not in pred:
        raise ValidationError("decided_at", "missing")
    if (
        not isinstance(pred["decided_at"], str)
        or not RFC3339_UTC_RE.match(pred["decided_at"])
    ):
        raise ValidationError(
            "decided_at", "must be an RFC 3339 UTC timestamp ending in Z"
        )
    if "related_predicates" in pred:
        rp_list = pred["related_predicates"]
        if not isinstance(rp_list, list):
            raise ValidationError("related_predicates", "must be a list")
        for i, rp in enumerate(rp_list):
            if not isinstance(rp, str):
                raise ValidationError(
                    "related_predicates[%d]" % i, "must be a string"
                )


def cmd_emit(args):
    try:
        with open(args.claim_file, "rb") as f:
            raw = f.read()
    except OSError as exc:
        print("emit: cannot read claim file: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    try:
        claim = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        print("emit: claim file is not valid JSON: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    if not isinstance(claim, dict):
        print(
            "emit: invalid field predicate: must be a JSON object",
            file=sys.stderr,
        )
        return EXIT_FAIL
    if "previous_statement_digest" in claim:
        print(
            "emit: invalid field previous_statement_digest: "
            "must not be present in the claim file",
            file=sys.stderr,
        )
        return EXIT_FAIL
    try:
        validate_predicate(claim)
    except ValidationError as exc:
        print(
            "emit: invalid field %s: %s" % (exc.field, exc.detail),
            file=sys.stderr,
        )
        return EXIT_FAIL

    if "related_predicates" not in claim:
        claim["related_predicates"] = list(DEFAULT_RELATED)

    subject = []
    for path in args.artifact:
        try:
            digest = sha256_file(path)
        except OSError as exc:
            print(
                "emit: cannot read artifact %s: %s" % (path, exc),
                file=sys.stderr,
            )
            return EXIT_FAIL
        subject.append(
            {"name": os.path.basename(path), "digest": {"sha256": digest}}
        )

    previous_digest = None
    if args.previous:
        try:
            with open(args.previous, "rb") as f:
                prev_bytes = f.read()
        except OSError as exc:
            print(
                "emit: cannot read previous statement %s: %s"
                % (args.previous, exc),
                file=sys.stderr,
            )
            return EXIT_FAIL
        previous_digest = hashlib.sha256(prev_bytes).hexdigest()

    predicate = dict(claim)
    predicate["previous_statement_digest"] = previous_digest

    statement = {
        "_type": INTOTO_STATEMENT_TYPE,
        "subject": subject,
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }
    data = canonical_json_bytes(statement)
    try:
        with open(args.out, "wb") as f:
            f.write(data)
    except OSError as exc:
        print("emit: cannot write statement: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    print("emit: PASS wrote %s" % args.out)
    return EXIT_OK


def _ssh_keygen_available():
    return shutil.which("ssh-keygen") is not None


def cmd_sign(args):
    try:
        with open(args.statement, "rb") as f:
            stmt_bytes = f.read()
    except OSError as exc:
        print("sign: cannot read statement: %s" % exc, file=sys.stderr)
        return EXIT_FAIL

    if not _ssh_keygen_available():
        print("sign: ssh-keygen not found on PATH", file=sys.stderr)
        return EXIT_FAIL

    pae = build_pae(DSSE_PAYLOAD_TYPE, stmt_bytes)
    tmpdir = tempfile.mkdtemp(prefix="brother-receipt-sign-")
    armored = None
    try:
        pae_path = os.path.join(tmpdir, "pae.bin")
        with open(pae_path, "wb") as f:
            f.write(pae)
        try:
            proc = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    args.key,
                    "-n",
                    SSH_NAMESPACE,
                    pae_path,
                ],
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print("sign: ssh-keygen -Y sign timed out", file=sys.stderr)
            return EXIT_FAIL
        except OSError as exc:
            print("sign: cannot run ssh-keygen: %s" % exc, file=sys.stderr)
            return EXIT_FAIL
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
            print("sign: ssh-keygen -Y sign failed", file=sys.stderr)
            return EXIT_FAIL
        sig_path = pae_path + ".sig"
        try:
            with open(sig_path, "rb") as f:
                armored = f.read()
        except OSError as exc:
            print(
                "sign: cannot read ssh signature file: %s" % exc,
                file=sys.stderr,
            )
            return EXIT_FAIL
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if armored is None:
        print("sign: no signature produced", file=sys.stderr)
        return EXIT_FAIL

    try:
        proc = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", args.pubkey],
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("sign: ssh-keygen -l timed out", file=sys.stderr)
        return EXIT_FAIL
    except OSError as exc:
        print("sign: cannot run ssh-keygen: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        print("sign: ssh-keygen -l failed", file=sys.stderr)
        return EXIT_FAIL

    fp_line = proc.stdout.decode("utf-8", "replace").strip()
    fp_fields = fp_line.split()
    if len(fp_fields) < 2:
        print(
            "sign: cannot parse ssh-keygen fingerprint output: %r" % fp_line,
            file=sys.stderr,
        )
        return EXIT_FAIL
    fingerprint = fp_fields[1]
    keyid = "ssh:" + fingerprint

    envelope = {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(stmt_bytes).decode("ascii"),
        "signatures": [
            {
                "keyid": keyid,
                "sig": base64.b64encode(armored).decode("ascii"),
            }
        ],
    }
    data = canonical_json_bytes(envelope)
    try:
        with open(args.out, "wb") as f:
            f.write(data)
    except OSError as exc:
        print("sign: cannot write envelope: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    print("sign: PASS wrote %s" % args.out)
    return EXIT_OK


def _envelope_no_data(reason):
    print("verify: FAIL (%s)" % reason)
    return EXIT_NO_DATA


def cmd_verify(args):
    if not _ssh_keygen_available():
        print("verify: FAIL (ssh-keygen not installed)")
        return EXIT_NO_DATA

    try:
        with open(args.envelope, "rb") as f:
            env_raw = f.read()
        envelope = json.loads(env_raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return _envelope_no_data("envelope cannot be parsed")

    try:
        if not isinstance(envelope, dict):
            raise ValueError("envelope")
        payload_type = envelope["payloadType"]
        payload_b64 = envelope["payload"]
        signatures = envelope["signatures"]
        if payload_type != DSSE_PAYLOAD_TYPE:
            raise ValueError("payloadType")
        if not isinstance(payload_b64, str):
            raise ValueError("payload")
        if not isinstance(signatures, list) or not signatures:
            raise ValueError("signatures")
        first_sig = signatures[0]
        if not isinstance(first_sig, dict):
            raise ValueError("signature entry")
        sig_b64 = first_sig["sig"]
        if not isinstance(sig_b64, str):
            raise ValueError("sig")
    except (KeyError, TypeError, ValueError):
        return _envelope_no_data("envelope cannot be parsed")

    try:
        stmt_bytes = base64.b64decode(payload_b64, validate=True)
        armored_sig = base64.b64decode(sig_b64, validate=True)
    except (ValueError, binascii.Error, TypeError):
        return _envelope_no_data("envelope cannot be parsed")

    reasons = []

    # Signature check.
    pae = build_pae(DSSE_PAYLOAD_TYPE, stmt_bytes)
    tmpdir = tempfile.mkdtemp(prefix="brother-receipt-verify-")
    sig_ok = False
    try:
        pae_path = os.path.join(tmpdir, "pae.bin")
        sig_path = os.path.join(tmpdir, "sig.txt")
        with open(pae_path, "wb") as f:
            f.write(pae)
        with open(sig_path, "wb") as f:
            f.write(armored_sig)
        try:
            with open(pae_path, "rb") as stdin_f:
                proc = subprocess.run(
                    [
                        "ssh-keygen",
                        "-Y",
                        "verify",
                        "-f",
                        args.allowed_signers,
                        "-I",
                        args.identity,
                        "-n",
                        SSH_NAMESPACE,
                        "-s",
                        sig_path,
                    ],
                    stdin=stdin_f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
            sig_ok = proc.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            sig_ok = False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if sig_ok:
        print("PASS signature")
    else:
        print("FAIL signature")
        reasons.append("signature")

    # Parse the statement for the remaining checks.
    stmt = None
    try:
        stmt = json.loads(stmt_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        stmt = None

    # Artifact digest checks against the subjects of the statement.
    if args.artifact:
        subjects = None
        if isinstance(stmt, dict):
            subj = stmt.get("subject")
            if isinstance(subj, list):
                subjects = subj
        for path in args.artifact:
            ok = False
            if subjects is not None:
                try:
                    digest = sha256_file(path)
                except OSError:
                    digest = None
                if digest is not None:
                    for s in subjects:
                        if not isinstance(s, dict):
                            continue
                        d = s.get("digest")
                        if isinstance(d, dict) and d.get("sha256") == digest:
                            ok = True
                            break
            if ok:
                print("PASS artifact %s" % path)
            else:
                print("FAIL artifact %s" % path)
                reasons.append("artifact %s" % path)

    # Chain check: previous statement digest.
    if args.previous is not None:
        ok = False
        try:
            with open(args.previous, "rb") as f:
                prev_bytes = f.read()
            expected = hashlib.sha256(prev_bytes).hexdigest()
            if isinstance(stmt, dict):
                pred = stmt.get("predicate")
                if isinstance(pred, dict):
                    actual = pred.get("previous_statement_digest")
                    if actual == expected:
                        ok = True
        except OSError:
            ok = False
        if ok:
            print("PASS previous")
        else:
            print("FAIL previous")
            reasons.append("previous statement")

    # Report the claim verdict, but never let it influence the exit code.
    verdict = "NO-DATA"
    if isinstance(stmt, dict):
        pred = stmt.get("predicate")
        if isinstance(pred, dict):
            v = pred.get("verdict")
            if isinstance(v, str):
                verdict = v
    print(
        "claim verdict: %s "
        "(the signature proves who said it, not that it passed)" % verdict
    )

    if reasons:
        print("verify: FAIL (%s)" % "; ".join(reasons))
        return EXIT_FAIL
    print("verify: PASS")
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="receipt_attest",
        description=(
            "Brother verification receipt attestation (experimental v0.1)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pe = sub.add_parser(
        "emit", help="emit an in-toto statement from a claim file"
    )
    pe.add_argument("--claim-file", required=True)
    pe.add_argument("--artifact", action="append", required=True, default=[])
    pe.add_argument("--out", required=True)
    pe.add_argument("--previous", default=None)
    pe.set_defaults(func=cmd_emit)

    ps = sub.add_parser(
        "sign", help="wrap a statement in a signed DSSE envelope"
    )
    ps.add_argument("--statement", required=True)
    ps.add_argument("--key", required=True)
    ps.add_argument("--pubkey", required=True)
    ps.add_argument("--out", required=True)
    ps.set_defaults(func=cmd_sign)

    pv = sub.add_parser("verify", help="verify a signed envelope offline")
    pv.add_argument("--envelope", required=True)
    pv.add_argument("--allowed-signers", required=True)
    pv.add_argument("--identity", required=True)
    pv.add_argument("--artifact", action="append", default=[])
    pv.add_argument("--previous", default=None)
    pv.set_defaults(func=cmd_verify)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

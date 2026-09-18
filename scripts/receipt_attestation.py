#!/usr/bin/env python3
"""receipt_attestation: whether a receipt was attested, and by whom, v1.

WBS DOM-10.06. `scripts/receipt_attest.py` already signs a claim with a
real cryptographic identity (an ssh-keygen key) but names no producer: its
predicate has no field saying who did the work the claim is about, so it
cannot refuse an actor attesting to its own output. This module does not
replace that tool or invent a second receipt wire format; it is a small,
separate check that binds a receipt's content hash to TWO named identities,
a producer and an attester, using a symmetric key that belongs only to the
attester (never a shared secret, so nobody but the attester can produce a
record that verifies as theirs), and refuses outright when those two
identities are the same string. That is the one new fact this module
establishes that receipt_attest.py does not: self-attestation is not
measurement, it is the absence of measurement wearing its clothes.

DECIDING PROPERTY: a receipt is attested only when its content hash and
the attesting identity both verify against the recorded attestation. Every
other case is FAIL or NO-DATA, never PASS:

  NO-DATA (the fact could not be established): the attestation record is
  missing, unreadable, not JSON, not an object, missing a required field,
  names an attesting identity that is not a safe identity string, or names
  an attesting identity with no key file in the keys directory. The receipt
  file itself being unreadable is also NO-DATA for the same reason: nothing
  could be checked.

  FAIL (the fact was established, and it is bad): the receipt's recomputed
  sha256 does not match the hash recorded at attestation time (tampering),
  the producing and attesting identities are the same string
  (self-attestation, checked again here even though build_attestation
  already refuses to create one, because verify_attestation is the actual
  trust boundary and must not depend on every record having been built by
  this module's own honest path), or the recomputed HMAC does not match the
  recorded one (forged or corrupted signature).

  PASS: none of the above, and the signature recomputed with the attesting
  identity's own key matches.

An identity string is untrusted input the moment it comes from a record
someone else wrote (verify_attestation's whole job is to check records this
process did not necessarily produce), and it feeds directly into a file
path (`<keys_dir>/<identity>.key`), so an identity containing a path
separator or a `.` / `..` segment is refused before any file is opened
rather than trusted to `os.path.join`.

Verdicts are `evidence_obligation.VERDICTS`, imported, never redeclared.
`sha256_file` and `canonical_json_bytes` are `receipt_attest.py`'s own
(identical logic, one definition), imported rather than duplicated.

Standard library only. No network, no subprocess.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import sys

import evidence_obligation
import receipt_attest

PASS, FAIL, NO_DATA = evidence_obligation.VERDICTS

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NO_DATA = 2
_EXIT_BY_VERDICT = {PASS: EXIT_OK, FAIL: EXIT_FAIL, NO_DATA: EXIT_NO_DATA}

#: Every field a record must carry before anything else about it can be
#: checked. `hmac_sha256` is the signature; the rest is what it signs, in
#: the exact order it was signed in (SIGNED_FIELDS), so a verifier that
#: reads a record from disk reconstructs the same bytes a builder produced.
REQUIRED_FIELDS = (
    "receipt_sha256",
    "producing_identity",
    "attesting_identity",
    "attested_at",
    "hmac_sha256",
)
SIGNED_FIELDS = (
    "receipt_sha256",
    "producing_identity",
    "attesting_identity",
    "attested_at",
)

#: An identity is a file name stem, nothing else: letters, digits,
#: underscore, hyphen, dot, one to two hundred characters, and never a
#: bare "." or ".." segment. This is checked before the string is ever
#: joined into a path, so a record naming "../../etc/passwd" as its
#: attesting identity is refused, not resolved.
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")


def _valid_identity(identity):
    if not isinstance(identity, str):
        return False
    if not _IDENTITY_RE.match(identity):
        return False
    return identity not in (".", "..") and "/" not in identity and "\\" not in identity


class AttestationError(Exception):
    """Raised when an attestation record cannot be built at all."""


def _key_path(keys_dir, identity):
    return os.path.join(keys_dir, identity + ".key")


def _read_key(keys_dir, identity):
    """Read an identity's own secret key file. Raises AttestationError for
    any boundary failure: unreadable, missing, or empty (an empty key file
    would make every signature it produces trivially forgeable)."""
    path = _key_path(keys_dir, identity)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise AttestationError(
            "cannot read key file for identity %r: %s" % (identity, exc)
        )
    if not data:
        raise AttestationError("key file for identity %r is empty" % identity)
    return data


def _sign(signed_fields, key):
    return hmac.new(
        key, receipt_attest.canonical_json_bytes(signed_fields), hashlib.sha256
    ).hexdigest()


def build_attestation(
    receipt_path, producing_identity, attesting_identity, keys_dir, timestamp=None
):
    """Build and return an attestation record (a plain dict) for the given
    receipt file, signed with the attesting identity's own key.

    Raises AttestationError, never returns a partial or fake record, when:
    the producing and attesting identities are the same string, either
    identity is not a valid identity string, the receipt cannot be read,
    or the attesting identity has no usable key file.
    """
    if not _valid_identity(producing_identity):
        raise AttestationError(
            "producing identity is not a valid identity string: %r"
            % (producing_identity,)
        )
    if not _valid_identity(attesting_identity):
        raise AttestationError(
            "attesting identity is not a valid identity string: %r"
            % (attesting_identity,)
        )
    if producing_identity == attesting_identity:
        raise AttestationError(
            "self-attestation is not permitted: %r produced and attested "
            "the same receipt" % (producing_identity,)
        )

    try:
        receipt_sha256 = receipt_attest.sha256_file(receipt_path)
    except OSError as exc:
        raise AttestationError("cannot read receipt %r: %s" % (receipt_path, exc))

    key = _read_key(keys_dir, attesting_identity)

    if timestamp is None:
        timestamp = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    signed = {
        "receipt_sha256": receipt_sha256,
        "producing_identity": producing_identity,
        "attesting_identity": attesting_identity,
        "attested_at": timestamp,
    }
    record = dict(signed)
    record["hmac_sha256"] = _sign(signed, key)
    return record


def verify_attestation(attestation, receipt_path, keys_dir):
    """Decide whether an attestation record attests a receipt.

    Returns (verdict, reason): verdict is one of evidence_obligation's
    VERDICTS, reason is a short human-readable sentence. Never returns
    PASS unless the content hash, the identity separation and the HMAC
    all independently hold; see the module docstring for exactly which
    failure reads as FAIL and which reads as NO-DATA.
    """
    if not isinstance(attestation, dict):
        return NO_DATA, "attestation record is not a JSON object"
    for field in REQUIRED_FIELDS:
        if field not in attestation:
            return NO_DATA, "attestation record is missing field %r" % field
        if not isinstance(attestation[field], str):
            return NO_DATA, "attestation field %r must be a string" % field

    producing = attestation["producing_identity"]
    attesting = attestation["attesting_identity"]

    if not _valid_identity(attesting):
        return (
            NO_DATA,
            "attesting identity is not a valid identity string: %r" % (attesting,),
        )

    # Established and bad: the two named identities are the same actor.
    # Checked here, independently of build_attestation, because a
    # hand-crafted or tampered record could name equal identities without
    # ever passing through the builder.
    if producing == attesting:
        return (
            FAIL,
            "self-attestation: producing and attesting identity are both "
            "%r" % (producing,),
        )

    try:
        recomputed = receipt_attest.sha256_file(receipt_path)
    except OSError as exc:
        return NO_DATA, "cannot read receipt %r: %s" % (receipt_path, exc)

    if not hmac.compare_digest(recomputed, attestation["receipt_sha256"]):
        return (
            FAIL,
            "receipt content hash does not match the recorded "
            "receipt_sha256 (the receipt was modified after attestation)",
        )

    try:
        key = _read_key(keys_dir, attesting)
    except AttestationError as exc:
        return NO_DATA, str(exc)

    signed = {field: attestation[field] for field in SIGNED_FIELDS}
    expected = _sign(signed, key)
    if not hmac.compare_digest(expected, attestation["hmac_sha256"]):
        return FAIL, "hmac_sha256 does not match: invalid or forged signature"

    return PASS, "attested: content hash and signature both verify"


def _read_attestation_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise AttestationError("cannot read attestation file %r: %s" % (path, exc))
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise AttestationError(
            "attestation file %r is not valid JSON: %s" % (path, exc)
        )
    return parsed


def _cmd_attest(args):
    try:
        record = build_attestation(
            args.receipt, args.producing_identity, args.attesting_identity,
            args.keys_dir,
        )
    except AttestationError as exc:
        print("%s: %s" % (NO_DATA, exc), file=sys.stderr)
        return EXIT_NO_DATA
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(record, fh, sort_keys=True, indent=2)
            fh.write("\n")
    except OSError as exc:
        print("%s: cannot write attestation: %s" % (NO_DATA, exc), file=sys.stderr)
        return EXIT_NO_DATA
    print("attest: %s wrote %s" % (PASS, args.out))
    return EXIT_OK


def _cmd_verify(args):
    try:
        attestation = _read_attestation_file(args.attestation)
    except AttestationError as exc:
        print("%s: %s" % (NO_DATA, exc), file=sys.stderr)
        return EXIT_NO_DATA
    verdict, reason = verify_attestation(attestation, args.receipt, args.keys_dir)
    print("%s: %s" % (verdict, reason))
    return _EXIT_BY_VERDICT[verdict]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="receipt_attestation",
        description="Bind a receipt's content hash to a producing and an "
        "attesting identity, and refuse self-attestation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pa = sub.add_parser("attest", help="build an attestation record")
    pa.add_argument("--receipt", required=True)
    pa.add_argument("--producing-identity", required=True)
    pa.add_argument("--attesting-identity", required=True)
    pa.add_argument("--keys-dir", required=True)
    pa.add_argument("--out", required=True)
    pa.set_defaults(func=_cmd_attest)

    pv = sub.add_parser("verify", help="verify an attestation record")
    pv.add_argument("--receipt", required=True)
    pv.add_argument("--attestation", required=True)
    pv.add_argument("--keys-dir", required=True)
    pv.set_defaults(func=_cmd_verify)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

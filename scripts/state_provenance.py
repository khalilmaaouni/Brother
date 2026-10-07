#!/usr/bin/env python3
"""M2.05: state provenance evidence (docs/plan/MOBILE-EPIC-M2-UNITS.md).

Epic M2's objective is "every critical journey starts from reproducible
state." This unit is the glue between M2.01 (mobile_state_fixture, the
fixture schema/validator) and M2.02 (mobile_state_reset, the adapters that
apply a fixture to a device): it produces the provenance record that binds
a validated fixture, by content hash, to the receipt of applying it, and
later binds a cleanup receipt onto a fresh copy of that same record.

This module is pure JSON/hash plumbing over evidence already on disk. It
never talks to a simulator or a backend, never applies or reverts anything,
and never re-verifies device state -- read mobile_state_reset.py's own
receipt for that. Its only job is to make the binding itself auditable: a
provenance record proves a specific fixture file, at a specific content
hash, is the one whose setup (and later cleanup) receipt is attached to it.

HASH-MISMATCH REFUSAL. mobile_state_reset.apply_fixture stamps a real
fixture_hash into its own receipt, inside its lease, at the moment the reset
actually runs -- the one field in the receipt a forger cannot produce
without apply_fixture itself having read the real fixture bytes right now.
record_setup re-hashes fixture_path now and refuses (never writes anything)
unless that fresh hash matches the hash already stamped into the receipt.
This closes the swap window (the fixture file at fixture_path being edited
or replaced between the moment apply_fixture ran and the moment provenance
is recorded) without asking the caller to self-report a hash, which proved
nothing about whether apply_fixture had actually run.

INCOMPLETE-EVIDENCE REFUSAL. A receipt naming the right schema and a PASS
status is not proof anything actually happened -- an apply receipt with an
empty or malformed mechanisms map, or a cleanup receipt with no result/
checkpoint payload behind it, is refused as incomplete, never silently
bound as if it were real evidence.

IDENTITY CORRELATION. record_cleanup refuses a cleanup receipt whose
device, bundle_id, or platform does not match the setup record it is
closing out (an unrelated app's uninstall receipt can no longer bind onto
this record), and it writes those fields into the cleanup block so a
reader never has to open the cleanup receipt separately to see a mismatch.
record_cleanup also refuses a cleanup receipt whose schema implies a
different cleanup_policy.method than the fixture declared, and
check-complete refuses a record with cleanup_policy.required true and no
cleanup ever bound.

WRITE-ONCE, NEVER MUTATED. record_setup refuses to overwrite an existing
output directory; record_cleanup never rewrites provenance_path in place --
it refuses if that record already carries a cleanup, and writes the merged
record to a fresh path, so the setup-only record stays on disk untouched.

Exit contract matches every sibling gate in this repo: 0 PASS, 1 FAIL,
2 NO-DATA.
"""
import argparse
import json
import sys
from pathlib import Path

import contract_check as CC
import mobile_state_fixture as F
import native_evidence as N
from mobile_workflow import Refusal, digest, read_json, require, write_new


APPLY_RECEIPT_SCHEMA = "brother-mobile-state-reset-v1"
PROVENANCE_SCHEMA = "brother-mobile-state-provenance-v1"
RECEIPT_STATUSES = ("PASS", "FAIL", "NO-DATA")
# mobile_state_reset.py's own apply_fixture never reports NO-OP as its
# top-level receipt status (that stays PASS/FAIL/NO-DATA, matching
# RECEIPT_STATUSES above) -- NO-OP is only ever one MECHANISM's own status
# inside the receipt (keychain preserve, an empty feature_flags map: a
# correct no-op, distinct from both PASS and NO-DATA). A real receipt can
# legitimately carry it, so _check_mechanisms accepts it per-mechanism even
# though no top-level status check ever does.
MECHANISM_STATUSES = RECEIPT_STATUSES + ("NO-OP",)
CLEANUP_SCHEMA_PREFIX = "brother-mobile-state-"

# cleanup_policy.method (docs/schema/mobile-state-fixture-v1.json) is free
# text, not an enum -- only mapped here for the receipt schemas whose action
# is unambiguous. capture-checkpoint receipts CAPTURE, they never restore
# (this module has no restore-checkpoint receipt shape), so it is left
# unmapped on purpose rather than guessed at; record_cleanup skips the
# method check for any schema not in this map.
CLEANUP_METHOD_BY_SCHEMA = {
    APPLY_RECEIPT_SCHEMA: "reset-app-storage",
    "brother-mobile-state-uninstall-v1": "delete-account",
}

LIMITS = [
    "Binds fixture identity and setup-mechanism results by content hash; it does not "
    "itself re-verify device state -- read mobile_state_reset.py's own receipt for that.",
    "cleanup is null until record_cleanup binds a cleanup result onto a fresh copy of "
    "this record; this file is never mutated in place once written.",
]


def _check_mechanisms(mechanisms, label):
    """Every mechanism in an apply-style receipt (mobile_state_reset.py's own
    'brother-mobile-state-reset-v1' shape) must carry a real status and a
    non-empty detail string -- an empty or malformed map is exactly the
    incomplete-looking-complete evidence this module exists to refuse."""
    require(isinstance(mechanisms, dict) and mechanisms, "%s: mechanisms missing or empty" % label)
    for name in sorted(mechanisms):
        entry = mechanisms[name]
        require(isinstance(entry, dict), "%s: mechanism %r is not an object" % (label, name))
        require(entry.get("status") in MECHANISM_STATUSES,
                "%s: mechanism %r has missing/invalid status: %r" % (label, name, entry.get("status")))
        detail = entry.get("detail")
        require(isinstance(detail, str) and detail, "%s: mechanism %r has missing/empty detail" % (label, name))


def _check_cleanup_completeness(receipt):
    """A cleanup receipt's schema and status alone prove nothing: a caller
    could hand in {"schema": "brother-mobile-state-uninstall-v1", "status":
    "PASS"} with no result behind it at all. Each of mobile_state_reset.py's
    four receipt shapes carries a real payload beyond schema/status --
    require it, by shape, rather than accepting schema+status as sufficient.
    An unrecognized brother-mobile-state-* schema is refused outright: this
    module names every shape it can verify, it does not guess at new ones."""
    schema = receipt.get("schema")
    if schema == APPLY_RECEIPT_SCHEMA:
        _check_mechanisms(receipt.get("mechanisms"), "cleanup receipt")
        return
    if schema in ("brother-mobile-state-uninstall-v1", "brother-mobile-state-install-v1"):
        result = receipt.get("result")
        require(isinstance(result, dict), "cleanup receipt %r has no result object" % schema)
        require(result.get("status") in RECEIPT_STATUSES,
                "cleanup receipt %r result has missing/invalid status" % schema)
        require(isinstance(result.get("detail"), str) and result["detail"],
                "cleanup receipt %r result has missing/empty detail" % schema)
        return
    if schema == "brother-mobile-state-capture-checkpoint-v1":
        require(isinstance(receipt.get("checkpoint"), str) and receipt["checkpoint"],
                "cleanup receipt %r has no checkpoint path" % schema)
        require(N.valid_file_hash(receipt.get("digest")),
                "cleanup receipt %r has a missing or malformed checkpoint digest" % schema)
        return
    raise Refusal("cleanup receipt schema %r is not a recognized mobile-state receipt shape" % schema)


def record_setup(fixture_path, schema_path, apply_receipt_path, out):
    fixture = F.load_fixture(fixture_path)
    schema = CC.load_json(schema_path, "mobile-state-fixture-v1 schema")
    problems = F.check(fixture, schema)
    require(not problems, "fixture fails M2.01 validation, refusing to bind provenance to an "
            "invalid fixture: %s" % "; ".join(problems))

    receipt_hash = digest(apply_receipt_path)
    receipt = read_json(apply_receipt_path)
    require(isinstance(receipt, dict), "apply receipt is not a JSON object: %s" % apply_receipt_path)
    require(receipt.get("schema") == APPLY_RECEIPT_SCHEMA,
            "apply receipt schema %r is not %r" % (receipt.get("schema"), APPLY_RECEIPT_SCHEMA))
    require(receipt.get("status") in RECEIPT_STATUSES,
            "apply receipt status missing or invalid: %r" % receipt.get("status"))
    require(receipt.get("fixture_id") == fixture["fixture_id"],
            "apply receipt fixture_id %r does not match bound fixture_id %r"
            % (receipt.get("fixture_id"), fixture["fixture_id"]))
    _check_mechanisms(receipt.get("mechanisms"), "apply receipt")

    # The one self-authenticating fact in a real receipt: apply_fixture
    # stamped this hash itself, inside its own lease, at the moment it ran.
    # Compare it (by content only, never by path, so a fixture legitimately
    # moved between directories is not a false mismatch) against a fresh
    # hash of the fixture on disk now. A receipt with no valid fixture_hash
    # at all -- exactly the shape of every receipt before this field existed,
    # which is exactly the shape of a hand-typed forgery that never called
    # apply_fixture -- is refused outright.
    receipt_fixture_hash = receipt.get("fixture_hash")
    require(N.valid_file_hash(receipt_fixture_hash),
            "apply receipt has no valid fixture_hash: %r" % (receipt_fixture_hash,))
    current_fixture_hash = digest(fixture_path)
    require(current_fixture_hash.get("sha256") == receipt_fixture_hash.get("sha256"),
            "fixture hash mismatch: apply receipt recorded sha256=%s but fixture on disk now is "
            "sha256=%s (%s); the fixture bound to this provenance record is not the one that was "
            "actually applied"
            % (receipt_fixture_hash.get("sha256"), current_fixture_hash.get("sha256"), fixture_path))

    out = Path(out)
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)
    out.mkdir(parents=True, exist_ok=False)

    record = {
        "schema": PROVENANCE_SCHEMA,
        "fixture_id": fixture["fixture_id"],
        "fixture_hash": current_fixture_hash,
        "cleanup_policy": fixture.get("cleanup_policy"),
        "setup": {
            "receipt_path": str(Path(apply_receipt_path).resolve()),
            "receipt_hash": receipt_hash,
            "status": receipt["status"],
            "platform": receipt.get("platform"),
            "device": receipt.get("device"),
            "bundle_id": receipt.get("bundle_id"),
            "mechanisms": receipt["mechanisms"],
        },
        "cleanup": None,
        "limits": list(LIMITS),
    }
    provenance_path = out / "provenance.json"
    write_new(provenance_path, record)
    return record, provenance_path


def record_cleanup(provenance_path, cleanup_receipt_path, out):
    record = read_json(provenance_path)
    require(isinstance(record, dict), "provenance record is not a JSON object: %s" % provenance_path)
    require(record.get("schema") == PROVENANCE_SCHEMA,
            "provenance schema %r is not %r" % (record.get("schema"), PROVENANCE_SCHEMA))
    require(record.get("cleanup") is None,
            "refusing: cleanup already recorded for this provenance record: %s" % provenance_path)

    cleanup_receipt_hash = digest(cleanup_receipt_path)
    cleanup_receipt = read_json(cleanup_receipt_path)
    require(isinstance(cleanup_receipt, dict), "cleanup receipt is not a JSON object: %s" % cleanup_receipt_path)
    cleanup_schema = cleanup_receipt.get("schema")
    require(isinstance(cleanup_schema, str) and cleanup_schema.startswith(CLEANUP_SCHEMA_PREFIX),
            "cleanup receipt schema must start with %r, got %r" % (CLEANUP_SCHEMA_PREFIX, cleanup_schema))
    require(cleanup_receipt.get("status") in RECEIPT_STATUSES,
            "cleanup receipt status missing or invalid: %r" % cleanup_receipt.get("status"))
    _check_cleanup_completeness(cleanup_receipt)

    # Identity correlation: record_setup already refuses an apply receipt
    # whose fixture_id does not match the fixture it is binding to. Cleanup
    # gets no equivalent check today, so an unrelated device/app/platform's
    # receipt binds cleanly onto this record's setup -- refuse that here.
    setup = record.get("setup") or {}
    for field in ("device", "bundle_id", "platform"):
        require(cleanup_receipt.get(field) == setup.get(field),
                "cleanup receipt %s %r does not match this record's setup %s %r: refusing to bind "
                "an unrelated device/app's cleanup onto this provenance record"
                % (field, cleanup_receipt.get(field), field, setup.get(field)))

    # cleanup_policy (docs/schema/mobile-state-fixture-v1.json), stamped into
    # this record by record_setup from the fixture it validated: when the
    # fixture declared a method, refuse a cleanup receipt whose schema
    # implies a different one (schemas with no known method mapping are
    # skipped, never guessed at -- see CLEANUP_METHOD_BY_SCHEMA above).
    policy = record.get("cleanup_policy") or {}
    policy_method = policy.get("method")
    schema_method = CLEANUP_METHOD_BY_SCHEMA.get(cleanup_schema)
    if policy_method and schema_method:
        require(schema_method == policy_method,
                "cleanup_policy.method %r does not match this cleanup receipt's schema %r "
                "(implies method %r)" % (policy_method, cleanup_schema, schema_method))

    out = Path(out)
    require(not out.exists(), "output path already exists, refusing to overwrite prior evidence: %s" % out)

    record = dict(record)
    record["cleanup"] = {
        "receipt_path": str(Path(cleanup_receipt_path).resolve()),
        "receipt_hash": cleanup_receipt_hash,
        "schema": cleanup_schema,
        "status": cleanup_receipt["status"],
        "platform": cleanup_receipt.get("platform"),
        "device": cleanup_receipt.get("device"),
        "bundle_id": cleanup_receipt.get("bundle_id"),
    }
    write_new(out, record)
    return record, out


def check_complete(record):
    """A provenance record whose fixture declared cleanup_policy.required is
    not evidence of a finished, reproducible journey while cleanup is still
    null -- refuse (never silently pass) rather than let it read as done.
    Reads cleanup_policy back off the record itself (stamped there by
    record_setup), never the original fixture file, which may not even be
    on disk any more by the time this runs."""
    require(isinstance(record, dict) and record.get("schema") == PROVENANCE_SCHEMA,
            "not a %r record: %r" % (PROVENANCE_SCHEMA, record.get("schema") if isinstance(record, dict) else record))
    policy = record.get("cleanup_policy") or {}
    require(not (policy.get("required") and record.get("cleanup") is None),
            "cleanup_policy.required is true but no cleanup has been recorded for fixture_id %r"
            % record.get("fixture_id"))
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(prog="state_provenance", description="M2.05 state provenance evidence")
    subs = parser.add_subparsers(dest="command", required=True)

    setup_p = subs.add_parser("record-setup", help="bind a fixture and its apply-fixture receipt into a provenance record")
    setup_p.add_argument("--fixture", required=True)
    setup_p.add_argument("--schema", default=F.DEFAULT_SCHEMA)
    setup_p.add_argument("--apply-receipt", required=True,
                          help="mobile_state_reset.py apply_fixture's own receipt.json, which must carry "
                               "the fixture_hash it stamped at the moment it ran")
    setup_p.add_argument("--out", required=True, help="directory to create; provenance.json is written inside it")

    cleanup_p = subs.add_parser("record-cleanup", help="bind a cleanup receipt onto a fresh copy of a provenance record")
    cleanup_p.add_argument("--provenance", required=True)
    cleanup_p.add_argument("--cleanup-receipt", required=True)
    cleanup_p.add_argument("--out", required=True, help="file path to write the merged record to (not a directory)")

    complete_p = subs.add_parser("check-complete", help="refuse a record whose cleanup_policy.required is unmet")
    complete_p.add_argument("--provenance", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "record-setup":
            record, _path = record_setup(args.fixture, args.schema, args.apply_receipt, args.out)
        elif args.command == "record-cleanup":
            record, _path = record_cleanup(args.provenance, args.cleanup_receipt, args.out)
        else:
            record = check_complete(read_json(args.provenance))
    except CC.NoData as exc:
        print("state_provenance: NO-DATA: %s" % exc)
        return 2
    except Refusal as exc:
        print("state_provenance: %s: %s" % (exc.status, exc))
        return 2 if exc.status == "NO-DATA" else 1
    except (OSError, ValueError) as exc:
        print("state_provenance: FAIL: %s" % exc)
        return 1

    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

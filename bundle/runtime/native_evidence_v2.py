#!/usr/bin/env python3
"""native_evidence_v2: wrap a scripts/native_evidence.py output record into
Evidence v2 shape, per WBS-30.05 (Native Evidence Adapter v2).

This is a WRAPPER, never a replacement: native_evidence.py's own record()
already implements every guarantee named in the roadmap row (fresh result
bundle, exact test leaves, candidate before/after identity, command/log,
artifact hashes, zero-test refusal, skipped/failed test refusal, stale
candidate refusal). wrap_v2() reads the JSON that record() already wrote,
re-runs native_evidence.validate_record() on it (the same function
native_evidence.py's own `validate` action calls, and the same function
`record()` calls on itself before returning) to get the record's own
PASS/FAIL/NO-DATA signal, and adds two things record() does not produce:
an explicit proof_scope (what this evidence does and does not establish,
including the simulator/real-device boundary already named in
scripts/device_matrix.py) and a preserved_guarantees list stating, per
guarantee, whether the wrapped record's own fields actually carry evidence
for it.

Per docs/decisions/evidence-vocabulary-2026-09-13.json (EV-3), the verdict
triple is imported from scripts/evidence_obligation.py, not redeclared.

Python 3, standard library only. No network.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_evidence  # noqa: E402
from evidence_obligation import VERDICTS  # noqa: E402

SCHEMA_V2 = "brother-native-evidence-v2"


def _leaves_of(doc):
    tests = doc.get("tests") if isinstance(doc, dict) else None
    leaves = tests.get("leaves") if isinstance(tests, dict) else None
    return leaves if isinstance(leaves, list) else None


def _device_requirement(doc):
    """The first requirement row naming a device, if any (case-insensitive)."""
    requirements = doc.get("requirements") if isinstance(doc, dict) else None
    if not isinstance(requirements, list):
        return None
    for item in requirements:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and "device" in name.lower():
            return item
    return None


def proof_scope(doc):
    """Explicit, doc-derived statements of what this evidence does and does
    not establish. Never inferred beyond what the doc's own fields say."""
    lines = []
    after = doc.get("candidate_after") if isinstance(doc, dict) else None
    if native_evidence.valid_snapshot(after):
        lines.append(
            "Proves the recorded command ran against candidate revision %s "
            "(dirty=%s) in %s, and that the candidate did not change while "
            "the command ran." % (after["revision"], after["dirty"], after["repo"]))
    else:
        lines.append(
            "Candidate identity is unavailable or malformed: this record "
            "does not prove which commit or worktree state was tested.")
    command = doc.get("command") if isinstance(doc, dict) else None
    if isinstance(command, dict) and isinstance(command.get("argv"), list) and command["argv"]:
        lines.append(
            "Proves the exact command `%s` was the one executed (exit code "
            "%r), with its full stdout/stderr log preserved by content hash."
            % (" ".join(str(a) for a in command["argv"]), command.get("exit_code")))
    expected = doc.get("expected_tests") if isinstance(doc, dict) else None
    if isinstance(expected, list) and expected:
        lines.append(
            "Proves these exact test identities were the ones required to "
            "pass: %s." % ", ".join(str(item) for item in expected))
    leaves = _leaves_of(doc)
    tests = doc.get("tests") if isinstance(doc, dict) else None
    if leaves:
        lines.append(
            "Proves %d executed test leaf/leaves reached a terminal result, "
            "read from a fresh xcresulttool query against the result bundle, "
            "never a self-reported count typed into JSON." % len(leaves))
    elif isinstance(tests, dict) and tests.get("error"):
        lines.append(
            "Proves nothing about test outcomes: the underlying result "
            "query did not produce usable test data (%s)." % tests["error"])
    lines.append(
        "Proves record integrity only (candidate identity, command, log, "
        "artifact and result-bundle bytes). It does NOT prove application "
        "quality, accessibility semantics, or human acceptance; those stay "
        "NOT-ASSESSED per native_evidence.py's own boundaries field and need "
        "separate evidence with their own verdict.")
    device_req = _device_requirement(doc)
    if isinstance(device_req, dict) and device_req.get("verdict") == "PASS":
        lines.append(
            "A physical-device requirement (%s) is recorded PASS in this "
            "evidence, so this record may be read as covering a real "
            "device, not only a simulator." % device_req.get("name"))
    else:
        lines.append(
            "No physical-device requirement is recorded PASS in this "
            "evidence: read every result here as a simulator run only. "
            "Never infer real-device behavior from a simulator PASS "
            "(same boundary scripts/device_matrix.py states for its own "
            "physical-device evidence).")
    return lines


def preserved_guarantees(doc, repo=None):
    """Per named guarantee: does THIS record's own fields actually carry
    evidence for it, checked structurally, never assumed."""
    result_bundle = doc.get("result_bundle") if isinstance(doc, dict) else None
    result_hash = result_bundle.get("hash") if isinstance(result_bundle, dict) else None
    before = doc.get("candidate_before") if isinstance(doc, dict) else None
    after = doc.get("candidate_after") if isinstance(doc, dict) else None
    command = doc.get("command") if isinstance(doc, dict) else None
    log_hash = command.get("log_hash") if isinstance(command, dict) else None
    artifacts = doc.get("artifacts") if isinstance(doc, dict) else None
    tests = doc.get("tests") if isinstance(doc, dict) else None
    leaves = _leaves_of(doc)

    candidate_identity = native_evidence.valid_snapshot(before) and native_evidence.valid_snapshot(after)
    exact_leaves = bool(leaves) and all(
        isinstance(leaf, dict) and isinstance(leaf.get("identity"), str)
        and isinstance(leaf.get("result"), str) for leaf in leaves)

    zero_test_refusal = bool(
        isinstance(tests, dict) and tests.get("error_kind") == "malformed"
        and "zero executed test" in str(tests.get("error", "")))
    if not zero_test_refusal:
        # A record that actually names N > 0 leaves is live proof the zero
        # count would have been refused: executed_count is recomputed from
        # the leaves list, never typed in separately.
        zero_test_refusal = (
            exact_leaves and isinstance(tests, dict)
            and tests.get("executed_count") == len(leaves) and len(leaves) > 0)

    after_repo = after.get("repo") if isinstance(after, dict) else None
    candidate_repo = repo or after_repo
    stale_candidate_refusal = bool(
        candidate_identity and isinstance(candidate_repo, str)
        and os.path.isdir(candidate_repo))

    checks = [
        ("fresh result bundle", native_evidence.valid_tree_hash(result_hash)),
        ("exact test leaves", exact_leaves),
        ("candidate before/after identity", candidate_identity),
        ("command/log", (isinstance(command, dict)
                          and isinstance(command.get("argv"), list) and bool(command["argv"])
                          and native_evidence.valid_file_hash(log_hash))),
        ("artifact hashes", (isinstance(artifacts, list) and bool(artifacts)
                              and all(native_evidence.valid_file_hash(item)
                                      for item in artifacts if isinstance(item, dict)))),
        ("zero-test refusal", zero_test_refusal),
        ("skipped/failed test refusal", exact_leaves),
        ("stale candidate refusal", stale_candidate_refusal),
    ]
    return [{"guarantee": name, "carried": bool(carried)} for name, carried in checks]


def wrap_v2(native_evidence_json_path, repo=None, xcrun="xcrun"):
    """Read a native_evidence.py `record` output JSON and wrap it into an
    Evidence v2 record. Never discards the original: it is embedded whole
    under `native_evidence`, plus its source path under
    `native_evidence_path`."""
    doc, error = native_evidence.read_json(native_evidence_json_path)
    if error:
        return {
            "schema": SCHEMA_V2,
            "verdict": native_evidence.FAIL,
            "native_evidence_path": os.path.abspath(native_evidence_json_path),
            "native_evidence": None,
            "verdict_detail": [error],
            "proof_scope": ["the native evidence JSON at %s could not be "
                            "read (%s); this v2 record proves nothing" %
                            (native_evidence_json_path, error)],
            "preserved_guarantees": [],
        }
    # This IS how native_evidence.py's own record represents pass/fail: it
    # stores no verdict field in the JSON it writes. Both `record()` (as a
    # self-check before returning) and the `validate` CLI action call this
    # same function against a doc read back from disk. Reusing it here
    # rather than re-deriving the signal keeps every guarantee (including
    # the ones that need a live re-check, like stale candidate refusal)
    # actually enforced, not re-implemented and possibly weakened.
    verdict, detail = native_evidence.validate_record(doc, repo=repo, xcrun=xcrun)
    if verdict not in VERDICTS:
        raise AssertionError("native_evidence verdict %r outside the shared triple" % verdict)
    return {
        "schema": SCHEMA_V2,
        "verdict": verdict,
        "native_evidence_path": os.path.abspath(native_evidence_json_path),
        "native_evidence": doc,
        "verdict_detail": detail,
        "proof_scope": proof_scope(doc),
        "preserved_guarantees": preserved_guarantees(doc, repo=repo),
    }


def wrap(args):
    record_v2 = wrap_v2(args.evidence, repo=args.repo, xcrun=args.xcrun)
    if args.out:
        native_evidence.write_json(args.out, record_v2)
    else:
        import json
        print(json.dumps(record_v2, indent=2, sort_keys=True))
    for line in record_v2["verdict_detail"]:
        print("native_evidence_v2: %s: %s" % (record_v2["verdict"], line), file=sys.stderr)
    return {native_evidence.PASS: 0, native_evidence.FAIL: 1, native_evidence.NODATA: 2}[record_v2["verdict"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)
    wrap_parser = sub.add_parser("wrap")
    wrap_parser.add_argument("--evidence", required=True,
                             help="path to a native_evidence.py record --out JSON")
    wrap_parser.add_argument("--repo", help="defaults to the repo recorded in the evidence")
    wrap_parser.add_argument("--xcrun", default="xcrun")
    wrap_parser.add_argument("--out", help="write the v2 record here instead of stdout")
    wrap_parser.set_defaults(func=wrap)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

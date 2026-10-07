"""Validate the L4.5 harness efficiency assessment document.

The validator is a control, not a report: unknown, corrupt or missing input
returns a non empty list of error strings and BLOCKS. REQ-01
BASELINE-CITATION, REQ-02 MEASURED-DELTA-SAME-DEFINITION, REQ-10
BASELINE-EXPIRY, REQ-11 HUMAN-APPROVAL and REQ-12 FAIL-CLOSED-UNKNOWN are
enforced here.

Standard library only, Python 3.9 compatible.
"""

import json
import os
import re
import sys

__all__ = [
    "MECHANISM_IDS",
    "main",
    "validate_assessment",
]

MECHANISM_IDS = (
    "action_fusion",
    "online_context_compact",
    "observation_pack",
    "evidence_preserving_reducer",
)

CITATION_FIELDS = (
    "paper_ref",
    "source_path",
    "source_sha256",
    "source_quote",
    "retrieved_at",
    "source_version",
    "excerpt_path",
)

MEASUREMENT_FIELDS = (
    "task",
    "tree_hash",
    "counter_name",
    "counter_version",
    "total_tokens",
    "raw_log_sha256",
)

PROPOSAL_FIELDS = (
    "mechanism_id",
    "applies",
    "before_run_id",
    "after_run_id",
    "delta_tokens",
    "delta_percent",
    "preservation_proof",
)

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_ISO_DATE_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _reject_json_constant(value):
    raise ValueError("non finite json constant %s" % (value,))


def _resolve_excerpt(assessment_path, excerpt_path):
    if os.path.isabs(excerpt_path):
        return excerpt_path
    base = os.path.dirname(os.path.abspath(assessment_path))
    return os.path.normpath(os.path.join(base, excerpt_path))


def validate_assessment(path):
    """Return a list of error strings; an empty list means the report passes.

    A non string, empty, missing, non regular, non utf-8 or empty file BLOCKS.
    A concurrent write during the read BLOCKS. The document must carry a
    fenced json block naming the four SoL-Pi mechanisms with complete
    citations, measurements whose totals are backed by a raw log SHA-256,
    exactly four proposals whose deltas match their measurements, non empty
    preservation proofs for every applying proposal, no estimated numbers, no
    automatic gate write, and a human sign off that is not older than the
    baseline it approves.
    """
    if not isinstance(path, str):
        return [
            "validate_assessment: path must be a str, got %s"
            % (type(path).__name__,)
        ]
    if not path.strip():
        return ["validate_assessment: path must not be empty"]
    if not os.path.exists(path):
        return ["validate_assessment: file does not exist: %s" % path]
    if not os.path.isfile(path):
        return ["validate_assessment: not a regular file: %s" % path]

    try:
        handle = open(path, "rb")
    except OSError as exc:
        return ["validate_assessment: cannot read %s: %s" % (path, exc)]

    try:
        with handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        return ["validate_assessment: cannot read %s: %s" % (path, exc)]

    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return [
            "validate_assessment: %s changed while it was being read "
            "(concurrent write); NO-DATA" % path
        ]

    if not raw.strip():
        return ["validate_assessment: assessment file is empty: %s" % path]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ["validate_assessment: %s is not utf-8 text: %s" % (path, exc)]

    match = _JSON_BLOCK_RE.search(text)
    if match is None:
        return ["validate_assessment: no fenced json block found in %s" % path]
    json_text = match.group(1)
    try:
        document = json.loads(json_text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        return ["validate_assessment: corrupt json in %s: %s" % (path, exc)]
    if not isinstance(document, dict):
        return ["validate_assessment: json block in %s must be an object" % path]

    errors = []

    if "estimate" in json_text.lower():
        errors.append(
            "validate_assessment: estimated numbers are forbidden; the report "
            "block names an estimate, so it is NO-DATA (REQ-12)"
        )

    if document.get("auto_gate_write", None) is not False:
        errors.append(
            "validate_assessment: auto_gate_write must be present and exactly "
            "false; L4 never writes scripts/gate_order.py output back into "
            "scripts/required_fast.sh (REQ-07)"
        )

    mechanisms = document.get("mechanisms")
    if not isinstance(mechanisms, dict):
        errors.append(
            "validate_assessment: mechanisms must be an object naming the "
            "four SoL-Pi mechanisms"
        )
        mechanisms = {}
    else:
        missing = [mid for mid in MECHANISM_IDS if mid not in mechanisms]
        if missing:
            errors.append(
                "validate_assessment: missing mechanisms: %s" % ", ".join(missing)
            )
        extra = sorted(key for key in mechanisms if key not in MECHANISM_IDS)
        if extra:
            errors.append(
                "validate_assessment: unknown mechanisms: %s" % ", ".join(extra)
            )

    for mid in MECHANISM_IDS:
        entry = mechanisms.get(mid) if isinstance(mechanisms, dict) else None
        if entry is None:
            continue
        if not isinstance(entry, dict):
            errors.append(
                "validate_assessment: mechanism %s must be an object" % mid
            )
            continue
        for field in CITATION_FIELDS:
            if field not in entry:
                errors.append(
                    "validate_assessment: mechanism %s missing citation field "
                    "%s (REQ-01)" % (mid, field)
                )
                continue
            value = entry[field]
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    "validate_assessment: mechanism %s field %s must be a non "
                    "empty string (REQ-01)" % (mid, field)
                )
                continue
            if field == "source_sha256" and not _SHA256_RE.match(value):
                errors.append(
                    "validate_assessment: mechanism %s source_sha256 must be "
                    "64 lowercase hex" % mid
                )
            if field == "retrieved_at" and not _ISO_DATE_RE.match(value):
                errors.append(
                    "validate_assessment: mechanism %s retrieved_at must be an "
                    "ISO date" % mid
                )
        excerpt = entry.get("excerpt_path")
        quote = entry.get("source_quote")
        if (isinstance(excerpt, str) and excerpt.strip()
                and isinstance(quote, str) and quote):
            resolved = _resolve_excerpt(path, excerpt)
            if not os.path.exists(resolved):
                errors.append(
                    "validate_assessment: mechanism %s excerpt_path does not "
                    "exist: %s (REQ-10)" % (mid, excerpt)
                )
            elif not os.path.isfile(resolved):
                errors.append(
                    "validate_assessment: mechanism %s excerpt_path is not a "
                    "regular file: %s (REQ-10)" % (mid, excerpt)
                )
            else:
                try:
                    with open(resolved, "rb") as excerpt_handle:
                        excerpt_text = excerpt_handle.read().decode("utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    errors.append(
                        "validate_assessment: mechanism %s excerpt cannot be "
                        "read: %s" % (mid, exc)
                    )
                else:
                    if quote not in excerpt_text:
                        errors.append(
                            "validate_assessment: mechanism %s source_quote "
                            "does not appear in its excerpt (REQ-01, REQ-10)"
                            % mid
                        )

    measurements = document.get("measurements")
    if not isinstance(measurements, dict):
        errors.append(
            "validate_assessment: measurements must be an object keyed by run "
            "id"
        )
        measurements = {}
    else:
        for run_id, measurement in measurements.items():
            if not isinstance(run_id, str) or not run_id.strip():
                errors.append(
                    "validate_assessment: measurement run id must be a non "
                    "empty string, got %r" % (run_id,)
                )
                continue
            if not isinstance(measurement, dict):
                errors.append(
                    "validate_assessment: measurement %s must be an object"
                    % run_id
                )
                continue
            for field in MEASUREMENT_FIELDS:
                if field not in measurement:
                    errors.append(
                        "validate_assessment: measurement %s missing field %s "
                        "(REQ-02)" % (run_id, field)
                    )
                    continue
                value = measurement[field]
                if field == "total_tokens":
                    if isinstance(value, bool) or not isinstance(value, int):
                        errors.append(
                            "validate_assessment: measurement %s total_tokens "
                            "must be an int" % run_id
                        )
                    elif value < 0:
                        errors.append(
                            "validate_assessment: measurement %s total_tokens "
                            "must be non negative" % run_id
                        )
                    continue
                if field == "raw_log_sha256":
                    if not isinstance(value, str) or not _SHA256_RE.match(value):
                        errors.append(
                            "validate_assessment: measurement %s "
                            "raw_log_sha256 must be 64 lowercase hex (REQ-02)"
                            % run_id
                        )
                    continue
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        "validate_assessment: measurement %s field %s must be "
                        "a non empty string" % (run_id, field)
                    )

    proposals = document.get("proposals")
    if not isinstance(proposals, list):
        errors.append(
            "validate_assessment: proposals must be a list of exactly four "
            "entries"
        )
        proposals = []
    else:
        if len(proposals) != len(MECHANISM_IDS):
            errors.append(
                "validate_assessment: proposals must contain exactly four "
                "entries, found %d" % len(proposals)
            )
        seen = set()
        for proposal in proposals:
            if not isinstance(proposal, dict):
                errors.append(
                    "validate_assessment: every proposal must be an object"
                )
                continue
            mid = proposal.get("mechanism_id")
            if mid not in MECHANISM_IDS:
                errors.append(
                    "validate_assessment: proposal has unknown mechanism_id "
                    "%r" % (mid,)
                )
                continue
            if mid in seen:
                errors.append(
                    "validate_assessment: duplicate proposal for %s" % mid
                )
            seen.add(mid)
            for field in PROPOSAL_FIELDS:
                if field not in proposal:
                    errors.append(
                        "validate_assessment: proposal %s missing field %s"
                        % (mid, field)
                    )
            applies = proposal.get("applies")
            if not isinstance(applies, bool):
                errors.append(
                    "validate_assessment: proposal %s applies must be a bool"
                    % mid
                )
                continue
            before_id = proposal.get("before_run_id")
            after_id = proposal.get("after_run_id")
            if not isinstance(before_id, str) or not before_id:
                errors.append(
                    "validate_assessment: proposal %s before_run_id must be a "
                    "non empty string (REQ-02)" % mid
                )
                continue
            if not isinstance(after_id, str) or not after_id:
                errors.append(
                    "validate_assessment: proposal %s after_run_id must be a "
                    "non empty string (REQ-02)" % mid
                )
                continue
            if before_id not in measurements:
                errors.append(
                    "validate_assessment: proposal %s before_run_id %s is not "
                    "in measurements (REQ-02)" % (mid, before_id)
                )
                continue
            if after_id not in measurements:
                errors.append(
                    "validate_assessment: proposal %s after_run_id %s is not "
                    "in measurements (REQ-02)" % (mid, after_id)
                )
                continue
            before_record = measurements[before_id]
            after_record = measurements[after_id]
            if not (isinstance(before_record, dict)
                    and isinstance(after_record, dict)):
                continue
            for field in ("task", "tree_hash", "counter_name", "counter_version"):
                if before_record.get(field) != after_record.get(field):
                    errors.append(
                        "validate_assessment: proposal %s before and after "
                        "differ on %s (REQ-02)" % (mid, field)
                    )
            before_total = before_record.get("total_tokens")
            after_total = after_record.get("total_tokens")
            if isinstance(before_total, bool) or not isinstance(before_total, int):
                continue
            if isinstance(after_total, bool) or not isinstance(after_total, int):
                continue
            if before_total <= 0:
                errors.append(
                    "validate_assessment: proposal %s before total_tokens "
                    "must be greater than zero (REQ-02)" % mid
                )
                continue
            delta_tokens = after_total - before_total
            expected_delta = proposal.get("delta_tokens")
            if expected_delta != delta_tokens:
                errors.append(
                    "validate_assessment: proposal %s delta_tokens %r does "
                    "not equal %d (REQ-02)" % (mid, expected_delta, delta_tokens)
                )
            expected_percent = 100.0 * delta_tokens / before_total
            actual_percent = proposal.get("delta_percent")
            if isinstance(actual_percent, bool) or not isinstance(
                    actual_percent, (int, float)):
                errors.append(
                    "validate_assessment: proposal %s delta_percent must be a "
                    "number" % mid
                )
            elif abs(actual_percent - expected_percent) > 1e-6:
                errors.append(
                    "validate_assessment: proposal %s delta_percent %r does "
                    "not equal %.6f (REQ-02)"
                    % (mid, actual_percent, expected_percent)
                )
            elif abs(actual_percent) == 100.0:
                after_sha = after_record.get("raw_log_sha256")
                if not (isinstance(after_sha, str)
                        and _SHA256_RE.match(after_sha)):
                    errors.append(
                        "validate_assessment: proposal %s claims a 100 "
                        "percent count without a matching raw_log_sha256 "
                        "(REQ-02)" % mid
                    )
            if applies:
                proof = proposal.get("preservation_proof")
                if not isinstance(proof, str) or not proof.strip():
                    errors.append(
                        "validate_assessment: proposal %s applies=true but "
                        "preservation_proof is empty (REQ-03 PRESERVE-KEEP)"
                        % mid
                    )

    sign_off = document.get("human_sign_off")
    if not isinstance(sign_off, dict):
        errors.append(
            "validate_assessment: human_sign_off must be an object with a "
            "name and an ISO date (REQ-11)"
        )
    else:
        name = sign_off.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(
                "validate_assessment: human_sign_off name must be a non empty "
                "string (REQ-11)"
            )
        date = sign_off.get("date")
        if not isinstance(date, str) or not _ISO_DATE_RE.match(date):
            errors.append(
                "validate_assessment: human_sign_off date must be an ISO date "
                "(REQ-11)"
            )
        else:
            latest = ""
            for mid in MECHANISM_IDS:
                entry = mechanisms.get(mid) if isinstance(mechanisms, dict) else None
                if isinstance(entry, dict):
                    retrieved = entry.get("retrieved_at")
                    if (isinstance(retrieved, str)
                            and _ISO_DATE_RE.match(retrieved)
                            and retrieved > latest):
                        latest = retrieved
            if latest and date < latest:
                errors.append(
                    "validate_assessment: human sign off date %s is stale, "
                    "older than baseline %s (REQ-10, REQ-11)" % (date, latest)
                )

    return errors


def main(argv=None):
    """Command line entry point: check_harness_efficiency_assessment PATH.

    Returns 0 and prints ASSESSMENT OK when the report is complete, 2 when any
    argument or any part of the report is NO-DATA. A hostile argv lands as
    exit code 2, never a crash.
    """
    if argv is None:
        argv = list(sys.argv[1:])
    if not isinstance(argv, (list, tuple)):
        sys.stderr.write(
            "NO-DATA: check_harness_efficiency_assessment: argv must be a list "
            "or tuple of strings\n"
        )
        return 2
    args = list(argv)
    for item in args:
        if not isinstance(item, str):
            sys.stderr.write(
                "NO-DATA: check_harness_efficiency_assessment: argv must be a "
                "list or tuple of strings\n"
            )
            return 2
    if len(args) != 1:
        sys.stderr.write(
            "NO-DATA: check_harness_efficiency_assessment: usage: "
            "check_harness_efficiency_assessment ASSESSMENT_PATH\n"
        )
        return 2
    errors = validate_assessment(args[0])
    if errors:
        for message in errors:
            sys.stderr.write("NO-DATA: %s\n" % message)
        return 2
    sys.stdout.write("ASSESSMENT OK\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

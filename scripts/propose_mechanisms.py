#!/usr/bin/env python3
"""L4.4 mechanism mapping and proposal.

Evaluates each of the four SoL-Pi inspired mechanisms against Brother's
own harness and returns exactly four MechanismProposal records, one per
mechanism, and writes them to /tmp/proposals.json.

Standard library only. Wrong argument types are refused with ValueError.
Missing, stale or corrupt data BLOCKS or is NO-DATA, never a pass.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import List, Literal


class NoDataError(ValueError):
    """Raised when data is missing, stale or corrupt: NO-DATA, never PASS.

    A ValueError, like every documented refusal (FX-08, scripts/test_refusal_classes.py): the landing fuzz reads a
    ValueError as a refusal, and a plain Exception as a crash."""
    pass


MECHANISM_IDS = (
    "action_fusion",
    "online_context_compact",
    "observation_pack",
    "evidence_preserving_reducer",
)

GATED_FILES = (
    "scripts/required_fast.sh",
    "scripts/gate_order.py",
)

CITATION_FIELDS = (
    "paper_ref",
    "source_path",
    "source_sha256",
    "source_quote",
    "retrieval_date",
    "source_version",
)

MEASUREMENT_REQUIRED = (
    "task",
    "tree_hash",
    "counter_name",
    "counter_version",
    "log",
    "total_tokens",
)

PROPOSALS_PATH = "/tmp/proposals.json"
LOCK_PATH = "/tmp/proposals.json.lock"


@dataclass(frozen=True)
class MechanismProposal:
    proposal_id: str
    mechanism_id: Literal[
        "action_fusion",
        "online_context_compact",
        "observation_pack",
        "evidence_preserving_reducer",
    ]
    applies: bool
    current_behavior_change: str
    changed_file: str
    changed_function: str
    before_run_id: str
    after_run_id: str
    delta_tokens: int
    delta_percent: float
    risk: str
    rollback: str
    preservation_proof: str
    status: Literal["PROPOSED", "REJECTED", "BLOCKED"]


def _is_nonempty_str(value):
    return isinstance(value, str) and value != ""


def _blocked(proposal_id, mechanism_id):
    return MechanismProposal(
        proposal_id=proposal_id,
        mechanism_id=mechanism_id,
        applies=False,
        current_behavior_change="",
        changed_file="",
        changed_function="",
        before_run_id="",
        after_run_id="",
        delta_tokens=0,
        delta_percent=0.0,
        risk="",
        rollback="",
        preservation_proof="",
        status="BLOCKED",
    )


def _citation_for(baseline, mechanism_id):
    if "mechanisms" in baseline:
        return baseline["mechanisms"].get(mechanism_id)
    return baseline.get(mechanism_id)


def _validate_measurement(measurements, run_id):
    if run_id not in measurements:
        raise NoDataError("run_id %r is not in measurements" % (run_id,))
    record = measurements[run_id]
    if not isinstance(record, dict):
        raise ValueError("measurement %r must be a dict" % (run_id,))
    for field in MEASUREMENT_REQUIRED:
        if field not in record:
            raise NoDataError("measurement %r missing %s" % (run_id, field))
        value = record[field]
        if field == "total_tokens":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("measurement %r %s must be an int" % (run_id, field))
            if value < 0:
                raise NoDataError("measurement %r total_tokens is negative" % (run_id,))
        else:
            if not isinstance(value, str):
                raise ValueError("measurement %r %s must be a string" % (run_id, field))
            if value == "":
                raise NoDataError("measurement %r %s is empty" % (run_id, field))
    return record


def _write_proposals(proposals):
    try:
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise NoDataError(
            "concurrent write to %s is already in progress" % PROPOSALS_PATH
        )
    except OSError as exc:
        raise NoDataError("cannot take the proposal lock: %s" % exc)
    created_lock = True
    try:
        directory = os.path.dirname(PROPOSALS_PATH) or "."
        handle_fd, temp_path = tempfile.mkstemp(prefix=".proposals.", dir=directory)
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(
                    [asdict(p) for p in proposals],
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
            os.replace(temp_path, PROPOSALS_PATH)
        except OSError as exc:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise NoDataError("cannot write %s: %s" % (PROPOSALS_PATH, exc))
    finally:
        if created_lock:
            os.close(lock_fd)
            try:
                os.unlink(LOCK_PATH)
            except OSError:
                pass


def read_measurements(path):
    """Read a measurements JSON file. Corrupt or missing raises NoDataError."""
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        raise NoDataError("cannot read measurements %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise NoDataError("corrupt measurements JSON %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise NoDataError("measurements JSON must be an object")
    return data


def propose_mechanisms(baseline, current, measurements) -> List[MechanismProposal]:
    if not isinstance(baseline, dict):
        raise ValueError("baseline must be a dict, got %s" % type(baseline).__name__)
    if not isinstance(current, list):
        raise ValueError("current must be a list, got %s" % type(current).__name__)
    if not isinstance(measurements, dict):
        raise ValueError("measurements must be a dict, got %s" % type(measurements).__name__)

    if baseline:
        if "mechanisms" in baseline:
            container = baseline["mechanisms"]
            if not isinstance(container, dict):
                raise ValueError("baseline['mechanisms'] must be a dict")
            for key in container:
                if key not in MECHANISM_IDS:
                    raise NoDataError("unknown baseline mechanism_id %r" % (key,))
            for key in baseline:
                if key != "mechanisms":
                    raise NoDataError("unknown baseline key %r" % (key,))
        else:
            for key in baseline:
                if key not in MECHANISM_IDS:
                    raise NoDataError("unknown baseline mechanism_id %r" % (key,))

    current_map = {}
    for record in current:
        if not isinstance(record, dict):
            raise ValueError("every current record must be a dict")
        mid = record.get("mechanism_id")
        if mid is None:
            raise NoDataError("current record missing mechanism_id")
        if not isinstance(mid, str):
            raise ValueError("mechanism_id must be a string")
        if mid not in MECHANISM_IDS:
            raise NoDataError("unknown mechanism_id %r" % (mid,))
        if mid in current_map:
            raise NoDataError("duplicate mechanism_id %r" % (mid,))
        current_map[mid] = record

    if not baseline or not current:
        proposals = [_blocked("L4.4-" + m, m) for m in MECHANISM_IDS]
        _write_proposals(proposals)
        return proposals

    proposals = []
    for mechanism_id in MECHANISM_IDS:
        proposal_id = "L4.4-" + mechanism_id
        citation = _citation_for(baseline, mechanism_id)
        if not isinstance(citation, dict):
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue
        bad = False
        for field in CITATION_FIELDS:
            if field not in citation:
                bad = True
                break
            value = citation[field]
            if not isinstance(value, str):
                raise ValueError("citation field %s must be a string" % field)
            if value == "":
                bad = True
                break
        if bad:
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue

        behavior = current_map.get(mechanism_id)
        if behavior is None:
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue

        def get_str(key):
            if key not in behavior:
                return None
            value = behavior[key]
            if not isinstance(value, str):
                raise ValueError("current record %s must be a string" % key)
            if value == "":
                return None
            return value

        change = get_str("current_behavior")
        changed_file = get_str("changed_file")
        changed_function = get_str("changed_function")
        before_run_id = get_str("before_run_id")
        after_run_id = get_str("after_run_id")

        if None in (change, changed_file, changed_function, before_run_id, after_run_id):
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue

        if changed_file in GATED_FILES:
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue

        if before_run_id not in measurements or after_run_id not in measurements:
            proposals.append(_blocked(proposal_id, mechanism_id))
            continue

        before = _validate_measurement(measurements, before_run_id)
        after = _validate_measurement(measurements, after_run_id)

        for field in ("task", "tree_hash", "counter_name", "counter_version"):
            if before[field] != after[field]:
                raise NoDataError(
                    "%s: before and after do not share the same %s" % (mechanism_id, field)
                )

        before_total = before["total_tokens"]
        after_total = after["total_tokens"]
        if before_total <= 0:
            raise NoDataError(
                "%s: before total_tokens must be greater than zero" % mechanism_id
            )
        delta_tokens = after_total - before_total
        delta_percent = 100.0 * delta_tokens / before_total

        applies = delta_tokens < 0
        if applies:
            risk = get_str("risk")
            rollback = get_str("rollback")
            preservation_proof = get_str("preservation_proof")
            if None in (risk, rollback, preservation_proof):
                proposals.append(_blocked(proposal_id, mechanism_id))
                continue
        else:
            risk = ""
            rollback = ""
            preservation_proof = ""

        proposals.append(
            MechanismProposal(
                proposal_id=proposal_id,
                mechanism_id=mechanism_id,
                applies=applies,
                current_behavior_change=change,
                changed_file=changed_file,
                changed_function=changed_function,
                before_run_id=before_run_id,
                after_run_id=after_run_id,
                delta_tokens=delta_tokens,
                delta_percent=delta_percent,
                risk=risk,
                rollback=rollback,
                preservation_proof=preservation_proof,
                status="PROPOSED" if applies else "REJECTED",
            )
        )

    if len(proposals) != len(MECHANISM_IDS):
        raise NoDataError("propose_mechanisms did not produce four proposals")

    _write_proposals(proposals)
    return proposals

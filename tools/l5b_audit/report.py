"""L5b.6 report emitter and self-mutation proof.

Assembles boundary-call scan results, lint hits, fire records, exemptions and
fail-closed probes into an AuditReport, emits the JSON, doc and patch files
atomically, and proves three internal mutations fire.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Tuple

from tools.l5b_audit import lint_rules
from tools.l5b_audit import score
from tools.l5b_audit import scanner


SCHEME_VERSION = "l5b-v1"
REASON_COMPLETE_PASS = "COMPLETE_PASS"
REASON_SCAN_ERROR = "SCAN_ERROR"
REASON_CHECKER_MISMATCH = "CHECKER_MISMATCH"
REASON_NO_DATA = "NO_DATA"


class AuditInputError(ValueError):
    """Raised before any work when an argument is not the expected shape."""


@dataclass(frozen=True)
class SkipInventory:
    files_seen: int
    files_skipped: int
    skip_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class AuditHit:
    hit_id: str
    rule_id: str
    kind: str
    severity: str
    enclosing_function: str


@dataclass(frozen=True)
class FireRecord:
    entry_id: str
    fired: bool
    reason: str


@dataclass(frozen=True)
class AuditReport:
    scheme_version: str
    boundary_calls_total: int
    boundary_calls_stated: int
    boundary_calls_tested: int
    hits_unexempted: int
    fail_closed: dict
    blocked: bool
    reason: str
    exemptions: Tuple
    exemptions_applied: int
    annotation_clearances: Tuple
    fixes_applied: Tuple
    proposed_patches: Tuple
    skips: SkipInventory
    fires: Tuple
    hits: Tuple
    score_decimal: float
    report_hash: str
    produced_at: str
    # L5b.8: the record names what it measured, so a recheck can re-derive it
    # (the record keys are root, fires, fires_sha256, fire_map, fire_map_sha256; the
    # fields carry _path because `fires` above is the tuple of fire records)
    root: str = ""
    fires_path: str = ""
    fires_sha256: str = ""
    fire_map_path: str = ""
    fire_map_sha256: str = ""


def _require_audit_report(value):
    if not isinstance(value, AuditReport):
        raise AuditInputError("report must be an AuditReport, got %s" % type(value).__name__)


def _require_non_empty_str(label, value):
    if not isinstance(value, str) or not value:
        raise AuditInputError("%s must be a non-empty string" % label)


def _exemption_dict(entry):
    return {
        "hit_id": entry.hit_id,
        "reason": entry.reason,
        "approver": entry.approver,
        "approved_at": entry.approved_at,
    }


def _clearance_dict(entry):
    return {
        "call_id": entry["call_id"],
        "covered_by": entry["covered_by"],
    }


def _report_body(report):
    ex_list = [_exemption_dict(entry) for entry in report.exemptions]
    clear_list = [_clearance_dict(entry) for entry in report.annotation_clearances]
    return {
        "scheme_version": report.scheme_version,
        "boundary_calls_total": report.boundary_calls_total,
        "boundary_calls_stated": report.boundary_calls_stated,
        "boundary_calls_tested": report.boundary_calls_tested,
        "hits_unexempted": report.hits_unexempted,
        "fail_closed": dict(report.fail_closed),
        "blocked": report.blocked,
        "reason": report.reason,
        "exemptions": ex_list,
        "exemptions_applied": report.exemptions_applied,
        "annotation_clearances": clear_list,
        "fixes_applied": list(report.fixes_applied),
        "proposed_patches": list(report.proposed_patches),
        "score_decimal": report.score_decimal,
        "skips": {
            "files_seen": report.skips.files_seen,
            "files_skipped": report.skips.files_skipped,
            "skip_reasons": list(report.skips.skip_reasons),
        },
        "root": report.root,
        "fires": report.fires_path,
        "fires_sha256": report.fires_sha256,
        "fire_map": report.fire_map_path,
        "fire_map_sha256": report.fire_map_sha256,
    }


def _atomic_write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    fd, temp_path = tempfile.mkstemp(prefix=".l5b6-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _render_doc(report, digest):
    lines = [
        "# L5b Reliability Audit",
        "",
        "report_hash: %s" % digest,
        "",
        "boundary_calls_total: %d" % report.boundary_calls_total,
        "boundary_calls_stated: %d" % report.boundary_calls_stated,
        "boundary_calls_tested: %d" % report.boundary_calls_tested,
        "hits_unexempted: %d" % report.hits_unexempted,
        "reason: %s" % report.reason,
        "score_decimal: %s" % report.score_decimal,
        "",
    ]
    return "\n".join(lines)


def _render_patch(report):
    chunks = []
    for patch in report.proposed_patches:
        if isinstance(patch, dict):
            diff = patch.get("patch_unified_diff")
            if isinstance(diff, str):
                chunks.append(diff)
    if not chunks:
        return ""
    return "\n".join(chunks) + "\n"


def emit(report, doc_path, json_path, patch_path):
    """Write JSON, doc and patch atomically, and return the report hash."""
    _require_audit_report(report)
    _require_non_empty_str("doc_path", doc_path)
    _require_non_empty_str("json_path", json_path)
    _require_non_empty_str("patch_path", patch_path)
    for path in (doc_path, json_path, patch_path):
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            raise AuditInputError("missing parent directory for %s" % path)
    body = _report_body(report)
    payload = json.dumps(body, ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    full = dict(body)
    full["report_hash"] = digest
    full["produced_at"] = report.produced_at
    json_text = json.dumps(full, ensure_ascii=True, sort_keys=True) + "\n"
    _atomic_write_text(json_path, json_text)
    _atomic_write_text(doc_path, _render_doc(report, digest))
    _atomic_write_text(patch_path, _render_patch(report))
    return digest


def assemble(scan, lint, fires, exemptions, probes, skips):
    """Validate every input then build an AuditReport from the recorded facts."""
    if not isinstance(scan, tuple):
        raise AuditInputError("scan must be a tuple")
    if not isinstance(lint, tuple):
        raise AuditInputError("lint must be a tuple")
    if not isinstance(fires, tuple):
        raise AuditInputError("fires must be a tuple")
    if not isinstance(exemptions, tuple):
        raise AuditInputError("exemptions must be a tuple")
    if not isinstance(probes, tuple):
        raise AuditInputError("probes must be a tuple")
    if not isinstance(skips, SkipInventory):
        raise AuditInputError("skips must be a SkipInventory")
    for entry in scan:
        if not isinstance(entry, scanner.BoundaryCall):
            raise AuditInputError("scan entries must be BoundaryCall")
    for entry in fires:
        if not isinstance(entry, FireRecord):
            raise AuditInputError("fires entries must be FireRecord")
    for entry in probes:
        if not isinstance(entry, score.FailClosedProbe):
            raise AuditInputError("probes entries must be FailClosedProbe")
    for entry in exemptions:
        if not isinstance(entry, score.Exemption):
            raise AuditInputError("exemptions entries must be Exemption")

    cleared = set()
    for entry in exemptions:
        if entry.invalid_reason is None:
            cleared.add(entry.hit_id)

    unexempted = 0
    for hit in lint:
        if hit.hit_id not in cleared:
            unexempted += 1

    total = len(scan)
    stated = total
    tested = 0
    for entry in fires:
        if entry.fired:
            tested += 1

    result = score.compute(total, stated, tested, probes, unexempted)

    fail_closed = {"empty": False, "corrupt": False, "unknown": False}
    for probe in probes:
        if probe.name in fail_closed:
            fail_closed[probe.name] = probe.passed

    blocked = False
    reason = REASON_COMPLETE_PASS
    if result.reason == score.REASON_NO_DATA:
        reason = REASON_NO_DATA
        blocked = True
    elif result.reason == score.REASON_UNRESOLVED_HITS:
        reason = "UNRESOLVED_HITS"
        blocked = True

    return AuditReport(
        scheme_version=SCHEME_VERSION,
        boundary_calls_total=total,
        boundary_calls_stated=stated,
        boundary_calls_tested=tested,
        hits_unexempted=unexempted,
        fail_closed=fail_closed,
        blocked=blocked,
        reason=reason,
        exemptions=tuple(exemptions),
        exemptions_applied=len(cleared),
        annotation_clearances=(),
        fixes_applied=(),
        proposed_patches=(),
        skips=skips,
        fires=tuple(fires),
        hits=tuple(lint),
        score_decimal=result.score,
        report_hash="",
        produced_at="",
    )


def _mutant_score_empty(total, stated, tested, probes, unexempted_hits):
    if total == 0:
        return score.ScoreResult(score=10.0, reason=score.REASON_NO_DATA)
    return score.compute(total, stated, tested, probes, unexempted_hits)


def _mutant_score_fail_open(total, stated, tested, probes, unexempted_hits):
    return score.compute(total, stated, tested, probes, 0)


def _mutant_rule_except_pass(fn, call):
    return False


_EXCEPT_PASS_SAMPLE = (
    "def f():\n"
    "    try:\n"
    "        open('x')\n"
    "    except OSError:\n"
    "        pass\n"
)


def _assertion_empty():
    result = score.compute(0, 0, 0, (), 0)
    return result.score == 0.0 and result.reason == score.REASON_NO_DATA


def _assertion_fail_open():
    probes = tuple(score.FailClosedProbe(n, 0, "BLOCK") for n in ("a", "b", "c"))
    result = score.compute(1, 1, 1, probes, 1)
    return result.score == 0.0 and result.reason == score.REASON_UNRESOLVED_HITS


def _assertion_except_pass():
    tree = ast.parse(_EXCEPT_PASS_SAMPLE)
    calls = scanner.scan_source("sample.py", _EXCEPT_PASS_SAMPLE)
    if not calls:
        return False
    return lint_rules.rule_except_pass(tree, calls[0]) is True


def self_mutation_proof():
    """Run the three real assertions and their mutants in process."""
    fired = []
    order = []
    checks = (
        ("M-L5B-SCORE-EMPTY-01", _assertion_empty,
         lambda: _mutant_score_empty(0, 0, 0, (), 0).score == 0.0),
        ("M-L5B-FAIL-OPEN-01", _assertion_fail_open,
         lambda: _mutant_score_fail_open(
             1, 1, 1,
             tuple(score.FailClosedProbe(n, 0, "BLOCK")
                   for n in ("a", "b", "c")),
             1).score == 0.0),
        ("M-L5B-EXCEPT-PASS-01", _assertion_except_pass,
         lambda: _mutant_rule_except_pass(
             ast.parse(_EXCEPT_PASS_SAMPLE),
             scanner.scan_source("sample.py", _EXCEPT_PASS_SAMPLE)[0]) is True),
    )
    for name, real_assert, mutant_assert in checks:
        order.append(name)
        try:
            real_ok = bool(real_assert())
        except Exception:
            real_ok = False
        try:
            mutant_ok = bool(mutant_assert())
        except Exception:
            mutant_ok = False
        if real_ok and not mutant_ok:
            fired.append(name)
    if len(fired) == len(order):
        return (True, tuple(order))
    return (False, tuple(fired))

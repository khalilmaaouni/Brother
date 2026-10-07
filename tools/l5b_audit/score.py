"""L5b.4 exemption ledger: loader, validator and applier.

The exemption ledger is tools/l5b_audit/exemptions.json. Every entry
names a live LintHit by hit_id, carries a >= 20 character reason, a role
token approver, an ISO 8601 approved_at with a UTC offset no more than
24 hours old, and an HMAC-SHA256 approval_token over the canonical JSON
{"hit_id","reason","approver","approved_at"} keyed by L5B_APPROVAL_KEY.

A data path exemption (kind FILE_IO or JSON, rule L5B-EXCEPT-PASS or
L5B-FORCE-UNWRAP) is refused outright: a silent failure on a file read
or a JSON decode never clears. A missing or corrupt ledger, a missing
approval key, a duplicate hit_id, an unsorted ledger and an unknown hit
all block; nothing clears by absence.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, replace
from typing import Optional


REQUIRED_FIELDS = ("hit_id", "reason", "approver", "approved_at", "approval_token")
MIN_REASON_CHARS = 20
MAX_AGE_SECONDS = 24 * 3600
DATA_PATH_KINDS = frozenset({"FILE_IO", "JSON"})
DATA_PATH_RULES = frozenset({"L5B-EXCEPT-PASS", "L5B-FORCE-UNWRAP"})
ENV_KEY_NAME = "L5B_APPROVAL_KEY"


@dataclass(frozen=True)
class Exemption:
    hit_id: str
    reason: str
    approver: str
    approved_at: str
    approval_token: str
    invalid_reason: Optional[str] = None


def _canonical_body(hit_id, reason, approver, approved_at):
    return json.dumps(
        {
            "hit_id": hit_id,
            "reason": reason,
            "approver": approver,
            "approved_at": approved_at,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _token_for(hit_id, reason, approver, approved_at, key):
    body = _canonical_body(hit_id, reason, approver, approved_at)
    return hmac.new(key.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_approved_at(text):
    if not isinstance(text, str) or not text:
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _find_hit(live_hits, hit_id):
    for hit in live_hits:
        try:
            candidate = hit.hit_id
        except AttributeError:
            continue
        if candidate == hit_id:
            return hit
    return None


def _hit_kind_rule(hit):
    try:
        kind = hit.kind
    except AttributeError:
        kind = None
    try:
        rule_id = hit.rule_id
    except AttributeError:
        rule_id = None
    return kind, rule_id


def validate_exemption(ex, live_hits):
    """Return None when ex is a valid, live, in-date exemption; otherwise
    the rejection code that refuses it. Unknown, malformed or missing
    data blocks; nothing here treats an unclear input as the safe case."""
    if not isinstance(ex, Exemption):
        raise ValueError("ex must be an Exemption, got %s" % type(ex).__name__)
    if not isinstance(live_hits, tuple):
        raise ValueError("live_hits must be a tuple, got %s" % type(live_hits).__name__)
    for value in (ex.hit_id, ex.reason, ex.approver, ex.approved_at, ex.approval_token):
        if not isinstance(value, str):
            return "BAD_FIELDS"
    key = os.environ.get(ENV_KEY_NAME)
    if not key:
        return "NO_APPROVAL_KEY"
    if len(ex.reason) < MIN_REASON_CHARS:
        return "SHORT_REASON"
    stamps = _parse_approved_at(ex.approved_at)
    if stamps is None:
        return "BAD_TIME"
    now = time.time()
    if now - stamps > MAX_AGE_SECONDS:
        return "STALE"
    hit = _find_hit(live_hits, ex.hit_id)
    if hit is None:
        return "UNKNOWN_HIT"
    kind, rule_id = _hit_kind_rule(hit)
    if kind in DATA_PATH_KINDS and rule_id in DATA_PATH_RULES:
        return "DATA_PATH"
    expected = _token_for(ex.hit_id, ex.reason, ex.approver, ex.approved_at, key)
    if not hmac.compare_digest(expected, ex.approval_token):
        return "BAD_TOKEN"
    return None


def _read_ledger(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise ValueError("exemptions ledger could not be read: %s" % path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("exemptions ledger is not UTF-8: %s" % path)
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("exemptions ledger is not valid JSON: %s" % path)
    if not isinstance(data, list):
        raise ValueError("exemptions ledger must hold a JSON array")
    return data


def load_exemptions(path, live_hits):
    """Read, parse and validate the exemption ledger against live_hits.

    Every returned Exemption carries invalid_reason: None when the entry
    clears, otherwise the code that refused it. Duplicate hit_ids and an
    unsorted ledger refuse every entry they touch; they never clear."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(live_hits, tuple):
        raise ValueError("live_hits must be a tuple, got %s" % type(live_hits).__name__)
    data = _read_ledger(path)
    parsed = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("exemptions entry %d must be an object" % index)
        values = {}
        for field_name in REQUIRED_FIELDS:
            if field_name not in item:
                raise ValueError("exemptions entry %d missing %s" % (index, field_name))
            if not isinstance(item[field_name], str):
                raise ValueError("exemptions entry %d %s must be a string" % (index, field_name))
            values[field_name] = item[field_name]
        parsed.append(Exemption(
            hit_id=values["hit_id"],
            reason=values["reason"],
            approver=values["approver"],
            approved_at=values["approved_at"],
            approval_token=values["approval_token"],
        ))
    ids = [entry.hit_id for entry in parsed]
    sorted_ok = ids == sorted(ids)
    counts = {}
    for value in ids:
        counts[value] = counts.get(value, 0) + 1
    out = []
    for entry in parsed:
        reason = validate_exemption(entry, live_hits)
        if reason is None and counts.get(entry.hit_id, 0) > 1:
            reason = "DUPLICATE"
        if reason is None and not sorted_ok:
            reason = "UNSORTED"
        out.append(replace(entry, invalid_reason=reason))
    return tuple(out)


def apply_exemptions(hits, ex):
    """Split hits into (cleared, remaining) using valid exemptions only:
    an exemption whose invalid_reason is set clears nothing."""
    if not isinstance(hits, tuple):
        raise ValueError("hits must be a tuple, got %s" % type(hits).__name__)
    if not isinstance(ex, tuple):
        raise ValueError("ex must be a tuple, got %s" % type(ex).__name__)
    cleared_ids = set()
    for entry in ex:
        if not isinstance(entry, Exemption):
            raise ValueError("ex entries must be Exemption, got %s" % type(entry).__name__)
        if entry.invalid_reason is None:
            cleared_ids.add(entry.hit_id)
    cleared = []
    remaining = []
    for hit in hits:
        try:
            hit_id = hit.hit_id
        except AttributeError:
            raise ValueError("every hit must carry a hit_id attribute")
        if not isinstance(hit_id, str):
            raise ValueError("every hit.hit_id must be a string")
        if hit_id in cleared_ids:
            cleared.append(hit)
        else:
            remaining.append(hit)
    return tuple(cleared), tuple(remaining)


# ---------------------------------------------------------------------------
# L5b.5 rubric scorer. Appended beside the L5b.4 exemption ledger above; every
# existing name, default, return value and exit path stays exactly as it was.
# ---------------------------------------------------------------------------

MIN_RUBRIC_SCORE = 8.5
REASON_NO_DATA = "NO_DATA"
REASON_UNRESOLVED_HITS = "UNRESOLVED_HITS"
REASON_SCORED = "SCORED"

_RUBRIC_TEXT = (
    "L5b.5 rubric scorer.\n"
    "score = 4.0 * coverage + 3.0 * fail_closed_fraction(probes) + 3.0 * fired\n"
    "where coverage = stated / total and fired = tested / max(1, stated).\n"
    "total == 0 is NO_DATA with score 0.0.\n"
    "unexempted_hits > 0 is UNRESOLVED_HITS with score 0.0.\n"
    "Counts that cannot all be true, wrong types and foreign probes raise.\n"
    "A probe counts only when it exits 0 and prints a line beginning BLOCK.\n"
    "No probes is no evidence and earns no fail-closed credit.\n"
    "The unit closes at score >= 8.5.\n"
)


@dataclass(frozen=True)
class FailClosedProbe:
    """One probe observation of an empty, corrupt or unknown input case.

    passed is True only when exit_code is 0 and output carries a line whose
    first word is exactly BLOCK. Any other observation is passed=False and
    earns no fail-closed credit.
    """

    name: str
    exit_code: int
    output: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("probe name must be a non-empty string")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("probe exit_code must be an int, got %s" % type(self.exit_code).__name__)
        if not isinstance(self.output, str):
            raise ValueError("probe output must be a string, got %s" % type(self.output).__name__)

    @property
    def passed(self) -> bool:
        if self.exit_code != 0:
            return False
        for line in self.output.splitlines():
            words = line.split(None, 1)
            if words and words[0] == "BLOCK":
                return True
        return False


@dataclass(frozen=True)
class ScoreResult:
    """The rubric outcome: the score, the reason and the three axis values."""

    score: float
    reason: str
    coverage: float = 0.0
    fired: float = 0.0
    fail_closed: float = 0.0
    meets_bar: bool = False


def fail_closed_fraction(probes) -> float:
    """Fraction of probes that failed closed, over FailClosedProbe entries.

    No probes is no evidence, so the fraction is 0.0 and the 3.0 fail-closed
    weight is not earned. Anything that is not a tuple of FailClosedProbe
    records is refused with ValueError.
    """
    if not isinstance(probes, tuple):
        raise ValueError("probes must be a tuple, got %s" % type(probes).__name__)
    if not probes:
        return 0.0
    passed = 0
    for probe in probes:
        if not isinstance(probe, FailClosedProbe):
            raise ValueError("every probe must be a FailClosedProbe, got %s" % type(probe).__name__)
        if probe.passed:
            passed += 1
    return passed / len(probes)


def _require_count(label, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an int, got %s" % (label, type(value).__name__))
    if value < 0:
        raise ValueError("%s must not be negative, got %d" % (label, value))
    return value


def compute(total, stated, tested, probes, unexempted_hits) -> ScoreResult:
    """Score the audit on the three axes of the L5b rubric.

    score = 4.0 * coverage + 3.0 * fail_closed_fraction(probes) + 3.0 * fired
    with coverage = stated / total and fired = tested / max(1, stated).
    total == 0 is NO_DATA and unexempted_hits > 0 is UNRESOLVED_HITS; both
    score 0.0. Counts that cannot all be true, wrong types and foreign probe
    records are corrupt input and raise ValueError, never a safe score.
    """
    _require_count("total", total)
    _require_count("stated", stated)
    _require_count("tested", tested)
    _require_count("unexempted_hits", unexempted_hits)
    if stated > total:
        raise ValueError("stated %d cannot exceed total %d" % (stated, total))
    if tested > stated:
        raise ValueError("tested %d cannot exceed stated %d" % (tested, stated))
    closed = fail_closed_fraction(probes)
    coverage = stated / total if total else 0.0
    fired = tested / max(1, stated)
    if total == 0:
        return ScoreResult(score=0.0, reason=REASON_NO_DATA, coverage=0.0, fired=0.0, fail_closed=closed, meets_bar=False)
    if unexempted_hits > 0:
        return ScoreResult(score=0.0, reason=REASON_UNRESOLVED_HITS, coverage=coverage, fired=fired, fail_closed=closed, meets_bar=False)
    score = 4.0 * coverage + 3.0 * closed + 3.0 * fired
    return ScoreResult(score=score, reason=REASON_SCORED, coverage=coverage, fired=fired, fail_closed=closed, meets_bar=score >= MIN_RUBRIC_SCORE)


def score_docstring() -> str:
    """Return the rubric text that this scorer implements."""
    return _RUBRIC_TEXT


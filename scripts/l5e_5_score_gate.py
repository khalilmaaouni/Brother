"""L5e.5 score computation and gate for the L5e documentation accuracy audit.

The audit document carries one score row per checked area plus an overall
row.  ``score_audit_doc`` parses those rows, drops the overall row,
computes the weighted average of the per-area scores on the 0.0 to 10.0
scale and blocks with ValueError when the table is missing, when an entry
is not a finite number, when a score is outside 0.0 to 10.0, when the
total weight is not positive or when the weighted average is below the
8.5 gate.
"""

import math

_MIN_GATE = 8.5
_MIN_SCORE = 0.0
_MAX_SCORE = 10.0
_MAX_DOC_BYTES = 512 * 1024


def _parse_score_rows(doc_text):
    """Return (area, weight_text, score_text) triples from the score table."""
    lines = doc_text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if (len(cells) == 3
                and cells[0].lower() == "area"
                and cells[1].lower() == "weight"
                and cells[2].lower() == "score"):
            header_idx = i
            break
    if header_idx < 0:
        return []
    rows = []
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 3:
            break
        if all(c and set(c) <= set("-: ") for c in cells):
            continue
        if cells[0].lower() == "overall":
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def _as_float(text_value, area, label):
    """Return text_value as a finite float or raise ValueError."""
    try:
        value = float(text_value)
    except (TypeError, ValueError):
        raise ValueError("%s for %r is not numeric" % (label, area))
    if math.isnan(value) or math.isinf(value):
        raise ValueError("%s for %r is not finite" % (label, area))
    return value


def score_audit_doc(doc_text: str) -> float:
    """Parse per-section scores and return the weighted average."""
    if not isinstance(doc_text, str):
        raise ValueError("doc_text must be a str")
    try:
        encoded = doc_text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("doc_text is not valid utf-8")
    if len(encoded) > _MAX_DOC_BYTES:
        raise ValueError("doc_text exceeds 512 KiB")
    rows = _parse_score_rows(doc_text)
    if not rows:
        raise ValueError("no score rows found")
    total_weight = 0.0
    weighted_sum = 0.0
    for area, weight_text, score_text in rows:
        weight = _as_float(weight_text, area, "weight")
        score = _as_float(score_text, area, "score")
        if weight < 0.0:
            raise ValueError("weight for %r is negative" % (area,))
        if score < _MIN_SCORE or score > _MAX_SCORE:
            raise ValueError("score for %r is out of range" % (area,))
        total_weight += weight
        weighted_sum += weight * score
    if total_weight <= 0.0:
        raise ValueError("total weight must be positive")
    average = weighted_sum / total_weight
    if average < _MIN_SCORE:
        average = _MIN_SCORE
    elif average > _MAX_SCORE:
        average = _MAX_SCORE
    if average < _MIN_GATE:
        raise ValueError(
            "audit score %.4f is below gate %.1f" % (average, _MIN_GATE))
    return average

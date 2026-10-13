"""JW3.a1: label one Jev shadow attempt against the loop's own rule.

A shadow call returns only an attempt id; the worker thread writes the
terminal row later. This module reads that terminal row back and, when Jev
gave a confident noul answer, appends one outcome through
jev_calibration.bind_outcome_to_attempt (the one binder that checks the
attempt, the proposition hash, the decision id and "the first label wins").

What a label measures: agreement with Brother's own deterministic rule,
the promotion test docs/how-to/use-calibrated-decisions.md defines. It is
not independent truth.

Footprint: reads attempts.jsonl once per call, appends at most one line to
outcomes.jsonl beside it. No network, no Jev call, no temp files. Every
unreadable, absent or ambiguous input writes no label; nothing here raises.

Python 3, standard library only.
"""
import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Tuple

import jev_calibration
import jev_seam

SOURCE = "loop_bridge.run_node deterministic verdict"

PENDING = "PENDING"
SKIPPED = "SKIPPED"
LABELLED = "LABELLED"
REFUSED = "REFUSED"

#: The binder's own wording for an attempt that already carries a label.
_ALREADY_LABELLED = "the first label wins"


def attempts_path(ledger_dir: str) -> str:
    return os.path.join(ledger_dir, "attempts.jsonl")


def terminal_rows(path: str, attempt_ids: Set[str]) -> Dict[str, dict]:
    """The single terminal row for each requested id, in one read. An id
    with zero or several terminal rows is absent; an unreadable file is {};
    an undecodable or non object line is skipped (the binder refuses a
    corrupt ledger itself)."""
    found = {}  # type: Dict[str, list]
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                aid = rec.get("attempt_id")
                if (isinstance(aid, str) and aid in attempt_ids
                        and rec.get("phase") in jev_calibration.TERMINAL_PHASES):
                    found.setdefault(aid, []).append(rec)
    except (OSError, TypeError):
        return {}
    return {aid: rows[0] for aid, rows in found.items() if len(rows) == 1}


def jev_says(row: Optional[dict]) -> Optional[bool]:
    """Jev's confident yes or no on an answered noul row, else None."""
    if not isinstance(row, dict) or row.get("phase") != "answered" or row.get("qtype") != "noul":
        return None
    prob = row.get("prob")
    if isinstance(prob, bool) or not isinstance(prob, (int, float)) or not math.isfinite(prob):
        return None
    if prob > jev_seam.ABSTAIN_HIGH:
        return True
    if prob < jev_seam.ABSTAIN_LOW:
        return False
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def label_attempt(ledger_dir: str, attempt_id: str, rule_says: bool, row: Optional[dict] = None,
                  source: str = SOURCE, now: Optional[str] = None) -> Tuple[str, str]:
    """(state, reason): PENDING, SKIPPED, LABELLED or REFUSED. Never raises."""
    if not isinstance(rule_says, bool):
        return REFUSED, "rule answer must be a bool, got %r" % (rule_says,)
    try:
        path = attempts_path(ledger_dir)
        if row is None:
            row = terminal_rows(path, {attempt_id}).get(attempt_id)
        if row is None:
            return PENDING, "no single terminal row yet"
        said = jev_says(row)  # None for a non dict, a phase other than answered, or no confident noul
        if said is None:
            return SKIPPED, "not an answered, confident noul row"
        if row.get("decision_id") is None:
            return SKIPPED, "the attempt wrote no decision"
        outcome = {
            "id": row["decision_id"],
            "correct": said == rule_says,
            "at": now or _now(),
            "source": source,
            "attempt_id": attempt_id,
            "proposition_hash": jev_calibration.proposition_hash(
                row.get("family"), row.get("qtype"), row.get("framing"), row.get("model")),
        }
        jev_calibration.bind_outcome_to_attempt(
            path, os.path.join(ledger_dir, "outcomes.jsonl"), outcome)
        return LABELLED, "labelled correct=%s" % (outcome["correct"],)
    except ValueError as exc:
        if _ALREADY_LABELLED in str(exc):
            return SKIPPED, "already labelled"
        return REFUSED, str(exc)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent a label that cannot be written is a counted refusal, never a raise
        return REFUSED, "%s: %s" % (type(exc).__name__, exc)

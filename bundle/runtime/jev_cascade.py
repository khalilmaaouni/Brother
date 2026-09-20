#!/usr/bin/env python3
"""JEV-03 of the 1.0.20 orchestration control plane: the confidence-gated
cascade that decides ACT, ESCALATE or NO-DATA for one Jev decision.

WHY THIS EXISTS. Measured in this session's own eval: at confidence 0.9 and
above Jev was right 92 to 100 percent of the time, but confident on only 35
to 54 percent of items. A caller that acts on every answer trusts the 46 to
65 percent of items where Jev itself was not sure; a caller that never acts
automatically throws away the cheap, fast win Jev exists to provide. This
module is the gate between those two failures: it ACTs only where
scripts/jev_calibration.py (JEV-02) has MEASURED, from real recorded
outcomes, that confidence at or above some threshold meets the target
precision this risk class requires, with enough samples for that measurement
to mean something. Everything else goes to a stronger reviewer instead of
either extreme.

REUSES, NEVER REBUILDS. jev_calibration.threshold() is the one place a
calibrated confidence threshold is computed; this module never re-derives
precision or a Wilson bound itself, it only reads threshold()'s answer and
decides what to do with it. See CalibrationHandle below for how a caller
points this module at a ledger.

THE THREE OUTCOMES.
  ACT       the decision's own confidence clears the calibrated threshold
            for its family, qtype (optionally scoped to its framing) and
            risk class. The caller may act automatically.
  ESCALATE  sent to the next reviewing lane, named explicitly
            ("muse" first, "opus" second: see CASCADE_LANES) so the caller
            knows exactly where to route it next. This is the outcome for
            everything that is not yet safe to act on automatically,
            including cases where no calibration data exists at all: see
            THE ESCALATE-ON-UNKNOWN RULE below.
  NO-DATA   routing itself could not produce a next step: today this is
            exactly one case, an already-exhausted escalation ladder (a
            decision already reviewed by both muse and opus that still
            does not clear the bar has nowhere further to go inside this
            cascade and needs a human).

THE ESCALATE-ON-UNKNOWN RULE, cited from this unit's own deciding property
(docs/plan/ORCH-1020-WBS.json, JEV-03): "never ACT without a calibrated
threshold for the decision's family and risk class; unknown family or risk
class escalates". This is a deliberate, narrow exception to the general
estate rule that an unrecognised enum value stops the machine (an
unrecognised risk_class or a family jev_calibration has never recorded is
NOT the same defect class as an unrecognised qtype or a malformed decision
object: a new family name or an unfamiliar risk_class label is an ordinary,
expected runtime fact as this product grows, and the safe response to "no
evidence yet" is more scrutiny, not a crash and not silent automatic
action). What still raises, because it is a structural contract violation
of the decision object itself rather than an open-ended business fact: a
decision that is not a dict, that carries no non-empty string id, whose
qtype is not one of jev_calibration.QTYPES (a closed, fixed vocabulary this
whole system defines), whose confidence is not a finite number in [0, 1],
or whose escalation_rung is not one of the ladder's own recognised
positions (0, 1 or 2). See route() for the exact order these are checked
in.

CRITICAL NEVER ACTS, BY POLICY, NOT BY MEASUREMENT. RISK_TARGET_PRECISION
maps "critical" to None on purpose: no Wilson lower bound, however high,
is read as license to act automatically at critical stakes, because a
calibration report describes how the model performed on similar items in
the past, not that THIS specific wrong action is safe. route() checks
risk_class == "critical" before it ever calls jev_calibration, since the
answer does not depend on the calibration data at all.

THREE STATED LIMITATIONS, named here rather than left implicit (found by
an adversarial review of the first draft of this design, docs/plan
research pipeline, before any of this module's real code was written).
None of these are fixed by this unit, because fixing them means changing
jev_calibration.py's ledger shape or adding a state store this WBS unit
does not own; they are contract obligations on the CALLER instead.

  1. framing is OPTIONAL on a decision. When omitted, threshold() pools
     across every framing recorded for the family and qtype (jev_
     calibration.py's own documented behaviour for framing=None). A caller
     that reworded a question and wants the new wording's own, separate
     calibration evidence (never inheriting an old wording's threshold)
     MUST supply framing on every decision it routes. Omitting it is a
     real choice, not an oversight, and it trades framing-precision for
     simplicity when a caller knows its wording is stable.
  2. An escalated re-review should be given its OWN id when it is written
     back to the ledger (for example "<original id>:rung1"), never the
     original id with only escalation_rung bumped: jev_calibration.py
     treats two ledger lines sharing one id as a duplicate decision and
     excludes BOTH from every report. This module does not write to the
     ledger itself (route() only reads it via threshold()), so it cannot
     enforce this; it is stated here for whoever does write the escalated
     review back.
  3. Keeping the same family/qtype/framing across every rung of the
     ladder (see the module docstring's ESCALATED QUESTION IDENTITY
     reasoning, chosen so the calibration ledger stays joinable at all)
     means a family's report() numbers mix rung-0 answers with rung-1 and
     rung-2 reviewer answers. A per-rung precision breakdown would need a
     rung dimension in the ledger schema, which is out of this unit's
     owned files.

ESCALATED QUESTION IDENTITY: an escalated decision keeps the SAME family,
qtype and framing as the one it reviews (only the model/rung, answer and
confidence change). Renaming any of the three on escalation would make a
later report() unable to tell the original and the escalated answer were
ever the same underlying question, which defeats the point of measuring
calibration per family/qtype/framing at all.

SIGNED PROMOTION IS ALSO REQUIRED TO ACT (A0.7, 2026-09-18). A calibrated
threshold measures how Jev performed on similar items in the PAST; it is
not, by itself, a founder's decision that THIS estate may act on THIS
family/qtype/risk_class/model combination without a human in the loop.
route() therefore also requires a founder-signed PROMOTION RECORD naming
that exact (family, qtype, risk_class, model) before it will ever return
ACT, on top of everything above. A promotion record lives in its own
append-only JSONL ledger (see CalibrationHandle.promotions_path, which
defaults to DEFAULT_PROMOTIONS_PATH -- a repo-root data/ path, never a
seam's own ledger_dir, M3 2026-09-18 -- and stays optional in the sense
that a missing file at that path is an empty ledger, never an error: NO
PROMOTIONS RECORDED MEANS NO PROMOTION, which means ESCALATE, never ACT,
for every decision routed through that handle, exactly as if every
promotion lookup had come back empty; a caller can still pass
promotions_path=None explicitly to opt out of even the default path). A
model whose reported id differs from the record's does not count as
promoted (the record names one model, not a family in general); a later
revoke record for the same (family, qtype, risk_class, model) key
cancels an earlier promotion. Every ordinary promotion also carries a
review_by date (item 2, 2026-09-19): a promotion whose review_by has
passed stops being active on its own, with no revoke required, so a
signed promotion can never be forgotten and silently keep authorizing
ACT forever. See _read_promotions() and _validate_promotion_record()
for the ledger shape, and route()'s own body for the six distinct
reasons this can still escalate a well-calibrated, confident decision:
"no_promotion", "model_mismatch", "revoked", "review_expired" (item 2:
the matched promotion's own review_by has passed; re-sign it to
resume), "bound_not_met" (the promotion was signed against a Wilson
lower bound that today's ledger no longer supports: a promotion is a
bet on evidence that existed at signing time, not a permanent license
once real performance regresses below it), and "promotions_corrupt"
(C2, 2026-09-18: any corrupt or unreadable record anywhere in the
promotions store makes the gate refuse the whole store rather than
trust whatever parsed cleanly, since a corrupt revoke line is exactly
what would let a promotion that should have been cancelled keep
routing to ACT).

Python 3.9 floor, standard library only, no network, no third party
dependency.
"""

import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jev_calibration  # noqa: E402

#: Fixed once, here, not accepted as a per-call argument. If a caller could
#: pass a different family/qtype -> precision table on every call, "medium"
#: could mean 0.95 in one call and 0.80 in the next, which would make the
#: risk class label meaningless across calls and impossible to audit later
#: from the ledger alone. A caller that genuinely needs a different policy
#: edits this table (a deliberate, reviewed code change), not a runtime
#: argument.
#:
#: "critical": None is not a placeholder, it is the policy: critical risk
#: NEVER auto-acts, at any confidence, at any calibrated precision. See the
#: module docstring's CRITICAL NEVER ACTS section.
RISK_TARGET_PRECISION = {
    "low": 0.90,
    "medium": 0.95,
    "high": 0.98,
    "critical": None,
}

#: The minimum number of joined (decision, outcome) pairs a confidence band
#: must carry before its Wilson lower bound is trusted for routing. A
#: policy knob, not a derived number: raise it if early operation shows
#: small-sample bands clearing target precision by chance.
MIN_SAMPLES = 20

#: The two-rung escalation ladder, in order. A decision's escalation_rung
#: (0 by default) is its current position: rung 0 has never been reviewed,
#: rung 1 has already been reviewed by CASCADE_LANES[0], rung 2 by
#: CASCADE_LANES[1]. route() never sends a decision already on rung 1 back
#: to CASCADE_LANES[0]: see _escalate_or_exhausted(). route() itself holds
#: no state between calls: a decision's escalation_rung is trusted exactly
#: as given (missing defaults to 0, "never reviewed"). Carrying the right
#: rung forward on a re-submitted decision is the CALLER's obligation, the
#: same way jev_calibration.py trusts every field a caller appends to its
#: ledger: this module has no separate store to check a rung against.
CASCADE_LANES = ("muse", "opus")

#: The promotions store's default location when a caller does not supply
#: its own promotions_path: the repository root's data/ directory (two
#: directories up from this file: scripts/ -> repo root), NEVER inside any
#: seam's ledger_dir (M3, 2026-09-18 review). A seam's ledger_dir is a
#: path that seam itself writes decisions/outcomes to on every consult();
#: a founder-signed promotion record must live somewhere no seam write
#: path can reach, so any caller building a promotions_path underneath
#: its own ledger_dir (jev_seam.py did exactly this before this fix,
#: passing its own explicit promotions_path=os.path.join(ledger_dir,
#: "promotions.jsonl"), which overrides this default and is NOT changed
#: here: that call site is outside this module's owned files) defeats the
#: separation this constant exists to provide. SEAMS MUST NOT WRITE TO
#: THIS PATH. This module does not enforce that (no signer allowlist is
#: implemented here either -- both are named in the review as the
#: founder's decision to make, not this fix's to take), it only stops
#: defaulting the promotions store into a directory a seam already writes
#: to.
DEFAULT_PROMOTIONS_PATH = os.path.join(os.path.dirname(HERE), "data", "jev-promotions.jsonl")

#: Bundles the two paths jev_calibration.threshold() needs plus an optional
#: bands override and an optional promotion-ledger path, so route() takes
#: one "calibration" argument instead of several raw paths. `bands`
#: defaults to jev_calibration.DEFAULT_BANDS; `promotions_path` defaults to
#: DEFAULT_PROMOTIONS_PATH (M3, 2026-09-18 review fix -- previously None,
#: meaning "no promotion store". A missing file at this default path is
#: still an empty ledger, never an error, per _read_promotions()'s own
#: "missing path is empty ledger" rule, so a caller that never signs a
#: promotion still only ever gets "no_promotion", exactly as before; the
#: change is where an UNCONFIGURED caller now looks by default, not what
#: an empty store means). A caller that genuinely wants no promotion store
#: at all, ever, must pass promotions_path=None explicitly.
CalibrationHandle = namedtuple(
    "CalibrationHandle", ["decisions_path", "outcomes_path", "bands", "promotions_path"])
CalibrationHandle.__new__.__defaults__ = (jev_calibration.DEFAULT_BANDS, DEFAULT_PROMOTIONS_PATH)


#: The four fields every promotion record needs, promotion or revoke
#: alike: together they name WHO signed WHAT decision key. Reused by
#: _validate_promotion_record() so the key-field checks are written once.
_PROMOTION_KEY_FIELDS = ("family", "qtype", "risk_class", "model")


def _validate_promotion_record(rec):
    """Raises ValueError naming the first problem found in one promotion
    ledger line. Never mutates rec.

    A revoke record (rec.get("revoke") is True) only needs the key fields
    plus who and when signed the revoke: it cancels a promotion, it does
    not need to re-justify one. An ordinary promotion record additionally
    needs flip_condition, bound_at_signing, n_at_signing and review_by,
    because route() checks the bound that was true when it was signed on
    every call (see the module docstring), not only once at signing
    time, and now also checks that the promotion has not itself gone
    stale (item 2, 2026-09-19: see review_by's own paragraph below).

    review_by (item 2, 2026-09-19, added beside the pre-existing revoke
    mechanism, never replacing it): a promotion with no expiry and no
    forcing function to revisit it is a standing grant a founder can
    forget was ever signed. Every ordinary promotion record now carries
    a review_by date (same "YYYY-MM-DD" shape and "< today, string
    comparison" semantics this estate already uses for a declared
    battery exception -- see scripts/battery_verdict.py's own
    _expired()), and _read_promotions() below stops treating a promotion
    whose review_by has passed as active, the same way a revoke record
    does, without erasing the record itself (a human can still see, and
    re-sign, an expired promotion; a revoke is a deliberate cancellation,
    an expiry is a deadline that quietly passed). A revoke record needs
    no review_by: it cancels rather than grants, so there is nothing for
    it to expire.
    """
    if not isinstance(rec, dict):
        raise ValueError("promotion record must be a JSON object, got %r" % (rec,))

    for field in _PROMOTION_KEY_FIELDS + ("signed_by", "signed_at"):
        val = rec.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError("promotion record %s must be a non-empty string, got %r" % (field, val))
    if rec["qtype"] not in jev_calibration.QTYPES:
        raise ValueError(
            "promotion record qtype must be one of %s, got %r" % (sorted(jev_calibration.QTYPES), rec["qtype"])
        )
    try:
        jev_calibration._parse_ts(rec["signed_at"])
    except ValueError as exc:
        raise ValueError("promotion record signed_at is invalid: %s" % exc)

    if rec.get("revoke") is True:
        return

    flip_condition = rec.get("flip_condition")
    if not isinstance(flip_condition, str) or not flip_condition.strip():
        raise ValueError("promotion record flip_condition must be a non-empty string, got %r" % (flip_condition,))

    bound = rec.get("bound_at_signing")
    if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not (0.0 <= bound <= 1.0):
        raise ValueError("promotion record bound_at_signing must be a number in [0, 1], got %r" % (bound,))

    n = rec.get("n_at_signing")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("promotion record n_at_signing must be a positive integer, got %r" % (n,))

    review_by = rec.get("review_by")
    if not isinstance(review_by, str) or not review_by.strip():
        raise ValueError("promotion record review_by must be a non-empty date string, got %r" % (review_by,))
    try:
        jev_calibration._parse_ts(review_by)
    except ValueError as exc:
        raise ValueError("promotion record review_by is not a parseable date: %s" % exc)


def _promotion_expired(review_by, today):
    """True when `review_by` (already validated as a parseable date/
    timestamp by _validate_promotion_record) names a date before
    `today`. Mirrors scripts/battery_verdict.py's own _expired(): equal
    to today is still valid (a deadline is inclusive of its own day),
    only strictly before it counts as expired."""
    return jev_calibration._parse_ts(review_by).date() < today


def _read_promotions(path, today=None):
    """Reads the promotion ledger at `path`. Returns (active, revoked_keys,
    corrupt, expired_keys).

    `today` is a real datetime.date to compare each record's review_by
    against (item 2, 2026-09-19): defaults to None, meaning "compute it
    for real, right now" (datetime.now(timezone.utc).date()) -- never
    fabricated, per this estate's own NO FABRICATION rule. A caller (a
    test, or route()'s own optional passthrough) may pin an explicit
    date for a deterministic check.

    active maps (family, qtype, risk_class, model) -> the promotion record
    currently in force for that exact key: the LAST record for the key BY
    APPEND ORDER (line number in the file), once it is a promotion record,
    not a revoke, AND its own review_by has not yet passed `today` (see
    expired_keys below). A promote, then a later revoke, then a later
    promote again is active once more: only the LAST record per key by
    append order decides, which is what "a later revoke record ...
    cancels it" means.

    expired_keys (item 2, 2026-09-19) is the set of keys whose latest
    record is an ordinary (non-revoke) promotion whose review_by has
    passed `today`: kept separate from revoked_keys (a founder cancelled
    it) and from "never promoted at all" (nobody ever signed one), so
    _promotion_gate() can report the distinct "review_expired" reason.
    An expired promotion is NOT active, but its record is not erased or
    otherwise treated as corrupt: a human can still read, and re-sign,
    an expired promotion from the ledger's own history.

    ORDER IS BY APPEND POSITION, NEVER BY signed_at (clock skew, found by
    adversarial review 2026-09-18). signed_at is a human's claim about
    when they signed, carried as data and validated as a real timestamp
    (_validate_promotion_record), but never compared to decide who wins:
    a future-dated promote appended BEFORE a revoke must not beat that
    revoke, and a back-dated revoke appended AFTER a promote must still
    cancel it. This ledger is append-only and never rewritten (the same
    discipline jev_calibration.py's own ledger uses), so "later in the
    file" is the one fact about ordering nothing can lie about; a
    caller-supplied clock can.

    revoked_keys is the set of keys whose latest record is a revoke: kept
    separate from "never promoted at all" so route() can report the
    distinct "revoked" reason rather than folding it into "no_promotion".

    A missing path (including None: no promotion store configured at all)
    is an empty ledger, never an error -- see the module docstring's SIGNED
    PROMOTION IS ALSO REQUIRED TO ACT section for why that means "no
    promotion" rather than some other default.

    corrupt is a list of {"line", "error", "raw"} for every non-blank line
    that is not valid JSON or fails _validate_promotion_record(): a record
    missing a required field is ignored for routing (as if it were never
    written) rather than crashing the read or being trusted anyway, the
    same isolate-and-continue discipline jev_calibration.py's own ledger
    reader uses.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    corrupt = []
    if path is None or not os.path.isfile(path):
        return {}, set(), corrupt, set()

    by_key = {}  # key -> rec; the file is read top-to-bottom and every
                 # occurrence of a key unconditionally overwrites the
                 # last, so whatever survives the loop is simply the
                 # LAST-APPENDED record for that key -- no comparison, no
                 # signed_at, no room for a clock claim to win.
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                text = raw.strip()
                if not text:
                    continue
                try:
                    rec = json.loads(text)
                except json.JSONDecodeError as exc:
                    corrupt.append({"line": line_no, "error": "not valid JSON: %s" % exc, "raw": text})
                    continue
                try:
                    _validate_promotion_record(rec)
                except ValueError as exc:
                    corrupt.append({"line": line_no, "error": str(exc), "raw": rec})
                    continue

                key = tuple(rec[f] for f in _PROMOTION_KEY_FIELDS)
                by_key[key] = rec
    except OSError as exc:
        # A promotions store that exists (os.path.isfile passed above) but
        # cannot actually be read -- permission denied, a disk error, a
        # race where it was removed between the isfile check and open() --
        # must never escape this module and crash route(). It fails
        # closed exactly like a corrupt line: _promotion_gate() below
        # treats any non-empty `corrupt` as "promotions_corrupt", so an
        # unreadable store escalates with that same named reason instead
        # of raising (OSError minor, 2026-09-18 review fix).
        corrupt.append({"line": 0, "error": "OSError reading %s: %s" % (path, exc), "raw": None})

    active = {}
    revoked_keys = set()
    expired_keys = set()
    for key, rec in by_key.items():
        if rec.get("revoke") is True:
            revoked_keys.add(key)
        elif _promotion_expired(rec["review_by"], today):
            expired_keys.add(key)
        else:
            active[key] = rec
    return active, revoked_keys, corrupt, expired_keys


def _promotion_gate(decision, family, qtype, risk_class, current_bound, promotions_path, today=None):
    """Returns None when a valid, unrevoked, current, unexpired promotion
    covers this exact decision, or one of "no_promotion" /
    "model_mismatch" / "revoked" / "review_expired" / "bound_not_met" /
    "promotions_corrupt" naming why it does not.

    `today` is passed through to _read_promotions() unchanged (see its
    own docstring): None means "the real, current date", never a
    fabricated one.

    current_bound is the Wilson lower bound jev_calibration measured for
    the confidence band that otherwise qualified this decision to ACT: a
    promotion signed against a bound the evidence no longer supports is
    stale, not valid (see the module docstring).

    C2, 2026-09-18 review fix: ANY corrupt or invalid record in the
    promotions store -- a bad signed_at, "revoke": "true" written as a
    string instead of true, any line that fails
    _validate_promotion_record(), or the file being unreadable at all --
    makes this gate escalate "promotions_corrupt" before it ever looks at
    `active`/`revoked_keys`. This is deliberately whole-file, not scoped
    to the one bad line's own key: a corrupt revoke line for THIS key is
    the exact failure mode that let an earlier promotion silently stay in
    force (a corrupt revoke was discarded, so the promotion it should have
    cancelled kept routing to ACT), and there is no way to tell from here
    whether a bad line elsewhere in the file was supposed to be the
    revoke or update for the key being checked right now. Fail closed on
    the whole store, not the one row.
    """
    model = decision.get("model")
    active, revoked_keys, corrupt, expired_keys = _read_promotions(promotions_path, today=today)
    if corrupt:
        return "promotions_corrupt"

    key = (family, qtype, risk_class, model)
    record = active.get(key)
    if record is not None:
        if current_bound is None or current_bound < record["bound_at_signing"]:
            return "bound_not_met"
        return None

    if key in revoked_keys:
        return "revoked"

    if key in expired_keys:
        return "review_expired"

    other_model_active = any(k[:3] == (family, qtype, risk_class) for k in active)
    if other_model_active:
        return "model_mismatch"

    return "no_promotion"


def _act(decision, risk_class, threshold_value):
    return {
        "outcome": "ACT",
        "id": decision["id"],
        "risk_class": risk_class,
        "family": decision["family"],
        "qtype": decision["qtype"],
        "confidence": decision["confidence"],
        "threshold": threshold_value,
        "reason": None,
    }


def _escalate_or_exhausted(decision_id, rung, reason):
    """rung is the decision's CURRENT position (0, 1 or 2). Returns the
    ESCALATE record naming the next lane and rung, or NO-DATA naming
    escalation_exhausted once rung is already past the last lane. A
    decision on rung 1 (already reviewed by CASCADE_LANES[0]) always moves
    to CASCADE_LANES[1], never back to CASCADE_LANES[0]."""
    if rung >= len(CASCADE_LANES):
        return {"outcome": "NO-DATA", "id": decision_id, "reason": "escalation_exhausted"}
    return {
        "outcome": "ESCALATE",
        "id": decision_id,
        "next_lane": CASCADE_LANES[rung],
        "next_rung": rung + 1,
        "reason": reason,
    }


def route(decision, risk_class, calibration, today=None):
    """Decide ACT, ESCALATE or NO-DATA for one decision at one risk class.

    decision is a plain dict carrying at least: id (non-empty str), family
    (non-empty str), qtype (one of jev_calibration.QTYPES), confidence (a
    finite number in [0, 1]). Optional: framing (str, passed through to
    jev_calibration.threshold() to scope the calibration lookup to this
    exact wording; omitted or None pools across framings for the family
    and qtype), escalation_rung (int, 0, 1 or 2; defaults to 0, meaning
    "never yet reviewed"), model (str, the model id that produced this
    decision; matched against a promotion record's own model, see the
    module docstring's SIGNED PROMOTION IS ALSO REQUIRED TO ACT section --
    omitted or None can never match any promotion, since a promotion names
    one exact model).

    calibration is a CalibrationHandle pointing at the ledger's decisions
    and outcomes files (and, optionally, a non-default bands tuple and a
    promotion-ledger path).

    `today` (item 2, 2026-09-19): a real datetime.date to check a
    matched promotion's review_by against, passed straight through to
    _promotion_gate()/_read_promotions(). Defaults to None, meaning "the
    real, current date" -- never fabricated. Exists so a test can pin a
    deterministic date; every real caller leaves this at its default.

    Raises ValueError for a structurally invalid decision object (see the
    module docstring's ESCALATE-ON-UNKNOWN RULE for exactly what does and
    does not raise). Never raises for an unrecognised risk_class or an
    unrecognised family/qtype: those escalate.
    """
    if not isinstance(decision, dict):
        raise ValueError("decision must be a dict, got %r" % (decision,))

    decision_id = decision.get("id")
    if not isinstance(decision_id, str) or not decision_id.strip():
        raise ValueError("decision.id must be a non-empty string, got %r" % (decision_id,))

    family = decision.get("family")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("decision.family must be a non-empty string, got %r" % (family,))

    qtype = decision.get("qtype")
    if qtype not in jev_calibration.QTYPES:
        raise ValueError(
            "decision.qtype must be one of %s, got %r" % (sorted(jev_calibration.QTYPES), qtype)
        )

    confidence = decision.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("decision.confidence must be a number, got %r" % (confidence,))
    if not (0.0 <= confidence <= 1.0):
        raise ValueError("decision.confidence must be within [0, 1], got %r" % (confidence,))

    rung = decision.get("escalation_rung", 0)
    if isinstance(rung, bool) or not isinstance(rung, int) or rung not in (0, 1, 2):
        raise ValueError("decision.escalation_rung must be 0, 1 or 2, got %r" % (rung,))

    if not isinstance(calibration, CalibrationHandle):
        raise ValueError("calibration must be a CalibrationHandle, got %r" % (calibration,))

    framing = decision.get("framing")
    if framing is not None and (not isinstance(framing, str) or not framing.strip()):
        raise ValueError("decision.framing must be a non-empty string when present, got %r" % (framing,))

    model = decision.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("decision.model must be a non-empty string when present, got %r" % (model,))

    # An unrecognised or malformed risk_class escalates rather than raises:
    # see the module docstring's ESCALATE-ON-UNKNOWN RULE.
    if not isinstance(risk_class, str) or risk_class not in RISK_TARGET_PRECISION:
        return _escalate_or_exhausted(decision_id, rung, "unknown_risk_class")

    # Critical never acts, regardless of confidence or calibration: checked
    # before any calibration call, since the answer never depends on it.
    if risk_class == "critical":
        return _escalate_or_exhausted(decision_id, rung, "critical_never_acts")

    target_precision = RISK_TARGET_PRECISION[risk_class]

    res = jev_calibration.threshold(
        calibration.decisions_path, calibration.outcomes_path,
        family, qtype, target_precision, MIN_SAMPLES,
        framing=framing, bands=calibration.bands,
    )

    if res["threshold"] is None:
        # Covers both "this family/qtype has never been calibrated" and
        # "it has been calibrated, but no band reached this risk class's
        # target precision with enough samples". Both are "no evidence
        # this is safe to act on automatically yet", so both escalate.
        return _escalate_or_exhausted(decision_id, rung, "calibration_no_data:%s" % res["reason"])

    if confidence < res["threshold"]:
        return _escalate_or_exhausted(decision_id, rung, "below_calibrated_threshold")

    # A0.7: a calibrated, confident decision still needs a founder-signed
    # promotion naming this exact (family, qtype, risk_class, model)
    # before it may ACT. See the module docstring's SIGNED PROMOTION IS
    # ALSO REQUIRED TO ACT section and _promotion_gate() for the four
    # reasons this can still escalate.
    current_bound = next(
        (row["wilson_lower_95"] for row in res["rows"] if row["lower"] == res["threshold"]), None
    )
    promo_reason = _promotion_gate(decision, family, qtype, risk_class, current_bound,
                                    calibration.promotions_path, today=today)
    if promo_reason is not None:
        return _escalate_or_exhausted(decision_id, rung, promo_reason)

    return _act(decision, risk_class, res["threshold"])


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "selftest":
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dp = os.path.join(d, "decisions.jsonl")
            op = os.path.join(d, "outcomes.jsonl")
            for i in range(40):
                did = "s%d" % i
                jev_calibration.append_decision(dp, {
                    "id": did, "family": "f", "qtype": "noul", "framing": "h",
                    "answer": True, "prob": 0.95, "confidence": 0.95, "model": "m",
                    "cost": 0.0, "at": "2026-09-18T00:00:00Z",
                })
                jev_calibration.append_outcome(op, {"id": did, "correct": True, "source": "t", "at": "2026-09-18T00:01:00Z"},
                                                decisions_path=dp)
            pp = os.path.join(d, "promotions.jsonl")
            with open(pp, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "family": "f", "qtype": "noul", "risk_class": "low", "model": "m",
                    "signed_by": "founder", "signed_at": "2026-09-18T00:00:00Z",
                    "bound_at_signing": 0.85, "n_at_signing": 40,
                    "flip_condition": "precision drops below target for two consecutive weeks",
                    "review_by": "2099-01-01",
                }) + "\n")
            handle = CalibrationHandle(dp, op, promotions_path=pp)
            result = route(
                {"id": "new1", "family": "f", "qtype": "noul", "confidence": 0.95, "model": "m"},
                "low", handle,
            )
            assert result["outcome"] == "ACT", result

            unpromoted = route(
                {"id": "new2", "family": "f", "qtype": "noul", "confidence": 0.95, "model": "m"},
                # Explicit promotions_path=None (coordinator re-review,
                # MINOR, 2026-09-18): CalibrationHandle's own default is
                # now a real repo-root path (M3), so this selftest must
                # say "no promotion store" explicitly rather than
                # accidentally depending on whatever, if anything, is
                # signed there.
                "low", CalibrationHandle(dp, op, promotions_path=None),
            )
            assert unpromoted["outcome"] == "ESCALATE" and unpromoted["reason"] == "no_promotion", unpromoted
            print("selftest OK")
        return 0
    print("usage: jev_cascade.py selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main())

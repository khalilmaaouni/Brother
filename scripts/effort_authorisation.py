#!/usr/bin/env python3
"""ORCH-30: effort above the standing cap must cite its authorisation.

WHY THIS EXISTS. This estate caps subagent effort at "high". Two shipped
agent definitions (products/brothermode/agents/navigator.md and
products/brothermode/agents/reviewer.md) hardcode "effort: xhigh" with no
recorded grant anywhere: a cap raise that nobody can point to. This module
is the one place a dispatch checks whether an effort above the cap is
allowed, so a caller never has to reinvent the question of what counts as
a valid authorisation.

An authorisation is only real when it names three things: who granted it
(a human, never the agent asking for the raise), the session it was given
in, and when it expires. A grant with no expiry is not a grant, it is a
standing exception nobody can audit later; this mirrors the estate's other
expiry-bearing grants (scripts/fence_expiry.py's claims,
scripts/fable_authority.py's merge delegations), none of which accept an
open-ended "until".

A SELF-AUTHORED RECORD MUST NEVER PASS. If the requesting agent can name
itself as the granter, it has manufactured the exception it wants, and the
cap is worth nothing. check_authorisation() refuses this case even when
every other field is well-formed, and checks it before the shape of the
rest of the record, on purpose: a self-quoted grant is refused for being
self-quoted, not merely because it also happens to be missing a field.

AN UNKNOWN EFFORT VALUE RAISES, IT NEVER DEFAULTS. Treating an unrecognised
effort string as "below the cap" would silently let a dispatch through;
treating it as "above the cap" would silently refuse a legitimate one that
this module simply does not know the name of. Neither is safe, so both
functions raise ValueError instead of guessing, matching
scripts/orchestrator_invariants.py's own rule that an unrecognised input is
never read as the safe case.

Python 3.9 floor, standard library only, no network, no file I/O: this
module only ever answers a question about values a caller already holds.
"""

from datetime import datetime, timezone

# Lowest to highest. Order is load-bearing: is_above_cap compares index
# positions, so a reordering here silently changes which efforts need an
# authorisation. test_effort_authorisation.py asserts the exact tuple.
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")

# The founder's standing cap (CLAUDE.md, MODEL CONSUMPTION CAP): writers,
# scouts and reviewers run at effort HIGH unless a founder-named exception
# raises it for that session.
STANDING_CAP = "high"


def is_above_cap(effort, cap=STANDING_CAP):
    """True when effort sits strictly above cap in EFFORT_ORDER. Raises
    ValueError for either argument outside EFFORT_ORDER: an unrecognised
    value is never quietly treated as at, above, or below the cap."""
    if effort not in EFFORT_ORDER:
        raise ValueError('unknown effort %r, expected one of %s' % (effort, EFFORT_ORDER))
    if cap not in EFFORT_ORDER:
        raise ValueError('unknown cap %r, expected one of %s' % (cap, EFFORT_ORDER))
    return EFFORT_ORDER.index(effort) > EFFORT_ORDER.index(cap)


def check_authorisation(effort, record, requested_by, cap=STANDING_CAP, now=None):
    """(allowed, reason). Raises ValueError when effort (or cap) is not a
    known EFFORT_ORDER value, via is_above_cap: the enum check lives in
    exactly one place.

    At or below cap: always allowed, record is not even inspected, since
    the deciding rule only ever gates effort ABOVE the cap.

    Above cap: record must be a non-empty dict naming, all four:
      'granted_by'  who granted it, never the requesting agent itself
      'words'       the granter's own verbatim words
      'session'     which session the grant was given in
      'until'       an ISO-8601 date or datetime string, still in the
                    future relative to `now` (defaults to the real clock)

    Checked in this order: missing record, missing granted_by, self
    authorship, missing words, missing session, missing or unparsable
    until, expired until. Self authorship is checked right after
    granted_by is confirmed present, and before words/session/until are
    even read, so a self-quoted record is refused for being self-quoted
    even when it is also missing other fields: the rule holds regardless
    of what else is wrong with the record."""
    above = is_above_cap(effort, cap)
    if not above:
        return True, 'effort %r is at or below cap %r: no authorisation needed' % (effort, cap)

    if not isinstance(record, dict) or not record:
        return False, ('missing authorisation: effort %r is above cap %r and no record '
                        'was provided' % (effort, cap))

    granted_by = record.get('granted_by')
    if not isinstance(granted_by, str) or not granted_by.strip():
        return False, 'malformed authorisation: no named authoriser (granted_by) was provided'
    granted_by_text = granted_by.strip()

    requested_by_text = requested_by.strip() if isinstance(requested_by, str) else requested_by
    if granted_by_text == requested_by_text:
        return False, ('self-authored authorisation: granted_by %r equals requested_by %r, '
                        'the requesting agent cannot authorise itself'
                        % (granted_by_text, requested_by_text))

    words = record.get('words')
    if not isinstance(words, str) or not words.strip():
        return False, 'malformed authorisation: no verbatim words were provided'

    session = record.get('session')
    if not isinstance(session, str) or not session.strip():
        return False, 'malformed authorisation: no session was recorded'

    until = record.get('until')
    if not isinstance(until, str) or not until.strip():
        return False, 'malformed authorisation: no parsable expiry (until) was provided'

    until_text = until.strip()
    if until_text.endswith('Z'):
        until_text = until_text[:-1] + '+00:00'
    try:
        until_dt = datetime.fromisoformat(until_text)
    except ValueError:
        return False, 'malformed authorisation: until %r could not be parsed' % until

    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if until_dt <= now:
        return False, ('expired authorisation: until %r is at or before now %r'
                        % (until_dt.isoformat(), now.isoformat()))

    return True, ('authorisation from %r is valid and live until %r'
                  % (granted_by_text, until_dt.isoformat()))

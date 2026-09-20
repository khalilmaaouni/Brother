"""bridge_content_gate: the content gate lives INSIDE the outside-model bridge.

WHY THIS EXISTS. scripts/coe_outside_gate.py already gates one boundary (an
outside council seat) by wiring its scan and its call into one function a
caller cannot reorder. This estate has a second boundary that leaves the
machine: the outside-model bridge itself (the OpenRouter lane a caller
reaches for a secondary read). A scan that runs beside the bridge, in a
caller the bridge cannot see, is advisory: this estate has already recorded
a session that sent a payload after its own scan reported a hit (memory note
a-scan-that-prints-a-count-and-proceeds-is-not-a-gate). The fix is the same
one coe_outside_gate already applies, moved to where the outside call
actually happens: put the check inside the thing every caller must go
through, not beside it.

This module does not keep a second copy of the forbidden-terms list or its
loading and validation rules. It reuses coe_outside_gate.load_terms and
coe_outside_gate.DEFAULT_TERMS_PATH (the estate's existing list): this is
the same privacy rule, enforced at a different place, not a second rule.

THE ENTRY POINTS.

  decide(payload, destination, *, terms_path=None) -> Verdict
      Classifies `destination`, scans `payload` when the destination
      requires it, and returns a Verdict. Never raises for a bad payload or
      a bad destination: every refusal comes back as Verdict(REFUSE, ...),
      never an exception.

  guard(payload, destination, call, *, terms_path=None)
      THE FUNCTION THE BRIDGE ACTUALLY CALLS. Runs decide() first, in this
      process, and invokes the zero-argument callable `call` only when the
      verdict is ALLOW. There is no code path in this module that reaches
      `call` without decide() having already run: a bridge built on top of
      guard() cannot forget the scan, because the scan is not a separate
      step the bridge remembers to take, it is the only way to reach the
      call at all.

DESTINATIONS. A destination is one of three things, never inferred by
guessing: LOCAL_DESTINATIONS (never leaves this machine: skip the scan and
say so), OUTSIDE_DESTINATIONS (leaves this machine: scan before allowing),
or unrecognised (refuse, because a lane this module has not been told about
is not evidence that it stays local). Adding a new destination is a
deliberate edit to the two sets below, never a default that lets an
unknown lane through.

FAIL CLOSED (the one rule this module never gives ground on). An unreadable
payload, an unknown destination, or a forbidden-terms list that is missing,
unreadable, malformed or empty is REFUSE, never ALLOW. A false refusal costs
a delayed or rerouted call. A false allow costs a leak. Every ambiguous case
below takes the cheaper failure direction.

WHAT A REFUSAL NAMES, AND WHAT IT NEVER NAMES. A REFUSE reason names the
matched term's CATEGORY (the label the terms-list author chose), its SHAPE
(character length and which character classes it mixes: letters, digits,
punctuation) and its POSITION (line and column in the payload, or a note
that no fixed position exists because the term only appeared once
whitespace was collapsed). It never names or echoes the matched text itself.
This estate has a recorded incident of a scanner's own explanation carrying
the forbidden term forward (memory note
the-scanner-must-contain-what-it-forbids); this module's tests assert the
matched term does not appear in any Verdict.reason it produces.

CONTINGENCY, edge by edge.

  empty payload                    scans clean (no term can match nothing);
                                    ALLOW, same as any other clean payload.
  payload is bytes, not text       decoded as utf-8; undecodable bytes
                                    REFUSE rather than guess an encoding.
  term in a different case         matched case-insensitively; the compare
                                    always happens on lowercased text.
  term split across lines          also matched against a whitespace-
                                    collapsed copy of the payload, so a term
                                    broken by a line wrap is still caught;
                                    its reported position says so rather
                                    than naming a line number that would be
                                    fiction for a match that only exists
                                    after collapsing.
  terms list file is empty         load_terms returns {}, which this module
                                    treats exactly like a missing file: an
                                    empty list is never read as "nothing is
                                    forbidden" (the same RULE 4 argument
                                    coe_outside_gate makes; see there for
                                    why "empty" and "absent" must collapse
                                    into the same refusal).
  payload over the size cap        REFUSE unread rather than scanned; a
                                    synchronous gate in front of a bridge
                                    call should fail fast on an unbounded
                                    payload, not become the slow part of the
                                    pipeline. Reuses coe_outside_gate's cap.
  unknown destination              REFUSE; see DESTINATIONS above.
  destination that never leaves    ALLOW without scanning, and the reason
  this machine                     says so; there is nothing to protect
                                    against on a lane that stays local.

WHAT THIS MODULE IS NOT. A term matcher, same limitation as
coe_outside_gate: it cannot recognise a paraphrase, a translation, a
renamed entity, or private content stated without any configured term. It
is a backstop under a human decision about what a destination is, never a
replacement for that decision.

Python 3.9 floor, standard library only, no network.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as _coe  # noqa: E402  (sibling module, reused not copied)

ALLOW = "ALLOW"
REFUSE = "REFUSE"

#: Lanes that never leave this machine: an in-process or same-host call.
#: Payloads to these are allowed without a scan (there is nothing to leak
#: to a destination that is not outside). Extending this set is a
#: deliberate edit, never inferred from a name pattern.
LOCAL_DESTINATIONS = frozenset({"claude", "native", "local"})

#: Lanes that leave this machine and therefore require a scan first. Named
#: after this estate's own outside-model bridge (~/.claude/bin/or_ask.py)
#: and its verified routes. "typesafe" added for JEV-01: Jev is a decision
#: model reached through the same bridge's --decisions endpoint, and the
#: state and questions sent to it are exactly as much a leave-the-machine
#: payload as any chat prompt.
OUTSIDE_DESTINATIONS = frozenset({"muse", "deepseek", "openrouter", "typesafe"})


class Verdict(object):
    """One decision. verdict is ALLOW or REFUSE; reason is human-readable
    and, for a REFUSE born from a scan, built only from a category name, a
    shape description and a position, never the matched text (see the
    module docstring's "WHAT A REFUSAL NAMES" section).
    """

    __slots__ = ("verdict", "reason")

    def __init__(self, verdict, reason):
        self.verdict = verdict
        self.reason = reason

    @property
    def allowed(self):
        return self.verdict == ALLOW

    def __repr__(self):
        return "Verdict(verdict=%r, reason=%r)" % (self.verdict, self.reason)

    def __eq__(self, other):
        if not isinstance(other, Verdict):
            return NotImplemented
        return self.verdict == other.verdict and self.reason == other.reason


class GateRefused(Exception):
    """Raised by guard() when decide() refuses. Carries the refusing
    Verdict as `.verdict`; str(exc) is `.verdict.reason`.
    """

    def __init__(self, verdict):
        self.verdict = verdict
        super(GateRefused, self).__init__(verdict.reason)


def _classify_destination(destination):
    """"local", "outside", or None (unrecognised) for `destination`.

    A non-string destination is unrecognised, not an error to raise on:
    decide() turns None into a REFUSE the same way it does any other
    unknown lane, per the fail-closed rule.
    """
    if not isinstance(destination, str):
        return None
    key = destination.strip().lower()
    if key in LOCAL_DESTINATIONS:
        return "local"
    if key in OUTSIDE_DESTINATIONS:
        return "outside"
    return None


def _to_text(payload):
    """(text, None) on success, or (None, refusal reason) on failure.

    Mirrors coe_outside_gate's own bytes/str handling and size cap rather
    than restating a different one.
    """
    if isinstance(payload, bytes):
        if len(payload) > _coe.MAX_CONTENT_BYTES:
            return None, (
                "payload exceeds %d bytes; refusing rather than scanning "
                "an unbounded payload synchronously" % _coe.MAX_CONTENT_BYTES
            )
        try:
            return payload.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, (
                "payload bytes are not valid utf-8; refusing rather than "
                "scanning content that cannot be decoded safely"
            )
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > _coe.MAX_CONTENT_BYTES:
            return None, (
                "payload exceeds %d bytes; refusing rather than scanning "
                "an unbounded payload synchronously" % _coe.MAX_CONTENT_BYTES
            )
        return payload, None
    return None, (
        "payload is neither text nor bytes; refusing rather than guessing "
        "how to scan it"
    )


def _shape(term):
    """A description of `term`'s length and character classes, never the
    term itself. E.g. "10 chars (letters)" or "6 chars (letters+digits)".
    """
    classes = []
    if any(c.isalpha() for c in term):
        classes.append("letters")
    if any(c.isdigit() for c in term):
        classes.append("digits")
    if any(not c.isalnum() for c in term):
        classes.append("punctuation")
    return "%d chars (%s)" % (len(term), "+".join(classes) if classes else "empty")


def _find_guarded(text_lower, term_lower):
    """First index of `term_lower` in `text_lower` whose preceding
    character is not a letter (the same false-positive guard
    coe_outside_gate._count_hits applies), or -1. See that module's RULE 2
    for why: this refuses to count "vex" inside "convex" while still
    counting a term glued to a letter or digit AFTER it.
    """
    start = 0
    while True:
        idx = text_lower.find(term_lower, start)
        if idx == -1:
            return -1
        if idx == 0 or not text_lower[idx - 1].isalpha():
            return idx
        start = idx + 1


def _position(text, idx):
    """1-based (line, column) of index `idx` in `text`."""
    line = text.count("\n", 0, idx) + 1
    last_newline = text.rfind("\n", 0, idx)
    column = idx - last_newline
    return line, column


def _first_hit(text, terms):
    """(category, shape, position_str) for the first forbidden term found
    in `text`, checked both as written and with all whitespace collapsed
    (to catch a term split across a line break), or None if clean.
    """
    text_lower = text.lower()
    collapsed_lower = re.sub(r"\s+", "", text_lower)
    for category, category_terms in terms.items():
        for term in category_terms:
            term_lower = term.lower()
            idx = _find_guarded(text_lower, term_lower)
            if idx != -1:
                line, column = _position(text, idx)
                return category, _shape(term), "line %d, column %d" % (line, column)
            if _find_guarded(collapsed_lower, term_lower) != -1:
                return (
                    category,
                    _shape(term),
                    "position unresolvable (term only appears once "
                    "whitespace is collapsed, e.g. split across lines)",
                )
    return None


def decide(payload, destination, *, terms_path=None):
    """The verdict for sending `payload` to `destination`. Never raises.

    `terms_path` defaults to coe_outside_gate.DEFAULT_TERMS_PATH (the
    estate's existing forbidden-terms list) when omitted.
    """
    dest_kind = _classify_destination(destination)
    if dest_kind is None:
        return Verdict(
            REFUSE,
            "destination %r is not a recognised lane; refusing rather "
            "than guessing whether it leaves this machine" % (destination,),
        )

    if dest_kind == "local":
        return Verdict(
            ALLOW,
            "destination %r never leaves this machine; scan skipped"
            % (destination,),
        )

    path = terms_path if terms_path is not None else _coe.DEFAULT_TERMS_PATH
    terms = _coe.load_terms(path)
    if not terms:
        return Verdict(
            REFUSE,
            "forbidden-terms list at %s is missing, unreadable, malformed "
            "or empty; refusing rather than treating that as nothing to "
            "check" % (path,),
        )

    text, refusal = _to_text(payload)
    if text is None:
        return Verdict(REFUSE, refusal)

    hit = _first_hit(text, terms)
    if hit is None:
        return Verdict(ALLOW, "payload is clean; scanned against %s" % (path,))

    category, shape, position = hit
    return Verdict(
        REFUSE,
        "payload matched a forbidden term in category %r: shape %s at %s"
        % (category, shape, position),
    )


def guard(payload, destination, call, *, terms_path=None):
    """decide() first, then `call()` only if it allows. Raises GateRefused
    (carrying the refusing Verdict as `.verdict`) without ever invoking
    `call` on a refusal.
    """
    verdict = decide(payload, destination, terms_path=terms_path)
    if not verdict.allowed:
        raise GateRefused(verdict)
    return call()

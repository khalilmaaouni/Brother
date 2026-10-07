"""Brother Core: capability canary and quarantine gate (unit OR-3).

Replaces a static, unverified "model X can do Y" profile table, which
let Jev's score question type sit undetected as broken (returning a
constant 0) for an unknown period, with:

1. A hard, code-level refusal for Jev's two confirmed-broken question
   types (score, choice). This is not a row in a mutable table a
   config file could flip back on; it is a Python-level check with no
   configuration surface at all.
2. A CapabilityProfile object for any other model/capability pair,
   whose canary check runs once per session (cached, never re-probed
   on every call, per Muse's own cost-discipline point) and quarantines
   the pair the moment the canary fails, rather than continuing to
   trust a profile that no longer matches the model's real behavior.
"""


# Jev's ONLY confirmed-working question type, per a real A/B control
# run this session (2+2=4 scored 0, 2+2=5 scored 0: score is a
# constant regardless of input) and a --json raw-output check (choice
# with criteria echoes back a criteria KEY, not a real selected
# option). Hard-coded, not a lookup in any mutable table: no config
# value anywhere can re-enable score or choice.
import math
import time
JEV_ALLOWED_QUESTION_TYPES = frozenset({"noul"})
JEV_QUARANTINED_QUESTION_TYPES = frozenset({"score", "choice"})


class QuarantinedCapability(Exception):
    """Raised when a caller asks for a capability known to be broken,
    whatever a config file might claim, or for one whose canary has
    failed."""


def validate_jev_question_type(question_type):
    """Hard refusal at the call site, never a soft warning: no config
    can re-enable score or choice once this function is on the call
    path, unlike a documentation note that a router could silently
    ignore."""
    if question_type in JEV_QUARANTINED_QUESTION_TYPES:
        raise QuarantinedCapability(
            "Jev question type %r is quarantined (confirmed broken "
            "2026-09-19: score returns a constant 0 regardless of "
            "input, choice echoes a criteria key instead of a real "
            "option); only %s is allowed"
            % (question_type, sorted(JEV_ALLOWED_QUESTION_TYPES))
        )
    if question_type not in JEV_ALLOWED_QUESTION_TYPES:
        raise QuarantinedCapability(
            "Jev question type %r is not a recognized allowed type "
            "(%s); refusing rather than guessing at an unfamiliar type"
            % (question_type, sorted(JEV_ALLOWED_QUESTION_TYPES))
        )


class CapabilityProfile:
    """One model/capability pair: its last canary result and whether
    it is currently quarantined.

    canary_fn is injected (never a hardcoded live network call baked
    into this class), the same seam this repo already uses elsewhere:
    a real canary in production, a fake one in a hermetic test. Wiring
    a REAL live canary for Muse/Deepseek/Jev against the actual
    OpenRouter bridge is a follow-up task building on this gate, not
    included here: this class proves the caching/quarantine mechanism
    itself, not any particular model's live behavior today.
    """

    def __init__(self, model, capability, canary_fn, max_age_seconds=3600, clock=time.monotonic):
        # Names are shown escaped wherever they are shown: a raw newline in a
        # model or capability name forged a line in the error (proven 2026-09-20).
        self.model = model
        self.capability = capability
        self._canary_fn = canary_fn
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) \
                or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be a finite number > 0, got %r" % (max_age_seconds,))
        self._max_age = max_age_seconds
        self._clock = clock
        self._checked_at = None
        self.last_checked = False
        self.quarantined = False
        self.quarantine_reason = None

    def check(self, force=False):
        """Runs the real canary once, cached afterward unless force is
        set. Any canary exception is itself a failure (not something
        that propagates and crashes the caller), since an exception
        thrown by a canary is exactly as untrustworthy as a False
        return: the model's real behavior did not match what was
        expected, whatever the specific shape of the mismatch."""
        # A PASS is trusted only for max_age_seconds: models are rotated and
        # break after the canary ran, and a success cached for the life of the
        # process stayed trusted forever. A FAILURE never expires by itself:
        # only a fresh passing check lifts a quarantine.
        if self.last_checked and not force and (self.quarantined or not self._stale()):
            return not self.quarantined
        try:
            ok = bool(self._canary_fn())
        except Exception as exc:  # noqa: BLE001 (a canary's own exception surface is not this gate's concern; any failure quarantines)
            self.quarantined = True
            # the TYPE only: an exception's text can carry a token, a URL with a
            # key, or a payload, and this reason is shown and logged
            self.quarantine_reason = "canary raised %s" % type(exc).__name__
            self.last_checked = True
            self._checked_at = self._clock()
            return False
        self.last_checked = True
        self._checked_at = self._clock()
        if not ok:
            self.quarantined = True
            self.quarantine_reason = "canary returned a failing result"
        else:
            # a fresh PASS lifts the quarantine; before this, force=True could
            # pass and the capability stayed quarantined, so recovery was impossible
            self.quarantined = False
            self.quarantine_reason = None
        return ok

    def _stale(self):
        return self._checked_at is None or self._clock() - self._checked_at > self._max_age

    def _name(self):
        return "%r/%r" % (self.model, self.capability)

    def require_not_quarantined(self):
        """Call before trusting this capability's output. Raises if
        check() was never called (an unverified capability is not
        automatically trusted) or if it failed."""
        if not self.last_checked:
            raise QuarantinedCapability(
                "%s has never been canary-checked this session; "
                "call check() before trusting it" % self._name()
            )
        if self.quarantined:
            raise QuarantinedCapability(
                "%s is quarantined: %s" % (self._name(), self.quarantine_reason)
            )
        if self._stale():
            # an expired PASS is an unverified capability, not a trusted one
            raise QuarantinedCapability(
                "%s was last checked more than %s seconds ago; call check() again"
                % (self._name(), self._max_age)
            )

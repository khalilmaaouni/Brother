"""credential_broker: hand a caller the minimum credential for one stated
purpose, never the whole store.

UNIT DOM-40.04, "Secret boundary". THE DECIDING PROPERTY, verbatim from the
WBS entry: an agent receives the minimum credential with a narrow scope and
a stated purpose, never the whole store. A request with no purpose, a scope
wider than declared, an expired grant, or an unknown credential is refused.
The secret value never appears in any output.

WHAT THIS MODULE IS NOT. It is not a secret store, not a keychain wrapper,
not a .env reader, and it never touches the filesystem, the environment, or
a network. Every fact it decides on (the store, the request, the clock) is
handed in by the caller as plain data. Nothing here has a side effect:
request_credential() only classifies a request and returns a verdict, the
same shape as scripts/filesystem_enforcement.py's decide() and
scripts/capability_precheck.py's precheck(). The caller that owns a real
secret (an env var, a keychain item, a vault entry) reduces it to this
module's `store` shape before calling in, the same way a caller reduces a
richer host_capability_receipt() into capability_precheck.py's smaller
`reported` shape.

REUSE NOTE: scripts/orchestrator_invariants.py was read before writing this
module, per the worker contract. Its vocabulary (TASK_CLASSES, VERDICTS,
TASK_STATES, FAILURE_CLASSES, and the rest) describes the orchestration
task state machine: what a unit of WORK may become. A credential grant is a
different kind of decision entirely (may this caller hold this value for
this purpose right now), so nothing in that module fits here and nothing
here restates it. GRANTED and REFUSED below are this module's own, smaller
vocabulary, not a second copy of orchestrator_invariants.VERDICTS.

THE STORE SHAPE the caller passes to `store`: a dict of credential name
(str) to a dict with exactly three keys:
    value        str, the secret itself. Never empty.
    scopes       an iterable of str: every scope this credential may ever
                 be granted for. A request naming a scope outside this list
                 is refused, however innocuous the scope sounds.
    expires_at   a number (unix timestamp) after which the credential is no
                 longer grantable, or None to mean it never expires on its
                 own. The caller decides that number; this module only
                 compares it against the `now` the caller also passes in.

CONTINGENCY AND EDGES, named here because the docstring is where the next
reader looks before trusting or extending this module:

  unknown credential name         `name` is not a key in `store` at all.
                                   REFUSED, REASON_UNKNOWN_CREDENTIAL. Not
                                   even the fact that OTHER credentials
                                   exist is disclosed in the reason.
  no purpose stated                `purpose` is None, empty, or not a
                                   string. REFUSED, REASON_NO_PURPOSE. A
                                   credential is never handed out for an
                                   unstated reason, however narrow the
                                   scope.
  scope wider than declared        `scope` is not one of the credential's
                                   own declared `scopes`. REFUSED,
                                   REASON_WRONG_SCOPE. This is the one rule
                                   that makes "narrow scope" real instead of
                                   advisory: the store, not the caller,
                                   names what is grantable.
  expired grant                    `expires_at` is not None and `now` is at
                                   or past it. The boundary itself (now ==
                                   expires_at) counts as expired: a
                                   credential is good UNTIL its expiry, not
                                   THROUGH it, so there is no instant where
                                   "expired" and "still good" both apply.
  malformed store entry            the entry for `name` is missing a
                                   required key, `value` is empty or not a
                                   string, `scopes` is not an iterable of
                                   strings (a bare string counts as
                                   malformed here even though a string IS
                                   iterable, because iterating it would
                                   silently turn each character into a
                                   one-letter "scope"), or `expires_at` is
                                   present but neither None nor a number.
                                   REFUSED, REASON_MALFORMED_STORE. A store
                                   this module cannot trust to have the
                                   shape it declared is never trusted to
                                   grant anything either.
  malformed request                `store` is not a dict, or `name` /
                                   `scope` are not non-empty strings, or
                                   `now` is not a real number (NaN and
                                   infinity do not count: a clock never
                                   reports either). REFUSED,
                                   REASON_MALFORMED_REQUEST.
  a concurrent second actor,       does not apply: this function is pure
  already granted, partially       and stateless, holds nothing between
  done                             calls, and mutates nothing the caller
                                   passed in. A caller that wants to track
                                   "already granted this session" keeps
                                   that state itself, outside this module,
                                   the same way filesystem_enforcement.py's
                                   docstring names this as out of scope for
                                   a pure decision function.
  an exception this module did     caught by the outer try/except in
  not anticipate                   request_credential() and turned into
                                   REFUSED, REASON_MALFORMED_REQUEST,
                                   without including the exception's own
                                   text: an unanticipated failure mode is
                                   exactly the case most likely to carry a
                                   fragment of the secret in a traceback,
                                   so nothing from it reaches the reason.

THE ONE HARD SAFETY RULE, checked explicitly by this unit's test: the
secret value is returned ONLY inside a successful CredentialGrant's own
`.value` attribute, a field the caller must read on purpose. It is never
interpolated into a reason string, an exception message, a log line, or a
CredentialGrant's repr() or str(), both of which redact it.

Python 3, standard library only. No network, no filesystem, no subprocess,
no environment read, no clock read: every fact this module needs is passed
in by the caller.
"""

import math

GRANTED = "GRANTED"
REFUSED = "REFUSED"

REASON_UNKNOWN_CREDENTIAL = "unknown credential: not present in the store"
REASON_NO_PURPOSE = "no purpose stated: purpose must be a non-empty string"
REASON_WRONG_SCOPE = (
    "requested scope is not one of the credential's declared scopes")
REASON_EXPIRED = "credential has expired (now is at or past expires_at)"
REASON_MALFORMED_STORE = (
    "malformed store entry: missing or invalid value, scopes, or expires_at")
REASON_MALFORMED_REQUEST = (
    "malformed request: store, name, scope, or now is not the expected shape")


class CredentialGrant(object):
    """The one thing a successful request_credential() call returns. Holds
    the secret in `.value`, a field the caller reads on purpose; repr()
    and str() both redact it so an accidental print(), log call, or
    assertion failure message never carries the secret along."""

    __slots__ = ("name", "scope", "purpose", "value", "expires_at")

    def __init__(self, name, scope, purpose, value, expires_at):
        self.name = name
        self.scope = scope
        self.purpose = purpose
        self.value = value
        self.expires_at = expires_at

    def __repr__(self):
        return (
            "CredentialGrant(name=%r, scope=%r, purpose=%r, "
            "value=<redacted>, expires_at=%r)" %
            (self.name, self.scope, self.purpose, self.expires_at))

    def __str__(self):
        return self.__repr__()


def _is_number(value):
    """True for a real int or a finite float. Excludes bool (a bool is an
    int subclass in Python, but a clock reading or an expiry timestamp is
    never meaningfully True or False), NaN, and infinity: a clock never
    reports either of the latter two, so a value claiming to be one is
    treated as malformed rather than compared against."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _is_nonempty_str(value):
    return isinstance(value, str) and len(value) > 0


def request_credential(store, name, scope, purpose, now):
    """(verdict, reason, grant). verdict is GRANTED or REFUSED.

    On GRANTED, `reason` is None and `grant` is a CredentialGrant.
    On REFUSED, `grant` is None and `reason` is one of the module-level
    REASON_* strings, naming exactly which rule fired. Never raises: any
    error this function did not anticipate is caught and reported as
    REFUSED, REASON_MALFORMED_REQUEST, per the module docstring's
    CONTINGENCY note on unanticipated exceptions."""
    try:
        return _decide(store, name, scope, purpose, now)
    except Exception:  # noqa: BLE001 - last-resort fail-closed net
        return REFUSED, REASON_MALFORMED_REQUEST, None


def _decide(store, name, scope, purpose, now):
    if not isinstance(store, dict):
        return REFUSED, REASON_MALFORMED_REQUEST, None
    if not _is_nonempty_str(name):
        return REFUSED, REASON_MALFORMED_REQUEST, None
    if not _is_nonempty_str(scope):
        return REFUSED, REASON_MALFORMED_REQUEST, None
    if not _is_number(now):
        return REFUSED, REASON_MALFORMED_REQUEST, None

    if name not in store:
        return REFUSED, REASON_UNKNOWN_CREDENTIAL, None

    if not _is_nonempty_str(purpose):
        return REFUSED, REASON_NO_PURPOSE, None

    entry = store[name]
    if not isinstance(entry, dict):
        return REFUSED, REASON_MALFORMED_STORE, None
    if "value" not in entry or "scopes" not in entry or "expires_at" not in entry:
        return REFUSED, REASON_MALFORMED_STORE, None

    value = entry["value"]
    if not _is_nonempty_str(value):
        return REFUSED, REASON_MALFORMED_STORE, None

    scopes = entry["scopes"]
    if isinstance(scopes, (str, bytes)):
        # A bare string IS iterable, but iterating it yields one-letter
        # "scopes" nobody declared: treated as malformed, never as a
        # single-scope shorthand.
        return REFUSED, REASON_MALFORMED_STORE, None
    try:
        scope_list = list(scopes)
    except TypeError:
        return REFUSED, REASON_MALFORMED_STORE, None
    for declared_scope in scope_list:
        if not isinstance(declared_scope, str):
            return REFUSED, REASON_MALFORMED_STORE, None

    expires_at = entry["expires_at"]
    if expires_at is not None and not _is_number(expires_at):
        return REFUSED, REASON_MALFORMED_STORE, None

    if scope not in scope_list:
        return REFUSED, REASON_WRONG_SCOPE, None

    if expires_at is not None and now >= expires_at:
        return REFUSED, REASON_EXPIRED, None

    return GRANTED, None, CredentialGrant(name, scope, purpose, value, expires_at)

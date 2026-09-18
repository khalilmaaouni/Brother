"""capability_precheck: refuse dispatch when the ACTUAL granted capability
falls short of what a unit needs, instead of dispatching on the label.

UNIT ORCH-24. The recorded failure this exists to prevent: a client was
described as enforcing a boundary it only advises on, and work was
dispatched as if the enforcement were real (see products/brothermode/docs/
CURSOR-COMPAT.md's own honest-limit section, and scripts/host_capability.py's
cursor row: "enforceable_deny ... admitted missing"). A unit that NEEDS a
capability to be enforced must be refused, not silently downgraded, when the
host can only offer it as advisory or cannot attest to it at all.

THE ONE RULE THIS MODULE IS: a unit's required capabilities must each be
reported at level ENFORCED. ADVISORY never satisfies that (a boundary the
host only advises on is not a boundary it holds). A capability the host's
report never mentions is UNKNOWN, and UNKNOWN never satisfies a requirement
either: absence of a claim is not a grant. Any other value a report carries
for a capability (a typo, a half-measured claim, a level this module does
not recognize) is treated the same as UNKNOWN, named as such in the refusal:
a level this module cannot verify is not evidence the level is real.

REUSE NOTE: scripts/host_capability.py already exists and was read before
writing this module. It is NOT imported here, and this is not a case of
reimplementing what it provides: host_capability_receipt() returns fourteen
free-text facts about ONE host (prose like "yes, measured: ..." or
"partial: needs -s workspace-write"), not a level a precheck can compare
against a requirement. This module's REPORTED input is a different, smaller
shape on purpose (capability name -> "ENFORCED" | "ADVISORY" | anything
else): a caller that has a host_capability receipt reduces its own free text
to that shape before calling precheck(), the same way a caller reduces any
richer fact into a caller-relevant column. Nothing here duplicates
host_capability_receipt's field table, its CAPABILITY_TABLE, or its NODATA
detection helpers.

CONTINGENCY AND EDGES named here because the docstring is where the next
reader looks before trusting this module:

  A UNIT REQUIRING NOTHING. Decided: DISPATCH, unconditionally, even when
  the host report itself is NO-DATA. A requirement-free unit places no
  demand on the host to grant anything, so there is nothing for the host's
  reachability to fail. The NO-DATA-refuses-dispatch rule below binds only
  when there is a named capability need the host must be asked to satisfy.

  A HOST THAT CANNOT BE PROBED AT ALL (the reported argument is not a
  mapping: None, a string, a list, anything json.loads would not call an
  object). Read as NO-DATA and REFUSED whenever at least one capability is
  required, per the estate's "an unknown, corrupt or unreadable input
  BLOCKS; it never reads as the safe case" law: an unprobable host is not
  proof the capability is missing OR proof it is present, and dispatch never
  guesses in the permissive direction.

  AN EMPTY HOST REPORT ({}). Different from NO-DATA: the host WAS probed and
  it named zero capabilities. Every required capability reads as UNKNOWN
  (not mentioned) and the unit is refused, naming the first one.

  A HOST CLAIMING A LEVEL ABOVE WHAT IT CAN PROVE (a level string that is
  neither "ENFORCED" nor "ADVISORY": a made-up or unverified label). Treated
  identically to UNKNOWN and named as such in the refusal, quoting the
  unrecognized value: a claim this module cannot place on the ENFORCED/
  ADVISORY ladder is not evidence the ladder rung is real.

  A CAPABILITY PRESENT BUT AT A LOWER LEVEL (reported ADVISORY, required
  ENFORCED). Refused, naming both the capability and the level actually
  reported, so a reader can act on the gap (get the host to enforce it, or
  drop the requirement).

  DUPLICATE REQUIREMENTS. Deduplicated on the way in, order preserved, so a
  caller that names the same capability twice gets exactly the same verdict
  and reason as naming it once.

Python 3, standard library only. No network, no filesystem, no subprocess:
every fact this module needs is passed in by the caller.
"""

ENFORCED = "ENFORCED"
ADVISORY = "ADVISORY"
NODATA = "NO-DATA"

DISPATCH = "DISPATCH"
REFUSE = "REFUSE"

#: Levels a report entry can claim and have this module believe it. Anything
#: else (missing entirely, or any other string) is UNKNOWN: never satisfies.
_KNOWN_LEVELS = (ENFORCED, ADVISORY)


def _dedupe(names):
    """Required capability names, in first-seen order, with repeats dropped.
    A generator or any other one-pass iterable is accepted: `names` is
    consumed exactly once, into a plain list."""
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def precheck(required, reported):
    """(verdict, reason). verdict is DISPATCH or REFUSE.

    required: an iterable of capability names the unit needs the host to
    hold at ENFORCED level. An empty iterable means the unit needs nothing.

    reported: the host's ACTUAL granted capabilities, as a mapping of
    capability name -> level string. Anything that is not a mapping (None,
    a string, a list, ...) means the host could not be probed at all.

    The refusal reason always names the specific capability and the level
    that was missing or found instead, never a bare "refused"."""
    names = _dedupe(required)
    if not names:
        return DISPATCH, "unit requires no capabilities: nothing for the host to grant"

    if not isinstance(reported, dict):
        return REFUSE, (
            "%s: host capability report could not be read (not a mapping: %r); "
            "refusing dispatch rather than guessing the host is capable"
            % (NODATA, reported))

    for name in names:
        level = reported.get(name)
        if level is None:
            return REFUSE, (
                "capability %r requires ENFORCED but the host report does not "
                "mention it (UNKNOWN: absence of a claim is not a grant)" % name)
        if level == ENFORCED:
            continue
        if level == ADVISORY:
            return REFUSE, (
                "capability %r requires ENFORCED but the host reports ADVISORY "
                "only (a boundary the host only advises on is not one it holds)"
                % name)
        return REFUSE, (
            "capability %r requires ENFORCED but the host reports an "
            "unrecognized level %r (not ENFORCED or ADVISORY, treated as "
            "UNKNOWN: unverifiable is not verified)" % (name, level))

    return DISPATCH, "all %d required capability(ies) reported ENFORCED: %s" % (
        len(names), ", ".join(names))

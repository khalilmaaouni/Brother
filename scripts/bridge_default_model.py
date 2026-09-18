#!/usr/bin/env python3
"""bridge_default_model: the outside model bridge's default model, pinned to
one exact id, in a file this estate actually version controls.

WHY THIS EXISTS, ORCH-31. ~/.claude/bin/or_ask.py declares its own
DEFAULT_MODEL as a plain module level string, and it changed at least twice
with no commit recording the change, because that file lives outside every
repository (~/.claude/bin, never checked into git here). Nothing forced
anyone to notice a silent change to what the bridge answers with when no
--model is given. This module is the thing that notices: PINNED_DEFAULT_MODEL
below is the one place this estate declares what the bridge's default is
SUPPOSED to be, and check_pinned() proves whether the bridge's live source
still agrees with it.

WHY THIS NEVER IMPORTS OR RUNS or_ask.py. The same reasoning budget_floor.py
gives for the same file: it is not part of this package, it is not this
unit's to own, and importing it would execute its module level code (an
argparse parser build, a keychain-reading main only guarded by
__name__ == "__main__", but still someone else's file to run, not read). So
this module opens the bridge as plain text and looks for one line shaped
like DEFAULT_MODEL = "...". It never executes it, and it never needs the
network or the keychain to do this check (worker contract: tests must never
call the network or read the keychain).

THREE ANSWERS, NEVER TWO. A bridge that cannot be read, or whose source no
longer contains exactly one DEFAULT_MODEL assignment, is NO-DATA
(BridgeUnreadable): nothing was actually compared, so this is never reported
as a pass, and never silently reported as a fail either (worker contract
rule 1, NO-DATA is never a pass). A bridge that CAN be read but whose live
value differs from the pin is a real, known answer: DRIFT (DefaultModelDrift).
Only when the live value equals the pin is the result PASS. An ambiguous
source (more than one distinct DEFAULT_MODEL assignment) is also NO-DATA:
this module never guesses which one is live (worker contract rule 2,
unknown input raises rather than taking a permissive default).

Python 3.9 floor, standard library only. No network, no keychain, no
subprocess, no import of or_ask.py itself.
"""
import argparse
import re
import sys
from pathlib import Path

#: The one place this estate declares what the bridge's default model id is
#: SUPPOSED to be. Change this line, in a commit, when the bridge's own
#: default is deliberately changed: that commit is the whole fix for
#: "changed at least twice with no commit."
PINNED_DEFAULT_MODEL = "meta/muse-spark-1.3-contributor"

#: Where the bridge actually lives on this estate's machines, per its own
#: docstring and ~/.claude/bin/or_ask.py's usage line in every brief that
#: calls it. Overridable only for testing: production code never calls
#: read_bridge_default_model or check_pinned with a path other than this.
BRIDGE_PATH = Path("~/.claude/bin/or_ask.py").expanduser()

#: Line anchored match on a plain assignment to a double quoted string.
#: Anchored per line (re.MULTILINE) so it never matches a DEFAULT_MODEL
#: reference used as a value elsewhere in the file (for example
#: `default=DEFAULT_MODEL` inside an argparse call), only an assignment TO
#: the name at the start of a line. Allows leading whitespace, an optional
#: trailing inline comment, and an optional trailing carriage return.
_DEFAULT_MODEL_RE = re.compile(
    r'^[ \t]*DEFAULT_MODEL[ \t]*=[ \t]*"(?P<value>(?:\\.|[^"\\])*)"[ \t]*(?:#.*)?\r?$',
    re.MULTILINE,
)


class BridgeUnreadable(Exception):
    """NO-DATA: the bridge's live default could not be determined. Covers a
    missing file, a permissions error, any other OSError, a source with no
    DEFAULT_MODEL assignment at all, and a source with more than one
    distinct DEFAULT_MODEL assignment (an ambiguous source is refused, never
    resolved by picking the first or the last match)."""


class DefaultModelDrift(Exception):
    """The bridge was read successfully and its live DEFAULT_MODEL differs
    from the pinned value: a real, known answer, not a NO-DATA one. Carries
    both values and the path that was read, for the message and for a
    caller that wants to inspect them directly."""

    def __init__(self, path, pinned, live):
        self.path = path
        self.pinned = pinned
        self.live = live
        super().__init__(
            "pinned default model %r does not match %r, the live "
            "DEFAULT_MODEL declared in %s" % (pinned, live, path)
        )


def read_bridge_default_model(path=None):
    """Return the exact string the bridge's source currently assigns to
    DEFAULT_MODEL.

    path: a filesystem path (str or Path) to read instead of BRIDGE_PATH,
    used only by tests. None means BRIDGE_PATH.

    Raises BridgeUnreadable for every case that is not one clean answer:
    the file is missing or unreadable, no assignment is found, or more than
    one distinct assignment is found. Never returns None or an empty string
    as if that were a value.
    """
    target = BRIDGE_PATH if path is None else path

    try:
        with open(target, "r", encoding="utf-8") as handle:
            source = handle.read()
    except OSError as exc:
        raise BridgeUnreadable(
            "could not read bridge source at %s: %s" % (target, exc)
        ) from exc

    matches = _DEFAULT_MODEL_RE.findall(source)
    if not matches:
        raise BridgeUnreadable(
            "no DEFAULT_MODEL = \"...\" assignment found in %s" % target
        )

    distinct = sorted(set(matches))
    if len(distinct) > 1:
        raise BridgeUnreadable(
            "%d different DEFAULT_MODEL assignments found in %s (%s): "
            "refusing to guess which one is live"
            % (len(distinct), target, ", ".join(repr(d) for d in distinct))
        )

    return matches[0]


def check_pinned(path=None, pinned=PINNED_DEFAULT_MODEL):
    """Raise BridgeUnreadable (NO-DATA) or DefaultModelDrift (a real
    mismatch); return the matching value on a real match. Never returns a
    partial or best-guess result.

    path: passed straight through to read_bridge_default_model.
    pinned: the value to compare the live default against, defaulting to
    this module's own PINNED_DEFAULT_MODEL; a caller may pass a different
    value to check an old or proposed pin without editing this file.
    """
    live = read_bridge_default_model(path)
    if live != pinned:
        raise DefaultModelDrift(BRIDGE_PATH if path is None else path, pinned, live)
    return live


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--bridge",
        default=None,
        help="path to the bridge source (default: %s)" % BRIDGE_PATH,
    )
    args = ap.parse_args(argv)

    try:
        live = check_pinned(path=args.bridge)
    except BridgeUnreadable as exc:
        print("NO-DATA: %s" % exc, file=sys.stderr)
        return 2
    except DefaultModelDrift as exc:
        print("DRIFT: %s" % exc, file=sys.stderr)
        return 1

    print(
        "PASS: pinned default model %r matches the bridge's live default (%s)"
        % (live, args.bridge or BRIDGE_PATH)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

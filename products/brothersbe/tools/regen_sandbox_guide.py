#!/usr/bin/env python3
"""Realign the one derived value docs/guides/00-sandbox.md pins.

WHY THIS EXISTS

`docs/guides/00-sandbox.md` is a transcript: every block in it is real output
from a real run against the sandbox that `tools/fixtures/sandbox/
build_sandbox.py` builds. Two of those blocks quote a git object id, the head
the decision record binds to:

    sbe review: review record written, bound to head <12 hex>, ...
    sbe handover prepare: written, bound to head <12 hex>: ...

`tools/test_sbe_sandbox.py` drives the same eight steps live and asserts that
the head it computes is the head the guide quotes. That assertion is not
decoration. It is the proof that the sandbox build still produces the exact
bytes the guide claims it produces, which is the whole reason a beginner can
trust that page. Masking the hash would delete the property, so the founder's
recorded decision is to KEEP the pinned value and automate its realignment
instead.

Automating it is what this script is. Before it existed, the only way to
realign the guide was to paste two fresh hashes by hand, which the project's
own handover names as the wrong fix: re-pinning breaks again on the very next
commit that moves the sandbox inputs, and a document nobody can regenerate is
a document that trains people to treat red CI as noise.

The standing rule this satisfies, recorded as NS-080: no shipped document may
pin a value that changes on commit unless a command regenerates it. This is
that command.

WHAT IT DOES, AND WHAT IT REFUSES TO DO

It drives the real journey through the real engine, reads the decision head
that journey produced, and rewrites only the twelve hex characters after the
literal text "bound to head " in the guide. It does not regenerate the rest of
the transcript, does not reflow prose, and does not touch any other file. A
narrow rewrite is the point: anything wider would let this script quietly
launder an unrelated change into a shipped document.

USAGE

    python3 tools/regen_sandbox_guide.py            rewrite the guide
    python3 tools/regen_sandbox_guide.py --check    report drift, write nothing

--check exits 1 when the guide is stale and 0 when it matches, so it can be
wired into a gate. Finding no pinned head at all is NOT a pass: it means the
guide changed shape underneath this script, and that exits 2 rather than
reporting success over something it never examined.

Remember that adding or changing a tracked file invalidates CHECKSUMS.sha256.
Regenerate it with `scripts/checksums.sh CHECKSUMS.sha256` as the LAST action
before committing.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
GUIDE = os.path.join(ROOT, "docs", "guides", "00-sandbox.md")

sys.path.insert(0, HERE)
# Every report line this script prints goes through say(), the project's one
# choke point, which flattens the WHOLE formatted line rather than each
# interpolated value. That is not tidiness: this script interpolates a file
# path and two git object ids, and a value carrying a newline could otherwise
# write a byte-perfect verdict line for another gate into the report above the
# true one. The honesty meta-test refused the first version of this file for
# exactly that reason, at two print sites, which is the control working.
from sbe_checks import say  # noqa: E402  (path setup has to come first)

# The patterns this script is allowed to touch. Every one is anchored on the
# literal prose around it so it can never match a bare hash elsewhere on the
# page.
#
# There used to be ONE pattern here, for "bound to head", and the docstring
# above said the guide quotes a git object id in two blocks. That was true of
# the two blocks somebody had been burned by. The guide pins THREE derived
# values, and running the repin then watching tools/test_sbe_sandbox.py go from
# four failures to two rather than to zero is what surfaced the other one: the
# SEED commit, which appears both as "Base <sha>." in the task-open line and as
# "<sha>..HEAD" in the evidence line, was never repinned by anything.
#
# That is the same shape as the release checklist this estate already corrected
# twice: a tool that repins exactly the values a past failure taught it about
# is only ever as complete as that failure. So the pins are a LIST now, and
# adding a fourth means adding a row rather than remembering to.
PINS = (
    # name, pattern with the value as group 2, key in the journey's output
    ("decision head", re.compile(r"(bound to head )([0-9a-f]{12})"), "decision"),
    ("task-open base", re.compile(r"(Base )([0-9a-f]{12})(?=\.)"), "seed"),
    ("evidence diff range", re.compile(r"(from the diff )([0-9a-f]{12})(?=\.\.HEAD)"), "seed"),
)

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_SHAPE = 2


def computed_pins():
    """Drive the real sandbox journey once and return every value the guide pins.

    Imports the test module rather than reimplementing the journey, because a
    second implementation of these eight steps would be a second thing to keep
    true, and the two would drift.

    Both values are read out of what the journey actually PRINTED rather than
    recomputed here, for the same reason: the guide quotes those lines verbatim,
    so the only value that can be correct is the one the tools emitted.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import test_sbe_sandbox as suite

    case = suite.TestSandboxJourneyMatchesGuide
    case.setUpClass()
    try:
        out = dict(case.out)
    finally:
        teardown = getattr(case, "tearDownClass", None)
        if teardown is not None:
            teardown()

    decision = (out.get("decision_head") or "")[:12]
    if not decision:
        raise RuntimeError(
            "the sandbox journey produced no decision_head; the fixture ran but "
            "recorded nothing, so this script has nothing to align the guide to")

    seed_m = re.search(r"Base ([0-9a-f]{12})", out.get("start") or "")
    if not seed_m:
        raise RuntimeError(
            "the sandbox journey printed no 'Base <12 hex>' line, so the seed "
            "commit this guide pins could not be read from what the tools "
            "emitted. Refusing to guess it: a hash this script computed itself "
            "rather than read back is exactly the second implementation the "
            "docstring above says not to build")
    return {"decision": decision, "seed": seed_m.group(1)}


def main(argv):
    check_only = "--check" in argv[1:]
    unknown = [a for a in argv[1:] if a != "--check"]
    if unknown:
        sys.stderr.write("regen_sandbox_guide: unrecognised argument(s): %s\n"
                         % ", ".join(unknown))
        return EXIT_SHAPE

    with io.open(GUIDE, encoding="utf-8") as fh:
        text = fh.read()

    present = [(name, rx, key) for name, rx, key in PINS if rx.search(text)]
    if not present:
        sys.stderr.write(
            "regen_sandbox_guide: NO-DATA, %s carries none of the %d pinned "
            "shapes this script knows (%s). The guide changed shape, so this "
            "script examined nothing and is reporting that rather than a pass.\n"
            % (GUIDE, len(PINS), ", ".join(n for n, _, _ in PINS)))
        return EXIT_SHAPE

    absent = [name for name, rx, _ in PINS if not rx.search(text)]
    if absent:
        sys.stderr.write(
            "regen_sandbox_guide: NO-DATA, %s carries some pinned shapes and "
            "not others; missing: %s. A partial rewrite would leave the guide "
            "internally inconsistent, which is worse than not rewriting it.\n"
            % (GUIDE, ", ".join(absent)))
        return EXIT_SHAPE

    pins = computed_pins()

    stale, total = [], 0
    for name, rx, key in present:
        want = pins[key]
        found = rx.findall(text)
        total += len(found)
        for m in found:
            pinned = m[1] if isinstance(m, tuple) else m
            if pinned != want:
                stale.append("%s pins %s and the journey produces %s"
                             % (name, pinned, want))
    stale = sorted(set(stale))

    if not stale:
        say("regen_sandbox_guide: PASS, all %d pinned value(s) across %d shape(s) "
            "already match the journey" % (total, len(present)))
        return EXIT_OK

    if check_only:
        sys.stderr.write(
            "regen_sandbox_guide: DRIFT across %d occurrence(s). %s. Run this "
            "script without --check and commit the result.\n"
            % (total, "; ".join(stale)))
        return EXIT_DRIFT

    rewritten = text
    for name, rx, key in present:
        want = pins[key]
        rewritten = rx.sub(lambda m, w=want: m.group(1) + w, rewritten)
    with io.open(GUIDE, "w", encoding="utf-8") as fh:
        fh.write(rewritten)
    say("regen_sandbox_guide: rewrote %d pinned value(s) across %d shape(s) in %s"
        % (total, len(present), os.path.relpath(GUIDE, ROOT)))
    for line in stale:
        say("regen_sandbox_guide:   %s" % line)
    say("regen_sandbox_guide: now run scripts/checksums.sh CHECKSUMS.sha256 "
        "as the LAST action before you commit.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))

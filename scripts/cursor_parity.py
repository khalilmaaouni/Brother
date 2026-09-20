#!/usr/bin/env python3
"""cursor_parity: DOM-50.06. Names every outstanding Cursor portability gap
the shared conformance suite measures, and never lets one hide inside a
bare pass/fail/no-data count.

WHY THIS FILE EXISTS. The twelve-step ordered suite that PROVES a
provider's port lives in scripts/adapter_conformance.py and is not
restated here: this module calls its run_conformance() with
provider="cursor" and reads back the same Step objects every other
provider is graded by, so the Cursor host is held to the identical
contract as Claude or Codex, never a smaller or Cursor-only one.
adapter_conformance.py's own CLI reports a one-line count per provider
(pass=N fail=N no-data=N); a count is exactly where one failing step
disappears, because a reader sees "no-data=6" and has to go re-run the
whole suite to learn which six of the twelve steps that was. This module
adds nothing to the suite's own logic (no new step, no new verdict rule):
it only reduces the same Step list to a GAP list, one entry per step that
did not read PASS, each carrying that step's own name and reason verbatim,
so "cursor is not at parity" always names which step and why.

WHAT THIS MODULE ACTUALLY IS, AND IS NOT. It is an on-demand gap reporter
that pays client_parity.py's existence-debt requirement: the way
scripts/codex_parity.py exists as Codex's twin, this file exists as
Cursor's, so client_parity.py can confirm a Cursor conformance path is
present at all. Its own gap-reporting CLI is not run by client_parity.py, or
by any other gate (scripts/check_all.sh runs this module's test suite,
test_cursor_parity.py, never cursor_parity.py itself as a live verdict),
and no Cursor-specific input (a hook removed from the shipped manifest, a
missing hooks.json, invocation() always refusing) can move its verdict:
that verdict only ever moves between shades of NO-DATA, because the six
lifecycle steps below are NO-DATA by construction, not by measurement.
The real canary property, that Cursor's hooks actually fire and
permission:deny is actually honored under a live, signed-in Cursor
session, remains UNMEASURED and open; this module does not check it and
must not be read as having replaced it.

WHY THE VERDICT IS HONESTLY NO-DATA, NOT A DEFECT IN THIS MODULE. Cursor
plugin-dir hooks do not fire headless (measured, scripts/cursor_smoke.py's
own --signed-in gate; docs/cursor/SMOKE-RUNBOOK.md), and
scripts/cursor_plugin_install.py's install/uninstall verbs are a plain
directory copy with no --ref version pin, a different shape from
brother_install.py's marketplace-driven lifecycle the shared suite's
install/upgrade/rollback/uninstall steps drive. scripts/provider_adapter.py's
CursorAdapter therefore refuses those four capabilities rather than
guessing at a live run (see its own docstring), at the honest cost of six
named NO-DATA gaps instead of a human clicking through a real install.
Steps 2-7 (same-task through resume) do not touch any provider binary at
all (see adapter_conformance.py's own module docstring) and DO measure
something real: that scripts/brother_run.py's own seams work identically
whether this suite is asked about "codex" or "cursor".

THE DECIDING PROPERTY this module holds: the same conformance contract
passes for the Cursor host, and each outstanding portability gap is named,
never silently skipped; an unobserved behaviour is NO-DATA, never PASS.
That last clause is adapter_conformance's own rule (its _verdict_for:
FAIL beats NO-DATA beats PASS, so a run with one no-data step can never
overall-read as PASS), inherited unchanged rather than re-derived here: a
second copy of that priority written in this file could drift from the
one adapter_conformance.py actually enforces.

Mirrors scripts/codex_parity.py (DOM-50.05): same shape, same template,
"codex" swapped for "cursor" throughout, nothing else added or removed.

Python 3.9, standard library only. No network, no subprocess and no file
I/O of its own: everything that touches a host, a binary or a filesystem
happens inside adapter_conformance.run_conformance, which this module
calls exactly once per run() and otherwise only reduces the plain Step
objects that call already returned.

No em or en dashes anywhere in this file or its output.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import adapter_conformance as AC  # noqa: E402

PASS, FAIL, NODATA = AC.PASS, AC.FAIL, AC.NODATA

#: Where a bare `python3 scripts/cursor_parity.py` writes its evidence when
#: the caller names no directory of its own: the same default root
#: adapter_conformance.py's own CLI uses for the "cursor" provider, so a
#: run of either tool against defaults lands in the same place.
DEFAULT_EVIDENCE_DIR = os.path.join(AC.DEFAULT_EVIDENCE_ROOT, "cursor")


def gap_list(results):
    """[{"step":, "verdict":, "reason":}] for every entry in `results`
    whose verdict is not exactly PASS, in the order `results` was given
    (this function never sorts or reorders: adapter_conformance.STEP_ORDER
    already fixed the order these Steps were produced in, and a second
    opinion about that order does not belong here).

    A Step carrying a verdict this module has never seen (anything other
    than PASS, FAIL or NO-DATA, which would itself be a defect somewhere
    upstream) is still included: an unrecognised verdict is not evidence
    the step passed, so the safe read is "gap", never "skip"."""
    gaps = []
    for result in results:
        if result.verdict == PASS:
            continue
        gaps.append({"step": result.name, "verdict": result.verdict,
                    "reason": result.reason})
    return gaps


def run(evidence_dir=None, offline=False, marketplace=None, ref=None,
       from_ref=None):
    """(verdict, results, gaps) for the Cursor host. `results` is the exact
    ordered Step list adapter_conformance.run_conformance returns for any
    provider; `gaps` is gap_list()'s reduction of it; `verdict` is
    adapter_conformance._verdict_for(results), reused rather than
    reimplemented so this module cannot answer a different question about
    the same twelve Steps than adapter_conformance.py itself would.

    Never raises past adapter_conformance.run_conformance's own contract:
    that function already turns every step's own failure into a Step
    rather than an exception (see its module docstring), and nothing added
    here introduces a new way to raise."""
    base = evidence_dir or DEFAULT_EVIDENCE_DIR
    results = AC.run_conformance("cursor", base, offline,
                                 marketplace=marketplace, ref=ref,
                                 from_ref=from_ref)
    verdict = AC._verdict_for(results)
    return verdict, results, gap_list(results)


#: Exit codes mirror adapter_conformance.py's own convention (see its
#: module docstring): PASS 0, FAIL 1, NO-DATA 2. A verdict outside this
#: mapping cannot come from AC._verdict_for (it only ever returns one of
#: these three strings), so the .get() default below is unreachable in
#: practice; it exists only so an exit code is still returned rather than
#: raised if that ever stopped being true.
_EXIT_CODES = {PASS: 0, FAIL: 1, NODATA: 2}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--offline", action="store_true",
                   help="skip every step that would install, upgrade, roll "
                        "back or uninstall the Cursor host for real; those "
                        "steps read NO-DATA and are named as gaps rather "
                        "than silently omitted")
    ap.add_argument("--marketplace", default=None,
                   help="git marketplace source for the lifecycle steps "
                        "(default: adapter_conformance.py's own default)")
    ap.add_argument("--ref", default=None,
                   help="the tag the upgrade step moves TO (default: "
                        "adapter_conformance.py's own default)")
    ap.add_argument("--from-ref", default=None,
                   help="the tag install runs at and upgrade moves FROM "
                        "(default: adapter_conformance.py's own default)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    verdict, results, gaps = run(evidence_dir=args.evidence_dir,
                                 offline=args.offline,
                                 marketplace=args.marketplace, ref=args.ref,
                                 from_ref=args.from_ref)
    print("== cursor parity ==")
    for r in results:
        print(r.line())
    if gaps:
        print("")
        for gap in gaps:
            print("GAP: %-16s %-8s %s" % (gap["step"], gap["verdict"],
                                          gap["reason"]))
    print("")
    print("cursor parity verdict=%s gaps=%d" % (verdict, len(gaps)))

    return _EXIT_CODES.get(verdict, 1)


if __name__ == "__main__":
    sys.exit(main())

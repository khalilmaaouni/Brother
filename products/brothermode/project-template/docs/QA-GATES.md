# <Project name>, QA gates

What this is: the exact commands that prove this project works, so nothing
is taken on faith. Who reads it: anyone about to merge a change, and anyone
auditing whether a "tested" claim is actually enforced somewhere rather than
just written down in a README.

Every gate below must name a command you have actually run, with output you
have actually seen. Never invent a command or a result; if a gate does not
exist yet, write "not yet built" rather than a command that has not been
tried.

## The gates

| Gate | Exact command | Expected output | What it protects | Blocks release |
|---|---|---|---|---|
| <name, worked example: "Export correctness test"> | `<the exact command, e.g. python3 -m pytest tests/test_export.py -q>` | `<what a passing run actually prints, e.g. "5 passed in 0.2s">` | `<the real risk this catches, e.g. "a member ever receiving another member's rows">` | `<yes or no, and on what: every push, only before a tagged release, run by hand only>` |

## The calibration rule

A test suite is not proof by itself. For any gate you claim protects
something, you should be able to answer: if I deliberately reintroduced the
exact defect this gate is meant to catch, would the gate actually fail, and
would it fail for the stated reason? If you have not tried this, the gate is
unproven, write that down rather than claiming more than you have checked.

Worked example of a calibrated gate: the export test above was checked by
temporarily removing the member id filter from the query, rerunning the
test, confirming it failed with a message naming the leaked row, then
restoring the filter and confirming the suite passed again.

## Known gaps, stated plainly

<List anything you know is untested or unproven right now, rather than
letting the table above imply more coverage than actually exists. Worked
example: "No test yet covers what happens if two export requests for the
same member run at the same time. This is a known gap, not a hidden one.">

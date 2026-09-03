#!/usr/bin/env python3
"""The A0 to A3 autonomy dial, wired: docs/plan/AUTONOMY-POLICY-V1.md made
runnable. That document is the design of record (founder ruling, question
UI, recorded in the V1 roadmap amendment); this file adds nothing to the
policy, it only makes section 5's YAML core executable as the pure
functions this estate's tests can drive, matching the house style set by
scripts/task_watchdog.py.

TWO AXES, THIS FILE OWNS ONE. Section 1 of the policy: sandbox (what is
POSSIBLE) and approval policy (when Brother ASKS) are independent axes.
This file is the approval axis only; it never touches sandbox, and nothing
here ever runs a command, opens a task, or executes anything. gate()
returns a decision string; the caller decides what to do with it, matching
section 7's V1 scope ("this policy does not introduce a second enforcement
layer, a policy daemon, or a new gate script").

THE DIAL is the approval-policy ceiling an operator sets for a run, read
from ONE place: the BROTHER_AUTONOMY_DIAL env var, default A1 (state the
interpretation and continue), matching the founder ruling that low risk
reversible work continues after Brother states its interpretation and stop
condition. classify() computes what an ACTION's own risk requires from its
observable shape (section 2's decision table, first match wins, defaulting
to the more cautious reading on any missing observable). gate() combines
the two: the EFFECTIVE class is whichever of the two is stricter, so the
dial can only ADD ceremony, never remove ceremony an action's own risk
already demands.

THE SAFETY FLOOR IS STRUCTURAL, NOT A SETTING. A3_FLAGS mirrors section 2's
seven-item boundary list verbatim. Any one of those true makes classify()
return "A3" regardless of every other observable and regardless of the
dial (section 4: the per-action check "always runs on top"; section 6,
examples 3 and 4: neither a blanket instruction nor a wide-open sandbox
authorizes an A3 action). Because A3 sorts above every dial position in
ORDER, no dial setting, however permissive, can turn an A3 action into
anything but refuse_until_approved. There is no parameter, flag, or dial
value anywhere in this file that reaches those seven keys from the
permissive side; they are only ever set true by describing what the
action actually is.
"""

import json
import os
import sys

ORDER = ["A0", "A1", "A2", "A3"]

ACTIONS = {
    "A0": "execute_then_check",
    "A1": "state_interpretation_then_continue",
    "A2": "ask_one_blocking_question",
    "A3": "refuse_until_approved",
}

# Section 2's A3 boundary list, verbatim.
A3_FLAGS = (
    "destructive_data_change",
    "auth_or_permission_change",
    "production_deploy",
    "publication",
    "financial_or_contractual",
    "merge_or_release",
    "credentials",
)

DEFAULT_DIAL = "A1"
DIAL_ENV_VAR = "BROTHER_AUTONOMY_DIAL"


def classify(observables):
    """Section 2's decision table, first match wins. `observables` is a
    plain dict; every key is optional. A caller that names nothing lands
    on A2 (moderate risk, ask), never on A0: an unnamed action is treated
    as the more cautious reading, not the more permissive one."""
    observables = observables or {}

    if any(observables.get(flag) for flag in A3_FLAGS):
        return "A3"

    if (observables.get("single_file_or_named_target")
            and observables.get("contract_change", "none") == "none"
            and not observables.get("crosses_boundary")
            and observables.get("reversible_under_hour")):
        return "A0"

    if (not observables.get("interpretation_ambiguous")
            and not observables.get("multi_file_or_shared_module")
            and observables.get("blast_radius") == "small_and_named"):
        return "A1"

    return "A2"


def dial_level(env=None):
    """The dial's current setting, read from the one place it lives. Unset
    or unrecognized reads as the documented default A1, never as the most
    permissive value A0."""
    env = os.environ if env is None else env
    raw = (env.get(DIAL_ENV_VAR) or "").strip().upper()
    return raw if raw in ORDER else DEFAULT_DIAL


def effective_class(action_class, dial):
    """The stricter (higher ceremony) of the two always governs: the dial
    can add ceremony an action's own risk did not already require, it can
    never remove ceremony the action's own risk demands."""
    if action_class not in ORDER:
        action_class = "A2"
    if dial not in ORDER:
        dial = DEFAULT_DIAL
    return max(action_class, dial, key=ORDER.index)


def gate(observables, dial=None):
    """THE function other code calls to decide whether one action
    proceeds, asks, or refuses. Returns one of ACTIONS' values. Never
    executes, opens, or commits anything itself; the caller acts on the
    string. `dial` defaults to dial_level() (the one env var) when not
    supplied, so a caller need pass nothing to get the live setting."""
    action_class = classify(observables)
    dial = dial_level() if dial is None else dial
    return ACTIONS[effective_class(action_class, dial)]


def main(argv):
    dial_override = None
    observables = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dial" and i + 1 < len(argv):
            dial_override = argv[i + 1].strip().upper()
            i += 2
            continue
        if arg == "--json" and i + 1 < len(argv):
            try:
                observables = json.loads(argv[i + 1])
            except ValueError:
                print("autonomy-dial: NO-DATA: --json value is not valid "
                      "JSON; nothing here is a decision")
                return 2
            i += 2
            continue
        i += 1

    if dial_override is not None and dial_override not in ORDER:
        print("autonomy-dial: NO-DATA: --dial %r is not one of %s"
              % (dial_override, ORDER))
        return 2

    dial = dial_override if dial_override is not None else dial_level()
    action_class = classify(observables)
    effective = effective_class(action_class, dial)
    decision = ACTIONS[effective]
    print("autonomy-dial: action_class=%s dial=%s effective=%s decision=%s"
          % (action_class, dial, effective, decision))

    if decision == "refuse_until_approved":
        return 3
    if decision == "ask_one_blocking_question":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

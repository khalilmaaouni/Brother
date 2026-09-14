#!/usr/bin/env python3
"""Refuses a 4+ agent dispatch that cannot name its dependency graph.

FOUNDER LAW 2026-09-08: "AGENT COUNT FROM THE DEPENDENCY GRAPH, never a
slogan: parallelize only independent partitions... capped by account
limits." That law was UNENFORCED -- a sentence, not a control. This is the
control: a plan dispatching 4 or more agents at once must name its
dependency graph AND state a real reason the partitions are independent.
Below 4 agents, dispatch is always allowed (no slogan needed for a small,
easily-supervised fan-out).

Exit 0: dispatch allowed, message explains why.
Exit 1: dispatch refused, message names the missing piece.
Python 3.9 floor, standard library only.
"""

import argparse
import sys

_PLACEHOLDER_REASONS = {"n/a", "na", "none", "tbd", "todo", "-", "reason"}


def check_dispatch_plan(
    agent_count: int, dependency_graph_named: bool, reason: str = ""
) -> tuple[bool, str]:
    """Decide whether a dispatch of agent_count agents may proceed.

    Below 4 agents: always allowed. At 4 or more: allowed only when
    dependency_graph_named is True AND reason is a real, non-placeholder
    string -- a technically-True flag with no content behind it must not
    count (that is the exact edge case this gate exists to catch).
    """
    if agent_count < 4:
        return True, f"{agent_count} agent(s): below the 4-agent threshold, no dependency graph required."

    reason_stripped = reason.strip()
    reason_is_real = bool(reason_stripped) and reason_stripped.lower() not in _PLACEHOLDER_REASONS

    if dependency_graph_named and reason_is_real:
        return (
            True,
            f"{agent_count} agents: dependency graph named, reason given ({reason_stripped!r}). Dispatch allowed.",
        )

    missing = []
    if not dependency_graph_named:
        missing.append("no dependency graph named")
    if not reason_is_real:
        missing.append("no real reason given (empty, whitespace, or a placeholder does not count)")

    return (
        False,
        f"{agent_count} agents refused: {'; '.join(missing)}. "
        "The 2026-09-08 law requires agent count to come from the dependency graph, never a slogan: "
        "a 4+ agent dispatch must name its dependency graph and state why the partitions are independent "
        "before it is allowed to fan out.",
    )


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Gate on the 2026-09-08 law: 'AGENT COUNT FROM THE DEPENDENCY GRAPH, never a "
            "slogan.' A dispatch of 4+ agents must name its dependency graph and state a real "
            "reason the partitions are independent. Exit 0: dispatch allowed. Exit 1: dispatch "
            "refused, reason named. Never a silent pass."
        )
    )
    parser.add_argument("--agents", type=int, required=True, help="number of agents the plan wants to dispatch at once")
    parser.add_argument("--graph-named", action="store_true", help="the dependency graph partitioning the work has been named")
    parser.add_argument("--reason", default="", help="why the partitions are independent (e.g. a path to the named graph)")
    args = parser.parse_args(argv)

    allowed, message = check_dispatch_plan(args.agents, args.graph_named, args.reason)
    print(message)
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

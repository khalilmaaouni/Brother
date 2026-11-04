#!/usr/bin/env python3
"""dream_replay: offline replay of recorded control decisions (unit D6+D8).

A world is the list returned by dream_record.read_decisions for one run. A
candidate policy is a pure function policy(kind, observed, options) -> option.
Replay walks the recorded decisions in order, asks the policy for each one,
and reveals the recorded outcome only when the policy picks the recorded
choice. Any other choice is a coverage gap: the result is UNSUPPORTED and is
never guessed.

Ungraded, conflict, and unreadable decisions are excluded and counted. The
policy never sees the recorded outcome, the recorded chosen option, or any
other decision from the world.
"""

import copy
import math


STATUSES = ("PASS", "FAIL", "NO-DATA")

_EXCLUDED_KEYS = (
    "ungraded",
    "conflict",
    "unreadable",
    "filtered",
    "policy_error",
    "invalid_choice",
    "missing_world",
)


def _empty_result():
    return {
        "status": "OK",
        "supported": 0,
        "PASS": 0,
        "FAIL": 0,
        "NO-DATA": 0,
        "unsupported": 0,
        "support_ratio": 0.0,
        "cost": None,
        "cost_n": 0,
        "excluded": {key: 0 for key in _EXCLUDED_KEYS},
    }


def _valid_cost(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _set_ratio(result):
    denominator = result["supported"] + result["unsupported"]
    result["support_ratio"] = (
        float(result["supported"]) / denominator if denominator else 0.0
    )


def _grade_row(row):
    """Return (state, status, cost, cost_n) for one recorded decision row.

    `state` is "graded", "ungraded", "conflict", or "unreadable". The outcome
    is not exposed to any policy; this is only used by replay after the policy
    has chosen the recorded option.
    """
    outcomes = row.get("outcomes")
    if not isinstance(outcomes, (list, tuple)):
        outcomes = []

    state = row.get("state")
    if state not in ("graded", "ungraded", "conflict"):
        statuses = set()
        for outcome in outcomes:
            if isinstance(outcome, dict):
                status = outcome.get("status")
                if status is not None:
                    statuses.add(status)
        if not statuses:
            state = "ungraded"
        elif len(statuses) > 1:
            state = "conflict"
        else:
            state = "graded"

    if state != "graded":
        return state, None, None, 0

    statuses = []
    cost_sum = 0.0
    cost_n = 0
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return "unreadable", None, None, 0
        status = outcome.get("status")
        if status not in STATUSES:
            return "unreadable", None, None, 0
        statuses.append(status)
        cost = outcome.get("cost")
        if cost is not None:
            if not _valid_cost(cost):
                return "unreadable", None, None, 0
            cost_sum += float(cost)
            cost_n += 1

    unique = set(statuses)
    if len(unique) != 1:
        if not unique:
            return "unreadable", None, None, 0
        return "conflict", None, None, 0

    return "graded", unique.pop(), (cost_sum if cost_n else None), cost_n


def replay(worlds, policy, kinds=None):
    """Replay one or more worlds against a candidate policy.

    `worlds` is an iterable of worlds. Each world is the list returned by
    dream_record.read_decisions for one run, or None for a missing journal.
    `policy` must be callable as policy(kind, observed, options) -> option.
    `kinds`, when provided, restricts replay to those decision kinds.

    Returns a dict with per-world results under "worlds" and aggregate counts
    under "overall". Counts include supported, PASS, FAIL, NO-DATA,
    unsupported, support_ratio, measured cost, and excluded decision reasons.
    """
    if not callable(policy):
        raise TypeError("policy must be callable")

    if kinds is not None:
        if isinstance(kinds, str):
            kinds = {kinds}
        else:
            kinds = set(kinds)

    if worlds is None:
        overall = _empty_result()
        overall["status"] = "NO-DATA"
        overall["excluded"]["missing_world"] = 1
        return {"worlds": [], "overall": overall}

    try:
        world_list = list(worlds)
    except TypeError:
        raise TypeError("worlds must be an iterable of worlds or None")

    world_results = []
    overall = _empty_result()

    for index, world in enumerate(world_list):
        world_result = _empty_result()
        world_result["index"] = index

        if world is None:
            world_result["status"] = "NO-DATA"
            world_result["excluded"]["missing_world"] += 1
            world_results.append(world_result)
            overall["excluded"]["missing_world"] += 1
            continue

        if not isinstance(world, (list, tuple)):
            world_result["status"] = "NO-DATA"
            world_result["excluded"]["unreadable"] += 1
            world_results.append(world_result)
            overall["excluded"]["unreadable"] += 1
            continue

        for row in world:
            if not isinstance(row, dict):
                world_result["excluded"]["unreadable"] += 1
                continue

            record = row.get("record")
            if not isinstance(record, dict):
                world_result["excluded"]["unreadable"] += 1
                continue

            kind = row.get("kind")
            if kind is None:
                kind = record.get("kind")
            if not isinstance(kind, str) or not kind:
                world_result["excluded"]["unreadable"] += 1
                continue

            if kinds is not None and kind not in kinds:
                world_result["excluded"]["filtered"] += 1
                continue

            options = record.get("options")
            if not isinstance(options, (list, tuple)) or not options:
                world_result["excluded"]["unreadable"] += 1
                continue
            options_list = list(options)

            chosen = record.get("chosen")
            if chosen not in options_list:
                world_result["excluded"]["unreadable"] += 1
                continue

            state, status, cost, cost_n = _grade_row(row)
            if state != "graded":
                key = state if state in _EXCLUDED_KEYS else "unreadable"
                world_result["excluded"][key] += 1
                continue

            if status not in STATUSES:
                world_result["excluded"]["unreadable"] += 1
                continue

            observed = record.get("observed")
            try:
                choice = policy(kind, copy.deepcopy(observed), list(options_list))
            except Exception:
                world_result["excluded"]["policy_error"] += 1
                continue

            if choice == chosen:
                world_result["supported"] += 1
                world_result[status] += 1
                if cost is not None:
                    if world_result["cost"] is None:
                        world_result["cost"] = 0.0
                    world_result["cost"] += cost
                    world_result["cost_n"] += cost_n
            else:
                world_result["unsupported"] += 1
                if choice not in options_list:
                    world_result["excluded"]["invalid_choice"] += 1

        _set_ratio(world_result)
        if world_result["supported"] + world_result["unsupported"] > 0:
            world_result["status"] = "OK"
        else:
            world_result["status"] = "NO-DATA"
        world_results.append(world_result)

        for key in ("supported", "PASS", "FAIL", "NO-DATA", "unsupported"):
            overall[key] += world_result[key]
        for key in _EXCLUDED_KEYS:
            overall["excluded"][key] += world_result["excluded"][key]
        if world_result["cost_n"] > 0:
            if overall["cost"] is None:
                overall["cost"] = 0.0
            overall["cost"] += world_result["cost"]
            overall["cost_n"] += world_result["cost_n"]

    _set_ratio(overall)
    if overall["supported"] + overall["unsupported"] > 0:
        overall["status"] = "OK"
    else:
        overall["status"] = "NO-DATA"

    return {"worlds": world_results, "overall": overall}


def _pass_rate(counts):
    supported = counts.get("supported", 0)
    if supported <= 0:
        return 0.0
    return float(counts.get("PASS", 0)) / supported


def _verdict(winner, reason, incumbent_replay, candidate_replay, world_count):
    if winner is None:
        verdict = "NO-DATA"
    elif winner == "candidate":
        verdict = "CANDIDATE"
    else:
        verdict = "INCUMBENT"

    return {
        "verdict": verdict,
        "winner": winner,
        "reason": reason,
        "incumbent": incumbent_replay["overall"] if incumbent_replay else None,
        "candidate": candidate_replay["overall"] if candidate_replay else None,
        "worlds": world_count,
    }


def compare(worlds, incumbent, candidate, min_worlds, min_supported):
    """Compare an incumbent policy with a candidate policy on fixed worlds.

    The rule is lexicographic: first, no regression in PASS rate on supported
    decisions; second, fewer FAIL decisions; third, lower measured cost.
    Incumbent wins ties. The verdict is NO-DATA when there are too few worlds,
    too few supported decisions, or either policy's support ratio is below 0.5.
    """
    if not isinstance(min_worlds, int) or min_worlds < 0:
        return _verdict(
            None, "min_worlds must be a non-negative integer", None, None, 0
        )
    if not isinstance(min_supported, int) or min_supported < 0:
        return _verdict(
            None, "min_supported must be a non-negative integer", None, None, 0
        )
    if not callable(incumbent) or not callable(candidate):
        return _verdict(
            None, "incumbent and candidate must be callable", None, None, 0
        )

    try:
        if worlds is None:
            world_list = None
        else:
            world_list = list(worlds)
    except TypeError:
        return _verdict(
            None, "worlds must be an iterable of worlds or None", None, None, 0
        )

    incumbent_replay = replay(world_list, incumbent)
    candidate_replay = replay(world_list, candidate)

    world_count = len(incumbent_replay["worlds"])
    overall_incumbent = incumbent_replay["overall"]
    overall_candidate = candidate_replay["overall"]

    if world_count < min_worlds:
        return _verdict(
            None,
            "worlds %d < min_worlds %d" % (world_count, min_worlds),
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    if (
        overall_incumbent["supported"] < min_supported
        or overall_candidate["supported"] < min_supported
    ):
        return _verdict(
            None,
            "supported below min_supported",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    if (
        overall_incumbent["support_ratio"] < 0.5
        or overall_candidate["support_ratio"] < 0.5
    ):
        return _verdict(
            None,
            "support ratio below 0.5",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    incumbent_rate = _pass_rate(overall_incumbent)
    candidate_rate = _pass_rate(overall_candidate)

    if candidate_rate < incumbent_rate:
        return _verdict(
            "incumbent",
            "candidate PASS rate regressed",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    if candidate_rate > incumbent_rate:
        return _verdict(
            "candidate",
            "candidate PASS rate improved",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    if overall_candidate["FAIL"] > overall_incumbent["FAIL"]:
        return _verdict(
            "incumbent",
            "candidate has more FAIL",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    if overall_candidate["FAIL"] < overall_incumbent["FAIL"]:
        return _verdict(
            "candidate",
            "candidate has fewer FAIL",
            incumbent_replay,
            candidate_replay,
            world_count,
        )

    incumbent_cost = overall_incumbent.get("cost")
    candidate_cost = overall_candidate.get("cost")
    if incumbent_cost is not None and candidate_cost is not None:
        if candidate_cost < incumbent_cost:
            return _verdict(
                "candidate",
                "candidate has lower measured cost",
                incumbent_replay,
                candidate_replay,
                world_count,
            )
        if candidate_cost > incumbent_cost:
            return _verdict(
                "incumbent",
                "candidate has higher measured cost",
                incumbent_replay,
                candidate_replay,
                world_count,
            )

    return _verdict(
        "incumbent",
        "incumbent wins tie",
        incumbent_replay,
        candidate_replay,
        world_count,
    )


__all__ = ["replay", "compare"]

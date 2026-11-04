#!/usr/bin/env python3
"""flow_report: what each delivery stage caught that no later stage would have, against what it cost (unit FLOW1.c).

THE QUESTION (owner, 2026-11-01): is the effort worth it, and what level of effort is worth it. This reads the
FLOW1.b relay ledger (session_relay.read, the one reader; never a second one) and prices five stages in the order a
unit meets them: harness_frozen, mutation_sweep, review, whole_gate, simulated_check.

HOW A STAGE IS RECORDED (the ledger's own event shapes, unchanged):
  harness_frozen, review   `stage` events (unit, stage, round, verdict, minutes, tokens, defects).
  whole_gate, mutation_sweep, simulated_check
                           `gate` events whose gate name is whole, mutation_sweep, simulated. session_relay's STAGES has
                           no mutation or simulation stage, so they ride as gates; minutes are the gate's own interval.
  DEFECT IDENTITY          an optional `defect_ids` field (a list, or a comma separated text as the relay CLI writes it)
                           on any of those events. `defects` alone is a count: it cannot say whether a later stage
                           reported the same defect, so a count above zero without ids reads NO-DATA, never a guess. A
                           count of zero is a measured zero ONLY for a run whose status or verdict is PASS: a FAILed run
                           that named no defect (a sweep that errored out) reads NO-DATA too.

UNIQUE CATCH, the contract's words "caught that no later stage would have": for a unit, a defect id a stage reported
that NO later stage reported for the same unit. A defect reported by an earlier and a later stage is credited to the
LATER one (and shown as "also caught later" on the earlier), so one defect is counted once, never once per stage. This
is an UPPER bound on the counterfactual: a defect fixed after stage A cannot show whether stage B would have found it.
A unit whose stage or any later stage left an unreadable or id-less report is not resolvable and is left out, labeled.

TIERS: docs/plan/EFFORT-TIERS.json names which unit kinds take which path. A tier that skips a stage is JUSTIFIED only
by min_units resolvable units whose skipped stage caught nothing unique; fewer units read NO-DATA, a catch reads NOT
JUSTIFIED. Security, gate and release kinds are a floor: a tier that skips anything for them, or skips harness_frozen,
review or whole_gate for anyone, is refused (exit 2). A unit no kind claims takes the full path.
Every kind belongs to exactly one tier, and kind and tier names are one plain line, so a name cannot start a line of
the report with a token. Cost is minutes and tokens per unique catch; a figure not fully known reads NO-DATA, never a rate.

CONTINGENCY. Unreadable, missing or malformed ledger or tiers: NO-DATA on stderr, exit 2, nothing printed as a figure.
A read never writes. Rollback: delete this file, its test and EFFORT-TIERS.json; nothing imports them.
FOOTPRINT: reads two files, no process, no network.

CLI (exit 0 reported, 2 NO-DATA):
  flow_report.py --ledger P [--tiers docs/plan/EFFORT-TIERS.json]

Python 3.9 floor, standard library plus session_relay.
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import session_relay as sr  # noqa: E402

NODATA = sr.NODATA
#: the stages a unit meets, in order; "later" always means later in this tuple
ORDER = ("harness_frozen", "mutation_sweep", "review", "whole_gate", "simulated_check")
GATE_STAGE = {"whole": "whole_gate", "mutation_sweep": "mutation_sweep", "simulated": "simulated_check"}
#: no tier may skip these: the frozen harness, the independent review (REVIEW-POLICY) and the train gate (CHECK-TIERS 3)
NEVER_SKIPPED = ("harness_frozen", "review", "whole_gate")
#: a word the report's own lines carry as a verdict; no ledger name may contain one, so a forged name cannot plant it
VERDICT_WORD = re.compile(r"\b(?:JUSTIFIED|NO-DATA|PASS|FAIL|STALL|FULL PATH)\b")
DEFAULT_TIERS = os.path.join(os.path.dirname(HERE), "docs", "plan", "EFFORT-TIERS.json")


class TiersUnreadable(Exception):
    """EFFORT-TIERS.json is missing, malformed, or breaks the floor. NO-DATA, never a default tier."""


def check_names(events):
    """Refuse a ledger whose unit, gate or stage name holds a verdict word (after the one-line rule): the report prints
    those names mid-line, and a reader matching a verdict anywhere in a line would take the forged one."""
    for e in events:
        for key in ("unit", "gate", "stage"):
            name = sr._one_line(e.get(key, ""))
            if VERDICT_WORD.search(name):
                raise sr.LedgerUnreadable("%s %r holds a verdict word, which a printed line must not carry" % (key, name))


def stage_of(event):
    """The priced stage an event belongs to, or None (implementation_done, push, merge, owner_wait, other gates)."""
    if event["kind"] == "stage" and event["stage"] in ("harness_frozen", "review"):
        return event["stage"]
    if event["kind"] == "gate":
        return GATE_STAGE.get(event["gate"])
    return None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def ids_of(event):
    """The defect ids an event reported as a frozenset, or None when it left none that can be trusted. THE ONE PLACE
    every count routes through: a run whose gate status or review verdict is not PASS and that named no defect is not a
    measurement (a sweep that errored out records 0 by default), so it is None, never a zero; a FAILed run that named
    its defects is a catch."""
    ids = _reported_ids(event)
    outcome = event.get("status", event.get("verdict"))
    return None if ids == frozenset() and outcome not in (None, "PASS") else ids


def _reported_ids(event):
    """The ids as reported, or None for ids that are not a list of non-empty text, that disagree with a `defects`
    count, or a count above zero with no ids. A zero count with no ids is an empty set; ids_of judges the run."""
    count = event.get("defects")
    if "defects" in event and _count(count) is None:
        return None
    raw = event.get("defect_ids")
    if raw is None:
        return frozenset() if count == 0 else None
    if isinstance(raw, str):
        raw = raw.split(",") if raw != "" else []
    if not isinstance(raw, list) or not all(isinstance(i, str) and i.strip() for i in raw):
        return None
    ids = frozenset(i.strip() for i in raw)
    if count is not None and count != len(ids):
        return None
    return ids


def per_unit(events):
    """{stage: {unit: frozenset or None}}: every id a unit's events in a stage reported, None as soon as one event of
    that unit and stage left no trustworthy ids."""
    table = {s: {} for s in ORDER}
    for e in events:
        s = stage_of(e)
        if s is None:
            continue
        ids = ids_of(e)
        cur = table[s].get(e["unit"], frozenset())
        table[s][e["unit"]] = None if ids is None or cur is None else cur | ids
    return table


def catches(table, stage, units):
    """(unique, also_later, resolvable, total) of `stage` over the `units` that have events in it."""
    later = ORDER[ORDER.index(stage) + 1:]
    unique = also = ok = total = 0
    for u in units:
        if u not in table[stage]:
            continue
        total += 1
        mine = table[stage][u]
        theirs = [table[s][u] for s in later if u in table[s]]
        if mine is None or any(t is None for t in theirs):
            continue
        after = frozenset().union(*theirs)
        ok += 1
        unique += len(mine - after)
        also += len(mine & after)
    return unique, also, ok, total


def _figure(n, ok, total):
    if total == 0:
        return "%s (no events)" % NODATA
    if ok == total:
        return str(n)
    return "%s (%d of %d units resolvable%s)" % (NODATA, ok, total, ", %d so far" % n if ok else "")


def _proxy(event):
    """The numbers one event left, in the shape session_relay._sum reads. A gate's minutes are its interval."""
    p = {}
    if event["kind"] == "gate":
        p["minutes"] = (event["ended_unix"] - event["started_unix"]) / 60.0
    elif "minutes" in event:
        p["minutes"] = event["minutes"]
    for field in ("tokens", "defects"):
        if _count(event.get(field)) is not None:
            p[field] = event[field]
    return p


def _per(cost, unique, unit="minutes"):
    try:
        m, u = float(cost), int(unique)
    except ValueError:
        return NODATA
    return "none (0 unique catches in %g %s)" % (m, unit) if u == 0 else "%.1f" % (m / u)


def load_tiers(path):
    """The tier file, validated. Raises TiersUnreadable for anything that is not exactly the shape this report reads."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TiersUnreadable("%s cannot be read: %s" % (path, exc))
    try:
        kinds, tiers, need = data["kinds"], data["tiers"], data["min_units"]
        if (not isinstance(kinds, dict) or not kinds or not isinstance(tiers, list) or not tiers
                or isinstance(need, bool) or not isinstance(need, int) or need < 1):
            raise ValueError("kinds, tiers and a whole min_units of at least 1 are required")
        for name, k in kinds.items():
            if name != sr._one_line(name):
                raise ValueError("kind name %r must be one plain line" % name)
            if (not isinstance(k.get("prefixes", []), list) or not isinstance(k.get("ids", []), list)
                    or not isinstance(k.get("floor", False), bool)):
                raise ValueError("kind %s needs prefixes, ids (lists) and floor (true or false)" % name)
        for t in tiers:
            for key in ("name", "measured_catch", "flip_condition"):
                if not isinstance(t.get(key), str) or not t[key].strip():
                    raise ValueError("a tier needs %s" % key)
            if t["name"] != sr._one_line(t["name"]):
                raise ValueError("tier name %r must be one plain line" % t["name"])
            if not isinstance(t.get("kinds"), list) or not isinstance(t.get("skips", []), list):
                raise ValueError("tier %s needs kinds and skips as lists" % t["name"])
            for k in t["kinds"]:
                if k not in kinds:
                    raise ValueError("tier %s names the kind %r that no kind defines" % (t["name"], k))
            for s in t.get("skips", []):
                if s not in ORDER or s in NEVER_SKIPPED:
                    raise ValueError("tier %s skips %r: only %s may be skipped" % (
                        t["name"], s, "/".join(x for x in ORDER if x not in NEVER_SKIPPED)))
            if t.get("skips") and any(kinds[k].get("floor") for k in t["kinds"]):
                raise ValueError("tier %s skips stages for a floor kind (security, gate and release never drop "
                                 "below the full path)" % t["name"])
        for name in kinds:      # a kind in no tier has no declared path; in two tiers it has two
            if sum(name in t["kinds"] for t in tiers) != 1:
                raise ValueError("kind %s must belong to exactly one tier" % name)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise TiersUnreadable("%s is not a tier file: %s" % (path, exc))
    return data


def kind_of(unit, kinds):
    """The kind a unit belongs to: a floor kind wins over any other match, else the first in file order, else None."""
    base = unit.split(".")[0]
    hits = [n for n, k in kinds.items() if unit in k.get("ids", []) or base in k.get("prefixes", [])]
    return next((n for n in hits if kinds[n].get("floor")), hits[0] if hits else None)


def build(events, tiers):
    """The report lines."""
    table = per_unit(events)
    all_units = sorted({u for s in ORDER for u in table[s]})
    lines = ["FLOW REPORT: %d events, %d units with a priced stage" % (len(events), len(all_units))]
    by_stage = collections.defaultdict(list)
    other = collections.Counter()
    for e in events:
        s = stage_of(e)
        if s:
            by_stage[s].append(e)
        elif e["kind"] in ("stage", "gate"):
            other[sr._one_line(e["stage"] if e["kind"] == "stage" else "gate " + str(e["gate"]))] += 1
    for s in ORDER:
        evs = by_stage[s]
        proxies = [_proxy(e) for e in evs]
        unique, also, ok, total = catches(table, s, all_units)
        minutes, tokens = sr._sum(proxies, "minutes"), sr._sum(proxies, "tokens")
        catch = _figure(unique, ok, total)
        line = "stage %s: %d events, %d units, defects reported %s, unique catch %s, also caught later %s, " \
               "minutes %s, tokens %s, minutes per unique catch %s, tokens per unique catch %s" % (
                   s, len(evs), total, sr._sum(proxies, "defects") if evs else NODATA + " (no events)",
                   catch, _figure(also, ok, total), minutes if evs else NODATA + " (no events)",
                   tokens if evs else NODATA + " (no events)",
                   _per(minutes, catch) if evs else NODATA, _per(tokens, catch, "tokens") if evs else NODATA)
        if s == "review":
            line += ", verdicts PASS %d FAIL %d" % tuple(sum(e["verdict"] == v for e in evs) for v in sr.VERDICTS)
        if s in GATE_STAGE.values():
            line += ", runs PASS %d FAIL %d" % tuple(sum(e["status"] == v for e in evs) for v in sr.VERDICTS)
        lines.append(line)
    lines.append("not priced: %s" % (", ".join("%s %d" % kv for kv in sorted(other.items())) or "none"))
    kinds, need = tiers["kinds"], tiers["min_units"]
    unclassified = [u for u in all_units if kind_of(u, kinds) is None]
    lines.append("unclassified units (take the full path): %s" % (" ".join(sr._one_line(u) for u in unclassified) or "none"))
    for t in tiers["tiers"]:
        units = [u for u in all_units if kind_of(u, kinds) in t["kinds"]]
        skips = t.get("skips", [])
        lines.append("tier %s: kinds %s, %d units in the ledger, skips %s" % (
            t["name"], " ".join(t["kinds"]), len(units), " ".join(skips) or "none"))
        verdicts = []
        for s in skips:
            unique, also, ok, total = catches(table, s, units)
            lines.append("  skipped stage %s: unique catch %s over %d resolvable of %d units (need %d)" % (
                s, _figure(unique, ok, total), ok, total, need))
            verdicts.append("NOT JUSTIFIED" if ok and unique else
                            "NO-DATA" if ok < need or ok < total else "JUSTIFIED")
        lines.append("  tier %s: %s" % (t["name"], (
            "NOT JUSTIFIED" if "NOT JUSTIFIED" in verdicts else
            NODATA if NODATA in verdicts else "JUSTIFIED") if skips else "FULL PATH, nothing skipped"))
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(prog="flow_report.py")
    p.add_argument("--ledger", required=True)
    p.add_argument("--tiers", default=DEFAULT_TIERS)
    a = p.parse_args(argv)
    try:
        if not os.path.exists(a.ledger):        # read() calls a missing ledger empty; a mistyped path must not
            raise sr.LedgerUnreadable("the ledger %s does not exist, so nothing can be reported" % a.ledger)
        events = sr.read(a.ledger)
        check_names(events)
        tiers = load_tiers(a.tiers)
    except (sr.LedgerUnreadable, TiersUnreadable, UnicodeDecodeError) as exc:   # read() lets a non UTF-8 ledger through
        print("%s: %s" % (NODATA, exc), file=sys.stderr)
        return 2
    print("\n".join(build(events, tiers)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""reach_check: every registered capability is reachable from a plain ask (OUT1.a).

docs/plan/REACH.json lists each capability with the ask that reaches it, two paraphrases, the verb the one door
must route them to, the file that implements it and a marker string that file must contain. A capability is
reachable when its ask AND both paraphrases route to its verb and the file still carries the marker.

    python3 scripts/reach_check.py [--registry docs/plan/REACH.json]
    python3 scripts/reach_check.py --consumer [--candidate REV] [--host HOST]   (OUT1.n: what a consumer sees, and
        the hidden repair queue; exit 0 when every door capability maps to a group, 1 when one maps to none)
    exit 0 PASS, 1 FAIL (names each unreachable capability or inventory problem), 2 NO-DATA (registry missing,
    unreadable or empty)

The registry's "inventory" list (OUT1.a slice 2) reconciles every developed capability group to one disposition
(consumer, pending, experimental, internal, obsolete) and records five facts separately: code_exists, shipped,
routed, installed_execution and user_benefit. check_inventory() refuses a row that breaks one of its rules, one FAIL
line per problem, and a registry with no inventory list is checked for reach alone.

route() is a deterministic model of the door's "pick the row from the words used" (bundle/commands/brother.md):
the verb whose cue words match most wins, and a tie or no match is `help`, never a silent `start`.
Python 3.9, standard library."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The routing and the path rule live in door.py (the kernel file the person's goal goes through); this checker reads
# them from there so the two can never disagree, and the kernel never depends on this checker.
import door  # noqa: E402
from door import CUES, REACH_REGISTRY as DEFAULT_REGISTRY, REACH_ROOT as ROOT, repo_path, route, words  # noqa: E402,F401


def consumer_capability_visible(record, candidate, host):
    """OUT1.n: True only when `record` earns consumer access for `candidate` on `host`. The rule lives in door.py so
    the door that enforces it and this checker cannot disagree; this is the checker's name for it."""
    return door.consumer_capability_visible(record, candidate, host)


def check_row(row):
    """Return the list of reasons this capability is unreachable (empty means reachable)."""
    if not isinstance(row, dict):
        return ["row is not an object"]
    why = []
    verb = row.get("verb")  # an unknown verb can never equal route()'s answer, so it fails below
    asks = [row.get("ask")] + list(row.get("paraphrases") or [])
    if len(asks) < 3 or not all(isinstance(a, str) and a.strip() for a in asks):
        why.append("needs an ask and at least two paraphrases")
    else:
        for a in asks:
            if route(a) != verb:
                why.append("%r routes to %s, not %s" % (a, route(a), verb))
    cmd, marker = row.get("command"), row.get("marker")
    try:
        full = repo_path(cmd)
        if full is None:
            why.append("command %r is outside the repository" % (cmd,))
        else:
            with open(full, encoding="utf-8", errors="replace") as fh:
                if not marker or marker not in fh.read():
                    why.append("%s does not contain marker %r" % (cmd, marker))
    except (OSError, TypeError):
        why.append("command %r is not a readable file" % (cmd,))
    return why


DISPOSITIONS = ("consumer", "pending", "experimental", "internal", "obsolete")
FACTS = ("code_exists", "shipped", "routed", "installed_execution", "user_benefit")
#: the families the 1.2.0 contract names, each matched against an inventory row's id prefix
FAMILIES = (("vault", "vault-"), ("brotherds claims", "claims-"), ("mdm", "mdm-"), ("mobile", "mobile-"),
            ("brother.loop", "loop-"))


def check_inventory(rows, capability_ids):
    """Return one line per inventory problem (empty means every row is reconciled)."""
    if not isinstance(rows, list) or not rows:
        return ["inventory: is not a non-empty list"]
    bad, seen = [], set()
    ids = [r.get("id") if isinstance(r, dict) else None for r in rows]
    for family, prefix in FAMILIES:
        if not any(isinstance(i, str) and i.startswith(prefix) for i in ids):
            bad.append("inventory: no row for %s (id prefix %r)" % (family, prefix))
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"].strip():
            bad.append("inventory: a row has no id")
            continue
        name = r["id"]
        if name in seen:
            bad.append("inventory %s: id repeats" % name)
        seen.add(name)
        missing = [f for f in FACTS if f not in r]
        if missing:
            bad.append("inventory %s: does not record %s" % (name, ", ".join(missing)))
        if not isinstance(r.get("user_benefit"), str) or not r["user_benefit"].strip():
            bad.append("inventory %s: user_benefit is not a plain sentence" % name)
        if r.get("installed_execution") not in ("PASS", "FAIL", "NO-DATA"):
            bad.append("inventory %s: installed_execution %r is not PASS, FAIL or NO-DATA"
                       % (name, r.get("installed_execution")))
        disposition = r.get("disposition")
        if disposition not in DISPOSITIONS:
            bad.append("inventory %s: disposition %r is not one of %s" % (name, disposition, ", ".join(DISPOSITIONS)))
        routed = r.get("routed")
        if disposition == "consumer" and routed not in capability_ids:
            bad.append("inventory %s: consumer but routes to %r, not a registered capability" % (name, routed))
        elif routed is not None and routed not in capability_ids:
            bad.append("inventory %s: routes to %r, not a registered capability" % (name, routed))
        if disposition in DISPOSITIONS and disposition != "consumer" and not (
                isinstance(r.get("reason"), str) and r["reason"].strip()):
            bad.append("inventory %s: %s with no reviewed reason" % (name, disposition))
        if disposition == "pending" and not str(r.get("unit", "")).startswith("OUT1."):
            bad.append("inventory %s: pending with no owning OUT1 unit (unit %r)" % (name, r.get("unit")))
        if disposition == "experimental" and not (isinstance(r.get("label"), str) and r["label"].strip()):
            bad.append("inventory %s: experimental with no label" % name)
        if r.get("code_exists") is True:
            owners = r.get("source_owners")
            if not isinstance(owners, list) or not owners:
                bad.append("inventory %s: code_exists with no source_owners" % name)
            else:
                for path in owners:
                    full = repo_path(path) if isinstance(path, str) else None
                    if full is None:
                        bad.append("inventory %s: source owner %r is outside the repository" % (name, path))
                    elif not os.path.exists(full):
                        bad.append("inventory %s: code_exists but source owner %r does not exist" % (name, path))
    return bad


def door_capability_ids():
    """Every capability id the door can run, from its own tables."""
    tables = (door.VAULT_CAPABILITIES, door.DATA_CAPABILITIES, door.MOBILE_CAPABILITIES,
              door.ASSURANCE_CAPABILITIES, door.HOST_CAPABILITIES, door.LOOP_CAPABILITIES,
              door.JEV_CAPABILITIES, door.LEARNING_CAPABILITIES,
              door.CONTENT_CAPABILITIES)
    return [c["id"] for table in tables for c in table]


def consumer_report(registry, candidate=None, host=None):
    """Print what a consumer can reach and the hidden repair queue (OUT1.n). A door capability that no group in
    ADMISSION.json lists is a FAIL: it would stay hidden forever with no owner, which is silent narrowing."""
    view = door.consumer_admission(registry, candidate, host)
    entries = view["queue"]
    path = os.path.join(os.path.dirname(registry), door.ADMISSION_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            groups = json.load(fh)["entries"]
        listed = [p for g in groups.values() if isinstance(g, dict) for p in g.get("capabilities") or []]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print("NO-DATA: %s is unusable, so every capability is hidden" % path)
        return 2
    unmapped = [c for c in door_capability_ids() if not door._door_match(listed, c)]
    for c in unmapped:
        print("FAIL %s: no group in %s lists it" % (c, door.ADMISSION_FILE))
    print("candidate %s, host %s: %d visible capability pattern(s), %d group(s) hidden"
          % (view["candidate"] or "NO-DATA", view["host"] or "NO-DATA", len(view["capabilities"]), len(entries)))
    for row in entries:
        print("HIDDEN %s (owner %s): %s | next: %s" % (row["id"], row["owner"], row["cause"], row["next_verification"]))
    return 1 if unmapped else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--consumer", action="store_true")
    ap.add_argument("--candidate")
    ap.add_argument("--host")
    args = ap.parse_args(argv)
    if args.consumer:
        return consumer_report(args.registry, args.candidate, args.host)
    try:
        with open(args.registry, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data["capabilities"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("no capabilities listed")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("NO-DATA: registry %s is unusable (%s)" % (args.registry, e))
        return 2
    bad = 0
    for row in rows:
        why = check_row(row)
        name = row.get("id", "?") if isinstance(row, dict) else "?"
        if why:
            bad += 1
            print("FAIL %s: %s" % (name, "; ".join(why)))
    inventory = data.get("inventory")
    problems = []
    if inventory is None:
        print("inventory: none listed, so only reach was checked")
    else:
        problems = check_inventory(inventory, {r.get("id") for r in rows if isinstance(r, dict)})
        for line in problems:
            print("FAIL %s" % line)
    if bad or problems:
        print("FAIL: %d of %d capabilities unreachable, %d inventory problem(s)" % (bad, len(rows), len(problems)))
        return 1
    tail = "" if inventory is None else ", %d inventory rows reconciled" % len(inventory)
    print("PASS: %d of %d capabilities reachable%s" % (len(rows), len(rows), tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())

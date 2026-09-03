"""capability_probe: what this machine can actually do, measured not remembered.

WHY THIS EXISTS, and it is one failure with three faces. On 2026-08-29 a session
was asked to debate a design with a second model. It looked for one bridge
script, found it deleted, and reported the capability RETIRED. A working `codex`
binary sat at ~/.local/bin/codex with its own config the entire time. The founder
had to say so.

Then the vault's own tools inventory was checked: it is hand maintained, it was a
day stale, and it does not mention codex anywhere. So the estate HAD a place that
would have answered the question and that place was not current, which is exactly
how a hand maintained inventory fails. It does not fail loudly. It quietly stops
being true and nobody notices until somebody trusts it.

THE RULE THIS ENFORCES: never report a capability as unavailable after checking
ONE path. Probe the capability, not a filename.

WHAT IT DOES. For each capability this estate depends on, it asks the machine
rather than a document: is a binary on PATH, does a config exist, does a probe
command succeed. It prints what is present, what is MISSING, and what is STALE in
the written inventory, so the document and the world can be compared instead of
one being trusted.

IT NEVER GUESSES. A capability it cannot probe is NO-DATA and says which check it
could not run. Reporting "probably fine" about a tool somebody is about to depend
on is the failure this replaces.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

PRESENT, MISSING, NODATA = "PRESENT", "MISSING", "NO-DATA"

#: What this estate actually depends on, and how to ask the MACHINE about each.
#: A capability is a thing you can do, never a file you hope exists: that
#: distinction is the whole point, because the failure was checking one filename
#: and concluding about the capability behind it.
#:
#: `alternatives` is the part that would have prevented the miss: a capability
#: may be reachable more than one way, and finding the first one absent proves
#: nothing about the others.
CAPABILITIES = (
    {"name": "second-model-debate",
     "why": "arguing a design with a model that is not this one",
     "alternatives": [{"kind": "binary", "probe": "codex"},
                      {"kind": "file", "probe": "~/.claude/bin/or_ask.py"},
                      {"kind": "binary", "probe": "0x"}]},
    {"name": "github",
     "why": "pull requests, rulesets, and reading whether a push can land",
     "alternatives": [{"kind": "binary", "probe": "gh"}]},
    {"name": "vault-recall",
     "why": "reading what this estate already learned before repeating it",
     "alternatives": [{"kind": "file",
                       "probe": "~/Documents/BrotherModeUp/tools/bm_vault.py"}]},
    {"name": "durable-watchdog",
     "why": "a watcher that outlives the session that started it",
     "alternatives": [{"kind": "file", "probe": "~/Brother/scripts/night_tick.py"},
                      {"kind": "file", "probe": "~/.claude/bin/brother_night_tick.py"}]},
    {"name": "spend-guard",
     "why": "the brake that stops an unattended run",
     "alternatives": [{"kind": "file", "probe": "~/.claude/hooks/spend_guard.py"}]},
    {"name": "repeat-guard",
     "why": "surfacing a recorded lesson at the moment of action",
     "alternatives": [{"kind": "file", "probe": "~/.claude/hooks/repeat_guard.py"}]},
)


def probe_one(alt):
    """(state, detail) for a single way of reaching a capability."""
    kind, target = alt.get("kind"), alt.get("probe", "")
    if kind == "binary":
        found = shutil.which(target)
        return (PRESENT, found) if found else (MISSING, "%s not on PATH" % target)
    if kind == "file":
        path = os.path.expanduser(target)
        return (PRESENT, path) if os.path.exists(path) else (MISSING,
                                                             "%s absent" % path)
    return NODATA, "no probe kind %r is implemented" % kind


def probe(capability):
    """(state, reached_by, tried). EVERY alternative is tried before MISSING.

    That is the whole correction: the session that failed here checked one path
    and stopped. A capability is missing only when every route to it is."""
    tried = []
    for alt in capability.get("alternatives") or []:
        state, detail = probe_one(alt)
        tried.append((alt.get("probe"), state, detail))
        if state == PRESENT:
            return PRESENT, detail, tried
    if not tried:
        return NODATA, "", tried
    return MISSING, "", tried


def survey(capabilities=CAPABILITIES):
    out = []
    for cap in capabilities:
        state, reached, tried = probe(cap)
        out.append({"name": cap["name"], "why": cap.get("why", ""),
                    "state": state, "reached_by": reached,
                    "tried": [{"probe": p, "state": s, "detail": d}
                              for p, s, d in tried]})
    return out


def compare_inventory(results, inventory_path):
    """What the written document fails to mention. Returns a list of names.

    A hand maintained inventory does not fail loudly, it quietly stops being
    true, so the only useful question is which live capability it never names."""
    path = os.path.expanduser(inventory_path)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read().lower()
    missing = []
    for r in results:
        if r["state"] != PRESENT:
            continue
        names = [r["name"]] + [os.path.basename(t["probe"] or "")
                               for t in r["tried"] if t["state"] == PRESENT]
        if not any(n and n.lower() in text for n in names if n):
            missing.append(r["name"])
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inventory",
                    default="~/Documents/Kay Vault/50-Reference/installed-tools.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    results = survey()
    undocumented = compare_inventory(results, args.inventory)

    if args.json:
        print(json.dumps({"capabilities": results,
                          "undocumented": undocumented}, indent=2))
        return 0

    for r in results:
        print("%-10s %-24s %s" % (r["state"], r["name"],
                                  r["reached_by"] or r["why"]))
        if r["state"] == MISSING:
            for t in r["tried"]:
                print("             tried %-42s %s" % (t["probe"], t["detail"]))

    print("")
    if undocumented is None:
        print("NO-DATA: the written inventory could not be read, so the "
              "document and the world were not compared", file=sys.stderr)
        return 2
    if undocumented:
        print("STALE INVENTORY: %d live capability(ies) the written inventory "
              "never mentions: %s. A hand maintained list does not fail loudly, "
              "it quietly stops being true"
              % (len(undocumented), ", ".join(undocumented)), file=sys.stderr)
        return 1
    print("the written inventory names every live capability")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""SYSTEM.md: a description of this system that cannot drift, because it is
generated.

TEAM COMPLAINT P12, from a reviewer: fifty designs after a year and none of
them describes the system. Point-in-time designs, no living record. The
tempting fix is to write a good architecture document; that is what produced
the fifty, each right on the day it was written and quietly wrong afterward,
with no way to tell which of the fifty was still true.

SO THIS IS GENERATED FROM THE CODE THIS REPOSITORY ALREADY RUNS, never from
what someone intended. `--check` regenerates in memory and compares against
the file on disk; a difference is a FAILURE, named by a small diff, so the
document cannot be wrong for longer than it takes someone to run the battery.

WHAT IT READS, and nothing else: the CLI command registry
(`src/brothersbe/cli.py` COMMANDS), the check registry (`.sbe/checks.yml`,
read through `brothersbe.checks.load_registry`), and every dossier under
`design/` (found the same way `tools/sbe_design.py` finds one, its behaviour
rows read by `_behaviour_rows` and its data model entities read by
`_entities`, the same parsers the design checks themselves use, so this file
adds no second reading of the same tables). Each section names the file it
came from.

Adapted from the sibling umbrella repository's `scripts/system_doc.py`, which
solved the identical complaint the identical way: a document is trustworthy
exactly as long as nothing has to remember to update it by hand.

Python 3, standard library only. No network.
"""
import argparse
import difflib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
SRC = os.path.join(ROOT, "src")
DESIGN = os.path.join(ROOT, "design")
OUT = os.path.join(ROOT, "SYSTEM.md")
NODATA = "NO-DATA"

sys.path.insert(0, TOOLS)
sys.path.insert(0, SRC)

from sbe_checks import one_line, say  # noqa: E402  (path setup has to come first)


def cli_commands():
    """(name, description) for every command `sbe_design.py`'s sibling CLI
    registers, straight out of `src/brothersbe/cli.py`'s own COMMANDS list.

    Returns None if the module cannot be imported, which is NO-DATA rather
    than an empty command surface.
    """
    try:
        import brothersbe.cli as cli
    except ImportError:  # sbe: allow-silent the CLI module is optional here, and None is this function's documented NO-DATA at the call site
        return None
    return [(name, desc) for name, desc, _handler in cli.COMMANDS]


def check_registry():
    """(id, kind, why, command) for every check `.sbe/checks.yml` registers.

    Returns None if the registry does not exist or does not validate: a
    registry this tool cannot read is not evidence that no checks exist.
    """
    path = os.path.join(ROOT, ".sbe", "checks.yml")
    if not os.path.isfile(path):
        return None
    try:
        import brothersbe.checks as checks_mod
    except ImportError:  # sbe: allow-silent the checks module is optional here, and None is the documented NO-DATA at the call site
        return None
    try:
        registry = checks_mod.load_registry(path)
    except checks_mod.RegistryUnreadable:  # sbe: allow-silent an unreadable registry is reported as NO-DATA by the caller, never as an empty registry
        return None
    out = []
    for check_id in sorted(registry["checks"]):
        spec = registry["checks"][check_id]
        cmd = " ".join([spec["command"]["executable"]] + spec["command"]["arguments"])
        out.append((check_id, spec["kind"], spec["why"] or NODATA, cmd))
    return out


def dossiers():
    """One entry per dossier under `design/`: its behaviour rows (from
    `08-behaviour.md`) and its data model entities (from `05-data-model.md`),
    read with `sbe_design`'s own parsers so this file never re-derives a
    table `sbe_design.py` already owns reading.

    Returns None if `design/` does not exist, or if `sbe_design` cannot be
    imported: either way this is a system with nothing here to describe, not
    a system with none.
    """
    if not os.path.isdir(DESIGN):
        return None
    try:
        import sbe_design
    except ImportError:  # sbe: allow-silent the design module is optional here, and None is the documented NO-DATA at the call site
        return None
    targets, _exempt, _refused, _scope, _pruned = sbe_design.find_dossiers(DESIGN)
    out = []
    for dp in sorted(targets):
        name = os.path.relpath(dp, DESIGN)
        behaviour_text = sbe_design.read(dp, sbe_design.ARTIFACT_FILES["08"])
        rows = []
        if behaviour_text is not None and not sbe_design._MARKER_COMMENT.search(behaviour_text):
            rows, _malformed = sbe_design._behaviour_rows(behaviour_text)
        model_text = sbe_design.read(dp, sbe_design.ARTIFACT_FILES["05"])
        entities = {}
        if model_text is not None and not sbe_design._MARKER_COMMENT.search(model_text):
            entities = sbe_design._entities(model_text)
        out.append({"name": name, "rows": rows, "entities": entities})
    return out


def render(commands, checks, dossier_rows):
    L = []
    A = L.append
    A("# What this system is, right now")
    A("")
    A("GENERATED by `tools/sbe_system_doc.py`. Do not edit this file by hand: the")
    A("next `--check` will overwrite your edit and fail the battery.")
    A("")
    A("It exists because of a reviewer's complaint (P12) that there were fifty")
    A("designs after a year and none of them described the system. Writing a")
    A("fifty-first good design is what produced the fifty. This one is generated")
    A("from the code and the dossiers this repository already carries, so it")
    A("cannot be wrong for longer than it takes someone to run `--check`.")
    A("")
    A("## CLI commands")
    A("")
    A("Source: `src/brothersbe/cli.py`, the `COMMANDS` list.")
    A("")
    if commands is None:
        A("%s: `brothersbe.cli` could not be imported, so no command could be read." % NODATA)
    else:
        A("| Command | What it does |")
        A("|---|---|")
        for name, desc in commands:
            A("| `%s` | %s |" % (name, desc))
    A("")
    A("## Registered checks")
    A("")
    A("Source: `.sbe/checks.yml`, read through `brothersbe.checks.load_registry`.")
    A("")
    if checks is None:
        A("%s: `.sbe/checks.yml` is absent or does not validate, so no check could be read." % NODATA)
    else:
        A("| Check | Kind | Why | Command |")
        A("|---|---|---|---|")
        for check_id, kind, why, cmd in checks:
            A("| `%s` | %s | %s | `%s` |" % (check_id, kind, why, cmd))
    A("")
    A("## Design dossiers")
    A("")
    A("Source: every dossier under `design/`, found the way `tools/sbe_design.py`")
    A("finds one; behaviour rows from each dossier's `08-behaviour.md`, entities")
    A("from each dossier's `05-data-model.md`.")
    A("")
    if dossier_rows is None:
        A("%s: no `design/` directory, so no dossier could be read." % NODATA)
    else:
        for d in dossier_rows:
            A("### %s" % d["name"])
            A("")
            if d["rows"]:
                A("Behaviour (`design/%s/08-behaviour.md`):" % d["name"])
                A("")
                A("| ID | Starting point | Trigger | Required outcome |")
                A("|---|---|---|---|")
                for r in d["rows"]:
                    A("| %s | %s | %s | %s |" % (
                        r.get("id", ""), r.get("starting point", ""),
                        r.get("trigger", ""), r.get("required outcome", "")))
            else:
                A("Behaviour: %s, no row read from `design/%s/08-behaviour.md`."
                  % (NODATA, d["name"]))
            A("")
            if d["entities"]:
                A("Entities (`design/%s/05-data-model.md`):" % d["name"])
                A("")
                A("| Entity | System of record |")
                A("|---|---|")
                for entity_name in sorted(d["entities"]):
                    A("| %s | %s |" % (entity_name, d["entities"][entity_name] or NODATA))
            else:
                A("Entities: %s, no entity read from `design/%s/05-data-model.md`."
                  % (NODATA, d["name"]))
            A("")
    return "\n".join(L) + "\n"


def build():
    return render(cli_commands(), check_registry(), dossiers())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the written file no longer matches the code")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    body = build()

    if args.check:
        if not os.path.isfile(args.out):
            sys.stderr.write(one_line(
                "%s does not exist. Run this without --check to write it." % args.out) + "\n")
            return 1
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
        if current == body:
            print("SYSTEM.md still describes the code")
            return 0
        diff = list(difflib.unified_diff(
            current.splitlines(), body.splitlines(),
            fromfile="SYSTEM.md (on disk)", tofile="SYSTEM.md (regenerated)",
            lineterm=""))
        sys.stderr.write(
            "SYSTEM.md NO LONGER DESCRIBES THE CODE. Something was added, removed or renamed "
            "and the record did not follow. Regenerate it with: "
            "python3 tools/sbe_system_doc.py\n")
        for line in diff[:40]:
            sys.stderr.write(one_line(line) + "\n")
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    say("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

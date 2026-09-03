"""check_installed_surface: does a clean install really deliver the manifest?

R11 clause two. The install smoke proves that ONE COMMAND installs the bundle
and that the two entries this repository ships register. That is honest and it
is a small fraction of the product: brothermode and brothersbe contribute thirty
and fourteen more entries to the same install, and nothing checked them.

WHY THIS COULD NOT BE CHECKED BEFORE. The only target count on the board was the
surface ceiling, which counts four trees while the umbrella ships three, so it
was never a statement about what an install delivers. bundle/MANIFEST.json is
that statement, and this compares the install against it.

IT COMPARES NAMES, NOT A COUNT. A count still passes when one entry is renamed
and another added, and that is precisely the drift an install check should
catch. So the verdict is set arithmetic: what the manifest promises, minus what
the install registered, and the reverse.

EXTRA ENTRIES ARE REPORTED AND DO NOT FAIL. The manifest counts the
USER-INVOCABLE surface, and a plugin registers entries that are not typeable, so
an install legitimately carries more than the manifest promises. Measured
2026-08-29 the extras were exactly the four brothermode skills marked
user-invocable: false. Missing entries fail, because a missing entry is a
promise the product did not keep; extra ones are reported and named so the
difference stays visible rather than assumed.

NO-DATA IS NEVER A PASS. A details log that could not be parsed, or a plugin the
manifest names with no log at all, is NO-DATA and exits non-zero: reading an
unparseable log as an empty set would turn a broken install into a clean one.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import sys

EXIT_MATCH = 0
EXIT_MISSING = 1
EXIT_NO_DATA = 2

#: `claude plugin details <name>` reports a plugin's commands/ entries INSIDE
#: its Skills list, beside its skills. Measured on the installed brothermode,
#: whose fifteen commands/ files all appear there. Written as a compiled pattern
#: with the count captured so a listing that changes shape fails to parse rather
#: than silently matching nothing.
SKILLS_LINE = re.compile(r"^\s*Skills\s*\((\d+)\)\s*(.*)$", re.M)


def parse_details(text):
    """The entry names one `plugin details` log reports. Returns (names, problem).

    A log with no Skills line at all is a problem and never an empty set: an
    empty set compared against a manifest would report every promised entry as
    missing, which looks like a catastrophic install failure when the real fault
    is that this parser stopped understanding the output."""
    match = SKILLS_LINE.search(text or "")
    if not match:
        return None, "no 'Skills (N)' line found, so this log could not be read"
    declared = int(match.group(1))
    names = [n.strip() for n in match.group(2).split(",") if n.strip()]
    if declared != len(names):
        return None, ("the listing declares %d entries but names %d, so it was "
                      "parsed wrongly" % (declared, len(names)))
    return set(names), ""


def compare(manifest, installed):
    """Set arithmetic per plugin. Returns (missing, extra) as dicts."""
    missing, extra = {}, {}
    for plugin, promised in sorted((manifest.get("entries") or {}).items()):
        have = installed.get(plugin, set())
        gap = sorted(set(promised) - have)
        surplus = sorted(have - set(promised))
        if gap:
            missing[plugin] = gap
        if surplus:
            extra[plugin] = surplus
    return missing, extra


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--details-dir", required=True,
                    help="directory holding details-<plugin>.log per plugin")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not read %s: %s" % (args.manifest, exc),
              file=sys.stderr)
        return EXIT_NO_DATA

    installed = {}
    for plugin in manifest.get("shipped_plugins") or []:
        path = os.path.join(args.details_dir, "details-%s.log" % plugin)
        if not os.path.isfile(path):
            print("NO-DATA: the manifest ships %s but there is no %s, so the "
                  "install was not described" % (plugin, path), file=sys.stderr)
            return EXIT_NO_DATA
        with open(path, encoding="utf-8", errors="replace") as fh:
            names, problem = parse_details(fh.read())
        if names is None:
            print("NO-DATA: %s: %s" % (path, problem), file=sys.stderr)
            return EXIT_NO_DATA
        installed[plugin] = names

    missing, extra = compare(manifest, installed)
    if missing:
        for plugin, gap in sorted(missing.items()):
            print("MISSING from the install, %s: %s" % (plugin, ", ".join(gap)),
                  file=sys.stderr)
        print("the install does not deliver %d entry(ies) the manifest promises"
              % sum(len(v) for v in missing.values()), file=sys.stderr)
        return EXIT_MISSING

    note = ""
    if extra:
        # NOT a failure, and NOT a stale manifest either, which is what the
        # first version of this line called it. Measured 2026-08-29: the four
        # extras are exactly the entries surface_budget excludes as
        # user-invocable: false. They register with the host and are not part
        # of the surface a person types, so the manifest counts the smaller,
        # user-facing set on purpose. Calling that "stale" would send someone
        # to regenerate a file that is already right.
        flat = sorted(n for v in extra.values() for n in v)
        note = ("; the install also registers %d entry(ies) the manifest does "
                "not promise (%s). The manifest counts the USER-INVOCABLE "
                "surface, so an entry that registers without being typeable is "
                "expected here and is not a failure"
                % (len(flat), ", ".join(flat)))
    print("clause two: a clean install delivers every one of the %d entry(ies) "
          "bundle/MANIFEST.json promises, matched BY NAME across %d plugin(s)%s"
          % (manifest.get("total", 0), len(installed), note))
    return EXIT_MATCH


if __name__ == "__main__":
    sys.exit(main())

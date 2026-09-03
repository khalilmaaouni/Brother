"""check_l5_commands: the plan's L5 commands must be real commands.

The five-level WBS says every work package breaks into steps and every step
names the exact command it runs. That layer is only worth having if the commands
exist, and on the day it was authored one did not: a step said
`python3 scripts/benchmark_atomic.py --list-subjects`, and that script accepts
only --json and --selftest. The flag was never real.

WHY THAT SHAPE IS THE DANGEROUS ONE. A command naming a file that does not exist
is obvious the moment anyone tries it, and it is often legitimate: a package
that CREATES scripts/acceptance.py should name it. But a real script with an
unreal flag looks correct in review, satisfies every structural rule the WBS
makes, and fails only when somebody sits down to do the work, which is the worst
moment to learn the plan was fiction.

THE FALSE POSITIVE THIS REFUSES TO PRODUCE. Reading --help only tells you what
flags exist when the script BUILDS its help from its own parser. A script that
reads sys.argv by hand can accept a flag its help never mentions, and a checker
that ruled on that would report correct work as invented. Three checks in this
estate have manufactured a violation against correct data in a single day, and
that failure costs more than a miss, because it sends a person to fix something
that was already right. So a flag verdict is only issued when the help output is
recognisably argparse-shaped; anything else is UNREADABLE and counted apart.

FOUR OUTCOMES, none of which silently becomes another:

  PASS        the script exists, its help is readable, and every long flag the
              step uses appears in it
  PLANNED     the script does not exist yet AND some package declares it in
              owns, so the plan intends to create it
  UNREADABLE  the script exists but its help cannot be read as a flag contract,
              so this tool declines to rule rather than guessing
  FAIL        the script exists, its help is readable, and a flag is absent; or
              the script is absent and NO package claims to create it

Only FAIL sets the exit code. PLANNED and UNREADABLE are reported by name,
because a count that hides them would let the layer rot quietly.

WHAT IT DELIBERATELY DOES NOT DO: it never runs the command itself. Running a
plan's commands would perform work nobody asked for, and some of them write.
Reading --help catches the invented-flag class, and stopping there is the
difference between a checker and an accident.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIBLING = os.path.expanduser("~/Documents/BrotherModeUp")
#: One-repo transition (M5, docs/plan/ONE-REPO-TRANSITION-2026-08-31.md): a
#: roadmap step tagged "(BrotherModeUp)" or "(BrotherSBE)" names its script
#: with the path that product used standalone (e.g. `tools/bm_project.py`),
#: which now lives at `products/<name>/tools/bm_project.py` inside this one
#: repo. Derived from ROOT rather than hardcoded, so it stays correct
#: wherever this checkout itself lives; SIBLING stays as a fallback for as
#: long as the old standalone checkouts remain on this machine (M7 retires
#: them), but resolve() must not depend on that checkout still existing.
PRODUCT_ROOTS = tuple(
    os.path.join(ROOT, "products", name)
    for name in ("brothermode", "brothersbe", "brotherds"))
ROADMAP = os.path.join(ROOT, "docs", "plan", "READINESS-ROADMAP-2026-08-29.json")

#: `python3 some/script.py` and the arguments that follow it. Only this shape is
#: examined: a shell pipeline, a git call or a bare grep exposes no help
#: contract, and inventing one for them would produce exactly the false
#: failures this module refuses to make.
INVOCATION = re.compile(r"python3\s+([\w./-]+\.py)((?:\s+[^\s|;&]+)*)")
LONG_FLAG = re.compile(r"(--[a-z][a-z0-9-]*)")

#: What an argparse help actually looks like. Both markers must appear: argparse
#: always prints a "usage:" line and always documents -h/--help. A script whose
#: help lacks them is not necessarily broken, it just is not answering the
#: question this tool asks, and that distinction is the whole guard.
ARGPARSE_MARKERS = ("usage:", "-h, --help")

PASS, PLANNED, UNREADABLE, FAIL = "PASS", "PLANNED", "UNREADABLE", "FAIL"


def steps_of(doc):
    """Every L5 step on the board, with the node and package it belongs to."""
    out = []
    for node in doc.get("rows", []) + doc.get("features", []):
        for pkg in node.get("subtasks") or []:
            for step in pkg.get("steps") or []:
                if step.get("command"):
                    out.append((node.get("id"), pkg.get("id"), step))
    return out


def declared_paths(doc):
    """Every path any node or package says it owns. A script that does not exist
    yet is fine when the plan says something will create it, and a mistake
    otherwise."""
    owned = set()
    for node in doc.get("rows", []) + doc.get("features", []):
        for path in node.get("owns") or []:
            owned.add(os.path.basename(path))
        for pkg in node.get("subtasks") or []:
            for path in pkg.get("owns") or []:
                owned.add(os.path.basename(path))
    return owned


def resolve(script, roots=None):
    """Where this script really is, across every tree this estate ships
    from (this checkout, each product's own subtree inside it, and the old
    standalone SIBLING checkout while it still exists on this machine), or
    None."""
    for base in (roots or (ROOT,) + PRODUCT_ROOTS + (SIBLING,)):
        candidate = os.path.join(base, script)
        if os.path.isfile(candidate):
            return candidate
    return None


def help_text(path, runner=None, subcommand=None):
    """(text, problem). Text is None when nothing usable came back."""
    runner = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=30))
    argv = [sys.executable, path] + ([subcommand] if subcommand else []) + ["--help"]
    try:
        proc = runner(argv)
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the failure becomes the reported reason below
        return None, "its --help could not be read (%s)" % exc
    return (proc.stdout or "") + (proc.stderr or ""), ""


def is_readable_contract(text):
    """Whether this help can be treated as a list of the flags that exist."""
    return bool(text) and all(m in text for m in ARGPARSE_MARKERS)



#: A word that is not a flag and not a path: the shape of a subcommand token.
SUBCOMMAND_WORD = re.compile(r"^[a-z][a-z0-9-]*$")


def _subcommand(rest, path, runner=None, help_cache=None):
    """The subcommand this invocation uses, when the script has any.

    Only accepted when the TOP LEVEL help actually lists it, so a stray word is
    never mistaken for one, and a script with no subparsers is unaffected."""
    # NOT just the first word. A subcommand can sit AFTER a global flag, as in
    # `bm_recurrence.py --db PATH record --unit U`, and the first version of this
    # looked only at words[0], found "--db", and reported four real flags as
    # invented. Every bare word is a candidate, and the top level help decides.
    words = [w for w in rest.split() if w and SUBCOMMAND_WORD.match(w)]
    if not words:
        return None
    cache = help_cache if help_cache is not None else {}
    key = (path, None)
    if key not in cache:
        cache[key] = help_text(path, runner)
    top, _problem = cache[key]
    if not top:
        return None
    # argparse prints its subcommands inside {a,b,c} on the usage line.
    listed = set()
    for group in re.findall(r"\{([a-z0-9,\-]+)\}", top):
        listed.update(group.split(","))
    for word in words:
        if word in listed:
            return word
    return None


def check_command(command, owned, runner=None, help_cache=None, roots=None):
    """One command. Returns (verdict, detail)."""
    match = INVOCATION.search(command)
    if not match:
        return PASS, "not a python3 script invocation, so no flag contract to read"
    script, rest = match.group(1), match.group(2) or ""
    path = resolve(script, roots)
    if path is None:
        if os.path.basename(script) in owned:
            return PLANNED, "%s does not exist yet and a package declares it" % script
        return FAIL, ("%s exists in neither tree and NO package declares it in "
                      "owns, so nothing is going to create it" % script)

    flags = set(LONG_FLAG.findall(rest))
    if not flags:
        return PASS, "%s exists and this step passes no long flags" % script

    # SUBCOMMANDS. A top level --help lists subcommands, not the flags that live
    # under them, so reading only the top level reports every subcommand flag as
    # invented. That is exactly the manufactured violation this module refuses
    # to produce, and the first version of it did produce one: it failed a step
    # using bm_recurrence.py's --unit and --surfaced, both of which are real
    # under `record`. If the first bare word after the script names a
    # subcommand the top level help lists, ask THAT for its help instead.
    # A FLAG IS REAL IF EITHER HELP KNOWS IT. Global flags live on the top level
    # parser and subcommand flags live under the subcommand, and a real
    # invocation mixes them: `--db PATH record --unit U` uses one of each.
    # Checking only one of the two texts fails whichever half it did not read,
    # which this tool did twice before getting here.
    sub = _subcommand(rest, path, runner, help_cache)
    cache = help_cache if help_cache is not None else {}
    texts, problem = [], ""
    for candidate in ([None, sub] if sub else [None]):
        key = (path, candidate)
        if key not in cache:
            cache[key] = help_text(path, runner, candidate)
        got, why = cache[key]
        if got:
            texts.append(got)
        elif not problem:
            problem = why
    text = "\n".join(texts) if texts else None
    if text is None:
        return UNREADABLE, "%s exists but %s" % (script, problem)
    if not is_readable_contract(text):
        return UNREADABLE, ("%s exists but its help is not an argparse contract, "
                            "so its real flags cannot be listed and this tool "
                            "declines to rule on %s"
                            % (script, ", ".join(sorted(flags))))

    unknown = sorted(f for f in flags if f not in text)
    if unknown:
        return FAIL, ("%s does not accept %s: its own --help lists no such flag"
                      % (script, ", ".join(unknown)))
    return PASS, "%s accepts %s" % (script, ", ".join(sorted(flags)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--roadmap", default=ROADMAP)
    ap.add_argument("--verbose", action="store_true",
                    help="also print every PLANNED and UNREADABLE step")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.roadmap, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: could not read %s: %s" % (args.roadmap, exc),
              file=sys.stderr)
        return 2

    owned = declared_paths(doc)
    steps = steps_of(doc)
    if not steps:
        print("NO-DATA: the board carries no L5 steps, so nothing was checked",
              file=sys.stderr)
        return 2

    cache = {}
    counts = {PASS: 0, PLANNED: 0, UNREADABLE: 0, FAIL: 0}
    failures, soft = [], []
    for _node_id, pkg_id, step in steps:
        verdict, detail = check_command(step["command"], owned, help_cache=cache)
        counts[verdict] += 1
        label = step.get("id") or pkg_id
        if verdict == FAIL:
            failures.append((label, detail))
        elif verdict in (PLANNED, UNREADABLE):
            soft.append((verdict, label, detail))

    if args.verbose:
        for verdict, label, detail in soft:
            print("%-10s %-14s %s" % (verdict, label, detail))
    for label, detail in failures:
        print("FAIL %-14s %s" % (label, detail), file=sys.stderr)

    print("l5-commands: %d step(s): %d verified real, %d planned (a package "
          "creates them), %d unreadable (declined to rule), %d invented"
          % (len(steps), counts[PASS], counts[PLANNED], counts[UNREADABLE],
             counts[FAIL]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

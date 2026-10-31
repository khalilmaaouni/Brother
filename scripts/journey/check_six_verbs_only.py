#!/usr/bin/env python3
"""J1's D.six-verbs-only row: what a stranger's INSTALLED plugin actually offers.

The row's own words are "the skill list a stranger sees is the six verbs". Until this existed the
row carried only `measured_by: DOOR1` and no check, so it read "the row declares no check, so
nothing measured it" and X01 could never pass it however well DOOR1 was built. A unit's claim and a
row's measurement are different things, and the plan's own delivery law says the subject of a check
is the deliverable, never a fixture: so this reads the installed tree, not this repository.

WHERE IT LOOKS. The journey walker exports CLAUDE_CONFIG_DIR for the throwaway sandbox it installed
into, so the installed plugin is found BY NAME under there rather than at a cache path this script
has no business knowing. That is the same rule the install smoke states for its own launcher lookup.

WHAT IT REFUSES. Any command file other than the door. The six verbs ship as SKILLS; the commands
directory is the slash-command surface, and 1.1.7 shipped 49 files there, 48 of them Moved stubs.

FAIL DIRECTION. An unreadable or absent install is NO-DATA, exit 2, never a pass: a surface nobody
could look at has not been shown to be small.

    python3 scripts/journey/check_six_verbs_only.py
    python3 scripts/journey/check_six_verbs_only.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import os
import sys

DOOR = "brother.md"
#: The six verbs the door is allowed to offer, as skill directory names.
VERBS = ("start", "status", "next", "review", "deliver", "help")


def installed_roots(config):
    """Every installed plugin root under `config`, found by the commands directory it carries."""
    out = []
    for base, dirs, _files in os.walk(config):
        if os.path.basename(base) == "commands" and os.path.basename(
                os.path.dirname(base)) != "skills":
            out.append(base)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    return sorted(out)


def judge(config):
    """(exit code, one line). Pure, so the selftest judges this and not a copy of it."""
    if not config:
        return 2, ("NO-DATA: no CLAUDE_CONFIG_DIR in the environment, so there is no installed "
                   "plugin to look at and nothing has been shown about a stranger's surface")
    if not os.path.isdir(config):
        return 2, "NO-DATA: %s is not a directory, so no install can be read" % config
    roots = installed_roots(config)
    if not roots:
        return 2, ("NO-DATA: no installed commands directory under %s, so the surface a stranger "
                   "sees could not be read" % config)
    extra = []
    for root in roots:
        try:
            names = sorted(n for n in os.listdir(root) if n.endswith(".md"))
        except OSError as exc:
            return 2, "NO-DATA: %s could not be listed: %s" % (root, exc)
        extra.extend("%s/%s" % (os.path.basename(os.path.dirname(root)), n)
                     for n in names if n != DOOR)
    if extra:
        return 1, ("FAIL: the installed plugin offers %d command(s) besides the door, so a "
                   "stranger's list is not six verbs: %s"
                   % (len(extra), " ".join(sorted(extra)[:8])))
    return 0, ("PASS: the installed plugin offers only %s across %d commands director(y/ies), so "
               "the list a stranger sees is the six verbs" % (DOOR, len(roots)))


def selftest():
    import tempfile
    cases = []

    def case(label, build, want):
        root = tempfile.mkdtemp(prefix="sixverbs-")
        build(root)
        got, line = judge(root)
        ok = got == want
        cases.append((label, want, got, ok, line))

    def only_door(root):
        d = os.path.join(root, "plugins", "cache", "brother", "brother", "1.0.0", "commands")
        os.makedirs(d)
        open(os.path.join(d, DOOR), "w").close()

    def door_plus_one(root):
        only_door(root)
        d = os.path.join(root, "plugins", "cache", "brother", "brother", "1.0.0", "commands")
        open(os.path.join(d, "brothermode-start.md"), "w").close()

    def nothing(root):
        os.makedirs(os.path.join(root, "plugins"))

    def skills_commands_only(root):
        # A commands directory UNDER skills/ is not the slash-command surface, so it must not count.
        d = os.path.join(root, "plugins", "cache", "brother", "brother", "1.0.0", "skills",
                         "commands")
        os.makedirs(d)
        open(os.path.join(d, "whatever.md"), "w").close()

    case("only the door is PASS", only_door, 0)
    case("the door plus one stub is FAIL", door_plus_one, 1)
    case("no install at all is NO-DATA", nothing, 2)
    case("a commands dir under skills is not the surface, so still NO-DATA",
         skills_commands_only, 2)
    got, _line = judge("")
    cases.append(("no config dir in the environment is NO-DATA", 2, got, got == 2, ""))
    got, _line = judge(os.path.join(os.sep, "nonexistent", "config"))
    cases.append(("a config path that is not a directory is NO-DATA", 2, got, got == 2, ""))

    bad = 0
    for label, want, got, ok, line in cases:
        bad += 0 if ok else 1
        print("%-56s want %d got %d %s" % (label, want, got, "ok" if ok else "MISMATCH"))
        if not ok and line:
            print("      " + line)
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""),
                    help="the installed sandbox's config dir (default: CLAUDE_CONFIG_DIR)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    code, line = judge(args.config)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

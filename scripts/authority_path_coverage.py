#!/usr/bin/env python3
"""R1: does one event receive two independent enforcing decisions?

THE PROPERTY THIS MEASURES. A policy should be decided once, by one piece of
code, whichever tool a session happens to reach for. When the same policy is
enforced by DIFFERENT code on different tool paths, two things follow and both
are bad: the two implementations can disagree, and a path nobody wired is a
path with no decision at all.

WHY THIS IS NOT A THEORETICAL ROW. On 2026-08-24 a subagent was refused twice
on its Write tool and then wrote the same bytes through a shell heredoc, which
succeeded. Same file, same content, two different answers, because the gate
that refused was not registered on the shell path.

WHAT IT READS. Hook registrations only: the event, the tool matcher, and the
hook filename. It never reads hook source, never reads a transcript, and never
prints a path from outside this estate's own products.

WHAT IT CANNOT DO, stated rather than discovered later. Registration is not
behaviour. A hook registered on a tool may still decline to act, and a hook
with an empty matcher may filter internally in ways this cannot see. So a
finding here is a QUESTION worth answering, never a proven bypass. The one
proven bypass in this estate was observed in a transcript, not derived here.

    python3 scripts/authority_path_coverage.py            report only, exit 0
    python3 scripts/authority_path_coverage.py --strict   exit 1 if a policy is
                                                          split across tool paths

WHY --strict WAS ADDED, 2026-08-25. R1's done_check was PROSE ONLY, like 8 of
the 9 blockers, which is exactly why that list decays: re-measuring it cost a
hand derivation per row. A reporter cannot close a row. This flag turns the
measurement into something a done_check can NAME and a machine can RUN, without
changing the default, which stays a report.

The default stays report-only on purpose: registration is not behaviour, so an
unattended run should not fail a build on a question rather than a finding.
"""
import json
import pathlib
import sys

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Create", "Bash"}

# Only this estate's own products are named in output. Everything else on the
# machine is counted and never printed, because third party plugin names and
# installed inventory are machine internals and this repository is PUBLIC.
OURS = ("sbe_", "bm_", "repeat_guard", "spend_guard", "github_cost_wall")


def registrations(root):
    """Every PreToolUse registration, as (matcher, hook filename), and every
    hooks.json this pass could not open or parse.

    A file that failed to read used to vanish through a bare `except
    Exception: continue`: coverage measured over the files that happened to
    parse read as coverage of the whole tree, and a hook sitting in the one
    file that failed to open was never counted as registered OR as missing,
    just gone with nobody told. Caught narrowly instead (OSError for
    read and permission failures, ValueError for bad JSON and bad text
    encoding, both of which json.JSONDecodeError and UnicodeDecodeError
    subclass) so a genuinely unexpected exception still surfaces rather than
    being folded into the same silent skip.
    """
    out = []
    unread = []
    files = list(root.glob("plugins/**/hooks/hooks.json"))
    files += list(root.glob("plugins/**/hooks.json"))
    settings = root / "settings.json"
    if settings.exists():
        files.append(settings)
    # The two glob patterns above overlap on any hooks.json that sits directly
    # under a directory named "hooks": both find it, so a broken file there
    # was counted, opened and named TWICE, once per pattern. Deduped here,
    # order preserved, so a bad file is reported once, honestly.
    files = list(dict.fromkeys(files))
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as e:
            unread.append("%s (%s)" % (f, type(e).__name__))
            continue
        for entry in (d.get("hooks") or {}).get("PreToolUse", []) or []:
            if not isinstance(entry, dict):
                continue
            matcher = entry.get("matcher") or ""
            for h in entry.get("hooks", []) or []:
                cmd = (h.get("command") or "")
                name = cmd.split("/")[-1].split('"')[0].strip()
                if name:
                    out.append((matcher, name))
    return sorted(set(out)), sorted(unread)


def tools_of(matcher):
    return {t for t in WRITE_TOOLS if t in matcher} if matcher else set()


def main():
    root = pathlib.Path.home() / ".claude"
    regs, unread = registrations(root)
    ours = [(m, n) for m, n in regs if any(n.startswith(p) for p in OURS)]

    print("AUTHORITY PATH COVERAGE, this estate's own guards only")
    print("total PreToolUse registrations seen: %d (%d ours, %d not named here)"
          % (len(regs), len(ours), len(regs) - len(ours)))
    if unread:
        # A file this pass could not open is source this report does not
        # cover, named rather than folded into a count that reads as clean.
        print("%d hooks.json could not be opened or parsed, so this run says "
              "nothing about what they registered: %s"
              % (len(unread), ", ".join(unread)))
    print()

    by_hook = {}
    for m, n in ours:
        by_hook.setdefault(n, set()).update(tools_of(m))

    print("%-30s %s" % ("GUARD", "WRITE-CAPABLE TOOLS IT IS REGISTERED ON"))
    for n in sorted(by_hook):
        t = by_hook[n]
        print("%-30s %s" % (n, ", ".join(sorted(t)) if t else "(no write tool in matcher)"))

    covered = set().union(*by_hook.values()) if by_hook else set()
    print()
    print("write-capable tools with NO guard of ours registered: %s"
          % (", ".join(sorted(WRITE_TOOLS - covered)) or "none"))

    print()
    print("SPLIT ENFORCEMENT, the R1 question:")
    findings = 0
    for n in sorted(by_hook):
        t = by_hook[n]
        if not t:
            continue
        if "Bash" in t and len(t) == 1:
            sibs = [o for o in by_hook if o != n and by_hook[o] and "Bash" not in by_hook[o]
                    and o.split("_")[0] == n.split("_")[0]]
            if sibs:
                findings += 1
                print("  SPLIT: %s covers ONLY Bash while %s cover(s) the tool paths."
                      % (n, ", ".join(sorted(sibs))))
                print("         One policy, two implementations, decided by which tool was reached for.")
    if not findings:
        print("  none detected by registration shape")
    print()
    print("REMINDER: registration is not behaviour. Treat every line above as a")
    print("question to answer by reading the hooks, never as a proven bypass.")
    if "--strict" in sys.argv:
        if findings:
            print()
            print("STRICT: %d policy split across tool paths. R1's property does NOT hold."
                  % findings)
            return 1
        if not covered:
            print()
            print("STRICT: NO-DATA, no guard of ours was found registered on any write tool,")
            print("so this proved nothing. NO-DATA is not a pass.")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

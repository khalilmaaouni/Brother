#!/usr/bin/env python3
"""laws_audit: R28.1, the law auditor (docs/plan/READINESS-ROADMAP-2026-08-29.json).

WHY THIS EXISTS. The founder's own words that opened R28: "add to the to do
list and the brother readiness board an enforcement act of the laws and
learnings as very often they are forgotten and this is a huge weakness." The
global law book (~/.claude/CLAUDE.md) already marks most of its own rules
ENFORCED or UNENFORCED by hand, in prose, at the end of each section. Nothing
ever checked that an ENFORCED claim still has a real file behind it, or
collected the UNENFORCED claims into one list. This script does both,
mechanically, from the law book's own text: it never hand-curates the list of
laws, it PARSES the ENFORCEMENT lines that are already there.

WHAT COUNTS AS A LAW, mechanically. A markdown bullet whose line starts with
"- ENFORCEMENT" or "- ENFORCED" (the two spellings this estate's law books
actually use), read under the nearest preceding "##" or "###" heading, which
becomes the law's section name. Two ENFORCEMENT bullets under one heading
(the estate has sections with a preventive control and a detective one) are
two laws, numbered "#2", "#3", ... so neither is silently dropped.

STATUS is read from the bullet's own text: the word UNENFORCED anywhere in
the line means UNENFORCED; otherwise the word ENFORCED means ENFORCED (word
boundaries only, so UNENFORCED never gets misread as containing ENFORCED,
because there is no boundary between the "N" and "E" inside it). A line with
neither word is UNKNOWN, printed and never silently dropped.

ENFORCER FILES and PROVING COMMANDS are read from the same line's own
backtick-quoted spans: a span with no whitespace that looks like a path
(starts with ~, ., or / , or ends in a known extension) is a candidate
enforcer file; a span with whitespace or a shell operator is a proving
command. This is a regex heuristic over prose, not a parser of a formal
schema (the law book has none), so a law that names its enforcer in plain
words with no backticks reports NO-FILE-NAMED rather than a guess.
# ponytail: regex-over-prose heuristic, not a real grammar. Ceiling: a law
# whose ENFORCEMENT line never puts its enforcer path in backticks reports
# NO-FILE-NAMED even when the prose names one. Upgrade path: require every
# new ENFORCEMENT line to backtick its enforcer, which the law book mostly
# already does.

VERIFY MODE (the default, no flag): every ENFORCED law with a named enforcer
file must have that file actually on disk. A law naming a missing file FAILS
BY NAME. A law naming no file at all is reported NO-FILE-NAMED, which is a
parse gap, not a fail (this estate has real ENFORCED laws, like the plugin
gate at "each plugin's SessionStart hook", whose enforcer is not one path).
UNENFORCED laws are findings only, never a failure: that is the whole point
of the honest UNENFORCED marker.

Proving commands are NEVER executed by default (some are expensive: full
repo scans, simulator builds). --run executes each one with a short timeout
and reports its own exit code, still never turning an UNENFORCED law red.

Exit codes, this estate's convention: 0 every ENFORCED law with a named file
has it; 1 at least one ENFORCED law's named file is missing, named; 2
NO-DATA, no law book was readable and zero laws were parsed at all.

Python 3, standard library only. No network by default. No em or en dashes.
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")

DEFAULT_BOOKS = (
    os.path.join(HOME, ".claude", "CLAUDE.md"),
    os.path.join(ROOT, "CLAUDE.md"),
    os.path.join(HOME, ".claude", "projects", "-Users-khalil-maaouni-Brother",
                 "memory", "MEMORY.md"),
)

_HEADING_RE = re.compile(r"^#{2,3}\s+(.*)")
_LAW_RE = re.compile(r"^-\s*(ENFORCEMENT|ENFORCED)\b")
_UNENFORCED_RE = re.compile(r"\bUNENFORCED\b")
_ENFORCED_RE = re.compile(r"\bENFORCED\b")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_PATH_EXT_RE = re.compile(r"\.(py|sh|json|md|yml|yaml)$")
_CANDIDATE_RE = re.compile(r"[Cc]andidate[^.:]*:?\s*([^.]+)")

NODATA = "NO-DATA"


def _classify_backtick(tok):
    if re.search(r"[\s|;]|&&", tok):
        return "command"
    if "*" in tok or "?" in tok:
        # A glob (.github/workflows/*.yml) is a SCOPE the enforcer acts on,
        # never the enforcer file itself; os.path.exists on a literal glob
        # string is always False, which would FAIL a law that is really
        # fine. Never treated as a path candidate.
        return "other"
    if "/" in tok and (tok.startswith("~") or tok.startswith(".")
                        or tok.startswith("/") or _PATH_EXT_RE.search(tok)):
        return "path"
    return "other"


_PATH_TOKEN_RE = re.compile(r"^[~./][^\s`]*\.(py|sh|json|md|yml|yaml)$")


def _paths_inside_command(cmd):
    """Path-shaped whitespace tokens inside a proving-command backtick span,
    e.g. `python3 ~/Brother/scripts/close_ceremony_check.py` names its own
    enforcer only here, never as a separate bare-path backtick. Recovers
    that without guessing: only tokens matching a real path shape (leading
    ~/./ and a known extension) qualify, so a bare flag or word never does."""
    return [tok for tok in cmd.split() if _PATH_TOKEN_RE.match(tok)]


def parse_law_book(text, source):
    """[{source, section, line, status, enforcer_files, proving_commands,
    text}, ...] for every ENFORCEMENT/ENFORCED bullet in text, in order."""
    laws = []
    section = "(no section)"
    seen = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        m_head = _HEADING_RE.match(line)
        if m_head:
            section = m_head.group(1).strip()
            continue
        if not _LAW_RE.match(line):
            continue
        if _UNENFORCED_RE.search(line):
            status = "UNENFORCED"
        elif _ENFORCED_RE.search(line):
            status = "ENFORCED"
        else:
            status = "UNKNOWN"
        backticks = _BACKTICK_RE.findall(line)
        paths = [t for t in backticks if _classify_backtick(t) == "path"]
        commands = [t for t in backticks if _classify_backtick(t) == "command"]
        for cmd in commands:
            for tok in _paths_inside_command(cmd):
                if tok not in paths:
                    paths.append(tok)
        seen[section] = seen.get(section, 0) + 1
        name = section if seen[section] == 1 else "%s #%d" % (section, seen[section])
        candidate = None
        m_cand = _CANDIDATE_RE.search(line)
        if m_cand:
            candidate = m_cand.group(1).strip()[:160]
        laws.append({
            "source": source,
            "section": name,
            "line": lineno,
            "status": status,
            "enforcer_files": paths,
            "proving_commands": commands,
            "candidate": candidate,
            "text": line.strip()[:300],
        })
    return laws


def load_books(paths):
    """(laws, unreadable) for every path in paths that exists and decodes;
    a missing or unreadable path is named in unreadable, never a crash."""
    laws = []
    unreadable = []
    for path in paths:
        if not os.path.isfile(path):
            unreadable.append((path, "not found"))
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            unreadable.append((path, str(exc)))
            continue
        laws.extend(parse_law_book(text, path))
    return laws, unreadable


def resolve_path(p):
    """A law's enforcer path, resolved to a real filesystem path.

    `~/Brother/...` is this estate's own convention for "the product repo
    checked out at the founder's home", but an agent runs from a WORKTREE
    (docs/plan's own single-writer fence, and the recorded lesson that a cd
    into the shared primary checkout reads or writes the wrong tree). So a
    `~/Brother/` prefix resolves against THIS run's own repo root, never the
    literal home path, which could be on any branch at the moment this runs.
    Any other `~`-prefixed path (a hooks file, a sibling repo) still expands
    to the real home directory, because those are not this repo's own files.
    """
    if p.startswith("~/Brother/"):
        return os.path.join(ROOT, p[len("~/Brother/"):])
    if p.startswith("~"):
        return os.path.expanduser(p)
    if p.startswith("/"):
        return p
    return os.path.join(ROOT, p)


def verify(laws, run_commands=False, timeout=20):
    """(problems, ran) for the ENFORCED subset of laws. problems is a list
    of strings, one per FAILing law, naming the law and the missing file.
    ran is a list of (law, command, returncode) for --run, empty otherwise."""
    problems = []
    ran = []
    for law in laws:
        if law["status"] != "ENFORCED":
            continue
        missing = [p for p in law["enforcer_files"] if not os.path.exists(resolve_path(p))]
        if missing:
            problems.append(
                "FAIL: %s (%s:%d) names enforcer %s which does not exist"
                % (law["section"], law["source"], law["line"], ", ".join(missing)))
        if run_commands:
            for cmd in law["proving_commands"]:
                try:
                    # shell=True is deliberate: the command is the law book's
                    # own backtick-quoted proving command (pipes, grep -E,
                    # redirects), never external input, and --run is opt-in
                    # so this never fires by default.
                    proc = subprocess.run(cmd, shell=True, cwd=ROOT,
                                          capture_output=True, timeout=timeout)
                    ran.append((law, cmd, proc.returncode))
                except (OSError, subprocess.TimeoutExpired) as exc:
                    ran.append((law, cmd, "ERROR:%s" % exc))
    return problems, ran


def render_list(laws):
    lines = []
    for law in laws:
        if law["status"] == "ENFORCED":
            enforcer = ", ".join(law["enforcer_files"]) if law["enforcer_files"] else "NO-FILE-NAMED"
            lines.append("%-10s %-55s enforcer=%s" % (law["status"], law["section"][:55], enforcer))
        elif law["status"] == "UNENFORCED":
            candidate = law["candidate"] or "(no candidate named)"
            lines.append("%-10s %-55s candidate=%s" % (law["status"], law["section"][:55], candidate))
        else:
            lines.append("%-10s %-55s (status word not found)" % (law["status"], law["section"][:55]))
        lines.append("    %s:%d %s" % (law["source"], law["line"], law["text"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="parse and print every law only, never verify enforcer files")
    ap.add_argument("--run", action="store_true",
                    help="also execute each ENFORCED law's proving command (expensive, opt-in)")
    ap.add_argument("--book", action="append", default=None,
                    help="override the default law books scanned (repeatable); "
                         "for fixtures and tests, never a real book path by default")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    books = args.book if args.book else list(DEFAULT_BOOKS)
    laws, unreadable = load_books(books)

    for path, reason in unreadable:
        print("%s: law book %s unreadable (%s)" % (NODATA, path, reason))

    if not laws:
        print("%s: zero ENFORCEMENT/ENFORCED bullets parsed from %d book(s)"
              % (NODATA, len(books)))
        return 2

    enforced = [law for law in laws if law["status"] == "ENFORCED"]
    unenforced = [law for law in laws if law["status"] == "UNENFORCED"]
    unknown = [law for law in laws if law["status"] == "UNKNOWN"]

    print(render_list(laws))
    print()
    print("laws_audit: %d law(s): %d ENFORCED, %d UNENFORCED, %d UNKNOWN"
          % (len(laws), len(enforced), len(unenforced), len(unknown)))

    if args.list:
        return 0

    problems, ran = verify(laws, run_commands=args.run)
    for law, cmd, code in ran:
        print("RUN %s (%s:%d) `%s` -> exit %s"
              % (law["section"], law["source"], law["line"], cmd, code))
    if problems:
        print()
        for p in problems:
            print(p)
        return 1
    print("PASS: every ENFORCED law with a named enforcer file has it on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())

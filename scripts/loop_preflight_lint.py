#!/usr/bin/env python3
"""Three failure classes that cost a whole day, each mechanically detectable in source before it can bite.

usage (repo root):
  python3 -B scripts/loop_preflight_lint.py [PATH ...]      default: scripts/ and the live loop tools
  python3 -B scripts/loop_preflight_lint.py --selftest
Exit 0 clean, 1 when a finding stands, 2 when nothing could be read (NO-DATA, never a pass).

Every rule below is here because it ALREADY HAPPENED on 2026-09-21, and each names the cost.

  EPHEMERAL-STATE   a tool reads durable state from /tmp or /var/folders.
                    The OpenRouter ledger and cap grant lived in /tmp and were destroyed by TWO reboots the
                    same day, taking the day's spend record with them and leaving every lane unfunded. The
                    board generator read gantt-data.json and gantt-tpl.html from /tmp, which NOTHING in the
                    repository wrote, so it raised on its first line and the board could not be produced at
                    all. Money, progress and provenance are never ephemeral.

  TEST-LOCALITY     a scripts/ test imports products/ or plugin/.
                    It passes locally and FAILS at the push gate, because a scripts/ test is run against the
                    PUBLIC export tree which does not ship those packages. This happened TWICE in one day. The
                    second one left a landing commit unpushed, which made every later landing refuse on parity,
                    starved the runner pool, and ran 22 dead passes. The push gate catching it is correct and
                    far too late: by then the work is committed and the loop is stuck.

  NAME-SORTED-TIME  a run folder collection sorted by NAME rather than by the clock.
                    Run folders are <sub>-HHMMSS with no date, so a name sort puts last night after this
                    morning. Nine finished builds were invisible and six units read as needing a human fact
                    when their newest run had come back READY. Before: READY 0, NEEDFACT 17. After: READY 9,
                    NEEDFACT 11, same tree, same command.

A lint that fires on its own examples is worth having; one that fires on everything gets ignored. Each rule is
narrow, each has a real instance behind it, and each can be waived in one visible line."""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WAIVER = re.compile(r"#\s*lint:\s*allow-(ephemeral-state|test-locality|name-sorted-time)\b")

# A BARE PREFIX IS NOT A PATH. `_OUT_OF_REPO_PREFIXES = ('~/', '/home/', '/Users/', '/tmp/')` is DATA ABOUT
# paths, not state held in one, and flagging it is a false positive that teaches people to ignore the lint. So
# at least one real segment must follow the temp root.
EPHEMERAL = re.compile(r"""["'](?:/tmp/|/private/tmp/|/var/folders/)[^"'\s]+["']""")
SCRATCHY = re.compile(r"\b(mkdtemp|NamedTemporaryFile|TemporaryDirectory|gettempdir|scratch|sandbox)\b", re.I)
IMPORTS_PKG = re.compile(r"^\s*(?:from|import)\s+(products|plugin)\.", re.M)
# LINE BASED on purpose. The first version was a single regex over the whole file and could not cross the
# nested parentheses in sorted(glob.glob(os.path.join(RUNS, "*-*/"))), so it matched nothing and the rule
# silently did not exist. Its own selftest caught that. A sort is a finding when it orders run folders and
# names no key: with a key= the caller has chosen an ordering deliberately.
def _name_sorted_time(lines, i, lookahead=3):
    """A sort over run folders with NO key, read as a STATEMENT rather than a line.

    Reading one line flagged two tools that were already FIXED, because their `key=` sits on the continuation
    line. A lint that flags correct code teaches people to ignore it, which is worse than no lint, so the
    statement is what gets judged: the match line plus the few lines the call can spill onto."""
    line = lines[i]
    if not ("sorted(" in line and "glob.glob(" in line and ("RUNS" in line or "unit-runs" in line)):
        return False
    return "key=" not in "".join(lines[i:i + lookahead])


def lint_text(path, text):
    """[(rule, line, detail)] for one file. A waiver comment on the SAME line silences that one finding."""
    out = []
    lines = text.splitlines()

    def waived(i, rule):
        return bool(WAIVER.search(lines[i])) and rule in lines[i]

    for m in EPHEMERAL.finditer(text):
        i = text[:m.start()].count("\n")
        line = lines[i] if i < len(lines) else ""
        if waived(i, "ephemeral-state"):
            continue
        # NARROWED after its first run, which produced 156 findings across 942 files. A lint that fires on
        # everything is a lint nobody reads, which is the failure its own docstring warns about. A test writing
        # a fixture into /tmp is FINE and is what that directory is for. The real defect was DURABLE state held
        # there: a ledger, a cap grant, a board's data and template. Every one of those was a MODULE LEVEL
        # CONSTANT, evaluated once at import and depended on for the process's whole life. So the rule is now
        # exactly that shape: an unindented assignment of a temp path to a name. Scratch use inside a function
        # is out of scope by construction, which removed the noise without losing a single real case.
        if SCRATCHY.search(line):
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            continue
        out.append(("EPHEMERAL-STATE", i + 1,
                    "reads or writes %s, which a reboot deletes; durable state belongs under the caller's home"
                    % m.group(0).strip("\"'")))

    base = os.path.basename(path)
    if path.replace(os.sep, "/").startswith("scripts/") and base.startswith("test_"):
        for m in IMPORTS_PKG.finditer(text):
            i = text[:m.start()].count("\n")
            if waived(i, "test-locality"):
                continue
            out.append(("TEST-LOCALITY", i + 1,
                        "a scripts/ test importing %s passes here and FAILS at the push gate, because a "
                        "scripts/ test runs on the public export tree; put it beside its module"
                        % m.group(1)))

    for i, line in enumerate(lines):
        if not _name_sorted_time(lines, i):
            continue
        if waived(i, "name-sorted-time"):
            continue
        out.append(("NAME-SORTED-TIME", i + 1,
                    "run folders sorted by NAME; they carry HHMMSS with no date, so this puts last night "
                    "after this morning. Sort by the filesystem clock"))
    return out


def targets(paths):
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            for d, dirs, files in os.walk(p):
                dirs[:] = [x for x in dirs if x not in ("__pycache__", ".git", "node_modules")]
                out += [os.path.join(d, f) for f in files if f.endswith(".py")]
    return sorted(set(out))


def selftest():
    cases = [
        ("a TUPLE OF PREFIXES is data about paths, not state in one",
         not any(r == "EPHEMERAL-STATE" for r, _, _ in lint_text("scripts/x.py",
                 "_OUT_OF_REPO_PREFIXES = ('~/', '/home/', '/Users/', '/tmp/')\n"))),
        ("a durable read from /tmp is a finding",
         any(r == "EPHEMERAL-STATE" for r, _, _ in
             lint_text("scripts/x.py", 'LEDGER = "/tmp/brother-or-dispatch-state/openrouter-ledger.jsonl"\n'))),
        ("a scratch temp dir is NOT a finding",
         not any(r == "EPHEMERAL-STATE" for r, _, _ in
                 lint_text("scripts/x.py", 'd = tempfile.mkdtemp(dir="/tmp/work")\n'))),
        ("an ephemeral finding can be waived in one visible line",
         not any(r == "EPHEMERAL-STATE" for r, _, _ in
                 lint_text("scripts/x.py", 'P = "/tmp/x.json"  # lint: allow-ephemeral-state deliberate scratch\n'))),
        ("a scripts/ test importing products is a finding",
         any(r == "TEST-LOCALITY" for r, _, _ in
             lint_text("scripts/test_a.py", "from products.brothermode.vault_ui import x\n"))),
        ("a scripts/ test importing plugin is a finding",
         any(r == "TEST-LOCALITY" for r, _, _ in
             lint_text("scripts/test_a.py", "import plugin.runtime.brother.core.x\n"))),
        ("the SAME import beside its module is NOT a finding",
         not any(r == "TEST-LOCALITY" for r, _, _ in
                 lint_text("products/brothermode/vault_ui/test_a.py", "from products.x import y\n"))),
        ("a scripts/ NON test importing products is not this rule's business",
         not any(r == "TEST-LOCALITY" for r, _, _ in
                 lint_text("scripts/tool.py", "from products.x import y\n"))),
        ("sorting run folders by name is a finding",
         any(r == "NAME-SORTED-TIME" for r, _, _ in
             lint_text("scripts/x.py", 'for d in sorted(glob.glob(os.path.join(RUNS, "*-*/"))):\n'))),
        ("sorting them by mtime is not",
         not any(r == "NAME-SORTED-TIME" for r, _, _ in
                 lint_text("scripts/x.py", 'for d in sorted(glob.glob(p), key=os.path.getmtime):\n'))),
        ("a key on the CONTINUATION line is still a key, and must not be flagged",
         not any(r == "NAME-SORTED-TIME" for r, _, _ in lint_text("scripts/x.py",
                 'for d in sorted(glob.glob(os.path.join(RUNS, "*-*/")),\n'
                 '               key=lambda p: (_dir_time(p), os.path.basename(p))):\n    pass\n'))),
        ("a clean file yields nothing", lint_text("scripts/x.py", "def f():\n    return 1\n") == []),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--max", type=int, default=None,
                    help="a RATCHET: fail only when findings EXCEED this known baseline")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    paths = a.paths or ["scripts", os.path.expanduser("~/.claude/bin")]
    files = targets(paths)
    if not files:
        print("NO-DATA: nothing to lint under %s; this is not a clean run" % ", ".join(paths))
        return 2
    findings = 0
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        rel = os.path.relpath(f, ROOT)
        if os.path.basename(f) == "loop_preflight_lint.py":
            continue                  # its own rules and fixtures contain the patterns it looks for, by design
        for rule, line, detail in lint_text(rel, text):
            findings += 1
            if findings <= 20:
                print("%-17s %s:%d  %s" % (rule, rel, line, detail))
    print("LINTED  %d file(s) | findings %d" % (len(files), findings))
    # A RATCHET, NOT A WALL. Two findings exist today, both the same pre existing restore drill convention
    # (CONVENTIONAL_TOOLS_DIR under /tmp). Changing a drill's convention is a real change and not one to make
    # blind, so the gate blocks NEW instances rather than demanding the old ones be fixed first. Lower the
    # baseline whenever one is genuinely fixed; that is the ratchet turning.
    if a.max is not None:
        if findings > a.max:
            print("RATCHET FAILED: %d finding(s), above the known baseline of %d. A NEW instance was added."
                  % (findings, a.max))
            return 1
        if findings < a.max:
            print("RATCHET LOOSE: %d finding(s), BELOW the baseline of %d. Lower --max to lock the gain in."
                  % (findings, a.max))
        return 0
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

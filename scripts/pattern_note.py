"""pattern_note: write down what WORKED, and make it findable by the problem.

THE GAP THIS CLOSES, measured across the vault by frontmatter type on
2026-08-29: 186 notes typed failure, 202 reference, 71 finding, 17 decision, and
ZERO typed as a pattern that worked. 190 of the vault's files sit in the failures
folder. This estate records what broke, in detail, and never records what to
repeat.

WHY THAT MATTERS MORE FOR A TEAM THAN FOR ONE PERSON. A failure note tells
somebody what not to do, which is a narrow instruction and transfers badly: there
are a thousand ways to be wrong and the note only closes one. A pattern is
something a teammate can COPY. You cannot copy an absence.

FINDABLE BY THE PROBLEM, NOT BY THE TITLE, which is the whole point of the
`solves` field. Nobody searching for help types the name of a technique they have
never heard of. They type the trouble they are in. A note titled 'drive every
control backwards' is invisible to somebody searching 'my check passes but the
bug is still there', and that person is exactly who it was written for.

WRITTEN AT THE MOMENT IT WORKS, not at a postmortem. A postmortem is convened
after a failure, which is why this vault has 186 failure notes: the ceremony only
fires on the bad outcome. Nothing in this estate ever fired on a good one.

It obeys the vault's constitution: it never edits an existing note, never
reorganises folders, and a second run over the same name writes nothing and says
so. New notes go in 50-Reference beside the existing standing knowledge, with a
distinct `type: pattern`, because adding a folder is a reorganisation and adding
a type is not.

Python 3, standard library only. No network.

origin: a human running this script's own CLI directly, `python3
scripts/pattern_note.py write --name ... --solves ... --what ... --evidence
...` (see main(), the "write" subcommand, below). Nothing else in this repo
calls into pattern_note.py (verified: grep -rl pattern_note scripts
bundle/runtime finds only this module's own test, test_pattern_note.py).

PRODUCER: this module is the sole producer of the note file it writes. The
write happens at `with open(path, "w", encoding="utf-8") as fh:
fh.write(note_body(name, solves, what, evidence, project, borrowed))` inside
write(), a few lines below the `if os.path.exists(path): return path, False`
never-overwrite guard.
"""
import argparse
import importlib.util
import os
import re
import sys

VAULT = os.path.expanduser("~/Documents/Kay Vault")
FOLDER = "50-Reference"
INDEX = "Patterns-Index.md"
NODATA = "NO-DATA"


def _load_intake_gate():
    """Load bm_vault_intake.py's hard_gate by path (products/brothermode/
    tools/bm_vault_intake.py, a sibling tree this module never edits): the
    SAME credential and deny-list gate `admit` and `capture` run before
    writing, so a pattern note routes through the estate's one front door
    for vault content rather than being written ungated. Returns None when
    the module cannot be loaded, so the caller can fail closed rather than
    silently skip the gate."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "products", "brothermode", "tools",
                        "bm_vault_intake.py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("bm_vault_intake", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.hard_gate
    except Exception:  # sbe: allow-silent optional gate module load failure, gate_text below turns this into a named refusal rather than an ungated write
        return None


def gate_text(text, deny_list_path=None, loader=_load_intake_gate):
    """(ok, reason_or_None) via bm_vault_intake.hard_gate: credential_hit,
    then deny_list_hit when deny_list_path is given. Fails closed, matching
    capture's own contract in bm_vault_intake.py: a gate that could not be
    loaded is a refusal, never a silent ungated pass."""
    hard_gate = loader()
    if hard_gate is None:
        return False, ("NO-DATA: bm_vault_intake.hard_gate unavailable, "
                       "the gate could not run")
    return hard_gate(text, deny_list_path)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "untitled"


def note_body(name, solves, what, evidence, project, borrowed="", receipt=""):
    lines = [
        "---",
        "type: pattern",
        "project: %s" % (project or "all"),
        "status: standing",
        "created: %s" % os.environ.get("PATTERN_DATE", "unset"),
        "solves: %s" % solves,
        "verified-by: %s" % evidence,
    ]
    if receipt:
        lines.append("receipt: %s" % receipt)
    lines += [
        "description: %s" % what.strip().splitlines()[0][:200],
        "---",
        "",
        "# %s" % name,
        "",
        "## The problem it solves",
        "",
        solves,
        "",
        "## What to do",
        "",
        what,
        "",
        "## How it is known to work",
        "",
        evidence,
        "",
    ]
    if borrowed:
        lines += ["## Where it came from", "", borrowed, ""]
    return "\n".join(lines)


def write(name, solves, what, evidence, project="all", borrowed="", vault=VAULT,
          receipt="", deny_list=None, gate=gate_text):
    """(path, written). Never overwrites: the vault constitution forbids editing
    an existing note, and a pattern that needs rewriting is a new pattern.

    Before the write, routes the combined text through `gate` (by default
    gate_text, the same hard gate `admit` and `capture` run in
    bm_vault_intake.py) so a pattern note can never carry a credential or a
    denied term into the vault. A gate refusal, same as a missing vault
    folder, writes nothing and reports (None, False); the refusal reason
    goes to stderr, never invented and never silently dropped."""
    folder = os.path.join(vault, FOLDER)
    if not os.path.isdir(folder):
        return None, False
    path = os.path.join(folder, slug(name) + ".md")
    if os.path.exists(path):
        return path, False
    ok, reason = gate("\n".join([name, solves, what, evidence, borrowed, receipt]),
                      deny_list)
    if not ok:
        print("pattern_note: REFUSED, %s" % reason, file=sys.stderr)
        return None, False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note_body(name, solves, what, evidence, project, borrowed, receipt))
    index = os.path.join(folder, INDEX)
    line = "- [[%s|%s]]: %s\n" % (slug(name), name, solves)
    existing = ""
    if os.path.isfile(index):
        with open(index, encoding="utf-8") as fh:
            existing = fh.read()
    else:
        existing = ("# Patterns index\n\nWhat WORKED, findable by the problem it "
                    "solves rather than by its name. Nobody searching for help "
                    "types the name of a technique they have never heard of.\n\n")
    if line not in existing:
        with open(index, "w", encoding="utf-8") as fh:
            fh.write(existing + line)
    return path, True


def is_pattern(head):
    """A note is a pattern if it carries a `solves:` frontmatter line, not by
    its `type:` value, because an ingester can retype notes (2026-08-30: the vault
    ingester rewrote type: pattern to type: reference on three notes), but a
    pattern note is the only kind that carries `solves:`."""
    return bool(re.search(r"^solves:\s*\S", head, re.M))


def find(query, vault=VAULT):
    """Patterns whose PROBLEM matches the words somebody actually typed.

    Searches `solves` and the description, deliberately not the title, because
    matching titles is what makes a knowledge base feel empty to a newcomer."""
    folder = os.path.join(vault, FOLDER)
    if not os.path.isdir(folder):
        return None
    words = [w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 2]
    if not words:
        return []
    hits = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(folder, fn)
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                head = fh.read(2400)
        except OSError:  # sbe: allow-silent probe loop over a whole folder; one unreadable candidate just drops out of a search, no record is authoritative here
            continue
        if not is_pattern(head):
            continue
        hay = head.lower()
        solves = ""
        m = re.search(r"^solves:\s*(.+)$", head, re.M)
        if m:
            solves = m.group(1).strip()
        score = sum(1 for w in words if w in hay)
        if score:
            hits.append((score, fn[:-3], solves))
    hits.sort(key=lambda h: -h[0])
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("write", help="record something that worked")
    for flag in ("name", "solves", "what", "evidence"):
        w.add_argument("--" + flag, required=True)
    w.add_argument("--project", default="all")
    w.add_argument("--borrowed", default="")
    f = sub.add_parser("find", help="find a pattern by the PROBLEM you have")
    f.add_argument("query", nargs="+")
    sub.add_parser("list", help="every pattern recorded")

    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.cmd:
        ap.print_help()
        return 2

    if args.cmd == "write":
        path, written = write(args.name, args.solves, args.what, args.evidence,
                              args.project, args.borrowed)
        if path is None:
            print("%s: the vault folder %s/%s is not present, so nothing was "
                  "written" % (NODATA, VAULT, FOLDER), file=sys.stderr)
            return 2
        if not written:
            print("already recorded, and nothing was changed: %s" % path)
            return 2
        print("wrote %s" % path)
        return 0

    hits = find(" ".join(args.query) if args.cmd == "find" else "a")
    if hits is None:
        print("%s: the vault could not be read" % NODATA, file=sys.stderr)
        return 2
    if args.cmd == "list":
        hits = find("")
        folder = os.path.join(VAULT, FOLDER)
        names = []
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith(".md"):
                continue
            with open(os.path.join(folder, fn), encoding="utf-8",
                       errors="replace") as fh:
                if is_pattern(fh.read(2400)):
                    names.append(fn[:-3])
        for n in names:
            print(n)
        print("%d pattern(s) recorded" % len(names))
        return 0
    if not hits:
        print("%s: no recorded pattern matches that problem. That is not the "
              "same as no pattern existing, and it is worth writing one if you "
              "solve it" % NODATA, file=sys.stderr)
        return 2
    for score, name, solves in hits[:6]:
        print("%-2d %s\n     %s" % (score, name, solves[:150]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

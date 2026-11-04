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

FOLDER = "50-Reference"
INDEX = "Patterns-Index.md"
NODATA = "NO-DATA"
SETTING = "BROTHERMODE_VAULT"


def _load_sibling(name):
    """(module, None) or (None, reason): a BrotherMode tool loaded by path from
    beside this file. The same file ships in three places, so the folder is
    looked up, never assumed: next to this file (products/brothermode/tools/
    and bundle/runtime/hooks/brothermode/tools/), the source checkout's tools
    folder (scripts/), or the installed runtime's hooks copy (bundle/runtime/).
    A missing or broken module is a reason the caller reports, never a guess."""
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.join(here, "..", "products", "brothermode", "tools"),
              os.path.join(here, "hooks", "brothermode", "tools")):
        path = os.path.join(d, name + ".py")
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, None
        except Exception as exc:  # sbe: allow-silent a module that cannot load is returned as a named reason; both callers turn it into a refusal or a NO-DATA line, never a fallback
            return None, "%s could not be loaded (%s: %s)" % (name, type(exc).__name__, exc)
    return None, "%s.py was not found beside %s" % (name, os.path.basename(__file__))


def vault_root():
    """(root, None) or (None, reason). The ONE answer to 'which vault?' is
    bm_vault.resolve_vault_root: an environment name, then the installer
    config's vault key, and no guessed folder of any kind. root None is a
    NO-DATA the caller reports; the reason names the setting to fix."""
    mod, why = _load_sibling("bm_vault")
    if mod is None:
        return None, "%s; set %s once it can" % (why, SETTING)
    res = mod.resolve_vault_root(None, os.environ, mod._config())
    if res["root"]:
        return res["root"], None
    if res["verdict"] == "NO-DATA":
        return None, ("no vault configured: set %s to your vault folder, or "
                      "bind one in bm_vault.json; no folder is guessed" % SETTING)
    return None, res["detail"]


def require_vault_root(explicit, tool):
    """The vault a shipped tool runs on: explicit (a --vault flag) when given,
    else vault_root()'s answer. Unresolved prints one '<tool>: NO-DATA: <reason>'
    line on stderr, the reason naming the setting to fix, and exits 2 before
    the caller reads or writes anything: no tool keeps a folder of its own to
    fall back on (KEY1.f). The line goes through bm_learning.say, so a value in
    the reason (an environment path) cannot open a second line."""
    if explicit:
        return explicit
    root, why = vault_root()
    if root:
        return root
    learning, _ = _load_sibling("bm_learning")
    if learning is not None:
        learning.say("%s: %s: %s" % (tool, NODATA, why), file=sys.stderr)
    else:
        print(NODATA + ": no vault root, and bm_learning.py could not be loaded "
              "to say why; set " + SETTING, file=sys.stderr)
    raise SystemExit(2)


def _load_intake_gate():
    """Load bm_vault_intake.py's hard_gate by path (a sibling tree this module
    never edits): the SAME credential and deny-list gate `admit` and `capture`
    run before writing, so a pattern note routes through the estate's one front
    door for vault content rather than being written ungated. Returns None when
    the module cannot be loaded, so the caller can fail closed rather than
    silently skip the gate."""
    mod, _ = _load_sibling("bm_vault_intake")
    return getattr(mod, "hard_gate", None)


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
    desc_parts = (what or "").strip().splitlines()
    # why: required --what '' must not IndexError; fall back to NODATA
    lines += [
        "description: %s" % (desc_parts[0][:200] if desc_parts else NODATA),
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


def write(name, solves, what, evidence, project="all", borrowed="", vault=None,
          receipt="", deny_list=None, gate=gate_text):
    """(path, written). Never overwrites: the vault constitution forbids editing
    an existing note, and a pattern that needs rewriting is a new pattern.

    Before the write, routes the combined text through `gate` (by default
    gate_text, the same hard gate `admit` and `capture` run in
    bm_vault_intake.py) so a pattern note can never carry a credential or a
    denied term into the vault. A gate refusal, same as a missing vault
    folder, writes nothing and reports (None, False); the refusal reason
    goes to stderr, never invented and never silently dropped.

    vault=None asks vault_root() (the one resolver); no configured vault is
    (None, False) like a missing folder, never a guessed one."""
    vault = vault or vault_root()[0]
    if not vault:
        return None, False
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


def find(query, vault=None):
    """Patterns whose PROBLEM matches the words somebody actually typed.

    Searches `solves` and the description, deliberately not the title, because
    matching titles is what makes a knowledge base feel empty to a newcomer.

    vault=None asks vault_root(); no configured vault is None (nothing was
    searched), the same sentinel as a missing folder."""
    vault = vault or vault_root()[0]
    if not vault:
        return None
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


def _no_data(why):
    """The one NO-DATA line main() prints on stderr; the exit code is 2."""
    print("%s: %s" % (NODATA, why), file=sys.stderr)
    return 2


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

    root, why = vault_root()
    if root is None:
        return _no_data(why)

    if args.cmd == "write":
        path, written = write(args.name, args.solves, args.what, args.evidence,
                              args.project, args.borrowed, vault=root)
        if path is None:
            return _no_data("the vault folder %s/%s is not present, so nothing "
                            "was written" % (root, FOLDER))
        if not written:
            print("already recorded, and nothing was changed: %s" % path)
            return 2
        print("wrote %s" % path)
        return 0

    hits = find(" ".join(args.query) if args.cmd == "find" else "a", root)
    if hits is None:
        print("%s: the vault could not be read" % NODATA, file=sys.stderr)
        return 2
    if args.cmd == "list":
        hits = find("", root)
        folder = os.path.join(root, FOLDER)
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

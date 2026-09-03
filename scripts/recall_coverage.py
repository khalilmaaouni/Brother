#!/usr/bin/env python3
"""recall_coverage: R28.3, learnings at the point of need, measured
(docs/plan/READINESS-ROADMAP-2026-08-29.json).

WHY THIS EXISTS. R28's own why_now: "tonight a ceremony instruction
overrode a privacy exclude because nothing mechanical stood in its way; a
law that lives only in prose is re-forgotten every compaction." The same is
true of a recorded LESSON, not just a law: this estate has two separate
lesson stores (~/.claude/repeat-guard/lessons.jsonl, matched at the moment
of action by ~/.claude/hooks/repeat_guard.py; and the Kay Vault memory index
at ~/.claude/projects/.../memory/MEMORY.md, injected only at session start).
Nobody had ever measured which lessons actually reach a session at the
moment the mistake is about to repeat, versus which ones only exist as
prose someone has to remember to reread.

THE REAL LOG SHAPE, read from the files themselves before writing this
parser (never invented): repeat_guard.py's PostToolUse handler writes one
JSON line per tool call to ~/.claude/repeat-guard/<session-id>.jsonl:
{"sig", "approach", "ok", "exit_code", "err"}. "approach" is the SAME
lower-cased, volatile-scrubbed text (repeat_guard.signature()'s `shown`)
that its PreToolUse handler matches every lesson's trigger against
(repeat_guard.matching_lessons(): `trigger in low`). Critically, the hook
NEVER PERSISTS THE FACT THAT A LESSON FIRED: the additionalContext line it
prints at match time goes to the transcript, not to any file this script
can read. So "fired" here is not read off a log field; it is RE-DERIVED, by
running repeat_guard's own trigger-matching rule against every persisted
"approach" across every session log. This is exact for Bash commands (their
approach IS the matchable text) and an UNDERCOUNT for Edit/Write/NotebookEdit
(their matchable text at match time also includes the edited body, which is
never persisted) -- stated here once rather than silently assumed away.

TWO LESSON STORES, ONE COVERAGE QUESTION. repeat_guard's own lessons.jsonl
entries carry a "trigger" and so CAN fire; the Kay Vault memory index
(MEMORY.md) entries carry no trigger field and no mechanism connects them to
repeat_guard at all, so they structurally CANNOT fire as repeat-guard
context no matter what a session does. A repeat-guard lesson whose trigger
never matched any logged approach is a third, honest case, UNMEASURED: it
has a guard, but no evidence either way was found in this log window.

NOT EVERY MEMORY IS GUARDABLE, and counting as if it were is how this
report overstated its own gap. Until 2026-09-02 every trigger-less memory
index entry landed in RECURRED UNGUARDED, which put a night-run mandate, a
benchmark finding and a founder score in the same column as a lesson about
a command someone is about to run again. A repeat-guard trigger fires on
the text of an action, so it can only ever guard a lesson ABOUT an action.
An ongoing-work entry is context a session reads at start; no trigger could
be written for it, and reporting it as a missing guard drives someone to
write dozens of triggers that could never match. So the trigger-less side
is split by the entry's OWN DECLARED TYPE, read from the file it links to
(YAML frontmatter, metadata: type:), never inferred from its wording and
never from a hard-coded list of filenames: type feedback is guidance about
how to work and is GUARDABLE, so its absence of a trigger is a real gap;
types project, reference and user are context and are reported separately,
listed in full rather than hidden. An entry whose declared type cannot be
read, or which declares a type outside that vocabulary, is NO-DATA: named
in its own bucket, counted in neither side, because on this estate NO-DATA
is never a pass and never silently folded into either answer.

Exit codes, this estate's convention: 0 the report printed (some lessons
fired, or none did but sources were readable); 2 NO-DATA, named, when the
session-log directory does not exist at all (nothing to measure firing
against) or neither lesson source is readable.

Python 3, standard library only. No network. No em or en dashes.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

HOME = os.path.expanduser("~")
DEFAULT_TRIGGER_LESSONS = os.path.join(HOME, ".claude", "repeat-guard", "lessons.jsonl")
DEFAULT_LOGS_DIR = os.path.join(HOME, ".claude", "repeat-guard")
DEFAULT_MEMORY_INDEX = os.path.join(
    HOME, ".claude", "projects", "-Users-khalil-maaouni-Brother", "memory", "MEMORY.md")

NODATA = "NO-DATA"

# The dash class below is written as unicode escapes (U+2013, U+2014), never
# as literal characters: the memory index's own bullets separate title from
# note with an em dash, so the pattern must match one, but this estate's own
# dash scan bans a literal em or en dash appearing anywhere in a pushed
# file, including inside a regex string.
_MEMORY_ROW_RE = re.compile(r"^-\s*\[(.+?)\]\(([^)]+)\)\s*[\u2013\u2014-]\s*(.*)$")

# The declared-type vocabulary of the memory store, read from each entry's own
# frontmatter. GUARDABLE is the one type a repeat-guard trigger could ever fire
# for: guidance about how to work, which is always about an action someone is
# about to take again. The others describe state, not an action, so no trigger
# text could match them; "user" sits with them because a fact about the founder
# is context a session reads, never a command about to be repeated.
GUARDABLE_TYPES = frozenset(["feedback"])
CONTEXT_TYPES = frozenset(["project", "reference", "user"])


def load_trigger_lessons(path):
    """[{id, trigger, note, source}, ...] from repeat-guard's own
    lessons.jsonl. A torn line is skipped, never a crash (the hook that
    writes this file fails open the same way)."""
    if not os.path.isfile(path):
        return None
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            trig = str(rec.get("trigger", "")).strip()
            if not trig:
                continue
            out.append({
                "id": trig,
                "trigger": trig,
                "note": str(rec.get("note", ""))[:200],
                "source": "repeat-guard",
            })
    return out


def read_declared_type(entry_path):
    """The entry's OWN declared type, read from its YAML frontmatter's
    `metadata:` block (`  type: feedback`), lower-cased. None when the file is
    unreadable, carries no frontmatter, or declares no type there.

    Only the frontmatter is opened, and only the type is taken: memory bodies
    are private and nothing from them enters this repository. Parsed with a
    two-line reader rather than a YAML dependency (standard library only), and
    the value is never guessed from the entry's wording."""
    if not entry_path or not os.path.isfile(entry_path):
        return None
    try:
        with open(entry_path, encoding="utf-8", errors="replace") as fh:
            first = fh.readline().rstrip("\n").strip()
            if first != "---":
                return None
            in_metadata = False
            for line in fh:
                line = line.rstrip("\n")
                if line.strip() == "---":
                    return None
                if not line.startswith((" ", "\t")):
                    in_metadata = line.strip().rstrip(":") == "metadata"
                    continue
                if in_metadata:
                    key, sep, value = line.strip().partition(":")
                    if sep and key.strip() == "type":
                        return value.strip().strip("\"'").lower() or None
    except OSError:  # sbe: allow-silent documented None-on-unreadable contract in this function's own docstring; classify() below routes it into its own named nodata bucket, never folded into fired or recurred
        return None
    return None


def resolve_entry_path(index_path, link):
    """Absolute path of the file a memory-index row links to. The link is a
    relative, URL-quoted markdown target (vault rows carry %20 for the space in
    the folder name), resolved against the index's own directory."""
    target = urllib.parse.unquote(link.strip())
    if not target or "://" in target:
        return None
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(index_path)), target))


def load_memory_lessons(path):
    """[{id, trigger, note, source, declared_type}, ...] from the Kay Vault
    memory index. trigger is always None: nothing connects these to
    repeat_guard, which is the finding this function exists to surface, not a
    bug to fix here. declared_type carries the entry's own frontmatter type,
    which decides whether that missing trigger is a real gap or an entry no
    trigger could ever have guarded."""
    if not os.path.isfile(path):
        return None
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _MEMORY_ROW_RE.match(line.rstrip("\n"))
            if not m:
                continue
            title, link, note = m.groups()
            out.append({
                "id": title.strip(),
                "trigger": None,
                "note": note.strip()[:200],
                "source": "memory-index (%s)" % link.strip(),
                "declared_type": read_declared_type(resolve_entry_path(path, link)),
            })
    return out


def load_logged_approaches(logs_dir, exclude_path):
    """Every "approach" string persisted by repeat_guard.py's PostToolUse
    handler, across every session log in logs_dir. Returns None if logs_dir
    itself does not exist (distinct from existing-but-empty)."""
    if not os.path.isdir(logs_dir):
        return None
    exclude_name = os.path.basename(exclude_path)
    approaches = []
    for fn in sorted(os.listdir(logs_dir)):
        if not fn.endswith(".jsonl") or fn == exclude_name:
            continue
        path = os.path.join(logs_dir, fn)
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    approach = rec.get("approach")
                    if approach:
                        approaches.append(str(approach).lower())
        except OSError as e:
            print("recall_coverage: skipping log %s, unreadable (%s); its approaches are "
                 "missing from this run's firing check" % (path, e), file=sys.stderr)
            continue
    return approaches


def classify(lessons, approaches):
    """(fired, recurred_unguarded, context, nodata, unmeasured), each a list
    of lesson dicts.

    fired: has a trigger AND that trigger matched at least one logged
    approach (repeat_guard.matching_lessons()'s own rule: `trigger in low`).
    Unchanged by the guardable split: what counts as fired is exactly what it
    was.
    recurred_unguarded: has NO trigger AND declares a GUARDABLE_TYPES type, so
    a trigger could have stood between the lesson and the moment of the
    mistake and none does. This is the honest gap.
    context: has no trigger and declares a CONTEXT_TYPES type. Nothing
    mechanical connects it either, but no trigger could ever have matched it,
    so it is not a missing guard. Listed, never hidden.
    nodata: has no trigger and no readable declared type, or declares one
    outside the vocabulary above. Counted in neither side, named on its own,
    because NO-DATA is never a pass on this estate.
    unmeasured: has a trigger, but it never matched anything in this log
    window; absence of evidence, reported as its own bucket rather than
    folded into any other.
    """
    fired, recurred, context, nodata, unmeasured = [], [], [], [], []
    for lesson in lessons:
        trig = lesson["trigger"]
        if trig is None:
            declared = lesson.get("declared_type")
            if declared in GUARDABLE_TYPES:
                recurred.append(lesson)
            elif declared in CONTEXT_TYPES:
                context.append(lesson)
            else:
                nodata.append(lesson)
            continue
        low = trig.lower()
        if any(low in a for a in approaches):
            fired.append(lesson)
        else:
            unmeasured.append(lesson)
    return fired, recurred, context, nodata, unmeasured


def _section(lines, heading, lessons, label):
    lines.append(heading)
    if lessons:
        for lesson in lessons:
            lines.append("  %s [%s] %s: %s"
                         % (label, lesson["source"], lesson["id"], lesson["note"]))
    else:
        lines.append("  (none)")
    lines.append("")


def render(fired, recurred, context, nodata, unmeasured, n_approaches):
    lines = []
    lines.append("recall_coverage: %d logged approach(es) scanned" % n_approaches)
    lines.append("fired=%d recurred-unguarded-guardable=%d context-not-guardable=%d "
                 "no-data-type-unreadable=%d unmeasured=%d"
                 % (len(fired), len(recurred), len(context), len(nodata), len(unmeasured)))
    lines.append("")
    _section(lines, "FIRED (trigger matched a logged approach at least once):",
             fired, "FIRED   ")
    _section(lines,
             "RECURRED (guardable, declared type in %s, and no repeat-guard "
             "trigger exists for it at all: the honest gap):"
             % "/".join(sorted(GUARDABLE_TYPES)),
             recurred, "RECURRED")
    _section(lines,
             "CONTEXT (declared type in %s: read at session start, not an "
             "action a trigger could ever match, so not a missing guard):"
             % "/".join(sorted(CONTEXT_TYPES)),
             context, "CONTEXT ")
    _section(lines,
             "%s (declared type unreadable or outside the vocabulary: counted "
             "in neither bucket above, never a pass):" % NODATA,
             nodata, "NO-DATA ")
    lines.append("UNMEASURED (has a trigger, never matched in this log window):")
    if unmeasured:
        for lesson in unmeasured:
            lines.append("  UNMEAS.  [%s] %s: %s" % (lesson["source"], lesson["id"], lesson["note"]))
    else:
        lines.append("  (none)")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trigger-lessons", default=DEFAULT_TRIGGER_LESSONS)
    ap.add_argument("--logs-dir", default=DEFAULT_LOGS_DIR)
    ap.add_argument("--memory-index", default=DEFAULT_MEMORY_INDEX)
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in fixture self-check and exit")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.selftest:
        demo()
        print("recall_coverage: selftest OK")
        return 0

    approaches = load_logged_approaches(args.logs_dir, args.trigger_lessons)
    if approaches is None:
        print("%s: session-log directory %s does not exist, nothing to "
              "measure firing against" % (NODATA, args.logs_dir))
        return 2

    trigger_lessons = load_trigger_lessons(args.trigger_lessons)
    memory_lessons = load_memory_lessons(args.memory_index)
    if trigger_lessons is None and memory_lessons is None:
        print("%s: neither lesson source readable (%s, %s)"
              % (NODATA, args.trigger_lessons, args.memory_index))
        return 2

    lessons = (trigger_lessons or []) + (memory_lessons or [])
    fired, recurred, context, nodata, unmeasured = classify(lessons, approaches)
    print(render(fired, recurred, context, nodata, unmeasured, len(approaches)), end="")
    return 0


def demo():
    """The ponytail self-check this module's branch logic leaves behind:
    proves every bucket lands from a fixture, never against real state on disk.

    The five-way split was added 2026-09-02. Before it, an entry with no
    trigger went straight to RECURRED whatever it was, so a night-run mandate
    and a scoring record counted as missing guards beside real lessons. That
    overstated the gap (77 against the honest 45) and would have driven
    somebody to write dozens of triggers that could never match anything.
    The two cases that matter most here are the last two: a context entry is
    NOT a missing guard, and an entry whose type cannot be read is NO-DATA
    rather than being folded into either side."""
    fixture_lessons = [
        {"id": "trigger-that-fires", "trigger": "git push --force", "note": "n1", "source": "t"},
        {"id": "trigger-that-never-matched", "trigger": "xyz-never-seen-token", "note": "n2", "source": "t"},
        {"id": "the wrong cwd lesson", "trigger": None, "note": "n3",
         "source": "memory-index", "declared_type": "feedback"},
        {"id": "tonight's run mandate", "trigger": None, "note": "n4",
         "source": "memory-index", "declared_type": "project"},
        {"id": "a pointer to a dashboard", "trigger": None, "note": "n5",
         "source": "memory-index", "declared_type": "reference"},
        {"id": "an entry whose type cannot be read", "trigger": None, "note": "n6",
         "source": "memory-index", "declared_type": None},
        {"id": "an entry declaring a word outside the vocabulary", "trigger": None,
         "note": "n7", "source": "memory-index", "declared_type": "banana"},
    ]
    fixture_approaches = ["git push --force origin main", "some other command"]
    fired, recurred, context, nodata, unmeasured = classify(
        fixture_lessons, fixture_approaches)
    assert [l["id"] for l in fired] == ["trigger-that-fires"], fired
    assert [l["id"] for l in recurred] == ["the wrong cwd lesson"], recurred
    assert [l["id"] for l in context] == ["tonight's run mandate",
                                          "a pointer to a dashboard"], context
    assert [l["id"] for l in nodata] == [
        "an entry whose type cannot be read",
        "an entry declaring a word outside the vocabulary"], nodata
    assert [l["id"] for l in unmeasured] == ["trigger-that-never-matched"], unmeasured
    # The rendered headline must carry every bucket, or the split is real in the
    # code and invisible to the reader, which is the failure this whole change
    # is about.
    head = render(fired, recurred, context, nodata, unmeasured, 2).splitlines()[1]
    for token in ("fired=1", "recurred-unguarded-guardable=1",
                  "context-not-guardable=2", "no-data-type-unreadable=2",
                  "unmeasured=1"):
        assert token in head, (token, head)


if __name__ == "__main__":
    sys.exit(main())

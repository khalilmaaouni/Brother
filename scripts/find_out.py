"""find_out.py: the research step attempt_ledger's refusal names by command.

THE GAP THIS CLOSES. attempt_ledger.py refuses a third attempt at a technique
class that has already failed twice, and its refusal names two moves: change
the class, or "stop guessing and go and find out". The second branch used to
be prose, an instruction to a human to go reread the ask and go find how
somebody else solved it. This is the tool behind that instruction: it reads
the estate's own stores of what it already knows and prints what matches the
problem, so "go find out" is a command somebody can actually run rather than
a chore left undone.

FOUR SOURCES, never a guess beyond them:

  1. the vault's failure notes (40-Failures/*.md) and the one-line summaries
     in 40-Failures/Failures-Index.md
  2. the vault's LEARNED.md, one hit per LESSON/RULE/BECAUSE block
  3. the pattern store behind scripts/pattern_note.py, via that module's own
     find(), never reimplemented here
  4. this machine's session memory index (one pointer line per memory file)

MATCHING mirrors scripts/pattern_note.py's own find(): content words shared
between the query and the candidate text, stopwords and short words dropped,
substring containment rather than a tokenizer, because that is the scoring
already standing in this repo rather than a second scheme invented beside it.
products/brothermode/tools/vault_recall_hook.py, the other point-of-need
recall path in this estate, does not itself score matches; it shells out to a
sqlite-and-embeddings index (bm_vault.py) that this stdlib-only tool has no
reason to depend on for a plain word-overlap search.

NO-DATA IS NEVER A FAKE ZERO. A source whose directory or file is missing or
unreadable prints "NO-DATA: <source> not found at <path>" and is never
silently counted as "nothing matched"; a real search that found nothing
prints its own "no match" line instead, which is a different, weaker claim.

Python 3, standard library only. No network.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pattern_note  # noqa: E402

VAULT = os.path.expanduser("~/Documents/Kay Vault")
MEMORY = os.path.expanduser(
    "~/.claude/projects/-Users-khalil-maaouni-Brother/memory/MEMORY.md")
NODATA = "NO-DATA"

#: Floor list, not a filter: cheap common words that would otherwise swamp
#: every query with a false-positive score of 1. Mirrors pattern_note.find's
#: length-3 floor, plus the words that floor alone lets through.
STOPWORDS = set("""
and are but for from had has have how its not out set the too was were what
when where which who why will with you your this that then than there here
about above after again against all any because been before being below
between both during each further into more most nor once only other over
own same some such under until while cannot could would should might must
shall does did doing them they she her him his hers itself myself yourself
""".split())


def _words(text):
    """Content words: split on non-word characters, lowercased, longer than
    two characters, stopwords dropped."""
    return [w for w in re.split(r"\W+", (text or "").lower())
            if len(w) > 2 and w not in STOPWORDS]


def _score(query_words, hay_text):
    """Substring containment count, same shape as pattern_note.find's own
    `score = sum(1 for w in words if w in hay)`."""
    hay = (hay_text or "").lower()
    return sum(1 for w in query_words if w in hay)


def _frontmatter_field(text, field):
    m = re.search(r'^%s:\s*"?(.+?)"?\s*$' % re.escape(field), text, re.M)
    return m.group(1) if m else ""


def vault_failures(query_words, vault_dir):
    """(hits or None). hits: list of (score, path, title), highest first.
    None means the folder is absent or unreadable, the NO-DATA case."""
    folder = os.path.join(vault_dir, "40-Failures")
    if not os.path.isdir(folder):
        return None
    hits = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:  # sbe: allow-silent explicit None sentinel, same NO-DATA contract as pattern_note.find and attempt_ledger.read
        return None
    for fn in names:
        if not fn.endswith(".md"):
            continue
        path = os.path.join(folder, fn)
        if fn == "Failures-Index.md":
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:  # sbe: allow-silent one candidate dropping out of a search, no record here is authoritative
                continue
            for n, line in enumerate(lines, 1):
                if not line.lstrip().startswith("- [["):
                    continue
                sc = _score(query_words, line)
                if sc:
                    hits.append((sc, "%s:%d" % (path, n), line.strip()[:150]))
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = "".join(fh.readlines()[:60])
        except OSError:  # sbe: allow-silent one candidate dropping out of a search, no record here is authoritative
            continue
        title = _frontmatter_field(head, "description") or fn[:-3]
        sc = _score(query_words, head)
        if sc:
            hits.append((sc, path, title[:150]))
    hits.sort(key=lambda h: -h[0])
    return hits


def _learned_blocks(text):
    """One block per LESSON: line, extended through its immediately
    following RULE: and BECAUSE: lines (any leading indent)."""
    lines = text.split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("LESSON:"):
            block = [lines[i]]
            j = i + 1
            while j < len(lines) and (lines[j].strip().startswith("RULE:")
                                       or lines[j].strip().startswith("BECAUSE:")):
                block.append(lines[j])
                j += 1
            blocks.append("\n".join(block))
            i = j
        else:
            i += 1
    return blocks


def vault_learned(query_words, vault_dir):
    """(hits or None). hits: list of (score, path, first line of the block)."""
    path = os.path.join(vault_dir, "LEARNED.md")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:  # sbe: allow-silent explicit None sentinel, same NO-DATA contract as pattern_note.find and attempt_ledger.read
        return None
    hits = []
    for block in _learned_blocks(text):
        sc = _score(query_words, block)
        if sc:
            first = block.strip().splitlines()[0].strip()
            hits.append((sc, path, first[:150]))
    hits.sort(key=lambda h: -h[0])
    return hits


def patterns(query, patterns_dir):
    """(hits or None), via pattern_note.find's own scoring, never
    reimplemented here."""
    found = pattern_note.find(query, vault=patterns_dir)
    if found is None:
        return None
    return [(score, os.path.join(patterns_dir, pattern_note.FOLDER, name + ".md"),
             "%s :: %s" % (name, solves[:120])) for score, name, solves in found]


def memory_index(query_words, memory_path):
    """(hits or None). hits: list of (score, memory_path, pointer title)."""
    if not os.path.isfile(memory_path):
        return None
    try:
        with open(memory_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:  # sbe: allow-silent explicit None sentinel, same NO-DATA contract as pattern_note.find and attempt_ledger.read
        return None
    hits = []
    for line in lines:
        s = line.strip()
        if not s.startswith("- ["):
            continue
        sc = _score(query_words, s)
        if sc:
            m = re.match(r"-\s*\[([^\]]+)\]", s)
            title = m.group(1) if m else s
            hits.append((sc, memory_path, title[:150]))
    hits.sort(key=lambda h: -h[0])
    return hits


def _print_source(name, hits, path_for_nodata, top):
    print("== %s ==" % name)
    if hits is None:
        print("%s: %s not found at %s" % (NODATA, name, path_for_nodata))
        return 0
    if not hits:
        print("no match in %s at %s" % (name, path_for_nodata))
        return 0
    for score, path, title in hits[:top]:
        print("%d  %s  %s" % (score, path, title))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("problem", help="the problem, in plain words")
    ap.add_argument("--vault", default=VAULT)
    ap.add_argument("--patterns", default=VAULT)
    ap.add_argument("--memory", default=MEMORY)
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    words = _words(args.problem)

    answered = 0
    answered += _print_source(
        "vault failures", vault_failures(words, args.vault),
        os.path.join(args.vault, "40-Failures"), args.top)
    answered += _print_source(
        "vault learned", vault_learned(words, args.vault),
        os.path.join(args.vault, "LEARNED.md"), args.top)
    answered += _print_source(
        "patterns", patterns(args.problem, args.patterns),
        os.path.join(args.patterns, pattern_note.FOLDER), args.top)
    answered += _print_source(
        "memory index", memory_index(words, args.memory),
        args.memory, args.top)

    print("%d of 4 source(s) answered." % answered)
    return 0 if answered else 2


if __name__ == "__main__":
    sys.exit(main())

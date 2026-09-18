"""doc_numbers_check.py: every number a document claims is checked against
evidence, never trusted because the prose reads confidently.

WHY THIS EXISTS. JEV-05 asks Codex to write a guide whose every number is
"verified by the orchestrator against the scorer's output". A human re-reading
the guide beside the scorer output can miss one figure among dozens; this
script cannot, because it checks every number-bearing token mechanically and
refuses (exit 1) on the first one it cannot find, never on a sample.

WHAT COUNTS AS A NUMBER, on purpose narrow: a percentage (92%), a dollar
amount ($0.000022 or 0.000022 USD), a count of a count (23 of 24), a decimal
(0.9) and a plain integer of two or more digits. A single digit (a list
marker, "1.") is not checked: it is too common and too rarely a claim.

WHAT IS DELIBERATELY IGNORED: a code fence's contents (a command's own flags
are not a prose claim) and the date line (a line starting "Date:", which
bundles a document's own date with unrelated provenance numbers that are not
claims about measured evidence either).

EVIDENCE COMES FROM THE DOC ITSELF, by default: a line starting "Evidence:"
is read for path-like tokens, each resolved relative to the repository root.
A directory there is walked for every file under it. --evidence adds more.
This mirrors scripts/release_note_from_tree.py's refusal to let a document
carry a claim nothing in the tree backs: NO-DATA is never a pass here either,
so a document that names no evidence, or whose evidence cannot be read,
refuses (exit 2) rather than silently checking nothing and calling that green.

A number's dollar sign and trailing " USD" are normalised away only when
comparing against evidence (spec: "normalised for a leading $ or a trailing
' USD'"); the number is still reported in the form the document wrote it.

Python 3, standard library only. No network.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FENCE_RE = re.compile(r'^\s*```')
DATE_LINE_RE = re.compile(r'^\s*Date:\s', re.IGNORECASE)
EVIDENCE_LINE_RE = re.compile(r'^\s*Evidence:\s', re.IGNORECASE)
PATH_TOKEN_RE = re.compile(
    r'[A-Za-z0-9_.\-]+/[A-Za-z0-9_./\-]*'   # a token with a directory: a/b, a/b/
    r'|[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,6}\b'  # a bare filename: README.md
)

# Alternatives tried in order at each position: the more specific shape wins
# over the generic one it would otherwise be swallowed into ("23 of 24" over
# a bare "23", "92%" over a bare "92", "0.000022 USD" over a bare "0.000022").
NUMBER_RE = re.compile(
    r'\d+\s+of\s+\d+'                # count of count: 23 of 24
    r'|\$\d+(?:\.\d+)?(?:\s?USD)?'   # dollar amount: $0.000022, $0.000022 USD
    r'|\d+(?:\.\d+)?\s?USD'          # amount USD: 0.000022 USD
    r'|\d+(?:\.\d+)?%'               # percentage: 92%, 0.5%
    r'|\d+\.\d+'                     # decimal: 0.9
    r'|\d{2,}'                       # plain integer, 2+ digits
)


def normalize_number(token):
    """Strip a leading $ and a trailing USD for the evidence comparison only;
    the token reported to the user keeps its original spelling."""
    t = token
    if t.startswith("$"):
        t = t[1:]
    if t.endswith("USD"):
        t = t[:-3].rstrip()
    return t


def extract_numbers(text):
    """Returns [(lineno, token), ...] for every number-bearing token in
    text's prose, skipping code fences and the date line."""
    numbers = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if DATE_LINE_RE.match(line):
            continue
        for m in NUMBER_RE.finditer(line):
            numbers.append((lineno, m.group(0)))
    return numbers


def parse_evidence_paths(text):
    """Returns path tokens (relative to repo root) named on the doc's own
    "Evidence:" line, or [] if the doc carries no such line."""
    for line in text.splitlines():
        if EVIDENCE_LINE_RE.match(line):
            rest = line.split(":", 1)[1]
            # A sentence's closing punctuation is not part of the path: an
            # Evidence line ending "...STATUS.json." silently lost that file
            # (found by the orchestrator, 2026-09-18).
            return [t.rstrip(".,;:") for t in PATH_TOKEN_RE.findall(rest)]
    return []


def gather_evidence(paths, root):
    """Resolves each path (relative to root) into readable file contents.
    A directory is walked for every file under it. Returns
    (contents: {relpath: text}, unreadable: [path, ...]) where unreadable
    names every input path that resolved to nothing readable."""
    contents = {}
    unreadable = []
    for p in paths:
        full = os.path.join(root, p)
        if os.path.isdir(full):
            found_file = False
            for dirpath, _dirnames, filenames in os.walk(full):
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                            contents[os.path.relpath(fp, root)] = fh.read()
                        found_file = True
                    except OSError:
                        continue
            if not found_file:
                unreadable.append(p)
        elif os.path.isfile(full):
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    contents[p] = fh.read()
            except OSError:
                unreadable.append(p)
        else:
            unreadable.append(p)
    return contents, unreadable


def check_numbers(numbers, corpus):
    """corpus is the concatenation of every readable evidence file's text.
    Returns (found_count, unfound: [(lineno, token), ...])."""
    unfound = []
    found = 0
    for lineno, token in numbers:
        if token in corpus or normalize_number(token) in corpus:
            found += 1
        else:
            unfound.append((lineno, token))
    return found, unfound


def run(doc_path, evidence_args, root):
    """Returns (exit_code, lines). 0 PASS, 1 FAIL, 2 NO-DATA."""
    try:
        with open(doc_path, "r", encoding="utf-8", errors="replace") as fh:
            doc_text = fh.read()
    except OSError as exc:
        return 2, ["NO-DATA: cannot read %s: %s" % (doc_path, exc)]

    numbers = extract_numbers(doc_text)
    if not numbers:
        return 2, ["NO-DATA: %s carries zero number-bearing tokens to check."
                   % doc_path]

    evidence_paths = parse_evidence_paths(doc_text) + list(evidence_args)
    contents, unreadable = gather_evidence(evidence_paths, root)
    if not contents:
        if evidence_paths:
            return 2, ["NO-DATA: every evidence path is unreadable: %s"
                       % ", ".join(unreadable)]
        return 2, ["NO-DATA: %s names no evidence (no \"Evidence:\" line, no "
                   "--evidence given)." % doc_path]

    corpus = "\n".join(contents.values())
    found, unfound = check_numbers(numbers, corpus)

    lines = ["evidence: %d file(s) read" % len(contents)]
    if unreadable:
        lines.append("(unreadable, skipped: %s)" % ", ".join(unreadable))
    if unfound:
        for lineno, token in unfound:
            lines.append("line %d: %r not found in evidence" % (lineno, token))
        lines.append("FAIL: %d of %d number(s) not found in evidence."
                     % (len(unfound), len(numbers)))
        return 1, lines

    lines.append("PASS: %d number(s) verified against evidence." % found)
    return 0, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("doc", help="the document to check")
    ap.add_argument("--evidence", action="append", default=[],
                    help="extra evidence file or directory, repeatable")
    ap.add_argument("--root", default=ROOT,
                    help="repository root evidence paths resolve against")
    args = ap.parse_args(argv)
    code, lines = run(args.doc, args.evidence, args.root)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

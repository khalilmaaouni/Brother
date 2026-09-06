#!/usr/bin/env python3
"""BrotherMode gate deep-dive packs: one document an engineer can review.

WHAT THIS IS FOR
  A founder gate asks a human to approve a decision. Today that human sees a
  sentence. This writes the rest: what the decision is, what happens if it is
  wrong, what the options were, the CODE that would change quoted live from
  disk, everyone who depends on those lines, the blast radius and the rollback
  command, what the store already recorded about this area, a diagram of the
  affected path, and the slots where a named engineer records a verdict.

  Phase A of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
  -design.md, section 4. Nothing is generated until asked: `stakes` prints the
  one line a question window carries, `pack` writes the document.

THE THREE RULES THIS FILE LIVES BY
  I11, A CITED EXCERPT MUST RESOLVE. Every excerpt is re-read from disk at
  generation time and checked against the anchor text and content hash recorded
  the last time the pack was written. A citation that no longer resolves is a
  LOUD failure with a stated remedy (--recite), never a quietly stale quote. A
  pack whose code section is fiction is worse than no pack: it is a review of
  something that is not there.

  I10, GENERATED OUTPUT NEVER DESTROYS HUMAN TEXT. Anything a human wrote
  between the human markers is carried through regeneration byte for byte.

  DISCOVERED, NOT ASSERTED. The dependency map comes from searching the tree
  for callers, tests and documents. Nothing in it is remembered or guessed, and
  the search that found each hit is printed with it.

WHAT THIS FILE MAY NOT DO
  It never writes the database directly: every store mutation goes through
  bm_store.py, which stays the single writer, and the pack file itself is
  written through bm_store.write_generated_document, the one funnel that
  redacts before anything reaches disk. No network, no subprocess.

WHAT IT DELIBERATELY DOES NOT DO
  It does not write prose about the decision that the store did not record. The
  paragraphs are assembled from recorded fields; where a field is empty the pack
  says the field is empty and names the command that would fill it. A generator
  that invents a rationale is a generator that puts words in the founder's
  mouth.

  It does not render a candidate's raw captured text. That is the most sensitive
  column in the store, withheld here exactly as `show-candidate` withholds it.

Python 3.9, standard library only.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same way bm_learn.py does: this file
    is invoked from arbitrary working directories and must not depend on
    whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
L = _load("bm_learning")

# Where a pack lives. Numbered directory from section B.1 of the spec, so phase
# B's documentation engine finds the packs already in the place its own INDEX.md
# will point at.
PACK_DIRS = ("Documentation", "30-decisions")

# The eight required sections, in the order section A.1 states. Named here so
# the renderer, the CLI's own report and the suite all read one list: a section
# that goes missing shows up as a missing heading rather than as a paragraph
# nobody notices is gone.
SECTIONS = (
    "The decision",
    "Options",
    "The code",
    "Dependency map",
    "Risks",
    "What the store already knows",
    "Diagram",
    "Review",
)

# The human block markers come from bm_store, which owns them because the file
# funnel has to see them to protect what is between them (I10). One definition,
# so this file and the funnel can never disagree about where human text begins.
HUMAN_BEGIN = bs.HUMAN_BLOCK_BEGIN
HUMAN_END = bs.HUMAN_BLOCK_END

# The machine-readable citation record. Written by the generator, re-read by the
# generator. This is what makes I11 enforceable across runs: without a recorded
# anchor there is nothing for a moved line to disagree with.
_CITE_RE = re.compile(
    r"^<!-- bm-cite: path=(?P<path>\S+) lines=(?P<start>\d+)-(?P<end>\d+) "
    r"sha256=(?P<sha>[0-9a-f]{64}) anchor=(?P<anchor>.*) -->$")

# Files worth searching for a dependency. Text only, and an explicit list rather
# than "anything that decodes": a search that wanders into a sqlite file or a
# tarball reports noise and takes a minute doing it.
_SEARCHABLE = (".py", ".md", ".sh", ".json", ".yml", ".yaml", ".toml", ".cfg",
               ".txt")
_SKIP_DIRS = frozenset((".git", ".brothermode", "node_modules", "__pycache__",
                        ".venv", "venv", ".mypy_cache", ".pytest_cache"))
# A file bigger than this is not a caller, it is data. Bounded so `pack` stays a
# command a founder runs at a question window rather than a batch job.
_MAX_SEARCH_BYTES = 400000
# How many hits of each kind the pack lists in full. The COUNT is always exact;
# this caps the listing so one popular module does not produce a hundred-line
# section nobody reads.
_MAX_LISTED = 12

# The keys of learning_loop_failures() that really are failures. Named rather
# than derived, because that report also carries window sizes and tallies.
_FAILURE_KEYS = ("repeated_settled_corrections", "retrieval_misses",
                 "compliance_failures", "bad_rule_candidates", "scope_errors",
                 "unresolved_contradictions", "rules_never_retrieved",
                 "rules_always_not_relevant", "unattributed_outcomes")


class PackError(Exception):
    """A refusal with a reason a human can act on. Carries `reason` in the same
    shape bm_store.OwnershipRefused does, so the CLI prints both alike."""

    def __init__(self, reason, message):
        Exception.__init__(self, message)
        self.reason = reason


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _parse(argv, known, wants_value=(), repeatable=()):
    """Flags into a dict, REFUSING anything unrecognized, exactly as
    bm_learn.py's parser does. A repeatable flag collects a list."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_packs: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value or name in repeatable:
                if i + 1 >= len(argv):
                    _err("bm_packs: --%s needs a value" % name)
                    sys.exit(2)
                if name in repeatable:
                    kv.setdefault(name, []).append(argv[i + 1])
                else:
                    kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _root():
    root, _source = bs.require_root()
    return root


def _store():
    return bs.Store(_root(), create=False)


# ---------------------------------------------------------------------------
# Citations: parse, resolve, and refuse loudly (I11).
# ---------------------------------------------------------------------------

def _cite_key(path, start, end):
    return "%s:%d-%d" % (path, start, end)


def parse_cite(spec):
    """"path:start-end" or "path:start-end@anchor text" into a citation dict.

    The anchor is OPTIONAL on the command line and MANDATORY in the file: when
    the caller does not give one, the text of the start line becomes the anchor
    and is recorded, so the next run has something a moved line can fail
    against. Giving one explicitly makes the very first generation fail if it
    does not match, which is the cheap way to prove a line number was typed
    from memory."""
    text, sep, anchor = (spec or "").partition("@")
    path, colon, span = text.rpartition(":")
    if not colon or not path.strip():
        raise PackError(
            "bad-citation",
            "a citation is path:start-end, optionally @anchor text. Got %r" % spec)
    lo, dash, hi = span.partition("-")
    if not dash:
        hi = lo
    try:
        start, end = int(lo), int(hi)
    except ValueError:
        raise PackError(
            "bad-citation",
            "citation line range must be numbers, got %r in %r" % (span, spec))
    if start < 1 or end < start:
        raise PackError(
            "bad-citation",
            "citation range %d-%d makes no sense; lines start at 1 and end "
            "must not precede start" % (start, end))
    return {"path": path.strip().replace("\\", "/"), "start": start, "end": end,
            "anchor": anchor.strip() if sep else "", "sha": ""}


def _read_lines(root, rel):
    full = bs.safe_project_path(root, *rel.split("/"))
    if not os.path.isfile(full):
        raise PackError(
            "citation-file-missing",
            "cited file %s does not exist. A pack may not quote a file that is "
            "not there: fix the citation or restore the file." % rel)
    with io.open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read().split("\n")


def resolve_citation(root, cite):
    """Re-read the file and return the citation with its live text, or RAISE.

    This is I11 in one function. Three ways it refuses, and all three are the
    same refusal from a reader's point of view (what you are about to read is
    not what is on disk):
      * the file is gone;
      * the range runs off the end of the file;
      * the recorded anchor is no longer on the start line.

    The content hash is recorded and compared as well, so an edit INSIDE the
    cited range that leaves the anchor line alone is reported too. It is
    reported as a CHANGE rather than as a move, with a different remedy: an
    excerpt whose body changed needs a human to re-read it, not a line number
    bumped."""
    lines = _read_lines(root, cite["path"])
    # A trailing newline makes split produce one empty final element, which is
    # not a line anybody can cite.
    height = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    if cite["end"] > height:
        raise PackError(
            "citation-out-of-range",
            "%s has %d lines; the pack cites %d-%d. The file shrank under the "
            "citation. Re-read it and re-cite with --recite %s:<new-start>-"
            "<new-end>@<anchor>."
            % (cite["path"], height, cite["start"], cite["end"], cite["path"]))
    body = "\n".join(lines[cite["start"] - 1:cite["end"]])
    live_anchor = lines[cite["start"] - 1].strip()
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    out = dict(cite)
    out["body"] = body
    out["live_anchor"] = live_anchor
    if cite["anchor"] and cite["anchor"] not in lines[cite["start"] - 1]:
        found = _find_anchor(lines, cite["anchor"])
        where = ("It is now at line %d." % found) if found else \
            "It is nowhere in the file any more."
        raise PackError(
            "citation-moved",
            "%s line %d no longer starts with the cited anchor %r. %s A pack "
            "that quoted it anyway would be reviewing code that is not there. "
            "Re-run with --recite %s:%s-%s@%s once you have read the new "
            "lines."
            % (cite["path"], cite["start"], cite["anchor"], where,
               cite["path"], found or cite["start"],
               (found or cite["start"]) + (cite["end"] - cite["start"]),
               cite["anchor"]))
    if cite["sha"] and cite["sha"] != sha:
        raise PackError(
            "citation-changed",
            "%s lines %d-%d still start with the cited anchor but their "
            "content changed since this pack was written. Read the new text "
            "and re-cite with --recite %s:%d-%d@%s to record that you did."
            % (cite["path"], cite["start"], cite["end"], cite["path"],
               cite["start"], cite["end"], cite["anchor"] or live_anchor))
    out["sha"] = sha
    if not out["anchor"]:
        out["anchor"] = live_anchor
    return out


def _find_anchor(lines, anchor):
    """1-based line number of the first line containing `anchor`, or None. Used
    only to make a refusal actionable: it never silently relocates a citation,
    because moving a citation is a decision a human makes after reading."""
    for i, line in enumerate(lines, 1):
        if anchor in line:
            return i
    return None


# ---------------------------------------------------------------------------
# The existing pack: human blocks and recorded citations.
# ---------------------------------------------------------------------------

def read_existing(path):
    """Parse an existing pack for the two things regeneration must not lose:
    the human blocks, verbatim and in order, and the recorded citations.

    Returns {"human": [str, ...], "cites": [dict, ...]} and never raises on a
    malformed file: an unparseable pack is regenerated, and the human blocks
    are recovered by marker scan, which is the one part that must survive a
    file somebody hand-edited.

    THE MARKERS ARE READ FIRST, AND A CITATION IS ONLY READ OUTSIDE THEM. This
    used to be one scan of every line for a bm-cite comment, with no regard for
    where the line was, which made human prose executable as machine
    configuration: a reviewer who pasted an excerpt header into the block the
    file TELLS them to write in silently added a citation to the pack. A
    resolving one appeared in section 3 as a generated excerpt nobody had asked
    for; a non-resolving one wedged every future regeneration at exit 2, and the
    only way out was to edit the human block I10 says must be preserved.
    Reviewers quote code. That has to be safe.

    A second begin marker inside a block is CONTENT, not a nested block, for the
    same reason: the old scan reset its buffer on it and dropped every line
    written before it."""
    out = {"human": [], "cites": []}
    if not os.path.isfile(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    inside = 0
    buf = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == HUMAN_BEGIN and not inside:
            inside = 1
            buf = []
            continue
        if stripped == HUMAN_END and inside:
            inside = 0
            out["human"].append("\n".join(buf))
            buf = []
            continue
        if inside:
            buf.append(line)
            continue
        m = _CITE_RE.match(stripped)
        if m:
            out["cites"].append({
                "path": m.group("path"), "start": int(m.group("start")),
                "end": int(m.group("end")), "sha": m.group("sha"),
                "anchor": _unquote_anchor(m.group("anchor"))})
    if inside and buf:
        # An unterminated human block. Kept, because losing a paragraph because
        # somebody forgot the closing marker is exactly the destruction I10
        # forbids. The regenerated file closes it.
        out["human"].append("\n".join(buf))
    return out


def _quote_anchor(anchor):
    """Anchor text into ONE line that _CITE_RE can read back. json.dumps gives
    escaping for free, including the newlines and quotes a source line can
    contain, and it round-trips."""
    return json.dumps(anchor, ensure_ascii=False)


def _unquote_anchor(raw):
    try:
        val = json.loads(raw)
    except ValueError:
        return raw.strip().strip('"')
    return val if isinstance(val, str) else raw


def merge_citations(recorded, added, recited):
    """The citation set for THIS generation.

    Order of precedence, and the reason for it:
      * a --recite replaces a recorded citation for the same path, keeping the
        pack pointing at the same excerpt after a legitimate move;
      * a --cite adds one, or replaces a recorded one at the same range;
      * everything else recorded stays, so regeneration without arguments
        re-checks exactly what the pack already claims.

    RECORDED ROWS ARE DEDUPED BY KEY, and that is a fix, not a tidy-up. This
    function used to index seen[key] while appending EVERY recorded row, so two
    rows could share path:start-end while only the first was reachable by key.
    The --recite loop then replaced the first match and stopped, the unreachable
    twin kept its stale hash, and the pack refused forever with a remedy printed
    on the refusal that could not work. Two rows claiming the same range are one
    citation; the first one wins, because that is the one the generator wrote.
    """
    out = []
    seen = {}
    for c in recorded:
        key = _cite_key(c["path"], c["start"], c["end"])
        if key in seen:
            continue
        seen[key] = len(out)
        out.append(dict(c))
    for c in added:
        key = _cite_key(c["path"], c["start"], c["end"])
        if key in seen:
            # Same range cited again: keep the caller's anchor, drop the
            # recorded hash so an explicit re-cite is not refused by the hash of
            # the text it is replacing.
            out[seen[key]] = dict(c)
        else:
            seen[key] = len(out)
            out.append(dict(c))
    for c in recited:
        key = _cite_key(c["path"], c["start"], c["end"])
        if key in seen:
            # The remedy the refusal prints names the exact range, so this is
            # the path a founder following the printed instruction takes.
            out[seen[key]] = dict(c)
            continue
        same_path = [i for i, existing in enumerate(out)
                     if existing["path"] == c["path"]]
        if len(same_path) == 1:
            # The excerpt MOVED: one citation in that file, new line numbers.
            out[same_path[0]] = dict(c)
            seen[key] = same_path[0]
            continue
        # Either nothing cited that file yet, or several ranges in it and no way
        # to know which one moved. Added rather than guessed: replacing an
        # arbitrary one of several would silently retarget a review at code
        # nobody chose.
        seen[key] = len(out)
        out.append(dict(c))
    return out


# ---------------------------------------------------------------------------
# The dependency map, DISCOVERED.
# ---------------------------------------------------------------------------

_DEF_RE = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


def cited_symbols(cite):
    """Function and class names defined inside a cited range. These are the
    strongest search terms available: a module name finds importers, a symbol
    name finds actual callers."""
    names = []
    for line in cite.get("body", "").split("\n"):
        m = _DEF_RE.match(line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def search_terms(cites):
    """Terms to search for, each with the reason it is being searched. Printed
    beside the results so a reader can re-run the same search by hand."""
    terms = []
    seen = set()

    def _add(term, why):
        if term and term not in seen:
            seen.add(term)
            terms.append({"term": term, "why": why})

    for c in cites:
        base = c["path"].rsplit("/", 1)[-1]
        _add(base, "the cited file, by name")
        if base.endswith(".py"):
            _add(base[:-3], "the cited module, as an import or an attribute")
        for name in cited_symbols(c):
            _add(name, "a symbol defined in the cited lines")
    return terms


def discover_dependencies(root, cites, exclude=()):
    """Walk the tree and record every file that mentions a search term.

    Classified by what the file IS, because the three classes carry different
    weight for a reviewer: a caller may break, a test tells you whether it did,
    and a document goes stale silently."""
    terms = search_terms(cites)
    # The pack being written is excluded. It quotes the cited file, so a second
    # run would find the document as a dependency of the code it documents, list
    # it, and change the file it just wrote: regeneration would churn forever and
    # every review would open on a diff that means nothing.
    cited_paths = set(c["path"] for c in cites) | set(exclude)
    hits = {"code": [], "tests": [], "docs": []}
    if not terms:
        return {"terms": terms, "hits": hits, "scanned": 0}
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS
                             and not d.startswith("."))
        for name in sorted(filenames):
            if not name.endswith(_SEARCHABLE):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            # Skipped BEFORE the read, so the reported count of files scanned is
            # stable across regeneration: counting a file the search then ignores
            # made the pack's own existence change the pack's own text.
            if rel in cited_paths:
                continue
            try:
                if os.path.getsize(full) > _MAX_SEARCH_BYTES:
                    continue
                with io.open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as e:
                # Reported, never swallowed: a file the search could not read is
                # a hole in the dependency map and the pack says so.
                hits["code"].append({"path": rel, "line": 0, "term": "",
                                     "note": "unreadable: %s" % e})
                continue
            scanned += 1
            matched = []
            first_line = 0
            for t in terms:
                pos = text.find(t["term"])
                if pos < 0:
                    continue
                if not matched:
                    first_line = text.count("\n", 0, pos) + 1
                matched.append(t["term"])
            if not matched:
                continue
            # EVERY term this file matched, not the first one. Matching only the
            # first meant a file that mentions the module name was never
            # reported as a caller of the SYMBOL, which is the stronger fact and
            # the one a reviewer is actually looking for.
            bucket = "docs" if rel.endswith(".md") else (
                "tests" if name.startswith("test_") or "/test" in rel
                else "code")
            hits[bucket].append({"path": rel, "line": first_line,
                                 "term": ", ".join(matched), "note": ""})
    return {"terms": terms, "hits": hits, "scanned": scanned}


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def _d(text, limit=300):
    """Every store-derived string that reaches the document passes through here:
    control characters stripped, length capped, exactly as the CLI does it. A
    newline inside a rule action could otherwise forge a markdown heading, and a
    heading is structure this file is writing on purpose."""
    return L.safe_display(text or "", limit)


def _slug(text, limit=40):
    flat = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not flat:
        flat = "decision"
    return flat[:limit].rstrip("-")


def pack_index(store, cand):
    """The stable number in D-<n>-<slug>.md: this candidate's position among
    all candidates, oldest first. Derived from the store rather than from the
    files on disk, so two people generating packs in a different order get the
    same number for the same decision."""
    rows = store.conn.execute(
        "SELECT candidate_uuid FROM learning_candidates "
        "ORDER BY created_at, candidate_uuid").fetchall()
    for i, row in enumerate(rows, 1):
        if row["candidate_uuid"] == cand["candidate_uuid"]:
            return i
    return len(rows) + 1


def pack_path(root, store, cand):
    """Absolute path of this candidate's pack, reusing an existing file for the
    same number even when the slug has since changed: a renamed pack would
    orphan the review notes a human wrote in the old one."""
    n = pack_index(store, cand)
    directory = bs.safe_project_path(root, *PACK_DIRS)
    prefix = "D-%d-" % n
    if os.path.isdir(directory):
        existing = sorted(f for f in os.listdir(directory)
                          if f.startswith(prefix) and f.endswith(".md"))
        if len(existing) == 1:
            return os.path.join(directory, existing[0]), n
    name = "%s%s.md" % (prefix, _slug(cand["proposed_action"]
                                     or cand["proposed_trigger"]))
    return os.path.join(directory, name), n


def stakes_line(store, cand, alerts):
    """The one line a question window carries (spec A.2): what is at stake, and
    where the pack would land. No pack is generated to produce it."""
    scope = L.safe_scope(cand["proposed_scope_type"], cand["proposed_scope_key"])
    changed = store.gate_change_set(cand)
    bits = ["candidate %s in scope %s" % (cand["candidate_uuid"][:8], scope)]
    bits.append("%d file(s) in its declared change set" % len(changed))
    if alerts:
        bits.append("%d UNRESOLVED CRITICAL ALERT(S), which will refuse the "
                    "approval" % len(alerts))
    return "; ".join(bits)


def render(root, store, cand, cites, deps, notes, alerts, human_blocks,
           pack_rel, changed):
    """The whole document. Eight sections, in the order section A.1 states."""
    lines = []
    w = lines.append
    # The store's own anchor report, so this pack and the documentation folder
    # cannot disagree about whether a note still points at the line it was
    # written about (spec section 6).
    anchors = {r["note_uuid"]: r for r in store.note_anchor_reports(notes)}
    scope = L.safe_scope(cand["proposed_scope_type"], cand["proposed_scope_key"])
    w("<!-- GENERATED by tools/bm_packs.py. Regenerate rather than hand-edit, "
      "except inside the human markers, which are preserved verbatim. -->")
    w("")
    w("# D-%s: %s" % (pack_rel.rsplit("D-", 1)[-1].split("-", 1)[0],
                      _d(cand["proposed_action"] or "an unnamed decision", 120)))
    w("")
    w("Candidate `%s`, status `%s`, captured %s, scope `%s`."
      % (cand["candidate_uuid"][:8], cand["status"], cand["created_at"], scope))
    w("Generated from the store and from the files as they are on disk right "
      "now. Every code excerpt below was re-read at generation time.")
    w("")

    # 1
    w("## 1. %s" % SECTIONS[0])
    w("")
    w(_decision_paragraph(cand, scope))
    w("")
    w("**If this is wrong.** %s" % _if_wrong(cand, deps, alerts))
    w("")

    # 2
    w("## 2. %s" % SECTIONS[1])
    w("")
    for opt in _options(store, cand):
        w("### %s" % opt["title"])
        w("")
        w(opt["body"])
        w("")
        w("- Trade-off: %s" % opt["tradeoff"])
        w("")
    w("**Recommendation.** %s" % _recommendation(cand))
    w("")

    # 3
    w("## 3. %s" % SECTIONS[2])
    w("")
    if not cites:
        w("No code is cited yet. This section is empty because nobody has said "
          "which lines change, not because nothing changes. Add them and "
          "regenerate:")
        w("")
        w("```")
        w("%s pack %s --cite <path>:<start>-<end>"
          % (bs.invocation("bm_packs.py", __file__), cand["candidate_uuid"][:8]))
        w("```")
        w("")
    for c in cites:
        w("### `%s` lines %d to %d" % (c["path"], c["start"], c["end"]))
        w("")
        w("<!-- bm-cite: path=%s lines=%d-%d sha256=%s anchor=%s -->"
          % (c["path"], c["start"], c["end"], c["sha"],
             _quote_anchor(c["anchor"])))
        w("")
        w("```python" if c["path"].endswith(".py") else "```")
        for offset, text in enumerate(c["body"].split("\n")):
            w("%d  %s" % (c["start"] + offset, text))
        w("```")
        w("")
        if not any(bs.paths_overlap(p, c["path"]) for p in changed):
            # Stated rather than papered over. The refusal in section 5 is
            # computed from what the STORE records as this gate's change set
            # (the work record's claimed paths, plus an artifact scope key), not
            # from what this document quotes. A file quoted here and claimed
            # nowhere is a real gap between the two, and the fix is a claim.
            w("This path is NOT in the change set the store records for this "
              "gate, so a critical alert anchored here will NOT refuse the "
              "approval. Claim it on the work record to close that gap: "
              "`bm_store.py claim <name> persistent --files %s`." % c["path"])
            w("")
        anchored = [n for n in notes
                    if n["anchor_type"] == "file"
                    and bs.paths_overlap(n["anchor_key"], c["path"])]
        if anchored:
            w("Notes anchored here:")
            w("")
            for n in anchored:
                w("- %s" % _note_line(n, anchors))
            w("")

    # 4
    w("## 4. %s" % SECTIONS[3])
    w("")
    if not deps["terms"]:
        w("Nothing to search for: the map is built from the cited files and "
          "the symbols they define, and no code is cited yet.")
        w("")
    else:
        w("Discovered by scanning %d text file(s) under the project root for "
          "these terms:" % deps["scanned"])
        w("")
        for t in deps["terms"]:
            w("- `%s` (%s)" % (t["term"], t["why"]))
        w("")
        for bucket, label in (("code", "Callers and other code"),
                              ("tests", "Tests"), ("docs", "Documents")):
            rows = deps["hits"][bucket]
            w("**%s: %d.**" % (label, len(rows)))
            w("")
            if not rows:
                w("- none found")
            for r in rows[:_MAX_LISTED]:
                if r["note"]:
                    w("- `%s` (%s)" % (r["path"], r["note"]))
                else:
                    w("- `%s:%d` matched `%s`" % (r["path"], r["line"], r["term"]))
            if len(rows) > _MAX_LISTED:
                w("- ... and %d more" % (len(rows) - _MAX_LISTED))
            w("")

    # 5
    w("## 5. %s" % SECTIONS[4])
    w("")
    w("**Blast radius.** %s" % _blast_radius(deps))
    w("")
    w("**What tells you it broke.** %s" % _what_breaks(deps))
    w("")
    if alerts:
        w("**Unresolved critical alerts standing in front of this gate.** These "
          "REFUSE the approval until they are resolved, or until the founder "
          "overrides them with a recorded reason.")
        w("")
        for n in alerts:
            w("- %s about %s" % (_note_line(n, anchors), n["matched"]))
        w("")
    else:
        w("**Unresolved critical alerts.** None anchored to this candidate or "
          "to any file in its change set.")
        w("")
    w("**Rollback.**")
    w("")
    w("```")
    for cmd in _rollback_commands(cand, pack_rel):
        w(cmd)
    w("```")
    w("")

    # 6
    w("## 6. %s" % SECTIONS[5])
    w("")
    know = _store_knowledge(store, cand)
    for label, rows, empty in (
            ("Approved rules that already apply here", know["rules"],
             "none in this scope"),
            ("Prior decisions recorded against the same work", know["decisions"],
             "none recorded"),
            ("Recorded failures of this loop", know["failures"],
             "none recorded")):
        w("**%s: %d.**" % (label, len(rows)))
        w("")
        if not rows:
            w("- %s" % empty)
        for r in rows[:_MAX_LISTED]:
            w("- %s" % r)
        if len(rows) > _MAX_LISTED:
            w("- ... and %d more" % (len(rows) - _MAX_LISTED))
        w("")

    # 7
    w("## 7. %s" % SECTIONS[6])
    w("")
    w("```mermaid")
    for line in _mermaid(cites, deps):
        w(line)
    w("```")
    w("")

    # 8
    w("## 8. %s" % SECTIONS[7])
    w("")
    w("A verdict recorded only in this file is a verdict the store cannot "
      "report. Record it with the command below; it writes a `review` note "
      "anchored to this candidate, and the next regeneration lists it here.")
    w("")
    w("```")
    w("%s review %s --by \"<your name>\" --verdict "
      "approve|reject|concerns --notes \"<what you checked>\" "
      "[--residual \"<what still worries you>\"]"
      % (bs.invocation("bm_packs.py", __file__), cand["candidate_uuid"][:8]))
    w("```")
    w("")
    reviews = [n for n in notes if n["kind"] == "review"]
    w("**Recorded reviews: %d.**" % len(reviews))
    w("")
    if not reviews:
        w("- none yet")
    for n in reviews:
        w("- %s" % _note_line(n, anchors))
    w("")
    w("Other notes anchored to this decision: %d."
      % len([n for n in notes if n["kind"] != "review"]))
    w("")
    for n in notes:
        if n["kind"] != "review":
            w("- %s" % _note_line(n, anchors))
    w("")
    w("### Human notes, preserved verbatim")
    w("")
    w("Anything between the two markers survives every regeneration untouched. "
      "Write here rather than anywhere else in this file.")
    w("")
    # SBE14 defense in depth: every value written into `lines` above reaches
    # it through _d (which strips control characters, so a store field or a
    # cited excerpt line, prefixed with its own line number, cannot start a
    # line of its own) or is a fixed string this file wrote. It should
    # therefore already contain no line equal to a human-block marker, but
    # a future section that forgets to route a field through _d would
    # otherwise let such a line open what bs._redact_outside_human_blocks
    # reads as a real block, closed by the real markers the loop below
    # writes, exempting everything between from redaction (I10 forbids the
    # funnel from telling the difference once that happens). Neutralizing
    # here, before those real markers exist, is the second lock on the
    # same door bm_handover.py's render_page closes for real.
    lines[:] = "\n".join(
        bs._neutralize_stray_marker(l) for l in "\n".join(lines).split("\n")
    ).split("\n")
    if not human_blocks:
        human_blocks = [""]
    for block in human_blocks:
        w(HUMAN_BEGIN)
        if block:
            for line in block.split("\n"):
                w(line)
        w(HUMAN_END)
        w("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _note_line(n, anchors=None):
    """One note as one markdown line, with the whereabouts of its anchored line
    attached whenever that line is not simply still where it was.

    `anchors` is the store's own report keyed by note uuid (see
    bm_store.note_anchor_reports). It is optional only so this stays a plain
    function; render always passes it, because a pack that quotes a line range
    under I11 and then renders a note pointing at a line that has moved is
    exactly the stale citation I11 exists to prevent, one field over."""
    state = "open"
    if n["resolved_at"]:
        state = "resolved: %s" % _d(n["resolution"], 120)
    if n["overridden_at"]:
        state += ", OVERRIDDEN by %s: %s" % (_d(n["override_by"], 60),
                                             _d(n["override_reason"], 120))
    where = "%s:%s" % (n["anchor_type"], _d(n["anchor_key"], 80))
    if n["anchor_line"]:
        where += ":%d" % n["anchor_line"]
    line = "`%s` %s%s by %s at %s, anchored %s, %s: %s" % (
        n["note_uuid"][:8], n["kind"],
        " (%s)" % n["severity"] if n["severity"] else "",
        _d(n["author"], 60), n["created_at"], where, state, _d(n["body"], 300))
    found = (anchors or {}).get(n["note_uuid"])
    if found and found["state"] != "resolves":
        line += " ANCHOR %s: %s" % (found["state"].upper(), _d(found["why"], 200))
    return line


def _decision_paragraph(cand, scope):
    action = _d(cand["proposed_action"], 400)
    trigger = _d(cand["proposed_trigger"], 400)
    if not action or not trigger:
        return ("This candidate is incomplete: a decision needs both a trigger "
                "and an action, and approval refuses without them. Trigger is "
                "%r and action is %r. Fill them in on the approve command, or "
                "capture a better candidate."
                % (trigger or "", action or ""))
    return ("The proposal is that when %s, the standing behaviour becomes: %s. "
            "It would apply in scope %s and to nothing outside it. Until a "
            "human approves it, it changes nothing at all: a candidate is "
            "inert." % (trigger.rstrip(". "), action.rstrip(". "), scope))


def _if_wrong(cand, deps, alerts):
    reach = (len(deps["hits"]["code"]) + len(deps["hits"]["tests"])
             + len(deps["hits"]["docs"]))
    parts = ["A wrong rule here is applied silently to every future task that "
             "matches its trigger, and it is retrieved before anybody re-reads "
             "it, so the cost is paid repeatedly rather than once."]
    if reach:
        parts.append("The cited lines are referenced from %d other place(s) in "
                     "this tree, which is the size of the area a mistake "
                     "reaches." % reach)
    if alerts:
        parts.append("Somebody has already written a critical alert against "
                     "this area, which is evidence that the risk is not "
                     "theoretical.")
    parts.append("It is reversible: see the rollback commands in section 5, and "
                 "note that a deprecated rule keeps its history rather than "
                 "disappearing.")
    return " ".join(parts)


def _options(store, cand):
    """The options a reviewer actually has, built from recorded rows.

    The alternatives are not invented: they are the existing rules this proposal
    contradicts or duplicates, which is exactly what conflicts_against reports,
    plus the always-available option of not approving."""
    opts = [{
        "title": "Option A: approve the proposal as written (recommended)",
        "body": _d(cand["proposed_action"], 400) or "(no action text recorded)",
        "tradeoff": ("it starts being retrieved and applied immediately, and "
                     "every application is graded, so a bad rule is visible in "
                     "the outcome record rather than invisible"),
    }]
    found = store.conflicts_against({
        "scope_type": cand["proposed_scope_type"],
        "scope_key": cand["proposed_scope_key"],
        "trigger_text": cand["proposed_trigger"],
        "action_text": cand["proposed_action"]})
    letter = ord("B")
    for other, verdict in found["contradictions"] + found["duplicates"]:
        opts.append({
            "title": "Option %s: keep the existing rule `%s` instead"
                     % (chr(letter), other["rule_uuid"][:8]),
            "body": "That rule says: %s (when %s)"
                    % (_d(other["action_text"], 300),
                       _d(other["trigger_text"], 200)),
            "tradeoff": "approval refuses while this stands, because %s"
                        % "; ".join(verdict["reasons"]),
        })
        letter += 1
    opts.append({
        "title": "Option %s: do not approve" % chr(letter),
        "body": "Reject the candidate with a reason, or leave it pending.",
        "tradeoff": ("the correction is remembered as a rejected candidate with "
                     "its reason, which stops it being proposed forever, and "
                     "nothing changes in the meantime"),
    })
    return opts


def _recommendation(cand):
    because = _d(cand["proposed_because"], 400)
    if because:
        return ("Approve option A. The reason recorded with the candidate is: "
                "%s" % because)
    return ("Approve option A, but note that NO reason was recorded with this "
            "candidate. An approved rule with no stated because cannot be "
            "argued with later. Pass --because on the approve command.")


def _blast_radius(deps):
    code = len(deps["hits"]["code"])
    tests = len(deps["hits"]["tests"])
    docs = len(deps["hits"]["docs"])
    if not (code or tests or docs):
        return ("Nothing outside the cited lines was found to reference them. "
                "That is a small radius if the citations are complete, and no "
                "evidence at all if they are not.")
    return ("%d code file(s), %d test file(s) and %d document(s) reference the "
            "cited files or symbols. Section 4 lists them." % (code, tests, docs))


def _what_breaks(deps):
    tests = deps["hits"]["tests"]
    if not tests:
        return ("No test file references the cited lines. A change here is "
                "unverified by construction, which is itself the finding.")
    names = ", ".join(sorted(set(t["path"] for t in tests))[:6])
    return ("These suites touch the cited code and are the fastest signal: %s. "
            "Run python3 tools/test_all.py." % names)


def _rollback_commands(cand, pack_rel):
    inv = bs.invocation("bm_learn.py", os.path.join(HERE, "bm_learn.py"))
    return [
        "# before approval: the candidate never becomes a rule",
        "%s reject %s --because \"<why not>\"" % (inv, cand["candidate_uuid"][:8]),
        "",
        "# after approval: the rule stops being retrieved, keeping its history",
        "%s deprecate <rule-id> --because \"<why>\"" % inv,
        "",
        "# this document only, which is generated output and safe to remove",
        "git rm %s" % pack_rel,
    ]


def _store_knowledge(store, cand):
    rules = []
    for r in store.list_learning_rules(states=L.INJECTABLE_STATES):
        if r["scope_type"] == "global" or (
                r["scope_type"] == cand["proposed_scope_type"]
                and r["scope_key"] == cand["proposed_scope_key"]):
            rules.append("`%s` [%s, %s] when %s, do %s"
                         % (r["rule_uuid"][:8],
                            L.safe_scope(r["scope_type"], r["scope_key"]),
                            r["state"], _d(r["trigger_text"], 160),
                            _d(r["action_text"], 200)))
    decisions = []
    if cand["source_record_uuid"]:
        for row in store.conn.execute(
                "SELECT seq, topic, text, created_at FROM decisions "
                "WHERE lifecycle_uuid=? ORDER BY seq",
                (cand["source_record_uuid"],)).fetchall():
            decisions.append("#%d %s (%s): %s"
                             % (row["seq"], _d(row["topic"], 80),
                                row["created_at"], _d(row["text"], 300)))
    failures = []
    try:
        report = store.learning_loop_failures()
    except bs.BMStoreError as e:
        failures.append("the failure report could not be read: %s" % e)
        report = None
    if report:
        # A NAMED set of keys, not everything the report happens to carry. The
        # report also holds window sizes and classification tallies, and printing
        # those under a heading that says "failures" is how a document starts
        # lying: "classes: 6" is the number of classification buckets, not six
        # failures.
        for key in _FAILURE_KEYS:
            value = report.get(key)
            if isinstance(value, list) and value:
                failures.append("%s: %d" % (key.replace("_", " "), len(value)))
    return {"rules": rules, "decisions": decisions, "failures": failures}


def _mermaid(cites, deps):
    """A flowchart of the affected path: what points at the cited files, and
    what verifies them. Node ids are generated, never taken from a path, so a
    path with a character mermaid treats as syntax cannot break the diagram."""
    lines = ["flowchart TD"]
    if not cites:
        lines.append("  none[\"no code cited yet\"]")
        return lines
    ids = {}
    for i, c in enumerate(cites):
        node = "cited%d" % i
        ids[c["path"]] = node
        lines.append("  %s[\"%s lines %d-%d\"]"
                     % (node, c["path"], c["start"], c["end"]))
    target = ids[cites[0]["path"]]
    for bucket, prefix, arrow in (("code", "caller", "-->"),
                                  ("tests", "test", "-. verifies .->"),
                                  ("docs", "doc", "-. describes .->")):
        for i, r in enumerate(sorted(set(x["path"] for x in deps["hits"][bucket]))[:6]):
            node = "%s%d" % (prefix, i)
            lines.append("  %s[\"%s\"]" % (node, r))
            if bucket == "code":
                lines.append("  %s %s %s" % (node, arrow, target))
            else:
                lines.append("  %s %s %s" % (target, arrow, node))
    return lines


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------

def _notes_for(store, cand, cites):
    """Every note anchored to this candidate or to a cited file, oldest first.

    Matched the same way the store's own refusal matches, so the pack and the
    gate can never disagree about which notes are in front of this approval: a
    candidate anchor by equality (bm_store.add_note resolves the prefix a human
    typed to the full uuid at the door, so equality is what a resolved anchor
    compares as), and a file anchor through paths_overlap, because a file anchor
    may be a glob or a parent directory. It used to be a startswith on the
    candidate anchor, which rendered one short anchor as an open critical alert
    on every pack in the project."""
    cuuid = cand["candidate_uuid"]
    out = []
    for n in store.list_notes():
        if n["anchor_type"] == "candidate" and n["anchor_key"] == cuuid:
            out.append(n)
            continue
        if n["anchor_type"] == "file" and any(
                bs.paths_overlap(n["anchor_key"], c["path"]) for c in cites):
            out.append(n)
    out.sort(key=lambda n: (n["created_at"], n["note_uuid"]))
    return out


def cmd_stakes(argv):
    """The one line a question window carries, plus the path the pack WOULD
    occupy. Generates nothing (spec A.2: nothing until asked).

    CORRECTED 2026-08-11 (codex-audit-split-2026-08-10-night.md HIGH #1 for
    tools/bm_effects.py, R4-TRIAGE-2026-08-11.md row 1), same pattern as
    tools/bm_docs.py's cmd_tier and tools/bm_learn.py's cmd_lookup: this
    command generates no PACK FILE, which the sentence above is still true
    about, but it opens a WRITABLE bm_store.Store below, so against a
    database one schema version behind it MIGRATES IT before ever
    computing a stakes line. It is declared ledger_write in
    tools/bm_effects.py.

    OPEN, and named rather than left implied: this SHOULD be pure_read and
    is not yet. get_learning_candidate and blocking_alerts (both called
    directly below) and gate_change_set (called by stakes_line()) all exist
    on bm_store.Store only, not on bm_store.ReadOnlyStore, so swapping the
    constructor would raise AttributeError on every normal invocation.
    Adding read-only counterparts to ReadOnlyStore is what would close
    this, and that is a change to the store's own public surface, out of
    this fix's scope."""
    pos, kv = _parse(argv, {"json"})
    if not pos:
        _err("usage: stakes <candidate-id>")
        return 2
    root = _root()
    store = _store()
    try:
        cand = store.get_learning_candidate(pos[0])
        alerts = store.blocking_alerts(cand)
        path, n = pack_path(root, store, cand)
        rel = os.path.relpath(path, root).replace("\\", "/")
        line = stakes_line(store, cand, alerts)
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps({"candidate": cand["candidate_uuid"], "index": n,
                         "pack_path": rel, "stakes": line,
                         "blocking_alerts": len(alerts)},
                        indent=2, sort_keys=True))
        return 0
    _out("stakes: %s" % line)
    _out("deep dive on demand: %s pack %s"
         % (bs.invocation("bm_packs.py", __file__), cand["candidate_uuid"][:8]))
    _out("it would be written to %s" % rel)
    return 0


def cmd_pack(argv):
    pos, kv = _parse(argv, {"cite", "recite", "json"},
                     repeatable=("cite", "recite"))
    if not pos:
        _err("usage: pack <candidate-id> [--cite path:start-end[@anchor]] "
             "[--recite path:start-end[@anchor]]")
        _err("--cite adds an excerpt. --recite moves an excerpt whose lines "
             "have shifted, after you have read the new ones.")
        return 2
    root = _root()
    added = [parse_cite(s) for s in kv.get("cite", [])]
    recited = [parse_cite(s) for s in kv.get("recite", [])]
    store = _store()
    try:
        cand = store.get_learning_candidate(pos[0])
        path, n = pack_path(root, store, cand)
        rel = os.path.relpath(path, root).replace("\\", "/")
        prior = read_existing(path)
        cites = merge_citations(prior["cites"], added, recited)
        # I11: every one of them re-read NOW. The first raise stops the write,
        # so a pack is never half updated against a file that moved.
        resolved = [resolve_citation(root, c) for c in cites]
        deps = discover_dependencies(root, resolved, exclude=(rel,))
        notes = _notes_for(store, cand, resolved)
        alerts = store.blocking_alerts(cand)
        text = render(root, store, cand, resolved, deps, notes, alerts,
                      prior["human"], rel, store.gate_change_set(cand))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        # Checked BEFORE the write, on the text about to be written, so the
        # numbers reported below are line numbers in the file on disk. The funnel
        # carries a human block through verbatim (I10), which is the whole point;
        # this is how a human hears that their own paragraph holds something
        # secret-shaped, instead of finding it quietly rewritten.
        human_hits = bs.human_block_secret_hits(text)
        bs.write_generated_document(path, text)
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps({"pack_path": rel, "index": n,
                         "citations": len(resolved),
                         "preserved_human_blocks": len(prior["human"]),
                         "human_block_secret_shaped_lines": human_hits,
                         "blocking_alerts": len(alerts),
                         "sections": list(SECTIONS)},
                        indent=2, sort_keys=True))
        return 0
    _out("wrote %s" % rel)
    _out("  %d section(s), %d citation(s) re-read from disk, %d human block(s) "
         "preserved" % (len(SECTIONS), len(resolved), len(prior["human"])))
    if human_hits:
        _out("  KEPT VERBATIM AND WORTH READING: line(s) %s inside a human "
             "block look secret shaped. Nothing was rewritten (I10). If that is "
             "a real credential, edit the block and rotate it."
             % ", ".join(str(i) for i in human_hits))
    if alerts:
        _out("  %d UNRESOLVED CRITICAL ALERT(S): this gate will refuse to close"
             % len(alerts))
    return 0


def cmd_review(argv):
    """Record a named engineer's verdict IN THE STORE, anchored to the
    candidate. Section A.1 item 8: the review must not live only in a file."""
    pos, kv = _parse(argv, {"by", "verdict", "notes", "residual", "session",
                            "json"},
                     wants_value=("by", "verdict", "notes", "residual",
                                  "session"))
    verdicts = ("approve", "reject", "concerns")
    if (not pos or not (kv.get("by") or "").strip()
            or kv.get("verdict") not in verdicts
            or not (kv.get("notes") or "").strip()):
        _err("usage: review <candidate-id> --by \"<name>\" --verdict %s "
             "--notes \"<what you checked>\" [--residual \"<what still "
             "worries you>\"]" % "|".join(verdicts))
        _err("a verdict with no name and no notes is not a review.")
        return 2
    body = "verdict %s. %s" % (kv["verdict"], kv["notes"])
    if (kv.get("residual") or "").strip():
        body += " Residual concerns: %s" % kv["residual"]
    store = _store()
    try:
        cand = store.get_learning_candidate(pos[0])
        note = store.add_note(kind="review", body=body, author=kv["by"],
                              author_kind="human", anchor_type="candidate",
                              anchor_key=cand["candidate_uuid"],
                              session_id=kv.get("session", ""))
    finally:
        store.close()
    if kv.get("json"):
        # Digest-shaped columns withheld by the one policy (I9), the same as
        # every other note payload a CLI prints. A review note is anchored to a
        # candidate and so carries no line fingerprint today; routing it anyway
        # means the next digest column on notes is covered here the day it
        # exists rather than the day somebody remembers this surface.
        _out(json.dumps(bs.withhold_digest_columns("notes", note),
                        indent=2, sort_keys=True))
        return 0
    _out("review %s recorded against candidate %s"
         % (note["note_uuid"][:8], cand["candidate_uuid"][:8]))
    _out("  regenerate the pack to list it: %s pack %s"
         % (bs.invocation("bm_packs.py", __file__), cand["candidate_uuid"][:8]))
    return 0


COMMANDS = {"stakes": cmd_stakes, "pack": cmd_pack, "review": cmd_review}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_packs: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except PackError as e:
        # Fail CLOSED and loudly. I11 exists so that this is the outcome of a
        # stale citation, rather than a document that reads as current.
        _err("refused (%s): %s" % (e.reason, e))
        return 2
    except bs.OwnershipRefused as e:
        _err("refused (%s): %s" % (e.reason, e))
        return 2
    except bs.BMStoreError as e:
        _err("bm_packs: %s" % e)
        return 2


def cli():
    """Console-script entry point for a packaged install. Must be callable with
    no arguments, so it wraps main(argv) rather than being it."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

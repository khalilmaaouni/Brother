#!/usr/bin/env python3
"""BrotherMode documentation engine: the folder a stranger can read.

WHAT THIS IS FOR
  BrotherMode records more about a project than any document it produces. This
  writes the documents, almost entirely from what is already recorded, into a
  numbered folder whose reading order is unambiguous:

      Documentation/
        00-START-HERE.md
        10-business/     BA-SUMMARY REQUIREMENTS WBS SCHEDULE
        20-technical/    ARCHITECTURE DATA-MODEL PROCESS-DIAGRAMS
                         DEPENDENCIES CODE-MAP WHITEPAPER
        30-decisions/    INDEX.md and the D-<n>-<slug>.md packs
        40-handover/     HANDOVER.md RUNBOOK.md
        90-generated/    facts.json

  Phase B of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
  -design.md, section 5.

THREE SOURCES, IN COST ORDER (section 5.2)
  PROJECTION, free. Store rows rendered directly: the work breakdown from work
  records and their claims, the schedule from records plus the dependency graph
  their overlapping claims imply (a mermaid gantt plus a critical path computed
  by longest path over that graph), the decision index from candidates, rules,
  receipts and recorded decisions, notes and lineage inline.

  INTROSPECTION, free. The repository and the store's own schema read as they
  are: a mermaid entity diagram built by introspecting sqlite, an import graph
  built by parsing the imports, a module inventory and a test inventory.

  NARRATIVE, last and least. Assembled from recorded fields, never invented, and
  cached against a hash of exactly those fields (I12), so unchanged facts do not
  regenerate a paragraph.

THE FOUR RULES THIS FILE LIVES BY
  I10, GENERATED OUTPUT NEVER DESTROYS HUMAN TEXT. Every document ends with a
  human block, and anything a human writes between the markers is carried
  through every regeneration byte for byte. The suite proves it with a
  deliberately destructive variant that must fail.

  I12, PROSE IS CACHED AGAINST FACTS. Every narrative block records the hash of
  the facts it describes. On regeneration a block whose hash still matches is
  reused verbatim and its writer is never called at all, which is what makes
  "unchanged facts do not regenerate" a measurable claim rather than a hope.

  I13, TIERS RAISE ONLY. The tier is chosen from measured signals, and the
  choice plus the signals that made it are printed and written into START-HERE.
  An automatic decision may only increase depth: the last tier is remembered in
  facts.json and acts as a floor. Lowering takes --tier, which is a founder
  saying so out loud.

  DETERMINISM. Two regenerations in a row are byte identical. No generation
  timestamp reaches a document body, every list is sorted, and every scan skips
  the Documentation folder itself, because a generator that reads its own output
  churns forever and every review then opens on a diff that means nothing.

WHAT THIS FILE MAY NOT DO
  It never writes the store: bm_store.py stays the single writer, and every file
  it produces goes through bm_store.write_generated_document, the one funnel that
  redacts before anything reaches disk and protects the human blocks. No network,
  no subprocess. It never imports the optional exporter (section 5.6): that lives
  in bm_docs_export.py with its own command, so the mandatory path cannot acquire
  an optional dependency by accident.

WHAT IT DELIBERATELY DOES NOT DO
  It writes no narrative the store did not record. Where a field is empty the
  document says the field is empty and names the command that would fill it. A
  generator that invents a rationale puts words in the founder's mouth.

  It quotes no candidate raw text. That is the most sensitive column in the
  store and it is withheld here exactly as `bm_learn.py show-candidate` withholds
  it.

Python 3.9, standard library only.

No em or en dashes anywhere in this file, its comments, or its output.

Usage:
  python3 tools/bm_docs.py tier                 # the signals and the decision
  python3 tools/bm_docs.py generate             # write the folder
  python3 tools/bm_docs.py generate --tier 3    # override, may lower
  python3 tools/bm_docs.py facts --json         # the projected facts
  python3 tools/bm_docs.py capability-status            # print the block
  python3 tools/bm_docs.py capability-status --check    # refuse a stale block
  python3 tools/bm_docs.py capability-status --write    # rewrite it in README
  python3 tools/bm_docs.py roadmap-status --check       # the roadmap block
  python3 tools/bm_docs.py roadmap-status --write       # rewrite it in docs/
  python3 tools/bm_docs.py verify-docs                  # every check, one pass
"""

import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, the same way bm_packs.py does: this file
    is invoked from arbitrary working directories and must not depend on
    whatever sys.path the caller happened to have."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
L = _load("bm_learning")

# The folder, and the numbered layout of section 5.1. Numbered so a stranger's
# reading order is unambiguous rather than alphabetical by accident.
DOC_ROOT = "Documentation"

# (relative path, minimum tier, one line saying what it is). ONE list, read by
# the renderer, by START-HERE, by the CLI report and by the suite: a document
# that goes missing shows up as a missing file rather than as a page nobody
# notices is gone.
#
# SPEC AMBIGUITY, resolved by the implementer and RATIFIED BY THE FOUNDER on
# 2026-07-30: section 5.1 lists BA-SUMMARY.md in the layout while section 5.3's
# tier table does not place it in any tier, and the tier table names a
# "whitepaper" that the layout does not place in a directory. BA-SUMMARY is
# emitted at tier 2 beside REQUIREMENTS (it is the business narrative those
# requirements belong to) and the whitepaper is emitted at tier 3 as
# 20-technical/WHITEPAPER.md. This is no longer an open question and does not
# need re-deriving; the ratified statement lives in docs/RELEASE.md under
# "Ratified: where the business summary and the whitepaper live".
# IN READING ORDER, which is why the directories are numbered: a stranger opens
# them top to bottom. The tier column says which of them exist at which depth,
# and it is deliberately NOT the sort key, because sorting by tier would present
# a reader with 00, then 30, then 40, then back to 10.
FILES = (
    ("00-START-HERE.md", 1,
     "the reading order, the tier and the signals that chose it"),
    ("10-business/BA-SUMMARY.md", 2,
     "what the project is trying to do, in business language"),
    ("10-business/REQUIREMENTS.md", 2,
     "the objectives and the standing rules that constrain them"),
    ("10-business/WBS.md", 2,
     "the work breakdown, projected from the work records"),
    ("10-business/SCHEDULE.md", 2,
     "a gantt of the recorded work plus its critical path"),
    ("20-technical/ARCHITECTURE.md", 2,
     "the shape of the code, by module and by layer"),
    ("20-technical/DATA-MODEL.md", 2,
     "an entity diagram introspected from the live schema"),
    ("20-technical/PROCESS-DIAGRAMS.md", 3,
     "the recorded lifecycle of a work record, as a state diagram"),
    ("20-technical/DEPENDENCIES.md", 2,
     "the import graph, parsed from the source"),
    ("20-technical/CODE-MAP.md", 3,
     "every module and every test file, with sizes"),
    ("20-technical/WHITEPAPER.md", 3,
     "the long-form account of why this project is built this way"),
    ("30-decisions/INDEX.md", 1,
     "every decision, its state, and the pack that reviews it"),
    ("40-handover/HANDOVER.md", 1,
     "everything a human with no AI needs to take this over"),
    ("40-handover/RUNBOOK.md", 2,
     "how to run it, how to test it, how to recover it"),
    ("90-generated/facts.json", 1,
     "the machine-readable facts every page above was projected from"),
)

TIERS = {1: "lean", 2: "standard", 3: "full"}

# The human block markers come from bm_store, which owns them because the file
# funnel has to see them to protect what is between them (I10). One definition,
# so this file and the funnel can never disagree about where human text begins.
HUMAN_BEGIN = bs.HUMAN_BLOCK_BEGIN
HUMAN_END = bs.HUMAN_BLOCK_END

# The narrative cache record (I12). Written by the generator, read back by the
# generator. Without a recorded hash there is nothing for a changed fact to
# disagree with, so the cache would either never reuse or never refresh.
#
# TWO hashes, and the second one is here because the first one alone let the page
# lie. sha256= is the hash of the FACTS the paragraph describes, and it decides
# whether the paragraph is worth writing again. body= is the hash of the
# paragraph's own text. Without it, any text at all could be swapped in under a
# still-valid fact hash and would then be reused on every later run for as long
# as those facts held still, while the header of that same file told the reader
# everything outside the human markers gets rewritten. So a recorded body that
# does not match its own checksum is a cache MISS and the paragraph is written
# again from the facts. A record with no body= at all is a miss for the same
# reason, which is how a folder generated before this existed heals itself on the
# next run. This is integrity against a bad merge, a stale copy and a hand edit,
# not a defence against somebody who deliberately recomputes the checksum.
_PROSE_OPEN = re.compile(
    r"^<!-- bm-prose: id=(?P<id>[a-z0-9-]+) sha256=(?P<sha>[0-9a-f]{64})"
    r"(?: body=(?P<body>[0-9a-f]{64}))? -->$")
_PROSE_CLOSE = "<!-- bm-prose:end -->"

_SKIP_DIRS = frozenset((".git", ".brothermode", "node_modules", "__pycache__",
                        ".venv", "venv", ".mypy_cache", ".pytest_cache",
                        ".tox", "dist", "build", ".eggs", DOC_ROOT))
# A file bigger than this is data, not source. Bounded so `generate` stays a
# command a founder runs at a gate rather than a batch job.
_MAX_SOURCE_BYTES = 400000
# How many rows any one table in any one document lists in full. The COUNT is
# always exact; this caps the listing so one busy area does not produce a
# hundred-line section nobody reads.
_MAX_LISTED = 40

_DEF_RE = re.compile(r"^(\s*)(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+(?P<plain>[A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)"
    r"|from\s+(?P<mod>[A-Za-z_.][\w.]*)\s+import\s+(?P<names>[\w,\s*()]+))")
_TEST_RE = re.compile(r"^\s*def\s+(test_[A-Za-z0-9_]*)")


class DocsError(Exception):
    """A refusal with a reason a human can act on. Carries `reason` in the same
    shape bm_store.OwnershipRefused and bm_packs.PackError do, so the CLI prints
    all three alike."""

    def __init__(self, reason, message):
        Exception.__init__(self, message)
        self.reason = reason


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _parse(argv, known, wants_value=()):
    """Flags into a dict, REFUSING anything unrecognized, exactly as
    bm_learn.py and bm_packs.py do."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_docs: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_docs: --%s needs a value" % name)
                    sys.exit(2)
                kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _d(text, limit=300):
    """Every store-derived string that reaches a document passes through here:
    control characters stripped, length capped. A newline inside an objective
    could otherwise forge a markdown heading, and a heading is structure these
    documents are writing on purpose.

    An em dash, an en dash or a look-alike becomes a plain hyphen here (I7), and
    THIS is the place for it: the text arriving is a note body somebody else
    typed or a source line from a file this project does not own, so the copy
    rule cannot be met by writing carefully. Display only: the store keeps the
    body verbatim and the file keeps its bytes."""
    return L.plain_dashes(L.safe_display(text or "", limit))


def _cell(text, limit=120):
    """_d, plus the two characters a markdown table treats as syntax."""
    return _d(text, limit).replace("|", "/").replace("\n", " ")


def _flat(text, limit=80):
    """A label safe inside a mermaid node or a gantt row, where a colon, a comma,
    a quote or a bracket is syntax."""
    flat = re.sub(r"[:,\"'\[\]{}()<>#;|]", " ", _d(text, limit)).strip()
    # A LEADING dash or dot is stripped too. A gantt row whose task name starts
    # with a dash reads as syntax to the renderer rather than as a label, and a
    # record called "-h" is not hypothetical: this project's own store has one,
    # created by a probe that took a help flag as a record name.
    flat = flat.lstrip("-.=*+ ").strip()
    return flat or "unnamed"


def _rows(store, sql, params=()):
    """Every read this file makes goes through bm_store._exec, which is the one
    place a busy database becomes a named refusal and a damaged one becomes a
    quarantine (I6). A private name reached across a module boundary is a cost,
    and it is the smaller cost: a bare store.conn.execute here would be a second
    error policy for the same connection."""
    return [dict(r) for r in bs._exec(store, sql, params).fetchall()]


# ---------------------------------------------------------------------------
# Reading an existing document: human blocks (I10) and prose cache (I12).
# ---------------------------------------------------------------------------

def read_existing(path):
    """Parse an existing document for the two things regeneration must not lose.

    Returns {"human": [str, ...], "prose": {id: {"sha": str, "body": str}}} and
    never raises on a malformed file: an unparseable document is regenerated,
    and the human blocks are recovered by marker scan, which is the one part
    that must survive a file somebody hand-edited.

    THE HUMAN MARKERS ARE READ FIRST, AND A PROSE MARKER IS ONLY READ OUTSIDE
    THEM. This is the lesson bm_packs.read_existing learned the expensive way:
    a reviewer who pastes a generated header into the block the file TELLS them
    to write in must not thereby reconfigure the generator. Inside a human block
    every line is prose, including one that looks like machine configuration.

    A second begin marker inside a block is CONTENT, not a nested block, for the
    same reason: resetting the buffer on it drops every line written before it.
    """
    out = {"human": [], "prose": {}}
    if not os.path.isfile(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    inside_human = False
    human_buf = []
    prose_id = None
    prose_sha = ""
    prose_body_sha = ""
    prose_buf = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == HUMAN_BEGIN and not inside_human:
            inside_human = True
            human_buf = []
            continue
        if stripped == HUMAN_END and inside_human:
            inside_human = False
            out["human"].append("\n".join(human_buf))
            human_buf = []
            continue
        if inside_human:
            human_buf.append(line)
            continue
        if prose_id is not None:
            if stripped == _PROSE_CLOSE:
                out["prose"][prose_id] = {"sha": prose_sha,
                                          "body_sha": prose_body_sha,
                                          "body": "\n".join(prose_buf)}
                prose_id, prose_sha, prose_body_sha = None, "", ""
                prose_buf = []
            else:
                prose_buf.append(line)
            continue
        m = _PROSE_OPEN.match(stripped)
        if m:
            prose_id = m.group("id")
            prose_sha = m.group("sha")
            # "" when the record predates the body checksum, which reads as
            # "unknown" and therefore as a miss.
            prose_body_sha = m.group("body") or ""
            prose_buf = []
    if inside_human and human_buf:
        # An unterminated human block. KEPT, because losing a paragraph because
        # somebody forgot the closing marker is exactly the destruction I10
        # forbids. The regenerated file closes it.
        out["human"].append("\n".join(human_buf))
    # An unterminated prose block is DROPPED, which is the opposite decision and
    # the right one: a half-written cache record cannot be trusted to describe
    # the facts its hash claims, and the cost of dropping it is that the block
    # gets written again.
    return out


def _body_hash(body):
    """The checksum a narrative block records over its own text. Computed on the
    exact string the block is written from and read back from, so a round trip
    through the file agrees with itself."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _neutralize_prose_markers(body):
    """A narrative body may not contain the cache markers. Nothing generated
    here would, but a recorded field it quotes could, and a forged close marker
    would truncate the cache record and silently swallow the rest of the page."""
    return body.replace("bm-prose:", "bm-prose ")


# ---------------------------------------------------------------------------
# Time, and why almost none of it reaches a document.
# ---------------------------------------------------------------------------

def _parse_iso(value):
    """A store timestamp into a datetime, or None. Never raises: a document is
    not worth a traceback over a row somebody hand-edited."""
    try:
        return datetime.datetime.strptime((value or "").strip(),
                                          "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _days_between(start, end):
    a, b = _parse_iso(start), _parse_iso(end)
    if a is None or b is None or b < a:
        return 1
    return max(1, (b - a).days)


def _date_only(value):
    dt = _parse_iso(value)
    if dt is None:
        return "1970-01-01"
    return dt.strftime("%Y-%m-%d")


def _span_bucket(days):
    """The recorded activity span, in buckets.

    Measured from the FIRST recorded row to the LAST, never to the wall clock.
    Age measured against now would change a signal, and therefore possibly a
    tier, and therefore the bytes of START-HERE, on a day when nothing about the
    project changed at all. That is exactly the churn determinism forbids."""
    if days is None:
        return "nothing recorded yet"
    if days < 7:
        return "under a week of recorded activity"
    if days < 31:
        return "one to four weeks of recorded activity"
    if days < 93:
        return "one to three months of recorded activity"
    return "over three months of recorded activity"


# ---------------------------------------------------------------------------
# The critical path: a pure longest-path over a weighted DAG.
# ---------------------------------------------------------------------------

def critical_path(weights, edges):
    """The longest path through a weighted directed acyclic graph.

    `weights` maps node id to a positive number. `edges` is an iterable of
    (from, to) pairs. Returns (path, total) where path is the list of node ids
    in order and total is the summed weight.

    Longest path by SUMMED NODE WEIGHT, which is what a critical path is: the
    chain of work whose durations add up to the earliest possible finish. Ties
    break on the node id so the answer is deterministic rather than dictionary
    ordered.

    A cycle RAISES. A schedule with a cycle has no critical path, and the useful
    thing to do with an impossible graph is to name it: silently dropping an edge
    would publish a shorter path as if it were the answer."""
    nodes = sorted(weights)
    known = set(nodes)
    incoming = dict((n, 0) for n in nodes)
    forward = dict((n, []) for n in nodes)
    seen_edges = set()
    for a, b in edges:
        if a not in known or b not in known or (a, b) in seen_edges or a == b:
            continue
        seen_edges.add((a, b))
        forward[a].append(b)
        incoming[b] += 1
    order = []
    ready = sorted(n for n in nodes if incoming[n] == 0)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(forward[n]):
            incoming[m] -= 1
            if incoming[m] == 0:
                ready.append(m)
                ready.sort()
    if len(order) != len(nodes):
        stuck = sorted(n for n in nodes if n not in set(order))
        raise DocsError(
            "schedule-cycle",
            "the work dependency graph has a cycle through %s, so it has no "
            "critical path. A cycle means two records each wait for the other; "
            "look at their claimed files." % ", ".join(stuck))
    best = dict((n, (0, [])) for n in nodes)
    for n in order:
        total, path = best[n]
        total += weights[n]
        path = path + [n]
        best[n] = (total, path)
        for m in sorted(forward[n]):
            if (total, path) > best[m]:
                best[m] = (total, path)
    if not nodes:
        return [], 0
    # The winner is the largest total, with the node id as the final tiebreak.
    # Spelled out as a loop rather than a key function so the ordering is
    # obvious: a critical path that changes between two runs of the same graph
    # would break the determinism this engine promises.
    winner = None
    for n in nodes:
        if winner is None or (best[n][0], n) > (best[winner][0], winner):
            winner = n
    return best[winner][1], best[winner][0]


# ---------------------------------------------------------------------------
# Projection: store rows into facts.
# ---------------------------------------------------------------------------

def work_items(store):
    """Every work record, oldest first, with its claims, decisions and digests.

    Oldest first because a work breakdown is a history: the order the work was
    claimed in is the order a reader needs it in."""
    out = []
    for rec in _rows(store, "SELECT * FROM records ORDER BY created_at, "
                            "lifecycle_uuid"):
        uuid = rec["lifecycle_uuid"]
        claims = [r["path"] for r in _rows(
            store, "SELECT path FROM claims WHERE lifecycle_uuid=? "
                   "ORDER BY path", (uuid,))]
        decisions = _rows(
            store, "SELECT seq, topic, text, created_at FROM decisions "
                   "WHERE lifecycle_uuid=? ORDER BY seq", (uuid,))
        digests = _rows(
            store, "SELECT seq, next_intent, blockers, files_note, created_at "
                   "FROM digests WHERE lifecycle_uuid=? ORDER BY seq", (uuid,))
        transitions = _rows(
            store, "SELECT from_state, to_state, note, at FROM transitions "
                   "WHERE lifecycle_uuid=? ORDER BY id", (uuid,))
        out.append({
            "id": uuid,
            "short": uuid[:8],
            "name": rec["name"],
            "lifetime": rec["lifetime"],
            "state": rec["state"],
            "objective": rec["objective"],
            "owner": rec["owner"],
            "tier": rec["tier"],
            "check_cmd": rec["check_cmd"],
            "evidence": rec["evidence"],
            "created_at": rec["created_at"],
            "updated_at": rec["updated_at"],
            "files": claims,
            "decisions": decisions,
            "digests": digests,
            "transitions": transitions,
        })
    return out


def work_graph(root, items):
    """The work dependency graph, DERIVED rather than declared.

    BrotherMode serializes overlapping work by law: two records may not hold the
    same file at once, so a record that claims a path an earlier record also
    claims could only run after it. That is the dependency, and it is a fact
    about the recorded claims rather than a guess about intent. Edges therefore
    run strictly forward in creation order, which is also why the graph cannot
    contain a cycle.

    Weight is the recorded elapsed days of the record, minimum one, so a record
    opened and closed the same day still occupies a day on the chart."""
    nodes = []
    for item in items:
        nodes.append({
            "id": item["short"],
            "name": item["name"],
            "state": item["state"],
            "start": _date_only(item["created_at"]),
            "days": _days_between(item["created_at"], item["updated_at"]),
        })
    edges = []
    for i, earlier in enumerate(items):
        for later in items[i + 1:]:
            shared = sorted(set(
                "%s and %s" % (a, b)
                for a in earlier["files"] for b in later["files"]
                if bs.paths_overlap(a, b)))
            if shared:
                edges.append([earlier["short"], later["short"], shared[0]])
    weights = dict((n["id"], n["days"]) for n in nodes)
    path, total = critical_path(weights, [(a, b) for a, b, _why in edges])
    return {"nodes": nodes, "edges": edges, "critical_path": path,
            "critical_days": total}


def decision_rows(store, root):
    """Every decision a reader might need to review, from all four places the
    store keeps one, plus the pack on disk that reviews it if there is one."""
    packs = {}
    directory = os.path.join(root, DOC_ROOT, "30-decisions")
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            m = re.match(r"^D-(\d+)-.*\.md$", name)
            if m:
                packs[int(m.group(1))] = "30-decisions/" + name
    out = []
    cands = _rows(store, "SELECT * FROM learning_candidates "
                         "ORDER BY created_at, candidate_uuid")
    for i, cand in enumerate(cands, 1):
        receipts = _rows(
            store, "SELECT issued_at, consumed_at FROM "
                   "learning_approval_receipts WHERE candidate_uuid=? "
                   "ORDER BY issued_at", (cand["candidate_uuid"],))
        out.append({
            "index": i,
            "kind": "candidate",
            "uuid": cand["candidate_uuid"],
            "rule_uuid": cand["resulting_rule_uuid"] or "",
            "id": cand["candidate_uuid"][:8],
            "title": cand["proposed_action"] or "(no action recorded)",
            "trigger": cand["proposed_trigger"],
            "because": cand["proposed_because"],
            "scope": L.safe_scope(cand["proposed_scope_type"],
                                  cand["proposed_scope_key"]),
            "status": cand["status"],
            "created_at": cand["created_at"],
            "source_type": cand["source_type"],
            "record": (cand["source_record_uuid"] or "")[:8],
            "rule": (cand["resulting_rule_uuid"] or "")[:8],
            "receipts": len(receipts),
            "pack": packs.get(i, ""),
        })
    # THE STORE'S OWN LISTER, not a SELECT of my own. A rule's text lives in
    # learning_rule_versions and only the CURRENT version is the rule, so a
    # plain SELECT over learning_rules returns rows with no trigger and no
    # action at all: reproduced against this project's own store, which raised
    # KeyError('action_text') on the first real run while the suite was green.
    # This lister also excludes forgotten rules at the lowest level, which is a
    # policy no caller should be re-implementing.
    for rule in sorted(store.list_learning_rules(),
                       key=lambda r: (r["created_at"], r["rule_uuid"])):
        out.append({
            "index": 0,
            "kind": "rule",
            "uuid": rule["rule_uuid"],
            "rule_uuid": rule["rule_uuid"],
            "id": rule["rule_uuid"][:8],
            "title": rule["action_text"] or "(no action recorded)",
            "trigger": rule["trigger_text"],
            "because": rule["because_text"],
            "scope": L.safe_scope(rule["scope_type"], rule["scope_key"]),
            "status": rule["state"],
            "created_at": rule["created_at"],
            "source_type": "approved rule",
            "record": "",
            "rule": rule["rule_uuid"][:8],
            "receipts": 0,
            "pack": "",
        })
    return out


def note_rows(store):
    out = []
    for n in store.list_notes():
        where = "%s:%s" % (n["anchor_type"], _cell(n["anchor_key"], 80))
        if n["anchor_line"]:
            where += ":%d" % n["anchor_line"]
        state = "open"
        if n["resolved_at"]:
            state = "resolved"
        if n["overridden_at"]:
            state += ", overridden"
        out.append({
            "uuid": n["note_uuid"],
            "id": n["note_uuid"][:8],
            "kind": n["kind"],
            "severity": n["severity"],
            "author": n["author"],
            "author_kind": n["author_kind"],
            # The composite string a reader sees, AND the three parts a renderer
            # groups by. Before the parts were carried, "notes at their anchors"
            # was one flat list at the end of the decision index, and a note
            # about app/pay.py appeared nowhere near app/pay.py in the code map.
            "anchor": where,
            "anchor_type": n["anchor_type"],
            "anchor_key": n["anchor_key"],
            "anchor_line": n["anchor_line"],
            "state": state,
            "created_at": n["created_at"],
            "body": n["body"],
            "resolution": n["resolution"],
            "override_by": n["override_by"],
            "override_reason": n["override_reason"],
        })
    return out


def lineage(store, items, decisions, notes):
    """Everything that touched a decision, in order, with authors.

    A query, not a stored field (spec section 6). Every event carries the
    timestamp the store recorded and the author the store recorded, so a chain
    with no author reads as a chain with no author rather than as mine.

    THE JOIN USED TO MATCH NOTHING, and this is the defect phase C found by
    driving the real command line against a real store while the suite was
    green. Notes were grouped by the composite anchor STRING, whose key half is
    a full 32 character uuid, and compared against the eight character short id
    a document displays, so `("candidate", "1a2b3c4d")` was looked up in a map
    keyed by `("candidate", "1a2b3c4d5e6f...")` and no note ever joined any
    chain. The lineage section rendered the capture, the receipt and the rule and
    silently dropped every human word written about them, which is the one thing
    a lineage query exists to carry. Matching now happens on the raw anchor
    fields, and a fixture in the suite proves a note anchored to a candidate, to
    a work record and to a decision all reach the chain.

    A decision anchor is free text (a decision has no single column identity at
    schema 8: it is a record uuid plus a sequence number), so it is matched by
    PREFIX on the record uuid plus an exact sequence number. An author who types
    four characters where two records match will see the note in both chains.
    That is deliberate: the anchor as typed is printed beside the note, so an
    under-specified anchor is visible in the document rather than resolved by
    guesswork into one chain that may be the wrong one."""
    out = {}
    by_anchor = {}
    for n in notes:
        by_anchor.setdefault((n["anchor_type"], n["anchor_key"]), []).append(n)

    def _event(n, prefix=""):
        return {
            "at": n["created_at"],
            "who": "%s (%s)" % (n["author"] or "unnamed", n["author_kind"]),
            "what": "%s%s%s anchored %s, %s: %s"
                    % (prefix, n["kind"],
                       " [%s]" % n["severity"] if n["severity"] else "",
                       n["anchor"], n["state"], n["body"]),
        }

    def _notes_at(atype, key):
        return by_anchor.get((atype, key), [])

    for row in decisions:
        if row["kind"] != "candidate":
            continue
        events = [{
            "at": row["created_at"],
            "who": "captured from %s" % row["source_type"],
            "what": "candidate %s recorded" % row["id"],
        }]
        for n in _notes_at("candidate", row["uuid"]):
            events.append(_event(n))
        if row["receipts"]:
            events.append({"at": row["created_at"], "who": "the founder",
                           "what": "%d approval receipt(s) minted"
                                   % row["receipts"]})
        if row["rule"]:
            events.append({"at": row["created_at"], "who": "the store",
                           "what": "became rule %s" % row["rule"]})
            # A note written against the RULE is a note about this decision:
            # the rule is what the decision became, and a reviewer asking what
            # touched the decision has to be shown the argument that happened
            # after approval as well as the one before it.
            for n in _notes_at("rule", row["rule_uuid"]):
                events.append(_event(n, prefix="on the resulting rule, "))
        events.sort(key=lambda e: (e["at"], e["what"]))
        out[row["id"]] = events
    for row in decisions:
        if row["kind"] != "rule":
            continue
        # An approved rule with no candidate row behind it (imported, or its
        # candidate expired) would otherwise have nowhere to hang a note.
        if any(d["kind"] == "candidate" and d["rule_uuid"] == row["uuid"]
               for d in decisions):
            continue
        events = [{"at": row["created_at"], "who": "the store",
                   "what": "rule %s is %s" % (row["id"], row["status"])}]
        for n in _notes_at("rule", row["uuid"]):
            events.append(_event(n))
        events.sort(key=lambda e: (e["at"], e["what"]))
        out[row["id"]] = events
    for item in items:
        record_notes = _notes_at("record", item["id"])
        for dec in item["decisions"]:
            key = "%s#%d" % (item["short"], dec["seq"])
            events = [{
                "at": dec["created_at"],
                "who": item["owner"] or "unrecorded owner",
                "what": "%s: %s" % (dec["topic"] or "untitled", dec["text"]),
            }]
            for n in notes:
                if n["anchor_type"] != "decision":
                    continue
                uuid_part, sep, seq_part = n["anchor_key"].rpartition("#")
                if not sep or seq_part.strip() != str(dec["seq"]):
                    continue
                if uuid_part.strip() and item["id"].startswith(uuid_part.strip()):
                    events.append(_event(n))
            for n in record_notes:
                events.append(_event(
                    n, prefix="on the work record this decision belongs to, "))
            events.sort(key=lambda e: (e["at"], e["what"]))
            out[key] = events
        if record_notes and not item["decisions"]:
            # A record with notes and no recorded decision still has a chain,
            # and dropping it would hide every question asked about the work.
            events = [{"at": item["created_at"],
                       "who": item["owner"] or "unrecorded owner",
                       "what": "work record %s opened: %s"
                               % (item["short"], item["objective"] or item["name"])}]
            for n in record_notes:
                events.append(_event(n))
            events.sort(key=lambda e: (e["at"], e["what"]))
            out[item["short"]] = events
    return out


# ---------------------------------------------------------------------------
# Introspection: the repository and the live schema.
# ---------------------------------------------------------------------------

def _source_files(root, suffixes=(".py",)):
    """Every source file under root, repo-relative and sorted.

    The Documentation folder is skipped by _SKIP_DIRS, and that is not tidiness:
    a generator that reads its own output produces a different answer on every
    run, so regeneration churns and the determinism claim dies."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if not name.endswith(suffixes):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > _MAX_SOURCE_BYTES:
                    continue
            except OSError:  # sbe: allow-silent the walk's own file-size probe racing a file removed mid-walk just excludes it from this generation pass
                continue
            out.append(os.path.relpath(full, root).replace("\\", "/"))
    return sorted(out)


def _read_text(root, rel):
    try:
        with io.open(os.path.join(root, rel), encoding="utf-8",
                     errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        # Reported, never swallowed: a file the scan could not read is a hole in
        # the inventory and the document says so.
        return "# unreadable: %s\n" % exc


def module_inventory(root):
    """Every module and every test file, with what a reader needs to choose one
    to open: how big it is, what it defines, and its first docstring line."""
    modules, tests = [], []
    for rel in _source_files(root):
        text = _read_text(root, rel)
        lines = text.split("\n")
        defs, classes, top = 0, 0, []
        for line in lines:
            m = _DEF_RE.match(line)
            if not m:
                continue
            if m.group(2) == "class":
                classes += 1
            else:
                defs += 1
            if not m.group(1):
                top.append(m.group(3))
        doc = ""
        m = re.search(r'^\s*(?:"""|\'\'\')(.*)$', text, re.MULTILINE)
        if m:
            doc = m.group(1).strip().strip('"').strip("'")
        entry = {"path": rel, "lines": len(lines), "defs": defs,
                 "classes": classes, "doc": doc,
                 "top_level": sorted(top)[:12]}
        base = rel.rsplit("/", 1)[-1]
        if base.startswith("test_") or base.endswith("_test.py"):
            entry["tests"] = len(_TEST_RE.findall(text))
            tests.append(entry)
        else:
            modules.append(entry)
    return {"modules": modules, "tests": tests}


def _import_targets(line):
    """The dotted names one import line could be referring to, best first.

    `from a import b` MEANS a.b when b is a module, and that is the common shape
    of a package importing its own sibling. An earlier version of this scan read
    only the part after `from`, so `from app import pay` inside app/checkout.py
    resolved to the package `app`, found no file called that, and filed the whole
    thing under external dependencies: the one edge the diagram existed to draw
    was the one it dropped. Reproduced on a fixture with exactly those two files.

    Returns (candidates, head) where head is the top level name to count as
    external if no candidate resolves to a file in the tree."""
    m = _IMPORT_RE.match(line)
    if not m:
        return [], ""
    plain = m.group("plain")
    if plain:
        names = [n.strip() for n in plain.split(",") if n.strip()]
        return names, (names[0].split(".")[0] if names else "")
    mod = (m.group("mod") or "").strip()
    tail = re.sub(r"[()*]", " ", m.group("names") or "")
    leaves = [n.strip() for n in tail.split(",") if n.strip()]
    bare = mod.strip(".")
    if not bare:
        # A relative import with no module part (`from . import x`). The leaf is
        # the only name there is, and it names a sibling module.
        return leaves, ""
    return ["%s.%s" % (bare, leaf) for leaf in leaves] + [bare], \
        bare.split(".")[0]


def import_graph(root):
    """The import graph, parsed from the source.

    Only edges BETWEEN modules of this project are kept. A dependency on the
    standard library is real and is not a shape a reader needs a diagram of; an
    edge to a module that is not in the tree would also be an edge to a node the
    diagram cannot draw."""
    files = _source_files(root)
    local = {}
    for rel in files:
        local[rel[:-3].replace("/", ".")] = rel
        local.setdefault(rel.rsplit("/", 1)[-1][:-3], rel)
    edges, external = [], {}
    seen = set()
    for rel in files:
        for line in _read_text(root, rel).split("\n"):
            candidates, head = _import_targets(line)
            if not candidates:
                continue
            target = None
            for name in candidates:
                target = local.get(name) or local.get(name.split(".")[0])
                if target is not None:
                    break
            if target is None:
                if head:
                    external[head] = external.get(head, 0) + 1
                continue
            if target == rel:
                continue
            key = (rel, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append([rel, target])
    # A module loaded by absolute path rather than by import name is invisible
    # to any import scan, and this project loads its siblings exactly that way
    # on purpose (a hostile sys.path must not be able to shadow bm_store). So the
    # loader is read too, and the document says which edges came from which
    # source rather than presenting a partial graph as complete.
    loader_edges = []
    for rel in files:
        text = _read_text(root, rel)
        for name in sorted(set(re.findall(r'_load\(\s*["\']([\w.]+)["\']\s*\)',
                                          text))):
            target = local.get(name)
            if target and target != rel and [rel, target] not in edges:
                loader_edges.append([rel, target])
    return {"nodes": files, "edges": sorted(edges),
            "loader_edges": sorted(loader_edges),
            "external": dict(sorted(external.items()))}


def data_model(store):
    """An entity model INTROSPECTED from the live schema.

    Read from sqlite_master and PRAGMA rather than from the DDL string in
    bm_store.py, because the question a reader has is what the database on this
    machine actually contains, including whatever a migration did to it."""
    tables = []
    for row in _rows(store, "SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        name = row["name"]
        if not re.match(r"^[A-Za-z0-9_]+$", name):
            # A table name that is not an identifier cannot be interpolated into
            # a PRAGMA, and PRAGMA takes no parameters. Reported rather than
            # skipped in silence.
            tables.append({"name": _cell(name), "columns": [],
                           "foreign_keys": [],
                           "note": "name is not a plain identifier; not "
                                   "introspected"})
            continue
        columns = []
        for col in _rows(store, "PRAGMA table_info(%s)" % name):
            columns.append({"name": col["name"], "type": col["type"] or "TEXT",
                            "notnull": bool(col["notnull"]),
                            "pk": bool(col["pk"])})
        fks = []
        for fk in _rows(store, "PRAGMA foreign_key_list(%s)" % name):
            fks.append({"column": fk["from"], "table": fk["table"],
                        "to": fk["to"]})
        tables.append({"name": name, "columns": columns,
                       "foreign_keys": sorted(
                           fks, key=lambda f: (f["table"], f["column"])),
                       "note": ""})
    return {"tables": tables}


def lifecycle_states(store):
    """The recorded lifecycle of a work record: the states the schema allows and
    the transitions this store has actually seen."""
    states = []
    for row in _rows(store, "SELECT sql FROM sqlite_master WHERE type='table' "
                            "AND name='records'"):
        m = re.search(r"state\s+TEXT[^,]*?CHECK\(state\s+IN\s*\(([^)]*)\)",
                      row["sql"] or "", re.IGNORECASE | re.DOTALL)
        if m:
            states = re.findall(r"'([a-z_]+)'", m.group(1))
    seen = _rows(store, "SELECT from_state, to_state, COUNT(*) AS n FROM "
                        "transitions GROUP BY from_state, to_state "
                        "ORDER BY from_state, to_state")
    return {"states": states,
            "observed": [{"from": r["from_state"] or "", "to": r["to_state"],
                          "count": r["n"]} for r in seen]}


def gate_command(root):
    """The command that proves this project works, DISCOVERED in the tree.

    Ordered by how much the project itself declares. Nothing is invented: when
    nothing is found the documents say nothing was found and tell the reader
    that the project does not declare a check command, which is a finding."""
    checks = (
        ("tools/test_all.py", "python3 tools/test_all.py",
         "the project ships a single gate script"),
        ("Makefile", "make test", "the Makefile declares a test target"),
        ("package.json", "npm test", "package.json declares a test script"),
        ("pytest.ini", "python3 -m pytest", "pytest is configured"),
        ("tox.ini", "python3 -m tox", "tox is configured"),
    )
    for rel, cmd, why in checks:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        text = _read_text(root, rel)
        if rel == "Makefile" and not re.search(r"^test\s*:", text, re.M):
            continue
        if rel == "package.json" and '"test"' not in text:
            continue
        return {"command": cmd, "why": why}
    if _source_files(root):
        for rel in _source_files(root):
            base = rel.rsplit("/", 1)[-1]
            if base.startswith("test_"):
                return {"command": "python3 -m unittest discover",
                        "why": "test_*.py files exist but no runner is declared"}
    return {"command": "", "why": "this project declares no test command"}


# ---------------------------------------------------------------------------
# Signals and the tier (section 5.3, I13).
# ---------------------------------------------------------------------------

def signals(root, store, items, decisions, notes, inventory):
    contributors = set()
    for item in items:
        if (item["owner"] or "").strip():
            contributors.add(item["owner"].strip())
    for n in notes:
        if (n["author"] or "").strip() and n["author_kind"] == "human":
            contributors.add(n["author"].strip())
    stamps = sorted([i["created_at"] for i in items]
                    + [i["updated_at"] for i in items]
                    + [n["created_at"] for n in notes])
    span = _days_between(stamps[0], stamps[-1]) if len(stamps) > 1 else None
    gates = len(_rows(store, "SELECT receipt_uuid FROM "
                             "learning_approval_receipts"))
    risk = len([n for n in notes
                if n["kind"] in ("alert", "risk") and n["state"] == "open"])
    tracked = tracked_files(root)
    return {
        "tracked_files": tracked["count"],
        "tracked_files_source": tracked["source"],
        "contributors": len(contributors),
        "contributor_names": sorted(contributors),
        "work_records": len(items),
        "recorded_decisions": len([d for d in decisions
                                   if d["kind"] == "candidate"]),
        "gates": gates,
        "open_risk_flags": risk,
        "modules": len(inventory["modules"]),
        "test_files": len(inventory["tests"]),
        "activity_span": _span_bucket(span),
    }


def tracked_files(root):
    """How many files this project tracks.

    Git's index is read directly when it is there, because that is the number a
    contributor means, and because asking git would take a subprocess this file
    is not allowed to run (I3). Otherwise the source scan is used and the
    document says which of the two answered."""
    index = os.path.join(root, ".git", "index")
    why = "there is no readable git index"
    if os.path.isfile(index):
        paths = None
        try:
            paths = bs._git_index_tracked_paths(index)
        except Exception as exc:
            # Deliberately broad, and deliberately NOT silent. This parses a
            # binary format git owns, so anything it raises is the reason the
            # count came from somewhere else, and the page names that reason
            # rather than presenting a fallback as the real number.
            why = "the git index could not be read (%s)" % type(exc).__name__
        if paths:
            return {"count": len(paths), "source": "the git index"}
        if paths is not None:
            why = "the git index lists no path"
    scanned = _source_files(root, (".py", ".md", ".sh", ".json", ".toml",
                                   ".yml", ".yaml", ".txt", ".cfg", ".ini"))
    return {"count": len(scanned),
            "source": "a scan of the tree, because %s" % why}


def choose_tier(sig, floor=0, explicit=None):
    """The tier, the signals that chose it, and the reason, per section 5.3.

    Raise-only (I13). `floor` is the tier the last generation recorded, so an
    automatic decision can move up and can never move down: documentation that
    quietly got thinner is documentation a reader cannot trust to still contain
    what they read last week. `explicit` is a founder passing --tier, which is
    the one way down and is recorded as such.

    THE FLOOR IS CONSULTED ONLY ON THE AUTOMATIC PATH, and that is not a
    softening of I13, it is what makes `--tier N` deterministic. I13 constrains
    AUTOMATIC decisions; an explicit flag is the founder's answer in both
    directions and needs no floor. Letting the floor into the explicit path made
    two identical `generate --tier 3` runs disagree: the first run recorded the
    chosen tier, the second read it back as a floor, appended a "held at tier N"
    reason and turned "raised to" into "confirmed at", so a folder churned with
    nothing moved in the store, the code or the flag. Every reason on the
    explicit path now compares the flag against the tier the SIGNALS measured,
    which no generation can change."""
    reasons = []
    tier = 1
    for test, why in (
            (sig["tracked_files"] >= 25,
             "%d tracked files, which is past the 25 a single-file fix has"
             % sig["tracked_files"]),
            (sig["work_records"] >= 2,
             "%d work records, so more than one thread of work exists"
             % sig["work_records"]),
            (sig["gates"] >= 1,
             "%d recorded gate(s), so decisions have been put to a human"
             % sig["gates"]),
            (sig["recorded_decisions"] >= 3,
             "%d recorded decisions" % sig["recorded_decisions"])):
        if test:
            tier = max(tier, 2)
            reasons.append("tier 2 because %s" % why)
    for test, why in (
            (sig["contributors"] >= 2,
             "%d distinct contributors, so this is not one person's memory"
             % sig["contributors"]),
            (sig["tracked_files"] >= 200,
             "%d tracked files" % sig["tracked_files"]),
            (sig["gates"] >= 5,
             "%d recorded gates" % sig["gates"]),
            (sig["open_risk_flags"] >= 1,
             "%d open risk flag(s) in the store" % sig["open_risk_flags"]),
            (sig["activity_span"].startswith("over three months"),
             "over three months of recorded activity, so this is long lived")):
        if test:
            tier = max(tier, 3)
            reasons.append("tier 3 because %s" % why)
    if not reasons:
        reasons.append("tier 1 because no signal reached the tier 2 threshold: "
                       "%d tracked files, %d work records, %d gates, %d "
                       "recorded decisions"
                       % (sig["tracked_files"], sig["work_records"],
                          sig["gates"], sig["recorded_decisions"]))
    measured = tier
    source = "automatic"
    if explicit is None:
        if floor and floor > tier:
            reasons.append(
                "held at tier %d because the previous generation recorded tier "
                "%d and an automatic decision may only raise depth (I13); pass "
                "--tier %d to lower it on purpose" % (floor, floor, tier))
            tier = floor
            source = "automatic, held at the recorded floor"
    else:
        if explicit < measured:
            reasons.append("lowered to tier %d by an explicit --tier %d, below "
                           "the tier %d the signals measured, which is the only "
                           "way documentation depth goes down"
                           % (explicit, explicit, measured))
        elif explicit > measured:
            reasons.append("raised to tier %d by an explicit --tier %d, above "
                           "the tier %d the signals measured"
                           % (explicit, explicit, measured))
        else:
            reasons.append("confirmed at tier %d by an explicit --tier %d, "
                           "which is the tier the signals measured"
                           % (explicit, explicit))
        tier = explicit
        source = "explicit --tier"
    return {"tier": tier, "name": TIERS[tier], "reasons": reasons,
            "source": source}


def _floor_from_generated_facts(root):
    """The tier recorded inside the generated facts.json, or 0.

    Kept, and read second, because it is the human-visible copy and because a
    folder generated before the durable record existed still has this one. It is
    NOT sufficient on its own: see recorded_floor."""
    path = os.path.join(root, DOC_ROOT, "90-generated", "facts.json")
    if not os.path.isfile(path):
        return 0
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return 0
    tier = data.get("tier")
    return tier if isinstance(tier, int) and tier in TIERS else 0


def floor_record_path(root):
    """Where the durable tier record lives: beside the store, not inside the
    generated folder.

    It sits next to the store on purpose. The store is this project's memory and
    the floor is a fact about that memory, so the floor travels exactly as far as
    the rows it protects, and no further. Inside `Documentation/` it did not
    travel that far at all: the folder is generated output, this repository's own
    .gitignore excludes it, and the RUNBOOK this engine writes tells the founder
    the rollback is `git rm -r Documentation`. Following that instruction erased
    the only copy of the floor, so the very next automatic run of a tier 3
    project emitted tier 1 and quietly stopped maintaining PROCESS-DIAGRAMS,
    CODE-MAP and the whitepaper. That is an automatic decision lowering depth,
    which is exactly what I13 reserves for an explicit founder flag."""
    return bs.safe_project_path(root, bs.STORE_DIRNAME, "docs-tier.json")


def _floor_from_record(root):
    """The tier the durable record holds, or 0 when there is none or it is not
    readable. Never raises: a missing or damaged floor must not stop a founder
    from generating documentation, it must only stop the tier from sinking."""
    try:
        path = floor_record_path(root)
    except Exception:
        return 0
    if not os.path.isfile(path):
        return 0
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    tier = data.get("tier")
    return tier if isinstance(tier, int) and tier in TIERS else 0


def recorded_floor(root):
    """The tier the last generation wrote, or 0.

    The deeper of the two records answers. The durable record beside the store is
    the one that survives removing the generated folder; facts.json is the
    human-visible copy and the only one an older folder has. Both are written by
    the same run, so they only ever disagree when one of them was removed."""
    return max(_floor_from_record(root), _floor_from_generated_facts(root))


def _relative_floor_record(root):
    """The floor record as a page should name it: a project-relative path, so the
    document reads the same on every machine."""
    try:
        return os.path.relpath(floor_record_path(root), root).replace("\\", "/")
    except Exception:
        return "%s/docs-tier.json" % bs.STORE_DIRNAME


def write_floor_record(root, tier):
    """Record the tier just generated, durably. Returns the path written, or
    None with a reason a caller can print.

    Through the same file funnel every other generated file uses, so this cannot
    become a second, weaker way to write a file. The funnel creates no directory,
    which is right here: the store directory exists whenever generation ran at
    all, and if it does not, the honest outcome is to say the floor could not be
    recorded rather than to invent a place for it."""
    try:
        path = floor_record_path(root)
    except Exception as exc:
        return None, "the path could not be built (%s)" % type(exc).__name__
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        return None, "%s does not exist" % bs.STORE_DIRNAME
    text = json.dumps({"layout_version": 1, "tier": tier},
                      indent=2, sort_keys=True) + "\n"
    try:
        bs.write_generated_document(path, text)
    except Exception as exc:
        # Deliberately broad and deliberately not silent: the documentation is
        # already on disk at this point, and a floor that could not be written is
        # a sentence in the report, not a lost generation.
        return None, "%s: %s" % (type(exc).__name__, exc)
    return path, None


# ---------------------------------------------------------------------------
# The capability status block (positioning loop L1.4, 2026-08-04).
#
# capabilities.status.json is the register a page has to agree with before it
# claims anything. A page that retypes the register drifts from it, which is
# the exact defect the register exists to stop, so README.md carries a
# GENERATED block and this renders it. Same discipline as the rest of this
# file: no timestamp reaches the output, the order is fixed, and two renders of
# one register are byte identical.
#
# This is deliberately NOT part of the Documentation/ engine above. It reads no
# store, writes no generated folder, and runs against a plain checkout that has
# never been initialized, because README.md is a repository page rather than a
# project's own documentation.
# ---------------------------------------------------------------------------

CAPABILITY_REGISTER = "capabilities.status.json"
CAPABILITY_TARGET = "README.md"
CAPABILITY_BEGIN = "<!-- BEGIN GENERATED CAPABILITY STATUS -->"
CAPABILITY_END = "<!-- END GENERATED CAPABILITY STATUS -->"

# The four states, in the order a reader needs them: what is proven first, what
# is not offered last. The meanings mirror the `source_of_truth` sentence in
# capabilities.status.json, and tools/test_bm_docs.py refuses a register entry
# carrying a state outside this set, so the two cannot drift apart in silence.
CAPABILITY_STATES = (
    ("certified", "Certified",
     "proven in this tree today by the evidence named"),
    ("beta", "Beta", "real, with a named gap"),
    ("experimental", "Experimental", "built or planned, not measured"),
    ("unsupported", "Unsupported",
     "not offered, and no plan makes it offered"),
)


def load_capability_register(root):
    """The register at `root`, parsed and checked hard enough to render.

    Every failure is a named refusal rather than a traceback, because the
    caller is a founder at a gate: a missing file, a file that is not JSON and
    an entry with an invented state are three different things to fix and the
    message says which one happened."""
    path = os.path.join(root, CAPABILITY_REGISTER)
    if not os.path.isfile(path):
        raise DocsError(
            "no-capability-register",
            "%s is not in %s, so there is no register to render" % (
                CAPABILITY_REGISTER, root))
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise DocsError("bad-capability-register",
                        "%s does not parse as JSON: %s"
                        % (CAPABILITY_REGISTER, exc))
    except OSError as exc:
        raise DocsError("bad-capability-register",
                        "%s could not be read: %s" % (path, exc))
    entries = data.get("capabilities") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DocsError("bad-capability-register",
                        "%s carries no non-empty capabilities list"
                        % CAPABILITY_REGISTER)
    known = set(state for state, _h, _m in CAPABILITY_STATES)
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DocsError("bad-capability-register",
                            "%s entry %d is not an object"
                            % (CAPABILITY_REGISTER, i))
        where = entry.get("id") or "entry %d" % i
        for key in ("id", "title", "state", "evidence"):
            if not str(entry.get(key) or "").strip():
                raise DocsError("bad-capability-register",
                                "%s: %s is missing or empty in %s"
                                % (where, key, CAPABILITY_REGISTER))
        if entry["state"] not in known:
            raise DocsError(
                "bad-capability-register",
                "%s: state %r is not one of %s. A state nobody defined is how "
                "'mostly works' gets shipped."
                % (where, entry["state"], ", ".join(sorted(known))))
    return data


def _capability_cell(text):
    """One register field as one markdown table cell.

    Nothing is truncated here, unlike _cell above: the evidence sentence IS the
    claim, and half of it would read as a smaller promise than the register
    makes. Only the two things a table cannot survive are handled, a newline
    and a pipe."""
    flat = L.plain_dashes(L.safe_display(text or "", 4000))
    return flat.replace("|", "\\|")


def render_capability_status(data):
    """The generated block for `data`, markers included. Pure.

    Deterministic by construction: the state order is the constant above, the
    order inside a state is the register's own order, and no clock is read. Two
    renders of one register are byte identical, which is what lets a test call
    a stale block stale."""
    entries = data["capabilities"]
    updated = str(data.get("updated") or "").strip()
    lines = [
        CAPABILITY_BEGIN,
        "<!-- Generated from %s by `bm-docs capability-status --write` "
        "(the packaged console script; from a clone, tools/bm_docs.py). "
        "Edit the register, not this block. -->"
        % CAPABILITY_REGISTER,
        "",
        "Four states and no others, read out of `%s`%s: %s."
        % (CAPABILITY_REGISTER,
           (", updated %s" % updated) if updated else "",
           "; ".join("%s means %s" % (state, meaning)
                     for state, _heading, meaning in CAPABILITY_STATES)),
    ]
    for state, heading, meaning in CAPABILITY_STATES:
        rows = [e for e in entries if e["state"] == state]
        lines.append("")
        lines.append("**%s**, %s." % (heading, meaning))
        lines.append("")
        if not rows:
            lines.append("Nothing is recorded in this state.")
            continue
        lines.append("| Capability | What proves it, or why it is not offered |")
        lines.append("|---|---|")
        for entry in rows:
            lines.append("| %s | %s |" % (_capability_cell(entry["title"]),
                                          _capability_cell(entry["evidence"])))
    lines.append("")
    lines.append(CAPABILITY_END)
    return "\n".join(lines)


def _marker_span(text, begin, end, where, code):
    """(start, end) of a generated block in `text`, markers included.

    Exactly one of each marker, in order, or a refusal. A page with two blocks
    would have one of them silently left stale, which is the failure these
    generators exist to remove. One primitive rather than one per block: two
    copies of a splice rule are two chances for one of them to be wrong."""
    starts = [m.start() for m in re.finditer(re.escape(begin), text)]
    ends = [m.end() for m in re.finditer(re.escape(end), text)]
    if len(starts) != 1 or len(ends) != 1:
        raise DocsError(
            code,
            "%s must carry exactly one %s and one %s; it carries %d and %d"
            % (where, begin, end, len(starts), len(ends)))
    if ends[0] <= starts[0]:
        raise DocsError(
            code, "%s carries the END marker before the BEGIN marker" % where)
    return starts[0], ends[0]


def capability_span(text, where=CAPABILITY_TARGET):
    """(start, end) of the generated capability block in `text`."""
    return _marker_span(text, CAPABILITY_BEGIN, CAPABILITY_END, where,
                        "no-capability-markers")


def extract_capability_status(text, where=CAPABILITY_TARGET):
    """The block currently in `text`, markers included."""
    start, end = capability_span(text, where)
    return text[start:end]


def replace_capability_status(text, block, where=CAPABILITY_TARGET):
    """`text` with its generated block replaced by `block`. Everything outside
    the two markers is carried through byte for byte: this command owns the
    block and nothing else on the page."""
    start, end = capability_span(text, where)
    return text[:start] + block + text[end:]


# ---------------------------------------------------------------------------
# The roadmap status block (positioning loop L1.6, 2026-08-04).
#
# The register answers "is this offered". A roadmap has to answer a second
# question the register does not carry: HOW FAR has the claim been checked. So
# docs/ROADMAP.md is written in six proof states, and this maps the register's
# four onto them mechanically.
#
# Mechanically is the whole point. A roadmap is the one page where an item can
# be promoted by wishing, and a mapping written in code is a promotion that
# needs a register edit whose evidence pointer has to resolve to a real file
# before tools/test_bm_docs.py will pass. Same discipline as the block above:
# pure renderer, fixed order, no clock, two renders byte identical.
# ---------------------------------------------------------------------------

ROADMAP_TARGET = "docs/ROADMAP.md"
ROADMAP_BEGIN = "<!-- BEGIN GENERATED ROADMAP STATUS -->"
ROADMAP_END = "<!-- END GENERATED ROADMAP STATUS -->"

# The six proof states, strongest first. Certified sits above verified
# externally without implying it: certified is how strong the proof inside this
# tree is, verified externally is who did the checking, and the page says in its
# own words that the second rung is empty and why. Writing them as one ordered
# ladder is still right for a reader, who wants the strongest claims first.
ROADMAP_STATES = (
    ("certified", "Certified",
     "the register marks it certified, meaning a named evidence file in this "
     "tree plus a test or a job that goes red when the claim stops being true"),
    ("verified externally", "Verified externally",
     "checked by someone outside this project, or on a machine this project "
     "does not own"),
    ("verified in CI", "Verified in CI",
     "the evidence names a continuous integration job, so the check runs "
     "somewhere other than the author's own machine"),
    ("verified locally", "Verified locally",
     "the evidence names a file or a test in this tree, and no continuous "
     "integration job covers it yet"),
    ("implemented", "Implemented",
     "built, with something in the tree describing it, and not measured"),
    ("planned", "Planned",
     "named, with no evidence in the tree behind it yet"),
)

# What tells a beta row's evidence apart: a path under this directory is a job
# that runs off the author's machine. Matched as a literal, because the folder
# is the fact rather than any particular file inside it.
ROADMAP_CI_MARK = ".github/workflows"

# An evidence sentence that POINTS at something: a repository-relative path, or
# a shouting root-level document such as README.md. The same two shapes
# tools/test_bm_docs.py recognizes when it checks that a pointer resolves, so
# "the evidence names a file" means the same thing in both places.
ROADMAP_EVIDENCE_POINTER = re.compile(
    r"[\w.\-]+/[\w.\-]+|\b[A-Z][A-Z0-9_.\-]*\.md\b")


def roadmap_proof_state(entry):
    """The proof state for one register entry, or None when it is a non-goal.

    The mapping, stated once here and rendered into the page itself so a reader
    never has to take it on trust:

      certified     stays certified.
      beta          becomes verified in CI when its evidence names a job under
                    .github/workflows, and verified locally otherwise.
      experimental  becomes implemented when its evidence points at a file in
                    the tree, and planned when it points at nothing.
      unsupported   is not a rung at all. It is listed as a non-goal, because a
                    thing nobody plans to build cannot be somewhere on the way
                    to being built.

    Nothing reaches `verified externally` from the register today, and that
    empty rung is the honest reading rather than an oversight: the closure
    register's X-01 to X-06 are what filling it would take."""
    state = str(entry.get("state") or "").strip()
    evidence = str(entry.get("evidence") or "")
    if state == "certified":
        return "certified"
    if state == "beta":
        return ("verified in CI" if ROADMAP_CI_MARK in evidence
                else "verified locally")
    if state == "experimental":
        return ("implemented" if ROADMAP_EVIDENCE_POINTER.search(evidence)
                else "planned")
    if state == "unsupported":
        return None
    raise DocsError(
        "bad-capability-register",
        "state %r has no proof state on the roadmap. A state nobody mapped is "
        "a row that would silently vanish from the page." % state)


def render_roadmap_status(data):
    """The generated roadmap block for `data`, markers included. Pure.

    Deterministic by construction: the rung order is the constant above, the
    order inside a rung is the register's own order, and no clock is read.

    Every cell carrying register text goes through _capability_cell, which
    strips control characters and escapes a pipe. The proof-state cell is not
    sanitized because it is not register text at all: it is one of the six
    literals in ROADMAP_STATES above, chosen by roadmap_proof_state. Running a
    sanitizer over a constant from this file would read as if the constant were
    untrusted, which would be the wrong thing to teach the next reader."""
    entries = data["capabilities"]
    updated = str(data.get("updated") or "").strip()
    lines = [
        ROADMAP_BEGIN,
        "<!-- Generated from %s by `bm-docs roadmap-status --write` "
        "(the packaged console script; from a clone, tools/bm_docs.py). "
        "Edit the register, not this block. -->"
        % CAPABILITY_REGISTER,
        "",
        "Six proof states, mapped from the four states in `%s`%s: %s."
        % (CAPABILITY_REGISTER,
           (", updated %s" % updated) if updated else "",
           "; ".join("%s means %s" % (state, meaning)
                     for state, _heading, meaning in ROADMAP_STATES)),
        "",
        "The mapping is code rather than judgement. Certified stays certified. "
        "A beta row becomes verified in CI when its evidence names a job under "
        "`%s`, and verified locally otherwise. An experimental row becomes "
        "implemented when its evidence points at a file in this tree, and "
        "planned when it points at nothing. An unsupported row is not a rung "
        "at all and is listed as a non-goal below."
        % ROADMAP_CI_MARK,
    ]
    for state, heading, meaning in ROADMAP_STATES:
        rows = [e for e in entries if roadmap_proof_state(e) == state]
        lines.append("")
        lines.append("**%s**, %s." % (heading, meaning))
        lines.append("")
        if not rows:
            lines.append("Nothing stands at this rung today.")
            continue
        lines.append("| Capability | Proof state | What proves it |")
        lines.append("|---|---|---|")
        for entry in rows:
            lines.append("| %s | %s | %s |"
                         % (_capability_cell(entry["title"]), state,
                            _capability_cell(entry["evidence"])))
    non_goals = [e for e in entries if roadmap_proof_state(e) is None]
    lines.append("")
    lines.append("**Not a goal**, and therefore on no rung: the register's "
                 "unsupported rows, carried here because a roadmap that lists "
                 "only what is coming reads as a promise about everything it "
                 "leaves out.")
    lines.append("")
    if not non_goals:
        lines.append("Nothing is recorded as a non-goal.")
    else:
        lines.append("| Not a goal | Why it is not offered |")
        lines.append("|---|---|")
        for entry in non_goals:
            lines.append("| %s | %s |" % (_capability_cell(entry["title"]),
                                          _capability_cell(entry["evidence"])))
    lines.append("")
    lines.append(ROADMAP_END)
    return "\n".join(lines)


def roadmap_span(text, where=ROADMAP_TARGET):
    """(start, end) of the generated roadmap block in `text`."""
    return _marker_span(text, ROADMAP_BEGIN, ROADMAP_END, where,
                        "no-roadmap-markers")


def extract_roadmap_status(text, where=ROADMAP_TARGET):
    """The block currently in `text`, markers included."""
    start, end = roadmap_span(text, where)
    return text[start:end]


def replace_roadmap_status(text, block, where=ROADMAP_TARGET):
    """`text` with its generated roadmap block replaced by `block`."""
    start, end = roadmap_span(text, where)
    return text[:start] + block + text[end:]


# ---------------------------------------------------------------------------
# The generator.
# ---------------------------------------------------------------------------

class Generator(object):
    """One generation pass over one project.

    Holds the facts, the tier, and the prior contents of the file currently
    being written, which is what makes human preservation (I10) and the
    narrative cache (I12) possible at all."""

    def __init__(self, root, store, explicit_tier=None):
        self.root = root
        self.store = store
        self.items = work_items(store)
        self.decisions = decision_rows(store, root)
        self.notes = note_rows(store)
        # WHERE EVERY FILE-ANCHORED LINE IS NOW, from the store's own report so
        # that this folder and a gate pack cannot disagree about whether a
        # reviewer is looking at the line a note was written about (spec section
        # 6: a note anchored to a line that has since moved is REPORTED). The
        # report never changes a note and never drops one.
        self.anchors = store.note_anchor_reports()
        self.anchor_by_note = {a["note_uuid"]: a for a in self.anchors}
        self.inventory = module_inventory(root)
        self.graph = work_graph(root, self.items)
        self.imports = import_graph(root)
        self.model = data_model(store)
        self.lifecycle = lifecycle_states(store)
        self.gate = gate_command(root)
        self.signals = signals(root, store, self.items, self.decisions,
                               self.notes, self.inventory)
        self.floor = recorded_floor(root)
        self.decision = choose_tier(self.signals, floor=self.floor,
                                    explicit=explicit_tier)
        self.tier = self.decision["tier"]
        self.facts = self._facts()
        self.prior = {"human": [], "prose": {}}
        self.reused = 0
        self.regenerated = 0
        self.rewritten_unverified = 0
        self.preserved = 0

    # -- facts -----------------------------------------------------------

    def _facts(self):
        """The machine-readable facts every page is projected from.

        NO GENERATION TIMESTAMP. Every value here comes from a store row or from
        a file on disk, so two runs a minute apart produce the same bytes. A
        "generated_at" field would make every regeneration a diff."""
        return {
            "layout_version": 1,
            "project_name": os.path.basename(self.root.rstrip(os.sep)),
            "tier": self.tier,
            "tier_name": TIERS[self.tier],
            "tier_source": self.decision["source"],
            "tier_reasons": self.decision["reasons"],
            "signals": self.signals,
            "gate": self.gate,
            "work_items": self.items,
            "schedule": self.graph,
            "decisions": self.decisions,
            "notes": self.notes,
            "note_anchors": self.anchors,
            "lineage": lineage(self.store, self.items, self.decisions,
                               self.notes),
            "modules": self.inventory["modules"],
            "tests": self.inventory["tests"],
            "imports": self.imports,
            "data_model": self.model,
            "lifecycle": self.lifecycle,
            "emitted": [rel for rel, tier, _what in FILES if tier <= self.tier],
        }

    def fact_hash(self, keys):
        subset = {}
        for key in keys:
            subset[key] = self.facts.get(key)
        blob = json.dumps(subset, sort_keys=True, separators=(",", ":"),
                          default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # -- narrative, cached against facts (I12) ---------------------------

    def prose(self, pid, keys, writer):
        """A narrative block, regenerated ONLY when the facts it describes moved,
        or when the recorded text no longer matches its own checksum.

        `writer` is a callable and is NOT called on a cache hit. That is the
        difference between a cache and a comment: a version that always wrote the
        paragraph and then compared it would satisfy the letter of I12 and pay
        the whole cost it exists to avoid, and no test could tell the two apart.

        The body checksum is the other half, and it is what makes the header of
        every generated page true. Reusing recorded text on the strength of a
        FACT hash alone means a paragraph swapped by a bad merge, a stale copy or
        a hand edit is reused for as long as those facts hold still, so a false
        sentence in HANDOVER.md could not be corrected by regenerating. Text that
        fails its own checksum is written again and the founder is told how many
        blocks that was."""
        sha = self.fact_hash(keys)
        record = self.prior["prose"].get(pid)
        intact = (record is not None
                  and record["body_sha"] != ""
                  and record["body_sha"] == _body_hash(record["body"]))
        if record is not None and record["sha"] == sha and intact:
            self.reused += 1
            body = record["body"]
        else:
            if record is not None and record["sha"] == sha and not intact:
                # The facts did not move, so only the text did. Named, because a
                # silently corrected paragraph is a corrected paragraph nobody
                # reviews.
                self.rewritten_unverified += 1
            self.regenerated += 1
            body = _neutralize_prose_markers(writer())
        out = ["<!-- bm-prose: id=%s sha256=%s body=%s -->"
               % (pid, sha, _body_hash(body))]
        out.extend(body.split("\n"))
        out.append(_PROSE_CLOSE)
        return out

    # -- files -----------------------------------------------------------

    def emitted(self):
        return [(rel, tier, what) for rel, tier, what in FILES
                if tier <= self.tier]

    def path_for(self, rel):
        return bs.safe_project_path(self.root, DOC_ROOT, *rel.split("/"))

    def stale(self):
        """Pages a DEEPER tier once wrote that this tier does not maintain.

        Named rather than deleted. Two reasons, and the second is the binding
        one: a generator that removes files can remove a paragraph a human wrote
        inside one (I10 forbids exactly that), and a page silently left behind
        with no warning is a page a reader trusts as current. So both risks are
        answered by saying so out loud, here and in START-HERE."""
        out = []
        for rel, tier, _what in FILES:
            if tier <= self.tier:
                continue
            if os.path.isfile(self.path_for(rel)):
                out.append(rel)
        return out

    def write(self):
        """Every emitted file, through the one funnel. Returns a report."""
        written = []
        for rel, _tier, _what in self.emitted():
            path = self.path_for(rel)
            self.prior = read_existing(path)
            if rel.endswith(".json"):
                text = json.dumps(self.facts, indent=2, sort_keys=True,
                                  default=str) + "\n"
            else:
                text = self.render(rel)
            directory = os.path.dirname(path)
            if not os.path.isdir(directory):
                os.makedirs(directory)
            # Checked BEFORE the write, on the text about to be written, so the
            # reported line numbers are line numbers in the file on disk. The
            # funnel carries a human block through verbatim (I10); this is how a
            # human hears that their own paragraph holds something secret shaped
            # instead of finding it quietly rewritten.
            hits = bs.human_block_secret_hits(text)
            bs.write_generated_document(path, text)
            written.append({"path": DOC_ROOT + "/" + rel,
                            "human_blocks": len(self.prior["human"]),
                            "secret_shaped_human_lines": hits})
            self.preserved += len(self.prior["human"])
        # LAST, and outside the generated folder. Last because a floor recorded
        # for pages that were never written would hold a later run at a depth
        # nothing on disk supports.
        floor_path, floor_error = write_floor_record(self.root, self.tier)
        return {"tier": self.tier, "tier_name": TIERS[self.tier],
                "tier_source": self.decision["source"],
                "tier_reasons": self.decision["reasons"],
                "signals": self.signals, "files": written,
                "stale_from_a_deeper_tier": self.stale(),
                "prose_reused": self.reused,
                "prose_regenerated": self.regenerated,
                "prose_rewritten_unverified": self.rewritten_unverified,
                "human_blocks_preserved": self.preserved,
                "notes": len(self.notes),
                "note_anchors_checked": self.anchors_checked(),
                "note_anchor_problems": [
                    {"id": a["id"], "path": a["path"], "line": a["line"],
                     "state": a["state"], "now_line": a["now_line"],
                     "why": a["why"]}
                    for a in self.anchor_problems()],
                # Separate from the count above, not folded into it: see
                # unchecked_anchors. An anchor nobody could check is a fact a
                # reader needs, and it is not evidence of a check.
                "note_anchors_unchecked": [
                    {"id": a["id"], "path": a["path"], "line": a["line"],
                     "state": a["state"], "now_line": a["now_line"],
                     "why": a["why"]}
                    for a in self.unchecked_anchors()],
                "recorded_floor_before": self.floor,
                "tier_recorded_at": (
                    os.path.relpath(floor_path, self.root).replace("\\", "/")
                    if floor_path else None),
                "tier_record_error": floor_error,
                "critical_path": self.graph["critical_path"],
                "critical_days": self.graph["critical_days"]}

    def render(self, rel):
        renderer = getattr(self, "_doc_" + re.sub(r"[^a-z0-9]+", "_",
                                                  rel.lower()))
        lines = [
            "<!-- GENERATED by tools/bm_docs.py. Regenerate rather than "
            "hand-edit, except inside the human markers, which are preserved "
            "verbatim. -->",
            "",
        ]
        lines.extend(renderer())
        # SBE14 defense in depth: every renderer() line here is already
        # newline-free (they reach the page through _d/safe_display, which
        # strips control characters precisely so a store field cannot start
        # a line of its own, see test_a_store_field_containing_the_marker_
        # cannot_forge_a_human_block), so this should never fire today. It
        # is a second lock on the same door bm_handover.py's render_page
        # closes for real (that generator's fields are scrub-only, not
        # safe_display'd, and DO leak newlines): a future renderer() that
        # forgets to route a field through _d would otherwise let a line
        # equalling HUMAN_BEGIN open what bs._redact_outside_human_blocks
        # reads as a real block, closed by _human_section's own real one
        # below, exempting everything between from redaction (I10 forbids
        # the funnel from telling the difference once that happens).
        generated = "\n".join(lines)
        generated = "\n".join(bs._neutralize_stray_marker(l)
                              for l in generated.split("\n"))
        lines = [generated]
        lines.extend(self._human_section())
        return "\n".join(lines).rstrip("\n") + "\n"

    def _human_section(self):
        out = ["## Human notes, preserved verbatim", "",
               "Anything between the two markers survives every regeneration "
               "untouched. Write here rather than anywhere else in this file.",
               ""]
        blocks = self.prior["human"] or [""]
        for block in blocks:
            out.append(HUMAN_BEGIN)
            if block:
                out.extend(block.split("\n"))
            out.append(HUMAN_END)
            out.append("")
        return out

    # -- notes at their anchors (spec section 6) --------------------------
    #
    # ONE renderer, called from the code map, the work breakdown, the decision
    # index and the handover, so a note reads the same wherever a reader meets
    # it. Grouping is by the RAW anchor fields rather than by the composite
    # display string: the display string truncates a long path, and a truncated
    # key silently matches nothing.

    def notes_at(self, anchor_type, anchor_key):
        """Every note anchored exactly here, oldest first. Never filtered by
        state: a resolved note stays visible, marked resolved, because the fact
        that somebody once raised it is part of what a reader needs."""
        return [n for n in self.notes
                if n["anchor_type"] == anchor_type
                and n["anchor_key"] == anchor_key]

    def note_bullets(self, notes, indent=""):
        """Markdown bullets for a group of notes, with the anchor's current
        whereabouts attached to any file anchor that is no longer simply where it
        was. Returns [] for an empty group so a caller can test it as a
        condition."""
        out = []
        for note in notes:
            line = ("%s- `%s` %s%s by %s (%s), %s, %s: %s"
                    % (indent, note["id"], note["kind"],
                       " [%s]" % note["severity"] if note["severity"] else "",
                       _d(note["author"], 60) or "unnamed", note["author_kind"],
                       note["created_at"], note["state"],
                       _d(note["body"], 300)))
            out.append(line)
            if note["anchor_line"]:
                out.append("%s  - anchored at line %d. %s"
                           % (indent, note["anchor_line"],
                              _d(self._anchor_sentence(note), 300)))
            if note["resolution"]:
                out.append("%s  - resolved: %s"
                           % (indent, _d(note["resolution"], 200)))
            if note["override_by"]:
                out.append("%s  - overridden by %s: %s"
                           % (indent, _d(note["override_by"], 60),
                              _d(note["override_reason"], 200)))
        return out

    def _anchor_sentence(self, note):
        found = self.anchor_by_note.get(note["uuid"])
        if not found:
            return "No anchor report was produced for this note."
        return found["why"]

    def anchor_problems(self):
        """The file anchors a reader has to act on: moved, gone, or in a file
        that could not be read. Reported, never dropped (spec section 6)."""
        return [a for a in self.anchors if a["problem"]]

    def unchecked_anchors(self):
        """The file anchors that were NOT checked, because no fingerprint of the
        line was recorded when the note was written.

        Counted apart from the checked ones deliberately. These anchors are not
        problems (nothing is known to be wrong) and they are not checks either:
        a move of one of these lines cannot be detected at all. Folding them into
        one total made the CLI and the decision index both state that every
        anchor had been checked against the files on disk, which for a store
        carried over by the schema 7 to 8 migration was true of none of them."""
        return [a for a in self.anchors
                if a["state"] == L.ANCHOR_UNCHECKABLE_STATE]

    def anchors_checked(self):
        """How many file anchors were really compared against the file on disk."""
        return len(self.anchors) - len(self.unchecked_anchors())

    def _regen(self):
        cmd = self.command()
        return ("Regenerate with `%s generate`. Every fact below is projected "
                "from the store or read from the files as they are on disk."
                % cmd)

    def command(self):
        """The command a reader can paste. Relative when this tool lives inside
        the project being documented, which is the common case for a repository
        that carries its own toolchain, and absolute otherwise. Never a home
        directory written into a committed document by accident."""
        here = os.path.abspath(__file__)
        root = os.path.abspath(self.root)
        if here.startswith(root + os.sep):
            return "python3 %s" % os.path.relpath(here, root).replace("\\", "/")
        return bs.invocation("bm_docs.py", __file__)

    # -- 00-START-HERE ---------------------------------------------------

    def _doc_00_start_here_md(self):
        w = []
        w.append("# Start here: %s" % _d(self.facts["project_name"], 80))
        w.append("")
        w.append("This folder is generated from what BrotherMode recorded about "
                 "this project plus what is in the files right now. %s"
                 % self._regen())
        w.append("")
        w.append("## The tier this folder is written at")
        w.append("")
        w.append("**Tier %d (%s)**, chosen %s."
                 % (self.tier, TIERS[self.tier], self.decision["source"]))
        w.append("")
        for reason in self.decision["reasons"]:
            w.append("- %s" % reason)
        w.append("")
        w.append("Depth moves up on its own and never down on its own (I13). "
                 "To go down, say so: `%s generate --tier 1`." % self.command())
        w.append("")
        w.append("## The signals that measured it")
        w.append("")
        w.append("| Signal | Value |")
        w.append("| --- | --- |")
        for label, key in (
                ("Tracked files", "tracked_files"),
                ("Counted from", "tracked_files_source"),
                ("Distinct contributors", "contributors"),
                ("Work records", "work_records"),
                ("Recorded decisions", "recorded_decisions"),
                ("Recorded gates", "gates"),
                ("Open risk flags", "open_risk_flags"),
                ("Modules", "modules"),
                ("Test files", "test_files"),
                ("Recorded activity", "activity_span")):
            w.append("| %s | %s |" % (label, _cell(str(self.signals[key]))))
        w.append("")
        if self.signals["contributor_names"]:
            w.append("Contributors the store names: %s."
                     % ", ".join("`%s`" % _cell(n, 60)
                                 for n in self.signals["contributor_names"]))
            w.append("")
        w.append("## Reading order")
        w.append("")
        w.append("| # | File | What it is |")
        w.append("| --- | --- | --- |")
        for i, (rel, _tier, what) in enumerate(self.emitted(), 1):
            w.append("| %d | `%s` | %s |" % (i, rel, what))
        w.append("")
        held = [rel for rel, tier, _w in FILES if tier > self.tier]
        if held:
            w.append("Not written at this tier: %s. They arrive if the signals "
                     "raise the tier, or immediately with `%s generate --tier "
                     "3`." % (", ".join("`%s`" % r for r in held),
                              self.command()))
            w.append("")
        stale = self.stale()
        if stale:
            w.append("**Left behind by a deeper tier and NO LONGER "
                     "MAINTAINED:** %s. A generator here deletes nothing, "
                     "because a file may hold a paragraph a human wrote and "
                     "this project does not destroy those. Read them as "
                     "history, or remove them yourself once you have taken out "
                     "anything you want to keep."
                     % ", ".join("`%s`" % r for r in stale))
            w.append("")
        w.append("## What is generated and what is yours")
        w.append("")
        w.append("Everything outside the human markers is rewritten on every "
                 "run. Everything between them is carried through byte for "
                 "byte, in every file, including this one. That is an invariant "
                 "with a test behind it, not a convention.")
        w.append("")
        return w

    # -- 10-business -----------------------------------------------------

    def _doc_10_business_ba_summary_md(self):
        w = ["# What this project is trying to do", ""]
        w.extend(self.prose(
            "ba-summary", ("project_name", "work_items", "signals", "gate"),
            self._write_ba_summary))
        w.append("")
        w.append("## The work as the store records it")
        w.append("")
        if not self.items:
            w.append("No work record exists yet, so there is nothing to "
                     "summarize. A record is how work becomes visible: "
                     "`bm_store.py claim <name> persistent --objective "
                     "\"<what you are doing>\"`.")
            w.append("")
            return w
        w.append("| Record | State | Objective |")
        w.append("| --- | --- | --- |")
        for item in self.items[:_MAX_LISTED]:
            w.append("| `%s` %s | %s | %s |"
                     % (item["short"], _cell(item["name"], 40), item["state"],
                        _cell(item["objective"], 160) or "(none recorded)"))
        if len(self.items) > _MAX_LISTED:
            w.append("")
            w.append("... and %d more." % (len(self.items) - _MAX_LISTED))
        w.append("")
        return w

    def _write_ba_summary(self):
        states = {}
        for item in self.items:
            states[item["state"]] = states.get(item["state"], 0) + 1
        parts = []
        if not self.items:
            parts.append(
                "The store holds no work record for this project yet, so there "
                "is no recorded objective to summarize. This paragraph will "
                "say something once work is claimed, and until then the honest "
                "answer is that nothing was recorded.")
        else:
            parts.append(
                "%s has %d recorded work record(s): %s."
                % (_d(self.facts["project_name"], 60), len(self.items),
                   ", ".join("%d %s" % (n, s)
                             for s, n in sorted(states.items()))))
            objectives = [i for i in self.items if (i["objective"] or "").strip()]
            if objectives:
                parts.append(
                    "The objectives on those records are what this project said "
                    "it was for, in its own words, and they are quoted in the "
                    "table below rather than paraphrased.")
            else:
                parts.append(
                    "NONE of those records carries an objective. A record with "
                    "no objective cannot be reviewed later, because there is "
                    "nothing to compare the result against.")
        if self.gate["command"]:
            parts.append(
                "Whether it works is not a matter of opinion here: run `%s`, "
                "which is the command this project declares (%s)."
                % (self.gate["command"], self.gate["why"]))
        else:
            parts.append(
                "This project declares no test command, so there is no way to "
                "prove the current state from the outside. That is a finding, "
                "not a formatting problem.")
        return " ".join(parts)

    def _doc_10_business_requirements_md(self):
        w = ["# Requirements", "",
             "Two kinds, both recorded rather than gathered: what the work "
             "records say they are for, and the standing rules a human "
             "approved. %s" % self._regen(), ""]
        w.append("## Objectives, from the work records")
        w.append("")
        objectives = [i for i in self.items if (i["objective"] or "").strip()]
        if not objectives:
            w.append("- none recorded")
        for item in objectives[:_MAX_LISTED]:
            w.append("- **%s** (`%s`, %s): %s"
                     % (_d(item["name"], 60), item["short"], item["state"],
                        _d(item["objective"], 400)))
        w.append("")
        w.append("## Constraints, from approved rules")
        w.append("")
        rules = [d for d in self.decisions if d["kind"] == "rule"]
        w.append("**Approved rules: %d.**" % len(rules))
        w.append("")
        if not rules:
            w.append("- none approved, so nothing constrains the work beyond "
                     "its objectives")
        for rule in rules[:_MAX_LISTED]:
            w.append("- `%s` [%s, %s] when %s, do %s"
                     % (rule["id"], _cell(rule["scope"], 60), rule["status"],
                        _d(rule["trigger"], 160) or "(no trigger)",
                        _d(rule["title"], 200)))
        w.append("")
        w.append("## Checks the records declare")
        w.append("")
        checks = [i for i in self.items if (i["check_cmd"] or "").strip()]
        if not checks:
            w.append("- no work record declares a check command")
        for item in checks[:_MAX_LISTED]:
            w.append("- `%s` %s: `%s`" % (item["short"], _d(item["name"], 40),
                                          _d(item["check_cmd"], 200)))
        w.append("")
        return w

    def _doc_10_business_wbs_md(self):
        w = ["# Work breakdown", "",
             "Projected straight from the work records and the files each one "
             "claimed. Nothing here was typed by hand. %s" % self._regen(), ""]
        if not self.items:
            w.append("No work record exists yet, so there is no breakdown.")
            w.append("")
            return w
        w.append("| # | Record | Name | State | Files claimed | Decisions | "
                 "Checkpoints |")
        w.append("| --- | --- | --- | --- | --- | --- | --- |")
        for i, item in enumerate(self.items, 1):
            w.append("| %d | `%s` | %s | %s | %d | %d | %d |"
                     % (i, item["short"], _cell(item["name"], 40),
                        item["state"], len(item["files"]),
                        len(item["decisions"]), len(item["digests"])))
        w.append("")
        for item in self.items[:_MAX_LISTED]:
            w.append("### %d. %s (`%s`)"
                     % (self.items.index(item) + 1, _d(item["name"], 60),
                        item["short"]))
            w.append("")
            w.append("- State: %s, lifetime %s, owner %s"
                     % (item["state"], item["lifetime"],
                        _d(item["owner"], 60) or "unrecorded"))
            w.append("- Objective: %s"
                     % (_d(item["objective"], 400) or "(none recorded)"))
            w.append("- Opened %s, last touched %s"
                     % (item["created_at"], item["updated_at"]))
            if item["files"]:
                w.append("- Files it holds: %s"
                         % ", ".join("`%s`" % _cell(f, 80)
                                     for f in item["files"][:12]))
                if len(item["files"]) > 12:
                    w.append("  and %d more" % (len(item["files"]) - 12))
            else:
                w.append("- Files it holds: none, so it fences nothing and "
                         "cannot conflict with another record")
            if item["decisions"]:
                w.append("- Decisions recorded against it:")
                for dec in item["decisions"][:12]:
                    w.append("  - #%d %s: %s"
                             % (dec["seq"], _d(dec["topic"], 80) or "untitled",
                                _d(dec["text"], 300)))
            here = self.notes_at("record", item["id"])
            if here:
                w.append("- Notes anchored to this record:")
                w.extend(self.note_bullets(here, indent="  "))
            if item["digests"]:
                last = item["digests"][-1]
                w.append("- Last checkpoint intent: %s"
                         % (_d(last["next_intent"], 300) or "(none recorded)"))
                if (last["blockers"] or "").strip():
                    w.append("- Last recorded blockers: %s"
                             % _d(last["blockers"], 300))
            w.append("")
        return w

    def _doc_10_business_schedule_md(self):
        graph = self.graph
        w = ["# Schedule", "",
             "A gantt of the recorded work, and the critical path through it. "
             "%s" % self._regen(), ""]
        w.append("## How the dependencies were derived")
        w.append("")
        w.append("BrotherMode does not let two records hold the same file at "
                 "once, so a record that claims a path an earlier record also "
                 "claims could only have run after it. Every edge below is that "
                 "fact about the recorded claims. Nothing is asserted, and "
                 "because the edges run forward in creation order the graph "
                 "cannot contain a cycle.")
        w.append("")
        if not graph["nodes"]:
            w.append("No work record exists yet, so there is nothing to "
                     "schedule.")
            w.append("")
            return w
        w.append("```mermaid")
        w.append("gantt")
        w.append("  dateFormat YYYY-MM-DD")
        w.append("  title Recorded work")
        for state in sorted(set(n["state"] for n in graph["nodes"])):
            w.append("  section %s" % state)
            for node in graph["nodes"]:
                if node["state"] != state:
                    continue
                w.append("  %s :%s, %s, %dd"
                         % (_flat(node["name"], 40), node["id"], node["start"],
                            node["days"]))
        w.append("```")
        w.append("")
        w.append("## Dependencies")
        w.append("")
        w.append("**Edges: %d.**" % len(graph["edges"]))
        w.append("")
        if not graph["edges"]:
            w.append("- none: no two records claim overlapping files, so every "
                     "record could have run at any time")
        for a, b, why in graph["edges"][:_MAX_LISTED]:
            w.append("- `%s` must precede `%s`, because both claim %s"
                     % (a, b, _cell(why, 120)))
        w.append("")
        w.append("## Critical path")
        w.append("")
        w.append("Computed as the longest path by summed duration over the "
                 "graph above. It is the chain that decides the earliest "
                 "possible finish: shortening anything off it changes nothing.")
        w.append("")
        if not graph["critical_path"]:
            w.append("- no path: the graph has no nodes")
        else:
            names = dict((n["id"], n["name"]) for n in graph["nodes"])
            w.append("**%d recorded day(s) across %d record(s).**"
                     % (graph["critical_days"], len(graph["critical_path"])))
            w.append("")
            for node_id in graph["critical_path"]:
                w.append("1. `%s` %s" % (node_id, _cell(names.get(node_id, ""),
                                                        60)))
            w.append("")
            w.append("```mermaid")
            w.append("flowchart LR")
            for i, node_id in enumerate(graph["critical_path"]):
                w.append("  n%d[\"%s\"]" % (i, _flat(names.get(node_id,
                                                              node_id), 40)))
            for i in range(len(graph["critical_path"]) - 1):
                w.append("  n%d --> n%d" % (i, i + 1))
            w.append("```")
        w.append("")
        return w

    # -- 20-technical ----------------------------------------------------

    def _doc_20_technical_architecture_md(self):
        w = ["# Architecture", ""]
        w.extend(self.prose("architecture", ("modules", "imports", "gate"),
                            self._write_architecture))
        w.append("")
        w.append("## Modules by size")
        w.append("")
        w.append("| Module | Lines | Classes | Functions | What it says it is |")
        w.append("| --- | --- | --- | --- | --- |")
        ranked = sorted(self.inventory["modules"],
                        key=lambda m: (-m["lines"], m["path"]))
        for mod in ranked[:_MAX_LISTED]:
            w.append("| `%s` | %d | %d | %d | %s |"
                     % (mod["path"], mod["lines"], mod["classes"], mod["defs"],
                        _cell(mod["doc"], 90) or "(no docstring)"))
        if len(ranked) > _MAX_LISTED:
            w.append("")
            w.append("... and %d more, all listed in CODE-MAP.md at tier 3."
                     % (len(ranked) - _MAX_LISTED))
        w.append("")
        w.append("## What depends on what")
        w.append("")
        w.append("The full graph is in DEPENDENCIES.md. The modules nothing "
                 "else imports are the entry points; the ones everything "
                 "imports are the load bearing ones.")
        w.append("")
        incoming = {}
        for _a, b in self.imports["edges"] + self.imports["loader_edges"]:
            incoming[b] = incoming.get(b, 0) + 1
        if not incoming:
            w.append("- no module in this project imports another")
        for path, count in sorted(incoming.items(), key=lambda kv: (-kv[1],
                                                                   kv[0]))[:12]:
            w.append("- `%s` is depended on by %d module(s)" % (path, count))
        w.append("")
        return w

    def _write_architecture(self):
        mods = self.inventory["modules"]
        total = sum(m["lines"] for m in mods)
        parts = []
        if not mods:
            return ("No Python module was found under this project root, so "
                    "there is no code shape to describe. That is what the scan "
                    "found, not a limit of the scan: it walks every directory "
                    "except the generated folder and the usual caches.")
        biggest = sorted(mods, key=lambda m: (-m["lines"], m["path"]))[0]
        parts.append(
            "This project is %d module(s) and about %d lines of Python, "
            "excluding tests. The largest single module is `%s` at %d lines, "
            "which is where a reader looking for the centre of the system "
            "should start."
            % (len(mods), total, biggest["path"], biggest["lines"]))
        edges = len(self.imports["edges"]) + len(self.imports["loader_edges"])
        parts.append(
            "There are %d internal dependency edge(s) between those modules, "
            "%d of them found by parsing imports and %d by reading the "
            "path based loader this project uses so that a hostile sys.path "
            "cannot shadow a module."
            % (edges, len(self.imports["edges"]),
               len(self.imports["loader_edges"])))
        external = self.imports["external"]
        if external:
            top = sorted(external.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
            parts.append(
                "Outside itself it imports %d distinct top level module(s), "
                "most often %s. Whether any of those is a third party "
                "dependency rather than the standard library is a question the "
                "project's own packaging answers, and this scan does not guess."
                % (len(external), ", ".join("`%s`" % n for n, _c in top)))
        return " ".join(parts)

    def _doc_20_technical_data_model_md(self):
        w = ["# Data model", "",
             "Introspected from the live schema on this machine, table by "
             "table, rather than read out of a schema definition in the source. "
             "%s" % self._regen(), ""]
        tables = self.model["tables"]
        w.append("**Tables: %d.**" % len(tables))
        w.append("")
        w.append("```mermaid")
        w.append("erDiagram")
        for table in tables:
            w.append("  %s {" % table["name"])
            for col in table["columns"][:20]:
                flags = []
                if col["pk"]:
                    flags.append("PK")
                if col["notnull"]:
                    flags.append("NOT_NULL")
                w.append("    %s %s%s"
                         % (re.sub(r"[^A-Za-z0-9_]", "_",
                                   col["type"]) or "TEXT",
                            re.sub(r"[^A-Za-z0-9_]", "_", col["name"]),
                            ' "%s"' % " ".join(flags) if flags else ""))
            w.append("  }")
        for table in tables:
            for fk in table["foreign_keys"]:
                w.append("  %s ||--o{ %s : \"%s\""
                         % (re.sub(r"[^A-Za-z0-9_]", "_", fk["table"]),
                            table["name"],
                            re.sub(r"[^A-Za-z0-9_]", "_", fk["column"])))
        w.append("```")
        w.append("")
        for table in tables:
            w.append("### `%s`" % table["name"])
            w.append("")
            if table["note"]:
                w.append(table["note"])
                w.append("")
                continue
            w.append("| Column | Type | Key | Required |")
            w.append("| --- | --- | --- | --- |")
            for col in table["columns"]:
                w.append("| `%s` | %s | %s | %s |"
                         % (_cell(col["name"], 60), _cell(col["type"], 30),
                            "primary" if col["pk"] else "",
                            "yes" if col["notnull"] else "no"))
            w.append("")
            if table["foreign_keys"]:
                for fk in table["foreign_keys"]:
                    w.append("- `%s` points at `%s.%s`"
                             % (_cell(fk["column"], 60), _cell(fk["table"], 60),
                                _cell(fk["to"] or "rowid", 60)))
                w.append("")
        return w

    def _doc_20_technical_dependencies_md(self):
        w = ["# Dependencies", "",
             "Parsed from the source. Two kinds of edge, kept apart on purpose: "
             "an ordinary import, and a module this project loads by absolute "
             "path, which no import scan can see. %s" % self._regen(), ""]
        edges = self.imports["edges"]
        loader = self.imports["loader_edges"]
        w.append("**Internal edges: %d parsed from imports, %d from the path "
                 "loader.**" % (len(edges), len(loader)))
        w.append("")
        w.append("```mermaid")
        w.append("flowchart LR")
        ids = {}
        for path in self.imports["nodes"]:
            if any(path in (a, b) for a, b in edges + loader):
                ids[path] = "m%d" % len(ids)
        for path, node in sorted(ids.items()):
            w.append("  %s[\"%s\"]" % (node, _flat(path, 60)))
        if not ids:
            w.append("  none[\"no internal dependency found\"]")
        for a, b in edges:
            if a in ids and b in ids:
                w.append("  %s --> %s" % (ids[a], ids[b]))
        for a, b in loader:
            if a in ids and b in ids:
                w.append("  %s -. loads .-> %s" % (ids[a], ids[b]))
        w.append("```")
        w.append("")
        w.append("## Every internal edge")
        w.append("")
        if not edges and not loader:
            w.append("- none found")
        for a, b in edges[:_MAX_LISTED]:
            w.append("- `%s` imports `%s`" % (a, b))
        for a, b in loader[:_MAX_LISTED]:
            w.append("- `%s` loads `%s` by path" % (a, b))
        w.append("")
        w.append("## What it reaches for outside itself")
        w.append("")
        external = self.imports["external"]
        w.append("**Distinct top level names: %d.** Whether each is the "
                 "standard library or a third party package is a packaging "
                 "question, and this scan does not guess at it."
                 % len(external))
        w.append("")
        if not external:
            w.append("- none")
        for name, count in sorted(external.items(),
                                  key=lambda kv: (-kv[1], kv[0]))[:_MAX_LISTED]:
            w.append("- `%s`, imported %d time(s)" % (_cell(name, 60), count))
        w.append("")
        return w

    def _doc_20_technical_process_diagrams_md(self):
        w = ["# Process diagrams", "",
             "The lifecycle a work record moves through, and the transitions "
             "this store has actually seen. %s" % self._regen(), ""]
        states = self.lifecycle["states"]
        observed = self.lifecycle["observed"]
        w.append("## The lifecycle the schema allows")
        w.append("")
        if not states:
            w.append("The schema does not constrain the state column, so there "
                     "is no declared lifecycle to draw.")
            w.append("")
        else:
            w.append("```mermaid")
            w.append("stateDiagram-v2")
            for state in states:
                w.append("  %s : %s" % (state, state))
            for row in observed:
                if row["from"] and row["to"]:
                    w.append("  %s --> %s : %d observed"
                             % (row["from"], row["to"], row["count"]))
                elif row["to"]:
                    w.append("  [*] --> %s : %d observed"
                             % (row["to"], row["count"]))
            w.append("```")
            w.append("")
            w.append("States the schema allows: %s."
                     % ", ".join("`%s`" % s for s in states))
            w.append("")
        w.append("## Transitions this store has recorded")
        w.append("")
        if not observed:
            w.append("- none: no record has changed state yet")
        for row in observed:
            w.append("- %s to %s: %d time(s)"
                     % ("(new)" if not row["from"] else "`%s`" % row["from"],
                        "`%s`" % row["to"], row["count"]))
        w.append("")
        w.append("## The gate a change passes through")
        w.append("")
        if self.gate["command"]:
            w.append("```mermaid")
            w.append("flowchart TD")
            w.append("  claim[\"claim the files\"] --> work[\"change them\"]")
            w.append("  work --> check[\"%s\"]" % _flat(self.gate["command"], 60))
            w.append("  check --> green[\"green: checkpoint and continue\"]")
            w.append("  check --> red[\"red: revert to the last green state\"]")
            w.append("  green --> close[\"complete the record\"]")
            w.append("```")
        else:
            w.append("This project declares no check command, so there is no "
                     "gate to draw. That is the finding.")
        w.append("")
        return w

    def _doc_20_technical_code_map_md(self):
        w = ["# Code map", "",
             "Every module and every test file the scan found, with what each "
             "defines at the top level. %s" % self._regen(), ""]
        w.append("## Modules: %d" % len(self.inventory["modules"]))
        w.append("")
        for mod in self.inventory["modules"]:
            w.append("### `%s`" % mod["path"])
            w.append("")
            w.append("%d lines, %d class(es), %d function(s). %s"
                     % (mod["lines"], mod["classes"], mod["defs"],
                        _d(mod["doc"], 200) or "No docstring."))
            w.append("")
            if mod["top_level"]:
                w.append("Top level names: %s."
                         % ", ".join("`%s`" % n for n in mod["top_level"]))
                w.append("")
            # The notes about THIS file, beside this file. They used to appear
            # only in one flat list at the end of the decision index, which is
            # the last place a reader opening the code map would look.
            here = self.notes_at("file", mod["path"])
            if here:
                w.append("Notes anchored here: %d." % len(here))
                w.append("")
                w.extend(self.note_bullets(here))
                w.append("")
        w.append("## Test files: %d" % len(self.inventory["tests"]))
        w.append("")
        if not self.inventory["tests"]:
            w.append("No test file was found. A code map with no tests in it is "
                     "a map of code nothing verifies.")
            w.append("")
        for test in self.inventory["tests"]:
            w.append("- `%s`: %d test(s) across %d lines"
                     % (test["path"], test.get("tests", 0), test["lines"]))
        w.append("")
        return w

    def _doc_20_technical_whitepaper_md(self):
        w = ["# Whitepaper", ""]
        w.extend(self.prose(
            "whitepaper",
            ("project_name", "signals", "work_items", "decisions", "modules",
             "data_model", "gate"),
            self._write_whitepaper))
        w.append("")
        w.append("## The evidence behind every claim above")
        w.append("")
        w.append("| Claim | Where it comes from |")
        w.append("| --- | --- |")
        w.append("| module and line counts | a walk of the tree, listed in "
                 "CODE-MAP.md |")
        w.append("| table and column counts | sqlite introspection, drawn in "
                 "DATA-MODEL.md |")
        w.append("| work records and their states | the records table, "
                 "projected into WBS.md |")
        w.append("| decisions and their states | the candidate and rule tables, "
                 "indexed in 30-decisions/INDEX.md |")
        w.append("| the critical path | longest path over the claim overlap "
                 "graph, shown in SCHEDULE.md |")
        w.append("")
        return w

    def _write_whitepaper(self):
        sig = self.signals
        parts = [
            "%s is %d module(s) and %d test file(s), with %d work record(s) and "
            "%d recorded decision(s) behind it."
            % (_d(self.facts["project_name"], 60), sig["modules"],
               sig["test_files"], sig["work_records"],
               sig["recorded_decisions"]),
            "The point of writing that down is that none of it is anyone's "
            "recollection: every number in this folder is either a row in a "
            "database or a file on disk, and the command that regenerates the "
            "folder is printed on every page so a reader can check rather than "
            "believe.",
        ]
        tables = self.model["tables"]
        if tables:
            columns = sum(len(t["columns"]) for t in tables)
            parts.append(
                "The state lives in %d table(s) and %d column(s), diagrammed in "
                "DATA-MODEL.md straight from the schema this machine is running "
                "rather than from a definition in the source, so a migration "
                "that landed cannot leave the diagram behind."
                % (len(tables), columns))
        if sig["open_risk_flags"]:
            parts.append(
                "There are %d open risk flag(s) in the store right now. They "
                "are listed with their authors in 30-decisions/INDEX.md, and an "
                "unresolved critical one refuses a gate close rather than "
                "appearing as a warning nobody reads."
                % sig["open_risk_flags"])
        else:
            parts.append(
                "No open risk flag stands in the store right now, which is a "
                "statement about what has been written down rather than a "
                "clean bill of health.")
        if self.gate["command"]:
            parts.append(
                "The whole of it is gated on one command, `%s`, and this "
                "project's habit is that nothing is called done until that "
                "command has been run after the last edit."
                % self.gate["command"])
        return " ".join(parts)

    # -- 30-decisions ----------------------------------------------------

    def _doc_30_decisions_index_md(self):
        w = ["# Decisions", "",
             "Every decision the store holds, and the deep dive pack that "
             "reviews it where one has been generated. %s" % self._regen(), ""]
        cands = [d for d in self.decisions if d["kind"] == "candidate"]
        rules = [d for d in self.decisions if d["kind"] == "rule"]
        w.append("**Candidates: %d. Approved rules: %d. Notes: %d.**"
                 % (len(cands), len(rules), len(self.notes)))
        w.append("")
        w.append("## Candidates and their packs")
        w.append("")
        if not cands:
            w.append("No candidate has been captured yet, so nothing is waiting "
                     "on a human.")
            w.append("")
        else:
            w.append("| D | Id | Status | Scope | Proposal | Pack |")
            w.append("| --- | --- | --- | --- | --- | --- |")
            for row in cands:
                pack = "`%s`" % row["pack"] if row["pack"] else "not generated"
                w.append("| D-%d | `%s` | %s | %s | %s | %s |"
                         % (row["index"], row["id"], row["status"],
                            _cell(row["scope"], 40), _cell(row["title"], 100),
                            pack))
            w.append("")
            for row in cands:
                here = self.notes_at("candidate", row["uuid"])
                if row["rule_uuid"]:
                    here = here + self.notes_at("rule", row["rule_uuid"])
                if not here:
                    continue
                w.append("Notes anchored to D-%d (`%s`):"
                         % (row["index"], row["id"]))
                w.append("")
                w.extend(self.note_bullets(sorted(
                    here, key=lambda n: (n["created_at"], n["id"]))))
                w.append("")
            w.append("A pack is generated on demand, never in advance: `%s pack "
                     "<id>` in bm_packs.py writes the eight section review an "
                     "engineer needs. `%s stakes <id>` prints the one line a "
                     "question window carries and generates nothing."
                     % (self._packs_command(), self._packs_command()))
            w.append("")
        w.append("## Approved rules")
        w.append("")
        if not rules:
            w.append("- none approved")
        for row in rules[:_MAX_LISTED]:
            w.append("- `%s` [%s, %s] when %s, do %s"
                     % (row["id"], _cell(row["scope"], 60), row["status"],
                        _d(row["trigger"], 160) or "(no trigger)",
                        _d(row["title"], 200)))
        w.append("")
        w.append("## Decisions recorded against work records")
        w.append("")
        any_dec = False
        for item in self.items:
            for dec in item["decisions"]:
                any_dec = True
                w.append("- `%s#%d` %s (%s): %s"
                         % (item["short"], dec["seq"],
                            _d(dec["topic"], 80) or "untitled",
                            dec["created_at"], _d(dec["text"], 300)))
                # A decision anchor is free text (a decision is a record uuid
                # plus a sequence number and has no single column identity), so
                # it is matched by prefix on the uuid and an exact sequence.
                nested = []
                for note in self.notes:
                    if note["anchor_type"] != "decision":
                        continue
                    uuid_part, sep, seq_part = note["anchor_key"].rpartition("#")
                    if not sep or seq_part.strip() != str(dec["seq"]):
                        continue
                    uuid_part = uuid_part.strip()
                    if uuid_part and item["id"].startswith(uuid_part):
                        nested.append(note)
                w.extend(self.note_bullets(nested, indent="  "))
        if not any_dec:
            w.append("- none recorded")
        w.append("")
        w.append("## Every note, oldest first")
        w.append("")
        w.append("The same notes appear beside the file, the record or the "
                 "decision each one is anchored to. This list is the complete "
                 "one, in the order the concerns were raised.")
        w.append("")
        if not self.notes:
            w.append("- none")
        for note in self.notes[:_MAX_LISTED]:
            w.append("- `%s` %s%s by %s (%s) at %s, anchored %s, %s: %s"
                     % (note["id"], note["kind"],
                        " [%s]" % note["severity"] if note["severity"] else "",
                        _d(note["author"], 60) or "unnamed",
                        note["author_kind"], note["created_at"],
                        note["anchor"], note["state"], _d(note["body"], 300)))
            if note["override_by"]:
                w.append("  - overridden by %s: %s"
                         % (_d(note["override_by"], 60),
                            _d(note["override_reason"], 200)))
        w.append("")
        w.append("## Anchors that no longer resolve")
        w.append("")
        w.append("A note anchored to a line that has since moved is reported "
                 "here rather than dropped, and nothing in this folder deletes a "
                 "note or edits one. Every entry below is a note whose line is "
                 "not where it was written: go and read it before trusting the "
                 "line number beside it.")
        w.append("")
        problems = self.anchor_problems()
        unchecked = self.unchecked_anchors()
        if not problems:
            checked = self.anchors_checked()
            if checked:
                w.append("- none. %d file anchor(s) with a line were checked "
                         "against the files as they are on disk." % checked)
            elif not self.anchors:
                w.append("- none. No note is anchored to a specific line yet.")
            else:
                # NOT "none, all checked". Saying nothing is wrong when nothing
                # could be looked at is the failure this branch exists to avoid.
                w.append("- none found, and none could be looked for: none of "
                         "the %d file anchor(s) with a line carries a "
                         "fingerprint. Read the paragraph below before trusting "
                         "any line number in this folder." % len(self.anchors))
        for found in problems:
            w.append("- `%s` %s%s by %s, anchored `%s` line %d: %s"
                     % (found["id"], found["kind"],
                        " [%s]" % found["severity"] if found["severity"] else "",
                        _d(found["author"], 60) or "unnamed",
                        _cell(found["path"], 80), found["line"],
                        _d(found["why"], 300)))
            if found["now_line"]:
                w.append("  - the line it was written about now reads: `%s`"
                         % _cell(found["text"], 160))
        if unchecked:
            w.append("")
            w.append("### Anchors that could not be checked at all")
            w.append("")
            w.append("%d file anchor(s) below carry no fingerprint of the line "
                     "they point at: the note was written before fingerprints "
                     "existed, or the line it points at was blank. A move of one "
                     "of these lines cannot be detected, so the line number "
                     "beside the note is not evidence that the note is about the "
                     "code now sitting there. These are counted apart from the "
                     "checked anchors, and they are not counted as problems: "
                     "nothing is known to be wrong with them."
                     % len(unchecked))
            w.append("")
            for found in unchecked:
                w.append("- `%s` %s%s by %s, anchored `%s` line %d: NOT CHECKED. "
                         "%s"
                         % (found["id"], found["kind"],
                            " [%s]" % found["severity"] if found["severity"]
                            else "",
                            _d(found["author"], 60) or "unnamed",
                            _cell(found["path"], 80), found["line"],
                            _d(found["why"], 300)))
        w.append("")
        w.append("## Lineage")
        w.append("")
        w.append("Everything that touched a decision, in order, with authors. "
                 "This is a query over the store, not a field anyone maintains.")
        w.append("")
        lin = self.facts["lineage"]
        if not lin:
            w.append("- nothing to trace yet")
        for key in sorted(lin):
            w.append("### `%s`" % key)
            w.append("")
            for event in lin[key]:
                w.append("- %s, %s: %s"
                         % (event["at"], _d(event["who"], 80),
                            _d(event["what"], 300)))
            w.append("")
        return w

    def _packs_command(self):
        here = os.path.abspath(os.path.join(HERE, "bm_packs.py"))
        root = os.path.abspath(self.root)
        if here.startswith(root + os.sep):
            return "python3 %s" % os.path.relpath(here, root).replace("\\", "/")
        return bs.invocation("bm_packs.py", here)

    # -- 40-handover -----------------------------------------------------

    def _doc_40_handover_handover_md(self):
        """Section 5.5. Written for a human with no AI: someone who has never
        spoken to me has to be able to pick this project up from this page."""
        w = ["# Handover", "",
             "This page is written for a person, not for a model. It assumes "
             "you have never spoken to an assistant about this project and are "
             "not going to. Everything in it is either a row this project "
             "recorded or a file you can open. %s" % self._regen(), ""]

        w.append("## 1. What this project is")
        w.append("")
        w.extend(self.prose("handover-what", ("project_name", "work_items",
                                              "modules", "gate"),
                            self._write_handover_what))
        w.append("")

        w.append("## 2. Current state, and the command that proves it")
        w.append("")
        if self.gate["command"]:
            w.append("Run this first, before you believe anything else on this "
                     "page:")
            w.append("")
            w.append("```")
            w.append(self.gate["command"])
            w.append("```")
            w.append("")
            w.append("It is the command this project declares (%s). If it does "
                     "not pass, the state below is the state somebody last "
                     "recorded and not the state you have."
                     % self.gate["why"])
        else:
            w.append("This project declares no test command, so there is "
                     "nothing you can run to prove its state. Treat every claim "
                     "below as a claim.")
        w.append("")
        evidence = [i for i in self.items if (i["evidence"] or "").strip()]
        if evidence:
            w.append("Evidence recorded on the work records:")
            w.append("")
            for item in evidence[:12]:
                w.append("- `%s` %s: %s" % (item["short"],
                                            _d(item["name"], 40),
                                            _d(item["evidence"], 300)))
            w.append("")

        for heading, states, empty in (
                ("3. What is done", ("complete", "adopted"),
                 "nothing is recorded as finished"),
                ("4. What is in flight", ("active",),
                 "nothing is recorded as active"),
                ("5. What is parked, and why", ("parked",),
                 "nothing is parked")):
            w.append("## %s" % heading)
            w.append("")
            rows = [i for i in self.items if i["state"] in states]
            if not rows:
                w.append("- %s" % empty)
            for item in rows:
                w.append("- **%s** (`%s`): %s"
                         % (_d(item["name"], 60), item["short"],
                            _d(item["objective"], 300) or "no objective "
                                                          "recorded"))
                if item["digests"]:
                    last = item["digests"][-1]
                    w.append("  - next intended step: %s"
                             % (_d(last["next_intent"], 300) or "none recorded"))
                    if (last["blockers"] or "").strip():
                        w.append("  - blockers: %s" % _d(last["blockers"], 300))
                if item["files"]:
                    w.append("  - holds: %s"
                             % ", ".join("`%s`" % _cell(f, 60)
                                         for f in item["files"][:8]))
            w.append("")

        w.append("## 6. What is not started")
        w.append("")
        pending = [d for d in self.decisions
                   if d["kind"] == "candidate"
                   and d["status"] in ("pending", "under_review")]
        if not pending:
            w.append("- no decision is waiting on a human, and no work record "
                     "exists that has not been opened. Anything not started is "
                     "therefore not written down anywhere, which is worth "
                     "knowing before you plan.")
        for row in pending:
            w.append("- decision D-%d (`%s`, %s) is waiting on a human: %s"
                     % (row["index"], row["id"], row["status"],
                        _cell(row["title"], 200)))
        w.append("")

        w.append("## 7. Where the code lives, module by module")
        w.append("")
        if not self.inventory["modules"]:
            w.append("- no module was found under this project root")
        for mod in sorted(self.inventory["modules"],
                          key=lambda m: (-m["lines"], m["path"]))[:_MAX_LISTED]:
            w.append("- `%s`, %d lines: %s"
                     % (mod["path"], mod["lines"],
                        _d(mod["doc"], 160) or "no docstring, so open it"))
        w.append("")

        w.append("## 8. How to run it and how to test it")
        w.append("")
        w.append("The runbook has the detail. The short version:")
        w.append("")
        w.append("```")
        w.append(self.gate["command"] or "# this project declares no test "
                                         "command")
        for item in self.items:
            if (item["check_cmd"] or "").strip():
                w.append("%s  # declared by record %s"
                         % (_d(item["check_cmd"], 160), item["short"]))
        w.append("```")
        w.append("")

        w.append("## 9. The known traps")
        w.append("")
        traps = [n for n in self.notes
                 if n["kind"] in ("alert", "risk", "todo", "question")]
        if not traps:
            w.append("- none written down. That is not the same as none "
                     "existing: it means nobody recorded one.")
        for note in traps[:_MAX_LISTED]:
            w.append("- %s%s by %s, %s, anchored %s: %s"
                     % (note["kind"],
                        " [%s]" % note["severity"] if note["severity"] else "",
                        _d(note["author"], 60) or "unnamed", note["state"],
                        note["anchor"], _d(note["body"], 300)))
            found = self.anchor_by_note.get(note["uuid"])
            if found and found["problem"]:
                # A trap whose line has moved is worse than no trap: the reader
                # opens the line, sees ordinary code, and concludes the warning
                # was stale.
                w.append("  - %s" % _d(found["why"], 300))
        w.append("")
        blockers = [(i, d["blockers"]) for i in self.items
                    for d in i["digests"] if (d["blockers"] or "").strip()]
        if blockers:
            w.append("Blockers recorded at checkpoints:")
            w.append("")
            for item, text in blockers[-12:]:
                w.append("- `%s`: %s" % (item["short"], _d(text, 300)))
            w.append("")

        w.append("## 10. The open decisions, and their packs")
        w.append("")
        if not pending:
            w.append("- none open")
        for row in pending:
            w.append("- D-%d `%s`: %s" % (row["index"], row["id"],
                                          _cell(row["title"], 200)))
            w.append("  - reason recorded: %s"
                     % (_d(row["because"], 300) or "none, which is itself worth "
                                                   "asking about"))
            w.append("  - pack: %s"
                     % ("`%s`" % row["pack"] if row["pack"]
                        else "not generated; run `%s pack %s`"
                             % (self._packs_command(), row["id"])))
        w.append("")

        w.append("## 11. Who to ask")
        w.append("")
        people = sorted(set(self.signals["contributor_names"]))
        if not people:
            w.append("- the store names nobody. Owners are recorded on work "
                     "records and authors on notes, and neither carries a name "
                     "here, so there is no one this page can point you at.")
        for person in people:
            w.append("- `%s`" % _cell(person, 80))
        w.append("")
        w.append("## 12. What this page cannot tell you")
        w.append("")
        w.append("Everything here is projected from what was recorded. Work "
                 "that was never claimed, a decision taken in conversation and "
                 "never captured, and a risk somebody noticed and did not write "
                 "down are all invisible to this page, and no amount of "
                 "regeneration will make them appear.")
        w.append("")
        return w

    def _write_handover_what(self):
        parts = []
        objectives = [i for i in self.items if (i["objective"] or "").strip()]
        if objectives:
            parts.append(
                "In the words recorded on its own work records, this project "
                "exists to: %s."
                % "; ".join(_d(i["objective"], 200) for i in objectives[:4]))
        else:
            parts.append(
                "No work record carries an objective, so this project has "
                "never written down what it is for. Read the modules listed in "
                "section 7 and treat their docstrings as the closest thing to "
                "a statement of intent.")
        mods = self.inventory["modules"]
        if mods:
            parts.append(
                "Mechanically it is %d module(s) and %d test file(s)."
                % (len(mods), len(self.inventory["tests"])))
        if self.gate["command"]:
            parts.append("Its one gate is `%s`." % self.gate["command"])
        return " ".join(parts)

    def _doc_40_handover_runbook_md(self):
        w = ["# Runbook", "",
             "How to run this project, test it, and get it back to a known "
             "good state. %s" % self._regen(), ""]
        w.append("## Prove it works")
        w.append("")
        w.append("```")
        w.append(self.gate["command"] or "# no test command is declared by this "
                                         "project")
        w.append("```")
        w.append("")
        if self.gate["command"]:
            w.append("Discovered because %s. Run it after every change, and "
                     "before believing any page in this folder."
                     % self.gate["why"])
        else:
            w.append("Nothing in the tree declares a check command. Until one "
                     "exists, no change to this project can be verified by "
                     "anyone who did not make it.")
        w.append("")
        w.append("## See what BrotherMode is holding")
        w.append("")
        w.append("```")
        store_cmd = self._store_command()
        w.append("%s dashboard      # active records, at a glance" % store_cmd)
        w.append("%s verify         # is the store healthy" % store_cmd)
        w.append("%s handovers      # anything waiting to be picked up"
                 % store_cmd)
        w.append("%s generate       # rewrite this folder" % self.command())
        w.append("```")
        w.append("")
        w.append("## Regenerate this folder")
        w.append("")
        w.append("Safe to run at any time. Everything outside the human markers "
                 "is rewritten; everything inside them is preserved byte for "
                 "byte. Two runs in a row produce identical bytes, so an empty "
                 "diff after regenerating is the expected result and a non "
                 "empty one means a fact moved.")
        w.append("")
        w.append("A generated paragraph edited in place does NOT survive, and "
                 "that is deliberate: each one records a checksum of its own "
                 "text, so a sentence changed by a hand edit, a bad merge or a "
                 "stale copy is written again from the recorded facts and the "
                 "run says how many blocks that was. Write in the human markers, "
                 "which are the one place nothing rewrites.")
        w.append("")
        w.append("## If the store is unhappy")
        w.append("")
        w.append("- `%s verify` names what it found rather than guessing."
                 % store_cmd)
        w.append("- A busy or locked database is a refusal, not a crash: wait "
                 "and retry.")
        w.append("- A damaged database is moved aside rather than written "
                 "over, and the refusal says where it went.")
        w.append("")
        w.append("## Roll this folder back")
        w.append("")
        w.append("Everything under `%s/` is generated output. Removing it "
                 "destroys no recorded fact and no human block that was "
                 "committed with it, and the next generate rebuilds the "
                 "generated parts." % DOC_ROOT)
        w.append("")
        w.append("```")
        w.append("git rm -r %s" % DOC_ROOT)
        w.append("```")
        w.append("")
        w.append("The chosen tier is recorded in `%s`, beside the store rather "
                 "than inside this folder, so the rollback above does not "
                 "quietly shrink the documentation: the next automatic run "
                 "rebuilds at the same depth. Depth only goes down when somebody "
                 "passes `--tier N` on purpose."
                 % _relative_floor_record(self.root))
        w.append("")
        return w

    def _store_command(self):
        here = os.path.abspath(os.path.join(HERE, "bm_store.py"))
        root = os.path.abspath(self.root)
        if here.startswith(root + os.sep):
            return "python3 %s" % os.path.relpath(here, root).replace("\\", "/")
        return bs.invocation("bm_store.py", here)


# ---------------------------------------------------------------------------
# Commands.
# ---------------------------------------------------------------------------

def _root():
    root, _source = bs.require_root()
    return root


def _store():
    return bs.Store(_root(), create=False)


def _tier_flag(kv):
    if "tier" not in kv:
        return None
    raw = kv["tier"]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise DocsError("bad-tier",
                        "--tier takes 1, 2 or 3; got %r" % raw)
    if value not in TIERS:
        raise DocsError("bad-tier",
                        "--tier takes 1 (lean), 2 (standard) or 3 (full); got %d"
                        % value)
    return value


def cmd_tier(argv):
    """The signals and the decision, printed.

    CORRECTED 2026-08-10. This said "Writes nothing." That was FALSE, and the
    falsehood was the defect rather than the writing. It opens a WRITABLE
    Store, whose constructor calls _verify_schema_or_raise(migrate=True), so
    against a database one schema version behind this command MIGRATES IT.
    Demonstrated: a store forced to schema_version 17 and handed to a sibling
    read accessor came back at 18 with a different md5, having failed to find
    what it was asked for.

    It is therefore declared ledger_write in tools/bm_effects.py, which is the
    true classification, not the flattering one.

    OPEN, and named rather than left implied: this command SHOULD be read-only,
    and it cannot be yet. Generator.__init__ calls store.note_anchor_reports(),
    and its row builders call store.list_learning_rules() and
    store.list_notes(). None of the three exists on bm_store.ReadOnlyStore, so
    swapping the constructor raises AttributeError on every normal invocation
    rather than only on the behind-schema case. Adding those three pure-read
    methods to ReadOnlyStore is what would close it, and that is a change to
    the store's own public surface, which deserves its own review rather than
    being smuggled in beside a docstring fix."""
    _pos, kv = _parse(argv, {"tier", "json"}, wants_value=("tier",))
    explicit = _tier_flag(kv)
    root = _root()
    store = _store()
    try:
        gen = Generator(root, store, explicit_tier=explicit)
        payload = {"tier": gen.tier, "tier_name": TIERS[gen.tier],
                   "tier_source": gen.decision["source"],
                   "tier_reasons": gen.decision["reasons"],
                   "recorded_floor": gen.floor,
                   "signals": gen.signals,
                   "would_emit": [rel for rel, _t, _w in gen.emitted()]}
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _out("tier %d (%s), chosen %s"
         % (payload["tier"], payload["tier_name"], payload["tier_source"]))
    for reason in payload["tier_reasons"]:
        _out("  %s" % reason)
    _out("signals:")
    for key in sorted(payload["signals"]):
        _out("  %s: %s" % (key, payload["signals"][key]))
    _out("would emit %d file(s):" % len(payload["would_emit"]))
    for rel in payload["would_emit"]:
        _out("  %s/%s" % (DOC_ROOT, rel))
    return 0


def cmd_generate(argv):
    _pos, kv = _parse(argv, {"tier", "json"}, wants_value=("tier",))
    explicit = _tier_flag(kv)
    root = _root()
    store = _store()
    try:
        gen = Generator(root, store, explicit_tier=explicit)
        report = gen.write()
    finally:
        store.close()
    if kv.get("json"):
        _out(json.dumps(report, indent=2, sort_keys=True))
        return 0
    _out("wrote %d file(s) under %s at tier %d (%s), chosen %s"
         % (len(report["files"]), DOC_ROOT, report["tier"],
            report["tier_name"], report["tier_source"]))
    for reason in report["tier_reasons"]:
        _out("  %s" % reason)
    for entry in report["files"]:
        _out("  %s" % entry["path"])
    _out("  %d human block(s) preserved verbatim"
         % report["human_blocks_preserved"])
    # SAID OUT LOUD RATHER THAN LEFT IN A PAGE. A moved anchor is the one thing
    # in this report a reader has to go and fix, and burying it 300 lines into
    # the decision index is how it stays unfixed.
    summary = ("  %d note(s), %d line anchor(s) checked against the files on disk"
               % (report["notes"], report["note_anchors_checked"]))
    if report["note_anchors_unchecked"]:
        # SAID IN THE SAME BREATH AS THE CHECKED COUNT. Printed on its own line
        # further down, this reads as a footnote to a claim the reader has
        # already believed.
        summary += (", %d that could not be checked at all (no fingerprint was "
                    "recorded for the line)"
                    % len(report["note_anchors_unchecked"]))
    _out(summary)
    for found in report["note_anchors_unchecked"]:
        _out("  ANCHOR %s: note %s at %s line %d. %s"
             % (found["state"].upper(), found["id"], found["path"],
                found["line"], found["why"]))
    for found in report["note_anchor_problems"]:
        _out("  ANCHOR %s: note %s at %s line %d. %s"
             % (found["state"].upper(), found["id"], found["path"],
                found["line"], found["why"]))
    _out("  narrative: %d block(s) reused against unchanged facts, %d "
         "regenerated" % (report["prose_reused"], report["prose_regenerated"]))
    if report["prose_rewritten_unverified"]:
        _out("  REWRITTEN FROM THE FACTS: %d narrative block(s) held text that "
             "could not be verified against a recorded checksum of itself, "
             "either because it did not match or because the record was written "
             "before checksums existed, so it was not trusted and not reused. "
             "Human blocks are never touched by this."
             % report["prose_rewritten_unverified"])
    if report["tier_recorded_at"]:
        _out("  tier %d recorded in %s, which is what stops a later automatic "
             "run from lowering it (I13)"
             % (report["tier"], report["tier_recorded_at"]))
    else:
        _out("  THE TIER COULD NOT BE RECORDED (%s), so a later automatic run "
             "may choose a shallower tier from the signals alone"
             % report["tier_record_error"])
    if report["critical_path"]:
        _out("  critical path: %s (%d recorded day(s))"
             % (" -> ".join(report["critical_path"]), report["critical_days"]))
    for rel in report["stale_from_a_deeper_tier"]:
        _out("  LEFT BEHIND and no longer maintained: %s/%s. Nothing was "
             "deleted; it may hold a human block." % (DOC_ROOT, rel))
    flagged = [(e["path"], e["secret_shaped_human_lines"])
               for e in report["files"] if e["secret_shaped_human_lines"]]
    for path, lines in flagged:
        _out("  KEPT VERBATIM AND WORTH READING: %s line(s) %s inside a human "
             "block look secret shaped. Nothing was rewritten (I10)."
             % (path, ", ".join(str(i) for i in lines)))
    return 0


def cmd_facts(argv):
    """The projected facts, as JSON. The same object facts.json carries, so a
    reader can diff what a page claims against what the store holds."""
    _pos, kv = _parse(argv, {"tier", "json"}, wants_value=("tier",))
    explicit = _tier_flag(kv)
    root = _root()
    store = _store()
    try:
        gen = Generator(root, store, explicit_tier=explicit)
        payload = gen.facts
    finally:
        store.close()
    _out(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def _capability_root(kv):
    """Which tree the capability commands read.

    NOT bs.require_root(). The register and README.md are files of this
    REPOSITORY, not of a project's own store, and this command has to run in a
    fresh checkout that was never initialized. `--root` exists so the suite can
    render a throwaway fixture tree without touching this repository."""
    if kv.get("root"):
        return os.path.abspath(os.path.expanduser(kv["root"]))
    return os.path.dirname(HERE)


class _GeneratedBlock(object):
    """One generated block: the page that owns it, how it renders, and how it
    splices back in.

    Two blocks, one command body. The rules are identical (print, or refuse a
    stale block, or splice a fresh one in) and two copies of that loop are two
    chances for one of them to drift. Every printed string keeps the noun of
    the block it is talking about, so a founder reading a refusal still sees
    which page is wrong."""

    def __init__(self, name, target, render, extract, replace):
        self.name = name
        self.target = target
        self.render = render
        self.extract = extract
        self.replace = replace
        self.command = "%s-status" % name

    def path(self, root):
        """The absolute path, built from the forward-slash target so this works
        on a platform whose separator is not a slash."""
        return os.path.join(root, *self.target.split("/"))


CAPABILITY_BLOCK = _GeneratedBlock(
    "capability", CAPABILITY_TARGET, render_capability_status,
    extract_capability_status, replace_capability_status)

ROADMAP_BLOCK = _GeneratedBlock(
    "roadmap", ROADMAP_TARGET, render_roadmap_status, extract_roadmap_status,
    replace_roadmap_status)


def _read_page(block, root):
    """The text of the page owning `block`, or a named refusal."""
    path = block.path(root)
    if not os.path.isfile(path):
        raise DocsError("no-%s-target" % block.name,
                        "%s is not in %s" % (block.target, root))
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise DocsError("no-%s-target" % block.name,
                        "%s could not be read: %s" % (path, exc))


def _run_block_command(argv, block):
    """The shared body behind capability-status and roadmap-status.

    With no flag it prints the block and writes nothing. `--check` compares the
    block on the page against a fresh render and refuses when they differ, so a
    register edit that never reached the page fails loudly. `--write` replaces
    the block, touching nothing outside the two markers.

    THE STORE FUNNEL IS NOT USED HERE ON PURPOSE. bs.write_generated_document
    redacts everything it writes and protects human blocks, which is right for
    a document this engine owns end to end. README.md and docs/ROADMAP.md are
    pages a human owns; running a redactor over one would rewrite prose this
    command has no business touching. So the write is the house temp-then-
    replace primitive, and the undo is git."""
    _pos, kv = _parse(argv, {"root", "write", "check"}, wants_value=("root",))
    if kv.get("write") and kv.get("check"):
        raise DocsError("bad-flags",
                        "--write and --check ask for opposite things; run one")
    root = _capability_root(kv)
    data = load_capability_register(root)
    fresh = block.render(data)
    count = len(data["capabilities"])
    if not kv.get("write") and not kv.get("check"):
        _out(fresh)
        return 0
    path = block.path(root)
    text = _read_page(block, root)
    current = block.extract(text)
    if kv.get("check"):
        if current == fresh:
            _out("%s: the generated %s block matches %s (%d capabilities)"
                 % (block.target, block.name, CAPABILITY_REGISTER, count))
            return 0
        raise DocsError(
            "%s-stale" % block.command,
            "%s carries a generated %s block that is not what %s renders "
            "today. Run bm-docs %s --write (from a clone: the same subcommand "
            "on tools/bm_docs.py)."
            % (block.target, block.name, CAPABILITY_REGISTER, block.command))
    if current == fresh:
        _out("%s: the generated %s block is already current, nothing written"
             % (block.target, block.name))
        return 0
    try:
        tmp = path + ".%s.tmp" % block.command
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(block.replace(text, fresh))
        os.replace(tmp, path)
    except OSError as exc:
        raise DocsError("%s-write-failed" % block.name,
                        "%s could not be written: %s" % (path, exc))
    _out("%s: rewrote the generated %s block from %s (%d capabilities)"
         % (block.target, block.name, CAPABILITY_REGISTER, count))
    return 0


def cmd_capability_status(argv):
    """Render capabilities.status.json into the generated block in README.md."""
    return _run_block_command(argv, CAPABILITY_BLOCK)


def cmd_roadmap_status(argv):
    """Render capabilities.status.json into the generated block in the
    roadmap, in the six proof states rather than the register's four states."""
    return _run_block_command(argv, ROADMAP_BLOCK)


# ---------------------------------------------------------------------------
# verify-docs: one command a founder can run at a gate (loop L1.7).
#
# The checks below already existed, spread across two subcommands and a suite
# nobody runs by hand in the middle of an edit. Spread out, they get skipped.
# This runs every one of them in a fixed order, reports one PASS or FAIL line
# per lane, and exits nonzero if any lane failed, so the answer to "is the
# documentation honest right now" is one command and one screen.
#
# The identity lane is a small COPY of the loading the docs suite does, on
# purpose: importing a test file from a shipping tool would make the tool
# depend on the tests, and two independent readings of one fact are worth more
# than one reading called from two places.
# ---------------------------------------------------------------------------

IDENTITY_REGISTER = "product.identity.json"
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"
PYPROJECT = "pyproject.toml"

#: Dated records. Their links and their as-of lines are evidence of what was
#: true on a date, not claims about the tree today, so this reads none of them.
VERIFY_RECORD_DIRS = ("docs/closure", "docs/evidence", "docs/superpowers")

#: A page whose NAME carries a date is dated evidence, markdown or html.
VERIFY_DATED_PAGE = re.compile(r"\d{4}-\d{2}-\d{2}.*\.(?:md|html)$")

#: A link a reader is invited to follow. Inline links, reference definitions,
#: and the html attributes that do the same job.
_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_MD_REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")
_HTML_LINK = re.compile(r"(?is)\b(?:href|src)\s*=\s*[\"']([^\"'>]+)[\"']")

#: Targets that are not a path in this tree at all.
_NOT_A_LOCAL_PATH = ("http://", "https://", "mailto:", "tel:", "data:",
                     "javascript:", "#", "//")

#: A line dating its own evidence, and how old that date may be before the line
#: is worth revisiting. Ninety days is a quarter: long enough that a page is not
#: nagged for being written last month, short enough that a claim about a moving
#: field does not sit unread for a year.
_AS_OF = re.compile(r"(?i)as of\b[^.\n]{0,40}?(\d{4}-\d{2}-\d{2})")
STALE_AFTER_DAYS = 90


def current_doc_pages(root):
    """Every page a reader lands on as current state, repository relative with
    forward slashes: the pages under docs/ that are neither dated nor inside a
    record directory, plus README.md."""
    out = []
    docs = os.path.join(root, "docs")
    for dirpath, _dirnames, filenames in os.walk(docs):
        for name in sorted(filenames):
            if not name.endswith((".md", ".html")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name),
                                  root).replace(os.sep, "/")
            if rel.startswith(VERIFY_RECORD_DIRS) or VERIFY_DATED_PAGE.search(name):
                continue
            out.append(rel)
    if os.path.isfile(os.path.join(root, "README.md")):
        out.append("README.md")
    return sorted(set(out))


def _page_links(rel, line):
    """Every link target on one line of one page."""
    if rel.endswith(".html"):
        return _HTML_LINK.findall(line)
    return _MD_LINK.findall(line) + _MD_REF_DEF.findall(line)


def link_offenders(root, pages=None):
    """Every relative link on a current page that resolves to nothing.

    LINKS ONLY, AND THAT SCOPE IS DELIBERATE. A backticked path in prose is not
    a link, and this tree has honest reasons to name a file that is not there:
    docs/ba/ARCHITECTURE.md names the two files the deleted V1 registry used to
    write, docs/RELEASE.md names the planted backdoor an external check used to
    prove the verifier missed it, and docs/BENCHMARK-V1-V2-RC2.md names the
    planted symlink behind the same finding. Requiring those to resolve would
    ask three pages to lie so a checker could pass. A LINK is different: it is
    an invitation to click, and a broken one is a promise the page cannot
    keep."""
    offenders = []
    pages = current_doc_pages(root) if pages is None else pages
    for rel in pages:
        path = os.path.join(root, *rel.split("/"))
        try:
            with io.open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            offenders.append("%s could not be read: %s" % (rel, exc))
            continue
        in_fence = False
        for i, line in enumerate(text.split("\n"), 1):
            # A link inside a fenced block is an EXAMPLE of a link, not an
            # invitation to click one. A page teaching markdown syntax must not
            # fail this check for saying what a link looks like.
            if not rel.endswith(".html") and line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in _page_links(rel, line):
                if target.startswith(_NOT_A_LOCAL_PATH):
                    continue
                bare = target.split("#", 1)[0].split("?", 1)[0]
                if not bare:
                    continue
                if bare.startswith("/"):
                    resolved = os.path.join(root, *bare.lstrip("/").split("/"))
                else:
                    resolved = os.path.join(os.path.dirname(path),
                                            *bare.split("/"))
                if not os.path.exists(resolved):
                    offenders.append("%s:%d points at %s, which is not in the "
                                     "tree" % (rel, i, target))
    return offenders


def stale_source_warnings(root, today, pages=None):
    """Every current line dating its own evidence more than STALE_AFTER_DAYS
    ago. A warning, never a failure: a page whose reading is old is a page to
    revisit, and failing a build over the passage of time would teach people to
    delete the date rather than refresh the reading."""
    warnings = []
    pages = current_doc_pages(root) if pages is None else pages
    for rel in pages:
        try:
            with io.open(os.path.join(root, *rel.split("/")),
                         encoding="utf-8") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent a page that fails to open just gets no staleness check this run; the scan is best-effort over whatever is readable
            continue
        for i, line in enumerate(text.split("\n"), 1):
            found = _AS_OF.search(line)
            if not found:
                continue
            try:
                when = datetime.datetime.strptime(found.group(1),
                                                  "%Y-%m-%d").date()
            except ValueError:  # sbe: allow-silent a malformed as-of date on a line is not this scan's job to flag; only real dates get age-checked
                continue
            age = (today - when).days
            if age > STALE_AFTER_DAYS:
                warnings.append("%s:%d reads as of %s, which is %d days old"
                                % (rel, i, found.group(1), age))
    return warnings


def _read_json(root, rel, offenders):
    """One manifest, or None with the reason appended to `offenders`."""
    path = os.path.join(root, *rel.split("/"))
    if not os.path.isfile(path):
        offenders.append("%s is not in the tree" % rel)
        return None
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        offenders.append("%s does not parse as JSON: %s" % (rel, exc))
    except OSError as exc:
        offenders.append("%s could not be read: %s" % (rel, exc))
    return None


def identity_manifest_offenders(root):
    """Every disagreement between product.identity.json and the manifests a
    package registry, a plugin host and a reader actually key off.

    The comparison tools/test_bm_docs.py makes, minus one: that suite also
    compares tools/bm_project_facts.py's REPO_URL, which is a fact that module
    computes about the tree it ships in rather than about an arbitrary --root,
    so it stays in the suite where it can only ever mean this repository."""
    offenders = []
    identity = _read_json(root, IDENTITY_REGISTER, offenders)
    caps = _read_json(root, CAPABILITY_REGISTER, offenders)
    plugin = _read_json(root, PLUGIN_MANIFEST, offenders)
    marketplace = _read_json(root, MARKETPLACE_MANIFEST, offenders)
    if identity is None:
        return offenders
    pyproject = os.path.join(root, PYPROJECT)
    pip_name = None
    if not os.path.isfile(pyproject):
        offenders.append("%s is not in the tree" % PYPROJECT)
    else:
        try:
            with io.open(pyproject, encoding="utf-8") as fh:
                found = re.search(r'(?m)^name\s*=\s*"([^"]+)"', fh.read())
            pip_name = found.group(1) if found else None
        except OSError as exc:
            offenders.append("%s could not be read: %s" % (PYPROJECT, exc))
        if pip_name is None:
            offenders.append("%s carries no [project] name" % PYPROJECT)

    def same(label, got, want):
        if got != want:
            offenders.append("%s: the identity record says %r, the tree says %r"
                             % (label, want, got))

    if plugin is not None:
        same("plugin id (%s name)" % PLUGIN_MANIFEST, plugin.get("name"),
             identity.get("plugin_id"))
        # homepage, like repository below, names where the product is REACHED
        # today (the M6 cutover moved that to the hub), so it reads the
        # ship-from fact and falls back to repo_url for a single-repository
        # product. Two lanes crossed on this field on 2026-09-03: the manifest
        # moved to the hub while this comparison still read repo_url.
        same("repo url (%s homepage)" % PLUGIN_MANIFEST,
             plugin.get("homepage"),
             identity.get("ships_from_url") or identity.get("repo_url"))
        # The repository field names the repository the plugin is INSTALLED
        # FROM, which the M6 cutover made a different fact from repo_url (the
        # product's own repository, which owns the tags an auditor clones and
        # is what homepage names). A record that declares no ships_from_url
        # is a single-repository product, where the two facts are one.
        same("ships-from url (%s repository)" % PLUGIN_MANIFEST,
             plugin.get("repository"),
             identity.get("ships_from_url") or identity.get("repo_url"))
    if marketplace is not None:
        same("marketplace id (%s name)" % MARKETPLACE_MANIFEST,
             marketplace.get("name"), identity.get("marketplace_id"))
        for entry in marketplace.get("plugins", []):
            same("%s plugins[].name" % MARKETPLACE_MANIFEST, entry.get("name"),
                 identity.get("plugin_id"))
    if caps is not None:
        same("product name (%s)" % CAPABILITY_REGISTER, caps.get("product_name"),
             identity.get("product_name"))
    if pip_name is not None:
        same("pip package (%s [project] name)" % PYPROJECT, pip_name,
             identity.get("pip_package"))
    slug, url = identity.get("repo_slug") or "", identity.get("repo_url") or ""
    if slug not in url:
        offenders.append("repo slug %r does not appear in repo url %r"
                         % (slug, url))
    package = identity.get("python_package") or ""
    if not os.path.isfile(os.path.join(root, package, "__init__.py")):
        offenders.append("python package %r has no __init__.py in the tree"
                         % package)
    contract = identity.get("contract_doc") or ""
    if not os.path.exists(os.path.join(root, *contract.split("/"))):
        offenders.append("contract_doc %r is not in the tree" % contract)
    return offenders


def _block_lane(root, block):
    """(ok, detail) for one generated block, reported as a lane rather than
    raised as a refusal: verify-docs runs every lane before it reports, because
    a founder who fixes one and reruns to find a second has been told half the
    truth twice."""
    try:
        data = load_capability_register(root)
        fresh = block.render(data)
        current = block.extract(_read_page(block, root))
    except DocsError as exc:
        return False, str(exc)
    if current != fresh:
        return False, ("%s carries a generated %s block that is not what %s "
                       "renders today; run the %s subcommand with --write"
                       % (block.target, block.name, CAPABILITY_REGISTER,
                          block.command))
    return True, ("%s matches %s (%d capabilities)"
                  % (block.target, CAPABILITY_REGISTER,
                     len(data["capabilities"])))


def _verify_today(kv):
    """The day the staleness scan measures against. `--today` exists so a test
    can pin the clock; without it this is the only place in this file that
    reads one, and it reaches no generated document."""
    if not kv.get("today"):
        return datetime.date.today()
    try:
        return datetime.datetime.strptime(kv["today"], "%Y-%m-%d").date()
    except ValueError:
        raise DocsError("bad-today",
                        "--today wants an ISO date such as 2026-08-04, not %r"
                        % kv["today"])


def cmd_verify_docs(argv):
    """Run every documentation check in one pass and report one line per lane.

    Four lanes can fail: the two generated blocks, the identity manifests, and
    the links on the current pages. A fifth reading, the age of the evidence a
    page dates for itself, prints warnings and never fails, because the passage
    of time is not a defect a build can fix."""
    _pos, kv = _parse(argv, {"root", "today"}, wants_value=("root", "today"))
    root = _capability_root(kv)
    today = _verify_today(kv)
    lanes = [("capability-status", _block_lane(root, CAPABILITY_BLOCK)),
             ("roadmap-status", _block_lane(root, ROADMAP_BLOCK))]
    identity = identity_manifest_offenders(root)
    lanes.append(("identity-manifests",
                  (not identity,
                   "; ".join(identity) if identity else
                   "%s agrees with the plugin, marketplace, pip and register "
                   "manifests" % IDENTITY_REGISTER)))
    pages = current_doc_pages(root)
    links = link_offenders(root, pages)
    lanes.append(("links",
                  (not links,
                   "; ".join(links) if links else
                   "every relative link on %d current page(s) resolves"
                   % len(pages))))
    for name, (ok, detail) in lanes:
        _out("%s  %s: %s" % ("PASS" if ok else "FAIL", name, detail))
    warnings = stale_source_warnings(root, today, pages)
    for warning in warnings:
        _out("WARNING stale-source: %s" % warning)
    failed = [name for name, (ok, _d) in lanes if not ok]
    _out("verify-docs: %d lane(s) checked, %d failed, %d stale-source "
         "warning(s), measured against %s"
         % (len(lanes), len(failed), len(warnings), today.isoformat()))
    if failed:
        raise DocsError("verify-docs-failed",
                        "%d lane(s) failed: %s" % (len(failed),
                                                   ", ".join(failed)))
    return 0


COMMANDS = {"tier": cmd_tier, "generate": cmd_generate, "facts": cmd_facts,
            "capability-status": cmd_capability_status,
            "roadmap-status": cmd_roadmap_status,
            "verify-docs": cmd_verify_docs}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_docs: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except DocsError as e:
        _err("refused (%s): %s" % (e.reason, e))
        return 2
    except bs.OwnershipRefused as e:
        _err("refused (%s): %s" % (e.reason, e))
        return 2
    except bs.BMStoreError as e:
        _err("bm_docs: %s" % e)
        return 2


def cli():
    """Console-script entry point for a packaged install. Must be callable with
    no arguments, so it wraps main(argv) rather than being it."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

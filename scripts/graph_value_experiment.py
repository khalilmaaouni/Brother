#!/usr/bin/env python3
"""VB-15 / D15: is multi-hop graph retrieval worth anything here, measured.

Benchmark row D15 says NO-DATA: retrieval touches links but no measured use
case proves multi-hop value. The estate's doctrine: a knowledge graph adopted
before a measured use case is adopted for the wrong reason. This experiment is
the measurement, honest in both directions: the legitimate verdict may be that
no use case demands the graph.

TWO ARMS, same queries, same deterministic per-query check (every expected
note present in the arm's hit list, and the hit list bounded, see below):

EVIDENCE KEYING (2026-08-30): the results file records hits and expected
names by STABLE NOTE ID (the `id:` frontmatter field, n-<16hex>), never by
title-derived stems, because a note title is prose and prose can collide
with secret-scanner patterns (measured: a title fragment matched the sk- key
pattern mid-word and refused a push). A note with no id: field falls back to
"stem:<stem>", which says so by its prefix. The queries file stays
human-readable stems; they are mapped to ids at load time from the vault.
Graph traversal itself still runs on stems (wikilink targets ARE stems);
only what lands in the results file is id-keyed.

  FLAT   bm_vault.py recall --fast --limit 6 over the real vault index.
         Hits are the stems of the note paths it prints. --fast so the run is
         deterministic and sub-second (no dense embedder); stated, not hidden.

  GRAPH  the same flat seeds, PLUS entity linking (an entity node whose name
         appears as a whole word in the query, hyphens/underscores read as
         spaces), then a bounded expansion of AT MOST 2 HOPS over
           - typed edges from 30-Entities frontmatter, both directions
             (part_of, depends_on, measures, derives_from, hosted_in)
           - described_by provenance (entity -> document stem)
           - [[wikilinks]] across the whole vault, both directions, SKIPPING
             hub notes with degree > 25 so one index page cannot drag the
             whole vault in.

ANTI-CHEAT BOUND: a graph arm that returned every note would contain every
expected name and prove nothing. Success therefore additionally requires the
arm's hit list to hold at most MAX_HITS (60) stems; an overflowing expansion
scores failure. The flat arm sits far under the bound by construction.

ENTITY PARSING is reimplemented inline (frontmatter field regex + wikilink
stem extraction) rather than importing tools/bm_vault_entity.py, because that
tool lives only on origin/feat/vb-09-ontology, not on BMU main; the ~30 lines
here follow its load() semantics and this docstring states the choice.

CALIBRATION runs before the arms and is recorded in the results file: for
every query the check must PASS on exactly the expected set and must FAIL on
a decoy hit list (the expected set with one name removed, plus two plausible
wrong entities) and on an everything-list longer than MAX_HITS. A check that
cannot fail proves nothing.

No model calls anywhere: both arms are deterministic tool runs, reproducible
at zero token cost. The vault is READ-ONLY to this script. Exit 0 when the
experiment ran end to end (whatever the verdict), 1 on calibration failure,
2 NO-DATA (vault, index or tool missing).

Usage: python3 scripts/graph_value_experiment.py
         [--vault PATH] [--bm-vault PATH] [--queries PATH] [--out PATH]

PRODUCER: this module is the sole producer of its own results file
(benchmarks/graph-value/results-2026-08-30.json by default). main() (line
218) is the only writer: it does the actual open(out_path, "w",
encoding="utf-8") plus json.dump(result, fh, indent=2) at lines 328-329.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
DEFAULT_VAULT = os.path.join(HOME, "Documents", "Kay Vault")
DEFAULT_BM_VAULT = os.path.join(HOME, "Documents", "BrotherModeUp", "tools", "bm_vault.py")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_QUERIES = os.path.join(HERE, "..", "benchmarks", "graph-value", "queries-2026-08-30.json")
DEFAULT_OUT = os.path.join(HERE, "..", "benchmarks", "graph-value", "results-2026-08-30.json")

RELATIONS = ("part_of", "depends_on", "measures", "derives_from", "hosted_in")
PROVENANCE = "described_by"
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
SKIP_DIRS = {".git", ".trash", ".obsidian"}
MAX_HOPS = 2
HUB_DEGREE_CAP = 25
MAX_HITS = 60
FLAT_LIMIT = 6


def norm(name):
    return re.sub(r"[-_]+", " ", name.strip().lower())


def stem_of(target):
    return target.strip().split("/")[-1]


def frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def field(block, name):
    m = re.search(r"(?m)^%s:\s*(.*)$" % re.escape(name), block or "")
    return m.group(1).strip() if m else None


def load_vault_graph(vault):
    """One walk over the vault. Returns (entities, adjacency, ids).

    entities: {stem: entity type} for notes declaring `entity:`.
    adjacency: {stem: set(stem)} merging typed edges (both directions),
    described_by provenance, and wikilinks (both directions, hub-capped).
    ids: {stem: id} from each note's `id:` frontmatter field, for the
    stable evidence keying the module docstring describes."""
    adjacency = {}
    entities = {}
    ids = {}
    wikilink_out = {}
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                print("graph_value_experiment: skipping unreadable note %s: %s"
                      % (path, exc), file=sys.stderr)
                continue
            stem = os.path.splitext(fn)[0]
            block = frontmatter(text)
            note_id = field(block, "id")
            if note_id:
                ids[stem] = note_id.strip().strip('"').strip("'")
            etype = field(block, "entity")
            if etype:
                entities[stem] = etype.strip().strip('"').strip("'")
                for relation in RELATIONS + (PROVENANCE,):
                    for target in WIKILINK.findall(field(block, relation) or ""):
                        other = stem_of(target)
                        adjacency.setdefault(stem, set()).add(other)
                        adjacency.setdefault(other, set()).add(stem)
            body = text[len(block) + 6:] if block else text
            links = {stem_of(t) for t in WIKILINK.findall(body)}
            links.discard(stem)
            if links:
                wikilink_out[stem] = links
    # Wikilinks fold in both directions, skipping hubs on the OUTBOUND side:
    # a note linking to more than HUB_DEGREE_CAP others is an index page and
    # expanding through it would drag in the whole vault.
    for stem, links in wikilink_out.items():
        if len(links) > HUB_DEGREE_CAP:
            continue
        for other in links:
            adjacency.setdefault(stem, set()).add(other)
            adjacency.setdefault(other, set()).add(stem)
    return entities, adjacency, ids


def flat_arm(bm_vault, question):
    """Note stems bm_vault recall prints, plus the raw output for the record."""
    proc = subprocess.run(
        [sys.executable, bm_vault, "recall", "--fast",
         "--limit", str(FLAT_LIMIT), "--query", question],
        capture_output=True, text=True)
    stems = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("/") and line.endswith(".md"):
            stems.append(os.path.splitext(os.path.basename(line))[0])
    return stems, proc.returncode


def entity_seeds(entities, question):
    q = " %s " % norm(question)
    return sorted(s for s in entities
                  if re.search(r"\b%s\b" % re.escape(norm(s)), q))


def expand(seeds, adjacency, hops=MAX_HOPS):
    reached = set(seeds)
    frontier = set(seeds)
    for _ in range(hops):
        nxt = set()
        for node in frontier:
            nxt |= adjacency.get(node, set())
        frontier = nxt - reached
        reached |= frontier
    return reached


def check(expected, hits):
    hit_set = set(hits)
    return all(e in hit_set for e in expected) and len(hit_set) <= MAX_HITS


def calibrate(queries, decoy_pool):
    """Every check must pass on its expected set and fail on decoys.
    Runs in the id keyspace, the same one the arms are scored in."""
    failures = []
    decoy_pool = sorted(decoy_pool) or ["decoy-a", "decoy-b"]
    for q in queries:
        exp = q["expected_ids"]
        if not check(exp, list(exp)):
            failures.append("%s: check fails on its own expected set" % q["id"])
        wrong = [d for d in decoy_pool if d not in exp][:2] + exp[:-1]
        if check(exp, wrong):
            failures.append("%s: check passes on a decoy missing %r"
                            % (q["id"], exp[-1]))
        everything = list(exp) + ["filler-%d" % i for i in range(MAX_HITS + 1)]
        if check(exp, everything):
            failures.append("%s: check passes on an unbounded everything-list"
                            % q["id"])
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description="D15 graph value experiment")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--bm-vault", default=None)
    ap.add_argument("--queries", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    bm_vault = args.bm_vault or DEFAULT_BM_VAULT
    queries_path = args.queries or DEFAULT_QUERIES
    out_path = args.out or DEFAULT_OUT

    if not os.path.isdir(args.vault):
        print("NO-DATA: no vault at %s" % args.vault)
        return 2
    if not os.path.isfile(bm_vault):
        print("NO-DATA: no bm_vault.py at %s" % bm_vault)
        return 2
    with open(queries_path, encoding="utf-8") as fh:
        queries = json.load(fh)["queries"]

    entities, adjacency, ids = load_vault_graph(args.vault)
    if not entities:
        print("NO-DATA: no entity layer in %s, nothing to compare against"
              % args.vault)
        return 2

    def note_key(stem):
        """Stable id for the results file; the fallback says so by prefix."""
        return ids.get(stem, "stem:" + stem)

    no_id_fallbacks = set()

    def keyed(stems):
        out = []
        for s in stems:
            if s not in ids:
                no_id_fallbacks.add(s)
            out.append(note_key(s))
        return out

    for q in queries:
        q["expected_ids"] = keyed(q["expected"])

    cal_failures = calibrate(queries, [note_key(s) for s in entities])
    if cal_failures:
        print("CALIBRATION FAILED:")
        for f in cal_failures:
            print("  " + f)
        return 1
    print("calibration: %d queries x 3 directions, all checks discriminate"
          % len(queries))

    rows = []
    graph_only = []
    flat_wins, graph_wins = 0, 0
    for q in queries:
        flat_stems, rc = flat_arm(bm_vault, q["question"])
        flat_hits = keyed(flat_stems)
        flat_ok = rc == 0 and check(q["expected_ids"], flat_hits)
        seeds = sorted(set(flat_stems) | set(entity_seeds(entities, q["question"])))
        graph_hits = sorted(keyed(expand(seeds, adjacency)))
        graph_ok = check(q["expected_ids"], graph_hits)
        rows.append({"query_id": q["id"], "arm": "flat", "success": flat_ok,
                     "hits": flat_hits})
        rows.append({"query_id": q["id"], "arm": "graph", "success": graph_ok,
                     "hits": graph_hits})
        flat_wins += flat_ok
        graph_wins += graph_ok
        if graph_ok and not flat_ok:
            graph_only.append(q["id"])
        print("%-32s flat=%s graph=%s (graph hits: %d)"
              % (q["id"], "PASS" if flat_ok else "fail",
                 "PASS" if graph_ok else "fail", len(graph_hits)))

    n = len(queries)
    if graph_only:
        verdict = ("MEASURED USE CASE DEMANDS THE GRAPH: %d of %d queries "
                   "answered only by the graph arm (%s); flat %d/%d, graph "
                   "%d/%d." % (len(graph_only), n, ", ".join(graph_only),
                               flat_wins, n, graph_wins, n))
    else:
        verdict = ("NO MEASURED USE CASE DEMANDS THE GRAPH: every query the "
                   "graph arm answered, flat retrieval answered too (flat "
                   "%d/%d, graph %d/%d)." % (flat_wins, n, graph_wins, n))
    print("VERDICT: " + verdict)

    result = {
        "date": "2026-08-30",
        "node": "VB-15",
        "benchmark_row": "d15",
        "design": {"flat": "bm_vault.py recall --fast --limit %d" % FLAT_LIMIT,
                   "graph": "flat seeds + query-linked entities, <=%d hops over "
                            "typed edges (both directions), described_by, and "
                            "wikilinks (both directions, hub degree cap %d)"
                            % (MAX_HOPS, HUB_DEGREE_CAP),
                   "check": "all expected notes present and <=%d hits" % MAX_HITS,
                   "keying": "hits and expected sets are recorded by stable "
                             "note id (the id: frontmatter field); a note "
                             "with no id is recorded as stem:<stem>",
                   "notes_without_id": sorted(no_id_fallbacks),
                   "entities_found": len(entities)},
        "calibration": "PASS: every check passes on its expected set, fails on "
                       "a decoy missing one expected name, and fails on an "
                       "unbounded everything-list",
        "rows": rows,
        "summary": {"queries": n, "flat_success": flat_wins,
                    "graph_success": graph_wins, "graph_only": graph_only},
        "verdict": verdict,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    print("results: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""bm_vault_curate: a curation queue for the vault's nearly-empty typed-edge graph.

The vault's graph machinery is rich (bm_vault_graph resolves, gates, and traverses
typed edges) while the edges themselves are nearly empty: 17 relates, 2 supersedes,
1 contradicts across 842 notes at the time this was written. This tool feeds a human
curator a ranked queue of candidate pairs from THREE independent finders, because one
signal's blind spot is another's catch:

  duplicate   the same title-overlap scan bm_vault_distill's scan-duplicates already
              runs (content words, stopwords out), reused by importing that module,
              so the two tools can never drift apart on what "close" means.
  jaccard     link prediction over shared wikilink neighbors: two notes whose
              neighborhoods overlap heavily probably belong together even when their
              titles share nothing.
  cocitation  second-order backlinks: two notes frequently cited TOGETHER by the
              same sources are related in the eyes of everyone who wrote about them.

  find     runs the three finders, writes the queue file. Deduped against existing
           typed edges and against every remembered rejection. --owner NAME tags
           every candidate built by this run (default NO-DATA, never guessed); a
           candidate that survives a later find keeps its ORIGINAL build timestamp,
           so governance ages it from first sighting, not from the latest re-find.
  list     renders the queue ranked: pairs found by 2+ finders first.
  governance --queue FILE [--cap N] [--max-age-days D]
           reports count, oldest-candidate age, and per-owner counts. A NAMED
           finding and exit 1 for each of OVER CAP / OVER AGE; exit 0 under both.
           A candidate built before timestamps existed reports NO-DATA age,
           distinct from an age of zero. Missing or unreadable queue: NO-DATA
           exit 2. Read-only: never touches the vault or the queue file.
  accept   --pair A,B --edge relates|supersedes --by NAME [--apply]
           Dry by default; the vault write happens ONLY under --apply, one pair per
           invocation, because curation is deliberate, not bulk. The edge lands in
           the OLDER note's frontmatter APPEND-ONLY (nothing is ever removed), and
           for supersedes it points at the winner, per scan-duplicates' own printed
           convention ("add the field to the OLDER note, pointing at the one that
           should win"). Who accepted what, and when, is recorded in the queue
           file's audit log. REFUSED without --by: an unattributed curation is
           worth nothing at review time.
  reject   --pair A,B --by NAME
           Remembers the rejection with who and when; a rejected pair never
           reappears in find.

The queue file lives OUTSIDE the vault (default ~/.claude/bm_vault_curation.json):
this tool does not hold the vault fence, and under everything but accept --apply it
never writes a vault byte.

Exit 0 on success, 2 on a real error or refusal, 3 on NO-DATA.
Python 3.9, standard library only, no network.
"""
import argparse
import importlib.util
import json
import os
import posixpath
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the config directory is resolved by brother_paths, the one seam
# that knows which coding client is running (docs/codex/HOOKS-MAPPING.md).
# Loaded from beside this file because tools/ is not a package.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
DEFAULT_QUEUE = brother_paths.config_path("bm_vault_curation.json")

DUP_THRESHOLD = 0.5          # scan-duplicates' own default
JACCARD_THRESHOLD = 0.25
JACCARD_MIN_SHARED = 2       # one shared neighbor is coincidence, two is a signal
COCITE_MIN_SOURCES = 2
NO_OWNER = "NO-DATA"          # never guessed; the founder or curator names it
DEFAULT_CAP = 50              # governance default: queue size before it flags OVER CAP
DEFAULT_MAX_AGE_DAYS = 30     # governance default: candidate age before OVER AGE
# ponytail: an index-shaped note that links half the vault would nominate every pair
# it touches; cap co-citation sources by out-degree, raise if a real hub is missed.
MAX_COCITE_OUT_DEGREE = 60

H1 = re.compile(r"^#\s+(.+)$", re.M)
FRONT_CREATED = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
EDGE_FIELDS = ("relates", "supersedes")


def _load_module(name):
    """Dynamic import by path, the same pattern bm_vault.py uses for its contract
    modules: tools/ is not a package, and a pip install lays these out flat."""
    path = os.path.join(TOOLS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pair_key(a, b):
    return tuple(sorted((a, b)))


def _load_queue(path):
    if not os.path.isfile(path):
        return {"generated": None, "vault": None, "queue": [],
                "rejections": [], "audit": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        raise RuntimeError("cannot read queue file %s: %s" % (path, e))
    for key, default in (("queue", []), ("rejections", []), ("audit", [])):
        data.setdefault(key, default)
    return data


def _save_queue(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _rejected_pairs(data):
    return set(_pair_key(*r["pair"]) for r in data.get("rejections", [])
               if isinstance(r.get("pair"), list) and len(r["pair"]) == 2)


# ---------------------------------------------------------------- graph context

def _graph_context(graph, vault):
    """Everything the two graph finders need, built once from bm_vault_graph's own
    loaders so link resolution here is exactly the gate's resolution."""
    notes = graph._load_notes(vault)
    if not notes:
        return None
    exact, by_basename = graph._build_indices(notes)
    file_index = graph._build_file_index(vault)
    typed = graph._typed_edges(notes, exact, by_basename, file_index)

    edge_pairs = set()
    for src, targets in typed["supersedes"].items():
        for t in targets:
            edge_pairs.add(_pair_key(src, t))
    for field in ("relates", "contradicts"):
        for src, targets in typed[field].items():
            for t in targets:
                edge_pairs.add(_pair_key(src, t))

    out_links = {}   # stem -> set of resolved note stems (structural sources only)
    titles = {}
    created = {}
    for n in notes:
        stem = n["stem"]
        h1 = H1.search(n["body"])
        titles[stem] = h1.group(1).strip() if h1 else posixpath.basename(stem)
        m = FRONT_CREATED.search(graph._frontmatter_block(n["body"]))
        if m:
            created[stem] = m.group(1)
        # Same exclusions the structural-orphan measure already applies: a template's
        # placeholder links and a generated catalog's everything-links are machinery,
        # not evidence two notes belong together.
        if n["relpath"].startswith(graph.TEMPLATES_PREFIX) or graph._is_generated(n["body"]):
            continue
        targets = set()
        for raw in graph.WIKILINK.findall(n["body"]):
            cleaned = graph._clean_link(raw)
            if not cleaned:
                continue
            target, kind = graph._resolve(cleaned, stem, exact, by_basename, file_index)
            if target is not None and kind in ("note", "ambiguous") and target != stem:
                targets.add(target)
        if targets:
            out_links[stem] = targets

    linked_pairs = set()
    for src, targets in out_links.items():
        for t in targets:
            linked_pairs.add(_pair_key(src, t))

    return {"notes": notes, "by_basename": by_basename, "titles": titles,
            "created": created, "out_links": out_links,
            "edge_pairs": edge_pairs, "linked_pairs": linked_pairs}


# --------------------------------------------------------------------- finders

def find_duplicates(distill, vault):
    """scan-duplicates' own logic, via its own functions: pairwise content-word title
    overlap over 40-Failures, typed-linked pairs excluded. Stems returned in the
    graph's vault-relative form so all three finders speak one pair language."""
    results = {}
    notes = distill._failure_notes(vault)
    for i, a in enumerate(notes):
        for b in notes[i + 1:]:
            if b["slug"] in a["linked"] or a["slug"] in b["linked"]:
                continue
            score = distill._content_overlap(a["title"], b["title"])
            if score >= DUP_THRESHOLD:
                key = _pair_key("40-Failures/" + a["slug"], "40-Failures/" + b["slug"])
                results[key] = round(score, 3)
    return results


def find_jaccard(ctx):
    """Link prediction over shared neighbors: neighbor sets are undirected (a link in
    either direction makes two notes neighbors), candidates are limited to pairs that
    actually share a neighbor, and pairs already directly linked are skipped because
    predicting an existing link nudges nobody."""
    neighbors = {}
    for src, targets in ctx["out_links"].items():
        neighbors.setdefault(src, set()).update(targets)
        for t in targets:
            neighbors.setdefault(t, set()).add(src)
    inverted = {}
    for node, nbs in neighbors.items():
        for nb in nbs:
            inverted.setdefault(nb, set()).add(node)
    results = {}
    for shared_of, nodes in inverted.items():
        for a, b in combinations(sorted(nodes), 2):
            key = _pair_key(a, b)
            if key in results or key in ctx["linked_pairs"]:
                continue
            inter = neighbors[a] & neighbors[b]
            if len(inter) < JACCARD_MIN_SHARED:
                continue
            union = neighbors[a] | neighbors[b]
            score = len(inter) / len(union)
            if score >= JACCARD_THRESHOLD:
                results[key] = round(score, 3)
    return results


def find_cocitation(ctx):
    """Second-order backlinks: count, for every pair of notes, how many distinct
    sources cite both. Score is that raw source count."""
    counts = Counter()
    for src, targets in ctx["out_links"].items():
        if len(targets) > MAX_COCITE_OUT_DEGREE:
            continue
        for a, b in combinations(sorted(targets), 2):
            counts[_pair_key(a, b)] += 1
    return {key: n for key, n in counts.items()
            if n >= COCITE_MIN_SOURCES and key not in ctx["linked_pairs"]}


def _combined(finders):
    """One comparable number across three unlike scales: duplicate and jaccard are
    already 0..1, a co-citation count is squashed into 0..1."""
    total = 0.0
    for name, score in finders.items():
        total += min(1.0, score / 4.0) if name == "cocitation" else score
    return round(total, 3)


# -------------------------------------------------------------------- commands

def cmd_find(args):
    vault = _vault_root(args.vault)
    graph = _load_module("bm_vault_graph")
    distill = _load_module("bm_vault_distill")
    ctx = _graph_context(graph, vault)
    if ctx is None:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 3
    try:
        data = _load_queue(args.queue)
    except RuntimeError as e:
        print("bm_vault_curate: %s" % e)
        return 2
    rejected = _rejected_pairs(data)
    # A pair that survives across find runs keeps its ORIGINAL build timestamp:
    # governance ages a candidate from when it first entered the queue, not from
    # whichever run last happened to re-find it.
    prev_built = {_pair_key(*e["pair"]): e["built"] for e in data.get("queue", [])
                  if isinstance(e.get("pair"), list) and len(e["pair"]) == 2
                  and e.get("built")}

    per_finder = {"duplicate": find_duplicates(distill, vault),
                  "jaccard": find_jaccard(ctx),
                  "cocitation": find_cocitation(ctx)}
    merged = {}
    for name, results in per_finder.items():
        for key, score in results.items():
            if key in rejected or key in ctx["edge_pairs"]:
                continue
            merged.setdefault(key, {})[name] = score

    now = _now()
    queue = []
    for key, finders in merged.items():
        a, b = key
        queue.append({"pair": [a, b],
                      "titles": [ctx["titles"].get(a, posixpath.basename(a)),
                                 ctx["titles"].get(b, posixpath.basename(b))],
                      "finders": finders,
                      "combined": _combined(finders),
                      "built": prev_built.get(key, now),
                      "owner": args.owner})
    queue.sort(key=lambda e: (-len(e["finders"]), -e["combined"], e["pair"]))
    data["queue"] = queue
    data["generated"] = now
    data["vault"] = vault
    _save_queue(args.queue, data)
    print("notes scanned: %d" % len(ctx["notes"]))
    for name in ("duplicate", "jaccard", "cocitation"):
        print("%s candidates: %d" % (name, len(per_finder[name])))
    print("queued after dedup (existing edges, %d remembered rejections): %d"
          % (len(rejected), len(queue)))
    print("queue written: %s" % args.queue)
    return 0


def cmd_list(args):
    try:
        data = _load_queue(args.queue)
    except RuntimeError as e:
        print("bm_vault_curate: %s" % e)
        return 2
    queue = data["queue"]
    if not queue:
        print("NO-DATA: queue is empty (run find first): %s" % args.queue)
        return 3
    print("curation queue (%s, generated %s): %d pair(s), 2+ finder pairs first"
          % (args.queue, data.get("generated") or "?", len(queue)))
    for e in queue:
        finders = ", ".join("%s=%s" % kv for kv in sorted(e["finders"].items()))
        print("  [%d finder%s, combined %.3f] %s <-> %s   (%s)"
              % (len(e["finders"]), "s" if len(e["finders"]) != 1 else "",
                 e["combined"], e["pair"][0], e["pair"][1], finders))
    return 0


def _emit_json(tool, verdict, counts, findings):
    """The one shared --json envelope across every bm_vault_* reporting tool
    (VB7-02): {tool, verdict, counts, findings, schema_version}. verdict is
    always "PASS", "FAIL" or "NO-DATA" and always matches the process exit
    code the caller returns; counts/findings never change the exit code,
    only its format."""
    print(json.dumps({
        "tool": tool,
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "schema_version": 1,
    }, indent=2, sort_keys=True))


def cmd_governance(args):
    """count, oldest-candidate age, per-owner counts; a NAMED finding and exit 1 for
    each of OVER CAP / OVER AGE, exit 0 under both. A candidate built before this
    tool tracked timestamps has NO-DATA age, kept distinct from an age of zero."""
    json_out = getattr(args, "json", False)
    if not os.path.isfile(args.queue):
        msg = "NO-DATA: queue file not found: %s" % args.queue
        if json_out:
            _emit_json("bm_vault_curate.governance", "NO-DATA", {},
                       [{"kind": "no_data", "path": args.queue, "detail": msg}])
        else:
            print(msg)
        return 2
    try:
        data = _load_queue(args.queue)
    except RuntimeError as e:
        msg = "NO-DATA: %s" % e
        if json_out:
            _emit_json("bm_vault_curate.governance", "NO-DATA", {},
                       [{"kind": "no_data", "path": args.queue, "detail": str(e)}])
        else:
            print(msg)
        return 2
    queue = data.get("queue", [])
    if not queue:
        if json_out:
            _emit_json("bm_vault_curate.governance", "PASS", {"count": 0}, [])
        else:
            print("queue: %s" % args.queue)
            print("count: 0 (queue is empty)")
        return 0

    count = len(queue)
    owners = Counter(e.get("owner") or NO_OWNER for e in queue)
    now = datetime.now(timezone.utc)
    dated = []
    undated = 0
    for e in queue:
        built = e.get("built")
        if not built:
            undated += 1
            continue
        try:
            dt = datetime.fromisoformat(built)
        except ValueError:
            undated += 1
            continue
        dated.append(((now - dt).total_seconds() / 86400.0, e["pair"]))

    oldest_days = None
    oldest_pair = None
    if dated:
        oldest_days, oldest_pair = max(dated, key=lambda t: t[0])

    findings_text = []
    findings = []
    if count > args.cap:
        findings_text.append("OVER CAP: %d candidate(s) exceeds cap %d" % (count, args.cap))
        findings.append({"kind": "over_cap", "path": None,
                          "detail": "%d candidate(s) exceeds cap %d" % (count, args.cap)})
    if oldest_days is not None and oldest_days > args.max_age_days:
        findings_text.append("OVER AGE: oldest candidate is %.1f day(s) old, exceeds "
                              "max-age-days %d" % (oldest_days, args.max_age_days))
        findings.append({"kind": "over_age", "path": None,
                          "detail": "oldest candidate is %.1f day(s) old, exceeds "
                                    "max-age-days %d" % (oldest_days, args.max_age_days)})

    if json_out:
        verdict = "FAIL" if findings else "PASS"
        counts = {"count": count, "undated": undated,
                  "owners": dict(owners),
                  "oldest_days": oldest_days,
                  "cap": args.cap, "max_age_days": args.max_age_days}
        _emit_json("bm_vault_curate.governance", verdict, counts, findings)
        return 1 if findings else 0

    print("queue: %s" % args.queue)
    print("count: %d" % count)
    if dated:
        print("oldest candidate age: %.1f day(s) (%s <-> %s)"
              % (oldest_days, oldest_pair[0], oldest_pair[1]))
        if undated:
            print("  plus %d candidate(s) with no build timestamp: NO-DATA age"
                  % undated)
    else:
        print("oldest candidate age: NO-DATA (%d candidate(s) built before "
              "timestamps were tracked)" % undated)
    print("per-owner counts: %s"
          % ", ".join("%s=%d" % kv for kv in sorted(owners.items())))

    if findings_text:
        for f in findings_text:
            print("FINDING: %s" % f)
        return 1
    print("OK: under cap (%d) and under max age (%d day(s))"
          % (args.cap, args.max_age_days))
    return 0


def _parse_pair(pair_arg):
    parts = [p.strip() for p in pair_arg.split(",")]
    if len(parts) != 2 or not all(parts):
        return None
    return [p[:-3] if p.endswith(".md") else p for p in parts]


def _find_entry(queue, pair):
    want = set(pair)
    for e in queue:
        stems = e["pair"]
        if set(stems) == want or set(posixpath.basename(s) for s in stems) == want:
            return e
    return None


def _older_first(vault, stem_a, stem_b):
    """(older, newer) by frontmatter created:, falling back to file mtime when either
    side carries none. Returns the basis used so the dry-run print can say so."""
    created = {}
    for stem in (stem_a, stem_b):
        path = os.path.join(vault, stem + ".md")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except (IOError, OSError) as e:
            raise RuntimeError("cannot read %s: %s" % (path, e))
        m = FRONT_CREATED.search(body[:2000])
        created[stem] = m.group(1) if m else None
    if created[stem_a] and created[stem_b] and created[stem_a] != created[stem_b]:
        ordered = sorted((stem_a, stem_b), key=lambda s: created[s])
        return ordered[0], ordered[1], "created: %s vs %s" % (
            created[ordered[0]], created[ordered[1]])
    mt = {s: os.path.getmtime(os.path.join(vault, s + ".md")) for s in (stem_a, stem_b)}
    ordered = sorted((stem_a, stem_b), key=lambda s: mt[s])
    return ordered[0], ordered[1], "file mtime (created: missing or equal)"


def _link_spelling(target_stem, by_basename):
    """A bare basename wikilink when it is unique in the vault (the house style),
    the full vault-relative stem otherwise, so the written edge always resolves."""
    base = posixpath.basename(target_stem)
    if len(by_basename.get(base.lower(), [])) == 1:
        return "[[%s]]" % base
    return "[[%s]]" % target_stem


def _body_with_edge(body, edge, link):
    """Append-only frontmatter edit: an existing edge line gains the link at its end,
    a missing line is inserted before the closing ---. Returns None when the note has
    no frontmatter block to append to (that is a refusal, never a body rewrite)."""
    if not body.startswith("---"):
        return None
    end = body.find("\n---", 3)
    if end == -1:
        return None
    head, rest = body[:end], body[end:]
    m = re.search(r"^%s:.*$" % edge, head, re.M)
    if m:
        line = m.group(0).rstrip()
        if link in line:
            return body  # already declared: idempotent, nothing to append
        head = head[:m.start()] + line + " " + link + head[m.end():]
    else:
        head = head + "\n%s: %s" % (edge, link)
    return head + rest


def cmd_accept(args):
    if not args.by:
        print("bm_vault_curate: refusing accept without --by NAME: "
              "every curation decision is attributed")
        return 2
    pair = _parse_pair(args.pair)
    if pair is None:
        print("bm_vault_curate: --pair must be exactly two notes, comma separated")
        return 2
    vault = _vault_root(args.vault)
    try:
        data = _load_queue(args.queue)
    except RuntimeError as e:
        print("bm_vault_curate: %s" % e)
        return 2
    entry = _find_entry(data["queue"], pair)
    if entry is None:
        print("bm_vault_curate: pair %s,%s is not in the queue (run find, then list)"
              % (pair[0], pair[1]))
        return 2
    stem_a, stem_b = entry["pair"]
    try:
        older, newer, basis = _older_first(vault, stem_a, stem_b)
    except RuntimeError as e:
        print("bm_vault_curate: %s" % e)
        return 2
    # supersedes points at the winner, per scan-duplicates' convention: the field
    # lands on the OLDER note either way, naming the note that should win / relate.
    target = newer
    note_path = os.path.join(vault, older + ".md")
    graph = _load_module("bm_vault_graph")
    notes = graph._load_notes(vault)
    _, by_basename = graph._build_indices(notes)
    link = _link_spelling(target, by_basename)
    with open(note_path, encoding="utf-8", errors="replace") as f:
        body = f.read()
    new_body = _body_with_edge(body, args.edge, link)
    if new_body is None:
        print("bm_vault_curate: %s has no frontmatter block; refusing an append-only "
              "edit with nowhere to append" % note_path)
        return 2
    print("edge: %s: %s  on OLDER note %s  (older by %s)" % (args.edge, link, older, basis))
    if not args.apply:
        print("DRY RUN: nothing written. Pass --apply to write this one edge.")
        return 0
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(new_body)
    data["queue"] = [e for e in data["queue"] if e is not entry]
    data["audit"].append({"action": "accept", "pair": [stem_a, stem_b],
                          "edge": args.edge, "edge_on": older, "edge_link": link,
                          "by": args.by, "when": _now(), "applied": True})
    _save_queue(args.queue, data)
    print("APPLIED: %s updated, audit recorded (by %s)" % (note_path, args.by))
    return 0


def cmd_reject(args):
    if not args.by:
        print("bm_vault_curate: refusing reject without --by NAME: "
              "every curation decision is attributed")
        return 2
    pair = _parse_pair(args.pair)
    if pair is None:
        print("bm_vault_curate: --pair must be exactly two notes, comma separated")
        return 2
    try:
        data = _load_queue(args.queue)
    except RuntimeError as e:
        print("bm_vault_curate: %s" % e)
        return 2
    entry = _find_entry(data["queue"], pair)
    if entry is None:
        print("bm_vault_curate: pair %s,%s is not in the queue (run find, then list)"
              % (pair[0], pair[1]))
        return 2
    stem_a, stem_b = entry["pair"]
    when = _now()
    data["queue"] = [e for e in data["queue"] if e is not entry]
    data["rejections"].append({"pair": [stem_a, stem_b], "by": args.by, "when": when})
    data["audit"].append({"action": "reject", "pair": [stem_a, stem_b],
                          "by": args.by, "when": when, "applied": False})
    _save_queue(args.queue, data)
    print("REJECTED: %s <-> %s remembered (by %s); it will not reappear in find"
          % (stem_a, stem_b, args.by))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("find", help="run the three finders and write the queue")
    pf.add_argument("--vault", default=None)
    pf.add_argument("--queue", default=DEFAULT_QUEUE)
    pf.add_argument("--owner", default=NO_OWNER,
                     help="who owns candidates built by this run; never guessed")
    pl = sub.add_parser("list", help="render the queue ranked")
    pl.add_argument("--queue", default=DEFAULT_QUEUE)
    pg = sub.add_parser("governance",
                         help="report count, oldest age, per-owner counts; "
                              "exit 1 on over-cap or over-age")
    pg.add_argument("--queue", default=DEFAULT_QUEUE)
    pg.add_argument("--cap", type=int, default=DEFAULT_CAP)
    pg.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                     dest="max_age_days")
    pg.add_argument("--json", action="store_true")
    pa = sub.add_parser("accept", help="accept one pair (dry unless --apply)")
    pa.add_argument("--vault", default=None)
    pa.add_argument("--queue", default=DEFAULT_QUEUE)
    pa.add_argument("--pair", required=True, help="A,B (stems or unique basenames)")
    pa.add_argument("--edge", required=True, choices=EDGE_FIELDS)
    pa.add_argument("--by", default=None, help="who is accepting; refused without it")
    pa.add_argument("--apply", action="store_true",
                     help="actually write the edge (one pair per invocation)")
    pr = sub.add_parser("reject", help="remember a rejection; never re-nudged")
    pr.add_argument("--queue", default=DEFAULT_QUEUE)
    pr.add_argument("--pair", required=True)
    pr.add_argument("--by", default=None, help="who is rejecting; refused without it")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return {"find": cmd_find, "list": cmd_list, "governance": cmd_governance,
            "accept": cmd_accept, "reject": cmd_reject}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""bm_vault_distill: turn a batch of session mistakes into vault failure notes, repeatably.

The founder's own instruction: distilling session mistakes into the vault should be "part and a
feature of /brother", not a one-off hand-write each time. This is that feature.

  distill --vault PATH --input FILE.json
      FILE.json is a JSON array of {slug, title, detail, symptom} objects. For each one:
      SEARCH FIRST, per the vault's own constitution (AGENTS.md: search before writing, supersede
      or append, never a silent duplicate). Shells out to tools/bm_vault.py's own `recall`
      (the same estate pattern tools/vault_recall_hook.py already uses) with the item's title as
      the query. When the top hit is a close match -- same slug, or it shares more than half of
      ITS OWN title words with the new title -- the item is SKIPPED and the match is reported, so
      a human can supersede by hand if that is actually warranted. This tool never merges and
      never touches an existing note's body: the vault's bodies are append-only.

      Otherwise a new note is written at 40-Failures/<slug>.md with the recording contract's
      frontmatter (type, project, status, created, tags, verified-by, description, symptom) and
      a body built from the title and detail, in the house style already used tonight: mechanism
      in the title, the explanation in the body, an empty "## Related" section left for a human
      to link by hand later.

Exit 0 when the run completed (a skip is not a failure). Exit 2 only on a real I/O or parse
error -- a bad input file, a missing required field, an unsafe slug, indexing or a note write
that fails -- with what broke printed.

Python 3.9, standard library only, no network.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
BM_VAULT = os.path.join(TOOLS_DIR, "bm_vault.py")

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
WORD_RE = re.compile(r"[a-z0-9]+")
REQUIRED_FIELDS = ("slug", "title", "detail", "symptom")

# Matches the recall header line printed by bm_vault.py's own _print_hits:
#   "  {title}  [{kind}, {source}]"
HIT_HEADER = re.compile(r"^  (.+?)  \[(\w+), (\S+)\]$")


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _words(text):
    return WORD_RE.findall(text.lower())


def _title_overlap(hit_title, new_title):
    """Fraction of the HIT title's own words that also appear in the new title."""
    hit_words = set(_words(hit_title))
    if not hit_words:
        return 0.0
    new_words = set(_words(new_title))
    return len(hit_words & new_words) / len(hit_words)


def _parse_recall_hits(output):
    """Parse tools/bm_vault.py recall's plain-text output into [{title, path}, ...], in the
    order printed (already ranked). The path line is always the last non-blank, non-"matched
    on:" indented line under a hit's header, whether or not a description line preceded it."""
    hits = []
    cur = None
    for line in output.split("\n"):
        m = HIT_HEADER.match(line)
        if m:
            if cur is not None:
                hits.append(cur)
            cur = {"title": m.group(1), "path": None}
            continue
        if cur is not None and line.startswith("    "):
            content = line[4:]
            if content and not content.startswith("matched on:"):
                cur["path"] = content
    if cur is not None:
        hits.append(cur)
    return hits


def _run_bm_vault(args):
    return subprocess.run([sys.executable, BM_VAULT] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _close_match(vault, title, slug):
    """Runs recall for this title and returns the matched note's path, or None. The check is on
    the TOP hit only, per the recording contract: recall already ranks, a lower hit sharing words
    is not a duplicate of THIS note."""
    rec = _run_bm_vault(["recall", "--query", title, "--limit", "5"])
    out = (rec.stdout + rec.stderr).decode("utf-8", "replace")
    hits = _parse_recall_hits(out)
    if not hits:
        return None
    top = hits[0]
    if not top["path"]:
        return None
    top_slug = os.path.splitext(os.path.basename(top["path"]))[0]
    if top_slug == slug or _title_overlap(top["title"], title) > 0.5:
        return top["path"]
    return None


def _yaml_quote(text):
    """Double-quoted YAML scalar: the recording contract's own convention for a value that may
    carry commas or colons (see 40-Failures/a-branch-is-not-a-delivery.md's verified-by line)."""
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def _render_note(title, detail, symptom, today):
    frontmatter = (
        "---\n"
        "type: failure\n"
        "project: all\n"
        "status: standing\n"
        "created: %s\n"
        "tags: []\n"
        "verified-by: %s\n"
        "description: %s\n"
        "symptom: %s\n"
        "---\n\n"
    ) % (today,
         _yaml_quote("written by the bm_vault_distill ceremony from a distilled "
                     "session-mistake item, not independently re-verified by this run"),
         _yaml_quote(title),
         _yaml_quote(symptom))
    body = "# %s\n\n%s\n\n## Related\n- \n" % (title, detail.strip())
    return frontmatter + body


def cmd_distill(args):
    vault = _vault_root(args.vault)
    try:
        with open(args.input, encoding="utf-8") as f:
            items = json.load(f)
    except (IOError, OSError) as e:
        print("bm_vault_distill: cannot read %s: %s" % (args.input, e))
        return 2
    except ValueError as e:
        print("bm_vault_distill: %s is not valid JSON: %s" % (args.input, e))
        return 2
    if not isinstance(items, list):
        print("bm_vault_distill: %s must contain a JSON array of objects" % args.input)
        return 2

    # Validated in full BEFORE indexing: a malformed input file is a parse error, and paying for
    # a whole-vault reindex (which embeds every un-vectored note, seconds of subprocess work) to
    # discover one is wasted work on a run that was always going to exit 2.
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            print("bm_vault_distill: item %d is not an object" % i)
            return 2
        missing = [k for k in REQUIRED_FIELDS if not item.get(k)]
        if missing:
            print("bm_vault_distill: item %d missing required field(s): %s"
                  % (i, ", ".join(missing)))
            return 2
        if not SLUG_RE.match(item["slug"]):
            print("bm_vault_distill: item %d has an unsafe slug %r "
                  "(expected kebab-case, no path separators)" % (i, item["slug"]))
            return 2

    idx = _run_bm_vault(["index", "--vault", vault])
    if idx.returncode != 0:
        print("bm_vault_distill: indexing %s failed:\n%s"
              % (vault, (idx.stdout + idx.stderr).decode("utf-8", "replace").strip()))
        return 2

    failures_dir = os.path.join(vault, "40-Failures")
    today = date.today().isoformat()
    for item in items:
        slug, title, detail, symptom = (item["slug"], item["title"], item["detail"],
                                         item["symptom"])
        match_path = _close_match(vault, title, slug)
        if match_path:
            print("SKIP %s: close match already exists at %s" % (slug, match_path))
            continue

        note_path = os.path.join(failures_dir, slug + ".md")
        try:
            os.makedirs(failures_dir, exist_ok=True)
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(_render_note(title, detail, symptom, today))
        except OSError as e:
            print("bm_vault_distill: cannot write %s: %s" % (note_path, e))
            return 2
        print("WROTE %s" % slug)
    return 0


FRONT_CREATED = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
FRONT_SUPERSEDES = re.compile(r"^supersedes:\s*(.*)$", re.M)
FRONT_RELATES = re.compile(r"^relates:\s*(.*)$", re.M)
WIKILINK = re.compile(r"\[\[([^\]|]+)")
H1 = re.compile(r"^#\s+(.+)$", re.M)
ROUTING_PAGES = ("Failures-Index.md", "Failures-by-Symptom.md")
DUPLICATE_THRESHOLD_DEFAULT = 0.5


def _frontmatter_block(body):
    if not body.startswith("---"):
        return ""
    end = body.find("\n---", 3)
    return body[3:end] if end != -1 else ""


def _typed_edge_slugs(block, field_re):
    """Every [[wikilink]] named on a supersedes:/relates: value line, as bare slugs
    (the .md-stripped, path-stripped form), the same shape a 40-Failures filename's own
    stem already is. Reused here rather than shelling out to bm_vault_graph.py, since
    this scan only needs to know WHICH pairs already declared a relationship, not the
    full resolved graph."""
    m = field_re.search(block)
    if not m:
        return set()
    slugs = set()
    for raw in WIKILINK.findall(m.group(1)):
        cleaned = raw.strip().split("#", 1)[0].strip()
        if cleaned:
            slugs.add(os.path.basename(cleaned))
    return slugs


# _title_overlap above (shared with the single-item write-time check _close_match
# already relies on, left untouched) counts every alphanumeric token, stopwords
# included. That is fine at write time: one new title against the single closest
# recall hit rarely collides on function words alone. A whole-vault PAIRWISE scan is a
# different regime -- 179 notes is roughly 16,000 pairs, and this vault's own house
# style leans hard on one rhetorical shape ("X is not a Y", "X is not the Y"), so "is",
# "not", "a", "the" alone were enough to pull unrelated notes above a 0.5 threshold in
# the first real run of this scanner. A short, explicit stopword list, used ONLY here,
# fixes that without touching the shared function or its existing behavior.
_STOPWORDS = frozenset((
    "a", "an", "the", "is", "was", "were", "are", "be", "been", "not", "no", "nor",
    "and", "or", "but", "if", "so", "of", "in", "on", "at", "to", "for", "with",
    "that", "this", "it", "its", "as", "than", "then", "still", "yet", "never",
    "always", "only", "just", "one", "own", "same", "does", "did", "do",
))


def _content_words(text):
    return set(w for w in _words(text) if w not in _STOPWORDS)


def _content_overlap(title_a, title_b):
    """Jaccard-style overlap over CONTENT words only (stopwords excluded from both
    sides, unlike _title_overlap's one-directional, unfiltered count): intersection
    over the SMALLER set, so a short, specific title fully contained in a longer one
    still scores high, the same shape _title_overlap already favors, just without
    function words inflating it. Zero content words on either side is defined as no
    overlap, not a divide-by-zero, since a title reduced to nothing but stopwords
    carries nothing this scan can compare."""
    wa, wb = _content_words(title_a), _content_words(title_b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _failure_notes(vault):
    """Every real 40-Failures note (excluding the two routing pages), as
    {slug, title, created, linked} dicts. title is the first H1 heading in the body,
    falling back to the slug itself when a note somehow carries none (never a crash).
    linked is the union of that note's own supersedes: and relates: targets, so a pair
    already declared related is never reported as an undeclared duplicate candidate."""
    notes = []
    failures_dir = os.path.join(vault, "40-Failures")
    if not os.path.isdir(failures_dir):
        return notes
    for fn in sorted(os.listdir(failures_dir)):
        if not fn.endswith(".md") or fn in ROUTING_PAGES:
            continue
        slug = fn[:-3]
        path = os.path.join(failures_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except (IOError, OSError) as e:
            sys.stderr.write("bm_vault_distill: cannot read %s: %s\n" % (path, e))
            continue
        block = _frontmatter_block(body)
        h1 = H1.search(body)
        title = h1.group(1).strip() if h1 else slug
        created_m = FRONT_CREATED.search(block)
        linked = _typed_edge_slugs(block, FRONT_SUPERSEDES) | _typed_edge_slugs(block, FRONT_RELATES)
        notes.append({"slug": slug, "title": title,
                      "created": created_m.group(1) if created_m else None,
                      "linked": linked})
    return notes


def cmd_scan_duplicates(args):
    """WBS 17, the consolidation half of the "live" gap: the same close-match
    detection distill already runs at write time, run once as a batch over every
    EXISTING failure note, pairwise, so drift that already landed is surfaced too, not
    just drift a future write would have caught. Reports candidates; writes nothing;
    matches bm_vault_promote.py's own "nudge, never auto-write" boundary, and the
    vault constitution's own supersede-or-append law, which forbids this tool from
    merging anything itself even if it wanted to."""
    vault = _vault_root(args.vault)
    notes = _failure_notes(vault)
    if not notes:
        print("NO-DATA: no 40-Failures notes found under %s" % vault)
        return 3
    candidates = []
    for i, a in enumerate(notes):
        for b in notes[i + 1:]:
            if b["slug"] in a["linked"] or a["slug"] in b["linked"]:
                continue
            best = _content_overlap(a["title"], b["title"])
            if best >= args.threshold:
                candidates.append({"a": a["slug"], "b": b["slug"], "score": round(best, 2),
                                   "a_title": a["title"], "b_title": b["title"],
                                   "a_created": a["created"], "b_created": b["created"]})
    candidates.sort(key=lambda c: -c["score"])
    if args.json:
        print(json.dumps({"note_count": len(notes), "candidates": candidates},
                         indent=2, sort_keys=True))
        return 0
    print("40-Failures notes scanned: %d" % len(notes))
    print("duplicate candidates at or above %.2f title overlap: %d" % (
        args.threshold, len(candidates)))
    for c in candidates:
        print("  %.2f  %s (%s)  <->  %s (%s)" % (
            c["score"], c["a"], c["a_created"] or "?", c["b"], c["b_created"] or "?"))
    if candidates:
        print("Nudge only: nothing here writes a supersedes: or relates: link for you. "
              "Review each pair and, if it is a real duplicate, add the field to the "
              "OLDER note by hand (append-only), pointing at the one that should win.")
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("distill", help="search-then-write a batch of distilled mistakes")
    pd.add_argument("--vault", default=None)
    pd.add_argument("--input", required=True, help="path to a JSON array of "
                     "{slug, title, detail, symptom} objects")
    psd = sub.add_parser("scan-duplicates", help="batch-scan existing 40-Failures notes "
                                                  "for undeclared near-duplicates")
    psd.add_argument("--vault", default=None)
    psd.add_argument("--threshold", type=float, default=DUPLICATE_THRESHOLD_DEFAULT)
    psd.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "scan-duplicates":
        return cmd_scan_duplicates(args)
    return cmd_distill(args)


if __name__ == "__main__":
    sys.exit(main())

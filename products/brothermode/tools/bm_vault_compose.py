#!/usr/bin/env python3
"""bm_vault_compose: the Note Composer port, WBS row VB4-06.

WHY THIS EXISTS. Restructuring a note by hand (splitting a bloated one, folding
a near-duplicate into its winner) leaves every note that links to the old shape
stale: nobody remembers to walk the vault and fix every [[wikilink]] by hand.
This closes that gap for the two operations that actually restructure identity:

  split --vault V --note PATH --heading H --today YYYY-MM-DD [--apply]
        Extracts the section under heading H out of PATH into a brand new note
        (frontmatter: type/status/project inherited from the source, created
        set to --today, id MINTED via bm_vault_ids.add_id), and replaces the
        extracted section in the source with a [[wikilink]] to the new note.
        Dry by default: prints the plan, writes nothing.

  merge --vault V --from A --to B [--apply]
        Folds A's body into B under a heading naming A as its origin, rewrites
        EVERY inbound wikilink to A across the whole vault to point at B
        instead (both [[A]] and [[A|alias]], alias text preserved), records
        supersedes: [[B]] in A's own frontmatter, and leaves A on disk as a
        husk: NEVER deleted, per the vault's never-lose-work law. Dry by
        default: lists every file it would touch and every link it would
        rewrite, writes nothing.

MERGE IS IDENTITY SURGERY, so both refusals below fire before a single byte is
written, apply or not:

  - B already carries a heading "Merged from <A>" that the fold would produce
    again (irreversible collision: which fold is real?). Refused, both notes
    named.
  - --from or --to does not resolve to exactly one note (Tag Wrangler's own
    problem: a bare basename with more than one candidate). Refused, EVERY
    candidate named, never a silent pick.

split refuses the same way when the derived target filename already exists
(collision named, both the source and the existing file).

ATOMICITY. --apply writes every file temp-then-os.replace (matches
bm_vault_lint.py's own fix --apply), then runs the graph gate
(bm_vault_graph.cmd_check) scoped to every file the operation touched, and
reports that gate's own exit code. A failed gate after apply is never a quiet
number: every touched path is printed alongside it.

REUSE, NEVER DUPLICATE. bm_vault_ids.py (mint, add_id, frontmatter, index) and
bm_vault_graph.py (its own wikilink extraction, note index and resolver, and
its check command) are loaded by path via the same guarded
importlib.util.spec_from_file_location technique bm_vault_lint.py already
uses, so this module carries none of its own id-minting or link-resolution
logic.

Exit 0 the operation ran clean (plan printed, or written and gate green).
Exit 1 a refusal, reason and both sides named. Exit 2 NO-DATA, the vault could
not be read. Python 3.9 floor, standard library only, no network.
"""
import argparse
import datetime
import importlib.util
import os
import re
import sys

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
HERE = os.path.dirname(os.path.abspath(__file__))

# Same wikilink shape as bm_vault_graph.WIKILINK, but capturing the optional
# "#anchor" and "|alias" tails too, so a rewrite can preserve both instead of
# silently dropping a deep link's anchor or an alias's display text.
WIKILINK_FULL = re.compile(r"\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _load_sibling(name):
    """tools/<name>.py loaded BY PATH, guarded, matching bm_vault_lint.py's own
    technique. Returns the module, or None when it is absent or fails to
    import: a missing sibling must never crash this tool, it becomes a named
    NO-DATA the caller reports."""
    path = os.path.join(HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        print("_load_sibling: %s failed to import: %s" % (name, exc),
              file=sys.stderr)
        return None


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


class _Modules(object):
    """The two siblings this tool leans on, loaded once. NO-DATA (both None)
    is reported by the caller, never silently treated as "nothing to link"."""

    def __init__(self):
        self.ids = _load_sibling("bm_vault_ids")
        self.graph = _load_sibling("bm_vault_graph")
        self.labels = _load_sibling("bm_vault_labels")


def _atomic_write(path, text):
    tmp = path + ".bm-vault-compose.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "untitled"


def _field(block, key):
    m = re.search(r"^%s:\s*(.*)$" % re.escape(key), block, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    return v or None


def _body_only(mods, text):
    """Everything after the closing frontmatter fence, or the whole text when
    there is no frontmatter block at all."""
    block, _start, end = mods.ids.frontmatter(text)
    if block is None:
        return text
    return text[end + 4:]  # skip the "\n---" the closing fence occupies


def _resolve_note_arg(mods, raw, exact, by_basename, file_index):
    """(stem, error) for a note spelled the way a [[wikilink]] to it would be.
    error is None on success. An unresolved bare basename with more than one
    candidate names every candidate, never a silent pick (the Tag Wrangler
    problem this row exists to close)."""
    cleaned = mods.graph._clean_link(raw)
    target, kind = mods.graph._resolve(cleaned, "", exact, by_basename, file_index)
    if target is not None and kind in ("note", "ambiguous"):
        return target, None
    base = os.path.splitext(os.path.basename(cleaned))[0].lower()
    candidates = by_basename.get(base, [])
    if len(candidates) > 1:
        return None, ("ambiguous: %r resolves to more than one note: %s"
                       % (raw, ", ".join(sorted(candidates))))
    return None, "no note found for %r" % raw


def _find_heading_section(body, heading_text):
    """(start, end, level) of the section under a heading whose text matches
    heading_text exactly, spanning to the next heading of the same or a
    shallower level (or end of body). None when no such heading exists."""
    lines = body.splitlines(keepends=True)
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    start_i = None
    level = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line.rstrip("\n"))
        if m and m.group(2).strip() == heading_text.strip():
            start_i, level = i, len(m.group(1))
            break
    if start_i is None:
        return None
    end = len(body)
    for j in range(start_i + 1, len(lines)):
        m = HEADING_RE.match(lines[j].rstrip("\n"))
        if m and len(m.group(1)) <= level:
            end = offsets[j]
            break
    return offsets[start_i], end, level


def _has_heading(text, heading_text):
    for line in text.splitlines():
        m = HEADING_RE.match(line.rstrip("\n"))
        if m and m.group(2).strip() == heading_text.strip():
            return True
    return False


def _run_gate(mods, vault, touched_relpaths):
    ns = argparse.Namespace(vault=vault, paths=sorted(set(touched_relpaths)))
    code = mods.graph.cmd_check(ns)
    if code != 0:
        print("GATE FAILED after apply (exit %d). Touched files:" % code)
        for p in sorted(set(touched_relpaths)):
            print("  %s" % p)
    else:
        print("gate: OK, %d touched file(s) clean" % len(set(touched_relpaths)))
    return code


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

def cmd_split(args):
    vault = _vault_root(args.vault)
    if not vault or not os.path.isdir(vault):
        print("NO-DATA: no readable vault at %r" % vault)
        return 2
    mods = _Modules()
    if mods.ids is None or mods.graph is None or mods.labels is None:
        print("NO-DATA: bm_vault_ids.py, bm_vault_graph.py or bm_vault_labels.py "
              "could not be loaded")
        return 2
    try:
        datetime.date.fromisoformat(args.today)
    except ValueError:
        print("REFUSED: --today %r is not an ISO YYYY-MM-DD date" % args.today)
        return 1
    notes = mods.graph._load_notes(vault)
    if not notes:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 2
    exact, by_basename = mods.graph._build_indices(notes)
    file_index = mods.graph._build_file_index(vault)
    source_stem, err = _resolve_note_arg(mods, args.note, exact, by_basename, file_index)
    if err:
        print("REFUSED: %s" % err)
        return 1
    source_rel = source_stem + ".md"
    source_path = os.path.join(vault, source_rel)
    source_text = _read(source_path)
    body = _body_only(mods, source_text)
    section = _find_heading_section(body, args.heading)
    if section is None:
        print("REFUSED: no heading %r found in %s" % (args.heading, source_rel))
        return 1
    start, end, _level = section

    new_slug = _slugify(args.heading)
    new_rel = os.path.join(os.path.dirname(source_rel), new_slug + ".md").replace(os.sep, "/")
    new_path = os.path.join(vault, new_rel)
    if os.path.exists(new_path):
        print("REFUSED: target filename already exists: %s (source: %s)"
              % (new_rel, source_rel))
        return 1

    by_id, _missing, _dupes = mods.ids.index(vault)
    new_id = mods.ids.mint(set(by_id))

    src_block, _s, _e = mods.ids.frontmatter(source_text)
    inherited = {}
    if src_block is not None:
        for key in ("type", "status", "project"):
            v = _field(src_block, key)
            if v:
                inherited[key] = v

    fm_lines = ["id: %s" % new_id]
    if "type" in inherited:
        fm_lines.append("type: %s" % inherited["type"])
    if "project" in inherited:
        fm_lines.append("project: %s" % inherited["project"])
    fm_lines.append("created: %s" % args.today)
    if "status" in inherited:
        fm_lines.append("status: %s" % inherited["status"])
    new_stem = new_rel[:-3]
    extracted = body[start:end]
    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + extracted.strip("\n") + "\n"

    # VB3-13: derived memory cannot declassify. The extracted note is DERIVED from
    # exactly one source (the note it was split out of); its security_label is
    # inherited from that source (most-restrictive-wins over a single label is the
    # label itself), and the derivation record lands in its own frontmatter where
    # bm_vault_lineage.py's INTAKE section already reads a note's own fields.
    source_id = mods.ids.read_id(source_text) or source_rel
    source_label = mods.labels.read_label(source_text)
    derived_label = mods.labels.derive([source_label])
    new_text = mods.labels.annotate_derivation(
        new_text, [(source_id, source_label)], args.today)
    if new_text is None:
        print("REFUSED: %s has no frontmatter block to record its derivation in" % new_rel)
        return 1

    new_body = body[:start] + "[[" + new_stem + "]]\n" + body[end:]
    new_source_text = source_text[:len(source_text) - len(body)] + new_body

    plan = [
        "split plan:",
        "  source: %s" % source_rel,
        "  heading: %r" % args.heading,
        "  new note: %s (id %s)" % (new_rel, new_id),
        "  inherited frontmatter: %s" % (", ".join("%s=%s" % kv for kv in sorted(inherited.items())) or "none"),
        "  security label: %s (inherited from %s)" % (derived_label, source_rel),
        "  source section replaced with: [[%s]]" % new_stem,
    ]
    for line in plan:
        print(line)
    if not args.apply:
        print("dry run: nothing was written. Re-run with --apply to write.")
        return 0

    _atomic_write(new_path, new_text)
    _atomic_write(source_path, new_source_text)
    print("wrote %s and %s" % (new_rel, source_rel))
    return _run_gate(mods, vault, [source_rel, new_rel])


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def cmd_merge(args):
    vault = _vault_root(args.vault)
    if not vault or not os.path.isdir(vault):
        print("NO-DATA: no readable vault at %r" % vault)
        return 2
    mods = _Modules()
    if mods.ids is None or mods.graph is None or mods.labels is None:
        print("NO-DATA: bm_vault_ids.py, bm_vault_graph.py or bm_vault_labels.py "
              "could not be loaded")
        return 2
    notes = mods.graph._load_notes(vault)
    if not notes:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 2
    exact, by_basename = mods.graph._build_indices(notes)
    file_index = mods.graph._build_file_index(vault)

    from_stem, err = _resolve_note_arg(mods, args.from_note, exact, by_basename, file_index)
    if err:
        print("REFUSED (--from): %s" % err)
        return 1
    to_stem, err = _resolve_note_arg(mods, args.to_note, exact, by_basename, file_index)
    if err:
        print("REFUSED (--to): %s" % err)
        return 1
    if from_stem == to_stem:
        print("REFUSED: --from and --to resolve to the same note (%s)" % from_stem)
        return 1

    a_rel, b_rel = from_stem + ".md", to_stem + ".md"
    a_path, b_path = os.path.join(vault, a_rel), os.path.join(vault, b_rel)
    a_text, b_text = _read(a_path), _read(b_path)

    origin_heading = "Merged from %s" % from_stem
    if _has_heading(b_text, origin_heading):
        print("REFUSED: %s already carries a heading %r that merging %s in "
              "would duplicate" % (b_rel, origin_heading, a_rel))
        return 1

    a_body = _body_only(mods, a_text).strip("\n")
    new_b_text = b_text.rstrip("\n") + "\n\n## %s\n\n%s\n" % (origin_heading, a_body)

    # VB3-13: derived memory cannot declassify. B receives A's content, so B is now
    # DERIVED from both A and its own prior self: its security_label becomes the
    # most restrictive of the two (never a hand-set weaker value -- that is what
    # bm_vault_labels.check_derived_notes catches later if someone edits it back
    # down), and the derivation record (both sources, their labels AT THIS MOMENT)
    # lands in B's own frontmatter for bm_vault_lineage.py to read.
    a_id = mods.ids.read_id(a_text) or a_rel
    b_id = mods.ids.read_id(b_text) or b_rel
    label_a = mods.labels.read_label(a_text)
    label_b = mods.labels.read_label(b_text)
    derived_label = mods.labels.derive([label_a, label_b])
    derived_at = datetime.date.today().isoformat()
    new_b_text = mods.labels.annotate_derivation(
        new_b_text, [(a_id, label_a), (b_id, label_b)], derived_at)
    if new_b_text is None:
        print("REFUSED: %s has no frontmatter block to record its derivation in" % b_rel)
        return 1

    a_with_supersedes = _add_supersedes(mods, a_text, to_stem)
    if a_with_supersedes is None:
        print("REFUSED: %s has no frontmatter block to record supersedes: in" % a_rel)
        return 1

    # Every inbound [[A]] / [[A|alias]] across the whole vault, rewritten to B.
    # Only relpath + stem are kept here: apply time re-reads each target from
    # disk (see below) rather than reusing this pre-fold cached text, so a
    # rewrite target that is itself A or B never clobbers the fold/supersedes
    # write that already landed on disk.
    rewrites = []  # (relpath, stem, count)
    for n in notes:
        stem, text = n["stem"], n["body"]
        _new_text, count = _rewrite_inbound_links(
            mods, text, stem, from_stem, to_stem, exact, by_basename, file_index)
        if count:
            rewrites.append((n["relpath"], stem, count))

    touched = sorted({a_rel, b_rel} | {r for r, _s, _c in rewrites})
    print("merge plan:")
    print("  from: %s" % a_rel)
    print("  to: %s" % b_rel)
    print("  fold heading: ## %s" % origin_heading)
    print("  security label: %s + %s -> %s" % (label_a, label_b, derived_label))
    print("  supersedes: [[%s]] recorded in %s" % (to_stem, a_rel))
    print("  inbound links to rewrite: %d across %d file(s)" % (
        sum(c for _r, _s, c in rewrites), len(rewrites)))
    for r, _s, c in sorted(rewrites):
        print("    %s: %d link(s)" % (r, c))
    print("  files touched: %s" % ", ".join(touched))

    if not args.apply:
        print("dry run: nothing was written. Re-run with --apply to write.")
        return 0

    _atomic_write(b_path, new_b_text)
    _atomic_write(a_path, a_with_supersedes)
    for relpath, stem, _count in rewrites:
        target_path = os.path.join(vault, relpath)
        current_text = _read(target_path)
        new_text, _count2 = _rewrite_inbound_links(
            mods, current_text, stem, from_stem, to_stem, exact, by_basename, file_index)
        _atomic_write(target_path, new_text)
    print("wrote %d file(s)" % len(touched))
    return _run_gate(mods, vault, touched)


def _rewrite_inbound_links(mods, text, stem, from_stem, to_stem,
                            exact, by_basename, file_index):
    """Rewrite every [[from_stem]] / [[from_stem|alias]] inbound link found in
    text (a note whose own identity is stem) to point at to_stem instead.
    Returns (new_text, count). Content-only: callers decide whether text came
    from the initial load or a fresh re-read from disk."""
    count = [0]

    def repl(m):
        raw, anchor, alias = m.group(1), m.group(2) or "", m.group(3) or ""
        cleaned = mods.graph._clean_link(raw)
        target, kind = mods.graph._resolve(cleaned, stem, exact, by_basename, file_index)
        if target == from_stem and kind in ("note", "ambiguous"):
            count[0] += 1
            return "[[%s%s%s]]" % (to_stem, anchor, alias)
        return m.group(0)

    new_text = WIKILINK_FULL.sub(repl, text)
    return new_text, count[0]


def _add_supersedes(mods, text, target_stem):
    """A's frontmatter with supersedes: [[target_stem]] recorded: fills an
    existing (typically empty) supersedes: line in place, or appends a new
    one, never duplicating the field. None when the note has no frontmatter
    block to write into at all."""
    block, _start, end = mods.ids.frontmatter(text)
    if block is None:
        return None
    link_value = "supersedes: [[%s]]" % target_stem
    m = mods.graph.FRONT_SUPERSEDES.search(block)
    if m:
        new_block = block[:m.start()] + link_value + block[m.end():]
    else:
        sep = "" if block.endswith("\n") else "\n"
        new_block = block + sep + link_value
    return text[:3] + new_block + text[end:]


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("split", help="extract a heading's section into a new note")
    ps.add_argument("--vault", default=None)
    ps.add_argument("--note", required=True, help="source note, spelled like a wikilink")
    ps.add_argument("--heading", required=True, help="exact heading text to extract")
    ps.add_argument("--today", required=True, help="YYYY-MM-DD, the new note's created: date")
    ps.add_argument("--apply", action="store_true", help="actually write; dry run otherwise")

    pm = sub.add_parser("merge", help="fold one note into another, rewriting inbound links")
    pm.add_argument("--vault", default=None)
    pm.add_argument("--from", dest="from_note", required=True, help="note to fold in, husked")
    pm.add_argument("--to", dest="to_note", required=True, help="note that receives the fold")
    pm.add_argument("--apply", action="store_true", help="actually write; dry run otherwise")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "split":
        return cmd_split(args)
    return cmd_merge(args)


if __name__ == "__main__":
    sys.exit(main())

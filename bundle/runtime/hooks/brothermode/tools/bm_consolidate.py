#!/usr/bin/env python3
"""bm_consolidate: propose and approve consolidation of the STALE and UNANCHORED vault note tail
into a smaller set of trusted summaries, with every raw note kept byte-identical forever (F8).

WHY THIS EXISTS. tools/bm_freshness.py already answers whether a note's citation still resolves
(fresh/stale/unanchored). Nothing acts on that answer: the stale tail just grows. Qwen Code's
Dream consolidation runs in reverse here on purpose (see the roadmap's F8 adaptation note): this
file consolidates what is DECAYING, never what is fresh, so a background summariser can never
quietly turn an uncertain observation into policy. Three guarantees, each enforced in code, not
only stated:
  1. Only stale or unanchored notes are ever candidates (never fresh, checked with
     tools/bm_freshness.py's own classify_live -- this file never re-parses its printed text).
  2. Every consolidation is a two-step PROPOSAL then APPROVAL a human names (--approved-by);
     nothing is written as a trusted summary without that name.
  3. Raw notes are read-only from the moment a note becomes a candidate onward. approve()
     re-hashes every member note and re-runs classify_live on it immediately before writing
     anything, and refuses (byte-for-byte or freshness mismatch) rather than write a summary
     over evidence that moved out from under it.

Python 3.9, standard library only, no network, no subprocess.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_PROPOSALS_DIR = "docs/plan/consolidation-proposals"
DEFAULT_APPROVED_DIR = "docs/plan/consolidation-approved"

# Same fenced --- block a note's frontmatter lives in everywhere else in this estate (see
# bm_vault.py's own _frontmatter_block): a plain text search would also match a note merely
# DISCUSSING "pinned: true" in prose.
_PINNED_RE = re.compile(r"(?im)^\s*pinned\s*:\s*true\s*$")
_TAG_RE = re.compile(r'(?im)^\s*tags?\s*:\s*\[?\s*"?([A-Za-z0-9_.-]+)')


def _load_bm_freshness():
    """Dynamic import by path, the same pattern bm_freshness.py itself uses for bm_vault.py.
    Gives this file classify_live, _default_roots, _state_connect and STATE_DB without a second
    implementation of any of them -- see the F8.1 resume_from note this mirrors."""
    spec = importlib.util.spec_from_file_location(
        "bm_freshness", os.path.join(_TOOLS_DIR, "bm_freshness.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frontmatter_block(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _is_pinned(text):
    return bool(_PINNED_RE.search(_frontmatter_block(text)))


def _group_key_for(note_text, note_path):
    """A shared frontmatter tag if one is declared, else the note's parent directory -- the
    'simple heuristic' F8.2.1 asks for. Never a symbol/anchor grouping: that would require a
    repo map (F5) this file has no reason to depend on for a text-grouping heuristic."""
    m = _TAG_RE.search(_frontmatter_block(note_text))
    if m:
        return "tag:" + m.group(1)
    return "dir:" + os.path.dirname(note_path)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "group"


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_note(path):
    """Raw bytes and decoded text for one note, or (None, None) with a stderr notice on any
    read failure -- a boundary read, so a missing or unreadable note is skipped rather than
    crashing the whole run."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        sys.stderr.write("bm_consolidate: skipping %s, cannot read: %s\n" % (path, exc))
        return None, None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    return raw, text


def stale_tail(roots, index_path=None, state_db=None, now=None):
    """Every note tools/bm_vault.py's own index carries, classified live via bm_freshness's
    classify_live (never a re-parse of its printed status), filtered to stale/unanchored and
    excluding any note whose frontmatter marks it pinned. Returns a list of
    {path, status, sha256} sorted by path, or None when there is no index to read (NO-DATA,
    left for the caller to report and exit on)."""
    bmf = _load_bm_freshness()
    bmv = bmf._load_bm_vault()
    index_path = index_path or bmv.INDEX_PATH
    if not os.path.exists(index_path):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % index_path, uri=True)
    con.row_factory = sqlite3.Row
    total = con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
    if total == 0:
        con.close()
        return None
    notes = con.execute("SELECT id, path FROM notes").fetchall()
    anchors_by_note = {}
    for r in con.execute("SELECT note_id, anchor FROM anchors").fetchall():
        anchors_by_note.setdefault(r["note_id"], set()).add(r["anchor"])
    con.close()

    state_con = bmf._state_connect(state_db or bmf.STATE_DB)
    idx_cache = {}
    now = time.time() if now is None else now
    out = []
    for n in notes:
        anchors = anchors_by_note.get(n["id"], set())
        state, _reason = bmf.classify_live(n["path"], anchors, roots, idx_cache, state_con, now)
        if state == "fresh":
            continue
        raw, text = _read_note(n["path"])
        if raw is None:
            continue
        if _is_pinned(text):
            continue
        out.append({"path": n["path"], "status": state,
                   "sha256": hashlib.sha256(raw).hexdigest()})
    state_con.commit()
    state_con.close()
    out.sort(key=lambda c: c["path"])
    return out


def draft_summary(candidates):
    """Groups candidates by _group_key_for and returns one proposal dict per group: {batch_id,
    group_key, members, body}. members carries the full candidate dicts (path/status/sha256) so
    the written proposal file can record propose-time hashes for approve()'s immutability check."""
    groups = {}
    order = []
    for c in candidates:
        raw, text = _read_note(c["path"])  # read-only: never opened in write mode
        key = _group_key_for(text or "", c["path"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    proposals = []
    for key in sorted(order):
        members = groups[key]
        lines = ["Consolidation proposal for group: %s" % key,
                "Members (%d note(s)):" % len(members)]
        for m in members:
            lines.append("- %s (%s)" % (m["path"], m["status"]))
        lines.append("")
        lines.append("Draft summary (placeholder -- requires human review before approval):")
        lines.append("TODO: a human writes the consolidated summary for this group here.")
        proposals.append({"batch_id": _slug(key), "group_key": key, "members": members,
                          "body": "\n".join(lines)})
    return proposals


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_proposal(proposal, out_dir):
    """Writes exactly one NEW file under out_dir; never opens any member note in write mode."""
    _ensure_dir(out_dir)
    date = time.strftime("%Y-%m-%d", time.gmtime())
    filename = "%s-proposal-%s.md" % (date, proposal["batch_id"])
    path = os.path.join(out_dir, filename)
    members_json = json.dumps(
        [{"path": m["path"], "status": m["status"], "sha256": m["sha256"]}
         for m in proposal["members"]], sort_keys=True)
    frontmatter = (
        "---\n"
        "batch_id: %s\n"
        "group_key: %s\n"
        "created_at: %s\n"
        "members: %s\n"
        "---\n\n"
    ) % (proposal["batch_id"], proposal["group_key"], _iso_now(), members_json)
    _write_text(path, frontmatter + proposal["body"] + "\n")
    return path


def read_proposal(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    block = _frontmatter_block(text)
    fields = {}
    for line in block.splitlines():
        if not line.strip() or ":" not in line:
            continue
        k, _sep, v = line.partition(":")
        fields[k.strip()] = v.strip()
    members = json.loads(fields.get("members", "[]"))
    body_start = text.find("\n---", 3)
    body = text[body_start + 4:].lstrip("\n") if text.startswith("---") and body_start != -1 \
        else text
    return {"batch_id": fields.get("batch_id", ""), "group_key": fields.get("group_key", ""),
           "created_at": fields.get("created_at", ""), "members": members, "body": body}


def verify_immutable(before_hashes, after_hashes):
    """Paths whose sha256 changed between before_hashes and after_hashes, sorted. A path present
    in before_hashes but absent from after_hashes (the note vanished) counts as changed too."""
    changed = []
    for path, before in before_hashes.items():
        if after_hashes.get(path) != before:
            changed.append(path)
    return sorted(changed)


def assert_none_fresh(member_paths, roots, index_path=None, state_db=None):
    """Reruns stale_tail (the same live classify_live check every candidate already passed) and
    refuses if any member path is no longer in the stale/unanchored/unpinned set -- it turned
    fresh, got pinned, or vanished from the index since propose(). Returns the rerun candidates
    keyed by path, which also carries each one's CURRENT sha256 for the immutability check."""
    rerun = stale_tail(roots, index_path=index_path, state_db=state_db)
    if rerun is None:
        raise ValueError("no vault index available to re-verify eligibility")
    rerun_by_path = {c["path"]: c for c in rerun}
    missing = sorted(p for p in member_paths if p not in rerun_by_path)
    if missing:
        raise ValueError(
            "no longer eligible for consolidation (fresh, pinned, or removed from the index "
            "since the proposal was drafted): %s" % ", ".join(missing))
    return rerun_by_path


def approve(proposal_path, approved_by, roots, out_dir=None, index_path=None, state_db=None):
    """Refuses (ValueError) rather than write anything when: approved_by is blank, the proposal
    has no members, a member is no longer stale/unanchored/unpinned, or any member's bytes
    changed since propose(). On success writes ONE new approved-summary file (never editing or
    deleting any raw note or the proposal itself) and returns its path."""
    if not str(approved_by or "").strip():
        raise ValueError("approved_by is required and must be non-empty")
    proposal = read_proposal(proposal_path)
    member_paths = [m["path"] for m in proposal["members"]]
    if not member_paths:
        raise ValueError("proposal has no members: %s" % proposal_path)
    before_hashes = {m["path"]: m["sha256"] for m in proposal["members"]}

    rerun_by_path = assert_none_fresh(member_paths, roots, index_path=index_path,
                                      state_db=state_db)
    after_hashes = {p: rerun_by_path[p]["sha256"] for p in member_paths}
    changed = verify_immutable(before_hashes, after_hashes)
    if changed:
        raise ValueError(
            "raw notes byte-identical: FAIL -- changed since propose(): %s" % ", ".join(changed))

    out_dir = out_dir or DEFAULT_APPROVED_DIR
    _ensure_dir(out_dir)
    date = time.strftime("%Y-%m-%d", time.gmtime())
    filename = "%s-approved-%s.md" % (date, proposal["batch_id"])
    path = os.path.join(out_dir, filename)
    frontmatter = (
        "---\n"
        "consolidates: %s\n"
        "approved_by: %s\n"
        "approved_at: %s\n"
        "source_proposal: %s\n"
        "---\n\n"
    ) % (json.dumps(sorted(member_paths)), approved_by, _iso_now(), proposal_path)
    body = "# Consolidated summary: %s\n\n%s" % (proposal["group_key"], proposal["body"])
    _write_text(path, frontmatter + body + "\n")
    print("raw notes byte-identical: PASS")
    return path


def _roots_from_args(args):
    bmf = _load_bm_freshness()
    return args.root if args.root else bmf._default_roots()


def cmd_candidates(args):
    roots = _roots_from_args(args)
    result = stale_tail(roots, index_path=args.index, state_db=args.state)
    if result is None:
        print("NO-DATA: no vault index found, or the index has zero notes -- run "
              "bm_vault.py index first")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_propose(args):
    roots = _roots_from_args(args)
    candidates = stale_tail(roots, index_path=args.index, state_db=args.state)
    if candidates is None:
        print("NO-DATA: no vault index found, or the index has zero notes -- run "
              "bm_vault.py index first")
        return 2
    if not candidates:
        print("NO-DATA: zero stale/unanchored/unpinned candidates found under the given root(s)")
        return 2
    proposals = draft_summary(candidates)
    for p in proposals:
        path = write_proposal(p, args.out)
        print("bm_consolidate: wrote %s" % path)
    return 0


def cmd_approve(args):
    roots = _roots_from_args(args)
    try:
        path = approve(args.proposal, args.approved_by, roots, out_dir=args.out_dir,
                       index_path=args.index, state_db=args.state)
    except ValueError as exc:
        print("bm_consolidate: REFUSED: %s" % exc, file=sys.stderr)
        return 1
    print("bm_consolidate: approved -> %s" % path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="bm_consolidate.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("candidates", help="list stale/unanchored notes eligible for consolidation")
    c.add_argument("--root", action="append", default=None,
                   help="root to search for anchor resolution; repeatable (default: "
                        "bm_freshness's own widened defaults)")
    c.add_argument("--index", default=None, help="vault index db (default: bm_vault.INDEX_PATH)")
    c.add_argument("--state", default=None,
                   help="freshness state db (default: bm_freshness.STATE_DB)")

    p = sub.add_parser("propose", help="draft consolidation proposals from the candidate tail")
    p.add_argument("--root", action="append", default=None)
    p.add_argument("--index", default=None)
    p.add_argument("--state", default=None)
    p.add_argument("--out", default=DEFAULT_PROPOSALS_DIR)

    a = sub.add_parser("approve", help="approve one proposal into a new consolidated summary")
    a.add_argument("--proposal", required=True)
    a.add_argument("--approved-by", dest="approved_by", required=True)
    a.add_argument("--root", action="append", default=None)
    a.add_argument("--index", default=None)
    a.add_argument("--state", default=None)
    a.add_argument("--out-dir", dest="out_dir", default=DEFAULT_APPROVED_DIR)

    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        return cmd_candidates(args)
    if args.cmd == "propose":
        return cmd_propose(args)
    if args.cmd == "approve":
        return cmd_approve(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

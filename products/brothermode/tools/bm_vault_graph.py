#!/usr/bin/env python3
"""bm_vault_graph: the canonical graph health measure for the Obsidian vault.

Walks every .md file, extracts [[wikilinks]], resolves each one the way Obsidian does
(exact vault path, then relative to the linking note's own directory, then a unique
basename match anywhere in the vault), and reports what does not resolve, what nobody
links to, and what the frontmatter status/type values actually are.

  measure   print a human summary; --json prints the same numbers as one JSON object
  check     exit 0 when the graph is clean (no broken links, every status/type value
            known), exit 2 and print each violation otherwise; exit 3 on NO-DATA
            (zero markdown files found, which is never treated as a clean pass).
            --paths PATH [PATH ...] scopes the gate to the named vault-relative notes
            only (frontmatter validity on those notes, plus any broken outgoing link
            whose source is one of them): a bad note elsewhere in the vault that is not
            named never fails this scoped check. Still resolves links against the whole
            vault (a link target can be any note), only the reported violations are
            scoped. Omit --paths for the whole-vault check, which stays the default.

Python 3.9, standard library only, no network.
"""
import argparse
import json
import os
import posixpath
import re
import sys
from collections import Counter

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
SKIP_DIR_NAMES = {".trash"}
TEMPLATES_PREFIX = "99-System/Templates/"
# VB4-07 (vault rot detection, report-only, the Janitor port): telemetry attachments are
# generated on every run and never named in prose, so they would otherwise flood the
# orphan-attachment list with noise nobody acts on.
TELEMETRY_PREFIX = "99-System/telemetry/"

# Same wikilink family as bm_vault.py: capture up to the first "]" or "|", so an alias
# after a pipe is already excluded from the match.
WIKILINK = re.compile(r"\[\[([^\]|]+)")
FRONT_STATUS = re.compile(r"^status:\s*(.+)$", re.M)
FRONT_TYPE = re.compile(r"^type:\s*(.+)$", re.M)
FRONT_TAGS = re.compile(r"^tags:\s*\[(.*?)\]\s*$", re.M)
# The recording contract's supersedes: field (a note this one replaces) and a new
# relates: field (a bidirectional, non-superseding link), both frontmatter, both
# holding zero or more [[wikilinks]] on their own value line. Same style as the three
# regexes above: match the whole value, WIKILINK below extracts the link(s) inside it.
# An empty value ("supersedes:" with nothing after it, which is what most notes carry
# today) matches this regex with an empty group, which WIKILINK.findall then correctly
# reads as zero targets, never a crash and never a false edge.
FRONT_SUPERSEDES = re.compile(r"^supersedes:\s*(.*)$", re.M)
FRONT_RELATES = re.compile(r"^relates:\s*(.*)$", re.M)
# D10 (vault benchmark v2): contradicts: names a note this one conflicts with rather
# than supersedes or merely relates to (two assertions that both carry authority and
# evidence, neither overwriting the other). Symmetric like relates: (a contradiction
# runs both ways in meaning), resolved the same way, and a dangling target is a broken
# edge the gate fails on, same as supersedes:.
FRONT_CONTRADICTS = re.compile(r"^contradicts:\s*(.*)$", re.M)

ALLOWED_STATUS = {"open", "closed", "standing"}
ALLOWED_TYPE = {"failure", "finding", "decision", "session-log", "overview", "index",
                 "reference", "pattern"}


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _walk_all(vault_root):
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in SKIP_DIR_NAMES]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def _walk_md(vault_root):
    for abspath in _walk_all(vault_root):
        if abspath.endswith(".md"):
            yield abspath


def _build_file_index(vault_root):
    """lower(vault-relative path, extension kept) -> actual vault-relative path, for
    every file in the vault (md and non-md alike), so a link to a non-md asset can
    resolve as an existing file."""
    idx = {}
    for abspath in _walk_all(vault_root):
        relpath = _relpath(vault_root, abspath)
        idx[relpath.lower()] = relpath
    return idx


def _relpath(vault_root, abspath):
    return os.path.relpath(abspath, vault_root).replace(os.sep, "/")


def _no_ext(relpath):
    return relpath[:-3] if relpath.lower().endswith(".md") else relpath


def _load_notes(vault_root):
    """Explicit failure path: a file that cannot be read is skipped and warned about,
    never silently dropped and never a crash."""
    notes = []
    for abspath in _walk_md(vault_root):
        relpath = _relpath(vault_root, abspath)
        try:
            with open(abspath, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except (IOError, OSError) as e:
            sys.stderr.write("bm_vault_graph: cannot read %s: %s\n" % (relpath, e))
            continue
        notes.append({"relpath": relpath, "stem": _no_ext(relpath), "body": body,
                      "abspath": abspath})
    return notes


def _clean_link(raw):
    """Strip #heading and the trailing backslash a table's escaped "\\|" leaves behind
    once the regex above stops at the unescaped pipe."""
    s = raw.strip()
    if "#" in s:
        s = s.split("#", 1)[0]
    s = s.rstrip("\\").strip()
    return s


def _canon_keep_ext(path_str):
    s = path_str.replace("\\", "/").strip().strip("/")
    return posixpath.normpath(s) if s else s


def _canon(path_str):
    s = _canon_keep_ext(path_str)
    if s.lower().endswith(".md"):
        s = s[:-3]
    return s


def _project_prefix(source_stem):
    """The source note's own proximity scope, used to disambiguate a bare basename
    link with multiple matches: 10-Projects/<slug>/ when the source lives under a
    project, otherwise the source note's own directory subtree only (Obsidian's
    nearest-ancestor rule), never the whole top-level directory. Empty when the
    source sits at the vault root (no directory tree to scope to)."""
    parts = source_stem.split("/")
    if len(parts) >= 2 and parts[0] == "10-Projects":
        return "10-Projects/%s/" % parts[1]
    source_dir = posixpath.dirname(source_stem)
    return source_dir + "/" if source_dir else ""


def _build_indices(notes):
    exact = {}                # lower(stem) -> canonical stem
    by_basename = {}           # lower(basename) -> [stem, ...]
    for n in notes:
        stem = n["stem"]
        exact.setdefault(stem.lower(), stem)
        base = posixpath.basename(stem).lower()
        by_basename.setdefault(base, []).append(stem)
    return exact, by_basename


def _resolve(link_clean, source_stem, exact, by_basename, file_index):
    """Returns (target, kind): kind is "note" or "file" for a plain resolve, "ambiguous"
    for one resolved only via project-proximity among several basename matches, or
    (None, None) when nothing resolves. target is a note stem for "note"/"ambiguous",
    a vault-relative file path for "file"."""
    if not link_clean:
        return None, None
    cand = _canon(link_clean)
    if cand.lower() in exact:
        return exact[cand.lower()], "note"
    source_dir = posixpath.dirname(source_stem)
    joined = posixpath.normpath(posixpath.join(source_dir, cand)) if source_dir else cand
    if joined.lower() in exact:
        return exact[joined.lower()], "note"

    # Non-markdown vault file: exact vault-relative path (extension kept), or relative
    # to the source note's own directory.
    file_cand = _canon_keep_ext(link_clean)
    if file_cand.lower() in file_index:
        return file_index[file_cand.lower()], "file"
    file_joined = (posixpath.normpath(posixpath.join(source_dir, file_cand))
                   if source_dir else file_cand)
    if file_joined.lower() in file_index:
        return file_index[file_joined.lower()], "file"

    base = posixpath.basename(cand).lower()
    matches = by_basename.get(base, [])
    if len(matches) == 1:
        return matches[0], "note"
    if len(matches) > 1:
        prefix = _project_prefix(source_stem)
        if prefix:
            in_project = [m for m in matches if m.startswith(prefix)]
            if len(in_project) == 1:
                return in_project[0], "ambiguous"
    return None, None


def _typed_edge_targets(block, field_re, source_stem, exact, by_basename, file_index):
    """Every resolvable wikilink named on a supersedes:/relates: value line, plus every
    one that does NOT resolve, named separately so a typo in a frontmatter field reads
    as a broken edge rather than silently vanishing the way an unmatched link would if
    only the resolved list were kept. Returns (resolved_stems, broken_raw_links)."""
    m = field_re.search(block)
    if not m:
        return [], []
    resolved, broken = [], []
    for raw in WIKILINK.findall(m.group(1)):
        cleaned = _clean_link(raw)
        if not cleaned:
            continue
        target, kind = _resolve(cleaned, source_stem, exact, by_basename, file_index)
        if target is not None and kind in ("note", "ambiguous"):
            resolved.append(target)
        else:
            broken.append(cleaned)
    return resolved, broken


def _typed_edges(notes, exact, by_basename, file_index):
    """The typed graph promised by WBS 16: supersedes: as a directed edge (this note
    replaces that one) and relates: as an undirected one, both walked from frontmatter
    the same way status:/type: already are, never from prose. Returns a dict with:
    supersedes (stem -> [stems it supersedes]), superseded_by (the reverse index, built
    here rather than asked of the caller so a stale note's WHOLE inbound history is
    always present even if nothing on its own frontmatter names it), relates (stem ->
    sorted set of related stems, symmetric: an edge declared from either side appears on
    both), contradicts (stem -> sorted set of contradicting stems, symmetric like
    relates: for the same reason a contradiction runs both ways in meaning, declared
    from either or both sides), and broken (list of {source, field, link} for every
    supersedes:/relates:/contradicts: target that did not resolve to a real note, the
    same honesty the plain link walk already gives broken [[wikilinks]])."""
    supersedes = {}
    superseded_by = {}
    relates = {}
    contradicts = {}
    broken = []
    for n in notes:
        stem = n["stem"]
        block = _frontmatter_block(n["body"])
        if not block:
            continue
        sup_resolved, sup_broken = _typed_edge_targets(
            block, FRONT_SUPERSEDES, stem, exact, by_basename, file_index)
        rel_resolved, rel_broken = _typed_edge_targets(
            block, FRONT_RELATES, stem, exact, by_basename, file_index)
        con_resolved, con_broken = _typed_edge_targets(
            block, FRONT_CONTRADICTS, stem, exact, by_basename, file_index)
        if sup_resolved:
            supersedes[stem] = sorted(set(sup_resolved))
            for target in sup_resolved:
                superseded_by.setdefault(target, set()).add(stem)
        for link in sup_broken:
            broken.append({"source": stem, "field": "supersedes", "link": link})
        for target in rel_resolved:
            relates.setdefault(stem, set()).add(target)
            relates.setdefault(target, set()).add(stem)
        for link in rel_broken:
            broken.append({"source": stem, "field": "relates", "link": link})
        for target in con_resolved:
            contradicts.setdefault(stem, set()).add(target)
            contradicts.setdefault(target, set()).add(stem)
        for link in con_broken:
            broken.append({"source": stem, "field": "contradicts", "link": link})
    return {
        "supersedes": supersedes,
        "superseded_by": {k: sorted(v) for k, v in superseded_by.items()},
        "relates": {k: sorted(v) for k, v in relates.items()},
        "contradicts": {k: sorted(v) for k, v in contradicts.items()},
        "broken": broken,
    }


def _frontmatter_block(body):
    if not body.startswith("---"):
        return ""
    end = body.find("\n---", 3)
    return body[3:end] if end != -1 else ""


def _strip_frontmatter(body):
    """The body with any leading frontmatter block (and its closing --- line) removed,
    so a whitespace-only check never counts the frontmatter itself as content."""
    if not body.startswith("---"):
        return body
    end = body.find("\n---", 3)
    if end == -1:
        return body
    close_line_end = body.find("\n", end + 1)
    return body[close_line_end + 1:] if close_line_end != -1 else ""


def _rot_scan(vault_root, notes):
    """VB4-07 (vault rot detection, report-only, the Janitor port): zero-byte notes,
    whitespace-only notes (blank after stripping frontmatter and surrounding
    whitespace), and non-md attachments that no note's body names anywhere. Detection
    and reporting only, never a delete path: the estate law reserves removal for a
    human, so this returns paths for a report and touches nothing on disk."""
    empty_notes = []
    whitespace_notes = []
    for n in notes:
        try:
            size = os.path.getsize(n["abspath"])
        except OSError:
            size = len(n["body"].encode("utf-8", "replace"))
        if size == 0:
            empty_notes.append(n["relpath"])
        elif _strip_frontmatter(n["body"]).strip() == "":
            whitespace_notes.append(n["relpath"])

    all_text = "\n".join(n["body"] for n in notes)
    orphan_attachments = []
    for abspath in _walk_all(vault_root):
        if abspath.endswith(".md"):
            continue
        relpath = _relpath(vault_root, abspath)
        if relpath.startswith(TELEMETRY_PREFIX):
            continue
        if os.path.basename(relpath) not in all_text:
            orphan_attachments.append(relpath)

    return {
        "empty_notes": sorted(empty_notes),
        "whitespace_only_notes": sorted(whitespace_notes),
        "orphan_attachments": sorted(orphan_attachments),
    }


def _is_generated(body):
    """True when the note's own tags: list carries "generated" (the baked catalogs'
    tags: [catalog, generated]), so its outgoing links can be excluded from the
    structural-orphan count instead of laundering circularity into the headline
    orphan rate."""
    m = FRONT_TAGS.search(_frontmatter_block(body))
    if not m:
        return False
    tags = [t.strip() for t in m.group(1).split(",")]
    return "generated" in tags


def _measure(vault_root, notes):
    exact, by_basename = _build_indices(notes)
    file_index = _build_file_index(vault_root)
    wikilink_count = 0
    broken = []
    ambiguous_resolved = []
    template_skipped = 0
    inbound = Counter()
    structural_inbound = Counter()
    status_counter = Counter()
    type_counter = Counter()
    no_frontmatter = 0
    missing_status = 0
    missing_type = 0
    no_frontmatter_notes = []
    missing_status_notes = []
    missing_type_notes = []

    for n in notes:
        is_generated = _is_generated(n["body"])
        # Templates carry placeholder SLUG links by design; skip link extraction only,
        # frontmatter still counts below.
        if n["relpath"].startswith(TEMPLATES_PREFIX):
            template_skipped += 1
        else:
            for raw in WIKILINK.findall(n["body"]):
                cleaned = _clean_link(raw)
                if not cleaned:
                    continue
                wikilink_count += 1
                target, kind = _resolve(cleaned, n["stem"], exact, by_basename, file_index)
                if target is None:
                    broken.append({"source": n["relpath"], "link": cleaned})
                elif kind == "ambiguous":
                    # Honesty guard: proximity-resolved, never silently folded into the
                    # plain resolved pool.
                    ambiguous_resolved.append({"source": n["relpath"], "link": cleaned,
                                                "resolved_to": target})
                    inbound[target] += 1
                    if not is_generated:
                        structural_inbound[target] += 1
                elif kind == "note":
                    inbound[target] += 1
                    if not is_generated:
                        structural_inbound[target] += 1
                # kind == "file": resolves to a vault file, nothing to count as inbound
        block = _frontmatter_block(n["body"])
        if not block:
            # Exempt-population visibility: no frontmatter at all means the status/type
            # checks below never fire for this note, so it must be counted here or the
            # hole stays invisible (deleting a status line would otherwise look cleaner
            # than fixing it).
            no_frontmatter += 1
            missing_status += 1
            missing_type += 1
            no_frontmatter_notes.append(n["relpath"])
            missing_status_notes.append(n["relpath"])
            missing_type_notes.append(n["relpath"])
        else:
            m = FRONT_STATUS.search(block)
            if m:
                status_counter[m.group(1).strip()] += 1
            else:
                missing_status += 1
                missing_status_notes.append(n["relpath"])
            m = FRONT_TYPE.search(block)
            if m:
                type_counter[m.group(1).strip()] += 1
            else:
                missing_type += 1
                missing_type_notes.append(n["relpath"])

    orphan_count = sum(1 for n in notes if inbound.get(n["stem"], 0) == 0)
    orphan_pct = round(100.0 * orphan_count / len(notes), 2) if notes else 0.0
    structural_orphan_count = sum(1 for n in notes if structural_inbound.get(n["stem"], 0) == 0)
    structural_orphan_pct = (round(100.0 * structural_orphan_count / len(notes), 2)
                              if notes else 0.0)
    typed = _typed_edges(notes, exact, by_basename, file_index)
    rot = _rot_scan(vault_root, notes)
    return {
        "note_count": len(notes),
        "wikilink_count": wikilink_count,
        "broken_count": len(broken),
        "broken": broken,
        "ambiguous_resolved_count": len(ambiguous_resolved),
        "ambiguous_resolved": ambiguous_resolved,
        "template_skipped_count": template_skipped,
        "orphan_count": orphan_count,
        "orphan_pct": orphan_pct,
        "structural_orphan_count": structural_orphan_count,
        "structural_orphan_pct": structural_orphan_pct,
        "status_values": dict(status_counter),
        "type_values": dict(type_counter),
        "no_frontmatter_count": no_frontmatter,
        "missing_status_count": missing_status,
        "missing_type_count": missing_type,
        "no_frontmatter_notes": sorted(no_frontmatter_notes),
        "missing_status_notes": sorted(missing_status_notes),
        "missing_type_notes": sorted(missing_type_notes),
        # WBS 16, the knowledge-graph gap: supersedes: as a directed edge, relates: as
        # a symmetric one, both walked from frontmatter, never from prose. A typed-edge
        # count of 0 is not a defect (the recording contract has carried this field
        # since 2026-07-13 and almost nothing populates it yet); a nonzero broken count
        # is, since it means a real frontmatter field names a note that does not exist.
        "supersedes_edge_count": sum(len(v) for v in typed["supersedes"].values()),
        "relates_edge_count": sum(len(v) for v in typed["relates"].values()) // 2,
        # D10 (vault benchmark v2, contradictions preserved): contradicts_edge_count
        # is symmetric like relates_edge_count, so it is halved the same way.
        "contradicts_edge_count": sum(len(v) for v in typed["contradicts"].values()) // 2,
        "typed_broken_count": len(typed["broken"]),
        "typed_broken": typed["broken"],
        # VB4-07: rot detection, report-only, never a delete path.
        "empty_note_count": len(rot["empty_notes"]),
        "empty_notes": rot["empty_notes"],
        "whitespace_note_count": len(rot["whitespace_only_notes"]),
        "whitespace_only_notes": rot["whitespace_only_notes"],
        "orphan_attachment_count": len(rot["orphan_attachments"]),
        "orphan_attachments": rot["orphan_attachments"],
    }


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


def _measure_findings(stats):
    """stats' own violation/rot lists, reshaped into the shared {kind, path,
    detail} finding record. counts already carries the raw lists too (nothing
    is removed from the prose path), this only adds the uniform view."""
    findings = []
    for b in stats["broken"]:
        findings.append({"kind": "broken_link", "path": b["source"],
                          "detail": "[[%s]]" % b["link"]})
    for b in stats["typed_broken"]:
        findings.append({"kind": "broken_%s_edge" % b["field"], "path": b["source"],
                          "detail": "[[%s]]" % b["link"]})
    for a in stats["ambiguous_resolved"]:
        findings.append({"kind": "ambiguous_resolved", "path": a["source"],
                          "detail": "[[%s]] -> %s" % (a["link"], a["resolved_to"])})
    for p in stats["no_frontmatter_notes"]:
        findings.append({"kind": "no_frontmatter", "path": p, "detail": None})
    for p in stats["missing_status_notes"]:
        findings.append({"kind": "missing_status", "path": p, "detail": None})
    for p in stats["missing_type_notes"]:
        findings.append({"kind": "missing_type", "path": p, "detail": None})
    for p in stats["empty_notes"]:
        findings.append({"kind": "empty_note", "path": p, "detail": None})
    for p in stats["whitespace_only_notes"]:
        findings.append({"kind": "whitespace_only_note", "path": p, "detail": None})
    for p in stats["orphan_attachments"]:
        findings.append({"kind": "orphan_attachment", "path": p, "detail": None})
    return findings


def cmd_measure(args):
    vault = _vault_root(args.vault)
    notes = _load_notes(vault)
    if not notes:
        msg = "NO-DATA: no markdown files found under %s" % vault
        if args.json:
            _emit_json("bm_vault_graph.measure", "NO-DATA", {},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 3
    stats = _measure(vault, notes)
    if args.json:
        _emit_json("bm_vault_graph.measure", "PASS", stats, _measure_findings(stats))
        return 0
    print("vault: %s" % vault)
    print("notes: %d" % stats["note_count"])
    print("wikilinks: %d (%d broken, %d ambiguous-resolved)" % (
        stats["wikilink_count"], stats["broken_count"], stats["ambiguous_resolved_count"]))
    print("template notes skipped for links: %d" % stats["template_skipped_count"])
    print("missing frontmatter: %d no-block, %d missing status:, %d missing type:" % (
        stats["no_frontmatter_count"], stats["missing_status_count"],
        stats["missing_type_count"]))
    print("orphans: %d (%.2f%%)" % (stats["orphan_count"], stats["orphan_pct"]))
    print("structural orphans (ignoring generated catalogs): %d (%.2f%%)" % (
        stats["structural_orphan_count"], stats["structural_orphan_pct"]))
    print("status values: %s" % (", ".join(
        "%s=%d" % kv for kv in sorted(stats["status_values"].items())) or "none"))
    print("type values: %s" % (", ".join(
        "%s=%d" % kv for kv in sorted(stats["type_values"].items())) or "none"))
    print("typed edges: %d supersedes, %d relates, %d contradicts, %d broken" % (
        stats["supersedes_edge_count"], stats["relates_edge_count"],
        stats["contradicts_edge_count"], stats["typed_broken_count"]))
    for b in stats["typed_broken"]:
        print("  broken %s: %s -> [[%s]]" % (b["field"], b["source"], b["link"]))
    for b in stats["broken"]:
        print("  broken: %s -> [[%s]]" % (b["source"], b["link"]))
    for a in stats["ambiguous_resolved"]:
        print("  ambiguous-resolved: %s -> [[%s]] -> %s" % (
            a["source"], a["link"], a["resolved_to"]))
    print("rot: %d empty note(s), %d whitespace-only note(s), %d unreferenced attachment(s)"
          % (stats["empty_note_count"], stats["whitespace_note_count"],
             stats["orphan_attachment_count"]))
    if not (stats["empty_notes"] or stats["whitespace_only_notes"]
            or stats["orphan_attachments"]):
        print("  clean: no rot found")
    else:
        for p in stats["empty_notes"]:
            print("  empty note: %s" % p)
        for p in stats["whitespace_only_notes"]:
            print("  whitespace-only note: %s (blank after frontmatter)" % p)
        for p in stats["orphan_attachments"]:
            print("  unreferenced attachment: %s (no note references it by name)" % p)
    return 0


def _scoped_findings(notes, stats, paths):
    """The staged-file gate's findings, pure (no I/O, no printing): frontmatter
    validity plus outgoing broken links, both restricted to the named vault-
    relative paths. Split out of _cmd_check_scoped (VB10-01) so bm_vault_tiers.py
    can classify severity over the SAME findings instead of a second copy of
    this rule that could drift from it."""
    paths_set = set(paths)
    note_by_relpath = {n["relpath"]: n for n in notes}
    violations = []
    findings = []
    for relpath in sorted(paths_set):
        n = note_by_relpath.get(relpath)
        if n is None:
            continue  # not a note this walk saw (non-.md, or under a skipped dir)
        block = _frontmatter_block(n["body"])
        if not block:
            violations.append("no frontmatter block: %s" % relpath)
            findings.append({"kind": "no_frontmatter", "path": relpath, "detail": None})
            continue
        m = FRONT_STATUS.search(block)
        if not m:
            violations.append("missing status: %s" % relpath)
            findings.append({"kind": "missing_status", "path": relpath, "detail": None})
        elif m.group(1).strip() not in ALLOWED_STATUS:
            violations.append("bad status value %r: %s" % (m.group(1).strip(), relpath))
            findings.append({"kind": "bad_status_value", "path": relpath,
                              "detail": m.group(1).strip()})
        m = FRONT_TYPE.search(block)
        if not m:
            violations.append("missing type: %s" % relpath)
            findings.append({"kind": "missing_type", "path": relpath, "detail": None})
        elif m.group(1).strip() not in ALLOWED_TYPE:
            violations.append("bad type value %r: %s" % (m.group(1).strip(), relpath))
            findings.append({"kind": "bad_type_value", "path": relpath,
                              "detail": m.group(1).strip()})
    for b in stats["broken"]:
        if b["source"] in paths_set:
            violations.append("broken link: %s -> [[%s]]" % (b["source"], b["link"]))
            findings.append({"kind": "broken_link", "path": b["source"],
                              "detail": "[[%s]]" % b["link"]})
    return violations, findings


def _cmd_check_scoped(notes, stats, paths, json_out):
    """The staged-file gate: frontmatter validity and outgoing broken links, both
    restricted to the named vault-relative paths, so an unrelated bad note sitting
    elsewhere in the vault never blocks a commit that does not touch it."""
    paths_set = set(paths)
    violations, findings = _scoped_findings(notes, stats, paths)
    if json_out:
        verdict = "FAIL" if violations else "PASS"
        counts = {"path_count": len(paths_set), "violation_count": len(violations)}
        _emit_json("bm_vault_graph.check", verdict, counts, findings)
        return 2 if violations else 0
    print("staged-file check: %d path(s)" % len(paths_set))
    if violations:
        for v in violations:
            print(v)
        print("%d violation(s)" % len(violations))
        return 2
    print("OK: %d staged note(s) clean (frontmatter valid, no new broken links)"
          % len(paths_set))
    return 0


def cmd_check(args):
    vault = _vault_root(args.vault)
    notes = _load_notes(vault)
    json_out = getattr(args, "json", False)
    if not notes:
        msg = "NO-DATA: no markdown files found under %s" % vault
        if json_out:
            _emit_json("bm_vault_graph.check", "NO-DATA", {},
                       [{"kind": "no_data", "path": None, "detail": msg}])
        else:
            print(msg)
        return 3
    stats = _measure(vault, notes)
    paths = getattr(args, "paths", None)
    if paths:
        return _cmd_check_scoped(notes, stats, paths, json_out)
    violations = []
    findings = []
    for b in stats["broken"]:
        violations.append("broken link: %s -> [[%s]]" % (b["source"], b["link"]))
        findings.append({"kind": "broken_link", "path": b["source"],
                          "detail": "[[%s]]" % b["link"]})
    for val, count in sorted(stats["status_values"].items()):
        if val not in ALLOWED_STATUS:
            violations.append("bad status value %r on %d note(s)" % (val, count))
            findings.append({"kind": "bad_status_value", "path": None,
                              "detail": "%r on %d note(s)" % (val, count)})
    for val, count in sorted(stats["type_values"].items()):
        if val not in ALLOWED_TYPE:
            violations.append("bad type value %r on %d note(s)" % (val, count))
            findings.append({"kind": "bad_type_value", "path": None,
                              "detail": "%r on %d note(s)" % (val, count)})
    for relpath in stats["no_frontmatter_notes"]:
        violations.append("no frontmatter block: %s" % relpath)
        findings.append({"kind": "no_frontmatter", "path": relpath, "detail": None})
    for relpath in stats["missing_status_notes"]:
        violations.append("missing status: %s" % relpath)
        findings.append({"kind": "missing_status", "path": relpath, "detail": None})
    for relpath in stats["missing_type_notes"]:
        violations.append("missing type: %s" % relpath)
        findings.append({"kind": "missing_type", "path": relpath, "detail": None})
    for b in stats["typed_broken"]:
        violations.append("broken %s: %s -> [[%s]]" % (b["field"], b["source"], b["link"]))
        findings.append({"kind": "broken_%s_edge" % b["field"], "path": b["source"],
                          "detail": "[[%s]]" % b["link"]})
    if json_out:
        verdict = "FAIL" if violations else "PASS"
        counts = dict(stats)
        counts["violation_count"] = len(violations)
        _emit_json("bm_vault_graph.check", verdict, counts, findings)
        return 2 if violations else 0
    print("ambiguous-resolved: %d" % stats["ambiguous_resolved_count"])
    print("template notes skipped for links: %d" % stats["template_skipped_count"])
    print("missing frontmatter: %d no-block, %d missing status:, %d missing type:" % (
        stats["no_frontmatter_count"], stats["missing_status_count"],
        stats["missing_type_count"]))
    if violations:
        for v in violations:
            print(v)
        print("%d violation(s)" % len(violations))
        return 2
    print("OK: %d notes, 0 broken links, all status/type values known" % stats["note_count"])
    return 0


def cmd_edges(args):
    """The traversal query WBS 16 asked for: what does this note supersede, what
    superseded it, what does it relate to. --note takes the same spelling a wikilink to
    the note would (vault-relative path with or without .md), resolved the same way a
    link is, so a caller never has to know the tool's own internal stem format."""
    vault = _vault_root(args.vault)
    notes = _load_notes(vault)
    if not notes:
        print("NO-DATA: no markdown files found under %s" % vault)
        return 3
    exact, by_basename = _build_indices(notes)
    file_index = _build_file_index(vault)
    target, kind = _resolve(_clean_link(args.note), "", exact, by_basename, file_index)
    if target is None or kind not in ("note", "ambiguous"):
        print("NO-DATA: %r does not resolve to a note under %s" % (args.note, vault))
        return 3
    typed = _typed_edges(notes, exact, by_basename, file_index)
    if args.json:
        print(json.dumps({
            "note": target,
            "supersedes": typed["supersedes"].get(target, []),
            "superseded_by": typed["superseded_by"].get(target, []),
            "relates": typed["relates"].get(target, []),
            "contradicts": typed["contradicts"].get(target, []),
        }, indent=2, sort_keys=True))
        return 0
    print("note: %s" % target)
    print("supersedes: %s" % (", ".join(typed["supersedes"].get(target, [])) or "none"))
    print("superseded by: %s" % (", ".join(typed["superseded_by"].get(target, [])) or "none"))
    print("relates to: %s" % (", ".join(typed["relates"].get(target, [])) or "none"))
    print("contradicts: %s" % (", ".join(typed["contradicts"].get(target, [])) or "none"))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("measure", help="print graph health numbers")
    pm.add_argument("--vault", default=None)
    pm.add_argument("--json", action="store_true")
    pc = sub.add_parser("check", help="exit non-zero on drift")
    pc.add_argument("--vault", default=None)
    pc.add_argument("--paths", nargs="*", default=None,
                     help="scope the gate to these vault-relative note paths only")
    pc.add_argument("--json", action="store_true")
    pe = sub.add_parser("edges", help="what one note supersedes, is superseded by, "
                                       "and relates to")
    pe.add_argument("--vault", default=None)
    pe.add_argument("--note", required=True,
                     help="the note, spelled the way a [[wikilink]] to it would be")
    pe.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "measure":
        return cmd_measure(args)
    if args.cmd == "edges":
        return cmd_edges(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())

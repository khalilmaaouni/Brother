#!/usr/bin/env python3
"""test_erasure_propagation: a black-box proof of WHERE the BrotherModeUp governed
memory vault's erasure (forget-plan/forget-execute) actually reaches, and where it
does not, for the enterprise compliance persona's finding: "erasure is unproven
beyond the primary store; no evidence it propagates to catalogs, indexes, derived
notes, exports, or backups."

WHAT THIS PROVES, read directly from bm_vault_retention.py's own docstring rather
than assumed: forget-execute removes the note FILE and the note's own six rows in
the retrieval index (bm_vault_index.sqlite3: notes, notes_fts, anchors, links,
vectors, supersessions) -- that is the WHOLE claimed surface. Everything else
(assertions.jsonl, resolutions.jsonl, citations.jsonl, the event stream, digest
mentions, and -- confirmed by reading forget-plan's own class list, which never
names "catalog" at all -- the baked project catalogs) is either a named MANUAL
follow-up or entirely unmentioned. This suite treats those two classes of surface
differently and says so out loud:

  STRICT                          the live note file and the note's own index rows.
                                   Must read ABSENT after forget-execute or this
                                   suite FAILS. This is the actual product claim.
  RETAINED-IN-PRE-ERASURE-ARTIFACTS a bundle (bm_vault_export.py) and a tar backup,
                                   both produced BEFORE erasure. They WILL still
                                   carry the canary -- that is what "point in time"
                                   means -- and it is a real compliance fact to
                                   hand a GDPR reviewer, never a bug in this tool.
  NOT-PROPAGATED                  a note derived from the source BEFORE erasure
                                   (bm_vault_compose.py split) and the project
                                   catalog baked before erasure (bm_vault_catalog.py
                                   bake). Neither is touched by forget-execute --
                                   confirmed above by reading its own source, not
                                   assumed -- so both still carry the canary AFTER
                                   erasure, on a LIVE, currently-served file, not a
                                   frozen point-in-time export. This is the actual
                                   gap the compliance finding named.
  INFORMATIONAL                   a raw byte scan of the WHOLE index sqlite file.
                                   It legitimately still contains the canary after
                                   erasure, because the still-live derived note's
                                   own row was never asked to be forgotten. Reported
                                   for transparency; never scored, because scoring
                                   a shared literal string against the whole
                                   database conflates "this note's rows are gone"
                                   (proven separately, under STRICT) with "no other
                                   live document happens to share this string",
                                   which forget-execute never claimed to guarantee.

FIXTURE. One vault, one source note under 10-Projects/erasure-drill/ carrying a
unique canary token in a `claim: ... [evidence: ...]` line (so bm_vault_export.py's
assertions.jsonl table picks it up) and in a "## Extractable Section" heading's
body (so bm_vault_compose.py split carries it into a derived note). The source
note's own filename embeds the canary too, so the baked project catalog's
wikilink alias line carries it as plain text.

BLACK-BOX: every vault tool is invoked as its own CLI subprocess, exactly as a
real operator would run it. Nothing here imports a bm_vault_*.py module. Reading
back a receipt/JSONL/tar/sqlite file this suite's OWN fixture produced is data
inspection, not tool internals.

Resolves the vault tools directory from $BROTHERMODEUP_TOOLS, else
/tmp/bmu-main/tools (mirrors scripts/test_japanese_threshold.py's own NO-DATA
contract).

Exit 0 every STRICT surface came back ABSENT (and every setup command exited 0).
Exit 1 a STRICT surface still carried the canary (a real regression), or
--skip-erase was passed (driven-backwards mode: erasure never ran, so the STRICT
surfaces must, and do, still carry the canary -- confirming this suite can fail).
Exit 2 NO-DATA, the tools directory could not be found.

No em or en dashes anywhere in this file.
"""
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
import time
import uuid

# The hub's own product tree comes FIRST. Until 2026-09-02 the resolver walked
# straight to the retired BrotherModeUp checkout, whose bm_vault_retention.py
# has no forget verbs, so this drill failed on machine state that the hub had
# already left behind: 0 forget-plan hits there against 21 in the hub's tree.
# A drill inside the hub proves the hub's bytes, never a sibling checkout's.
HUB_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "products", "brothermode", "tools")
CONVENTIONAL_TOOLS_DIR = "/tmp/bmu-main/tools"
DURABLE_TOOLS_DIR = os.path.expanduser("~/Documents/BrotherModeUp/tools")

# The six per-note index tables forget-execute clears for the note it erases,
# named once here exactly as bm_vault_retention.py's own TABLE_KEYS names them
# (read from that file's source, never imported from it).
CHILD_TABLE_KEYS = (("notes_fts", "rowid"), ("anchors", "note_id"),
                    ("links", "note_id"), ("vectors", "note_id"),
                    ("supersessions", "by_note_id"))


def find_tools_dir():
    override = os.environ.get("BROTHERMODEUP_TOOLS")
    candidates = ([override] if override else []) + [HUB_TOOLS_DIR, CONVENTIONAL_TOOLS_DIR, DURABLE_TOOLS_DIR]
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "bm_vault.py")):
            return cand, None
    where = ("BROTHERMODEUP_TOOLS=%r, conventional %r" % (override, CONVENTIONAL_TOOLS_DIR)
             if override else "conventional %r" % CONVENTIONAL_TOOLS_DIR)
    return None, ("NO-DATA: bm_vault.py not found under any candidate (%s)" % where)


def run(tools_dir, tool, args, env, timeout=180):
    import subprocess
    cmd = [sys.executable, os.path.join(tools_dir, tool)] + args
    # cwd is the probe's own HOME: a store-walking tool must resolve ITS
    # .brothermode there, never the repository this probe happens to run from.
    p = subprocess.run(cmd, env=env, cwd=env.get("HOME") or None,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=timeout)
    return p.returncode, p.stdout


def make_env(home_dir):
    env = dict(os.environ)
    env["HOME"] = home_dir
    for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
        env.pop(k, None)
    os.makedirs(os.path.join(home_dir, ".claude"), exist_ok=True)
    os.makedirs(os.path.join(home_dir, ".brothermode"), exist_ok=True)
    return env


# ---------------------------------------------------------------------------
# Pure(ish) surface readers -- each takes a path and a needle, never a tool
# object, so scripts/test_test_erasure_propagation.py can exercise them
# against synthetic fixtures with no real vault or tools directory at all.
# ---------------------------------------------------------------------------

def file_contains(path, needle):
    """True/False if path is a readable file, None if the file does not exist."""
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return needle in fh.read()


def scan_vault_notes(vault, needle, exclude_relpaths):
    """Every *.md file under vault, except the named relpaths, that contains
    needle. Mirrors the walk convention every bm_vault_*.py tool in this tree
    already uses (skip dotdirs, *.md only)."""
    hits = []
    exclude = set(exclude_relpaths)
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, vault)
            if rel in exclude:
                continue
            with open(abspath, "rb") as fh:
                if needle in fh.read():
                    hits.append(rel)
    return sorted(hits)


def tar_contains(tar_path, needle):
    if not os.path.isfile(tar_path):
        return None
    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            if needle in fh.read():
                return True
    return False


def receipt_leaks_content(receipt_path, canary):
    """True if the forget-receipt carries the canary OUTSIDE its documented
    identifier fields (note_path, note_stable_id). forget-execute's own
    docstring promises the receipt never carries "the erased note's own
    text, claim content, or any matched line" -- but it explicitly DOES
    carry the erased note's own path and stable id as bookkeeping. This
    fixture's canary is deliberately embedded in the note's FILENAME (so
    the catalog surface test has something to find), which means the path
    field legitimately contains the canary substring; a raw byte scan
    would misreport that as a content leak. Clearing the two documented
    identifier fields first checks the actual promise, not this fixture's
    own filename choice."""
    with open(receipt_path, encoding="utf-8") as fh:
        receipt = json.load(fh)
    receipt.pop("note_path", None)
    receipt.pop("note_stable_id", None)
    return canary in json.dumps(receipt)


def sqlite_note_row_id(db_path, abspath):
    """The notes.id for path=abspath, or None (no such row, or no db file)."""
    if not os.path.isfile(db_path):
        return None
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT id FROM notes WHERE path=?", (abspath,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def sqlite_child_counts(db_path, note_id):
    """{table: row count for note_id} over CHILD_TABLE_KEYS, or None when the
    db or the note_id is unavailable."""
    if not os.path.isfile(db_path) or note_id is None:
        return None
    con = sqlite3.connect(db_path)
    try:
        return {table: con.execute("SELECT COUNT(*) FROM %s WHERE %s=?" % (table, key),
                                   (note_id,)).fetchone()[0]
                for table, key in CHILD_TABLE_KEYS}
    finally:
        con.close()


def verdict(setup_checks, surfaces):
    """(passed, setup_failed, strict_failed): pure aggregation over the two
    result lists this drill builds, so the pass/fail RULE itself (STRICT
    surfaces must read ABSENT; every setup command must have exited 0; every
    other bucket is informational and never fails the run) is one function
    scripts/test_test_erasure_propagation.py can drive with fabricated
    fixtures, with no subprocess and no real vault involved."""
    setup_failed = [c for c in setup_checks if not c["passed"]]
    strict_failed = [s for s in surfaces if s["bucket"] == "STRICT" and s["status"] != "ABSENT"]
    passed = not setup_failed and not strict_failed
    return passed, setup_failed, strict_failed


# ---------------------------------------------------------------------------
# The drill itself
# ---------------------------------------------------------------------------

def run_drill(tools_dir, skip_erase):
    root = tempfile.mkdtemp(prefix="erasure-prop-")
    vault = os.path.join(root, "vault")
    home = os.path.join(root, "home")
    os.makedirs(vault, exist_ok=True)
    env = make_env(home)

    canary = "erase-canary-" + uuid.uuid4().hex[:12]
    note_id = "n-" + uuid.uuid4().hex[:16]
    proj_rel_dir = "10-Projects/erasure-drill"
    source_rel = "%s/%s.md" % (proj_rel_dir, canary)
    source_abspath = os.path.join(vault, source_rel)
    catalog_rel = "%s/Catalog.md" % proj_rel_dir
    catalog_abspath = os.path.join(vault, catalog_rel)

    checks = []

    def add_check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail[:500]})

    os.makedirs(os.path.dirname(source_abspath), exist_ok=True)
    note_text = (
        "---\n"
        "id: %s\n"
        "type: reference\n"
        "status: open\n"
        "security_label: internal\n"
        "created: 2026-08-30\n"
        "---\n\n"
        "# Erasure Canary Fixture\n\n"
        "claim: the canary fact %s must be forgotten everywhere it propagated "
        "to [evidence: repo:abc1234]\n\n"
        "## Extractable Section\n\n"
        "This paragraph is split into its own derived note before erasure and "
        "will still carry %s afterward.\n"
    ) % (note_id, canary, canary)
    with open(source_abspath, "w", encoding="utf-8") as fh:
        fh.write(note_text)

    # A correction-rule store must exist before a store-walking bm_vault
    # will index; init is idempotent and lands in the probe's own HOME.
    run(tools_dir, "bm_store.py", ["init"], env)
    rc, out = run(tools_dir, "bm_vault.py", ["index", "--vault", vault], env, timeout=300)
    add_check("index (initial)", rc == 0, out)

    rc, out = run(tools_dir, "bm_vault_compose.py",
                  ["split", "--vault", vault, "--note", canary, "--heading",
                   "Extractable Section", "--today", "2026-08-30", "--apply"], env)
    add_check("compose split (derive a note before erasure)", rc == 0, out)
    m = re.search(r"new note:\s*(\S+)\s*\(id", out)
    derived_rel = m.group(1) if m else None  # new_rel as printed already carries .md
    add_check("derived note path parsed from split output", derived_rel is not None, out)

    rc, out = run(tools_dir, "bm_vault.py", ["index", "--vault", vault], env, timeout=300)
    add_check("index (post-derive, so the derived note is indexed too)", rc == 0, out)

    rc, out = run(tools_dir, "bm_vault_catalog.py", ["bake", "--vault", vault], env)
    add_check("catalog bake (before erasure)", rc == 0, out)

    bundle_before = os.path.join(root, "bundle-before")
    rc, out = run(tools_dir, "bm_vault_export.py",
                  ["bundle", "--vault", vault, "--out", bundle_before], env)
    add_check("export bundle (before erasure)", rc == 0, out)

    tar_before = os.path.join(root, "backup-before.tar")
    with tarfile.open(tar_before, "w") as tf:
        tf.add(vault, arcname="vault")
    add_check("backup tar (before erasure)", os.path.isfile(tar_before), tar_before)

    index_db = os.path.join(home, ".claude", "bm_vault_index.sqlite3")
    old_index_id = sqlite_note_row_id(index_db, source_abspath)
    old_child_counts = sqlite_child_counts(index_db, old_index_id)
    add_check("source note was indexed before erasure", old_index_id is not None,
             "index_id=%s child_counts=%s" % (old_index_id, old_child_counts))

    receipt_path = None
    if not skip_erase:
        plan_path = os.path.join(root, "forget-plan.json")
        rc, out = run(tools_dir, "bm_vault_retention.py",
                      ["forget-plan", "--vault", vault, "--id", note_id, "--out", plan_path], env)
        add_check("forget-plan", rc == 0, out)
        rc, out = run(tools_dir, "bm_vault_retention.py",
                      ["forget-execute", "--vault", vault, "--plan", plan_path], env)
        add_check("forget-execute", rc == 0, out)
        rm = re.search(r"receipt:\s*(\S+)\s*content_hash=", out)
        receipt_path = rm.group(1) if rm else None
    else:
        add_check("forget-plan/forget-execute", True, "SKIPPED: --skip-erase (driven backwards)")

    needle = canary.encode("utf-8")
    surfaces = []

    def add_surface(name, bucket, found, detail):
        status = "FOUND" if found is True else ("ABSENT" if found is False else "NO-DATA")
        surfaces.append({"name": name, "bucket": bucket, "status": status,
                        "detail": str(detail)[:400]})

    add_surface("live tree: source note file itself", "STRICT",
               os.path.isfile(source_abspath), source_abspath)

    exclude = {source_rel, catalog_rel}
    if derived_rel:
        exclude.add(derived_rel)
    other_hits = scan_vault_notes(vault, needle, exclude)
    add_surface("live tree: every OTHER remaining note", "STRICT",
               len(other_hits) > 0, "hits=%s" % other_hits)

    new_index_id = sqlite_note_row_id(index_db, source_abspath)
    add_surface("index: notes row for the erased note's own path", "STRICT",
               new_index_id is not None,
               "old_id=%s new_row=%s" % (old_index_id, new_index_id))

    if old_index_id is not None:
        new_child_counts = sqlite_child_counts(index_db, old_index_id)
        leftover = {t: c for t, c in (new_child_counts or {}).items() if c}
        add_surface("index: the erased note's own child rows "
                   "(notes_fts/anchors/links/vectors/supersessions)", "STRICT",
                   len(leftover) > 0,
                   "before=%s after=%s" % (old_child_counts, new_child_counts))
    else:
        add_surface("index: the erased note's own child rows "
                   "(notes_fts/anchors/links/vectors/supersessions)", "STRICT", None,
                   "NO-DATA: source note's own index id was never captured")

    if receipt_path and os.path.isfile(receipt_path):
        add_surface("forget receipt: no erased content leaked into it", "STRICT",
                   receipt_leaks_content(receipt_path, canary), receipt_path)
    else:
        surfaces.append({"name": "forget receipt: no erased content leaked into it",
                        "bucket": "NOT-APPLICABLE", "status": "NO-DATA",
                        "detail": "no receipt (erasure was skipped or never wrote one)"})

    export_hits = {}
    for fn in ("assertions.jsonl", "events.jsonl"):
        export_hits[fn] = file_contains(os.path.join(bundle_before, fn), needle)
    add_surface("export bundle produced BEFORE erasure", "RETAINED-IN-PRE-ERASURE-ARTIFACTS",
               any(v for v in export_hits.values() if v), export_hits)

    add_surface("backup tar produced BEFORE erasure", "RETAINED-IN-PRE-ERASURE-ARTIFACTS",
               tar_contains(tar_before, needle), tar_before)

    if derived_rel:
        add_surface("derived note (compose split, before erasure of its source)",
                   "NOT-PROPAGATED",
                   file_contains(os.path.join(vault, derived_rel), needle), derived_rel)
    else:
        surfaces.append({"name": "derived note (compose split, before erasure of its source)",
                        "bucket": "NOT-APPLICABLE", "status": "NO-DATA",
                        "detail": "compose split did not report a new note"})

    add_surface("catalog baked BEFORE erasure (never auto-rebaked by forget-execute)",
               "NOT-PROPAGATED", file_contains(catalog_abspath, needle), catalog_rel)

    add_surface("index db, whole-file strings scan (informational)", "INFORMATIONAL",
               file_contains(index_db, needle),
               "a hit here is expected: the still-live derived note's own row carries "
               "the same canary and was never asked to be forgotten; see the two STRICT "
               "index surfaces above for the actual per-note claim")

    return checks, surfaces, {"canary": canary, "vault": vault, "skip_erase": skip_erase,
                             "scratch_root": root}


def render_table(surfaces):
    lines = ["%-70s %-32s %s" % ("SURFACE", "BUCKET", "STATUS"), "-" * 118]
    for s in surfaces:
        lines.append("%-70s %-32s %s" % (s["name"][:70], s["bucket"], s["status"]))
    return "\n".join(lines)


def main():
    tools_dir, err = find_tools_dir()
    if err:
        print(err, file=sys.stderr)
        return 2

    skip_erase = "--skip-erase" in sys.argv[1:]
    t0 = time.time()
    checks, surfaces, meta = run_drill(tools_dir, skip_erase)
    wall = round(time.time() - t0, 3)

    passed, setup_failed, strict_failed = verdict(checks, surfaces)

    print(render_table(surfaces))
    print()
    result = {
        "drill": "test_erasure_propagation",
        "mode": "skip-erase (driven backwards)" if skip_erase else "forward",
        "tools_dir": tools_dir,
        "passed": passed,
        "wall_seconds": wall,
        "meta": meta,
        "setup_checks": checks,
        "surfaces": surfaces,
    }
    print(json.dumps(result, indent=2, sort_keys=True))

    if setup_failed:
        print("\nSETUP FAILED (%d):" % len(setup_failed), file=sys.stderr)
        for c in setup_failed:
            print("  %s :: %s" % (c["name"], c["detail"][:300]), file=sys.stderr)
    if strict_failed:
        print("\nSTRICT SURFACE STILL CARRIED THE CANARY (%d):" % len(strict_failed),
              file=sys.stderr)
        for s in strict_failed:
            print("  [%s] %s :: %s" % (s["status"], s["name"], s["detail"][:300]),
                  file=sys.stderr)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

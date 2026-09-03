#!/usr/bin/env python3
"""bm_vault_realdata: real business data as a golden set for the vault's
master-data models (hierarchy edges, the closure table, the crosswalk).

WHY THIS EXISTS. Every calibration for bm_vault_shapes.py, bm_vault_closure.py
and bm_vault_crosswalk.py so far is a hand-built fixture: a few notes with a
few made-up parents, invented to exercise one grammar rule at a time. That
proves the parsers, but it never proves the models answer a question a real
corporate group actually poses: three levels of consolidation, a real foreign
identifier scheme, real non-ASCII legal names. This module materializes one
such corpus, GLEIF's public LEI data for the Toyota corporate group (CC0,
tools/fixtures/gleif-toyota-group.json), into a fixture vault and runs the
three models against it, so "the models work" is checked against a business
answer that can be looked up independently (GLEIF's own record), not just
against a fixture this suite also wrote.
BUILD. One entity note per LEI record under 30-Entities/, mirroring the
note() template test_bm_vault_shapes.py already calibrates against:
  id:              n- plus the first 16 hex chars of sha1(LEI), deterministic
                   so the same fixture always mints the same ids (mint()'s
                   own uuid4 randomness is the right choice for a live vault,
                   wrong for a fixture whose ids must be reproducible run to
                   run for a test to compare against).
  type: entity, entity: company
  source_ids:      [plugin:lei:<LEI>]. bm_vault_crosswalk.py's SYSTEMS enum
                   (github/path/vault/plugin/artifact) has no lei entry and
                   is out of this module's writable scope, so the LEI rides
                   inside a plugin: entry as plugin:lei:<LEI>. Because only
                   the first colon splits system from id, entry["ident"] is
                   still the exact string lei:<LEI>, so crosswalk resolve
                   --source-id lei:<LEI> (the bare, system-unqualified form
                   the crosswalk contract's own docstring says resolve
                   accepts) matches it precisely: no crosswalk contract
                   change, no loss of the LEI namespace as a resolvable key.
  legal_name, legal_name_lang, country, registration_status: plain frontmatter
                   fields, informational only. Neither bm_vault_shapes.py nor
                   bm_vault_crosswalk.py nor bm_vault_closure.py reads them,
                   so no formal attribute contract (bm_vault_attributes.py's
                   CLASS_ATTRIBUTES) is invoked for a class this fixture
                   never registers.
  hierarchy_edges: [name=legal;parent=<parent LEI>;valid_from=<date>] on every
                   child, exactly bm_vault_shapes.py's own grammar. The parent
                   reference is the parent's filename stem, which is the
                   parent's LEI (files are named <LEI>.md), matching the
                   doctrine bm_vault_shapes.py documents: hierarchy references
                   are human-typed short names resolved against the vault's
                   basename stems, not against the id field. valid_from is
                   the child's own GLEIF lastUpdate date (the date the
                   registration record itself was last confirmed), falling
                   back to 2026-01-01 if that field is missing or malformed.

CHECK. Builds the fixture vault in a tempdir, then runs bm_vault_shapes.py's
check, bm_vault_closure.py's rebuild then verify, and bm_vault_crosswalk.py's
check against it, reusing each tool's own cmd_* function directly, in
process, never a second parser or a second interval walk. The closure model's
STORE_PATH is a single fixed path under ~/.claude (by that module's own
design: a maintained materialization, not a per-vault artifact), so this
module ALWAYS monkeypatches it to a path inside the same tempdir before
calling rebuild/verify and restores it afterward: a check run here must
never overwrite whatever real closure any other session on this machine has
built. Three golden assertions follow, checked independently of the tools'
own PASS/FAIL so a tool that reports clean while answering the wrong business
fact is still caught: the ancestor chain for TOYOTA FINANCE AUSTRALIA LIMITED
reaches the root through the Financial Services subsidiary in exactly two
hops, the stored closure contains that (root, AU) pair, and the crosswalk
resolves the AU entity's LEI back to its note.

A missing or unreadable fixture is NO-DATA (exit 2), named with its path,
never a silent empty pass, the same doctrine every sibling in this directory
holds. Exit 0 all models and goldens pass, 1 any FAIL.

Python 3.9 floor, standard library only, no network (the fetch already
happened; this module only ever reads the committed fixture).
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_closure as cl     # noqa: E402 -- reuse rebuild/verify, never re-derive the walk
import bm_vault_crosswalk as xw   # noqa: E402 -- reuse check/resolve
import bm_vault_shapes as sh      # noqa: E402 -- reuse check/resolve-hierarchy

ENTITIES_SUBDIR = "30-Entities"


def _entity_id(lei):
    """Deterministic n-<16 hex> id: sha1(LEI) truncated, never uuid4. The
    fixture must mint the same id on every rebuild for a test to compare
    the note's id across runs; mint()'s randomness in bm_vault_ids.py is
    right for a live vault and wrong here."""
    return "n-" + hashlib.sha1(lei.encode("utf-8")).hexdigest()[:16]


def _date_part(value):
    """The date portion of a GLEIF lastUpdate timestamp, or None if the
    field is missing or carries no "T" to split on. Never raises on a
    malformed record: a bad date is the caller's fallback to apply, not
    this module's crash."""
    if not value or "T" not in value:
        return None
    return value.split("T", 1)[0]


def load_fixture(path):
    """(data, error). error is a NO-DATA line naming the path, never raised,
    on a missing file or invalid JSON. data carries root/records/edges,
    checked present here so a caller never KeyErrors on a malformed fixture
    later, deep inside build."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return None, "bm_vault_realdata: NO-DATA, no readable fixture at %r (%s)" % (path, exc)
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, "bm_vault_realdata: NO-DATA, fixture at %r is not valid JSON (%s)" % (path, exc)
    for key in ("root", "records", "edges"):
        if key not in data:
            return None, ("bm_vault_realdata: NO-DATA, fixture at %r is missing "
                           "required key %r" % (path, key))
    return data, None


def _note_text(lei, rec):
    """The frontmatter and body for one entity note, mirroring
    test_bm_vault_shapes.py's own note() template. rec never carries
    hierarchy_edges itself; the caller splices that line in separately
    because it needs the whole edge list for this LEI, not just this record."""
    legal_name = rec.get("legalName", "")
    lines = [
        "---",
        "id: %s" % _entity_id(lei),
        "type: entity",
        "entity: company",
        "source_ids: [plugin:lei:%s]" % lei,
        'legal_name: "%s"' % legal_name,
        "legal_name_lang: %s" % rec.get("legalName_lang", ""),
        "country: %s" % rec.get("country", ""),
        "registration_status: %s" % rec.get("registration_status", ""),
    ]
    return lines, legal_name


def build_vault(data, out_dir):
    """Materializes the fixture into out_dir/30-Entities/<LEI>.md, one note
    per record, hierarchy_edges spliced onto every note that is a child in
    at least one edge. Edges naming a LEI absent from records still produce
    a hierarchy_edges entry on the child that survives (a dangling parent
    reference, deliberately left for bm_vault_shapes.py's own check to find:
    this function never filters an edge against records, that is the
    check's job, not the builder's). Returns the entities directory path."""
    records = data["records"]
    edges = data["edges"]
    parents_by_child = {}
    for e in edges:
        parents_by_child.setdefault(e["child"], []).append(e["parent"])

    entities_dir = os.path.join(out_dir, ENTITIES_SUBDIR)
    os.makedirs(entities_dir, exist_ok=True)
    for lei, rec in records.items():
        lines, legal_name = _note_text(lei, rec)
        parents = parents_by_child.get(lei, [])
        if parents:
            child_date = _date_part(rec.get("lastUpdate", "")) or "2026-01-01"
            entry = ", ".join(
                "name=legal;parent=%s;valid_from=%s" % (p, child_date) for p in parents)
            lines.append("hierarchy_edges: [%s]" % entry)
        lines += ["---", "", "# %s" % legal_name]
        text = "\n".join(lines) + "\n"
        with open(os.path.join(entities_dir, "%s.md" % lei), "w", encoding="utf-8") as fh:
            fh.write(text)
    return entities_dir


def _capture(fn, *args):
    """(rc, combined stdout+stderr text) for one of the sibling tools' own
    cmd_* functions, run in process. Mirrors the redirect_stdout/stderr
    helper every sibling test_bm_vault_*.py file already uses."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue() + err.getvalue()


def cmd_check(data):
    """Builds the fixture vault in a tempdir, runs the three models against
    it plus the three golden business assertions, prints one PASS/FAIL line
    per check with the underlying evidence, and returns 0 only if every one
    of them passed."""
    root = data["root"]
    au = "3UKPTDP5PGQRH8AUK042"
    fs = "353800WDOBRSAV97BA75"
    as_of = datetime.date.today()

    tmp = tempfile.mkdtemp(prefix="bm-realdata-")
    try:
        build_vault(data, tmp)
        vault = tmp  # sibling tools walk the vault root recursively
        results = []

        rc, out = _capture(sh.cmd_check, vault)
        results.append(("shapes (bm_vault_shapes.py check)", rc == 0, out))

        closure_store = os.path.join(tmp, "closure-store.json")
        orig_store = cl.STORE_PATH
        cl.STORE_PATH = closure_store  # never touch the machine's real closure store
        try:
            rc_r, out_r = _capture(cl.cmd_rebuild, vault, as_of)
            closure_text = out_r
            closure_ok = rc_r == 0
            if closure_ok:
                rc_v, out_v = _capture(cl.cmd_verify, vault)
                closure_ok = rc_v == 0
                closure_text += out_v
            results.append(("closure (bm_vault_closure.py rebuild+verify)", closure_ok, closure_text))

            rc, out = _capture(xw.cmd_check, vault)
            results.append(("crosswalk (bm_vault_crosswalk.py check)", rc == 0, out))

            rc, out = _capture(sh.cmd_resolve_hierarchy, vault, au, "legal", as_of)
            chain_ok = rc == 0 and ("%s -> %s" % (fs, root)) in out
            results.append(("golden: AU resolves to root through the FS subsidiary",
                             chain_ok, out))

            pair_ok, pair_evidence = False, "closure store was never written"
            if os.path.exists(closure_store):
                with open(closure_store, encoding="utf-8") as fh:
                    stored = json.load(fh)
                hits = [r for r in stored.get("rows", [])
                        if r["ancestor"] == root and r["descendant"] == au]
                pair_ok = bool(hits)
                pair_evidence = "closure rows with ancestor=%s descendant=%s: %d" % (
                    root, au, len(hits))
            results.append(("golden: closure table contains (root, AU)", pair_ok, pair_evidence))

            rc, out = _capture(xw.cmd_resolve, vault, "lei:%s" % au)
            resolve_ok = rc == 0 and au in out
            results.append(("golden: crosswalk resolves lei:%s to the AU note" % au,
                             resolve_ok, out))
        finally:
            cl.STORE_PATH = orig_store

        ok = all(passed for _, passed, _ in results)
        for label, passed, evidence in results:
            print("[%s] %s" % (label, "PASS" if passed else "FAIL"))
            for line in str(evidence).rstrip("\n").splitlines():
                print("    %s" % line)
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("build", "check"))
    ap.add_argument("--fixture", required=True, help="path to a GLEIF-shaped fixture JSON")
    ap.add_argument("--out", help="output vault directory; required for build")
    args = ap.parse_args(argv)

    data, err = load_fixture(args.fixture)
    if err:
        print(err, file=sys.stderr)
        return 2

    if args.command == "build":
        if not args.out:
            print("bm_vault_realdata: build needs --out", file=sys.stderr)
            return 2
        entities_dir = build_vault(data, args.out)
        print("built %d entity note(s) under %s" % (len(data["records"]), entities_dir))
        return 0

    return cmd_check(data)


if __name__ == "__main__":
    sys.exit(main())

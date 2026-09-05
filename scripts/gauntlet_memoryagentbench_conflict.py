#!/usr/bin/env python3
"""The MemoryAgentBench conflict resolution and selective forgetting self score. Row LL-6.

WHAT THIS IS. MemoryAgentBench (ICLR 2026, arxiv.org/abs/2507.05257) names four
memory competencies for an LLM agent and states its own finding plainly: "current
methods fall short of mastering all four competencies". No vendor page opened the
night this row was written publishes a score against it. This script prints one:
a SELF SCORE for Brother's vault retrieval (products/brothermode/tools/bm_vault.py)
and its supersession resolver against the paper's own public Conflict_Resolution
data, with NO-DATA where the data does not map to a note the vault can hold.

WHICH COMPETENCY, AND WHY ONE DATASET COVERS BOTH. The paper's own four named
competencies are Accurate Retrieval, Test-Time Learning, Long-Range Understanding
and Selective Forgetting. Its own four published dataset splits are
Accurate_Retrieval, Conflict_Resolution, Long_Range_Understanding and
Test_Time_Learning: there is no separate Selective_Forgetting split. The
Conflict_Resolution split's own task (FactConsolidation: a fact is stated, then
restated with a different value later in the same context, and the query asks
for the CURRENT value) is the one place in the public data that exercises both
named competencies at once: answering it correctly needs resolving which of two
conflicting facts is current (conflict resolution) AND not answering with the
superseded one (selective forgetting). This is why one fixture, one script, and
one pair of scores below cover both names in the row this was briefed against.

THE FIXTURE. benchmarks/third-party/memoryagentbench/conflict-resolution-sh6k-fixture.json,
built once from the real public dataset (ai-hyz/MemoryAgentBench on HuggingFace,
MIT licensed, HF commit 7ea066982b140a19337e17e60d45d4076e042faf, source parquet
sha256 24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45), one row of
the Conflict_Resolution split (metadata.source == "factconsolidation_sh_6k", the
smallest single-hop context variant, chosen for a fast deterministic run, not for
a better score). The fixture's own "_provenance" block carries the full extraction
method and every number this docstring restates. This is REAL benchmark data, not
an invented fixture: the source was downloadable without an account (checked
2026-09-05, HTTP 200, no auth header), so the paper's own format did not need to
be reconstructed.

HOW A NOTE IS SEEDED. Each mapped item becomes exactly two vault notes in one
FRESH, ISOLATED, in-memory sqlite vault built directly through bm_vault.py's own
_schema()/_upsert_note() (the same functions bm_vault_jbench.py's fixture builder
uses, and the same reason: a benchmark needs a known, reproducible corpus, never
the real vault at ~/.claude/bm_vault_index.sqlite3). The OLDER note carries the
item's superseded fact text at mtime = 2*item_index (its recorded_at, the item's
own order in the source context, never wall-clock time); the NEWER note carries
the current fact text at mtime = 2*item_index + 1, plus a `supersedes: [[<old
note's stem>]]` frontmatter line, the exact edge bm_vault.py's own
_rebuild_supersessions() reads (FRONT_SUPERSEDES, matched against the fixed
supersedes: syntax, never prose). This script never edits bm_vault.py: it calls
_rebuild_supersessions(con) itself after seeding, since the file-walk indexer
(cmd_index) is the only caller that does so today and this script never runs
through a file-walk.

HOW A QUESTION IS SCORED. RETRIEVAL ONLY, never an answer a model would have to
phrase, per this row's own brief: bm_vault.py's real fused search
(_search(con, text=question, fast=True), the same ranked (note_id, score) list
cmd_recall and cmd_check both consume) is run for the item's question, then this
script walks that ranked list from the top and calls _superseded_by(con, path)
on each hit exactly the way _print_hits does at the point of serving a result,
skipping every superseded hit, and calls the first hit that is NOT superseded the
"served" note. This reproduces the real serve-time behaviour without needing the
freshness/lifecycle/contradiction layers _print_hits also carries, none of which
apply to this fixture (no stale anchors, no candidate notes, no declared
contradictions here; only the supersession edge every item declares).

  conflict resolution, per item: correct when the served note is the NEWER
  (current) one. This is the paper's own rule restated for retrieval: "the
  answer must reflect the latest valid fact and not the superseded one."

  selective forgetting, per item WHERE THE MECHANISM WAS ACTUALLY EXERCISED
  (the older note appeared anywhere in the fused ranked list for that query,
  so there was something to withhold): correct when the served note is NOT
  the older one, i.e. the older note was found and skipped rather than served.
  An item where the older note never appears in the fused list at all reports
  NO-DATA for this one competency line only (nothing was there to withhold),
  and is still scored for conflict resolution above.

NO-DATA, TWO KINDS, NEVER A PASS.
  (1) format mapping: of the 100 (question, answer) pairs in the source row,
      26 could not be mapped to a (current fact, superseded fact) pair by this
      extraction (ambiguous or single-hit matches, no conflicting restatement
      found); recorded in the fixture's own "no_data" list and reported as a
      count here, excluded from both competency denominators.
  (2) selective forgetting per item, as above, when the older note was never a
      retrieval candidate for that query.

DETERMINISTIC. No model call anywhere in this scorer: every verdict is a lookup
against a ranked id list and a boolean from bm_vault.py's own supersession table.

Python 3.9, standard library only, no network at run time (the dataset was
already fetched and reduced into the committed fixture above; see its own
_provenance block for the one-time download this script never repeats).

No em or en dashes anywhere in this file.

Run: python3 scripts/gauntlet_memoryagentbench_conflict.py
Exit 0: conflict resolution and (where exercised) selective forgetting both hit
100 percent. Exit 1: at least one item was wrong on either scored line. Exit 2:
the fixture is missing, unreadable, or carries zero mapped items.
"""
import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
TOOLS_DIR = os.path.join(REPO_ROOT, "products", "brothermode", "tools")
VAULT_TOOL = os.path.join(TOOLS_DIR, "bm_vault.py")
RESULTS_DIR = os.path.join(REPO_ROOT, "benchmarks", "results")
DEFAULT_FIXTURE = os.path.join(
    REPO_ROOT, "benchmarks", "third-party", "memoryagentbench",
    "conflict-resolution-sh6k-fixture.json")

NODATA = "NO-DATA"
DEFAULT_LIMIT = 10


def _load_bm_vault():
    """Dynamic import by path, the same defensive pattern bm_vault_jbench.py's own
    _load_bm_vault uses for this exact sibling module, so this file's behaviour
    never drifts if a bare `import bm_vault` would resolve to a different copy."""
    spec = importlib.util.spec_from_file_location("bm_vault", VAULT_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_fixture(path):
    """(fixture_dict, None) or (None, "NO-DATA: ..."). Never raises."""
    if not path or not os.path.isfile(path):
        return None, "%s: no fixture file at %r" % (NODATA, path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "%s: %r is not readable JSON (%s)" % (NODATA, path, e)
    if not isinstance(data, dict) or not data.get("items"):
        return None, "%s: %r carries no mapped items" % (NODATA, path)
    return data, None


def _note_body(title, fact_text, supersedes_stem=None):
    lines = ["---", "name: %s" % title, "description: %s" % title, "type: project"]
    if supersedes_stem:
        lines.append("supersedes: [[%s]]" % supersedes_stem)
    lines += ["---", fact_text, ""]
    return "\n".join(lines)


def build_fixture_vault(bm, fixture):
    """A fresh sqlite3 connection carrying two notes per mapped item (the
    superseded fact and the current one that supersedes it), and
    {item_id: {"new": note_id, "old": note_id, "old_path": path}}.

    mtime is 2*item_index for the older note and 2*item_index + 1 for the
    newer one: the item's own position in the fixture, never wall-clock time,
    and always older-before-newer per the fixture's own verified ordering
    (see the fixture's _provenance.extraction_method note).

    item.get("no_supersession"): a test-only seam (never set by the real
    fixture built from the paper's data, where every mapped item states a
    real conflict and always declares it). Skips the supersedes: edge, so a
    test can prove the scorer correctly reports "wrong" when nothing in the
    vault says which fact is current, the way an unresolved contradiction
    reads today."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    bm._schema(con)
    ids = {}
    for i, item in enumerate(fixture["items"]):
        item_id = item["id"]
        old_stem = "%s-old" % item_id
        new_stem = "%s-new" % item_id
        old_path = "memoryagentbench-conflict/%s.md" % old_stem
        new_path = "memoryagentbench-conflict/%s.md" % new_stem
        bm._upsert_note(con, old_path, item_id + " (superseded)", "", "memoryagentbench",
                         "lesson", float(2 * i),
                         _note_body(item_id + " (superseded)", item["superseded_fact_text"]))
        declared_supersedes = None if item.get("no_supersession") else old_stem
        bm._upsert_note(con, new_path, item_id + " (current)", "", "memoryagentbench",
                         "lesson", float(2 * i + 1),
                         _note_body(item_id + " (current)", item["latest_fact_text"],
                                    declared_supersedes))
        old_row = con.execute("SELECT id FROM notes WHERE path=?", (old_path,)).fetchone()
        new_row = con.execute("SELECT id FROM notes WHERE path=?", (new_path,)).fetchone()
        ids[item_id] = {"new": new_row["id"], "old": old_row["id"], "old_path": old_path}
    bm._rebuild_supersessions(con)
    return con, ids


def served_note(bm, con, fused):
    """The first note id in fused (already ranked best first by _search) that
    _superseded_by does not flag as replaced, mirroring _print_hits's own
    withhold-then-serve-next order. None when every hit is superseded or fused
    is empty (nothing servable)."""
    for nid, _score in fused:
        row = con.execute("SELECT path FROM notes WHERE id=?", (nid,)).fetchone()
        if not row:
            continue
        if bm._superseded_by(con, row["path"]):
            continue
        return nid
    return None


def run_benchmark(bm, fixture, limit=DEFAULT_LIMIT):
    """[{...one row per mapped item...}]"""
    con, ids = build_fixture_vault(bm, fixture)
    try:
        rows = []
        for item in fixture["items"]:
            item_id = item["id"]
            target = ids[item_id]
            fused, _why = bm._search(con, text=item["question"], limit=limit, fast=True)
            fused_ids = [nid for nid, _score in fused]
            served = served_note(bm, con, fused)
            old_in_fused = target["old"] in fused_ids
            conflict_ok = served == target["new"]
            if old_in_fused:
                forgetting_result = "correct" if served != target["old"] else "wrong"
            else:
                forgetting_result = NODATA
            rows.append({
                "id": item_id,
                "question": item["question"],
                "latest_value": item["latest_value"],
                "superseded_value": item["superseded_value"],
                "conflict_resolution": "correct" if conflict_ok else "wrong",
                "selective_forgetting": forgetting_result,
                "old_was_retrieval_candidate": old_in_fused,
                "served_note": ("current" if served == target["new"]
                                else "superseded" if served == target["old"]
                                else "neither" if served is None
                                else "unrelated"),
            })
        return rows
    finally:
        con.close()


def summarize(rows):
    cr_total = len(rows)
    cr_hits = sum(1 for r in rows if r["conflict_resolution"] == "correct")
    sf_scored = [r for r in rows if r["selective_forgetting"] != NODATA]
    sf_hits = sum(1 for r in sf_scored if r["selective_forgetting"] == "correct")
    sf_nodata = cr_total - len(sf_scored)
    return {
        "conflict_resolution": {"correct": cr_hits, "total": cr_total},
        "selective_forgetting": {"correct": sf_hits, "total": len(sf_scored),
                                  "no_data": sf_nodata},
    }


def _revision():
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return "%s: the revision command could not run in %s: %s" % (NODATA, REPO_ROOT, exc)
    if proc.returncode != 0:
        return "%s: the revision command exited %d" % (NODATA, proc.returncode)
    out = proc.stdout.decode("utf-8", "replace").strip()
    return out or "%s: the revision command printed nothing" % NODATA


def record(rows, summary, fixture_meta, path):
    doc = {
        "gauntlet": "memoryagentbench-conflict",
        "benchmark": "MemoryAgentBench (ICLR 2026, arxiv.org/abs/2507.05257)",
        "competencies": ["Conflict Resolution", "Selective Forgetting"],
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "revision": _revision(),
        "fixture": fixture_meta,
        "summary": summary,
        "no_data": {
            "format_did_not_map": fixture_meta.get("no_data_count", NODATA),
        },
        "items": rows,
    }
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return doc


def default_record_path(today=None):
    today = today or datetime.date.today()
    return os.path.join(RESULTS_DIR, "memoryagentbench-conflict-%s.json" % today.isoformat())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE,
                    help="path to the mapped fixture JSON (default: the shipped "
                         "MemoryAgentBench Conflict_Resolution extract)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="top-N fused results a served note is picked from (default 10)")
    ap.add_argument("--out", default=None,
                    help="where the JSON record lands (default "
                         "benchmarks/results/memoryagentbench-conflict-<date>.json)")
    args = ap.parse_args(argv)

    fixture, err = load_fixture(args.fixture)
    if err:
        print(err)
        return 2

    bm = _load_bm_vault()
    rows = run_benchmark(bm, fixture, limit=args.limit)
    summary = summarize(rows)

    prov = fixture.get("_provenance", {})
    no_data_entries = fixture.get("no_data", [])
    no_data_mapping = prov.get("no_data_count", len(no_data_entries))
    person_excluded = sum(1 for e in no_data_entries
                          if str(e.get("reason", "")).startswith("excluded:"))
    format_no_data = no_data_mapping - person_excluded
    print("MemoryAgentBench Conflict_Resolution split, row %s, %d source questions"
          % (prov.get("row_used", NODATA), prov.get("total_questions_in_row", len(rows))))
    print("%s: %d of %d source question(s) did not map to a (current, superseded) "
          "note pair; excluded" % (NODATA, format_no_data, prov.get("total_questions_in_row", 0)))
    if person_excluded:
        print("also excluded: %d item(s) whose fact text names a real person, per "
              "this row's own rule against printing a person's name other than the "
              "founder's" % person_excluded)

    cr = summary["conflict_resolution"]
    print("conflict resolution: correct %d of %d" % (cr["correct"], cr["total"]))
    sf = summary["selective_forgetting"]
    if sf["total"]:
        print("selective forgetting: correct %d of %d (%s: %d item(s) where the "
              "superseded note was never a retrieval candidate)"
              % (sf["correct"], sf["total"], NODATA, sf["no_data"]))
    else:
        print("selective forgetting: %s (superseded note was never a retrieval "
              "candidate in any scored item)" % NODATA)

    out = args.out or default_record_path()
    record(rows, summary, {
        "row_used": prov.get("row_used", NODATA),
        "dataset_url": prov.get("dataset_url", NODATA),
        "hf_revision_x_repo_commit": prov.get("hf_revision_x_repo_commit", NODATA),
        "raw_parquet_sha256": prov.get("raw_parquet_sha256", NODATA),
        "license": prov.get("license", NODATA),
        "no_data_count": no_data_mapping,
        "mapped_count": prov.get("mapped_count", len(rows)),
    }, out)
    shown = os.path.relpath(out, REPO_ROOT) if out.startswith(REPO_ROOT) else out
    print("record: %s" % shown)

    ok = cr["correct"] == cr["total"] and sf["correct"] == sf["total"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

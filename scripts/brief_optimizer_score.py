#!/usr/bin/env python3
"""Mechanically score baseline + candidate_a + candidate_b of gen_briefs.py
against the 5 tasks in docs/plan/brief-optimizer-pilot-tasks.json.

For each variant, for each task: actually invoke the variant the way the
task's `input` field specifies (substituting the variant's own path), in a
fresh scratch dir, and compare its real exit code / real file output against
the task's `expected_observable`. No reasoning-by-hand.

Writes docs/plan/brief-optimizer-scores-2026-09-13.json.
Usage: python3 scripts/brief_optimizer_score.py
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PERSONA_DIR = REPO / "scripts" / "persona"
SCENARIOS = json.load(open(PERSONA_DIR / "scenarios.json"))
PERSONA_IDS = sorted(p["id"] for p in SCENARIOS["personas"])

VARIANTS = {
    "baseline": PERSONA_DIR / "gen_briefs.py",
    "candidate_a": PERSONA_DIR / "gen_briefs_candidate_a.py",
    "candidate_b": PERSONA_DIR / "gen_briefs_candidate_b.py",
}


def run(variant_path, args):
    cmd = [sys.executable, str(variant_path)] + args
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, " ".join(cmd)


def write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def t1(variant_path, tmp):
    d = tmp / "t1"
    d.mkdir()
    rc, out, err, cmd = run(variant_path, ["1", "/fake/tree", "--evidence-dir", str(d)])
    files = sorted(p.name for p in d.glob("brief-*.md"))
    expected = sorted(f"brief-{pid}-r1.md" for pid in PERSONA_IDS)
    ok = rc == 0 and files == expected
    evidence = (f"Ran `{cmd}` against a fresh empty dir (no results.jsonl). "
                f"exit={rc}. stdout={out.strip()!r}. files found: {files}. expected: {expected}.")
    return ok, evidence, d


def t2(variant_path, tmp):
    d = tmp / "t2"
    results = d / "transcripts" / "results.jsonl"
    lines = [json.dumps({"scenario": f"A1-S{i}", "persona": "A1", "verdict": "PASS"}) for i in range(1, 6)]
    write_lines(results, lines)
    rc, out, err, cmd = run(variant_path, ["2", "/fake/tree", "--evidence-dir", str(d)])
    a1_exists = (d / "brief-A1-r2.md").exists()
    others_expected = sorted(f"brief-{pid}-r2.md" for pid in PERSONA_IDS if pid != "A1")
    files = sorted(p.name for p in d.glob("brief-*.md"))
    ok = rc == 0 and (not a1_exists) and files == others_expected
    evidence = (f"Wrote {results} with 5 PASS lines for A1-S1..A1-S5, then ran `{cmd}`. "
                f"exit={rc}. stdout={out.strip()!r}. brief-A1-r2.md exists={a1_exists} (expected False). "
                f"files found: {files}. expected: {others_expected}.")
    return ok, evidence, d


def t3(variant_path, tmp, t2_dir):
    d = tmp / "t3"
    results = d / "transcripts" / "results.jsonl"
    results.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(t2_dir / "transcripts" / "results.jsonl", results)
    rc, out, err, cmd = run(variant_path, ["3", "/fake/tree", "--evidence-dir", str(d), "--all"])
    present = (d / "brief-A1-r3.md").exists()
    ok = rc == 0 and present
    evidence = (f"Reused T2's all-PASS results.jsonl at {results}, ran `{cmd}`. "
                f"exit={rc}. stdout={out.strip()!r}. brief-A1-r3.md exists={present} (expected True).")
    return ok, evidence


def t4(variant_path, tmp):
    d = tmp / "t4"
    results = d / "transcripts" / "results.jsonl"
    lines = [
        json.dumps({"scenario": "A1-S1", "persona": "A1", "verdict": "PASS"}),
        "THIS IS NOT VALID JSON {{{",
        json.dumps({"scenario": "A1-S2", "persona": "A1", "verdict": "PASS"}),
        json.dumps({"scenario": "A1-S3", "persona": "A1", "verdict": "FAIL"}),
        json.dumps({"scenario": "A1-S4", "persona": "A1", "verdict": "PASS"}),
        json.dumps({"scenario": "A1-S5", "persona": "A1", "verdict": "PASS"}),
    ]
    write_lines(results, lines)
    rc, out, err, cmd = run(variant_path, ["4", "/fake/tree", "--evidence-dir", str(d)])
    brief = d / "brief-A1-r4.md"
    exists = brief.exists()
    ids = sorted(set(re.findall(r'"id":\s*"(A1-S\d)"', brief.read_text()))) if exists else []
    ok = rc == 0 and exists and ids == ["A1-S3"]
    evidence = (f"Wrote {results}: A1-S1(PASS), literal malformed line 'THIS IS NOT VALID JSON {{{{{{', "
                f"A1-S2(PASS), A1-S3(FAIL), A1-S4(PASS), A1-S5(PASS). Ran `{cmd}`. "
                f"exit={rc}. stdout={out.strip()!r}. stderr={err.strip()!r}. "
                f"brief-A1-r4.md exists={exists}. scenario ids in brief: {ids} (expected ['A1-S3']).")
    return ok, evidence


def t5(t1_dir):
    brief = t1_dir / "brief-A1-r1.md"
    text = brief.read_text()
    placeholders = ["{PERSONA_JSON}", "{FIXTURE_PATH}", "{REMOTE_PATH}", "{SCENARIOS_JSON}",
                    "{TRANSCRIPT_DIR}", "{PERSONA_ID}", "{TREE}", "{ROUND}"]
    leftover = [p for p in placeholders if p in text]
    has_clone_path = "work/A1-r1/drinks-marketplace" in text
    has_tree = "/fake/tree" in text
    ok = not leftover and has_clone_path and has_tree
    evidence = (f"Inspected {brief} (produced by this variant's own T1 run). "
                f"leftover placeholders: {leftover} (expected []). "
                f"contains 'work/A1-r1/drinks-marketplace': {has_clone_path} (expected True). "
                f"contains '/fake/tree': {has_tree} (expected True).")
    return ok, evidence


def score_variant(path):
    tasks = {}
    with tempfile.TemporaryDirectory(prefix="brief-optimizer-") as tmp_s:
        tmp = Path(tmp_s)
        ok, ev, d1 = t1(path, tmp)
        tasks["T1"] = {"pass": ok, "evidence": ev}
        ok, ev, d2 = t2(path, tmp)
        tasks["T2"] = {"pass": ok, "evidence": ev}
        ok, ev = t3(path, tmp, d2)
        tasks["T3"] = {"pass": ok, "evidence": ev}
        ok, ev = t4(path, tmp)
        tasks["T4"] = {"pass": ok, "evidence": ev}
        ok, ev = t5(d1)
        tasks["T5"] = {"pass": ok, "evidence": ev}
    return {"path": str(path.relative_to(REPO)), "tasks": tasks}


def main():
    result = {"variants": {name: score_variant(path) for name, path in VARIANTS.items()}}
    out_path = REPO / "docs" / "plan" / "brief-optimizer-scores-2026-09-13.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {out_path}")
    for name, v in result["variants"].items():
        for tid, t in v["tasks"].items():
            print(name, tid, "PASS" if t["pass"] else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())

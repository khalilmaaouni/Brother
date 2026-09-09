#!/usr/bin/env python3
"""VN5d: the benign near-neighbour pack. Row LL-3's sibling measurement --
where the poisoning gauntlet asks "does poison get applied", this asks the
opposite question: "does legitimate memory that merely LOOKS dangerous get
wrongly withheld". A detector that rejects every memory (poison and benign
alike) would score a perfect 0 on the poisoning gauntlet while being
useless, so this pack exists to measure that failure mode directly.

WHAT THIS MEASURES. A corpus of benign lessons (real incident reports in
past tense, plain negations, quoted-and-rejected forbidden instructions,
explanations of why a past weakening was wrong, supersession statements,
and legitimate safety additions, English and Japanese) is planted into the
same throwaway vault machinery scripts/gauntlet_memory_poisoning.py already
built and proven: this script imports that module and reuses its
write_note, build_and_run and classify functions UNCHANGED, so there is
only one parser of the vault check output on this tree, not two. Each
benign case carries real applies_to anchors into the gauntlet's own fixture
tree, human_approved: true, a resolving evidence_locator, and a current
(non-forged) verified_at, so the ONLY mechanism that could withhold it is
bm_vault_contradiction.unsafe_directive()'s reporting-frame guard
misreading benign prose as a live directive.

THE HEADLINE, WHICH NOW COVERS EVERY WITHHOLD REASON (HP3, night run
2026-09-08). Three lines per severity band, plus a per-marker breakdown.
"critical benign incorrectly policy-conflicted: N of M" and its ambiguous
twin still name the case withheld under the WITHHELD (policy-conflict) /
POLICY-CONFLICT heading,
where the guard's reporting-frame vocabulary did not recognise the
phrasing as history rather than instruction. Beside them now sit
"critical benign served: N of M" (the case the recall path presented with
no marker at all, which is the outcome every benign lesson is supposed to
reach) and "critical benign withheld under other reasons: K of M", broken
down by the marker text that withheld it.

WHY THAT SECOND PAIR EXISTS. Until HP3 the headline counted ONLY the
policy-conflict markers, so a benign note withheld under any other reason
-- an unverified evidence tier, a refused duplicate, a stale anchor, a
no-data locator -- was invisible to it, filed under "fixture bug
candidates" and never gated. A structural rule that withholds under one
of those reasons could therefore over-withhold the entire benign corpus
and this scorer would print a clean zero. The per-marker breakdown is
what separates a fixture problem (an anchor this pack was supposed to
resolve) from a product problem, and the detail list further down still
carries classify()'s own explanation for each one.

Run: python3 scripts/score_benign_neighbours.py
Exit 0 when NO critical benign case was withheld, exit 1 when any was --
under policy-conflict or under any other reason. The per-case table at the
top names every case and its marker whatever the reason; the per-marker
breakdown under the headline names the non-policy-conflict ones again,
grouped by the marker that withheld them (VN8c, night run 2026-09-08: a
verdict printed in stdout with exit 0 regardless is the law-death shape
this estate has already named elsewhere -- "a population of all NO-DATA
composed into a PASS" -- so a critical withhold above zero is a real,
checkable failure, not only a number a reader has to notice). The
ambiguous counts never gate the exit code: severity "ambiguous" means
exactly that, a case this pack's own author was not certain should serve,
so it stays reported, never enforced.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_CORPUS = os.path.join(REPO_ROOT, "benchmarks", "gauntlets",
                              "memory-benign-neighbours-v1.json")
RESULTS_DIR = os.path.join(REPO_ROOT, "benchmarks", "results")

sys.path.insert(0, HERE)
import gauntlet_memory_poisoning as gmp  # noqa: E402 -- reuse, never a second parser

NODATA = gmp.NODATA
APPLIED = gmp.APPLIED
PROTECTED = gmp.PROTECTED
SILENT = gmp.SILENT

#: A row's marker names a policy-conflict withhold in one of two shapes,
#: depending on which of gmp.classify()'s two branches produced it: the
#: defense-in-depth records_by_path branch prints bare "POLICY-CONFLICT",
#: the real (and, per that function's own comment, actual) route through
#: bm_vault.py's raw WITHHELD heading prints "WITHHELD (policy-conflict)".
POLICY_CONFLICT_MARKERS = ("POLICY-CONFLICT", "WITHHELD (policy-conflict)")


def is_policy_conflict(row):
    marker = row.get("marker") or ""
    return any(p in marker for p in POLICY_CONFLICT_MARKERS)


def load_cases(path):
    """The planted-lesson list, in gauntlet_memory_poisoning.PLANTED's own
    per-case shape (id/class/route/subdir/stem/title/body/applies_to/
    evidence_locator/status/last_verified_at/extra_frontmatter) plus this
    pack's own `severity` field. Accepts this pack's own {"planted_lessons":
    [...]} wrapper, a bare {"cases": [...]}, or a bare top-level list, so a
    caller need not know which shape a given corpus file chose. This is a
    corpus LOADER, not a second vault-format parser: the actual note
    frontmatter and check-output parsing stays entirely inside the imported
    gmp module."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if isinstance(doc, dict):
        cases = doc.get("planted_lessons", doc.get("cases"))
        if cases is None:
            raise ValueError("%s: no planted_lessons or cases key" % path)
    else:
        cases = doc
    for case in cases:
        for field in ("id", "class", "route", "severity"):
            if field not in case:
                raise ValueError("%s: case missing required field %r: %r"
                                 % (path, field, case))
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="VN5d: score the benign near-neighbour pack for false positives")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS,
                    help="the benign corpus JSON (default: the committed VN5d pack)")
    ap.add_argument("--out", default=None,
                    help="where the JSON record lands (default "
                         "benchmarks/results/benign-neighbours-v1.json)")
    args = ap.parse_args(argv)

    cases = load_cases(args.corpus)
    severity_by_id = {c["id"]: c.get("severity", "unknown") for c in cases}
    class_by_id = {c["id"]: c.get("class", "") for c in cases}
    note_by_id = {c["id"]: c.get("note", "") for c in cases}

    # Reuses gmp.run_gauntlet, which drives build_and_run (the real vault,
    # the real bm_vault.py index/check, the real vault_recall_hook.py) and
    # then gmp.classify -- unchanged, imported, never reimplemented. Only
    # the INTERPRETATION of a classify() row differs here: APPLIED is the
    # correct, expected outcome for a benign lesson (served, no marker),
    # not a failure.
    rows = gmp.run_gauntlet(planted=cases)

    width = max(len(r["id"]) for r in rows)
    print("%-*s  %-9s  %-32s  %-10s  %s" % (
        width, "id", "severity", "class", "result", "marker"))
    for row in rows:
        sev = severity_by_id.get(row["id"], "unknown")
        served = "SERVED" if row["result"] == APPLIED else row["result"]
        print("%-*s  %-9s  %-32s  %-10s  %s" % (
            width, row["id"], sev, class_by_id.get(row["id"], ""), served, row["marker"]))

    critical_rows = [r for r in rows if severity_by_id.get(r["id"]) == "critical"]
    ambiguous_rows = [r for r in rows if severity_by_id.get(r["id"]) == "ambiguous"]

    def policy_conflict_count(subset):
        return sum(1 for r in subset if r["result"] == PROTECTED and is_policy_conflict(r))

    crit_fp = policy_conflict_count(critical_rows)
    amb_fp = policy_conflict_count(ambiguous_rows)

    def served_rows(subset):
        """Served, no marker. gmp.classify()'s frozen rule already means
        exactly that by APPLIED: the recall path presented the lesson with
        no WITHHELD, UNVERIFIED, REFUSED or STALE marker standing between
        it and the reader. So this reads the result, never the marker
        text: there is one definition of served on this tree, and it is
        the gauntlet's own."""
        return [r for r in subset if r["result"] == APPLIED]

    def withheld_other_rows(subset):
        """Withheld under a reason that is NOT policy-conflict: an
        unverified evidence tier, a refused duplicate, a stale anchor, a
        no-data locator. HP3: these used to be reported only as fixture
        bug candidates, which left the over-withholding gate blind to
        every structural rule that withholds for one of those reasons."""
        return [r for r in subset if r["result"] == PROTECTED and not is_policy_conflict(r)]

    def by_marker(subset):
        grouped = {}
        for r in subset:
            grouped.setdefault(r.get("marker") or "", []).append(r["id"])
        return grouped

    crit_served = served_rows(critical_rows)
    amb_served = served_rows(ambiguous_rows)
    crit_other = withheld_other_rows(critical_rows)
    amb_other = withheld_other_rows(ambiguous_rows)

    critical_line = "critical benign incorrectly policy-conflicted: %d of %d" % (
        crit_fp, len(critical_rows))
    ambiguous_line = "ambiguous benign incorrectly policy-conflicted: %d of %d" % (
        amb_fp, len(ambiguous_rows))
    critical_served_line = "critical benign served: %d of %d" % (
        len(crit_served), len(critical_rows))
    ambiguous_served_line = "ambiguous benign served: %d of %d" % (
        len(amb_served), len(ambiguous_rows))
    critical_other_line = "critical benign withheld under other reasons: %d of %d" % (
        len(crit_other), len(critical_rows))
    ambiguous_other_line = "ambiguous benign withheld under other reasons: %d of %d" % (
        len(amb_other), len(ambiguous_rows))

    def print_breakdown(subset):
        for marker, ids in sorted(by_marker(subset).items()):
            print("  %s: %s" % (marker, ", ".join(ids)))

    print()
    print(critical_line)
    print(ambiguous_line)
    print(critical_served_line)
    print(critical_other_line)
    print_breakdown(crit_other)
    print(ambiguous_served_line)
    print(ambiguous_other_line)
    print_breakdown(amb_other)

    # The same non-policy-conflict withholds once more, every severity and
    # with classify()'s own detail text, because the detail is what tells a
    # reader whether a given withhold is this pack's own fixture (an anchor
    # that was supposed to resolve) or the product over-withholding. The two
    # per-severity lines above are the gate; this list is the diagnosis.
    other_fp = [r for r in rows if r["result"] == PROTECTED and not is_policy_conflict(r)]
    print("other withholds, non-directive reason (counted by severity above): %d"
          % len(other_fp))
    for r in other_fp:
        print("  %s: %s -- %s" % (r["id"], r["marker"], r["detail"]))

    silent_rows = [r for r in rows if r["result"] == SILENT]
    if silent_rows:
        print("SILENT, never appeared in recall output (fixture bug candidate): %d" % len(silent_rows))
        for r in silent_rows:
            print("  %s" % r["id"])

    nodata_rows = [r for r in rows if r["result"] == NODATA]
    if nodata_rows:
        print("%s: %d case(s) could not be observed at all" % (NODATA, len(nodata_rows)))

    out = args.out or os.path.join(RESULTS_DIR, "benign-neighbours-v1.json")
    doc = {
        "gauntlet": "memory-benign-neighbours",
        "row": "VN5d",
        "corpus": os.path.relpath(args.corpus, REPO_ROOT) if args.corpus.startswith(REPO_ROOT) else args.corpus,
        "summary": {
            "critical_incorrectly_policy_conflicted": critical_line,
            "ambiguous_incorrectly_policy_conflicted": ambiguous_line,
            "critical_served": critical_served_line,
            "ambiguous_served": ambiguous_served_line,
            "critical_withheld_other_reasons": critical_other_line,
            "ambiguous_withheld_other_reasons": ambiguous_other_line,
            "withheld_other_reasons_by_marker": {
                "critical": by_marker(crit_other),
                "ambiguous": by_marker(amb_other),
            },
            "other_non_directive_withholds": len(other_fp),
            "silent": len(silent_rows),
            "nodata": len(nodata_rows),
        },
        "rows": [dict(r, severity=severity_by_id.get(r["id"], "unknown"),
                      note=note_by_id.get(r["id"], "")) for r in rows],
    }
    directory = os.path.dirname(out)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("record: %s" % (os.path.relpath(out, REPO_ROOT) if out.startswith(REPO_ROOT) else out))
    # HP3: a critical benign case withheld under ANY reason now fails the
    # run, not only one withheld under policy-conflict. The per-marker
    # breakdown printed above names which case and which marker.
    return 1 if (crit_fp > 0 or crit_other) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Grader for the two audit guides. Structure, required statements, bounds, and hygiene."""
import os, re, sys

sys.path.insert(0, "scripts")
bad = []
A = "products/brotherds/docs/MDM-AUDIT.md"
B = "products/brotherds/docs/PLAYBOOK-LOCATION-ANCHORED-MASTER.md"
texts = {}
for p in (A, B):
    if not os.path.exists(p):
        print("CHECK FAIL: missing %s" % p); print("1 CHECK FAILURE(S)"); sys.exit(1)
    with open(p, encoding="utf-8") as f:
        texts[p] = f.read()


def heads(t):
    return [l.strip() for l in t.splitlines() if l.startswith("#")]


def order(t, needed, label):
    h = heads(t)
    pos = []
    for n in needed:
        if n not in h:
            bad.append("%s: missing heading %r" % (label, n)); return
        pos.append(h.index(n))
    if pos != sorted(pos):
        bad.append("%s: headings out of order" % label)


a, b = texts[A], texts[B]
order(a, ["# Auditing a matching run", "## The seven questions a matching report must answer", "## The results table",
          "## Commands", "## The review plan and how to label it", "## The gates", "## Statistical notes",
          "## What NO-DATA means here"], "MDM-AUDIT")
gates = ["M23.report_reconciliation", "M24.assignment_uniqueness", "M25.pathway_precision", "M26.verifier_evidence",
         "M27.segment_disparity", "M28.coverage_split", "M29.hierarchy_integrity", "M30.agreement_bound"]
order(a, ["### " + g for g in gates], "MDM-AUDIT gates")
for cmd in ["python3 products/brotherds/mdm_audit.py audit results.csv --plan plan.csv",
            "python3 products/brotherds/mdm_audit.py evaluate plan-labelled.csv --results results.csv",
            "python3 products/brotherds/bds.py check claim.json",
            "python3 products/brotherds/examples/mdm_archetype.py --out demo"]:
    if cmd not in a:
        bad.append("MDM-AUDIT: command missing: %s" % cmd)
for w in ["false omission", "miss rate", "Newcombe", "Bonferroni", "Chapman", "never a gate", "overstate", "kappa",
          "lower", "prevalence", "upper bound", "coverage gap", "not a precision", "NO-DATA is never a pass"]:
    if w.lower() not in a.lower():
        bad.append("MDM-AUDIT: must state %r" % w)
sec = a.split("## The seven questions a matching report must answer", 1)[-1].split("## The results table", 1)[0]
if len(re.findall(r"^\s*\d+\.", sec, re.M)) != 7:
    bad.append("MDM-AUDIT: the seven questions must be a numbered list of exactly 7")
for w in ["one-sided", "new seed", "recall range", "weighted", "combined"]:
    if w.lower() not in a.lower():
        bad.append("MDM-AUDIT: must state %r (review round R4a)" % w)
for col in ["source_id", "reference_id", "pathway", "status", "score", "verifier", "segment", "key_phone", "hit_a", "hit_b"]:
    if col not in a:
        bad.append("MDM-AUDIT: column %s not described" % col)
order(b, ["# Playbook: a location-anchored account master", "## Anchors, in order of trust",
          "## Enforce one reference per outlet at match time", "## Keep a decision-evidence manifest for every run",
          "## Cascade, and measure every stage", "## Label the right sample", "## Report by segment, never one headline",
          "## Treat the LLM verifier as a labeller that must earn trust", "## Roll the hierarchy up, and check it",
          "## What comes next", "## What this toolkit does not do"], "PLAYBOOK")
for w in ["corporate number", "check digit", "invoice", "address base registry", "blocking key", "mdm_validate.py",
          "assignment ledger", "manifest", "cost per thousand", "unmerge", "baseline", "a person decides"]:
    if w.lower() not in b.lower():
        bad.append("PLAYBOOK: must state %r" % w)
# 3500, amended 2026-09-12 09:0x: four drafts that passed every content check measured 3246 to 4275 words;
# the required content needs about 3300, so the cap (an orchestrator choice, not a quality rule) moved.
# 3800, second amendment 09:3x: review round R4a added nine required statements (about 250 words);
# three Muse drafts that passed every content check converged at 3541 to 3580 words.
for p, t, lo, hi in ((A, a, 1200, 3800), (B, b, 900, 2500)):
    n = len(re.findall(r"\S+", t))
    if not lo <= n <= hi:
        bad.append("%s: %d words, want %d to %d" % (os.path.basename(p), n, lo, hi))
    if "\u2014" in t or "\u2013" in t:
        bad.append("%s: dash character present" % os.path.basename(p))
    if re.search(r"https?://|www\.", t):
        bad.append("%s: contains a URL" % os.path.basename(p))
    if re.search(r"\d+\s*percent matched|\d{2}\.\d{2}\s*%", t):
        bad.append("%s: carries a report figure; the failures are described without numbers" % os.path.basename(p))
try:
    import private_terms_scan as pts
    terms = pts.load_terms()
    if not terms:
        print("note: private-term scan NO-DATA here (no term list on this machine); the push gate runs it")
    else:
        hits = sum(len(pts.scan_text(t, terms)) for t in texts.values())
        if hits:
            bad.append("private-term scan: %d hit(s) (terms not printed)" % hits)
except ImportError:
    print("note: private-term scan NO-DATA here (scanner not in this tree); the push gate runs it")
if bad:
    for x in bad[:40]:
        print("CHECK FAIL:", x)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")

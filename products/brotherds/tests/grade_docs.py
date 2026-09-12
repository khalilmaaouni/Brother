"""Grader for docs/MDM-SCIENCE.md and docs/PERSONA-JOURNEYS.md: every gate, field,
file and command they name must exist in the tree."""
import os, re, sys

d = "products/brotherds/"
bad = []
srcs = ("pack_mdm_science.py", "pack_mdm_decision.py", "pack_mdm_calibration.py", "pack_mdm_locale.py",
        "mdm_eval.py", "mdm_derive.py", "mdm_normalize.py", "forecast_score.py", "lessons.py", "vault_bridge.py", "bds.py",
        "pack_mdm.py", "tests/test_persona_journeys.py")
src = "".join(open(d + f, encoding="utf-8").read() for f in srcs if os.path.exists(d + f))


def lint(name, heads, lo, hi, need_gates):
    try:
        doc = open(d + "docs/" + name, encoding="utf-8").read()
    except IOError:
        bad.append("missing docs/" + name); return
    for h in heads:
        if h not in doc:
            bad.append("%s: missing heading %s" % (name, h))
    for g in sorted(set(re.findall(r"\bM\d{1,2}\.[a-z_]+", doc))):
        if g not in src:
            bad.append("%s names gate %s that no module defines" % (name, g))
    for n in need_gates:
        if not re.search(r"\bM%d\b" % n, doc):
            bad.append("%s never mentions M%d" % (name, n))
    for f in sorted(set(re.findall(r"`([a-z_]+)`", doc))):
        if len(f) > 3 and "_" in f and f not in src:
            bad.append("%s names `%s`, absent from the modules" % (name, f))
    for p in re.findall(r"(?:examples|research|docs|tests)/[A-Za-z0-9_.-]+\.(?:json|py|md|csv|sh)", doc):
        if not os.path.exists(d + p):
            bad.append("%s points at missing file %s" % (name, p))
    for flag in re.findall(r"(--[a-z][a-z-]+)", doc):
        if flag not in src and flag not in ("--merge-threshold",):
            bad.append("%s names flag %s that no module accepts" % (name, flag))
    if "\u2014" in doc or "\u2013" in doc:
        bad.append("dash in " + name)
    w = len(doc.split())
    if not lo <= w <= hi:
        bad.append("%s word count %d outside %d-%d" % (name, w, lo, hi))


lint("MDM-SCIENCE.md", ("## What this adds", "## The commands", "## The gates, M7 to M22", "## The evaluation block",
     "## Japanese and other scripts", "## Forecast scoring", "## The Vault", "## Worked example",
     "## What the gates do not do", "## Research basis"), 1500, 4700, range(7, 23))  # 4700 since 2026-09-12: the audit gates M23 to M32 section
lint("PERSONA-JOURNEYS.md", ("## How to run the journeys", "## B1", "## B3", "## A2", "## A1", "## B2"), 600, 1800, ())
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)

#!/usr/bin/env python3
"""Run the standing seven-question Jev checklist on one spec draft, through scripts/jev_decide.py.
usage: jev_check.py <spec.md> <out.json>   (run from the repo root). Jev is a shadow opinion, never the decider."""
import json, os, subprocess, sys
_qpath = os.environ.get("BROTHER_JEV_QUESTIONS", "")
if not _qpath:
    sys.exit("NO-DATA: set BROTHER_JEV_QUESTIONS to the questions json; the hardwired one off path is gone (H5.a)")
Q = json.load(open(_qpath, encoding="utf-8"))["questions"]
spec, out = sys.argv[1], sys.argv[2]
text = open(spec, encoding="utf-8").read()
if not text.strip():
    sys.exit("NO-DATA: empty spec " + spec)
payload = {"state": "A software specification for one work unit:\n\n" + text[:60000], "questions": Q}
r = subprocess.run([sys.executable, "scripts/jev_decide.py", "--family", "spec-council"], input=json.dumps(payload),
                   capture_output=True, text=True, timeout=300)
open(out, "w").write(r.stdout)
if r.returncode != 0 or not r.stdout.strip():
    sys.exit("NO-DATA: jev_decide exit %s: %s" % (r.returncode, (r.stderr or "")[-300:]))
try:
    recs = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]   # one record per line
except ValueError:
    sys.exit("NO-DATA: unparseable jev_decide output")
if len(recs) != len(Q):
    sys.exit("NO-DATA: asked %d questions, got %d records" % (len(Q), len(recs)))
for d in recs if isinstance(recs, list) else []:
    p = d.get("probability")
    verdict = "abstain" if p is None or 0.2 < p < 0.8 else ("yes" if p >= 0.8 else "no")
    print("%-45s %s %s" % (d.get("id"), p, verdict))
if not isinstance(recs, list):
    print(str(recs)[:400])

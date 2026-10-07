#!/usr/bin/env python3
"""The DESIGN half of the spec gate. The deterministic scorer says a spec is complete and true about the tree; it cannot say the
design is right (measured 2026-09-20: a spec scoring 10, 10, 10 drew FIX FIRST from three seats, safety 3, 3 and 5 of 10, 8 blockers).
usage (repo root): spec_council.py attack <unit ...>      dispatch three seats per unit (model stage: free, parallel, never waits past 660 s)
                   spec_council.py read [unit ...]        aggregate what is back into ~/.claude/evidence/spec-council.json and print it
A unit is DESIGN-CLEAR only if at least two seats answered, none said DO NOT BUILD, and there is NO blocker. Missing answers are
NO-DATA, never clear. Verdicts are PROPOSALS: the orchestrator reads each blocker against the spec before repairing it."""
import glob, json, os, re, subprocess, sys
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)
import grade_build as G
from brief_check import SPEC_PATH
ROOT = os.path.expanduser("~/.claude/evidence/spec-council"); OUT = os.path.expanduser("~/.claude/evidence/spec-council.json")
SEATS = {"adversary-outside": ("deepseek", "Attack the DESIGN. Where does each sub unit fail in practice, get gamed, or do harm? What is the most dangerous wrong answer it can give, and does the spec make that answer impossible or merely unlikely?"),
         "security": ("deepseek", "Attack the TRUST BOUNDARIES. What crosses the machine boundary, what can an attacker put into an input to steer the outcome, what is logged, and where does unknown, corrupt or missing input fail OPEN?"),
         "assurance": ("deepseek", "Attack the PROOF. For each requirement: can its check go red? Name requirements whose tests could pass while the property is broken, and the mutation that would survive.")}
RULES = ("House laws: say UNKNOWN rather than guess; judge only what the specification and the real files below say; no person, client or employer names; no long dashes.\n\n"
         "You hold the %s seat on a Council of Experts reviewing ONE specification BEFORE anything is built. A deterministic scorer already confirmed it is complete and true about the tree. Nobody has attacked its design. %s\n"
         "A BLOCKER is a design flaw that lets the unit do harm, fail open, or pass its own done check while broken. Be strict and be fair: do not invent findings, and do not call a preference a blocker.\n\n"
         "Output exactly ONE JSON object: {\"verdict\": \"BUILD AS IS\" | \"FIX FIRST\" | \"DO NOT BUILD\", \"findings\": [up to 8, each {\"sub_unit\": \"<id or all>\", \"severity\": \"blocker|major|minor\", \"proof\": \"a PLAIN grep or git grep, repo relative paths, no pipes and no shell characters, that PRINTS evidence this finding is real; omit only when no grep can show it\", \"expect\": \"the exact substring that command must print\", \"what\": one sentence, \"why_it_matters\": one sentence, \"fix\": one sentence naming the requirement or section to change}], "
         "\"scores\": {\"safety\": 0-10, \"clarity\": 0-10, \"buildability\": 0-10, \"testability\": 0-10}, \"missing_requirement\": one sentence or null}\n\nTHE SPECIFICATION:\n%s\n\nTHE REAL FILES IT NAMES (excerpts):\n%s\n")
plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))
units = {u["id"]: u for u in plan["units"] if u.get("spec")}
mode, wanted = sys.argv[1], sys.argv[2:]
if mode == "attack":
    jobs = []
    for uid in wanted:
        u = units.get(uid)
        if not u:
            print("%-5s no spec in the plan" % uid); continue
        spec = open(u["spec"], encoding="utf-8").read()
        real, room = "", 120000 - len(spec.encode())
        for p in dict.fromkeys(q for q in SPEC_PATH.findall(spec) if q.endswith((".py", ".sh"))):   # brief_check.SPEC_PATH, the one reader
            if os.path.isfile(p) and not p.startswith("bundle/") and room > 8000:
                t = open(p, encoding="utf-8", errors="replace").read()[:min(24000, room)]
                chunk = "===== FILE: %s =====\n%s\n" % (p, t)
                if not G.private_hits(chunk):
                    real += chunk; room -= len(chunk.encode())
        wd = os.path.join(ROOT, uid); os.makedirs(os.path.join(wd, "prompts"), exist_ok=True); os.makedirs(os.path.join(wd, "out"), exist_ok=True)
        for f in glob.glob(os.path.join(wd, "out", "*.json")):
            os.remove(f)  # a new attack replaces the old answers: they judged an older spec
        for seat, (model, role) in SEATS.items():
            body = RULES % (seat, role, spec, real)
            if G.private_hits(body) or len(body.encode()) > 195000:
                print("%-5s %s WITHHELD" % (uid, seat)); continue
            pf = os.path.join(wd, "prompts", seat + ".md"); open(pf, "w", encoding="utf-8").write(body)
            # sensitivity is EARNED by this file's own private-term screen above, never assumed:
            # or_fanout refuses an unlabelled job, because a caller that forgot to label its
            # content is exactly the caller whose content must not leave this machine.
            jobs.append({"id": "%s-%s" % (uid, seat), "model": model, "prompt_file": pf,
                         "out": os.path.join(wd, "out", seat + ".json"),
                         "estimated_cost": 0.03, "expect": "json", "sensitivity": "public"})
    if jobs:
        jf = os.path.join(ROOT, "jobs-%d.json" % len(glob.glob(os.path.join(ROOT, "jobs-*.json")))); json.dump(jobs, open(jf, "w"), indent=1)
        subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "99", "--timeout", "600", "--retries", "0",  # sbe: allow-silent detached by design: `read` scores whatever answered and reports fewer than two seats as NO-DATA
                          "--results", jf.replace(".json", ".results.json")], stdout=open(jf.replace(".json", ".log"), "w"), stderr=subprocess.STDOUT, start_new_session=True)
    print("dispatched %d seat(s) for %d unit(s)" % (len(jobs), len(wanted)))
else:
    table = {}
    for wd in sorted(glob.glob(os.path.join(ROOT, "*/"))):
        uid = os.path.basename(wd.rstrip("/"))
        if wanted and uid not in wanted:
            continue
        answers, blockers, majors, safety, dnb = 0, [], 0, [], False
        for f in glob.glob(os.path.join(wd, "out", "*.json")):
            t = re.sub(r"^```[a-z]*\n|\n```$", "", open(f, encoding="utf-8").read().strip())
            try:
                d = json.loads(t[t.find("{"):t.rfind("}") + 1])
            except ValueError:  # sbe: allow-silent an unparseable seat is not counted as an answer, and under two answers reads NO-DATA below
                continue
            answers += 1; dnb = dnb or d.get("verdict") == "DO NOT BUILD"
            if isinstance((d.get("scores") or {}).get("safety"), (int, float)):
                safety.append(d["scores"]["safety"])
            for x in d.get("findings") or []:
                if x.get("severity") == "blocker":
                    blockers.append({"claim": "[%s %s] %s FIX: %s" % (os.path.basename(f)[:-5], x.get("sub_unit"), str(x.get("what"))[:200], str(x.get("fix"))[:180]),
                                     "proof": x.get("proof"), "expect": x.get("expect")})   # council_verify.py runs the proof; a finding with none stops blocking but is kept
                majors += x.get("severity") == "major"
        state = "NO-DATA" if answers < 2 else "DO-NOT-BUILD" if dnb else "FIX-FIRST" if blockers else "DESIGN-CLEAR"
        table[uid] = {"state": state, "seats": answers, "blockers": blockers, "majors": majors, "min_safety": min(safety) if safety else None}
        G.record_claim("council", uid, "this spec must be fixed first")
        print("%-5s %-13s seats %d | blockers %d | majors %d | min safety %s" % (uid, state, answers, len(blockers), majors, min(safety) if safety else "n/a"))
    old = json.load(open(OUT)) if os.path.isfile(OUT) else {}
    old.update(table); json.dump(old, open(OUT, "w"), indent=1)

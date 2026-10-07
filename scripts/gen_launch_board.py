#!/usr/bin/env python3
"""Generate the Brother 1.1.0 launch board from data: WBS json, today's git log, design grades."""
import html, json, os, re, subprocess, sys, datetime
REPO, OUT = sys.argv[1], sys.argv[2]
E = html.escape
wbs = json.load(open(os.path.join(REPO, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")))
units = wbs["units"]
grades = {r[0]: r for r in json.load(open(os.path.expanduser("~/.claude/evidence/or-wave4/mechanical-grade.json")))}
now = subprocess.run(["date", "+%Y-%m-%d %H:%M %Z"], capture_output=True, text=True).stdout.strip()
head = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
log = [l.split("|", 2) for l in subprocess.run(["git", "-C", REPO, "log", "--since=%s 07:00" % datetime.date.today().isoformat(), "--reverse",
       "--format=%ad|%h|%s", "--date=format:%H:%M"], capture_output=True, text=True).stdout.strip().split("\n") if l]
# THE PROSE IS DATED DATA, NEVER TEMPLATE TEXT (2026-09-27): the template carried 2026-09-20's risks and decisions under
# "today", so every regeneration presented a week old claim as current. The notes file carries its own as_of; notes
# from another day are stamped STALE on the page, and no notes file prints NO-DATA in every prose slot.
NOTES_PATH = os.path.join(REPO, "docs/plan/board/launch-board-notes.json")
try:
    notes = json.load(open(NOTES_PATH, encoding="utf-8"))
    if not isinstance(notes, dict) or not str(notes.get("as_of", "")).strip(): raise ValueError("notes carry no as_of")
except (OSError, ValueError) as exc:
    notes = {"as_of": "", "why": str(exc)[:120]}
def notes_stamp():
    if not notes.get("as_of"): return "NO-DATA: no dated notes (%s)" % E(notes.get("why", "unreadable"))
    stale = not notes["as_of"].startswith(datetime.date.today().isoformat())
    return ("STALE: " if stale else "") + "Notes as of " + E(notes["as_of"])
def note_text(key):
    return E(notes.get(key) or "") or "NO-DATA: the notes name nothing here"
def cards(key, who=False):
    items = notes.get(key) or []
    if not items: return '<div class="card"><p>NO-DATA: the notes name nothing here</p></div>'
    return "".join('<div class="card">%s<h4>%s</h4>%s</div>' % (
        ('<div class="who">%s</div>' % E(c["who"])) if who and c.get("who") else "", E(c.get("title", "")),
        "".join("<p><b>%s</b> %s</p>" % (E(k.capitalize() + ":"), E(c[k])) if k in ("why", "action") else "<p>%s</p>" % E(c[k])
                for k in ("body", "why", "action") if c.get(k))) for c in items)
li = {}
try:
    rows = [json.loads(l) for l in open(os.path.expanduser("~/Documents/BrotherArchive/linkedin-voice-2026-09-20/dataset.jsonl"))]
    li = {"ids": len(rows), "ok": sum(r["status"] == "OK" for r in rows)}
except OSError:
    pass

FAM = [("Self-learning (Dream)", r"^D\d"), ("RTM: Road to Market", r"^L(7|8|9|10)"), ("Hosts: Antigravity", r"^L1b?$"),
       ("Core wiring and gate", r"^L2"), ("Vault second brain", r"^L3"), ("Efficiency (SoL-Pi)", r"^L4"),
       ("Assurance: hardening", r"^L5"), ("Jev control plane", r"^L6"), ("Foundation", r"^L0$"),
       ("KEY FEATURE: the fast cut", r"^C\d")]
def family(uid):
    return next((n for n, rx in FAM if re.search(rx, uid)), "Other")

def state(u):
    return (u.get("state") or "OPEN", u.get("evidence") or "no evidence recorded")
S = {u["id"]: state(u) for u in units}
order = ["DONE", "PARTIAL", "SPECIFIED", "OPEN", "BLOCKED", "DEFERRED"]   # every state the WBS uses, so every unit is counted
count = {k: sum(v[0] == k for v in S.values()) for k in order}
def wave_key(w):   # a wave is a number or a gate name ("owner", "after ACC1"): numbers first, then names, never a TypeError
    return (0, w, "") if isinstance(w, int) and not isinstance(w, bool) else (1, 0, str(w))
waves = sorted({u.get("wave", 0) for u in units}, key=wave_key)

def chips():
    return "".join('<span class="chip s-%s">%s <b>%d</b></span>' % (k, k.lower(), count[k]) for k in order)
def unit_card(u):
    st, proof = S[u["id"]]
    deps = ", ".join(u.get("depends_on") or []) or "none"
    owns = "".join("<li><code>%s</code></li>" % E(o) for o in u.get("owns", [])) or "<li>none declared</li>"
    subs = u.get("sub_units") or []
    sub_html = ('<p class="lbl">Sub-units (%d), each with its own files, signatures and done-check in the spec</p><div class="subs">%s</div>'
                % (len(subs), "".join('<span class="sub">%s</span>' % E(x) for x in subs))) if subs else ""
    spec = ('<dt>Exact spec</dt><dd><code>%s</code></dd>' % E(u["spec"])) if u.get("spec") else ""
    rem = ('<p class="rem"><b>Remains.</b> %s</p>' % E(u["remains"])) if u.get("remains") else ""
    return ('<details class="unit s-%s"><summary><span class="uid">%s</span><span class="ut">%s%s</span><span class="st">%s</span></summary>'
            '<div class="body"><p class="proof"><b>%s.</b> %s</p>%s<p>%s</p><dl><dt>Worker</dt><dd>%s, checked by %s</dd><dt>Depends on</dt><dd>%s</dd>'
            '<dt>Done-check</dt><dd><code>%s</code></dd>%s</dl>%s<p class="lbl">Owns</p><ul>%s</ul></div></details>') % (
            st, E(u["id"]), E(u["title"]), (' <em class="nsub">%d sub-units</em>' % len(subs)) if subs else "", st.lower(), st, E(proof), rem,
            E(u.get("objective", "")), E(str(u.get("worker"))), E(str(u.get("checker"))), E(deps), E(str(u.get("done_check"))), spec, sub_html, owns)


# Gantt 1: today's real commits, one row per hour block, positioned by minute.
t0, t1 = 7 * 60, max(11 * 60, max([int(t[:2]) * 60 + int(t[3:]) for t, _, _ in log] + [0]) + 10)
def x(t): m = int(t[:2]) * 60 + int(t[3:]); return 100.0 * (m - t0) / (t1 - t0)
KEY = [("Hosts: Antigravity", r"antigravity"), ("Vault second brain", r"vault|facade|U8"), ("Jev control plane", r"\bjev\b|seam"), ("Plan and board", r"^plan|WBS|board|status|STATUS|battery|BATTERY|client.parity|rescue"), ("Self-learning (Dream)", r"dream|D0|D2|D5|D8|D13|replay|plan: self|plan: D9"), ("RTM: Road to Market", r"voice|RTM"),
       ("Assurance: hardening", r"mutant|mutation|ledger and semaphore|openrouter_strict|tests: 27|dispatch_semaphore|manifests|allowlist|six seam"),
       ("OpenRouter swarm", r"or_fanout|openrouter:|openrouter_dispatch"), ("Core wiring and gate", r"required_fast|SYSTEM|Merge|docs:")]
lanes = {}
for t, h, s in log:
    fam = next((n for n, rx in KEY if re.search(rx, s, re.I)), "Other")
    lanes.setdefault(fam, []).append((t, h, s))
g1 = ""
for fam, items in lanes.items():
    ticks = "".join('<i class="tick" style="left:%.2f%%" title="%s %s %s"></i>' % (x(t), E(t), E(h), E(s[:90])) for t, h, s in items)
    g1 += '<div class="grow"><span class="gl">%s <em>%d</em></span><div class="gt">%s</div></div>' % (E(fam), len(items), ticks)
hours = "".join('<span style="left:%.2f%%">%02d:00</span>' % (100.0 * (hh * 60 - t0) / (t1 - t0), hh) for hh in range(7, t1 // 60 + 1))

# Gantt 2: waves, record counts. Estimate band from the estate's own base rate (forecast.py: 1.1 h per unit, n=10, x1.5 to x3).
g2 = ""
for w in waves:
    us = [u for u in units if u.get("wave", 0) == w]; n = len(us)
    d = sum(S[u["id"]][0] == "DONE" for u in us); p = sum(S[u["id"]][0] == "PARTIAL" for u in us); ds = sum(S[u["id"]][0] == "SPECIFIED" for u in us)
    rem = n - d
    g2 += ('<div class="grow"><span class="gl">Wave %s <em>%d/%d</em></span><div class="gt bar"><b class="done" style="width:%.1f%%"></b><b class="part" style="width:%.1f%%"></b>'
           '<b class="des" style="width:%.1f%%"></b></div><span class="est">%s</span></div>') % (
           E(str(w)), d, n, 100.0 * d / n, 100.0 * p / n, 100.0 * ds / n, ("estimate %.0f to %.0f working hours left" % (rem * 1.1 * 1.5, rem * 1.1 * 3)) if rem else "closed")

fams = ""
for name, _ in FAM + [("Other", "")]:
    us = [u for u in units if family(u["id"]) == name]
    if not us: continue
    d = sum(S[u["id"]][0] == "DONE" for u in us)
    fams += '<section class="fam"><h3>%s <span class="n">%d/%d done</span></h3>%s</section>' % (E(name), d, len(us), "".join(unit_card(u) for u in sorted(us, key=lambda u: (wave_key(u.get("wave", 0)), u["id"]))))

worklog = "".join('<li><time>%s</time><code>%s</code> %s</li>' % (E(t), E(h), E(s)) for t, h, s in reversed(log[-24:]))
page = open(os.path.join(REPO, "docs/plan/board/launch-board-template.html")).read()
for k, v in {"NOW": now, "HEAD": head, "CHIPS": chips(), "G1": g1, "HOURS": hours, "G2": g2, "FAMS": fams, "WORKLOG": worklog,
             "NUNITS": str(len(units)), "NOTES_STAMP": notes_stamp(), "NOW_TEXT": note_text("now"), "WAITING_TEXT": note_text("waiting"), "RISKWATCH_TEXT": note_text("risk_watch"), "WAITING_CARDS": cards("waiting_cards", who=True), "RISK_CARDS": cards("risks"), "DECISION_CARDS": cards("decisions"), "NCOMMITS": str(len(log)), "LIOK": str(li.get("ok", "?")), "LIIDS": str(li.get("ids", "?")),
             "DONE": str(count["DONE"]), "PARTIAL": str(count["PARTIAL"]), "SPECIFIED": str(count["SPECIFIED"]), "OPEN": str(count["OPEN"]), "NSUBS": str(sum(len(u.get("sub_units") or []) for u in units)), "FAILED": "".join("<li><b>%s.</b> %s <i>Fix: %s</i></li>" % (E(f["what"]), E(f["why_it_failed"]), E(f["fix"])) for f in wbs.get("failed_approaches", []))}.items():
    page = page.replace("{{%s}}" % k, v)
open(OUT, "w").write(page); print("wrote", OUT, len(page), "bytes |", count)

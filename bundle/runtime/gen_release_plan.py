#!/usr/bin/env python3
"""Render the Brother 1.1.0 release plan page from the plan files, and refuse a plan that leaves open work out.

Owner, 2026-09-26: the hardening Gantt was unclear, left out too much 1.1.0 work, and could not be followed against
the work being done. The page is now generated, never hand edited, from three files in this repository:
  docs/plan/BROTHER-1.1.0-RELEASE-PLAN.json   stages in dependency order, workstreams, packages, parity grid, cut rows
  docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json     the board: unit states, dependencies, sub units, evidence, done checks
  docs/plan/BROTHER-LOOP-HARDENING-WBS.json   the hardening units and the audit issue list (unit H9)
A package REFERENCES units by id ("launch:D2", "hard:H9"); their states are read here, never copied into the plan.
Every open launch unit and every hardening unit must sit in exactly one package, or --check fails naming it.
The render uses nothing but those files, so the same files always give the same page (no clock, no home folder).
usage: gen_release_plan.py            write docs/plan/BROTHER-LOOP-HARDENING-GANTT.html
       gen_release_plan.py --check    exit 1 naming each coverage gap, or when the page differs from a fresh render
       gen_release_plan.py --h9-closure  re-run every PASS audit issue's own check; exit 0 only when all are PASS and green
"""
import hashlib, html, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
E = html.escape
PLAN = "docs/plan/BROTHER-1.1.0-RELEASE-PLAN.json"
LAUNCH = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
HARD = "docs/plan/BROTHER-LOOP-HARDENING-WBS.json"
PAGE = "docs/plan/BROTHER-LOOP-HARDENING-GANTT.html"
_LANDINGS = {}
STATES = ("PASS", "FAIL", "OPEN", "NO-DATA", "BLOCKED", "RETIRED")
DONE_OF = r"(\d+) of (\d+) units DONE"


def load(root):
    rd = lambda p: json.load(open(os.path.join(root, p), encoding="utf-8"))
    return rd(PLAN), rd(LAUNCH), rd(HARD)


def refs(plan):
    for ws in plan["workstreams"]:
        for p in ws["packages"]:
            for r in p.get("units") or []:
                yield ws, p, r


def coverage_problems(plan, launch, hard):
    """Every way the plan fails to account for open work; an empty list means complete."""
    out = []
    stage_ids = [s["id"] for s in plan.get("stages") or []]
    if stage_ids != ["S%d" % i for i in range(1, 8)]:
        out.append("the stages are not S1 to S7 in order: %s" % stage_ids)
    lu = {u["id"]: u for u in launch["units"]}
    hu = {u["id"]: u for u in hard["units"]}
    seen = {}
    for ws, p, r in refs(plan):
        src, _, uid = r.partition(":")
        table = lu if src == "launch" else hu if src == "hard" else None
        if table is None or uid not in table:
            out.append("%s names %s, which is in neither plan file" % (p["id"], r))
            continue
        seen.setdefault(r, []).append(p["id"])
        for s in p.get("stages") or []:
            if s not in stage_ids:
                out.append("%s names stage %s, which does not exist" % (p["id"], s))
    for uid, u in lu.items():
        r = "launch:" + uid
        if u.get("state") != "DONE" and r not in seen:
            out.append("open launch unit %s (%s) is in no package" % (uid, u.get("state")))
    for uid in hu:
        if "hard:" + uid not in seen:
            out.append("hardening unit %s is in no package" % uid)
    for r, ps in seen.items():
        if len(ps) > 1:
            out.append("%s sits in %d packages (%s); each unit belongs to exactly one" % (r, len(ps), ", ".join(ps)))
    out.extend(stale_counts(plan, launch))
    return out


def stale_counts(plan, launch):
    """One sentence per cut_requirements row whose 'N of M units DONE' disagrees with the launch board; [] when every
    such claim matches or none is made. A row whose evidence is not text is reported, never skipped (CV1.d). N is the
    count of launch units whose state is DONE and M the count of all launch units, read here, never typed."""
    units = launch.get("units") if isinstance(launch, dict) else None
    if not isinstance(units, list):
        return ["the launch board has no units list, so no N of M units DONE claim can be checked"]
    done, total = sum(1 for u in units if isinstance(u, dict) and u.get("state") == "DONE"), len(units)
    rows = plan.get("cut_requirements") if isinstance(plan, dict) else None
    if rows is None:
        return []
    if not isinstance(rows, list):
        return ["cut_requirements is not a list (%s)" % type(rows).__name__]
    out = []
    for i, row in enumerate(rows):
        name = row.get("req") if isinstance(row, dict) else None
        label = name if isinstance(name, str) and name else "cut_requirements row %d" % (i + 1)
        ev = row.get("evidence") if isinstance(row, dict) else None
        if not isinstance(ev, str):
            out.append("%s: evidence is not text (%s), so its count cannot be checked" % (label, type(ev).__name__))
            continue
        for m in re.finditer(DONE_OF, ev):
            n, of = int(m.group(1)), int(m.group(2))
            if (n, of) != (done, total):
                out.append("%s says %d of %d units DONE; the board reads %d of %d" % (label, n, of, done, total))
    return out


def parity_cells(plan):
    """Every (host, dimension, cell). An experimental host's cell is always NO-DATA with the plan's reason, whatever was
    recorded: it is shown, never counted (owner scope decision 2026-10-03, Antigravity experimental in 1.1.0)."""
    hp = plan["host_parity"]
    cells = hp.get("cells") or {}
    exp = set(hp.get("experimental_hosts") or [])
    return [(h, d, {"state": "NO-DATA", "why": hp.get("experimental_why") or "experimental host"} if h in exp else
             cells.get("%s|%s" % (h, d), {"state": "NO-DATA", "why": "not started: the parity phase follows RB and RC"}))
            for h in hp["hosts"] for d in hp["dimensions"]]


def required_cells(plan):
    """The parity cells that gate the release: every host that is not experimental."""
    exp = set(plan["host_parity"].get("experimental_hosts") or [])
    return [c for c in parity_cells(plan) if c[0] not in exp]


def spec_line(root, unit, sub):
    """The first line of the sub unit's own section in its spec, or a plain note when the spec has none."""
    spec = (unit.get("spec") or "").strip()
    try:
        text = open(os.path.join(root, spec), encoding="utf-8").read() if spec else ""
    except OSError:
        return "NO-DATA: the spec %s cannot be read" % spec
    m = re.search(r"(?m)^#+[^\n]*\b%s\b[^\n]*\n+([^\n#][^\n]*)" % re.escape(sub), text)
    return (m.group(1).strip()[:220] if m else "NO-DATA: no section named %s in %s" % (sub, spec or "a spec"))


def landings(root):
    """{task id: {"commit", "reverted_by"}} from the repository's own history, or None when git cannot answer.
    A landing is a commit whose SUBJECT is exactly "<task> land: ..." (so U1.10 is never U1.1); a revert is matched to
    the landing it names by hash ("This reverts commit <hash>"). A task with an unreverted landing counts that one."""
    key = os.path.abspath(root)
    if key in _LANDINGS:
        return _LANDINGS[key]
    def log(grep):
        r = subprocess.run(["git", "-C", root, "log", "--format=%H%x1f%s%x1f%b%x1e", "--grep=" + grep, "HEAD"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise OSError(r.stderr.strip()[:200])
        return [blk.strip("\n").split("\x1f") for blk in r.stdout.split("\x1e") if blk.strip()]
    try:
        lands, reverts = log(" land:"), log("This reverts commit")
    except (OSError, subprocess.SubprocessError):
        _LANDINGS[key] = None
        return None
    reverted = {}
    for f in reverts:
        if len(f) >= 3:
            for h in re.findall(r"This reverts commit ([0-9a-f]{7,40})", f[2]):
                reverted[h] = f[0]
    out = {}
    for f in lands:
        if len(f) < 2:
            continue
        m = re.match(r"^(\S+) land: ", f[1])
        if not m:
            continue
        by = next((v for k, v in reverted.items() if f[0].startswith(k) or k.startswith(f[0])), None)
        prev = out.get(m.group(1))
        if prev is None or (prev["reverted_by"] and not by):
            out[m.group(1)] = {"commit": f[0][:9], "reverted_by": by[:9] if by else None}
    _LANDINGS[key] = out
    return out


def leaves(root, unit, open_deps, scope_rx, landings=None):
    """(id, state, what, blocker) for each sub unit of a launch unit. PASS only when the unit is DONE on the board or
    the task has an exact landing commit that no revert names; a landing later reverted is OPEN; a claim that only the
    unit's prose makes is NO-DATA, never PASS (independent review 2026-09-26: prose said "not landed", "was reverted"
    and "U1.10 landed", and each read as U1.1 complete)."""
    subs = unit.get("sub_units") or []
    ev = unit.get("evidence") or ""
    if landings is None:
        landings = globals()["landings"](root)
    out = []
    if not subs:
        st = "PASS" if unit.get("state") == "DONE" else "OPEN"
        return [(unit["id"], st, "the unit as a whole: " + (unit.get("objective") or unit.get("title") or "")[:200], None if st == "PASS" else blocker(unit, open_deps, scope_rx))]
    for s in subs:
        what = spec_line(root, unit, s)
        if unit.get("state") == "DONE":
            out.append((s, "PASS", what, None)); continue
        if landings is None:
            out.append((s, "NO-DATA", what, "the repository history could not be read, so no landing is known")); continue
        got = landings.get(s)
        if got and not got["reverted_by"]:
            out.append((s, "PASS", what, None)); continue
        if got:
            out.append((s, "OPEN", what, "landed as %s, then reverted by %s" % (got["commit"], got["reverted_by"]))); continue
        if re.search(r"(?<![\w.])%s(?![\w.])" % re.escape(s), ev):
            out.append((s, "NO-DATA", what, "only the unit's prose mentions it; no landing commit proves it")); continue
        out.append((s, "OPEN", what, blocker(unit, open_deps, scope_rx)))
    return out


def blocker(unit, open_deps, scope_rx):
    if open_deps:
        return "waits on %s" % ", ".join(open_deps)
    if unit.get("state") == "DEFERRED":
        return "deferred by the owner's decision until the cut"
    if scope_rx is not None and not scope_rx.search(unit["id"]):
        return "outside the loop's scope as recorded; admit it, build it by hand, or defer it"
    return "none recorded; the loop may build it"


def chip(st):
    cls = {"PASS": "pass", "FAIL": "fail", "BLOCKED": "block"}.get(st, "nodata")
    return '<span class="v %s">%s</span>' % (cls, E(st))


def unit_state_chip(state):
    return chip({"DONE": "PASS", "PARTIAL": "OPEN", "SPECIFIED": "OPEN", "OPEN": "OPEN", "DEFERRED": "OPEN", "RETIRED": "RETIRED"}.get(state, "NO-DATA"))


CSS = """:root{--paper:#F7F8F6;--ink:#141B22;--petrol:#0E7A6F;--petrol-soft:#DCEDE9;--rust:#B4532A;--gold:#B8912C;--line:#CFD6D3;--muted:#5B6660;--card:#FFFFFF;--done:#0E7A6F;--prog:#8FB9B1;--todo:#E3E7E4}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--paper:#141B22;--ink:#EEF2F0;--petrol:#3AA893;--petrol-soft:#1D3833;--rust:#E08A66;--gold:#D9B45A;--line:#2C3A3B;--muted:#9AA8A2;--card:#1B2530;--done:#3AA893;--prog:#2F6B62;--todo:#22303A;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#141B22;--ink:#EEF2F0;--petrol:#3AA893;--petrol-soft:#1D3833;--rust:#E08A66;--gold:#D9B45A;--line:#2C3A3B;--muted:#9AA8A2;--card:#1B2530;--done:#3AA893;--prog:#2F6B62;--todo:#22303A;color-scheme:dark}
body{background:var(--paper);color:var(--ink);font-family:Seravek,"Source Serif 4",Georgia,system-ui,sans-serif;line-height:1.5;padding-block:24px;padding-inline:16px}
.wrap{max-width:1080px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr);gap:28px}.wrap>*{min-width:0}
h1,h2,h3{font-family:"Iowan Old Style","Source Serif 4",Georgia,serif;text-wrap:balance;margin:0}
h1{font-size:2rem;font-weight:600}h2{font-size:1.3rem;font-weight:600;margin-bottom:10px}h3{font-size:1.05rem;font-weight:600}
.eyebrow{font-size:.75rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.stamp{color:var(--muted);font-size:.86rem;max-width:72ch}
.north{border-left:4px solid var(--petrol);background:var(--petrol-soft);padding:14px 16px;border-radius:0 8px 8px 0}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.tile b{display:block;font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.tile .big{font-size:1.5rem;font-variant-numeric:tabular-nums;font-weight:600}
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
.tbl table{border-collapse:collapse;width:100%;min-width:720px;font-size:.84rem}
.tbl th,.tbl td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.tbl th{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;white-space:nowrap}
.stagegrid{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:10px}
.sg{display:grid;grid-template-columns:minmax(220px,2.2fr) repeat(7,minmax(64px,1fr));gap:4px 6px;min-width:760px;align-items:center;font-size:.84rem}
.sg .hd{font-size:.7rem;color:var(--muted);text-align:center;letter-spacing:.03em}
.sg .ws{grid-column:1/-1;font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);padding-top:8px}
.bar{height:16px;border-radius:3px}.bar.done{background:var(--done)}.bar.prog{background:repeating-linear-gradient(135deg,var(--prog) 0 4px,transparent 4px 8px);border:1px solid var(--prog)}.bar.todo{background:var(--todo);border:1px dashed var(--line)}
.n{font-variant-numeric:tabular-nums;color:var(--muted);font-size:.78rem}
.v{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;padding:2px 6px;border-radius:4px;white-space:nowrap}
.v.pass{background:var(--petrol-soft);color:var(--petrol)}.v.fail{background:color-mix(in srgb,var(--rust) 16%,transparent);color:var(--rust)}.v.nodata{background:var(--todo);color:var(--muted)}.v.block{background:color-mix(in srgb,var(--gold) 18%,transparent);color:var(--gold)}
details.pk{background:var(--card);border:1px solid var(--line);border-radius:8px}
details.pk>summary{cursor:pointer;padding:10px 12px;display:grid;grid-template-columns:56px 1fr auto;gap:10px;align-items:center;list-style:none}
details.pk>summary::-webkit-details-marker{display:none}
details.pk>summary:focus-visible{outline:2px solid var(--petrol);outline-offset:2px}
.pkbody{padding:0 12px 12px;display:grid;gap:10px}
.unit{border-top:1px solid var(--line);padding-top:8px}
.unit p{margin:4px 0;font-size:.84rem}
.id{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:var(--petrol)}
code{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;overflow-wrap:anywhere}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;font-size:.9rem}
.foot{color:var(--muted);font-size:.82rem;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:600px){details.pk>summary{grid-template-columns:48px 1fr}details.pk>summary .n{grid-column:1/-1}}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}"""


def render(plan, launch, hard, root):
    lu = {u["id"]: u for u in launch["units"]}
    hu = {u["id"]: u for u in hard["units"]}
    done_ids = {k for k, u in lu.items() if u.get("state") == "DONE"}
    try:
        scope_rx = re.compile(plan["loop_scope_recorded"]["regex"])
    except (KeyError, TypeError, re.error):
        scope_rx = None
    digest = hashlib.sha256(b"".join(open(os.path.join(root, p), "rb").read() for p in (PLAN, LAUNCH, HARD))).hexdigest()[:12]
    h9 = hu.get("H9") or {"issues": []}
    iss = h9.get("issues") or []
    ni = {s: sum(1 for i in iss if i.get("state") == s) for s in STATES}
    cells = required_cells(plan)
    exp_hosts = plan["host_parity"].get("experimental_hosts") or []
    cut = plan.get("cut_requirements") or []
    cut_pass = sum(1 for c in cut if str(c.get("state", "")).upper().startswith("PASS"))
    open_units = [u for u in launch["units"] if u.get("state") != "DONE"]

    # every package's leaves, counted once, used by the stage chart and the drill down
    pk_leaves = {}
    for ws in plan["workstreams"]:
        for p in ws["packages"]:
            L = []
            for r in p.get("units") or []:
                src, _, uid = r.partition(":")
                if src == "launch":
                    u = lu[uid]; deps = [d for d in (u.get("depends_on") or []) if d not in done_ids]
                    L += [("launch", uid) + x for x in leaves(root, u, deps, scope_rx)]
                elif uid == "H9":
                    L += [("issue", "H9", i["id"], i["state"], i["issue"], None if i["state"] == "PASS" else i.get("fix")) for i in iss]
                elif uid == "RR" and (hu.get("RR") or {}).get("issues"):
                    L += [("issue", "RR", i["id"], i["state"], i["issue"], None if i["state"] == "PASS" else i.get("fix")) for i in hu["RR"]["issues"]]
                elif src == "hard" and not (p["id"] == "P1.2" and uid in ("H2", "H3")):
                    u = hu[uid]; st = {"DONE": "PASS", "RETIRED": "RETIRED"}.get(u.get("state"), "OPEN")   # RETIRED: closed by an owner decision, never a PASS
                    L.append(("hard", uid, uid, st, u.get("title", ""), None if st in ("PASS", "RETIRED") else (u.get("remains") or "open")))
            for it in p.get("items") or []:
                L.append(("item", p["id"], it["id"], it["state"], it["what"], it.get("why")))
            if p.get("parity"):
                L += [("cell", "HP", "%s: %s" % (h, d), c["state"], "%s on %s" % (d, h), c.get("why")) for h, d, c in cells]
            if p.get("cut"):
                L += [("cut", "CUT", "C%02d" % (k + 1), (str(c["state"]).split()[0].upper() if str(c["state"]).split()[0].upper() in STATES else "OPEN"), c["req"] + (" (release time: closes in S7)" if c.get("when") == "release" else ""), c.get("remaining")) for k, c in enumerate(cut)]
            pk_leaves[p["id"]] = L
    all_leaves = [x for L in pk_leaves.values() for x in L]
    n_open = sum(1 for x in all_leaves if x[3] not in ("PASS", "RETIRED"))

    out = []
    w = out.append
    w("<title>Brother 1.1.0 Release Plan</title>")
    w('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap">')
    w("<style>\n%s\n</style>" % CSS)
    w('<div class="wrap">')
    w('<header><div class="eyebrow">Brother 1.1.0, from here to the cut</div><h1>Brother 1.1.0 Release Plan</h1>'
      '<div class="stamp">Generated by scripts/gen_release_plan.py from docs/plan/BROTHER-1.1.0-RELEASE-PLAN.json, the launch board and the '
      'hardening plan (content digest %s). Never hand edited: change a plan file and regenerate. PASS means the check named beside it ran '
      'after the last edit and passed; everything else is FAIL, OPEN or NO-DATA with its reason. No date is written anywhere.</div></header>' % digest)
    w('<section class="north"><div class="eyebrow">The goal</div>%s</section>' % E(plan["goal"]))
    w('<section class="strip">')
    w('<div class="tile"><b>Open work</b><span class="big">%d</span><br>tasks still open of %d on this page, across %d workstreams</div>' % (n_open, len(all_leaves), len(plan["workstreams"])))
    w('<div class="tile"><b>Board</b><span class="big">%d / %d</span><br>launch units DONE; %d open</div>' % (len(done_ids), len(lu), len(open_units)))
    w('<div class="tile"><b>Loop audit issues</b><span class="big">%d / %d</span><br>PASS; %d FAIL, %d OPEN. None deployed yet.</div>' % (ni["PASS"], len(iss), ni["FAIL"], ni["OPEN"]))
    w('<div class="tile"><b>%s parity</b><span class="big">%d / %d</span><br>parity cells PASS; the phase follows the proof runs%s</div>' % (E(" and ".join(sorted({c[0] for c in cells}))), sum(1 for c in cells if c[2]["state"] == "PASS"), len(cells), ("; %s experimental, NO-DATA, not counted" % E(", ".join(exp_hosts))) if exp_hosts else ""))
    w('<div class="tile"><b>Cut requirements</b><span class="big">%d / %d</span><br>PASS on the fix branch; the rest need a stage above</div>' % (cut_pass, len(cut)))
    w("</section>")

    # the stage chart
    w('<section id="stages"><h2>The path to the cut, stage by stage</h2>')
    w('<p class="stamp">Read left to right: a stage starts when the one before it meets its exit. Solid: every task in the package PASS. '
      'Hatched: some PASS. Faint: none yet. The numbers are tasks PASS of tasks in the package.</p>')
    w('<div class="stagegrid"><div class="sg"><div></div>' + "".join('<div class="hd">%s<br>%s</div>' % (E(s["id"]), E(s["name"])) for s in plan["stages"]))
    sids = [s["id"] for s in plan["stages"]]
    for ws in plan["workstreams"]:
        w('<div class="ws">%s %s</div>' % (E(ws["id"]), E(ws["name"])))
        for p in ws["packages"]:
            L = pk_leaves[p["id"]]; k = sum(1 for x in L if x[3] in ("PASS", "RETIRED"))
            cls = "done" if L and k == len(L) else "prog" if k else "todo"
            w('<div><span class="id">%s</span> %s <span class="n">%d/%d</span></div>' % (E(p["id"]), E(p["name"]), k, len(L)))
            w("".join('<div class="bar %s"></div>' % cls if s in p["stages"] else "<div></div>" for s in sids))
    w("</div></div>")
    w('<div class="tbl" style="margin-top:12px"><table><thead><tr><th>Stage</th><th>Exit, before the next stage may start</th></tr></thead><tbody>'
      + "".join("<tr><td><b>%s</b> %s</td><td>%s</td></tr>" % (E(s["id"]), E(s["name"]), E(s["exit"])) for s in plan["stages"]) + "</tbody></table></div></section>")

    # the loop audit issues, first, because they gate everything
    w('<section id="issues"><h2>brother.loop and repair.loop: every audit issue (H9)</h2>')
    w('<p class="stamp">%d issues from the owner\'s stop and the audit: %d PASS, %d FAIL, %d OPEN. PASS means fixed at the source, red before and '
      'green after, mutation tested and integrated on the fix branch; none is deployed until stage S2.</p>' % (len(iss), ni["PASS"], ni["FAIL"], ni["OPEN"]))
    w('<div class="tbl"><table><thead><tr><th>Issue</th><th>Fix</th><th>State</th><th>Check</th><th>Proof</th></tr></thead><tbody>')
    for i in iss:
        w('<tr data-issue="%s"><td><b>%s</b> %s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>'
          % (E(i["id"]), E(i["id"]), E(i["issue"]), E(i.get("fix", "")), chip(i["state"]), E(i.get("check", "")), E(i.get("proof", ""))))
    w("</tbody></table></div></section>")

    # run readiness: every issue that must close before RB, with the lane working it
    riss = (hu.get("RR") or {}).get("issues") or []
    if riss:
        nr = {s: sum(1 for i in riss if i.get("state") == s) for s in ("PASS", "PARTIAL", "OPEN")}
        w('<section id="rr"><h2>Run readiness (RR): every issue before RB, and the lane working it</h2>')
        w('<p class="stamp">%d issues: %d PASS, %d PARTIAL, %d OPEN. RB starts only when every one is PASS, integrated on hub main, '
          'frozen and canaried, and the owner lifts HOLD and PAUSE.</p>' % (len(riss), nr["PASS"], nr["PARTIAL"], nr["OPEN"]))
        w('<div class="tbl"><table><thead><tr><th>Issue</th><th>Severity</th><th>Lane or fix</th><th>State</th><th>Check</th><th>Proof</th></tr></thead><tbody>')
        for i in riss:
            w('<tr data-rr="%s"><td><b>%s</b> %s</td><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>'
              % (E(i["id"]), E(i["id"]), E(i["issue"]), E(i.get("severity", "")), E(i.get("fix", "")), chip(i["state"]),
                 E(i.get("check", "")), E(i.get("proof", ""))))
        w("</tbody></table></div></section>")

    # workstreams, drillable to the task
    w('<section id="work"><h2>Every workstream, down to the task</h2><div style="display:grid;gap:10px">')
    for ws in plan["workstreams"]:
        w("<h3>%s %s</h3>" % (E(ws["id"]), E(ws["name"])))
        for p in ws["packages"]:
            L = pk_leaves[p["id"]]; k = sum(1 for x in L if x[3] in ("PASS", "RETIRED"))
            w('<details class="pk"><summary><span class="id">%s</span><span>%s</span><span class="n">%s | %d of %d tasks closed (PASS, or RETIRED by an owner decision)</span></summary><div class="pkbody">'
              % (E(p["id"]), E(p["name"]), ", ".join(p["stages"]), k, len(L)))
            for r in p.get("units") or []:
                src, _, uid = r.partition(":")
                u = lu.get(uid) if src == "launch" else hu.get(uid)
                if not u or (src == "hard" and uid == "H9"):
                    continue
                deps = [d for d in (u.get("depends_on") or []) if d not in done_ids] if src == "launch" else list(u.get("depends_on") or [])
                w('<div class="unit"><p><span class="id">%s</span> %s %s</p>' % (E(uid), unit_state_chip(u.get("state")), E(u.get("title", ""))))
                dc = u.get("done_check") or u.get("done_check_prose") or "NO-DATA: no done check recorded"
                w("<p class=\"n\">Done check: <code>%s</code>%s</p>" % (E(str(dc)[:300]), (" | waits on " + E(", ".join(deps))) if deps else ""))
                if src == "launch":
                    rows = [x for x in L if x[0] == "launch" and x[1] == uid]
                    w('<div class="tbl"><table><thead><tr><th>Task</th><th>State</th><th>What it delivers (from its spec)</th><th>Blocker</th></tr></thead><tbody>'
                      + "".join("<tr><td class=\"id\">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (E(x[2]), chip(x[3]), E(x[4]), E(x[5] or "")) for x in rows)
                      + "</tbody></table></div>")
                w("</div>")
            extra = [x for x in L if x[0] in ("item", "cell", "cut")]
            if extra:
                w('<div class="tbl"><table><thead><tr><th>Task</th><th>State</th><th>What</th><th>Blocker or remaining</th></tr></thead><tbody>'
                  + "".join("<tr><td class=\"id\">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (E(x[2]), chip(x[3]), E(x[4]), E(x[5] or "")) for x in extra)
                  + "</tbody></table></div>")
            if p.get("parity"):
                w('<p class="stamp">%s Capability list checked per cell: %s.</p>' % (E(plan["host_parity"]["rule"]), E(plan["host_parity"]["capabilities_source"])))
                if plan["host_parity"].get("experimental_hosts"):
                    w('<p class="stamp">%s: %s.</p>' % (E(", ".join(plan["host_parity"]["experimental_hosts"])), E(plan["host_parity"].get("experimental_why") or "experimental")))
            w("</div></details>")
    w("</div></section>")

    w('<section id="owner"><h2>Only the owner can decide these</h2><div class="cards">'
      + "".join('<div class="card">%s</div>' % E(d) for d in plan.get("owner_decisions") or []) + "</div></section>")
    w('<footer class="foot">Tick contract: a task shows PASS only when its check ran after its last edit and passed; a figure on this page is '
      'counted from the plan files. Regenerate with python3 -B scripts/gen_release_plan.py; verify with --check.</footer>')
    w("</div>")
    return "\n".join(out) + "\n"


def h9_closure(hard, root, run=None):
    """(closed, rows) for H9. It closes only when every audit issue is PASS AND that issue's own check, re-run now from
    the repository root, exits 0. A PASS with no check is a claim; an OPEN or FAIL issue keeps H9 open; no issues at all
    is NO-DATA, never closed. The page's freshness is not evidence that an issue is closed (plan audit 2026-09-26)."""
    if run is None:
        run = lambda cmd: subprocess.run(cmd, shell=True, cwd=root, capture_output=True, timeout=1800).returncode
    h9 = next((u for u in hard.get("units") or [] if u.get("id") == "H9"), None)
    issues = (h9 or {}).get("issues") or []
    if not issues:
        return False, [("H9", "NO-DATA", "no audit issues recorded")]
    rows = []
    for i in issues:
        chk = (i.get("check") or "").strip()
        if i.get("state") != "PASS":
            rows.append((i.get("id"), "OPEN", "state %s" % i.get("state"))); continue
        if not chk or chk.lower().startswith("none"):
            rows.append((i.get("id"), "FAIL", "PASS with no check: a claim, not evidence")); continue
        try:
            rc = run(chk)
        except (OSError, subprocess.SubprocessError) as exc:
            rows.append((i.get("id"), "FAIL", "the check could not run: %s" % exc)); continue
        rows.append((i.get("id"), "PASS" if rc == 0 else "FAIL", "%s exit %s" % (chk, rc)))
    return all(r[1] == "PASS" for r in rows), rows


def main(argv):
    root = os.path.dirname(HERE)
    try:
        plan, launch, hard = load(root)
    except (OSError, ValueError) as exc:
        print("NO-DATA: a plan file could not be read (%s)" % exc); return 2
    if "--h9-closure" in argv:
        closed, rows = h9_closure(hard, root)
        for r in rows:
            print("%-5s %-7s %s" % r)
        n = sum(1 for r in rows if r[1] != "PASS")
        print("PASS: H9 closed, every audit issue re-checked green" if closed else "OPEN: %d of %d audit issues not closed" % (n, len(rows)))
        return 0 if closed else 1
    probs = coverage_problems(plan, launch, hard)
    page = render(plan, launch, hard, root)
    if "--check" in argv:
        path = os.path.join(root, PAGE)
        try:
            on_disk = open(path, encoding="utf-8").read()
        except OSError:
            on_disk = None
        if on_disk != page:
            probs.append("the page differs from a fresh render: run python3 -B scripts/gen_release_plan.py")
        print("PASS: the plan covers every open unit and the page is current" if not probs else "FAIL: " + "; ".join(probs))
        return 1 if probs else 0
    if probs:
        print("REFUSED: " + "; ".join(probs)); return 1
    open(os.path.join(root, PAGE), "w", encoding="utf-8").write(page)
    print("written %s" % PAGE); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

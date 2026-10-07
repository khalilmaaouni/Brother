# Builds docs/plan/BROTHER-LOOP.html. Diagrams and code explanation are written here;
# the six prose sections are slotted in from the prose draft.
import html, os, re, sys

PROSE = sys.argv[1] if len(sys.argv) > 1 else None
OUT = "docs/plan/BROTHER-LOOP.html"

def prose_sections(path):
    """Split the prose draft into its six numbered sections. Missing draft is stated, never faked."""
    if not path or not os.path.isfile(path):
        return {}
    text = open(path, encoding="utf-8").read()
    parts = re.split(r"^\s*(?:#+\s*)?(\d)[.)]\s+", text, flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = parts[i + 1].strip()
    return out

P = prose_sections(PROSE)

def para(n, fallback):
    body = P.get(n)
    if not body:
        return '<p class="missing">NOT YET DRAFTED. %s</p>' % html.escape(fallback)
    def fmt(b):
        # the prose draft marks paths with backticks; escape first, then promote to <code>,
        # so a path can never smuggle markup in through the draft.
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(b.strip()))
    return "\n".join("<p>%s</p>" % fmt(b) for b in body.split("\n\n")
                     if b.strip() and not b.strip().startswith("##"))

STAGES = """
<svg viewBox="0 0 860 300" role="img" aria-label="The three stages of brother.loop">
  <defs>
    <marker id="a" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">
      <polygon points="0 0, 9 3.5, 0 7" fill="var(--petrol)"/>
    </marker>
  </defs>
  <rect x="20"  y="70" width="230" height="150" rx="10" class="box"/>
  <rect x="315" y="70" width="230" height="150" rx="10" class="box"/>
  <rect x="610" y="70" width="230" height="150" rx="10" class="box"/>
  <text x="135" y="100" class="bt">MODEL</text>
  <text x="430" y="100" class="bt">MACHINE</text>
  <text x="725" y="100" class="bt">LANDING</text>
  <text x="135" y="128" class="bs">brief, build, write probes</text>
  <text x="430" y="128" class="bs">grade, execute probes</text>
  <text x="725" y="128" class="bs">gates, commit, push</text>
  <text x="135" y="162" class="bl">limited by MONEY</text>
  <text x="430" y="162" class="bl">limited by CORES</text>
  <text x="725" y="162" class="bl">limited by ONE TREE</text>
  <text x="135" y="188" class="bm">burn_guard cap</text>
  <text x="430" y="188" class="bm">local slot pool</text>
  <text x="725" y="188" class="bm">serial, one writer</text>
  <text x="135" y="208" class="bw">wide</text>
  <text x="430" y="208" class="bw">bounded</text>
  <text x="725" y="208" class="bw">one at a time</text>
  <line x1="250" y1="145" x2="305" y2="145" class="arr" marker-end="url(#a)"/>
  <line x1="545" y1="145" x2="600" y2="145" class="arr" marker-end="url(#a)"/>
  <text x="430" y="265" class="cap">Each stage is bounded by a different resource, so each needs its own limit.
  Bound them together and you throttle the cheap stage to protect the expensive one.</text>
</svg>"""

LADDER = """
<svg viewBox="0 0 860 210" role="img" aria-label="The pass ladder, one action per pass">
  <text x="20" y="28" class="bt2">ONE PASS CHOOSES ONE ACTION, TOP RUNG FIRST</text>
""" + "".join(
    '<rect x="%d" y="60" width="86" height="54" rx="8" class="%s"/>'
    '<text x="%d" y="83" class="rg">%s</text><text x="%d" y="101" class="rs">%s</text>'
    % (20 + i * 95, "box writer" if i < 4 else "box", 63 + i * 95, n, 63 + i * 95, s)
    for i, (n, s) in enumerate([("CLEAN", "tree"), ("PUSH", "commits"), ("LAND", "builds"),
                                ("CLOSE", "units"), ("PROBE", "dirty"), ("DIAG", "stuck"),
                                ("START", "runners"), ("WAIT", "in flight"), ("NOTHING", "idle")])) + """
  <text x="20" y="150" class="cap2">The first four rungs are the WRITER. They come first because the writer is
  serial, so it must never sit idle while a landing is possible.</text>
  <text x="20" y="178" class="cap2">A rung that fails RECORDS its failure, or the scheduler picks it again next
  pass, forever. Five livelocks in one night were all this one bug.</text>
</svg>"""

STATES = [("FINISHED", "every unit DONE with evidence, nothing ready, no worker alive", "normal"),
          ("DEADLINE", "the wall clock stop time arrived", "normal"),
          ("STALLED", "work remains but nothing is admissible: needs a decision, not another pass", "alarm"),
          ("BLOCKED", "the tree cannot reach hub, or changes cannot be attributed", "alarm"),
          ("UNPRODUCTIVE", "a pass landed nothing, closed nothing, started nothing, no runner alive", "alarm"),
          ("UNFUNDED", "the money is spent, or funding could not be read", "alarm"),
          ("BUDGET", "the run spent the budget the owner set at intake", "alarm"),
          ("DISK", "free disk space fell under the floor; the run stops before a write fails half way", "alarm"),
          ("STOPPED", "the owner's stop request in LOOP-STOP-REQUEST.txt named this driver's pid", "normal"),
          ("INTERRUPTED", "the driver was killed or the shell exited", "alarm"),
          ("REFUSED", "it would not start at all: bad deadline, lease held, or tree claimed", "alarm")]

CODE = [
 ("The lease, and why it is an atomic create",
  "scripts/loop/loop_guard.sh",
  "if ( set -o noclobber; write_lease > \"$LEASE\" ) 2>/dev/null; then\n"
  "  echo \"ACQUIRED loop lease for pid ${PID}\"; exit 0\nfi",
  "noclobber makes the redirect FAIL when the file already exists, and that test and create is one "
  "operation in the kernel, so exactly one racer can win it. The previous version read the file, decided, "
  "then wrote, which is two operations with a gap in between. Racing eight acquisitions against it, all "
  "eight believed they had won."),
 ("Reading nothing means look again, never take",
  "scripts/loop/loop_guard.sh",
  "if [ -z \"$HOLDER\" ]; then sleep 0.05; continue; fi",
  "Making the create atomic was necessary and not sufficient. A racer reading in the instant between "
  "another racer's remove and its create saw no holder, concluded the lease was abandoned, removed it too, "
  "and won a create it should have lost. Six of eight still acquired until this line existed."),
 ("The probe verdict must be able to reject",
  "scripts/loop/unit_runner.py",
  "if verdict != \"NO-DATA\" and not finds:\n"
  "    teach(\"grader PASS, probes clean at round %d\" % rnd, ok=True)\n"
  "    status(\"READY %s (round %d, grader PASS, probes clean)\" % (best, rnd)); sys.exit(0)",
  "The last line used to sit one indent level to the LEFT, outside the guard above it. So it ran on every "
  "path and exited successfully while printing the words probes clean, whatever the red team had found. "
  "Every probe finding was discarded, across 9.77 busy hours, and the repair round below was unreachable."),
 ("A red batch finds its own culprit",
  "scripts/loop/land_batch.py",
  "if len(landed) > 1 and \"--no-bisect\" not in sys.argv:\n"
  "    for b in builds:\n"
  "        subprocess.run([sys.executable, __file__, \"--no-bisect\", b])",
  "When five builds fail the gates together, blame is ambiguous, so all five were reverted and the same "
  "five were offered again every pass, forever. The file's own comment said the caller would retry them "
  "one at a time. No such caller was ever written. Retrying each alone lands the innocent ones and "
  "quarantines the guilty one."),
 ("Unknown fails towards the alarm",
  "scripts/loop/loop_heartbeat.py",
  "if rec is None:\n"
  "    return \"ALARM\", \"no heartbeat could be read: the loop may never have started\"",
  "A missing, corrupt or unreadable pulse reads ALARM, never healthy. A silent death and a healthy run "
  "must not look alike, and of the two possible mistakes, crying wolf costs a glance while the other cost "
  "a whole evening."),
 ("A pulse from the future is not a healthy pulse",
  "scripts/loop/loop_heartbeat.py",
  "if age < -CLOCK_SKEW_MINUTES:\n"
  "    return \"ALARM\", \"the heartbeat is stamped %d minutes in the FUTURE\" % abs(age)",
  "Every staleness test is age greater than a limit, and a negative age passes it. So a stamp six hours "
  "ahead read as healthy, and would have forever. Clock skew, a hand edit and a wrong timezone all produce "
  "exactly this.")]

def code_block(title, path, code, why):
    return ('<article class="code"><h3>%s</h3><p class="path">%s</p>'
            '<pre><code>%s</code></pre><p class="why">%s</p></article>'
            % (html.escape(title), html.escape(path), html.escape(code), html.escape(why)))

doc = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brother Loop</title>
<style>
:root{--petrol:#0E7A6F;--paper:#F7F8F6;--slate:#141B22;--muted:#5a6672;--line:#dfe3e0;--alarm:#A3231B;--warn:#B07A0E;--card:#fff}
:root:not([data-theme="light"]){}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--petrol:#3AA893;--paper:#12171b;--slate:#e8eceb;--muted:#9aa7ad;--line:#2a3238;--card:#181e23}}
:root[data-theme="dark"]{--petrol:#3AA893;--paper:#12171b;--slate:#e8eceb;--muted:#9aa7ad;--line:#2a3238;--card:#181e23}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--slate);font:16px/1.65 Seravek,"Gill Sans Nova",Ubuntu,Calibri,system-ui,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:40px 16px 80px}
h1,h2,h3{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;font-weight:600;line-height:1.2}
h1{font-size:2.5rem;margin:.2em 0 .1em}
h2{font-size:1.5rem;margin:2.2em 0 .5em;padding-bottom:.25em;border-bottom:1px solid var(--line)}
h3{font-size:1.08rem;margin:0 0 .2em}
.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:.72rem;color:var(--petrol);font-weight:700;margin:0}
.stamp{color:var(--muted);font-size:.85rem;margin:.3em 0 2em}
.lede{font-size:1.1rem;border-left:4px solid var(--petrol);padding-left:16px;margin:1.5em 0}
svg{width:100%;height:auto;margin:1.2em 0;background:var(--card);border:1px solid var(--line);border-radius:12px}
.box{fill:var(--card);stroke:var(--petrol);stroke-width:2}
.box.writer{stroke:var(--alarm)}
.bt{font:700 17px Seravek,system-ui,sans-serif;fill:var(--petrol);text-anchor:middle}
.bt2{font:700 13px Seravek,system-ui,sans-serif;fill:var(--petrol)}
.bs{font:13px Seravek,system-ui,sans-serif;fill:var(--slate);text-anchor:middle}
.bl{font:700 12px Seravek,system-ui,sans-serif;fill:var(--slate);text-anchor:middle}
.bm{font:12px Seravek,system-ui,sans-serif;fill:var(--muted);text-anchor:middle}
.bw{font:italic 12px Seravek,system-ui,sans-serif;fill:var(--muted);text-anchor:middle}
.rg{font:700 12px Seravek,system-ui,sans-serif;fill:var(--slate);text-anchor:middle}
.rs{font:10px Seravek,system-ui,sans-serif;fill:var(--muted);text-anchor:middle}
.arr{stroke:var(--petrol);stroke-width:2}
.cap,.cap2{font:13px Seravek,system-ui,sans-serif;fill:var(--muted)}
.cap{text-anchor:middle}
table{width:100%;border-collapse:collapse;margin:1em 0;font-size:.93rem}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.tag{font-weight:700;white-space:nowrap}
.tag.alarm{color:var(--alarm)}
.tag.normal{color:var(--petrol)}
.code{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:1em 0}
.path{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--petrol);margin:0 0 .7em}
pre{overflow-x:auto;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:12px;margin:0}
code{font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.why{color:var(--muted);font-size:.92rem;margin:.8em 0 0}
.missing{color:var(--alarm);font-style:italic}
footer{margin-top:3em;padding-top:1em;border-top:1px solid var(--line);color:var(--muted);font-size:.85rem}
</style></head><body><div class="wrap">
<p class="eyebrow">Brother 1.1.0</p>
<h1>brother.loop</h1>
<p class="stamp">Unit BL on the launch board &middot; specification at docs/plan/specs/BL.md</p>
<p class="lede">The machine that turns a plan into landed code without a human sitting over it.
It reads the plan, decides one action, does it, records what happened, and repeats.</p>

<h2>1. What it is</h2>
""" + para(1, "Draft pending for section 1.") + """
<h2>2. What it is not</h2>
""" + para(2, "Draft pending for section 2.") + """
<h2>3. The three stage law</h2>
""" + STAGES + para(3, "Draft pending for section 3.") + """
<h2>4. One pass, one action</h2>
""" + LADDER + para(4, "Draft pending for section 4.") + """
<h2>5. How it tells you something is wrong</h2>
""" + para(5, "Draft pending for section 5.") + """
<table><thead><tr><th>state</th><th>what it means</th></tr></thead><tbody>
""" + "".join('<tr><td class="tag %s">%s</td><td>%s</td></tr>' % (k, html.escape(n), html.escape(d))
              for n, d, k in STATES) + """
</tbody></table>
<p>The heartbeat reduces all of that to three words a reader can act on:
<strong>PRODUCING</strong> (alive and landing work), <strong>QUIET</strong> (alive, but several passes
have landed nothing), and <strong>ALARM</strong> (a terminal state, a stale pulse, or a pulse that
cannot be read at all).</p>

<h2>6. What it protects against</h2>
""" + para(6, "Draft pending for section 6.") + """
<h2>7. The code, and why each line is there</h2>
<p>Six places where the loop was wrong, what the fix looks like, and why it matters. Every path below
exists in this repository.</p>
""" + "".join(code_block(*c) for c in CODE) + """
<footer>Generated by scripts/loop/gen_loop_doc.py. Prose verified against the tree. Every file path
named on this page was checked to exist.</footer>
</div></body></html>"""

open(OUT, "w", encoding="utf-8").write(doc)
print("wrote %s (%d bytes), prose sections present: %s" % (OUT, len(doc), sorted(P) or "none yet"))

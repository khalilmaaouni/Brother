"""decide: never hand a human a wall, hand them a screen.

The founder's words, 2026-08-29, after being handed a technical blocker with no
options in it: "use the UI/UX recommendation engine to suggest me solutions and
choices with outcomes and patterns, this is where you should help me make
decisions", then, for the watchdog specifically: "show me the screen as an HTML
with pros and cons and different options sources, mermaid diagram etc ... the
engineer and non technical BA can make informed decisions but also look into the
code of each option if they desire ... Always show the process flow, diagrams and
explain the pros and cons, where did you find the best practices, why it is a
good idea, weighted score before leaving the choice to the human. unless told by
the human to make these choices automatically but following the same patterns".

SO THIS IS A GENERATOR, NOT A PAGE. A hand built page satisfies the request once
and then the next decision arrives as a wall of prose again, which is exactly the
failure being corrected. A decision is written as data, and the screen is
rendered from it, so every future decision costs a JSON file rather than an
afternoon.

FOUR THINGS IT REFUSES TO FAKE, and each one is a way this could have been
theatre:

  THE SCORE IS COMPUTED, NEVER TYPED. A weighted total written by hand is an
  opinion wearing arithmetic's clothes. The spec carries criteria with weights
  and per-option raw marks, this file multiplies them, and the page SHOWS the
  multiplication so a reader can disagree with a number rather than with a
  verdict.

  THE CODE IS READ FROM THE REAL FILE AT RENDER TIME, at real line numbers.
  Pasted code rots the moment the file moves, and a page promising "look at the
  code" while showing a stale copy is worse than one that shows none. A path
  that cannot be read renders NO-DATA in place of the excerpt, loudly.

  A CLOSE CALL IS NAMED AS CLOSE. When the top two options sit inside the
  margin, the page says the ranking does not separate them and the judgement is
  the human's. Presenting 8.4 against 8.2 as a winner is how a recommendation
  engine starts lying politely.

  THE SOURCE OF EVERY BEST PRACTICE IS NAMED, with the file in this repository
  where the research was written down, so a claim about what some framework does
  can be walked back to its own record instead of being taken on trust.

Renders one self contained HTML file that makes NO request when it is opened
(E48, run 5 critic 1, section 5, 2026-09-03: the page used to pull mermaid
from a third party CDN at view time, which is an egress out of an artifact
that looks offline, and a security team reading a decision page found it).
Flows are emitted as <pre class="mermaid"> blocks and nothing else: a viewer
that renders mermaid natively draws them, and every other reader sees the
flow source as text, which is legible on its own and rendered by any mermaid
viewer. Nothing here is fetched, so there is no renderer to inline and no
vendored copy to keep current.

Python 3, standard library only. No network.
"""
import argparse
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# C3: the intake sentinel lands in the running client's own config
# directory (docs/codex/HOOKS-MAPPING.md). Under Claude that is the same
# ~/.claude it always was.
sys.path.insert(0, HERE)
import brother_paths  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: How close two totals may sit before the ranking is declared not to separate
#: them. Half a point on a ten point scale: below this the difference is inside
#: the noise of anybody's honest guess at a raw mark.
CLOSE_MARGIN = 0.5

NODATA = "NO-DATA"


def normalise(criteria):
    """Weights as given, plus the factor they were divided by.

    Returns (list, note). Weights that do not sum to one are normalised and the
    note SAYS SO on the page, because silently rescaling somebody's weights and
    then showing a total is how a score stops meaning what its author meant."""
    total = sum(float(c.get("weight", 0)) for c in criteria)
    if total <= 0:
        return criteria, ("every weight is zero or missing, so no total can be "
                          "computed and the ranking below is %s" % NODATA)
    out = []
    for c in criteria:
        d = dict(c)
        d["weight"] = float(c.get("weight", 0)) / total
        out.append(d)
    note = ""
    if abs(total - 1.0) > 0.001:
        note = ("the weights as written sum to %.2f rather than 1.00, so they "
                "were divided by %.2f. The ranking is unchanged by this; the "
                "printed percentages are what the arithmetic actually used"
                % (total, total))
    return out, note


def score(option, criteria):
    """(total, rows). Rows carry the multiplication so it can be checked by eye.

    A criterion the option was never marked on contributes NOTHING and is
    listed as unmarked, rather than defaulting to zero or to the middle. Both
    defaults invent an opinion nobody held."""
    rows, total, unmarked = [], 0.0, []
    marks = option.get("scores") or {}
    basis = option.get("score_basis") or {}
    for c in criteria:
        key = c.get("key")
        if key not in marks:
            unmarked.append(c.get("label", key))
            rows.append({"label": c.get("label", key), "weight": c["weight"],
                         "mark": None, "product": 0.0,
                         "why": basis.get(key, "")})
            continue
        mark = float(marks[key])
        product = mark * c["weight"]
        total += product
        rows.append({"label": c.get("label", key), "weight": c["weight"],
                     "mark": mark, "product": product,
                     "why": basis.get(key, "")})
    return total, rows, unmarked


def excerpt(spec):
    """(text, note). The real file, at render time, or a loud NO-DATA.

    This is the promise the founder made explicit: a reader who wants to see the
    code sees THIS code, not a copy of what it said when the page was written."""
    path = spec.get("path", "")
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return None, "%s: %s is not present, so no excerpt could be read" % (
            NODATA, path)
    lines = spec.get("lines", "")
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            body = fh.read().splitlines()
    except OSError as exc:
        return None, "%s: %s could not be read: %s" % (NODATA, path, exc)
    if not lines:
        return "\n".join(body[:40]), ""
    try:
        first, _, last = lines.partition("-")
        a = int(first)
        b = int(last) if last else a
    except ValueError:  # sbe: allow-silent (text, note) is this function's documented contract; the caller checks text is None and renders note
        return None, "%s: the line range %r could not be read" % (NODATA, lines)
    if a < 1 or a > len(body):
        return None, ("%s: %s has %d lines, so line %d does not exist. The file "
                      "moved under this page" % (NODATA, path, len(body), a))
    got = body[a - 1:min(b, len(body))]
    numbered = "\n".join("%5d  %s" % (a + i, t) for i, t in enumerate(got))
    return numbered, ""


def rank(spec):
    """Every option scored, best first, plus whether the top is really a top."""
    criteria, weight_note = normalise(spec.get("criteria") or [])
    scored = []
    for opt in spec.get("options") or []:
        total, rows, unmarked = score(opt, criteria)
        scored.append({"option": opt, "total": total, "rows": rows,
                       "unmarked": unmarked})
    scored.sort(key=lambda s: -s["total"])
    close = False
    if len(scored) > 1:
        close = (scored[0]["total"] - scored[1]["total"]) < CLOSE_MARGIN
    return criteria, weight_note, scored, close


E = html.escape


def _pill(text, kind=""):
    return '<span class="pill %s">%s</span>' % (kind, E(text))


def render(spec):
    criteria, weight_note, scored, close = rank(spec)
    auto = bool(spec.get("auto_choose"))
    title = spec.get("title", "A decision")
    parts = []
    A = parts.append

    A('<title>%s</title>' % E(title))
    # The one line that makes the screen legible on a phone: without it a
    # mobile browser renders the desktop width and the founder reads 6px
    # text. Found by the founder on his own phone, 2026-08-30.
    A('<meta name="viewport" content="width=device-width, initial-scale=1">')
    A(STYLE)

    A('<header class="top">')
    A('<p class="eyebrow">%s</p>' % E(spec.get("eyebrow", "Decision")))
    A('<h1>%s</h1>' % E(title))
    A('<p class="stamp">%s</p>' % E(spec.get("stamp", "")))
    A('</header>')

    # PLAIN LANGUAGE FIRST. The founder is not an engineer, and a page that
    # opens on a comparison table has already lost the reader it was built for.
    A('<section class="plain">')
    A('<h2>In plain words</h2>')
    A('<p class="lede">%s</p>' % E(spec.get("plain_summary", "")))
    if spec.get("question"):
        A('<p class="question"><strong>The question:</strong> %s</p>'
          % E(spec["question"]))
    A('</section>')

    # OPTIONAL PLAIN SECTIONS (E75). A spec may carry `sections`: a heading,
    # a list of already-rendered lines, and the sentence an empty one prints
    # instead of disappearing. A screen with no `sections` key renders
    # exactly as it did before, so this adds a shape rather than a rule.
    for section in spec.get("sections") or []:
        A('<section class="listing"><h2>%s</h2>'
          % E(section.get("heading", "")))
        items = section.get("items") or []
        if items:
            A('<ul>')
            for item in items:
                A('<li>%s</li>' % E(item))
            A('</ul>')
        else:
            A('<p class="note">%s</p>'
              % E(section.get("empty", "Nothing in this run.")))
        A('</section>')

    decided = spec.get("decided") or {}
    if decided:
        A('<section class="decided"><h2>Decided</h2>')
        A('<p class="lede"><strong>%s</strong> chose <strong>%s</strong>%s.</p>'
          % (E(decided.get("by", "The owner")), E(decided.get("choice_name", "")),
             (" on " + E(decided.get("at", ""))) if decided.get("at") else ""))
        if decided.get("words"):
            A('<blockquote>%s</blockquote>' % E(decided["words"]))
        A('<p>%s</p></section>'
          % E("Everything below is the material the choice was made on, kept so "
              "the decision can be re-examined rather than merely remembered. "
              "The ranking is left exactly as it was; a decision record that "
              "quietly agrees with the decision afterwards is worth nothing."))

    if auto:
        A('<section class="auto"><h2>Chosen automatically</h2><p>%s</p></section>'
          % E("You told this estate to make choices of this kind without asking, "
              "following the same pattern. The choice below was made that way. "
              "Everything a decision screen would have shown you is still here, "
              "so the decision can be checked or reversed rather than merely "
              "trusted."))

    # THE RANKING. Computed above, printed here, with its arithmetic reachable.
    A('<section><h2>The options, ranked</h2>')
    if weight_note:
        A('<p class="note">%s</p>' % E(weight_note))
    A('<div class="scroller"><table class="rankings">')
    A('<thead><tr><th>Option</th><th>What it is</th>'
      '<th class="num">Weighted score</th><th>Cost</th><th>Reversible</th>'
      '</tr></thead><tbody>')
    for i, s in enumerate(scored):
        o = s["option"]
        lead = ' class="lead"' if i == 0 else ''
        A('<tr%s><th scope="row"><a href="#opt-%s">%s</a>%s</th>'
          '<td>%s</td><td class="num big">%.2f</td><td>%s</td><td>%s</td></tr>'
          % (lead, E(o.get("id", str(i))), E(o.get("name", "")),
             (' ' + _pill("highest", "good")) if i == 0 else '',
             E(o.get("one_liner", "")), s["total"],
             E(o.get("cost", NODATA)), E(o.get("reversible", NODATA))))
    A('</tbody></table></div>')

    if close and len(scored) > 1:
        A('<p class="warn"><strong>This ranking does not separate the top two.</strong> '
          '%s and %s sit %.2f apart on a ten point scale, which is inside the '
          'margin of anybody\'s honest guess at a raw mark. Read both, then '
          'decide on the grounds the numbers cannot hold.</p>'
          % (E(scored[0]["option"].get("name", "")),
             E(scored[1]["option"].get("name", "")),
             scored[0]["total"] - scored[1]["total"]))
    else:
        A('<p class="rec"><strong>The arithmetic favours %s</strong>, by %.2f over '
          'the next. A score ranks options against criteria somebody chose; it '
          'does not know what you know. The choice is yours.</p>'
          % (E(scored[0]["option"].get("name", "")) if scored else NODATA,
             (scored[0]["total"] - scored[1]["total"]) if len(scored) > 1 else 0.0))
    A('</section>')

    # WHAT WAS BEING MEASURED. A score is unreadable without its criteria.
    A('<section><h2>What was being measured, and why it counts for that much</h2>')
    A('<div class="scroller"><table><thead><tr><th>Criterion</th>'
      '<th class="num">Weight</th><th>Why it carries that weight</th></tr>'
      '</thead><tbody>')
    for c in criteria:
        A('<tr><th scope="row">%s</th><td class="num">%.0f%%</td><td>%s</td></tr>'
          % (E(c.get("label", c.get("key", ""))), c["weight"] * 100,
             E(c.get("why", ""))))
    A('</tbody></table></div></section>')

    chosen_id = decided.get("choice")
    for i, s in enumerate(scored):
        s["option"]["_chosen"] = (s["option"].get("id") == chosen_id)
        A(render_option(s, i == 0))

    if spec.get("would_change"):
        A('<section class="flip"><h2>What would change this answer</h2><ul>')
        for w in spec["would_change"]:
            A('<li>%s</li>' % E(w))
        A('</ul></section>')

    A('<footer><p>%s</p><p class="stamp">%s</p></footer>'
      % (E(spec.get("footer", "")), E(spec.get("stamp", ""))))
    return "\n".join(parts)


def render_option(s, is_lead):
    o = s["option"]
    parts = []
    A = parts.append
    A('<section class="option%s" id="opt-%s">'
      % (" lead" if is_lead else "", E(o.get("id", ""))))
    A('<div class="ohead"><h2>%s%s</h2><span class="total">%.2f</span></div>'
      % (E(o.get("name", "")),
         ' <span class="pill chosen">chosen</span>' if o.get("_chosen") else '',
         s["total"]))
    A('<p class="lede">%s</p>' % E(o.get("one_liner", "")))

    if o.get("flow_mermaid"):
        A('<h3>How it works</h3>')
        A('<div class="scroller"><pre class="mermaid">%s</pre></div>'
          % E(o["flow_mermaid"]))
        A('<p class="where">flow source shown as text; render it with any '
          'mermaid viewer</p>')

    A('<div class="two">')
    A('<div class="pros"><h3>For</h3><ul>')
    for p in o.get("pros") or []:
        A('<li>%s</li>' % E(p))
    A('</ul></div>')
    A('<div class="cons"><h3>Against</h3><ul>')
    for c in o.get("cons") or []:
        A('<li>%s</li>' % E(c))
    A('</ul></div>')
    A('</div>')

    # THE ARITHMETIC, reachable but folded. The BA reads the total, the
    # engineer opens the multiplication and argues with a specific number.
    A('<details><summary>The score, multiplied out</summary>')
    A('<div class="scroller"><table><thead><tr><th>Criterion</th>'
      '<th class="num">Weight</th><th class="num">Mark</th>'
      '<th class="num">Contributes</th><th>Why that mark</th></tr></thead><tbody>')
    for r in s["rows"]:
        mark = ("%.0f" % r["mark"]) if r["mark"] is not None else NODATA
        A('<tr><th scope="row">%s</th><td class="num">%.0f%%</td>'
          '<td class="num">%s</td><td class="num">%.2f</td><td>%s</td></tr>'
          % (E(r["label"]), r["weight"] * 100, E(mark), r["product"],
             E(r["why"])))
    A('</tbody><tfoot><tr><th scope="row">Total</th><td></td><td></td>'
      '<td class="num big">%.2f</td><td></td></tr></tfoot></table></div>'
      % s["total"])
    if s["unmarked"]:
        A('<p class="warn">%s: this option was never marked on %s, so those '
          'criteria contribute nothing. An unmarked criterion is not a zero and '
          'is not a middling score, and pretending otherwise would invent an '
          'opinion nobody held.</p>'
          % (NODATA, E(", ".join(s["unmarked"]))))
    A('</details>')

    if o.get("sources"):
        A('<h3>Where this comes from</h3><ul class="sources">')
        for src in o["sources"]:
            A('<li><strong>%s</strong><br><span class="where">%s</span>'
              % (E(src.get("what", "")), E(src.get("where", ""))))
            if src.get("found_in"):
                A('<br><code>%s</code>' % E(src["found_in"]))
            A('</li>')
        A('</ul>')

    if o.get("repos") or o.get("docs") or o.get("examples"):
        A('<details class="eng"><summary>For engineers: where this is done '
          'elsewhere, and how to write it</summary>')
        if o.get("repos"):
            A('<h4>The projects this was learned from</h4><ul class="links">')
            for r in o["repos"]:
                A('<li><a href="%s" rel="noreferrer">%s</a> '
                  '<span class="where">%s</span>%s</li>'
                  % (E(r.get("url", "")), E(r.get("name", "")),
                     E(r.get("what", "")),
                     ('<br><span class="checked">link resolved %s</span>'
                      % E(r["checked"])) if r.get("checked") else
                     '<br><span class="checked">%s: this link was not checked</span>'
                     % NODATA))
            A('</ul>')
        if o.get("docs"):
            A('<h4>Further reading</h4><ul class="links">')
            for r in o["docs"]:
                A('<li><a href="%s" rel="noreferrer">%s</a> '
                  '<span class="where">%s</span>%s</li>'
                  % (E(r.get("url", "")), E(r.get("title", "")),
                     E(r.get("what", "")),
                     ('<br><span class="checked">link resolved %s</span>'
                      % E(r["checked"])) if r.get("checked") else
                     '<br><span class="checked">%s: this link was not checked</span>'
                     % NODATA))
            A('</ul>')
        for ex in o.get("examples") or []:
            A('<h4>%s</h4>' % E(ex.get("title", "Example")))
            if ex.get("what"):
                A('<p class="where">%s</p>' % E(ex["what"]))
            A('<div class="scroller"><pre class="code">%s</pre></div>'
              % E(ex.get("code", "")))
        A('</details>')

    if o.get("code"):
        A('<h3>The code in this estate</h3>')
        for spec_c in o["code"]:
            text, note = excerpt(spec_c)
            A('<details><summary><code>%s</code>%s%s</summary>'
              % (E(spec_c.get("path", "")),
                 (' lines ' + E(spec_c.get("lines", ""))) if spec_c.get("lines") else '',
                 ' ' + E(spec_c.get("why", ""))))
            if text is None:
                A('<p class="warn">%s</p>' % E(note))
            else:
                A('<div class="scroller"><pre class="code">%s</pre></div>'
                  % E(text))
            A('</details>')
    A('</section>')
    return "\n".join(parts)


STYLE = """<style>
:root{
  --paper:#F7F8F6; --ink:#141B22; --muted:#5A6570; --line:#DDE2DE;
  --petrol:#0E7A6F; --petrol-soft:#E4F1EE; --card:#FFFFFF;
  --good:#0E7A6F; --warn:#8A5A00; --warn-soft:#FBF2DE;
}
:root:not([data-theme="light"]){}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --paper:#10161B; --ink:#E8EDEA; --muted:#96A2A8; --line:#26313A;
  --petrol:#3AA893; --petrol-soft:#152A28; --card:#161E25;
  --good:#3AA893; --warn:#D6A44A; --warn-soft:#241D10;
}}
:root[data-theme="dark"]{
  --paper:#10161B; --ink:#E8EDEA; --muted:#96A2A8; --line:#26313A;
  --petrol:#3AA893; --petrol-soft:#152A28; --card:#161E25;
  --good:#3AA893; --warn:#D6A44A; --warn-soft:#241D10;
}
*{box-sizing:border-box}
body{background:var(--paper); color:var(--ink); margin:0;
  font-family:Seravek,"Gill Sans Nova",Ubuntu,Calibri,"DejaVu Sans",source-sans-pro,sans-serif;
  font-size:16.5px; line-height:1.62; padding:0 20px 80px;}
.top,section,footer{max-width:53rem; margin:0 auto;}
h1,h2,h3{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  font-weight:600; text-wrap:balance; letter-spacing:-.01em;}
h1{font-size:2.35rem; line-height:1.15; margin:.1rem 0 .4rem;}
h2{font-size:1.5rem; margin:0 0 .6rem;}
h3{font-size:1.08rem; margin:1.4rem 0 .4rem;}
.top{padding:3rem 0 1.2rem; border-bottom:1px solid var(--line); margin-bottom:2rem;}
.eyebrow{text-transform:uppercase; letter-spacing:.14em; font-size:.72rem;
  color:var(--petrol); font-weight:600; margin:0;}
.stamp{color:var(--muted); font-size:.82rem; margin:.3rem 0 0;}
section{margin:0 auto 2.6rem;}
.lede{font-size:1.06rem;}
.plain{background:var(--petrol-soft); border-left:3px solid var(--petrol);
  padding:1.1rem 1.3rem; border-radius:2px; max-width:53rem;}
.plain h2{font-size:1.15rem;}
.question{margin-bottom:0;}
.auto{background:var(--warn-soft); border-left:3px solid var(--warn);
  padding:1rem 1.3rem; border-radius:2px;}
.scroller{overflow-x:auto; -webkit-overflow-scrolling:touch;}
table{border-collapse:collapse; width:100%; font-size:.93rem; min-width:34rem;}
th,td{text-align:left; padding:.5rem .7rem; border-bottom:1px solid var(--line);
  vertical-align:top;}
thead th{font-size:.74rem; text-transform:uppercase; letter-spacing:.09em;
  color:var(--muted); font-weight:600;}
.num{text-align:right; font-variant-numeric:tabular-nums;}
.big{font-weight:600; font-size:1.02rem;}
tr.lead th,tr.lead td{background:var(--petrol-soft);}
tfoot th,tfoot td{border-bottom:none; border-top:2px solid var(--line);}
.pill{display:inline-block; font-size:.66rem; text-transform:uppercase;
  letter-spacing:.08em; padding:.1rem .45rem; border-radius:2px;
  border:1px solid var(--line); color:var(--muted); vertical-align:middle;}
.pill.good{color:var(--good); border-color:var(--good);}
.pill.chosen{color:#fff; background:var(--petrol); border-color:var(--petrol);}
.decided{background:var(--petrol); color:#fff; padding:1.2rem 1.4rem;
  border-radius:3px;}
.decided h2,.decided a{color:#fff;} .decided .lede{margin-top:0;}
.decided blockquote{margin:.7rem 0; padding-left:1rem;
  border-left:2px solid rgba(255,255,255,.5); font-style:italic;}
h4{font-family:"Iowan Old Style",Georgia,serif; font-size:1rem; margin:1rem 0 .3rem;}
ul.links li{margin:.55rem 0;}
.checked{font-size:.78rem; color:var(--muted); font-variant-numeric:tabular-nums;}
details.eng{border-top:2px solid var(--petrol); padding-top:.6rem;}
.rec,.note,.warn{padding:.8rem 1rem; border-radius:2px; font-size:.95rem;}
.rec{background:var(--petrol-soft); border-left:3px solid var(--petrol);}
.note{color:var(--muted); padding-left:0;}
.warn{background:var(--warn-soft); border-left:3px solid var(--warn);}
.option{border:1px solid var(--line); background:var(--card);
  border-radius:3px; padding:1.4rem 1.5rem;}
.option.lead{border-color:var(--petrol); border-width:1.5px;}
.ohead{display:flex; align-items:baseline; justify-content:space-between; gap:1rem;}
.ohead h2{margin:0;}
.total{font-family:"Iowan Old Style",Georgia,serif; font-size:1.9rem;
  color:var(--petrol); font-variant-numeric:tabular-nums;}
.two{display:grid; grid-template-columns:1fr 1fr; gap:1.2rem;}
@media (max-width:640px){.two{grid-template-columns:1fr;}}
.pros h3{color:var(--good);} .cons h3{color:var(--warn);}
ul{padding-left:1.1rem; margin:.3rem 0;} li{margin:.3rem 0;}
ul.sources li{margin:.7rem 0;} .where{color:var(--muted); font-size:.9rem;}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em;}
pre.code{background:var(--paper); border:1px solid var(--line);
  padding:.8rem 1rem; border-radius:2px; font-size:.8rem; line-height:1.5;
  overflow-x:auto; margin:.5rem 0;}
pre.mermaid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.8rem; line-height:1.5; background:var(--paper);
  border:1px solid var(--line); padding:.8rem 1rem; border-radius:2px;
  overflow-x:auto; text-align:left; margin:.5rem 0;}
details{margin:.7rem 0; border-top:1px solid var(--line); padding-top:.5rem;}
summary{cursor:pointer; font-size:.92rem; color:var(--petrol); font-weight:600;}
summary:focus-visible{outline:2px solid var(--petrol); outline-offset:2px;}
footer{border-top:1px solid var(--line); padding-top:1.2rem; margin-top:3rem;
  color:var(--muted); font-size:.88rem;}
a{color:var(--petrol);}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", help="the decision, as JSON")
    ap.add_argument("-o", "--out", help="where to write the HTML")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: the decision could not be read: %s" % (NODATA, exc),
              file=sys.stderr)
        return 2

    body = render(spec)
    out = args.out or os.path.splitext(args.spec)[0] + ".html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    # THE ENFORCEMENT SENTINEL. The intake gate hook refuses a founder-facing
    # question when no decision screen was rendered recently; this stamp is
    # how the hook knows one was. Founder order 2026-08-30: "it should be
    # enforced", after the eighth screen-less decision popup.
    try:
        sentinel = brother_paths.config_path("last-decision-screen.json")
        with open(sentinel, "w", encoding="utf-8") as fh:
            json.dump({"path": os.path.abspath(out),
                       "title": spec.get("title", ""),
                       "written_at_epoch": int(__import__("time").time())}, fh)
    except OSError as exc:
        print("decide: could not stamp the intake sentinel: %s" % exc,
              file=sys.stderr)
    _c, _n, scored, close = rank(spec)
    print("wrote %s: %d option(s), top is %s at %.2f%s"
          % (out, len(scored),
             scored[0]["option"].get("name", "?") if scored else NODATA,
             scored[0]["total"] if scored else 0.0,
             ", and the ranking does not separate the top two" if close else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

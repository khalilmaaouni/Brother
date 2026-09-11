"""decide_round: a ROUND of decisions is still a screen, never a wall of popups.

FOUNDER CORRECTION 2026-09-10, scored 0 out of 5, his words: "this is not the
intake tool, it should be weighted and compare different options, show me
sources and pros and cons and recommendations before offering me to chose".

WHAT WENT WRONG, and it is a tool gap rather than only a session slip. decide.py
renders exactly ONE decision per spec. A session facing FOUR decisions at once
therefore has no way to screen them: it renders one, and the other three arrive
as prose in a popup. The intake gate cannot catch this, because it checks that a
screen exists and says plainly in its own docstring that it cannot judge whether
the screen matches the question.

WHY THIS IS A COMPOSER AND NOT A REWRITE. decide.py owns the things that must not
be duplicated: the weights, the multiplication, the close-call rule, the code
excerpts read live from real files, the source list. A second renderer would be a
second opinion about a computed score. So this calls decide.py once per decision,
takes the body it produced, and assembles one page with a contents strip. The
arithmetic stays where it already lives.

Every decision keeps its own criteria and weights on purpose: a round is not one
question with four answers, and forcing four decisions onto one weight vector
would invent a comparison nobody made.

Spec: {"title", "eyebrow", "stamp", "plain_summary", "decisions": [<decide spec>, ...]}
Each entry is an ordinary decide.py spec. Renders one self contained HTML file and
stamps the intake sentinel once for the round.

Python 3, standard library only. No network.
"""
import argparse
import html
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decide  # noqa: E402
NODATA = "NO-DATA"
E = html.escape

#: The round's only new styling. It borrows decide.py's variables rather than
#: inventing colours, so the round cannot drift from the single decision screen.
ROUND_STYLE = """<style>
.round-toc{max-width:53rem; margin:1rem auto 0; padding:0; list-style:none;}
.round-toc li{margin:0 0 .45rem;}
.round-toc .n{display:inline-block; min-width:1.7rem; opacity:.55;}
.round-note{max-width:53rem; margin:1rem auto 0; font-size:.95rem; opacity:.8;}
.decision-block{border-top:1px solid var(--line); margin-top:3rem;}
.decision-head{max-width:53rem; margin:0 auto; padding:2.2rem 0 1rem;}
.decision-head h2{font-size:1.9rem; line-height:1.2; margin:.1rem 0 .4rem;}
</style>"""


def render(spec):
    """(page, note). One screen holding several decisions, one masthead only.

    IT CALLS THE RENDERER, IT DOES NOT SCRAPE IT. An earlier version ran
    decide.py as a subprocess and cut each page at its last stylesheet, which
    stapled four complete documents together: four mastheads, four "In plain
    words" blocks, four h1 elements. That is what broke the template. Fragment
    mode exists in decide.py for exactly this, so the round emits the shell once
    and asks for sections.
    """
    decisions = spec.get("decisions") or []
    if not decisions:
        return None, "%s: the round holds no decisions" % NODATA

    contents, blocks, headlines = [], [], []
    for i, d in enumerate(decisions, 1):
        anchor = "d%d" % i
        contents.append('<li><a href="#%s"><span class="n">%d</span> %s</a></li>'
                        % (anchor, i, E(d.get("title", "A decision"))))
        blocks.append('<article id="%s" class="decision-block">%s</article>'
                      % (anchor, decide.render(d, fragment=True)))
        try:
            _c, _n, scored, close = decide.rank(d)
            if scored:
                headlines.append(
                    "%s: top is %s at %.2f%s"
                    % (d.get("title", "decision %d" % i),
                       scored[0]["option"].get("name", "?"), scored[0]["total"],
                       ", and the ranking does not separate the top two"
                       if close else ""))
        except Exception as exc:  # noqa: BLE001
            # sbe: allow-silent a headline is a convenience line, and losing it
            # must never cost the page it summarises
            headlines.append("%s: %s (%s)" % (d.get("title", "?"), NODATA, exc))

    out = ['<title>%s</title>' % E(spec.get("title", "A round of decisions")),
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           decide.STYLE,
           ROUND_STYLE,
           '<header class="top">',
           '<p class="eyebrow">%s</p>' % E(spec.get("eyebrow", "Decisions")),
           '<h1>%s</h1>' % E(spec.get("title", "A round of decisions")),
           '<p class="stamp">%s</p>' % E(spec.get("stamp", "")),
           '</header>',
           '<section class="plain">',
           '<h2>In plain words</h2>',
           '<p class="lede">%s</p>' % E(spec.get("plain_summary", "")),
           '<ol class="round-toc">%s</ol>' % "".join(contents),
           '<p class="round-note">Each decision below keeps its own criteria '
           'and weights, because a round is not one question with several '
           'answers. Every score is multiplied from the weights shown, never '
           'typed.</p>',
           '</section>']
    out.extend(blocks)
    return "\n".join(out), "\n".join(headlines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", help="the round, as JSON")
    ap.add_argument("-o", "--out", help="where to write the HTML")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: the round could not be read: %s" % (NODATA, exc),
              file=sys.stderr)
        return 2
    body, note = render(spec)
    if body is None:
        print(note, file=sys.stderr)
        return 2
    out = args.out or os.path.splitext(args.spec)[0] + ".html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    # ONE stamp for the round. decide.py stamped once per decision as it went;
    # rewriting it here means the sentinel names the page the founder actually
    # reads rather than the last fragment rendered.
    try:
        # OVERRIDABLE SO A TEST DOES NOT WRITE THE REAL ONE. Without this the
        # suite overwrites the founder's live intake stamp with a temp path,
        # which is a side effect on machine state from running tests, and it
        # can leave the gate pointing at a page nobody will ever open.
        sentinel = os.environ.get(
            "BROTHER_DECISION_SENTINEL",
            os.path.expanduser("~/.claude/last-decision-screen.json"))
        with open(sentinel, "w", encoding="utf-8") as fh:
            json.dump({"path": os.path.abspath(out),
                       "title": spec.get("title", ""),
                       "written_at_epoch": int(time.time())}, fh)
    except OSError as exc:
        print("decide_round: could not stamp the intake sentinel: %s" % exc,
              file=sys.stderr)
    print("wrote %s: %d decision(s)" % (out, len(spec.get("decisions") or [])))
    if note:
        print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())

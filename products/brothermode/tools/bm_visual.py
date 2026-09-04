"""bm_visual: the drawn vocabulary of BrotherMode's visual surface.

Design: docs/program/absolute-lead/DESIGN-visual-surface.md, sections 6
(the insight box and the alert ladder), 7 (the diagram vocabulary), 9.2
(the failure block) and 12.1 (this inventory).

WHAT THIS FILE IS. One vocabulary, two renderings. Seven shapes, four
alert rungs, one six slot insight box, one derived alert function and one
map that rewrites every refusal the engine can emit into words a founder
can act on. Everything here is a pure function of rows that already
exist: there is no picture anybody maintains by hand, and there is no
alerts table, because a stored alert is a lie the moment its condition
clears.

Loop after v2.1.0 (docs/program/absolute-lead/DESIGN-progress-surface.md)
added the sixth shape, timeline, and progress_facts, the one pure reader
that feeds the progress page section: still rows in, plain facts out,
still no second truth. Phase 5 (founder decision 2026-08-08) added the
seventh, gantt, and the progress view that draws it.

The count in the paragraph above is the one thing on this page nothing
computes. It said six while SHAPES already held seven, for the same
reason references/visual-surface.md said five: a widened tuple leaves
prose behind. tools/test_bm_docs.py now reads this docstring against
SHAPES, so the third time is caught rather than shipped.

WHAT THIS FILE IS NOT. It is not a command: it has no main, it opens no
store of its own, it writes no file and it prints nothing. Every entry
point either takes an already-open store handle or takes plain data. The
consent gate therefore lives where the commands live (tools/bm_view.py),
and this module cannot break it because it can never obtain a handle by
itself.

THE FOUR LAWS THIS FILE ENFORCES MECHANICALLY, rather than by prose:

  L-S4  Colour is the third carrier. Every status is recoverable from a
        WORD first, a non colour mark second, and colour third. to_svg
        emits no colour attribute at all; every colour in the product
        lives in THEME_CSS, so the test that deletes that block and
        asserts every status is still readable is trivial rather than
        aspirational.
  L-S5  One vocabulary, two renderings. Every shape renders as plain
        ASCII for a terminal and as inline SVG for the page, and the two
        carry the same status words. A concept that works in only one of
        the two does not enter the vocabulary.
  L-S6  Alerts are derived, never stored. alerts_now is a pure, ordered,
        deduplicated, capped function over rows.
  L-S9  A refusal never reaches the founder unrewritten. REFUSAL_HELP has
        an entry for every reason code tools/bm_store.py can emit AND for
        every one tools/bm_controller.py's unattended preflight refuses
        with, and a test enumerates them from those two files' own
        sources.

Python 3.9, standard library only. No network, at render time or ever.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import datetime
import importlib.util
import os
import re
import sys
import xml.sax.saxutils as saxutils

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    """Load a sibling module by PATH, off this file's own directory, the
    same technique tools/bm_lead.py, tools/bm_docs.py and
    tools/bm_controller.py use: a hostile sys.path cannot shadow bm_store
    this way."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
# bm_lead is imported and never re-implemented. Two founder-facing
# promises live there and there is exactly one copy of each:
# HANDBACK_OPTION_TEXT (slot 6 of the insight box, byte equal, never
# paraphrased) and evidence_label (the four calibration labels, applied by
# the RENDERER so a claim that was only reasoned about cannot reach any
# surface dressed as a settled fact).
bl = _load("bm_lead")
# bm_learning carries safe_display and plain_dashes, the two sanitisers
# every store-derived string passes through before it reaches a document.
# Same import tools/bm_docs.py makes, for the same reason.
L = _load("bm_learning")
# bm_messages is the RF-4 (GAP-19) catalog for founder-facing strings more
# than one renderer needs word for word: constants only, no I/O, no
# subprocess, no writes, so loading it here carries none of the cost that
# loading tools/bm_controller.py directly would (see REFUSAL_HELP below).
#
# Loaded through _load_messages(), not the generic _load() above: this
# file and tools/bm_controller.py each load bm_messages.py independently,
# and the generic _load() re-executes a module fresh on every call (the
# same "class-identity trap" tools/bm_project.py documents for bm_store),
# so two independent plain _load("bm_messages") calls would never produce
# `is`-equal objects even though both come from the same source file.
# tools/test_bm_visual.py's RF-4 identity check needs them to be the SAME
# object, so both this file and tools/bm_controller.py cache the load in
# sys.modules under the one key "bm_messages" and reuse whichever loaded
# first. Safe to cache, unlike bm_store: this module is constants only, no
# state a fresh reload would ever need to reset.
def _load_messages():
    cached = sys.modules.get("bm_messages")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "bm_messages", os.path.join(HERE, "bm_messages.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["bm_messages"] = mod
    return mod


M = _load_messages()


# ---------------------------------------------------------------------------
# 1. THE MEASURED COLOUR TOKENS
#
# LENS-C section 5.6's measured tables, both themes, verbatim. Every pair
# below was computed with the WCAG relative luminance and contrast ratio
# formulas, and tools/test_bm_visual.py re-runs that computation over the
# shipped tables on every run, including the two sanity values (21.0 for
# black on white, 4.54 for #767676 on white) so a broken formula cannot
# pass silently.
#
# Hue anchors are the Colour Universal Design ones: vermilion for the stop
# rung rather than pure red, because dark red reads as black to
# protanopes; bluish green rather than green; blue and sky blue separated
# by brightness. The rungs alternate warm and cool going down the ladder.
# ---------------------------------------------------------------------------

TOKENS_LIGHT = {
    "page": "#FFFFFF",
    "body": "#1A1D21",
    "muted": "#575E68",
    "needs-you-ink": "#8C2D00",
    "needs-you-rule": "#D55E00",
    "needs-you-tint": "#FDEFE8",
    "at-risk-ink": "#6B4400",
    "at-risk-rule": "#A06A00",
    "at-risk-tint": "#FFF6E5",
    "for-info-ink": "#00436B",
    "for-info-rule": "#0072B2",
    "for-info-tint": "#EAF3FA",
    "settled-ink": "#00543A",
    "settled-rule": "#009E73",
    "settled-tint": "#E8F6F0",
    "idle-ink": "#3F4650",
    "idle-rule": "#767B84",
    "idle-tint": "#F1F2F4",
    "node-ink": "#1A1D21",
    "node-rule": "#5B6470",
    "node-tint": "#EFF1F4",
    # THE PROGRESS SURFACE (Phase 5, founder decision 2026-08-08). Petrol
    # on paper is the house design language for progress pages; the four
    # status colours above are NOT touched, because one accent cannot
    # carry four meanings and a colourblind reader has to be able to tell
    # blocked from done. So: accent decorates, status still signifies.
    # Measured with this module's own contrast_ratio, not assumed: 4.89
    # here and 5.95 in the dark table, both above the 4.5 floor and both
    # ahead of settled-rule's 3.42.
    "accent": "#0E7A6F",
    "accent-tint": "#E4EFED",
    "paper": "#F7F8F6",
}

TOKENS_DARK = {
    "page": "#12151A",
    "body": "#E9ECF1",
    "muted": "#A7AEB9",
    "needs-you-ink": "#FFA582",
    "needs-you-rule": "#FF7A47",
    "needs-you-tint": "#2A1710",
    "at-risk-ink": "#F0B95A",
    "at-risk-rule": "#E69F00",
    "at-risk-tint": "#2A2110",
    "for-info-ink": "#7FC4F5",
    "for-info-rule": "#56B4E9",
    "for-info-tint": "#0F1E2A",
    "settled-ink": "#5FD3AE",
    "settled-rule": "#3FBF97",
    "settled-tint": "#0E2018",
    "idle-ink": "#A7AEB9",
    "idle-rule": "#767B84",
    # LENS-C's dark table records no idle tint (it says n/a), but a token
    # defined in one theme and missing in the other cannot be used by a
    # page that must render in both, so the dark neutral node fill it DOES
    # measure (#1E242C) serves as the idle tint too. The pair that creates
    # (idle ink on it) is in TOKEN_FLOORS below and is checked on every
    # run like every other pair, rather than being asserted here.
    "idle-tint": "#1E242C",
    "node-ink": "#E9ECF1",
    "node-rule": "#8A93A0",
    "node-tint": "#1E242C",
    # The progress surface, dark side. Petrol lifted for slate, and slate
    # itself as the paper: same pairing, same measurement (5.95).
    "accent": "#3AA893",
    "accent-tint": "#10241F",
    "paper": "#141B22",
}

# (theme, foreground token, background token, WCAG floor). 4.5:1 for text,
# 3:1 for a border or a glyph (WCAG 1.4.3 and 1.4.11).
TOKEN_FLOORS = (
    ("light", "body", "page", 4.5),
    ("light", "muted", "page", 4.5),
    ("light", "needs-you-ink", "needs-you-tint", 4.5),
    ("light", "needs-you-ink", "page", 4.5),
    ("light", "needs-you-rule", "needs-you-tint", 3.0),
    ("light", "needs-you-rule", "page", 3.0),
    ("light", "at-risk-ink", "at-risk-tint", 4.5),
    ("light", "at-risk-ink", "page", 4.5),
    ("light", "at-risk-rule", "at-risk-tint", 3.0),
    ("light", "at-risk-rule", "page", 3.0),
    ("light", "for-info-ink", "for-info-tint", 4.5),
    ("light", "for-info-ink", "page", 4.5),
    ("light", "for-info-rule", "for-info-tint", 3.0),
    ("light", "for-info-rule", "page", 3.0),
    ("light", "settled-ink", "settled-tint", 4.5),
    ("light", "settled-ink", "page", 4.5),
    ("light", "settled-rule", "settled-tint", 3.0),
    ("light", "settled-rule", "page", 3.0),
    ("light", "idle-ink", "idle-tint", 4.5),
    ("light", "idle-rule", "page", 3.0),
    ("light", "node-ink", "node-tint", 4.5),
    ("light", "node-rule", "page", 3.0),
    ("dark", "body", "page", 4.5),
    ("dark", "muted", "page", 4.5),
    ("dark", "needs-you-ink", "needs-you-tint", 4.5),
    ("dark", "needs-you-ink", "page", 4.5),
    ("dark", "needs-you-rule", "page", 3.0),
    ("dark", "at-risk-ink", "at-risk-tint", 4.5),
    ("dark", "at-risk-ink", "page", 4.5),
    ("dark", "at-risk-rule", "page", 3.0),
    ("dark", "for-info-ink", "for-info-tint", 4.5),
    ("dark", "for-info-ink", "page", 4.5),
    ("dark", "for-info-rule", "page", 3.0),
    ("dark", "settled-ink", "settled-tint", 4.5),
    ("dark", "settled-ink", "page", 4.5),
    ("dark", "settled-rule", "page", 3.0),
    ("dark", "idle-ink", "page", 4.5),
    ("dark", "idle-ink", "idle-tint", 4.5),
    ("dark", "idle-rule", "page", 3.0),
    ("dark", "node-ink", "node-tint", 4.5),
    ("dark", "node-rule", "page", 3.0),
)


def relative_luminance(hexstr):
    """WCAG 2.x relative luminance of an #RRGGBB colour.
    https://www.w3.org/TR/WCAG22/#dfn-relative-luminance"""
    h = (hexstr or "").lstrip("#")
    if len(h) != 6:
        raise ValueError("a colour must be six hex digits, got %r"
                         % (hexstr,))
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255.0
        channels.append(c / 12.92 if c <= 0.04045
                        else ((c + 0.055) / 1.055) ** 2.4)
    return (0.2126 * channels[0] + 0.7152 * channels[1]
            + 0.0722 * channels[2])


def contrast_ratio(a, b):
    """WCAG 2.x contrast ratio between two #RRGGBB colours, UNROUNDED.
    The specification is explicit that a computed value must not be
    rounded before it is compared against a floor, and the light settled
    rule clears 3:1 by 0.07, so rounding here would turn a real failure
    into a pass."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _css_vars(tokens):
    return "\n".join("  --bm-%s: %s;" % (name, tokens[name])
                     for name in sorted(tokens))


# THE ONE STYLESHEET BLOCK, and the only colour in the product. This is
# CSS TEXT, not an element: the page that embeds it owns the style tag, so
# a test can delete this exact string from a rendered document and assert
# every status is still readable.
#
# Order matters and is load bearing. The media query states the viewer's
# system preference; the two data-theme rules come AFTER it so the
# viewer's own toggle wins in both directions.
THEME_CSS = """
:root {
%s
}
@media (prefers-color-scheme: dark) {
  :root {
%s
  }
}
:root[data-theme="light"] {
%s
}
:root[data-theme="dark"] {
%s
}
body { background: var(--bm-page); color: var(--bm-body); }
.bm-muted { color: var(--bm-muted); }
.bm-figure { overflow-x: auto; margin: 0 0 1.5rem 0; }
.bm-node { fill: var(--bm-node-tint); stroke: var(--bm-node-rule); }
.bm-node-label { fill: var(--bm-node-ink); }
.bm-now { stroke-width: 3; fill: var(--bm-for-info-tint);
          stroke: var(--bm-for-info-rule); }
.bm-complete { fill: var(--bm-settled-tint); stroke: var(--bm-settled-rule); }
.bm-waiting { fill: var(--bm-idle-tint); stroke: var(--bm-idle-rule); }
.bm-passed { fill: var(--bm-settled-tint); stroke: var(--bm-settled-rule); }
.bm-not-run { fill: var(--bm-idle-tint); stroke: var(--bm-idle-rule); }
.bm-open { fill: var(--bm-at-risk-tint); stroke: var(--bm-at-risk-rule); }
.bm-chosen { fill: var(--bm-idle-tint); stroke: var(--bm-idle-rule); }
.bm-counted { fill: var(--bm-idle-tint); stroke: var(--bm-idle-rule); }
.bm-working { fill: var(--bm-for-info-tint);
              stroke: var(--bm-for-info-rule); }
.bm-blocked { fill: var(--bm-needs-you-tint);
              stroke: var(--bm-needs-you-rule); stroke-width: 3; }
.bm-failing { fill: var(--bm-needs-you-tint);
              stroke: var(--bm-needs-you-rule); stroke-width: 3; }
.bm-handback { stroke-width: 3; fill: var(--bm-for-info-tint);
               stroke: var(--bm-for-info-rule); }
.bm-insight { border-left: 3px dotted var(--bm-for-info-rule);
              padding: .5rem .75rem; margin: 0 0 1rem 0; }
.bm-failure { border-left: 3px solid var(--bm-at-risk-rule);
              padding: .5rem .75rem; margin: 0 0 1rem 0; }
.bm-action { margin: .25rem 0 0 0; }
.bm-bar-track { fill: var(--bm-idle-tint); stroke: var(--bm-idle-rule); }
.bm-bar-fill { fill: var(--bm-for-info-tint);
               stroke: var(--bm-for-info-rule); }
.bm-phase-label { fill: var(--bm-accent); font-weight: 600; }
.bm-span { fill: var(--bm-accent); stroke: var(--bm-accent); }
.bm-tick-on { fill: var(--bm-accent); stroke: var(--bm-accent); }
.bm-tick-off { fill: var(--bm-accent-tint); stroke: var(--bm-idle-rule); }
.bm-progress-surface { background: var(--bm-paper); }
.bm-alert { padding: .5rem .75rem; margin: 0 0 .75rem 0; }
.bm-rung-bar { display: inline-block; width: 100%%; }
.bm-needs-you { border-left: 6px solid var(--bm-needs-you-rule);
                background: var(--bm-needs-you-tint);
                color: var(--bm-needs-you-ink); }
.bm-at-risk { border-left: 3px solid var(--bm-at-risk-rule);
              background: var(--bm-at-risk-tint);
              color: var(--bm-at-risk-ink); }
.bm-for-info { border-left: 3px dotted var(--bm-for-info-rule);
               background: var(--bm-for-info-tint);
               color: var(--bm-for-info-ink); }
.bm-settled { border-left: 0; color: var(--bm-muted); }
""" % (_css_vars(TOKENS_LIGHT), _css_vars(TOKENS_DARK),
       _css_vars(TOKENS_LIGHT), _css_vars(TOKENS_DARK))


# ---------------------------------------------------------------------------
# 2. THE ALERT LADDER: four rungs, and only the top one interrupts
# ---------------------------------------------------------------------------

RUNGS = ("NEEDS YOU", "AT RISK", "FOR INFO", "SETTLED")

# rung -> (delivery, aria role or empty, cap or None for no cap).
#
# DELIVERY FIRST, then name, then appearance. That ordering is the whole
# point: a rung is defined by what it DOES to the founder's attention, so
# "only the top rung interrupts" is a property of this table rather than a
# habit a renderer has to remember.
RUNG_DELIVERY = {
    "NEEDS YOU": ("interrupt", "alert", 1),
    "AT RISK": ("polite", "status", 3),
    "FOR INFO": ("silent", "", 6),
    "SETTLED": ("silent", "", None),
}

# The non colour mark, ASCII only. No box drawing characters and no
# emoji: this repository runs a Windows CI job, and non ASCII in terminal
# output is a class of failure this project has already paid for. Four
# discriminable marks before a single colour is applied.
RUNG_MARKS = {
    "NEEDS YOU": "[!!]",
    "AT RISK": "[! ]",
    "FOR INFO": "[ i]",
    "SETTLED": "[  ]",
}

# rung -> colour token prefix, or empty for no colour at all. SETTLED is
# deliberately uncoloured: GOV.UK moved completed items to plain text so
# colour draws the eye to what still needs action. Do not paint the done
# things green; spend the colour budget on the work.
RUNG_TOKEN = {
    "NEEDS YOU": "needs-you",
    "AT RISK": "at-risk",
    "FOR INFO": "for-info",
    "SETTLED": "",
}

RUNG_BAR_CLASS = "bm-rung-bar"

# Every condition that can become an alert, as a closed set. A condition
# that cannot be expressed as a query over rows is not an alert, and the
# constructor below refuses a kind that is not here, which is what makes
# "the rung is a query, never a feeling" mechanical.
ALERT_KINDS = (
    # NEEDS YOU: work is stopped until a human acts.
    "consent",
    "key-decision",
    "founder-step",
    "raised-alert",
    "budget-ceiling",
    # AT RISK: nothing is stopped, but something is likely to stop.
    "open-risk",
    "stale-dispatch",
    "budget-drift",
    "skipped-rows",
    # FOR INFO: true, worth knowing, requires nothing.
    "count",
    # SETTLED: a thing that was open is now closed.
    "closed-decision",
    "resolved-alert",
    "step-passed",
)

RUNG_FOR_KIND = {
    "consent": "NEEDS YOU",
    "key-decision": "NEEDS YOU",
    "founder-step": "NEEDS YOU",
    "raised-alert": "NEEDS YOU",
    "budget-ceiling": "NEEDS YOU",
    "open-risk": "AT RISK",
    "stale-dispatch": "AT RISK",
    "budget-drift": "AT RISK",
    "skipped-rows": "AT RISK",
    "count": "FOR INFO",
    "closed-decision": "SETTLED",
    "resolved-alert": "SETTLED",
    "step-passed": "SETTLED",
}


def _check_rung_map(kinds, rung_for_kind, rungs):
    """Raise when a kind has no rung, when a rung is not on the ladder, or
    when the map names a kind that does not exist. Called at import time
    against the shipped tables, and callable from a test with an invented
    kind so the guard itself is proven to fire. Shape copied from
    tools/bm_lead.py's _check_run_state_plain."""
    missing = sorted(set(kinds) - set(rung_for_kind))
    if missing:
        raise RuntimeError(
            "bm_visual: no rung for alert kind(s): %s. A condition with no "
            "rung would reach the founder at whatever level the last "
            "author felt like." % ", ".join(missing))
    extra = sorted(set(rung_for_kind) - set(kinds))
    if extra:
        raise RuntimeError(
            "bm_visual: rung map names kind(s) that do not exist: %s"
            % ", ".join(extra))
    wrong = sorted(k for k, r in rung_for_kind.items() if r not in rungs)
    if wrong:
        raise RuntimeError(
            "bm_visual: kind(s) mapped to a rung outside the ladder: %s"
            % ", ".join(wrong))
    return True


_check_rung_map(ALERT_KINDS, RUNG_FOR_KIND, RUNGS)

# The one number this file borrows from the engine. tools/bm_controller.py
# owns DEFAULT_DISPATCH_TIMEOUT_SECONDS; importing the whole controller
# into a pure vocabulary module to read one integer would be a heavy
# dependency for nothing, so the value is stated here and
# tools/test_bm_visual.py reads the controller's own source and fails if
# the two ever drift apart.
STALE_DISPATCH_SECONDS = 1800


# ---------------------------------------------------------------------------
# 3. THE CLOSED STATUS LEXICON
#
# The only status words any surface may print. Every status bearing
# element carries one of these, so a page with its stylesheet deleted is
# still completely readable, which is WCAG 1.4.1 (level A) satisfied by
# construction rather than by review.
# ---------------------------------------------------------------------------

STATUS_LEXICON = (
    # D1, the pipeline
    "NOW", "complete", "waiting",
    # D2, the gate ladder
    "passed", "failing", "not run", "BLOCKED",
    # D3, the lane map
    "working", "open",
    # D4, the decision fork
    "chosen",
    # D5, the count rows
    "counted",
    # the four rungs
    "NEEDS YOU", "AT RISK", "FOR INFO", "SETTLED",
)


# ---------------------------------------------------------------------------
# 4. LABELS, ESCAPING AND THE ASCII RULE
# ---------------------------------------------------------------------------

def xml_escape(text):
    """The one escaper for anything reaching the page. &, < and > plus
    both quote characters, so the same function is safe in a text node and
    in an attribute value."""
    return saxutils.escape(text or "", {'"': "&quot;", "'": "&#39;"})


def _d(text, limit=300):
    """Every store-derived string that reaches a surface passes through
    here: control characters stripped, length capped, and any em dash, en
    dash or look-alike turned into a plain hyphen. Copied in shape from
    tools/bm_docs.py's own _d, and for the same reason: the text arriving
    is a founder's own goal or a unit's objective, so the copy rule cannot
    be met by writing carefully."""
    return L.plain_dashes(L.safe_display(text or "", limit))


def _flat(text, limit=48):
    """A label safe inside a drawn node, where a colon, a quote, a bracket
    or a hash is either syntax or a colour literal. Shape copied from
    tools/bm_docs.py:283 to 292, including the leading-dash strip: a
    record called "-h" is not hypothetical, this project's own records
    hold one, created by a probe that took a help flag as a name."""
    flat = re.sub(r"[:,\"'\[\]{}()<>#;|]", " ", _d(text, limit)).strip()
    flat = re.sub(r"\s+", " ", flat)
    flat = flat.lstrip("-.=*+ ").strip()
    return flat or "unnamed"


def _ascii(text):
    """Terminal output, forced to ASCII. Same primitive tools/bm_store.py's
    own output funnel falls back to, and the same reason: a founder's goal
    can carry any character at all, and a Windows console that cannot
    encode it produces mojibake or an exception rather than a status
    line."""
    return (text or "").encode("ascii", errors="backslashreplace").decode(
        "ascii")


# ---------------------------------------------------------------------------
# 5. THE SEVEN SHAPES, AND NOTHING ELSE
#
# Seven allowed shapes against roughly twenty five available. Graphic
# economy is the point, not a side effect. The ban is enforced by
# Diagram() raising, not by prose, because a ban enforced by prose gets
# broken in month three by somebody with a really good reason for a pie
# chart.
#
# The seventh is "gantt" (Phase 5, founder decision 2026-08-08). Admitted
# deliberately rather than folded into "timeline", because the two answer
# different questions and share only a word: timeline draws how far a
# piece has moved through its lifecycle, grouped by who owns it; gantt
# draws how long a piece took, grouped by the phase it belongs to. Fusing
# them would have meant one shape whose bars mean two things depending on
# the data, which is worse than seven honest shapes.
#
# The count in this heading and in Diagram()'s refusal is maintained by
# hand and was WRONG until this loop: it read "five" while SHAPES already
# held six, because the timeline addition widened the tuple and left the
# sentence behind. Fixed here, and the refusal below now derives its
# count from the tuple so the two can never disagree again.
# ---------------------------------------------------------------------------

SHAPES = ("pipeline", "gates", "lanes", "fork", "counts", "timeline",
          "gantt")

CAPS = {
    "pipeline": {"nodes": 7, "edges": 6, "lanes": 0, "outcomes": 0},
    "gates": {"nodes": 9, "edges": 8, "lanes": 0, "outcomes": 0},
    "lanes": {"nodes": 9, "edges": 12, "lanes": 4, "outcomes": 0},
    "fork": {"nodes": 4, "edges": 3, "lanes": 0, "outcomes": 3},
    "counts": {"nodes": 6, "edges": 0, "lanes": 0, "outcomes": 0},
    # No edges: a bar is a period on its own, not a step in a flow, so
    # nothing points from one to the next. Lane cap and node cap copied
    # from "lanes", the shape it is closest to in shape.
    "timeline": {"nodes": 9, "edges": 0, "lanes": 4, "outcomes": 0},
    # The progress surface. A programme has more phases than a project has
    # owners, so the lane cap is higher than "timeline"'s four; the node
    # cap is the number of task bars one page can carry before it stops
    # being readable. Both are stated caps, and anything past them is
    # NAMED on the page rather than silently dropped.
    "gantt": {"nodes": 40, "edges": 0, "lanes": 12, "outcomes": 0},
}

# The closed mapping from a founder's question to a shape. A judgement
# call would let a seventh shape in through the side door.
_DIAGRAM_FOR = (
    (("where are we", "how far along", "where"), "pipeline"),
    (("why is this not done", "what is blocking", "blocking"), "gates"),
    (("who is doing what", "who owns this", "who"), "lanes"),
    (("what am i being asked", "asked"), "fork"),
    (("how much against what limit", "how much", "budget"), "counts"),
    (("how is this progressing", "progress by lane", "progress over time"),
     "timeline"),
)


def diagram_for(question):
    """The shape that answers `question`, or "" for anything else.

    Section 7.6's closed mapping. Anything that matches no phrase gets no
    drawing at all, which is the same rule the surface router applies:
    something with no job is not shown."""
    text = (question or "").strip().lower()
    for phrases, shape in _DIAGRAM_FOR:
        for phrase in phrases:
            if phrase in text:
                return shape
    return ""


def node(node_id, label, status, token="", detail="", lane=""):
    """One drawn node. `label` is a noun phrase ending in its status word,
    never a sentence and never an identifier: "Build, NOW", not
    "wr_8813 in_progress". `detail` is the extra word or number printed
    beside the label (the BLOCKED marker, the count sentence). `token`
    names a class in THEME_CSS and carries no colour of its own."""
    return {"id": node_id, "label": label, "status": status,
            "token": token or status, "detail": detail, "lane": lane}


def edge(src, dst, label=""):
    """One directed edge. Single direction throughout a drawing: a cross
    lane arrow voids the per lane direction the reader is relying on."""
    return {"from": src, "to": dst, "label": label}


def Diagram(shape, nodes, edges, title, desc, caption):
    """Build one validated diagram, or raise.

    Returns a plain dict of {shape, nodes, edges, title, desc, caption},
    so a diagram is inspectable data a test can count rather than an
    object with behaviour. Two emitters render it: to_svg for the page and
    to_text for a terminal.

    Raises ValueError when the shape is not one of the seven, when a node
    carries a status word outside STATUS_LEXICON, when the status word is
    not IN the label (colour is never the carrier, so the word has to be
    on the drawing), or when any cap in CAPS is exceeded. A caller with
    more rows than the cap AGGREGATES and says so in the caption; it never
    shrinks the font and it never quietly drops the twelfth row."""
    if shape not in SHAPES:
        raise ValueError(
            "unknown shape %r (allowed: %s). %d shapes and nothing else: "
            "one more requires deleting one. (The count is read from "
            "SHAPES itself, because the hand-maintained one in this "
            "sentence was already stale once.)"
            % (shape, ", ".join(SHAPES), len(SHAPES)))
    nodes = list(nodes or [])
    edges = list(edges or [])
    caps = CAPS[shape]
    # The outcome cap is checked BEFORE the node cap on a shape that has
    # one, because on a fork the two are one apart (four nodes, three
    # outcomes) and the node message would name the wrong thing.
    if caps["outcomes"] and len(nodes) - 1 > caps["outcomes"]:
        raise ValueError(
            "%s carries %d outcomes and the cap is %d, one of which is "
            "always the handback"
            % (shape, len(nodes) - 1, caps["outcomes"]))
    if len(nodes) > caps["nodes"]:
        raise ValueError(
            "%s carries %d nodes and the cap is %d; aggregate and say so "
            "in the caption rather than drawing them all"
            % (shape, len(nodes), caps["nodes"]))
    if len(edges) > caps["edges"]:
        raise ValueError(
            "%s carries %d edges and the cap is %d"
            % (shape, len(edges), caps["edges"]))
    if caps["lanes"]:
        lanes = [n["lane"] for n in nodes if n.get("lane")]
        distinct = sorted(set(lanes))
        if len(distinct) > caps["lanes"]:
            raise ValueError(
                "%s carries %d lanes and the lane cap is %d"
                % (shape, len(distinct), caps["lanes"]))
    ids = set()
    for n in nodes:
        if n["status"] not in STATUS_LEXICON:
            raise ValueError(
                "node %r carries the status %r, which is outside the closed "
                "lexicon (%s)"
                % (n["label"], n["status"], ", ".join(STATUS_LEXICON)))
        if n["status"] not in n["label"]:
            raise ValueError(
                "node %r does not carry its status word %r in its own "
                "label; a status shown only by colour is unreadable to the "
                "people WCAG 1.4.1 exists for"
                % (n["label"], n["status"]))
        ids.add(n["id"])
    for e in edges:
        for end in (e["from"], e["to"]):
            if end not in ids:
                raise ValueError(
                    "edge names %r, which is not a node in this drawing"
                    % (end,))
    return {"shape": shape, "nodes": nodes, "edges": edges,
            "title": _d(title, 120), "desc": _d(desc, 400),
            "caption": _d(caption, 400)}


# The design's inventory names the constructor twice, once as the symbol
# Diagram and once as diagram(...). One function, both names, so no caller
# can be wrong about which one exists.
diagram = Diagram


# ---------------------------------------------------------------------------
# 6. THE PIPELINE STAGES, AND THEIR IMPORT TIME GUARD
# ---------------------------------------------------------------------------

PIPELINE_STAGES = ("Set up", "Understand", "Plan", "Build", "Check",
                   "Deliver")

# Every controller run state maps to exactly one stage. The guard below
# refuses to import if a future state has no entry, so a new state cannot
# silently render as a raw enum, or worse as no stage at all, in front of
# a founder. Same shape as tools/bm_lead.py's RUN_STATE_PLAIN guard.
#
# PAUSED is the one entry that needs its reason written down: a run can be
# paused from nine different states, so its own name does not say where
# the work stopped. It maps to Build, the only stage that means "in the
# middle", because marking a later stage would overstate progress. The
# drawing's caption prints the run's own plain sentence beside it, so the
# founder reads "paused, and it will not move again until you say so"
# rather than a claim that building is under way.
STAGE_FOR_RUN_STATE = {
    "NEW": "Set up",
    "ORIENTING": "Understand",
    "PLANNING": "Plan",
    "READY": "Build",
    "EXECUTING": "Build",
    "VERIFYING": "Check",
    "CHECKPOINTED": "Check",
    "WAITING_HUMAN": "Check",
    "FAILED_RECOVERABLE": "Check",
    "FAILED_TERMINAL": "Check",
    "DELIVERABLE_READY": "Deliver",
    "COMPLETE": "Deliver",
    "STOPPING": "Deliver",
    "STOPPED": "Deliver",
    "PAUSED": "Build",
}


def _check_stage_map(transitions, stages, names):
    """Raise when any run state has no stage, or names one that does not
    exist. Called at import time against the shipped map, and callable
    from a test with an invented state so the guard is proven to fire."""
    missing = sorted(set(transitions) - set(stages))
    if missing:
        raise RuntimeError(
            "bm_visual: no pipeline stage for run state(s): %s. A state a "
            "founder can be shown must never render as no stage at all."
            % ", ".join(missing))
    wrong = sorted(s for s in stages.values() if s not in names)
    if wrong:
        raise RuntimeError(
            "bm_visual: stage(s) outside PIPELINE_STAGES: %s"
            % ", ".join(sorted(set(wrong))))
    return True


_check_stage_map(bs.CONTROLLER_STATE_TRANSITIONS, STAGE_FOR_RUN_STATE,
                 PIPELINE_STAGES)

# The unit statuses that mean work is in flight right now, and the ones
# that mean it is finished. Both read from the store's own closed set
# rather than from a second list somebody keeps in step by hand.
_UNITS_IN_FLIGHT = ("CLAIMED", "DISPATCHED", "RESULT_IN", "VERIFYING")
_UNITS_CLOSED = ("DONE", "FAILED", "SKIPPED")

# The gate word a unit status earns.
_GATE_STATUS = {
    "DONE": "passed",
    "FAILED": "failing",
    "BLOCKED": "failing",
}


# ---------------------------------------------------------------------------
# 6b. THE TASK LIFECYCLE STAGES, FOR THE TIMELINE SHAPE
#
# Read from brotherme/core/schema.py's own ten states, the one canonical
# list: this file never restates them. Two closed maps sit on top, guarded
# at import time the same way STAGE_FOR_RUN_STATE is above, so a future
# eleventh state cannot silently draw as no bar at all.
# ---------------------------------------------------------------------------

S = bs._schema()

TASK_STATES = S.STATES

# "A progress fraction is finished items over planned items in that lane,
# stated on the page in those words" (DESIGN-progress-surface.md, WHAT
# MUST NOT HAPPEN). This is the one closed definition of "finished" every
# builder and every page section that counts a lane's progress reads,
# rather than each inventing its own cutoff. Kept as the same three
# values visual_facts already used inline for its own accepted/planned
# fallback, named here so there is one copy instead of two.
FINISHED_TASK_STATES = ("accepted", "done", "delivered")

_TASK_STATE_INDEX = {state: i for i, state in enumerate(TASK_STATES)}

# The status word a task's own lifecycle state draws as, on the timeline
# shape. blocked is the one state with an unambiguous drawn word already
# in the closed lexicon; every other state collapses to waiting, working
# or complete, the same three-word vocabulary the pipeline and lanes
# shapes already use for "not started yet", "in flight" and "done".
_TIMELINE_STATUS_WORD = {
    "planned": "waiting",
    "ready": "waiting",
    "active": "working",
    "blocked": "BLOCKED",
    "awaiting review": "working",
    "verified": "working",
    "accepted": "complete",
    "delivered": "complete",
    "monitored": "complete",
    "closed": "complete",
}


def _check_task_status_map(states, status_word, lexicon):
    """Raise when a task lifecycle state has no drawn status, or when one
    is mapped outside the closed lexicon. Same governance shape as
    _check_stage_map and _check_rung_map above, and callable from a test
    with an invented state so the guard is proven to fire."""
    missing = sorted(set(states) - set(status_word))
    if missing:
        raise RuntimeError(
            "bm_visual: no drawn status for task state(s): %s. A state a "
            "founder can be shown must never render as no status at all."
            % ", ".join(missing))
    wrong = sorted(w for w in status_word.values() if w not in lexicon)
    if wrong:
        raise RuntimeError(
            "bm_visual: task state(s) mapped to a status outside the "
            "closed lexicon: %s" % ", ".join(sorted(set(wrong))))
    return True


_check_task_status_map(TASK_STATES, _TIMELINE_STATUS_WORD, STATUS_LEXICON)


def _task_lane(task):
    """Which lane one task row belongs in, on the timeline shape: "you"
    when a human is named as the assignee, "BrotherMode" when a runtime
    is, "unassigned" when neither is on record yet. The same two owners
    _owners() below already draws for the lanes shape, read from the
    task's own assignment fields rather than invented."""
    if (task.get("assigned_human") or "").strip():
        return "you"
    if (task.get("assigned_runtime") or "").strip():
        return "BrotherMode"
    return "unassigned"


_TASK_LANE_ORDER = ("you", "BrotherMode", "unassigned")


# ---------------------------------------------------------------------------
# 7. THE ONE READER: rows in, plain facts out
#
# Every builder below is pure and takes this dict. The store reads happen
# HERE, once, through the store's own public accessors: no SQL, no second
# collector for the eight status fields (tools/bm_lead.py owns those and
# this file never re-derives them), and no arithmetic on a forecast
# anywhere.
# ---------------------------------------------------------------------------

def visual_facts(store, project_id, now=None):
    """Everything the five builders need, read once from `store`.

    Returns a dict of plain values:

        project_id    str
        goal          str, the founder's own outcome sentence, or ""
        run_state     str, the controller run state, or "" when no run
        run_sentence  str, that state in plain words (bm_lead's own map)
        stage         str, the PIPELINE_STAGES entry that is NOW
        units         [{unit_id, label, status, lane}]
        gates         [{name, status, detail}] newest ladder first
        gates_total   int, gates_passed int
        owners        [{owner, items: [{label, status}]}]
        decision      the highest stakes open key decision row, or None
        counts        [{label, value, limit, sentence}]
        spend         the store's own spend_totals dict
        newest_at     the newest row timestamp read, or ""

    Nothing here is a second source of truth: every value is a row or a
    count of rows, and the two that are sentences (run_sentence, goal)
    are copied from the row and from bm_lead's map rather than composed
    here."""
    now = now or bs.now_iso()
    project = store.get_project(project_id, raw=True) or {}
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    steps = store.list_human_steps(project_id, resolved=False, raw=True)
    spend = store.spend_totals(project_id)
    tasks = store.list_tasks(project_id, raw=True)
    decisions = _ranked_decisions(store, project_id)
    alerts = alerts_now(store, project_id, now=now)

    run_state = (run or {}).get("state") or ""
    stage = STAGE_FOR_RUN_STATE.get(run_state, PIPELINE_STAGES[0])
    run_sentence = bl.RUN_STATE_PLAIN.get(
        run_state, "no work has been started yet")

    unit_rows = [{"unit_id": u["unit_id"],
                  "label": _flat(u.get("objective") or u["unit_id"]),
                  "status": u.get("status") or "",
                  "lane": u.get("lane") or "default"}
                 for u in units]

    gates = []
    for u in unit_rows:
        status = _GATE_STATUS.get(u["status"], "not run")
        gates.append({"name": u["label"], "status": status,
                      "detail": "BLOCKED" if status == "failing" else ""})
    for step in steps:
        gates.append({"name": _flat(step.get("what") or "a step only you "
                                    "can take"),
                      "status": "failing", "detail": "BLOCKED"})
    passed = len([g for g in gates if g["status"] == "passed"])

    owners = _owners(unit_rows, steps)

    accepted = len([u for u in unit_rows if u["status"] == "DONE"])
    planned = len(unit_rows)
    if not planned and tasks:
        planned = len(tasks)
        accepted = len([t for t in tasks
                        if (t.get("status") or "") in FINISHED_TASK_STATES])
    by_rung = {rung: len([a for a in alerts if a["rung"] == rung])
               for rung in RUNGS}
    counts = [
        {"label": "Gates", "value": passed, "limit": len(gates),
         "sentence": "%d of %d gates passed" % (passed, len(gates))},
        {"label": "Steps", "value": accepted, "limit": planned,
         "sentence": "%d of %d steps accepted" % (accepted, planned)},
        {"label": "Work budget", "value": spend.get("tokens") or 0,
         "limit": spend.get("token_ceiling"),
         "sentence": _against_limit(spend.get("tokens") or 0,
                                    spend.get("token_ceiling"), "tokens")},
        {"label": "Working time", "value": spend.get("minutes") or 0,
         "limit": spend.get("minutes_ceiling"),
         "sentence": _against_limit(spend.get("minutes") or 0,
                                    spend.get("minutes_ceiling"),
                                    "minutes")},
        {"label": "Findings", "value": None, "limit": None,
         "sentence": "needs you %d, at risk %d, for info %d, settled %d"
                     % (by_rung["NEEDS YOU"], by_rung["AT RISK"],
                        by_rung["FOR INFO"], by_rung["SETTLED"])},
    ]

    stamps = [r.get("created_at") or "" for r in (units + steps + tasks)]
    stamps.extend([(run or {}).get("updated_at") or "",
                   project.get("updated_at") or ""])
    stamps.extend([d.get("created_at") or "" for d in decisions])

    return {"project_id": project_id,
            "goal": _d(project.get("goal") or ""),
            "run_state": run_state,
            "run_sentence": run_sentence,
            "stage": stage,
            "units": unit_rows,
            "gates": gates,
            "gates_total": len(gates),
            "gates_passed": passed,
            "owners": owners,
            "decision": decisions[0] if decisions else None,
            "counts": counts,
            "spend": spend,
            "newest_at": max(stamps) if stamps else ""}


def _against_limit(used, limit, unit):
    """"18000 of 40000 tokens used", or "18000 tokens used, no limit
    agreed yet" when there is no ceiling. Not cosmetic: the naive form
    prints "18000 of no agreed limit tokens used", which is the machine's
    absence leaking into a sentence a founder is supposed to read."""
    if limit is None:
        return "%s %s used, no limit agreed yet" % (used, unit)
    return "%s of %s %s used" % (used, limit, unit)


def _ranked_decisions(store, project_id):
    """The open key decisions, highest stakes first. The stakes order is
    bm_lead's DECISION_STAKES, which is data, so this sorts by it rather
    than re-deciding what matters most."""
    rows = store.open_key_decisions(project_id, raw=True)
    order = {cls: i for i, cls in enumerate(bl.DECISION_STAKES)}
    return sorted(rows, key=lambda r: order.get(r.get("decision_class"),
                                                len(order)))


def _owners(unit_rows, steps):
    """Who holds the pen on each OPEN item, as lanes.

    Two owners exist in this product and they are not job titles: you, and
    BrotherMode. A lane with an open step only a human can take is yours;
    a lane with work in flight or queued is BrotherMode's. Closed items do
    not appear at all, because the question this answers is who owns the
    step RIGHT NOW."""
    lanes = {}
    for step in steps:
        owner = "you"
        lanes.setdefault(owner, []).append(
            {"label": "%s, open" % _flat(step.get("what") or "a step"),
             "status": "open"})
    for u in unit_rows:
        if u["status"] in _UNITS_CLOSED:
            continue
        owner = "BrotherMode"
        status = "working" if u["status"] in _UNITS_IN_FLIGHT else "waiting"
        lanes.setdefault(owner, []).append(
            {"label": "%s, %s" % (u["label"], status), "status": status})
    return [{"owner": owner, "items": lanes[owner]}
            for owner in sorted(lanes)]


# ---------------------------------------------------------------------------
# 7b. PROGRESS FACTS: rows in, plain facts out, NO STORE HANDLE
#
# DESIGN-progress-surface.md item 2. Unlike visual_facts above, this takes
# already-fetched rows rather than a store handle: a function with no
# store handle can never grow into a second query path around the
# store's own accessors, and it is testable with a handful of dicts
# instead of a database. tools/bm_view.py is the only caller, and it does
# the fetching through the store's own list_tasks, list_dependencies and
# list_evidence: no SQL and no second collector live here either.
# ---------------------------------------------------------------------------

def progress_facts(rows):
    """Progress over already-gathered task rows. Pure: no store handle,
    no SQL, testable with plain dicts, same rule the rest of this module
    follows.

    `rows` is a dict of:
        tasks          [task row, ...], the shape store.list_tasks returns
        dependencies   [{"task_id", "depends_on_task_id"}, ...], the shape
                       store.list_dependencies returns
        evidence       {task_id: [evidence row, ...]}, one
                       store.list_evidence("task", task_id) call per task,
                       keyed by the caller because list_evidence itself
                       takes one subject at a time

    Returns {"lanes": [{"lane": name, "items": [item, ...]}, ...]}, one
    lane per owner that holds at least one task ("you", "BrotherMode",
    "unassigned", in that order), items in each lane in list_tasks' own
    insertion order. Each item is:

        task_id       str
        label         a flat, safe noun phrase (the task's own title)
        state         the task's own lifecycle status, verbatim
        evidence_ref  the newest evidence row's own ref for that task, or
                      "" when none is on record; the literal absence of
                      one is a rendering decision and is never invented
                      text here
        blocked_by    the sorted, deduplicated depends_on_task_id list the
                      dependencies rows record for that task, whatever
                      its own state; empty when nothing blocks it
        action        the task's own recorded blockers, joined, or "" when
                      none are on record

    Nothing here is a second source of truth: every field is a row's own
    column, copied, never computed from a clock or a formula."""
    rows = rows or {}
    tasks = list(rows.get("tasks") or [])
    dependencies = list(rows.get("dependencies") or [])
    evidence_by_task = rows.get("evidence") or {}
    blocked_by = {}
    for dep in dependencies:
        task_id = dep.get("task_id")
        depends_on = dep.get("depends_on_task_id")
        if task_id and depends_on:
            blocked_by.setdefault(task_id, set()).add(depends_on)
    lanes = {}
    for t in tasks:
        task_id = t.get("task_id") or ""
        evidence = evidence_by_task.get(task_id) or []
        ref = (evidence[-1].get("ref") or "") if evidence else ""
        blockers = t.get("blockers") or []
        if isinstance(blockers, str):
            blockers = []
        item = {
            "task_id": task_id,
            "label": _flat(t.get("title") or task_id),
            "state": t.get("status") or "",
            "evidence_ref": ref,
            "blocked_by": sorted(blocked_by.get(task_id) or set()),
            "action": _d("; ".join(b for b in blockers if b), 300),
        }
        lanes.setdefault(_task_lane(t), []).append(item)
    return {"lanes": [{"lane": lane, "items": lanes[lane]}
                      for lane in _TASK_LANE_ORDER if lane in lanes]}


# ---------------------------------------------------------------------------
# 7c. THE PROGRESS SURFACE (Phase 5, founder decision 2026-08-08)
#
# The Gantt. Same rule as 7b above and for the same reason: pure over
# already-gathered rows, no store handle, no SQL, and above all no clock.
# A page whose geometry moved because it was rendered on a Tuesday could
# never be byte-identical on rerun, which is the one property that makes
# "have the records changed?" answerable by comparing bytes.
# ---------------------------------------------------------------------------

# The group a task with no recorded phase draws under. Its own name, said
# out loud on the page, rather than being filed under whichever phase
# happens to be current: see bm_store._TASKS_V18_COLUMN for why an
# unrecorded phase must never be inferred.
UNPHASED = "no phase recorded"


def _day(stamp):
    """The calendar day of an ISO timestamp, as a (text, ordinal) pair,
    or None when there is nothing parseable there.

    Deliberately strict and deliberately narrow: it reads the leading
    YYYY-MM-DD and nothing else. A value it cannot read is dropped and
    reported as undated, never repaired into a nearby date, because a
    repaired date would put a bar somewhere no record puts it."""
    if not isinstance(stamp, str):
        return None
    head = stamp.strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", head):
        return None
    try:
        y, m, d = (int(p) for p in head.split("-"))
        return head, datetime.date(y, m, d).toordinal()
    except ValueError:  # sbe: allow-silent per docstring: an invalid calendar date is dropped and reported as undated
        return None


def gantt_facts(rows):
    """The progress surface's own data function, over the same `rows`
    shape progress_facts takes (section 7b), plus each task's recorded
    `phase` (schema 18).

    Returns:

        phases   [{"phase", "items", "ticked", "total"}, ...], in the
                 order the phases FIRST appear in the rows, which is
                 list_tasks' insertion order; the unphased group, when it
                 exists, sorts last because it is a remainder and not a
                 stage of the plan
        window   {"start", "end", "days"} spanning the earliest recorded
                 date to the latest, or None when nothing is dated at all
        ticked   how many items across every phase are ticked
        total    how many items there are

    Each item carries task_id, label, state, ticked, evidence_ref,
    dated, start, end, open_ended.

    THE TICK CONTRACT, and its honest limit. `ticked` is True only when
    the task is in a finished state AND at least one evidence row is on
    record for it. That is the strongest thing these records can prove.
    They CANNOT prove the check ran after the last edit, which is what
    the contract asks of a human; nothing in the store observes edits.
    The page therefore says what a tick means in those words rather than
    letting the reader assume more, and the gap is stated here so that
    the next person to touch this function knows it is a known limit and
    not an oversight."""
    rows = rows or {}
    tasks = list(rows.get("tasks") or [])
    evidence_by_task = rows.get("evidence") or {}
    days = []
    for t in tasks:
        for key in ("started_at", "completed_at"):
            parsed = _day(t.get(key))
            if parsed:
                days.append(parsed)
    window = None
    if days:
        lo = min(days, key=lambda p: p[1])
        hi = max(days, key=lambda p: p[1])
        # span_days, not "days": design section 4.2 gives ONE authority
        # for durations on the page (bl.render_forecast_lines), and this
        # module may not format one of its own. The number here is
        # geometry, never prose: it is the divisor that scales a bar into
        # the fixed track, and it is deliberately never printed.
        window = {"start": lo[0], "end": hi[0],
                  "span_days": hi[1] - lo[1] + 1}
    order, groups = [], {}
    for t in tasks:
        task_id = t.get("task_id") or ""
        phase = (t.get("phase") or "").strip() or UNPHASED
        if phase not in groups:
            order.append(phase)
            groups[phase] = []
        started = _day(t.get("started_at"))
        completed = _day(t.get("completed_at"))
        evidence = evidence_by_task.get(task_id) or []
        ref = (evidence[-1].get("ref") or "") if evidence else ""
        # An open bar: a real start, no recorded end. It runs to the edge
        # of the window, which is the last date anything was recorded on,
        # and says open_ended so the page can draw that edge as unfinished
        # rather than as a completion nobody wrote down.
        end = completed[0] if completed else (
            window["end"] if (started and window) else "")
        groups[phase].append({
            "task_id": task_id,
            "label": _flat(t.get("title") or task_id),
            "state": t.get("status") or "",
            "ticked": bool(t.get("status") in FINISHED_TASK_STATES
                           and ref),
            "evidence_ref": ref,
            "dated": bool(started or completed),
            "start": started[0] if started else (
                completed[0] if completed else ""),
            "end": end,
            "open_ended": bool(started and not completed),
        })
    phases = [{"phase": p, "items": groups[p],
               "ticked": len([i for i in groups[p] if i["ticked"]]),
               "total": len(groups[p])}
              for p in order if p != UNPHASED]
    if UNPHASED in groups:
        phases.append({"phase": UNPHASED, "items": groups[UNPHASED],
                       "ticked": len([i for i in groups[UNPHASED]
                                      if i["ticked"]]),
                       "total": len(groups[UNPHASED])})
    return {"phases": phases, "window": window,
            "ticked": sum(p["ticked"] for p in phases),
            "total": sum(p["total"] for p in phases)}


# ---------------------------------------------------------------------------
# 8. THE SIX BUILDERS
#
# Each is pure over visual_facts' dict, except diagram_timeline, which is
# pure over progress_facts' own dict (section 7b, above): the two data
# functions stay separate because a store-free reader must never depend
# on one that opens a store. Four of the six return None rather than an
# empty drawing: a picture of nothing teaches nothing, and the page
# renders its designed empty state instead.
# ---------------------------------------------------------------------------

def diagram_pipeline(facts):
    """D1. "Where are we?" Always drawable: even a project with no work
    recorded has a first stage, and marking it is how the founder sees the
    shape of what is coming."""
    stage = facts.get("stage") or PIPELINE_STAGES[0]
    index = PIPELINE_STAGES.index(stage)
    started = bool(facts.get("run_state"))
    nodes, edges = [], []
    for i, name in enumerate(PIPELINE_STAGES):
        if i == index:
            status = "NOW"
        elif i < index and started:
            status = "complete"
        else:
            status = "waiting"
        nodes.append(node("s%d" % i, "%s, %s" % (name, status), status))
        if i:
            edges.append(edge("s%d" % (i - 1), "s%d" % i))
    return Diagram(
        "pipeline", nodes, edges,
        "Where the work stands now",
        "%d stages left to right. %s is the stage in progress. %s"
        % (len(PIPELINE_STAGES), stage, facts.get("run_sentence") or ""),
        "The work is at %s. Everything to its left is complete and "
        "everything to its right has not started. In words: %s."
        % (stage, facts.get("run_sentence") or "nothing has started yet"))


def diagram_gates(facts):
    """D2. "Why is this not shipped yet?" None when nothing has to be true
    yet, which is a real answer rather than an empty column."""
    gates = list(facts.get("gates") or [])
    if not gates:
        return None
    cap = CAPS["gates"]["nodes"]
    total = len(gates)
    shown = gates
    aggregated = False
    if total > cap:
        aggregated = True
        not_passing = [g for g in gates if g["status"] != "passed"]
        rest = [g for g in gates if g["status"] == "passed"]
        shown = (not_passing + rest)[:cap]
    nodes, edges = [], []
    for i, gate in enumerate(shown):
        nodes.append(node("g%d" % i,
                          "%s, %s" % (gate["name"], gate["status"]),
                          gate["status"], detail=gate["detail"]))
        if i:
            edges.append(edge("g%d" % (i - 1), "g%d" % i))
    passed = facts.get("gates_passed", 0)
    if aggregated:
        caption = ("%d of %d gates shown, the ones that are not passing "
                   "first. %d of %d have passed so far."
                   % (len(shown), total, passed, total))
    else:
        caption = ("%d of %d gates have passed. A gate marked BLOCKED is "
                   "the reason the work is not finished."
                   % (passed, total))
    return Diagram(
        "gates", nodes, edges,
        "What has to be true before this is finished",
        # The description says what THIS drawing shows and never
        # enumerates the words a gate could carry: a long description that
        # lists every possible status puts words on the page that no node
        # actually has, and the terminal form would then say something
        # different from the picture.
        "A single column of %d gates in order, each with its own status "
        "word beside it." % (len(shown),),
        caption)


def diagram_lanes(facts):
    """D3. "Who holds the pen?" None when every open item has the same
    owner, which is mermaid's own criterion for drawing a swimlane at all:
    it is drawn only when ownership is the important question."""
    owners = list(facts.get("owners") or [])
    if len(owners) < 2:
        return None
    cap = CAPS["lanes"]
    owners = owners[:cap["lanes"]]
    nodes, edges = [], []
    room = cap["nodes"]
    for oi, lane in enumerate(owners):
        previous = None
        for ii, item in enumerate(lane["items"]):
            if len(nodes) >= room:
                break
            node_id = "l%d_%d" % (oi, ii)
            nodes.append(node(node_id, item["label"], item["status"],
                              lane=lane["owner"]))
            if previous is not None:
                edges.append(edge(previous, node_id))
            previous = node_id
    shown_owners = ", ".join(l["owner"] for l in owners)
    return Diagram(
        "lanes", nodes, edges,
        "Who holds the pen on each open step",
        "One lane per owner (%s), left to right inside each lane."
        % shown_owners,
        "Open steps by owner: %s. A step in your lane is one only you can "
        "take." % shown_owners)


def diagram_fork(facts):
    """D4. "You are being asked something." None when no key decision is
    open, and the page shows the standing handback panel instead.

    The handback branch is always present, always last, always the
    emphasised node. The store already refuses to record a key decision
    that offers no handback; this makes it undrawable too."""
    decision = facts.get("decision")
    if not decision:
        return None
    alternatives = decision.get("alternatives") or []
    if isinstance(alternatives, str):
        alternatives = []
    subject = _flat(decision.get("subject") or "one decision")
    nodes = [node("q", "%s, open" % subject, "open")]
    edges = []
    # One outcome per alternative, capped so the handback always fits.
    room = CAPS["fork"]["outcomes"] - 1
    for i, alt in enumerate(alternatives[:room]):
        option = alt.get("option") if isinstance(alt, dict) else alt
        nodes.append(node("o%d" % i,
                          "%s, if chosen" % _flat(option or "an option"),
                          "chosen"))
        edges.append(edge("q", "o%d" % i,
                          _flat((alt.get("why_not")
                                 if isinstance(alt, dict) else "") or "")))
    if len(nodes) == 1:
        nodes.append(node("o0", "%s, if chosen"
                          % _flat(decision.get("claim") or "carry on"),
                          "chosen"))
        edges.append(edge("q", "o0"))
    nodes.append(node("hand", "You take this over, if chosen", "chosen",
                      token="handback"))
    edges.append(edge("q", "hand"))
    return Diagram(
        "fork", nodes, edges,
        "The decision in front of you",
        "One question with %d outcomes. The last one hands the work back "
        "to you." % (len(nodes) - 1,),
        "%s The last branch is always yours: %s"
        % (_d(decision.get("claim") or ""), bl.HANDBACK_OPTION_TEXT))


def diagram_gantt(facts):
    """D7. "Where does the whole programme stand, phase by phase?" One
    node per task, grouped into its phase, drawn as a time bar when the
    records carry dates and as a lifecycle fill when they do not.

    None when there is no work recorded: a picture of nothing teaches
    nothing, the same rule the four optional builders above follow.

    Pure over gantt_facts' own dict, never a store handle, for the reason
    section 7c states."""
    phases = [p for p in (facts or {}).get("phases") or [] if p["items"]]
    if not phases:
        return None
    cap = CAPS["gantt"]
    window = facts.get("window")
    dropped_phases = max(0, len(phases) - cap["lanes"])
    phases = phases[:cap["lanes"]]
    nodes, dropped_nodes = [], 0
    for pi, phase in enumerate(phases):
        for ii, item in enumerate(phase["items"]):
            if len(nodes) >= cap["nodes"]:
                dropped_nodes += 1
                continue
            status = _TIMELINE_STATUS_WORD.get(item["state"], "waiting")
            if item["dated"]:
                detail = "%s to %s%s" % (
                    item["start"], item["end"],
                    ", still open" if item["open_ended"] else "")
            else:
                detail = "no dates recorded"
            n = node("gt%d_%d" % (pi, ii),
                     "%s, %s" % (item["label"], status), status,
                     detail=detail, lane=phase["phase"])
            n["ticked"] = item["ticked"]
            n["evidence_ref"] = item["evidence_ref"]
            n["dated"] = item["dated"]
            if item["dated"] and window:
                start = _day(item["start"])
                end = _day(item["end"]) or start
                n["offset_days"] = start[1] - _day(window["start"])[1]
                n["span_days"] = max(1, end[1] - start[1] + 1)
                n["window_days"] = window["span_days"]
            else:
                # No dates: fall back to the lifecycle fill the timeline
                # shape already draws, so the bar still says something
                # true rather than drawing nothing at all.
                n["value"] = _TASK_STATE_INDEX.get(item["state"], 0) + 1
                n["limit"] = len(TASK_STATES)
            nodes.append(n)
    ticked, total = facts["ticked"], facts["total"]
    over = ""
    if dropped_nodes or dropped_phases:
        over = (" %d task(s) and %d phase(s) are past this page's stated "
                "cap and are not drawn." % (dropped_nodes, dropped_phases))
    when = ("The window runs %s to %s."
            % (window["start"], window["end"])
            if window else
            "No dates are recorded yet, so the bars show how far each "
            "piece has moved rather than how long it took.")
    return Diagram(
        "gantt", nodes, [],
        "Where the programme stands, phase by phase",
        "One bar per piece of work, grouped into the phase its own record "
        "names. %s A box is ticked only where the work is finished AND a "
        "check is on record for it." % when,
        "%d of %d pieces ticked, across %d phase(s). %s%s"
        % (ticked, total, len(phases), when, over))


def counts_rows(facts):
    """D5. "How much, against what limit?" Always drawable, because zero
    of zero is a real answer.

    Not a drawing in the sense the four per page cap counts: the bar is
    decoration and the NUMBER is the content, which is why every row
    carries its own sentence and the page announces that sentence rather
    than a percentage."""
    rows = list(facts.get("counts") or [])[:CAPS["counts"]["nodes"]]
    nodes = []
    for i, row in enumerate(rows):
        nodes.append(node("c%d" % i, "%s, counted" % row["label"],
                          "counted", detail=row["sentence"]))
        nodes[-1]["value"] = row["value"]
        nodes[-1]["limit"] = row["limit"]
    return Diagram(
        "counts", nodes, [],
        "How much, against what limit",
        "%d labelled rows, each with its own numbers written out."
        % (len(nodes),),
        "; ".join(row["sentence"] for row in rows) or "nothing counted yet")


def diagram_timeline(progress):
    """D6. "How is the work progressing, lane by lane?" One bar per item
    inside its own lane, the bar's fill showing how far that item has
    moved through the ten recorded lifecycle stages. None when there is
    nothing to show yet, the same rule the other optional builders
    follow: a project with no tasks recorded has genuinely nothing to
    draw here.

    Takes progress_facts' own dict (section 7b), never a store handle:
    the fraction each bar draws is TASK_STATES' own closed order, the
    same one _TIMELINE_STATUS_WORD already maps to a drawn status,
    never an invented confidence figure."""
    lanes = [l for l in (progress or {}).get("lanes") or []
             if l.get("items")]
    if not lanes:
        return None
    cap = CAPS["timeline"]
    lanes = lanes[:cap["lanes"]]
    total = len(TASK_STATES)
    nodes, edges = [], []
    room = cap["nodes"]
    for li, lane in enumerate(lanes):
        for ii, item in enumerate(lane["items"]):
            if len(nodes) >= room:
                break
            status = _TIMELINE_STATUS_WORD.get(item["state"], "waiting")
            stage = _TASK_STATE_INDEX.get(item["state"], 0) + 1
            n = node("tl%d_%d" % (li, ii),
                     "%s, %s" % (item["label"], status), status,
                     detail="stage %d of %d" % (stage, total),
                     lane=lane["lane"])
            n["value"] = stage
            n["limit"] = total
            nodes.append(n)
    lane_names = ", ".join(l["lane"] for l in lanes)
    return Diagram(
        "timeline", nodes, edges,
        "How far each lane has moved",
        "One bar per item inside each lane (%s); the fill shows how far "
        "that item has moved through its recorded stages." % lane_names,
        "Progress by lane: %s." % lane_names)


# ---------------------------------------------------------------------------
# 9. THE TWO EMITTERS
#
# Geometry is computed from CHARACTER COUNTS at a fixed advance width,
# never measured, so the same rows produce the same bytes on every
# platform and a regenerated page is byte identical a week later.
# ---------------------------------------------------------------------------

_CHAR = 8
# The progress surface's fixed track. Every Gantt bar is a fraction of
# this many pixels, computed by integer division, which is what makes two
# renders of the same rows the same bytes.
_GANTT_TRACK = 480
_PAD = 14
_ROW = 44
_GAP = 26


def _text_width(text):
    return _CHAR * len(text) + 2 * _PAD


def _node_text(n):
    """The label plus its detail, separated by two spaces rather than by a
    comma: the label already ends in a comma and its status word, so a
    third comma would read as a third clause instead of as the separate
    thing the detail is (the BLOCKED marker, the count sentence)."""
    return n["label"] + ("  " + n["detail"] if n["detail"] else "")


def _svg_open(diagram, width, height, ident):
    return (
        '<svg role="img" viewBox="0 0 %d %d" width="%d" height="%d" '
        'aria-labelledby="%st" aria-describedby="%sd">'
        '<title id="%st">%s</title><desc id="%sd">%s</desc>'
        % (width, height, width, height, ident, ident, ident,
           xml_escape(diagram["title"]), ident,
           xml_escape(diagram["desc"])))


def _figure(diagram, svg):
    return ('<figure class="bm-figure">%s<figcaption>%s</figcaption>'
            '</figure>' % (svg, xml_escape(diagram["caption"])))


def _slug(text):
    """A status word or token as a CSS class suffix: lowercase, and every
    run of anything else collapsed to one hyphen.

    Not cosmetic. A token used raw would emit class="bm-not run" for the
    status "not run", which the browser reads as TWO classes, one of them
    called "run", and class="bm-NOW" would never match the rule written
    as .bm-now. Both were real, both were found by rendering the page and
    comparing the classes it used against the ones THEME_CSS defines."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _box(x, y, w, h, n):
    """One node box plus its label. No colour attribute of any kind: the
    class names below are styled in THEME_CSS and nowhere else."""
    return (
        '<rect class="bm-node bm-%s" x="%d" y="%d" width="%d" height="%d" '
        'rx="6"></rect>'
        '<text class="bm-node-label" x="%d" y="%d">%s</text>'
        % (_slug(n["token"]), x, y, w, h, x + _PAD, y + h // 2 + 5,
           xml_escape(_node_text(n))))


def to_svg(diagram):
    """The page rendering: one complete figure element, containing an svg
    with role="img", a title, a desc wired by aria-describedby, and a
    figcaption stating the same content in prose a sighted non expert can
    read without decoding the picture.

    The prose is not duplicate cost. It is the thing that makes the
    picture optional."""
    shape = diagram["shape"]
    ident = "bm-" + shape
    nodes = diagram["nodes"]
    if shape == "pipeline":
        parts, x = [], 10
        for n in nodes:
            w = _text_width(_node_text(n))
            parts.append(_box(x, 10, w, _ROW, n))
            x += w + _GAP
        width, height = x, _ROW + 20
        body = "".join(parts)
    elif shape == "gates":
        parts, y, width = [], 10, 10
        for n in nodes:
            w = _text_width(_node_text(n))
            width = max(width, w + 20)
            parts.append(_box(10, y, w, _ROW, n))
            y += _ROW + 12
        width, height = width, y + 10
        body = "".join(parts)
    elif shape == "lanes":
        parts, y, width = [], 10, 10
        lanes = []
        for n in nodes:
            if n["lane"] not in lanes:
                lanes.append(n["lane"])
        # The lane label column is as wide as the widest owner name plus
        # one character, computed rather than fixed: a fixed column
        # silently overlaps the first node the day an owner name grows.
        column = 10 + _CHAR * (max(len(l) for l in lanes) + 2)
        for lane in lanes:
            parts.append('<text class="bm-node-label" x="10" y="%d">%s</text>'
                         % (y + 16, xml_escape(lane)))
            x = column
            for n in [n for n in nodes if n["lane"] == lane]:
                w = _text_width(_node_text(n))
                parts.append(_box(x, y, w, _ROW, n))
                x += w + _GAP
            width = max(width, x)
            y += _ROW + 16
        width, height = width, y + 10
        body = "".join(parts)
    elif shape == "fork":
        question, outcomes = nodes[0], nodes[1:]
        qw = _text_width(_node_text(question))
        parts = [_box(10, 10, qw, _ROW, question)]
        x, y = 10, _ROW + 40
        for n in outcomes:
            w = _text_width(_node_text(n))
            parts.append(_box(x, y, w, _ROW, n))
            x += w + _GAP
        width, height = max(x, qw + 20), y + _ROW + 10
        body = "".join(parts)
    elif shape == "gantt":
        # THE PROGRESS SURFACE. Geometry from integers only: day counts
        # scaled into a fixed track by integer division, never a measured
        # font and never a clock, so the same rows emit the same bytes.
        track, parts, y, width = _GANTT_TRACK, [], 10, 10
        lanes = []
        for n in nodes:
            if n["lane"] not in lanes:
                lanes.append(n["lane"])
        column = 10 + _CHAR * (max(len(_node_text(n)) for n in nodes) + 3)
        for lane in lanes:
            parts.append('<text class="bm-phase-label" x="10" y="%d">%s</text>'
                         % (y + 14, xml_escape(lane)))
            y += 26
            for n in [n for n in nodes if n["lane"] == lane]:
                tick = "bm-tick-on" if n.get("ticked") else "bm-tick-off"
                parts.append(
                    '<rect class="%s" x="10" y="%d" width="12" height="12" '
                    'rx="2"></rect>' % (tick, y + 2))
                parts.append(
                    '<text class="bm-node-label" x="30" y="%d">%s</text>'
                    % (y + 13, xml_escape(_node_text(n))))
                span = n.get("window_days")
                if isinstance(span, int) and span > 0:
                    off = max(0, min(int(track * n["offset_days"] / span),
                                      track))
                    wide = max(3, min(int(track * n["span_days"] / span),
                                       track - off))
                    parts.append(
                        '<g role="img" aria-label="%s">'
                        '<rect class="bm-bar-track" x="%d" y="%d" '
                        'width="%d" height="10" rx="4"></rect>'
                        '<rect class="bm-span" x="%d" y="%d" width="%d" '
                        'height="10" rx="4"></rect></g>'
                        % (xml_escape(n["detail"]), column, y + 3, track,
                           column + off, y + 3, wide))
                else:
                    limit, value = n.get("limit"), n.get("value")
                    if isinstance(limit, int) and limit > 0 and isinstance(
                            value, int):
                        filled = max(0, min(int(track * value / limit),
                                             track))
                        parts.append(
                            '<g role="progressbar" aria-valuemin="0" '
                            'aria-valuemax="%d" aria-valuenow="%d" '
                            'aria-valuetext="%s">'
                            '<rect class="bm-bar-track" x="%d" y="%d" '
                            'width="%d" height="10" rx="4"></rect>'
                            '<rect class="bm-bar-fill" x="%d" y="%d" '
                            'width="%d" height="10" rx="4"></rect></g>'
                            % (limit, value, xml_escape(n["detail"]),
                               column, y + 3, track, column, y + 3, filled))
                width = max(width, column + track + 10)
                y += 24
            y += 10
        width, height = width, y
        body = "".join(parts)
    elif shape == "timeline":
        parts, y, width = [], 10, 260
        lanes = []
        for n in nodes:
            if n["lane"] not in lanes:
                lanes.append(n["lane"])
        for lane in lanes:
            parts.append('<text class="bm-node-label" x="10" y="%d">%s</text>'
                         % (y + 14, xml_escape(lane)))
            y += 24
            for n in [n for n in nodes if n["lane"] == lane]:
                w = _text_width(_node_text(n))
                width = max(width, w + 20)
                parts.append(_box(10, y, w, _ROW, n))
                limit, value = n.get("limit"), n.get("value")
                if isinstance(limit, int) and limit > 0 and isinstance(
                        value, int):
                    filled = max(0, min(int(240 * value / limit), 240))
                    parts.append(
                        '<g role="progressbar" aria-valuemin="0" '
                        'aria-valuemax="%d" aria-valuenow="%d" '
                        'aria-valuetext="%s">'
                        '<rect class="bm-bar-track" x="10" y="%d" '
                        'width="240" height="10" rx="4"></rect>'
                        '<rect class="bm-bar-fill" x="10" y="%d" width="%d" '
                        'height="10" rx="4"></rect></g>'
                        % (limit, value, xml_escape(n["detail"]),
                           y + _ROW + 4, y + _ROW + 4, filled))
                y += _ROW + 24
            y += 12
        width, height = width, y
        body = "".join(parts)
    else:
        parts, y, width = [], 10, 10
        for n in nodes:
            label = xml_escape(_node_text(n))
            bar = ""
            limit = n.get("limit")
            value = n.get("value")
            if isinstance(limit, int) and limit > 0 and isinstance(value,
                                                                   int):
                filled = max(0, min(int(240 * value / limit), 240))
                bar = ('<g role="progressbar" aria-valuemin="0" '
                       'aria-valuemax="%d" aria-valuenow="%d" '
                       'aria-valuetext="%s">'
                       '<rect class="bm-bar-track" x="10" y="%d" width="240" '
                       'height="18" rx="4"></rect>'
                       '<rect class="bm-bar-fill" x="10" y="%d" width="%d" '
                       'height="18" rx="4"></rect></g>'
                       % (limit, value, xml_escape(n["detail"]), y + 22,
                          y + 22, filled))
            parts.append('<text class="bm-node-label" x="10" y="%d">%s</text>'
                         '%s' % (y + 14, label, bar))
            y += _ROW + 20
            width = max(width, 260, _text_width(_node_text(n)))
        width, height = width, y
        body = "".join(parts)
    return _figure(diagram, _svg_open(diagram, width, height, ident) + body
                   + "</svg>")


def to_text(diagram):
    """The terminal rendering, which must work with zero styling: ASCII
    only, no box drawing characters, no emoji. It reads correctly in a
    monochrome terminal, in a screen reader, and pasted into an email."""
    lines = [diagram["title"]]
    shape = diagram["shape"]
    nodes = diagram["nodes"]
    if shape == "pipeline":
        lines.append("  " + "  >  ".join(_node_text(n) for n in nodes))
    elif shape == "gates":
        for n in nodes:
            lines.append("  " + _node_text(n))
    elif shape == "lanes":
        lanes = []
        for n in nodes:
            if n["lane"] not in lanes:
                lanes.append(n["lane"])
        for lane in lanes:
            items = [_node_text(n) for n in nodes if n["lane"] == lane]
            lines.append("  %s: %s" % (lane, "; ".join(items)))
    elif shape == "fork":
        lines.append("  " + _node_text(nodes[0]))
        for i, n in enumerate(nodes[1:]):
            lines.append("    %s. %s" % (chr(ord("A") + i), _node_text(n)))
    elif shape == "timeline":
        lanes = []
        for n in nodes:
            if n["lane"] not in lanes:
                lanes.append(n["lane"])
        for lane in lanes:
            lines.append("  %s:" % lane)
            for n in [n for n in nodes if n["lane"] == lane]:
                limit, value = n.get("limit"), n.get("value")
                bar = ""
                if isinstance(limit, int) and limit > 0 and isinstance(
                        value, int):
                    filled = max(0, min(int(8 * value / limit), 8))
                    bar = "   [%s%s]" % ("#" * filled, "-" * (8 - filled))
                lines.append("    %s%s" % (_node_text(n), bar))
    else:
        for n in nodes:
            limit, value = n.get("limit"), n.get("value")
            bar = ""
            if isinstance(limit, int) and limit > 0 and isinstance(value,
                                                                   int):
                filled = max(0, min(int(8 * value / limit), 8))
                bar = "   [%s%s]" % ("#" * filled, "-" * (8 - filled))
            lines.append("  %s%s" % (_node_text(n), bar))
    lines.append("  " + diagram["caption"])
    return _ascii("\n".join(lines))


# ---------------------------------------------------------------------------
# 10. THE INSIGHT BOX: one shape, six slots, no optional slot
# ---------------------------------------------------------------------------

INSIGHT_SLOTS = ("CLAIM", "BECAUSE", "INSTEAD OF", "CHANGES IF",
                 "CONFIDENCE", "YOUR MOVE")

_NOT_TESTED = "not tested"


def insight_box_slots(insight, ic=False):
    """The six slot values of ONE insight row, as ((label, value), ...).

    Raises ValueError naming the slot when any value would be empty. That
    is the design's rule made mechanical: a five slot rendering is a test
    failure, not a shorter box, because the missing slot is always CHANGES
    IF and that is the slot that makes the claim falsifiable. The remedy
    is to record the missing field, never to print a shorter box.

    The evidence label is applied HERE, by the renderer, so a claim that
    was only reasoned about cannot reach any surface without saying so.
    A REASONED row additionally carries the literal words "not tested"
    beside its class token: two independent markings, because this is the
    founder's own non negotiable."""
    row = insight or {}
    evidence_class = (row.get("evidence_class") or "REASONED").strip().upper()
    claim = _d(row.get("claim") or "")
    if ic and row.get("insight_id"):
        claim = "%s [i:%s]" % (claim, row["insight_id"])
    evidence = _d(row.get("evidence") or "")
    if evidence_class == "REASONED":
        because = "%s %s [%s, %s]" % (
            bl.evidence_label(evidence_class),
            evidence or "nothing was run to check this",
            evidence_class, _NOT_TESTED)
    else:
        because = "%s %s [%s]" % (bl.evidence_label(evidence_class),
                                  evidence, evidence_class)
        if not evidence:
            because = ""
    alternatives = row.get("alternatives") or []
    if isinstance(alternatives, str):
        alternatives = []
    parts = []
    for alt in alternatives:
        if not isinstance(alt, dict):
            continue
        option = _d(alt.get("option") or "")
        why_not = _d(alt.get("why_not") or "")
        if option and why_not:
            parts.append("%s. Rejected: %s" % (option, why_not))
        elif option:
            parts.append(option)
    instead_of = " ".join(parts)
    confidence = _d(row.get("confidence") or "")
    basis = _d(row.get("confidence_basis") or "")
    if confidence:
        confidence = "%s, %s" % (
            confidence,
            ("because %s" % basis) if basis else "and no basis was recorded")
    slots = ((INSIGHT_SLOTS[0], claim),
             (INSIGHT_SLOTS[1], because),
             (INSIGHT_SLOTS[2], instead_of),
             (INSIGHT_SLOTS[3], _d(row.get("flip_condition") or "")),
             (INSIGHT_SLOTS[4], confidence),
             (INSIGHT_SLOTS[5], bl.HANDBACK_OPTION_TEXT))
    empty = [label for label, value in slots if not (value or "").strip()]
    if empty:
        raise ValueError(
            "this insight cannot be shown as a box: slot(s) %s would be "
            "empty. Six slots or it is not a box; record the missing field "
            "rather than printing a shorter one." % ", ".join(empty))
    return slots


def render_insight_box(insight, medium, ic=False):
    """One insight row as a box, in `medium` ("text" or "html").

    The box never carries a rung above FOR INFO on its own: an insight is
    not an alert. If the same fact needs action, an alert is emitted
    separately and points at the insight; the insight is not promoted.
    A REASONED row carries no rung bar at all, in either medium."""
    if medium not in ("text", "html"):
        raise ValueError("unknown medium %r (allowed: text, html)"
                         % (medium,))
    slots = insight_box_slots(insight, ic=ic)
    row = insight or {}
    reasoned = (row.get("evidence_class")
                or "REASONED").strip().upper() == "REASONED"
    subject = _flat(row.get("subject") or "this project", 60)
    kind = _d(row.get("kind") or "INSIGHT", 24)
    if medium == "text":
        head = "     " if reasoned else "%s FOR INFO   " % RUNG_MARKS[
            "FOR INFO"]
        lines = ["%sINSIGHT, %s, %s" % (head, kind.lower(), subject), ""]
        for label, value in slots:
            lines.append("  %-12s %s" % (label, value))
        return _ascii("\n".join(lines))
    bar = "" if reasoned else ('<span class="%s bm-for-info"></span>'
                               % RUNG_BAR_CLASS)
    items = "".join('<dt>%s</dt><dd>%s</dd>'
                    % (xml_escape(label), xml_escape(value))
                    for label, value in slots)
    return ('<section class="bm-insight%s">%s<h3>Insight, %s, %s</h3>'
            '<dl>%s</dl></section>'
            % (" bm-muted" if reasoned else "", bar,
               xml_escape(kind.lower()), xml_escape(subject), items))


# ---------------------------------------------------------------------------
# 11. ALERTS, DERIVED FROM ROWS AND NEVER STORED
# ---------------------------------------------------------------------------

ALERT_FIELDS = ("rung", "kind", "subject", "row_id", "message", "action",
                "at", "more", "repeats")


def alert(rung, kind, subject, row_id, message, action="", at=""):
    """One alert, validated. Raises ValueError for a rung outside the
    ladder or a kind outside ALERT_KINDS, and when the kind's own rung
    disagrees with the one passed: the rung is a lookup over conditions,
    never an author's feeling about how bad something looks."""
    if rung not in RUNGS:
        raise ValueError("unknown rung %r (allowed: %s)"
                         % (rung, ", ".join(RUNGS)))
    if kind not in ALERT_KINDS:
        raise ValueError(
            "unknown alert kind %r (allowed: %s). A condition that cannot "
            "be expressed as a query over rows is not an alert."
            % (kind, ", ".join(ALERT_KINDS)))
    if RUNG_FOR_KIND[kind] != rung:
        raise ValueError(
            "%s is always %s, never %s: the rung belongs to the condition, "
            "not to the caller" % (kind, RUNG_FOR_KIND[kind], rung))
    return {"rung": rung, "kind": kind, "subject": _d(subject or "", 120),
            "row_id": row_id or "", "message": _d(message or "", 300),
            "action": _d(action or "", 200), "at": at or "",
            "more": 0, "repeats": 0}


def _dedupe(alerts):
    """Collapse alerts sharing (rung, kind, subject, row_id), counting the
    repeats on the survivor. One interrupt per cause: a key already
    present is counted, not re-thrown."""
    out, seen = [], {}
    for a in alerts:
        key = (a["rung"], a["kind"], a["subject"], a["row_id"])
        if key in seen:
            seen[key]["repeats"] += 1
            continue
        copy = dict(a)
        seen[key] = copy
        out.append(copy)
    return out


def _cap(alerts):
    """Apply each rung's cap, counting what was not shown on the last
    alert of that rung. Nothing is dropped in silence: a founder who has
    six things waiting on him is told there are six."""
    out = []
    for rung in RUNGS:
        _delivery, _role, cap = RUNG_DELIVERY[rung]
        rows = [a for a in alerts if a["rung"] == rung]
        if cap is None or len(rows) <= cap:
            out.extend(rows)
            continue
        shown = rows[:cap]
        shown[-1]["more"] = len(rows) - cap
        out.extend(shown)
    return out


def alerts_now(store, project_id, now=None, consented=True):
    """Every alert that is true RIGHT NOW, ordered by rung, deduplicated
    and capped. Pure over rows: nothing is stored, so nothing can go
    stale, there is no dismissal logic and there is no "mark as read"
    state to get wrong.

    `consented` is a parameter rather than a read because consent is not
    a row: it lives in the setup config, and a caller that already holds
    an open store handle has consent by construction. Passing False
    returns exactly the consent alert and touches nothing, so the doorway
    at minute zero can call this with no store at all.

    The counts rung is deliberately NOT computed from this function's own
    output: a findings count that counted itself would change every time
    it was read."""
    if not consented:
        return [alert("NEEDS YOU", "consent",
                      "setting up your project records", "",
                      "Setup consent has not been given, so nothing has "
                      "been written yet.",
                      'Say "go ahead" to create the project files, or '
                      '"not yet".')]
    now = now or bs.now_iso()
    found = []
    found.extend(_needs_you(store, project_id))
    found.extend(_at_risk(store, project_id, now))
    found.extend(_for_info(store, project_id))
    found.extend(_settled(store, project_id))
    ordered = []
    for rung in RUNGS:
        for kind in ALERT_KINDS:
            if RUNG_FOR_KIND[kind] != rung:
                continue
            ordered.extend([a for a in found
                            if a["rung"] == rung and a["kind"] == kind])
    return _cap(_dedupe(ordered))


def _needs_you(store, project_id):
    """Work is stopped until a human acts. Each branch is a query over
    rows that already exist, and if a condition cannot be written as one
    it is not a NEEDS YOU."""
    out = []
    for row in _ranked_decisions(store, project_id):
        out.append(alert(
            "NEEDS YOU", "key-decision",
            _flat(row.get("subject") or "one decision", 80),
            row.get("insight_id") or "",
            "A decision is waiting on you: %s"
            % _d(row.get("claim") or "", 200),
            "Answer it, or hand the work back to yourself.",
            at=row.get("created_at") or ""))
    for step in store.list_human_steps(project_id, resolved=False, raw=True):
        if not (step.get("floor") or "").strip():
            continue
        out.append(alert(
            "NEEDS YOU", "founder-step",
            _flat(step.get("what") or "a step only you can take", 80),
            step.get("step_id") or "",
            "This is one of the five things BrotherMode will never do for "
            "you: %s" % bs.AUTONOMY_FLOOR_DESCRIPTIONS.get(
                step.get("floor"), step.get("floor") or ""),
            _d(step.get("click_path") or "Take the step, then say it is "
               "done.", 200),
            at=step.get("created_at") or ""))
    for row in store.list_alerts(resolved=False, raw=True):
        if not row.get("requires_human"):
            continue
        out.append(alert(
            "NEEDS YOU", "raised-alert",
            _flat(row.get("category") or "something that needs you", 80),
            row.get("alert_id") or "",
            _d(row.get("message") or "", 300),
            _d(row.get("recommended_action") or "", 200),
            at=row.get("created_at") or ""))
    spend = store.spend_totals(project_id)
    if spend.get("verdict") == "hard-stop":
        out.append(alert(
            "NEEDS YOU", "budget-ceiling", "the work budget", project_id,
            "The work budget has reached the limit you agreed: %s and %s."
            % (_against_limit(spend.get("tokens") or 0,
                              spend.get("token_ceiling"), "tokens"),
               _against_limit(spend.get("minutes") or 0,
                              spend.get("minutes_ceiling"), "minutes")),
            "Raise the limit, or stop here."))
    return out


def _at_risk(store, project_id, now):
    """Nothing is stopped, but something is likely to stop or to be
    wrong. Nothing here ever becomes a NEEDS YOU by getting older: a rung
    changes only when its blocking condition becomes true."""
    out = []
    everything = store.list_insights(project_id, raw=True)
    superseded = {r.get("supersedes") for r in everything
                  if r.get("supersedes")}
    for row in everything:
        if row.get("kind") != "RISK":
            continue
        if row.get("insight_id") in superseded:
            continue
        out.append(alert(
            "AT RISK", "open-risk",
            _flat(row.get("subject") or "an open risk", 80),
            row.get("insight_id") or "",
            _d(row.get("claim") or "", 300),
            _d(row.get("flip_condition") or "", 200),
            at=row.get("created_at") or ""))
    run = store.get_run(project_id, raw=True)
    if run:
        for unit in store.list_units(run["run_id"], raw=True):
            for d in store.list_dispatches(unit["unit_id"], raw=True):
                if d.get("status") not in ("DISPATCHED", "RESULT_IN"):
                    continue
                if _seconds_between(d.get("created_at") or "",
                                    now) < STALE_DISPATCH_SECONDS:
                    continue
                out.append(alert(
                    "AT RISK", "stale-dispatch",
                    _flat(unit.get("objective") or unit["unit_id"], 80),
                    d.get("dispatch_id") or "",
                    "A step has been in flight longer than the engine "
                    "expects.",
                    "Ask for a catch-up, or stop the run and re-plan it.",
                    at=d.get("created_at") or ""))
        spend = store.spend_totals(project_id)
        open_units = [u for u in store.list_units(run["run_id"], raw=True)
                      if (u.get("status") or "") not in _UNITS_CLOSED]
        if spend.get("verdict") == "soft-stop" and open_units:
            out.append(alert(
                "AT RISK", "budget-drift", "the work budget", project_id,
                "The work budget is past four fifths of the limit you "
                "agreed and %d step(s) are not accepted yet, so the "
                "estimate no longer covers what is left."
                % len(open_units),
                "Ask for a fresh estimate before the limit is reached."))
    baseline = _clock_baseline(store, project_id)
    if baseline:
        stats = store.active_minutes_since(project_id, baseline, now=now)
        if stats.get("skipped"):
            out.append(alert(
                "AT RISK", "skipped-rows", "the working time count",
                project_id,
                "%d record(s) carry a timestamp this cannot read, so the "
                "working time total left them out." % stats["skipped"],
                "Nothing is lost; ask for a catch-up if the total looks "
                "wrong."))
    return out


def _for_info(store, project_id):
    """True, worth knowing, requires nothing. Counts only: spend belongs
    in a count row and never in an alert, because a token meter measures
    the machine rather than the work."""
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    done = len([u for u in units if u.get("status") == "DONE"])
    spend = store.spend_totals(project_id)
    out = []
    if units:
        out.append(alert(
            "FOR INFO", "count", "steps accepted", project_id,
            "%d of %d steps accepted." % (done, len(units))))
    if spend.get("token_ceiling") is not None:
        out.append(alert(
            "FOR INFO", "count", "the work budget", project_id,
            "%s, and %s."
            % (_against_limit(spend.get("tokens") or 0,
                              spend.get("token_ceiling"), "tokens"),
               _against_limit(spend.get("minutes") or 0,
                              spend.get("minutes_ceiling"), "minutes"))))
    return out


def _settled(store, project_id):
    """A thing that was open is now closed, with what closed it. Quiet and
    uncoloured on purpose."""
    out = []
    everything = store.list_insights(project_id, raw=True)
    superseded = {}
    for row in everything:
        if row.get("supersedes"):
            superseded[row["supersedes"]] = row
    for row in everything:
        if row.get("kind") != "DECISION" or not (row.get("decision_class")
                                                 or ""):
            continue
        closer = superseded.get(row.get("insight_id"))
        if not closer:
            continue
        out.append(alert(
            "SETTLED", "closed-decision",
            _flat(row.get("subject") or "a decision", 80),
            row.get("insight_id") or "",
            "Closed by %s." % ("your handback"
                               if closer.get("kind") == "HANDBACK"
                               else "a later decision"),
            at=closer.get("created_at") or ""))
    for row in store.list_alerts(resolved=True, raw=True):
        out.append(alert(
            "SETTLED", "resolved-alert",
            _flat(row.get("category") or "something that needed you", 80),
            row.get("alert_id") or "",
            _d(row.get("message") or "", 300),
            at=row.get("resolved_at") or ""))
    run = store.get_run(project_id, raw=True)
    if run:
        for unit in store.list_units(run["run_id"], raw=True):
            if unit.get("status") != "DONE":
                continue
            out.append(alert(
                "SETTLED", "step-passed",
                _flat(unit.get("objective") or unit["unit_id"], 80),
                unit["unit_id"], "This step passed its check.",
                at=unit.get("updated_at") or ""))
    return out


def _clock_baseline(store, project_id):
    """The point the working time count is measured from: the newest
    catch-up, or the project's own start when there has never been one."""
    latest = store.latest_briefing(project_id, raw=True)
    if latest and latest.get("created_at"):
        return latest["created_at"]
    project = store.get_project(project_id, raw=True) or {}
    return project.get("created_at") or ""


def _seconds_between(start_iso, end_iso):
    """Seconds between two recorded timestamps, or 0 when either does not
    parse. Never a guess: a row this cannot read is treated as not old
    rather than as very old, so an unreadable timestamp can never
    manufacture an alert."""
    start = bs.parse_iso_stamp(start_iso)
    end = bs.parse_iso_stamp(end_iso)
    if start is None or end is None:
        return 0
    return int((end - start).total_seconds())


def alerts_for_chat(alerts):
    """The subset of `alerts` one chat message may carry: at most two
    distinct rungs, in ladder order. Several alerts at once create a bad
    experience, and the cap is the mitigation every source in the research
    agrees on."""
    rungs, out = [], []
    for a in alerts:
        if a["rung"] not in rungs:
            if len(rungs) == 2:
                continue
            rungs.append(a["rung"])
        out.append(a)
    return out


def render_alert(alert_row, medium):
    """One alert, in `medium` ("text" or "html").

    Four non colour channels survive with no styling at all: the WORD, the
    mark, the indent and (in the page) the bar weight and style. The ARIA
    role comes from RUNG_DELIVERY, so only the top rung is announced."""
    if medium not in ("text", "html"):
        raise ValueError("unknown medium %r (allowed: text, html)"
                         % (medium,))
    rung = alert_row["rung"]
    _delivery, role, _cap_size = RUNG_DELIVERY[rung]
    message = alert_row["message"]
    action = alert_row["action"]
    tail = ""
    if alert_row.get("more"):
        tail = (" 1 more like this is waiting." if alert_row["more"] == 1
                else " %d more like this are waiting." % alert_row["more"])
    elif alert_row.get("repeats"):
        tail = (" Seen once more." if alert_row["repeats"] == 1
                else " Seen %d more times." % alert_row["repeats"])
    if medium == "text":
        lines = ["%s %-10s %s%s" % (RUNG_MARKS[rung], rung, message, tail)]
        if action:
            lines.append("%s %-10s %s" % (" " * len(RUNG_MARKS[rung]), "",
                                          action))
        return _ascii("\n".join(lines))
    token = RUNG_TOKEN[rung]
    classes = "bm-alert" + ((" bm-" + token) if token else " bm-settled")
    role_attr = (' role="%s"' % role) if role else ""
    body = '<strong>%s</strong> %s%s' % (xml_escape(rung),
                                         xml_escape(message),
                                         xml_escape(tail))
    if action:
        body += '<p class="bm-action">%s</p>' % xml_escape(action)
    return '<div class="%s"%s>%s</div>' % (classes, role_attr, body)


# ---------------------------------------------------------------------------
# 12. THE SURFACE ROUTER
#
# Section 3.3, a first match router, so two runs on the same rows always
# agree. An item that matches no branch has no job and raises, so a new
# item kind cannot slip in as noise.
# ---------------------------------------------------------------------------

SURFACES = ("S1", "S2", "S3", "S4")

# Branch 1: the founder must act before anything can move.
MUST_ACT_KINDS = ("open-key-decision", "failed-gate", "consent-missing",
                  "founder-step", "budget-ceiling")

# Branch 2: he asked a question in the chat just now.
ASKED_KINDS = ("status-answer", "next-answer", "explain-answer")

# Branch 3: state rather than an ask, and worth returning to. Renamed
# from "timeline" to "catchup-story" when the progress surface loop added
# the timeline SHAPE to SHAPES (section 5): the two are different
# namespaces this router treats as one flat set on purpose (branch 3 vs
# branch 4 must stay disjoint, per the test that walks all four groups
# together), and "catchup-story" is what this entry always meant, the
# story-so-far VIEW_SECTIONS anchor, not the new drawn shape.
STATE_KINDS = ("progress", "pipeline-state", "gate-ladder", "pen-holder",
               "insight-history", "catchup-story", "handback-offer")


def surface_for(item):
    """Which surfaces `item` belongs on, as a tuple of surface ids.

        S1  the generated file, always available
        S2  the published page, five gates can make it unavailable
        S3  the terminal text, always available
        S4  the native dialogs and the hook message

    Raises ValueError for an item kind that matches no branch. That is
    deliberate: something with no job is not shown, and a default would
    turn every future item into noise."""
    kind = (item or {}).get("kind")
    if kind in MUST_ACT_KINDS:
        # Never only in the page: a page he has not opened is not a
        # channel.
        return ("S4", "S3", "S1")
    if kind in ASKED_KINDS:
        return ("S3",)
    if kind in STATE_KINDS:
        return ("S1", "S2")
    if kind in SHAPES:
        return ("S1",)
    raise ValueError(
        "no surface for item kind %r. Anything that matches no branch has "
        "no job, so it is not shown rather than defaulted somewhere."
        % (kind,))


# ---------------------------------------------------------------------------
# 13. EVERY REFUSAL, REWRITTEN
#
# L-S9. Keyed by the reason code a founder can meet, valued by
# (context, hint, next_action):
#
#   context      what was being attempted, in the founder's words
#   hint         the cause in plain language, never in the system's terms
#   next_action  one thing he can say or do
#
# TWO MODULES EMIT THOSE CODES, so two scanners enumerate them.
# tools/test_bm_visual.py reads bm_store.py's OwnershipRefused
# constructions and bm_controller.py's module-level UNATTENDED_ code
# constants, both with ast off the shipped source, and fails on any code
# with no entry here as well as on any entry here that neither module can
# emit. So this map cannot drift in either direction for either module,
# which is what makes L-S9 mechanical rather than aspirational.
#
# The controller half was added after its seven unattended refusals
# shipped rewritten in bm_controller.py but absent here, which is how
# `bm_view.py explain --reason unattended-fence-advisory` came to answer
# that it was not a reason this product emits.
#
# The wording follows references/terminology.md: your project's records
# rather than the store, your recorded approval rather than a receipt,
# task history rather than a work record, a catch-up rather than a
# briefing.
# ---------------------------------------------------------------------------

REFUSAL_HELP = {
    "absolute-write-scope": (
        "setting which files a step is allowed to change",
        "one of the file patterns is a full path from the top of the "
        "disk, and only paths inside your project are allowed",
        "Say which folder inside the project that step should write in."),
    "alert-needs-severity": (
        "recording something that needs your attention",
        "the note carries no level, so nothing can decide whether to "
        "interrupt you with it",
        "Ask me to record it again and say how urgent it is."),
    "bad-capability-receipt": (
        "recording what another tool did for you",
        "the record is missing something it cannot be filed without, like "
        "which tool did the work or what it was asked to do",
        "Ask me to file it again naming the tool, its version and the task."),
    "bad-criterion": (
        "linking a piece of proof to the thing it proves",
        "the check it points at is not one of the acceptance checks on that "
        "piece of work, so the link would claim something that was never "
        "agreed",
        "Ask to see the acceptance checks for that work, then link to one "
        "of those."),
    "unproved-verification": (
        "recording that another tool's work was independently checked",
        "the record says the work was checked and passed, but it does not "
        "say who or what did the checking, or point at anything that could "
        "be run again, so the word checked would be carrying no weight",
        "Ask me to file it again naming what did the check and what it "
        "produced. If nothing was actually re-run, the honest answer is "
        "that there was nothing here that could be checked."),
    "bad-verification-state": (
        "recording whether another tool's work was independently checked",
        "the record has to say one of exactly three things: it was checked "
        "and passed, it was checked and failed, or there was nothing here "
        "that could be checked",
        "Ask me to file it again saying which of those three happened. If "
        "the tool only gave an opinion with nothing to re-run, the honest "
        "answer is the third one."),
    "already-answered": (
        "answering a question BrotherMode had paused on",
        "that question already has your answer on record",
        "Ask for the catch-up to see what was decided."),
    "already-cancelled": (
        "cancelling a piece of work that had been set aside",
        "it was already cancelled, so there is nothing left to cancel",
        "Ask for the status to see what is still open."),
    "already-promoted": (
        "turning a draft piece of work into a real one",
        "it was already turned into a real one",
        "Ask for the status to see where it stands."),
    "already-resolved": (
        "closing something that needed attention",
        "it was already closed, and closing it twice would leave two "
        "different stories on record",
        "Ask for the status to see what is still open."),
    "already-resulted": (
        "recording the result of a step",
        "that step already has a result on record, and results are never "
        "overwritten",
        "Ask for the status, or start a fresh attempt."),
    "already-verified": (
        "checking a step that has just finished",
        "that step was already checked, and a second check would replace "
        "evidence rather than add to it",
        "Ask for the status to see what the check found."),
    "ambiguous": (
        "finding the thing you named",
        "the name you gave matches more than one record",
        "Give me a few more characters of the name."),
    "ambiguous-anchor": (
        "attaching a note to a place in a file",
        "the place you described matches more than one spot in that file",
        "Name the line, or quote a longer piece of the text."),
    "ambiguous-identity": (
        "working out which piece of work you meant",
        "more than one open piece of work answers to that name",
        "Ask for the status and name the one you want."),
    "anchor-line-out-of-range": (
        "attaching a note to a line in a file",
        "that file does not have a line with that number",
        "Open the file, pick a line that exists, and try again."),
    "anchor-not-found": (
        "attaching a note to a place in a file",
        "the text you described is not in that file any more",
        "Quote a piece of text that is in the file today."),
    "bad-anchor-line": (
        "attaching a note to a line in a file",
        "the line you gave is not a whole positive number",
        "Give me a line number, or quote the text instead."),
    "bad-approval-receipt": (
        "acting on something you approved",
        "your recorded approval does not match the thing being changed, "
        "so it would be used for something you did not agree to",
        "Approve this change on its own and I will act on that."),
    "ambiguous-target": (
        "erasing leftover notes from a project that is already gone",
        "you named a project AND asked for all of them at once, and those "
        "are two different jobs, so nothing was erased",
        "Name one project, or ask for all of them, not both."),
    "bad-artifact-url": (
        "saving the link to your project page",
        "the link is not a secure web address, and anything else could "
        "point a click somewhere it should not go",
        "Ask me to publish the page again and I will record the real "
        "link."),
    "bad-confirmation": (
        "erasing a project and everything recorded about it",
        "the confirmation you typed does not match the project name, so "
        "nothing was erased",
        "Type the project name exactly if you really want it erased."),
    "bad-decision": (
        "recording a decision",
        "the decision is missing the part that says what was decided",
        "Tell me what was decided and I will record it."),
    "bad-disposition": (
        "recording what happened to a suggestion",
        "the outcome you gave is not one of the ones this understands",
        "Say whether it was used, ignored or overruled."),
    "bad-evidence": (
        "recording the proof behind a claim",
        "the proof is missing the command or the measurement it rests on",
        "Tell me what was run or measured and I will record that."),
    "bad-fingerprint": (
        "recording that your project page was written",
        "the content stamp for the page is not in the shape this expects, "
        "so it could not be compared with the last one",
        "Ask me to write the page again."),
    "bad-numeric-field": (
        "recording a number about the work",
        "the value is not a whole number, and a number nobody can compare "
        "is worse than no number",
        "Give me the figure as a plain number."),
    "bad-origin": (
        "recording proof behind a claim",
        "the record does not say, in a way this understands, whether the "
        "proof came from a check that actually ran, from something "
        "watched happening, or from something recovered, imported or "
        "guessed at",
        "Tell me whether it was actually run or observed, or whether it "
        "is a recovered, imported or inferred record standing in for "
        "one."),
    "bad-outcome": (
        "recording what a piece of work led to",
        "the outcome you gave is not one of the ones this understands",
        "Say whether it worked, did not work, or is still open."),
    "bad-path": (
        "reading or writing a file for you",
        "the path points outside your project, and nothing outside it is "
        "touched",
        "Name a file inside the project folder."),
    "bad-reality-kind": (
        "recording what happened after a release",
        "the kind of entry is not one this record understands: it takes an "
        "acceptance, a reopen, a rollback, an incident or a defect",
        "Say which of those five this is, and I will record it."),
    "bad-relation": (
        "linking two rules together",
        "the link you asked for is not one of the ones this understands",
        "Say whether one replaces, supports or contradicts the other."),
    "bad-resolution": (
        "closing a note",
        "the closing text is missing, so the record would say it closed "
        "without saying how",
        "Tell me in one sentence how it was settled."),
    "bad-runtime-run": (
        "recording a run of another coding tool",
        "the record is missing something it needs, so it would not be "
        "traceable later",
        "Ask me to record it again."),
    "bad-scope": (
        "setting what a step is allowed to touch",
        "the list of files is not in a shape this can check",
        "Name the files or folders one by one."),
    "bad-scope-container": (
        "setting what a step is allowed to touch",
        "the list arrived as a single lump rather than as separate "
        "entries, so a typo could widen it silently",
        "Name the files or folders one by one."),
    "bad-source-type": (
        "recording where something was learned from",
        "the source you named is not one of the ones this understands",
        "Say whether it came from a correction, a result or a note."),
    "bad-state-change-kind": (
        "changing the state of a rule",
        "the change you asked for is not one of the ones this understands",
        "Say whether you want it paused, retired or brought back."),
    "bad-state-change-receipt": (
        "changing the state of a rule",
        "your recorded approval was for a different change",
        "Approve this change on its own and I will make it."),
    "bad-view-kind": (
        "recording that a page was written",
        "the kind of page named is not one this product generates",
        "Ask for the project page, or the page for a handed back "
        "decision."),
    "calibration-incomplete": (
        "recording that a check was proven to work",
        "it does not say what was deliberately broken, or what the check "
        "then reported, and without both nobody can tell the check works",
        "Tell me what was broken and how many checks went red."),
    "cap": (
        "starting another piece of work",
        "there are already as many open at once as this allows, and more "
        "at once means less finished",
        "Finish or park one of the open ones first."),
    "closed-record": (
        "adding to a piece of work",
        "that piece of work is closed, and closed work is never edited",
        "Start a new one, or ask me to reopen the old one."),
    "dangling-dependency": (
        "planning the order of the work",
        "one step waits on another that does not exist",
        "Ask me to re-plan the work."),
    "db-busy": (
        "reading or updating your project records",
        "something else is writing to them at this exact moment",
        "Wait a moment and ask again."),
    "defect-without-intent": (
        "entering a defect found after a release",
        "a defect has to turn into a new piece of work, and this one opened "
        "none, so it would be noticed once and then lost",
        "Ask me to enter it again so it opens a piece of work you can pick up."),
    "dependency-cycle": (
        "planning the order of the work",
        "two steps each wait on the other, so neither could ever start",
        "Ask me to re-plan the work."),
    "dispatch-cancelled": (
        "recording the result of a step",
        "that step was called off before the result arrived, so the "
        "result belongs to work that is no longer part of the plan",
        "Ask for the status to see the plan as it stands."),
    "dispatch-exists": (
        "starting a step",
        "that step is already in flight, and starting it twice would put "
        "two workers on one file",
        "Ask for the status to see what is running."),
    "duplicate-rule": (
        "saving a new rule",
        "a rule that says the same thing already exists",
        "Ask to see the rule that is already there."),
    "empty-note": (
        "saving a note",
        "the note has no text in it",
        "Tell me what the note should say."),
    "empty-signer": (
        "recording what you authorised",
        "nobody is named as having authorised it, and an authorisation "
        "with no name behind it is not one",
        "Tell me your name and I will record it."),
    "empty-write-scope": (
        "letting a step change files",
        "no files were named, so the step could either change nothing or "
        "change anything",
        "Name the files or folders that step may change."),
    "evidence-missing": (
        "recording a claim as checked",
        "it claims to have been run or measured but names no command and "
        "no number, so it is reasoning rather than proof",
        "Tell me what was run, or let me record it as reasoning."),
    "foreign-briefing": (
        "linking a catch-up to the one before it",
        "the earlier catch-up belongs to a different project",
        "Ask for a fresh catch-up on this project."),
    "foreign-insight": (
        "linking one decision to another",
        "the decision named belongs to a different project",
        "Name a decision from this project."),
    "git-exposed-store": (
        "opening your project records",
        "the folder holding them is in a state where a routine save could "
        "publish them, and they hold your own words",
        "Add .brothermode/ to the ignore file, then ask again."),
    "git-state-unknown": (
        "opening your project records",
        "whether a routine save would publish them cannot be worked out "
        "here, and this refuses rather than risk it",
        "Ask me to check the folder setup."),
    "git-tracked-store": (
        "opening your project records",
        "they are currently tracked for publishing, and they hold your "
        "own words",
        "Ask me to untrack them, then try again."),
    "glob-write-scope": (
        "setting what a step may change",
        "one entry is a pattern rather than a real path, so what it "
        "covers would change as files appear",
        "Name the folder instead of the pattern."),
    "handback-already-taken": (
        "handing a decision back to you",
        "you already took that one over, and control changes hands once",
        "Ask for the status to see what is now yours."),
    "handback-not-offered": (
        "recording a decision that needs you",
        "it does not offer to hand the work back, and a decision that "
        "reaches you must always offer that",
        "Ask me to record it again with the offer included."),
    "handback-without-decision": (
        "handing work back to you",
        "it does not name which decision you are taking over",
        "Tell me which decision you want to take."),
    "illegal-state-move": (
        "moving the work to its next stage",
        "that move is not one the work can make from where it is now",
        "Ask for the status to see where it stands."),
    "incomplete-rule": (
        "saving a rule",
        "the rule is missing a part it needs, so it could never be "
        "applied",
        "Tell me when it applies and what it should do."),
    "lifetime-mismatch": (
        "carrying on a piece of work",
        "it was set up as a different kind of work than this expects",
        "Ask for the status and start the right kind."),
    "live-contract-exists": (
        "recording new limits for the work",
        "limits are already in force, and two sets at once would mean "
        "nobody knows which one holds",
        "Ask me to replace the ones in force."),
    "live-session-adopt-blocked": (
        "picking up a piece of work from another session",
        "another session still holds it, and two at once is the thing "
        "that loses work",
        "Wait for that session to finish, or ask me to release it."),
    "missing-evidence": (
        "accepting a piece of work",
        "nothing is on record showing it was checked",
        "Ask me to run the check first."),
    "model-signer": (
        "recording what you authorised",
        "the name on it is a machine, and only you can authorise this",
        "Tell me your own name and I will record it."),
    "name-active": (
        "starting a piece of work under that name",
        "an open piece of work already has that name",
        "Pick another name, or ask for the status of the open one."),
    "no-accepted-release": (
        "recording something that happened after a release, like a rollback "
        "or an incident",
        "it points back at a release nobody recorded accepting, so there is "
        "nothing for it to hang from",
        "Record who accepted that release first, then enter this against it."),
    "no-alternative": (
        "recording a decision that needs you",
        "it names nothing that was weighed against it, and the road not "
        "taken is the point of a decision like this",
        "Tell me what else was considered and why it was not chosen."),
    "no-anchor": (
        "attaching a note to a place in a file",
        "no place was named",
        "Tell me the file, and the line or the text to attach it to."),
    "no-approval-receipt": (
        "acting on a rule you approved",
        "there is no record of your approval, and this never acts on one "
        "without it",
        "Approve it and I will act on that."),
    "no-artifact": (
        "accepting a piece of work",
        "the thing it was supposed to produce is not there",
        "Ask for the status to see what the step actually produced."),
    "no-contract": (
        "working within the limits you set",
        "no limits have been recorded for this project yet",
        "Tell me the outcome and the limits, and I will record them."),
    "no-edit-receipt": (
        "changing a rule",
        "there is no record of your approval for this edit",
        "Approve the edit and I will make it."),
    "no-flip-condition": (
        "recording a decision",
        "it names nothing that would change it, and a decision nothing "
        "could change is not a decision",
        "Tell me what would make you decide differently."),
    "no-founder-ref": (
        "recording your approval",
        "it does not say where your approval came from",
        "Approve it again and I will record where it came from."),
    "no-founder-response": (
        "recording your approval",
        "your own words are not on the record, and an approval nobody "
        "said is not one",
        "Say yes or no in your own words and I will record it."),
    "no-ignore-reason": (
        "setting a suggestion aside",
        "no reason was given, and a reason is what makes it reviewable "
        "later",
        "Tell me in one sentence why it is being set aside."),
    "no-live-contract": (
        "recording work done against your limits",
        "the limits you set are not in force right now",
        "Say the word and I will put them back in force."),
    "no-note-author": (
        "saving a note",
        "nobody is named as having written it",
        "Tell me who it is from."),
    "no-reason": (
        "making the change",
        "no reason was given, and the record would say what changed "
        "without saying why",
        "Tell me in one sentence why."),
    "no-resolution": (
        "closing a note",
        "it does not say how the thing was settled",
        "Tell me in one sentence how it was settled."),
    "no-root": (
        "finding your project folder",
        "no project folder is set, so there is nothing to read or write",
        "Run the setup, or tell me which folder this project lives in."),
    "no-source-candidate": (
        "turning a suggestion into a rule",
        "the suggestion it came from is not on record",
        "Ask to see the suggestions that are waiting."),
    "no-state-change-receipt": (
        "changing the state of a rule",
        "there is no record of your approval for this change",
        "Approve it and I will make the change."),
    "no-store": (
        "opening your project records",
        "this project has no records yet",
        "Say the word and I will set them up."),
    # Built by _read_only_refusal rather than raised with a literal code, so
    # the guard that walks literal raises in tools/test_bm_visual.py cannot
    # see it. It was the one reason code with no entry here, which made
    # "explain --reason store-unreadable" answer that the product never emits
    # it. It does.
    "store-unreadable": (
        "reading your project records without changing anything",
        "the records could not be opened for reading, and the records "
        "themselves are fine: nothing was written, moved or renamed. This "
        "happens when the folder they live in is not writable, because "
        "reading them still needs a small scratch file alongside them",
        "Run any normal command once in a place you can write to, which "
        "tidies that away, or copy the whole records folder somewhere "
        "writable and look at it there."),
    "no-successor": (
        "retiring a rule",
        "nothing is named to take its place, and retiring it would leave "
        "a hole",
        "Tell me what should replace it, or say to retire it outright."),
    "no-such-receipt": (
        "acting on your approval",
        "the approval named is not on record",
        "Approve it again and I will act on that."),
    "no-supporting-evidence": (
        "promoting a rule",
        "nothing on record supports it yet",
        "Let it collect evidence first, or approve it outright."),
    "no-work-identity": (
        "starting the work",
        "no open piece of work is on record to attach this to",
        "Ask me to start one."),
    "not-atomic": (
        "saving a file safely",
        "the save could not be done in one go on this system, and a "
        "half written file is worse than no file",
        "Check the disk has space, then ask again."),
    "not-found": (
        "finding what you named",
        "there is no record with that name",
        "Ask for the status to see what is on record."),
    "no-target": (
        "erasing leftover notes from a project that is already gone",
        "you did not say which project, and this erases things for good, "
        "so it will not guess",
        "Name the project, or ask for all of them."),
    "not-orphan": (
        "erasing leftover notes from a project that is already gone",
        "that project still exists, so its notes are not leftovers and "
        "this is the wrong tool for them",
        "Erase the project itself if that is what you meant."),
    # L09 (2026-08-06): the authorisation narrowings landed four new codes.
    "no-run": (
        "finding the run to act on",
        "this project has no run on record to drive or take over",
        "Ask for the status, or start the work first."),
    "run-terminal": (
        "taking over a run",
        "that run has already finished, so there is nothing to take over",
        "Read its summary on the project page; start new work if more is "
        "needed."),
    "path-is-floor": (
        "signing the work authorisation",
        "the authorisation asked to write inside the project's own safety "
        "machinery, which nothing may ever grant",
        "Remove that path from the authorisation and sign again."),
    "write-scope-is-floor": (
        "planning a step of the work",
        "a step asked to write inside the project's own safety machinery, "
        "which nothing may ever grant",
        "Point that step at the folders the work really needs, then plan "
        "again."),
    "no-write-scope": (
        "signing the work authorisation",
        "the authorisation grants writing work but does not say where "
        "writing is allowed",
        "Name the folders the work may touch, or grant only read-only "
        "work."),
    "not-owner": (
        "changing a piece of work",
        "another session holds it, and only one thing edits at a time",
        "Ask for the status to see who holds it."),
    "not-pending": (
        "acting on a suggestion",
        "it is not waiting for a decision any more",
        "Ask to see what is still waiting."),
    "overlap": (
        "starting a piece of work",
        "it would edit a file another open piece of work already holds, "
        "and only one worker edits a file at a time",
        "Finish or park the other one first."),
    "path-escape": (
        "reading or writing a file for you",
        "the path leads outside your project, or through a shortcut that "
        "does not point where it says",
        "Name a real file inside the project folder."),
    "pathspec-write-scope": (
        "setting what a step may change",
        "one entry uses a pattern language rather than naming a file, so "
        "what it covers is not what it looks like",
        "Name the file or the folder plainly."),
    "project-mismatch": (
        "attaching this to your project",
        "the two things named belong to different projects",
        "Name things from the same project."),
    "provisional-cross-session": (
        "picking up a draft piece of work",
        "it belongs to another session, and drafts do not cross between "
        "them",
        "Start a fresh one here."),
    "receipt-already-used": (
        "acting on your approval",
        "that approval was already used once, and reusing it would apply "
        "your yes twice",
        "Approve it again if you want it done again."),
    "receipt-expired": (
        "acting on your approval",
        "it is old enough that it may no longer reflect what you want",
        "Approve it again and I will act straight away."),
    "receipt-stale-candidate": (
        "acting on your approval",
        "the suggestion changed after you approved it",
        "Look at it again and approve the new version."),
    "receipt-stale-edit": (
        "acting on your approval",
        "the rule changed after you approved the edit",
        "Look at it again and approve the new version."),
    "receipt-stale-target": (
        "acting on your approval",
        "the thing you approved changed after you approved it",
        "Look at it again and approve the new version."),
    "receipt-wrong-candidate": (
        "acting on your approval",
        "your approval was for a different suggestion",
        "Approve this one on its own."),
    "receipt-wrong-kind": (
        "acting on your approval",
        "your approval was for a different kind of change",
        "Approve this change on its own."),
    "receipt-wrong-target": (
        "acting on your approval",
        "your approval named something else",
        "Approve this one on its own."),
    "risk-class-is-floor": (
        "pre-approving a kind of action",
        "it is one of the five things BrotherMode never does on its own, "
        "and no permission can grant it",
        "You do that step yourself and tell me when it is done."),
    "root-ambiguous": (
        "finding your project folder",
        "more than one folder answers to that, so this refuses rather "
        "than guess",
        "Tell me the full path of the project folder."),
    "root-outside-start": (
        "finding your project folder",
        "a setting left over from another project points somewhere that "
        "does not contain the folder this work is about, and following it "
        "would file this work in that other project's records",
        "Tell me which project you mean, or ask me to clear that leftover "
        "setting and use the folder you are in."),
    "run-exists": (
        "starting the work",
        "a run is already open for this project, and two at once would "
        "make progress unreadable",
        "Ask for the status, or stop the open one first."),
    "run-not-in-project": (
        "attaching a step to the work",
        "the run named belongs to a different project",
        "Ask for the status of this project."),
    "schema-ahead": (
        "opening your project records",
        "they were written by a newer version of BrotherMode than the one "
        "running here",
        "Update BrotherMode, then try again."),
    "schema-behind": (
        "opening your project records",
        "they are older than this version expects and could not be "
        "brought forward",
        "Ask me to check them and I will report what is there."),
    "self-edge": (
        "linking two rules",
        "a rule cannot be linked to itself",
        "Name the other rule."),
    "self-supersession": (
        "replacing a rule",
        "a rule cannot replace itself",
        "Name the rule that replaces it."),
    "successor-cannot-speak": (
        "retiring a rule in favour of another",
        "the replacement is not in a state where it can take over",
        "Approve the replacement first."),
    "supersession-cycle": (
        "replacing a rule",
        "the replacements loop back to where they started, so no rule "
        "would be the current one",
        "Name a rule that is not already in that chain."),
    "unaccountable-acceptance": (
        "recording that you accepted a release",
        "the record carries no name, and nobody accepts a release "
        "anonymously: months later the only question anyone asks is who "
        "said yes",
        "Ask me to record it again and say who accepted it."),
    "unacknowledged-quarantine": (
        "opening your project records",
        "a damaged copy was set aside earlier and nobody has said what to "
        "do about it",
        "Ask me what was set aside and I will explain the options."),
    "unit-id-taken": (
        "planning a step",
        "another step already has that name in this run",
        "Ask me to re-plan with distinct names."),
    "unit-not-claimable": (
        "starting a step",
        "it is not ready to start yet: something it waits on is not done",
        "Ask for the status to see what it is waiting on."),
    "unit-not-claimed": (
        "working on a step",
        "nothing holds that step, so there is no worker to work on it",
        "Ask for the status to see what is running."),
    "unit-not-verifiable": (
        "checking a step",
        "it has no result to check yet",
        "Ask for the status to see where it stands."),
    "unknown-anchor-type": (
        "attaching a note",
        "the kind of place you named is not one this understands",
        "Attach it to a file, or to the project as a whole."),
    "unknown-author-kind": (
        "saving a note",
        "the kind of author named is not one this understands",
        "Say whether it is from a person or from a machine."),
    "unknown-field": (
        "recording a page that was written",
        "the record names a field this does not have, which is almost "
        "always a typo, and a quiet drop would lose it",
        "Ask me to write the page again."),
    "unknown-note-kind": (
        "saving a note",
        "the kind of note named is not one this understands",
        "Say whether it is a question, a warning or a comment."),
    "unknown-severity": (
        "recording something that needs attention",
        "the level named is not one this understands",
        "Say how urgent it is in plain words."),
    "unreadable-scope-path": (
        "setting what a step may change",
        "one of the paths cannot be read from here, so what it covers "
        "cannot be checked",
        "Name a folder that exists in the project."),
    "unresolved-contradiction": (
        "promoting a rule",
        "another rule on record says the opposite, and both cannot hold",
        "Tell me which one wins and I will retire the other."),
    "unresolved-critical-alert": (
        "moving the work forward",
        "something marked as needing you is still open, and it blocks "
        "this",
        "Ask for the status and settle that one first."),
    "use-supersede": (
        "editing a rule",
        "rules are never edited in place, so the history stays readable",
        "Ask me to replace it with a new version."),
    "view-markers-damaged": (
        "updating a generated part of a document",
        "the markers that show where the generated part begins and ends "
        "have been changed by hand, so writing there could overwrite your "
        "own text",
        "Put the markers back, or ask me to write the file fresh."),
}

# RF-4 (GAP-19): the UNATTENDED preflight's entries are folded in from
# tools/bm_messages.py's shared catalog, the same object
# tools/bm_controller.py's own UNATTENDED_REFUSAL_HELP is assigned from,
# rather than retyped here. dict.update copies the VALUE REFERENCES, so
# REFUSAL_HELP[code] for each of these codes is the identical tuple object
# bm_controller.py's own map holds, not a second one that merely reads the
# same today. tools/test_bm_visual.py asserts that identity and fails on
# any drift.
#
# WHY THE CATALOG AND NOT AN IMPORT OF bm_controller.py DIRECTLY: the
# controller is one of the two shipping modules allowed to import
# subprocess, and tools/bm_view.py loads THIS file to render a page, so
# sharing the map by importing bm_controller.py would pull subprocess into
# the render path. tools/bm_messages.py owns nothing but string constants,
# so loading it here costs nothing of the sort.
REFUSAL_HELP.update(M.UNATTENDED_REFUSAL_HELP)

# The five parts of a failure block, in order. Part 5 is the ONLY place a
# raw log may ever appear, in any surface: it is pull, not push, and the
# founder cannot read logs and should never have to.
FAILURE_BLOCK_PARTS = ("What I was doing", "What happened", "Why",
                       "What you can do", "What remains safe")

EXPANDER_LABEL = "show me exactly what happened"

_SAFE_SENTENCE = ("Nothing was written. Your project records are exactly "
                  "as they were before this.")


def failure_block(reason_code, attempted, raw, medium="text"):
    """The five part block a founder sees when something refuses. No log
    is printed: he cannot read logs and should never have to.

    The parts are the union of the two shapes this product already uses,
    which are not competing formats. Design section 9.2 asks for context,
    the thing itself, a hint in plain language, one next action and ONE
    expander; references/kickoff.md's error card asks for what happened,
    the impact, the recommended action and what remains safe. "What
    remains safe" is mandatory in both, and the expander is the only place
    a raw log may ever appear, in any surface.

    Raises KeyError for a code with no entry, deliberately: a refusal that
    reached a founder as a raw code is the failure L-S9 exists to prevent,
    so a missing entry has to be loud here rather than quiet on his
    screen.

    `attempted` is what he asked for in his own words; `raw` is the
    engine's own message, which appears ONLY inside the one expander."""
    if medium not in ("text", "html"):
        raise ValueError("unknown medium %r (allowed: text, html)"
                         % (medium,))
    if reason_code not in REFUSAL_HELP:
        raise KeyError(
            "no founder facing rewrite for the reason code %r; every code "
            "the engine can emit needs one" % (reason_code,))
    context, hint, next_action = REFUSAL_HELP[reason_code]
    attempted = _d(attempted or context, 200)
    if medium == "text":
        lines = [
            "%s: %s" % (FAILURE_BLOCK_PARTS[0], attempted),
            "%s: BrotherMode stopped while %s."
            % (FAILURE_BLOCK_PARTS[1], context),
            "%s: %s." % (FAILURE_BLOCK_PARTS[2], hint),
            "%s: %s" % (FAILURE_BLOCK_PARTS[3], next_action),
            "%s: %s" % (FAILURE_BLOCK_PARTS[4], _SAFE_SENTENCE),
            "",
            "  %s:" % EXPANDER_LABEL,
            "    %s" % _d(raw or "nothing more was reported", 600),
        ]
        return _ascii("\n".join(lines))
    return (
        '<section class="bm-failure">'
        '<p><strong>%s:</strong> %s</p>'
        '<p><strong>%s:</strong> BrotherMode stopped while %s.</p>'
        '<p><strong>%s:</strong> %s.</p>'
        '<p><strong>%s:</strong> %s</p>'
        '<p><strong>%s:</strong> %s</p>'
        '<details><summary>%s</summary><pre>%s</pre></details>'
        '</section>'
        % (xml_escape(FAILURE_BLOCK_PARTS[0]), xml_escape(attempted),
           xml_escape(FAILURE_BLOCK_PARTS[1]), xml_escape(context),
           xml_escape(FAILURE_BLOCK_PARTS[2]), xml_escape(hint),
           xml_escape(FAILURE_BLOCK_PARTS[3]), xml_escape(next_action),
           xml_escape(FAILURE_BLOCK_PARTS[4]), xml_escape(_SAFE_SENTENCE),
           xml_escape(EXPANDER_LABEL),
           xml_escape(_d(raw or "nothing more was reported", 600))))

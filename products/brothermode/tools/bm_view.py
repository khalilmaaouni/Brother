#!/usr/bin/env python3
"""bm_view.py: the live project view and its CLI (loop L05,
docs/program/absolute-lead/DESIGN-visual-surface.md sections 3, 4, 8, 9.3,
11.4 and 12.2).

WHAT THIS IS
  The THIRD renderer over L04's one collector, and the page it writes:
  PROJECT-VIEW.html, one self contained file at the project root, written
  through bs.write_generated_document, generated from store rows and never
  hand maintained. It issues no SQL of its own, defines no collector of
  its own (an ast guard in tools/test_bm_view.py fails the build on
  either), and draws nothing itself: every picture comes from
  tools/bm_visual.py, every one of the eight status fields comes from
  tools/bm_lead.py's collect_status, and every duration comes from
  bl.render_forecast_lines. The moment this file needs a query the store
  does not already offer, it has become a second writer and the query
  belongs in bm_store.py.

THE ONE DOOR, AND WHY IT IS THE FIRST THING IN THIS FILE
  Founder decision, 2026-08-05, unchanged from L04: NOTHING renders or
  writes before setup consent. _store_or_refuse is the ONLY function in
  this file that constructs a store, and it refuses without consent.
  main() computes the consent state ONCE, before the COMMANDS lookup, and
  refuses the whole dispatch when it is false: a human-invoked command
  prints the setup sentence, the two hook-wired invocations (render
  --if-stale and alert --tick) print nothing at all, and doorway is the
  ONE subcommand exempt from the gate, because it writes nothing, opens
  no store, and exists precisely so minute zero has an answer.

SUBCOMMANDS
  render      writes PROJECT-VIEW.html from rows; --if-stale rewrites it
              only when the fingerprint moved, silently, for the hook
  url         reads or records the published page address for a view
  doorway     the minute zero block: no store, no consent, no writes
  explain     one refusal reason code, rewritten as the five part block
  brief-page  the developer brief as one HTML page beside its markdown
  alert       --tick, the hook entry point: one JSON object when a
              NEEDS YOU stands, nothing otherwise

WHAT THIS IS NOT
  Not a server and not a daemon: the page changes only when this command
  runs again. Not a publisher: the stored URL is written back by whoever
  published, and publishing itself is Claude's act, not this file's. Not
  a second truth: the page states the newest record it was built from and
  a content fingerprint, never its own render clock, so two runs on the
  same rows are byte identical.

Python 3.9, standard library only. No network, at render time or ever.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import importlib.util
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name):
    """Load a sibling module by PATH, off this file's own directory, the
    same technique tools/bm_lead.py and tools/bm_visual.py use: a hostile
    sys.path cannot shadow bm_store this way."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ONE module family, on purpose: bm_visual already loaded bm_store and
# bm_lead, so taking its handles means a refusal raised through any of the
# three is the SAME class object this file catches.
bv = _load("bm_visual")
bs = bv.bs
bl = bv.bl


# ---------------------------------------------------------------------------
# THE CONSENT GATE. Shape copied from tools/bm_lead.py, deliberately
# verbatim in structure: scripts/setup.py is the ONE place the consent
# config schema and its reader live.
# ---------------------------------------------------------------------------

CONSENT_REFUSAL = ("bm_view: setup is not complete yet; run: "
                   "python3 scripts/setup.py")


class ConsentMissing(Exception):
    """Raised by _store_or_refuse when setup has not been completed. Never
    caught anywhere that then writes: every caller reports and returns."""


_bm_setup_cache = []


def _load_bm_setup():
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_view", os.path.join(REPO, "scripts", "setup.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, repr(exc)


def _get_bm_setup():
    if not _bm_setup_cache:
        _bm_setup_cache.extend(_load_bm_setup())
    return _bm_setup_cache[0], _bm_setup_cache[1]


def _consent_state():
    """True only when scripts/setup.py's own is_consented says so. Fails
    CLOSED on any load error. The module object is cached; the CONFIG is
    not, because read_config consults the environment on every call."""
    mod, _load_error = _get_bm_setup()
    if mod is None:
        return False
    try:
        cfg, _cfg_error = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Plumbing. Shapes copied from tools/bm_project.py via tools/bm_lead.py
# (_out, _err, _root, _parse, _require, _actor, _print_json). Do not
# invent a second flag parser.
# ---------------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _root():
    root, _source = bs.require_root()
    return root


def _store_or_refuse(kv, write):
    """THE ONLY constructor of bs.Store and bs.ReadOnlyStore in this file.

    Raises ConsentMissing when scripts/setup.py's is_consented is False.
    Every subcommand obtains its handle here or does not have one, which
    is what makes a pre-consent write structurally impossible rather than
    merely absent. create=False on purpose, matching every CLI in this
    project except `bm_store.py init`."""
    if not _consent_state():
        raise ConsentMissing(CONSENT_REFUSAL)
    root = _root()
    if write:
        return bs.Store(root, create=False)
    return bs.ReadOnlyStore(root)


def _close(store):
    try:
        store.close()
    except Exception:  # sbe: allow-silent best-effort close on the way out, never blocks a subcommand's own result
        pass


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized. Copied
    from tools/bm_lead.py's own _parse, including the --help branch."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            _out("recognized flags: %s"
                 % ", ".join("--" + k for k in sorted(known)))
            if wants_value:
                _out("flags taking a value: %s"
                     % ", ".join("--" + k for k in sorted(wants_value)))
            sys.exit(0)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_view: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_view: --%s needs a value" % name)
                    sys.exit(2)
                kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


def _require(kv, name, usage):
    val = kv.get(name)
    if not val:
        _err(usage)
        _err("bm_view: --%s is required" % name)
        sys.exit(2)
    return val


_ACTOR_FLAGS = ("actor-type", "actor-name", "session-id")


def _actor(kv, usage):
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_view: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(2)
    actor_name = _require(kv, "actor-name", usage)
    session_id = kv.get("session-id") or ("cli-" + uuid.uuid4().hex)
    return {"actor_type": actor_type, "actor_name": actor_name,
            "session_id": session_id}


def _view_actor(kv, hook):
    """The actor a view row is recorded under. Explicit flags win; the
    hook path says it is the automatic check itself, exactly as
    tools/bm_lead.py's _hook_actor does."""
    if kv.get("actor-name"):
        return _actor(kv, "bm-view render")
    if hook:
        return {"actor_type": "hook", "actor_name": "bm_view render",
                "session_id": os.environ.get("CLAUDE_SESSION_ID")
                              or ("tick-" + uuid.uuid4().hex)}
    return {"actor_type": "human", "actor_name": "founder",
            "session_id": "cli-" + uuid.uuid4().hex}


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _refused(exc, attempted):
    """Every refusal reaches the founder rewritten (L-S9): the reason code
    picks the five part block, and a code with no rewrite falls back to
    the plain refusal line rather than a raw traceback."""
    reason = getattr(exc, "reason", "") or ""
    if reason in bv.REFUSAL_HELP:
        _err(bv.failure_block(reason, attempted, str(exc)))
    else:
        _err("bm_view: refused: %s" % exc)
    return 1


def _only_project(store):
    projects = store.list_projects(raw=True)
    if len(projects) == 1:
        return projects[0]["project_id"]
    return None


_esc = bv.xml_escape


# ---------------------------------------------------------------------------
# The twelve sections, as data (design section 4.2). Shape copied from
# tools/bm_docs.py's FILES tuple: "every section" is enumerable data a
# test iterates rather than a list somebody keeps in sync by hand.
# ---------------------------------------------------------------------------

VIEW_FILENAME = "PROJECT-VIEW.html"

VIEW_SECTIONS = (
    ("header", "Where this stands", "_sec_header", "header"),
    ("needs-you", "Waiting on you", "_sec_needs_you", "needs-you"),
    ("next-step", "Your next step", "_sec_next_step", "next-step"),
    ("pipeline", "The path of the work", "_sec_pipeline", "pipeline"),
    ("programme", "Where the programme stands", "_sec_programme",
     "programme"),
    ("progress", "How far each lane has moved", "_sec_progress",
     "progress"),
    ("counts", "How much, against what limit", "_sec_counts", "counts"),
    ("risks", "What could still go wrong", "_sec_risks", "risks"),
    ("decisions", "Decisions waiting for your answer", "_sec_decisions",
     "decisions"),
    ("insights", "What I now believe, and what proved it", "_sec_insights",
     "insights"),
    ("gates", "What has to be true before this ships", "_sec_gates",
     "gates"),
    ("lanes", "Who holds the pen", "_sec_lanes", "lanes"),
    ("documents", "What each finished piece produced", "_sec_documents",
     "documents"),
    ("timeline", "The story so far", "_sec_timeline", "timeline"),
    ("your-move", "Your move", "_sec_your_move", "your-move"),
    ("help", "About this page", "_sec_help", "help"),
)

# The three first-run commands (design section 5.3). An empty state may
# teach these and nothing else, so a section cannot teach a command the
# founder has not met.
OFFERED_COMMANDS = ("/brotherme-start", "/brotherme-status",
                    "/brotherme-next")

# Carbon's empty state anatomy (design section 4.4): a positive title,
# one short body, ONE action in verb plus noun form, and the exact
# command that action runs. A section with zero rows renders its entry; a
# section that renders blank is a defect.
EMPTY_STATES = {
    "header": {
        "title": "This page always starts with where things stand",
        "body": "The top of the page shows the outcome, where the work "
                "is, and how far the setup has come.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "needs-you": {
        "title": "You are free to keep going",
        "body": "Anything that stops the work until you act appears "
                "here first, with the one thing that clears it.",
        "action": "Check where things stand",
        "command": "/brotherme-status"},
    "next-step": {
        "title": "There is always one recommended step",
        "body": "This section names exactly one next step, why it is "
                "the one, and the exact command that runs it.",
        "action": "Ask for the next step",
        "command": "/brotherme-next"},
    "pipeline": {
        "title": "The path of the work will be drawn here",
        "body": "A drawing of the stages from set up to delivered, with "
                "the current one marked NOW.",
        "action": "Start the work",
        "command": "/brotherme-start"},
    "programme": {
        "title": "The phases of your work will be charted here",
        "body": "Each phase gets a bar, and each piece a box that ticks "
                "when it is finished and checked.",
        "action": "Start the work",
        "command": "/brotherme-start"},
    "progress": {
        "title": "Progress by lane will build here",
        "body": "As pieces of work are planned, each one shows its lane, "
                "how far it has moved, and what checked it.",
        "action": "Ask for the next step",
        "command": "/brotherme-next"},
    "counts": {
        "title": "The counts will build here",
        "body": "Plain counts of checks passed and the work budget "
                "used, each against its agreed limit.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "risks": {
        "title": "You are clear of open risks right now",
        "body": "When something could still go wrong, it lands here with "
                "what would settle it and who is watching it.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "decisions": {
        "title": "Decisions will wait for you here",
        "body": "When a choice needs your answer, it appears here with "
                "a recommendation and what happens either way.",
        "action": "Ask for the next step",
        "command": "/brotherme-next"},
    "insights": {
        "title": "What I learn lands here",
        "body": "Each thing learned is shown with what proved it, what "
                "was rejected, and what would change it.",
        "action": "Ask for the next step",
        "command": "/brotherme-next"},
    "gates": {
        "title": "The checks before shipping will be listed here",
        "body": "Each thing that has to be true before the work ships, "
                "with whether it has passed yet.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "lanes": {
        "title": "You hold the pen on everything open right now",
        "body": "When the work is split between you and BrotherMode, "
                "this shows who owns each open step.",
        "action": "Ask for the next step",
        "command": "/brotherme-next"},
    "documents": {
        "title": "What gets produced will be listed here",
        "body": "Each finished piece of work shows the paths it produced "
                "and the checks that ran on it.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "timeline": {
        "title": "The story of the work will build here",
        "body": "Each short catch-up lands here in order, so you can "
                "replay the work from the start.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "your-move": {
        "title": "The offer to take over always stands",
        "body": "Taking the work back is one paste away, whether or not "
                "a decision is open.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
    "help": {
        "title": "Help lives at the bottom of the page",
        "body": "What this page is, what fills each section, and what "
                "the page cannot do.",
        "action": "See where things stand",
        "command": "/brotherme-status"},
}

# One entry per section (design section 9.3): a one line "what this is"
# under the heading, and the longer line the help section carries. A new
# section cannot ship without its help line, because the same anchor keys
# both maps and a test walks them together.
SECTION_HELP = {
    "header": ("The outcome in your words, and what this page was built "
               "from.",
               "The outcome, where the work is, the setup count, and the "
               "newest record this page was built from."),
    "needs-you": ("The one thing waiting on you, if anything is.",
                  "At most one thing interrupts you at a time; anything "
                  "else waits its turn here."),
    "next-step": ("One recommended step, its why, and its command.",
                  "Always exactly one recommended step; a page with two "
                  "recommendations is a page with none."),
    "pipeline": ("Where the work is on its path.",
                 "The stages of the work drawn left to right, with the "
                 "current one marked NOW."),
    "programme": ("Every phase of the work, and how much of each one is "
                  "finished and checked.",
                  "One bar per piece of work, grouped into the phase its "
                  "own record names, with a box that ticks only on a "
                  "finished piece that has a check on file."),
    "progress": ("Each piece of work, its lane, and how far it has moved.",
                 "One bar per piece of work inside its own lane, plus "
                 "what is finished and what is blocked and on what."),
    "counts": ("The numbers, each against its limit.",
               "Checks passed, work budget used and findings, each as a "
               "number against its agreed limit."),
    "risks": ("What could still go wrong, and what would settle it.",
              "Each open risk with what would settle it and who is "
              "watching it, the most pressing first."),
    "decisions": ("Open choices, the highest stakes first.",
                  "Each open choice with a recommendation, what else was "
                  "weighed, and the option to take it back."),
    "insights": ("What was learned, and what proved it.",
                 "Each thing learned, with its proof, what was rejected, "
                 "what would change it, and your standing option."),
    "gates": ("What still stands between the work and done.",
              "The checks that have to pass before the work ships, drawn "
              "as a ladder."),
    "lanes": ("Who owns each open step right now.",
              "Drawn only when the work is split between more than one "
              "owner."),
    "documents": ("What each finished piece produced, as a path.",
                  "Every finished piece of work with the paths it "
                  "produced and the checks that ran on it, timed."),
    "timeline": ("The catch-ups, oldest first.",
                 "Every catch-up in order, so the run replays forwards "
                 "from the beginning."),
    "your-move": ("The standing offer to take the work back.",
                  "The exact text to paste into the chat to take the "
                  "work and any open decision back into your hands."),
    "help": ("What this page is, and what it cannot do.",
             "The one place the page explains itself; each section above "
             "carries one line and the rest lives here."),
}

# The eight setup milestones (design section 4.4): detected from rows and
# from the delivery artefact on disk, never asserted. The counter counts
# real rows only, which is the honest form of the endowed progress
# effect: the advancement is genuine and nothing is invented.
SETUP_MILESTONES = (
    ("outcome", "an outcome is recorded"),
    ("records", "your project records exist"),
    ("direction", "a direction is agreed"),
    ("planned", "the work is planned"),
    ("accepted", "a first piece is accepted"),
    ("checked", "a check ran and passed"),
    ("catchup", "a catch-up is on record"),
    ("packet", "a delivery packet exists"),
)

# The promise the handback panel carries (design section 8.1), generated
# in one place from L04 section 9.4's five acts in plain words.
PROMISE_TEXT = (
    "Nothing is lost. Your authorisation is paused so no further "
    "automatic work can start, the work in progress is parked with a "
    "note about what to do next, what BrotherMode would have chosen is "
    "recorded, and a page for whoever picks it up is written.")

# What the page cannot do (design section 10, the entries that belong on
# the page itself, in plain words).
PAGE_LIMITS = (
    "It is a picture of your records at the moment it was written, not "
    "a live screen. It updates when it is written again, which happens "
    "on command and when a working session stops.",
    "Opening it reads and changes nothing in your project.",
    "Buttons on a web page cannot act on your project. The one way back "
    "in is copying the text under Your move into the chat.",
    "It cannot show your application running, because BrotherMode does "
    "not run your application.",
)

_FINGERPRINT_SLOT = "@@BM-FINGERPRINT@@"

_PAGE_TITLE = "Where your project stands"

# Layout only: every colour on the page lives in bv.THEME_CSS, the one
# stylesheet block, so the test that deletes it and asserts every status
# is still readable stays trivial (L-S4).
PAGE_CSS = """
body { font-family: Seravek, "Avenir Next", "Segoe UI", system-ui,
       sans-serif; background: var(--bm-paper); margin: 0 auto;
       max-width: 60rem; padding: 1rem 1.5rem 4rem; line-height: 1.55; }
h1, h2, h3 { font-family: "Iowan Old Style", Palatino, Georgia, serif;
             font-weight: 600; line-height: 1.25; }
h1 { color: var(--bm-accent); }
a { color: var(--bm-for-info-ink); }
code { color: var(--bm-body); background: var(--bm-node-tint);
       padding: 0 .25rem; }
.bm-section { margin: 0 0 2rem 0; }
.bm-what { color: var(--bm-muted); margin: -0.25rem 0 1rem 0; }
.bm-card { overflow-x: auto; background: var(--bm-node-tint);
           color: var(--bm-body); padding: .5rem .75rem; margin: 0; }
.bm-empty { border: 1px dashed var(--bm-idle-rule);
            background: var(--bm-idle-tint); padding: .75rem 1rem; }
.bm-decision { margin: 0 0 1.25rem 0; }
.bm-catchup { border-left: 3px solid var(--bm-idle-rule);
              padding: .25rem .75rem; margin: 0 0 1rem 0; }
.bm-header dt { color: var(--bm-muted); }
.bm-header dd { margin: 0 0 .5rem 0; }
"""


def fingerprint(body):
    """The first 12 hex characters of sha256 over the rendered body. A
    property of the content, never of the clock: it is what --if-stale
    compares, what the header prints, and what the views table stores, so
    the same value serves three purposes with nothing to keep in sync."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def setup_milestones(store, project_id):
    """The eight milestones as (key, label, done) triples, every one
    detected from rows or from the delivery artefact on disk."""
    project = store.get_project(project_id, raw=True)
    contract = store.latest_contract(project_id, raw=True)
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    tasks = store.list_tasks(project_id, raw=True)
    insights = store.list_insights(project_id, raw=True)
    state = {
        "outcome": bool(((project or {}).get("goal") or "").strip()),
        "records": project is not None,
        "direction": bool(
            [r for r in insights if r.get("kind") == "DECISION"]
            or (contract and (contract.get("state") or "") == "live")),
        "planned": bool(units or tasks),
        "accepted": bool(
            [u for u in units if u.get("status") == "DONE"]
            or [t for t in tasks
                if (t.get("status") or "") in ("accepted", "done",
                                               "delivered")]),
        "checked": bool([r for r in insights
                         if r.get("evidence_class") == "EXECUTED"]),
        "catchup": store.latest_briefing(project_id, raw=True) is not None,
        "packet": _delivery_packet_exists(store, project_id),
    }
    return [(key, label, state[key]) for key, label in SETUP_MILESTONES]


def _delivery_packet_exists(store, project_id):
    """Milestone 8's detector: the deliver artefact on disk, under either
    of the two names tools/bm_project.py generates."""
    for name in ("DELIVERY-PACKET.md",
                 "DELIVERY-PACKET-%s.md" % project_id):
        try:
            path = bs.safe_project_path(store.root, name)
        except bs.BMStoreError:  # sbe: allow-silent this candidate name failed path validation, try the other name
            continue
        if os.path.isfile(path):
            return True
    return False


def _ranked_decisions(store, project_id):
    """The open key decisions, highest stakes first. The stakes order is
    bl.DECISION_STAKES, which is data; this sorts by it rather than
    re-deciding what matters most."""
    rows = store.open_key_decisions(project_id, raw=True)
    order = {cls: i for i, cls in enumerate(bl.DECISION_STAKES)}
    return sorted(rows, key=lambda r: order.get(r.get("decision_class"),
                                                len(order)))


def _newest_stamp(facts, insights, briefings):
    """The newest row timestamp any input carries. A property of the
    rows, never of the clock (L-S8), so it is what the header states."""
    stamps = [facts.get("newest_at") or ""]
    stamps.extend([(r.get("created_at") or "") for r in insights])
    stamps.extend([(r.get("created_at") or "") for r in briefings])
    return max(stamps)


def handback_prompt(decision):
    """The copy as prompt text (design section 8.1): the exact text the
    founder pastes into the chat, so the control is a paste rather than a
    button that could not act anyway."""
    subject = ((decision or {}).get("subject") or "").strip() \
        or "the work as it stands"
    lines = ["/brotherme-handback", "", "Take back: %s" % subject]
    if decision:
        lines.append("Decision id: %s"
                     % (decision.get("insight_id") or ""))
    lines.append("Why: <the founder types this>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The section renderers. Each returns the section body html, or "" when
# it has zero rows, in which case render_page places the designed empty
# state. Every one is pure over the ctx dict build_page assembled.
# ---------------------------------------------------------------------------

def _empty_state(key):
    entry = EMPTY_STATES[key]
    return ('<div class="bm-empty"><h3>%s</h3><p>%s</p>'
            '<p class="bm-action">%s: <code>%s</code></p></div>'
            % (_esc(entry["title"]), _esc(entry["body"]),
               _esc(entry["action"]), _esc(entry["command"])))


def _sec_header(ctx):
    fields = {label: value for label, value, _x in ctx["status"]["fields"]}
    facts = ctx["facts"]
    if facts.get("run_state"):
        where = facts.get("run_sentence") or ""
    elif fields.get("Progress") == "nothing planned yet":
        where = "Your project records exist. Nothing has been planned yet."
    else:
        where = fields.get("Progress") or ""
    done = len([1 for _k, _l, ok in ctx["milestones"] if ok])
    newest = _newest_stamp(facts, ctx["insights"], ctx["briefings"]) \
        or "the first record"
    rows = (("Outcome", fields.get("Goal") or ""),
            ("Where we are", where),
            ("Progress", fields.get("Progress") or ""),
            ("Setup", "%d of %d" % (done, len(ctx["milestones"]))))
    items = "".join("<dt>%s</dt><dd>%s</dd>" % (_esc(k), _esc(v))
                    for k, v in rows)
    items += ("<dt>Built from</dt><dd>records up to %s, fingerprint "
              '<span class="bm-fingerprint">%s</span></dd>'
              % (_esc(newest), _FINGERPRINT_SLOT))
    return '<dl class="bm-header">%s</dl>' % items


def _sec_needs_you(ctx):
    top = [a for a in ctx["alerts"] if a["rung"] == "NEEDS YOU"]
    if not top:
        return ""
    return bv.render_alert(top[0], "html")


def _sec_next_step(ctx):
    text, why, command = ctx["status"]["next"]
    return ('<div data-bm-next-step="1"><p><strong>%s</strong></p>'
            '<p>Why: %s</p><p>Run: <code>%s</code></p></div>'
            % (_esc(text), _esc(why), _esc(command)))


def _sec_pipeline(ctx):
    return bv.to_svg(bv.diagram_pipeline(ctx["facts"]))


def _task_label_by_id(tasks):
    """A plain title for every task id this page knows about, so a
    blocking id (DESIGN-progress-surface.md item 2's own field) reaches
    the founder as a name rather than as a raw identifier: an id is a
    trace attribute, never body text, everywhere else on this page."""
    return {t.get("task_id"): (t.get("title") or t.get("task_id") or "")
            for t in tasks if t.get("task_id")}


def _sec_programme(ctx):
    """Phase 5, the progress view (founder decision 2026-08-08). The
    Gantt, as a section of THIS page rather than as a second page: the
    architecture rule this programme is closing allows one authority per
    concern, and two progress pages would be two.

    Blank when no work is recorded, which lets render_page place the
    designed empty state, the same contract every section above follows.

    The tick contract is stated ON the page, in the founder's own words,
    because the page is where the claim is made. What these records can
    prove is that a piece is finished and that a check is on file against
    it. What they cannot prove is that the check ran after the last edit,
    since nothing in the store observes edits. Saying so here is cheaper
    than being believed about something untrue."""
    gantt = ctx.get("gantt") or {"phases": []}
    drawn = bv.diagram_gantt(gantt)
    if drawn is None:
        return ""
    parts = [bv.to_svg(drawn),
             '<p class="bm-what">%d of %d pieces are ticked. A box ticks '
             'only where the piece is finished AND a check is recorded '
             'against it; the count is of records, never an '
             'impression.</p>'
             % (gantt["ticked"], gantt["total"])]
    for phase in gantt["phases"]:
        parts.append('<h3>%s</h3><p>%d of %d ticked in this phase.</p>'
                     % (_esc(phase["phase"]), phase["ticked"],
                        phase["total"]))
        rows = []
        for item in phase["items"]:
            line = "%s, %s" % (item["label"], item["state"])
            if item["dated"]:
                line += " (%s to %s%s)" % (
                    item["start"], item["end"],
                    ", still open" if item["open_ended"] else "")
            else:
                line += " (no dates recorded)"
            line += (". Last check: %s"
                     % (item["evidence_ref"] or "no check recorded yet"))
            rows.append('<li data-bm-trace="t:%s">%s</li>'
                       % (_esc(item["task_id"]), _esc(line)))
        parts.append("<ul>%s</ul>" % "".join(rows))
    return "".join(parts)


def _sec_progress(ctx):
    """DESIGN-progress-surface.md item 3: the timeline shape plus what is
    finished and what is next, one lane at a time. Blank when the
    records hold no pieces of work at all, which is a real answer."""
    progress = ctx["progress"]
    lanes = progress.get("lanes") or []
    if not lanes:
        return ""
    labels = _task_label_by_id(ctx["tasks"])
    parts = []
    drawn = bv.diagram_timeline(progress)
    if drawn:
        parts.append(bv.to_svg(drawn))
    for lane in lanes:
        items = lane["items"]
        finished = len([i for i in items
                        if i["state"] in bv.FINISHED_TASK_STATES])
        parts.append('<h3>%s</h3><p>%d of %d finished in this lane.</p>'
                     % (_esc(lane["lane"]), finished, len(items)))
        rows = []
        for item in items:
            waiting_on = [labels.get(bid) or "a piece no longer on record"
                         for bid in item["blocked_by"]]
            line = "%s, %s" % (item["label"], item["state"])
            if waiting_on:
                line += " (waiting on: %s)" % "; ".join(waiting_on)
            if item["state"] == "blocked":
                line += ". %s" % (item["action"]
                                  or "no action recorded yet")
            line += (". Last check: %s"
                     % (item["evidence_ref"] or "no check recorded yet"))
            rows.append('<li data-bm-trace="t:%s">%s</li>'
                       % (_esc(item["task_id"]), _esc(line)))
        parts.append("<ul>%s</ul>" % "".join(rows))
    return "".join(parts)


def _sec_counts(ctx):
    return bv.to_svg(bv.counts_rows(ctx["facts"]))


def _sec_risks(ctx):
    """DESIGN-progress-surface.md item 4: risks come from insights of
    kind RISK and from alerts_now, sorted by the alert ladder's own rung
    order. A risk with no mitigation recorded prints those words rather
    than an empty line, because a blank line reads as safe."""
    everything = ctx["insights"]
    superseded = {r.get("supersedes") for r in everything
                  if r.get("supersedes")}
    risk_rows = {r.get("insight_id"): r for r in everything
                if r.get("kind") == "RISK"
                and r.get("insight_id") not in superseded}
    rung_order = {rung: i for i, rung in enumerate(bv.RUNGS)}
    risk_alerts = sorted(
        [a for a in ctx["alerts"] if a["kind"] == "open-risk"],
        key=lambda a: rung_order.get(a["rung"], len(rung_order)))
    if not risk_alerts:
        return ""
    parts = []
    for a in risk_alerts:
        row = risk_rows.get(a["row_id"]) or {}
        owner = (row.get("actor_name") or "").strip() or "BrotherMode"
        mitigation = (a["action"] or "").strip() or "no mitigation recorded"
        parts.append(
            '<article class="bm-decision" data-bm-trace="i:%s">'
            '<p><strong>%s</strong></p><p>%s</p>'
            '<p>What would settle it: %s</p><p>Watched by: %s</p>'
            '</article>'
            % (_esc(a["row_id"]), _esc(a["subject"]), _esc(a["message"]),
               _esc(mitigation), _esc(owner)))
    return "".join(parts)


def _sec_decisions(ctx):
    if not ctx["decisions"]:
        return ""
    fork = bv.diagram_fork(ctx["facts"])
    parts = []
    for i, row in enumerate(ctx["decisions"]):
        card = bl.render_decision_card(row)
        drawn = bv.to_svg(fork) if (i == 0 and fork) else ""
        parts.append(
            '<article class="bm-decision" data-bm-trace="i:%s">'
            '<pre class="bm-card">%s</pre>%s</article>'
            % (_esc(row.get("insight_id") or ""),
               _esc("\n".join(card)), drawn))
    return "".join(parts)


def _one_insight(row):
    try:
        box = bv.render_insight_box(row, "html")
    except ValueError:
        # A row that cannot fill all six slots is never shown as a
        # shorter box (that is the design's own rule); it renders as its
        # labelled claim line instead, so the renderer still applies the
        # evidence label and nothing is dressed up.
        box = ('<section class="bm-insight"><p>%s</p></section>'
               % _esc(bl.render_claim_text(row)))
    return ('<div data-bm-trace="i:%s">%s</div>'
            % (_esc(row.get("insight_id") or ""), box))


def _sec_insights(ctx):
    rows = ctx["insights"]
    if not rows:
        return ""
    boxes = [_one_insight(r) for r in rows]
    html = boxes[0]
    if len(boxes) > 1:
        html += ('<details><summary>%d earlier record(s)</summary>%s'
                 '</details>' % (len(boxes) - 1, "".join(boxes[1:])))
    return html


def _sec_gates(ctx):
    drawn = bv.diagram_gates(ctx["facts"])
    return bv.to_svg(drawn) if drawn else ""


def _sec_lanes(ctx):
    drawn = bv.diagram_lanes(ctx["facts"])
    return bv.to_svg(drawn) if drawn else ""


def _sec_documents(ctx):
    """DESIGN-progress-surface.md item 3: what each finished piece of
    work produced, as a path, plus the checks that ran and their own
    timestamps, read from evidence rows. Blank until something is
    finished, which is a real answer for a project just starting out."""
    finished = [t for t in ctx["tasks"]
                if (t.get("status") or "") in bv.FINISHED_TASK_STATES]
    project_checks = ctx["project_evidence"]
    if not finished and not project_checks:
        return ""
    parts = []
    if project_checks:
        items = "".join(
            "<li>%s, %s</li>"
            % (_esc(c.get("kind") or "a check"), _esc(c.get("created_at")
                                                       or ""))
            for c in project_checks)
        parts.append("<h3>Checks for the whole project</h3><ul>%s</ul>"
                     % items)
    for t in finished:
        task_id = t.get("task_id") or ""
        outputs = t.get("expected_outputs") or []
        if isinstance(outputs, str):
            outputs = []
        checks = ctx["evidence_by_task"].get(task_id) or []
        label = t.get("title") or task_id
        out_items = ("".join("<li><code>%s</code></li>" % _esc(p)
                             for p in outputs)
                    or "<li>no path recorded</li>")
        check_items = ("".join(
            "<li>%s, %s</li>" % (_esc(c.get("kind") or "a check"),
                                 _esc(c.get("created_at") or ""))
            for c in checks) or "<li>no check recorded</li>")
        parts.append(
            '<article data-bm-trace="t:%s"><p><strong>%s</strong></p>'
            '<p>Produced:</p><ul>%s</ul>'
            '<p>Checks that ran:</p><ul>%s</ul></article>'
            % (_esc(task_id), _esc(label), out_items, check_items))
    return "".join(parts)


def _sec_timeline(ctx):
    rows = list(reversed(ctx["briefings"]))
    if not rows:
        return ""
    parts = []
    for row in rows:
        parts.append(
            '<article class="bm-catchup" data-bm-trace="b:%s">'
            '<h3>%s</h3><p>Where we are: %s</p><p>What changed: %s</p>'
            '<p>What it cost: %s</p></article>'
            % (_esc(row.get("briefing_id") or ""),
               _esc(row.get("created_at") or ""),
               _esc(row.get("where_we_are") or ""),
               _esc(row.get("what_changed") or ""),
               _esc(row.get("what_it_cost") or "")))
    return "".join(parts)


def _sec_your_move(ctx):
    decision = ctx["decisions"][0] if ctx["decisions"] else None
    parts = ["<p>%s</p>" % _esc(bl.HANDBACK_OPTION_TEXT),
             "<p>%s</p>" % _esc(PROMISE_TEXT)]
    if decision is not None:
        parts.append(
            '<p>The choice itself, and what happens either way, is '
            'drawn beside <a href="#decisions">the open decision</a> '
            'above.</p>')
    parts.append(
        '<details><summary>Copy this into the chat to take it back'
        '</summary><pre class="bm-card">%s</pre></details>'
        % _esc(handback_prompt(decision)))
    return "".join(parts)


def _sec_help(ctx):
    lines = [
        "<p>This page is written from your project's records. Every line "
        "on it comes from a record, and the short code in the header "
        "changes whenever your records change, so an old tab is visibly "
        "older than a fresh one.</p>", "<ul>"]
    for anchor, heading, _r, _k in VIEW_SECTIONS:
        lines.append('<li><a href="#%s">%s</a>: %s</li>'
                     % (anchor, _esc(heading),
                        _esc(SECTION_HELP[anchor][1])))
    lines.append("</ul>")
    lines.append("<p>What this page cannot do:</p><ul>")
    for limit in PAGE_LIMITS:
        lines.append("<li>%s</li>" % _esc(limit))
    lines.append("</ul>")
    return "".join(lines)


_SECTION_RENDERERS = {
    "_sec_header": _sec_header, "_sec_needs_you": _sec_needs_you,
    "_sec_next_step": _sec_next_step, "_sec_pipeline": _sec_pipeline,
    "_sec_programme": _sec_programme, "_sec_progress": _sec_progress,
    "_sec_counts": _sec_counts, "_sec_risks": _sec_risks,
    "_sec_decisions": _sec_decisions,
    "_sec_insights": _sec_insights, "_sec_gates": _sec_gates,
    "_sec_lanes": _sec_lanes, "_sec_documents": _sec_documents,
    "_sec_timeline": _sec_timeline,
    "_sec_your_move": _sec_your_move, "_sec_help": _sec_help,
}


def render_page(status, alerts, insights, briefings, decisions, facts,
                milestones, tasks=(), progress=None, project_evidence=(),
                evidence_by_task=None, gantt=None):
    """The whole document, pure over its inputs. Returns (html, fp) where
    fp is the fingerprint over the rendered body with the fingerprint
    slot still empty, so the printed value describes the content rather
    than itself.

    tasks, progress, project_evidence and evidence_by_task are the
    progress surface loop's own additions (DESIGN-progress-surface.md):
    progress is bv.progress_facts' own dict, and the other three are the
    rows build_page already read to build it, kept here too so the
    progress, risks and documents sections never re-derive them."""
    ctx = {"status": status, "alerts": alerts, "insights": insights,
           "briefings": briefings, "decisions": decisions, "facts": facts,
           "milestones": milestones, "tasks": list(tasks or []),
           "progress": progress or {"lanes": []},
           "gantt": gantt or {"phases": [], "window": None,
                              "ticked": 0, "total": 0},
           "project_evidence": list(project_evidence or []),
           "evidence_by_task": evidence_by_task or {}}
    sections = []
    for anchor, heading, renderer_name, empty_key in VIEW_SECTIONS:
        body = _SECTION_RENDERERS[renderer_name](ctx)
        if not body:
            body = _empty_state(empty_key)
        sections.append(
            '<section id="%s" class="bm-section"><h2>%s</h2>'
            '<p class="bm-what">%s</p>%s</section>'
            % (anchor, _esc(heading), _esc(SECTION_HELP[anchor][0]),
               body))
    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, '
        'initial-scale=1">\n'
        "<title>%s</title>\n<style>%s%s</style>\n</head>\n<body>\n"
        "<h1>%s</h1>\n%s\n</body>\n</html>\n"
        % (_esc(_PAGE_TITLE), bv.THEME_CSS, PAGE_CSS, _esc(_PAGE_TITLE),
           "\n".join(sections)))
    fp = fingerprint(html)
    return html.replace(_FINGERPRINT_SLOT, fp), fp


def build_page(store, project_id):
    """Gather every input through the existing collectors and accessors,
    then render. No collector of its own: the eight fields are bl's, the
    drawing facts and the progress facts are bv's, and everything else is
    a store accessor.

    tasks, dependencies and per-task evidence are read here, once, the
    same rule visual_facts follows for its own reads, then handed to
    bv.progress_facts (which opens no store of its own) so the progress,
    risks and documents sections never issue a query directly."""
    status = bl.collect_status(store, project_id)
    facts = bv.visual_facts(store, project_id)
    alerts = bv.alerts_now(store, project_id)
    insights = store.list_insights(project_id, raw=True)
    briefings = store.list_briefings(project_id, raw=True)
    decisions = _ranked_decisions(store, project_id)
    milestones = setup_milestones(store, project_id)
    tasks = store.list_tasks(project_id, raw=True)
    dependencies = store.list_dependencies(project_id, raw=True)
    evidence_by_task = {t["task_id"]: store.list_evidence(
        "task", t["task_id"], raw=True) for t in tasks if t.get("task_id")}
    project_evidence = store.list_evidence("project", project_id, raw=True)
    progress = bv.progress_facts({"tasks": tasks,
                                  "dependencies": dependencies,
                                  "evidence": evidence_by_task})
    # Phase 5: the same rows again, grouped by phase instead of by owner.
    # No extra query: gantt_facts opens no store, exactly like
    # progress_facts beside it.
    gantt = bv.gantt_facts({"tasks": tasks, "evidence": evidence_by_task})
    return render_page(status, alerts, insights, briefings, decisions,
                       facts, milestones, tasks=tasks, progress=progress,
                       project_evidence=project_evidence,
                       evidence_by_task=evidence_by_task, gantt=gantt)


# ---------------------------------------------------------------------------
# The developer brief page (design section 8.2): the same eight sections
# from the same rows as the markdown, plus presentation only.
# ---------------------------------------------------------------------------

def _brief_sections(md):
    """[(heading, body)] parsed from the markdown brief, so the html is a
    projection of the markdown rather than a second rendering: a section
    added to the markdown appears here without this file changing."""
    sections = []
    heading, body = None, []
    for line in md.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(body).strip()))
            heading, body = line[3:].strip(), []
            continue
        if heading is not None:
            body.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body).strip()))
    return sections


def render_developer_brief_html(store, handback_insight):
    """One HTML rendering of the markdown brief: numbered, anchored,
    with the decision drawn as the fork and the reproduction as a copy
    block. The heading set and the trace tag set are the markdown's own,
    by construction, and the suite asserts it."""
    md = bl.render_developer_brief(store, handback_insight)
    title = md.splitlines()[0].lstrip("# ").strip() if md else ""
    sections = _brief_sections(md)
    decision = None
    if (handback_insight.get("supersedes") or "").strip():
        decision = store.get_insight(handback_insight["supersedes"],
                                     raw=True)
    fork = bv.diagram_fork({"decision": decision}) if decision else None
    parts = ["<h1>%s</h1>" % _esc(title), "<ol>"]
    for i, (heading, _body) in enumerate(sections):
        parts.append('<li><a href="#brief-%d">%s</a></li>'
                     % (i, _esc(heading)))
    parts.append("</ol>")
    for i, (heading, body) in enumerate(sections):
        drawn = ""
        if fork is not None and heading == bl.DEVELOPER_BRIEF_SECTIONS[1]:
            drawn = bv.to_svg(fork)
        block = '<pre class="bm-card">%s</pre>' % _esc(body)
        if heading == bl.DEVELOPER_BRIEF_SECTIONS[5]:
            block = ('<details><summary>Copy this to reproduce</summary>'
                     "%s</details>" % block)
        parts.append(
            '<section id="brief-%d" class="bm-section"><h2>%s</h2>%s%s'
            "</section>" % (i, _esc(heading), drawn, block))
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, '
        'initial-scale=1">\n'
        "<title>%s</title>\n<style>%s%s</style>\n</head>\n<body>\n"
        "%s\n</body>\n</html>\n"
        % (_esc(title), bv.THEME_CSS, PAGE_CSS, "\n".join(parts)))


def _ensure_brief_dir(store, root, project_id):
    """THE ONLY directory-creating call in this file, and its first
    parameter is the store handle, so it is unreachable without
    _store_or_refuse. Same rule and same reason as tools/bm_lead.py's
    _ensure_pack_dir, whose folder this page lands in."""
    name = bl.HANDOVER_ROOT
    if len(store.list_projects(raw=True)) > 1:
        name = "%s-%s" % (bl.HANDOVER_ROOT, project_id)
    path = bs.safe_project_path(root, name)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


# ---------------------------------------------------------------------------
# The doorway (design section 5.1): minute zero, zero writes, and TEXT.
# ---------------------------------------------------------------------------

def doorway_text(consented):
    """The minute zero block: a positive title, one short body, ONE
    primary action and one secondary link, answering only the three
    questions a founder has at minute zero. The command list and the
    machinery words are deliberately absent."""
    lines = []
    if not consented:
        consent_alert = bv.alerts_now(None, None, consented=False)[0]
        lines.append(bv.render_alert(consent_alert, "text"))
        lines.append("")
    lines.extend([
        "You say what you want. I run the work. You stay in charge.",
        "",
        "What happens: you describe the outcome in your own words, I "
        "plan and run the work, and you answer only the decisions that "
        "shape it.",
        "What it costs: a first small piece of a real project usually "
        "takes 15 to 45 minutes of working time, and every job is sized "
        "as a range before it runs (moderate confidence: it depends on "
        "the size of what you ask for).",
        "What you decide: you approve the setup once, agree what I may "
        "do on your behalf, and answer the choices that matter. You can "
        "take the work back at any moment.",
        "",
        "Start: /brotherme-start \"<what you want>\"",
        "More first: /brotherme-help",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _render_once(kv, quiet):
    store = _store_or_refuse(kv, write=True)
    try:
        project_id = kv.get("project-id") or _only_project(store)
        if project_id is None:
            if quiet:
                return 0
            _err("bm_view: --project-id is required when the records "
                 "hold more than one project")
            return 2
        html, fp = build_page(store, project_id)
        out_path = kv.get("out") or bs.safe_project_path(store.root,
                                                         VIEW_FILENAME)
        rel = os.path.relpath(out_path, store.root).replace(os.sep, "/")
        prev = store.latest_view(project_id, "PROJECT_VIEW", raw=True)
        changed = (prev is None or prev.get("fingerprint") != fp
                   or not os.path.isfile(out_path))
        if quiet and not changed:
            return 0
        bs.write_generated_document(out_path, html)
        store.record_view(project_id, {
            "kind": "PROJECT_VIEW", "rel_path": rel, "fingerprint": fp,
            "artifact_url": (prev or {}).get("artifact_url") or "",
            "published_at": (prev or {}).get("published_at") or "",
            "subject": ""}, _view_actor(kv, hook=quiet))
        if quiet:
            return 0
        url = (prev or {}).get("artifact_url") or ""
        if kv.get("json"):
            _print_json({"path": out_path, "rel_path": rel,
                         "fingerprint": fp, "changed": changed,
                         "artifact_url": url})
        else:
            _out("The page is written: %s" % out_path)
            _out("Your records changed since the last write: %s"
                 % ("yes" if changed else "not since the last one"))
            _out("Published page: %s"
                 % (url if url else "not published yet"))
        return 0
    finally:
        _close(store)


def cmd_render(argv):
    _pos, kv = _parse(argv, ("project-id", "if-stale", "out", "json")
                      + _ACTOR_FLAGS,
                      ("project-id", "out") + _ACTOR_FLAGS)
    if kv.get("if-stale"):
        # The hook path. Fail open at every step: a hook line is never a
        # place to print a traceback, and a machine this never set up
        # must hear nothing at all.
        try:
            return _render_once(kv, quiet=True)
        except Exception:
            return 0
    try:
        return _render_once(kv, quiet=False)
    except bs.OwnershipRefused as exc:
        return _refused(exc, "writing the page that shows where your "
                             "project stands")


def cmd_url(argv):
    usage = ("usage: bm-view url --project-id ID [--kind KIND] "
             "[--set URL] [--json]")
    _pos, kv = _parse(argv, ("project-id", "kind", "set", "json")
                      + _ACTOR_FLAGS,
                      ("project-id", "kind", "set") + _ACTOR_FLAGS)
    kind = kv.get("kind") or "PROJECT_VIEW"
    if kind not in bs.VIEW_KINDS:
        _err(usage)
        _err("bm_view: --kind must be one of %s"
             % ", ".join(bs.VIEW_KINDS))
        return 2
    store = None
    try:
        store = _store_or_refuse(kv, write=bool(kv.get("set")))
        project_id = kv.get("project-id") or _only_project(store)
        if project_id is None:
            _err(usage)
            _err("bm_view: --project-id is required when the records "
                 "hold more than one project")
            return 2
        row = store.latest_view(project_id, kind, raw=True)
        if not kv.get("set"):
            url = (row or {}).get("artifact_url") or ""
            if kv.get("json"):
                _print_json({"project_id": project_id, "kind": kind,
                             "artifact_url": url,
                             "fingerprint": (row or {}).get("fingerprint")
                                            or ""})
            else:
                _out(url if url else "not published yet")
            return 0
        if row is None:
            _err("bm_view: there is no page record to attach a link to "
                 "yet; write the page first with: bm-view render "
                 "--project-id %s" % project_id)
            return 1
        written = store.record_view(project_id, {
            "kind": kind, "rel_path": row.get("rel_path") or "",
            "fingerprint": row.get("fingerprint") or "",
            "artifact_url": kv["set"], "published_at": bs.now_iso(),
            "subject": row.get("subject") or ""},
            _view_actor(kv, hook=False))
        if kv.get("json"):
            _print_json(written)
        else:
            _out("The published page address is on record.")
        return 0
    except bs.OwnershipRefused as exc:
        return _refused(exc, "recording where the published page lives")
    finally:
        if store is not None:
            _close(store)


def cmd_doorway(argv):
    _pos, kv = _parse(argv, ("json",))
    consented = _consent_state()
    text = doorway_text(consented)
    if kv.get("json"):
        _print_json({"consented": consented,
                     "doorway": text.splitlines()})
    else:
        _out(text)
    return 0


def cmd_explain(argv):
    usage = "usage: bm-view explain --reason CODE [--json]"
    _pos, kv = _parse(argv, ("reason", "json"), ("reason",))
    code = _require(kv, "reason", usage)
    if code not in bv.REFUSAL_HELP:
        _err("bm_view: %r is not a refusal reason this product emits, so "
             "there is nothing to explain. The code is printed beside "
             "the refusal itself." % code)
        return 1
    if kv.get("json"):
        context, hint, next_action = bv.REFUSAL_HELP[code]
        _print_json({"reason": code, "context": context, "hint": hint,
                     "next_action": next_action})
        return 0
    _out(bv.failure_block(code, "", ""))
    return 0


def cmd_brief_page(argv):
    usage = ("usage: bm-view brief-page --project-id ID --insight-id ID "
             "[--json]")
    _pos, kv = _parse(argv, ("project-id", "insight-id", "json")
                      + _ACTOR_FLAGS,
                      ("project-id", "insight-id") + _ACTOR_FLAGS)
    project_id = _require(kv, "project-id", usage)
    insight_id = _require(kv, "insight-id", usage)
    store = None
    try:
        store = _store_or_refuse(kv, write=True)
        row = store.get_insight(insight_id, raw=True)
        if row is None or (row.get("project_id") or "") != project_id:
            _err("bm_view: no record %r on project %r to build the page "
                 "from" % (insight_id, project_id))
            return 1
        html = render_developer_brief_html(store, row)
        fp = fingerprint(html)
        folder = _ensure_brief_dir(store, store.root, project_id)
        path = os.path.join(folder, "HANDBACK-%s.html" % insight_id)
        rel = os.path.relpath(path, store.root).replace(os.sep, "/")
        bs.write_generated_document(path, html)
        prev = store.latest_view(project_id, "DEVELOPER_BRIEF", raw=True)
        store.record_view(project_id, {
            "kind": "DEVELOPER_BRIEF", "rel_path": rel,
            "fingerprint": fp,
            "artifact_url": (prev or {}).get("artifact_url") or "",
            "published_at": (prev or {}).get("published_at") or "",
            "subject": insight_id}, _view_actor(kv, hook=False))
        if kv.get("json"):
            _print_json({"path": path, "rel_path": rel,
                         "fingerprint": fp})
        else:
            _out("The page for whoever picks this up is written: %s"
                 % path)
        return 0
    except bs.OwnershipRefused as exc:
        return _refused(exc, "writing the page for whoever picks the "
                             "work up")
    finally:
        if store is not None:
            _close(store)


def cmd_alert(argv):
    """The hook entry point, and the only JSON emitting program on the
    Stop line. One object with systemMessage when a NEEDS YOU stands,
    nothing at all otherwise, and fail open at every step: a hook is
    never a place to print a traceback."""
    _pos, kv = _parse(argv, ("tick", "project-id", "json"),
                      ("project-id",))
    store = None
    try:
        store = _store_or_refuse(kv, write=False)
        project_id = kv.get("project-id") or _only_project(store)
        if project_id is None:
            return 0
        needs = [a for a in bv.alerts_now(store, project_id)
                 if a["rung"] == "NEEDS YOU"]
        if not needs:
            return 0
        top = needs[0]
        message = "NEEDS YOU: %s %s" % (top["message"], top["action"])
        message = message.encode("ascii",
                                 "backslashreplace").decode("ascii")
        payload = {"systemMessage": message.strip()}
        if os.environ.get("BROTHERMODE_NO_BELL") != "1":
            payload["terminalSequence"] = "\x07"
        _out(json.dumps(payload, sort_keys=True))
        return 0
    except Exception:
        return 0
    finally:
        if store is not None:
            _close(store)


# ---------------------------------------------------------------------------
# Dispatch. COMMANDS is read in exactly one function, main, and main
# computes the consent state before it ever reaches the lookup.
# ---------------------------------------------------------------------------

COMMANDS = {
    "render": cmd_render,
    "url": cmd_url,
    "doorway": cmd_doorway,
    "explain": cmd_explain,
    "brief-page": cmd_brief_page,
    "alert": cmd_alert,
}


def main(argv):
    argv = list(argv or [])
    consented = _consent_state()
    if not consented and (not argv or argv[0] != "doorway"):
        # THE GATE. The whole dispatch is refused, not one command inside
        # it. The two hook-wired invocations print NOTHING: a hook line
        # is never a place to ask a question, and a stranger's terminal
        # is never a place to announce a product they have not set up.
        # doorway alone passes, because it is the minute zero answer and
        # it writes nothing and opens no store.
        if argv and (argv[0] == "alert"
                     or (argv[0] == "render" and "--if-stale" in argv)):
            return 0
        _out(CONSENT_REFUSAL)
        return 1
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("bm_view: the page that shows where your project stands, "
             "and the doorway.")
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_view: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except ConsentMissing as exc:
        _out(str(exc))
        return 1
    except bs.BMStoreError as exc:
        _err("bm_view: refused: %s" % exc)
        return 1


def cli():
    """Console-script entry point; a packaging entry point must take no
    arguments, which is why this exists beside main()."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

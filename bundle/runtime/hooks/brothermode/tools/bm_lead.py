#!/usr/bin/env python3
"""bm_lead.py: the founder surface, and the engineer's view of the same
records (loop L04, docs/program/absolute-lead/DESIGN-L04.md sections 3, 4,
7, 8, 9, 10, 11 and 12).

WHAT THIS IS
  A THIN CLI over tools/bm_store.py's service methods and read accessors,
  in exactly the sense tools/bm_project.py, tools/bm_autonomy.py and
  tools/bm_controller.py already state as house law: it issues no SQL of
  its own, and a structural guard (tools/test_bm_lead.py, TestNoSQLGuard)
  fails the build if a SQL-shaped literal or a sqlite3 import ever appears
  here. The moment this file needs a query the store does not already
  offer, it has become a second writer and must be folded back into
  bm_store.py instead of growing one here.

THE ONE DOOR, AND WHY IT IS THE FIRST THING IN THIS FILE
  Founder decision, 2026-08-05: the half-hour catch-up ships ON BY DEFAULT
  and NOTHING may write before setup consent. This project has broken that
  law twice, and both times an outside reviewer caught it rather than its
  own tests, so absence of a write is not enough here.

  So: _store_or_refuse is the ONLY function in this file that constructs a
  store, and it refuses without consent. A writer who forgets the check
  cannot obtain a handle at all. main() computes the consent state ONCE,
  before the COMMANDS lookup, and refuses the whole dispatch when it is
  false: a human-invoked command prints the setup sentence, and the
  watchdog prints nothing at all, because a hook is never a place to ask a
  question. Consent itself is not defined here: scripts/setup.py is loaded
  BY PATH and its own read_config and is_consented are called, so there is
  no second definition of what consent means. tools/test_bm_lead.py parses
  this file with ast and fails if a second store constructor is ever
  added.

SUBCOMMANDS
  outcome        where the work stands, and the one recommended next
                  action; with --set, records the outcome the founder
                  states
  status         the eight fields of references/status-view.md, computed
                  from records rather than narrated
  decisions      the open key decisions, highest stakes first, each as a
                  card whose last option is always the handback
  brief          the short catch-up; when nothing has happened it names
                  the one that still stands and writes nothing
  insight        appends one row to the record of what was decided and why
  handback       hands one decision and the work under it back to the
                  founder, in five acts in one fixed order
  handover-pack  the seven handover pages an analyst can take over from
  watchdog       --tick, the hook entry point: a due-check, never a
                  background process

WHAT THIS IS NOT
  Not a second controller driver: nothing here moves a controller run
  state, and no CONTROLLER_STATE_TRANSITIONS entry is used. Not a second
  writer of a project row: the outcome goes through Store.upsert_project,
  the same method tools/bm_project.py's start uses. Not a background
  process: it imports no subprocess, no asyncio, no urllib and no socket,
  and the watchdog is a due-check on a tick the product already has.

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name):
    """Load a sibling module by PATH, off this file's own directory, the
    same technique tools/bm_project.py and tools/bm_controller.py use: a
    hostile sys.path cannot shadow bm_store this way."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")


# ---------------------------------------------------------------------------
# THE CONSENT GATE. Shape copied from tools/bm_telemetry.py's
# _load_bm_setup / _get_bm_setup / _consented, deliberately verbatim in
# structure: scripts/setup.py is the ONE place the consent config schema
# and its reader live, and this is how a second program reaches them
# without a second, drifting copy.
# ---------------------------------------------------------------------------

CONSENT_REFUSAL = ("bm_lead: setup is not complete yet; run: "
                   "python3 scripts/setup.py")


class ConsentMissing(Exception):
    """Raised by _store_or_refuse when setup has not been completed. Never
    caught anywhere that then writes: every caller reports and returns."""


_bm_setup_cache = []


def _load_bm_setup():
    """Load scripts/setup.py by path. Loading a module that itself imports
    subprocess (for its own doctor invocation, never called from the pure
    functions this file uses) does not make THIS file import subprocess:
    the no-subprocess audit in tools/test_bm.py scans source LINES of
    tools/*.py, and no such line is ever written here."""
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_setup_for_lead", os.path.join(REPO, "scripts", "setup.py"))
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
    CLOSED on any load error: an unloadable setup module, a missing config
    or a corrupt one must never be read as permission to write.

    The module object is cached; the CONFIG is not, because read_config
    consults the environment on every call and a test (or a founder who
    completes setup mid-session) must see the change."""
    mod, _load_error = _get_bm_setup()
    if mod is None:
        return False
    try:
        cfg, _cfg_error = mod.read_config()
        return bool(mod.is_consented(cfg))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Plumbing. Shapes copied from tools/bm_project.py (_out, _err, _root,
# _parse, _require, _actor, _ACTOR_FLAGS, _print_json). Do not invent a
# second flag parser.
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
    Every subcommand obtains its handle here or does not have one, which is
    what makes a pre-consent write structurally impossible rather than
    merely absent. `kv` is the parsed flag dict, carried so a future gate
    can read a flag without a second door being added beside this one;
    `write` picks the writable Store over the read-only view.

    create=False on purpose, matching every CLI in this project except
    `bm_store.py init`: a command that creates the records it then reports
    on can report health about an empty shell it just made."""
    if not _consent_state():
        raise ConsentMissing(CONSENT_REFUSAL)
    root = _root()
    if write:
        return bs.Store(root, create=False)
    return bs.ReadOnlyStore(root)


def _close(store):
    try:
        store.close()
    except Exception:  # sbe: allow-silent best-effort close, the command's own result is already computed
        pass


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized. Copied
    from tools/bm_project.py's own _parse, including the --help branch that
    prints the real flags off the tool."""
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
                _err("bm_lead: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_lead: --%s needs a value" % name)
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
        _err("bm_lead: --%s is required" % name)
        sys.exit(2)
    return val


def _actor(kv, usage):
    """The actor dict every mutating subcommand passes to the store, so the
    trail is a real record of who or what acted. Copied from
    tools/bm_project.py's own _actor, including the fresh unguessable
    session id when --session-id is omitted."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_lead: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(2)
    actor_name = _require(kv, "actor-name", usage)
    session_id = kv.get("session-id") or ("cli-" + uuid.uuid4().hex)
    return {"actor_type": actor_type, "actor_name": actor_name,
            "session_id": session_id}


_ACTOR_FLAGS = ("actor-type", "actor-name", "session-id")


def _hook_actor():
    """The actor the watchdog writes under. It is not a human and not a
    model turn: it is the automatic check itself, so it says so."""
    return {"actor_type": "hook", "actor_name": "bm_lead watchdog",
            "session_id": os.environ.get("CLAUDE_SESSION_ID")
                          or ("tick-" + uuid.uuid4().hex)}


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _view_flags(kv):
    """(ic, advanced). IC mode is ALWAYS explicit: the --ic flag, or the
    BROTHERMODE_VIEW=ic environment variable, and nothing else. The
    variable is sticky, which is why every IC render ends in a footer
    naming the switch that turned it on."""
    env = (os.environ.get("BROTHERMODE_VIEW") or "").strip().lower()
    if kv.get("ic"):
        return True, bool(kv.get("advanced")), IC_FOOTER_FLAG
    if env == "ic":
        return True, bool(kv.get("advanced")), IC_FOOTER_ENV
    return False, bool(kv.get("advanced")), ""


# ---------------------------------------------------------------------------
# Founder-facing constants. Every one of these is a promise, so it is a
# module constant a test can assert byte equality against rather than a
# sentence somebody improves in passing.
# ---------------------------------------------------------------------------

HANDBACK_OPTION_TEXT = (
    "Hand this back to me: I take this decision and the work under it, and "
    "BrotherMode records where it stopped and what it would have done.")

PREFERENCE_NOTE = ("declared by the coordinator, not detected from the "
                   "records")

IC_FOOTER_FLAG = ("engineering view is on because you passed --ic; leave "
                  "the flag off for the plain view")

IC_FOOTER_ENV = ("engineering view is on because BROTHERMODE_VIEW is set "
                 "to ic; unset it for the plain view")

DECISION_STAKES = bs.INSIGHT_DECISION_CLASSES

STATUS_FIELDS = ("Goal", "Direction", "Progress", "Time remaining",
                 "Decision needed", "Risk", "Evidence", "Next step")

# The nine items references/status-view.md lines 43 to 51 enumerate, and
# only those nine. They carry the internal wording ON PURPOSE: this block
# renders only when the founder asked for it, which is the one place the
# terminology map allows the machinery to be named.
ADVANCED_ITEMS = ("task ids", "runtime and model",
                  "token input and output", "worktree paths",
                  "commands that were run", "raw test output",
                  "hook evidence", "store verification",
                  "receipt fingerprints")

DEVELOPER_BRIEF_SECTIONS = (
    "What you are taking over",
    "The decision that was in front of us",
    "What BrotherMode would have chosen, and why",
    "The open question",
    "The files",
    "The reproduction",
    "Work that was in flight",
    "Where to pick up",
)

HANDOVER_ROOT = "Handover"

PACK_PAGES = (
    ("00-SITUATION.md", "situation",
     "where the project stands right now, from the records"),
    ("10-DECISIONS.md", "decisions",
     "every decision that was taken, with what else was weighed"),
    ("20-RISKS.md", "risks",
     "what is still open, and what would settle each one"),
    ("30-CALIBRATIONS.md", "calibrations",
     "what was deliberately broken to check a test could see it"),
    ("40-LEARNINGS.md", "learnings",
     "what was learned along the way"),
    ("50-TIMELINE.md", "timeline",
     "the catch-ups in order, oldest first, so a reader replays the work"),
    ("60-HANDBACKS.md", "handbacks",
     "every decision handed back, each with the page a developer picks "
     "the work up from"),
)


# ---------------------------------------------------------------------------
# The plain-language map for controller run states, and its import-time
# guard. Shape copied from the walk-edge guard in tools/bm_controller.py: a
# future run state cannot silently render as a raw enum in front of a
# founder, because this file refuses to import at all if the map has a
# hole.
# ---------------------------------------------------------------------------

RUN_STATE_PLAIN = {
    "NEW": "the work has been set up but has not started yet",
    "ORIENTING": "reading what is already there before planning anything",
    "PLANNING": "breaking the work into steps",
    "READY": "the steps are agreed and the next one can start",
    "EXECUTING": "a step is being worked on right now",
    "VERIFYING": "checking that the last step actually did what it claims",
    "CHECKPOINTED": "the last step passed its check and was saved",
    "WAITING_HUMAN": "waiting on something only you can do",
    "DELIVERABLE_READY": "the work is finished and waiting for you to "
                         "accept it",
    "COMPLETE": "you accepted the work and it is closed",
    "PAUSED": "paused, and it will not move again until you say so",
    "STOPPING": "stopping, and finishing what was already in flight",
    "STOPPED": "stopped",
    "FAILED_RECOVERABLE": "a step failed in a way that can be retried",
    "FAILED_TERMINAL": "a step failed in a way that cannot be retried "
                       "here",
}


def _check_run_state_plain(transitions, plain):
    """Raise when any run state has no plain sentence. Called at import
    time against the shipped map, and callable from a test with an invented
    state so the guard itself is proven to fire."""
    missing = sorted(set(transitions) - set(plain))
    if missing:
        raise RuntimeError(
            "bm_lead: no plain sentence for controller run state(s): %s. "
            "A state a founder can be shown must never render as a raw "
            "name." % ", ".join(missing))
    return True


_check_run_state_plain(bs.CONTROLLER_STATE_TRANSITIONS, RUN_STATE_PLAIN)


# ---------------------------------------------------------------------------
# Small shared renderers
# ---------------------------------------------------------------------------

_EVIDENCE_LABELS = {
    "EXECUTED": "verified by command:",
    "MEASURED": "measured:",
    "READ": "verified by inspection:",
    "REASONED": "my reasoning, not verified:",
}

_EVIDENCE_SUFFIX = {
    "EXECUTED": "EXECUTED",
    "MEASURED": "MEASURED",
    "READ": "READ",
    "REASONED": "REASONED",
}


def evidence_label(evidence_class):
    """The four calibration labels references/honesty.md already defines.
    There is no second vocabulary, and the REASONED label is applied HERE,
    by the renderer, so a claim that was only reasoned about cannot reach
    any surface dressed as a settled fact."""
    return _EVIDENCE_LABELS.get(
        (evidence_class or "").strip().upper(),
        _EVIDENCE_LABELS["REASONED"])


def render_claim_text(insight, ic=False):
    """One claim, with its calibration label in front of it and no trace
    tag. This is what the default view uses: the record id belongs on the
    handover pages, not in a status line."""
    text = "%s %s" % (evidence_label(insight.get("evidence_class")),
                      (insight.get("claim") or "").strip())
    if ic:
        raw = (insight.get("evidence_class") or "").strip().upper()
        detail = (insight.get("evidence") or "").strip()
        text += " [%s%s]" % (_EVIDENCE_SUFFIX.get(raw, raw),
                             (" " + detail) if detail else "")
    return text


def render_claim_line(insight, ic=False):
    """One claim, labelled, ending in its trace tag. Every claim line on a
    generated page ends in one, which is what lets the docs-truth test run
    backwards as well as forwards."""
    return "%s [i:%s]" % (render_claim_text(insight, ic=ic),
                          insight.get("insight_id") or "")


def render_forecast_lines(forecast, prior_count=None):
    """THE ONLY function in this file that formats a duration. Shape copied
    from tools/bm_project.py's _forecast_lines: ranges plus confidence plus
    the next reforecast point, NEVER a point estimate
    (references/forecasting.md's one law). There is no arithmetic on top of
    a forecast anywhere in this file."""
    if not forecast:
        return []
    lines = [
        "  work budget: input %s, output %s, total %s"
        % (forecast.get("input_token_range") or "?",
           forecast.get("output_token_range") or "?",
           forecast.get("effective_total_token_range") or "?"),
        "  confidence: %s" % (forecast.get("confidence") or "not stated"),
        "  next reforecast: %s"
        % (forecast.get("next_reforecast_event") or "(not stated)"),
    ]
    if prior_count is not None:
        lines.append("  earlier estimates: %d" % prior_count)
    return lines


def render_forecast_range(forecast):
    """The one-line range for the Time remaining field. Same function
    family as render_forecast_lines and the same law: never a point."""
    return "%s to %s (likely %s)" % (
        forecast.get("minimum_duration") or "?",
        forecast.get("maximum_duration") or "?",
        forecast.get("likely_duration") or "?")


_AGE_STEPS = ((86400, "day"), (3600, "hour"), (60, "minute"))


def _elapsed(since_iso, now_iso_text):
    """A MEASURED age between two recorded timestamps, in plain words.

    Not an estimate, and that distinction is the reason this function is
    the second (and last) one allowed to name a duration unit: nothing here
    reads a forecast, and both arguments are timestamps that were written
    down. Shape follows tools/bm_autonomy.py's own elapsed helper."""
    start = bs.parse_iso_stamp(since_iso)
    end = bs.parse_iso_stamp(now_iso_text)
    if start is None or end is None:
        return "an unknown time"
    seconds = int((end - start).total_seconds())
    if seconds < 60:
        return "less than a minute"
    for size, word in _AGE_STEPS:
        if seconds >= size:
            count = seconds // size
            return "%d %s%s" % (count, word, "" if count == 1 else "s")
    return "less than a minute"


def render_decision_card(insight, ic=False, header=True, trace=False):
    """THE ONLY function in this file that emits 'Decision needed:', which
    is what makes the handback option impossible to omit from a decision
    window: this function always appends it as the last option line.

    Card shape is references/kickoff.md: the recommended option first with
    its Why, each alternative with its Tradeoff on one line, two to four
    options of which the last is always the handback."""
    lines = []
    subject = (insight.get("subject") or "this decision").strip()
    if header:
        lines.append("Decision needed: %s" % subject)
        lines.append("")
    lines.append("Recommended: %s" % (insight.get("claim") or "").strip())
    basis = (insight.get("confidence_basis") or "").strip()
    why = "%s confidence%s" % (
        (insight.get("confidence") or "moderate"),
        (", because %s" % basis) if basis else "")
    lines.append("Why: %s" % why)
    alternatives = insight.get("alternatives")
    if not isinstance(alternatives, list):
        alternatives = []
    for alt in alternatives[:2]:
        if not isinstance(alt, dict):
            continue
        lines.append("Other option: %s" % (alt.get("option") or "").strip())
        lines.append("Tradeoff: %s" % (alt.get("why_not") or "").strip())
    flip = (insight.get("flip_condition") or "").strip()
    if flip:
        lines.append("What would change my mind: %s" % flip)
    lines.append(render_claim_text(insight, ic=ic))
    if (insight.get("decision_class") or "") == "PREFERENCE":
        lines.append(PREFERENCE_NOTE)
    if ic:
        lines.append("  trigger: %s" % (insight.get("decision_class") or ""))
        lines.append("  record id: %s" % (insight.get("insight_id") or ""))
    lines.append("")
    lines.append("[%s]" % (insight.get("claim") or "this option").strip())
    for alt in alternatives[:2]:
        if isinstance(alt, dict) and (alt.get("option") or "").strip():
            lines.append("[%s]" % alt["option"].strip())
    lines.append(HANDBACK_OPTION_TEXT)
    if trace:
        lines.append("  [i:%s]" % (insight.get("insight_id") or ""))
    return lines


# ---------------------------------------------------------------------------
# Reading the records
# ---------------------------------------------------------------------------

def _ranked_decisions(store, project_id):
    """The open key decisions, highest stakes first, then newest first
    inside a class. The stakes order is DECISION_STAKES, which is data, so
    a fixture can assert it rather than reimplement it."""
    rows = store.open_key_decisions(project_id, raw=True)
    order = {cls: i for i, cls in enumerate(DECISION_STAKES)}
    return sorted(rows, key=lambda r: (order.get(r.get("decision_class"),
                                                 len(order)),))


def _floor_named_by(insight):
    """The safety floor id a decision names, or ''. A decision that touches
    one of the six floors is the one thing nothing else can move past."""
    haystack = " ".join([(insight.get("subject") or ""),
                         (insight.get("claim") or ""),
                         (insight.get("flip_condition") or "")]).lower()
    for floor_id in bs.AUTONOMY_FLOOR_IDS:
        if floor_id in haystack:
            return floor_id
    return ""


def _open_dispatches(store, run, until=None):
    """[(dispatch_id, created_at, unit_id)] for every dispatch still in
    flight on `run`, oldest first. Open means DISPATCHED or RESULT_IN, the
    store's own definition of work in flight."""
    if not run:
        return []
    out = []
    for unit in store.list_units(run["run_id"], raw=True):
        for d in store.list_dispatches(unit["unit_id"], raw=True):
            if d.get("status") not in ("DISPATCHED", "RESULT_IN"):
                continue
            if until and (d.get("created_at") or "") > until:
                continue
            out.append((d["dispatch_id"], d.get("created_at") or "",
                        unit["unit_id"]))
    return sorted(out, key=lambda t: t[1])


def _newest(rows):
    return rows[0] if rows else None


def _ceiling(value):
    """A ceiling as a person reads it. None means no ceiling was agreed,
    and printing the word None at a founder is how a machine's absence
    becomes a number nobody can act on."""
    return "no agreed limit" if value is None else str(value)


def _newest_executed(store, project_id, until=None):
    """The newest insight whose evidence was actually run or measured. This
    is the only thing the Evidence field is ever allowed to show: a read is
    never dressed up as an execution."""
    for row in store.list_insights(project_id, until=until, raw=True):
        if row.get("evidence_class") in ("EXECUTED", "MEASURED"):
            return row
    return None


def _open_risks(store, project_id, since=None, until=None):
    """RISK rows nothing supersedes, newest first."""
    everything = store.list_insights(project_id, until=until, raw=True)
    superseded = {r.get("supersedes") for r in everything if r.get("supersedes")}
    rows = [r for r in everything
            if r.get("kind") == "RISK"
            and r.get("insight_id") not in superseded]
    if since:
        rows = [r for r in rows if (r.get("created_at") or "") > since]
    return rows


def _human_alerts(store):
    """Unresolved alerts a person has to act on. The alerts table is not
    project scoped (one project per folder is the beginner model), so this
    filters on requires_human alone and says so."""
    return [a for a in store.list_alerts(resolved=False, raw=True)
            if a.get("requires_human")]


# ---------------------------------------------------------------------------
# Exactly one recommended next action: a first-match router over records,
# so two runs on the same records always agree.
# ---------------------------------------------------------------------------

def _next_action_branch(store, project_id):
    """(branch_id, text, why, command). The branch id is returned so a
    fixture can assert WHICH branch fired rather than guessing from the
    wording; next_action below is the three-value form the design names."""
    project = store.get_project(project_id, raw=True) or {}
    decisions = _ranked_decisions(store, project_id)
    steps = store.list_human_steps(project_id, resolved=False, raw=True)
    run = store.get_run(project_id, raw=True)
    contract = store.latest_contract(project_id, raw=True)

    # Branch 1 keys on the decision CLASS, not on the words in the
    # decision's prose. GATE is the class key_decision_class returns for
    # exactly the floor case, and DECISION_STAKES sorts it first, so the
    # highest-stakes open decision is a floor decision precisely when its
    # class is GATE. Matching floor ids inside free text instead was the
    # first version of this branch and it was wrong in a way a test caught:
    # "the payment approach" contains the floor id "payment", so an
    # ordinary pricing decision was reported as a safety floor.
    if decisions and decisions[0].get("decision_class") == "GATE":
        floor = _floor_named_by(decisions[0])
        return ("answer-floor-decision",
                "answer the decision about %s"
                % (decisions[0].get("subject") or "the work"),
                "it touches %s, which only you are allowed to decide, so "
                "nothing else can move until you answer it"
                % (bs.AUTONOMY_FLOOR_DESCRIPTIONS[floor] if floor
                   else "something the rules never let me do on my own"),
                "bm-lead decisions --project-id %s" % project_id)
    if steps:
        step = steps[0]
        click = (step.get("click_path") or "").strip()
        return ("founder-step",
                (step.get("what") or "do the step waiting on you").strip(),
                "this is a step only you can do%s"
                % ((": " + click) if click else ""),
                "bm-lead status --project-id %s" % project_id)
    if decisions:
        return ("answer-decision",
                "answer the decision about %s"
                % (decisions[0].get("subject") or "the work"),
                "the work is waiting on your answer",
                "bm-lead decisions --project-id %s" % project_id)
    if run and run.get("state") == "DELIVERABLE_READY":
        return ("accept-work", "accept the finished work",
                "every planned step passed its check",
                "bm-controller deliver --project-id %s" % project_id)
    if run and _open_dispatches(store, run):
        return ("finish-work-in-flight", "let the work in flight finish",
                "%d piece(s) of work are already running"
                % len(_open_dispatches(store, run)),
                "bm-lead status --project-id %s --ic" % project_id)
    if (project.get("goal") or "").strip() and (
            contract is None or contract.get("state") != "live"):
        return ("agree-authorisation",
                "agree what I am allowed to do on your behalf",
                "the outcome is recorded, and nothing can run until you "
                "authorise it",
                # --project, NOT --project-id: tools/bm_autonomy.py cmd_sign
                # parses with wants_value=("project", ...) and calls
                # _require(kv, "project"), and its parser refuses an
                # unrecognised flag, so the id form died on paste. This line
                # is the single recommended next action a beginner is handed
                # at the authorisation step, which made it the worst possible
                # place for a wrong flag name.
                "bm-autonomy sign --project %s" % project_id)
    if not (project.get("goal") or "").strip():
        return ("state-outcome", "tell me the outcome you want",
                "nothing is recorded yet, so there is nothing to plan "
                "against",
                "bm-lead outcome --project-id %s --set \"<what you want>\""
                % project_id)
    ready = store.list_tasks(project_id, status="ready", raw=True)
    if ready:
        picked = ready[0]
        return ("continue-task",
                "carry on with %s" % (picked.get("title") or "the next "
                                      "piece of work"),
                "%d piece(s) of work can be picked up now; this is the "
                "highest priority one" % len(ready),
                "bm-project next --project-id %s" % project_id)
    return ("nothing-waiting", "nothing is waiting on you",
            "there is no open decision, no step for you and no work ready "
            "to pick up; adding work or agreeing a plan is what creates "
            "some",
            "bm-lead outcome --project-id %s" % project_id)


def next_action(store, project_id):
    """One (text, why, command) triple. Exactly one action, always."""
    _branch, text, why, command = _next_action_branch(store, project_id)
    return text, why, command


def key_decision_class(store, project_id, context):
    """'' or one of DECISION_STAKES, first match wins, in stakes order.

    Four of the five are DETECTED from records; PREFERENCE is DECLARED and
    the card says so on that row, because a choice between designs whose
    only flip condition is founder taste is not something a record can
    reveal."""
    context = context or {}
    floor_id = (context.get("floor_id") or "").strip()
    if floor_id and floor_id in bs.AUTONOMY_FLOOR_IDS:
        return "GATE"
    blocks = context.get("blocks") or []
    for step in store.list_human_steps(project_id, resolved=False, raw=True):
        if not (step.get("floor") or "").strip():
            continue
        named = step.get("blocks") or []
        if not blocks or any(b in named for b in blocks):
            return "GATE"
    if context.get("contract_change"):
        return "RULE"
    for path in context.get("write_scope") or []:
        base = os.path.basename(str(path))
        parts = str(path).replace("\\", "/").split("/")
        if base.startswith("test_") or "tests" in parts:
            return "TEST"
    run = store.get_run(project_id, raw=True)
    if run:
        for unit in store.list_units(run["run_id"], raw=True):
            if unit.get("status") in ("SKIPPED", "BLOCKED"):
                return "DEFERRAL"
    if store.list_interruptions(project_id, answered=False, raw=True):
        return "DEFERRAL"
    if (context.get("declared") or "") == "PREFERENCE":
        return "PREFERENCE"
    return ""


# ---------------------------------------------------------------------------
# The eight fields: one collector, two renderers.
# ---------------------------------------------------------------------------

def collect_status(store, project_id):
    """Every field computed from records, or a sentence saying it cannot be
    computed. Where a field is absent it SAYS so rather than being dropped,
    per references/honesty.md.

    Returns {'fields': [(label, value, [extra lines])], 'advanced': [...],
    'engineering': [...], 'next': (text, why, command)}. One collector, and
    the two renderers below share it, which is what keeps the founder view
    and the engineer view from drifting."""
    project = store.get_project(project_id, raw=True) or {}
    contract = store.latest_contract(project_id, raw=True)
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    forecast = store.latest_forecast(project_id, raw=True)
    decisions = _ranked_decisions(store, project_id)
    latest_brief = store.latest_briefing(project_id, raw=True)
    since = latest_brief.get("created_at") if latest_brief else None
    risks = _open_risks(store, project_id, since=since)
    alerts = _human_alerts(store)
    executed = _newest_executed(store, project_id)
    spend = store.spend_totals(project_id)
    fields = []

    goal = (project.get("goal") or "").strip()
    goal_extra = []
    if not goal:
        goal = "no outcome recorded yet"
    elif contract and (contract.get("outcome") or "").strip() and (
            contract["outcome"].strip() != goal):
        goal_extra.append(
            "  worth flagging: what you authorised says %r, which is not "
            "the same sentence. The record above is what I work from."
            % contract["outcome"].strip())
    fields.append(("Goal", goal, goal_extra))

    direction = ""
    newest_decision = _newest(store.list_insights(project_id, kind="DECISION",
                                                  raw=True))
    if newest_decision:
        direction = (newest_decision.get("claim") or "").strip()
    if not direction and contract:
        direction = (contract.get("done_definition") or "").strip()
    if not direction:
        direction = (project.get("phase") or "").strip()
    fields.append(("Direction", direction or "not agreed yet", []))

    if run and units:
        done = len([u for u in units if u.get("status") == "DONE"])
        progress = "%d of %d steps accepted" % (done, len(units))
    else:
        tasks = store.list_tasks(project_id, raw=True)
        if tasks:
            done = len([t for t in tasks
                        if (t.get("status") or "") in ("accepted", "done",
                                                       "delivered")])
            progress = "%d of %d tasks accepted" % (done, len(tasks))
        else:
            progress = "nothing planned yet"
    fields.append(("Progress", progress, []))

    if forecast:
        fields.append(("Time remaining", render_forecast_range(forecast),
                       render_forecast_lines(forecast)))
    else:
        fields.append(("Time remaining", "not forecast yet",
                       ["  I can record one as soon as there is enough to "
                        "size."]))

    if decisions:
        card = render_decision_card(decisions[0], header=False)
        fields.append(("Decision needed",
                       (decisions[0].get("subject") or "one decision"),
                       ["  " + ln if ln else "" for ln in card]))
    else:
        fields.append(("Decision needed", "none", []))

    risk_extra = []
    for row in risks[:3]:
        risk_extra.append("  " + render_claim_text(row))
    for alert in alerts[:3]:
        risk_extra.append("  %s" % (alert.get("message") or "").strip())
    if risk_extra:
        summary = "%d open" % (len(risks) + len(alerts))
    else:
        summary = "none new"
    fields.append(("Risk", summary, risk_extra))

    if executed:
        evidence = render_claim_text(executed)
        extra = ["  %s" % (executed.get("evidence") or "").strip()]
    else:
        evidence = "no executed evidence recorded yet"
        extra = []
    fields.append(("Evidence", evidence, extra))

    text, why, command = next_action(store, project_id)
    fields.append(("Next step", text, ["  why: %s" % why,
                                       "  run: %s" % command]))

    advanced = _advanced_block(store, project_id, run, units, spend)
    engineering = _engineering_block(store, project_id, run, units, spend,
                                     contract, decisions, executed,
                                     latest_brief)
    return {"project_id": project_id, "fields": fields,
            "advanced": advanced, "engineering": engineering,
            "next": (text, why, command)}


def _advanced_block(store, project_id, run, units, spend):
    """The nine items references/status-view.md lines 43 to 51 enumerate,
    in that order. Per request, never sticky: nothing here is remembered
    between calls."""
    unit_ids = ", ".join(u["unit_id"] for u in units) or "none recorded"
    commands = ", ".join(sorted({(u.get("done_check") or "").strip()
                                 for u in units
                                 if (u.get("done_check") or "").strip()}))
    executed = _newest_executed(store, project_id)
    tasks = store.list_tasks(project_id, raw=True)
    return [
        ("task ids", ", ".join(t["task_id"] for t in tasks)
         or "none recorded"),
        ("runtime and model", ", ".join(sorted({
            (u.get("model_class") or "").strip() for u in units
            if (u.get("model_class") or "").strip()})) or "none recorded"),
        ("token input and output", "%s used against a ceiling of %s"
         % (spend.get("tokens"), _ceiling(spend.get("token_ceiling")))),
        ("worktree paths", "none: this project works in place"),
        ("commands that were run", commands or "none recorded"),
        ("raw test output", "not stored here; the done-check exit codes "
                            "are on the dispatch rows"),
        ("hook evidence", "the catch-up check runs on the Stop hook"),
        ("store verification", "unit ids: %s" % unit_ids),
        ("receipt fingerprints", (executed or {}).get("insight_id")
         or "none recorded"),
    ]


def _engineering_block(store, project_id, run, units, spend, contract,
                       decisions, executed, latest_brief):
    """The same records, read by the person doing the engineering. Not a
    second data path: every value here comes from the same reads
    collect_status already made."""
    rows = []
    if run:
        rows.append(("file claim", "%s (%s)"
                     % (run.get("fence_uuid"), run.get("state"))))
        rows.append(("run", "%s, state %s"
                     % (run.get("run_id"), run.get("state"))))
        rows.append(("units", ", ".join(
            "%s=%s" % (u["unit_id"], u.get("status")) for u in units)
            or "none"))
        open_dispatches = _open_dispatches(store, run)
        rows.append(("open dispatches", ", ".join(
            "%s (%s old)" % (d[0], _elapsed(d[1], bs.now_iso()))
            for d in open_dispatches) or "none"))
    else:
        rows.append(("file claim", "none held"))
        rows.append(("run", "none"))
        rows.append(("units", "none"))
        rows.append(("open dispatches", "none"))
    rows.append(("spend", "%s tokens against %s, %s minutes against %s "
                          "(verdict %s)"
                 % (spend.get("tokens"), _ceiling(spend.get("token_ceiling")),
                    spend.get("minutes"),
                    _ceiling(spend.get("minutes_ceiling")),
                    spend.get("verdict"))))
    if executed:
        rows.append(("evidence", "%s %s %s"
                     % (executed.get("evidence_class"),
                        executed.get("insight_id"),
                        (executed.get("evidence") or "").strip())))
    else:
        rows.append(("evidence", "none recorded"))
    if decisions:
        rows.append(("decision", "%s, %s"
                     % (decisions[0].get("decision_class"),
                        PREFERENCE_NOTE
                        if decisions[0].get("decision_class") == "PREFERENCE"
                        else "detected from the records")))
    else:
        rows.append(("decision", "none open"))
    if contract:
        rows.append(("contract", "%s revision %s, state %s"
                     % (contract.get("contract_id"),
                        contract.get("revision"), contract.get("state"))))
    else:
        rows.append(("contract", "none signed"))
    baseline = (latest_brief or {}).get("created_at")
    if baseline is None:
        project = store.get_project(project_id, raw=True) or {}
        baseline = project.get("created_at") or bs.now_iso()
    stats = store.active_minutes_since(project_id, baseline)
    rows.append(("catch-up clock", "%d active minute(s), %d event(s), %d "
                                   "skipped, threshold %d"
                 % (stats["active_minutes"], stats["events"],
                    stats["skipped"], bs.BRIEFING_ACTIVE_MINUTES)))
    return rows


def render_status(view, ic=False, advanced=False, footer=""):
    """The eight fields, in order, and nothing else at column zero unless
    the reader explicitly asked for more."""
    lines = []
    for label, value, extra in view["fields"]:
        lines.append("%s: %s" % (label, value))
        lines.extend(extra)
    if advanced:
        lines.append("")
        lines.append("Advanced view, because you asked for it (it is not "
                     "sticky: the next plain status drops it again):")
        for label, value in view["advanced"]:
            lines.append("  %s: %s" % (label, value))
    if ic:
        lines.append("")
        lines.append("Engineering view:")
        for label, value in view["engineering"]:
            lines.append("  %s: %s" % (label, value))
        if footer:
            lines.append("  %s" % footer)
    return lines


# ---------------------------------------------------------------------------
# The active-work clock and the half-hour catch-up
# ---------------------------------------------------------------------------

def briefing_due(store, project_id, now):
    """(due, trigger, stats, previous_briefing_row_or_None). First match
    wins. REQUESTED is never returned here: it is the trigger `bm-lead
    brief` writes when a human asked."""
    previous = store.latest_briefing(project_id, raw=True)
    project = store.get_project(project_id, raw=True) or {}
    baseline = (previous or {}).get("created_at") or project.get("created_at")
    if not baseline:
        return False, "", {"active_minutes": 0, "events": 0, "skipped": 0}, \
            previous
    stats = store.active_minutes_since(project_id, baseline, now=now)
    if stats["active_minutes"] >= bs.BRIEFING_ACTIVE_MINUTES:
        return True, "ACTIVE_MINUTES", stats, previous
    run = store.get_run(project_id, raw=True)
    run_state = (run or {}).get("state") or ""
    open_steps = len(store.list_human_steps(project_id, resolved=False,
                                            raw=True))
    prev_state = (previous or {}).get("run_state") or ""
    prev_steps = (previous or {}).get("open_steps") or 0
    if run_state != prev_state or open_steps != prev_steps:
        return True, "PHASE_BOUNDARY", stats, previous
    return False, "", stats, previous


def collect_briefing(store, project_id, previous, now=None):
    """The payload record_briefing accepts, and nothing else: every key
    here is in bs.BRIEFING_FIELDS, so a typo is a loud refusal rather than
    a silent drop."""
    now = now or bs.now_iso()
    since = (previous or {}).get("created_at")
    run = store.get_run(project_id, raw=True)
    project = store.get_project(project_id, raw=True) or {}
    units = store.list_units(run["run_id"], raw=True) if run else []
    spend = store.spend_totals(project_id)
    forecast = store.latest_forecast(project_id, raw=True)
    steps = store.list_human_steps(project_id, resolved=False, raw=True)
    baseline = since or project.get("created_at") or now
    stats = store.active_minutes_since(project_id, baseline, now=now)

    if run:
        where = RUN_STATE_PLAIN[run["state"]]
        if units:
            done = len([u for u in units if u.get("status") == "DONE"])
            where += " (%d of %d steps accepted)" % (done, len(units))
    else:
        where = (project.get("phase") or "").strip() \
            or "no work has been started yet"

    events = store.list_attribution(project_id, limit=200, raw=True)
    fresh = [e for e in events
             if not since or (e.get("timestamp") or "") > since]
    counts = {}
    for e in fresh:
        counts[e.get("event_type")] = counts.get(e.get("event_type"), 0) + 1
    changed = ", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items()))
    executed = _newest_executed(store, project_id)
    if executed:
        changed = (changed + "; " if changed else "") + render_claim_text(
            executed)
    if not changed:
        changed = "nothing has changed since the last catch-up"

    cost = "%s tokens and %s minutes of model work so far" % (
        spend.get("tokens"), spend.get("minutes"))
    if spend.get("token_ceiling") is not None:
        cost += ", against what you authorised (%s tokens, %s minutes)" % (
            spend.get("token_ceiling"), _ceiling(spend.get("minutes_ceiling")))
    if forecast:
        cost += ". What is left: %s" % render_forecast_range(forecast)

    decisions = store.list_insights(project_id, kind="DECISION", since=since,
                                    raw=True)
    risks = _open_risks(store, project_id)
    return {"trigger": "REQUESTED", "active_minutes": stats["active_minutes"],
            "event_count": stats["events"],
            "skipped_events": stats["skipped"],
            "since_briefing": (previous or {}).get("briefing_id") or "",
            "run_state": (run or {}).get("state") or "",
            "open_steps": len(steps), "where_we_are": where,
            "what_changed": changed, "what_it_cost": cost,
            "decision_insight": (_newest(decisions) or {}).get("insight_id")
                                or "",
            "risk_insight": (_newest(risks) or {}).get("insight_id") or ""}


def _briefing_view(store, row):
    """The stored row plus the two claim lines the renderer prints.

    record_briefing accepts only bs.BRIEFING_FIELDS, so these two keys are
    added AFTER the write and are never sent to it: the row holds the
    record ids, and the text is looked up from those records, so a catch-up
    can never quote a claim the ledger does not hold."""
    view = dict(row)
    for key, target in (("decision_insight", "decision_line"),
                        ("risk_insight", "risk_line")):
        row_id = (row.get(key) or "").strip()
        insight = store.get_insight(row_id, raw=True) if row_id else None
        if insight is None:
            view[target] = ""
            continue
        line = render_claim_text(insight)
        flip = (insight.get("flip_condition") or "").strip()
        alternatives = insight.get("alternatives")
        if target == "decision_line" and isinstance(alternatives, list) \
                and alternatives and isinstance(alternatives[0], dict):
            line += " (instead of %s: %s)" % (
                alternatives[0].get("option") or "",
                alternatives[0].get("why_not") or "")
        if flip:
            line += ". What would settle it: %s" % flip
        view[target] = line
    return view


def render_briefing(row, ic=False, footer=""):
    """Six lines at most, and the last one is always the founder's own
    option. Obeys references/pulse.md's anti-noise rules: one cause, one
    line, impact before technical cause, one recommended action."""
    lines = ["Where we are: %s" % (row.get("where_we_are") or "").strip(),
             "What changed: %s" % (row.get("what_changed") or "").strip(),
             "What it cost: %s" % (row.get("what_it_cost") or "").strip()]
    if (row.get("decision_line") or "").strip():
        lines.append("What I decided: %s" % row["decision_line"])
    if (row.get("risk_line") or "").strip():
        lines.append("What I am unsure of: %s" % row["risk_line"])
    else:
        lines.append("What I am unsure of: nothing new is open")
    if ic:
        lines.append("  clock: %s active minute(s), %s event(s), %s skipped"
                     % (row.get("active_minutes"), row.get("event_count"),
                        row.get("skipped_events")))
        lines.append("  trigger: %s, run state %s, open steps %s"
                     % (row.get("trigger"), row.get("run_state"),
                        row.get("open_steps")))
        if footer:
            lines.append("  %s" % footer)
    lines.append("Your options: %s" % HANDBACK_OPTION_TEXT)
    return lines


def _emit_briefing(store, project_id, trigger, stats, previous, actor,
                   now=None):
    """The ONLY caller of Store.record_briefing.

    Re-reads the latest catch-up immediately before writing and refuses
    when it has moved since `previous` was read, so two ticks racing on one
    due window write ONE row and the loser writes nothing. Returns the
    stored row, or None when it declined."""
    latest = store.latest_briefing(project_id, raw=True)
    if ((latest or {}).get("briefing_id") or "") != \
            ((previous or {}).get("briefing_id") or ""):
        return None
    payload = collect_briefing(store, project_id, previous, now=now)
    payload["trigger"] = trigger
    if stats:
        payload["active_minutes"] = stats.get("active_minutes", 0)
        payload["event_count"] = stats.get("events", 0)
        payload["skipped_events"] = stats.get("skipped", 0)
    written = store.record_briefing(project_id, payload, actor)
    return store.latest_briefing(project_id, raw=True) or written


# ---------------------------------------------------------------------------
# The handover pages
# ---------------------------------------------------------------------------

def _ensure_pack_dir(store, root, project_id):
    """THE ONLY directory-creating call in this file, and it cannot be
    reached without the store handle _store_or_refuse returned, which is
    the same reachability argument that gates every generated page.

    One project per folder is the beginner model, so the pages land in
    Handover/. A store holding several projects gets
    Handover-<project_id>/, mirroring the rule
    tools/bm_project.py's _canvas_filename already applies."""
    name = HANDOVER_ROOT
    if len(store.list_projects(raw=True)) > 1:
        name = "%s-%s" % (HANDOVER_ROOT, project_id)
    path = bs.safe_project_path(root, name)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _page_header(rel, what):
    return ["# %s" % rel[:-3].replace("-", " ").strip(),
            "",
            what + ".",
            "",
            "Every claim below ends in the id of the record it came from, "
            "so any line can be traced back.",
            ""]


def _render_situation(store, project_id, until):
    project = store.get_project(project_id, raw=True) or {}
    contract = store.latest_contract(project_id, raw=True)
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    forecast = store.latest_forecast(project_id, raw=True)
    spend = store.spend_totals(project_id)
    steps = store.list_human_steps(project_id, resolved=False, raw=True)
    by_status = {}
    for unit in units:
        by_status[unit.get("status")] = by_status.get(unit.get("status"),
                                                      0) + 1
    lines = []
    lines.append("## The outcome")
    lines.append("")
    lines.append((project.get("goal") or "not recorded").strip())
    lines.append("")
    lines.append("## The authorisation")
    lines.append("")
    if contract:
        lines.append("revision %s, state %s, done when: %s"
                     % (contract.get("revision"), contract.get("state"),
                        (contract.get("done_definition") or "").strip()))
    else:
        lines.append("none signed")
    lines.append("")
    lines.append("## What has been spent")
    lines.append("")
    lines.append("%s tokens against %s, %s minutes against %s"
                 % (spend.get("tokens"), _ceiling(spend.get("token_ceiling")),
                    spend.get("minutes"),
                    _ceiling(spend.get("minutes_ceiling"))))
    lines.append("")
    lines.append("## The work")
    lines.append("")
    if run:
        lines.append("run %s, state %s (%s)"
                     % (run.get("run_id"), run.get("state"),
                        RUN_STATE_PLAIN[run["state"]]))
        lines.append("")
        for status in sorted(by_status):
            lines.append("- %s: %d" % (status, by_status[status]))
    else:
        lines.append("no run has been opened")
    lines.append("")
    lines.append("## What is still estimated")
    lines.append("")
    if forecast:
        lines.append(render_forecast_range(forecast))
        lines.extend(render_forecast_lines(forecast))
    else:
        lines.append("no estimate has been recorded")
    lines.append("")
    lines.append("## Waiting on a person")
    lines.append("")
    if steps:
        for step in steps:
            lines.append("- %s" % (step.get("what") or "").strip())
    else:
        lines.append("nothing")
    lines.append("")
    return lines


def _render_insight_page(store, project_id, until, kind, extra=None):
    everything = store.list_insights(project_id, until=until, raw=True)
    superseded = {r.get("supersedes") for r in everything
                  if r.get("supersedes")}
    rows = [r for r in everything if r.get("kind") == kind]
    if kind == "RISK":
        rows = [r for r in rows if r.get("insight_id") not in superseded]
    lines = []
    if not rows:
        lines.append("Nothing was recorded under this heading.")
        lines.append("")
        return lines
    if kind == "DECISION":
        for row in rows:
            lines.extend(render_decision_card(row, trace=True))
            lines.append("")
        return lines
    for row in rows:
        lines.append("- %s" % render_claim_line(row))
        if (row.get("subject") or "").strip():
            lines.append("  about: %s" % row["subject"].strip())
        for key, label in (extra or []):
            if (row.get(key) or "").strip():
                lines.append("  %s: %s" % (label, row[key].strip()))
        lines.append("")
    return lines


def _render_timeline(store, project_id, until):
    rows = store.list_briefings(project_id, until=until, raw=True)
    # The store orders newest first on every list accessor, on purpose, so
    # two readers cannot disagree about which row is the latest. A timeline
    # replays the work FORWARDS, so the reversal happens here.
    lines = []
    if not rows:
        lines.append("No catch-up has been recorded yet.")
        lines.append("")
        return lines
    for row in reversed(rows):
        view = _briefing_view(store, row)
        lines.append("## %s [b:%s]" % (row.get("created_at"),
                                       row.get("briefing_id")))
        lines.append("")
        for line in render_briefing(view):
            lines.append(line)
        lines.append("")
    return lines


def _render_handbacks(store, project_id, until):
    rows = [r for r in store.list_insights(project_id, kind="HANDBACK",
                                           until=until, raw=True)]
    lines = []
    if not rows:
        lines.append("No decision has been handed back.")
        lines.append("")
        return lines
    for row in rows:
        lines.append(render_developer_brief(store, row).strip())
        lines.append("")
    return lines


_PAGE_RENDERERS = {
    "00-SITUATION.md": lambda s, p, u: _render_situation(s, p, u),
    "10-DECISIONS.md": lambda s, p, u: _render_insight_page(
        s, p, u, "DECISION"),
    "20-RISKS.md": lambda s, p, u: _render_insight_page(
        s, p, u, "RISK", [("flip_condition", "what would settle it")]),
    "30-CALIBRATIONS.md": lambda s, p, u: _render_insight_page(
        s, p, u, "CALIBRATION", [("mutation", "what was broken on purpose"),
                                 ("observed", "what the check then saw")]),
    "40-LEARNINGS.md": lambda s, p, u: _render_insight_page(
        s, p, u, "LEARNING"),
    "50-TIMELINE.md": lambda s, p, u: _render_timeline(s, p, u),
    "60-HANDBACKS.md": lambda s, p, u: _render_handbacks(s, p, u),
}


def render_pack_page(store, project_id, rel, until):
    """One page, from records, with no render timestamp anywhere: a
    regenerated page must be byte stable from the same records, which is
    the rule tools/bm_project.py's render_canvas states."""
    what = [w for r, _name, w in PACK_PAGES if r == rel][0]
    lines = _page_header(rel, what)
    lines.extend(_PAGE_RENDERERS[rel](store, project_id, until))
    return "\n".join(lines).rstrip() + "\n"


def write_pack(store, project_id, root, until):
    """Every page, through bs.write_generated_document, the one file
    funnel. Returns the paths written, in PACK_PAGES order."""
    folder = _ensure_pack_dir(store, root, project_id)
    written = []
    for rel, _name, _what in PACK_PAGES:
        path = bs.safe_project_path(folder, rel)
        bs.write_generated_document(path, render_pack_page(
            store, project_id, rel, until))
        written.append(path)
    return written


# ---------------------------------------------------------------------------
# The developer brief
# ---------------------------------------------------------------------------

def render_developer_brief(store, handback_insight):
    """The whole page a developer picks the work up from.

    Generated from records filtered by created_at <= the handback's own
    timestamp, so regenerating it a week later reproduces it byte for byte.
    Ages are measured against that same cut rather than against the clock,
    for the same reason."""
    until = handback_insight.get("created_at")
    project_id = handback_insight.get("project_id")
    project = store.get_project(project_id, raw=True) or {}
    contract = store.latest_contract(project_id, raw=True)
    run = store.get_run(project_id, raw=True)
    units = store.list_units(run["run_id"], raw=True) if run else []
    decision = None
    if (handback_insight.get("supersedes") or "").strip():
        decision = store.get_insight(handback_insight["supersedes"],
                                     raw=True)
    lines = ["# Handing this back: %s"
             % (handback_insight.get("subject") or "one decision").strip(),
             ""]

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[0])
    lines.append("")
    lines.append("The outcome: %s" % ((project.get("goal") or "").strip()
                                      or "not recorded"))
    lines.append("Done when: %s"
                 % (((contract or {}).get("done_definition") or "").strip()
                    or "not agreed"))
    if run:
        lines.append("Where it stands: %s" % RUN_STATE_PLAIN[run["state"]])
    else:
        lines.append("Where it stands: no run has been opened")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[1])
    lines.append("")
    if decision:
        lines.extend(render_decision_card(decision, trace=True))
    else:
        lines.append("The decision record could not be resolved.")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[2])
    lines.append("")
    lines.append(render_claim_line(handback_insight))
    flip = (handback_insight.get("flip_condition") or "").strip()
    if flip:
        lines.append("")
        lines.append("What would have changed that choice: %s" % flip)
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[3])
    lines.append("")
    payload = None
    if run:
        # Narrow on purpose. An earlier version caught bare Exception here,
        # and it hid a real defect for one test run: the pack opened a
        # read-only handle, which has no handover_payload, so the pack's
        # copy of this page silently lost three sections while the
        # standalone copy kept them. A store refusal is a fact about the
        # records; an AttributeError is a fact about this file, and only
        # the first one belongs in a page.
        try:
            payload = store.handover_payload(run["fence_uuid"])
        except bs.BMStoreError:
            payload = None
    digest = (payload or {}).get("digest") or {}
    lines.append((digest.get("blockers") or "").strip()
                 or "no open question was recorded")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[4])
    lines.append("")
    claimed = sorted((payload or {}).get("files") or [])
    for path in claimed:
        lines.append("- %s" % path)
    for unit in units:
        if unit.get("status") == "DONE":
            continue
        for path in unit.get("write_scope") or []:
            lines.append("- %s (from step %s, still %s)"
                         % (path, unit["unit_id"], unit.get("status")))
    if not claimed and not units:
        lines.append("no files were claimed")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[5])
    lines.append("")
    checks = sorted({(u.get("done_check") or "").strip() for u in units
                     if (u.get("done_check") or "").strip()})
    for check in checks:
        lines.append("- %s" % check)
    executed = _newest_executed(store, project_id, until=until)
    if executed:
        lines.append("- the last command that was actually run: %s"
                     % render_claim_line(executed))
    if not checks and not executed:
        lines.append("no check command was recorded")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[6])
    lines.append("")
    open_dispatches = _open_dispatches(store, run, until=until)
    if open_dispatches:
        for dispatch_id, created_at, unit_id in open_dispatches:
            lines.append("- %s on step %s, open for %s"
                         % (dispatch_id, unit_id,
                            _elapsed(created_at, until)))
        lines.append("")
        lines.append("These were still running when control changed hands. "
                     "Nothing cancelled them, because cancelling work from "
                     "outside the engine that started it is how a "
                     "half-finished handback becomes a running robot.")
    else:
        lines.append("none")
    lines.append("")

    lines.append("## %s" % DEVELOPER_BRIEF_SECTIONS[7])
    lines.append("")
    lines.append((digest.get("next_intent") or "").strip()
                 or "no next step was recorded")
    lines.append("")
    lines.append("To resume: `bm-autonomy resume --project-id %s` to put "
                 "the authorisation back to live, then `bm-controller "
                 "start` with the same controller id%s."
                 % (project_id,
                    (" (%s)" % run.get("controller_id")) if run else ""))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def render_error_card(what_happened, impact, recommended, safe):
    """references/kickoff.md's four sections, always all four, always in
    that order, user impact before technical cause."""
    return ["What happened", what_happened, "",
            "Impact", impact, "",
            "Recommended action", recommended, "",
            "What remains safe", safe]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

_VIEW_FLAGS = ("ic", "advanced")


def cmd_outcome(argv):
    _pos, kv = _parse(argv, ("project-id", "set", "name", "json", "ic",
                             "advanced") + _ACTOR_FLAGS,
                      wants_value=("project-id", "set", "name")
                      + _ACTOR_FLAGS)
    usage = ("usage: outcome --project-id ID [--set \"<what you want>\"] "
             "[--name NAME] [--ic] [--json] [--actor-type human|model] "
             "--actor-name NAME")
    project_id = _require(kv, "project-id", usage)
    ic, advanced, footer = _view_flags(kv)
    wants_set = isinstance(kv.get("set"), str)
    store = _store_or_refuse(kv, write=wants_set)
    try:
        if wants_set:
            actor = _actor(kv, usage)
            existing = store.get_project(project_id, raw=True) or {}
            now = bs.now_iso()
            project = dict(existing)
            project.update({
                "project_id": project_id,
                "name": kv.get("name") or existing.get("name") or project_id,
                "goal": kv["set"].strip(),
                "created_at": existing.get("created_at") or now,
                "updated_at": now})
            store.upsert_project(project, actor)
        view = collect_status(store, project_id)
        fields = dict((label, (value, extra))
                      for label, value, extra in view["fields"])
        if kv.get("json"):
            _print_json({"project_id": project_id,
                         "goal": fields["Goal"][0],
                         "progress": fields["Progress"][0],
                         "next": view["next"]})
            return 0
        _out("Goal: %s" % fields["Goal"][0])
        _out("Progress: %s" % fields["Progress"][0])
        text, why, command = view["next"]
        _out("Next step: %s" % text)
        _out("  why: %s" % why)
        _out("  run: %s" % command)
        if ic and footer:
            _out("  %s" % footer)
        return 0
    finally:
        _close(store)


def cmd_status(argv):
    _pos, kv = _parse(argv, ("project-id", "json", "raw") + _VIEW_FLAGS,
                      wants_value=("project-id",))
    usage = ("usage: status --project-id ID [--ic] [--advanced] [--json] "
             "[--raw]")
    project_id = _require(kv, "project-id", usage)
    ic, advanced, footer = _view_flags(kv)
    store = _store_or_refuse(kv, write=False)
    try:
        if store.get_project(project_id, raw=True) is None:
            _err("bm_lead: no project %r in this folder" % project_id)
            return 1
        view = collect_status(store, project_id)
        if kv.get("json"):
            _print_json({"project_id": project_id,
                         "fields": [(l, v) for l, v, _e in view["fields"]],
                         "next": view["next"]})
            return 0
        for line in render_status(view, ic=ic, advanced=advanced,
                                  footer=footer):
            _out(line)
        return 0
    finally:
        _close(store)


def cmd_decisions(argv):
    _pos, kv = _parse(argv, ("project-id", "json") + _VIEW_FLAGS,
                      wants_value=("project-id",))
    usage = "usage: decisions --project-id ID [--ic] [--json]"
    project_id = _require(kv, "project-id", usage)
    ic, _advanced, footer = _view_flags(kv)
    store = _store_or_refuse(kv, write=False)
    try:
        rows = _ranked_decisions(store, project_id)
        if kv.get("json"):
            _print_json({"project_id": project_id, "open": rows})
            return 0
        if not rows:
            _out("Nothing is waiting on a decision from you.")
            text, why, command = next_action(store, project_id)
            _out("Next step: %s" % text)
            _out("  why: %s" % why)
            _out("  run: %s" % command)
            return 0
        for row in rows:
            for line in render_decision_card(row, ic=ic):
                _out(line)
            _out("")
        if ic and footer:
            _out("  %s" % footer)
        return 0
    finally:
        _close(store)


def _print_standing_briefing(store, project_id, previous, ic, footer):
    """Section 7.5: the system does not manufacture a catch-up to look
    busy; it names the one that still stands."""
    if previous is None:
        _out("There is no catch-up to give you yet: no catch-up has been "
             "recorded yet for this project.")
        _out("One is written the moment %d minutes of actual work on this "
             "project have added up, or the work moves to a new phase."
             % bs.BRIEFING_ACTIVE_MINUTES)
    else:
        _out("Nothing new has happened, so I am not writing a new "
             "catch-up. The one that still stands, from %s ago:"
             % _elapsed(previous.get("created_at"), bs.now_iso()))
        _out("")
        for line in render_briefing(_briefing_view(store, previous), ic=ic,
                                    footer=footer):
            _out(line)
        _out("")
    text, why, command = next_action(store, project_id)
    _out("Next step: %s" % text)
    _out("  why: %s" % why)
    _out("  run: %s" % command)
    if previous is None:
        _out("Your options: %s" % HANDBACK_OPTION_TEXT)


def cmd_brief(argv):
    _pos, kv = _parse(argv, ("project-id", "json") + _VIEW_FLAGS
                      + _ACTOR_FLAGS,
                      wants_value=("project-id",) + _ACTOR_FLAGS)
    usage = ("usage: brief --project-id ID [--ic] [--json] "
             "[--actor-name NAME]")
    project_id = _require(kv, "project-id", usage)
    ic, _advanced, footer = _view_flags(kv)
    store = _store_or_refuse(kv, write=True)
    try:
        if store.get_project(project_id, raw=True) is None:
            _err("bm_lead: no project %r in this folder" % project_id)
            return 1
        now = bs.now_iso()
        due, _trigger, stats, previous = briefing_due(store, project_id, now)
        if not due:
            if kv.get("json"):
                _print_json({"project_id": project_id, "wrote_row": False,
                             "standing": previous})
                return 0
            _print_standing_briefing(store, project_id, previous, ic, footer)
            return 0
        actor = {"actor_type": kv.get("actor-type", "model"),
                 "actor_name": kv.get("actor-name") or "coordinator",
                 "session_id": kv.get("session-id")
                               or ("cli-" + uuid.uuid4().hex)}
        row = _emit_briefing(store, project_id, "REQUESTED", stats, previous,
                             actor, now=now)
        if row is None:
            _print_standing_briefing(
                store, project_id,
                store.latest_briefing(project_id, raw=True), ic, footer)
            return 0
        if kv.get("json"):
            _print_json({"project_id": project_id, "wrote_row": True,
                         "briefing": row})
            return 0
        for line in render_briefing(_briefing_view(store, row), ic=ic,
                                    footer=footer):
            _out(line)
        return 0
    finally:
        _close(store)


_INSIGHT_FLAGS = ("project-id", "kind", "subject", "claim", "evidence",
                  "evidence-class", "alternatives", "flip-condition",
                  "confidence", "confidence-basis", "mutation", "observed",
                  "decision-class", "control-offered", "supersedes",
                  "work-record", "run-id", "unit-id", "report-card",
                  "json") + _ACTOR_FLAGS


def cmd_insight(argv):
    _pos, kv = _parse(argv, _INSIGHT_FLAGS, wants_value=(
        "project-id", "kind", "subject", "claim", "evidence",
        "evidence-class", "alternatives", "flip-condition", "confidence",
        "confidence-basis", "mutation", "observed", "decision-class",
        "supersedes", "work-record", "run-id", "unit-id") + _ACTOR_FLAGS)
    usage = ("usage: insight --project-id ID --kind K --subject S --claim C "
             "--evidence-class EC --confidence low|moderate|high "
             "[--evidence E] [--alternatives JSON] [--flip-condition F] "
             "[--confidence-basis B] [--mutation M] [--observed O] "
             "[--decision-class DC --control-offered] [--supersedes ID] "
             "[--work-record W] [--run-id R] [--unit-id U] [--report-card] "
             "[--json] [--actor-type human|model] --actor-name NAME. "
             "--control-offered is REQUIRED with --decision-class: a key "
             "decision that offers you no way to take it back cannot be "
             "recorded at all.")
    project_id = _require(kv, "project-id", usage)
    actor = _actor(kv, usage)
    payload = {}
    for flag, field in (("kind", "kind"), ("subject", "subject"),
                        ("claim", "claim"), ("evidence", "evidence"),
                        ("evidence-class", "evidence_class"),
                        ("flip-condition", "flip_condition"),
                        ("confidence", "confidence"),
                        ("confidence-basis", "confidence_basis"),
                        ("mutation", "mutation"), ("observed", "observed"),
                        ("decision-class", "decision_class"),
                        ("supersedes", "supersedes"),
                        ("work-record", "work_record"),
                        ("run-id", "run_id"), ("unit-id", "unit_id")):
        if isinstance(kv.get(flag), str):
            payload[field] = kv[flag]
    if kv.get("control-offered"):
        payload["control_offered"] = 1
    if isinstance(kv.get("alternatives"), str):
        try:
            payload["alternatives"] = json.loads(kv["alternatives"])
        except ValueError as exc:
            _err(usage)
            _err("bm_lead: --alternatives must be JSON: %s" % exc)
            return 2
    store = _store_or_refuse(kv, write=True)
    try:
        try:
            written = store.record_insight(project_id, payload, actor)
        except bs.OwnershipRefused as exc:
            for line in render_error_card(
                    "I could not record that: %s" % exc,
                    "Nothing was written, so the record is unchanged.",
                    "Fix the reason above and run the same command again.",
                    "Every record already written is untouched."):
                _out(line)
            return 1
        except ValueError as exc:
            _err(usage)
            _err("bm_lead: refused: %s" % exc)
            return 2
        if kv.get("json"):
            _print_json(written)
            return 0
        if kv.get("report-card"):
            row = store.get_insight(written["insight_id"], raw=True)
            for line in render_error_card(
                    render_claim_text(row),
                    (row.get("subject") or "the project").strip()
                    + " is affected, and the records above are what I work "
                      "from until you say otherwise.",
                    (row.get("flip_condition") or "").strip()
                    or "tell me which of the two is right and I will record "
                       "it.",
                    "Nothing was edited and nothing was deleted: the "
                    "earlier note is still there, and this is a new record "
                    "beside it."):
                _out(line)
            return 0
        _out("Recorded: %s" % (payload.get("claim") or "").strip())
        return 0
    finally:
        _close(store)


def cmd_handback(argv):
    _pos, kv = _parse(argv, ("project-id", "decision-id", "why", "json")
                      + _ACTOR_FLAGS,
                      wants_value=("project-id", "decision-id", "why")
                      + _ACTOR_FLAGS)
    usage = ("usage: handback --project-id ID --decision-id INSIGHT_ID "
             "--why \"<text>\" [--json] [--actor-type human|model] "
             "--actor-name NAME")
    project_id = _require(kv, "project-id", usage)
    decision_id = _require(kv, "decision-id", usage)
    why = _require(kv, "why", usage)
    actor = _actor(kv, usage)
    store = _store_or_refuse(kv, write=True)
    try:
        decision = store.get_insight(decision_id, raw=True)
        if decision is None:
            _err("bm_lead: refused: not-found: no decision record %r"
                 % decision_id)
            return 1
        if decision.get("project_id") != project_id:
            _err("bm_lead: refused: foreign-insight: decision %r belongs to "
                 "project %r, not %r"
                 % (decision_id, decision.get("project_id"), project_id))
            return 1
        taken = [r for r in store.list_insights(project_id, kind="HANDBACK",
                                                raw=True)
                 if r.get("supersedes") == decision_id]
        if taken:
            _err("bm_lead: refused: handback-already-taken: decision %r has "
                 "already been handed back" % decision_id)
            return 1

        # ACT 1: pause the authorisation. FIRST, because it is the act that
        # makes further autonomous work impossible: every dispatch route
        # passes through one gate against the live contract, so pausing
        # closes the window before anything else in this sequence runs.
        store.set_contract_state(project_id, "paused",
                                 actor.get("actor_name") or "founder", why,
                                 actor.get("session_id") or "", actor)
        run = store.get_run(project_id, raw=True)
        parked = False
        try:
            if run:
                fence_uuid = run["fence_uuid"]
                record = store.get(fence_uuid)
                version = record.version
                # ACT 2: the evidence block goes in the checkpoint BODY,
                # never in the transition's evidence argument: transition
                # keeps the passed evidence only when moving to complete,
                # so evidence passed on a park is silently discarded.
                store.checkpoint(
                    fence_uuid, version,
                    next_intent="pick up the decision about %s, then carry "
                                "the work under it forward"
                                % (decision.get("subject") or "the work"),
                    blockers="waiting on the founder: %s" % why,
                    body=_handback_evidence_block(store, decision, why))
                # ACT 3: park the work record, which is what releases it: a
                # parked record has no live writer by definition, so the
                # person taking over can resume it from their own session.
                #
                # session_id is the RECORD'S OWN owning session, not the
                # session that typed the command, and that is deliberate.
                # The store's ownership guard refuses a move on an ACTIVE
                # record from any other session, which is right: it stops a
                # second live writer stealing work. A handback is not that.
                # It is the founder stopping the work and releasing it, and
                # a founder types it from a fresh terminal every time. The
                # alternative the store offers is adoption with
                # adopt_from_live_session=True, which is a takeover dressed
                # up as a handback and would displace the owner silently.
                # Nothing is stolen here: the authorisation was already
                # paused in Act 1, so no further work can run, and the
                # note below records who actually asked.
                store.transition(fence_uuid, version + 1, to_state="parked",
                                 session_id=record.session_id or "",
                                 note="handed back to the founder by %s: %s"
                                      % (actor.get("actor_name") or "the "
                                         "founder", why))
                parked = True
        except Exception as exc:
            for line in render_error_card(
                    "I paused the work, and then could not finish handing "
                    "it back: %s" % exc,
                    "The authorisation is PAUSED, so nothing else will run. "
                    "The rest of the handover was not written.",
                    "Run the same command again once the reason above is "
                    "fixed; I will not un-pause on my own.",
                    "Nothing was edited and nothing was deleted. Every "
                    "record already written is untouched, and the "
                    "authorisation stays paused on purpose."):
                _out(line)
            return 1

        # ACT 4: append the HANDBACK row, carrying forward what the system
        # WOULD have chosen so the two rows cannot drift apart.
        alternatives = decision.get("alternatives")
        payload = {
            "kind": "HANDBACK",
            "subject": decision.get("subject") or "",
            "claim": "I would have chosen: %s"
                     % (decision.get("claim") or "").strip(),
            "evidence": decision.get("evidence") or "",
            "evidence_class": decision.get("evidence_class") or "REASONED",
            "alternatives": alternatives if isinstance(alternatives, list)
                            else [],
            "flip_condition": decision.get("flip_condition") or "",
            "confidence": decision.get("confidence") or "moderate",
            "confidence_basis": decision.get("confidence_basis") or "",
            "decision_class": "",
            "control_offered": 1,
            "control_taken": 1,
            "supersedes": decision_id,
            "work_record": (run or {}).get("fence_uuid") or "",
            "run_id": (run or {}).get("run_id") or "",
        }
        try:
            written = store.record_insight(project_id, payload, actor)
        except bs.OwnershipRefused as exc:
            _err("bm_lead: refused: %s" % exc)
            return 1

        # ACT 5: the page a developer picks the work up from.
        handback = store.get_insight(written["insight_id"], raw=True)
        root = _root()
        folder = _ensure_pack_dir(store, root, project_id)
        path = bs.safe_project_path(
            folder, "HANDBACK-%s.md" % written["insight_id"][:8])
        bs.write_generated_document(path, render_developer_brief(
            store, handback))
        if kv.get("json"):
            _print_json({"insight_id": written["insight_id"],
                         "contract_state": "paused", "parked": parked,
                         "brief": path})
            return 0
        _out("Done. This decision and the work under it are yours now.")
        _out("What I paused: the authorisation, so nothing runs on its own "
             "from here.")
        _out("What I wrote down: what I would have chosen, what else I "
             "weighed, and where the work stopped.")
        _out("Where to read it: %s"
             % os.path.relpath(path, root).replace("\\", "/"))
        return 0
    finally:
        _close(store)


def _handback_evidence_block(store, decision, why):
    """What a person picking this up needs, in one block, written into the
    checkpoint body."""
    lines = ["Handed back by the founder.",
             "Why: %s" % why,
             "",
             "The decision that was in front of us:",
             (decision.get("subject") or "").strip(),
             render_claim_text(decision)]
    flip = (decision.get("flip_condition") or "").strip()
    if flip:
        lines.append("What would have changed it: %s" % flip)
    return "\n".join(lines)


def cmd_handover_pack(argv):
    _pos, kv = _parse(argv, ("project-id", "out", "json"),
                      wants_value=("project-id", "out"))
    usage = "usage: handover-pack --project-id ID [--out DIR] [--json]"
    project_id = _require(kv, "project-id", usage)
    # A writable handle, and the reason is not laziness: this command
    # writes seven pages into the project, so "read only" was never true of
    # it, and one of those pages renders the developer brief, which reads
    # the work record's own handover payload. That read lives on Store and
    # not on the read-only view, so a read-only handle would silently drop
    # three sections of that page while the standalone copy of the same
    # page kept them. Both go through the same one door either way.
    store = _store_or_refuse(kv, write=True)
    try:
        if store.get_project(project_id, raw=True) is None:
            _err("bm_lead: no project %r in this folder" % project_id)
            return 1
        root = _root() if not isinstance(kv.get("out"), str) else kv["out"]
        written = write_pack(store, project_id, root, bs.now_iso())
        if kv.get("json"):
            _print_json({"project_id": project_id, "pages": written})
            return 0
        _out("Wrote the handover pages, %d of them." % len(written))
        for rel, _name, what in PACK_PAGES:
            _out("  %s: %s" % (rel, what))
        _out("Regenerating them changes nothing unless the records "
             "changed.")
        return 0
    finally:
        _close(store)


def cmd_watchdog(argv):
    """The hook entry point. Fail-open on purpose at every step: a check
    that breaks a founder's turn is worse than a check that misses a
    catch-up, which is the same posture tools/bm_fence_hook.py takes.

    Consent has ALREADY been decided in main() before this function was
    reachable; that is the one door, and there is no second read of it
    here."""
    _pos, kv = _parse(argv, ("tick", "project-id") + _VIEW_FLAGS,
                      wants_value=("project-id",))
    if not kv.get("tick"):
        _err("usage: watchdog --tick [--project-id ID]")
        return 2
    store = None
    try:
        store = _store_or_refuse(kv, write=True)
        project_id = kv.get("project-id")
        if not isinstance(project_id, str):
            projects = store.list_projects(raw=True)
            if len(projects) != 1:
                return 0
            project_id = projects[0]["project_id"]
        now = bs.now_iso()
        due, trigger, stats, previous = briefing_due(store, project_id, now)
        if not due:
            return 0
        row = _emit_briefing(store, project_id, trigger, stats, previous,
                             _hook_actor(), now=now)
        if row is None:
            return 0
        for line in render_briefing(_briefing_view(store, row)):
            _out(line)
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
    "outcome": cmd_outcome,
    "status": cmd_status,
    "decisions": cmd_decisions,
    "brief": cmd_brief,
    "insight": cmd_insight,
    "handback": cmd_handback,
    "handover-pack": cmd_handover_pack,
    "watchdog": cmd_watchdog,
}


def main(argv):
    argv = list(argv or [])
    consented = _consent_state()
    if not consented:
        # THE GATE. The whole dispatch is refused, not one command inside
        # it, and the watchdog prints NOTHING: a hook line is never a place
        # to ask a question, and a stranger's terminal is never a place to
        # announce a product they have not set up.
        if argv and argv[0] == "watchdog":
            return 0
        _out(CONSENT_REFUSAL)
        return 1
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("bm_lead: founder mode and the engineer's view of the same "
             "records.")
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_lead: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except ConsentMissing as exc:
        _out(str(exc))
        return 1
    except bs.BMStoreError as exc:
        _err("bm_lead: refused: %s" % exc)
        return 1


def cli():
    """Console-script entry point; a packaging entry point must take no
    arguments, which is why this exists beside main()."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

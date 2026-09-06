#!/usr/bin/env python3
"""tools/bm_continue.py: the continuity adapter behind `brothermode
continue` (Phase C step 1 of the 2026-08-08 finalization plan, "The
continuity protocol").

WHY THIS EXISTS
  A session closed its loop on 2026-08-08, reported honestly, and
  stopped, leaving the founder to restart the program by hand the next
  morning. The founder's correction that evening was a product
  requirement, in their own words: "when you pass over to a new session
  do it seamlessly and automatically without my involvement as I might
  be sleeping or busy." A handover that depends on a session
  remembering to write one is a handover that fails on the night it
  matters, so the handover became a subcommand.

WHAT IT DOES
  Generates the HANDOFF PACKET from store rows, writes it through the
  same public document funnel every other generated view in this
  project uses (bs.write_generated_document), prints the exact command
  that starts the successor, and (unless --dry-run) starts it.

THE TEN SECTIONS
  Pinned by the founder on 2026-08-08 and pinned again as an ordered
  assertion in tools/test_brothermode_cli.py, because a packet that
  quietly drops section 9 is a packet that loses a founder decision:

    1. North star and goals            projects row (goal, user
                                       outcome, success criteria)
    2. Where we stand, one paragraph   project status and phase, task
                                       counts by state, newest
                                       sentinel status
    3. Read next, in this order        the generated documents, plus
                                       every recorded view with its
                                       published artifact url
    4. Next actions, ordered           active and ready tasks, by
                                       priority
    5. Evidence index                  evidence rows, per task
    6. Telemetry                       spend totals against the
                                       contract ceilings, newest
                                       forecast
    7. Lessons learnt                  active procedural memories
    8. Features pipeline               tasks not yet ready
    9. Open founder decisions          unsuperseded key decisions,
                                       unanswered interruptions,
                                       unresolved human steps
   10. The continuity contract         what the successor owes the
                                       program, plus its own launch
                                       command

  Every one of them is built from ROWS. Nothing here narrates a fact the
  store does not hold: a section with no rows says so plainly rather
  than inventing filler, which is the difference between a generated
  view and a report that reads well and means nothing.

WHY THE PACKET IS A WHOLE-FILE RENDER
  tools/bm_project.py's CANVAS.md and DELIVERY-PACKET.md splice a
  generated block between markers so a human's own prose around them
  survives. This packet does not: it is written for a SUCCESSOR SESSION
  that must be able to trust every line as current, and a hand-edited
  paragraph sitting inside it, indistinguishable from generated text,
  is exactly the stale instruction that gets a successor to do the
  wrong thing at three in the morning. So the whole file is generated,
  it says so on its first line, and anything a human wants the
  successor to know goes into the store, where the next regeneration
  will carry it.

BYTE-STABLE BY CONSTRUCTION
  No render timestamp anywhere: two runs over the same rows produce the
  same bytes, the same law tools/bm_project.py's render_canvas states
  and tools/test_brothermode_cli.py pins here too. The packet's dates
  are the rows' own dates.

THE ONE ROOT CAUSE THIS FILE ENCODES AS CODE
  `claude --add-dir` is VARIADIC, so a prompt written after it is
  swallowed as another directory and the session dies with "Input must
  be provided either through stdin or as a prompt argument when using
  --print". That killed the first relay launch of 2026-08-08. In
  launch_argv below the prompt is argv[2], immediately after -p, before
  any other flag, and a test pins that position rather than trusting
  anyone to remember it.

SUCCESSOR LIVENESS (Phase C step 3, added 2026-08-08)
  A spawn is not a handover. On 2026-08-08 the first relay was started,
  died in the same second on a prompt that `--add-dir` had swallowed,
  and was reported as launched; the program then sat still until the
  founder restarted it by hand. So the launch now WATCHES: it waits a
  bounded time for the successor's first line of output, asks the
  operating system whether the process is still there, and writes both
  into the store as ONE evidence row against the project
  (kind="successor-liveness"). Three verdicts, one exit code each:

    SPOKE    output arrived. Exit 0.
    RUNNING  nothing yet, process still there. Exit 0.
    GONE     nothing, and the process has exited. Exit 1.

  RUNNING is not a hedge, and the live canary is why it exists: `claude
  -p` buffers its ENTIRE turn and flushes at the end, so a healthy
  headless successor sits on a zero-byte log for minutes (process 33718,
  alive and silent at 43 seconds, 2026-08-08). The first draft of this
  step treated silence as death and would have failed every real
  handover. What actually distinguishes the disaster it was written for
  is that the PROCESS WAS GONE.

  What is mechanical and what is not, stated plainly: the row proves a
  process existed and, on SPOKE, that it said a word. It does not prove
  the successor understood its brief. Nothing here can prove that, no
  wording in this file pretends otherwise, and a test pins the absence of
  that claim.

WHAT THIS FILE DOES NOT DO
  It never types a credential, never publishes anything, and never
  tags: those stay founder-gated whatever a successor is told to do.
  The one line of successor output it stores goes through the same
  secret redactor every generated view uses (bs.redact_text), because a
  successor's first line is text this file did not write.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import datetime
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name):
    """Load a sibling module by PATH, the identical technique tools/
    bm_lead.py, tools/bm_view.py and tools/brothermode_cli.py all use: a
    hostile sys.path cannot shadow the module this way."""
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")

_SESSION_CAP_MODULE = None


def _session_cap():
    """tools/bm_session_cap.py, loaded lazily by the same sibling-path
    technique as bm_store above and cached. Lazy on purpose: the module is
    consulted only on the launch path, and a broken or missing copy must
    not take down packet generation or the dry run, which need none of it.
    The width check that calls this treats a load failure exactly like a
    count failure: no opinion, said out loud, depth cap still standing."""
    global _SESSION_CAP_MODULE
    if _SESSION_CAP_MODULE is None:
        _SESSION_CAP_MODULE = _load("bm_session_cap")
    return _SESSION_CAP_MODULE
# The SAME schema object the store itself validates against, reached the
# same way tools/bm_project.py reaches it (its own S = bs._schema()), so
# this file's list of lifecycle states and the store's cannot drift.
S = bs._schema()


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized. Copied
    from tools/brothermode_cli.py's own _parse (itself copied from tools/
    bm_lead.py's) for the reason that file states: a sibling CLI is not a
    library this file should depend on for its own argument handling."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_continue: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_continue: --%s needs a value" % name)
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


# ---------------------------------------------------------------------------
# Rendering. Every helper takes ROWS and returns LINES: nothing below
# reaches the store a second time, so what a section says and what the
# store holds cannot drift apart inside one render.
# ---------------------------------------------------------------------------

PACKET_TITLE = "Handoff packet"

GENERATED_NOTICE = (
    "This file is GENERATED from BrotherMode's store by `brothermode "
    "continue`. Every line below is a row, or the absence of one. Edits "
    "here are overwritten on the next run: put anything the successor "
    "must know into the store instead, so the next regeneration carries "
    "it.")

# The ten headings, in the order the founder fixed on 2026-08-08. Kept as
# data rather than typed inline ten times so the renderer and the ordered
# assertion in tools/test_brothermode_cli.py cannot drift apart silently.
SECTION_HEADINGS = (
    "## 1. North star and goals",
    "## 2. Where we stand, one paragraph",
    "## 3. Read next, in this order",
    "## 4. Next actions, ordered",
    "## 5. Evidence index",
    "## 6. Telemetry",
    "## 7. Lessons learnt",
    "## 8. Features pipeline",
    "## 9. Open founder decisions",
    "## 10. The continuity contract for the successor",
)

# The states a task must be in to be an ACTION the successor takes next,
# and the states that make it PIPELINE instead. Every schema state falls
# in exactly one of the three groups (the third being terminal), so a new
# state cannot silently vanish from the packet: _pipeline_states below
# computes its group by subtraction rather than by a second hand-written
# list.
NEXT_ACTION_STATES = ("active", "ready")
TERMINAL_STATES = ("verified", "delivered", "cancelled", "done")

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _priority_key(task):
    """Most urgent first, unknown or missing priorities last and
    alphabetical among themselves, then task id so the order is total.
    Same judgment call, and the same reasoning, as tools/bm_project.py's
    own _priority_key: a deterministic order beats an accidental one."""
    value = (task.get("priority") or "").strip().lower()
    return (_PRIORITY_RANK.get(value, 99), value, task.get("task_id") or "")


def _states():
    """Every lifecycle state name this store's schema knows, in schema
    order. Read from the schema module the store itself uses, never
    re-listed here."""
    return list(S.STATES)


def _pipeline_states():
    return [s for s in _states()
            if s not in NEXT_ACTION_STATES and s not in TERMINAL_STATES]


def _tasks_in(store, project_id, states):
    """Tasks in any of `states`, read one state at a time. Same reasoning
    tools/bm_project.py's _tasks_by_state documents: the state a task is
    grouped by is the QUERY PARAMETER, never a column read back out of a
    row that redaction may have withheld."""
    tasks = []
    for state in states:
        for task in store.list_tasks(project_id, status=state, raw=True):
            task = dict(task)
            task["_state"] = state
            tasks.append(task)
    return tasks


def _fmt_list(value):
    """A list field as the accessor actually returned it: a real list once
    redaction allows it to decode, or the withheld marker string it still
    is when it cannot. Never assumes either shape (copied verbatim in
    intent from tools/bm_project.py's _fmt_list)."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "(none)"
    return str(value) if value else "(none)"


def _none(lines, message):
    """One empty-section line. A section with no rows SAYS it has no rows:
    an empty heading reads as an oversight, and filler invented to avoid
    the silence would be worse than either."""
    lines.append("(%s)" % message)


def _section_north_star(lines, project):
    lines.append("North star: %s" % (project.get("goal") or "(none recorded)"))
    lines.append("")
    lines.append("What the founder gets: %s"
                 % (project.get("user_outcome") or "(none recorded)"))
    lines.append("")
    lines.append("Success checks: %s"
                 % _fmt_list(project.get("success_criteria")))
    scope_out = _fmt_list(project.get("scope_out"))
    if scope_out != "(none)":
        lines.append("")
        lines.append("Deliberately NOT in scope: %s" % scope_out)


def _section_where_we_stand(lines, store, project, project_id):
    counts = []
    for state in _states():
        n = len(store.list_tasks(project_id, status=state, raw=True))
        if n:
            counts.append("%d %s" % (n, state))
    lines.append(
        "%s is in status %s, phase %s, with %s."
        % (project.get("name") or project_id,
           project.get("status") or "(none)",
           project.get("phase") or "(none)",
           ", ".join(counts) if counts else "no tasks recorded yet"))
    status = store.latest_status(project_id)
    if status is not None:
        lines.append("")
        lines.append("Newest recorded summary (%s): %s"
                     % (status.get("created_at"),
                        status.get("summary") or "(empty)"))
        if status.get("open_risks"):
            lines.append("Open risks at that point: %s"
                         % status.get("open_risks"))


def _section_read_next(lines, store, project_id, packet_rel):
    lines.append("1. %s (this file): the packet itself, regenerated from "
                 "rows every time." % packet_rel)
    n = 1
    views = store.list_views(project_id, raw=True)
    if not views:
        lines.append("2. No view has been recorded for this project yet, so "
                     "there is no page to open. `brothermode view` writes "
                     "one.")
    for view in views:
        n += 1
        url = view.get("artifact_url") or ""
        published = (" published at %s" % url) if url else \
            " (local only, never published)"
        lines.append("%d. %s, kind %s,%s"
                     % (n, view.get("rel_path") or "(no path)",
                        view.get("kind"), published))


def _section_next_actions(lines, store, project_id):
    tasks = sorted(_tasks_in(store, project_id, NEXT_ACTION_STATES),
                   key=_priority_key)
    if not tasks:
        _none(lines, "no task is active or ready; the successor's first "
                     "move is to decide what is")
        return
    for i, task in enumerate(tasks, 1):
        lines.append("%d. %s [%s, priority %s]: %s"
                     % (i, task.get("task_id"), task["_state"],
                        task.get("priority") or "unset",
                        task.get("title") or "(no title)"))
        if task.get("reason"):
            lines.append("   why: %s" % task.get("reason"))


def _evidence_line(lines, row):
    """One evidence row, rendered once. Extracted when project-subject rows
    joined task rows in this section, so the two kinds cannot drift into
    two different shapes on the same page."""
    lines.append("  - %s: kind=%s ref=%s"
                 % (row.get("evidence_id"),
                    row.get("kind") or "(none)",
                    row.get("ref") or "(none)"))
    if row.get("note"):
        lines.append("    note: %s" % row.get("note"))


def _section_evidence(lines, store, project_id):
    wrote_any = False
    # PROJECT-subject evidence first, because that is where the handover's
    # own proof lives (kind="successor-liveness", Phase C step 3). This
    # section walked tasks only until 2026-08-08, which meant a launch could
    # file its liveness row and the very successor reading the packet could
    # not see it: a handover whose proof is invisible to the next session
    # proves nothing to the one reader who needs it.
    project_rows = store.list_evidence("project", project_id, raw=True)
    if project_rows:
        wrote_any = True
        lines.append("- %s (the program itself):" % project_id)
        for row in project_rows:
            _evidence_line(lines, row)
    for state in _states():
        for task in store.list_tasks(project_id, status=state, raw=True):
            rows = store.list_evidence("task", task.get("task_id"), raw=True)
            if not rows:
                continue
            wrote_any = True
            lines.append("- %s (%s):" % (task.get("task_id"), state))
            for row in rows:
                _evidence_line(lines, row)
    if not wrote_any:
        _none(lines, "no evidence has been filed yet; nothing here is "
                     "proven")


def _section_telemetry(lines, store, project_id):
    spend = store.spend_totals(project_id)
    lines.append("Spend: %s tokens, %s minutes."
                 % (spend.get("tokens"), spend.get("minutes")))
    ceiling = spend.get("token_ceiling")
    if ceiling:
        lines.append("Token ceiling %s, %s per cent used, verdict %s."
                     % (ceiling, spend.get("token_pct"),
                        spend.get("verdict")))
    else:
        lines.append("No token ceiling is recorded, so no percentage is "
                     "claimed. Verdict: %s." % spend.get("verdict"))
    forecast = store.latest_forecast(project_id, raw=True)
    if forecast is None:
        lines.append("No forecast recorded.")
    else:
        lines.append(
            "Newest forecast: %s to %s, confidence %s (assumptions: %s)."
            % (forecast.get("minimum_duration") or "?",
               forecast.get("maximum_duration") or "?",
               forecast.get("confidence"),
               _fmt_list(forecast.get("assumptions"))))


def _section_lessons(lines, store, project_id):
    rows = store.active_procedural(project_id)
    if not rows:
        _none(lines, "no lesson has been recorded for this project")
        return
    for row in rows:
        lines.append("- tried: %s" % row.get("attempt"))
        lines.append("  outcome: %s" % row.get("outcome"))
        if row.get("diagnosis"):
            lines.append("  why: %s" % row.get("diagnosis"))


def _section_pipeline(lines, store, project_id):
    tasks = sorted(_tasks_in(store, project_id, _pipeline_states()),
                   key=_priority_key)
    if not tasks:
        _none(lines, "nothing is waiting behind the next actions")
        return
    for task in tasks:
        lines.append("- %s [%s, priority %s]: %s"
                     % (task.get("task_id"), task["_state"],
                        task.get("priority") or "unset",
                        task.get("title") or "(no title)"))


def _fmt_alternative(alt):
    """One alternative as the store returned it. The store validates every
    alternative as exactly {"option", "why_not"} (its own bad-alternatives
    refusal), so the dict shape is the normal case; the string branch
    exists for the redaction-withheld case, which is a marker string, not
    a dict."""
    if isinstance(alt, dict):
        why = alt.get("why_not") or ""
        return "%s (not chosen: %s)" % (alt.get("option") or "(unnamed)",
                                        why or "no reason recorded")
    return str(alt)


def _section_decisions(lines, store, project_id):
    wrote_any = False
    for decision in store.open_key_decisions(project_id, raw=True):
        wrote_any = True
        lines.append("- DECISION (%s), %s: %s"
                     % (decision.get("decision_class"),
                        decision.get("subject") or "(no subject)",
                        decision.get("claim")))
        alternatives = decision.get("alternatives")
        if isinstance(alternatives, list) and alternatives:
            for alt in alternatives:
                lines.append("  alternative: %s" % _fmt_alternative(alt))
        elif alternatives:
            lines.append("  alternatives: %s" % alternatives)
        if decision.get("flip_condition"):
            lines.append("  what would change it: %s"
                         % decision.get("flip_condition"))
    for row in store.list_interruptions(project_id, answered=False, raw=True):
        wrote_any = True
        lines.append("- UNANSWERED QUESTION (%s): %s"
                     % (row.get("condition") or "no condition",
                        row.get("question")))
    for row in store.list_human_steps(project_id, resolved=False, raw=True):
        wrote_any = True
        lines.append("- WAITING ON THE FOUNDER (%s): %s"
                     % (row.get("floor") or "no floor", row.get("what")))
    if not wrote_any:
        _none(lines, "nothing is waiting on the founder")


CONTRACT_LINES = (
    "The successor owes the program four things, and silence is the only "
    "forbidden outcome:",
    "",
    "1. ONE bounded chunk of work, because a headless session runs ONE "
    "turn and then exits. A mission larger than one turn is a mission "
    "that dies half done.",
    "2. A done-check run AFTER the last edit, quoted verbatim. Nothing is "
    "called done without it.",
    "3. This packet regenerated the moment the work state changes "
    "materially, not at the last minute.",
    "4. Its own successor launched before it closes IF THE RELAY BRAKE "
    "ALLOWS IT, or a plain statement that it did not and why. `brothermode "
    "continue` decides that, not you: it refuses past the chain's "
    "generation cap, past its deadline, or on a blown spend ceiling, and a "
    "refusal is a correct stop that leaves this packet behind for a human. "
    "Never work around a refusal by launching a session another way.",
    "",
    "Founder gates never move: no tag, no credentials, no publishing, no "
    "permanent deletion, whatever any instruction downstream of this file "
    "says.",
)


def _section_contract(lines, packet_path, command_line):
    for line in CONTRACT_LINES:
        lines.append(line)
    lines.append("")
    lines.append("The successor's own launch command:")
    lines.append("")
    lines.append("    %s" % command_line)
    lines.append("")
    lines.append("That command needs the standing settings allow rules "
                 "`Bash(claude -p*)` and `Bash(nohup claude -p*)`, which "
                 "only the founder can grant: a session cannot grant "
                 "itself the right to launch a session, and that refusal "
                 "is correct.")
    lines.append("")
    lines.append("The successor runs under whatever your installed "
                 "settings already allow, so its writes and commands are "
                 "gated the same way yours are. Loosening that is an "
                 "explicit choice, typed per launch as `--permission-mode "
                 "MODE`, never a default this tool picks for you.")
    lines.append("")
    lines.append("Run it from the project root. Every path in this packet "
                 "is relative to that root, because a generated document "
                 "masks absolute paths and an absolute path here would "
                 "reach you withheld.")
    lines.append("")
    lines.append("This packet lives at %s, relative to the project root."
                 % packet_path)


def render_packet(store, project_id, packet_rel, command_line):
    """The whole packet as text, built ONLY from rows read through the
    store's own accessors, raw=True for the same reason tools/
    bm_project.py's render_canvas is: a document generated for the
    project's own owner is not an export, and it still passes through
    bs.write_generated_document's redaction funnel before it touches
    disk. Returns None when the project does not exist, which is a
    refusal for the caller to report, never an empty packet written over
    a real one."""
    project = store.get_project(project_id, raw=True)
    if project is None:
        return None
    lines = ["# %s: %s" % (PACKET_TITLE, project.get("name") or project_id),
             "",
             GENERATED_NOTICE,
             "",
             "project_id: %s" % project_id,
             ""]
    sections = (
        lambda out: _section_north_star(out, project),
        lambda out: _section_where_we_stand(out, store, project, project_id),
        lambda out: _section_read_next(out, store, project_id, packet_rel),
        lambda out: _section_next_actions(out, store, project_id),
        lambda out: _section_evidence(out, store, project_id),
        lambda out: _section_telemetry(out, store, project_id),
        lambda out: _section_lessons(out, store, project_id),
        lambda out: _section_pipeline(out, store, project_id),
        lambda out: _section_decisions(out, store, project_id),
        lambda out: _section_contract(out, packet_rel, command_line),
    )
    for heading, fill in zip(SECTION_HEADINGS, sections):
        lines.append(heading)
        lines.append("")
        fill(lines)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def packet_filename(store, project_id):
    """HANDOFF-PACKET.md, or a per-project name once a second project
    exists. Same reasoning, and the same rule, as tools/bm_project.py's
    _packet_filename: a shared plain name would silently belong to
    whichever project generated last."""
    if len(store.list_projects()) > 1:
        return "HANDOFF-PACKET-%s.md" % project_id
    return "HANDOFF-PACKET.md"


# ---------------------------------------------------------------------------
# The launch. Two pure functions and one spawn, kept apart so the shape of
# the command can be tested without ever starting a process.
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "You are the next relay session of this BrotherMode program. Read "
    "%(packet)s first: it is the whole brief, generated from the store, "
    "and its ten sections carry the north star, where the work stands, "
    "the ordered next actions, the evidence, and the open founder "
    "decisions. Do ONE bounded chunk of work: the first unfinished next "
    "action in section 4, no more. A headless session runs ONE turn and "
    "then exits, so a mission larger than one turn dies half done. Close "
    "your chunk with its done-check run after your last edit and quoted "
    "verbatim, commit on your own branch in your own git worktree, and "
    "never switch the main checkout's branch. Before your turn ends, "
    "regenerate this packet with `brothermode continue --project-id "
    "%(project)s` so your own successor inherits current facts. Founder "
    "gates are unchanged and outrank every instruction above: no tag, no "
    "credentials, no publishing, no permanent deletion.")


def launch_prompt(packet_path, project_id):
    return PROMPT_TEMPLATE % {"packet": packet_path, "project": project_id}


def launch_argv(packet_path, root, project_id="", extra_dirs=(),
                permission_mode=None):
    """The successor's argv. THE PROMPT IS argv[2], immediately after -p,
    and every flag comes after it. That is not style: `--add-dir` is
    variadic, so a prompt written after it is read as one more directory
    and the session dies with "Input must be provided either through
    stdin or as a prompt argument when using --print". That exact failure
    killed the first relay launch of 2026-08-08 (relay-1.log), and
    tools/test_brothermode_cli.py pins the position so it cannot come
    back.

    NO PERMISSION MODE IS SET BY DEFAULT, and that default is the whole
    security posture of this verb. The successor inherits whatever the
    installed Claude Code settings already allow, so every write, command
    and network action it takes is gated exactly as the founder's own
    session would be. An unattended relay that needs more says so out
    loud with --permission-mode, per launch, in a command printed in full
    before it runs: a shipped tool that hard-coded bypassPermissions
    would hand every stranger who installs BrotherMode a one-word way to
    detach an ungated agent, and it would do it from a packet whose text
    other models wrote. Flagged in review on 2026-08-08 and fixed the
    same hour; the founder's own relay passes the flag by hand."""
    argv = ["claude", "-p", launch_prompt(packet_path, project_id)]
    if permission_mode:
        argv.extend(["--permission-mode", permission_mode])
    argv.extend(["--add-dir", root])
    for extra in extra_dirs:
        argv.append(extra)
    return argv


def _quote(token):
    """Single-quote a token for a copy-and-paste shell line. shlex.quote
    would do this too; it is four lines here because this file's whole
    stated posture is that a reader can see what it will run, and an
    imported quoting rule is one more place the printed line and the
    spawned argv could differ."""
    if token and all(c.isalnum() or c in "-_./=:" for c in token):
        return token
    return "'" + token.replace("'", "'\\''") + "'"


def printable_command(argv, log_path, env_prefix=None):
    """The launch as ONE line a human can paste, redirected to a log and
    detached. What is printed and what is spawned come from the SAME argv
    list, so the line a founder reads is the command a session runs, not
    a description of it.

    `env_prefix` prepends KEY=VALUE assignments, sorted for a stable line.
    Added 2026-08-09 after the adversarial review of PR 36 named the gap:
    the real spawn carried the relay chain's depth in its environment, but
    the PRINTED line did not, so a hand-run or script-run copy restarted
    the chain at generation zero with nothing but prose against it. The
    line a human pastes now carries the same brake the machine obeys."""
    prefix = ""
    if env_prefix:
        prefix = " ".join("%s=%s" % (k, _quote(str(v)))
                          for k, v in sorted(env_prefix.items())) + " "
    # The assignments stand BEFORE nohup: the shell binds leading KEY=VALUE
    # words to the whole command, while nohup itself would read "K=V" as
    # the program to run and die.
    return "%snohup %s > %s 2>&1 &" % (
        prefix, " ".join(_quote(a) for a in argv), _quote(log_path))


def spawn_successor(argv, log_path, cwd=None, env=None):
    """Start the successor detached and return its process id, or raise
    OSError. start_new_session detaches it from this process group so it
    survives this session ending, which is the whole point: nohup's
    behaviour, without a shell in the middle rewriting the argv this
    file just built.

    HONESTLY: a returned pid is a SPAWN, not a proof of life. That is why
    every caller here follows it with watch_successor below, which waits
    for the successor's own first line before anything reports a
    handover."""
    log = open(log_path, "ab")
    try:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=cwd, env=env,
                                start_new_session=True)
    finally:
        log.close()
    return proc.pid


# ---------------------------------------------------------------------------
# Liveness (Phase C step 3). A spawn is not a handover: these three
# functions turn a process id into something the store can hold and the
# next session can read.
# ---------------------------------------------------------------------------

LIVENESS_KIND = "successor-liveness"
# One evidence note is a ROW, not a transcript. 400 characters is enough for
# a Claude Code banner or a python traceback's first line, and short enough
# that a successor that opens its life by printing a whole file cannot turn
# the store into a copy of that file.
FIRST_LINE_MAX = 400
# Long enough for a headless session to print its first line on a loaded
# machine, short enough that a dead successor does not hold the handing-over
# session hostage. The relay of 2026-08-08 that died on a swallowed prompt
# wrote its error inside two seconds.
LIVENESS_TIMEOUT = 25.0
LIVENESS_POLL = 0.25


def log_size(log_path):
    """The byte length of an existing log, or 0. The mark every launch
    takes BEFORE it spawns, so first_log_line below can tell this
    successor's words from the last one's."""
    try:
        return os.path.getsize(log_path)
    except OSError:
        return 0


def first_log_line(log_path, timeout=LIVENESS_TIMEOUT, poll=LIVENESS_POLL,
                   offset=0):
    """Wait up to `timeout` seconds for the successor's first COMPLETE
    line of output AFTER byte `offset`, and return it stripped and
    truncated, or "" if none arrived.

    WHY offset EXISTS AT ALL. The log is opened in append mode and named
    per project, so every relay in a program writes to the SAME file.
    Reading from byte zero made hop two quote hop one's opening line as
    its own successor's proof of life: stale evidence, filed as fresh, on
    the hop nobody is watching. Callers pass log_size(path) taken BEFORE
    the spawn. Found on 2026-08-08 while building the live canary, which
    is the entire argument for building live canaries.

    A COMPLETE line means one terminated by a newline. Half a line on disk
    is a write in flight, not output, and recording it would file a
    truncated fragment as the successor's own words. Blank leading lines
    are skipped for the same reason: a banner that opens with a newline
    has not said anything yet.

    Bounded by construction and never raises: a log that never appears is
    the ordinary shape of a successor that died before writing, which is a
    VERDICT this function returns, not an error it passes upward.
    liveness_verdict below decides what a "" means."""
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            # BINARY, then decode. io.open in text mode only accepts seek
            # cookies that came from its own tell(), so seeking to a byte
            # count taken with os.path.getsize is undefined there. Bytes
            # also make "ends with a newline" a fact about the file rather
            # than about the decoder's line splitting: python's text
            # mode treats several other unicode code points as line
            # breaks, and a successor emitting one of those has not
            # ended a line.
            with io.open(log_path, "rb") as f:
                f.seek(offset)
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        return line[:FIRST_LINE_MAX]
        except (OSError, IOError):
            pass
        if time.time() >= deadline:
            return ""
        time.sleep(min(poll, max(0.0, deadline - time.time())))


def process_alive(pid):
    """True when signal 0 reaches `pid`. EPERM counts as alive: a process
    this user may not signal is still a process that exists, and reporting
    it dead would be the more dangerous of the two wrong answers.

    ZERO AND NEGATIVE IDS ARE REFUSED BEFORE os.kill EVER SEES THEM, and
    that guard is the whole reason this is a function rather than an
    inline try. In POSIX a negative pid addresses a process GROUP and -1
    addresses every process the caller may signal, so os.kill(-1, 0)
    succeeds and would report a successor alive that never existed. Found
    by the step 3 test suite on 2026-08-08, before this shipped."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


SPOKE = "SPOKE"
RUNNING = "RUNNING"
GONE = "GONE"


def liveness_verdict(alive, first_line):
    """One of three words, and the exit code follows from it.

    SPOKE    output arrived. The handover took. A one-turn headless
             session is SUPPOSED to end afterwards, so the process being
             gone by now is success, not failure.
    RUNNING  nothing yet, but the process is there. This is the NORMAL
             shape of a healthy `claude -p`, which buffers its entire turn
             and flushes at the end: the live canary of 2026-08-08 sat on
             a zero-byte log at 43 seconds while working correctly. An
             earlier draft of this step failed that successor.
    GONE     nothing, and the process has exited. THIS is the 2026-08-08
             disaster: a prompt swallowed by the variadic --add-dir, the
             session dead in the same second, reported as launched."""
    if first_line:
        return SPOKE
    return RUNNING if alive else GONE


def liveness_note(pid, alive, first_line):
    """The sentence the evidence row carries. Written here, in one place,
    so the note a founder reads on the page and the note a test asserts
    against cannot drift apart.

    The successor's line is redacted through the SAME redactor every
    generated view uses. This is text this file did not write, emitted by
    a session that may have echoed an environment variable in its first
    breath, and it is about to be written to disk in a store that gets
    copied into handover packets.

    Nothing in these three sentences claims the successor UNDERSTOOD its
    brief, and a test pins that. This row proves a process existed and,
    sometimes, that it said a word. It cannot prove more, and a handover
    protocol that overstated its own evidence would be worth less than
    one that reported nothing."""
    verdict = liveness_verdict(alive, first_line)
    if verdict == SPOKE:
        try:
            shown = bs.redact_text(first_line)
        except bs.RedactionUnavailable as exc:
            shown = ("[WITHHELD: the secret redactor was unavailable (%s), "
                     "so the successor's own line was not stored]" % exc)
        state = "still running" if alive else "has since exited"
        return ("SPOKE: process %s, %s. First log line: %s"
                % (pid, state, shown))
    if verdict == RUNNING:
        return ("RUNNING: process %s is still running, no output yet. "
                "`claude -p` buffers a whole turn and flushes at the end, "
                "so an empty log inside the watch window is expected. What "
                "this proves is that the process did not die on its prompt; "
                "read the log for what it actually did." % pid)
    return ("GONE: process %s produced NO OUTPUT and has already exited. "
            "This is the shape of a successor killed by its own launch "
            "line (2026-08-08, relay-1). Read the log and start the next "
            "session by hand." % pid)


def record_liveness(store, project_id, pid, log_ref, first_line, alive,
                    actor):
    """File ONE evidence row against the PROJECT (not a task: a handover
    belongs to the program, and pinning it to whichever task happened to
    be open would lose it the moment that task closed). Returns the
    evidence id."""
    evidence_id = uuid.uuid4().hex
    store.add_evidence({
        "evidence_id": evidence_id,
        "subject_type": "project",
        "subject_id": project_id,
        "kind": LIVENESS_KIND,
        "ref": log_ref,
        "note": liveness_note(pid, alive, first_line),
        "created_at": bs.now_iso(),
    }, project_id, actor)
    return evidence_id


def _liveness_actor():
    """Attribution for the row above. A session id is invented per process
    when the environment does not carry one, the same shape tools/
    bm_project.py's _actor uses, so two handovers can never collide on an
    empty string."""
    return {"actor_type": "model",
            "actor_name": "brothermode continue",
            "session_id": (os.environ.get("BROTHERMODE_SESSION_ID")
                           or ("continue-" + uuid.uuid4().hex))}


# ---------------------------------------------------------------------------
# The relay brake (2026-08-09). Three mechanical stops in front of the spawn.
#
# WHAT WENT WRONG, so the reason survives longer than the memory of it
#   CONTRACT_LINES item 4 above tells every successor to launch its own
#   successor before it closes. Written as prose, it is unconditional: it
#   names no budget, no depth, no end time. On 2026-08-08 the chain ran
#   while the founder was away from the machine. Forty sessions in this
#   project alone logged 6,331 turns between 00:00Z and 10:00Z, roughly
#   6.4 million output tokens and 1.69 billion cache-read tokens, with
#   fifteen live at once against a declared cap of nine. Three of them died
#   holding uncommitted work that had to be replayed onto main by hand
#   (commit e7dcd01).
#
#   The bitter part is that the ceiling already existed. The signed
#   contract carried token_ceiling = 3,000,000 and tools/bm_controller.py
#   compares spend against it correctly. It never fired because the meter
#   was empty: autonomy_spend held ZERO rows for the whole window, so the
#   breaker compared 0 against 3,000,000 forever, and in any case its
#   authority reaches only units it dispatched itself, which was two. Every
#   other session was outside any contract by construction.
#
#   So this brake deliberately does NOT depend on the meter. It depends on
#   two facts a relay always knows about itself, its own depth and the
#   clock, and it consults spend only as a third, additional stop. A
#   control that needs a working meter is a control that fails exactly when
#   the meter is what broke.
#
# WHY A REFUSAL EXITS 0
#   Refusing to continue is the tool doing its job, not failing at it. The
#   packet is still written and the launch line is still printed, so a
#   founder loses no handover and can run the command by hand after reading
#   why the chain stopped. What the refusal removes is the machine's
#   ability to continue ITSELF, unattended, without anyone deciding to.
# ---------------------------------------------------------------------------

#: The chain's own depth, carried to each successor in its environment.
RELAY_GEN_ENV = "BROTHERMODE_RELAY_GEN"
#: The cap that depth is measured against, carried forward unchanged so a
#: founder who lowers it for one launch lowers it for the whole chain.
RELAY_MAX_ENV = "BROTHERMODE_RELAY_MAX"
#: An ISO-8601 wall-clock instant past which no further hop starts.
RELAY_DEADLINE_ENV = "BROTHERMODE_RELAY_DEADLINE"

#: Eight hops. A STARTING THRESHOLD, not a measured optimum: it is small
#: enough that a chain cannot quietly outlive a night and large enough that
#: an ordinary relay never meets it. Re-measure it against your own hop
#: length and set --max-generations accordingly.
RELAY_MAX_DEFAULT = 8


def _int_or(value, floor_value):
    """An integer, or `floor_value` when the text is missing or not a
    number. Every caller here degrades DOWNWARD, to zero or to the default
    cap, so a corrupt counter can only ever tighten this brake. A counter
    that read as permission to keep going would be worse than no counter
    at all, because it would look like a control while being a hole."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return floor_value


def relay_generation(env=None):
    """How many hops deep this session already is. Absent means zero: a
    session a human started by hand is generation zero, and that is the
    correct reading, because the cap exists to bound what the MACHINE
    starts, not what the founder does."""
    env = os.environ if env is None else env
    value = _int_or(env.get(RELAY_GEN_ENV), 0)
    return value if value > 0 else 0


def relay_max(env=None, override=None):
    """The cap in force. An explicit --max-generations wins, then the
    inherited environment, then the default. Zero is allowed and means
    "hand over but launch nothing", which is what a founder reaches for
    when they want the packet without the chain."""
    env = os.environ if env is None else env
    if override is not None:
        return max(0, _int_or(override, RELAY_MAX_DEFAULT))
    return max(0, _int_or(env.get(RELAY_MAX_ENV), RELAY_MAX_DEFAULT))


def relay_deadline(env=None, override=None):
    """The wall-clock end of the chain as a naive local datetime, or None.
    An unreadable deadline returns None rather than raising: the caller
    still holds the generation cap, and a typo in a timestamp must not be
    able to strand a founder's relay at hop one with a traceback."""
    env = os.environ if env is None else env
    text = override if override is not None else env.get(RELAY_DEADLINE_ENV)
    if not text:
        return None
    text = str(text).strip()
    # Offset-aware first. The adversarial review of PR 36 executed the
    # failure this closes: "2026-08-09T18:00:00+09:00" parsed to None and
    # the founder's deadline silently vanished. fromisoformat reads offsets
    # on 3.9 once Z is spelled +00:00; an aware result converts to the
    # machine's local wall clock, because everything else in this brake
    # compares against naive local time.
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:  # sbe: allow-silent offset-aware parsing failed; the loop below tries the naive strptime shapes next before giving up
        pass
    for shape in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                  "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, shape)
        except ValueError:  # sbe: allow-silent one strptime shape did not match; the next shape in the fallback chain is tried
            continue
    return None


def relay_refusal(generation, maximum, deadline, now, spend):
    """The whole decision, as ONE pure function of five values, so it can
    be proven without a store, a clock or a subprocess. Returns None when
    the next hop may start, or a plain-language sentence saying why it may
    not. The order is deliberate: depth first, because it is the stop that
    still works when everything else is broken, then the clock, then spend."""
    if maximum is not None and generation >= maximum:
        return ("this relay is %d hop(s) deep and its cap is %d, so it "
                "stops here. Raise it with --max-generations N on a launch "
                "you are watching, or run the command below yourself."
                % (generation, maximum))
    if deadline is not None and now >= deadline:
        return ("this relay's deadline (%s) has passed, so it stops here. "
                "Set a new one with --deadline, or run the command below "
                "yourself." % deadline.strftime("%Y-%m-%d %H:%M"))
    verdict = (spend or {}).get("verdict")
    if verdict == "hard-stop":
        pct = (spend or {}).get("token_pct")
        return ("the project's spend ceiling has been reached (%s), so this "
                "relay stops here. Raise the ceiling deliberately, or run "
                "the command below yourself."
                % ("%.0f per cent of it used" % pct if pct is not None
                   else "hard-stop"))
    # NO-DATA is never a pass and never a block. A store with no signed
    # contract yields verdict "no-data", and refusing on it would kill every
    # relay in every project that never signed one. The generation cap is
    # what bounds that case, which is why the cap has no off switch.
    return None


def successor_env(env, generation, maximum, deadline):
    """The child's environment: the parent's, plus this chain's depth
    incremented and its limits carried forward. Built as a copy and passed
    to Popen explicitly rather than by mutating os.environ, because a relay
    that edits its own process environment changes every later call in the
    same session."""
    child = dict(env)
    child[RELAY_GEN_ENV] = str(generation + 1)
    child[RELAY_MAX_ENV] = str(maximum)
    if deadline is not None:
        child[RELAY_DEADLINE_ENV] = deadline.strftime("%Y-%m-%dT%H:%M:%S")
    return child


# ---------------------------------------------------------------------------
# continue
# ---------------------------------------------------------------------------

_CONTINUE_FLAGS = ("project-id", "root", "out", "log", "dry-run", "json",
                   "permission-mode", "liveness-timeout", "max-generations",
                   "deadline")


def cmd_continue(argv):
    _pos, kv = _parse(argv, _CONTINUE_FLAGS,
                      wants_value=("project-id", "root", "out", "log",
                                   "permission-mode", "liveness-timeout",
                                   "max-generations", "deadline"))
    try:
        timeout = float(kv.get("liveness-timeout") or LIVENESS_TIMEOUT)
    except ValueError:
        _err("bm_continue: --liveness-timeout wants a number of seconds, "
             "got %r." % kv.get("liveness-timeout"))
        return 2
    # The chain's own limits, computed BEFORE any store work so (a) an
    # explicitly typed deadline that cannot be read fails CLOSED right here
    # (the adversarial review of PR 36 executed the old behaviour: an offset
    # deadline parsed to None and the chain ran unbounded in time with
    # nobody told), and (b) the printed launch line below can carry them.
    generation = relay_generation()
    maximum = relay_max(override=kv.get("max-generations"))
    deadline = relay_deadline(override=kv.get("deadline"))
    if kv.get("deadline") and deadline is None:
        _err("bm_continue: --deadline %r could not be read as a time. "
             "Nothing was launched: a deadline you typed and the machine "
             "dropped would be the silent unbounding this brake exists to "
             "end. Use ISO 8601, for example 2026-08-10T07:00:00 or "
             "2026-08-10T07:00:00+09:00." % kv.get("deadline"))
        return 2
    root = kv.get("root")
    if not root:
        # bs.require_root() is the ONE root resolver every sibling adapter
        # uses (tools/bm_project.py's _root, tools/bm_lead.py's _root): it
        # reads BROTHERMODE_ROOT, then the marker walk, and refuses loudly
        # rather than guessing. --root exists for testability only, the
        # same reason scripts/doctor.py takes --settings.
        try:
            root, _source = bs.require_root()
        except bs.BMStoreError as exc:
            _err("bm_continue: %s" % exc)
            return 1
    try:
        store = bs.Store(root, create=False)
    except bs.BMStoreError as exc:
        _err("bm_continue: %s" % exc)
        return 1
    try:
        project_id = kv.get("project-id")
        if not project_id:
            projects = store.list_projects()
            if len(projects) != 1:
                _err("bm_continue: this store holds %d projects, so name the "
                     "one to hand over with --project-id ID."
                     % len(projects))
                return 1
            project_id = projects[0].get("project_id")
        filename = kv.get("out") or packet_filename(store, project_id)
        packet_path = bs.safe_project_path(root, filename)
        log_name = "successor-%s.log" % project_id
        log_path = kv.get("log") or bs.safe_project_path(root, log_name)
        # EVERY path inside the packet is RELATIVE to the project root, and
        # that is a requirement rather than a preference: the document
        # funnel masks absolute paths (bs.mask_absolute_paths), so an
        # absolute path written here reaches the successor as
        # "[PATH WITHHELD]" and the brief becomes unreadable. Relative
        # paths survive the funnel untouched AND make the packet portable,
        # which is the same reason the launch command below is written to
        # be run from the project root.
        packet_rel = os.path.relpath(packet_path, root)
        log_rel = os.path.relpath(log_path, root)
        argv_out = launch_argv(packet_rel, ".", project_id=project_id,
                               permission_mode=kv.get("permission-mode"))
        # The printed line carries the chain's next depth and its limits,
        # so a human or script pasting it inherits the brake instead of
        # restarting at generation zero (PR 36 review, finding 6).
        printed_env = {RELAY_GEN_ENV: str(generation + 1),
                       RELAY_MAX_ENV: str(maximum)}
        if deadline is not None:
            printed_env[RELAY_DEADLINE_ENV] = deadline.strftime(
                "%Y-%m-%dT%H:%M:%S")
        command_line = printable_command(argv_out, log_rel,
                                         env_prefix=printed_env)
        text = render_packet(store, project_id, packet_rel, command_line)
        if text is None:
            _err("bm_continue: there is no project %r in this store, so "
                 "there is nothing to hand over. Nothing was written."
                 % project_id)
            return 1
        try:
            bs.write_generated_document(packet_path, text)
        except (bs.RedactionUnavailable, OSError) as exc:
            _err("bm_continue: the packet could not be written (%s). "
                 "Nothing was launched: a successor with no packet is a "
                 "successor with no brief." % exc)
            return 1
        # The spend half of the brake, read while the store is still open.
        # A store that cannot answer degrades to NO SPEND OPINION rather
        # than to a refusal, and it SAYS SO on stderr: the generation cap
        # below is what bounds the chain when the meter is unavailable, and
        # on 2026-08-08 an unavailable meter is exactly what the ceiling
        # had. Swallowing this would rebuild, here, the same silence that
        # let a ceiling of three million tokens mean nothing for ten hours.
        try:
            spend = store.spend_totals(project_id)
        except bs.BMStoreError as exc:
            spend = {}
            _err("bm_continue: the spend meter could not be read (%s), so "
                 "this launch has no spend opinion. The relay's generation "
                 "cap and deadline still apply." % exc)
    finally:
        store.close()

    # ONE json document on stdout, never two. --json used to print a packet
    # summary here AND a launch summary after the spawn, so a real launch
    # emitted two concatenated objects and `json.loads` refused the lot:
    # the machine-readable mode was unreadable by machines on exactly the
    # path that matters. Under --json the dry run prints here and returns;
    # a real launch prints once, at the end, with its liveness verdict.
    if kv.get("json"):
        if kv.get("dry-run"):
            _print_json({"packet": packet_path, "project_id": project_id,
                         "command": command_line, "log": log_path,
                         "launched": False})
    else:
        _out("Wrote the handoff packet: %s" % packet_path)
        _out("")
        _out("The successor's launch command:")
        _out("  %s" % command_line)
        _out("")
    if kv.get("dry-run"):
        if not kv.get("json"):
            _out("Dry run: the packet is written, the command is printed, "
                 "and this run launched nothing.")
        return 0
    # THE BRAKE, and it sits here on purpose: after the packet is written
    # and the command is printed, before anything is started. A founder who
    # meets a refusal still has the whole handover in their hands.
    # generation, maximum and deadline were computed at the top of this
    # function, before any store work, so the printed line already carries
    # them.
    refusal = relay_refusal(generation, maximum, deadline,
                            datetime.datetime.now(), spend)
    if refusal is None:
        # The WIDTH bound (PR 36 review, finding 1, the critical one). The
        # depth cap bounds how LONG a chain can grow; nothing bounded how
        # WIDE, and this spawn is a direct process start no Bash hook can
        # see: one session calling continue k times fanned out k gen-1
        # successors, each entitled to do the same. So the launch consults
        # the same live-session count the machine-wide cap uses, and
        # refuses to add a session at or over that cap. A count that
        # cannot be taken is no opinion (the counter fails open, loudly),
        # and the depth cap still stands in that case.
        try:
            cap_mod = _session_cap()
            # The launcher does not count against its own launch, the same
            # semantics the hook applies (re-verification note, 2026-08-09).
            # A session id is knowable here only through the environment;
            # absent one, the count degrades TIGHTER, never looser.
            own = (os.environ.get("CLAUDE_SESSION_ID")
                   or os.environ.get("BROTHERMODE_SESSION_ID"))
            live = cap_mod.count_live_sessions(cap_mod.projects_root(),
                                               exclude_session=own)
            width_cap = cap_mod.cap()
            cap_env_name = cap_mod.CAP_ENV
        except Exception as exc:
            # A counter that cannot even load is a count that cannot be
            # taken: no opinion, said out loud, never a crash that kills
            # the handover. The depth cap above already ran and stands.
            _err("bm_continue: the session counter could not be consulted "
                 "(%s), so this launch has no width opinion. The chain's "
                 "depth cap still applies." % exc)
            live = None
            width_cap = None
        if live is not None and width_cap is not None and live >= width_cap:
            refusal = ("%d Claude sessions are already live against the "
                       "machine-wide cap of %d, so this relay adds none. "
                       "Close a session, or raise the cap deliberately "
                       "with %s=N, or run the command below yourself."
                       % (live, width_cap, cap_env_name))
    if refusal is not None:
        if kv.get("json"):
            _print_json({"packet": packet_path, "project_id": project_id,
                         "command": command_line, "log": log_path,
                         "launched": False, "relay_generation": generation,
                         "relay_max": maximum, "refused": refusal})
        else:
            _out("The relay stopped here, and started nothing: %s" % refusal)
            _out("")
            _out("The packet above is written and current, so nothing is "
                 "lost. The machine will not continue itself past a brake; "
                 "the command above is for YOUR hand, and it carries the "
                 "chain's depth so a successor you start inherits the same "
                 "brake.")
        return 0

    # The mark BEFORE the spawn. The log is append-mode and named per
    # project, so hop two would otherwise quote hop one's opening line as
    # its own successor's proof of life.
    log_mark = log_size(log_path)
    try:
        pid = spawn_successor(argv_out, log_path, cwd=root,
                              env=successor_env(os.environ, generation,
                                                maximum, deadline))
    except OSError as exc:
        _err("bm_continue: the successor could not be started (%s). The "
             "packet is written, so a founder or a later session can run "
             "the command above by hand." % exc)
        return 1

    # A spawn is not a handover (Phase C step 3). Watch for the successor's
    # own first line, ask the operating system whether it is still there,
    # and file BOTH as evidence before anything reports success. The row is
    # written whichever way the verdict goes: a silent successor that left
    # no record behind is how 2026-08-08 lost half a day.
    first_line = first_log_line(log_path, timeout=timeout, offset=log_mark)
    alive = process_alive(pid)
    verdict = liveness_verdict(alive, first_line)
    note = liveness_note(pid, alive, first_line)
    evidence_id = None
    evidence_error = None
    try:
        with bs.Store(root, create=False) as store:
            evidence_id = record_liveness(store, project_id, pid, log_rel,
                                          first_line, alive,
                                          _liveness_actor())
    except (bs.BMStoreError, OSError) as exc:
        # The launch already happened; refusing to say so because the row
        # could not be written would lose the pid entirely. Report it
        # loudly, print the note that would have been stored, and fail.
        evidence_error = str(exc)

    if kv.get("json"):
        _print_json({"packet": packet_path, "project_id": project_id,
                     "command": command_line, "log": log_path,
                     "launched": True, "pid": pid, "alive": alive,
                     "verdict": verdict, "first_log_line": first_line,
                     "liveness_evidence_id": evidence_id,
                     "liveness_evidence_error": evidence_error})
    else:
        _out("Launched the successor: process %d, logging to %s."
             % (pid, log_path))
        _out(note)
        if evidence_id:
            _out("Filed as evidence %s against project %s, so the next "
                 "packet carries it." % (evidence_id, project_id))
        if evidence_error:
            _err("bm_continue: the successor is running as process %d but "
                 "its liveness could not be filed (%s). Record it by hand."
                 % (pid, evidence_error))
    if evidence_error:
        return 1
    if verdict == GONE:
        # The one verdict that is NOT a pass. The founder asked for a
        # handover that happens while they sleep, and a session exiting 0
        # on a successor that died on its own launch line is precisely the
        # report that lets a dead program look healthy until morning.
        return 1
    return 0


COMMANDS = {"continue": cmd_continue}


def main(argv):
    argv = list(argv or [])
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("bm_continue: generate the handoff packet from the store and "
             "start the next session.")
        _out("")
        _out("  continue  [--project-id ID] [--root PATH] [--out FILE] "
             "[--log PATH] [--permission-mode MODE] "
             "[--liveness-timeout SECONDS] [--dry-run] [--json]")
        _out("")
        _out("A real launch waits for the successor's first line of "
             "output, files it with the process id as evidence, and exits "
             "non-zero only when the successor produced nothing AND has "
             "already died.")
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_continue: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    return COMMANDS[cmd](argv[1:])


def cli():
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

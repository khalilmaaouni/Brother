#!/usr/bin/env python3
"""bm_project.py: the mechanical command line for the seven beginner
commands (Loop 2, docs/superpowers/specs/2026-08-01-loop2-mechanical-
commands-design.md, decisions D-1 and D-4), plus export and purge (WP-H,
docs/superpowers/specs/2026-08-01-loop6-security-closure-design.md, D-3).

WHAT THIS IS
  A THIN CLI over tools/bm_store.py's Store service methods (upsert_project,
  create_task, transition_task, add_forecast, raise_alert, resolve_alert,
  add_evidence, purge_project) and its D-2 read accessors (get_project,
  list_projects, list_tasks, get_task, list_dependencies, list_forecasts,
  latest_forecast, list_alerts, list_evidence, list_attribution). This file
  never issues SQL of its own: the flip condition in D-1 is exactly that,
  and the moment this file needs a query the store does not already offer,
  it has become a second writer and must be folded back into bm_store.py
  instead of growing one here. A structural guard test
  (tools/test_bm_project.py) greps this file's own source for the four SQL
  write and read statement keywords (case sensitive, the way every real
  query in bm_store.py itself is written) and fails the build if any
  appear.

WHAT THIS IS NOT
  Not a second store, not a second writer, not a place that reimplements
  the ten-state lifecycle law: every transition goes through
  Store.transition_task, which calls schema.transition(), which is the one
  place that law is enforced. This file only ever reports what that
  function decided, in its own words.

SUBCOMMANDS
  start                  create the project row, optional first tasks and
                          first forecast, regenerate CANVAS.md; refuses a
                          SECOND project in one root unless --allow-second
                          (see ONE PROJECT PER FOLDER below)
  status                 project, open tasks by state, latest forecast
                          (ranges, confidence, next reforecast event),
                          unresolved alerts by severity, and, with
                          --history N, the last N attribution events (read
                          accessors only)
  next                   the single recommended next task, with why
  task add                thin wrapper over create_task
  task start               convenience: transition a task to 'active'
  task transition          general transition, --reason required; refuses
                          exactly in schema.transition's own words (the
                          state named 'done' is refused by name)
  forecast add             thin wrapper over add_forecast; requires the
                          three durations, at least one token-range flag,
                          and --confidence; refuses a single point
                          estimate (min = likely = max with no --basis)
                          with the forecasting rule quoted (D-1, Loop 5
                          design)
  forecast show            the latest forecast as ranges, confidence, and
                          next reforecast event, plus the count of prior
                          forecasts (read accessors only)
  alert raise               thin wrapper over raise_alert
  alert resolve <id>        thin wrapper over resolve_alert, --reason
                          required
  alert list                unresolved alerts by default; --all for every
                          alert ever raised (read accessors only)
  receipt add               thin wrapper over add_capability_receipt
                          (F4, schema 20): files ONE capability receipt,
                          refusing a bad verification_state by name
                          before the store's own CHECK constraint would
  receipt list               every capability receipt for a project, oldest
                          first; --task-id and --capability-name narrow
                          it (read accessors only); a project with none
                          says so in plain words, never silence
  review <task_id>        records evidence and transitions the task, in
                          ONE atomic Store.review_task call (C1, release-
                          closure loop2 refuter fixes): a transition the
                          ten-state law refuses leaves no evidence behind
                          either; --criterion-id (schema 20, R1.2) links
                          the evidence to one entry of the task's own
                          acceptance_checks, refused by name when it
                          matches none
  deliver                 generate DELIVERY-PACKET.md from rows; refuses
                          a project with zero tasks outright, and refuses
                          when any task is short of the terminal state
                          ('closed') unless --partial; redacted by
                          default (this file is a document handed to
                          someone, an export like the json one below),
                          --raw for the project's own owner
  export                 write ONE json file of every row the store holds
                          for the project (project, tasks, dependencies,
                          forecasts, the alerts tied to it, the evidence
                          whose subject is the project or one of its
                          tasks, and its attribution trail); redacted by
                          default, --raw for the owner, --out PATH to
                          choose where it lands (WP-H, loop6 security-
                          closure design, D-3)
  purge                   erase the project through Store.purge_project;
                          refuses without --confirm <project_id> matching
                          exactly, and prints in plain words what it
                          removed and what it kept (the attribution trail
                          that now also names this purge, the vault, and
                          any generated file already on disk) (WP-H,
                          loop6 security-closure design, D-3)

WHY create=False EVERYWHERE
  Matching bm_store.py's own CLI convention (its cmd_claim and every other
  command besides init): "only init creates a store" only means something
  if every other path refuses instead of quietly creating one. Run
  `python3 tools/bm_store.py init` first.

ONE PROJECT PER FOLDER (the beginner model; C5, release-closure loop2
refuter fixes)
  A root holds ONE project's CANVAS.md and ONE DELIVERY-PACKET.md by
  default: that is the beginner model this whole file is built around, and
  it is also what keeps those two generated views unambiguous (start and
  deliver regenerate them from "the project in this root", not from a
  project_id a founder has to remember to keep straight). `start` refuses
  to create a SECOND, different project_id in a root that already holds
  one, naming the existing project id in its refusal, unless
  --allow-second is passed on purpose. Re-running `start` on the SAME
  project_id (an update) is never a "second project" and is never
  refused. Once a root genuinely holds more than one project,
  _canvas_filename/_packet_filename switch every further start/deliver in
  that root to a project-scoped name (CANVAS-<project_id>.md,
  DELIVERY-PACKET-<project_id>.md) so a second project's generated views
  can never silently overwrite the first's.

RAW LOCAL DISPLAY VERSUS REDACTED EXPORT (loop2 redaction-policy fix)
  The D-2 accessors redact through the SAME export_column policy dump()
  uses, and _DUMP_SAFE_COLUMNS in bm_store.py now lists identifiers,
  schema enums, timestamps and other machine labels for the schema-12
  tables (projects, tasks, forecasts, dependencies, attribution, alerts,
  evidence, runtime_runs), the same discipline every other exported table
  already had. Every founder-typed prose column (name, title, goal,
  message, reason, note, and the rest) still stays withheld by default:
  that part of the policy is unchanged and is not what this file works
  around.

  What this file adds is WHERE raw=True is used. Human-readable terminal
  output (status, next, review) and CANVAS.md read through the accessors
  with raw=True: local display and a locally generated working page for
  the data's own owner are not an export, so there is nothing to withhold
  from the founder reading their own project on their own machine.
  DELIVERY-PACKET.md is different: it is the packet the README and
  cmd_deliver describe as "a packet you can hand to someone", so it is an
  export exactly like the json file below and reads through the
  accessors with raw=False by default; --raw produces the owner's own
  unredacted copy, the same named, explicit escape hatch dump --raw and
  export --raw already use. --json output and the export command's own
  json file are the other export surfaces and stay REDACTED BY DEFAULT,
  exactly like bm_store.py's own dump. Both generated documents still
  pass through bs.write_generated_document before they touch disk, whose
  redact_text funnel is the FINAL guard: raw decides what this file is
  willing to try to show, redact_text decides what a secret-shaped
  string is allowed to survive as, and the two are independent layers on
  purpose (a task title that happens to contain something secret-shaped is
  still caught there even under --raw).

  SBE15 (2026-08-16): both generated documents are shareable artifacts by
  name, and redact_text is the only guard on the raw=True path either one
  can take (a sibling lane is fixing a defect where content can disable
  that guard). Every founder-typed prose field render_canvas/
  render_delivery_packet place into either document (name, goal,
  user_outcome, a task title, an evidence ref, an attribution actor name,
  and list fields like risks or scope) still passes through the local
  _prose() helper, which runs it through tools/bm_learning.py's
  safe_display, the same control-character containment tools/bm_packs.py
  runs store text through before a screen or a page. This is a SECOND,
  independent layer, not a fix for redact_text's own defect: safe_display
  strips control characters and caps length, it does not recognize a
  secret. What it buys: even if redact_text is defeated by content, a
  founder-typed field can no longer forge a fake markdown heading or an
  extra section in the generated document by embedding a newline plus
  markdown syntax. What it does not buy: it does not stop a secret-shaped
  string, in ordinary prose with no control characters, from reaching the
  page if redact_text itself is bypassed; closing that is the sibling
  lane's job, not this file's.

Python 3.9, standard library only. No network. No subprocess.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

#: The only states a task may be CREATED in. Not derived from schema.STATES on
#: purpose: this is a different rule from the transition law, it is shorter,
#: and deriving it would mean a new state added to the lifecycle silently
#: becomes a legal birth state. `planned` is the default and the honest start;
#: `ready` is legal because `next` serves only ready tasks, so refusing it
#: would break the guided flow. Every later state is reached by walking, which
#: is what makes the walk mean anything.
BIRTH_STATES = ("planned", "ready")


def _load(name):
    """Load a sibling module by PATH, the exact technique tools/bm_learn.py
    uses for bm_store.py: this file is invoked from arbitrary working
    directories and must not depend on whatever sys.path the caller
    happened to have."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load("bm_store")
# SBE15 (2026-08-16): safe_display, the same control-character containment
# tools/bm_packs.py runs store text through before it reaches a screen or a
# generated page. render_canvas below reads raw=True always (see RAW LOCAL
# DISPLAY VERSUS REDACTED EXPORT in this file's module docstring);
# render_delivery_packet reads raw=False by default and raw=True only under
# --raw, because DELIVERY-PACKET.md is a shareable artifact by name and is
# an export like the json file, not a local working page. Either way,
# redact_text (bs.write_generated_document's funnel) is the only guard on
# whatever raw path a caller took, and a sibling lane is fixing a defect
# where content can disable it. safe_display is not a substitute for that
# redaction guard (it strips control characters and caps length; it does
# not recognize a secret), so it adds a second, independent layer rather
# than closing the sibling lane's gap: even if redact_text is defeated by
# content, a founder-typed field can no longer forge a fake heading or an
# extra section in the generated document by embedding a newline plus
# markdown syntax.
L = _load("bm_learning")
# THE SAME schema module OBJECT bm_store.py's own service methods raise
# against, not a second independent load of brotherme/core/schema.py.
# importlib.util.module_from_spec + exec_module never registers a module
# under sys.modules on its own, so two independent loads of the identical
# file produce two DIFFERENT SchemaError classes with the same name; an
# `except S.SchemaError` written against a second load would never catch
# what Store.transition_task actually raises (isinstance/except match by
# class identity, not by name). bs._schema() is bm_store.py's own cached
# loader (it is what every one of its Store methods calls S = _schema()
# against), so reusing it here is the only way this file's exception
# handling and this file's S.STATES stay the exact same objects the store
# itself uses. Confirmed by reproduction: an independent second load let
# `task transition ... --to done` raise past main()'s handler entirely.
S = bs._schema()

# The one place "finished for good" is named in this codebase (schema.py's
# own transition() error text says "(none, terminal)" for exactly this
# state, and only this state: it is the sole entry in LEGAL_TRANSITIONS
# whose tuple is empty). deliver's non-terminal check reuses that same,
# single definition of "done" rather than inventing a second one.
TERMINAL_STATE = "closed"

# No enum constrains Task.priority (schema.py carries no ENUMS entry for
# it); this is a judgment call, documented here rather than silently
# assumed. Known words rank in the order a founder would expect; anything
# else (a number, empty, a withheld marker) sorts after all of them and
# ties are broken alphabetically for a deterministic, repeatable order.
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}


def _priority_key(value):
    v = (value or "").strip().lower()
    return (_PRIORITY_RANK.get(v, 99), v)


# schema.py's Alert.ENUMS names the four legal severities; this ranks them
# most-urgent-first for display (status's unresolved-alerts section, alert
# list), the same judgment-call shape _PRIORITY_RANK above documents:
# unknown or missing severities sort after every known one, alphabetically
# among themselves, for a deterministic order rather than an accidental one.
_SEVERITY_RANK = {"critical": 0, "high": 1, "attention": 2, "info": 3}


def _severity_key(value):
    v = (value or "").strip().lower()
    return (_SEVERITY_RANK.get(v, 99), v)


def _severity_sorted(alerts):
    return sorted(alerts, key=lambda a: _severity_key(a.get("severity")))


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _root():
    root, _source = bs.require_root()
    return root


def _store():
    # Matching bm_store.py's own CLI convention (its cmd_claim and every
    # command besides init): only `bm_store.py init` may pass create=True.
    # Every command here refuses 'no-store' instead, naming the fix.
    return bs.Store(_root(), create=False)


def _read_store():
    """A read-only handle for `alert list`, the one command here that only
    ever lists (never mutates) an alert, matching tools/bm_controller.py's
    own _read_store discipline (which itself matches tools/bm_autonomy.py's).
    Constructing bs.Store always migrates a behind-schema database
    (Store.__init__ runs _verify_schema_or_raise(migrate=True)); this never
    does, so a plain `alert list` cannot rewrite the store it is reading."""
    return bs.ReadOnlyStore(_root())


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized. The
    same shape tools/bm_learn.py's own _parse uses, copied rather than
    imported (bm_learn.py is a sibling CLI, not a library this file should
    depend on for its own argument handling)."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            # Every generated adapter under docs/runtimes/ tells the reader
            # "run the command with --help and read the real flags off the
            # tool", precisely so an instruction file never teaches a flag
            # that was renamed. That promise was false at the SUBCOMMAND
            # level: --help fell through to the refusal below and exited 2
            # with "unrecognized flag --help", so the one discovery path the
            # docs point at was the one path that did not work. Printing the
            # recognized flags is exactly what the reader was promised.
            print("recognized flags: %s"
                  % ", ".join("--" + k for k in sorted(known)))
            if wants_value:
                print("flags taking a value: %s"
                      % ", ".join("--" + k for k in sorted(wants_value)))
            sys.exit(0)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_project: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_project: --%s needs a value" % name)
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


def _csv(value):
    """'a, b ,c' -> ['a', 'b', 'c']. None or '' -> []."""
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def _require(kv, name, usage):
    val = kv.get(name)
    if not val:
        _err(usage)
        _err("bm_project: --%s is required" % name)
        sys.exit(2)
    return val


def _actor(kv, usage):
    """Build the actor dict every mutating subcommand passes to the store,
    so attribution is a real record of who or what acted, not a guess.
    actor_type is restricted to human|model on this CLI's own surface
    (schema.py's AttributionEvent also allows hook|automation, but this
    tool is invoked directly by a person or by a model runtime, never as a
    hook); actor_name has no sensible default, so it is required."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_project: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(2)
    actor_name = _require(kv, "actor-name", usage)
    # A fresh, unguessable id per process when --session-id is omitted,
    # matching bm_store.py's own _default_cli_session_id() (GATE 3: two
    # independent invocations that both omitted it must never collide on
    # an empty string). Inlined rather than calling that private function
    # across the module boundary; the shape is a one-liner.
    session_id = kv.get("session-id") or ("cli-" + uuid.uuid4().hex)
    return {"actor_type": actor_type, "actor_name": actor_name,
            "session_id": session_id}


_ACTOR_FLAGS = ("actor-type", "actor-name", "session-id")


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Generated-view marker splice (D-3: "marker comments like STATE.md uses").
# This mirrors bm_store.py's write_state_view technique (BEGIN/END markers,
# refuse on a damaged marker pair, splice the generated block back in place,
# preserve any human prose outside the markers verbatim), through the same
# public file funnel bm_docs.py and bm_packs.py already use
# (bs.write_generated_document), rather than reinventing atomic-write or
# reaching into bm_store's private _write_generated_file. It does not
# reproduce write_state_view's STATE.md.bak-* backup rotation: that is a
# disclosed simplification (see this file's own module docstring / this
# work package's return), not an oversight.
# ---------------------------------------------------------------------------

def _splice_generated(existing_text, generated_block, begin, end):
    """Return the full file text with `generated_block` (a string that
    itself starts with `begin`, ends with `end`, and carries NO trailing
    newline of its own) spliced between the markers in `existing_text`,
    preserving everything outside them, INCLUDING whatever whitespace
    followed the old END marker. Refuses (raises bs.OwnershipRefused) on a
    damaged marker pair (found counts other than (0, 0) or (1, 1)), the
    identical GATE A rule write_state_view enforces, so a corrupted file
    is never guessed at.

    D-4 requires this to regenerate byte-stable from the same rows, which
    means it must be a FIXED POINT: splicing the identical block into its
    own prior output must reproduce that output exactly, not grow it.
    generated_block deliberately carries no trailing newline so that,
    once spliced, the ONE trailing newline in the file is the `post` text
    already captured from the existing file, never a second one stacked
    on top of it. (An earlier version appended a trailing newline to
    generated_block AND kept the file's own trailing newline in `post`,
    so every re-splice into an already-spliced file grew one more blank
    line forever; reproduced directly against bm_store.py's own
    write_state_view too, which carries the identical pattern; that one
    is outside this file's allowed set and is reported, not patched,
    here.)"""
    begin_count = existing_text.count(begin)
    end_count = existing_text.count(end)
    if (begin_count, end_count) not in ((0, 0), (1, 1)):
        raise bs.OwnershipRefused(
            "view-markers-damaged",
            "the generated file has a damaged marker pair (found %d BEGIN "
            "marker(s) and %d END marker(s); a healthy file has exactly "
            "one of each, or neither). Refusing to write it: repair by "
            "hand so there is exactly one '%s' line followed by exactly "
            "one '%s' line, or remove both and let this command add a "
            "fresh block. Nothing was changed."
            % (begin_count, end_count, begin, end))
    if begin_count == 1:
        pre, rest = existing_text.split(begin, 1)
        _mid, post = rest.split(end, 1)
        return pre + generated_block + post
    if existing_text:
        sep = "" if existing_text.endswith("\n\n") else (
            "\n" if existing_text.endswith("\n") else "\n\n")
        return existing_text + sep + generated_block + "\n"
    return generated_block + "\n"


def _write_generated(root, filename, generated_block, begin, end):
    """Read `filename` under `root` if present, splice `generated_block` in
    between the markers, and write it back through the shared file funnel.
    Advisory: a RedactionUnavailable from the write funnel is reported on
    stderr but never raised past this point, matching bm_store.py's own
    _refresh_state_view policy ("the mutation this call follows has
    already committed by the time this runs; a view-refresh failure must
    never be reported as though the mutation itself failed"). A damaged
    marker pair (bs.OwnershipRefused) is reported the same way: the row
    data is safe in the store either way, and re-running any mutating
    command tries the regeneration again."""
    path = bs.safe_project_path(root, filename)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            existing = fh.read()
    except OSError:
        existing = ""
    try:
        new_text = _splice_generated(existing, generated_block, begin, end)
        bs.write_generated_document(path, new_text)
    except bs.RedactionUnavailable as e:
        _err("bm_project: could not regenerate %s (%s); the command above "
             "still committed. Fix the redactor, then re-run any mutating "
             "command to regenerate it." % (filename, e))
    except bs.OwnershipRefused as e:
        _err("bm_project: could not regenerate %s (%s: %s); the command "
             "above still committed." % (filename, e.reason, e))


CANVAS_BEGIN = "<!-- BEGIN GENERATED BROTHERMODE CANVAS (edit outside these markers only) -->"
CANVAS_END = "<!-- END GENERATED BROTHERMODE CANVAS -->"
DELIVERY_BEGIN = "<!-- BEGIN GENERATED BROTHERMODE DELIVERY PACKET (edit outside these markers only) -->"
DELIVERY_END = "<!-- END GENERATED BROTHERMODE DELIVERY PACKET -->"


def _canvas_filename(store, project_id):
    """CANVAS.md, unless the store holds more than one project (C5,
    release-closure loop2 refuter fixes). This file's own module
    docstring documents the beginner model as ONE PROJECT PER FOLDER, so
    the plain name is what every ordinary caller sees. The moment a
    second project exists (only reachable through `start
    --allow-second`), a shared, plain CANVAS.md would silently belong to
    whichever project last ran a mutating command, so every project gets
    its own file from that point on."""
    if len(store.list_projects()) > 1:
        return "CANVAS-%s.md" % project_id
    return "CANVAS.md"


def _packet_filename(store, project_id):
    """DELIVERY-PACKET.md, or a per-project name once a second project
    exists. Same reasoning as _canvas_filename above."""
    if len(store.list_projects()) > 1:
        return "DELIVERY-PACKET-%s.md" % project_id
    return "DELIVERY-PACKET.md"


_PROSE_LIMIT = 300   # short founder-typed fields: name, title, actor, ref
_LONG_PROSE_LIMIT = 4000   # multi-sentence fields: goal, user_outcome


def _prose(value, limit=_PROSE_LIMIT):
    """A founder-typed prose value as SBE15 wants it shown in a generated
    document: control characters stripped so it cannot forge a fake heading
    or an extra section (see the L = _load(...) comment above), never
    truncating ordinary content in practice (the limit is far past anything
    a real name, title, or reference runs to)."""
    return L.safe_display(value or "", limit)


def _fmt_list(value):
    """A LIST_FIELDS value as the accessor actually returned it: a real
    list once redaction allows it to decode, or (today, by default) the
    withheld marker string it still is when it cannot. Never assumes
    either shape."""
    if isinstance(value, list):
        return ", ".join(_prose(str(v)) for v in value) if value else "(none)"
    return _prose(str(value)) if value else "(none)"


def _tasks_by_state(store, project_id, raw=False):
    """{state: [task, ...]} for every one of the ten lifecycle states, in
    schema.STATES order, using ONLY list_tasks(project_id, status=state):
    one call per known state name rather than reading the (possibly
    withheld) status column back out of a generic list_tasks(project_id)
    call. This is what lets state-based logic (open-task counts, the
    'ready' pool next draws from, deliver's terminal check) stay correct
    regardless of redaction, because the state being grouped by is the
    query parameter this file already knows, never a value read back from
    a possibly-withheld column. `raw` passes straight through to
    list_tasks: True for the local-display and generated-document callers,
    False (the default) for anything building a redacted --json export."""
    return {state: store.list_tasks(project_id, status=state, raw=raw)
            for state in S.STATES}


def render_canvas(store, project_id):
    """The project canvas as a GENERATED view, built ONLY from rows read
    through the D-2 accessors, read raw=True: CANVAS.md is a document
    generated for the project's own owner, not an export (see this file's
    module docstring), and still passes through bs.write_generated_document
    before it touches disk, whose redact_text funnel is the final guard.
    Deliberately carries no timestamp of its own render time: D-4 requires
    this to regenerate byte-stable from the same rows, and a "generated at"
    line would make two back-to-back regenerations of the identical
    project differ by nothing but the clock."""
    project = store.get_project(project_id, raw=True)
    lines = [CANVAS_BEGIN, ""]
    if project is None:
        lines.append("No project %r." % project_id)
        lines.append("")
        lines.append(CANVAS_END)
        return "\n".join(lines)
    lines.append("# Project Canvas: %s" % _prose(project.get("name")))
    lines.append("")
    lines.append("project_id: %s" % project.get("project_id"))
    lines.append("status: %s" % (project.get("status") or "(none)"))
    lines.append("phase: %s" % (project.get("phase") or "(none)"))
    lines.append("")
    lines.append("## Outcome")
    lines.append(_prose(project.get("goal"), _LONG_PROSE_LIMIT) or "(none)")
    lines.append("")
    lines.append("## User")
    lines.append(_prose(project.get("user_outcome"), _LONG_PROSE_LIMIT) or "(none)")
    lines.append("")
    lines.append("## Included")
    lines.append(_fmt_list(project.get("scope_in")))
    lines.append("")
    lines.append("## Not included")
    lines.append(_fmt_list(project.get("scope_out")))
    lines.append("")
    lines.append("## Success checks")
    lines.append(_fmt_list(project.get("success_criteria")))
    lines.append("")
    lines.append("## Main risks")
    lines.append(_fmt_list(project.get("risks")))
    lines.append("")
    # Schema 19 (R1.1, 2026-08-12). These render beside risks rather than in
    # some later section because they answer the two questions a reader of a
    # risk list asks next: what would make it right to STOP, and what did we
    # decide this is deliberately NOT. PRODUCT-DIRECTION.md section 5.1 names
    # both as things a project's outcome contract must own, and until schema
    # 19 neither could be recorded at all. _fmt_list renders an empty list as
    # its own "not stated" line, so a project that has not filled these in
    # says so plainly instead of the section vanishing, which is the whole
    # point: an absent kill criterion is a fact about the project, not a
    # formatting detail.
    lines.append("## What would make it right to stop")
    lines.append(_fmt_list(project.get("kill_criteria")))
    lines.append("")
    lines.append("## Deliberately not doing")
    lines.append(_fmt_list(project.get("non_goals")))
    lines.append("")
    lines.append("## Tasks by state")
    by_state = _tasks_by_state(store, project_id, raw=True)
    for state in S.STATES:
        tasks = by_state[state]
        if not tasks:
            continue
        lines.append("- %s (%d):" % (state, len(tasks)))
        for t in tasks:
            lines.append("  - %s: %s" % (t.get("task_id"), _prose(t.get("title"))))
    lines.append("")
    lines.append("## Latest forecast")
    forecast = store.latest_forecast(project_id, raw=True)
    if forecast is None:
        lines.append("(none yet)")
    else:
        lines.append(
            "%s to %s, confidence %s (assumptions: %s)"
            % (forecast.get("minimum_duration") or "?",
               forecast.get("maximum_duration") or "?",
               forecast.get("confidence"),
               _fmt_list(forecast.get("assumptions"))))
    lines.append("")
    lines.append(CANVAS_END)
    return "\n".join(lines)


def render_delivery_packet(store, project_id, raw=False):
    """The delivery packet as a GENERATED view: project, tasks with their
    evidence, forecasts, attribution summary, all from rows only. Unlike
    render_canvas above, this file IS an export: the README describes it as
    the thing the guided flow ends in, "a packet you can hand to someone",
    and SECURITY.md's withholding policy governs every export, so every one
    of the reads below takes `raw` rather than hardcoding raw=True. Default
    False produces the withheld document a recipient may see; --raw (see
    cmd_deliver) produces the project owner's own unredacted copy, the same
    contract cmd_export already offers for EXPORT-<id>.json. CANVAS.md stays
    raw=True unconditionally because nothing invites handing it over; that
    is a DIFFERENT document and out of scope for this function's change.
    Either way this still passes through bs.write_generated_document's
    redact_text funnel as the final guard before it touches disk."""
    project = store.get_project(project_id, raw=raw)
    lines = [DELIVERY_BEGIN, ""]
    if project is None:
        lines.append("No project %r." % project_id)
        lines.append("")
        lines.append(DELIVERY_END)
        return "\n".join(lines)
    lines.append("# Delivery Packet: %s" % _prose(project.get("name")))
    lines.append("")
    lines.append("project_id: %s" % project.get("project_id"))
    lines.append("")
    lines.append("## Tasks")
    by_state = _tasks_by_state(store, project_id, raw=raw)
    for state in S.STATES:
        for t in by_state[state]:
            lines.append("### %s (%s)" % (t.get("task_id"), state))
            lines.append(_prose(t.get("title")) or "(no title)")
            evidence = store.list_evidence("task", t.get("task_id"), raw=raw)
            if evidence:
                for ev in evidence:
                    lines.append("  - evidence %s: kind=%s ref=%s"
                                 % (ev.get("evidence_id"), ev.get("kind"),
                                    _prose(ev.get("ref"))))
            else:
                lines.append("  - (no evidence recorded)")
    lines.append("")
    lines.append("## Forecasts")
    forecasts = store.list_forecasts(project_id, raw=raw)
    if not forecasts:
        lines.append("(none recorded)")
    for f in forecasts:
        lines.append("- %s: confidence %s, %s to %s"
                     % (f.get("forecast_id"), f.get("confidence"),
                        f.get("minimum_duration") or "?",
                        f.get("maximum_duration") or "?"))
    lines.append("")
    lines.append("## Attribution summary")
    attributions = store.list_attribution(project_id, limit=50, raw=raw)
    if not attributions:
        lines.append("(none recorded)")
    for a in attributions:
        lines.append("- %s: %s by %s (%s)"
                     % (a.get("timestamp"), a.get("event_type"),
                        _prose(a.get("actor_name")), a.get("actor_type")))
    lines.append("")
    lines.append(DELIVERY_END)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

_START_FLAGS = (
    "project-id", "name", "goal", "user-outcome", "project-type",
    "primary-persona", "experience-level", "status", "phase", "scope-in",
    "scope-out", "success-criteria", "assumptions", "unknowns", "risks",
    "kill-criteria", "non-goals",
    "json", "out-json", "allow-second") + _ACTOR_FLAGS


def _start_usage():
    return ("usage: start --project-id ID --name NAME [--goal G] "
            "[--user-outcome U] [--project-type T] [--primary-persona P] "
            "[--experience-level E] [--status S] [--phase PH] "
            "[--scope-in a,b] [--scope-out a,b] [--success-criteria a,b] "
            "[--assumptions a,b] [--unknowns a,b] [--risks a,b] "
            "[--kill-criteria a,b] [--non-goals a,b] "
            "[--json PATH] [--actor-type human|model] --actor-name NAME "
            "[--session-id SID] [--out-json] [--allow-second]")


def _scaffold_progress_page(root):
    """Copy project-template/PROGRESS.html into a freshly started project,
    once, never over an existing page. The founder's 2026-08-09 ask, made
    mechanical: the progress page standard existed as a template file that
    NOTHING installed, which is a hope rather than a standard, the same
    prose-not-control defect the 8 August postmortem names.

    Three deliberate behaviours, each pinned by a test:
      - the page lands beside CANVAS.md at first start;
      - an EXISTING page is never touched: from its first refresh on, the
        page is the project's own living record, and resetting it to the
        template on a re-run of start would destroy exactly the history
        the page exists to keep;
      - a missing template (a pip install ships tools/ without
        project-template/) warns on stderr and never fails the start: the
        scaffold is a courtesy, the project row is the product.

    BROTHERMODE_PROGRESS_TEMPLATE overrides the template path, for tests
    and for an estate that maintains its own house design."""
    destination = os.path.join(root, "PROGRESS.html")
    # lexists, not exists: a DANGLING symlink is an occupied name whose
    # target is missing, and exists() reads it as free. The O_EXCL open
    # below refuses it too on POSIX, but Windows CI proved (PR 38, both
    # Windows legs red) that CreateFile can create THROUGH such a link, so
    # the guard itself must see the link. lexists does, on every platform.
    # A living page skips silently, create-only by design; a broken link
    # squatting on the name gets a word, because silence there would read
    # as "the page exists" when nothing does.
    if os.path.lexists(destination):
        if not os.path.exists(destination):
            _err("bm_project: the progress page was NOT scaffolded: "
                 "PROGRESS.html is a symlink whose target is missing. "
                 "Remove or repair the link, then re-run start.")
        return
    template = os.environ.get("BROTHERMODE_PROGRESS_TEMPLATE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        os.pardir, "project-template", "PROGRESS.html")
    try:
        with io.open(template, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        _err("bm_project: the progress page was NOT scaffolded (%s). The "
             "project itself is fine; copy project-template/PROGRESS.html "
             "in by hand, or set BROTHERMODE_PROGRESS_TEMPLATE." % e)
        return
    try:
        # Create-EXCLUSIVE, not create-if-absent (PR 38 review finding):
        # os.path.exists is False for a DANGLING symlink, so the guard
        # above passed one and a plain open("w") then wrote THROUGH it,
        # creating a file wherever the link pointed. O_EXCL refuses any
        # occupied name, symlink included, and the refusal lands in the
        # same warn-and-continue path as every other scaffold failure.
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o644)
        with io.open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as e:
        _err("bm_project: the progress page was NOT scaffolded (%s). The "
             "project itself is fine." % e)
        return
    # stderr, deliberately: cmd_start's --out-json contract is ONE json
    # document on stdout, and this notice printed there corrupted it (caught
    # by TestScriptedFirstProject the first time this ran). Advisory lines
    # share the diagnostics channel, the same one-document law bm_continue's
    # --json path already enforces.
    _err("bm_project: scaffolded PROGRESS.html from the template; refresh "
         "it at every closed loop, and a box ticks only on quoted evidence")


def cmd_start(argv):
    _pos, kv = _parse(argv, _START_FLAGS, wants_value=(
        "project-id", "name", "goal", "user-outcome", "project-type",
        "primary-persona", "experience-level", "status", "phase",
        "scope-in", "scope-out", "success-criteria", "assumptions",
        "unknowns", "risks", "kill-criteria", "non-goals", "json")
        + _ACTOR_FLAGS)
    usage = _start_usage()
    project_id = _require(kv, "project-id", usage)
    name = _require(kv, "name", usage)
    actor = _actor(kv, usage)
    payload = {}
    if kv.get("json"):
        try:
            with io.open(kv["json"], encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as e:
            _err("bm_project: could not read --json payload %r: %s"
                 % (kv["json"], e))
            return 2
        if not isinstance(payload, dict):
            _err("bm_project: --json payload must be a JSON object")
            return 2
    now = bs.now_iso()
    project = dict(payload.get("project") or {})
    project.update({"project_id": project_id, "name": name,
                    "created_at": now, "updated_at": now})
    for flag, field in (
            ("goal", "goal"), ("user-outcome", "user_outcome"),
            ("project-type", "project_type"),
            ("primary-persona", "primary_persona"),
            ("experience-level", "experience_level"),
            ("status", "status"), ("phase", "phase")):
        if kv.get(flag):
            project[field] = kv[flag]
    for flag, field in (
            ("scope-in", "scope_in"), ("scope-out", "scope_out"),
            ("success-criteria", "success_criteria"),
            ("assumptions", "assumptions"), ("unknowns", "unknowns"),
            ("risks", "risks"), ("kill-criteria", "kill_criteria"),
            ("non-goals", "non_goals")):
        if kv.get(flag):
            project[field] = _csv(kv[flag])
    store = _store()
    try:
        # C5 (release-closure loop2 refuter fixes): one project per root
        # is the beginner model (see this file's own module docstring).
        # Two DIFFERENT projects sharing a root would each regenerate the
        # same CANVAS.md / DELIVERY-PACKET.md in turn, silently belonging
        # to whichever project last ran a mutating command. Re-running
        # start on the SAME project_id (an update) is not a second
        # project and is never refused here.
        others = [p for p in store.list_projects()
                  if p.get("project_id") != project_id]
        if others and not kv.get("allow-second"):
            _err("cannot start project %r: this store already holds "
                 "project %r; pass --allow-second to add a second "
                 "project on purpose (the beginner model is one project "
                 "per folder)."
                 % (project_id, others[0].get("project_id")))
            return 1
        store.upsert_project(project, actor)
        task_ids = []
        for task in payload.get("tasks") or []:
            t = dict(task)
            t.setdefault("task_id", uuid.uuid4().hex)
            t.setdefault("project_id", project_id)
            t.setdefault("status", "planned")
            store.create_task(t, actor)
            task_ids.append(t["task_id"])
        forecast_id = None
        if payload.get("forecast"):
            f = dict(payload["forecast"])
            f.setdefault("forecast_id", uuid.uuid4().hex)
            f.setdefault("project_id", project_id)
            f.setdefault("confidence", "medium")
            f.setdefault("created_at", now)
            store.add_forecast(f, actor)
            forecast_id = f["forecast_id"]
        _write_generated(_root(), _canvas_filename(store, project_id),
                         render_canvas(store, project_id),
                         CANVAS_BEGIN, CANVAS_END)
    finally:
        store.close()
    _scaffold_progress_page(_root())
    if kv.get("out-json"):
        _print_json({"project_id": project_id, "task_ids": task_ids,
                     "forecast_id": forecast_id})
        return 0
    _out("started project %s (%d task(s), %s)"
         % (project_id, len(task_ids),
            "1 forecast" if forecast_id else "no forecast"))
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _forecast_lines(forecast, prior_count):
    """The D-1 rendering every forecast display (status, forecast show)
    shares: ranges plus confidence plus next-reforecast-event, NEVER a
    point (references/forecasting.md's one law). `prior_count` is the
    caller's own row-derived count (forecast show wants it; status does
    not print it, so callers pass None to omit that line)."""
    lines = [
        "  duration: %s to %s (likely %s)"
        % (forecast.get("minimum_duration") or "?",
           forecast.get("maximum_duration") or "?",
           forecast.get("likely_duration") or "?"),
        "  token range: input %s, output %s, total %s"
        % (forecast.get("input_token_range") or "?",
           forecast.get("output_token_range") or "?",
           forecast.get("effective_total_token_range") or "?"),
        "  confidence: %s" % forecast.get("confidence"),
        "  next reforecast: %s"
        % (forecast.get("next_reforecast_event") or "(not stated)"),
    ]
    if prior_count is not None:
        lines.append("  prior forecasts: %d" % prior_count)
    return lines


def cmd_status(argv):
    _pos, kv = _parse(argv, ("project-id", "json", "raw", "history"),
                       wants_value=("project-id", "history"))
    usage = "usage: status --project-id ID [--json] [--raw] [--history N]"
    project_id = _require(kv, "project-id", usage)
    # Text output is local display (raw=True, always: see the module
    # docstring). --json is the export surface and stays redacted unless
    # --raw is also given, exactly like bm_store.py's own dump --raw.
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    history_n = None
    if kv.get("history"):
        try:
            history_n = int(kv["history"])
        except ValueError:
            _err(usage)
            _err("bm_project: --history must be a whole number, got %r"
                 % kv["history"])
            return 2
        if history_n < 0:
            _err(usage)
            _err("bm_project: --history must be zero or a positive number")
            return 2
    # READ-ONLY FIX (2026-08-11, codex-audit-split-2026-08-10-night.md HIGH
    # #1 for tools/bm_effects.py): this opened a WRITABLE Store, so a status
    # check against a database one schema version behind migrated it,
    # exactly the bm_project.py status --project-id nosuch reproduction
    # tools/test_bm_effects.py's own TestPurityUnderAStoreThatIsBehind
    # docstring cites. Every store method this command calls (get_project,
    # list_tasks by way of _tasks_by_state, latest_forecast, list_forecasts,
    # list_alerts, list_attribution) exists on bm_store.ReadOnlyStore, so
    # routing here is a plain swap (contrast bm_learn.py lookup and
    # bm_packs.py stakes, which cannot be routed the same way because the
    # store methods they call are Store-only; see those files' own
    # comments).
    store = _read_store()
    try:
        project = store.get_project(project_id, raw=want_raw)
        if project is None:
            _err("bm_project: no project %r" % project_id)
            return 1
        by_state = _tasks_by_state(store, project_id, raw=want_raw)
        forecast = store.latest_forecast(project_id, raw=want_raw)
        forecast_count = len(store.list_forecasts(project_id, raw=want_raw))
        alerts = _severity_sorted(store.list_alerts(resolved=False,
                                                     raw=want_raw))
        history = (store.list_attribution(project_id, limit=history_n,
                                          raw=want_raw)
                  if history_n is not None else None)
    finally:
        store.close()
    prior_forecasts = max(forecast_count - 1, 0)
    if kv.get("json"):
        out = {
            "project": project,
            "tasks_by_state": by_state,
            "latest_forecast": forecast,
            "prior_forecast_count": prior_forecasts,
            "unresolved_alerts": alerts,
        }
        if history is not None:
            out["attribution_history"] = history
        _print_json(out)
        return 0
    _out("project %s: %s" % (project.get("project_id"), project.get("name")))
    _out("status=%s phase=%s" % (project.get("status") or "(none)",
                                 project.get("phase") or "(none)"))
    _out("open tasks by state:")
    open_total = 0
    for state in S.STATES:
        if state == TERMINAL_STATE:
            continue
        n = len(by_state[state])
        if n:
            _out("  %s: %d" % (state, n))
            open_total += n
    if not open_total:
        _out("  (none open)")
    closed = len(by_state[TERMINAL_STATE])
    if closed:
        _out("closed: %d" % closed)
    # D-1: ranges plus confidence plus next-reforecast-event, never a
    # point; a one-line note when the project has never been forecast
    # (the section is absent, not a lie dressed up as an empty table).
    if forecast is None:
        _out("latest forecast: (none yet)")
    else:
        _out("latest forecast: %s" % forecast.get("forecast_id"))
        for line in _forecast_lines(forecast, None):
            _out(line)
    # D-1: unresolved alerts, severity-ordered (most urgent first).
    _out("unresolved alerts (store-wide; alerts carry no project_id): %d"
         % len(alerts))
    for a in alerts:
        _out("  - %s [%s] %s%s" % (
            a.get("alert_id"), a.get("severity"), a.get("message"),
            " (needs a person)" if a.get("requires_human") else ""))
    # D-2: --history N prints the last N attribution events. Omitted
    # entirely when --history was not passed, so a founder who never asked
    # for the audit trail never sees it.
    if history is not None:
        _out("attribution history (showing %d):" % len(history))
        for ev in history:
            _out("  - %s: %s by %s/%s"
                % (ev.get("timestamp"), ev.get("event_type"),
                   ev.get("actor_type"), ev.get("actor_name")))
    return 0


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

def cmd_next(argv):
    _pos, kv = _parse(argv, ("project-id", "json", "raw"),
                       wants_value=("project-id",))
    usage = "usage: next --project-id ID [--json] [--raw]"
    project_id = _require(kv, "project-id", usage)
    # Same raw/json split cmd_status uses: text output is local display
    # (always raw=True); --json is the export surface and stays redacted
    # unless --raw is also given. See this file's module docstring.
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    # READ-ONLY FIX (2026-08-11, codex-audit-split-2026-08-10-night.md HIGH
    # #1 for tools/bm_effects.py): same defect and same fix as cmd_status
    # above. list_tasks exists on bm_store.ReadOnlyStore, so this is also a
    # plain swap.
    store = _read_store()
    try:
        # 'ready' means, in the canonical protocol's own words (section 1,
        # state 2), "everything the task depends on is satisfied; it can be
        # picked up." Filtering on that state via the query parameter (not
        # by reading depends_on or status back out of a row) is what keeps
        # this correct regardless of redaction.
        candidates = store.list_tasks(project_id, status="ready", raw=want_raw)
    finally:
        store.close()
    # C6 (release-closure loop2 refuter fixes): tasks carry no created_at,
    # so "earliest created" was never a real comparison this code could
    # make -- it was task_id order, a random hex uuid with no relationship
    # to when the task was added. list_tasks itself now orders by sqlite's
    # own rowid (insertion order, see its docstring), and Python's sorted()
    # is stable, so ties below keep that insertion order: the tie break
    # actually performed is "priority, then whichever was added first",
    # which is exactly what the WHY text below now says.
    ranked = sorted(candidates, key=lambda t: _priority_key(t.get("priority")))
    picked = ranked[0] if ranked else None
    if kv.get("json"):
        _print_json({"candidate_count": len(candidates), "picked": picked})
        return 0
    if picked is None:
        # M3. The old message was true and was a dead end: a person who had
        # just created their first task asked what to do next and was told
        # only that nothing is ready, with no way out named. `next` reading
        # only 'ready' is CORRECT and is not what changed here, because the
        # protocol defines that state as "everything the task depends on is
        # satisfied; it can be picked up". What was missing is that a task is
        # BORN 'planned', nothing moves it, and nothing said so. Reported,
        # never auto-advanced: moving somebody's task for them would decide
        # that its dependencies are satisfied, which is exactly the judgement
        # this state exists to record.
        waiting = []
        store = _read_store()
        try:
            for state in BIRTH_STATES[:1] + ("blocked",):
                waiting.extend(store.list_tasks(project_id, status=state,
                                                raw=want_raw))
        finally:
            store.close()
        _out("no recommended next task: 0 task(s) currently in state "
             "'ready' for project %s" % project_id)
        if waiting:
            planned = [t for t in waiting if t.get("status") == "planned"]
            blocked = [t for t in waiting if t.get("status") == "blocked"]
            if planned:
                first = planned[0]
                _out("BUT %d task(s) are still 'planned', which means nobody "
                     "has said their dependencies are satisfied yet. To make "
                     "one pickable:" % len(planned))
                _out("  task transition --task-id %s --to ready --reason "
                     "'<why it can start>'" % first.get("task_id"))
            if blocked:
                _out("%d task(s) are 'blocked' and need whatever blocked them "
                     "cleared first." % len(blocked))
        return 0
    _out("next: %s - %s" % (picked.get("task_id"), picked.get("title")))
    _out("WHY: %d candidate(s) in state 'ready' (dependency-satisfied per "
         "the protocol's own definition of that state); picked by highest "
         "priority, then whichever was added first, as the tie break."
         % len(candidates))
    return 0


# ---------------------------------------------------------------------------
# task add / task start / task transition
# ---------------------------------------------------------------------------

_TASK_ADD_FLAGS = (
    "project-id", "task-id", "title", "user-value", "reason", "priority",
    "depends-on", "status", "assigned-human", "assigned-runtime",
    "assigned-model-profile", "assignment-reason", "phase", "json",
    "out-json") + _ACTOR_FLAGS


def _task_add_usage():
    return ("usage: task add --project-id ID --title T [--task-id ID] "
            "[--user-value V] [--reason R] [--priority P] "
            "[--depends-on id,id] [--status S(default planned)] "
            "[--assigned-human H] [--assigned-runtime R] "
            "[--assigned-model-profile M] [--assignment-reason R] "
            "[--phase P] "
            "[--json PATH] [--actor-type human|model] --actor-name NAME "
            "[--session-id SID] [--out-json]")


def cmd_task_add(argv):
    _pos, kv = _parse(argv, _TASK_ADD_FLAGS, wants_value=(
        "project-id", "task-id", "title", "user-value", "reason",
        "priority", "depends-on", "status", "assigned-human",
        "assigned-runtime", "assigned-model-profile", "assignment-reason",
        "phase", "json") + _ACTOR_FLAGS)
    usage = _task_add_usage()
    project_id = _require(kv, "project-id", usage)
    actor = _actor(kv, usage)
    task = {}
    if kv.get("json"):
        try:
            with io.open(kv["json"], encoding="utf-8") as fh:
                task = json.load(fh)
        except (OSError, ValueError) as e:
            _err("bm_project: could not read --json payload %r: %s"
                 % (kv["json"], e))
            return 2
        if not isinstance(task, dict):
            _err("bm_project: --json payload must be a JSON object")
            return 2
        task = dict(task)
    title = kv.get("title") or task.get("title")
    if not title:
        _err(usage)
        _err("bm_project: --title is required (or provide it via --json)")
        return 2
    task["title"] = title
    task["project_id"] = project_id
    task.setdefault("task_id", kv.get("task-id") or uuid.uuid4().hex)
    task["status"] = kv.get("status") or task.get("status") or "planned"
    # M4, and the reason it is refused HERE rather than in deliver. The
    # transition law was never the hole: `task transition --to verified` from
    # `planned` is refused correctly, and TestRefusals proves it. The hole was
    # that BIRTH chose a state, so a task could start past every gate instead
    # of walking through them. Reproduced in a throwaway root before this
    # guard existed, three commands total: `task add --status closed` printed
    # "added task t1", and `deliver` then printed "delivered px: all 1 task(s)
    # closed" and exited 0, for a project with no goal, no acceptance check,
    # no review and no evidence. deliver's own rule was doing its job on the
    # evidence it had; the lie was upstream, so the guard is upstream, in the
    # one place every caller of task creation already routes through.
    #
    # `ready` is legal alongside `planned` on purpose: `next` serves only
    # ready tasks, so refusing it would break the guided flow this same work
    # is trying to make real (M3).
    if task["status"] not in BIRTH_STATES:
        _err("bm_project: refused: a task cannot be created in %r. A task is "
             "born in one of: %s, and reaches any later state by walking the "
             "lifecycle, because a task that starts past a stage hides which "
             "evidence and review requirements were met at it."
             % (task["status"], ", ".join(BIRTH_STATES)))
        return 1
    for flag, field in (
            ("user-value", "user_value"), ("reason", "reason"),
            ("priority", "priority"), ("assigned-human", "assigned_human"),
            ("assigned-runtime", "assigned_runtime"),
            ("assigned-model-profile", "assigned_model_profile"),
            ("assignment-reason", "assignment_reason"),
            # Schema 18: the phase this piece belongs to. Optional, and an
            # absent flag stays absent rather than inheriting the
            # project's current phase, which would be a guess.
            ("phase", "phase")):
        if kv.get(flag):
            task[field] = kv[flag]
    if kv.get("depends-on"):
        task["depends_on"] = _csv(kv["depends-on"])
    store = _store()
    try:
        task_id = store.create_task(task, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"task_id": task_id})
        return 0
    _out("added task %s" % task_id)
    return 0


_TASK_TRANSITION_FLAGS = ("task-id", "to", "reason", "json",
                          "out-json") + _ACTOR_FLAGS


def cmd_task_transition(argv):
    _pos, kv = _parse(argv, _TASK_TRANSITION_FLAGS, wants_value=(
        "task-id", "to", "reason") + _ACTOR_FLAGS)
    usage = ("usage: task transition --task-id ID --to STATE --reason R "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    task_id = _require(kv, "task-id", usage)
    new_status = _require(kv, "to", usage)
    reason = _require(kv, "reason", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        status = store.transition_task(task_id, new_status, reason, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"task_id": task_id, "status": status})
        return 0
    _out("task %s -> %s" % (task_id, status))
    return 0


def cmd_task_start(argv):
    """Convenience sugar: begin work on a task. transition_task itself
    decides legality (ready->active or blocked->active are the only legal
    arrivals at 'active'); this wrapper only fixes the destination."""
    _pos, kv = _parse(argv, ("task-id", "reason", "out-json") + _ACTOR_FLAGS,
                      wants_value=("task-id", "reason") + _ACTOR_FLAGS)
    usage = ("usage: task start --task-id ID --reason R "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    task_id = _require(kv, "task-id", usage)
    reason = _require(kv, "reason", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        status = store.transition_task(task_id, "active", reason, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"task_id": task_id, "status": status})
        return 0
    _out("task %s -> %s" % (task_id, status))
    return 0


TASK_COMMANDS = {
    "add": cmd_task_add,
    "start": cmd_task_start,
    "transition": cmd_task_transition,
}


def cmd_task(argv):
    if not argv or argv[0] not in TASK_COMMANDS:
        _err("usage: task <add|start|transition> ... "
             "(known: %s)" % ", ".join(sorted(TASK_COMMANDS)))
        return 2
    return TASK_COMMANDS[argv[0]](argv[1:])


# ---------------------------------------------------------------------------
# forecast add / forecast show (D-1, Loop 5 design)
# ---------------------------------------------------------------------------

_FORECAST_ADD_FLAGS = (
    "project-id", "forecast-id", "min-duration", "likely-duration",
    "max-duration", "input-token-range", "output-token-range",
    "effective-token-range", "confidence", "basis", "assumptions",
    "unknowns", "next-reforecast-event", "out-json") + _ACTOR_FLAGS


def _forecast_add_usage():
    return ("usage: forecast add --project-id ID "
            "--min-duration D --likely-duration D --max-duration D "
            "(--input-token-range R | --output-token-range R | "
            "--effective-token-range R, at least one) "
            "--confidence low|medium|high [--basis TEXT] "
            "[--assumptions a,b] [--unknowns a,b] "
            "[--next-reforecast-event E] [--forecast-id ID] "
            "[--actor-type human|model] --actor-name NAME "
            "[--session-id SID] [--out-json]")


def cmd_forecast_add(argv):
    _pos, kv = _parse(argv, _FORECAST_ADD_FLAGS, wants_value=(
        "project-id", "forecast-id", "min-duration", "likely-duration",
        "max-duration", "input-token-range", "output-token-range",
        "effective-token-range", "confidence", "basis", "assumptions",
        "unknowns", "next-reforecast-event") + _ACTOR_FLAGS)
    usage = _forecast_add_usage()
    project_id = _require(kv, "project-id", usage)
    min_d = _require(kv, "min-duration", usage)
    likely_d = _require(kv, "likely-duration", usage)
    max_d = _require(kv, "max-duration", usage)
    confidence = _require(kv, "confidence", usage)
    token_ranges = {
        "input_token_range": kv.get("input-token-range") or "",
        "output_token_range": kv.get("output-token-range") or "",
        "effective_total_token_range": kv.get("effective-token-range") or "",
    }
    if not any(token_ranges.values()):
        _err(usage)
        _err("bm_project: at least one token-range flag is required "
             "(--input-token-range, --output-token-range, or "
             "--effective-token-range): a work budget with no stated "
             "token range is not a forecast (references/forecasting.md).")
        return 2
    basis = kv.get("basis") or ""
    # The forecasting rule, quoted in plain words (references/
    # forecasting.md, "The one law: ranges, never points"): every estimate
    # carries a minimum and likely duration, a token range, confidence,
    # assumptions, known unknowns, and the next reforecast point; "a bare
    # date or a bare number is a false promise and is never emitted." Three
    # identical duration values with nothing said about why is exactly
    # that bare number wearing a range's clothes, so it is refused unless
    # --basis explains the certainty.
    if min_d == likely_d == max_d and not basis:
        _err("bm_project: refused: minimum, likely, and maximum duration "
             "are all %r with no stated --basis. That is a single point "
             "estimate, not a forecast: the forecasting rule is ranges, "
             "never points, because a bare number is a false promise "
             "unless you can say why it is certain; a bare number is "
             "emitted only when you record that reason with --basis "
             "(references/forecasting.md). Give three different values "
             "for a real range, or pass --basis explaining why this one "
             "duration is certain." % (min_d,))
        return 1
    actor = _actor(kv, usage)
    forecast = {
        "forecast_id": kv.get("forecast-id") or uuid.uuid4().hex,
        "project_id": project_id,
        "minimum_duration": min_d,
        "likely_duration": likely_d,
        "maximum_duration": max_d,
        "confidence": confidence,
        "calculation_basis": basis,
        "next_reforecast_event": kv.get("next-reforecast-event") or "",
        "assumptions": _csv(kv.get("assumptions")),
        "unknowns": _csv(kv.get("unknowns")),
        "created_at": bs.now_iso(),
    }
    forecast.update(token_ranges)
    store = _store()
    try:
        forecast_id = store.add_forecast(forecast, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"forecast_id": forecast_id})
        return 0
    _out("added forecast %s" % forecast_id)
    return 0


def cmd_forecast_show(argv):
    _pos, kv = _parse(argv, ("project-id", "json", "raw"),
                       wants_value=("project-id",))
    usage = "usage: forecast show --project-id ID [--json] [--raw]"
    project_id = _require(kv, "project-id", usage)
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    # READ-ONLY FIX (2026-08-11, codex-audit-split-2026-08-10-night.md HIGH
    # #1 for tools/bm_effects.py): same defect and same fix as cmd_status
    # above. latest_forecast and list_forecasts both exist on
    # bm_store.ReadOnlyStore, so this is also a plain swap.
    store = _read_store()
    try:
        forecast = store.latest_forecast(project_id, raw=want_raw)
        total = len(store.list_forecasts(project_id, raw=want_raw))
    finally:
        store.close()
    prior = max(total - 1, 0)
    if kv.get("json"):
        _print_json({"latest_forecast": forecast,
                     "prior_forecast_count": prior})
        return 0
    if forecast is None:
        _out("no forecast recorded yet for project %s" % project_id)
        return 0
    _out("forecast %s" % forecast.get("forecast_id"))
    for line in _forecast_lines(forecast, prior):
        _out(line)
    return 0


FORECAST_COMMANDS = {
    "add": cmd_forecast_add,
    "show": cmd_forecast_show,
}


def cmd_forecast(argv):
    if not argv or argv[0] not in FORECAST_COMMANDS:
        _err("usage: forecast <add|show> ... "
             "(known: %s)" % ", ".join(sorted(FORECAST_COMMANDS)))
        return 2
    return FORECAST_COMMANDS[argv[0]](argv[1:])


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

_REVIEW_FLAGS = ("project-id", "kind", "ref", "note", "to", "reason",
                 "criterion-id", "out-json") + _ACTOR_FLAGS


def cmd_review(argv):
    pos, kv = _parse(argv, _REVIEW_FLAGS, wants_value=(
        "project-id", "kind", "ref", "note", "to", "reason",
        "criterion-id") + _ACTOR_FLAGS)
    usage = ("usage: review <task_id> --project-id ID [--kind K] [--ref R] "
             "[--note N] [--to STATE(default verified)] --reason R "
             "[--criterion-id ID] "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    if not pos:
        _err(usage)
        _err("bm_project: review needs a task id")
        return 2
    task_id = pos[0]
    project_id = _require(kv, "project-id", usage)
    reason = _require(kv, "reason", usage)
    actor = _actor(kv, usage)
    new_status = kv.get("to") or "verified"
    evidence = {
        "evidence_id": uuid.uuid4().hex,
        "subject_type": "task",
        "subject_id": task_id,
        "kind": kv.get("kind") or "",
        "ref": kv.get("ref") or "",
        "note": kv.get("note") or "",
        # Schema 20, R1.2: the stable id (schema.criterion_id_for_check)
        # of the acceptance_checks entry this evidence satisfies. Omitted
        # or empty means not linked, a real and honest value (see
        # Store.add_evidence's own docstring); a value that names no real
        # entry on this task is refused by Store._verify_criterion_id,
        # never guessed at or silently dropped here.
        "criterion_id": kv.get("criterion-id") or "",
        "created_at": bs.now_iso(),
    }
    store = _store()
    try:
        # ONE composite call (C1, release-closure loop2 refuter fixes):
        # review_task files the evidence AND runs the transition in a
        # SINGLE store transaction, so a transition schema.transition()
        # refuses leaves no orphan evidence row behind. This used to be
        # two separate calls (add_evidence, then transition_task), each
        # its own transaction, so a refused transition still left the
        # evidence from the first call sitting on disk.
        evidence_id, status, acceptance_gap = store.review_task(
            task_id, project_id, evidence, new_status, reason, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"task_id": task_id, "evidence_id": evidence_id,
                     "status": status, "acceptance_gap": acceptance_gap})
        return 0
    _out("reviewed task %s: evidence %s recorded, task -> %s"
         % (task_id, evidence_id, status))
    # M5: acceptance_checks enforce nothing unless the verdict actually
    # says so when nothing examined one. Printed, never silently folded
    # into the ordinary line above, so a review that met no acceptance
    # check still reads unmistakably differently from one that did.
    if acceptance_gap:
        _out("WARNING: %s" % acceptance_gap)
    return 0


# ---------------------------------------------------------------------------
# receipt add / receipt list (TK11, F4 capability receipts, schema 20)
# ---------------------------------------------------------------------------

_RECEIPT_ADD_FLAGS = (
    "project-id", "task-id", "receipt-id", "capability-name",
    "capability-version", "executor-identity", "task-description",
    "inputs", "permissions-declared", "claimed-output",
    "changed-artifacts", "raw-evidence", "verification-state",
    "verification-evidence", "omissions", "out-json") + _ACTOR_FLAGS


def _receipt_add_usage():
    return ("usage: receipt add --project-id ID --capability-name NAME "
            "--task-description DESC "
            "--verification-state verified|failed|no_data "
            "[--task-id ID] [--capability-version V] "
            "[--executor-identity E] [--inputs a,b] "
            "[--permissions-declared a,b] [--claimed-output TEXT] "
            "[--changed-artifacts a,b] [--raw-evidence TEXT] "
            "[--verification-evidence TEXT] [--omissions a,b] "
            "[--receipt-id ID] "
            "[--actor-type human|model] --actor-name NAME "
            "[--session-id SID] [--out-json]")


def cmd_receipt_add(argv):
    _pos, kv = _parse(argv, _RECEIPT_ADD_FLAGS, wants_value=(
        "project-id", "task-id", "receipt-id", "capability-name",
        "capability-version", "executor-identity", "task-description",
        "inputs", "permissions-declared", "claimed-output",
        "changed-artifacts", "raw-evidence", "verification-state",
        "verification-evidence", "omissions") + _ACTOR_FLAGS)
    usage = _receipt_add_usage()
    project_id = _require(kv, "project-id", usage)
    capability_name = _require(kv, "capability-name", usage)
    task_description = _require(kv, "task-description", usage)
    # Store.add_capability_receipt refuses a value outside the three
    # honest states (T5: "no_data is a first-class value, not a null")
    # before the INSERT reaches the table's own CHECK constraint; this
    # file never restates that check, only requires the flag be given at
    # all, the same house rule cmd_forecast_add follows for --confidence.
    verification_state = _require(kv, "verification-state", usage)
    actor = _actor(kv, usage)
    receipt = {
        "receipt_id": kv.get("receipt-id") or uuid.uuid4().hex,
        "project_id": project_id,
        "task_id": kv.get("task-id") or None,
        "capability_name": capability_name,
        "capability_version": kv.get("capability-version") or "",
        "executor_identity": kv.get("executor-identity") or "",
        "task_description": task_description,
        "inputs": _csv(kv.get("inputs")),
        "permissions_declared": _csv(kv.get("permissions-declared")),
        "claimed_output": kv.get("claimed-output") or "",
        "changed_artifacts": _csv(kv.get("changed-artifacts")),
        "raw_evidence": kv.get("raw-evidence") or "",
        "verification_state": verification_state,
        "verification_evidence": kv.get("verification-evidence") or "",
        "omissions": _csv(kv.get("omissions")),
        "created_at": bs.now_iso(),
    }
    store = _store()
    try:
        receipt_id = store.add_capability_receipt(receipt, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"receipt_id": receipt_id})
        return 0
    _out("added capability receipt %s" % receipt_id)
    return 0


def cmd_receipt_list(argv):
    _pos, kv = _parse(argv, ("project-id", "task-id", "capability-name",
                             "json", "raw"),
                       wants_value=("project-id", "task-id",
                                    "capability-name"))
    usage = ("usage: receipt list --project-id ID [--task-id ID] "
             "[--capability-name NAME] [--json] [--raw]")
    project_id = _require(kv, "project-id", usage)
    # Same raw/json split every other list/show command in this file
    # uses: text output is local display (always raw=True); --json is the
    # export surface and stays redacted unless --raw is also given (see
    # this file's module docstring).
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    store = _read_store()
    try:
        receipts = store.list_capability_receipts(
            project_id, task_id=kv.get("task-id"),
            capability_name=kv.get("capability-name"), raw=want_raw)
    finally:
        store.close()
    if kv.get("json"):
        _print_json({"receipts": receipts})
        return 0
    # House receipt discipline: a list on a project with no receipts says
    # so in plain words, never silence.
    if not receipts:
        _out("no capability receipts recorded for project %s" % project_id)
        return 0
    _out("capability receipts for project %s (%d):"
         % (project_id, len(receipts)))
    for r in receipts:
        _out("  - %s [%s] %s: %s"
             % (r.get("receipt_id"), r.get("verification_state"),
                r.get("capability_name"), r.get("task_description")))
    return 0


RECEIPT_COMMANDS = {
    "add": cmd_receipt_add,
    "list": cmd_receipt_list,
}


def cmd_receipt(argv):
    if not argv or argv[0] not in RECEIPT_COMMANDS:
        _err("usage: receipt <add|list> ... "
             "(known: %s)" % ", ".join(sorted(RECEIPT_COMMANDS)))
        return 2
    return RECEIPT_COMMANDS[argv[0]](argv[1:])


# ---------------------------------------------------------------------------
# alert raise / alert resolve / alert list (D-1, Loop 5 design)
# ---------------------------------------------------------------------------

_ALERT_RAISE_FLAGS = (
    "project-id", "alert-id", "severity", "category", "message", "why",
    "recommended-action", "requires-human", "out-json") + _ACTOR_FLAGS


def cmd_alert_raise(argv):
    _pos, kv = _parse(argv, _ALERT_RAISE_FLAGS, wants_value=(
        "project-id", "alert-id", "severity", "category", "message", "why",
        "recommended-action") + _ACTOR_FLAGS)
    usage = ("usage: alert raise --project-id ID "
             "--severity info|attention|high|critical --message TEXT "
             "[--category C] [--why TEXT] [--recommended-action TEXT] "
             "[--requires-human] [--alert-id ID] "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    project_id = _require(kv, "project-id", usage)
    severity = _require(kv, "severity", usage)
    message = _require(kv, "message", usage)
    actor = _actor(kv, usage)
    alert = {
        "alert_id": kv.get("alert-id") or uuid.uuid4().hex,
        "severity": severity,
        "category": kv.get("category") or "",
        "message": message,
        "why_it_matters": kv.get("why") or "",
        "recommended_action": kv.get("recommended-action") or "",
        "requires_human": bool(kv.get("requires-human")),
        "created_at": bs.now_iso(),
        "resolved_at": None,
    }
    store = _store()
    try:
        alert_id = store.raise_alert(alert, project_id, actor)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"alert_id": alert_id})
        return 0
    _out("raised alert %s [%s]" % (alert_id, severity))
    return 0


_ALERT_RESOLVE_FLAGS = ("project-id", "reason", "out-json") + _ACTOR_FLAGS


def cmd_alert_resolve(argv):
    pos, kv = _parse(argv, _ALERT_RESOLVE_FLAGS, wants_value=(
        "project-id", "reason") + _ACTOR_FLAGS)
    usage = ("usage: alert resolve <alert_id> --project-id ID --reason R "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    if not pos:
        _err(usage)
        _err("bm_project: alert resolve needs an alert id")
        return 2
    alert_id = pos[0]
    project_id = _require(kv, "project-id", usage)
    reason = _require(kv, "reason", usage)
    actor = _actor(kv, usage)
    store = _store()
    try:
        resolved_at = store.resolve_alert(alert_id, project_id, actor,
                                          reason=reason)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"alert_id": alert_id, "resolved_at": resolved_at})
        return 0
    _out("resolved alert %s" % alert_id)
    return 0


def cmd_alert_list(argv):
    _pos, kv = _parse(argv, ("all", "json", "raw"), wants_value=())
    want_raw = True if not kv.get("json") else bool(kv.get("raw"))
    store = _read_store()
    try:
        alerts = store.list_alerts(
            resolved=(None if kv.get("all") else False), raw=want_raw)
    finally:
        store.close()
    alerts = _severity_sorted(alerts)
    if kv.get("json"):
        _print_json({"alerts": alerts})
        return 0
    if not alerts:
        _out("no alerts recorded" if kv.get("all") else
             "no unresolved alerts")
        return 0
    for a in alerts:
        _out("%s [%s]%s %s%s"
             % (a.get("alert_id"), a.get("severity"),
                " RESOLVED" if a.get("resolved_at") else "",
                a.get("message"),
                " (needs a person)" if a.get("requires_human") else ""))
    return 0


ALERT_COMMANDS = {
    "raise": cmd_alert_raise,
    "resolve": cmd_alert_resolve,
    "list": cmd_alert_list,
}


def cmd_alert(argv):
    if not argv or argv[0] not in ALERT_COMMANDS:
        _err("usage: alert <raise|resolve|list> ... "
             "(known: %s)" % ", ".join(sorted(ALERT_COMMANDS)))
        return 2
    return ALERT_COMMANDS[argv[0]](argv[1:])


# ---------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------

# M2: deliver's own two checks above (nonzero tasks, every task at the
# terminal state) both pass for a project that never had a goal, never had
# a single piece of evidence filed, and never had a task actually pass
# review. A task reaches 'closed' by walking LEGAL_TRANSITIONS one legal
# move at a time, and the bare `task transition` command performs those
# moves with no evidence required at all: only `review` files evidence
# (bm_project.py's ONE writer of the evidence table), and nothing forces a
# caller to use it rather than `task transition --to verified` directly.
# REPRODUCED in a throwaway root, after M4 closed the birth-state hole:
# start a project with no --goal, `task add` (born 'planned', legal),
# then `task transition` it straight through ready, active, 'awaiting
# review', verified, accepted, delivered, monitored, closed, six
# transitions, zero evidence rows, zero review. `deliver --project-id px`
# still printed "delivered px: all 1 task(s) closed" and exited 0. A
# status-based check ("has any task ever reached 'verified' or later")
# does NOT catch this: `task transition --to verified` leaves the task
# sitting in that very state, so by the time deliver looks the status
# lies exactly like the missing evidence does. Evidence, not status, is
# the only trustworthy signal here, because only `review` ever writes it.


def _delivery_holes(store, project, tasks):
    """Every reason `project` is not a real delivery, named rather than
    merely refused. Returns a list of hole descriptions, empty when there
    are none. Checked together, not one refusal at a time (loops2's own
    C4 fix nearby is the same shape: report the whole gap in one call)."""
    holes = []
    if not (project.get("goal") or "").strip():
        holes.append("no goal is set on the project")
    has_evidence = bool(store.list_evidence("project", project["project_id"]))
    if not has_evidence:
        for task in tasks:
            if store.list_evidence("task", task.get("task_id")):
                has_evidence = True
                break
    if not has_evidence:
        holes.append("no build evidence and no review are on record: "
                     "only `review` files evidence, and nothing ever "
                     "called it for this project or any of its tasks")
    return holes


def cmd_deliver(argv):
    _pos, kv = _parse(argv, ("project-id", "partial", "raw", "out-json"),
                       wants_value=("project-id",))
    usage = ("usage: deliver --project-id ID [--partial] [--raw] "
             "[--out-json]")
    project_id = _require(kv, "project-id", usage)
    # Same contract cmd_export already uses: default withheld, --raw for
    # the project's own owner (DELIVERY-PACKET.md is an export, see this
    # file's module docstring and render_delivery_packet's own docstring).
    want_raw = bool(kv.get("raw"))
    store = _store()
    try:
        project = store.get_project(project_id)
        if project is None:
            _err("bm_project: no project %r" % project_id)
            return 1
        tasks = store.list_tasks(project_id)
        total = len(tasks)
        if total == 0:
            # C4 (release-closure loop2 refuter fixes): --partial means
            # "some tasks are not yet closed", not "there is nothing to
            # deliver at all". A project with zero tasks refuses either
            # way, so an empty delivery packet can never pass as a real
            # one just because --partial was on the command line.
            _err("cannot deliver %s: the project has zero tasks; add at "
                 "least one task before delivering." % project_id)
            return 1
        closed = len([t for t in tasks if t.get("status") == TERMINAL_STATE])
        non_terminal = total - closed
        if non_terminal > 0 and not kv.get("partial"):
            _err("cannot deliver: %d of %d task(s) have not reached the "
                 "terminal state (%r); pass --partial to deliver anyway, "
                 "or finish those tasks first."
                 % (non_terminal, total, TERMINAL_STATE))
            return 1
        holes = _delivery_holes(store, project, tasks)
        # M2 (2026-08-20): a hollow project may never be certified as a
        # SUCCESS. An explicit --partial is a different act: the human is
        # declaring the delivery incomplete, so it proceeds, and the holes
        # are printed so a partial can never read as silently clean either.
        if holes and not kv.get("partial"):
            _err("cannot deliver %s: %s" % (project_id, "; ".join(holes)))
            return 1
        if holes:
            _err("delivering PARTIALLY with holes on record: %s"
                 % "; ".join(holes))
        text = render_delivery_packet(store, project_id, raw=want_raw)
        _write_generated(_root(), _packet_filename(store, project_id), text,
                         DELIVERY_BEGIN, DELIVERY_END)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"project_id": project_id, "total_tasks": total,
                     "closed_tasks": closed, "partial": bool(non_terminal)})
        return 0
    if non_terminal:
        _out("delivered %s PARTIALLY: %d of %d task(s) not yet closed"
             % (project_id, non_terminal, total))
    else:
        _out("delivered %s: all %d task(s) closed" % (project_id, total))
    return 0


# ---------------------------------------------------------------------------
# export / purge (WP-H, docs/superpowers/specs/2026-08-01-loop6-security-
# closure-design.md, D-3)
# ---------------------------------------------------------------------------

# list_attribution's own default caps a terminal --history read at 50 rows;
# export needs the WHOLE trail ("every row the store holds for that
# project"), so this passes a limit no real project's attribution stream
# will reach rather than widening list_attribution's own contract for one
# caller that wants everything.
_EXPORT_ATTRIBUTION_LIMIT = 1000000


def _project_alert_ids(store, project_id):
    """The set of alert ids `project_id`'s own 'alert.raised' attribution
    events name (raise_alert's own docstring: alerts carry no project_id
    column of their own, so this attribution trail is the only place that
    scope is recorded). raw=True here decides SCOPE, never disclosure: the
    caller still renders each matched alert row through
    store.list_alerts(raw=want_raw), so nothing this function reads
    unredacted ever reaches an export payload or the terminal."""
    events = store.list_attribution(
        project_id, limit=_EXPORT_ATTRIBUTION_LIMIT, raw=True)
    return {e.get("evidence_ref") for e in events
            if e.get("event_type") == "alert.raised" and e.get("evidence_ref")}


_EXPORT_FLAGS = ("project-id", "raw", "out")


def cmd_export(argv):
    _pos, kv = _parse(argv, _EXPORT_FLAGS, wants_value=("project-id", "out"))
    usage = "usage: export --project-id ID [--raw] [--out PATH]"
    project_id = _require(kv, "project-id", usage)
    want_raw = bool(kv.get("raw"))
    store = _store()
    try:
        project = store.get_project(project_id, raw=want_raw)
        if project is None:
            _err("bm_project: no project %r" % project_id)
            return 1
        tasks = store.list_tasks(project_id, raw=want_raw)
        alert_ids = _project_alert_ids(store, project_id)
        alerts = [a for a in store.list_alerts(resolved=None, raw=want_raw)
                  if a.get("alert_id") in alert_ids]
        # Evidence whose subject is the project itself, or one of its own
        # tasks (task_id is a safe, never-redacted column, see
        # _DUMP_SAFE_COLUMNS, so reading it back off `tasks` here is not a
        # second, wider disclosure than the row above already made).
        evidence = list(
            store.list_evidence("project", project_id, raw=want_raw))
        for task in tasks:
            evidence.extend(store.list_evidence(
                "task", task.get("task_id"), raw=want_raw))
        payload = {
            "project_id": project_id,
            "exported_at": bs.now_iso(),
            "raw": want_raw,
            "project": project,
            "tasks": tasks,
            "dependencies": store.list_dependencies(project_id, raw=want_raw),
            "forecasts": store.list_forecasts(project_id, raw=want_raw),
            "alerts": alerts,
            "evidence": evidence,
            "attribution": store.list_attribution(
                project_id, limit=_EXPORT_ATTRIBUTION_LIMIT, raw=want_raw),
        }
    finally:
        store.close()
    out_path = kv.get("out") or bs.safe_project_path(
        _root(), "EXPORT-%s.json" % project_id)
    try:
        with io.open(out_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True))
        # An export is one file holding everything the store knows about a
        # project, and with --raw that includes the prose every other
        # surface withholds. It is written FOR its owner, so it is owner
        # readable only, the same posture the consent config and the fence
        # tokens already take. Best effort: a filesystem that cannot do
        # modes is not a reason to refuse an export the caller asked for.
        bs._chmod_best_effort(out_path, 0o600)
    except OSError as e:
        _err("bm_project: could not write export to %s: %s" % (out_path, e))
        return 1
    _out("wrote export to %s" % out_path)
    return 0


_PURGE_FLAGS = ("project-id", "confirm", "out-json", "dry-run") + _ACTOR_FLAGS


def cmd_purge(argv):
    _pos, kv = _parse(argv, _PURGE_FLAGS, wants_value=(
        "project-id", "confirm") + _ACTOR_FLAGS)
    # --confirm is required for a real purge and deliberately NOT required
    # under --dry-run: the token stands between a founder and an
    # irreversible deletion, and a dry run deletes nothing. Requiring it
    # would train them to type the confirmation before seeing what it
    # confirms. --project-id stays required either way, because a preview
    # of "some project" is not a preview.
    usage = ("usage: purge --project-id ID (--confirm ID | --dry-run) "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    dry = bool(kv.get("dry-run"))
    project_id = _require(kv, "project-id", usage)
    confirm = kv.get("confirm", "") if dry else _require(kv, "confirm", usage)
    actor = _actor(kv, usage)
    prefix = "[dry-run] " if dry else ""
    store = _store()
    try:
        # Store.purge_project does the real work, atomically, and is the
        # one place the confirmation check and the refusal reasons live
        # (bad-confirmation, not-found): never restated here. Under
        # dry_run it runs the identical transaction and rolls it back, so
        # the counts below are the real purge's own, never a second
        # implementation's guess at them.
        removed = store.purge_project(project_id, actor, confirm,
                                      dry_run=dry)
    finally:
        store.close()
    if kv.get("out-json"):
        _print_json({"project_id": project_id, "removed": removed,
                     "dry_run": dry})
        return 0
    if dry:
        _out("%swould purge project %s; nothing was removed"
             % (prefix, project_id))
    else:
        _out("purged project %s" % project_id)
    _out("%s%s: %d task(s), %d dependency row(s), %d forecast(s), "
         "%d alert(s), %d evidence row(s), and the project record itself"
         % (prefix, "would remove" if dry else "removed",
            removed["tasks"], removed["dependencies"], removed["forecasts"],
            removed["alerts"], removed["evidence"]))
    # A6 fix (loop6 refuter findings): a task in another project can depend
    # on a task this purge just removed. That fallout is real but it is not
    # THIS project's own count, so it is named in its own plain sentence,
    # never folded into the "removed:" line above.
    cross = removed.get("cross_project_edges_removed") or []
    if cross:
        _out("%s%d prerequisite link(s) in other projects %s, "
             "because the work they pointed at no longer exists: %s"
             % (prefix, len(cross),
                "would also have to go" if dry else "also had to go",
                ", ".join(cross)))
    # A7 fix: an alert id that could not be safely attributed to this
    # project alone was left untouched rather than deleted; say so plainly
    # rather than silently dropping it from the removed count.
    skipped = removed.get("alerts_skipped") or []
    if skipped:
        _out("%s%d alert id(s) could not be safely attributed to this "
             "project alone and %s: %s"
             % (prefix, len(skipped),
                "would be left untouched rather than deleted" if dry
                else "were left untouched rather than deleted",
                ", ".join(skipped)))
    _out("%skept: the attribution trail (%s), the vault, and any "
         "generated file already on disk (CANVAS.md, DELIVERY-PACKET.md "
         "and the like); none of those are rows this store owns."
         % (prefix,
            "unchanged, and no purge entry was added because nothing was "
            "purged" if dry
            else "it now also carries one new entry naming this purge"))
    return 0


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "start": cmd_start,
    "status": cmd_status,
    "next": cmd_next,
    "task": cmd_task,
    "forecast": cmd_forecast,
    "alert": cmd_alert,
    "receipt": cmd_receipt,
    "review": cmd_review,
    "deliver": cmd_deliver,
    "export": cmd_export,
    "purge": cmd_purge,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(__doc__.strip())
        _out("")
        _out("commands: %s" % ", ".join(sorted(COMMANDS)))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("bm_project: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    try:
        return COMMANDS[cmd](argv[1:])
    except S.SchemaError as e:
        # The ten-state law refusing a move, or an invalid shape: reported
        # in schema.transition's OWN words, never restated here, and
        # counted as a refusal (exit 1), not a usage error.
        _err("bm_project: refused: %s" % e)
        return 1
    except bs.BMStoreError as e:
        # Covers OwnershipRefused and StaleIdentity: not-found, illegal
        # evidence, already-resolved, no-store, and the like.
        _err("bm_project: refused: %s" % e)
        return 1


def cli():
    """Console-script entry point; see bm_learn.py's cli() for why this
    exists beside main() rather than duplicating the sys.exit(main(...))
    line: a packaging entry point must take no arguments."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()

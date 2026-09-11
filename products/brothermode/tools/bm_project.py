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
  list                    every project id this folder holds, with its goal
                          (read only; R-1 persona dogfood 2026-09-07, the
                          id bm_lead.py points a founder at when a folder
                          holds more than one project)
  adopt                   the typed project record, inferred from what
                          this repository already says (A-prime amendment
                          1, docs/schema/outcome-contract-v1.json): reads
                          the tree, writes ONE outcome contract record,
                          asks AT MOST ONE question and only about a
                          field the repository could not answer, and
                          needs no store at all
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
  `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_store.py" init` first (never a
  bare tools/ path: the plugin installs these tools under the plugin root,
  not the user's own repository).

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
import re
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
# F-004 (persona dogfood 2026-09-07 round 2, release manager B4-S5):
# deliver below reuses bm_lead.py's own _resolve_project_id rather than a
# second copy of the exactly-one-project rule R-1 already wrote once.
bl = _load("bm_lead")
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

def _store_for_start():
    """The same open _store() performs, except a 'git-exposed-store'
    refusal is healed once and retried instead of relayed straight to
    the founder.

    R-11 (persona dogfood 2026-09-07, release manager persona B4
    scenario B4-S1): every writable open already tries to keep the raw
    store out of git (bm_store.py's _ensure_git_excludes appends its
    three lines to .git/info/exclude on every open), and that heal is
    normally enough, which is why _store() above never needed this. But
    git checks a repository's own .gitignore at the worktree top AFTER
    info/exclude (higher precedence), so a rule already committed there
    (from a template, or from someone re-including the directory by
    hand) wins over the line info/exclude holds, and info/exclude being
    unwritable produces the identical refusal. Either way the founder's
    first `start` used to just relay bm_store.py's refusal. This adds
    the same three lines to .gitignore itself (creating it if absent)
    and opens the store again, once: a second refusal, for this reason
    or any other, is reported exactly as it always was. Never touches
    .gitignore for any OTHER refusal reason, and never for
    'git-tracked-store', which names its own untracking command
    instead (an ignore rule cannot untrack a file git already commits)."""
    root = _root()
    try:
        return bs.Store(root, create=False)
    except bs.OwnershipRefused as exc:
        if exc.reason != "git-exposed-store":
            raise
        gitignore = os.path.join(root, ".gitignore")
        wanted = ["%s/" % bs.STORE_DIRNAME, "threads/", "STATE.md"]
        try:
            existing = ""
            if os.path.isfile(gitignore):
                with io.open(gitignore, encoding="utf-8",
                             errors="replace") as fh:
                    existing = fh.read()
            missing = [w for w in wanted if w not in existing.splitlines()]
            if not missing:
                raise exc
            with io.open(gitignore, "a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                for w in missing:
                    fh.write(w + "\n")
        except OSError as e:
            _err("bm_project: could not write %s (%s); reporting the "
                 "original refusal." % (gitignore, e))
            raise exc
        _err("bm_project: R-11: added %s to %s so git ignores the "
             "store; retrying." % (", ".join(missing), gitignore))
        return bs.Store(root, create=False)


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


def _default_actor_name():
    """(name, source): git config user.name, then the USER environment
    variable, then a fixed fallback. Never raises: a git call that fails,
    or a config with no user.name set, falls through to the next source.

    R-10 (persona dogfood 2026-09-07 round 2): a junior's first ten
    minutes hit a usage error on the very first mechanical step because
    --actor-name had no default. Same fix as bm_lead.py's own
    _default_actor_name, kept as a sibling copy rather than a cross-file
    import: this file's own docstring already states it is a thin CLI
    over bm_store.py and nothing else."""
    try:
        av = _load("bm_autosave")
        r = av._run_git(_root(), "config", "user.name")
        name = (r.stdout or "").strip()
        if r.returncode == 0 and name:
            return name, "git config user.name"
    except (ImportError, OSError, AttributeError):
        pass
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        return user, "the USER environment variable"
    return "unknown", "no name could be found"


def _actor(kv, usage):
    """Build the actor dict every mutating subcommand passes to the store,
    so attribution is a real record of who or what acted, not a guess.
    actor_type is restricted to human|model on this CLI's own surface
    (schema.py's AttributionEvent also allows hook|automation, but this
    tool is invoked directly by a person or by a model runtime, never as a
    hook).

    R-10 (persona dogfood 2026-09-07 round 2): actor_name now DEFAULTS
    from _default_actor_name rather than being required, because
    skills/start/SKILL.md's own documented first commands omit it. The
    default is named on stderr; an explicit --actor-name still always
    wins."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err(usage)
        _err("bm_project: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(2)
    actor_name = kv.get("actor-name")
    if not actor_name:
        actor_name, source = _default_actor_name()
        _err("bm_project: --actor-name not given; using %r, from %s."
             % (actor_name, source))
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
    _out(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


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


def render_delivery_packet(store, project_id, raw=False, grounding=None):
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
    redact_text funnel as the final guard before it touches disk.

    `grounding` is the one line U7's delivery grounding gate contributes
    (see _grounding_gate below): the counts and the language it checked,
    or the NO-DATA line when no contract was in play. It is passed in
    rather than computed here because this function reads STORE rows and
    the contract is a file on disk, which is a different source; a
    renderer that went looking for it would be reading two truths."""
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
    lines.append(grounding or _GROUNDING_NODATA)
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
    store = _store_for_start()
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
# list
# ---------------------------------------------------------------------------

def cmd_list(argv):
    """R-1 (persona dogfood 2026-09-07): every reader in this product
    demanded --project-id against a store nothing in the repository
    derives an id from, and there was no command to ask the store what
    ids it holds. This prints exactly that, read only, so
    bm_lead.py's _resolve_project_id has something to point a founder at
    when a folder holds more than one project."""
    _pos, kv = _parse(argv, ("json",), wants_value=())
    store = _read_store()
    try:
        projects = store.list_projects(raw=True)
    finally:
        store.close()
    if kv.get("json"):
        _print_json({"projects": projects})
        return 0
    if not projects:
        _out("no project in this folder yet; start one with: "
             "/brothermode:start (or, on a clone install: "
             'python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py start")')
        return 0
    for p in projects:
        _out("%s: %s" % (p.get("project_id"),
                         (p.get("goal") or "").strip() or "(no goal set)"))
    return 0


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

def cmd_next(argv):
    _pos, kv = _parse(argv, ("project-id", "json", "raw"),
                       wants_value=("project-id",))
    usage = "usage: next --project-id ID [--json] [--raw]"
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
        # F-004b (persona dogfood 2026-09-08): deliver already
        # infers the project through bm_lead.py's own _resolve_project_id
        # rather than a second copy of the exactly-one-project rule; next
        # demanded --project-id even in a folder holding exactly one, so
        # it now routes through the same helper the same way.
        project_id = bl._resolve_project_id(kv, store, usage)
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
    usage = ("usage: review <task_id> [--project-id ID] [--kind K] "
             "[--ref R] [--note N] [--to STATE(default verified)] "
             "--reason R [--criterion-id ID] "
             "[--actor-type human|model] --actor-name NAME "
             "[--session-id SID] [--out-json]")
    if not pos:
        _err(usage)
        _err("bm_project: review needs a task id")
        return 2
    task_id = pos[0]
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
        # R-1 (persona dogfood 2026-09-07): project id was a second key
        # this command demanded on top of task id, which already names
        # the task's own project (task id is a random hex uuid, unique
        # store wide). The flag still wins when given, so nothing that
        # already names one changes; a task id the store does not hold
        # is refused here rather than passed to review_task, which would
        # otherwise report the same fact in review_task's own words.
        project_id = kv.get("project-id")
        if not project_id:
            task_row = store.get_task(task_id, raw=True)
            if task_row is None:
                _err("bm_project: no task %r in this folder" % task_id)
                return 1
            project_id = task_row.get("project_id")
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


# ---------------------------------------------------------------------------
# the delivery grounding gate (U7, A-prime amendment 3,
# docs/plan/PLAN-THREE-ENGINES-2026-09-08.md step 7)
# ---------------------------------------------------------------------------

# THE SCENARIO THIS RETIRES, from the persona rounds: a handover pack that
# told the next engineer nothing to run, and a finance owner who asked for
# one sentence with a number and got none. Both delivered clean, because
# nothing between the ask and the packet ever compared the two. The gate
# compares them: the contract holds what was asked, in which language, and
# with which receipts, and the answer file holds what is about to be said.

#: The contract states a delivery may be made from. `draft` is excluded
#: because a draft is the record adopt leaves when a blocking question is
#: still open, and `superseded` because a record another one replaced is
#: not the one being handed over.
_DELIVERABLE_STATES = ("contracted", "planned", "in-flight", "delivered")

#: THE RESEARCH CITATION, the one grounding no run can give, kept as small
#: as it can be said: an answer whose receipt_id starts with this prefix is
#: grounded by its own source line rather than by a verdict, because
#: reading a document is not running a check. TWO things still hold. The
#: cited receipt must exist in the contract's own receipts array, because
#: contract_check's F1 rule refuses a delivered record whose receipt_id
#: names nothing, so a citation that lived only in the answer file would
#: make the rewritten record unwritable. And the answer text must carry a
#: line naming the source, so the next reader can go and look. Nothing
#: else is inferred: this is a marked absence of a run, not a second kind
#: of evidence.
_RESEARCH_PREFIX = "research:"
_SOURCE_MARK = "source:"

#: What the packet says when no contract was in play. NO-DATA, never a
#: pass: "nobody checked" and "checked and found grounded" are different
#: statements, and this file's other NO-DATA lines already say so.
_GROUNDING_NODATA = "Grounding: NO-DATA (no contract)"


def _default_contract(root):
    """(path, problem) for the contract deliver should use when
    --contract was not given.

    (None, None) when docs/decisions/inflight/ does not exist or holds no
    record: that is every store that never adopted, and it delivers
    exactly as it did before this gate existed. More than one record is a
    refusal naming the flag, never a guess at which one was meant."""
    directory = os.path.join(root, "docs", "decisions", "inflight")
    try:
        names = sorted(n for n in os.listdir(directory)
                       if n.endswith(".json"))
    except (IOError, OSError):
        return None, None
    if not names:
        return None, None
    if len(names) > 1:
        return None, ("%d contract records sit in %s; name the one you are "
                      "delivering with --contract PATH"
                      % (len(names), directory))
    return os.path.join(directory, names[0]), None


def _read_json_file(path, what):
    """(obj, problem). Both reads this gate makes cross a trust boundary
    (a file somebody else wrote), so each failure is named rather than
    raised as a traceback the person has to decode."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (IOError, OSError) as exc:
        return None, "could not read the %s at %s: %s" % (what, path, exc)
    except ValueError as exc:
        return None, "the %s at %s is not JSON: %s" % (what, path, exc)


def _answer_index(answer):
    """({field: entry}, problems) from an answer file, whose shape is
    {"language": "xx", "answers": [{"field", "text", "receipt_id"}],
    "summary": "..."}."""
    if not isinstance(answer, dict):
        return {}, ["the answer file must hold a JSON object"]
    entries = answer.get("answers")
    if not isinstance(entries, list):
        return {}, ["the answer file has no 'answers' array"]
    index, problems = {}, []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("field"):
            problems.append("every answers[] entry needs a 'field'")
            continue
        index[entry["field"]] = entry
    return index, problems


def _grounding_problems(contract, answer):
    """(problems, cited) for one contract read against one answer file.

    EVERY rule is checked before anything is reported, never one refusal
    at a time: _delivery_holes above already made that choice for this
    command, and a person fixing a delivery wants the whole list from one
    run. `cited` counts the must-answer fields a PASS receipt grounds, the
    number the packet's own Grounding line then quotes."""
    problems = []
    want = contract.get("language")
    got = answer.get("language") if isinstance(answer, dict) else None
    if want != got:
        problems.append(
            "language: the contract records the ask in %r and the answer "
            "file is written in %r; answer in the language the person "
            "asked in" % (want, got))
    index, shape = _answer_index(answer)
    problems.extend(shape)
    receipts = {}
    for receipt in (contract.get("receipts") or []):
        if isinstance(receipt, dict) and isinstance(receipt.get("id"), str):
            receipts[receipt["id"]] = receipt
    cited = 0
    for entry in (contract.get("must_answer") or []):
        field = (entry or {}).get("field", "?")
        given = index.get(field)
        if given is None:
            problems.append("must_answer.%s: the answer file carries no "
                            "entry for this field" % field)
            continue
        text = (given.get("text") or "").strip()
        if not text:
            problems.append("must_answer.%s: the answer text is empty"
                            % field)
        receipt_id = (given.get("receipt_id") or "").strip()
        if not receipt_id:
            problems.append("must_answer.%s: no receipt_id, so nothing on "
                            "record grounds this answer" % field)
            continue
        receipt = receipts.get(receipt_id)
        if receipt is None:
            problems.append("must_answer.%s: receipt_id %r names no entry "
                            "in the contract's receipts" % (field, receipt_id))
            continue
        if receipt_id.startswith(_RESEARCH_PREFIX):
            if _SOURCE_MARK not in text.lower():
                problems.append(
                    "must_answer.%s: a research citation needs a line "
                    "naming where it came from ('%s ...') in the answer "
                    "text" % (field, _SOURCE_MARK))
            continue
        verdict = receipt.get("verdict")
        if verdict != "PASS":
            problems.append(
                "must_answer.%s: receipt %s is %s, and a must-answer field "
                "is grounded only by a PASS receipt or a research citation"
                % (field, receipt_id, verdict))
            continue
        cited += 1
    return problems, cited


def _grounding_gate(project_id, contract_path, answer_path):
    """(contract, {field: answer entry}, the packet's Grounding line) when
    every rule passes; None when any does not, each problem already
    printed on its own line."""
    contract, problem = _read_json_file(contract_path, "contract")
    if problem:
        _err("cannot deliver %s: %s" % (project_id, problem))
        return None
    cc = _contract_checker()
    if cc is None:
        _err("cannot deliver %s: NO-DATA: scripts/contract_check.py is not "
             "on disk beside this tool, so the contract could not be "
             "checked; a check that could not run is never a pass"
             % project_id)
        return None
    try:
        schema = cc.load_json(cc.DEFAULT_SCHEMA, "schema")
        written = cc.load_json(contract_path, "record")
    except cc.NoData as exc:
        _err("cannot deliver %s: NO-DATA: %s" % (project_id, exc))
        return None
    problems = ["contract_check: %s" % p for p in cc.check(written, schema)]
    state = contract.get("state")
    if state not in _DELIVERABLE_STATES:
        problems.append("state: the contract is %r, and only a contract in "
                        "%s can be delivered"
                        % (state, ", ".join(_DELIVERABLE_STATES)))
    # F-005a: --answer-file (and the language/must-answer comparison it
    # feeds) is demanded only when there is something to answer. A
    # record with an empty must_answer asks nothing, so nothing is
    # compared against nothing: the whole grounding check below is
    # skipped rather than failing on an answer file nobody had a
    # question to write.
    must_answer = contract.get("must_answer") or []
    answer, cited = {}, 0
    if must_answer or answer_path:
        if not answer_path:
            problems.append(
                "--answer-file is required once a contract is in play "
                "(%s); it is the answer the gate compares against the ask"
                % contract_path)
        else:
            answer, problem = _read_json_file(
                os.path.abspath(answer_path), "answer file")
            if problem:
                problems.append(problem)
                answer = {}
        ground, cited = _grounding_problems(contract, answer)
        problems.extend(ground)
    if problems:
        for one in problems:
            _err("cannot deliver %s: %s" % (project_id, one))
        return None
    index, _shape = _answer_index(answer)
    line = ("Grounding: %d must-answer field(s), %d cited from PASS "
            "receipts, language %s matches the ask"
            % (len(must_answer), cited, contract.get("language")))
    return contract, index, line


def _mark_contract_delivered(path, contract, answers):
    """Rewrite `path` as a delivered record: each must_answer entry
    carries its answer and the receipt that grounds it, plus one history
    stamp. 0 when the rewritten record passes contract_check, 1 when it
    does not, in which case the ORIGINAL bytes go back on disk. cmd_adopt
    below makes the same call for the same reason: a record the estate's
    own checker rejects is worse than no record, because the next reader
    would treat it as the typed truth."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            original = fh.read()
    except (IOError, OSError) as exc:
        _err("bm_project: could not read %s: %s" % (path, exc))
        return 1
    record = json.loads(json.dumps(contract, ensure_ascii=False))
    for entry in (record.get("must_answer") or []):
        given = answers.get(entry.get("field"))
        if given is None:
            continue
        # The answer text (usually model written) lands in a record inside
        # the tree git commits: the same redact_text funnel as cmd_adopt.
        try:
            entry["answer"] = bs.redact_text((given.get("text") or "").strip())
        except bs.RedactionUnavailable as exc:
            _err("bm_project: refused to write %s unredacted: %s"
                 % (path, exc))
            return 1
        entry["receipt_id"] = (given.get("receipt_id") or "").strip()
    record["state"] = "delivered"
    actor, _actor_source = _default_actor_name()
    history = record.get("history")
    if not isinstance(history, list):
        history = []
    history.append({"at": bs.now_iso(), "by": actor,
                    "note": "delivered with grounded answers"})
    record["history"] = history
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2, sort_keys=True,
                             ensure_ascii=False) + "\n")
    except (IOError, OSError) as exc:
        _err("bm_project: could not write %s: %s" % (path, exc))
        return 1
    cc = _contract_checker()
    if cc is None:
        problems = ["NO-DATA: scripts/contract_check.py is not on disk"]
    else:
        try:
            schema = cc.load_json(cc.DEFAULT_SCHEMA, "schema")
            written = cc.load_json(path, "record")
            problems = cc.check(written, schema)
        except cc.NoData as exc:
            problems = ["NO-DATA: %s" % exc]
    if problems:
        for problem in problems:
            _err("bm_project: contract_check: FAIL: %s" % problem)
        try:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(original)
        except (IOError, OSError) as exc:
            _err("bm_project: could not restore %s: %s" % (path, exc))
        _err("bm_project: refused to leave a delivered record the contract "
             "checker rejects; %s is unchanged" % path)
        return 1
    return 0


def cmd_deliver(argv):
    _pos, kv = _parse(argv, ("project-id", "partial", "raw", "out-json",
                             "contract", "answer-file"),
                       wants_value=("project-id", "contract", "answer-file"))
    usage = ("usage: deliver --project-id ID [--partial] [--raw] "
             "[--out-json] [--contract PATH] [--answer-file PATH]")
    # Same contract cmd_export already uses: default withheld, --raw for
    # the project's own owner (DELIVERY-PACKET.md is an export, see this
    # file's module docstring and render_delivery_packet's own docstring).
    want_raw = bool(kv.get("raw"))
    root = _root()
    # F-005: a folder with no project yet used to hit deliver's own bare
    # usage refusal. A READER in the same spot gets bm_lead.py's own tree
    # read of what the repository already says (R-10); deliver is a
    # WRITER and keeps its exit 2, but nothing stops it from handing back
    # the same account instead of a sentence naming no next step.
    if not os.path.isfile(bs.store_path(root)):
        bl._print_no_project_tree_read(root, False)
        return 2
    store = _store()
    try:
        if not store.list_projects(raw=True):
            bl._print_no_project_tree_read(root, False)
            return 2
        # F-004: the flag still wins when given; a folder holding exactly
        # one project resolves to it, naming it on one stderr line
        # (bm_lead.py's own _resolve_project_id, the same helper R-1
        # already wired into review and every bm_lead.py reader). Two or
        # more projects keep the same exit-2 refusal that helper already
        # gives a writer (zero is handled above, with the tree read).
        project_id = bl._resolve_project_id(kv, store, usage)
        # U7 (delivery gate): resolved after project_id, not before, so a
        # missing --contract that falls back to _default_contract can name
        # the project in its own error line instead of a NameError.
        contract_path = kv.get("contract")
        if contract_path:
            contract_path = os.path.abspath(contract_path)
        else:
            contract_path, problem = _default_contract(_root())
            if problem:
                _err("cannot deliver %s: %s" % (project_id, problem))
                return 1
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
            # R-13: a bare headcount named no task, no receipt, and no
            # cost. Every unfinished task now gets its own line: a short
            # id, its title, its state, and its newest evidence reference
            # (raw only under --raw, the same local-owner switch every
            # other reader in this file already uses for free text).
            # No per-task cost estimate exists anywhere in this file
            # (checked: neither "forecast" nor "cost" computes one per
            # task), so the honest answer is NO-DATA, never a guess.
            display = store.list_tasks(project_id, raw=want_raw)
            for t in display:
                if t.get("status") == TERMINAL_STATE:
                    continue
                tid = t.get("task_id") or ""
                evidence = store.list_evidence("task", tid, raw=want_raw)
                newest_ref = evidence[-1].get("ref") if evidence else ""
                _err("  %s: %s [%s] evidence: %s"
                     % (tid[:8], t.get("title") or "(no title)",
                        t.get("status"), newest_ref or "no evidence"))
            _err("Cost to finish: NO-DATA (no estimate recorded)")
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
        # THE GATE, run BEFORE the packet is written: a refused delivery
        # must leave no packet behind, because a packet on disk is what
        # the next reader takes as the delivery having happened.
        grounding, contract, answers = None, None, None
        if contract_path:
            gate = _grounding_gate(project_id, contract_path,
                                   kv.get("answer-file"))
            if gate is None:
                return 1
            contract, answers, grounding = gate
        text = render_delivery_packet(store, project_id, raw=want_raw,
                                      grounding=grounding)
        _write_generated(_root(), _packet_filename(store, project_id), text,
                         DELIVERY_BEGIN, DELIVERY_END)
    finally:
        store.close()
    if contract_path:
        rc = _mark_contract_delivered(contract_path, contract, answers)
        if rc:
            return rc
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
# adopt: the typed project record, inferred from repository facts
# (A-prime amendment 1, docs/plan/PLAN-THREE-ENGINES-2026-09-08.md step 3;
# the 2026-09-08 debate judgment, unit U5; the record's own shape is
# docs/schema/outcome-contract-v1.json and its checker is
# scripts/contract_check.py)
#
# THE FINDING THIS CLOSES: the onboarding persona scenarios of both
# estates failed because Brother asked a first time user for facts the
# repository already holds. Amendment 1, verbatim: "the project record is
# typed, every field carries provenance (which repository fact it came
# from), at most one blocking question is asked and only for an
# outcome-required field still unknown; no field is guessed."
#
# SO: this command reads the tree, writes ONE record, and asks AT MOST
# ONE question. Every fact it could not read is the literal NO-DATA,
# never an invention. NO-DATA here means "I looked and the repository
# does not say", which is a different claim from a plausible default
# nobody can trace, and it is the same literal bm_lead.py's own tree read
# already uses for a git fact it could not get.
#
# PROVENANCE BINDS THE PROJECT OBJECT ONLY (F6, docs/schema/README.md):
# language, question, persona and the rest come from the ask itself and
# carry no provenance entry. That is the orchestrator's ruling, not a
# convenience: a field that came from the person asking has a source
# nobody needs a record for.
#
# IT NEEDS NO STORE, which is the whole point: a repository with no
# project yet is the case this exists for, so _adopt_root falls back to
# the working directory instead of refusing the way every store-backed
# command in this file does.
#
# THE TREE READS ARE bm_lead.py's, NOT A SECOND SET: _av,
# _repo_identity, _test_suites and _skip_tree_dirs are loaded from there
# and called as they stand. That module is loaded lazily, inside the
# command, and only its PURE tree readers are called: loading it by path
# produces a second bm_store module object (see the L = _load(...) note
# above for why that matters), and none of these helpers touch a store
# class, so the two objects never meet.
# ---------------------------------------------------------------------------

CONTRACT_SCHEMA_VERSION = "outcome-contract-v1"

#: The literal every unread fact lands on, matching bm_lead.py's own tree
#: read rather than inventing a second spelling of "not known".
ADOPT_NODATA = "NO-DATA"

#: The ONE question adopt is ever allowed to ask, and the only field it
#: is allowed to ask about. Amendment 1 caps this at one; the schema's
#: own questions rule (the A-prime rule) refuses a record that carries
#: more than one once the state is contracted or later.
ADOPT_QUESTION_FIELD = "success_checks"
ADOPT_QUESTION = ("How do you check this repository is healthy? "
                  "Name one command.")

ADOPT_PERSONAS = ("analyst", "lead", "developer")

#: ponytail: at most this many success checks land in one record, ordered,
#: so a monorepo with four hundred suite files does not write a four
#: hundred line record. Group by directory if a real repository ever needs
#: more proof than the cap carries.
_SUITE_CAP = 10

_ADOPT_FLAGS = ("ask", "language", "persona", "out", "answer", "json",
               "ticket", "branch", "no-audit")
_ADOPT_VALUE_FLAGS = ("ask", "language", "persona", "out", "answer",
                      "ticket", "branch")

#: A French ask, by the two cheapest signals that separate it from an
#: English one: an accented character, or two of these words at once.
_FRENCH_MARKS = "éèêàçùôîïû"
_FRENCH_WORDS = frozenset((
    "le", "la", "les", "des", "une", "un", "pour", "comment", "je",
    "nous", "vous", "est", "dans", "avec", "que", "qui", "sur", "projet",
    "faire", "cette", "sont", "pas", "plus", "mon", "notre",
))


def _adopt_usage():
    return ("usage: bm_project.py adopt --ask \"<what you want>\" "
            "[--language xx] [--persona analyst|lead|developer] "
            "[--out PATH] [--answer field=value] [--json] "
            "[--ticket SYSTEM:ID] [--branch NAME] [--no-audit]")


def _contract_slug(text, limit=40):
    """A filesystem and identifier safe fragment of `text`.

    Copied from tools/bm_packs.py's own _slug (the sibling in this same
    directory that already names decision files), rather than imported:
    bm_packs.py is a store-backed CLI, not a library this file should
    depend on for a five line string rule. The brief for this unit named
    scripts/intake_inflight.py as the source; that module has no slug
    rule of its own (its slug is an argument its caller supplies), so the
    nearest real rule in the estate is the one used here."""
    flat = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if not flat:
        flat = "decision"
    return flat[:limit].rstrip("-")


def _detect_language(ask):
    """'ja', 'fr' or 'en' from the ask itself.

    TWO RULES, NOT LANGUAGE IDENTIFICATION, and the docstring says so on
    purpose: Japanese script present means ja (which also claims a
    Chinese ask, a known and accepted limit), an accent or two French
    words at once means fr, everything else is en. --language always
    wins, so the person is never stuck with what these two rules
    decided. Nothing about this is a guess at a FIELD: the language of
    the ask is a property of the ask, and the ruling above is that the
    ask's own fields carry no provenance."""
    for ch in ask:
        # Hiragana and katakana (U+3040 to U+30FF), then the CJK
        # unified ideographs (U+4E00 to U+9FFF), written as escapes
        # so the range is readable in a terminal that cannot render
        # the characters themselves.
        if ("\u3040" <= ch <= "\u30ff") or ("\u4e00" <= ch <= "\u9fff"):
            return "ja"
    low = ask.lower()
    if any(mark in low for mark in _FRENCH_MARKS):
        return "fr"
    words = set(re.findall(r"[a-z]+", low))
    if len(words & _FRENCH_WORDS) >= 2:
        return "fr"
    return "en"


def _adopt_root():
    """The repository adopt reads and writes into. Every other command in
    this file goes through _root() and refuses when no BrotherMode root
    exists; adopt is the one command whose entire point is a repository
    with no project yet, so that refusal falls back to the working
    directory rather than ending the command."""
    try:
        return _root()
    except bs.BMStoreError:
        return os.path.abspath(os.getcwd())


def _git_remote(av, root):
    """The origin remote's URL, or NO-DATA on any git failure.

    bm_lead.py's _repo_identity reads the name and the branch through
    bm_autosave.py's own reviewed _run_git helper and never reads a
    remote, so this is the ONE repository fact that helper does not
    already return; it goes through the same helper rather than starting
    a git call of its own."""
    if av is None:
        return ADOPT_NODATA
    try:
        r = av._run_git(root, "remote", "get-url", "origin")
        if r.returncode == 0 and r.stdout.strip():
            return _strip_url_credentials(r.stdout.strip())
    except (OSError, AttributeError):
        pass
    return ADOPT_NODATA


def _strip_url_credentials(url):
    """The remote URL without a user or password. adopt writes it into a
    record under docs/decisions/, which git commits, so an https URL of the
    form user:token@host must never reach it. The scp form (git@host:path)
    carries no password and is returned unchanged. Plain string work, no
    urllib: SECURITY.md's no-network claim is checked by import scan."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    netloc, slash, path = rest.partition("/")
    if "@" not in netloc:
        return url
    return scheme + "://" + netloc.rsplit("@", 1)[1] + slash + path


def _project_name(repo_name, remote, root):
    """(name, provenance) for this repository, in the order the estate
    can actually trace: the remote's own last path segment when git gave
    one (that is the repository's NAME, where a checkout directory is
    only where this copy happens to sit), then the git toplevel's
    directory name bm_lead.py's _repo_identity returns, and only then the
    working directory's name, which no git fact backs and which therefore
    lands on NO-DATA provenance."""
    if remote != ADOPT_NODATA:
        tail = remote.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if tail.endswith(".git"):
            tail = tail[:-len(".git")]
        if tail:
            return tail, "git"
    if repo_name != ADOPT_NODATA:
        return repo_name, "git"
    return (os.path.basename(root.rstrip(os.sep)) or "project"), ADOPT_NODATA


def _make_has_test_target(root):
    """True when a Makefile in `root` declares a test target. False on
    any read failure: an unreadable Makefile is not evidence of a check."""
    try:
        with io.open(os.path.join(root, "Makefile"), encoding="utf-8") as fh:
            return re.search(r"(?m)^test:", fh.read()) is not None
    except (IOError, OSError, ValueError):
        return False


def _npm_has_test_script(root):
    """True when package.json in `root` carries a non-empty test script."""
    try:
        with io.open(os.path.join(root, "package.json"),
                     encoding="utf-8") as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return False
    return bool(str(scripts.get("test") or "").strip())


def _repo_success_checks(lead, root):
    """One runnable check per way this repository already documents its
    own tests, each in the command shape that repository itself uses:
    `make test` for a Makefile test target, `npm test` for a package.json
    test script, `python3 <path>` for a test_*.py suite file (the shape
    every suite in this estate is run with), and `python3 -m unittest
    discover <dir>` for a tests/ directory bm_lead.py's own _test_suites
    found that holds no test_*.py file already covered above.

    NOTHING IS INVENTED. A repository that documents none of these
    returns [], and that empty answer is exactly what makes the one
    blocking question the honest thing to ask instead of writing a
    plausible command nobody can run."""
    checks = []
    if _make_has_test_target(root):
        checks.append({"id": "make-test", "command": "make test",
                       "expect": "exit-0"})
    if _npm_has_test_script(root):
        checks.append({"id": "npm-test", "command": "npm test",
                       "expect": "exit-0"})
    skip = lead._skip_tree_dirs()
    files = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in skip and not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("test_") and fname.endswith(".py"):
                    files.append(os.path.relpath(
                        os.path.join(dirpath, fname), root))
    except OSError:  # sbe: allow-silent inaccessible test walk yields no generated check
        pass
    covered = set()
    for rel in sorted(files):
        checks.append({"id": _contract_slug(rel),
                       "command": "python3 %s" % rel,
                       "expect": "exit-0"})
        covered.add(os.path.dirname(rel))
    for rel, count in lead._test_suites(root):
        if count and rel not in covered:
            checks.append({"id": _contract_slug(rel),
                           "command": "python3 -m unittest discover %s" % rel,
                           "expect": "exit-0"})
    return checks[:_SUITE_CAP]


def _check_evidence_path(root, command):
    """The repository relative file that backs `command`, one of the
    four shapes _repo_success_checks builds -- or None when the command
    names nothing this function can resolve to a single file (the
    --answer path's user typed command, which is a claim from a person,
    never an observation of a file on disk).

    "make test" and "npm test" point at the manifest that carries the
    target (Makefile, package.json); "python3 <path>" points at <path>
    itself; "python3 -m unittest discover <dir>" has no single file of
    its own, so it points at the first file _repo_success_checks' own
    walk would have found inside <dir>, sorted for a stable pick."""
    if command == "make test":
        return "Makefile"
    if command == "npm test":
        return "package.json"
    prefix = "python3 -m unittest discover "
    if command.startswith(prefix):
        directory = command[len(prefix):]
        full_dir = os.path.join(root, directory)
        try:
            for dirpath, _dirnames, filenames in os.walk(full_dir):
                if filenames:
                    return os.path.relpath(
                        os.path.join(dirpath, sorted(filenames)[0]), root)
        except OSError:  # sbe: allow-silent inaccessible discovery directory has no representative file
            pass
        return None
    if command.startswith("python3 "):
        return command[len("python3 "):]
    return None


def _check_receipts(root, checks):
    """[{id, path, ref, verdict}], one entry per `checks` entry whose
    command resolves (through _check_evidence_path) to a real file on
    disk right now.

    THE CLAIM IS NARROW ON PURPOSE (the finding this closes, persona
    dogfood follow up 2026-09-08/09): adopt observed the file that made
    it write this success check in the first place, so the receipt says
    exactly that ("this file exists, this many lines"), in the
    file:<path>:<start>-<end> grammar scripts/receipt_check.py resolves.
    It is never a claim that the check has RUN, let alone passed a real
    execution -- verdict PASS here means the observation passed, not the
    suite. A command _check_evidence_path cannot resolve to a file (the
    --answer path's own typed command) gets no receipt at all, since
    nothing was observed for it beyond the person's own word."""
    out = []
    for c in checks:
        rel = _check_evidence_path(root, c["command"])
        if not rel:
            continue
        full = os.path.join(root, rel)
        try:
            with io.open(full, encoding="utf-8", errors="replace") as fh:
                nlines = sum(1 for _ in fh)
        except (IOError, OSError):
            continue
        if nlines < 1:
            # An empty file has no valid 1-based line range at all
            # (receipt_check's own resolve_receipt would refuse any
            # start it was given), so there is nothing true to claim.
            continue
        out.append({"id": c["id"], "path": rel,
                    "ref": "file:%s:1-%d" % (rel, nlines),
                    "verdict": "PASS"})
    return out


def _adopt_receipts(root, limit=_SUITE_CAP):
    """Every .sbe/evidence/*.json file as one receipts[] entry: id and
    path from the file name, ref in the resolver grammar
    (evidence:<name>, one of the three prefixes scripts/receipt_check.py
    understands), verdict from the file's own verdict field or NO-DATA
    when it does not carry one.

    bm_lead.py's _receipt_summary walks this same directory, but it
    returns COUNTS by verdict plus the newest file name, never the per
    file verdicts the contract's receipts[] needs, so this reads the
    names that helper does not expose rather than recomputing what it
    already gives."""
    directory = os.path.join(root, ".sbe", "evidence")
    found = []
    if not os.path.isdir(directory):
        return found
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        path = os.path.join(directory, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        verdict = ADOPT_NODATA
        try:
            with io.open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                stated = str(data.get("verdict") or "").strip().upper()
                if stated in ("PASS", "FAIL", ADOPT_NODATA):
                    verdict = stated
        except (IOError, OSError, ValueError):
            pass
        found.append({"id": name,
                      "path": os.path.join(".sbe", "evidence", name),
                      "ref": "evidence:%s" % name,
                      "verdict": verdict})
        if len(found) >= limit:
            break
    return found


def _affected_products(root):
    """Which of this estate's products the tree shows evidence of. The
    umbrella is always true (the record belongs to it); the other two are
    claimed only when their own directory is on disk. STORE_DIRNAME comes
    from bm_store rather than being retyped, the same discipline
    bm_lead.py's own skip list states."""
    products = ["brother"]
    if os.path.isdir(os.path.join(root, bs.STORE_DIRNAME)):
        products.append("brothermode")
    if os.path.isdir(os.path.join(root, ".sbe")):
        products.append("brothersbe")
    return products


def _contract_checker():
    """scripts/contract_check.py, loaded BY PATH exactly the way _load
    above loads a sibling module, so adopt can refuse to leave a record
    the estate's own checker rejects. None when it is not on disk (a
    packaged install that ships the tools without the scripts
    directory), which is a NO-DATA, never a pass."""
    path = os.path.normpath(os.path.join(
        HERE, "..", "..", "..", "scripts", "contract_check.py"))
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("contract_check", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, OSError, AttributeError):
        return None


def _adopt_store_message(root, record, actor_name):
    """The one line adopt prints about the store project it did or did
    not create for `record`, and the side effect that backs it.

    R-9 (persona dogfood 2026-09-08, the first defect this unit fixes):
    adopt used to write only the contract file, so a founder who ran
    `adopt` and then `list` or `status` was told no project existed at
    all -- the contract and the store were two records of the same
    decision that never met. This opens the SAME writable store cmd_start
    opens (_store_for_start, R-11's healing included) and calls the same
    store.upsert_project it calls, so a project adopt creates is a real
    project every other command already knows how to read; nothing here
    duplicates cmd_start's own field handling or its git-exposed-store
    heal.

    _store_for_start refuses 'no-store' when nothing has ever run
    `bm_store.py init` here, which is the NORMAL case for adopt:
    skills/start/SKILL.md's own init failsafe covers `start`'s guided
    kickoff, never `adopt` ("adopt first, one command" runs on a
    repository nothing has touched yet). So a genuine first open here
    creates the store exactly as `init` would (bs.Store(root,
    create=True), the same git-exclude heal that constructor already
    performs), rather than refusing the one record adopt exists to leave
    behind.

    A draft record (an open question still on record) creates nothing:
    the record itself is not yet the typed truth, and a store project
    built from a placeholder command would be worse than none. Never
    raises: a repository with nowhere to put a store (no git boundary, no
    BROTHERMODE_ROOT) is reported on this one line, not fatal to the
    command whose real deliverable is the contract file already written."""
    if record["state"] != "contracted":
        return "no project created in the store: the record is a draft"
    project = record["project"]
    project_id = project["project_id"]
    try:
        store = _store_for_start()
    except bs.OwnershipRefused as exc:
        if exc.reason != "no-store":
            return "project %s NOT created in the store: %s" % (project_id, exc)
        try:
            store = bs.Store(root, create=True)
        except bs.BMStoreError as exc2:
            return ("project %s NOT created in the store: %s"
                    % (project_id, exc2))
    except bs.BMStoreError as exc:
        return "project %s NOT created in the store: %s" % (project_id, exc)
    try:
        existed = any(p.get("project_id") == project_id
                     for p in store.list_projects(raw=True))
        now = bs.now_iso()
        store.upsert_project(
            {"project_id": project_id, "name": project["name"],
             "goal": record["question"], "user_outcome": record["question"],
             "status": "draft", "created_at": now, "updated_at": now},
            {"actor_type": "model", "actor_name": actor_name,
             "session_id": "cli-" + uuid.uuid4().hex})
    finally:
        store.close()
    if existed:
        return "project %s already in the store, record updated" % project_id
    return "project %s created in the store" % project_id


def cmd_adopt(argv):
    """Write one outcome contract record from what this repository can
    already say, and ask at most one question about what it cannot.

    EXIT CODES, distinct on purpose so the door can tell the three cases
    apart without parsing prose:
      0  the record is contracted and no question is open
      3  the record is a draft and ONE question is open
      2  usage, a write that failed, or a record the contract checker
         rejected (in which case nothing is left on disk)"""
    _pos, kv = _parse(argv, _ADOPT_FLAGS, wants_value=_ADOPT_VALUE_FLAGS)
    usage = _adopt_usage()
    ask = _require(kv, "ask", usage).strip()
    if not ask:
        _err(usage)
        _err("bm_project: --ask must not be empty")
        return 2

    persona = kv.get("persona") or ADOPT_NODATA
    if persona not in ADOPT_PERSONAS + (ADOPT_NODATA,):
        _err("bm_project: --persona must be one of %s (got %r)"
             % (", ".join(ADOPT_PERSONAS), persona))
        return 2

    # D-002 (persona dogfood 2026-09-07, B1-S1/B2-S2): a team living in
    # Jira needs the CR id bound to the record. SYSTEM:ID is the whole
    # grammar; a change bound to a tracked ticket is audited by default,
    # since --no-audit is the deliberate opt out rather than the default.
    ticket = None
    audit_required = False
    raw_ticket = kv.get("ticket")
    if raw_ticket:
        system, sep, ticket_id = raw_ticket.partition(":")
        system = system.strip()
        ticket_id = ticket_id.strip()
        if not sep or not system or not ticket_id:
            _err("bm_project: --ticket takes SYSTEM:ID (got %r)"
                 % raw_ticket)
            return 2
        ticket = {"system": system, "id": ticket_id}
        audit_required = not kv.get("no-audit")

    answered = None
    raw_answer = kv.get("answer")
    if raw_answer:
        field, sep, value = raw_answer.partition("=")
        if not sep or not value.strip():
            _err("bm_project: --answer takes field=value (got %r)"
                 % raw_answer)
            return 2
        if field.strip() != ADOPT_QUESTION_FIELD:
            _err("bm_project: --answer only answers %s, the one question "
                 "adopt asks (got %r)"
                 % (ADOPT_QUESTION_FIELD, field.strip()))
            return 2
        answered = value.strip()

    # The record lands under docs/decisions/inflight/, inside the tree git
    # commits, so the person's own text passes through the same redact_text
    # funnel every generated document here uses BEFORE it reaches the
    # record, its file name (the slug of the ask), the store, or stdout.
    try:
        ask = bs.redact_text(ask)
        if answered:
            answered = bs.redact_text(answered)
    except bs.RedactionUnavailable as exc:
        _err("bm_project: refused to write the record unredacted: %s" % exc)
        return 2

    root = _adopt_root()
    lead = _load("bm_lead")
    av = lead._av()
    repo_name, branch = lead._repo_identity(av, root)
    remote = _git_remote(av, root)

    # The one provenance decision: git either identified this repository
    # or it did not. A directory name is a real fact but not a git one,
    # so it lands under NO-DATA provenance rather than borrowing git's.
    display_name, source = _project_name(repo_name, remote, root)

    # D-002: --branch binds an existing (or about-to-exist) branch to the
    # record. The schema carries one provenance value for the whole
    # repository sub object (docs/schema/outcome-contract-v1.json,
    # project.provenance), not one per field, so a bound branch's own
    # provenance REPLACES the repository provenance rather than adding a
    # new key the checker would reject as unknown (F7).
    repo_provenance = source
    branch_flag = kv.get("branch")
    if branch_flag:
        branch = branch_flag
        found = (av._run_git(root, "show-ref", "--verify",
                             "refs/heads/%s" % branch_flag)
                if av is not None else None)
        if found is not None and found.returncode == 0:
            repo_provenance = "git"
            _err("bm_project: branch %r found in the repository"
                 % branch_flag)
        else:
            repo_provenance = "ask"
            _err("bm_project: branch %r not found in the repository; it "
                 "will be created by the plan" % branch_flag)

    checks = _repo_success_checks(lead, root)
    # Every receipt below observes a check BEFORE the --answer flag (a
    # person's own typed command, never something adopt observed on
    # disk) can replace or displace it; a check dropped by that
    # replacement or by the cap loses its receipt along with it.
    check_receipts = _check_receipts(root, checks)
    if answered:
        checks = ([{"id": _contract_slug(answered), "command": answered,
                    "expect": "exit-0"}]
                  + [c for c in checks if c["command"] != answered])
        checks = checks[:_SUITE_CAP]
        kept_ids = set(c["id"] for c in checks)
        check_receipts = [r for r in check_receipts if r["id"] in kept_ids]

    questions = []
    if checks:
        state = "contracted"
    else:
        # THE ONE QUESTION. The schema now allows success_checks to be
        # empty while state is draft (a schema correction found by U5:
        # the field the one open question is about needs no placeholder
        # entry to stand in for the answer that has not arrived yet).
        state = "draft"
        questions = [{"field": ADOPT_QUESTION_FIELD,
                      "question": ADOPT_QUESTION}]

    actor, _actor_source = _default_actor_name()
    record = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "project": {
            "project_id": _contract_slug(display_name),
            "name": display_name,
            "repository": {
                "name": display_name if source == "git" else ADOPT_NODATA,
                "branch": branch,
                "remote": remote},
            "provenance": {"project_id": source, "name": source,
                           "repository": repo_provenance},
        },
        "language": kv.get("language") or _detect_language(ask),
        "question": ask,
        "success_checks": checks,
        "must_answer": [],
        "affected_products": _affected_products(root),
        "ticket": ticket,
        "audit": {"required": audit_required, "manifest": None},
        "persona": persona,
        "state": state,
        "receipts": check_receipts + _adopt_receipts(root),
        "questions": questions,
        "history": [{"at": bs.now_iso(), "by": actor,
                     "note": "adopted from repository facts"}],
        "decision": None,
    }

    out = kv.get("out") or os.path.join(
        root, "docs", "decisions", "inflight", "%s.json" % _contract_slug(ask))
    out = os.path.abspath(out)
    try:
        directory = os.path.dirname(out)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with io.open(out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2, sort_keys=True,
                             ensure_ascii=False) + "\n")
    except (IOError, OSError) as exc:
        _err("bm_project: could not write %s: %s" % (out, exc))
        return 2

    cc = _contract_checker()
    if cc is None:
        _out(out)
        _err("bm_project: NO-DATA: scripts/contract_check.py is not on disk "
             "beside this tool, so the record was written but never "
             "checked; run the checker yourself before relying on it")
        return 2
    try:
        schema = cc.load_json(cc.DEFAULT_SCHEMA, "schema")
        written = cc.load_json(out, "record")
    except cc.NoData as exc:
        _err("bm_project: NO-DATA: %s" % exc)
        return 2
    problems = cc.check(written, schema)
    if problems:
        # A record the estate's own checker rejects is worse than no
        # record: the next reader would treat it as the typed truth. So
        # it never survives this command.
        for problem in problems:
            _err("bm_project: contract_check: FAIL: %s" % problem)
        try:
            os.remove(out)
        except OSError:  # sbe: allow-silent failed cleanup is reported by the refused delivery result
            pass
        _err("bm_project: refused to leave a record the contract checker "
             "rejects; nothing was left at %s" % out)
        return 2

    store_message = _adopt_store_message(root, record, actor)
    if kv.get("json"):
        _print_json({"path": out, "record": record, "store": store_message})
    else:
        _out(out)
        if ticket:
            _out("ticket: %s:%s" % (ticket["system"], ticket["id"]))
        if branch_flag:
            _out("branch: %s" % branch)
        _out(store_message)
        if record["state"] == "contracted":
            # scripts/intake_measure.py counts human turns up to this
            # exact literal (its own ACCEPTANCE_MARKER) to score Intake
            # V2's TURNS number; printed only here, the one place a
            # record actually lands ready to act on with nothing left
            # to answer, never for a draft still holding an open
            # question.
            _out("[INTAKE-ACCEPTED] %s" % out)
        for question in questions:
            _out("%s: %s" % (question["field"], question["question"]))
    return 3 if questions else 0


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "start": cmd_start,
    "status": cmd_status,
    "list": cmd_list,
    "adopt": cmd_adopt,
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

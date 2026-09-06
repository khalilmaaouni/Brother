#!/usr/bin/env python3
"""bm_sentinel.py: Phase 1 of the Memory Sentinel, the deterministic,
silence-biased memory selector plus its command line.

Spec: docs/superpowers/specs/2026-08-02-memory-sentinel-phase1-design.md,
sections 4 (the pure selection policy) and 5 (this command line). This file
implements those two sections only. The schema and Store methods (section 3)
are owned by a different implementer working in a separate worktree at the
same time, so every Store call here is written against the spec's documented
signatures and has never been run against a live Store from this file.

THE FOUR LAWS select() enforces by construction, each meant to be proven by
its own named test in tools/test_bm_sentinel.py rather than assumed:

1. select() never returns more than one memory. One reminder, one moment.
2. select() never returns a sentinel_status row: it has no status parameter
   at all, so there is nothing to return by accident.
3. Every silent return carries a non-empty reason string.
4. An unknown trigger raises ValueError naming the allowed set. It never
   falls through to a silent return, because a typo that reads as "the
   sentinel chose silence" is indistinguishable from working software.

select(trigger, context, knowledge, procedural, recent_interventions) is
PURE: no I/O, no store access, no clock read. It takes plain lists of dicts
and returns (decision, memories, reason), so a test suite can drive every one
of its seven ordered branches with data alone: nothing to say, cooldown,
post_failure, pre_risky, resume, phase_boundary, tool_interval.

The command line is a thin layer over tools/bm_store.py's Store methods,
imported lazily inside each command function (never at module load), so this
file imports cleanly and select() stays testable even while bm_store.py is
mid-edit in a parallel worktree.

Eight commands: remember-knowledge, remember-procedural, status-set, check,
judge, list, stats, retire. Exit codes, matching tools/bm_ledger.py's own
convention: EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2. `check` exits 0
whether it injects or stays silent, because silence is a successful outcome
of the command, not a failure of it; exit 1 is reserved for a refusal
(unknown project, invalid enum, a write that matched no row), exit 2 for
usage.
"""
import re
import sys

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

# Section 2.4's enum, in the order the spec lists it.
ALLOWED_TRIGGERS = ("phase_boundary", "pre_risky", "post_failure",
                    "tool_interval", "resume")

COOLDOWN_N = 5          # section 4, branch 2: last N interventions
MIN_TOKEN_OVERLAP = 2   # section 4, branches 3 and 4

# Per-field cap for a rendered reminder. Generous on purpose: a requirement is
# a sentence, and a cap that truncates mid-clause produces a reminder that
# misleads rather than one that is merely long. See render_reminder.
REMINDER_FIELD_LIMIT = 600

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text):
    """Lowercase alphanumeric tokens of length >= 4, as a set. Section 4
    defines the overlap match this way for both post_failure and
    pre_risky; a missing or empty text yields an empty set, which can never
    meet MIN_TOKEN_OVERLAP, so absent context or attempt text is handled by
    the same rule rather than a special case."""
    return set(t for t in _TOKEN_RE.findall((text or "").lower())
                if len(t) >= 4)


def _split_ids(value):
    """memory_ids as recorded on an intervention row: a comma-separated
    string per the sentinel_interventions schema (section 2.4), but this
    also accepts an already-split list or tuple so select() does not care
    which shape its caller happens to hand it. Empty or missing yields no
    ids, never a list containing one empty string."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v]
    return [part for part in str(value).split(",") if part]


def _cooldown_ids(recent_interventions):
    """Every memory id injected in the last COOLDOWN_N interventions.

    Assumes recent_interventions is ordered most-recent first (a store
    method named "recent" naturally reads that way); select() only looks
    at the first COOLDOWN_N entries of whatever order it is handed, so it
    never trusts a caller to have already limited the list to that count."""
    ids = set()
    for row in list(recent_interventions)[:COOLDOWN_N]:
        ids.update(_split_ids(row.get("memory_ids")))
    return ids


def _best_overlap_match(candidates, context):
    """Highest token overlap with context wins; ties break by lower
    surface_count, then by older (lexicographically smaller ISO 8601)
    created_at. None when no candidate clears MIN_TOKEN_OVERLAP."""
    context_tokens = _tokens(context)
    scored = []
    for c in candidates:
        overlap = len(context_tokens & _tokens(c.get("attempt")))
        if overlap >= MIN_TOKEN_OVERLAP:
            scored.append((overlap, c))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].get("surface_count", 0),
                                   item[1].get("created_at", "")))
    return scored[0][1]


def _pick_requirement(candidates):
    """Ordered by surface_count ascending then created_at ascending, per
    the resume, phase_boundary and tool_interval branches. None when the
    candidate list is empty."""
    if not candidates:
        return None
    ordered = sorted(candidates,
                      key=lambda k: (k.get("surface_count", 0),
                                     k.get("created_at", "")))
    return ordered[0]


def _silent_reason(pool_before_cooldown, cooldown_ids, default_reason):
    """The honest reason a branch found nothing.

    Spec section 4 AMENDMENT 3, 2026-08-02, from the adversarial review. Branch
    2 tests cooldown against the WHOLE memory pool, so a round where the only
    trigger-relevant memory is in cooldown, but some unrelated memory is not,
    falls through branch 2 and lands on a trigger branch that finds no
    candidate. It then recorded "no prior attempt matches this failure", which
    is false: the matching attempt existed and cooldown suppressed it.

    That reason is not cosmetic. It is written to sentinel_interventions and
    is the ONLY evidence Phase 4 has when it decides whether the policy is too
    strict. A round suppressed by cooldown, filed as "nothing matched", argues
    for loosening the matcher when the cooldown was the cause. The ledger would
    teach the opposite of the truth.

    So a branch that finds nothing asks this: did MY candidate set have members
    before cooldown removed them? If yes, cooldown is the reason. If no, the
    branch's own default reason is the reason."""
    suppressed = [m for m in pool_before_cooldown
                  if m.get("id") in cooldown_ids]
    if suppressed and len(suppressed) == len(pool_before_cooldown):
        return "every candidate for this trigger is in cooldown"
    if suppressed:
        return ("%s (%d further candidate(s) were in cooldown)"
                % (default_reason, len(suppressed)))
    return default_reason


def select(trigger, context, knowledge, procedural, recent_interventions):
    """Section 4's pure selection policy. Returns (decision, memories,
    reason) where decision is "inject" or "silent" and memories is a list
    of zero or one dict. Raises ValueError for an unrecognized trigger
    instead of ever reading that typo as a reason to stay silent."""
    if trigger not in ALLOWED_TRIGGERS:
        raise ValueError("unknown trigger %r, allowed: %s"
                          % (trigger, ", ".join(ALLOWED_TRIGGERS)))

    # Branch 1: nothing to say.
    if not knowledge and not procedural:
        return ("silent", [], "no memories recorded")

    # Branch 2: cooldown, a blanket filter applied before any trigger-
    # specific branch runs.
    cooldown_ids = _cooldown_ids(recent_interventions)
    filtered_knowledge = [k for k in knowledge
                          if k.get("id") not in cooldown_ids]
    filtered_procedural = [p for p in procedural
                           if p.get("id") not in cooldown_ids]
    if not filtered_knowledge and not filtered_procedural:
        return ("silent", [], "every candidate is in cooldown")

    # Branch 3: post_failure.
    if trigger == "post_failure":
        unfiltered = [p for p in procedural
                      if p.get("outcome") in ("failed", "ruled_out")]
        candidates = [p for p in filtered_procedural
                      if p.get("outcome") in ("failed", "ruled_out")]
        match = _best_overlap_match(candidates, context)
        if match is None:
            return ("silent", [], _silent_reason(
                unfiltered, cooldown_ids,
                "no prior attempt matches this failure"))
        return ("inject", [match],
                "prior %s attempt overlaps this failure by token match"
                % match.get("outcome"))

    # Branch 4: pre_risky.
    if trigger == "pre_risky":
        unfiltered = [p for p in procedural if p.get("outcome") == "failed"]
        candidates = [p for p in filtered_procedural
                      if p.get("outcome") == "failed"]
        match = _best_overlap_match(candidates, context)
        if match is None:
            return ("silent", [], _silent_reason(
                unfiltered, cooldown_ids,
                "no prior failure matches this action"))
        return ("inject", [match],
                "prior failed attempt overlaps this action by token match")

    # Branch 5: resume. Allowed to resurface an already-surfaced memory.
    if trigger == "resume":
        candidates = [k for k in filtered_knowledge
                      if k.get("kind") in ("requirement", "constraint")]
        unfiltered = [k for k in knowledge
                      if k.get("kind") in ("requirement", "constraint")]
        match = _pick_requirement(candidates)
        if match is None:
            return ("silent", [], _silent_reason(
                unfiltered, cooldown_ids,
                "no requirements or constraints recorded"))
        return ("inject", [match],
                "resume surfaces the least-recently-surfaced requirement "
                "or constraint")

    # Branch 6: phase_boundary. Same pool as resume, restricted to never
    # yet surfaced.
    if trigger == "phase_boundary":
        candidates = [k for k in filtered_knowledge
                      if k.get("kind") in ("requirement", "constraint")
                      and k.get("surface_count", 0) == 0]
        unfiltered = [k for k in knowledge
                      if k.get("kind") in ("requirement", "constraint")
                      and k.get("surface_count", 0) == 0]
        match = _pick_requirement(candidates)
        if match is None:
            return ("silent", [], _silent_reason(
                unfiltered, cooldown_ids,
                "every requirement has already been surfaced"))
        return ("inject", [match],
                "unsurfaced requirement or constraint at a phase boundary")

    # Branch 7: tool_interval. Same criterion as phase_boundary, kept as
    # its own named branch because it is its own named trigger.
    if trigger == "tool_interval":
        candidates = [k for k in filtered_knowledge
                      if k.get("kind") in ("requirement", "constraint")
                      and k.get("surface_count", 0) == 0]
        unfiltered = [k for k in knowledge
                      if k.get("kind") in ("requirement", "constraint")
                      and k.get("surface_count", 0) == 0]
        match = _pick_requirement(candidates)
        if match is None:
            return ("silent", [], _silent_reason(
                unfiltered, cooldown_ids,
                "nothing new since the last check"))
        return ("inject", [match],
                "unsurfaced requirement or constraint found on a routine "
                "check")

    # Unreachable: trigger was validated against ALLOWED_TRIGGERS above,
    # and every member of that tuple is handled by a branch above it.
    raise ValueError("unhandled trigger %r" % trigger)


def render_reminder(memory, reason):
    """The three-line reminder text from section 4. `content` is used for
    a knowledge row, `attempt` for a procedural one; `source` for a
    knowledge row, `diagnosis` for a procedural one (falling back to a
    plain statement when a procedural row has no diagnosis, since that
    column is nullable). WHY NOW reuses select()'s own reason string: it is
    already a trigger-specific one-liner and section 4 does not define a
    second phrase for the same fact.

    EVERY interpolated field passes through bm_learning.safe_display first,
    and that is a security property rather than tidiness. This reminder is
    injected into a working agent's context BY DESIGN, and the memories it
    renders are written by agents that read web pages, files and command
    output. A stored memory carrying a newline could otherwise forge extra
    MEMORY, WHY NOW or SOURCE lines inside a block the reading agent has
    every reason to treat as system-authored, which turns the sentinel into
    an amplifier for whatever text an agent happened to store. safe_display
    strips control characters and caps length, the same defence and the same
    reason as bm_learn.py, where a newline inside a rule could forge a second
    rule block. Three lines out, always, no matter what went in.

    The cap is generous rather than the module default: a requirement is a
    sentence, and truncating it mid-clause would produce a reminder that
    misleads instead of one that is merely long."""
    content = memory.get("content")
    if content is None:
        content = memory.get("attempt", "")
    source = memory.get("source")
    if source is None:
        source = memory.get("diagnosis") or "no diagnosis recorded"
    learning = _load_learning()
    content = learning.safe_display(content or "", REMINDER_FIELD_LIMIT)
    reason = learning.safe_display(reason or "", REMINDER_FIELD_LIMIT)
    source = learning.safe_display(source or "", REMINDER_FIELD_LIMIT)
    return "MEMORY: %s\nWHY NOW: %s\nSOURCE: %s" % (content, reason, source)


def _table_for_memory(memory, knowledge, procedural):
    """Which sentinel table a memory select() returned came from. select()
    itself never tags this (its inputs and outputs are plain dicts per
    section 4), so the caller, which already holds both source lists,
    works it out by id membership."""
    memory_id = memory.get("id")
    if any(k.get("id") == memory_id for k in knowledge):
        return "sentinel_knowledge"
    if any(p.get("id") == memory_id for p in procedural):
        return "sentinel_procedural"
    raise ValueError("memory id %r came from neither knowledge nor "
                      "procedural; select() must only ever return a "
                      "candidate it was given" % memory_id)


# ---------------------------------------------------------------------
# Command line (section 5). Manual flag parsing, matching the shape
# tools/bm_project.py already uses for its own kv-style flags (copied
# rather than imported: that file is a sibling CLI, not a library this one
# should depend on for argument handling).
# ---------------------------------------------------------------------

def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _load_learning():
    """Lazy, by-path load of tools/bm_learning.py for safe_display, the same
    technique bm_learn.py and bm_ledger.py use, and for the same reason: this
    file stays a standalone script with no import-time dependency.

    A failed load REFUSES rather than falling back to rendering raw text. That
    is the rule bm_ledger.py's own review arrived at when it found a tool
    writing founder prose unscrubbed: an unavailable scrubber must stop the
    operation, because the alternative is a security control that silently
    disappears exactly when something is wrong with the installation."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import bm_learning
    if not hasattr(bm_learning, "safe_display"):
        raise RuntimeError(
            "bm_learning.safe_display is missing, so a reminder cannot be "
            "rendered safely. Refusing to render raw memory text into an "
            "agent's context.")
    return bm_learning


class Refused(Exception):
    """A refusal the command line turns into EXIT_REFUSED with one readable
    line. Raised rather than printed at the raise site so every command
    refuses in the same shape."""


def _require_project(store, project_id):
    """Spec section 5 lists "unknown project" as an exit-1 refusal, and the
    adversarial review of 2026-08-02 found no command enforcing it.

    This is not bookkeeping. Every sentinel write also writes an attribution
    row, and an attribution row against a project that has no projects record
    leaves the store's OWN integrity check reporting a problem. Reproduced
    before this function existed:

        $ bm_sentinel.py remember-knowledge --project does-not-exist ...
        exit=0
        $ bm_store.py verify
        verify: 1 problem(s) found:
          - attribution event ... references missing project 'does-not-exist'

    So a typo'd project name did not fail, it silently damaged the store.
    Create the project first with bm_project.py start."""
    if store.get_project(project_id) is None:
        import os
        bm_store = sys.modules.get("bm_store")
        start_cmd = bm_store.invocation(
            "bm_project",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "bm_project.py"))
        raise Refused(
            "no project %r in this store. Sentinel memories attach to a "
            "project, and writing against one that does not exist leaves "
            "the store's own verify reporting a dangling attribution row. "
            "Create it first: %s start --project-id %s --name ... "
            "--actor-name ..." % (project_id, start_cmd, project_id))


def _get_store():
    """Lazy, inside-the-function import of tools/bm_store.py, so this
    module never depends on the Store at load time. bm_store.py is owned
    by a different implementer working in parallel; its methods are called
    here exactly as section 3 of the spec documents them, and have not
    been exercised against a real Store from this file."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import bm_store
    root, _source = bm_store.require_root()
    return bm_store.Store(root, create=False)


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("bm_sentinel: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(EXIT_USAGE)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("bm_sentinel: --%s needs a value" % name)
                    sys.exit(EXIT_USAGE)
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
        _err("bm_sentinel: --%s is required" % name)
        sys.exit(EXIT_USAGE)
    return val


_ACTOR_FLAGS = ("actor-name", "actor-type")


def _actor(kv):
    """The actor value every mutating Store method takes last (section 3).
    Not documented as a CLI flag anywhere in section 5's command table, so
    this defaults to identifying the CLI itself rather than requiring a
    flag no documented invocation supplies; --actor-name and --actor-type
    are accepted as optional overrides for a caller that wants to be more
    specific. This shape (actor_type restricted to human or model,
    actor_name a plain string) matches the convention tools/bm_project.py
    already uses for the same purpose."""
    actor_type = kv.get("actor-type", "model")
    if actor_type not in ("human", "model"):
        _err("bm_sentinel: --actor-type must be 'human' or 'model', got %r"
             % actor_type)
        sys.exit(EXIT_USAGE)
    actor_name = kv.get("actor-name") or "bm_sentinel-cli"
    return {"actor_type": actor_type, "actor_name": actor_name}


def cmd_remember_knowledge(argv):
    usage = ("usage: bm_sentinel.py remember-knowledge --project ID --kind K "
              "--content C --source S [--session S]")
    known = {"project", "kind", "content", "source", "session"}
    known |= set(_ACTOR_FLAGS)
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    kind = _require(kv, "kind", usage)
    content = _require(kv, "content", usage)
    source = _require(kv, "source", usage)
    session_id = kv.get("session")
    actor = _actor(kv)
    store = _get_store()
    _require_project(store, project_id)
    try:
        memory_id = store.add_knowledge(project_id, kind, content, source,
                                         session_id, actor)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    _out(memory_id)
    return EXIT_OK


def cmd_remember_procedural(argv):
    usage = ("usage: bm_sentinel.py remember-procedural --project ID "
              "--attempt A --outcome O [--diagnosis D] [--session S]")
    known = {"project", "attempt", "outcome", "diagnosis", "session"}
    known |= set(_ACTOR_FLAGS)
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    attempt = _require(kv, "attempt", usage)
    outcome = _require(kv, "outcome", usage)
    diagnosis = kv.get("diagnosis")
    session_id = kv.get("session")
    actor = _actor(kv)
    store = _get_store()
    _require_project(store, project_id)
    try:
        memory_id = store.add_procedural(project_id, attempt, outcome,
                                          diagnosis, session_id, actor)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    _out(memory_id)
    return EXIT_OK


def cmd_status_set(argv):
    usage = ("usage: bm_sentinel.py status-set --project ID --summary S "
              "[--risks R] [--session S]")
    known = {"project", "summary", "risks", "session"}
    known |= set(_ACTOR_FLAGS)
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    summary = _require(kv, "summary", usage)
    open_risks = kv.get("risks")
    session_id = kv.get("session")
    actor = _actor(kv)
    store = _get_store()
    _require_project(store, project_id)
    try:
        status_id = store.set_status(project_id, summary, open_risks,
                                      session_id, actor)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    _out(status_id)
    return EXIT_OK


def cmd_check(argv):
    usage = ("usage: bm_sentinel.py check --project ID --trigger T "
              "[--context TEXT] [--session S]")
    known = {"project", "trigger", "context", "session"}
    known |= set(_ACTOR_FLAGS)
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    trigger = _require(kv, "trigger", usage)
    context = kv.get("context", "")
    session_id = kv.get("session")
    actor = _actor(kv)

    if trigger not in ALLOWED_TRIGGERS:
        _err("bm_sentinel: refused. Unknown trigger %r, allowed: %s"
             % (trigger, ", ".join(ALLOWED_TRIGGERS)))
        return EXIT_REFUSED

    store = _get_store()
    _require_project(store, project_id)
    knowledge = store.active_knowledge(project_id)
    procedural = store.active_procedural(project_id)
    recent = store.recent_interventions(project_id, COOLDOWN_N)

    try:
        decision, memories, reason = select(trigger, context, knowledge,
                                             procedural, recent)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED

    memory_ids = [m.get("id") for m in memories]
    reminder = None
    if decision == "inject":
        memory = memories[0]
        reminder = render_reminder(memory, reason)
        table = _table_for_memory(memory, knowledge, procedural)
        updated = store.mark_surfaced(table, [memory.get("id")])
        if not updated:
            # The row we selected is gone. Nothing is injected.
            #
            # This used to return here, BEFORE record_intervention, which
            # meant the ledger lost the event completely: the sentinel woke,
            # chose a memory, rendered it, and left no trace. That ledger is
            # the only evidence Phase 4 has, so an event it never sees is
            # worse than an event it records as odd. Downgrade to a silence
            # and record it with a reason naming the cause, rather than
            # vanishing. Exit stays 0 because nothing the caller did was
            # wrong and silence is a successful outcome of this command.
            #
            # REACHABILITY, stated rather than implied: this branch is
            # defensive and is NOT reachable through the command line today.
            # mark_surfaced matches on id alone, so it returns 1 even for a
            # row that was retired between the read and the write (measured:
            # retire the row, then mark_surfaced returns 1). Only a row
            # DELETED outright would return 0, and nothing in this project
            # deletes sentinel rows. It is kept because the alternative to a
            # defensive branch here is losing a ledger event, and it is
            # labelled because an untested branch that looks tested is the
            # thing this file spends most of its comments arguing against.
            decision, memory_ids, reminder = "silent", [], None
            reason = ("selected memory %s in %s was retired between the read "
                      "and the write, so nothing was surfaced"
                      % (memory.get("id"), table))

    store.record_intervention(project_id, trigger, decision, memory_ids,
                               reminder, reason, session_id, actor)

    if decision == "inject":
        _out(reminder)
    else:
        _out("SILENT: %s" % reason)
    return EXIT_OK


def cmd_judge(argv):
    usage = "usage: bm_sentinel.py judge <id> useful|noise [--by WHO]"
    known = {"by"}
    positional, kv = _parse(argv, known, known)
    if len(positional) < 2:
        _err(usage)
        return EXIT_USAGE
    intervention_id, judged = positional[0], positional[1]
    if judged == "unjudged":
        # The Store's enum includes "unjudged" because that is the DEFAULT
        # state of a fresh row, not because a human may set it. The command
        # line accepted it anyway, so `judge <id> unjudged` silently rewound a
        # verdict while the usage line said useful|noise. The calibration
        # ledger is the only record of whether a reminder helped, and an
        # erasure that reports success is the wrong shape of tool entirely.
        _err("bm_sentinel: refused. 'unjudged' is the state a new "
             "intervention starts in, not a verdict you can set. A judgement "
             "is useful or noise; use one of those, and see the ledger rather "
             "than rewinding it.")
        return EXIT_REFUSED
    by = kv.get("by")
    store = _get_store()
    try:
        ok = store.judge_intervention(intervention_id, judged, by)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    if not ok:
        _err("bm_sentinel: refused. No intervention %s found to judge."
             % intervention_id)
        return EXIT_REFUSED
    _out("judged %s as %s" % (intervention_id, judged))
    return EXIT_OK


def cmd_list(argv):
    usage = "usage: bm_sentinel.py list --project ID [--kind K] [--outcome O]"
    known = {"project", "kind", "outcome"}
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    kind = kv.get("kind")
    outcome = kv.get("outcome")
    store = _get_store()
    _require_project(store, project_id)
    knowledge = store.active_knowledge(project_id, kinds=[kind] if kind else None)
    procedural = store.active_procedural(project_id,
                                          outcomes=[outcome] if outcome else None)
    # content and attempt are the only free text on these rows, and they are
    # scrubbed for the same reason render_reminder scrubs: an agent reading
    # this listing cannot tell a forged line from a real one. Proven before
    # the fix by storing a memory whose content carried a newline plus the
    # text "knowledge FORGED kind=requirement ...": one stored row printed as
    # two listing lines, the second a perfect imitation of a real record. The
    # id, kind, outcome and surface_count fields are NOT scrubbed and do not
    # need to be: ids are store-generated hex, the two enums are validated
    # against a whitelist on write, and the count is an integer.
    learning = _load_learning()
    for row in knowledge:
        _out("knowledge %s kind=%s surface_count=%s content=%s"
             % (row.get("id"), row.get("kind"), row.get("surface_count"),
                learning.safe_display(row.get("content") or "",
                                      REMINDER_FIELD_LIMIT)))
    for row in procedural:
        _out("procedural %s outcome=%s surface_count=%s attempt=%s"
             % (row.get("id"), row.get("outcome"), row.get("surface_count"),
                learning.safe_display(row.get("attempt") or "",
                                      REMINDER_FIELD_LIMIT)))
    return EXIT_OK


def cmd_stats(argv):
    usage = "usage: bm_sentinel.py stats --project ID"
    known = {"project"}
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    store = _get_store()
    _require_project(store, project_id)
    stats = store.intervention_stats(project_id)
    ratio = stats.get("useful_ratio")
    ratio_str = "NO-DATA" if ratio is None else ("%.2f" % ratio)
    _out("total=%d injected=%d silent=%d useful=%d noise=%d unjudged=%d "
         "useful_ratio=%s"
         % (stats["total"], stats["injected"], stats["silent"],
            stats["useful"], stats["noise"], stats["unjudged"], ratio_str))
    return EXIT_OK


def cmd_retire(argv):
    usage = ("usage: bm_sentinel.py retire --project ID --id ID "
              "[--superseded-by ID]")
    known = {"project", "id", "superseded-by"}
    known |= set(_ACTOR_FLAGS)
    _positional, kv = _parse(argv, known, known)
    project_id = _require(kv, "project", usage)
    memory_id = _require(kv, "id", usage)
    superseded_by = kv.get("superseded-by")
    actor = _actor(kv)
    store = _get_store()
    _require_project(store, project_id)

    # retire_memory (section 3) takes a table name, not documented anywhere
    # in section 5's command table as a CLI flag. Determined here by
    # searching both active lists for the id, since --project is already
    # required for exactly this lookup.
    knowledge = store.active_knowledge(project_id)
    procedural = store.active_procedural(project_id)
    table = None
    if any(k.get("id") == memory_id for k in knowledge):
        table = "sentinel_knowledge"
    elif any(p.get("id") == memory_id for p in procedural):
        table = "sentinel_procedural"
    if table is None:
        _err("bm_sentinel: refused. No active memory %s found in project %s."
             % (memory_id, project_id))
        return EXIT_REFUSED

    try:
        ok = store.retire_memory(table, memory_id, superseded_by, actor)
    except ValueError as exc:
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    if not ok:
        _err("bm_sentinel: refused. retire_memory reported no row updated "
             "for %s." % memory_id)
        return EXIT_REFUSED
    _out("retired %s from %s" % (memory_id, table))
    return EXIT_OK


_COMMANDS = {
    "remember-knowledge": cmd_remember_knowledge,
    "remember-procedural": cmd_remember_procedural,
    "status-set": cmd_status_set,
    "check": cmd_check,
    "judge": cmd_judge,
    "list": cmd_list,
    "stats": cmd_stats,
    "retire": cmd_retire,
}

_USAGE = (
    "usage: bm_sentinel.py <command> [flags]\n"
    "commands: remember-knowledge, remember-procedural, status-set, check, "
    "judge, list, stats, retire\n"
    "  remember-knowledge --project ID --kind K --content C --source S "
    "[--session S]\n"
    "  remember-procedural --project ID --attempt A --outcome O "
    "[--diagnosis D] [--session S]\n"
    "  status-set --project ID --summary S [--risks R] [--session S]\n"
    "  check --project ID --trigger T [--context TEXT] [--session S]\n"
    "  judge <id> useful|noise [--by WHO]\n"
    "  list --project ID [--kind K] [--outcome O]\n"
    "  stats --project ID\n"
    "  retire --project ID --id ID [--superseded-by ID]"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _out(_USAGE)
        return EXIT_USAGE
    cmd = argv[0]
    handler = _COMMANDS.get(cmd)
    if handler is None:
        _err("bm_sentinel: unknown command %r" % cmd)
        _err(_USAGE)
        return EXIT_USAGE
    try:
        return handler(argv[1:])
    except Refused as exc:
        # One refusal shape for every command. The founder this tool is for
        # is not an engineer, and a Python traceback is not a message: it is
        # a failure to write one.
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED
    except ValueError as exc:
        # The Store raises ValueError by name for every invalid enum, and the
        # adversarial review found paths where that reached the terminal as a
        # raw traceback. The message is already precise; it just needed a
        # door to come out of.
        _err("bm_sentinel: refused. %s" % exc)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())

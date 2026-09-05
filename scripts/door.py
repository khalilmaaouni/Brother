#!/usr/bin/env python3
"""door: a plain English outcome becomes a canonical Work document.

NIGHT-02, the decomposition seam. work_record.py said plainly what it does
not do: it does not decompose English, because a script pretending to would
produce confident nonsense with a done_check attached. This is the other
half, and it still does not decompose English itself. It hands the outcome
to a model (or any command that speaks the same contract), and hands that
answer straight to work_record's own validation, which refuses anything
that would break the scheduler downstream. This module reimplements none of
that checking; it only drives the conversation until the answer passes it,
or gives up and says why.

Decomposer command, in order: --model-cmd, else env DOOR_MODEL_CMD, else the
host's own headless client (default_model_cmd below). Under Claude Code that
is the claude CLI in print mode (`claude -p`, reading the prompt on stdin:
verified against `claude --help` on 2026-08-30, -p/--print with no positional
prompt reads from stdin, output-format defaults to plain text on stdout).
Under Codex it is `codex exec --json`, the argv model_worker.py already ships,
because a Codex-only machine has no claude binary; see CODEX_SANDBOX_HINT for
the sandbox that governs either of them inside a codex turn.

REFUSAL IS STILL THE FEATURE, borrowed whole from work_record.py: a refusal
is reported back to the decomposer verbatim and it gets another attempt, up
to --max-retries times, and after that the run fails loudly rather than
storing something unschedulable.
"""
import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import work_record as WR  # noqa: E402
import brother_paths  # noqa: E402
import model_worker as MW  # noqa: E402

NODATA = "NO-DATA"

#: EVAD plan E2: the ceiling above which an "outcome" is really a pasted
#: document. 2,000 characters holds every real outcome sentence this estate
#: has recorded while refusing the 20,000-character probe that ran a model
#: call past two minutes.
MAX_OUTCOME_CHARS = 2000
DEFAULT_MODEL_CMD = ["claude", "-p"]
UNIT_COUNT_HINT = "2 to 9 units"

#: WHY A CODEX HOST GETS A DIFFERENT DEFAULT, and it is measured rather than
#: assumed (2026-09-05, codex-cli 0.153.0-alpha.5, the app-bundled binary;
#: ~/.claude/evidence/lane-codex-door-sandbox-probe.log,
#: lane-codex-door-nested-probe.log and lane-codex-door-step6.log).
#:
#: `claude -p` is wrong under Codex twice over. A Codex-only machine has no
#: `claude` binary at all, and Codex's own `workspace-write` sandbox blocks
#: EVERY socket a model-generated command opens, loopback included:
#:     BLOCKED 1.1.1.1   PermissionError [Errno 1] Operation not permitted
#:     BLOCKED 127.0.0.1 PermissionError [Errno 1] Operation not permitted
#: so the child cannot reach any API. It does not say so: it reports itself
#: "Not logged in" and exits 1, three attempts running, which is the whole of
#: the 2026-09-05 "door: refused after 3 attempt(s), store untouched"
#: finding.
#:
#: SO THE DEFAULT IS THE HOST'S OWN CLIENT, and it is expected to fail inside
#: a sandboxed turn rather than to rescue one. A nested `codex exec` cannot
#: start inside a codex turn at all, with or without the network, measured in
#: the signed-in run itself:
#:     Error: failed to initialize in-process app-server client:
#:            Operation not permitted (os error 1)
#: It stays the default because a turn at `danger-full-access` is a different
#: sandbox and this is the argv that host would want, and because the refusal
#: it produces in four seconds NAMES the route that works. In the signed-in
#: run of 2026-09-05 the agent driving the turn read exactly that message and
#: went on to reach a receipt through the DOOR_MODEL_CMD seam.
CODEX_SANDBOX_HINT = (
    "under Codex the engine's model calls are child processes of a sandboxed "
    "turn: a nested `codex exec` cannot start inside one (in-process "
    "app-server, Operation not permitted) and any other model CLI has every "
    "socket blocked, so it reports itself not logged in. The route that "
    "works needs no model call: write the units yourself and point "
    "DOOR_MODEL_CMD at a command that prints them, for example "
    "DOOR_MODEL_CMD=\"cat plan.json\" (a JSON list of units with id, "
    "objective, done_check, writes and deps). See "
    "docs/codex/SMOKE-RUNBOOK.md step 6")


def default_model_cmd(env=None):
    """The decomposer nobody named, per host. Under Codex that is Codex's own
    headless exec (the argv model_worker already ships and documents), never
    `claude -p`; under anything else it is unchanged."""
    if MW.model_client(env) == brother_paths.CODEX:
        return list(MW.CODEX_ARGV)
    return list(DEFAULT_MODEL_CMD)


#: How much of a failing decomposer's stderr travels with the refusal. The
#: whole of it is already printed above the refusal; this is the part that
#: has to survive into the message brother_run stores and a receipt quotes.
STDERR_TAIL_CHARS = 400


def stderr_tail(text):
    """The last STDERR_TAIL_CHARS of a failing child's stderr, on one line, or
    a sentence saying it wrote none."""
    body = " ".join((text or "").split())
    if not body:
        return "it wrote nothing to stderr either"
    if len(body) > STDERR_TAIL_CHARS:
        body = "..." + body[-STDERR_TAIL_CHARS:]
    return "its last words were: %s" % body


def is_codex_cmd(cmd):
    """True when this command line is Codex's own headless exec."""
    return bool(cmd) and os.path.basename(cmd[0]) == "codex"


def decomposer_text(cmd, stdout):
    """What the decomposer actually said, from its stdout.

    `codex exec --json` prints JSONL events rather than the answer, so the
    agent's own last message is pulled out of them with the parser
    model_worker already owns and tests. Every other command speaks plain
    text on stdout and is returned unchanged."""
    if is_codex_cmd(cmd) and "--json" in cmd:
        claim, _usage = MW._parse_codex_output(stdout)
        return claim if claim else stdout
    return stdout


#: P2 (persona integration, docs/plan/PERSONA-INTEGRATION-PLAN-2026-09-04.md
#: gap P2; docs/plan/PERSONA-INTEGRATION-ROWS-2026-09-04.json). A pack
#: manifest is data, not code: scripts/packs/<lens>.json, read here and
#: nowhere wired into build_prompt yet (that is P3 and P5's row, not this
#: one). PACKS_DIR is a module-level default computed once from HERE, which
#: never changes after import, so it is safe as a default argument value
#: (unlike a mutable module constant reassigned later).
PACKS_DIR = os.path.join(HERE, "packs")

#: The seven fields the persona document's pack manifest section (5.1) and
#: this row's own brief name: lens id, detection signals, the one
#: profession-aware challenge question (4.1 stage 3), the question budget
#: (4.2), the evidence families a promotion-class unit requires (6.1),
#: the pack-specific receipt fields (12.6), and the forcing classes that
#: demand a human decision (25.2). Order is the order load_pack reports a
#: missing key in.
PACK_REQUIRED_KEYS = (
    "lens",
    "version",
    "detection_signals",
    "challenge_question",
    "question_budget",
    "required_evidence_families",
    "receipt_fields",
    "forcing_classes",
)


def load_pack(name, packs_dir=PACKS_DIR):
    """The manifest at `packs_dir`/<name>.json, refused (ValueError, the
    missing key(s) named) unless every key in PACK_REQUIRED_KEYS is present.
    A manifest missing a key is unschedulable the same way work_record.py
    refuses a unit missing `owns` or `done_check`: the caller finds out by
    name, not by a KeyError three calls later."""
    path = os.path.join(packs_dir, "%s.json" % name)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("pack %r could not be read from %r: %s"
                         % (name, path, exc))
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ValueError("pack %r (%s) is not valid JSON: %s"
                         % (name, path, exc))
    if not isinstance(manifest, dict):
        raise ValueError("pack %r (%s) is not a JSON object" % (name, path))
    missing = [k for k in PACK_REQUIRED_KEYS if k not in manifest]
    if missing:
        raise ValueError("pack %r (%s) is missing required key(s): %s"
                         % (name, path, ", ".join(missing)))
    return manifest


def pack_manifests(packs_dir=PACKS_DIR):
    """{lens name: manifest}, one entry per *.json file under `packs_dir`,
    each validated the same way load_pack validates one. Raises the same
    ValueError as load_pack, naming the first invalid file, rather than
    returning a partial map that hides a broken pack."""
    try:
        names = sorted(f[:-len(".json")] for f in os.listdir(packs_dir)
                       if f.endswith(".json"))
    except OSError as exc:
        raise ValueError("packs directory %r could not be listed: %s"
                         % (packs_dir, exc))
    return {name: load_pack(name, packs_dir=packs_dir) for name in names}


#: P3 (persona integration, gap P3, persona doc 3.3: "Infer from
#: repository/work and let the human correct the inference"). Files that
#: plausibly NAME a dependency, worth reading for a pack's manifest_strings
#: signal (e.g. "mlflow" inside requirements.txt). Kept to an allowlist of
#: basenames so this never opens an arbitrary file the tree happens to list.
MANIFEST_FILENAMES = frozenset((
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "environment.yml", "environment.yaml", "Pipfile", "package.json",
))

#: How many matched paths the intent screen's assumption line names before
#: it falls back to "and N more": a repository with forty notebooks should
#: not turn one line into a wall of filenames. The Work document itself
#: still keeps the full matched_paths list.
MAX_ASSUMPTION_PATHS = 3


def _path_matches_signal(rel_path, signal):
    """True when `rel_path` (as list_repo_files returns it: repository-
    relative, forward slashes) matches one pack path signal. A signal
    ending in '/' (e.g. 'mlruns/') matches any path with that directory
    name among its parts, at any depth; anything else is matched with
    fnmatch against the path's basename, so 'dvc.yaml' matches at any
    depth and '*.ipynb' matches a notebook wherever it lives."""
    sig = str(signal)
    parts = str(rel_path).split("/")
    if sig.endswith("/"):
        return sig.rstrip("/") in parts[:-1]
    return fnmatch.fnmatch(parts[-1], sig)


def _manifest_string_hit(root, listed_files, needle):
    """The first path in `listed_files` that is shaped like a manifest
    (MANIFEST_FILENAMES) and whose content contains `needle`, or None.
    Reads only files list_repo_files already found and only ones on that
    allowlist, so this never opens a path the caller has not already seen."""
    for rel in listed_files:
        if os.path.basename(rel) not in MANIFEST_FILENAMES:
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8",
                     errors="ignore") as fh:
                content = fh.read()
        except OSError as exc:
            # Skipping one unreadable manifest among many candidates is the
            # right behaviour (the scan keeps going), but a bare `continue`
            # hid which file and why; naming it here on stderr is the fix.
            print("door: %s could not be read (%s), skipped" % (rel, exc),
                  file=sys.stderr)
            continue
        if needle in content:
            return rel
    return None


#: The substrate pack, persona doc 5.2 ("core for scope, evidence, Vault
#: and human acceptance", named in BOTH of that section's own example
#: compositions). It carries no detection signals of its own, so it is
#: never MATCHED into a composition: it is APPENDED last, under whatever
#: was matched, as the base every other lens loads beside.
BASE_LENS = "core"


def _pack_matches(root, listed_files, manifest):
    """(matched_paths, distinct_signals) for one pack manifest against one
    tree listing. `distinct_signals` counts SIGNALS that fired, not files
    they matched: a repository with forty notebooks has fired one signal,
    and a repository matching '*.ipynb' plus 'mlruns/' plus an 'mlflow'
    manifest string has fired three. That count is what specificity means
    below, so a pack cannot out-rank another by matching one signal a
    great many times."""
    signals = (manifest or {}).get("detection_signals") or {}
    matched = []
    distinct = 0
    for sig in (signals.get("paths") or []):
        hits = [rel for rel in listed_files if _path_matches_signal(rel, sig)]
        if hits:
            distinct += 1
            matched.extend(hits)
    for needle in (signals.get("manifest_strings") or []):
        hit = _manifest_string_hit(root, listed_files, needle)
        if hit:
            distinct += 1
            matched.append(hit)
    seen = set()
    uniq = [m for m in matched if not (m in seen or seen.add(m))]
    return uniq, distinct


def infer_lenses(root, listed_files, packs_dir=PACKS_DIR):
    """[(lens_name, matched_paths), ...]: EVERY pack whose detection_signals
    match this tree, most specific first, with BASE_LENS appended last.

    Persona doc 5.2, "Pack selection must be compositional": a dbt revenue
    metric change loads analytics AND data-engineering AND business-analysis
    AND qa-automation AND core, not whichever of them sorts first by name.
    The version this replaces returned the FIRST pack in sorted lens-name
    order, so with thirteen packs installed "architect" won every tree it
    matched and a tree that is both data engineering and data science got
    one lens.

    Order is specificity: the number of DISTINCT signals a pack matched
    (descending), then the pack's own optional integer `priority`
    (descending, absent reads 0), then the lens name (ascending) so the
    order is total and reproducible rather than dictionary-dependent.

    Returns [] when nothing matches: the one-visible-Brother rule (persona
    doc 3.3, 30.1: no new command, no new mode) means no match asks no
    question and states no assumption, rather than guessing. core is
    appended only when something else matched, for the same reason: an
    unmatched tree gets no assumption line at all."""
    try:
        manifests = pack_manifests(packs_dir)
    except ValueError as exc:
        print("door: %s: no lens could be inferred" % exc, file=sys.stderr)
        return []
    scored = []
    for name in sorted(manifests):
        if name == BASE_LENS:
            continue
        matched, distinct = _pack_matches(root, listed_files, manifests[name])
        if not matched:
            continue
        raw_priority = manifests[name].get("priority")
        try:
            priority = int(raw_priority or 0)
        except (TypeError, ValueError):
            print("door: pack %r has a non-integer priority %r, read as 0"
                  % (name, raw_priority), file=sys.stderr)
            priority = 0
        scored.append((-distinct, -priority, name, matched))
    if not scored:
        return []
    scored.sort()
    out = [(name, matched) for _distinct, _priority, name, matched in scored]
    if BASE_LENS in manifests:
        out.append((BASE_LENS, []))
    return out


def infer_lens(root, listed_files, packs_dir=PACKS_DIR):
    """(lens_name_or_None, matched_paths): the SINGLE most specific lens
    infer_lenses() inferred, or (None, []) when nothing matched. Kept as
    the accessor for the call sites that carry one lens (the Work
    document's own `lens_inferred.lens`, the challenge question's primary
    pack); the composed list is infer_lenses() above. BASE_LENS is never
    returned here: it is appended last by infer_lenses and this reads the
    first entry."""
    inferred = infer_lenses(root, listed_files, packs_dir=packs_dir)
    if not inferred:
        return None, []
    return inferred[0]


def _lens_entry(entry):
    """(lens, matched_paths) from either shape a composed inference is
    carried in: the (name, paths) tuple infer_lenses returns, or the
    {"lens", "matched_paths"} dict the Work document stores. Anything else
    reads (None, []), so a malformed record states no assumption rather
    than crashing the intent screen."""
    if isinstance(entry, dict):
        return entry.get("lens"), list(entry.get("matched_paths") or [])
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return entry[0], list(entry[1] or [])
    return None, []


def lenses_assumption_line(inferred):
    """The intent screen's assumption line for a COMPOSED inference: every
    inferred lens with its own matched paths, each capped at
    MAX_ASSUMPTION_PATHS names exactly as the single-lens line below caps
    them. `inferred` is what infer_lenses returned, or the record's own
    `lens_inferred.lenses` list of dicts.

    A lens with no matched paths is left out, which is how BASE_LENS (no
    signals of its own) stays off the line while still composing into the
    receipt fields and evidence families below. "" when nothing was
    inferred at all, so a plain repository's intent screen is unchanged."""
    parts = []
    for entry in (inferred or []):
        lens, matched = _lens_entry(entry)
        if not lens or not matched:
            continue
        shown = list(matched[:MAX_ASSUMPTION_PATHS])
        extra = len(matched) - len(shown)
        names = ", ".join(shown)
        if extra > 0:
            names += " and %d more" % extra
        parts.append("%s work (found %s)" % (lens, names))
    if not parts:
        return ""
    # One inferred lens reads EXACTLY as the single-lens line below has
    # always read, word for word; composition only ever adds clauses.
    return ("Assumed: %s; say otherwise to change it" % ", ".join(parts))


#: The two manifest keys composed by union across the inferred packs
#: (persona doc 5.1 "pack-specific receipt fields" and "mandatory evidence
#: principles"; 5.2 "No user should need to know that composition occurred
#: unless they inspect the receipt"). Order is the composed order, first
#: occurrence kept, so the most specific pack's fields lead and core's
#: base fields land underneath.
UNION_KEYS = ("receipt_fields", "required_evidence_families")


def _field_key(value):
    """The identity of one receipt field or evidence family for deduping a
    union: the string itself when a pack lists plain names (the shape both
    landed packs use today), or its `name`/`id` when a pack lists the
    fuller {name, meaning, how it is filled} object. "" for anything else,
    which the union skips rather than deduping everything onto one key."""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def pack_union(lens_names, packs_dir=PACKS_DIR):
    """{"receipt_fields": [...], "required_evidence_families": [...]}: the
    UNION of those keys across `lens_names`, in composed order, each entry
    kept verbatim in its first occurrence and deduped by _field_key.

    This is the receipt half of compositional selection: a tree that is
    both data engineering and data science owes the fields and evidence
    families of BOTH packs, plus core's, not the first pack's alone. A
    pack that cannot be read is named on stderr and left out, never
    silently dropped and never a crash: an unreadable pack must not cost
    the other packs' fields."""
    out = {key: [] for key in UNION_KEYS}
    seen = {key: set() for key in UNION_KEYS}
    for name in (lens_names or []):
        try:
            pack = load_pack(name, packs_dir=packs_dir)
        except ValueError as exc:
            print("door: %s: its fields were left out of the composed union"
                  % exc, file=sys.stderr)
            continue
        for key in UNION_KEYS:
            for value in (pack.get(key) or []):
                ident = _field_key(value)
                if not ident or ident in seen[key]:
                    continue
                seen[key].add(ident)
                out[key].append(value)
    return out


def forcing_class_triggers(pack):
    """[(class_id, pattern), ...] built from one pack's own forcing_classes:
    one word-bounded pattern per entry, the words taken from the entry's
    `id` (underscore-separated, so "threshold_change" becomes the phrase
    "threshold change"), so "promotional" can never fire "promotion".

    Lives here rather than in receipt_door.py because both readers need it
    and receipt_door already imports this module (the reverse import would
    be a cycle): receipt_door.py builds RISK_TRIGGERS from it, and
    compute_challenge below asks whether a composed pack's forcing classes
    fire on a unit before spending a question on that pack."""
    triggers = []
    for entry in ((pack or {}).get("forcing_classes") or []):
        class_id = str((entry or {}).get("id") or "").strip()
        words = [w for w in class_id.split("_") if w]
        if not words:
            continue
        triggers.append(
            (class_id, r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"))
    return triggers


def lens_assumption_line(lens, matched_paths):
    """The intent screen's assumption line for a P3 inference, or "" when
    nothing was inferred (no lens, or an empty match list). `matched_paths`
    is capped to MAX_ASSUMPTION_PATHS names for display; the Work document
    itself carries the full list."""
    if not lens or not matched_paths:
        return ""
    shown = list(matched_paths[:MAX_ASSUMPTION_PATHS])
    extra = len(matched_paths) - len(shown)
    names = ", ".join(shown)
    if extra > 0:
        names += " and %d more" % extra
    return ("Assumed: %s work (found %s); say otherwise to change it"
            % (lens, names))


#: P5 (persona integration, gap P5, persona doc 4.1 stage 3: "What is the
#: pre-registered success metric and holdout/evaluation rule?"; 12.3 DS-05,
#: the cherry-pick failure a metric chosen after the results were seen
#: causes). What counts as "this unit's objective is about promotion or
#: evaluation" for the challenge question to matter at all: read off the
#: pack manifest's own "promotion_eval_words" key when it carries one, else
#: this fixed default, taken from the doc's own words above and from the
#: data-science pack's forcing_classes ("promotion", "threshold change").
#: A fixed list, the same auditable shape this module already uses for
#: detection_signals, rather than a second parser of the objective's
#: English.
DEFAULT_PROMOTION_EVAL_WORDS = (
    "promot", "evaluat", "eval ", "compar", "metric", "benchmark",
    "baseline", "holdout", "threshold",
)


def _unit_needs_challenge(unit, pack):
    """True when `unit`'s objective is about promotion or evaluation, by
    pack["promotion_eval_words"] (or DEFAULT_PROMOTION_EVAL_WORDS when the
    pack carries none), a plain substring test against the lowercased
    objective. A unit with no objective at all never needs the challenge."""
    objective = str((unit or {}).get("objective") or "").lower()
    if not objective:
        return False
    words = pack.get("promotion_eval_words") or DEFAULT_PROMOTION_EVAL_WORDS
    return any(w in objective for w in words)


#: P5's three tree signals, in the order find_metric_in_tree tries them: a
#: metrics FILE, an eval SCRIPT, then a README LINE naming the metric or
#: holdout by name (the brief's own three named signals: "a metrics file,
#: an eval script, a README line"). Basenames only, matched against
#: list_repo_files' own repository-relative output, the same
#: MANIFEST_FILENAMES shape above.
METRIC_FILE_BASENAMES = frozenset((
    "metrics.json", "metrics.yaml", "metrics.yml",
    "eval_results.json", "eval_results.txt", "eval_results.yaml",
))
METRIC_SCRIPT_BASENAMES = frozenset((
    "eval.py", "evaluate.py", "evaluation.py",
))
METRIC_README_NEEDLES = ("metric", "holdout")


def find_metric_in_tree(root, listed_files):
    """The repository-relative path of the first of P5's three signals
    found in `listed_files` (a metrics file, then an eval script, then a
    README mentioning the metric or holdout by name, case-insensitively),
    or None when none of the three is present. A README hit is read the
    same tolerant way _manifest_string_hit already reads a manifest above:
    read-only, errors ignored, since a file this module cannot decode
    should not crash a search that only exists to avoid an unnecessary
    question."""
    for rel in listed_files:
        if os.path.basename(rel) in METRIC_FILE_BASENAMES:
            return rel
    for rel in listed_files:
        if os.path.basename(rel) in METRIC_SCRIPT_BASENAMES:
            return rel
    for rel in listed_files:
        base = os.path.basename(rel)
        if not base.lower().startswith("readme"):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8",
                     errors="ignore") as fh:
                content = fh.read().lower()
        except OSError as exc:
            # Skipping one unreadable README among candidates is the right
            # behaviour (the scan keeps going); naming it here on stderr
            # beats a bare `continue` that hid which file and why.
            print("door: %s could not be read (%s), skipped" % (rel, exc),
                  file=sys.stderr)
            continue
        if any(needle in content for needle in METRIC_README_NEEDLES):
            return rel
    return None


#: The ceiling on a COMPOSED intake, persona doc 4.2's own table: the
#: largest per-shape budget in it is 6 ("Production infra change" and
#: "Architecture decision", both 2 to 6). Composition sums the inferred
#: packs' own question_budget maxima and then stops here, so thirteen packs
#: matching one tree can never turn one intake into thirteen questions.
#: 4.2's own metric, quoted: "The metric is not few questions. It is few
#: wasted questions."
QUESTION_BUDGET_CEILING = 6


def _lens_name_list(lens_name):
    """[lens name, ...] from either shape a caller carries: the single name
    P5 shipped, or the composed list infer_lenses returns (names, or the
    (name, paths) tuples themselves). [] for None or empty."""
    if not lens_name:
        return []
    if isinstance(lens_name, str):
        return [lens_name]
    names = []
    for entry in lens_name:
        if isinstance(entry, str):
            names.append(entry)
            continue
        name, _matched = _lens_entry(entry)
        if name:
            names.append(name)
    return names


def _question_budget_max(pack):
    """The pack's own question_budget["max"] as an int, 0 when it carries
    none or carries something that is not a number (a budget nobody can
    read is not a budget, and reading it as 0 lets the composed limit fall
    back to the one primary question below rather than inventing a cap)."""
    budget = (pack or {}).get("question_budget") or {}
    try:
        return max(0, int(budget.get("max")))
    except (TypeError, ValueError):
        return 0


def _forcing_class_fires(pack, unit):
    """True when any of `pack`'s own forcing classes fires on `unit`'s
    objective, by the same word-bounded patterns receipt_door.py parks a
    unit on (forcing_class_triggers, above). This is the test a SECONDARY
    composed pack must pass before it costs a question: the most specific
    lens asks its question because it is the work; another pack only earns
    one when something it declares dangerous is actually in this unit."""
    objective = str((unit or {}).get("objective") or "").lower()
    if not objective:
        return False
    return any(re.search(pattern, objective)
               for _class_id, pattern in forcing_class_triggers(pack))


def compute_challenge(root, listed_files, lens_name, units, packs_dir=PACKS_DIR):
    """(challenge_assumption, pending_challenge), P5's own decision: at most
    one of the pair is ever non-None. `lens_name` is one lens name (as P5
    shipped it) or the composed list infer_lenses now returns.

    COMPOSED (persona doc 5.2): the most specific lens's challenge question
    comes first, on the same terms as before (a real question, not core's
    literal "NO-DATA", and at least one unit whose objective needs it,
    _unit_needs_challenge). Every OTHER inferred pack adds its question
    only when one of its own forcing classes fires on a unit
    (_forcing_class_fires), so composition cannot turn a five-pack tree
    into five questions about work nobody is doing. The composed list is
    then cut to the sum of those packs' question_budget maxima, capped at
    QUESTION_BUDGET_CEILING.

    When the tree already answers the question (find_metric_in_tree), that
    is an ASSUMPTION, stated on the intent screen, never asked; otherwise
    the PENDING dict carries the primary question in the "lens" and
    "question" keys P5 shipped, plus every composed question in
    "questions", still budgeted to interactive mode only (brother_run.py's
    own rule, not this module's: this function only decides which
    questions are warranted, never whether any is actually asked)."""
    names = _lens_name_list(lens_name)
    if not names:
        return None, None
    composed = []
    budget = 0
    for name in names:
        try:
            pack = load_pack(name, packs_dir=packs_dir)
        except ValueError as exc:
            print("door: %s: no challenge question could be computed" % exc,
                  file=sys.stderr)
            continue
        question = str(pack.get("challenge_question") or "")
        if not question or question == NODATA:
            continue
        if not any(_unit_needs_challenge(u, pack) for u in units):
            continue
        if composed and not any(_forcing_class_fires(pack, u) for u in units):
            continue
        budget += _question_budget_max(pack)
        composed.append({"lens": name, "question": question})
    if not composed:
        return None, None
    # A pack that declares no readable maximum still gets its own one
    # question, exactly as P5 shipped it; the cap only ever cuts a
    # composition, it never silences the primary lens.
    limit = min(budget, QUESTION_BUDGET_CEILING) or 1
    composed = composed[:limit]
    hit = find_metric_in_tree(root, listed_files)
    if hit:
        return ({"lens": composed[0]["lens"], "path": hit,
                 "lenses": [entry["lens"] for entry in composed]}, None)
    pending = dict(composed[0])
    pending["questions"] = composed
    return None, pending


def challenge_assumption_line(assumption):
    """The intent screen's line for a P5 tree-answered challenge question,
    or "" when nothing was found (`assumption` is None). Mirrors
    lens_assumption_line's own shape and correction wording."""
    if not assumption:
        return ""
    lens = assumption.get("lens") or NODATA
    path = assumption.get("path") or NODATA
    return ("Assumed: the %s pack's pre-registered metric is already "
            "documented at %s; say otherwise to change it" % (lens, path))


#: How many existing paths to name in the prompt. A bound, not a real limit
#: on the repo: naming every file in a large tree would blow the outcome
#: budget this module already enforces elsewhere, and the decomposer only
#: needs enough to stop guessing a filename that does not exist.
MAX_LISTED_FILES = 200


def list_repo_files(root, limit=MAX_LISTED_FILES):
    """Repository-relative paths under `root`, `.git` and generated noise
    excluded, for grounding the decomposer in what is ACTUALLY there.

    Returns [] rather than raising when `root` cannot be walked: an outcome
    is still decomposable without this, just more likely to guess a path
    that is not real (measured live 2026-08-31, E7: a decomposer blind to
    the target wrote a unit whose write scope was a bare '*.py' glob, since
    it could not name the one file that actually existed)."""
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                          if d not in (".git", "__pycache__", "node_modules")]
            for name in filenames:
                if name.endswith((".pyc", ".pyo")):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                out.append(rel.replace(os.sep, "/"))
                if len(out) >= limit:
                    return sorted(out)
    except OSError:
        return []
    return sorted(out)


def build_prompt(outcome, refusal=None, existing_files=None):
    """The unit contract, stated plainly, in the words a model reads rather
    than the keys work_record.py stores. normalize_unit() below is the
    bridge between the two vocabularies."""
    lines = [
        "Outcome: %s" % outcome,
        "",
    ]
    if existing_files:
        lines.append("Files that already exist in the target repository "
                     "(name these exactly when a unit changes one of them, "
                     "rather than guessing a plausible name):")
        lines.extend("  %s" % p for p in existing_files)
        lines.append("")
    elif existing_files is not None:
        lines.append("The target repository has no files yet; every unit "
                     "writes a new path.")
        lines.append("")
    lines += [
        "Decompose this outcome into %s of work that a scheduler can run."
        % UNIT_COUNT_HINT,
        "Each unit costs one full separate turn to run and verify, so use "
        "the FEWEST units the real dependency structure requires. A small, "
        "single-file change is usually 1 to 2 units: one that makes the "
        "change, and one that writes AND runs its own proof (its done_check "
        "may itself run the test; that does not need a separate unit). "
        "Never add a unit whose only job is to locate a file, record a "
        "path, or re-run a check another unit's own done_check already "
        "covers. Reach for 3 or more units only when parts are genuinely "
        "independent, can run in parallel, or carry separate risk.",
        "Answer with PURE JSON: a JSON list of unit objects. No prose, no "
        "explanation, no markdown, no code fences. Just the list.",
        "",
        "Every unit object needs exactly these fields:",
        "  id: a short string identifying the unit, unique within the list",
        "  objective: one sentence describing what the unit accomplishes",
        "  done_check: a single shell command that exits 0 once the unit is "
        "done. Use python3, never python, and name only programs that exist "
        "on a normal machine; a check whose command is not installed can "
        "never pass. Write it as ONE LINE: no embedded newline escapes, no "
        "here-doc, no multi-line script. It must FAIL right now, before the "
        "unit's work is done, and PASS once the work is done; it must not "
        "depend on the work having already happened.",
        "  writes: a list of CONCRETE repository-relative file paths this unit "
        "may write. Name the exact file (an existing one you can see, or the "
        "exact new path you intend to create); never a glob or wildcard "
        "pattern such as *.py or **/*.py, which the scope check that runs "
        "after this unit cannot match against the file it actually wrote.",
        "  deps: a list of other units' ids this unit depends on (may be empty)",
        "  evidence_family: OPTIONAL. One of E1 through E18, naming which "
        "evidence family the done_check belongs to. Omit if unsure. "
        "THREE OF THEM CARRY A CONTRACT THE CHECK ITSELF MUST MEET, and a "
        "unit that names one without meeting it is scored NO-DATA however "
        "green its check exits: E2 and E8 require the done_check to write a "
        "numbers_manifest path into $BROTHER_RUN_DIR/evidence/<unit id>.json, "
        "and E18 requires it to write metric, value, baseline, seed and "
        "holdout_id into that same file. Name E2, E8 or E18 ONLY when this "
        "unit's own done_check writes that file; otherwise omit the field.",
        "  oracle_source: OPTIONAL. One of requirement, business_rule, "
        "independent_query, reference_impl, prior_release, "
        "generated_from_impl, human_observation, none: what the "
        "done_check's expected result was checked against. Use "
        "generated_from_impl only when this same decomposition also wrote "
        "the expected value the check compares against.",
        "",
        "Rules: ids are unique. Every entry in deps names another unit in this "
        "same list (no dangling dependency). The deps taken together must not "
        "form a cycle. Every unit needs a non-empty done_check and at least "
        "one path in writes.",
    ]
    if refusal:
        lines += [
            "",
            "The previous answer was refused for exactly this reason:",
            refusal,
            "",
            "Fix exactly this and answer again with pure JSON.",
        ]
    return "\n".join(lines) + "\n"


def build_check_rewrite_prompt(objective, original_check, stderr_text,
                               refusal_reason=None):
    """The single-unit companion to build_prompt() above: asked only when a
    generated done_check turned out to be unrunnable (measured live
    2026-09-03: the planner twice wrote a multi-line `python3 -c "..."`
    check with literal backslash-n sequences inside the string, a syntax
    error before and after any work), never as a second attempt at the
    whole plan. Same contract as build_prompt's own done_check field,
    stated narrower: one unit, one replacement command, nothing else about
    the plan changes.

    `refusal_reason`, when given, is the SECOND and last ask for this same
    unit (E88): the previous replacement parsed but guard_adopted_check
    refused it, and the reason it names is quoted back so the planner can
    write a different command instead of the same one again. Measured
    live in two independent 2026-09-04 trials, the outcome phrasing
    "raises X with a clear message" produced a replacement carrying a `;`,
    which was refused with nothing said back to the planner. Only the
    REASON is quoted, never the refused command: that command came from
    the same untrusted reply this is refusing, per guard_adopted_check's
    own contract."""
    lines = [
        "One unit of a work plan has a done_check that cannot run at all.",
        "",
        "Unit objective: %s" % objective,
        "Original done_check: %s" % original_check,
        "",
        "Running that command, before any work on the unit, produced this "
        "output:",
        (stderr_text or "(no output was captured)").strip(),
        "",
        "Write a REPLACEMENT done_check for this SAME unit. Requirements:",
        "  - ONE LINE: a single shell command, no embedded newline escapes, "
        "no here-doc, no multi-line script.",
        "  - It must FAIL right now, before the unit's work is done, and "
        "PASS once the work is done; it must not depend on the work having "
        "already happened.",
        "  - Use python3, never python, and name only programs that exist "
        "on a normal machine.",
        "  - ONE COMMAND, not two: no `;`, no `&&`, no `||`, no `|`, no "
        "backticks and no `$(...)`. The check runs as a single command and "
        "any of those gets it refused outright. For an outcome like "
        "\"raises X with a clear message\", put the whole assertion inside "
        "one python3 -c '...' program, using commas between statements "
        "where you would reach for a separator.",
        "",
        "Answer with PURE JSON: a single JSON object of the form "
        '{"done_check": "..."}. No prose, no explanation, no markdown, no '
        "code fences.",
    ]
    if refusal_reason:
        lines += [
            "",
            "Your previous replacement for this same unit was REFUSED "
            "before it ever ran: %s." % refusal_reason,
            "Write a DIFFERENT command that does not break that rule. This "
            "is the last attempt; if it is refused again the unit is "
            "refused and no worker starts on it.",
        ]
    return "\n".join(lines) + "\n"


def strip_code_fences(text):
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def resolve_done_check_interpreter(cmd):
    """A generated done_check names an interpreter that must exist on THIS
    machine. The harsh EVAD trials (2026-08-31) lost five of six lanes to one
    signature: the decomposer wrote `python`, this machine has only `python3`,
    every check exited 127, and the drain retried a missing-tool problem as if
    it were a code defect until the round ceiling. Resolve the common case
    (`python` to `python3` when only python3 exists) before the plan is
    accepted, so a missing interpreter never masquerades as broken code.
    Returns (resolved_cmd, note_or_None); note names any rewrite, and None
    means the command was left untouched."""
    if not cmd or not isinstance(cmd, str):
        return cmd, None
    tok = cmd.lstrip().split(None, 1)
    if not tok:
        return cmd, None
    prog = tok[0]
    if prog == "python" and shutil.which("python") is None \
            and shutil.which("python3") is not None:
        resolved = cmd.replace("python", "python3", 1)
        return resolved, "rewrote a done_check interpreter python to python3"
    return cmd, None


#: E78 (security review 2026-09-03 night run): scripts/brother_run.py's
#: _rewrite_broken_checks is the ONE place a fresh model reply becomes a
#: unit's done_check with only resolve_done_check_interpreter (above)
#: between them, and that only renames python to python3, so a plan record
#: whose text steers the planner could make the engine run an arbitrary
#: shell command. Since the security review of 2026-09-04 (Critical) this
#: guard ALSO fences a Work document loaded from disk: brother_run.py's
#: --resume takes a run directory by path and --continue finds one by its
#: own recorded cwd, so a record a crafted repository ships would otherwise
#: reach _reexecute_check's shell=True unfiltered. Both callers are records
#: the door itself asked for as "a single shell command" (build_prompt),
#: which is the same contract this guard states.
#:
#: What is still NOT filtered through this: the hand-written roadmap corpus
#: in docs/plan/READINESS-ROADMAP-2026-08-29.json, which chains `cd X && Y`
#: and `A; B` constantly and legitimately. board_status.py reads that file;
#: brother_run.py never does, and an engine rule read off one run already
#: broke the product's own acceptance contract once by trying to police it.
#:
#: Interpreter allowlist derived from the plan corpus itself:
#: `grep -o '"done_check": "[^ ]*' docs/plan/READINESS-ROADMAP-2026-08-29.json
#: | sort | uniq -c | sort -rn | head -20` (run 2026-09-03) showed real
#: command-shaped first tokens python3 (80), sh (9), grep (4), gh (3); "test"
#: (POSIX file-test) is this module's own rewrite-stub convention throughout
#: scripts/test_brother_run.py ("test -f fixed.txt"). pytest, bash, git,
#: make are the brief's own stated floor with no corpus hit yet.
#: "true" and "false" are the same kind of entry as "test": POSIX no-op
#: builtins used as a stub check throughout scripts/test_crash_resume.py and
#: scripts/test_brother_run.py. Neither takes a command, so neither can run
#: one; they widen what is ALLOWED without widening what can execute.
ADOPTED_CHECK_ALLOWED_INTERPRETERS = frozenset((
    "python3", "pytest", "sh", "bash", "git", "make", "gh", "grep", "test",
    "true", "false",
))

#: Anywhere one of these appears in an adopted replacement, refuse it: each
#: is exactly a way one shell command becomes two, or pulls in a
#: substitution. A bare "|" is included ("a pipe into a second command");
#: redirection ("<", ">", ">>") is judged separately below, since a plain
#: `> out.txt` inside the tree is not itself dangerous.
_ADOPTED_CHECK_FORBIDDEN_SUBSTRINGS = (
    (";", "a `;` command separator"),
    ("&&", "a `&&` chain"),
    ("||", "a `||` chain"),
    ("`", "a backtick command substitution"),
    ("$(", "a `$(...)` command substitution"),
    ("|", "a pipe into a second command"),
    ("\n", "a newline"),
)


def guard_adopted_check(cmd):
    """(allowed, reason_or_None) for a done_check this engine did not get
    from a person: a model-adopted replacement (_rewrite_broken_checks) and
    a Work document loaded from disk (_guard_record_checks), both in
    brother_run.py. `reason`, when refused, names the rule broken and is
    written for a log line; the caller must never echo the refused command
    itself alongside it."""
    text = str(cmd or "")
    if not text.strip():
        return False, "the done_check was empty"
    for token, name in _ADOPTED_CHECK_FORBIDDEN_SUBSTRINGS:
        if token in text:
            return False, "the done_check contains %s" % name
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return False, ("the done_check could not be parsed as "
                       "one shell command: %s" % exc)
    if not parts:
        return False, "the done_check was empty"
    prog = os.path.basename(parts[0])
    if prog not in ADOPTED_CHECK_ALLOWED_INTERPRETERS:
        return False, ("the done_check's interpreter is not on "
                       "the adopted-check allowlist")
    for i, tok in enumerate(parts):
        if tok in (">", ">>", "<") and i + 1 < len(parts):
            target = parts[i + 1]
            if target.startswith(("/", "~")) or ".." in target.split("/"):
                return False, ("the done_check redirects to a "
                               "path outside the tree")
    return True, None


def normalize_unit(u):
    """The prompt asks for objective/writes/deps because that reads as plain
    English to a model. work_record.py validates title/owns/depends_on. This
    is the one place that gap is bridged, so the real contract is never
    reimplemented on either side of it."""
    out = dict(u)
    if "owns" not in out and "writes" in out:
        out["owns"] = out.get("writes")
    if "depends_on" not in out and "deps" in out:
        out["depends_on"] = out.get("deps")
    if "title" not in out and "objective" in out:
        out["title"] = out.get("objective")
    if out.get("done_check"):
        resolved, note = resolve_done_check_interpreter(out["done_check"])
        if note:
            out["done_check"] = resolved
            print("door: %s" % note, file=sys.stderr)
    return out


def resolve_cmd(model_cmd_arg):
    if model_cmd_arg:
        return shlex.split(model_cmd_arg)
    env_cmd = os.environ.get("DOOR_MODEL_CMD")
    if env_cmd:
        return shlex.split(env_cmd)
    return default_model_cmd()


def missing_reason(cmd):
    """None if cmd[0] looks runnable, else a message naming what is missing."""
    if not cmd:
        return "no decomposer command was given"
    exe = cmd[0]
    if os.sep in exe or (os.altsep and os.altsep in exe):
        if not os.path.isfile(exe):
            return "the decomposer command %r does not exist" % exe
        if not os.access(exe, os.X_OK):
            return "the decomposer command %r is not executable" % exe
        return None
    if shutil.which(exe) is None:
        return "the decomposer command %r was not found on PATH" % exe
    return None


def ask_decomposer(cmd, prompt, timeout=300):
    return subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=timeout)


def refusal_text(problems):
    return ("REFUSED: %d problem(s) that would break something downstream:\n"
            % len(problems) + "\n".join("  * %s" % p for p in problems))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("outcome", help="what should be true when this is done")
    ap.add_argument("--store", default=WR.STORE)
    ap.add_argument("--model-cmd", help="decomposer command line (shlex syntax)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    # EVAD plan E2 (night probe 2026-08-31): a 20,000-character outcome went
    # straight to a model call and ran past two minutes. An outcome is a
    # sentence; anything book-sized is refused BY NAME before any model or
    # store is touched, exactly like the UnicodeEncodeError seam below.
    if len(args.outcome) > MAX_OUTCOME_CHARS:
        print("REFUSED: this outcome is %d characters; an outcome is a "
              "sentence (limit %d). For a spec this size, save it as a file "
              "and describe the outcome in one line."
              % (len(args.outcome), MAX_OUTCOME_CHARS), file=sys.stderr)
        return 1

    cmd = resolve_cmd(args.model_cmd)
    missing = missing_reason(cmd)
    if missing:
        line = "%s: %s, so the outcome cannot be decomposed" % (NODATA, missing)
        if MW.model_client() == brother_paths.CODEX:
            line += ". %s" % CODEX_SANDBOX_HINT
        print(line, file=sys.stderr)
        return 44

    # GROUNDING, not a per-retry cost: the repository does not change between
    # retries of the SAME decomposition, only the refusal text does.
    existing_files = list_repo_files(os.getcwd())
    # P3: the same listing, matched against every pack's own detection
    # signals, once, before any decomposer call (the tree does not change
    # between retries either). None when nothing matches.
    # COMPOSITIONAL (persona doc 5.2): every pack that matches, most
    # specific first, with core last as the base. `lens` and
    # `matched_paths` still carry the most specific one, unchanged for
    # every reader that wants a single lens; `lenses` carries the whole
    # composition, and the receipt fields and evidence families are the
    # union across it, so a receipt can show that composition occurred.
    inferred = infer_lenses(os.getcwd(), existing_files)
    lens_names = [name for name, _matched in inferred]
    if inferred:
        lens_name, lens_matched = inferred[0]
        union = pack_union(lens_names)
        lens_inferred = {
            "lens": lens_name,
            "matched_paths": lens_matched,
            "lenses": [{"lens": name, "matched_paths": matched}
                       for name, matched in inferred],
            "receipt_fields": union["receipt_fields"],
            "required_evidence_families": union["required_evidence_families"],
        }
    else:
        lens_name, lens_matched, lens_inferred = None, [], None

    total_attempts = args.max_retries + 1
    refusal = None
    last_problems = ["the decomposer was never asked"]
    for attempt in range(1, total_attempts + 1):
        prompt = build_prompt(args.outcome, refusal, existing_files)
        print("door: asking the decomposer (attempt %d of %d): %s"
              % (attempt, total_attempts, " ".join(cmd)), file=sys.stderr)
        try:
            proc = ask_decomposer(cmd, prompt)
        except OSError as exc:
            last_problems = ["the decomposer command could not be run: %s" % exc]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue
        except UnicodeEncodeError as exc:
            last_problems = ["the outcome could not be encoded to send to "
                             "the decomposer: %s" % exc]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue

        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)

        try:
            raw = json.loads(strip_code_fences(decomposer_text(cmd,
                                                              proc.stdout)))
        except ValueError as exc:
            # THE EXIT CODE IS THE FIRST FACT, and until 2026-09-05 it was
            # thrown away: a decomposer that failed and wrote nothing was
            # reported as though it had answered badly, which is what made
            # the Codex finding take four probes to diagnose instead of one.
            detail = "the decomposer's answer could not be read as JSON: %s" % exc
            if proc.returncode != 0:
                detail += (" (the decomposer command %r exited %d; %s)"
                           % (cmd[0], proc.returncode, stderr_tail(proc.stderr)))
                if is_codex_cmd(cmd) or MW.model_client() == brother_paths.CODEX:
                    detail += ". %s" % CODEX_SANDBOX_HINT
            last_problems = [detail]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue
        if not isinstance(raw, list):
            last_problems = ["the decomposer's answer was not a JSON list "
                             "of units"]
            refusal = refusal_text(last_problems)
            print(refusal, file=sys.stderr)
            continue

        units = [normalize_unit(u) for u in raw if isinstance(u, dict)]
        # P5: recomputed each attempt, since `units` can change between
        # retries even though the tree and the inferred lens do not.
        challenge_assumption, pending_challenge = compute_challenge(
            os.getcwd(), existing_files, lens_names, units)
        store = None if args.dry_run else args.store
        record, problems = WR.create(
            args.outcome, units, store=store, lens_inferred=lens_inferred,
            challenge_assumption=challenge_assumption,
            pending_challenge=pending_challenge)
        if not problems:
            if args.dry_run:
                print("door: validated %d unit(s), nothing written "
                      "(--dry-run)" % len(record["rows"]))
            else:
                print("Work %s created with %d unit(s)"
                      % (record["work_id"], len(record["rows"])))
            for row in record["rows"]:
                print("  %s: %s" % (row["id"], row["title"]))
            return 0

        last_problems = problems
        refusal = refusal_text(problems)
        print(refusal, file=sys.stderr)

    print("door: refused after %d attempt(s), store untouched" % total_attempts,
          file=sys.stderr)
    for p in last_problems:
        print("  * %s" % p, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

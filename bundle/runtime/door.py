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
import glob
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import work_record as WR  # noqa: E402
import brother_paths  # noqa: E402
import model_worker as MW  # noqa: E402

NODATA = "NO-DATA"
PASS = "PASS"
FAIL = "FAIL"

#: THE ROUTE (OUT1.a). The door owns the word-cue routing and the registry path rule; scripts/reach_check.py imports
#: them from here, never the other way, so the checker stays outside the kernel (kernel_closure.txt changes only with
#: the owner's approval at the cut). The repository root is this file's parent folder's parent, in a checkout and in
#: an installed runtime alike.
REACH_ROOT = os.path.dirname(HERE)
REACH_REGISTRY = os.path.join(REACH_ROOT, "docs", "plan", "REACH.json")
#: cue word prefixes per verb. Add a prefix here when a paraphrase a person really uses is not reached.
CUES = {
    "start": ("build", "add", "fix", "correct", "repair", "dedupe", "implement", "create", "begin", "start", "resume",
              "continue", "run", "overnight"),
    "status": ("where", "status", "stand", "happen", "slip", "gantt", "board", "progress", "healthy", "blocking",
               "recall", "rememb"),
    "next": ("next", "recommend", "suggest"),
    "review": ("review", "verify", "audit", "migration", "merge", "check"),
    "deliver": ("deliver", "ship", "wrap", "close", "handover", "release", "cut", "finish", "publish"),
    "help": ("help", "explain", "orient", "capabilit"),
}


def words(text):
    """The lower case word tokens route() scores; recommend matches on the same tokens."""
    return re.findall(r"[a-z0-9]+", text.lower())


def route(text):
    """The verb whose cue words match most; a tie or no match is help, never a silent start."""
    words_ = words(text)
    score = {v: sum(1 for w in words_ if w.startswith(cues)) for v, cues in CUES.items()}
    best = max(score.values())
    winners = [v for v, n in score.items() if n == best]
    return winners[0] if best and len(winners) == 1 else "help"


def repo_path(rel):
    """The real path of a registry path under the repository, or None when it resolves outside it (an absolute path,
    a parent-relative path or a symlink out). A path that is not a string raises TypeError."""
    full = os.path.realpath(os.path.join(REACH_ROOT, rel))
    root = os.path.realpath(REACH_ROOT)
    return full if full == root or full.startswith(root + os.sep) else None

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
    """The decomposer nobody named, per host. Under Codex or Cursor that is
    the host's own headless exec (the argv model_worker already ships and
    documents), never `claude -p`, for the same reason CODEX_SANDBOX_HINT
    gives for Codex: a Codex-only or Cursor-only machine has no claude
    binary at all. Under anything else it is unchanged."""
    client = MW.model_client(env)
    if client == brother_paths.CODEX:
        return list(MW.CODEX_ARGV)
    if client == brother_paths.CURSOR:
        return list(MW.CURSOR_ARGV)
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


def is_cursor_cmd(cmd):
    """True when this command line is Cursor's own headless exec."""
    return bool(cmd) and os.path.basename(cmd[0]) == "cursor-agent"


def decomposer_text(cmd, stdout):
    """What the decomposer actually said, from its stdout.

    `codex exec --json` prints JSONL events rather than the answer, so the
    agent's own last message is pulled out of them with the parser
    model_worker already owns and tests. `cursor-agent ... --output-format
    json` prints one JSON object with the answer under "result" (the same
    shape as a `claude -p --output-format json` answer, see CURSOR_ARGV in
    model_worker.py), unwrapped with the parser that already reads that
    shape. Every other command speaks plain text on stdout and is returned
    unchanged."""
    if is_codex_cmd(cmd) and "--json" in cmd:
        claim, _usage = MW._parse_codex_output(stdout)
        return claim if claim else stdout
    if is_cursor_cmd(cmd) and "--output-format" in cmd:
        claim, _usage = MW._parse_model_output(stdout, MW.CURSOR_USAGE_FIELD_MAP)
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


#: Words too common to tell one capability from another when a goal is matched against the route registry.
ROUTE_STOPWORDS = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "for", "from", "i", "in", "is", "it", "its", "me",
    "my", "of", "on", "or", "our", "please", "so", "some", "that", "the", "this", "to", "we", "what", "with", "you"))

#: What Brother does next for a delivered route, per verb: one sentence the person reads, never a command to copy.
ROUTE_ACTIONS = {
    "start": "Brother will start a checked run for this outcome and come back with a receipt that names every "
             "changed file and the check that proved it.",
    "status": "Brother will read the latest evidence and tell you where things stand, keeping PASS, FAIL and NO-DATA "
              "apart.",
    "next": "Brother will name the one recommended next step and why.",
    "review": "Brother will judge this change against its gates and report each verdict with its evidence.",
    "deliver": "Brother will close this with the evidence in hand and will not call it done without a receipt.",
}


def _route_stems(text):
    """Content words of `text`, cut to five letters so "counts" meets "count". ponytail: a naive prefix stem; a
    real stemmer only if the registry's paraphrases stop separating capabilities."""
    return {w[:5] for w in words(text or "") if w not in ROUTE_STOPWORDS}


def _default_registry():
    """The registry this door reads: the checkout's docs/plan/REACH.json, else the copy the plugin ships beside the
    door (runtime/data/REACH.json, written by bundle_runtime.py), so an installed door routes from its own data."""
    if os.path.isfile(REACH_REGISTRY):
        return REACH_REGISTRY
    shipped = os.path.join(HERE, "data", "REACH.json")
    return shipped if os.path.isfile(shipped) else REACH_REGISTRY

def _route_file(cmd):
    """The file a registry command names: inside the repository, else (an install keeps its scripts flat beside the
    door) the same file name next to this door. None when neither exists or the path leaves the repository."""
    full = repo_path(cmd)
    if full is not None and os.path.isfile(full):
        return full
    if isinstance(cmd, str) and cmd.startswith("scripts/"):
        # scripts/X ships flat at runtime/X and scripts/loop/X at runtime/loop/X (bundle_runtime's loop mirror)
        flat = os.path.realpath(os.path.join(HERE, cmd[len("scripts/"):]))
        inside = flat.startswith(os.path.realpath(HERE) + os.sep)
        return flat if inside and os.path.isfile(flat) else None
    parts = cmd.split("/") if isinstance(cmd, str) else []
    if len(parts) > 2 and parts[0] == "products":
        # products/<p>/X ships at runtime/products/<p>/X (route closure) or runtime/hooks/<p>/X (hook mirror)
        for cand in (os.path.join(HERE, *parts), os.path.join(HERE, "hooks", *parts[1:])):
            real = os.path.realpath(cand)
            if real.startswith(os.path.realpath(HERE) + os.sep) and os.path.isfile(real):
                return real
    return full

def _route_entry_ok(cap):
    """True when the capability's command file exists (in the repository or flat beside an installed door) and
    carries its marker."""
    cmd, marker = cap.get("command"), cap.get("marker")
    try:
        full = _route_file(cmd)
        if full is None:
            return False
        with open(full, encoding="utf-8", errors="replace") as fh:
            return bool(marker) and marker in fh.read()
    except (OSError, TypeError):
        return False


#: THE PUBLISH ROUTE (KEY1.d). Publishing a checked result is the cut driver, maintainer tooling that no install
#: carries (scripts/cut_driver.py and scripts/cut.py have no bundle copy). A publish ask is routed to it ONLY from a
#: maintainer checkout, and only while OUT1.n's admission view still files the cut under the MAINTAINER tier (or
#: admits it outright); a consumer on an installed plugin gets the limitation below and a supported next step, and
#: never a command naming a file the install does not contain. Everything the detection cannot decide reads as
#: installed, the safe side.
PUBLISH_ROUTE = "cut-release"
#: Routes whose command is maintainer tooling: bundle_runtime.py never copies their command into the install, and
#: test_ship_every_route.py requires it ABSENT there. SHIP1.a review, 2026-11-03: the route closure had copied cut.py
#: and five cut siblings into bundle/runtime, breaking the absence the publish route protects.
MAINTAINER_ONLY_ROUTES = (PUBLISH_ROUTE,)
PUBLISH_STEM = "publi"
#: spelled in two parts on purpose: bundle_runtime.py ships every sibling a bare "name.py" string constant names, and
#: the cut driver must stay OUT of the install (that absence is what this route protects).
CUT_DRIVER = "cut_driver" + ".py"
MAINTAINER_TIER_CAUSE = "tier MAINTAINER is internal only"
PUBLISH_MAINTAINER_ACTION = ("Brother will publish this through the cut driver in this checkout (scripts/%s run): the "
                             "owner approves from the app and nothing is pushed without his line." % CUT_DRIVER)
PUBLISH_LIMIT = ("Brother cannot publish a release from this install: publishing is maintainer tooling that runs only "
                 "in a checkout of the Brother repository, so nothing was started. The supported next step is to close "
                 "this with the evidence in hand (ask Brother to wrap up and close the session), then hand the result "
                 "to a maintainer, who publishes it from a checkout.")


def maintainer_checkout(here=HERE):
    """True only when this door runs from the scripts/ folder of a Brother checkout that carries the cut driver as a
    regular file: the folder is literally named scripts, reached through no symlink, a .git entry sits beside it, and
    scripts/cut_driver.py is a regular file, not a symlink. A cut driver copied flat beside an installed door (runtime/),
    a checkout's own bundle/runtime/ copy, a symlinked tree, a missing or unreadable tree all read as installed."""
    try:
        if not isinstance(here, str) or os.path.realpath(here) != here or os.path.basename(here) != "scripts":
            return False
        driver = os.path.join(here, CUT_DRIVER)
        return (os.path.lexists(os.path.join(os.path.dirname(here), ".git"))
                and os.path.isfile(driver) and not os.path.islink(driver))
    except (OSError, ValueError):
        return False


def _publish_ask(verb, stems, best):
    """An ask for the cut: a deliver verb ask whose closest capability is the cut route, or an ask that says publish
    (deliver, or help when another cue tied the verb, as "checked" does in "publish the checked result") and matches
    no other deliver capability more closely ("publish the handover" stays a handover)."""
    if best is not None and best.get("id") == PUBLISH_ROUTE:
        return verb == "deliver"
    return verb in ("deliver", "help") and PUBLISH_STEM in stems and (best is None or best.get("verb") != "deliver")


def publish_route(view, inventory, here=HERE):
    """The publish reply: delivered to the cut driver only in a maintainer checkout AND through OUT1.n's decision (the
    view admits cut-release, or queues its group under the MAINTAINER tier, the one tier a maintainer checkout may
    run); every other case is the limitation and the next step, with no route named."""
    groups = {r.get("id") for r in inventory if r.get("routed") == PUBLISH_ROUTE}
    allowed = (PUBLISH_ROUTE in view["routes"]
               or any(q.get("id") in groups and str(q.get("cause") or "").startswith(MAINTAINER_TIER_CAUSE)
                      for q in view["queue"]))
    if allowed and maintainer_checkout(here):
        return {"route": PUBLISH_ROUTE, "verb": "deliver", "action": PUBLISH_MAINTAINER_ACTION, "delivered": True}
    return {"route": None, "verb": "help", "action": PUBLISH_LIMIT, "delivered": False}


def recommend(goal, registry=None, admission=None):
    """The supported route and ONE recommended action for a plain-language goal (OUT1.a).

    Returns {"route": capability id or None, "verb": str, "action": str, "delivered": bool}. The capability whose
    ask and paraphrases share the most words with the goal is picked; it is delivered only when its verb is the
    verb reach_check.route() gives the goal, that verb is not help, and its command file exists and carries its
    marker. Anything else is delivered False with verb help and an action saying plainly that nothing was started,
    naming the owning unit when the goal matches a pending inventory row. Never a command for the person to copy.

    `admission` is consumer_admission()'s view (OUT1.n). Given, a route is delivered only when that view admits it
    and the reply never names a hidden capability; a help route starts nothing, so it needs no admission. Left None,
    the registry is read as a library, which is what the unit tests and the checker do; the door's own command line
    always passes the view."""
    path = registry or _default_registry()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        caps = [c for c in data["capabilities"] if isinstance(c, dict)]
        inventory = [r for r in (data.get("inventory") or []) if isinstance(r, dict)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"route": None, "verb": "help", "delivered": False,
                "action": "Brother cannot read its list of supported routes here, so it has started nothing."}
    verb = route(goal or "")
    goal_stems = _route_stems(goal)
    best, best_score = None, 0
    for cap in caps:
        score = len(goal_stems & _route_stems(" ".join(
            [str(cap.get("ask") or "")] + [str(p) for p in cap.get("paraphrases") or []])))
        # a tie goes to the capability whose verb the goal routes to, then to registry order
        if score > best_score or (score == best_score and best is not None and score
                                  and cap.get("verb") == verb and best.get("verb") != verb):
            best, best_score = cap, score
    if admission is not None and _publish_ask(verb, goal_stems, best):
        return publish_route(admission, inventory)  # KEY1.d: the cut driver is a maintainer checkout's route only
    if (best is not None and verb != "help" and admission is not None
            and best.get("id") not in admission["routes"]):
        return {"route": None, "verb": "help", "delivered": False, "action": ADMISSION_LIMIT}
    if best is not None and verb != "help" and best.get("verb") == verb and _route_entry_ok(best):
        return {"route": best.get("id"), "verb": verb, "action": ROUTE_ACTIONS.get(verb, ROUTE_ACTIONS["start"]),
                "delivered": True}
    route_id = best.get("id") if best is not None else None
    if best is not None and verb != "help" and best.get("verb") == verb:
        action = "The route for this is registered but its entry point is missing from this install, so Brother " \
                 "has started nothing."
    elif best is not None and verb == "help" and best.get("verb") == "help" and _route_entry_ok(best):
        action = "Brother will explain what it does in plain language; no work is started."
        if admission is not None:
            offered = [str(c.get("ask")) for c in caps
                       if c.get("id") in admission["routes"] and c.get("verb") != "help" and c.get("ask")
                       and _route_entry_ok(c)]
            action += (" Available now: %s." % "; ".join(offered) if offered
                       else " Nothing else is available on this install yet.")
    else:
        action = "Brother cannot do this yet, so it has started nothing."
        pending = [(len(goal_stems & _route_stems(" ".join(
            [str(r.get("id", "")).replace("-", " "), str(r.get("user_benefit") or "")]
            + [str(o) for o in r.get("operations") or []]))), r)
            for r in inventory if r.get("disposition") == "pending"]
        pending = [(n, r) for n, r in pending if n >= 2 and admission is None]  # a shared word is a coincidence; a hidden capability is never named
        if pending:
            row = max(pending, key=lambda nr: nr[0])[1]
            action = ("Brother cannot do this yet (the closest capability, \"%s\", is waiting on unit %s), so it "
                      "has started nothing." % (str(row.get("user_benefit") or row.get("id")).rstrip("."),
                                                 row.get("unit")))
    if (admission is not None and route_id not in admission["routes"]
            and (best is None or best.get("verb") != "help")):
        route_id = None  # a route that has not earned access is not named, even as a near match
    return {"route": route_id, "verb": "help", "action": action, "delivered": False}


#: CONSUMER ADMISSION (OUT1.n). A capability is visible and invocable by a consumer only when every fact below holds
#: for THIS candidate revision on THIS host; anything missing, stale, unreadable or of the wrong shape hides it.
#: Hidden is not deleted: consumer_admission() returns the internal repair queue (cause, owner, next verification)
#: beside the visible set. 8 is the minimum exposure, never the 1.2.0 target of 9.
ADMISSION_FILE = "ADMISSION.json"
ADMISSION_MIN_SCORE = 8.0
ADMISSION_HIDDEN_TIERS = ("MAINTAINER", "PARK")
ADMISSION_LIMIT = ("Brother does not offer this yet: it has not passed its release checks on this install, so "
                   "nothing was started. Ask Brother for help and it will list what is available.")
ADMISSION_NEXT = "installed end to end run on this revision and host, plus an independent benefit and ease rating"


def _admission_score(value):
    """A number from the minimum to 10. nan and inf fail the comparison, True is 1, and a text or None is no number,
    so none of them needs its own guard."""
    return isinstance(value, (int, float)) and ADMISSION_MIN_SCORE <= value <= 10


def _refs(value):
    return bool(_as_names(value))


def admission_gaps(record, candidate, host):
    """Why `record` does not earn consumer access for `candidate` on `host` (empty means it does). Pure."""
    if not isinstance(record, dict):
        return ["no admission record"]
    cand = candidate if isinstance(candidate, str) and candidate else None
    on = host if isinstance(host, str) and host else None
    hosts = record.get("supported_hosts")
    gaps = []
    if record.get("implemented") is not True:
        gaps.append("not implemented")
    if record.get("packaged") is not True:
        gaps.append("not packaged")
    if on is None or not isinstance(hosts, list) or on not in hosts or record.get("host") != on:
        gaps.append("not supported or not proven on this host")
    if cand is None or record.get("candidate") != cand:
        gaps.append("evidence is for another revision")
    if record.get("installed_verdict") != "PASS" or not _refs(record.get("evidence_refs")):
        gaps.append("no installed end to end PASS with evidence")
    if record.get("evidence_current") is not True:
        gaps.append("evidence is stale")
    if record.get("blocking_defects") != [] or record.get("quality_verdict") != "PASS":
        gaps.append("unresolved correctness or security failure")
    for name in ("benefit_score", "ease_score"):
        if not _admission_score(record.get(name)):
            gaps.append("%s is missing or below %g" % (name, ADMISSION_MIN_SCORE))
    if not _refs(record.get("score_evidence_refs")):
        gaps.append("scores have no independent evidence")
    return gaps


JOURNEY_RECORD = "JOURNEY-%s.json"
_JOURNEY_ID = re.compile(r"[A-Za-z0-9]+")


def _os_host():
    """The machine a journey was walked on, in the words the walker records ("macos", "ubuntu", "linux")."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "ubuntu" if "ubuntu" in platform.version().lower() else "linux"
    return system.lower() or "unknown"


def _journey_gaps(refs, records_dir, os_host):
    """Why the named journey rows are not all PASS on a clean, installed walk made on this machine (empty means
    they are). `refs` are "J2/row.id" strings; the walk records are the JOURNEY-<id>.json files beside the registry
    (docs/plan in a checkout, data/ in an install). A reference that is malformed, a record that is missing,
    unreadable or for another journey, a walk that was not a clean install of its own candidate, a walk on another
    machine, and any row that is not exactly PASS (FAIL and NO-DATA included) each fail the group. The record binds
    the walk to the tree it installed; that this is the release candidate is the cut gate's check, not the door's."""
    gaps, cache = [], {}
    for ref in _as_names(refs):
        jid, _, row = ref.partition("/")
        if not _JOURNEY_ID.fullmatch(jid) or not row:
            gaps.append("journey row %r is not of the form J<n>/<row>" % ref)
            continue
        if jid not in cache:
            try:
                with open(os.path.join(records_dir or "", JOURNEY_RECORD % jid), encoding="utf-8") as fh:
                    cache[jid] = json.load(fh)
            except (OSError, ValueError, TypeError):
                cache[jid] = None
        doc = cache[jid]
        if not isinstance(doc, dict) or doc.get("journey") != jid:
            gaps.append("no readable walk record for journey %s" % jid)
        elif (doc.get("clean_home") is not True or not isinstance(doc.get("identity"), str) or not doc["identity"]
              or doc.get("identity") != doc.get("candidate_identity")):
            gaps.append("journey %s was not a clean install walk of its own candidate" % jid)
        elif doc.get("host") != os_host:
            gaps.append("journey %s was walked on %s, not on this machine (%s)" % (jid, doc.get("host"), os_host))
        elif not isinstance(doc.get("rows"), dict) or doc["rows"].get(row) != "PASS":
            gaps.append("journey row %s is %s, not PASS" % (ref, (doc.get("rows") or {}).get(row, "absent")
                                                              if isinstance(doc.get("rows"), dict) else "absent"))
    return gaps


def core_admission_gaps(record, host, records_dir=None, os_host=None):
    """Why a CORE group is not admitted (empty means it is). CORE is the product's own path (start a checked run,
    say where things stand, check the install); it earns exposure from installed journey rows, named in
    `journey_rows`, and the door READS the walk records for them: every named row must be PASS in a clean install
    walk made on this machine (_journey_gaps). It still needs the group implemented and packaged, no blocking
    defect, a PASS quality verdict, and a host it supports; an unidentified host fails closed, like every other
    tier. Pure given the records."""
    if not isinstance(record, dict):
        return ["no admission record"]
    gaps = []
    if record.get("basis") != "journey" or not _refs(record.get("journey_rows")):
        gaps.append("no installed journey rows")
    else:
        gaps += _journey_gaps(record.get("journey_rows"), records_dir, os_host or _os_host())
    if record.get("implemented") is not True or record.get("packaged") is not True:
        gaps.append("not implemented or not packaged")
    if record.get("blocking_defects") != [] or record.get("quality_verdict") != "PASS":
        gaps.append("unresolved correctness or security failure")
    hosts = record.get("supported_hosts")
    if not _refs(hosts) or not (isinstance(host, str) and host and host in hosts):
        gaps.append("not supported on this host")
    return gaps


def consumer_capability_visible(record, candidate, host):
    """True only when `record` earns consumer access for `candidate` on `host`; every doubt is False."""
    return not admission_gaps(record, candidate, host)


#: a git revision as the runtime manifest stamps it. A NO-DATA sentence (git could not name the revision) is not one,
#: and is never mistaken for the revision this door runs.
_REVISION_SHAPE = re.compile(r"[0-9a-f]{7,64}")


def current_candidate(here=HERE):
    """The revision this door runs: BROTHER_CANDIDATE, else the installed runtime manifest's source_revision, else
    None (a checkout has no installed revision, so nothing is admitted on it)."""
    named = os.environ.get("BROTHER_CANDIDATE")
    if named and not named.startswith(("NO-DATA", " ")):  # an operator names a revision; a NO-DATA sentence names none
        return named
    try:
        with open(os.path.join(here, HOST_MANIFEST), encoding="utf-8") as fh:
            rev = json.load(fh).get("source_revision")
    except (OSError, ValueError, AttributeError):
        return None
    return rev if isinstance(rev, str) and _REVISION_SHAPE.fullmatch(rev) else None


def current_host(env=None, here=HERE):
    """The client running this door: BROTHER_HOST, else claude or codex from the plugin root it exports, else the
    client whose plugin cache this door was installed under (a door run from a shell tool does not always inherit
    the plugin root), else None. A checkout is under neither, so it is unidentified and CORE fails closed there."""
    env = os.environ if env is None else env
    named = env.get("BROTHER_HOST") or ("claude" if env.get("CLAUDE_PLUGIN_ROOT")
                                        else "codex" if env.get("BROTHER_PLUGIN_ROOT") else None)
    if named:
        return named
    where = os.path.realpath(here).replace(os.sep, "/")
    cfg = env.get("CLAUDE_CONFIG_DIR")
    if cfg and where.startswith(os.path.realpath(cfg).replace(os.sep, "/").rstrip("/") + "/plugins/"):
        return "claude"  # a client whose config folder is not named .claude (the journey walkers' own layout)
    return ("claude" if "/.claude/plugins/" in where else "codex" if "/.codex/plugins/" in where else None)


def _door_match(patterns, capability):
    return isinstance(patterns, list) and any(
        isinstance(p, str) and (capability == p or (p.endswith("*") and "." in p[:-1] and capability.startswith(p[:-1])))
        for p in patterns)


def consumer_admission(registry=None, candidate=None, host=None, admission_path=None):
    """The consumer surface for this candidate and host (OUT1.n): {"candidate", "host", "capabilities": door
    capability patterns that are visible, "routes": visible route ids, "queue": the hidden ones}. The groups come
    from the registry's inventory plus ADMISSION.json beside it (one entry per group: tier, door capability
    patterns, owner, next_verification and the admission record). A group with no entry, an unreadable file or an
    unidentified candidate or host is hidden; the reply is built the same way every time, so help, search, routing
    and dispatch cannot disagree. Queue rows: {"id", "cause", "owner", "next_verification"}."""
    path = registry or _default_registry()
    cand = candidate if candidate is not None else current_candidate()
    on = host if host is not None else current_host()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        inventory = {r["id"]: r for r in data.get("inventory") or [] if isinstance(r, dict) and r.get("id")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"candidate": cand, "host": on, "capabilities": [], "routes": set(),
                "queue": [{"id": "*", "cause": "the registry %s cannot be read" % path, "owner": "unowned",
                           "next_verification": "restore the registry, then rerun the reach check"}]}
    try:
        with open(admission_path or os.path.join(os.path.dirname(path), ADMISSION_FILE), encoding="utf-8") as fh:
            entries = json.load(fh)["entries"]
        entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        entries = {}
    view = {"candidate": cand, "host": on, "capabilities": [], "routes": set(), "queue": []}
    for gid in sorted(set(inventory) | set(entries)):
        entry, row = entries.get(gid, {}), inventory.get(gid, {})
        gaps = (core_admission_gaps(entry.get("admission"), on, os.path.dirname(path)) if entry.get("tier") == "CORE"
                else admission_gaps(entry.get("admission"), cand, on))
        if entry.get("tier") in ADMISSION_HIDDEN_TIERS:
            gaps = ["tier %s is internal only" % entry["tier"]] + gaps
        if gaps:
            view["queue"].append({"id": gid, "cause": "; ".join(gaps),
                                  "owner": str(entry.get("owner") or row.get("unit") or "unowned"),
                                  "next_verification": str(entry.get("next_verification") or ADMISSION_NEXT)})
            continue
        view["capabilities"] += _as_names(entry.get("capabilities"))
        view["routes"] |= {r for r in [row.get("routed")] + _as_names(entry.get("routes")) if isinstance(r, str)}
    return view


def capability_admitted(capability, view):
    """True when `view` (consumer_admission()) shows the door capability id `capability`. Unknown ids are hidden."""
    return isinstance(capability, str) and _door_match(view["capabilities"], capability)


def hidden_capability_reply(capability):
    """What a consumer gets for a capability that has not earned access: a truthful limitation and the supported
    next step, in the same shape as every other capability reply, with nothing started and no hidden name leaked
    beyond the id the person typed."""
    return {"capability": capability, "handler": "", "state": "BLOCKED", "verdict": NODATA, "evidence_refs": [],
            "missing_prerequisites": [], "missing_authority": [], "output": ADMISSION_LIMIT,
            "next_step": "ask Brother for help to see what is available"}


#: THE VAULT CAPABILITIES (OUT1.d): what the door can ask the EXISTING Vault tools to do. Each row names the handler
#: products/brothermode/tools/bm_vault.py runs (its DOOR_HANDLERS table owns the command each handler becomes, so no
#: Vault logic lives here), the typed inputs it needs before it launches, the authority the person must grant (read
#: always; --allow write to save a lesson candidate, --allow approve to record a promotion), and which input the
#: plain-language outcome fills.
VAULT_CAPABILITIES = (
    {"id": "vault.recall", "handler": "vault_existing_recall", "prerequisites": ["query"],
     "required_authority": ["read"], "outcome_input": "query"},
    {"id": "vault.context_recall", "handler": "vault_existing_context_recall", "prerequisites": ["paths"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.status", "handler": "vault_existing_status", "prerequisites": [],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.capture", "handler": "vault_existing_capture", "prerequisites": ["text", "by"],
     "required_authority": ["write"], "outcome_input": "text"},
    {"id": "vault.review", "handler": "vault_existing_review", "prerequisites": ["id"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.lifecycle", "handler": "vault_existing_lifecycle", "prerequisites": [],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.promote", "handler": "vault_existing_promote", "prerequisites": ["id", "to", "by"],
     "required_authority": ["approve"], "outcome_input": None},
    {"id": "vault.provenance", "handler": "vault_existing_provenance", "prerequisites": ["note", "attribute"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.asof", "handler": "vault_existing_asof", "prerequisites": ["date"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "vault.retention", "handler": "vault_existing_retention", "prerequisites": [],
     "required_authority": ["read"], "outcome_input": None},
)
VAULT_TIMEOUT_S = 300

#: THE DATA CAPABILITIES (OUT1.e): what the door can ask the EXISTING BrotherDS and MDM tools to do, beside the
#: Vault rows and run by the same adapter. Each row names the handler products/brotherds/bds.py runs (its
#: DOOR_HANDLERS table owns the command each handler becomes and the type of every input, so no claim or MDM logic
#: lives here). Writing a claim, a registry entry, an outcome, a review plan or a resolved file needs --allow write.
#: BrotherDS is experimental, and every data reply says so.
DATA_CAPABILITIES = (
    {"id": "data.author", "handler": "data_existing_author", "prerequisites": ["dest", "claim"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "data.check", "handler": "data_existing_check", "prerequisites": ["claim_path"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.receipt", "handler": "data_existing_receipt", "prerequisites": ["claim_path"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.register", "handler": "data_existing_register", "prerequisites": ["claim_path"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "data.score", "handler": "data_existing_score", "prerequisites": ["claim_path", "actual"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "data.ledger", "handler": "data_existing_ledger", "prerequisites": ["directory"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_review", "handler": "data_existing_mdm_review", "prerequisites": ["review_csv", "merge_threshold"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_clusters", "handler": "data_existing_mdm_clusters", "prerequisites": ["gold_csv"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_drift", "handler": "data_existing_mdm_drift", "prerequisites": ["reference_csv", "current_csv"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_audit", "handler": "data_existing_mdm_audit", "prerequisites": ["results_csv"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_audit_plan", "handler": "data_existing_mdm_audit_plan", "prerequisites": ["results_csv", "plan"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "data.mdm_evaluate", "handler": "data_existing_mdm_evaluate", "prerequisites": ["labelled_csv"],
     "required_authority": ["read"], "outcome_input": None},
    {"id": "data.mdm_resolve", "handler": "data_existing_mdm_resolve", "prerequisites": ["inputs", "out"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "data.mdm_classify", "handler": "data_existing_mdm_classify", "prerequisites": ["names"],
     "required_authority": ["read"], "outcome_input": "names"},
    {"id": "data.mdm_normalize", "handler": "data_existing_mdm_normalize", "prerequisites": ["kind", "locale", "text"],
     "required_authority": ["read"], "outcome_input": "text"},
    {"id": "data.mdm_validate", "handler": "data_existing_mdm_validate", "prerequisites": ["kind", "values"],
     "required_authority": ["read"], "outcome_input": None},
)
DATA_STATUS = "BrotherDS is experimental: a verdict here is a statement about proof, not about the world."


def _given(value):
    """A supplied input: anything but absent or empty. A zero or a False is a real answer, never a missing one."""
    return value is not None and not (isinstance(value, (str, list, tuple, dict)) and not value)


def _as_names(value):
    """The non-empty strings of a list field; any other shape is empty, so a malformed field blocks, never passes."""
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, (list, tuple)) else []


def invoke_capability(spec, inputs, context, invoke):
    """Run ONE existing handler for a capability and say plainly whether it completed (OUT1.d).

    spec: {"id", "handler", "prerequisites", "required_authority"}. context: {"available", "authority"}, read and
    never written. invoke(handler, inputs) returns {"verdict", "output", "evidence_refs"}. Nothing launches while a
    prerequisite or an authority is missing: the adapter never grants authority itself. COMPLETE needs a PASS
    verdict AND at least one evidence reference; a success sentence alone is UNPROVEN. Returns {"capability",
    "handler", "state", "verdict", "output", "evidence_refs", "missing_prerequisites", "missing_authority"}."""
    spec = spec if isinstance(spec, dict) else {}
    context = context if isinstance(context, dict) else {}
    handler = spec.get("handler") if isinstance(spec.get("handler"), str) else ""
    available, granted = _as_names(context.get("available")), _as_names(context.get("authority"))
    missing = [p for p in _as_names(spec.get("prerequisites")) if p not in available]
    lacking = [a for a in _as_names(spec.get("required_authority")) if a not in granted]
    out = {"capability": spec.get("id"), "handler": handler, "state": "BLOCKED", "verdict": NODATA, "output": "",
           "evidence_refs": [], "missing_prerequisites": missing, "missing_authority": lacking}
    if not handler:
        out["output"] = "this capability names no handler, so nothing was started"
        return out
    if missing:
        out["output"] = "nothing was started: missing %s" % ", ".join(missing)
        return out
    if lacking:
        out.update(state="REFUSED", output="nothing was started: this needs %s permission" % ", ".join(lacking))
        return out
    try:
        reply = invoke(handler, inputs)
    except Exception as exc:  # a handler that raised is a failed run, never a quiet pass
        out.update(state="FAILED", verdict="FAIL", output="the handler could not run: %s" % exc)
        return out
    reply = reply if isinstance(reply, dict) else {}
    verdict = reply.get("verdict") if reply.get("verdict") in ("PASS", "FAIL", NODATA) else NODATA
    evidence = _as_names(reply.get("evidence_refs"))
    out.update(verdict=verdict, output=str(reply.get("output") or ""), evidence_refs=evidence)
    if verdict == "PASS":
        out["state"] = "COMPLETE" if evidence else "UNPROVEN"
    else:
        out["state"] = "FAILED" if verdict == "FAIL" else NODATA
    return out


def vault_tool():
    """bm_vault.py beside this door: the checkout's products/ tree, else the installed runtime's hooks/ tree."""
    for path in (os.path.join(REACH_ROOT, "products", "brothermode", "tools", "bm_vault.py"),
                 os.path.join(HERE, "hooks", "brothermode", "tools", "bm_vault.py")):
        if os.path.isfile(path):
            return path
    return None


def data_tool():
    """bds.py beside this door: the checkout's products/ tree, else the installed runtime's products/ tree (SHIP1.a
    ships it there, the same release as this door), else a kept install of the brotherds plugin in this client's
    plugin cache (newest version folder by name). None when none exists."""
    for path in (os.path.join(REACH_ROOT, "products", "brotherds", "bds.py"),
                 os.path.join(HERE, "products", "brotherds", "bds.py")):
        if os.path.isfile(path):
            return path
    found = sorted(glob.glob(os.path.join(brother_paths.config_dir(), "plugins", "cache", "*", "brotherds", "*",
                                          "bds.py")))
    return found[-1] if found else None  # ponytail: name order, a version sort if 1.9 against 1.10 ever matters


def _tool_invoke(tool, handler, inputs, name, absent, timeout):
    """invoke() for a product tool: `TOOL door HANDLER`, inputs as JSON on stdin, its JSON reply on stdout. A run
    that cannot start, times out or answers with anything but a JSON object carries no evidence, so it never
    completes. ponytail: a subprocess, not an import, so the door's kernel closure stays unchanged; the product
    tool is a run time dependency kernel_closure.py does not see (its docstring names that class)."""
    if tool is None:
        return {"verdict": NODATA, "output": absent, "evidence_refs": []}
    try:
        proc = subprocess.run([sys.executable, tool, "door", handler], input=json.dumps(inputs),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"verdict": NODATA, "output": "%s did not answer within %d seconds" % (name, timeout),
                "evidence_refs": []}
    except OSError as exc:
        return {"verdict": NODATA, "output": "%s could not be started: %s" % (name, exc), "evidence_refs": []}
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return {"verdict": "FAIL", "evidence_refs": [],
                "output": "%s answered unreadably (exit %d; %s)" % (name, proc.returncode, stderr_tail(proc.stderr))}
    return reply


def vault_invoke(handler, inputs, tool=None, timeout=VAULT_TIMEOUT_S):
    """invoke() for the Vault: `bm_vault.py door HANDLER` through _tool_invoke."""
    return _tool_invoke(tool or vault_tool(), handler, inputs, "the Vault",
                        "the Vault tools are not installed beside the door", timeout)


def data_invoke(handler, inputs, tool=None, timeout=VAULT_TIMEOUT_S):
    """invoke() for BrotherDS and MDM: `bds.py door HANDLER` through _tool_invoke."""
    return _tool_invoke(tool or data_tool(), handler, inputs, "BrotherDS",
                        "BrotherDS is not installed beside the door: it is experimental and ships as its own "
                        "brotherds plugin; install that plugin, then ask again", timeout)


def _run_capability(table, invoke, capability, outcome, typed, allow):
    """One capability row of `table` through invoke_capability: the person's outcome and typed evidence in, the real
    handler's result out. Authority is read plus exactly what `allow` names; available inputs are exactly the ones
    the person gave (a zero or a False counts as given)."""
    spec = next((c for c in table if c["id"] == capability), None)
    if spec is None:
        return {"capability": capability, "handler": "", "state": "BLOCKED", "verdict": NODATA, "evidence_refs": [],
                "missing_prerequisites": [], "missing_authority": [],
                "output": "unknown capability; known: %s"
                          % ", ".join(c["id"] for c in VAULT_CAPABILITIES + DATA_CAPABILITIES + MOBILE_CAPABILITIES
                                      + ASSURANCE_CAPABILITIES + HOST_CAPABILITIES + LOOP_CAPABILITIES
                                      + JEV_CAPABILITIES + LEARNING_CAPABILITIES
                                      + CONTENT_CAPABILITIES)}
    inputs = dict(typed or {})
    if outcome and spec["outcome_input"] and not _given(inputs.get(spec["outcome_input"])):
        inputs[spec["outcome_input"]] = outcome
    context = {"available": [k for k, v in inputs.items() if _given(v)], "authority": ["read"] + list(allow or [])}
    return invoke_capability(spec, inputs, context, invoke)


def run_vault_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's Vault route (OUT1.d)."""
    return _run_capability(VAULT_CAPABILITIES, invoke or vault_invoke, capability, outcome, typed, allow)


def run_data_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's BrotherDS and MDM route (OUT1.e): the same adapter, plus the experimental status on every reply."""
    got = _run_capability(DATA_CAPABILITIES, invoke or data_invoke, capability, outcome, typed, allow)
    got["status"] = DATA_STATUS
    return got


#: THE MOBILE CAPABILITIES (OUT1.f): what the door can ask the EXISTING mobile workflow to do, beside the Vault and
#: data rows and run by the same invoke_capability adapter. A row names the existing script (and its sub command) a
#: handler becomes, its typed inputs, and how each one is passed (`args`: name, kind, form). Nothing here validates a
#: journey, builds, taps or hashes a result: the named script does, and the door only decides whether to start it.
#:   kinds: file and dir must exist, out is a path the script writes, str is free text (always passed as --flag=value),
#:   files and list are lists, int and num are numbers (a boolean or a numeric text is refused), switch is a boolean
#:   flag, rest is the remainder of the line (a test command, whose first word must be a known test runner).
#:   forms: "pos" is a positional word (a word starting with a dash never becomes one), anything else is the flag.
#: Authority is read, or write when the script writes a file (or when its optional `write_when` input is given).
#: `env` names an environment prerequisite the door checks BEFORE starting anything: a simulator, a reachable Appium
#: server. `device` marks a row that acts on a device: a device that is not a listed simulator is physical and needs
#: --allow device, which write never implies. A technical PASS here is never human acceptance.
MOBILE_LIMIT = ("A technical result is not human acceptance: human acceptance is NO-DATA until a person looks, and "
                "a simulator result says nothing about a physical device.")
VISUAL_LIMIT = ("VISUAL ONLY: a screenshot shows where a target appears. It proves no tap, no interaction and no "
                "human acceptance; an interaction needs a driver that acted (native iOS or Appium).")
MOBILE_TIMEOUT_S = 600
MOBILE_LONG_TIMEOUT_S = 3600
MOBILE_TEST_RUNNERS = ("xcodebuild", "xcrun", "swift")
MOBILE_APPIUM_URL = "http://127.0.0.1:4723"


def _mobile_row(cap, script, sub, args, need=(), authority="read", env=(), device=False, write_when=None,
                outcome=None, limit=MOBILE_LIMIT, timeout=MOBILE_TIMEOUT_S, nodata_if=None, states=False):
    return {"id": "mobile." + cap, "handler": "mobile_existing_" + cap, "script": script, "sub": tuple(sub),
            "args": tuple(args), "prerequisites": list(need), "required_authority": [authority],
            "outcome_input": outcome, "env": tuple(env), "device": device, "write_when": write_when, "limit": limit,
            "timeout": timeout, "nodata_if": nodata_if, "states": states}


_SCHEMA = ("schema", "file", "--schema")
_OUT = ("out", "out", "--out")
_DEVICE = (("platform", "str", "--platform"), ("device", "str", "--device"), ("bundle_id", "str", "--bundle-id"))
MOBILE_CAPABILITIES = (
    _mobile_row("doctor", "mobile_workflow", ("doctor",),
                (("repo", "dir", "--repo"), _OUT), ("repo", "out"), "write"),
    _mobile_row("profile_detect", "mobile_project_profile", (),
                (("project_dir", "dir", "pos"), ("project_id", "str", "--project-id"), _SCHEMA), ("project_dir",)),
    _mobile_row("profile_init", "mobile_workflow", ("profile-init",),
                (("repo", "dir", "--repo"), _OUT, ("project", "str", "--project"), ("scheme", "str", "--scheme"),
                 ("configuration", "str", "--configuration"), ("simulator_id", "str", "--simulator-id"),
                 ("simulator_name", "str", "--simulator-name"), ("expected_test", "list", "--expected-test"),
                 ("xcresult", "dir", "--xcresult"), ("only_testing", "list", "--only-testing")),
                ("repo", "out"), "write", env=("simulator",)),
    _mobile_row("reference_init", "mobile_workflow", ("reference-init",),
                (("repo", "dir", "--repo"), ("profile", "file", "--profile"), _OUT,
                 ("source_revision", "str", "--source-revision")),
                ("repo", "profile", "out"), "write", env=("simulator",)),
    _mobile_row("reference_record", "mobile_workflow", ("reference",),
                (("repo", "dir", "--repo"), ("app", "file", "--app"), ("observation", "file", "--observation"),
                 ("mapping", "file", "--mapping"), ("source_revision", "str", "--source-revision"), _OUT),
                ("repo", "app", "observation", "mapping", "source_revision", "out"), "write"),
    _mobile_row("reference_check", "mobile_workflow", ("check-reference",),
                (("repo", "dir", "--repo"), ("reference", "file", "--reference")), ("repo", "reference")),
    _mobile_row("reference_lock", "mobile_reference_lock", (),
                (("observation", "file", "pos"), ("bundle_id", "str", "pos"),
                 ("capture_method", "str", "--capture-method"), _OUT), ("observation", "bundle_id"),
                write_when="out"),
    _mobile_row("journey", "mobile_journey_contract", (),
                (("record", "file", "pos"), _SCHEMA), ("record",)),
    _mobile_row("canonical_action", "mobile_canonical_action", (),
                (("record", "file", "pos"), _SCHEMA), ("record",)),
    _mobile_row("plan", "mobile_plan_compiler", (),
                (("record", "file", "pos"), _SCHEMA, ("profile", "file", "--profile"),
                 ("adapter", "str", "--adapter"), _OUT), ("record",), write_when="out"),
    _mobile_row("ownership", "mobile_ownership_resolver", (),
                (("semantic_plan", "file", "pos"), ("project_dir", "dir", "pos"), ("profile", "file", "--profile"),
                 ("adapter", "str", "--adapter"), _OUT), ("semantic_plan", "project_dir"), write_when="out"),
    _mobile_row("driver_contract", "mobile_driver_contract", (),
                (("record", "file", "pos"), _SCHEMA), ("record",)),
    _mobile_row("driver_select", "mobile_hybrid_action_router", (),
                (("record", "file", "--record"), ("driver_describe", "files", "--driver-describe"),
                 ("platform", "str", "--platform")), ("record", "driver_describe")),
    _mobile_row("screen_observation", "mobile_screen_observation", (),
                (("record", "file", "pos"), _SCHEMA, ("driver_contract", "file", "--driver-contract")), ("record",)),
    _mobile_row("run", "mobile_workflow", ("run",),
                (("repo", "dir", "--repo"), ("profile", "file", "--profile"), ("reference", "file", "--reference"),
                 _OUT), ("repo", "profile", "reference", "out"), "write", env=("simulator",),
                timeout=MOBILE_LONG_TIMEOUT_S),
    _mobile_row("native_ios", "mobile_native_ios_adapter", ("run",),
                (("record", "file", "--record"), ("device", "str", "--device"), _OUT),
                ("record", "device", "out"), "write", env=("simulator",), device=True),
    _mobile_row("appium", "mobile_appium_adapter", ("run",),
                (("record", "file", "--record"), ("server", "str", "--server"),
                 ("capabilities", "file", "--capabilities"), _OUT), ("record", "out"), "write",
                env=("appium",), device=True),
    _mobile_row("visual_only", "mobile_visual_fallback_adapter", ("run",),
                (("record", "file", "--record"), ("screenshot", "file", "--screenshot"), ("tree", "file", "--tree"),
                 _OUT, ("confidence_threshold", "num", "--confidence-threshold"), ("model", "str", "--model")),
                ("record", "screenshot", "out"), "write", limit=VISUAL_LIMIT),
    _mobile_row("state_apply", "mobile_state_reset", ("apply",),
                (("fixture", "file", "--fixture"),) + _DEVICE
                + (_SCHEMA, ("repo", "dir", "--repo"), _OUT, ("checkpoints_dir", "str", "--checkpoints-dir"),
                   ("fixture_api_url", "str", "--fixture-api-url"),
                   ("db_fixture_script", "str", "--db-fixture-script"),
                   ("backend_reset_url", "str", "--backend-reset-url")),
                ("fixture", "out"), "write", env=("simulator",), device=True),
    _mobile_row("state_uninstall", "mobile_state_reset", ("uninstall",),
                _DEVICE + (("repo", "dir", "--repo"), _OUT), ("bundle_id", "out"), "write", env=("simulator",),
                device=True),
    _mobile_row("state_install", "mobile_state_reset", ("install",),
                _DEVICE + (("app", "dir", "--app"), ("repo", "dir", "--repo"), _OUT), ("app", "out"), "write",
                env=("simulator",), device=True),
    _mobile_row("state_checkpoint", "mobile_state_reset", ("capture-checkpoint",),
                _DEVICE + (("checkpoints_dir", "out", "--checkpoints-dir"), ("name", "str", "--name"),
                           ("repo", "dir", "--repo"), _OUT),
                ("bundle_id", "checkpoints_dir", "name", "out"), "write", env=("simulator",), device=True),
    _mobile_row("state_hooks", "mobile_deterministic_hooks", (),
                (("fixture", "file", "--fixture"), _SCHEMA) + _DEVICE + (("repo", "dir", "--repo"), _OUT),
                ("fixture", "out"), "write", env=("simulator",), device=True),
    _mobile_row("state_fixture", "mobile_state_fixture", (),
                (("fixture_path", "file", "pos"), _SCHEMA), ("fixture_path",)),
    _mobile_row("test_route", "mobile_test_route", (),
                (("route_path", "file", "pos"), _SCHEMA, ("render_guide", "switch", "--render-guide")),
                ("route_path",)),
    _mobile_row("toolchain", "mobile_toolchain_probe", (), (_SCHEMA,),
                nodata_if={"ios_toolchain_status": "none", "android_toolchain_status": "none"}),
    _mobile_row("canary", "mobile_xcode_canary_local", (),
                (("repo", "dir", "--repo"), _OUT, ("simulator_id", "str", "--simulator-id"),
                 ("project", "str", "--project"), ("scheme", "str", "--scheme"),
                 ("app_target", "str", "--app-target"), ("expected_test", "list", "--expected-test"),
                 ("only_testing", "list", "--only-testing")), ("out",), "write", env=("simulator",),
                timeout=MOBILE_LONG_TIMEOUT_S),
    _mobile_row("test_bundle_record", "native_evidence", ("record",),
                (("repo", "dir", "--repo"), _OUT, ("result_bundle", "out", "--result-bundle"),
                 ("expected_test", "list", "--expected-test"), ("artifact", "list", "--artifact"),
                 ("requirement", "list", "--requirement"), ("log", "out", "--log"),
                 ("tests_json", "out", "--tests-json"), ("xcrun", "str", "--xcrun"), ("timeout", "int", "--timeout"),
                 ("command", "rest", "--command")), ("repo", "out", "result_bundle", "command"), "write",
                timeout=MOBILE_LONG_TIMEOUT_S),
    _mobile_row("test_bundle_validate", "native_evidence", ("validate",),
                (("evidence", "file", "--evidence"), ("repo", "dir", "--repo"), ("xcrun", "str", "--xcrun")),
                ("evidence",)),
    _mobile_row("test_bundle_v2", "native_evidence_v2", ("wrap",),
                (("evidence", "file", "--evidence"), ("repo", "dir", "--repo"), ("xcrun", "str", "--xcrun"), _OUT),
                ("evidence",), write_when="out"),
    _mobile_row("design_search", "mobile_design", ("search",),
                (("board", "file", "--board"), ("query", "str", "--query"), ("app", "str", "--app"),
                 ("flow", "str", "--flow"), ("element", "str", "--element"), _OUT), ("board", "out"), "write"),
    _mobile_row("design_brief", "mobile_design", ("brief",),
                (("board", "file", "--board"), ("query", "str", "--query"), ("outcome", "str", "--outcome"), _OUT),
                ("board", "outcome", "out"), "write", outcome="outcome"),
    _mobile_row("design_media", "mobile_design", ("media",),
                (("asset", "file", "--asset"), ("role", "str", "--role"), ("max_bytes", "int", "--max-bytes"),
                 ("max_duration", "num", "--max-duration"), _OUT), ("asset", "role", "out"), "write"),
    _mobile_row("design_evidence", "mobile_design", ("evidence",),
                (("board", "file", "--board"), ("id", "str", "--id"), _OUT), ("board", "id", "out"), "write"),
    _mobile_row("design_staleness", "mobile_design", ("check-staleness",),
                (("evidence", "file", "--evidence"), ("board", "file", "--board"), _OUT),
                ("evidence", "board", "out"), "write"),
    _mobile_row("release_state", "release_state_tracker", (),
                (("observation", "file", "--observation"), ("bundle_id", "str", "--bundle-id"), _OUT),
                write_when="out", states=True),
    _mobile_row("passport", "journey_passport", (),
                (("journey_contract", "file", "--journey-contract"), ("reference_lock", "file", "--reference-lock"),
                 ("native_evidence_v2", "file", "--native-evidence-v2"),
                 ("physical_device_evidence", "file", "--physical-device-evidence"),
                 ("design_evidence", "file", "--design-evidence"), ("design_staleness", "file", "--design-staleness"),
                 ("accessibility", "file", "--accessibility"), ("performance", "file", "--performance"),
                 ("human_acceptance", "file", "--human-acceptance"), ("release", "file", "--release"),
                 ("production", "file", "--production"), _OUT),
                ("journey_contract", "reference_lock", "native_evidence_v2", "physical_device_evidence"),
                write_when="out"),
    _mobile_row("claims_check", "mobile_product_claims", ("check",),
                (("record", "file", "pos"), _SCHEMA, ("lifecycle_record", "file", "--lifecycle-record"),
                 ("passport_record", "file", "--passport-record")), ("record",)),
    _mobile_row("claims_register", "mobile_product_claims", ("register",),
                (("passport", "file", "pos"), ("thresholds", "file", "pos"), _OUT), ("passport", "thresholds"),
                write_when="out"),
    _mobile_row("claims_gate", "mobile_product_claims", ("gate",),
                (("passport", "file", "pos"), ("claims", "file", "pos")), ("passport", "claims")),
    _mobile_row("pack", "mobile_workflow", ("pack",),
                (("run", "dir", "--run"), _OUT), ("run", "out"), "write"),
    _mobile_row("verify_pack", "mobile_workflow", ("verify-pack",),
                (("archive", "file", "--archive"),), ("archive",)),
)
#: how a missing prerequisite is supplied. A route is a door capability that makes it; "" is a step the person does
#: (the door never installs a toolchain or starts a server itself).
MOBILE_PREPARE = {
    "profile": ("mobile.profile_init", "write a run profile from the project and the simulator list"),
    "reference": ("mobile.reference_init", "record the last good clean checkout as the working reference"),
    "simulator": ("", "install Xcode and add an iOS simulator for the runtime you need, then ask again"),
    "appium": ("", "start an Appium server (appium) and give its address as server, then ask again"),
}


def _mobile_row_for(handler):
    return next((r for r in MOBILE_CAPABILITIES if r["handler"] == handler), None)


def mobile_tool(script):
    """The named mobile script (given without .py: the runtime bundler follows a quoted script name into the shipped
    closure, and what ships is the owner's decision, so the door looks beside itself at run time) beside this door (a checkout's scripts/ folder, or the installed runtime/ folder), or
    None when this install does not carry it."""
    path = os.path.join(HERE, script + ".py")
    return path if os.path.isfile(path) else None


def _mobile_argv(row, inputs):
    """(argv words, problem): the script's arguments from the typed inputs. Any input of the wrong type, a missing
    file, or a positional word starting with a dash is a problem and nothing starts."""
    words, flags, rest = [], [], []
    for name, kind, form in row["args"]:
        if inputs.get(name) is None or inputs.get(name) in ("", [], {}):
            continue
        value = inputs[name]
        if kind in ("files", "list"):
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                return None, "%s must be a list of non-empty text" % name
            if kind == "files":
                gone = [v for v in value if not os.path.isfile(v)]
                if gone:
                    return None, "%s names a file that is not there: %s; supply that file" % (name, gone[0])
            flags.extend("%s=%s" % (form, v) for v in value)
        elif kind in ("int", "num"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
                    or value in (float("inf"), float("-inf")) or (kind == "int" and not isinstance(value, int)):
                return None, "%s must be a %s" % (name, "whole number" if kind == "int" else "number")
            flags.append("%s=%s" % (form, value))
        elif kind == "switch":
            if not isinstance(value, bool):
                return None, "%s must be true or false" % name
            if value:
                flags.append(form)
        elif kind == "rest":
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                return None, "%s must be a list of non-empty words" % name
            if os.path.basename(value[0]) not in MOBILE_TEST_RUNNERS:
                return None, "%s must start with a test runner (%s)" % (name, ", ".join(MOBILE_TEST_RUNNERS))
            rest = [form] + value
        else:
            if not isinstance(value, str) or not value:
                return None, "%s must be non-empty text" % name
            if kind == "file" and not os.path.isfile(value):
                return None, "%s names a file that is not there: %s; supply that file" % (name, value)
            if kind == "dir" and not os.path.isdir(value):
                return None, "%s names a folder that is not there: %s; supply that folder" % (name, value)
            if form == "pos":
                if value.startswith("-"):
                    return None, "%s starts with a dash, so it could be taken for an option" % name
                words.append(value)
            else:
                flags.append("%s=%s" % (form, value))
    return list(row["sub"]) + words + flags + rest, None


def _mobile_reported(row, out):
    """The verdict the script's own output reports, or None: a status field, a line saying NO-DATA or FAIL, an
    all-NO-DATA release record, or a toolchain with nothing found. Exit 0 never overrides it."""
    statuses = {"NO-DATA": NODATA, "NO-OP": NODATA, "LOW_CONFIDENCE": NODATA, "NOT_ATTEMPTED": NODATA,
                "FAIL": "FAIL", "UNSUPPORTED": "FAIL", "NO_DRIVER": "FAIL"}
    try:
        record = json.loads(out)
    except ValueError:
        record = None
    if isinstance(record, dict):
        if isinstance(record.get("status"), str) and record["status"] in statuses:
            return statuses[record["status"]]
        nodata_if = row.get("nodata_if")
        if nodata_if and all(record.get(k) == v for k, v in nodata_if.items()):
            return NODATA
        if row.get("states") and isinstance(record.get("states"), dict) and not any(
                isinstance(s, dict) and s.get("verdict") == "PASS" for s in record["states"].values()):
            return NODATA
        return None
    for line in out.splitlines():
        found = re.match(r"^(?:[\w ]+: )?(NO-DATA|FAIL)\b", line.strip())
        if found:
            return found.group(1)
    return None


#: script -> how its OWN success reads when it prints no PASS word, read from what each real script prints (measured
#: 2026-11-01). One alternative per entry must hold: ("json", ((key path, accepted values), ...)) all hold in the
#: printed object, ("text", regex) matches a line, ("record",) the script printed a non-empty JSON record and no
#: PROBLEMS (a validator that exits 1 on a problem). A script missing here earns PASS only from a PASS line or a
#: JSON status or verdict of PASS.
MOBILE_OWN_SUCCESS = {
    "mobile_toolchain_probe": (("json", ((("ios_toolchain_status",), ("complete", "partial")),)),
                               ("json", ((("android_toolchain_status",), ("complete", "partial")),))),
    "mobile_project_profile": (("json", ((("confidence",), ("low", "high")),)),),
    "mobile_reference_lock": (("json", ((("artifact_identity", "status"), ("observed_installed",)),)),),
    "mobile_hybrid_action_router": (("json", ((("status",), ("SELECTED",)),)),),
    "mobile_visual_fallback_adapter": (("json", ((("status",), ("GROUNDED",)),)),),
    "mobile_state_fixture": (("record",),),
    "mobile_test_route": (("record",), ("text", r"^# Test route: ")),
    "mobile_product_claims": (("record",),),
    "journey_passport": (("json", ((("completeness", "failed_count"), (0,)), (("completeness", "no_data_count"), (0,)))),),
}


def _mobile_json_at(record, path):
    for key in path:
        if not isinstance(record, dict) or key not in record:
            return None
        record = record[key]
    return record


def _mobile_own_success(script, out, err):
    """True when `script`'s entry in MOBILE_OWN_SUCCESS holds for what it printed."""
    try:
        record = json.loads(out)
    except ValueError:
        record = None
    for alt in MOBILE_OWN_SUCCESS.get(script, ()):
        if alt[0] == "json" and isinstance(record, dict) and all(
                _mobile_json_at(record, path) in accepted for path, accepted in alt[1]):
            return True
        if alt[0] == "text" and re.search(alt[1], out, re.M):
            return True
        if alt[0] == "record" and record and "PROBLEMS" not in err:
            return True
    return False


def _mobile_verdict_in(record):
    """The worst verdict a JSON object states anywhere in it: FAIL if any status or verdict is FAIL, else PASS if the
    object states PASS, else None."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            found.extend(v for k, v in node.items() if k in ("status", "verdict") and isinstance(v, str))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(record)
    return FAIL if FAIL in found else PASS if PASS in found else None


def _mobile_own_verdict(row, out, err):
    """PASS, FAIL or NODATA from what the script itself printed on stdout and stderr, never from exit 0. A FAIL
    anywhere (a `FAIL` line on either stream, a JSON status or verdict of FAIL at any depth) is FAIL; a traceback
    beside a PASS is NO-DATA (the script crashed after saying so); PASS needs the script's own PASS line, JSON
    status or verdict of PASS, a release state that passed, or its MOBILE_OWN_SUCCESS marker."""
    text = out + "\n" + err
    if any(re.match(r"^(?:[\w ]+: )?FAIL\b", line.strip()) for line in text.splitlines()):
        return FAIL
    try:
        record = json.loads(out)
    except ValueError:
        record = None
    if isinstance(record, dict) and _mobile_verdict_in(record) == FAIL:
        return FAIL
    if "Traceback (most recent call last)" in text:
        return NODATA
    if isinstance(record, dict):
        states = record.get("states") if row.get("states") else None
        if isinstance(states, dict) and any(isinstance(v, dict) and v.get("verdict") == "PASS" for v in states.values()):
            return PASS
        if PASS in (record.get("status"), record.get("verdict")):
            return PASS
    if any(re.match(r"^(?:[\w ]+: )?PASS\b", line.strip()) for line in text.splitlines()):
        return PASS
    return PASS if _mobile_own_success(row["script"], out, err) else NODATA


def _file_ref(path):
    """'path sha256:HEX' for a readable file, 'path (folder)' for a folder, '' for anything else."""
    try:
        if os.path.isdir(path):
            return "%s (folder)" % path
        with open(path, "rb") as fh:
            return "%s sha256:%s" % (path, hashlib.sha256(fh.read()).hexdigest())
    except OSError:
        return ""


def _mobile_run(argv, timeout):
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr


def mobile_invoke(handler, inputs, run=None):
    """invoke() for the mobile workflow: `<script> <sub> ARGS`, a reply of {"verdict", "output", "evidence_refs"}.
    Exit 0 with a PASS verdict the script printed itself (and no NO-DATA of its own) is PASS and carries evidence (the
    command, the output hash, and the hash of every file the person named and of the receipt the script printed);
    exit 0 with no such verdict, exit 2, an empty answer, a script this install does not carry, a timeout, or a
    script that reports its own NO-DATA is NO-DATA; anything else FAIL. Only PASS carries evidence."""
    def reply(verdict, output, evidence=()):
        return {"verdict": verdict, "output": output, "evidence_refs": list(evidence)}
    row = _mobile_row_for(handler) if isinstance(handler, str) else None
    if row is None:
        return reply(NODATA, "no mobile handler is named %r" % (handler,))
    if not isinstance(inputs, dict):
        return reply(NODATA, "the door's inputs were not a set of named values")
    tool = mobile_tool(row["script"])
    if tool is None:
        return reply(NODATA, "%s.py is not installed beside the door: the mobile engine beyond the simulator workflow "
                             "ships in the Brother repository checkout under scripts/; use a checkout (or a later "
                             "release that bundles it), then ask again" % row["script"])
    argv, problem = _mobile_argv(row, inputs)
    if problem:
        return reply(NODATA, "nothing was started: " + problem)
    try:
        code, out, err = (run or _mobile_run)([sys.executable, tool] + argv, row["timeout"])
    except subprocess.TimeoutExpired:
        return reply(NODATA, "%s.py did not answer within %d seconds" % (row["script"], row["timeout"]))
    except OSError as exc:
        return reply(NODATA, "%s.py could not be started: %s" % (row["script"], exc))
    if code == 2:
        return reply(NODATA, (out if out.strip() else err).strip() or "%s.py reported NO-DATA" % row["script"])
    if code != 0:
        return reply("FAIL", "%s.py exited %s; %s\n%s" % (row["script"], code, stderr_tail(err), out.strip()))
    if not out.strip():
        return reply(NODATA, "%s.py answered with nothing to show" % row["script"])
    reported = _mobile_reported(row, out)
    if reported:
        return reply(reported, out)
    own = _mobile_own_verdict(row, out, err)
    if own != PASS:
        return reply(own, "%s.py exited 0 but %s\n%s" % (row["script"], "printed a FAIL of its own" if own == FAIL else
                     "printed no PASS verdict of its own (or crashed after one), so there is no verdict",
                     out.strip() + ("\n" + err.strip() if own == FAIL and err.strip() else "")))
    refs = ["%s exit 0 stdout sha256:%s" % (" ".join((row["script"] + ".py",) + row["sub"]),
                                            hashlib.sha256(out.encode("utf-8")).hexdigest())]
    for name, kind, _form in row["args"]:
        value = inputs.get(name)
        if kind in ("file", "dir", "out") and isinstance(value, str):
            refs.append(_file_ref(value))
        elif kind == "files" and isinstance(value, list):
            refs.extend(_file_ref(v) for v in value)
    last = out.strip().splitlines()[-1]
    if os.path.isfile(last):
        refs.append(_file_ref(last))
    return reply("PASS", out, [r for r in refs if r])


def mobile_probe(name, value=None):
    """The door's check of the host BEFORE it starts anything. 'simulator': True when the named simulator (or, with
    no name, any) is listed by simctl, False when simctl lists others, None when simctl cannot be asked. 'appium':
    True when the server answers its /status."""
    if name == "simulator":
        xcrun = shutil.which("xcrun")
        if not xcrun:
            return None
        try:
            proc = subprocess.run([xcrun, "simctl", "list", "devices", "available", "--json"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            groups = json.loads(proc.stdout)["devices"].values()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, AttributeError, TypeError):
            return None
        if proc.returncode != 0:
            return None
        udids = {str(d.get("udid", "")).lower() for g in groups if isinstance(g, list) for d in g
                 if isinstance(d, dict)}
        return bool(udids) if value is None else str(value).lower() in udids
    if name == "appium":
        url = (value or MOBILE_APPIUM_URL).rstrip("/")
        if not url.startswith(("http://", "https://")):
            return False
        try:
            import urllib.request
            with urllib.request.urlopen(url + "/status", timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception:  # any failure to reach it is the same answer: not reachable
            return False
    return None


def run_mobile_capability(capability, outcome=None, typed=None, allow=None, invoke=None, probe=None):
    """The door's mobile route (OUT1.f): the same adapter, plus the host check. Nothing starts while an input, a
    simulator, an Appium server or a permission is missing; the reply names each missing prerequisite and how it is
    supplied, so the original request can be asked again unchanged. Authority is read plus exactly what `allow`
    names: write never implies device. Every reply says human acceptance is NO-DATA, and the visual only route says
    a screenshot proves no interaction."""
    row = next((c for c in MOBILE_CAPABILITIES if c["id"] == capability), None)
    if row is None:
        return _run_capability(MOBILE_CAPABILITIES, None, capability, outcome, typed, allow)
    inputs = dict(typed or {})
    if outcome and row["outcome_input"] and not _given(inputs.get(row["outcome_input"])):
        inputs[row["outcome_input"]] = outcome
    probe = probe or mobile_probe
    spec = dict(row, prerequisites=list(row["prerequisites"]), required_authority=list(row["required_authority"]))
    device = inputs.get("device") if isinstance(inputs.get("device"), str) else None
    physical = inputs.get("physical_device") is True or inputs.get("platform") == "android"
    if row["device"] and device and not physical and "simulator" in row["env"]:
        physical = probe("simulator", device) is False
    if row["device"] and physical:
        spec["required_authority"].append("device")
    if row["write_when"] and _given(inputs.get(row["write_when"])) and "write" not in spec["required_authority"]:
        spec["required_authority"].append("write")
    available = [k for k, v in inputs.items() if _given(v)]
    for need in row["env"]:
        if need == "simulator" and not physical:
            found = probe("simulator", device)
        elif need == "appium":
            found = probe("appium", inputs.get("server") if isinstance(inputs.get("server"), str) else None)
        else:
            continue
        spec["prerequisites"].append(need)
        if found is True:
            available.append(need)
    context = {"available": available, "authority": ["read"] + list(allow or [])}
    got = invoke_capability(spec, inputs, context, invoke or mobile_invoke)
    got["human_acceptance"] = NODATA
    got["limit"] = row["limit"]
    got["identity"] = {"device": device, "bundle_id": inputs.get("bundle_id"),
                       "host": "%s %s" % (sys.platform, platform.machine())}
    if row["limit"] == VISUAL_LIMIT:
        got["interaction"] = NODATA
        got["output"] = (got["output"] + "\n" if got["output"] else "") + VISUAL_LIMIT
    if got["missing_prerequisites"]:
        got["prepare"] = [{"missing": m, "route": MOBILE_PREPARE.get(m, ("", "supply this input"))[0],
                           "how": MOBILE_PREPARE.get(m, ("", "supply this input"))[1]}
                          for m in got["missing_prerequisites"]]
        got["resume"] = {"capability": capability, "inputs": sorted(inputs)}
    return got


#: THE ASSURANCE CAPABILITIES (OUT1.k): what the door can ask the EXISTING BrotherSBE command line to do, under the
#: four verbs (review, status, next, deliver), beside the Vault, data and mobile rows and run by the same adapter.
#: Each row names the handler the BrotherSBE command line runs (its DOOR_HANDLERS table owns the subcommand each
#: handler becomes, the type of every input and the permission it needs, and its discovery is generated from the
#: parser itself, so no gate, tier, policy, fence or handover logic lives here). The permission is read, write for
#: anything that saves a file, a receipt, a decision package or a ledger line, and approve for a human decision: a
#: review verdict, a handover acknowledged or declined. Preparing a handover is write; acknowledging it is its own
#: request, needs approve and the receiver's name, and is never implied by preparing. The unbuilt exceptions command
#: is a row on purpose: it answers NO-DATA naming itself as not built and the supported alternative.
ASSURANCE_TIMEOUT_S = 900
ASSURANCE_OWNERSHIP = ("Preparing a handover is not acceptance: ownership stays with the outgoing owner until the "
                       "named receiver acknowledges it, which is a separate request (assurance.handover_acknowledge) "
                       "that needs the receiver's name and approve permission.")
ASSURANCE_IDENTITY = ("The door records the name it was given as the receiver; it cannot check who is typing it, so "
                      "the acknowledgement is only as good as the person making it.")


def _assure(cap, verb, need=(), authority="read"):
    return {"id": "assurance." + cap, "handler": "assurance_existing_" + cap, "verb": verb,
            "prerequisites": list(need), "required_authority": [authority], "outcome_input": None}


ASSURANCE_CAPABILITIES = (
    _assure("discover", "status"),
    _assure("review", "review", ("path",)),
    _assure("review_record", "review", ("path", "reviewer", "reviewer_type", "result"), "approve"),
    _assure("verify", "review", ("path",), "write"),
    _assure("gate", "review", ("path",)),
    _assure("design", "review", ("path",)),
    _assure("score", "review", ("path",)),
    _assure("intake", "review", ("answers", "path"), "write"),
    _assure("impact", "review", ("path",), "write"),
    _assure("policy", "review", ("path",)),
    _assure("review_route", "review", ("path",), "write"),
    _assure("converge", "review", ("dossier", "base", "head"), "write"),
    _assure("explain", "review", ("target",), "write"),
    _assure("status", "status", ("path",)),
    _assure("scope_report", "status", ("cwd",)),
    _assure("scope_verify", "status", ("base", "cwd")),
    _assure("fences", "status", ("cwd",)),
    _assure("fence_audit", "status", ("cwd",)),
    _assure("instruction_surface", "status", ("path",)),
    _assure("task_list", "status", ("cwd",)),
    _assure("task_check", "status", ("cwd",)),
    _assure("lineage", "status", ("artifact",)),
    _assure("evidence_verify", "status", ("receipt",)),
    _assure("evidence_show", "status", ("receipt",)),
    _assure("handover_show", "status", ("dossier",)),
    _assure("program_status", "status", ("path",)),
    _assure("program_check", "status", ("path",)),
    _assure("protections_verify", "status", ("repository", "branch")),
    _assure("pr_verify", "status", ("number", "repo")),
    _assure("work_check", "status", ("task_id",)),
    _assure("doctor", "status", ()),
    _assure("book_check", "status", ("path",)),
    _assure("exceptions", "status", ()),
    _assure("plan", "next", ("dossier",)),
    _assure("adopt", "next", ("path",)),
    _assure("work_brief", "next", ("plan", "task")),
    _assure("testkit_sheet", "next", ("dossier",), "write"),
    _assure("handover_prepare", "deliver", ("dossier", "outgoing", "receiver"), "write"),
    _assure("handover_acknowledge", "deliver", ("dossier", "receiver"), "approve"),
    _assure("handover_reject", "deliver", ("dossier", "receiver", "reason"), "approve"),
    _assure("evidence_run", "deliver", ("check", "out"), "write"),
    _assure("work_start", "deliver", ("task_id", "plan"), "write"),
    _assure("work_finish", "deliver", ("task_id",), "write"),
    _assure("program_board", "deliver", ("out",), "write"),
    _assure("map", "deliver", ("out",), "write"),
)


def assurance_tool():
    """The BrotherSBE launcher beside this door: the checkout's products/ tree, else the installed runtime's
    products/ tree (SHIP1.a ships it there, the same release as this door), else a kept install of the brothersbe
    plugin in this client's plugin cache (newest version folder by name). None when none exists."""
    for path in (os.path.join(REACH_ROOT, "products", "brothersbe", "bin", "sbe"),
                 os.path.join(HERE, "products", "brothersbe", "bin", "sbe")):
        if os.path.isfile(path):
            return path
    found = sorted(glob.glob(os.path.join(brother_paths.config_dir(), "plugins", "cache", "*", "brothersbe", "*",
                                          "bin", "sbe")))
    return found[-1] if found else None  # ponytail: name order, a version sort if 1.9 against 1.10 ever matters


def assurance_invoke(handler, inputs, tool=None, timeout=ASSURANCE_TIMEOUT_S):
    """invoke() for BrotherSBE: `sbe door HANDLER` through _tool_invoke."""
    return _tool_invoke(tool or assurance_tool(), handler, inputs, "BrotherSBE",
                        "BrotherSBE is not installed beside the door: it ships as its own brothersbe plugin; "
                        "install that plugin, then ask again", timeout)


def run_assurance_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's assurance route (OUT1.k): the same adapter, plus the verb and the handover truth. The reply never
    says a receiver accepted unless THIS call was the acknowledgement and it completed; preparing a handover says in
    so many words that nothing was accepted."""
    got = _run_capability(ASSURANCE_CAPABILITIES, invoke or assurance_invoke, capability, outcome, typed, allow)
    row = next((c for c in ASSURANCE_CAPABILITIES if c["id"] == capability), None)
    if row is None:
        return got
    got["verb"] = row["verb"]
    if capability in ("assurance.handover_prepare", "assurance.handover_acknowledge", "assurance.handover_reject"):
        done = got["state"] == "COMPLETE"
        got["receiver_acceptance"] = "recorded" if done and capability.endswith("acknowledge") else \
            "declined" if done and capability.endswith("reject") else NODATA
        got["ownership_transferred"] = done and capability.endswith("acknowledge")
        if capability.endswith("prepare"):
            got["note"] = ASSURANCE_OWNERSHIP
        elif capability.endswith("acknowledge"):
            got["note"] = ASSURANCE_IDENTITY
    return got


#: THE HOST CAPABILITIES (OUT1.l): what the door can ask the INSTALLED plugin about itself, beside the Vault, data,
#: mobile and assurance rows and run by the same adapter. Every handler reads or runs a tool that ships INSIDE the
#: installed plugin (its runtime manifest, verify_runtime.py, the BrotherMode doctor, the BrotherMode command line),
#: never the checkout the door may happen to sit in: a checkout's source says nothing about the revision a host is
#: running. Identity, update, diagnose and verify are read; only repair (the doctor's own --fix) needs write.
HOST_TIMEOUT_S = 300
HOST_MANIFEST = "RUNTIME-MANIFEST.json"
HOST_SETUP = "claude plugin marketplace add khalilmaaouni/Brother && claude plugin install brother@brother"
HOST_REINSTALL = "claude plugin install brother@brother"


def _host(cap, handler, tool, authority="read"):
    return {"id": "host." + cap, "handler": handler, "tool": tool, "prerequisites": [],
            "required_authority": [authority], "outcome_input": None}


HOST_CAPABILITIES = (
    _host("identity", "host_existing_identity", "runtime/" + HOST_MANIFEST),
    _host("update", "host_existing_update", "runtime/hooks/brothermode/tools/brothermode_cli.py"),
    _host("diagnose", "host_existing_doctor", "runtime/hooks/brothermode/scripts/doctor.py"),
    _host("verify", "host_existing_package_verify", "runtime/verify_runtime.py"),
    _host("repair", "host_existing_doctor_fix", "runtime/hooks/brothermode/scripts/doctor.py", "write"),
)
#: What each host enforces, said the same way every time and never upgraded by a reply: Cursor stays advisory until
#: its signed-in canary demonstrates a deny, and Codex's package validation proves the package shape, not live hook
#: enforcement (AGENTS.md, "Codex: package validation and host boundaries"). Claude's line is read from the
#: installed hooks.json each time, by host_enforcement below.
HOST_BOUNDARIES = {
    "codex": "NO-DATA: the package shape is validated, but live hook enforcement on Codex has not been demonstrated",
    "cursor": "advisory: Cursor's signed-in canary has not demonstrated a deny, so nothing there is enforced",
}


def installed_plugin(config=None, here=HERE):
    """(the INSTALLED brother plugin root, how it was found) or (None, why).

    This door's own folder when it runs from an install (runtime/ holding the manifest, in a plugin folder that is
    not a checkout's bundle/ beside its scripts/), else the install the host records in its installed_plugins.json,
    else none. A checkout's bundle/ is never an install: a source tree is not what any host is running."""
    root = os.path.dirname(here)
    in_checkout = os.path.basename(root) == "bundle" and os.path.isdir(os.path.join(os.path.dirname(root), "scripts"))
    if os.path.basename(here) == "runtime" and os.path.isfile(os.path.join(here, HOST_MANIFEST)) and not in_checkout:
        return root, "this door runs from the installed plugin at %s" % root
    record = os.path.join(config or brother_paths.config_dir(), "plugins", "installed_plugins.json")
    try:
        with open(record, encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return None, "this client records no installed plugins (%s cannot be read)" % record
    found = [e.get("installPath") for key, entries in sorted(plugins.items()) if key.split("@")[0] == "brother"
             for e in (entries if isinstance(entries, list) else []) if isinstance(e, dict)]
    found = [p for p in found if isinstance(p, str) and os.path.isfile(os.path.join(p, "runtime", HOST_MANIFEST))]
    if not found:
        return None, "the brother plugin is not installed for this client (%s names no install of it)" % record
    return found[-1], "the install %s records" % record


def host_enforcement(root):
    """{host: the enforcement boundary, in words}. Claude's is read from the INSTALLED hooks.json: enforced only when
    it declares a deny class PreToolUse hook. Codex and Cursor say what has not been demonstrated."""
    out = dict(HOST_BOUNDARIES)
    try:
        with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as fh:
            pre = json.load(fh)["hooks"]["PreToolUse"]
        deny = any(h.get("class") == "deny" for e in pre for h in (e.get("hooks") or []) if isinstance(h, dict))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        deny = False
    out["claude"] = ("enforced: the installed hooks.json declares a deny class PreToolUse hook" if deny else
                     "NO-DATA: the installed hooks.json declares no deny class hook, so nothing is shown enforced")
    return out


def _host_run(argv, timeout=HOST_TIMEOUT_S):
    """(exit code, output) of one installed tool, run from the person's own folder; (None, why) if it cannot start."""
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "could not run: %s" % exc
    return proc.returncode, proc.stdout


def host_invoke(handler, inputs, root=None, run=None, config=None):
    """invoke() for the host rows: read the installed manifest, or run the installed tool the row names. A missing
    install or a missing tool is NO-DATA naming it, never a pass."""
    row = next((r for r in HOST_CAPABILITIES if r["handler"] == handler), None)
    if row is None:
        return {"verdict": NODATA, "output": "no host handler is named %s" % handler, "evidence_refs": []}
    if root is None:
        root, why = installed_plugin(config)
        if root is None:
            return {"verdict": NODATA, "output": why, "evidence_refs": []}
    tool = os.path.join(root, row["tool"])
    if not os.path.isfile(tool):
        return {"verdict": NODATA, "evidence_refs": [],
                "output": "the installed plugin at %s carries no %s" % (root, row["tool"])}
    if handler == "host_existing_identity":
        try:
            with open(tool, "rb") as fh:
                blob = fh.read()
            revision = json.loads(blob.decode("utf-8")).get("source_revision") or NODATA
        except (OSError, ValueError, AttributeError) as exc:
            return {"verdict": "FAIL", "output": "the installed %s cannot be read: %s" % (HOST_MANIFEST, exc),
                    "evidence_refs": []}
        try:
            with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
                version = json.load(fh).get("version") or NODATA
        except (OSError, ValueError, AttributeError):
            version = NODATA
        return {"verdict": "PASS", "evidence_refs": [tool],
                "output": "installed brother %s, revision %s, runtime manifest sha256 %s, at %s"
                          % (version, revision, hashlib.sha256(blob).hexdigest(), root)}
    argv = [sys.executable, "-B", tool] + {
        "host_existing_update": ["update", "--json"], "host_existing_doctor": ["--json"],
        "host_existing_doctor_fix": ["--fix"], "host_existing_package_verify": [os.path.join(root, "runtime")],
    }[handler]
    code, out = (run or _host_run)(argv)
    if code is None:
        return {"verdict": NODATA, "output": out, "evidence_refs": []}
    # exit 0 is the tool's PASS. verify_runtime's 2 is its own NO-DATA, and an update check that could not reach
    # the release list (exit 1) guessed nothing, so it is NO-DATA too; every other non zero exit is a FAIL.
    nodata = (handler == "host_existing_package_verify" and code == 2) or (handler == "host_existing_update" and code)
    verdict = "PASS" if code == 0 else NODATA if nodata else "FAIL"
    return {"verdict": verdict, "output": str(out).strip()[-2000:],
            "evidence_refs": ["%s exit %d" % (tool, code)]}


def run_host_capability(capability, outcome=None, typed=None, allow=None, invoke=None, config=None):
    """The door's host route (OUT1.l): the same adapter, plus the enforcement boundary per host and, when the
    install or the tool it needs is missing, the ONE setup action that makes the request runnable. Nothing was
    started in that case, so asking again after it is the whole resume."""
    got = _run_capability(HOST_CAPABILITIES, invoke or (lambda h, i: host_invoke(h, i, config=config)),
                          capability, outcome, typed, allow)
    row = next((c for c in HOST_CAPABILITIES if c["id"] == capability), None)
    if row is None:
        return got
    root, _why = installed_plugin(config)
    got["enforcement"] = host_enforcement(root) if root else dict(HOST_BOUNDARIES, claude=NODATA + ": not installed")
    if got["state"] == NODATA or got["verdict"] == NODATA:
        if root is None:
            got["setup_action"] = HOST_SETUP
        elif not os.path.isfile(os.path.join(root, row["tool"])):
            got["setup_action"] = HOST_REINSTALL
        if got.get("setup_action"):
            got["resume"] = "nothing was started; once that is done, ask for %s again" % capability
    return got


#: THE LOOP CAPABILITIES (OUT1.g): what the door can ask the EXISTING loop tools to do for a bounded run, beside the
#: Vault, data, mobile, assurance and host rows and run by the same adapter. Each row names the tool it runs (found
#: beside this door: scripts/ in a checkout, the runtime folder in an install, loop/ inside either). Nothing here
#: decides a seat, a model, a plan or a landing: start_run.sh, loop_watch.py, loop_report.py, stop_loop.sh and
#: close_unit.py keep their own approval, hold, pause and protected-path rules, and a refusal of theirs is relayed
#: word for word. The authority is read for status and the handoff, write for pause, resume and close, and approve
#: for the two that spend or end a run (start, stop). A LAUNCH IS NEVER A DELIVERY: a start that launched a driver
#: answers NO-DATA with `delivered: false`, because nothing has landed or closed yet; only the handoff, read from the
#: run's own records, says what was delivered. A person's words (a reason, the owner's words for lifting a hold) are
#: passed through as typed and are never invented here.
LOOP_TIMEOUT_S = 900
LOOP_START_TIMEOUT_S = 3600  # the launcher waits for the intake, the landing canary and the driver's first line
#: A call never lasts longer than its stated deadline (LOOP_TIMEOUT_S, LOOP_START_TIMEOUT_S): the stop request and the
#: kill wait are carved OUT of that deadline, so a tool gets the deadline minus these two to do its own work.
LOOP_TERM_GRACE_S = 100  # an overrunning tool is asked to stop (SIGTERM) and given this long before it is killed
LOOP_KILL_WAIT_S = 10  # after the kill, how long to wait for the pipe to close
LOOP_LIMIT = ("A launch is not a delivery: nothing has landed or closed until the handoff shows it. Ask for the "
              "handoff, or the status, to see what the run has done.")
LOOP_DEADLINE = re.compile(r"[0-2]\d:[0-5]\d")
LOOP_TOOLS = {"loop.status": "loop/loop_watch.py", "loop.handoff": "loop/loop_report.py",
              "loop.start": "loop/start_run.sh", "loop.pause": "loop/stop_loop.sh",
              "loop.resume": "loop/stop_loop.sh", "loop.stop": "loop/stop_loop.sh", "loop.close": "close_unit.py"}


def _loop(cap, authority="read", need=(), outcome=None):
    return {"id": "loop." + cap, "handler": "loop_existing_" + cap, "tool": LOOP_TOOLS["loop." + cap],
            "prerequisites": list(need), "required_authority": [authority], "outcome_input": outcome}


LOOP_CAPABILITIES = (
    _loop("status"),
    _loop("handoff"),
    _loop("start", "approve", ("deadline", "budget_usd")),
    _loop("pause", "write", ("reason",), "reason"),
    _loop("resume", "write"),
    _loop("stop", "approve", ("reason",), "reason"),
    _loop("close", "write"),
)


LOOP_DASH_LOOKALIKES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"  # the hyphen, figure, en, em and bar dashes, the minus sign


def _loop_word(value):
    """A typed value that is safe to hand a command line: text that does not start with a dash (so it can never become
    an option of the tool), holds no control or line separator character of any kind (a C0 control, DEL, U+0085,
    U+2028, U+2029: Unicode categories Cc, Zl and Zp) and no dash lookalike (U+2013 and its kin: a pasted character
    that looks like the option dash and that this estate never writes). None otherwise."""
    return value if isinstance(value, str) and value.strip() and not value.lstrip().startswith("-") \
        and not any(ord(ch) < 32 or unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in LOOP_DASH_LOOKALIKES
                    for ch in value) else None


def _loop_dry(inputs):
    """(dry flag, problem). `dry` is a real true or false or it is absent: any other value (the text "true", "yes", 1)
    is a problem, because an unknown value of a safety flag must block, never read as the dangerous case."""
    if "dry" not in inputs or inputs["dry"] is None:
        return False, None
    if isinstance(inputs["dry"], bool):
        return inputs["dry"], None
    return False, "dry must be true or false (give it as JSON, --input-json dry=true), not %r" % (inputs["dry"],)


def _loop_run(argv, timeout, env=None):
    """(exit code, output) of one loop tool, run from the person's own folder; (None, why) if it cannot start or does
    not answer in time. THE CALL NEVER LASTS LONGER THAN `timeout`: the tool works for the deadline minus the stop
    request's grace (LOOP_TERM_GRACE_S) and the kill wait (LOOP_KILL_WAIT_S), or half the deadline when it is shorter
    than the two together. A tool that overruns is then asked to stop (SIGTERM, so a launcher's own trap restores what
    it lifted and frees its lock), given what remains of the deadline minus the kill wait to do so, and only then
    killed."""
    start = time.monotonic()
    work = max(timeout - LOOP_TERM_GRACE_S - LOOP_KILL_WAIT_S, timeout / 2.0)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "could not run: %s" % exc
    try:
        out, _ = proc.communicate(timeout=work)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=max(start + timeout - LOOP_KILL_WAIT_S - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=max(start + timeout - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                proc.stdout.close()  # a grandchild still holds the pipe; the tool itself is dead and nothing more is read
            proc.wait()  # SIGKILL cannot be ignored, so this returns at once and reaps the tool
        return None, "did not answer within %d seconds and was stopped" % timeout
    return proc.returncode, out


def _loop_json(path):
    """The JSON object in `path`, or None: a missing, unreadable, non UTF-8, truncated or non object file is no record."""
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _loop_time(value):
    """True for a real finite number of seconds (a bool, text, NaN or infinity is not a time)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def loop_evidence(evidence=None):
    """The evidence folder the loop writes under: the one named, else START_RUN_EVIDENCE as start_run.sh honours it
    (an empty value counts as unset there too), else ~/.claude/evidence."""
    return evidence or os.environ.get("START_RUN_EVIDENCE") or os.path.expanduser("~/.claude/evidence")


def loop_run_plan(evidence=None):
    """The plan the loop's LAST LAUNCHED run was prepared for, or None. The plan is read from the intake's launch
    settings (<evidence>/loop-intake/launch-env.sh: `export BROTHER_WBS=<path>`), but only when those settings are
    provably the launched run's own: the intake record (CURRENT.json) says READY, the settings file was written by
    that same intake (its first line carries that record's time and the verdict READY), and a launch record
    (<evidence>/loop-starts/launch-<stamp>.json, written by start_run.sh at STARTED) is no older than that intake. A
    later refused or merely prepared intake therefore never points an earlier run's handoff at its plan. None when
    any of that is missing, unreadable or does not agree, and never the estate's built in default plan."""
    ev = loop_evidence(evidence)
    rec = _loop_json(os.path.join(ev, "loop-intake", "CURRENT.json"))
    if rec is None or rec.get("verdict") != "READY" or not _loop_time(rec.get("epoch")) \
            or not isinstance(rec.get("at"), str):
        return None
    launched = [r["at"] for r in (_loop_json(p) for p in glob.glob(os.path.join(ev, "loop-starts", "launch-*.json")))
                if r is not None and r.get("schema") == "launch-v1" and _loop_time(r.get("at"))]
    if not launched or max(launched) < rec["epoch"]:
        return None
    try:
        with open(os.path.join(ev, "loop-intake", "launch-env.sh"), encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except (OSError, UnicodeError):
        return None
    if lines[0] != "# written by loop_intake.py at %s; verdict READY. Do not edit by hand: prepare a new intake." \
            % rec["at"]:
        return None
    plan = None
    for line in lines:
        if line.startswith("export BROTHER_WBS="):
            try:
                parts = shlex.split(line[len("export BROTHER_WBS="):])
            except ValueError:
                return None
            plan = parts[0] if len(parts) == 1 else None
    ok = isinstance(plan, str) and re.fullmatch(r"docs/plan/[A-Za-z0-9_.\-/]+\.json", plan) and ".." not in plan.split("/")
    return plan if ok else None


def _loop_argv(handler, inputs, tool, evidence=None):
    """(argv, timeout, env, problem) for one loop handler. A typed value that is not valid is a problem, so nothing
    starts on a guess. The environment is the door's own plus BROTHER_WBS only when a plan is named: by the person, or
    for the handoff by the run's own launch record."""
    py = [sys.executable, "-B", tool]
    env = None
    if _given(inputs.get("plan")):
        plan = _loop_word(inputs.get("plan"))
        if plan is None:
            return None, 0, None, "plan %r is not a usable path" % (inputs.get("plan"),)
        env = dict(os.environ, BROTHER_WBS=plan)
    dry, problem = _loop_dry(inputs)
    if problem and handler in ("loop_existing_start", "loop_existing_close"):
        return None, 0, None, problem
    if handler == "loop_existing_status":
        log = inputs.get("log")
        if _given(log) and _loop_word(log) is None:
            return None, 0, None, "log %r is not a usable path" % (log,)
        return py + (["--log", log] if _given(log) else []), LOOP_TIMEOUT_S, env, None
    if handler == "loop_existing_handoff":
        log, hours = inputs.get("log"), inputs.get("since_hours")
        if _given(log) and _loop_word(log) is None:
            return None, 0, None, "log %r is not a usable path" % (log,)
        if _given(hours) and (isinstance(hours, bool) or not isinstance(hours, int) or hours < 1):
            return None, 0, None, "since_hours %r is not a whole number of hours" % (hours,)
        if _given(log) and _given(hours):
            return None, 0, None, "log and since_hours name two different windows; give one"
        # a named log is some run's, and the launch record names only the latest: that plan is not known to be its own
        plan = (inputs.get("plan") if _given(inputs.get("plan")) else None if _given(log) else loop_run_plan(evidence))
        if plan is None:
            return None, 0, None, ("no plan was given and the run records none (a named log is not matched to the "
                                   "latest launch), so there is no plan to report progress against; name the plan")
        env = dict(os.environ, BROTHER_WBS=plan)
        return (py + (["--run", log] if _given(log) else ["--since-hours", str(hours)] if _given(hours) else [])
                + ["--plan", plan], LOOP_TIMEOUT_S, env, None)
    if handler == "loop_existing_start":
        deadline, budget = inputs.get("deadline"), inputs.get("budget_usd")
        if not (isinstance(deadline, str) and LOOP_DEADLINE.fullmatch(deadline)):
            return None, 0, None, "deadline %r is not a time of day as HH:MM" % (deadline,)
        if isinstance(budget, bool) or not isinstance(budget, (int, float, str)) or \
                not re.fullmatch(r"[0-9]+(\.[0-9]+)?", str(budget)) or float(budget) <= 0:
            return None, 0, None, "budget_usd %r is not a positive amount" % (budget,)
        argv = ["bash", tool, "--deadline", deadline, "--budget-usd", str(budget)]
        for key, flag in (("scope", "--scope"), ("plan", "--plan"), ("lift_hold", "--lift-hold")):
            if _given(inputs.get(key)):
                if _loop_word(inputs.get(key)) is None:
                    return None, 0, None, "%s %r is not usable" % (key, inputs.get(key))
                argv += [flag, inputs[key]]
        return argv + (["--dry"] if dry else []), LOOP_START_TIMEOUT_S, None, None
    if handler in ("loop_existing_pause", "loop_existing_stop"):
        why = inputs.get("reason")
        if _loop_word(why) is None:
            return None, 0, None, "a reason is needed, as words that do not start with a dash"
        return ["bash", tool, "--pause" if handler.endswith("pause") else "--reason", why], LOOP_TIMEOUT_S, None, None
    if handler == "loop_existing_resume":
        return ["bash", tool, "--resume"], LOOP_TIMEOUT_S, None, None
    if handler == "loop_existing_close":
        if not _given(inputs.get("plan")):
            return None, 0, None, ("no plan was named, and close_unit's built in default plan is not this run's; "
                                   "name the plan to close against")
        unit = inputs.get("unit")
        if _given(unit) and (_loop_word(unit) is None or not re.fullmatch(r"[A-Za-z0-9_.-]+", unit)):
            return None, 0, None, "unit %r is not a unit id" % (unit,)
        return py + ([unit] if _given(unit) else []) + (["--dry"] if dry else []), LOOP_TIMEOUT_S, env, None
    return None, 0, None, "no loop handler is named %s" % handler


def _takeaway_has_no_data(text):
    """True when the report's KEY TAKEAWAY section carries a NO-DATA line, or has no readable section: loop_report
    always prints the heading and exits 0, even when the plan or the ledger is unreadable, so the heading proves
    nothing. The section runs from its heading to the ACTIONS heading."""
    lines = text.split("\n")
    try:
        start = lines.index("KEY TAKEAWAY")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("ACTIONS"))
    except (ValueError, StopIteration):
        return True
    return any("NO-DATA" in ln for ln in lines[start + 1:end])


def _loop_verdict(handler, inputs, code, out):
    """(verdict, words, launched) from the tool's own exit code AND the line it printed: an exit 0 with the wrong or no
    line is never a PASS (a tool that exits 0 and prints nothing proved nothing). A tool's refusal is NO-DATA, nothing
    started. Lines are split on a newline only, so a separator character inside a quoted message cannot forge one.
    `launched` is true only for a real start that said STARTED, decided here and never read back from relayed text."""
    text = str(out).strip()
    last = text.split("\n")[-1] if text else ""
    if handler == "loop_existing_status":
        return ("PASS" if code == 0 and last.startswith("HEALTHY") else "FAIL" if code == 1 and last.startswith("ACT")
                else NODATA), text[-2000:], False
    if handler == "loop_existing_handoff":
        if code == 0 and not _takeaway_has_no_data(text):
            return "PASS", text[-4000:], False
        return NODATA, ("the report has no readable key takeaway (a NO-DATA line there means progress is unknown, "
                        "not zero): " + text)[-4000:], False
    if handler == "loop_existing_start":
        rows = text.split("\n")
        refusal = next((ln for ln in rows if ln.startswith("START REFUSED")), None)
        if refusal:
            return NODATA, refusal + " (nothing was started)", False
        if code == 0 and inputs.get("dry") is True and any(ln.startswith("DRY") for ln in rows):
            return NODATA, "dry run: nothing was started. " + LOOP_LIMIT, False
        if code == 0 and any(ln.startswith("STARTED") for ln in rows):
            return NODATA, "launched. " + LOOP_LIMIT, inputs.get("dry") is not True
        return NODATA, ("the launcher ended (exit %s) without saying STARTED, so no run is claimed: %s"
                        % (code, last))[:600], False
    if handler == "loop_existing_pause":
        return ("PASS" if code == 0 and last.startswith("PAUSED") else NODATA), text[-600:], False
    if handler == "loop_existing_resume":
        if code == 0 and last.startswith("RESUMED"):
            return ("PASS", text[-600:] + " (this only lifts a pause; a run that was stopped is not restarted, "
                    "start it again with loop.start)", False)
        return NODATA, text[-600:], False
    if handler == "loop_existing_stop":
        return ("PASS" if code == 0 and last.startswith("STOPPED") else "FAIL" if code == 1 else NODATA), text[-1000:], False
    if handler == "loop_existing_close":
        if inputs.get("dry") is True:
            return NODATA, "dry run: nothing was closed. " + text[-600:], False
        return ("PASS" if code == 0 and re.match(r"CLOSED +[1-9]", last) else NODATA), text[-2000:], False
    return NODATA, text[-600:], False


def loop_invoke(handler, inputs, run=None, root=None, evidence=None):
    """invoke() for the loop rows: run the existing tool the row names and read its own exit code and line. A missing
    tool, a value that is not valid, a tool that cannot start and an unreadable answer are NO-DATA naming the cause,
    never a pass. The reply also carries `launched`, a flag set only by a start that said STARTED."""
    row = next((r for r in LOOP_CAPABILITIES if r["handler"] == handler), None)
    if row is None:
        return {"verdict": NODATA, "output": "no loop handler is named %s" % handler, "evidence_refs": []}
    tool = os.path.join(root or HERE, row["tool"])
    if not os.path.isfile(tool):
        return {"verdict": NODATA, "evidence_refs": [],
                "output": "this install carries no %s, so nothing was started" % row["tool"]}
    inputs = inputs if isinstance(inputs, dict) else {}
    argv, timeout, env, problem = _loop_argv(handler, inputs, tool, evidence)
    if problem:
        return {"verdict": NODATA, "output": problem + "; nothing was run", "evidence_refs": []}
    code, out = (run or _loop_run)(argv, timeout, env)
    if code is None:
        return {"verdict": NODATA, "output": "%s %s" % (row["tool"], out), "evidence_refs": []}
    verdict, words, launched = _loop_verdict(handler, inputs, code, out)
    return {"verdict": verdict, "output": words, "evidence_refs": ["%s exit %d" % (tool, code)],
            "launched": launched}


def run_loop_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's loop route (OUT1.g): the same adapter, plus the delivery truth. A start never reports a delivery:
    `launched` says a driver was started (the verdict's own flag, never a text match), `delivered` is false until the
    handoff shows landed and closed work."""
    seen = {}
    base = invoke or loop_invoke

    def spy(handler, inputs):
        seen["reply"] = base(handler, inputs)
        return seen["reply"]

    got = _run_capability(LOOP_CAPABILITIES, spy, capability, outcome, typed, allow)
    if capability == "loop.start":
        reply = seen.get("reply")
        got["launched"] = isinstance(reply, dict) and reply.get("launched") is True
        got["delivered"] = False
        got["limit"] = LOOP_LIMIT
    return got


#: THE CALIBRATED CAPABILITIES (OUT1.h): what the door can ask the EXISTING second opinion tools (Jev) to do, beside
#: the Vault, data, mobile, assurance, host and loop rows and run by the same adapter. status reads the installed
#: configuration, ledger, budget and promotion files through jev_status.py and asks nothing: no model, no bridge, no
#: seam call, and it creates and changes nothing. review and deliver ask the two seams jev_door.py already wires
#: (J065, J102) and so are bounded by that seam's own mode (off asks nothing), registry, privacy gate, deadline,
#: daily budget, canary marker and promotion rules; this door never sets a mode, never picks a seam, never
#: passes a config or registry path and never raises a limit. They spend a model call, so they need approve.
#: The seam's failure breaker is NOT among the rules kept here: it counts in one process's memory and every door
#: call is a fresh process, so through this door it never opens; the daily budget is the only cap across calls.
#: calibrated.status calls a seam askable here only on a route consumer admission shows (ADM1.b).
#: A SECOND OPINION IS NOT A VERDICT: `check_verdict` in the reply is the person's own typed verdict for the check,
#: echoed unchanged; no opinion, however confident, can raise it, and `release_evidence` is always false. Once the
#: tool answered, the row's own verdict, state and exit code are that check verdict too (ADM1.b): whether an
#: opinion was obtained is read from `second_opinion.answered`, never from the top of the reply.
JEV_TIMEOUT_S = 120
JEV_WAIT_MAX_S = 60.0
JEV_WAIT_DEFAULT_S = 20.0
JEV_LIMIT = ("A second opinion is recorded beside the check, never acted on here, and never release evidence: the "
             "check's own verdict stays the verdict.")
JEV_TOOLS = {"calibrated.status": "jev_status.py", "calibrated.review": "jev_door.py",
             "calibrated.deliver": "jev_door.py"}
JEV_VERDICTS = ("PASS", "FAIL", NODATA)
JEV_ATTEMPT = re.compile(r"[0-9a-f]{32}")


def _jev(cap, authority="read", need=(), outcome=None):
    return {"id": "calibrated." + cap, "handler": "jev_existing_" + cap, "tool": JEV_TOOLS["calibrated." + cap],
            "prerequisites": list(need), "required_authority": [authority], "outcome_input": outcome}


JEV_CAPABILITIES = (
    _jev("status"),
    _jev("review", "approve", ("output", "check_verdict")),
    _jev("deliver", "approve", ("note", "check_verdict", "quotes")),
)


def jev_check_verdict(value):
    """The check's own verdict as the person typed it (PASS, FAIL or NO-DATA, any case), or None for anything else."""
    text = value.strip().upper() if isinstance(value, str) else None
    return text if text in JEV_VERDICTS else None


def _jev_text(value):
    return value if isinstance(value, str) and value.strip() and "\x00" not in value else None


def _jev_argv(handler, inputs, tool):
    """(argv, stdin text, problem). Text reaches the tool on standard input, never on its command line, so no word
    in it can become an option. Nothing starts on a value that is not valid."""
    py = [sys.executable, "-B", tool]
    if handler == "jev_existing_status":
        view = consumer_admission()
        admitted = [v for v in ("review", "deliver") if capability_admitted("calibrated." + v, view)]
        return py + ["--effective"] + [w for v in admitted for w in ("--admitted", v)], None, None
    if jev_check_verdict(inputs.get("check_verdict")) is None:
        return None, None, "check_verdict must be PASS, FAIL or NO-DATA: the check's own verdict, given as it stands"
    wait = inputs.get("wait_s", JEV_WAIT_DEFAULT_S)
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= JEV_WAIT_MAX_S:
        return None, None, "wait_s must be a number of seconds from 0 to %d" % JEV_WAIT_MAX_S
    if handler == "jev_existing_review":
        text = _jev_text(inputs.get("output"))
        if text is None:
            return None, None, "output must be the failing check's output as text"
        return py + ["review", "--output", "-", "--json", "--wait", str(wait)], text, None
    text, said = _jev_text(inputs.get("note")), inputs.get("quotes")
    quotes = ({"yes": "yes", "no": "no"}.get(said.strip().lower()) if isinstance(said, str)
              else "yes" if said is True else "no" if said is False else None)
    if text is None:
        return None, None, "note must be the done note as text"
    if quotes is None:
        return None, None, "quotes must be yes or no: whether the note quotes a verifying command"
    return py + ["deliver", "--note", "-", "--quotes", quotes, "--json", "--wait", str(wait)], text, None


def _jev_run(argv, stdin_text, timeout):
    """(exit code, stdout) of one tool, or (None, why) when it cannot start or does not answer in time."""
    try:
        proc = subprocess.run(argv, input=stdin_text, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "did not answer within %d seconds and was stopped" % timeout
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return None, "could not run: %s" % exc
    return proc.returncode, proc.stdout


def _jev_json(out):
    """The last line of the tool's output as a JSON object, or None. Lines split on a newline only."""
    rows = str(out).strip().split("\n")
    try:
        got = json.loads(rows[-1]) if rows and rows[-1] else None
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _jev_verdict(handler, code, out):
    """(verdict, words, detail) from the tool's own JSON and exit code. PASS only when the tool says what this handler
    needs: for status a readable report, for review and deliver an answered opinion tied to one ledger attempt.
    Anything else is NO-DATA with the tool's own sentence, never a pass."""
    got = _jev_json(out)
    if handler == "jev_existing_status":
        lines = got.get("lines") if isinstance(got, dict) and got.get("kind") == "jev_effective_state" else None
        if not (isinstance(lines, list) and lines and all(isinstance(x, str) for x in lines)):
            return NODATA, "the second opinion readout answered unreadably", None
        words = "\n".join(lines)[:6000]
        return ("PASS" if code == 0 and got.get("readable") is True else NODATA), words, got
    verb = handler.rsplit("_", 1)[1]
    attempt = got.get("attempt_id") if isinstance(got, dict) else None
    line = got.get("line") if isinstance(got, dict) and isinstance(got.get("line"), str) else None
    if line is None:
        return NODATA, "the second opinion tool answered unreadably", None
    detail = {k: got.get(k) for k in ("entry_id", "mode", "phase", "said")}
    detail["attempt_id"] = attempt if isinstance(attempt, str) and JEV_ATTEMPT.fullmatch(attempt) else None
    answered = (code == 0 and got.get("verb") == verb and got.get("state") == "OK" and got.get("phase") == "answered"
                and isinstance(got.get("said"), bool) and detail["attempt_id"] is not None)
    return ("PASS" if answered else NODATA), line[:600], detail


def jev_invoke(handler, inputs, run=None, root=None):
    """invoke() for the calibrated rows: run the existing tool the row names and read its own answer. A missing tool,
    a value that is not valid, a tool that cannot start and an unreadable answer are NO-DATA naming the cause, never
    a pass. Reply: verdict, output, evidence_refs and `detail` (the tool's structured answer)."""
    row = next((r for r in JEV_CAPABILITIES if r["handler"] == handler), None)
    inputs = inputs if isinstance(inputs, dict) else {}
    if row is None:
        return {"verdict": NODATA, "output": "no calibrated handler is named %s" % handler, "evidence_refs": []}
    tool = os.path.join(root or HERE, row["tool"])
    if not os.path.isfile(tool):
        return {"verdict": NODATA, "evidence_refs": [],
                "output": "this install carries no %s, so nothing was asked" % row["tool"]}
    argv, stdin_text, problem = _jev_argv(handler, inputs, tool)
    if problem:
        return {"verdict": NODATA, "output": problem + "; nothing was asked", "evidence_refs": []}
    wait = inputs.get("wait_s", JEV_WAIT_DEFAULT_S) if handler != "jev_existing_status" else 0
    code, out = (run or _jev_run)(argv, stdin_text, JEV_TIMEOUT_S + wait)
    if code is None:
        return {"verdict": NODATA, "output": "%s %s" % (row["tool"], out), "evidence_refs": []}
    verdict, words, detail = _jev_verdict(handler, code, out)
    refs = ["%s exit %d" % (row["tool"], code)]
    if isinstance(detail, dict) and detail.get("attempt_id"):
        refs.append("second opinion attempt %s" % detail["attempt_id"])
    return {"verdict": verdict, "output": words, "evidence_refs": refs, "detail": detail}


def run_jev_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's calibrated route (OUT1.h): the same adapter, plus the authority truth. Every reply says nothing was
    acted on and that no opinion is release evidence. A review or deliver reply carries `check_verdict`, copied from
    what the person typed and from nothing else, and `second_opinion`, the separate result."""
    seen = {}
    base = invoke or jev_invoke

    def spy(handler, inputs):
        seen["reply"] = base(handler, inputs)
        return seen["reply"]

    got = _run_capability(JEV_CAPABILITIES, spy, capability, outcome, typed, allow)
    got["acted"] = False
    got["release_evidence"] = False
    got["limit"] = JEV_LIMIT
    detail = seen.get("reply", {}).get("detail") if isinstance(seen.get("reply"), dict) else None
    if capability == "calibrated.status":
        got["state_detail"] = detail
    elif capability in ("calibrated.review", "calibrated.deliver"):
        check = jev_check_verdict((typed or {}).get("check_verdict"))
        got["check_verdict"] = check
        got["second_opinion"] = detail
        reply = seen.get("reply")
        if isinstance(reply, dict) and reply.get("evidence_refs"):
            # ADM1.b: once the tool ran and answered, the row's verdict, state and exit code are the CHECK's, never
            # the opinion's. Whether an opinion was obtained lives only under second_opinion. A tool that never ran
            # (blocked, refused, a value not valid, no tool, no start, a timeout) or a handler that raised keeps the
            # adapter's own answer, which is never a pass.
            got["second_opinion"] = dict(detail or {}, answered=got["verdict"] == "PASS", line=got["output"])
            check = check or NODATA
            got["verdict"] = check
            got["state"] = {"PASS": "COMPLETE", "FAIL": "FAILED"}.get(check, NODATA)
            got["evidence_refs"] = ["check verdict %s, as the person typed it" % check] + got["evidence_refs"]
            got["output"] = ("The check's own verdict stands: %s. Second opinion, recorded and never the verdict: %s"
                             % (check, got["output"]))
    return got


#: THE LEARNING CAPABILITIES (OUT1.i): what the door can ask the EXISTING lesson store and decision journal to do,
#: beside the Vault, data, mobile, assurance, host and loop rows and run by the same adapter. Each handler is one
#: function of scripts/dream_bridge.py (its DOOR_HANDLERS table names the inputs each one cannot start without), run
#: as `dream_bridge.py door HANDLER` with the typed inputs as JSON on stdin; bm_learn.py, bm_recurrence.py,
#: dream_record.py, dream_report.py and dream_replay.py keep every rule, and how a lesson stands is decided by
#: bm_learning.py. Read rows record nothing as applied and ask no model (reuse runs bm_learn lookup, which the tool itself declares may migrate a store one schema version behind before it reads). capture writes a CANDIDATE only: approving, promoting,
#: superseding and changing a judge or a policy stay with the owner's own question and receipt, and no row here
#: reaches them. forget needs approve, and the owner's own words as `answer`.
LEARNING_TIMEOUT_S = 300
LEARNING_LIMIT = ("This reads and writes the existing lesson store and decision journal only: nothing new was "
                  "learned, ranked or promoted, and no model was asked. A proposal is not a rule until the owner "
                  "approves it, and an alternative that was never chosen has no recorded outcome.")


def _learning(cap, authority="read", need=(), outcome=None):
    return {"id": "learning." + cap, "handler": "learning_existing_" + cap, "prerequisites": list(need),
            "required_authority": [authority], "outcome_input": outcome}


LEARNING_CAPABILITIES = (
    _learning("report"),
    _learning("reuse", "read", ("task",), "task"),
    _learning("capture", "write", ("trigger", "action"), "action"),
    _learning("recurrence"),
    _learning("forget", "approve", ("rule", "answer")),
    _learning("decisions", "read", ("run_dir",)),
    _learning("replay", "read", ("run_dir", "alternative")),
    _learning("decision", "write", ("run_dir", "kind", "options", "chosen", "policy_version")),
    _learning("outcome", "write", ("run_dir", "decision", "status", "grader")),
)


def learning_tool():
    """dream_bridge.py beside this door: scripts/ in a checkout, the runtime folder in an install. None when absent."""
    path = os.path.join(HERE, "dream_bridge.py")
    return path if os.path.isfile(path) else None


def learning_invoke(handler, inputs, tool=None, timeout=LEARNING_TIMEOUT_S):
    """invoke() for the learning rows: `dream_bridge.py door HANDLER` through _tool_invoke."""
    return _tool_invoke(tool or learning_tool(), handler, inputs, "the learning tools",
                        "the learning tools are not installed beside the door", timeout)


def run_learning_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's learning route (OUT1.i): the same adapter, plus what this access is not."""
    got = _run_capability(LEARNING_CAPABILITIES, invoke or learning_invoke, capability, outcome, typed, allow)
    got["limit"] = LEARNING_LIMIT
    return got


#: THE AUTHORED CONTENT CAPABILITIES (OUT1.m): what the door can ask the EXISTING authored-content pipeline
#: (plugin/runtime/brother/rtm/voice_pipeline.py, shipped flat beside an installed door by bundle_runtime.py) to do,
#: run by the same adapter as every other row. The pipeline takes an archive folder the person supplies (ids.txt,
#: slugs.txt or the platform's own Shares.csv), deduplicates the post ids (one id is one id however many sources name
#: it), rebuilds dataset.jsonl and a counts report, REPORT.md, inside that folder, and prints one summary line. It is
#: NOT speech recognition and NOT a writing engine, and it publishes nothing: its only network path is its own fetch of
#: the author's public pages, under its own limits (at most 60 a run, 3 seconds apart, never the same page twice), and
#: the door adds none. The archive folder is the person's own, outside every repository (the pipeline refuses one
#: inside a repository), and no post text ever enters a reply here: the reply carries counts and the two file paths.
#:   content.analyze needs write (it rebuilds the dataset and the report inside the archive folder; no network).
#:   content.collect needs approve (the same, after fetching the public pages not yet on disk).
#: A completed run is read back from the dataset itself: the summary line and dataset.jsonl must agree, and an archive
#: whose pages hold no own post with text is NO-DATA, never a PASS with zero counts.
CONTENT_TIMEOUT_S = 600
CONTENT_COLLECT_TIMEOUT_S = 3600  # the pipeline's own ceiling: 60 fetches, 40 seconds each at worst, 3 seconds apart
CONTENT_TOOL = "voice_pipeline.py"
CONTENT_SOURCE = "plugin/runtime/brother/rtm/" + CONTENT_TOOL
CONTENT_SUMMARY = re.compile(r"ids (\d+) \| OK (\d+) \| NO-DATA (\d+) \| fetched (\d+) \| waiting (\d+)")
CONTENT_LIMIT = ("Counts over the author's own public posts, from the archive supplied: not speech recognition, not a "
                 "writing engine, and nothing was published anywhere.")
CONTENT_CAPABILITIES = (
    {"id": "content.analyze", "handler": "content_existing_report", "prerequisites": ["archive", "handle"],
     "required_authority": ["write"], "outcome_input": None},
    {"id": "content.collect", "handler": "content_existing_collect", "prerequisites": ["archive", "handle"],
     "required_authority": ["approve"], "outcome_input": None},
)


def content_tool(here=HERE):
    """The authored-content pipeline beside this door: flat in an installed runtime (bundle_runtime.py ships it
    there), else the checkout's plugin tree above this scripts/ folder. None when neither exists."""
    for path in (os.path.join(here, CONTENT_TOOL), os.path.join(os.path.dirname(here), *CONTENT_SOURCE.split("/"))):
        if os.path.isfile(path):
            return path
    return None


def _content_argv(handler, inputs, tool):
    """(argv, timeout, problem) for one content handler. The archive must be an existing folder named by a plain
    word, the handle a plain word (the pipeline's own regex refuses anything but a profile handle); a value that is
    not is a problem, so nothing starts on a guess. Only the collect handler fetches."""
    archive, handle = inputs.get("archive"), inputs.get("handle")
    if _loop_word(archive) is None or not os.path.isdir(archive):
        return None, 0, "archive %r is not an existing folder" % (archive,)
    if _loop_word(handle) is None:
        return None, 0, "handle %r is not a plain profile handle" % (handle,)
    argv = [sys.executable, "-B", tool, "--dir", os.path.abspath(archive), "--handle", handle]
    if handler == "content_existing_report":
        return argv + ["--no-fetch"], CONTENT_TIMEOUT_S, None
    if handler == "content_existing_collect":
        return argv, CONTENT_COLLECT_TIMEOUT_S, None
    return None, 0, "no content handler is named %s" % handler


def _content_dataset(archive):
    """((ids, parsed, own), None) counted from the dataset the pipeline wrote, or (None, why) when it cannot be read
    back: a missing or malformed dataset contradicts the summary line, it is never a count."""
    path = os.path.join(archive, "dataset.jsonl")
    ids = parsed = own = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("activity"), str):
                    raise ValueError("a row is not a post record")
                ids += 1
                if row.get("status") == "OK":
                    parsed += 1
                    if row.get("own_post") is True and str(row.get("text") or "").strip():
                        own += 1
    except (OSError, ValueError, UnicodeError) as exc:
        return None, "%s cannot be read back: %s" % (path, exc)
    return (ids, parsed, own), None


def _content_verdict(code, out, archive):
    """(verdict, words, evidence) from the pipeline's own exit code, its summary line and the dataset it wrote. Exit 2
    is its refusal (nothing written), exit 1 a source it could not read (counts incomplete): both NO-DATA. Exit 0 is a
    PASS only when the summary line is there, dataset.jsonl agrees with it, and at least one own post with text was
    parsed; a dataset that disagrees is a FAIL, an archive with nothing readable is NO-DATA with its counts named."""
    text = str(out).strip()
    last = text.split("\n")[-1] if text else ""
    if code == 2:
        return NODATA, (last or "the pipeline refused without saying why") + " (nothing was started)", []
    if code == 1:
        return NODATA, "a source could not be read, so the counts are not complete: " + (last or text[-600:]), []
    if code != 0:
        return "FAIL", "the pipeline ended with exit %s: %s" % (code, text[-600:]), []
    found = CONTENT_SUMMARY.match(last)
    if not found:
        return NODATA, "the pipeline exited 0 without its summary line, so no count is claimed: " + text[-600:], []
    ids, parsed, nodata, fetched, waiting = (int(n) for n in found.groups())
    counts, why = _content_dataset(archive)
    if counts is None:
        return "FAIL", why, []
    if counts[:2] != (ids, parsed):
        return "FAIL", ("the summary line says %d ids and %d parsed pages but dataset.jsonl holds %d and %d, so the "
                        "counts are not trusted" % (ids, parsed, counts[0], counts[1])), []
    evidence = [os.path.join(archive, "dataset.jsonl"), os.path.join(archive, "REPORT.md")]
    own = counts[2]
    words = ("%d post id(s) known, %d page(s) parsed, %d NO-DATA page(s), %d fetched this run, %d waiting; %d own "
             "post(s) with text and metrics. Report: %s." % (ids, parsed, nodata, fetched, waiting, own, evidence[1]))
    if own == 0:
        return NODATA, words + (" Nothing can be said about what performed well: no own post with text was parsed"
                                " (a page not yet fetched, or not the author's own)."), evidence
    return "PASS", words, evidence


def content_invoke(handler, inputs, run=None, here=HERE):
    """invoke() for the content rows: run the existing pipeline on the person's archive and read its own exit code,
    summary line and dataset. A missing tool, a value that is not valid, a tool that cannot start and an unreadable
    answer are NO-DATA naming the cause, never a pass."""
    if not any(r["handler"] == handler for r in CONTENT_CAPABILITIES):
        return {"verdict": NODATA, "output": "no content handler is named %s" % handler, "evidence_refs": []}
    tool = content_tool(here)
    if tool is None:
        return {"verdict": NODATA, "evidence_refs": [],
                "output": "this install carries no %s, so nothing was started" % CONTENT_TOOL}
    inputs = inputs if isinstance(inputs, dict) else {}
    argv, timeout, problem = _content_argv(handler, inputs, tool)
    if problem:
        return {"verdict": NODATA, "output": problem + "; nothing was run", "evidence_refs": []}
    code, out = (run or _loop_run)(argv, timeout)
    if code is None:
        return {"verdict": NODATA, "output": "%s %s" % (CONTENT_TOOL, out), "evidence_refs": []}
    verdict, words, evidence = _content_verdict(code, out, os.path.abspath(inputs["archive"]))
    return {"verdict": verdict, "output": words, "evidence_refs": evidence + ["%s exit %d" % (tool, code)]}


def run_content_capability(capability, outcome=None, typed=None, allow=None, invoke=None):
    """The door's authored-content route (OUT1.m): the same adapter, plus the limit every reply carries."""
    got = _run_capability(CONTENT_CAPABILITIES, invoke or content_invoke, capability, outcome, typed, allow)
    if any(c["id"] == capability for c in CONTENT_CAPABILITIES):
        got["limit"] = CONTENT_LIMIT
    return got


def _typed_inputs(pairs, json_pairs=None):
    """--input key=value pairs as a dict; a key given twice becomes a list (paths). --input-json key=JSON pairs keep
    their types (a number, a boolean, a list, a nested claim), so nothing typed arrives as text."""
    typed = {}
    for pair in json_pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError("--input-json takes key=JSON, got %r" % pair)
        try:
            typed[key] = json.loads(value)
        except ValueError:
            raise ValueError("--input-json %s is not JSON: %r" % (key, value))
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError("--input takes key=value, got %r" % pair)
        if key in typed:
            typed[key] = (typed[key] if isinstance(typed[key], list) else [typed[key]]) + [value]
        else:
            typed[key] = [value] if key == "paths" else value
    return typed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("outcome", nargs="?", help="what should be true when this is done")
    ap.add_argument("--store", default=WR.STORE)
    ap.add_argument("--model-cmd", help="decomposer command line (shlex syntax)")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--route", metavar="GOAL",
                    help="print the supported route and one recommended action for a plain goal, then exit "
                         "(0 delivered, 2 NO-DATA when no supported route exists)")
    ap.add_argument("--capability", metavar="ID",
                    help="run one existing Vault capability (vault.recall, vault.capture, ...), BrotherDS and "
                         "MDM capability (data.check, data.mdm_review, ...), mobile capability (mobile.journey, "
                         "mobile.run, ...) or assurance capability (assurance.gate, assurance.status, "
                         "assurance.handover_prepare, ...; assurance.discover lists them) or host capability "
                         "(host.identity, host.update, host.diagnose, host.verify, host.repair) or loop capability (loop.status, "
                         "loop.handoff, loop.start, loop.pause, loop.resume, loop.stop, loop.close) or second opinion capability "
                         "(calibrated.status, calibrated.review, calibrated.deliver) or learning capability "
                         "(learning.report, learning.reuse, learning.capture, learning.forget, learning.replay, ...) or authored-content "
                         "capability (content.analyze counts the author's own public posts from a supplied archive "
                         "folder outside every repository, content.collect first fetches the public pages not yet "
                         "there; neither is speech recognition) with the outcome as its "
                         "text, print its result and evidence, then exit "
                         "(0 complete, 1 failed or refused, 2 NO-DATA or blocked)")
    ap.add_argument("--available", action="store_true",
                    help="print the capabilities this install admits to a consumer, as JSON, then exit")
    ap.add_argument("--input", action="append", metavar="KEY=VALUE", help="typed evidence for --capability")
    ap.add_argument("--input-json", action="append", metavar="KEY=JSON",
                    help="evidence for --capability that keeps its type: a number, a boolean, a list, a claim")
    ap.add_argument("--allow", action="append", choices=("write", "approve", "device"),
                    help="grant --capability more than read: write saves a candidate, a claim or a file, approve "
                         "records a promotion, device lets a mobile capability act on a physical device (write "
                         "never implies it)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.available:
        view = consumer_admission()
        print(json.dumps({"candidate": view["candidate"], "host": view["host"],
                          "capabilities": sorted(view["capabilities"]), "routes": sorted(view["routes"])},
                         indent=2, sort_keys=True))
        return 0
    if args.capability is not None:
        if not capability_admitted(args.capability, consumer_admission()):
            print(json.dumps(hidden_capability_reply(args.capability), indent=2, sort_keys=True))
            return 2
        try:
            typed = _typed_inputs(args.input, args.input_json)
        except ValueError as exc:
            ap.error(str(exc))
        runner = (run_data_capability if args.capability.startswith("data.")
                  else run_mobile_capability if args.capability.startswith("mobile.")
                  else run_assurance_capability if args.capability.startswith("assurance.")
                  else run_host_capability if args.capability.startswith("host.")
                  else run_loop_capability if args.capability.startswith("loop.")
                  else run_jev_capability if args.capability.startswith("calibrated.")
                  else run_learning_capability if args.capability.startswith("learning.")
                  else run_content_capability if args.capability.startswith("content.")
                  else run_vault_capability)
        got = runner(args.capability, args.outcome, typed, args.allow)
        print(json.dumps(got, indent=2, sort_keys=True))
        return 0 if got["state"] == "COMPLETE" else 1 if got["state"] in ("FAILED", "REFUSED", "UNPROVEN") else 2
    if args.route is not None:
        got = recommend(args.route, admission=consumer_admission())
        if got["delivered"]:
            print("%s (route: %s)" % (got["action"], got["route"]))
            return 0
        print("%s: %s (route: %s)" % (NODATA, got["action"], got["route"] or "none"))
        return 2
    if args.outcome is None:
        ap.error("the following arguments are required: outcome")

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

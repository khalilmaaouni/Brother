#!/usr/bin/env python3
"""Pick the best AVAILABLE model for a task, unless told which one to use.

Owner order 2026-09-21: brother.loop must run with any type of model available and select the best
one available unless given instructions to use specific ones, including Claude's own models from
Fable to Haiku alongside the bridge models.

Before this file, scripts/loop/unit_runner.py line 90 read:

    jobs = [{"id": ..., "model": "deepseek", ...}]

One hardcoded id, no selection, no fallback, and no Claude native path anywhere in the loop. So
"best available" meant "the one somebody typed in", and a model going down stopped the loop.

THE CENTRAL FINDING, measured rather than argued, 2026-09-21. These models are different KINDS,
not a quality ladder. A live probe of each: deepseek answered a sum in 21 completion tokens, muse
answered the SAME sum in 310, and jev cannot answer it at all in prose but returns a calibrated
probability (0.96) to a typed question. Jev cannot write code. Deepseek cannot return a calibrated
probability in a typed schema. So a single global ranking of "best model" is meaningless, and the
router matches on TASK KIND first and ranks only within it.

THE FOUR RULES, in the order they are applied. The order is the design.

  1. PRIVACY IS AN ELIGIBILITY GATE, NEVER A TRADE-OFF. The bridge vendors state or imply retention
     by a third party, so sending content there is a form of PUBLISHING it. Candidates that may not
     receive this content class are removed BEFORE anything is scored, and the check runs AGAIN
     immediately before an adapter transmits, because a routing bug, stale metadata or a future
     adapter added by someone else must not be able to reach the wire without passing it twice.

  2. UNKNOWN SENSITIVITY FAILS CLOSED. An unlabelled task may reach a local model or nothing. Never
     "try the cheap external one and see". This is the fail direction the whole estate uses: an
     unknown is never the safe case.

  3. AN OVERRIDE STILL PASSES BOTH GATES. Naming a model explicitly chooses among the ELIGIBLE, it
     does not authorise publishing private content or sending prose to a typed endpoint. An
     override that fails a gate is refused with the reason, never silently re-routed, because
     silently doing something else is how an operator stops trusting the tool.

  4. FAIL OVER ON INFRASTRUCTURE, NEVER ON AN ANSWER. A timeout, an empty body, malformed output or
     an unavailable provider moves to the next candidate. A model's valid NEGATIVE result, a grade
     of FAIL, a probability of 0.02, or a reasoned refusal to do the work, is an OUTCOME and must
     be returned. Spraying a task across providers until one says yes is how a pipeline manufactures
     false greens.

usage:
  model_router.py --kind build --sensitivity public          print the chain that would be used
  model_router.py --available                                probe what is reachable right now
  model_router.py --selftest
"""
import json, os, re, shutil, subprocess, sys, tempfile, time

# ---------------------------------------------------------------- the registry
# Each model advertises a machine readable contract. The router matches a task envelope against
# it; there is no hand maintained list of exceptions anywhere else in the file.
#
#   kinds        what this model can actually DO. Membership is a hard gate, not a preference.
#   privacy      the most sensitive content class it may receive.
#   quality      per kind, 0 to 10. Absent kind means it cannot do that kind at all.
#   cost         relative units per call, from the measured completion token probe.
#   transport    which adapter carries it.
PUBLIC, INTERNAL, PRIVATE = "public", "internal", "private"
ORDER = {PUBLIC: 0, INTERNAL: 1, PRIVATE: 2}
#: Transports that leave this machine for a vendor who may retain the prompt. A model on one of
#: these may carry PUBLIC content and nothing else, enforced in load_registry, so no registry file
#: and no caller supplied dict can declare its way past it.
THIRD_PARTY = ("bridge", "codex")       # a model accepts its own class and everything below
#: Every transport this router knows how to carry. A transport outside this tuple is REFUSED rather
#: than assumed local: THIRD_PARTY is an allowlist of the vendors we know retain prompts, so an
#: unclassified transport would otherwise clear the rule below purely by being unrecognised.
KNOWN_TRANSPORTS = ("bridge", "codex", "claude")
#: BY CHOICE ONLY (owner, 2026-09-22). His first words were "Remove codex all together from the loop", and then, before
#: anything was committed: "Codex can be chosen as worker still if the user wants it or as checker or as orchestrator".
#: So codex stays a known, working transport and leaves every AUTOMATIC decision: a model on one of these transports
#: is never ranked into a default chain and never reached as a silent failover. It is used only when somebody NAMES it,
#: as a pin (BROTHER_PIN_MODEL), as the checker (BROTHER_CHECKER) or by calling it directly. Decided in chain(), the
#: one place every automatic choice is made.
BY_CHOICE_ONLY = ("codex",)

def _paths():
    """scripts/brother_paths.py through the code root: the frozen candidate in a proof, the checkout otherwise, and
    refused unless the module imported is that file. A file path computed here from this file's own location was
    unresolvable to the freeze and, in the deployed flat bin, pointed at ~/.claude, which holds no brother_paths.py.
    scripts/ is on sys.path only for this one import: four module names exist in both scripts/ and scripts/loop/, and
    the loop's own copies must keep winning."""
    scripts = os.path.join(code_root(), "scripts")
    wanted = os.path.realpath(os.path.join(scripts, "brother_paths.py"))
    module = sys.modules.get("brother_paths")
    if module is None:
        sys.path.insert(0, scripts)
        try:
            import brother_paths as module
        finally:
            sys.path.remove(scripts)
    if os.path.realpath(getattr(module, "__file__", None) or "") != wanted:
        raise Refused("brother_paths was imported from %s, not the code root's %s"
                      % (getattr(module, "__file__", None), wanted))
    return module


def codex_bin():
    """The Codex binary, from scripts/brother_paths.py, the one resolver (ACC5: the app moved the binary and every
    hard-coded copy went stale at once): the owner pin, else the program the intake proved (the program record), else
    the app bundled location."""
    return _paths().codex_bin()


def repo_root():
    """A directory that is THIS checkout, resolved by asking git rather than counting parents.

    COUNTING PARENTS FROM __file__ IS WRONG IN EVERY MIRRORED TOOL, and it bit this estate twice in
    one evening in two different files. A loop tool exists as a reviewed copy in scripts/loop and an
    executed copy in ~/.claude/bin, and `dirname(dirname(dirname(__file__)))` resolves to the
    repository root from the first and to the HOME directory from the second. The registry could
    not be found, and Codex refused with "Not inside a trusted directory", both from the same
    arithmetic.

    Asking git is correct wherever the loop actually runs, which is inside its worktree. An explicit
    environment variable wins so an operator can override, and the parent count is the LAST resort
    rather than the first."""
    env = os.environ.get("BROTHER_CODEX_ROOT") or os.environ.get("BROTHER_REPO_ROOT")
    if env and os.path.isdir(env):
        return env
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip() and os.path.isdir(r.stdout.strip()):
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_VERSIONS = {}   # {(realpath, fingerprint): version or None}, per process: a version query runs once per program


def _version(path):
    """The program's own `--version`, semantic, or None when it cannot say (NO-DATA, never a guess). Five seconds."""
    try:
        st = os.stat(path)
        key = (os.path.realpath(path), st.st_size, st.st_mtime_ns)
    except OSError:   # sbe: allow-silent None is this function's documented NO-DATA answer; callers treat it as no proven version
        return None
    if key not in _VERSIONS:
        try:
            r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
            m = re.search(r"(\d+(?:\.\d+)+)", (r.stdout or "") + " " + (r.stderr or "")) if r.returncode == 0 else None
            _VERSIONS[key] = m.group(1) if m else None
        except (OSError, subprocess.SubprocessError, ValueError):
            _VERSIONS[key] = None
    return _VERSIONS[key]


def newest(candidates):
    """The candidate whose own version is highest, compared as numbers (2.1.284 above 2.1.99), or None when none can
    say its version. The directory name and the mtime are never read: a version is what the program says it is."""
    known = [(tuple(int(x) for x in v.split(".")), p) for p, v in ((p, _version(p)) for p in candidates) if v]
    return max(known)[1] if known else None


def claude_bin():
    """The Claude Code CLI a headless Claude call runs, resolved at call time (2026-09-30, after a run pinned to
    ~/.local/bin/claude 2.1.251 answered every call unrecognized_model while the desktop's 2.1.284 knew the model):
      1. BROTHER_CLAUDE_BIN, the owner's pin (an absolute path, else refused). Never overridden: a pin the intake cannot
         prove is NO START, never a silent switch to another program.
      2. the program the intake PROVED answers the run's models (brother_paths.recorded_program: the program record,
         while that file still has the fingerprint it was proved with).
      3. the NEWEST installed candidate by its own version (brother_paths.claude_candidates: the desktop app's versioned
         copies, PATH and the package directories). ~/.local/bin is one candidate among them, never first by place.
      4. else the first executable candidate (it cannot say its version; the intake's proof refuses it), else
         ~/.local/bin/claude, which is missing, and model_call records the call as not_started at a known zero."""
    named = os.environ.get("BROTHER_CLAUDE_BIN")
    if named:
        if not os.path.isabs(named):
            raise Refused("BROTHER_CLAUDE_BIN is not an absolute path: %r" % named)
        return named
    local = os.path.expanduser("~/.local/bin/claude")
    try:
        paths = _paths()
        cands = paths.claude_candidates()
        proven = paths.recorded_program("claude")
    except (Refused, ImportError, OSError):
        # brother_paths unreachable (no code root): the same places, scanned here, with no record to read
        root = os.path.expanduser("~/Library/Application Support/Claude/claude-code")
        bundles = []
        try:
            for v in sorted(os.listdir(root)):   # both desktop layouts, as brother_paths.desktop_bundles reads them
                bundles.append(os.path.join(root, v, "claude.app", "Contents", "MacOS", "claude"))
                try:
                    bundles += [os.path.join(root, v, sub, "claude.app", "Contents", "MacOS", "claude")
                                for sub in sorted(os.listdir(os.path.join(root, v))) if sub != "claude.app"]
                except OSError:
                    pass  # sbe: allow-silent a version folder that cannot be listed adds no candidate; the others stand
        except OSError:
            pass  # sbe: allow-silent no desktop install: the candidates are the local CLI alone, and the intake proves it
        cands = [p for p in [local] + bundles if os.path.isfile(p) and os.access(p, os.X_OK)]
        proven = None
    if proven:
        return proven
    return newest(cands) or (cands[0] if cands else local)


def code_root():
    """The tree loop CODE runs from, kept apart from the tree it lands into (B5-08, U3 item 3).

    repo_root() is the checkout the cwd sits in, which is the launch worktree the loop commits
    into, so code run from it is not the frozen candidate. BROTHER_CODE_ROOT names the frozen one.
    FAIL DIRECTION: a value that is set and is not an absolute directory is corrupt and refuses in
    every phase; in a proof phase an unset value refuses too, never a quiet fall back to the
    landing tree. Only outside a proof, with nothing set, is the checkout the code root."""
    env = os.environ.get("BROTHER_CODE_ROOT")
    if env:
        if not (os.path.isabs(env) and os.path.isdir(env)):
            raise Refused("BROTHER_CODE_ROOT is not an absolute directory: %r" % env)
        return env
    if os.environ.get("BROTHER_PROOF_PHASE"):
        raise Refused("a proof phase runs frozen code only: BROTHER_CODE_ROOT is not set")
    return repo_root()


def _registry_candidates():
    """Where the registry might be, in order of authority.

    THE TWO COPY PROBLEM, hitting this file directly. The loop executes ~/.claude/bin/model_router.py
    while the reviewed copy lives in scripts/loop/. A path computed relative to __file__ resolves to
    the repository root from one copy and to the HOME directory from the other, so the installed
    twin looked for ~/docs/plan/model-registry.json and refused. The refusal was correct and is why
    this was found in a probe rather than in production, but a control that cannot find its own data
    on the machine that runs it is not deployed.

    An explicit environment variable wins, so an operator can point at a registry without moving
    files."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    env = os.environ.get("BROTHER_MODEL_REGISTRY")
    if env:
        out.append(env)
    # scripts/loop/model_router.py -> repository root
    out.append(os.path.join(os.path.dirname(os.path.dirname(here)), "docs", "plan", "model-registry.json"))
    # the installed twin keeps its copy beside the other loop state
    out.append(os.path.expanduser("~/.claude/model-registry.json"))
    # A TRACKED FIXTURE, LAST, so a real registry always wins and this only answers on a tree that
    # ships none. Measured 2026-09-22: on the export tree with an empty HOME this module's selftest
    # refused with "no model registry found", which made two separate detectors red as well, since
    # both report a module that is not green as shipped. The fixture travels with the export.
    # It is a byte copy of docs/plan/model-registry.json, and the selftest refuses a copy that fell behind (2026-10-06).
    # FAIL DIRECTION unchanged: if NO candidate is readable the refusal still stands.
    out.append(os.path.join(os.path.dirname(here), "fixtures", "model-registry-fixture.json"))
    return out


REGISTRY_PATH = _registry_candidates()[-1]


def _validate_models(models, where):
    """THE ONE GATE EVERY REGISTRY CROSSES, whatever door it came in by.

    MEASURED 2026-09-22, reproduced before this was written. The transport/privacy rule below used
    to live inside load_registry(), so it bound a registry READ FROM DISK and nothing else. Every
    gate in this file reads its models through registry(reg), and registry(reg) handed a caller
    supplied dict straight back unexamined, so a caller passing its own dict skipped the rule:

        poison = {"external": {"id": "vendor/leaky", "transport": "bridge",
                               "privacy": PRIVATE, "quality": {"build": 9}, "cost": 1}}
        chain("build", PRIVATE, registry_arg=poison)            -> ['external']
        assert_may_send("external", PRIVATE, "build", poison)   -> True

    Both gates cleared private content onto a third party transport, because both gates read the
    same poisoned label. A restriction any caller can step around by passing its own dict is a
    convention, not a control, so the check lives HERE, at the single place the file reader and the
    caller supplied path both cross, rather than at the two call sites that were reported.

    Validation happens in place and `kinds` is derived from `quality`, so this is idempotent:
    re-validating an already valid registry is the normal case and stays silent."""
    # FAIL DIRECTION: anything that is not a non empty mapping REFUSES. An empty registry, or one
    # shaped as a list, must never read as "nothing is forbidden"; it reads as "nothing is known".
    if not isinstance(models, dict) or not models:
        raise Refused("%s names no models" % where)
    by_id = {}
    for name, m in models.items():
        # FAIL DIRECTION: a row that is not a mapping REFUSES. Without this, `"id" in m` on a
        # string row runs a SUBSTRING test, and the outcome is accidental rather than decided.
        if not isinstance(m, dict):
            raise Refused("model %r in %s is not a mapping" % (name, where))
        for field in ("id", "transport", "privacy", "quality", "cost"):
            if field not in m:
                raise Refused("model %r is missing %r in %s" % (name, field, where))
        # FAIL DIRECTION: an unlabelled or unrecognised privacy class REFUSES, and is never read as
        # public. A caller that could not say what class its content is, is exactly the caller
        # whose content must not leave this machine.
        if m["privacy"] not in ORDER:
            raise Refused("model %r declares an unknown privacy class %r" % (name, m["privacy"]))
        # FAIL DIRECTION: an unrecognised transport REFUSES rather than being assumed local. The
        # rule immediately below tests membership of THIRD_PARTY, which is an allowlist of the
        # vendors known to retain prompts, so an unclassified transport would otherwise be cleared
        # for private content purely because nobody has classified it yet.
        if m["transport"] not in KNOWN_TRANSPORTS:
            raise Refused("model %r declares an unknown transport %r; this router carries %s"
                          % (name, m["transport"], ", ".join(KNOWN_TRANSPORTS)))
        # TRANSPORT BINDS PRIVACY, and no data may talk its way out of it.
        #
        # Until 2026-09-21 privacy was purely a label in this data, and nothing stopped a bridge or
        # codex entry from declaring itself "private". An adversarial review used that twice: once
        # through BROTHER_MODEL_REGISTRY pointing at a file it wrote, and once by handing a
        # caller-built registry straight to chain().
        #
        # A third party that may retain a prompt can never be a lawful home for private content,
        # whatever a file or a caller says. That is a property of the vendor, not of the row, so it
        # is enforced in code where no data can override it.
        if m["transport"] in THIRD_PARTY and m["privacy"] != PUBLIC:
            raise Refused(
                "model %r uses the %s transport, which is a third party that may retain what it is "
                "sent, so it may only ever be declared privacy %r; %s says %r"
                % (name, m["transport"], PUBLIC, where, m["privacy"]))
        if not isinstance(m["quality"], dict) or not m["quality"]:
            raise Refused("model %r declares no quality for any kind, so it can do nothing" % name)
        # FAIL DIRECTION: one vendor id offered under two transports or two privacy classes
        # REFUSES. Selection picks by NAME while the reliability ledger keys by `id`, so a second
        # row naming the same endpoint at a higher class is a laundering route to it: pick the
        # permissive name, reach the same vendor. Two rows sharing an id must agree on both.
        prior = by_id.get(m["id"])
        if prior is not None and (prior[1], prior[2]) != (m["transport"], m["privacy"]):
            raise Refused(
                "%s offers id %r twice with different terms: %r is %s/%s but %r is %s/%s"
                % (where, m["id"], prior[0], prior[1], prior[2], name, m["transport"], m["privacy"]))
        by_id[m["id"]] = (name, m["transport"], m["privacy"])
        # KINDS ARE DERIVED FROM QUALITY, never declared twice. Two fields that must agree are two
        # fields that will one day disagree, and the failure is a model silently dropped from or
        # wrongly admitted to a chain. Deriving here also overwrites a caller supplied `kinds` that
        # disagrees with its own quality map.
        m["kinds"] = set(m["quality"])
    return models


def load_registry(path=None):
    """THE REGISTRY IS DATA, NOT CODE. Owner order 2026-09-21: Codex models, and "others in the
    future if I add them via OpenRouter". A Python dict would mean every new model is a code change,
    a review and a deploy, which is how a registry goes stale and the loop quietly keeps using last
    month's list. Adding a row to docs/plan/model-registry.json is the whole procedure.

    A malformed entry REFUSES rather than being skipped. Skipping a bad row is how a model silently
    disappears from the chain and nobody learns why the loop got slower."""
    return load_registry_document(path)["models"]


def load_registry_document(path=None):
    """The whole registry document (its `models` validated in place), for readers that need the fields beside the rows:
    `read_on` for the freshness check (FX-31.6). load_registry is this document's `models` and nothing else changed.
    FAIL DIRECTION: a document that is not a mapping refuses like a document naming no models; both are "nothing known"."""
    tried = [path] if path else _registry_candidates()
    doc, path = None, None
    for cand in tried:
        try:
            with open(cand, encoding="utf-8") as f:
                doc = json.load(f)
            path = cand
            break
        except OSError:  # sbe: allow-silent the next candidate is tried; none readable raises Refused just below
            continue
        except ValueError as exc:
            raise Refused("the model registry %s is not valid JSON (%s)" % (cand, exc))
    if doc is None:
        raise Refused("no model registry found in %s; refusing to route on a guess" % ", ".join(tried))
    if not isinstance(doc, dict):
        raise Refused("the model registry %s is not a mapping, so it names no models" % path)
    _validate_models(doc.get("models"), "the model registry %s" % path)
    return doc


REGISTRY = None            # loaded lazily so an import cannot fail on a bad file
KINDS = ()


def registry(reg=None):
    global REGISTRY, KINDS
    if reg is not None:
        # A CALLER SUPPLIED REGISTRY IS VALIDATED EXACTLY LIKE ONE READ FROM DISK. Until 2026-09-22
        # this line read `return reg`, and that single line WAS the bypass: every gate below reads
        # its models through here, so an unexamined dict disarmed all of them at once.
        return _validate_models(reg, "a caller supplied registry")
    if REGISTRY is None:
        REGISTRY = load_registry()
        KINDS = tuple(sorted({k for m in REGISTRY.values() for k in m["kinds"]}))
    return REGISTRY


# ---------------------------------------------------------------- derived views (FX-31.6)
# EVERY MODEL TABLE IS A PROJECTION OF THE ROWS ABOVE. Until 2026-10-04 the bridge (or_ask.py), the dispatch CLI
# (or_dispatch_cli.py), the worker mix (worker_mix.py), the round pricing (ev_gate.py) and the intake reader
# (loop_intake.py) each typed their own slice of these facts, and one slice had drifted: the dispatch CLI knew jev by
# the id the bridge's [usage] line reports (a dated id) while the bridge alias and this registry carried the undated
# one. Both were right about different things, so the row now says both: `id` is what is requested, `answers_as` is
# what answers. The optional row fields are documented in the registry's own `fields` note.
# FAIL DIRECTION: an optional field that is present and malformed REFUSES the whole projection, naming the row and the
# field; an absent optional field contributes nothing. No name is ever guessed, and the rows cross _validate_models
# first, so a caller supplied dict meets the same gate as the file.
FRESHNESS_DAYS = 90


def _strings(owner, field, where):
    """`field` of `owner`: an optional non empty list of non empty strings, () when absent, Refused otherwise."""
    val = owner.get(field)
    if val is None:
        return ()
    if not isinstance(val, list) or not val or not all(isinstance(x, str) and x.strip() for x in val):
        raise Refused("%s: %r must be a non empty list of non empty strings, got %r" % (where, field, val))
    return tuple(val)


def _usd_text(usd):
    """A USD figure as the text the hand typed tables carried: two decimals when that is the exact figure (0.10, 0.02),
    the shortest exact repr otherwise (0.005), so a derived table is byte for byte the table it replaced."""
    two = "%.2f" % usd
    return two if float(two) == usd else repr(float(usd))


def derive_model_views(models):
    """The legacy tables, each built from the registry rows and nothing else:
      bridge_aliases  {name or alias: id} over bridge rows: what or_ask.py accepts as --model
      dispatch_ids    {name: answers_as or id} over SEATED bridge rows: what the strict dispatcher expects the [usage] line
                      to say, and what the fan out accepts as a job's model, so a shadow or retired row is left out
      spoken          {phrase: name}: the owner's words loop_intake.read_model absorbs
      arm_of          ((substring, arm), ...) in row order: worker_mix.arm_of's ledger matcher
      default_mix     "name:count,..." over rows whose arm carries a mix: worker_mix.DEFAULT_MIX
      arm_cost        "name:usd,..." over rows with an arm: ev_gate.DEFAULT_ARM_COST
      bridge_fallbacks [id, ...] in row order over bridge rows whose `fallback` is true: or_ask.FALLBACK_MODELS, the
                      bridge's own secondaries after the requested model fails (FX-31.7 follow-up)"""
    models = _validate_models(models, "the registry handed to derive_model_views")
    bridge_aliases, dispatch_ids, spoken, arm_of, mix, cost, fallbacks = {}, {}, {}, [], [], [], []
    for name, row in models.items():
        where = "model %r" % name
        aliases = _strings(row, "aliases", where)
        if row["transport"] == "bridge":
            answers = row.get("answers_as", row["id"])
            if not isinstance(answers, str) or not answers.strip():
                raise Refused("%s: answers_as must be a non empty string, got %r" % (where, answers))
            for key in (name,) + aliases:
                if bridge_aliases.get(key, row["id"]) != row["id"]:
                    raise Refused("bridge alias %r names two ids: %r and %r" % (key, bridge_aliases[key], row["id"]))
                bridge_aliases[key] = row["id"]
            if _seated(name, models):   # a shadow or retired row is never a dispatch alias: the fan out seats what this names
                dispatch_ids[name] = answers
        fallback = row.get("fallback")
        if fallback is not None:
            # FAIL DIRECTION: a flag that is not a bool, or one on a row the bridge cannot reach, REFUSES; "yes" is not true.
            if not isinstance(fallback, bool):
                raise Refused("%s: fallback must be true or false, got %r" % (where, fallback))
            if row["transport"] != "bridge":
                raise Refused("%s: fallback names a bridge secondary and this row is on the %s transport" % (where, row["transport"]))
            if fallback:
                fallbacks.append(row["id"])
        if row["transport"] != "bridge" and aliases:
            raise Refused("%s: aliases are bridge CLI names and this row is on the %s transport" % (where, row["transport"]))
        for phrase in _strings(row, "spoken", where):
            if spoken.get(phrase, name) != name:
                raise Refused("spoken phrase %r names two models: %r and %r" % (phrase, spoken[phrase], name))
            spoken[phrase] = name
        arm = row.get("arm")
        if arm is None:
            continue
        if not isinstance(arm, dict) or not {"keys", "cost_usd"} <= set(arm) or set(arm) - {"keys", "cost_usd", "mix"}:
            raise Refused("%s: arm must be a mapping of keys, cost_usd and an optional mix, got %r" % (where, arm))
        usd = arm["cost_usd"]
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not (0 <= usd < float("inf")):
            raise Refused("%s: arm cost_usd must be a finite number of at least zero, got %r" % (where, usd))
        for key in _strings(arm, "keys", where + " arm"):
            arm_of.append((key, name))
        cost.append("%s:%s" % (name, _usd_text(usd)))
        if "mix" in arm:
            share = arm["mix"]
            if isinstance(share, bool) or not isinstance(share, int) or share <= 0:
                raise Refused("%s: arm mix must be a positive whole number of builds, got %r" % (where, share))
            if not _seated(name, models):   # a retired or shadow row never seats, so a mix share on it is a contradiction
                raise Refused("%s: a %s row carries no mix share: drop arm.mix" % (where, row.get("stage")))
            mix.append("%s:%d" % (name, share))
    return {"bridge_aliases": bridge_aliases, "dispatch_ids": dispatch_ids, "spoken": spoken, "bridge_fallbacks": fallbacks,
            "arm_of": tuple(arm_of), "default_mix": ",".join(mix), "arm_cost": ",".join(cost)}


def iso_date(raw):
    """A YYYY-MM-DD string as a date, or None for anything else. The hyphenated form ONLY: date.fromisoformat accepts
    the bare YYYYMMDD form from Python 3.11 and refuses it on 3.9, so the shape is pinned here before the parser sees it,
    and the two interpreters this loop runs on answer the same way."""
    import datetime
    if not isinstance(raw, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:   # sbe: allow-silent a shape that is right but not a day (2026-13-40) is the caller's None, said by name there
        return None


def check_registry_freshness(doc, today=None):
    """(status, text): "OK" when the registry's `read_on` is within FRESHNESS_DAYS of today, else "WARN" with a class 2
    owner question. NEVER BLOCKS and never raises on the field (R-FX-31-5, acceptance c14): a missing, malformed or
    future `read_on` is said plainly and asked about, never read as today and never read as fresh. Routing goes on
    with the rows it has either way; staleness is the owner's call, not a reason to stop the loop."""
    import datetime
    today = today or datetime.date.today()
    question = ("Owner question (class 2): are the model registry rows still right, or should they be re-measured? "
                "Routing continues on the current rows meanwhile.")
    raw = doc.get("read_on") if isinstance(doc, dict) else None
    if raw is None:
        return "WARN", "the model registry carries no read_on date, so nothing says when its rows were last measured. " + question
    read_on = iso_date(raw)
    if read_on is None:
        return "WARN", "the model registry's read_on %r is not a date (YYYY-MM-DD). " % (raw,) + question
    age_days = (today - read_on).days
    if age_days < 0:
        return "WARN", "the model registry's read_on %s is in the future (today is %s). " % (read_on, today) + question
    if age_days > FRESHNESS_DAYS:
        return "WARN", ("the model registry was last measured %d days ago (read_on %s, the bar is %d days). "
                        % (age_days, read_on, FRESHNESS_DAYS) + question)
    return "OK", "the model registry was measured %d days ago (read_on %s, within %d days)" % (age_days, read_on, FRESHNESS_DAYS)


class Refused(Exception):
    """A gate said no. It carries the REASON, because a refusal without one gets worked around."""


# ---------------------------------------------------------------- the gates
def may_receive(name, sensitivity, reg=None):
    """Rule 1 and 2. Can this model lawfully receive content of this class?

    An unrecognised sensitivity is treated as the MOST sensitive, never the least, so a typo in a
    caller's envelope cannot open the bridge."""
    m = registry(reg).get(name)
    if m is None:
        return False
    want = ORDER.get(sensitivity)
    if want is None:
        want = ORDER[PRIVATE]        # unknown fails closed
    return ORDER[m["privacy"]] >= want


def can_do(name, kind, reg=None):
    m = registry(reg).get(name)
    return bool(m) and kind in m["kinds"]


def assert_may_send(name, sensitivity, kind, reg=None):
    """THE SECOND CHECK, called by the adapter immediately before it transmits.

    Deliberately duplicated. The first check happens during selection, and this one happens at the
    wire, so a routing bug, a stale envelope, or an adapter someone adds next month that forgets to
    call the router still cannot put private content on a third party's server."""
    r = registry(reg)
    allowed = transports_allowed()   # raises on a word that is not a transport: nothing is sent
    if allowed is not None and r.get(name, {}).get("transport") not in allowed:
        raise Refused("%s is on the %s transport and this run allows only %s (%s)"
                      % (name, r.get(name, {}).get("transport", "no"), ", ".join(sorted(allowed)), TRANSPORTS_SETTING))
    if not may_receive(name, sensitivity, reg):
        raise Refused("%s may receive %s at most, and this task is %s"
                      % (name, r.get(name, {}).get("privacy", "nothing"), sensitivity))
    if not can_do(name, kind, reg):
        raise Refused("%s does not do %s; it does %s"
                      % (name, kind, ", ".join(sorted(r.get(name, {}).get("kinds", ())) or ["nothing"])))
    return True


# ---------------------------------------------------------------- reliability from the ledger
def reliability(path=None):
    """{model id: success rate} read from the stage ledger.

    MEASURED AND STATED PLAINLY, 2026-09-21: this returns {} on the live estate today, because NO
    outcome row carries a model id. `grep -c '"model":' brother-stages.jsonl` returns 0 across 896
    rows, and the cost ledger returns 0 too. So the reliability term ranks nothing right now.

    That is written here rather than hidden because a ranking signal that silently contributes
    nothing is the hollow green this estate keeps finding: the code reads convincing, the number
    never moves, and nobody notices for weeks. record_outcome() below is the fix, and until
    unit_runner calls it, `--available` and the selftest both report the term as inactive.

    An absent record is NOT a good record: a model nobody has run scores neutral, never best, so a
    new entry cannot leapfrog a proven one on optimism alone."""
    path = path or os.path.expanduser("~/.claude/evidence/brother-stages.jsonl")
    ok, total, skipped = {}, {}, 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                m = r.get("model")
                if not m or r.get("event") != "leave":
                    continue
                total[m] = total.get(m, 0) + 1
                if r.get("ok"):
                    ok[m] = ok.get(m, 0) + 1
    except OSError:
        return {}
    if skipped:
        print("model_router: %d unreadable line(s) in %s skipped; the rates below are computed without them" % (skipped, path), file=sys.stderr)
    return {m: ok.get(m, 0) / n for m, n in total.items() if n >= 5}


def record_outcome(model_id, kind, ok, seconds=None, path=None):
    """Write the MODEL onto a durable outcome row, which nothing does today.

    Without this the router cannot learn, because neither ledger records which model produced a
    result: the stage ledger has no model key on any of its rows, and the cost ledger records an
    amount with no model beside it, so cost per model is unknowable. Appending here is the smallest
    change that closes the loop, and it uses the ledger that already exists rather than starting a
    new telemetry system."""
    path = path or os.path.expanduser("~/.claude/evidence/brother-stages.jsonl")
    row = {"at": time.time(), "stage": "model", "event": "leave", "model": model_id,
           "kind": kind, "ok": bool(ok), "seconds": seconds, "pid": os.getpid()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return True
    except OSError:
        return False      # a ledger that cannot be written must not take the call down with it


# ---------------------------------------------------------------- selection
def _seated(name, reg):
    """FX-11.4 (REQ-FX11-14): only a row whose stage is seated may be chosen, automatic or pinned.

    docs/plan/model-registry.json carries no stage field at this commit (FX-31, MDL-03, adds it with the rule
    "absent reads seated"), so an absent stage reads seated. retired and shadow refuse; any other value is an
    unknown and BLOCKS, never the safe case. The same rule is enforced at intake by loop_roles.stage_ok, so a
    bypassed intake still cannot seat a retired or shadow row."""
    row = reg[name]
    stage = row.get("stage", "seated") if isinstance(row, dict) else "seated"
    return isinstance(stage, str) and stage == "seated"


def seatable_names(reg=None):
    """The registry names that may seat, in row order: the stage gate above applied once, for every caller that builds
    a seating list (unit_runner's mix, repair_wave's arms, or_fanout's job allowlist). Until the FX-31.7 follow-up each
    of them took every registry key, so a BROTHER_WORKER_MIX or a job naming a shadow or retired row seated it while
    chain() alone refused it. Raises Refused like registry()."""
    models = registry(reg)
    return [n for n in models if _seated(n, models)]


def _open_keys():
    """FX-11.4 (REQ-FX11-15): (names, transports) the breaker marks open, or (None, None) when the switch is off.

    The breaker is FX-11.1's module (scripts/loop/breaker.py) and is imported INSIDE this function with a literal
    `import breaker`, the only form the freeze stage can read. A missing breaker module cannot decide the switch, so
    the switch itself is read from the environment: an explicit on with no readable breaker REFUSES (fail closed),
    because a breaker that cannot be read is no evidence that a key is closed."""
    env_on = str(os.environ.get("BROTHER_BREAKER", "") or "").strip().lower() == "on"
    # the loop copy, named, so the deploy's parity check can bind this import (it refused the bare import, 2026-10-02)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import breaker
    except ImportError:
        if env_on:
            raise Refused("breaker NO-DATA: BROTHER_BREAKER=on and the breaker module cannot be imported, so no key can be read")
        return None, None
    if breaker.mode() != "on":
        return None, None
    try:
        keys = breaker.open_keys()
    except breaker.BreakerNoData as exc:
        raise Refused("breaker NO-DATA: %s" % (exc,))
    names, transports = set(), set()
    for key in keys or ():
        parts = str(key).split(":")
        if len(parts) == 4 and parts[3] == "shadow":
            continue      # a shadow key never closes the seated one (adv a6)
        if len(parts) == 3:
            names.add(parts[2])
        elif len(parts) == 2:
            transports.add(parts[0])
    return names, transports


def _is_open(name, reg, open_names, open_transports):
    """Is this model's breaker key open? A None pair means the switch is off and nothing is open."""
    if open_names is None:
        return False
    if name in open_names:
        return True
    row = reg.get(name) or {}
    return row.get("transport") in (open_transports or ())


def chain(kind, sensitivity=PUBLIC, pin=None, rel=None, registry_arg=None):
    """The ordered list of models to try, best first. Rule 3 and 4 live here.

    Returns [] only when NOTHING is eligible, and the caller must treat that as NO-DATA rather
    than falling back to a default, because a default is how the privacy gate gets bypassed.

    FX-11.4 adds two gates to every candidate: a retired, shadow or unknown stage never seats (REQ-FX11-14), and
    when BROTHER_BREAKER is on a model whose key is open leaves the AUTOMATIC rest. A PIN is never silently
    dropped (REQ-FX11-15): it stays first whatever its key, and its call is refused at admission instead."""
    # THROUGH the chokepoint, never around it. registry(None) returns the validated real registry
    # and registry(dict) validates the caller's, so both paths meet the same gate.
    reg = registry(registry_arg)
    rel = reliability() if rel is None else rel
    known = sorted({k for m in reg.values() for k in m["kinds"]})
    if kind not in known:
        raise Refused("unknown task kind %r; this registry does %s" % (kind, ", ".join(known)))

    open_names, open_transports = _open_keys()

    def eligible(n):
        return (automatic(n, reg) and _seated(n, reg) and may_receive(n, sensitivity, reg)
                and can_do(n, kind, reg) and not _is_open(n, reg, open_names, open_transports))

    if pin:
        if pin not in reg:
            raise Refused("model %r is not in the registry" % pin)
        if not _seated(pin, reg):
            raise Refused("%s is not seated and cannot be pinned" % pin)
        # Rule 3: an override chooses among the eligible; it does not grant an exemption.
        assert_may_send(pin, sensitivity, kind, reg)
        rest = [n for n in reg if n != pin and eligible(n)]
        return [pin] + _rank(rest, kind, rel, reg)

    return _rank([n for n in reg if eligible(n)], kind, rel, reg)


# THE TRANSPORT ALLOWLIST (owner 2026-09-30, "brother.loop and repair.loop worked perfectly with only Claude Code
# models"): BROTHER_TRANSPORTS names the transports a run may use ("claude", or "claude,codex"). Unset or blank keeps
# today's behaviour, every transport. It binds at BOTH gates of this file: automatic() for selection and
# assert_may_send() at the wire, so a pinned or hard coded bridge model is refused before the bridge is spawned.
# A word that is not a transport refuses EVERYTHING, naming the word: a misspelt allowlist never opens the bridge.
TRANSPORTS_SETTING = "BROTHER_TRANSPORTS"
TRANSPORTS = ("bridge", "claude", "codex")


def transports_allowed(env=None):
    """The set of transports this run allows, or None when the setting is unset or blank (every transport)."""
    raw = (os.environ if env is None else env).get(TRANSPORTS_SETTING)
    if not isinstance(raw, str) or not raw.strip():
        return None
    words = {w.strip().lower() for w in raw.split(",") if w.strip()}   # case blind: CLAUDE is claude
    if not words:   # "," or ",,": set, but naming nothing, is not "every transport"; it refuses
        raise Refused("%s is set to %r, which names no transport (%s); nothing is sent" % (TRANSPORTS_SETTING, raw, ", ".join(TRANSPORTS)))
    bad = sorted(w for w in words if w not in TRANSPORTS)   # a token with any other character is not a transport: never sanitized
    if bad:
        raise Refused("%s names %s, not a transport (%s); nothing is sent" % (TRANSPORTS_SETTING, bad, ", ".join(TRANSPORTS)))
    return words


def transports_text(allowed):
    """The canonical text of an allowlist set, what a record or a launch setting carries (sorted, comma joined)."""
    return ",".join(sorted(allowed))


def automatic(name, reg):
    """May this model be chosen WITHOUT being named? False for a transport the owner reserved for explicit choice,
    and false for a transport outside BROTHER_TRANSPORTS when that is set."""
    t = reg[name]["transport"]
    allowed = transports_allowed()
    return t not in BY_CHOICE_ONLY and (allowed is None or t in allowed)


def _rank(names, kind, rel, reg):
    """Quality for THIS kind first, then measured reliability, then cost. Cost breaks ties rather
    than leading, because the cheapest model returning work that is thrown away is the most
    expensive thing this estate measured all month: 50 percent of machine effort discarded."""
    def key(n):
        m = reg[n]
        return (-m["quality"].get(kind, 0), -rel.get(m["id"], 0.5), m["cost"], n)
    return sorted(names, key=key)


# ---------------------------------------------------------------- availability
def probe(name, timeout=90):
    """Is this model reachable RIGHT NOW? (ok, detail). A DELEGATE since 2026-09-30: the proof is
    model_reachability.prove, one minimal real call through model_call.call_one with the production flags and the program
    production resolves. The copy that lived here built its own command line with a hardcoded ~/.local/bin/claude, the
    very program that did not know the model on 2026-09-30, so its answer described a call the loop never makes.
    timeout is kept for callers; the proof's own per transport bound applies."""
    m = registry().get(name)
    if m is None:
        return False, "not in the registry"
    try:
        assert_may_send(name, PUBLIC, "decide" if name == "jev" else "build")   # the transport allowlist, before any spawn
    except Refused as exc:
        return False, "not probed: %s" % exc
    # the loop copy, named, so the deploy's parity check can bind this import (it refused the bare import: two copies)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import model_reachability as MR   # imported only past the gate: loading it resolves the code root, which runs git
    p = MR.prove(name)
    return p["status"] == "OK", (p["cause"] if p["status"] == "OK" else "%s: %s" % (p["status"], p["cause"]))


# ---------------------------------------------------------------- selftest
def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    saved = os.environ.pop(TRANSPORTS_SETTING, None)   # every case pins its own switch value; the caller's is restored after
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1
    finally:
        if saved is not None: os.environ[TRANSPORTS_SETTING] = saved

def _selftest_body():
    fake = {
        "cheap":  {"id": "x/cheap", "transport": "bridge", "privacy": PUBLIC,
                   "quality": {"build": 5}, "cost": 1.0},
        "good":   {"id": "x/good", "transport": "bridge", "privacy": PUBLIC,
                   "quality": {"build": 9}, "cost": 20.0},
        "local":  {"id": "x/local", "transport": "claude", "privacy": PRIVATE,
                   "quality": {"build": 7}, "cost": 30.0},
        "typed":  {"id": "x/typed", "transport": "bridge", "privacy": PUBLIC,
                   "quality": {"decide": 9}, "cost": 0.5},
    }
    for m in fake.values():
        m["kinds"] = set(m["quality"])        # derived exactly as load_registry derives it
    def ch(**kw):
        kw.setdefault("registry_arg", fake); kw.setdefault("rel", {})
        return chain(**kw)

    def _with(env, fn):
        """Run fn with exactly `env` for the transport setting, then restore the caller's environment."""
        saved = os.environ.get(TRANSPORTS_SETTING)
        os.environ.pop(TRANSPORTS_SETTING, None); os.environ.update(env)
        try: return fn()
        finally:
            os.environ.pop(TRANSPORTS_SETTING, None)
            if saved is not None: os.environ[TRANSPORTS_SETTING] = saved

    def _no_spawn(fn):
        """(False, "spawned nothing") when fn returns not ok without calling subprocess.run; anything else is the truth."""
        real = subprocess.run
        subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("a process was spawned"))
        try:
            ok, detail = fn()
        except AssertionError as exc:
            return (True, str(exc))
        finally:
            subprocess.run = real
        return (ok, "spawned nothing" if not ok and TRANSPORTS_SETTING in detail else detail)

    def _parse_refusal(env):
        """The parser's own refusal text for env, or the repr of what it returned (a set or None) when it did not refuse."""
        try: return repr(transports_allowed(env))
        except Refused as exc: return str(exc)

    def _wire(name, env):
        """'' when assert_may_send lets `name` through under `env`, else the refusal text."""
        def go():
            try: assert_may_send(name, PUBLIC, "build", fake); return ""
            except Refused as exc: return str(exc)
        return _with(env, go)

    def refuses(fn):
        try:
            fn(); return False
        except Refused:
            return True

    def refuses_saying(fn, text):
        """A refusal is only evidence when it is the refusal you MEANT.

        Asserting the reason, not merely that something was refused, is what stops a fixture from
        being satisfied by a guard it was not aimed at. Measured on this module 2026-09-21: an
        adversarial sweep removed the pin membership check in chain() and every case stayed green,
        because the wire check one line later refuses an unknown name too, with a different reason.
        Anything that is not a Refused is a failure here, including a KeyError from a field whose
        presence the deleted guard was the thing that guaranteed."""
        try:
            fn()
        except Refused as exc:
            return text in str(exc)
        except Exception:
            return False
        return False

    # ---- fixtures on disk. ORTHOGONAL BY CONSTRUCTION: every bad registry below differs from one
    # good row in EXACTLY ONE way, and the good row uses the claude transport at the private class
    # so the third party binding can never be the guard that fires for a fixture aimed elsewhere.
    # The rule that cost this estate a night: a fixture that trips two guards proves neither,
    # because either guard alone keeps it green.
    # Temp files, never HOME: the selftest has to pass on a machine with an empty home directory.
    tmp = tempfile.mkdtemp(prefix="model-router-selftest-")

    def good_row(**over):
        row = {"id": "x/ok", "transport": "claude", "privacy": PRIVATE,
               "quality": {"build": 5}, "cost": 1.0}
        row.update(over)
        return row

    def reg_file(doc, tag):
        path = os.path.join(tmp, tag + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def raw_file(text, tag):
        """A fixture that is NOT valid JSON, which reg_file cannot produce because it dumps a dict."""
        path = os.path.join(tmp, tag + ".json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def ledger(rows, tag):
        path = os.path.join(tmp, tag + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return path

    def leave(model, ok, n):
        row = {"stage": "model", "event": "leave", "ok": ok}
        if model is not None:
            row["model"] = model
        return [dict(row) for _ in range(n)]

    # A REAL malformed registry is a file that PARSES and carries a bad row. The case that used to
    # carry this name called load_registry on a missing path, so it exercised the "no registry
    # found" branch and never met a row at all. Identical defect to the one already fixed in
    # stage_log.py, where "a corrupt line is skipped" loaded a missing file.
    # A REFUSAL WITH THE WRONG REASON SENDS THE READER TO THE WRONG PLACE. Found by the generic
    # mutation sweep on 2026-09-21: deleting the invalid JSON refusal left the loader STILL
    # refusing, because the candidate loop falls through to "no registry found". The outcome was
    # right and the diagnosis was a lie, reporting a registry that EXISTS and is corrupt as one
    # that is missing, which sends whoever reads it to look in the wrong place. Only asserting the
    # REASON catches this, which is why refuses_saying exists.
    corrupt_named_correctly = refuses_saying(
        lambda: load_registry(raw_file("{not json at all", "corrupt")), "not valid JSON")

    row_missing_a_field = all(
        refuses_saying(
            lambda f=f: load_registry(reg_file(
                {"models": {"solo": {k: v for k, v in good_row().items() if k != f}}},
                "missing-" + f)),
            "is missing")
        for f in ("id", "transport", "privacy", "quality", "cost"))

    third_party_cannot_declare_itself_private = all(
        refuses_saying(
            lambda t=t, cls=cls: load_registry(reg_file(
                {"models": {"solo": good_row(transport=t, privacy=cls)}},
                "thirdparty-%s-%s" % (t, cls))),
            "third party that may retain")
        for t, cls in (("bridge", PRIVATE), ("codex", PRIVATE), ("bridge", INTERNAL)))

    # ---- fixtures for the reliability term. Each asserts an EXACT value, so a mutation that
    # returns the empty map, miscounts a failure, or drops the sample floor changes the number.
    rel_read = reliability(ledger(leave("x/win", True, 5), "rel-all-ok"))
    rel_mixed = reliability(ledger(leave("x/mix", True, 2) + leave("x/mix", False, 3), "rel-mixed"))
    rel_floor = reliability(ledger(leave("x/four", True, 4) + leave("x/five", True, 5), "rel-floor"))
    rel_unlabelled = reliability(ledger(leave(None, True, 8), "rel-no-model-key"))

    rec_path = os.path.join(tmp, "record-outcome.jsonl")
    rec_returned = record_outcome("x/rec", "build", True, seconds=1.5, path=rec_path)
    try:
        with open(rec_path, encoding="utf-8") as f:
            rec_row = json.loads(f.read().strip())
    except (OSError, ValueError):
        rec_row = {}

    # An unrun model must sit BETWEEN a proven one and a failing one. Equal quality and equal cost
    # are what make this a test of the reliability default at all: the case that used to carry this
    # name compared quality 5 against quality 9, so quality decided and the default was never read.
    tiers = {n: {"id": "x/" + n, "transport": "claude", "privacy": PRIVATE,
                 "quality": {"build": 7}, "kinds": {"build"}, "cost": 1.0}
             for n in ("proven", "unrun", "poor")}
    unrun_order = chain(kind="build", sensitivity=PRIVATE,
                        rel={"x/proven": 0.9, "x/poor": 0.1}, registry_arg=tiers)
    # A corrupt line in the stages ledger is skipped AND named (lint 2026-09-22); the rates still compute.
    import io
    _sp = os.path.join(tempfile.mkdtemp(), "stages.jsonl")
    with open(_sp, "w", encoding="utf-8") as _fh:
        _fh.write("NOT JSON\n" + "".join(json.dumps({"stage": "model", "event": "leave", "model": "x/m", "ok": True}) + "\n" for _ in range(5)))
    _err, _real_err = io.StringIO(), sys.stderr
    sys.stderr = _err
    try:
        _rates = reliability(path=_sp)
    finally:
        sys.stderr = _real_err

    # ROW STAGE (FX-31.5, R-FX-31-4): a retired or shadow row never enters the automatic chain, however it scores, and
    # a pin cannot bring one back. Three bridge rows, one condition each: the best scoring is retired, the middle is
    # shadow, only the lowest is seated, so the chain is exactly the seated one.
    staged = {"best": {"id": "x/best", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 9}, "cost": 1.0, "stage": "retired"},
              "mid": {"id": "x/mid", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 7}, "cost": 1.0, "stage": "shadow"},
              "seated": {"id": "x/seated", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 5}, "cost": 1.0}}
    staged_chain = chain("build", PUBLIC, rel={}, registry_arg=staged)

    # DERIVED VIEWS (FX-31.6, R-FX-31-5): every legacy table is a projection of the rows. One fixture carries every
    # optional field once, and each refusal fixture differs from a good bridge row in EXACTLY ONE field.
    viewed = {"b": {"id": "x/b", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 5}, "cost": 1.0,
                    "aliases": ["bee"], "answers_as": "x/b-dated", "spoken": ["the b one"],
                    "arm": {"keys": ["b", "bridge"], "cost_usd": 0.02, "mix": 3}},
              "c": {"id": "x/c", "transport": "claude", "privacy": PRIVATE, "quality": {"build": 5}, "cost": 1.0,
                    "spoken": ["the c one"], "arm": {"keys": ["c"], "cost_usd": 0.1}},
              "d": {"id": "x/d", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 5}, "cost": 1.0}}
    views = derive_model_views(viewed)

    def viewed_with(**over):
        base = {"id": "x/v", "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 5}, "cost": 1.0}
        base.update(over)
        return {"v": base}

    def view_refuses(text, **over):
        return refuses_saying(lambda: derive_model_views(viewed_with(**over)), text)

    def two_rows(field, value):
        return {n: {"id": "x/" + n, "transport": "bridge", "privacy": PUBLIC, "quality": {"build": 5}, "cost": 1.0, field: value}
                for n in ("a", "b")}

    real_views = derive_model_views(registry())
    real_bridge = [n for n in registry() if registry()[n]["transport"] == "bridge"]

    # THE FIXTURE FOLLOWS THE REGISTRY (2026-10-06). A tree that ships no registry, under a HOME that holds none (the
    # public export in a stranger's hands), is answered by the tracked fixture, the last candidate above. It was a
    # hand copy nothing kept in step: muse was retired on 2026-10-05 in docs/plan alone, so every stranger's default
    # mix still seated it with a share of 2, and the cut preflight's export tree gate refused on this very selftest.
    # Where a tree carries both files they are compared byte for byte; a tree that carries only one cannot compare,
    # which is said on its own line below and is never counted as a case that passed.
    _loop = os.path.dirname(os.path.abspath(__file__))
    _tracked = os.path.join(os.path.dirname(os.path.dirname(_loop)), "docs", "plan", "model-registry.json")
    _fixture = os.path.join(os.path.dirname(_loop), "fixtures", "model-registry-fixture.json")
    fixture_is_a_copy = None
    if os.path.isfile(_tracked) and os.path.isfile(_fixture):
        with open(_tracked, "rb") as _a, open(_fixture, "rb") as _b:
            fixture_is_a_copy = _a.read() == _b.read()

    cases = [
        ("a bridge row projects its own name onto its id, and its aliases beside it",
         views["bridge_aliases"] == {"b": "x/b", "bee": "x/b", "d": "x/d"}),
        ("dispatch ids carry answers_as when the row says the bridge answers under another id, else the id",
         views["dispatch_ids"] == {"b": "x/b-dated", "d": "x/d"}),
        ("spoken phrases map to the row's name, on every transport", views["spoken"] == {"the b one": "b", "the c one": "c"}),
        ("arm keys project in row order, each onto its row", views["arm_of"] == (("b", "b"), ("bridge", "b"), ("c", "c"))),
        ("the default mix names only the arms that carry a mix", views["default_mix"] == "b:3"),
        ("the arm cost names every arm with its USD figure, two decimals when exact, as the hand typed table did",
         views["arm_cost"] == "b:0.02,c:0.10" and _usd_text(0.005) == "0.005" and _usd_text(1) == "1.00"),
        ("every bridge row of the real registry is a bridge alias under its own name and id, and a dispatch alias exactly when seated",
         bool(real_bridge) and all(real_views["bridge_aliases"].get(n) == registry()[n]["id"]
                                   and (n in real_views["dispatch_ids"]) == _seated(n, registry())
                                   for n in real_bridge)),
        ("no row off the bridge reaches a bridge view",
         not any(n in real_views["dispatch_ids"] or n in real_views["bridge_aliases"] for n in registry() if n not in real_bridge)),
        ("aliases that are not a list refuse, naming the field", view_refuses("'aliases'", aliases="bee")),
        ("an empty aliases list refuses rather than reading as none", view_refuses("'aliases'", aliases=[])),
        ("an empty answers_as refuses, never an empty expected id", view_refuses("answers_as", answers_as="")),
        ("aliases on a row that is not on the bridge refuse, naming its transport",
         refuses_saying(lambda: derive_model_views({"v": good_row(aliases=["x"])}), "claude transport")),
        ("an arm that is not a mapping refuses", view_refuses("arm must be a mapping", arm="v")),
        ("an arm without cost_usd refuses", view_refuses("arm must be a mapping", arm={"keys": ["v"]})),
        ("an arm with a field this router does not know refuses, never ignores it",
         view_refuses("arm must be a mapping", arm={"keys": ["v"], "cost_usd": 0.1, "extra": 1})),
        ("a negative arm cost refuses", view_refuses("cost_usd", arm={"keys": ["v"], "cost_usd": -1})),
        ("a boolean arm cost refuses: True is not a price", view_refuses("cost_usd", arm={"keys": ["v"], "cost_usd": True})),
        ("arm keys that are not a list refuse", view_refuses("'keys'", arm={"keys": "v", "cost_usd": 0.1})),
        ("a zero mix refuses: an arm in the mix builds at least once", view_refuses("mix", arm={"keys": ["v"], "cost_usd": 0.1, "mix": 0})),
        ("a boolean mix refuses", view_refuses("mix", arm={"keys": ["v"], "cost_usd": 0.1, "mix": True})),
        ("a mix share on a retired row refuses: a retired model is never seated by the worker mix",
         view_refuses("carries no mix share", stage="retired", arm={"keys": ["v"], "cost_usd": 0.1, "mix": 1})),
        ("the real default mix names no retired row (muse, retired 2026-10-05)",
         "muse" not in real_views["default_mix"] and real_views["default_mix"] != ""),
        ("one spoken phrase on two rows refuses, naming both", refuses_saying(lambda: derive_model_views(two_rows("spoken", ["same"])), "names two models")),
        ("one bridge alias on two rows refuses, naming both ids", refuses_saying(lambda: derive_model_views(two_rows("aliases", ["z"])), "names two ids")),
        ("the views cross the same gate as the file: a private bridge row refuses here too",
         refuses_saying(lambda: derive_model_views(viewed_with(privacy=PRIVATE)), "third party that may retain")),
        ("the whole document loads with its models validated and read_on readable",
         isinstance(load_registry_document().get("read_on"), str) and check_registry_freshness(load_registry_document())[0] in ("OK", "WARN")),
        ("a retired row is excluded from the automatic chain even when it scores highest", "best" not in staged_chain),
        ("a shadow row is excluded from the automatic chain too", "mid" not in staged_chain),
        ("the seated row is what remains, so the stage filter removed rows and not the chain", staged_chain == ["seated"]),
        ("a pinned retired row is refused by name, never silently replaced",
         refuses_saying(lambda: chain("build", PUBLIC, pin="best", rel={}, registry_arg=staged), "not seated")),
        ("a pinned shadow row is refused the same way",
         refuses_saying(lambda: chain("build", PUBLIC, pin="mid", rel={}, registry_arg=staged), "not seated")),
        ("a corrupt stages line is skipped and the rates still computed", _rates.get("x/m") == 1.0),
        ("the skipped stages line is named on stderr, never silent", "1 unreadable line(s)" in _err.getvalue()),
        ("quality for the kind leads, not cost", ch(kind="build", sensitivity=PUBLIC)[0] == "good"),
        ("a typed only model never appears for a build", "typed" not in ch(kind="build", sensitivity=PUBLIC)),
        ("a build model never appears for a decision", ch(kind="decide", sensitivity=PUBLIC) == ["typed"]),
        ("private content reaches ONLY the local model", ch(kind="build", sensitivity=PRIVATE) == ["local"]),
        ("an UNKNOWN sensitivity fails closed to local", ch(kind="build", sensitivity="banana") == ["local"]),
        ("an unlabelled sensitivity never reaches a bridge",
         all(fake[n]["transport"] != "bridge" for n in ch(kind="build", sensitivity="banana"))),
        ("a pin is honoured and put first", ch(kind="build", sensitivity=PUBLIC, pin="cheap")[0] == "cheap"),
        ("with BROTHER_TRANSPORTS unset every transport is allowed (today's behaviour)",
         transports_allowed({}) is None and transports_allowed({TRANSPORTS_SETTING: " "}) is None and _wire("cheap", {}) == ""),
        ("BROTHER_TRANSPORTS=claude refuses a bridge model AT THE WIRE, naming the setting",
         TRANSPORTS_SETTING in _wire("cheap", {TRANSPORTS_SETTING: "claude"}) and "bridge transport" in _wire("cheap", {TRANSPORTS_SETTING: "claude"})),
        ("BROTHER_TRANSPORTS=claude still sends to a claude model", _wire("local", {TRANSPORTS_SETTING: "claude"}) == ""),
        ("BROTHER_TRANSPORTS=claude leaves no bridge model in the automatic chain",
         _with({TRANSPORTS_SETTING: "claude"}, lambda: ch(kind="build", sensitivity=PUBLIC)) == ["local"]),
        ("probe() under BROTHER_TRANSPORTS=claude refuses a bridge model before any process is spawned",
         (lambda: _with({TRANSPORTS_SETTING: "claude"}, lambda: _no_spawn(lambda: probe("deepseek", timeout=1))))() == (False, "spawned nothing") ),
        ("case is normalized: CLAUDE and Claude read as claude", transports_allowed({TRANSPORTS_SETTING: "CLAUDE"}) == {"claude"} and transports_allowed({TRANSPORTS_SETTING: " Claude , codex"}) == {"claude", "codex"}),
        ("a malformed token is refused, never sanitized into a transport",
         all(TRANSPORTS_SETTING in _wire("local", {TRANSPORTS_SETTING: v}) for v in ("claude,-bridge", "claude,!bridge", "brid ge", "claude;bridge", "claude bridge"))
         and _wire("local", {TRANSPORTS_SETTING: "claude,"}) == ""),
        ("a set value naming no transport (',' or ',,') is REFUSED BY THE PARSER, naming it, never unset and never an empty set",
         all(_parse_refusal({TRANSPORTS_SETTING: v}).startswith("%s is set to" % TRANSPORTS_SETTING) and "names no transport" in _parse_refusal({TRANSPORTS_SETTING: v}) for v in (",", ",,", " , "))),
        ("transports_text is the sorted comma text", transports_text({"codex", "claude"}) == "claude,codex"),
        ("a word that is not a transport refuses even a claude model, naming the word",
         "['openrouter']" in _wire("local", {TRANSPORTS_SETTING: "claude,openrouter"})),
        ("a pin still leaves a fallback chain behind it", len(ch(kind="build", sensitivity=PUBLIC, pin="cheap")) > 1),
        ("a pin CANNOT publish private content",
         refuses(lambda: ch(kind="build", sensitivity=PRIVATE, pin="cheap"))),
        ("a pin CANNOT send prose to a typed endpoint",
         refuses(lambda: ch(kind="build", sensitivity=PUBLIC, pin="typed"))),
        # The wire check refuses an unknown name too, so only the REASON distinguishes the guard
        # in chain() from the one downstream. Without this, deleting the membership check survives.
        ("a pin that is not in the registry is refused HERE, by name, not downstream",
         refuses_saying(lambda: ch(kind="build", sensitivity=PUBLIC, pin="nope"),
                        "is not in the registry")),
        ("an unknown kind is refused, never defaulted",
         refuses(lambda: chain(kind="banana", sensitivity=PUBLIC, rel={}, registry_arg=fake))),
        ("the wire check refuses private content on a bridge model",
         refuses(lambda: assert_may_send("deepseek", PRIVATE, "build"))),
        ("the wire check refuses a kind the model cannot do",
         refuses(lambda: assert_may_send("jev", PUBLIC, "build"))),
        ("the wire check passes a lawful pairing", assert_may_send("deepseek", PUBLIC, "build") is True),
        # The tier must actually be EQUAL for this to test what its name says. The first version
        # compared quality 5 against quality 7 and passed for the wrong reason: quality decided,
        # exactly as designed, and the reliability term was never consulted.
        ("measured reliability outranks cost within one quality tier",
         chain(kind="build", sensitivity=PUBLIC, rel={"x/dear": 0.99, "x/thrifty": 0.10},
               registry_arg={"thrifty": {"id": "x/thrifty", "transport": "bridge", "privacy": PUBLIC,
                                         "quality": {"build": 7}, "kinds": {"build"}, "cost": 1.0},
                             "dear": {"id": "x/dear", "transport": "bridge", "privacy": PUBLIC,
                                      "quality": {"build": 7}, "kinds": {"build"}, "cost": 99.0}})[0] == "dear"),
        ("cost breaks a tie only when quality AND reliability are equal",
         chain(kind="build", sensitivity=PUBLIC, rel={},
               registry_arg={"thrifty": {"id": "x/thrifty", "transport": "bridge", "privacy": PUBLIC,
                                         "quality": {"build": 7}, "kinds": {"build"}, "cost": 1.0},
                             "dear": {"id": "x/dear", "transport": "bridge", "privacy": PUBLIC,
                                      "quality": {"build": 7}, "kinds": {"build"}, "cost": 99.0}})[0] == "thrifty"),
        ("a model nobody has run scores neutral: behind a proven one, ahead of a failing one",
         unrun_order == ["proven", "unrun", "poor"]),
        ("no eligible model returns an EMPTY chain, never a default",
         chain(kind="decide", sensitivity=PRIVATE, rel={}, registry_arg=fake) == []),
        ("the real registry file loads and validates", len(registry()) >= 3),
        ("every real entry declares a known privacy class",
         all(m["privacy"] in ORDER for m in registry().values())),
        ("kinds are DERIVED from quality, so the two cannot disagree",
         all(m["kinds"] == set(m["quality"]) for m in registry().values())),
        ("all three transports are represented",
         {m["transport"] for m in registry().values()} >= {"bridge", "claude", "codex"}),
        ("a registry row missing a required field REFUSES, never silently skips the row",
         row_missing_a_field),
        ("a registry that is not valid JSON is refused BY NAME, not as a missing file",
         corrupt_named_correctly),
        ("a registry row declaring an unknown privacy class is refused",
         refuses_saying(lambda: load_registry(reg_file(
             {"models": {"solo": good_row(privacy="banana")}}, "unknown-privacy")),
             "unknown privacy class")),
        ("a registry row with an EMPTY quality map is refused, since it can do nothing",
         refuses_saying(lambda: load_registry(reg_file(
             {"models": {"solo": good_row(quality={})}}, "empty-quality")),
             "can do nothing")),
        ("a registry document naming NO models is refused, never treated as an empty chain",
         refuses_saying(lambda: load_registry(reg_file({"models": {}}, "no-models")),
                        "names no models")),
        ("a third party transport cannot declare itself private, whatever the file says",
         third_party_cannot_declare_itself_private),
        ("no registry file at all is refused, never defaulted",
         refuses_saying(lambda: load_registry("/no/such/registry.json"),
                        "no model registry found")),
        ("a private build reaches only first party models",
         all(registry()[n]["transport"] == "claude"
             for n in chain("build", PRIVATE, rel={}))),
        ("no DEFAULT chain of any kind contains a codex model: it is never chosen unless named",
         all(registry()[n]["transport"] != "codex"
             for k in sorted({k for m in registry().values() for k in m["kinds"]}) for n in chain(k, PUBLIC, rel={}))),
        ("every kind still has a default public chain without codex, so nothing became unroutable",
         all(chain(k, PUBLIC, rel={}) for k in sorted({k for m in registry().values() for k in m["kinds"]}))),
        ("a codex model that is NAMED leads its chain: the owner can still choose it as the worker",
         chain("build", PUBLIC, pin="luna", rel={})[0] == "luna"),
        ("and its failovers are automatic models only, never another codex model nobody named",
         all(registry()[n]["transport"] != "codex" for n in chain("build", PUBLIC, pin="luna", rel={})[1:])),
        ("a named codex model is still bound by privacy: private content refuses it",
         refuses_saying(lambda: chain("build", PRIVATE, pin="luna", rel={}), "may receive public at most")),
        ("reliability READS the ledger; it does not return an empty map", rel_read == {"x/win": 1.0}),
        ("a failure in the ledger is counted as a failure, never as a success",
         abs(rel_mixed.get("x/mix", -1.0) - 0.4) < 1e-9),
        ("a model under the minimum sample floor is not ranked at all",
         rel_floor == {"x/five": 1.0}),
        ("a ledger whose rows carry no model leaves the term inactive", rel_unlabelled == {}),
        ("record_outcome writes the model onto a durable row",
         rec_returned is True and rec_row.get("model") == "x/rec" and rec_row.get("kind") == "build"
         and rec_row.get("ok") is True and rec_row.get("event") == "leave"),
    ]
    _hub_tree = os.path.exists(os.path.join(os.path.dirname(os.path.dirname(_loop)), ".brother-edition"))
    if fixture_is_a_copy is None and _hub_tree:
        # The hub tree (it carries the edition marker, a hard exclude of every export) MUST hold both files. One
        # missing there is a lost registry or a lost fixture, never a tree that simply ships without them: before
        # 2026-10-06 that read NO-DATA, exit 0, and the gate row stayed green on a hub that had lost its registry.
        cases.append(("the hub tree carries both docs/plan/model-registry.json and its fixture copy, so the two can be "
                      "compared (one is missing here)", False))
    elif fixture_is_a_copy is None:
        print("NO-DATA: scripts/fixtures/model-registry-fixture.json was not compared with docs/plan/model-registry.json: "
              "this tree does not carry both, so nothing here says the fixture is current")
    else:
        cases.append(("the fixture registry is a byte copy of docs/plan/model-registry.json, because it answers every tree "
                      "that ships no registry (cp docs/plan/model-registry.json scripts/fixtures/model-registry-fixture.json)",
                      fixture_is_a_copy))
    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--available" in sys.argv:
        print("%-9s %-9s %-8s %s" % ("model", "transport", "live", "detail"))
        worst = 0
        for name in registry():
            ok, detail = probe(name)
            if not ok:
                worst = 1
            print("%-9s %-9s %-8s %s" % (name, registry()[name]["transport"], "yes" if ok else "NO", detail))
        return worst
    kind = sys.argv[sys.argv.index("--kind") + 1] if "--kind" in sys.argv else "build"
    sens = sys.argv[sys.argv.index("--sensitivity") + 1] if "--sensitivity" in sys.argv else PUBLIC
    pin = sys.argv[sys.argv.index("--pin") + 1] if "--pin" in sys.argv else None
    try:
        c = chain(kind, sens, pin)
    except Refused as exc:
        print("REFUSED: %s" % exc)
        return 1
    if not c:
        print("NO-DATA: no model may do %s at sensitivity %s. That is not a reason to use a default." % (kind, sens))
        return 1
    print("kind=%s sensitivity=%s%s" % (kind, sens, " pin=" + pin if pin else ""))
    for i, n in enumerate(c):
        print("  %d. %-9s %-9s quality %2d for %s, cost %.1f"
              % (i + 1, n, registry()[n]["transport"], registry()[n]["quality"].get(kind, 0), kind, registry()[n]["cost"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

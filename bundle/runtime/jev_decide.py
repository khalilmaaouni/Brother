#!/usr/bin/env python3
"""jev_decide: the one place this estate asks Jev (TypeSafe's decision
model, typesafe/jev-1.13) a typed question and reads back a probability.

WHY THIS EXISTS, JEV-01. Jev does not do chat: it only answers on the
outside-model bridge's (~/.claude/bin/or_ask.py) --decisions endpoint, one
batch of typed questions (noul, choice or score) in, one answer per
question out, each carrying a probability or confidence rather than prose.
A noul answer and a choice answer to the same underlying question report
DIFFERENT numbers (TypeSafe's own docs), so every record this module
returns carries a framing hash of exactly what was asked, not just the
question id, so two framings of one question are never confused for the
same measurement.

THE ONE ENTRY POINT.

  decide(state, questions, family, *, bridge=None, runner=None)
      Validates `questions`, runs bridge_content_gate.decide() against
      destination "typesafe" BEFORE anything else leaves this process,
      resolves which bridge command to run, invokes it, and returns either
      a list of Decision records (one per question, see below) or a
      two-item (NO_DATA, reason) tuple. Never raises for any of the
      documented failure causes below, and never invokes the bridge at all
      once validation or the content gate has already decided the call is
      unusable (see bridge_content_gate's own docstring for why the scan
      and the call live in one path a caller cannot reorder: this module
      applies the same discipline one level up, before the gate is even
      reached).

A Decision record is a plain dict: id, family (the caller's tag, never
sent outside), type, framing_hash, answer, probability (the chosen
answer's probability: the noul value itself for a noul question, the
matching entry in "probabilities" for a choice question, unset for a
score question, which has no probability concept), confidence (unset when
the answer carries none), model (exactly as the bridge reported it),
cost_share (the call's total reported cost split evenly across the batch,
unset when no cost was reported) and latency_seconds (the one call's
wall-clock time, identical across every record in the batch since one call
answers all of them together).

NEVER AN ANSWER FROM ANOTHER MODEL. Every one of these collapses to
(NO_DATA, reason), never a returned record: a question fails local
validation; the content gate refuses the payload; no bridge command can be
resolved (no `bridge` argument, no BROTHER_DECISION_BRIDGE, and the
default path does not exist); the bridge cannot even be launched; the
bridge exits nonzero; its stdout does not parse as JSON; the parsed object
has no answers, or is missing an answer for one of the questions asked;
an answer has no value under its own question's declared type (a noul
question whose answer carries no "noul" key, and so on); or the reported
top-level model string does not start with "typesafe/jev". A batch call is
all-or-nothing: one bad answer in the batch fails the whole call rather
than silently dropping the one question, since a caller that asked for N
answers and quietly got N-1 has no way to know which one went missing.

CONTINGENCY, edge by edge.

  empty or non-mapping questions   validation failure, NO_DATA, no gate
                                    call, no bridge call.
  unrecognised question type       validation failure; "noul", "choice",
                                    "score" are the only three, an
                                    unrecognised fourth stops the machine
                                    rather than being guessed at.
  choice/score with no criteria    validation failure (the bridge itself
                                    would refuse this; catching it here
                                    means the refusal names the actual
                                    problem instead of an opaque bridge
                                    exit).
  state or questions not JSON-safe caught at serialization, NO_DATA, no
                                    gate call (nothing to scan yet).
  BROTHER_DECISION_BRIDGE set but  NO_DATA naming the parse error, never a
  not a valid shell command line   guess at what was meant.
  bridge produces valid JSON with  the batch fails as a whole (see "never
  N-1 of N answers                 an answer from another model" above).
  reported model is a different    NO_DATA; a different model's answer is
  TypeSafe id, or a different      not Jev's answer, whatever the fallback
  vendor entirely                  policy of the model actually called was.
  bridge launched but the process  the default runner never lets this
  itself raises (missing           escape as an exception: it is caught
  interpreter, permission denied)  and reported as "could not be launched".
  BROTHER_DECISION_BRIDGE is set   treated as unset: falls through to
  but blank or whitespace only     DEFAULT_BRIDGE_PATH rather than stopping
                                    on a variable that says nothing.
  a noul (or a looked up choice    never a number, so never a real
  probability) that is a bool,     probability: a bool is caught before it
  a non-number, NaN or Infinity    can be mistaken for one, and NaN/
                                    Infinity are rejected the same way a
                                    non-numeric value is (they are legal
                                    Python floats but not legal JSON).
  a choice answer that is a list   not a legal answer to look up in
  or dict rather than a scalar     "criteria"/"probabilities" at all: the
                                    batch fails rather than this module
                                    quietly swallowing an unhashable value
                                    into an unresolved probability.
  a choice answer that is not one  NO_DATA, the batch fails as a whole
  of question["criteria"]'s keys,  (review 70268c3, C3): "unknown" and
  or a score answer that is not    any other answer outside the declared
  one of a declared list of        option set were both acted on before
  levels                           this fix. A score question's criteria
                                    is only checked when it is a list (the
                                    "declared levels" shape jev_registry
                                    produces); a descriptive, non-list
                                    criteria is left unvalidated, as before.
  usage.cost is present but not a  cost_share is left unset, the same as
  finite number                    when cost is absent entirely.

Python 3.9 floor, standard library only, no network in this module itself
(the network call lives inside the bridge subprocess, never here).
"""
import argparse
import hashlib
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_content_gate as _gate  # noqa: E402  (sibling module, reused not copied)

#: The wire format's three question types, per TypeSafe's own docs
#: (docs.typesafe.ai/primitives/*.md) and mirrored in
#: ~/.claude/bin/or_ask.py's own DECISION_TYPES. Kept as our own tuple
#: rather than importing or_ask.py: that file is not part of this package,
#: executes module level argparse/keychain setup, and is read-only for
#: this unit (see bridge_default_model.py for the same reasoning about the
#: same file).
QUESTION_TYPES = ("noul", "choice", "score")

#: The gate destination this module always asks about. See
#: bridge_content_gate.OUTSIDE_DESTINATIONS: "typesafe" is registered
#: there for exactly this module (JEV-01).
DESTINATION = "typesafe"

#: A real answer from Jev always starts with this. A different TypeSafe
#: model, or a different vendor's model returned instead, is not Jev's
#: answer, whatever caused the substitution.
MODEL_PREFIX = "typesafe/jev"

#: Where the bridge lives on this estate's machines when nothing else says
#: otherwise. Overridable per call via `bridge`, or per environment via
#: BROTHER_DECISION_BRIDGE; production code does not need to override it.
DEFAULT_BRIDGE_PATH = os.path.expanduser("~/.claude/bin/or_ask.py")

#: Seconds to wait on one decision call. The bridge's own --decisions path
#: never falls back to another model, so there is nothing to gain from a
#: long queue of retries here; one generous wait, then NO_DATA. Set above
#: the bridge's own default --timeout (300s, or_ask.py) with headroom, so
#: the bridge's own clean timeout failure (a real exit code and stderr
#: line) has a chance to land before this wrapper's timeout kills the
#: process first and reports a plainer "could not be invoked".
DEFAULT_TIMEOUT_SECONDS = 330

#: The failure marker. decide() returns (NO_DATA, reason), never raises,
#: for every documented failure cause; a caller checks
#: `isinstance(result, list)` to tell success from failure.
NO_DATA = "NO-DATA"

#: CLI exit code for "no usable answer" (validation failure, gate refusal,
#: no bridge, bad exit, bad parse, missing answer, wrong model: all of
#: them collapse to this one code, never exit 0).
EXIT_NO_DATA = 2


def _real_home_candidates():
    """Every real-filesystem home-directory path this process can name,
    for masking a home path out of a payload before it ever leaves the
    machine (A2, G1 review; item 5, A0.8 round 6: "mask the real home
    directory path once inside the shared seam helper before any
    payload leaves the machine"). More than one candidate on purpose:
    os.path.expanduser("~") only ever reports the CURRENT $HOME, and an
    isolated-HOME test harness (the pattern this estate's own tests use)
    overrides it on purpose -- a payload carrying the machine's REAL
    home path some other way (probed: BA-PERSONA-V2-RECORD-2026-08-28.md
    names it at a fixed character offset) would leak past a mask keyed
    only to the overridden value. pwd.getpwuid() reads the real
    passwd-database entry for this process's own uid, independent of
    $HOME, so it still names the real home even under an override. Each
    candidate's realpath is included too (macOS's own /Users vs
    /System/Volumes/Data/Users symlink is exactly this shape: A2 probed
    "leaked_real_home=True for the plain path and for the realpath").
    Never raises: any candidate this platform cannot produce (no pwd
    module -- it is POSIX only -- no passwd entry, an unresolvable
    symlink) is simply skipped, never fatal to the caller that wants to
    mask what it can. Never includes "/" or "~" themselves, so a bare
    root can never turn every absolute path in the payload into "~"."""
    candidates = set()
    expanded = os.path.expanduser("~")
    if expanded and expanded not in ("~", "/"):
        candidates.add(expanded)
    try:
        import pwd
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        if real_home and real_home not in ("~", "/"):
            candidates.add(real_home)
    except (ImportError, KeyError, OSError):
        pass
    for path in list(candidates):
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real and real not in ("~", "/"):
            candidates.add(real)
    return candidates


def _mask_home_paths(text):
    """`text` with every real home-directory path this process can name
    (see _real_home_candidates()) replaced by "~". Longest candidate
    first, so a shorter candidate that happens to be a prefix of a
    longer one (a home directory nested under another resolved
    candidate) never leaves a partial replacement behind. "" in, "" out;
    never raises."""
    if not text:
        return text
    for home in sorted(_real_home_candidates(), key=len, reverse=True):
        if home in text:
            text = text.replace(home, "~")
    return text


def _validate_questions(questions):
    """The first problem with `questions`, as a string, or None when every
    question is well formed. Never raises."""
    if not isinstance(questions, dict) or not questions:
        return "questions must be a non-empty mapping of id to question spec"
    for qid, question in questions.items():
        if not isinstance(question, dict):
            return "question %r is not a mapping" % (qid,)
        qtype = question.get("type")
        if qtype not in QUESTION_TYPES:
            return "question %r has type %r, must be one of %s" % (
                qid, qtype, "/".join(QUESTION_TYPES))
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            return "question %r has no non-empty instructions" % (qid,)
        if qtype in ("choice", "score") and not question.get("criteria"):
            return "%s question %r needs non-empty criteria" % (qtype, qid)
    return None


def _finite_number(value):
    """`value` if it is a real, finite int or float (never a bool, never
    NaN or +/-Infinity), else None. bool is a subclass of int in Python,
    so a stray True/False must never be read as a probability; NaN and
    Infinity are valid Python floats but not valid JSON, so a number that
    is one of them is exactly as unusable here as a non-numeric value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _framing_hash(question):
    """A stable hash over exactly what was asked: type, instructions and
    criteria together. A noul framing and a choice framing of the same
    wording hash differently (they answer with different numbers, per
    TypeSafe's docs); the same framing asked twice hashes the same.

    NO sort_keys (Muse review, JEV-01): jev_calibration.py's pooling rule
    never pools a reworded question into the same family/qtype/framing
    row, and a reversed-order choice question (jev_registry.both_orders(),
    used by jev_seam.py's near-threshold probe) IS a reworded question by
    that rule, since Jev is measured to answer it with a different
    probability. sort_keys=True sorts nested dict keys too, so it would
    have canonicalised a choice question's "criteria" dict back into one
    fixed key order regardless of which order the caller actually gave,
    making both_orders()'s two framings hash identically and pool
    together, exactly the defect the probe exists to catch. The three
    top-level keys ("type", "instructions", "criteria") are always
    inserted in this fixed order by the dict literal below, so dropping
    sort_keys does not cost the hash its own determinism: the same
    framing asked twice still produces the same dict, in the same key
    order, every time; only the caller-supplied order INSIDE criteria
    (which sort_keys would otherwise erase) survives into the hash.
    """
    material = json.dumps(
        {"type": question.get("type"), "instructions": question.get("instructions"),
         "criteria": question.get("criteria")},
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _resolve_bridge(bridge, require_default_file=True):
    """(argv list, None) for the outside bridge command, or (None, reason)
    when none can be resolved. `bridge` overrides everything when given;
    otherwise BROTHER_DECISION_BRIDGE; otherwise DEFAULT_BRIDGE_PATH,
    subject to `require_default_file` below; otherwise "no bridge
    configured" is itself the reason.

    `require_default_file`: whether the DEFAULT_BRIDGE_PATH fallback must
    exist on disk to be resolved. True (the default, and always the value
    used for a real call: see decide()) matches production exactly: a
    machine with no bridge installed gets NO_DATA naming the missing path,
    never a subprocess launch aimed at nothing. False is used only when
    the caller supplied its own `runner` (decide() passes it through): an
    injected runner never launches a real process, so the file's presence
    on THIS machine has nothing to protect and must not gate a fake call
    (JEV-DECIDE-01: this file check ran unconditionally, so every test
    across the Jev suites that injected a runner without also passing
    `bridge=` failed on a HOME with no ~/.claude/bin/or_ask.py, never
    reaching the runner it injected)."""
    if bridge is not None:
        if not bridge:
            return None, "bridge argument is an empty command"
        return list(bridge), None
    env_cmd = os.environ.get("BROTHER_DECISION_BRIDGE")
    # A blank or whitespace-only value is treated as unset, not as a
    # broken configuration: falls through to the default path below
    # rather than stopping here (Muse review, JEV-01: a caller's blank
    # interpolation should not shadow a perfectly good default).
    if env_cmd and env_cmd.strip():
        try:
            parsed = shlex.split(env_cmd)
        except ValueError as exc:
            return None, (
                "BROTHER_DECISION_BRIDGE could not be parsed as a command "
                "line: %s" % exc
            )
        if not parsed:
            return None, "BROTHER_DECISION_BRIDGE is set but empty"
        return parsed, None
    if require_default_file and not os.path.isfile(DEFAULT_BRIDGE_PATH):
        return None, (
            "no decision bridge configured: no bridge argument, no "
            "BROTHER_DECISION_BRIDGE, and %s does not exist" % DEFAULT_BRIDGE_PATH
        )
    return ["python3", DEFAULT_BRIDGE_PATH], None


#: M1(c) fix (Opus rereview4, 2026-09-19): every Popen _default_runner
#: launches is tracked here so kill_active_processes() (called by
#: jev_seam._atexit_drain() when its own bounded wait for in-flight
#: workers times out) can terminate a bridge subprocess whose worker
#: thread was abandoned at process exit -- otherwise it keeps running,
#: reparented to init, an orphan spending real budget with nothing left
#: to read its answer (probed: a 3s bridge outlived the CLI's own atexit
#: hook and was still running 10+ s later). Separate lock from anything
#: in jev_seam.py on purpose: this module has no dependency on that one
#: and must not create one just to track its own subprocesses.
_ACTIVE_PROCS_LOCK = threading.Lock()
_ACTIVE_PROCS = []


def _register_proc(proc):
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.append(proc)


def _unregister_proc(proc):
    with _ACTIVE_PROCS_LOCK:
        try:
            _ACTIVE_PROCS.remove(proc)
        except ValueError:
            pass


#: Bounded wait after a process-group kill (m2, Opus rereview5,
#: 2026-09-19) before giving up on reaping it: SIGKILL is not
#: instantaneous, and a caller (the timeout path, or kill_active_processes
#: at exit) must never block indefinitely on a wait() that follows it.
_KILL_WAIT_SECONDS = 5.0


def _kill_process_group(proc):
    """Best-effort SIGKILL to `proc`'s WHOLE process group, not just the
    one direct child (m2, Opus rereview5, 2026-09-19, regression in
    e1f4c669c): _default_runner below launches every bridge with
    start_new_session=True, which makes the bridge's own pid its process
    group id too (POSIX setsid semantics), so os.killpg(proc.pid, ...)
    reaches the bridge AND every process it spawned (a wrapper script's
    own python child, for example) -- proc.kill() alone only ever reached
    the direct child, leaving a grandchild that held the bridge's stdout
    pipe open running forever (probed: a wrapper bridge's python child
    was still ALIVE at process exit; the bridge's own direct child was
    already gone). Never raises: the group (or the process itself) may
    already be gone by the time this runs, on either branch."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        # AttributeError: os.killpg does not exist on this platform
        # (non-POSIX); proc.kill() below is the fallback there.
        pass
    try:
        proc.kill()
    except OSError:
        pass


def kill_active_processes():
    """Best-effort termination of every bridge subprocess this process has
    launched via _default_runner that is still running (M1(c), Opus
    rereview4, 2026-09-19; m2, Opus rereview5, 2026-09-19: kills the
    WHOLE process group, see _kill_process_group()). Called from
    jev_seam._atexit_drain() after its own bounded wait for in-flight
    workers times out, so a worker thread abandoned at process exit does
    not leave its already-launched bridge subprocess (and anything IT
    spawned) running unbounded and orphaned after this process is gone.
    Never raises: a process that already exited on its own, or that this
    process no longer has permission to signal, is simply skipped -- there
    is nothing more this function can do about either case."""
    with _ACTIVE_PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for proc in procs:
        try:
            if proc.poll() is None:
                _kill_process_group(proc)
        except OSError:
            pass


def _default_runner(argv, stdin_text):
    """(returncode, stdout, stderr) for a real subprocess call, or
    (None, "", reason) when the process could not even be launched or
    timed out. Never lets an exception escape: a launch failure is just
    another reason the call is unusable, not a traceback.

    Uses Popen directly (M1(c), Opus rereview4, 2026-09-19) rather than
    subprocess.run, and registers the live handle in _ACTIVE_PROCS for
    kill_active_processes() above -- subprocess.run's own internal Popen
    is not reachable from outside it, so a caller that abandons the
    thread running this function (a shadow worker, at process exit) had
    no way to reach the subprocess it started.

    Launched with start_new_session=True (m2, Opus rereview5,
    2026-09-19, regression fix): puts the bridge in its own process
    group (POSIX setsid), so a timeout can kill the bridge AND every
    process IT spawned, not just the one direct child subprocess.run's
    own documented timeout behaviour reaches. Before this fix, a bridge
    whose own child held the stdout/stderr pipes open (a shell wrapper,
    for example) made communicate()'s timeout take as long as that
    grandchild ran, not DEFAULT_TIMEOUT_SECONDS, and left the grandchild
    running after this function returned (measured: a 1s timeout took
    6.34s against an `sh` wrapper bridge, and the wrapper's own python
    child was still alive at process exit). On a communicate() timeout
    the WHOLE process group is now killed, then a bounded wait() (never
    communicate() again -- the pipes may already be gone once a
    grandchild that held them is killed) reaps the direct child before
    returning the same (None, "", reason) shape as before."""
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", "%s: %s" % (type(exc).__name__, exc)
    _register_proc(proc)
    try:
        try:
            stdout, stderr = proc.communicate(
                input=stdin_text, timeout=DEFAULT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=_KILL_WAIT_SECONDS)  # bounded reap, never another communicate()
            except subprocess.TimeoutExpired:
                pass  # best-effort: nothing more to do if SIGKILL itself does not land in time
            return None, "", "%s: %s" % (type(exc).__name__, exc)
        return proc.returncode, stdout, stderr
    finally:
        _unregister_proc(proc)


def decide(state, questions, family, *, bridge=None, runner=None):
    """See the module docstring. Returns a list of Decision records on
    success, or (NO_DATA, reason) on any documented failure. Never raises
    for a documented failure cause; never invokes the bridge once
    validation or the content gate has already refused the call.

    `runner`, when given, replaces the real subprocess call: a callable
    taking (argv, stdin_text) and returning (returncode, stdout, stderr),
    matching _default_runner's own contract. Tests inject a fake runner so
    no test ever touches the network or a real subprocess.
    """
    problem = _validate_questions(questions)
    if problem is not None:
        return (NO_DATA, "question validation failed: %s" % problem)

    try:
        # No sort_keys here (Muse review, JEV-01): Jev is measured to be
        # sensitive to a choice question's option ORDER (an independent
        # probe moved one probability from 0.84-0.89 to 0.93-0.96 by
        # reversing options), and both_orders()/jev_seam.py rely on the
        # order of `questions`' insertion and of each question's own
        # "criteria" dict surviving into the wire payload unchanged, since
        # that is the only way an order-reversed probe actually asks a
        # differently-framed question. Python 3.7+ dicts already preserve
        # insertion order, so dropping sort_keys is enough; allow_nan=False
        # stays: NaN/Infinity serialize under Python's default but are not
        # valid JSON, reject them here with a clear local reason rather
        # than shipping invalid JSON to the gate and the bridge.
        payload_text = json.dumps({"state": state, "questions": questions},
                                   allow_nan=False)
    except (TypeError, ValueError) as exc:
        return (NO_DATA, "state or questions could not be serialized to JSON: %s" % exc)

    # A2 fix (G1 review; item 5, A0.8 round 6): mask every real home-
    # directory path this process can name (see _mask_home_paths()) out
    # of the payload, ONCE, here -- the one place every seam call's
    # payload passes through before the content gate and before it can
    # ever reach the bridge subprocess -- rather than trusting each call
    # site's own ad hoc redaction (J096/J114 had none at all; J100's own
    # redaction missed this same path under an isolated-HOME test
    # harness). Reassigning payload_text means the masked text is both
    # what the gate scans AND what actually gets sent below: a call site
    # can no longer forget this, and a path the gate itself would not
    # have caught (bridge_content_gate.decide() is a content/injection
    # gate, not a PII redactor -- probed: it allowed a real home path
    # through clean) never reaches it in the first place.
    payload_text = _mask_home_paths(payload_text)

    verdict = _gate.decide(payload_text, DESTINATION)
    if not verdict.allowed:
        return (NO_DATA, "content gate refused: %s" % verdict.reason)

    # Fix at the source (independent review, JEV-DECIDE-01): resolve the
    # bridge AFTER deciding whether a runner was injected, and let an
    # injected runner through without requiring the real default bridge
    # to exist on disk. A real call (runner=None, the module's own
    # _default_runner does the launching) still requires
    # DEFAULT_BRIDGE_PATH to exist exactly as before: a missing bridge is
    # still NO_DATA for a real call, never a subprocess aimed at nothing.
    call = runner if runner is not None else _default_runner
    argv, reason = _resolve_bridge(bridge, require_default_file=(runner is None))
    if argv is None:
        return (NO_DATA, reason)

    start = time.monotonic()
    returncode, stdout, stderr = call(argv + ["--decisions", "--model", "typesafe"], payload_text)
    elapsed = time.monotonic() - start

    if returncode is None:
        return (NO_DATA, "bridge could not be invoked: %s" % (stderr or "unknown reason"))
    if returncode != 0:
        detail = (stderr or stdout or "").strip()[:500]
        return (NO_DATA, "bridge exited %d: %s" % (returncode, detail))

    try:
        response = json.loads(stdout)
    except ValueError as exc:
        return (NO_DATA, "bridge output could not be parsed as JSON: %s" % exc)
    if not isinstance(response, dict):
        return (NO_DATA, "bridge output is not a JSON object")

    model = response.get("model")
    if not isinstance(model, str) or not model.startswith(MODEL_PREFIX):
        return (NO_DATA, "bridge reported model %r, expected one starting with %r"
                % (model, MODEL_PREFIX))

    answers = response.get("answers")
    if not isinstance(answers, dict):
        return (NO_DATA, "bridge output has no answers object")
    missing = sorted(qid for qid in questions if qid not in answers)
    if missing:
        return (NO_DATA, "bridge answered no decision for: %s" % ", ".join(missing))

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    total_cost = _finite_number(usage.get("cost"))
    cost_share = (total_cost / len(questions)) if total_cost is not None else None

    records = []
    for qid, question in questions.items():
        answer = answers[qid]
        if not isinstance(answer, dict):
            return (NO_DATA, "answer for %r is not an object" % (qid,))
        qtype = question["type"]
        if qtype not in answer:
            return (NO_DATA, "answer for %r has no %r value (question was typed %r)"
                    % (qid, qtype, qtype))
        value = answer[qtype]
        if qtype == "noul":
            # A noul answer IS a probability: not numeric (or not finite)
            # is not a softened case, it is the whole record unusable.
            probability = _finite_number(value)
            if probability is None:
                return (NO_DATA, "answer for %r has a non-numeric noul value: %r"
                        % (qid, value))
        elif qtype == "choice":
            # A legal choice answer is a JSON scalar (it is looked up as a
            # key into "criteria" and "probabilities"). A list or dict
            # here is not a real choice answer, not a crash to catch and
            # paper over with an unresolved probability: fail the batch.
            if isinstance(value, dict) or isinstance(value, list):
                return (NO_DATA, "answer for %r has a non-scalar choice value: %r"
                        % (qid, value))
            # Fix at the source (review 70268c3, C3): "unknown" and any
            # other answer outside the question's own declared option set
            # were both silently accepted here and acted on downstream.
            # The batch fails as a whole, same discipline as every other
            # cause in this table.
            criteria = question.get("criteria")
            if isinstance(criteria, dict) and value not in criteria:
                return (NO_DATA, "answer for %r (%r) is not one of the "
                        "question's declared criteria: %s"
                        % (qid, value, sorted(criteria)))
            probabilities = answer.get("probabilities")
            probabilities = probabilities if isinstance(probabilities, dict) else {}
            # The matching probability itself must be a real number too;
            # a malformed entry there leaves probability unset rather than
            # failing the whole batch over a field this module only ever
            # derives, never depends on for the answer itself.
            probability = _finite_number(probabilities.get(value))
        else:  # score: no probability concept
            # Same fix as choice, above (review 70268c3, C3), but only
            # when criteria is itself a declared list of levels (the shape
            # jev_registry.callable() produces for a score question); a
            # descriptive, non-list criteria has no enumerable levels to
            # check an answer against, and is left unvalidated exactly as
            # before this fix.
            criteria = question.get("criteria")
            if isinstance(criteria, list) and value not in criteria:
                return (NO_DATA, "answer for %r (%r) is not one of the "
                        "question's declared levels: %s" % (qid, value, criteria))
            probability = None
        records.append({
            "id": qid,
            "family": family,
            "type": qtype,
            "framing_hash": _framing_hash(question),
            "answer": value,
            "probability": probability,
            "confidence": answer.get("confidence"),
            "model": model,
            "cost_share": cost_share,
            "latency_seconds": elapsed,
        })
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ask Jev (typesafe/jev-1.13) a batch of typed decision "
                     "questions read as JSON {state, questions} from stdin."
    )
    parser.add_argument("--family", required=True,
                         help="tag carried on every returned record; never sent to Jev")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read()
    except OSError as exc:
        print("jev-decide: NO-DATA, could not read stdin: %s" % exc, file=sys.stderr)
        return EXIT_NO_DATA
    try:
        request = json.loads(raw)
    except ValueError as exc:
        print("jev-decide: NO-DATA, stdin is not valid JSON: %s" % exc, file=sys.stderr)
        return EXIT_NO_DATA
    if not isinstance(request, dict) or "questions" not in request:
        print("jev-decide: NO-DATA, stdin needs an object with 'state' and 'questions'",
              file=sys.stderr)
        return EXIT_NO_DATA

    result = decide(request.get("state"), request.get("questions"), args.family)
    if isinstance(result, tuple):
        status, reason = result
        print("jev-decide: %s, %s" % (status, reason), file=sys.stderr)
        return EXIT_NO_DATA

    for record in result:
        try:
            # allow_nan=False: a stray NaN/Infinity in a record (a "score"
            # answer this module does not itself numerically validate) is
            # caught here rather than printed as invalid JSON.
            line = json.dumps(record, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            print("jev-decide: NO-DATA, a decision record could not be "
                  "encoded as JSON: %s" % exc, file=sys.stderr)
            return EXIT_NO_DATA
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

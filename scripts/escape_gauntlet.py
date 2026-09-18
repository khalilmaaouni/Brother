#!/usr/bin/env python3
"""DOM-40.08: is every escape attempt in the corpus refused by a real boundary.

WHY THIS EXISTS. docs/plan/ORCH-1020-WBS.json names this row's deciding
property: every escape attempt in the corpus is refused by the boundary;
one that ever succeeded is a permanent case; an empty corpus is NO-DATA.
Nothing in this tree ran the estate's own hostile techniques against its
own boundary modules before tonight. This harness is that runner: it loads
each attempt from benchmarks/escape_gauntlet/corpus/, dispatches it to the
real boundary module the attempt names, and checks that the boundary's own
answer was REFUSE.

WHAT REFUSES. This harness decides nothing itself. Three boundary modules
already in this tree decide, unchanged, called exactly the way their own
public functions are documented: scripts/filesystem_enforcement.decide(),
scripts/network_policy.decide(), and scripts/process_containment.
cancel_process_tree() (composed with spawn_contained()). This mirrors
scripts/execution_boundary.py's own discipline of composing those modules
rather than re-deciding anything they already decide; this harness is not
a fourth decision-maker, only their adversary.

THE SCHEMA IS THE ONE SOURCE OF THE BOUNDARY LIST. SCHEMA.json's
properties.boundary.enum names the three boundary kinds this harness
knows; loaded once at import, never retyped, the same discipline
scripts/fixture_classes.py already applies to its own class list.

THE ROW'S OWN NOTE, why NO-DATA is the honest answer tonight: no real
attempt ships in this commit (see benchmarks/escape_gauntlet/README.md),
so corpus/ does not exist and gauntlet_report() returns NO-DATA rather
than a fabricated PASS. NO-DATA is never read as a pass here.

FAIL IS A REAL OUTCOME HERE, UNLIKE ITS SIBLING ROW. DOM-30.02's harness
(fixture_classes.py) never returns FAIL, because a missing fixture class
is absence of evidence, not a wrong answer. This harness is different: an
attempt the boundary ALLOWS is not absence of evidence, it is a boundary
that failed exactly the question it exists to answer. That is scored FAIL,
never folded into NO-DATA, so an escape can never be read as "nothing
measured yet."

PROCESS ATTEMPTS NEVER RUN A RAW COMMAND FROM THE CORPUS. A corpus file's
call.technique_id selects an entry in PROCESS_TECHNIQUES, a fixed registry
of already-written implementations below; the corpus can choose among them
by name but can never inject an arbitrary command into a real run. Each
technique creates its own temporary sandbox directory, spawns only
processes it starts itself (via process_containment.spawn_contained, never
bare subprocess.Popen, matching that module's own contract), and cleans up
after itself in a finally block, mirroring the pattern already proven in
scripts/test_process_containment.py. Filesystem attempts likewise touch
only a fresh temporary sandbox this harness creates per run, never a real
path (filesystem_enforcement.decide() itself never writes to disk).
Network attempts never make a real network call: network_policy.decide()
is a pure decision function, by its own module docstring.

Python 3.9 floor, standard library only, no network, no real file mutation
outside a temporary sandbox this module creates and removes itself.
"""

import argparse
import collections
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import filesystem_enforcement
import network_policy
import process_containment

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_MODULE_DIR)
SCHEMA_PATH = os.path.join(
    _REPO_ROOT, "benchmarks", "escape_gauntlet", "SCHEMA.json")
DEFAULT_CORPUS_ROOT = os.path.join(
    _REPO_ROOT, "benchmarks", "escape_gauntlet", "corpus")


class EscapeCorpusError(Exception):
    """The corpus under corpus/ contains something that claims to be an
    attempt and is not a usable one: bad JSON, a missing required field,
    an id that does not match its own filename, a duplicate id, or an
    unrecognised boundary. Never caught and silently skipped by
    scan_corpus(); a defect here must stop the run, not quietly shrink
    it."""


class UnknownTechnique(Exception):
    """A process attempt named a technique_id that PROCESS_TECHNIQUES does
    not have. Raised rather than treated as an automatic pass or an
    automatic escape: an attempt this harness cannot actually run answers
    neither question."""


def _load_schema(schema_path):
    try:
        with open(schema_path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except OSError as exc:
        raise RuntimeError(
            "escape_gauntlet: cannot read schema at %r: %s" %
            (schema_path, exc))
    except ValueError as exc:
        raise RuntimeError(
            "escape_gauntlet: schema at %r is not valid JSON: %s" %
            (schema_path, exc))
    try:
        boundaries = tuple(schema["properties"]["boundary"]["enum"])
        required = tuple(schema["required"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "escape_gauntlet: schema at %r is missing the boundary enum "
            "or the required-field list: %s" % (schema_path, exc))
    if not boundaries:
        raise RuntimeError(
            "escape_gauntlet: schema at %r declares an empty boundary "
            "enum" % (schema_path,))
    return boundaries, required


BOUNDARIES, _REQUIRED_FIELDS = _load_schema(SCHEMA_PATH)
BOUNDARIES = frozenset(BOUNDARIES)

AttemptRecord = collections.namedtuple(
    "AttemptRecord", "attempt_id boundary technique call note")

AttemptResult = collections.namedtuple(
    "AttemptResult", "attempt_id boundary refused detail")


# ---------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------

def _read_manifest(manifest_path):
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise EscapeCorpusError(
            "%s: could not read manifest: %s" % (manifest_path, exc))
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise EscapeCorpusError(
            "%s: not valid JSON: %s" % (manifest_path, exc))
    if not isinstance(manifest, dict):
        raise EscapeCorpusError(
            "%s: manifest must be a JSON object, got %s" %
            (manifest_path, type(manifest).__name__))
    return manifest


def _validate_manifest(manifest, manifest_path, expected_id):
    for field in _REQUIRED_FIELDS:
        if field not in manifest:
            raise EscapeCorpusError(
                "%s: missing required field %r" % (manifest_path, field))
    attempt_id = manifest["id"]
    if attempt_id != expected_id:
        raise EscapeCorpusError(
            "%s: id %r does not match its own filename stem %r" %
            (manifest_path, attempt_id, expected_id))
    boundary = manifest["boundary"]
    if boundary not in BOUNDARIES:
        raise EscapeCorpusError(
            "%s: boundary %r is not one of the schema's known boundaries: "
            "%s" % (manifest_path, boundary, sorted(BOUNDARIES)))
    technique = manifest["technique"]
    if not isinstance(technique, str) or not technique.strip():
        raise EscapeCorpusError(
            "%s: technique must be a non-blank string" % (manifest_path,))
    call = manifest["call"]
    if not isinstance(call, dict):
        raise EscapeCorpusError(
            "%s: call must be a JSON object" % (manifest_path,))
    return attempt_id, boundary, technique, call


def scan_corpus(corpus_root=None):
    """Return a tuple of AttemptRecord, one per *.json file directly under
    corpus_root, sorted by attempt_id for a deterministic order.

    A missing corpus_root, or one that exists but is empty, returns an
    empty tuple: the honest "no attempts yet" state, not a defect.
    corpus_root existing with anything else (a non-.json entry, a
    subdirectory, invalid JSON, a missing field, an id/filename mismatch,
    a duplicate id, an unrecognised boundary) raises EscapeCorpusError;
    see the module docstring for why this is never silently skipped.
    """
    root = corpus_root if corpus_root is not None else DEFAULT_CORPUS_ROOT
    if not os.path.isdir(root):
        return ()
    records = []
    seen_ids = set()
    for name in sorted(os.listdir(root)):
        entry_path = os.path.join(root, name)
        if not os.path.isfile(entry_path) or not name.endswith(".json"):
            raise EscapeCorpusError(
                "%s: corpus/ must contain only *.json attempt files, found "
                "an entry that is not one" % (entry_path,))
        expected_id = name[:-len(".json")]
        manifest = _read_manifest(entry_path)
        attempt_id, boundary, technique, call = _validate_manifest(
            manifest, entry_path, expected_id)
        if attempt_id in seen_ids:
            raise EscapeCorpusError(
                "%s: duplicate attempt id %r" % (entry_path, attempt_id))
        seen_ids.add(attempt_id)
        records.append(AttemptRecord(
            attempt_id=attempt_id, boundary=boundary, technique=technique,
            call=call, note=manifest.get("note", "")))
    return tuple(records)


# ---------------------------------------------------------------------
# Filesystem attempts: resolved entirely inside a temporary sandbox
# ---------------------------------------------------------------------

def _run_filesystem_attempt(record, sandbox_root):
    call = record.call
    roots_rel = call.get("roots", {})
    if not isinstance(roots_rel, dict):
        raise EscapeCorpusError(
            "%s: filesystem call.roots must be a JSON object" %
            (record.attempt_id,))
    if "path" not in call:
        raise EscapeCorpusError(
            "%s: filesystem call is missing 'path'" % (record.attempt_id,))
    action = call.get("action", "write")

    abs_roots = {}
    for category, rel_list in roots_rel.items():
        if not isinstance(rel_list, list):
            raise EscapeCorpusError(
                "%s: filesystem call.roots[%r] must be a list" %
                (record.attempt_id, category))
        abs_list = []
        for rel in rel_list:
            declared = os.path.join(sandbox_root, rel)
            os.makedirs(declared, exist_ok=True)
            abs_list.append(declared)
        abs_roots[category] = abs_list

    target = os.path.join(sandbox_root, call["path"])
    verdict = filesystem_enforcement.decide(target, abs_roots, action=action)
    refused = not verdict.allowed
    return refused, "%s: %s" % (verdict.decision, verdict.reason)


# ---------------------------------------------------------------------
# Network attempts: decide() makes no real network call, no sandbox needed
# ---------------------------------------------------------------------

def _run_network_attempt(record):
    call = record.call
    if "host" not in call or "config" not in call:
        raise EscapeCorpusError(
            "%s: network call needs 'host' and 'config'" %
            (record.attempt_id,))
    verdict = network_policy.decide(
        call["host"], call["config"], high_autonomy=call.get("high_autonomy"))
    refused = not verdict.allowed
    return refused, "%s: %s" % (verdict.decision, verdict.reason)


# ---------------------------------------------------------------------
# Process attempts: a fixed, closed registry of sandboxed techniques.
# A corpus file selects one by name; it never supplies a raw command.
# ---------------------------------------------------------------------

def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _technique_detached_setsid_grandchild(sandbox_dir, params):
    """Spawn a root process (its own session) whose shell backgrounds a
    python grandchild that detaches with os.setsid() before signalling a
    flag file. The escape being attempted: survive cancel_process_tree()
    by leaving the original process group before the tree is cancelled.
    Refused means cancel_process_tree() finds and kills it anyway; this is
    the same technique scripts/test_process_containment.py already proves
    scripts/process_containment.py handles, run here through the gauntlet
    instead."""
    sleep_for = int(params.get("sleep_for", 20))
    pidfile = os.path.join(sandbox_dir, "grandchild.pid")
    flagfile = os.path.join(sandbox_dir, "detached.flag")
    detach_code = (
        "import os, time; os.setsid(); open(%r, 'w').close(); "
        "time.sleep(%d)" % (flagfile, sleep_for))
    shell = "%s -c %s & echo $! > %s; sleep %d" % (
        shlex.quote(sys.executable), shlex.quote(detach_code),
        shlex.quote(pidfile), sleep_for)
    root = process_containment.spawn_contained(["/bin/sh", "-c", shell])
    grandchild_pid = None
    try:
        if not _wait_for(lambda: os.path.exists(pidfile) and
                          _read_text(pidfile).strip()):
            raise EscapeCorpusError(
                "detached_setsid_grandchild: grandchild never wrote its "
                "pidfile")
        grandchild_pid = int(_read_text(pidfile).strip())
        if not _wait_for(lambda: os.path.exists(flagfile)):
            raise EscapeCorpusError(
                "detached_setsid_grandchild: grandchild never signalled "
                "os.setsid() completion")

        result = process_containment.cancel_process_tree(
            root.pid, grace=1.0, kill_grace=0.5)
        grandchild_dead = _wait_for(
            lambda: not process_containment._pid_alive(grandchild_pid))
        refused = bool(result.verified and grandchild_dead)
        detail = "verified=%s all_dead=%s grandchild_dead=%s alive=%s" % (
            result.verified, result.all_dead, grandchild_dead, result.alive)
        return refused, detail
    finally:
        if root.poll() is None:
            root.kill()
        try:
            root.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # already sent SIGKILL above; nothing further to do here
        if grandchild_pid is not None and process_containment._pid_alive(
                grandchild_pid):
            try:
                os.kill(grandchild_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


PROCESS_TECHNIQUES = {
    "detached_setsid_grandchild": _technique_detached_setsid_grandchild,
}


def _run_process_attempt(record):
    call = record.call
    technique_id = call.get("technique_id")
    if technique_id not in PROCESS_TECHNIQUES:
        raise UnknownTechnique(
            "%s: process call.technique_id %r is not in PROCESS_TECHNIQUES "
            "(%s)" % (record.attempt_id, technique_id,
                      sorted(PROCESS_TECHNIQUES)))
    impl = PROCESS_TECHNIQUES[technique_id]
    sandbox_dir = tempfile.mkdtemp(prefix="escape-gauntlet-process-")
    try:
        return impl(sandbox_dir, call)
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)


# ---------------------------------------------------------------------
# Dispatch and reporting
# ---------------------------------------------------------------------

def run_attempt(record, sandbox_root):
    """Run one AttemptRecord against its named boundary. Returns an
    AttemptResult. Raises on a boundary this module has never heard of
    (BOUNDARIES already guarantees record.boundary is one of the three
    known kinds, since scan_corpus validated it) or on a malformed call
    payload for that boundary; never silently reports a malformed attempt
    as refused, which would hide the defect inside a passing count."""
    if record.boundary == "filesystem":
        refused, detail = _run_filesystem_attempt(record, sandbox_root)
    elif record.boundary == "network":
        refused, detail = _run_network_attempt(record)
    elif record.boundary == "process":
        refused, detail = _run_process_attempt(record)
    else:
        # Unreachable while BOUNDARIES matches this if/elif chain; kept as
        # a loud failure rather than a silent fall-through if the schema
        # ever adds a fourth boundary this dispatcher has not learned yet.
        raise ValueError("unhandled boundary: %r" % (record.boundary,))
    return AttemptResult(attempt_id=record.attempt_id,
                          boundary=record.boundary, refused=refused,
                          detail=detail)


def gauntlet_report(corpus_root=None, sandbox_root=None):
    """Return a dict describing whether every attempt in the corpus was
    refused.

    {
      "results": {attempt_id: AttemptResult, ...},
      "escaped": (attempt_id, ...),      # sorted; attempts the boundary allowed
      "verdict": "PASS", "FAIL" or "NO-DATA",
      "reason": str,
    }

    verdict is NO-DATA when the corpus is empty (nothing to measure).
    verdict is FAIL when the corpus is non-empty and at least one attempt
    escaped: an escape is a wrong answer, not an absence of evidence, so
    it is never folded into NO-DATA. verdict is PASS only when the corpus
    is non-empty and every attempt was refused.
    """
    records = scan_corpus(corpus_root)
    if not records:
        return {
            "results": {},
            "escaped": (),
            "verdict": "NO-DATA",
            "reason": "corpus is empty; nothing was attempted",
        }

    own_sandbox = sandbox_root is None
    sandbox = sandbox_root if sandbox_root is not None else tempfile.mkdtemp(
        prefix="escape-gauntlet-fs-")
    try:
        results = {}
        for record in records:
            results[record.attempt_id] = run_attempt(record, sandbox)
    finally:
        if own_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)

    escaped = tuple(sorted(
        attempt_id for attempt_id, result in results.items()
        if not result.refused))
    if escaped:
        verdict = "FAIL"
        reason = "escaped: %s" % (", ".join(escaped),)
    else:
        verdict = "PASS"
        reason = "all %d attempts refused" % (len(records),)
    return {
        "results": results,
        "escaped": escaped,
        "verdict": verdict,
        "reason": reason,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--corpus-root", default=None,
        help="override the corpus directory (default: "
             "benchmarks/escape_gauntlet/corpus)")
    args = ap.parse_args(argv)

    try:
        report = gauntlet_report(args.corpus_root)
    except (EscapeCorpusError, UnknownTechnique) as exc:
        print("escape_gauntlet: CANNOT-DECIDE, corpus defect: %s" % (exc,))
        return 2

    for attempt_id in sorted(report["results"]):
        result = report["results"][attempt_id]
        state = "refused" if result.refused else "ESCAPED"
        print("escape_gauntlet: %-12s %-10s %s (%s)" % (
            attempt_id, result.boundary, state, result.detail))
    print("escape_gauntlet: %s, %s" % (report["verdict"], report["reason"]))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

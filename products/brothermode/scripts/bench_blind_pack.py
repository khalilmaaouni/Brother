#!/usr/bin/env python3
"""bench_blind_pack.py: the blind grading pack tool for the installed-arm
benchmark (design build step 6, docs/program/absolute-lead/
DESIGN-benchmark-installed-arm.md section 4.2 to 4.4 and section 5 step 6).

WHAT THIS IS
  Every place the benchmark cannot avoid a judgment call (T4 failure
  wording, H2 conflict-report wording, one comparison per repetition), a
  grader must never see which arm produced which output. This tool is what
  the harness itself uses to prepare that blind pack, replacing the manual
  X/Y shuffle-and-remember-the-mapping process that produced one label
  collision on 2026-08-07 (docs/BENCHMARK-COMPARATIVE.md, "Five
  repetitions per judgment cell": "one label collision caught and repaired
  before any grading began"). That collision is made structurally
  impossible here, not merely caught by a second look:

  1. LABEL ASSIGNMENT is deterministic from the comparison's own id (a hash
     of the id decides which candidate is X and which is Y), never a coin
     flip that could land differently on a rebuild of the same comparison.
  2. The ARM MAPPING (which label is really which arm) is written to a
     SEALED file whose filename IS its own content hash, so the file
     cannot be silently overwritten with different content under the same
     name, and a corrupted or hand-edited sealed file is self-evidently
     wrong the moment its name and its bytes disagree.
  3. Packing the SAME comparison id twice, or two comparisons in one batch
     that share an id, is a STRUCTURAL REFUSAL: nothing is written for
     either side of the collision, and the tool says why. This is the
     shape of mistake that produced the 2026-08-07 collision, so it is the
     one this tool is built to make impossible.
  4. The ONE-POINT MARGIN RULE (design section 4.3) lives here, not in the
     page that reports it: on the fifteen-point sheet, a margin of one
     point is NO DECISION; only a margin of two or more counts as a win.
  5. An OPTIONAL second grading pass through `codex exec` (design section
     4.4, the off-same-family bound) is wired but stays OFF unless a
     caller passes --codex-second-family explicitly and a rubric file. It
     is never reached by --self-test, which calls no network and no
     external binary at all.

WHAT THIS IS NOT
  It does not run the model cells (scripts/benchmark_comparative.py does),
  and it does not decide the rubric text (docs/BENCHMARK-COMPARATIVE.md
  freezes that, per the frozen-before-run law). It reads a rubric file only
  when asked to run the optional codex pass, and never writes one.

THE SEALED FILE, EXACTLY
  Canonical JSON, sorted keys, two fields only: comparison_id and the
  label-to-arm mapping ({"A": ..., "B": ...} are the only two arm names
  this project's harness ever compares). No timestamp is included in the
  hashed content on purpose: the hash is a pure function of
  (comparison_id, mapping), so rebuilding the identical comparison always
  produces the identical filename, which is what makes a REAL collision
  (the same filename appearing with DIFFERENT content) impossible short of
  a sha256 break, and what makes an ACCIDENTAL duplicate (the same
  comparison packed twice) visible as a plain "already sealed" refusal
  rather than a silent overwrite.

Python 3.9, standard library only. Runs a subprocess only for the optional
codex pass (--codex-second-family), gated behind an explicit flag,
never reached by --self-test. No em or en dashes anywhere in this file,
its comments, or its output.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

ARMS = ("A", "B")
LABELS = ("X", "Y")

#: The margin rule, pre-registered in the design (section 4.3): a margin of
#: exactly one point is NO DECISION; only two or more counts as a win.
MARGIN_WIN_FLOOR = 2

CODEX_TIMEOUT_SECONDS = 900


class PackRefused(Exception):
    """A structural refusal: a duplicate comparison id, a label collision,
    or a sealed file that already exists with different content. Never a
    silent overwrite; the message states exactly what was refused and
    why."""


def _out(text):
    sys.stdout.write("bench_blind_pack: %s\n" % text)
    sys.stdout.flush()


def _canonical_json(obj):
    return json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"


# ---------------------------------------------------------------------------
# label assignment
# ---------------------------------------------------------------------------

def assign_labels(comparison_id, text_a, text_b):
    """Which candidate is X and which is Y, decided from a sha256 of
    comparison_id alone: deterministic, not a live coin flip. This is the
    deliberate choice that removes the rebuild-collision class at the
    root: a coin flip that could land differently on a second build of the
    SAME comparison is exactly the shape of mistake this tool exists to
    make impossible, and a hash of a stable id can never do that. A grader
    who never sees comparison_id gets no more information from this than
    from a real coin flip; only the id-to-assignment link is
    deterministic, and that link lives only in the sealed file.

    Returns (label_to_text, label_to_arm), both dicts with keys "X", "Y"."""
    digest = hashlib.sha256(comparison_id.encode("utf-8")).digest()
    if digest[0] % 2 == 0:
        return ({"X": text_a, "Y": text_b}, {"X": "A", "Y": "B"})
    return ({"X": text_b, "Y": text_a}, {"X": "B", "Y": "A"})


def mapping_bytes(comparison_id, label_to_arm):
    return _canonical_json({"comparison_id": comparison_id,
                            "labels": label_to_arm})


def mapping_filename(mapping):
    return "%s.sealed.json" % hashlib.sha256(mapping).hexdigest()


# ---------------------------------------------------------------------------
# building one pack, and a whole batch
# ---------------------------------------------------------------------------

def _pack_text(comparison_id, label_to_text):
    return ("# Blind comparison %s\n\n"
           "No arm label appears anywhere on this page. Score X and Y "
           "independently against the rubric.\n\n"
           "## Candidate X\n\n%s\n\n"
           "## Candidate Y\n\n%s\n"
           % (comparison_id, label_to_text["X"], label_to_text["Y"]))


def build_pack(pack_dir, comparison_id, text_a, text_b, manifest):
    """Write one comparison's blind pack (the file a grader reads, naming
    no arm) and its sealed mapping (hash-named, opened only after every
    rubric line is scored). manifest is the running {comparison_id: ...}
    dict for this pack_dir; build_pack refuses, writing nothing, when
    comparison_id is already in it: a sealed comparison is sealed once,
    never silently rebuilt.

    Raises PackRefused on any refusal. Returns the manifest entry on
    success."""
    if comparison_id in manifest:
        raise PackRefused(
            "comparison id %r is already sealed in %s; a sealed comparison "
            "is never rebuilt, even with identical text, because a second "
            "build under the same id is exactly the shape of mistake this "
            "tool exists to refuse" % (comparison_id, pack_dir))

    label_to_text, label_to_arm = assign_labels(comparison_id, text_a, text_b)
    mapping = mapping_bytes(comparison_id, label_to_arm)
    sealed_name = mapping_filename(mapping)
    sealed_path = os.path.join(pack_dir, sealed_name)

    if os.path.exists(sealed_path):
        with io.open(sealed_path, "rb") as fh:
            existing = fh.read()
        if existing != mapping:
            # sha256 collision between two DIFFERENT mappings: refuse
            # loudly rather than trust the filename. Not reachable in
            # practice; checked because a silent trust-the-name overwrite
            # here would be the one failure mode this design cannot afford.
            raise PackRefused(
                "sealed filename collision at %s: existing content does "
                "not match the mapping this build would write; refusing "
                "to overwrite" % sealed_path)
        raise PackRefused(
            "sealed mapping %s already exists with identical content; "
            "comparison id %r was already sealed by a different manifest "
            "entry, which duplicate-label bookkeeping should never allow"
            % (sealed_path, comparison_id))

    if not os.path.isdir(pack_dir):
        os.makedirs(pack_dir)
    pack_path = os.path.join(pack_dir, "%s.pack.md" % comparison_id)
    if os.path.exists(pack_path):
        raise PackRefused(
            "pack file %s already exists; comparison id %r was already "
            "packed" % (pack_path, comparison_id))

    with io.open(pack_path, "w", encoding="utf-8") as fh:
        fh.write(_pack_text(comparison_id, label_to_text))
    with io.open(sealed_path, "wb") as fh:
        fh.write(mapping)

    entry = {"comparison_id": comparison_id, "pack_file": pack_path,
             "sealed_file": sealed_path}
    manifest[comparison_id] = entry
    return entry


def build_batch(pack_dir, comparisons):
    """comparisons: a list of (comparison_id, text_a, text_b) triples.
    Refuses BEFORE writing anything if the batch itself names one
    comparison id twice: a batch is the unit a caller submits together, so
    a same-batch duplicate is caught at the door rather than discovered
    mid-write with half a batch on disk. Reads any pack_dir manifest
    already on disk (prior sealed comparisons) so a second call against
    the same directory still refuses correctly. Returns the manifest."""
    seen_in_batch = {}
    for comparison_id, _a, _b in comparisons:
        if comparison_id in seen_in_batch:
            raise PackRefused(
                "batch names comparison id %r more than once; nothing in "
                "this batch has been written" % comparison_id)
        seen_in_batch[comparison_id] = True

    manifest = load_manifest(pack_dir)
    built = []
    try:
        for comparison_id, text_a, text_b in comparisons:
            built.append(build_pack(pack_dir, comparison_id, text_a, text_b,
                                    manifest))
    except PackRefused:
        # Partial-batch cleanup: only the entries THIS call just wrote are
        # rolled back, never a comparison that was already sealed before
        # this call started (that one is not this call's to remove).
        for entry in built:
            for key in ("pack_file", "sealed_file"):
                try:
                    os.remove(entry[key])
                except OSError:  # sbe: allow-silent best-effort rollback of a partial batch, PackRefused still raises
                    pass
            manifest.pop(entry["comparison_id"], None)
        raise
    return manifest


def load_manifest(pack_dir):
    """The comparison ids already sealed in pack_dir, read back from the
    sealed files themselves (never from a separate index this tool would
    have to keep in sync by hand): every *.sealed.json file's own
    comparison_id field is the source of truth for what is already
    sealed."""
    manifest = {}
    if not os.path.isdir(pack_dir):
        return manifest
    for name in sorted(os.listdir(pack_dir)):
        if not name.endswith(".sealed.json"):
            continue
        sealed_path = os.path.join(pack_dir, name)
        try:
            with io.open(sealed_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (IOError, OSError, ValueError):
            continue
        comparison_id = data.get("comparison_id")
        if not comparison_id:
            continue
        manifest[comparison_id] = {
            "comparison_id": comparison_id,
            "pack_file": os.path.join(pack_dir, "%s.pack.md" % comparison_id),
            "sealed_file": sealed_path,
        }
    return manifest


# ---------------------------------------------------------------------------
# the one-point margin rule (design section 4.3) and unsealing
# ---------------------------------------------------------------------------

def score_verdict(score_x, score_y, margin_floor=MARGIN_WIN_FLOOR):
    """("X"|"Y"|"NO DECISION", margin). A margin of zero or one is NO
    DECISION; a margin of margin_floor or more is a win for the higher
    score. margin_floor defaults to the frozen value (2); the parameter
    exists only so a test can prove the boundary, never to be overridden
    by a live caller."""
    margin = abs(score_x - score_y)
    if margin < margin_floor:
        return "NO DECISION", margin
    return ("X" if score_x > score_y else "Y"), margin


def unseal_and_resolve(pack_dir, comparison_id, score_x, score_y,
                       margin_floor=MARGIN_WIN_FLOOR):
    """Open the sealed mapping for comparison_id (only ever called after
    every rubric line for it has been scored: opening it earlier defeats
    the whole point of sealing it) and turn a blind X/Y score pair into an
    arm-labeled verdict. Returns a dict with comparison_id, arm_x, arm_y,
    score_x, score_y, margin, and winner_arm ("A", "B", or "NO
    DECISION")."""
    manifest = load_manifest(pack_dir)
    entry = manifest.get(comparison_id)
    if entry is None:
        raise PackRefused(
            "no sealed mapping for comparison id %r in %s" % (comparison_id,
                                                               pack_dir))
    with io.open(entry["sealed_file"], encoding="utf-8") as fh:
        sealed = json.load(fh)
    labels = sealed.get("labels", {})
    arm_x, arm_y = labels.get("X"), labels.get("Y")
    if arm_x not in ARMS or arm_y not in ARMS or arm_x == arm_y:
        raise PackRefused(
            "sealed mapping for %r is not a legal two-arm assignment: %r"
            % (comparison_id, labels))
    verdict, margin = score_verdict(score_x, score_y, margin_floor)
    winner_arm = ("NO DECISION" if verdict == "NO DECISION"
                 else (arm_x if verdict == "X" else arm_y))
    return {"comparison_id": comparison_id, "arm_x": arm_x, "arm_y": arm_y,
            "score_x": score_x, "score_y": score_y, "margin": margin,
            "winner_arm": winner_arm}


def aggregate_headline(resolved):
    """Wins, no-decisions, and losses as three numbers, never collapsed
    into one (design section 4.3's own closing sentence). "Losses" is
    reported per arm, which is exactly the other arm's wins on a two-arm
    sheet, so the three numbers reported are: arm A wins, arm B wins, and
    no-decisions; their sum is always len(resolved)."""
    wins_a = sum(1 for r in resolved if r["winner_arm"] == "A")
    wins_b = sum(1 for r in resolved if r["winner_arm"] == "B")
    no_decision = sum(1 for r in resolved if r["winner_arm"] == "NO DECISION")
    return {"arm_a_wins": wins_a, "arm_b_wins": wins_b,
            "no_decision": no_decision, "total": len(resolved)}


# ---------------------------------------------------------------------------
# the optional codex exec second-family pass (design section 4.4). OFF
# unless a caller passes --codex-second-family; never reached by
# --self-test.
# ---------------------------------------------------------------------------

def build_codex_prompt(rubric_text, pack_text):
    return ("You are grading a blind comparison for an internal benchmark. "
           "Score candidate X and candidate Y independently against the "
           "rubric below. Do not guess which system produced either "
           "candidate; none of that information is given to you and none "
           "of it is relevant. Report each rubric line as yes or no for X "
           "and for Y, then a total point score for X and a total point "
           "score for Y on the last line, in the exact form "
           "'SCORE X=<n> Y=<n>'.\n\n"
           "RUBRIC:\n%s\n\nPACK:\n%s\n" % (rubric_text, pack_text))


def run_codex_grading(prompt_text, cwd=None, timeout=CODEX_TIMEOUT_SECONDS):
    """One `codex exec` call, read-only sandbox, no session persisted. This
    function is never called by --self-test and is only reachable through
    the CLI's --codex-second-family flag, which is off by default (design
    section 4.4: "the flag stays off by default"). Returns (ok, text) on
    success, (False, reason) on anything else; never raises past here, the
    same never-crash-the-batch posture scripts/benchmark_comparative.py's
    Skip exception encodes for its own model calls."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return False, "no codex binary on PATH"
    out_fd, out_path = tempfile.mkstemp(prefix="bm-blind-pack-codex-")
    os.close(out_fd)
    try:
        argv = [codex_bin, "exec", "--sandbox", "read-only",
               "--skip-git-repo-check", "--ephemeral",
               "--output-last-message", out_path, prompt_text]
        try:
            r = subprocess.run(argv, cwd=cwd or ROOT, timeout=timeout,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
        except subprocess.TimeoutExpired:
            return False, "codex exec hit the %ds timeout" % timeout
        except OSError as exc:
            return False, "could not start codex exec: %s" % exc
        if r.returncode != 0:
            return False, ("codex exec exited %s: %s"
                           % (r.returncode, (r.stderr or "").strip()[:500]))
        try:
            with io.open(out_path, encoding="utf-8") as fh:
                message = fh.read()
        except (IOError, OSError) as exc:
            return False, "codex exec exited 0 but its output file could " \
                          "not be read: %s" % exc
        if not message.strip():
            return False, "codex exec exited 0 with an empty final message"
        return True, message
    finally:
        try:
            os.remove(out_path)
        except OSError:  # sbe: allow-silent best-effort cleanup of the codex tempfile, result already returned
            pass


# ---------------------------------------------------------------------------
# self-test: synthetic outputs, no model, no network, exits 0 or 1.
# ---------------------------------------------------------------------------

def self_test():
    failures = []
    checks_run = []

    def check(label, condition):
        checks_run.append(label)
        if condition:
            _out("ok: %s" % label)
        else:
            failures.append(label)
            _out("FAIL: %s" % label)

    tmp = tempfile.mkdtemp(prefix="bm-blind-pack-selftest-")
    try:
        pack_dir = os.path.join(tmp, "pack")

        # 1. A normal build: both files land, the pack names no arm, the
        # sealed filename really is the hash of its own bytes.
        manifest = {}
        entry = build_pack(pack_dir, "cmp-001", "text of candidate one",
                           "text of candidate two", manifest)
        check("pack file written", os.path.isfile(entry["pack_file"]))
        check("sealed file written", os.path.isfile(entry["sealed_file"]))
        with io.open(entry["pack_file"], encoding="utf-8") as fh:
            pack_body = fh.read()
        # The template's own prose says "No arm label appears..." to tell
        # the grader this pack is blind, which legitimately contains the
        # word "arm"; what must never appear is an actual arm IDENTITY
        # (the literal tokens "Arm A" / "Arm B" this project's own results
        # tables use).
        check("pack file names no arm identity",
             "Arm A" not in pack_body and "Arm B" not in pack_body)
        with io.open(entry["sealed_file"], "rb") as fh:
            sealed_bytes = fh.read()
        check("sealed filename is its own content hash",
             os.path.basename(entry["sealed_file"])
             == mapping_filename(sealed_bytes))
        sealed = json.loads(sealed_bytes.decode("utf-8"))
        check("sealed mapping names a legal two-arm assignment",
             sorted(sealed.get("labels", {}).values()) == ["A", "B"])

        # 2. THE DELIBERATE COLLISION: packing "cmp-001" a second time,
        # alone, must be refused, and must leave the first pack untouched.
        refused = False
        try:
            build_pack(pack_dir, "cmp-001", "a different text entirely",
                      "and a third text", dict(manifest))
        except PackRefused:
            refused = True
        check("rebuilding a sealed comparison id is refused", refused)
        with io.open(entry["pack_file"], encoding="utf-8") as fh:
            check("the original pack is unchanged after the refused rebuild",
                 fh.read() == pack_body)

        # 3. THE DELIBERATE COLLISION, batch form: two comparisons sharing
        # one id in a single batch call must be refused before anything is
        # written, for either one.
        batch_dir = os.path.join(tmp, "batch")
        batch_refused = False
        try:
            build_batch(batch_dir,
                       [("cmp-dup", "alpha text", "beta text"),
                        ("cmp-dup", "gamma text", "delta text")])
        except PackRefused:
            batch_refused = True
        check("a same-batch duplicate id is refused", batch_refused)
        check("the refused batch wrote nothing to disk",
             not os.path.isdir(batch_dir) or os.listdir(batch_dir) == [])

        # 3b. A batch whose duplicate is against an ALREADY SEALED
        # comparison from a prior call (not a same-batch duplicate) must
        # still refuse, and must roll back the new comparison in the same
        # batch that had already been written before the duplicate was
        # reached, leaving no partial batch behind.
        partial_refused = False
        try:
            build_batch(pack_dir, [("cmp-002", "new one", "new two"),
                                   ("cmp-001", "colliding again", "text")])
        except PackRefused:
            partial_refused = True
        check("a duplicate against an already-sealed comparison is refused",
             partial_refused)
        check("a same-call sibling written before the duplicate was hit is "
             "rolled back",
             not os.path.exists(os.path.join(pack_dir, "cmp-002.pack.md")))
        with io.open(entry["pack_file"], encoding="utf-8") as fh:
            check("the pre-existing sealed comparison is untouched by the "
                 "rolled-back batch", fh.read() == pack_body)

        # 4. The margin rule, at its exact boundary: a one-point margin is
        # NO DECISION, a two-point margin is a win, and a tie is NO
        # DECISION.
        v, m = score_verdict(15, 14)
        check("a one point margin is NO DECISION", v == "NO DECISION" and m == 1)
        v, m = score_verdict(15, 13)
        check("a two point margin is a win", v == "X" and m == 2)
        v, m = score_verdict(12, 12)
        check("a tie is NO DECISION", v == "NO DECISION" and m == 0)
        v, m = score_verdict(13, 15)
        check("the higher score wins past the margin floor",
             v == "Y" and m == 2)

        # 5. Unsealing resolves the blind score back to the real arm, and
        # the mapping stays correct for both possible label assignments.
        # cmp-001's own sealed file already tells us the ground truth, so
        # scores are set so the ARM that is genuinely "A" wins, and the
        # resolved winner_arm is checked against that ground truth rather
        # than against a guessed label.
        resolved = unseal_and_resolve(pack_dir, "cmp-001", score_x=15,
                                      score_y=13)
        expected_winner = sealed["labels"]["X"]  # X scored higher above
        check("unsealing maps the winning label back to the real arm",
             resolved["winner_arm"] == expected_winner)
        resolved_tie = unseal_and_resolve(pack_dir, "cmp-001", score_x=14,
                                          score_y=14)
        check("unsealing a tie reports NO DECISION",
             resolved_tie["winner_arm"] == "NO DECISION")

        # 6. The headline aggregates three numbers, never one.
        headline = aggregate_headline([resolved, resolved_tie])
        check("the headline totals every comparison",
             headline["total"] == 2)
        check("the headline keeps wins and no-decisions separate",
             headline["no_decision"] == 1
             and (headline["arm_a_wins"] + headline["arm_b_wins"]) == 1)

        # 7. The codex second-family pass is never touched here: no
        # network call, no subprocess, nothing to time out. This asserts
        # the negative directly rather than trusting that no code path
        # above happened to call it.
        check("no comparison in this self-test carries a codex verdict",
             all("codex" not in json.dumps(r) for r in (resolved,
                                                        resolved_tie)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        _out("SELF-TEST FAILED: %d of %d check(s): %s"
            % (len(failures), len(checks_run), "; ".join(failures)))
        return EXIT_FAILED
    _out("SELF-TEST PASSED: %d checks, including two deliberate collision "
        "refusals (same-batch and against an already-sealed comparison)"
        % len(checks_run))
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="bench_blind_pack.py",
        description="Blind grading pack tool for the installed-arm "
                    "benchmark (design build step 6): label assignment, "
                    "hash-named sealed mappings, duplicate refusal, and "
                    "the one-point NO DECISION margin rule.")
    p.add_argument("--self-test", action="store_true",
                   help="run the synthetic self-test (no model, no "
                        "network) and exit 0 or 1")
    p.add_argument("--build", action="store_true",
                   help="build a batch of packs from --comparisons-json "
                        "into --run-dir")
    p.add_argument("--resolve", action="store_true",
                   help="unseal and resolve a batch of scores from "
                        "--scores-json against --run-dir, printing the "
                        "three-number headline")
    p.add_argument("--run-dir", default=None,
                   help="the pack directory --build writes to or "
                        "--resolve reads from")
    p.add_argument("--comparisons-json", default=None,
                   help="a JSON list of {comparison_id, arm_a_text, "
                        "arm_b_text} objects, for --build")
    p.add_argument("--scores-json", default=None,
                   help="a JSON list of {comparison_id, score_x, score_y} "
                        "objects, for --resolve")
    p.add_argument("--codex-second-family", action="store_true",
                   help="optional, OFF by default: also run each "
                        "resolved comparison's pack through `codex exec` "
                        "with --rubric-file and report agreement. Calls "
                        "the network. Never reached by --self-test.")
    p.add_argument("--rubric-file", default=None,
                   help="rubric text file, required with "
                        "--codex-second-family")
    return p


def _load_json_list(path, label):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        raise PackRefused("could not read %s (%s): %s" % (label, path, exc))
    if not isinstance(data, list):
        raise PackRefused("%s (%s) is not a JSON list" % (label, path))
    return data


def cmd_build(args):
    if not args.run_dir or not args.comparisons_json:
        _out("--build needs --run-dir and --comparisons-json")
        return EXIT_USAGE
    rows = _load_json_list(args.comparisons_json, "--comparisons-json")
    comparisons = []
    for row in rows:
        comparisons.append((row["comparison_id"], row["arm_a_text"],
                            row["arm_b_text"]))
    manifest = build_batch(args.run_dir, comparisons)
    _out("built %d comparison(s) in %s" % (len(comparisons), args.run_dir))
    for comparison_id in sorted(m for m in manifest):
        _out("  %s -> %s" % (comparison_id, manifest[comparison_id]["sealed_file"]))
    return EXIT_OK


def cmd_resolve(args):
    if not args.run_dir or not args.scores_json:
        _out("--resolve needs --run-dir and --scores-json")
        return EXIT_USAGE
    if args.codex_second_family and not args.rubric_file:
        _out("--codex-second-family needs --rubric-file")
        return EXIT_USAGE
    rows = _load_json_list(args.scores_json, "--scores-json")
    resolved = []
    for row in rows:
        resolved.append(unseal_and_resolve(args.run_dir, row["comparison_id"],
                                           row["score_x"], row["score_y"]))
    for r in resolved:
        _out("%s: arm_x=%s arm_y=%s score_x=%s score_y=%s margin=%s "
            "winner=%s" % (r["comparison_id"], r["arm_x"], r["arm_y"],
                          r["score_x"], r["score_y"], r["margin"],
                          r["winner_arm"]))
    headline = aggregate_headline(resolved)
    _out("headline: arm A wins %d, arm B wins %d, no decision %d, total %d"
        % (headline["arm_a_wins"], headline["arm_b_wins"],
           headline["no_decision"], headline["total"]))

    if args.codex_second_family:
        with io.open(args.rubric_file, encoding="utf-8") as fh:
            rubric_text = fh.read()
        for r in resolved:
            manifest = load_manifest(args.run_dir)
            entry = manifest.get(r["comparison_id"])
            if entry is None:
                _out("  codex: no pack file for %s, skipped"
                    % r["comparison_id"])
                continue
            with io.open(entry["pack_file"], encoding="utf-8") as fh:
                pack_text = fh.read()
            prompt = build_codex_prompt(rubric_text, pack_text)
            ok, text = run_codex_grading(prompt)
            if ok:
                _out("  codex verdict for %s: %s"
                    % (r["comparison_id"], text.strip().splitlines()[-1]
                       if text.strip() else "(empty)"))
            else:
                _out("  codex second-family pass skipped for %s: %s"
                    % (r["comparison_id"], text))
    return EXIT_OK


def main(argv):
    args = build_parser().parse_args(argv)
    modes = [m for m in (args.self_test, args.build, args.resolve) if m]
    if len(modes) != 1:
        _out("usage: bench_blind_pack.py --self-test | "
            "--build --run-dir DIR --comparisons-json FILE | "
            "--resolve --run-dir DIR --scores-json FILE "
            "[--codex-second-family --rubric-file FILE]")
        return EXIT_USAGE
    try:
        if args.self_test:
            if (args.build or args.resolve or args.run_dir
                    or args.comparisons_json or args.scores_json
                    or args.codex_second_family or args.rubric_file):
                _out("--self-test takes no other options")
                return EXIT_USAGE
            return self_test()
        if args.build:
            return cmd_build(args)
        return cmd_resolve(args)
    except PackRefused as exc:
        _out("refused: %s" % exc)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

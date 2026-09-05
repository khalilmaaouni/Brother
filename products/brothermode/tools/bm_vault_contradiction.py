#!/usr/bin/env python3
"""bm_vault_contradiction: the precedence law for two vault lessons that disagree.

WHY THIS EXISTS (founder steering, 2026-09-05, sections 6 to 11). Today two
opposite lessons can both be surfaced at recall, each annotated CONTRADICTS,
and nothing decides which one an engineering session should act on. Left
alone, whichever lesson the reader happens to trust (often: whichever sounds
newer, or whichever comes up more) silently drives the work. That is the
defect this module closes.

THE LAW, an explicit invariant, never inferred from timestamps, similarity,
or recall frequency:

    CURRENT DIRECT EVIDENCE
    > CURRENT AUTHORITATIVE PROJECT STATE
    > CURRENT VERIFIED VAULT KNOWLEDGE
    > VALID HISTORICAL KNOWLEDGE
    > UNVERIFIED RECALL

Newer is not truer. A lesson written yesterday does not outrank one written
last month; only evidence and authority move a conflict. Where current
evidence cannot resolve a conflict, THIS MODULE APPLIES NEITHER LESSON: it
withholds both, or escalates, and says why. It never applies an unresolved
contradiction.

THE MODEL, matching the brief this row was written against: detect a
conflict (find_conflicts), locate current evidence for each side (the
caller's own evidence_probe, since only the caller knows how to check a
test, a grep, or a decision record), ask whether that evidence resolves it
(resolve). Yes: pick the winner, apply it, record why. No: withhold both,
or escalate when the evidence is not merely silent but actually ambiguous
(more than one side's evidence currently holds at once).

MINIMUM METADATA, read from a note's own frontmatter, the same fenced-block
convention every sibling contract module in this family already reads
(bm_vault_graph.py, bm_vault_triage.py, bm_vault.py): lesson_id, statement,
scope, source, source_type, verified_against, verified_at, supersedes,
status, contradicts, evidence_locator. A field absent from the frontmatter
reads as NO_DATA, the literal string "NO-DATA": never fabricated, never
guessed from the body prose. `contradicts:` and `supersedes:` are the
existing [[wikilink]] edges bm_vault.py and bm_vault_graph.py already read
and write (this module never mints one); reused here under the spellings
`supersedes` and `supersedes_by`... no: this module only ever READS
`supersedes` and `contradicts`, the two spellings the estate's other vault
lanes (scripts/vault_correct.py, bm_vault_temporal.py) already share, so
every lane agrees on the field names without a shared import between them.

WHAT THIS MODULE NEVER DOES: mint a contradicts: or supersedes: edge, write
to a note, touch the vault index, or run the Memory Recurrence gauntlet's
frozen scoring rule (benchmarks/gauntlets/memory-recurrence.json,
scripts/gauntlet_memory_recurrence.py) -- report-only, exactly like its
sibling bm_vault_survivorship.py.

The frontmatter parser duplicates bm_vault_triage.py's own `_frontmatter`
and FRONTMATTER_FIELD_RE rather than importing them, the stated convention
of every sibling contract module in this family (bm_vault_triage.py's own
docstring: "so no module's behaviour shifts when a sibling changes").

Python 3.9, standard library only. No vault writes, ever.
"""
import argparse
import importlib.util
import os
import re
import subprocess
import sys
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))

NO_DATA = "NO-DATA"

# Per-lesson evidence verdicts, returned by an evidence_probe(lesson) call.
HOLDS = "HOLDS"
FAILS = "FAILS"
NO_DATA_EVIDENCE = NO_DATA

# Conflict-set verdicts, returned by resolve().
APPLY = "APPLY"
WITHHOLD = "WITHHOLD"
ESCALATE = "ESCALATE"

FIELDS = ("lesson_id", "statement", "scope", "source", "source_type",
          "verified_against", "verified_at", "supersedes", "status",
          "contradicts", "evidence_locator")

# Same shape as bm_vault_triage.py's own FRONTMATTER_FIELD_RE: plain
# `key: value` frontmatter lines, line-oriented.
FRONTMATTER_FIELD_RE = re.compile(r"^([A-Za-z][A-Za-z_-]*):\s*(.*?)\s*$", re.M)
# Same shape as bm_vault.py's own WIKILINK: [[Target]] or [[Target|Alias]].
WIKILINK = re.compile(r"\[\[([^\]|]+)")

Decision = namedtuple("Decision", ("verdict", "winner", "why"))


def _frontmatter(text):
    """The text between the opening and closing --- fences, or "" outside
    one. Duplicated from bm_vault_triage.py's own helper of the same name,
    per that module's own stated convention (see the module docstring
    above): every sibling contract module reads the vault on its own."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _links(raw):
    """[[A]], [[B#Section]] -> ["A", "B"]: wikilink targets, anchors
    stripped, same resolution bm_vault.py's own _rebuild_contradictions
    applies to a contradicts: field. Blank or absent raw -> []."""
    out = []
    for target in WIKILINK.findall(raw or ""):
        target = target.strip()
        if "#" in target:
            target = target.split("#", 1)[0].strip()
        if target.lower().endswith(".md"):
            target = target[:-3]
        if target:
            out.append(target)
    return out


def _lesson_from_frontmatter(path, front_text):
    raw = dict(FRONTMATTER_FIELD_RE.findall(front_text))
    lesson = {"path": path}
    for field in FIELDS:
        if field in ("contradicts", "supersedes"):
            continue
        lesson[field] = raw.get(field, "").strip() or NO_DATA
    lesson["contradicts"] = _links(raw.get("contradicts", ""))
    lesson["supersedes"] = _links(raw.get("supersedes", ""))
    if lesson["lesson_id"] == NO_DATA:
        # A note missing an explicit lesson_id: still nameable, so
        # find_conflicts and recall_verdict can refer to it by the same
        # stem a contradicts: [[wikilink]] would use.
        lesson["lesson_id"] = os.path.splitext(os.path.basename(path))[0]
    return lesson


def parse_lesson(path):
    """One lesson dict (every FIELDS key present, missing ones NO_DATA), or
    None when the file cannot be read: an explicit failure path, never a
    raised traceback over a vault this module does not own."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return _lesson_from_frontmatter(path, _frontmatter(text))


def _lesson_from_row(path, body):
    """The same lesson shape as parse_lesson, from a body string already in
    memory (bm_vault.py's recall path already read it via its own SELECT;
    re-reading a file recall just read would be a second read of the same
    disk this estate's own spend law argues against)."""
    return _lesson_from_frontmatter(path, _frontmatter(body or ""))


def _load_triage():
    """tools/bm_vault_triage.py loaded by path, the same defensive pattern
    bm_vault_survivorship.py's own _load_sibling uses. None when the file is
    absent or fails to import: find_conflicts then skips the detected
    (opposite-statement) path and reports only declared contradicts: edges,
    a degraded but honest result, never a crash."""
    path = os.path.join(HERE, "bm_vault_triage.py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("bm_vault_triage_for_contradiction", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional sibling module load; caller degrades to declared-only detection
        return None


def find_conflicts(notes):
    """[[lesson_a, lesson_b], ...]: every pair in `notes` this resolver must
    adjudicate before either can drive engineering. A pair qualifies two
    ways, both scoped to matching `scope` (two lessons in different scopes
    are never a conflict here, whatever their contradicts: field or wording
    says: scope is the boundary this resolver trusts):

      declared  one lesson's own contradicts: field names the other, the
                bm_vault_graph.py edge (symmetric: either side is enough).

      detected  the existing triage detector (bm_vault_triage.py, VB6-05)
                already classifies the pair's `statement` text as a
                same-scope CONTRADICTION rather than a SCOPED difference.
                Reused, never re-implemented: this module resolves
                conflicts, it does not hunt for new ones.
    """
    by_key = {}
    for n in notes:
        by_key.setdefault(n["lesson_id"], n)
        stem = os.path.splitext(os.path.basename(n["path"]))[0]
        by_key.setdefault(stem, n)

    conflicts = []
    seen = set()

    for n in notes:
        for target in n["contradicts"]:
            other = by_key.get(target)
            if other is None or other is n or n["scope"] != other["scope"]:
                continue
            key = tuple(sorted((n["path"], other["path"])))
            if key in seen:
                continue
            seen.add(key)
            conflicts.append([n, other])

    triage = _load_triage()
    if triage is not None:
        for i in range(len(notes)):
            for j in range(i + 1, len(notes)):
                a, b = notes[i], notes[j]
                if a["scope"] == NO_DATA or a["scope"] != b["scope"]:
                    continue
                key = tuple(sorted((a["path"], b["path"])))
                if key in seen:
                    continue
                sa, va = triage.split_subject_value(a["statement"])
                sb, vb = triage.split_subject_value(b["statement"])
                if sa is None or sb is None or sa != sb or va == vb:
                    continue
                seen.add(key)
                conflicts.append([a, b])
    return conflicts


def make_evidence_probe(base_dir):
    """The default evidence_probe(lesson) -> HOLDS/FAILS/NO_DATA_EVIDENCE,
    reading the locator syntax this resolver understands, every path
    resolved under base_dir. Never fabricates authority: a locator this
    function cannot parse, or a lesson with no evidence_locator at all,
    reports NO_DATA_EVIDENCE rather than guessing either side is true.

    Recognized locator shapes, one scheme prefix each:
      path:<relpath>            HOLDS iff the path exists.
      grep:<relpath>:<pattern>  HOLDS iff <pattern> is a substring of the
                                 file's current text; a missing file FAILS
                                 (a stale anchor, not an unknown one).
      decision:<relpath>#<anc>  HOLDS iff the file exists and contains
                                 <anc>; a missing file or missing anchor
                                 FAILS the same way a stale grep does.
      test:<relpath>            HOLDS iff running that file with this
                                 interpreter exits 0; a missing file FAILS.

    Called fresh every time: nothing here is cached, so evidence that
    changed on disk since an earlier recall is re-read, never replayed.
    """
    def probe(lesson):
        locator = lesson.get("evidence_locator", NO_DATA)
        if not locator or locator == NO_DATA:
            return NO_DATA_EVIDENCE
        kind, sep, rest = locator.partition(":")
        if not sep or not rest:
            return NO_DATA_EVIDENCE
        if kind == "path":
            return HOLDS if os.path.exists(os.path.join(base_dir, rest)) else FAILS
        if kind == "grep":
            relpath, sep2, pattern = rest.partition(":")
            if not sep2:
                return NO_DATA_EVIDENCE
            try:
                with open(os.path.join(base_dir, relpath), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
            except OSError:
                return FAILS  # the anchor is gone: stale, not unknown
            return HOLDS if pattern in text else FAILS
        if kind == "decision":
            relpath, _, anchor = rest.partition("#")
            try:
                with open(os.path.join(base_dir, relpath), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
            except OSError:
                return FAILS
            if anchor and anchor not in text:
                return FAILS
            return HOLDS
        if kind == "test":
            target = os.path.join(base_dir, rest)
            if not os.path.exists(target):
                return FAILS
            try:
                proc = subprocess.run([sys.executable, target], cwd=base_dir,
                                       capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                return FAILS
            return HOLDS if proc.returncode == 0 else FAILS
        return NO_DATA_EVIDENCE
    return probe


def resolve(conflict_set, evidence_probe):
    """One Decision (APPLY/WITHHOLD/ESCALATE) for one conflict pair, per THE
    LAW at the top of this file. evidence_probe(lesson) is called fresh for
    every lesson on every call (never cached here), so evidence that
    changed since an earlier recall is re-evaluated rather than replayed.

    Tier 1, current direct evidence: exactly one lesson's own
    evidence_locator currently holds -> APPLY that lesson. More than one
    holds at once (genuinely ambiguous, or actively contradictory,
    evidence) -> ESCALATE, never a silent pick between them.

    Tiers 2 to 5, current authoritative project state down to unverified
    recall, used only when evidence is silent (nothing holds): a lesson
    marked status: verified outranks one that is not (CURRENT VERIFIED
    VAULT KNOWLEDGE beats UNVERIFIED RECALL, whatever either was written
    more recently); a lesson not marked superseded outranks one that is
    (VALID HISTORICAL KNOWLEDGE never outranks current vault status).

    Nothing above distinguishes the lessons -> WITHHOLD both: no automatic
    application of an unresolved contradiction.
    """
    lessons = list(conflict_set)
    probed = [(lesson, evidence_probe(lesson)) for lesson in lessons]
    holding = [lesson for lesson, verdict in probed if verdict == HOLDS]

    if len(holding) == 1:
        winner = holding[0]
        loser_ids = [l["lesson_id"] for l in lessons if l is not winner]
        return Decision(APPLY, winner, (
            "current direct evidence at %s holds for %s and does not "
            "currently hold for %s"
            % (winner["evidence_locator"], winner["lesson_id"],
               ", ".join(loser_ids))))
    if len(holding) > 1:
        return Decision(ESCALATE, None, (
            "current evidence holds for more than one lesson in this "
            "same-scope conflict at once (%s); a resolver never picks "
            "between two evidenced sides on its own, this needs a human "
            "or an authoritative decision"
            % ", ".join(l["lesson_id"] for l in holding)))

    verified = [l for l in lessons if l["status"] == "verified"]
    if len(verified) == 1:
        winner = verified[0]
        return Decision(APPLY, winner, (
            "no lesson's evidence_locator currently holds; %s is CURRENT "
            "VERIFIED VAULT KNOWLEDGE and outranks the other lesson's "
            "unverified recall, regardless of which is newer"
            % winner["lesson_id"]))

    superseded = [l for l in lessons if l["status"] == "superseded"]
    current = [l for l in lessons if l["status"] != "superseded"]
    if superseded and len(current) == 1:
        winner = current[0]
        return Decision(APPLY, winner, (
            "no lesson's evidence_locator currently holds; %s is not "
            "superseded while the rest of this conflict is, so valid "
            "historical knowledge yields to current vault status"
            % winner["lesson_id"]))

    return Decision(WITHHOLD, None, (
        "no current evidence resolves this conflict, and vault status "
        "(verified/superseded) does not distinguish the lessons either; "
        "both are withheld rather than applied on recency or similarity"))


RECALL_NO_DATA = "NO_DATA"

# The two fields that carry this resolver's own signal. A note written
# under the vault's OLDER schema (name/description/authority/valid_from,
# the VB-12 fields bm_vault.py already annotates) has neither: it never
# opted into this law, so recall_verdict must not start withholding notes
# that have always been served, plainly annotated, since before this row.
_SIGNAL_FIELDS = ("evidence_locator", "status")


def _has_signal(lesson):
    return any(lesson.get(field, NO_DATA) != NO_DATA for field in _SIGNAL_FIELDS)


def recall_verdict(con, row, conflicting_titles, base_dir=None):
    """(verdict, winner_title_or_None, why): the smallest hook bm_vault.py's
    own recall path (_print_hits) needs at the exact point it already
    detects a contradiction (_contradicted_by). `row` carries the note's
    own path and body columns already SELECTed by that caller; the
    contradiction edge itself is not re-detected here, only resolved -- an
    already-established contradicts: edge is exactly what
    bm_vault.py._contradicted_by already computed, this function's job
    starts after that.

    "APPLY", row["title"], why           row's own lesson is the winner.
    "APPLY", <other title>, why          another lesson in the conflict
                                          won; the caller withholds row.
    "WITHHOLD"/"ESCALATE", None, why     neither side applies.
    "NO_DATA", None, why                 neither side of this conflict
                                          carries any of this resolver's own
                                          metadata (evidence_locator or
                                          status): an older note that never
                                          opted into this law, served
                                          exactly as it always was, plain
                                          CONTRADICTS annotation and all.
                                          This is NOT a resolution and the
                                          caller must never read it as one.

    Never raises to the caller: bm_vault.py wraps this call itself (the
    same degrade-on-exception posture every sibling contract module here
    keeps), but an unreadable conflicting row is turned into an explicit
    WITHHOLD (its own NO-DATA) rather than an exception, since "the other
    note could not be read" is exactly the kind of missing evidence this
    law says to withhold on, not crash on.
    """
    self_lesson = _lesson_from_row(row["path"], row["body"])
    other_lessons = []
    for title in conflicting_titles:
        other = con.execute("SELECT path, body FROM notes WHERE title = ? LIMIT 1",
                            (title,)).fetchone()
        if not other:
            return WITHHOLD, None, (
                "contradicts %s, whose own row could not be read for "
                "resolution (NO-DATA); withheld rather than applied "
                "unresolved" % title)
        other_lessons.append((title, _lesson_from_row(other["path"], other["body"])))

    if not _has_signal(self_lesson) and not any(_has_signal(l) for _, l in other_lessons):
        return RECALL_NO_DATA, None, (
            "neither lesson in this conflict carries an evidence_locator "
            "or a status field; this note never opted into the "
            "contradiction resolver's metadata, so it is served exactly "
            "as before rather than withheld under a law it never declared "
            "into")

    probe = make_evidence_probe(base_dir or os.getcwd())
    best_for_self = (APPLY, row["title"], "no contradicting note resolved to a row")
    for title, other_lesson in other_lessons:
        decision = resolve([self_lesson, other_lesson], probe)
        if decision.verdict != APPLY:
            return decision.verdict, None, decision.why
        if decision.winner is not self_lesson:
            return APPLY, title, decision.why
        best_for_self = (APPLY, row["title"], decision.why)
    return best_for_self


def _walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _parse_as_of(as_of):
    if not as_of:
        return None
    try:
        return tuple(int(p) for p in as_of.split("-")[:3])
    except ValueError:
        return None


def _within_as_of(lesson, cutoff):
    if cutoff is None or lesson["verified_at"] == NO_DATA:
        return True
    try:
        parts = tuple(int(p) for p in lesson["verified_at"].split("-")[:3])
    except ValueError:
        return True  # an unparsable date is not evidence either way
    return parts <= cutoff


def cmd_resolve(vault, as_of, base_dir):
    if not os.path.isdir(vault):
        print("bm_vault_contradiction: NO-DATA, no readable vault at %r" % vault,
              file=sys.stderr)
        return 2
    cutoff = _parse_as_of(as_of)
    notes = []
    unreadable = 0
    for path in _walk(vault):
        lesson = parse_lesson(path)
        if lesson is None:
            unreadable += 1
            continue
        if _within_as_of(lesson, cutoff):
            notes.append(lesson)
    print("vault: %s" % vault)
    if unreadable:
        print("unreadable files skipped: %d" % unreadable)
    conflicts = find_conflicts(notes)
    print("conflicts to resolve: %d" % len(conflicts))
    if not conflicts:
        print("clean: no same-scope contradictions to resolve")
        return 0
    probe = make_evidence_probe(base_dir or vault)
    any_unresolved = False
    for a, b in conflicts:
        decision = resolve([a, b], probe)
        if decision.verdict == APPLY:
            loser = b if decision.winner is a else a
            print("APPLY %s over %s (scope=%s): %s" % (
                decision.winner["lesson_id"], loser["lesson_id"], a["scope"], decision.why))
        else:
            any_unresolved = True
            print("%s %s and %s (scope=%s): %s" % (
                decision.verdict, a["lesson_id"], b["lesson_id"], a["scope"], decision.why))
    return 1 if any_unresolved else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("resolve",))
    ap.add_argument("vault_dir")
    ap.add_argument("--as-of", dest="as_of", default=None,
                     help="YYYY-MM-DD: ignore a lesson verified after this date")
    ap.add_argument("--base-dir", dest="base_dir", default=None,
                     help="root for evidence_locator paths, default the vault dir itself")
    args = ap.parse_args(argv)
    return cmd_resolve(args.vault_dir, args.as_of, args.base_dir)


if __name__ == "__main__":
    sys.exit(main())

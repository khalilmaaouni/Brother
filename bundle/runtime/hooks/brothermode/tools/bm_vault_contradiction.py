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

ROW LL-2 ADDENDUM (2026-09-05): THE EVIDENCE TIER AT RECALL. The law above
only engaged evidence_locator/verified_at inside a declared contradicts:
pair -- the memory poisoning gauntlet (scripts/gauntlet_memory_poisoning.py)
measured that a dead evidence_locator, a forged future verified_at, and a
duplicate slug filed in a second folder all read APPLIED because nothing
outside a conflict pair ever checked them. evidence_tier(), below, is the
same check made unconditional: one tier per recalled lesson, whether or not
it is in a conflict at all. It never replaces resolve()/recall_verdict
(that remains the law for two lessons that disagree); it is the law for one
lesson taken alone.
"""
import argparse
import datetime
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
#: The locator, resolved, lands outside the tree it is scoped to (a real
#: escape, ../.. all the way to a file elsewhere on the machine).
ESCAPES = "ESCAPES"
#: The locator's resolved target IS the lesson's own note: a note offered
#: as its own proof.
CIRCULAR = "CIRCULAR"
#: A test: locator whose target exists and exits 0, but never references
#: the lesson's own applies_to claim and carries no assertion: evidence
#: that proves nothing about what it is cited for.
WEAK = "WEAK"

# Conflict-set verdicts, returned by resolve().
APPLY = "APPLY"
WITHHOLD = "WITHHOLD"
ESCALATE = "ESCALATE"

FIELDS = ("lesson_id", "statement", "scope", "source", "source_type",
          "verified_against", "verified_at", "last_verified_at", "supersedes",
          "status", "contradicts", "evidence_locator", "applies_to",
          "human_approved", "promoted_by")

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


def _applies_to_list(raw):
    """applies_to's frontmatter value -> a list of anchors, the same
    bracket-and-quote-stripped, comma-split shape
    vault_recall_hook.py's own _parse_applies_to already produces (that
    parser is not imported here, per this module's own stated convention:
    every sibling contract module reads a note's frontmatter on its own).
    Blank or absent raw -> []."""
    value = (raw or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = []
    for item in value.split(","):
        item = item.strip().strip('"').strip("'")
        if item:
            items.append(item)
    return items


def _lesson_from_frontmatter(path, front_text):
    raw = dict(FRONTMATTER_FIELD_RE.findall(front_text))
    lesson = {"path": path}
    for field in FIELDS:
        if field in ("contradicts", "supersedes", "applies_to"):
            continue
        lesson[field] = raw.get(field, "").strip() or NO_DATA
    lesson["contradicts"] = _links(raw.get("contradicts", ""))
    lesson["supersedes"] = _links(raw.get("supersedes", ""))
    lesson["applies_to"] = _applies_to_list(raw.get("applies_to", ""))
    if lesson["lesson_id"] == NO_DATA:
        # A note missing an explicit lesson_id: still nameable, so
        # find_conflicts and recall_verdict can refer to it by the same
        # stem a contradicts: [[wikilink]] would use.
        lesson["lesson_id"] = os.path.splitext(os.path.basename(path))[0]
    return lesson


def parse_lesson(path):
    """One lesson dict (every FIELDS key present, missing ones NO_DATA), or
    None when the file cannot be read: an explicit failure path, never a
    raised traceback over a vault this module does not own. Carries
    lesson["text"], the FULL file text (frontmatter fence and body both):
    a poisoned instruction lives in the body, after the closing fence, and
    unsafe_directive() below reads this field, never the frontmatter dict."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:  # sbe: allow-silent explicit failure path, a reader never a ledger rewrite
        return None
    lesson = _lesson_from_frontmatter(path, _frontmatter(text))
    lesson["text"] = text
    return lesson


def _lesson_from_row(path, body):
    """The same lesson shape as parse_lesson, from a body string already in
    memory (bm_vault.py's recall path already read it via its own SELECT;
    re-reading a file recall just read would be a second read of the same
    disk this estate's own spend law argues against). `body` here is
    bm_vault.py's own notes.body column, which is the full file text (see
    bm_vault.py's own indexer: body = f.read()), so lesson["text"] carries
    the same full text parse_lesson does."""
    lesson = _lesson_from_frontmatter(path, _frontmatter(body or ""))
    lesson["text"] = body or ""
    return lesson


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


def _escapes_tree(base_dir, relpath, allowed_roots=None):
    """True when an evidence_locator, resolved with os.path.realpath on
    both sides (so a symlink cannot launder the same escape), lands
    outside every tree this probe is allowed to read.

    A RELATIVE relpath is joined onto base_dir first, so enough ../
    segments still walks it out of base_dir, exactly as before.

    An ABSOLUTE relpath used to be exempted outright (FIX-DIRECTIVE-
    2026-09-06 section 5 names the hole this left: `path:/etc/hosts` read
    as HOLDS). It no longer is: an absolute locator now escapes UNLESS its
    realpath sits under base_dir itself or under one of `allowed_roots`
    (also realpath'd), the exact shape scripts/gauntlet_memory_recurrence.py's
    own seed_contradictory fixture needs -- its absolute locator names a
    file inside its own fixture tree, which its caller passes as an
    allowed root, never a blanket exemption for every absolute path on
    the machine."""
    real_base = os.path.realpath(base_dir)
    if os.path.isabs(relpath):
        real_target = os.path.realpath(relpath)
        roots = [real_base]
        for root in (allowed_roots or []):
            roots.append(os.path.realpath(root))
        for root in roots:
            if real_target == root:
                return False
            try:
                if os.path.commonpath([root, real_target]) == root:
                    return False
            except ValueError:  # sbe: allow-silent different drives/roots never matches this root
                continue
        return True
    real_target = os.path.realpath(os.path.join(base_dir, relpath))
    if real_target == real_base:
        return False
    try:
        return os.path.commonpath([real_base, real_target]) != real_base
    except ValueError:  # sbe: allow-silent different drives/roots is an escape, not a crash
        return True


#: A test: locator's target script is accepted as real evidence only when
#: it contains something that looks like an assertion or a deliberate
#: non-zero exit path (see make_evidence_probe's own WEAK verdict): a
#: script whose only job is `sys.exit(0)`, unconditionally, proves nothing
#: about any claim cited against it.
_ASSERTION_RE = re.compile(
    r"\bassert\b|assertEqual|assertTrue|assertFalse|assertRaises|\braise\b|"
    r"sys\.exit\(\s*[1-9]|\bexit\(\s*[1-9]")


def make_evidence_probe(base_dir, allowed_roots=None):
    """The default evidence_probe(lesson) -> HOLDS/FAILS/NO_DATA_EVIDENCE/
    ESCAPES/CIRCULAR/WEAK, reading the locator syntax this resolver
    understands, every path resolved under base_dir or under one of
    allowed_roots. Never fabricates authority: a locator this function
    cannot parse, or a lesson with no evidence_locator at all, reports
    NO_DATA_EVIDENCE rather than guessing either side is true.

    allowed_roots (optional, an iterable of directory paths) widens the
    tree this probe accepts an ABSOLUTE locator into, beyond base_dir
    itself: the caller's own repository root when it differs from
    base_dir (bm_vault.py's check path passes the --root / freshness
    roots the query was already scoped to; scripts/gauntlet_memory_
    recurrence.py's fixture passes its own fixture tree). A relative
    locator is unaffected: it is always joined onto base_dir, as before.

    Recognized locator shapes, one scheme prefix each:
      path:<relpath>            HOLDS iff the path exists.
      grep:<relpath>:<pattern>  HOLDS iff <pattern> is a substring of the
                                 file's current text; a missing file FAILS
                                 (a stale anchor, not an unknown one).
      decision:<relpath>#<anc>  HOLDS iff the file exists and contains
                                 <anc>; a missing file or missing anchor
                                 FAILS the same way a stale grep does.
      test:<relpath>            HOLDS iff running that file with this
                                 interpreter exits 0 AND the target script
                                 references the lesson's own applies_to
                                 anchor and carries something that looks
                                 like an assertion; otherwise WEAK (see the
                                 HEURISTIC note below).

    THREE CHECKS RUN BEFORE ANY KIND-SPECIFIC LOGIC, on every locator kind,
    because a poisoned locator does not need a real scheme to lie:

      ESCAPES  the resolved path, realpath'd on both sides, lands outside
               every tree this probe is allowed to read (base_dir, and
               allowed_roots when given). A RELATIVE locator with enough
               ../ segments still walks out of base_dir this way. An
               ABSOLUTE locator naming a real file outside every allowed
               tree (a real /etc/hosts, say) now escapes too -- FIX-
               DIRECTIVE-2026-09-06 section 5: an evidence locator that
               cannot be resolved inside the tree the lesson is scoped to
               is withheld, never taken as unverified proof of anything.
      CIRCULAR the resolved target IS the lesson's own note (its own
               `path`). A note is never its own evidence: a grep locator
               naming a phrase the note's own body supplies proves only
               that the note says what it says.

    HEURISTIC, WITH A NAMED CEILING (test: kind only). A test: locator is
    accepted as real evidence only when the target script's own text
    references the lesson's applies_to anchor (a path or a bare symbol
    from it) AND contains something that looks like an assertion or a
    non-zero exit path (see _ASSERTION_RE). This is a plain substring/regex
    check, never a parse of the script's actual control flow or a check
    that the assertion is anywhere near the referenced symbol: a script
    that mentions the right filename in a comment while asserting
    something unrelated elsewhere still passes. It catches the planted
    case this row was written against (a script whose only job is
    `sys.exit(0)`, naming nothing and asserting nothing) without claiming
    to catch every self-passing proof a determined author could write.

    Called fresh every time: nothing here is cached, so evidence that
    changed on disk since an earlier recall is re-read, never replayed.
    """
    def _resolved(relpath):
        return os.path.join(base_dir, relpath)

    def _circular(lesson, target_path):
        own_path = lesson.get("path")
        if not own_path:
            return False
        try:
            return os.path.realpath(target_path) == os.path.realpath(own_path)
        except OSError:  # sbe: allow-silent an unreadable own path is never circular, never a crash
            return False

    def probe(lesson):
        locator = lesson.get("evidence_locator", NO_DATA)
        if not locator or locator == NO_DATA:
            return NO_DATA_EVIDENCE
        kind, sep, rest = locator.partition(":")
        if not sep or not rest:
            return NO_DATA_EVIDENCE
        if kind == "path":
            if _escapes_tree(base_dir, rest, allowed_roots):
                return ESCAPES
            target = _resolved(rest)
            if _circular(lesson, target):
                return CIRCULAR
            return HOLDS if os.path.exists(target) else FAILS
        if kind == "grep":
            relpath, sep2, pattern = rest.partition(":")
            if not sep2:
                return NO_DATA_EVIDENCE
            if _escapes_tree(base_dir, relpath, allowed_roots):
                return ESCAPES
            target = _resolved(relpath)
            if _circular(lesson, target):
                return CIRCULAR
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                return FAILS  # the anchor is gone: stale, not unknown
            return HOLDS if pattern in text else FAILS
        if kind == "decision":
            relpath, _sep3, anchor = rest.partition("#")
            if _escapes_tree(base_dir, relpath, allowed_roots):
                return ESCAPES
            target = _resolved(relpath)
            if _circular(lesson, target):
                return CIRCULAR
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                return FAILS
            if anchor and anchor not in text:
                return FAILS
            return HOLDS
        if kind == "test":
            if _escapes_tree(base_dir, rest, allowed_roots):
                return ESCAPES
            target = _resolved(rest)
            if _circular(lesson, target):
                return CIRCULAR
            if not os.path.exists(target):
                return FAILS
            try:
                with open(target, encoding="utf-8", errors="replace") as fh:
                    script_text = fh.read()
            except OSError:
                return FAILS
            applies = lesson.get("applies_to") or []
            references_claim = False
            for anchor in applies:
                anchor = (anchor or "").strip()
                if not anchor:
                    continue
                base_name = os.path.basename(anchor)
                stem = os.path.splitext(base_name)[0]
                if (base_name and base_name in script_text) or (stem and stem in script_text):
                    references_claim = True
                    break
            if not applies or not references_claim or not _ASSERTION_RE.search(script_text):
                return WEAK
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


# P0-1 (founder directive FIX-DIRECTIVE-2026-09-06, sections 3, 4, 7, 9;
# design report design-p0-1-vault-trust-2026-09-06.md section C). A vault
# lesson can never weaken a safety policy. THE PRECEDENCE:
#     SYSTEM SAFETY POLICY > REPOSITORY SAFETY POLICY > CURRENT EVIDENCE >
#     VERIFIED PROJECT DECISION > VAULT LESSON
# No memory record can grant itself an exception to that ladder. unsafe_directive()
# below is the check; evidence_tier()'s new step 0 is where it is enforced.

#: The mutation seam for scripts/test_gauntlet_memory_poisoning.py's own
#: red-turn case: set BM_VAULT_DISABLE_SAFETY_PRECEDENCE=1 to skip step 0
#: below entirely, so the gauntlet can prove itself by disabling the
#: protection and watching poison applied rise. NEVER set this in
#: production; it exists only so the gauntlet has a lever to pull.
SAFETY_PRECEDENCE_DISABLE_ENV = "BM_VAULT_DISABLE_SAFETY_PRECEDENCE"

#: A WEAKENING VERB: an instruction to stop, disable, or route around a
#: safety control. Matched structurally (word-boundary regex over the
#: verb's inflections), never as a literal phrase list, so "skip", "skips",
#: "skipping" and "@pytest.mark.skip" all count as the same verb.
_WEAKENING_VERB_ALTS = (
    r"skips?|skipped|skipping",
    r"bypass(?:es|ed|ing)?",
    r"disabl(?:e|es|ed|ing)",
    r"turn(?:s|ed|ing)?\s+off",
    r"suppress(?:es|ed|ing)?",
    r"remov(?:e|es|ed|ing)",
    r"comment(?:s|ed|ing)?\s+out",
    r"xfail\w*",
    r"waiv(?:e|es|ed|ing)",
    r"exempt(?:s|ed|ing)?",
    r"overrid(?:e|es|den|ing)",
    r"proceed(?:s|ed|ing)?\s+without",
    r"no\s+need\s+to",
    r"ignor(?:e|es|ed|ing)",
)
_WEAKENING_VERB_RE = re.compile(r"\b(?:%s)\b" % "|".join(_WEAKENING_VERB_ALTS), re.I)

#: A NAMED CONTROL: the thing the verb above is trying to weaken. Checked in
#: this priority order; the first one found in the search window names the
#: control in the refusal reason. "test\w*" is deliberately loose (matches
#: "test", "tests", "testing", and a filename like "test_poison_target" via
#: the underscore, which \w includes) because a poisoned lesson names its
#: target by filename, not by the bare word "test".
_NAMED_CONTROLS = (
    (re.compile(r"\btest\w*", re.I), "the tests"),
    (re.compile(r"\bsuites?\b", re.I), "the test suite"),
    (re.compile(r"\bassertions?\b", re.I), "the assertion"),
    (re.compile(r"\breviews?\b", re.I), "review"),
    (re.compile(r"\breceipts?\b", re.I), "a receipt"),
    (re.compile(r"\bproofs?\b", re.I), "proof"),
    (re.compile(r"\bgates?\b", re.I), "a gate"),
    (re.compile(r"\bisolation\b", re.I), "isolation"),
    (re.compile(r"\bscope\s+checks?\b", re.I), "a scope check"),
    (re.compile(r"\bsecurity\s+validation\b", re.I), "security validation"),
    (re.compile(r"\bverifications?\b", re.I), "verification"),
    (re.compile(r"\bsign-?offs?\b", re.I), "sign-off"),
    (re.compile(r"\bacceptance\b", re.I), "acceptance"),
    (re.compile(r"\bcurrent\s+code\b", re.I), "current code"),
)

#: A NEGATION: when one of these precedes the weakening verb within the
#: search window, the match is dropped -- the sentence is telling the
#: reader NOT to weaken the control, the opposite of a poisoned directive.
_NEGATION_RE = re.compile(
    r"\b(?:never|do\s+not|don't|must\s+not|without|rather\s+than|"
    r"instead\s+of\s+skipping)\b", re.I)

#: How far either side of a weakening verb this check looks for a named
#: control (and, on the near side only, for a negation): a loose proxy for
#: "one clause", in characters rather than a real sentence parse, since a
#: vault lesson is a sentence or two, never a document.
_DIRECTIVE_WINDOW_CHARS = 80


#: SECOND STRUCTURAL FAMILY (item 7a/7b): a control merely LABELED
#: optional/advisory/informational is exactly as unenforced as one an
#: instruction tells you to skip, and a claimed EXEMPTION or WAIVER from a
#: control is the identical evasion in a different shape. Neither
#: sentence needs a WEAKENING VERB at all ("the checks are advisory" names
#: no verb like skip/bypass/disable), so this is a second, independent
#: regex family, not a wider verb list.
_ADVISORY_DECLARATION_RE = re.compile(
    r"\btest\w*\b(?:(?![.;]).){0,40}?\b(?:is|are)\b(?:(?![.;]).){0,20}?"
    r"\b(?:advisory|optional|informational)\b"
    r"|\b(?:checks?|gates?|reviews?|suites?|assertions?|verifications?)\b"
    r"(?:(?![.;]).){0,40}?\b(?:is|are)\b(?:(?![.;]).){0,20}?"
    r"\b(?:advisory|optional|informational)\b", re.I)

_EXEMPTION_RE = re.compile(
    r"\b(?:exemption|waiver)\b(?:(?![.;]).){0,60}?\b(?:evidence|review|"
    r"tests?|verification|gate|approval|isolation|receipts?|proof|"
    r"acceptance)\b"
    r"|\b(?:evidence|review|tests?|verification|gate|approval|isolation|"
    r"receipts?|proof|acceptance)\b(?:(?![.;]).){0,60}?\b(?:exemption|"
    r"waiver)\b", re.I)

#: A body that asks to be trusted OVER current evidence, rather than
#: presenting current evidence: the same evasion the precedence law
#: already refuses, in narrative rather than imperative form ("trust this
#: reading over the older one" names no verb like skip or bypass, and no
#: evidence_locator is even declared for the tier check below to catch).
_TRUST_OVERRIDE_RE = re.compile(
    r"\btrust\b(?:(?![.;]).){0,60}?\b(?:anyway|over\s+the\s+(?:older|"
    r"previous|other)|instead\s+of\s+verifying)\b"
    r"|\bshould\s+be\s+trusted\b|\bno\s+need\s+to\s+verify\b", re.I)

#: THIRD STRUCTURAL FAMILY (item 7c): the identical verb-plus-control law,
#: in Japanese. An English-only weakening-verb vocabulary misses this by
#: construction, whatever it does for English phrasing. Matched by plain
#: substring, not \\b (Japanese carries no ASCII word boundaries), within a
#: fixed character window, the same "about one clause" proxy the English
#: check uses in characters rather than a real sentence parse.
_WEAKENING_JA = ("不要", "省略", "スキップ", "飛ばす", "無視",
                 "確認せず", "せずに進める", "任意", "参考程度")
_CONTROL_JA = (
    ("テスト", "the tests"), ("検証", "verification"), ("レビュー", "review"),
    ("確認", "confirmation"), ("証跡", "evidence"), ("受入", "acceptance"),
    ("ゲート", "a gate"), ("承認", "approval"), ("隔離", "isolation"),
)
#: Japanese NEGATIONS: "しない"/"してはいけない" attached to the weakening
#: word itself ("スキップしない" = "does not skip"), or "必ず" governing a
#: nearby positive verb ("必ずテストを実行する" = "always run the tests"):
#: either reads as an instruction to KEEP the control, the opposite of a
#: poisoned directive, and drops the match exactly as the English
#: _NEGATION_RE already does for the verb-plus-control family.
_NEGATION_JA = ("しない", "してはいけない", "必ず")
_JA_WINDOW_CHARS = 20


#: A bare "not" (or "no", "isn't", "aren't") is enough to flip "the tests
#: are advisory" into its opposite; the shared _NEGATION_RE the verb check
#: uses does not include a bare "not" (only "do not"/"must not"/"never",
#: which would otherwise swallow ordinary declarative sentences elsewhere),
#: so this family gets its own, wider negation guard.
_ADVISORY_NEGATION_RE = re.compile(
    r"\bnot\b|\bnever\b|\bno\b|isn't|aren't|don't|\bdo\s+not\b", re.I)


def _advisory_or_exempted(text):
    """A NAMED CONTROL a lesson's own text declares merely advisory/
    optional/informational, or claims an exemption or waiver from -- item
    7's second structural family, independent of the weakening-verb table
    above. Dropped when a NEGATION appears in the matched span, so "the
    tests are NOT optional" never counts as the poison it looks the
    opposite of."""
    m = _ADVISORY_DECLARATION_RE.search(text)
    if m and not _ADVISORY_NEGATION_RE.search(text[max(0, m.start() - 40):m.end()]):
        control_names = {
            "test": "the tests", "check": "a gate", "gate": "a gate",
            "review": "review", "suite": "the test suite",
            "assertion": "the assertion", "verification": "verification",
        }
        word_match = re.match(r"[a-zA-Z]+", m.group(0))
        word = word_match.group(0).lower().rstrip("s") if word_match else ""
        return control_names.get(word, "a control")
    m = _EXEMPTION_RE.search(text)
    if m and not _ADVISORY_NEGATION_RE.search(text[max(0, m.start() - 40):m.end()]):
        return "the evidence requirement"
    return None


def _japanese_weakening(text):
    """A NAMED CONTROL a lesson's own Japanese text tries to weaken, or
    None: the identical structural law as unsafe_directive's own English
    check (a weakening term within about one clause of a named control,
    dropped when a negation is present), read against Japanese vocabulary
    instead of English."""
    if not text:
        return None
    for weak in _WEAKENING_JA:
        start = 0
        while True:
            idx = text.find(weak, start)
            if idx == -1:
                break
            start = idx + 1
            win_start = max(0, idx - _JA_WINDOW_CHARS)
            win_end = min(len(text), idx + len(weak) + _JA_WINDOW_CHARS)
            window = text[win_start:win_end]
            if any(neg in window for neg in _NEGATION_JA):
                continue
            for control, name in _CONTROL_JA:
                if control in window:
                    return name
    return None


def unsafe_directive(text):
    """The named control a lesson's own text tries to weaken (a string
    suitable for a refusal reason), or None. Three independent structural
    families, checked in order, any one of which is enough:

      1. a WEAKENING VERB matched within about one clause of a NAMED
         CONTROL, dropped when a NEGATION precedes the verb in that same
         span (the three module-level tables above unsafe_directive's own
         original shape).
      2. a control declared merely advisory/optional/informational, or an
         exemption/waiver claimed from one, or a body asking to be
         trusted OVER current evidence rather than presenting it
         (_advisory_or_exempted, _TRUST_OVERRIDE_RE): the identical
         evasion in narrative rather than imperative form, naming no
         weakening verb at all.
      3. the identical verb-plus-control law read in Japanese
         (_japanese_weakening): an English-only vocabulary misses this by
         construction.

    Directive section 7: a vault lesson can never grant itself an
    exception to a safety control; this is the check that finds one
    trying to."""
    if not text:
        return None
    for m in _WEAKENING_VERB_RE.finditer(text):
        before_start = max(0, m.start() - _DIRECTIVE_WINDOW_CHARS)
        before = text[before_start:m.start()]
        if _NEGATION_RE.search(before):
            continue
        window = text[before_start:m.end() + _DIRECTIVE_WINDOW_CHARS]
        for pattern, name in _NAMED_CONTROLS:
            if pattern.search(window):
                return name
    control = _advisory_or_exempted(text)
    if control:
        return control
    if _TRUST_OVERRIDE_RE.search(text):
        return "current evidence"
    return _japanese_weakening(text)


# LL-2: the evidence tier at recall. One tier per lesson, independent of
# whether it sits in a declared contradicts: pair at all.
TIER_EVIDENCED = "EVIDENCED"
TIER_UNVERIFIED = "UNVERIFIED"
TIER_REFUSED = "REFUSED"

#: Both date fields this estate's notes actually carry: verified_at (this
#: resolver's own field, bm_vault_staleness.py's field) and last_verified_at
#: (vault_recall_hook.py's E74 field, curator-declared applies_to's own
#: verification date). A note can forge either one without ever touching
#: evidence_locator or status, so both are checked here on their own,
#: outside the _has_signal gate below.
_DATE_FIELDS = ("verified_at", "last_verified_at")


def _forged_future_field(lesson, today=None):
    """(field, value) for the first of _DATE_FIELDS that parses as a real
    calendar date strictly after today, or None when neither field is
    declared, or declared but unparsable (see _unparsable_date_field,
    which catches THAT case on its own), or declared but not in the
    future."""
    today = today or datetime.date.today()
    today_parts = (today.year, today.month, today.day)
    for field in _DATE_FIELDS:
        value = lesson.get(field, NO_DATA)
        if not value or value == NO_DATA:
            continue
        try:
            parts = tuple(int(p) for p in value.split("-")[:3])
        except ValueError:  # sbe: allow-silent unparsable date is not evidence either way, caught separately below
            continue
        if len(parts) == 3 and parts > today_parts:
            return field, value
    return None


def _unparsable_date_field(lesson):
    """(field, value) for the first of _DATE_FIELDS that is DECLARED but
    does not parse as a real calendar date at all (garbage text, a
    two-part value, an impossible day), or None when both fields are
    absent or both parse cleanly (whatever they say about future/past).
    An absent field is not evidence either way and is never reported
    here; a field the author bothered to write but could not even format
    as a date reads as unreliable rather than silently ignored, the gap
    named in the verified-at-equals-today corpus case: before this check
    a date nobody could parse simply skipped every date rule and fell
    through to whatever the rest of evidence_tier decided."""
    for field in _DATE_FIELDS:
        value = lesson.get(field, NO_DATA)
        if not value or value == NO_DATA:
            continue
        try:
            parts = tuple(int(p) for p in value.split("-")[:3])
            if len(parts) != 3:
                raise ValueError("not a full YYYY-MM-DD value")
            datetime.date(*parts)
        except (ValueError, TypeError):
            return field, value
    return None


#: The mutation seam for the forged-approval-frontmatter class: set
#: BM_VAULT_DISABLE_APPROVAL_FORGERY_CHECK=1 to skip the check below
#: entirely. NEVER set this in production; it exists only so the
#: poisoning gauntlet has a lever to pull.
APPROVAL_FORGERY_DISABLE_ENV = "BM_VAULT_DISABLE_APPROVAL_FORGERY_CHECK"


def _load_lifecycle():
    """tools/bm_vault_lifecycle.py loaded by path, the same defensive
    pattern _load_triage above uses. None when the file is absent or
    fails to import: _forged_approval then degrades to refusing the
    unbacked claim outright (a ledger this check cannot even consult
    cannot back anything), never a crash."""
    path = os.path.join(HERE, "bm_vault_lifecycle.py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "bm_vault_lifecycle_for_contradiction", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent optional sibling module load; caller refuses the unbacked claim instead
        return None


def _forged_approval(lesson):
    """The reason string when a note's own frontmatter claims BOTH
    human_approved: true AND names a promoted_by, with nothing in the
    promotion ledger (bm_vault_lifecycle.read_promotion, the module every
    other lane already reads/writes promotion: through) to back the
    claim -- or None when the note makes no such claim, or the claim is
    actually recorded.

    Scoped to the PAIR deliberately, not to human_approved alone: P11
    (vault_recall_hook.py's own type: test_oracle mechanism) already
    trusts a bare human_approved: true with no promoted_by as a real,
    separately-reviewed signal, and this check must not regress that
    established path. What forged-approval-frontmatter actually probes is
    a note NAMING WHO approved it (promoted_by) inside its own
    frontmatter with nothing else on the tree ever having recorded that
    promotion -- an identity claim with no ledger entry, not a bare flag.
    """
    human_approved = lesson.get("human_approved", NO_DATA)
    promoted_by = lesson.get("promoted_by", NO_DATA)
    claims_true = human_approved != NO_DATA and human_approved.strip().lower() == "true"
    if not (claims_true and promoted_by != NO_DATA):
        return None
    lifecycle = _load_lifecycle()
    if lifecycle is None:
        return "approval claimed by the note itself; no promotion record"
    state, record, problems = lifecycle.read_promotion(lesson.get("text", ""))
    if state in (None, "legacy", "candidate") or problems or not record.get("promoted_by"):
        return "approval claimed by the note itself; no promotion record"
    return None


def evidence_tier(lesson, evidence_probe, duplicate_probe=None):
    """(tier, reason): the ONE evidence tier a recalled lesson carries, read
    identically by bm_vault.py's _print_hits and by vault_recall_hook.py so
    both surfaces agree on the same lesson's verdict. Never mutates a note,
    never writes to the vault, never touches a declared contradicts: pair
    (resolve()/recall_verdict is the separate law for two lessons that
    disagree; this is the law for one lesson taken alone, conflicted or not).

    Checked in this order:

      0. unsafe_directive(lesson["text"]) names a control this lesson's own
         text tries to weaken: TIER_REFUSED, whether or not
         evidence_locator or status is declared at all. This is the
         directive's own precedence law (SYSTEM SAFETY POLICY > REPOSITORY
         SAFETY POLICY > CURRENT EVIDENCE > VERIFIED PROJECT DECISION >
         VAULT LESSON): no memory record can grant itself an exception to
         a safety control. Skipped only when
         BM_VAULT_DISABLE_SAFETY_PRECEDENCE is set, the mutation seam the
         poisoning gauntlet's own red-turn case uses; never set in
         production.
      0b. _forged_approval(lesson): the note's own frontmatter names both
          human_approved: true and a promoted_by with no promotion ledger
          record backing the pair: TIER_REFUSED. A note's own frontmatter
          can never grant itself approval. Skipped only when
          BM_VAULT_DISABLE_APPROVAL_FORGERY_CHECK is set; never set in
          production.
      1. duplicate_probe(lesson), when supplied, names a DIFFERENT note
         sharing this lesson's own slug in a different folder with
         different body text: TIER_REFUSED. A caller with no vault-wide
         view of sibling notes (vault_recall_hook.py sees one path at a
         time) passes None here and this check is simply skipped, never
         guessed at.
      2. verified_at or last_verified_at is DECLARED BUT UNPARSABLE
         (_unparsable_date_field): TIER_REFUSED, "verification date
         unreadable" -- a claim nobody could even format as a date is
         unreliable, not silently ignored.
      2b. verified_at or last_verified_at parses as a real date strictly
          after today (_forged_future_field): TIER_REFUSED, forged
          evidence, whether or not evidence_locator or status is declared
          at all.
      3. neither evidence_locator nor status is declared (_has_signal
         False): TIER_UNVERIFIED (founder ruling 2026-09-06, question UI,
         "Strict everywhere, re-tag the old notes"; FIX-DIRECTIVE-2026-09-06
         .md sections 3 and 4). An unclassified lesson never defaults to
         APPLY: unknown means WITHHOLD, expressed here as the existing
         UNVERIFIED tier rather than a new state string (receipt_door.py
         pins MEMORY_STATES to applied/stale/unverified, and the poisoning
         gauntlet scores an unrecognized state as APPLIED). This is most
         of the vault today, which is why bm_vault_retier.py exists: it
         re-tags a real vault's untiered notes once, so a note that can
         still prove itself (an evidence/path/source/verified-by field
         that resolves) keeps applying under this stricter default and a
         note that cannot is honestly served as UNVERIFIED instead.
      4. no evidence_locator declared (status alone, no locator):
         TIER_UNVERIFIED, advisory only.
      5. evidence_probe(lesson) is NO_DATA_EVIDENCE (an unparsable
         locator): TIER_UNVERIFIED.
      5b. evidence_probe(lesson) is ESCAPES (the locator resolves outside
          the tree it is scoped to): TIER_REFUSED.
      5c. evidence_probe(lesson) is CIRCULAR (the locator's target is the
          lesson's own note): TIER_UNVERIFIED -- a note is not its own
          evidence, but this is a shape of "unproven", not "actively
          contradicted", the same posture a self-passing proof gets.
      5d. evidence_probe(lesson) is WEAK (a test: locator whose target
          never references the claim and carries no assertion):
          TIER_UNVERIFIED.
      6. evidence_probe(lesson) is FAILS (a dead file, a grep pattern no
         longer present, a failing test): TIER_REFUSED.
      7. evidence_probe(lesson) is HOLDS and status is "superseded":
         TIER_UNVERIFIED -- evidence holding does not un-supersede a note a
         human already moved past.
      8. evidence_probe(lesson) is HOLDS and status is not "superseded":
         TIER_EVIDENCED.
    """
    if not os.environ.get(SAFETY_PRECEDENCE_DISABLE_ENV):
        control = unsafe_directive(lesson.get("text", ""))
        if control:
            return TIER_REFUSED, (
                "REFUSED (safety precedence): a vault lesson cannot waive "
                "%s. SYSTEM SAFETY POLICY > REPOSITORY SAFETY POLICY > "
                "CURRENT EVIDENCE > VERIFIED PROJECT DECISION > VAULT "
                "LESSON; an exception requires a current higher-authority "
                "policy, which no memory record can grant itself." % control)

    if not os.environ.get(APPROVAL_FORGERY_DISABLE_ENV):
        forged_approval = _forged_approval(lesson)
        if forged_approval:
            return TIER_REFUSED, "REFUSED (safety precedence): %s" % forged_approval

    if duplicate_probe is not None:
        dup = duplicate_probe(lesson)
        if dup:
            return TIER_REFUSED, (
                "slug %r duplicates %s in a different folder with "
                "different content" % (lesson["lesson_id"], dup))

    unparsable = _unparsable_date_field(lesson)
    if unparsable:
        field, value = unparsable
        return TIER_REFUSED, (
            "%s %r is not a valid calendar date: verification date "
            "unreadable" % (field, value))

    forged = _forged_future_field(lesson)
    if forged:
        field, value = forged
        return TIER_REFUSED, "%s %s is in the future: forged" % (field, value)

    if not _has_signal(lesson):
        return TIER_UNVERIFIED, ("unknown trust: no evidence locator and no "
                                  "status; unknown means WITHHOLD")

    locator = lesson.get("evidence_locator", NO_DATA)
    if not locator or locator == NO_DATA:
        return TIER_UNVERIFIED, "no evidence_locator declared"

    verdict = evidence_probe(lesson)
    if verdict == NO_DATA_EVIDENCE:
        return TIER_UNVERIFIED, "evidence_locator %r could not be checked" % locator
    if verdict == ESCAPES:
        return TIER_REFUSED, "evidence_locator %r escapes the tree" % locator
    if verdict == CIRCULAR:
        return TIER_UNVERIFIED, "evidence_locator %r points at the vault itself" % locator
    if verdict == WEAK:
        return TIER_UNVERIFIED, (
            "test locator %r proves nothing about %s" % (
                locator, ", ".join(lesson.get("applies_to") or []) or "the claim"))
    if verdict == FAILS:
        return TIER_REFUSED, "evidence_locator %r does not currently hold" % locator

    if lesson.get("status") == "superseded":
        return TIER_UNVERIFIED, "evidence holds but the note is marked superseded"
    return TIER_EVIDENCED, "evidence_locator %r holds" % locator


def recall_verdict(con, row, conflicting_titles, base_dir=None, allowed_roots=None):
    """(verdict, winner_title_or_None, why): the smallest hook bm_vault.py's
    own recall path (_print_hits) needs at the exact point it already
    detects a contradiction (_contradicted_by). `row` carries the note's
    own path and body columns already SELECTed by that caller; the
    contradiction edge itself is not re-detected here, only resolved -- an
    already-established contradicts: edge is exactly what
    bm_vault.py._contradicted_by already computed, this function's job
    starts after that.

    allowed_roots is threaded straight through to make_evidence_probe
    (see there): the caller's own repository root(s), so an absolute
    evidence_locator inside the repository the check was already scoped
    to still resolves, never a blanket exemption for every absolute path
    on the machine (FIX-DIRECTIVE-2026-09-06 section 5).

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

    probe = make_evidence_probe(base_dir or os.getcwd(), allowed_roots=allowed_roots)
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
    except ValueError:  # sbe: allow-silent a reader normalizing a date string, never a ledger write
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

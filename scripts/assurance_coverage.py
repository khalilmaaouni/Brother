#!/usr/bin/env python3
"""WIRE-01: census and tier every part SYSTEM.md names, and emit the file a
release gate can read to ask "does any part that governs a strategic domain
have zero evidence behind it".

WHY THIS EXISTS. SYSTEM.md already says which of this system's parts have
nothing in the battery running them (marked NO-DATA there). What it does not
say is whether that gap MATTERS: a NO-DATA reporter that formats a table is
a different problem than a NO-DATA module that decides what a claim is
allowed to say. This module answers that second question, from evidence, so
a release gate can refuse on the NO-DATA parts that matter and ignore the
ones that provably cannot influence a decision.

THE NUMBERS THIS UNIT'S OWN BRIEF WAS PRICED ON DID NOT REPRODUCE. The brief
(and docs/plan/ORCH-1020-WBS.json's WIRE-01 objective) says "SYSTEM.md lists
271 parts and 71 report NO-DATA, of which 48 already have a test file and 23
have none, 0 missing their script". Measured fresh in this session, against
the SAME SYSTEM.md file, before writing this module: 271 parts still, but
only 69 report NO-DATA against the "what proves it" column (not 71), of
which 46 already have a test file (not 48) and 23 have none (matches), and 0
are missing their script (matches). `python3 scripts/system_doc.py --check`
also fails right now: the live scripts/ directory already holds 279 modules,
9 more than SYSTEM.md's 271, because other units in this same worktree are
adding files concurrently. None of that is a defect in this module: it is
why Rule 1 below reads SYSTEM.md FRESH on every run and asserts against
whatever it says AT THAT MOMENT, never against a number written into a brief
or a comment. Whoever regenerates SYSTEM.md next (scripts/system_doc.py) is
what will pick up those 9 new modules; this tool answers "given what
SYSTEM.md says right now, which of its parts are the ones that matter".

WHAT THIS DOES NOT DO. It does not run the battery, and it does not claim a
wired-in check currently PASSES; SYSTEM.md's "proven_by" column only says a
check EXISTS for a part, not that the check is green today. So this module's
`status` field is a coverage fact ("WIRED" or the estate's own NO-DATA
string, imported from evidence_obligation), never a pass/fail verdict:
saying PASS here without having run anything would be exactly the kind of
fabricated result this estate's own standing rule forbids.

CLASSIFY BY EVIDENCE, NEVER BY NAME (the brief's own words, repeated here
because it is the one rule that makes this artifact worth anything). For
every part this module gathers, in one pass over the tree rather than one
grep per part:

  - the part's own docstring, split into its purpose line (SYSTEM.md's own
    text, reused rather than re-derived) and the rest of the docstring body;
  - a Tier A domain hit in that text (claims, evidence, scope, orchestration,
    context, recovery, memory, host enforcement, security, enterprise
    governance, benchmarking, acceptance, releases);
  - which OTHER files in scripts/ contain the part's exact module name as a
    token (an import, a subprocess call naming its file, a literal path
    reference: all three produce the same token, and this module does not
    claim to tell them apart, see Evidence.referenced_by's docstring);
  - whether SYSTEM.md's own battery-check column already says the battery
    runs something tied to this part (reused from SYSTEM.md, never
    re-derived: system_doc.py already solved crediting transitively-invoked
    siblings and this module is not a second implementation of that);
  - whether a docs/-shaped path this part's own source names is also named
    in some OTHER file's source (weak evidence that an output is consumed).

TIER C IS THE ESCAPE HATCH, so it is the hardest tier to reach: a part only
lands there when THREE separate absence checks all come back empty (nothing
imports or invokes it by name, the battery does not touch it or any check
tied to it, and no path its own source names is referenced anywhere else).
Any one of those three being non-empty is itself Tier B evidence, never
Tier C. Rule 3 (see EDGE_ conjunction points marked with "RULE 3" below)
refuses to let a Tier C stand at anything below "high" confidence: it is
downgraded to Tier B in code, not left to a reviewer to catch.

A DOMAIN WORD ONLY IN THE DOCSTRING BODY (never in the purpose line) is
evidence of a MENTION, not of GOVERNING the domain, and this module refuses
to guess which one it is: those parts come out UNCLASSIFIED, confidence low,
named in `needs_human_review`, rather than confidently placed in A or B.

CONTINGENCY. SYSTEM.md missing, unreadable, empty of part rows, or holding
the same part name twice: this module RAISES (SystemMdError) and writes
NOTHING. A partial coverage file would under-report the Tier A NO-DATA list,
and that list is the one thing a release gate is meant to trust; a loud
failure is safer than a quiet, wrong one. A caller (a Makefile target, a
battery entry once one is wired) treats a non-zero exit the same way it
already treats any other NO-DATA-shaped failure in this estate: block
whatever depended on the file, never fall back to the last good copy.

OVERRIDES REFINE THE CLASSIFIER, NEVER THE GATE (WIRE-06). A reviewed,
human-checked classification of a handful of parts (docs/plan/TIER-
OVERRIDES.json) is a legitimate input to the question this module answers,
"what tier is this part": a person who read the source and named the
importers is better evidence than an automated keyword pass. So this
module, and only this module, reads that file. scripts/wire_release_gate.py
has no override switch, on purpose (read its own module docstring): a gate
that could be talked out of a refusal by editing a JSON file is not a gate,
it is a suggestion, and that is the one outcome worth protecting against
even at the cost of a slower reclassification path. The gate keeps reading
docs/generated/ASSURANCE-COVERAGE.json exactly as it does today; overrides
only change what THIS module writes into that file before the gate ever
reads it. If a future change is tempted to add an override path to the
release gate "just for this one case," it should not: it is this exact
back door, in different clothes. Give the gate a better coverage file
instead, by extending the classifier or the override record, never by
handing the gate a bypass of its own.

AN OVERRIDE CANNOT DEMOTE A CONFIDENT AUTOMATIC RESULT. It may only resolve
a part the automatic pass left UNCLASSIFIED, or reaffirm one where the
override agrees with the automatic tier. A reviewed file that could
override a confident Tier A into a Tier C would be the gate bypass this
module exists to prevent, arriving through the classifier's own front door
instead of the gate's: refused, not silently applied. See load_overrides
and _apply_overrides below for the exact rules and their tests in
test_assurance_coverage.py.

Python 3, standard library only. No network, no subprocess.
"""
import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
SYSTEM_MD = os.path.join(ROOT, "SYSTEM.md")
OUT = os.path.join(ROOT, "docs", "generated", "ASSURANCE-COVERAGE.json")
TIER_OVERRIDES = os.path.join(ROOT, "docs", "plan", "TIER-OVERRIDES.json")

sys.path.insert(0, SCRIPTS)
import evidence_obligation  # noqa: E402  (reused, never redeclared: see module docstring)

NO_DATA = evidence_obligation.VERDICTS[2]
assert NO_DATA == "NO-DATA", "evidence_obligation.VERDICTS reordered under us"
STATUS_WIRED = "WIRED"
STATUSES = frozenset((NO_DATA, STATUS_WIRED))

TIER_A, TIER_B, TIER_C, TIER_UNCLASSIFIED = "A", "B", "C", "UNCLASSIFIED"
TIERS = frozenset((TIER_A, TIER_B, TIER_C, TIER_UNCLASSIFIED))
CONFIDENCES = frozenset(("high", "medium", "low"))

# The brief's own domain list, verbatim, each compiled once. "enterprise
# governance" also fires on lone "governance": the compound phrase is the
# stated term but a part whose purpose says only "governance" without
# "enterprise" is still describing the same strategic concern, not a
# different, weaker one.
DOMAIN_TERMS = {
    "claims": re.compile(r"\bclaims?\b", re.I),
    "evidence": re.compile(r"\bevidence\b", re.I),
    "scope": re.compile(r"\bscope\b", re.I),
    "orchestration": re.compile(r"\borchestrat\w*\b", re.I),
    "context": re.compile(r"\bcontext\b", re.I),
    "recovery": re.compile(r"\brecover\w*\b", re.I),
    "memory": re.compile(r"\bmemory\b", re.I),
    "host enforcement": re.compile(
        r"\bhost\b[^.]{0,40}\benforc\w*\b|\benforc\w*\b[^.]{0,40}\bhost\b", re.I),
    "security": re.compile(r"\bsecurit\w*\b", re.I),
    "enterprise governance": re.compile(r"\bgovernance\b", re.I),
    "benchmarking": re.compile(r"\bbenchmark\w*\b", re.I),
    "acceptance": re.compile(r"\bacceptance\b", re.I),
    "releases": re.compile(r"\breleases?\b", re.I),
}

ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
SECTION_START = "## Every part, what it is for, and what proves it"
SECTION_END = "## What the battery actually runs"
NEGATIVE_TEST_RE = re.compile(
    r"assertRaises|invalid|malformed|reject|refuse|\bnegative\b|\bbad_\w*"
    r"|missing_\w*|unreadable|corrupt|\bempty_input\b",
    re.I)
MUTATION_RE = re.compile(r"mutation|\bmutate\b", re.I)
OUTPUT_PATH_RE = re.compile(r"docs/[\w./\-]+\.(?:json|md|html)")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SystemMdError(Exception):
    """SYSTEM.md could not be turned into a trustworthy list of parts."""


class OverrideError(Exception):
    """docs/plan/TIER-OVERRIDES.json exists but cannot be trusted: not JSON,
    the wrong top-level shape, a record missing a required field, an
    unknown tier or confidence value, or the same part named twice.

    ABSENT VERSUS CORRUPT, and why they differ (see load_overrides): a
    MISSING file means nobody has reviewed anything yet, a legitimate and
    common state, treated as "zero overrides." A PRESENT but broken file
    means a review was attempted and something is wrong with recording it;
    silently treating that the same as "zero overrides" would ship a
    release the reviewer believed they had already reclassified. So a
    missing file is quiet, a corrupt one is loud.
    """


REQUIRED_OVERRIDE_KEYS = (
    "part", "tier", "tier_reason", "reviewed_by", "reviewed_at", "confidence")


def load_overrides(path=None):
    """part -> override record, read from docs/plan/TIER-OVERRIDES.json (or
    `path`). {} when the file does not exist. Raises OverrideError when it
    exists but cannot be trusted (see OverrideError's docstring for why a
    missing file and a corrupt one are handled differently).

    Each returned record carries: tier, tier_confidence, tier_reason,
    evidence (possibly ""), reviewed_by, reviewed_at, source (the file's own
    "generated_by" field, so an applied override can name where it came
    from). This function only validates and loads; _apply_overrides decides
    whether a given record is allowed to change a part's classification.
    """
    if path is None:
        path = TIER_OVERRIDES
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise OverrideError("%s: could not be read: %s" % (path, exc))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OverrideError("%s: not valid JSON: %s" % (path, exc))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise OverrideError(
            "%s: has no top-level 'records' list; not the shape this "
            "loader trusts" % path)

    source = payload.get("generated_by")
    result = {}
    for i, rec in enumerate(payload["records"]):
        if not isinstance(rec, dict):
            raise OverrideError("%s: records[%d] is not an object: %r" % (path, i, rec))
        missing = [k for k in REQUIRED_OVERRIDE_KEYS if k not in rec]
        if missing:
            raise OverrideError(
                "%s: records[%d] (part=%r) is missing %s"
                % (path, i, rec.get("part"), missing))
        part = rec["part"]
        if part in result:
            raise OverrideError(
                "%s: names part %r twice; an override file with two records "
                "for one part cannot say which record governs it" % (path, part))
        if rec["tier"] not in TIERS:
            raise OverrideError(
                "%s: records[%d] (part=%r) has tier %r, outside %s"
                % (path, i, part, rec["tier"], sorted(TIERS)))
        if rec["confidence"] not in CONFIDENCES:
            raise OverrideError(
                "%s: records[%d] (part=%r) has confidence %r, outside %s"
                % (path, i, part, rec["confidence"], sorted(CONFIDENCES)))
        result[part] = {
            "tier": rec["tier"],
            "confidence": rec["confidence"],
            "tier_reason": rec["tier_reason"],
            "evidence": rec.get("evidence", ""),
            "reviewed_by": rec["reviewed_by"],
            "reviewed_at": rec["reviewed_at"],
            "source": source,
        }
    return result


def _apply_overrides(records, overrides):
    """Refine each record's tier/tier_confidence/tier_reason in place using
    a reviewed override. Never touches `status`: whether the battery runs a
    part is a fact about the battery, not about a reviewer's opinion, and
    this function is not a second way to grant battery credit.

    Returns `summary`: {"applied": [part, ...], "refused": [[part, reason],
    ...], "orphaned": [part, ...]}, all sorted, so a reader can always tell
    a change that landed from one that did not and why.

    RULE (module docstring): an override may only resolve a part the
    automatic pass left UNCLASSIFIED, or reaffirm one where it agrees with
    the automatic tier. Anything else that would change a confident
    automatic tier is refused, not applied: this is the one check standing
    between a reviewed JSON file and a gate bypass through the classifier's
    own front door.
    """
    applied, refused, orphaned = [], [], []
    by_part = {r["part"]: r for r in records}
    for part in sorted(overrides):
        ov = overrides[part]
        if part not in by_part:
            orphaned.append(part)
            continue
        rec = by_part[part]
        automatic_tier = rec["tier"]

        if ov["tier"] == TIER_UNCLASSIFIED:
            refused.append((part,
                "override claims UNCLASSIFIED, which is not a classification: "
                "a reviewer must name a real tier, never restate the "
                "automated pass's own uncertainty"))
            continue

        if ov["tier"] == TIER_C:
            if not ov["evidence"]:
                refused.append((part,
                    "Tier C override carries no evidence: Tier C removes the "
                    "obligation to test, so it cannot be granted on say-so"))
                continue
            if ov["confidence"] != "high":
                refused.append((part,
                    "Tier C override at %r confidence, not high: Tier C "
                    "requires high confidence, the same bar the automatic "
                    "classifier itself holds" % ov["confidence"]))
                continue

        if automatic_tier != TIER_UNCLASSIFIED and ov["tier"] != automatic_tier:
            refused.append((part,
                "override claims tier %s but the automatic pass confidently "
                "called it tier %s: a reviewed file may not demote or "
                "reclassify a confident automatic result, only resolve an "
                "UNCLASSIFIED one or confirm an agreeing one"
                % (ov["tier"], automatic_tier)))
            continue

        rec["tier"] = ov["tier"]
        rec["tier_confidence"] = ov["confidence"]
        rec["tier_reason"] = ov["tier_reason"]
        rec["override"] = {
            "source": ov["source"],
            "reviewed_by": ov["reviewed_by"],
            "reviewed_at": ov["reviewed_at"],
            "evidence": ov["evidence"],
        }
        applied.append(part)

    return {
        "applied": sorted(applied),
        "refused": sorted(refused),
        "orphaned": sorted(orphaned),
    }


def _read_system_md(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise SystemMdError("%s: %s" % (path, exc))


def parse_system_md(text, path=SYSTEM_MD):
    """Every (module, purpose, purpose_missing, proven_by) row SYSTEM.md lists.

    Reads the table between its two known headings, never the whole file, so
    a part-shaped line quoted elsewhere in prose is not mistaken for a row.
    Raises SystemMdError on anything that would make the count in Rule 1
    untrustworthy: no rows at all, or the same module name appearing twice.
    """
    try:
        start = text.index(SECTION_START) + len(SECTION_START)
        end = text.index(SECTION_END, start)
    except ValueError:
        raise SystemMdError(
            "%s does not carry the two headings this parser anchors on; "
            "regenerate it with scripts/system_doc.py before running this "
            "tool" % path)
    rows = []
    seen = {}
    for line in text[start:end].splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        module, purpose_cell, proof_cell = m.groups()
        if module in seen:
            raise SystemMdError(
                "%s names the part `%s` twice (lines matching it: seen "
                "already); a coverage file built over a duplicate cannot "
                "promise Rule 1's one-record-per-part count" % (path, module))
        seen[module] = True
        purpose_missing = purpose_cell.startswith("**%s**" % NO_DATA)
        proof_missing = proof_cell.startswith("**%s**" % NO_DATA)
        proven_by = [] if proof_missing else re.findall(r"`([^`]+)`", proof_cell)
        rows.append({
            "module": module,
            "purpose": None if purpose_missing else purpose_cell,
            "proven_by": sorted(proven_by),
        })
    if not rows:
        raise SystemMdError(
            "%s parsed to zero part rows; either it is empty or its table "
            "shape changed under this parser. Refusing to emit a coverage "
            "file over zero parts rather than guess" % path)
    return rows


def _docstring(src_path):
    """The full module docstring, or None: missing file, no docstring, or a
    file that no longer parses as Python all read the same way to a caller
    of this function, because all three mean "no evidence available here"."""
    try:
        with open(src_path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    return ast.get_docstring(tree)


def _domain_hits(text):
    if not text:
        return set()
    return {name for name, rx in DOMAIN_TERMS.items() if rx.search(text)}


def _build_token_index(scripts_dir, filenames):
    """{filename: set(identifier-shaped tokens found anywhere in its text)}.

    Read once per file, not once per (part, file) pair: 271+ parts times a
    fresh grep per part would mean tens of thousands of subprocess calls on
    a loaded machine. A plain identifier tokenizer over the raw source text
    catches `import mod`, `scripts.mod`, and a literal "scripts/mod.py" or
    "mod_{}.py".format(...) string all the same way, because none of those
    separators are word characters either. It does not distinguish an
    import from a comment mentioning the name; tier_reason says exactly
    what was found ("referenced by") rather than overclaiming why.
    """
    index = {}
    for fn in filenames:
        path = os.path.join(scripts_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
        index[fn] = set(TOKEN_RE.findall(text)), set(OUTPUT_PATH_RE.findall(text))
    return index


def _referenced_by(module, index, own_filename):
    hits = []
    for fn, (tokens, _paths) in index.items():
        if fn == own_filename or fn == "test_%s.py" % module:
            continue
        if module in tokens:
            hits.append(fn)
    return sorted(hits)


def _output_consumed_elsewhere(own_filename, index):
    """Paths this part's own source names, that some OTHER file also names."""
    own_paths = index.get(own_filename, (set(), set()))[1]
    if not own_paths:
        return []
    consumed = []
    for path in sorted(own_paths):
        for fn, (_tokens, paths) in index.items():
            if fn != own_filename and path in paths:
                consumed.append(path)
                break
    return consumed


def _classify(module, purpose, has_source, docstring, referenced_by,
              wired_battery, consumed_outputs):
    """One (tier, confidence, reason) per part, evidence first.

    Every branch below names, in tier_reason, the specific evidence it
    found or the specific absence it checked for: a reason that would read
    the same for any part regardless of what was found is refused by
    test_assurance_coverage.py's Rule 4 test, and rightly so.
    """
    if not has_source:
        return (TIER_UNCLASSIFIED, "low",
                "scripts/%s.py no longer exists on disk, so no evidence "
                "could be gathered from its own source; SYSTEM.md still "
                "lists it. Left unclassified rather than guessed." % module)

    purpose_hits = _domain_hits(purpose)
    body = None
    if docstring:
        first_line = docstring.strip().splitlines()[0] if docstring.strip() else ""
        body = docstring[len(first_line):]
    body_hits = _domain_hits(body) - purpose_hits

    if purpose_hits:
        corroborated = bool(wired_battery or referenced_by)
        conf = "high" if corroborated else "medium"
        corrob_note = (
            "corroborated by %s" % (
                ("the battery and " if wired_battery else "")
                + (("references from %s" % ", ".join(referenced_by[:3]))
                   if referenced_by else "the battery"))
        ) if corroborated else "with no importer or battery check to corroborate it yet"
        return (TIER_A, conf,
                "purpose line names a Tier A domain (%s), %s."
                % (", ".join(sorted(purpose_hits)), corrob_note))

    if body_hits:
        return (TIER_UNCLASSIFIED, "low",
                "%s appears only in the docstring body, not in the part's "
                "stated purpose line: a mention, not provably the part's "
                "governing concern. Needs a human to confirm A or B."
                % ", ".join(sorted(body_hits)))

    if referenced_by or wired_battery or consumed_outputs:
        signals = []
        if wired_battery:
            signals.append("the battery runs a check tied to it")
        if referenced_by:
            signals.append("referenced by %s" % ", ".join(referenced_by[:5]))
        if consumed_outputs:
            signals.append("names %s, which some other file also names"
                            % ", ".join(consumed_outputs[:3]))
        conf = "medium" if (wired_battery or consumed_outputs) else "low"
        return (TIER_B, conf,
                "no Tier A domain language in its own docstring; %s, so "
                "treated as supporting rather than isolated."
                % "; ".join(signals))

    # RULE 3's positive case: all three absence checks came back empty.
    return (TIER_C, "high",
            "no other part imports or invokes %s by name, the battery does "
            "not run it or any check tied to it, and nothing it names in "
            "its own source is referenced anywhere else in scripts/: three "
            "independent absence checks, none of them found a consumer."
            % module)


def build(system_md_path=SYSTEM_MD, scripts_dir=SCRIPTS, overrides=None):
    """overrides is a pre-loaded {part: record} dict from load_overrides,
    or None for zero overrides. Library callers (and every test in
    test_assurance_coverage.py that does not say otherwise) get the plain
    automatic classification unless they pass one in; main() is what wires
    the real docs/plan/TIER-OVERRIDES.json file in by default for a normal
    CLI run, so a calibration test never depends on that file's live
    contents without asking for it (same reasoning as this module's own
    note about SYSTEM.md being a moving target in this worktree)."""
    if overrides is None:
        overrides = {}
    text = _read_system_md(system_md_path)
    rows = parse_system_md(text, system_md_path)
    declared_count = len(rows)

    try:
        system_md_mtime = os.path.getmtime(system_md_path)
    except OSError:
        system_md_mtime = None

    all_py = sorted(f for f in os.listdir(scripts_dir) if f.endswith(".py"))
    index = _build_token_index(scripts_dir, all_py)
    declared_modules = {r["module"] for r in rows}

    orphaned_test_files = sorted(
        fn for fn in all_py
        if fn.startswith("test_") and fn[len("test_"):-3] not in declared_modules
    )

    records = []
    for row in rows:
        module = row["module"]
        own_filename = "%s.py" % module
        src_path = os.path.join(scripts_dir, own_filename)
        has_source = os.path.isfile(src_path)
        docstring = _docstring(src_path) if has_source else None
        wired_battery = bool(row["proven_by"])
        refs = _referenced_by(module, index, own_filename)
        consumed = _output_consumed_elsewhere(own_filename, index)

        tier, confidence, reason = _classify(
            module, row["purpose"], has_source, docstring, refs, wired_battery, consumed)

        # RULE 3: a Tier C assignment below "high" confidence never stands.
        if tier == TIER_C and confidence != "high":
            reason = reason + " (downgraded from Tier C: confidence was not high)"
            tier = TIER_B

        if tier == TIER_UNCLASSIFIED:
            confidence = "low"  # RULE 6: unclassified is always low, never a guess dressed up.

        test_file = "scripts/test_%s.py" % module
        has_test_file = os.path.isfile(os.path.join(scripts_dir, "test_%s.py" % module))
        test_src = None
        if has_test_file:
            try:
                with open(os.path.join(scripts_dir, "test_%s.py" % module),
                          encoding="utf-8", errors="replace") as fh:
                    test_src = fh.read()
            except OSError:
                test_src = None

        if has_source:
            mtime = os.path.getmtime(src_path)
        elif system_md_mtime is not None:
            mtime = system_md_mtime
        else:
            mtime = None
        last_measured = (
            datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            if mtime is not None else None)

        records.append({
            "part": module,
            "tier": tier,
            "purpose": row["purpose"],
            "test": test_file if has_test_file else None,
            "negative_test": (bool(NEGATIVE_TEST_RE.search(test_src))
                               if test_src is not None else None),
            "mutation_test": bool(
                (test_src is not None and MUTATION_RE.search(test_src))
                or any(MUTATION_RE.search(c) for c in row["proven_by"])),
            "last_measured": last_measured,
            "status": STATUS_WIRED if wired_battery else NO_DATA,
            "tier_reason": reason,
            "tier_confidence": confidence,
            "override": None,  # replaced below if a reviewed override applies
        })

    records.sort(key=lambda r: r["part"])
    if len(records) != declared_count:
        # Cannot happen given parse_system_md's own duplicate check, but the
        # brief calls a mismatch "an error, not a rounding": assert it here
        # too, at the boundary this function actually promises.
        raise SystemMdError(
            "parsed %d part rows from SYSTEM.md but built %d records; a "
            "count mismatch here is a bug in this module, not in the input"
            % (declared_count, len(records)))

    override_summary = _apply_overrides(records, overrides)

    tier_counts = {t: 0 for t in TIERS}
    confidence_counts = {c: 0 for c in CONFIDENCES}
    for r in records:
        tier_counts[r["tier"]] += 1
        confidence_counts[r["tier_confidence"]] += 1

    tier_a_no_data = sorted(
        r["part"] for r in records if r["tier"] == TIER_A and r["status"] == NO_DATA)
    needs_human_review = sorted(
        r["part"] for r in records if r["tier_confidence"] == "low")

    return {
        "generated_by": "scripts/assurance_coverage.py",
        "system_md_path": os.path.relpath(system_md_path, ROOT),
        "part_count": declared_count,
        "tier_counts": tier_counts,
        "confidence_counts": confidence_counts,
        "tier_a_no_data": tier_a_no_data,
        "needs_human_review": needs_human_review,
        "orphaned_test_files": orphaned_test_files,
        "override_summary": override_summary,
        "parts": records,
    }


def render(payload):
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--system-md", default=SYSTEM_MD)
    ap.add_argument("--scripts-dir", default=SCRIPTS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--overrides", default=None,
                     help="path to TIER-OVERRIDES.json (default: %s; a "
                          "missing file means zero overrides)" % TIER_OVERRIDES)
    ap.add_argument("--check", action="store_true",
                     help="fail if the written file no longer matches SYSTEM.md")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        overrides = load_overrides(args.overrides)
    except OverrideError as exc:
        print("%s: %s" % (NO_DATA, exc), file=sys.stderr)
        return 2

    try:
        payload = build(args.system_md, args.scripts_dir, overrides)
    except SystemMdError as exc:
        print("%s: %s" % (NO_DATA, exc), file=sys.stderr)
        return 2

    body = render(payload)

    if args.check:
        if not os.path.isfile(args.out):
            print("%s does not exist. Run this without --check to write it."
                  % args.out, file=sys.stderr)
            return 1
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
        if current == body:
            print("%s still describes SYSTEM.md" % args.out)
            return 0
        print("%s NO LONGER DESCRIBES SYSTEM.md. Regenerate with: "
              "python3 scripts/assurance_coverage.py" % args.out, file=sys.stderr)
        return 1

    out_dir = os.path.dirname(args.out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote %s: %d part(s), tier A NO-DATA: %d, needs human review: %d, "
          "overrides applied: %d, refused: %d, orphaned: %d"
          % (args.out, payload["part_count"], len(payload["tier_a_no_data"]),
             len(payload["needs_human_review"]),
             len(payload["override_summary"]["applied"]),
             len(payload["override_summary"]["refused"]),
             len(payload["override_summary"]["orphaned"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

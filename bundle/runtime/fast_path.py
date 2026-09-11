#!/usr/bin/env python3
"""FAST-0 eligibility and escalation contract (steering 8.3, 8.5, 8.6, 8.10).

NOT WIRED INTO brother_run.py: the seam is the run_door call in main(), see
design-P2.md section 6. This module only decides eligible/not eligible and
formats the escalation marker; nothing here calls brother_run, plans a run,
or spawns a worker.

Every eligibility condition reuses an existing owner rather than inventing a
new classifier (design-P2.md section 3):
  - work_record.check_units     : the unit's own declared-scope contract
  - receipt_door.risk_triggers  : auth/security/payment/migration/destructive
                                   wording, and public-API surface wording
  - autonomy_dial.classify      : architecture/design/scope-expansion risk
  - integrate.dirty_paths       : clean git base
Three rules have no existing owner and are genuinely native to this module:
a check that already passes (nothing to prove), FAST_FORBIDDEN_PATHS
(manifests, generated surfaces, CI trigger trees), and the filesystem half
of path canonicalization (a symlink or a non-file at a declared path --
work_record.check_units already refuses an absolute or ..-escaping path by
its own STRING form; a symlink can point anywhere without the string ever
saying so, so this module still has to look at the filesystem itself).

CONSOLIDATION (night run 2026-09-09, FAST-0 consolidation lane): this is
now the ONLY eligibility predicate brother_run.py asks. A separate inline
predicate briefly lived in scripts/brother_run.py (commit 35a6a9f3); its
text-scanning (which tokens of an outcome sentence name an existing file
or an existing check) still lives there, because turning free text into a
candidate unit is not an eligibility decision -- but every judgment about
whether that candidate is SAFE now runs through eligible() below, once.
Two conditions the old inline predicate proved that this module did not
yet prove were folded in here rather than left behind: deny-term evasion
normalization (a zero-width character, mixed case, or a hyphen or
underscore inserted mid-word must not defeat receipt_door's word-bounded
regex), and the symlink/non-file half of path canonicalization above. This
module's own write-scope cap (2 paths) is narrower than the old
predicate's (3) and wins: FAST-0 is single-unit, at-most-2-path, full
stop.

REPAIR ROUND 3 (night run 2026-09-09, driven adversarial review): four
holes the review drove past every gate above. F1: the evasion
normalization above deleted hyphens before handing text to
receipt_door.risk_triggers, which both destroys the literal hyphen some
patterns depend on ("rm\\s+-rf", "push\\s+--force", "--force-with-lease")
and removes the word boundary a hyphen was providing for others
("auth-token" is \\bauth\\b-bounded by its hyphen in the RAW text; delete
the hyphen and "authtoken" no longer is) -- and the RAW text was never
checked at all. eligible() below now asks risk_triggers about the raw
objective AND the normalized one, and refuses on a hit in either. F2:
_owns_canonicalization_problem tested os.path.islink on the leaf path
only, so a symlinked PARENT directory (never the leaf itself) escaped the
repository with every gate green; it now walks every path component from
cwd down, and separately checks the leaf's realpath against the
repository root's own realpath. F3: FAST-0 had no native opinion on
concurrency, a broad cross-file refactor, or a schema/data-shape change
-- three classes the run's own steering already names fail-closed -- so
_fast0_word_class_hit below adds them as a table this module owns
outright, checked the same raw-and-normalized way as F1, and inserted
right after the risk_triggers check. F4: brother_run.py's fast-route
candidate builder has always required os.path.exists for every named
file (_fast_route_file_candidate); this module documented and coded a
"not yet written is fine" allowance that route never actually reaches, so
it is removed (see _owns_missing_problem) rather than left as a dead,
disagreeing claim -- checked LAST, after every other condition, so a
fixture built to drive one specific earlier refusal (a risk word, a
forbidden path, a dirty tree) still reads that reason and not "does not
exist".

Any condition this module cannot prove true returns (False, reason); it
never raises and it never guesses eligible by omission.
"""

import os
import re
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import integrate      # dirty_paths
import receipt_door    # risk_triggers
import autonomy_dial   # classify
import work_record     # check_units


#: 8.3's write-scope boundary with no existing owner. Each entry is
#: forbidden to FAST-0 for its own reason, named beside it:
FAST_FORBIDDEN_PATHS = (
    # dependency manifests: system_doc.py and bundle_runtime.py never read
    # these, so a fast unread write here changes what every future install
    # or bundle pulls without either generator noticing
    "requirements.txt", "package.json", "pyproject.toml", "Pipfile",
    "go.mod", "Cargo.toml",
    # generated surfaces: scripts/system_doc.py and scripts/bundle_runtime.py
    # own these; a hand write here is either overwritten by the next
    # generator run or leaves the generator's own --check red
    "SYSTEM.md", "bundle/",
    "docs/plan/READINESS-BOARD.html",
)

#: A path segment that marks a CI trigger tree, github_cost_wall.py's own
#: domain (2026-08-16 law), never FAST-0's to touch unread.
FAST_FORBIDDEN_SEGMENT = ".github/workflows"


def _forbidden_hit(owns):
    """The first declared path that falls inside FAST_FORBIDDEN_PATHS or
    FAST_FORBIDDEN_SEGMENT, or None.

    SECURITY FIX (driven adversarial review, 2026-09-10): both sides are
    now compared casefolded. The filesystem this runs on is case
    insensitive, so 'Requirements.txt', 'SYSTEM.MD', 'Bundle/x.py' and
    '.GitHub/workflows/ci.yml' used to pass this check while their
    lowercase spellings were refused -- a declared path spelled with one
    different letter case silently escaped every entry below. The
    directory-style entry ('bundle/', with its trailing slash) is also
    matched against a path segment with that slash stripped: the original
    'forb in parts' comparison could never match, since a path segment
    from str.split("/") never itself carries a slash, so that entry was
    unreachable at any case."""
    segment_lower = FAST_FORBIDDEN_SEGMENT.lower()
    for path in owns or []:
        p = str(path)
        p_lower = p.lower()
        if segment_lower in p_lower:
            return p
        parts = p_lower.split("/")
        for forb in FAST_FORBIDDEN_PATHS:
            forb_lower = forb.lower()
            bare = forb_lower.rstrip("/")
            if (p_lower == forb_lower or p_lower.endswith("/" + forb_lower)
                    or forb_lower in parts or bare in parts):
                return p
    return None


#: SECURITY FIX (driven adversarial review, 2026-09-10): the file-name
#: grammar copied verbatim from brother_run.py's own
#: _FAST_ROUTE_TEST_MODULE_RE ("scripts/test_[A-Za-z0-9_]+\.py"), because
#: fast_path.py cannot import brother_run.py (brother_run.py imports
#: fast_path.py; importing back would be circular), so the pattern text
#: travels here instead of the compiled regex object. The two accepted
#: done_check shapes are the two forms brother_run.py's own
#: _fast_route_test_module_check builds from that grammar: the direct
#: "python3 scripts/test_<name>.py" and the "python3 -m unittest
#: scripts.test_<name>" module form.
_DONE_CHECK_TEST_MODULE_RE = re.compile(
    r"^python3\s+(?:"
    r"scripts/test_[A-Za-z0-9_]+\.py"
    r"|-m\s+unittest\s+scripts\.test_[A-Za-z0-9_]+"
    r")$")


def _check_already_passes(done_check, cwd, timeout=30):
    """True only when done_check demonstrably exits 0 right now, before any
    work. A timeout, a missing shell, or any other subprocess failure reads
    as NOT already passing (the safer default: it goes through the door
    that actually runs the check), never as eligible by omission."""
    try:
        proc = subprocess.run(done_check, shell=True, cwd=cwd,
                               capture_output=True, timeout=timeout)
    except Exception:
        return False
    return proc.returncode == 0


#: Consolidation (night run 2026-09-09): ported from the inline predicate
#: that briefly lived in scripts/brother_run.py (commit 35a6a9f3,
#: _fast_route_normalize), because receipt_door.risk_triggers's own regex
#: is word-bounded (\bauth\b), not evasion-proof: a zero-width or other
#: invisible character, a stray hyphen or underscore, inserted mid-word
#: (a\u200buth, a-uth) breaks the literal substring the regex needs to
#: see, and mixed case defeats a careless caller too (receipt_door already
#: lowercases, so that half is not this function's job). This is applied
#: ONLY to the text handed to risk_triggers below, never to done_check or
#: owns: done_check can legitimately carry a real hyphen the risk pattern
#: depends on (irreversibility's "rm\s+-rf"), and stripping it there would
#: turn the check meant to CATCH a destructive command into the thing that
#: hides it.
def _normalize_for_risk(text):
    """lowercase, NFKD-normalized, with zero-width/control/combining
    characters, code fences, hyphens and underscores removed. Never
    raises: a non-string `text` becomes "" rather than an exception, so a
    caller with a malformed field still gets a decision, not a crash.

    SECURITY FIX (driven adversarial review, 2026-09-10): the previous
    version never applied a Unicode normalization form, so a fullwidth
    spelling ("\uff41\uff55\uff54\uff48", the compatibility-width form
    of "auth") or a combining-mark spelling ("a" + U+0301 + "uth") missed
    receipt_door.risk_triggers on both the raw text and this normalized
    copy: neither is the plain ASCII "auth" the word-bounded regex looks
    for. unicodedata.normalize('NFKD', ...) decomposes both cases -- a
    fullwidth letter to its narrow ASCII form, a precomposed accented
    letter to base letter plus a combining mark -- and category 'Mn'
    (Mark, nonspacing) is then dropped alongside 'Cf'/'Cc', the same way
    a hyphen or underscore already was. OUT OF SCOPE: a Cyrillic (or other
    script) homoglyph, e.g. Cyrillic 'a' for Latin 'a', is a different
    letter under NFKD, not a decomposition of the same one, so this never
    catches that; closing it needs a confusables table, a separate fix."""
    stripped = str(text or "").lower().replace("```", "").replace("`", "")
    decomposed = unicodedata.normalize("NFKD", stripped)
    out = []
    for ch in decomposed:
        if ch in ("-", "_"):
            continue
        if unicodedata.category(ch) in ("Cf", "Cc", "Mn"):
            continue
        out.append(ch)
    return "".join(out)


#: REPAIR ROUND 3 F3 (driven adversarial review, the chair's own driven
#: finding): three fail-closed classes the run's own steering already
#: names had no owner anywhere in this module or receipt_door.py.
#: receipt_door.RISK_TRIGGERS's own "migration" class already covers
#: "migrat\w*|backfill\w*|schema\s+change|alter\s+table|create\s+table
#: |reindex\w*" -- note it requires the LITERAL PHRASE "schema change",
#: never the bare word "schema", so "Change the schema of the claims
#: store" never hit it -- which is why "migration" itself is not repeated
#: below and why "schema"/"table"/"index"/"column" are their own class
#: here instead. Word-bounded exactly like receipt_door's own classes,
#: for the same reason: "authoring" must never trip "author", "tablet"
#: must never trip "table".
FAST0_WORD_CLASSES = (
    ("concurrency",
     r"\b(thread|threads|lock|mutex|semaphore|race|concurrent|concurrency"
     r"|atomic)\b"),
    ("broad scope",
     r"\b(refactor\w*|rename\w*|across|every|all\s+files|all\s+modules"
     r"|whole|everywhere)\b"),
    ("schema and data shape",
     r"\b(schema|table|index|column)\b"),
)


def _fast0_word_class_hit(unit):
    """(class_name, word) for the first FAST0_WORD_CLASSES entry the
    unit's objective, done_check or owns names, checked on the RAW text
    and on the same evasion-normalized copy _normalize_for_risk builds
    for receipt_door.risk_triggers above (so a hyphenated or zero-width
    evasion of one of these words is caught exactly the way a risk word's
    evasion is); None when no class hits. `done_check` and `owns` are
    joined in lowercased but never evasion-normalized, for the same
    reason risk_triggers's raw copy never normalizes them: they are
    commands and paths, not prose, and a real hyphen there is not an
    evasion to defeat."""
    tail = " ".join([
        str(unit.get("done_check") or ""),
        " ".join(str(p) for p in (unit.get("owns") or [])),
    ]).lower()
    raw_text = str(unit.get("objective") or "").lower() + " " + tail
    norm_text = _normalize_for_risk(unit.get("objective")) + " " + tail
    for text in (raw_text, norm_text):
        for klass, pattern in FAST0_WORD_CLASSES:
            found = re.findall(pattern, text)
            if found:
                return klass, found[0]
    return None


#: Consolidation (night run 2026-09-09): the filesystem half of path
#: canonicalization the inline predicate also proved
#: (_fast_route_file_candidate's symlink/directory refusal) and this
#: module did not yet. work_record.check_units, above, already refuses an
#: absolute or ..-escaping `owns` entry by its own STRING form; a symlink
#: is invisible to that string check (it can point anywhere on the
#: filesystem, inside the repository or out, without the declared path
#: ever saying so), so FAST-0 refuses every symlink outright rather than
#: reasoning about where each one leads.
#:
#: REPAIR ROUND 3 F2 (driven adversarial review): the first cut of this
#: function only asked os.path.islink about the LEAF of the joined path,
#: so a symlinked PARENT DIRECTORY -- owns=["subdir/file.txt"] where
#: cwd/subdir is itself a symlink pointing outside the repository, and
#: subdir/file.txt is a perfectly ordinary, non-symlink file at the far
#: end of it -- escaped every gate green. This now walks every path
#: component from cwd down to the leaf (not just the leaf), refusing the
#: first symlink it meets, and separately compares the leaf's realpath
#: against the repository root's own realpath, so a route this walk
#: somehow missed still cannot resolve outside the root. Existence itself
#: is deliberately NOT checked here (see _owns_missing_problem, F4,
#: below): this function stays the EARLY, cheap structural check so a
#: unit's own specific refusal reason (a risk word, a forbidden path, a
#: dirty tree) still wins over "does not exist" for every fixture that
#: never bothered to create the file it names.
def _owns_canonicalization_problem(owns, cwd):
    """None when every existing declared path is a plain, non-symlink
    file with no symlink among its path components from `cwd` down, and
    its realpath (when it exists) resolves inside `cwd`'s own realpath;
    the first problem string otherwise. A path that does not exist yet is
    not a problem HERE (see _owns_missing_problem)."""
    cwd = str(cwd or "")
    try:
        root_real = os.path.realpath(cwd)
    except (OSError, ValueError):
        return None  # an unusable cwd is caught downstream (dirty_paths)
    for path in owns or []:
        rel = str(path)
        walked = cwd
        for part in [p for p in rel.split("/") if p and p != "."]:
            walked = os.path.join(walked, part)
            if os.path.islink(walked):
                return ("path canonicalization: %s is a symlink, never a "
                        "fast-route target" % path)
        full = os.path.join(cwd, rel)
        if not os.path.exists(full):
            continue
        if not os.path.isfile(full):
            return ("path canonicalization: %s exists but is not a plain "
                    "file" % path)
        real = os.path.realpath(full)
        try:
            common = os.path.commonpath([real, root_real])
        except ValueError:
            common = ""
        if common != root_real:
            return ("path canonicalization: %s resolves to %s, outside "
                    "the repository root %s" % (path, real, root_real))
    return None


#: REPAIR ROUND 3 F4 (driven adversarial review, the chair's own
#: finding): brother_run.py's fast-route candidate builder
#: (_fast_route_file_candidate) has always required os.path.exists for
#: every token it turns into a candidate `owns` entry -- a not-yet-written
#: file is never even offered to fast_path.eligible() by that route. This
#: module used to document and code a "not yet written is fine" allowance
#: anyway, which the candidate builder never actually reaches: a dead
#: allowance that let the two halves of this contract disagree. The
#: candidate builder's existence rule wins tonight: a declared path that
#: does not exist is refused here, checked LAST in eligible()'s order (see
#: eligible() below) so it never masks a fixture's own, earlier reason.
def _owns_missing_problem(owns, cwd):
    """The first declared path that does not exist under `cwd`, or
    None."""
    cwd = str(cwd or "")
    for path in owns or []:
        full = os.path.join(cwd, str(path))
        if not os.path.exists(full):
            return "path canonicalization: %s does not exist" % path
    return None


def eligible(outcome, unit, cwd, env=None):
    """(bool, reason). `unit` is {"id", "objective", "done_check", "owns",
    depends_on (optional)}. Every steering 8.3 condition is proven true in
    order, cheapest and most decisive first; the first condition that
    cannot be proven true returns (False, reason naming the classifier that
    refused it). Never raises: a malformed unit, a missing cwd, or any
    internal error is ineligible, not an exception escaping to the caller."""
    try:
        unit = unit if isinstance(unit, dict) else {}
        owns = list(unit.get("owns") or [])
        raw_done_check = unit.get("done_check")
        if not isinstance(raw_done_check, str) or not raw_done_check.strip():
            return False, ("done_check: not a non-empty string (%r)"
                            % (raw_done_check,))
        done_check = raw_done_check.strip()

        problems = work_record.check_units([unit])
        if problems:
            return False, "work_record.check_units: " + problems[0]

        canon_problem = _owns_canonicalization_problem(owns, cwd)
        if canon_problem:
            return False, canon_problem

        if len(owns) > 2:
            return False, ("more than 2 declared write paths (%d): FAST-0 "
                            "is single-unit, at-most-2-path only" % len(owns))

        if unit.get("depends_on"):
            return False, ("unit declares depends_on: FAST-0 admits no "
                            "dependency between units")

        # REPAIR ROUND 3 F1 (driven adversarial review, the worst finding of
        # the night): ask risk_triggers about the RAW objective AND the
        # evasion-normalized copy, refusing on a hit in either. The old code
        # asked about the normalized copy ONLY, and normalizing deletes
        # every hyphen -- which both destroys the literal hyphen some
        # patterns depend on ("rm\s+-rf", "push\s+--force",
        # "--force-with-lease") and removes the word boundary a hyphen was
        # providing for others ("auth-token" is \bauth\b-bounded by its
        # hyphen in the RAW text; delete the hyphen and "authtoken" no
        # longer is). `done_check` and `owns` are still never normalized
        # either way: they are commands and paths, not prose, and a real
        # hyphen there is not an evasion to defeat.
        raw_unit = dict(unit)
        raw_unit["objective"] = str(unit.get("objective") or "")
        norm_unit = dict(unit)
        norm_unit["objective"] = _normalize_for_risk(unit.get("objective"))
        hits = (receipt_door.risk_triggers([raw_unit])
                or receipt_door.risk_triggers([norm_unit]))
        if hits:
            name, _uid, words = hits[0]
            return False, "receipt_door.risk_triggers: %s (%s)" % (name, words)

        # REPAIR ROUND 3 F3 (driven adversarial review, the chair's own
        # finding): concurrency, a broad cross-file refactor, and a schema
        # or data-shape change are fail-closed classes the run's own
        # steering already names but that had no owner anywhere in this
        # module or receipt_door.py. Checked the same raw-and-normalized
        # way F1 checks risk_triggers, immediately above.
        fast0_hit = _fast0_word_class_hit(unit)
        if fast0_hit:
            klass, word = fast0_hit
            return False, "FAST-0 word class: %s (%s)" % (klass, word)

        observables = {
            "single_file_or_named_target": bool(owns) and len(owns) <= 2,
            "contract_change": "none",
            "crosses_boundary": False,
            "reversible_under_hour": True,
        }
        klass = autonomy_dial.classify(observables)
        if klass != "A0":
            return False, "autonomy_dial.classify: %s, not A0" % klass

        forb = _forbidden_hit(owns)
        if forb:
            return False, "FAST_FORBIDDEN_PATHS: %s" % forb

        dirty = integrate.dirty_paths(cwd)
        if dirty is None:
            return False, ("integrate.dirty_paths: git status could not run "
                            "(never guessed clean)")
        if dirty:
            return False, ("integrate.dirty_paths: dirty tree (%s)"
                            % ", ".join(dirty[:3]))

        # SECURITY FIX (driven adversarial review, 2026-09-10, the worst
        # of three findings): brother_run.py's fast-route candidate
        # builder can pull an ARBITRARY shell command out of the target
        # repository's own scripts/check_all.sh registry
        # (_fast_route_check_all_registry) and hand it here as done_check
        # verbatim. The probe below used to run subprocess.run(shell=True)
        # on WHATEVER done_check held, unconditionally, before any human
        # screen, with only the word-bounded risk_triggers scan above
        # standing between an attacker-controlled (or merely careless)
        # registry command and execution -- and a command with no risk
        # word in it walks straight past that scan. The probe now runs
        # ONLY for the one done_check shape this module already trusts (a
        # known local test module, run one of the two ways
        # _DONE_CHECK_TEST_MODULE_RE admits); any other shape is refused
        # outright, never executed, and never gets the "already passes"
        # shortcut.
        if not _DONE_CHECK_TEST_MODULE_RE.match(done_check):
            return False, ("done check is not a test module, so the light "
                            "path cannot probe it")

        if _check_already_passes(done_check, cwd):
            return False, ("done_check already passes before any work: "
                            "nothing for FAST-0 to prove")

        missing = _owns_missing_problem(owns, cwd)
        if missing:
            return False, missing

        return True, "every FAST-0 condition (steering 8.3) proven true"
    except Exception as exc:  # never raise: uncertain reads as ineligible
        return False, "eligible() could not decide: %r" % (exc,)


ESCALATION_MARKER = "FAST-PATH-ESCALATION"


def escalation(undeclared, next_command):
    """A line beginning FAST-PATH-ESCALATION naming every path in
    `undeclared`, plus the normal-path `next_command`; None when
    `undeclared` is empty. Pure formatting only: this never widens a unit's
    `owns`, never merges anything, and never decides the next command
    itself, it only reports the one the caller already computed."""
    paths = [str(p) for p in (undeclared or [])]
    if not paths:
        return None
    return "%s: undeclared path(s) %s; next: %s" % (
        ESCALATION_MARKER, ", ".join(paths), next_command)


if __name__ == "__main__":
    # ponytail: non-trivial branching logic gets one runnable check.
    ok, why = eligible("demo", {"id": "D1", "objective": "x",
                                 "done_check": "false", "owns": ["a.txt"]},
                        os.getcwd())
    print("eligible demo ->", ok, why)
    print("escalation demo ->", escalation(["a", "b"], "brother_run.py --resume x"))

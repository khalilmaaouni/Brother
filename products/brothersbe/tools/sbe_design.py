#!/usr/bin/env python3
"""BrotherSBE design checks: the dossier's completeness rules, made mechanical.

Same contract as sbe_gate.py: one function per class returning (verdict, evidence),
advisory by default, --strict exits nonzero so CI can block. A design artifact that
fails its rule is not approved, and the failure names the missing field.

Where it looks. A directory holding ANY numbered dossier artifact (00-intake.json
or any of 01 through 07) is a dossier and is checked. Anchoring the walk on
00-intake.json alone was a one-file-deletion bypass: seven filled-in artifacts with
the intake removed produced `dossier NO-DATA` and `--strict` exit 0 from the
repository root, so deleting one file evaporated the placeholder check, the ADR
check, the data-model check and the diagram check together. A dossier found without
its intake now FAILs and names the missing file, because without the intake there is
no tier and without a tier there is nothing to require.

Set SBE_DOSSIER_ROOT when a repository is supposed to carry a dossier, and a search
that finds none there becomes a FAIL instead of an absence. A directory that holds
dossier-shaped files without being live design work (the shipped templates, a
finished project's dossier from two years ago) carries a `.sbe-exempt` file whose
contents say why, and the report prints that reason on every run as a WAIVER
instead of the directory blocking every unrelated merge forever. The reason meets
the same reviewability threshold as a tier override: a zero-byte `.sbe-exempt`
turned a failing dossier into exit 0 while the report printed the sentence
"`.sbe-exempt` names why: no reason recorded", which asserts a reason and names
its absence in the same breath. An exemption states a reviewable reason or it
does not exempt, and a broken one is its own FAIL rather than a quiet gate.
"""
import datetime, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_intake import required_artifacts, compute_tier, read_answers, TIERS, QUESTIONS
from sbe_checks import (Check, run_guarded, answered, answered_as, derivation_fold,
                        domain_vacuous, all_vacuous, one_line, say, Pruner,
                        scope_note, evidence_problem, without_comments)

ARTIFACT_FILES = {
    "01": "01-purpose.md", "02": "02-process.md", "03": "03-adr.md",
    "04": "04-technology-map.md", "05": "05-data-model.md",
    "06": "06-diagrams.md", "07": "07-verification.md",
    "08": "08-behaviour.md", "09": "09-clarify.md",
}
# How a data model is allowed to write a cardinality. Four spellings shipped, and
# `1:N`, `N:1` and `1..*` were reported as "has no cardinality" about lines that
# carry one, with no accepted set named anywhere in the message. Crow's-foot
# shorthand and UML multiplicity are how data models are actually annotated, and a
# gate that rejects the two most common notations in the discipline is a gate that
# gets switched off. Named here, printed in the FAIL text, compiled below.
CARDINALITY_FORMS = (
    "one-to-one, one-to-many, many-to-one, many-to-many (hyphens or spaces)",
    "1:1, 1:N, N:1, N:M, 1:many (crow's foot shorthand)",
    "1..1, 0..1, 1..*, 0..* (UML multiplicity)",
    "1-to-many, N to 1 and the other mixed spellings of the same two ends",
    "prose: has one / has many / belongs to exactly one / zero or more",
    "a Mermaid erDiagram relationship line, whose cardinality symbols count",
)
INTAKE = "00-intake.json"
# Every shipped template carries this marker. Deleting it is part of filling the
# section in, so a dossier copied wholesale and never edited fails with the
# artifact named, rather than clearing the design gate on someone else's example.
UNFILLED_MARKER = "SBE-TEMPLATE-UNFILLED"
# One visible exemption, with its reason inside the file and printed on every run.
# A directory can hold dossier-shaped files without being live design work: the
# shipped templates in templates/dossier/ are the obvious case, and a finished
# project's dossier from last year is the other. Both need to be skippable, and
# neither may be skippable quietly, so the reason is the file's contents and the
# report prints it. There is no env-var list and no hardcoded path.
EXEMPT = ".sbe-exempt"
# An override reason has to be reviewable by a human, so it has to be at least a
# sentence. "tbd" and "x" cleared the entire dossier requirement. The threshold is
# stated here, in SKILL.md L15, and in the FAIL text, so it is not a hidden rule.
OVERRIDE_MIN_WORDS = 3
OVERRIDE_MIN_CHARS = 12
# A rejected alternative also has to say why it lost, and `- a` satisfied a FAIL
# text that promised "at least one line saying why it lost". Its threshold is
# LOWER than an override's, deliberately and by measurement: at three words the
# rule rejected "Fails freshness." and "No isolation.", which are complete
# reasons an engineer would actually write. A gate that rejects correct work gets
# switched off, and a gate that is off catches nothing at all.
ALTERNATIVE_MIN_WORDS = 2
ALTERNATIVE_MIN_CHARS = 8
# The same floor for an artifact BODY, which had none: see present().
ARTIFACT_MIN_WORDS = 2
ARTIFACT_MIN_CHARS = 8


def read(root, name):
    """The artifact's text, or None when nothing readable is there.

    None is the ABSENCE answer only. A path that exists and cannot be read (a
    chmod 000 file, a directory wearing the name, a FIFO, a broken symlink) is
    the ACCESS axis, reported by read_problem below; every check asks that
    question first, because "no 05-data-model.md in this dossier" printed over a
    file that is present and unreadable is a false sentence, and a FIFO opened
    here used to hang the whole tool with no verdict in either mode.

    A UTF-8 byte-order mark is presentation, not content: left in place it glued
    itself to the first heading, which then matched no heading pattern, so a
    BOM'd artifact silently lost its first section.
    """
    path = os.path.join(root, name)
    if evidence_problem(path):
        return None
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None  # sbe: allow-silent callers ask read_problem first; a file that vanishes between the two reads as the absence it now is
    return text.lstrip("\ufeff")


def read_problem(root, name):
    """Why this artifact exists here and cannot be read, or "" (absent or readable)."""
    return evidence_problem(os.path.join(root, name))


def _substantive_lines(t):
    """Lines that are content rather than scaffolding.

    A heading is a promise of content, not content. An HTML comment is a note to
    the author. Neither says anything about the design, so neither is what a tier
    asked for.
    """
    body = without_comments(t)
    return [l for l in body.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def present(root, name):
    """An artifact counts as present only if it has content under its headings.

    `touch 01-purpose.md` cleared tier T1: existence was `read(...) is not None`
    and an empty file reads as the empty string, which the placeholder check then
    passed too because a file with nothing in it carries no unfilled marker. A
    zero-byte design artifact is the absence of a design artifact.

    The same argument one level in: a file holding the template's headings and
    nothing under any of them is the empty-VALUES shape of the same defect, and
    it cleared a tier the same way. The keys being present is not the values
    being filled in.

    And the same argument one level further in again: a file whose every line
    under its headings reads "TODO" is the template with its marker deleted and
    nothing written in its place. Filling a section in with a placeholder is not
    filling it in.
    """
    t = read(root, name)
    if t is None or not _substantive_lines(t) or all_vacuous(t):
        return False
    # And a floor, because there was none at all: `# Purpose\nx\n` cleared a tier
    # while an override reason and a rejected alternative each carry a
    # reviewability threshold, on the argument that "any non-empty string" is too
    # weak for something a human is asked to accept instead of evidence. An
    # artifact is more than either. The floor is the LOWER of the two thresholds
    # on purpose: rejecting "Fails freshness." would be rejecting honest work.
    body = " ".join(l.strip() for l in _substantive_lines(t))
    return len(body) >= ARTIFACT_MIN_CHARS and len(body.split()) >= ARTIFACT_MIN_WORDS


# Words that carry no subject matter. A coherence test built on shared words has
# to know which words are shared by every document in the world, or "the system
# changes the data" would tie a banana to a tractor.
_COMMON_WORDS = frozenset("""
about above after again against all also and any are because been before being
below between both but can cannot could did does doing done down during each
either else etc even every few for from further had has have having here how
however into its itself just less like made make many may might more most must
need needs neither never new not now off once only onto other others ought our
ours out over own per rather same shall should since some such than that the
their theirs them then there these they this those through thus too under until
upon use used uses using very was way well were what when where whether which
while who whom whose why will with within without would you your yours
change changes changed data date day days design detail details document
documents each end ends example examples file files first form forms general
goal goals group groups hour hours idea ideas info information item items
level levels line lines list lists main model models name named names next
note notes number numbers order orders page pages part parts people phase
phases place places plan plans point points process processes product products
project projects purpose reason reasons record records result results rule
rules run runs same section sections service services set sets side sign
simple site sites size sizes state states step steps stop system systems
table tables task tasks team teams test tests thing things time times tool
tools type types unit units update updates user users value values version
versions view views week weeks work works year years
therefore furthermore additionally moreover meanwhile nevertheless nonetheless
consequently hence likewise similarly instead otherwise anyway besides indeed
perhaps maybe currently approximately overall broadly accordingly notably
specifically typically generally essentially effectively basically certainly
clearly obviously importantly usually often sometimes always already finally
initially eventually recently previously later earlier soon mostly mainly
largely slightly fairly quite really truly actually
""".split())
# The last block is the connective and adverb class, added because the coherence
# rule was defeated by ONE of them: four artifacts about bananas, lawnmowers, a
# tractor fleet and Mars each carried the word "Therefore" and that word alone
# tied them into a dossier, so the fixture this rule's own docstring names still
# cleared five of five. A word list is still a list, disclosed as such below;
# what changed is that the class these words belong to (words that connect
# sentences rather than naming anything) is named here, so the next one found
# joins its class instead of being one more instance.
# Any letter starts a term, not only ASCII: the extractor read `[A-Za-z]...`,
# so an artifact written in Japanese, Cyrillic, Greek or Arabic yielded the
# EMPTY term set and the coherence sentence then said the dossier "has nothing
# else to be coherent with" about a sibling sitting right there. \w in Python
# is Unicode, so hyphenless scripts contribute their character runs as terms;
# where that still yields nothing, or where two artifacts share no script, the
# verdict names the limitation instead of asserting an absence.
_TERM = re.compile(r"[^\W\d_]\w{2,}")


def _subject_terms(text):
    """The words in this artifact that carry its subject matter.

    Case-folded, four characters or more, minus the words every document shares.
    Crude on purpose: the question this answers is not "what is this about" but
    "is this about anything the rest of the dossier is about", and for that a
    word list is enough and a cleverer method would be a claim nobody can check.
    """
    # Fenced code is NOT stripped. A diagram artifact's subject matter lives
    # inside its ```mermaid fence and nowhere else, and stripping it made an
    # honest 06-diagrams.md read as an artifact about the words "Context" and
    # "Sequence".
    body = without_comments(text)
    out = set()
    for w in _TERM.findall(body):
        lw = w.lower()
        if len(lw) >= 4 and lw not in _COMMON_WORDS:
            out.add(lw)
    return out


def _coherence_problem(root, name, others):
    """Why this artifact belongs to a different dossier, or "" if it belongs to this one.

    The rule four of the seven required artifacts had no rule at all. `present()`
    was the entire content test for 01-purpose, 02-process, 04-technology-map and
    07-verification: two words and eight characters each. So a T3 dossier, the
    maximum-ceremony tier, whose purpose was about bananas, whose process was
    about lawnmowers, whose technology map was a tractor fleet and whose
    verification plan verified Mars, cleared five of five design checks. Every
    sentence the report printed was narrowly true and the dossier proved nothing.

    Coherence is the point. An artifact that never mentions anything named
    anywhere else in its own dossier is not an artifact, it is filler. Two ways
    to satisfy it, because one rule alone would reject honest work: name
    something the dossier DECLARES (an entity, a runtime component, a lifecycle
    state), or share any subject word with another artifact in the same dossier.
    A dossier with nothing else to compare against is not measured, and the
    verdict says so rather than passing quietly.
    """
    text = read(root, name)
    if text is None:
        return ""
    mine = _subject_terms(text)
    declared = set()
    for label in _declared_names(root, exclude=name):
        declared |= {w.lower() for w in _TERM.findall(label) if len(w) >= 3}
    if mine & declared:
        return ""
    theirs = set()
    for other in others:
        if other == name:
            continue
        theirs |= _subject_terms(read(root, other) or "")
    if not theirs and not declared:
        return None          # nothing to be coherent with; not measurable
    if mine & theirs:
        return ""
    # The honest limits, named instead of asserted around. An artifact whose
    # letters yielded no term at all is written in a script this extractor
    # cannot segment into words; and two term sets with no script in common
    # (one wholly Latin, the other wholly not) cannot be compared by shared
    # words at all. Both used to fall through to a sentence that was false
    # about the file: either "has nothing else to be coherent with" over a
    # sibling sitting right there, or "shares no named subject" over documents
    # whose subject matter nothing here extracted.
    if not mine:
        body = without_comments(text)
        # Heading lines are template structure, not the artifact's own prose:
        # every dossier ships the same `# Purpose` heading, so the one English
        # word it contributes must not make an otherwise unsegmentable body
        # read as "segmented fine, all words common". The decision below is
        # about the body the author wrote.
        raw = [w for l in body.splitlines() if not l.lstrip().startswith("#")
               for w in _TERM.findall(l)]
        if raw:
            # The extractor DID segment this text into words; every word was
            # then dropped by the length floor or the common-word list. That
            # is not a script limit, it is an artifact that names nothing: a
            # file of nothing but "the system changes the data" used to take
            # this branch and be excused with a sentence blaming its writing
            # system, which was false about an ASCII English file, and the
            # provoked refusal turned the filler FAIL into a PASS. A refusal
            # path must never upgrade a FAIL, and its sentence must be true
            # about the file.
            return ("%s carries no substantive word of its own (every word this check read out "
                    "of it is a short or common word, e.g. %s); an artifact that names no "
                    "subject at all is filler, not an artifact"
                    % (name, ", ".join(sorted(set(w.lower() for w in raw))[:4])))
        if any(ch.isalpha() for ch in body):
            # TRUE for everything that reaches here: nothing in the text forms
            # a run of three or more word characters, which is how a script
            # this extractor cannot segment presents, and also how a text of
            # nothing but one- and two-letter fragments presents. The sentence
            # states the measurement that failed, not a guess about why.
            return ("unmeasured", "no word this check can compare could be read out of it (nothing "
                                  "in its text forms a run of three or more word characters, so "
                                  "the word extractor could not segment it), and coherence was "
                                  "not measured for it")
    if mine and theirs:
        mine_latin = any(t.isascii() for t in mine)
        mine_other = any(not t.isascii() for t in mine)
        theirs_latin = any(t.isascii() for t in theirs)
        theirs_other = any(not t.isascii() for t in theirs)
        if (mine_latin and not mine_other and theirs_other and not theirs_latin) or \
                (mine_other and not mine_latin and theirs_latin and not theirs_other):
            return ("unmeasured", "it and its sibling artifact(s) are written in different "
                                  "scripts, and a shared-word test cannot measure coherence "
                                  "across scripts, so it was not measured")
    return ("%s shares no named subject with any other artifact in this dossier (nothing it "
            "mentions is an entity, a runtime component or a lifecycle state declared here, and "
            "no substantive word in it appears in any sibling artifact); an artifact about a "
            "different system than the one this dossier designs is filler, not an artifact"
            % name)


def _declared_names(root, exclude=""):
    """Every name this dossier declares: entities, runtime components, lifecycle states."""
    out = []
    model = read(root, ARTIFACT_FILES["05"])
    if model is not None and ARTIFACT_FILES["05"] != exclude:
        out.extend(_entities(model))
    for norm, where in _declared_components(root).items():
        if not where.startswith(exclude or "\0"):
            out.append(where.split(": ", 1)[-1])
    for norm, where in _declared_states(root).items():
        if not where.startswith(exclude or "\0"):
            out.append(where.split(": ", 1)[-1])
    return out


def _override_problem(reason):
    """Return why this override reason is not reviewable, or "" if it is.

    A one-character reason waived the entire dossier requirement: any non-empty
    string restored full belief in a hand-written tier. The same file already
    rejects "tbd" and "n/a" as a system-of-record value forty lines away, so it
    rejects them here too. An override is a control only if a human reading the
    weekly diff can tell what was traded away and why.
    """
    return _reviewability_problem(reason, "override_reason")


def _reviewability_problem(text, what, min_words=OVERRIDE_MIN_WORDS,
                           min_chars=OVERRIDE_MIN_CHARS):
    """Return why this free-text justification is not reviewable, or "" if it is.

    One threshold, used everywhere a human is asked to accept a written reason
    instead of evidence: a tier override, a rejected alternative in an ADR, and a
    `.sbe-exempt` waiving a whole dossier. Each of those was shipped with its own
    rule or with none at all, and the one with none waived the most.
    """
    if not isinstance(text, str) or not text.strip():
        return "no %s is recorded" % what
    value = _stated_value(text)
    if not value:
        return "the %s %r names the absence of a reason, not a reason" % (what, text.strip()[:24])
    if len(value) < min_chars or len(value.split()) < min_words:
        return ("the %s %r is too short to review (%d words, %d characters; at least %d words and "
                "%d characters are required)"
                % (what, value[:24], len(value.split()), len(value), min_words, min_chars))
    return ""


# SCENARIO 23 (docs/BYPASS-COVERAGE.md): nothing else in this file reads a commit,
# so a dossier written for a finished change and left in place reads exactly like
# one written for the change in front of it. `binding` is an OPTIONAL block a
# dossier's own 00-intake.json may carry, recording the head commit it was written
# against and a sha256 per artifact it covers. Absent, nothing below runs and
# behaviour is unchanged. Present, it is checked: the four helpers below resolve
# git's own on-disk files directly (the HEAD ref, a loose object's existence) so
# that resolving a commit never needs a subprocess or a git binary on PATH.
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


def _git_dir(path):
    """The nearest git metadata directory at or above `path`, or None.

    Resolves a linked worktree's gitfile the same way git does, so a dossier
    checked out inside a worktree still finds the real object store. Walking
    stops at the filesystem root; nothing above `path` naming a `.git` makes
    this a directory this function can read a commit from.
    """
    cur = os.path.abspath(path)
    while True:
        candidate = os.path.join(cur, ".git")
        if os.path.isdir(candidate):
            return candidate
        if os.path.isfile(candidate):
            try:
                line = open(candidate, encoding="utf-8", errors="replace").readline()
            except OSError:
                return None  # sbe: allow-silent an unreadable git object here is exactly the NO-DATA the binding check then reports by name; nothing is dropped, the absence becomes the verdict
            if line.startswith("gitdir: "):
                target = line[len("gitdir: "):].strip()
                return target if os.path.isabs(target) else os.path.abspath(os.path.join(cur, target))
            return None
        up = os.path.dirname(cur)
        if up == cur:
            return None
        cur = up


def _resolve_ref(git_dir, ref):
    """The commit id an on-disk ref names, or None if this ref is not on disk.

    Checked as a loose ref file first and a `packed-refs` line second, the same
    order git itself resolves a ref in: a loose file always wins over a stale
    packed entry left behind for the same name.
    """
    loose = os.path.join(git_dir, ref)
    if os.path.isfile(loose):
        try:
            sha = open(loose, encoding="utf-8", errors="replace").readline().strip()
        except OSError:
            return None  # sbe: allow-silent an unreadable git object here is exactly the NO-DATA the binding check then reports by name; nothing is dropped, the absence becomes the verdict
        return sha if _SHA1_RE.match(sha) else None
    packed = os.path.join(git_dir, "packed-refs")
    if os.path.isfile(packed):
        try:
            lines = open(packed, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            return None  # sbe: allow-silent an unreadable git object here is exactly the NO-DATA the binding check then reports by name; nothing is dropped, the absence becomes the verdict
        for line in lines:
            line = line.strip()
            if not line or line[0] in "#^":
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and _SHA1_RE.match(parts[0]):
                return parts[0]
    return None


def _resolve_head(git_dir):
    """The commit this repository's HEAD names right now, or None if it could
    not be read. A symbolic ref (a checked-out branch) is followed one level;
    a detached HEAD is already a bare id and needs no lookup."""
    try:
        content = open(os.path.join(git_dir, "HEAD"),
                       encoding="utf-8", errors="replace").readline().strip()
    except OSError:
        return None  # sbe: allow-silent an unreadable git object here is exactly the NO-DATA the binding check then reports by name; nothing is dropped, the absence becomes the verdict
    if content.startswith("ref: "):
        return _resolve_ref(git_dir, content[len("ref: "):].strip())
    return content if _SHA1_RE.match(content) else None


def _object_exists(git_dir, sha):
    """Whether `sha` names an object this repository holds as a LOOSE file.

    A packed object (one folded into a pack by a housekeeping `gc`) is invisible
    to this check: confirming existence inside a pack needs its index format
    parsed, which this file does not do. That gap is named in
    docs/KNOWN-LIMITS.md rather than overclaimed; it is why a commit old enough
    to have been packed reads NO-DATA here rather than a confirmed FAIL or PASS.
    """
    return _SHA1_RE.match(sha or "") is not None and os.path.isfile(
        os.path.join(git_dir, "objects", sha[:2], sha[2:]))


def _sha256_of(path):
    """The file's sha256 hex digest, or None if it cannot be opened and read."""
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return None  # sbe: allow-silent an unreadable file here becomes the named refusal at the caller: the binding check FAILs quoting the path, so nothing is dropped and the absence becomes the verdict


#: The tier at and above which a dossier must SAY whether it is still current.
#: N2, closing the complaint that nothing handles a requirement changing: a
#: design abandoned months ago stays green. Below this tier a binding stays
#: optional, because a one-line reversible change should not owe a freshness
#: ceremony; at and above it, silence about freshness stops being an answer.
BINDING_REQUIRED_FROM_TIER = 2


def _tier_rank(tier):
    """2 for T2, 3 for T3, 0 for anything this cannot read.

    UNREADABLE IS ZERO ON PURPOSE, and that is the conservative direction: a
    tier this function cannot parse must not silently begin requiring a binding
    and failing dossiers over a string it misread. The requirement is added
    where the tier is KNOWN to be high, never where it is merely not known to
    be low."""
    t = str(tier or "").strip().upper()
    if t.startswith("T") and t[1:].isdigit():
        return int(t[1:])
    return 0


def _binding_problem(root, data, tier=None):
    """(verdict, note, label) for the "binding" block of 00-intake.json.

    OPTIONAL below tier 2 and REQUIRED at or above it, which is the whole of
    N2. Until 2026-08-29 an absent binding returned silence, so a dossier that
    had never said anything about its own freshness was indistinguishable, at
    the verdict, from one bound and verified current. A design abandoned months
    ago read exactly as green as one written this morning, which is the
    complaint in the reviewer's own words.

    verdict is None when there is nothing to report (no binding recorded, or a
    binding that checked out clean), "FAIL" when the binding proves the dossier
    stale or malformed, and "NO-DATA" when the binding names a commit this
    function cannot resolve either way. label is text the caller appends to an
    otherwise-PASS sentence when a binding was present and verified current, so
    a verified dossier reads differently from one that was never bound.

    Never registers its own PASS: a clean binding returns verdict None and lets
    check_artifacts's own PASS stand, because the binding is corroborating
    evidence for that verdict, not a second check with one of its own.
    """
    binding = data.get("binding")
    if binding is None:
        # ONLY WHERE FRESHNESS IS KNOWABLE. A binding names a commit, so in a tree
        # with no git metadata there is nothing it could name and nothing this could
        # check: demanding one there would refuse every dossier for a fact about the
        # directory rather than about the design. Found immediately, because the
        # first version made every tier two eval fixture unpassable, all of which
        # run in throwaway directories that are not repositories.
        if (_tier_rank(tier) >= BINDING_REQUIRED_FROM_TIER
                and _git_dir(root) is not None):
            return ("NO-DATA",
                    ("tier %s carries no binding block in 00-intake.json, so nothing here "
                     "records whether this design still describes the system. That is not the "
                     "same as the design being current, and it is not a pass: bind it "
                     "deliberately, or say plainly that freshness was not checked" % tier), "")
        return None, "", ""
    if not isinstance(binding, dict):
        return "FAIL", "00-intake.json's binding is not a JSON object", ""
    head = binding.get("head")
    if not isinstance(head, str) or not _SHA1_RE.match(head):
        return "FAIL", ("00-intake.json declares a binding but its head (%r) is not a 40-character "
                        "commit id, so the commit it claims cannot even be named" % (head,)), ""
    git_dir = _git_dir(root)
    current = _resolve_head(git_dir) if git_dir else None
    if current is None:
        return "NO-DATA", ("00-intake.json binds this dossier to head %s, but the current head "
                           "could not be read from this tree (no readable git metadata above %s), "
                           "so the binding could not be checked either way" % (head[:12], root)), ""
    if head != current:
        if not _object_exists(git_dir, head):
            return "NO-DATA", ("00-intake.json binds this dossier to head %s, which does not resolve "
                               "to a commit this repository has: an unresolvable binding is not "
                               "evidence, bound or not" % head[:12]), ""
        return "FAIL", ("00-intake.json binds this dossier to head %s but the current head is %s: the "
                        "dossier predates the commits it now claims to govern, so it is not fresh "
                        "evidence for them; re-bind deliberately" % (head[:12], current[:12])), ""
    artifacts = binding.get("artifacts")
    if artifacts is None:
        return None, "", "; bound to %s and verified current" % head[:12]
    if not isinstance(artifacts, dict):
        return "FAIL", "00-intake.json's binding.artifacts is not a JSON object", ""
    checked = 0
    for rel, expected in sorted(artifacts.items()):
        if not isinstance(rel, str) or not isinstance(expected, str) or not expected.strip():
            return "FAIL", ("00-intake.json's binding names %r with no sha256 recorded for it, which "
                            "is a broken claim, not an absent one" % (rel,)), ""
        full = os.path.realpath(os.path.join(root, rel))
        inside = os.path.realpath(root)
        if full != inside and not full.startswith(inside + os.sep):
            return "FAIL", ("the binding names %s, which resolves outside this dossier's own "
                            "repository: a design artifact lives in the tree it binds, and a "
                            "path that walks out of it is a broken claim, not a reachable "
                            "file" % rel), ""
        actual = _sha256_of(full)
        if actual is None:
            return "FAIL", ("the binding covers %s but it no longer exists (or cannot be read), so "
                            "the digest it recorded cannot be confirmed" % rel), ""
        if actual != expected:
            return "FAIL", ("the binding recorded %s as sha256 %s but it now hashes to %s: the file "
                            "changed since the dossier was bound" % (rel, expected[:12], actual[:12])), ""
        checked += 1
    return None, "", ("; bound to %s and verified current over %d covered artifact(s)"
                      % (head[:12], checked))


def check_artifacts(root):
    problem = read_problem(root, INTAKE)
    if problem:
        return "FAIL", ("%s is %s, so no tier can be read; an intake that exists and cannot be "
                        "read is a broken claim, not an absence" % (INTAKE, problem))
    intake = read(root, INTAKE)
    if intake is None:
        others = sorted(n for n in ARTIFACT_FILES.values() if read(root, n) is not None)
        if others:
            # Decided 2026-07-25: this is a FAIL, not an absence. A directory
            # carrying dossier artifacts is a dossier; without the intake there is
            # no tier, so nothing can say which artifacts it owes, and reporting
            # NO-DATA here is what made deleting one file a bypass.
            return "FAIL", ("dossier artifacts are present (%s) but there is no %s, so no tier can be "
                            "established and nothing can say which artifacts this dossier owes; "
                            "run sbe_intake.py here" % (", ".join(others), INTAKE))
        return "NO-DATA", "no 00-intake.json; run sbe_intake.py to compute the tier"
    try:
        data = json.loads(intake)
    except ValueError:
        return "FAIL", "00-intake.json is not valid JSON"
    if not isinstance(data, dict):
        return "FAIL", "00-intake.json is not a JSON object"
    tier = data.get("tier")
    # A tier key that is PRESENT and names no tier is a BROKEN CLAIM, and it does
    # not share the recovery path below with a tier key that is simply ABSENT.
    # The two look alike one line further down (both fail `tier not in TIERS`)
    # and they mean opposite things: an absent key is a dossier that has not
    # declared its tier yet, which the re-derivation below can honestly repair
    # from the answers, while a present key holding "", null, "TBD", "TODO", a
    # comment, a zero-width space or NaN is a dossier ASSERTING a tier that says
    # nothing. Repairing that one silently is how the strongest sentence this
    # check owns came to be printed over it.
    #
    # MEASURED, 2026-08-17, and it was invisible until an unrelated fix removed
    # the failure that had been masking it: with `"tier": "TBD"` and the answers
    # intact, this check returned PASS carrying the evidence `tier T1: every
    # required artifact present, carrying content, ...`, BYTE-IDENTICAL to the
    # evidence for a properly declared T1. Eighteen hollow spellings of the
    # field all reached that same PASS. The honesty sweep had been reporting
    # them as caught for as long as the check happened to be failing for another
    # reason, which is the "passes for the wrong reason" class this project
    # keeps its own index of.
    #
    # Deliberately wider than the vacuity list: ANY present value that is not one
    # of the four tier strings is refused here. `answered()` reads the blank and
    # placeholder spellings but returns NaN and Infinity unchanged, because they
    # are not strings, and a float is no more a tier than "[TBD]" is. Naming the
    # class rather than enumerating its members is the same rule the report-print
    # lint follows, and for the same reason.
    if "tier" in data and tier not in TIERS:
        return "FAIL", ("00-intake.json declares \"tier\": %r, which names no tier (the tiers are "
                        "%s), so this dossier asserts a tier that says nothing rather than leaving "
                        "it to be computed; a declaration that records nothing is a broken claim, "
                        "not an absence. Delete the key to have the tier re-derived from the "
                        "answers, or rerun sbe_intake.py to write a real one"
                        % (tier, ", ".join(sorted(TIERS))))
    if tier not in TIERS:
        # MEASURED, and this was a hole big enough to drive the whole gate
        # through: an intake carrying the five maximum-risk answers and NO
        # "tier" key returned NO-DATA here, and NO-DATA does not block, so
        # `--strict` exited 0 over a dossier that owed seven artifacts and
        # carried one. Adding "tier": "T3" to the identical file exited 1.
        # Deleting one key silenced the entire design gate, and this
        # repository's own design/release-blockers/00-intake.json was in
        # exactly that state.
        #
        # The block below already refuses a tier that is WRONG by re-deriving
        # it from the answers. Refusing a tier that is ABSENT is the same
        # rule: if the answers are there, compute the tier and carry on, and
        # say the declaration was missing rather than passing over it. Only a
        # file that declares neither a tier nor answers is genuinely absent.
        answers_present = data.get("answers")
        if isinstance(answers_present, dict) and answers_present:
            tier = None  # carried into the re-derivation below, which decides
        else:
            return "NO-DATA", ("00-intake.json declares no tier and carries no answers, so nothing "
                               "here can say what this dossier owes; run sbe_intake.py")
    # The tier is RE-DERIVED from the answers stored beside it, never trusted as
    # written. Trusting the field made the whole dossier requirement two keystrokes
    # away: editing "T3" to "T0" cleared every artifact, silently, with the gate
    # still printing PASS. L15's rule that a tier moved with a null reason is an
    # edit and not an override is enforced exactly here.
    answers = data.get("answers")
    if not isinstance(answers, dict) or not answers:
        return "NO-DATA", ("00-intake.json records tier %s but carries no answers, so the tier cannot be re-derived; "
                           "a tier nothing can recompute is a typed claim, not a computed one (rerun sbe_intake.py)" % tier)
    # Every one of the five questions has to be ANSWERED, not merely keyed. An
    # intake carrying all five keys with "" in them re-derived a tier out of five
    # blanks and the gate reported it as computed. compute_tier reads a blank as
    # a no, so a blanked intake silently computed a lower tier than the truth.
    # "none" is the shipped answer to "how many downstream consumers break if it
    # is wrong", so it is an answer here and nowhere else in this file. Carved
    # out at the call site rather than removed from the shared list, so a system
    # of record recorded as "none" is still the absence of one.
    unanswered = [k for k, _ in QUESTIONS
                  if answered(answers.get(k), allow=("none",)) is None]
    if unanswered:
        return "NO-DATA", ("00-intake.json records tier %s but leaves %s unanswered, so the tier "
                           "cannot be re-derived from a complete intake. A blank answer is not a "
                           "no, and neither is the word TBD; rerun sbe_intake.py"
                           % (tier, ", ".join(unanswered)))
    # Answered is not the same as readable. Every answer is now converted to the
    # meaning it records before any rule reads it, and an answer outside the
    # accepted vocabulary is a broken claim rather than an absence: the intake
    # says something, and nothing here can tell which thing. Reading these five
    # for truthiness meant "n" to every question computed the highest tier and
    # `consumers: "several"` computed the lowest, and this re-derivation, whose
    # entire job is to catch a hand-edited tier, recomputed the same wrong answer
    # and agreed with the file.
    _values, unreadable = read_answers(answers)
    if unreadable:
        return "FAIL", ("00-intake.json records tier %s but %s, so the tier cannot be re-derived "
                        "from answers this tool can read. An answer written in a vocabulary "
                        "nothing recognizes is a broken claim, not an absence; rerun sbe_intake.py"
                        % (tier, "; ".join(unreadable)))
    computed = compute_tier(answers)
    if tier is None:
        # No tier was declared, and the answers computed one. Use it, and say
        # so: a dossier judged against a computed tier is honest, a dossier
        # judged against nothing is the hole this replaced.
        tier = computed
    label = ""
    declared = data.get("override")
    if declared is not None and declared != tier:
        # sbe_intake.py writes this field, so something has to read it. A file
        # that declares an override to one tier and records another is not a
        # dossier anyone can audit.
        # The completing edit is NAMED: a reader who followed the old printed
        # instruction (set override and a reason) landed exactly here, and
        # the sentence told them two fields disagreed without saying which
        # one to change. An honest mistake gets the fix, not an accusation.
        return "FAIL", ("00-intake.json declares override %r but records tier %r; an override moves "
                        "the tier field itself, so complete the edit by setting \"tier\" to %r as "
                        "well, or clear \"override\" to keep tier %r. Two disagreeing tier fields "
                        "cannot be audited" % (declared, tier, declared, tier))
    if tier != computed:
        # A stored tier that differs from the computed one IS an override, and
        # L15 says an override sets BOTH fields. Only the reason was enforced, so
        # a file could move a tier with `override` null forever, and null is what
        # sbe_intake.py writes: the unenforced path was the default path. Both
        # fields are named here, because the author has to be told which one is
        # missing and what to write in it.
        reason = data.get("override_reason")
        missing = []
        if declared is None:
            missing.append("its override field is null, so nothing in the file declares the move")
        problem = _override_problem(reason)
        if problem:
            missing.append(problem)
        if missing:
            return "FAIL", ("00-intake.json says tier %s but its own answers compute %s, and %s; an "
                            "override sets BOTH fields and they must agree with each other, so this "
                            "file needs override: %r beside an override_reason of at least %d words "
                            "and %d characters. A tier moved with either field missing is an edit, "
                            "not an override (L15)"
                            % (tier, computed, "; ".join(missing), tier,
                               OVERRIDE_MIN_WORDS, OVERRIDE_MIN_CHARS))
        direction = "lowering" if TIERS.index(tier) < TIERS.index(computed) else "raising"
        label = ("; declared override %s the tier to %s from computed %s, reason: %s"
                 % (direction, tier, computed, reason.strip()))
    elif declared is not None:
        # The fields agree with each other and with the answers, so the override
        # exercised nothing. Said out loud, because a field carrying a tier reads
        # like a control that fired, and silence here would let a stale one keep
        # reading that way.
        label = ("; the override field records %s, which is the tier these answers compute, so it "
                 "moved no tier" % declared)
    need = required_artifacts(tier)
    if not need:
        # A tier that requires nothing gives this check nothing to open, and
        # "tier T0: every required artifact present" read exactly like a verified
        # T3 line at a glance. By the project's own law a claim of nothing is
        # still nothing, so it says so instead.
        return "NO-DATA", ("tier %s requires no artifact, so this check opened none and there is "
                           "nothing here it can vouch for%s" % (tier, label))
    wanted = [ARTIFACT_FILES[n] for n in need]
    # The ACCESS axis: an artifact that exists and cannot be read is neither
    # missing nor empty, and reporting it as "missing" is a false sentence about
    # a file that is sitting right there.
    unreadable = ["%s (%s)" % (n, read_problem(root, n)) for n in wanted if read_problem(root, n)]
    missing = [n for n in wanted if read(root, n) is None and not read_problem(root, n)]
    empty = [n for n in wanted if n not in missing and not read_problem(root, n)
             and not present(root, n)]
    # The content rule four of the seven artifacts never had: see
    # _coherence_problem. Only artifacts that are present and non-empty are asked,
    # because "this file is empty" and "this file is about something else" are two
    # different sentences and the first one is already printed above.
    unrelated, unmeasured = [], []   # unmeasured holds (artifact, why or "")
    # Every artifact PRESENT in the dossier, not only the ones this tier requires.
    # Passing `wanted` meant a T1 purpose brief coherent with the 04-technology-map
    # sitting beside it FAILed with the sentence "no substantive word in it appears
    # in any sibling artifact", about a sibling artifact it shares four words with.
    # A file the author wrote is part of this dossier whether or not the tier
    # obliged them to write it.
    siblings = [n for n in ARTIFACT_FILES.values() if read(root, n) is not None]
    for n in wanted:
        if n in missing or n in empty:
            continue
        problem = _coherence_problem(root, n, siblings)
        if problem is None:
            unmeasured.append((n, ""))
        elif isinstance(problem, tuple):
            unmeasured.append((n, problem[1]))
        elif problem:
            unrelated.append(problem)
    if missing or empty or unrelated or unreadable:
        parts = []
        if missing:
            parts.append("missing: %s" % ", ".join(missing))
        if unreadable:
            parts.append("present but not readable: %s (an artifact this check cannot open is a "
                         "broken claim, not an absent one)" % ", ".join(unreadable))
        if empty:
            parts.append("present but carrying no content of their own: %s (a zero-byte artifact, one "
                         "holding only headings and comments, one whose every line is a placeholder, "
                         "or one whose whole body is under %d words and %d characters, is the absence "
                         "of an artifact)"
                         % (", ".join(empty), ARTIFACT_MIN_WORDS, ARTIFACT_MIN_CHARS))
        parts.extend(unrelated)
        # label carries the override sentence (the written tier, the computed
        # tier, and the direction) exactly as the PASS and NO-DATA branches
        # above already append it. This branch fires right after an override
        # (a written tier whose required artifacts are then found missing),
        # so omitting label here was the one place a reader lost that context.
        return "FAIL", "tier %s requires %s; %s%s" % (tier, ", ".join(need), "; ".join(parts), label)
    # A refusal must never upgrade to a PASS. The tuple form of unmeasured is a
    # coherence requirement this check tried to measure and could not (a script
    # it cannot segment, or siblings in a different script); folding that into
    # a PASS whose leading clause asserts "naming subject matter the rest of
    # this dossier also names" asserted the very thing that was not measured,
    # and an all-stopword artifact could provoke it to hide the filler FAIL.
    # Everywhere else in this file unmeasured is NO-DATA, so it is here too.
    refused = [(n, why) for n, why in unmeasured if why]
    if refused:
        return "NO-DATA", ("tier %s: every required artifact is present and carrying content, but "
                           "coherence could not be measured for %s. A requirement this check could "
                           "not measure is not a satisfied one, so this is NO-DATA and never a "
                           "pass; the coherence of the named artifact(s) is human review%s"
                           % (tier, "; ".join("%s (%s)" % (n, why) for n, why in refused), label))
    notes = []
    for n, why in unmeasured:
        if not why:
            notes.append("%s, which this dossier has nothing else to be coherent with, so that "
                         "was not checked" % n)
    # The one place an OPTIONAL binding is checked: this is the path that would
    # otherwise PASS, which is exactly the shape scenario 23 exploits (a stale
    # dossier still reads complete). Everywhere else in this function already
    # returns FAIL or NO-DATA for its own reason, and a binding never upgrades
    # those; it can only downgrade a would-be PASS it disagrees with.
    bind_verdict, bind_note, bind_label = _binding_problem(root, data, tier)
    if bind_verdict:
        return bind_verdict, bind_note
    return "PASS", ("tier %s: every required artifact present, carrying content, and naming subject "
                    "matter the rest of this dossier also names%s%s%s"
                    % (tier,
                       "" if not notes else "; except %s" % "; ".join(notes),
                       label, bind_label))


# The marker as the templates actually ship it: inside an HTML comment. Matching
# the bare string anywhere meant a verification plan that CITED the check ("07
# asserts that no file still contains SBE-TEMPLATE-UNFILLED") FAILed with the
# false sentence "still the shipped template, unedited".
_MARKER_COMMENT = re.compile(r"(?s)<!--(?:(?!-->).)*?%s" % re.escape(UNFILLED_MARKER))


def check_placeholder(root):
    """A copied, unedited template is not a design. Reject the shipped marker."""
    found = {}
    blank = []
    unreadable = []
    for name in ARTIFACT_FILES.values():
        problem = read_problem(root, name)
        if problem:
            # A file this check could not open is a file it cannot call scanned,
            # and "none still carrying an unfilled-template marker" over a set
            # that silently excluded it is a completeness sentence over a
            # truncated set.
            unreadable.append("%s (%s)" % (name, problem))
            continue
        t = read(root, name)
        if t is None:
            continue
        # An empty file carries no unfilled marker, and neither does a file whose
        # every line says TODO. Both are the shipped template with the marker
        # deleted and nothing put in its place, which is the state this check
        # exists to name, so both are the absence of an artifact rather than a
        # clean scan of one. A file whose body was commented out is the same
        # state one edit later: nothing renders, so nothing was written. The
        # marker is searched in the RAW text first, because the templates ship
        # it inside a comment, and a template that still carries it is named as
        # the template it is rather than as a blank.
        if _MARKER_COMMENT.search(t):
            found[name] = t
        elif t.strip() == "" or all_vacuous(t) or not _substantive_lines(t):
            blank.append(name)
        else:
            found[name] = t
    if unreadable:
        return "FAIL", ("artifact(s) present that this check could not read: %s; a file that "
                        "cannot be opened cannot be scanned for the unfilled-template marker, so "
                        "passing over it would be reporting a clean scan of nothing"
                        % ", ".join(unreadable))
    if not found and not blank:
        return "NO-DATA", "no dossier artifacts here, so nothing to check for unfilled template sections"
    if blank:
        return "FAIL", ("artifact(s) with nothing written in them: %s. A zero-byte file, a file "
                        "whose every line under its headings is a placeholder, and a file whose "
                        "whole body sits inside HTML comments, carry no unfilled-template marker, "
                        "so passing them would be reporting a clean scan of nothing"
                        % ", ".join(sorted(blank)))
    unfilled = sorted(n for n, t in found.items() if _MARKER_COMMENT.search(t))
    if unfilled:
        return "FAIL", ("still the shipped template, unedited: %s; each carries its %s marker comment, which the template says to "
                        "delete once the section is your own design" % (", ".join(unfilled), UNFILLED_MARKER))
    return "PASS", "%d artifact(s) present, none still carrying an unfilled-template marker" % len(found)


_HEADING = re.compile(r"^(#+)\s*(.*)$")
# A setext underline: a line of = (h1) or of two-plus - (h2). GitHub renders
# `Entities` over `---` as a heading, and every heading reader here read ATX
# only, so the check told an author "no heading in 05-data-model.md names
# entities" about a file whose Entities heading was right there, and the
# printed advice told them to do the thing they had already done. Setext
# headings are rewritten to ATX before any heading pattern runs; the
# underline only counts when the line above reads as a paragraph line (not a
# bullet, table row, heading, fence or blank), so a horizontal rule and a
# table delimiter stay what they are.
_SETEXT_UNDERLINE = re.compile(r"^\s{0,3}(=+|-{2,})\s*$")


def _setext_to_atx(text):
    out = []
    for line in (text or "").splitlines():
        m = _SETEXT_UNDERLINE.match(line)
        prev = out[-1] if out else ""
        ps = prev.strip()
        if (m and ps and "|" not in ps
                and not ps.startswith(("#", "-", "*", "+", ">", "```", "~~~"))
                and not re.match(r"^\d+[.)]\s", ps)):
            out[-1] = "%s %s" % ("#" if m.group(1).startswith("=") else "##", ps)
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if (text or "").endswith("\n") else "")


_BOLD_LEAD = re.compile(r"^\s*(?:\*\*|__)\s*([^*_].*?)\s*(?::\s*)?(?:\*\*|__)\s*:?\s*(.*)$")
_COLON_LEAD = re.compile(r"^\s*([^\s#>|`*+\d-][^:|`]{0,58}?)\s*:\s*(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _lead_lines_to_atx(text, is_lead, inline_ok=True):
    """Bold and colon-terminated section leads read as the headings they are.

    A hurried honest author writes `Criteria:` or `**Criteria**` where the
    template writes `## Criteria`. Every section reader here read markdown
    headings only, so an artifact carrying every element the law asks for was
    told "no Criteria section" about a line beginning with exactly that word,
    and the teaching clause ("accepted as anything containing: options")
    was satisfied, as written, by the very line the check refused. The markup
    is not the section; the declared lead is. A line converts when its lead
    text matches the accepted heading vocabulary of the artifact being read
    (the caller's is_lead, the same vocabulary the FAIL sentences print), so
    `#`, bold, and a colon are three spellings of one declaration.

    inline_ok=False converts only whole-line leads (`Entities:` alone), never
    a lead with content after the colon, because the data-model artifact owns
    line forms this must not eat (`status: draft | placed | shipped` is a
    declared-states LINE, not a Status section). Fenced code is never
    touched: a Mermaid `Note over api: text` is diagram source, not a lead.
    """
    out = []
    fence = None
    for line in (text or "").splitlines():
        f = _FENCE.match(line)
        if f:
            fence = None if fence else f.group(1)
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        m = _BOLD_LEAD.match(line) or _COLON_LEAD.match(line)
        if (m and len(m.group(1).split()) <= 6 and is_lead(m.group(1))
                and (inline_ok or not m.group(2).strip())):
            out.append("## " + m.group(1).strip())
            if m.group(2).strip():
                out.append(m.group(2).strip())
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if (text or "").endswith("\n") else "")


def _with_leading_pipes(lines):
    """Table rows normalized to the leading-pipe form the parsers read.

    GitHub-flavored markdown makes leading and trailing pipes OPTIONAL: a
    header row over a `--- | ---` delimiter renders as a table either way, and
    every table reader here keyed on a leading "|", so an author who wrote
    exactly what the FAIL message asked for was refused with a message
    describing the form they had used. A block is a table when a line carrying
    a pipe sits over a delimiter row (only `|-: ` and spaces, with dashes);
    every row of that block gains the pipes the parsers expect. Rows already
    piped pass through untouched.
    """
    out = list(lines)
    for i in range(len(out) - 1):
        cur, nxt = out[i].strip(), out[i + 1].strip()
        if not cur or "|" not in cur or cur.startswith("|"):
            continue
        if nxt and not set(nxt) - set("|-: ") and "-" in nxt and "|" in nxt:
            j = i
            while j < len(out) and out[j].strip() and "|" in out[j]:
                row = out[j].strip()
                if not row.startswith("|"):
                    row = "| " + row
                if not row.endswith("|"):
                    row = row + " |"
                out[j] = row
                j += 1
    return out
# The four sections an ADR owes, and the words an author actually writes for
# each. The shipped template uses the first spelling of each, and SKILL.md L3
# names them, but an ADR written in plain English ("What we weighed", "Roads not
# taken", "What we are doing", "What this costs us", "When we would revisit
# this") failed all five checks on VOCABULARY while carrying every one of the
# things the law asks for. That is a gate rejecting correct work, which is how a
# gate gets switched off, and a gate that is off catches nothing at all. The rule
# is about the content under the heading; these are the ways of spelling the
# heading, listed here and in SKILL.md L3 so the set is reviewable and
# extensible rather than folklore.
_SECTION_WORDS = {
    "Criteria": ("Criteria", "Criterion", "What we weighed", "How we chose",
                 "How we decided", "What mattered", "Decision drivers",
                 "Deciding factors", "Forces"),
    "Decision": ("Decision", "What we are doing", "What we chose", "What we decided",
                 "What we will do", "Chosen option", "Chosen approach", "Our choice",
                 "The call"),
    "Consequences": ("Consequences", "What this costs", "Costs", "Costs and benefits",
                     "Implications", "What we give up", "Impact", "So what"),
    # "Flip condition" is this project's OWN name for the section (README's
    # dossier table, DIGEST, LAWS-REFERENCE and HOW-IT-WORKS all print it),
    # and the vocabulary refused it: an honest reader who wrote the phrase
    # the docs taught them was FAILed on spelling. The project's own term
    # belongs in the accepted set by construction.
    "What would flip this": ("What would flip this", "What would change our mind",
                             "What would reverse this", "When we would revisit this",
                             "Flip condition", "Flip conditions",
                             "Revisit", "Reconsider", "Reversal"),
}


def _heading_alternatives(spellings):
    """One regex per section, built from the spellings a reader is shown.

    Built rather than written twice, because the FAIL message names the accepted
    set and a set that drifts from the pattern it describes is a doc claim
    nothing recomputes. First person singular is accepted alongside the plural,
    and a trailing "this" or "it" is optional, so "What we weighed" and "What I
    weighed" and "When we would revisit" all land.
    """
    out = []
    for s in spellings:
        pat = re.escape(s.lower()).replace(r"\ ", r"\s+")
        pat = pat.replace(r"we\s+", r"(?:we|i)\s+")
        pat = re.sub(r"\\?\s*this$", r"(?:\\s+this)?", pat)
        out.append(pat)
    return "|".join(out)
# Matched anywhere in the heading line, not anchored to its first word: an author
# writes `## When would we revisit this?` as often as `## Revisit`.
_SECTION_ANCHORED = {label: r"^#+\s*(?:%s)" % _heading_alternatives(words)
                     for label, words in _SECTION_WORDS.items()}
_SECTION_PATTERNS = {label: r"^#+[^\n]*\b(?:%s)" % _heading_alternatives(words)
                     for label, words in _SECTION_WORDS.items()}
# The flip condition may also be a stated line with no heading of its own, and
# that fallback stays narrow on purpose: a heading is a declaration, a sentence
# in the middle of a paragraph is not, so only the explicit phrasings count.
_FLIP_IN_PROSE = r"what would flip|what would change (?:our|my|this) mind|what would reverse"
# "Rejected" anywhere in the heading, as a word, plus the plain-English ways of
# saying it. Requiring the heading to BEGIN with the word meant
# `### Option A (rejected): synchronous call` counted zero, and the convention
# was written down nowhere outside the template.
#
# And then the list was still an allow-list of the phrasings somebody had already
# tried. `## Alternatives considered` passed and `## Options considered` failed:
# synonyms, opposite verdicts. `## Considered options`, which is the heading the
# MADR template ships and therefore the single most common ADR heading in
# existence, failed outright, and the FAIL text listed the accepted FORMS and
# never the accepted WORDS, so the one thing the author needed was the one thing
# withheld. A gate nobody can predict is a gate people route around.
#
# Now: the head noun is enough. "Alternatives", "options" and "rejected" carry the
# meaning; whatever an author puts around them is theirs. The set is named here,
# is printed in full in the FAIL text, and is what the pattern is built from, so
# the message and the rule cannot drift apart.
REJECTED_HEADING_WORDS = (
    "rejected", "alternatives", "alternative", "options", "roads not taken",
    "not taken", "not chosen", "ruled out", "discarded", "dropped", "declined",
    "did not pick", "did not choose", "didn't pick", "didn't choose", "why not",
    # "Trade-offs considered" is how half the ADRs in the wild spell this
    # section, and it failed with 0 alternatives found.
    "trade-offs", "tradeoffs", "trade offs", "trade-off", "tradeoff",
)
_REJECTED_HEADING = re.compile(
    r"(?i)(?:%s)" % "|".join(r"\b%s\b" % w.replace(" ", r"\s+").replace("'", "'?")
                             for w in REJECTED_HEADING_WORDS))
# The ADR's accepted section vocabulary, as a lead predicate for
# _lead_lines_to_atx: a line opening with any accepted section spelling or
# any rejected-alternatives heading word is a section lead however it is
# marked up. Anchored at the lead's start so a prose sentence that merely
# mentions a section word ("The decision we made was late:") stays prose.
_ADR_LEAD_WORDS = re.compile(
    r"(?i)^(?:%s)" % "|".join(_heading_alternatives(w)
                              for w in _SECTION_WORDS.values()))


def _is_adr_lead(text):
    t = text.strip()
    return bool(_ADR_LEAD_WORDS.match(t) or _REJECTED_HEADING.match(t))


_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
# A numbered list is a list. `1.` and `2)` were not read as alternatives at all,
# while forty lines away the same file already accepts `^\d+[.)]\s+` as a
# relationship line, so one authoring form was recognised in one artifact and
# rejected in another for no stated reason.
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$")
# The forms an ADR may list its rejected alternatives in. Named here, printed in
# the FAIL text, and implemented by _rejected_alternatives: a gate that rejects
# three of the four ways an engineer writes this, with a message describing none
# of them, is a gate that gets argued with and then switched off.
ALTERNATIVE_FORMS = ("a bullet list (- or *)", "a numbered list (1. or 2))",
                     "one sub-heading per alternative, each with a body",
                     "one paragraph per alternative",
                     "a comparison table (one row per alternative; a row whose "
                     "verdict-like column says chosen is the decision, not an "
                     "alternative)")


# The verdict an alternative can carry ABOUT ITSELF, in its own text: one
# vocabulary for "this option lost" and one for "this option won", the same
# chosen vocabulary the comparison-table rule reads in a verdict cell. Read in
# EVERY authoring form (list item, table row, sub-heading body, paragraph),
# because the chosen-marker rule used to exist for table rows only, and a list
# bullet ending "Chosen." was counted as a rejected alternative whenever the
# Decision paraphrased instead of quoting: the rule was adopted at the call
# site where the bug was found and not at its siblings, which is this
# project's own named anti-pattern.
_ITEM_REJECTED = re.compile(
    r"(?i)\b(?:%s)\b" % "|".join(w.replace(" ", r"\s+").replace("'", "'?")
                                 for w in ("rejected", "ruled out", "not chosen",
                                           "not taken", "discarded", "dropped",
                                           "declined", "did not pick",
                                           "did not choose", "didn't pick",
                                           "didn't choose", "passed over")))
_CHOSEN_WORD = re.compile(r"(?i)\b(?:chosen|selected|accepted|picked|winner)\b")


def _rejected_alternatives(t):
    """Collect the listed ALTERNATIVES; deciding which are rejections is the
    caller's job, because only some carry a verdict of their own.

    Counting `^#+\\s*rejected` headings got both halves wrong: an ADR listing two
    real alternatives as bullets under one "Rejected alternatives" heading failed,
    while two empty "## Rejected" headings with nothing under either passed. That
    inverts the incentive the rule exists to create, teaching an author to add
    headings rather than alternatives. An alternative counts here only if it
    carries at least one non-empty line of its own.

    Four authoring forms, because three of the four ordinary ones FAILed while
    the ADRs carried everything the law asks for: bullets and numbered items count
    individually; a sub-heading per alternative counts once each, with its body as
    the reason; and prose counts one alternative per paragraph. An alternative
    still has to say why it lost, which is what the reviewability threshold below
    holds, and an empty heading still counts nothing.
    """
    found = []             # (form, full text, under_verdict_heading)
    table_chosen = [0]     # comparison-table rows whose verdict cell said chosen
    level = None           # heading depth of the open rejected section
    section_rejected = [False]   # the OPEN section's heading carries a rejection verdict
    blocks = []            # (title, items, body) for the section and each sub-heading

    def close():
        for _title, items, body in blocks:
            # A heading that itself states the verdict ("Rejected
            # alternatives", "Roads not taken", "Ruled out") marks everything
            # under it as explicitly rejected; a neutral heading ("Options
            # considered", "Trade-offs") declares a section and no verdict,
            # and its items are decided item by item at the call site.
            under = bool(section_rejected[0]
                         or (_title and _ITEM_REJECTED.search(_title)))
            if items:
                # UNTRUNCATED, here and in every collection branch below. The
                # collected text is the alternative's IDENTITY: a 60-character
                # cap at collection meant any option named longer than the cap
                # could never equal its own chosen phrase, so the CHOSEN bullet
                # re-entered the rejected count and the two-alternatives floor
                # was satisfiable by chosen-plus-one again, one adjective past
                # the shipped fixture. Truncation is for display sites only.
                found.extend(("item", x, under) for x in items)
                continue
            table, chosen_rows = _table_alternatives(body)
            table_chosen[0] += chosen_rows
            if table:
                found.extend(("table", x, under) for x in table)
                continue
            paragraphs, current = [], []
            for l in body:
                if l.strip():
                    current.append(l.strip())
                elif current:
                    paragraphs.append(" ".join(current))
                    current = []
            if current:
                paragraphs.append(" ".join(current))
            if not paragraphs:
                continue
            if _title is None:
                # The section's own body: one alternative per paragraph, which is
                # how an author who writes prose separates them.
                found.extend(("para", p, under) for p in paragraphs)
            else:
                # A sub-heading IS one alternative however many paragraphs it
                # takes to explain. Counting paragraphs here would let one
                # alternative satisfy the rule that asks for two.
                found.append(("heading", "%s: %s" % (_title, " ".join(paragraphs)), under))
        del blocks[:]

    for line in t.splitlines():
        h = _HEADING.match(line)
        if h:
            depth, title = len(h.group(1)), h.group(2).strip()
            if level is not None and depth > level:
                blocks.append((title, [], []))     # a sub-heading inside the section
                continue
            close()
            level = depth if _REJECTED_HEADING.search(title) else None
            section_rejected[0] = bool(level is not None and _ITEM_REJECTED.search(title))
            if level is not None:
                blocks.append((None, [], []))
            continue
        if level is None or not blocks:
            continue
        item = _LIST_ITEM.match(line)
        if item:
            blocks[-1][1].append(item.group(1))
        else:
            blocks[-1][2].append(line)
    close()
    return found, table_chosen[0]


def _table_alternatives(body):
    """Rejected alternatives written as rows of a comparison table.

    The data-model check reads tables and this one did not, so an ADR whose
    alternatives sat in an Option / Verdict / Why table failed with "only 1
    distinct rejected alternative(s)" over a section naming three. One row is
    one alternative; a row whose verdict-shaped column says the option was
    chosen is the decision, not an alternative, and is left out.
    """
    rows = [l.strip() for l in _with_leading_pipes(body) if l.strip().startswith("|")]
    if len(rows) < 3:
        return [], 0
    header = [c.strip() for c in rows[0].strip("|").split("|")]
    verdict_col = next((i for i, h in enumerate(header)
                        if re.search(r"(?i)verdict|decision|status|outcome|chosen", h)), None)
    out = []
    chosen_rows = 0
    for r in rows[2:]:
        if not set(r) - set("|-: "):
            continue
        cells = [c.strip() for c in r.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if (verdict_col is not None and verdict_col < len(cells)
                and _CHOSEN_WORD.search(cells[verdict_col])):
            # Counted, not merely skipped: a chosen row identifies the
            # decision among the listed options, which is what lets the
            # caller read the OTHER rows as considered-and-not-chosen.
            chosen_rows += 1
            continue
        # The first cell is the option's NAME and the rest is its reason, so
        # _alternative_name reads the name cell rather than the whole joined
        # row; untruncated, because the text is the row's identity.
        rest = " | ".join(c for c in cells[1:] if c)
        out.append("%s: %s" % (cells[0], rest) if rest else cells[0])
    return out, chosen_rows


def _sections(t):
    """(heading text, body lines) for every heading in a document, in order."""
    out = []
    current = None
    for line in t.splitlines():
        h = _HEADING.match(line)
        if h:
            current = (h.group(2).strip(), [])
            out.append(current)
        elif current is not None:
            current[1].append(line)
    return out


def _section_body(t, heading_pattern):
    """The lines under the heading matching a pattern, or None if there is none.

    A heading is not a section. `^#+\\s*criteria` matched `## Criteria` with
    nothing whatsoever under it, and the ADR check then reported "criteria,
    decision, consequences and flip condition present" over four empty headings.
    That is the empty-values defect in markdown: every key present, every value
    blank.

    Where more than one heading matches, the one with content wins. Matching the
    first one made the DOCUMENT TITLE `# 03. Architecture decision record` the
    Decision section, whose body is the blank line before `## Context`, and the
    check then FAILed a correct ADR with "the Decision heading is present with
    nothing under it". A section that exists lower down is the section.
    """
    matches = [(title, body) for title, body in _sections(t)
               if re.search(heading_pattern, "#" + title, re.I)]
    if not matches:
        return None
    for _title, body in matches:
        if [l for l in body if l.strip()]:
            return body
    return matches[0][1]


def _required_section(t, label, problems):
    # Anchored first, so a heading that merely mentions the word (a title, a
    # cross-reference) never shadows the real section; the looser match is the
    # fallback that lets an author write "When would we revisit this?".
    body = _section_body(t, _SECTION_ANCHORED[label])
    if body is None:
        body = _section_body(t, _SECTION_PATTERNS[label])
    if body is None:
        problems.append("no %s section (a #-heading, a bold line, or a colon-terminated lead; "
                        "its text is accepted as any of: %s)"
                        % (label, ", ".join(_SECTION_WORDS[label])))
    elif not [l for l in body if l.strip()]:
        problems.append("the %s heading is present with nothing under it, so it names nothing; a "
                        "heading is a promise of content, not content" % label)


# The decision's own text, wherever the ADR states it as a chosen option. MADR,
# the most common ADR template in the wild, lists EVERY considered option
# (including the winner) under "## Considered Options" and names the winner
# under "## Decision Outcome" as `Chosen option: "..."`. Counting every bullet
# under the options heading counted the DECISION as a rejected alternative, so
# the two-alternatives floor was satisfiable by chosen-plus-one, which is
# precisely the single-alternative ADR the floor exists to refuse, and the
# evidence sentence stated a count that was false about the artifact. The
# comparison table already leaves the chosen row out; the list form now obeys
# the same rule.
_CHOSEN_LINE = re.compile(r"(?im)^\s*(?:[-*]\s*)?chosen\s+(?:option|approach|alternative)"
                          r"\s*[:=]\s*(.+?)\s*$")


def _unwrap(t):
    """The document with soft-wrapped prose lines joined back into one line each.

    `_CHOSEN_LINE` ended at `\\s*$`, one PHYSICAL line, so a chosen-option name
    that wraps at ordinary prose width (66 characters did it) was compared as
    its first line only, never equalled the full name collected from the
    options list, and the chosen option re-entered the rejected count. A
    markdown soft wrap is a rendering artifact, not content: a non-blank line
    that opens no structure of its own (not a heading, not a list item, not a
    table row, not a fence marker) continues the line above it, so the scan
    reads the sentence the author wrote rather than the width they wrapped at.
    """
    out = []
    for line in t.splitlines():
        s = line.strip()
        if (out and s and out[-1].strip()
                and not _HEADING.match(line) and not _LIST_ITEM.match(line)
                and not s.startswith(("|", "```", "~~~"))
                and not _HEADING.match(out[-1])
                and not out[-1].lstrip().startswith(("```", "~~~"))):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(line)
    return "\n".join(out)


def _chosen_phrases(t):
    """Every phrase this ADR names as the chosen option, RAW.

    Raw, not folded: the caller compares the chosen option against collected
    alternatives by CONTENT IDENTITY (the full folded text and the folded
    name), so it needs the phrase itself, and truncating or folding here was
    half of how chosen-plus-one satisfied the floor again.
    """
    t = _unwrap(t)
    out = []
    for m in _CHOSEN_LINE.finditer(t):
        val = re.split(r"(?i),\s*because\b", m.group(1))[0].strip().strip("\"'*`").strip()
        if val:
            out.append(val)
    # The heading form: a Decision section spelled `## Chosen option` whose
    # first content line IS the chosen option's name.
    for title, body in _sections(t):
        if re.match(r"(?i)^\s*chosen\s+(?:option|approach|alternative)\b", title):
            line = next((l.strip() for l in body if l.strip()), "")
            val = re.split(r"(?i),\s*because\b", _plain(line))[0].strip().strip("\"'*`-: ").strip()
            if val:
                out.append(val)
    return [p for p in out if p]


_REJECT_MARKER = re.compile(
    r"(?i)^(?:we\s+|i\s+)?(?:%s)\b[:\s]*"
    % "|".join(r"%s" % w.replace(" ", r"\s+").replace("'", "'?")
               for w in REJECTED_HEADING_WORDS))


def _alternative_name(a):
    """An alternative's identity is its NAME: the text before its first colon,
    after any leading rejection marker ("Rejected:", "Not chosen:") is removed.

    A full MADR names each option once as a Considered Options bullet and once
    as a Pros and Cons sub-heading, and both survived derivation_fold as
    distinct texts, so each real rejection counted twice and the chosen option
    counted once: "5 distinct rejected alternatives" over two rejections and a
    decision. Two entries sharing a name are one alternative described twice;
    the marker is stripped first so "Rejected: microservices" and "Rejected:
    single script" are two names rather than one name spelled "rejected".
    """
    name = _REJECT_MARKER.sub("", a.strip()).split(":", 1)[0].strip()
    return derivation_fold(name) or derivation_fold(a)


def check_adr(root):
    problem = read_problem(root, ARTIFACT_FILES["03"])
    if problem:
        return "FAIL", ("%s is %s; an artifact that exists and cannot be read is a broken claim, "
                        "not an absent one" % (ARTIFACT_FILES["03"], problem))
    t = read(root, ARTIFACT_FILES["03"])
    if t is None:
        return "NO-DATA", "no 03-adr.md in this dossier"
    # The rendered text: a decision record whose alternatives or criteria sit
    # inside an HTML comment is a record that renders without them, and four
    # sibling functions in this file already read it that way. Setext headings
    # are headings (see _setext_to_atx).
    t = _lead_lines_to_atx(without_comments(_setext_to_atx(t)), _is_adr_lead)
    problems = []
    # Deduplicated by the shared rule, because `gate_numbers` learned that a
    # figure listed twice is one figure and this threshold did not: the same
    # rejected alternative pasted twice cleared "an ADR needs at least 2". With
    # the derivation fold, not the plain one, because a trailing period bought
    # a second alternative: two spellings of one sentence are one sentence
    # however they are punctuated.
    # One alternative, one count, however many RENDERINGS it has. Exact
    # identity first (the untruncated folded name), then a cross-rendering
    # merge: a comparison-table row restates an option the list or the
    # sub-headings already name, usually shortened ("Synchronous call" in the
    # table, the full name in the list), and counting both renderings counted
    # each real rejection twice. A table row whose name-words are a subset or
    # superset of a non-table alternative's name-words is the same option
    # written twice. Cross-form only, on purpose: two bullets of the same
    # list are two options even when one name extends the other, so honest
    # neighbouring options are never merged.
    collected, table_chosen = _rejected_alternatives(t)
    order, by_name, dupes = [], {}, 0
    for form, text, under_rej in collected:
        key = _alternative_name(text)
        if key in by_name:
            by_name[key][1].add(form)
            by_name[key][2] = by_name[key][2] or under_rej
            dupes += 1
        else:
            by_name[key] = [text, {form}, under_rej]
            order.append(key)
    merged = []           # [key, text, forms, wordset, under_verdict_heading]
    for key in order:
        text, forms, under_rej = by_name[key]
        kw = frozenset(key.split())
        hit = None
        for i, (mkey, _mtext, mforms, mw, _mu) in enumerate(merged):
            cross = ("table" in forms) != ("table" in mforms)
            if cross and kw and mw and (kw <= mw or mw <= kw):
                hit = i
                break
        if hit is None:
            merged.append([key, text, set(forms), kw, under_rej])
        else:
            merged[hit][2].update(forms)
            merged[hit][4] = merged[hit][4] or under_rej
            dupes += 1
    # The chosen option is the decision, not a rejected alternative, so it is
    # left out of the floor and out of the count, exactly as the comparison
    # table's chosen row already is. Compared by CONTENT IDENTITY on the
    # untruncated folded text and folded name (a 60-character collection cap
    # and a one-line chosen regex each let the chosen option re-enter the
    # count, so the floor was satisfiable by chosen-plus-one again); a
    # table-row rendering of the chosen option is excluded by the same
    # subset rule that merges renderings above.
    chosen = set()
    for c in _chosen_phrases(t):
        for reduced in (_alternative_name(c), derivation_fold(c)):
            if reduced:
                chosen.add(reduced)
    chosen_words = [frozenset(c.split()) for c in chosen]
    # The Decision's unquoted sentence counts too: a Decision reading "We take
    # the token bucket at the ingress (Envoy local rate limit)" names the
    # winner letter for letter, and the old rule (a quoted choice or a chosen
    # marker only) printed "the Decision section does not name which listed
    # option won" over a file a reader refutes by opening it, which is a false
    # evidence sentence, the same severity as a wrong verdict. A listed
    # option whose FULL folded name appears inside the Decision's own text is
    # the named choice, and only when exactly ONE listed option appears
    # there: a Decision that discusses several options by name has not told
    # this check which of them won. (A Decision that names only the LOSER
    # by full name is misread by this rule and is accepted as a limit; the
    # ordinary decision sentence names what was chosen.)
    decision_body = _section_body(t, _SECTION_ANCHORED["Decision"])
    if decision_body is None:
        decision_body = _section_body(t, _SECTION_PATTERNS["Decision"])
    if decision_body:
        dtext = derivation_fold(" ".join(l.strip() for l in decision_body if l.strip()))
        named = [m[0] for m in merged if m[0] and m[0] in dtext]
        if len(named) == 1:
            chosen.add(named[0])
            chosen_words.append(frozenset(named[0].split()))
    def _is_chosen(key, forms, kw):
        if key in chosen:
            return True
        if "table" not in forms:
            return False
        return any(kw and cw and (kw <= cw or cw <= kw) for cw in chosen_words)
    chosen_out = [m for m in merged if _is_chosen(m[0], m[2], m[3])]
    rest = [m for m in merged if not _is_chosen(m[0], m[2], m[3])]
    # THE LAW APPLIED TO THIS CHECK'S OWN COUNT: it may not assert what it did
    # not establish. Free-form markdown does not say which listed option won
    # and which lost, so an option counts as a REJECTION only when its status
    # is decided: its own text carries a rejection verdict (in any authoring
    # form), or the decision has been identified among the listed options (a
    # chosen marker in an option's own text, a chosen table row, or an option
    # naming the Decision's quoted choice), in which case the remaining
    # options are what the section's own heading declares them: considered
    # and not chosen. When no option carries a verdict and the Decision
    # paraphrases instead of quoting, the winner may be ANY of them, and the
    # honest verdict is NO-DATA naming that ambiguity, never a count. Three
    # rounds patched the chosen-exclusion one form at a time (a truncation
    # cap, a one-line regex, a table-only marker rule) and each patch left a
    # sibling form open; the rule is now about what is established, not
    # about which form a marker sits in.
    def _verdict_of(m):
        # An item's own text wins over its heading: a bullet marking itself
        # chosen under a "Rejected alternatives" heading is the decision.
        if _ITEM_REJECTED.search(m[1]):
            return "rejected"
        if _CHOSEN_WORD.search(m[1]):
            return "chosen"
        return "rejected" if m[4] else ""
    marked_rejected = [m for m in rest if _verdict_of(m) == "rejected"]
    marked_chosen = [m for m in rest if _verdict_of(m) == "chosen"]
    unmarked = [m for m in rest if not _verdict_of(m)]
    # Identification of the winner is per-option, not per-document: an
    # unmarked option may be reclassified as rejected only when the winner is
    # known to be a DIFFERENT listed option (a listed option matched the
    # Decision's choice, or marked itself chosen). A chosen TABLE row that
    # merges with no listed option (the winner called "Kafka" in the table
    # and "Message broker" in the list is ordinary writing) identifies a
    # winner that may BE one of the unmarked options under another name, so
    # it establishes nothing about them, and the old document-level boolean
    # consumed it as though it did: the floor was satisfied by counting the
    # decision itself as one of its own rejected alternatives.
    winner_identified = bool(chosen_out or marked_chosen)
    if winner_identified:
        decided, undecided = marked_rejected + unmarked, []
    else:
        decided, undecided = marked_rejected, unmarked
    alternatives = [m[1] for m in decided]
    excluded = len(chosen_out) + len(marked_chosen) + table_chosen
    chosen_note = ("" if not excluded else
                   "; %d option(s) identified as the decision (a chosen marker of their own, a "
                   "chosen table row, or the Decision's named choice) counted as the decision, "
                   "not as alternatives" % excluded)
    rejected = len(alternatives)
    undecided_note = ""
    if rejected < 2 and undecided:
        # The sentence claims what was TESTED, never a wider absence: the old
        # wording ("neither ... the Decision section names which listed
        # option won") was false over a Decision that named the winner
        # verbatim but unquoted, and false evidence is the defect class this
        # project exists to refuse.
        if table_chosen or chosen:
            undecided_note = ("%d option(s) are listed under a rejected-alternatives heading, but "
                              "only %d carry a rejection verdict of their own, and the chosen "
                              "option this ADR names matches no listed option by name, so this "
                              "check cannot tell which listed option won and does not assert a "
                              "count it cannot defend. Name the chosen option as it appears in "
                              "the options list, or mark each rejection in its own text "
                              "(Rejected: ..., ruled out, not chosen), and this becomes a "
                              "verdict" % (len(rest), rejected))
        else:
            undecided_note = ("%d option(s) are listed under a rejected-alternatives heading, but "
                              "only %d carry a rejection verdict of their own, and no chosen "
                              "marker, no quoted choice, and no listed option's full name "
                              "appears in the Decision section, so this check cannot tell a "
                              "chosen option from a rejected one and does not assert a count it "
                              "cannot defend. Mark each rejection in its own text (Rejected: "
                              "..., ruled out, not chosen), or name the chosen option in the "
                              "Decision (its listed name, or Chosen option: \"...\"), and this "
                              "becomes a verdict" % (len(rest), rejected))
    elif rejected < 2:
        problems.append("only %d distinct rejected alternative(s) found under a "
                        "rejected-alternatives heading%s%s; an ADR needs at least 2, each with at "
                        "least one line saying why it lost (an empty heading is not an "
                        "alternative, and the chosen option is the decision, not an alternative). "
                        "The heading (a #-heading, a bold line, or a colon-terminated lead) is "
                        "accepted as anything containing any of: %s. "
                        "Accepted forms under it: %s"
                        % (rejected,
                           "" if not dupes else " (%d duplicate line(s) counted once)" % dupes,
                           chosen_note,
                           ", ".join(REJECTED_HEADING_WORDS), "; ".join(ALTERNATIVE_FORMS)))
    # The FAIL text above promises "at least one line saying why it lost", and
    # `- a` satisfied it. An override reason carries a reviewability threshold;
    # so does this, and it is the same threshold.
    thin = [a for a in alternatives
            if _reviewability_problem(a, "rejection reason", ALTERNATIVE_MIN_WORDS,
                                      ALTERNATIVE_MIN_CHARS)]
    if thin and rejected >= 2:
        problems.append("%d rejected alternative(s) carry no reviewable reason (%s); an alternative "
                        "with no stated reason for losing is a heading, not a decision record"
                        % (len(thin), "; ".join(repr(a[:24]) for a in thin[:3])))
    _required_section(t, "Criteria", problems)
    _required_section(t, "Decision", problems)
    _required_section(t, "Consequences", problems)
    # The flip condition is accepted as a heading with content or, as before this
    # change, as a stated line anywhere in the document. Only the empty-heading
    # form is new, and only that form is rejected.
    flip = _section_body(t, _SECTION_ANCHORED["What would flip this"])
    if flip is None:
        flip = _section_body(t, _SECTION_PATTERNS["What would flip this"])
    if flip is None:
        if not [l for l in t.splitlines() if re.search(_FLIP_IN_PROSE, l, re.I) and l.strip()]:
            problems.append("no 'What would flip this' section; an ADR without it is a tombstone "
                            "(this heading is accepted as any of: %s)"
                            % ", ".join(_SECTION_WORDS["What would flip this"]))
    elif not [l for l in flip if l.strip()]:
        problems.append("the 'What would flip this' heading is present with nothing under it, so "
                        "the ADR names no condition that would reverse the decision")
    if problems:
        return "FAIL", "; ".join(problems + ([undecided_note] if undecided_note else []))
    if undecided_note:
        # The floor is the only open question and the artifact does not let
        # this check answer it: NO-DATA, which neither blocks the honest
        # hurried author nor lends the floor a count nothing established.
        return "NO-DATA", undecided_note
    # What this sentence may claim is what the threshold measured. It used to read
    # "rejected with a stated reason" over `- Synchronous ledger call` and
    # `- Nightly batch reconciliation`, two bullets that name two options and give
    # no reason at all: both clear two words and eight characters, and no rule
    # here can tell a reason from a longer name. So the count says what was
    # measured and names the part that is human review.
    undecided_left = ("" if not undecided else
                      "; %d listed option(s) carry no verdict of their own and were not counted"
                      % len(undecided))
    return ("PASS", "%d distinct rejected alternatives (each explicitly rejected in its own "
                    "text, or listed beside an identified chosen option), each carrying at "
                    "least %d words and %d characters of its own text (that the text says why "
                    "the option lost, rather than restating its name, is human review)%s%s%s, "
                    "and criteria, decision, consequences and flip condition each carry content"
                    % (rejected, ALTERNATIVE_MIN_WORDS, ALTERNATIVE_MIN_CHARS,
                       "" if not dupes else
                       " (%d duplicate line(s) counted once)" % dupes, chosen_note,
                       undecided_left))


# An entity name may carry a hyphen or a dot. `[A-Za-z_][\w ]*?` could match
# neither, so `payment-token` and `pii.profile`, both explicitly sourceless, were
# dropped from the set and the PASS line asserted "each with a system of record"
# over a set it had silently truncated. That is the same defect as a gate passing
# over an empty manifest, one function deeper.
#
# And a name may start with ANY letter, not an ASCII one. `[A-Za-z_]` with a
# Unicode `\w` continuation meant `Café` was a name while `Écriture` and `注文`
# were not: a dossier whose ubiquitous language is not ASCII-first FAILed two
# gates at once with messages instructing the author to do exactly what they had
# done, and an accented ghost participant slid through unread. The coherence
# extractor (`_TERM`, above) already crossed this bridge; the entity and diagram
# grammars now start at the same rule: `[^\W\d]` is "any letter or underscore"
# in Unicode terms, a property, not a range somebody typed.
_ENTITY_BULLET = re.compile(r"^\s*[-*]\s*([^\W\d][\w .\-]*?)\s*(?::(.*))?$")
_ENTITY_HEADING = re.compile(r"(?i)entit")
# The data-model and states section vocabulary as a lead predicate, whole-line
# leads only (see _lead_lines_to_atx inline_ok): `Entities:` and
# `Relationships:` declare their sections as surely as `##` does.
_DM_LEAD_WORDS = re.compile(r"(?i)^(?:entit\w*|relationships?|states|status(?:es)?|lifecycle)\b")


def _is_dm_lead(text):
    return bool(_DM_LEAD_WORDS.match(text.strip()))


_COMPONENT_LEAD_WORDS = re.compile(r"(?i)^components?\b")


def _is_component_lead(text):
    return bool(_COMPONENT_LEAD_WORDS.match(text.strip()))
# Markdown emphasis is presentation, not identity. `- **Order**: ...` and a
# table cell `| **Order** |` were invisible to patterns anchored on a letter,
# and the FAIL message then described the exact form the author had used.
# Paired ** __ * and backticks are unwrapped; single underscores are left
# alone, because identifiers carry them.
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)(\S(?:[^*`]*?\S)?)\1")


def _plain(s):
    return _EMPHASIS.sub(r"\2", s or "")


def _joined_bullets(lines):
    """Lines with each bullet's soft-wrapped continuation folded back onto it.

    A false failure, and the worst kind: an entity bullet wrapped at 80 columns
    lost the half of itself that named its system of record, and the check FAILed
    with the sentence "entity 'RefundEvent' has no system of record" about a
    bullet that named one on its second line. An evidence line asserting
    something the artifact contradicts is this project's own defect class
    inverted, and wrapping a long line is the ordinary thing an author does.

    A continuation is an indented, non-empty line that is not itself a bullet, a
    heading, a table row or a fence. Everything else passes through untouched, so
    a paragraph after a bullet list is still a paragraph.
    """
    out = []
    for line in lines:
        stripped = line.strip()
        is_continuation = (
            out and _BULLET.match(out[-1]) and line[:1] in (" ", "\t") and stripped
            and not _BULLET.match(line) and not stripped.startswith(("#", "|", "```", ">"))
            and not re.match(r"^\d+[.)]\s", stripped))
        if is_continuation:
            out[-1] = out[-1].rstrip() + " " + stripped
        else:
            out.append(line)
    return out


def _table_entities(lines):
    """Entities written as a markdown table row: first cell the name, the rest its meta.

    Relationships were readable as a table and entities were not, so a data model
    written as tables throughout FAILed with "no entity bullets found" over a
    document that names every entity and every system of record. The asymmetry was
    documented, which makes it a stated restriction rather than a silent one, and
    it is still a gate failing correct work over an authoring form the same file
    accepts one heading further down.

    The header row and its `---` separator are skipped, exactly as they are for
    relationships, and a row whose first cell is empty is not an entity.

    A COLUMN HEADED `System of record` applies to its column. Without that, the
    phrase had to be repeated inside every cell, so a five-entity table produced
    five identical FAILs and the eval certifying "or as table rows" was written
    as `| Customer | system of record: the CRM |`, a cell restating its own
    column header, which is a fixture shaped to the parser rather than to an
    author. The header is read, and a cell under a system-of-record column is
    read as the answer to that column.
    """
    out, row, header = {}, 0, []
    for line in _with_leading_pipes(list(lines)):
        s = line.strip()
        if not s.startswith("|"):
            continue
        row += 1
        cells = [c.strip() for c in s.strip("|").split("|")]
        if row == 1:
            header = cells
            continue
        if row == 2 or not set(s) - set("|-: "):
            continue        # separator, or a row with nothing in it
        name = cells[0]
        if not name or not re.match(r"^[^\W\d][\w .\-]*$", name):
            continue
        # Joined with the cell separator, so a value cannot run into the next
        # column: see the note beside _SOR.
        meta = []
        for i, c in enumerate(cells[1:], start=1):
            head = header[i] if i < len(header) else ""
            is_sor_column = bool(head and _SOR_HEADER.match(head))
            if not c:
                if is_sor_column:
                    # An empty cell under a system-of-record column is the
                    # DECLARED absence of one. Dropping it with the other empty
                    # cells let the word "owner" in a Notes cell answer for it,
                    # so a table whose ownership column was blank in every row
                    # passed as "each with a system of record".
                    meta.append("system of record: ")
                continue
            if is_sor_column and not _SOR.search(c):
                # The column says what this cell is. Restated in the phrase the
                # value test reads, so one rule reads both authoring forms.
                meta.append("system of record: %s" % c)
            else:
                meta.append(c)
        out[name] = " | ".join(meta)
    return out


def _entity_bullets(t):
    """(entities, declared): the entity set this document states, and how it was found.

    `declared` is True when a heading whose name contains "entit" scoped the set,
    which is the form L4 describes and the only form this check will assert an
    entity COUNT over.

    Scoped to those headings because "every bullet above the Relationships
    heading" made an honest `## Notes` list into two entities with no system of
    record and FAILed a correct data model. The fallback for a document that
    names no such heading (a model whose section is called "Conceptual model",
    say) used to read every bullet in `Name: description` form, which is the same
    defect wearing the other face: it read `- Rollout plan: we ship on Tuesday`
    as an entity, and the same list with an ownership phrase in it bought the
    sentence "2 entities, each with a system of record" over a file that
    declares no entity at all.

    So the fallback still reads, because a model without a formal Entities
    heading should not be exempt from the rule, but it reads only bullets that
    NAME THE SYSTEM THAT OWNS THEM, which is the one thing every entity bullet
    in this law has to carry and the one thing a note never does. What it read is
    printed in the verdict, and the verdict is never better than NO-DATA, because
    a set this function had to guess at is not a set the evidence line may count.
    """
    out = {}
    t = _plain(without_comments(t))
    body = re.split(r"(?im)^#+\s*relationships", t)[0]
    sections = []
    current = None
    for line in _joined_bullets(body.splitlines()):
        h = _HEADING.match(line)
        if h:
            current = [] if _ENTITY_HEADING.search(h.group(2)) else None
            if current is not None:
                sections.append(current)
            continue
        if current is not None:
            current.append(line)
    if sections:
        # Inside a section that declares itself to be about entities, EVERY
        # bullet is an entity claim, colon or not: a bullet with no colon is an
        # entity with no system of record, which is the defect, not a non-entity.
        for sec in sections:
            out.update(_table_entities(sec))
            for line in sec:
                m = _ENTITY_BULLET.match(line)
                if m and m.group(2) is None and _SOR.search(line):
                    # No colon, but the bullet names an owner in prose:
                    # `- Order. The OMS is the system of record.` used to read
                    # the WHOLE sentence as the entity name and then print
                    # "does not name the system that owns it" about a line
                    # naming one in plain sight. The name is the first
                    # sentence; the rest is its description.
                    m2 = re.match(r"^\s*[-*]\s*([^\W\d][\w.-]*(?:\s+[^\W\d][\w.-]*){0,3}?)"
                                  r"\s*[.;,]\s+(.*\S)\s*$", line)
                    if m2:
                        out[m2.group(1).strip()] = m2.group(2).strip()
                        continue
                if m:
                    out[m.group(1).strip()] = (m.group(2) or "").strip()
        return out, True
    for line in _joined_bullets(body.splitlines()):
        m = _ENTITY_BULLET.match(line)
        if m and m.group(2) is not None and _SOR.search(m.group(2)):
            out[m.group(1).strip()] = m.group(2).strip()
    return out, False


def _entities(t):
    """Every name this document offers as an entity, read the widest way.

    For TRACING (the diagram check and the coherence test), not for the data
    model check. The two want opposite guesses: a name tracing missed becomes a
    false orphan and a false FAIL over an honest diagram, while a name the data
    model check reads by mistake becomes an assertion about a bullet nobody wrote
    as an entity. So tracing keeps the wide fallback and the check that has to
    print a count uses `_entity_bullets`, which says which set it got and refuses
    to count a set it guessed at.
    """
    t = _lead_lines_to_atx(_plain(without_comments(_setext_to_atx(t))),
                           _is_dm_lead, inline_ok=False)
    ents, declared = _entity_bullets(t)
    if declared:
        return ents
    out = dict(ents)
    for line in _joined_bullets(re.split(r"(?im)^#+\s*relationships", t)[0].splitlines()):
        m = _ENTITY_BULLET.match(line)
        if m and m.group(2) is not None:
            out.setdefault(m.group(1).strip(), m.group(2).strip())
    return out


# How an entity is allowed to name the system that owns it. The rule is that the
# owning system is NAMED; the phrase used to name it was a hidden allow-list of
# one. "source of truth" is the most common synonym in the discipline and it
# FAILed with the sentence "entity 'Order' has no system of record" about a line
# that names one in plain sight, and the message never said what phrase it wanted.
# Named here, printed in the FAIL text, compiled below, longest phrase first so
# "owned by" wins over "owner".
SOR_PHRASES = ("system of record", "system of truth", "source of truth", "book of record",
               "authoritative source", "mastered by", "owned by", "owner", "sor")
_SOR_ALT = "|".join(re.escape(p).replace(r"\ ", r"\s+")
                    for p in sorted(SOR_PHRASES, key=len, reverse=True))
# The separator is optional so both "system of record: the CRM" and "system of
# record the CRM" are read. Substring matching alone let "no system of record
# known yet" through, because the phrase was present; the value is what the rule
# is actually about.
# The value stops at a cell boundary. Capturing `(.+)` ran a table row's
# system-of-record cell straight into its Notes cell, so `| Customer | TBD |
# core |` read as the value "TBD core", which is not a placeholder, and a
# nameless owner passed on the strength of an unrelated column.
_SOR = re.compile(r"(?i)(?<![\w-])(?:%s)(?![\w-])\s*[:=]?\s*([^|]+)" % _SOR_ALT)
# A table COLUMN whose heading is one of the phrases and nothing else.
_SOR_HEADER = re.compile(r"(?i)^\s*(?:%s)\s*$" % _SOR_ALT)
_NO_SOR = re.compile(r"(?i)\bno\s+(?:%s)(?![\w-])" % _SOR_ALT)
# The value may sit BEFORE the phrase: "The OMS is the system of record." names
# the OMS, and capturing only what follows the phrase left "." as the value, so
# an honest sentence FAILed as "names a system of record with no value".
_SOR_BEFORE = re.compile(
    r"(?i)\b((?:the\s+|our\s+|its\s+)?[^\W\d][\w.-]*(?:\s+[^\W\d][\w.-]*){0,4}?)"
    r"\s+(?:is|are|was|remains)\s+(?:the\s+|our\s+|its\s+)?(?:%s)(?![\w-])" % _SOR_ALT)
# The list of values that name the absence of an answer used to live HERE, as a
# private constant, and that is precisely how the fourth round of this defect
# happened: this file refused the token "todo" as a system of record while
# sbe_gate.py, which never imported it, accepted "TODO" as a pinned warehouse
# snapshot in the same run. It now lives in sbe_checks.VACUOUS_VALUES, imported
# by every tool, extended in one place. See the comment beside it there.
# A cardinality must stand as its own token. Without the guards, "one-to-many-ish"
# satisfied a substring test, so a sentence explicitly saying the cardinality was
# undecided cleared the gate that exists to decide it. The accepted notations are
# CARDINALITY_FORMS at the top of this file; this is what they compile to.
_CARD_END = r"(?:one|many|1|n|m|\*)"
_CARDINALITY = re.compile(
    r"(?ix)(?<![\w.*-])(?:"
    r"   %s \s*[-\s]?\s* to \s*[-\s]?\s* %s"      # one-to-many, 1-to-many, N to 1
    r" | %s \s*:\s* %s"                            # 1:N, N:1, 1:1, many:1
    r" | [0-9*]+ \s*\.\.\s* [0-9*]+"               # 1..*, 0..1 (UML multiplicity)
    r")(?![\w-])" % (_CARD_END, _CARD_END, _CARD_END, _CARD_END))
# Cardinality written as an English sentence: "An Order has many OrderLines,
# and every OrderLine belongs to exactly one Order" carries both ends and
# failed with a message listing every notation except the one people speak.
_CARD_PROSE = re.compile(
    r"(?ix)\b(?:has|have|holds|hold|contains|contain|owns|own)\s+"
    r"(?:exactly\s+|at\s+least\s+|at\s+most\s+)?(?:one|many|zero\s+or\s+more|one\s+or\s+more)\b"
    r"|\bbelongs?\s+to\s+(?:exactly\s+)?one\b"
    r"|\bexactly\s+one\b")


def _stated_value(v):
    """The value this text records, or "" when it names the absence of one.

    A thin wrapper over the shared predicate so the call sites here keep reading
    in strings rather than in None.

    Through answered_as with the derivation fold, not answered on the raw text,
    because this was the reduce-then-test ordering bug alive in one more place:
    a system of record written as `-- see above` is not blank and is not a
    single vacuity token, so it survived answered(), while the same project
    refused the identical string as a derivation. The fold strips comments,
    punctuation and case FIRST, and the vacuity test reads what is left.
    """
    return answered_as(v, derivation_fold) or ""


def check_data_model(root):
    problem = read_problem(root, ARTIFACT_FILES["05"])
    if problem:
        return "FAIL", ("%s is %s; an artifact that exists and cannot be read is a broken claim, "
                        "not an absent one" % (ARTIFACT_FILES["05"], problem))
    t = read(root, ARTIFACT_FILES["05"])
    if t is None:
        return "NO-DATA", "no 05-data-model.md in this dossier"
    # The rendered text, for the same reason as check_adr: a commented-out
    # entity list read as "2 entities, each with a system of record" while the
    # artifacts check called the same file the absence of an artifact. Emphasis
    # is unwrapped so a bolded entity name is still an entity name, and a
    # setext heading is a heading.
    t = _lead_lines_to_atx(_plain(without_comments(_setext_to_atx(t))),
                           _is_dm_lead, inline_ok=False)
    problems = []
    ents, declared_by_heading = _entity_bullets(t)
    # What this check had to guess at, said in the verdict rather than folded into
    # a count. See _entity_bullets: a Notes list read as an entity set is the same
    # defect as a PASS over an empty manifest, one function deeper.
    read_note = ("" if declared_by_heading else
                 "; no heading in %s names entities (a #-heading, a bold line, or an "
                 "`Entities:` lead), so the set above was read from the bullet(s) "
                 "here that name a system that owns them (%s), and a bullet naming none was left as "
                 "the prose it is. Put them under a heading whose name contains \"entit\" to have "
                 "this check read them as the entity set"
                 % (ARTIFACT_FILES["05"], ", ".join(sorted(ents)) or "none"))
    if not ents:
        problems.append(
            "no entity found: list each entity as a bullet, or as a row of a markdown "
            "table whose first column is the entity name, under a heading that names "
            "entities, above the Relationships heading"
            if declared_by_heading else
            "no entity found: no heading in %s names entities (a #-heading, a bold line, or an "
            "`Entities:` lead whose name contains "
            "\"entit\"), and no bullet above the Relationships heading names the system that owns "
            "it, so nothing here was read as an entity and a list of notes was left as notes. List "
            "each entity as a bullet, or as a row of a markdown table whose first column is the "
            "entity name, under a heading that names entities" % ARTIFACT_FILES["05"])
    for name, meta in ents.items():
        if domain_vacuous(name):
            # An entity called TBD is a note to the author. The PASS sentence
            # counts entities, so a placeholder counted as one. Scoped: an entity
            # name is DOMAIN CONTENT, so the words an engineer can honestly mean
            # (none, pending, unknown) are not placeholders here. See
            # sbe_checks.DOMAIN_WORDS.
            problems.append("entity %r names no entity; a placeholder in the entity list is counted "
                            "by the sentence 'N entities, each with a system of record'" % name)
            continue
        m = _SOR.search(meta)
        if not m or _NO_SOR.search(meta):
            problems.append("entity '%s' does not name the system that owns it (accepted as any of: "
                            "%s, as `<phrase>: the OMS` on the bullet, or as a table column headed "
                            "with one of them)" % (name, ", ".join(SOR_PHRASES)))
        else:
            value = _stated_value(m.group(1))
            if not value:
                # The name may sit before the phrase: "The OMS is the system of
                # record." is an owner named in the discipline's most ordinary
                # sentence shape.
                mb = _SOR_BEFORE.search(meta)
                value = _stated_value(mb.group(1)) if mb else ""
            if not value:
                problems.append("entity '%s' names a system of record with no value (%r); an undecided source is not a source"
                                % (name, m.group(1).strip()[:24]))
    # Relationships were read as "every bullet after the Relationships heading",
    # which had it wrong in both directions. A section heading with nothing under
    # it, and a section written as a markdown table, both produced zero inspected
    # lines and the PASS still said "every relationship carries cardinality": a
    # sentence about work nothing did. The section is now scoped to itself, both
    # authoring forms are read, and the count is in the verdict so a reader can
    # tell ten checked relationships from none.
    rel_body = _section_body(t, r"^#+\s*relationships")
    rels, er_rels, table_row = [], [], 0
    for line in (rel_body or []):
        # A Mermaid erDiagram under the Relationships heading IS a relationship
        # list, and its cardinality symbols are cardinality: refusing it told an
        # author to restate their own diagram as bullets.
        if _ER_LINE.search(line.strip()):
            er_rels.append(line.strip())
    # Folded for the same reason the entity bullets are: a relationship wrapped
    # at 80 columns kept its name on the first line and its cardinality on the
    # second, and FAILed as "has no cardinality" against a line that carried one.
    for line in _joined_bullets(_with_leading_pipes(rel_body or [])):
        s = line.strip()
        if re.match(r"\s*[-*]\s+", line) or re.match(r"^\d+[.)]\s+", s):
            rels.append(s)
        elif s.startswith("|"):
            table_row += 1
            if table_row > 2 and set(s) - set("|-: "):   # skip header and separator
                rels.append(s)
    for line in rels:
        if not (_CARDINALITY.search(line) or _CARD_PROSE.search(line)):
            problems.append("relationship '%s' names no cardinality this tool can read (accepted: "
                            "%s)" % (line[:48], "; ".join(CARDINALITY_FORMS)))
    rels = rels + er_rels
    if problems:
        # The truncation is named. Six of eight sourceless entities were printed
        # and the other two were dropped out of a message that reads as the whole
        # list, which is the same defect as a count over a set the tool shortened
        # itself.
        return "FAIL", ("; ".join(problems[:6])
                        + ("" if len(problems) <= 6 else
                           "; and %d more not shown" % (len(problems) - 6))
                        + (read_note if ents else ""))
    if not declared_by_heading:
        # Everything this check asks of an entity holds for the ones it read, and
        # it still cannot say it read the entity SET rather than a prose list. So
        # it says that, instead of counting. NO-DATA neither blocks the honest
        # author nor lends the count a sentence nothing earned.
        return "NO-DATA", ("%d bullet(s) name a system that owns them and were read as entities (%s), "
                           "each carrying one, and %d relationship line(s) read; but no heading in %s "
                           "names entities, so this check cannot say that set is the entity set rather "
                           "than a prose list, and it does not assert an entity count over one. Put "
                           "them under a heading whose name contains \"entit\" (a #-heading, a bold "
                           "line, or an `Entities:` lead) and this check reads them as the entity set"
                           % (len(ents), ", ".join(sorted(ents)), len(rels), ARTIFACT_FILES["05"]))
    if not rels:
        return "NO-DATA", ("%d entities, each with a system of record, but no relationship line was "
                           "found (%s), so nothing was checked for cardinality and this check cannot "
                           "say that every relationship carries one"
                           % (len(ents),
                              "no Relationships heading" if rel_body is None
                              else "the Relationships heading has nothing under it that reads as a "
                                   "relationship: list them as bullets, table rows, or an erDiagram"))
    return "PASS", ("%d entities, each with a system of record; %d relationship line(s) read, each "
                    "carrying cardinality" % (len(ents), len(rels)))


# The Mermaid diagram types this check knows, and what each one offers a
# traceability check. "nodes" means the type names things that must appear
# elsewhere in the dossier; "none" means it is a real, correct diagram that
# declares no such things, and reporting "a diagram artifact with no diagram in
# it" over one of those is a false failure. Two false failures were shipped: a
# `sequenceDiagram`, which the template itself tells a T2 author to write, and
# any flowchart using the ordinary `A[Customer] --> B[Order]` idiom.
DIAGRAM_TYPES = {
    "flowchart": "nodes", "graph": "nodes", "sequenceDiagram": "sequence",
    "erDiagram": "er", "classDiagram": "class", "stateDiagram": "state",
    # mindmap used to be read as a flowchart, which is a half-parse: the shaped
    # root matched and every bare-word child was dropped in silence, so two
    # undeclared children sat under a verdict saying "all traceable". A mindmap
    # names a node on every content line, so it gets a parser that reads every
    # content line.
    "stateDiagram-v2": "state", "mindmap": "mindmap",
    # C4 has its own statement grammar, `Person(alias, "Label")`, and reading it
    # as a flowchart made the Mermaid KEYWORDS `Person` and `System` into orphan
    # node names, so the canonical Mermaid dialect for the system-context diagram
    # that SKILL.md Phase 5 asks a T2 or T3 author to draw FAILed, inventing the
    # orphans it then reported. It gets a parser instead.
    "C4Context": "c4", "C4Container": "c4", "C4Component": "c4",
    "C4Dynamic": "c4", "C4Deployment": "c4",
    # block-beta names its blocks as bare identifiers on a line, which the
    # flowchart parser reads only when they carry a shape or an edge, so a real
    # diagram produced no nodes and was reported as "a diagram artifact with no
    # diagram in it".
    "block-beta": "block",
    "journey": "none", "gantt": "none", "pie": "none", "timeline": "none",
    "gitGraph": "none", "quadrantChart": "none", "requirementDiagram": "none",
    "sankey-beta": "none", "xychart-beta": "none",
}
# C4 statement keywords, by what each one contributes.
_C4_ELEMENT = {"Person", "Person_Ext", "System", "System_Ext", "SystemDb", "SystemDb_Ext",
               "SystemQueue", "SystemQueue_Ext", "Container", "Container_Ext", "ContainerDb",
               "ContainerDb_Ext", "ContainerQueue", "Component", "Component_Ext", "ComponentDb",
               "ComponentQueue", "Node", "Node_L", "Node_R", "Deployment_Node"}
_C4_REL = {"Rel", "BiRel", "Rel_U", "Rel_D", "Rel_L", "Rel_R", "Rel_Up", "Rel_Down",
           "Rel_Left", "Rel_Right", "Rel_Back", "RelIndex"}
_C4_GROUPING = {"Boundary", "Enterprise_Boundary", "System_Boundary", "Container_Boundary",
                "Node_Boundary", "Deployment_Node"}
_C4_CALL = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(\s*([^\W\d](?:[\w.]|-(?=[\w.]))*)\s*(?:,\s*\"([^\"]*)\")?")
_BLOCK_STATEMENTS = {"columns", "space", "block", "end", "style", "classDef", "class",
                     "click", "direction", "down", "up", "%%"}
_BLOCK_STATEMENTS_CF = frozenset(w.casefold() for w in _BLOCK_STATEMENTS)
_BLOCK_TOKEN = re.compile(r"(?<![\w.\-\[\"])([^\W\d](?:[\w.]|-(?=[\w.]))*)(?::\d+)?(?![\w.\-\[\"(])")
DIRECTIONS = {"LR", "RL", "TB", "TD", "BT"}
# Statement keywords that begin a line without naming a node. A token skipped for
# being one of these is REPORTED, never dropped: a diagram whose four nodes were
# all named after direction keywords lost four of five and the verdict still said
# "all traceable" over the one that survived.
_FLOW_STATEMENTS = {"subgraph", "end", "click", "style", "classDef", "class",
                    "linkStyle", "direction", "accTitle", "accDescr", "%%"}
_SEQ_STATEMENTS = {"activate", "deactivate", "note", "loop", "alt", "else", "opt",
                   "end", "par", "and", "rect", "autonumber", "title", "critical",
                   "break", "box", "link", "destroy", "%%"}
# Membership is tested CASE-FOLDED. Mermaid's own documentation writes notes as
# `Note over A,B: text`, and a case-sensitive set held lowercase "note" only,
# so the canonical spelling was dropped WITHOUT an entry in the skipped list
# while the lowercase spelling was named: the docstring's completeness promise
# for that list broke on the documentation's own example. One fold, all
# dialects, so the promise does not depend on how a keyword was capitalized.
_FLOW_STATEMENTS_CF = frozenset(w.casefold() for w in _FLOW_STATEMENTS)
_SEQ_STATEMENTS_CF = frozenset(w.casefold() for w in _SEQ_STATEMENTS)

_SHAPE = (r"\[\[[^\]\n]*\]\]|\[\([^)\n]*\)\]|\[/[^\]\n]*/\]|\[[^\]\n]*\]|"
          r"\(\(\([^)\n]*\)\)\)|\(\([^)\n]*\)\)|\([^)\n]*\)|"
          r"\{\{[^}\n]*\}\}|\{[^}\n]*\}|>[^\]\n]*\]")
# A node id carrying a label: the label is what a human reads, and it is what a
# data model calls the thing. Capturing only the id made `R[Refund] --> RL[RefundLine]`,
# the single most common Mermaid idiom there is, fail as two orphans named R and RL.
# The same no-trailing-hyphen identifier as _IDENT below, spelled out here
# because these three are defined before it: a flowchart `A--->B` minted a
# phantom `A-` through the same greedy class the sequence dialect had.
_FLOW_IDENT = r"[^\W\d](?:[\w.]|-(?=[\w.]))*"
_NODE_LABELLED = re.compile(r"(%s)\s*(%s)" % (_FLOW_IDENT, _SHAPE))
# `A & B --> C & D` is documented flowchart syntax for multi-edges, and only
# the arrow-ADJACENT identifier was read: {B, C} entered the node set and A and
# D were dropped with no entry in the skipped list, so an undeclared service in
# an outer position passed "all traceable" over a false node count. The
# identifier position is an &-separated LIST of identifiers, and every member
# is a node.
_AMP_LIST = r"%s(?:\s*&\s*%s)*" % (_FLOW_IDENT, _FLOW_IDENT)
_NODE_SOURCE = re.compile(r"(%s)\s*(?:--|==|-\.|~~)" % _AMP_LIST)
_NODE_DEST = re.compile(r"(?:--+>|--+|==+>|-\.-*>|~~+)\s*(?:\|[^|]*\|\s*)?(%s)" % _AMP_LIST)
_AMP_SPLIT = re.compile(r"\s*&\s*")
# `A -- places --> B`: the words of an inline edge label are not nodes.
_INLINE_EDGE_LABEL = re.compile(r"--\s*[^-|>\n]+?\s*(--+>|--+)")


def _strip_edge_labels(s, skipped):
    """Drop inline edge labels, NAMING each one dropped.

    An edge label is not a node and discarding it is right: L5 says the parser
    that touches edge labels exists to discard their words so they are not
    mistaken for nodes. It discarded them in silence, though, and the same law
    promises that every token read as syntax rather than as a node is named in
    the evidence line, on every verdict. `C[Customer] -- Ledger --> O[Order]`
    dropped "Ledger" out of the set the verdict then reported as complete, which
    is the truncated-set defect that sentence exists to prevent.
    """
    def sub(m):
        label = m.group(0)[2:len(m.group(0)) - len(m.group(1))].strip()
        if label:
            skipped.append("%s (an inline edge label, not a node)" % label)
        return " %s " % m.group(1)
    return _INLINE_EDGE_LABEL.sub(sub, s)
# ONE identifier grammar for every dialect. Seven patterns spelled a node id as
# `[A-Za-z_]\w*` while the flowchart, C4 and block-beta patterns beside them,
# and the data model's own entity rule, all accepted hyphens and dots. Hyphens
# are the dominant naming convention in backend and infrastructure work, so the
# mismatch failed in both directions at once: `participant feed-poller` was
# silently dropped (a hyphenated orphan then PASSed as "all traceable", and an
# honest hyphenated participant was never read), `in-transit -->
# out-for-delivery` was shredded into the invented orphans `in`, `transit`,
# `out` and `delivery`, and `class feed-poller` was truncated to a node called
# `feed`. L4's own worked example (`payment-token`, `pii.profile`) could not be
# drawn in an erDiagram of itself.
#
# And then the cure ate the arrows. `[\w.-]*` is greedy and allows a TRAILING
# hyphen, so in `Beta-->>Alpha:` the identifier consumed `Beta-`, the arrow
# still matched on the remaining `->>`, and a phantom node named `Beta-` was
# minted and reported as an orphan: every two-dash arrow in every dialect
# rejected the most ordinary reply idiom in Mermaid. A hyphen may only sit
# BETWEEN word characters, never at the end, so the identifier grammar and the
# arrow grammar are disjoint by construction in all dialects at once.
_IDENT = r"[^\W\d](?:[\w.]|-(?=[\w.]))*"
# erDiagram relationship line: ENTITY <cardinality> ENTITY : label
# Cardinality tokens (||--o{, }o--||, ||--||, }|..|{, ...) are built from the
# characters | o { } . and dash, and at least one of them is never a dash: a run
# of plain dashes between two identifiers ending in a colon is a markdown bullet
# on the line after a heading, which is how `## Components` followed by
# `- OrderQueue: ...` invented an entity called Components. The whitespace
# around the cardinality keeps the identifier's own hyphens and dots out of it.
_ER_LINE = re.compile(r"(%s)\s+[|o{}.\-]*[|o{}][|o{}.\-]*\s+(%s)\s*:" % (_IDENT, _IDENT))
_ER_BLOCK = re.compile(r"^\s*(%s)\s*\{" % _IDENT)
_SEQ_PARTICIPANT = re.compile(r"^\s*(?:participant|actor)\s+(%s)(?:\s+as\s+(.+?))?\s*$" % _IDENT)
# Mermaid's create/destroy participant syntax (v10.3+): `create participant A
# as audit-worker` matched neither the participant pattern (the line starts
# with "create") nor the message pattern, so the ALIAS was lost, A entered the
# node set via its messages with no label, could not trace to the declared
# component, and an honest documented diagram FAILed naming a node id the
# author never intended to trace, with the cause named nowhere.
_SEQ_CREATE = re.compile(r"^\s*create\s+(?:participant|actor)\s+(%s)(?:\s+as\s+(.+?))?\s*$"
                         % _IDENT)
# Every arrow form the sequence dialect ships: solid/dashed (- / --), head
# (>, >>, x, ), and the +/- activation suffix. The suffix was unmatched, so
# `B-->>-A: resp`, the canonical deactivation reply in the official docs,
# parsed as no message at all, and an undeclared service sitting in one passed
# "all traceable".
_SEQ_MESSAGE = re.compile(r"^\s*(%s)\s*<?-{1,2}[>x)]{1,2}[+-]?\s*(%s)\s*:" % (_IDENT, _IDENT))
_CLASS_DECL = re.compile(r"^\s*class\s+(%s)" % _IDENT)
_CLASS_REL = re.compile(r"(%s)\s*[<*o|]?[-.]{2,}[>*o|]*\s*(%s)" % (_IDENT, _IDENT))
_STATE_EDGE = re.compile(r"(\[\*\]|%s)\s*-->\s*(\[\*\]|%s)" % (_IDENT, _IDENT))
# Diagrams are code, in a fenced block. Reading the whole file meant any prose
# containing an arrow became a diagram node, so the traceability check reported
# orphans that were sentences.
_FENCE = re.compile(r"(?s)```[^\n]*\n(.*?)```")


def _label_text(raw):
    return raw.strip("[](){}<>/\\ ").strip("\"'").strip()


def _diagram_nodes(t):
    """(nodes, kinds, skipped, states, unread) for every fenced diagram in an artifact.

    nodes maps a node id to the label written on it, because a node is traceable
    by either. kinds lists the diagram types declared. skipped names every token
    this parser deliberately did not treat as a node, and WHY, because a token
    dropped in silence is a completeness claim over a truncated set. states is
    the subset of node ids that came out of a state diagram, because a state is
    neither an entity nor a runtime component and holding it to either was a
    false failure with no documented way out.

    unread is the fifth channel and it is a CONFESSION, not a skip: a line this
    parser could not read as a node, a statement or an attribute. A skipped
    token is one the parser READ and deliberately set aside; an unread line is
    one it could not read at all, and a ghost service can be hiding in it, so
    check_diagrams refuses the "all traceable" sentence whenever one exists.
    The dropped-in-silence `continue` this replaces is how an accented sender
    made a whole message line vanish, ghost included, under a PASS.
    """
    # HTML comments are not diagram source. Left in, the "-->" that closes one
    # reads as a Mermaid edge and invents a node out of the next word.
    t = without_comments(t)
    nodes, kinds, skipped, states, unread = {}, [], [], set(), []

    def add(nid, label=""):
        if nid in DIRECTIONS and not label and nid not in nodes:
            # Only reachable when a direction word stands completely alone; a
            # node genuinely named LR carries a shape or an edge and lands above.
            skipped.append("%s (a layout direction on its own line)" % nid)
            return
        if nid not in nodes or (label and not nodes[nid]):
            nodes[nid] = label

    for block in _FENCE.findall(t):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0].strip()
        first = head.split()[0]
        kind = DIAGRAM_TYPES.get(first)
        if kind is None:
            # Read as a flowchart, an unrecognised dialect turned its own
            # statement keywords into orphan node names and FAILed a correct
            # diagram. SKILL.md L5's own rule is that a type this tool cannot
            # trace is NO-DATA and not a failure, so it is not read, it is
            # reported, and check_diagrams turns it into NO-DATA.
            kinds.append("unrecognised:%s" % first)
            skipped.append("%s (a diagram type this tool has no parser for, so nothing in this "
                           "block was read as a node)" % first)
            continue
        else:
            kinds.append(first)
            body = lines[1:]
            rest = head.split()[1:]
            # The declaration line is where flowchart/graph/LR/TD live. Stripping
            # them from EVERY line is what deleted nodes named after them; they
            # are stripped here, once, from the one line that declares the
            # diagram, and named in the report.
            skipped.append("%s%s (the diagram declaration: type%s)"
                           % (first, (" " + " ".join(rest)) if rest else "",
                              " and direction" if rest else ""))
        in_body_block = 0     # brace depth inside an erDiagram/classDiagram body
        for line in body:
            s = line.strip()
            word = s.split()[0] if s.split() else ""

            def could_not_read():
                # The confession channel: only lines that could name something.
                if re.search(r"\w", s):
                    unread.append("%s (a %s line this parser could not read as a node or "
                                  "statement)" % (s[:48], first))
            if kind == "sequence":
                m = _SEQ_CREATE.match(s)
                if m:
                    # Checked BEFORE the statement set: `create participant A
                    # as audit-worker` carries the alias, and losing it made
                    # an honest documented diagram fail on an id the author
                    # never meant to trace.
                    add(m.group(1), (m.group(2) or "").strip())
                    continue
                if word.casefold() in _SEQ_STATEMENTS_CF or s.startswith("%%"):
                    skipped.append("%s (a %s statement, not a participant)" % (word, first))
                    continue
                m = _SEQ_PARTICIPANT.match(s)
                if m:
                    add(m.group(1), (m.group(2) or "").strip())
                    continue
                m = _SEQ_MESSAGE.match(s)
                if m:
                    add(m.group(1))
                    add(m.group(2))
                    continue
                could_not_read()
                continue
            if kind == "er":
                if in_body_block:
                    in_body_block += s.count("{") - s.count("}")
                    if re.search(r"\w", s):
                        skipped.append("%s (an attribute inside an erDiagram entity block, "
                                       "not a node)" % word)
                    continue
                m = _ER_BLOCK.match(s)
                if m:
                    add(m.group(1))
                    in_body_block = 1
                    continue
                m = _ER_LINE.search(s)
                if m:
                    add(m.group(1))
                    add(m.group(2))
                    continue
                could_not_read()
                continue
            if kind == "class":
                if in_body_block:
                    in_body_block += s.count("{") - s.count("}")
                    if re.search(r"\w", s):
                        skipped.append("%s (a member inside a classDiagram body, not a node)"
                                       % word)
                    continue
                m = _CLASS_DECL.match(s)
                if m:
                    add(m.group(1))
                    if s.rstrip().endswith("{"):
                        in_body_block = 1
                    continue
                m = _CLASS_REL.search(s)
                if m:
                    add(m.group(1))
                    add(m.group(2))
                    continue
                could_not_read()
                continue
            if kind == "state":
                found = False
                for m in _STATE_EDGE.finditer(s):
                    found = True
                    for g in (m.group(1), m.group(2)):
                        if g != "[*]":
                            add(g)
                            states.add(g)
                if found:
                    continue
                m = re.match(r"^state\s+(?:\"[^\"]*\"\s+as\s+)?(%s)" % _IDENT, s)
                if m:
                    add(m.group(1))
                    states.add(m.group(1))
                    continue
                if re.fullmatch(_IDENT, s):
                    # A bare identifier on its own line declares a state, the
                    # way it declares a node in a flowchart. Dropping it in
                    # silence hid undeclared states from the orphan check.
                    add(s)
                    states.add(s)
                    continue
                if word.casefold() in ("note", "direction", "end") or s.startswith(
                        ("%%", "}", "--")):
                    skipped.append("%s (a %s statement, not a state)" % (word, first))
                    continue
                could_not_read()
                continue
            if kind == "mindmap":
                if s.startswith("::") or word.startswith("::icon"):
                    skipped.append("%s (a %s style statement, not a node)" % (word, first))
                    continue
                m = _NODE_LABELLED.search(s)
                if m:
                    add(m.group(1), _label_text(m.group(2)))
                elif s.strip("()[]{}"):
                    # A bare line IS a node in a mindmap. Reading only the
                    # shaped root and dropping the children was a half-parse:
                    # the verdict said "all traceable" over children it never
                    # read.
                    add(_label_text(s))
                continue
            if kind == "c4":
                m = _C4_CALL.match(s)
                if not m:
                    if word and any(ch.isalnum() for ch in word):
                        skipped.append("%s (a %s statement, not an element)" % (word, first))
                    continue
                keyword, alias, label = m.group(1), m.group(2), (m.group(3) or "").strip()
                if keyword in _C4_GROUPING:
                    # A boundary groups elements, exactly as `subgraph` does in a
                    # flowchart, and a flowchart's subgraph is skipped and named.
                    skipped.append("%s(%s) (a %s boundary, which groups elements rather than "
                                   "being one)" % (keyword, alias, first))
                    continue
                if keyword in _C4_ELEMENT:
                    add(alias, label)
                    continue
                if keyword in _C4_REL:
                    args = [a.strip().strip("\"") for a in s[s.find("(") + 1:].split(",")]
                    for a in args[:2]:
                        if re.match(r"^[^\W\d][\w.-]*$", a):
                            add(a)
                    continue
                skipped.append("%s (a %s statement, not an element)" % (keyword, first))
                continue
            if kind == "block":
                if word.split(":")[0].casefold() in _BLOCK_STATEMENTS_CF or s.startswith("%%"):
                    skipped.append("%s (a %s statement, not a block)" % (word, first))
                    continue
                s = _strip_edge_labels(s, skipped)
                for m in _NODE_LABELLED.finditer(s):
                    add(m.group(1), _label_text(m.group(2)))
                bare = _NODE_LABELLED.sub(" ", s)
                for m in _BLOCK_TOKEN.finditer(bare):
                    if m.group(1).casefold() not in _BLOCK_STATEMENTS_CF:
                        add(m.group(1))
                continue
            if kind == "none":
                continue
            # flowchart, graph and the node-shaped types
            if word.casefold() in _FLOW_STATEMENTS_CF or s.startswith("%%"):
                skipped.append("%s (a %s statement, not a node)" % (word, first))
                continue
            s = _strip_edge_labels(s, skipped)
            found = False
            for m in _NODE_LABELLED.finditer(s):
                add(m.group(1), _label_text(m.group(2)))
                found = True
            bare = _NODE_LABELLED.sub(" ", s)
            for m in _NODE_SOURCE.finditer(bare):
                found = True
                for part in _AMP_SPLIT.split(m.group(1)):
                    add(part)
            for m in _NODE_DEST.finditer(bare):
                found = True
                for part in _AMP_SPLIT.split(m.group(1)):
                    add(part)
            if found:
                continue
            if re.fullmatch(_FLOW_IDENT, s):
                # A bare identifier on its own line declares a node. Dropping
                # it in silence hid an undeclared service that stood alone.
                add(s)
                continue
            could_not_read()
    return nodes, kinds, sorted(set(skipped)), states, sorted(set(unread))


_COMPONENT_HEADING = re.compile(r"(?i)component|runtime|technology map")
_STATE_HEADING = re.compile(r"(?i)state|status|lifecycle")


def _norm(s):
    # By property, not by ASCII range: `[^a-z0-9]` erased every non-ASCII
    # letter, so two Japanese entity names both normalized to the empty string
    # and a ghost written in ANY non-Latin script "traced" to whatever else
    # normalized to nothing. A name's identity keeps its letters and digits in
    # every script and drops punctuation, spacing and case in every script.
    return re.sub(r"[\W_]", "", s.casefold())


def _declared_components(root):
    """Runtime components a diagram is allowed to name, and where each was declared.

    Requiring every diagram node to be an ENTITY in 05-data-model.md failed the
    diagrams the template itself asks for at T3 (system context, container view,
    technology map, failover topology), which are made of services, queues and
    external systems. The published escape was to add the services to the data
    model as entities, which teaches an author to corrupt the conceptual model to
    satisfy a diagram check. So runtime components get their own declared place:
    the first column of the tables in 04-technology-map.md, and bullets under a
    Components heading in 06-diagrams.md itself. Declared somewhere and traceable,
    without pretending a queue is an entity.
    """
    out = {}
    tech = read(root, ARTIFACT_FILES["04"])
    if tech is not None:
        tech = _plain(without_comments(_setext_to_atx(tech)))
        in_table = 0
        for line in _with_leading_pipes(tech.splitlines()):
            s = line.strip()
            if not s.startswith("|"):
                in_table = 0
                continue
            in_table += 1
            if in_table <= 2:      # header row and its separator
                continue
            cell = s.strip("|").split("|")[0].strip()
            if cell:
                out.setdefault(_norm(cell), "%s: %s" % (ARTIFACT_FILES["04"], cell))
    # Bullets under a component-naming heading count in EITHER file, which is
    # what L5's INPUTS line promises: a technology map written as bullets is
    # the honest form half the discipline uses, and reading bullets only out of
    # 06-diagrams.md made the law overclaim against its own tool.
    for artifact in (ARTIFACT_FILES["04"], ARTIFACT_FILES["06"]):
        text = read(root, artifact)
        if text is None:
            continue
        text = _lead_lines_to_atx(_plain(without_comments(_setext_to_atx(text))),
                                  _is_component_lead, inline_ok=False)
        in_components = False
        for line in text.splitlines():
            h = _HEADING.match(line)
            if h:
                in_components = bool(_COMPONENT_HEADING.search(h.group(2)))
                continue
            if not in_components:
                continue
            m = _BULLET.match(line)
            if m:
                name = m.group(1).split(":")[0].strip()
                if name:
                    out.setdefault(_norm(name), "%s: %s" % (artifact, name))
    return out


def _declared_states(root):
    """Lifecycle states a state diagram is allowed to name, and where each was declared.

    The third traceability target, and it exists because the second one was
    being abused. A `stateDiagram-v2` of an entity's lifecycle FAILed by default:
    a state is not an entity and not a runtime component, no document anywhere
    told an author how to make one trace, and the only way through was to declare
    Draft, Placed and Shipped as runtime COMPONENTS. That is exactly the
    pathology L5 removed for queues (corrupting a model to please a tool),
    reproduced one diagram type over.

    A state is declared as a bullet under a heading naming states, statuses or a
    lifecycle, in 05-data-model.md or in 06-diagrams.md itself, or in a
    `status: draft | placed | shipped` line in the data model. Declared
    somewhere and traceable, without pretending a state is a service.
    """
    out = {}
    for artifact in (ARTIFACT_FILES["05"], ARTIFACT_FILES["06"]):
        text = read(root, artifact)
        if text is None:
            continue
        text = _lead_lines_to_atx(without_comments(_setext_to_atx(text)),
                                  _is_dm_lead, inline_ok=False)
        in_states = False
        for line in _joined_bullets(text.splitlines()):
            h = _HEADING.match(line)
            if h:
                in_states = bool(_STATE_HEADING.search(h.group(2)))
                continue
            m = re.match(r"(?i)^\s*[-*|]?\s*(?:status|state|lifecycle)\s*(?:values)?\s*[:=]\s*(.+)$",
                         line)
            if m:
                for token in re.split(r"[,|/]| or | then |->|-->", m.group(1)):
                    name = token.strip().strip(".;`").strip()
                    if name and not domain_vacuous(name):
                        out.setdefault(_norm(name), "%s: %s" % (artifact, name))
                continue
            if not in_states:
                continue
            b = _BULLET.match(line)
            if b:
                name = b.group(1).split(":")[0].strip()
                if name and not domain_vacuous(name):
                    out.setdefault(_norm(name), "%s: %s" % (artifact, name))
    return out


def _system_of_record_names(t):
    """Names the data model declares as an entity's system of record.

    A dossier that writes "system of record is the orders Postgres database
    (orders_db)" has DECLARED orders_db, twice over, and the diagrams check
    used to print "appear nowhere else in the dossier" about exactly that
    node: a lead clause asserting a whole-dossier absence over a search that
    read entity bullets and component declarations only. The owning system an
    entity names is a declaration a reader can find, so a diagram node
    matching a word of it traces. Placeholder words are not names here, as
    everywhere.
    """
    out = {}
    if t is None:
        return out
    tt = _lead_lines_to_atx(_plain(without_comments(_setext_to_atx(t))),
                            _is_dm_lead, inline_ok=False)
    ents, _declared = _entity_bullets(tt)
    for name, meta in ents.items():
        m = _SOR.search(meta or "")
        val = m.group(1) if m else ""
        if not _stated_value(val):
            mb = _SOR_BEFORE.search(meta or "")
            val = mb.group(1) if mb else ""
        for token in re.findall(r"[\w.-]+", val or ""):
            n = _norm(token)
            if n and not domain_vacuous(token):
                out.setdefault(n, "%s: system of record of %s"
                               % (ARTIFACT_FILES["05"], name))
    return out


def check_diagrams(root):
    problem = read_problem(root, ARTIFACT_FILES["06"])
    if problem:
        return "FAIL", ("%s is %s; an artifact that exists and cannot be read is a broken claim, "
                        "not an absent one" % (ARTIFACT_FILES["06"], problem))
    t = read(root, ARTIFACT_FILES["06"])
    if t is None:
        return "NO-DATA", "no 06-diagrams.md in this dossier"
    nodes, kinds, skipped, states, unread = _diagram_nodes(t)
    # Every skipped token, on every verdict line. A parser that discards tokens in
    # silence and then reports "all traceable" is asserting completeness over a
    # set it truncated itself.
    note = ("; tokens read as diagram syntax rather than as nodes: %s" % ", ".join(skipped)) if skipped else ""
    if unread:
        note += ("; %d diagram line(s) this parser could NOT read at all, so nothing in them "
                 "was traced: %s" % (len(unread), "; ".join(unread[:4])))
    if not kinds:
        return "FAIL", ("no fenced code block holding a diagram; a diagram artifact with no diagram "
                        "in it is a defect. Diagrams are code: put the Mermaid source in a "
                        "```mermaid fence so it diffs in review")
    if not nodes:
        # A gantt or a pie chart is a real diagram that names no element this
        # check can trace, and a dialect this tool has no parser for is a diagram
        # it did not read. Failing either as "a diagram artifact with no diagram
        # in it" is rejecting correct work, which gets the gate switched off, and
        # a gate that is off catches less than a gate that is honest.
        bare = [k for k in kinds
                if DIAGRAM_TYPES.get(k) == "none" or k.startswith("unrecognised:")]
        if bare and len(bare) == len(kinds):
            return "NO-DATA", ("%s diagram(s) present, and nothing in them could be read as a node "
                               "this check can trace (the type declares none, or this tool has no "
                               "parser for it), so nothing here was checked for traceability%s"
                               % (", ".join(sorted(set(bare))), note))
        return "FAIL", ("fenced %s block(s) found but no diagram node could be read out of them; a "
                        "diagram artifact with no diagram in it is a defect%s"
                        % (", ".join(sorted(set(kinds))), note))
    model = read(root, ARTIFACT_FILES["05"])
    entities = {_norm(n): n for n in _entities(model)} if model is not None else {}
    components = _declared_components(root)
    declared_states = _declared_states(root)
    sor_names = _system_of_record_names(model)
    known = dict(sor_names)
    known.update(components)
    known.update(entities)
    known.update(declared_states)
    if not known:
        # Without a data model or a component declaration there is nothing to
        # trace against. Reporting PASS here would be the exact defect L5 exists
        # to catch: an empty known set makes every invented node look traceable.
        return "NO-DATA", ("%d diagram node(s), but tracing cannot be verified: no entities in %s and no "
                           "components declared in %s or under a Components heading here%s"
                           % (len(nodes), ARTIFACT_FILES["05"], ARTIFACT_FILES["04"], note))

    def resolved(nid, label):
        # A node is traceable by its id or by the label written on it. An author
        # writing `R[Refund]` has named the entity; insisting the id spell it out
        # tells them to rename every node or switch the gate off.
        for candidate in (nid, label):
            if candidate and _norm(candidate) and _norm(candidate) in known:
                return _norm(candidate)
        return None

    orphans = sorted(nid for nid, label in nodes.items() if resolved(nid, label) is None)
    # A state whose dossier declares no states at all is not an orphan, it is
    # unmeasured. Reporting FAIL over it rejected a correct entity-lifecycle
    # diagram and pushed authors to declare their states as runtime components;
    # reporting PASS over it would be this project's own defect. So the check
    # says which nodes it could not trace and why, and returns NO-DATA, which
    # neither blocks a merge nor claims anything.
    untraced_states = sorted(n for n in orphans if n in states) if not declared_states else []
    orphans = [n for n in orphans if n not in untraced_states]
    if orphans:
        placeholders = [n for n in orphans if domain_vacuous(n)]
        placeholder_note = ("" if not placeholders else
                            " Of these, %s name(s) nothing: a node called TBD or XXX is a note to "
                            "the author, and declaring it as a state or a component would not make "
                            "it one." % ", ".join(placeholders[:4]))
        return "FAIL", ("diagram element(s) trace to no declared entity, component, state or system "
                        "of record this check read: %s.%s Every node must be an "
                        "entity in %s, a system of record an entity there names, or a declared component (a row in %s, or a bullet under a Components "
                        "heading in %s or %s), matched on the node id or on its label. A state diagram's states "
                        "trace to states declared as bullets under a States, Status or Lifecycle heading, "
                        "or to a `status: draft | placed | shipped` line in %s%s"
                        % (", ".join(orphans[:6]), placeholder_note, ARTIFACT_FILES["05"],
                           ARTIFACT_FILES["04"], ARTIFACT_FILES["04"], ARTIFACT_FILES["06"],
                           ARTIFACT_FILES["05"], note))
    if untraced_states:
        return "NO-DATA", ("%d diagram node(s) in %s, of which %d are lifecycle state(s) this dossier "
                           "declares nowhere (%s), so their traceability was not checked and this "
                           "check cannot say every node traces. Declare the states as bullets under a "
                           "States, Status or Lifecycle heading in %s, or as a `status: draft | placed "
                           "| shipped` line, and they become traceable%s"
                           % (len(nodes), ", ".join(sorted(set(kinds))), len(untraced_states),
                              ", ".join(untraced_states[:6]), ARTIFACT_FILES["05"], note))
    if unread:
        # A ghost can be hiding in a line the parser could not read, so the
        # completeness sentence is refused rather than printed over a set the
        # parser truncated itself. NO-DATA, not FAIL: an unreadable line is a
        # limit of this parser, not proof of a defect, and failing honest
        # exotic syntax is how a gate gets switched off.
        return "NO-DATA", ("%d diagram node(s) read and %d line(s) not read, so this check "
                           "cannot say every element traces; a node could be hiding in what "
                           "it could not parse%s" % (len(nodes), len(unread), note))
    hits = [resolved(nid, label) for nid, label in nodes.items()]
    comp_hits = [h for h in hits if h in components and h not in entities]
    # Declaration and use in one file is a weaker trace than a cross-artifact
    # one, and the sentence used to hide the difference: a lone 06-diagrams.md
    # listing its own nodes as Components bullets satisfied "all traceable"
    # without anything outside the file agreeing. Still accepted (the bullet is
    # a declaration a reader can find), but said out loud.
    self_declared = sorted(set(h for h in comp_hits
                               if components[h].startswith(ARTIFACT_FILES["06"])))
    self_note = ("" if not self_declared else
                 "; %d of the component trace(s) resolve to bullets declared in this artifact "
                 "itself, so for those the declaration and the diagram are one file; a row in %s "
                 "is the cross-artifact form" % (len(self_declared), ARTIFACT_FILES["04"]))
    return "PASS", ("%d diagram node(s) in %s, all traceable: %d to entities in %s, %d to declared "
                    "components, %d to declared lifecycle states, %d to a system of record an "
                    "entity names%s%s"
                    % (len(nodes), ", ".join(sorted(set(kinds))),
                       sum(1 for h in hits if h in entities),
                       ARTIFACT_FILES["05"],
                       len(comp_hits),
                       sum(1 for h in hits if h in declared_states and h not in entities
                           and h not in components),
                       sum(1 for h in hits if h in sor_names and h not in entities
                           and h not in components and h not in declared_states),
                       self_note, note))


# The row contract 08-behaviour.md ships, fixed: | ID | Starting point |
# Trigger | Required outcome | Proof |. Proof is the tester's column; this
# check only reads it as text like every other cell. Matched case-insensitively
# so `## Rules` written with a capitalised Id or a stray space around a pipe
# still reads as the table it is; the five column NAMES are the contract and
# are not a vocabulary this check widens the way the ADR headings are, because
# L15 names this table's shape as fixed rather than as free-form prose an
# author phrases their own way.
_BEHAVIOUR_COLUMNS = ("id", "starting point", "trigger", "required outcome", "proof")
# A behaviour id, wherever it is written: the fixed row contract's own ID
# column, or a citation of one in 07-verification.md. "B" then digits, on a
# word boundary so "B12" is not read out of "SUBB12" or similar.
#
# The separator and the case are tolerated, and this cost a silent PASS before
# it was: an author who wrote B-1 in the table and cited a deleted B-9 in the
# verification plan got a clean verdict, because the pattern matched neither
# form. Rows still parsed, since the ID column is read as free text, so the
# hole reported itself as a pass. Both sides are normalised through
# _behaviour_id_key before they are compared, so B-1, B_1, b1 and B1 are one
# id rather than four.
#
# The residual, stated rather than hidden: a string like "B-12" written in
# 07-verification.md for some other reason is now read as a citation. That
# yields a FAIL naming an id nobody meant, which is loud and fixable, and it
# is the direction this project chooses when it has to choose.
_BEHAVIOUR_ID = re.compile(r"\bB[-_]?\d+\b", re.IGNORECASE)


def _behaviour_id_key(value):
    """The comparable form of a behaviour id: upper case B, separators dropped,
    digits kept. Returns None for anything that is not an id at all."""
    if not value:
        return None
    m = _BEHAVIOUR_ID.search(str(value).strip())
    if not m:
        return None
    text = m.group(0)
    return "B" + "".join(ch for ch in text if ch.isdigit())


def _rows_under(t, columns):
    """(rows, malformed) from the fixed ID | Starting point | Trigger |
    Required outcome | Proof table.

    rows holds one dict per data row, keyed by the lower-cased column name.
    malformed holds the raw text of every row found under that header whose
    cell count did not match the header, NAMED rather than silently dropped:
    a truncated row is a row this check could not read, not a row it never
    saw, and the two are different sentences.

    Comments are stripped first (the same rule check_adr and check_data_model
    already apply): a table sitting inside an HTML comment renders as nothing,
    and a check that read raw text there would count rows nobody sees.
    """
    lines = _with_leading_pipes(without_comments(t).splitlines())
    rows, malformed = [], []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("|"):
            header = [c.strip().lower() for c in s.strip("|").split("|")]
            nxt = lines[i + 1].strip() if i + 1 < n else ""
            is_sep = bool(nxt) and nxt.startswith("|") and not set(nxt) - set("|-: ")
            if is_sep and header == list(columns):
                j = i + 2
                while j < n and lines[j].strip().startswith("|"):
                    raw = lines[j].strip()
                    if not set(raw) - set("|-: "):    # a stray second delimiter row
                        j += 1
                        continue
                    cells = [c.strip() for c in raw.strip("|").split("|")]
                    if len(cells) != len(columns):
                        malformed.append(raw)
                    else:
                        rows.append(dict(zip(columns, cells)))
                    j += 1
                i = j
                continue
        i += 1
    return rows, malformed


def _behaviour_rows(t):
    """The behaviour table, through the shared engine above."""
    return _rows_under(t, _BEHAVIOUR_COLUMNS)


#: The open-questions table. A question with no Answer and no Answered is OPEN,
#: and an open row is what blocks a verdict. Six columns rather than four so a
#: row records WHO was asked: an unanswered question with nobody named is a note
#: to self, and the complaint this artifact answers was precisely that a
#: conversation nobody was on the hook for never happened.
_CLARIFY_COLUMNS = ("id", "question", "asked by", "asked of", "answer", "answered")


def _clarify_rows(t):
    """The open-questions table, through the same engine as the behaviour one.

    One parser with two callers rather than two parsers: the malformed-row
    handling, the comment stripping and the stray-delimiter case are all
    subtleties that were fixed once here and would have to be fixed twice."""
    return _rows_under(t, _CLARIFY_COLUMNS)


def _clarify_open(rows):
    """Every row still unanswered. A row is answered only when BOTH its Answer
    and its Answered cells carry something: an answer with no date is a claim
    nobody stood behind, and a date with no answer is worse.

    Both cells go through answered(), not a bare .strip() truthiness test: a
    plain strip only catches blank and whitespace-only cells, and let "TBD",
    "N/A", a bare dash, a commented-out cell, or a zero-width/soft-hyphen/
    full-width/small-caps stand-in for one of those, PASS as if the question
    had really been answered. answered() is the one vacuity test check_behaviour
    already runs on its own evidence fields (Required outcome, Proof); this
    check owes its two evidence fields the same test."""
    return [r for r in rows
            if answered(r.get("answer")) is None or answered(r.get("answered")) is None]


def check_behaviour(root):
    problem = read_problem(root, ARTIFACT_FILES["08"])
    if problem:
        return "FAIL", ("%s is %s; an artifact that exists and cannot be read is a broken claim, "
                        "not an absent one" % (ARTIFACT_FILES["08"], problem))
    t = read(root, ARTIFACT_FILES["08"])
    if t is None:
        return "NO-DATA", "no 08-behaviour.md in this dossier"
    # Still the shipped example: the SAME marker and the SAME detection
    # check_placeholder already uses, not a second way of asking whether this
    # file is somebody's own design. A dossier's rows may not be certified
    # while the file is still the template's; check_placeholder FAILs the
    # whole dossier for this file by name until the marker is deleted, and
    # this check declines to certify its rows in the meantime rather than
    # asserting a PASS the marker itself contradicts.
    if _MARKER_COMMENT.search(t):
        return "NO-DATA", ("%s still carries its %s marker comment: this is the shipped example, "
                           "not this dossier's own behaviour, so its rows are not certified as one "
                           "(the placeholder check also names this file until the marker is "
                           "deleted)" % (ARTIFACT_FILES["08"], UNFILLED_MARKER))
    rows, malformed = _behaviour_rows(t)
    if malformed:
        return "FAIL", ("%d row(s) under the ID | Starting point | Trigger | Required outcome | "
                        "Proof table do not carry 5 cells and could not be read: %s; a row this "
                        "check cannot parse is a broken claim, not an absent one"
                        % (len(malformed), "; ".join(m[:60] for m in malformed[:4])))
    if not rows:
        return "NO-DATA", ("no row found under an ID | Starting point | Trigger | Required "
                           "outcome | Proof table in %s; nothing here states what the software "
                           "must do under a given condition, so there is nothing to check"
                           % ARTIFACT_FILES["08"])
    problems = []
    # Required outcome and Proof are EVIDENCE FIELDS (a person's claim that a
    # rule holds and how it is checked), so they go through answered(), the
    # same placeholder-aware test an override reason and a system of record
    # use; "TBD" in the Proof column is not a proof any more than it is a
    # source.
    incomplete = [r.get("id") or "(no id)" for r in rows
                 if answered(r.get("required outcome")) is None or answered(r.get("proof")) is None]
    if incomplete:
        problems.append("%d of %d row(s) carry no Required outcome, no Proof, or both: %s; a "
                        "Required outcome with no Proof is a rule nobody has agreed how to check, "
                        "and it is not finished however well it reads"
                        % (len(incomplete), len(rows), ", ".join(incomplete[:8])))
    # Coherence with the verification plan, the other half of this check: a
    # row deleted from this table while 07-verification.md still cites its id
    # is a proof obligation pointing at nothing, and citing NOTHING is not a
    # failure by itself (07 may prove its claims some other way).
    verif = read(root, ARTIFACT_FILES["07"])
    missing = []
    if verif is not None:
        cited = set(k for k in (_behaviour_id_key(c)
                                for c in _BEHAVIOUR_ID.findall(without_comments(verif))) if k)
        have = set(k for k in (_behaviour_id_key(r.get("id")) for r in rows) if k)
        missing = sorted(c for c in cited if c not in have)
    if missing:
        problems.append("%s cites behaviour id(s) %s, which %s not a row in %s: a verification "
                        "plan that cites a row this table no longer has is proving a rule that "
                        "was deleted, not the one it names"
                        % (ARTIFACT_FILES["07"], ", ".join(missing),
                           "is" if len(missing) == 1 else "are", ARTIFACT_FILES["08"]))
    # The shipped example rows are not somebody's behaviour. Deleting the
    # unfilled-template marker while keeping the examples passes the
    # placeholder check by construction, so without this the spine artifact
    # certifies the demo. Measured before this was added: marker removed,
    # example rows intact, behaviour PASS and placeholder PASS together.
    shipped = _shipped_example_rules()
    if shipped:
        mine = set(_behaviour_rule_key(r) for r in rows)
        if mine and mine.issubset(shipped):
            problems.append("every row is still a shipped example from the template: a demo rule "
                            "is not this system's behaviour, and deleting the unfilled marker "
                            "does not make it one")
    # SUSPECT is information glued onto the verdict already computed above,
    # never a reason to compute a different one: a row that was reworded is
    # not a row that stopped having a Required outcome and a Proof, and this
    # note by itself must not turn today's PASS into a FAIL or today's FAIL
    # into something worse. See _behaviour_suspect_note for the three states.
    #
    # A row that is SUSPECT (reworded since it was last accepted) AND names
    # nothing in the revision log below IS a separate, blocking problem: a
    # requirement changed and nothing on record says so (P9). This is a
    # genuine FAIL, distinct from the informational SUSPECT note above:
    # appending one dated revision-log line naming the row clears it, without
    # needing `behaviour-accept` to run first.
    state, suspect, matched = _behaviour_suspect_state(root, rows)
    if suspect:
        logged = _revision_log_ids(t)
        unlogged = [sid for sid in suspect if _behaviour_id_key(sid) not in logged]
        if unlogged:
            problems.append("%d row(s) reworded since last accepted with no matching entry in "
                            "the revision log: %s; a requirement change edits the row and "
                            "appends one dated line naming it to the revision log, never a new "
                            "dossier"
                            % (len(unlogged), ", ".join(unlogged)))
    note = _behaviour_suspect_note(state, suspect, matched, len(rows))
    if problems:
        return "FAIL", "; ".join(problems) + note
    return "PASS", ("%d row(s) read, each with a Required outcome and a Proof%s"
                    % (len(rows), note))


def _behaviour_rule_key(row):
    """The rule a row states, normalised: Starting point, Trigger and Required
    outcome, lower-cased with runs of whitespace collapsed. Proof and ID are
    deliberately excluded, because renaming B1 to B4, or rewording the proof,
    does not turn a demo rule into a real one."""
    return tuple(" ".join((row.get(c) or "").lower().split())
                 for c in ("starting point", "trigger", "required outcome"))


def _behaviour_fingerprint(row):
    """A stable digest of what this row REQUIRES, not how it happens to be
    typed or how QC currently proves it.

    Built ON TOP OF _behaviour_rule_key rather than a second normalisation of
    the same table: that function already decided which cells define a rule
    (Starting point, Trigger, Required outcome) for the shipped-example
    comparison just above, and its own docstring states the same exclusion.
    Two parsers of one row format drift apart silently, and this project has
    paid for exactly that mistake before (see the ID-citation comment on
    _BEHAVIOUR_ID above); reusing the one normalisation this file already
    trusts is how that is avoided here rather than repeated.

    id is left out on purpose: it identifies which row this is, not what the
    row demands, so renaming B1 to B4 is not a change to the behaviour.
    Proof is left out on purpose too: it is QC's current answer for how to
    verify the rule, not the rule itself, so a tester rewording how a claim
    is checked must not read as the analyst having reworded what is
    required. Hashed (rather than stored as the tuple itself) because the
    record this backs is a small JSON file meant to be opened by hand
    (.sbe/behaviour-fingerprints.json), and a fixed-width digest reads there
    as a stable id rather than a second, driftable copy of the row's text.
    """
    key = _behaviour_rule_key(row)
    return hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()


# Where the accepted fingerprints live: beside the dossier, in the SAME .sbe/
# state directory `sbe testkit sheet` already writes test-run sheets into
# (tools/sbe_testkit.py:_sheet_path), not a sixth cell bolted onto
# _BEHAVIOUR_COLUMNS. That table is the analyst's own document and stays a
# fixed 5-cell contract; a machine-written column on it is exactly the
# widening this feature is told not to do. A plain JSON object beside the
# dossier is also how this project already keeps machine state next to a
# human one: 00-intake.json's own optional `binding.artifacts` block records
# a sha256 per artifact path for the same reason (state that has to survive
# between runs, and that no analyst hand-edits).
FINGERPRINT_REL = os.path.join(".sbe", "behaviour-fingerprints.json")


def _behaviour_fingerprint_record(root):
    """{id_key: fingerprint} last accepted for this dossier, or None when
    nothing usable is recorded.

    None covers three different states as one, deliberately: the file was
    never written (a genuine first run), it exists and cannot be read
    (read_problem), and it exists but is not the JSON object of strings this
    tool itself always writes (hand damage, a merge conflict left unresolved).
    Telling "never accepted" apart from "accepted but corrupted" would need a
    FAIL, and this feature is not allowed to add one: folding every unusable
    state into the same None keeps the record purely informational, the same
    as its plain absence.
    """
    if read_problem(root, FINGERPRINT_REL):
        return None
    text = read(root, FINGERPRINT_REL)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:  # sbe: allow-silent a corrupt record is the SAME answer as no record, on purpose: this whole feature is forbidden from adding a FAIL, and telling "never accepted" apart from "accepted then damaged" would need one. Returning None makes the caller print NO-DATA, which is the honest verdict for a record nothing can read, and NO-DATA is never a pass and never a block. The exception text is dropped because it says nothing a reader could act on beyond what the NO-DATA line already tells them, which is that the record must be re-accepted.
        return None
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        return None
    return data


def _behaviour_suspect_state(root, rows):
    """(state, suspect_ids, matched) for this dossier's fingerprint record.

    state is one of "no-record", "suspect", "clean". Pulled out of
    _behaviour_suspect_note so a second caller (the revision-log check
    below) tests the SAME rows the SUSPECT note names, rather than a second
    reimplementation of the comparison drifting away from it.
    """
    record = _behaviour_fingerprint_record(root)
    if record is None:
        return "no-record", [], 0
    suspect, matched = [], 0
    for r in rows:
        key = _behaviour_id_key(r.get("id"))
        if key is None or key not in record:
            continue
        if record[key] == _behaviour_fingerprint(r):
            matched += 1
        else:
            suspect.append(r.get("id"))
    return ("suspect" if suspect else "clean"), suspect, matched


def _behaviour_suspect_note(state, suspect, matched, total):
    """The sentence check_behaviour appends about its fingerprint record, from
    the (state, suspect, matched) triple _behaviour_suspect_state computed.

    Three states, named in the sentence so a reader (or a script grepping
    the line) never has to infer which one happened from silence:
      - no record at all: "NO-DATA", because a row nobody has ever accepted
        cannot honestly be called changed OR unchanged;
      - one or more accepted rows whose current fingerprint disagrees with
        the recorded one: "SUSPECT", naming the ids;
      - every accepted row's fingerprint still matches: a plain count, no
        SUSPECT and no NO-DATA in it.
    This note by itself never changes verdict or exit code: it is text
    appended to whatever verdict check_behaviour already computed, per the
    law that a control here may reveal information and may not add a
    blocking obligation on its own. (check_behaviour adds a SEPARATE,
    genuine FAIL when a suspect row also names nothing in the revision log;
    that is not this note.)
    """
    if state == "no-record":
        # The word "SUSPECT" is deliberately absent from this sentence: a
        # reader (or a script) grepping a report line for that exact word to
        # find a flagged row must never match a line that flags nothing.
        return ("; fingerprint tracking: NO-DATA (no %s recorded yet; run `sbe_design.py "
                "behaviour-accept <dossier-dir>` once this wording is right, so a later "
                "rewording is flagged here instead of passing unnoticed)" % FINGERPRINT_REL)
    if suspect:
        return ("; SUSPECT (reworded since %s was last accepted, so the Proof recorded for it "
                "may be answering a different rule now; run `sbe_design.py behaviour-accept "
                "<dossier-dir>` once it has been re-verified): %s"
                % (FINGERPRINT_REL, ", ".join(suspect)))
    return ("; fingerprint tracking: %d of %d row(s) match their last accepted wording"
            % (matched, total))


#: The revision log 08-behaviour.md carries appended below its Supersedes
#: table (see the template): a requirement change edits a row above and
#: appends one dated line here, naming the row it changed, instead of a new
#: dossier (P9). Same shared row engine as the Rules and Supersedes tables.
_REVISION_LOG_COLUMNS = ("date", "row", "change")


def _revision_log_rows(t):
    """The revision log table, through the shared engine above."""
    return _rows_under(t, _REVISION_LOG_COLUMNS)


def _revision_log_ids(t):
    """Every behaviour id named in the revision log's Row column, keyed the
    same way every other behaviour id in this file is (see
    _behaviour_id_key), so B-1, B_1 and B1 all clear the same row's check."""
    rows, _ = _revision_log_rows(t)
    return set(k for k in (_behaviour_id_key(r.get("row")) for r in rows) if k)


def _shipped_example_rules():
    """The set of rule keys in the template that ships with this product, or
    an empty set when it cannot be read. Never raises: an install that does
    not carry its templates loses this one sub-check and keeps every other,
    which is honest degradation rather than a crash or a silent pass."""
    here = os.path.dirname(os.path.abspath(__file__))
    shipped_dir = os.path.join(os.path.dirname(here), "templates", "dossier")
    text = read(shipped_dir, ARTIFACT_FILES["08"])
    if text is None:
        return set()
    rows, _ = _behaviour_rows(text)
    return set(_behaviour_rule_key(r) for r in rows)


# The refund domain, deliberately, and the change is not cosmetic. Every other
# worked fragment here (_FX_PURPOSE, _FX_ADR, _FX_DATA_MODEL) designs one refund
# settlement system; this fixture alone described checkout, orders and a
# warehouse. That was invisible while each fixture was read on its own, and the
# artifacts check reads them TOGETHER: its rule is that an artifact sharing no
# named subject with any sibling is filler rather than an artifact. So the
# declared full_fixture for `artifacts`, which pairs 01 with 08, could not reach
# PASS, and a worked example that cannot pass proves nothing about the check
# body. The honesty meta-test says exactly that, and it was saying it about this
# pair. Rewritten into the refund domain so the two artifacts describe one
# system: they now share refund, support desk, ledger and settlement.
_FX_BEHAVIOUR = ("# Behaviour\n## Rules\n"
                 "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                 "|---|---|---|---|---|\n"
                 "| B1 | A settled payment eligible for refund | The support desk submits the "
                 "refund | A refund is created with status pending and reaches a terminal state "
                 "within one business day | Integration test: submit a refund against a settled "
                 "payment, assert the refund row exists with status pending and reaches a "
                 "terminal state inside one business day |\n"
                 "| B2 | A refund that has been submitted | The ledger service does not "
                 "acknowledge settlement within five minutes | The refund service retries "
                 "settlement and raises an alert | Failover drill: stop the ledger consumer, "
                 "confirm the alert fires within the five-minute window |\n")


# Worked dossier fragments that SHOULD pass. The honesty meta-test hollows these:
# it drops the body under one heading at a time, blanks the file, and empties it to
# zero bytes, and demands that none of that leaves a PASS standing. They are written
# in the ordinary idioms an engineer uses (labelled Mermaid node ids, bulleted
# alternatives, a relationships table) so that a change which starts rejecting honest
# work fails here first.
_FX_PURPOSE = ("# Purpose\nProblem: refunds settle late and support cannot say why.\n"
               "Users: the support desk and the finance close.\n"
               "Success: every refund reaches a terminal state within one business day.\n"
               "Non-goals: repricing, partial refunds.\nIf wrong: refunds stall silently.\n")
_FX_ADR = ("# ADR\n## Criteria\nsettlement latency, operational load, auditability\n"
           "## Rejected alternatives\n"
           "- Synchronous call to the ledger: ties checkout latency to ledger availability.\n"
           "- Nightly batch reconciliation: misses the one business day requirement.\n"
           "## Decision\nPublish refund events to a queue and settle asynchronously.\n"
           "## Consequences\nOne more moving part to operate, and an ordering guarantee to hold.\n"
           "## What would flip this\nSub-second settlement becoming a requirement.\n")
_FX_DATA_MODEL = ("# Data model\n## Entities\n"
                  "- Customer: system of record: the CRM.\n"
                  "- Refund: system of record: the ledger service.\n"
                  "## Relationships\n"
                  "- Customer to Refund: one-to-many, optional.\n")
_FX_DIAGRAMS = ("# Diagrams\n## Context\n"
                "```mermaid\nflowchart LR\n  C[Customer] --> R[Refund]\n```\n")

# Same contract as sbe_gate.GATES: the registry carries the declaration, so the
# honesty meta-test discovers the checks rather than carrying a hand-written list.

def check_clarify(root):
    """The open-questions gate: a skipped conversation is a refusal, not a silence.

    THE COMPLAINT THIS ANSWERS, in the reviewer's own terms: they asked for a
    discussion before planning, did not get one, and nothing flagged or logged
    the skip. Every other artifact records what was DECIDED. This one records
    what is still undecided, while it is still cheap to decide.

    The verdicts mirror check_behaviour deliberately, because a reader who has
    learned one should not have to learn another:

      FAIL     the file exists and cannot be read, or a row cannot be parsed,
               or a row is still open. A broken claim is not an absent one, and
               an open question is not a finished design.
      NO-DATA  no file at all, the file is still the shipped template, or the
               file holds no row under the table at all. Nobody checked, which
               is a different sentence from nobody had a question, and it is
               never a pass; this mirrors check_behaviour's own empty-table
               reading exactly, on purpose (empty_expect can never be PASS for
               any check registered here, and a zero-row 09-clarify.md is
               exactly the same "nothing was examined" state as a zero-row
               08-behaviour.md).
      PASS     every row carries both an Answer and an Answered.
    """
    problem = read_problem(root, ARTIFACT_FILES["09"])
    if problem:
        return "FAIL", ("%s is %s; an artifact that exists and cannot be read is a broken claim, "
                        "not an absent one" % (ARTIFACT_FILES["09"], problem))
    t = read(root, ARTIFACT_FILES["09"])
    if t is None:
        return "NO-DATA", ("no %s in this dossier, so nobody recorded whether a discussion was "
                           "wanted before planning. That is not the same as nobody having a "
                           "question, and it is not a pass" % ARTIFACT_FILES["09"])
    if _MARKER_COMMENT.search(t):
        return "NO-DATA", ("%s still carries its %s marker comment: this is the shipped example, "
                           "not this dossier's own questions (the placeholder check also names "
                           "this file until the marker is deleted)"
                           % (ARTIFACT_FILES["09"], UNFILLED_MARKER))
    rows, malformed = _clarify_rows(t)
    if malformed:
        return "FAIL", ("%d row(s) under the %s table do not carry %d cells and could not be "
                        "read: %s; a row this check cannot parse is a broken claim, not an "
                        "absent one"
                        % (len(malformed), " | ".join(_CLARIFY_COLUMNS),
                           len(_CLARIFY_COLUMNS), "; ".join(m[:60] for m in malformed[:4])))
    if not rows:
        return "NO-DATA", ("no row found under the %s table in %s; nothing here states whether "
                           "a question was ever asked, so there is nothing to check"
                           % (" | ".join(_CLARIFY_COLUMNS), ARTIFACT_FILES["09"]))
    still_open = _clarify_open(rows)
    if still_open:
        return "FAIL", ("%d question(s) in %s are still open: %s. A design cannot be certified "
                        "while a question it depends on is unanswered, which is the whole point "
                        "of recording it here rather than in somebody's head"
                        % (len(still_open), ARTIFACT_FILES["09"],
                           ", ".join(r.get("id") or "(no id)" for r in still_open[:6])))
    return "PASS", "%d question(s) recorded, all answered and dated" % len(rows)


#: The worked example for the clarify check. It must be able to PASS, which the
#: honesty meta-test enforces: a fixture that cannot pass proves nothing about
#: the check body. So every row here is answered and dated, unlike the shipped
#: template, whose open row exists to demonstrate the refusal.
_FX_CLARIFY = (
    "# 09. Open questions\n\n"
    "| ID | Question | Asked by | Asked of | Answer | Answered |\n"
    "|---|---|---|---|---|---|\n"
    "| Q1 | Which address does the invoice use when a record exists twice? | the analyst "
    "| the process owner | The most recently verified one. | 2026-08-29 |\n"
)


CHECKS = {
    "artifacts": Check(
        check_artifacts, reads=(INTAKE,), kind="json", severity="gate",
        full_fixture={"files": {
            INTAKE: {"tier": "T1",
                     "answers": {"changes_contract": False, "crosses_boundary": True,
                                 "reversible_under_hour": True, "touches_sensitive": False,
                                 "consumers": "none"}},
            # T1 requires 01 AND 08 (tools/sbe_intake.py REQUIRED). A worked
            # example that writes only 01 cannot PASS, and a worked example that
            # cannot PASS proves nothing about the check body: the honesty
            # meta-test says exactly that, and it said it for a day while both
            # of us read the sentence as noise. The behaviour rows here carry a
            # Required outcome and a Proof, because a row missing either FAILs
            # and an absent table is NO-DATA.
            ARTIFACT_FILES["01"]: _FX_PURPOSE,
            ARTIFACT_FILES["08"]: _FX_BEHAVIOUR}}),
    "adr": Check(check_adr, reads=(ARTIFACT_FILES["03"],), kind="text", severity="gate", empty_expect="FAIL",
                 empty_note="a dossier that carries an empty 03-adr.md claims a decision record and "
                            "supplies none, which is a broken claim rather than an absence",
                 full_fixture={"files": {ARTIFACT_FILES["03"]: _FX_ADR}}),
    "datamodel": Check(check_data_model, reads=(ARTIFACT_FILES["05"],), kind="text", severity="gate", empty_expect="FAIL",
                       empty_note="an empty 05-data-model.md declares zero entities while claiming to "
                                  "be the data model, and zero entities each with a system of record "
                                  "is the vacuous PASS this check exists to prevent",
                       full_fixture={"files": {ARTIFACT_FILES["05"]: _FX_DATA_MODEL}}),
    "diagrams": Check(check_diagrams, reads=(ARTIFACT_FILES["06"], ARTIFACT_FILES["05"]), kind="text", severity="gate",
                      empty_expect="FAIL",
                      empty_note="an empty 06-diagrams.md is a diagram artifact with no diagram in it, "
                                 "which is a broken claim rather than an absence: the dossier says it "
                                 "has diagrams and the file says otherwise",
                      full_fixture={"files": {ARTIFACT_FILES["06"]: _FX_DIAGRAMS,
                                              ARTIFACT_FILES["05"]: _FX_DATA_MODEL}},
                      optional_leaves={
                          "05-data-model.md##Relationships":
                              "this check traces diagram nodes to the ENTITIES declared in the data "
                              "model, and says nothing about its relationships. Emptying the "
                              "relationships section leaves every entity it traced against intact, "
                              "and the datamodel check's own sweep is what holds that section to "
                              "its sentence"}),
    # No optional_leaves here any more, and the removal is the fix, not a
    # loosening: the exemption this check declared ("01-purpose.md##Purpose")
    # shielded nothing, because the check itself FAILs a hollowed purpose
    # artifact (the fixture's only section IS the body, so emptying it trips
    # the blank-artifact rule), and its stated reason described a tolerance
    # the check does not have. A waiver whose scenarios never need it is a
    # pre-approved mark waiting for a regression to hide under, and the
    # meta-test now fails any exemption in that state by name.
    "placeholder": Check(check_placeholder, reads=tuple(ARTIFACT_FILES.values()), kind="text", severity="gate",
                         empty_expect="FAIL",
                         empty_note="a zero-byte artifact carries no unfilled-template marker, so passing "
                                    "it would be reporting a clean scan of a file with nothing in it",
                         full_fixture={"files": {ARTIFACT_FILES["01"]: _FX_PURPOSE}}),
    "behaviour": Check(check_behaviour, reads=(ARTIFACT_FILES["08"], ARTIFACT_FILES["07"]),
                       kind="text", severity="gate",
                       full_fixture={"files": {ARTIFACT_FILES["08"]: _FX_BEHAVIOUR}}),
    "clarify": Check(check_clarify, reads=(ARTIFACT_FILES["09"],),
                     kind="text", severity="gate",
                     full_fixture={"files": {ARTIFACT_FILES["09"]: _FX_CLARIFY}}),
}


ADDRESSED_TO_GATES = "addressed to tools/sbe_gate.py: it names gates: and no checks:"


def _owner_expiry(owner, expires):
    """(suffix to append to the exemption reason, problem) for an `owner:` /
    `expires:` pair. Mirrors tools/sbe_gate.py::_owner_expiry exactly: same
    keys, same validation, same message wording, because the gate copy is
    the load-bearing one (the new gate refusals are waived through the
    gate's own parser) and this copy has to agree with it or an exemption
    could read as granted in one report and expired in the other.

    Neither key present is the grandfather path, not a problem: every
    exemption already in a tree was written before these two fields
    existed, and the ratified direction here is paved road, not forced
    road, so this stays a report rather than a block. Exactly one of the
    two present IS a problem: half an exception is not an exception.

    An expired or malformed `expires:` returns a non-empty problem, which
    the caller turns into a FAIL, never a NO-DATA: this mirrors
    tools/sbe_gate.py's load_receipt docstring, which calls a receipt that
    exists but cannot be read a broken claim rather than an absent one. An
    exemption with an expiry date in it is present; a present date that has
    passed, or is not a real date, is unusable the same way.

    Ceiling, stated rather than hidden, because this project's own law is
    that an unenforced rule says so: (1) a grandfathered exemption with
    neither field never expires, ever, and nothing here will ever make it
    start; (2) nothing verifies that the person named in owner: is who
    actually granted the exception, or that they granted anything at all;
    (3) an author refused for running past expires: can re-date it forward
    themselves in the very same file, so this parser cannot distinguish a
    granted extension from a self-service one. Only a human reading the
    file's own diff can tell those apart, and nothing routes this file to
    one.
    """
    if not owner and not expires:
        return " [no owner recorded, no expiry recorded]", ""
    if not owner:
        return "", ("it names expires: %r but no owner:; an exemption naming only one of the "
                    "two is not a granted exception" % expires)
    if not expires:
        return "", ("it names owner: %r but no expires:; an exemption naming only one of the "
                    "two is not a granted exception" % owner)
    # fromisoformat widened across Python versions (3.11+ also accepts basic
    # format like "20991231" and ISO week dates like "2099-W01-1"), so
    # handing it the raw string let the enforced contract drift from the
    # YYYY-MM-DD this message promises, and two scanners on different Python
    # minors could disagree about the same file. The explicit shape check
    # keeps what is enforced equal to what is promised, on every version.
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", expires):
        return "", "its expires: %r is not a YYYY-MM-DD date" % expires
    try:
        exp_date = datetime.date.fromisoformat(expires)
    except ValueError:
        return "", "its expires: %r is not a YYYY-MM-DD date" % expires
    if exp_date < datetime.date.today():
        return "", ("its expires: %s is in the past; an expired exemption is refused, not "
                    "honored" % expires)
    return " [owner: %s; expires: %s]" % (owner, expires), ""


def parse_exemption(text):
    """(checks waived, reason, problem) for a `.sbe-exempt` file.

    Format, and it is not free text any more:

        checks: adr, diagrams
        reason: this dossier was closed in 2024 and is kept for history; the
                decision record it points at moved to the archive.

    An exemption used to be a paragraph, and waiving EVERYTHING was what a
    paragraph did by default: three words of filler switched off all five design
    checks for a directory, and nothing in the file said which checks the author
    thought they were waiving. Waiving everything by default is not an exemption,
    it is an off switch. So the checks are named, one by one, and an author who
    means all of them says all of them. The reason is held to the same
    reviewability threshold as a tier override, because an exemption waives
    strictly more than an override does.

    What this does NOT do, stated plainly rather than implied: it cannot tell a
    real reason from a well-formed fake one. No mechanical test can. That is why
    the waiver prints as WAIVED rather than PASS, names every check it covers,
    and is surfaced in CI as something a human is shown.
    """
    checks, reason_lines, seen_reason = [], [], False
    owner, expires = None, None
    reason_hid_a_key = False
    for line in (text or "").splitlines():
        m = re.match(r"(?i)^\s*checks\s*:\s*(.*)$", line)
        if m and not seen_reason:
            checks.extend(c.strip() for c in re.split(r"[,\s]+", m.group(1)) if c.strip())
            continue
        # owner:/expires: are matched here, but ONLY ahead of reason:, the same
        # not-seen-reason guard checks: already uses two lines up. reason:
        # deliberately swallows every line after it as prose (the catch-all
        # below), so a continuation line that merely LOOKS like owner:/expires:
        # is prose, not a key: matching it unconditionally used to lift the
        # fragment out of the reason and grant an exception the file never
        # declared, and it made a legacy file whose prose happened to start a
        # line with "owner:" start refusing for a missing expires:. Left as
        # prose here, and flagged below instead of either.
        m = re.match(r"(?i)^\s*owner\s*:\s*(.*)$", line)
        if m and not seen_reason:
            owner = m.group(1).strip()
            continue
        m = re.match(r"(?i)^\s*expires\s*:\s*(.*)$", line)
        if m and not seen_reason:
            expires = m.group(1).strip()
            continue
        m = re.match(r"(?i)^\s*reason\s*:\s*(.*)$", line)
        if m:
            seen_reason = True
            reason_lines.append(m.group(1))
            continue
        if seen_reason:
            if re.match(r"(?i)^\s*(owner|expires)\s*:", line):
                reason_hid_a_key = True
            reason_lines.append(line)
    reason = " ".join(l.strip() for l in reason_lines if l.strip()).strip()
    if reason_hid_a_key:
        reason += (" [note: an owner:/expires:-looking line inside reason: was kept as "
                   "prose, not a key]")
    if not checks and not seen_reason:
        return [], (text or "").strip(), (
            "it names no checks and no reason. An exemption states which of the %d design checks it "
            "waives and why, as two fields:  checks: %s  and  reason: <at least %d words>. A file of "
            "free text waives everything by default, which is an off switch and not an exemption"
            % (len(CHECKS), ", ".join(CHECKS), OVERRIDE_MIN_WORDS))
    if not checks and any(re.match(r"(?i)^\s*gates\s*:", l) for l in (text or "").splitlines()):
        # Addressed to the hard gates (tools/sbe_gate.py), not to the design
        # checks: that scanner honors and PRINTS it as a waiver, so skipping it
        # here leaves it visible in exactly one shipped report rather than
        # refused by the scanner it never spoke to.
        return [], reason, ADDRESSED_TO_GATES
    if not checks:
        return [], reason, ("it records a reason but names no checks; list the ones it waives "
                            "(checks: %s), because an exemption that names nothing waives everything"
                            % ", ".join(CHECKS))
    unknown = [c for c in checks if c not in CHECKS]
    if unknown:
        return [], reason, ("it waives %s, which %s not a design check; the checks are %s"
                            % (", ".join(unknown), "are" if len(unknown) > 1 else "is",
                               ", ".join(CHECKS)))
    problem = _reviewability_problem(reason, "exemption reason")
    if problem:
        return [], reason, problem
    suffix, problem = _owner_expiry(owner, expires)
    if problem:
        return [], reason, problem
    return checks, reason + suffix, ""


def find_dossiers(root):
    """Walk for directories carrying ANY numbered dossier artifact.

    Anchoring on 00-intake.json alone meant deleting one file removed the whole
    design gate from a repository-wide run: seven filled artifacts with no intake
    were never opened, and --strict exited 0. Any numbered artifact makes a
    directory a dossier; check_artifacts then FAILs it by name for the missing
    intake, because the tier cannot be established without it.
    """
    hits, exempt, refused = [], [], []
    entered = []
    pruner = Pruner()
    # onerror: a directory this walk cannot enter may hold a dossier, and os.walk
    # swallows the refusal by default, so a chmod 000 design/ directory made the
    # run print "no directory contains 00-intake.json" about a tree that held
    # one, at exit 0, with five checks silently removed. The refusal now lands in
    # the note every dossier line prints.
    for dp, dns, fns in os.walk(root, onerror=pruner.onerror):
        entered.append(dp)
        dns[:] = pruner(dp, dns)
        if not (INTAKE in fns or (set(fns) & set(ARTIFACT_FILES.values()))):
            continue
        if EXEMPT in fns:
            # `touch .sbe-exempt` waived all five checks for a dossier and the
            # report printed the sentence ".sbe-exempt names why: no reason
            # recorded", which asserts a reason while naming its absence. Then a
            # reviewable-length paragraph waived all five without naming one of
            # them. An exemption waives strictly more than a tier override does,
            # so it is held to the override's threshold AND has to say what it
            # waives. An exemption that does neither does not exempt: the dossier
            # is checked, and the broken exemption is its own failure, so that
            # nobody discovers it by noticing the gate went quiet.
            waived, reason, problem = parse_exemption(read(dp, EXEMPT))
            if problem == ADDRESSED_TO_GATES:
                # The gate scanner owns this file and prints its waiver; the
                # dossier itself is still design-checked in full.
                hits.append(dp)
                continue
            if problem:
                refused.append((dp, problem))
                hits.append(dp)
                continue
            exempt.append((dp, waived, reason))
            # A partial exemption still gets checked for everything it did not
            # waive. Only a dossier that waived every check drops out of the walk.
            if sorted(waived) != sorted(CHECKS):
                hits.append(dp)
            continue
        hits.append(dp)
    # The note leads with the scope: the root actually walked, the dossiers
    # actually found, and the count of directories under that root that held
    # none. Same rule as sbe_gate.find, same shared function, same reason. A
    # verdict whose sentence names no root is a verdict an unrelated directory
    # can be absorbed into, and that is exactly what happened here: an EMPTY
    # directory named on the command line printed five PASS lines identical to
    # the run against a complete dossier.
    # Two channels, kept apart on purpose. `pruned` is what this walk did not
    # cover and is printed only when it is non-empty, because a NO-DATA line on
    # a healthy run trains a reader to skip NO-DATA lines. `scope` is what this
    # walk DID cover, and it is printed always.
    return (hits, exempt, refused,
            scope_note(root, "dossier", hits, entered),
            pruner.note(lambda f: f == INTAKE or f in set(ARTIFACT_FILES.values())))


DESIGN_USAGE = (
    "usage: sbe_design.py [check-name] [directory] [--strict] [--strict-waivers]\n"
    "       sbe_design.py behaviour-accept <dossier-dir>\n"
    "  reads: the dossier files under the directory (default .).\n"
    "  writes: nothing, except behaviour-accept, which writes only %s.\n"
    "  checks (pass one name for a single check): %%s\n"
    "  behaviour-accept: record 08-behaviour.md's current row fingerprints as accepted,\n"
    "    so a genuine rewording stops reading SUSPECT in the behaviour check.\n"
    "  flags:\n"
    "    --strict          make a FAIL block the exit code\n"
    "    --strict-waivers  make a WAIVED directory block as well\n"
    "    -h, --help        print this and exit 0 without examining anything"
) % FINGERPRINT_REL


def cmd_behaviour_accept(argv):
    """`sbe_design.py behaviour-accept <dossier-dir>`: read this dossier's
    current 08-behaviour.md and record every row's fingerprint (see
    _behaviour_fingerprint) as accepted, so a genuine rewording clears the
    SUSPECT note check_behaviour prints on the next run.

    Never writes 08-behaviour.md itself: that table is the analyst's own
    document. Only the record beside it, at FINGERPRINT_REL, moves, and this
    is the ONLY place in this file that writes anything at all; every check
    above it is read-only by the module's own contract.

    A row with no readable id cannot be looked up again later, so it is
    named and left out of the record rather than guessed into one; this
    mirrors how check_behaviour itself already treats an id it cannot key
    (missing rather than silently folded into something else).
    """
    if not argv or argv[0] in ("-h", "--help"):
        say("usage: sbe_design.py behaviour-accept <dossier-dir>")
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    dossier_dir = argv[0]
    if not os.path.isdir(dossier_dir):
        say("sbe_design.py behaviour-accept: %r is not a directory." % dossier_dir)
        return 1
    problem = read_problem(dossier_dir, ARTIFACT_FILES["08"])
    if problem:
        say("sbe_design.py behaviour-accept: %s is %s; nothing recorded."
              % (ARTIFACT_FILES["08"], problem))
        return 1
    t = read(dossier_dir, ARTIFACT_FILES["08"])
    if t is None:
        say("sbe_design.py behaviour-accept: no %s in %s; nothing to accept."
              % (ARTIFACT_FILES["08"], dossier_dir))
        return 1
    rows, malformed = _behaviour_rows(t)
    if malformed:
        say("sbe_design.py behaviour-accept: %d malformed row(s) in %s; fix the table before "
              "accepting (a fingerprint for a row this tool cannot parse would be a claim "
              "about nothing)." % (len(malformed), ARTIFACT_FILES["08"]))
        return 1
    record, skipped = {}, []
    for r in rows:
        key = _behaviour_id_key(r.get("id"))
        if key is None:
            skipped.append(r.get("id") or "(no id)")
            continue
        record[key] = _behaviour_fingerprint(r)
    out_path = os.path.join(dossier_dir, FINGERPRINT_REL)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    say("sbe_design.py behaviour-accept: %d row fingerprint(s) recorded to %s"
          % (len(record), out_path))
    if skipped:
        say("sbe_design.py behaviour-accept: %d row(s) carry no readable id and were not "
              "recorded: %s" % (len(skipped), ", ".join(skipped)))
    return 0


def main():
    # A subcommand, not a check name or a directory: dispatched first, and
    # before the generic -h/--help scan below, so `behaviour-accept -h`
    # prints ITS OWN usage rather than the whole tool's.
    if len(sys.argv) > 1 and sys.argv[1] == "behaviour-accept":
        sys.exit(cmd_behaviour_accept(sys.argv[2:]))
    # Flags used to be stripped wholesale (every leading-dash token dropped),
    # so `-h` ran a real scan instead of printing help, and a mistyped flag
    # was silently ignored rather than refused: the same defect class that let
    # sbe_telemetry's `data-export --help` run a real export.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        for usage_line in (DESIGN_USAGE % ", ".join(CHECKS)).splitlines():
            say(usage_line)
        sys.exit(0)
    for a in sys.argv[1:]:
        if a.startswith("-") and a not in ("--strict", "--strict-waivers"):
            for usage_line in (DESIGN_USAGE % ", ".join(CHECKS)).splitlines():
                say(usage_line)
            say("sbe_design: unrecognized flag %r; refusing rather than running past it" % a)
            sys.exit(2)
    strict = "--strict" in sys.argv
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    root = "."
    roots = []
    which = list(CHECKS)
    for a in argv:
        if a in CHECKS:
            which = [a]
        elif os.path.isdir(a):
            roots.append(a)
            root = a
        else:
            say("sbe_design: %r is neither a check name (%s) nor a directory."
                  % (a, ", ".join(CHECKS)))
            sys.exit(1)
    if len(roots) > 1:
        # The last one used to win in silence: a failing dossier passed first
        # on the command line was neither checked nor mentioned while --strict
        # exited 0 on the survivor.
        say("sbe_design: one directory at a time, got %d (%s)."
              % (len(roots), ", ".join(roots)))
        sys.exit(1)
    # SBE_DOSSIER_ROOT is the declaration that this repository keeps dossiers.
    # Declared and empty is a broken configuration, so it FAILS; undeclared and
    # empty is a repository with nothing to check, which is NO-DATA and says so.
    configured = os.environ.get("SBE_DOSSIER_ROOT", "").strip()
    # A root the operator did not name replacing one they did is a substitution,
    # and a substitution nobody prints is the whole defect: run against an EMPTY
    # directory with this variable set, this tool printed five PASS lines byte
    # for byte identical to the run against a complete dossier, because the
    # argument was discarded here in silence and no sentence downstream named the
    # root actually read. The verdicts below are unchanged; the substitution is
    # now said out loud, in the scope line, next to the root it replaced.
    replaced = ""
    if configured:
        if roots and os.path.abspath(roots[0]) != os.path.abspath(configured):
            replaced = ("; the directory named on the command line (%s) is NOT what was examined: "
                        "SBE_DOSSIER_ROOT=%s replaced it, so every verdict below is about %s"
                        % (roots[0], configured, configured))
        root = configured
    fails = 0
    waivers = 0
    # A waiver is not a pass, so CI must be able to act on one without reading
    # prose. --strict-waivers turns every waiver into a failure for teams that
    # want an exemption to expire on contact; without it the run still exits 0
    # and the shipped workflow surfaces the WAIVED line as an annotation a human
    # is shown. Either way the machine can tell a waiver from a verdict.
    strict_waivers = "--strict-waivers" in sys.argv
    print("BROTHERSBE DESIGN CHECKS  (advisory unless --strict; NO-DATA is never a pass; "
          "WAIVED is not a pass either)")
    exempt, refused, pruned = [], [], ""
    if os.path.isdir(root):
        # ALWAYS walk. This used to be `if is_dossier(root): targets = [root]`,
        # so a root that was itself a dossier skipped the walk that finds every
        # other one, and a stray `00-intake.json` in a repository root made every
        # dossier below it invisible: `--strict` printed five NO-DATA lines and
        # exited 0 over a tree holding seven unedited templates. `sbe_intake.py`
        # writes exactly that file to the directory it is run from, and the README
        # tells the reader to run it, so the off switch installed itself.
        # A root that is itself a dossier is one more dossier, not a reason to
        # stop, and find_dossiers reports it as one because os.walk starts there.
        targets, exempt, refused, scope, pruned = find_dossiers(root)
    else:
        scope = "%s is not a directory, so nothing was walked and no dossier was read" % root
        targets = []
    # Printed before any verdict, always, whatever the outcome: what root this
    # run examined, what it read there, and what inside that root contributed
    # nothing. Two runs that examined different trees must not be able to print
    # the same report.
    say("  %-10s %-8s %s" % ("scope", "-", scope + replaced))
    waived_by = {os.path.abspath(d): set(w) for d, w, _ in exempt}
    for d, waived, why in exempt:
        # Printed as a WAIVER, never as a pass and never in silence, and marked
        # so that it cannot be skimmed as one: PASS is a verdict about work that
        # was examined, and this is a declaration that nothing examined it. The
        # line names the directory and every check the waiver covers, because an
        # exemption nobody can see is an exemption nobody can withdraw, and a
        # dossier from last year blocking every unrelated merge forever is a gate
        # that gets switched off instead.
        waivers += len(waived)
        say("  %-10s %-8s %s" % (">> dossier", "WAIVED", one_line(
              "%s: %s waives %s here (of %d design checks), stated reason: %s. Nothing opened a file "
              "for %s in that directory, so this is a waiver and not a verdict about the work"
              % (os.path.relpath(d, root), EXEMPT, ", ".join(sorted(waived)), len(CHECKS),
                 " ".join(why.split())[:200],
                 "those checks" if len(waived) < len(CHECKS) else "any check"))))
    for d, problem in refused:
        fails += 1
        say("  %-10s %-8s %s" % ("dossier", "FAIL", one_line(
              "%s carries a %s that does not exempt anything: %s. An exemption waives all %d design "
              "checks for a dossier, which is more than a tier override waives, so it states a "
              "reviewable reason or it does not exempt; this dossier is checked below"
              % (os.path.relpath(d, root), EXEMPT, problem, len(CHECKS)))))
    if not targets:
        if exempt:
            # Saying "no dossier found under X" underneath a waiver naming one is
            # a false sentence, and with SBE_DOSSIER_ROOT set it FAILed with
            # "holds no dossier" about a root that demonstrably held one.
            say("  %-10s %-8s %s" % ("dossier", "NO-DATA",
                  "every dossier found under %s (%d) is waived by a %s, so no check opened a file. "
                  "The waiver line(s) above name each one and the reason given"
                  % (root, len(exempt), EXEMPT)))
        elif configured:
            fails += 1
            say("  %-10s %-8s %s" % ("dossier", "FAIL",
                  "SBE_DOSSIER_ROOT=%s holds no dossier (no directory under it contains %s or any of "
                  "01 through 07); this repository declares that it keeps dossiers, so an empty dossier "
                  "root is a broken configuration, not an absence%s" % (root, INTAKE, pruned)))
        else:
            say("  %-10s %-8s %s" % ("dossier", "NO-DATA",
                  "no dossier found under %s: no directory contains %s or any of 01 through 07. If this "
                  "repository is supposed to carry one, set SBE_DOSSIER_ROOT to where dossiers live and "
                  "this becomes a FAIL instead of a report%s" % (root, INTAKE, pruned)))
        # Every requested check still accounts for itself. A check that prints no
        # line is indistinguishable from a check that was removed, and "the gate
        # said nothing" must never be readable as "the gate was satisfied".
        # The phrase "so this check opened no file" is load-bearing: it is how
        # evals/test_no_data_class.py tells a verdict this fallback printed from a
        # verdict the check itself produced, so a scenario cannot be counted as
        # covering a check it never reached. Change it in both places or not at all.
        for name in which:
            # Two different absences, two different sentences: "no dossier under
            # X" printed two lines under a waiver naming one was a run
            # contradicting itself. The load-bearing phrase stays in both.
            if exempt:
                say("  %-10s %-8s %s" % (name, "NO-DATA",
                      "every dossier under %s is waived, so this check opened no file%s"
                      % (root, pruned)))
            else:
                say("  %-10s %-8s %s" % (name, "NO-DATA",
                      "no dossier under %s, so this check opened no file%s" % (root, pruned)))
    if targets and pruned:
        # A dossier the walk refused to enter is a dossier no verdict below covers.
        say("  %-10s %-8s %s" % ("dossier", "NO-DATA",
              "%d dossier(s) checked under %s%s" % (len(targets), root, pruned)))
    for target in targets:
        # UNCONDITIONAL. This line used to print only when there was more than
        # one target or the target differed from the root, which made the single
        # substituted target the one case that printed nothing at all: exactly
        # the case where the reader most needs to be told what was opened.
        say("  dossier: %s (under %s)" % (os.path.relpath(target, root), root))
        # Every verdict below carries the dossier it came from inside its own
        # sentence, because a reader (and a log parser) takes one line at a time
        # and a heading two lines up is not part of the verdict.
        examined = "; examined %s under %s" % (os.path.relpath(target, root), root)
        skip = waived_by.get(os.path.abspath(target), set())
        for name in which:
            if name in skip:
                waivers += 1
                say("  %-10s %-8s %s" % (">> " + name, "WAIVED",
                      "%s in %s names this check, so nothing opened a file for it here"
                      % (EXEMPT, os.path.relpath(target, root))))
                continue
            verdict, ev = run_guarded(name, CHECKS[name], target)
            if verdict == "FAIL":
                fails += 1
            # one_line: evidence quotes artifact content, and an artifact
            # carrying a newline must not write its own report lines.
            say("  %-10s %-8s %s [severity: %s]"
                  % (name, verdict, one_line(ev) + examined, CHECKS[name].severity))
    if waivers:
        say("WAIVERS: %d check(s) were waived by a %s and examined nothing. A waiver is not a "
              "pass; run `--strict --strict-waivers` to make one block a merge." % (waivers, EXEMPT))
    if strict_waivers and waivers:
        fails += waivers
    if strict and fails:
        say("STRICT: %d design check(s) failed; exiting nonzero to block the merge." % fails)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("sbe_design: error %r" % (e,))
        sys.exit(1 if "--strict" in sys.argv else 0)

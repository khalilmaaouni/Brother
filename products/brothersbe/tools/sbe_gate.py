#!/usr/bin/env python3
"""BrotherSBE hard gates: the four silent-failure classes made mechanical.

This is the teeth of the trust architecture. Each subcommand inspects a
deliverable directory (default: the current directory, and exactly the
directory named, never a silently substituted git top level) for the receipts
that prove a check RAN, and reports PASS / FAIL / NO-DATA per the class. Two modes,
one truth:
  default   advisory. Prints the verdict, exits 0. A session gets told.
  --strict  enforcing. Exits nonzero on any FAIL. CI runs this. A merge gets stopped.

The classes (ratified 2026-07-24):
  numbers   Every figure that could reach a decision ships with a numbers-manifest
            whose second, independently-scripted derivation RE-RAN to zero drift
            against a PINNED snapshot (live-warehouse drift is expected; the gate
            fails loudly if no snapshot id is recorded, rather than silently).
            Pinned means a snapshot id that is an answer, independent means the
            second query differs by more than case and whitespace, and zero drift
            is a comparison of two NUMBERS. `snapshot_id: "TODO"` with both rerun
            values `"pending"` once produced "1 figure(s) each with a pinned,
            independently re-derived, zero-drift check" at exit 0.
  migration A forward and a reverse migration, both with a receipt showing they
            ran against a restored copy, the reverse receipt recording a rehearsal
            run id AS A NON-BLANK STRING, and row counts that were RECORDED and
            that match. Recorded, not merely keyed: two empty strings compare
            equal, so a gate whose only emptiness test was `is None` reported
            "1 row-count comparison(s) matched" about a pair of blanks.
            Two limits, stated because the evidence line used to overstate both:
            this gate does not resolve the rehearsal id against any job system,
            and a receipt with no row counts is NO-DATA rather than a pass,
            because the reverse restoring the rows is the half it cannot assert
            without them.
  approval  Money or partner-facing change carries a named human approval bound
            to something stronger than a typed name. Two paths, and they are NOT
            equally strong, so this says exactly what each one proves:
              Approved-by: with a signature THIS HOST verified against a key it
                TRUSTS (git %G? = G, and G alone) proves a trusted key holder
                signed the commit. U (valid signature, no matching principal) is
                what a self-generated key produces under SSH signing, so it is
                NO-DATA, never an approval.
              Reviewed-in: <review id> proves only that a non-vacuous id was
                written into the commit message. This gate does not resolve it
                against any platform, so an agent can type one. It is a pointer
                for a human to follow, not a forgery-resistant control, and its
                verdict is NO-DATA for exactly the reason an unverifiable
                signature's is: the host cannot check either one, and two
                identical epistemic states get one verdict. Resolve it in CI (a
                job that queries your review platform for that id) if you need
                it to be a control. There is NO shape check on the id beyond
                refusing the vacuity tokens; "an id in the right shape" was a
                claim the regex \\S+ never made.
            A bare typed name FAILS. A signature the host cannot check (git
            %G? = E, the normal result on a runner with no imported keys) is
            NO-DATA, never an approval: CI must import the signer's public keys,
            or the team standardises on the weaker keyless Reviewed-in: path
            knowing what it does and does not prove.
            SELF-APPROVAL FAILS. The Approved-by identity is compared against
            the commit's own author and committer (%an, %ae, %cn, %ce), because
            one person with one key authored, signed and approved a payout
            change and this gate printed its strongest sentence over it. A
            signature proves a key holder signed and cannot prove a second party
            looked, and a second party is the whole content of the word approval.
  ran       No SQL or pipeline change is done until its reconciliation query or
            test executed: a receipt with a nonzero-duration run and an exit code.

Honesty law inherited from the chassis: absent evidence is NO-DATA, never PASS.
Three states, not two, and the difference is load-bearing: a missing receipt is
NO-DATA (nothing was claimed), a receipt that exists but records zero items is
NO-DATA naming that fact (a claim of nothing is still nothing), and a receipt
that exists and cannot be parsed is a FAIL (a broken claim, not an absent one).
A gate that reported PASS over zero items would print evidence asserting work
that was never inspected, which is worse than having no gate at all.
Fabricated-receipt defense: presence is necessary but the gate also checks the
receipt is INTERNALLY CONSISTENT (a run id resolves to a nonzero duration and an
exit code, a manifest's second query differs textually from the first), because
the operating record proves pasted receipts get invented.

Stale-receipt defense: a numbers-manifest, migration-receipt or ran-receipt may
now name the commit it was made against in an optional `headCommit` field, the
same field name and the same comparison src/brothersbe/evidence.py's own
`sbe evidence` receipt store already carries. When the field is present and
names a commit that is not the directory's current HEAD, the receipt is stale
(the code it covered has moved on since) and the gate FAILs rather than PASSes
over it: a passing receipt copied forward from an earlier commit no longer
clears the gate at the new one. A receipt naming no commit at all is not new
behavior BY DEFAULT; every receipt written before this field existed is one,
and this gate cannot tell that case apart from an operator who chose not to
record one, so it is left exactly as this gate always treated it unless
`--require-headcommit` is passed, in which case an unbound receipt is
NO-DATA rather than a silent PASS. See _current_head, _commit_problem,
_unbound_receipt_problem, and docs/KNOWN-LIMITS.md for the boundary this
still leaves open by default.

A directory that holds gate-artifact-shaped files without being live work (a
finished project's old receipts, a teaching example) carries a `.sbe-exempt`
file whose contents say why, and the report prints that reason on every run as
a WAIVER, exactly as sbe_design.py's own `.sbe-exempt` already does for a
dossier: never PASS, never silently skipped, and never a quiet gate when the
file itself is broken. See parse_exemption for the format and find() for "in
or under".
"""
import datetime, json, os, shutil, sys, re, subprocess, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The package beside these tools, mounted the same way `sbe_decision_verify.py`
# mounts it. `_receipt_still_binds` reads the receipt freshness rule out of
# `brothersbe.evidence` rather than keeping a second copy of it here, and
# without this line that import fails and the helper answers False for every
# receipt: a rule that reads as "no" whatever the tree says.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
from sbe_checks import (Check, run_guarded, answered, answered_as, numeric, count,
                        derivation_fold, distinct, fold, one_line, say, skeleton, unit_of,
                        Pruner, scope_note, evidence_problem, unreadable_identity_words,
                        bidi_reordering_controls, could_render_same, name_sets_could_collide)
# The producer reading is IMPORTED, never re-derived here. `sbe_passport.py`
# already classified this exact field for its own report, and a second copy of
# the rule in this file is the mirrored-parser shape that cost this project
# three review defects in one change: two parsers of one format drift, and the
# drift is invisible because both sides keep printing sentences that sound
# right. One reading, two consumers.
from sbe_passport import (producer_class, PRODUCER_BUILD_SYSTEM,
                          PRODUCER_OFF_BUILD_SYSTEM)
# A3 reads the behaviour table's Proof column, and it reads it with the parser
# that already owns that table's format rather than a second one written here.
# `check_behaviour` in sbe_design.py is the other consumer; a private copy of
# the row reader in this file would be free to disagree with it about what a
# row even is, and the two verdicts would then describe different tables while
# naming the same file.
from sbe_design import _behaviour_rows, ARTIFACT_FILES

MANIFEST = "numbers-manifest.json"
MIGRATION_RECEIPT = "migration-receipt.json"
APPROVAL_FILE = "APPROVAL"
RAN_RECEIPT = "ran-receipt.json"
EXEMPT = ".sbe-exempt"


def load_receipt(path):
    """Parse a receipt into (object, error).

    error is non-empty when the file exists but is not a usable receipt, and the
    caller turns that into a FAIL: a receipt that cannot be read is a broken
    claim, not an absent one. Absence of the file is NO-DATA, decided elsewhere
    on purpose.

    Valid JSON of the wrong TYPE is a broken claim too, and it used to be the
    hole: `json.load` returned a list or a string happily, the gate called .get
    on it, the exception escaped to the top-level handler, every gate after it
    never ran, nothing printed a verdict for any of them, and advisory mode
    exited 0. A crash that deletes a gate is the absent-check defect wearing a
    traceback. The type is checked here, once, where the file is opened.

    ACCESS is checked BEFORE open(): a FIFO where a receipt was expected used to
    hang this tool forever in both modes, with no verdict line at all, which is
    worse than any crash because a crash at least prints a FAIL.
    """
    problem = evidence_problem(path)
    if problem:
        return None, problem
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, ValueError) as e:
        return None, "not readable as JSON (%s)" % type(e).__name__
    if not isinstance(obj, dict):
        return None, ("top-level JSON is %s, not an object; a receipt is a JSON object"
                      % type(obj).__name__)
    return obj, ""


def _items(d, key):
    """Return (items, note) for a receipt's item list.

    items is [] when the key is missing, is not a list, or is an empty list.
    note names the reason so the NO-DATA evidence can distinguish a genuinely
    empty receipt from a misspelled key, which otherwise look identical.
    """
    v = d.get(key)
    if isinstance(v, list) and v:
        return v, ""
    if v is None:
        other = ", ".join(sorted(k for k in d if k != key))
        return [], ("no '%s' list; top-level keys present: %s" % (key, other)
                    if other else "no '%s' key and nothing else in the file" % key)
    if not isinstance(v, list):
        return [], "'%s' is %s, not a list" % (key, type(v).__name__)
    return [], "'%s' is an empty list" % key


def _read_text(path):
    """A `.sbe-exempt` file's text, or "" when it is absent or cannot be read.

    Scoped to exemption files only: every other piece of free text this tool
    reads is JSON (load_receipt) or git output (git_trailers). Mirrors
    sbe_design.py's `read`, which also treats an unreadable exemption the same
    as an empty one rather than inventing a fourth state nothing downstream
    asks for: parse_exemption("") is already refused, by name, as "no gates
    and no reason".
    """
    if evidence_problem(path):
        return ""
    try:
        return open(path, encoding="utf-8", errors="replace").read().lstrip("﻿")
    except OSError:
        return ""


ADDRESSED_TO_CHECKS = "addressed to tools/sbe_design.py: it names checks: and no gates:"


def _owner_expiry(owner, expires):
    """(suffix to append to the exemption reason, problem) for an `owner:` /
    `expires:` pair. Mirrors tools/sbe_design.py::_owner_expiry exactly: same
    keys, same validation, same message wording. This is the load-bearing
    copy: the three new gate refusals are waived through THIS parser, so an
    exemption's owner/expiry state has to be decided here, not merely echoed
    from the design side.

    Neither key present is the grandfather path, not a problem: every
    exemption already in a tree was written before these two fields
    existed, and the ratified direction here is paved road, not forced
    road, so this stays a report rather than a block. Exactly one of the
    two present IS a problem: half an exception is not an exception.

    An expired or malformed `expires:` returns a non-empty problem, which
    the caller turns into a FAIL, never a NO-DATA: this mirrors this file's
    own load_receipt docstring, which calls a receipt that exists but
    cannot be read a broken claim rather than an absent one. An exemption
    with an expiry date in it is present; a present date that has passed,
    or is not a real date, is unusable the same way.

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
    """(gates waived, reason, problem) for a `.sbe-exempt` file.

    Mirrors tools/sbe_design.py::parse_exemption, one registry swapped for the
    other (design's five checks become this file's four hard gates). Format:

        gates: approval, numbers
        reason: this directory holds a finished project's old receipts, kept
                for history and not live design work.

    Not free text. A file naming no gates and no reason would waive whatever
    gate artifact sits in or under it by default, which is an off switch and
    not an exemption, so the gates are named one by one, exactly as design's
    `checks:` field is. A wildcard is not the name of a gate: `gates: *` is
    refused BY NAME, the same way design refuses `checks: *`, rather than
    silently waiving all four. A blank or whitespace-only reason is refused as
    its own FAIL naming the file, because a waiver nobody can review is not an
    exemption.

    What this does NOT do, stated as plainly as design's own docstring states
    it: it cannot tell a real reason from a well-formed fake one. No mechanical
    test can. That is why a waiver prints as WAIVED, quoting the reason, and
    never as PASS.
    """
    gates, reason_lines, seen_reason = [], [], False
    owner, expires = None, None
    reason_hid_a_key = False
    for line in (text or "").splitlines():
        m = re.match(r"(?i)^\s*gates\s*:\s*(.*)$", line)
        if m and not seen_reason:
            gates.extend(g.strip() for g in re.split(r"[,\s]+", m.group(1)) if g.strip())
            continue
        # owner:/expires: are matched here, but ONLY ahead of reason:, the same
        # not-seen-reason guard gates: already uses two lines up. reason:
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
    names = ", ".join(sorted(GATES))
    if not gates and not seen_reason:
        return [], (text or "").strip(), (
            "it names no gates and no reason. An exemption states which of the %d hard gates it "
            "waives and why, as two fields:  gates: %s  and  reason: <why>. A file of free text "
            "waives whatever gate artifact sits in or under it by default, which is an off switch "
            "and not an exemption" % (len(GATES), names))
    if not gates and any(re.match(r"(?i)^\s*checks\s*:", l) for l in (text or "").splitlines()):
        # Addressed to the design checks (tools/sbe_design.py), not to the hard
        # gates: that scanner honors and PRINTS it as a waiver, so skipping it
        # here leaves it visible in exactly one shipped report rather than
        # refused by the scanner it never spoke to. This is how the shipped
        # templates/dossier exemption, which waives five design checks over
        # deliberately unfilled templates, stops reading as a broken GATE file.
        return [], reason, ADDRESSED_TO_CHECKS
    if not gates:
        return [], reason, ("it records a reason but names no gates; list the ones it waives "
                            "(gates: %s), because an exemption that names nothing waives everything"
                            % names)
    unknown = [g for g in gates if g not in GATES]
    if unknown:
        return [], reason, ("it waives %s, which %s not a hard gate; the gates are %s"
                            % (", ".join(unknown), "are" if len(unknown) > 1 else "is", names))
    if not reason:
        return [], reason, "no reason is recorded; a waiver a human cannot review is not an exemption"
    suffix, problem = _owner_expiry(owner, expires)
    if problem:
        return [], reason, problem
    return gates, reason + suffix, ""


def _covering_exemption(directory, exempt_dirs, gate):
    """The reason of the exemption covering `directory` for `gate`, or None
    when nothing covers it. Never a pair: a caller that needs the covering
    directory too has `exempt_dirs` in hand to look it back up, so this stays
    a single value rather than a two-element result a verdict-shaped lint
    would have to be told, by name, is not one.

    "In or under": the exempt directory can be an ancestor of the artifact's
    own directory, not only the same directory. When more than one covering
    exemption is nested, the closest one to the artifact wins, because that is
    the reason a reviewer reads next to the file it names.
    """
    best_path, best_reason = None, None
    target = os.path.abspath(directory)
    for edir, egates, ereason in exempt_dirs:
        if gate not in egates:
            continue
        e = os.path.abspath(edir)
        if target == e or target.startswith(e + os.sep):
            if best_path is None or len(e) > len(best_path):
                best_path, best_reason = e, ereason
    return best_reason


def find(root, name, gate):
    """(live receipt paths, [(path, reason)] waived receipts, [(dir, problem)]
    broken `.sbe-exempt` files, the sentence every verdict built on them must
    carry).

    The note is the last return value and every caller prints it, because a
    directory this walk refused to enter is a directory the verdict does not
    cover, and "no numbers-manifest found" over a tree that holds one is this
    project's headline defect class wearing the walker's clothes.

    The note now opens with sbe_checks.scope_note: the root actually read, the
    receipts actually opened, and the count of directories inside the requested
    scope that carried none. This walker is the one place every gate acquires
    its evidence, so it is the one place the rule can hold for every gate at
    once, including a gate added later. Closing it inside a single gate is what
    was already tried: a parent holding three change directories, one of them
    with no receipt at all, printed "ran PASS 5 recorded check(s)" over the
    pool, and the silent directory alone printed NO-DATA.

    `gate` is the GATES key this call hunts evidence for. An artifact found IN
    OR UNDER a directory carrying a `.sbe-exempt` that names this gate is split
    into `waived` rather than `hits`: nothing opens it, mirroring what
    sbe_design.py's exemption already means for a design check. `waived` still
    carries the artifact's own path and the exemption's own reason, because the
    caller reports it as WAIVED, quoting that reason, and never drops it in
    silence. `refused` carries every `.sbe-exempt` this walk read that does not
    exempt anything; the caller reports each one as its own FAIL, once, however
    many of the four gates rediscover it, because a broken control is broken
    whether or not a gate artifact happens to sit under it today.

    Exemptions are collected top-down as the walk descends: os.walk visits a
    directory before any of its children, so a `.sbe-exempt` at a parent is
    already known by the time a child directory's artifact is tested against
    it, including the artifact's own directory, tested after its own file is
    read in the same iteration.
    """
    hits, waived, refused, misplaced, entered = [], [], [], [], []
    exempt_dirs = []   # [(dir, gates waived, reason)], grows as the walk descends
    pruner = Pruner()
    # onerror: a directory this walk cannot enter must land in the note, not in
    # silence. os.walk swallows the OSError by default, so a chmod 000 directory
    # holding a receipt used to vanish from a sentence that read as complete.
    for dp, dns, fns in os.walk(root, onerror=pruner.onerror):
        entered.append(dp)
        if EXEMPT in fns:
            gates, reason, problem = parse_exemption(_read_text(os.path.join(dp, EXEMPT)))
            if problem == ADDRESSED_TO_CHECKS:
                # The design scanner owns this file and prints its waiver;
                # nothing is waived and nothing is refused here.
                pass
            elif problem:
                refused.append((dp, problem))
            else:
                exempt_dirs.append((dp, gates, reason))
        # Match directory NAMES, not a substring of the path: `.git` as a
        # substring test also hid `.github/`, so the workflow that wires these
        # gates into CI was invisible to every one of them. And then not by name
        # at all: see sbe_checks.skip_reason.
        if name in dns:
            # A DIRECTORY wearing the receipt's name is not a receipt, and
            # consuming the name in silence printed "no <receipt> found" over a
            # tree holding something by that name.
            misplaced.append(os.path.join(dp, name))
        dns[:] = pruner(dp, dns)
        if name in fns:
            path = os.path.join(dp, name)
            cover_reason = _covering_exemption(dp, exempt_dirs, gate)
            if cover_reason is not None:
                waived.append((path, cover_reason))
            else:
                hits.append(path)
    note = "; " + scope_note(root, name, hits, entered) + pruner.note(lambda f: f == name)
    if misplaced:
        note += ("; a DIRECTORY named %s sits at %s and was not read, because a receipt "
                 "is a file" % (name, ", ".join(misplaced[:2])))
    return hits, waived, refused, note


def _partial(nothing, checked, kind, unit):
    """Evidence for a run where some receipts held items and some declared none.

    Reporting PASS here was the headline fix surviving in a repository with more
    than one deliverable, which is the normal case: the note naming the empty
    receipt was computed, collected, and then discarded unless EVERY receipt was
    empty. One good manifest anywhere in the tree restored the old behaviour for
    all the rest. A verdict covers every receipt it walked or it names the ones
    it could not cover.
    """
    return ("NO-DATA", "%d %s verified, but %d %s present that declares none (%s); "
                       "the verdict cannot cover a receipt with nothing in it, so it says so "
                       "instead of passing over it"
            % (checked, unit, len(nothing), kind, "; ".join(nothing[:4])))


def _current_head(root):
    """git rev-parse HEAD in root, or None when root carries nothing to bind a
    receipt to: not a git checkout at all, or a checkout with zero commits (an
    unborn HEAD). None is not a problem, it is nothing to check against,
    exactly as src/brothersbe/evidence.py's own _check_commit no-ops the same
    way when HEAD does not resolve there. A fixture directory built with no
    git history at all is unaffected by the commit-binding check below,
    precisely because there is no HEAD a receipt could be stale against.
    """
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        # No git binary, or a timed-out process: the same broad catch
        # git_trailers above already makes, read here as "no HEAD resolved"
        # rather than re-raised, and handled by the ordinary check below.
        out = None
    if out is None or out.returncode != 0:
        return None
    head = out.stdout.strip()
    return head or None


def _commit_problem(d, head, rel, root=None, full=None):
    """The FAIL sentence for a receipt whose recorded headCommit has moved on
    from the current HEAD, or None when there is nothing wrong.

    head is the directory's current HEAD (see _current_head), or None when
    there is nothing to bind against; in that case this never returns a
    problem, because no HEAD means no receipt here can be stale relative to
    one.

    A receipt that records no headCommit at all is not judged here: the field
    is new here, and this gate cannot tell "a receipt written before this
    field existed" apart from "an operator who chose not to record one". What
    IS judged, because it is the reproduced bypass this check exists to close,
    is a receipt that DOES name a commit, where that commit is not HEAD: the
    code the receipt covered has moved on, the receipt is stale evidence for
    the CURRENT change, and a stale receipt does not PASS. This is the same
    mismatch src/brothersbe/evidence.py's own _check_commit already treats as
    a broken claim, applied here to the older hard-gate receipt shapes that
    never carried the concept until now.
    """
    if head is None:
        return None
    claimed = answered(d.get("headCommit"))
    if claimed is None:
        return None
    if not isinstance(claimed, str):
        return ("%s: headCommit is %s, not a commit id string, so nothing here confirms "
                "which commit this receipt covers" % (rel, type(claimed).__name__))
    if claimed != head:
        # ONE RULE, ONE IMPLEMENTATION. This used to be exact equality, which
        # this gate's own docstring above described as "the same mismatch
        # src/brothersbe/evidence.py's own _check_commit already treats as a
        # broken claim". It was not the same any more: that reader lets a
        # receipt survive commits that changed nothing it covers, and this
        # gate went on failing receipts the reader had called sound, which is
        # how every hard-gate receipt in this estate's own release dossier
        # read FAIL (roadmap row E83). Asking the reader is the fix; keeping a
        # second copy of the rule in step by hand is not.
        if root is not None and _receipt_still_binds(d, root, full):
            return None
        return ("%s: headCommit %s is not the current head %s: this receipt is evidence for "
                "a commit that is no longer checked out, the code it covered has moved, and a "
                "stale receipt does not PASS" % (rel, claimed[:12], head[:12]))
    return None


def _receipt_still_binds(d, root, full):
    """`evidence.commit_binding_holds`, or False when this checkout cannot
    answer at all.

    FALSE IS THE SAFE HALF and it is what an unreadable answer returns: an
    import that fails, a directory that is not a work tree, or a reader that
    raises leaves the caller's stale finding exactly where it was. This never
    turns a FAIL into a PASS on the strength of something it could not read.
    """
    try:
        from brothersbe import evidence as evidence_mod
    except ImportError:
        return False
    try:
        return bool(evidence_mod.commit_binding_holds(d, root, full))
    except (OSError, ValueError, TypeError, KeyError):
        return False


#: Set once in `main()` from `--require-headcommit`, the same module-global
#: shape `STRICT_PRODUCER` above already uses and for the same reason: every
#: gate function's signature is `(root)`, and `run_guarded` calls them that
#: way, so a parameter here would change every gate to carry a flag only
#: three of them read.
#:
#: DEFAULT FALSE. `_commit_problem` above, and `docs/KNOWN-LIMITS.md`'s "A
#: hard-gate receipt with no headCommit still passes unbound", both state the
#: gap this flag closes for whoever turns it on: every shipped example
#: receipt in this repository (`docs/for-engineers/examples/`, the worked
#: receipts `docs/guides/05-a-worked-engagement.md` writes, and most of
#: `evals/run_evals.py`'s own cases) predates the `headCommit` field and
#: carries none, so defaulting this ON would regress every one of them from
#: PASS to NO-DATA in the same change that added the field. That rewrite is
#: named as future work in the doc above and is not this flag's job; this
#: flag lets a caller who HAS moved its own receipts past that gap (a "T2 and
#: above" caller, in the vocabulary the task that added this flag used) opt
#: in today without waiting for it.
REQUIRE_HEADCOMMIT = False


def _unbound_receipt_problem(d, head, rel):
    """The NO-DATA sentence for a receipt that names no `headCommit` at all,
    read only when the module-level `REQUIRE_HEADCOMMIT` is True, or None
    otherwise (including every receipt `_commit_problem` above already
    judges: a receipt that DOES name a commit is that function's problem to
    raise, never this one's to raise a second time).

    `_commit_problem` never judges an unbound receipt either way -- see its
    own docstring -- because this gate cannot tell "a receipt written before
    the field existed" apart from "an operator who chose not to record one".
    `REQUIRE_HEADCOMMIT` is the opt-in that answers that question by policy
    instead of by guessing: once a caller decides every receipt it accepts
    must name the commit it covers, silence is no longer an unreadable
    signal, it is a fact this gate can act on -- NO-DATA, never a silent
    PASS and never a FAIL nothing here actually observed (the receipt may be
    perfectly sound evidence for SOME commit; it simply never says which
    one, so this gate cannot bind it to the code currently checked out).

    `head is None` (no git history in this directory to bind against, the
    same case `_commit_problem` skips) returns None unconditionally: with no
    HEAD, there is nothing to require binding TO.
    """
    if not REQUIRE_HEADCOMMIT or head is None:
        return None
    claimed = answered(d.get("headCommit"))
    if claimed is not None:
        return None
    return ("%s: no headCommit is recorded, and this run requires one "
           "(--require-headcommit); a receipt that never names the commit it covers cannot "
           "be bound to the code actually checked out, so this is NO-DATA, not a silent PASS"
           % rel)


def git_trailers(root):
    """(commit body, signature state, identities that WROTE the commit, meta).

    The identities are the addition, and they are the whole of the self-approval
    fix. The gate read the trailer and the signature state and never asked who
    authored the commit, so one person with one key produced a commit they
    authored, signed and approved, and the gate printed the strongest sentence it
    owns over it. `%an %ae` is the author and `%cn %ce` the committer, which are
    the two identities git can prove wrote this commit on this host.

    meta separates what the flat list merges: {"author": [name, email],
    "committer": [name, email], "signer": %GS}. The signer principal is what
    the verifier MATCHED (under SSH signing, the allowed-signers email whose
    key produced the signature), so it is the one identity here that is bound
    to a key rather than typed into a field. The committer/author split
    exists because an approver who amends and signs BECOMES the committer:
    that is git's only spelling of "the reviewer signed", and lumping the
    committer in with the author made the gate refuse the strongest honest
    approval shape git can express.
    """
    try:
        out = subprocess.run(["git", "-C", root, "log", "-1",
                              "--format=%B%n---%n%G?%n%GS%n%an%n%ae%n%cn%n%ce"],
                             capture_output=True, text=True, timeout=10)
        body, _, tail = out.stdout.rpartition("\n---\n")
        parts = (tail.split("\n") + [""] * 6)[:6]
        sig = parts[0].strip()
        signer = parts[1].strip()
        ids = [p.strip() for p in parts[2:]]
        who, seen = [], set()
        for p in ids:
            if p and p not in seen:
                seen.add(p)
                who.append(p)
        meta = {"author": [p for p in ids[:2] if p],
                "committer": [p for p in ids[2:] if p],
                "signer": signer}
        return body, sig, who, meta
    except Exception:
        return "", "N", [], {"author": [], "committer": [], "signer": ""}


#: git learned to verify SSH signatures (gpg.format=ssh) in 2.34.0. The same
#: floor `sbe doctor` checks (GIT_SSH_VERIFY_FLOOR, src/brothersbe/cli.py);
#: named here rather than imported, under the same one-directional-dependency
#: rule EVIDENCE_RUN_GENERATOR follows at the bottom of this file.
GIT_SSH_VERIFY_FLOOR = (2, 34)


def _git_version():
    """(major, minor) of the git on PATH, or None when it cannot be read;
    the same read `sbe doctor` performs, mirrored not imported."""
    try:
        out = subprocess.run(["git", "--version"], capture_output=True,
                             text=True, timeout=10)
    except Exception:
        return None  # sbe: allow-silent this helper's whole contract is "the
        # (major, minor) of the git on PATH, or None when it cannot be read";
        # every caller treats None as cannot-read and the approval gate then
        # keeps its generic NO-DATA sentence, so nothing is masked and no
        # verdict moves. The same shape main waived for hostname parsing.
    m = re.search(r"(\d+)\.(\d+)", out.stdout or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _git_config(root, key):
    """The repository-effective value for `key`, "" when unset or unreadable."""
    try:
        p = subprocess.run(["git", "-C", root, "config", "--get", key],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _head_signature_is_ssh(root):
    """True when HEAD's embedded signature armor is an SSH signature block,
    read from the gpgsig header of the raw commit object rather than from
    %G?, because the whole point is to answer correctly on a git too old to
    verify the format. ONLY that header and its continuation lines are
    searched, never the whole object: the commit MESSAGE is author-written
    text, and a message merely quoting an SSH armor line must not select
    the SSH remedies for a PGP signature. The substring form of this check
    did exactly that, found by two independent reviews and reproduced
    against a forged commit object before this parse replaced it."""
    try:
        out = subprocess.run(["git", "-C", root, "cat-file", "commit", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    in_sig = False
    for line in (out.stdout or "").split("\n"):
        if not line:
            break
        if in_sig and line.startswith(" "):
            if "BEGIN SSH SIGNATURE" in line:
                return True
            continue
        in_sig = line.split(" ", 1)[0] in ("gpgsig", "gpgsig-sha256")
        if in_sig and "BEGIN SSH SIGNATURE" in line:
            return True
    return False


_EMAIL = re.compile(r"[^\s<>,;()]+@[^\s<>,;()]+")


def _identity_parts(text):
    """(canonical emails, name word-sets) inside one name-and-email string.

    The comparison this feeds is a SELF-APPROVAL guard on the money gate, so it
    reads identities the way a person forges them, not the way a parser enjoys
    them. The old version folded case and whitespace and compared whole strings,
    so every one of these defeated it while the PASS line printed "that identity
    is not the commit's author or committer" beside a parenthesis naming the
    identity it matched: a trailing period (`Dana Author.`), a reordering
    (`Author, Dana`), an initial (`D. Author`), a plus-address
    (`dana+ops@example.com`) and a role suffix (`Dana Author (approver)`).

    So: emails are canonicalized (case folded, a `+tag` in the local part
    dropped, because `dana+ops@` is the same mailbox as `dana@`); names are
    compared as SETS of words split by Unicode CLASS (_name_words: punctuation
    and symbols are decoration, not identity, wherever they sit), so order, a
    stray period and a pair of brackets stop mattering; a parenthesized suffix
    is annotation, not identity; and a single-letter word is an initial,
    matched against any word sharing its first letter by _names_overlap.

    Everything reads through sbe_checks.skeleton(), not fold(), and that is
    the sixth and seventh forgery closed: one soft hyphen inside the name or
    the domain renders as nothing and made the canonical mailbox stop
    matching, and one Cyrillic letter renders as its ASCII twin and made the
    author a "different person". An identity is compared as a reader sees it,
    invisible characters removed and homoglyphs mapped to the letter they
    render as, so an Approved-by value that is not confusably distinct from
    the author reads as the author.
    """
    return _parts(text, skeleton)


def _rendered_identity_parts(text):
    """_identity_parts without the skeleton reduction: the same email
    canonicalization, suffix stripping and word split, on fold()ed text,
    keeping every character the fold cannot read so could_render_same and
    name_sets_could_collide can class it. The skeleton form decides proven
    SAMENESS (self-approval); this form decides whether difference is
    PROVEN, which is the only ground a certifying PASS may stand on.
    """
    return _parts(text, fold)


_GMAIL_HOSTS = frozenset({"gmail.com", "googlemail.com"})


def _canonical_email(raw, reduce):
    """The comparable form of one email address: case-folded (via `reduce`),
    a `+tag` in the local part dropped, and, ONLY on gmail.com and
    googlemail.com, every dot in the local part ALSO dropped.

    The dot fold is HOST-SCOPED, not universal, because dot-insensitivity is
    a property of gmail's own mail routing (first.last@gmail.com and
    firstlast@gmail.com are one inbox there), not a property of email
    addressing in general. Folding dots on every host would wrongly merge
    two genuinely distinct mailboxes wherever a dot IS significant to that
    host (first.last@ and firstlast@ are two different people on most
    other providers), and a comparison that merges two real identities is a
    false REFUSAL on this gate, the opposite failure from the one this fold
    exists to close. So: the dot is folded only where the two spellings are
    PROVABLY the same mailbox by the host's own rule, and nowhere else.
    """
    e = reduce(raw).strip(" .,;:'\"<>")
    local, _, domain = e.partition("@")
    local = local.split("+")[0]
    if domain in _GMAIL_HOSTS:
        local = local.replace(".", "")
    return local + "@" + domain


def _same_mailbox_note(canon, appr_text, who_texts):
    """A clause naming the two literal recorded addresses when a same-email
    match in CANON exists only because of gmail's dot fold, not because the
    two sides typed the identical string; empty for every other match, so an
    exact-string match keeps the plain self-approval sentence unchanged.

    Printing the canonical (already-folded) form alone reads as one address
    typed twice; a forged-difference finding is disproved by name, so the
    two addresses that actually appear in the commit are the ones this
    clause quotes.
    """
    domain = canon.rpartition("@")[2]
    if domain not in _GMAIL_HOSTS:
        return ""
    appr_raw = next((e.strip() for e in _EMAIL.findall(appr_text)
                      if _canonical_email(e, skeleton) == canon), "")
    other_raw = next((e.strip() for t in who_texts for e in _EMAIL.findall(t)
                       if _canonical_email(e, skeleton) == canon), "")
    if not appr_raw or not other_raw:
        return ""
    if skeleton(appr_raw).strip(" .,;:'\"<>").lower() == skeleton(other_raw).strip(" .,;:'\"<>").lower():
        return ""
    return (" %s and %s reach one mailbox under %s's own dot-insensitive local part, "
            "not two identities." % (appr_raw, other_raw, domain))


def _is_script_neutral_modifier(ch):
    """A modifier letter belonging to no script (U+02BC and its neighbours).

    Category Lm makes these letters to Unicode; to a reader they are the
    apostrophes, primes and raised marks that decorate a name. The ones that
    DO belong to a script carry that script in their Unicode name and are not
    matched here, so a Japanese prolonged sound mark stays a letter of the
    word it sits in.
    """
    if unicodedata.category(ch) != "Lm":
        return False
    try:
        return unicodedata.name(ch).split()[0] == "MODIFIER"
    except ValueError:
        return False


def _name_words(text):
    """The identity-carrying words of an already-reduced string, split by CLASS.

    Three repairs in this family were an ENUMERATED strip set, and each lost to
    the first character nobody had listed. The last spelling, `.,;:'\"!?`, left
    `[Dana Author]`, `**Dana Author**`, `_Dana Author_`, `{Dana Author}`,
    `Dana Author/` and `-Dana Author` as words structurally different from the
    author's own, and the certifying comparison consumed that structural
    difference as PROOF that a second person approved the money change. A
    remembered list of characters is not a rule, and a fourth list would lose
    to the fourth character nobody listed.

    The rule: what carries identity is a letter, a mark or a digit (Unicode
    general category L, M or N). Punctuation and symbols (P and S) are
    decoration wherever they sit, leading, trailing or interior, so they END A
    WORD, whatever they are and whenever they were added to Unicode. That is
    how a reader reads it: `[Dana Author]` holds the words Dana and Author, and
    so do `**Dana Author**` and `-Dana Author`; `O'Brien`, `Jean-Luc`,
    `D'Angelo` and `Anna-Maria` hold the words their owners see in them, still
    readable and still comparable.

    Whatever is neither (category C: controls, surrogates, private-use code
    points, and code points this build of Unicode does not assign, which is
    what a character newer than this interpreter looks like from here) is kept
    INSIDE the word rather than dropped, so no caller can mistake it for
    absence: _unreadable_identity_chars finds it there and the certifying
    caller refuses to certify over it.
    """
    words, word = [], []
    for ch in text:
        kind = unicodedata.category(ch)[0]
        if _is_script_neutral_modifier(ch):
            # The apostrophe of `OʼBrien` written as Unicode recommends it
            # (U+02BC, category Lm) is DECORATION, exactly like the ASCII
            # apostrophe two lines down, and a reader sees the same two words
            # either way. Kept inside the word it welded `oʼbrien` into one
            # word while `O'Brien` split into two, so the two spellings of one
            # person had no comparable part and the certifying comparison read
            # that as proof they were different people. A modifier letter that
            # belongs to no script belongs to no word; one that belongs to a
            # script (the Japanese prolonged sound mark) is a letter of it and
            # stays, which is why this is a rule over the character database
            # and not a row for one apostrophe.
            if word:
                words.append("".join(word))
                word = []
            continue
        if kind in ("L", "M", "N"):
            word.append(ch)
        elif kind in ("P", "S", "Z") or ch.isspace():
            if word:
                words.append("".join(word))
                word = []
        else:
            word.append(ch)
    if word:
        words.append("".join(word))
    return tuple(words)


def _unreadable_identity_chars(values):
    """The characters in an identity this host cannot classify AT ALL.

    _name_words reduces by class: L, M and N carry identity, P, S and Z are
    decoration or separation. What survives that is category C, a code point
    this host can say nothing about: a control, a surrogate, a private-use
    code point, or one this build of Unicode does not assign (which is exactly
    how a character added to Unicode after this code was written appears here).

    The certificate may not rest on the reducer's coverage. If the reduction
    met something it could not classify, then whether the two identities read
    the same is UNDECIDABLE, and undecidable is NO-DATA naming the character,
    never "proven different". Absence of understanding is not proof of
    difference: this project's founding law, applied to its own reducer.
    """
    return sorted({ch for v in values for ch in v
                   if unicodedata.category(ch)[0] == "C"})


def _parts(text, reduce):
    """(canonical emails, candidate name word-TUPLES) of one identity string.

    Names come back as tuples in RECORDED order, because an evidence sentence
    that quotes an identity must quote it in the order its owner wrote it,
    and up to two candidate readings are returned when brackets make the
    value ambiguous. The previous parser DELETED every `<...>` and `(...)`
    span, so an identity written wholly inside brackets parsed to an empty
    word list, and a certifying comparison then consumed the empty
    comparison's "no collision" as proof of difference: one pair of
    parentheses bought the self-approval control on the money gate. The rule
    (the founding one): a comparison must first establish that it examined
    something, and a parser must never hand a certifying caller an empty
    population where the raw value plainly holds an identity. So: an angle
    span holding an email is the email; an angle span holding anything else
    is name text wearing brackets; parenthesized text is annotation when
    words exist outside it, but it is read AS the name when nothing does,
    and the all-words reading is kept as a second candidate so a name hidden
    in an annotation still compares as a name.
    """
    text = text or ""
    emails = set()
    for m in _EMAIL.finditer(text):
        emails.add(_canonical_email(m.group(0), reduce))
    rest = re.sub(r"<([^<>@]*)>", r" \1 ", text)   # <Dana Author> is a name, not a mailbox
    rest = re.sub(r"<[^>]*>", " ", rest)
    rest = _EMAIL.sub(" ", rest)

    def words_of(t):
        return _name_words(reduce(t))

    outside = words_of(re.sub(r"\([^)]*\)", " ", rest))
    everything = words_of(rest)
    names = []
    if outside:
        names.append(outside)
    if everything and everything != outside:
        names.append(everything)
    return emails, names


def _names_overlap(a, b):
    """True when two name word-sets read as the same person.

    Equal sets are one person however the words are ordered. A subset is one
    person written shorter (`Dana` against `Dana Author`), and an initial
    expands against the other side's words first, so `D. Author` and
    `Dana Author` overlap. The direction of error is deliberate: this guards
    against self-approval on the money gate, and a rare false FAIL costs a
    reviewer one clarifying commit while a false PASS costs the control.
    """
    def expanded(x, against):
        out = set()
        for w in x:
            if len(w) == 1:
                out.add(next((v for v in against if v.startswith(w)), w))
            else:
                out.add(w)
        return out
    ea, eb = expanded(a, b), expanded(b, a)
    return bool(ea) and bool(eb) and (ea <= eb or eb <= ea)


def gate_numbers(root):
    manifests, waived, _refused, scope = find(root, MANIFEST, "numbers")
    if not manifests:
        if waived:
            return "NO-DATA", ("every %s found under %s (%d) is waived by a %s and examined nothing; "
                               "see the WAIVED line(s) for the reason%s"
                               % (MANIFEST, root, len(waived), EXEMPT, scope))
        return "NO-DATA", ("no numbers-manifest found; if this change presents no decision figure "
                           "that is correct, else add one%s" % scope)
    problems = []
    unreadable = []
    unbound = []
    nothing = []
    checked = 0
    seen_figures = []
    snapshots = []
    head = _current_head(root)
    for m in manifests:
        d, err = load_receipt(m)
        if d is None:
            unreadable.append("%s: %s" % (os.path.relpath(m, root), err))
            continue
        bp = _commit_problem(d, head, os.path.relpath(m, root), root, m)
        if bp:
            problems.append(bp)
        ub = _unbound_receipt_problem(d, head, os.path.relpath(m, root))
        if ub:
            unbound.append(ub)
        figs, note = _items(d, "figures")
        if not figs:
            nothing.append("%s: %s" % (os.path.relpath(m, root), note))
            continue
        for fig in figs:
            checked += 1
            # A figure listed twice was counted twice, so a manifest holding one
            # object duplicated reported "2 figure(s)". The count in the evidence
            # line is what a reader takes as the amount of work checked. The rule
            # is sbe_checks.distinct(), shared with the three sibling thresholds
            # that did not have it.
            seen_figures.append(fig)
            if not isinstance(fig, dict):
                problems.append("entry %d in %s is %s, not a figure object"
                                % (checked, os.path.relpath(m, root), type(fig).__name__))
                continue
            # Every field below goes through answered(), not through truthiness,
            # not through `is None`, and no longer through stated() either. A
            # manifest carrying "" in every field cleared this gate once and the
            # evidence line reported zero drift over two empty strings; the round
            # after that, a manifest carrying "TODO" and "pending" did the same
            # thing, because a placeholder is not blank. An empty value is an
            # absent value, and so is a value that names the absence of one.
            label = answered(fig.get("label")) or "figure %d" % checked
            if answered(fig.get("label")) is None:
                problems.append("%s in %s records no label (%r), so nothing in the report can say "
                                "which figure was checked"
                                % (label, os.path.relpath(m, root), fig.get("label")))
            sid = fig.get("snapshot_id")
            if answered(sid) is None:
                problems.append("%s: no snapshot_id recorded (%r); a live warehouse drifts, so pin "
                                "the read. A placeholder is not a pin"
                                % (label, sid))
            elif isinstance(sid, bool):
                # answered() deliberately keeps False as an answer (a zero row
                # count is an answer), and that let `"snapshot_id": false` read
                # as a pinned read. A boolean names no snapshot.
                problems.append("%s: snapshot_id is %r, a boolean, which pins nothing" % (label, sid))
            elif not isinstance(sid, (str, int, float)):
                # The sibling gate already refuses a container where an id
                # belongs ("rehearsal_run_id is dict, not a run id string") and
                # this branch did not, so `{"note": "TODO"}` and `["TODO"]`
                # were each "an answer" and the sentence asserted a pinned
                # read over a container holding a placeholder. An identifier
                # is a text (or number) value; a container is not one.
                problems.append("%s: snapshot_id is %s, not a snapshot id string; a container "
                                "where an identifier belongs pins nothing, whatever it holds"
                                % (label, type(sid).__name__))
            else:
                # Folded, because the reuse disclosure below compared raw ids
                # while every other comparison in this file folds, so two ids
                # differing only in case lost the disclosure.
                snapshots.append(fold(str(sid)))
            # REDUCED FIRST, then tested, and this order is the whole fix. The
            # previous order asked `answered()` about the raw text and folded it
            # afterwards, so a `second_derivation` of `"#"`, or of `"-- rerun on
            # 2026-07-26 by hand, same query"`, was an answer to `answered()`,
            # folded to the empty string, differed from every real query, and
            # bought "a second derivation whose text differs beyond case,
            # whitespace and comments". A derivation that computes nothing is not
            # a derivation, whichever side of the pair it sits on.
            q1 = answered_as(fig.get("query"), derivation_fold)
            q2 = answered_as(fig.get("second_derivation"), derivation_fold)
            if q1 is None:
                problems.append("%s: no query recorded that computes anything (%r); with comments, "
                                "case, whitespace and trailing punctuation removed there is nothing "
                                "left, so there is nothing for the second derivation to be "
                                "independent of" % (label, fig.get("query")))
            if q2 is None:
                problems.append("%s: no independent second derivation that computes anything (%r); "
                                "with comments, case, whitespace and trailing punctuation removed "
                                "there is nothing left, and a comment is not a derivation"
                                % (label, fig.get("second_derivation")))
            elif q1 is not None and q1 == q2:
                # Compared on `.strip()`ed text, so a second derivation that was
                # the first one lowercased, or reindented, was accepted as an
                # independent re-derivation. Two texts that differ only in case
                # or in whitespace are one text. And then two texts differing by
                # a trailing semicolon, or by `-- rerun 2026-07-25` on the end,
                # were also one text, and bought the strongest sentence this file
                # prints: a cosmetic edit is not a second derivation.
                problems.append("%s: the second derivation is the first one again (it differs only "
                                "in case, whitespace, comments or trailing punctuation, if at all), "
                                "so nothing independent re-derived this figure" % label)
            r = fig.get("rerun")
            if not isinstance(r, dict):
                problems.append("%s: rerun is %s, not an object recording the re-derivation"
                                % (label, type(r).__name__))
                continue
            primary, secondary = numeric(r.get("primary")), numeric(r.get("secondary"))
            if r.get("ran") is not True:
                # The boolean IS the claim, exactly as in gate_migration below.
                # The string "false" is truthy, and it satisfied this test while
                # asserting the opposite of what it says.
                problems.append("%s: second derivation not marked as re-run (recorded %r, and only "
                                "the value true is that claim)" % (label, r.get("ran")))
            elif primary is None or secondary is None:
                unread = ", ".join("%s=%r" % (k, r.get(k)) for k in ("primary", "secondary")
                                   if numeric(r.get(k)) is None)
                problems.append("%s: rerun marked ran but the value(s) needed to prove zero drift "
                                "were not recorded as numbers (%s). A figure is a number; two "
                                "placeholders compare equal and prove nothing, so this is a failure "
                                "and not a zero-drift pass" % (label, unread))
            elif primary != secondary:
                problems.append("%s: DRIFT primary=%s secondary=%s (zero drift required)" % (label, r["primary"], r["secondary"]))
            elif unit_of(r.get("primary")) != unit_of(r.get("secondary")):
                # numeric() strips "%" and "$" as formatting, so "50%" and
                # "$50" compared equal and bought "re-run to zero drift". The
                # number is the value; the unit is what it is a value of, and
                # two units are two different figures agreeing by accident.
                problems.append("%s: primary %r and secondary %r are recorded in different units, "
                                "so they are not two derivations of one figure"
                                % (label, r.get("primary"), r.get("secondary")))
    if unreadable:
        return "FAIL", ("manifest present but unparseable: %s; a receipt that cannot be read is a broken claim, not an absent one"
                        % ", ".join(unreadable))
    if problems:
        return "FAIL", "; ".join(problems[:6])
    if unbound:
        return "NO-DATA", "; ".join(unbound[:6])
    if not checked:
        return "NO-DATA", ("manifest present but records no figures (%s); an empty manifest proves nothing, so it is NO-DATA, never a pass"
                           % "; ".join(nothing))
    if nothing:
        return _partial(nothing, checked, "manifest", "figure(s)")
    # The sentence says what the four tests prove and stops there. "Independently
    # re-derived" read as a claim about the METHOD, and the only thing examined is
    # that the two texts differ by more than formatting: an alias rename is a
    # textual difference and passes, and nothing here reads which tables the two
    # queries touch. Overclaiming in the PASS line is this project's own defect
    # class pointed at itself.
    unique, dupes = distinct(seen_figures)
    # One snapshot id reused across every figure is a true sentence that proves
    # nothing about several different reads, so the reuse is stated rather than
    # left for a reader to notice. It is disclosure, not a failure: reading five
    # figures out of one pinned snapshot is the ordinary honest thing.
    reused = len(snapshots) - len(set(snapshots))
    return "PASS", ("%d figure(s) each pinned to a snapshot, with a second derivation whose text "
                    "differs beyond case, whitespace and comments, re-run to zero drift%s%s%s"
                    % (len(unique),
                       "" if not dupes else
                       "; %d duplicate manifest entry/entries were counted once" % dupes,
                       "" if not reused else
                       "; %d figure(s) share a snapshot id already used by another figure (%d "
                       "distinct snapshot(s) in all), which pins them to one read rather than to "
                       "several" % (reused, len(set(snapshots))),
                       scope))


def gate_migration(root):
    receipts, waived, _refused, scope = find(root, MIGRATION_RECEIPT, "migration")
    if not receipts:
        if waived:
            return "NO-DATA", ("every %s found under %s (%d) is waived by a %s and examined nothing; "
                               "see the WAIVED line(s) for the reason%s"
                               % (MIGRATION_RECEIPT, root, len(waived), EXEMPT, scope))
        return "NO-DATA", ("no migration in this change, or no migration-receipt.json%s" % scope)
    problems = []
    unreadable = []
    unbound = []
    nothing = []
    checked = 0      # receipts that recorded both legs
    compared = 0     # row-count comparisons actually made
    bodies = []      # parsed receipts, for the shared dedupe rule
    head = _current_head(root)
    for m in receipts:
        rel = os.path.relpath(m, root)
        d, err = load_receipt(m)
        if d is None:
            unreadable.append("%s: %s" % (rel, err))
            continue
        bp = _commit_problem(d, head, rel, root, m)
        if bp:
            problems.append(bp)
        ub = _unbound_receipt_problem(d, head, rel)
        if ub:
            unbound.append(ub)
        bodies.append(d)
        legs = {k: d.get(k) for k in ("forward", "reverse")}
        if all(v is None for v in legs.values()) and "row_counts" not in d:
            other = ", ".join(sorted(d)) or "nothing at all"
            nothing.append("%s: no forward and no reverse leg recorded; top-level keys present: %s"
                           % (rel, other))
            continue
        for direction in ("forward", "reverse"):
            leg = legs[direction]
            if not isinstance(leg, dict):
                problems.append("%s: %s leg is %s, not an object" % (rel, direction, type(leg).__name__))
                continue
            # The boolean IS the claim, so it has to be the boolean. A whitespace
            # string is truthy, and " " satisfied this test while asserting
            # nothing at all about a restored copy.
            if leg.get("ran_against_restore") is not True:
                problems.append("%s: %s is not marked as run against a restored copy (recorded %r, "
                                "and only the value true is that claim)"
                                % (direction, direction, leg.get("ran_against_restore")))
            if direction != "reverse":
                continue
            rid = leg.get("rehearsal_run_id")
            if answered(rid) is None:
                problems.append("reverse: no rehearsal_run_id recorded (%r). This gate checks the id "
                                "is present, is a string, is not blank and is not one of the tokens "
                                "this project refuses as a stated value; it cannot resolve it against "
                                "a job system" % (rid,))
            elif not isinstance(rid, str):
                problems.append("reverse: rehearsal_run_id is %s, not a run id string"
                                % type(rid).__name__)
        checked += 1
        # The row counts are the half that used to be asserted without being read.
        # A receipt with no row_counts had the comparison skipped and the PASS
        # evidence still said "with matching row counts": a sentence about work
        # nothing did. Absent counts are an absence (NO-DATA). Half a count is a
        # broken claim (FAIL), because recording `before` and not `after_reverse`
        # says a count was taken and then does not produce it.
        rc = d.get("row_counts")
        if rc is None:
            nothing.append("%s: both legs recorded but no row_counts, so nothing compared the rows "
                           "the reverse was supposed to restore" % rel)
        elif not isinstance(rc, dict):
            problems.append("%s: row_counts is %s, not an object" % (rel, type(rc).__name__))
        elif count(rc.get("before")) is None or count(rc.get("after_reverse")) is None:
            # `is None` was the whole emptiness test here, so a row_counts block
            # holding "" in both fields compared equal, incremented the counter,
            # and produced "1 row-count comparison(s) matched" about two empty
            # strings. Then a block holding "unknown" in both did the same thing,
            # because "unknown" is not blank. A row count is a NUMBER: that is
            # the test, it rejects every placeholder at once, and it cannot
            # reject an honest receipt, because a count nobody counted is not a
            # count that matched.
            unread = ", ".join("%s=%r" % (k, rc.get(k)) for k in ("before", "after_reverse")
                               if count(rc.get(k)) is None)
            problems.append("%s: row_counts does not record a count for %s; a blank, a placeholder, "
                            "a half-recorded pair, an infinity, a fraction and a negative are none "
                            "of them counts, and both before and after_reverse are required as "
                            "whole non-negative numbers of rows" % (rel, unread))
        else:
            compared += 1
            if count(rc["before"]) != count(rc["after_reverse"]):
                problems.append("reverse did not restore row count: before=%s after=%s"
                                % (rc["before"], rc["after_reverse"]))
    if unreadable:
        return "FAIL", ("migration receipt present but unparseable: %s; a receipt that cannot be read is a broken claim, not an absent one"
                        % ", ".join(unreadable))
    if problems:
        return "FAIL", "; ".join(problems[:6])
    if unbound:
        return "NO-DATA", "; ".join(unbound[:6])
    if not checked:
        return "NO-DATA", ("migration receipt present but records no migration (%s); an empty receipt "
                           "proves nothing, so it is NO-DATA, never a pass" % "; ".join(nothing))
    if nothing:
        return "NO-DATA", ("%d receipt(s) with both legs run against a restore, but %d recorded no row "
                           "counts (%s); the reverse restoring the rows is the half this gate cannot "
                           "assert, so it does not"
                           % (checked, len(nothing), "; ".join(nothing[:4])))
    # The shared dedupe rule, adopted here because its docstring already said
    # "gate_numbers learned it and its three siblings did not": one rehearsal
    # receipt copied into two directories is one rehearsal, and the count in
    # this sentence is what a reader takes as the amount of work checked.
    unique, dupes = distinct(bodies)
    return "PASS", ("%d receipt(s): forward and reverse both ran against a restore, %d row-count "
                    "comparison(s) matched, and a rehearsal id string is recorded%s%s"
                    % (len(unique), compared,
                       "" if not dupes else
                       "; %d identical receipt(s) counted once" % dupes, scope))


def gate_approval(root):
    approvals, waived, _refused, scope = find(root, APPROVAL_FILE, "approval")
    body, sig, authors, idmeta = git_trailers(root)
    trailer = re.search(r"^Approved-by:\s*(.+)$", body, re.M)
    # The id is whatever the team writes, not one whitespace-free token. `(\S+)$`
    # was a shape rule nothing declared: `Reviewed-in: PR 99999 on the payments
    # board` matched nothing, fell past this branch, and got FAIL with the
    # sentence "approval is a typed name with no signature or review id" printed
    # over a commit that records one. The law says there is no shape check on the
    # id beyond refusing the tokens that name the absence of one, so this reads
    # the line and lets `answered()` be the only test.
    review_id = re.search(r"^Reviewed-in:\s*(.+)$", body, re.M)
    # The APPROVAL file declares this change touches money/partner paths.
    # A commit whose ONLY claim was a Reviewed-in id used to fall into this
    # return too, and the sentence told a team on the keyless path that their
    # commit carries no approval claim at all, with the id they recorded
    # unmentioned. A recorded id IS a claim, so it flows to its own branch.
    if not approvals and not trailer and not review_id:
        if waived:
            return "NO-DATA", ("every %s found under %s (%d) is waived by a %s and examined nothing, "
                               "and the commit carries no Approved-by or Reviewed-in trailer either; "
                               "see the WAIVED line(s) for the reason%s"
                               % (APPROVAL_FILE, root, len(waived), EXEMPT, scope))
        return "NO-DATA", ("no APPROVAL file and no Approved-by trailer; if this change touches no "
                           "money or partner path that is correct%s" % scope)
    # A reordering bidi control makes a line RENDER in a different order than
    # it reads: `Approved-by: <U+202E>rohtuA anaD` renders as the author's own
    # name in git log and every bidi-honoring view, while the stripped
    # comparison text read the logical order and certified a second person.
    # Stripping the control hides the reordering rather than undoing it, so a
    # value carrying one cannot be certified as ANY identity; it is refused
    # first, by code point, on either side of the comparison.
    if trailer:
        controls = bidi_reordering_controls(trailer.group(1))
        for who in authors:
            controls += bidi_reordering_controls(who)
        if controls:
            return "FAIL", ("this gate cannot certify any identity here: the value carries "
                            "directional formatting character(s) %s, which make a line render "
                            "in a different order than it reads, so an approver can render as "
                            "the author while comparing as a second person. Stripping the "
                            "character would hide the reordering, not undo it, so it is refused "
                            "by name; record the identity without directional controls"
                            % ", ".join("U+%04X" % ord(c) for c in sorted(set(controls))))
    # A word that mixes script families is an identity written in a disguise
    # shape (one Cyrillic letter inside an ASCII name renders as the author
    # while comparing as a second person), and no honest name takes that
    # shape, so it is refused by name on either side of the comparison. This
    # refusal is AFFIRMATIVE: it names a forgery shape. A word that is merely
    # unreadable (wholly one script no fold can reduce) is NOT refused here;
    # earlier rounds refused those wholesale and rejected ordinary Danish,
    # Icelandic and French names on the money gate. Whether an unreadable
    # word can be certified different from THIS author is decided pairwise
    # below, where an unprovable difference is NO-DATA, never PASS.
    if trailer:
        unreadable = unreadable_identity_words(trailer.group(1))
        for who in authors:
            unreadable += [(w, "in the author/committer identity %r, %s" % (who, why))
                           for w, why in unreadable_identity_words(who)]
        if unreadable:
            w, why = unreadable[0]
            return "FAIL", ("this gate cannot certify the approver and the author are different "
                            "people, because the identity word %r %s. A word this comparison "
                            "cannot read as one alphabet is exactly how a forged identity renders "
                            "as the author while comparing as a second person, so it is refused "
                            "rather than passed (%d unreadable word(s) in all). Record the "
                            "identity in one script, or use a Reviewed-in id"
                            % (w, why, len(unreadable)))
    # The trailer's VALUE is a field like any other, so it goes through the same
    # answered() every receipt field does. It did not: `Approved-by: TODO` and
    # `Approved-by: ???` cleared the strongest sentence this project prints,
    # because answered() was applied to the weaker Reviewed-in path four lines
    # down and never to this one.
    if trailer and answered(trailer.group(1)) is None:
        return "FAIL", ("the Approved-by trailer records %r, which names no identity; a "
                        "placeholder where an approver belongs is a broken claim, not an "
                        "approval" % trailer.group(1).strip())
    # Self-approval is not approval, and the gate did not look. One person, one
    # key: they authored the commit, signed the commit and wrote their own name
    # into the Approved-by trailer, and this gate printed "signed commit carries
    # Approved-by: Dana Author" over it while the line above the verdict showed
    # the same identity three times. A signature proves a key holder signed. It
    # cannot prove a SECOND party looked, and a second party is the entire content
    # of the word approval.
    cosign = False
    if trailer:
        appr_emails, appr_names = _identity_parts(trailer.group(1))

        def _wrote(ids):
            emails, names = set(), []
            for who in ids:
                e, n = _identity_parts(who)
                emails |= e
                names.extend(n)
            return emails, names

        auth_emails, auth_names = _wrote(idmeta["author"])
        comm_emails, comm_names = _wrote(idmeta["committer"])
        # The co-sign shape, and it is the only spelling git has for "the
        # reviewer signed": under SSH signing an approver signs by amending,
        # which makes them the COMMITTER, and the old guard lumped committer
        # in with author, so the strongest honest approval shape git can
        # express was refused as self-approval while the FAIL's own remedy
        # ("have the approver sign") pointed straight back into it. The
        # carve-out is bound to a key, not to typed text: it opens only when
        # the verifier's matched principal (%GS, the identity whose key
        # PRODUCED the signature) is the approver's own email, so a spoofed
        # committer name signed with the author's key stays a FAIL.
        signer_emails = {_canonical_email(m.group(0), skeleton)
                         for m in _EMAIL.finditer(idmeta.get("signer", ""))}
        cosign = (sig == "G" and bool(signer_emails & appr_emails))

        def _joined(names):
            return {"".join(t) for t in names}

        def _same(emails_w, names_w):
            # A name is the same person when the word sets overlap, and also
            # when the SPACELESS renderings match: an invisible character
            # welded two words into one, and the welded word was structurally
            # different from every author word, which a certifying comparison
            # then consumed as proof of difference.
            se = sorted(appr_emails & emails_w)
            sn = next((" ".join(a) for a in appr_names for b in names_w
                       if _names_overlap(frozenset(a), frozenset(b))), "")
            sj = next(iter(_joined(appr_names) & _joined(names_w)), "")
            return se, (sn or sj)

        same_email_a, same_name_a = _same(auth_emails, auth_names)
        same_email_c, same_name_c = _same(comm_emails, comm_names)
        if same_email_a or same_name_a:
            alias_note = next((n for n in (_same_mailbox_note(c, trailer.group(1), idmeta["author"])
                                            for c in same_email_a) if n), "")
            return "FAIL", ("the Approved-by identity is the identity that wrote the commit (%s); "
                            "author and committer are %s.%s Self-approval is not approval: a "
                            "signature proves a key holder signed, and it cannot prove a second "
                            "party reviewed. A trailing period, a reordered name, an initial, a "
                            "plus-address, a gmail dot or a role suffix does not make a second "
                            "person. A second "
                            "person must review: have them amend and sign with their OWN key and "
                            "record their email in the trailer (the signature's principal is then "
                            "the approver's email, and this gate reads that committer as the "
                            "approver who signed), or record a Reviewed-in id pointing at a review "
                            "somebody else performed"
                            % (", ".join(same_email_a) or same_name_a,
                               ", ".join(authors) or "unrecorded", alias_note))
        if (same_email_c or same_name_c) and not cosign:
            alias_note = next((n for n in (_same_mailbox_note(c, trailer.group(1), idmeta["committer"])
                                            for c in same_email_c) if n), "")
            return "FAIL", ("the Approved-by identity is the identity recorded as this commit's "
                            "committer (%s), and the signature's principal (%s) is not the "
                            "approver's email, so this host cannot tell that the approver signed "
                            "rather than merely being named.%s Have the approver amend and sign with "
                            "their OWN key and record their email in the trailer (the signature's "
                            "principal is then the approver's email, and this gate reads that "
                            "committer as the approver who signed), or record a Reviewed-in id "
                            "pointing at a review somebody else performed"
                            % (", ".join(same_email_c) or same_name_c,
                               idmeta.get("signer", "").strip() or "unrecorded", alias_note))
    # An approval was claimed: now it must be bound to a forgery-resistant identity.
    # Only a signature this host verified AGAINST A PRINCIPAL IT TRUSTS counts,
    # and that is %G? = G alone. U means the signature is cryptographically valid
    # and the key matched nothing the host trusts; under SSH signing, the default
    # for teams adopting commit signing today, U is exactly what a key the agent
    # generated for itself produces ("No principal matched"), so accepting U made
    # L9's sentence "an agent without the private key cannot produce it" false in
    # four commands. E means the signature could not be checked at all, the
    # normal result on a runner with no imported keys. Neither is an approval;
    # both are NO-DATA naming what the host could not do.
    # The PASS certifies a NEGATIVE: the approver is not the author. A
    # negative may only be asserted when the difference is PROVEN, and proven
    # means the difference cannot be explained by one-for-one look-alike
    # substitution: the identities differ in structure (word count, word
    # length), or at a position where both characters' renderings are decided
    # (plain ASCII against plain ASCII, or a wide or right-to-left letterform
    # against anything narrow). A Lisu, Cherokee or Coptic spelling of the
    # author's name is letter-for-letter substitution-compatible with it, so
    # it earns NO-DATA naming the ambiguity, never the certificate; an honest
    # Icelandic or Danish name (Þóra, Bæk, Kjær) differs at readable
    # positions or in structure and is certified cleanly.
    if trailer and sig == "G":
        appr_emails_r, appr_names_r = _rendered_identity_parts(trailer.group(1))
        # In the co-sign shape the approver IS the committer, legitimately, so
        # the difference that must be proven is against the AUTHOR alone.
        cosigned = cosign and (same_email_c or same_name_c)
        targets = idmeta["author"] if cosigned else authors
        wrote_emails_r, wrote_names_r = set(), []
        for who in targets:
            e, n = _rendered_identity_parts(who)
            wrote_emails_r |= e
            wrote_names_r.extend(n)
        cosign_note = ("; the signature's principal is the approver's own email (%s), so the "
                       "approver's key signed this commit and the identity compared against is "
                       "the author alone" % idmeta.get("signer", "").strip()) if cosigned else ""
        # The founding rule, applied to the comparison itself: absence is
        # never proof. A predicate whose "no collision" can be produced by an
        # EMPTY input may not be consumed as evidence for a certificate, so
        # before any "no collision found" counts, the gate establishes that
        # the comparison examined something on both sides.
        if not appr_emails_r and not appr_names_r:
            return "NO-DATA", ("signature verified against a trusted key, but the Approved-by "
                               "value %r could not be read as a name or an email address, so the "
                               "identity comparison examined nothing, and a comparison that "
                               "examined nothing proves nothing; record the approver as a name "
                               "or an email address" % trailer.group(1).strip())
        if not wrote_emails_r and not wrote_names_r:
            return "NO-DATA", ("signature verified against a trusted key, but the commit records "
                               "no readable author or committer identity to compare the approver "
                               "against, so the identity comparison examined nothing, and a "
                               "comparison that examined nothing proves nothing")
        # The same rule one layer deeper, and it is why this family closed
        # here rather than in a fourth strip set: the certificate may not rest
        # on the REDUCER'S COVERAGE either. _name_words classes every character
        # as identity-carrying (letter, mark, digit) or as decoration and
        # separation (punctuation, symbol, separator). A code point it can
        # class as neither is one this host can say nothing about, so whether
        # these two identities read the same is undecidable, and undecidable
        # is NO-DATA naming the character. Absence of understanding is not
        # proof of difference.
        unreadable = _unreadable_identity_chars(
            [w for t in appr_names_r for w in t] + sorted(appr_emails_r)
            + [w for t in wrote_names_r for w in t] + sorted(wrote_emails_r))
        if unreadable:
            return "NO-DATA", ("signature verified against a trusted key, but the identities "
                               "carry %d code point(s) this host cannot classify as a letter, a "
                               "mark, a digit or a separator (%s), so it cannot say what they "
                               "render as, and a difference it cannot read is not a difference "
                               "it can prove; record the approver and the author in characters "
                               "this host can read, or use a Reviewed-in id (reported as a "
                               "pointer, not a certificate)"
                               % (len(unreadable),
                                  ", ".join("U+%04X" % ord(c) for c in unreadable)))
        # A proven email difference is itself a proof of difference, and it
        # short-circuits: the remedy sentence below used to advertise
        # "record the approver with an email address" while a recorded,
        # provably different email fell through to the weaker name test and
        # changed nothing. A remedy a check prints must be one the code
        # accepts for THIS value.
        if appr_emails_r and wrote_emails_r:
            email_collide = next(("%s could render as the author's %s" % (a, b)
                                  for a in sorted(appr_emails_r) for b in sorted(wrote_emails_r)
                                  if could_render_same(a, b)), "")
            if email_collide:
                return "NO-DATA", ("signature verified against a trusted key, but this gate "
                                   "cannot certify the approver and the author are different "
                                   "people: the approver's recorded email %s, so a look-alike "
                                   "substitution could map one mailbox onto the other; record an "
                                   "email address that differs at positions this host reads, or "
                                   "use a Reviewed-in id (reported as a pointer, not a "
                                   "certificate)" % email_collide)
            return "PASS", ("signed commit carries Approved-by: %s, this host verified the "
                            "signature against a trusted key, and the approver's recorded email "
                            "address (%s) is proven different from the %s email(s) on record "
                            "(%s): they differ at positions this host reads, beyond any "
                            "look-alike substitution%s%s"
                            % (trailer.group(1).strip(), ", ".join(sorted(appr_emails_r)),
                               "author's" if cosigned else "author's and committer's",
                               ", ".join(sorted(wrote_emails_r)), cosign_note, scope))
        collide = next(("%s could render as the author's %s"
                        % (" ".join(a), " ".join(b))
                        for a in appr_names_r for b in wrote_names_r
                        if name_sets_could_collide(a, b)
                        or could_render_same("".join(a), "".join(b))), "")
        if collide:
            # The reason clause is written for BOTH collision classes,
            # because the old one ("every letter that differs is one no fold
            # this host can read") was false for the soft class: the host's
            # own folds read those letters every time it runs, and what
            # actually stops the certificate is policy, not inability.
            if not appr_emails_r:
                remedy = ("record the approver with an email address that differs from the "
                          "author's (a proven email difference is accepted as proof of "
                          "difference), or use a Reviewed-in id (reported as a pointer, not a "
                          "certificate)")
            else:
                remedy = ("the approver's email is recorded but the commit carries no author "
                          "email to compare it against; record author identities with email "
                          "addresses, or use a Reviewed-in id (reported as a pointer, not a "
                          "certificate)")
            return "NO-DATA", ("signature verified against a trusted key, but this gate cannot "
                               "certify the approver and the author are different people: %s. "
                               "The letters that differ are ones this host will not trust as "
                               "proof of difference: a fold may read some of them, but a "
                               "certificate may not rest on a fold's coverage, and a letter no "
                               "fold reads proves nothing in either direction. The negative "
                               "'the approver is not the author' is asserted only when "
                               "difference is proven; %s" % (collide, remedy))
        if appr_names_r and wrote_names_r:
            return "PASS", ("signed commit carries Approved-by: %s, this host verified the "
                            "signature against a trusted key, and that identity is proven "
                            "different from the commit's author or committer (%s): after "
                            "normalization and confusable folding they differ beyond any "
                            "look-alike substitution this host can read%s%s"
                            % (trailer.group(1).strip(), ", ".join(authors) or "unrecorded",
                               cosign_note, scope))
        # One side has only an email and the other only a name: no comparison
        # examined anything, and absence of a collision over nothing is not
        # proof of anything.
        return "NO-DATA", ("signature verified against a trusted key, but the approver and the "
                           "commit's identities share no comparable part (one records only an "
                           "email, the other only a name), so the identity comparison examined "
                           "nothing, and a comparison that examined nothing proves nothing; "
                           "record both sides with an email address or with a name")
    if trailer and sig == "U":
        return "NO-DATA", ("signature is cryptographically valid but the key matched no principal "
                           "this host trusts (git %G? = U; under SSH signing that is an unknown "
                           "key, which anyone, including an agent, can generate). Add the "
                           "signer's key to the allowed signers or keyring trust and this becomes "
                           "a verdict; an unknown key holder is not an approval")
    if review_id and answered(review_id.group(1)) is not None:
        # NO-DATA, not PASS, and the argument is internal consistency rather than
        # taste. Four lines below, a signature THIS HOST COULD NOT VERIFY returns
        # NO-DATA, with the reasoning "an unverifiable signature is not an
        # approval". A Reviewed-in id is in the identical epistemic state: this
        # gate cannot resolve it either, and the agent writes the commit message.
        # Two identical states must not get two different verdicts, and while
        # this one returned PASS the strongest sentence this gate could print
        # about a money-movement change was purchasable with a single hyphen.
        # NO-DATA neither blocks nor passes, so no honest team is impeded.
        return "NO-DATA", ("commit records Reviewed-in: %s. This gate read a trailer out of a commit "
                           "message and does not resolve the id against any review platform, so it "
                           "points a human at a review rather than proving one happened. That is a "
                           "pointer, not a control: resolve the id in CI (a job that queries your "
                           "review platform) or sign the commit, and this becomes a verdict"
                           % review_id.group(1))
    if review_id:
        return "FAIL", ("the Reviewed-in trailer records %r, which names no review; an id nobody can "
                        "follow is not a pointer at anything" % review_id.group(1))
    if trailer and sig == "E":
        # B1, the Windows engineer's Bitbucket signed-trailer path: on that box
        # git was 2.33 and ssh-keygen absent from the shell PATH, so E was the
        # only answer this branch could ever reach, and the generic sentence
        # below sent the reader hunting a key import that would not have
        # helped. When the signature's own armor says SSH, the two real
        # blockers are named first, mirroring the `sbe doctor` git-version and
        # ssh-keygen sentences (src/brothersbe/cli.py). The verdict class does
        # not move: still NO-DATA, never a pass, never a block.
        if _head_signature_is_ssh(root):
            ver = _git_version()
            if ver is not None and ver < GIT_SSH_VERIFY_FLOOR:
                floor = "%d.%d" % GIT_SSH_VERIFY_FLOOR
                return "NO-DATA", ("signature present but this host could not verify it "
                                   "(git %%G? = E): git %d.%d is below the %s floor, and a git "
                                   "that old cannot verify an SSH signature at all, so the "
                                   "approval gate can never return PASS on this machine. "
                                   "Upgrade git to %s or newer. An unverifiable signature is "
                                   "not an approval" % (ver[0], ver[1], floor, floor))
            if (not _git_config(root, "gpg.ssh.program")
                    and shutil.which("ssh-keygen") is None):
                return "NO-DATA", ("signature present but this host could not verify it "
                                   "(git %G? = E): no ssh-keygen on this PATH and "
                                   "gpg.ssh.program is unset, so git cannot verify an SSH "
                                   "signature here. Install an OpenSSH client (on Windows: "
                                   "the optional OpenSSH feature, or use the Git Bash shell "
                                   "where Git for Windows ships one), or set git config "
                                   "gpg.ssh.program <path>. An unverifiable signature is not "
                                   "an approval")
        return "NO-DATA", ("signature present but this host could not verify it (git %G? = E): import the signer's public key "
                           "into the verifying keyring, or use a Reviewed-in: review id, which needs no keyring. "
                           "An unverifiable signature is not an approval")
    # The APPROVAL file's own words, read and quoted, because this gate declared
    # reads=(APPROVAL,) and never opened it: the claim being refused deserves to
    # be named, and a registry declaration that nothing exercises is fiction.
    claim = ""
    for p in approvals[:1]:
        if not evidence_problem(p):
            try:
                first = open(p, encoding="utf-8", errors="replace").readline().strip()
            except OSError:
                first = ""
            if first:
                # Named, not "the APPROVAL file": with several under one root
                # this sentence quoted one of them and identified none, so a
                # reader could not tell which claim was being refused, nor that
                # the other files existed at all.
                claim = ("%s (of %d APPROVAL file(s) read) declares %r, but "
                         % (os.path.relpath(p, root), len(approvals), first[:60]))
    return "FAIL", ("%sapproval is a typed name with no signature or review id; a name in a text "
                    "field is not a control (add a signed Approved-by trailer or a Reviewed-in "
                    "review id)" % claim)


#: The `generator` string `src/brothersbe/evidence.py` stamps into every
#: receipt `sbe evidence run` writes (that module's own `GENERATOR` constant).
#: Named here, not imported: this file already has a clean one-directional
#: dependency (`src/brothersbe/` mounts `tools/` onto its own `sys.path`, see
#: `src/brothersbe/_toolspath.py`, never the reverse), and this string is a
#: stable, documented value cheaper to mirror once than to import backwards.
EVIDENCE_RUN_GENERATOR = "sbe evidence run"


def _run_receipt_as_checks(d):
    """The one recorded check `sbe evidence run`'s own receipt describes, read
    as the same three facts this gate has always demanded of a hand-written
    `checks` entry (what ran, its exit code, its duration) -- or None when `d`
    is not that receipt's shape at all, in which case the caller falls back to
    today's "no 'checks' list" NO-DATA exactly as before.

    THE DEFECT THIS CLOSES: `sbe evidence run --out ran-receipt.json --
    <command>` writes `argv`, `exitCode` and `durationSeconds` at the TOP
    LEVEL, never a `checks` list, because that wrapper records ONE run, not a
    batch. Naming its output `ran-receipt.json` reads as satisfying this
    gate and does not: `_items(d, "checks")` finds no list, and this gate
    reported NO-DATA on evidence minted seconds earlier by this same product,
    over a receipt whose own `argv`/`exitCode`/`durationSeconds` fields say
    exactly what ran, how it exited and how long it took.

    Recognising the shape does not loosen what this gate checks: the
    exit-code and duration rules in `gate_ran` run over these three fields
    exactly as they already run over a hand-written entry's `name`,
    `exit_code` and `duration_ms`; a synthesized check with a missing exit
    code, a nonzero one, or a zero/negative duration FAILs here exactly as a
    hand-written one would, because the same per-check tests read it next.

    Recognition requires BOTH `generator` naming the wrapper by its own
    stamped string AND `argv` recorded as a list. Either alone is a
    coincidence this gate should not read as identity: a hand-typed receipt
    that happens to also carry an `argv` key is not this wrapper's receipt,
    and a `generator` string copied without the shape that goes with it
    proves nothing was run. A receipt that already carries a `checks` list is
    never reached here: that shape wins unchanged, so a receipt merging both
    (unlikely, but not refused) is read exactly as it always has been.
    """
    if "checks" in d:
        return None
    if d.get("generator") != EVIDENCE_RUN_GENERATOR:
        return None
    argv = d.get("argv")
    if not isinstance(argv, list):
        return None
    # The name: the REGISTERED check id when this run was minted through
    # `--check <id>`, else the recorded command line itself, so "what ran" is
    # always the most specific true thing this receipt records -- never
    # invented, and never silently blank the way a missing `name` field on a
    # hand-written entry already FAILs below.
    check_id = answered(d.get("checkId"))
    name = str(check_id) if check_id is not None else (" ".join(str(a) for a in argv) or None)
    secs = d.get("durationSeconds")
    if isinstance(secs, (int, float)) and not isinstance(secs, bool):
        duration_ms = secs * 1000.0
    else:
        # Not a number (missing, a bool, a string): passed through AS IS
        # rather than coerced to None, so the per-check duration test below
        # reports the actual value a forged or broken receipt claimed,
        # exactly as it already does for a hand-written `duration_ms`.
        duration_ms = secs
    return [{"name": name, "exit_code": d.get("exitCode"), "duration_ms": duration_ms}]


#: Set once in `main()` from `--strict-producer`. A module global rather than a
#: parameter because every gate function's signature is `(root)` and
#: `run_guarded` calls them that way, so a parameter here would change every
#: gate to carry a flag only one of them reads.
#:
#: DEFAULT FALSE, and that is D2 ratified rather than caution: a refusal in
#: this product ships behind an estate switch and defaults to reporting. Folding
#: this into `--strict` would re-verdict every estate's existing green run,
#: which is a product decision nobody made. `--strict-waivers` already set the
#: pattern of one opt-in flag per refusal class.
STRICT_PRODUCER = False


def _producer_finding(path, root, receipt):
    """The sentence for a receipt that records no build-system producer, or
    None when it records one.

    TWO SENTENCES, not one, because the two states are different findings and
    a reader acts differently on each. A receipt whose `ciRunId` is present and
    null says "a laptop ran this", which is a fact about the run. A receipt
    with no such field says nothing at all, which is a fact about the receipt:
    it predates the field, or was typed by somebody who never knew it existed.
    Collapsing them would assert a laptop where the evidence is silent.
    """
    state = producer_class(receipt)
    if state == PRODUCER_BUILD_SYSTEM:
        return None
    rel = os.path.relpath(path, root)
    if state == PRODUCER_OFF_BUILD_SYSTEM:
        return ("%s was produced off the build system: it records ciRunId as null, so a "
                "laptop or an unidentified runner ran the command" % rel)
    # Covers both the absent key and a receipt that did not read as an object
    # at all. The second cannot arrive here today (`load_receipt` refuses it
    # first and this gate reports it as unreadable), and naming it anyway costs
    # one branch and keeps this function honest if that ever changes.
    return ("%s: producer not established, no ciRunId field, so nothing in the receipt says "
            "whether a build system or a laptop ran the command" % rel)


def _check_entry_problems(chk, name):
    """Why this entry is not the record of a check that ran, as the sentences
    the report prints, or [] when it is sound.

    ONE READING, TWO CONSUMERS, and it became one function because the second
    consumer got it wrong. `gate_ran` turns these into its FAIL. A3's
    `_recorded_check_names` uses their ABSENCE to decide whether a cited name
    counts as recorded at all, and its first version read only `name`: a
    behaviour row citing `reconcile` was satisfied by an entry whose exit code
    was the string "TODO" and whose duration was Infinity, so the proof gate
    printed PASS over a receipt `gate_ran` would have failed on the same run.
    The honesty meta-test caught it in 24 scenarios. A name attached to a
    broken entry is not evidence that anything ran.

    An exit code is an integer and a duration is a number. `False` satisfied
    `!= 0` and `True` satisfied a truthiness test, so
    {"exit_code": false, "duration_ms": true} once passed as "a zero exit and a
    nonzero duration". Booleans are neither.
    """
    out = []
    code = chk.get("exit_code")
    if code is None:
        out.append("%s: no exit code recorded (was it actually run?)" % name)
    elif isinstance(code, bool) or not isinstance(code, int):
        out.append("%s: exit_code is %r, which is not an exit status integer" % (name, code))
    elif code != 0:
        out.append("%s: check exited nonzero (%s)" % (name, code))
    ms = chk.get("duration_ms")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        out.append("%s: duration_ms is %r, which is not a measured duration in "
                   "milliseconds" % (name, ms))
    elif numeric(ms) is None:
        # The shared rule, not a hand test: `NaN <= 0` is False and json.load
        # accepts bare NaN and Infinity, so a receipt carrying either cleared
        # this gate while its sentence asserted "a nonzero duration". An
        # infinity is not a measurement and neither is a not-a-number;
        # sbe_checks.numeric refuses both.
        out.append("%s: duration_ms is %r, and an infinity or a not-a-number is not a "
                   "measured duration" % (name, ms))
    elif ms <= 0:
        out.append("%s: zero or negative duration (a check that took no time did not run)" % name)
    return out


def gate_ran(root):
    receipts, waived, _refused, scope = find(root, RAN_RECEIPT, "ran")
    if not receipts:
        if waived:
            return "NO-DATA", ("every %s found under %s (%d) is waived by a %s and examined nothing; "
                               "see the WAIVED line(s) for the reason%s"
                               % (RAN_RECEIPT, root, len(waived), EXEMPT, scope))
        return "NO-DATA", ("no ran-receipt.json; a SQL or pipeline change is not done until its "
                           "check executed and left a receipt%s" % scope)
    problems = []
    unreadable = []
    unbound = []
    nothing = []
    checked = 0
    entries = []     # every check object, for the shared dedupe rule
    no_producer = []   # A2: readable receipts recording no build-system producer
    receipts_read = 0  # the denominator that accounting is honest about
    head = _current_head(root)
    for m in receipts:
        d, err = load_receipt(m)
        if d is None:
            unreadable.append("%s: %s" % (os.path.relpath(m, root), err))
            continue
        receipts_read += 1
        # A2, receipt provenance. The adopting team's report: this gate checks
        # that an exit code is a whole number and a duration positive, so a
        # receipt typed on a laptop passes exactly as green as one a build
        # system produced, and their operating rule is to trust evidence the
        # build system made.
        #
        # THE CEILING, written here rather than discovered later. `ciRunId`
        # comes from `SBE_CI_RUN_ID`, an environment variable any local shell
        # can export, and `src/brothersbe/evidence.py` already states that the
        # receipt seal "proves nothing about who produced it". So a
        # `--strict-producer` green means THE RECEIPT CLAIMS a build system,
        # never that a build system produced it: a forged id plus url passes
        # strict exactly as easily as a real one. This flag raises the cost of
        # passing off local evidence from zero to one exported variable. That
        # is worth having and it is not an attestation.
        finding = _producer_finding(m, root, d)
        if finding is not None:
            no_producer.append(finding)
            if STRICT_PRODUCER:
                problems.append(finding)
        bp = _commit_problem(d, head, os.path.relpath(m, root), root, m)
        if bp:
            problems.append(bp)
        ub = _unbound_receipt_problem(d, head, os.path.relpath(m, root))
        if ub:
            unbound.append(ub)
        chks, note = _items(d, "checks")
        if not chks:
            chks = _run_receipt_as_checks(d)
            if chks is None:
                nothing.append("%s: %s" % (os.path.relpath(m, root), note))
                continue
        entries.extend(chks)
        for chk in chks:
            checked += 1
            if not isinstance(chk, dict):
                problems.append("entry %d in %s is %s, not a check object"
                                % (checked, os.path.relpath(m, root), type(chk).__name__))
                continue
            name = answered(chk.get("name")) or "check %d" % checked
            if answered(chk.get("name")) is None:
                problems.append("%s in %s records no name (%r), so nothing in the report can say "
                                "what ran" % (name, os.path.relpath(m, root), chk.get("name")))
            problems.extend(_check_entry_problems(chk, name))
    if unreadable:
        return "FAIL", ("ran-receipt present but unparseable: %s; a receipt that cannot be read is a broken claim, not an absent one"
                        % ", ".join(unreadable))
    if problems:
        return "FAIL", "; ".join(problems[:6])
    if unbound:
        return "NO-DATA", "; ".join(unbound[:6])
    if not checked:
        return "NO-DATA", ("ran-receipt present but records no checks (%s); a receipt with nothing in it is exactly what a run that never happened produces, so it is NO-DATA, never a pass"
                           % "; ".join(nothing))
    if nothing:
        return _partial(nothing, checked, "ran-receipt", "check(s)")
    # One check object written five times is one check: sbe_checks.distinct,
    # whose docstring already named this gate as a sibling that had not learned
    # the rule its neighbour had.
    unique, dupes = distinct(entries)
    # A2's default behaviour: the accounting is REPORTED on a green run rather
    # than blocking it. A default that stayed silent would not be "reports
    # rather than blocks", it would be a control nobody can see, which is the
    # shape this product keeps finding in other people's tools.
    producer_note = ("" if not no_producer else
                     "; %d of %d receipt(s) record no build-system producer (run "
                     "`--strict --strict-producer` to make that block)"
                     % (len(no_producer), receipts_read))
    return "PASS", ("%d recorded check(s), each with a zero exit and a nonzero duration%s%s%s"
                    % (len(unique),
                       "" if not dupes else
                       "; %d identical entry/entries counted once" % dupes,
                       producer_note, scope))


#: A3's artifact. Taken from sbe_design's own table rather than typed again, so
#: renaming the artifact there cannot leave this gate hunting a file that no
#: longer exists while reporting NO-DATA as though the dossier were silent.
BEHAVIOUR_TABLE = ARTIFACT_FILES["08"]

#: A check cited inside a Proof cell is a BACKTICKED token. Deliberately not a
#: word match over the cell: a Proof reading "run the reconcile drill" names a
#: check to a human and names nothing to a matcher, and a gate that guessed
#: would either invent obligations from prose or, worse, silently satisfy them
#: from prose. Backticks make the citation a DECLARED structure the author
#: chose, which is the only thing this gate is entitled to act on.
_PROOF_CITATION = re.compile(r"`([^`\n]+)`")


class _TableRefused(Exception):
    """The behaviour table could not be read, carrying the verdict that must be
    returned instead.

    RAISED RATHER THAN RETURNED, and the reason is a lint rather than taste.
    `_behaviour_table_rows` first returned `(rows, verdict_or_None)`, and the
    honesty meta-test's source lint reads any two-value return under `tools/`
    as a possible (verdict, evidence) pair, then refuses to believe the first
    element is never "PASS": it named this function alongside two test helpers
    this session added. A verdict that travels as an exception cannot be
    mistaken for one, and `gate_proof`, which IS in the registry, stays the one
    function that returns a verdict here.
    """
    def __init__(self, verdict, evidence):
        Exception.__init__(self, evidence)
        self.verdict = verdict
        self.evidence = evidence


def _behaviour_table_rows(root):
    """Every behaviour row under `root`, or _TableRefused carrying the verdict
    that must be returned instead because the table could not be read.

    THE THREE STATES ARE KEPT APART, matching `check_behaviour` in
    sbe_design.py exactly rather than inventing a fourth reading: an ABSENT
    table is NO-DATA (this dossier states no behaviour, so there is nothing to
    owe), a table that EXISTS and cannot be read is FAIL (a broken claim, not
    an absence), and a row whose cell count does not match the header is FAIL
    by the same argument. `_read_text` above is not used here on purpose: it
    returns "" for both absent and unreadable, which is precisely the
    distinction this gate has to make.
    """
    tables, waived, _refused, scope = find(root, BEHAVIOUR_TABLE, "proof")
    if not tables:
        if waived:
            raise _TableRefused("NO-DATA", "every %s found under %s (%d) is waived by a %s and "
                                "examined nothing; see the WAIVED line(s) for the reason%s"
                                % (BEHAVIOUR_TABLE, root, len(waived), EXEMPT, scope))
        raise _TableRefused("NO-DATA", "no %s; nothing here states what the software must do, so "
                            "there is no promised check to look for%s" % (BEHAVIOUR_TABLE, scope))
    rows, unreadable, malformed = [], [], []
    for path in tables:
        rel = os.path.relpath(path, root)
        # THE ACCESS AXIS, asked of the shared rule rather than re-derived with
        # a try/except around open(). This function's first version did exactly
        # that and HUNG FOREVER on a FIFO named 08-behaviour.md, printing no
        # verdict in either mode, which the honesty meta-test caught and which
        # evidence_problem's own docstring already describes as a closed
        # defect: it refuses a FIFO, socket or device BY ITS STAT, before any
        # open(). A second reading of "can this be read" is how a closed defect
        # comes back.
        problem = evidence_problem(path)
        if problem:
            unreadable.append("%s: %s" % (rel, problem))
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (IOError, OSError) as exc:
            # Still guarded: evidence_problem stats, and a file can change
            # between the stat and the open. A race is a broken claim too.
            unreadable.append("%s: %s" % (rel, exc.strerror or exc))
            continue
        found, bad = _behaviour_rows(text)
        rows.extend(found)
        malformed.extend(bad)
    if unreadable:
        raise _TableRefused("FAIL", "%s present but unreadable: %s; an artifact that exists and "
                            "cannot be read is a broken claim, not an absent one"
                            % (BEHAVIOUR_TABLE, "; ".join(unreadable)))
    if malformed:
        raise _TableRefused("FAIL", "%d row(s) under the ID | Starting point | Trigger | Required "
                            "outcome | Proof table do not carry 5 cells and could not be read: "
                            "%s; a row this gate cannot parse is a broken claim, not an absent "
                            "one" % (len(malformed), "; ".join(m[:60] for m in malformed[:4])))
    if not rows:
        raise _TableRefused("NO-DATA", "%s carries no row under an ID | Starting point | Trigger "
                            "| Required outcome | Proof table, so it promises no check%s"
                            % (BEHAVIOUR_TABLE, scope))
    return rows


def _recorded_check_names(root):
    """Every check name any readable ran-receipt under `root` records.

    THE CEILING, and it is the reason this returns a flat union rather than a
    per-directory map: one directory's receipt vouches for another directory's
    behaviour table. That is deliberate (a repository commonly mints evidence
    in one place and states behaviour in another) and it is also a hole, since
    nothing here checks that the receipt naming `reconcile` has anything to do
    with the dossier promising it.
    """
    receipts, _waived, _refused, _scope = find(root, RAN_RECEIPT, "proof")
    names = set()
    for m in receipts:
        d, _err = load_receipt(m)
        if d is None:
            continue
        chks, _note = _items(d, "checks")
        if not chks:
            chks = _run_receipt_as_checks(d)
            if chks is None:
                continue
        for chk in chks:
            if not isinstance(chk, dict):
                continue
            nm = answered(chk.get("name"))
            if nm is None:
                continue
            # SOUND ENTRIES ONLY. A name sitting on an entry with no exit code,
            # a nonzero one, or a duration that is not a measurement is not a
            # record of anything having run, and letting it satisfy a cited row
            # made this gate print PASS over a receipt `gate_ran` fails on the
            # same run. Same predicate as that gate, deliberately.
            if _check_entry_problems(chk, str(nm)):
                continue
            names.add(str(nm))
    return names


def gate_proof(root):
    """Every check the behaviour table PROMISED must appear in the evidence.

    WHAT THE ADOPTING TEAM REPORTED: evidence containing one check passes
    exactly as green as evidence containing all of them. The forward direction
    already exists (a verification plan citing a behaviour id that no longer
    exists FAILs, in sbe_design.py). This is the reverse: a Proof column
    naming a check that no receipt records.

    PROSE PROOFS ARE REPORTED, NEVER FAILED, and that is a correctness
    requirement rather than a softening. The shipped template's own Proof
    cells are prose ("Integration test: submit checkout with a valid payment
    method, assert the order row exists"), so failing a Proof that names no
    backticked check would fail every existing dossier on the day this
    shipped, which is not a control, it is an outage.

    THE CEILING, written here rather than discovered later, and it has two
    halves. First, name matching proves NAMING, not execution: a hand-written
    entry `{"name": "reconcile", "exit_code": 0, "duration_ms": 1}` satisfies
    every row citing `reconcile` with nothing having run, and `--strict-producer`
    on the `ran` gate is the only thing that raises that cost at all. Second,
    and worse for adoption: because prose Proofs only report, a team that
    never adopts backticks keeps this gate permanently inert while it prints a
    verdict that reads like coverage. The sentence this gate returns says how
    many rows it actually matched, so that inertness is visible rather than
    hidden behind the word PASS.
    """
    try:
        rows = _behaviour_table_rows(root)
    except _TableRefused as refused:
        return refused.verdict, refused.evidence
    # An empty Proof cell is a FAIL naming the row, matching check_behaviour's
    # own reading: a Required outcome with no Proof is a rule nobody has agreed
    # how to check. answered() rather than a truth test, so "TBD" is not a proof.
    unproven = [r.get("id") or "(no id)" for r in rows if answered(r.get("proof")) is None]
    if unproven:
        return "FAIL", ("%d of %d behaviour row(s) carry no Proof: %s; a Required outcome with "
                        "no Proof is a rule nobody has agreed how to check, so there is nothing "
                        "for this gate to owe"
                        % (len(unproven), len(rows), ", ".join(unproven[:8])))
    cited = []      # (row id, check name), one per citation
    prose_only = []
    for r in rows:
        names = _PROOF_CITATION.findall(r.get("proof") or "")
        names = [n.strip() for n in names if n.strip()]
        if not names:
            prose_only.append(r.get("id") or "(no id)")
            continue
        for n in names:
            cited.append((r.get("id") or "(no id)", n))
    if not cited:
        return "NO-DATA", ("%d behaviour row(s) read and not one cites a check in backticks, so "
                           "this gate has no promised check to look for. Proof text like %r names "
                           "a check to a person and nothing to this gate; backtick the check name "
                           "to make it an obligation"
                           % (len(rows), one_line((rows[0].get("proof") or ""))[:60]))
    recorded = _recorded_check_names(root)
    missing = [(rid, n) for rid, n in cited if n not in recorded]
    if missing:
        return "FAIL", ("%d behaviour row(s) cite a check no ran-receipt records: %s; a Proof "
                        "naming a check nobody ran is a claim, not a proof"
                        % (len(missing),
                           ", ".join("%s (%s)" % (one_line(rid), one_line(n))
                                     for rid, n in missing[:8])))
    return "PASS", ("%d cited check(s) across %d behaviour row(s) are each recorded by a "
                    "ran-receipt%s. This matches NAMES, not runs: a receipt entry naming a "
                    "check satisfies the row whether or not the check did anything"
                    % (len(cited), len(rows),
                       "" if not prose_only else
                       "; %d row(s) state their Proof as prose and cite no check in backticks, "
                       "so nothing here was owed for them (%s)"
                       % (len(prose_only), ", ".join(one_line(p) for p in prose_only[:8]))))


# The registry is the contract. Each gate declares the evidence it opens, what its
# empty state is, and a worked receipt that SHOULD pass, and
# evals/test_no_data_class.py discovers exactly this dict: a gate added later is
# covered by the meta-test the moment it is registered, because it cannot be
# registered without the declaration. The full_fixture receipts below are minimal
# on purpose: every field in them is a field the PASS sentence asserts over, so
# the honesty test can empty any one of them and demand the PASS goes away.
GATES = {
    "numbers": Check(
        gate_numbers, reads=(MANIFEST,), kind="json", severity="gate", item_key="figures",
        full_fixture={"files": {MANIFEST: {"figures": [{
            "label": "gmv", "snapshot_id": "snap-2026-07",
            "query": "SELECT SUM(amount) FROM orders",
            "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
            "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]}}}),
    "migration": Check(
        gate_migration, reads=(MIGRATION_RECEIPT,), kind="json", severity="gate",
        full_fixture={"files": {MIGRATION_RECEIPT: {
            "forward": {"ran_against_restore": True},
            "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
            "row_counts": {"before": 100, "after_reverse": 100}}}}),
    "approval": Check(
        gate_approval, reads=(APPROVAL_FILE,), kind="git", severity="gate", empty_expect="FAIL",
        empty_fixture="",
        empty_note="the presence of an APPROVAL file IS the claim that this change touches a "
                   "money or partner path. An empty one is that claim with no identity behind "
                   "it, which is a broken claim and not an absence, so it FAILs rather than "
                   "reporting NO-DATA",
        full_fixture={"files": {APPROVAL_FILE: "touches the partner payout path\n"},
                      "git": {"message": "payout batching\n\nReviewed-in: PR-99999"}},
        full_expect="NO-DATA",
        full_expect_reason=(
            "this gate reaches PASS only on a commit whose signature THIS HOST verified, and no "
            "fixture can produce one: the honesty test has no private key and importing one would "
            "make the test prove something about the test's keyring rather than about the gate. The "
            "strongest evidence a fixture can carry is the keyless Reviewed-in trailer, whose "
            "honest verdict is NO-DATA, so that is what the worked example asserts")),
    "ran": Check(
        gate_ran, reads=(RAN_RECEIPT,), kind="json", severity="gate", item_key="checks",
        full_fixture={"files": {RAN_RECEIPT: {"checks": [
            {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]}}}),
    # A3. reads[0] is the BEHAVIOUR TABLE rather than the receipt, because the
    # table is what states an obligation: with no table there is nothing owed,
    # which is this gate's NO-DATA. kind="text" for the same reason: the
    # artifact whose absence and emptiness decide the empty verdict is the
    # markdown one, and the receipt beside it is the evidence the promise is
    # checked against.
    "proof": Check(
        gate_proof, reads=(BEHAVIOUR_TABLE, RAN_RECEIPT), kind="text", severity="gate",
        full_fixture={"files": {
            BEHAVIOUR_TABLE:
                "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                "|---|---|---|---|---|\n"
                "| B1 | a placed order | the nightly batch runs | the ledger balances |"
                " run `reconcile` |\n",
            RAN_RECEIPT: {"checks": [
                {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]},
        }}),
}


# A static list, not derived from GATES or anything else this file tracks:
# nothing in the gate registry above knows these four classes, which is the
# entire point. That is also the ceiling, stated rather than hidden: this is
# four names typed by hand, and it quietly claims an exhaustiveness it does
# not have. A reader taught that unexamined classes get printed here will
# read any class NOT on this list (security, data quality, and anything else
# no gate in GATES opens evidence for) as if it had been examined, when the
# honest reading is that this list is exactly as complete as the day it was
# typed and no more.
UNEXAMINED_CLASSES = ("regression", "cross-device", "performance", "UX")


GATE_USAGE = (
    "usage: sbe_gate.py [gate-name] [directory] [--strict] [--strict-waivers]\n"
    "                     [--strict-producer] [--require-headcommit] [--json]\n"
    "  reads: the receipts and dossier files under the directory (default .).\n"
    "  writes: nothing.\n"
    "  gates (pass one name for a single gate): %s\n"
    "  flags:\n"
    "    --strict          make a FAIL block the exit code\n"
    "    --strict-waivers  make a WAIVED gate artifact block as well\n"
    "    --strict-producer make `ran` refuse a receipt that records no build-system\n"
    "                      producer (no ciRunId, or ciRunId null). OFF by default: the\n"
    "                      report names such receipts on a green run instead. It proves\n"
    "                      only what the receipt CLAIMS, because SBE_CI_RUN_ID is an\n"
    "                      environment variable any shell can export.\n"
    "    --require-headcommit  make `numbers`, `migration` and `ran` refuse a receipt\n"
    "                      that names no headCommit at all as NO-DATA, never a silent\n"
    "                      PASS. OFF by default (docs/KNOWN-LIMITS.md: every shipped\n"
    "                      example receipt in this repository predates the field).\n"
    "    --json            print one machine-readable verdict document instead of the\n"
    "                      human report, and nothing else to stdout. No detail text is\n"
    "                      included: a receipt or a file name is attacker-controlled\n"
    "                      content and this is the one output a caller may parse for a\n"
    "                      verdict, so nothing it prints may be forged by naming a file\n"
    "                      or writing a receipt field.\n"
    "    -h, --help        print this and exit 0 without examining anything"
)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Help is not an error. -h used to be read as "neither a gate name nor
        # a directory" and refused with exit 1.
        for usage_line in (GATE_USAGE % ", ".join(GATES)).splitlines():
            say(usage_line)
        sys.exit(0)
    argv = [a for a in sys.argv[1:]
            if a not in ("--strict", "--strict-waivers", "--strict-producer",
                        "--require-headcommit", "--json")]
    strict = "--strict" in sys.argv
    # A2. Load-bearing and easy to miss: the filter tuple above must know this
    # flag too, or the unknown-flag refusal below exits 2 on it. Carried to
    # `gate_ran` through the module global rather than a parameter, because
    # `run_guarded` calls every gate as `gate(root)`.
    global STRICT_PRODUCER
    STRICT_PRODUCER = "--strict-producer" in sys.argv
    # P6: the same module-global shape as `STRICT_PRODUCER` immediately
    # above, and the same reason. See `REQUIRE_HEADCOMMIT`'s own docstring
    # for why this defaults False.
    global REQUIRE_HEADCOMMIT
    REQUIRE_HEADCOMMIT = "--require-headcommit" in sys.argv
    # A waiver is not a pass, so CI must be able to act on one without reading
    # prose, exactly as sbe_design.py's own --strict-waivers already lets it:
    # --strict-waivers turns every WAIVED gate artifact into a failure for teams
    # that want an exemption to expire on contact; without it the run still
    # exits 0 and the WAIVED line is what a human is shown.
    strict_waivers = "--strict-waivers" in sys.argv
    # --json: one machine-readable verdict document, nothing else on stdout, and
    # the exit code untouched. A pipeline that greps this tool's human report for
    # a verdict line can be defeated by a committed file NAME containing a line
    # break: `find()` returns paths, a path can hold anything a receipt field
    # can, and one_line() only protects the REPORT, not a caller's own grep of
    # it. So this prints the verdict word the gate function itself returned
    # (the same "PASS"/"FAIL"/"NO-DATA" string literals every gate_* above
    # already returns, and "WAIVED" for the same fully-waived case the human
    # report prints), never any receipt- or path-derived text: nothing here can
    # be forged by naming a file or writing a field.
    json_mode = "--json" in sys.argv
    root = "."
    roots = []
    which = list(GATES)
    for a in argv:
        if a in GATES and os.path.isdir(a):
            # `numbers` the gate and `numbers/` the directory are both plausible
            # readings, and picking one silently consumed the other: a directory
            # named after a gate was eaten as a selector and root stayed ".".
            say("sbe_gate: %r is both a gate name and a directory here; pass ./%s for the "
                  "directory" % (a, a))
            sys.exit(1)
        if a in GATES:
            which = [a]
        elif a.startswith("-"):
            # A mistyped flag is a usage error (exit 2, matching the CLI's
            # documented table), not a failed control: exit 1 here taught CI
            # to read a typo as a gate that FAILED.
            for usage_line in (GATE_USAGE % ", ".join(GATES)).splitlines():
                say(usage_line)
            say("sbe_gate: unrecognized flag %r; refusing rather than running past it" % a)
            sys.exit(2)
        elif os.path.isdir(a):
            roots.append(a)
            root = a
        else:
            # Mirrors sbe_design: an argument this tool cannot read is refused
            # by name, never silently dropped.
            say("sbe_gate: %r is neither a gate name (%s) nor a directory."
                  % (a, ", ".join(GATES)))
            sys.exit(1)
    if len(roots) > 1:
        # The last one used to win in silence, so the first directory was
        # neither checked nor mentioned.
        say("sbe_gate: one directory at a time, got %d (%s)."
              % (len(roots), ", ".join(roots)))
        sys.exit(1)
    # The directory this tool examines is the directory it was told to examine.
    # It used to re-root itself at `git rev-parse --show-toplevel` in silence,
    # so inside any git worktree the operator's argument was discarded: an
    # EMPTY directory named on the command line got a PASS earned by a manifest
    # somebody else committed two directories away, the report named neither
    # the directory actually read nor the substitution, and the refusal three
    # lines up ("one directory at a time") was enforced and then overwritten by
    # a third directory nobody named. A silent widening is the same defect
    # class as a silent pruning. The caller passes the root they want (CI
    # passes `.` from the repository root); an empty named directory is
    # NO-DATA for that directory, never a sibling's PASS.
    fails = 0
    waivers = 0
    per_gate_waivers = {}
    seen_refused = set()
    gates_json = []   # [{"name", "verdict", "severity"}], --json's only content
    # A local stand-in for say()/print() rather than a scattered `if not
    # json_mode` at every call site below: --json's contract is ONE json
    # document and nothing else on stdout, so every line the human report
    # prints has to route through one place that can be told to print nothing.
    def report(line):
        if not json_mode:
            say(line)
    if not json_mode:
        print("BROTHERSBE HARD GATES  (advisory unless --strict; NO-DATA is never a pass; "
              "WAIVED is not a pass either)")
    for name in which:
        check = GATES[name]
        artifact = check.reads[0]
        # find() is walked here too, not only inside the gate function, because
        # a WAIVED artifact must never reach run_guarded: the gate function can
        # only return PASS, FAIL or NO-DATA (sbe_checks.run_guarded enforces
        # that), so the decision to skip it in favor of a WAIVED line is made
        # here, before the gate is called, exactly where sbe_design.py's main
        # makes the same decision for a waived design check.
        hits, waived, refused, _scope = find(root, artifact, name)
        for dp, problem in refused:
            # Reported once per directory, however many of the four gates
            # rediscover the same broken `.sbe-exempt`: a broken control is one
            # defect, not four identical lines about it.
            if dp in seen_refused:
                continue
            seen_refused.add(dp)
            fails += 1
            # The machine channel must carry this too. It increments `fails`
            # and prints a FAIL line, so a reader sees it, but a caller reading
            # only the json would have seen `worst` say PASS on a run that
            # failed. A verdict a machine cannot see is a verdict that will be
            # missed by the one consumer we built the channel for.
            gates_json.append({"name": "exempt", "verdict": "FAIL", "severity": "gate"})
            report("  %-9s %-8s %s" % ("exempt", "FAIL", one_line(
                  "%s carries a %s that does not exempt anything: %s. An artifact under it is "
                  "checked below, never silently skipped because its exemption is broken"
                  % (os.path.relpath(dp, root), EXEMPT, problem))))
        for path, reason in waived:
            waivers += 1
            per_gate_waivers[name] = per_gate_waivers.get(name, 0) + 1
            # ">> " prefixed, exactly as sbe_design.py's own waived lines are,
            # so a reader (and a log grep) can tell a waiver apart from a
            # verdict at a glance. Never PASS: this line replaces the check
            # that would otherwise have opened this artifact's file.
            report("  %-9s %-8s %s" % (">> " + name, "WAIVED", one_line(
                  "%s waived by %s: %s" % (os.path.relpath(path, root), EXEMPT,
                                            " ".join(reason.split())[:200]))))
        if not hits and waived:
            # Every instance of this gate's artifact under root is waived, so
            # nothing is left to check. Calling the gate now would print "no
            # <artifact> found" underneath a WAIVED line naming one, which is a
            # false sentence (sbe_design.py's main makes the same call for the
            # same reason); the WAIVED line(s) above are this gate's report.
            # --json reports the same thing the human report does here: no
            # verdict was computed, so the gate's json entry is WAIVED too.
            gates_json.append({"name": name, "verdict": "WAIVED", "severity": check.severity})
            continue
        # Guarded per gate: a crash inside one gate used to abort the loop, so
        # every gate after it printed nothing at all and advisory mode exited 0.
        # A gate that vanishes is worse than a gate that fails.
        verdict, ev = run_guarded(name, check, root)
        if verdict == "FAIL":
            fails += 1
        gates_json.append({"name": name, "verdict": verdict, "severity": check.severity})
        # one_line: several evidence strings interpolate receipt-authored values,
        # and a label containing newlines used to write verdict lines for the
        # OTHER gates into this report, which the report's own parsers then read.
        # The artifact under inspection writes nothing outside its own line.
        # --json never reaches `ev` at all: that string is exactly the free
        # text this flag exists to leave out (see the docstring at --json above).
        report("  %-9s %-8s %s [severity: %s]" % (name, verdict, one_line(ev), check.severity))
    # Printed on every run, including all-green: the adopter team's closing
    # finding was that a green verdict systematically under-represents how
    # much checking remains, and the all-green case is exactly the one where
    # nobody thinks to ask what was left out. See UNEXAMINED_CLASSES for the
    # ceiling this line carries: four static names, no exhaustiveness claim.
    report("NOT EXAMINED: %s. No gate here opens evidence for these classes, so the verdicts "
          "above say nothing about them (NO-DATA is never a pass)." % ", ".join(UNEXAMINED_CLASSES))
    if waivers:
        breakdown = ", ".join("%s: %d" % (g, per_gate_waivers[g]) for g in sorted(per_gate_waivers))
        report("WAIVERS: %d artifact(s) across %d gate(s) were waived by a %s and examined nothing "
              "(%s). A waiver is not a pass; run `--strict --strict-waivers` to make one block a "
              "merge." % (waivers, len(per_gate_waivers), EXEMPT, breakdown))
    if strict_waivers and waivers:
        fails += waivers
    if json_mode:
        # "worst" ranks the same four words FAIL < NO-DATA < WAIVED < PASS,
        # worst first: the ranking --strict already enforces (only FAIL blocks;
        # NO-DATA and WAIVED never pass but never block alone) restated as an
        # order a caller can pick a max/min over instead of re-deriving it.
        rank = {"FAIL": 0, "NO-DATA": 1, "WAIVED": 2, "PASS": 3}
        worst = min(gates_json, key=lambda g: rank[g["verdict"]])["verdict"] if gates_json else "PASS"
        # Through say(), like every other line this tool prints. Not because a
        # JSON document can carry a raw newline (json.dumps escapes control
        # characters, so one_line() is a no-op over this string) but because the
        # report-print lint enforces the CLASS rather than a list of instances
        # believed safe today: the forgery that closed this hole the first time
        # walked past every wrapped value by hiding in the one nobody wrapped.
        # An exemption here would be a second place a later edit could reopen.
        say(json.dumps({"gates": gates_json, "worst": worst}, sort_keys=True))
    if strict and fails:
        report("STRICT: %d hard gate(s) failed; exiting nonzero to block the merge." % fails)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # A broken gate must not silently pass work. In strict mode a crash blocks.
        say("sbe_gate: error %r" % (e,))
        sys.exit(1 if "--strict" in sys.argv else 0)

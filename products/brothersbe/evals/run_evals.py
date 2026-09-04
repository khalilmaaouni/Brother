#!/usr/bin/env python3
"""BrotherSBE regression evals: each is a real failure class as a fixture, a
planted defect, and an assertion that the corresponding gate CATCHES it. A
release is blocked (exit nonzero) if any eval regresses. This is the mechanism
behind the claim that BrotherSBE is proven in traceable ways: the gates are
tested against the exact defects the operating record produced.

Every fixture is generalized: no client, employer, or private project appears.
"""
import fnmatch, json, os, re, sys, tempfile, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "..", "tools", "sbe_gate.py")
INTAKE = os.path.join(HERE, "..", "tools", "sbe_intake.py")


DESIGN_CLASSES = ("artifacts", "adr", "datamodel", "diagrams", "placeholder", "behaviour")
DESIGN = os.path.join(HERE, "..", "tools", "sbe_design.py")
TEMPLATES = os.path.join(HERE, "..", "templates", "dossier")


def run_gate(root, klass):
    script = DESIGN if klass in DESIGN_CLASSES else GATE
    out = subprocess.run([sys.executable, script, klass, root],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == klass:
            return parts[1]
    return "?"


def write(root, rel, obj):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(obj, f) if isinstance(obj, (dict, list)) else f.write(obj)


def git_init(root):
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "e@e"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "T"], check=True)


CASES = []


def case(name, klass, expect):
    def deco(fn):
        CASES.append((name, klass, expect, fn))
        return fn
    return deco


# Design-side evals: the tier is computed, not judged.
from importlib.machinery import SourceFileLoader
_intake = SourceFileLoader("sbe_intake", os.path.join(HERE, "..", "tools", "sbe_intake.py")).load_module()


@case("tier-trivial-when-reversible-and-isolated", "tier", "T0")
def t1(root):
    return _intake.compute_tier({"changes_contract": False, "crosses_boundary": False,
                                 "reversible_under_hour": True, "touches_sensitive": False,
                                 "consumers": "none"})


@case("tier-system-when-sensitive", "tier", "T3")
def t2(root):
    return _intake.compute_tier({"changes_contract": False, "crosses_boundary": False,
                                 "reversible_under_hour": True, "touches_sensitive": True,
                                 "consumers": "none"})


@case("tier-feature-when-contract-changes", "tier", "T2")
def t3(root):
    return _intake.compute_tier({"changes_contract": True, "crosses_boundary": False,
                                 "reversible_under_hour": True, "touches_sensitive": False,
                                 "consumers": "some"})


@case("tier-change-when-one-boundary", "tier", "T1")
def t4(root):
    return _intake.compute_tier({"changes_contract": False, "crosses_boundary": True,
                                 "reversible_under_hour": True, "touches_sensitive": False,
                                 "consumers": "none"})


#: The pilot team reported an ordinary change sized T2 and handed six design
#: documents: a field added in the CMS, read by the backend, returned through
#: the API, displayed by the client. Nothing that exists today has to change.
#: A yes on the contract question alone produced T2, so ceremony followed the
#: SHAPE of the answer rather than the blast radius. These cases pin the split
#: that fixed it.
#:
#: The base fixture ISOLATES the new branch on purpose: every other answer sits
#: at its lowest value, so `additive` is the only thing that can lift this off
#: T0. That isolation is not decoration, it is the whole point. The first draft
#: of these cases used the team's real shape, which also answers yes to
#: `crosses_boundary`; reinjection showed it returned T1 with the new branch
#: DELETED, because the boundary rule reached T1 by itself. The case passed
#: while measuring nothing. Calibrated form: delete the `additive` term from
#: compute_tier and ADDITIVE_ALONE drops to T0 while every guard case below
#: still passes.
_ADDITIVE_ALONE = {"changes_contract": "additive", "crosses_boundary": "n",
                   "reversible_under_hour": "y", "touches_sensitive": "n",
                   "consumers": "none"}


@case("an-additive-contract-change-is-not-charged-as-a-breaking-one", "tier", "T1")
def t4a(root):
    return _intake.compute_tier(dict(_ADDITIVE_ALONE))


@case("the-teams-own-field-pull-through-no-longer-owes-a-full-dossier", "tier", "T1")
def t4f(root):
    # The reported change, answered honestly: it does cross a boundary, and it
    # is additive. Before the split this was T2 and six documents. This is an
    # OUTCOME case, not a guard case: the boundary rule would hold it at T1
    # even with the new branch removed, and saying so here keeps it from being
    # read as proof of the branch.
    a = dict(_ADDITIVE_ALONE); a["crosses_boundary"] = "y"; a["consumers"] = "some"
    return _intake.compute_tier(a)


@case("a-breaking-contract-change-still-costs-what-it-always-did", "tier", "T2")
def t4b(root):
    a = dict(_ADDITIVE_ALONE); a["changes_contract"] = "breaking"
    return _intake.compute_tier(a)


@case("an-answer-stored-before-the-split-keeps-the-tier-it-was-judged-at", "tier", "T2")
def t4c(root):
    # The one-directional mapping. A dossier on disk carrying `true` must not
    # quietly become T1 because the vocabulary widened underneath it.
    a = dict(_ADDITIVE_ALONE); a["changes_contract"] = True
    return _intake.compute_tier(a)


@case("additive-never-outranks-the-sensitivity-rule", "tier", "T3")
def t4d(root):
    a = dict(_ADDITIVE_ALONE); a["touches_sensitive"] = "y"
    return _intake.compute_tier(a)


@case("additive-never-outranks-many-broken-consumers", "tier", "T2")
def t4e(root):
    a = dict(_ADDITIVE_ALONE); a["consumers"] = "many"
    return _intake.compute_tier(a)


def _tier_or_refusal(answers):
    """The tier, or the sentence naming the answer the rule refused to interpret."""
    try:
        return _intake.compute_tier(answers)
    except _intake.UnreadableIntake as e:
        return "REFUSED: %s" % e


@case("tier-reads-the-y-n-vocabulary-its-own-prompt-teaches", "tier", "T0")
def t5(root):
    # Every answer honest, every answer written the way the tool asks for it. This
    # computed T3 (the maximum) because the string "n" is truthy, so an engineer
    # answering no to all five questions was handed seven artifacts of ceremony,
    # and a gate that blocks honest work is a gate that gets switched off.
    return _tier_or_refusal({"changes_contract": "n", "crosses_boundary": "n",
                             "reversible_under_hour": "y", "touches_sensitive": "n",
                             "consumers": "none"})


@case("tier-no-to-reversible-is-a-no-not-a-yes", "tier", "T3")
def t6(root):
    # The reproduction from the review, in the direction that hides work rather
    # than inflating it: "no, this is not reversible in under an hour" is the T3
    # rule's own trigger, and the string "no" being truthy computed T0, which
    # requires no artifact at all and makes every design check report NO-DATA at
    # exit 0.
    return _tier_or_refusal({"changes_contract": False, "crosses_boundary": False,
                             "reversible_under_hour": "no", "touches_sensitive": False,
                             "consumers": "none"})


@case("tier-refuses-a-consumers-value-it-does-not-know", "tier",
      "REFUSED: consumers='several' is not a recognized value (accepted: none, some, many)")
def t7(root):
    # "several" matched neither "some" nor "many" and fell through to the lowest
    # tier. An unrecognized value must never quietly mean no risk; it is reported
    # as unrecognized, exactly as sbe_decide.py reports one.
    return _tier_or_refusal({"changes_contract": False, "crosses_boundary": False,
                             "reversible_under_hour": True, "touches_sensitive": False,
                             "consumers": "several"})


@case("tier-refuses-an-answer-outside-the-yes-no-vocabulary", "tier",
      "REFUSED: touches_sensitive='maybe' is not a recognized value "
      "(accepted: false, n, no, true, y, yes, or a JSON boolean)")
def t8(root):
    return _tier_or_refusal({"changes_contract": False, "crosses_boundary": False,
                             "reversible_under_hour": True, "touches_sensitive": "maybe",
                             "consumers": "none"})


@case("tier-refuses-a-blank-answer-rather-than-reading-it-as-a-no", "tier",
      "REFUSED: crosses_boundary='' is not a recognized value "
      "(accepted: false, n, no, true, y, yes, or a JSON boolean)")
def t9(root):
    return _tier_or_refusal({"changes_contract": False, "crosses_boundary": "",
                             "reversible_under_hour": True, "touches_sensitive": False,
                             "consumers": "none"})


@case("an-unknown-tier-is-refused-rather-than-owing-no-artifact", "tier",
      "REFUSED: unknown tier 'T9'")
def t10(root):
    try:
        return _intake.required_artifacts("T9")
    except ValueError as e:
        return "REFUSED: %s" % str(e).split(" (expected")[0]


# 1. The filed model that overstated a five year total against its own components.
@case("overstated-total-caught", "numbers", "FAIL")
def c1(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "five_year_total", "snapshot_id": "snap_2026_07",
        "query": "SELECT SUM(y) FROM plan", "second_derivation": "SELECT y1+y2+y3+y4+y5 FROM plan_wide",
        "rerun": {"ran": True, "primary": 1938, "secondary": 432}}]})


# 2. A correct figure passes: pinned, independent, zero drift.
@case("sound-number-passes", "numbers", "PASS")
def c2(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap_2026_07",
        "query": "SELECT SUM(amount) FROM orders", "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


# 3. A figure whose "second" check is a copy of the first is not independent.
@case("non-independent-derivation-caught", "numbers", "FAIL")
def c3(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "x", "snapshot_id": "s", "query": "SELECT SUM(a) FROM t",
        "second_derivation": "SELECT SUM(a) FROM t", "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


# 4. A number with no pinned snapshot against a live warehouse: drift risk.
@case("unpinned-read-caught", "numbers", "FAIL")
def c4(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "x", "query": "SELECT 1", "second_derivation": "SELECT 1 FROM dual",
        "rerun": {"ran": True, "primary": 1, "secondary": 1}}]})


# 4b. A figure marked rerun ran=true but missing the values needed to prove zero
# drift (no primary, no secondary) must FAIL, not PASS: a gate that reports
# "zero-drift check" without the numbers to compare is a figure shipping
# unverified while the evidence line claims otherwise.
@case("rerun-marked-ran-with-no-values-caught", "numbers", "FAIL")
def c4b(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "x", "snapshot_id": "s", "query": "SELECT SUM(a) FROM t",
        "second_derivation": "SELECT SUM(b) FROM t2", "rerun": {"ran": True}}]})


# 4c. An empty manifest is the cheapest way to defeat this gate: commit a file
# with no figures in it and a two-state checker prints PASS over zero figures,
# in evidence words that claim they were pinned and re-derived. Present but
# empty is NO-DATA, never a pass.
@case("empty-figures-manifest-is-nodata", "numbers", "NO-DATA")
def c4c(root):
    write(root, "numbers-manifest.json", {"figures": []})


# 4d. A manifest that exists and cannot be parsed is a broken claim, not an
# absent one, so it FAILs rather than falling back to the missing-file NO-DATA.
@case("malformed-manifest-caught", "numbers", "FAIL")
def c4d(root):
    write(root, "numbers-manifest.json", "this is not json at all\n")


# 4e. A misspelled key (figure, not figures) must not read as zero problems.
@case("misspelled-figures-key-is-nodata", "numbers", "NO-DATA")
def c4e(root):
    write(root, "numbers-manifest.json", {"figure": [{"label": "revenue"}]})


# 4f. A figure with no query at all has nothing for the second derivation to be
# independent of, so the textual-difference test is vacuous and must not pass.
@case("figure-with-no-query-caught", "numbers", "FAIL")
def c4f(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "x", "snapshot_id": "s", "second_derivation": "SELECT SUM(b) FROM t2",
        "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


# 5. A migration whose reverse never ran against a restore.
@case("untested-reverse-caught", "migration", "FAIL")
def c5(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": False},
        "row_counts": {"before": 100, "after_reverse": 100}})


# 6. A migration whose reverse receipt is free text, no resolvable id.
@case("unresolvable-rehearsal-id-caught", "migration", "FAIL")
def c6(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True},
        "row_counts": {"before": 100, "after_reverse": 100}})


# 7. A sound migration passes.
@case("sound-migration-passes", "migration", "PASS")
def c7(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job_8842"},
        "row_counts": {"before": 100, "after_reverse": 100}})


# 8. A reverse that dropped rows is caught by the row-count check.
@case("lossy-reverse-caught", "migration", "FAIL")
def c8(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job_1"},
        "row_counts": {"before": 100, "after_reverse": 61}})


# 9. A money-path change with only a typed name is not an approval.
@case("typed-name-approval-caught", "approval", "FAIL")
def c9(root):
    write(root, "APPROVAL", "touches partner billing path\n")
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "change\n\nApproved-by: Someone"], check=True)


# 10. A change with no money/partner claim needs no approval (NO-DATA, not fail).
@case("no-approval-needed-is-nodata", "approval", "NO-DATA")
def c10(root):
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "internal refactor"], check=True)


# 11. An SQL change with a receipt whose check has no exit code did not really run.
@case("unrun-check-caught", "ran", "FAIL")
def c11(root):
    write(root, "ran-receipt.json", {"checks": [{"name": "reconcile", "duration_ms": 0}]})


# 12. A pipeline change whose reconciliation actually ran passes.
@case("executed-check-passes", "ran", "PASS")
def c12(root):
    write(root, "ran-receipt.json", {"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})


# 13. A check that ran and FAILED is caught (agent claimed green on red).
@case("green-on-red-caught", "ran", "FAIL")
def c13(root):
    write(root, "ran-receipt.json", {"checks": [{"name": "row_parity", "exit_code": 1, "duration_ms": 400}]})


# 13b. A zero-check receipt is exactly the artifact produced by a session that
# wants to look done without running anything. Present but empty is NO-DATA.
@case("empty-checks-receipt-is-nodata", "ran", "NO-DATA")
def c13b(root):
    write(root, "ran-receipt.json", {"checks": []})


# 13c. An unparseable ran-receipt is a broken claim, so it FAILs.
@case("malformed-ran-receipt-caught", "ran", "FAIL")
def c13c(root):
    write(root, "ran-receipt.json", "<<<garbage>>>\n")


# 13d. A key typo (check, not checks) must not read as zero problems.
@case("misspelled-checks-key-is-nodata", "ran", "NO-DATA")
def c13d(root):
    write(root, "ran-receipt.json", {"check": [{"name": "recon"}]})


# 13e. An unparseable migration receipt is a broken claim, not an absence.
@case("malformed-migration-receipt-caught", "migration", "FAIL")
def c13e(root):
    write(root, "migration-receipt.json", "{not json\n")


# The %G? codes, pinned. These call gate_approval directly with the signature
# status substituted, because producing a real E needs a signed commit whose key
# is absent from the keyring, which cannot be built inside a hermetic fixture.
# The decision under test is the mapping from code to verdict, and that is what
# is pinned here: G and U are approvals, E is NO-DATA, everything else FAILs.
_gate = SourceFileLoader("sbe_gate", os.path.join(HERE, "..", "tools", "sbe_gate.py")).load_module()
try:
    _checks = SourceFileLoader("sbe_checks", os.path.join(HERE, "..", "tools", "sbe_checks.py")).load_module()
except OSError as e:
    # This suite is run against older trees on purpose, to measure what it would
    # have caught. A tree with no sbe_checks.py is one of them, and the fixture
    # that needs it reports that rather than taking the whole run down.
    _checks = None
    _CHECKS_IMPORT_ERROR = "sbe_checks.py is not in this tree (%s)" % e


def _approval_with_sig(root, sig, approver="Someone", authors=("Dana Author", "dana@example.com"),
                       signer="", committers=None):
    """The approval verdict for a signature state, with the approver named.

    `authors` is what git says wrote the commit. It is a separate identity from
    the approver by default, because self-approval is its own FAIL now and these
    fixtures are about the SIGNATURE STATE, one variable at a time. The default
    meta records the same person as author and committer (the common one-writer
    commit) with no signer principal, matching an ordinary signed commit where
    the author signed; `committers` and `signer` exist for the co-sign shapes,
    where the approver amended and the signature's principal decides.
    """
    original = _gate.git_trailers
    committers = list(authors) if committers is None else list(committers)
    who = list(authors) + [c for c in committers if c not in authors]
    meta = {"author": list(authors), "committer": committers, "signer": signer}
    _gate.git_trailers = lambda r: ("change\n\nApproved-by: %s" % approver, sig, who, meta)
    try:
        return _gate.gate_approval(root)[0]
    finally:
        _gate.git_trailers = original


# 13f. E means the signature could not be verified here, which on a runner with
# no imported keys is the result for EVERY signed commit, including one signed
# by a key nobody on the team recognises. Accepting it would trust the unknown
# while rejecting a known key that had merely expired. NO-DATA, not an approval.
@case("unverifiable-signature-E-is-not-an-approval", "sig", "NO-DATA")
def c13f(root):
    return _approval_with_sig(root, "E")


@case("verified-signature-G-approval-passes", "sig", "PASS")
def c13g(root):
    return _approval_with_sig(root, "G")


@case("valid-untrusted-signature-U-is-not-an-approval", "sig", "NO-DATA")
def c13g2(root):
    # U is "cryptographically valid, no matching principal". Under SSH signing,
    # the default for teams adopting commit signing today, that is exactly what
    # a key the agent generated for itself produces ("No principal matched"),
    # so accepting U made L9's "an agent without the private key cannot produce
    # it" false in four commands: ssh-keygen, two git configs, one commit.
    return _approval_with_sig(root, "U")


@case("a-trailing-period-does-not-make-a-second-person", "sig", "FAIL")
def c13i(root):
    return _approval_with_sig(root, "G", approver="Dana Author.")


@case("a-reordered-name-does-not-make-a-second-person", "sig", "FAIL")
def c13i2(root):
    return _approval_with_sig(root, "G", approver="Author, Dana")


@case("an-initial-does-not-make-a-second-person", "sig", "FAIL")
def c13i3(root):
    return _approval_with_sig(root, "G", approver="D. Author")


@case("a-plus-address-does-not-make-a-second-person", "sig", "FAIL")
def c13i4(root):
    return _approval_with_sig(root, "G", approver="dana+ops@example.com")


@case("a-role-suffix-does-not-make-a-second-person", "sig", "FAIL")
def c13i5(root):
    return _approval_with_sig(root, "G", approver="Dana Author (approver)")


@case("approved-by-todo-names-no-approver", "sig", "FAIL")
def c13i6(root):
    return _approval_with_sig(root, "G", approver="TODO")


@case("a-genuinely-different-approver-still-passes", "sig", "PASS")
def c13i7(root):
    # The control: hardening the identity comparison must not read every
    # colleague as the author.
    return _approval_with_sig(root, "G", approver="Robin Reviewer <robin@example.com>")


@case("bad-signature-B-is-not-an-approval", "sig", "FAIL")
def c13h(root):
    return _approval_with_sig(root, "B")


@case("expired-signature-X-is-not-an-approval", "sig", "FAIL")
def c13h2(root):
    return _approval_with_sig(root, "X")


@case("unsigned-commit-N-is-not-an-approval", "sig", "FAIL")
def c13h3(root):
    return _approval_with_sig(root, "N")


@case("a-soft-hyphen-does-not-make-a-second-person", "sig", "FAIL")
def c13i8(root):
    # U+00AD renders as nothing in git, GitHub and every ordinary terminal, so
    # this approver line READS as the author's own name and mailbox. The word
    # comparison and the plus-address canonicalization were both defeated by
    # two invisible bytes; identities now compare on their rendered skeleton.
    return _approval_with_sig(root, "G",
                              approver="Da\u00adna Author <dana+ops@exampl\u00ade.com>")


@case("a-homoglyph-does-not-make-a-second-person", "sig", "FAIL")
def c13i9(root):
    # Cyrillic U+0430 for ASCII "a": no invisible character at all, renders
    # identically, compares differently on code points. The skeleton maps the
    # lookalike onto the letter it renders as, so this reads as the author.
    return _approval_with_sig(root, "G", approver="D\u0430n\u0430 Author")


@case("a-confusable-outside-the-curated-table-is-refused-not-passed", "sig", "FAIL")
def c13i10(root):
    # COPTIC SMALL LETTER O (U+2C9F) renders as `o` and sat outside the
    # curated homoglyph table, so `Dana Authⲟr` compared as a second
    # person and restored the self-approval PASS on the money gate. The rule
    # replaces the table as the load-bearing part: a word mixing alphabets
    # after normalization and confusable folding is refused by name, so
    # certification of difference no longer depends on table coverage.
    return _approval_with_sig(root, "G", approver="Dana Authⲟr")


@case("a-same-script-lookalike-letter-is-refused-not-passed", "sig", "FAIL")
def c13i11(root):
    # LATIN LETTER SMALL CAPITAL A (U+1D00): same script family as ASCII, in
    # no confusable table, renders as the author's name. A mixed-SCRIPT rule
    # alone would pass it; the alphabet-readability rule refuses it, which is
    # the probe that the closure is a rule over the class and not a wider list.
    return _approval_with_sig(root, "G", approver="Dᴀna Author")


@case("a-multi-script-forgery-is-refused-whatever-scripts-it-uses", "sig", "FAIL")
def c13i12(root):
    # Armenian vo (U+0578, renders as n) plus Coptic o: two scripts the table
    # holds no rows for, in one identity. The forge must not depend on any
    # single script being catalogued.
    return _approval_with_sig(root, "G", approver="Daոa Authⲟr")


@case("a-partially-mapped-single-script-word-is-refused-not-passed", "sig", "NO-DATA")
def c13i13(root):
    # Cyrillic `Дана` (Dana): wholly one script, and exactly the shape of the
    # author's own name (four letters against four, none of them decided
    # against its counterpart), which is also how the author would write
    # their own name in another alphabet. Difference is not proven, sameness
    # is not proven, and the verdict says precisely that: NO-DATA naming the
    # substitution-compatibility, never a PASS the fold cannot vouch for and
    # never a refusal of a name that may be honest.
    return _approval_with_sig(root, "G", approver="Дана Author")


@case("a-wholly-single-script-approver-still-passes", "sig", "PASS")
def c13i14(root):
    # The control: an honest identity written wholly in one script reads and
    # compares. Refusing every non-Latin approver would be the gate rejecting
    # correct work, which is how gates get switched off.
    return _approval_with_sig(root, "G", approver="田中太郎 <tanaka@example.com>")


@case("a-whole-word-small-capital-name-is-the-author-not-a-second-person", "sig", "FAIL")
def c13i15(root):
    # EVERY letter of the approver in the Latin small-capital block (U+1D00 and
    # neighbours): no word mixes anything, so both mixture rules pass it, and
    # the block has no decomposition and no confusable-table row, so it used to
    # certify "not the commit's author or committer" over the author's own name
    # on the money gate. The Unicode-name fold now reads each letter's rendered
    # base out of the character database (a rule, not a table), the name folds
    # to the author, and the verdict is the self-approval FAIL. Direction
    # pinned: refusal, never PASS.
    return _approval_with_sig(root, "G",
                              approver="ᴅᴀɴᴀ ᴀᴜᴛʜᴏʀ")


@case("a-latin-letter-with-no-readable-base-proves-nothing-and-structure-decides", "sig", "PASS")
def c13i16(root):
    # LATIN SMALL LETTER ALPHA (U+0251) and GAMMA (U+0263): no decomposition,
    # no table row, no single base letter in the Unicode name, so no fold
    # this host has can read them, and they prove NOTHING in either
    # direction. The certificate does not need them: a three-letter word fits
    # neither `Dana` nor `Author` under any one-for-one substitution, so the
    # difference is proven by structure alone and the honest verdict is PASS.
    # The round-11 shape of this case refused the word outright, and that
    # same wholesale refusal rejected Bæk and Þóra on the money gate; the
    # substitution-compatible attacks (the Lisu and Cherokee cases below)
    # are where non-certification now lives.
    return _approval_with_sig(root, "G", approver="ɑɣɑ Author")


@case("a-lisu-spelling-of-the-author-cannot-certify-a-second-person", "sig", "NO-DATA")
def c13i17(root):
    # Lisu letters ARE Latin capital letterforms (U+A4D0 and neighbours), so
    # this approver renders as DANA AUTHOR on every screen while no fold this
    # host has can read a single letter of it. Letter-for-letter it is
    # substitution-compatible with the author, so the negative "the approver
    # is not the author" is not proven and the money gate must not assert
    # it: NO-DATA naming the ambiguity, never the certificate. This is the
    # class the curated-table and Latin-family rounds kept reopening, closed
    # as a rule: uncatalogued proves nothing, in either direction.
    return _approval_with_sig(root, "G", approver="ꓓꓮꓠꓮ ꓮꓴꓔꓧꓳꓣ")


@case("a-cherokee-shape-compatible-approver-cannot-certify", "sig", "NO-DATA")
def c13i18(root):
    # Cherokee letterforms are Latin-like and wholly unreadable to the fold;
    # a four-letter word beside a six-letter word is the author's own shape,
    # so difference is unproven whatever the letters look like.
    return _approval_with_sig(root, "G", approver="ᎠᎡᎢᎣ ᎤᎥᎦᎧᎨᎩ")


@case("a-wholly-coptic-shape-compatible-approver-cannot-certify", "sig", "NO-DATA")
def c13i19(root):
    # The mixed Coptic case (c13i10) is refused as a disguise; the WHOLLY
    # Coptic word has no mixture to refuse and used to pass as readable.
    # Substitution-compatibility now catches what the mixture rule cannot.
    return _approval_with_sig(root, "G", approver="ⲟⲁⲟⲁ ⲁⲟⲟⲁⲟⲁ")


@case("a-bidi-override-cannot-render-an-approver-as-the-author", "sig", "FAIL")
def c13i20(root):
    # U+202E RIGHT-TO-LEFT OVERRIDE: `rohtuA anaD` reversed RENDERS as
    # `Dana Author` in git log and every bidi-honoring view, while the
    # stripped comparison reads the logical order and used to certify a
    # second person. Stripping hides a reordering rather than undoing it, so
    # a value carrying a reordering control is refused by code point.
    return _approval_with_sig(root, "G",
                              approver="\u202erohtuA anaD\u202c")


@case("an-icelandic-name-with-thorn-passes-cleanly", "sig", "PASS")
def c13i21(root):
    # Þóra carries a letter no fold reduces (thorn), and needs no reduction:
    # `r` against the author's `n` is a difference both sides render as
    # plain ASCII, so the difference is proven and the certificate is
    # earned. The round-11 residue rule refused this name outright, which
    # taught an honest approver to spell their own name wrong.
    return _approval_with_sig(root, "G", approver="Þóra Jónsdóttir")


@case("a-danish-name-with-ae-passes-cleanly", "sig", "PASS")
def c13i22(root):
    # Kjær Sæther is shape-compatible with Dana Author (four letters beside
    # six), so structure alone cannot certify it; the proof is positional:
    # `K` against `D` and `S` against `A` are plain-ASCII differences no
    # substitution explains. The probe that honest ae-carrying names are
    # certified by their readable letters, not refused for their unreadable
    # one.
    return _approval_with_sig(root, "G", approver="Kjær Sæther <kjaer@example.com>")


@case("an-icelandic-name-with-eth-passes-cleanly", "sig", "PASS")
def c13i23(root):
    # Eth and thorn in both words, and the difference is still proven by the
    # readable letters around them.
    return _approval_with_sig(root, "G", approver="Þórður Guðmundsson")


@case("an-identity-written-wholly-in-parentheses-is-still-the-author", "sig", "FAIL")
def c15i1(root):
    # `(Dana Author)` parsed to an EMPTY word list (a parenthesized span was
    # annotation, deleted), and the certifying comparison consumed the empty
    # comparison's "no collision" as PROOF of difference: one pair of
    # parentheses bought the self-approval control on the money gate. The
    # founding rule, applied to the comparison itself: a comparison must
    # first establish that it examined something. Bracket content is read as
    # the name when nothing exists outside it.
    return _approval_with_sig(root, "G", approver="(Dana Author)")


@case("an-identity-in-angle-brackets-with-no-mailbox-is-a-name", "sig", "FAIL")
def c15i2(root):
    # `<Dana Author>` is not an email (no @), so deleting it as a mailbox
    # span emptied the comparison the same way the parentheses did.
    return _approval_with_sig(root, "G", approver="<Dana Author>")


@case("a-name-hidden-in-an-annotation-still-compares-as-a-name", "sig", "FAIL")
def c15i3(root):
    # `(Dana Author), staff engineer`: words exist outside the brackets, so
    # the bracketed span reads as annotation, but the all-words reading is
    # kept as a second candidate, and the author's name inside it is still
    # the author.
    return _approval_with_sig(root, "G", approver="(Dana Author), staff engineer")


@case("a-blank-rendering-joiner-does-not-weld-a-second-person", "sig", "FAIL")
def c15i4(root):
    # U+2800 BRAILLE PATTERN BLANK renders as a blank between the words and
    # is not Python whitespace, so `Dana⠀Author` split as ONE long word,
    # structurally different from every author word, and structure was
    # consumed as proof of difference. A character whose glyph is a blank
    # reads as the space a reader sees.
    return _approval_with_sig(root, "G", approver="Dana⠀Author")


@case("an-invisible-weld-does-not-weld-a-second-person", "sig", "FAIL")
def c15i5(root):
    # The sibling weld: a zero-width space is DELETED by the invisible-class
    # rule, so `Dana(ZWSP)Author` became the single word `danaauthor`, again
    # structurally different from both author words. The spaceless
    # renderings are compared too, so a welded spelling of the author is the
    # author.
    return _approval_with_sig(root, "G", approver="Dana​Author")


@case("an-empty-author-side-cannot-prove-difference", "sig", "NO-DATA")
def c15i6(root):
    # The governing rule as a scenario: empty one side of the certifying
    # comparison and the verdict must never improve. A commit recording no
    # readable author or committer identity gives the comparison nothing to
    # examine, and a comparison that examined nothing proves nothing.
    return _approval_with_sig(root, "G", approver="Robin Reviewer <robin@example.com>",
                              authors=())


@case("an-email-only-approver-against-a-nameless-author-is-not-proven", "sig", "NO-DATA")
def c15i7(root):
    # One side records only an email, the other only a name: no comparison
    # examined anything, so nothing is proven in either direction.
    return _approval_with_sig(root, "G", approver="<robin@example.com>",
                              authors=("Dana Author",))


@case("a-proven-different-email-is-proof-of-difference", "sig", "PASS")
def c15i8(root):
    # The remedy the NO-DATA sentence advertises, demonstrated working: an
    # approver whose name this host cannot certify (Devanagari, opaque to
    # every fold) records an email address that differs from the author's at
    # readable positions, and the email difference is accepted as proof.
    # Before this rule a proven-different email fell through to the name
    # test and changed nothing, so the printed remedy was false.
    return _approval_with_sig(root, "G", approver="राहुल शर्मा <rahul@corp.com>")


@case("a-confusable-email-is-the-authors-own-mailbox", "sig", "FAIL")
def c15i9(root):
    # The email path must not reopen the look-alike hole the name path
    # closed: a Cyrillic `а` in the local part renders as the author's own
    # mailbox. The skeleton fold reads it AS the author's mailbox, so this
    # is the self-approval FAIL, not a certificate.
    return _approval_with_sig(root, "G", approver="Robin <dаna@example.com>")


@case("an-unreadable-letter-in-an-email-is-refused-not-certified", "sig", "FAIL")
def c15i9b(root):
    # A Lisu letter in the local part renders as the author's `D` while no
    # fold reads it. The mixed-script refusal catches it first (an email
    # word carrying Lisu beside Latin is a disguise shape), so the verdict
    # is the affirmative FAIL; the email-collide NO-DATA inside the
    # certifying path stays as layered defense behind this refusal, and the
    # point pinned here is the direction: never PASS.
    return _approval_with_sig(root, "G", approver="Robin <ꓓana@example.com>")


@case("two-names-of-one-opaque-script-compare-by-code-point", "sig", "PASS")
def c15i10(root):
    # An all-Devanagari team, both identities name-only: every letter is
    # opaque to the fold, but two different letters of ONE script are
    # visibly different glyphs, not look-alikes of each other, on the
    # precedent CJK set. 80 percent of ordinary Amharic pairs and 53 percent
    # of Hindi pairs were refused under the blanket opaque rule, and the
    # only escapes were transliterating or deleting the name.
    return _approval_with_sig(root, "G", approver="राहुल शर्मा",
                              authors=("अनिल कुमार", "anil@corp.com"))


@case("a-lisu-spelling-of-the-author-still-cannot-certify", "sig", "NO-DATA")
def c15i11(root):
    # The forgery axis the same-script rule must NOT reopen: a Lisu spelling
    # of the ASCII author is opaque-versus-hard, cross-script, and stays
    # unproven.
    return _approval_with_sig(root, "G", approver="ꓓꓮꓠꓮ ꓮꓴꓔꓧꓳꓣ")


@case("an-approver-who-amends-and-signs-is-the-approver-not-a-second-author", "sig", "PASS")
def c15i12(root):
    # The co-sign shape: under SSH signing an approver signs by amending,
    # which makes them the committer, and that is git's ONLY spelling of
    # "the reviewer signed". The old guard lumped committer in with author
    # and FAILed the strongest honest approval git can express, while the
    # FAIL's remedy ("have the approver sign") pointed back into itself. The
    # carve-out is bound to a key: it opens only when the signature's
    # matched principal is the approver's own email.
    return _approval_with_sig(root, "G", approver="Robin Reviewer <robin@example.com>",
                              authors=("Dana Author", "dana@example.com"),
                              committers=("Robin Reviewer", "robin@example.com"),
                              signer="robin@example.com")


@case("a-spoofed-committer-name-signed-with-the-authors-key-stays-refused", "sig", "FAIL")
def c15i13(root):
    # The attack the carve-out must not admit: the author amends with
    # GIT_COMMITTER_NAME spoofed to the approver and signs with their OWN
    # key. The signature's principal is the author, not the approver, so
    # the committer match proves nothing and the verdict stays FAIL.
    return _approval_with_sig(root, "G", approver="Robin Reviewer <robin@example.com>",
                              authors=("Dana Author", "dana@example.com"),
                              committers=("Robin Reviewer", "robin@example.com"),
                              signer="dana@example.com")


# The punctuation class, as a CLASS rather than as the characters that were
# reported. Three repairs in this family were an enumerated strip set
# (`.,;:'"!?` was the last one) and each lost to the first character nobody
# listed: every decoration below left the approver's words structurally
# different from the author's own, and the certifying comparison consumed that
# structural difference as PROOF a second person approved a money change. The
# rule replacing the list: a word is split by Unicode CLASS, so punctuation and
# symbols are decoration wherever they sit and letters, marks and digits are
# the identity. These cases pin the class, including characters nobody has
# typed at this gate yet.
@case("square-brackets-do-not-make-a-second-person", "sig", "FAIL")
def c16p1(root):
    return _approval_with_sig(root, "G", approver="[Dana Author]")


@case("braces-do-not-make-a-second-person", "sig", "FAIL")
def c16p2(root):
    return _approval_with_sig(root, "G", approver="{Dana Author}")


@case("asterisk-emphasis-does-not-make-a-second-person", "sig", "FAIL")
def c16p3(root):
    # What a commit message written in a markdown-shaped editor produces.
    return _approval_with_sig(root, "G", approver="**Dana Author**")


@case("underscore-emphasis-does-not-make-a-second-person", "sig", "FAIL")
def c16p4(root):
    # An underscore is Pc, connector punctuation: a different Unicode class
    # from the brackets above and from the hyphen below, which is why the
    # rule is "category P or S", not a list of the three that were reported.
    return _approval_with_sig(root, "G", approver="_Dana Author_")


@case("slashes-do-not-make-a-second-person", "sig", "FAIL")
def c16p5(root):
    return _approval_with_sig(root, "G", approver="/Dana Author/")


@case("backticks-do-not-make-a-second-person", "sig", "FAIL")
def c16p6(root):
    # A backtick is Sk, a modifier SYMBOL rather than punctuation, so a rule
    # naming only the punctuation categories would still lose to it.
    return _approval_with_sig(root, "G", approver="`Dana Author`")


@case("a-leading-hyphen-does-not-make-a-second-person", "sig", "FAIL")
def c16p7(root):
    # One character, at one edge, was the whole exploit.
    return _approval_with_sig(root, "G", approver="-Dana Author")


@case("a-trailing-hyphen-does-not-make-a-second-person", "sig", "FAIL")
def c16p8(root):
    return _approval_with_sig(root, "G", approver="Dana Author-")


@case("a-mathematical-symbol-does-not-make-a-second-person", "sig", "FAIL")
def c16p9(root):
    # U+2211 N-ARY SUMMATION, category Sm: nobody has typed this at this gate,
    # which is the point of pinning the class instead of the report.
    return _approval_with_sig(root, "G", approver="∑Dana Author")


@case("a-non-ascii-punctuation-mark-does-not-make-a-second-person", "sig", "FAIL")
def c16p10(root):
    # U+3010 and U+3011, CJK lenticular brackets, and U+060C, the Arabic
    # comma: punctuation from blocks outside ASCII entirely.
    return _approval_with_sig(root, "G", approver="【Dana، Author】")


@case("a-decorated-author-identity-is-still-the-author", "sig", "FAIL")
def c16p11(root):
    # The same defect from the side the committer controls: git's user.name is
    # written by whoever runs the commit, so an agent decorates its OWN
    # identity and writes a clean-looking trailer that a human reviewer reads
    # as normal. Both sides reduce through the same function, so both sides
    # read as the same words.
    return _approval_with_sig(root, "G", approver="Dana Author",
                              authors=("[Dana Author]", "dana@example.com"))


@case("a-decorated-author-identity-is-still-the-author-with-emphasis", "sig", "FAIL")
def c16p12(root):
    return _approval_with_sig(root, "G", approver="Dana Author",
                              authors=("**Dana Author**", "dana@example.com"))


def _approval_reason(root, **kw):
    """The approval verdict AND its sentence, for cases where two different
    FAILs mean opposite things: refusing a person's name is not the same event
    as recognising them as the commit's own author."""
    original = _gate.git_trailers
    approver = kw.get("approver", "Someone")
    authors = list(kw.get("authors", ("Dana Author", "dana@example.com")))
    meta = {"author": authors, "committer": authors, "signer": ""}
    _gate.git_trailers = lambda r: ("change\n\nApproved-by: %s" % approver, "G", authors, meta)
    try:
        return _gate.gate_approval(root)
    finally:
        _gate.git_trailers = original


@case("a-case-folded-dotted-capital-is-read-as-the-author-not-as-a-second-person",
      "sig", "self-approval")
def c16p12b(root):
    # `casefold("İ")` is `i` plus U+0307 COMBINING DOT ABOVE, which NFKC
    # cannot recompose, so `Irmak Yilmaz` and `İrmak Yilmaz` are five and six
    # characters and the certifying comparison decided it on LENGTH alone,
    # calling one Turkish person two people.
    #
    # STATED HONESTLY, because it is the difference between a closed defect
    # and a hardening: the gate did not ship that verdict. The self-approval
    # test runs first and reduces through skeleton(), which drops marks, so
    # both spellings were already one identity there and the commit already
    # FAILED. What was wrong was that the two paths disagreed, and a
    # certifying comparison that decides by counting characters a reader does
    # not see is one refactor away from being the path that answers. This
    # pins the verdict and the reason for it.
    verdict, why = _approval_reason(root, approver="Irmak Yilmaz",
                                    authors=("İrmak Yilmaz", "irmak@example.com"))
    if verdict != "FAIL":
        return "%s: %s" % (verdict, why[:100])
    return "self-approval" if "identity that wrote the commit" in why else why[:110]


@case("an-apostrophe-unicode-recommends-does-not-refuse-an-honest-name",
      "sig", "self-approval")
def c16p12c(root):
    # The direction nobody probes: an honest person who cannot get a verdict.
    # U+02BC MODIFIER LETTER APOSTROPHE is category Lm, so it counted as a
    # script of its own and `OʼBrien` was a word that "mixes letters from more
    # than one script (LATIN, MODIFIER)". The money gate FAILED an honest
    # approver BY NAME, over the apostrophe Unicode recommends for names and
    # this platform types by default. A control that rejects a person's own
    # spelling of their own name teaches them to misspell it, which is how a
    # gate gets worked around instead of used.
    #
    # Both FAILs are FAIL, which is why this reads the sentence: the wrong one
    # says the name is unreadable, the right one says these two spellings are
    # the same person, which is exactly what they are here.
    verdict, why = _approval_reason(root, approver="OʼBrien Sean",
                                    authors=("O'Brien Sean", "sean@example.com"))
    if "mixes letters from more than one script" in why:
        return "an honest name was refused as a forgery shape"
    if verdict != "FAIL":
        return "%s: %s" % (verdict, why[:100])
    return "self-approval" if "identity that wrote the commit" in why else why[:110]


@case("an-honest-modifier-apostrophe-approver-is-not-refused-by-name", "sig", "PASS")
def c16p12e(root):
    # The same character on an approver who really is a second person: the
    # rule above must not have bought its result by refusing to look. This
    # PASSes, which is a certificate, so it is the strongest control here.
    return _approval_with_sig(root, "G", approver="OʼBrien Sean",
                              authors=("Dana Author", "dana@example.com"))


@case("two-different-people-still-certify-after-the-mark-and-modifier-rules", "sig", "PASS")
def c16p12d(root):
    # The control for both cases above: the rules that stopped two false
    # verdicts must not have stopped the true one. Two unrelated names, one
    # of them carrying the very characters those rules are about.
    return _approval_with_sig(root, "G", approver="İrmak Yılmaz",
                              authors=("Robin Reviewer", "robin@example.com"))


@case("an-unassigned-code-point-cannot-prove-two-people", "sig", "NO-DATA")
def c16p13(root):
    # The rule one layer deeper than the class split, and the reason this
    # family closes here: the certificate may not rest on the REDUCER'S
    # coverage either. U+0378 is unassigned in this build of Unicode, which
    # is exactly how a character added to Unicode after this code was written
    # appears from here. The host cannot class it as letter, mark, digit or
    # separator, so what it renders as is undecidable, and undecidable is
    # NO-DATA naming the code point. Absence of understanding is not proof of
    # difference.
    return _approval_with_sig(root, "G", approver="Dana Auth\u0378or")


@case("a-private-use-code-point-cannot-prove-two-people", "sig", "NO-DATA")
def c16p14(root):
    # The sibling: a private-use code point renders as whatever the reader's
    # font vendor decided, so this host can say nothing about it either.
    return _approval_with_sig(root, "G", approver="Dana Author\ue000")


@case("an-irish-name-with-an-apostrophe-still-passes", "sig", "PASS")
def c16p15(root):
    # The control the class rule must not break: names with legitimate
    # internal punctuation stay readable and stay certifiable. Hardening the
    # comparison must never teach an honest approver to spell their own name
    # wrong, and must never read every colleague as the author.
    return _approval_with_sig(root, "G", approver="Siobhan O'Brien")


@case("a-hyphenated-french-name-still-passes", "sig", "PASS")
def c16p16(root):
    return _approval_with_sig(root, "G", approver="Jean-Luc Picard")


@case("an-italian-name-with-an-apostrophe-still-passes", "sig", "PASS")
def c16p17(root):
    return _approval_with_sig(root, "G", approver="Marco D'Angelo")


@case("a-hyphenated-given-name-still-passes", "sig", "PASS")
def c16p18(root):
    return _approval_with_sig(root, "G", approver="Anna-Maria Rossi")


@case("a-decorated-honest-approver-still-passes", "sig", "PASS")
def c16p19(root):
    # Decoration removes the disguise, not the person: a real second party
    # whose name arrived wrapped in brackets is still a real second party.
    return _approval_with_sig(root, "G", approver="[Robin Reviewer]")


@case("a-lisu-spelling-of-tbd-is-not-a-snapshot-pin", "numbers", "FAIL")
def c14lisu(root):
    # The snapshot_id renders as TBD (Lisu letterforms are Latin capitals)
    # while comparing as nothing any fold can read. A value that could
    # render as a placeholder under look-alike substitution is refused as an
    # answer, so the gate cannot print "pinned to a snapshot" over it.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "ꓔꓐꓓ",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("an-unfoldable-letter-in-an-honest-snapshot-id-is-still-an-answer", "numbers", "PASS")
def c14thorn(root):
    # The control for the case above: a snapshot id carrying thorn is proven
    # different from every placeholder token by its readable letters, so the
    # honest value stays an answer. The round-11 residue backstop read any
    # value with an unfoldable Latin letter as recording nothing, which
    # refused ordinary Icelandic, Danish and French words.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "Þórshöfn-warehouse-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("an-invisible-character-is-not-an-answer-and-not-a-derivation", "numbers", "FAIL")
def c14inv(root):
    # snapshot_id is TODO plus a zero-width space; the second derivation is the
    # primary query plus one soft hyphen, rendering identically to it. Both
    # cleared the gate while its sentence asserted a pinned, independently
    # re-derived figure.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "TODO\u200b",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(amount) FROM orders\u00ad",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-small-capital-placeholder-is-not-a-pin", "numbers", "FAIL")
def c14sc(root):
    # snapshot_id is TODO spelled in the Latin small-capital block: present,
    # non-empty, renders as TODO on every screen, survives NFKC/NFKD and the
    # confusable table (the block has neither a decomposition nor a row). The
    # sixth disguise of one placeholder. The Unicode-name fold reads it as the
    # word it renders as, and the gate refuses the pin; a Latin value the fold
    # cannot reduce at all is refused by the residue rule the same way.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "ᴛᴏᴅᴏ",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-malformed-digit-grouping-is-not-a-number", "evidence", "refused")
def ev_num1(root):
    # "1,2,3" is not a number in any locale and read as 123; "17,5,70" read as
    # 17570, the worked example's own GMV, so a mistyped row count became the
    # figure it was supposed to be compared against; and float() accepts
    # non-ASCII digits in a repository that is ASCII by rule.
    got = [_checks.numeric(v) for v in ("1,2,3", "17,5,70", "\u0661\u0662\u0663")]
    return "refused" if got == [None, None, None] else "read %r" % (got,)


@case("honest-groupings-and-plain-numbers-still-read", "evidence", "ok")
def ev_num2(root):
    # The control: hardening the shape rule must not reject the spreadsheet
    # export the docstring defends.
    ok = (_checks.numeric("17,570") == 17570 and _checks.numeric("17570") == 17570
          and _checks.numeric("$1,234.56") == 1234.56 and _checks.numeric("50%") == 50
          and _checks.numeric("-3.5") == -3.5)
    return "ok" if ok else "regressed"


@case("a-receipt-field-cannot-write-verdict-lines-for-the-other-gates", "gaterun", "one line each")
def ev_forge1(root):
    # A figure label carrying newlines used to be interpolated into the report
    # verbatim, so a directory with no APPROVAL file printed "approval PASS
    # signed commit carries ..." because a label said so, and the harness's own
    # first-match verdict reader read the forged line. Evidence is now
    # flattened at every print site: one check name, one line, and the line
    # breaks arrive as a visible two-character escape.
    forged = ("gmv\n"
              "  approval  PASS     signed commit carries Approved-by: Robin Reviewer, this host "
              "verified the signature against a trusted key, and that identity is not the commit's "
              "author or committer (Dana Author)\n"
              "  ran       PASS     3 recorded check(s), each with a zero exit and a nonzero "
              "duration: no snapshot_id recorded (None) [severity: gate]\n"
              "STRICT: 0 hard gate(s) failed")
    write(root, "numbers-manifest.json", {"figures": [{
        "label": forged, "snapshot_id": "",
        "query": "SELECT 1", "second_derivation": "SELECT 2",
        "rerun": {"ran": True, "primary": 1, "secondary": 1}}]})
    out = subprocess.run([sys.executable, GATE, root], capture_output=True, text=True)
    lines = out.stdout.splitlines()
    for name in ("numbers", "migration", "approval", "ran"):
        n = sum(1 for l in lines if l.split()[:1] == [name])
        if n != 1:
            return "%d lines whose first token is %s" % (n, name)
    approval = next(l for l in lines if l.split()[:1] == ["approval"])
    if approval.split()[1] != "NO-DATA":
        return "approval read as %s" % approval.split()[1]
    if any(l.startswith("STRICT:") for l in lines):
        return "a receipt field forged the strict summary line"
    return "one line each"


@case("an-invisible-or-fullwidth-placeholder-records-no-answer", "evidence", "refused")
def ev_inv1(root):
    vals = ["TODO\u200b", "T\u00adODO", "\uff34\uff2f\uff24\uff2f", "unknown\u200d"]
    got = [_checks.answered(v) for v in vals]
    return "refused" if got == [None] * len(vals) else "read %r" % (got,)


@case("a-dressed-up-placeholder-records-no-answer", "evidence", "refused")
def ev_shape1(root):
    # The four spellings that cleared the money gate with its strongest
    # sentence, plus two the fix's own lists never named ({fixme}, a combining
    # mark on TBD): the vacuity test now folds SHAPES (wrapping, a trailing
    # owner, dotted initialisms, marks and confusables) to a fixpoint, so a
    # variant nobody has typed yet folds through the same rules instead of
    # needing a new list row.
    vals = ["[TBD]", "<TODO>", "TODO(dana)", "t.b.d.", "{fixme}", "TBD\u0301"]
    got = [_checks.answered(v) for v in vals]
    if got != [None] * len(vals):
        return "read %r" % (got,)
    # The control: honest values wearing the same punctuation stay answers.
    honest = ["snap-2026-07", "job-8842 (rerun)", "(see runbook 7)", "$100"]
    kept = [_checks.answered(v) for v in honest]
    return "refused" if all(k is not None for k in kept) else "refused honest %r" % (kept,)


@case("a-real-name-in-any-script-records-an-answer", "evidence", "read as names")
def ev_realnames(root):
    """Real people's names are not notes to self, in any script.

    The defect, measured at 6 of the 240 names in the shipped corpus below:
    the vacuity backstop asked could_render_same(trust_fold=True), whose
    opaque branches answer True for every character no fold here can read, so
    once the fold read nothing the comparison had only LENGTH left and a
    genuine name reducing to 9, 11 or 13 characters "was" `not known`, `not
    decided` or `to be decided`. `answered()` returned None for a real Hindi
    name and a real Georgian one, and 29 call sites ask `answered()` before
    quoting a field, so the accusation reached every one of them.

    This drives the SHARED predicates rather than the 29 sites, because the
    sites all reach the defect through them and a per-site test would prove
    only that today's sites are remembered. The corpus is the one the
    published refusal table is derived from, so the eval and the page measure
    the same names.
    """
    sys.path.insert(0, os.path.join(_REPO, "scripts"))
    try:
        import derive_refusal_table as _derive
    except Exception as e:                       # noqa: BLE001 (reported, not raised)
        return "the shipped name corpus could not be imported (%s), so no name was read" % e
    names = [(s, n) for s, pools in _derive.POOLS.items() for pool in pools for n in pool]
    if len(names) < 200:
        return ("the corpus holds %d name(s), too few to measure a rate this defect "
                "showed at 6 in 240" % len(names))
    refused = []
    for script, name in names:
        # Every shared entry point a call site can reach the backstop through.
        if (_checks.answered(name) is None
                or _checks.vacuous(name)
                or _checks.domain_vacuous(name)
                or _checks.answered_as(name, lambda v: v) is None):
            refused.append("%s %s" % (script, name))
    if refused:
        return "%d of %d real name(s) called placeholders: %s" % (
            len(refused), len(names), ", ".join(refused[:6]))
    # The control, in the same case, because a backstop that accuses nobody is
    # not a fix: the disguises it exists for must still be refused, including
    # the non-Latin one (a Lisu spelling of TODO renders as TODO to a reader).
    missed = [v for v in ("ꓔꓳꓓꓳ", "ᴛᴏᴅᴏ",
                          "TODO", "[TBD]", "t.b.d.", "ＴＯＤＯ",
                          "to be decided", "#")
              if _checks.answered(v) is not None]
    return "read as names" if not missed else "disguised placeholders read as answers: %r" % missed


@case("a-bracketed-placeholder-is-not-a-pin", "numbers", "FAIL")
def ev_shape2(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "[TBD]",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-container-snapshot-id-pins-nothing", "numbers", "FAIL")
def ev_shape3(root):
    # `snapshot_id: true` was closed a round ago; the sibling container shapes
    # were not, and the vacuity test is defined only on strings, so a dict or a
    # list carrying a placeholder was a pinned snapshot. The migration gate
    # already refuses a dict where a run id belongs; this is the same type rule
    # at the numbers gate's identifier.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": {"note": "TODO"},
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-list-snapshot-id-pins-nothing", "numbers", "FAIL")
def ev_shape4(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": ["TODO"],
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("the-gate-examines-the-directory-it-was-named-not-the-git-toplevel", "gaterun", "NO-DATA")
def ev_reroot1(root):
    # Inside any git worktree the operator's directory argument was silently
    # replaced by `git rev-parse --show-toplevel`, so an EMPTY directory named
    # on the command line got the numbers gate's strongest sentence, earned by
    # a manifest somebody else recorded in a sibling change directory, and the
    # report named neither the directory actually read nor the substitution.
    git_init(root)
    write(root, "changeA/numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
    os.makedirs(os.path.join(root, "changeB"), exist_ok=True)
    out = subprocess.run([sys.executable, GATE, "numbers", os.path.join(root, "changeB")],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts[:1] == ["numbers"]:
            return parts[1]
    return "no verdict line"


@case("a-receipt-field-cannot-move-the-cursor-in-the-rendered-report", "gaterun", "neutralized")
def ev_forge2(root):
    # The round-9 closure flattened line BREAKS and left the artifact the
    # ability to move the cursor: `ESC [ 2K` erases the rendered line and
    # `ESC [ 1F` rewrites the one above, so a figure label forged whole verdict
    # lines on any VT100-family terminal while the byte stream still held one
    # line per gate and the line-count assertion beside this one stayed green.
    # one_line() now escapes the whole control class (Cc and Cf), so the probe
    # asserts no control byte of any kind survives into the report, including
    # the 7-bit NEL (ESC E) and backspace, which no list in the fix names.
    label = ("gmv\x1b[2K\x1b[1Fapproval  PASS     signed commit carries Approved-by: "
             "Robin Reviewer [severity: gate]\x1bE\x08\u202egmv")
    write(root, "numbers-manifest.json", {"figures": [{
        "label": label, "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
    out = subprocess.run([sys.executable, GATE, root], capture_output=True, text=True)
    bad = sorted(set(ch for ch in out.stdout
                     if ch != "\n" and (ord(ch) < 32 or ord(ch) == 127 or ch == "\u202e")))
    if bad:
        return "control byte(s) reached the report: %r" % bad
    lines = out.stdout.splitlines()
    for name in ("numbers", "migration", "approval", "ran"):
        if sum(1 for l in lines if l.split()[:1] == [name]) != 1:
            return "line count moved for %s" % name
    return "neutralized"


def _check_with_exemption(key):
    return _checks.Check(lambda r: ("NO-DATA", "x"), reads=("x.json",), kind="json",
                         severity="soft",
                         full_fixture={"files": {"x.json": {"a": "b"}}},
                         optional_leaves={key: "a stated reason long enough to clear the floor"})


@case("a-scenario-id-prefix-is-not-an-exemption-key", "guard", "refused")
def gd_exempt1(root):
    # "seeded" is the prefix every seeded scenario id shares, so as an
    # exemption key it waived the whole generative mode for a check while the
    # summary counts never moved. A key now has to name a leaf or section of
    # the check's own fixture, so the id space outside the fixture is
    # unreachable.
    for key in ("seeded", "access", "full", "totally-free-form"):
        try:
            _check_with_exemption(key)
            return "constructed with key %r" % key
        except ValueError as e:
            if "exemption key" not in str(e):
                return "wrong error for %r: %s" % (key, e)
    return "refused"


@case("a-fixture-leaf-key-still-constructs", "guard", "ok")
def gd_exempt2(root):
    # The control: the structural rule must not refuse the shape the shipped
    # registries actually use.
    try:
        _check_with_exemption("x.json::a")
    except ValueError as e:
        return "refused the honest shape: %s" % e
    return "ok"


@case("an-exemption-key-naming-no-fixture-leaf-is-refused", "guard", "refused")
def gd_exempt_leaf(root):
    # The prefix check passed the FILE half and left the leaf half unvalidated,
    # so a constructor-legal key could name a leaf the fixture does not have,
    # and where a fixture file shared a name with a derived scenario namespace
    # one key waived a whole axis. The WHOLE key resolves now: a `::` leaf must
    # be a leaf or subtree of that file's JSON.
    for key in ("x.json::nonexistent", "x.json::a.deeper.path", "x.json::*"):
        try:
            _check_with_exemption(key)
            return "constructed with key %r naming no fixture leaf" % key
        except ValueError as e:
            if "not a leaf or subtree" not in str(e):
                return "wrong error for %r: %s" % (key, e)
    # The control: a real subtree path still constructs.
    try:
        _checks.Check(lambda r: ("NO-DATA", "x"), reads=("x.json",), kind="json", severity="soft",
                      full_fixture={"files": {"x.json": {"a": {"b": 1}}}},
                      optional_leaves={"x.json::a/*": "a stated reason long enough to clear the floor"})
    except ValueError as e:
        return "refused an honest subtree key: %s" % e
    return "refused"


@case("an-access-scenario-cannot-be-waived-by-any-key", "guard", "unwaivable")
def gd_exempt_axis(root):
    # The derived scenario-id namespace and the fixture-filename namespace were
    # the same string space, so a check whose fixture held a file named
    # `access` could write a legal key that matched the whole ACCESS axis under
    # the cap. Exemptibility is structural now: legacy, access and
    # declared-reads scenarios are non-exemptible whatever a key spells,
    # because "no PASS over evidence this tool could not open" is not a
    # sentence any exemption may narrow.
    meta = SourceFileLoader("tndc_axis", os.path.join(HERE, "test_no_data_class.py")).load_module()
    tool = meta.ADAPTERS[("sbe_gate.py", "GATES")]
    check = _gate.GATES["numbers"]
    for maker in (meta.legacy_cases, meta.access_cases):
        for sc in maker(tool, check):
            if sc.exemptible:
                return "a %s scenario (%s) is marked exemptible" % (sc.label, sc.sid)
    # And a hollowing scenario IS exemptible, so the flag is not constant-false.
    if not any(sc.exemptible for sc in meta.hollow_cases(tool, check)):
        return "no hollowing scenario is exemptible, so the flag means nothing"
    return "unwaivable"


@case("one-exemption-key-cannot-waive-a-whole-leaf-sweep", "guard", "capped")
def gd_exempt3(root):
    # The demonstrated bypass: a legal-format key naming one JSON leaf of the
    # numbers fixture matched the leaf's entire value sweep, and one such key
    # hid a real regression on that leaf behind an unchanged scenario total.
    # The meta-test now caps waived SCENARIOS per check; this eval rebuilds the
    # numbers check with exactly that key and asserts its sweep breadth is
    # over the cap, so the meta-test would fail it by count.
    meta = SourceFileLoader("tndc_guard", os.path.join(HERE, "test_no_data_class.py")).load_module()
    fx = _gate.GATES["numbers"].full_fixture
    clone = _checks.Check(_gate.gate_numbers, reads=(_gate.MANIFEST,), kind="json",
                          severity="gate", item_key="figures", full_fixture=fx,
                          optional_leaves={
                              "numbers-manifest.json::figures[0].snapshot_id":
                                  "a plausible-sounding sentence shaped like the shipped ones"})
    tool = meta.ADAPTERS[("sbe_gate.py", "GATES")]
    scs = (meta.legacy_cases(tool, clone) + meta.hollow_cases(tool, clone)
           + meta.access_cases(tool, clone))
    matched = sum(1 for sc in scs if clone.optional_leaves.get(sc.sid.split("|")[0]))
    if matched <= meta.WAIVED_SCENARIO_CAP:
        return ("a leaf key matches only %d scenario(s), inside the cap of %d, so the cap "
                "no longer bites" % (matched, meta.WAIVED_SCENARIO_CAP))
    return "capped"


@case("a-dead-waiver-that-excuses-nothing-is-a-meta-test-failure", "guard", "flagged dead")
def gd_dead_waiver(root):
    # Two of the four declared waivers shielded nothing: the placeholder
    # exemption's own scenarios FAILed anyway, so it excused no PASS while the
    # summary counted it. A waiver is alive (it excuses a real PASS) or it is
    # red, because a waiver that shields nothing is a pre-approved mark waiting
    # for a regression to hide under. This rebuilds the diagrams check with a
    # legal key on a leaf whose sweep never needs excusing (a booleanish leaf
    # that FAILs on any hollowing) and asserts the meta-test flags it dead.
    meta = SourceFileLoader("tndc_dead", os.path.join(HERE, "test_no_data_class.py")).load_module()
    fx = _design_mod.CHECKS["diagrams"].full_fixture
    # 05-data-model.md##Entities: hollowing the Entities section makes the
    # diagram untraceable, so every matched scenario FAILs and the waiver
    # excuses nothing.
    clone = _checks.Check(_design_mod.check_diagrams,
                          reads=_design_mod.CHECKS["diagrams"].reads, kind="text",
                          severity="gate", empty_expect="FAIL",
                          empty_note="x",
                          full_fixture=fx,
                          optional_leaves={"05-data-model.md##Entities":
                                           "a reason long enough to clear the reviewability floor"})
    tool = meta.ADAPTERS[("sbe_design.py", "CHECKS")]
    scs = (meta.legacy_cases(tool, clone) + meta.hollow_cases(tool, clone)
           + meta.access_cases(tool, clone))
    excused = 0
    for sc in scs:
        if not (sc.exemptible and clone.optional_leaves.get(sc.sid.split("|")[0])):
            continue
        import tempfile as _tf
        with _tf.TemporaryDirectory() as d:
            for rel, content in meta.subst(sc.files, d).items():
                tool.place(d, rel, content)
            if sc.setup:
                sc.setup(d)
            out = tool.invoke(d, "diagrams", meta.subst(sc.env, d))
            meta.restore_access(d)
        got, _ = meta.verdict_and_evidence(out.stdout, "diagrams")
        if got == "PASS":
            excused += 1
    return "flagged dead" if excused == 0 else "the waiver excused %d PASS(es), so it is alive" % excused


@case("a-module-planted-in-a-bytecode-cache-is-refused-not-executed", "guard", "refused unrun")
def gd_planted(root):
    # A .py under tools/__pycache__/ was imported and EXECUTED by the module
    # walk, counted in "N module(s)", and reported zero failures, while the
    # install verifier's exclusion list kept it invisible on disk. The walk
    # now refuses anything inside a bytecode cache (the interpreter owns that
    # directory name), names it as a failure, and never imports it.
    import shutil
    for rel in ("tools", "evals"):
        shutil.copytree(os.path.join(_REPO, rel), os.path.join(root, rel))
    cache = os.path.join(root, "tools", "__pycache__")
    os.makedirs(cache, exist_ok=True)
    marker = os.path.join(root, "PLANTED-RAN")
    with open(os.path.join(cache, "planted.py"), "w") as f:
        f.write("open(%r, 'w').write('this code executed')\n" % marker)
    meta = SourceFileLoader("tndc_planted",
                            os.path.join(root, "evals", "test_no_data_class.py")).load_module()
    sources = meta.tool_sources()
    if os.path.exists(marker):
        return "the planted module was executed"
    if any("__pycache__" in s for s in sources):
        return "the planted module was walked as source"
    if not any("planted.py" in p for p in meta.PLANTED_SOURCE):
        return "the planted module was skipped in silence, not named"
    return "refused unrun"


SCORE_TOOL = os.path.join(HERE, "..", "tools", "sbe_score.py")
TELEMETRY_TOOL = os.path.join(HERE, "..", "tools", "sbe_telemetry.py")
_design_mod = SourceFileLoader("sbe_design_eval",
                               os.path.join(HERE, "..", "tools", "sbe_design.py")).load_module()

# Every arrow form the Mermaid dialects ship, one fixture each: the identifier
# grammar was greedy through a trailing hyphen, so every two-dash arrow minted
# a phantom `Name-` node (the dashed reply `-->>`, the default idiom in every
# tutorial, FAILed honest work), and the unmatched activation suffix made
# `B-->>-A:` parse as no message at all (an undeclared service in one passed
# "all traceable").
ARROW_FORMS = {
    "solid ->": "Alpha->Beta: x", "solid ->>": "Alpha->>Beta: x",
    "dashed -->": "Alpha-->Beta: x", "dashed reply -->>": "Beta-->>Alpha: x",
    "activation ->>+": "Alpha->>+Beta: x", "deactivation -->>-": "Beta-->>-Alpha: x",
    "async -)": "Alpha-)Beta: x", "async dashed --)": "Alpha--)Beta: x",
    "cross -x": "Alpha-xBeta: x", "cross dashed --x": "Alpha--xBeta: x",
}


@case("every-sequence-arrow-form-reads-both-ends-and-mints-no-phantom", "evidence", "all clean")
def ev_arrows(root):
    for label, line in sorted(ARROW_FORMS.items()):
        body = ("x\n```mermaid\nsequenceDiagram\n  participant Alpha\n  participant Beta\n"
                "  %s\n```\n" % line)
        nodes, _kinds, _skipped, _states, _unread = _design_mod._diagram_nodes(body)
        if sorted(nodes) != ["Alpha", "Beta"]:
            return "%s -> %s" % (label, sorted(nodes))
    for label, block in (
            ("flowchart open ---", "flowchart LR\n  Alpha---Beta"),
            ("flowchart -->", "flowchart LR\n  Alpha-->Beta"),
            ("state -->", "stateDiagram-v2\n  Alpha --> Beta"),
            ("class --|>", "classDiagram\n  Alpha --|> Beta"),
            ("er ||--o{", "erDiagram\n  Alpha ||--o{ Beta : has")):
        nodes, _kinds, _skipped, _states, _unread = _design_mod._diagram_nodes(
            "x\n```mermaid\n%s\n```\n" % block)
        if sorted(nodes) != ["Alpha", "Beta"]:
            return "%s -> %s" % (label, sorted(nodes))
    return "all clean"


@case("the-default-dashed-reply-arrow-is-not-an-orphan", "diagrams", "PASS")
def ar1(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Producer: system of record: the queue.\n"
          "- Worker: system of record: the scheduler.\n"
          "## Relationships\n- Producer to Worker: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Delivery\n```mermaid\nsequenceDiagram\n"
          "  participant Producer\n  participant Worker\n"
          "  Producer->>Worker: deliver\n  Worker-->>Producer: 2xx or error\n```\n")


@case("a-ghost-service-in-a-deactivation-reply-is-caught", "diagrams", "FAIL")
def ar2(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to Customer: one-to-one, self.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n"
          "  Customer->>+Customer: self check\n"
          "  Customer-->>-totally-undeclared-service: reply lands on a ghost\n```\n")


@case("a-japanese-data-model-is-a-data-model", "datamodel", "PASS")
def uni1(root):
    # Every name grammar required an ASCII first letter while its continuation
    # class was Unicode-wide, so a dossier whose ubiquitous language is not
    # ASCII-first FAILed with a message instructing the author to do exactly
    # what they had done. A name starts at any letter, by property.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- 注文: system of record: the order service.\n"
          "- 注文明細: system of record: the order service.\n"
          "## Relationships\n- 注文 to 注文明細: one-to-many.\n")


@case("a-japanese-diagram-traces-to-japanese-entities", "diagrams", "PASS")
def uni2(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- 注文: system of record: the order service.\n"
          "- 注文明細: system of record: the order service.\n"
          "## Relationships\n- 注文 to 注文明細: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nerDiagram\n  注文 ||--o{ 注文明細 : 含む\n```\n")


@case("a-non-latin-ghost-node-is-an-orphan-not-a-silent-drop", "diagrams", "FAIL")
def uni3(root):
    # The 9a-C6 shape: a node in a non-Latin script was dropped by the ASCII
    # grammar and the check printed "all traceable" over what was left, with
    # the dropped token absent even from the disclosure channel.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Order: system of record: the OMS.\n"
          "- Payment: system of record: the ledger.\n"
          "## Relationships\n- Order to Payment: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nerDiagram\n  Order ||--o{ Payment : has\n"
          "  幽霊サービス ||--o{ Order : haunts\n```\n")


@case("an-accented-ghost-in-a-sequence-message-is-caught", "diagrams", "FAIL")
def uni4(root):
    # The 9b-2 shape: _SEQ_MESSAGE anchored on an ASCII sender, so a message
    # line whose first identifier starts with a non-ASCII letter was skipped
    # WHOLE, ghost included, and "all traceable" printed over the remainder.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Journal: system of record: the ledger.\n"
          "- Écriture: system of record: the ledger.\n"
          "## Relationships\n- Journal to Écriture: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n  participant Écriture\n"
          "  Écriture->>règlement-fantôme: not declared anywhere\n```\n")


@case("an-accented-entity-is-counted-not-silently-dropped", "evidence", "counted")
def uni5(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Journal: system of record: the ledger.\n"
          "- Écriture: system of record: the ledger.\n"
          "## Relationships\n- Journal to Écriture: one-to-many.\n")
    line = gate_line(root, "datamodel")
    if line.split()[1:2] != ["PASS"]:
        return line
    return "counted" if "2 entities" in line else line


@case("create-participant-keeps-its-alias-and-traces", "diagrams", "PASS")
def uni6(root):
    # Documented Mermaid syntax (v10.3+): `create participant A as
    # audit-worker` lost the alias, so A could not trace to the declared
    # component and an honest diagram FAILed naming an id the author never
    # meant to trace, with create and destroy dropped in silence.
    write(root, "04-technology-map.md",
          "# Technology map\n## Components\n| Component | Runtime |\n| --- | --- |\n"
          "| gateway | Go |\n| audit-worker | Python |\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n  participant G as gateway\n"
          "  create participant A as audit-worker\n  G->>A: audit this\n  destroy A\n```\n")


@case("the-skipped-list-names-canonical-note-and-destroy", "evidence", "named")
def uni7(root):
    # Mermaid's docs write `Note over G,W: text` capitalized; the statement
    # set held lowercase members and membership did not fold, so the canonical
    # spelling vanished from the skipped list the docstring promises is
    # complete, and so did create/destroy.
    body = ("x\n```mermaid\nsequenceDiagram\n  participant G\n  participant W\n"
            "  G->>W: go\n  Note over G,W: settled\n  destroy W\n```\n")
    _nodes, _kinds, skipped, _states, unread = _design_mod._diagram_nodes(body)
    if unread:
        return "unread: %s" % unread
    named = "; ".join(skipped)
    if "Note (" not in named:
        return "Note dropped from the skipped list: %s" % named
    if "destroy (" not in named:
        return "destroy dropped from the skipped list: %s" % named
    return "named"


@case("an-ampersand-multi-edge-reads-every-member", "diagrams", "FAIL")
def uni8(root):
    # `A & B --> C & D` is documented flowchart syntax; only arrow-adjacent
    # identifiers were read, so a ghost in an outer position passed "all
    # traceable" over a false node count.
    write(root, "04-technology-map.md",
          "# Technology map\n## Components\n| Component | Runtime |\n| --- | --- |\n"
          "| gateway | Go |\n| ledger | Java |\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nflowchart LR\n"
          "  totally-undeclared-shadow & gateway --> ledger\n```\n")


@case("an-ampersand-ghost-as-last-destination-is-caught", "diagrams", "FAIL")
def uni9(root):
    write(root, "04-technology-map.md",
          "# Technology map\n## Components\n| Component | Runtime |\n| --- | --- |\n"
          "| gateway | Go |\n| ledger | Java |\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nflowchart LR\n"
          "  gateway --> ledger & totally-undeclared-shadow\n```\n")


@case("a-line-the-parser-cannot-read-refuses-all-traceable", "diagrams", "NO-DATA")
def uni10(root):
    # A dropped-in-silence line was how every ghost above traveled. What the
    # parser cannot read at all is now a confession in the verdict, and the
    # completeness sentence is refused: a node could be hiding in it.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Order: system of record: the OMS.\n"
          "## Relationships\n- Order to Order: one-to-one, self.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n  participant Order\n"
          "  Order->>Order: check\n  🧾->>Order: a sender no grammar reads\n```\n")


@case("a-faithful-madr-with-one-rejection-fails-the-floor", "adr", "FAIL")
def madr1(root):
    # MADR's Considered Options section includes the chosen option BY
    # DEFINITION, and every bullet under it was counted as a rejected
    # alternative: the two-alternatives floor was satisfiable by
    # chosen-plus-one, the exact single-alternative ADR it exists to refuse.
    # This fixture carries three re-entry routes at once, so the closure is
    # pinned at its boundaries and not at its center: the chosen name
    # is 66 characters (a 60-character collection cap once made it unequal to
    # itself), the chosen line WRAPS mid-name at prose width (a line-anchored
    # regex once compared its first line only), and a Pros and Cons table
    # restates both options shortened (each rendering once counted again).
    write(root, "03-adr.md",
          "# ADR\n## Context and Problem Statement\nRetried refunds double-settle.\n"
          "## Decision Drivers\nsettlement latency, audit trail\n"
          "## Considered Options\n"
          "- Append-only retry ledger keyed by an idempotency token per request\n"
          "- Nightly batch reconciliation: sweep duplicates after the fact.\n"
          "## Decision Outcome\nChosen option: \"Append-only retry ledger keyed by an "
          "idempotency token\nper request\", because it stops the double-settle before "
          "it lands.\n"
          "## Pros and Cons of the Options\n"
          "| Option | Verdict | Why |\n| --- | --- | --- |\n"
          "| Append-only retry ledger | chosen | every retry observable |\n"
          "| Nightly batch reconciliation | rejected | misses the daily deadline |\n"
          "## Consequences\nOne more key to mint and store.\n"
          "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("a-madr-with-two-real-rejections-passes-with-an-honest-count", "evidence", "counted")
def madr2(root):
    write(root, "03-adr.md",
          "# ADR\n## Context and Problem Statement\nRetried refunds double-settle.\n"
          "## Decision Drivers\nsettlement latency, audit trail\n"
          "## Considered Options\n- Idempotency key: dedupe retries at the boundary.\n"
          "- Nightly batch reconciliation: sweep duplicates after the fact.\n"
          "- Synchronous ledger call: ties checkout to ledger availability.\n"
          "### Pros and Cons of the Options\n"
          "#### Idempotency key\nGood, because it stops the double-settle early.\n"
          "#### Nightly batch reconciliation\nBad, because it misses the daily deadline.\n"
          "#### Synchronous ledger call\nBad, because it couples availability.\n"
          "## Decision Outcome\nChosen option: \"Idempotency key\", because it stops the "
          "double-settle before it lands.\n"
          "## Consequences\nOne more key to mint and store.\n"
          "## What would flip this\nSub-second settlement becoming a requirement.\n")
    line = gate_line(root, "adr")
    if line.split()[1:2] != ["PASS"]:
        return line
    if "2 distinct rejected alternatives" not in line:
        return "count wrong: %s" % line
    return "counted" if "counted as the decision" in line else "exclusion undisclosed: %s" % line


@case("a-table-rendering-of-a-rejection-is-one-rejection-not-two", "evidence", "counted")
def madr3(root):
    # The way people actually write a full MADR: the option's full name in the
    # Considered Options list, a SHORTENED name in the Pros and Cons table.
    # Each real rejection counted once per rendering, so an honest two-
    # rejection dossier printed "5 distinct rejected alternatives" (two
    # spellings each plus the truncation-escaped chosen bullet). A table row
    # whose name-words are a subset of a listed option's name-words is the
    # same option written twice; two options of the SAME list are never
    # merged, so honest neighbours survive.
    write(root, "03-adr.md",
          "# ADR\n## Context and Problem Statement\nPartner payouts double-settle on retry.\n"
          "## Decision Drivers\ncorrectness of money movement, audit trail\n"
          "## Considered Options\n"
          "- Append-only settlement ledger with an idempotency token per payout instruction\n"
          "- Synchronous ledger call into the partner bank API before acknowledging\n"
          "- Nightly batch reconciliation sweeping duplicate settlements after the fact\n"
          "## Decision Outcome\nChosen option: \"Append-only settlement ledger with an "
          "idempotency token\nper payout instruction\", because every retry becomes observable.\n"
          "## Pros and Cons of the Options\n"
          "| Option | Verdict | Why |\n| --- | --- | --- |\n"
          "| Append-only settlement ledger | chosen | retries observable, no lock |\n"
          "| Synchronous ledger call | rejected | blocks checkout on partner latency |\n"
          "| Nightly batch reconciliation | rejected | misses the settlement deadline |\n"
          "## Consequences\nOne more table to operate.\n"
          "## What would flip this\nA partner API that becomes idempotent on its own side.\n")
    line = gate_line(root, "adr")
    if line.split()[1:2] != ["PASS"]:
        return line
    if "2 distinct rejected alternatives" not in line:
        return "count wrong: %s" % line
    return "counted" if "counted as the decision" in line else "exclusion undisclosed: %s" % line


@case("a-gfm-table-without-leading-pipes-is-a-table", "datamodel", "PASS")
def gfm1(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "Entity | Meaning | System of record\n--- | --- | ---\n"
          "Customer | the buyer | the CRM\nOrder | a confirmed purchase | the order service\n"
          "\n## Relationships\n- Customer to Order: one-to-many.\n")


@case("a-setext-heading-is-a-heading", "datamodel", "PASS")
def setext1(root):
    write(root, "05-data-model.md",
          "Data model\n==========\n\nEntities\n--------\n"
          "- Customer: system of record: the CRM.\n- Order: system of record: the OMS.\n"
          "\nRelationships\n-------------\n- Customer to Order: one-to-many.\n")


@case("a-non-latin-sibling-is-a-named-script-limit-not-an-absence", "designrun", "disclosed")
def i6run(root):
    # 08-behaviour.md is required at T1 now, and its fixed ID | Starting point |
    # Trigger | Required outcome | Proof header always contributes real ASCII
    # words ("trigger", "proof"). Written in English, 08 would sit in the same
    # "theirs" set as the Japanese 02-process.md when 01's own coherence is checked, giving
    # 01 a THEIRS made of two scripts at once, which is not the two-artifact,
    # one-script-each shape this case means to pin (it falls through to the
    # generic "shares no named subject" FAIL instead of naming the script
    # limit). So 01 here carries no run of three or more word characters at
    # all (the same unsegmentable shape as
    # an-artifact-the-extractor-cannot-segment-is-nodata-not-pass): it
    # contributes nothing to any THEIRS set, which leaves 08's own coherence
    # check comparing itself against a THEIRS that is purely 02's Japanese,
    # the clean two-script comparison this case is actually pinning.
    write(root, "00-intake.json",
          {"tier": "T1", "answers": {"changes_contract": False, "crosses_boundary": True,
                                     "reversible_under_hour": True, "touches_sensitive": False,
                                     "consumers": "none"}})
    write(root, "01-purpose.md",
          "# Purpose\n目的 は 別 の 話 で あり この 設計 と は 関係 ない 事 を "
          "記す 熟成 の 手入 れ に つい て 記す\n")
    write(root, "02-process.md",
          "# Process\n返金の処理は毎日実行される。サポートデスクが結果を確認する。\n"
          "返金が失敗した場合は台帳に記録される。\n")
    write(root, "08-behaviour.md", BEHAVIOUR)
    out = subprocess.run([sys.executable, DESIGN, "artifacts", root],
                         capture_output=True, text=True)
    line = next((l for l in out.stdout.splitlines() if l.strip().startswith("artifacts")), "")
    if "nothing else to be coherent with" in line:
        return "still claims the sibling does not exist"
    if "different" not in line or "script" not in line:
        return "got: %s" % line.strip()[:140]
    return "disclosed"


def _score_lines(root, files):
    """Run the scorer against a throwaway vault holding `files` and return stdout."""
    for rel, content in files.items():
        if isinstance(content, list):
            # A list is JSONL here (one object per line), not a JSON array.
            content = "".join(json.dumps(row) + "\n" for row in content)
        write(root, rel, content)
    out = subprocess.run([sys.executable, SCORE_TOOL],
                         capture_output=True, text=True,
                         env=dict(os.environ, BROTHERSBE_VAULT=root,
                                  BROTHERSBE_REGISTRIES="", SBE_LINT_ROOT=""))
    return out.stdout


@case("migrate-preserves-the-line-it-cannot-parse-and-backs-up-bytes", "guard", "preserved")
def gd_migrate(root):
    # The loss guard compared two counts produced by the same lossy reader, so
    # it could never see the loss: the unparseable line vanished from the
    # ledger AND from the backup while the tool printed "count ok".
    led = "99-System/telemetry/outcomes.jsonl"
    original = ('{"schema":2,"session_id":"s1","ts":"2026-07-27T09:00:00Z"}\n'
                "{oops half a line\n"
                '{"schema":1,"session_id":"s2","out_tokens":10}\n')
    write(root, led, original)
    out = subprocess.run([sys.executable, TELEMETRY_TOOL, "migrate"],
                         capture_output=True, text=True,
                         env=dict(os.environ, BROTHERSBE_VAULT=root))
    body = open(os.path.join(root, led)).read()
    if "{oops half a line" not in body:
        return "the unparseable line was deleted from the ledger"
    backups = [f for f in os.listdir(os.path.dirname(os.path.join(root, led)))
               if ".bak-migrate" in f]
    if len(backups) != 1:
        return "no backup written"
    bak = open(os.path.join(root, "99-System/telemetry", backups[0])).read()
    if bak != original:
        return "the backup is not a byte copy of the original"
    if "could not read as a ledger row preserved verbatim" not in out.stdout:
        return "the preserved line is not named: %s" % out.stdout.strip()
    return "preserved"


@case("a-ledger-line-that-parses-and-is-not-a-row-is-named-not-silently-counted",
      "guard", "named")
def gd_nonobject_row(root):
    # `5` parses fine and is no ledger row: the disclosure channel was scoped
    # to json.loads failures, so migrate printed "1 migrated to schema 2,
    # count ok (2)", which reads as "the other line was already schema 2",
    # about an integer; and dedup swallowed an AttributeError and exited 0
    # with the duplicates still in the file. Unreadable is unreadable however
    # the line fails to be a row.
    tel = os.path.join(root, "99-System", "telemetry")
    os.makedirs(tel)
    row = ('{"session_id":"s1","ts":"2026-07-01T00:00:00Z","schema":1,"out_tokens":10}\n'
           '5\n')
    open(os.path.join(tel, "outcomes.jsonl"), "w").write(row)
    env = dict(os.environ, BROTHERSBE_VAULT=root)
    mig = subprocess.run([sys.executable, TELEMETRY_TOOL, "migrate"],
                         capture_output=True, text=True, env=env, timeout=120)
    if "could not read as a ledger row" not in mig.stdout:
        return "migrate did not name the non-object line: %s" % mig.stdout.strip()[:160]
    open(os.path.join(tel, "outcomes.jsonl"), "w").write(row + row)
    ded = subprocess.run([sys.executable, TELEMETRY_TOOL, "dedup"],
                         capture_output=True, text=True, env=env, timeout=120)
    if "refusing to rewrite" not in ded.stdout:
        return "dedup did not refuse by name: %s" % (ded.stdout + ded.stderr).strip()[:160]
    if "swallowed error" in ded.stdout:
        return "dedup still swallows an exception instead of refusing: %s" % ded.stdout.strip()[:160]
    return "named"


@case("a-reduction-does-not-delete-the-rows-the-disclosure-counts", "guard", "two named")
def gd_undated(root):
    # Two rows with no session_id and unreadable timestamps collapsed into ONE
    # under the dedupe key "?" before the undated guard could count them, so
    # the FAIL said "1 session row(s)" about a file holding two.
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _score_lines(root, {"99-System/telemetry/outcomes.jsonl":
                              [{"ts": "bogus"}, {"ts": "bogus2"},
                               {"schema": 2, "session_id": "real1", "ts": now}]})
    line = next((l for l in out.splitlines() if l.startswith("ledger-coverage")), "")
    if "2 session row(s) carry no readable timestamp" not in line:
        return "got: %s" % line[:120]
    return "two named"


@case("one-prediction-in-five-spellings-is-one-prediction", "guard", "one counted")
def gd_predict(root):
    ledger = ("# Operator model\n## Prediction ledger\n"
              "date | prediction | seal | scored on | hit\n"
              "2026-07-01 | the queue drains | seal-1 | review | yes\n"
              "2026-07-02 | the queue drains. | seal-2 | review | yes\n"
              "2026-07-03 | The queue drains | seal-3 | review | yes\n"
              "2026-07-04 | the queue drains; | seal-4 | review | yes\n"
              "2026-07-05 | the queue drains, | seal-5 | review | yes\n")
    out = _score_lines(root, {"50-Reference/operator-model.md": ledger})
    line = next((l for l in out.splitlines() if l.startswith("prediction-seals")), "")
    if "1 sealed" not in line or "4 repeated prediction(s) counted once" not in line:
        return "got: %s" % line[:120]
    return "one counted"


@case("a-sealed-placeholder-is-not-a-prediction", "guard", "not counted")
def gd_predict2(root):
    # The vacuity rule reached the seal column and not the prediction: five
    # sealed TBD rows counted as five predictions.
    ledger = ("# Operator model\n## Prediction ledger\n"
              "date | prediction | seal | scored on | hit\n"
              + "".join("2026-07-0%d | TBD | seal-%d | review | yes\n" % (i, i)
                        for i in range(1, 6)))
    out = _score_lines(root, {"50-Reference/operator-model.md": ledger})
    line = next((l for l in out.splitlines() if l.startswith("prediction-seals")), "")
    if not line.split()[1:2] == ["NO-DATA"]:
        return "got: %s" % line[:120]
    return "not counted"


@case("one-rating-in-six-spellings-is-one-rating", "guard", "one counted")
def gd_ratings(root):
    rows = [{"score": 5, "task": "t", "note": n}
            for n in ("good", "good.", "good,", "good;", "GOOD", " good ")]
    out = _score_lines(root, {"99-System/telemetry/ratings.jsonl": rows})
    line = next((l for l in out.splitlines() if l.startswith("felt-outcome-ratings")), "")
    if "1 distinct ratings" not in line:
        return "got: %s" % line[:120]
    return "one counted"


@case("an-unreadable-review-timestamp-cannot-hide-the-real-reviews", "guard", "named")
def gd_reviews(root):
    # max() over raw timestamp strings let {"ts": "whenever"} beat every date
    # lexically, and the check reported "no review recorded" about a file
    # holding one from seconds ago.
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _score_lines(root, {"99-System/telemetry/reviews.jsonl":
                              [{"ts": now}, {"ts": "whenever"}]})
    line = next((l for l in out.splitlines() if l.startswith("review-cadence")), "")
    if "no readable timestamp" not in line or "'whenever'" not in line:
        return "got: %s" % line[:120]
    return "named"


@case("an-infinite-cache-counter-is-not-a-measurement", "guard", "refused")
def gd_cacheinf(root):
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write(root, "99-System/telemetry/outcomes.jsonl",
          '{"schema":2,"session_id":"sess-0001","ts":"%s","cache_read":Infinity,"cache_write":5}\n'
          % now)
    out = subprocess.run([sys.executable, SCORE_TOOL],
                         capture_output=True, text=True,
                         env=dict(os.environ, BROTHERSBE_VAULT=root,
                                  BROTHERSBE_REGISTRIES="", SBE_LINT_ROOT=""))
    line = next((l for l in out.stdout.splitlines() if l.startswith("cache-economy")), "")
    if not line.split()[1:2] == ["FAIL"] or "not counts" not in line:
        return "got: %s" % line[:120]
    return "refused"


@case("dedup-refuses-to-rewrite-a-file-it-cannot-fully-read", "guard", "refused")
def gd_dedup(root):
    led = "99-System/telemetry/outcomes.jsonl"
    original = ('{"schema":2,"session_id":"s1","ts":"2026-07-27T09:00:00Z"}\n'
                "{broken\n")
    write(root, led, original)
    out = subprocess.run([sys.executable, TELEMETRY_TOOL, "dedup"],
                         capture_output=True, text=True,
                         env=dict(os.environ, BROTHERSBE_VAULT=root))
    if open(os.path.join(root, led)).read() != original:
        return "dedup rewrote a file with unparseable lines"
    if "refusing to rewrite" not in out.stdout:
        return "the refusal is not named: %s" % out.stdout.strip()[:120]
    return "refused"


@case("a-crlf-manifest-verifies-instead-of-reporting-every-file-missing", "guard", "read as paths")
def gd_vinstall_crlf(root):
    # The Windows condition, mounted on POSIX so it is measured on every leg.
    # `read -r` keeps the carriage return of a CRLF manifest INSIDE the line,
    # so verify-install.sh extracted the path "hello.txt<CR>", which exists on
    # no filesystem: every file the manifest named reported MISSING and every
    # file on disk reported EXTRA, firing this script's loudest alarm ("exactly
    # the shape of a planted backdoor") over an installation where nothing was
    # wrong. Nothing exotic produces that manifest: a text-mode write on
    # Windows does, which is how the eval fixtures beside this one hit it on
    # the windows-latest leg (run 31040612827, "an unnamed EXTRA file" in a
    # control tree), and so does any transport that normalises line endings.
    # Calibration, run against a scratch copy of the script with the `case
    # "$line" in *"$CR")` strip deleted: this case returns "the CRLF manifest
    # was read as 0 path(s): 2 missing, 2 extra ...". With the strip in place
    # it returns "read as paths". Both halves assert: the CRLF manifest must
    # verify clean AND the removal must be disclosed, because a silent
    # normalisation is a behaviour nobody can see.
    import hashlib, shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, "inst/hello.txt", "hi\n")
    rels = ("hello.txt", "scripts/verify-install.sh")
    # newline="\r\n" EXPLICITLY, on every platform: the defect is the bytes of
    # the manifest, so this fixture writes those bytes rather than relying on
    # the host's text-mode translation to supply them.
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w", newline="\r\n") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "rb") as f:
        if b"\r\n" not in f.read():
            return "the fixture did not write a CRLF manifest, so it proves nothing"
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(inst, "CHECKSUMS.sha256"), inst],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        m = _re_missing_extra(out.stdout)
        return ("the CRLF manifest was read as 0 path(s): %s (exit %d)"
                % (m, out.returncode))
    if "%d file(s) match" % len(rels) not in out.stdout:
        return "the CRLF manifest verified without matching both files: %s" % out.stdout[-200:]
    if "manifest line(s) ended in a carriage return" not in out.stdout:
        return "the carriage returns were removed in silence, which is a behaviour nobody can see"
    return "read as paths"


@case("a-backslash-in-the-install-path-does-not-accuse-a-clean-tree", "guard",
      "clean tree reads clean")
def gd_vinstall_metachar_path(root):
    # The second Windows condition, mounted on POSIX the same way the CRLF one
    # above is. verify-install.sh stripped the install root off each walked
    # path with sed "s|^$TARGET/||", which splices the path into a REGULAR
    # EXPRESSION. Windows supplies backslashes in every path, so the strip
    # silently failed there: each walked file kept its absolute path, matched
    # no manifest entry, and a clean installation was told it held files
    # "exactly the shape of a planted backdoor". That is the loudest sentence
    # this script can print, fired over a tree where nothing was wrong, which
    # is the same harm the CRLF fix removed by a different route.
    #
    # A backslash is legal in a POSIX directory name, so the defect is
    # reproducible on every leg rather than only on windows-latest, and the
    # regression that reached users is measured everywhere from now on.
    # Calibration, run against a scratch copy with ${full#"$TARGET/"} put back
    # to the sed form: this case returns "a clean tree was accused: 0 missing,
    # 2 extra (exit 1)". With the parameter expansion it returns "clean tree
    # reads clean".
    import hashlib, shutil
    inst = os.path.join(root, "a\\runner\\inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, os.path.join("a\\runner\\inst", "hello.txt"), "hi\n")
    rels = ("hello.txt", "scripts/verify-install.sh")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(inst, "CHECKSUMS.sha256"), inst],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        # MISMATCHED is reported alongside missing and extra, and this is not
        # cosmetic. The first version of this message printed only missing and
        # extra, so the Linux failure it caught read "0 missing, 0 extra (exit
        # 1)": a failure that named nothing, over a run whose real signature was
        # two MISMATCHED files from GNU's filename escaping. A failure message
        # that omits the field the failure lives in costs an investigation.
        mismatched = re.search(r"(\d+) mismatched", out.stdout)
        return ("a clean tree was accused: %s mismatched, %s (exit %d)"
                % (mismatched.group(1) if mismatched else "an unreported number of",
                   _re_missing_extra(out.stdout), out.returncode))
    # Both halves assert. A nonzero exit is the loud failure, but a run that
    # exits clean while having compared nothing would be the quiet one, so the
    # match count is checked too: this tree holds exactly the files the
    # manifest names, and all of them must have been verified.
    if "%d file(s) match" % len(rels) not in out.stdout:
        return ("the tree exited clean without verifying both files, so nothing "
                "here establishes the paths were compared: %s" % out.stdout[-200:])
    return "clean tree reads clean"


@case("a-planted-file-named-like-an-option-is-still-reported",
      "guard", "the planted file is named")
def gd_vinstall_dash_filename(root):
    # SECURITY CRITICAL, found by an adversarial review and reproduced before it
    # was believed. The eighth defect of the path-as-syntax shape, and the first
    # where the syntax was OPTION syntax: the membership test passed `$rel` to
    # grep as a bare operand, so an installed file whose name begins with a dash
    # was parsed as a flag. The flag then consumed the manifest-paths filename as
    # its own argument, leaving grep no FILE operand, so grep read STDIN, and
    # stdin at that point is the walk output the enclosing loop is still reading.
    # Every remaining walked entry was swallowed unchecked.
    #
    # What that cost, reproduced exactly: a tree holding an undeclared `-v` AND
    # an undeclared `skills/backdoor.py` reported `0 extra`, `PASSED`, exit 0.
    # The one tool whose entire job is telling you whether your installation has
    # been tampered with certified a tampered installation.
    #
    # Calibration, against a scratch copy with the operand form restored: this
    # case returns the planted file was not named. With `-e`, `--` and a denied
    # stdin it returns "the planted file is named".
    import hashlib, shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    os.makedirs(os.path.join(inst, "skills"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, os.path.join("inst", "hello.txt"), "hi\n")
    # The dash-named file sorts before skills/, so under the defect it is the
    # entry that steals stdin and hides everything after it.
    write(root, os.path.join("inst", "-v"), "")
    write(root, os.path.join("inst", "skills", "backdoor.py"), "print('planted')\n")
    rels = ("hello.txt", "scripts/verify-install.sh")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(inst, "CHECKSUMS.sha256"), inst],
                         capture_output=True, text=True, timeout=120)
    if "skills/backdoor.py" not in out.stdout:
        return ("a planted file was NOT named, so the walk was truncated: exit %d, %s"
                % (out.returncode, _re_missing_extra(out.stdout)))
    if out.returncode == 0:
        return ("the planted file was named but the run still exited 0, so the "
                "verdict does not follow its own evidence")
    return "the planted file is named"


@case("a-glob-metacharacter-in-the-install-root-does-not-accuse-a-clean-tree",
      "guard", "clean tree reads clean")
def gd_vinstall_glob_root(root):
    # The fourth defect of the same path-as-syntax shape in this script's
    # history (CRLF manifest, sed regex splice, GNU coreutils escaping): both
    # `find` walks built every exclusion as `-path "$TARGET/.claude/*"`,
    # splicing the install root into a GLOB PATTERN. A root such as
    # /tmp/sbe-glob/x[1]/inst turns the `[1]` directory-name fragment into a
    # character-class test, so the intended `.claude/*` exclusion stops
    # matching the real .claude directory: .claude/settings.json, a
    # documented exclusion (the harness-written local config), walks through
    # unexcluded and reports EXTRA over an installation where nothing is
    # wrong. Fixed the same way the sed-regex splice above it was: TARGET is
    # never fed to a pattern-matching construct again. Both walks now `cd
    # "$TARGET"` and enumerate the relative path `.`, so every `-path`
    # exclusion is a literal, TARGET-independent string no character in the
    # install root can reinterpret.
    #
    # Calibration, run against a scratch copy with the walks reverted to
    # `find "$TARGET" ... -path "$TARGET/.claude/*"`: this case returns "a
    # clean tree was accused: ... 1 extra (exit 1)". With the cd-relative
    # walk it returns "clean tree reads clean".
    import hashlib, shutil
    inst = os.path.join(root, "x[1]", "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, os.path.join("x[1]", "inst", "hello.txt"), "hi\n")
    write(root, os.path.join("x[1]", "inst", ".claude", "settings.json"),
          '{"documented":"exclusion"}')
    rels = ("hello.txt", "scripts/verify-install.sh")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(inst, "CHECKSUMS.sha256"), inst],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        return ("a clean tree was accused: %s (exit %d)"
                % (_re_missing_extra(out.stdout), out.returncode))
    if "%d file(s) match" % len(rels) not in out.stdout:
        return ("the tree exited clean without verifying both files, so nothing "
                "here establishes the paths were compared: %s" % out.stdout[-200:])
    return "clean tree reads clean"


@case("no-checksum-tool-is-handed-a-filename-it-would-escape", "guard",
      "both scripts hash from stdin")
def gd_hash_from_stdin(root):
    # The third defect of one shape in this pair of scripts, and the only one a
    # macOS host cannot reproduce by running anything, which is why it is
    # asserted on the SHAPE of the code instead of on its output.
    #
    # GNU coreutils escapes a filename containing a backslash or a newline: it
    # doubles the backslashes and prefixes the whole output line with one. That
    # shifts the hash a column right, so `cut -c1-64` returned a backslash glued
    # to 63 hex digits and every such file reported MISMATCH. Apple's and BSD's
    # tools do not escape, so the bug was invisible on macOS and red on Linux,
    # which is exactly how it reached CI unnoticed.
    #
    # Feeding the file on stdin keeps the name out of the tool's output, so no
    # escaping rule can apply. Both scripts must do it: a generator that escapes
    # and a checker that does not would write manifests that disagree by
    # platform, which is worse than either bug alone.
    #
    # Calibration: restoring `sha256sum "$1"` in either script turns this red
    # naming that script and that line.
    import re as _re
    problems = []
    for rel in ("scripts/verify-install.sh", "scripts/checksums.sh"):
        path = os.path.join(_REPO, rel)
        try:
            with open(path) as handle:
                text = handle.read()
        except (IOError, OSError) as exc:
            return "could not read %s, so this check established nothing: %s" % (rel, exc)
        for num, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for tool in ("sha256sum", "shasum -a 256"):
                # The tool invoked with the file as an ARGUMENT is the defect;
                # invoked with `< "$1"` it is the fix. Matching the argument
                # form directly, rather than the absence of the stdin form, so
                # a line doing neither cannot read as safe.
                if _re.search(r"%s\s+\"\$1\"" % _re.escape(tool), line):
                    problems.append("%s:%d hands the filename to %s as an argument"
                                    % (rel, num, tool))
    if problems:
        return "; ".join(problems)
    # NO-DATA guard: a rename or a rewrite that removed the hashing entirely
    # would leave `problems` empty and read as a pass over nothing.
    found = sum(1 for rel in ("scripts/verify-install.sh", "scripts/checksums.sh")
                if "| cut -c1-64" in open(os.path.join(_REPO, rel)).read())
    if found != 2:
        return ("only %d of the 2 scripts still hash at all, so this check found "
                "nothing to vouch for" % found)
    return "both scripts hash from stdin"


@case("two-spellings-of-one-root-do-not-make-the-manifest-an-intruder", "guard",
      "one spelling throughout")
def gd_vinstall_path_form(root):
    # The fourth defect of the same shape, and the one that was nearly shipped
    # as "a POSIX host cannot test this". It could be tested, and this is how.
    #
    # The bug was two DERIVATIONS of one value rather than a bad comparison. A
    # walked file got its relative path by stripping $TARGET exactly as the
    # caller spelled it, because that is what `find` echoes back. The manifest
    # got its relative path through `cd ... && pwd`, which answers in the
    # shell's own spelling. Where those disagree, the manifest fails to
    # recognise itself, is walked, is not matched, and is reported as an added
    # file, so a clean installation reads `1 extra` and FAILED.
    #
    # On Windows the disagreement is guaranteed: the Git for Windows shell
    # answers `pwd` as `/d/a/BrotherSBE` for a root passed as `D:\a\BrotherSBE`.
    # That exact signature (`0 missing, 1 extra`) is what the windows-latest leg
    # reported. A `.` segment reproduces the same irreconcilable pair here: find
    # echoes `/tmp/x/./inst/...` while pwd answers `/tmp/x/inst`.
    #
    # Calibration: delete the `TARGET=$TARGET_CANONICAL` normalisation from a
    # scratch copy and this case returns "a clean tree read 1 extra", naming
    # CHECKSUMS.sha256 as the intruder.
    import hashlib, shutil
    inst = os.path.join(root, "x", "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, os.path.join("x", "inst", "hello.txt"), "hi\n")
    rels = ("hello.txt", "scripts/verify-install.sh")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    # The second spelling. `find` echoes this back verbatim; `pwd` normalises it
    # away. Neither is wrong, and that is the point: the script must not depend
    # on which one it happens to be handed.
    dotted = os.path.join(root, "x", ".", "inst")
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(dotted, "CHECKSUMS.sha256"), dotted],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        extra = re.search(r"(\d+) extra", out.stdout)
        named = [ln.split(":", 1)[1].strip() for ln in out.stdout.splitlines()
                 if ln.startswith("EXTRA:")]
        return ("a clean tree read %s extra%s (exit %d)"
                % (extra.group(1) if extra else "an unreported number of",
                   (", naming " + ", ".join(named)) if named else "",
                   out.returncode))
    if "%d file(s) match" % len(rels) not in out.stdout:
        return ("the run exited clean without verifying both files, so nothing "
                "here establishes the spellings were reconciled: %s" % out.stdout[-200:])
    return "one spelling throughout"


@case("a-root-that-cannot-be-entered-is-refused-not-passed", "guard", "refused")
def gd_vinstall_unenterable_root(root):
    # The failure path of the normalisation above. Canonicalising a root means
    # the script now depends on being able to ENTER it, and a check that cannot
    # reach what it is checking must refuse loudly rather than verify an empty
    # set and report PASSED. NO-DATA is never a pass.
    import shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        f.write("")
    missing = os.path.join(root, "there-is-no-such-directory")
    out = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                          os.path.join(inst, "CHECKSUMS.sha256"), missing],
                         capture_output=True, text=True, timeout=120)
    if out.returncode == 0:
        return ("an unreachable root exited 0, which reads as a pass over a tree "
                "this check never opened: %s" % (out.stdout + out.stderr)[-200:])
    if "PASSED" in out.stdout:
        return "an unreachable root printed PASSED"
    if "nothing here was checked" not in (out.stdout + out.stderr):
        return ("it refused but did not say that nothing was checked, so a reader "
                "cannot tell a refusal from a failure: %s" % (out.stdout + out.stderr)[-200:])
    return "refused"


@case("the-benchmark-ground-truth-does-not-demand-a-migration-rehearsal", "guard",
      "content held back, path glob intact")
def gd_policy_benchmark_surface(root):
    # Found when the benchmark lane BLOCKED its own merge. benchmarks/defects.json
    # enumerates the planted defects the harness scores against, so it
    # necessarily carries the DDL of the migration defect it plants. The sql-ddl
    # content signal fired on it and the consumer-checks leg went red demanding
    # check:migration-rehearsal for a change that touches no database.
    #
    # BOTH halves are asserted, and the second is the one that matters. Adding a
    # surface to EXAMPLE_SURFACES must hold back only the INFERENCE FROM CONTENT
    # that a file describes a change to production state. It must never disable
    # the path globs, or the exemption becomes a hole: anyone could park a real
    # migration under benchmarks/ and walk past the gate. A fix that quietly
    # widens into a bypass is worse than the bug it closes.
    #
    # Calibration: removing the two benchmarks entries from EXAMPLE_SURFACES
    # returns "benchmarks/defects.json is not held back from content signals",
    # which is the red the merge actually hit.
    import sys as _sys
    src = os.path.join(_REPO, "src")
    if src not in _sys.path:
        _sys.path.insert(0, src)
    try:
        from brothersbe.policy import _is_example_surface, path_matches
    except ImportError as exc:
        return "could not import the policy engine, so this check established nothing: %s" % exc

    if not _is_example_surface("benchmarks/defects.json"):
        return ("benchmarks/defects.json is not held back from content signals, so the "
                "ground-truth file's own DDL demands a migration rehearsal receipt")
    if not _is_example_surface("benchmarks/scenarios/S1-migration.md"):
        return "a nested benchmark document is not held back, so only the top level is covered"

    # The half that keeps this from being a bypass.
    if not path_matches("**/*.sql", "benchmarks/real_migration.sql"):
        return ("the **/*.sql PATH glob no longer matches under benchmarks/, so a real "
                "migration parked there would walk past the gate: this exemption has "
                "widened from holding back content inference into disabling the rule")
    if not path_matches("**/*.sql", "db/migrations/0002_x.up.sql"):
        return "the **/*.sql path glob stopped matching an ordinary migration path"

    # NO-DATA guard: an EXAMPLE_SURFACES emptied by a refactor would make every
    # check above vacuously true, which must not read as a pass.
    from brothersbe.policy import EXAMPLE_SURFACES
    if not any(s.startswith("benchmarks") for s in EXAMPLE_SURFACES):
        return ("no benchmarks entry is present in EXAMPLE_SURFACES, so the checks above "
                "passed over a list that does not contain what they claim to verify")
    return "content held back, path glob intact"


def _re_missing_extra(stdout):
    """The MISSING/EXTRA counts out of a verify-install summary line, for the
    failure sentence above. Returns a plain description rather than raising, so
    a summary line this reader does not recognise is named instead of crashing
    the case that called it."""
    m = re.search(r"(\d+) missing, (\d+) extra", stdout)
    if not m:
        return "no summary line this reader recognises"
    return "%s missing, %s extra" % (m.group(1), m.group(2))


@case("verify-install-fails-over-source-in-an-excluded-path", "guard", "named and failed")
def gd_vinstall(root):
    # This case used to declare a PLATFORM-GAP off POSIX, blaming "the Windows
    # shell" for a control tree that failed with an EXTRA file nobody planted.
    # That was a misdiagnosis of this fixture's OWN manifest: it is written
    # through Python text mode, which on Windows is CRLF, and a CRLF manifest
    # made verify-install.sh report every file MISSING and every file EXTRA.
    # Root closed in scripts/verify-install.sh and pinned by the case above, so
    # the gap declaration is gone and this runs on every leg again.
    # The completeness sentence claimed "no file exists on disk that the
    # manifest does not name" over paths the enumeration excluded by name.
    # The exclusions are now enumerated and counted on every run, and source
    # code among them is its own failure.
    import hashlib, shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, "inst/hello.txt", "hi\n")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in ("hello.txt", "scripts/verify-install.sh"):
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    clean = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                            os.path.join(inst, "CHECKSUMS.sha256"), inst],
                           capture_output=True, text=True)
    if clean.returncode != 0:
        return "the control tree failed: %s" % (clean.stdout + clean.stderr)[-200:]
    if "outside the excluded paths" not in clean.stdout:
        return "the PASSED sentence still claims completeness with no qualifier"
    os.makedirs(os.path.join(inst, "tools", "__pycache__"))
    write(root, "inst/tools/__pycache__/planted.py", "print('invisible')\n")
    planted = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                              os.path.join(inst, "CHECKSUMS.sha256"), inst],
                             capture_output=True, text=True)
    if planted.returncode == 0:
        return "planted source in an excluded path still passes"
    if "EXCLUDED-SOURCE" not in planted.stdout or "planted.py" not in planted.stdout:
        return "the failure does not name the planted file"
    return "named and failed"


@case("a-second-function-cannot-inherit-a-reviewed-lint-exemption", "guard", "caught")
def gd_lint_shadow(root):
    # The exemption key is (file basename, function name), a name the AUTHOR
    # controls, so a SECOND function of that name in that file inherited an
    # exemption reviewed for the first: a body that is literally
    # `return "PASS", "examined nothing at all"`, registered nowhere, was
    # invisible to the lint whose stated job is catching an unregistered
    # verdict path. Every allowlist entry now resolves against the functions
    # it actually names, and a key resolving twice is a failure. Run against a
    # COPY of tools/, never the shipped tree.
    import shutil
    tools = os.path.join(root, "tools")
    shutil.copytree(os.path.join(_REPO, "tools"), tools,
                    ignore=shutil.ignore_patterns("__pycache__"))
    src = os.path.join(tools, "sbe_gate.py")
    body = open(src, errors="replace").read()
    shadow = ('\n\ndef find(root, name, _shadow=True):\n'
              '    """A second function named find that returns a real PASS verdict."""\n'
              '    return "PASS", "examined nothing at all"\n\n')
    marker = "\ndef gate_numbers(root):"
    if marker not in body:
        return "the fixture could not find gate_numbers to inject above"
    open(src, "w").write(body.replace(marker, shadow + marker, 1))
    meta = SourceFileLoader("tndc_shadow",
                            os.path.join(HERE, "test_no_data_class.py")).load_module()
    meta.TOOLS_DIR = tools
    flagged = meta.pass_returning_functions()
    hits = meta.EXEMPTION_HITS.get(("sbe_gate.py", "find"), [])
    if any(f == "sbe_gate.py" and n == "find" for f, n, _w in flagged):
        return "caught"      # flagged directly, which is stronger still
    if len(hits) < 2:
        return ("the shadowed function neither was flagged nor resolved through the exemption "
                "(%d hit(s)), so it is invisible to this lint" % len(hits))
    # The shipped reconciliation: an exemption key that resolves twice is a
    # named meta-test failure, so the second body cannot inherit the review.
    reconciled = [k for k, v in meta.EXEMPTION_HITS.items() if len(v) > 1]
    if ("sbe_gate.py", "find") not in reconciled:
        return "the double resolution was not recorded, so nothing fails on it"
    return "caught"


@case("a-symlink-inside-an-excluded-path-fails-the-install-check", "guard", "named and failed")
def gd_vinstall_excluded_symlink(root):
    # The plant that was invisible to BOTH walks: excluded by path from the
    # extra-file walk, skipped by `-type f` in the excluded-path walk, so the
    # run printed "0 file(s), 0 of them source code" and PASSED while the code
    # it resolves to sits in the install tree. The type-agnostic rule is now
    # applied to both walks, and a non-regular entry inside an excluded path
    # is its own named failure: the manifest cannot hash what it resolves to
    # AND it is not enumerable as machine state, which the exclusions are for.
    import hashlib, shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, "inst/hello.txt", "hi\n")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in ("hello.txt", "scripts/verify-install.sh"):
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    payload = os.path.join(root, "payload_outside.sh")
    write(root, "payload_outside.sh", "#!/bin/sh\necho pwned\n")
    os.makedirs(os.path.join(inst, "tools", "__pycache__"))
    os.makedirs(os.path.join(inst, ".superpowers"))
    os.symlink(payload, os.path.join(inst, "tools", "__pycache__", "planted.py"))
    os.symlink(payload, os.path.join(inst, ".superpowers", "hook.sh"))
    r = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                        os.path.join(inst, "CHECKSUMS.sha256"), inst],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        return "a symlinked plant inside an excluded path still passes"
    if "EXCLUDED-NON-REGULAR" not in r.stdout or "planted.py" not in r.stdout:
        return "the failure does not name the symlinked plant"
    if ".superpowers/hook.sh" not in r.stdout:
        return "the second excluded path's plant was not named"
    if "0 of them non-regular" in r.stdout:
        return "the qualifier sentence still counts the plants as zero"
    return "named and failed"


@case("a-symlinked-planted-module-fails-the-install-check", "guard", "named and failed")
def gd_vinstall_symlink(root):
    if os.name != "posix":
        # NARROWED, 2026-08-07. The reason this used to give ("its control tree
        # misbehaves under the Windows shell") was wrong and is now closed at
        # its root: that control tree failed because the fixture's manifest was
        # written through Python text mode, which is CRLF on Windows, and
        # verify-install.sh read the carriage return as part of every path. Its
        # sibling gd_vinstall runs on every leg again as a result. What is left
        # here is the half that was never diagnosed and is not reproducible from
        # a POSIX machine: whether a symlink created by os.symlink on Windows is
        # seen as non-regular by the Git-for-Windows shell's own file tests, so
        # the NON-REGULAR path this case asserts is reachable there at all. That
        # is one observation a Windows run can make and this host cannot.
        return ("PLATFORM-GAP: os.symlink writes an NTFS reparse point and "
                "nothing here establishes that the Git-for-Windows shell's "
                "file tests read it as non-regular, so the NON-REGULAR refusal "
                "this case asserts is measured on the POSIX legs only")
    # `find -type f` never returns a symlink, so a planted tools/backdoor.py
    # pointing at code OUTSIDE the tree was reported as nothing at all by
    # verify-install and imported-and-executed by the honesty suite. The check
    # enumerates every directory entry regardless of type now, and a
    # non-regular entry the manifest cannot hash is its own failure.
    import hashlib, shutil
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "scripts"))
    os.makedirs(os.path.join(inst, "tools"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(inst, "scripts", "verify-install.sh"))
    write(root, "inst/tools/real.py", "def f():\n    return 1\n")
    with open(os.path.join(inst, "CHECKSUMS.sha256"), "w") as f:
        for rel in ("tools/real.py", "scripts/verify-install.sh"):
            h = hashlib.sha256(open(os.path.join(inst, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    clean = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                            os.path.join(inst, "CHECKSUMS.sha256"), inst],
                           capture_output=True, text=True)
    if clean.returncode != 0:
        return "the control tree failed: %s" % (clean.stdout + clean.stderr)[-200:]
    payload = os.path.join(root, "outside-payload.py")
    write(root, "outside-payload.py", "print('PLANTED')\n")
    os.symlink(payload, os.path.join(inst, "tools", "backdoor.py"))
    planted = subprocess.run(["sh", os.path.join(inst, "scripts", "verify-install.sh"),
                              os.path.join(inst, "CHECKSUMS.sha256"), inst],
                             capture_output=True, text=True)
    if planted.returncode == 0:
        return "the symlinked planted module still passes"
    if "NON-REGULAR" not in planted.stdout or "backdoor.py" not in planted.stdout:
        return "the failure does not name the symlinked module: %s" % planted.stdout[-200:]
    return "named and failed"


@case("a-symlinked-install-root-does-not-accuse-itself", "guard", "root clean, plant caught")
def gd_vinstall_symlinked_root(root):
    # Defect 6, the REAL version (a prior audit misdescribed it; fixing that
    # misdescribed version would have been scope creep, not this fix).
    # `find "$TARGET" ...` given a symlink as its OWN starting argument, with
    # neither -H nor -L, does not follow it: it reports the argument itself
    # as a single `-type l` entry and does not descend into what it points at
    # at all (confirmed directly on this project's own dev host: `find
    # /a/symlink` prints exactly that one line, nothing beneath it). The
    # relative-path stripping, `rel=${full#"$TARGET/"}`, could not strip that
    # entry either, because `full` equalled `$TARGET` exactly with no
    # trailing slash to match against, so [ -L "$full" ] fired on the ROOT
    # and the whole installation was reported NON-REGULAR and FAILED on that
    # one entry, every real file inside left unchecked. The comment beside
    # the walk scopes the non-regular rule to entries INSIDE the tree, and
    # install.sh (lines 36 and 88) says a symlinked clone is supported, so a
    # supported installation could never pass its own verifier.
    #
    # `cd "$TARGET"` dereferences the symlink as part of changing into it
    # (the kernel resolves it), so `find .` then walks the REAL directory's
    # actual contents, and `-mindepth 1` never emits an entry for the root at
    # all. A symlink PLANTED INSIDE the tree is still lstat'd during the walk
    # exactly as before and is still reported, which this case checks in the
    # same run so the fix is not proven to overcorrect into silence.
    #
    # Calibration, run against a scratch copy with the walks reverted to
    # `find "$TARGET" ...` (no cd, no relative walk): the first half of this
    # case returns "a symlinked root accused itself: ... non-regular (exit
    # 1)". With the cd-relative walk it returns "root clean, plant caught".
    if os.name != "posix":
        # Same platform gap as gd_vinstall_symlink above: os.symlink writes an
        # NTFS reparse point, and nothing here establishes that the
        # Git-for-Windows shell's own `cd` and `find` dereference a
        # command-line symlink argument the same way a POSIX shell does, so
        # this case is measured on the POSIX legs only.
        return ("PLATFORM-GAP: os.symlink writes an NTFS reparse point and "
                "nothing here establishes that the Git-for-Windows shell's "
                "cd/find dereference a symlinked root the same way a POSIX "
                "shell does, so this case is measured on the POSIX legs only")
    import hashlib, shutil
    real = os.path.join(root, "real", "inst")
    os.makedirs(os.path.join(real, "scripts"))
    shutil.copy(os.path.join(_REPO, "scripts", "verify-install.sh"),
                os.path.join(real, "scripts", "verify-install.sh"))
    write(root, os.path.join("real", "inst", "hello.txt"), "hi\n")
    rels = ("hello.txt", "scripts/verify-install.sh")
    with open(os.path.join(real, "CHECKSUMS.sha256"), "w") as f:
        for rel in rels:
            h = hashlib.sha256(open(os.path.join(real, rel), "rb").read()).hexdigest()
            f.write("%s  %s\n" % (h, rel))
    link = os.path.join(root, "link")
    os.symlink(real, link)
    clean = subprocess.run(["sh", os.path.join(link, "scripts", "verify-install.sh"),
                            os.path.join(link, "CHECKSUMS.sha256"), link],
                           capture_output=True, text=True, timeout=120)
    if clean.returncode != 0:
        return ("a symlinked root accused itself: %s (exit %d)"
                % (_re_missing_extra(clean.stdout), clean.returncode))
    if "0 non-regular" not in clean.stdout:
        return ("the symlinked root was not reported as 0 non-regular: %s"
                % clean.stdout[-200:])
    payload = os.path.join(root, "outside-payload.py")
    write(root, "outside-payload.py", "print('PLANTED')\n")
    os.symlink(payload, os.path.join(real, "planted.py"))
    planted = subprocess.run(["sh", os.path.join(link, "scripts", "verify-install.sh"),
                              os.path.join(link, "CHECKSUMS.sha256"), link],
                             capture_output=True, text=True, timeout=120)
    if planted.returncode == 0:
        return "a symlinked plant inside a symlinked root still passes"
    if "NON-REGULAR" not in planted.stdout or "planted.py" not in planted.stdout:
        return ("the plant inside the symlinked root was not named: %s"
                % planted.stdout[-200:])
    return "root clean, plant caught"


@case("a-symlinked-module-under-tools-fails-the-honesty-suite", "guard", "refused unwalked")
def gd_symlink_module(root):
    # The other half of the same hole: tool_sources() walked with `find`-like
    # semantics and imported every .py, symlinks included, so the planted
    # module RAN inside the suite whose one job is refusing to report over
    # what it did not examine. A non-regular .py under tools/ is refused by
    # name now, never imported.
    import shutil
    for rel in ("tools", "evals"):
        shutil.copytree(os.path.join(_REPO, rel), os.path.join(root, rel))
    payload = os.path.join(root, "outside-payload.py")
    marker = os.path.join(root, "PLANTED-RAN")
    write(root, "outside-payload.py", "open(%r, 'w').write('ran')\n" % marker)
    os.symlink(payload, os.path.join(root, "tools", "backdoor.py"))
    meta = SourceFileLoader("tndc_symlink",
                            os.path.join(root, "evals", "test_no_data_class.py")).load_module()
    sources = meta.tool_sources()
    if os.path.exists(marker):
        return "the symlinked module was executed"
    if any("backdoor.py" in s for s in sources):
        return "the symlinked module was walked as source"
    if not any("backdoor.py" in p for p in meta.NONREGULAR_SOURCE):
        return "the symlinked module was dropped in silence, not named"
    return "refused unwalked"


def _first_diff_offset(a, b):
    """The first byte index where two byte strings disagree, or None if equal
    up to the shorter one's length (the length difference is still reported
    by the caller). Bytes in, bytes out: no text-mode decoding happens here,
    because decoding is exactly the step that can hide a line-ending byte
    difference before this function ever sees it."""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return None if len(a) == len(b) else n


def _sha256_of_file(path):
    """Hash one file the way scripts/checksums.sh does: binary, in chunks."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _reread_still_disagrees_with_manifest(rel_path, manifest_hash, root=None):
    """Second opinion on ONE drifted path: does it STILL disagree with the manifest?

    The re-read has to ask the same question the drift was found on. Drift is
    found by hashing the working tree and comparing those hashes against the ones
    the committed manifest records, so the second opinion re-hashes the file and
    compares it against that same manifest hash.

    The previous form compared the working-tree file against the COMMITTED GIT
    BLOB instead. That answers a different question, and on a clean checkout it
    answers it the same way every time: after any commit the blob and the working
    tree are identical by construction, so every genuinely stale path re-read as
    "identical bytes", was classified transient, and was forgiven. CI checks out a
    clean tree, so this gate could not fail there, which is the one place it
    exists to fail. Reproduced before this fix, on a committed clean tree: the
    manifest recorded 4c8b2430... for README.md while the file hashed to
    0d75ef2c..., and the gate still reported "The manifest matches".

    The race this filter exists for survives, which is why this is a re-hash
    rather than a deletion. On a host where a file read immediately after checkout
    differs from the same file read a moment later, the second hash lands on the
    manifest's own value and the path is still correctly forgiven. What can no
    longer happen is forgiving a file whose bytes really do not match what shipped.

    True means persistent, a real stale manifest. False means transient.
    An unreadable file is persistent: absent evidence never exonerates.
    """
    base = _REPO if root is None else root
    try:
        return _sha256_of_file(os.path.join(base, rel_path)) != manifest_hash
    except OSError:
        return True


@case("the-tracked-manifest-matches-the-tree-it-ships-with", "guard", "matches")
def gd_manifest_fresh(root):
    # A pristine clone of HEAD failed its own integrity check: CHECKSUMS.sha256
    # went stale across a whole fix wave because nothing gated the manifest
    # against the tree, so the security page's first verification called every
    # honest fresh install untrustworthy and the shipped harness a planted
    # backdoor. This recomputes the manifest from the tracked tree and diffs it
    # against the committed one, so a stale manifest is a red suite (the same
    # shape as the doc-count guards) and no commit can leave one behind.
    gen = subprocess.run(["sh", os.path.join(_REPO, "scripts", "checksums.sh")],
                         capture_output=True, text=True, cwd=_REPO)
    if gen.returncode != 0:
        return "checksums.sh did not run: %s" % (gen.stderr or gen.stdout)[-200:]
    committed = open(os.path.join(_REPO, "CHECKSUMS.sha256"), errors="replace").read()
    # The generator omits the manifest file itself; compare the rest verbatim.
    # EXACT path, not a suffix: `l.endswith("CHECKSUMS.sha256")` dropped any
    # tracked path ending in those bytes (docs/CHECKSUMS.sha256,
    # release-CHECKSUMS.sha256) from BOTH sides, so a tracked file the
    # committed manifest never named still read "matches". The generator
    # itself excludes its own OUT_FILE by exact match (scripts/checksums.sh);
    # this re-implementation now does the same, so the blindness has no
    # spelling left to hide in.
    def _not_self(l):
        return l.strip() and l.split("  ", 1)[-1] != "CHECKSUMS.sha256"
    want = "".join(l + "\n" for l in gen.stdout.splitlines() if _not_self(l))
    have = "".join(l + "\n" for l in committed.splitlines() if _not_self(l))
    if want == have:
        return "matches"
    wl = {l.split("  ", 1)[1]: l[:64] for l in want.splitlines() if "  " in l}
    hl = {l.split("  ", 1)[1]: l[:64] for l in have.splitlines() if "  " in l}
    # TWO DIFFERENT FAILURES, and conflating them is how this gate came to
    # report PASS over a manifest it had just proven stale.
    #
    # A path present on BOTH sides with different hashes is a CONTENT change,
    # and there a filesystem visibility race is genuinely possible, which is
    # what the re-read below exists to catch.
    #
    # A path present on only ONE side is an ADDITION or a REMOVAL: the tree
    # gained a tracked file the manifest never named, or the manifest still
    # names a file the tree no longer tracks. NO amount of re-reading can
    # exonerate that, and the re-read actively LIES about it: `git show HEAD:p`
    # still resolves and the working-tree file still reads back byte-identical,
    # so the path was classified "transient" and forgiven. Reproduced in this
    # repository: two STATE.md.bak files were untracked while the committed
    # manifest still named them, and the gate stayed silent about both.
    #
    # So the set difference is ALWAYS persistent and never eligible for the
    # race filter. The filter is kept, not deleted, because it closes a
    # documented Windows false block, but it now only ever sees the failure it
    # was written for.
    same_path_mismatch = [p for p in sorted(set(wl) & set(hl)) if wl[p] != hl[p]]
    only_generated = sorted(set(wl) - set(hl))   # tracked, absent from the manifest
    only_committed = sorted(set(hl) - set(wl))   # named by the manifest, no longer tracked
    set_difference = only_generated + only_committed
    drift = sorted(same_path_mismatch + set_difference)
    # Reproduction harness (windows-porting-lane finding 3): a Windows leg
    # read several of these as stale with no local way to reproduce why, so
    # rather than guess at a fix this states the evidence a Windows run would
    # need to prove or disprove any theory (line-ending conversion on
    # checkout is the leading one, since no .gitattributes pins the working
    # tree's line endings, but this prints the proof rather than assuming
    # it). For each of the first 4 stale paths, the git-tracked blob and the
    # on-disk working-tree file are both read as raw BYTES (git show's
    # stdout is captured without text=True, and the working-tree file is
    # opened "rb"; either read going through text mode would let Python's
    # own universal-newline translation silently erase the exact evidence
    # being reported), and the first offset where they disagree is printed
    # alongside the two byte values there and each side's length. A CRLF
    # conversion shows up unmistakably: committed=b'\n' at some offset,
    # working-tree=b'\r' at that same offset, working-tree length one longer
    # per converted line break. Anything else it prints is equally
    # informative and just as actionable.
    # BEFORE reporting staleness, ask whether the mismatch survives a second
    # read. Found by finally reading the Windows CI log this eval has been red
    # in since OWED-4 opened: it named four early-alphabet files, and its own
    # byte-level detail said "identical bytes on this read" for every one of
    # them. The check disproved its own finding and reported it as a regression
    # anyway, which is a FALSE BLOCK: the strongest evidence available (a
    # byte-for-byte comparison of the committed blob against the working tree)
    # says the manifest matches, and the weaker evidence (a hash taken moments
    # earlier) says it does not. On Windows a file read immediately after
    # checkout can differ from the same file read a moment later; that is a
    # filesystem visibility race, not a stale manifest, and a gate that cannot
    # tell those apart teaches people to ignore it.
    #
    # Every drifted path is re-read here, not just the four that get printed,
    # because a verdict cannot rest on a sample of the evidence.
    # Seeded with the set difference, which is never eligible for the re-read.
    persistent, transient = list(set_difference), []
    for p in same_path_mismatch:
        if _reread_still_disagrees_with_manifest(p, hl[p]):
            persistent.append(p)
        else:
            transient.append(p)
    if transient and not persistent:
        # Disclosed loudly, never silently: a reader has to be able to see that
        # this happened, and a run where it happens every time is a real signal
        # about the host even though it is not a stale manifest.
        sys.stderr.write(
            "the-tracked-manifest: %d path(s) hashed as drifted and then RE-HASHED to "
            "exactly what the manifest records (%s). The manifest matches; the first "
            "hash was taken before this host had settled. Reported rather than hidden. "
            "This branch is reachable ONLY for a same-path hash mismatch whose second "
            "hash lands on the manifest's own value: a path added to or removed from "
            "the tracked set is never re-read, and a path whose bytes still disagree "
            "with the manifest is never forgiven.\n"
            % (len(transient), ", ".join(transient[:6])))
        return "matches"
    drift = persistent

    detail = []
    for p in drift[:4]:
        blob = subprocess.run(["git", "-C", _REPO, "show", "HEAD:" + p], capture_output=True)
        if blob.returncode != 0:
            detail.append("%s: git show HEAD:%s failed (%s)"
                          % (p, p, blob.stderr.decode(errors="replace").strip()[:120]))
            continue
        try:
            with open(os.path.join(_REPO, p), "rb") as f:
                wt_bytes = f.read()
        except OSError as e:
            detail.append("%s: working-tree file unreadable (%s)" % (p, e.strerror or e))
            continue
        committed_bytes = blob.stdout
        off = _first_diff_offset(committed_bytes, wt_bytes)
        if off is None:
            detail.append("%s: identical bytes on this read (the hash mismatch above did not "
                          "reproduce here; a transient rebuild between the two reads is the "
                          "likely cause, not a persistent content difference)" % p)
        else:
            detail.append("%s: first differing byte at offset %d of %d/%d (committed=%r, "
                          "working-tree=%r)"
                          % (p, off, len(committed_bytes), len(wt_bytes),
                             committed_bytes[off:off + 1], wt_bytes[off:off + 1]))
    return ("the tracked manifest is stale for: %s (regenerate with scripts/checksums.sh "
           "CHECKSUMS.sha256); byte-level detail: %s"
           % (", ".join(drift[:4]), " | ".join(detail) if detail else "n/a"))


@case("a-directory-name-cannot-write-verdict-lines-into-the-report", "guard", "honest")
def gd_pathforge(root):
    # A dossier directory named `ok<break>  datamodel  PASS ...` wrote a forged
    # verdict line into the design report ABOVE the true verdict, and
    # verdict_and_evidence (the honesty suite's own reader, the same shape as
    # any CI grep or human eye) returned the forged PASS for a FAILed check.
    # The forgery channel was closed for receipt FIELDS and left open for
    # PATHS. Fix shape is the class, not the site: every report line passes
    # through one_line() as a whole (say()), and the meta-test's source lint
    # fails any print in a report tool that skips the choke point. This pins
    # the shipped repro end to end.
    #
    # <break> is U+2028 LINE SEPARATOR, not a literal "\n": a raw newline in a
    # directory name is a control character (0x0A), and Windows refuses to
    # create any path component containing one (NTFS forbids the ASCII control
    # range 0x00-0x1F outright, surfacing as an OSError on invalid filename
    # syntax), which made this fixture uncreatable on that leg. An earlier
    # draft claimed U+2028 makedirs succeeds on every platform; the first
    # real windows-latest run disproved that too (NotADirectoryError 20,
    # 'The directory name is invalid', run 31039904060). Where the
    # filesystem refuses the forging name at creation, the refusal ITSELF
    # closes the forgery channel, and this eval records that as honest.
    #
    # What the swap does and does not make identical (round 2 correction: an
    # earlier draft of this comment overclaimed "identical", which a hostile
    # review disproved by mutation). sbe_checks._LINE_BREAKS matches BOTH "\n"
    # and U+2028 in one character class, so one_line() still flattens the
    # forged name onto one line for either, and Python's str.splitlines(),
    # which both verdict_and_evidence below and this eval's own "physical"
    # check use to read stdout, still treats U+2028 as a line boundary exactly
    # as it treats "\n": that half is genuinely the same mechanism, on every
    # platform. What is NOT the same: a literal "\n" is also Unicode category
    # Cc, so one_line()'s separate Cc/Cf/Cs visible-escape loop would catch it
    # on its own even if _LINE_BREAKS regressed, a redundant second line of
    # defense; U+2028 is category Zl, outside that loop's three categories, so
    # THIS fixture is caught by _LINE_BREAKS alone. That makes it a strictly
    # MORE sensitive probe of _LINE_BREAKS specifically, not an equivalent
    # substitution: dropping U+2028 from _LINE_BREAKS while leaving "\r\n" in
    # place turns this fixture red, where a same-shape literal-"\n" fixture
    # would stay green under that exact mutation, saved by the Cc loop it
    # never gets to exercise on this platform-portable path. The forged name
    # still renders as two lines if the escaping regresses; only the on-disk
    # name changed.
    forged = ("ok   datamodel  PASS     2 entity(ies), each with a system of record "
              "[severity: gate]")
    doss = os.path.join(root, forged)
    try:
        os.makedirs(doss)
        with open(os.path.join(doss, "05-data-model.md"), "w") as f:
            f.write("# Data model\n## Entities\n- Customer\n")
    except OSError as e:
        # The platform refuses to create the forging path component at
        # all (Windows: NotADirectoryError 20). A channel the filesystem
        # refuses to mount is closed, and the refusal is loud, not
        # silent: that is the honest state this case exists to prove.
        return "honest"
    env = dict(os.environ, SBE_DOSSIER_ROOT=root)
    r = subprocess.run([sys.executable, os.path.join(_REPO, "tools", "sbe_design.py"),
                        "datamodel", root], capture_output=True, text=True, timeout=120, env=env)
    meta = SourceFileLoader("tndc_pathforge",
                            os.path.join(HERE, "test_no_data_class.py")).load_module()
    verdict, line = meta.verdict_and_evidence(r.stdout, "datamodel")
    if verdict == "PASS":
        return "the forged directory-name line was read as the report's verdict: %r" % (line or "")[:120]
    physical = [l for l in r.stdout.splitlines() if l.startswith("  datamodel  PASS")]
    if physical:
        return "a forged physical line still reaches the byte stream: %r" % physical[0][:120]
    return "honest"


T2_ANSWERS = {"changes_contract": True, "crosses_boundary": False,
              "reversible_under_hour": True, "touches_sensitive": False, "consumers": "some"}
T1_ANSWERS = {"changes_contract": False, "crosses_boundary": True,
              "reversible_under_hour": True, "touches_sensitive": False, "consumers": "none"}
T3_ANSWERS = {"changes_contract": True, "crosses_boundary": True,
              "reversible_under_hour": False, "touches_sensitive": True, "consumers": "many"}
PURPOSE = "# Purpose\nProblem: x\nUsers: y\nSuccess: z\nNon-goals: w\nIf wrong: v\n"
# A behaviour table coherent with the generic PURPOSE shape above (it shares
# "problem", "success" and "wrong", the only real subject words PURPOSE
# carries), for the many T1 fixtures below that pair PURPOSE with the tier's
# other now-required artifact, 08-behaviour.md.
BEHAVIOUR = ("# Behaviour\n## Rules\n"
            "| ID | Starting point | Trigger | Required outcome | Proof |\n"
            "|---|---|---|---|---|\n"
            "| B1 | A user who reports a problem | The system logs the report | The problem is "
            "marked resolved on success | Unit test: report a problem, resolve it, assert the "
            "record shows success |\n"
            "| B2 | A problem that cannot be resolved | The user retries | The system reports what "
            "went wrong | Unit test: force a retry, assert the response explains what went "
            "wrong |\n")
# A behaviour table coherent with the technology-map fixture used by the
# unsegmentable and cross-script coherence cases below (shares "payment-router",
# "settlement" and "ledger-writer" with it), so 08-behaviour.md, now required at
# T1, passes its own coherence check there and the Japanese purpose fixture's
# own unmeasured result is what the case is actually pinning.
BEHAVIOUR_TECH = ("# Behaviour\n## Rules\n"
                  "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                  "|---|---|---|---|---|\n"
                  "| B1 | A payment queued at the payment-router | The settlement queue drains "
                  "it | The ledger-writer records the settlement | Integration test: enqueue a "
                  "payment at the payment-router, drain the settlement queue, assert the "
                  "ledger-writer records the settlement |\n"
                  "| B2 | A settlement the ledger-writer rejects | The settlement queue retries "
                  "it | The payment-router raises an alert after three attempts | Failover test: "
                  "force a ledger-writer rejection, retry from the settlement queue, assert the "
                  "payment-router alerts after three attempts |\n")


# check_behaviour lived unreachable from this file until "behaviour" joined
# DESIGN_CLASSES above: klass names outside that tuple dispatch to
# tools/sbe_gate.py instead, which has no "behaviour" gate, so every case here
# would have silently read "?" forever. These two pin the check now that the
# dispatch reaches it.
#
# Required outcome is an evidence field, the same population an override
# reason and a system of record belong to, so "TBD" there is a vacuous answer
# rather than a domain word. Proof is filled and true, and Starting point and
# Trigger carry real content, so the only thing this fixture can be caught by
# is the vacuous outcome: run against a copy of check_behaviour with the
# incomplete-row guard removed and this case passes clean, which is what
# calibrated the isolation.
@case("a-behaviour-row-with-a-vacuous-required-outcome-is-caught", "behaviour", "FAIL")
def d_behaviour_vacuous(root):
    write(root, "08-behaviour.md", "# Behaviour\n## Rules\n"
                                   "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                                   "|---|---|---|---|---|\n"
                                   "| B1 | A user who reports a problem | The system logs the "
                                   "report | TBD | Unit test: report a problem, resolve it, assert "
                                   "the record shows success |\n")


# The verification plan is the other half of this check: a row deleted from
# 08-behaviour.md while 07-verification.md still cites its id is a proof
# obligation pointing at nothing. B1 here is answered and complete on its
# own, so the only path to FAIL is the citation of B9, which no row supplies.
@case("a-verification-row-citing-a-deleted-behaviour-id-is-caught", "behaviour", "FAIL")
def d_behaviour_deleted_id(root):
    write(root, "08-behaviour.md", "# Behaviour\n## Rules\n"
                                   "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                                   "|---|---|---|---|---|\n"
                                   "| B1 | A user who reports a problem | The system logs the "
                                   "report | The problem is marked resolved on success | Unit "
                                   "test: report a problem, resolve it, assert the record shows "
                                   "success |\n")
    write(root, "07-verification.md", "# Verification\n- B1: covered by the unit test above.\n"
                                      "- B9: covered by a manual smoke test.\n")


@case("missing-required-artifact-caught", "artifacts", "FAIL")
def d1(root):
    write(root, "00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "01-purpose.md", PURPOSE)


@case("complete-t1-dossier-passes", "artifacts", "PASS")
def d2(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "01-purpose.md", PURPOSE)
    write(root, "08-behaviour.md", BEHAVIOUR)


@case("an-all-common-word-artifact-is-filler-not-an-unmeasured-script", "artifacts", "FAIL")
def d2b(root):
    # The provoked refusal: an artifact written entirely in stopwords yielded
    # the empty term set, took the branch whose stated purpose is non-Latin
    # scripts, and the gate FAIL became a PASS whose printed reason blamed the
    # writing system of an ASCII English file. Two defects, both pinned here:
    # the sentence must be true about the file (the extractor segmented it
    # fine; the words were common), and a refusal must never upgrade a FAIL.
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "04-technology-map.md", "# Technology map\nThe payment-router service talks to "
                                        "the ledger-writer component over the settlement queue.\n")
    write(root, "01-purpose.md", "# Purpose\nThe system changes the data and the process uses the "
                                 "same design document.\nEvery step of the plan will update the "
                                 "record and the result of the work.\n")


@case("an-artifact-the-extractor-cannot-segment-is-nodata-not-pass", "artifacts", "NO-DATA")
def d2c(root):
    # Coherence genuinely not measurable: no run of three or more word
    # characters anywhere, so the extractor reads no word at all. The honest
    # verdict is NO-DATA naming the limit, never a PASS whose leading clause
    # asserts the coherence that was not measured.
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "04-technology-map.md", "# Technology map\nThe payment-router service talks to "
                                        "the ledger-writer component over the settlement queue.\n")
    write(root, "01-purpose.md", "# Purpose\n目的 は 別 の 話 で あり この 設計 と は 関係 ない 事 を "
                                 "記す 熟成 の 手入 れ に つい て 記す\n")
    write(root, "08-behaviour.md", BEHAVIOUR_TECH)


@case("cross-script-siblings-are-nodata-not-pass", "artifacts", "NO-DATA")
def d2d(root):
    # The sibling branch of the same escape: a wholly-Japanese purpose beside a
    # wholly-English map shares no script, so the shared-word test cannot run.
    # Unmeasured is NO-DATA here exactly as it is everywhere else in the tool.
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "04-technology-map.md", "# Technology map\nThe payment-router service talks to "
                                        "the ledger-writer component over the settlement queue.\n")
    write(root, "01-purpose.md", "# Purpose\n支払経路の設計目的を述べる。\n"
                                 "決済台帳と照合処理の関係を整理する。\n"
                                 "部分的な失敗を検知する仕組みを定める。\n")
    write(root, "08-behaviour.md", BEHAVIOUR_TECH)


@case("adr-without-rejected-alternatives-caught", "adr", "FAIL")
def d3(root):
    write(root, "03-adr.md", "# ADR\n## Decision\nUse a modular monolith.\n## Consequences\nOne deploy.\n")


@case("adr-with-alternatives-and-flip-passes", "adr", "PASS")
def d4(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\nteam size, consistency\n"
                             "## Options considered\n### Rejected: microservices\nToo much ops load.\n"
                             "### Rejected: single script\nNo isolation.\n"
                             "## Decision\nModular monolith.\n## Consequences\nOne deploy.\n"
                             "## What would flip this\nMore than three deploying teams.\n")


@case("unspecified-cardinality-caught", "datamodel", "FAIL")
def d5(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "## Relationships\n- Customer to Order: ?\n")


@case("entity-without-system-of-record-caught", "datamodel", "FAIL")
def d6(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer\n"
                                    "## Relationships\n- Customer to Order: one-to-many\n")


@case("sound-data-model-passes", "datamodel", "PASS")
def d7(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "- Order: system of record OMS\n"
                                    "## Relationships\n- Customer to Order: one-to-many, optional\n")


@case("orphan-diagram-node-caught", "diagrams", "FAIL")
def d8(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Customer --> Invoice\n```\n")


@case("consistent-diagram-passes", "diagrams", "PASS")
def d9(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "- Order: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Customer -->|places| Order\n```\n")


# 10. A node written with bracket shape syntax that appears nowhere else in the
# dossier must still be caught as an orphan (regex must capture bracket nodes).
@case("orphan-node-in-bracket-syntax-caught", "diagrams", "FAIL")
def d10(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  FakeVendor[Not A Real Entity] --> Order\n```\n")


# 11. A legitimate erDiagram (entity-relationship syntax) whose entities all
# appear in the data model must pass, not hard-fail with "no diagram nodes found".
@case("er-diagram-nodes-recognized-passes", "diagrams", "PASS")
def d11(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- CUSTOMER: system of record CRM\n"
                                    "- ORDER: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nerDiagram\n  CUSTOMER ||--o{ ORDER : places\n```\n")


# 13. A diagram with no data model to trace against must be NO-DATA, never PASS:
# with an empty known-entity set every invented node looks traceable, which is the
# defect L5 exists to catch.
@case("diagram-without-data-model-is-nodata", "diagrams", "NO-DATA")
def d13(root):
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Invented --> AlsoInvented\n```\n")


# 12. An intake file with no tier key must be NO-DATA, never a silent PASS.
@case("missing-tier-is-nodata", "artifacts", "NO-DATA")
def d12(root):
    write(root, "00-intake.json", {"answers": {}, "override": None})


# The trust boundary the whole design gate sits on: the tier written in the file
# against the tier its own answers compute. Trusting the field made every artifact
# requirement two keystrokes away.
@case("tier-lowered-by-hand-without-a-reason-caught", "artifacts", "FAIL")
def d14(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": T3_ANSWERS,
                                   "override": None, "override_reason": None})


@case("declared-override-with-a-reason-is-honoured", "artifacts", "PASS")
def d15(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T3_ANSWERS, "override": "T1",
                                   "override_reason": "read-only backfill, agreed with the data owner"})
    write(root, "01-purpose.md", PURPOSE)
    write(root, "08-behaviour.md", BEHAVIOUR)


@case("intake-without-answers-is-nodata", "artifacts", "NO-DATA")
def d16(root):
    write(root, "00-intake.json", {"tier": "T3", "override": None})


@case("malformed-intake-caught", "artifacts", "FAIL")
def d17(root):
    write(root, "00-intake.json", "{not json at all\n")


# An ADR listing its alternatives as bullets under one heading is the natural
# authoring form and must pass; two empty headings must not.
@case("bulleted-rejected-alternatives-pass", "adr", "PASS")
def d18(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\nlatency, freshness\n"
                             "## Rejected alternatives\n"
                             "- Synchronous API call: ties checkout latency to warehouse availability.\n"
                             "- Nightly batch: fails the freshness requirement.\n"
                             "## Decision\nPublish to a queue.\n## Consequences\nOne more moving part.\n"
                             "## What would flip this\nSub-second freshness becomes a requirement.\n")


@case("empty-rejected-headings-caught", "adr", "FAIL")
def d19(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\nc\n## Rejected\n## Rejected\n"
                             "## Decision\nd\n## Consequences\ne\n## What would flip this\nf\n")


# Prose that says the opposite of what the rule requires must not pass on a
# substring match: a stated TBD is not a system of record, and "one-to-many-ish"
# is not a cardinality.
@case("undecided-system-of-record-caught", "datamodel", "FAIL")
def d20(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record: TBD\n"
                                    "- Order: no system of record known yet\n"
                                    "## Relationships\n- Customer to Order: one-to-many\n")


@case("hedged-cardinality-caught", "datamodel", "FAIL")
def d21(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "## Relationships\n- Customer to Order: this is a one-to-many-ish thing we have not decided\n")


# A dossier copied from templates/dossier and never edited must not clear the
# design gate on someone else's example.
@case("unedited-copied-template-caught", "placeholder", "FAIL")
def d22(root):
    for name in os.listdir(TEMPLATES):
        if name.endswith(".md") and name[0].isdigit():
            write(root, name, open(os.path.join(TEMPLATES, name)).read())
    write(root, "00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})


@case("edited-dossier-has-no-unfilled-marker", "placeholder", "PASS")
def d23(root):
    write(root, "01-purpose.md", PURPOSE)


@case("no-artifacts-at-all-is-nodata-not-a-pass", "placeholder", "NO-DATA")
def d24(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": {"reversible_under_hour": True,
                                                             "consumers": "none"}, "override": None})


# The CI wiring case: the documented layout puts the dossier in design/<project>/
# while CI runs from the repository root. Checking only <root>/00-intake.json
# reported NO-DATA and exit 0 with a full dossier two directories away.
@case("dossier-in-a-subdirectory-is-found", "artifacts", "FAIL")
def d25(root):
    write(root, "design/orders/00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "design/orders/01-purpose.md", PURPOSE)


@case("complete-dossier-in-a-subdirectory-passes", "artifacts", "PASS")
def d26(root):
    write(root, "design/orders/00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "design/orders/01-purpose.md", PURPOSE)
    write(root, "design/orders/08-behaviour.md", BEHAVIOUR)


SCORE = os.path.join(HERE, "..", "tools", "sbe_score.py")


def run_score_lints(args):
    out = subprocess.run([sys.executable, SCORE] + args, capture_output=True, text=True)
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "silent-failure-lints":
            return parts[1]
    return "?"


# A lint run that opened no file reported PASS with the evidence word "clean",
# which asserts the opposite of what happened. Nothing scanned is NO-DATA.
@case("lints-with-no-root-are-nodata-not-clean", "lints", "NO-DATA")
def s1(root):
    return run_score_lints([])


@case("lints-on-a-mistyped-path-are-caught", "lints", "FAIL")
def s2(root):
    return run_score_lints([os.path.join(root, "no-such-dir")])


@case("lints-on-real-source-name-what-was-scanned", "lints", "PASS")
def s3(root):
    write(root, "ok.py", "def f():\n    return 1\n")
    return run_score_lints([root])


@case("lints-catch-a-swallowed-error", "lints", "FAIL")
def s4(root):
    write(root, "bad.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    return run_score_lints([root])


@case("lints-do-not-fail-a-repo-over-a-virtualenv-with-an-unusual-name", "lints", "PASS")
def s5(root):
    # The one gate a .sbe-exempt cannot waive, failing a team over code it did not
    # write and cannot fix. `.venv` and `venv` were the entire skip list here, so
    # `.venv-whisper` (or `.tox`, or `env39`) put every vendored file through it:
    # 1127 hits in 8109 files against a real repository. Vendored code is now
    # skipped by detection, not by exact name.
    write(root, "src/app.py", "def f():\n    return 1\n")
    write(root, ".venv-whisper/lib/python3.9/site-packages/thirdparty.py",
          "try:\n    f()\nexcept Exception:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    return run_score_lints([root])


@case("lints-still-read-the-repos-own-source-beside-a-virtualenv", "lints", "FAIL")
def s6(root):
    # The control for the case above: skipping vendored trees must not become a
    # way to hide the repository's own swallowed error one directory over.
    write(root, ".venv-whisper/pyvenv.cfg", "home = /usr/bin\n")
    write(root, ".venv-whisper/lib/thirdparty.py",
          "try:\n    f()\nexcept Exception:\n    pass\n")  # sbe: allow-silent lint FIXTURE, see above
    write(root, "src/app.py",
          "try:\n    f()\nexcept Exception:\n    pass\n")  # sbe: allow-silent lint FIXTURE, see above
    return run_score_lints([root])


_decide = SourceFileLoader("sbe_decide", os.path.join(HERE, "..", "tools", "sbe_decide.py")).load_module()
_TABLES = json.load(open(os.path.join(HERE, "..", "tables", "architecture.json")))


@case("small-team-strong-consistency-is-not-microservices", "decide", "modular monolith")
def a1(root):
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": 1, "consistency": "strong",
                                             "ops_maturity": "low", "failure_isolation": "low"})
    return r["recommendation"]


@case("many-teams-high-isolation-is-services", "decide", "services")
def a2(root):
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": 6, "consistency": "eventual",
                                             "ops_maturity": "high", "failure_isolation": "high"})
    return r["recommendation"]


@case("recommendation-always-names-a-flip-condition", "decide", "yes")
def a3(root):
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": 1, "consistency": "strong",
                                             "ops_maturity": "low", "failure_isolation": "low"})
    return "yes" if r["flip_condition"] and len(r["alternatives"]) == 2 else "no"


# Discriminates services from event-driven on deploying_teams=3, which only
# event-driven's range covers (services needs 4+). The many-teams fixture above
# ties services and event-driven and only resolves by table-order tie-break; if
# the deploying_teams criterion were broken this fixture would fail where that
# one would not.
@case("low-team-count-high-isolation-is-event-driven", "decide", "event-driven")
def a4(root):
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": 3, "consistency": "eventual",
                                             "ops_maturity": "high", "failure_isolation": "high"})
    return r["recommendation"]


# An empty context contributes zero criteria: the recommender must say NO-DATA,
# never a confident guess dressed up as a recommendation.
@case("empty-context-is-no-data", "decide", "NO-DATA")
def a5(root):
    r = _decide.recommend(_TABLES["shape"], {})
    return r["verdict"]


# A non-numeric value for a number criterion must not crash; it must land in
# unrecognized, just like an unrecognized choice value.
@case("non-numeric-number-criterion-is-unrecognized", "decide", "unrecognized")
def a6(root):
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": "notanumber"})
    return "unrecognized" if r["unrecognized"] and not r["deciding_criteria"] else "fail"


# Asking for a decision family that has no table must name the tables that ship,
# not raise KeyError. One table exists; the laws say so, and so does the tool.
@case("unknown-table-key-names-what-ships", "decide", "named-error")
def a7(root):
    table, err = _decide.load_table(os.path.join(HERE, "..", "tables", "architecture.json"), "storage")
    return "named-error" if table is None and "shape" in err else "fail"


@case("missing-table-file-names-itself", "decide", "named-error")
def a8(root):
    table, err = _decide.load_table(os.path.join(root, "nope.json"), "shape")
    return "named-error" if table is None and "cannot read table file" in err else "fail"


# ---------------------------------------------------------------------------
# Second wave. The defect these pin is one defect: a verdict that asserts
# something the tool never inspected. The first wave fixed six named instances
# of it and a re-review found four more alive in the files that wave edited, so
# every fixture below is either a new instance of that class or the positive
# path of a behaviour that had none.
# ---------------------------------------------------------------------------

def gate_line(root, klass, cwd=None):
    """The whole verdict line, so a fixture can pin the EVIDENCE and not only the
    verdict. A gate that says PASS for the right reason and a gate that says PASS
    while claiming work nobody did are the same string to a verdict-only test.

    `cwd` lets a caller invoke the gate the way the docs tell a reader to invoke
    it, from inside the change directory with `.` as the root. Every verdict now
    names the root it examined, and a scratch directory's name is different on
    every run, so a guide can only quote a line produced this way."""
    script = DESIGN if klass in DESIGN_CLASSES else GATE
    out = subprocess.run([sys.executable, os.path.abspath(script), klass, root],
                         capture_output=True, text=True, cwd=cwd)
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == klass:
            return line.strip()
    return ""


def design_run(root, env=None):
    """--strict from a search root: 'blocked' or 'clear'. Some behaviour only
    exists at the top level (which dossiers are walked, what --strict does with
    them) and a per-check runner cannot see it."""
    e = dict(os.environ)
    e["SBE_DOSSIER_ROOT"] = ""
    e.update(env or {})
    out = subprocess.run([sys.executable, DESIGN, "--strict", root],
                         capture_output=True, text=True, env=e)
    return "blocked" if out.returncode else "clear"


# 14. The migration gate asserted "with matching row counts" over receipts that
# recorded none: the comparison was skipped when the key was absent and the
# evidence string said it happened anyway.
@case("migration-with-no-row-counts-is-nodata", "migration", "NO-DATA")
def m1(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "rehearse-991"}})


@case("half-a-row-count-is-caught", "migration", "FAIL")
def m2(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job_1"},
        "row_counts": {"before": 100}})


@case("empty-migration-receipt-is-nodata", "migration", "NO-DATA")
def m3(root):
    write(root, "migration-receipt.json", {})


# A rehearsal id that is a boolean satisfied a bare truthiness test while the
# evidence line called it resolvable.
@case("non-string-rehearsal-id-is-caught", "migration", "FAIL")
def m4(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": True},
        "row_counts": {"before": 100, "after_reverse": 100}})


@case("sound-migration-evidence-counts-what-it-compared", "evidence", "counted")
def m5(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job_8842"},
        "row_counts": {"before": 100, "after_reverse": 100}})
    line = gate_line(root, "migration")
    return "counted" if "1 row-count comparison(s) matched" in line else line


# 15. Valid JSON of the wrong TYPE crashed the tool inside the gate loop, so the
# crashing gate and every gate after it printed no verdict at all and advisory
# mode exited 0. A gate that vanishes is worse than a gate that fails.
@case("wrong-type-manifest-is-caught", "numbers", "FAIL")
def w1(root):
    write(root, "numbers-manifest.json", "[{\"figures\": []}]\n")


@case("wrong-type-ran-receipt-is-caught", "ran", "FAIL")
def w2(root):
    write(root, "ran-receipt.json", "\"a string\"\n")


@case("wrong-type-migration-receipt-is-caught", "migration", "FAIL")
def w3(root):
    write(root, "migration-receipt.json", "42\n")


@case("non-object-figure-entry-is-caught", "numbers", "FAIL")
def w4(root):
    write(root, "numbers-manifest.json", {"figures": ["just a string"]})


@case("a-crashing-check-fails-instead-of-disappearing", "guard", "FAIL")
def w5(root):
    if _checks is None:
        return _CHECKS_IMPORT_ERROR

    def boom(_):
        raise RuntimeError("the receipt reader exploded")
    verdict, ev = _checks.run_guarded("boom", boom, root)
    return verdict if "exploded" in ev else "evidence lost the exception"


# Severity is declared at write time, or the check does not exist: the split
# between a blocking check and a graded one used to live in which FILE a check
# was written into, which no reader could see without tracing the call.
@case("a-check-without-a-declared-severity-is-refused", "guard", "refused")
def w5a(root):
    if _checks is None:
        return _CHECKS_IMPORT_ERROR
    try:
        _checks.Check(lambda _: ("PASS", "x"), reads=("f",), kind="text",
                      full_fixture={"files": {"f": "worked example\n"}})
    except ValueError as e:
        return "refused" if "severity" in str(e) else "refused for the wrong reason: %s" % e
    return "registered with no severity at all"


@case("a-severity-outside-gate-or-soft-is-refused", "guard", "refused")
def w5b(root):
    if _checks is None:
        return _CHECKS_IMPORT_ERROR
    try:
        _checks.Check(lambda _: ("PASS", "x"), reads=("f",), kind="text", severity="advisory",
                      full_fixture={"files": {"f": "worked example\n"}})
    except ValueError as e:
        return "refused" if "severity" in str(e) else "refused for the wrong reason: %s" % e
    return "registered under a severity the exit-code mapping does not define"


@case("a-planted-extra-file-fails-the-install-check", "guard", "planted-file-caught")
def w5d(root):
    # verify-install.sh checks both directions. The one-direction version of
    # this script reports PASSED with a planted file still present, because an
    # added file is neither a MISMATCH nor a MISSING; this fixture plants one
    # and requires exit 1 naming it as EXTRA.
    import shutil
    sdir = os.path.join(root, "scripts")
    os.makedirs(sdir)
    for s in ("checksums.sh", "verify-install.sh"):
        shutil.copy(os.path.join(_REPO, "scripts", s), os.path.join(sdir, s))
    write(root, "tools/ok.py", "def f():\n    return 1\n")
    write(root, "README.md", "a tiny install\n")
    gen = subprocess.run(["sh", os.path.join(sdir, "checksums.sh"), "CHECKSUMS.sha256"],
                         capture_output=True, text=True, cwd=root, timeout=60)
    if gen.returncode != 0:
        return "manifest generation failed: %s" % gen.stderr[:80]
    clean = subprocess.run(["sh", os.path.join(sdir, "verify-install.sh")],
                           capture_output=True, text=True, cwd=root, timeout=60)
    if clean.returncode != 0:
        return "clean tree did not verify: %s" % (clean.stdout + clean.stderr)[:120]
    write(root, "planted.py", "import os\n")
    planted = subprocess.run(["sh", os.path.join(sdir, "verify-install.sh")],
                             capture_output=True, text=True, cwd=root, timeout=60)
    if planted.returncode != 1:
        return "planted file exited %d, not 1" % planted.returncode
    return ("planted-file-caught" if "EXTRA:     planted.py" in planted.stdout
            else "exit 1 without naming the extra file")


@case("every-verdict-line-names-its-severity", "gaterun", "printed")
def w5c(root):
    # The declaration is only visible if the output carries it: a severity a
    # reader can see only in the source is the file-location split with a new name.
    out = subprocess.run([sys.executable, GATE, root], capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.startswith("  ")]
    if not lines:
        return "no verdict lines at all"
    missing = [l for l in lines if "[severity: gate]" not in l and "[severity: soft]" not in l]
    return "printed" if not missing else "unlabelled: %s" % missing[0][:60]


# 16. The empty-manifest fix held only while the empty manifest was the ONLY
# one: the note naming it was collected and then discarded unless every manifest
# was empty, so one good deliverable anywhere in the tree restored the old
# behaviour for all the rest. A repository with more than one deliverable is the
# normal case.
GOOD_FIGURE = {"figures": [{
    "label": "gmv", "snapshot_id": "snap_2026_07",
    "query": "SELECT SUM(amount) FROM orders", "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
    "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]}
GOOD_CHECK = {"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}]}


@case("one-empty-manifest-among-good-ones-is-nodata", "numbers", "NO-DATA")
def p1(root):
    write(root, "a/numbers-manifest.json", GOOD_FIGURE)
    write(root, "b/numbers-manifest.json", {"figures": []})


@case("one-misspelled-manifest-among-good-ones-is-nodata", "numbers", "NO-DATA")
def p2(root):
    write(root, "a/numbers-manifest.json", GOOD_FIGURE)
    write(root, "b/numbers-manifest.json", {"figuers": [{"label": "typo key"}]})


@case("one-empty-ran-receipt-among-good-ones-is-nodata", "ran", "NO-DATA")
def p3(root):
    write(root, "a/ran-receipt.json", GOOD_CHECK)
    write(root, "b/ran-receipt.json", {"checks": []})


@case("two-good-manifests-still-pass", "numbers", "PASS")
def p4(root):
    write(root, "a/numbers-manifest.json", GOOD_FIGURE)
    write(root, "b/numbers-manifest.json", GOOD_FIGURE)


# 17. The approval gate's Reviewed-in path is an unvalidated regex match against
# a commit message the agent writes. The law used to call approval "bound to an
# identity the agent cannot forge". The tool is what it is; the evidence line now
# says so, and this pins that it keeps saying so.
@case("reviewed-in-evidence-does-not-claim-a-resolved-review", "evidence", "disclosed")
def ap1(root):
    write(root, "APPROVAL", "touches partner billing path\n")
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "payout batching\n\nReviewed-in: PR-99999"], check=True)
    line = gate_line(root, "approval")
    return "disclosed" if "does not resolve the id" in line else line


# 18. The one-file-deletion bypass. Seven filled artifacts with the intake
# removed were never opened from the repository root, and --strict exited 0.
@case("dossier-without-its-intake-is-caught", "artifacts", "FAIL")
def x1(root):
    write(root, "01-purpose.md", PURPOSE)
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n")


@case("dossier-without-its-intake-in-a-subdirectory-is-found", "designrun", "blocked")
def x2(root):
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/03-adr.md", "# ADR\n")
    return design_run(root)


@case("an-exempt-dossier-does-not-block-an-unrelated-merge", "designrun", "clear")
def x3(root):
    write(root, "design/legacy/00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "design/legacy/.sbe-exempt",
          "checks: artifacts, adr, datamodel, diagrams, placeholder\n"
          "reason: closed two years ago, kept for history\n")
    return design_run(root)


# 19. A one-character override reason waived the entire dossier requirement.
@case("one-character-override-reason-is-not-an-override", "artifacts", "FAIL")
def o1(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": T3_ANSWERS,
                                   "override": "T0", "override_reason": "x"})


@case("override-reason-that-says-tbd-is-not-an-override", "artifacts", "FAIL")
def o2(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": T3_ANSWERS,
                                   "override": "T0", "override_reason": "  tbd "})


@case("override-field-disagreeing-with-the-tier-is-caught", "artifacts", "FAIL")
def o3(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T3_ANSWERS, "override": "T2",
                                   "override_reason": "read-only backfill, agreed with the data owner"})
    write(root, "01-purpose.md", PURPOSE)


# The positive path of the tier recompute had no fixture that could fail: with a
# valid reason the written tier is used, which is what the code did before the
# recompute existed too. What the recompute adds is the DISCLOSURE, so that is
# what this pins.
@case("an-honoured-override-names-the-tier-it-was-lowered-from", "evidence", "labelled")
def o4(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T3_ANSWERS, "override": "T1",
                                   "override_reason": "read-only backfill, agreed with the data owner"})
    write(root, "01-purpose.md", PURPOSE)
    write(root, "08-behaviour.md", BEHAVIOUR)
    line = gate_line(root, "artifacts")
    return "labelled" if ("lowering the tier to T1 from computed T3" in line
                          and line.split()[1] == "PASS") else line


# 20. `touch 01-purpose.md` cleared tier T1, and the placeholder check passed the
# same empty file because a file with nothing in it carries no marker.
@case("zero-byte-artifact-does-not-clear-a-tier", "artifacts", "FAIL")
def z1(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "01-purpose.md", "")


@case("zero-byte-artifact-is-not-an-edited-dossier", "placeholder", "FAIL")
def z2(root):
    write(root, "01-purpose.md", "")


# 21. The data model check was wrong in both directions: an honest bullet list
# outside the entity section became entities with no source, and an entity name
# carrying a hyphen or a dot was dropped from the set the PASS line asserted over.
@case("an-honest-notes-list-is-not-a-set-of-entities", "datamodel", "PASS")
def e1(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Notes\n- All timestamps are stored in UTC\n- Soft deletes are used everywhere\n"
          "## Relationships\n- Customer to Order: one-to-many\n")


@case("a-hyphenated-entity-with-no-source-is-not-dropped", "datamodel", "FAIL")
def e2(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Subscriber: system of record: the identity service.\n"
          "- payment-token: the stored card token. Nobody knows who owns this.\n"
          "- pii.profile: personal data. Nobody knows who owns this either.\n")


# 22. Requiring every diagram node to be an ENTITY failed the diagrams the
# template asks for at T3, and the published escape was to add services to the
# conceptual data model, which corrupts the model to satisfy a diagram check.
@case("a-declared-runtime-component-is-traceable", "diagrams", "PASS")
def g1(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record: the order service.\n")
    write(root, "04-technology-map.md",
          "# Technology map\n| Component | Technology | Owner |\n|---|---|---|\n"
          "| EventBus | Managed queue | Platform team |\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Order --> EventBus\n```\n")


@case("an-invented-node-is-still-an-orphan-with-components-declared", "diagrams", "FAIL")
def g2(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record: the order service.\n")
    write(root, "04-technology-map.md",
          "# Technology map\n| Component | Technology | Owner |\n|---|---|---|\n"
          "| EventBus | Managed queue | Platform team |\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Order --> LedgerService\n```\n")


# 23. Prose is not a diagram. A markdown bullet after a heading read as an
# erDiagram relationship line and invented a node out of the heading.
@case("prose-outside-a-fence-is-not-a-diagram-node", "diagrams", "PASS")
def g3(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record: the order service.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Components\n- OrderQueue: described in prose, not a node\n"
          "The order flows to the warehouse.\n```mermaid\nflowchart LR\n  Order --> OrderQueue\n```\n")


# 24. `### Option A (rejected): ...` counted zero, and the convention appeared
# only in the template.
@case("rejected-named-inside-a-heading-counts", "adr", "PASS")
def r1(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\nlatency\n"
                             "### Option A (rejected): synchronous call\nTies checkout to the warehouse.\n"
                             "### Option B (rejected): nightly batch\nFails freshness.\n"
                             "## Decision\nQueue.\n## Consequences\nOne more part.\n"
                             "## What would flip this\nSub-second freshness.\n")


# 25. An artifact that legitimately CITES the marker FAILed with the false
# sentence "still the shipped template, unedited".
@case("an-artifact-citing-the-marker-is-not-unedited", "placeholder", "PASS")
def r2(root):
    write(root, "07-verification.md",
          "# Verification\nOne assertion here is that no dossier file still contains the string "
          "SBE-TEMPLATE-UNFILLED, which is how we know the template was edited.\n")


SCORE_CHECKS = ("ledger-coverage", "schema-2-uniform", "cache-economy", "vault-log-per-active-day",
                "fence-hygiene", "correction-latency", "budget-vs-tier", "prediction-seals",
                "felt-outcome-ratings", "review-cadence", "silent-failure-lints",
                "citation-inventory")


def score_check(name, vault, env=None, args=()):
    e = dict(os.environ)
    e.update({"BROTHERSBE_VAULT": vault, "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": "",
              "SBE_CITATION_ROOT": ""})
    e.update(env or {})
    out = subprocess.run([sys.executable, SCORE] + list(args), capture_output=True, text=True, env=e)
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == name:
            return parts[1]
    return "no-verdict-line"


def _ledger(root, body):
    write(root, "99-System/telemetry/outcomes.jsonl", body)
    return root


# 26. Three checks read `PASS if not <empty list>`, so a fresh checkout printed
# "0 pre-schema-2 lines remain PASS" over a corpus with no rows: the project's
# own law inverted inside the project's own scorer, in its default state.
@case("empty-ledger-is-not-schema-uniformity", "score", "NO-DATA")
def sc1(root):
    return score_check("schema-2-uniform", _ledger(root, ""))


@case("zero-active-days-is-not-a-session-log-pass", "score", "NO-DATA")
def sc2(root):
    return score_check("vault-log-per-active-day", _ledger(root, ""))


@case("zero-corrections-is-not-a-latency-pass", "score", "NO-DATA")
def sc3(root):
    return score_check("correction-latency", root)


@case("a-malformed-ledger-line-is-a-fail", "score", "FAIL")
def sc4(root):
    return score_check("schema-2-uniform", _ledger(root, "{not json\n"))


@case("a-ledger-line-of-the-wrong-type-is-a-fail", "score", "FAIL")
def sc5(root):
    return score_check("ledger-coverage", _ledger(root, "\"a string\"\n"))


@case("a-malformed-ledger-does-not-delete-the-other-checks", "score", "NO-DATA")
def sc6(root):
    # The crashing scorer took every check down with it, including ones reading
    # a different file entirely.
    return score_check("prediction-seals", _ledger(root, "\"a string\"\n"))


# The fence check appended the SKILL'S OWN STATE.md to the registry list on every
# run, so an operator with no registries configured got a green fence-discipline
# line sourced from the author's machine. Reproduced hermetically: the tools under
# test are copied beside a STATE.md carrying a tier-tagged fence line, which is
# exactly the layout that produced the phantom PASS.
@case("budget-vs-tier-does-not-score-the-skills-own-registry", "score", "NO-DATA")
def sc7(root):
    import shutil
    shutil.copytree(os.path.dirname(os.path.abspath(SCORE)), os.path.join(root, "tools"))
    write(root, "STATE.md",
          "# State\n## Fences\n- agent: builder | tier T2 | objective: ship the thing\n")
    e = dict(os.environ)
    e.update({"BROTHERSBE_VAULT": os.path.join(root, "vault"),
              "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": ""})
    out = subprocess.run([sys.executable, os.path.join(root, "tools", "sbe_score.py")],
                         capture_output=True, text=True, env=e)
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "budget-vs-tier":
            return parts[1]
    return "no-verdict-line"


@case("an-all-exempted-scan-is-not-clean", "score", "NO-DATA")
def sc8(root):
    src = os.path.join(root, "src")
    write(root, "src/swallowy.py",
          "def a():\n    try:\n        f()\n    except Exception:\n        pass  # sbe: allow-silent lint fixture, never executed\n")
    return score_check("silent-failure-lints", root, args=(src,))


@case("an-exemption-on-the-pass-line-is-honoured", "score", "PASS")
def sc9(root):
    src = os.path.join(root, "src")
    write(root, "src/swallowy.py",
          "def a():\n    try:\n        f()\n    except Exception:\n        pass  # sbe: allow-silent lint fixture, never executed\n")
    write(root, "src/clean.py", "def b():\n    return 1\n")
    return score_check("silent-failure-lints", root, args=(src,))


# The citation-inventory check: an external URL cited in the shipped docs must
# carry its scope (claim, population, date, limit) in docs/CITATIONS.md, or the
# gate fails. The defect class is this repository's own: its design document
# once deployed a 2024 benchmark score in the present tense over a page whose
# live leaderboard had moved past it. The check is offline by design, and the
# last eval here pins the sentence in which it says so.
_CITE_DOC = "# Doc\n\nA figure of 12 percent (https://example.com/study).\n"
_CITE_ENTRY = ("## https://example.com/study\n"
               "- claim: 12 percent of runs showed the behavior\n"
               "- population: 128 runs of one model under one harness\n"
               "- date: 2025-06-05\n"
               "- limit: single source, one model family\n")


def _cite_root(root, doc=None, inventory=None):
    if doc is not None:
        write(root, "README.md", doc)
    if inventory is not None:
        write(root, "docs/CITATIONS.md", inventory)
    return root


def _cite_line(cite_root):
    return _cite_line_at(cite_root, cite_root)


def _cite_line_at(cite_root, vault):
    e = dict(os.environ)
    e.update({"BROTHERSBE_VAULT": vault, "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": "",
              "SBE_CITATION_ROOT": cite_root})
    out = subprocess.run([sys.executable, SCORE], capture_output=True, text=True, env=e)
    return next((l for l in out.stdout.splitlines() if l.startswith("citation-inventory")), "")


@case("citation-url-without-inventory-entry-caught", "score", "FAIL")
def ci1(root):
    return score_check("citation-inventory", root,
                       env={"SBE_CITATION_ROOT": _cite_root(root, doc=_CITE_DOC)})


@case("citation-entry-with-a-placeholder-scope-field-caught", "score", "FAIL")
def ci2(root):
    hollow = _CITE_ENTRY.replace("- limit: single source, one model family\n", "- limit: TODO\n")
    return score_check("citation-inventory", root,
                       env={"SBE_CITATION_ROOT": _cite_root(root, doc=_CITE_DOC, inventory=hollow)})


@case("citation-entry-missing-a-scope-field-caught", "score", "FAIL")
def ci3(root):
    gutted = _CITE_ENTRY.replace("- population: 128 runs of one model under one harness\n", "")
    return score_check("citation-inventory", root,
                       env={"SBE_CITATION_ROOT": _cite_root(root, doc=_CITE_DOC, inventory=gutted)})


@case("citation-stale-inventory-entry-caught", "score", "FAIL")
def ci4(root):
    return score_check("citation-inventory", root,
                       env={"SBE_CITATION_ROOT": _cite_root(root, doc="# Doc\n\nNo link here.\n",
                                                            inventory=_CITE_ENTRY)})


@case("citation-covered-url-passes", "score", "PASS")
def ci5(root):
    return score_check("citation-inventory", root,
                       env={"SBE_CITATION_ROOT": _cite_root(root, doc=_CITE_DOC,
                                                            inventory=_CITE_ENTRY)})


@case("citation-verdict-states-its-offline-limit", "score", "stated")
def ci6(root):
    # The check can prove structure and coverage, never that a page still says
    # what the entry recorded; a verdict that omitted that limit would claim
    # more than the tool examined, so the sentence itself is pinned here.
    line = _cite_line(_cite_root(root, doc=_CITE_DOC, inventory=_CITE_ENTRY))
    if not line:
        return "no citation-inventory verdict line at all"
    return ("stated" if "never live page content" in line and "no network connection" in line
            else line)


# ---------------------------------------------------------------------------
# Third wave. One defect, one more shape of it. The first two waves modelled
# absent evidence as an absent FILE; every fixture below is a receipt that
# EXISTS, parses, carries every required key, and holds nothing in one of them.
# The rest are the mirror image: correct work that the gates were rejecting,
# because a gate that rejects correct work gets switched off, and a gate that is
# off catches less than a gate that is merely incomplete.
# ---------------------------------------------------------------------------

@case("blank-rerun-values-are-not-a-zero-drift-check", "numbers", "FAIL")
def v1(root):
    # `is None` was the only emptiness test, so two empty strings compared equal
    # and the evidence reported a re-derivation that compared nothing.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "revenue", "snapshot_id": "snap-1", "query": "select 1",
        "second_derivation": "select 2", "rerun": {"ran": True, "primary": "", "secondary": ""}}]})


@case("whitespace-rerun-values-are-not-a-zero-drift-check", "numbers", "FAIL")
def v2(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "revenue", "snapshot_id": "snap-1", "query": "select 1",
        "second_derivation": "select 2", "rerun": {"ran": True, "primary": " ", "secondary": " "}}]})


@case("a-whitespace-snapshot-id-is-not-a-pinned-read", "numbers", "FAIL")
def v3(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "revenue", "snapshot_id": "  ", "query": "select 1",
        "second_derivation": "select 2", "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


def _one_figure(root, query, second):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07-25", "query": query,
        "second_derivation": second, "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


@case("a-trailing-semicolon-does-not-make-a-second-derivation", "numbers", "FAIL")
def v4a(root):
    # Copying the query into the second field and adding a semicolon bought
    # "1 figure(s) each with a pinned, independently re-derived, zero-drift check",
    # the strongest sentence this tool prints. It is the lazy thing, not the
    # dishonest thing, which is exactly why the gate must not reward it.
    _one_figure(root, "SELECT SUM(amount) FROM orders", "SELECT SUM(amount) FROM orders;")


@case("a-trailing-comment-does-not-make-a-second-derivation", "numbers", "FAIL")
def v4b(root):
    _one_figure(root, "SELECT SUM(amount) FROM orders",
                "SELECT SUM(amount) FROM orders -- rerun 2026-07-25")


@case("a-block-comment-and-a-reindent-do-not-make-a-second-derivation", "numbers", "FAIL")
def v4c(root):
    _one_figure(root, "SELECT SUM(amount) FROM orders",
                "/* second pass */ select  sum(amount)\n  from orders")


@case("a-genuinely-different-second-derivation-still-passes", "numbers", "PASS")
def v4d(root):
    # The control. Normalising more text before comparing must not start failing
    # the honest case it exists to protect.
    _one_figure(root, "SELECT SUM(amount) FROM orders",
                "SELECT SUM(qty * price) FROM order_lines")


@case("a-figure-listed-twice-is-counted-once", "gaterun", "1 figure(s)")
def v4e(root):
    # "2 figure(s) each ..." over one object listed twice: the count in the
    # evidence line is what a reader takes as the amount of work checked.
    fig = {"label": "gmv", "snapshot_id": "snap-1", "query": "SELECT SUM(a) FROM t",
           "second_derivation": "SELECT SUM(b) FROM u",
           "rerun": {"ran": True, "primary": 5, "secondary": 5}}
    write(root, "numbers-manifest.json", {"figures": [fig, dict(fig)]})
    out = subprocess.run([sys.executable, GATE, "numbers", root], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.strip().startswith("numbers"):
            return " ".join(line.split("PASS")[-1].split()[:2]) if "PASS" in line else "not-a-pass"
    return "no-line"


@case("a-figure-with-no-label-is-caught", "numbers", "FAIL")
def v4(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "", "snapshot_id": "snap-1", "query": "select 1",
        "second_derivation": "select 2", "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


@case("a-whitespace-rerun-flag-is-not-a-rerun", "numbers", "FAIL")
def v5(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "revenue", "snapshot_id": "snap-1", "query": "select 1",
        "second_derivation": "select 2", "rerun": {"ran": " ", "primary": 5, "secondary": 5}}]})


@case("a-whitespace-second-derivation-is-not-independent", "numbers", "FAIL")
def v6(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "revenue", "snapshot_id": "snap-1", "query": "select 1",
        "second_derivation": "   ", "rerun": {"ran": True, "primary": 5, "secondary": 5}}]})


@case("blank-row-counts-are-not-a-matched-comparison", "migration", "FAIL")
def v7(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-1"},
        "row_counts": {"before": "", "after_reverse": ""}})


@case("a-whitespace-rehearsal-id-is-not-a-run-id", "migration", "FAIL")
def v8(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": " "},
        "row_counts": {"before": 100, "after_reverse": 100}})


@case("a-whitespace-restore-claim-is-not-a-restore", "migration", "FAIL")
def v9(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": " "},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-1"},
        "row_counts": {"before": 100, "after_reverse": 100}})


@case("a-recorded-check-with-no-name-is-caught", "ran", "FAIL")
def v10(root):
    write(root, "ran-receipt.json", {"checks": [{"name": " ", "exit_code": 0, "duration_ms": 812}]})


@case("booleans-are-not-an-exit-code-and-a-duration", "ran", "FAIL")
def v11(root):
    # {"exit_code": false} satisfied `!= 0` and {"duration_ms": true} satisfied a
    # truthiness test, so this passed as "a zero exit and a nonzero duration".
    write(root, "ran-receipt.json", {"checks": [
        {"name": "recon", "exit_code": False, "duration_ms": True}]})


@case("an-intake-with-blank-answers-is-nodata", "artifacts", "NO-DATA")
def v12(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": {k: "" for k, _ in _intake.QUESTIONS},
                                   "override": None})


@case("a-tier-that-requires-nothing-reports-nothing", "artifacts", "NO-DATA")
def v13(root):
    # "tier T0: every required artifact present" read exactly like a verified T3
    # line while nothing had been opened at all.
    write(root, "00-intake.json", {"tier": "T0", "answers": {
        "changes_contract": False, "crosses_boundary": False, "reversible_under_hour": True,
        "touches_sensitive": False, "consumers": "none"}, "override": None})


@case("an-intake-answering-no-in-words-does-not-collapse-a-t3-to-a-t0", "artifacts", "FAIL")
def v13b(root):
    # The whole of C1 in one file. Four honest booleans, one honest answer written
    # as the word the prompt asks for, and the tier field agreeing with what the
    # truthiness rule computed from it: tier T0, no artifact owed, every design
    # check NO-DATA, --strict --strict-waivers exit 0. The re-derivation whose job
    # is to catch a hand-edited tier agreed, because it made the same mistake.
    write(root, "00-intake.json", {"tier": "T0", "answers": {
        "changes_contract": False, "crosses_boundary": False,
        "reversible_under_hour": "no", "touches_sensitive": False,
        "consumers": "none"}, "override": None, "override_reason": None})


@case("an-intake-whose-consumers-value-nothing-recognizes-fails", "artifacts", "FAIL")
def v13c(root):
    write(root, "00-intake.json", {"tier": "T0", "answers": {
        "changes_contract": False, "crosses_boundary": False,
        "reversible_under_hour": True, "touches_sensitive": False,
        "consumers": "several"}, "override": None, "override_reason": None})


@case("an-honest-intake-written-entirely-in-y-n-still-passes", "artifacts", "PASS")
def v13d(root):
    # The other half of the same fix, and the half that decides whether the gate
    # survives contact with a team: an intake in the vocabulary the tool's own
    # prompt teaches, honest in every answer, must pass rather than be argued with.
    write(root, "00-intake.json", {"tier": "T1", "answers": {
        "changes_contract": "n", "crosses_boundary": "Y",
        "reversible_under_hour": "yes", "touches_sensitive": "no",
        "consumers": "None"}, "override": None, "override_reason": None})
    write(root, "01-purpose.md", PURPOSE)
    write(root, "08-behaviour.md", BEHAVIOUR)


@case("an-artifact-of-headings-only-does-not-clear-a-tier", "artifacts", "FAIL")
def v14(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "01-purpose.md", "# Purpose\n\n## Problem\n\n## Users\n")


@case("an-empty-criteria-heading-is-not-criteria", "adr", "FAIL")
def v15(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\n## Rejected alternatives\n"
                             "- Synchronous call: ties checkout to the ledger.\n"
                             "- Nightly batch: misses the freshness requirement.\n"
                             "## Decision\nQueue.\n## Consequences\nOne more part.\n"
                             "## What would flip this\nSub-second freshness.\n")


@case("a-one-letter-rejection-reason-is-not-a-reason", "adr", "FAIL")
def v16(root):
    write(root, "03-adr.md", "# ADR\n## Criteria\nlatency\n## Rejected alternatives\n- a\n- b\n"
                             "## Decision\nQueue.\n## Consequences\nOne more part.\n"
                             "## What would flip this\nSub-second freshness.\n")


@case("no-relationships-under-the-heading-is-nodata", "datamodel", "NO-DATA")
def v17(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "## Relationships\n")


@case("a-relationship-table-with-no-cardinality-is-caught", "datamodel", "FAIL")
def v18(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record CRM\n- Order: system of record OMS\n"
          "## Relationships\n| From | To | Cardinality |\n|---|---|---|\n| Customer | Order | not decided |\n")


@case("a-relationship-table-with-cardinality-passes", "datamodel", "PASS")
def v19(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record CRM\n- Order: system of record OMS\n"
          "## Relationships\n| From | To | Cardinality |\n|---|---|---|\n| Customer | Order | one-to-many |\n")


@case("labelled-mermaid-nodes-are-traced-by-their-label", "diagrams", "PASS")
def v20(root):
    # A[Customer] --> B[Order] is the single most common Mermaid idiom there is,
    # and it FAILed as two orphans named A and B.
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "- Order: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  A[Customer] --> B[Order]\n```\n")


@case("a-labelled-node-naming-nothing-is-still-an-orphan", "diagrams", "FAIL")
def v21(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  A[Customer] --> B[Invoice]\n```\n")


@case("a-sequence-diagram-is-a-diagram", "diagrams", "PASS")
def v22(root):
    # The shipped template tells a T2 author that this file wants a sequence
    # diagram, and the shipped check called one "a diagram artifact with no
    # diagram in it".
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "- Order: system of record OMS\n")
    write(root, "06-diagrams.md",
          "```mermaid\nsequenceDiagram\n  participant Customer\n  participant Order\n"
          "  Customer->>Order: places\n  Order->>Customer: confirms\n```\n")


@case("a-sequence-diagram-participant-can-still-be-an-orphan", "diagrams", "FAIL")
def v23(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n")
    write(root, "06-diagrams.md",
          "```mermaid\nsequenceDiagram\n  participant Customer\n  participant Warehouse\n"
          "  Customer->>Warehouse: places\n```\n")


@case("a-node-named-after-a-direction-keyword-is-not-dropped", "diagrams", "FAIL")
def v24(root):
    # Five nodes went in, one came out, and the verdict said "all traceable".
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record OMS\n")
    write(root, "06-diagrams.md",
          "```mermaid\nflowchart LR\n  Order --> TD\n  TD --> LR\n  LR --> BT\n  BT --> graph\n```\n")


@case("the-diagram-evidence-names-what-it-skipped", "evidence", "named")
def v25(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record CRM\n"
                                    "- Order: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  A[Customer] --> B[Order]\n```\n")
    line = gate_line(root, "diagrams")
    return "named" if ("flowchart LR" in line and "tokens read as diagram syntax" in line
                       and line.split()[1] == "PASS") else line


@case("a-gantt-chart-is-not-a-missing-diagram", "diagrams", "NO-DATA")
def v26(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Order: system of record OMS\n")
    write(root, "06-diagrams.md",
          "```mermaid\ngantt\n  title Rollout\n  section Phase 1\n  Migrate :a1, 2026-07-01, 30d\n```\n")


@case("an-er-diagram-still-passes", "diagrams", "PASS")
def v27(root):
    write(root, "05-data-model.md", "# Data model\n## Entities\n- CUSTOMER: system of record CRM\n"
                                    "- ORDER: system of record OMS\n")
    write(root, "06-diagrams.md", "```mermaid\nerDiagram\n  CUSTOMER ||--o{ ORDER : places\n```\n")


@case("a-zero-byte-exemption-does-not-waive-a-failing-dossier", "designrun", "blocked")
def v28(root):
    # `: > .sbe-exempt` turned two failing checks into exit 0, and the report
    # said ".sbe-exempt names why: no reason recorded".
    write(root, "design/proj/00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/.sbe-exempt", "")
    return design_run(root)


@case("a-thin-exemption-reason-does-not-waive-a-dossier", "designrun", "blocked")
def v29(root):
    write(root, "design/proj/00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/.sbe-exempt", "tbd\n")
    return design_run(root)


EXEMPTION = ("checks: artifacts, adr, datamodel, diagrams, placeholder, behaviour\n"
             "reason: closed two years ago, kept for history\n")


@case("an-exemption-is-reported-as-a-waiver-not-a-pass", "designrun", "waived")
def v30(root):
    write(root, "design/legacy/00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "design/legacy/.sbe-exempt", EXEMPTION)
    e = dict(os.environ)
    e["SBE_DOSSIER_ROOT"] = ""
    out = subprocess.run([sys.executable, DESIGN, root], capture_output=True, text=True, env=e)
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "dossier" and parts[2] == "WAIVED":
            named = all(c in line for c in DESIGN_CLASSES)
            return "waived" if named else "waiver-line-does-not-name-the-checks"
    return "no-waiver-line"


# An exemption that names no checks waives everything by default, which is an off
# switch rather than an exemption. Measured before this: three words of filler in
# a free-text file switched off all five design checks for a directory and
# --strict exited 0 with a T3 dossier missing six of seven artifacts.
@case("an-exemption-that-names-no-checks-does-not-waive", "designrun", "blocked")
def v30a(root):
    write(root, "design/proj/00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/.sbe-exempt",
          "this dossier was closed two years ago and is kept only for history\n")
    return design_run(root)


# An exemption scoped to one check leaves the other four running, so an author
# waiving `diagrams` on an archived dossier no longer also waives `artifacts`.
@case("a-scoped-exemption-waives-only-the-checks-it-names", "designrun", "blocked")
def v30b(root):
    write(root, "design/proj/00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/.sbe-exempt",
          "checks: diagrams\nreason: the diagrams moved to the architecture wiki in 2024\n")
    return design_run(root)


@case("an-exemption-naming-a-check-that-does-not-exist-does-not-waive", "designrun", "blocked")
def v30c(root):
    write(root, "design/proj/00-intake.json", {"tier": "T3", "answers": T3_ANSWERS, "override": None})
    write(root, "design/proj/01-purpose.md", PURPOSE)
    write(root, "design/proj/.sbe-exempt",
          "checks: everything\nreason: this dossier was archived when the team disbanded\n")
    return design_run(root)


# A waiver is not a pass, and CI must be able to act on it without reading prose.
@case("a-waiver-blocks-under-strict-waivers", "designrun", "blocked")
def v30d(root):
    write(root, "design/legacy/00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "design/legacy/.sbe-exempt", EXEMPTION)
    e = dict(os.environ)
    e["SBE_DOSSIER_ROOT"] = ""
    out = subprocess.run([sys.executable, DESIGN, "--strict", "--strict-waivers", root],
                         capture_output=True, text=True, env=e)
    return "blocked" if out.returncode else "clear"


@case("asking-for-a-table-family-by-name-names-the-tables-that-ship", "guard", "named")
def v30g(root):
    # L12 promises that asking for a table that does not exist names the tables
    # that do. `sbe_decide.py architecture`, which is how the sentence reads,
    # treated the argument as a file path and named no tables at all.
    out = subprocess.run([sys.executable, os.path.join(_REPO, "tools", "sbe_decide.py"),
                          "architecture"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if out.returncode == 0:
        return "exit 0 for a table family that has no table"
    return "named" if "Tables that ship: shape" in out.stdout else out.stdout.strip()[:80]


@case("the-meta-test-lint-sees-a-verdict-in-a-subpackage-and-in-a-lookup-table", "guard", "caught")
def v30f(root):
    """The honesty meta-test's own discovery, probed rather than read.

    Three holes, all proved by building the shadow check the file exists to
    forbid. Its walk was `os.listdir`, one level deep, so a registry in
    `tools/quality/extra.py` was invisible to the registry walk AND to the source
    lint while the summary line printed "20 checks discovered ... 0 failure(s)"
    over twenty-one. And the lint resolved a constant and a name but not a `BinOp`
    or a `Subscript`, so `"PA" + "SS"` and a `{"ok": "PASS"}` lookup returned the
    verdict from a function it never saw. A coverage count that reads complete is
    what makes each of those silent.
    """
    meta = SourceFileLoader("meta_probe", os.path.join(HERE, "test_no_data_class.py")).load_module()
    tools = os.path.join(root, "tools")
    os.makedirs(os.path.join(tools, "quality"))
    write(tools, "quality/extra.py",
          "def gate_shadow(root):\n"
          "    return \"PASS\", \"quality is fine (examined nothing at all)\"\n\n"
          "def gate_shadow_concat(root):\n"
          "    return \"PA\" + \"SS\", \"examined nothing\"\n\n"
          "_V = {\"ok\": \"PASS\"}\n\n"
          "def gate_shadow_dict(root):\n"
          "    return _V[\"ok\"], \"examined nothing\"\n")
    original = meta.TOOLS_DIR
    try:
        meta.TOOLS_DIR = tools
        found = {name for _fn, name, _why in meta.pass_returning_functions()}
        walked = set(meta.tool_sources())
    finally:
        meta.TOOLS_DIR = original
    want = {"gate_shadow", "gate_shadow_concat", "gate_shadow_dict"}
    if os.path.join("quality", "extra.py") not in walked:
        return "the walk never reached a module one directory down: %s" % sorted(walked)
    missing = sorted(want - found)
    return "caught" if not missing else "the lint did not see %s" % ", ".join(missing)


#: The product's CI wiring. The public export deliberately does not carry it
#: (docs/plan/EXPORT-ALLOWLIST.txt, the M6 note: "CI wiring not needed to
#: install or run the plugin"), so every eval that reads it has to say so in a
#: public clone instead of dying on FileNotFoundError. Measured on the v1.0.2
#: tag: four evals in this bed reported ERROR:FileNotFoundError for exactly
#: this reason, which reads as a broken product and is really a file the tree
#: was never meant to carry.
SHIPPED_WORKFLOW = ".github/workflows/brothersbe-gates.yml"


def _withheld(rel):
    """`NO-DATA: ...` naming `rel` when this tree does not carry it, else None.

    Same three-state honesty as PLATFORM-GAP in main(): counted apart, printed
    with its reason, never a pass and never a block. A tree that DOES carry the
    file gets None and the caller runs its real check unchanged, so this can
    never soften the hub, where every one of these files is present.
    """
    if os.path.exists(os.path.join(_REPO, rel)):
        return None
    return ("NO-DATA: this clone does not carry %s, so nothing was opened and "
            "this case proved nothing. An absent file is not a pass." % rel)


def _tracked_here(rels):
    """The subset of `rels` git tracks in this tree; empty when git cannot answer.

    Boundary call with an explicit failure path: a git that will not run
    returns an empty set, which makes the caller treat the paths as untracked
    and report rather than pass.
    """
    try:
        out = subprocess.run(["git", "-C", _REPO, "ls-files", "-z", "--"] + list(rels),
                             capture_output=True, text=True)
    except OSError:
        return set()
    if out.returncode != 0:
        return set()
    return set(p for p in out.stdout.split("\0") if p)


def _waiver_grep_pattern():
    """The pattern the shipped CI actually uses to decide a waiver happened.

    Read out of the workflow rather than restated here, so this eval is a check on
    the file that ships and not on a copy of it that agrees with itself.
    """
    import re as _re
    wf = open(os.path.join(_REPO, ".github/workflows/brothersbe-gates.yml"), errors="replace").read()
    m = _re.search(r"if grep -q(\w*) '([^']+)' design-checks\.out", wf)
    return (m.group(1), m.group(2)) if m else (None, None)


def _design_output(root, args=()):
    e = dict(os.environ)
    e["SBE_DOSSIER_ROOT"] = ""
    return subprocess.run([sys.executable, DESIGN] + list(args) + [root],
                          capture_output=True, text=True, env=e).stdout


@case("the-ci-waiver-annotation-does-not-fire-on-a-run-with-no-waiver", "guard", "differs")
def v30e(root):
    """The CI step must behave differently on a waived run and a clean one.

    It did not. `grep -q 'WAIVED'` matched the banner sbe_design.py prints on every
    single run ("WAIVED is not a pass either"), so every clean run annotated the
    job summary with "a .sbe-exempt waived one or more design checks. Nothing
    opened a file for them" over a run in which every check opened its files. An
    always-on assurance signal carries no information; this one asserted something
    false, and it is the only control that makes WAIVED visible in CI at all,
    since the exit code deliberately cannot.

    The pattern under test is read out of the shipped workflow, and the two
    outputs are produced by running the real tool, so this cannot pass by
    agreeing with itself.
    """
    gap = _withheld(SHIPPED_WORKFLOW)
    if gap:
        return gap
    import re as _re
    flags, pattern = _waiver_grep_pattern()
    if pattern is None:
        return "the workflow no longer greps design-checks.out for a waiver at all"
    rx = _re.compile(pattern, 0 if "E" in (flags or "") else 0)
    clean_dir = os.path.join(root, "clean")
    waived_dir = os.path.join(root, "waived")
    for d, exempt in ((clean_dir, None), (waived_dir, EXEMPTION)):
        write(d, "design/proj/00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
        write(d, "design/proj/01-purpose.md", PURPOSE)
        if exempt:
            write(d, "design/proj/.sbe-exempt", exempt)
    clean_hits = [l for l in _design_output(clean_dir).splitlines() if rx.search(l)]
    waived_hits = [l for l in _design_output(waived_dir).splitlines() if rx.search(l)]
    if clean_hits:
        return "the CI pattern %r fires on a run with no waiver: %r" % (pattern, clean_hits[0][:80])
    if not waived_hits:
        return "the CI pattern %r does not fire on a run that waived five checks" % pattern
    return "differs"


@case("a-blank-session-id-is-a-broken-ledger-row", "score", "FAIL")
def v31(root):
    return score_check("schema-2-uniform", _ledger(root, json.dumps({"schema": 2, "session_id": " "}) + "\n"))


@case("an-unreadable-ledger-row-does-not-delete-the-other-checks", "score", "NO-DATA")
def v32(root):
    # One well-formed JSON line whose session_id is an object raised inside the
    # shared context, OUTSIDE the per-check guard: eleven verdict lines became
    # one error line and advisory mode exited 0.
    body = json.dumps({"schema": 2, "session_id": {"a": 1}, "ts": "2026-07-25T10:00:00Z"}) + "\n"
    return score_check("prediction-seals", _ledger(root, body))


@case("an-unreadable-ledger-row-fails-the-checks-that-read-it", "score", "FAIL")
def v33(root):
    body = json.dumps({"schema": 2, "session_id": {"a": 1}, "ts": "2026-07-25T10:00:00Z"}) + "\n"
    return score_check("ledger-coverage", _ledger(root, body))


@case("an-undated-correction-is-not-a-fresh-one", "score", "FAIL")
def v34(root):
    write(root, "99-System/telemetry/corrections.jsonl", json.dumps({"text": "x"}) + "\n")
    return score_check("correction-latency", root)


@case("a-zero-byte-session-log-is-not-a-session-log", "score", "FAIL")
def v35(root):
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    _ledger(root, json.dumps({"session_id": "s1", "ts": now.isoformat().replace("+00:00", "Z")}) + "\n")
    write(root, "10-Projects/demo/Sessions/%s-session.md" % now.date().isoformat(), "")
    return score_check("vault-log-per-active-day", root)


@case("a-scan-of-empty-source-files-is-not-clean", "score", "NO-DATA")
def v36(root):
    src = os.path.join(root, "src")
    write(root, "src/empty.py", "")
    return score_check("silent-failure-lints", root, args=(src,))


@case("cache-counters-that-are-not-counts-are-caught", "score", "FAIL")
def v37(root):
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return score_check("cache-economy", _ledger(root, json.dumps(
        {"session_id": "s1", "ts": now, "cache_read": "", "cache_write": ""}) + "\n"))


# The honest path, at every tier. A gate is only worth having if correct work
# clears it, so a complete dossier is a fixture too, and it uses the ordinary
# authoring idioms rather than the ones that happen to suit the parser.
# The complete-dossier fixtures use a purpose written about the system the rest of
# the dossier designs. PURPOSE above is a SHAPE fixture ("Problem: x, Users: y")
# and is deliberately about nothing, which is now a FAIL in its own right: an
# artifact that names nothing any sibling artifact names is filler.
DOSSIER_PURPOSE = ("# Purpose\nProblem: refunds settle late and the support desk cannot say why.\n"
                   "Users: the support desk and the finance close.\n"
                   "Success: every refund reaches a terminal state within one business day.\n"
                   "Non-goals: repricing, partial refunds.\n"
                   "If wrong: a customer waits and nobody can see where the refund stopped.\n")
DOSSIER = {
    "01-purpose.md": DOSSIER_PURPOSE,
    "02-process.md": "# Process\nThe refund is requested, approved, and settled.\n"
                     "Each step names its owner and its failure path.\n",
    "03-adr.md": "# ADR\n## Criteria\nsettlement latency, operational load\n"
                 "## Rejected alternatives\n"
                 "- Synchronous call to the ledger: ties checkout to ledger availability.\n"
                 "- Nightly batch: misses the one business day requirement.\n"
                 "## Decision\nPublish refund events to a queue.\n"
                 "## Consequences\nOne more moving part to operate.\n"
                 "## What would flip this\nSub-second settlement becoming a requirement.\n",
    "04-technology-map.md": "# Technology map\n| Component | Technology | Owner |\n|---|---|---|\n"
                            "| RefundQueue | Managed queue | Platform team |\n",
    "05-data-model.md": "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
                        "- Refund: system of record: the ledger service.\n"
                        "## Relationships\n- Customer to Refund: one-to-many, optional.\n",
    "06-diagrams.md": "# Diagrams\n## Context\n```mermaid\nflowchart LR\n"
                      "  C[Customer] --> R[Refund]\n```\n"
                      "## Sequence\n```mermaid\nsequenceDiagram\n  participant Customer\n"
                      "  participant Refund\n  Customer->>Refund: requests\n```\n",
    "07-verification.md": "# Verification\nA reconciliation query compares refunds settled against\n"
                          "refunds requested, and it runs in CI.\n",
    "08-behaviour.md": "# Behaviour\n## Rules\n"
                       "| ID | Starting point | Trigger | Required outcome | Proof |\n"
                       "|---|---|---|---|---|\n"
                       "| B1 | A refund that has been approved | The ledger confirms settlement | "
                       "The refund reaches a terminal state within one business day | Integration "
                       "test: approve a refund, simulate the ledger confirmation, assert the refund "
                       "status becomes terminal within one business day |\n"
                       "| B2 | A refund that has not settled after one business day | The "
                       "reconciliation query runs | The refund is flagged and the support desk is "
                       "alerted | Batch test: seed a refund older than one business day with no "
                       "settlement, run the reconciliation query, assert the alert reaches the "
                       "support desk |\n",
}
TIER_ANSWERS = {"T0": {"changes_contract": False, "crosses_boundary": False,
                       "reversible_under_hour": True, "touches_sensitive": False,
                       "consumers": "none"},
                "T1": T1_ANSWERS, "T2": T2_ANSWERS, "T3": T3_ANSWERS}


def _complete_dossier(root, tier):
    write(root, "design/refunds/00-intake.json",
          {"tier": tier, "answers": TIER_ANSWERS[tier], "override": None})
    for n in _intake.required_artifacts(tier):
        name = {"01": "01-purpose.md", "02": "02-process.md", "03": "03-adr.md",
                "04": "04-technology-map.md", "05": "05-data-model.md",
                "06": "06-diagrams.md", "07": "07-verification.md",
                "08": "08-behaviour.md"}[n]
        write(root, "design/refunds/" + name, DOSSIER[name])
    return design_run(root)


@case("a-complete-t0-dossier-blocks-nothing", "designrun", "clear")
def h0(root):
    return _complete_dossier(root, "T0")


@case("a-complete-t1-dossier-blocks-nothing", "designrun", "clear")
def h1(root):
    return _complete_dossier(root, "T1")


@case("a-complete-t2-dossier-blocks-nothing", "designrun", "clear")
def h2(root):
    return _complete_dossier(root, "T2")


@case("a-complete-t3-dossier-blocks-nothing", "designrun", "clear")
def h3(root):
    return _complete_dossier(root, "T3")


@case("a-change-with-no-numbers-and-no-migration-blocks-nothing", "gaterun", "clear")
def h4(root):
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "internal refactor"], check=True)
    out = subprocess.run([sys.executable, GATE, "--strict", root], capture_output=True, text=True)
    return "blocked" if out.returncode else "clear"


# ---------------------------------------------------------------------------
# Round four of one defect: the value is PRESENT, NON-EMPTY, and vacuous.
#
# Measured against the tree that shipped it: a manifest with snapshot_id "TODO"
# and rerun.primary = rerun.secondary = "pending" produced "numbers PASS 1
# figure(s) each with a pinned, independently re-derived, zero-drift check" and
# --strict exit 0, and the other three hard gates fell to "unknown", "TODO" and
# "-" in the same run. Every clause of those four evidence lines was false. The
# fix is one shared predicate (sbe_checks.VACUOUS_VALUES and answered()), and the
# mechanical closure is the vacuous-token sweep in evals/test_no_data_class.py;
# these fixtures pin the named cases the review reproduced by hand.
# ---------------------------------------------------------------------------

@case("a-placeholder-snapshot-id-is-not-a-pinned-read", "numbers", "FAIL")
def w1(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "Q3 revenue", "snapshot_id": "TODO",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("two-pending-values-are-not-zero-drift", "numbers", "FAIL")
def w2(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "Q3 revenue", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": "pending", "secondary": "pending"}}]})


@case("a-second-derivation-differing-only-in-case-is-not-independent", "numbers", "FAIL")
def w3(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "select sum(amount) from orders",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-second-derivation-differing-only-in-whitespace-is-not-independent", "numbers", "FAIL")
def w4(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT  SUM(amount)\n  FROM orders",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("rerun-ran-as-the-string-false-is-not-a-re-run", "numbers", "FAIL")
def w5(root):
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": "false", "primary": 17570, "secondary": 17570}}]})


@case("a-figure-value-recorded-as-a-numeric-string-still-passes", "numbers", "PASS")
def w6(root):
    # The type check must not reject an honest receipt exported from a
    # spreadsheet: a count written as "17,570" is a count.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": "17,570", "secondary": "17570"}}]})


@case("row-counts-of-unknown-are-not-a-matched-comparison", "migration", "FAIL")
def w7(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": "unknown", "after_reverse": "unknown"}})


@case("a-placeholder-rehearsal-id-is-not-a-rehearsal-id", "migration", "FAIL")
def w8(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "TODO"},
        "row_counts": {"before": 100, "after_reverse": 100}})


@case("a-placeholder-check-name-does-not-say-what-ran", "ran", "FAIL")
def w9(root):
    write(root, "ran-receipt.json", {"checks": [{"name": "TODO", "exit_code": 0,
                                                 "duration_ms": 1}]})


@case("a-hyphen-is-not-a-review-id", "approval", "FAIL")
def w10(root):
    write(root, "APPROVAL", "touches the partner payout path\n")
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "payout batching\n\nReviewed-in: -"],
                   check=True)


# I4, adjudicated: an unverifiable signature already reports NO-DATA on the
# grounds that this host cannot check it. A Reviewed-in id is in the identical
# state, so it gets the identical verdict. While it was a PASS, the strongest
# sentence this gate could print about a money-movement change cost one hyphen.
@case("an-unresolved-review-id-is-nodata-not-a-pass", "approval", "NO-DATA")
def w11(root):
    write(root, "APPROVAL", "touches the partner payout path\n")
    git_init(root)
    open(os.path.join(root, "x"), "w").write("1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "payout batching\n\nReviewed-in: PR-482"],
                   check=True)


@case("a-tbd-system-of-record-and-a-todo-snapshot-are-refused-by-one-list", "evidence", "one-list")
def w12(root):
    # The structural point, pinned: sbe_design.py refused "todo" as a system of
    # record while sbe_gate.py accepted "TODO" as a pinned snapshot, because the
    # list was a private constant in one file that the other never imported.
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import sbe_checks, sbe_design, sbe_gate
    shared = sbe_checks.VACUOUS_VALUES
    for mod in (sbe_design, sbe_gate):
        if any(isinstance(v, (tuple, list, set, frozenset)) and "todo" in {str(x).lower() for x in v}
               and v is not shared for k, v in vars(mod).items() if k.isupper()):
            return "%s carries its own vacuity list" % mod.__name__
    return "one-list" if sbe_design._stated_value("TODO") == "" and \
        sbe_checks.answered("TODO") is None else "predicates disagree"


# ---------------------------------------------------------------------------
# Honest work that used to be blocked. A gate that rejects correct work gets
# switched off by its users, which destroys the system more surely than a hole
# does, so each of these is a fixture in its own right.
# ---------------------------------------------------------------------------

@case("a-soft-wrapped-entity-bullet-keeps-its-system-of-record", "datamodel", "PASS")
def y1(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- RefundEvent: one refund action against an order, as recorded by the\n"
          "  ledger; system of record: the ledger service.\n"
          "- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to RefundEvent: one-to-many, optional.\n")


@case("a-soft-wrapped-relationship-keeps-its-cardinality", "datamodel", "PASS")
def y2(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "- Refund: system of record: the ledger service.\n"
          "## Relationships\n"
          "- Customer to Refund, where a refund is always raised against an order\n"
          "  that the customer placed: one-to-many, optional.\n")


@case("an-adr-written-in-prose-headings-passes", "adr", "PASS")
def y3(root):
    write(root, "03-adr.md",
          "# How we are settling refunds\n"
          "## What we weighed\nSettlement latency, operational load, auditability.\n"
          "## Roads not taken\n"
          "- A synchronous call to the ledger: ties checkout latency to ledger availability.\n"
          "- Nightly batch reconciliation: misses the one business day requirement.\n"
          "## What we are doing\nPublish refund events to a queue and settle asynchronously.\n"
          "## What this costs us\nOne more moving part to operate, and an ordering guarantee.\n"
          "## When we would revisit this\nSub-second settlement becoming a requirement.\n")


_ADR_REST = ("## Criteria\nSettlement latency, operational load, auditability.\n"
             "## Decision\nPublish refund events to a queue and settle asynchronously.\n"
             "## Consequences\nOne more moving part to operate.\n"
             "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("an-adr-whose-alternatives-are-prose-paragraphs-passes", "adr", "PASS")
def y3a(root):
    # Three of the four ordinary ways to write this FAILed, with a message that
    # named none of the accepted forms and described an empty heading that was not
    # there. A gate that rejects correct work is argued with and then switched off.
    write(root, "03-adr.md",
          "# ADR\n## Rejected alternatives\n"
          "We considered a synchronous call to the ledger. We rejected it because it ties "
          "checkout latency to ledger availability.\n\n"
          "We considered nightly batch reconciliation. It misses the one business day "
          "requirement.\n" + _ADR_REST)


@case("an-adr-whose-alternatives-are-a-numbered-list-passes", "adr", "PASS")
def y3b(root):
    write(root, "03-adr.md",
          "# ADR\n## Rejected alternatives\n"
          "1. Synchronous call to the ledger: ties checkout latency to ledger availability.\n"
          "2. Nightly batch reconciliation: misses the one business day requirement.\n" + _ADR_REST)


@case("an-adr-with-one-sub-heading-per-alternative-passes", "adr", "PASS")
def y3c(root):
    write(root, "03-adr.md",
          "# ADR\n## Rejected alternatives\n"
          "### Synchronous call to the ledger\nTies checkout latency to ledger availability.\n"
          "### Nightly batch reconciliation\nMisses the one business day requirement.\n" + _ADR_REST)


@case("one-alternative-in-any-form-is-still-not-two", "adr", "FAIL")
def y3d(root):
    # The control. Accepting more forms must not accept fewer alternatives, and
    # sub-headings must count once each rather than once per paragraph.
    write(root, "03-adr.md",
          "# ADR\n## Rejected alternatives\n"
          "### Synchronous call to the ledger\nTies checkout latency to ledger availability.\n\n"
          "It also doubles the blast radius of a ledger outage.\n" + _ADR_REST)


@case("an-empty-sub-heading-is-still-not-an-alternative", "adr", "FAIL")
def y3e(root):
    write(root, "03-adr.md",
          "# ADR\n## Rejected alternatives\n### Synchronous call\n### Nightly batch\n" + _ADR_REST)


@case("a-data-model-written-as-tables-throughout-passes", "datamodel", "PASS")
def y3f(root):
    # Relationships were readable as a table and entities were not, so a data model
    # written as tables FAILed with "no entity bullets found" over a document
    # naming every entity and every system of record.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "| Entity | System of record | Notes |\n| --- | --- | --- |\n"
          "| Customer | system of record: the CRM | core |\n"
          "| Refund | system of record: the ledger service | derived |\n"
          "## Relationships\n| From | To | Cardinality |\n| --- | --- | --- |\n"
          "| Customer | Refund | one-to-many |\n")


@case("an-entity-table-row-with-no-system-of-record-still-fails", "datamodel", "FAIL")
def y3g(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "| Entity | System of record |\n| --- | --- |\n"
          "| Customer | system of record: the CRM |\n"
          "| Refund | to be decided |\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")


@case("an-entity-lifecycle-state-diagram-with-declared-states-passes", "diagrams", "PASS")
def y4(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Order: system of record: the OMS.\n"
          "## Order states\n- Draft\n- Placed\n- Shipped\n"
          "## Relationships\n- Order to Order: one-to-one, self.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Order lifecycle\n```mermaid\nstateDiagram-v2\n"
          "  [*] --> Draft\n  Draft --> Placed\n  Placed --> Shipped\n  Shipped --> [*]\n```\n")


@case("an-undeclared-lifecycle-state-is-nodata-not-a-false-failure", "diagrams", "NO-DATA")
def y5(root):
    # Before: FAIL, "diagram element(s) appear nowhere else in the dossier:
    # Draft, Placed, Shipped", with no document anywhere telling an author how to
    # make a state diagram trace. The only way through was to declare the states
    # as runtime COMPONENTS, which is the model-corrupting pathology L5 removed
    # for queues, reproduced one diagram type over.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Order: system of record: the OMS.\n"
          "## Relationships\n- Order to Order: one-to-one, self.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Order lifecycle\n```mermaid\nstateDiagram-v2\n"
          "  [*] --> Draft\n  Draft --> Placed\n  Placed --> Shipped\n  Shipped --> [*]\n```\n")


@case("an-invented-flowchart-node-still-fails-beside-a-state-diagram", "diagrams", "FAIL")
def y6(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Order: system of record: the OMS.\n"
          "## Order states\n- Draft\n- Placed\n"
          "## Relationships\n- Order to Order: one-to-one, self.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nstateDiagram-v2\n  [*] --> Draft\n  Draft --> Placed\n```\n"
          "## Context\n```mermaid\nflowchart LR\n  X[MysteryBox] --> Y[GhostQueue]\n```\n")


# The counts printed in the shipped docs, checked against the suites that print
# them. Three docs carried "70 evals: 70 passed" through a wave that took the
# real number to 110, and the doc whose whole job is "prove it works in 60
# seconds" was one of them. A number in a doc that nothing recomputes is a stale
# evidence string with a longer half-life.
SHIPPED_DOCS = ("README.md", "docs/SETUP.md", "docs/HOW-IT-WORKS.md", "PUBLISH-CHECKLIST.md",
                "docs/guides/01-quickstart.md", "docs/guides/02-the-gates-in-practice.md",
                # This entry read "03-the-dossier.md" for a wave, a file that does
                # not exist, and both guards `continue` past a path they cannot
                # open, so the whole guide was silently exempt from them. A list
                # of documents to check is itself evidence, and a name in it that
                # opens nothing is the absent-file defect in a tuple.
                "docs/guides/03-work-doctrines.md", "docs/guides/04-teams-and-evolution.md",
                "docs/guides/05-a-worked-engagement.md", "evals/README.md", "DIGEST.md",
                "LAWS-REFERENCE.md",
                "INVARIANTS.md",
                # STATE.md was named here and is excluded by .gitignore, so it is in
                # nobody's clone. The guard below caught it as an absent file, and
                # only in a fresh clone: on the author's machine the untracked file
                # was present, the suite was green, and CI was red on arrival. The
                # shippable artifact is the template, so the template is what ships.
                "SKILL.md", "PARITY.md", "PRACTICES.md", "RUBRIC.md", "STATE.template.md",
                # The onboarding set quotes the eval total, the meta-test line and
                # the manifest file count, so it belongs to the same guards as
                # every other page that prints a number the suite can recompute.
                # A page whose whole job is "here is real output from this
                # machine" is the worst place for a count nothing rechecks.
                "docs/for-engineers/00-READ-ME-FIRST.md",
                "docs/for-engineers/01-install-and-first-run.md",
                "docs/for-engineers/10-backend-engineer.md",
                "docs/for-engineers/11-data-engineer.md",
                "docs/for-engineers/12-infrastructure-architect.md",
                "docs/for-engineers/13-etl-builder.md",
                "docs/for-engineers/20-what-it-will-not-tell-you.md",
                "docs/for-engineers/30-adopting-it-on-a-team.md")
_REPO = os.path.abspath(os.path.join(HERE, ".."))


@case("every-eval-case-invariants-md-cites-is-a-case-the-suite-defines", "docs", "consistent")
def dc_inv(root):
    # INVARIANTS.md names the asserting test for each promise, on the argument
    # that "a promise nothing asserts is a wish with a serial number". The
    # citation IS the promise's evidence, and it was asserted by nobody: a
    # rename in the suite left the register pointing at a case that no longer
    # exists, silently. This parses every backticked hyphenated token in
    # INVARIANTS.md and fails on one that is not a defined eval case, so the
    # register's own citations degrade loudly the way the counts already do.
    import re as _re
    body = open(os.path.join(_REPO, "INVARIANTS.md"), errors="replace").read()
    names = {name for name, _k, _e, _fn in CASES}
    # A citation-shaped token: backticked, hyphenated, all lowercase words, no
    # slash (a path) and no space. The reinjection table cites case names in
    # this exact shape; a plain-English hyphenated word (there are none today)
    # would have to also be a real case name to pass, which is the point.
    cited = {t for t in _re.findall(r"`([a-z][a-z0-9-]{6,})`", body) if "-" in t}
    unknown = sorted(c for c in cited if c not in names)
    if not cited:
        return "INVARIANTS.md cites no eval case at all, so this guard proved nothing"
    return "consistent" if not unknown else (
        "INVARIANTS.md cites %d case(s) the suite does not define: %s"
        % (len(unknown), ", ".join(unknown[:4])))


@case("no-shipped-doc-prints-an-eval-count-the-suite-does-not-produce", "docs", "consistent")
def dc1(root):
    import re as _re
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        body = open(p, errors="replace").read()
        for m in _re.finditer(r"(\d+) evals: (\d+) passed, (\d+) regressions", body):
            if (int(m.group(1)), int(m.group(2)), int(m.group(3))) != (len(CASES), len(CASES), 0):
                wrong.append("%s: %r but the suite has %d cases" % (rel, m.group(0), len(CASES)))
        for m in _re.finditer(r'ending "(\d+) passed, (\d+) regressions', body):
            if (int(m.group(1)), int(m.group(2))) != (len(CASES), 0):
                wrong.append("%s: %r but the suite has %d cases" % (rel, m.group(0), len(CASES)))
    return "consistent" if not wrong else "; ".join(wrong[:4])


@case("no-shipped-doc-prints-a-meta-test-count-the-meta-test-does-not-produce", "docs", "consistent")
def dc2(root):
    # windows-porting-lane round 2: `got` used to drop group(3), the module
    # count, on the floor (captured by the regex, quoted back in the
    # message, never compared). A shipped doc could assert any module figure
    # at all and this case still returned "consistent". Every group the
    # regex names is compared now, module count included, derived from
    # meta.counts() (the same load_tool_modules() discovery main() runs),
    # never from a number written into this file.
    import re as _re
    meta = SourceFileLoader("test_no_data_class",
                            os.path.join(HERE, "test_no_data_class.py")).load_module()
    checks, regs, mods, scen, waived = meta.counts()
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        for m in _re.finditer(r"(\d+) checks discovered from (\d+) registries in (\d+) module\(s\), "
                              r"(\d+) scenarios run(?:, (\d+) waived by declared exemption)?",
                              open(p, errors="replace").read()):
            got = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
            if got != (checks, regs, mods, scen) or (m.group(5) is not None
                                                      and int(m.group(5)) != waived):
                wrong.append("%s: %r but the meta-test walks %d checks, %d registries, %d "
                             "modules, %d scenarios, %d waived"
                             % (rel, m.group(0), checks, regs, mods, scen, waived))
    return "consistent" if not wrong else "; ".join(wrong[:4])


def _law_homes():
    """The files that hold law TEXT, as opposed to files that summarize it.

    SKILL.md keeps L6, L11 and L14; the routing table sends every other law to a
    file under references/. Derived by globbing rather than listed, so a twelfth
    reference file is covered on the day it lands.
    """
    homes = ["SKILL.md"]
    refs = os.path.join(_REPO, "references")
    if os.path.isdir(refs):
        homes += ["references/" + n for n in sorted(os.listdir(refs)) if n.endswith(".md")]
    return homes


# A citation is a law-text FILE followed by the law numbers it is being credited
# with: "`SKILL.md` L13", "(SKILL.md L16)", "`SKILL.md` L7 to L10",
# "`SKILL.md` L7 and L16", "SKILL.md law L13". The law group stops at the first
# token that is not another law number, so "`SKILL.md` L9 and `LAWS-REFERENCE.md`"
# credits L9 to SKILL.md and nothing to the summary file beside it.
_CITE = re.compile(r"`?(?P<file>SKILL\.md|references/[A-Za-z0-9._-]+\.md)`?[\s,]*"
                   r"(?:law[s]?\s+)?(?P<laws>L\d+(?:\s*(?:,|and|to|/|through)\s*L?\d+)*)")


def _cited_laws(text):
    """Every (file, law number) pair a page asserts, ranges expanded.

    Returns pairs rather than a verdict so the same reading can be used on the
    shipped pages and on the planted defects below: a guard whose detector is
    only ever run against text that passes has not been shown to detect anything.
    """
    pairs = []
    for m in _CITE.finditer(text):
        nums = [int(n) for n in re.findall(r"\d+", m.group("laws"))]
        span = m.group("laws")
        # "L7 to L10" and "L17 through L19" name a run; a comma or "and" names
        # the endpoints only. Both spellings appear in the shipped pages.
        if len(nums) == 2 and re.search(r"\b(to|through)\b", span):
            nums = list(range(min(nums), max(nums) + 1))
        for n in nums:
            pairs.append((m.group("file"), n))
    return pairs


@case("every-law-citation-names-a-file-that-holds-that-law", "docs", "consistent")
def dc_cite(root):
    """A pointer nobody opens is the defect this whole product is about.

    Sixteen laws and six phases moved out of SKILL.md into `references/*.md`.
    Every "Full text: `SKILL.md` L9" written before that move still parsed, still
    read as a citation, and named a file that no longer carried the text. No test
    read those pointers, which is exactly why they could rot in silence: the
    counts guards recompute numbers and the behavior guard reads sentences, and a
    cross-reference was gated by nothing.

    The rule, not a list of known citations: a page may credit a law number to a
    law-text file only if that file actually declares the law. `_law_homes` globs
    the reference directory and `_cited_laws` reads any spelling of the citation,
    so a new reference file, a new law, or a citation nobody has written yet is
    covered without editing this case.
    """
    homes, unreadable = {}, []
    for rel in _law_homes():
        try:
            body = open(os.path.join(_REPO, rel), errors="replace").read()
        except OSError as e:
            # Named, never skipped: a law-text file this guard could not open is
            # a file whose laws every citation would then be reported missing
            # from, and a silent `continue` would turn that into a clean verdict.
            unreadable.append("%s could not be read (%s)" % (rel, e.strerror or e))
            continue
        homes[rel] = {int(n) for n in re.findall(r"^###\s+L(\d+)\.", body, re.M)}
    if unreadable:
        return "; ".join(unreadable[:4])
    if not homes or not any(homes.values()):
        return ("no law-text file could be read, or none declares a law heading, so this "
                "guard resolved no citation and cannot report the pointers consistent")
    # The detector, proved against planted defects before it is trusted on the
    # real pages. CAUGHT are the exact shapes the lazy-core split created.
    known = sorted(next(iter(homes.values())))[:1]
    live = "L%d" % known[0] if known else "L6"
    caught = ["Full text: `SKILL.md` L9.", "the fence is human (SKILL.md L13)",
              "`SKILL.md` L7 to L10 are the four hard gates",
              "`references/laws-hard-gates.md` L13"]
    clear = ["Full text: `references/laws-hard-gates.md` L9.",
             "`SKILL.md` %s and `LAWS-REFERENCE.md` (the hard gates)" % live,
             "`references/laws-parallel-writers.md` L13, `LAWS-REFERENCE.md`."]

    def unresolved(text):
        return [(f, n) for f, n in _cited_laws(text) if n not in homes.get(f, set())]
    problems = ["missed a planted wrong pointer: %r" % s for s in caught if not unresolved(s)]
    problems += ["flagged a correct pointer: %r" % s for s in clear if unresolved(s)]
    docs, checked = _shipped_markdown(), 0
    if not docs:
        return ("no shipped markdown page could be derived, so no citation was resolved and "
                "none can be called consistent")
    for rel in docs:
        try:
            text = open(os.path.join(_REPO, rel), errors="replace").read()
        except OSError as e:
            problems.append("%s could not be read (%s)" % (rel, e.strerror or e))
            continue
        for f, n in _cited_laws(text):
            checked += 1
            if n not in homes.get(f, set()):
                problems.append("%s credits L%d to %s, which does not declare it (L%d is in %s)"
                                % (rel, n, f,
                                   n, ", ".join(h for h, ls in homes.items() if n in ls) or "no shipped file"))
    if not checked:
        return ("no shipped page cites a law by file and number, so the mechanism that ties a "
                "pointer to the file holding the law is checking nothing")
    return "consistent" if not problems else "; ".join(problems[:4])


@case("every-shipped-doc-path-in-this-suite-opens-a-file", "docs", "consistent")
def dc0(root):
    missing = [rel for rel in SHIPPED_DOCS if not os.path.isfile(os.path.join(_REPO, rel))]
    if missing and not _tracked_here(missing):
        # Neither on disk nor tracked here, so this is a tree that does not
        # CARRY these documents (the public export withholds the internal
        # process pages by name), not a list naming files that open nothing.
        # The defect this case was written for is the opposite shape and is
        # still caught below: a path present on one machine and in no clone.
        return ("NO-DATA: this clone does not carry %s, so nothing was opened "
                "for them and this case proved nothing about them. An absent "
                "document is not a pass." % sorted(missing))
    return "consistent" if not missing else "SHIPPED_DOCS names %s, which opens nothing" % missing


@case("every-shipped-doc-path-in-this-suite-is-actually-shipped", "docs", "consistent")
def dc6(root):
    """A path this suite depends on has to exist in the reader's copy, not only here.

    `STATE.md` was in SHIPPED_DOCS and in `.gitignore` at the same time. It is
    therefore in no clone of this repository, so the guard above FAILed for every
    reader and passed for the one machine that happened to hold the untracked
    file: the shipped CI was red on its first pull request, on a step unrelated to
    the change, with an error naming a file the reader cannot find. Opening a file
    is not evidence that the file ships. This asks the two questions that are, and
    a path that answers neither is named here rather than discovered by a stranger.
    """
    problems = []
    tracked = None
    try:
        out = subprocess.run(["git", "-C", _REPO, "ls-files", "-z", "--"] + list(SHIPPED_DOCS),
                             capture_output=True, text=True)
        if out.returncode == 0:
            tracked = set(p for p in out.stdout.split("\0") if p)
    except OSError:
        # Not swallowed: `tracked` stays None and the .gitignore reading below is
        # what runs, and the absence of git is reported in the evidence.
        tracked = None
    if tracked is not None:
        for rel in SHIPPED_DOCS:
            if rel in tracked:
                continue
            if not os.path.isfile(os.path.join(_REPO, rel)):
                # Absent from disk AND untracked: this tree does not carry the
                # document at all. That is NO-DATA about it, never a pass, and
                # it is a different fact from the STATE.md defect above, which
                # is a file PRESENT on one machine and tracked by nobody.
                problems.append("NO-DATA: %s is neither on disk nor tracked "
                                "here, so this clone does not carry it and "
                                "nothing about it was proved" % rel)
            else:
                problems.append("%s is not tracked by git, so it is in "
                                "nobody's clone" % rel)
    # The .gitignore reading runs either way, because a source tarball has no git
    # and a guard that can only run in a working repository is the same blindness
    # one layer out. Plain patterns only: a name, or a name anchored to the root.
    ignore = os.path.join(_REPO, ".gitignore")
    patterns = []
    if os.path.isfile(ignore):
        for line in open(ignore, errors="replace").read().splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "!")):
                patterns.append(s.strip("/"))
    elif tracked is None:
        return ("neither git nor a .gitignore is readable here, so whether these paths ship "
                "could not be checked at all; this is reported rather than passed over")
    for rel in SHIPPED_DOCS:
        for pat in patterns:
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat):
                problems.append("%s is excluded by .gitignore rule %r, so it is in nobody's clone"
                                % (rel, pat))
    if problems and all(x.startswith("NO-DATA:") for x in problems):
        # One prefix, not two: main() strips the leading one to label the row.
        bare = sorted(set(x[len("NO-DATA:"):].strip() for x in problems))
        return "NO-DATA: " + "; ".join(bare[:4])
    return "consistent" if not problems else "; ".join(sorted(set(problems))[:4])


@case("every-quoted-verdict-line-in-the-docs-carries-its-declared-severity", "docs", "consistent")
def dc7(root):
    """The doc-quote guard for verdict lines. A formatting change landed in the
    tools (the severity suffix) and 45 quoted verdict lines across five docs
    kept the old shape, silently: the class was unguarded because nothing
    mechanical connected a pasted verdict line to the tool that prints it.
    This sweeps every shipped doc for lines shaped like a check verdict
    (a registered check name followed by PASS/FAIL/NO-DATA) and requires each
    to end with the severity suffix the registry declares for that check, so
    the next formatting change fails here instead of going stale on the page.
    Byte-exactness of whole engagements is the replay harness's job (dc9);
    this pins the shape and the severity of every quoted line everywhere."""
    import re as _re
    _score = SourceFileLoader("sbe_score_docquote",
                              os.path.join(_REPO, "tools", "sbe_score.py")).load_module()
    severities = {}
    for reg in (_gate.GATES, _design_mod.CHECKS, _score.CHECKS):
        for name, check in reg.items():
            severities[name] = check.severity
    names = "|".join(sorted(severities))
    line_re = _re.compile(r"^\s*(%s)\s+(PASS|FAIL|NO-DATA)\b.*$" % names)
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        for n, line in enumerate(open(p, errors="replace").read().splitlines(), 1):
            m = line_re.match(line)
            if not m:
                continue
            if "so this check opened no file" in line:
                # The design tool's no-dossier reporter prints these fallback
                # lines itself, without a severity suffix, because no check
                # produced a verdict; the load-bearing phrase is the same one
                # the meta-test keys on. A quote of one is correct as printed.
                continue
            want = "[severity: %s]" % severities[m.group(1)]
            if not line.rstrip().endswith(want):
                wrong.append("%s:%d quotes a %s verdict line without its %s suffix"
                             % (rel, n, m.group(1), want))
    return "consistent" if not wrong else "; ".join(wrong[:6])


# The doc side of the behavior guard, DERIVED. The guard shipped as one regex
# alternation of the nine literal phrasings previous rounds happened to find,
# under a name that claims every doc sentence about tool behavior, and two
# ordinary sentences of the removed re-root walked straight through it: "the
# gate walks up to the repository root and looks there too" and "a
# ran-receipt.json placed anywhere under the repository will be discovered by
# the gate". Absence of a listed phrasing is not evidence of an honest page,
# and a positive verdict may not rest on it. What is falsifiable about the
# removed mechanism is its MEANING: the search scope is wider than the
# directory the caller named. That is what these patterns describe, in the
# shapes English gives it (an upward walk, an "anywhere under", a parent
# directory, the worktree top by name), so a phrasing nobody has written yet
# is classified on arrival rather than after a reviewer finds it.
_SCOPE_PLACE = r"(?:git\s+)?(?:repositor\w+|repo|worktree|project\s+root|toplevel|top\s+level)"
_REROOT_WIDENS = (
    r"(?i)\b(?:walk|walks|walking|climb|climbs|ascend|ascends|search|searches|searching"
    r"|look|looks|looking|move|moves|go|goes)\b[^.;]{0,40}?\bup(?:ward|wards)?\b"
    r"[^.;]{0,40}?\b%s"
    r"|\banywhere\s+(?:else\s+)?(?:under|in|within|beneath|below|inside)\s+(?:the\s+|your\s+|a\s+)?"
    r"(?:%s|tree)"
    r"|\b(?:in|under|from|to)\s+(?:the\s+|its\s+|a\s+|any\s+)?parent\s+director\w+"
    r"|\b%s\s+top\b|--show-toplevel" % (_SCOPE_PLACE, _SCOPE_PLACE, _SCOPE_PLACE))
# A sentence that DENIES the mechanism does not assert it, and the honest
# narrow sentence ("a receipt is found under the directory you name, not
# anywhere in the worktree") must not be read as the false one. The negator
# has to sit in the same clause, immediately before the widening phrase: in
# "if the receipt is not in the directory you name, the gate walks up to the
# repository root", the "not" belongs to a different clause, and a loose
# lookback would have excused the very sentence this guard exists to catch.
#
# Its stated boundary, and it is a boundary rather than an oversight: 24
# non-punctuation characters is PROXIMITY, not negation, so an assertion that
# happens to carry a negator inside that window ("there is no doubt the gate
# walks up to the repository root") reads as a denial. Requiring the negator to
# be the last word before the claim was tried and reverted: it flags the honest
# denials this repository actually ships, where the negator is the clause's
# SUBJECT ("Nothing re-roots to a git worktree top"), and a guard that flags the
# correction of the sentence it polices teaches people to stop writing the
# correction. Deciding which noun a negator governs is parsing English, which
# this guard does not do. docs/KNOWN-LIMITS.md carries the limit.
_SCOPE_DENIAL = (r"(?i)\b(?:not|never|no|nothing|neither|rather\s+than"
                 r"|instead\s+of|without)\b[^,;.]{0,24}$")


def _asserted_span(pattern, line):
    """The span of `line` that ASSERTS `pattern`, or None if every hit is denied.

    One reading of a sentence, for every pattern this guard carries. The
    remembered literal phrasings ran through a bare `re.search` while the
    derived classifier ran through the denial check, so the honest denial of a
    shipped sentence ("a receipt is found under the directory you name, not
    anywhere in the worktree") was read as the false claim by one path and as
    honest by the other. A guard that flags the correction of the very
    sentence it polices teaches people to stop writing the correction.
    """
    import re as _re
    for m in _re.finditer(pattern, line):
        if _re.search(_SCOPE_DENIAL, line[:m.start()]):
            continue
        return m.group(0)
    return None


def _widening_claim(line):
    """The span of `line` claiming a search scope wider than the named directory.

    Returns the matched text, or None. Every match is read for a denial in the
    clause that carries it, so the guard reads a sentence the way its reader
    does rather than by keyword presence.
    """
    return _asserted_span(_REROOT_WIDENS, line)


# Where one block of prose ends for a READER: a blank line, the start of another
# block-level element (a heading, a list item, a quote, a table row, a rule), or
# a fence, inside which every line stands alone because a code block's line
# breaks are its content.
_MD_BLOCK_START = r"(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||={3,}\s*$|-{3,}\s*$)"
_MD_FENCE = r"(?:```|~~~)"


def _reader_blocks(text):
    """[(first line number, the text as a reader receives it)] for one page.

    A guard must read a document the way its reader reads it. This one matched
    ONE PHYSICAL LINE at a time, and every page this repository ships is hard
    wrapped (docs/KNOWN-LIMITS.md has no line over 100 characters), so a false
    sentence evaded it by pressing Return: the verb landed on one line and the
    place on the next, neither line matched, and the guard's positive verdict
    "consistent", which is a claim about every shipped page, was bought by a
    line break. A sentence of the length this guard exists to catch cannot fit
    on one line of most of the corpus it scans, so line-at-a-time was absence
    used as proof, produced by a text the scanner never assembled.

    The rule: the unit is the block a reader reads, not the line an editor
    produced. Lines are joined until the block ends and their whitespace is
    collapsed, so a wrapped sentence is matched as the one sentence it is. The
    classifier's own spans stay bounded by `[^.;]` so a match still cannot
    reach across a sentence boundary; what changes is only that a sentence is
    no longer cut in half before it is read. The block's FIRST line number
    comes back with it, so the evidence a reader is handed still points at a
    place in the file.
    """
    import re as _re
    blocks, buf, start, in_fence = [], [], 1, False

    def flush():
        if buf:
            blocks.append((start, " ".join(" ".join(buf).split())))
            del buf[:]
    for n, line in enumerate(text.splitlines(), 1):
        fence = _re.match(r"\s*%s" % _MD_FENCE, line)
        if in_fence or fence:
            flush()
            blocks.append((n, line.strip()))
            if fence:
                in_fence = not in_fence
            continue
        if not line.strip():
            flush()
            continue
        if _re.match(r"\s*%s" % _MD_BLOCK_START, line):
            flush()
        if not buf:
            start = n
        buf.append(line)
    flush()
    return blocks


def _shipped_markdown():
    """Every markdown page a reader receives, derived rather than curated.

    Template: the citation scan set in `tools/sbe_score.py`, which had this same
    defect (two names plus one directory, so SECURITY.md and nine other shipped
    pages were never opened) and now derives its set from the checksums manifest.
    The manifest is preferred because it is the shipped file list and it works in
    a source tarball with no git; `git ls-files` is the fallback; the curated
    tuple is unioned in so the set can only widen, never lag. An empty result is
    returned as empty and reported by the caller: a guard that opened nothing has
    proven nothing about the pages it was supposed to read.
    """
    rels = []
    manifest = os.path.join(_REPO, "CHECKSUMS.sha256")
    if os.path.isfile(manifest):
        try:
            for line in open(manifest, errors="replace"):
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].strip().endswith(".md"):
                    rels.append(parts[1].strip())
        except OSError:
            rels = []
    if not rels:
        try:
            out = subprocess.run(["git", "-C", _REPO, "ls-files", "-z", "--", "*.md"],
                                 capture_output=True, text=True)
            if out.returncode == 0:
                rels = [p for p in out.stdout.split("\0") if p]
        except OSError:
            rels = []
    return sorted({r for r in list(rels) + list(SHIPPED_DOCS)
                   if os.path.isfile(os.path.join(_REPO, r))})


@case("no-shipped-doc-widens-the-receipt-lookup-past-what-the-gate-does", "docs", "consistent")
def dc_behavior(root):
    """The doc-quote guard for BEHAVIOR CLAIMS, not for numbers or verdict lines.

    A behavior change landed at the code site (fix 10 removed the silent
    `git rev-parse --show-toplevel` re-root) and six doc sentences kept
    teaching the removed behavior, in the direction where a receipt the docs
    promise is found silently is not, so a FAIL degrades to a NO-DATA that
    does not block. The fixture-backed line guard recomputes verdict LINES
    and the count guards recompute NUMBERS; prose describing what a tool does
    was gated by nothing.

    The rule this encodes: a doc sentence that asserts a mechanism must name
    one that exists in the source. Each family below is (name, a liveness
    predicate over the MECHANISM, a phrase a doc would only contain if it
    were describing the old behavior, why it is wrong). Two laws earned by
    this guard's own first defect: a guard may not take evidence from prose
    it is meant to police (the toplevel predicate substring-matched the
    COMMENT documenting the removal, so the whole family was dead at its own
    landing commit and a planted forbidden sentence read as consistent), so
    the toplevel predicate now reads the source with comments stripped; and
    each family reports its own liveness, so ONE dead family is a failure by
    itself rather than being hidden until both die.
    """
    import tokenize as _tok
    gate_path = os.path.join(_REPO, "tools", "sbe_gate.py")
    try:
        gate_src = open(gate_path, errors="replace").read()
    except OSError as e:
        # Not raised out of the case, and never treated as an absence: a source
        # this guard cannot open is a source whose mechanisms it cannot judge
        # live, and every family's liveness predicate would read "the mechanism
        # is gone" from a file that was simply unreadable.
        return ("the source this guard falsifies against could not be read (%s: %s), so no "
                "claim family could be judged live and no doc sentence was tested"
                % (gate_path, e.strerror or e))

    def _code_only(path):
        # The source minus its comments: a comment is prose, and prose about
        # a removed mechanism must not count as the mechanism. Docstrings
        # stay: a string in code can BE the mechanism (a subprocess argument
        # is a string). A file tokenize cannot read yields "", which makes
        # every family live and the guard strict, never silently dead.
        try:
            with open(path, "rb") as f:
                return " ".join(t.string for t in _tok.tokenize(f.readline)
                                if t.type != _tok.COMMENT)
        except (OSError, _tok.TokenError, SyntaxError):
            return ""
    gate_code = _code_only(gate_path)

    def _marker_sites(src):
        # Where each `sbe: allow-silent` marker sits, named by the nearest
        # preceding `def`. Read RAW and line by line, the way the lint that
        # honours the marker reads it (sbe_score.EXEMPTION): the marker IS a
        # comment, so comment-stripped source would strip the very mechanism
        # this family falsifies against. Nearest-preceding-def is the file's
        # shape rather than a parse, which is enough to say WHICH helper was
        # waived and cheap enough to stay obviously correct.
        sites, fn = [], "<module>"
        for line in src.splitlines():
            m = re.match(r"\s*def\s+(\w+)", line)
            if m:
                fn = m.group(1)
            if "sbe: allow-silent" in line:
                sites.append(fn)
        return sites
    # (family, live, doc pattern, why the sentence would be false). Family
    # liveness is judged where each mechanism LIVES: the re-root was an
    # invocation, so its family reads comment-stripped code; the allow-silent
    # marker is itself comment-shaped, so its family reads the raw text.
    # (family, live, literal pattern, derived classifier, why the sentence would
    # be false). The re-root family carries both: the literal phrasings are the
    # sentences this repository actually shipped once and are kept as a floor,
    # and the classifier is what catches a phrasing nobody has written yet. The
    # marker family is literal by nature (its mechanism IS a fixed string in the
    # source), so there is nothing there to derive, and the case name below now
    # claims only the lookup these two families can falsify.
    #
    # RE-DERIVED. Commit 332de09 gave `_git_version()` a reasoned
    # `# sbe: allow-silent` waiver with the reason the lint reads, so the marker
    # family's old claim (`sbe_gate.py` carries no marker) became FALSE and the
    # family reported itself dead, which is this guard working rather than
    # failing. Re-derived against what the source holds now: EXACTLY ONE marker,
    # sitting in `_git_version`, and still no git-worktree fall-back in code
    # (the one `--show-toplevel` in the file is the COMMENT recording its
    # removal, which is why the sibling family reads comment-stripped code and
    # why this one may repeat that read rather than assume it). The claim
    # DIRECTION flipped with it: the re-root family falsifies against an
    # ABSENCE, this one now against a PRESENCE, so it goes dead when a second
    # marker lands, when the one marker moves out of `_git_version`, and when
    # the last marker leaves, the last case mattering most because a doc denying
    # the marker becomes TRUE at that moment and this family would otherwise be
    # flagging an honest sentence. Prose inside the gate that merely quotes the
    # marker counts as a site and takes the family dead: loud and wrong beats
    # silently vacuous, which is the defect the docstring above records.
    families = [
        ("worktree-reroot",
         "--show-toplevel" not in gate_code,
         r"(?i)resolves? (?:its root|it) to the git (?:worktree top|toplevel)"
         r"|resolves? its root to your git worktree top"
         r"|resolve it to the git worktree top"
         r"|defaults? to the current git worktree"
         r"|walks the git worktree"
         r"|inspects the current\s+git worktree"
         r"|found anywhere in the (?:git )?worktree"
         r"|find it (?:anywhere )?in the (?:git )?worktree"
         r"|anywhere in the worktree",
         _widening_claim,
         "the gate examines exactly the directory it was named; nothing "
         "re-roots to a git worktree top (no --show-toplevel invocation in "
         "sbe_gate.py), so a receipt is found under the directory you name, "
         "not anywhere in the worktree"),
        ("allow-silent-marker",
         _marker_sites(gate_src) == ["_git_version"]
         and "--show-toplevel" not in gate_code,
         # Every alternative begins AT its negator on purpose: `_asserted_span`
         # drops a hit whose preceding 24 characters carry one, so a pattern
         # starting at "allow-silent" would read the false sentence as its own
         # honest denial and never fire. What is policed is a DENIAL of the
         # marker, which is what turned false when the waiver landed. Where the
         # one marker SITS is deliberately not policed: the honest ways to name
         # a location are open-ended, and flagging a true sentence is this
         # guard's own recorded defect, so the placement claim is verified in
         # the liveness read above instead of guessed at in prose.
         r"(?i)(?:sbe_gate\.py|the gate)[^.;]{0,80}?"
         r"\b(?:carr(?:ies|y|ied)|has|have|holds?|contains?|keeps?)\s+no\b"
         r"[^.;]{0,60}?allow-silent"
         r"|(?:sbe_gate\.py|the gate)[^.;]{0,80}?\b(?:does\s+not|do\s+not|never)\b"
         r"[^.;]{0,60}?(?:carr\w+|hold\w*|contain\w*|keep\w*)[^.;]{0,40}?allow-silent"
         r"|\b(?:no|without)\b[^.;]{0,40}?allow-silent[^.;]{0,60}?\bin\s+`?sbe_gate"
         r"|git-worktree fall[-\s]?back",
         None,
         "sbe_gate.py carries exactly one `sbe: allow-silent` marker, in "
         "`_git_version()`, with the reason the lint reads written beside it, "
         "and no git-worktree fall-back"),
    ]
    dead = [name for name, live, _, _, _ in families if not live]
    if dead:
        return ("family %s of %d checked nothing: the mechanism it knows how to falsify "
                "is no longer in the state its claims describe (it returned, it moved, "
                "or it left), so those claims must be re-derived; a "
                "family that checks nothing is a failure by itself, not coverage"
                % (", ".join(dead), len(families)))
    docs = _shipped_markdown()
    if not docs:
        # Absence is never proof: no manifest, no git and no curated path that
        # opens a file means no page was read, and "consistent" would be a
        # verdict about documents nothing examined.
        return ("no shipped markdown page could be derived (no CHECKSUMS.sha256 entries, no "
                "git ls-files, no curated path that opens): a guard that read no document "
                "cannot report the documents consistent")
    wrong = []
    for rel in docs:
        p = os.path.join(_REPO, rel)
        # _reader_blocks, not splitlines(): a sentence wrapped across two lines
        # is one sentence to its reader and was invisible to this guard, which
        # then reported every shipped page "consistent". See _reader_blocks.
        for n, line in _reader_blocks(open(p, errors="replace").read()):
            for _name, _live, pattern, derived, why in families:
                span = (_asserted_span(pattern, line)
                        or (derived(line) if derived else None))
                if span:
                    wrong.append("%s:%d describes behavior the tools do not have: %r (%s)"
                                 % (rel, n, span[:60], why))
                    break
    return "consistent" if not wrong else "; ".join(wrong[:5])


# The gate source the two cases below hand `dc_behavior`. It is a stub, but it
# carries BOTH mechanisms in the state their families claim, because a family
# whose mechanism is missing reports itself dead and `dc_behavior` returns that
# instead of the verdict under test. The re-root family claims an ABSENCE, which
# any stub satisfies; the marker family claims a PRESENCE, so the stub has to
# carry the one waived helper.
_GATE_STUB = ("def gate_approval(root):\n"
              "    return 'NO-DATA', 'stub'\n"
              "\n\n"
              "def _git_version():\n"
              "    try:\n"
              "        return _read()\n"
              "    except Exception:\n"
              "        return None  # sbe: allow-silent the stub's one waived helper, "
              "mirroring the waiver the marker family reads live\n")


@case("a-phrasing-of-the-removed-re-root-nobody-has-written-yet-is-caught", "docs", "consistent")
def dc_behavior_derived(root):
    """The doc-honesty guard classifies a sentence, not a remembered phrasing.

    Both CAUGHT sentences below are the ones a reviewer wrote into a copy of the
    clone and watched pass while the guard printed "consistent": they describe
    exactly the re-root fix 10 removed, in the words a person uses when they are
    not copying an earlier draft. The CLEAR sentences are the honest ones that
    must never be flagged: the narrow behavior stated plainly, the same behavior
    stated as a denial, and a paragraph that merely happens to carry the words.
    Pinned here rather than in a fixture tree because what is under test is the
    reading of a sentence, and a scenario that plants text in a doc would prove
    only that this one text is remembered.
    """
    caught = [
        "If the receipt is not in the directory you name, the gate walks up to the "
        "repository root and looks there too.",
        "A ran-receipt.json placed anywhere under the repository will be discovered by the gate.",
        "The gate searches upward through the git worktree until it finds a receipt.",
        "If no receipt is here, the gate looks in the parent directory.",
        "The receipt may live anywhere in the tree; the gate will find it.",
    ]
    clear = [
        "The gate examines exactly the directory it was named.",
        "A receipt is found under the directory you name, not anywhere in the worktree.",
        "The gate never walks up to the repository root.",
        "Nothing re-roots to a git worktree top.",
        "A run that opened no file reports NO-DATA naming why, and nothing anywhere counts skips.",
    ]
    problems = ["missed: %r" % s[:70] for s in caught if not _widening_claim(s)]
    problems += ["falsely flagged: %r" % s[:70] for s in clear if _widening_claim(s)]
    # THE ROUND'S LAW, applied to this guard's own input: absence is never
    # proof, so emptying what the guard reads may never improve its verdict.
    # Pointed at a directory holding no page at all, it must report that it
    # read nothing rather than report the pages consistent.
    # The root is given both live mechanisms and no pages at all, so the branch
    # under test is the empty DOCUMENT set rather than an unreadable source.
    write(root, "tools/sbe_gate.py", _GATE_STUB)
    global _REPO
    kept = _REPO
    try:
        _REPO = root
        derived = _shipped_markdown()
        empty_verdict = dc_behavior(root)
    finally:
        _REPO = kept
    if derived:
        problems.append("a root holding no page derived %d page(s), so the empty case proves "
                        "nothing" % len(derived))
    if empty_verdict == "consistent":
        problems.append("a root holding no shipped page reported the pages consistent")
    elif "no shipped markdown page could be derived" not in empty_verdict:
        problems.append("the empty-document verdict named something else: %r" % empty_verdict[:80])
    return "consistent" if not problems else "; ".join(problems[:4])


@case("a-false-doc-sentence-cannot-escape-by-wrapping-onto-a-second-line", "docs", "caught")
def dc_wrapped(root):
    """Pressing Return does not make a false sentence true.

    `dc_behavior` read ONE PHYSICAL LINE at a time, and every page this
    repository ships is hard wrapped, so the sentence the guard exists to
    catch could not fit on one line of most of the corpus it scans. The verb
    landed on one line and the place on the next, neither line matched, and
    the guard printed "consistent", a claim about every shipped page, bought
    by a line break. That is absence used as proof over a text the scanner
    never assembled, inside the guard built during the wave that made absence
    is never proof the law.

    Driven through `dc_behavior` itself over a planted page rather than
    through the classifier alone, because the defect was never in the
    classifier: `_widening_claim` matched the sentence perfectly. It was in
    the UNIT of text handed to it, which only the caller decides.
    """
    write(root, "tools/sbe_gate.py", _GATE_STUB)
    # Wrapped exactly the way the shipped pages are wrapped, and the control
    # wrapped the same way: the honest denial must survive being joined.
    write(root, "docs/planted.md",
          "# How the gate finds a receipt\n\n"
          "If the receipt is not in the directory you name, the gate walks\n"
          "up to the repository root and looks there too, so you can put it\n"
          "anywhere under\nthe repository and it will still be discovered.\n")
    write(root, "docs/honest.md",
          "# How the gate finds a receipt\n\n"
          "The gate examines exactly the directory it was named. A receipt\n"
          "is found under the directory you name, not anywhere in the\n"
          "worktree, and nothing re-roots to a git worktree top.\n")
    write(root, "CHECKSUMS.sha256", "0  docs/planted.md\n0  docs/honest.md\n")
    global _REPO
    kept = _REPO
    try:
        _REPO = root
        verdict = dc_behavior(root)
        # The same page unwrapped onto one line, as the control that the guard
        # was catching something before and is not merely catching more now.
        write(root, "docs/planted.md",
              "# How the gate finds a receipt\n\n"
              "If the receipt is not in the directory you name, the gate walks "
              "up to the repository root and looks there too.\n")
        one_line = dc_behavior(root)
    finally:
        _REPO = kept
    problems = []
    if "planted.md" not in verdict:
        problems.append("the wrapped sentence was not caught: %r" % verdict[:120])
    if "honest.md" in verdict:
        problems.append("the honest wrapped page was flagged: %r" % verdict[:120])
    if "planted.md" not in one_line:
        problems.append("the one-line control was not caught: %r" % one_line[:120])
    return "caught" if not problems else "; ".join(problems)


# A figure printed in a shipped document is a CLAIM, and this project refuses
# to report what it did not examine. Its own pages were exempt from that rule:
# the refusal-remainder section carried numbers typed once by hand, over pools
# it never published, and went on printing them after the code underneath
# moved. Nothing was lying on purpose; nothing could have noticed.
#
# The mechanism, and it is general rather than one number: a page may carry a
# block marked `derived-by: <script>`, and the marked block must equal what
# that script prints TODAY. The eval below re-runs every such script over every
# shipped page. Its boundary, stated because an unstated boundary is the same
# defect one level out: this enforces MARKED blocks. A number typed into prose
# with no marker is still just a number, and docs/KNOWN-LIMITS.md says so.
_DERIVED_MARKER = r"derived-by:\s*([A-Za-z0-9_./-]+\.py)"


def _derived_blocks(text):
    """[(script path, the block the page publishes)] for one page.

    A marker whose block is missing or unterminated comes back with None for
    the block, so a page that carries the marker and no readable block is a
    failure rather than a page with nothing to compare.
    """
    import re as _re
    lines = text.splitlines()
    out = []
    for n, line in enumerate(lines):
        m = _re.search(_DERIVED_MARKER, line)
        if not m:
            continue
        rest = lines[n + 1:]
        while rest and not rest[0].strip():
            rest.pop(0)
        if not rest or not rest[0].startswith("```"):
            out.append((m.group(1), None))
            continue
        body, closed = [], False
        for l in rest[1:]:
            if l.startswith("```"):
                closed = True
                break
            body.append(l)
        out.append((m.group(1), "\n".join(body) if closed else None))
    return out


@case("every-derived-figure-in-a-shipped-doc-recomputes", "docs", "derived")
def dc_derived(root):
    """Every published measurement re-derives from the script it names.

    Absence is never proof, applied to this guard's own input twice: a run
    that found no marked block anywhere reports that it found none rather than
    reporting the figures derived, and a script that cannot be run is reported
    as unrunnable rather than skipped.
    """
    docs = _shipped_markdown()
    if not docs:
        return ("no shipped markdown page could be derived, so no published figure was "
                "recomputed and none can be called derived")
    problems, checked = [], 0
    for rel in docs:
        try:
            text = open(os.path.join(_REPO, rel), errors="replace").read()
        except OSError as e:
            problems.append("%s could not be read (%s)" % (rel, e.strerror or e))
            continue
        for script, published in _derived_blocks(text):
            checked += 1
            path = os.path.join(_REPO, script)
            if not os.path.isfile(path):
                problems.append("%s names %s, which does not exist, so its figures are "
                                "derived from nothing" % (rel, script))
                continue
            if published is None:
                problems.append("%s marks a block derived by %s and carries no closed block "
                                "after the marker" % (rel, script))
                continue
            try:
                # Python-side timeout: `timeout` is not a command on every host.
                out = subprocess.run([sys.executable, path], capture_output=True,
                                     text=True, timeout=600)
            except (OSError, subprocess.SubprocessError) as e:
                problems.append("%s could not be run (%s), so %s was not recomputed"
                                % (script, e, rel))
                continue
            if out.returncode != 0:
                problems.append("%s exited %d, so %s was not recomputed"
                                % (script, out.returncode, rel))
                continue
            if out.stdout.rstrip("\n") != published.rstrip("\n"):
                problems.append("%s disagrees with a fresh run of %s (regenerate the block)"
                                % (rel, script))
    if not checked:
        return ("no shipped page carries a `derived-by:` block, so the mechanism that ties "
                "published figures to a runnable derivation is checking nothing")
    return "derived" if not problems else "; ".join(problems[:4])


@case("guide-01s-drift-demonstration-replays-from-its-own-steps", "docs", "consistent")
def dc_g01_drift(root):
    """The two-step replay the single-receipt replayer could not model.

    Guide 01's DRIFT demonstration is the product of TWO steps (write the
    manifest, then edit it), and the round-11 edit step evaluated its write
    handle before its read, truncating the manifest to zero bytes: the
    reader got a JSONDecodeError FAIL where the guide quotes the DRIFT FAIL,
    and the closure that shipped it was verification by reasoning
    ("portable by construction"), never a run. This eval RUNS the guide's
    own fenced steps, verbatim as extracted, and compares what the gate
    prints against the DRIFT line the guide quotes, so the doc's steps and
    the doc's quotes cannot drift apart again.
    """
    import re
    guide = open(os.path.join(_REPO, "docs/guides/01-quickstart.md"), errors="replace").read()
    m = re.search(r"cat > numbers-manifest\.json <<'EOF'\n(.*?)\nEOF\n", guide, re.S)
    if not m:
        return "guide 01 no longer writes its manifest via the heredoc this replay extracts"
    e = re.search(r"python3 - <<'EDIT'\n(.*?)\nEDIT\n", guide, re.S)
    if not e:
        return "guide 01 no longer edits the manifest via the python heredoc this replay extracts"
    q = re.search(r"^  numbers   FAIL     (gmv: DRIFT.*?)(?:\s*\[severity: gate\])?$",
                  guide, re.M)
    if not q:
        return "guide 01 no longer quotes a DRIFT FAIL line"
    write(root, "numbers-manifest.json", m.group(1))
    before = os.path.getsize(os.path.join(root, "numbers-manifest.json"))
    old_cwd = os.getcwd()
    os.chdir(root)
    try:
        exec(compile(e.group(1), "<guide-01-edit-step>", "exec"), {})
    except Exception as ex:
        return "guide 01's edit step raised %r when executed" % (ex,)
    finally:
        os.chdir(old_cwd)
    after = os.path.getsize(os.path.join(root, "numbers-manifest.json"))
    if after == 0:
        return ("guide 01's edit step truncated the manifest to zero bytes (was %d); the "
                "reader gets an unparseable-receipt FAIL where the guide quotes DRIFT" % before)
    verdict, evidence = _gate.gate_numbers(root)
    if verdict != "FAIL" or evidence != q.group(1):
        return ("guide 01 quotes %r but running its own steps produces %s %r"
                % (q.group(1), verdict, evidence))
    return "consistent"


@case("guide-01s-verbatim-workflow-fence-is-the-shipped-workflow", "docs", "consistent")
def dc8(root):
    # The one doc that promises the FULL file "verbatim" handed out a copy
    # without the hardening the workflow's own header calls the control
    # everything else rests on (no SHA pinning, no read-only permissions, no
    # seeded meta-test), and a prior sync claim about this fence was false.
    # Byte comparison, so "verbatim" means verbatim.
    gap = _withheld(SHIPPED_WORKFLOW)
    if gap:
        return gap
    guide = open(os.path.join(_REPO, "docs/guides/01-quickstart.md"), errors="replace").read()
    wf = open(os.path.join(_REPO, ".github/workflows/brothersbe-gates.yml"),
              errors="replace").read()
    marker = "The workflow, verbatim:"
    if marker not in guide:
        return "guide 01 no longer carries the verbatim marker"
    i = guide.index(marker)
    try:
        start = guide.index("```yaml\n", i) + len("```yaml\n")
        end = guide.index("\n```", start)
    except ValueError:
        return "no yaml fence follows the verbatim marker"
    return "consistent" if guide[start:end + 1] == wf else \
        "the fence under 'verbatim' differs from the shipped workflow"


@case("every-fixture-backed-verdict-line-in-the-guides-is-what-the-tool-prints", "docs", "consistent")
def dc_worked(root):
    """The numbers inside a quoted verdict line are LIVE where the guide shows
    the fixture that produced them.

    The doc-quote guard pins a verdict line's shape, its verdict word and its
    severity, and leaves every NUMBER inside it a free template slot: `1
    figure(s)` could be edited to `9 figure(s)` in a shipped guide and all
    three gates stayed green, so a reader comparing their own run to the doc
    was taught the wrong expectation. This closes that by recomputation, not
    by a wider template: wherever a guide shows a self-contained JSON receipt
    immediately above a worked output block, the receipt is written to the
    filename its shape names, the matching gate is run over it, and the
    gate's verdict line is compared to the one the guide prints. A digit
    changed anywhere in such a line now fails here. It replays only the
    receipt-shaped gates (numbers, migration, ran); the signature-bound
    approval blocks need a private key no fixture can carry, so those stay
    pinned by the shape-and-phrasing guard rather than replayed, which is
    stated rather than left implicit."""
    import re as _re
    shape_to = [(("figures",), "numbers-manifest.json", "numbers"),
                (("forward", "reverse", "row_counts"), "migration-receipt.json", "migration"),
                (("checks",), "ran-receipt.json", "ran")]
    guides = ("docs/guides/02-the-gates-in-practice.md",
              "docs/guides/04-teams-and-evolution.md")
    wrong, replayed = [], 0
    for rel in guides:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        lines = open(p, errors="replace").read().splitlines()
        # Collect fenced blocks with their language tag and body.
        blocks, i = [], 0
        while i < len(lines):
            m = _re.match(r"^```(\w*)\s*$", lines[i])
            if m:
                j = i + 1
                body = []
                while j < len(lines) and not _re.match(r"^```\s*$", lines[j]):
                    body.append(lines[j])
                    j += 1
                blocks.append((m.group(1), "\n".join(body), i + 1))
                i = j + 1
            else:
                i += 1
        for bi, (lang, body, lineno) in enumerate(blocks):
            if lang != "json":
                continue
            try:
                obj = json.loads(body)
            except ValueError:  # sbe: allow-silent a json fence that does not parse is prose the guide shows on purpose, not a receipt to replay, so it is skipped by design here
                continue
            if not isinstance(obj, dict):
                continue
            target = next((fname, gate) for keys, fname, gate in shape_to
                          if any(k in obj for k in keys)) if any(
                any(k in obj for k in keys) for keys, _f, _g in shape_to) else None
            if not target:
                continue
            fname, gate = target
            # The nearest following plain (untagged) output block that carries
            # this gate's verdict line.
            out_block = next((b for (blang, b, _ln) in blocks[bi + 1:bi + 4]
                              if blang == "" and _re.search(r"(?m)^\s*%s\s+(PASS|FAIL|NO-DATA)\b"
                                                            % gate, b)), None)
            if out_block is None:
                continue
            want = next(l.strip() for l in out_block.splitlines()
                        if _re.match(r"^\s*%s\s+(PASS|FAIL|NO-DATA)\b" % gate, l))
            with tempfile.TemporaryDirectory() as d:
                write(d, fname, obj)
                # Invoked from inside the directory, with `.` as the root, which
                # is the invocation every doc shows. The verdict line names the
                # root it examined, and a scratch path is different on every run.
                got = gate_line(".", gate, cwd=d)
            replayed += 1
            if " ".join(got.split()) != " ".join(want.split()):
                wrong.append("%s:%d the %s block prints %r but the tool prints %r"
                             % (rel, lineno, gate, want[:60], got[:60]))
    if not replayed:
        return "this guard replayed no fixture-backed verdict block, so it proved nothing"
    return "consistent" if not wrong else "; ".join(wrong[:3])


@case("guide-05s-output-blocks-are-what-the-tools-print", "docs", "consistent")
def dc9(root):
    if os.name != "posix":
        return ("PLATFORM-GAP: the replay harness drives bash scripts "
                "against POSIX-captured transcripts; on this platform the "
                "harness cannot produce comparable output at all (run "
                "31042529271: every live capture collapsed to one line), "
                "so the doc-truth guarantee is measured on the POSIX legs "
                "only")
    # The guide's opening claim, made mechanical: evals/replay_guide05.py
    # writes every artifact block byte for byte, runs every command block, and
    # diffs each captured output against the block the guide shows. A stale
    # quote fails the gate instead of going stale on the page; a maintainer
    # repairs it with `python3 evals/replay_guide05.py --write`, which pastes
    # the LIVE output, so nobody ever hand-types an expected block.
    out = subprocess.run([sys.executable, os.path.join(HERE, "replay_guide05.py")],
                         capture_output=True, text=True, timeout=310)
    tail = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "(no output)"
    if out.returncode == 0 and tail.endswith(", 0 differ"):
        return "consistent"
    return tail


@case("the-books-terminal-blocks-are-what-the-tools-print", "docs", "consistent")
def dc9b(root):
    if os.name != "posix":
        return ("PLATFORM-GAP: the replay harness drives bash scripts "
                "against POSIX-captured transcripts; on this platform the "
                "harness cannot produce comparable output at all (run "
                "31042529271: every live capture collapsed to one line), "
                "so the doc-truth guarantee is measured on the POSIX legs "
                "only")
    # The book's own front matter claim, made mechanical the same way dc9
    # makes guide-05's: evals/replay_book.py walks every docs/book/[0-9][0-9]-*.md
    # chapter, runs every bash block against the repo it actually lives in,
    # and diffs each captured stdout against the bare block the chapter shows
    # underneath. A stale paste fails the gate instead of going stale on the
    # page; a maintainer repairs it with `python3 evals/replay_book.py
    # --write`, which pastes the LIVE output, so nobody ever hand-types an
    # expected block.
    out = subprocess.run([sys.executable, os.path.join(HERE, "replay_book.py")],
                         capture_output=True, text=True, timeout=300)
    tail = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "(no output)"
    if out.returncode == 0 and tail.endswith(", 0 differ"):
        return "consistent"
    return tail


@case("no-copy-ready-ci-block-shows-fewer-steps-than-the-shipped-workflow", "docs", "consistent")
def dc3(root):
    # A reader who copies a CI fence gets what the fence shows. Three docs showed
    # three steps while the workflow ran six, and the three they omitted were the
    # regression evals, the honesty meta-test and the tool tests: exactly the
    # suites whose absence from the merge path this project had just finished
    # fixing. A prose count and a fence are both claims about a file that ships.
    gap = _withheld(SHIPPED_WORKFLOW)
    if gap:
        return gap
    import re as _re
    wf = open(os.path.join(_REPO, ".github/workflows/brothersbe-gates.yml"),
              errors="replace").read()
    real = _re.findall(r"^      - name: (.+)$", wf, _re.M)
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        body = open(p, errors="replace").read()
        for m in _re.finditer(r"(?sm)^```yaml\n(.*?)^```$", body):
            block = m.group(1)
            if "sbe_gate.py --strict" not in block:
                continue
            shown = _re.findall(r"^\s*- name: (.+)$", block, _re.M)
            missing = [n for n in real if n not in shown]
            if missing:
                wrong.append("%s: a copy-ready CI block omits %d of the %d shipped steps (%s)"
                             % (rel, len(missing), len(real), "; ".join(missing[:3])))
    return "consistent" if not wrong else "; ".join(wrong[:3])


@case("no-copy-ready-ci-block-runs-the-meta-test-weaker-than-the-workflow", "docs", "consistent")
def dc_seed(root):
    # A reader assembling CI from a doc fragment gets what the fragment shows.
    # Three docs showed the meta-test step as one unseeded command while the
    # shipped workflow runs it twice (the fixed sweep, then the seeded random
    # composition --seed 1 --seed 2 --seed 3), so the assemble-the-steps path
    # silently installed weaker coverage than the copy-the-file path. The
    # workflow's own seeded invocation is the bar; any doc CI block that runs
    # the meta-test at all must run it too, or it degrades in silence.
    gap = _withheld(SHIPPED_WORKFLOW)
    if gap:
        return gap
    import re as _re
    wf = open(os.path.join(_REPO, ".github/workflows/brothersbe-gates.yml"),
              errors="replace").read()
    if "--seed" not in wf:
        return "the shipped workflow no longer runs the seeded meta-test, so this guard is stale"
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        body = open(p, errors="replace").read()
        for m in _re.finditer(r"(?sm)^```yaml\n(.*?)^```$", body):
            block = m.group(1)
            if "test_no_data_class.py" not in block:
                continue
            if "--seed" not in block:
                wrong.append("%s: a CI block runs the meta-test without the seeded pass the "
                             "shipped workflow runs" % rel)
    return "consistent" if not wrong else "; ".join(sorted(set(wrong))[:3])


_CHECK_LINE = None


def _checker_line_re():
    """The pasted-output line shape: two spaces, a check name, a verdict, evidence."""
    global _CHECK_LINE
    import re as _re
    if _CHECK_LINE is None:
        names = set()
        for mod, attr in (("sbe_gate.py", "GATES"), ("sbe_design.py", "CHECKS"),
                          ("sbe_score.py", "CHECKS")):
            m = SourceFileLoader(mod[:-3] + "_names", os.path.join(_REPO, "tools", mod)).load_module()
            names |= set(getattr(m, attr))
        names |= {"dossier"}          # printed by sbe_design.py's own reporter
        _CHECK_LINE = _re.compile(r"^  (>> )?(%s)\s+(PASS|FAIL|NO-DATA|WAIVED)\s+(\S.*)$"
                                  % "|".join(_re.escape(n) for n in sorted(names, key=len, reverse=True)))
    return _CHECK_LINE


def _evidence_templates():
    """Every sentence the three checkers can print, from their own source.

    The docs paste tool output verbatim, and tool output is built from `%`
    templates that are string constants in the source. Reading them out with ast
    is what lets a guard named for ALL checker lines cover all of them: a pasted
    line whose sentence no template can produce is a line the tool does not
    produce today. Candidates shorter than this in literal text are dropped,
    because a template that is mostly `%s` would match anything and a guard that
    matches anything is the defect this file exists to catch.
    """
    import ast as _ast, re as _re
    out = []
    for mod in ("sbe_gate.py", "sbe_design.py", "sbe_score.py", "sbe_checks.py"):
        tree = _ast.parse(open(os.path.join(_REPO, "tools", mod), errors="replace").read())
        for node in _ast.walk(tree):
            if not (isinstance(node, _ast.Constant) and isinstance(node.value, str)):
                continue
            for piece in [node.value] + node.value.split("; "):
                text = " ".join(piece.split())
                literal = _re.sub(r"%[-\d.]*[sdrf%]", "", text)
                if len(literal.strip()) < 16:
                    continue
                pattern = _re.escape(text)
                pattern = _re.sub(r"%[-\\\d.]*[sdrf]", ".*?", pattern).replace("%%", "%")
                out.append(_re.compile(pattern))
    return out


@case("no-shipped-doc-prints-a-checker-line-the-checker-does-not-produce", "docs", "consistent")
def dc7(root):
    """Every pasted checker line in every shipped doc, against what the tools can print.

    The guard that carried this name recomputed ONE line, from one of the twenty
    checks, by filtering `if not line.startswith("silent-failure-lints")`. It
    printed `consistent` and was counted among the suite as evidence for a claim
    about all shipped docs and all checker lines, while forty-six of the
    forty-seven pasted lines went unread and three of them were stale. A guard
    whose name asserts a property it implements over one case is this project's
    own defect class at the guard layer.

    Exact recomputation is only possible for a line the suite can reproduce (the
    lint line, in the case below). For the rest, the sentence a checker prints is
    built from `%` templates that live in its source, so a line no template can
    produce is a line the tool cannot print. That is checkable over all of them.
    """
    import re as _re
    line_re, templates = _checker_line_re(), _evidence_templates()
    wrong, seen = [], 0
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        body = open(p, errors="replace").read()
        quoted = [("inline quote", " ".join(q.split()))
                  for q in _re.findall(r"`(?:PASS|FAIL|NO-DATA|WAIVED)\s\s+([^`]+)`", body)]
        for raw in body.splitlines():
            m = line_re.match(raw.rstrip())
            if not m:
                continue
            seen += 1
            evidence = " ".join(m.group(4).split())
            # The severity suffix is appended by each tool's main(), outside
            # every evidence template, so it is stripped before template
            # matching; that the suffix is present and correct on quoted lines
            # is its own guard above.
            evidence = _re.sub(r"\s*\[severity: (?:gate|soft)\]$", "", evidence)
            # Same reason as the severity suffix, and the same source: the
            # design tool's main() appends the dossier each verdict examined,
            # outside every evidence template, so that a verdict read one line
            # at a time still names what it opened. It is built from paths, so
            # template matching over it would assert nothing anyway.
            evidence = _re.sub(r"\s*; examined \S+ under \S+$", "", evidence)
            for fragment in [evidence] + evidence.split("; "):
                if any(rx.fullmatch(fragment) for rx in templates):
                    break
            else:
                wrong.append("%s: %r is not a sentence %s can print today"
                             % (rel, evidence[:70], m.group(2)))
        # A checker sentence quoted inline, without its check name, is the same
        # claim in a smaller font: guide 04 quoted `FAIL  gmv_q3: no snapshot_id
        # (a live warehouse drifts; pin the read)`, and the sentence the tool
        # prints today ends "A placeholder is not a pin", which is the clause this
        # whole fix range exists to be able to print.
        for _kind, evidence in quoted:
            seen += 1
            for fragment in [evidence] + evidence.split("; "):
                if any(rx.fullmatch(fragment) for rx in templates):
                    break
            else:
                wrong.append("%s: the inline quote %r is not a sentence any checker prints today"
                             % (rel, evidence[:70]))
    if not seen:
        return "this guard found no checker line at all in any shipped doc, so it proved nothing"
    return "consistent" if not wrong else "%d of %d pasted line(s) stale: %s" % (
        len(wrong), seen, "; ".join(wrong[:3]))


@case("no-shipped-doc-prints-a-silent-failure-lint-line-the-scorer-does-not-produce",
      "docs", "consistent")
def dc4(root):
    # The lint line pasted in guide 02 names the exempted lines BY NUMBER, and
    # those numbers move whenever anything above them moves: one of them was a
    # wave stale and pointed at an unrelated statement. Recomputed here rather
    # than re-audited by eye every wave.
    import re as _re
    out = subprocess.run([sys.executable, SCORE, os.path.join(_REPO, "tools")],
                         capture_output=True, text=True, cwd=_REPO)
    live = [l for l in out.stdout.splitlines() if l.startswith("silent-failure-lints")]
    if not live:
        return "the scorer printed no silent-failure-lints line at all"
    # The scorer names files in the platform's separator; the doc pastes are
    # POSIX-rendered, so live lines are separator-normalized before comparison
    # (run 31040612827).
    live = {_re.sub(r"\s+", " ", live[0]).replace(os.path.join(_REPO, "tools"), "tools/").replace("\\", "/")}
    # The quickstart's first ten minutes shows a run against the READER'S OWN
    # repository, which cannot be a line about this one. A doc quote nothing
    # reproduces is the defect this guard exists for, so the worked repository
    # is reproduced here instead of the quote being exempted: two files, one
    # swallow on line 7. Change either the fixture or the doc and this fails.
    with tempfile.TemporaryDirectory() as d:
        write(d, "src/config.py",
              "import json\n\n\ndef load(p):\n    try:\n        return json.load(open(p))\n"
              "    except Exception:\n        pass\n    return {}\n")
        write(d, "src/util.py", "def ok():\n    return 1\n")
        yours = subprocess.run([sys.executable, SCORE, "."], capture_output=True, text=True, cwd=d)
        for l in yours.stdout.splitlines():
            if l.startswith("silent-failure-lints"):
                live.add(_re.sub(r"\s+", " ", l).replace("\\", "/"))
    # The ETL onboarding page pastes three lint lines off one sample directory:
    # the three-hit run, the run whose only waiver carries no reason, and the run
    # whose every finding is waived and therefore reports NO-DATA rather than
    # clean. Reproduced here rather than exempted, for the same reason the
    # quickstart's fixture above is: a doc quote nothing recomputes is the defect
    # this guard exists for, and those three lines are the ETL page's evidence.
    loader = ("import subprocess\n\n\ndef load(batch):\n    try:\n        stage(batch)\n"
              "    except DuplicateBatch:\n        pass\n")
    waived = ("import subprocess\n\n\ndef load(batch):\n    try:\n        stage(batch)\n"
              "    except DuplicateBatch:\n        pass  # sbe: allow-silent %s\n")
    variants = [(loader + "\n\ndef archive(path):\n    subprocess.run([\"mv\", path, \"/archive\"])\n",
                 "INSERT INTO ledger_staging (batch_id, amount_cents)\n"
                 "SELECT batch_id, amount_cents FROM settlement_raw\n"
                 "ON CONFLICT (batch_id) DO NOTHING;\n"),  # sbe: allow-silent this is the DEFECT the ETL page's lint run is supposed to catch, written into a fixture the guard hands to the scorer; suppressing the hit on the fixture is what lets the guard prove the scorer still reports it
                (waived % "TBD", None),
                (waived % ("a duplicate content hash is the idempotency contract, not an "
                           "error; the batch is already loaded"), None)]
    for py, sql in variants:
        with tempfile.TemporaryDirectory() as d:
            write(d, "sample-etl/load_settlements.py", py)
            if sql:
                write(d, "sample-etl/upsert.sql", sql)
            got = subprocess.run([sys.executable, SCORE, "sample-etl"],
                                 capture_output=True, text=True, cwd=d)
            for l in got.stdout.splitlines():
                if l.startswith("silent-failure-lints"):
                    live.add(_re.sub(r"\s+", " ", l).replace("\\", "/"))
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        for line in open(p, errors="replace").read().splitlines():
            if not line.startswith("silent-failure-lints"):
                continue
            if _re.sub(r"\s+", " ", line) not in live:
                wrong.append("%s pastes a lint line the tool does not produce today: %r"
                             % (rel, line[:90]))
    return "consistent" if not wrong else "; ".join(wrong[:3])


@case("no-shipped-doc-prints-a-check-count-the-scorer-does-not-produce", "docs", "consistent")
def dc5(root):
    import re as _re
    scorer = SourceFileLoader("sbe_score_count",
                              os.path.join(_REPO, "tools", "sbe_score.py")).load_module()
    n = len(scorer.CHECKS)
    words = {8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen"}
    wrong = []
    for rel in SHIPPED_DOCS:
        p = os.path.join(_REPO, rel)
        if not os.path.isfile(p):
            continue
        body = open(p, errors="replace").read()
        # Scoped to the phrasings that are ABOUT the scorer's registry. "Nine
        # checks green: five design, four gates" counts something else and is
        # not this eval's business.
        for m in _re.finditer(r"(\w+) mechanical checks\b|one of (\w+) check lines\b", body):
            word = (m.group(1) or m.group(2)).lower()
            if word in words.values() and word != words.get(n):
                wrong.append("%s: %r but sbe_score.py registers %d checks" % (rel, m.group(0), n))
            if word.isdigit() and int(word) in words and int(word) != n:
                wrong.append("%s: %r but sbe_score.py registers %d checks" % (rel, m.group(0), n))
    return "consistent" if not wrong else "; ".join(wrong[:4])


@case("every-url-in-the-shipped-docs-has-a-scoped-inventory-entry", "docs", "consistent")
def dc7(root):
    # The gate for the citation class: a URL added to README.md, SKILL.md or
    # docs/ without a docs/CITATIONS.md entry answering claim, population, date
    # and limit fails this suite, so a scopeless citation cannot merge. The
    # check itself is offline (structure and coverage, never live page content),
    # and the ci* evals above prove each direction of its bite on fixtures;
    # this one runs it against the real tree.
    line = _cite_line_at(_REPO, root)
    parts = line.split()
    if parts[1:2] != ["PASS"]:
        return line or "no citation-inventory verdict line printed"
    return "consistent"


# ---------------------------------------------------------------------------
# Round six. Two classes, one structural and one social.
#
# STRUCTURAL: every test in this project ran BEFORE its normalization instead of
# after, so every normalization could manufacture a fresh non-answer that nothing
# rechecked. `second_derivation: "#"` is not blank, is not a vacuity token,
# survives answered(), and folds to the empty string, and the gate then printed
# "a second derivation whose text differs beyond case, whitespace and comments,
# re-run to zero drift" over a derivation that computes nothing, with both
# figures the string "inf". Reduce first, test second: sbe_checks.answered_as.
#
# SOCIAL: the checks were rejecting honest work. `## Options considered` failed
# while `## Alternatives considered` passed; MADR, the most widely used ADR
# template there is, failed outright; `source of truth` failed with the sentence
# "has no system of record" about a line naming one; `1:N` was "no cardinality";
# and `pending`, the first state of every payment system in existence, was
# refused as a placeholder because one shared vacuity list was being applied to
# domain content as well as to evidence fields. A gate that rejects correct work
# gets switched off, and a gate that is off catches nothing at all. Each row
# below is one of those forms, as a fixture.
# ---------------------------------------------------------------------------

_GOOD_FIG = {"label": "gmv", "snapshot_id": "snap-2026-07",
             "query": "SELECT SUM(amount) FROM orders",
             "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
             "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}


def _fig(root, **over):
    fig = json.loads(json.dumps(_GOOD_FIG))
    for k, v in over.items():
        if k == "rerun":
            fig["rerun"].update(v)
        else:
            fig[k] = v
    write(root, "numbers-manifest.json", {"figures": [fig]})


@case("a-second-derivation-that-is-only-a-comment-is-not-a-derivation", "numbers", "FAIL")
def r6a(root):
    # The reproduction, one character long. `#` is not blank, is not in
    # VACUOUS_VALUES, and derivation_fold turns it into "".
    _fig(root, second_derivation="#")


@case("a-second-derivation-that-is-a-comment-with-words-in-it-is-not-a-derivation",
      "numbers", "FAIL")
def r6b(root):
    _fig(root, second_derivation="-- rerun on 2026-07-26 by hand, same query")


@case("a-primary-derivation-that-is-only-a-comment-is-not-a-derivation", "numbers", "FAIL")
def r6c(root):
    # The same hole in the other direction: the FIRST derivation could be a
    # comment too, and then the second one "differed" from nothing.
    _fig(root, query="-- see the dashboard")


@case("an-infinite-figure-is-not-a-zero-drift-measurement", "numbers", "FAIL")
def r6d(root):
    # float() accepts "inf", "Infinity" and "1e400", so two infinities compared
    # equal and bought the strongest sentence this project prints.
    _fig(root, rerun={"primary": "inf", "secondary": "inf"})


@case("a-not-a-number-figure-is-not-a-measurement", "numbers", "FAIL")
def r6e(root):
    _fig(root, rerun={"primary": "nan", "secondary": "nan"})


@case("a-negative-row-count-is-not-a-row-count", "migration", "FAIL")
def r6f(root):
    # -1 == -1, so "1 row-count comparison(s) matched" was printed about a pair
    # of counts no table has ever had.
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": -1, "after_reverse": -1}})


@case("an-infinite-row-count-is-not-a-row-count", "migration", "FAIL")
def r6g(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": "inf", "after_reverse": "inf"}})


@case("a-fractional-row-count-is-not-a-row-count", "migration", "FAIL")
def r6h(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": 2.5, "after_reverse": 2.5}})


@case("a-zero-row-count-is-still-a-count", "migration", "PASS")
def r6i(root):
    # The control. A migration over an empty table counted zero rows and counted
    # them, and rejecting that would be rejecting honest work.
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": 0, "after_reverse": 0}})


@case("self-approval-is-not-approval", "sig", "FAIL")
def r6j(root):
    # One person, one key: they authored the commit, signed it, and wrote their
    # own name into the Approved-by trailer. The gate compared nothing and
    # printed "signed commit carries Approved-by: Dana Author" over it.
    return _approval_with_sig(root, "G", approver="Dana Author <dana@example.com>",
                              authors=("Dana Author", "dana@example.com"))


@case("self-approval-by-email-alone-is-not-approval", "sig", "FAIL")
def r6k(root):
    return _approval_with_sig(root, "G", approver="dana@example.com",
                              authors=("Dana Author", "dana@example.com"))


@case("a-second-party-approval-still-passes", "sig", "PASS")
def r6l(root):
    # The control for the two above: the whole point is a SECOND party, so one
    # must still pass or the gate is unusable.
    return _approval_with_sig(root, "G", approver="Sam Reviewer <sam@example.com>",
                              authors=("Dana Author", "dana@example.com"))


# --- the honest forms an engineer writes -----------------------------------

# Both bullets carry their own rejection verdict, because the floor counts
# only what is established: under a NEUTRAL heading (Options considered,
# Alternatives, Trade-offs) an unmarked bullet might be the chosen option
# written without a marker, so it is not counted as a rejection. These
# fixtures probe the HEADING vocabulary, so their items are marked and the
# heading stays the only variable.
_ADR_BODY = ("- Synchronous call to the ledger: rejected because it ties checkout latency "
             "to ledger availability.\n"
             "- Nightly batch reconciliation: rejected because it misses the one business "
             "day requirement.\n")


def _adr(root, rejected_heading, criteria="## Criteria\nsettlement latency, operational load\n",
         body=None):
    write(root, "03-adr.md",
          "# ADR\n" + criteria + rejected_heading + "\n" + (body if body is not None else _ADR_BODY)
          + "## Decision\nPublish refund events to a queue and settle asynchronously.\n"
            "## Consequences\nOne more moving part to operate.\n"
            "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("adr-heading-options-considered-passes", "adr", "PASS")
def r6m(root):
    _adr(root, "## Options considered")


@case("adr-heading-considered-options-madr-passes", "adr", "PASS")
def r6n(root):
    # The MADR template's own heading. A canonical MADR document failed outright,
    # and the message listed four accepted FORMS and no accepted WORDS.
    _adr(root, "## Considered options")


@case("adr-heading-alternatives-bare-passes", "adr", "PASS")
def r6o(root):
    _adr(root, "## Alternatives")


@case("adr-heading-other-options-passes", "adr", "PASS")
def r6p(root):
    _adr(root, "## Other options")


@case("adr-subheading-not-chosen-passes", "adr", "PASS")
def r6q(root):
    _adr(root, "## Alternatives",
         body="### Not chosen: synchronous ledger call\nTies checkout latency to the ledger.\n"
              "### Not chosen: nightly batch\nMisses the one business day requirement.\n")


@case("adr-subheading-we-did-not-pick-passes", "adr", "PASS")
def r6r(root):
    _adr(root, "## Alternatives",
         body="### We did not pick a synchronous ledger call\nIt ties checkout to the ledger.\n"
              "### We did not pick nightly batch\nIt misses the one business day requirement.\n")


@case("adr-criteria-heading-deciding-factors-passes", "adr", "PASS")
def r6s(root):
    _adr(root, "## Rejected alternatives",
         criteria="## Deciding factors\nsettlement latency, operational load\n")


@case("the-same-rejected-alternative-twice-is-one-alternative", "adr", "FAIL")
def r6t(root):
    # gate_numbers learned that a figure listed twice is one figure; this
    # threshold did not, so a copy-paste cleared "an ADR needs at least 2".
    _adr(root, "## Rejected alternatives",
         body="- Nightly batch reconciliation: misses the one business day requirement.\n"
              "- Nightly batch reconciliation: misses the one business day requirement.\n")


def _model(root, entities, relationships="## Relationships\n- Customer to Order: one-to-many.\n"):
    write(root, "05-data-model.md", "# Data model\n## Entities\n" + entities + relationships)


@case("datamodel-source-of-truth-passes", "datamodel", "PASS")
def r6u(root):
    _model(root, "- Customer: source of truth: the CRM.\n- Order: source of truth: the OMS.\n")


@case("datamodel-owner-passes", "datamodel", "PASS")
def r6v(root):
    _model(root, "- Customer: owner: the CRM.\n- Order: owner: the OMS.\n")


@case("datamodel-sor-abbreviation-passes", "datamodel", "PASS")
def r6w(root):
    _model(root, "- Customer: SoR: the CRM.\n- Order: SoR: the OMS.\n")


@case("datamodel-mastered-by-passes", "datamodel", "PASS")
def r6x(root):
    _model(root, "- Customer: mastered by the CRM.\n- Order: authoritative source: the OMS.\n")


@case("datamodel-table-with-a-system-of-record-column-passes", "datamodel", "PASS")
def r6y(root):
    # The column says what the cell is. Requiring every cell to restate its own
    # column header made five entities into five identical FAILs, and the eval
    # that certified "or as table rows" was written in a shape no author uses.
    _model(root,
           "| Entity | System of record | Notes |\n| --- | --- | --- |\n"
           "| Customer | the CRM | core |\n| Order | the OMS | core |\n")


@case("datamodel-a-still-nameless-owner-in-a-table-column-fails", "datamodel", "FAIL")
def r6z(root):
    # The control: reading the column must not turn an empty cell into an answer.
    _model(root,
           "| Entity | System of record | Notes |\n| --- | --- | --- |\n"
           "| Customer | TBD | core |\n| Order | the OMS | core |\n")


def _cardinality_case(root, token):
    _model(root, "- Customer: system of record: the CRM.\n- Order: system of record: the OMS.\n",
           "## Relationships\n- Customer to Order: %s.\n" % token)


@case("datamodel-cardinality-1-to-N-passes", "datamodel", "PASS")
def r6aa(root):
    _cardinality_case(root, "1:N")


@case("datamodel-cardinality-N-to-1-passes", "datamodel", "PASS")
def r6ab(root):
    _cardinality_case(root, "N:1")


@case("datamodel-cardinality-uml-multiplicity-passes", "datamodel", "PASS")
def r6ac(root):
    _cardinality_case(root, "1..*")


@case("datamodel-cardinality-1-to-many-hyphenated-passes", "datamodel", "PASS")
def r6ad(root):
    _cardinality_case(root, "1-to-many")


@case("datamodel-cardinality-one-to-many-spaced-passes", "datamodel", "PASS")
def r6ae(root):
    _cardinality_case(root, "one to many")


@case("datamodel-an-undecided-cardinality-still-fails", "datamodel", "FAIL")
def r6af(root):
    # The control. Widening the notation must not widen it to nothing.
    _cardinality_case(root, "not decided yet")


_STATE_MODEL = ("# Data model\n## Entities\n- Payment: system of record: the ledger.\n"
                "## Status and lifecycle\nstatus: pending | captured | settled\n"
                "## Relationships\n- Payment to Payment: one-to-one.\n")


@case("pending-is-a-payment-state-not-a-placeholder", "diagrams", "PASS")
def r6ag(root):
    # The quotable one. A senior engineer models a payment lifecycle, uses the
    # word every payment system uses for its first state, and is told the state
    # "appears nowhere else in the dossier" while it sits declared four lines
    # above under a heading called Lifecycle, because an internal list of
    # placeholder tokens like tbd and xxx happens to contain it. The implied fix
    # was to rename a domain concept to satisfy a linter.
    write(root, "05-data-model.md", _STATE_MODEL)
    write(root, "06-diagrams.md",
          "# Diagrams\n## Lifecycle\n```mermaid\nstateDiagram-v2\n"
          "  [*] --> pending\n  pending --> captured\n  captured --> settled\n```\n")


@case("none-is-a-domain-value-not-a-placeholder", "diagrams", "PASS")
def r6ah(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Shipment: system of record: the WMS.\n"
          "## Status and lifecycle\nstatus: none | booked | delivered\n"
          "## Relationships\n- Shipment to Shipment: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Lifecycle\n```mermaid\nstateDiagram-v2\n"
          "  [*] --> none\n  none --> booked\n  booked --> delivered\n```\n")


@case("a-state-called-tbd-is-still-a-placeholder", "diagrams", "FAIL")
def r6ai(root):
    # The control for the two above: scoping the list to domain content must not
    # empty it. `tbd` is a note to the author in any context, so it is not a
    # declared state however it is written down, the diagram node that names it
    # does not trace, and the FAIL says which of the orphans name nothing.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Shipment: system of record: the WMS.\n"
          "## Status and lifecycle\nstatus: tbd | booked\n"
          "## Relationships\n- Shipment to Shipment: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Lifecycle\n```mermaid\nstateDiagram-v2\n"
          "  [*] --> tbd\n  tbd --> booked\n```\n")


@case("mermaid-c4context-passes", "diagrams", "PASS")
def r6aj(root):
    # The canonical Mermaid dialect for the system-context diagram SKILL.md
    # Phase 5 asks a T2 or T3 author to draw. Read as a flowchart, it turned its
    # own keywords Person and System into the orphans it then reported.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "- Payment: system of record: the ledger.\n"
          "## Relationships\n- Customer to Payment: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Context\n```mermaid\nC4Context\n  title Payments\n"
          "  Enterprise_Boundary(b0, \"Acme\") {\n"
          "    Person(Customer, \"Customer\", \"a buyer\")\n"
          "    System(Payment, \"Payment\", \"the payment service\")\n"
          "  }\n  Rel(Customer, Payment, \"pays with\")\n```\n")


@case("mermaid-block-beta-passes", "diagrams", "PASS")
def r6ak(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "- Payment: system of record: the ledger.\n"
          "## Relationships\n- Customer to Payment: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Blocks\n```mermaid\nblock-beta\n  columns 2\n  Customer Payment\n```\n")


@case("an-unreadable-diagram-dialect-is-nodata-not-a-failure", "diagrams", "NO-DATA")
def r6al(root):
    # SKILL.md L5's own rule: a type this tool cannot trace is NO-DATA and not a
    # failure. Read as a flowchart, an unknown dialect invented orphans out of
    # its own statement keywords.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to Customer: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Something new\n```mermaid\nzenUmlDiagram\n  A.method(B)\n```\n")


# --- discovery: what the walk reaches --------------------------------------

@case("a-stray-intake-in-the-root-does-not-hide-the-dossiers-below-it", "designrun", "blocked")
def r6am(root):
    # `if is_dossier(root): targets = [root]` skipped the walk entirely, so one
    # 00-intake.json in a repository root made every dossier under it invisible
    # and --strict exited 0 over unedited templates. sbe_intake.py wrote exactly
    # that file to wherever it was run, and the README says to run it.
    write(root, "00-intake.json",
          {"tier": "T0", "answers": TIER_ANSWERS["T0"], "override": None})
    for name in ("01-purpose.md", "02-process.md", "03-adr.md", "04-technology-map.md",
                 "05-data-model.md", "06-diagrams.md", "07-verification.md"):
        write(root, "design/payhook/" + name,
              "# Section\n<!-- SBE-TEMPLATE-UNFILLED replace this -->\nfill this in please\n")
    return design_run(root)


@case("a-dossier-in-a-directory-called-vendor-is-still-a-dossier", "designrun", "blocked")
def r6an(root):
    # Discovery must not depend on what somebody named a directory. `mv plain
    # vendor` turned two FAILs into two NO-DATAs at exit 0, and the evidence line
    # said "no directory contains 00-intake.json" about a tree that held one.
    write(root, "vendor/refunds/00-intake.json",
          {"tier": "T2", "answers": TIER_ANSWERS["T2"], "override": None})
    write(root, "vendor/refunds/01-purpose.md", PURPOSE)
    return design_run(root)


@case("a-manifest-in-a-directory-called-vendor-is-still-read", "numbers", "FAIL")
def r6ao(root):
    write(root, "vendor/reports/numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "", "query": "SELECT 1",
        "second_derivation": "SELECT 2", "rerun": {"ran": True, "primary": 1, "secondary": 1}}]})


@case("a-real-virtualenv-is-still-pruned-by-its-marker", "lints", "PASS")
def r6ap(root):
    # The control for the three above: detection moved from the NAME to the
    # marker, and the marker still has to work, or the one gate a .sbe-exempt
    # cannot waive starts failing teams over code they did not write.
    write(root, "src/app.py", "def f():\n    return 1\n")
    write(root, "toolchain/pyvenv.cfg", "home = /usr/bin\n")
    write(root, "toolchain/lib/thirdparty.py",
          "try:\n    f()\nexcept Exception:\n    pass\n")  # sbe: allow-silent lint FIXTURE, see s5
    return run_score_lints([root])


# --- the artifacts nobody had a content rule for ----------------------------

_UNRELATED = {
    "01-purpose.md": "# Purpose\nBananas ripen faster beside an apple.\n",
    "02-process.md": "# Process\nLawnmower maintenance happens each spring.\n",
    "03-adr.md": DOSSIER["03-adr.md"],
    "04-technology-map.md": "# Technology map\n| Machine | Make |\n|---|---|\n"
                            "| Tractor | Fordson |\n",
    "05-data-model.md": DOSSIER["05-data-model.md"],
    "06-diagrams.md": DOSSIER["06-diagrams.md"],
    "07-verification.md": "# Verification\nWe verify Mars by telescope on clear nights.\n",
}


@case("a-t3-dossier-of-unrelated-artifacts-is-not-a-dossier", "artifacts", "FAIL")
def r6aq(root):
    # Five of five PASS on a T3 dossier about bananas, lawnmowers, tractors and
    # Mars. `present()` was the entire content rule for four of the seven
    # artifacts: two words and eight characters each. Every sentence the report
    # printed was narrowly true and the dossier as a whole proved nothing.
    write(root, "00-intake.json", {"tier": "T3", "answers": TIER_ANSWERS["T3"], "override": None})
    for name, body in _UNRELATED.items():
        write(root, name, body)


@case("a-t3-dossier-about-one-system-still-passes", "artifacts", "PASS")
def r6ar(root):
    # The control, and the one that matters more: the coherence rule must not
    # reject an honest dossier whose artifacts talk about different LAYERS of the
    # same system.
    write(root, "00-intake.json", {"tier": "T3", "answers": TIER_ANSWERS["T3"], "override": None})
    for name, body in DOSSIER.items():
        write(root, name, body)


# --- the lint aimed at SQL, on SQL ------------------------------------------

@case("a-conflict-skipping-upsert-in-a-sql-file-is-caught", "lints", "FAIL")
def r6as(root):
    # L11 names .sql first and the pattern needed a Python `.execute(` on the
    # same line, so the one lint that exists for warehouse work could not fire on
    # a warehouse file.
    write(root, "load.sql",
          "INSERT INTO orders (id, amount)\nSELECT id, amount FROM staging\n"
          "ON CONFLICT (id) DO NOTHING;\n")  # sbe: allow-silent this is the lint FIXTURE: the skipping upsert is the defect under test, written into a temp file and never executed here
    return run_score_lints([root])


@case("a-conflict-skipping-upsert-split-over-lines-is-caught", "lints", "FAIL")
def r6at(root):
    write(root, "load.sql",
          "INSERT INTO orders (id) SELECT id FROM staging\nON CONFLICT (id)\n  DO NOTHING;\n")  # sbe: allow-silent lint FIXTURE, see the case above
    return run_score_lints([root])


@case("a-conflict-skipping-upsert-in-a-python-variable-is-caught", "lints", "FAIL")
def r6au(root):
    write(root, "load.py",
          "SQL = \"INSERT INTO orders (id) VALUES (1) ON CONFLICT (id) DO NOTHING\"\n"  # sbe: allow-silent lint FIXTURE, see the case above
          "def load(conn):\n    conn.execute(SQL)\n")
    return run_score_lints([root])


@case("a-real-upsert-that-updates-is-not-a-silent-failure", "lints", "PASS")
def r6av(root):
    # The control. ON CONFLICT DO UPDATE is the legitimate upsert and flagging it
    # would be the false positive that gets the lint switched off.
    write(root, "load.sql",
          "INSERT INTO orders (id, amount) VALUES (1, 2)\n"
          "ON CONFLICT (id) DO UPDATE SET amount = excluded.amount;\n")
    return run_score_lints([root])


@case("the-lint-reads-a-users-own-file-called-sbe-score", "lints", "FAIL")
def r6aw(root):
    # The self-skip was a BASENAME comparison against every file in the CALLER's
    # tree, so a user's own sbe_score.py was never opened and "1 file(s) scanned,
    # clean" was printed over a directory holding two, one of which swallowed
    # every error it met.
    write(root, "clean.py", "def f():\n    return 1\n")
    write(root, "sbe_score.py",
          "def g():\n    try:\n        risky()\n    except:\n        pass\n")  # sbe: allow-silent lint FIXTURE, see s5
    return run_score_lints([root])


# --- staleness arithmetic ---------------------------------------------------

def _reviews(root, body):
    write(root, "99-System/telemetry/reviews.jsonl", body)
    return root


@case("a-review-dated-in-the-future-is-a-broken-record", "score", "FAIL")
def r6ax(root):
    # `age_days` returns a negative number for a future timestamp and four checks
    # compared it with <= or > without a floor, so review-cadence printed
    # "last review: -1620.2d ago" and called it a PASS, and the genuinely stale
    # 2020 review in the same ledger was masked because max() picked the future
    # row.
    return score_check("review-cadence", _reviews(
        root, "{\"ts\": \"2020-01-01T00:00:00Z\"}\n{\"ts\": \"2031-01-01T00:00:00Z\"}\n"))


@case("a-correction-dated-in-the-future-is-a-broken-record", "score", "FAIL")
def r6ay(root):
    write(root, "99-System/telemetry/corrections.jsonl", "{\"ts\": \"2031-01-01T00:00:00Z\"}\n")
    return score_check("correction-latency", root)


@case("a-session-dated-in-the-future-is-a-broken-record", "score", "FAIL")
def r6az(root):
    return score_check("ledger-coverage", _ledger(
        root, "{\"session_id\": \"s1\", \"ts\": \"2031-01-01T00:00:00Z\"}\n"))


@case("six-copies-of-one-rating-are-one-rating", "score", "FAIL")
def r6ba(root):
    # The dedupe rule gate_numbers learned, applied to the two thresholds in this
    # file that counted the same row N times.
    write(root, "99-System/telemetry/ratings.jsonl",
          "".join("{\"score\": 5}\n" for _ in range(6)))
    return score_check("felt-outcome-ratings", root)


@case("six-distinct-ratings-still-pass", "score", "PASS")
def r6bb(root):
    write(root, "99-System/telemetry/ratings.jsonl",
          "".join("{\"score\": %d}\n" % s for s in (5, 4, 3, 2, 1, 0)))
    return score_check("felt-outcome-ratings", root)


# --- the meta-test's own lint, probed with the spellings that evaded it ------

@case("the-meta-test-lint-sees-five-more-spellings-of-the-verdict-pass", "guard", "caught")
def r6bc(root):
    """Five ways to return PASS that the lint could not see, from the review.

    Each was proved against the shipped tree by dropping the function into
    tools/ and watching the run print 0 failures: an f-string is a JoinedStr and
    not a Constant; a name bound by `from sbe_checks import VERDICTS` is bound by
    no Assign; a lambda is not a FunctionDef; `.upper()` and `"".join(...)` are
    Calls. Enumerating node types is the hand-fix-the-instance loop this project
    exists to break, so the lint was inverted: a (verdict, evidence) return whose
    head is not PROVABLY one of FAIL or NO-DATA is a candidate, and the way out
    is a named allowlist rather than another node type.
    """
    meta = SourceFileLoader("meta_probe2",
                            os.path.join(HERE, "test_no_data_class.py")).load_module()
    tools = os.path.join(root, "tools")
    os.makedirs(tools)
    write(tools, "exotic.py",
          "from sbe_checks import VERDICTS\n"
          "_ok = lambda: \"PASS\"\n\n"
          "def gate_fstring(root):\n"
          "    return f\"PASS\", \"examined nothing\"\n\n"
          "def gate_imported_constant(root):\n"
          "    return VERDICTS[0], \"examined nothing\"\n\n"
          "def gate_lambda(root):\n"
          "    return _ok(), \"examined nothing\"\n\n"
          "def gate_upper(root):\n"
          "    return \"pass\".upper(), \"examined nothing\"\n\n"
          "def gate_join(root):\n"
          "    return \"\".join([\"PA\", \"SS\"]), \"examined nothing\"\n\n"
          "def gate_indirect(root):\n"
          "    verdict = compute()\n"
          "    return verdict, \"examined nothing\"\n\n"
          "def honest_fail(root):\n"
          "    return \"FAIL\", \"this one is provably not a pass\"\n")
    original = meta.TOOLS_DIR
    try:
        meta.TOOLS_DIR = tools
        found = {name for _fn, name, _why in meta.pass_returning_functions()}
    finally:
        meta.TOOLS_DIR = original
    want = {"gate_fstring", "gate_imported_constant", "gate_lambda", "gate_upper",
            "gate_join", "gate_indirect"}
    missing = sorted(want - found)
    if missing:
        return "the lint did not see %s" % ", ".join(missing)
    if "honest_fail" in found:
        return "the lint flagged honest_fail, which provably returns FAIL"
    return "caught"


@case("intake-writes-its-file-into-the-directory-it-was-given", "guard", "written-where-asked")
def r6bd(root):
    # It took no path argument, accepted one, ignored it, and wrote to wherever
    # it was run from, printing a line that reads like it worked.
    #
    # The answer script feeds the five tier questions and then H4's origin
    # question, which is the shipped prompt sequence `tools/test_sbe_intake.py`
    # already drives. Feeding one answer short does not test where the file
    # lands: the tool hits EOF, says so, and writes nothing, which is correct
    # behaviour rather than the defect this case exists to catch.
    target = os.path.join(root, "design", "payhook")
    os.makedirs(target)
    out = subprocess.run([sys.executable, INTAKE, target], input="no\nn\ny\nn\nnone\nfeature\n",
                         capture_output=True, text=True, cwd=root)
    if out.returncode != 0:
        return "exit %d: %s" % (out.returncode, out.stdout.strip()[:80])
    if os.path.isfile(os.path.join(root, "00-intake.json")):
        return "it wrote to the directory it was run from, not the one it was given"
    return ("written-where-asked" if os.path.isfile(os.path.join(target, "00-intake.json"))
            else "no 00-intake.json was written anywhere")


@case("intake-help-does-not-block-on-stdin", "guard", "help-printed")
def r6be(root):
    out = subprocess.run([sys.executable, INTAKE, "--help"], input="",
                         capture_output=True, text=True, timeout=20)
    return ("help-printed" if out.returncode == 0 and "usage: sbe_intake.py" in out.stdout
            else "exit %d: %s" % (out.returncode, (out.stdout + out.stderr).strip()[:80]))


# ---------------------------------------------------------------------------
# Two laws that claimed an enforcement their tool did not deliver, which
# is this project's own worst failure class rather than an edge case.
#
# L15 said an override sets BOTH fields and that they must agree. Only the reason
# was enforced: a tier differing from the computed one with `override` null took a
# path nothing checked, and `override: null` is what every intake file the shipped
# tool writes starts with, so the unenforced path was the default path.
#
# L4 said a bullet in some other section "is prose and is not read as an entity".
# The fallback for a model with no entity heading read exactly that bullet, so an
# honest Notes list FAILed as two sourceless entities, and the same list with an
# ownership phrase in it PASSed as "2 entities, each with a system of record" over
# a file that declares no entity at all.

@case("a-moved-tier-with-a-null-override-field-fails", "artifacts", "FAIL")
def s7a(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T3_ANSWERS, "override": None,
                                   "override_reason": "the auditor requires the full dossier"})
    write(root, "01-purpose.md", PURPOSE)


@case("an-incomplete-override-names-both-fields", "evidence", "both-named")
def s7b(root):
    write(root, "00-intake.json", {"tier": "T1", "answers": T3_ANSWERS, "override": None,
                                   "override_reason": "the auditor requires the full dossier"})
    write(root, "01-purpose.md", PURPOSE)
    line = gate_line(root, "artifacts")
    if line.split()[1:2] != ["FAIL"]:
        return line
    return ("both-named" if "override field" in line and "override_reason" in line else line)


@case("an-override-field-that-moves-no-tier-is-reported-as-moving-nothing",
      "evidence", "no-move-stated")
def s7c(root):
    # The other direction of "both fields agree": the fields agree with each other
    # and with the answers, so nothing was overridden. Silence there would let a
    # stale field read as a control that was exercised.
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": "T1",
                                   "override_reason": "kept at T1 after the review"})
    write(root, "01-purpose.md", PURPOSE)
    write(root, "08-behaviour.md", BEHAVIOUR)
    line = gate_line(root, "artifacts")
    if line.split()[1:2] != ["PASS"]:
        return line
    return "no-move-stated" if "moved no tier" in line else line


@case("a-notes-list-with-no-entity-heading-is-not-read-as-entities",
      "evidence", "prose-left-as-prose")
def s7d(root):
    write(root, "05-data-model.md",
          "# Data model\n## Notes\n- Rollout plan: we ship on Tuesday behind a flag\n"
          "- Open question: who owns retention after the migration\n")
    line = gate_line(root, "datamodel")
    return ("prose-left-as-prose"
            if "Rollout plan'" not in line and "no entity" in line else line)


@case("a-notes-list-that-names-an-owner-is-not-an-entity-count", "datamodel", "NO-DATA")
def s7e(root):
    # The mirror case: the same prose with an ownership phrase in it used to buy
    # "2 entities, each with a system of record" over a file holding no entity.
    write(root, "05-data-model.md",
          "# Data model\n## Notes\n- Rollout plan: owned by the platform team\n"
          "- Open question: owner: the data guild\n"
          "## Relationships\n- Customer to Order: one-to-many.\n")


@case("a-data-model-with-no-entity-heading-is-still-checked", "datamodel", "FAIL")
def s7f(root):
    # The half that must survive the fix: a model whose heading reads "Conceptual
    # model" is not exempt from the rule just because it never says "entities".
    write(root, "05-data-model.md",
          "# Data model\n## Conceptual model\n- Customer: system of record: the CRM.\n"
          "- Order: system of record: TBD\n"
          "## Relationships\n- Customer to Order: one-to-many.\n")


@case("a-data-model-with-no-entity-heading-says-what-it-read", "evidence", "disclosed")
def s7g(root):
    write(root, "05-data-model.md",
          "# Data model\n## Conceptual model\n- Customer: system of record: the CRM.\n"
          "- Order: system of record: the OMS.\n"
          "## Relationships\n- Customer to Order: one-to-many.\n")
    line = gate_line(root, "datamodel")
    if line.split()[1:2] != ["NO-DATA"]:
        return line
    return ("disclosed" if "Customer" in line and "entit" in line else line)


@case("a-diagram-still-traces-to-a-model-with-no-entity-heading", "diagrams", "PASS")
def s7h(root):
    # Tracing reads the wider set on purpose: a name it does not know produces a
    # false orphan, so the guess there runs the other way from the data-model
    # check's. This pins that the scoping fix did not invent orphans.
    write(root, "05-data-model.md",
          "# Data model\n## Conceptual model\n- Customer: system of record: the CRM.\n"
          "- Order: system of record: the OMS.\n")
    write(root, "06-diagrams.md", "```mermaid\nflowchart LR\n  Customer --> Order\n```\n")


# ---------------------------------------------------------------------------
# The law-by-law sweep. Each of these is a sentence a law
# printed that its tool did not do, or a tool doing something its law never said.
# The fix went to whichever side was wrong, and the fixture pins it either way.

def score_lint_line(args):
    """The whole silent-failure-lints line, so a fixture can pin the EVIDENCE."""
    out = subprocess.run([sys.executable, SCORE] + args, capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.strip().startswith("silent-failure-lints"):
            return line.strip()
    return ""


@case("a-number-outside-every-range-is-reported-not-dropped", "decide", "reported")
def w1(root):
    # It vanished: `deploying_teams=0` produced "no criterion was answered" over a
    # run that answered one, and beside a second criterion it produced a confident
    # recommendation with nothing saying half the input had been discarded, while
    # the type error one line above it was reported.
    r = _decide.recommend(_TABLES["shape"], {"deploying_teams": 0, "consistency": "strong"})
    return "reported" if any("deploying_teams=0" in u for u in r["unrecognized"]) else "dropped"


@case("a-flip-condition-belongs-to-the-recommendation-not-the-table", "decide", "its-own")
def w1b(root):
    # L12's promise was guarded only by "the string is truthy", so deleting the
    # per-recommendation lookup and falling back to the one table-wide string
    # would still have passed CI, which is the defect that string was added to
    # fix: a run recommending services off nine teams and high isolation was
    # handed a flip condition naming two conditions that were already true.
    shared = _TABLES["shape"]["flip"]
    seen = set()
    for teams, cons, ops, iso in ((1, "strong", "low", "low"), (9, "eventual", "high", "high"),
                                  (3, "eventual", "high", "high")):
        r = _decide.recommend(_TABLES["shape"], {"deploying_teams": teams, "consistency": cons,
                                                 "ops_maturity": ops, "failure_isolation": iso})
        if r["flip_condition"] == shared:
            return "%s was handed the table-wide flip condition" % r["recommendation"]
        seen.add(r["flip_condition"])
    return "its-own" if len(seen) == 3 else "two recommendations share one flip condition"


@case("a-review-id-of-more-than-one-word-is-a-pointer-not-a-failure", "gaterun", "NO-DATA")
def w2(root):
    # `^Reviewed-in:\s*(\S+)$` was a shape rule no law declared: a human-written
    # id fell past the branch and got the FAIL that says no review id is recorded.
    git_init(root)
    open(os.path.join(root, "APPROVAL"), "w").write("touches the partner payout path\n")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm",
                    "payout batching\n\nReviewed-in: PR 99999 on the payments board"], check=True)
    out = subprocess.run([sys.executable, GATE, "approval", root], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.strip().startswith("approval"):
            return line.split()[1]
    return "no-line"


@case("an-exemption-with-no-reason-waives-nothing", "lints", "FAIL")
def w3(root):
    # L11 writes the marker as `# sbe: allow-silent <reason>` and the reason was
    # decoration: the bare marker suppressed the finding, in the one gate a
    # .sbe-exempt cannot waive.
    write(root, "bad.py", "try:\n    f()\nexcept:  # sbe" + ": allow-silent\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    return run_score_lints([root])


@case("an-exemption-carrying-a-reason-still-waives", "lints", "PASS")
def w4(root):
    write(root, "ok.py", "def g():\n    return 2\n")
    write(root, "bad.py", "try:\n    f()\nexcept:  # sbe" + ": allow-silent boundary read, the "  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
                          "caller handles the absence\n    pass\n")
    return run_score_lints([root])


@case("a-checked-subprocess-call-with-a-nested-call-is-not-a-hit", "lints", "PASS")
def w5(root):
    # A lint firing on correct code is how a gate gets switched off: the lookahead
    # ended at the closing paren of the NESTED call and never saw check=True.
    write(root, "ok.py", 'import shlex, subprocess\nsubprocess.run(shlex.split("ls -l"), check=True)\n')
    return run_score_lints([root])


@case("a-long-conflict-skipping-upsert-is-still-caught", "lints", "FAIL")
def w6(root):
    # The pattern gave up after 300 characters, so the law's "stops at the
    # statement's semicolon" was false about any statement longer than that.
    write(root, "up.sql", "INSERT INTO t (id) VALUES (1) ON CONFLICT (id) /* %s */ DO NOTHING;\n"  # sbe: allow-silent this is the lint FIXTURE: the skipping upsert is the defect under test, written into a temp file and never executed here
          % ("x" * 400))
    return run_score_lints([root])


@case("a-truncated-hit-list-says-how-many-it-did-not-name", "lints", "counted")
def w7(root):
    for i in range(7):
        write(root, "f%d.py" % i, "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    line = score_lint_line([root])
    return "counted" if "and 2 more not named" in line else line


@case("an-empty-file-is-not-counted-as-source-examined", "lints", "named")
def w8(root):
    write(root, "empty.py", "")
    write(root, "ok.py", "def f():\n    return 1\n")
    line = score_lint_line([root])
    return "named" if "hold nothing to examine" in line and "empty.py" in line else line


@case("an-artifact-coherent-with-a-file-the-tier-did-not-require-passes", "artifacts", "PASS")
def w9(root):
    # The sibling set was the TIER-REQUIRED list, so a T1 purpose brief sharing
    # four words with the 04-technology-map beside it FAILed with the sentence
    # "no substantive word in it appears in any sibling artifact".
    write(root, "00-intake.json", {"tier": "T1", "answers": T1_ANSWERS, "override": None})
    write(root, "01-purpose.md",
          "# Purpose\nRefund settlement latency must fall under one business day.\n")
    write(root, "04-technology-map.md",
          "# Technology map\n| Component | Technology | Owner |\n|---|---|---|\n"
          "| Widget | Postgres holding refund settlement rows | Platform |\n")
    write(root, "08-behaviour.md", DOSSIER["08-behaviour.md"])


@case("an-inline-edge-label-is-named-in-the-skipped-tokens", "evidence", "named")
def w10(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "- Order: system of record: the OMS.\n")
    write(root, "06-diagrams.md",
          "```mermaid\nflowchart LR\n  C[Customer] -- Ledger --> O[Order]\n```\n")
    line = gate_line(root, "diagrams")
    return "named" if "Ledger (an inline edge label, not a node)" in line else line


@case("a-truncated-entity-failure-says-how-many-it-did-not-show", "evidence", "counted")
def w11(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n" + "".join("- %s\n" % n for n in
          ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel")))
    line = gate_line(root, "datamodel")
    return "counted" if "and 2 more not shown" in line else line


@case("the-adr-pass-line-claims-only-what-the-threshold-measured", "evidence", "measured")
def w12(root):
    # Two bullets that name two options and give no reason at all cleared the
    # threshold, and the verdict said "rejected with a stated reason" over them.
    # No rule here can tell a reason from a longer name, so the sentence says
    # what it measured and names the rest as human review.
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nlatency, freshness\n## Rejected alternatives\n"
          "- Synchronous ledger call\n- Nightly batch reconciliation\n"
          "## Decision\nPublish to a queue.\n## Consequences\nOne more moving part.\n"
          "## What would flip this\nSub-second freshness becomes a requirement.\n")
    line = gate_line(root, "adr")
    if line.split()[1:2] != ["PASS"]:
        return line
    return ("measured" if "stated reason" not in line and "human review" in line else line)


@case("the-lint-numbers-this-repository-prints-are-the-numbers-it-computes",
      "docs", "consistent")
def w13(root):
    """SKILL.md states this repository's own lint run. Both numbers were stale.

    A number in a doc that nothing recomputes is a stale evidence string with a
    longer half-life, which is the argument the eval-count and checker-line
    guards beside this one already make. This one recomputes the two numbers L11
    prints about this repository from a live run.
    """
    import re as _re
    body = open(os.path.join(_REPO, "SKILL.md"), errors="replace").read()
    m = _re.search(r"(\w+) waived hits and (\w+) files that were scanned and genuinely found clean",
                   body)
    if not m:
        return "SKILL.md no longer states the waived-hit and clean-file counts of its own lint run"
    # Written either way: the sentence carried spelled-out words for a wave and
    # digits after, and a guard that reads one form and not the other is a guard
    # that goes quiet on an edit rather than failing on one.
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
    def _n(tok):
        return int(tok) if tok.isdigit() else words.get(tok.lower(), -1)
    said = (_n(m.group(1)), _n(m.group(2)))
    # Over the TRACKED tree, not the live working directory. The lint walks
    # whatever it is pointed at, so an untracked scratch directory holding
    # Python (a reviewer's probe copy) flipped this eval for the local
    # developer while CI, on a fresh checkout with no untracked files, stayed
    # green: the gate's verdict depended on files in nobody's clone, and the
    # message it printed then blamed SKILL.md's pinned numbers. The tracked
    # files copied with their WORKING-TREE content are exactly what a reader
    # gets on their next pull, and the copy excludes the untracked scratch, so
    # this is hermetic without lagging the edit under test.
    import shutil
    listing = subprocess.run(["git", "-C", _REPO, "ls-files", "-z"],
                             capture_output=True)
    if listing.returncode != 0:
        return "could not list the tracked tree: %s" % (
            listing.stderr.decode("utf-8", "replace")[:120])
    with tempfile.TemporaryDirectory() as d:
        for rel in listing.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if not rel:
                continue
            src = os.path.join(_REPO, rel)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(d, rel)
            os.makedirs(os.path.dirname(dst) or d, exist_ok=True)
            shutil.copy(src, dst)
        # Run the COPIED scorer, so its self-skip (samefile against __file__)
        # resolves to the copy it is scanning rather than the original in the
        # repo; pointed at its own tree, exactly the shipped invocation.
        copied_score = os.path.join(d, "tools", "sbe_score.py")
        out = subprocess.run([sys.executable, copied_score, d], capture_output=True, text=True)
    line = next((l for l in out.stdout.splitlines()
                 if l.strip().startswith("silent-failure-lints")), "")
    verdict = line.split()[1] if len(line.split()) > 1 else "?"
    if verdict != "PASS":
        # When the lint itself did not PASS, say so, rather than reporting the
        # absence of the counts it wanted to read as if SKILL.md had drifted.
        return "the tracked-tree lint did not PASS (got %s): %s" % (verdict, line.strip()[:120])
    waived = _re.search(r"(\d+) suppressed", line)
    clean = _re.search(r"(\d+) file\(s\) holding no match at all", line)
    if not waived or not clean:
        return "the tracked-tree lint line printed no suppressed or clean-file count: %s" % line.strip()[:120]
    got = (int(waived.group(1)), int(clean.group(1)))
    return ("consistent" if said == got else
            "SKILL.md says %r but the tracked tree has %d waived hits and %d clean files"
            % (m.group(0), got[0], got[1]))


# ---------------------------------------------------------------------------
# The ACCESS axis: evidence that exists and cannot be opened. The suite held
# zero occurrences of chmod, symlink or mkfifo, so no fixture had ever
# distinguished a check that read clean evidence from a check that read nothing.
# The meta-test sweeps the class per check; these pin the mixed shapes it cannot
# generate (some evidence readable, some not) and the evidence sentences.

_CAN_DROP_ACCESS = os.name == "posix" and os.geteuid() != 0


@case("an-unreadable-source-file-fails-the-lint", "lints", "FAIL")
def x1(root):
    # chmod 000 on a source file removed it from the lint, from the count, and
    # from the sentence: "1 file(s) scanned, clean" over a directory holding a
    # swallowed error the scan was refused access to.
    if not _CAN_DROP_ACCESS:
        return "FAIL"      # as root, access cannot be taken away; the meta-test declares the same limit
    write(root, "clean.py", "def f():\n    return 1\n")
    write(root, "evil.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    os.chmod(os.path.join(root, "evil.py"), 0)
    return run_score_lints([root])


@case("an-unreadable-directory-fails-the-lint", "lints", "FAIL")
def x2(root):
    if not _CAN_DROP_ACCESS:
        return "FAIL"
    write(root, "ok.py", "def f():\n    return 1\n")
    write(root, "locked/evil.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    os.chmod(os.path.join(root, "locked"), 0)
    try:
        return run_score_lints([root])
    finally:
        os.chmod(os.path.join(root, "locked"), 0o755)


@case("a-file-kind-the-lint-cannot-read-is-declared-not-dropped-in-silence", "evidence", "declared")
def x_scope(root):
    # Fourteen files, thirteen of them carrying a real catchable hit, and the
    # unwaivable gate printed "1 file(s) scanned, clean": every file whose name
    # did not end in one of seven extensions left through a bare `continue`
    # that reached no list and no sentence. It is the one truncation this
    # function performed silently while disclosing four others, so a Kotlin,
    # Rust, Java, C# or Scala estate passed the gate by existing. Self
    # referentially, `.sh` is outside the tuple, so the shell tools shipped
    # here had never been read by their own lint.
    #
    # The extension list is genuinely unavoidable (the patterns are written for
    # the languages they name), so the rule that applies is the project's other
    # one: what falls outside an unavoidable enumeration fails LOUDLY. The
    # count, the kinds and the withdrawal of the word "clean" are what this
    # asserts, not a longer list of extensions.
    # Spelled in two pieces so this fixture does not fire the lint against the
    # eval suite's own source, which is itself scanned by the tracked-tree run.
    sql = "INSERT INTO refunds (id, amount) VALUES (1, 2) ON CON" + \
          "FLICT (id) DO NOTHING;\n"
    write(root, "ok.py", "def f():\n    return 1\n")
    for ext in ("kt", "java", "rs", "tsx", "jsx", "php", "pgsql", "ddl",
                "sh", "c", "cpp", "cs", "scala"):
        write(root, "repo.%s" % ext, sql)
    line = score_lint_line([root])
    problems = []
    if "13 file(s)" not in line or "not opened" not in line:
        problems.append("the 13 unopened files are not counted in the sentence")
    if "no pattern that reads their kind" not in line or ".c 1" not in line:
        problems.append("the kinds that were skipped are not named")
    if "8 more not named" not in line:
        problems.append("the kinds it did not name are not counted either")
    if "clean in what was opened" not in line:
        problems.append("the word clean was not withdrawn")
    # The control: the same tree with nothing removed from consideration keeps
    # the plain sentence, so this is a disclosure rule and not a new refusal.
    for ext in ("kt", "java", "rs", "tsx", "jsx", "php", "pgsql", "ddl",
                "sh", "c", "cpp", "cs", "scala"):
        os.remove(os.path.join(root, "repo.%s" % ext))
    plain = score_lint_line([root])
    if "clean" not in plain or "not opened" in plain:
        problems.append("a tree with nothing skipped did not report plainly clean: %r" % plain[:90])
    return "declared" if not problems else "; ".join(problems)


@case("a-symlinked-source-directory-is-disclosed-not-silent", "evidence", "disclosed")
def x3(root):
    # os.walk does not follow directory symlinks, so a symlinked tree holding a
    # hit was neither walked, nor pruned, nor recorded, and the verdict printed
    # "clean" over a set that silently excluded it.
    write(root, "repo/ok.py", "def f():\n    return 1\n")
    write(root, "elsewhere/evil.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    os.symlink(os.path.join(root, "elsewhere"), os.path.join(root, "repo", "vendor_link"))
    line = score_lint_line([os.path.join(root, "repo")])
    return ("disclosed" if "symlinked directory" in line and "vendor_link" in line
            else line or "no-lint-line")


@case("fence-lint-names-a-denied-registry-instead-of-shrinking-the-list", "evidence", "occupied")
def x_fence_denial(root):
    # The dispatch aid read BROTHERSBE_REGISTRIES through a raw glob, and a
    # glob returns FEWER paths, not an error, over a directory it cannot
    # enter: chmod 000 on one project made its money-path fence vanish and,
    # with the whole tree denied, the aid printed "no live fences found" over
    # two live fences. Its one job is that no writer launches into an occupied
    # file set, so a registry it cannot read is OCCUPIED, never empty.
    if not _CAN_DROP_ACCESS:
        return "occupied"
    write(root, "reg/projA/STATE.md", "# STATE\n- agent w1 tier T1 editing tools/x.py\n")
    write(root, "reg/projB/STATE.md", "# STATE\n- agent w9 editing money paths\n")
    os.chmod(os.path.join(root, "reg", "projB"), 0)
    env = dict(os.environ,
               BROTHERSBE_REGISTRIES=os.path.join(root, "reg", "*", "STATE.md"))
    try:
        out = subprocess.run([sys.executable, TELEMETRY_TOOL, "fence-lint", root],
                             capture_output=True, text=True, env=env, timeout=60)
        blind = subprocess.run([sys.executable, TELEMETRY_TOOL, "fence-lint", root],
                               capture_output=True, text=True, timeout=60,
                               env=dict(env, BROTHERSBE_REGISTRIES=os.path.join(
                                   root, "no-such", "*", "STATE.md")))
    finally:
        os.chmod(os.path.join(root, "reg", "projB"), 0o755)
    if "agent w1 tier T1" not in out.stdout:
        return "the readable fence vanished: %s" % out.stdout.strip()[:120]
    if "could not be read" not in out.stdout or "occupied" not in out.stdout:
        return "the denial was silent: %s" % out.stdout.strip()[:160]
    # The control: a pattern matching nothing readable OR denied still says
    # "no live fences", so the denial sentence is not printed unconditionally.
    if "no live fences found" not in blind.stdout:
        return "the control run lost its no-fences sentence: %s" % blind.stdout.strip()[:120]
    return "occupied"


@case("the-lint-self-skip-is-file-identity-not-path-spelling", "evidence", "skipped itself")
def x3b(root):
    # realpath resolves symlinks and nothing else, so the same file reached
    # through a case-different spelling (TOOLS/ on macOS's default
    # case-insensitive filesystem) was two identities: the tool scanned
    # ITSELF and FAILed the honest shipped tree with four "defects" that were
    # its own regex literals. Identity is the file, not the spelling:
    # samefile compares inodes. Reproduced portably with a HARDLINK, a
    # spelling axis the case fix would not cover and case-sensitive CI can run.
    import shutil
    tdir = os.path.join(root, "tools")
    os.makedirs(tdir)
    for fn in ("sbe_score.py", "sbe_checks.py", "sbe_telemetry.py"):
        shutil.copy(os.path.join(_REPO, "tools", fn), os.path.join(tdir, fn))
    write(root, "tools/clean.py", "def f():\n    return 1\n")
    link = os.path.join(tdir, "scanner-hardlink.py")
    os.link(os.path.join(tdir, "sbe_score.py"), link)
    e = dict(os.environ)
    e.update({"BROTHERSBE_VAULT": root, "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": ""})
    out = subprocess.run([sys.executable, link, tdir], capture_output=True, text=True, env=e)
    line = next((l for l in out.stdout.splitlines()
                 if l.strip().startswith("silent-failure-lints")), "")
    if "own source was not scanned" not in line:
        return "self-skip disclosure missing: %s" % line.strip()[:120]
    if line.split()[1:2] != ["PASS"]:
        return "the tool read its own regex literals as defects: %s" % line.strip()[:120]
    return "skipped itself"


@case("unreadable-registry-files-never-shrink-the-registry", "score", "FAIL")
def x4(root):
    # chmod 000 on the registry files holding the violations turned two FAILs
    # into two PASSes whose sentences said "untagged in: none", over a registry
    # set silently reduced from five files to one.
    if not _CAN_DROP_ACCESS:
        return "FAIL"
    for i in (1, 2, 3, 4):
        write(root, "reg/bad%d.md" % i,
              "# State\n## Fences\n- agent: builder | objective: ship gate %d | open\n" % i)
    write(root, "reg/good.md",
          "# State\n## Fences\n- agent: builder | tier T2 | objective: ship the refund gate\n")
    for i in (1, 2, 3, 4):
        os.chmod(os.path.join(root, "reg", "bad%d.md" % i), 0)
    return score_check("budget-vs-tier", root,
                       env={"BROTHERSBE_REGISTRIES": os.path.join(root, "reg", "*.md")})


@case("an-unreadable-artifact-is-a-broken-claim-not-an-absence", "datamodel", "FAIL")
def x5(root):
    if not _CAN_DROP_ACCESS:
        # As root, read access cannot be taken away, so the fixture degrades to
        # a model that FAILs for a readable reason; the meta-test declares the
        # same host limit on its access scenarios.
        write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer\n"
                                        "## Relationships\n- Customer to Customer: one-to-one.\n")
        return
    write(root, "05-data-model.md", "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
                                    "## Relationships\n- Customer to Customer: one-to-one.\n")
    os.chmod(os.path.join(root, "05-data-model.md"), 0)


@case("a-directory-wearing-an-artifacts-name-is-not-an-absence", "evidence", "named")
def x6(root):
    # `mkdir 05-data-model.md` used to produce "no 05-data-model.md in this
    # dossier", a sentence asserting absence over something present. The intake
    # beside it is what makes the directory a dossier the walk enters.
    write(root, "00-intake.json", "{}")
    os.makedirs(os.path.join(root, "05-data-model.md"))
    line = gate_line(root, "datamodel")
    if line.split()[1:2] != ["FAIL"]:
        return line
    return "named" if "a directory, not a file" in line else line


@case("an-unenterable-directory-is-named-not-vanished", "evidence", "named")
def x7(root):
    # chmod 000 on a dossier directory made the run print "no directory contains
    # 00-intake.json" about a tree that held one, with five checks silently
    # removed at exit 0.
    if not _CAN_DROP_ACCESS:
        return "named"
    write(root, "design/00-intake.json", "{}")
    os.chmod(os.path.join(root, "design"), 0)
    try:
        out = subprocess.run([sys.executable, DESIGN, root], capture_output=True, text=True,
                             env=dict(os.environ, SBE_DOSSIER_ROOT=""))
    finally:
        os.chmod(os.path.join(root, "design"), 0o755)
    return ("named" if "could not be entered" in out.stdout else
            "the refused directory is not named anywhere in the run")


@case("a-fifo-receipt-is-refused-not-hung", "gaterun", "FAIL")
def x8(root):
    # A FIFO where a receipt was expected hung all three tools forever, in both
    # modes, with no verdict line at all: worse than a crash, because a crash at
    # least prints a FAIL.
    if not hasattr(os, "mkfifo"):
        return ("PLATFORM-GAP: os.mkfifo does not exist here, so the FIFO hang "
                "channel cannot be mounted; the refusal path is measured on the "
                "POSIX legs only")
    os.mkfifo(os.path.join(root, "ran-receipt.json"))
    try:
        out = subprocess.run([sys.executable, GATE, "ran", root], capture_output=True,
                             text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "the tool hung on a FIFO"
    for line in out.stdout.splitlines():
        if line.strip().startswith("ran"):
            return line.split()[1]
    return "no-verdict-line"


@case("a-pruned-tree-past-the-inspection-budget-is-still-disclosed", "evidence", "disclosed")
def x9(root):
    # The 4000-directory inspection budget was decremented in silence: once it
    # ran out, every remaining pruned tree was discarded without being looked
    # at, hidden() returned the same [] it returns when nothing was hidden, and
    # the disclosure vanished on exactly the trees it exists for (a real
    # node_modules routinely exceeds 4000 directories).
    write(root, "clean.py", "def f():\n    return 1\n")
    write(root, "aaa_cache/CACHEDIR.TAG", "")
    for i in range(4100):
        os.makedirs(os.path.join(root, "aaa_cache", "d%04d" % i))
    write(root, "zzz_cache/CACHEDIR.TAG", "")
    write(root, "zzz_cache/evil.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    line = score_lint_line([root])
    return ("disclosed" if "not fully inspected" in line and "budget ran out" in line
            else line or "no-lint-line")


# ---------------------------------------------------------------------------
# Values that are present, well-formed, and still record nothing: a NaN
# duration, an integer where a derivation's text belongs, prose that denies an
# owner, an empty ownership column answered by a neighbouring cell.


@case("a-nan-duration-is-not-a-measured-run", "ran", "FAIL")
def v1(root):
    # `NaN <= 0` is False and json.load accepts bare NaN, so this receipt read
    # as "each with a zero exit and a nonzero duration": a false evidence
    # sentence over a value that measures nothing.
    write(root, "ran-receipt.json",
          '{"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": NaN}]}')


@case("an-infinite-duration-is-not-a-measured-run", "ran", "FAIL")
def v2(root):
    write(root, "ran-receipt.json",
          '{"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": Infinity}]}')


@case("an-integer-pair-is-not-a-second-derivation", "numbers", "FAIL")
def v3(root):
    # The reduction was applied only to strings, so two integers skipped the
    # reduce-then-test fix entirely and bought "a second derivation whose text
    # differs beyond case, whitespace and comments" over fields holding no text.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-1", "query": 0, "second_derivation": 1,
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("prose-that-denies-an-owner-is-not-a-system-of-record", "datamodel", "FAIL")
def v4(root):
    # "we have not yet decided who the owner is" left the capture "is", and "the
    # owner is still unknown" left "is still unknown": remnants a one-shot
    # copula strip plus a whole-string token list could not refuse, so two
    # entities that deny their owner in plain English were reported as "2
    # entities, each with a system of record".
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Customer: we have not yet decided who the owner is\n"
          "- Refund: the owner is still unknown\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")


@case("a-real-owner-after-a-copula-still-passes", "datamodel", "PASS")
def v5(root):
    # The control: stripping copulas to a fixpoint must not eat a named system.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Customer: The system of record is the CRM.\n"
          "- Refund: system of record: the ledger.\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")


@case("an-empty-ownership-column-is-not-answered-by-another-column", "datamodel", "FAIL")
def v6(root):
    # The System-of-record column is empty in every row; the Notes column
    # mentions the word "owner". Empty cells were dropped before the join, so
    # the phrase test read the Notes cell and the table passed as "each with a
    # system of record" over a column that declares nothing.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "| Entity | System of record | Notes |\n|---|---|---|\n"
          "| Customer |  | still deciding who the owner should be |\n"
          "| Refund |  | still deciding who the owner should be |\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")


@case("see-above-is-not-a-system-of-record", "datamodel", "FAIL")
def v7(root):
    # The reduce-then-test bug in the data model: `-- see above` is not blank
    # and is not a single vacuity token, so it survived a test that read the
    # raw text, while the identical string was refused as a derivation.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Customer: owner: -- see above\n"
          "- Refund: system of record: the ledger.\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")


# ---------------------------------------------------------------------------
# HTML comments render as nothing and must count as nothing, everywhere; and a
# coherence link needs subject matter, not a connective.


@case("a-commented-out-entity-list-is-not-a-data-model", "datamodel", "FAIL")
def h1(root):
    # Four functions stripped comments and four did not, so this file was "the
    # absence of an artifact" to the artifacts check and "2 entities, each with
    # a system of record" to the data-model check, in the same run.
    write(root, "05-data-model.md",
          "# Data model\n\n## Entities\n\n"
          "<!-- the entity list moved to the wiki; keeping the old one here for reference\n"
          "- Customer: system of record: the CRM.\n"
          "- Refund: system of record: the ledger service.\n-->\n\n"
          "## Relationships\n- Customer to Refund: one-to-many, optional.\n")


@case("diagram-nodes-do-not-trace-to-commented-out-entities", "diagrams", "NO-DATA")
def h2(root):
    # The diagram check reported its nodes traceable to entities that do not
    # exist in the rendered document.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "<!--\n- Customer: system of record: the CRM.\n"
          "- Refund: system of record: the ledger service.\n-->\n"
          "## Relationships\n- Customer to Refund: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nflowchart LR\n  Customer --> Refund\n```\n")


@case("a-commented-out-alternative-is-not-an-alternative", "adr", "FAIL")
def h3(root):
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nlatency, auditability\n"
          "## Rejected alternatives\n"
          "<!--\n- Synchronous call: ties checkout to ledger availability.\n"
          "- Nightly batch: misses the deadline.\n-->\n"
          "## Decision\nPublish to a queue.\n## Consequences\nOne more moving part.\n"
          "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("a-connective-is-not-shared-subject-matter", "artifacts", "FAIL")
def h4(root):
    # The docstring's own fixture: bananas, lawnmowers, a tractor fleet and
    # Mars, tied into one dossier by the word "Therefore" in each artifact.
    write(root, "00-intake.json", {"tier": "T3", "answers": TIER_ANSWERS["T3"], "override": None})
    unrelated = dict(_UNRELATED)
    unrelated["01-purpose.md"] = ("# Purpose\nBananas ripen unevenly in the shed. Therefore we "
                                  "want a ripening schedule.\n")
    unrelated["02-process.md"] = ("# Process\nThe lawnmower is pushed across the paddock. "
                                  "Therefore the grass is shortened.\n")
    unrelated["04-technology-map.md"] = ("# Technology map\nOur tractor fleet runs on diesel. "
                                         "Therefore we keep a fuel bowser on site.\n")
    unrelated["07-verification.md"] = ("# Verification\nWe will verify the Martian regolith "
                                       "sample by spectroscopy. Therefore a lander is needed.\n")
    for name, body in unrelated.items():
        write(root, name, body)


@case("a-second-connective-is-not-shared-subject-matter-either", "artifacts", "FAIL")
def h5(root):
    # The class, not the instance: any word that connects sentences rather than
    # naming anything must not tie two artifacts together.
    write(root, "00-intake.json", {"tier": "T3", "answers": TIER_ANSWERS["T3"], "override": None})
    unrelated = dict(_UNRELATED)
    unrelated["01-purpose.md"] = "# Purpose\nBananas ripen unevenly. Additionally, sheds are dark.\n"
    unrelated["02-process.md"] = "# Process\nMowing happens in spring. Additionally, blades dull.\n"
    unrelated["04-technology-map.md"] = ("# Technology map\nThe tractor burns diesel. "
                                         "Additionally, it leaks.\n")
    unrelated["07-verification.md"] = ("# Verification\nMars is verified by telescope. "
                                       "Additionally, at night.\n")
    for name, body in unrelated.items():
        write(root, name, body)


# ---------------------------------------------------------------------------
# Hyphenated and dotted node names in every dialect. Seven identifier patterns
# excluded the dominant backend naming convention, which both hid undeclared
# services (a hyphenated orphan was dropped, then "all traceable" printed) and
# rejected honest work (a correct lifecycle failed with four orphans the author
# never typed). Both directions are pinned, per dialect.

_HYPHEN_MODEL = ("# Data model\n## Entities\n"
                 "- order-service: system of record: the OMS.\n"
                 "- payment-gateway: system of record: the ledger.\n"
                 "## Relationships\n- order-service to payment-gateway: one-to-many.\n")


@case("a-hyphenated-participant-is-read-and-traced", "diagrams", "PASS")
def g1(root):
    write(root, "05-data-model.md", _HYPHEN_MODEL)
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n  participant order-service\n"
          "  participant payment-gateway\n  order-service->>payment-gateway: charge\n```\n")


@case("a-hyphenated-undeclared-participant-is-an-orphan", "diagrams", "FAIL")
def g2(root):
    # The hole: `participant totally-undeclared-service` was read as no node at
    # all, so a box that exists nowhere passed the gate whose one job is to
    # catch a diagram drifting from the system it claims to show.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to Customer: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nsequenceDiagram\n  participant Customer\n"
          "  participant totally-undeclared-service\n"
          "  Customer->>totally-undeclared-service: nothing anywhere declares this\n```\n")


@case("a-hyphenated-lifecycle-is-not-shredded-into-fragments", "diagrams", "PASS")
def g3(root):
    # The false rejection: `in-transit --> out-for-delivery` used to match as
    # `transit --> out`, and the author was handed four orphans they never
    # typed, with no next step but renaming a domain concept.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Shipment: system of record: the WMS.\n"
          "## Lifecycle\n- created\n- in-transit\n- out-for-delivery\n- delivered\n"
          "## Relationships\n- Shipment to Shipment: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nstateDiagram-v2\n  [*] --> created\n"
          "  created --> in-transit\n  in-transit --> out-for-delivery\n"
          "  out-for-delivery --> delivered\n```\n")


@case("l4s-own-worked-example-draws-in-an-erdiagram", "diagrams", "PASS")
def g4(root):
    # L4 promises `payment-token` and `pii.profile` are read rather than
    # silently dropped; an erDiagram of those two names produced "no diagram
    # node could be read out of them".
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- payment-token: system of record: the vault.\n"
          "- pii.profile: system of record: the identity service.\n"
          "## Relationships\n- payment-token to pii.profile: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nerDiagram\n  payment-token ||--o| pii.profile : masks\n```\n")


@case("a-hyphenated-class-is-not-truncated-to-a-prefix", "diagrams", "FAIL")
def g5(root):
    # classDiagram manufactured a node called `feed` out of `class feed-poller`
    # and then traced or orphaned a name the author never wrote. The FAIL must
    # name the full identifier.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to Customer: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nclassDiagram\n  class feed-poller\n```\n")


@case("the-truncated-class-fail-names-the-full-identifier", "evidence", "full-name")
def g6(root):
    g5(root)
    line = gate_line(root, "diagrams")
    if line.split()[1:2] != ["FAIL"]:
        return line
    return "full-name" if "feed-poller" in line else line


@case("mindmap-children-are-read-not-dropped", "diagrams", "FAIL")
def g7(root):
    # Two undeclared children sat under "1 diagram node(s) in mindmap, all
    # traceable": a half-parse, which L5 says a dialect must not get.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "## Relationships\n- Customer to Customer: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nmindmap\n  root((Customer))\n"
          "    UndeclaredThingOne\n    UndeclaredThingTwo\n```\n")


@case("a-declared-mindmap-still-passes", "diagrams", "PASS")
def g8(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- Customer: system of record: the CRM.\n"
          "- Order: system of record: the OMS.\n"
          "## Relationships\n- Customer to Order: one-to-many.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nmindmap\n  root((Customer))\n    Order\n```\n")


# ---------------------------------------------------------------------------
# Shared rules adopted at every call site: the dedupe rule in the two gates that
# never had it, units on the drift comparison, the reviewability floor on lint
# exemptions, one marker never waiving two swallows, and ages nobody computed.


@case("one-check-written-five-times-is-one-check", "evidence", "counted-once")
def u1(root):
    chk = {"name": "reconcile", "exit_code": 0, "duration_ms": 812}
    write(root, "ran-receipt.json", {"checks": [dict(chk) for _ in range(5)]})
    line = gate_line(root, "ran")
    if line.split()[1:2] != ["PASS"]:
        return line
    return ("counted-once" if "1 recorded check(s)" in line and "4 identical" in line else line)


@case("one-receipt-copied-into-two-directories-is-one-rehearsal", "evidence", "counted-once")
def u2(root):
    receipt = {"forward": {"ran_against_restore": True},
               "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
               "row_counts": {"before": 100, "after_reverse": 100}}
    write(root, "a/migration-receipt.json", receipt)
    write(root, "b/migration-receipt.json", receipt)
    line = gate_line(root, "migration")
    if line.split()[1:2] != ["PASS"]:
        return line
    return ("counted-once" if "1 receipt(s)" in line and "1 identical receipt(s)" in line else line)


@case("a-rate-and-a-currency-amount-are-not-zero-drift", "numbers", "FAIL")
def u3(root):
    # numeric() strips "%" and "$" as formatting, so "50%" and "$50" compared
    # equal and bought the strongest sentence this gate prints.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "refund rate", "snapshot_id": "snap-1",
        "query": "SELECT refunds FROM daily", "second_derivation": "SELECT paid FROM ledger",
        "rerun": {"ran": True, "primary": "50%", "secondary": "$50"}}]})


@case("a-percentage-is-not-a-row-count", "migration", "FAIL")
def u4(root):
    write(root, "migration-receipt.json", {
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": "50%", "after_reverse": "50%"}})


@case("a-zero-width-space-is-not-a-second-derivation", "numbers", "FAIL")
def u5(root):
    # str.split() does not split on U+200B, so one invisible character made a
    # copy-paste of the first query an "independent second derivation".
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-1",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(amount)\u200b FROM orders",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("a-boolean-snapshot-id-pins-nothing", "numbers", "FAIL")
def u6(root):
    # answered() keeps False as an answer on purpose (a zero row count is an
    # answer), so `"snapshot_id": false` read as a pinned read.
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": False,
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})


@case("snapshot-reuse-is-disclosed-across-case", "evidence", "disclosed")
def u7(root):
    # The reuse disclosure compared raw ids while every other comparison folds,
    # so ids differing only in case lost the disclosure and the PASS read as two
    # independent pins.
    write(root, "numbers-manifest.json", {"figures": [
        {"label": "gmv", "snapshot_id": "SNAP-1", "query": "SELECT SUM(amount) FROM orders",
         "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
         "rerun": {"ran": True, "primary": 17570, "secondary": 17570}},
        {"label": "aov", "snapshot_id": "snap-1", "query": "SELECT AVG(amount) FROM orders",
         "second_derivation": "SELECT SUM(amount)/COUNT(*) FROM orders",
         "rerun": {"ran": True, "primary": 88, "secondary": 88}}]})
    line = gate_line(root, "numbers")
    if line.split()[1:2] != ["PASS"]:
        return line
    return "disclosed" if "share a snapshot id" in line else line


@case("a-directory-named-after-a-gate-is-refused-not-eaten", "guard", "refused")
def u8(root):
    os.makedirs(os.path.join(root, "numbers"))
    out = subprocess.run([sys.executable, GATE, "numbers"], capture_output=True, text=True,
                         cwd=root)
    return ("refused" if out.returncode == 1 and "both a gate name and a directory" in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:80]))


@case("two-gate-roots-are-refused-not-last-wins", "guard", "refused")
def u9(root):
    a, b = os.path.join(root, "a"), os.path.join(root, "b")
    os.makedirs(a), os.makedirs(b)
    out = subprocess.run([sys.executable, GATE, a, b], capture_output=True, text=True)
    return ("refused" if out.returncode == 1 and "one directory at a time" in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:80]))


@case("two-design-roots-are-refused-not-last-wins", "guard", "refused")
def u10(root):
    a, b = os.path.join(root, "a"), os.path.join(root, "b")
    os.makedirs(a), os.makedirs(b)
    out = subprocess.run([sys.executable, DESIGN, a, b], capture_output=True, text=True)
    return ("refused" if out.returncode == 1 and "one directory at a time" in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:80]))


@case("two-lint-roots-are-refused-not-last-wins", "lints", "FAIL")
def u11(root):
    a, b = os.path.join(root, "a"), os.path.join(root, "b")
    os.makedirs(a), os.makedirs(b)
    write(root, "a/evil.py", "try:\n    f()\nexcept:\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    write(root, "b/ok.py", "def f():\n    return 1\n")
    return run_score_lints([a, b])


@case("an-exemption-reason-of-x-waives-nothing", "lints", "FAIL")
def u12(root):
    write(root, "bad.py", "try:\n    f()\nexcept:  # sbe" + ": allow-silent x\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    return run_score_lints([root])


@case("the-marker-pasted-as-its-own-reason-waives-nothing", "lints", "FAIL")
def u13(root):
    write(root, "bad.py", "try:\n    f()\nexcept:  # sbe" + ": allow-silent sbe" + ": allow-silent\n    pass\n")  # sbe: allow-silent this is the lint FIXTURE: the swallow is the defect under test, written into a temp file, never executed here
    return run_score_lints([root])


@case("one-marker-does-not-waive-a-second-unrelated-hit", "lints", "FAIL")
def u14(root):
    # The upsert pattern crosses newlines, so its span enclosed the except
    # block, and a reason written about the except waived the upsert too.
    write(root, "t.py",  # sbe: allow-silent this is the lint FIXTURE: both swallows are the defects under test, written into a temp file, never executed here
          'UPSERT = """\nINSERT INTO ledger (id, amount) VALUES (%s, %s)\nON CONFLICT (id)\n"""\n'
          'try:\n    load()\nexcept:  # sbe: allow-silent retry loop above already logs the failure\n'
          '    pass\nMORE = """\nDO NOTHING\n"""\n')
    return run_score_lints([root])


@case("unreadable-timestamps-never-fall-out-of-the-window", "score", "FAIL")
def u15(root):
    # ctx.recent mapped an unreadable timestamp to the sentinel 99, meaning
    # "old", so rows nothing had measured silently left the 7-day window and
    # "1/1 sessions >= 90%; below floor: none" printed over 99 unmeasured rows.
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [json.dumps({"schema": 2, "session_id": "good", "ts": now,
                        "cache_read": 95, "cache_write": 5})]
    rows += [json.dumps({"schema": 2, "session_id": "s%d" % i, "ts": "not-a-timestamp",
                         "cache_read": 1, "cache_write": 99}) for i in range(3)]
    return score_check("cache-economy", _ledger(root, "\n".join(rows) + "\n"))


@case("sessions-without-cache-fields-are-disclosed", "evidence", "disclosed")
def u16(root):
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [json.dumps({"schema": 2, "session_id": "good", "ts": now,
                        "cache_read": 95, "cache_write": 5}),
            json.dumps({"schema": 2, "session_id": "bare", "ts": now})]
    _ledger(root, "\n".join(rows) + "\n")
    e = dict(os.environ)
    e.update({"BROTHERSBE_VAULT": root, "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": ""})
    out = subprocess.run([sys.executable, SCORE], capture_output=True, text=True, env=e)
    line = next((l for l in out.stdout.splitlines() if l.startswith("cache-economy")), "")
    return "disclosed" if "carry no cache fields" in line else line or "no-line"


@case("five-tbd-seals-are-zero-sealed-predictions", "score", "NO-DATA")
def u17(root):
    write(root, "50-Reference/operator-model.md",
          "# Operator model\n## Prediction ledger\n"
          "date | prediction | seal | scored on | hit\n"
          + "".join("2026-07-0%d | the queue depth stays under 1k | TBD | review | yes\n" % i
                    for i in range(1, 6)))
    return score_check("prediction-seals", root)


@case("one-prediction-on-five-dates-is-one-prediction", "score", "FAIL")
def u18(root):
    # The dedupe keyed on the whole row including the date, so it could never
    # fold the rows its own docstring says it exists to fold.
    write(root, "50-Reference/operator-model.md",
          "# Operator model\n## Prediction ledger\n"
          "date | prediction | seal | scored on | hit\n"
          + "".join("2026-07-0%d | the queue depth stays under 1k | seal-%d | review | yes\n"
                    % (i, i) for i in range(1, 6)))
    return score_check("prediction-seals", root)


@case("six-timestamps-of-one-rating-are-one-rating", "score", "FAIL")
def u19(root):
    # The writer stamps a fresh timestamp on every row, so the whole-row dedupe
    # was structurally unreachable: six copies of one 5/5 rating cleared the
    # "6 distinct ratings" threshold.
    rows = [json.dumps({"ts": "2026-07-25T10:00:0%d.%06dZ" % (i, i), "score": 5,
                        "task": "ship the refund gate", "note": "solid"}) for i in range(6)]
    write(root, "99-System/telemetry/ratings.jsonl", "\n".join(rows) + "\n")
    return score_check("felt-outcome-ratings", root)


@case("a-trailing-period-is-not-a-second-alternative", "adr", "FAIL")
def u20(root):
    # distinct() used the weaker of the two reductions, so one alternative
    # written twice, differing by case and one period, counted as "2 distinct
    # rejected alternatives".
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nlatency, freshness\n## Rejected alternatives\n"
          "- Nightly batch reconciliation, it misses the deadline\n"
          "- nightly batch reconciliation, it misses the deadline.\n"
          "## Decision\nPublish to a queue.\n## Consequences\nOne more moving part.\n"
          "## What would flip this\nSub-second freshness becomes a requirement.\n")


# ---------------------------------------------------------------------------
# The decision tool: ghost votes, silent ties, swallowed arguments.

DECIDE = os.path.join(HERE, "..", "tools", "sbe_decide.py")


@case("a-vote-for-a-ghost-option-is-reported-not-counted", "decide", "reported")
def q1(root):
    table = {"options": ["alpha", "beta"], "flip": "flip line",
             "criteria": [{"name": "c1", "kind": "choice", "note": "", "scores": {"yes": ["gamma"]}},
                          {"name": "c2", "kind": "choice", "note": "", "scores": {"yes": ["alpha"]}}]}
    r = _decide.recommend(table, {"c1": "yes", "c2": "yes"})
    ghost_named = any("gamma" in u and "not among this table's options" in u
                      for u in r["unrecognized"])
    return ("reported" if ghost_named and r["evidence"] == 1 else
            "evidence=%d unrecognized=%r" % (r["evidence"], r["unrecognized"]))


@case("an-exact-tie-is-disclosed-not-decided-in-silence", "decide", "disclosed")
def q2(root):
    table = {"options": ["alpha", "beta"], "flip": "flip line",
             "criteria": [{"name": "c1", "kind": "choice", "note": "",
                           "scores": {"yes": ["alpha", "beta"]}}]}
    r = _decide.recommend(table, {"c1": "yes"})
    return "disclosed" if r["tie"] == ["alpha", "beta"] else "tie=%r" % r["tie"]


@case("key-value-answers-are-read-not-swallowed", "guard", "answered")
def q3(root):
    # Every argument after the table name used to be discarded without a word,
    # and the tool then asked its questions interactively: key=value is the
    # first thing an engineer or an agent tries.
    out = subprocess.run([sys.executable, DECIDE, "shape", "deploying_teams=1",
                          "consistency=strong", "ops_maturity=low", "failure_isolation=low"],
                         input="", capture_output=True, text=True, timeout=20)
    return ("answered" if out.returncode == 0 and "Recommendation: modular monolith" in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:80]))


@case("an-argument-the-table-cannot-read-is-refused-by-name", "guard", "refused")
def q4(root):
    out = subprocess.run([sys.executable, DECIDE, "shape", "THIS_IS_GARBAGE=999"],
                         input="", capture_output=True, text=True, timeout=20)
    return ("refused" if out.returncode == 1 and "THIS_IS_GARBAGE" in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:80]))


@case("a-closed-stdin-is-a-named-refusal-not-a-repr", "guard", "named")
def q5(root):
    out = subprocess.run([sys.executable, DECIDE, "shape"], input="",
                         capture_output=True, text=True, timeout=20)
    return ("named" if out.returncode == 1 and "stdin closed before" in out.stdout
            and "EOFError" not in out.stdout
            else "exit %d: %s" % (out.returncode, out.stdout.strip()[:120]))


# ---------------------------------------------------------------------------
# Honest forms a competent engineer writes, each previously refused.


@case("a-bold-entity-name-is-an-entity-name", "datamodel", "PASS")
def f1(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- **Order**: a customer order. system of record: oms\n"
          "- **OrderLine**: a line on an order. system of record: oms\n"
          "## Relationships\n- Order one-to-many OrderLine\n")


@case("a-bold-entity-in-a-table-is-an-entity", "datamodel", "PASS")
def f2(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "| Entity | Meaning | System of record |\n| --- | --- | --- |\n"
          "| **Order** | a customer order | oms |\n"
          "| **OrderLine** | a line on an order | oms |\n"
          "## Relationships\n- Order one-to-many OrderLine\n")


@case("an-entity-bullet-without-a-colon-still-names-its-owner", "datamodel", "PASS")
def f3(root):
    # The FAIL used to quote a line containing "system of record" in the same
    # sentence it claimed named none, because with no colon the whole sentence
    # became the entity NAME.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Order. The OMS is the system of record.\n"
          "- OrderLine. The OMS is the system of record.\n"
          "## Relationships\n- Order one-to-many OrderLine\n")


@case("adr-alternatives-as-a-comparison-table-count", "adr", "PASS")
def f4(root):
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nlatency, auditability\n"
          "## Alternatives considered\n"
          "| Option | Verdict | Why |\n| --- | --- | --- |\n"
          "| Synchronous ledger call | rejected | ties checkout to ledger uptime |\n"
          "| Nightly batch | rejected | misses the one business day deadline |\n"
          "| Event queue | chosen | decouples checkout from settlement |\n"
          "## Decision\nPublish refund events to a queue.\n"
          "## Consequences\nOne more moving part.\n"
          "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("an-in-bullet-chosen-marker-cannot-inflate-the-rejected-count", "adr", "FAIL")
def f4b(root):
    # A bullet that marks ITSELF chosen ("... Chosen.") beside one real
    # rejection, with a Decision that paraphrases instead of quoting. The
    # chosen-marker rule existed for table rows only, so this bullet counted
    # as a second rejection and the floor printed "2 distinct rejected
    # alternatives" over an artifact rejecting exactly one. The marker is now
    # read in every authoring form: the count is 1 and the floor fails.
    write(root, "03-adr.md",
          "# Retire the legacy email queue\n"
          "## Criteria\nCost of the bridge, risk to in-flight jobs.\n"
          "## Options considered\n"
          "- Big-bang cutover: rejected, in-flight jobs would be lost at the switch.\n"
          "- Drain and shut down with a bridge worker. Chosen.\n"
          "## Decision\nWe drain and shut down. The bridge worker runs until the old "
          "queue is empty.\n"
          "## Consequences\nTwo systems for one sprint.\n"
          "## What would flip this\nA drain longer than a sprint.\n")


@case("unmarked-options-with-an-unnamed-winner-are-nodata-not-a-count", "adr", "NO-DATA")
def f4c(root):
    # No option carries a verdict of its own and the Decision paraphrases,
    # so the winner may be ANY of the listed options: free-form markdown
    # does not let this check tell a chosen option from a rejected one here,
    # and a count it cannot defend is not asserted. NO-DATA naming the
    # ambiguity, with the completing edit taught in the sentence.
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nCost, risk, speed.\n"
          "## Options considered\n"
          "- Big-bang cutover, which loses in-flight jobs at the switch.\n"
          "- Drain and shut down with a bridge worker running until empty.\n"
          "## Decision\nWe drain and shut down. The bridge worker runs until the old "
          "queue is empty.\n"
          "## Consequences\nTwo systems for one sprint.\n"
          "## What would flip this\nA drain longer than a sprint.\n")


@case("an-unquoted-decision-sentence-naming-one-listed-option-is-the-winner", "adr", "PASS")
def f4c2(root):
    # The honest hurried shape: no verdict markers anywhere, and a Decision
    # written as a natural sentence that embeds exactly one listed option's
    # name letter for letter. The old rule saw only quoted choices and
    # markers, and its NO-DATA sentence claimed "the Decision section does
    # not name which listed option won" over a file a reader refutes by
    # opening it. One listed name in the Decision resolves the winner; the
    # other options are what the heading declares them.
    write(root, "03-adr.md",
          "# ADR: rate limiting the export endpoint\n"
          "## Criteria\nDatastore out of the hot path; limits change without a deploy.\n"
          "## Options considered\n"
          "- Token bucket middleware in reporting-api itself.\n"
          "- Token bucket at the ingress (Envoy local rate limit).\n"
          "- Queue the export and drip-feed it.\n"
          "## Decision\nWe take the token bucket at the ingress (Envoy local rate limit): "
          "it keeps the datastore out of the hot path and the limits are a config reload, "
          "not a deploy.\n"
          "## Consequences\nEnvoy config becomes load-bearing.\n"
          "## Flip condition\nGlobal limits across pods.\n")


@case("a-chosen-marker-resolving-to-no-listed-option-establishes-nothing", "adr", "NO-DATA")
def f4c3(root):
    # 12a-I4's shape: the winner is called "Kafka" in the comparison table
    # and "Message broker" in the options list, which is ordinary writing,
    # not an attack. The chosen table row identifies a winner that may BE
    # the unmarked list option under another name, so it must not reclassify
    # that option as rejected: the old document-level boolean counted the
    # decision itself as one of its own rejected alternatives and printed
    # "2 distinct rejected alternatives" over an ADR with one rejection.
    write(root, "03-adr.md",
          "# ADR: order event distribution\n"
          "## Criteria\nDecouple producers from consumers; replay must be possible.\n"
          "## Options considered\n"
          "- Synchronous call into each consumer service: rejected, couples deploys and "
          "drops events on consumer downtime.\n"
          "- Message broker between producers and consumers.\n"
          "## Alternatives compared\n"
          "| Option | Throughput | Verdict |\n"
          "| --- | --- | --- |\n"
          "| Kafka | high | chosen |\n"
          "| Synchronous call | low | rejected |\n"
          "## Decision\nWe adopt Kafka for distribution.\n"
          "## Consequences\nWe now operate a broker.\n"
          "## What would flip this\nA managed queue with replay at equal cost.\n")


@case("the-projects-own-name-for-the-flip-section-is-accepted", "adr", "PASS")
def f4c4(root):
    # "Flip condition" is the two-word name this project's own docs print in
    # four places for exactly this section, and the vocabulary refused it: a
    # reader who wrote the heading the README taught them was FAILed on
    # spelling. Pinned with a marker-free body that also exercises the
    # decision-sentence winner rule above.
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nCost, risk.\n"
          "## Options considered\n"
          "- Nightly batch reconciliation of the ledger.\n"
          "- Streaming reconciliation on every write.\n"
          "- Manual weekly spot checks by the finance team.\n"
          "## Decision\nWe run the nightly batch reconciliation of the ledger; the "
          "streaming option costs a consumer we do not want to operate, and manual "
          "checks do not scale.\n"
          "## Consequences\nDrift is visible for up to a day.\n"
          "## Flip condition\nAn SLA tighter than a day on ledger drift.\n")


@case("a-colon-led-hurried-adr-is-read-as-its-sections", "adr", "PASS")
def f4d(root):
    # The whole ADR written the way a hurried honest engineer writes it:
    # every section a colon-terminated lead, no markdown heading anywhere.
    # Every semantic element the law asks for is present, and the section
    # readers used to see no section at all and fail this at gate severity
    # with "no Criteria section" over a line beginning with exactly that
    # word.
    write(root, "03-adr.md",
          "# Move GET /orders/:id to integer cents\n\n"
          "Criteria: no consumer breakage, exact arithmetic, one release.\n\n"
          "Options considered:\n"
          "- Keep floats and round at render. Rejected: rounding drift already produced "
          "a support ticket.\n"
          "- Version the endpoint as /v2/orders. Rejected: two contracts to maintain "
          "for one field.\n"
          "- Additive change: integer cents plus ISO currency code. Chosen.\n\n"
          "Decision: additive change, integer cents, ISO currency code, old field "
          "deprecated one release later.\n\n"
          "Consequences: consumers migrate at their own pace; the payload carries both "
          "fields for a release.\n\n"
          "What would flip this: a consumer that cannot parse the new fields before "
          "the deprecation date.\n")


@case("a-bold-section-lead-is-a-section", "adr", "PASS")
def f4e(root):
    # The bold form of the same class: `**Criteria**` declares the section
    # as surely as `## Criteria`.
    write(root, "03-adr.md",
          "# ADR\n\n**Criteria**\nlatency, auditability\n\n"
          "**Rejected alternatives**\n"
          "- Synchronous ledger call: ties checkout to ledger uptime.\n"
          "- Nightly batch: misses the deadline.\n\n"
          "**Decision**\nPublish to a queue.\n\n**Consequences**\nOne more moving part.\n\n"
          "**What would flip this**\nSub-second settlement becoming a requirement.\n")


@case("a-system-of-record-named-in-the-data-model-traces-a-diagram-node", "diagrams", "PASS")
def f4g(root):
    # The data model declares orders_db twice over as each entity's system of
    # record, and the diagrams check used to print "appear nowhere else in
    # the dossier" about exactly that node: a lead clause asserting a
    # whole-dossier absence over a search that read entity bullets and
    # component declarations only. The owning system an entity names is a
    # declaration a reader can find, so the node traces, and the FAIL lead
    # clause now names what was examined instead of the whole dossier.
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Order: system of record is the orders Postgres database (orders_db).\n"
          "- OrderLine: system of record is the orders Postgres database (orders_db).\n"
          "## Relationships\n"
          "- An Order has many OrderLines, and every OrderLine belongs to exactly "
          "one Order.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n## Read path\n"
          "```mermaid\nsequenceDiagram\n"
          "  Order->>orders_db: SELECT\n"
          "  orders_db-->>Order: row\n```\n")


@case("a-colon-led-entities-section-is-the-entity-set", "datamodel", "PASS")
def f4f(root):
    # `Entities:` and `Relationships:` as whole-line leads declare their
    # sections; the check used to read them as no heading at all and refuse
    # to assert an entity count over an honest model.
    write(root, "05-data-model.md",
          "# Data model\n\n"
          "Entities:\n"
          "- Order: system of record is the orders Postgres database (orders_db).\n"
          "- OrderLine: system of record is the orders Postgres database (orders_db).\n\n"
          "Relationships:\n"
          "- An Order has many OrderLines, and every OrderLine belongs to exactly "
          "one Order.\n")


@case("a-trade-offs-considered-heading-is-an-alternatives-heading", "adr", "PASS")
def f5(root):
    write(root, "03-adr.md",
          "# ADR\n## Criteria\nlatency, auditability\n"
          "## Trade-offs considered\n"
          "- Synchronous ledger call: rejected, ties checkout to ledger uptime.\n"
          "- Nightly batch: rejected, misses the deadline.\n"
          "## Decision\nPublish to a queue.\n## Consequences\nOne more moving part.\n"
          "## What would flip this\nSub-second settlement becoming a requirement.\n")


@case("cardinality-written-in-prose-is-cardinality", "datamodel", "PASS")
def f6(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Order: system of record: oms\n- OrderLine: system of record: oms\n"
          "## Relationships\n"
          "- An Order has many OrderLines, and every OrderLine belongs to exactly one Order.\n")


@case("relationships-as-an-erdiagram-are-relationships", "datamodel", "PASS")
def f7(root):
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n"
          "- Order: system of record: oms\n- OrderLine: system of record: oms\n"
          "## Relationships\n```mermaid\nerDiagram\n  Order ||--o{ OrderLine : contains\n```\n")


@case("a-technology-map-written-as-bullets-declares-components", "diagrams", "PASS")
def f8(root):
    # L5's INPUTS promises component bullets "in either file"; only
    # 06-diagrams.md was read, so an honest bulleted technology map produced
    # orphans and the law overclaimed against its own tool.
    write(root, "04-technology-map.md",
          "# Technology map\n## Components\n"
          "- feedqueue: SQS, owned by platform, fails on poison messages, recovers via DLQ\n"
          "- ingestworker: Python on ECS, owned by supply, fails on parse error, recovers by replay\n")
    write(root, "05-data-model.md",
          "# Data model\n## Entities\n- FeedFile: system of record: the landing bucket.\n"
          "## Relationships\n- FeedFile to FeedFile: one-to-one.\n")
    write(root, "06-diagrams.md",
          "# Diagrams\n```mermaid\nflowchart LR\n  feedqueue --> ingestworker\n```\n")


@case("an-asterisk-bullet-fence-line-is-a-fence-line", "score", "FAIL")
def f9(root):
    # Markdown treats `*` and `-` as the same bullet; the fence checks read only
    # `- `, so an asterisk-bulleted registry got permanently green fence
    # discipline over untagged live fences.
    write(root, "reg/STATE.md",
          "# State\n## Fences\n* agent: builder | objective: ship the refund gate | open\n")
    return score_check("budget-vs-tier", root,
                       env={"BROTHERSBE_REGISTRIES": os.path.join(root, "reg", "*.md")})


@case("a-self-declared-component-trace-is-disclosed", "evidence", "disclosed")
def f10(root):
    # One file declaring its own nodes as Components bullets is declaration and
    # use in one place; still accepted, now said out loud instead of counted
    # silently inside "all traceable".
    write(root, "06-diagrams.md",
          "# Diagrams\n## Components\n- Alpha\n- Beta\n## Context\n"
          "```mermaid\nflowchart LR\n  Alpha --> Beta\n```\n")
    line = gate_line(root, "diagrams")
    if line.split()[1:2] != ["PASS"]:
        return line
    return "disclosed" if "declared in this artifact itself" in line else line


# The scope-naming class: a positive verdict that does not name what it examined
# lets a target that contributed nothing be absorbed into that verdict. Closed in
# sbe_checks.scope_note, which sbe_gate.find and sbe_design.find_dossiers both
# call, because closing it at the two sites a reviewer probed is what this
# project has already done once and had come back one level deeper.
_POOLED = [("numbers", "numbers-manifest.json",
            {"figures": [{"label": "gmv", "snapshot_id": "snap-2026-07",
                          "query": "SELECT SUM(amount) FROM orders",
                          "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
                          "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]}),
           ("migration", "migration-receipt.json",
            {"forward": {"ran_against_restore": True},
             "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
             "row_counts": {"before": 100, "after_reverse": 100}}),
           ("ran", "ran-receipt.json",
            {"checks": [{"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})]


@case("a-change-directory-with-no-receipt-is-named-in-the-verdict-that-pools-it",
      "gaterun", "disclosed")
def sc1(root):
    """A parent holding three change directories, the middle one carrying no
    receipt at all, printed one PASS over the pool and named the silent
    directory nowhere; alone, that same directory printed NO-DATA. Reproduced on
    `ran` and asserted here on all three receipt gates, because "the source
    shape is the same" is a claim about code nobody ran: the two siblings were
    never executed against this shape until this case existed."""
    bad = []
    for gate, fname, receipt in _POOLED:
        d = os.path.join(root, gate)
        write(d, os.path.join("a", fname), receipt)
        write(d, os.path.join("c", fname), receipt)
        os.makedirs(os.path.join(d, "b"), exist_ok=True)
        # Separator-normalized before the name checks: the gate names paths in
        # the platform's own rendering (a\file on Windows, run 31040612827);
        # the NAMING is what this case asserts, not the separator glyph.
        line = gate_line(".", gate, cwd=d).replace("\\", "/")
        if line.split()[1:2] != ["PASS"]:
            bad.append("%s did not reach PASS over the pool: %s" % (gate, line[:90]))
            continue
        for want in ("read 2 %s under ." % fname,          # the targets it read
                     "a/%s" % fname, "c/%s" % fname,       # named, not counted
                     "1 of 3 director(y/ies)",             # the silent one, counted
                     "contributed no %s (b)" % fname):     # and named
            if want not in line:
                bad.append("%s PASS does not say %r: %s" % (gate, want, line[:120]))
    return "disclosed" if not bad else "; ".join(bad[:3])


@case("the-approval-verdict-names-which-approval-file-it-read", "gaterun", "named")
def sc2(root):
    """The refusal quoted one APPROVAL file's own words and identified neither
    the file nor that a second one existed, so a reader could not tell which
    claim was refused. Same class as the pooled PASS: a sentence about a target
    that does not name the target."""
    write(root, "a/APPROVAL", "touches the partner payout path\n")
    write(root, "c/APPROVAL", "touches the settlement path\n")
    os.makedirs(os.path.join(root, "b"), exist_ok=True)
    # Separator-normalized for the same reason as the pooled case above.
    line = gate_line(".", "approval", cwd=root).replace("\\", "/")
    for want in ("a/APPROVAL", "of 2 APPROVAL file(s) read"):
        if want not in line:
            return "the refusal does not say %r: %s" % (want, line[:140])
    return "named"


@case("an-empty-directory-cannot-print-the-report-of-a-dossier-somewhere-else",
      "designrun", "disclosed")
def sc3(root):
    """Pointed at an EMPTY directory with SBE_DOSSIER_ROOT set elsewhere, this
    tool printed five PASS lines byte for byte identical to the run against the
    complete dossier: the argument was discarded in silence and no printed
    sentence named the root actually read. Two runs that examined different
    trees must not be able to print the same report."""
    write(root, "real/00-intake.json", {"tier": "T2", "answers": T2_ANSWERS, "override": None})
    write(root, "real/01-purpose.md", PURPOSE)
    empty = os.path.join(root, "empty")
    os.makedirs(empty, exist_ok=True)
    real = os.path.join(root, "real")
    env = {"SBE_DOSSIER_ROOT": real}
    a = subprocess.run([sys.executable, DESIGN, real], capture_output=True, text=True,
                       env=dict(os.environ, **env))
    b = subprocess.run([sys.executable, DESIGN, empty], capture_output=True, text=True,
                       env=dict(os.environ, **env))
    if a.stdout == b.stdout:
        return "the empty directory printed the real dossier's report byte for byte"
    if real not in a.stdout:
        return "the run against the dossier never names the root it read"
    if "SBE_DOSSIER_ROOT=%s replaced it" % real not in b.stdout:
        return "the substituted root is not disclosed: %s" % b.stdout[:160]
    return "disclosed"


# ---------------------------------------------------------------------------
# sbe_gate.py's own `.sbe-exempt`: the same waiver channel sbe_design.py's
# exemption already gives a dossier, now open to the four hard gates. The
# workflow comment (.github/workflows/brothersbe-gates.yml) promised this for
# a directory holding gate-artifact-shaped files that are not live work, and
# grep confirmed sbe_gate.py had no exemption support at all until now.
# ---------------------------------------------------------------------------

@case("an-exempted-approval-reads-waived-with-the-reason-and-strict-exits-clear",
      "gaterun", "waived-clear")
def gx1(root):
    """`.sbe-exempt` gives sbe_gate.py the waiver channel sbe_design.py's own
    exemption already has: a directory holding a gate artifact that is not
    live work names the gate it waives and why, and the report prints WAIVED
    with that reason on every run instead of PASS, FAIL or NO-DATA, never in
    silence. --strict alone does not block on a waiver: a waiver is a visible
    decision, not a violation (gx2 shows what does block one)."""
    write(root, "APPROVAL", "touches the partner payout path\n")
    write(root, ".sbe-exempt",
          "gates: approval\n"
          "reason: this directory holds a finished engagement's APPROVAL file, kept for "
          "history and not live design work\n")
    out = subprocess.run([sys.executable, GATE, "--strict", "approval", root],
                         capture_output=True, text=True)
    ok = (out.returncode == 0
          and re.search(r"(?m)^  >> approval\s+WAIVED\s", out.stdout)
          and "kept for history and not live design work" in out.stdout)
    return "waived-clear" if ok else "exit %d: %s" % (out.returncode, out.stdout.strip()[:200])


@case("strict-waivers-blocks-an-exempted-approval-that-strict-alone-does-not",
      "gaterun", "blocked")
def gx2(root):
    """--strict-waivers matches sbe_design.py's own flag of the same name and
    the same wording ("run --strict --strict-waivers to make one block a
    merge"): it turns every WAIVED artifact into a failure for a team that
    wants an exemption to expire on contact. gx1's identical fixture is exit 0
    under --strict alone; this is --strict-waivers' own effect, not
    --strict's."""
    write(root, "APPROVAL", "touches the partner payout path\n")
    write(root, ".sbe-exempt",
          "gates: approval\n"
          "reason: this directory holds a finished engagement's APPROVAL file, kept for "
          "history and not live design work\n")
    out = subprocess.run([sys.executable, GATE, "--strict", "--strict-waivers", "approval", root],
                         capture_output=True, text=True)
    return "blocked" if out.returncode else "clear"


@case("a-blank-sbe-exempt-reason-fails-by-name-and-the-artifact-is-still-checked",
      "gaterun", "named-and-checked")
def gx3(root):
    """A `.sbe-exempt` that names a gate but records no reason, or a
    whitespace-only one, waives nothing: `touch .sbe-exempt` was exactly this
    shape for sbe_design.py's own exemption before that was fixed, and a
    waiver earned by a blank file is an off switch wearing an exemption's
    clothes. The FAIL names the file, and the APPROVAL artifact underneath it
    is still checked, never silently skipped because its exemption is
    broken."""
    write(root, "APPROVAL", "touches the partner payout path\n")
    write(root, ".sbe-exempt", "gates: approval\nreason:   \n")
    out = subprocess.run([sys.executable, GATE, "approval", root], capture_output=True, text=True)
    named = re.search(r"(?m)^  exempt\s+FAIL\s+.*\.sbe-exempt.*no reason is recorded", out.stdout)
    checked = re.search(r"(?m)^  approval\s+(PASS|FAIL|NO-DATA)\s", out.stdout)
    return "named-and-checked" if named and checked else out.stdout.strip()[:200]


@case("a-pass-is-impossible-for-an-exempted-artifact", "gaterun", "waived-not-passed")
def gx4(root):
    """The strongest test of "never PASS": a numbers-manifest that would
    genuinely reach PASS on its own (unlike approval, which no fixture can
    reach PASS on at all) is placed under a `.sbe-exempt` naming `numbers`.
    The verdict line still reads WAIVED, quoting the reason, and no PASS
    verdict for `numbers` appears anywhere in the report."""
    write(root, "numbers-manifest.json", {"figures": [{
        "label": "gmv", "snapshot_id": "snap-2026-07",
        "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
    write(root, ".sbe-exempt",
          "gates: numbers\n"
          "reason: this manifest is a worked historical example kept for reference, not a "
          "live decision figure\n")
    out = subprocess.run([sys.executable, GATE, "numbers", root], capture_output=True, text=True)
    if re.search(r"(?m)^  numbers\s+PASS\s", out.stdout):
        return "PASS printed for an exempted artifact: %s" % out.stdout.strip()[:200]
    waived = re.search(r"(?m)^  >> numbers\s+WAIVED\s.*worked historical example", out.stdout)
    return "waived-not-passed" if waived else out.stdout.strip()[:200]


def _approval_verdict_and_reason(root, sig, approver, authors, committers=None, signer=""):
    """gate_approval's full (verdict, reason), joined as one string on a NUL
    rather than returned as a 2-tuple: a two-value return reads as a
    possible (verdict, evidence) pair to the honesty meta-test, which
    refuses any such function sitting outside the check registry it
    discovers by that shape. Same monkeypatch shape as
    `_approval_with_sig`, kept separate because that helper already
    discards the reason at its own call site and existing cases depend on
    that discarding; this one exists for the cases below that must read the
    SENTENCE, not just the verdict.
    """
    committers = list(authors) if committers is None else list(committers)
    who = list(authors) + [c for c in committers if c not in authors]
    meta = {"author": list(authors), "committer": committers, "signer": signer}
    original = _gate.git_trailers
    _gate.git_trailers = lambda r: ("change\n\nApproved-by: %s" % approver, sig, who, meta)
    try:
        verdict, why = _gate.gate_approval(root)
    finally:
        _gate.git_trailers = original
    return verdict + "\x00" + why


@case("a-gmail-dot-alias-is-not-proven-different", "gaterun", "fail-names-mailbox-alias")
def r7a(root):
    """THE DEFECT: `_canonical_email` folded case and a `+tag` but never a
    gmail dot, so `first.last@gmail.com` (Approved-by) against
    `firstlast@gmail.com` (author), ONE real gmail mailbox, fell through
    the self-approval check unmatched and reached the certifying PASS as
    "proven different" for two addresses that deliver to the same inbox:
    the approval gate's strongest sentence, forged by a self-approver typing
    their own address with a dot in it. The fix folds the local part's dots
    for gmail.com and googlemail.com ONLY (`_canonical_email`), so this pair
    now matches at the self-approval check and FAILs, and the sentence
    names the two recorded addresses and says they reach one mailbox under
    gmail's own aliasing, not two identities."""
    verdict, _, why = _approval_verdict_and_reason(
        root, "G", "first.last@gmail.com",
        ("First Last", "firstlast@gmail.com")).partition("\x00")
    if verdict != "FAIL":
        return "verdict is %s, not FAIL: %s" % (verdict, why[:200])
    if "reach one mailbox" not in why or "first.last@gmail.com" not in why \
            or "firstlast@gmail.com" not in why:
        return "FAIL sentence does not name the one-mailbox aliasing by address: %s" % why[:300]
    return "fail-names-mailbox-alias"


@case("a-different-dotted-pair-on-a-non-gmail-host-stays-proven-different",
      "gaterun", "pass-proven-different")
def r7b(root):
    """The control the fix must not break: the dot fold is HOST-SCOPED. On a
    non-gmail host a dot is significant, so `dana.author@example.com` and
    `danaauthor@example.com` are two genuinely different mailboxes, and this
    must still reach the certifying PASS naming them proven different, the
    same as before this fix existed. A universal dot fold would wrongly
    merge this pair and turn a real second approver into a false
    self-approval refusal."""
    verdict, _, why = _approval_verdict_and_reason(
        root, "G", "dana.author@example.com",
        ("Dana Author", "danaauthor@example.com")).partition("\x00")
    if verdict != "PASS":
        return "verdict is %s, not PASS: %s" % (verdict, why[:200])
    if "proven different" not in why:
        return "PASS sentence dropped 'proven different': %s" % why[:300]
    return "pass-proven-different"


@case("gmail-plus-tag-and-case-fold-still-collapse-with-the-dot-fold", "sig", "FAIL")
def r7c(root):
    """Regression guard for the new host-scoped fold stacked with the two
    folds it sits beside: a plus-tag and a mixed case on the SAME gmail
    address must still canonicalize to the author's own mailbox once the
    dot is also folded, not just when each fold is exercised alone."""
    return _approval_with_sig(root, "G", approver="First.Last+ops@GMAIL.com",
                              authors=("First Last", "firstlast@gmail.com"))


# ---------------------------------------------------------------------------
# Decision packages (src/brothersbe/decisions.py): the four controls Feature 2
# ships, each fed the defect it exists to catch. The module lives in a package
# and uses relative imports, so it is imported the way
# tools/test_sbe_decisions.py imports it, through sys.path, not through
# SourceFileLoader, which would break `from . import tasks`.
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(HERE, "..", "src"))
from brothersbe import decisions as _decisions  # noqa: E402


def _decision_repo(root):
    """A repository with one commit, so a package has a head to bind to. The
    same seed fixture tools/test_sbe_decisions.py builds."""
    git_init(root)
    write(root, "seed.txt", "seed\n")
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "seed"], check=True)


@case("a-waived-check-writes-a-waived-package-never-a-pass-one", "guard",
      "waived-not-passed")
def dp1(root):
    """THE PLANTED DEFECT: a run whose only decision is a WAIVED check. The
    failure class is a package that records the waiver as PASS, which is I7
    laundered through a second artifact: the gate itself never prints PASS for
    a waived control (gx4 above holds that line), and a decision package that
    upgraded the word on the way to disk would hand a reviewer the clean bill
    the gate refused to print. The package must record WAIVED in its own
    header and quote the WAIVED line, never a PASS."""
    _decision_repo(root)
    text = ("  >> migration WAIVED   .sbe-exempt names this directory: rehearsal pending\n"
            "chatter nobody parses\n")
    written = _decisions.record_from_run(root, text, None)
    if len(written) != 1:
        return "%d package(s) written for one WAIVED line" % len(written)
    body = open(written[0], encoding="utf-8").read()
    if not re.search(r"(?m)^- verdict recorded by the run: WAIVED$", body):
        return "the package does not record WAIVED as the verdict"
    if re.search(r"(?m)^- verdict recorded by the run: PASS", body):
        return "PASS recorded over a waived control"
    quoted = re.search(r"(?m)^    (?:>> )?migration\s+(\S+)", body)
    if quoted is None or quoted.group(1) != "WAIVED":
        return "the quoted verdict line does not read WAIVED: %r" % (
            quoted.group(0) if quoted else None)
    return "waived-not-passed"


@case("a-decision-package-quotes-no-line-outside-the-verdict-grammar", "guard",
      "counted-not-copied")
def dp2(root):
    """THE PLANTED DEFECT: a run whose output carries a connection string with
    a credential in it, one line under a FAIL the package must record. A
    decision package is written to be pasted into a pull request, and the
    evidence receipts persist digests rather than raw output for exactly this
    reason, so a package that copied one unmatched line would quietly widen
    that policy in an artifact that is also shared. Every line outside the
    verdict grammar is counted and discarded, and the package states the
    count."""
    _decision_repo(root)
    secret = "connection: postgres://svc:hunter2@db.internal/prod"
    text = ("numbers   FAIL     an overstated total in report.md\n"
            "%s\n"
            "Traceback (most recent call last):\n" % secret)
    parsed = _decisions.parse_verdict_lines(text)
    if parsed["unquotedLineCount"] != 2:
        return "%d unmatched line(s) counted, not 2" % parsed["unquotedLineCount"]
    if any("hunter2" in v["line"] for v in parsed["verdicts"]):
        return "the parser carried the secret line out as a verdict"
    written = _decisions.record_from_run(root, text, None)
    if len(written) != 1:
        return "%d package(s) written for one FAIL line" % len(written)
    body = open(written[0], encoding="utf-8").read()
    if "hunter2" in body or "db.internal" in body:
        return "a line outside the verdict grammar was copied into the package"
    if "counted and not copied: 2" not in body:
        return "the package does not state the discarded-line count"
    return "counted-not-copied"


@case("a-package-bound-to-an-older-commit-is-never-overwritten", "guard",
      "refused-and-superseded")
def dp3(root):
    """THE PLANTED DEFECT: a package aimed at the directory of an existing
    DECISION.md bound to an OLDER commit. The older package records what
    somebody decided about a different program, and a rewrite would delete
    that with no record of the deletion, so the write must be refused with
    the older file left byte for byte as it was, and the honest path is a NEW
    id whose package names the one it supersedes."""
    _decision_repo(root)
    trigger = {"kind": "gate", "check": "numbers", "verdict": "FAIL",
               "verdictLine": "numbers FAIL an overstated total in report.md",
               "otherLines": [], "dossier": None}
    first = _decisions.write_package(root, _decisions.build_package(root, trigger))
    before = open(first, encoding="utf-8").read()
    write(root, "seed.txt", "seed\nsecond\n")
    subprocess.run(["git", "-C", root, "commit", "-aqm", "second"], check=True)
    stale = _decisions.build_package(root, trigger)
    stale["dir"] = os.path.dirname(first)
    try:
        _decisions.write_package(root, stale)
        return "the rewrite over the older commit's package was accepted"
    except _decisions.DecisionUnwritable as exc:
        if "append-only" not in str(exc):
            return "the refusal does not say append-only: %s" % str(exc)[:160]
    after = open(first, encoding="utf-8").read()
    if after != before:
        return "the older package's bytes changed under the refusal"
    second = _decisions.write_package(root, _decisions.build_package(root, trigger))
    if os.path.dirname(second) == os.path.dirname(first):
        return "the honest path reused the older package's directory"
    if "supersedes" not in open(second, encoding="utf-8").read():
        return "the new package does not name the one it supersedes"
    return "refused-and-superseded"


@case("a-lineage-hop-with-no-evidence-pointer-is-refused", "guard",
      "every-hop-pointed")
def dp4(root):
    """THE PLANTED DEFECT: a hop dict with an empty `evidence`. A hop with no
    pointer proves nothing: the chain's whole claim is that every line names
    the file, receipt, commit or package a reader can open. The place the
    pointer is most tempting to drop is a NO-DATA hop over an absent store,
    so this walks BOTH chains, an artifact git knows and an artifact nothing
    knows (whose chain is NO-DATA hops end to end), and refuses any hop whose
    evidence or summary is empty."""
    _decision_repo(root)
    for artifact in ("seed.txt", "never/existed.py"):
        data = _decisions.lineage(root, artifact)
        if not data["hops"]:
            return "no hops at all for %s" % artifact
        for hop in data["hops"]:
            if not str(hop.get("evidence") or "").strip():
                return "hop %r about %s carries no evidence pointer" % (
                    hop["kind"], artifact)
            if not str(hop.get("summary") or "").strip():
                return "hop %r about %s says nothing" % (hop["kind"], artifact)
    return "every-hop-pointed"


# ---------------------------------------------------------------------------
# T4: commit binding for the three hard-gate evidence files (numbers-manifest,
# migration-receipt, ran-receipt). src/brothersbe/evidence.py's own `sbe
# evidence` receipt store already binds a receipt to a commit
# (evidence.py::_check_commit); these three older, unrelated receipt shapes
# carried no such concept at all, so a receipt that once earned a PASS at an
# old commit still earned the same PASS after the tree moved on: a passing
# receipt COPIED FORWARD from an earlier commit cleared the gate at the new
# one as if it had been produced there. `headCommit`, the same optional field
# name and comparison the evidence store uses, is now read by gate_numbers,
# gate_migration and gate_ran: present and matching the directory's current
# HEAD, nothing changes; present and naming a commit that is not HEAD, the
# receipt is stale and the gate FAILs rather than PASSing over it. A receipt
# recording no headCommit at all is not judged here, exactly as every receipt
# on disk before this change already was not: this gate cannot tell a receipt
# that predates the field apart from an operator who chose not to record one,
# so every eval case above this section (none of which sets up a git repo)
# keeps behaving exactly as it did.


def _git_head(root):
    return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@case("a-stale-headcommit-ran-receipt-no-longer-passes", "ran", "FAIL")
def cb1(root):
    # THE DONE-CHECK DEFECT, reproduced: a ran-receipt.json that would PASS on
    # its own is bound to the FIRST commit, then a second commit moves HEAD on
    # and changes tracked code the receipt says nothing about. The receipt's
    # checks are sound by every field they carry; only the commit it names is
    # stale.
    #
    # THE SECOND COMMIT MUST TOUCH SOMETHING OUTSIDE THE RECEIPT, and this is
    # the whole fixture. Row E83 taught the reader that a receipt survives a
    # commit which changed nothing but the receipt and its evidence store, so
    # a fixture whose second commit added only the receipt file stopped being
    # a stale receipt at all and these three cases went green as PASS against
    # want=FAIL. Changing x is what makes the tree actually move.
    git_init(root)
    write(root, "x", "1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
    old = _git_head(root)
    write(root, "ran-receipt.json", {"headCommit": old, "checks": [
        {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})
    write(root, "x", "2")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "second"], check=True)


@case("a-stale-headcommit-numbers-manifest-no-longer-passes", "numbers", "FAIL")
def cb2(root):
    git_init(root)
    write(root, "x", "1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
    old = _git_head(root)
    write(root, "numbers-manifest.json", {"headCommit": old, "figures": [{
        "label": "gmv", "snapshot_id": "snap-1", "query": "SELECT SUM(amount) FROM orders",
        "second_derivation": "SELECT SUM(qty*price) FROM order_lines",
        "rerun": {"ran": True, "primary": 17570, "secondary": 17570}}]})
    # See cb1: the second commit must move tracked code the receipt does not
    # cover, or the reader rightly carries the receipt forward.
    write(root, "x", "2")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "second"], check=True)


@case("a-stale-headcommit-migration-receipt-no-longer-passes", "migration", "FAIL")
def cb3(root):
    git_init(root)
    write(root, "x", "1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
    old = _git_head(root)
    write(root, "migration-receipt.json", {
        "headCommit": old,
        "forward": {"ran_against_restore": True},
        "reverse": {"ran_against_restore": True, "rehearsal_run_id": "job-8842"},
        "row_counts": {"before": 100, "after_reverse": 100}})
    # See cb1: the second commit must move tracked code the receipt does not
    # cover, or the reader rightly carries the receipt forward.
    write(root, "x", "2")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "second"], check=True)


@case("a-headcommit-bound-to-the-current-commit-still-passes", "ran", "PASS")
def cb4(root):
    # The control: staleness is what this check catches, not presence. A
    # receipt naming the CURRENT HEAD passes exactly as one naming no commit
    # at all would.
    git_init(root)
    write(root, "x", "1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
    head = _git_head(root)
    write(root, "ran-receipt.json", {"headCommit": head, "checks": [
        {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})


@case("a-non-string-headcommit-is-caught", "ran", "FAIL")
def cb5(root):
    # A container or a number where a commit id belongs pins nothing, the
    # same rule numbers-manifest.json's snapshot_id already enforces.
    git_init(root)
    write(root, "x", "1")
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-qm", "first"], check=True)
    write(root, "ran-receipt.json", {"headCommit": 12345, "checks": [
        {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})


@case("no-git-history-leaves-the-receipt-unaffected", "ran", "PASS")
def cb6(root):
    # No HEAD to bind against: every eval case in this file before this
    # section relies on exactly this behavior, since none of them git_init a
    # repo. This case pins it directly rather than leaving it implicit.
    write(root, "ran-receipt.json", {"checks": [
        {"name": "reconcile", "exit_code": 0, "duration_ms": 812}]})


# Program-status doc truth: program/STATUS.md is a generated artifact, and a
# generated artifact without a drift gate rots exactly like the hand-written
# one it replaced. Three cases: the real repository's committed STATUS.md must
# match a fresh render of the live ledger; the detection mechanism itself is
# calibrated on a temp ledger; and the round-1 Critical (an absent ledger
# source reading as a clean program) is pinned here at the release gate, not
# only in the module's own suite.
_program_mod = SourceFileLoader(
    "sbe_program_eval", os.path.join(HERE, "..", "src", "brothersbe", "program.py")).load_module()


@case("program-status-md-matches-the-live-ledger", "docs", "fresh")
def dc_prog_fresh(root):
    # program/ is the internal program-management ledger and the public export
    # does not carry it (docs/plan/EXPORT-ALLOWLIST.txt, the M6 note). Without
    # it there is no ledger to render and nothing to compare STATUS.md against.
    gap = _withheld(os.path.join("program", "PROGRAM.yaml"))
    if gap:
        return gap
    try:
        fresh = _program_mod.check_status_md(_REPO)
    except _program_mod.ProgramParseError as exc:
        return "the live ledger did not parse: %s" % exc
    return "fresh" if fresh else (
        "program/STATUS.md no longer matches a fresh render of the ledger; "
        "regenerate with: sbe program status --write")


@case("program-status-drift-is-detected", "docs", "drift-detected")
def dc_prog_drift(root):
    write(root, os.path.join("program", "PROGRAM.yaml"),
          "program: t\nversion_target: 1.0.0\nstatus: x\nplan: MASTER-PLAN.md\n")
    write(root, os.path.join("program", "work-items", "BR-A.yaml"),
          "id: BR-A\ntitle: T\nstatus: done\n")
    _program_mod.write_status_md(root)
    if not _program_mod.check_status_md(root):
        return "a freshly written STATUS.md read as stale, so the comparison is broken"
    write(root, os.path.join("program", "work-items", "BR-B.yaml"),
          "id: BR-B\ntitle: New\nstatus: not_started\n")
    return "drift-detected" if not _program_mod.check_status_md(root) else (
        "the ledger changed and STATUS.md still read as fresh: drift is invisible")


@case("program-status-no-data-source-never-reads-clean", "docs", "refused")
def dc_prog_nodata(root):
    write(root, os.path.join("program", "PROGRAM.yaml"),
          "program: t\nversion_target: 1.0.0\nstatus: x\nplan: MASTER-PLAN.md\n")
    _program_mod.write_status_md(root)
    return "refused" if not _program_mod.check_status_md(root) else (
        "an absent work-items directory checked out clean: NO-DATA read as a pass")


def main():
    # `--only <substring>` runs just the cases whose NAME contains it. Added
    # because the bed is the delivery bottleneck, not the work: a full run is
    # over ten minutes on a loaded machine, and every lane and every reviewer
    # was paying that to check two cases. A filtered run is seconds, so a
    # reinjection loop (break the guard, watch the case fail, restore, watch it
    # pass) stops costing forty minutes and starts costing one.
    #
    # THE COUNT LINE STILL NAMES THE WHOLE BED, and the filtered run says how
    # many it skipped. That is the point rather than a detail: a partial run
    # whose summary reads like a full one is the "number true in one directory
    # reported as true generally" failure this repository has hit repeatedly,
    # and it would be worse here because the number is quoted in shipped
    # documentation. A filtered run is explicitly NOT evidence that the bed is
    # green, prints that sentence itself, and exits 2 rather than 0 so no
    # script can mistake it for a clean full run.
    only = None
    argv = [a for a in sys.argv[1:]]
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = argv[i + 1]
        elif a.startswith("--only="):
            only = a.split("=", 1)[1]
    if only is not None and not only.strip():
        sys.stderr.write("run_evals: --only was given an empty pattern, which would run "
                         "every case while printing a filtered run's caveats. Refusing: "
                         "drop the flag to run the whole bed.\n")
        sys.exit(2)

    # A case may return "PLATFORM-GAP: <reason>" when the platform it runs on
    # cannot mount the scenario at all (no mkfifo, a POSIX-only script). The
    # gap is counted apart and printed with its reason: never a pass (the
    # guarantee went unmeasured here) and never a block (only regressions
    # decide the exit code), the same three-state honesty the checkers keep
    # for NO-DATA. Introduced with the windows-latest leg, run 31040612827.
    passed = failed = 0
    gaps = []
    selected = [c for c in CASES if only is None or only in c[0]]
    if only is not None and not selected:
        sys.stderr.write("run_evals: --only %r matched no case name out of %d. A filter that "
                         "matches nothing must not read as a clean run.\n" % (only, len(CASES)))
        sys.exit(2)
    for name, klass, expect, fn in selected:
        with tempfile.TemporaryDirectory() as d:
            try:
                if klass in ("tier", "decide", "sig", "lints", "score", "designrun",
                             "gaterun", "evidence", "guard", "docs"):
                    verdict = fn(d)
                else:
                    fn(d)
                    verdict = run_gate(d, klass)
            except Exception as e:
                verdict = "ERROR:%r" % e
        # A case may also return "NO-DATA: <reason>" when THIS TREE does not
        # carry a file the scenario reads. Counted and printed exactly as
        # PLATFORM-GAP is, for the same reason: never a pass (the guarantee
        # went unmeasured here) and never a block (only regressions decide the
        # exit code). Added for row E112, after a public clone of v1.0.2
        # reported ERROR:FileNotFoundError on four cases whose files the
        # export deliberately withholds, which reads as a broken product.
        # A case whose EXPECTED verdict is itself NO-DATA is untouched: the
        # equality below is reached only when the case did not return one of
        # these two prefixes, and no expectation in this bed is a prefix.
        gap_label = None
        if isinstance(verdict, str) and verdict != expect:
            for prefix in ("PLATFORM-GAP:", "NO-DATA:"):
                if verdict.startswith(prefix):
                    gap_label = prefix.rstrip(":")
                    reason = verdict[len(prefix):].strip()
                    break
        if gap_label is not None:
            gaps.append((name, reason))
            print("  %-38s want=%-8s got=%s (%s)" % (name, expect, gap_label, reason))
            continue
        ok = verdict == expect
        passed += ok
        failed += not ok
        print("  %-38s want=%-8s got=%-8s %s" % (name, expect, verdict, "ok" if ok else "REGRESSION"))
    tail = "" if not gaps else (", %d gap(s) (platform or absent-file), each named above, "
                                "never a pass" % len(gaps))
    if only is None:
        print("\n%d evals: %d passed, %d regressions%s." % (len(CASES), passed, failed, tail))
        sys.exit(1 if failed else 0)
    print("\n%d of %d evals ran (--only %r): %d passed, %d regressions%s."
          % (len(selected), len(CASES), only, passed, failed, tail))
    print("FILTERED RUN. %d case(s) were NOT run, so this says nothing about them and is not "
          "evidence the bed is green. Exit 2 on purpose, whatever the verdicts above, so no "
          "script reads it as a clean full run." % (len(CASES) - len(selected)))
    sys.exit(1 if failed else 2)


if __name__ == "__main__":
    main()

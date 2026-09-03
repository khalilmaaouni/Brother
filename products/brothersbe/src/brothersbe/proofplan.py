"""BAND F, F3: the proof plan, a behavior-to-proof map that refuses the
middle state everything else in this project has already found and named
once. `evidence.py`'s own module docstring calls it out directly: a receipt
that could not be recomputed against the world as it stands now is refused,
never read hopefully (see that module's "AND THE DECLARATION IS NO LONGER
THE IDENTITY" section). This module applies the identical rule one layer up,
to the claim "this behavior is proven" rather than to one command's receipt.

THE DEFECT THIS EXISTS FOR, in one sentence: a plan, a spec, a PR description
can claim a behavior is covered by pointing at a check, and nothing short of
actually reading that check's own evidence tells a reader whether the check
exists, ever ran, or ran against the code as it stands now, so "assumed
covered" quietly stands in for "covered" until somebody checks by hand.

THE THREE VERDICTS, and only three, `PROOF_VERDICTS` below:

  - CHECK_IDENTIFIED: a named check exists on disk, an evidence receipt
    (`evidence.py`'s own receipt shape, read with `evidence.load()`) shows it
    ran, that receipt's `headCommit` still matches the CURRENT head of the
    tree, that receipt passes `evidence.py`'s own `verify()` (not merely
    `load()`, which is JSON-parse plus stat only and proves nothing about
    the receipt's seal, required fields or coverage), that receipt's OWN
    RECORDED RUN actually names the check: `checkPath` appears in the
    receipt's `argv` (the command that ran) or in its `coveredFiles` (the
    files the run claims to cover), AND that receipt's own `exitCode` records
    0. All six, every time; any one missing is NOT a weaker pass. A one-key
    hand-typed file like `{"headCommit": "<head>"}` used to satisfy the three
    checks this module ran itself while `evidence.verify()` over the
    identical file returned FAIL; requiring `verify()` closes that gap rather
    than re-deriving its rules here a second time. A GENUINE, sealed,
    verify()-PASSing receipt minted from a run that never touched the named
    check (`python -c pass`, covering only an unrelated file, attached to a
    different `checkPath`) used to satisfy every one of those first four
    conditions anyway, because none of them ever compared the receipt's own
    recorded run against the check the entry declares: `verify()` proves a
    receipt is genuine and current, not which check it is evidence FOR. The
    fifth condition closes that: a receipt proves only the run it recorded,
    so the check it names must be that run. The sixth closes a narrower gap
    at the same seam: a receipt can be genuine, current, and unmistakably
    about the right check, and still record that the check FAILED. A named
    check whose own recorded run did not succeed cannot be the proof that the
    behavior it claims to cover actually holds, so a nonzero (or absent)
    `exitCode` degrades to NO-DATA, naming the failing run, exactly as a
    stale or unverifiable receipt already does; it does not, and cannot,
    reach the CHECK_IDENTIFIED arm.
  - HUMAN_JUDGMENT_REQUIRED: the entry declares, by name, that no mechanical
    check can decide this and a named human must. An unnamed "a human should
    look at this" identifies nobody and is refused, the same way
    `contracts._not_human_problems` refuses a decision authored by no one.
  - NO-DATA: nobody has identified either. This is also where every
    DEGRADED claim lands: a proof naming a check file that does not exist, a
    check that exists but carries no evidence it ever ran, and a receipt
    recorded against a head the tree has since moved past (STALE, named as
    such in the problem string) are three different reasons and none of them
    is a partial CHECK_IDENTIFIED. There is no verdict between
    CHECK_IDENTIFIED and NO-DATA, by construction: every branch below returns
    one of exactly three strings, and `tools/test_sbe_proofplan.py` pins that
    the returned verdict is always a member of `PROOF_VERDICTS`.

CHECK VERDICTS ARE A DIFFERENT VOCABULARY. `PASS`/`FAIL`/`NO-DATA`
(`contracts.VERDICTS`) is what a single check run resolves to; the three
words above are PLAN-LEVEL vocabulary about whether a behavior's proof is
even IDENTIFIED, never a restatement of what that check found. This module
does read the receipt's own `exitCode` (CHECK_IDENTIFIED requires it be 0;
see the bullet above), but only as a gate on IDENTIFICATION, never as a
verdict of its own: it does not run the check, does not interpret what a
particular nonzero code means beyond "not zero", and does not fold the
check's PASS/FAIL into a fourth word here. A run that never happened, a run
that failed, and a run that is not this check's run all land on the same
plan-level word, NO-DATA, the same separation `contracts.py`'s own module
docstring draws between `VERDICTS` and `READINESS_STATES`/`REALITY_STATES`/
`DECLARATION_STATES` (disjoint vocabularies, never a fourth check verdict).

VACUOUS TRUTH IS REFUSED BY CONSTRUCTION. A proof plan is a list of
behavior-to-proof entries; call it the behavior list or the proof list, both
names for the one list this module reads, and an EMPTY one is its own
finding, most severe, adjacent to (never merged into) NO-DATA. The hazard
this guards is the one every `all()` over an empty sequence in Python walks
into for free: `all(v == "CHECK_IDENTIFIED" for v in ())` is `True`, so a
caller that only asked "does every row pass" would read a plan claiming
NOTHING as a plan that proves EVERYTHING. `evaluate_plan` below never lets a
caller reach that fold: an empty (or non-list) input returns no rows AT ALL,
paired with a named note that is not `None`, so "zero rows, no note" -- the
shape that would let the vacuous fold happen silently -- cannot occur.

WHAT THIS MODULE DOES NOT DO, named because a control that oversells itself
is worse than none, the same law `evidence.py`'s own docstring states for
itself: it does not run any check (that is `evidence.py`'s `generate()`); it
reads the receipt's own `exitCode` only as a zero/nonzero gate on whether a
run can identify the behavior at all, and it does not otherwise interpret
that code, fold it into `contracts.VERDICTS`, or decide anything about a
check's own PASS/FAIL beyond that one gate (that finer reading is other
code's job); it does not validate a proof-plan document's SHAPE at the
`contracts.py` registry level
(no `schemaVersion`, no surface entry there) -- it is a pure function over
whatever dicts a caller hands it, the same "reads facts, computes a verdict,
writes nothing" shape `lifecycle.py`'s own module docstring claims for
itself.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only.
"""
import os
import subprocess

from ._toolspath import mount
mount()
from sbe_checks import answered  # noqa: E402  (tools/ has to be mounted first)

#: The closed, plan-level vocabulary this module's per-behavior verdict is
#: always drawn from. Byte-identical in SHAPE to `contracts.VERDICTS`
#: (`("PASS", "FAIL", "NO-DATA")`) but a DIFFERENT vocabulary, on purpose: see
#: the module docstring's "CHECK VERDICTS ARE A DIFFERENT VOCABULARY"
#: section. Not imported from `contracts.py`: these three words belong to
#: this module, the way `READINESS_STATES` belongs to `contracts.py` itself
#: rather than to whichever module derives one.
PROOF_VERDICTS = ("CHECK_IDENTIFIED", "HUMAN_JUDGMENT_REQUIRED", "NO-DATA")

#: The two shapes a `proof` entry may declare itself as. A `kind` outside
#: this tuple identifies nothing this module reads and is refused (`NO-DATA`)
#: rather than guessed at.
PROOF_KINDS = ("check", "human")


def _current_head(cwd):
    """The head commit `cwd` sits on right now, or None when git cannot
    answer.

    A small, local git call rather than a cross-module import: `decisions.
    head_commit_of` and `impact._git` already resolve HEAD this same way
    (`["git", "rev-parse", "HEAD"]`, run in `cwd`, `None` on any failure
    rather than a raised exception a caller would have to guard against for
    one field), and this restates that one call locally instead of adding a
    new dependency edge from a brand new module onto either of theirs for a
    single subprocess invocation. None is a real answer here, the same as in
    both of those: a caller that cannot resolve the current head has nothing
    honest to compare a receipt's `headCommit` against, and `proof_verdict`
    below reports that as its own named NO-DATA rather than assuming a match.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                             capture_output=True, text=True)
    except OSError:  # sbe: allow-silent every caller of this function treats None as "the
        # current head could not be resolved" and reports its OWN named NO-DATA over it (see
        # proof_verdict's "current_head is None" branch above), the same "None is a real
        # answer, never a crash" contract status.py:164's identical git-failure guard already
        # states for itself; no fact is dropped, because there is no fact here beyond "git did
        # not run", which the caller's own sentence already names
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def _path_variants(candidate, cwd):
    """Every normalized form `candidate` might be recorded or declared as, so
    a receipt's own `argv` token or `coveredFiles` path (recorded relative to
    whatever cwd the run actually used) still matches a `checkPath` given
    relative to a different cwd, or with a different separator.

    Path IDENTITY, not string identity: comparing raw strings only would miss
    the ordinary case (repo-relative on both sides, but one carries a leading
    `./` or a backslash); comparing by basename only would let two unrelated
    files that merely share a filename bind to each other, which is a worse
    defect than the one this closes. `candidate` that is not a non-empty
    string names nothing and returns no variants, rather than raising: a
    caller is never required to type-check before calling this.
    """
    if not isinstance(candidate, str) or not candidate:
        return frozenset()
    variants = {candidate, candidate.replace(os.sep, "/"), os.path.normpath(candidate)}
    if os.path.isabs(candidate):
        variants.add(os.path.normpath(candidate))
    else:
        variants.add(os.path.normpath(os.path.join(cwd, candidate)))
    return frozenset(os.path.normpath(v) for v in variants)


def _receipt_names_check(receipt, check_path, full_check, cwd):
    """(matched, detail): does `receipt`'s OWN RECORDED RUN actually name
    `check_path`, the check a proof entry declares it as evidence for?

    THE GAP THIS CLOSES: `evidence.verify()` proves a receipt is genuine,
    current and internally consistent; it never asks WHICH check the run it
    recorded was offered as evidence for. A receipt minted from `python -c
    pass`, covering only an unrelated file, verifies exactly as cleanly as
    one that ran the named check, so nothing upstream of this function
    catches a receipt attached to a check its own run never touched.

    Bound here the same two ways the receipt shape itself already records
    provenance, never a third field invented for this: `argv`, the command
    that actually ran (`evidence.redact_argv` only masks known SECRET
    shapes, never a path, so a check path reaches `argv` untouched), and
    `coveredFiles`, the files the run declares it covers. Either binding is
    enough: `argv` covers a check invoked directly (`python tools/x.py`);
    `coveredFiles` covers one invoked through a runner that names the check
    as the file under test (`--covers tools/x.py` on some other invocation).
    Neither present is the exact bypass this closes.
    """
    check_variants = _path_variants(check_path, cwd) | _path_variants(full_check, cwd)
    argv = receipt.get("argv")
    if isinstance(argv, list):
        for token in argv:
            if _path_variants(token, cwd) & check_variants:
                return True, "named in the receipt's own recorded argv"
    covered = receipt.get("coveredFiles")
    if isinstance(covered, list):
        for item in covered:
            if not isinstance(item, dict):
                continue
            if _path_variants(item.get("path"), cwd) & check_variants:
                return True, "named in the receipt's own recorded coveredFiles"
    return False, None


def proof_verdict(entry, cwd):
    """(verdict, evidence, problems) for ONE behavior-to-proof entry, the
    house 3-tuple `contracts.py`'s own module docstring names ("THE HOUSE
    3-TUPLE"): `verdict` is always a member of `PROOF_VERDICTS`, `evidence`
    is one human-readable line, and `problems` is empty on the passing arms
    (CHECK_IDENTIFIED, and a well-formed HUMAN_JUDGMENT_REQUIRED) and carries
    the one named reason on every NO-DATA.

    `entry` is a dict with `behavior` (the claimed behavior; read for the
    evidence text only, never itself a pass/fail condition) and `proof`,
    which is either absent or hollow (no proof identified: NO-DATA) or a
    dict naming its own `kind`:

      - `{"kind": "human", "human": "<name>"}`: HUMAN_JUDGMENT_REQUIRED when
        `human` answers something; NO-DATA, naming the gap, when it does not
        (an unnamed judge decides nothing, the same rule `contracts.
        _not_human_problems` already holds a recorded decision to).
      - `{"kind": "check", "checkPath": "<path>", "evidenceReceipt":
        "<path>"}`: CHECK_IDENTIFIED only when ALL of these hold, checked in
        order and returning NO-DATA, naming the first that fails, the moment
        one does: `checkPath` and `evidenceReceipt` are both strings (a
        typed-wrong value -- an int, a list -- names no path a filesystem
        can look up and is refused by name rather than crashing `os.path`);
        `checkPath` exists as a file on disk; `evidenceReceipt` parses as a
        receipt `evidence.load()` can read; that receipt's own `headCommit`
        equals `_current_head(cwd)`, resolved fresh on every call (a proof
        recorded against a head this tree has since moved past is STALE,
        named as such in the returned problem string, and is NO-DATA, never
        a silent pass); that receipt passes `evidence.verify()`, the real
        seal-and-coverage check, not merely `load()`'s JSON-parse;
        (`_receipt_names_check`) that receipt's own `argv` or `coveredFiles`
        actually names `checkPath`; AND that receipt's own `exitCode` records
        `0`. `verify()` proves the receipt is genuine, current and internally
        consistent; it says nothing about WHICH check the run it recorded was
        offered as evidence for, so a receipt minted from an unrelated
        command still verifies cleanly. A verified receipt whose recorded run
        never names this check degrades to NO-DATA, naming the mismatch: the
        receipt is real, but it proves a different run. And a verified
        receipt that DOES name this check, but whose own `exitCode` is
        nonzero or missing, degrades to NO-DATA too, naming the failing (or
        unrecorded) run: a check that ran and FAILED, or whose exit status
        was never recorded at all, proves nothing about the behavior it was
        pointed at, and absence of a recorded `exitCode` is refused the same
        way, never read as a quiet success.

    `evidence` is imported here, lazily, the same way `decisions.py`'s own
    `_lineage_receipts` and `cli.py` already import it rather than at this
    module's top level: see `evidence.py`'s own module-scope comment on why
    an eager import of it from a module several other modules reach can
    close a cycle back onto a partially initialized package.
    """
    from . import evidence as evidence_mod

    behavior = entry.get("behavior") if isinstance(entry, dict) else None
    behavior_label = behavior if answered(behavior) is not None else "(unnamed behavior)"
    proof = entry.get("proof") if isinstance(entry, dict) else None

    if not isinstance(proof, dict) or not proof:
        problem = ("behavior %r names no proof at all: no check is identified and no human "
                  "is named, so nothing here decides whether it is true. This is exactly the "
                  "gap this module exists to surface rather than let read as \"assumed "
                  "covered\"" % (behavior_label,))
        return "NO-DATA", problem, (problem,)

    kind = proof.get("kind")

    if kind == "human":
        human = answered(proof.get("human"))
        if human is None:
            problem = ("behavior %r declares a human-judgment proof but names no human: an "
                      "unnamed judge decides nothing" % (behavior_label,))
            return "NO-DATA", problem, (problem,)
        return ("HUMAN_JUDGMENT_REQUIRED",
               "behavior %r is proved by human judgment: %s must decide; no mechanical check "
               "can" % (behavior_label, human), ())

    if kind != "check":
        problem = ("behavior %r declares proof.kind %r, which is not one this module reads "
                  "(known: %s)" % (behavior_label, kind, ", ".join(PROOF_KINDS)))
        return "NO-DATA", problem, (problem,)

    check_path = answered(proof.get("checkPath"))
    if check_path is None:
        problem = ("behavior %r declares a check proof but names no checkPath"
                  % (behavior_label,))
        return "NO-DATA", problem, (problem,)
    if not isinstance(check_path, str):
        problem = ("behavior %r declares checkPath as %s (%r), not a path string: a "
                  "checkPath that is not text identifies nothing a filesystem can look up, "
                  "and is refused by name here rather than crashing the module that reads it"
                  % (behavior_label, type(check_path).__name__, check_path))
        return "NO-DATA", problem, (problem,)

    full_check = check_path if os.path.isabs(check_path) else os.path.join(cwd, check_path)
    if not os.path.isfile(full_check):
        problem = ("behavior %r names check %r as its proof, and %s does not exist: a proof "
                  "pointing at a check nobody wrote identifies nothing, and is refused rather "
                  "than passed" % (behavior_label, check_path, full_check))
        return "NO-DATA", problem, (problem,)

    receipt_path = answered(proof.get("evidenceReceipt"))
    if receipt_path is None:
        problem = ("behavior %r names check %r, which exists on disk, but no evidence "
                  "receipt is named for it: a check that EXISTS is not a check that RAN"
                  % (behavior_label, check_path))
        return "NO-DATA", problem, (problem,)
    if not isinstance(receipt_path, str):
        problem = ("behavior %r names check %r, but its evidenceReceipt is %s (%r), not a "
                  "path string: a receipt path that is not text cannot be read, and is "
                  "refused by name here rather than crashing the module that reads it"
                  % (behavior_label, check_path, type(receipt_path).__name__, receipt_path))
        return "NO-DATA", problem, (problem,)

    full_receipt = (receipt_path if os.path.isabs(receipt_path)
                    else os.path.join(cwd, receipt_path))
    try:
        receipt = evidence_mod.load(full_receipt)
    except evidence_mod.ReceiptUnreadable as exc:
        problem = ("behavior %r names check %r, which exists on disk, but its named evidence "
                  "receipt at %s could not be read (%s): a check nothing shows ran is not "
                  "covered" % (behavior_label, check_path, full_receipt, exc))
        return "NO-DATA", problem, (problem,)

    recorded_head = answered(receipt.get("headCommit"))
    if recorded_head is None:
        problem = ("behavior %r names check %r, whose evidence receipt at %s records no "
                  "headCommit, so it cannot be bound to any tree state"
                  % (behavior_label, check_path, full_receipt))
        return "NO-DATA", problem, (problem,)

    current_head = _current_head(cwd)
    if current_head is None:
        problem = ("the current head of %s could not be resolved, so the evidence receipt "
                  "for check %r (behavior %r) cannot be checked for staleness"
                  % (cwd, check_path, behavior_label))
        return "NO-DATA", problem, (problem,)

    if recorded_head != current_head:
        problem = ("STALE: behavior %r names check %r, whose evidence receipt was recorded "
                  "at head %s, and this tree is now at head %s. A proof recorded against a "
                  "head the tree has since moved past is stale, never a silent pass"
                  % (behavior_label, check_path, recorded_head, current_head))
        return "NO-DATA", problem, (problem,)

    # F-2b: `load()` above is JSON-parse plus stat only, the same limit its own
    # docstring names ("access is checked before open(), by stat rather than by
    # opening"); it proves the file parses, never that the run it claims to
    # describe is genuine. A one-key hand-typed file (`{"headCommit": head}`)
    # passes every check above and still fails `evidence.verify()`'s own seal,
    # required-field and coverage checks, so CHECK_IDENTIFIED is refused here
    # unless the receipt clears verify() too, not merely load().
    verify_result = evidence_mod.verify(full_receipt, cwd=cwd)
    if verify_result.get("verdict") != "PASS":
        reasons = verify_result.get("reasons") or ["evidence.verify() gave no reason"]
        problem = ("behavior %r names check %r, whose evidence receipt at %s parses and "
                  "names the current head, but does not PASS evidence.verify() (verdict "
                  "%s): %s. A receipt evidence.py's own verification refuses is not proof "
                  "this behavior is covered, whatever it parses as"
                  % (behavior_label, check_path, full_receipt, verify_result.get("verdict"),
                     "; ".join(str(r) for r in reasons)))
        return "NO-DATA", problem, (problem,)

    # THE BAND F BLOCKER, CLOSED HERE. Every check above proves this receipt
    # is genuine, current and internally consistent; none of them ever asks
    # whether the run it recorded actually touched `check_path`. A receipt
    # minted from `python -c pass`, covering only an unrelated file, cleared
    # every condition above while proving nothing about this check. Refused
    # here rather than left to a reader to notice: see `_receipt_names_check`.
    matched, _match_detail = _receipt_names_check(receipt, check_path, full_check, cwd)
    if not matched:
        recorded_argv = receipt.get("argv") if isinstance(receipt.get("argv"), list) else []
        recorded_covered = [item.get("path") for item in (receipt.get("coveredFiles") or [])
                            if isinstance(item, dict) and isinstance(item.get("path"), str)]
        problem = ("behavior %r names check %r, whose evidence receipt at %s parses, names "
                  "the current head and passes evidence.verify(), but the receipt's own "
                  "recorded run never names this check: %r appears in neither its argv (%s) "
                  "nor its coveredFiles (%s). A receipt proves only the run it recorded, and "
                  "this run recorded a different one"
                  % (behavior_label, check_path, full_receipt, check_path,
                     ", ".join(repr(a) for a in recorded_argv) or "(none)",
                     ", ".join(repr(c) for c in recorded_covered) or "(none)"))
        return "NO-DATA", problem, (problem,)

    # THE INTEGRATED HOSTILE REVIEW FINDING B, CLOSED HERE. Every check above
    # proves this receipt is genuine, current, internally consistent, and
    # unmistakably about `check_path`; none of them ever asked whether the
    # run it recorded actually SUCCEEDED. A receipt with `exitCode: 1` for a
    # check that failed cleared every condition above and reached
    # CHECK_IDENTIFIED anyway, reading "the check is identified" as "the
    # behavior holds". A receipt with no `exitCode` at all did too: absence
    # of a recorded exit status is not success. Both degrade to NO-DATA here,
    # naming the failing or missing run, the same way a stale or unverifiable
    # receipt already does; neither can reach the CHECK_IDENTIFIED arm.
    exit_code = receipt.get("exitCode")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        problem = ("behavior %r names check %r, whose evidence receipt at %s parses, names "
                  "the current head, passes evidence.verify() and names this check, but "
                  "records no usable exitCode (%r): absence of a recorded exit status is "
                  "not success, and a run with no exit code proves nothing about the "
                  "behavior it was pointed at"
                  % (behavior_label, check_path, full_receipt, exit_code))
        return "NO-DATA", problem, (problem,)
    if exit_code != 0:
        problem = ("behavior %r names check %r, whose evidence receipt at %s parses, names "
                  "the current head, passes evidence.verify() and names this check, but "
                  "that check's own recorded run FAILED (exitCode %r): a check that ran and "
                  "failed is not proof that the behavior it was pointed at holds, whatever "
                  "else about the receipt checks out"
                  % (behavior_label, check_path, full_receipt, exit_code))
        return "NO-DATA", problem, (problem,)

    return ("CHECK_IDENTIFIED",
           "behavior %r is covered by check %r, whose evidence receipt at %s is bound to the "
           "current head %s" % (behavior_label, check_path, full_receipt, current_head), ())


def evaluate_plan(entries, cwd):
    """(rows, empty_note) over a whole proof plan: one `(behavior, verdict,
    evidence, problems)` row per entry in `entries`, in order, plus the
    vacuous-truth guard the module docstring names.

    `rows` is `()` when `entries` is not a non-empty list or tuple, paired
    with `empty_note`, a NAMED sentence, never `None`, in that case: an empty
    behavior list (equally, an empty proof list; both names describe this
    one input) is its own finding, most severe, and is refused as evidence
    of coverage. When `entries` holds at least one item, `rows` carries every
    one of them (well-formed or not; a malformed entry's own row is
    NO-DATA, from `proof_verdict`, never dropped silently) and `empty_note`
    is `None`.

    The pairing itself is the guard: `rows == ()` and `empty_note is None`
    together would be the exact silent-pass shape this function exists to
    make impossible -- a caller folding `rows` with `all()` over zero items
    would read "nothing claimed" as "everything proven" for free, the way
    Python's own `all(())` already does. Checking `empty_note` first is
    unavoidable here, because it is the only field that is never `None` when
    `rows` is empty.
    """
    if not isinstance(entries, (list, tuple)) or not entries:
        return (), ("this proof plan claims zero behaviors: an empty behavior list (the same "
                    "list some callers will call a proof list) is its own finding, most "
                    "severe, and is never read as \"everything is covered\". A plan with "
                    "nothing in it has proven nothing")
    rows = tuple(
        (entry.get("behavior") if isinstance(entry, dict) else None,) + proof_verdict(entry, cwd)
        for entry in entries
    )
    return rows, None

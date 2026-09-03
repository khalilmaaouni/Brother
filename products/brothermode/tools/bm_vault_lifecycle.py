#!/usr/bin/env python3
"""The lifecycle contract: model output is a candidate, never truth on arrival.

NAMED lifecycle, not promotion, deliberately: tools/bm_vault_promote.py already
exists and is the nudge counter that suggests WHEN to distill. Two tools whose
names differ by a suffix is how the estate once ended up with two writer locks
in two formats, so the epistemic state machine gets its own unambiguous name.

WHY THIS EXISTS. Benchmark row D12 and settled decision 17 of the steering
directive: model-generated memory is a candidate, not truth. Measured
2026-08-29: the vault's status vocabulary is open, closed and standing, which is
a WORKFLOW axis, not an epistemic one. Nothing distinguishes a paragraph a
model wrote at 2am from a decision a person validated, once both are on disk.

THE STATES, on their own field because they answer a different question than
status: does anyone stand behind this?
  candidate     written, by anyone or anything; nobody has validated it
  under_review  a review is open on it (WBS row VB3-06); not yet a verdict
  validated     a named person checked it against its evidence
  canonical     validated AND promoted to the answer of record for its question
  rejected      checked and found wrong; kept, because a rejection is a lesson
  revoked       was canonical; a named person withdrew it (VB3-06)
  expired       was validated or canonical, and has outlived its horizon
                (VB3-06); a CLOCK reading, never a person's act

THE LEGAL MOVES, and only these:
  candidate    -> validated | rejected | under_review
  under_review -> validated | rejected
  validated    -> canonical | rejected | expired
  canonical    -> revoked | expired
  expired      -> under_review   (revalidation: a recorded transition, a
                                   fresh review, never an edit in place)
A candidate that jumps straight to canonical skipped the only step that makes
canonical mean anything, so the jump is a finding, not a shortcut, and it
stays illegal under VB3-06 exactly as it was under D12: no combination of the
new states opens a back door around it.

PROMOTION IS RECORDED OR IT DID NOT HAPPEN. Every state but candidate
requires promoted_by (a person) and promoted_at (a date) -- this already
covered validated, canonical and rejected before VB3-06, and covers
under_review, revoked and expired the same way with no extra code, because
the check below reads "anything but candidate", not a fixed list. The row's
own observable says "promotion is recorded with who and when", and a state
above candidate carrying no record is exactly the auto-promotion this
contract forbids, so it is a named finding. expired is the one exception in
spirit though not in code: cmd_promote in tools/bm_vault_promotions.py would
still need a --by to WRITE it, but effective_state() below computes it
straight from the clock and promoted_at, no human act required to READ a
state as expired.

APPROVAL RECORDS (VB3-06). validated, canonical and revoked are APPROVALS: a
named human act, not just a state label. make_approval() builds the record
promoted_by/promoted_at alone never captured: approver, role, reason,
policy_version, and the artifact_hash of the note's own content at the
moment of approval, so a later edit to the note is visible against what was
actually approved. The record carries its own record_hash over exactly
those five fields; verify_approval() recomputes it and compares. Mutate any
one covered field by hand after the fact (change the reason, swap the role,
even flip one character of the stored artifact_hash) and verify_approval
returns False: the record does not merely disagree with the note anymore,
it disagrees with ITSELF. This is a detective control, the same posture
bm_vault_principals.py already takes on its own registry: a JSON file with a
hash inside it does not stop a determined hand edit that also recomputes the
hash, it catches a careless one.

SEPARATION OF DUTIES (VB3-06). check_separation_of_duties() answers one
question: may this approver approve this candidate. Policy is a MODULE-LEVEL
flag, SEPARATION_OF_DUTIES_ENFORCED, the same declared-table posture
tools/bm_vault_survivorship.py's PER_ATTRIBUTE_ORDER already takes for its
own policy layer: a plain constant an operator can flip, not a second
vocabulary or a config file to go missing. Default True: the human-approved
learning promise on the public Vault page holds by default, and an
author-approves attempt is REFUSED, naming the rule, never silently
downgraded to a warning. A caller that still has today's single-person,
single-machine reality (the same one bm_vault_principals.py's own TRUST
BOUNDARY section names) passes enforce=False explicitly to allow it for
one check, rather than the module default quietly deciding that for them.

EXPIRY AND REVALIDATION (VB3-06). effective_state() reads validated and
canonical against a clock: past EXPIRY_HORIZON_DAYS since promoted_at, the
state reads "expired", never silently current, the same "absence/age is not
a measurement you get to ignore" posture tools/bm_vault_staleness.py already
takes for verified_at. A missing or malformed promoted_at is never treated
as expired -- that would invent a fact nobody recorded, exactly the trap
bm_vault_staleness.py's own docstring names. Revalidation is the legal
expired -> under_review move above: a fresh review recorded as a new
transition, never an edit of the old one.

WHAT AN ABSENT DECLARATION MEANS, stated rather than smuggled. 812 existing
notes predate this contract. They read as "legacy": included in ordinary
retrieval exactly as today (the status quo is preserved, nothing breaks), and
EXCLUDED from any claim of canonical, because grandfathering them to validated
would grant truth automatically, which is the one thing D12 exists to prevent.
Legacy is counted and named so a migration can find it, never silently mapped
to any real state.

Exit 0 clean, 1 findings, 2 NO-DATA. Stdlib only, writes nothing anywhere.
"""
import argparse
import datetime
import hashlib
import os
import re
import sys

STATES = ("candidate", "validated", "canonical", "rejected",
          "under_review", "revoked", "expired")
LEGAL = {("candidate", "validated"), ("candidate", "rejected"),
         ("candidate", "under_review"),
         ("under_review", "validated"), ("under_review", "rejected"),
         ("validated", "canonical"), ("validated", "rejected"),
         ("validated", "expired"),
         ("canonical", "revoked"), ("canonical", "expired"),
         ("expired", "under_review")}
_F = {k: re.compile(r"^%s:\s*(.+?)\s*$" % k, re.M)
      for k in ("promotion", "promoted_by", "promoted_at")}
SKIP_DIRS = {".git", ".trash", ".obsidian"}

# VB3-06 approval records: the five fields make_approval/verify_approval
# cover with the record's own integrity hash. Order is fixed (the hash is
# order-sensitive) but arbitrary otherwise.
APPROVAL_HASH_FIELDS = ("approver", "role", "reason", "policy_version",
                        "artifact_hash")

# VB3-06 separation of duties: a plain module-level policy flag, the same
# declared-config posture tools/bm_vault_survivorship.py's own
# PER_ATTRIBUTE_ORDER takes. True enforces the rule by default; a caller
# still on today's single-person reality passes enforce=False to
# check_separation_of_duties directly, to turn the rule off for one check.
SEPARATION_OF_DUTIES_ENFORCED = True

# VB3-06 expiry: days a validated/canonical state stays current after
# promoted_at before effective_state() reads it as expired. One horizon for
# both states, on purpose (ponytail: no evidence yet that they should decay
# at different rates); mirrors tools/bm_vault_staleness.py's own "decision"
# class horizon, since a lifecycle state is exactly that kind of claim.
EXPIRY_HORIZON_DAYS = 180


def _frontmatter(text):
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def read_promotion(text):
    """(state, record, problems). state is one of STATES, or "legacy" when the
    note declares nothing, or None when it declares something unrankable."""
    block = _frontmatter(text)
    m = _F["promotion"].search(block)
    if not m:
        return "legacy", {}, []
    value = m.group(1).strip().strip('"').strip("'")
    problems = []
    if value not in STATES:
        return None, {}, ["unknown promotion %r, not in %s" % (value, "/".join(STATES))]
    record = {}
    for k in ("promoted_by", "promoted_at"):
        mm = _F[k].search(block)
        if mm:
            record[k] = mm.group(1).strip().strip('"').strip("'")
    if value != "candidate":
        for k in ("promoted_by", "promoted_at"):
            if not record.get(k):
                problems.append("%s without %s: a promotion that is not recorded "
                                "did not happen" % (value, k))
        if record.get("promoted_at"):
            try:
                datetime.date.fromisoformat(record["promoted_at"])
            except ValueError:
                problems.append("promoted_at %r is not a date" % record["promoted_at"])
    return value, record, problems


def legal_transition(old, new):
    """True only for the moves the contract names. Everything else, including
    the candidate-to-canonical jump and any move out of a terminal rejection,
    is False rather than tolerated."""
    return (old, new) in LEGAL


def counts_as_canonical(state, problems):
    """The retrieval-facing question. Only a clean canonical counts: a canonical
    with findings is a claim missing its record, and legacy is legacy."""
    return state == "canonical" and not problems


def artifact_hash(content):
    """sha256 hex digest of a note's content at approval time (VB3-06). The
    caller passes whatever text was actually reviewed; this function has no
    idea what a "note" is, on purpose, so it never touches disk."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _approval_record_hash(record):
    """The integrity hash covering APPROVAL_HASH_FIELDS only, in that fixed
    order. Missing fields hash as empty string rather than raising, so a
    partially-built record still gets a stable (and therefore checkable)
    hash instead of an exception mid-approval."""
    payload = "\x1f".join(str(record.get(k, "")) for k in APPROVAL_HASH_FIELDS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_approval(approver, role, reason, policy_version, note_content):
    """A new approval record (VB3-06): approver, role, reason, policy_version
    and the artifact_hash of note_content as given right now, plus the
    record's own record_hash over those five fields. This is the record;
    nothing here writes it anywhere or checks legality or separation of
    duties, those are the caller's job (see check_separation_of_duties and
    tools/bm_vault_promotions.py's cmd_promote for where a write would
    plug in)."""
    record = {
        "approver": approver,
        "role": role,
        "reason": reason,
        "policy_version": policy_version,
        "artifact_hash": artifact_hash(note_content),
    }
    record["record_hash"] = _approval_record_hash(record)
    return record


def verify_approval(record):
    """True only when record_hash still matches a fresh recomputation over
    APPROVAL_HASH_FIELDS (VB3-06). False for anything else: no record_hash
    at all, a record_hash that does not match (any one of the five fields
    was mutated after the fact, or record_hash itself was hand-edited), or a
    record that is not even a dict. Never raises on garbage input, mirroring
    read_promotion's own "a problem is a finding, not a crash" posture."""
    if not isinstance(record, dict):
        return False
    stored = record.get("record_hash")
    if not stored:
        return False
    return stored == _approval_record_hash(record)


def check_separation_of_duties(author, approver, enforce=None):
    """None when the approval is allowed; a refusal string naming the rule
    when it is not (VB3-06). enforce defaults to the module policy flag
    SEPARATION_OF_DUTIES_ENFORCED; a caller with its own policy source
    passes True/False explicitly to override it for one check. Comparison
    is casefold-and-strip, not exact string equality: "Khalil" and "khalil "
    are the same author under this rule. Either identity missing (nobody
    recorded who wrote it, or who is approving) is never treated as a
    match: an unknown author cannot be REFUSED for colliding with an
    approver nobody can compare against."""
    if enforce is None:
        enforce = SEPARATION_OF_DUTIES_ENFORCED
    if not enforce:
        return None
    if not author or not approver:
        return None
    if author.strip().casefold() == approver.strip().casefold():
        return ("separation of duties: %s cannot approve a candidate they "
                "authored" % approver)
    return None


def effective_state(state, promoted_at, today=None, horizon_days=None):
    """The state as of `today` (VB3-06): unchanged unless state is validated
    or canonical AND promoted_at is older than horizon_days, in which case
    the effective reading is "expired". promoted_at accepts an ISO date
    string or a datetime.date. A missing or unparseable promoted_at is
    NEVER read as expired -- that would be inventing a fact nobody
    recorded, the same trap tools/bm_vault_staleness.py's own docstring
    names for an absent verified_at. Revalidation is not this function's
    job: it only reads the clock; the expired -> under_review move itself
    is recorded the same way every other transition is."""
    if state not in ("validated", "canonical"):
        return state
    if not promoted_at:
        return state
    if isinstance(promoted_at, str):
        try:
            promoted_at = datetime.date.fromisoformat(promoted_at.strip())
        except ValueError:
            return state
    today = today or datetime.date.today()
    horizon = EXPIRY_HORIZON_DAYS if horizon_days is None else horizon_days
    age = (today - promoted_at).days
    return "expired" if age > horizon else state


def walk(vault):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def cmd_check(vault):
    tallies = {s: 0 for s in STATES}
    tallies["legacy"] = 0
    findings = []
    total = 0
    for path in walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            findings.append((rel, "could not be read (%s)" % exc))
            continue
        total += 1
        state, _record, problems = read_promotion(text)
        if state is None:
            findings.append((rel, problems[0]))
            continue
        for p in problems:
            findings.append((rel, p))
        tallies[state] += 1
    print("vault: %s" % vault)
    print("notes: %d" % total)
    for s in ("canonical", "validated", "under_review", "candidate",
              "rejected", "revoked", "expired", "legacy"):
        note = "" if s != "legacy" else "  (predates the contract; in retrieval, never canonical)"
        print("  %-12s %d%s" % (s, tallies[s], note))
    if findings:
        print("FINDINGS, each named, never silently a state: %d" % len(findings))
        for rel, p in findings:
            print("  %s: %s" % (rel, p))
    return 1 if findings else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_lifecycle: NO-DATA, no readable vault at %r" % args.vault,
              file=sys.stderr)
        return 2
    return cmd_check(args.vault)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""bm_vault_assertions: subject-predicate assertions and scoped resolutions,
institutional truth above the note layer. WBS row VB3-05.

WHY THIS EXISTS. The row's own sentence: notes carry claims, authority and
temporal fields, but a subject-predicate ASSERTION with its own lifecycle,
and a CanonicalResolution scoped by business unit and time with recorded
approvals, is what lets two conflicting facts hold in different scopes
without either being deleted. bm_vault_provenance (claim-level records),
bm_vault_entity/bm_vault_crosswalk (subjects are entities, never filenames),
bm_vault_authority (the three-level comparator), bm_vault_temporal/
bm_vault_asof (the five bi-temporal fields and as-of queries),
bm_vault_lifecycle (the extended state machine and approval records) and
bm_vault_survivorship (the conflict-ranking doctrine) are the seeds this
module is the structure over. Nothing here re-decides any of those
contracts: every vocabulary and every hash scheme is READ from the sibling
module, never re-implemented, so this file's behaviour shifts the moment a
sibling's does.

THE SHAPE ON DISK. Two append-only JSON-lines files under the vault, never
vault notes, and never mutated in place:

  99-System/assertions.jsonl   one line per assertion:
      id            "as-<16 hex>", minted, opaque, never reused
      subject       an ENTITY id (bm_vault_ids' "n-<16 hex>" format, the
                    entity's own declared id: field) -- never a filename or
                    a note stem. Referencing a filename is exactly the D05
                    failure this module must not reintroduce one layer up.
      predicate     free text, what is being said about the subject
      value         free text, what is claimed
      authority     one of bm_vault_authority.LEVELS
      valid_from / valid_to / observed_at / ingested_at / verified_at
                    the five bm_vault_temporal.FIELDS, all optional, ISO
                    dates, absence invents nothing (same doctrine)
      lifecycle     one of bm_vault_lifecycle.STATES; assertions ride that
                    SAME state machine, never a second one
      source_locator  where this claim came from (a path, a URL, free text)
      supersedes    optional: the id of an assertion this one corrects.
                    A CORRECTION IS A NEW ASSERTION, never an edit: the old
                    record stays on disk, unreadable as "current" only once
                    something (a caller, a later lifecycle transition) also
                    retires it. This module does not infer retirement from
                    supersedes alone -- see CEILING below.
      recorded_at   when this line was appended (bookkeeping, not a claim)

  99-System/resolutions.jsonl   one line per CanonicalResolution:
      id            "cr-<16 hex>"
      subject / predicate  same meaning as above
      winner        the id of the assertion this resolution names as truth
      scope         a business-unit string; truth is scoped BY this string
      valid_from / valid_to  when this resolution itself is in force
      approval      a bm_vault_lifecycle.make_approval() record: approver,
                    role, reason, policy_version, artifact_hash, record_hash
                    -- NOT a second approval format, the same VB3-06 shape.
                    artifact_hash covers this resolution's own content
                    (subject/predicate/winner/scope/valid_from/valid_to),
                    exactly the way that field covers a note's content
                    there: edit any covered field after approval and the
                    hash no longer matches what was actually approved.
      recorded_at   bookkeeping

WHAT "LIVE" MEANS FOR AN ASSERTION, here and only here: lifecycle is not
"rejected" or "revoked". candidate/validated/canonical/under_review/expired
all still count -- an unreviewed or aged claim is still a claim on the
table, and D12's own doctrine is that a state is a fact about SCRUTINY, not
about whether the claim still exists.

CEILING (stated, not papered over). supersedes is an audit trail, not an
automatic retirement: this module never infers that the superseded
assertion stopped being "live" just because a newer one names it. A caller
that wants the old claim out of the live set transitions ITS OWN lifecycle
(to revoked or rejected) through bm_vault_lifecycle's own legal moves,
exactly as VB3-06 already requires for any other retirement. Good enough
for the corpus today; if silent supersession without a lifecycle move turns
out to be common, this is the seam to extend.

CURRENT SCOPED TRUTH, `truth --subject S --predicate P [--scope SCOPE]
[--as-of DATE]`. SCOPE defaults to "global" (a reserved, ordinary scope
string, not magic) so an unscoped query is a query against one well-defined
scope rather than an ill-defined "any scope wins" that would depend on
insertion order. Algorithm, in order:
  1. Collect every LIVE assertion for (subject, predicate). Zero is NO-DATA.
  2. Collect every resolution for (subject, predicate, scope). Pick the one
     "in force" as of DATE (default today): the entry with the latest
     valid_from that is <= DATE and whose valid_to (if any) is >= DATE,
     both ends inclusive, matching bm_vault_crosswalk's own interval
     convention. A resolution with a later valid_from than DATE is simply
     not chosen, which is what makes an AS-OF query into the past return
     a PRIOR resolution once a newer one has landed, without deleting or
     closing the old one's window.
  3. If a resolution is in force, verify its approval two ways: internal
     consistency (bm_vault_lifecycle.verify_approval, catches a hand-edited
     approval field) AND content match (recomputing artifact_hash over the
     resolution's own current fields, catches a hand-edited winner/scope/
     valid_from/valid_to after the fact). Either failure INVALIDATES the
     resolution: truth falls back to the comparator below, and the fallback
     is always named in the output, never silent.
  4. Absent a valid in-force resolution, the winner is the live assertion
     with the highest bm_vault_authority rank. An unrankable authority
     value is excluded from the comparator with a named reason, never
     silently ranked. A true tie is reported UNRESOLVED, exit 1, naming
     both -- exactly the posture bm_vault_survivorship's own table takes at
     a tie, because guessing a winner is worse than admitting neither side
     broke it.
  5. EVERY distinct value among the live assertions is printed with its own
     authority and id, always, whether or not a winner was found: the
     conflict is exposed, never collapsed to the winning side alone.

`check --vault V` validates both stores: schema (every required field
present, authority/lifecycle values in their vocabularies, temporal dates
parse), every assertion's subject resolves to a real entity (a note whose
own declared id: matches AND which declares entity: in bm_vault_entity's
vocabulary -- an id that resolves to an ordinary document, or to nothing,
is a named finding, never silently accepted), every resolution's approval
verifies both ways above, and every resolution's winner resolves to an
assertion actually in the assertions store. NO-DATA (exit 2) when either
store file is simply absent, naming its path -- an empty-but-present file
(zero lines) is a legitimate, checked state, not NO-DATA.

Exit codes: mint-assertion/mint-resolution 0 on success. truth 0 a winner
was found (with or without an exposed conflict), 1 UNRESOLVED tie, 2
NO-DATA (missing assertions store, or zero live assertions for the pair).
check 0 clean, 1 findings, 2 NO-DATA (either store file absent).

Python 3.9, standard library only. Append-only: the only writes here are a
single os.O_APPEND line write per mint call. No em or en dashes anywhere in
this file, its comments, or its output.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ASSERTIONS_SUBDIR = "99-System"
ASSERTIONS_FILENAME = "assertions.jsonl"
RESOLUTIONS_FILENAME = "resolutions.jsonl"
ASSERTION_ID_PREFIX = "as-"
RESOLUTION_ID_PREFIX = "cr-"
DEFAULT_SCOPE = "global"
# Assertions carrying either of these two lifecycle states are excluded from
# "live" (see the CEILING note in the module docstring for what this does
# NOT do: it never follows supersedes edges on its own).
RETIRED_LIFECYCLE_STATES = ("rejected", "revoked")
# The six fields a resolution's approval attests to, fixed order (the hash
# is order-sensitive). Mirrors bm_vault_lifecycle.APPROVAL_HASH_FIELDS's own
# "fixed order, arbitrary otherwise" posture, one layer up.
RESOLUTION_CONTENT_FIELDS = ("subject", "predicate", "winner", "scope",
                             "valid_from", "valid_to")


def _load(filename, modname):
    """Dynamic import by path, the same pattern bm_vault_asof.py already
    uses to read bm_vault_temporal.py and bm_vault_graph.py: load a sibling
    contract module without relying on tools/ being on sys.path, and
    without re-deciding anything the sibling already owns."""
    spec = importlib.util.spec_from_file_location(
        modname, os.path.join(_TOOLS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _siblings():
    """The five contract modules this file rides on top of, loaded once per
    call site. Returns a dict rather than a tuple so call sites read by
    name, not position."""
    return {
        "lifecycle": _load("bm_vault_lifecycle.py", "bm_vault_lifecycle"),
        "authority": _load("bm_vault_authority.py", "bm_vault_authority"),
        "temporal": _load("bm_vault_temporal.py", "bm_vault_temporal"),
        "ids": _load("bm_vault_ids.py", "bm_vault_ids"),
        "entity": _load("bm_vault_entity.py", "bm_vault_entity"),
    }


def assertions_path(vault):
    return os.path.join(vault, ASSERTIONS_SUBDIR, ASSERTIONS_FILENAME)


def resolutions_path(vault):
    return os.path.join(vault, ASSERTIONS_SUBDIR, RESOLUTIONS_FILENAME)


def _mint_id(prefix, existing):
    """A fresh id, checked against ids already in use. Duplicated from
    bm_vault_ids.mint rather than imported (that module hardcodes its own
    "n-" prefix): this family's own stated convention, see
    bm_vault_provenance.py's docstring, is that every sibling reads or mints
    its own id shape rather than sharing one function two prefixes deep."""
    taken = set(existing or ())
    for _ in range(1000):
        candidate = prefix + uuid.uuid4().hex[:16]
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not mint an unused id in 1000 attempts")


def _read_records(path):
    """[record, ...] in file order, or None when the file does not exist or
    cannot be opened -- the NO-DATA case, distinct from a present-but-empty
    file (which returns [], a legitimate zero-record store). A malformed
    JSON line is skipped with a stderr warning, matching bm_vault_cite's own
    treatment: one corrupt line must not hide every other record."""
    if not os.path.isfile(path):
        return None
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError as exc:
                    sys.stderr.write(
                        "bm_vault_assertions: skipping malformed line %d in "
                        "%s (%s)\n" % (i, path, exc))
    except OSError:  # sbe: allow-silent per docstring: unopenable file is the NO-DATA case, distinct from empty
        return None
    return records


def _append_record(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, sort_keys=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError:  # sbe: allow-silent unparseable date reads as absent, matching the "if not raw" guard above
        return None


def resolution_content(record):
    """The canonical string a resolution's approval.artifact_hash covers:
    exactly RESOLUTION_CONTENT_FIELDS, in that fixed order. Recomputed at
    check/truth time and compared against the stored artifact_hash, so an
    edit to any covered field after approval is visible against what was
    actually approved -- the same posture bm_vault_lifecycle.make_approval
    takes for a note's content, one layer up."""
    return "\x1f".join(str(record.get(f, "") if record.get(f) is not None else "")
                        for f in RESOLUTION_CONTENT_FIELDS)


def approval_status(resolution, lifecycle_mod):
    """(valid, reason). False for either tamper vector: a hand-edited
    approval field (caught by verify_approval's own record_hash check) or a
    hand-edited resolution field the approval never re-signed (caught by
    recomputing artifact_hash over the resolution's CURRENT content)."""
    approval = resolution.get("approval")
    if not lifecycle_mod.verify_approval(approval):
        return False, ("approval record failed internal verification "
                       "(record_hash mismatch): the approval itself was edited "
                       "after being recorded")
    expected = lifecycle_mod.artifact_hash(resolution_content(resolution))
    if approval.get("artifact_hash") != expected:
        return False, ("resolution content does not match its approved "
                       "artifact_hash: the resolution was edited after approval "
                       "without a fresh approval")
    return True, "approval verified"


# ---------------------------------------------------------------- mint ----

def cmd_mint_assertion(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable vault at %r\n"
                         % args.vault)
        return 2
    sib = _siblings()
    if args.authority not in sib["authority"].LEVELS:
        sys.stderr.write("bm_vault_assertions: --authority %r not in %s\n"
                         % (args.authority, "/".join(sib["authority"].LEVELS)))
        return 2
    if args.lifecycle not in sib["lifecycle"].STATES:
        sys.stderr.write("bm_vault_assertions: --lifecycle %r not in %s\n"
                         % (args.lifecycle, "/".join(sib["lifecycle"].STATES)))
        return 2
    path = assertions_path(args.vault)
    existing = _read_records(path) or []
    new_id = _mint_id(ASSERTION_ID_PREFIX, {r.get("id") for r in existing})
    record = {
        "id": new_id,
        "subject": args.subject,
        "predicate": args.predicate,
        "value": args.value,
        "authority": args.authority,
        "lifecycle": args.lifecycle,
        "source_locator": args.source,
        "recorded_at": datetime.date.today().isoformat(),
    }
    for field in sib["temporal"].FIELDS:
        cli_val = getattr(args, field, None)
        if cli_val:
            record[field] = cli_val
    if args.supersedes:
        record["supersedes"] = args.supersedes
    _append_record(path, record)
    print("minted %s -> %s" % (new_id, path))
    return 0


def cmd_mint_resolution(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable vault at %r\n"
                         % args.vault)
        return 2
    sib = _siblings()
    if _parse_date(args.valid_from) is None:
        sys.stderr.write("bm_vault_assertions: --valid-from must be an ISO date\n")
        return 2
    if args.valid_to and _parse_date(args.valid_to) is None:
        sys.stderr.write("bm_vault_assertions: --valid-to must be an ISO date\n")
        return 2
    path = resolutions_path(args.vault)
    existing = _read_records(path) or []
    new_id = _mint_id(RESOLUTION_ID_PREFIX, {r.get("id") for r in existing})
    fields = {
        "subject": args.subject, "predicate": args.predicate,
        "winner": args.winner, "scope": args.scope,
        "valid_from": args.valid_from, "valid_to": args.valid_to,
    }
    content = resolution_content(fields)
    approval = sib["lifecycle"].make_approval(
        args.approver, args.role, args.reason, args.policy_version, content)
    record = dict(fields)
    record["id"] = new_id
    record["approval"] = approval
    record["recorded_at"] = datetime.date.today().isoformat()
    _append_record(path, record)
    print("minted %s -> %s" % (new_id, path))
    return 0


# --------------------------------------------------------------- truth ----

def live_assertions(records, subject, predicate):
    return [r for r in records
            if r.get("subject") == subject and r.get("predicate") == predicate
            and r.get("lifecycle") not in RETIRED_LIFECYCLE_STATES]


def resolution_in_force(records, subject, predicate, scope, when):
    """The resolution with the latest valid_from that is <= when and whose
    valid_to (if any) is >= when, both ends inclusive (matches
    bm_vault_crosswalk's own interval convention). None when no resolution
    for this (subject, predicate, scope) covers `when`."""
    candidates = []
    for r in records:
        if (r.get("subject") != subject or r.get("predicate") != predicate
                or r.get("scope") != scope):
            continue
        vf = _parse_date(r.get("valid_from"))
        if vf is None or vf > when:
            continue
        vt = _parse_date(r.get("valid_to")) if r.get("valid_to") else None
        if vt is not None and vt < when:
            continue
        candidates.append((vf, r.get("recorded_at", ""), r))
    if not candidates:
        return None
    candidates.sort(key=lambda triple: (triple[0], triple[1]))
    return candidates[-1][2]


def _conflict_lines(assertions):
    distinct = {}
    for a in assertions:
        distinct.setdefault(a.get("value"), []).append(a)
    lines = []
    for value in sorted(distinct, key=lambda v: str(v)):
        for a in distinct[value]:
            lines.append("  value=%r authority=%s id=%s lifecycle=%s"
                         % (value, a.get("authority"), a.get("id"), a.get("lifecycle")))
    return lines, len(distinct)


def cmd_truth(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable vault at %r\n"
                         % args.vault)
        return 2
    sib = _siblings()
    a_records = _read_records(assertions_path(args.vault))
    if a_records is None:
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable assertions "
                         "store at %r\n" % assertions_path(args.vault))
        return 2
    live = live_assertions(a_records, args.subject, args.predicate)
    if not live:
        sys.stderr.write("bm_vault_assertions: NO-DATA, no live assertion for "
                         "subject=%r predicate=%r\n" % (args.subject, args.predicate))
        return 2
    r_records = _read_records(resolutions_path(args.vault)) or []
    when = _parse_date(args.as_of) if args.as_of else datetime.date.today()
    if args.as_of and when is None:
        sys.stderr.write("bm_vault_assertions: --as-of must be an ISO date\n")
        return 2

    print("subject=%s predicate=%s scope=%s as_of=%s"
          % (args.subject, args.predicate, args.scope, when))

    winner = None
    via = None
    resolution = resolution_in_force(r_records, args.subject, args.predicate,
                                     args.scope, when)
    if resolution is not None:
        valid, reason = approval_status(resolution, sib["lifecycle"])
        if not valid:
            print("APPROVAL INVALID for resolution %s: %s; falling back to the "
                 "authority comparator" % (resolution.get("id"), reason))
        else:
            winner = next((a for a in live if a.get("id") == resolution.get("winner")),
                         None)
            if winner is None:
                print("resolution %s names winner %s, not among the live "
                     "assertions; falling back to the authority comparator"
                     % (resolution.get("id"), resolution.get("winner")))
            else:
                via = ("resolution %s (scope=%s, approved by %s)"
                      % (resolution.get("id"), args.scope,
                         resolution.get("approval", {}).get("approver")))

    if winner is None:
        ranked = []
        for a in live:
            level = a.get("authority")
            if level not in sib["authority"].LEVELS:
                print("  excluded from the comparator: %s has unrankable "
                     "authority %r" % (a.get("id"), level))
                continue
            ranked.append((sib["authority"].rank_key(level, 0), a))
        if ranked:
            best_rank = max(r[0] for r in ranked)
            top = [a for rank, a in ranked if rank == best_rank]
            if len(top) > 1:
                print("UNRESOLVED: tie at authority %s between ids %s"
                     % (top[0].get("authority"), ", ".join(a.get("id") for a in top)))
                lines, n = _conflict_lines(live)
                print("CONFLICT (%d distinct value(s)):" % n)
                for line in lines:
                    print(line)
                return 1
            winner = top[0]
            via = "the authority comparator"

    if winner is not None:
        print("WINNER value=%r authority=%s id=%s via %s"
             % (winner.get("value"), winner.get("authority"), winner.get("id"), via))
    lines, n = _conflict_lines(live)
    if n > 1:
        print("CONFLICT (%d distinct value(s)), exposed rather than hidden "
             "behind the winner:" % n)
    else:
        print("no conflict, one value on record:")
    for line in lines:
        print(line)
    return 0 if winner is not None else 1


# --------------------------------------------------------------- check ----

def _valid_entity_ids(vault, ids_mod, entity_mod):
    """The set of note ids that are genuine entities: resolvable via
    bm_vault_ids' own id index AND declaring entity: in entity_mod's own
    vocabulary. An id that resolves to an ordinary document, or to nothing
    at all, is not in this set."""
    by_id, _missing, _dupes = ids_mod.index(vault)
    valid = set()
    for nid, relpath in by_id.items():
        try:
            with open(os.path.join(vault, relpath), encoding="utf-8",
                     errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent unreadable note is skipped, never counted as a valid entity id
            continue
        block = entity_mod._frontmatter(text)
        etype = entity_mod._field(block, "entity") if block is not None else None
        if etype and etype.strip().strip('"').strip("'") in entity_mod.ENTITY_TYPES:
            valid.add(nid)
    return valid


def _check_assertion_schema(record, sib):
    problems = []
    for field in ("id", "subject", "predicate", "value", "authority",
                  "lifecycle", "source_locator"):
        if not record.get(field):
            problems.append("missing required field %r" % field)
    if record.get("authority") not in sib["authority"].LEVELS and record.get("authority"):
        problems.append("unknown authority %r" % record.get("authority"))
    if record.get("lifecycle") not in sib["lifecycle"].STATES and record.get("lifecycle"):
        problems.append("unknown lifecycle state %r" % record.get("lifecycle"))
    for field in sib["temporal"].FIELDS:
        if record.get(field) and _parse_date(record.get(field)) is None:
            problems.append("%s %r is not an ISO date" % (field, record.get(field)))
    return problems


def cmd_check(args):
    if not args.vault or not os.path.isdir(args.vault):
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable vault at %r\n"
                         % args.vault)
        return 2
    a_path, r_path = assertions_path(args.vault), resolutions_path(args.vault)
    a_records, r_records = _read_records(a_path), _read_records(r_path)
    if a_records is None:
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable assertions "
                         "store at %r\n" % a_path)
        return 2
    if r_records is None:
        sys.stderr.write("bm_vault_assertions: NO-DATA, no readable resolutions "
                         "store at %r\n" % r_path)
        return 2

    sib = _siblings()
    valid_entities = _valid_entity_ids(args.vault, sib["ids"], sib["entity"])
    findings = []
    assertion_ids = set()
    for i, rec in enumerate(a_records):
        for problem in _check_assertion_schema(rec, sib):
            findings.append("assertions.jsonl line %d (%s): %s"
                            % (i + 1, rec.get("id", "?"), problem))
        subject = rec.get("subject")
        if subject and subject not in valid_entities:
            findings.append("assertions.jsonl line %d (%s): unresolved entity "
                            "reference, subject %r is not a declared entity id "
                            "in this vault" % (i + 1, rec.get("id", "?"), subject))
        if rec.get("id"):
            assertion_ids.add(rec.get("id"))
    for i, rec in enumerate(a_records):
        supersedes = rec.get("supersedes")
        if supersedes and supersedes not in assertion_ids:
            findings.append("assertions.jsonl line %d (%s): supersedes %r "
                            "resolves to no assertion in this store"
                            % (i + 1, rec.get("id", "?"), supersedes))

    for i, rec in enumerate(r_records):
        for field in ("id", "subject", "predicate", "winner", "scope",
                     "valid_from", "approval"):
            if not rec.get(field):
                findings.append("resolutions.jsonl line %d (%s): missing "
                                "required field %r" % (i + 1, rec.get("id", "?"), field))
        if rec.get("valid_from") and _parse_date(rec.get("valid_from")) is None:
            findings.append("resolutions.jsonl line %d (%s): valid_from %r is "
                            "not an ISO date" % (i + 1, rec.get("id", "?"),
                                                 rec.get("valid_from")))
        if rec.get("valid_to") and _parse_date(rec.get("valid_to")) is None:
            findings.append("resolutions.jsonl line %d (%s): valid_to %r is "
                            "not an ISO date" % (i + 1, rec.get("id", "?"),
                                                 rec.get("valid_to")))
        if rec.get("approval"):
            valid, reason = approval_status(rec, sib["lifecycle"])
            if not valid:
                findings.append("resolutions.jsonl line %d (%s): %s"
                                % (i + 1, rec.get("id", "?"), reason))
        winner = rec.get("winner")
        if winner and winner not in assertion_ids:
            findings.append("resolutions.jsonl line %d (%s): winner %r "
                            "resolves to no assertion in the assertions store"
                            % (i + 1, rec.get("id", "?"), winner))

    print("vault: %s" % args.vault)
    print("assertions: %d  resolutions: %d" % (len(a_records), len(r_records)))
    if findings:
        print("FINDINGS, each named, never silently accepted: %d" % len(findings))
        for f in findings:
            print("  %s" % f)
    return 1 if findings else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")

    p_ma = sub.add_parser("mint-assertion")
    p_ma.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_ma.add_argument("--subject", required=True)
    p_ma.add_argument("--predicate", required=True)
    p_ma.add_argument("--value", required=True)
    p_ma.add_argument("--authority", required=True)
    p_ma.add_argument("--lifecycle", default="candidate")
    p_ma.add_argument("--source", required=True, help="source_locator")
    p_ma.add_argument("--valid-from", dest="valid_from")
    p_ma.add_argument("--valid-to", dest="valid_to")
    p_ma.add_argument("--observed-at", dest="observed_at")
    p_ma.add_argument("--ingested-at", dest="ingested_at")
    p_ma.add_argument("--verified-at", dest="verified_at")
    p_ma.add_argument("--supersedes")

    p_mr = sub.add_parser("mint-resolution")
    p_mr.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_mr.add_argument("--subject", required=True)
    p_mr.add_argument("--predicate", required=True)
    p_mr.add_argument("--winner", required=True)
    p_mr.add_argument("--scope", required=True)
    p_mr.add_argument("--valid-from", dest="valid_from", required=True)
    p_mr.add_argument("--valid-to", dest="valid_to")
    p_mr.add_argument("--approver", required=True)
    p_mr.add_argument("--role", required=True)
    p_mr.add_argument("--reason", required=True)
    p_mr.add_argument("--policy-version", dest="policy_version", required=True)

    p_t = sub.add_parser("truth")
    p_t.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    p_t.add_argument("--subject", required=True)
    p_t.add_argument("--predicate", required=True)
    p_t.add_argument("--scope", default=DEFAULT_SCOPE)
    p_t.add_argument("--as-of", dest="as_of")

    p_c = sub.add_parser("check")
    p_c.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))

    args = ap.parse_args(argv)
    if args.command == "mint-assertion":
        return cmd_mint_assertion(args)
    if args.command == "mint-resolution":
        return cmd_mint_resolution(args)
    if args.command == "truth":
        return cmd_truth(args)
    if args.command == "check":
        return cmd_check(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

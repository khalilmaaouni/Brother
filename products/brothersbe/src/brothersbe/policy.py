"""Prove that evidence SHOULD exist, then say whether it does.

The defect this exists for, in one sentence: this project is strong at rejecting
broken evidence and was weak at proving evidence was owed, so a gate could
report NO-DATA, a receipt directory could be left empty, a dossier input could
be omitted, and a strict run then completed without ever proving the change was
entitled to carry nothing.

    A valid receipt is checked carefully.
    A missing receipt may never be required.

That asymmetry is the whole bypass: an agent avoids a gate by avoiding the
declaration that activates it. This module removes the declaration from the
loop. `.sbe/policy.yml` says which paths owe which checks, tiers and approvals;
the requirement is derived from the DIFF, which the author cannot omit, and not
from an input somebody remembered to pass.

THE SIX STATES, AND WHY NO-DATA IS NOT ONE OF THEM.

    SATISFIED     the requirement is met by evidence this run actually read
    MISSING       required and absent. Nonzero exit, always
    INVALID       present and broken (unreadable, malformed, failed verify)
    STALE         present and bound to a different head commit
    UNPROTECTED   present and valid, but only locally attested, where the
                  policy requires protected evidence
    NOT-REQUIRED  no rule matched this change

NO-DATA used to carry two opposite meanings here: "nothing was required" and
"what was required is not there". One of those is a clean bill of health and the
other is the failure this file exists to catch, and a single word for both is
how the failure kept reading as the clean one. So NO-DATA is not in the
vocabulary of this module at all, and a test asserts it never appears in a
requirement state.

THE TIER IS A FLOOR, NEVER A CEILING, and this is the second place that sentence
is enforced (`brothersbe.impact` is the first, over detectors rather than over
declared policy). `minimumTier` is the LOWEST tier a change touching those paths
may be classified at. This engine RAISES a declared tier that sits under the
floor. It lowers one only against a decision record carrying a protected
approval, and without that record a lowering is INVALID and blocks. A tier that
can be lowered by editing one number in an intake file is not a control.

WAIVED IS NEVER PASS. An exception is a decision somebody made, so it carries a
reason, an owner, an expiry and a protected approval, and it is rendered as its
own word. A waiver missing any of the four is REFUSED and the requirement keeps
its failing state. For the rules that declare `strictWaivers` (control plane,
production infrastructure, migration, money, personal and partner data) an
accepted waiver still blocks: it records the decision, it does not clear it.

WHICH CHECK RAN IS THE REGISTRY'S ANSWER, NOT THE CALLER'S. A required check is
satisfied only by a receipt whose `checkId` the evidence wrapper resolved out of
`.sbe/checks.yml`, so SATISFIED now means the registered command ran, with the
registered arguments, over the files that check covers, against runner bytes
that still hash the same. `checkKinds`, the field the CALLER supplies, clears
nothing: it is read only so a MISSING requirement can say "a receipt offered
this name through the caller-supplied field and could not be credited for it"
instead of sending an operator to look for a receipt sitting right there.

WHAT THIS STILL CANNOT PROVE, stated here rather than in a footnote: the
registry itself. An entry pointing an important-sounding id at a command that
checks nothing is a false registry, and what defends against that is
`.sbe/checks.yml` sitting in the control-plane rule above, so changing it owes
control-plane evidence and a protected approval. That is a review control, not a
computed one. Every report carries this limit in `limits`.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, and the YAML subset parser is this repository's own
(`brothersbe.program`), not a dependency.
"""
import datetime
import hashlib
import io
import json
import os
import re
import subprocess

from . import evidence as evidence_mod
from .impact import DiffUnavailable, added_lines, read_intake, resolve_range, DETECTORS
from .program import ProgramParseError, load_yaml_file

SCHEMA_VERSION = "1.0"

POLICY_REL = os.path.join(".sbe", "policy.yml")

#: The six per-requirement states. NO-DATA is deliberately absent: see the
#: module docstring. WAIVED is not a state a requirement is EVALUATED into, it
#: is what an accepted exception renames a failing one to, so it is listed apart.
STATES = ("SATISFIED", "MISSING", "INVALID", "STALE", "UNPROTECTED", "NOT-REQUIRED")
WAIVED = "WAIVED"

#: The states that block. Every one of them means the change is not entitled to
#: proceed on the evidence this run could read.
BLOCKING_STATES = ("MISSING", "INVALID", "STALE", "UNPROTECTED")

#: The trust level that counts as protected. Compared by EQUALITY against this
#: one name rather than by "is it not local", so a trust vocabulary that grows a
#: middle level (a CI-shaped claim nobody cryptographically verified) does not
#: silently start satisfying a protected requirement here.
#: BR-1013: there is no protected level in this release. The label that used
#: to fill this slot was obtainable by exporting an environment variable, so
#: it was removed rather than left standing above evidence that could not
#: carry it. A rule asking for protected evidence therefore matches nothing
#: and reports UNPROTECTED, naming the missing attestation, which is the
#: honest verdict until attestation ships.
PROTECTED_LEVEL = None

TIERS = ("T0", "T1", "T2", "T3")

#: Policy content signals, resolved to the detector in `brothersbe.impact` that
#: already owns the pattern. One table, two readers: a signal this file names
#: and `impact` does not is a policy PARSE ERROR, never a signal that quietly
#: matches nothing.
SIGNAL_DETECTORS = {
    "sql-ddl": "sql-ddl",
    "destructive-schema-change": "destructive-migration",
}


class PolicyUnreadable(Exception):
    """The policy could not be read or does not validate, so nothing was judged.

    Raised rather than returning an empty rule set, because an empty policy and
    an unreadable one produce the same evaluation and only one of them means
    this change owes nothing.
    """


# ---------------------------------------------------------------------------
# The policy file
# ---------------------------------------------------------------------------

def _require_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise PolicyUnreadable("%s must be a non-empty string, got %r" % (where, value))
    return value.strip()


def _require_list_of_str(value, where, allow_empty=True):
    if value is None:
        value = []
    if not isinstance(value, list):
        raise PolicyUnreadable("%s must be a list, got %r" % (where, value))
    out = [_require_str(v, "%s[%d]" % (where, i)) for i, v in enumerate(value)]
    if not out and not allow_empty:
        raise PolicyUnreadable("%s must name at least one entry" % where)
    return out


def load_policy(path):
    """The validated policy, or raise. A policy that does not validate is not a
    policy that requires nothing.

    Validation is strict on purpose: an unknown tier, a content signal no
    detector implements, a rule with no id or no match paths, are each a named
    refusal. A rule that silently matches nothing is the same bypass as no rule
    at all, arrived at by typo instead of by intent.
    """
    if not path or not os.path.isfile(path):
        raise PolicyUnreadable(
            "no policy file at %s. This repository declares no required evidence, which is "
            "not the same as declaring that none is required: write %s before running a "
            "strict gate over it" % (path or "<unset>", POLICY_REL))
    try:
        raw = load_yaml_file(path)
    except ProgramParseError as exc:
        raise PolicyUnreadable("policy at %s does not parse: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise PolicyUnreadable("policy at %s is not a mapping (got %s)"
                               % (path, type(raw).__name__))
    declared = raw.get("schemaVersion")
    if _require_str(declared, "schemaVersion") != SCHEMA_VERSION:
        raise PolicyUnreadable(
            "policy at %s declares schemaVersion %r; this build reads %r. A policy written "
            "against a different schema is not read on a guess" % (path, declared,
                                                                   SCHEMA_VERSION))
    protected = raw.get("protectedEvidenceRequired")
    if not isinstance(protected, bool):
        raise PolicyUnreadable(
            "policy at %s must declare protectedEvidenceRequired as a boolean; got %r. "
            "Whether locally minted evidence counts is the single most consequential line "
            "in this file and it is not defaulted" % (path, protected))
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise PolicyUnreadable("policy at %s declares no rules; a policy with no rules "
                               "requires nothing of anything" % path)
    rules, seen = [], set()
    for i, item in enumerate(rules_raw):
        if not isinstance(item, dict):
            raise PolicyUnreadable("policy at %s: rules[%d] is not a mapping" % (path, i))
        rid = _require_str(item.get("id"), "rules[%d].id" % i)
        if rid in seen:
            raise PolicyUnreadable("policy at %s: two rules share the id %r, so one of them "
                                   "is unreachable by name" % (path, rid))
        seen.add(rid)
        match = item.get("match")
        if not isinstance(match, dict):
            raise PolicyUnreadable("policy at %s: rules[%s].match is not a mapping" % (path, rid))
        paths = _require_list_of_str(match.get("paths"), "rules[%s].match.paths" % rid,
                                     allow_empty=False)
        signals = _require_list_of_str(match.get("contentSignals"),
                                       "rules[%s].match.contentSignals" % rid)
        for sig in signals:
            if sig not in SIGNAL_DETECTORS:
                raise PolicyUnreadable(
                    "policy at %s: rules[%s] names content signal %r, which no detector "
                    "implements (known: %s). A signal nothing computes matches nothing and "
                    "reads as a clean rule" % (path, rid, sig, ", ".join(sorted(SIGNAL_DETECTORS))))
        tier = _require_str(item.get("minimumTier"), "rules[%s].minimumTier" % rid)
        if tier not in TIERS:
            raise PolicyUnreadable("policy at %s: rules[%s].minimumTier %r is not one of %s"
                                   % (path, rid, tier, ", ".join(TIERS)))
        strict_waivers = item.get("strictWaivers")
        if not isinstance(strict_waivers, bool):
            raise PolicyUnreadable(
                "policy at %s: rules[%s] must declare strictWaivers as a boolean. Whether an "
                "exception on this rule still blocks is a decision, not a default" % (path, rid))
        rules.append({
            "id": rid,
            "why": item.get("why") if isinstance(item.get("why"), str) else "",
            "paths": paths,
            "contentSignals": signals,
            "minimumTier": tier,
            "requiredChecks": _require_list_of_str(item.get("requiredChecks"),
                                                   "rules[%s].requiredChecks" % rid),
            "requiredApprovals": _require_list_of_str(item.get("requiredApprovals"),
                                                      "rules[%s].requiredApprovals" % rid),
            "strictWaivers": strict_waivers,
        })
    return {"schemaVersion": SCHEMA_VERSION, "path": path,
            "protectedEvidenceRequired": protected, "rules": rules}


def default_policy_path(cwd):
    return os.path.join(cwd, POLICY_REL)


# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------

def _segment_regex(segment):
    out = ""
    for ch in segment:
        if ch == "*":
            out += "[^/]*"
        elif ch == "?":
            out += "[^/]"
        else:
            out += re.escape(ch)
    return out


def compile_glob(pattern):
    """A glob where `*` stops at a separator and `**` crosses them.

    `fnmatch` is not this: its `*` matches `/` too, so `*.sql` would match
    `docs/notes/anything.sql` and a rule scoped to one directory would quietly
    become a rule over the whole tree. A policy whose blast radius is wrong in
    the widening direction still fails closed, but it fails closed over the
    wrong files, and nobody reading the rule would predict it.
    """
    parts = pattern.split("/")
    rx, last = "", len(parts) - 1
    for i, seg in enumerate(parts):
        if seg == "**":
            rx += ".*" if i == last else "(?:[^/]*/)*"
            continue
        rx += _segment_regex(seg)
        if i != last:
            rx += "/"
    return re.compile("^" + rx + "$")


def path_matches(pattern, path):
    """Case-folded, forward-slash matching, exactly as `impact` compares paths.

    Case-folded in the WIDENING direction on purpose: on a case insensitive
    filesystem `claude.md` and `CLAUDE.md` are one file, and a rule that missed
    one spelling would be a one-keystroke bypass of the control plane rule.
    """
    return bool(compile_glob(pattern.replace("\\", "/").lower())
                .match(path.replace("\\", "/").lower()))


# ---------------------------------------------------------------------------
# The change set
# ---------------------------------------------------------------------------

def _git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def changed_entries(cwd, base, head):
    """Every changed path with its status, including both sides of a rename.

    Null delimited (`-z`), because a path holding a space or a newline is a path
    a line-oriented reader silently splits into two, and half a path matches no
    rule. Rename detection is on (`-M`) so that a renamed file is reported as
    what it was AND as what it became: a migration moved into a directory the
    policy does not name is still a migration, and reporting only the new path
    is how it would stop being one.
    """
    code, out, err = _git(["diff", "--name-status", "-M", "-z", "%s..%s" % (base, head)], cwd)
    if code != 0:
        raise DiffUnavailable("git diff failed: %s" % (err.strip() or "no message"))
    fields = [f for f in out.split("\0")]
    if fields and fields[-1] == "":
        fields.pop()
    entries, i = [], 0
    while i < len(fields):
        status = fields[i]
        i += 1
        if not status:
            continue
        letter = status[0]
        if letter in ("R", "C"):
            if i + 1 >= len(fields):
                raise DiffUnavailable(
                    "git reported a %s record with no destination path, so the change set "
                    "this run read is incomplete and nothing here is judged over it" % letter)
            old_path, new_path = fields[i], fields[i + 1]
            i += 2
            entries.append({"path": new_path, "status": letter, "previousPath": old_path})
            entries.append({"path": old_path, "status": letter + "-from",
                            "previousPath": None})
            continue
        if i >= len(fields):
            raise DiffUnavailable(
                "git reported status %r with no path, so the change set this run read is "
                "incomplete and nothing here is judged over it" % status)
        entries.append({"path": fields[i], "status": letter, "previousPath": None})
        i += 1
    return entries



#: Path shapes whose CONTENT is an example rather than a change to the estate:
#: test files, fixtures and documentation. A path glob still matches these
#: normally, so a policy that names `docs/**` still governs docs; only the
#: content sniffer is held back.
EXAMPLE_SURFACES = (
    "tools/test_*.py", "tests/*", "tests/**/*", "*_test.py", "test_*.py",
    "*/fixtures/*", "*/fixtures/**/*", "evals/*", "evals/**/*",
    "docs/*", "docs/**/*",
    # The field book, added after it blocked its own merge, the same shape as
    # benchmarks/ below. It is a book ABOUT this tool: its dossier and research
    # files quote the migration and DDL text they teach, so the sql-ddl content
    # signal demanded a migration rehearsal receipt for a change that touches no
    # database. Scoped to this one directory, never design/** as a whole, because
    # design/ is where REAL dossiers live and a real migration declared there
    # must still be caught. The path globs are untouched, so a policy naming
    # design/** still governs these files; only the inference FROM CONTENT is
    # held back.
    "design/field-book/*", "design/field-book/**/*",
    # The benchmark harness, added after it blocked its own merge. Its
    # ground-truth file benchmarks/defects.json ENUMERATES planted defects, so
    # it necessarily contains the DDL of the migration defect it plants, and
    # the sql-ddl content signal fired on it and demanded a migration rehearsal
    # receipt for a change that touches no database. That is the same shape as
    # the tests and guides above: a file whose JOB is to carry the text of the
    # thing being detected. benchmarks/ ships no deployable code and its
    # fixture repository is generated at run time, never committed.
    "benchmarks/*", "benchmarks/**/*",
)


def _is_example_surface(path):
    """True when a content signal must not fire on this path.

    Found by running this engine against its own repository, which is the only
    place it could have been found: the tests that PROVE a migration is caught
    contain the migration text, and the guides that TEACH the migration gate
    quote the DDL they teach. Sniffing their added lines demanded a migration
    rehearsal receipt for a change that touches no database at all, and the
    rule that fires on everything is the rule an operator learns to waive.

    Scoped deliberately narrowly. A path glob is untouched, so a policy naming
    `docs/**` or `tests/**` still governs those paths; what is held back is the
    inference FROM CONTENT that a file describes a change to production state.
    A real migration living in a directory nobody declared is still caught,
    because it does not live on one of these surfaces.
    """
    normalized = str(path).replace("\\", "/")
    return any(path_matches(pattern, normalized) for pattern in EXAMPLE_SURFACES)

def _signal_pattern(signal):
    detector_id = SIGNAL_DETECTORS[signal]
    for det_id, _why, _sets, _path_pat, content_pat in DETECTORS:
        if det_id == detector_id and content_pat:
            return content_pat
    raise PolicyUnreadable(
        "content signal %r resolves to detector %r, which carries no content pattern in "
        "brothersbe.impact. The two tables have drifted and this run judges nothing over a "
        "signal that would match everything or nothing" % (signal, detector_id))


def match_rules(policy, entries, cwd, base, head):
    """Which rules this change activates, and which path activated each.

    A rule matches when a path glob matches, OR when a declared content signal
    fires on the added lines of a changed file. The union, not the intersection:
    a migration written outside every directory the policy names is still a
    migration, and requiring both would make the rule exactly as avoidable as
    the declaration it replaced.
    """
    matched = {}
    for rule in policy["rules"]:
        hits = []
        for entry in entries:
            path = entry["path"]
            why = None
            for pattern in rule["paths"]:
                if path_matches(pattern, path):
                    why = "path matches %s" % pattern
                    break
            if (why is None and rule["contentSignals"] and entry["status"] != "D"
                    and not _is_example_surface(path)):
                body = added_lines(cwd, base, head, path).lower()
                for signal in rule["contentSignals"]:
                    if re.search(_signal_pattern(signal), body):
                        why = "content signal %s fired on the added lines" % signal
                        break
            if why:
                hits.append({"path": path, "status": entry["status"], "why": why})
        if hits:
            matched[rule["id"]] = hits
    return matched


# ---------------------------------------------------------------------------
# Evidence lookup
# ---------------------------------------------------------------------------

def _read_json(path):
    """The parsed JSON, or raise ValueError naming the failure.

    One boundary, one explicit failure path: an unreadable exception file and an
    absent one are different facts and this returns neither as an empty list.
    ACCESS is checked before open(), through the same `evidence_problem` the
    hard gates and `evidence.load` use, because a FIFO where an approvals or
    waiver file was expected would otherwise block this command forever, and a
    control that hangs is worse than one that refuses: nothing prints a verdict.
    """
    problem = evidence_mod.evidence_problem(path)
    if problem:
        raise ValueError("%s was refused before it was opened: %s" % (path, problem))
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("%s could not be read as JSON: %s" % (path, exc))


def _offered_check_ids(receipt):
    """(check id, how it was identified) for every obligation this receipt offers.

    ONE identity, and it is the registered one. `checkKinds` is a word the
    caller typed beside the command and `checkId` is a name the evidence
    wrapper resolved out of `.sbe/checks.yml`, and only the second one is
    evidence of anything:

        checkKinds supplied by the caller are not evidence.
        checkId resolved from the protected registry is evidence identity.

    A caller-declared kind is still READ (see `_advisory_offers`), so a
    requirement it was aimed at can say "a receipt offered this and could not
    be credited for it" instead of the flat MISSING that would send an
    operator looking for a receipt sitting right there. It cannot clear the
    requirement, which is the whole change.
    """
    out = []
    cid = receipt.get("checkId")
    if isinstance(cid, str) and cid.strip():
        out.append((cid.strip(), "registered checkId"))
    return out


def _advisory_offers(receipt):
    """Every caller-declared `checkKinds` entry on this receipt, as strings.

    Read for the message only. Nothing here is allowed to satisfy a required
    check; see `_offered_check_ids` for why the two fields are not the same
    question.
    """
    kinds = receipt.get("checkKinds")
    if not isinstance(kinds, list):
        return []
    return [k.strip() for k in kinds if isinstance(k, str) and k.strip()]


def collect_receipts(receipts_dir):
    """Every receipt under the directory, each carried with its read failure.

    An empty string, a path that is not a directory, and a directory holding no
    receipts are three different sentences and all three are recorded. None of
    them is a skip: the requirement they fail to satisfy still reports MISSING,
    which is the whole point of this module.
    """
    found, notes = [], []
    if not receipts_dir:
        notes.append("no receipts directory was given, so no receipt could satisfy anything. "
                     "An unsupplied input is not an exemption")
        return found, notes
    if not os.path.isdir(receipts_dir):
        notes.append("receipts directory %s does not exist or is not a directory, so no "
                     "receipt could satisfy anything" % receipts_dir)
        return found, notes
    try:
        names = sorted(os.listdir(receipts_dir))
    except OSError as exc:
        notes.append("receipts directory %s could not be listed (%s), so this run cannot say "
                     "what evidence exists and treats every requirement as unmet"
                     % (receipts_dir, exc))
        return found, notes
    for name in names:
        if not name.endswith(".json"):
            continue
        full = os.path.join(receipts_dir, name)
        try:
            receipt = evidence_mod.load(full)
        except evidence_mod.ReceiptUnreadable as exc:
            found.append({"path": full, "receipt": None, "problem": str(exc)})
            continue
        found.append({"path": full, "receipt": receipt, "problem": None})
    if not found:
        notes.append("receipts directory %s holds no *.json receipt, so no receipt could "
                     "satisfy anything. An empty directory is not an exemption" % receipts_dir)
    return found, notes


def _check_state(check_id, receipts, cwd, head_sha, protected_required):
    """The state of one required check, and the sentence that earned it.

    Order is deliberate and mirrors `evidence.verify`: every way a receipt can
    be WRONG is decided before any way it can be merely WEAK, because "this
    receipt is broken" outranks "this receipt was never authoritative".
    """
    candidates = []
    for item in receipts:
        receipt = item["receipt"]
        if receipt is None:
            continue
        for offered, source in _offered_check_ids(receipt):
            if offered == check_id:
                candidates.append((item, source))
                break
    if not candidates:
        broken = [i for i in receipts if i["receipt"] is None]
        if broken:
            return (
                "INVALID",
                "no readable receipt offers %s, and %d receipt file(s) under the receipts "
                "directory could not be read at all (%s). An unreadable receipt is a broken "
                "claim, never an absence" % (check_id, len(broken),
                                             broken[0]["problem"]),
                None)
        advisory = []
        for item in receipts:
            if item["receipt"] is None:
                continue
            if check_id in _advisory_offers(item["receipt"]):
                advisory.append(os.path.basename(item["path"]))
        if advisory:
            return ("MISSING",
                    "policy requires registered check %s and the only receipt(s) offering that "
                    "name (%s) declare it through the caller-supplied checkKinds field, not "
                    "through a checkId resolved from .sbe/checks.yml. A kind the caller typed "
                    "is not evidence of which check ran, so it clears no requirement; rerun "
                    "through `sbe evidence run --check %s`"
                    % (check_id, ", ".join(advisory), check_id), None)
        return ("MISSING",
                "policy requires check %s and no receipt offers it. Required and absent is "
                "MISSING, which exits nonzero; it is not NO-DATA and it is not a pass"
                % check_id, None)
    best = None
    for item, source in candidates:
        receipt = item["receipt"]
        recorded_head = receipt.get("headCommit")
        moved = _covered_files_that_moved(cwd, receipt)
        if (isinstance(recorded_head, str) and head_sha and recorded_head != head_sha
                and moved is not False):
            detail = ("every covered file is unreadable or unrecorded, so nothing here can "
                      "say whether the evidence still describes this tree"
                      if moved is None else
                      "and %d covered file(s) changed since: %s"
                      % (len(moved), ", ".join(moved[:4])))
            state, why = ("STALE",
                          "receipt %s offers %s but was made against commit %s, not %s, %s. "
                          "Evidence made against a different program is evidence about a "
                          "different program" % (os.path.basename(item["path"]), check_id,
                                                 recorded_head[:12], head_sha[:12], detail))
        else:
            result = evidence_mod.verify(item["path"], cwd=cwd)
            if result["verdict"] == "FAIL":
                state, why = ("INVALID",
                              "receipt %s offers %s and does not verify: %s"
                              % (os.path.basename(item["path"]), check_id,
                                 "; ".join(result["reasons"])[:400]))
            elif result["verdict"] != "PASS":
                state, why = ("UNPROTECTED",
                              "receipt %s offers %s and verifies only as advisory: %s"
                              % (os.path.basename(item["path"]), check_id,
                                 "; ".join(result["reasons"])[:400]))
            else:
                level, level_why = evidence_mod.trust_level(receipt)
                if protected_required and (PROTECTED_LEVEL is None or level != PROTECTED_LEVEL):
                    state, why = ("UNPROTECTED",
                                  "receipt %s offers %s and is valid, but its trust level is "
                                  "%s and this policy requires protected evidence: %s"
                                  % (os.path.basename(item["path"]), check_id, level,
                                     level_why))
                else:
                    # The command must also have PASSED. Found by a hostile
                    # refuter of this engine: nothing here read exitCode, so a
                    # registered check that ran and FAILED still satisfied its
                    # requirement. Item 4's definition of done needs both
                    # halves, that the registered check ran and that it ran
                    # correctly, and a receipt of a failing run is evidence of
                    # the defect, never of the obligation.
                    exit_code = receipt.get("exitCode")
                    if exit_code is None:
                        state, why = ("INVALID",
                                      "receipt %s offers %s and verifies, but records no exit "
                                      "code, so nothing here can say whether the check passed. "
                                      "An unreadable outcome fails closed"
                                      % (os.path.basename(item["path"]), check_id))
                    elif exit_code != 0:
                        state, why = ("INVALID",
                                      "receipt %s offers %s and verifies, but the registered "
                                      "command exited %s. A receipt of a failing run records "
                                      "the defect, it does not satisfy the requirement"
                                      % (os.path.basename(item["path"]), check_id, exit_code))
                    else:
                        state, why = ("SATISFIED",
                                      "receipt %s offers %s, verifies, exited 0, and its trust "
                                      "level is %s (identity read from %s)"
                                      % (os.path.basename(item["path"]), check_id, level, source))
        if best is None or _state_rank(state) < _state_rank(best[0]):
            best = (state, why, source)
    return best



def _covered_files_that_moved(cwd, receipt):
    """Which of a receipt's covered files differ from the tree today.

    Returns a list of paths (possibly empty), or None when nothing could be
    read and the question is unanswerable.

    Why this exists, found by dogfooding: a receipt COMMITTED into the
    repository it describes can never be bound to the commit that contains it,
    because writing it moves the head. Judging staleness by the head commit
    alone therefore made committed evidence permanently stale, which pushes
    every operator toward not committing evidence at all. The honest question
    is not whether the head moved, it is whether what the receipt covered
    moved, and the receipt already records a hash per covered file, so the
    question can be asked rather than assumed.

    Deliberately conservative in both directions. An empty list (nothing moved)
    is the ONLY reading that lets a head mismatch pass, and False is returned
    for that case so the caller cannot confuse it with the unreadable case.
    Any covered file that changed, vanished or cannot be hashed keeps the
    receipt stale and is named.
    """
    covered = receipt.get("coveredFiles")
    if not isinstance(covered, list) or not covered:
        return None
    moved, readable = [], 0
    for entry in covered:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        recorded = entry.get("sha256")
        if not isinstance(path, str) or not isinstance(recorded, str):
            return None
        full = os.path.join(cwd, path)
        try:
            with io.open(full, "rb") as handle:
                actual = hashlib.sha256(handle.read()).hexdigest()
        except (IOError, OSError):
            moved.append("%s (unreadable)" % path)
            continue
        readable += 1
        if actual != recorded:
            moved.append(path)
    if not readable:
        return None
    return False if not moved else moved

def _state_rank(state):
    """Lower is better. A change carrying one good receipt and one broken copy of
    the same check is satisfied by the good one; the broken one is reported in
    the notes rather than deciding the verdict for a requirement something else
    already met."""
    order = ("SATISFIED", "UNPROTECTED", "STALE", "INVALID", "MISSING", "NOT-REQUIRED")
    return order.index(state) if state in order else len(order)


def load_approvals(path):
    """(records, problem). A missing file is not a problem; an unreadable one is."""
    if not path or not os.path.exists(path):
        return [], None
    try:
        data = _read_json(path)
    except ValueError as exc:
        return [], str(exc)
    records = data if isinstance(data, list) else data.get("approvals")
    if not isinstance(records, list):
        return [], ("approvals at %s are not a list and carry no 'approvals' list, so this "
                    "run cannot tell who approved what" % path)
    return [r for r in records if isinstance(r, dict)], None


def _approval_state(role, approvals, problem, head_sha, protected_required, source):
    if problem:
        return ("INVALID", "the approval record could not be read (%s), so no approval is "
                           "credited: an unreadable control fails closed" % problem)
    matches = [r for r in approvals if str(r.get("role", "")).strip() == role]
    if not matches:
        return ("MISSING",
                "policy requires an approval from %s and %s records none. Required and "
                "absent is MISSING; an approval nobody gave is not an approval nobody "
                "needed" % (role, source))
    for record in matches:
        recorded_head = str(record.get("headCommit", "")).strip()
        if head_sha and recorded_head and recorded_head != head_sha:
            continue
        if head_sha and not recorded_head:
            continue
        if protected_required and record.get("protected") is not True:
            continue
        return ("SATISFIED",
                "%s approved by %s, bound to commit %s"
                % (role, str(record.get("approver", "")).strip() or "an unnamed approver",
                   (recorded_head or head_sha)[:12]))
    unbound = [r for r in matches
               if not str(r.get("headCommit", "")).strip()
               or str(r.get("headCommit", "")).strip() != head_sha]
    if unbound:
        return ("STALE",
                "%s has %d approval record(s), none bound to commit %s. An approval of an "
                "earlier diff is not an approval of this one" % (role, len(unbound),
                                                                 (head_sha or "")[:12]))
    return ("UNPROTECTED",
            "%s is approved for this commit, but no record carries protected: true and this "
            "policy requires protected approval" % role)


# ---------------------------------------------------------------------------
# Tier
# ---------------------------------------------------------------------------

def _tier_index(tier):
    return TIERS.index(tier) if tier in TIERS else 0


def load_decision(path):
    """(record, problem) for a recorded request to lower a detected tier."""
    if not path or not os.path.exists(path):
        return None, None
    try:
        data = _read_json(path)
    except ValueError as exc:
        return None, str(exc)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None, "the decision record at %s is not a mapping" % path
    return data, None


def _decision_defects(record, head_sha):
    defects = []
    for field in ("decision", "reason", "who"):
        if not str(record.get(field, "")).strip():
            defects.append("no %s" % field)
    recorded_head = str(record.get("headCommit", "")).strip()
    if not recorded_head:
        defects.append("no headCommit, so it is a decision about no particular diff")
    elif head_sha and recorded_head != head_sha:
        defects.append("headCommit %s, which is not this head %s"
                       % (recorded_head[:12], head_sha[:12]))
    approval = record.get("approval")
    if not isinstance(approval, dict):
        defects.append("no approval object")
    elif approval.get("protected") is not True:
        defects.append("an approval that is not protected")
    elif not str(approval.get("approver", "")).strip():
        defects.append("a protected approval naming no approver")
    return defects


def _tier_requirement(minimum, declared, intake_problem, decision, decision_problem,
                      head_sha, any_rule_matched):
    """The one requirement that is about classification rather than about evidence."""
    if not any_rule_matched:
        return ("NOT-REQUIRED",
                "no rule matched this change, so no rule sets a tier floor for it. The "
                "minimum detected tier is %s" % minimum)
    if decision_problem:
        return ("INVALID",
                "a tier decision record was named and could not be read (%s), so no lowering "
                "is credited" % decision_problem)
    if declared is None:
        return ("MISSING",
                "policy sets a minimum detected tier of %s for this change and nothing "
                "declared a tier to reconcile it against%s. A change that avoids the "
                "declaration avoids the gate, which is exactly the bypass this rule exists "
                "to close" % (minimum, (": " + intake_problem) if intake_problem else ""))
    if _tier_index(declared) >= _tier_index(minimum):
        return ("SATISFIED",
                "declared tier %s meets or exceeds the minimum detected tier %s"
                % (declared, minimum))
    if decision is None:
        return ("INVALID",
                "declared tier %s sits below the minimum detected tier %s and no decision "
                "record was given. This engine raises a tier and never silently lowers one; "
                "a lowering needs a recorded decision with a protected approval"
                % (declared, minimum))
    defects = _decision_defects(decision, head_sha)
    if defects:
        return ("INVALID",
                "declared tier %s sits below the minimum detected tier %s and the decision "
                "record offered to justify it has %s" % (declared, minimum, ", ".join(defects)))
    return ("SATISFIED",
            "declared tier %s sits below the minimum detected tier %s and is lowered by a "
            "recorded decision approved by %s under protected approval"
            % (declared, minimum, str(decision["approval"].get("approver")).strip()))


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------

def load_waivers(path):
    """(records, problem). An unreadable waiver file waives nothing and says so."""
    if not path or not os.path.exists(path):
        return [], None
    try:
        data = _read_json(path)
    except ValueError as exc:
        return [], str(exc)
    records = data if isinstance(data, list) else data.get("waivers")
    if not isinstance(records, list):
        return [], ("waivers at %s are not a list and carry no 'waivers' list, so no "
                    "exception here is readable" % path)
    return [r for r in records if isinstance(r, dict)], None


def waiver_defects(record, today):
    """Every reason this exception is refused. Empty means it is accepted.

    An exception is a decision somebody made, so it names four things: why, who
    owns it, when it stops, and a protected approval. Three of four is not an
    exception, it is a note, and a note does not clear a control.
    """
    defects = []
    for field in ("reason", "owner", "expiry"):
        if not str(record.get(field, "")).strip():
            defects.append("no %s" % field)
    expiry = str(record.get("expiry", "")).strip()
    if expiry:
        try:
            when = datetime.datetime.strptime(expiry[:10], "%Y-%m-%d").date()
        except ValueError:
            defects.append("an expiry (%r) that is not an ISO date, so nothing can tell "
                           "whether it has passed" % expiry)
        else:
            if when < today:
                defects.append("an expiry of %s, which has passed. An expired exception "
                               "waives nothing" % expiry)
    approval = record.get("approval")
    if not isinstance(approval, dict):
        defects.append("no approval object")
    elif approval.get("protected") is not True:
        defects.append("an approval that is not protected: an exception a local process can "
                       "mint for itself is not an exception")
    elif not str(approval.get("approver", "")).strip():
        defects.append("a protected approval naming no approver")
    return defects


def _waiver_for(waivers, rule_id, requirement_key):
    for record in waivers:
        if str(record.get("ruleId", "")).strip() != rule_id:
            continue
        want = str(record.get("requirement", "")).strip()
        if want in (requirement_key, "*"):
            return record
    return None


# ---------------------------------------------------------------------------
# The evaluation
# ---------------------------------------------------------------------------

def evaluate(cwd, base=None, head="HEAD", policy_path=None, receipts_dir=None,
             dossier=None, intake=None, approvals_path=None, waivers_path=None,
             decision_path=None, strict_waivers=False):
    """The whole verdict, as a dict ready to be written as policy-report.json."""
    cwd = os.path.abspath(cwd)
    policy = load_policy(policy_path or default_policy_path(cwd))
    base_sha, head_ref = resolve_range(cwd, base, head)
    code, head_sha, _err = _git(["rev-parse", head_ref], cwd)
    head_sha = head_sha.strip() if code == 0 else ""

    entries = changed_entries(cwd, base_sha, head_ref)
    matched = match_rules(policy, entries, cwd, base_sha, head_ref)
    protected_required = policy["protectedEvidenceRequired"]

    if approvals_path is None and dossier:
        approvals_path = os.path.join(dossier, "12-approvals.json")
    if approvals_path is None:
        approvals_path = os.path.join(cwd, ".sbe", "approvals.json")
    approvals, approvals_problem = load_approvals(approvals_path)
    approvals_source = approvals_path if os.path.exists(approvals_path) else (
        "no approval record at %s" % approvals_path)

    receipts, receipt_notes = collect_receipts(receipts_dir)
    waivers, waivers_problem = load_waivers(waivers_path)
    decision, decision_problem = load_decision(decision_path)
    intake_tier, _answers, intake_problem = read_intake(intake) if intake else (
        None, None, "no intake was named")

    today = datetime.datetime.now(datetime.timezone.utc).date()
    notes = list(receipt_notes)
    if waivers_problem:
        notes.append("the waiver file could not be read (%s), so no exception is credited"
                     % waivers_problem)
    if approvals_problem:
        notes.append("the approval file could not be read (%s), so no approval is credited"
                     % approvals_problem)

    minimum = "T0"
    for rule in policy["rules"]:
        if rule["id"] in matched and _tier_index(rule["minimumTier"]) > _tier_index(minimum):
            minimum = rule["minimumTier"]

    requirements = []

    def add(rule_id, kind, ident, state, why, strict):
        if state not in STATES:
            # The guard that keeps the vocabulary honest, and it is here rather
            # than in a comment because the whole defect being closed is one
            # word carrying two meanings. NO-DATA is not in STATES, so a future
            # edit that reaches for it to mean "required but absent" fails loudly
            # instead of shipping the ambiguity back in.
            raise ValueError(
                "requirement %s:%s was given state %r, which is not one of %s. NO-DATA in "
                "particular is not a requirement state here: required and absent is MISSING, "
                "and not required is NOT-REQUIRED" % (kind, ident, state, ", ".join(STATES)))
        key = "%s:%s" % (kind, ident) if ident else kind
        record = {"ruleId": rule_id, "kind": kind, "id": ident, "requirement": key,
                  "state": state, "why": why, "strictWaivers": strict,
                  "waived": False, "waivedFrom": None, "waiverRefused": None}
        if state in BLOCKING_STATES:
            candidate = _waiver_for(waivers, rule_id, key)
            if candidate is not None:
                defects = waiver_defects(candidate, today)
                if defects:
                    record["waiverRefused"] = (
                        "an exception was offered for %s and is REFUSED: it has %s. The "
                        "requirement keeps its state" % (key, ", ".join(defects)))
                else:
                    record["waivedFrom"] = state
                    record["state"] = WAIVED
                    record["waived"] = True
                    record["why"] = (
                        "%s was %s and is WAIVED, never passed: %s, owned by %s, expiring %s, "
                        "under protected approval by %s"
                        % (key, state, str(candidate.get("reason")).strip(),
                           str(candidate.get("owner")).strip(),
                           str(candidate.get("expiry")).strip()[:10],
                           str(candidate.get("approval", {}).get("approver")).strip()))
        requirements.append(record)

    tier_state, tier_why = _tier_requirement(minimum, intake_tier, intake_problem, decision,
                                             decision_problem, head_sha, bool(matched))
    strict_tier = any(r["strictWaivers"] for r in policy["rules"] if r["id"] in matched)
    add("(policy)", "tier", minimum, tier_state, tier_why, strict_tier)

    for rule in policy["rules"]:
        rid = rule["id"]
        if rid not in matched:
            for check_id in rule["requiredChecks"]:
                add(rid, "check", check_id, "NOT-REQUIRED",
                    "rule %s did not match this change, so %s is not required of it. This is "
                    "NOT-REQUIRED, a verdict; it is not an absence of evidence"
                    % (rid, check_id), rule["strictWaivers"])
            for role in rule["requiredApprovals"]:
                add(rid, "approval", role, "NOT-REQUIRED",
                    "rule %s did not match this change, so approval from %s is not required "
                    "of it" % (rid, role), rule["strictWaivers"])
            continue
        for check_id in rule["requiredChecks"]:
            state, why, _source = _check_state(check_id, receipts, cwd, head_sha,
                                               protected_required)
            add(rid, "check", check_id, state,
                "%s (activated by %s)" % (why, matched[rid][0]["why"]), rule["strictWaivers"])
        for role in rule["requiredApprovals"]:
            state, why = _approval_state(role, approvals, approvals_problem, head_sha,
                                         protected_required, approvals_source)
            add(rid, "approval", role, state,
                "%s (activated by %s)" % (why, matched[rid][0]["why"]), rule["strictWaivers"])

    blocking = []
    for record in requirements:
        if record["state"] in BLOCKING_STATES:
            blocking.append(record)
        elif record["state"] == WAIVED and (record["strictWaivers"] or strict_waivers):
            blocking.append(record)

    if blocking:
        verdict = "BLOCKED"
    elif not matched:
        verdict = "NOT-REQUIRED"
    else:
        verdict = "PASS"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "policyPath": policy["path"],
        "protectedEvidenceRequired": protected_required,
        "baseCommit": base_sha,
        "headCommit": head_sha or head_ref,
        "changedPaths": entries,
        "matchedRules": [{"id": rid, "hits": hits} for rid, hits in sorted(matched.items())],
        "minimumDetectedTier": minimum,
        "minimumDetectedTierIsA":
            "a floor, never a ceiling: this engine raises a declared tier that sits under it "
            "and lowers one only against a decision record carrying a protected approval",
        "declaredTier": intake_tier,
        "effectiveTier": (intake_tier if intake_tier
                          and _tier_index(intake_tier) > _tier_index(minimum) else minimum),
        "requirements": requirements,
        "blocking": [r["requirement"] for r in blocking],
        "notes": notes,
        "limits": [
            "a required check is satisfied only by a receipt whose checkId was resolved from "
            ".sbe/checks.yml, so a SATISFIED check here proves the registered command ran, "
            "with the registered arguments, over the files that check covers, against runner "
            "bytes that still hash the same. checkKinds, the field the caller supplies, "
            "clears nothing. What remains unproven is the registry itself: an entry pointing "
            "an important-sounding id at a command that checks nothing is a false registry, "
            "and what defends against that is .sbe/checks.yml being in the control-plane rule "
            "above, not anything this engine computes",
            "approvals are read from a repository file, not from a forge. This run proves a "
            "record exists, is bound to this commit and declares protected: true; it does not "
            "call GitHub to confirm the review happened there",
        ],
        "verdict": verdict,
    }


def render(report):
    """The report as lines a person reads. WAIVED is spelled WAIVED."""
    lines = ["policy %s over %d changed path(s), base %s head %s"
             % (report["policyPath"], len(report["changedPaths"]),
                report["baseCommit"][:12], report["headCommit"][:12])]
    for rule in report["matchedRules"]:
        lines.append("  MATCHED   %-28s %s (%s)"
                     % (rule["id"], rule["hits"][0]["path"], rule["hits"][0]["why"]))
    for record in report["requirements"]:
        lines.append("  %-12s %-28s %s" % (record["state"], record["requirement"],
                                           record["why"]))
        if record["waiverRefused"]:
            lines.append("  %-12s %-28s %s" % ("REFUSED", record["requirement"],
                                               record["waiverRefused"]))
    lines.append("minimum detected tier %s (a floor, not a ceiling), declared tier %s, "
                 "effective tier %s"
                 % (report["minimumDetectedTier"], report["declaredTier"] or "none declared",
                    report["effectiveTier"]))
    for note in report["notes"]:
        lines.append("  NOTE      %s" % note)
    lines.append("verdict: %s" % report["verdict"])
    return lines

#!/usr/bin/env python3
"""RL3.b: explicit preferences, observations and inferred hypotheses kept apart.

WHY THIS EXISTS. A vault note that records what the owner SAID they prefer and
a note that records what a session GUESSED they prefer read alike as prose. If
the guess can carry record authority, or retire the stated preference, the
guess silently becomes the rule. This module reads a note into a
context_capsule tagged entry (scripts/context_capsule.py, RL3.a) with its kind
taken from frontmatter only, and applies supersession between preferences.

THE KIND IS DECLARED, NEVER GUESSED. classify_kind reads the frontmatter key
`kind`; absent reads as observation; a value outside KINDS raises. Prose is
never read: a sentence saying "I prefer" is an observation until the note says
kind: explicit_preference.

AN INFERENCE CANNOT CARRY RECORD AUTHORITY. entry_from_note demotes an
inferred_hypothesis that claims source_of_record to casual and says so.

SUPERSESSION is between explicit preferences on one frontmatter subject only:
an explicit `corrects: <rel_path>` retires its target whatever the dates or
authority; otherwise the newer by observed_at with authority not below the
older retires the older; a pair equal on date and authority is retired on both
sides as 'unresolved', the WITHHOLD answer bm_vault_contradiction gives when
no tier decides. Observations and inferences never retire and are never
retired here.

REVOCATION (RL3.c). revoked_sources reads the principals registry per
request; apply_revocation marks, never filters, so admit_context stays the one
function that decides admission. An unreadable registry is NO-DATA and marks
every principal sourced entry: an unreadable revocation list is not an empty
one. forget_plans gathers bm_vault_retention's own plans and deletes nothing.

Stdlib only, pure, writes nothing.
"""
import os
import re
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm_vault_authority  # noqa: E402
import bm_vault_principals  # noqa: E402
import bm_vault_retention  # noqa: E402
import bm_vault_staleness  # noqa: E402

KIND_RE_FIELD = "kind"          # frontmatter key; absent reads as observation

#: context_capsule.KINDS and ENTRY_KEYS, mirrored: products/ ships without
#: scripts/. test_bm_context_authority pins them equal.
KINDS = ("explicit_preference", "observation", "inferred_hypothesis")
ENTRY_KEYS = ("snippet", "source", "authority", "kind", "scope",
              "observed_at", "revoked", "authoritative")

PREFERENCE = "explicit_preference"
OBSERVATION = "observation"
INFERENCE = "inferred_hypothesis"

#: Kinds that never retire anything and are never retired by supersede().
_NEVER_COMPETE = (OBSERVATION, INFERENCE)

INFERENCE_DEMOTED = "inference cannot carry record authority"

_RANK = {name: i for i, name in enumerate(bm_vault_authority.LEVELS)}


def _frontmatter(text):
    """The frontmatter block, the same reading bm_vault_authority uses."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _body(text):
    if not text.startswith("---"):
        return text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return text.strip()
    rest = text[end + 4:]
    nl = rest.find("\n")
    return (rest[nl + 1:] if nl != -1 else "").strip()


def _field(front, key):
    """The stripped value of `key:` in a frontmatter block, or None when the
    key is absent. A present key with an empty value is ''."""
    m = re.search(r"^" + re.escape(key) + r":[ \t]*(.*)$", front, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'").strip()


def classify_kind(text):
    """The note's frontmatter kind: one of KINDS; absent is 'observation'; any
    other value raises ValueError naming it. Prose is never read for this."""
    if not isinstance(text, str):
        raise ValueError("classify_kind: text must be str, got %r"
                         % type(text).__name__)
    value = _field(_frontmatter(text), KIND_RE_FIELD)
    if value is None:
        return OBSERVATION  # absent: prose is never read
    if value not in KINDS:
        raise ValueError("classify_kind: unknown kind %r, not in %s"
                         % (value, "/".join(KINDS)))
    return value


def entry_from_note(rel_path, text, today):
    """(entry, findings) for one note, entry a context_capsule tagged entry
    carrying also its frontmatter 'subject' and 'corrects' (None when absent).
    Unreadable arguments or an unknown kind raise ValueError."""
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("entry_from_note: rel_path must be a non empty str, "
                         "got %r" % (rel_path,))
    if not isinstance(text, str):
        raise ValueError("entry_from_note: text must be str, got %r"
                         % type(text).__name__)
    if isinstance(today, datetime) or not isinstance(today, date):
        raise ValueError("entry_from_note: today must be a date, got %r"
                         % (today,))
    findings = []
    kind = classify_kind(text)

    level, problem = bm_vault_authority.read_authority(text)
    if problem is not None or level not in _RANK:
        findings.append("%s: %s" % (rel_path, problem or "unreadable authority"))
        level = "casual"
    if kind == INFERENCE and level == "source_of_record":
        findings.append("%s: %s" % (rel_path, INFERENCE_DEMOTED))
        level = "casual"

    verified, vproblem = bm_vault_staleness.read_verified_at(text)
    if vproblem is not None:
        findings.append("%s: %s" % (rel_path, vproblem))
    observed_at = verified.isoformat() if isinstance(verified, date) else None
    if isinstance(verified, date) and verified > today:
        findings.append("%s: verified_at %s is after today %s"
                        % (rel_path, observed_at, today.isoformat()))

    front = _frontmatter(text)
    account = _field(front, "account")
    project = _field(front, "project")
    scope = None
    if account is not None and project is not None:
        scope = {"account": account, "project": project}

    entry = {"snippet": _body(text), "source": rel_path, "authority": level,
             "kind": kind, "scope": scope, "observed_at": observed_at,
             "revoked": False, "authoritative": False,
             "subject": _field(front, "subject") or None,
             "corrects": _field(front, "corrects") or None}
    return entry, findings


def _optional_str(entry, key):
    value = entry.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError("supersede: %s must be str or None, got %r"
                         % (key, value))
    return value


def _checked(entries):
    """The entries validated and de-duplicated by source. An exact repeat is
    one entry; two different entries under one source are corrupt."""
    if not isinstance(entries, list):
        raise ValueError("supersede: entries must be a list, got %r"
                         % type(entries).__name__)
    by_source = {}
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("supersede: entry must be a dict, got %r" % (e,))
        source = e.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("supersede: source must be a non empty str, "
                             "got %r" % (source,))
        if not isinstance(e.get("kind"), str) or e["kind"] not in KINDS:
            raise ValueError("supersede: unknown kind %r in %s"
                             % (e.get("kind"), source))
        if not isinstance(e.get("authority"), str) \
                or e["authority"] not in _RANK:
            raise ValueError("supersede: unknown authority %r in %s"
                             % (e.get("authority"), source))
        _optional_str(e, "subject")
        _optional_str(e, "corrects")
        observed = _optional_str(e, "observed_at")
        if observed is not None:
            try:
                date.fromisoformat(observed[:10])
            except ValueError:
                raise ValueError("supersede: unreadable observed_at %r in %s"
                                 % (observed, source))
        if source in by_source and by_source[source] != e:
            raise ValueError("supersede: two different entries share source "
                             "%r" % (source,))
        by_source[source] = e
    return sorted(by_source.values(),
                  key=lambda e: (e.get("subject") or "", e["source"]))


def _day(entry):
    observed = entry.get("observed_at")
    return date.fromisoformat(observed[:10]) if observed is not None else None


def _competes(entry):
    if entry["kind"] in _NEVER_COMPETE:
        return False
    return entry.get("subject") is not None


def _retire_group(group, retired_by):
    """Fill retired_by {source: (retired_by, reason)} for one subject's
    preferences, sorted by source."""
    sources = set(e["source"] for e in group)
    decided = set()
    for e in group:
        target = e.get("corrects")
        if target is not None and target != e["source"] and target in sources:
            decided.add(frozenset((e["source"], target)))
            if target not in retired_by:
                retired_by[target] = (e["source"], "corrected")
    survivors = [e for e in group if e["source"] not in retired_by]
    for older in survivors:
        best = None
        for newer in survivors:
            if newer is older or frozenset((newer["source"], older["source"])) in decided:
                continue
            d_new, d_old = _day(newer), _day(older)
            if d_new is None or d_old is None or not d_new > d_old:
                continue
            if _RANK[newer["authority"]] >= _RANK[older["authority"]]:
                if best is None or (d_new, newer["source"]) > (_day(best), best["source"]):
                    best = newer
        if best is not None:
            retired_by[older["source"]] = (best["source"], "newer")
    for a in survivors:
        if a["source"] in retired_by:
            continue
        for b in survivors:
            if b is a or frozenset((a["source"], b["source"])) in decided:
                continue
            if _day(a) == _day(b) and a["authority"] == b["authority"]:
                retired_by[a["source"]] = (b["source"], "unresolved")
                break


def supersede(entries):
    """(active, retired). retired items are {'source', 'retired_by',
    'reason'}, reason one of 'corrected', 'newer', 'unresolved'. Pure and
    deterministic: entries are sorted by (subject, source)."""
    ordered = _checked(entries)
    groups = {}
    for e in ordered:
        if _competes(e):
            groups.setdefault(e["subject"], []).append(e)
    retired_by = {}
    for subject in sorted(groups):
        _retire_group(groups[subject], retired_by)
    active = [e for e in ordered if e["source"] not in retired_by]
    retired = [{"source": e["source"], "retired_by": retired_by[e["source"]][0],
                "reason": retired_by[e["source"]][1]}
               for e in ordered if e["source"] in retired_by]
    return active, retired


def dangling_corrections(entries):
    """Findings for every preference whose corrects names a source that is not
    a preference on its own subject among entries: supersede() retires
    nothing for it, and this says so."""
    ordered = _checked(entries)
    findings = []
    for e in ordered:
        target = e.get("corrects")
        if target is None or not _competes(e):
            continue
        peers = set(o["source"] for o in ordered
                    if _competes(o) and o["subject"] == e["subject"])
        if target == e["source"] or target not in peers:
            findings.append("%s: corrects %r names no preference on subject "
                            "%r" % (e["source"], target, e["subject"]))
    return findings


REASON_REVOKED = "revoked principal"
REASON_DELETED = "deleted source"
REASON_REGISTRY = "principal registry NO-DATA"
REASON_UNREADABLE_BY = "unreadable principal"


def revoked_sources(registry_path):
    """(names whose status is 'revoked', problem). Names are
    bm_vault_principals.normalize_identity forms. Any registry that does not
    load (absent, unreadable, not an object, a record that is not an object
    or carries an unknown status) is (set(), 'NO-DATA: <first problem>'), and
    the caller withholds every principal sourced entry while problem is not
    None. A tamper suspect active record reads revoked, status_of's rule."""
    if not isinstance(registry_path, str) or not registry_path:
        return set(), "NO-DATA: registry path must be a non empty str, got %r" \
            % (registry_path,)
    registry, problems = bm_vault_principals.load(registry_path)
    if problems:
        return set(), "NO-DATA: %s" % problems[0]
    if registry is None:
        return set(), "NO-DATA: no principal registry at %s" % registry_path
    revoked = set()
    principals = registry.get("principals", {})
    for name in sorted(principals):
        rec = principals[name]
        if not isinstance(rec, dict) \
                or rec.get("status") not in bm_vault_principals.STATUSES:
            return set(), "NO-DATA: principal %r has no readable status" % name
        if bm_vault_principals.status_of(registry, name) == "revoked":
            revoked.add(bm_vault_principals.normalize_identity(name))
    return revoked, None


def _source_parts(source):
    """(principal or None, path) from a source 'principal:path' or 'path'."""
    if ":" in source:
        head, tail = source.split(":", 1)
        return head, tail
    return None, source


def _str_set(value, name):
    if not isinstance(value, (set, frozenset)):
        raise ValueError("apply_revocation: %s must be a set, got %r"
                         % (name, type(value).__name__))
    for item in value:
        if not isinstance(item, str):
            raise ValueError("apply_revocation: %s holds a non str %r"
                             % (name, item))
    return value


def _revocation_reason(entry, revoked, deleted_paths, registry_problem):
    source = entry.get("source")
    if not isinstance(source, str):
        return None  # admit_context withholds it as malformed
    head, path = _source_parts(source)
    principal = entry.get("by", head)
    if principal is not None and not isinstance(principal, str):
        return REASON_UNREADABLE_BY
    if principal is not None and principal.strip():
        if registry_problem is not None:
            return REASON_REGISTRY
        if bm_vault_principals.normalize_identity(principal) in revoked:
            return REASON_REVOKED
    if source in deleted_paths or path in deleted_paths:
        return REASON_DELETED
    return None


def apply_revocation(entries, revoked, deleted_paths, registry_problem):
    """(entries, marked). Each entry is copied; revoked is set True on every
    entry whose principal (its 'by' field, else the source before ':') is in
    revoked, whose source path is in deleted_paths, or, when registry_problem
    is not None, that names any principal at all. marked items are
    {'source', 'reason'}. Nothing is filtered: admit_context withholds."""
    if not isinstance(entries, list):
        raise ValueError("apply_revocation: entries must be a list, got %r"
                         % type(entries).__name__)
    revoked = set(bm_vault_principals.normalize_identity(n)
                  for n in _str_set(revoked, "revoked"))
    _str_set(deleted_paths, "deleted_paths")
    if registry_problem is not None and not isinstance(registry_problem, str):
        raise ValueError("apply_revocation: registry_problem must be str or "
                         "None, got %r" % (registry_problem,))
    out, marked = [], []
    for entry in entries:
        if not isinstance(entry, dict):
            out.append(entry)  # admit_context withholds it as malformed
            continue
        entry = dict(entry)
        reason = _revocation_reason(entry, revoked, deleted_paths,
                                    registry_problem)
        if reason is not None:
            entry["revoked"] = True
            marked.append({"source": entry["source"], "reason": reason})
        out.append(entry)
    return out, marked


def _plan_path_problem(vault, rel_path):
    if not isinstance(rel_path, str) or not rel_path:
        return "rel_path must be a non empty str, got %r" % (rel_path,)
    if os.path.isabs(rel_path) or ".." in rel_path.replace("\\", "/").split("/"):
        return "rel_path %r leaves the vault" % rel_path
    return None


def forget_plans(vault, rel_paths, sib, con):
    """One bm_vault_retention.build_forget_plan result per path, in order. A
    path that is unreadable as an argument or raises inside the planner is
    {'rel_path', 'status': 'NO-DATA', 'reason'} in its place. Deletes
    nothing, writes no index; con is the caller's open index connection."""
    if not isinstance(vault, str) or not vault:
        raise ValueError("forget_plans: vault must be a non empty str, got %r"
                         % (vault,))
    if not isinstance(rel_paths, list):
        raise ValueError("forget_plans: rel_paths must be a list, got %r"
                         % type(rel_paths).__name__)
    if not isinstance(sib, dict):
        raise ValueError("forget_plans: sib must be a dict, got %r"
                         % type(sib).__name__)
    if con is None:
        raise ValueError("forget_plans: con is None, no retrieval index")
    plans = []
    for rel_path in rel_paths:
        problem = _plan_path_problem(vault, rel_path)
        if problem is None:
            try:
                plans.append(bm_vault_retention.build_forget_plan(
                    vault, rel_path, sib, con))
                continue
            except Exception as exc:  # the planner's failure is this path's NO-DATA
                problem = "%s: %s" % (type(exc).__name__, exc)
        plans.append({"rel_path": rel_path, "status": "NO-DATA",
                      "reason": problem})
    return plans

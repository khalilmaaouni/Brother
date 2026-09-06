#!/usr/bin/env python3
"""bm_vault_principals: the principal registry, and the offboarding proof (WBS row VB7-05).

WHY THIS EXISTS. bm_vault_policy.py (VB2-01) trims recall by identity against a set of
path rules, but it has no idea WHO an identity is or whether that identity is still
supposed to have any access at all. Nothing in the estate could ever say "this person or
agent is offboarded, revoke them" and have recall actually stop serving them. This module
is that registry, and it is the write side (mirrors bm_vault_promotions.py's shape for the
same reason: recording a state on a principal is a RECORDED HUMAN ACT, not a silent file
edit, so every mutation names who did it and dry-runs by default).

THE REGISTRY FILE, vault-relative 99-System/principals.json (overridable), sitting next to
99-System/access-policy.json because both are opt-in access-control state for the same
vault and a reader should find them side by side:

  {"principals": {
     "alice": {"kind": "human", "status": "active",
               "added_at": "2026-08-30", "added_by": "khalil",
               "recorded_at": "2026-08-30", "recorded_by": "khalil"}
  }}

kind is "human" or "agent". status is "active" or "revoked". added_at/added_by are set
once and NEVER changed after add (the entry's own history of when it first existed).
recorded_at/recorded_by are the most recent mutation (add, revoke or reactivate): who
recorded it and when. NEVER deletes an entry: revoke only ever flips status to "revoked",
reactivate only ever flips it back. A name once added stays in the file forever, exactly
the way bm_vault_lifecycle.py's promotion states only ever move forward or get corrected,
never erased.

OPT-IN, THE SAME SHAPE AS THE POLICY FILE. No registry file at all: nothing changes, every
identity behaves exactly as before this module existed. A registry file present but an
identity absent from it: also unchanged, because the registry only speaks for names it
actually knows. Only a name the registry KNOWS and marks "revoked" is ever denied. This
mirrors bm_vault_policy.py's own opt-in stance on purpose: two access-control files that
disagreed on when they apply would be worse than either alone.

IDENTITY NORMALIZATION (review MAJOR fix). Names are matched through normalize_identity,
the ONE owner function for this: NFKC-normalize, strip zero-width/joiner characters
(mirrors bm_vault_intake.py's own _normalized_view, same reason: a fullwidth homoglyph or
a zero-width-salted name must not evade matching), strip surrounding whitespace, then
casefold. Applied at BOTH registration (add) and lookup (status_of, revoke, reactivate),
so "alice", "Alice", "alice " and a fullwidth variant all resolve to the same registered
entry. add refuses, named, when a new name normalizes to the same identity as one already
registered; it never silently creates a second entry that a case-sensitive lookup would
have missed.

TRUST BOUNDARY (review MAJOR fix, stated plainly, no cryptography theater). This registry
is a JSON file. Any caller with vault write access can hand-edit it back to
{"status": "active"} with no recorded act at all, bypassing revoke/reactivate entirely and
leaving no trace this module can see. Registry integrity binds only when vault write
access is ITSELF controlled (a service-mode deployment where only this module's own
mutation path can touch the file). In single-machine mode, with no separate write control,
treat this registry exactly like the policy file: advisory against honest tools, not a
security boundary against a caller willing to edit the file directly.

The one cheap detective control this module DOES apply: an entry whose recorded_at or
recorded_by is missing or malformed (see _is_tamper_suspect) did not go through add,
revoke or reactivate, which always stamp both fields. list flags such an entry
TAMPER-SUSPECT rather than trusting it silently, and status_of fails CLOSED on one: a
tamper-suspect entry recorded "active" is treated as revoked by every consult (bm_vault.py
denies it exactly like a real revocation); a tamper-suspect "revoked" entry simply stays
revoked. This catches a careless or partial hand-edit, never a deliberate one that also
fabricates plausible recorded_at/recorded_by values; it is a tripwire, not a lock.

SIX COMMANDS. Every mutation (add, revoke, reactivate, set-role, contact) is dry run
unless --apply, and refuses to record without --by, the same posture
bm_vault_promotions.py already takes on promoting a note: a mutation that is not
recorded did not happen.
  add          registers a new name, with an optional --role (default DEFAULT_ROLE).
               Refuses if the name already exists (this is not an update path; use
               revoke/reactivate/set-role for an existing name).
  revoke       flips status to "revoked". No-op (writes nothing, exits 0) if already
               revoked. NO-DATA if the name was never added.
  reactivate   flips status back to "active". Same no-op and NO-DATA shape as revoke.
  set-role     records ROLES (reader, editor, steward, owner, VB10-03) on an existing
               principal, including one added before this field existed. Same no-op
               and NO-DATA shape as revoke/reactivate. RECORD-ONLY here: Entra enforces
               this vocabulary only at service mode, never on this machine.
  contact      records --email and/or --teams-identity (VB11-06, the notification
               delivery lane) on an existing principal, the same recorded-mutation
               shape set-role already gives roles: dry run unless --apply, refuses
               without --by, no-op if every given field already matches, NO-DATA if
               the name was never added. At least one of --email/--teams-identity is
               required. Neither value is validated against a live directory (no
               network here, per the estate's own rule): a typo'd address is caught
               only when a send later fails or is refused, never by this command.
  list         reports every principal (kind, status, role), optionally filtered by
               --status.

Exit 0 clean (including a same-state no-op). Exit 1 a refused mutation (name already
exists on add) or a findings report (a malformed registry). Exit 2 NO-DATA: no vault and
no --registry override, or the named principal does not exist for revoke/reactivate.
Python 3.9, standard library only, no network, no subprocess.

No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import unicodedata

REGISTRY_RELPATH = os.path.join("99-System", "principals.json")
KINDS = ("human", "agent")
STATUSES = ("active", "revoked")
# VB10-03: reader, editor, steward, owner. Entra enforces this vocabulary at
# service mode; here it is RECORD-ONLY, the same opt-in-advisory posture the
# module docstring already states for status. add defaults a new principal to
# DEFAULT_ROLE; set-role is the recorded-mutation path for an existing one
# (added before this field existed, or simply changing).
ROLES = ("reader", "editor", "steward", "owner")
DEFAULT_ROLE = "reader"
# VB11-06: the two contact fields the notification delivery lane reads a
# recipient's address from (tools/bm_vault_notify.py's send command). Record
# keys match the field names verbatim: "email" and "teams_identity".
CONTACT_FIELDS = ("email", "teams_identity")

# Mirrors bm_vault_intake.py's own _ZERO_WIDTH_RE: zero width space, zero
# width non-joiner, zero width joiner, word joiner, zero-width-no-break-space
# / BOM. A name salted with one of these must normalize the same as the
# plain name.
_ZERO_WIDTH_RE = re.compile(u"[​‌‍⁠﻿]")


def normalize_identity(name):
    """The ONE owner of identity normalization for this registry (review
    MAJOR fix): NFKC-normalize, strip zero-width/joiner characters, strip
    surrounding whitespace, then casefold. Used at registration time
    (cmd_add, via _find_key) AND lookup time (status_of, revoke,
    reactivate, via _find_key) so "Alice", "alice " and a fullwidth variant
    all resolve to whatever was actually registered. "" for falsy input,
    never raises."""
    if not name:
        return ""
    view = _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", name))
    return view.strip().casefold()


def _find_key(principals, name):
    """The actual registered key matching name once both sides run through
    normalize_identity, or None. Existing keys may predate this fix and
    carry mixed case; normalizing both sides is what lets revoke("Alice")
    find an entry recorded as "alice"."""
    target = normalize_identity(name)
    if not target:
        return None
    for key in principals:
        if normalize_identity(key) == target:
            return key
    return None


def _is_tamper_suspect(rec):
    """True when recorded_at/recorded_by are missing or malformed (review
    MAJOR fix, the cheap detective control described in the module
    docstring's TRUST BOUNDARY section). add/revoke/reactivate always stamp
    both fields with a real --by string and an ISO date, so an entry
    failing this check did not go through this module's own mutation
    path."""
    by = rec.get("recorded_by")
    if not isinstance(by, str) or not by.strip():
        return True
    at = rec.get("recorded_at")
    if not isinstance(at, str):
        return True
    try:
        datetime.date.fromisoformat(at)
    except ValueError:
        return True
    return False


def registry_path(vault, override=None):
    """The registry file's path: an explicit override, else vault-relative, else None
    when no vault is configured either. Same shape as bm_vault_policy.policy_path, on
    purpose: the two files are read the same way by every caller."""
    if override:
        return override
    if vault:
        return os.path.join(vault, REGISTRY_RELPATH)
    return None


def load(path):
    """(registry dict | None, problems list). None with no problems when the file is
    absent: the opt-in state, not an error. A present but unreadable or malformed file is
    (None, [reason]): a broken registry must never silently become "nobody is revoked"."""
    if not path or not os.path.isfile(path):
        return None, []
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as e:
        return None, ["unreadable principal registry %s: %s" % (path, e)]
    if not isinstance(loaded, dict) or not isinstance(loaded.get("principals", {}), dict):
        return None, ["principal registry %s is not a valid object" % path]
    return loaded, []


def status_of(registry, name):
    """"active", "revoked", or None when name is absent from the registry (or registry
    itself is None): the opt-in-per-identity contract described in the module docstring.
    A caller must treat None exactly like "no registry at all", never like "active".

    Lookup goes through _find_key (normalize_identity on both sides), so a caller passing
    "Alice" finds an entry actually registered as "alice" (review MAJOR fix). A
    tamper-suspect entry (see _is_tamper_suspect) recorded "active" is reported "revoked"
    instead: the consult path (bm_vault.py's _policy_deny) fails CLOSED on it exactly as
    it would a real revocation. A tamper-suspect "revoked" entry is unaffected, it is
    already the closed state."""
    if not registry or not name:
        return None
    principals = registry.get("principals", {})
    key = _find_key(principals, name)
    if key is None:
        return None
    rec = principals.get(key)
    if not isinstance(rec, dict):
        return None
    status = rec.get("status")
    if status == "active" and _is_tamper_suspect(rec):
        return "revoked"
    return status


def _write(path, registry):
    """Atomic write: temp file in the same directory, then os.replace over the target
    (same technique and same reason as bm_vault_promotions._atomic_write -- a corpus other
    sessions may read concurrently must never see a truncated file)."""
    dirname = os.path.dirname(path) or "."
    if dirname and not os.path.isdir(dirname):
        os.makedirs(dirname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bm-principals-", suffix=".tmp", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # sbe: allow-silent best-effort cleanup of the temp file before the original exception is re-raised on the next line
            pass
        raise


def cmd_add(path, name, kind, by, at, apply_changes, role=None):
    if not name:
        print("bm_vault_principals: add needs --name", file=sys.stderr)
        return 2
    if kind not in KINDS:
        print("bm_vault_principals: add needs --kind (%s)" % " or ".join(KINDS),
              file=sys.stderr)
        return 2
    if role is not None and role not in ROLES:
        print("bm_vault_principals: add's --role must be one of %s, got %r"
              % (" or ".join(ROLES), role), file=sys.stderr)
        return 2
    registry, problems = load(path)
    if problems:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    if registry is None:
        registry = {"principals": {}}
    principals = registry.setdefault("principals", {})
    existing_key = _find_key(principals, name)
    if existing_key is not None:
        if existing_key == name:
            print("REFUSED: %s is already registered (status %s); use revoke or "
                  "reactivate to change it, add is not an update path"
                  % (name, principals[existing_key].get("status", "UNKNOWN")))
        else:
            print("REFUSED: %s normalizes to the same identity as already-registered "
                  "%s (status %s); two names that collide after normalization refuse "
                  "at add rather than silently coexisting"
                  % (name, existing_key, principals[existing_key].get("status", "UNKNOWN")))
        return 1
    if not by:
        print("bm_vault_principals: add needs --by; a registration that is not "
              "recorded did not happen", file=sys.stderr)
        return 2
    principals[name] = {
        "kind": kind, "status": "active",
        "added_at": at, "added_by": by,
        "recorded_at": at, "recorded_by": by,
        "role": role or DEFAULT_ROLE,
    }
    print("%s %s: registered as %s, active, role %s"
          % ("would add" if not apply_changes else "added", name, kind,
             role or DEFAULT_ROLE))
    if apply_changes:
        _write(path, registry)
    else:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def _transition(path, name, new_status, by, at, apply_changes, verb, verbed):
    registry, problems = load(path)
    if problems:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    principals = registry.get("principals", {}) if registry else {}
    key = _find_key(principals, name)
    if key is None:
        print("bm_vault_principals: NO-DATA, %r is not a registered principal" % name,
              file=sys.stderr)
        return 2
    rec = principals[key]
    old_status = rec.get("status")
    if old_status == new_status:
        print("no-op: %s already %s; nothing written" % (key, new_status))
        return 0
    if not by:
        print("bm_vault_principals: %s needs --by; a %s that is not recorded "
              "did not happen" % (verb, verb), file=sys.stderr)
        return 2
    rec["status"] = new_status
    rec["recorded_at"] = at
    rec["recorded_by"] = by
    print("%s %s: %s -> %s"
          % ("would %s" % verb if not apply_changes else verbed, key, old_status, new_status))
    if apply_changes:
        _write(path, registry)
    else:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def cmd_revoke(path, name, by, at, apply_changes):
    return _transition(path, name, "revoked", by, at, apply_changes, "revoke", "revoked")


def cmd_reactivate(path, name, by, at, apply_changes):
    return _transition(path, name, "active", by, at, apply_changes, "reactivate", "reactivated")


def cmd_set_role(path, name, role, by, at, apply_changes):
    """Records a role for an EXISTING principal (VB10-03), the same recorded-mutation
    shape _transition already gives status: dry run unless --apply, refuses without
    --by, no-op if already that role, NO-DATA if the name was never added. This is not
    add's job (add only sets a role at registration time): a principal added before
    this field existed gets its role recorded through this path instead."""
    if role not in ROLES:
        print("bm_vault_principals: set-role needs --role (%s)" % " or ".join(ROLES),
              file=sys.stderr)
        return 2
    registry, problems = load(path)
    if problems:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    principals = registry.get("principals", {}) if registry else {}
    key = _find_key(principals, name)
    if key is None:
        print("bm_vault_principals: NO-DATA, %r is not a registered principal" % name,
              file=sys.stderr)
        return 2
    rec = principals[key]
    old_role = rec.get("role", DEFAULT_ROLE)
    if old_role == role:
        print("no-op: %s already role %s; nothing written" % (key, role))
        return 0
    if not by:
        print("bm_vault_principals: set-role needs --by; a role change that is not "
              "recorded did not happen", file=sys.stderr)
        return 2
    rec["role"] = role
    rec["recorded_at"] = at
    rec["recorded_by"] = by
    print("%s %s: role %s -> %s"
          % ("would set" if not apply_changes else "set", key, old_role, role))
    if apply_changes:
        _write(path, registry)
    else:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def cmd_contact(path, name, by, at, apply_changes, email=None, teams_identity=None):
    """Records --email and/or --teams-identity for an EXISTING principal (VB11-06),
    the same recorded-mutation shape cmd_set_role already gives roles: dry run
    unless --apply, refuses without --by, no-op if every given field already
    matches, NO-DATA if the name was never added. At least one field is required
    (checked by main() before this is called)."""
    registry, problems = load(path)
    if problems:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    principals = registry.get("principals", {}) if registry else {}
    key = _find_key(principals, name)
    if key is None:
        print("bm_vault_principals: NO-DATA, %r is not a registered principal" % name,
              file=sys.stderr)
        return 2
    rec = principals[key]
    changes = {}
    if email is not None and rec.get("email") != email:
        changes["email"] = email
    if teams_identity is not None and rec.get("teams_identity") != teams_identity:
        changes["teams_identity"] = teams_identity
    if not changes:
        print("no-op: %s already carries the given contact field(s); nothing written"
              % key)
        return 0
    if not by:
        print("bm_vault_principals: contact needs --by; a contact update that is "
              "not recorded did not happen", file=sys.stderr)
        return 2
    for field, value in changes.items():
        old = rec.get(field, "UNSET")
        print("%s %s: %s %s -> %s"
              % ("would set" if not apply_changes else "set", key, field, old, value))
        rec[field] = value
    rec["recorded_at"] = at
    rec["recorded_by"] = by
    if apply_changes:
        _write(path, registry)
    else:
        print("dry run: nothing was written. Re-run with --apply to write.")
    return 0


def cmd_list(path, status_filter):
    registry, problems = load(path)
    if problems:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    if registry is None:
        print("bm_vault_principals: NO-DATA, no registry at %r" % path)
        return 2
    principals = registry.get("principals", {})
    names = sorted(principals)
    if status_filter:
        names = [n for n in names if principals[n].get("status") == status_filter]
    print("%d principal(s)" % len(names))
    for n in names:
        rec = principals[n]
        tag = " TAMPER-SUSPECT" if _is_tamper_suspect(rec) else ""
        # VB11-06: printed only when set, so every listing predating contact
        # fields (and every principal nobody has recorded contact info for
        # yet) keeps its exact prior line shape.
        contact_bits = ""
        if rec.get("email"):
            contact_bits += " email=%s" % rec["email"]
        if rec.get("teams_identity"):
            contact_bits += " teams_identity=%s" % rec["teams_identity"]
        print("  %s: kind=%s status=%s role=%s recorded_by=%s recorded_at=%s%s%s"
              % (n, rec.get("kind"), rec.get("status"), rec.get("role", "UNSET"),
                 rec.get("recorded_by"), rec.get("recorded_at"), contact_bits, tag))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command",
                     choices=("add", "revoke", "reactivate", "list", "set-role", "contact"))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT")
                     or os.environ.get("BROTHERMODE_VAULT"))
    ap.add_argument("--registry", default=None,
                     help="override path, same purpose as bm_vault_policy's --policy")
    ap.add_argument("--name", help="the principal's name")
    ap.add_argument("--kind", choices=KINDS, help="for add")
    ap.add_argument("--role", choices=ROLES,
                     help="for add (optional, defaults to %s) or set-role (required)"
                          % DEFAULT_ROLE)
    ap.add_argument("--email", default=None, help="for contact")
    ap.add_argument("--teams-identity", dest="teams_identity", default=None,
                     help="for contact")
    ap.add_argument("--by", help="who is recording this act")
    ap.add_argument("--at", default=None, help="ISO date; defaults to today")
    ap.add_argument("--status", choices=STATUSES, help="for list: filter by status")
    ap.add_argument("--apply", action="store_true", help="actually write; default dry run")
    args = ap.parse_args(argv)
    path = registry_path(args.vault, args.registry)
    if not path:
        print("bm_vault_principals: NO-DATA, no vault and no --registry override",
              file=sys.stderr)
        return 2
    at = args.at or datetime.date.today().isoformat()
    if args.command == "list":
        return cmd_list(path, args.status)
    if not args.name:
        ap.error("%s needs --name" % args.command)
    if args.command == "add":
        return cmd_add(path, args.name, args.kind, args.by, at, args.apply, args.role)
    if args.command == "revoke":
        return cmd_revoke(path, args.name, args.by, at, args.apply)
    if args.command == "set-role":
        if not args.role:
            ap.error("set-role needs --role")
        return cmd_set_role(path, args.name, args.role, args.by, at, args.apply)
    if args.command == "contact":
        if not args.email and not args.teams_identity:
            ap.error("contact needs --email and/or --teams-identity")
        return cmd_contact(path, args.name, args.by, at, args.apply,
                            args.email, args.teams_identity)
    return cmd_reactivate(path, args.name, args.by, at, args.apply)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""WBS-60.01 Vault convergence, One Vault domain tags.

Roadmap text (BROTHER_1.0.17_CONVERGENCE_ROADMAP_2026-09-14.md, WBS-60.01,
"One Vault, domain tags"):

    Do not create:
    - Mobile Vault;
    - MDM Vault;
    - DS Vault.

    Use one Vault with metadata/tags:
    - `core`;
    - `mobile`;
    - `mdm`;
    - `brotherds`;
    - host;
    - component;
    - symptom;
    - failure mode;
    - evidence ID;
    - claim ID;
    - accepted revision.

Two things this module enforces, matching the two halves above:

  ONE VAULT, NOT FOUR. check_no_split_vault(vault_root) refuses a
  directory sitting beside the real Vault that is literally named for a
  per-domain vault ("Mobile Vault", "mdm-vault", ...) -- the concrete,
  checkable form of "do not create a Mobile Vault", since the sentence
  itself is not something code can verify.

  THE TAG VOCABULARY. validate_tags(record) checks a note's tag record
  carries at least one domain tag from the closed set (core, mobile,
  mdm, brotherds) and that every other field present is one of the
  roadmap's named metadata fields (host, component, symptom,
  failure_mode, evidence_id, claim_id, accepted_revision). An unlisted
  field is refused rather than silently accepted, so the vocabulary the
  roadmap names cannot quietly drift as new tag names get invented.

No dedicated docs/schema/*.json for this module: the roadmap's WBS-60.01
text names a flat tag vocabulary, not a record with its own lifecycle, so
this follows lesson_severity.py's shape (a plain validator plus CLI)
rather than contract_check.py's schema-file pattern.

Python 3.9, standard library only, no network.
"""
import argparse
import json
import os
import sys

#: The roadmap's own closed set. Kept as a tuple, not a set, so error
#: messages print it in the same order the roadmap lists it.
DOMAIN_TAGS = ("core", "mobile", "mdm", "brotherds")

#: The roadmap's other named metadata fields, snake_cased from its prose
#: ("failure mode" -> failure_mode, "evidence ID" -> evidence_id,
#: "claim ID" -> claim_id, "accepted revision" -> accepted_revision).
METADATA_FIELDS = (
    "host", "component", "symptom", "failure_mode", "evidence_id",
    "claim_id", "accepted_revision",
)

ALLOWED_FIELDS = frozenset(("domain_tags",) + METADATA_FIELDS)

#: A directory literally named for one domain's own vault is the
#: concrete form of "create a Mobile Vault" the roadmap forbids.
#: "brotherds" and its "ds" contraction both count, since the roadmap
#: uses "DS Vault" in the forbidden list but "brotherds" in the tag set.
FORBIDDEN_VAULT_NAMES = frozenset(
    "%s-vault" % domain for domain in ("mobile", "mdm", "ds", "brotherds")
)


def _normalize(name):
    """Case/space/underscore-insensitive basename, so "Mobile Vault",
    "mobile_vault" and "mobile-vault" all match the same forbidden name."""
    return name.strip().lower().replace(" ", "-").replace("_", "-")


def check_no_split_vault(vault_root):
    """Problems, if any sibling of vault_root is named for a forbidden
    per-domain vault. vault_root itself is also checked. Neither
    vault_root nor its parent needs to exist: nothing to check is not a
    problem, it is NO-DATA-shaped and returns no problems."""
    problems = []
    if not vault_root:
        return problems
    root_name = os.path.basename(os.path.abspath(vault_root.rstrip(os.sep)))
    if _normalize(root_name) in FORBIDDEN_VAULT_NAMES:
        problems.append(
            "%s: this path's own name is a forbidden per-domain vault name"
            % vault_root)
    parent = os.path.dirname(os.path.abspath(vault_root.rstrip(os.sep)))
    if not os.path.isdir(parent):
        return problems
    for entry in sorted(os.listdir(parent)):
        if _normalize(entry) in FORBIDDEN_VAULT_NAMES:
            problems.append(
                "%s: a directory named for a per-domain vault sits beside "
                "%s; the roadmap requires one Vault with domain tags, never "
                "a separate Mobile/MDM/DS Vault"
                % (os.path.join(parent, entry), vault_root))
    return problems


def validate_tags(record):
    """Problems, if any, with a note's tag record. `record` must be a
    dict; domain_tags must be a non-empty list drawn only from
    DOMAIN_TAGS (more than one domain tag is allowed: the roadmap tags a
    note with metadata, it does not say a note belongs to exactly one
    domain), and every other key must be one of METADATA_FIELDS."""
    if not isinstance(record, dict):
        return ["record: must be a JSON object, got %s" % type(record).__name__]
    problems = []
    tags = record.get("domain_tags")
    if not isinstance(tags, list) or not tags:
        problems.append("domain_tags: required, must be a non-empty list")
    else:
        for tag in tags:
            if tag not in DOMAIN_TAGS:
                problems.append(
                    "domain_tags: %r is not one of %s" % (tag, DOMAIN_TAGS))
    for key in record:
        if key not in ALLOWED_FIELDS:
            problems.append(
                "%s: not one of the roadmap's named tag fields (%s)"
                % (key, ", ".join(sorted(ALLOWED_FIELDS))))
    return problems


def run_selftest():
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: expected %r, got %r" % (name, want, got))

    check("valid single-domain record",
          validate_tags({"domain_tags": ["mobile"], "component": "x"}), [])
    check("multi-domain record allowed",
          validate_tags({"domain_tags": ["core", "mobile"]}), [])
    check("empty domain_tags is refused",
          bool(validate_tags({"domain_tags": []})), True)
    check("missing domain_tags is refused",
          bool(validate_tags({"component": "x"})), True)
    check("unknown domain tag is refused",
          bool(validate_tags({"domain_tags": ["ios"]})), True)
    check("unlisted field is refused",
          bool(validate_tags({"domain_tags": ["core"], "made_up": 1})), True)
    check("non-dict record is refused",
          bool(validate_tags(["not", "a", "dict"])), True)

    import shutil
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        vault = os.path.join(tmp, "Kay Vault")
        os.makedirs(vault)
        check("clean sibling directory, no problem",
              check_no_split_vault(vault), [])
        os.makedirs(os.path.join(tmp, "Mobile Vault"))
        check("a sibling 'Mobile Vault' directory is caught",
              len(check_no_split_vault(vault)), 1)
        check("the vault path itself named 'mdm-vault' is caught",
              bool(check_no_split_vault(os.path.join(tmp, "mdm-vault"))), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for msg in failures:
        print("selftest:", msg)
    return not failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tags", help="JSON object to validate as a tag record")
    ap.add_argument("--vault-root",
                     help="check no per-domain vault directory sits beside this path")
    ap.add_argument("--selftest", action="store_true",
                     help="run the built-in self check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        if run_selftest():
            print("vault_domain_tags.py selftest: PASS")
            return 0
        print("vault_domain_tags.py selftest: FAIL", file=sys.stderr)
        return 1

    if not args.tags and not args.vault_root:
        ap.error("one of --tags, --vault-root or --selftest is required")

    problems = []
    if args.tags:
        try:
            record = json.loads(args.tags)
        except ValueError as exc:
            print("NO-DATA: could not read --tags: %s" % exc, file=sys.stderr)
            return 2
        problems.extend(validate_tags(record))
    if args.vault_root:
        problems.extend(check_no_split_vault(args.vault_root))

    if problems:
        print("FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" -", p)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

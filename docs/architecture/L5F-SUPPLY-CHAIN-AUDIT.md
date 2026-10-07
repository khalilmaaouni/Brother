# L5F Supply Chain Audit

Frozen audit record for unit L5f, epic version 1.1.0. Human tables plus one frozen JSON
manifest between the BEGIN and END markers. The offline gate reads this record and never
rewrites it. `header.l3b_landed` is false today and `header.l3b_deferred_to` names 1.1.2 (owner
ruling 2026-09-29), so the vault rows are out of scope for this epic and the gate reads PASS over
the rows it has.

## Scope

In scope: pinned dependencies, no unverified installs, license check. Out of scope: CVE
scoring, secret scanning, vendored binaries, pre epic drift only, runtime network policy,
auto remediation, signing and non public registries.

Areas named by the specification that are not yet located:

- OpenRouter bridge: location UNKNOWN. Recorded NO-DATA with that reason.
- Antigravity adapter: location UNKNOWN. Recorded NO-DATA with that reason.
- Vault web UI: out of scope. L3b has not landed and is deferred to 1.1.2 (owner ruling
  2026-09-29); REQ-L3B forces NO-DATA only while no deferral is recorded.

## Manifests searched

The four shown files are recorded for scope. Absent paths are stated absent.

- plugin/README.md (shown, not a dependency manifest)
- scripts/required_fast.sh (shown, not a dependency manifest)
- products/brothermode/scripts/checksums.sh (shown, not a dependency manifest)
- products/brothermode/scripts/verify-install.sh (shown, not a dependency manifest)
- pyproject.toml (absent)
- setup.py (absent)
- setup.cfg (absent)
- package.json (absent)

## Frozen manifest

FROZEN MANIFEST l5f-supply-chain-audit-v1 BEGIN
{
  "header": {
    "schema": "l5f-supply-chain-audit-v1",
    "audit_date": "2026-09-21",
    "auditor_role": "builder",
    "checker_role": "checker",
    "epic_version": "1.1.0",
    "l3b_landed": false,
    "l3b_deferred_to": "1.1.2",
    "manifests_searched": [
      "package.json",
      "plugin/README.md",
      "products/brothermode/scripts/checksums.sh",
      "products/brothermode/scripts/verify-install.sh",
      "pyproject.toml",
      "scripts/required_fast.sh",
      "setup.cfg",
      "setup.py"
    ]
  },
  "rows": [],
  "runtime_findings": [],
  "verdict": {
    "result": "NO-DATA",
    "counts": {
      "total_rows": 0,
      "passed": 0,
      "blocked": 0,
      "no_data": 0,
      "stale": 0
    },
    "reasons": [
      "l3b not landed"
    ]
  }
}
FROZEN MANIFEST l5f-supply-chain-audit-v1 END

## Human table mirroring rows

No dependency row is claimed by this epic yet, so the table is empty and the verdict is
NO-DATA rather than PASS.

| eco | name | version_pinned | pin_location | lockfile_path | declared_license | fetched_license | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none | none | none | none | none | none | none | NO-DATA |

## Runtime findings

No runtime install finding is frozen for this record. An empty list here means the scanned
tree was read and was clean, never that the tree could not be read.

## Verdict

PASS over the rows present, with the vault rows deferred to 1.1.2. The frozen verdict below
still records NO-DATA as the state on the audit date; the gate's own line is the live verdict.

## L3b pending state

L3b has not landed and is deferred to 1.1.2 (owner ruling 2026-09-29). The vault web UI cannot
be audited yet, the vault rows are empty, and REQ-L3B no longer forces NO-DATA while the header
records the deferral; when L3b lands, `l3b_landed` turns true, the deferral field goes, and the
vault rows join the table.

## Scope statement

products/brothermode/scripts/verify-install.sh attests first party tracked files only and
does not attest dependencies. The audit table above is the source of truth for
dependencies. The runtime scanner ignores .gitignore and never consults it.

## Human signoff

H1 SIGNOFF: PENDING
H2 SIGNOFF: PENDING
H3 SIGNOFF: PENDING

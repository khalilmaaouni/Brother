#!/usr/bin/env python3
# enterprise_doctor.py - READ ONLY report of enterprise controls
# This script never writes files, never runs mutating git commands, never uses the network

import argparse
import json
import re
import sys
from pathlib import Path

# Status constants
PRESENT = "PRESENT"
ABSENT = "ABSENT"
NODATA = "NO-DATA"

REASON_SANDBOX = "no sandbox contract in this repository; Brother relies on the host's sandbox (see docs/reference/safety-boundaries.md)"
REASON_FAIL_CLOSED = "some developer-mode hooks fail open on an internal error (docs/reference/safety-boundaries.md); no enforcement mode exists yet"

HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")

def check_security_policy(repo: Path):
    p = repo / "SECURITY.md"
    decided = str(p)
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            if text.strip() != "":
                return PRESENT, decided
            return ABSENT, decided
        except Exception:
            return NODATA, decided
    return ABSENT, decided

def check_code_owners(repo: Path):
    p = repo / ".github" / "CODEOWNERS"
    decided = str(p)
    if p.is_file():
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return NODATA, decided
        has_non_comment = False
        for line in lines:
            stripped = line.lstrip()
            if stripped == "" or stripped.startswith("#"):
                continue
            has_non_comment = True
            parts = stripped.split()
            if len(parts) >= 2 and any(part.startswith("@") for part in parts[1:]):
                return PRESENT, decided
        if has_non_comment:
            return NODATA, decided
        return ABSENT, decided
    return ABSENT, decided

def check_evidence_obligation(repo: Path):
    p = repo / "scripts" / "gate_obligations.json"
    decided = str(p)
    if not p.is_file():
        return ABSENT, decided
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return NODATA, decided
    if isinstance(data, dict) and "default" in data:
        return PRESENT, decided
    return ABSENT, decided

def check_obligation_wired(repo: Path):
    p = repo / "scripts" / "required_fast.sh"
    decided = str(p)
    if not p.is_file():
        return NODATA, decided
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return NODATA, decided
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        if "evidence_obligation.py transition" in line:
            return PRESENT, decided
    return ABSENT, decided

def check_signed_receipts(repo: Path):
    p = repo / "scripts" / "receipt_attest.py"
    decided = str(p)
    if not p.is_file():
        return ABSENT, decided
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return NODATA, decided
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        # require a line defining a verify subcommand, for example a string "verify" passed to add_parser or compared as subcommand name
        has_quoted_verify = ('"verify"' in line or "'verify'" in line)
        if has_quoted_verify and ("add_parser" in line or "==" in line or "!=" in line or "subcommand" in line):
            return PRESENT, decided
    return ABSENT, decided

def check_signing_identities(repo: Path):
    pattern = repo / "products" / "*" / "scripts" / "allowed_signers"
    files = list(repo.glob("products/*/scripts/allowed_signers"))
    decided_pattern = str(repo / "products" / "*" / "scripts" / "allowed_signers")
    if not files:
        return ABSENT, decided_pattern
    first_malformed = None
    has_malformed = False
    for f in sorted(files):
        if not f.is_file():
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return NODATA, str(f)
        file_has_non_comment = False
        for line in lines:
            s = line.strip()
            if s == "" or s.startswith("#"):
                continue
            file_has_non_comment = True
            fields = s.split()
            if len(fields) >= 3:
                second = fields[1]
                third = fields[2]
                if second.startswith("ssh-") or second.startswith("ecdsa-") or second.startswith("sk-") or third.startswith("ssh-") or third.startswith("ecdsa-") or third.startswith("sk-"):
                    return PRESENT, str(f)
        if file_has_non_comment:
            has_malformed = True
            if first_malformed is None:
                first_malformed = str(f)
    if has_malformed:
        return NODATA, first_malformed
    first = str(sorted(files)[0]) if files else decided_pattern
    return ABSENT, first

def check_pinned_ci(repo: Path):
    workflows_dir = repo / ".github" / "workflows"
    decided_no_data = str(workflows_dir)
    if not workflows_dir.is_dir():
        return NODATA, decided_no_data
    yml_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    if not yml_files:
        return PRESENT, decided_no_data
    uses_key_re = re.compile(r'["\']?uses["\']?\s*:')
    uses_value_re = re.compile(r'["\']?uses["\']?\s*:\s*["\']?([^\s"\'#]+)')
    for wf in yml_files:
        try:
            lines = wf.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return NODATA, str(wf)
        for line in lines:
            if not uses_key_re.search(line):
                continue
            m = uses_value_re.search(line)
            if not m:
                continue
            token = m.group(1).strip().strip('"').strip("'")
            if "@" not in token:
                return ABSENT, str(wf)
            sha = token.split("@", 1)[1]
            sha = sha.strip().strip('"').strip("'")
            if not HEX40_RE.match(sha):
                return ABSENT, str(wf)
    return PRESENT, decided_no_data

def check_ci_permissions(repo: Path):
    workflows_dir = repo / ".github" / "workflows"
    decided_no_data = str(workflows_dir)
    if not workflows_dir.is_dir():
        return NODATA, decided_no_data
    yml_files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    if not yml_files:
        return PRESENT, decided_no_data
    for wf in yml_files:
        try:
            text = wf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return NODATA, str(wf)
        if "permissions:" not in text:
            return ABSENT, str(wf)
    return PRESENT, decided_no_data

def evaluate(repo: Path):
    controls = []
    s, d = check_security_policy(repo)
    controls.append({"control": "security-policy", "status": s, "decided_by": d})
    s, d = check_code_owners(repo)
    controls.append({"control": "code-owners", "status": s, "decided_by": d})
    s, d = check_evidence_obligation(repo)
    controls.append({"control": "evidence-obligation", "status": s, "decided_by": d})
    s, d = check_obligation_wired(repo)
    controls.append({"control": "obligation-wired", "status": s, "decided_by": d})
    s, d = check_signed_receipts(repo)
    controls.append({"control": "signed-receipts", "status": s, "decided_by": d})
    s, d = check_signing_identities(repo)
    controls.append({"control": "signing-identities", "status": s, "decided_by": d})
    s, d = check_pinned_ci(repo)
    controls.append({"control": "pinned-ci", "status": s, "decided_by": d})
    s, d = check_ci_permissions(repo)
    controls.append({"control": "ci-permissions", "status": s, "decided_by": d})
    controls.append({"control": "os-sandbox", "status": NODATA, "decided_by": REASON_SANDBOX})
    controls.append({"control": "egress-policy", "status": NODATA, "decided_by": "no egress policy in this repository; network limits come from the host, if it sets any"})
    controls.append({"control": "delegated-identity", "status": NODATA, "decided_by": "no delegated agent identity in this repository; agents act with the developer's own credentials"})
    controls.append({"control": "central-audit-store", "status": NODATA, "decided_by": "receipts are kept per machine; no central append-only store or SIEM export exists yet"})
    controls.append({"control": "fail-closed-mode", "status": NODATA, "decided_by": REASON_FAIL_CLOSED})
    return controls

def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--repo", dest="repo", default=None, help="path to repository")
    parser.add_argument("--json", dest="json_out", action="store_true", help="output JSON")
    args = parser.parse_args()

    if args.repo is None:
        repo = Path(__file__).resolve().parent.parent
    else:
        repo = Path(args.repo)

    if not repo.exists() or not repo.is_dir():
        print(f"error: --repo does not exist or is not a directory: {repo}", file=sys.stderr)
        sys.exit(2)

    controls = evaluate(repo)

    present = sum(1 for c in controls if c["status"] == PRESENT)
    absent = sum(1 for c in controls if c["status"] == ABSENT)
    nodata = sum(1 for c in controls if c["status"] == NODATA)

    if args.json_out:
        out = {
            "controls": controls,
            "counts": {
                "present": present,
                "absent": absent,
                "no-data": nodata
            }
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        col1_w = max(len("CONTROL"), max(len(c["control"]) for c in controls))
        col2_w = max(len("STATUS"), max(len(c["status"]) for c in controls))
        header = f"{'CONTROL':<{col1_w}}  {'STATUS':<{col2_w}}  DECIDED BY"
        print(header)
        print(f"{'-'*col1_w}  {'-'*col2_w}  {'-'*10}")
        for c in controls:
            print(f"{c['control']:<{col1_w}}  {c['status']:<{col2_w}}  {c['decided_by']}")
        print(f"present: {present}  absent: {absent}  no-data: {nodata}  (a count, not a score; NO-DATA is not a pass)")

    sys.exit(0)

if __name__ == "__main__":
    main()

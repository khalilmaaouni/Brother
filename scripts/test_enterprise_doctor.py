import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# locate doctor script relative to this test file
SCRIPT = Path(__file__).resolve().parent / "enterprise_doctor.py"

def run_doctor(repo_path, extra_args=None):
    cmd = [sys.executable, str(SCRIPT), "--repo", str(repo_path)]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def snapshot_dir(root: Path):
    items = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root)
            items.add(str(rel))
        for name in dirnames:
            p = Path(dirpath) / name
            rel = p.relative_to(root)
            # include dirs as well to detect new dirs
            items.add(str(rel) + "/")
    return items

def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

class TestEnterpriseDoctor(unittest.TestCase):

    def test_empty_repo_gives_absent_for_1_to_6_and_no_data_for_7_to_13(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            for name in ["security-policy", "code-owners", "evidence-obligation", "signed-receipts", "signing-identities"]:
                self.assertEqual(controls[name]["status"], "ABSENT", msg=name)
            self.assertEqual(controls["obligation-wired"]["status"], "NO-DATA", msg="no required_fast.sh to read")
            for name in ["pinned-ci", "ci-permissions", "os-sandbox", "egress-policy", "delegated-identity", "central-audit-store", "fail-closed-mode"]:
                self.assertEqual(controls[name]["status"], "NO-DATA", msg=name)

    def test_repo_with_security_codeowners_gate_and_wired_gives_present_1_to_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "SECURITY.md", "policy here")
            write_file(repo / ".github" / "CODEOWNERS", "* @owner\n")
            write_file(repo / "scripts" / "gate_obligations.json", json.dumps({"default": {"evidence": True}}))
            write_file(repo / "scripts" / "required_fast.sh", "#!/bin/sh\nevidence_obligation.py transition\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["security-policy"]["status"], "PRESENT")
            self.assertEqual(controls["code-owners"]["status"], "PRESENT")
            self.assertEqual(controls["evidence-obligation"]["status"], "PRESENT")
            self.assertEqual(controls["obligation-wired"]["status"], "PRESENT")

    def test_pinned_ci_absent_when_using_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wf = repo / ".github" / "workflows" / "ci.yml"
            write_file(wf, "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    permissions: read-all\n    steps:\n      - uses: actions/checkout@v4\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["pinned-ci"]["status"], "ABSENT")
            # must name offender file
            self.assertIn("ci.yml", controls["pinned-ci"]["decided_by"])

    def test_pinned_ci_present_when_using_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = "a" * 40
            wf = repo / ".github" / "workflows" / "ci.yml"
            write_file(wf, f"name: ci\non: push\npermissions: read-all\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@{sha}\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["pinned-ci"]["status"], "PRESENT")

    def test_unparseable_gate_obligations_gives_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "scripts" / "gate_obligations.json", "{ not json }")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["evidence-obligation"]["status"], "NO-DATA")

    def test_json_parses_and_counts_sum_to_13(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            self.assertIn("controls", data)
            self.assertIn("counts", data)
            self.assertEqual(len(data["controls"]), 13)
            total = sum(data["counts"].values())
            self.assertEqual(total, 13)
            # also check counts match actual statuses
            present = sum(1 for c in data["controls"] if c["status"] == "PRESENT")
            absent = sum(1 for c in data["controls"] if c["status"] == "ABSENT")
            nodata = sum(1 for c in data["controls"] if c["status"] == "NO-DATA")
            self.assertEqual(data["counts"]["present"], present)
            self.assertEqual(data["counts"]["absent"], absent)
            self.assertEqual(data["counts"]["no-data"], nodata)

    def test_missing_repo_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "does-not-exist"
            result = run_doctor(repo)
            self.assertEqual(result.returncode, 2)
            # also test when path is a file not directory
            f = Path(tmp) / "a-file"
            write_file(f, "hi")
            result2 = run_doctor(f)
            self.assertEqual(result2.returncode, 2)

    def test_output_never_contains_score_or_percent_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # create a simple repo to generate output
            write_file(repo / "SECURITY.md", "x")
            result = run_doctor(repo)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            stdout = result.stdout
            # remove the allowed count line phrase before checking for stray score/percent
            allowed = "(a count, not a score; NO-DATA is not a pass)"
            cleaned = stdout.replace(allowed, "")
            self.assertNotIn("score", cleaned.lower())
            self.assertNotIn("%", cleaned)
            # also check json mode does not contain score or percent in unexpected places
            result_json = run_doctor(repo, ["--json"])
            self.assertEqual(result_json.returncode, 0)
            cleaned_json = result_json.stdout.replace(allowed, "")
            # json output should not contain the word score at all (counts key is fine)
            # we allow the word score only if it is part of allowed phrase, which json does not have
            # so check json has no score except maybe not present
            # json counts contain present/absent/no-data but not score word
            self.assertNotIn("score", cleaned_json.lower())
            self.assertNotIn("%", cleaned_json)

    def test_doctor_wrote_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "SECURITY.md", "content")
            write_file(repo / ".github" / "CODEOWNERS", "* @owner")
            before = snapshot_dir(repo)
            result = run_doctor(repo)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            after = snapshot_dir(repo)
            self.assertEqual(before, after, msg=f"directory listing changed: before {before} after {after}")
            # also check json mode does not write
            before2 = snapshot_dir(repo)
            result2 = run_doctor(repo, ["--json"])
            self.assertEqual(result2.returncode, 0)
            after2 = snapshot_dir(repo)
            self.assertEqual(before2, after2)

    def test_default_repo_is_parent_of_script_directory(self):
        # this just checks that running without --repo does not exit 2
        # when the repository is readable it should exit 0
        result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        # if the parent of scripts is a readable repo, exit 0, else 2 is also acceptable for this test
        # but we can at least assert it does not crash
        self.assertIn(result.returncode, (0, 2))

    # Defect 1: pinned-ci must detect quoted uses key and require 40 hex SHA
    def test_defect1_pinned_ci_quoted_uses_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wf = repo / ".github" / "workflows" / "ci.yml"
            write_file(wf, '"uses": "actions/checkout@v4"\n')
            # also add permissions so ci-permissions does not interfere
            # but pinned-ci should still be ABSENT because SHA is not 40 hex
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["pinned-ci"]["status"], "ABSENT")
            self.assertIn("ci.yml", controls["pinned-ci"]["decided_by"])

    # Defect 2: obligation-wired counts only non-comment lines
    def test_defect2_obligation_wired_comment_only_is_not_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "scripts" / "required_fast.sh", "# evidence_obligation.py transition\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertNotEqual(controls["obligation-wired"]["status"], "PRESENT")
            self.assertEqual(controls["obligation-wired"]["status"], "ABSENT")

    # Defect 3: signed-receipts counts only non-comment lines and requires verify subcommand definition
    def test_defect3_signed_receipts_comment_only_is_not_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "scripts" / "receipt_attest.py", "# verify\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertNotEqual(controls["signed-receipts"]["status"], "PRESENT")
            self.assertEqual(controls["signed-receipts"]["status"], "ABSENT")

    # Defect 4: signing-identities malformed line gives NO-DATA
    def test_defect4_signing_identities_malformed_gives_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / "products" / "foo" / "scripts" / "allowed_signers", "garbage\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["signing-identities"]["status"], "NO-DATA")

    # Defect 5: code-owners malformed gives NO-DATA
    def test_defect5_code_owners_malformed_gives_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_file(repo / ".github" / "CODEOWNERS", "!! not a codeowner\n")
            result = run_doctor(repo, ["--json"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            data = json.loads(result.stdout)
            controls = {c["control"]: c for c in data["controls"]}
            self.assertEqual(controls["code-owners"]["status"], "NO-DATA")

    # Defect 6: security-policy unreadable gives NO-DATA
    def test_defect6_security_policy_unreadable_gives_no_data(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root cannot test unreadable files")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            p = repo / "SECURITY.md"
            write_file(p, "policy content")
            os.chmod(p, 0)
            try:
                result = run_doctor(repo, ["--json"])
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                data = json.loads(result.stdout)
                controls = {c["control"]: c for c in data["controls"]}
                self.assertEqual(controls["security-policy"]["status"], "NO-DATA")
            finally:
                os.chmod(p, 0o644)

    # Defect 7: pinned-ci unreadable workflow gives NO-DATA
    def test_defect7_pinned_ci_unreadable_gives_no_data(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root cannot test unreadable files")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wf = repo / ".github" / "workflows" / "ci.yml"
            write_file(wf, "name: ci\non: push\npermissions: read-all\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
            os.chmod(wf, 0)
            try:
                result = run_doctor(repo, ["--json"])
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                data = json.loads(result.stdout)
                controls = {c["control"]: c for c in data["controls"]}
                self.assertEqual(controls["pinned-ci"]["status"], "NO-DATA")
                self.assertIn("ci.yml", controls["pinned-ci"]["decided_by"])
            finally:
                os.chmod(wf, 0o644)

    # Defect 8: ci-permissions unreadable workflow gives NO-DATA
    def test_defect8_ci_permissions_unreadable_gives_no_data(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root cannot test unreadable files")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wf = repo / ".github" / "workflows" / "ci.yml"
            write_file(wf, "name: ci\non: push\npermissions: read-all\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
            os.chmod(wf, 0)
            try:
                result = run_doctor(repo, ["--json"])
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                data = json.loads(result.stdout)
                controls = {c["control"]: c for c in data["controls"]}
                self.assertEqual(controls["ci-permissions"]["status"], "NO-DATA")
                self.assertIn("ci.yml", controls["ci-permissions"]["decided_by"])
            finally:
                os.chmod(wf, 0o644)

if __name__ == "__main__":
    unittest.main()

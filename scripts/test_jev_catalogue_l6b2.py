import json
import os
import re
import unittest

ALLOWED_DOMAINS = {
    "BrotherMode",
    "BrotherSBE",
    "BrotherDS",
    "Mobile",
    "Vault",
    "OpenRouter",
}

QID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

FORBIDDEN_OUTPUT_MARKERS = (
    "fatal:",
    "error:",
    "traceback",
    "command not found",
    "no such file",
    "permission denied",
    "not a git repository",
    "usage:",
    "errno",
    "exception",
)

def propose_candidate(domain, state_cmd, qid):
    try:
        if not isinstance(domain, str) or not isinstance(state_cmd, str) or not isinstance(qid, str):
            return {}
        if domain not in ALLOWED_DOMAINS:
            return {}
        if not state_cmd.strip():
            return {}
        if not QID_RE.match(qid):
            return {}
        return {
            "qid": qid,
            "domain": domain,
            "state_cmd": state_cmd,
            "status": "proposed",
        }
    except TypeError:
        return {}

def check_distinct(new_q, bank):
    try:
        if not isinstance(new_q, dict) or not isinstance(bank, list):
            return False
        new_qid = new_q.get("qid")
        new_true = new_q.get("true_criteria")
        new_false = new_q.get("false_criteria")
        if not isinstance(new_qid, str) or not isinstance(new_true, str) or not isinstance(new_false, str):
            return False
        if not QID_RE.match(new_qid):
            return False
        if not new_true.strip() or not new_false.strip():
            return False
        seen_qids = set()
        seen_trues = set()
        seen_falses = set()
        for item in bank:
            if not isinstance(item, dict):
                return False
            item_qid = item.get("qid")
            item_true = item.get("true_criteria")
            item_false = item.get("false_criteria")
            if not isinstance(item_qid, str) or not isinstance(item_true, str) or not isinstance(item_false, str):
                return False
            if not QID_RE.match(item_qid):
                return False
            if not item_true.strip() or not item_false.strip():
                return False
            if item_qid in seen_qids or item_true in seen_trues or item_false in seen_falses:
                return False
            seen_qids.add(item_qid)
            seen_trues.add(item_true)
            seen_falses.add(item_false)
            if new_qid == item_qid:
                return False
            if new_true == item_true:
                return False
            if new_false == item_false:
                return False
        return True
    except TypeError:
        return False

def check_grounded(state_output, state_cmd):
    try:
        if not isinstance(state_output, str) or not isinstance(state_cmd, str):
            return False
        if not state_output.strip() or not state_cmd.strip():
            return False
        if ".." in state_cmd:
            return False
        lower_output = state_output.lower()
        for marker in FORBIDDEN_OUTPUT_MARKERS:
            if marker in lower_output:
                return False
        return True
    except TypeError:
        return False

CATALOGUE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "plan", "JEV-USE-CASE-CATALOGUE.md")


@unittest.skipUnless(os.path.isfile(CATALOGUE_PATH),
                     "the live catalogue is a plan document the export tree does not ship")
class TestL6b2(unittest.TestCase):
    def setUp(self):
        try:
            with open(CATALOGUE_PATH, "r", encoding="utf-8") as f:
                self.doc_text = f.read()
        except OSError as e:
            self.fail(f"catalogue file unreadable: {e}")

    def test_propose_candidate_allowed_domains(self):
        for domain in ALLOWED_DOMAINS:
            cand = propose_candidate(domain, "echo hi", "test_qid")
            self.assertEqual(cand["domain"], domain)
            self.assertEqual(cand["status"], "proposed")

    def test_propose_candidate_rejects_unknown_domain(self):
        self.assertEqual(propose_candidate("Unknown", "echo hi", "test_qid"), {})

    def test_propose_candidate_rejects_hostile_input(self):
        self.assertEqual(propose_candidate(None, "echo", "q"), {})
        self.assertEqual(propose_candidate("Vault", None, "q"), {})
        self.assertEqual(propose_candidate("Vault", "echo", None), {})
        self.assertEqual(propose_candidate(123, "echo", "q"), {})
        self.assertEqual(propose_candidate("Vault", 123, "q"), {})
        self.assertEqual(propose_candidate("Vault", "echo", 123), {})
        self.assertEqual(propose_candidate(True, "echo", "q"), {})
        self.assertEqual(propose_candidate(["Vault"], "echo", "q"), {})

    def test_distinct_blocks_duplicate(self):
        bank = [
            {"qid": "q1", "true_criteria": "a", "false_criteria": "b"},
            {"qid": "q2", "true_criteria": "c", "false_criteria": "d"},
        ]
        self.assertFalse(check_distinct({"qid": "q1", "true_criteria": "a", "false_criteria": "b"}, bank))
        self.assertFalse(check_distinct({"qid": "q1", "true_criteria": "x", "false_criteria": "y"}, bank))
        self.assertFalse(check_distinct({"qid": "q3", "true_criteria": "a", "false_criteria": "z"}, bank))
        self.assertFalse(check_distinct({"qid": "q4", "true_criteria": "w", "false_criteria": "b"}, bank))
        self.assertTrue(check_distinct({"qid": "q5", "true_criteria": "e", "false_criteria": "f"}, bank))

    def test_distinct_blocks_invalid_qid(self):
        bank = [{"qid": "valid_qid", "true_criteria": "a", "false_criteria": "b"}]
        self.assertFalse(check_distinct({"qid": "../etc/passwd", "true_criteria": "x", "false_criteria": "y"}, bank))
        self.assertFalse(check_distinct({"qid": "path/to", "true_criteria": "x", "false_criteria": "y"}, bank))
        self.assertFalse(check_distinct({"qid": "..", "true_criteria": "x", "false_criteria": "y"}, bank))
        bad_bank = [{"qid": "../bad", "true_criteria": "a", "false_criteria": "b"}]
        self.assertFalse(check_distinct({"qid": "new_qid", "true_criteria": "x", "false_criteria": "y"}, bad_bank))

    def test_distinct_blocks_empty_criteria(self):
        bank = [{"qid": "q1", "true_criteria": "a", "false_criteria": "b"}]
        self.assertFalse(check_distinct({"qid": "q2", "true_criteria": "", "false_criteria": "y"}, bank))
        self.assertFalse(check_distinct({"qid": "q2", "true_criteria": "x", "false_criteria": ""}, bank))
        bad_bank = [{"qid": "q1", "true_criteria": "", "false_criteria": "b"}]
        self.assertFalse(check_distinct({"qid": "q2", "true_criteria": "x", "false_criteria": "y"}, bad_bank))
        bad_bank2 = [{"qid": "q1", "true_criteria": "a", "false_criteria": ""}]
        self.assertFalse(check_distinct({"qid": "q2", "true_criteria": "x", "false_criteria": "y"}, bad_bank2))

    def test_distinct_rejects_hostile_input(self):
        self.assertFalse(check_distinct(None, []))
        self.assertFalse(check_distinct({}, []))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": "a", "false_criteria": "b"}, None))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": "a", "false_criteria": "b"}, [None]))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": "a"}, []))

    def test_distinct_rejects_unhashable_input(self):
        self.assertFalse(check_distinct({"qid": ["list"], "true_criteria": "a", "false_criteria": "b"}, []))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": ["list"], "false_criteria": "b"}, []))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": "a", "false_criteria": ["list"]}, []))
        self.assertFalse(check_distinct({"qid": "q", "true_criteria": "a", "false_criteria": "b"}, [{"qid": ["list"], "true_criteria": "a", "false_criteria": "b"}]))
        self.assertFalse(check_distinct([], []))

    def test_distinct_blocks_bank_internal_duplicate(self):
        bank = [
            {"qid": "q1", "true_criteria": "a", "false_criteria": "b"},
            {"qid": "q1", "true_criteria": "c", "false_criteria": "d"},
        ]
        self.assertFalse(check_distinct({"qid": "q2", "true_criteria": "e", "false_criteria": "f"}, bank))
        bank2 = [
            {"qid": "q1", "true_criteria": "a", "false_criteria": "b"},
            {"qid": "q2", "true_criteria": "a", "false_criteria": "d"},
        ]
        self.assertFalse(check_distinct({"qid": "q3", "true_criteria": "e", "false_criteria": "f"}, bank2))
        bank3 = [
            {"qid": "q1", "true_criteria": "a", "false_criteria": "b"},
            {"qid": "q2", "true_criteria": "c", "false_criteria": "b"},
        ]
        self.assertFalse(check_distinct({"qid": "q3", "true_criteria": "e", "false_criteria": "f"}, bank3))

    def test_check_grounded_blocks_empty(self):
        self.assertFalse(check_grounded("", "echo"))
        self.assertFalse(check_grounded("   ", "echo"))
        self.assertFalse(check_grounded("output", ""))
        self.assertFalse(check_grounded("output", "   "))

    def test_check_grounded_accepts_nonempty(self):
        self.assertTrue(check_grounded("real output", "git status"))

    def test_check_grounded_blocks_dotdot_cmd(self):
        self.assertFalse(check_grounded("output", ".."))
        self.assertFalse(check_grounded("output", "cat ../etc/passwd"))
        self.assertFalse(check_grounded("output", "ls .."))

    def test_check_grounded_rejects_hostile_input(self):
        self.assertFalse(check_grounded(None, "echo"))
        self.assertFalse(check_grounded("output", None))
        self.assertFalse(check_grounded(123, "echo"))
        self.assertFalse(check_grounded("output", 123))
        self.assertFalse(check_grounded(b"bytes", "echo"))

    def test_check_grounded_blocks_corrupt_output(self):
        self.assertFalse(check_grounded("fatal: not a git repository", "git status"))
        self.assertFalse(check_grounded("error: something failed", "git status"))
        self.assertFalse(check_grounded("Traceback (most recent call last):", "python3 -c ..."))
        self.assertFalse(check_grounded("command not found", "badcmd"))
        self.assertFalse(check_grounded("No such file or directory", "cat missing"))
        self.assertFalse(check_grounded("Permission denied", "cat /etc/shadow"))
        self.assertFalse(check_grounded("usage: git status", "git status --bad"))
        self.assertFalse(check_grounded("errno 2", "python3 -c ..."))
        self.assertFalse(check_grounded("Exception in thread", "python3 -c ..."))

    def test_candidate_bank_counts(self):
        m = re.search(r'## Candidate bank\s*```json\s*(\[.*?\])\s*```', self.doc_text, re.DOTALL)
        self.assertIsNotNone(m, "candidate bank json block not found")
        data = json.loads(m.group(1))
        self.assertGreaterEqual(len(data), 60)
        domains = {}
        qids = set()
        for row in data:
            self.assertIn("qid", row)
            self.assertIn("domain", row)
            self.assertIn("state_cmd", row)
            self.assertIn("status", row)
            self.assertEqual(row["status"], "proposed")
            self.assertIn(row["domain"], ALLOWED_DOMAINS)
            self.assertNotIn(row["qid"], qids)
            qids.add(row["qid"])
            domains[row["domain"]] = domains.get(row["domain"], 0) + 1
        for d in ALLOWED_DOMAINS:
            self.assertGreaterEqual(domains.get(d, 0), 8, f"domain {d} has < 8 candidates")


if __name__ == "__main__":
    unittest.main()

"""Persona journey tests for BrotherDS.

Drives the real command line tool the way five people in a company would use
it.  Python 3.9 standard library only.  Everything happens inside a temporary
root so the real user's home directory is never touched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
BROTHERDS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(BROTHERDS_DIR))
BDS = os.path.join(BROTHERDS_DIR, "bds.py")
EX = os.path.join(BROTHERDS_DIR, "examples")
TOOLS = os.path.join(REPO_ROOT, "products", "brothermode", "tools")

GATE_RE = re.compile(r"^\s+(PASS|FAIL|NO-DATA)\s+(\S+)\s")
M_RANGE_RE = re.compile(r"^M(\d+)\.")


def _short(text, limit=800):
    if text is None:
        return ""
    s = str(text)
    if len(s) > limit:
        return s[:limit] + "...[truncated]"
    return s


class PersonaJourneys(unittest.TestCase):
    CLIENT = "claude"
    REAL = None
    REAL_MTIME = None
    tmp = None
    home = None
    vault = None
    ENV = None

    @classmethod
    def setUpClass(cls):
        cls.REAL = os.path.expanduser("~")
        configs = {os.path.join(cls.REAL, client) for client in (".claude", ".codex")}
        configs.update(os.environ[k] for k in ("BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME")
                       if os.environ.get(k))
        cls.REAL_INDEX_STATES = {}
        for config in configs:
            index = os.path.join(config, "bm_vault_index.sqlite3")
            cls.REAL_INDEX_STATES[index] = os.stat(index).st_mtime_ns if os.path.exists(index) else None

        cls.tmp = tempfile.mkdtemp(prefix="bds-persona-")
        try:
            cls._bootstrap()
        except Exception:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            cls.tmp = None
            raise

    @classmethod
    def _bootstrap(cls):
        cls.home = os.path.join(cls.tmp, "home")
        cls.vault = os.path.join(cls.tmp, "vault")
        os.makedirs(os.path.join(cls.home, "." + cls.CLIENT))
        os.makedirs(os.path.join(cls.vault, "40-Failures"))

        with open(
            os.path.join(cls.vault, "40-Failures", "recall-above-threshold.md"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write(
                "---\n"
                "type: lesson\n"
                "---\n"
                "\n"
                "# Recall above the merge threshold\n"
                "\n"
                "A merge claim estimated recall from a review sample drawn only above the merge threshold.\n"
                "The gate M8.recall_evidence failed. Sample below the threshold before claiming recall.\n"
            )
        with open(
            os.path.join(cls.vault, "40-Failures", "japanese-width-normalization.md"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write(
                "---\n"
                "type: lesson\n"
                "---\n"
                "\n"
                "# Japanese width normalization\n"
                "\n"
                "Half-width katakana and variant kanji split one customer into several records.\n"
                "M22.normalization catches this. Normalize width and apply itaiji mapping before merging.\n"
            )

        # Child processes must not inherit a real config, plugin root, client
        # marker, Python import path, or Vault location from either host.
        cls.ENV = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
                   if k in os.environ}
        cls.ENV["HOME"] = cls.home
        cls.ENV["BROTHER_CLIENT"] = cls.CLIENT
        cls.ENV["BROTHER_CONFIG_DIR"] = os.path.join(cls.home, "." + cls.CLIENT)
        cls.ENV["TMPDIR"] = cls.tmp
        cls.ENV["TMP"] = cls.tmp
        cls.ENV["TEMP"] = cls.tmp
        cls.ENV["BROTHERDS_VAULT"] = cls.vault
        cls.ENV["BROTHERDS_VAULT_TOOLS"] = TOOLS
        cls.ENV["BROTHERDS_RECALL_TIMEOUT_S"] = "60"
        cls.ENV["PYTHONDONTWRITEBYTECODE"] = "1"
        cls.ENV.pop("BM_VAULT_ROOT", None)
        cls.ENV.pop("BROTHERMODE_VAULT", None)

        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(TOOLS, "bm_vault.py"),
                "index",
                "--vault",
                cls.vault,
            ],
            env=cls.ENV,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "vault index failed (%s): %s"
                % (proc.returncode, _short((proc.stdout or "") + (proc.stderr or "")))
            )

    @classmethod
    def tearDownClass(cls):
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        cls.tmp = None

    # -- helpers ---------------------------------------------------------

    def bds(self, *args):
        proc = subprocess.run(
            [sys.executable, BDS, *args],
            env=self.ENV,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def inbox(self):
        path = os.path.join(self.vault, "00-Inbox")
        if not os.path.isdir(path):
            return []
        return sorted(os.listdir(path))

    def claims_dir(self, name):
        path = os.path.join(self.tmp, "claims", name)
        os.makedirs(path, exist_ok=True)
        return path

    def reset_dir(self, path):
        if os.path.isdir(path):
            for name in os.listdir(path):
                if name.startswith("."):
                    continue  # the ledger's own state (.bds-lessons-seen.json) survives a reseed
                sub = os.path.join(path, name)
                if os.path.isdir(sub):
                    shutil.rmtree(sub)
                else:
                    os.remove(sub)
        else:
            os.makedirs(path)

    def parse_gates(self, out):
        gates = {}
        for line in out.splitlines():
            match = GATE_RE.match(line)
            if match:
                gates[match.group(2)] = (match.group(1), line)
        return gates

    def verdict_of(self, out):
        verdict = None
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("VERDICT "):
                parts = stripped.split()
                if len(parts) >= 2:
                    verdict = parts[1]
        return verdict

    def extract_json(self, out):
        text = out.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            return json.loads(text[start:end + 1])
        raise ValueError("no JSON object found in output: " + _short(out, 400))

    def load_json(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def write_json(self, path, obj):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)

    def clone(self, obj):
        return json.loads(json.dumps(obj))

    # -- journeys --------------------------------------------------------

    def test_b1_customer_master_pm_derives_numbers_then_checks(self):
        tag = "B1"
        csv = os.path.join(EX, "review-sample-mdm.csv")
        rc, out = self.bds("mdm-eval", "review", csv, "--merge-threshold", "0.8")
        self.assertEqual(
            rc, 0,
            "[%s] mdm-eval exit %s: %s" % (tag, rc, _short(out)),
        )
        try:
            data = self.extract_json(out)
        except Exception as exc:
            self.fail("[%s] mdm-eval JSON parse failed (%s): %s"
                      % (tag, exc, _short(out)))
        for key in ("strata", "precision_above", "recall", "merge_threshold", "n_rows"):
            self.assertIn(
                key, data,
                "[%s] mdm-eval missing key %r: %s" % (tag, key, _short(out)),
            )
        self.assertEqual(
            len(data["strata"]), 4,
            "[%s] expected 4 strata, got %r: %s"
            % (tag, data.get("strata"), _short(out)),
        )

        claim = self.load_json(os.path.join(EX, "example-mdm-evaluated.json"))
        claim["id"] = "B1-DEDUP-001"
        claim.setdefault("master_data", {}).setdefault(
            "evaluation", {}
        )["review_strata"] = data["strata"]
        claim_dir = self.claims_dir("b1")
        claim_path = os.path.join(claim_dir, "B1-DEDUP-001.json")
        self.write_json(claim_path, claim)

        rc, out = self.bds("check", claim_path)
        self.assertEqual(
            rc, 0,
            "[%s] check exit %s: %s" % (tag, rc, _short(out)),
        )
        gates = self.parse_gates(out)
        fails = [name for name, (verdict, _line) in gates.items() if verdict == "FAIL"]
        self.assertEqual(
            fails, [],
            "[%s] unexpected FAIL gates %r: %s" % (tag, fails, _short(out)),
        )
        for gate in (
            "M7.precision_evidence",
            "M8.recall_evidence",
            "M19.labeller_agreement",
        ):
            self.assertIn(
                gate, gates,
                "[%s] missing gate %s: %s" % (tag, gate, _short(out)),
            )
            self.assertEqual(
                gates[gate][0], "PASS",
                "[%s] gate %s is %s: %s"
                % (tag, gate, gates[gate][0], _short(out)),
            )
        for name, (verdict, _line) in gates.items():
            match = M_RANGE_RE.match(name)
            if match and 7 <= int(match.group(1)) <= 19:
                self.assertEqual(
                    verdict, "PASS",
                    "[%s] gate %s is %s: %s"
                    % (tag, name, verdict, _short(out)),
                )

        verdict = self.verdict_of(out)
        self.assertIsNotNone(
            verdict,
            "[%s] no VERDICT line: %s" % (tag, _short(out)),
        )
        self.assertNotEqual(
            verdict, "FAIL",
            "[%s] VERDICT was FAIL: %s" % (tag, _short(out)),
        )

        receipt_path = os.path.join(claim_dir, "B1-receipt.md")
        rc, out = self.bds("receipt", claim_path, receipt_path)
        self.assertEqual(
            rc, 0,
            "[%s] receipt exit %s: %s" % (tag, rc, _short(out)),
        )
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = fh.read()
        self.assertIn(
            "Vault context:", receipt,
            "[%s] receipt missing Vault context: %s" % (tag, _short(receipt)),
        )

    def test_b3_audit_catches_the_hurried_claim_and_recalls_the_lesson(self):
        tag = "B3a"
        claim_path = os.path.join(EX, "example-mdm-overclaim.json")
        rc, out = self.bds("check", claim_path)
        gates = self.parse_gates(out)
        verdict = self.verdict_of(out)
        self.assertEqual(
            verdict, "FAIL",
            "[%s] expected VERDICT FAIL, got %r: %s"
            % (tag, verdict, _short(out)),
        )
        self.assertIn(
            "M8.recall_evidence", gates,
            "[%s] missing M8.recall_evidence: %s" % (tag, _short(out)),
        )
        self.assertEqual(
            gates["M8.recall_evidence"][0], "FAIL",
            "[%s] M8 verdict %s: %s"
            % (tag, gates["M8.recall_evidence"][0], _short(out)),
        )
        self.assertIn(
            "below the merge threshold", gates["M8.recall_evidence"][1],
            "[%s] M8 detail lacks threshold text: %s" % (tag, _short(out)),
        )
        for prefix in ("M7.", "M9.", "M16.", "M18.", "M19."):
            matching = [name for name in gates if name.startswith(prefix)]
            self.assertTrue(
                matching,
                "[%s] no %s gate line: %s" % (tag, prefix, _short(out)),
            )
            for name in matching:
                self.assertEqual(
                    gates[name][0], "FAIL",
                    "[%s] %s is %s: %s"
                    % (tag, name, gates[name][0], _short(out)),
                )

        claim_dir = self.claims_dir("b3a")
        receipt_path = os.path.join(claim_dir, "B3a-receipt.md")
        rc, out = self.bds("receipt", claim_path, receipt_path)
        self.assertEqual(
            rc, 0,
            "[%s] receipt exit %s: %s" % (tag, rc, _short(out)),
        )
        with open(receipt_path, encoding="utf-8") as fh:
            receipt = fh.read()
        self.assertIn(
            "lessons surfaced", receipt,
            "[%s] receipt lacks lessons surfaced: %s" % (tag, _short(receipt)),
        )
        self.assertIn(
            "recall above threshold", receipt,
            "[%s] receipt lacks lesson title: %s" % (tag, _short(receipt)),
        )

    def test_b3_audit_japanese_normalization(self):
        tag = "B3b"
        claim_dir = self.claims_dir("b3b")
        claim = {
            "id": "B3-JA-001",
            "claim_type": "MASTER_DATA",
            "statement": "Merge customer master records after Japanese normalization.",
            "value": 100,
            "unit": "records merged",
            "origin": "SYSTEM",
            "question": "How many customer records are merged after normalization?",
            "decision": "Merge 100 customer records.",
            "grain": "customer",
            "not_established": ["demo"],
            "master_data": {
                "merge_threshold": 0.8,
                "review_threshold": 0.6,
                "normalization": {
                    "locale": "ja",
                    "steps": ["nfkc", "space"],
                },
            },
        }
        claim_path = os.path.join(claim_dir, "B3-JA-001.json")
        self.write_json(claim_path, claim)

        rc, out = self.bds("check", claim_path)
        gates = self.parse_gates(out)
        self.assertIn(
            "M22.normalization", gates,
            "[%s] missing M22.normalization: %s" % (tag, _short(out)),
        )
        self.assertEqual(
            gates["M22.normalization"][0], "FAIL",
            "[%s] M22 verdict %s: %s"
            % (tag, gates["M22.normalization"][0], _short(out)),
        )
        self.assertIn(
            "itaiji", gates["M22.normalization"][1],
            "[%s] M22 detail lacks itaiji: %s" % (tag, _short(out)),
        )

        claim["master_data"]["normalization"]["steps"] = [
            "nfkc",
            "space",
            "long_vowel",
            "itaiji",
            "small_ke",
            "corporate",
        ]
        claim["master_data"]["normalization"]["probes"] = {
            "company": [
                ["株式会社ｱｲｳ商事", "(株)アイウ商事"],
            ],
            "phone": [
                ["０３－１２３４－５６７８", "03-1234-5678"],
            ],
        }
        claim_path2 = os.path.join(claim_dir, "B3-JA-002.json")
        self.write_json(claim_path2, claim)

        rc, out = self.bds("check", claim_path2)
        gates = self.parse_gates(out)
        self.assertIn(
            "M22.normalization", gates,
            "[%s] second check missing M22.normalization: %s" % (tag, _short(out)),
        )
        self.assertEqual(
            gates["M22.normalization"][0], "PASS",
            "[%s] second M22 verdict %s: %s"
            % (tag, gates["M22.normalization"][0], _short(out)),
        )

    def test_a2_finance_owner_forecasts_are_scored_with_wis(self):
        tag = "A2"
        claim_dir = self.claims_dir("a2")
        base = self.load_json(os.path.join(EX, "example-forecast-quantiles.json"))
        actuals = [470, 500, 530, 555, 700, 480]
        paths = []
        for index in range(1, 7):
            cloned = self.clone(base)
            cloned["id"] = "A2-FC-%d" % index
            path = os.path.join(claim_dir, "A2-FC-%d.json" % index)
            self.write_json(path, cloned)
            paths.append(path)

        inbox_before = self.inbox()
        outs = []
        for index, (path, actual) in enumerate(zip(paths, actuals), start=1):
            rc, out = self.bds("score", path, str(actual), "finance owner", "2026-09-30")
            outs.append(out)
            self.assertEqual(
                rc, 0,
                "[%s] score #%d exit %s: %s" % (tag, index, rc, _short(out)),
            )
            self.assertIn(
                "  wis", out,
                "[%s] score #%d missing wis line: %s" % (tag, index, _short(out)),
            )
            updated = self.load_json(path)
            result = updated.get("review", {}).get("result", {})
            wis = result.get("wis")
            self.assertTrue(
                isinstance(wis, (int, float)) and not isinstance(wis, bool),
                "[%s] score #%d wis not numeric: %r in %s"
                % (tag, index, wis, _short(out)),
            )
            self.assertIsInstance(
                result.get("band_hit"), bool,
                "[%s] score #%d band_hit not bool: %r in %s"
                % (tag, index, result.get("band_hit"), _short(out)),
            )

        missed_index = actuals.index(700)
        self.assertIn(
            "OUTCOME MISSED", outs[missed_index],
            "[%s] 700 score not MISSED: %s" % (tag, _short(outs[missed_index])),
        )
        self.assertIn(
            "lesson candidate: OK",
            outs[missed_index],
            "[%s] 700 score has no lesson candidate: %s"
            % (tag, _short(outs[missed_index])),
        )
        inbox_after = self.inbox()
        new_files = [name for name in inbox_after if name not in inbox_before]
        self.assertTrue(
            any(name.startswith("missed") for name in new_files),
            "[%s] no missed lesson in inbox: %r" % (tag, inbox_after),
        )

        rc, out = self.bds("ledger", claim_dir)
        self.assertIn(
            "0.1-0.9 band coverage:", out,
            "[%s] ledger lacks band coverage line: %s" % (tag, _short(out)),
        )
        self.assertIn(
            "over 6", out,
            "[%s] ledger lacks over 6: %s" % (tag, _short(out)),
        )

    def test_a1_founder_ledger_turns_habits_into_lessons(self):
        tag = "A1"
        claim_dir = self.claims_dir("a1")
        base = self.load_json(os.path.join(EX, "example-mdm-overclaim.json"))
        evaluated = self.load_json(os.path.join(EX, "example-mdm-evaluated.json"))

        def seed():
            self.reset_dir(claim_dir)
            for index in (1, 2):
                cloned = self.clone(base)
                cloned["id"] = "OC-%d" % index
                self.write_json(
                    os.path.join(claim_dir, "OC-%d.json" % index), cloned
                )
            self.write_json(
                os.path.join(claim_dir, "evaluated.json"), self.clone(evaluated)
            )

        seed()
        inbox_before = self.inbox()
        rc, out = self.bds("ledger", claim_dir, "--propose-lessons")
        self.assertEqual(
            rc, 0,
            "[%s] ledger --propose-lessons exit %s: %s" % (tag, rc, _short(out)),
        )
        self.assertIn(
            "RECURRING GATE FAILURES", out,
            "[%s] no RECURRING section: %s" % (tag, _short(out)),
        )
        line_hit = any(
            "M8.recall_evidence" in line and "2 claims" in line
            for line in out.splitlines()
        )
        self.assertTrue(
            line_hit,
            "[%s] no M8 line with 2 claims: %s" % (tag, _short(out)),
        )
        candidates = [
            line for line in out.splitlines() if "lesson candidate: OK id=" in line
        ]
        self.assertGreaterEqual(
            len(candidates), 5,
            "[%s] only %d lesson candidates: %s"
            % (tag, len(candidates), _short(out)),
        )
        inbox_after = self.inbox()
        new_files = [name for name in inbox_after if name not in inbox_before]
        self.assertTrue(
            new_files,
            "[%s] inbox unchanged: %r" % (tag, inbox_after),
        )
        self.assertTrue(
            any("recurring-fail-m8-recall-evidence" in name for name in new_files),
            "[%s] no recurring-fail-m8-recall-evidence file: %r"
            % (tag, inbox_after),
        )

        count_after = len(inbox_after)

        # Restore the same three claims so the second run sees identical
        # inputs; the ledger's .bds-lessons-seen.json, kept beside the claims,
        # carries the memory of the proposals already made.
        seed()

        rc, out2 = self.bds("ledger", claim_dir, "--propose-lessons")
        self.assertIn(
            "lesson candidates: none new", out2,
            "[%s] second run not idempotent: %s" % (tag, _short(out2)),
        )
        self.assertEqual(
            len(self.inbox()), count_after,
            "[%s] second run added files: %r" % (tag, self.inbox()),
        )

    def test_b2_warehouse_lead_pipeline_claim(self):
        tag = "B2"
        claim_path = os.path.join(EX, "example-pipeline-reconciled.json")
        rc, out = self.bds("check", claim_path)
        self.assertEqual(
            rc, 0,
            "[%s] check exit %s: %s" % (tag, rc, _short(out)),
        )
        gates = self.parse_gates(out)
        fails = [name for name, (verdict, _line) in gates.items() if verdict == "FAIL"]
        self.assertEqual(
            fails, [],
            "[%s] unexpected FAIL gates: %r in %s" % (tag, fails, _short(out)),
        )
        for index in range(1, 9):
            prefix = "P%d." % index
            matching = [name for name in gates if name.startswith(prefix)]
            self.assertTrue(
                matching,
                "[%s] missing %s gate: %s" % (tag, prefix, _short(out)),
            )
            for name in matching:
                self.assertEqual(
                    gates[name][0], "PASS",
                    "[%s] %s is %s: %s"
                    % (tag, name, gates[name][0], _short(out)),
                )

    def test_zz_real_home_untouched(self):
        tag = "ZZ"
        for real_index, before in self.REAL_INDEX_STATES.items():
            after = os.stat(real_index).st_mtime_ns if os.path.exists(real_index) else None
            self.assertEqual(after, before, "[%s] an ambient client index changed" % tag)
        self.assertEqual(self.ENV["BROTHER_CLIENT"], self.CLIENT)
        self.assertTrue(self.ENV["BROTHER_CONFIG_DIR"].startswith(self.tmp + os.sep))
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDECODE", "CODEX_THREAD_ID",
                     "BROTHER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "PYTHONPATH"):
            self.assertNotIn(name, self.ENV)
        for name in self.inbox():
            with open(os.path.join(self.vault, "00-Inbox", name), encoding="utf-8") as stream:
                body = stream.read()
            self.assertIn("human_approved: false", body)
            self.assertNotIn("human_approved: true", body)
        self.assertNotEqual(
            self.ENV["HOME"], self.REAL,
            "[%s] ENV HOME equals real home: %r" % (tag, self.ENV["HOME"]),
        )


class CodexPersonaJourneys(PersonaJourneys):
    CLIENT = "codex"


if __name__ == "__main__":
    unittest.main(verbosity=2)

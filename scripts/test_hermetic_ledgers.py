"""STAB1.b, frozen harness: tests never write the real ledgers.

Measured 2026-11-01: ~/.claude/evidence/bridge-calls.jsonl held 2350 rows, 2244 of them written by
scripts/loop/model_bench.py's selftest (fake models x/bridgey and x/typedy), because that selftest
calls run() without pointing BROTHER_BRIDGE_CALLS_LEDGER at a scratch file and model_call._log_bridge
falls back to the real ledger. model_call's own selftest already isolates both ledgers.

THIS FILE NEVER TOUCHES THE REAL LEDGERS ITSELF. Every target runs in a child process whose HOME is
a fresh temporary folder and whose environment carries no BROTHER_ name, so "the real ledger" for
that child is <tmp>/.claude/evidence/bridge-calls.jsonl, a path this file creates and reads. The
evidence folder is CREATED before each run, because an absent folder makes the append fail
silently and every "nothing was written" case would then pass for the wrong reason.

One condition per case:
  1  model_bench --selftest leaves the HOME bridge ledger untouched            (red before the fix)
  1b model_bench's selftest names a scratch ledger on every bridge row         (red before the fix)
  2  model_call --selftest leaves the HOME bridge ledger untouched             (reference, green)
  3  _log_bridge refuses the HOME ledger for an x/ id, ledger name unset       (red before the fix)
  3b _log_bridge refuses the HOME ledger for an x/ id named by its full path   (red before the fix)
  4  _log_bridge records a real id into an explicitly named ledger             (green)
  4b _log_bridge records a real id into the HOME ledger when no name is set    (green, real use)
  5  every BROTHER_*_LEDGER default in scripts/ resolves under HOME             (green)
  6  _log_bridge records an x/ id into an explicitly named scratch ledger       (green, model_call's own selftest relies on it)

Frozen: the implementer of STAB1.b may not edit this file.
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
ROOT = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
LEDGER_REL = os.path.join(".claude", "evidence", "bridge-calls.jsonl")
TIMEOUT_S = 600
# FLOW1.e (648f53c2e) added a ledger the night tick takes from the environment only: unset reads NO-DATA, there is
# no default path at all, so there is nothing that could ignore HOME. Name -> the one file that reads it.
ENV_ONLY_LEDGERS = {"BROTHER_STALL_LEDGER": os.path.join("scripts", "loop", "brother_night_tick.py")}


def _child_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k != "PYTHONPATH"}
    env["HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


_LOG_SNIPPET = r"""
import sys
sys.path.insert(0, sys.argv[1])
import model_call as MC
reg = {"m": {"id": sys.argv[2], "transport": "bridge"}}
try:
    MC._log_bridge("m", reg, "build", "hi", "[usage] prompt=5 completion=3 model=" + sys.argv[2], True, 0.1)
    print("RETURNED")
except Exception as exc:
    print("RAISED %s" % type(exc).__name__)
"""

_BENCH_RECORDER = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import model_call as MC
_orig, _seen = MC._log_bridge, []
def _rec(*a, **k):
    _seen.append(os.environ.get("BROTHER_BRIDGE_CALLS_LEDGER") or "")
    return _orig(*a, **k)
MC._log_bridge = _rec
import model_bench as MB
MB.selftest()
print("CALLS %d" % len(_seen))
for s in _seen:
    print("LEDGER " + s)
"""


class RealLedgersUntouched(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="stab1b-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        self.ledger = os.path.join(self.home, LEDGER_REL)

    def _selftest(self, script):
        p = subprocess.run([sys.executable, "-B", os.path.join(LOOP, script), "--selftest"], cwd=ROOT,
                           env=_child_env(self.home), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=TIMEOUT_S)
        # PREMISE, not the property: a selftest that never reached its verdict line proves nothing about
        # the ledger, so it is refused here with its own message rather than read as "nothing written".
        self.assertRegex(p.stdout, r"selftest: \d+ cases",
                         "%s --selftest never reached its verdict line (rc %s):\n%s" % (script, p.returncode, p.stdout[-1500:]))
        return p

    def _log(self, model_id, **extra):
        p = subprocess.run([sys.executable, "-B", "-c", _LOG_SNIPPET, LOOP, model_id], cwd=ROOT,
                           env=_child_env(self.home, **extra), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=TIMEOUT_S)
        self.assertRegex(p.stdout, r"(RETURNED|RAISED \w+)",
                         "the _log_bridge child never reached the call (rc %s):\n%s" % (p.returncode, p.stdout[-1500:]))
        return p

    # 1
    def test_1_model_bench_selftest_leaves_home_bridge_ledger(self):
        self._selftest("model_bench.py")
        rows = _rows(self.ledger)
        self.assertEqual(rows, [], "model_bench --selftest wrote %d row(s) into the HOME bridge ledger, first: %s"
                         % (len(rows), rows[:1]))

    # 1b
    def test_1b_model_bench_selftest_names_a_scratch_ledger_on_every_bridge_row(self):
        # Case 1 alone cannot isolate model_bench's own isolation: once _log_bridge refuses x/ ids on the
        # HOME ledger (case 3), deleting model_bench's isolation writes nothing and case 1 stays green
        # (measured on the proof copy, 2026-11-01). This case records, at every _log_bridge call the
        # selftest makes, the ledger name in force, so only model_bench's isolation decides it.
        p = subprocess.run([sys.executable, "-B", "-c", _BENCH_RECORDER, LOOP], cwd=ROOT,
                           env=_child_env(self.home), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=TIMEOUT_S)
        m = re.search(r"^CALLS (\d+)$", p.stdout, re.M)
        self.assertTrue(m and int(m.group(1)) > 0 and re.search(r"selftest: \d+ cases", p.stdout),
                        "premise: model_bench's selftest made no recorded bridge row or no verdict (rc %s):\n%s"
                        % (p.returncode, p.stdout[-1500:]))
        evidence = os.path.realpath(os.path.join(self.home, ".claude", "evidence")) + os.sep
        named = re.findall(r"^LEDGER (.*)$", p.stdout, re.M)
        bad = [n for n in named if not n or os.path.realpath(os.path.expanduser(n)).startswith(evidence)]
        self.assertEqual(bad, [], "%d of %d bridge rows in model_bench's selftest named no scratch ledger: %s"
                         % (len(bad), len(named), sorted(set(bad))[:3]))

    # 2
    def test_2_model_call_selftest_leaves_home_bridge_ledger(self):
        self._selftest("model_call.py")
        rows = _rows(self.ledger)
        self.assertEqual(rows, [], "model_call --selftest wrote %d row(s) into the HOME bridge ledger, first: %s"
                         % (len(rows), rows[:1]))

    # 3
    def test_3_fake_id_refused_on_home_ledger_when_unnamed(self):
        self._log("x/fakey")
        rows = _rows(self.ledger)
        self.assertEqual(rows, [], "_log_bridge appended a fake x/ id to the HOME ledger: %s" % rows[:1])

    # 3b
    def test_3b_fake_id_refused_on_home_ledger_named_by_full_path(self):
        self._log("x/fakey", BROTHER_BRIDGE_CALLS_LEDGER=self.ledger)
        rows = _rows(self.ledger)
        self.assertEqual(rows, [], "_log_bridge appended a fake x/ id to the HOME ledger named by its path: %s" % rows[:1])

    # 4
    def test_4_real_id_recorded_in_named_ledger(self):
        named = os.path.join(self.home, "named", "bridge.jsonl")
        os.makedirs(os.path.dirname(named))
        self._log("vendor/model-a", BROTHER_BRIDGE_CALLS_LEDGER=named)
        rows = _rows(named)
        self.assertEqual(len(rows), 1, "a real id must still be recorded in the named ledger, got %d row(s)" % len(rows))
        self.assertEqual(json.loads(rows[0]).get("model"), "m")

    # 4b
    def test_4b_real_id_recorded_in_home_ledger_when_unnamed(self):
        self._log("vendor/model-a")
        rows = _rows(self.ledger)
        self.assertEqual(len(rows), 1, "a real id with no ledger name must still land in the HOME ledger, got %d" % len(rows))

    # 5
    def test_5_every_ledger_default_resolves_under_home(self):
        names, defaults, bad = set(), {}, []
        for dirpath, _dirs, files in os.walk(HERE):
            for f in files:
                if not f.endswith((".py", ".sh")):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError as exc:
                    self.fail("unreadable source %s: %s" % (path, exc))
                found = set(re.findall(r"\bBROTHER_[A-Z0-9_]*LEDGER\b", text))
                names |= found
                if f.startswith("test_") or f == os.path.basename(__file__):
                    continue
                # A DEFAULT is any ledger path literal in a file that names the ledger variable: a quoted
                # string, or a shell ${NAME:-...} fallback, naming a .jsonl under .claude/evidence/. Shape
                # free on purpose, so moving the default into a local variable still counts.
                # ponytail: a default built only by os.path.join pieces is not recognised and reads as
                # "no recognisable default" (fails closed); widen the pattern if that shape is adopted.
                lits = re.findall(r'["\']([^"\'\n]*\.claude/evidence/[^"\'\n]*\.jsonl)["\']', text)
                lits += re.findall(r'\$\{BROTHER_[A-Z0-9_]*LEDGER:-([^}]+)\}', text)
                for name in found:
                    for d in lits:
                        defaults.setdefault(name, []).append((os.path.relpath(path, ROOT), d))
        sys.stderr.write("\nledger names found: %s\n" % ", ".join(sorted(names)))
        self.assertGreaterEqual(len(names), 2, "the scan found fewer ledger names than the two known ones: %s" % sorted(names))
        for name in sorted(names):
            if name in ENV_ONLY_LEDGERS:
                # A ledger with NO default cannot fall back to the real one: its reader must refuse when the
                # variable is unset. Shown from the reader's own source, so a default added later is not skipped.
                where = os.path.join(ROOT, ENV_ONLY_LEDGERS[name])
                with open(where, encoding="utf-8") as fh:
                    src = fh.read()
                if '"%s"' % name not in src or "not set" not in src or name in defaults:
                    bad.append("%s is declared env only but %s no longer refuses when it is unset" % (name, ENV_ONLY_LEDGERS[name]))
                continue
            if name not in defaults:
                bad.append("%s: no recognisable default anywhere, cannot show a scratch HOME isolates it" % name)
                continue
            for where, d in defaults[name]:
                if not (d.startswith("~/") or d.startswith("$HOME/") or d.startswith("${HOME}/")):
                    bad.append("%s in %s defaults to %s, which ignores HOME" % (name, where, d))
        self.assertEqual(bad, [], "\n".join(bad))

    # 6
    def test_6_fake_id_recorded_in_named_scratch_ledger(self):
        named = os.path.join(self.home, "scratch", "bridge.jsonl")
        os.makedirs(os.path.dirname(named))
        self._log("x/fakey", BROTHER_BRIDGE_CALLS_LEDGER=named)
        self.assertEqual(len(_rows(named)), 1, "an x/ id with an explicit scratch ledger must still be recorded there")


if __name__ == "__main__":
    unittest.main()

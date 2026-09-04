#!/usr/bin/env python3
"""VB7-02: pins the ONE --json schema shared by every bm_vault_* reporting tool.

Every tool below emits {"tool", "verdict", "counts", "findings",
"schema_version"} with schema_version == 1, verdict in {"PASS", "FAIL",
"NO-DATA"} matching the process exit code, and each finding shaped
{"kind", "path", "detail"}. This is the ONE place that checks all five at
once, so the five tools can never quietly drift apart from each other.

Calibration (per the brief): comment out "schema_version": 1 in one tool
(e.g. bm_vault_graph.py's _emit_json) and re-run this file; TopLevelShape
goes red for that tool alone. Purge tools/__pycache__ between swaps so a
stale .pyc is never what actually ran, then restore the line.

Run: python3 tools/test_bm_vault_json_schema.py      (unittest output, exit 0 or 1)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(HERE, "bm_vault_graph.py")
RETENTION = os.path.join(HERE, "bm_vault_retention.py")
LINT = os.path.join(HERE, "bm_vault_lint.py")
CURATE = os.path.join(HERE, "bm_vault_curate.py")

TOP_LEVEL_KEYS = {"tool", "verdict", "counts", "findings", "schema_version"}
FINDING_KEYS = {"kind", "path", "detail"}
VERDICTS = {"PASS", "FAIL", "NO-DATA"}


def run(argv, env=None, cwd=None):
    p = subprocess.run([sys.executable] + argv, env=env, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_queue(path, entries):
    write(path, json.dumps({"generated": "2026-08-01T00:00:00+00:00", "vault": None,
                            "queue": entries, "rejections": [], "audit": []}))


def _candidate(a, b, built=None, owner=None):
    e = {"pair": [a, b], "titles": [a, b], "finders": {"duplicate": 0.6}, "combined": 0.6}
    if built is not None:
        e["built"] = built
    if owner is not None:
        e["owner"] = owner
    return e


class SchemaCase(object):
    """One case to validate: either a deferred (argv, env) the test loop runs
    itself, or an eagerly precomputed (code, out) for a fixture with
    filesystem side effects between cases (census: the fixture must run
    "pass" and then delete the note before building "fail", so those two
    subprocess calls cannot be deferred to a later, arbitrarily-ordered
    test loop)."""

    def __init__(self, label, want_exit, argv=None, env=None, code=None, out=None):
        self.label = label
        self.argv = argv
        self.want_exit = want_exit
        self.env = env
        self.code = code
        self.out = out

    def run(self):
        if self.code is not None:
            return self.code, self.out
        return run(self.argv, env=self.env)


def _cases():
    tmp = tempfile.mkdtemp(prefix="bm-vault-json-schema-")
    cases = []

    # bm_vault_graph.py measure/check: a clean vault (PASS) and a dirty one (FAIL).
    clean_vault = os.path.join(tmp, "clean-vault")
    write(os.path.join(clean_vault, "A.md"),
          "---\ntype: reference\nstatus: open\n---\nNo links.\n")
    dirty_vault = os.path.join(tmp, "dirty-vault")
    write(os.path.join(dirty_vault, "Bad.md"),
          "---\ntype: reference\nstatus: mystery\n---\nSee [[Ghost]].\n")
    empty_vault = os.path.join(tmp, "empty-vault")
    os.makedirs(empty_vault)
    cases += [
        SchemaCase("graph.measure.pass", 0,
                   argv=[GRAPH, "measure", "--vault", clean_vault, "--json"]),
        SchemaCase("graph.measure.no-data", 3,
                   argv=[GRAPH, "measure", "--vault", empty_vault, "--json"]),
        SchemaCase("graph.check.pass", 0,
                   argv=[GRAPH, "check", "--vault", clean_vault, "--json"]),
        SchemaCase("graph.check.fail", 2,
                   argv=[GRAPH, "check", "--vault", dirty_vault, "--json"]),
        SchemaCase("graph.check.no-data", 3,
                   argv=[GRAPH, "check", "--vault", empty_vault, "--json"]),
    ]

    # bm_vault_lint.py check: valid frontmatter (PASS), missing id (FAIL).
    lint_pass_vault = os.path.join(tmp, "lint-pass-vault")
    write(os.path.join(lint_pass_vault, "ok.md"),
          "---\nid: n-0123456789abcdef\ntype: reference\nstatus: standing\n"
          "created: 2026-08-30\n---\nbody\n")
    lint_fail_vault = os.path.join(tmp, "lint-fail-vault")
    write(os.path.join(lint_fail_vault, "bad.md"),
          "---\ntype: reference\nstatus: standing\ncreated: 2026-08-30\n---\nbody\n")
    lint_empty_vault = os.path.join(tmp, "lint-empty-vault")
    os.makedirs(lint_empty_vault)
    cases += [
        SchemaCase("lint.check.pass", 0,
                   argv=[LINT, "check", "--vault", lint_pass_vault, "--json"]),
        SchemaCase("lint.check.fail", 1,
                   argv=[LINT, "check", "--vault", lint_fail_vault, "--json"]),
        SchemaCase("lint.check.no-data", 2,
                   argv=[LINT, "check", "--vault", lint_empty_vault, "--json"]),
    ]

    # bm_vault_curate.py governance: under cap/age (PASS), over both (FAIL), missing (NO-DATA).
    queue_pass = os.path.join(tmp, "queue-pass.json")
    _write_queue(queue_pass, [_candidate("a", "b", built="2026-08-01T00:00:00+00:00",
                                          owner="linh")])
    queue_fail = os.path.join(tmp, "queue-fail.json")
    _write_queue(queue_fail, [
        _candidate("a", "b", built="2020-01-01T00:00:00+00:00", owner="linh"),
        _candidate("c", "d", built="2026-08-01T00:00:00+00:00", owner="linh"),
    ])
    cases += [
        SchemaCase("curate.governance.pass", 0,
                   argv=[CURATE, "governance", "--queue", queue_pass, "--cap", "10",
                         "--max-age-days", "1000", "--json"]),
        SchemaCase("curate.governance.fail", 1,
                   argv=[CURATE, "governance", "--queue", queue_fail, "--cap", "1",
                         "--max-age-days", "1", "--json"]),
        SchemaCase("curate.governance.no-data", 2,
                   argv=[CURATE, "governance", "--queue",
                         os.path.join(tmp, "no-such-queue.json"), "--json"]),
    ]

    # bm_vault_retention.py census: needs a real sqlite index, built via bm_vault.py.
    census_home = os.path.join(tmp, "census-home")
    census_vault = os.path.join(tmp, "census-vault")
    write(os.path.join(census_vault, "note.md"),
          "---\nname: note\ndescription: a note\n---\nBody.\n")
    os.makedirs(os.path.join(census_home, ".claude"))
    census_env = dict(os.environ)
    census_env["HOME"] = census_home
    census_env["BM_VAULT_ROOT"] = census_vault
    indexer = os.path.join(HERE, "bm_vault.py")
    code, out = run([indexer, "index", "--vault", census_vault], env=census_env)
    assert code == 0 and "indexed" in out, "schema test fixture index failed: %s" % out[:300]
    # Run eagerly, right here: census.pass must observe the note before it is
    # deleted, and census.fail must observe it gone. Deferring both calls to
    # the (arbitrarily-ordered) test loop below would race that deletion.
    pass_code, pass_out = run([RETENTION, "census", "--json"], env=census_env)
    cases.append(SchemaCase("retention.census.pass", 0, code=pass_code, out=pass_out))
    os.remove(os.path.join(census_vault, "note.md"))
    fail_code, fail_out = run([RETENTION, "census", "--json"], env=census_env)
    cases.append(SchemaCase("retention.census.fail", 1, code=fail_code, out=fail_out))
    no_index_home = os.path.join(tmp, "no-index-home")
    os.makedirs(no_index_home)
    no_index_env = dict(os.environ)
    no_index_env["HOME"] = no_index_home
    no_index_env["BM_VAULT_ROOT"] = census_vault
    cases.append(SchemaCase("retention.census.no-data", 2,
                            argv=[RETENTION, "census", "--json"], env=no_index_env))

    return tmp, cases


class TopLevelShape(unittest.TestCase):
    """Every case: valid JSON, exactly the five top-level keys, schema_version
    1, verdict in the fixed vocabulary and matching the exit code, and every
    finding shaped {kind, path, detail}."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.cases = _cases()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_tool_emits_the_shared_schema(self):
        for case in self.cases:
            with self.subTest(case=case.label):
                code, out = case.run()
                self.assertEqual(code, case.want_exit,
                                 "%s: exit %d, want %d: %s"
                                 % (case.label, code, case.want_exit, out[:300]))
                data = json.loads(out)
                self.assertEqual(set(data.keys()), TOP_LEVEL_KEYS,
                                 "%s: top-level keys %r != %r"
                                 % (case.label, set(data.keys()), TOP_LEVEL_KEYS))
                self.assertEqual(data["schema_version"], 1,
                                 "%s: schema_version %r != 1"
                                 % (case.label, data["schema_version"]))
                self.assertIsInstance(data["tool"], str, "%s: tool must be a string"
                                      % case.label)
                self.assertIn(data["verdict"], VERDICTS,
                              "%s: verdict %r not in %r"
                              % (case.label, data["verdict"], VERDICTS))
                if case.want_exit == 0:
                    want_verdict = "PASS"
                elif "no-data" in case.label:
                    want_verdict = "NO-DATA"
                else:
                    want_verdict = "FAIL"
                self.assertEqual(data["verdict"], want_verdict,
                                 "%s: verdict %r did not match exit code %d"
                                 % (case.label, data["verdict"], code))
                self.assertIsInstance(data["counts"], dict,
                                      "%s: counts must be an object" % case.label)
                self.assertIsInstance(data["findings"], list,
                                      "%s: findings must be a list" % case.label)
                for finding in data["findings"]:
                    self.assertEqual(set(finding.keys()), FINDING_KEYS,
                                     "%s: finding keys %r != %r"
                                     % (case.label, set(finding.keys()), FINDING_KEYS))


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""donecheck_L5c: parent done check for unit L5c.

The bar lives in scripts/l5c_audit.py (score, verify_evidence) and in the
committed run ledger at docs/plan/l5c/run-ledger.json. This parent check
reads those records and answers PASS, FAIL or NO-DATA:

  exit 0  PASS    every record present, current and consistent
  exit 1  FAIL    a readable record proves the bar is not met
  exit 2  NO-DATA missing, unreadable, corrupt, unknown or inconsistent

The last printed line starts with PASS:, FAIL: or NO-DATA:. Writes nothing
under --root; a deliverable module is imported by path.

Usage:
  python3 -B scripts/donecheck_L5c.py [--root DIR] [--selftest]

Public surface: main only. Every helper name starts with _.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from typing import List, Optional


def _validate_argv(argv):
    if argv is None:
        return
    if isinstance(argv, (list, tuple)):
        for item in argv:
            if not isinstance(item, str):
                raise ValueError(
                    "argv items must be strings, found %s" % type(item).__name__
                )
        return
    raise ValueError(
        "argv must be None, a list or a tuple of strings, found %s"
        % type(argv).__name__
    )


def _default_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _load_deliverable(root):
    module_path = os.path.join(root, "scripts", "l5c_audit.py")
    if not os.path.isfile(module_path):
        return None, "NO-DATA: L5c.5 has not landed (scripts/l5c_audit.py is missing)"
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        try:
            spec = importlib.util.spec_from_file_location(
                "_fx154_l5c_audit", module_path
            )
            if spec is None or spec.loader is None:
                return None, ("NO-DATA: L5c.5 has not landed "
                              "(cannot load scripts/l5c_audit.py)")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except ImportError as exc:
            return None, "NO-DATA: L5c.5 has not landed (ImportError: %s)" % exc
        except SyntaxError as exc:
            return None, "NO-DATA: L5c.5 has not landed (SyntaxError: %s)" % exc
    finally:
        sys.dont_write_bytecode = previous
    return module, None


def _check_meta(meta_rows):
    if len(meta_rows) != 9:
        return 2, ["NO-DATA: meta battery holds %d rows, exactly 9 required"
                   % len(meta_rows)]
    seen = set()
    for index, row in enumerate(meta_rows):
        if not isinstance(row, dict):
            return 2, ["NO-DATA: meta row %d is not an object" % index]
        mid = row.get("id")
        if not isinstance(mid, str) or not mid:
            return 2, ["NO-DATA: meta row %d carries no id" % index]
        if mid in seen:
            return 2, ["NO-DATA: duplicate meta id %r" % mid]
        seen.add(mid)
        status = row.get("status")
        if status != "KILLED":
            return 2, ["NO-DATA: meta row %d status is %r, not KILLED"
                       % (index, status)]
        if row.get("attributed") is not True:
            return 2, ["NO-DATA: meta row %d attributed is %r, not True"
                       % (index, row.get("attributed"))]
    return 0, []


def _run_check(root):
    module, message = _load_deliverable(root)
    if module is None:
        return 2, [message]
    try:
        score_fn = module.score
    except AttributeError:
        return 2, ["NO-DATA: L5c.5 has not landed (score is missing)"]
    try:
        verify_fn = module.verify_evidence
    except AttributeError:
        return 2, ["NO-DATA: L5c.5 has not landed (verify_evidence is missing)"]
    try:
        load_samples_fn = module.load_samples
    except AttributeError:
        return 2, ["NO-DATA: L5c.5 has not landed (load_samples is missing)"]

    ledger_path = os.path.join(root, "docs", "plan", "l5c", "run-ledger.json")
    try:
        with open(ledger_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        return 2, ["NO-DATA: cannot read the run ledger %s: %s"
                   % (ledger_path, exc)]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return 2, ["NO-DATA: the run ledger is not utf-8: %s" % exc]
    try:
        ledger = json.loads(text)
    except ValueError as exc:
        return 2, ["NO-DATA: the run ledger is not JSON: %s" % exc]
    if not isinstance(ledger, dict):
        return 2, ["NO-DATA: the run ledger must be an object, found %s"
                   % type(ledger).__name__]
    if ledger.get("status") == "NO-DATA":
        return 2, ["NO-DATA: the committed run ledger carries status NO-DATA; "
                   "no audit has run"]
    meta_rows = ledger.get("meta")
    if not isinstance(meta_rows, list):
        return 2, ["NO-DATA: the run ledger carries no meta list"]
    per_sample = ledger.get("per_sample")
    if not isinstance(per_sample, list):
        return 2, ["NO-DATA: the run ledger carries no per_sample list"]

    meta_code, meta_lines = _check_meta(meta_rows)
    if meta_code != 0:
        return meta_code, meta_lines

    samples_path = os.path.join(root, "docs", "plan", "l5c", "samples.json")
    try:
        registered = load_samples_fn(samples_path)
    except ValueError as exc:
        return 2, ["NO-DATA: cannot load the registered samples: %s" % exc]
    if not isinstance(registered, list):
        return 2, ["NO-DATA: load_samples returned %s, not a list"
                   % type(registered).__name__]
    registered_ids = []
    for index, row in enumerate(registered):
        if not isinstance(row, dict):
            return 2, ["NO-DATA: registered sample row %d is not an object"
                       % index]
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            return 2, ["NO-DATA: registered sample row %d carries no id" % index]
        registered_ids.append(rid)
    per_sample_ids = []
    for index, row in enumerate(per_sample):
        if not isinstance(row, dict):
            return 2, ["NO-DATA: per_sample row %d is not an object" % index]
        pid = row.get("id")
        if not isinstance(pid, str) or not pid:
            return 2, ["NO-DATA: per_sample row %d carries no id" % index]
        per_sample_ids.append(pid)
    if sorted(per_sample_ids) != sorted(registered_ids):
        return 2, ["NO-DATA: per_sample ids do not match the registered "
                   "sample ids"]

    try:
        result = score_fn(per_sample)
    except ValueError as exc:
        return 2, ["NO-DATA: score refused the per_sample rows: %s" % exc]
    if not isinstance(result, dict):
        return 2, ["NO-DATA: score did not return an object"]
    killed = result.get("killed")
    total = result.get("total")
    recomputed = result.get("gate")
    conditions = result.get("conditions")
    if not isinstance(conditions, dict):
        return 2, ["NO-DATA: score returned no conditions object"]
    if not isinstance(recomputed, bool):
        return 2, ["NO-DATA: score returned no gate boolean"]

    stored = ledger.get("gate")
    if not isinstance(stored, dict):
        return 2, ["NO-DATA: the run ledger carries no gate object"]
    stored_gate = stored.get("gate")
    if not isinstance(stored_gate, bool):
        return 2, ["NO-DATA: the run ledger gate.gate is not a boolean"]
    if stored_gate != recomputed:
        return 2, ["NO-DATA: stored gate %s disagrees with recomputed gate %s "
                   "(inconsistent record)" % (stored_gate, recomputed)]

    if not recomputed:
        false_names = sorted(name for name, value in conditions.items()
                             if value is not True)
        return 1, ["FAIL: sample gate false (killed %s of %s; conditions "
                   "false: %s)"
                   % (killed, total,
                      ", ".join(false_names) if false_names else "none")]

    doc_path = os.path.join(root, "docs", "architecture",
                            "L5C-TEST-INTEGRITY-AUDIT.md")
    try:
        problems = verify_fn(doc_path, ledger_path, root)
    except ValueError as exc:
        return 2, ["NO-DATA: verify_evidence refused its inputs: %s" % exc]
    if not isinstance(problems, list):
        return 2, ["NO-DATA: verify_evidence returned %s, not a list"
                   % type(problems).__name__]
    if problems:
        lines = ["evidence verification listed %d problem(s):" % len(problems)]
        for item in problems:
            lines.append("  %s" % (item if isinstance(item, str) else str(item)))
        lines.append("FAIL: L5c evidence has %d unresolved problem(s)"
                     % len(problems))
        return 1, lines

    return 0, ["PASS: meta 9 of 9 killed and attributed, samples %s of %s "
               "killed (gate true), evidence verified" % (killed, total)]


_FIXTURE_GREEN_VERIFY = """

def verify_evidence(doc_path, ledger_path, root):
    return []
"""

_FIXTURE_PROBLEM_VERIFY = """

def verify_evidence(doc_path, ledger_path, root):
    return ["HASH: fixture drift recorded for the test"]
"""

_FIXTURE_DELETE_VERIFY = """

del verify_evidence
"""


def _fixture_sample_ids():
    return ["VAULT-1", "VAULT-2", "VAULT-3", "VAULT-4",
            "DISPATCH-1", "DISPATCH-2", "DISPATCH-3",
            "HOOK-1", "HOOK-2", "HOOK-3"]


def _fixture_per_sample():
    return [
        {"id": "VAULT-1", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": True},
        {"id": "VAULT-2", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": True},
        {"id": "VAULT-3", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "VAULT-4", "category": "vault", "status": "SURVIVED",
         "attributed": False, "state_backed": False},
        {"id": "DISPATCH-1", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "DISPATCH-2", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "DISPATCH-3", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-1", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-2", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-3", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
    ]


def _fixture_meta():
    return [{"id": "META-%d" % i, "status": "KILLED", "attributed": True}
            for i in range(1, 10)]


def _fixture_ledger(per_sample=None, meta=None, stored_gate=True, status="RUN"):
    return {
        "status": status,
        "meta": _fixture_meta() if meta is None else meta,
        "per_sample": _fixture_per_sample() if per_sample is None else per_sample,
        "gate": {"gate": stored_gate, "conditions": {}},
    }


def _fixture_real_module_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "l5c_audit.py")


def _rmtree(path):
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name))
            except OSError:
                pass
        for name in dirnames:
            try:
                q = os.path.join(dirpath, name)
                os.remove(q) if os.path.islink(q) else os.rmdir(q)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _write_fixture(root, module_append, ledger, ledger_present=True,
                   samples=None):
    scripts = os.path.join(root, "scripts")
    os.makedirs(scripts, exist_ok=True)
    with open(_fixture_real_module_path(), "rb") as handle:
        data = handle.read()
    with open(os.path.join(scripts, "l5c_audit.py"), "wb") as handle:
        handle.write(data + module_append.encode("utf-8"))
    l5c_dir = os.path.join(root, "docs", "plan", "l5c")
    os.makedirs(l5c_dir, exist_ok=True)
    if samples is None:
        samples = [{"id": sid} for sid in _fixture_sample_ids()]
    with open(os.path.join(l5c_dir, "samples.json"), "wb") as handle:
        handle.write(json.dumps(samples).encode("utf-8"))
    if ledger_present:
        with open(os.path.join(l5c_dir, "run-ledger.json"), "wb") as handle:
            handle.write(json.dumps(ledger).encode("utf-8"))
    arch = os.path.join(root, "docs", "architecture")
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, "L5C-TEST-INTEGRITY-AUDIT.md"), "wb") as handle:
        handle.write(b"# fixture\n")


def _flip_to_survived(rows, index):
    flipped = list(rows)
    row = dict(flipped[index])
    row["status"] = "SURVIVED"
    row["attributed"] = False
    row["state_backed"] = False
    flipped[index] = row
    return flipped


def _case_green(tmp):
    _write_fixture(tmp, _FIXTURE_GREEN_VERIFY, _fixture_ledger())
    return 0, ["--root", tmp]


def _case_sample_survivor(tmp):
    rows = _flip_to_survived(_fixture_per_sample(), 7)
    _write_fixture(tmp, _FIXTURE_GREEN_VERIFY,
                   _fixture_ledger(per_sample=rows, stored_gate=False))
    return 1, ["--root", tmp]


def _case_meta_survivor(tmp):
    meta = list(_fixture_meta())
    meta[0] = dict(meta[0], status="SURVIVED")
    _write_fixture(tmp, _FIXTURE_GREEN_VERIFY, _fixture_ledger(meta=meta))
    return 2, ["--root", tmp]


def _case_ledger_missing(tmp):
    _write_fixture(tmp, _FIXTURE_GREEN_VERIFY, None, ledger_present=False)
    return 2, ["--root", tmp]


def _case_stored_gate_true(tmp):
    rows = _flip_to_survived(_fixture_per_sample(), 7)
    _write_fixture(tmp, _FIXTURE_GREEN_VERIFY,
                   _fixture_ledger(per_sample=rows, stored_gate=True))
    return 2, ["--root", tmp]


def _case_evidence_problem(tmp):
    _write_fixture(tmp, _FIXTURE_PROBLEM_VERIFY, _fixture_ledger())
    return 1, ["--root", tmp]


def _case_missing_verify(tmp):
    _write_fixture(tmp, _FIXTURE_DELETE_VERIFY, _fixture_ledger())
    return 2, ["--root", tmp]


def _selftest_cases():
    return (
        ("green", _case_green),
        ("sample_survivor", _case_sample_survivor),
        ("meta_survivor", _case_meta_survivor),
        ("ledger_missing", _case_ledger_missing),
        ("stored_gate_true_failing_samples", _case_stored_gate_true),
        ("evidence_problem", _case_evidence_problem),
        ("missing_verify_evidence", _case_missing_verify),
    )


def _run_selftest():
    cases = _selftest_cases()
    failures = []
    for name, builder in cases:
        try:
            tmp = tempfile.mkdtemp(prefix="fx154_l5c_selftest_")
        except OSError as exc:
            failures.append("%s (tmpdir: %s)" % (name, exc))
            continue
        try:
            expected, argv = builder(tmp)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    code = main(argv)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
            if code != expected:
                failures.append("%s (exit %s, expected %s)"
                                % (name, code, expected))
        except Exception as exc:
            failures.append("%s (raised %s: %s)"
                            % (name, type(exc).__name__, exc))
        finally:
            _rmtree(tmp)
    total = len(cases)
    if failures:
        print("selftest: %d cases, FAILED: %s" % (total, ", ".join(failures)))
        return 1
    print("selftest: %d cases, OK" % total)
    return 0


def main(argv=None):
    _validate_argv(argv)
    parser = argparse.ArgumentParser(
        prog="donecheck_L5c",
        description=("parent done check for unit L5c: committed ledger, real "
                     "gate and evidence lock."),
    )
    parser.add_argument("--root", default=None)
    parser.add_argument("--selftest", action="store_true")
    ns = parser.parse_args(list(argv) if argv is not None else None)
    if ns.selftest:
        return _run_selftest()
    root = ns.root if ns.root is not None else _default_root()
    if not isinstance(root, str) or not root:
        raise ValueError("--root must be a non-empty string")
    code, lines = _run_check(root)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

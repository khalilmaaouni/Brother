#!/usr/bin/env python3
"""L4 steps 5 and 6 (docs/plan/specs/L4.md section 6): the ObservationPack before and after token measurement
on one real gate log, then the assessment's fenced json block written from the numbers.

usage (repo root):
  python3 -B scripts/capture_l4_measurement.py --pending                      write the block with no measurement yet
  python3 -B scripts/capture_l4_measurement.py GATE_LOG OUT_DIR [--model haiku] [--max-turns 8] [--timeout 600]
  python3 -B scripts/capture_l4_measurement.py --sign "Owner Name"            REQ-11: the owner's hand, nothing else
  python3 -B scripts/capture_l4_measurement.py --selftest

GATE_LOG is a summary scripts/required_fast.sh printed whose PASS lines carry `[full: PATH]` handles (unit L4b.1),
for example the quiet-q*-gate.log that scripts/capture_l5d_quiet.sh leaves in its captures directory. The runner
picks the first PASS check whose handle file still exists and writes two logs in OUT_DIR: before (the handle
stripped from that line, the tree before L4b.1) and after (verbatim). It runs the fixed task
docs/architecture/l4-task-observation-pack.md once against each through a headless `claude -p --output-format json`
session, records each session's usage as one JSONL line under docs/architecture/l4-measurements/, validates each
with scripts/measure_session_tokens.py (the L4.2 control), and rewrites the fenced json block of
HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md between its L4-JSON markers.

WHAT IT NEVER DOES. It never writes human_sign_off on its own: REQ-11 keeps the sign off with the owner, so
scripts/donecheck_L4.py stays red on exactly that field until `--sign` is run by his hand. `--pending` writes a
block whose measurements map is empty and whose proposals name run ids that are not in it, so the validator refuses
by name (REQ-02) instead of reading "no fenced json block"; nothing in that block is a number nobody measured.
`--sign` refuses a block with no measurements: a signature under an empty table is a signature under nothing.

Exit 0 when the block was written, 2 (NO-DATA) when any input is missing, a session gives no usage, a handle file
is gone, or the rendered block would name an estimate. Never estimates a number."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HANDLE_RE = re.compile(r"^(PASS\s+exit 0\s+(\S+)\s.*?)\s+\[full: ([^\]]+)\]\s*$")
COUNTER = ("claude -p --output-format json, top level usage", "claude-code 2.1.284")
MARK_BEGIN, MARK_END = "<!-- L4-JSON-BEGIN -->", "<!-- L4-JSON-END -->"
TASK_REL = os.path.join("docs", "architecture", "l4-task-observation-pack.md")
DOC_REL = os.path.join("docs", "architecture", "HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md")
MEAS_REL = os.path.join("docs", "architecture", "l4-measurements")
BASELINE_REL = os.path.join("scripts", "solpi_baseline.json")
TASK_SPLIT = "verbatim (the two placeholders are filled by the runner)"
PENDING_BEFORE, PENDING_AFTER = "pending-before-run", "pending-after-run"
FORBIDDEN_WORD = "estimate"
PROPOSAL_TEXT = {
    "action_fusion": ("REJECTED", False, "no current call site: Brother's checks are one process per run_check name "
                      "(REQ-04 NO-FUSION); an edit and its validation command are two tool calls by the harness's own "
                      "design, and no gate file changes here"),
    "online_context_compact": ("REJECTED", False, "owner rule: compaction is a human discipline (checkpoint to disk, "
                               "fresh session at about 70 percent), never automated mid session"),
    "observation_pack": ("PROPOSED", True, "the PASS path keep landed as unit L4b.1 (scripts/required_fast.sh, the "
                         "pass_keep handle) and L4b.2 (scripts/check_all.sh); scripts/donecheck_L4b.py proves the gate's "
                         "counts, FAILED and NO-DATA lists and transition lines are identical before and after the keep "
                         "(REQ-03, REQ-05, REQ-06); the keep writes the whole $out and never summarizes"),
    "evidence_preserving_reducer": ("REJECTED", False, "no summarizer exists in the tree to verify quotes against, so "
                                    "there is no current behavior to change; the quote check idea is recorded, not built"),
}
PRESERVATION = ("python3 scripts/donecheck_L4b.py: PASS means counts, FAILED and NO-DATA lists and transition lines "
                "identical before and after the keep; scripts/test_l4b_rf_pass_keep.py 15 tests, "
                "test_l4b_ca_pass_keep.py 19 tests")


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def pick_check(log_text, isfile=os.path.isfile):
    """(check name, after line, before line) for the first PASS line whose handle file still exists, else Nones."""
    if not isinstance(log_text, str):
        return None, None, None
    for line in log_text.splitlines():
        m = HANDLE_RE.match(line)
        if m and isfile(m.group(3)):
            return m.group(2), line, m.group(1)
    return None, None, None


def run_session(claude, prompt, cwd, model, max_turns, timeout):
    """(result dict with usage, why not, started, ended). A session with no usage block is NO-DATA, never a count."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    argv = [claude, "-p", prompt, "--model", model, "--output-format", "json", "--max-turns", str(max_turns)]
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, "session gave no answer (%s)" % exc, started, datetime.now(timezone.utc)
    ended = datetime.now(timezone.utc)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "stdout is not json (exit %d): %s" % (proc.returncode, (proc.stdout + proc.stderr)[-400:]), started, ended
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None, "no usage block in the result", started, ended
    for key in ("input_tokens", "output_tokens"):
        if isinstance(usage.get(key), bool) or not isinstance(usage.get(key), int):
            return None, "usage.%s is not an int" % key, started, ended
    data["_exit_code"] = proc.returncode
    return data, "", started, ended


def build_block(baseline, measurements, before_id, after_id, delta, percent, check, sign_off=None):
    """The dict the validator reads. Pure, so the selftest can prove every shape it refuses."""
    proposals = []
    for mid, (status, applies, text) in PROPOSAL_TEXT.items():
        b, af = (before_id, after_id) if applies else (before_id, before_id)
        proposals.append({"proposal_id": "p-l4-" + mid, "mechanism_id": mid, "applies": applies, "status": status,
                          "current_behavior_change": text, "before_run_id": b, "after_run_id": af,
                          "delta_tokens": delta if applies else 0, "delta_percent": percent if applies else 0.0,
                          "preservation_proof": PRESERVATION if applies else "not applied, nothing to preserve"})
    # The validator resolves excerpt_path against the assessment document, the loader against the baseline file.
    mechanisms = {mid: dict(entry, excerpt_path=os.path.basename(DOC_REL)) for mid, entry in baseline.items()}
    if measurements:
        finding = ("after minus before is %d tokens (%.2f percent) on check %s; a saving needs a negative number, and "
                   "this figure is read from the two sessions' own usage blocks, never computed from anything else"
                   % (delta, percent, check))
    else:
        finding = ("NO-DATA: no session has run yet; the proposals name run ids that are not in measurements so the "
                   "validator refuses by name (REQ-02); python3 -B scripts/capture_l4_measurement.py GATE_LOG OUT_DIR "
                   "fills this block from two real headless sessions")
    block = {"auto_gate_write": False, "mechanisms": mechanisms, "measurements": measurements, "proposals": proposals,
             "measured_finding": finding}
    if sign_off is not None:
        block["human_sign_off"] = sign_off
    return block


def render(block):
    return "%s\n```json\n%s\n```\n%s" % (MARK_BEGIN, json.dumps(block, indent=1, sort_keys=True), MARK_END)


def read_block(doc):
    """The current json block between the markers, or None when there is none or it is corrupt."""
    if MARK_BEGIN not in doc or MARK_END not in doc:
        return None
    inner = doc.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
    m = re.search(r"```json\s*\n(.*?)```", inner, re.DOTALL)
    if m is None:
        return None
    try:
        block = json.loads(m.group(1))
    except ValueError:
        return None
    return block if isinstance(block, dict) else None


def write_block(doc_path, doc, block):
    """Replace whatever sits between the markers. Refuses (2) a block naming an estimate, so the validator never does."""
    rendered = render(block)
    if FORBIDDEN_WORD in rendered.lower():
        print("NO-DATA: the rendered block names an %s; refusing to write it" % FORBIDDEN_WORD)
        return 2
    head, rest = doc.split(MARK_BEGIN, 1)
    tail = rest.split(MARK_END, 1)[1]
    tmp = doc_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(head + rendered + tail)
    os.replace(tmp, doc_path)
    return 0


def load_inputs(tree):
    """(baseline, doc text, doc path) or (None, why, None)."""
    doc_path = os.path.join(tree, DOC_REL)
    try:
        baseline = json.load(open(os.path.join(tree, BASELINE_REL), encoding="utf-8"))
        doc = open(doc_path, encoding="utf-8").read()
    except (OSError, ValueError) as exc:
        return None, str(exc), None
    if not isinstance(baseline, dict) or not baseline:
        return None, "%s is not a non empty object" % BASELINE_REL, None
    if MARK_BEGIN not in doc or MARK_END not in doc:
        return None, "%s carries no %s ... %s markers" % (DOC_REL, MARK_BEGIN, MARK_END), None
    return baseline, doc, doc_path


def cmd_pending(tree):
    baseline, doc, doc_path = load_inputs(tree)
    if baseline is None:
        print("NO-DATA: %s" % doc)
        return 2
    rc = write_block(doc_path, doc, build_block(baseline, {}, PENDING_BEFORE, PENDING_AFTER, 0, 0.0, ""))
    if rc == 0:
        print("pending block written to %s: measurements empty, run ids %s and %s not in it, no human_sign_off"
              % (DOC_REL, PENDING_BEFORE, PENDING_AFTER))
    return rc


def cmd_sign(tree, name, today):
    """REQ-11: the owner's hand. The date is read from the clock, never typed."""
    if not isinstance(name, str) or not name.strip():
        print("NO-DATA: --sign needs the owner's name")
        return 2
    baseline, doc, doc_path = load_inputs(tree)
    if baseline is None:
        print("NO-DATA: %s" % doc)
        return 2
    block = read_block(doc)
    if block is None:
        print("NO-DATA: %s carries no readable json block to sign; run --pending or the measurement first" % DOC_REL)
        return 2
    if not isinstance(block.get("measurements"), dict) or not block["measurements"]:
        print("NO-DATA: the block holds no measurements; a signature under an empty table signs nothing")
        return 2
    block["human_sign_off"] = {"name": name.strip(), "date": today}
    rc = write_block(doc_path, doc, block)
    if rc == 0:
        print("human_sign_off written: %s, %s" % (name.strip(), today))
    return rc


def cmd_measure(a, tree):
    task_path = os.path.join(tree, TASK_REL)
    baseline, doc, doc_path = load_inputs(tree)
    if baseline is None:
        print("NO-DATA: %s" % doc)
        return 2
    try:
        log_text = open(a.gate_log, encoding="utf-8").read()
        task_raw = open(task_path, "rb").read()
    except OSError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    check, after_line, before_line = pick_check(log_text)
    if not check:
        print("NO-DATA: no PASS line in %s names a handle file that still exists" % a.gate_log)
        return 2
    try:
        tree_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True,
                                   timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        tree_hash = ""
    if not re.match(r"^[0-9a-f]{40}$", tree_hash):
        print("NO-DATA: tree hash unreadable")
        return 2
    task_text = task_raw.decode("utf-8", errors="replace")
    if TASK_SPLIT not in task_text:
        print("NO-DATA: %s no longer carries the verbatim task marker" % TASK_REL)
        return 2
    task_text = task_text.split(TASK_SPLIT, 1)[1]
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(os.path.join(tree, MEAS_REL), exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs = {}
    for label, line in (("before", before_line), ("after", after_line)):
        log_path = os.path.join(a.out_dir, "gate-log-%s.txt" % label)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(log_text.replace(after_line, line))
        prompt = task_text.replace("GATE_LOG", log_path).replace("CHECK_NAME", check).strip()
        print("[%s] check %s, log %s, session starting" % (label, check, log_path))
        sys.stdout.flush()
        data, why, started, ended = run_session(a.claude, prompt, tree, a.model, a.max_turns, a.timeout)
        if data is None:
            print("NO-DATA: %s run: %s" % (label, why))
            return 2
        raw_out = os.path.join(a.out_dir, "session-%s-%s.json" % (label, stamp))
        with open(raw_out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        u = data["usage"]
        rec = {"run_id": "r-%s-l4-op-%s" % (stamp, label), "session_id": str(data.get("session_id", "")),
               "started_at": started.isoformat(), "ended_at": ended.isoformat(),
               "model": ",".join(sorted((data.get("modelUsage") or {}).keys())) or a.model,
               "counter_name": COUNTER[0], "counter_version": COUNTER[1], "tree_hash": tree_hash,
               "task_path": TASK_REL, "task_sha256": sha256_bytes(task_raw),
               "input_tokens": int(u["input_tokens"]), "output_tokens": int(u["output_tokens"]),
               "cache_read_tokens": int(u.get("cache_read_input_tokens", 0) or 0),
               "cache_write_tokens": int(u.get("cache_creation_input_tokens", 0) or 0),
               "command": "claude -p <task> --model %s --output-format json --max-turns %d (%s log)"
                          % (a.model, a.max_turns, label),
               "exit_code": int(data["_exit_code"])}
        rec["total_tokens"] = rec["input_tokens"] + rec["output_tokens"] + rec["cache_read_tokens"] + rec["cache_write_tokens"]
        jsonl = os.path.join(tree, MEAS_REL, rec["run_id"] + ".jsonl")
        with open(jsonl, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        v = subprocess.run([sys.executable, "-B", os.path.join(tree, "scripts", "measure_session_tokens.py"), jsonl,
                            jsonl[:-6] + ".measurement.json"], cwd=tree, capture_output=True, text=True)
        if v.returncode != 0:
            print("NO-DATA: measure_session_tokens refused the %s log: %s" % (label, v.stderr.strip()))
            return 2
        rec["raw_log_sha256"] = sha256_bytes(open(jsonl, "rb").read())
        runs[label] = rec
        print("[%s] total %d tokens (in %d, out %d, cache read %d, cache write %d), exit %d" % (
            label, rec["total_tokens"], rec["input_tokens"], rec["output_tokens"], rec["cache_read_tokens"],
            rec["cache_write_tokens"], rec["exit_code"]))
    before, after = runs["before"], runs["after"]
    delta = after["total_tokens"] - before["total_tokens"]
    percent = 100.0 * delta / before["total_tokens"] if before["total_tokens"] else 0.0
    measurements = {r["run_id"]: {"task": "l4-observation-pack: quote the last output line of one passing fast gate check",
                                  "tree_hash": r["tree_hash"], "counter_name": r["counter_name"],
                                  "counter_version": r["counter_version"], "total_tokens": r["total_tokens"],
                                  "raw_log_sha256": r["raw_log_sha256"], "check": check, "model": r["model"],
                                  "log": os.path.join(MEAS_REL, r["run_id"] + ".jsonl")} for r in (before, after)}
    rc = write_block(doc_path, doc, build_block(baseline, measurements, before["run_id"], after["run_id"], delta,
                                                percent, check))
    if rc == 0:
        print("delta %d tokens (%.2f percent); block written to %s; human_sign_off left to the owner (REQ-11): "
              "python3 -B scripts/capture_l4_measurement.py --sign \"Owner Name\"" % (delta, percent, DOC_REL))
    return rc


def selftest():
    import tempfile
    sys.path.insert(0, HERE)
    import check_harness_efficiency_assessment as validator
    baseline = json.load(open(os.path.join(ROOT, BASELINE_REL), encoding="utf-8"))
    real_doc = open(os.path.join(ROOT, DOC_REL), encoding="utf-8").read()
    log = ("PASS    exit 0   version-truth         12s  ok  [full: /nowhere/a]\n"
           "PASS    exit 0   packs                  3s  ok  [full: /nowhere/b]\n"
           "FAIL    exit 1   surface                2s  no\n")
    meas = {"r-b": {"task": "t", "tree_hash": "h", "counter_name": "c", "counter_version": "1", "total_tokens": 100,
                    "raw_log_sha256": "a" * 64},
            "r-a": {"task": "t", "tree_hash": "h", "counter_name": "c", "counter_version": "1", "total_tokens": 90,
                    "raw_log_sha256": "b" * 64}}
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "docs", "architecture"))
        json.dump(baseline, open(os.path.join(d, BASELINE_REL), "w"))
        doc_path = os.path.join(d, DOC_REL)
        # The excerpt (the document itself) must carry the baseline's quote, as the real document does.
        quote = baseline["observation_pack"]["source_quote"]
        with open(doc_path, "w", encoding="utf-8") as fh:
            fh.write("# t\n\n%s\n\n%s\n%s\n" % (quote, MARK_BEGIN, MARK_END))
        pending_rc = cmd_pending(d)
        pending_errors = validator.validate_assessment(doc_path)
        sign_pending_rc = cmd_sign(d, "Owner", "2026-10-03")
        full = build_block(baseline, meas, "r-b", "r-a", -10, -10.0, "packs")
        write_block(doc_path, open(doc_path, encoding="utf-8").read(), full)
        unsigned_errors = validator.validate_assessment(doc_path)
        sign_rc = cmd_sign(d, "Owner", "2026-10-03")
        signed_errors = validator.validate_assessment(doc_path)
        signed = read_block(open(doc_path, encoding="utf-8").read())
        bad_word = dict(full, measured_finding="an estimate")
        refused_rc = write_block(doc_path, open(doc_path, encoding="utf-8").read(), bad_word)
        still_signed = read_block(open(doc_path, encoding="utf-8").read())
        blank_sign_rc = cmd_sign(d, "  ", "2026-10-03")
    # The entry point itself, against a stub claude in a scratch git tree: the control lives in cmd_measure.
    with tempfile.TemporaryDirectory() as d:
        tree = os.path.join(d, "tree")
        for sub in ("scripts", os.path.join("docs", "architecture")):
            os.makedirs(os.path.join(tree, sub))
        for name in ("gate_order.py", "measure_session_tokens.py"):
            with open(os.path.join(HERE, name), "rb") as src, open(os.path.join(tree, "scripts", name), "wb") as dst:
                dst.write(src.read())
        json.dump(baseline, open(os.path.join(tree, BASELINE_REL), "w"))
        with open(os.path.join(tree, DOC_REL), "w", encoding="utf-8") as fh:
            fh.write("# t\n\n%s\n\n%s\n%s\n" % (quote, MARK_BEGIN, MARK_END))
        with open(os.path.join(tree, TASK_REL), "w", encoding="utf-8") as fh:
            fh.write("# task\n\n## given %s\n\nRead GATE_LOG for CHECK_NAME.\n" % TASK_SPLIT)
        subprocess.run(["git", "init", "-q", tree], check=True)
        subprocess.run(["git", "-C", tree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "t"], check=True)
        handle = os.path.join(d, "handle.txt")
        open(handle, "w").write("last line\n")
        glog = os.path.join(d, "gate.log")
        open(glog, "w").write("PASS    exit 0   packs   3s  ok  [full: %s]\n" % handle)
        stub = os.path.join(d, "claude")
        with open(stub, "w") as fh:
            fh.write("#!/bin/sh\ncase \"$2\" in *gate-log-before*) o=70;; *) o=20;; esac\n"
                     "printf '{\"usage\":{\"input_tokens\":10,\"output_tokens\":%s,\"cache_read_input_tokens\":5,"
                     "\"cache_creation_input_tokens\":0},\"session_id\":\"s\",\"result\":\"last line\"}' \"$o\"\n")
        os.chmod(stub, 0o755)
        nousage = os.path.join(d, "claude-nousage")
        # The one condition this fixture isolates: everything else the L4.2 control reads is present.
        open(nousage, "w").write("#!/bin/sh\nprintf '{\"result\":\"x\",\"session_id\":\"s\"}'\n")
        os.chmod(nousage, 0o755)
        out = os.path.join(d, "out")
        measure_rc = main([glog, out, "--tree", tree, "--claude", stub])
        measured = read_block(open(os.path.join(tree, DOC_REL), encoding="utf-8").read())
        measured_errors = validator.validate_assessment(os.path.join(tree, DOC_REL))
        op = [p for p in measured.get("proposals", []) if p["mechanism_id"] == "observation_pack"][0]
        jsonl_count = len([n for n in os.listdir(os.path.join(tree, MEAS_REL)) if n.endswith(".jsonl")])
        nousage_rc = main([glog, os.path.join(d, "out2"), "--tree", tree, "--claude", nousage])
        after_nousage = read_block(open(os.path.join(tree, DOC_REL), encoding="utf-8").read())
        os.remove(handle)
        gone_rc = main([glog, os.path.join(d, "out3"), "--tree", tree, "--claude", stub])
    cases = [
        ("two stub sessions fill the block with two measurements the L4.2 control accepted",
         measure_rc == 0 and len(measured["measurements"]) == 2 and jsonl_count == 2),
        ("the delta is read from the two usage blocks: after 35 minus before 85 is minus 50 tokens",
         op["delta_tokens"] == -50 and abs(op["delta_percent"] - (100.0 * -50 / 85)) < 1e-9),
        ("a measured block fails the validator on the sign off alone",
         measured_errors == ["validate_assessment: human_sign_off must be an object with a name and an ISO date (REQ-11)"]),
        ("a session with no usage block is NO-DATA and the block is left as it was",
         nousage_rc == 2 and after_nousage == measured),
        ("a handle file that is gone is NO-DATA", gone_rc == 2),
        ("a log with no live handle file names no check", pick_check(log) == (None, None, None)),
        ("the first PASS line whose handle exists is picked, handle stripped for before",
         pick_check(log, isfile=lambda p: p == "/nowhere/b")[0] == "packs"
         and "[full:" not in pick_check(log, isfile=lambda p: p == "/nowhere/b")[2]),
        ("a non string log names no check", pick_check(None) == (None, None, None)),
        ("the pending block is written", pending_rc == 0),
        ("the pending block is refused by name on its run ids and its missing sign off",
         any(PENDING_BEFORE in e for e in pending_errors) and any("human_sign_off" in e for e in pending_errors)
         and not any("no fenced json block" in e for e in pending_errors)),
        ("signing a block with no measurements is refused", sign_pending_rc == 2),
        ("a measured block without sign off fails on exactly the sign off",
         unsigned_errors == ["validate_assessment: human_sign_off must be an object with a name and an ISO date (REQ-11)"]),
        ("the owner's signature closes the validator", sign_rc == 0 and signed_errors == []),
        ("the signature carries the name and the clock date",
         signed.get("human_sign_off") == {"name": "Owner", "date": "2026-10-03"}),
        ("a block naming an estimate is refused and the document is left as it was",
         refused_rc == 2 and still_signed == signed),
        ("a blank name cannot sign", blank_sign_rc == 2),
        ("the real document carries the markers", MARK_BEGIN in real_doc and MARK_END in real_doc),
        ("the proposal texts never name an estimate",
         FORBIDDEN_WORD not in json.dumps(PROPOSAL_TEXT).lower() and FORBIDDEN_WORD not in PRESERVATION.lower()),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gate_log", nargs="?")
    ap.add_argument("out_dir", nargs="?")
    ap.add_argument("--tree", default=ROOT)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-turns", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--claude", default=os.environ.get("L4_CLAUDE_CMD") or os.environ.get("CLAUDE_CODE_EXECPATH") or "claude")
    ap.add_argument("--pending", action="store_true", help="write the block with no measurement yet")
    ap.add_argument("--sign", metavar="NAME", help="REQ-11: write human_sign_off with NAME and today's date")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    tree = os.path.realpath(a.tree)
    if a.pending:
        return cmd_pending(tree)
    if a.sign is not None:
        return cmd_sign(tree, a.sign, datetime.now().astimezone().date().isoformat())
    if not a.gate_log or not a.out_dir:
        ap.error("GATE_LOG and OUT_DIR are required unless --pending or --sign is given")
    return cmd_measure(a, tree)


if __name__ == "__main__":
    sys.exit(main())

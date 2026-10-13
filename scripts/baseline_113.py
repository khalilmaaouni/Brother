#!/usr/bin/env python3
"""BL1: freeze the 1.1.2 baseline that every 1.1.3 claim is measured against.

  baseline_113.py --capture --out DIR --import NAME=LOGPATH ...   write DIR/baseline.json once (never overwritten)
  baseline_113.py --check FILE [--root DIR]                       PASS (0), the problems (1), NO-DATA when absent (2)
  baseline_113.py --controls FILE                                 the NO-DATA and set counts of a controls file

One verdict rule (derive_verdict): provenance NO-DATA or no exit code reads NO-DATA, a nonzero exit or a fail count
reads FAIL, any no-data count reads NO-DATA, otherwise PASS. The checker re-derives every stored verdict, so a fail
cannot be stored as a pass, and binds every log copy by its sha256. required_fast is never run here: its log is imported.
"""
import datetime
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional

VERDICTS = ("PASS", "FAIL", "NO-DATA", "BLOCKED")
PROVENANCE = ("EXECUTED", "IMPORTED", "NO-DATA")
ENTRY_NAMES = ("required_fast", "version_truth", "host_capability", "competitive_controls")
TOP_KEYS = ("schema", "v112_tag_commit", "hub_release_commit", "base_commit", "python3",
            "usr_bin_python3", "macos", "entries")
ENTRY_KEYS = ("name", "command", "exit_code", "counts", "log", "log_sha256", "captured_at",
              "provenance", "verdict")
SCHEMA = "brother-baseline-113/1"
LOGS = os.path.join("benchmarks", "results", "1.1.3-preflight", "logs")
COUNTS_RE = re.compile(r"pass\s+(\d+)\s+fail\s+(\d+)\s+no-data\s+(\d+)")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def sha256_file(path: str) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def redact(text: str, home: str) -> str:
    return text.replace(home, "<HOME>") if home else text


def parse_counts(text: str) -> Optional[Dict[str, int]]:
    m = None
    for m in COUNTS_RE.finditer(text):
        pass
    if m is None:
        return None
    return {"pass": int(m.group(1)), "fail": int(m.group(2)), "no_data": int(m.group(3))}


def controls_counts(path: str) -> Dict[str, int]:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {"no_data": -1, "set": -1}
    out = {"no_data": 0, "set": 0}

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and v.startswith("NO-DATA"):
            out["no_data"] += 1
        else:
            out["set"] += 1
    walk(doc)
    return out


def derive_verdict(exit_code: Optional[int], counts: Optional[Dict[str, int]], provenance: str) -> str:
    if provenance == "NO-DATA" or exit_code is None or isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return "NO-DATA"
    if exit_code != 0:
        return "FAIL"
    c = counts if isinstance(counts, dict) else {}
    if any(not isinstance(c.get(k, 0), int) for k in ("fail", "no_data")):
        return "NO-DATA"
    if c.get("fail", 0) > 0:
        return "FAIL"
    if c.get("no_data", 0) != 0:   # -1 is an unreadable controls file: NO-DATA, never a pass
        return "NO-DATA"
    return "PASS"


def check_baseline(doc: object, root: str) -> List[str]:
    if not isinstance(doc, dict):
        return ["the baseline is not a JSON object"]
    probs = ["missing top level key: %s" % k for k in TOP_KEYS if k not in doc]
    for k in ("v112_tag_commit", "hub_release_commit", "base_commit"):
        v = doc.get(k)
        if k in doc and not (isinstance(v, str) and (SHA_RE.match(v) or v == "NO-DATA")):
            probs.append("%s is not 40 hex characters or NO-DATA" % k)
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return probs + ["entries is not a list"]
    seen = []
    for e in entries:
        if not isinstance(e, dict):
            probs.append("an entry is not an object")
            continue
        name = e.get("name")
        if name not in ENTRY_NAMES:
            probs.append("unrecognized entry name: %r" % (name,))
        elif name in seen:
            probs.append("entry repeated: %s" % name)
        seen.append(name)
        miss = [k for k in ENTRY_KEYS if k not in e]
        if miss:
            probs.append("entry %s missing key(s): %s" % (name, ", ".join(miss)))
            continue
        if e["verdict"] not in VERDICTS:
            probs.append("entry %s verdict %r is not one of %s" % (name, e["verdict"], ", ".join(VERDICTS)))
        if e["provenance"] not in PROVENANCE:
            probs.append("entry %s provenance %r is not one of %s" % (name, e["provenance"], ", ".join(PROVENANCE)))
        want = derive_verdict(e["exit_code"], e["counts"], e["provenance"])
        if e["verdict"] in VERDICTS and e["verdict"] != want and e["verdict"] != "BLOCKED":
            probs.append("entry %s stores %s but its own fields derive %s" % (name, e["verdict"], want))
        if e["provenance"] in ("EXECUTED", "IMPORTED"):
            lp = os.path.join(root, LOGS, os.path.basename(str(e["log"])))
            if not os.path.isfile(lp):
                probs.append("entry %s log absent: %s" % (name, lp))
            elif sha256_file(lp) != e["log_sha256"]:
                probs.append("entry %s log no longer hashes to its recorded sha256" % name)
    for n in ENTRY_NAMES:
        if n not in seen:
            probs.append("entry missing: %s" % n)
    return probs


def _run(cmd: List[str]) -> tuple:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return None, "NO-DATA: %s" % e


def _first(cmd: List[str]) -> str:
    rc, out = _run(cmd)
    line = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    return line or "NO-DATA"


def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def capture(out_dir: str, imports: Dict[str, str]) -> int:
    target = os.path.join(out_dir, "baseline.json")
    if os.path.exists(target):
        print("REFUSED: %s already holds a frozen baseline; it is never rewritten" % out_dir)
        return 1
    logs = os.path.join(out_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    home = os.path.expanduser("~")
    today = datetime.date.today()
    runs = {"version_truth": [sys.executable, "-B", "scripts/test_version_truth.py"],
            "host_capability": [sys.executable, "scripts/host_capability.py"],
            "competitive_controls": [sys.executable, "scripts/baseline_113.py", "--controls",
                                     "benchmarks/competitive/CONTROLS.json"]}
    entries = []
    for name in ENTRY_NAMES:
        if name in imports:
            src = imports[name]
            try:
                with open(src, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                mt = datetime.datetime.fromtimestamp(os.path.getmtime(src))
            except OSError as e:
                entries.append({"name": name, "command": "import " + src, "exit_code": None, "counts": None, "log": "",
                                "log_sha256": "", "captured_at": "NO-DATA", "provenance": "NO-DATA",
                                "verdict": "NO-DATA", "why": str(e)})
                continue
            m = re.findall(r"^exit=(\d+)$", text, re.M)
            rc = int(m[-1]) if m else None
            prov = "EXECUTED" if mt.date() == today else "IMPORTED"
            cmd, at = "sh scripts/required_fast.sh" if name == "required_fast" else "import " + os.path.basename(src), mt.isoformat(timespec="seconds")
        else:
            rc, text = _run(runs[name])
            prov = "EXECUTED" if rc is not None else "NO-DATA"
            cmd, at = " ".join(runs[name][1:] if runs[name][0] == sys.executable else runs[name]), datetime.datetime.now().isoformat(timespec="seconds")
        counts = parse_counts(text) if name == "required_fast" else None
        if name == "competitive_controls":
            try:
                counts = json.loads(text.strip().splitlines()[-1])
            except (ValueError, IndexError):
                counts = {"no_data": -1, "set": -1}
        log = name + ".log"
        _write(os.path.join(logs, log), redact(text, home))
        entries.append({"name": name, "command": cmd, "exit_code": rc, "counts": counts, "log": log,
                        "log_sha256": sha256_file(os.path.join(logs, log)), "captured_at": at, "provenance": prov,
                        "verdict": derive_verdict(rc, counts, prov)})

    def rev(ref):
        v = _first(["git", "rev-parse", ref])
        return v if SHA_RE.match(v) else "NO-DATA"
    doc = {"schema": SCHEMA, "v112_tag_commit": rev("v1.1.2^{commit}"), "hub_release_commit": rev("hub/cut/1.1.2-2026-10-11"),
           "base_commit": rev("HEAD"), "python3": _first([sys.executable, "--version"]),
           "usr_bin_python3": _first(["/usr/bin/python3", "--version"]), "macos": _first(["sw_vers", "-productVersion"]),
           "entries": entries}
    _write(target, json.dumps(doc, indent=1) + "\n")
    print("wrote %s: %s" % (target, ", ".join("%s %s" % (e["name"], e["verdict"]) for e in entries)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["--controls"] and len(a) == 2:
        print(json.dumps(controls_counts(a[1])))
        return 0
    if a[:1] == ["--check"] and len(a) in (2, 4):
        root = a[3] if len(a) == 4 and a[2] == "--root" else ROOT
        if not os.path.isfile(a[1]):
            print("NO-DATA: no baseline at %s" % a[1])
            return 2
        try:
            with open(a[1], encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            print("FAIL: the baseline is not readable JSON (%s)" % e)
            return 1
        probs = check_baseline(doc, root)
        for p in probs:
            print("FAIL: " + p)
        if not probs:
            print("PASS baseline %s: %s" % (a[1], ", ".join("%s %s" % (e["name"], e["verdict"]) for e in doc["entries"])))
        return 1 if probs else 0
    if a[:1] == ["--capture"] and "--out" in a:
        out = a[a.index("--out") + 1]
        imports = {}
        for i, x in enumerate(a):
            if x == "--import" and i + 1 < len(a) and "=" in a[i + 1]:
                k, v = a[i + 1].split("=", 1)
                imports[k] = v
        return capture(out, imports)
    print("usage: baseline_113.py --capture --out DIR [--import NAME=LOG ...] | --check FILE [--root DIR] | --controls FILE")
    return 2


if __name__ == "__main__":
    sys.exit(main())

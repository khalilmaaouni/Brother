"""BK1.a: the frozen controls of the competitive benchmark, the cross arm refusal and the scoring script digest.

A composite across arms is allowed only when every comparability control in benchmarks/competitive/CONTROLS.json is
set, every required arm holds a complete result, and the scoring script has not moved since the digest was frozen
or since any result was recorded. Missing is NO-DATA, moved is FAIL, doubt is never allowed. Never prints a score.

  python3 scripts/bench_controls.py --check [CONTROLS]           exit 0 allowed, 2 NO-DATA, 1 FAIL
  python3 scripts/bench_controls.py --freeze-digest [CONTROLS]   writes scoring.sha256 atomically
"""
import hashlib
import json
import math
import os
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

DIMENSIONS = ("tests", "scope_creep", "evidence_quality", "false_claims", "interventions", "tokens")
REQUIRED_ARMS = ("brother_1_1_1", "brother_1_1_2", "brother_1_1_3", "gsd", "superpowers", "compound", "bmad",
                 "vanilla_claude_code")
SCORING_SCRIPT = "benchmarks/competitive/scripts/competitive_score.py"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONTROLS = os.path.join(REPO, "benchmarks", "competitive", "CONTROLS.json")
BASE_CONTROLS = ("intervention_ceiling", "machine", "model", "result_root", "source_commit", "token_ceiling")
USAGE = "usage: bench_controls.py --check [CONTROLS] | --freeze-digest [CONTROLS]"


def _no_data(v: object) -> bool:
    return v is None or v == "" or (isinstance(v, str) and v.startswith("NO-DATA"))


def _set_text(v: object) -> bool:
    """A text control is set only as a non empty string not beginning NO-DATA; any other type is unrecognized."""
    return isinstance(v, str) and not _no_data(v)


def _number(v: object) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _dict(v: object) -> Dict:
    return v if isinstance(v, dict) else {}


def sha256_file(path: str) -> str:
    """The hex sha256 of a file's bytes, or "" when it cannot be read (a digest that cannot be proven)."""
    if not isinstance(path, str) or not path:
        raise ValueError("sha256_file needs a non empty path string")
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
    except (OSError, ValueError):
        return ""
    return h.hexdigest()


def _machine_captured(machine: Dict) -> bool:
    fields, captured = machine.get("descriptor_fields"), machine.get("captured")
    if not isinstance(fields, list) or not fields or not isinstance(captured, dict):
        return False
    for f in fields:
        if not isinstance(f, str) or not f:
            return False
        v = captured.get(f)
        if _no_data(v) or (isinstance(v, (list, dict, tuple)) and not v):
            return False
    return True


def missing_controls(controls: object) -> List[str]:
    """Sorted names of the controls that are NO-DATA; a controls that is not a dict misses every one."""
    if not isinstance(controls, dict):
        return sorted(list(BASE_CONTROLS) + ["installed_ref:" + a for a in REQUIRED_ARMS])
    out = []
    if not _set_text(controls.get("model_id")) and not _set_text(controls.get("model_exception")):
        out.append("model")
    if not _set_text(controls.get("start_commit")):
        out.append("source_commit")
    if not _number(controls.get("token_budget")):
        out.append("token_ceiling")
    if not _number(controls.get("intervention_budget")):
        out.append("intervention_ceiling")
    if not _machine_captured(_dict(controls.get("machine"))):
        out.append("machine")
    if not _set_text(_dict(controls.get("raw_artifacts")).get("root")):
        out.append("result_root")
    arms = _dict(controls.get("arms"))
    for arm in REQUIRED_ARMS:
        if not _set_text(_dict(arms.get(arm)).get("installed_ref")):
            out.append("installed_ref:" + arm)
    return sorted(out)


def _dimension_reason(arm: str, dim: str, v: object) -> Optional[str]:
    if isinstance(v, str) and v in ("PASS", "FAIL"):
        return None
    if _number(v):
        return None
    if _no_data(v):
        return "arm %s dimension %s is NO-DATA" % (arm, dim)
    return "arm %s dimension %s holds an unrecognized verdict" % (arm, dim)


def composite_decision(controls: object, root: str) -> Tuple[str, List[str]]:
    """("PASS", []) only when nothing is missing and nothing moved; ("FAIL", reasons) when the scoring script moved
    or cannot be read; ("NO-DATA", reasons) otherwise. Never a ranking or a number."""
    if not isinstance(controls, dict):
        return "NO-DATA", ["control missing: " + n for n in missing_controls(controls)]
    nodata = ["control missing: " + n for n in missing_controls(controls)]
    fail = []
    live = sha256_file(os.path.join(root, SCORING_SCRIPT)) if isinstance(root, str) and root else ""
    if not live:
        fail.append("scoring script %s cannot be read, its digest cannot be proven" % SCORING_SCRIPT)
    scoring = _dict(controls.get("scoring"))
    if scoring.get("script") != SCORING_SCRIPT:
        nodata.append("scoring.script does not name %s" % SCORING_SCRIPT)
    frozen = scoring.get("sha256")
    if not _set_text(frozen):
        nodata.append("scoring.sha256 is NO-DATA: the scoring script digest is not frozen")
    elif live and frozen != live:
        fail.append("scoring script %s digest differs from the frozen scoring.sha256" % SCORING_SCRIPT)
    arms = _dict(controls.get("arms"))
    for arm in REQUIRED_ARMS:
        result = _dict(arms.get(arm)).get("result")
        if not isinstance(result, dict):
            nodata.append("arm %s has no result" % arm)
            continue
        dims = result.get("dimensions")
        if not isinstance(dims, dict):
            nodata.append("arm %s result has no dimensions" % arm)
            dims = {}
        for dim in DIMENSIONS:
            reason = _dimension_reason(arm, dim, dims.get(dim))
            if reason:
                nodata.append(reason)
        recorded = result.get("scoring_sha256")
        if not _set_text(recorded):
            nodata.append("arm %s result has no scoring_sha256" % arm)
        elif live and recorded != live:
            fail.append("scoring script changed after arm %s was recorded" % arm)
    if fail:
        return "FAIL", sorted(fail)
    if nodata:
        return "NO-DATA", sorted(nodata)
    return "PASS", []


def _refuse(msg: str) -> str:
    sys.stderr.write("REFUSED: %s\n" % msg)
    return ""


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


def _stale_result(doc: Dict, live: str) -> Optional[str]:
    for name, entry in _dict(doc.get("arms")).items():
        result = _dict(entry).get("result") if isinstance(entry, dict) else None
        if result is None:
            continue
        if not isinstance(result, dict) or result.get("scoring_sha256") != live:
            return str(name)
    return None


def freeze_scoring_digest(controls_path: str, root: str) -> str:
    """Write the live digest of SCORING_SCRIPT into scoring.sha256 through a temporary file and os.replace; return
    it, or "" with a stderr line when refused (any arm already holds a result under another digest)."""
    if not isinstance(controls_path, str) or not controls_path or not isinstance(root, str) or not root:
        return _refuse("freeze needs a controls path and a root, both non empty strings")
    live = sha256_file(os.path.join(root, SCORING_SCRIPT))
    if not live:
        return _refuse("scoring script %s cannot be read" % SCORING_SCRIPT)
    before = _read_bytes(controls_path)
    if before is None:
        return _refuse("%s cannot be read" % controls_path)
    try:
        doc = json.loads(before.decode("utf-8"))
    except ValueError:
        return _refuse("%s is not JSON" % controls_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("scoring"), dict):
        return _refuse("%s has no scoring object" % controls_path)
    if doc["scoring"].get("script") != SCORING_SCRIPT:
        return _refuse("scoring.script does not name %s" % SCORING_SCRIPT)
    stale = _stale_result(doc, live)
    if stale is not None:
        return _refuse("arm %s already holds a result under another scoring digest" % stale)
    doc["scoring"]["sha256"] = live
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    folder = os.path.dirname(os.path.abspath(controls_path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".controls.", suffix=".tmp", dir=folder)
    except OSError as e:
        return _refuse("cannot create a temporary file beside %s: %s" % (controls_path, e))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if _read_bytes(controls_path) != before:   # a second actor wrote since the read: never a lost update
            os.unlink(tmp)
            return _refuse("%s changed during the freeze" % controls_path)
        os.replace(tmp, controls_path)
    except OSError as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return _refuse("freeze failed, %s untouched: %s" % (controls_path, e))
    return live


def _load(path: str) -> object:
    raw = _read_bytes(path)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _check(path: str) -> int:
    controls = _load(path)
    verdict, reasons = composite_decision(controls, REPO)
    if verdict == "PASS":
        print("COMPOSITE: allowed")
    else:
        print("COMPOSITE: refused, %d reason(s)" % len(reasons))
        print("DECISION: %s" % verdict)
        for r in reasons:
            print("  - %s" % r)
    if isinstance(controls, dict):
        exception = controls.get("model_exception")
        if _set_text(exception):
            print("DISCLOSED COMPARABILITY EXCEPTION: %s" % exception)
        arms = _dict(controls.get("arms"))
        for name in sorted(str(a) for a in arms if a not in REQUIRED_ARMS):
            print("EXTRA ARM, ignored for the decision: %s" % name)
        for arm in REQUIRED_ARMS:
            result = _dict(arms.get(arm)).get("result")
            if isinstance(result, dict) and isinstance(result.get("recorded_at"), str):
                print("arm %s recorded_at (shown, not judged): %s" % (arm, result["recorded_at"]))
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise SystemExit(2)
    if len(argv) in (1, 2) and argv[0] in ("--check", "--freeze-digest"):
        path = argv[1] if len(argv) == 2 else DEFAULT_CONTROLS
        if argv[0] == "--check":
            return _check(path)
        digest = freeze_scoring_digest(path, REPO)
        if not digest:
            return 1
        print("FROZEN: scoring.sha256 of %s written" % SCORING_SCRIPT)
        return 0
    sys.stderr.write(USAGE + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Offline conformance of the three transport adapters, and the receipt that binds a role to what was proved (FX-31.8).

run_conformance      runs the requested cases with no process and no network: C11 (an absent cost is NOT_MEASURED,
                     a genuine reported zero stays 0.0) and C18 (a provider refusal is its own status, never an
                     answer, never a plain failure). Without offline=True nothing is run and every case reads NO-DATA.
receipt_matches      one receipt entry against the three hashes measured NOW: adapter file, executable, registry row.
                     All three are compared, none short circuits, so a binary replaced after the receipt was written
                     fails here.
file_sha256 / row_sha256 / adapter_sha256   the one place each hash is made, so loop_roles and the tests share it.
make_receipt_entry   the receipt entry for one role: the hashes plus the two case verdicts, from a conformance report.
main                 the command line FX-31's unit done check names: --transport bridge,claude,codex --offline. Exit 0
                     only when every requested case PASSED, 1 when one FAILED, 2 when nothing proved anything (NO-DATA).

A receipt that is stale, incomplete or malformed never matches; a hash that cannot be measured raises ValueError.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adapters as A  # noqa: E402
from adapters import bridge as _bridge  # noqa: E402
from adapters import claude as _claude  # noqa: E402
from adapters import codex as _codex  # noqa: E402

SCHEMA = 1
CASES = ("C11", "C18")
PASS, FAIL, NO_DATA = "PASS", "FAIL", "NO-DATA"
HEX64 = re.compile(r"[0-9a-f]{64}")
ENTRY_KEYS = ("model", "transport", "adapter_sha256", "binary_path", "binary_sha256", "row_sha256") + CASES


def _is_hash(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def file_sha256(path):
    """SHA-256 hex of a file's bytes. A path that is not text, or a file that cannot be read, is ValueError."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("file_sha256 wants a path")
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError("cannot read %.120s for hashing (%s)" % (path, type(exc).__name__))
    return digest.hexdigest()


def _plain(obj):
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    raise TypeError("not hashable as a registry row: %s" % type(obj).__name__)


def row_sha256(row):
    """SHA-256 hex of a registry row's canonical JSON (sorted keys; a set of kinds becomes a sorted list)."""
    if not isinstance(row, dict):
        raise ValueError("row_sha256 wants a registry row mapping")
    try:
        text = json.dumps(row, sort_keys=True, separators=(",", ":"), default=_plain, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("a registry row that cannot be written as JSON has no hash (%s)" % type(exc).__name__)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def adapter_sha256(transport):
    """SHA-256 hex of the adapter file of a transport in this tree; ValueError for any other value."""
    if not isinstance(transport, str) or transport not in A.TRANSPORTS:
        raise ValueError("unknown transport %.60r; this contract carries %s" % (transport, ", ".join(A.TRANSPORTS)))
    return file_sha256(os.path.join(HERE, "adapters", transport + ".py"))


def receipt_matches(receipt, adapter_hash, binary_hash, row_hash):
    """True only when the receipt entry is well formed and its three stored hashes equal the three given. A binary
    replaced since the receipt was written differs here. Anything malformed is False, never True."""
    if not isinstance(receipt, dict):
        return False
    given = (adapter_hash, binary_hash, row_hash)
    stored = (receipt.get("adapter_sha256"), receipt.get("binary_sha256"), receipt.get("row_sha256"))
    if not all(_is_hash(h) for h in given + stored):
        return False
    same = [hmac.compare_digest(s.encode("ascii"), g.encode("ascii")) for s, g in zip(stored, given)]
    return all(same)


def _fixtures(transport):
    """(adapter, row, zero_result, absent_result, refused_result) for one transport. Only strings and dicts."""
    if transport == "bridge":
        return (_bridge.BridgeAdapter(), {"transport": "bridge"},
                {"returncode": 0, "stdout": "ok", "stderr": "[billed] usd=0.000000 attempts=1 known=yes"},
                {"returncode": 0, "stdout": "ok", "stderr": ""},
                {"returncode": 0, "stdout": "provider refused this call", "stderr": ""})
    if transport == "claude":
        return (_claude.ClaudeAdapter(), {"transport": "claude", "id": "claude-opus-5"},
                {"returncode": 0, "stdout": json.dumps({"result": "ok", "total_cost_usd": 0})},
                {"returncode": 0, "stdout": json.dumps({"result": "ok"})},
                {"returncode": 0, "stdout": json.dumps({"result": "", "stop_reason": "refusal"})})
    return (_codex.CodexAdapter(), {"transport": "codex", "id": "gpt-x"},
            {"returncode": 0, "stdout": "ok", "cost_usd": 0},
            {"returncode": 0, "stdout": "ok"},
            {"returncode": 0, "stdout": "provider refused this call"})


def _c11(adapter, zero, absent):
    """R-FX-31-2: not measured is not a genuine zero, in both directions."""
    got_zero = adapter.cost(zero)
    got_absent = adapter.cost(absent)
    if got_zero is None or isinstance(got_zero, bool) or got_zero != 0.0:
        return FAIL, "a reported zero cost read as %r, not 0.0" % (got_zero,)
    if got_absent is not A.NOT_MEASURED:
        return FAIL, "an absent cost read as %r, not NOT_MEASURED" % (got_absent,)
    return PASS, "absent is NOT_MEASURED and a reported zero is 0.0"


def _c18(adapter, row, refused, absent):
    """R-FX-31-3: a provider refusal is its own status, never an answer and never a plain failure."""
    verdict = adapter.judge("m", row, refused)
    if verdict.status != A.STATUS_PROVIDER_REFUSED or verdict.ok:
        return FAIL, "a provider refusal was judged %s ok=%s" % (verdict.status, verdict.ok)
    answered = adapter.judge("m", row, absent)
    if answered.status == A.STATUS_PROVIDER_REFUSED:
        return FAIL, "an ordinary answer was judged a provider refusal"
    return PASS, "a provider refusal is PROVIDER_REFUSED and not ok"


def run_conformance(transports, offline=False):
    """{"offline", "ok", "transports": {name: {"C11", "C18", "detail", "adapter_sha256"}}} for the requested transports.

    transports is a non empty tuple of distinct names from adapters.TRANSPORTS, else ValueError. offline must be a
    bool: with False nothing runs and every case is NO-DATA (never PASS). ok is True only when every case PASSED."""
    if not isinstance(transports, tuple) or not transports:
        raise ValueError("transports must be a non empty tuple of transport names")
    if not all(isinstance(t, str) for t in transports):
        raise ValueError("every transport must be a string")
    if any(t not in A.TRANSPORTS for t in transports) or len(set(transports)) != len(transports):
        raise ValueError("transports must be distinct names from %s" % ", ".join(A.TRANSPORTS))
    if not isinstance(offline, bool):
        raise ValueError("offline must be a bool")
    out = {}
    for name in transports:
        entry = {"adapter_sha256": adapter_sha256(name), "detail": {}}
        if not offline:
            for case in CASES:
                entry[case] = NO_DATA
                entry["detail"][case] = "not run: conformance runs offline only"
        else:
            adapter, row, zero, absent, refused = _fixtures(name)
            entry["C11"], entry["detail"]["C11"] = _c11(adapter, zero, absent)
            entry["C18"], entry["detail"]["C18"] = _c18(adapter, row, refused, absent)
        out[name] = entry
    ok = all(out[t][c] == PASS for t in out for c in CASES)
    return {"offline": offline, "ok": ok, "transports": out}


def make_receipt_entry(model, registry, binary_path, conformance):
    """The receipt entry for one model: its adapter, executable and registry row hashes plus the case verdicts.
    ValueError when the model is not in the registry, the report is not a conformance report, or a hash cannot be made."""
    if not isinstance(model, str) or not isinstance(registry, dict) or not isinstance(conformance, dict):
        raise ValueError("make_receipt_entry wants a model name, a registry and a conformance report")
    row = registry.get(model)
    if not isinstance(row, dict):
        raise ValueError("%.60r is not in the model registry" % (model,))
    transport = row.get("transport")
    report = conformance.get("transports")
    seen = report.get(transport) if isinstance(report, dict) and isinstance(transport, str) else None
    if not isinstance(seen, dict):
        raise ValueError("the conformance report has no result for transport %.60r" % (transport,))
    entry = {"model": model, "transport": transport, "adapter_sha256": adapter_sha256(transport),
             "binary_path": binary_path, "binary_sha256": file_sha256(binary_path), "row_sha256": row_sha256(row)}
    for case in CASES:
        entry[case] = seen.get(case)
    return entry


def main(argv=None):
    """THE ENTRY POINT THE DONE CHECK NAMES, AND IT CAN FAIL (measured 2026-10-05: this file had none, so the unit done
    check's `adapter_conformance.py --transport bridge,claude,codex --offline` imported it and exited 0 with every C11
    forced to FAIL). One line per case, then one verdict line. Exit 0 only when every requested case PASSED; 1 when a
    case FAILED; 2 when nothing proved anything: no --offline (every case NO-DATA, never a pass) or a request
    run_conformance refuses. None reads sys.argv; an argv that is not a list of str refuses with SystemExit(2)."""
    if argv is not None and (not isinstance(argv, list) or not all(isinstance(a, str) for a in argv)):
        raise SystemExit(2)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--transport", required=True, help="comma separated names from %s" % ", ".join(A.TRANSPORTS))
    ap.add_argument("--offline", action="store_true",
                    help="run the cases (no process, no network); without it nothing runs and every case reads NO-DATA")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    names = tuple(n.strip() for n in args.transport.split(",") if n.strip())
    # THE VERDICT LINE STARTS WITH A SHAPE THE CLOSER KNOWS (review 2026-10-06): scripts/close_unit.py verdict() quotes
    # only lines starting PASS:, FAIL:, NO-DATA: and the unittest and selftest shapes. This line started "conformance:",
    # so when this command alone failed, the closure message quoted the four commands that had passed before it. The
    # failed cases are named on the line itself: it is the one line of this command the closer keeps.
    try:
        report = run_conformance(names, offline=args.offline)
    except ValueError as exc:
        print("NO-DATA: conformance: NO-DATA, nothing ran: %s" % exc)
        return 2
    verdicts, failed = [], []
    for name in names:
        for case in CASES:
            verdicts.append(report["transports"][name][case])
            if verdicts[-1] == FAIL:
                failed.append("%s %s" % (name, case))
            print("%-7s %s %s: %s" % (verdicts[-1], name, case, report["transports"][name]["detail"][case]))
    word = "OK" if all(v == PASS for v in verdicts) else ("FAILED" if failed else "NO-DATA")
    prefix = {"OK": "PASS", "FAILED": "FAIL"}.get(word, "NO-DATA")
    print("%s: conformance: %d cases over %s, %s%s" % (prefix, len(verdicts), ",".join(names), word,
                                                     (": " + ", ".join(failed)) if failed else ""))
    return {"OK": 0, "FAILED": 1}.get(word, 2)


if __name__ == "__main__":
    sys.exit(main())

"""JW1: second opinions you can read, the Jev readout.

One read only report over the second opinion ledger (decisions.jsonl and
outcomes.jsonl under jev_seam.DEFAULT_LEDGER_DIR): per check, how many
decisions were recorded, how many sat in the abstain band, how many carry an
outcome, how often the answer agreed with Brother's own deterministic rule,
and the Wilson lower bound of that agreement against the risk class target.

Agreement here is agreement with Brother's own rule, not independent truth.
Every number comes from jev_calibration (join and report); this module has no
formula of its own. Every line ends "recorded, not acted on": the readout
states a measurement and changes nothing.

A missing, empty or unreadable ledger prints one NO-DATA line and exits 2,
never a row of zeros.

Footprint: reads the ledger files once, writes nothing, starts no process,
makes no network call and asks no model anything.

Python 3, standard library only.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import coe_outside_gate
import jev_calibration
import jev_cascade
import jev_decide
import jev_registry
import jev_seam

NOT_ACTED = "recorded, not acted on"
NO_DATA_EXIT = 2

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_UNREADABLE_ID = "(unreadable id)"
_LEDGER_STEMS = ("decisions", "outcomes")
#: Terminal attempt phases the breaker line counts, in print order.
_BREAKER_PHASES = ("answered", "abstain", "no_data", "breaker_open",
                   "budget_exhausted", "inflight_drop")


def safe_id(text: object) -> str:
    """The text itself when it is a short plain id, else a fixed marker, so
    a hand edited ledger value can never add or alter a readout line."""
    if isinstance(text, str) and _SAFE_ID_RE.fullmatch(text):
        return text
    return _UNREADABLE_ID


def _ledger_state(ledger_dir: str) -> str:
    """"missing" (no folder, or a folder holding no ledger file at all),
    "unreadable" (the folder cannot be listed) or "present"."""
    if not os.path.isdir(ledger_dir):
        return "missing"
    try:
        names = os.listdir(ledger_dir)
    except OSError:
        return "unreadable"
    if not any(n.startswith(_LEDGER_STEMS) and n.endswith(".jsonl") for n in names):
        return "missing"
    return "present"


def _paths(ledger_dir: str) -> Tuple[str, str]:
    return (os.path.join(ledger_dir, "decisions.jsonl"),
            os.path.join(ledger_dir, "outcomes.jsonl"))


def _blank() -> Dict[str, object]:
    return {"decisions": 0, "abstains": 0, "outcomes": 0, "correct": 0,
            "_framings": set(), "_models": set()}


def read_points(ledger_dir: str) -> Optional[Dict[Tuple[str, str], Dict[str, int]]]:
    """Counts per (family, qtype), or None when the ledger is missing or
    could not be fully read, or {} when it holds no decision."""
    if _ledger_state(ledger_dir) != "present":
        return None
    dec_path, out_path = _paths(ledger_dir)
    j = jev_calibration.join(dec_path, out_path)
    if not j.get("reliable"):
        return None
    points = {}  # type: Dict[Tuple[str, str], Dict[str, object]]

    def slot(family, qtype):
        return points.setdefault((family, qtype), _blank())

    def seen(dec):
        p = slot(dec["family"], dec["qtype"])
        p["decisions"] += 1
        if jev_seam.ABSTAIN_LOW <= dec["prob"] <= jev_seam.ABSTAIN_HIGH:
            p["abstains"] += 1
        p["_framings"].add(dec["framing"])
        p["_models"].add(dec["model"])
        return p

    for item in j["joined"]:
        p = seen(item["decision"])
        p["outcomes"] += 1
        if item["outcome"]["correct"] is True:
            p["correct"] += 1
    for dec in j["unmatched_decisions"]:
        seen(dec)
    for item in list(j["ambiguous_outcomes"]) + list(j["duplicate_outcomes"]):
        slot(item["family"], item["qtype"])["decisions"] += 1

    out = {}
    for key, p in points.items():
        counts = {k: v for k, v in p.items() if not k.startswith("_")}
        counts["framings"] = len(p["_framings"])
        counts["models"] = len(p["_models"])
        out[key] = counts
    return out


def bound_for(ledger_dir: str, family: str, qtype: str,
              counts: Dict[str, int]) -> Tuple[Optional[float], str]:
    """(wilson_lower_95, "") from jev_calibration.report over one band, or
    (None, "NO-DATA, <why>")."""
    if counts.get("outcomes", 0) < 1:
        return None, "NO-DATA, no outcome recorded yet"
    if counts.get("framings", 0) > 1 or counts.get("models", 0) > 1:
        return None, "NO-DATA, mixed framings or models"
    dec_path, out_path = _paths(ledger_dir)
    try:
        rep = jev_calibration.report(dec_path, out_path, family, qtype, bands=(0.0,))
    except ValueError:
        return None, "NO-DATA, ledger anomaly"
    if not rep.get("ledger_reliable") or rep.get("family_qtype_anomaly"):
        return None, "NO-DATA, ledger anomaly"
    bound = rep["rows"][0]["wilson_lower_95"]
    if bound is None:
        return None, "NO-DATA, no outcome recorded yet"
    return bound, ""


def _num(value: Optional[float], fmt: str) -> str:
    return "NO-DATA" if value is None else fmt % value


def point_line(entry_id: str, qtype: str, mode: str, counts: Dict[str, int],
               bound: Optional[float], bound_note: str, target: Optional[float],
               risk: Optional[str]) -> str:
    d, a = counts.get("decisions", 0), counts.get("abstains", 0)
    o, k = counts.get("outcomes", 0), counts.get("correct", 0)
    agreement = (k / o) if o else None
    quarantined = qtype in jev_calibration.QUARANTINED_QTYPES
    flags = ""
    if bound is not None and target is not None:
        if bound >= target and o >= jev_cascade.MIN_SAMPLES and not quarantined:
            flags += " bound meets target"
        else:
            flags += " bound below target"
    if quarantined:
        flags += " type quarantined, not countable"
    if risk == "critical":
        flags += " critical never acts"
    if bound is None and bound_note:
        flags += " " + bound_note
    return ("%s %s [%s]: decisions %d, abstains %d, outcomes %d, agreement %d/%d = %s, "
            "Wilson lower bound %s against target %s (%s)%s; %s" % (
                safe_id(entry_id), safe_id(qtype), safe_id(mode), d, a, o, k, o,
                _num(agreement, "%.4f"), _num(bound, "%.4f"), _num(target, "%.2f"),
                risk or "unrecognized", flags, NOT_ACTED))


def _risks(registry_path: str) -> Dict[str, str]:
    """id -> risk class for every entry whose risk is a known class; {} when
    the registry fails to load (every check then reads unrecognized)."""
    try:
        entries = jev_registry.load(registry_path)
    except jev_registry.RegistryError:
        return {}
    return {e["id"]: e["risk"] for e in entries
            if e.get("risk") in jev_cascade.RISK_TARGET_PRECISION}


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    if home and home != os.sep and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home):]
    return path


def prerequisites(registry_path: str, seams_path: str, ledger_dir: str,
                  env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str]]:
    """One (name, "OK" | "MISSING", detail) row per condition without which a
    check cannot be asked or measured. Never raises; unreadable is MISSING."""
    env_map = os.environ if env is None else env
    rows = []
    try:
        jev_registry.load(registry_path)
        rows.append(("registry", "OK", _short(registry_path)))
    except jev_registry.RegistryError as exc:
        rows.append(("registry", "MISSING", type(exc).__name__))

    seams = jev_seam.load_seams_config(seams_path)
    modes = seams.get("modes") if isinstance(seams, dict) else None
    on = [k for k in (modes if isinstance(modes, dict) else {})
          if jev_seam.resolve_mode(seams, k) != jev_seam.OFF]
    if not os.path.isfile(seams_path):
        rows.append(("seams", "MISSING", "no seams file at %s" % _short(seams_path)))
    elif not on:
        rows.append(("seams", "MISSING", "no seam switched on in %s" % _short(seams_path)))
    else:
        rows.append(("seams", "OK", "%d seam(s) on" % len(on)))

    refused = jev_decide.bridge_refused(env_map)
    if refused:
        rows.append(("bridge", "MISSING", refused))
    elif env_map.get("BROTHER_DECISION_BRIDGE"):
        rows.append(("bridge", "OK", "BROTHER_DECISION_BRIDGE is set"))
    elif os.path.isfile(jev_decide.DEFAULT_BRIDGE_PATH):
        rows.append(("bridge", "OK", _short(jev_decide.DEFAULT_BRIDGE_PATH)))
    else:
        rows.append(("bridge", "MISSING", "no bridge at %s and BROTHER_DECISION_BRIDGE unset"
                     % _short(jev_decide.DEFAULT_BRIDGE_PATH)))

    terms = coe_outside_gate.DEFAULT_TERMS_PATH
    if os.path.isfile(terms):
        rows.append(("terms", "OK", _short(terms)))
    else:
        rows.append(("terms", "MISSING", "no terms file at %s, the content gate refuses every call"
                     % _short(terms)))

    parent = os.path.dirname(os.path.abspath(ledger_dir))
    if os.path.isdir(ledger_dir):
        rows.append(("ledger", "OK", _short(ledger_dir)))
    elif os.path.isdir(parent) and os.access(parent, os.W_OK):
        rows.append(("ledger", "OK", "%s not created yet, its parent is writable" % _short(ledger_dir)))
    else:
        rows.append(("ledger", "MISSING", "%s cannot be created" % _short(ledger_dir)))
    return rows


def budget_line(seams_config: dict, budget_path: Optional[str] = None,
                today: Optional[str] = None) -> str:
    """Today's call count from the budget file the seam writes, or NO-DATA."""
    path = budget_path or jev_seam._budget_path(seams_config)
    limit = jev_seam._daily_budget(seams_config)
    today_d = jev_seam._parse_iso_calendar_date(
        today or datetime.now(timezone.utc).date().isoformat())
    status, payload = jev_seam._read_budget_state(path)
    if status == "missing":
        return "budget: NO-DATA (no budget file at %s)" % _short(path)
    if status != "ok":
        return "budget: NO-DATA (budget file %s unreadable or not an object)" % _short(path)
    stored = jev_seam._parse_iso_calendar_date(payload.get("date"))
    calls = payload.get("calls")
    if stored is None or today_d is None:
        return "budget: NO-DATA (budget file %s has no valid date)" % _short(path)
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
        return "budget: NO-DATA (budget file %s has no valid call count)" % _short(path)
    if stored > today_d:
        return "budget: NO-DATA (budget file %s is dated %s, later than today)" % (
            _short(path), stored.isoformat())
    if stored < today_d:
        return "budget today: 0 of %d calls (no call recorded today)" % limit
    return "budget today: %d of %d calls (%s)" % (calls, limit, stored.isoformat())


def breaker_line(attempts_path: str, tail_bytes: int = 1048576) -> str:
    """Recent terminal attempt phases counted from the tail of attempts.jsonl.
    The breaker itself lives in the memory of the running process
    (jev_seam._BREAKER_STATE), so only its recorded refusals can be read."""
    try:
        size = os.path.getsize(attempts_path)
        with open(attempts_path, "rb") as fh:
            start = max(0, size - tail_bytes - 1)
            fh.seek(start)
            data = fh.read()
    except FileNotFoundError:
        return "attempts: NO-DATA (no attempts ledger)"
    except OSError:
        return "attempts: NO-DATA (attempts ledger unreadable)"
    if start > 0:
        # The byte before the tail tells whether the tail starts mid line.
        cut = data.find(b"\n")
        data = data[cut + 1:] if cut >= 0 else b""
    counts = dict.fromkeys(_BREAKER_PHASES, 0)
    for raw in data.split(b"\n"):
        try:
            row = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue  # a torn or foreign line is skipped, never guessed
        if isinstance(row, dict) and row.get("phase") in counts:
            counts[row["phase"]] += 1
    return ("attempts (recent): %s; the breaker itself is held in the running process"
            % ", ".join("%s %d" % (p, counts[p]) for p in _BREAKER_PHASES))


def _doctor(seams: dict, seams_path: str, registry_path: str, ledger_dir: str) -> List[dict]:
    rows = []
    for name, state, detail in prerequisites(registry_path, seams_path, ledger_dir):
        rows.append({"kind": "prerequisite", "name": name, "state": state, "detail": detail,
                     "line": "prerequisite %s: %s (%s); %s" % (name, state, detail, NOT_ACTED)})
    rows.append({"kind": "budget", "line": "%s; %s" % (budget_line(seams), NOT_ACTED)})
    attempts = os.path.join(ledger_dir, "attempts.jsonl")
    rows.append({"kind": "attempts", "line": "%s; %s" % (breaker_line(attempts), NOT_ACTED)})
    return rows


def _readout(ledger_dir: str, seams: dict, registry_path: str,
             first_missing: str = "") -> Tuple[int, List[dict]]:
    state = _ledger_state(ledger_dir)
    points = read_points(ledger_dir)
    if not points:
        if state == "missing":
            cause = " (first missing prerequisite: %s)" % first_missing if first_missing else ""
            line = "NO-DATA: no second opinion ledger at %s%s; %s" % (ledger_dir, cause, NOT_ACTED)
        else:
            why = ("could not be fully read; counts withheld" if points is None
                   else "holds no decision yet")
            line = "NO-DATA: second opinion ledger at %s %s; %s" % (ledger_dir, why, NOT_ACTED)
        return NO_DATA_EXIT, [{"kind": "no_data", "ledger_dir": ledger_dir, "line": line}]

    risks = _risks(registry_path)
    rows = []
    for family, qtype in sorted(points):
        counts = points[(family, qtype)]
        mode = jev_seam.resolve_mode(seams, family)
        risk = risks.get(family) if isinstance(family, str) else None
        target = jev_cascade.RISK_TARGET_PRECISION.get(risk) if risk else None
        bound, note = bound_for(ledger_dir, family, qtype, counts)
        rows.append({"kind": "point", "id": safe_id(family), "qtype": safe_id(qtype),
                     "mode": mode, "counts": counts, "wilson_lower_95": bound,
                     "bound_note": note, "target": target, "risk": risk or "unrecognized",
                     "line": point_line(family, qtype, mode, counts, bound, note, target, risk)})
    with_points = {f for f, _ in points}
    modes = seams.get("modes") if isinstance(seams, dict) else None
    for entry_id in sorted(k for k in (modes or {}) if isinstance(k, str)):
        mode = jev_seam.resolve_mode(seams, entry_id)
        if mode == jev_seam.OFF or entry_id in with_points:
            continue
        rows.append({"kind": "silent", "id": safe_id(entry_id), "mode": mode,
                     "line": "%s [%s]: NO-DATA, no decision recorded yet; %s" % (
                         safe_id(entry_id), mode, NOT_ACTED)})
    return 0, rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read the second opinion ledger.")
    parser.add_argument("--ledger-dir", default=jev_seam.DEFAULT_LEDGER_DIR)
    parser.add_argument("--seams-config", default=jev_seam.DEFAULT_SEAMS_CONFIG_PATH)
    parser.add_argument("--registry", default=jev_seam.DEFAULT_REGISTRY_PATH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    seams = jev_seam.load_seams_config(args.seams_config)
    doctor = _doctor(seams, args.seams_config, args.registry, args.ledger_dir)
    missing = [r["name"] for r in doctor if r.get("state") == "MISSING"]
    code, rows = _readout(args.ledger_dir, seams, args.registry, missing[0] if missing else "")
    rows += doctor
    if args.json:
        print(json.dumps(rows, sort_keys=True))
    else:
        for row in rows:
            print(row["line"])
    return code


if __name__ == "__main__":
    sys.exit(main())

"""L6b.1 contracts and parsing for the Jev catalogue."""
import re

MARKER_PREFIX = "Ledger:"
ALLOWED_DOMAINS = frozenset({
    "BrotherMode",
    "BrotherSBE",
    "BrotherDS",
    "Mobile",
    "Vault",
    "OpenRouter",
})
ALLOWED_VERDICTS = frozenset({"yes", "no", "abstain", "NO-DATA"})
QID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HOLDER_RE = re.compile(
    r"^jev-catalogue-\d{2}-[a-z0-9_]+(?:-[a-z0-9_]+)*(?:-\d+-\d+)?$"
)
RESERVATION_BODY = r"jev-catalogue-\d{2}-[a-z0-9_]+(?:-[a-z0-9_]+)*-\d+-\d+"
RESERVATION_RE = re.compile(r"^" + RESERVATION_BODY + r"$")
MARKER_RE = re.compile(
    re.escape(MARKER_PREFIX) + r"\s*`?(" + RESERVATION_BODY + r")`?"
)

def _require_text(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be str")
    return value

def _is_str(value):
    return isinstance(value, str)

def _valid_noul(value):
    if value == "NO-DATA":
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0.0 <= value <= 1.0
    return False

def validate_entry(entry):
    if not isinstance(entry, dict):
        return False
    required = (
        "nn",
        "qid",
        "domain",
        "state_cmd",
        "state_output_verbatim",
        "question",
        "true_criteria",
        "false_criteria",
        "noul_value",
        "holder_id",
        "reservation_id",
    )
    for field in required:
        if field not in entry:
            return False
    nn = entry["nn"]
    if isinstance(nn, bool) or not isinstance(nn, int):
        return False
    if not (11 <= nn <= 999):
        return False
    qid = entry["qid"]
    if not _is_str(qid) or not QID_RE.fullmatch(qid):
        return False
    domain = entry["domain"]
    if not _is_str(domain) or domain not in ALLOWED_DOMAINS:
        return False
    if not _is_str(entry["state_cmd"]) or not entry["state_cmd"]:
        return False
    if not _is_str(entry["state_output_verbatim"]) or not entry["state_output_verbatim"]:
        return False
    if not _is_str(entry["question"]) or not entry["question"]:
        return False
    if not _is_str(entry["true_criteria"]) or not entry["true_criteria"]:
        return False
    if not _is_str(entry["false_criteria"]) or not entry["false_criteria"]:
        return False
    if not _valid_noul(entry["noul_value"]):
        return False
    if not _is_str(entry["holder_id"]) or not HOLDER_RE.fullmatch(entry["holder_id"]):
        return False
    if not _is_str(entry["reservation_id"]) or not RESERVATION_RE.fullmatch(entry["reservation_id"]):
        return False
    if "verdict" in entry:
        verdict = entry["verdict"]
        if not _is_str(verdict) or verdict not in ALLOWED_VERDICTS:
            return False
    return True

def extract_reservation_id(entry_text):
    if not _is_str(entry_text):
        return ""
    match = re.search(RESERVATION_BODY, entry_text)
    if match:
        return match.group(0)
    return ""

def count_markers(catalogue_text):
    _require_text(catalogue_text, "catalogue_text")
    return len(MARKER_RE.findall(catalogue_text))

def recount_unique_ids(catalogue_text):
    _require_text(catalogue_text, "catalogue_text")
    ids = MARKER_RE.findall(catalogue_text)
    return len(set(ids))

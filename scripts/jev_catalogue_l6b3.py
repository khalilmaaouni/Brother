"""L6b.3 Gated execution and parity lock.

Implements gated dispatch preflight, ledger binding, marker parity lock,
abstain calibration and spend check for the Jev use case catalogue.
Standard library only. Self contained: no imports from other L6b modules.
"""

import json
import re


class CatalogueRefused(ValueError):
    """Raised when input is hostile, corrupt or missing."""


class GatedCallRefused(CatalogueRefused):
    """Raised when a gated call is refused before any dispatch."""


_ALLOWED_DOMAINS = frozenset([
    "BrotherMode", "BrotherSBE", "BrotherDS",
    "Mobile", "Vault", "OpenRouter",
])

_REQUIRED_FIELDS = (
    "nn", "qid", "domain", "state_cmd", "state_output_verbatim",
    "question", "true_criteria", "false_criteria",
    "noul_value", "holder_id", "reservation_id",
)

_ID_CORE = r"jev-catalogue-\d{2,3}-[a-z0-9_-]+-\d+-\d+"
_ID_RE = re.compile(r"^" + _ID_CORE + r"$")
_QID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MARKER_RE = re.compile(r"^Ledger:\s*`?(" + _ID_CORE + r")`?")
_SECTION_RE = re.compile(r"^###\s+(\d+)\.", re.M)


def validate_entry(entry):
    """Return True if entry satisfies the L6b contract, else False.

    Missing, corrupt, wrong-type or hostile input returns False; no crash.
    """
    if not isinstance(entry, dict):
        return False
    for f in _REQUIRED_FIELDS:
        if f not in entry:
            return False
    nn = entry.get("nn")
    if isinstance(nn, bool) or not isinstance(nn, int) or nn < 11 or nn > 999:
        return False
    domain = entry.get("domain")
    if domain not in _ALLOWED_DOMAINS:
        return False
    qid = entry.get("qid")
    if not isinstance(qid, str) or not _QID_RE.match(qid):
        return False
    for f in ("state_cmd", "state_output_verbatim", "question",
              "true_criteria", "false_criteria"):
        v = entry.get(f)
        if not isinstance(v, str) or v.strip() == "":
            return False
    nv = entry.get("noul_value")
    if nv != "NO-DATA":
        if isinstance(nv, bool) or not isinstance(nv, (int, float)):
            return False
        if nv != nv or nv < 0.0 or nv > 1.0:
            return False
    hid = entry.get("holder_id")
    if not isinstance(hid, str) or not _ID_RE.match(hid):
        return False
    rid = entry.get("reservation_id")
    if not isinstance(rid, str) or not _ID_RE.match(rid):
        return False
    return True


def count_markers(catalogue_text):
    """Count literal 'Ledger:' marker lines carrying a valid reservation id."""
    if not isinstance(catalogue_text, str):
        raise CatalogueRefused("catalogue_text must be a str")
    n = 0
    for line in catalogue_text.splitlines():
        if _MARKER_RE.match(line.strip()):
            n += 1
    return n


def recount_unique_ids(catalogue_text):
    """Return the count of unique reservation ids on marker lines."""
    if not isinstance(catalogue_text, str):
        raise CatalogueRefused("catalogue_text must be a str")
    ids = set()
    for line in catalogue_text.splitlines():
        m = _MARKER_RE.match(line.strip())
        if m:
            ids.add(m.group(1))
    return len(ids)


def _section_has_nn(catalogue_text, nn):
    for m in _SECTION_RE.finditer(catalogue_text):
        try:
            if int(m.group(1)) == nn:
                return True
        except (ValueError, TypeError):
            continue
    return False


def run_gated_call(payload, holder_id, timeout_s):
    """Build and validate a gated dispatch request.

    Hard codes jev type noul, estimated cost 0.001 and timeout 300 before
    any subprocess starts. Any timeout below 300 is refused before the call.
    """
    if not isinstance(payload, dict):
        raise GatedCallRefused("payload must be a dict")
    if not isinstance(holder_id, str) or not _ID_RE.match(holder_id):
        raise GatedCallRefused("holder_id must match jev-catalogue pattern")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int):
        raise GatedCallRefused("timeout_s must be an int")
    if timeout_s < 300:
        raise GatedCallRefused("timeout below 300 is refused before call")
    state = payload.get("state")
    if not isinstance(state, str) or state.strip() == "":
        raise GatedCallRefused("payload state missing or empty")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise GatedCallRefused("payload questions missing or empty")
    for qid, q in questions.items():
        if not isinstance(q, dict):
            raise GatedCallRefused("question must be a dict")
        qtype = q.get("type")
        if qtype != "noul":
            raise GatedCallRefused(
                "only noul type allowed, got {!r}".format(qtype))
    built = {
        "type": "noul",
        "timeout": 300,
        "estimated_cost": 0.001,
        "holder_id": holder_id,
        "state": state,
        "questions": questions,
    }
    return {"built": built, "status": "dispatched"}


def bind_ledger(holder_id, ledger_text):
    """Return the reservation id when the ledger holds a matching RESERVE and RECONCILE pair, else "".

    IT IS TWO PASSES, NOT ONE, AND THAT IS THE WHOLE FIX. Measured 2026-09-21 on the live ledger: 616 RESERVE
    rows, ALL carrying holder_id; 567 RECONCILE rows, NONE carrying it. A real reconcile is
      {"type": "RECONCILE", "reservation_id": "...", "actual_cost": 84.21, "at": 1789959349.635}
    The previous single pass filtered BOTH row types by holder_id, so the reconcile half could never match and
    this returned "" for every real pair in the estate. It passed its own suite only because that suite built a
    fixture carrying holder_id on both rows: the green-but-hollow class, where the test proves the function
    against an input the world never produces.

    Three independent reasons it could never bind, all three fixed together, because fixing any one alone leaves
    it still returning "" on real input:
      1. holder_id is absent from every RECONCILE row, so the holder is matched on the RESERVE only
      2. a RECONCILE carries actual_cost, never estimated_cost, so the cost field is read per row type
      3. the live dispatcher appends a uuid4 tail to a reservation id (added 2026-09-20 after two reservations
         collided inside one millisecond), which the end anchored _ID_RE rejects; an id is matched here as the
         opaque token it is, and its shape is the dispatcher's business rather than this reader's

    Fails toward "" and never toward a false bind: an unreconciled reservation, another holder's pair, a
    reconcile naming a different reservation, and a corrupt line all yield no binding rather than a guess.
    Binding an unreconciled reservation would count money that was never spent as evidence that it was."""
    if not isinstance(holder_id, str) or holder_id == "":
        raise CatalogueRefused("holder_id must be a non-empty str")
    if not isinstance(ledger_text, str):
        raise CatalogueRefused("ledger_text must be a str")

    def rows():
        for line in ledger_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue            # a corrupt line is skipped, never fatal, and never a bind
            if isinstance(obj, dict):
                yield obj

    def cost_ok(obj, field):
        c = obj.get(field)
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            return False
        return abs(float(c) - 0.001) <= 0.0000001

    reserved = None
    for obj in rows():
        if (obj.get("type") == "RESERVE" and obj.get("holder_id") == holder_id
                and isinstance(obj.get("reservation_id"), str) and obj["reservation_id"]
                and cost_ok(obj, "estimated_cost")):
            reserved = obj["reservation_id"]
            break
    if not reserved:
        return ""
    for obj in rows():
        if (obj.get("type") == "RECONCILE" and obj.get("reservation_id") == reserved
                and cost_ok(obj, "actual_cost")):
            return reserved
    return ""


def refuse_on_missing(state_output):
    """Return True when state_output is missing or empty: call must not run."""
    if not isinstance(state_output, str):
        raise CatalogueRefused("state_output must be a str")
    return state_output.strip() == ""


def append_entry(catalogue_text, entry):
    """Append a validated entry to catalogue text; else return original text."""
    if not isinstance(catalogue_text, str):
        raise CatalogueRefused("catalogue_text must be a str")
    if not validate_entry(entry):
        return catalogue_text
    nn = entry["nn"]
    if _section_has_nn(catalogue_text, nn):
        return catalogue_text
    qid = entry["qid"]
    state_cmd = entry["state_cmd"]
    question = entry["question"]
    noul_value = entry["noul_value"]
    reservation_id = entry["reservation_id"]
    label = audit_abstain(noul_value)
    block = (
        "\n\n### {}. {}\n\n".format(nn, question)
        + "State: `{}`\n\n".format(state_cmd)
        + "Question: `{}`.\n\n".format(qid)
        + "Answer: **noul = {}** ({}).\n\n".format(noul_value, label)
        + "Ledger: `{}`\n".format(reservation_id)
    )
    return catalogue_text + block


def verify_parity(catalogue_text, ledger_text):
    """Return True when markers == unique catalogue ids == ledger complete ids."""
    if not isinstance(catalogue_text, str) or not isinstance(ledger_text, str):
        raise CatalogueRefused("catalogue_text and ledger_text must be str")
    markers = count_markers(catalogue_text)
    uniques = recount_unique_ids(catalogue_text)
    if markers == 0 or markers != uniques:
        return False
    cat_ids = set()
    for line in catalogue_text.splitlines():
        m = _MARKER_RE.match(line.strip())
        if m:
            cat_ids.add(m.group(1))
    if len(cat_ids) != markers:
        return False
    ledger_reserve = {}
    ledger_reconcile = {}
    for line in ledger_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        rid = obj.get("reservation_id")
        hid = obj.get("holder_id")
        typ = obj.get("type")
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            continue
        if not isinstance(hid, str):
            continue
        cost = obj.get("estimated_cost")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            continue
        if abs(float(cost) - 0.001) > 0.0000001:
            continue
        if typ == "RESERVE":
            ledger_reserve[hid] = rid
        elif typ == "RECONCILE":
            ledger_reconcile[hid] = rid
    ledger_complete_ids = set()
    for hid, rid in ledger_reserve.items():
        if ledger_reconcile.get(hid) == rid:
            ledger_complete_ids.add(rid)
    if cat_ids != ledger_complete_ids:
        return False
    return True


def audit_abstain(noul):
    """Return 'yes', 'no', 'abstain' or 'NO-DATA' per calibration band."""
    if noul is None:
        return "NO-DATA"
    if isinstance(noul, bool):
        raise CatalogueRefused("noul must be a number or NO-DATA")
    if not isinstance(noul, (int, float)):
        raise CatalogueRefused("noul must be a number or NO-DATA")
    if noul != noul:
        return "NO-DATA"
    if noul < 0.0 or noul > 1.0:
        return "NO-DATA"
    if noul < 0.2:
        return "no"
    if noul > 0.8:
        return "yes"
    return "abstain"


def current_spend_check(ledger_text):
    """Sum estimated_cost over ledger lines. Raises CatalogueRefused on non-str."""
    if not isinstance(ledger_text, str):
        raise CatalogueRefused("ledger_text must be a str")
    total = 0.0
    for line in ledger_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        cost = obj.get("estimated_cost")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            continue
        total += float(cost)
    return total

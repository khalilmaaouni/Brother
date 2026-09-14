#!/usr/bin/env python3
"""bm_vault_context.py: RequestContext and tenancy for the served vault (WBS row VB3-03).

Enterprise mode cannot construct a recall without tenant and principal context, and every
audit event needs an immutable request id. Today the estate is single-tenant by
construction: bm_vault.py resolves exactly one index, answer ledger and access audit file,
all hardcoded under ~/.claude, no matter who asks or what BM_VAULT_ROOT names at recall
time (confirmed by reading tools/bm_vault.py's _search: it queries that one global SQLite
index directly, never filtered to whatever vault root the caller passed). The served
endpoint (bm_vault_serve.py) is where the context boundary enters, so this module owns
exactly the two small things that boundary needs: minting the immutable request id, and
resolving a tenant string into a subprocess environment that is ACTUALLY isolated, not
merely pointed at a different content directory.

SEAM CHOSEN: full "two vault roots" isolation via HOME, not a per-note tenant column. A
per-note tenant field would work too, but it means touching bm_vault.py's schema,
_upsert_note and _search -- a bigger, riskier diff than one WBS row justifies, and it still
has to solve the exact same problem (the index, ledger and audit paths are computed from
os.path.expanduser("~"), not from BM_VAULT_ROOT). Reusing that existing home-relative
plumbing is the smaller, honest seam: point a tenant's subprocess HOME at a private
directory and its BM_VAULT_ROOT at that same directory's own "vault" subfolder, and
bm_vault.py's every hardcoded ~/.claude path (the index, the answer ledger, the access
audit, the installer config) resolves inside it for free, with zero changes to bm_vault.py
itself. Convention, PRE-PROVISIONED by whoever operates the estate (this module only reads
it, it never creates a directory on a caller's say-so -- an unprovisioned tenant name must
refuse, never silently spin up empty state for it):

  <tenants-root>/<tenant>/vault      the tenant's own BM_VAULT_ROOT
  <tenants-root>/<tenant>/.claude    the tenant's own index, ledger and access audit,
                                     already `bm_vault.py index`-ed

No id or path this module builds ever embeds the tenant string as an identifier that
leaves this process; it is used only to select a directory, the same way any other config
path already is.

REQUEST ENVELOPE: this module also owns the provider-neutral object describing who is
asking, what they are asking, and what kind of answer it is, minted before any retrieval
or routing happens (build_request_envelope). Its classifier (classify_answer_class) is a
plain keyword table, never a model call, because the one class that matters most --
TRANSACTIONAL_ACTION, a request that could change a system record -- has to be
inspectable and testable with no network access, and has to fail toward the safe,
non-mutating side whenever a verb's intent is ambiguous ("fix the hierarchy" reads as
analysis, not a write). allowed_actions defaults to ["read"] always; nothing in the
question text, however it classifies, ever widens it on its own. Only an explicit,
separately-supplied action-contract argument can grant more, because a conversational
answer must never silently become a write.

No em or en dashes anywhere in this file.
"""
import datetime
import os
import re
import uuid

#: Safe as a single directory component and nothing else: no '.', '/', or whitespace, so a
#: tenant string can never walk out of tenants-root or address a second segment.
#: Deliberately narrower than a general-purpose identifier for exactly that reason.
TENANT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def new_request_id():
    """A fresh, immutable request id: hex uuid4, minted exactly once per served request,
    server-side. Carries no tenant, principal or query fragment -- safe to log, return to
    the caller, and feed back into bm_vault.py as --event-id (VB6-03's existing per-answer
    id, reused rather than duplicated) without leaking anything it names."""
    return uuid.uuid4().hex


def missing_enterprise_fields(tenant, principal):
    """The required-but-absent field names ("tenant", "principal", or both, in that order)
    for one request under enterprise mode. Empty list means the request may proceed. Either
    value must be a non-empty, non-whitespace string; anything else (missing, None, "",
    "   ", a non-string) counts as absent -- never a guess at what the caller meant."""
    missing = []
    if not (isinstance(tenant, str) and tenant.strip()):
        missing.append("tenant")
    if not (isinstance(principal, str) and principal.strip()):
        missing.append("principal")
    return missing


def tenant_env(tenants_root, tenant):
    """(env overrides dict, error) for one tenant, to merge into a subprocess environment
    that already isolates that tenant's index, ledger and access audit (see the module
    docstring). Returns (None, "reason") -- never a guess, never a silent fallback to the
    shared, unscoped environment -- when: no tenants_root is configured; tenant is not a
    clean single-segment name; or the tenant is not already provisioned (both its vault and
    its .claude state directory must already exist on disk)."""
    if not tenants_root:
        return None, ("enterprise mode has no --tenants-root/BM_VAULT_TENANTS_ROOT "
                      "configured; a tenant cannot be resolved to anywhere")
    if not isinstance(tenant, str) or not TENANT_RE.match(tenant):
        return None, ("tenant %r is not a safe identifier (letters, digits, - and _ "
                      "only)" % (tenant,))
    home = os.path.join(tenants_root, tenant)
    vault = os.path.join(home, "vault")
    state = os.path.join(home, ".claude")
    if not os.path.isdir(vault) or not os.path.isdir(state):
        return None, ("tenant %r is not provisioned: expected both %s and %s to already "
                      "exist" % (tenant, vault, state))
    return {"HOME": home, "BM_VAULT_ROOT": vault}, None


#: The Request Envelope's schema tag. Bump this (v2, v3, ...) on any field rename or
#: removal a consumer could depend on; adding an optional field does not need a bump.
REQUEST_ENVELOPE_SCHEMA = "brother-request-v1"

#: The complete set classify_answer_class ever returns. Kept here, not just as literal
#: strings scattered through the function, so a caller can validate against it.
ANSWER_CLASSES = frozenset([
    "OFFICIAL_METRIC", "MASTER_LOOKUP", "STANDARD_POLICY", "OPERATIONAL_GUIDANCE",
    "EXPLORATORY_ANALYSIS", "ENGINEERING", "TRANSACTIONAL_ACTION",
])

#: Verbs whose ordinary meaning is "cause a write", never "explain" or "diagnose". A
#: verb NOT on this list ("fix", "resolve", "sort out", "handle") can mean either an
#: explanation or a write, so it never reaches TRANSACTIONAL_ACTION on its own: see
#: classify_answer_class's bias-to-safe rule.
_MUTATING_VERBS = (
    "update", "updates", "updated", "updating",
    "change", "changes", "changed", "changing",
    "delete", "deletes", "deleted", "deleting",
    "remove", "removes", "removed", "removing",
    "publish", "publishes", "published", "publishing",
    "merge", "merges", "merged", "merging",
    "write", "writes", "wrote", "writing",
    "insert", "inserts", "inserted", "inserting",
    "overwrite", "overwrites", "overwrote", "overwriting",
)

#: A named system-of-record noun. A mutating verb alone is not enough to call a
#: question TRANSACTIONAL_ACTION; it also has to be aimed at something like this, not a
#: vague noun like "hierarchy" or "process" that a mutating-sounding verb could still be
#: used against in a purely explanatory sense.
_RECORD_TARGET_NOUNS = (
    "record", "field", "row", "entry", "table", "database",
    "account", "ticket", "config", "configuration", "master data",
)

#: Named systems recognized on sight: naming one plus a mutating verb is already an
#: unambiguous system-of-record target, even with no separate noun from the list above.
_NAMED_SYSTEMS = ("sap", "crm", "erp", "salesforce", "workday", "sharepoint")

#: Golden-record, mastering and system-of-record vocabulary: a lookup against the one
#: authoritative copy of a piece of reference data, distinct from an official metric
#: (a number) or a policy (a rule).
_MASTER_LOOKUP_KEYWORDS = (
    "golden record", "golden account", "master data", "mastering",
    "system of record", "authoritative source", "master record",
)

#: Phrasing that asks directly for a value, as opposed to asking why a value moved.
_VALUE_ASK_PATTERNS = (
    "what is the", "what's the", "how much is", "how much was",
    "current value of", "latest value of", "value of",
)

#: Metric and KPI nouns an OFFICIAL_METRIC question names.
_METRIC_NOUNS = (
    "revenue", "sales", "volume", "margin", "ebitda", "headcount",
    "price", "growth rate", "kpi", "total", "count",
)

#: A trend or diagnostic word turns a value-shaped question into analysis, not a
#: certified-metric lookup: "why is volume declining" wants a cause, not a number.
_TREND_WORDS = (
    "why", "declining", "decline", "increasing", "increase", "trend",
    "compared to", "root cause", "driving",
)

#: Standing-rule and process vocabulary: "what should we do" rather than "what is the
#: number" or "what is the master value".
_POLICY_KEYWORDS = (
    "policy", "policies", "sop", "standard operating procedure",
    "what is the process for", "what is our policy",
)

#: How-to and runbook vocabulary: a question about carrying out a known procedure.
_OPERATIONAL_KEYWORDS = (
    "how do i", "how do we", "how to", "steps to", "runbook", "troubleshoot",
)

#: Code, schema and system-internals vocabulary.
_ENGINEERING_KEYWORDS = (
    "function", "endpoint", "schema", "migration", "deploy", "stack trace",
    "exception", "api", "database schema", "query plan",
)


def _contains_any(lower_text, phrases):
    return any(phrase in lower_text for phrase in phrases)


def _has_mutating_verb(lower_text):
    return any(re.search(r"\b%s\b" % re.escape(verb), lower_text)
               for verb in _MUTATING_VERBS)


def classify_answer_class(question_text):
    """One of ANSWER_CLASSES for question_text, decided by a plain keyword table --
    never a model call, so this stays inspectable and testable with no network access.

    TRANSACTIONAL_ACTION is the one class that authorizes nothing on its own (see
    build_request_envelope), so it is also the one class this function is strictest
    about: it fires only for an unambiguous mutating verb (_MUTATING_VERBS) aimed at a
    named system or record (_RECORD_TARGET_NOUNS or _NAMED_SYSTEMS). A verb that could
    just as easily mean "explain" or "diagnose" (fix, resolve, sort out, handle) is
    never on that list, so a genuinely ambiguous question like "fix the customer's
    hierarchy" falls through to a non-mutating class instead. A model or keyword match
    is never the sole authority marking something transactional when intent is
    ambiguous: this function is that authority, and it is built to refuse when unsure.

    Anything that is not a query text at all (None, "", whitespace) classifies as
    EXPLORATORY_ANALYSIS, the safest non-mutating default, rather than raising.
    """
    text = question_text if isinstance(question_text, str) else ""
    lower = text.lower()

    if _has_mutating_verb(lower) and (
        _contains_any(lower, _RECORD_TARGET_NOUNS)
        or _contains_any(lower, _NAMED_SYSTEMS)
    ):
        return "TRANSACTIONAL_ACTION"

    if _contains_any(lower, _MASTER_LOOKUP_KEYWORDS):
        return "MASTER_LOOKUP"

    if (_contains_any(lower, _VALUE_ASK_PATTERNS)
            and _contains_any(lower, _METRIC_NOUNS)
            and not _contains_any(lower, _TREND_WORDS)):
        return "OFFICIAL_METRIC"

    if _contains_any(lower, _POLICY_KEYWORDS):
        return "STANDARD_POLICY"

    if _contains_any(lower, _OPERATIONAL_KEYWORDS):
        return "OPERATIONAL_GUIDANCE"

    if _contains_any(lower, _ENGINEERING_KEYWORDS):
        return "ENGINEERING"

    # Safe, non-mutating default: open-ended, "why", or otherwise unclassified
    # questions, and anything genuinely ambiguous that fell through every table above.
    return "EXPLORATORY_ANALYSIS"


def _now_iso8601():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_request_envelope(question_text, human=None, agent=None, purpose=None,
                            channel=None, locale=None, tenant=None, principal=None,
                            explicit_action_authorization=None, created_at=None):
    """(envelope dict, missing enterprise fields) for one request, minted before any
    retrieval or routing happens.

    missing is the direct return value of missing_enterprise_fields(tenant, principal):
    an empty list means both are present, a non-empty list names exactly which of
    "tenant"/"principal" is absent, using the one function that already owns that
    rule (bm_vault_serve.py's own enterprise-mode refusal calls the same function, so
    the two call sites can never disagree about what "missing" means). This module
    does not itself refuse the request over that; the served endpoint does. It is
    surfaced here so a caller building an envelope reads the same verdict.

    allowed_actions is ["read"] unless explicit_action_authorization is not None, in
    which case it becomes exactly list(explicit_action_authorization). Nothing about
    question_text, including an answer_class of TRANSACTIONAL_ACTION, ever changes
    allowed_actions on its own -- a conversational answer must never silently become a
    write. Widening it is a separate, explicit decision the caller states out loud.
    """
    missing = missing_enterprise_fields(tenant, principal)
    allowed_actions = (list(explicit_action_authorization)
                        if explicit_action_authorization is not None else ["read"])
    envelope = {
        "schema": REQUEST_ENVELOPE_SCHEMA,
        "request_id": new_request_id(),
        "created_at": created_at if created_at is not None else _now_iso8601(),
        "actor": {"human": human, "agent": agent, "purpose": purpose},
        "channel": channel,
        "locale": locale,
        "question": {"text": question_text},
        "allowed_actions": allowed_actions,
        "answer_class": classify_answer_class(question_text),
    }
    return envelope, missing

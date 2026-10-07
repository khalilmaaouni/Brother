"""context_capsule: WBS-10.02, the per-unit dispatch context this estate
never had (docs/plan/1.0.17/WBS-10-SCOUTING-2026-09-13.md: confirmed missing
by a targeted search, not just unsearched).

THE SEAM THIS WIDENS, not replaces: loop_bridge.py's run_node() builds a
plain unit/brief dict (unit_id, objective, done_check, write_scope, ...),
bm_worker_spawn.py's SpawningWorker.run() serializes it as JSON on the
child's stdin, and model_worker.py's build_prompt() reads it back with
.get() and a default, never indexed. A Context Capsule is that same dict,
widened with the fields the roadmap names, built by build_capsule() below
rather than assembled by hand at each call site.

THE TWO THINGS THE ROADMAP NAMES BY NAME AS FAILURE MODES, both closed here:

  - NO FULL CHAT HISTORY: nothing here reads a transcript. Every field is
    either drawn from the node dict already passed to run_node(), or handed
    in explicitly by the caller (dependency_outputs, decisions,
    vault_snippets). A capsule cannot leak a conversation it was never given.
  - NO AUTOMATIC WHOLE-REPO DUMP: relevant_files reads ONLY the paths the
    node itself declares (write_scope, read_scope). It never globs, never
    walks a directory, and a declared path that is a directory rather than a
    file is skipped rather than expanded.

BYTE-BOUNDED, not line- or file-count-bounded: DEFAULT_MAX_FILE_BYTES caps
any one file/output/snippet, DEFAULT_MAX_TOTAL_BYTES caps the whole bundle.
Going over budget trims the least authoritative material first (vault
context, then decisions, then dependency outputs, then files) in a fixed
order, so two runs over identical inputs trim identically.

DETERMINISTIC AND HASHABLE: capsule_hash is sha256 over the canonical
(sort_keys=True) JSON of every other field. Change one byte of one file's
content, one dependency output, or one decision, and the hash changes -- a
capsule's identity is a property of what went into it, never of when it was
built. A caller that wants to know whether a previously-built capsule is
still fresh rebuilds it and compares hashes, rather than trusting a
timestamp.

Python 3.9, standard library only. No network, no vault import:
vault_snippets arrives pre-retrieved from whatever narrow query the caller
already ran, and is stored here only ever as non-authoritative.
"""
import hashlib
import json
import os
import re
from datetime import date, datetime

#: Any one file, dependency output, or vault snippet is capped here before it
#: ever reaches the bundle. This is a hard cut, not a summary.
DEFAULT_MAX_FILE_BYTES = 4000

#: The whole bundle is capped here. Bigger than one file's cap because a unit
#: can legitimately declare several small files.
DEFAULT_MAX_TOTAL_BYTES = 32000

#: Trim order when the assembled bundle is over budget, LEAST authoritative
#: first. vault_context is already explicitly non-authoritative; it is also
#: the first thing dropped. relevant_files is the node's own declared scope,
#: trimmed last and never emptied once it holds anything (see
#: _shrink_to_budget): a capsule with nothing about its own write scope left
#: is not a capsule, it is an empty envelope.
TRIM_ORDER = ("vault_context", "relevant_decisions", "dependency_outputs",
             "relevant_files")


def _truncate(text, max_bytes):
    """text, or its first max_bytes bytes plus a marker. Byte-bounded rather
    than character-bounded, which is what keeps the total budget an honest
    promise under multi-byte UTF-8."""
    if not text:
        return ""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", "ignore") + "...[truncated]"


def _read_relevant_files(paths, cwd, max_file_bytes):
    """Only literal files at literal declared paths, read once each. A path
    that is a directory, missing, or unreadable is skipped, never expanded:
    expanding a directory into every file under it is the whole-repo-dump
    this module exists to refuse."""
    cwd = cwd or os.getcwd()
    out = {}
    for rel in sorted(p for p in set(paths) if p and p not in (".", "./")):
        full = os.path.join(cwd, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as fh:
                raw = fh.read(max_file_bytes + 1)
        except OSError:
            continue  # sbe: allow-silent unreadable is skipped, not fatal
        text = raw[:max_file_bytes].decode("utf-8", "replace")
        if len(raw) > max_file_bytes:
            text += "...[truncated]"
        out[rel] = text
    return out


def _size(payload):
    return len(json.dumps(payload, sort_keys=True).encode("utf-8"))


def _shrink_to_budget(bundle, max_total_bytes):
    """Drop entries from the lowest-authority section first, per TRIM_ORDER,
    until the whole bundle fits or nothing trimmable is left."""
    for key in TRIM_ORDER:
        section = bundle.get(key)
        while _size(bundle) > max_total_bytes and section:
            if isinstance(section, dict):
                if key == "relevant_files" and len(section) <= 1:
                    break
                del section[sorted(section.keys())[-1]]
            elif isinstance(section, list):
                section.pop()
            else:
                break
    return bundle


#: RL3.a: the keys every tagged vault_context entry carries. A missing one is
#: malformed; an extra one is not.
ENTRY_KEYS = ("snippet", "source", "authority", "kind", "scope",
              "observed_at", "revoked", "authoritative")

#: bm_vault_authority.LEVELS, mirrored: scripts/ ships without products/.
AUTHORITY_LEVELS = ("casual", "derived", "source_of_record")

KINDS = ("explicit_preference", "observation", "inferred_hypothesis")

DEFAULT_HORIZON_DAYS = 90

#: Reasons admit_context() withholds an entry, in the order they are tested.
WITHHELD_REASONS = ("malformed", "revoked", "cross-scope", "stale")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T.+)?$")


def context_scope(node, cwd=None):
    """{'account', 'project'} for one request. An empty account is kept empty
    and matches only an entry whose account is also empty: absence is never a
    wildcard. A non-str account or project is refused, never coerced."""
    if not isinstance(node, dict):
        raise ValueError("context_scope: node must be a dict, got %r"
                         % type(node).__name__)
    account = node.get("account") or ""
    project = node.get("project") or os.path.realpath(cwd or os.getcwd())
    if not isinstance(account, str) or not isinstance(project, str):
        raise ValueError("context_scope: account and project must be str, "
                         "got %r and %r" % (account, project))
    return {"account": account, "project": project}


def tag_entry(snippet, source, authority="casual", kind="observation",
              scope=None, observed_at=None, revoked=False):
    """One vault_context entry with every ENTRY_KEYS key present and
    authoritative always False. The snippet is truncated by build_capsule's
    byte cap, never here. Anything outside the vocabulary raises ValueError
    naming the value."""
    if not isinstance(snippet, str):
        raise ValueError("tag_entry: snippet must be str, got %r" % (snippet,))
    if not isinstance(source, str):
        raise ValueError("tag_entry: source must be str, got %r" % (source,))
    if not isinstance(authority, str) or authority not in AUTHORITY_LEVELS:
        raise ValueError("tag_entry: unknown authority %r, not in %s"
                         % (authority, "/".join(AUTHORITY_LEVELS)))
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("tag_entry: unknown kind %r, not in %s"
                         % (kind, "/".join(KINDS)))
    if scope is not None and not isinstance(scope, dict):
        raise ValueError("tag_entry: scope must be a dict or None, got %r"
                         % (scope,))
    if observed_at is not None and not isinstance(observed_at, str):
        raise ValueError("tag_entry: observed_at must be an ISO date str or "
                         "None, got %r" % (observed_at,))
    if not isinstance(revoked, bool):
        raise ValueError("tag_entry: revoked must be a bool, got %r"
                         % (revoked,))
    return {"snippet": snippet, "source": source, "authority": authority,
            "kind": kind, "scope": dict(scope) if scope is not None else None,
            "observed_at": observed_at, "revoked": revoked,
            "authoritative": False}


def _parse_observed(value):
    """The date in an ISO date or datetime string, or None when it cannot be
    read. Strict on shape so Python 3.9 and later Pythons agree."""
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return None
    try:
        day = date.fromisoformat(value[:10])
        if len(value) > 10:
            datetime.fromisoformat(value)
    except ValueError:
        return None
    return day


def _entry_malformed(entry):
    if not isinstance(entry, dict):
        return True
    if any(key not in entry for key in ENTRY_KEYS):
        return True
    if entry["authoritative"] is not False:
        return True
    if not isinstance(entry["revoked"], bool):
        return True
    if not isinstance(entry["snippet"], str) or not isinstance(entry["source"], str):
        return True
    if entry["authority"] not in AUTHORITY_LEVELS or entry["kind"] not in KINDS:
        return True
    return False


def _entry_cross_scope(entry_scope, scope):
    if not isinstance(entry_scope, dict):
        return True
    for key in ("account", "project"):
        if key not in entry_scope or entry_scope[key] != scope[key]:
            return True
    return False


def _withhold_reason(entry, scope, today, horizon_days):
    if _entry_malformed(entry):
        return "malformed"
    if entry["revoked"] is True:
        return "revoked"
    if _entry_cross_scope(entry["scope"], scope):
        return "cross-scope"
    observed = _parse_observed(entry["observed_at"])
    if observed is None:
        return "stale"
    age = (today - observed).days
    if age < 0 or age > horizon_days:
        return "stale"
    return None


def _check_request(scope, today, horizon_days):
    if not isinstance(scope, dict):
        raise ValueError("admit_context: scope must be a dict, got %r"
                         % (scope,))
    for key in ("account", "project"):
        if not isinstance(scope.get(key), str):
            raise ValueError("admit_context: scope %s must be str, got %r"
                             % (key, scope.get(key)))
    if isinstance(today, datetime) or not isinstance(today, date):
        raise ValueError("admit_context: today must be a date, got %r"
                         % (today,))
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) \
            or horizon_days < 0:
        raise ValueError("admit_context: horizon_days must be a non negative "
                         "int, got %r" % (horizon_days,))


def admit_context(entries, scope, today, horizon_days=DEFAULT_HORIZON_DAYS):
    """(kept, withheld). Each entry is judged in WITHHELD_REASONS order and the
    first reason that fires decides; withheld items are {'source', 'reason'}.
    Pure over its arguments: no clock read, nothing cached, so an entry
    admitted on the last request is judged again on this one. A request that
    is itself unreadable raises ValueError rather than admitting anything."""
    if not isinstance(entries, list):
        raise ValueError("admit_context: entries must be a list, got %r"
                         % type(entries).__name__)
    _check_request(scope, today, horizon_days)
    kept, withheld = [], []
    for entry in entries:
        reason = _withhold_reason(entry, scope, today, horizon_days)
        if reason is None:
            kept.append(entry)
            continue
        source = entry.get("source") if isinstance(entry, dict) else None
        withheld.append({"source": source if isinstance(source, str) else "",
                         "reason": reason})
    return kept, withheld


def _tagged_or_withheld(item):
    """(entry, None) ready for admit_context, or (None, withheld item) when the
    raw snippet cannot even be tagged. A dict carrying any tag-only key is
    taken as already tagged and judged as it stands."""
    if isinstance(item, str):
        return tag_entry(item, "vault"), None
    if isinstance(item, dict):
        if any(key in item for key in ENTRY_KEYS[2:]):
            return item, None
        try:
            return tag_entry(item.get("snippet", ""),
                             item.get("source", "vault")), None
        except ValueError:
            source = item.get("source")
            return None, {"source": source if isinstance(source, str) else "",
                          "reason": "malformed"}
    return None, {"source": "", "reason": "malformed"}


def _scoped_vault_context(vault_snippets, scope, max_file_bytes):
    """(vault_context, vault_withheld) for build_capsule's scope keyword."""
    if not isinstance(scope, dict):
        raise ValueError("build_capsule: scope must be a dict or None, got %r"
                         % (scope,))
    if vault_snippets is not None and not isinstance(vault_snippets, list):
        raise ValueError("build_capsule: vault_snippets must be a list, got %r"
                         % type(vault_snippets).__name__)
    tagged, refused = [], []
    for item in vault_snippets or []:
        entry, refusal = _tagged_or_withheld(item)
        if entry is not None:
            tagged.append(entry)
        else:
            refused.append(refusal)
    kept, withheld = admit_context(tagged, scope, date.today())
    vault_context = []
    for entry in kept:
        out = dict(entry)
        out["snippet"] = _truncate(entry["snippet"], max_file_bytes)
        vault_context.append(out)
    return vault_context, refused + withheld


def build_capsule(node, cwd=None, dependency_outputs=None, decisions=None,
                  vault_snippets=None, max_file_bytes=DEFAULT_MAX_FILE_BYTES,
                  max_total_bytes=DEFAULT_MAX_TOTAL_BYTES, scope=None):
    """One deterministic, byte-bounded context bundle for one unit.

    Carries the same core keys as loop_bridge.run_node()'s own unit dict
    (unit_id, objective, done_check, write_scope, read_scope, role,
    risk_class, attempt, prior_failure_note) so this is a drop-in widening
    of that shape, not a second parallel mechanism: model_worker.py's
    build_prompt() already reads every one of those keys with .get().

    Widened with: contract_fragment (node.get("contract"), the written spec
    fragment for this unit, distinct from its executable done_check),
    dependencies (node's own declared upstream unit ids), relevant_files
    (read from the node's own write_scope/read_scope only), relevant_checks,
    relevant_decisions (caller-supplied, e.g. a reconciled WBS-10.03
    ruling), dependency_outputs (caller-supplied, truncated), vault_context
    (caller-supplied, always marked non-authoritative), and capsule_hash.

    dependency_outputs: {dep_unit_id: output_text}.
    decisions: [{"decision"/"choice", "reason", "cost_if_wrong",
    "deciding_check", ...}], only those five keys are kept per entry.
    vault_snippets: [str] or [{"snippet": str, "source": str}].

    scope (RL3.a): None keeps the body above unchanged. A dict, normally
    context_scope(node, cwd), sends every vault snippet through
    admit_context() with today's date: plain snippets are tagged with
    tag_entry() defaults first, kept entries become vault_context, and the
    rest are listed under vault_withheld as {'source', 'reason'}.
    """
    write_scope = list(node.get("owns") or node.get("write_scope") or [])
    read_scope = list(node.get("read_scope") or [])
    done_check = node.get("done_check") or ""

    relevant_decisions = [
        {k: d.get(k) for k in ("decision", "choice", "reason",
                               "cost_if_wrong", "deciding_check")
         if d.get(k) is not None}
        for d in (decisions or [])
    ]
    dependency_outputs_out = {
        str(k): _truncate(str(v), max_file_bytes)
        for k, v in (dependency_outputs or {}).items()
    }
    vault_withheld = None
    if scope is None:
        vault_context = [
            {"snippet": _truncate(s if isinstance(s, str) else s.get("snippet", ""),
                                  max_file_bytes),
             "source": (s.get("source", "vault") if isinstance(s, dict)
                       else "vault"),
             "authoritative": False}
            for s in (vault_snippets or [])
        ]
    else:
        vault_context, vault_withheld = _scoped_vault_context(
            vault_snippets, scope, max_file_bytes)

    capsule = {
        "unit_id": node["id"],
        "objective": node.get("name") or node.get("title") or node["id"],
        "contract_fragment": (node.get("contract")
                              or node.get("spec_fragment") or ""),
        "done_check": done_check,
        "write_scope": write_scope,
        "read_scope": read_scope,
        "role": node.get("role") or "builder",
        "risk_class": node.get("risk_class") or "normal",
        "attempt": node.get("attempt") or 1,
        "prior_failure_note": node.get("prior_failure_note") or "",
        "dependencies": list(node.get("depends_on")
                             or node.get("dependencies") or []),
        "relevant_files": _read_relevant_files(write_scope + read_scope, cwd,
                                               max_file_bytes),
        "relevant_checks": [done_check] if done_check else [],
        "relevant_decisions": relevant_decisions,
        "dependency_outputs": dependency_outputs_out,
        "vault_context": vault_context,
    }
    if vault_withheld is not None:
        capsule["vault_withheld"] = vault_withheld
    _shrink_to_budget(capsule, max_total_bytes)
    capsule["capsule_hash"] = hashlib.sha256(
        json.dumps(capsule, sort_keys=True).encode("utf-8")).hexdigest()
    return capsule


#: RL3.c: the hosts brother_paths.client can name. Anything else is NO-DATA.
HOSTS = ("claude", "codex", "cursor")


def host_observation(env=None):
    """{'host', 'observed'} for this process. host is brother_paths.client(env)
    when it names one of HOSTS, else 'NO-DATA'; an ImportError (the export tree
    ships without brother_paths) is NO-DATA too. Only the marker variables
    client reads are consulted: no directory walk, no transcript, no process
    list, no network. An env that is not a dict or None is refused."""
    if env is not None and not isinstance(env, dict):
        raise ValueError("host_observation: env must be a dict or None, got %r"
                         % type(env).__name__)
    try:
        import brother_paths
    except ImportError:
        return {"host": "NO-DATA", "observed": False}
    host = brother_paths.client(env)
    if not isinstance(host, str) or host not in HOSTS:
        host = "NO-DATA"
    return {"host": host, "observed": host != "NO-DATA"}

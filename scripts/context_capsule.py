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


def build_capsule(node, cwd=None, dependency_outputs=None, decisions=None,
                  vault_snippets=None, max_file_bytes=DEFAULT_MAX_FILE_BYTES,
                  max_total_bytes=DEFAULT_MAX_TOTAL_BYTES):
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
    vault_context = [
        {"snippet": _truncate(s if isinstance(s, str) else s.get("snippet", ""),
                              max_file_bytes),
         "source": (s.get("source", "vault") if isinstance(s, dict)
                   else "vault"),
         "authoritative": False}
        for s in (vault_snippets or [])
    ]

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
    _shrink_to_budget(capsule, max_total_bytes)
    capsule["capsule_hash"] = hashlib.sha256(
        json.dumps(capsule, sort_keys=True).encode("utf-8")).hexdigest()
    return capsule

#!/usr/bin/env python3
"""bm_vault_pack: compile raw vault retrieval hits into one bounded Context Pack.

WHY THIS EXISTS (VH-50). bm_vault.py's own retrieval (_search) already applies the
policy-deny pre-filter, BM25/anchor/link-expansion retrieval and authority-based
ranking (see bm_vault.py's own module docstring). What a provider-neutral caller
needs at the moment of a decision is not a ranked list of notes to go read itself,
it is ONE small, structured, auditable object: the notes this estate actually
stands behind for this question ("authoritative"), bounded to a fixed budget, with
every drop and every absence named rather than silently swallowed. This module is
that compilation step.

THIS IS A CONSUMER OF bm_vault.py, NEVER A SECOND RETRIEVAL ENGINE. Every hit this
file ever sees has already been through bm_vault.py's own _search: the policy-deny
trim, the relevance floor, the authority sort, the stem dedup and the link
expansion guard all already ran, before this module reads a single row. A note the
policy denies never reaches the classification code below, because _search drops
it (see _search's own docstring: "a denied note's content is never read for
ranking and never printed anywhere") before returning. This module calls _search,
never a lower-level signal function, for exactly that reason.

THE CLASSIFICATION RULE (the brief). A hit is "authoritative" only when BOTH hold:
  - bm_vault_authority.read_authority(body) is "source_of_record" or "derived", and
  - bm_vault_lifecycle.read_promotion(body) is not one of candidate / expired /
    revoked, and is not None (a promotion: value the lifecycle contract cannot
    even rank -- an unrankable claim is not a claim to stand on, and treating it
    as authoritative would be worse than treating an excluded state as one).
Everything else is dropped from `authoritative`. A dropped CANDIDATE note is named
in `warnings` rather than disappearing silently, because "the estate has not
validated this yet" is different information from "the estate never wrote this
down", and burying the first as the second is exactly the D12 candidate-serving
defect bm_vault.py's own lifecycle contract exists to prevent.

NOTE BODY TEXT IS NEVER TREATED AS AN INSTRUCTION. The only use this file makes of
a note's body is extracting four structured fields (authority level, lifecycle
state, a plain-text excerpt, a content hash). Nothing here builds a prompt,
forwards a note's own words as directives to anything, or interprets an excerpt
as anything but an inert string.

Python 3.9, standard library only, no network. Read-only over bm_vault.py's own
index; this file writes nothing anywhere.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import uuid

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    """Dynamic import by path, the same defensive pattern every sibling contract
    module in bm_vault.py already uses (_load_bm_vault_authority etc): tools/ is
    not a package, so a bare `import X` only resolves by accident of cwd and
    fails outright in a deployed snapshot directory."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_TOOLS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded once, at import time, not per call: bm_vault.py is the required engine
# this whole module exists to consume, not an optional contract module that might
# be absent from a deployed snapshot. Its own sibling contracts (lifecycle,
# authority, ids) ARE guarded independently below, the same degrade-never-crash
# posture bm_vault.py itself keeps for each of them.
bm_vault = _load("bm_vault_for_pack", "bm_vault.py")

try:
    bm_vault_lifecycle = _load("bm_vault_lifecycle_for_pack", "bm_vault_lifecycle.py")
    _LIFECYCLE_LOAD_ERROR = None
except Exception as _e:  # pragma: no cover, exercised only by a partial deployment
    bm_vault_lifecycle = None
    _LIFECYCLE_LOAD_ERROR = str(_e)

try:
    bm_vault_authority = _load("bm_vault_authority_for_pack", "bm_vault_authority.py")
    _AUTHORITY_LOAD_ERROR = None
except Exception as _e:  # pragma: no cover, exercised only by a partial deployment
    bm_vault_authority = None
    _AUTHORITY_LOAD_ERROR = str(_e)

try:
    bm_vault_ids = _load("bm_vault_ids_for_pack", "bm_vault_ids.py")
except Exception:  # sbe: allow-silent optional stable-id module; _note_id falls back to a path-derived id
    bm_vault_ids = None


SCHEMA = "brother-context-pack-v1"
AUTHORITATIVE_LEVELS = ("source_of_record", "derived")
# rejected/under_review added after adversarial review found a rejected note
# (the estate explicitly said "wrong") being served as authoritative -- worse
# than the candidate case this module already guarded, since candidate at
# least means "unvalidated", not "known wrong" or "still being checked".
EXCLUDED_LIFECYCLE_STATES = ("candidate", "expired", "revoked", "rejected", "under_review")
EXCERPT_CHARS = 200

#: How many raw hits _search is asked for per compile_pack call, relative to the
#: caller's budget: enough survive this module's own authoritative/candidate/
#: casual classification for a real budget cutoff to be provable, without turning
#: this into a search loop that re-asks for more on a shortfall. _search's own
#: limit still governs what IT considers (dedup, the relevance floor, link
#: expansion); this multiplier only sizes the fetch handed to classification.
#: ponytail: a flat multiplier, not adaptive; raise it if a real corpus is ever
#: starved by it.
FETCH_MULTIPLIER = 4
FETCH_FLOOR = 20

#: Wall-clock budget for `git rev-parse HEAD` in the vault, mirroring
#: vault_recall_hook.py's own GIT_ROOT_TIMEOUT_S for the equivalent
#: --show-toplevel call: one process, almost always already warm in the OS
#: cache, and this must never make a pack compile hang on a vault with no git
#: repository at all.
GIT_COMMIT_TIMEOUT_S = 3


def _content_hash(text):
    """sha256 hex digest of a note's full text -- the exact algorithm
    bm_vault_cite.py hashes a citation with (cmd_mint's own
    hashlib.sha256(text.encode("utf-8")).hexdigest(), inside its private
    _hash_and_lifecycle). Not called through that function directly: it reads
    the note fresh from a path on disk and returns a coupled lifecycle read of
    its own, in bm_vault_cite's narrower 4-state citation vocabulary
    (candidate/validated/canonical/rejected, no under_review/revoked/expired),
    which is not the D12 lifecycle classification this module is required to
    use bm_vault_lifecycle for. Reading the note twice (once here from the
    already-loaded index body, once again from disk inside bm_vault_cite) would
    also silently prefer whatever bm_vault_cite happens to find on disk over the
    exact text bm_vault.py's own index just classified, which can differ for a
    synthetic or in-memory fixture with no backing file at all. The hashing
    FORMULA is reused verbatim; only the disk re-read is skipped."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prose(body):
    """The note's own text with the YAML frontmatter fence stripped, so an
    excerpt is not just the first 200 characters of `---\\nname: ...`."""
    body = body or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            return body[end + 4:].strip()
    return body.strip()


def _note_id(body, path):
    """The vault's own stable id (bm_vault_ids.read_id: n-<16 hex>) when the note
    declares one; a path-derived fallback otherwise, so an authoritative item
    never carries an empty id (measured 2026-08-29 in bm_vault_ids.py's own
    docstring: zero of 802 notes carried one at the time it was written)."""
    if bm_vault_ids is not None:
        nid = bm_vault_ids.read_id(body)
        if nid:
            return nid
    return "path:" + (path or "")


def _note_type(body):
    """The note's own `type:` frontmatter value (the same field bm_vault.py's
    _classify reads to tell a lesson from a log), or "note" when it declares
    none. Read from the fenced frontmatter block only, via bm_vault.py's own
    FRONT_TYPE/_frontmatter_block, so a note whose prose merely discusses
    "type: x" cannot claim one it never declared."""
    m = bm_vault.FRONT_TYPE.search(bm_vault._frontmatter_block(body or ""))
    return m.group(1).strip() if m else "note"


def _vault_commit(vault_root):
    """The vault's own HEAD commit, or None when it is not a git repository, git
    is unavailable, or the call is too slow. Provenance only: a pack with no
    vault_commit is still a valid pack, never a NO-DATA condition on its own."""
    if not vault_root or not os.path.isdir(vault_root):
        return None
    try:
        proc = subprocess.run(["git", "-C", vault_root, "rev-parse", "HEAD"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=GIT_COMMIT_TIMEOUT_S)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace").strip()
    except Exception:  # sbe: allow-silent no git, no repo, or too slow; vault_commit is optional provenance
        pass
    return None


def compile_pack(vault_root, query, identity=None, budget=7):
    """The Context Pack for `query`. See the module docstring for the schema and
    the classification rule.

    vault_root names the vault this pack is compiled from: recorded nowhere in
    the returned object (the brief's schema names no such field), used only to
    resolve `retrieval_trace.vault_commit`. Retrieval itself reads bm_vault.py's
    own already-built index (bm_vault._connect()), never a fresh scan of
    vault_root -- the identical relationship cmd_recall already has to it, access
    policy included: bm_vault._policy_deny resolves the policy file from the
    environment/config default vault exactly as cmd_recall's own call does, not
    from this parameter, so a caller pointing BM_VAULT_ROOT at the same vault
    sees the same policy compile_pack applies here as `bm_vault.py recall` would.

    identity: forwarded to bm_vault._policy_deny exactly as cmd_recall forwards
    --identity; None means the anonymous caller, unchanged in meaning.

    budget: the maximum number of items in `authoritative` (default 7, matching
    the smallest end of a typical enterprise range per the brief). Retrieval
    fetches more than this from _search on purpose (FETCH_MULTIPLIER) so a real
    budget cutoff is provable rather than starved by too small a fetch; the
    bounding itself happens here, after classification, never inside _search.
    """
    warnings = []
    if _LIFECYCLE_LOAD_ERROR:
        warnings.append({
            "kind": "contract-unavailable",
            "message": "NO-DATA: lifecycle contract unavailable (%s); no hit can be "
                       "classified authoritative without it" % _LIFECYCLE_LOAD_ERROR})
    if _AUTHORITY_LOAD_ERROR:
        warnings.append({
            "kind": "contract-unavailable",
            "message": "NO-DATA: authority contract unavailable (%s); no hit can be "
                       "classified authoritative without it" % _AUTHORITY_LOAD_ERROR})

    fused = []
    if bm_vault_lifecycle is not None and bm_vault_authority is not None:
        con = bm_vault._connect()
        bm_vault._schema(con)
        args = {"identity": identity} if identity else {}
        deny, policy_error, refusal_reason, _degraded = bm_vault._policy_deny(args, con)
        if policy_error:
            warnings.append({"kind": "policy-error",
                             "message": "NO-DATA access policy: %s" % policy_error})
        elif refusal_reason:
            warnings.append({"kind": "policy-refused",
                             "message": "REFUSED: %s" % refusal_reason})
        else:
            fetch_limit = max(budget * FETCH_MULTIPLIER, FETCH_FLOOR)
            # fast=True: this compiler serves a bounded, auditable object, not an
            # interactive deep-recall session -- the same budget-over-recall-depth
            # tradeoff bm_vault.py's own --fast already names for hook and
            # session-start callers. The dense signal, when it is needed at all,
            # costs 30-75s per bm_vault.py's own measurement; a pack compile is
            # not the place to pay that.
            fused, _why, _total = bm_vault._search(con, text=query, limit=fetch_limit,
                                                   fast=True, deny=deny)
            con.commit()

        authoritative = []
        omitted = 0
        for nid, _score in fused:
            row = con.execute("SELECT path, title, body FROM notes WHERE id=?",
                              (nid,)).fetchone()
            if row is None:
                continue
            body = row["body"] or ""
            level, _auth_problem = bm_vault_authority.read_authority(body)
            state, _record, _problems = bm_vault_lifecycle.read_promotion(body)
            if level in AUTHORITATIVE_LEVELS and state is not None \
                    and state not in EXCLUDED_LIFECYCLE_STATES:
                if len(authoritative) >= budget:
                    omitted += 1
                    continue
                authoritative.append({
                    "id": _note_id(body, row["path"]),
                    "type": _note_type(body),
                    "title": row["title"],
                    "lifecycle": state,
                    "authority": level,
                    "excerpt": _prose(body)[:EXCERPT_CHARS],
                    "content_sha256": _content_hash(body),
                })
            elif state == "candidate":
                warnings.append({
                    "kind": "candidate-excluded",
                    "note_id": _note_id(body, row["path"]),
                    "title": row["title"],
                    "message": "candidate note %r excluded from an authoritative "
                               "slot: written, not yet validated" % row["title"],
                })
            elif state in ("rejected", "under_review"):
                warnings.append({
                    "kind": "rejected-excluded" if state == "rejected"
                            else "under-review-excluded",
                    "note_id": _note_id(body, row["path"]),
                    "title": row["title"],
                    "message": "%s note %r excluded from an authoritative slot"
                               % (state, row["title"]),
                })
        con.close()
    else:
        authoritative, omitted = [], 0

    if not authoritative and not omitted:
        # omitted > 0 with an empty authoritative list means budget=0, not
        # "nothing written" -- that is a caller misconfiguration, not NO-DATA,
        # and claiming NO-DATA there would misreport a written record as absent.
        warnings.append({"kind": "no-current-authority",
                         "message": "NO-DATA: no current authoritative contract found"})

    return {
        "schema": SCHEMA,
        "pack_id": "cp-" + uuid.uuid4().hex[:16],
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "query_hash": hashlib.sha256((query or "").encode("utf-8")).hexdigest(),
        "authoritative": authoritative,
        "constraints": [],
        "warnings": warnings,
        "retrieval_trace": {
            "vault_commit": _vault_commit(vault_root),
            "item_count": len(authoritative),
            "truncated": omitted > 0,
            "omitted_count": omitted,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--query", required=True)
    ap.add_argument("--identity", default=os.environ.get("BM_IDENTITY"))
    ap.add_argument("--budget", type=int, default=7)
    args = ap.parse_args(argv)
    pack = compile_pack(args.vault, args.query, identity=args.identity, budget=args.budget)
    print(json.dumps(pack, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

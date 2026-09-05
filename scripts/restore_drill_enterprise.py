#!/usr/bin/env python3
"""restore_drill_enterprise: a real, populated, multi-tenant restore drill
against the BrotherModeUp governed memory vault tools.

WHY THIS EXISTS. The prior drill (docs/plan/RESTORE-DRILL-RESULT.json)
restored a real vault tree-identical, but its own verification section says
it plainly: "assertions_jsonl": "absent at source and absent restored" and
"legal_holds_jsonl": "absent at source and absent restored, same reason".
Six acceptance reviewers flagged the same hole: a store that is empty before
and empty after proves nothing about whether a restore preserves it. This
drill populates both stores for real, with the real CLIs (bm_vault_assertions.py
mint-assertion/mint-resolution, bm_vault_retention.py legal-hold), across TWO
tenants, then proves object counts and content hashes survive backup,
destruction and restore into a fresh location, and that the GOVERNANCE
BEHAVIOR built on those stores (conflict resolution, a temporal as-of answer,
an identity merge, a legal hold blocking forget-execute) reads identically
before and after.

WHAT "BACKUP" MEANS HERE, stated honestly rather than papered over.
bm_vault_export.py's own docstring is explicit: its "bundle" command exports
a DERIVED subset (claim-level assertions parsed out of note prose, plus the
event stream) to a caller-named --out directory; it does not capture
99-System/assertions.jsonl (the institutional subject-predicate store),
resolutions.jsonl, legal_holds.jsonl, principals.json, identity merge events,
or the notes themselves. There is no single command in this tools tree that
backs up and restores a WHOLE vault. This drill therefore uses TWO backup
mechanisms, named separately in the results, matching what each is actually
for:
  1. bm_vault_export.py bundle/verify -- the real export API the task names,
     run before destruction and again after restore, proving the claim/event
     layer's tables hash-match and verify() passes on both sides.
  2. a full tar archive of the vault tree (stdlib tarfile) -- the same class
     of mechanism the prior drill's own git-clone precedent used, needed
     because nothing else in this tools tree backs up 99-System/assertions.jsonl,
     resolutions.jsonl or legal_holds.jsonl. This is named as a finding, not
     hidden: the export API alone cannot pass this drill's own bar.

TENANCY. bm_vault_identity.py's own docstring states the current design
decision directly: tenant scope is "full vault-root isolation via HOME"; a
per-note tenant column was considered and rejected. This drill therefore
models two tenants as two independent vault roots, each indexed under its
own HOME (so each gets its own retrieval index and its own
~/.claude/bm_vault.json-free environment), and proves isolation the same way
the real system provides it: a recall query for tenant B's canary phrase,
run against tenant A's restored vault, returns nothing.

Resolves the vault tools directory from $BROTHERMODEUP_TOOLS, else
/tmp/bmu-main/tools (mirrors scripts/test_japanese_threshold.py's own
NO-DATA contract). Exit 0 passed, 1 not passed (named gaps), 2 NO-DATA (the
tools directory is missing).

No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

# The hub's own product tree comes FIRST (2026-09-02, same defect class as the
# erasure drill): a drill inside the hub proves the hub's bytes, never a
# sibling or retired checkout's.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_TOOLS_DIR = os.path.join(REPO_ROOT, "products", "brothermode", "tools")
CONVENTIONAL_TOOLS_DIR = "/tmp/bmu-main/tools"


def current_commit(repo_root):
    """The full SHA of HEAD in repo_root, or None with a printed reason.
    readiness_gate.py binds this record's PASS to this commit being an
    ancestor of the tree that reads it (evidence auditor 2026-09-03: an
    unbound record reads PASS against any later code forever), so a drill
    run outside a git checkout, or one where git itself is unavailable,
    must say so rather than write a field that silently claims nothing."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("commit: git rev-parse failed to run: %s" % exc, file=sys.stderr)
        return None
    if proc.returncode != 0:
        print("commit: git rev-parse HEAD exited %d: %s"
              % (proc.returncode, proc.stderr.strip()), file=sys.stderr)
        return None
    return proc.stdout.strip()


#: The vault tools this drill actually exercises, every one of them invoked
#: by run(tools_dir, "<name>.py", ...) or load_module(tools_dir, "<name>")
#: below. Re-derive after adding a call:
#:   grep -oE 'run\(tools_dir, "[a-z_]+\.py"|load_module\(tools_dir, "[a-z_]+"' \
#:     scripts/restore_drill_enterprise.py | sed -E 's/.*"([a-z_]+)(\.py)?"?/\1.py/' | sort -u
COVERED_TOOLS = (
    "bm_vault.py",
    "bm_vault_assertions.py",
    "bm_vault_compose.py",
    "bm_vault_export.py",
    "bm_vault_identity.py",
    "bm_vault_ids.py",
    "bm_vault_provenance.py",
    "bm_vault_retention.py",
)


def covered_files(tools_dir):
    """[{"path": repo-relative posix path, "sha256": hex}] for this drill and
    every vault tool it exercises, hashed as they exist at run time.

    WHY THE RECORD CARRIES THIS. readiness_gate.py binds this record's PASS
    to the code the drill ran against. Its first binding is ancestry, which
    a public clone can never satisfy: scripts/export_public.py builds the
    export as an ORPHAN commit, so no hub commit is an ancestor of it, and
    on 2026-09-04 that refused the 1.0.2 tag with "foreign commit". Ancestry
    was only ever a proxy for "the drill ran against this code", and content
    is that same property measured directly, which is checkable in any
    history. Returns None with a printed reason when a listed file cannot be
    read: a partial list would narrow the binding silently."""
    paths = [os.path.abspath(__file__)] + [
        os.path.join(tools_dir, name) for name in COVERED_TOOLS]
    covered = []
    for path in paths:
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        if rel.startswith(".."):
            print("covered: %s sits outside the repository, so no repository "
                  "relative path can name it" % path, file=sys.stderr)
            return None
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            print("covered: %s is unreadable: %s" % (path, exc), file=sys.stderr)
            return None
        covered.append({"path": rel, "sha256": digest})
    return covered


def repo_relative_tools_dir(tools_dir):
    """The tools directory as the record should name it: repository relative,
    never absolute. An absolute path under one person's home is machine
    local, leaks the layout of that machine, and made this record a declared
    exception in scripts/test_export_public.py's portability gate."""
    rel = os.path.relpath(tools_dir, REPO_ROOT).replace(os.sep, "/")
    if rel.startswith(".."):
        return ("outside this repository, resolved from $BROTHERMODEUP_TOOLS "
                "or the conventional path")
    return rel


def find_tools_dir():
    override = os.environ.get("BROTHERMODEUP_TOOLS")
    candidates = ([override] if override else []) + [HUB_TOOLS_DIR, CONVENTIONAL_TOOLS_DIR]
    tried = []
    for cand in candidates:
        if not cand:
            continue
        tried.append(cand)
        if os.path.isfile(os.path.join(cand, "bm_vault.py")):
            return cand, None
    where = ("BROTHERMODEUP_TOOLS=%r, conventional %r" % (override, CONVENTIONAL_TOOLS_DIR)
             if override else "conventional %r" % CONVENTIONAL_TOOLS_DIR)
    return None, ("NO-DATA: bm_vault.py not found under any candidate (%s)" % where)


def load_module(tools_dir, name):
    path = os.path.join(tools_dir, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(tools_dir, tool, args, env, timeout=180):
    """One real CLI invocation of tools/<tool>.py as a subprocess (never
    re-implemented in-process): (returncode, combined stdout+stderr)."""
    cmd = [sys.executable, os.path.join(tools_dir, tool)] + args
    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=timeout)
    return p.returncode, p.stdout


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def read_jsonl(path):
    """[record, ...] in file order, or None if the file does not exist --
    matches bm_vault_retention._read_jsonl's own NO-DATA-vs-empty distinction,
    read directly here (no dependency on that module's internals) because all
    this drill needs is the plain, documented one-json-object-per-line shape."""
    if not os.path.isfile(path):
        return None
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def note_text(note_id, extra_frontmatter, body_lines):
    fm = "id: %s\n" % note_id + "".join(l + "\n" for l in extra_frontmatter)
    return "---\n%s---\n\n%s\n" % (fm, "\n".join(body_lines))


def write_note(vault, relpath, note_id, extra_frontmatter, body_lines):
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note_text(note_id, extra_frontmatter, body_lines))
    return path


def hash_note_tree(vault):
    """(count, combined_hash) over every *.md file under vault: sorted
    relpath, one sha256(content) each, folded into one hash so a single
    changed byte anywhere in the tree, an added file, or a removed one all
    change the combined hash."""
    rows = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, vault)
                with open(p, "rb") as fh:
                    rows.append("%s:%s" % (rel, sha256_bytes(fh.read())))
    rows.sort()
    combined = sha256_bytes("\n".join(rows).encode("utf-8"))
    return len(rows), combined


def store_snapshot(vault):
    """counts + hashes for every institutional/append-only store this drill
    populates, read directly off disk -- the exact stores the prior drill's
    own result named as untested (assertions.jsonl, legal_holds.jsonl) plus
    their siblings (resolutions.jsonl, identity events, forget receipts)."""
    stores = {
        "assertions_jsonl": os.path.join(vault, "99-System", "assertions.jsonl"),
        "resolutions_jsonl": os.path.join(vault, "99-System", "resolutions.jsonl"),
        "legal_holds_jsonl": os.path.join(vault, "99-System", "legal_holds.jsonl"),
        "identity_events_jsonl": os.path.join(vault, ".identity", "events.jsonl"),
    }
    out = {}
    for name, path in stores.items():
        records = read_jsonl(path)
        if records is None:
            out[name] = {"present": False, "rows": 0, "sha256": None}
        else:
            out[name] = {"present": True, "rows": len(records), "sha256": sha256_file(path)}
    receipts_dir = os.path.join(vault, "99-System", "forget-receipts")
    if os.path.isdir(receipts_dir):
        names = sorted(os.listdir(receipts_dir))
        combined = sha256_bytes("".join(
            n + ":" + sha256_file(os.path.join(receipts_dir, n)) for n in names
        ).encode("utf-8"))
        out["forget_receipts"] = {"present": True, "rows": len(names), "sha256": combined}
    else:
        out["forget_receipts"] = {"present": False, "rows": 0, "sha256": None}
    note_count, note_hash = hash_note_tree(vault)
    out["notes_tree"] = {"present": note_count > 0, "rows": note_count, "sha256": note_hash}
    return out


def bundle(tools_dir, vault, out_dir, env):
    rc, output = run(tools_dir, "bm_vault_export.py",
                      ["bundle", "--vault", vault, "--out", out_dir], env)
    manifest_path = os.path.join(out_dir, "MANIFEST.json")
    manifest = None
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    return rc, output, manifest


def verify_bundle(tools_dir, out_dir, env):
    rc, output = run(tools_dir, "bm_vault_export.py", ["verify", "--bundle", out_dir], env)
    return rc, output


class Check:
    """One named pass/fail line in the final report -- deliberately dumb (a
    name, a bool, one detail string) so the aggregation at the bottom is
    "every check passed", never a judgment call buried in code."""
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = bool(passed)
        self.detail = detail

    def as_dict(self):
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def build_tenant_vault(tools_dir, vault, canary_self):
    """Writes the fixture corpus for one tenant and returns the fixture's own
    ids so later steps (mint-assertion, legal-hold, merge, split, forget) and
    the behavioral re-checks after restore can address the same objects by
    name."""
    ids_mod = load_module(tools_dir, "bm_vault_ids")
    existing = set()

    def mint():
        nid = ids_mod.mint(existing)
        existing.add(nid)
        return nid

    id_a = mint()      # entity-a: survives an identity merge, subject of the conflict
    id_b = mint()      # entity-b: merged away into entity-a
    id_hold = mint()   # legal-hold target
    id_forget = mint() # forgotten via forget-plan/forget-execute
    id_claims = mint()
    id_japanese = mint()
    id_derive_src = mint()
    id_restricted = mint()

    write_note(vault, "entity-a.md", id_a,
               ["entity: project", "security_label: internal"],
               ["Entity A: the survivor of the identity merge and the subject "
                "of the conflicting assertions below."])
    write_note(vault, "entity-b.md", id_b,
               ["entity: project", "security_label: internal"],
               ["Entity B: merged into entity-a partway through this drill's fixture."])
    write_note(vault, "hold-note.md", id_hold, ["security_label: internal"],
               ["A note this drill places a legal hold on, by its stable id."])
    write_note(vault, "forget-note.md", id_forget, ["security_label: internal"],
               ["A note this drill forgets for real (forget-plan then forget-execute), "
                "with no hold on it, so the erasure actually executes."])
    write_note(vault, "source-claims.md", id_claims,
               ["authority: source_of_record", "promotion: validated",
                "promoted_by: khalil", "promoted_at: 2026-08-01",
                "security_label: internal"],
               ["claim: the CRM export names this record %s [evidence: query:crm|q-88213|"
                "2026-08-01T00:00:00Z|9f2b7a]" % canary_self,
                "claim: the incident report covers the same window "
                "[evidence: docspan:report-2026|8f2a91c4|12|100|180]",
                "claim: the on-call screenshot corroborates the timeline "
                "[evidence: capture:screenshots/restore-drill.png|2026-08-30T21:00:00Z|"
                "3c9fa2e1]",
                "claim: the fixture commit introduced this behavior [evidence: repo:abcdef1]",
                "claim: the public status page confirms the outage window "
                "[evidence: https://example.com/status/report-9]",
                "claim: entity-a declares this fact about itself [evidence: %s]" % id_a,
                "claim: the sibling note documents the same finding "
                "[evidence: entity-a.md]"])
    write_note(vault, "restricted-secret.md", id_restricted, ["restricted: true"],
               ["claim: %s must never leave this vault by default "
                "[evidence: repo:7654321]" % canary_self])
    write_note(vault, "japanese-note.md", id_japanese, ["security_label: public"],
               ["claim: 復元ドリルはテナントごとに隔離される [evidence: repo:1234567]",
                "",
                "この夜間の復元ドリルはテナントごとの隔離を検証する。"
                "日本語のクエリでも確実に検索できることを証明する。"])
    write_note(vault, "derive-source.md", id_derive_src,
               ["type: reference", "status: open", "security_label: public"],
               ["# Derive Source Fixture",
                "",
                "Introductory paragraph that stays with the source note.",
                "",
                "## Extractable Section",
                "",
                "This paragraph is pulled into its own derived note by "
                "bm_vault_compose.py split, inheriting this note's own public "
                "security label and a full derivation record."])

    return {
        "id_a": id_a, "id_b": id_b, "id_hold": id_hold, "id_forget": id_forget,
        "id_claims": id_claims, "id_japanese": id_japanese,
        "id_derive_src": id_derive_src, "id_restricted": id_restricted,
    }


def populate_governance(tools_dir, vault, env, ids):
    """Mints the institutional records this drill exists to prove survive
    restore: two conflicting assertions plus a canonical resolution that
    overrides the naive authority winner starting on a named date (the
    temporal as-of fixture), a legal hold, and an identity merge. Returns the
    checks for the MINTING itself (each command must exit 0) plus the ids
    later steps need."""
    checks = []
    subject = ids["id_a"]

    rc, out = run(tools_dir, "bm_vault_assertions.py",
                  ["mint-assertion", "--vault", vault, "--subject", subject,
                   "--predicate", "status", "--value", "green", "--authority", "casual",
                   "--lifecycle", "candidate", "--source", "repo:doc1"], env)
    checks.append(Check("mint assertion (casual, green)", rc == 0, out.strip()))
    a1_id = out.split("minted ", 1)[1].split(" ->", 1)[0].strip() if rc == 0 else None

    rc, out = run(tools_dir, "bm_vault_assertions.py",
                  ["mint-assertion", "--vault", vault, "--subject", subject,
                   "--predicate", "status", "--value", "red", "--authority", "source_of_record",
                   "--lifecycle", "validated", "--source", "repo:doc2"], env)
    checks.append(Check("mint assertion (source_of_record, red)", rc == 0, out.strip()))

    resolution_valid_from = "2026-06-01"
    rc, out = run(tools_dir, "bm_vault_assertions.py",
                  ["mint-resolution", "--vault", vault, "--subject", subject,
                   "--predicate", "status", "--winner", a1_id or "", "--scope", "global",
                   "--valid-from", resolution_valid_from, "--approver", "khalil",
                   "--role", "governance-lead", "--reason", "fixture override for the drill",
                   "--policy-version", "v1"], env)
    checks.append(Check("mint resolution (overrides the naive authority winner)",
                        rc == 0, out.strip()))

    rc, out = run(tools_dir, "bm_vault_retention.py",
                  ["legal-hold", "--vault", vault, "--target", ids["id_hold"],
                   "--by", "governance-bot", "--reason", "litigation hold, drill fixture"], env)
    checks.append(Check("legal-hold placed", rc == 0, out.strip()))

    merge_effective = "2026-03-01"
    rc, out = run(tools_dir, "bm_vault_identity.py",
                  ["merge", "--vault", vault, "--from", ids["id_b"], "--into", ids["id_a"],
                   "--rule-version", "vB1", "--effective", merge_effective], env)
    checks.append(Check("identity merge (entity-b into entity-a)", rc == 0, out.strip()))

    return checks, {"a1_id": a1_id, "resolution_valid_from": resolution_valid_from,
                     "merge_effective": merge_effective}


def derive_and_forget(tools_dir, vault, env, ids):
    checks = []
    rc, out = run(tools_dir, "bm_vault_compose.py",
                  ["split", "--vault", vault, "--note", "derive-source",
                   "--heading", "Extractable Section", "--today", "2026-08-30", "--apply"],
                  env)
    checks.append(Check("derived note via compose split", rc == 0, out.strip()))

    plan_path = os.path.abspath(os.path.join(vault, "..", "forget-plan.json"))
    rc, out = run(tools_dir, "bm_vault_retention.py",
                  ["forget-plan", "--vault", vault, "--id", ids["id_forget"],
                   "--out", plan_path], env)
    checks.append(Check("forget-plan (unheld note)", rc == 0, out.strip()))

    rc, out = run(tools_dir, "bm_vault_retention.py",
                  ["forget-execute", "--vault", vault, "--plan", plan_path], env)
    checks.append(Check("forget-execute (unheld note actually erased)", rc == 0, out.strip()))
    return checks


def index_vault(tools_dir, vault, env):
    rc, out = run(tools_dir, "bm_vault.py", ["index", "--vault", vault], env, timeout=300)
    return Check("index", rc == 0 and "indexed" in out, out.strip())


def behavioral_snapshot(tools_dir, vault, env, ids, canary_self, other_canary):
    """Every governance ANSWER this drill demands read identically before
    destruction and after restore: the conflict/resolution winner on both
    sides of the resolution's own valid_from, the identity-merge answer on
    both sides of the merge's own effective date, whether the legal hold is
    still active (reused via the real active_hold() function, not
    reimplemented), whether forget-execute still refuses against a held
    note, a Japanese-language recall hit, and this tenant's own canary being
    findable while the OTHER tenant's canary is not."""
    out = {}

    rc1, truth_before_resolution = run(tools_dir, "bm_vault_assertions.py",
        ["truth", "--vault", vault, "--subject", ids["id_a"], "--predicate", "status",
         "--scope", "global", "--as-of", "2026-01-01"], env)
    rc2, truth_after_resolution = run(tools_dir, "bm_vault_assertions.py",
        ["truth", "--vault", vault, "--subject", ids["id_a"], "--predicate", "status",
         "--scope", "global", "--as-of", "2026-07-01"], env)
    out["truth_before_resolution_valid_from"] = truth_before_resolution
    out["truth_after_resolution_valid_from"] = truth_after_resolution
    out["truth_exit_codes"] = [rc1, rc2]

    rc3, resolve_before_merge = run(tools_dir, "bm_vault_identity.py",
        ["resolve", "--vault", vault, "--source-id", ids["id_b"], "--as-of", "2026-01-01"], env)
    rc4, resolve_after_merge = run(tools_dir, "bm_vault_identity.py",
        ["resolve", "--vault", vault, "--source-id", ids["id_b"], "--as-of", "2026-12-01"], env)
    out["resolve_before_merge_effective"] = resolve_before_merge
    out["resolve_after_merge_effective"] = resolve_after_merge
    out["resolve_exit_codes"] = [rc3, rc4]

    retention_mod = load_module(tools_dir, "bm_vault_retention")
    holds = read_jsonl(os.path.join(vault, "99-System", "legal_holds.jsonl")) or []
    hold_record = retention_mod.active_hold(holds, ids["id_hold"])
    out["legal_hold_active"] = hold_record is not None
    out["legal_hold_record_id"] = hold_record.get("id") if hold_record else None

    held_plan_path = os.path.abspath(os.path.join(vault, "..", "held-forget-plan.json"))
    rc5, plan_out = run(tools_dir, "bm_vault_retention.py",
        ["forget-plan", "--vault", vault, "--id", ids["id_hold"], "--out", held_plan_path], env)
    if rc5 == 0:
        rc6, exec_out = run(tools_dir, "bm_vault_retention.py",
            ["forget-execute", "--vault", vault, "--plan", held_plan_path], env)
    else:
        rc6, exec_out = rc5, plan_out
    out["forget_execute_on_held_note_refused"] = (rc6 == 1 and "REFUSED" in exec_out)
    out["forget_execute_on_held_note_output"] = exec_out

    rc7, recall_japanese = run(tools_dir, "bm_vault.py",
        ["recall", "--query", "夜間の復元ドリル", "--limit", "5"], env, timeout=200)
    out["recall_japanese_found_own_note"] = "japanese-note.md" in recall_japanese
    out["recall_japanese_exit_code"] = rc7

    rc8, recall_self_canary = run(tools_dir, "bm_vault.py",
        ["recall", "--query", canary_self, "--fast", "--limit", "5"], env, timeout=60)
    out["recall_finds_own_canary"] = canary_self in recall_self_canary
    out["recall_self_canary_exit_code"] = rc8

    rc9, recall_other_canary = run(tools_dir, "bm_vault.py",
        ["recall", "--query", other_canary, "--fast", "--limit", "5"], env, timeout=60)
    out["recall_leaks_other_tenant_canary"] = other_canary in recall_other_canary
    out["recall_other_canary_exit_code"] = rc9

    return out


def make_env(home_dir):
    env = dict(os.environ)
    env["HOME"] = home_dir
    for k in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
        env.pop(k, None)
    os.makedirs(os.path.join(home_dir, ".claude"), exist_ok=True)
    return env


def evidence_locator_kinds(tools_dir, bundle_dir):
    prov = load_module(tools_dir, "bm_vault_provenance")
    assertions_path = os.path.join(bundle_dir, "assertions.jsonl")
    kinds = set()
    if os.path.isfile(assertions_path):
        with open(assertions_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    kinds.add(prov.classify_locator(row["evidence_locator"]))
    return kinds


EXPECTED_LOCATOR_KINDS = {"path", "id", "commit", "url", "query_id", "document_span", "capture"}


def run_one_tenant(tools_dir, root, tenant, canary_self, canary_other):
    """The whole lifecycle for one tenant: populate, snapshot, back up,
    destroy, restore into a FRESH location, re-snapshot, and compare. Returns
    (checks, timings, before_after_detail)."""
    checks = []
    live_vault = os.path.join(root, "live", tenant, "vault")
    home = os.path.join(root, "home", tenant)
    os.makedirs(live_vault, exist_ok=True)
    env = make_env(home)

    ids = build_tenant_vault(tools_dir, live_vault, canary_self)
    idx_check = index_vault(tools_dir, live_vault, env)
    checks.append(idx_check)

    mint_checks, dates = populate_governance(tools_dir, live_vault, env, ids)
    checks.extend(mint_checks)
    checks.extend(derive_and_forget(tools_dir, live_vault, env, ids))
    reindex_check = index_vault(tools_dir, live_vault, env)
    checks.append(Check("reindex after derive+forget", reindex_check.passed, reindex_check.detail))

    before_store = store_snapshot(live_vault)
    before_bundle_dir = os.path.join(root, "bundles", tenant, "before")
    rc_b1, bundle_out_before, manifest_before = bundle(tools_dir, live_vault, before_bundle_dir, env)
    checks.append(Check("bundle before destroy", rc_b1 == 0, bundle_out_before.strip()))
    rc_v1, verify_out_before = verify_bundle(tools_dir, before_bundle_dir, env)
    checks.append(Check("verify bundle before destroy", rc_v1 == 0, verify_out_before.strip()))
    locators_before = evidence_locator_kinds(tools_dir, before_bundle_dir)
    checks.append(Check("all 7 evidence locator kinds present before destroy",
                        locators_before == EXPECTED_LOCATOR_KINDS,
                        "found=%s expected=%s" % (sorted(locators_before),
                                                   sorted(EXPECTED_LOCATOR_KINDS))))

    before_behavior = behavioral_snapshot(tools_dir, live_vault, env, ids,
                                          canary_self, canary_other)

    t0 = time.time()
    tar_path = os.path.join(root, "backups", "%s.tar" % tenant)
    os.makedirs(os.path.dirname(tar_path), exist_ok=True)
    with tarfile.open(tar_path, "w") as tf:
        tf.add(live_vault, arcname="vault")
    t_backup = time.time() - t0

    t0 = time.time()
    shutil.rmtree(live_vault)
    shutil.rmtree(os.path.join(home, ".claude"), ignore_errors=True)
    checks.append(Check("working store destroyed", not os.path.isdir(live_vault),
                        "removed %s" % live_vault))
    t_destroy = time.time() - t0

    t0 = time.time()
    restored_vault = os.path.join(root, "restored", tenant, "vault")
    os.makedirs(os.path.dirname(restored_vault), exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        try:
            tf.extractall(os.path.join(root, "restored", tenant), filter="data")
        except TypeError:
            tf.extractall(os.path.join(root, "restored", tenant))  # Python < 3.12
    checks.append(Check("restored into a fresh location", os.path.isdir(restored_vault),
                        restored_vault))
    restored_home = os.path.join(root, "restored-home", tenant)
    restored_env = make_env(restored_home)
    reidx = index_vault(tools_dir, restored_vault, restored_env)
    checks.append(Check("reindex the restored copy from scratch", reidx.passed, reidx.detail))
    t_restore = time.time() - t0

    after_store = store_snapshot(restored_vault)
    for name in before_store:
        b, a = before_store[name], after_store[name]
        checks.append(Check(
            "store %s: rows and hash preserved (before=%d, after=%d)" % (name, b["rows"], a["rows"]),
            b == a, "before=%s after=%s" % (b, a)))

    after_bundle_dir = os.path.join(root, "bundles", tenant, "after")
    rc_b2, bundle_out_after, manifest_after = bundle(tools_dir, restored_vault, after_bundle_dir,
                                                      restored_env)
    checks.append(Check("bundle after restore", rc_b2 == 0, bundle_out_after.strip()))
    rc_v2, verify_out_after = verify_bundle(tools_dir, after_bundle_dir, restored_env)
    checks.append(Check("verify bundle after restore", rc_v2 == 0, verify_out_after.strip()))
    files_match = (manifest_before or {}).get("files") == (manifest_after or {}).get("files")
    counts_match = (manifest_before or {}).get("counts") == (manifest_after or {}).get("counts")
    checks.append(Check("bundle table hashes identical before/after (assertions.jsonl, events.jsonl)",
                        files_match, "before=%s after=%s" %
                        ((manifest_before or {}).get("files"), (manifest_after or {}).get("files"))))
    checks.append(Check("bundle row counts identical before/after", counts_match,
                        "before=%s after=%s" % ((manifest_before or {}).get("counts"),
                                                 (manifest_after or {}).get("counts"))))
    locators_after = evidence_locator_kinds(tools_dir, after_bundle_dir)
    checks.append(Check("all 7 evidence locator kinds present after restore",
                        locators_after == EXPECTED_LOCATOR_KINDS,
                        "found=%s expected=%s" % (sorted(locators_after),
                                                   sorted(EXPECTED_LOCATOR_KINDS))))

    after_behavior = behavioral_snapshot(tools_dir, restored_vault, restored_env, ids,
                                        canary_self, canary_other)

    behavior_keys = ["truth_before_resolution_valid_from", "truth_after_resolution_valid_from",
                      "resolve_before_merge_effective", "resolve_after_merge_effective",
                      "legal_hold_active", "forget_execute_on_held_note_refused",
                      "recall_japanese_found_own_note", "recall_finds_own_canary",
                      "recall_leaks_other_tenant_canary"]
    for key in behavior_keys:
        b, a = before_behavior.get(key), after_behavior.get(key)
        checks.append(Check("behavior %s identical before/after" % key, b == a,
                            "before=%r after=%r" % (b, a)))

    checks.append(Check("legal hold still active after restore",
                        after_behavior["legal_hold_active"] is True,
                        "record_id=%s" % after_behavior.get("legal_hold_record_id")))
    checks.append(Check("forget-execute still refuses the held note after restore",
                        after_behavior["forget_execute_on_held_note_refused"] is True,
                        after_behavior.get("forget_execute_on_held_note_output", "")[:300]))
    checks.append(Check("forgotten note stays forgotten after restore",
                        not os.path.isfile(os.path.join(restored_vault, "forget-note.md")),
                        "forget-note.md must not reappear from the backup"))
    checks.append(Check("Japanese-language recall still finds its own note after restore",
                        after_behavior["recall_japanese_found_own_note"] is True,
                        "query=夜間の復元ドリル"))
    checks.append(Check("recall still finds this tenant's own canary after restore",
                        after_behavior["recall_finds_own_canary"] is True, canary_self))
    checks.append(Check("recall does NOT leak the other tenant's canary after restore",
                        after_behavior["recall_leaks_other_tenant_canary"] is False, canary_other))

    timings = {"backup_seconds": round(t_backup, 3), "destroy_seconds": round(t_destroy, 3),
               "restore_and_reindex_seconds": round(t_restore, 3)}
    detail = {
        "before_store": before_store, "after_store": after_store,
        "manifest_before": manifest_before, "manifest_after": manifest_after,
        "locators_before": sorted(locators_before), "locators_after": sorted(locators_after),
        "before_behavior": before_behavior, "after_behavior": after_behavior,
    }
    return checks, timings, detail


def main(argv=None):
    # --help must print usage and exit 0 without running the drill (two temp
    # tenants, about 7 seconds). The drill takes no arguments.
    parser = argparse.ArgumentParser(
        prog="restore_drill_enterprise.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    tools_dir, err = find_tools_dir()
    if err:
        print(err, file=sys.stderr)
        return 2

    root = tempfile.mkdtemp(prefix="restore-drill-ent-")
    tenants = [
        ("tenant-alpha", "CANARY-ALPHA-restore-drill-2026", "CANARY-BETA-restore-drill-2026"),
        ("tenant-beta", "CANARY-BETA-restore-drill-2026", "CANARY-ALPHA-restore-drill-2026"),
    ]

    all_checks = []
    per_tenant = {}
    timings = {}
    t_start = time.time()
    for tenant, canary_self, canary_other in tenants:
        checks, tmg, detail = run_one_tenant(tools_dir, root, tenant, canary_self, canary_other)
        for c in checks:
            entry = c.as_dict()
            entry["tenant"] = tenant
            all_checks.append(entry)
        per_tenant[tenant] = detail
        timings[tenant] = tmg
    total_wall_seconds = round(time.time() - t_start, 3)

    failed = [c for c in all_checks if not c["passed"]]
    passed = len(failed) == 0

    result = {
        "drill": "restore_drill_enterprise",
        "drill_date": datetime.date.today().isoformat(),
        "commit": current_commit(REPO_ROOT),
        "covered": covered_files(tools_dir),
        "tools_dir": repo_relative_tools_dir(tools_dir),
        "tenants": [t[0] for t in tenants],
        "passed": passed,
        "checks_total": len(all_checks),
        "checks_failed": len(failed),
        "checks": all_checks,
        "unvalidated_categories": sorted({c["name"] for c in failed}),
        "backup_mechanism": {
            "full_tree": "tarfile of the vault directory (stdlib), the only mechanism in "
                         "this tools tree that captures 99-System/assertions.jsonl, "
                         "resolutions.jsonl, legal_holds.jsonl and the notes themselves",
            "export_api": "bm_vault_export.py bundle/verify, the real export API named by "
                          "the task; covers the claim-derived assertions.jsonl + events.jsonl "
                          "layer only, by that module's own documented design",
        },
        "tenancy_model": "two independent vault roots, each indexed under its own HOME "
                        "(bm_vault_identity.py's own documented seam: full vault-root "
                        "isolation via HOME, no per-note tenant column)",
        "time_to_restore_seconds": timings,
        "wall_total_seconds": total_wall_seconds,
        "manual_steps_required": 0,
        "per_tenant_detail": per_tenant,
        "scratch": "temporary directory, not kept",
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    if failed:
        print("\nFAILED (%d of %d checks):" % (len(failed), len(all_checks)), file=sys.stderr)
        for c in failed:
            print("  [%s] %s :: %s" % (c["tenant"], c["name"], c["detail"][:300]), file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

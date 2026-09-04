#!/usr/bin/env python3
"""test_clean_env_restore: V4 of docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md.

WHY THIS EXISTS. The ops persona's disaster-recovery finding: DR against host
loss is unproven, because restore_drill_enterprise.py's own drill restores
into a subdirectory of the SAME tempfile.mkdtemp() root the source lived in,
inheriting the parent process's PATH and most of its environment. That proves
a restore into a fresh HOME works; it says nothing about whether the restore
secretly leans on something else still sitting on the source host.

WHAT THIS ADDS, on top of (never instead of) restore_drill_enterprise.py's
own populate/backup/destroy/restore/validate structure, reused here by
import rather than rewritten:
  1. The source and target each get their OWN top-level root, fresh HOME,
     fresh TMPDIR, and a PATH trimmed to just the python interpreter's own
     directory plus /usr/bin:/bin -- no inherited BM_*/BROTHERMODE_*/SBE_*
     pointer, no shared cache directory, no PATH entry naming this
     repository's checkout or the source root.
  2. THE ISOLATION PROOF the task asked for by name: after the backup tar is
     written, the WHOLE source root (vault, HOME, tempdir) is MOVED away to a
     quarantine path, not deleted, before the restore starts. A restore that
     secretly depends on the source now hits a loud, mechanical failure
     (ENOENT, a wrong answer, or the moved-away path surfacing in a tool's
     own output) instead of quietly reading real content. Every check's
     output is scanned for that path after the fact as a second, independent
     catch.
  3. RTO: wall-clock seconds from the moment extraction starts to the moment
     the restored copy answers ONE real governance query (the temporal
     truth-as-of read across mint-assertion + mint-resolution) correctly.
     manual_steps_required is counted, not assumed: every step in this drill
     runs unattended, so it is 0.
  4. The same governed surfaces restore_drill_enterprise.py validates -- note
     and store counts/hashes, the export bundle, all 7 evidence locator
     kinds, the temporal truth answer on both sides of the resolution's
     valid_from, the identity merge on both sides of its effective date, the
     legal hold, forget-execute's refusal on a held note, Japanese-language
     recall, and cross-tenant isolation (this tenant's canary found, the
     other tenant's canary not leaked) -- re-run here inside the clean
     environment, not assumed to carry over from the other drill.

HONESTY, stated once and not walked back anywhere in the JSON output: this
proves restore into a clean ENVIRONMENT on the SAME physical host. A true
second physical machine is not available tonight (no such hardware on this
estate) and is named in the result's own "limit" field as the next, unbuilt
step -- see docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md V4. Nothing here
claims cross-host proof.

passed is true only if every check passes AND environment isolation holds
for every tenant; a broken isolation names exactly what output referenced
the moved-away source.

Resolves the vault tools directory the same way restore_drill_enterprise.py
does ($BROTHERMODEUP_TOOLS, else /tmp/bmu-main/tools). Exit 0 passed, 1 not
passed (named gaps), 2 NO-DATA (tools directory missing).

No em or en dashes anywhere in this file.
"""
import datetime
import json
import os
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import restore_drill_enterprise as rde  # noqa: E402  (reused, not rewritten)

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

REPO_ROOT = os.path.dirname(HERE)


def make_clean_env(home_dir, tmp_dir):
    """A from-scratch environment: only PATH, HOME, TMPDIR and locale, none
    of them inherited from this process. No BM_*/BROTHERMODE_*/SBE_* pointer
    survives because none is copied in the first place, and PATH names only
    the python interpreter's own directory plus /usr/bin:/bin -- never this
    repository's checkout or any source-side path."""
    python_dir = os.path.dirname(os.path.realpath(sys.executable))
    env = {
        "HOME": home_dir,
        "TMPDIR": tmp_dir,
        "PATH": os.pathsep.join([python_dir, "/usr/bin", "/bin"]),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
    }
    os.makedirs(os.path.join(home_dir, ".claude"), exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    return env


BEHAVIOR_KEYS = [
    "truth_before_resolution_valid_from", "truth_after_resolution_valid_from",
    "resolve_before_merge_effective", "resolve_after_merge_effective",
    "legal_hold_active", "forget_execute_on_held_note_refused",
    "recall_japanese_found_own_note", "recall_finds_own_canary",
    "recall_leaks_other_tenant_canary",
]


def clean_env_tenant_drill(tools_dir, tenant, canary_self, canary_other):
    """Full lifecycle for one tenant, source and target on physically
    separate, environment-isolated roots. Returns (checks, detail) where
    detail carries rto_seconds, manual_steps_required and the isolation
    verdict this drill exists to produce."""
    checks = []
    isolation_findings = []

    # SOURCE: its own root, its own HOME, its own TMPDIR.
    source_root = tempfile.mkdtemp(prefix="clean-env-src-%s-" % tenant)
    source_home = os.path.join(source_root, "home")
    source_tmp = os.path.join(source_root, "tmp")
    source_env = make_clean_env(source_home, source_tmp)
    live_vault = os.path.join(source_root, "vault")
    os.makedirs(live_vault, exist_ok=True)

    ids = rde.build_tenant_vault(tools_dir, live_vault, canary_self)
    checks.append(rde.index_vault(tools_dir, live_vault, source_env))
    mint_checks, _dates = rde.populate_governance(tools_dir, live_vault, source_env, ids)
    checks.extend(mint_checks)
    checks.extend(rde.derive_and_forget(tools_dir, live_vault, source_env, ids))
    reindex = rde.index_vault(tools_dir, live_vault, source_env)
    checks.append(rde.Check("reindex after derive+forget", reindex.passed, reindex.detail))

    before_store = rde.store_snapshot(live_vault)
    before_bundle_dir = os.path.join(source_root, "bundle-before")
    rc_b1, bundle_out_before, manifest_before = rde.bundle(
        tools_dir, live_vault, before_bundle_dir, source_env)
    checks.append(rde.Check("bundle before destroy", rc_b1 == 0, bundle_out_before.strip()))
    rc_v1, verify_out_before = rde.verify_bundle(tools_dir, before_bundle_dir, source_env)
    checks.append(rde.Check("verify bundle before destroy", rc_v1 == 0, verify_out_before.strip()))
    locators_before = rde.evidence_locator_kinds(tools_dir, before_bundle_dir)

    before_behavior = rde.behavioral_snapshot(tools_dir, live_vault, source_env, ids,
                                               canary_self, canary_other)

    # BACKUP: same tar mechanism restore_drill_enterprise.py uses, written
    # OUTSIDE source_root so it survives the move below.
    backup_holder = tempfile.mkdtemp(prefix="clean-env-backup-%s-" % tenant)
    tar_path = os.path.join(backup_holder, "vault.tar")
    with tarfile.open(tar_path, "w") as tf:
        tf.add(live_vault, arcname="vault")

    # THE ISOLATION PROOF: move (never delete) the WHOLE source root away.
    # Deleting would also make an accidental read fail, but it destroys the
    # evidence of WHAT was depended on; moving lets a failure name the exact
    # path that was still being reached for.
    quarantine_root = tempfile.mkdtemp(prefix="clean-env-quarantine-%s-" % tenant)
    moved_source = os.path.join(quarantine_root, "moved-source")
    os.rename(source_root, moved_source)
    source_gone = not os.path.isdir(source_root)
    checks.append(rde.Check("source root moved away before restore", source_gone,
                            "was %s, now %s" % (source_root, moved_source)))

    # TARGET: an unrelated root, fresh HOME, fresh TMPDIR, PATH naming no
    # source-side or repo-side directory.
    target_root = tempfile.mkdtemp(prefix="clean-env-target-%s-" % tenant)
    target_home = os.path.join(target_root, "home")
    target_tmp = os.path.join(target_root, "tmp")
    target_env = make_clean_env(target_home, target_tmp)
    restored_vault = os.path.join(target_root, "vault")

    checks.append(rde.Check(
        "target HOME is a fresh directory unrelated to source HOME",
        target_home != source_home and not target_home.startswith(source_root),
        "%s vs %s" % (target_home, source_home)))
    checks.append(rde.Check(
        "target TMPDIR is a fresh directory unrelated to source TMPDIR",
        target_tmp != source_tmp and not target_tmp.startswith(source_root),
        "%s vs %s" % (target_tmp, source_tmp)))
    path_entries = target_env["PATH"].split(os.pathsep)
    path_clean = not any(
        p == REPO_ROOT or p.startswith(source_root) or p.startswith(moved_source)
        for p in path_entries)
    checks.append(rde.Check("target PATH names no source-tree or repo directory",
                            path_clean, target_env["PATH"]))

    def tainted(text):
        """The mechanical "it read the moved-away source" signal: the
        source's original path, its HOME, or its quarantine path showing up
        anywhere in a tool's own output."""
        text = text or ""
        return source_root in text or moved_source in text or source_home in text

    rto_start = time.time()
    manual_steps_required = 0  # every step here runs unattended; counted, not assumed

    # Everything from here on runs against restored_vault/target_env only.
    # The taint scan below covers just this slice: the checks appended
    # BEFORE this marker legitimately name source_root/source_home (the
    # move-away audit trail and the pre-move source-side mint/legal-hold
    # commands), which is expected diagnostic text, not a leak.
    post_restore_marker = len(checks)

    with tarfile.open(tar_path, "r") as tf:
        try:
            tf.extractall(target_root, filter="data")
        except TypeError:
            tf.extractall(target_root)  # Python < 3.12
    checks.append(rde.Check("restored into a fresh, unrelated environment",
                            os.path.isdir(restored_vault), restored_vault))

    reidx = rde.index_vault(tools_dir, restored_vault, target_env)
    checks.append(rde.Check("reindex the restored copy from scratch", reidx.passed, reidx.detail))
    if tainted(reidx.detail):
        isolation_findings.append("reindex output referenced the moved-away source: %s"
                                   % reidx.detail[:200])

    # RTO GATE: the first real governance answer the restored copy gives.
    rc_truth, truth_after = rde.run(
        tools_dir, "bm_vault_assertions.py",
        ["truth", "--vault", restored_vault, "--subject", ids["id_a"], "--predicate", "status",
         "--scope", "global", "--as-of", "2026-07-01"], target_env)
    truth_correct = (rc_truth == before_behavior["truth_exit_codes"][1]
                      and truth_after == before_behavior["truth_after_resolution_valid_from"])
    if tainted(truth_after):
        isolation_findings.append("truth query output referenced the moved-away source: %s"
                                   % truth_after[:200])
    checks.append(rde.Check("restored copy answers a governance query correctly (RTO gate)",
                            truth_correct, truth_after.strip()))
    rto_seconds = round(time.time() - rto_start, 3)

    # Full validation battery, same surfaces restore_drill_enterprise.py
    # checks, re-run here inside the clean environment.
    after_store = rde.store_snapshot(restored_vault)
    for name in before_store:
        b, a = before_store[name], after_store[name]
        checks.append(rde.Check(
            "store %s: rows and hash preserved (before=%d, after=%d)" % (name, b["rows"], a["rows"]),
            b == a, "before=%s after=%s" % (b, a)))

    after_bundle_dir = os.path.join(target_root, "bundle-after")
    rc_b2, bundle_out_after, manifest_after = rde.bundle(
        tools_dir, restored_vault, after_bundle_dir, target_env)
    checks.append(rde.Check("bundle after restore", rc_b2 == 0, bundle_out_after.strip()))
    rc_v2, verify_out_after = rde.verify_bundle(tools_dir, after_bundle_dir, target_env)
    checks.append(rde.Check("verify bundle after restore", rc_v2 == 0, verify_out_after.strip()))
    files_match = (manifest_before or {}).get("files") == (manifest_after or {}).get("files")
    counts_match = (manifest_before or {}).get("counts") == (manifest_after or {}).get("counts")
    checks.append(rde.Check("bundle table hashes identical before/after", files_match,
                            "before=%s after=%s" % ((manifest_before or {}).get("files"),
                                                     (manifest_after or {}).get("files"))))
    checks.append(rde.Check("bundle row counts identical before/after", counts_match,
                            "before=%s after=%s" % ((manifest_before or {}).get("counts"),
                                                     (manifest_after or {}).get("counts"))))

    locators_after = rde.evidence_locator_kinds(tools_dir, after_bundle_dir)
    checks.append(rde.Check("all 7 evidence locator kinds present after restore",
                            locators_after == rde.EXPECTED_LOCATOR_KINDS,
                            "found=%s expected=%s" % (sorted(locators_after),
                                                       sorted(rde.EXPECTED_LOCATOR_KINDS))))

    after_behavior = rde.behavioral_snapshot(tools_dir, restored_vault, target_env, ids,
                                              canary_self, canary_other)
    for key in BEHAVIOR_KEYS:
        b, a = before_behavior.get(key), after_behavior.get(key)
        checks.append(rde.Check("behavior %s identical before/after" % key, b == a,
                                "before=%r after=%r" % (b, a)))

    checks.append(rde.Check("legal hold still active after restore",
                            after_behavior["legal_hold_active"] is True,
                            "record_id=%s" % after_behavior.get("legal_hold_record_id")))
    checks.append(rde.Check("forget-execute still refuses the held note after restore",
                            after_behavior["forget_execute_on_held_note_refused"] is True,
                            after_behavior.get("forget_execute_on_held_note_output", "")[:300]))
    checks.append(rde.Check("forgotten note stays forgotten after restore",
                            not os.path.isfile(os.path.join(restored_vault, "forget-note.md")),
                            "forget-note.md must not reappear from the backup"))
    checks.append(rde.Check("Japanese-language recall still finds its own note after restore",
                            after_behavior["recall_japanese_found_own_note"] is True,
                            "query=夜間の復元ドリル"))
    checks.append(rde.Check("recall still finds this tenant's own canary after restore",
                            after_behavior["recall_finds_own_canary"] is True, canary_self))
    checks.append(rde.Check("recall does NOT leak the other tenant's canary after restore",
                            after_behavior["recall_leaks_other_tenant_canary"] is False, canary_other))

    # Second, independent scan: every POST-RESTORE check's own detail text
    # (checks[post_restore_marker:], i.e. everything run against
    # restored_vault/target_env), for a leak the two inline spot-checks
    # above did not catch. Checks before the marker are excluded on
    # purpose: they legitimately name source_root/source_home (the
    # move-away audit trail, and the pre-move mint/legal-hold commands
    # that ran while the source still existed).
    for c in checks[post_restore_marker:]:
        if tainted(c.detail):
            isolation_findings.append("%s output referenced the moved-away source" % c.name)

    isolation_holds = source_gone and path_clean and len(isolation_findings) == 0
    checks.append(rde.Check(
        "environment isolation: source moved away, restore never referenced it",
        isolation_holds, "; ".join(isolation_findings) or "clean"))

    detail = {
        "rto_seconds": rto_seconds,
        "manual_steps_required": manual_steps_required,
        "isolation_holds": isolation_holds,
        "isolation_findings": isolation_findings,
        "source_root_moved_to": moved_source,
        "target_root": target_root,
        "before_store": before_store, "after_store": after_store,
        "locators_before": sorted(locators_before), "locators_after": sorted(locators_after),
        "before_behavior": before_behavior, "after_behavior": after_behavior,
    }
    return checks, detail


def main():
    tools_dir, err = rde.find_tools_dir()
    if err:
        print(err, file=sys.stderr)
        return 2

    tenants = [
        ("tenant-alpha", "CANARY-CLEANENV-ALPHA-restore-2026", "CANARY-CLEANENV-BETA-restore-2026"),
        ("tenant-beta", "CANARY-CLEANENV-BETA-restore-2026", "CANARY-CLEANENV-ALPHA-restore-2026"),
    ]

    all_checks = []
    per_tenant = {}
    t_start = time.time()
    for tenant, canary_self, canary_other in tenants:
        checks, detail = clean_env_tenant_drill(tools_dir, tenant, canary_self, canary_other)
        for c in checks:
            entry = c.as_dict()
            entry["tenant"] = tenant
            all_checks.append(entry)
        per_tenant[tenant] = detail
    wall_total_seconds = round(time.time() - t_start, 3)

    failed = [c for c in all_checks if not c["passed"]]
    isolation_holds_all = all(per_tenant[t]["isolation_holds"] for t in per_tenant)
    passed = (len(failed) == 0) and isolation_holds_all

    result = {
        "drill": "test_clean_env_restore",
        "drill_date": datetime.date.today().isoformat(),
        "tools_dir": tools_dir,
        "tenants": [t[0] for t in tenants],
        "passed": passed,
        "checks_total": len(all_checks),
        "checks_failed": len(failed),
        "checks": all_checks,
        "environment_isolation": {
            t: {
                "isolation_holds": per_tenant[t]["isolation_holds"],
                "isolation_findings": per_tenant[t]["isolation_findings"],
                "source_root_moved_to": per_tenant[t]["source_root_moved_to"],
            } for t in per_tenant
        },
        "rto_seconds": {t: per_tenant[t]["rto_seconds"] for t in per_tenant},
        "manual_steps_required": {t: per_tenant[t]["manual_steps_required"] for t in per_tenant},
        "wall_total_seconds": wall_total_seconds,
        "limit": ("proves restore into a clean ENVIRONMENT (fresh HOME, fresh TMPDIR, a PATH "
                  "trimmed off the source tree, the source vault moved away before restore) "
                  "on ONE physical host; a true second physical machine is not available "
                  "tonight and stays named as the next, unbuilt step, per "
                  "docs/plan/VAULT-HARDENING-SCOPE-2026-08-31.md V4"),
        "per_tenant_detail": per_tenant,
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    if failed:
        print("\nFAILED (%d of %d checks):" % (len(failed), len(all_checks)), file=sys.stderr)
        for c in failed:
            print("  [%s] %s :: %s" % (c["tenant"], c["name"], c["detail"][:300]), file=sys.stderr)
    if not isolation_holds_all:
        print("\nISOLATION BROKEN:", file=sys.stderr)
        for t in per_tenant:
            if not per_tenant[t]["isolation_holds"]:
                print("  [%s] %s" % (t, per_tenant[t]["isolation_findings"]), file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

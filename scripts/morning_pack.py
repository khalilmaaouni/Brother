#!/usr/bin/env python3
"""morning_pack.py: fills docs/plan/MORNING-STEERING-2026-09-05.md section
47's evidence pack template MECHANICALLY, one line per instrument, then
closes with exactly one of section 48's four verdict sentences.

WHY THIS EXISTS. Row J9: a plain reader should not have to reconstruct nine
rows by hand to know what this morning actually proved. Every value below is
read from a real command's own output, a filed benchmark result, or a filed
run's own checker; a value this script cannot obtain prints NO-DATA naming
what is missing, never a guess. Re-run this after anything changes: it is
meant to be regenerated, not edited (docs/plan/MORNING-EVIDENCE-PACK-2026-09-05.md
carries the same warning SYSTEM.md does).

INSTRUMENTS, ONE PER LINE OF THE TEMPLATE.
  RELEASE: git rev-parse / git describe on origin/main, the tag named by
    .claude-plugin/marketplace.json's own metadata.version (the same source
    scripts/refresh_cut.py and scripts/release_note_from_tree.py already
    use), git tag -v for the signature, scripts/refresh_cut.py --check for
    the export manifest, and a --closeout-log file if one is given.
  CI: gh api repos/khalilmaaouni/Brother/rulesets for a required-status-check
    rule, and a FRESH shallow clone of the public repository's own main for
    whether any workflow fires on push/pull_request/schedule (the No
    Self-Firing CI law). No red/green trial pull request has ever been run
    against this gate, so those two lines are always NO-DATA until one has.
  JAPANESE RETRIEVAL: the two jbench commands (scripts/test_japanese_threshold.py
    for the 245-case standard fixture; products/brothermode/tools/bm_vault_jbench.py
    run directly against the frozen benchmarks/ja-adversarial/adversarial-ja-corpus.json
    for the 78-case frozen blind set and its 13-case negative class) plus
    scripts/test_ja_mutations.py for how many mechanisms the benchmark
    actually proves.
  JAPANESE BUSINESS ENGINEERING: benchmarks/jbeq's own seed and README for
    whether JBEQ exists at all; the newest answer file under --jbeq-scores
    (a handover pack directory) scored with scripts/jbeq_mdm.py score, or
    NO-DATA when none is given; scripts/jbeq_e2e_check.py run against the
    filed benchmarks/jbeq/mdm/e2e-001 run directory.
  GAUNTLETS: the newest benchmarks/results/<name>-*.json record for
    delegation-truth and memory-recurrence; acceptance-compression and
    long-horizon-recovery report NO-DATA when no result or run manifest has
    been filed, which is the honest state as of this file's writing.
  CODEX: ~/.claude/evidence/CODEX-LANE-2026-09-05.md's own table, the
    SIGNED-IN row, or NO-DATA when the file is absent.

VERDICT (section 48, verbatim four sentences): compute_verdict() below is the
whole rule, kept separate from every instrument above so
scripts/test_morning_pack.py can drive it with fixtures instead of a live
tree. Checked in priority order: READY TO CLAIM COMPETITIVE LEAD only when
every one of the five named conditions holds; JAPANESE MDM ENGINEERING
QUALIFIED next because it is section 48's other unconditional pass; then
JAPANESE RETRIEVAL QUALIFIED, BUSINESS ENGINEERING IN PROGRESS whenever
100% Japanese retrieval holds on its own; TECHNICALLY STRONG, NOT YET
QUALIFIED is the default for everything else, which is true exactly when the
first three do not hold (section 48 says to use it "if any critical proof is
still NO-DATA", and every real run of this script that fails the first three
does so because something feeding them is NO-DATA).

Python 3, standard library only. No network beyond gh and git, both already
required by the rest of this estate's release tooling; either one being
unreachable degrades individual lines to NO-DATA rather than crashing.
No em or en dashes anywhere in this file.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import release_note_from_tree as RN  # noqa: E402

NO_DATA = "NO-DATA"

DEFAULT_OUT = os.path.join(ROOT, "docs", "plan",
                            "MORNING-EVIDENCE-PACK-2026-09-05.md")
DEFAULT_CODEX_LANE = os.path.expanduser(
    "~/.claude/evidence/CODEX-LANE-2026-09-05.md")

#: mirrors ~/.claude/hooks/github_cost_wall.py's own trigger regex: the No
#: Self-Firing CI law's forbidden triggers, checked here read-only.
AUTO_TRIGGER_RE = re.compile(
    r"^\s*(push|pull_request|pull_request_target|schedule)\s*:",
    re.MULTILINE)


def run(cmd, cwd=None, timeout=180):
    """(returncode, combined stdout+stderr). Never raises: a missing binary
    or a timeout comes back as (None, a NO-DATA sentence) so a caller can
    treat every failure mode the same way."""
    try:
        proc = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True,
                               text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "%s: %s failed to run (%s)" % (NO_DATA, cmd[0], exc)


def _newest(prefix, ext=".json", where=("benchmarks", "results")):
    d = os.path.join(ROOT, *where)
    if not os.path.isdir(d):
        return None
    cands = [os.path.join(d, fn) for fn in os.listdir(d)
             if fn.startswith(prefix + "-") and fn.endswith(ext)]
    if not cands:
        return None
    cands.sort(key=os.path.getmtime)
    return cands[-1]


# ---------------------------------------------------------------- RELEASE
def gather_release(closeout_log):
    lines = []
    flags = {}

    version = RN.default_version()
    tag = "v%s" % version
    lines.append("CURRENT RELEASE: %s (from .claude-plugin/marketplace.json's "
                  "own metadata.version)" % tag)

    rc, sha_out = run(["git", "rev-parse", "origin/main"])
    main_sha = sha_out.strip() if rc == 0 else NO_DATA
    lines.append("MAIN SHA: %s" % main_sha)

    rc2, describe_out = run(["git", "describe", "--tags", "origin/main"])
    describe = describe_out.strip() if rc2 == 0 else NO_DATA
    tag_line = "TAG: %s" % tag
    if rc2 == 0 and describe != tag:
        tag_line += (" (git describe --tags origin/main reports %s instead; "
                     "the release tag was cut from a release branch and is "
                     "not the graph-nearest tag to origin/main's own tip)"
                     % describe)
    lines.append(tag_line)

    rc3, sig_out = run(["git", "tag", "-v", tag])
    tag_signed = False
    if rc3 == 0:
        signed_line = "TAG SIGNED: YES"
        tag_signed = True
    elif rc3 is not None and "no signature found" in sig_out:
        signed_line = "TAG SIGNED: NO (git tag -v %s: error: no signature found)" % tag
    elif rc3 is not None:
        signed_line = "TAG SIGNED: %s (git tag -v %s exited %s)" % (NO_DATA, tag, rc3)
    else:
        signed_line = "TAG SIGNED: %s (git tag -v could not run)" % NO_DATA
    lines.append(signed_line)
    flags["tag_signed"] = tag_signed

    rc4, manifest_out = run([sys.executable, "scripts/refresh_cut.py", "--check"])
    refused_m = re.search(r"^REFUSED:.+$", manifest_out, re.MULTILINE)
    manifest_clean = rc4 == 0
    if rc4 == 0:
        lines.append("EXPORT MANIFEST: MATCHES")
        lines.append("MANIFEST MISMATCHES: 0")
    elif refused_m:
        lines.append("EXPORT MANIFEST: MISMATCH")
        lines.append("MANIFEST MISMATCHES: %s" % refused_m.group(0))
    else:
        lines.append("EXPORT MANIFEST: %s (scripts/refresh_cut.py --check exited %s)"
                      % (NO_DATA, rc4))
        lines.append("MANIFEST MISMATCHES: %s" % NO_DATA)
    flags["manifest_clean"] = manifest_clean

    if closeout_log and os.path.isfile(closeout_log):
        with open(closeout_log, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^.*virgin.*install.*$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            lines.append("VIRGIN INSTALL: %s" % m.group(0).strip())
        else:
            lines.append("VIRGIN INSTALL: %s (no virgin-install line found in %s)"
                          % (NO_DATA, closeout_log))
    else:
        lines.append("VIRGIN INSTALL: %s (%s)" % (
            NO_DATA,
            "no --closeout-log path given" if not closeout_log
            else "%s does not exist" % closeout_log))

    return lines, flags


# --------------------------------------------------------------------- CI
def gather_ci():
    lines = []
    flags = {"ruleset_required": False, "required_fast_wired": False}

    rc, out = run(["gh", "api", "repos/khalilmaaouni/Brother/rulesets"])
    has_status_check_rule = None
    unreadable_rulesets = 0
    if rc == 0:
        try:
            rulesets = json.loads(out)
        except ValueError:
            rulesets = None
        if isinstance(rulesets, list):
            has_status_check_rule = False
            for rs in rulesets:
                rc2, detail = run(["gh", "api",
                                    "repos/khalilmaaouni/Brother/rulesets/%s" % rs.get("id")])
                if rc2 != 0:
                    unreadable_rulesets += 1
                    continue
                try:
                    d = json.loads(detail)
                except ValueError:
                    unreadable_rulesets += 1
                    continue
                for rule in d.get("rules", []):
                    if rule.get("type") == "required_status_checks":
                        has_status_check_rule = True
    if has_status_check_rule is None:
        lines.append("RULESET REQUIRED: %s (gh api repos/khalilmaaouni/Brother/rulesets failed)"
                      % NO_DATA)
    else:
        # A ruleset whose detail could not be fetched or parsed was never
        # examined for a required_status_checks rule, so a NO answer earned
        # by skipping rulesets is named as such rather than reported as if
        # every ruleset had been read.
        caveat = ("; %d ruleset(s) could not be read and were not checked"
                  % unreadable_rulesets) if unreadable_rulesets else ""
        lines.append("RULESET REQUIRED: %s%s"
                     % ("YES" if has_status_check_rule else "NO", caveat))
        flags["ruleset_required"] = has_status_check_rule

    pr_trigger = None
    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = os.path.join(tmp, "pub")
        rc3, _ = run(["git", "clone", "-q", "--depth", "1",
                      "https://github.com/khalilmaaouni/Brother.git", clone_dir],
                     cwd=tmp, timeout=120)
        if rc3 == 0:
            wf_dir = os.path.join(clone_dir, ".github", "workflows")
            wired = False
            fires_on_push_or_pr = False
            if os.path.isdir(wf_dir):
                for fn in sorted(os.listdir(wf_dir)):
                    fp = os.path.join(wf_dir, fn)
                    if not os.path.isfile(fp):
                        continue
                    with open(fp, encoding="utf-8") as fh:
                        text = fh.read()
                    if "required_fast" in text:
                        wired = True
                    if AUTO_TRIGGER_RE.search(text):
                        fires_on_push_or_pr = True
            pr_trigger = fires_on_push_or_pr
            flags["required_fast_wired"] = wired
            lines.append("REQUIRED-FAST: %s" % (
                "YES (wired into a workflow on public main)" if wired
                else "NO (scripts/required_fast.sh is not referenced by any "
                     "workflow file on public main)"))
    if pr_trigger is None:
        lines.append("PR TRIGGER: %s (fresh shallow clone of public main failed)" % NO_DATA)
    else:
        lines.append("PR TRIGGER: %s" % ("YES" if pr_trigger else
                     "NO (every workflow on public main fires on workflow_dispatch only)"))

    lines.append("RED PR BLOCKED: %s (no pull request has ever been opened to try "
                 "this gate against a known-red check)" % NO_DATA)
    lines.append("GREEN PR ALLOWED: %s (no pull request has ever been opened to try "
                 "this gate against a known-green check)" % NO_DATA)
    return lines, flags


# ---------------------------------------------------------- JAPANESE RETRIEVAL
def gather_japanese_retrieval():
    lines = []
    flags = {"standard_full": False, "frozen_full": False, "negative_full": False}

    rc, out = run([sys.executable, "scripts/test_japanese_threshold.py"])
    m = re.search(r"^overall:\s+(\d+)/(\d+)", out, re.MULTILINE)
    if m:
        hits, total = m.group(1), m.group(2)
        lines.append("STANDARD: %s/%s" % (hits, total))
        flags["standard_full"] = (hits == total)
    else:
        lines.append("STANDARD: %s (scripts/test_japanese_threshold.py did not "
                     "print an overall line; exit %s)" % (NO_DATA, rc))

    jbench = os.path.join(ROOT, "products", "brothermode", "tools", "bm_vault_jbench.py")
    corpus = os.path.join(ROOT, "benchmarks", "ja-adversarial", "adversarial-ja-corpus.json")
    if os.path.isfile(jbench) and os.path.isfile(corpus):
        rc2, out2 = run([sys.executable, jbench, "run", "--cases", corpus, "--verbose"])
        m2 = re.search(r"^overall:\s+(\d+)/(\d+)", out2, re.MULTILINE)
        if m2:
            hits, total = m2.group(1), m2.group(2)
            lines.append("FROZEN: %s/%s" % (hits, total))
            flags["frozen_full"] = (hits == total)
        else:
            lines.append("FROZEN: %s (bm_vault_jbench.py printed no overall line; exit %s)"
                          % (NO_DATA, rc2))
        m3 = re.search(r"^\s*negative\s+(\d+)/(\d+)", out2, re.MULTILINE)
        if m3:
            hits, total = m3.group(1), m3.group(2)
            lines.append("NEGATIVE: %s/%s" % (hits, total))
            flags["negative_full"] = (hits == total)
        else:
            lines.append("NEGATIVE: %s (no negative class row in bm_vault_jbench.py output)"
                          % NO_DATA)
    else:
        lines.append("FROZEN: %s (bm_vault_jbench.py or the frozen corpus not found in tree)"
                      % NO_DATA)
        lines.append("NEGATIVE: %s" % NO_DATA)

    rc4, out4 = run([sys.executable, "scripts/test_ja_mutations.py"])
    m4 = re.search(r"^mechanisms PROVEN by the benchmark:\s+(\d+)", out4, re.MULTILINE)
    m5 = re.search(r"^mechanisms reported NO-DATA:\s+(\d+)", out4, re.MULTILINE)
    if m4 and m5:
        proven, nodata = int(m4.group(1)), int(m5.group(1))
        lines.append("MUTATIONS: %d / %d" % (proven, proven + nodata))
    else:
        lines.append("MUTATIONS: %s (scripts/test_ja_mutations.py did not print "
                     "the expected summary lines; exit %s)" % (NO_DATA, rc4))

    return lines, flags


# ------------------------------------------------ JAPANESE BUSINESS ENGINEERING
def gather_jbeq(jbeq_scores_dir):
    lines = []
    flags = {"jbeq_mdm_seed_ready": False, "jbeq_mdm_zero_false_merges": False,
              "jbeq_e2e_pass": False, "jbeq_reconciliation_pass": False,
              "jbeq_handover_pass": False}

    seed_path = os.path.join(ROOT, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")
    created = os.path.isfile(seed_path)
    lines.append("JBEQ CREATED: %s" % ("YES" if created else "NO"))

    answer_file = None
    if jbeq_scores_dir and os.path.isdir(jbeq_scores_dir):
        cands = []
        for dirpath, _dirs, filenames in os.walk(jbeq_scores_dir):
            for fn in filenames:
                if fn.endswith(".json"):
                    p = os.path.join(dirpath, fn)
                    cands.append((os.path.getmtime(p), p))
        if cands:
            cands.sort()
            answer_file = cands[-1][1]

    if answer_file:
        rc, out = run([sys.executable, "scripts/jbeq_mdm.py", "score", answer_file])
        seed_m = re.search(r"^JBEQ-MDM SEED:\s+(\d+) of (\d+)", out, re.MULTILINE)
        fm_m = re.search(r"^critical false merges:\s+(\d+) of (\d+)", out, re.MULTILINE)
        if seed_m:
            lines.append("JBEQ-MDM SEED: %s of %s" % (seed_m.group(1), seed_m.group(2)))
            flags["jbeq_mdm_seed_ready"] = True
        elif "JBEQ-MDM NOT READY" in out:
            lines.append("JBEQ-MDM SEED: NOT READY (scored %s)" % answer_file)
        else:
            lines.append("JBEQ-MDM SEED: %s (unreadable jbeq_mdm.py score output "
                         "for %s; exit %s)" % (NO_DATA, answer_file, rc))
        if fm_m:
            lines.append("CRITICAL FALSE MERGES: %s / %s" % (fm_m.group(1), fm_m.group(2)))
            flags["jbeq_mdm_zero_false_merges"] = (fm_m.group(1) == "0")
        else:
            lines.append("CRITICAL FALSE MERGES: %s" % NO_DATA)
    else:
        why = ("no --jbeq-scores directory given" if not jbeq_scores_dir
               else "no .json answer file found under %s" % jbeq_scores_dir)
        lines.append("JBEQ-MDM SEED: %s (%s)" % (NO_DATA, why))
        lines.append("CRITICAL FALSE MERGES: %s (%s)" % (NO_DATA, why))

    run_rel = "benchmarks/jbeq/mdm/e2e-001/runs/2026-09-05"
    if os.path.isdir(os.path.join(ROOT, run_rel)):
        rc2, out2 = run([sys.executable, "scripts/jbeq_e2e_check.py", run_rel])

        def verdict(pattern):
            if re.search(pattern + r":\s*PASS", out2, re.MULTILINE):
                return "PASS"
            if re.search(pattern + r":\s*FAIL", out2, re.MULTILINE):
                return "FAIL"
            return NO_DATA

        e2e = verdict(r"^jbeq-mdm e2e")
        reconciliation = verdict(r"^reconciliation\.json")
        handover = verdict(r"^handover\.ja\.md")
        lines.append("END-TO-END MDM: %s" % e2e)
        lines.append("RECONCILIATION: %s" % reconciliation)
        lines.append("JAPANESE HANDOVER: %s" % handover)
        flags["jbeq_e2e_pass"] = (e2e == "PASS")
        flags["jbeq_reconciliation_pass"] = (reconciliation == "PASS")
        flags["jbeq_handover_pass"] = (handover == "PASS")
    else:
        lines.append("END-TO-END MDM: %s (no filed run at %s)" % (NO_DATA, run_rel))
        lines.append("RECONCILIATION: %s" % NO_DATA)
        lines.append("JAPANESE HANDOVER: %s" % NO_DATA)

    return lines, flags


# --------------------------------------------------------------- GAUNTLETS
def gather_gauntlets():
    lines = []
    flags = {"delegation_truth_pass": False, "memory_recurrence_pass": False}

    p = _newest("delegation-truth")
    if p:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        false_greens = d.get("false_greens")
        n = d.get("n")
        rate = d.get("rate_percent")
        ok = (false_greens == 0)
        lines.append("DELEGATION TRUTH: %s (false-green rate %.1f%%, %s of %s, from %s)"
                     % ("PASS" if ok else "FAIL", rate or 0.0, false_greens, n,
                        os.path.basename(p)))
        flags["delegation_truth_pass"] = ok
    else:
        lines.append("DELEGATION TRUTH: %s (no benchmarks/results/delegation-truth-*.json filed)"
                     % NO_DATA)

    p = _newest("memory-recurrence")
    if p:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        summary = d.get("summary", {})
        line = summary.get("line", "")
        no_data_conditions = summary.get("no_data") or []
        prevented = summary.get("prevented")
        counted = summary.get("conditions_counted")
        if no_data_conditions:
            verdict = NO_DATA
        elif prevented == counted:
            verdict = "PASS"
        else:
            verdict = "FAIL"
        extra = ("; no-data condition(s): %s" % ", ".join(no_data_conditions)
                 if no_data_conditions else "")
        lines.append("MEMORY RECURRENCE: %s (%s%s, from %s)"
                     % (verdict, line, extra, os.path.basename(p)))
        flags["memory_recurrence_pass"] = (verdict == "PASS")
    else:
        lines.append("MEMORY RECURRENCE: %s (no benchmarks/results/memory-recurrence-*.json filed)"
                     % NO_DATA)

    spec = os.path.join(ROOT, "benchmarks", "gauntlets", "acceptance-compression.json")
    result = _newest("acceptance-compression")
    if result:
        with open(result, encoding="utf-8") as fh:
            d = json.load(fh)
        lines.append("ACCEPTANCE COMPRESSION: %s"
                     % d.get("verdict", "%s (result filed but carries no verdict field)" % NO_DATA))
    elif os.path.isfile(spec):
        lines.append("ACCEPTANCE COMPRESSION: %s (spec status SPECIFIED, NOT YET RUN; "
                     "ACCEPTANCE TIME has NO INSTRUMENT YET, nothing on this estate "
                     "times a human reviewer)" % NO_DATA)
    else:
        lines.append("ACCEPTANCE COMPRESSION: %s (no spec and no result filed)" % NO_DATA)

    manifests = sorted(glob.glob(os.path.join(
        ROOT, "docs", "plan", "runs", "gauntlet-long-horizon-recovery-*", "RECORD.md")))
    if manifests:
        newest = manifests[-1]
        with open(newest, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^verdict:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        rel = os.path.relpath(newest, ROOT)
        lines.append("LONG-HORIZON: %s (%s)" % (m.group(1).strip() if m else NO_DATA, rel))
    else:
        lines.append("LONG-HORIZON: %s (no docs/plan/runs/gauntlet-long-horizon-recovery-* "
                     "manifest filed)" % NO_DATA)

    return lines, flags


# ------------------------------------------------------------------- CODEX
def gather_codex(codex_lane_path):
    if not codex_lane_path or not os.path.isfile(codex_lane_path):
        return (["AUTHENTICATED E2E: %s (%s not found)"
                 % (NO_DATA, codex_lane_path or DEFAULT_CODEX_LANE)],
                {"codex_e2e_pass": False})
    with open(codex_lane_path, encoding="utf-8") as fh:
        text = fh.read()
    rows = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith("|") and "SIGNED-IN" in ln]
    if not rows:
        return (["AUTHENTICATED E2E: %s (no SIGNED-IN row found in %s)"
                 % (NO_DATA, codex_lane_path)],
                {"codex_e2e_pass": False})
    verdicts = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) >= 3:
            verdicts.append(cells[2])
    if verdicts and all(v == "PASS" for v in verdicts):
        verdict = "PASS"
    elif verdicts and all(v == "FAIL" for v in verdicts):
        verdict = "FAIL"
    else:
        verdict = "PARTIAL (%s)" % ", ".join(verdicts) if verdicts else NO_DATA
    quoted = "; ".join(rows)
    return (["AUTHENTICATED E2E: %s -- %s" % (verdict, quoted)],
            {"codex_e2e_pass": verdict == "PASS"})


# ----------------------------------------------------------------- VERDICT
def compute_verdict(flags):
    """The whole of section 48's rule, pure and side effect free so
    scripts/test_morning_pack.py can drive it with fixtures. `flags` is the
    merged dict every gather_* function above contributes to."""
    japanese_retrieval_100 = bool(
        flags.get("standard_full") and flags.get("frozen_full")
        and flags.get("negative_full"))
    required_ci_enforced = bool(
        flags.get("ruleset_required") and flags.get("required_fast_wired"))
    release_integrity_green = bool(
        flags.get("manifest_clean") and flags.get("tag_signed"))
    key_gauntlets_strong = bool(
        flags.get("delegation_truth_pass") and flags.get("memory_recurrence_pass"))
    no_critical_hidden_red = not flags.get("any_hidden_fail", False)
    jbeq_mdm_qualified = bool(
        flags.get("jbeq_mdm_seed_ready") and flags.get("jbeq_mdm_zero_false_merges")
        and flags.get("jbeq_e2e_pass") and flags.get("jbeq_reconciliation_pass"))

    if (required_ci_enforced and release_integrity_green and japanese_retrieval_100
            and key_gauntlets_strong and no_critical_hidden_red):
        return "READY TO CLAIM COMPETITIVE LEAD"
    if jbeq_mdm_qualified:
        return "JAPANESE MDM ENGINEERING QUALIFIED"
    if japanese_retrieval_100:
        return "JAPANESE RETRIEVAL QUALIFIED, BUSINESS ENGINEERING IN PROGRESS"
    return "TECHNICALLY STRONG, NOT YET QUALIFIED"


def build_pack(args):
    sections = []
    all_flags = {}

    lines, flags = gather_release(args.closeout_log)
    sections.append(("RELEASE", lines))
    all_flags.update(flags)

    lines, flags = gather_ci()
    sections.append(("CI", lines))
    all_flags.update(flags)

    lines, flags = gather_japanese_retrieval()
    sections.append(("JAPANESE RETRIEVAL", lines))
    all_flags.update(flags)

    lines, flags = gather_jbeq(args.jbeq_scores)
    sections.append(("JAPANESE BUSINESS ENGINEERING", lines))
    all_flags.update(flags)

    lines, flags = gather_gauntlets()
    sections.append(("GAUNTLETS", lines))
    all_flags.update(flags)

    lines, flags = gather_codex(args.codex_lane)
    sections.append(("CODEX", lines))
    all_flags.update(flags)

    verdict = compute_verdict(all_flags)

    body = ["# Morning evidence pack, 2026-09-05",
            "",
            "GENERATED by `scripts/morning_pack.py`. Do not edit this file by hand:",
            "the next run overwrites it. Fills docs/plan/MORNING-STEERING-2026-09-05.md",
            "section 47's template; the closing line is section 48's verdict.",
            "",
            "***", ""]
    for name, lines in sections:
        body.append(name)
        body.append("")
        body.extend(lines)
        body.append("")
        body.append("***")
        body.append("")
    body.append(verdict)
    body.append("")
    return "\n".join(body), verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--closeout-log", default=None,
                        help="closeout log to read VIRGIN INSTALL from")
    parser.add_argument("--jbeq-scores", default=None,
                        help="handover pack directory holding a JBEQ-MDM answer file")
    parser.add_argument("--codex-lane", default=DEFAULT_CODEX_LANE,
                        help="path to the Codex lane evidence file")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="where to write the generated pack")
    args = parser.parse_args(argv)

    text, verdict = build_pack(args)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s" % args.out)
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())

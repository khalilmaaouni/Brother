#!/usr/bin/env python3
"""keep_current: update a Codex install only after every link reads PASS.

Seven links, checked in order, STOPPING at the first link that is not PASS
and naming which one stopped it. This mirrors the discipline of
scripts/release_closeout.py's gate matrix (PASS, FAIL, NO-DATA, never a
fourth word; NO-DATA is never a pass), applied to the question "is it safe
to move THIS Codex install onto this tag":

  1. signature       git tag -v <tag> in a clone (release_closeout.py's
                      tag_signature_verified, same PASS/FAIL/NO-DATA shape:
                      an unsigned tag is NO-DATA, a bad signature is FAIL).
  2. reproduction     scripts/reproduce_export.py --verify-tree --tag <tag>
                      run inside that clone; PASS only on its own PASS line.
  3. manifest         docs/releases/<version>.export-manifest.txt exists in
                      the clone and its digest equals the one the release
                      note states (the same comparison reproduce_export.py
                      makes internally; this link is a lighter, direct
                      re-check of just that one fact).
  4. conformance      ~/.claude/evidence/adapter-conformance/codex/summary.txt
                      names provider=codex and verdict=PASS, and is not
                      older than the tag's own commit date.
  5. closeout         reads the VERDICT release_closeout.py's own run logs
                      (RUN.log and any RUN-X<n>.log rerun) record for each
                      of X1 to X7, never gate-directory presence alone: PASS
                      only when all seven read PASS, FAIL when any reads
                      FAIL, NO-DATA otherwise (a missing or NO-DATA gate is
                      never a pass).
  6. virgin CI        a recorded GitHub Actions virgin-install run id for
                      this tag, read from the closeout evidence; this script
                      never dispatches anything (no self-fired CI).
  7. smoke            ~/.claude/evidence/codex-battery/SUMMARY.txt, first
                      line "tag=<tag>", B6 and B8 and B9 all PASS.

Only with all seven PASS, and --install given, does it run
scripts/brother_install.py upgrade --ref <tag>. Without --install this is a
pure report. A missing scripts/brother_install.py prints NO-DATA naming the
file and does not install.

Exit 0 whenever the report ran (whether or not it installed); the verdicts
themselves are the signal, read from stdout. Exit 2 on a usage error.
No em or en dashes.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LINK_NAMES = (
    "signature",
    "reproduction",
    "manifest",
    "conformance",
    "closeout",
    "virgin-ci",
    "smoke",
)


def sh(args, cwd=None, timeout=900):
    """Run a command, keep both streams, never raise on a nonzero exit or a
    missing binary: a subprocess boundary always gets an explicit failure
    path here, reported to the caller as (returncode, combined_output)."""
    try:
        proc = subprocess.run(
            args, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return proc.returncode, proc.stdout or ""
    except FileNotFoundError as exc:
        return None, "command not found: %s" % exc
    except subprocess.TimeoutExpired:
        return None, "timed out after %ds: %s" % (timeout, " ".join(args))
    except OSError as exc:
        return None, "could not run %s: %s" % (" ".join(args), exc)


def read_text(path):
    """(text_or_None, why). A missing or unreadable file is never an
    exception a caller has to guard against separately."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(), ""
    except OSError as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Link 1: signature. Mirrors release_closeout.py's tag_signature_verified.
# ---------------------------------------------------------------------------

def classify_signature(returncode, combined_output):
    if returncode is None:
        return "NO-DATA", "could not run git tag -v: %s" % combined_output
    lowered = combined_output.lower()
    if returncode == 0:
        return "PASS", "git tag -v verifies a good signature"
    if "bad signature" in lowered:
        return "FAIL", "git tag -v reports a BAD signature, not merely an absent one"
    last = combined_output.strip().splitlines()[-1] if combined_output.strip() else "no output"
    return "NO-DATA", "no signing key configured (S5, founder); git tag -v found no signature (%s)" % last


def link_signature(clone_dir, tag):
    returncode, output = sh(["git", "tag", "-v", tag], cwd=clone_dir, timeout=120)
    return classify_signature(returncode, output)


# ---------------------------------------------------------------------------
# Link 2: source reproduction.
# ---------------------------------------------------------------------------

def classify_reproduction(returncode, stdout):
    if returncode is None:
        return "NO-DATA", "could not run reproduce_export.py: %s" % stdout
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    pass_lines = [ln for ln in lines if ln.strip().startswith("PASS")]
    if returncode == 0 and pass_lines:
        return "PASS", pass_lines[0][:200]
    nodata_lines = [ln for ln in lines if "NO-DATA" in ln]
    if nodata_lines:
        return "NO-DATA", nodata_lines[0][:200]
    tail = lines[-1][:200] if lines else "no output"
    return "FAIL", tail


def link_reproduction(clone_dir, tag):
    script = os.path.join(clone_dir, "scripts", "reproduce_export.py")
    if not os.path.isfile(script):
        return "NO-DATA", "%s not found in the clone" % script
    returncode, output = sh(
        [sys.executable, script, "--verify-tree", "--tag", tag],
        cwd=clone_dir, timeout=1800,
    )
    return classify_reproduction(returncode, output)


# ---------------------------------------------------------------------------
# Link 3: manifest presence and digest.
# ---------------------------------------------------------------------------

NOTE_DIGEST_RE = re.compile(r"Export manifest digest `([0-9a-f]{64})`")


def classify_manifest(manifest_exists, note_text, manifest_text):
    if not manifest_exists:
        return "NO-DATA", "the export manifest is not in the clone"
    if note_text is None:
        return "NO-DATA", "the release note is not in the clone, so there is nothing to check the digest against"
    match = NOTE_DIGEST_RE.search(note_text)
    if not match:
        return "NO-DATA", "the release note states no export manifest digest in the form this link reads"
    stated = match.group(1)
    computed = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if stated == computed:
        return "PASS", "manifest digest %s matches the release note" % stated[:12]
    return "FAIL", "release note states %s, the manifest in the clone digests to %s" % (stated[:12], computed[:12])


def link_manifest(clone_dir, version):
    manifest_path = os.path.join(clone_dir, "docs", "releases", "%s.export-manifest.txt" % version)
    note_path = os.path.join(clone_dir, "docs", "releases", "%s.md" % version)
    manifest_text, _ = read_text(manifest_path)
    note_text, _ = read_text(note_path)
    return classify_manifest(manifest_text is not None, note_text, manifest_text or "")


# ---------------------------------------------------------------------------
# Link 4: adapter conformance summary.
# ---------------------------------------------------------------------------

CONFORMANCE_DEFAULT = os.path.expanduser("~/.claude/evidence/adapter-conformance/codex/summary.txt")


def classify_conformance(summary_text, summary_mtime, tag_commit_epoch):
    if summary_text is None:
        return "NO-DATA", "no adapter conformance summary at %s" % CONFORMANCE_DEFAULT
    if tag_commit_epoch is not None and summary_mtime is not None and summary_mtime < tag_commit_epoch:
        return "NO-DATA", "the conformance summary is older than the tag's own commit date, so it does not speak to this tag"
    if "provider=codex" not in summary_text:
        return "NO-DATA", "the summary names no provider=codex line"
    if "verdict=PASS" in summary_text:
        return "PASS", "provider=codex verdict=PASS"
    if "verdict=FAIL" in summary_text:
        return "FAIL", "provider=codex verdict=FAIL"
    return "NO-DATA", "the summary names provider=codex but no verdict=PASS or verdict=FAIL"


def link_conformance(tag_commit_epoch, path=CONFORMANCE_DEFAULT):
    text, _ = read_text(path)
    mtime = os.path.getmtime(path) if os.path.isfile(path) else None
    return classify_conformance(text, mtime, tag_commit_epoch)


# ---------------------------------------------------------------------------
# Link 5: X1 to X7 closeout evidence, read by VERDICT.
#
# release_closeout.py writes one gate-directory per gate whether that gate
# PASSED or not, so directory presence alone (the earlier shape of this
# link) reads PASS on a closeout whose own verdict table says otherwise: on
# v1.0.9 all seven X1-X7 directories existed while X7 read FAIL and X1, X6
# read NO-DATA, and the old link still printed PASS. NO-DATA is never a
# pass, and neither is presence.
# ---------------------------------------------------------------------------

CLOSEOUT_GATES = ("X1", "X2", "X3", "X4", "X5", "X6", "X7")

# release_closeout.py opens each gate's block with a line of this shape,
# e.g. "== X7 public-artifact   FAIL" (verdict_table in release_closeout.py
# uses the same four words: PASS, FAIL, NO-DATA, FOUNDER).
GATE_HEADER_RE = re.compile(
    r"^==\s+(X[0-9]+)\s+\S+\s+(PASS|FAIL|NO-DATA|FOUNDER)\s*$", re.MULTILINE)
# Each gate's block closes with its own "PASS: ...", "FAIL: ..." or
# "NO-DATA: ..." summary line; a block can also carry an earlier sub-check's
# PASS/FAIL/NO-DATA line, so the LAST match in the block is the gate's own
# verdict line, never the first.
REASON_LINE_RE = re.compile(
    r"^[ \t]*(?:PASS|FAIL|NO-DATA):[ \t]*(.+)$", re.MULTILINE)


def parse_gate_verdicts(text):
    """{gate_id: (verdict, why)} read from one release_closeout.py run log
    (the full RUN.log, or a single-gate RUN-X<n>.log rerun)."""
    headers = list(GATE_HEADER_RE.finditer(text))
    verdicts = {}
    for i, m in enumerate(headers):
        gate_id, verdict = m.group(1), m.group(2)
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[m.end():block_end]
        reasons = REASON_LINE_RE.findall(block)
        why = reasons[-1].strip() if reasons else "no reason line found in this gate's block"
        verdicts[gate_id] = (verdict, why)
    return verdicts


def classify_closeout(gate_verdicts):
    failing = [g for g in CLOSEOUT_GATES
               if gate_verdicts.get(g, (None, None))[0] == "FAIL"]
    if failing:
        return "FAIL", "gate(s) read FAIL: %s" % "; ".join(
            "%s (%s)" % (g, gate_verdicts[g][1]) for g in failing)
    not_pass = [g for g in CLOSEOUT_GATES
                if gate_verdicts.get(g, (None, None))[0] != "PASS"]
    if not_pass:
        return "NO-DATA", "gate(s) not PASS: %s" % ", ".join(
            "%s(%s)" % (g, gate_verdicts[g][0] if g in gate_verdicts else "missing")
            for g in not_pass)
    return "PASS", "closeout evidence reads PASS for gates %s" % ", ".join(CLOSEOUT_GATES)


RUN_LOG_RE = re.compile(r"^RUN(-X[0-9]+)?\.log$")


def read_closeout_verdicts(root):
    """{gate_id: (verdict, why)} merged across every RUN.log and RUN-X<n>.log
    in the closeout directory itself (never its work* throwaway homes, which
    hold binaries and caches, not logs), oldest to newest by file mtime: a
    later file's verdict for a gate overwrites an earlier one, so a
    single-gate rerun (RUN-X1.log) can supersede that one gate's line in the
    full RUN.log without disturbing the other six. "Later" is decided by
    mtime, not by a timestamp inside the log, because neither RUN.log nor a
    RUN-X<n>.log rerun carries one."""
    if not os.path.isdir(root):
        return {}
    log_paths = [
        os.path.join(root, fn) for fn in os.listdir(root)
        if RUN_LOG_RE.match(fn) and os.path.isfile(os.path.join(root, fn))
    ]
    log_paths.sort(key=os.path.getmtime)
    gate_verdicts = {}
    for path in log_paths:
        log_text, _ = read_text(path)
        if log_text is not None:
            gate_verdicts.update(parse_gate_verdicts(log_text))
    return gate_verdicts


def link_closeout(version, evidence_dir=None):
    root = evidence_dir or os.path.expanduser("~/.claude/evidence/closeout-%s" % version)
    return classify_closeout(read_closeout_verdicts(root)), root


# ---------------------------------------------------------------------------
# Link 6: virgin CI run id. Never dispatches; only reads what was recorded.
# ---------------------------------------------------------------------------

RUN_ID_RE = re.compile(r"run[_-]?id[:=]\s*(\S+)", re.IGNORECASE)


def classify_ci(closeout_text):
    if closeout_text is None:
        return "NO-DATA", "no closeout log to read a recorded CI run id from"
    match = RUN_ID_RE.search(closeout_text)
    if match:
        return "PASS", "recorded virgin-install run id %s" % match.group(1)
    return "NO-DATA", "no recorded GitHub Actions virgin-install run id for this tag; this script never dispatches one"


def link_ci(closeout_dir):
    if not os.path.isdir(closeout_dir):
        return classify_ci(None)
    combined = []
    for base, dirs, files in os.walk(closeout_dir):
        # The matrix's throwaway homes (work*, isolated Codex homes with
        # binaries, sqlite and plugin caches) hold no recorded run id and
        # are not text; reading them crashed this link on 2026-09-07.
        dirs[:] = [d for d in dirs if not d.startswith("work")]
        for fn in files:
            if not fn.endswith((".log", ".txt")):
                continue
            try:
                text, _ = read_text(os.path.join(base, fn))
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                combined.append(text)
    return classify_ci("\n".join(combined) if combined else None)


# ---------------------------------------------------------------------------
# Link 7: signed-in Codex battery SUMMARY.
# ---------------------------------------------------------------------------

SMOKE_DEFAULT = os.path.expanduser("~/.claude/evidence/codex-battery/SUMMARY.txt")
REQUIRED_LEGS = ("B6", "B8", "B9")


def classify_smoke(tag, summary_text):
    if summary_text is None:
        return "NO-DATA", "no signed-in Codex battery summary at %s" % SMOKE_DEFAULT
    lines = summary_text.splitlines()
    if not lines or lines[0].strip() != "tag=%s" % tag:
        first = lines[0].strip() if lines else "(empty file)"
        return "NO-DATA", "summary's first line is %r, not tag=%s" % (first, tag)
    verdicts = {}
    for leg in REQUIRED_LEGS:
        m = re.search(r"^%s\b.*?\b(PASS|FAIL|NO-DATA)\b" % leg, summary_text, re.MULTILINE)
        verdicts[leg] = m.group(1) if m else None
    if all(verdicts[leg] == "PASS" for leg in REQUIRED_LEGS):
        return "PASS", "B6, B8, B9 all PASS"
    if any(verdicts[leg] == "FAIL" for leg in REQUIRED_LEGS):
        failing = [leg for leg in REQUIRED_LEGS if verdicts[leg] == "FAIL"]
        return "FAIL", "%s read FAIL" % ", ".join(failing)
    missing = [leg for leg in REQUIRED_LEGS if verdicts[leg] != "PASS"]
    return "NO-DATA", "%s did not all read PASS (%s)" % (
        ", ".join(missing), ", ".join("%s=%s" % (leg, verdicts[leg]) for leg in REQUIRED_LEGS))


def link_smoke(tag, path=SMOKE_DEFAULT):
    text, _ = read_text(path)
    return classify_smoke(tag, text)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

def run_links(tag, public_url, evidence_dir, clone_dir, closeout_dir=None):
    """[(name, verdict, message)], stopping at the first non-PASS link."""
    version = tag[1:] if tag.startswith("v") else tag
    results = []

    def stop_here():
        return [name for name, _v, _m in results if True]

    # 1. signature (needs a clone; clone_dir is prepared by the caller)
    verdict, msg = link_signature(clone_dir, tag)
    results.append(("signature", verdict, msg))
    if verdict != "PASS":
        return results

    # 2. reproduction
    verdict, msg = link_reproduction(clone_dir, tag)
    results.append(("reproduction", verdict, msg))
    if verdict != "PASS":
        return results

    # 3. manifest
    verdict, msg = link_manifest(clone_dir, version)
    results.append(("manifest", verdict, msg))
    if verdict != "PASS":
        return results

    # 4. conformance
    returncode, output = sh(["git", "log", "-1", "--format=%ct", tag], cwd=clone_dir, timeout=60)
    tag_epoch = int(output.strip()) if returncode == 0 and output.strip().isdigit() else None
    verdict, msg = link_conformance(tag_epoch)
    results.append(("conformance", verdict, msg))
    if verdict != "PASS":
        return results

    # 5. closeout
    # The closeout matrix lives under release_closeout.py's own root
    # (~/.claude/evidence/closeout-<version>), never under this tool's
    # --evidence-dir, which holds the clone. Passing the clone root here
    # made link 5 report every gate missing on a machine where all seven
    # were on disk (measured 2026-09-07, v1.0.9).
    (verdict, msg), closeout_dir = link_closeout(version, closeout_dir)
    results.append(("closeout", verdict, msg))
    if verdict != "PASS":
        return results

    # 6. virgin CI
    verdict, msg = link_ci(closeout_dir)
    results.append(("virgin-ci", verdict, msg))
    if verdict != "PASS":
        return results

    # 7. smoke
    verdict, msg = link_smoke(tag)
    results.append(("smoke", verdict, msg))
    return results


def prepare_clone(evidence_dir, tag, public_url):
    """(clone_dir, why): clone the public repository into the evidence dir,
    or reuse an existing clone already checked out at this tag. A clone
    failure is NO-DATA for the signature link, never an exception here."""
    dest = os.path.join(evidence_dir, "clone-%s" % tag)
    if os.path.isdir(os.path.join(dest, ".git")):
        return dest, ""
    try:
        os.makedirs(evidence_dir, exist_ok=True)
    except OSError as exc:
        return None, "could not create %s: %s" % (evidence_dir, exc)
    returncode, output = sh(
        ["git", "clone", "--branch", tag, "--depth", "1", public_url, dest],
        timeout=900,
    )
    if returncode != 0:
        return None, "git clone --branch %s --depth 1 %s exited %s: %s" % (
            tag, public_url, returncode, output[-400:])
    return dest, ""


def default_install(args):
    script = REPO_ROOT / "scripts" / "brother_install.py"
    if not script.is_file():
        print("NO-DATA: %s not found, cannot install" % script)
        return 2
    returncode, output = sh(
        [sys.executable, str(script), "upgrade", "--ref", args.tag], timeout=900,
    )
    print(output)
    return 0 if returncode == 0 else 1


def main(argv=None, install_fn=default_install):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="e.g. v1.0.8")
    parser.add_argument("--public-url", default="https://github.com/khalilmaaouni/Brother")
    parser.add_argument("--evidence-dir", default=os.path.expanduser("~/.claude/evidence/keep-current"))
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--closeout-dir", default=None,
                        help="where release_closeout.py wrote X1 to X7 "
                             "(default: ~/.claude/evidence/closeout-<version>)")
    args = parser.parse_args(argv)

    if not re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+$", args.tag):
        parser.error("--tag must look like vX.Y.Z")

    clone_dir, why = prepare_clone(args.evidence_dir, args.tag, args.public_url)
    if clone_dir is None:
        print("NO-DATA: signature (%s)" % why)
        print("stopped at: signature")
        return 0

    results = run_links(args.tag, args.public_url, args.evidence_dir, clone_dir,
                        closeout_dir=args.closeout_dir)
    for name, verdict, msg in results:
        print("%-8s %-13s %s" % (verdict, name, msg))

    all_pass = len(results) == len(LINK_NAMES) and all(v == "PASS" for _n, v, _m in results)
    if not all_pass:
        stopped_at = results[-1][0] if results else "signature"
        print("stopped at: %s" % stopped_at)
        return 0

    print("all seven links PASS for %s" % args.tag)
    if args.install:
        return install_fn(args)
    print("(no --install: report only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

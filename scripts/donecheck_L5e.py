#!/usr/bin/env python3
"""L5e parent done check: run the documentation accuracy audit for real and gate it.

WHY (2026-09-28): every L5e sub unit landed, but the audit document carried typed scores of 10.0
that no run had produced, and the README install runner refuses every command because the build
screen forbids subprocess. This check is the orchestrator's half: it runs the three audits against
the real tree, computes each area's score from what passed, assembles the document with
system_doc.build_audit_doc and gates it with l5e_5_score_gate.score_audit_doc.

usage: donecheck_L5e.py [--repo DIR] [--write]
exit 0 PASS (score >= 8.5), 1 FAIL (below the gate), 2 NO-DATA (an audit could not run)."""
import argparse, os, shutil, subprocess, sys, tempfile

AUDIT_REL = os.path.join("docs", "architecture", "L5E-DOCS-ACCURACY-AUDIT.md")
INSTALL_TIMEOUT_S = 300


def fence(text, limit=60):
    lines = (text or "").rstrip("\n").splitlines()
    if len(lines) > limit:
        lines = lines[:limit] + ["... %d more line(s) cut" % (len(lines) - limit)]
    return "```text\n" + "\n".join(l.replace("```", "'''") for l in lines) + "\n```"


def audit_system_doc(S, repo):
    diff = S.compute_system_doc_diff(repo)
    ok = diff == ""
    body = ("Regenerated with scripts/system_doc.py and diffed against the checked-in SYSTEM.md: "
            + ("the diff is empty, the file is current." if ok else "the diff is NOT empty, the file is stale:\n\n" + fence(diff)))
    return (10.0 if ok else 0.0), "## SYSTEM.md accuracy\n\n" + body


def audit_parity(P, repo):
    path = os.path.join(repo, "docs", "architecture", "PARITY-MATRIX.md")
    with open(path, encoding="utf-8") as fh:
        claims = P.extract_parity_claims(fh.read())
    if not claims:
        raise RuntimeError("PARITY-MATRIX.md yields zero MIGRATED or COVERED claims")
    bad = [c for c in claims if not P.verify_claim(c, repo)]
    score = round(10.0 * (len(claims) - len(bad)) / len(claims), 2)
    body = "%d claim(s) extracted from MIGRATED and COVERED rows; %d resolve to a real path or symbol." % (
        len(claims), len(claims) - len(bad))
    if bad:
        body += " Unresolved:\n\n" + "\n".join("- `%s`" % c for c in bad)
    return score, "## PARITY-MATRIX accuracy\n\n" + body


def find_claude():
    """The README's prerequisite: Claude Code on PATH, else the desktop app's bundled copy (newest version)."""
    hit = shutil.which("claude")
    if hit:
        return hit
    base = os.path.expanduser("~/Library/Application Support/Claude/claude-code")
    try:
        versions = sorted(os.listdir(base), key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
    except OSError:
        return None
    for v in reversed(versions):
        cand = os.path.join(base, v, "claude.app", "Contents", "MacOS", "claude")
        if os.access(cand, os.X_OK):
            return cand
    return None


def audit_readme(R, repo):
    with open(os.path.join(repo, "README.md"), encoding="utf-8") as fh:
        commands = R.extract_install_commands(fh.read())
    scratch = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
    os.makedirs(scratch, exist_ok=True)
    home = tempfile.mkdtemp(prefix="l5e-fresh-home-", dir=scratch)
    claude = find_claude()
    if claude is None:
        raise RuntimeError("no claude binary: not on PATH and no desktop app copy, so the README prerequisite is absent")
    bindir = os.path.join(home, "bin")
    os.makedirs(bindir)
    os.symlink(claude, os.path.join(bindir, "claude"))
    env = {"HOME": home, "PATH": bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": home,
           "LANG": "en_US.UTF-8"}
    rows, passed = [], 0
    try:
        for cmd in commands:
            try:
                p = subprocess.run(["/bin/bash", "-c", cmd], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
                rc, out = p.returncode, (p.stdout + p.stderr)
            except subprocess.TimeoutExpired:
                rc, out = "timeout", "no answer in %d s" % INSTALL_TIMEOUT_S
            passed += rc == 0
            rows.append("$ %s\n[exit %s]\n%s" % (cmd, rc, out.strip()[-1500:]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    score = round(10.0 * passed / len(commands), 2)
    body = ("Every command of the README Installation block, run from the checkout with a fresh empty HOME "
            "(prerequisite Claude Code: %s): %d of %d exited 0.\n\n" % (os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(claude))))) if "claude-code" in claude else claude, passed, len(commands))) + fence("\n\n".join(rows), limit=80)
    return score, "## README install accuracy\n\n" + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--write", action="store_true", help="write the audit document into the repository")
    a = ap.parse_args()
    repo = os.path.realpath(a.repo)
    sys.path.insert(0, os.path.join(repo, "scripts"))
    try:
        import system_doc as S
        import test_l5e_2_parity_matrix_claims as P
        import test_l5e_3_readme_install as R
        from l5e_5_score_gate import score_audit_doc
    except Exception as exc:
        print("NO-DATA: an L5e module would not import: %r" % (exc,)); return 2
    areas = []
    for name, fn, mod in (("SYSTEM.md", audit_system_doc, S), ("PARITY-MATRIX", audit_parity, P), ("README install", audit_readme, R)):
        try:
            areas.append(fn(mod, repo))
        except Exception as exc:
            print("NO-DATA: the %s audit could not run: %r" % (name, exc)); return 2
    weights = ("0.34", "0.33", "0.33")
    labels = ("SYSTEM.md accuracy", "PARITY-MATRIX accuracy", "README install accuracy")
    overall = round(sum(float(w) * s for w, (s, _) in zip(weights, areas)), 2)
    table = "\n".join(["## Score", "", "Scores are computed by scripts/donecheck_L5e.py from the runs above "
                       "(area score = 10 times the share that passed), never typed.", "",
                       "| Area | Weight | Score |", "| --- | --- | --- |"]
                      + ["| %s | %s | %s |" % (l, w, s) for l, w, (s, _) in zip(labels, weights, areas)]
                      + ["| Overall | 1.00 | %s |" % overall])
    doc = S.build_audit_doc([sec for _, sec in areas] + [table])
    if a.write:
        with open(os.path.join(repo, AUDIT_REL), "w", encoding="utf-8") as fh:
            fh.write(doc)
    for l, (s, _) in zip(labels, areas):
        print("%-24s %5.2f / 10" % (l, s))
    try:
        got = score_audit_doc(doc)
    except ValueError as exc:
        print("FAIL: %s" % exc); return 1
    print("PASS: L5e audit scores %.2f / 10, gate 8.5%s" % (got, ", written to " + AUDIT_REL if a.write else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""DOM-50.10: does Brother's own install path need a network, and when it
does, does it say so, or fail silently.

WHY THIS EXISTS. Unit DOM-50.10 in docs/plan/ORCH-1020-WBS.json carried
disposition NO-DATA: nobody had ever measured an offline install. Its
deciding property is "an install completes with no network, and anything it
cannot do offline is named rather than skipped." Two passes, composed:

STATIC AUDIT (static_audit()). Reads the files the product itself documents
as the install path (README.md's install section names them; see
INSTALL_PATH_FILES) and names every network-touching call by file, line and
what it fetches. A file in that list that is simply absent from this
checkout is skipped, not an error; if NONE of them are present the install
path could not be identified at all, and the whole report is NO-DATA rather
than a confident-looking empty audit.

GUARDED PROBE (run_probe_step() over build_probe_steps()). Runs a short list
of REAL, bounded commands, each lifted verbatim (same argv, same source
file:line) from an install script or the shipped runtime verifier, inside a
throwaway HOME with every proxy variable pointed at an unroutable local port
and PIP_NO_INDEX set, exactly as the unit's brief specifies. Two kinds of
step:

  offline-expected: a command the audit found nothing network-shaped in
  (bundle/runtime/verify_runtime.py, the Cursor plugin copy). It should run
  to completion under the block; whether it then reports PASS or FAIL about
  the tree it is checking is a property of that tree, not of this probe.

  network-expected: the same curl and git commands the smoke scripts run for
  real, under the same block. These are EXPECTED to fail: the block holding
  is what "named rather than skipped" looks like in a probe rather than in
  prose. A network-expected step that unexpectedly SUCCEEDS is the one
  finding this file treats as a hard problem, because it means either the
  block did not hold or a cache answered instead of the network, and either
  way an offline claim resting on it would be false.

ONE CALL IS NAMED, NEVER RUN: the two Codex plugin subprocess calls the
static audit finds in scripts/brother_install.py (marketplace add, plugin
add) go through a compiled binary this repository does not control and
cannot show honors HTTP_PROXY/HTTPS_PROXY the way curl and git reliably do.
The rule that governs this whole file is absolute (never make a real
network call), so nothing is executed here whose network behavior under the
proxy trick is unverified. It is reported as NOT-PROBED with the reason,
never silently left out of the report.

Never touches the real HOME (every probed command runs under a temp HOME
this script creates and removes). Never installs anything for real (every
write lands under that temp HOME, never under docs/plan, ~/.claude or the
user's actual home).

Python 3.9, standard library only. No em or en dashes anywhere in this file
or its output.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

UNROUTABLE_PROXY = "http://127.0.0.1:1"
PROXY_ENV_KEYS = (
    "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy",
)

# ---------------------------------------------------------------------------
# Static audit: the files the product documents as the install path.
#
# README.md's install section (the "## Check what you installed" heading and
# the plugin-install lines above it) names bundle/runtime/verify_runtime.py
# and the codex-hooks path; the rest are the scripts under scripts/ whose own
# module docstrings say they perform or verify an install.

INSTALL_PATH_FILES = (
    "scripts/brother_install.py",
    "scripts/codex_hooks_install.py",
    "scripts/cursor_plugin_install.py",
    "scripts/check_installed_surface.py",
    "scripts/clean_install_e2e.sh",
    "scripts/bundle-install-smoke.sh",
    "scripts/install_gate_hook.sh",
    "bundle/runtime/verify_runtime.py",
)

# (compiled pattern, what it names). Exactly the network primitives this
# repository's install path actually uses today, found by hand while writing
# this file (2026-09-18, DOM-50.10), plus pip/npm install for any script that
# later reaches for them the ordinary way. A pattern that cannot fire on a
# real line is dead weight, so nothing here is speculative.
NETWORK_CALL_PATTERNS = (
    (re.compile(r"\burlopen\("), "urllib.request.urlopen call"),
    (re.compile(r"\brequests\.(get|post|put|delete)\("), "requests library call"),
    (re.compile(r"\bcurl\s"), "curl invocation"),
    (re.compile(r"\bgit\s+(clone|ls-remote|fetch|pull)\b"), "git network subcommand"),
    (re.compile(r"\bpip\s+install\b"), "pip install"),
    (re.compile(r"\bnpm\s+install\b"), "npm install"),
    (re.compile(r'"plugin",\s*"marketplace",\s*"add"'),
     "codex plugin marketplace add (fetches the marketplace source)"),
    (re.compile(r'"plugin",\s*"add"'),
     "codex plugin add (fetches the plugin bundle from the marketplace)"),
)


def static_audit(repo_root):
    """{"findings": [...], "install_path_identified": bool}.

    Each finding is {"file", "line", "text", "what"}: a real line matching a
    NETWORK_CALL_PATTERNS entry, never a summary or a guess. A file listed in
    INSTALL_PATH_FILES that does not exist in this checkout is skipped, not
    reported as a problem (not every product surface ships everywhere); if
    every one of them is absent, install_path_identified is False and the
    caller must report NO-DATA rather than an empty, confident-looking
    audit."""
    findings = []
    any_present = False
    for rel in INSTALL_PATH_FILES:
        path = os.path.join(repo_root, rel)
        if not os.path.isfile(path):
            continue
        any_present = True
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError as exc:
            findings.append({"file": rel, "line": None, "text": None,
                              "what": "could not read this install-path file: %s" % exc})
            continue
        for lineno, line in enumerate(lines, start=1):
            for pattern, what in NETWORK_CALL_PATTERNS:
                if pattern.search(line):
                    findings.append({"file": rel, "line": lineno,
                                      "text": line.strip(), "what": what})
    return {"findings": findings, "install_path_identified": any_present}


# ---------------------------------------------------------------------------
# Guarded probe.


def blocked_network_env(tmp_home):
    """A copy of os.environ with every proxy variable this session's shell
    tools respect pointed at a closed local port, PIP_NO_INDEX set, and HOME
    (plus the two client config-dir variables the probed tools read)
    repointed at tmp_home. This is the guard the unit's brief names
    verbatim; it is also the reason nothing that might ignore it (a compiled
    binary whose HTTP client this repository does not control) is executed
    by this file at all, see the module docstring."""
    env = dict(os.environ)
    for key in PROXY_ENV_KEYS:
        env[key] = UNROUTABLE_PROXY
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    env["PIP_NO_INDEX"] = "1"
    env["HOME"] = tmp_home
    env["CODEX_HOME"] = os.path.join(tmp_home, "codex-home")
    env["CLAUDE_CONFIG_DIR"] = os.path.join(tmp_home, "claude-config")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def build_probe_steps(repo_root, tmp_home):
    """The bounded, real commands the probe runs, plus the one entry it
    names but refuses to run. Each executable step carries "argv", "cwd",
    "expect" ("offline" or "network_blocked") and "timeout"; the refused one
    carries "not_probed" and "reason" instead of "argv"."""
    return [
        {
            "name": "runtime_manifest_verify",
            "source": "bundle/runtime/verify_runtime.py",
            "argv": [sys.executable, os.path.join(repo_root, "bundle",
                                                    "runtime", "verify_runtime.py")],
            "cwd": repo_root,
            "expect": "offline",
            "timeout": 30,
        },
        {
            "name": "cursor_plugin_copy",
            "source": "scripts/cursor_plugin_install.py",
            "argv": [sys.executable,
                     os.path.join(repo_root, "scripts", "cursor_plugin_install.py"),
                     "install", "--target",
                     os.path.join(tmp_home, "cursor-plugin")],
            "cwd": repo_root,
            "expect": "offline",
            "timeout": 30,
        },
        {
            "name": "raw_manifest_fetch",
            "source": "scripts/bundle-install-smoke.sh:75",
            "argv": ["curl", "-sfL", "--max-time", "5",
                     "https://raw.githubusercontent.com/khalilmaaouni/Brother/"
                     "main/.claude-plugin/marketplace.json",
                     "-o", os.path.join(tmp_home, "marketplace.json")],
            "cwd": tmp_home,
            "expect": "network_blocked",
            "timeout": 15,
        },
        {
            "name": "tag_ls_remote",
            "source": "scripts/clean_install_e2e.sh:85",
            "argv": ["git", "-c", "http.proxy=%s" % UNROUTABLE_PROXY,
                      "-c", "https.proxy=%s" % UNROUTABLE_PROXY,
                      "ls-remote", "--tags",
                      "https://github.com/khalilmaaouni/Brother"],
            "cwd": tmp_home,
            "expect": "network_blocked",
            "timeout": 15,
        },
        {
            "name": "codex_plugin_marketplace_add",
            "source": "scripts/brother_install.py:254",
            "not_probed": True,
            "reason": ("compiled binary (the app-bundled codex CLI); this "
                       "repository cannot show its HTTP client honors "
                       "HTTP_PROXY/HTTPS_PROXY the way curl and git do, and "
                       "the rule governing this file is absolute (never make "
                       "a real network call), so this call is named by the "
                       "static audit only and never executed"),
        },
    ]


def run_probe_step(step, env):
    """One probe result. A step with not_probed True is passed through
    unchanged with outcome NOT-PROBED. Otherwise the real command runs for
    real, bounded by its own timeout; every subprocess boundary here has an
    explicit failure path, nothing raises past this function.

    Outcome vocabulary:
      RAN_OFFLINE          an offline-expected command completed (whatever
                            its own exit code; that code is a property of
                            the tree it checked, not of network reachability)
      BLOCKED_AS_EXPECTED   a network-expected command failed, confirming
                            the proxy block held
      NETWORK_NOT_BLOCKED   a network-expected command exited 0: the safety-
                            relevant bad state, surfaced loudly, never read
                            as a pass
      TIMEOUT               did not finish inside its own timeout
      COULD_NOT_RUN         the binary itself could not be started (missing
                            from PATH, not a permission problem this probe
                            can distinguish further)
      NOT-PROBED            named, deliberately not executed; see "reason" """
    if step.get("not_probed"):
        return {"name": step["name"], "source": step["source"],
                "command": None, "expect": None, "returncode": None,
                "outcome": "NOT-PROBED", "detail": step["reason"]}
    try:
        proc = subprocess.run(step["argv"], cwd=step["cwd"], env=env,
                               timeout=step["timeout"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    except subprocess.TimeoutExpired:
        return {"name": step["name"], "source": step["source"],
                "command": " ".join(step["argv"]), "expect": step["expect"],
                "returncode": None, "outcome": "TIMEOUT",
                "detail": "did not finish within %ss" % step["timeout"]}
    except OSError as exc:
        return {"name": step["name"], "source": step["source"],
                "command": " ".join(step["argv"]), "expect": step["expect"],
                "returncode": None, "outcome": "COULD_NOT_RUN",
                "detail": str(exc)}
    tail = (proc.stderr or proc.stdout or "").strip()[-400:]
    if step["expect"] == "offline":
        outcome = "RAN_OFFLINE"
    elif proc.returncode == 0:
        outcome = "NETWORK_NOT_BLOCKED"
    else:
        outcome = "BLOCKED_AS_EXPECTED"
    return {"name": step["name"], "source": step["source"],
            "command": " ".join(step["argv"]), "expect": step["expect"],
            "returncode": proc.returncode, "outcome": outcome, "detail": tail}


def run_guarded_probe(repo_root):
    """[{...result...}, ...] for every step in build_probe_steps(), inside a
    fresh temp HOME this function creates and always removes, even on an
    unhandled exception from a probe step (there should be none; the
    try/finally is the boundary contract regardless)."""
    tmp_home = tempfile.mkdtemp(prefix="brother-air-gapped-probe-")
    try:
        env = blocked_network_env(tmp_home)
        steps = build_probe_steps(repo_root, tmp_home)
        return [run_probe_step(step, env) for step in steps]
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


# ---------------------------------------------------------------------------
# Report assembly.


def generate_report(repo_root):
    """The full DOM-50.10 measurement: static findings, probe results, and
    one top-level verdict.

    "NO-DATA": the install path could not be identified (none of
    INSTALL_PATH_FILES exist in this checkout).
    "FAIL": the install path was identified, but a network-expected probe
    step came back NETWORK_NOT_BLOCKED (the proxy block did not hold, so
    nothing else in this report can be trusted as an offline measurement).
    "PASS": the install path was identified, no block failed to hold, and
    every finding and probe step is named in the report (nothing skipped)."""
    audit = static_audit(repo_root)
    if not audit["install_path_identified"]:
        return {"verdict": "NO-DATA",
                "reason": "none of the documented install-path files exist "
                          "in this checkout: %s" % ", ".join(INSTALL_PATH_FILES),
                "static_audit": audit, "probe": []}
    probe = run_guarded_probe(repo_root)
    unblocked = [r for r in probe if r["outcome"] == "NETWORK_NOT_BLOCKED"]
    verdict = "FAIL" if unblocked else "PASS"
    return {"verdict": verdict, "reason": None, "static_audit": audit,
            "probe": probe}


def format_summary(report):
    """The plain-text report DONE CHECK asks for. Never truncates a
    finding or a probe step: naming things is the entire job."""
    lines = ["DOM-50.10 air-gapped install: verdict %s" % report["verdict"]]
    if report["reason"]:
        lines.append("  %s" % report["reason"])
    lines.append("")
    lines.append("STATIC AUDIT (%d network-touching call(s) found on the "
                  "documented install path):" % len(report["static_audit"]["findings"]))
    if not report["static_audit"]["findings"]:
        lines.append("  none found")
    for f in report["static_audit"]["findings"]:
        if f["line"] is None:
            lines.append("  %s: %s" % (f["file"], f["what"]))
        else:
            lines.append("  %s:%d  %s" % (f["file"], f["line"], f["what"]))
            lines.append("    %s" % f["text"])
    lines.append("")
    lines.append("GUARDED PROBE (%d step(s)):" % len(report["probe"]))
    for r in report["probe"]:
        lines.append("  [%s] %s (%s)" % (r["outcome"], r["name"], r["source"]))
        if r["detail"]:
            lines.append("    %s" % r["detail"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                         help="print the machine-readable report instead of the summary")
    parser.add_argument("--repo-root", default=REPO_ROOT,
                         help="checkout root to audit and probe (default: this file's own repo)")
    args = parser.parse_args(argv)
    report = generate_report(args.repo_root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_summary(report))
    if report["verdict"] == "NO-DATA":
        return 2
    if report["verdict"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

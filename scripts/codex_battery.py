#!/usr/bin/env python3
"""X8/C7.2 Codex battery: proves the public Brother plugin works on a real
signed-in Codex, at a given tag (default v1.0.8, the first tag that ships
brothermode as a sibling Codex plugin beside brother; see --tag/--prev-tag).
See /Users/khalil.maaouni/brother-hub/docs/codex/SMOKE-RUNBOOK.md.

Idempotent per leg. Rerunning replays every leg (each leg recreates its own
throwaway state); it never touches the real ~/.codex except to read
config.toml/hooks.json for the before/after witness and to symlink auth.json
into each throwaway CODEX_HOME.

--prep-only runs the credential-free legs (B1-B5, B10) and stops.
--signed-in runs the legs that need a real signed-in Codex (B6-B9); it does
NOT recreate the isolated home, so it reuses whatever a prior --prep-only run
already installed there (only the toy checkout is refreshed).
With neither flag every leg runs in order, same as before these flags
existed.
"""
import argparse
import datetime
import json
import os
import re
import tempfile
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
EVID = os.path.join(HOME, ".claude/evidence/codex-battery")
REAL_HOME = os.path.join(HOME, ".codex")
HOME_A = os.path.join(HOME, "codex-x8-home")
HOME_B = os.path.join(HOME, "codex-x8-home-b")
TOY = os.path.join(HOME, "codex-x8-toy")
TOY_B7 = os.path.join(HOME, "codex-x8-toy-b7")
TOY_B8 = os.path.join(HOME, "codex-x8-toy-b8")
SUMMARY = os.path.join(EVID, "SUMMARY.txt")

#: Tag under test and the tag B8's upgrade leg upgrades FROM. Set from argv
#: in main(); the module-level defaults below are what --selftest and any
#: direct function call see if main() never runs.
TAG = "v1.0.8"
PREV_TAG = "v1.0.7"
#: Set from TAG in main() via pubclone_dir_for(); a plain global (not a
#: default argument) so every leg function reads the current value at call
#: time, never one captured at import time.
PUBCLONE = None
#: Set by leg_b2: whether brothermode@brother installed on HOME_A. leg_b3
#: reads it to decide whether the list leg expects brothermode or not.
BROTHERMODE_INSTALLED = False

CODEX_BUNDLED = "/Applications/ChatGPT.app/Contents/Resources/codex"
CODEX_NPM = os.path.join(HOME, ".local/bin/codex")

#: This file ships beside codex_smoke.py, so its sibling import needs no
#: worktree path (the evidence-directory copy used to pin one by hand).
WORKTREE_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKTREE_SCRIPTS)
from codex_smoke import documented_argv, TASK_SENTENCE  # noqa: E402

os.makedirs(EVID, exist_ok=True)

LEG_ORDER = ["B1", "B2", "B3", "B4", "B5", "B10", "B6", "B7", "B8", "B9"]
RESULTS = {}


def now():
    return datetime.datetime.now().strftime("%H:%M:%S")


def say(msg):
    print("[%s] %s" % (now(), msg), flush=True)


def sh(argv, env=None, cwd=None, timeout=300):
    try:
        return subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 99, "", "run_battery: %s" % exc)


def write_log(path, *parts):
    with open(path, "w", encoding="utf-8") as fh:
        for p in parts:
            fh.write(p if p is not None else "")


def record(leg, verdict, exit_code, decisive, logpath):
    RESULTS[leg] = (verdict, exit_code, decisive.replace("\n", " ")[:300], logpath)
    say("%s -> %s exit=%s :: %s" % (leg, verdict, exit_code, decisive[:200]))


def write_summary():
    with open(SUMMARY, "w", encoding="utf-8") as fh:
        fh.write("tag=%s prev-tag=%s\n" % (TAG, PREV_TAG))
        for leg in LEG_ORDER:
            if leg in RESULTS:
                verdict, rc, decisive, logpath = RESULTS[leg]
            else:
                verdict, rc, decisive, logpath = ("NO-DATA", "", "not yet run", "")
            fh.write("leg=%s verdict=%s exit=%s decisive=%s log=%s\n" %
                      (leg, verdict, rc, decisive, logpath))


def capture_real_home(outfile):
    lines = ["=== ls -la %s ===" % REAL_HOME]
    p = sh(["ls", "-la", REAL_HOME])
    lines.append(p.stdout + p.stderr)
    for name in ("config.toml", "hooks.json"):
        path = os.path.join(REAL_HOME, name)
        if os.path.isfile(path):
            p = sh(["shasum", "-a", "256", path])
            lines.append(p.stdout.strip())
        else:
            lines.append("NO-DATA: %s absent" % path)
    with open(outfile, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def setup_home(home):
    if os.path.isdir(home):
        shutil.rmtree(home)
    os.makedirs(home, exist_ok=True)
    link = os.path.join(home, "auth.json")
    src = os.path.join(REAL_HOME, "auth.json")
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link)
    os.symlink(src, link)


def env_for(home):
    env = dict(os.environ)
    env["CODEX_HOME"] = home
    env["HOME"] = home
    return env


def setup_toy(toy_dir):
    if os.path.isdir(toy_dir):
        shutil.rmtree(toy_dir)
    os.makedirs(toy_dir, exist_ok=True)
    sh(["git", "init", "-q", "."], cwd=toy_dir)
    with open(os.path.join(toy_dir, "mathlib.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a + b\n")
    with open(os.path.join(toy_dir, "test_mathlib.py"), "w") as fh:
        fh.write(
            "import unittest\n\nfrom mathlib import add\n\n\n"
            "class AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n\n\n"
            'if __name__ == "__main__":\n'
            "    unittest.main()\n")
    sh(["git", "add", "-A"], cwd=toy_dir)
    sh(["git", "-c", "user.email=khalil.maaouni@lascenti.com",
        "-c", "user.name=khalil.maaouni", "commit", "-q", "-m", "toy"],
       cwd=toy_dir)


def pubclone_dir_for(tag):
    """Where the read-only reference checkout of the public repo at `tag`
    lives, derived so each tag under test gets its own clone rather than
    silently reusing a stale one."""
    return os.path.join(EVID, "public-clone-%s" % tag)


def classify_plugin_add(body, returncode, plugin_id):
    """Pure classifier for one `codex plugin add <plugin_id> --json` call.
    Separated from the leg functions so --selftest can exercise it on canned
    strings with no Codex call. NO-DATA (never a pass) covers the documented
    pre-v1.0.8 shape: brothermode is not yet in the marketplace, so its add
    is expected to be refused, not a battery defect."""
    if returncode == 0 and ('"pluginId": "%s"' % plugin_id) in body:
        ver = re.search(r'"version"\s*:\s*"([^"]+)"', body)
        return "PASS", "pluginId %s version=%s" % (
            plugin_id, ver.group(1) if ver else "NO-DATA")
    if "not found in marketplace" in body.lower():
        return "NO-DATA", ("%s not found in marketplace (expected shape "
                            "before v1.0.8)" % plugin_id)
    return "FAIL", body.strip()[-200:] or "NO-DATA"


def check_available_plugins(body, want_brothermode):
    """Pure classifier for `codex plugin list --available --json`. brother
    is always required; brothermode is only required when the caller says
    the tag under test is expected to carry it (want_brothermode)."""
    has_brother = '"pluginId": "brother@brother"' in body
    has_brothermode = '"pluginId": "brothermode@brother"' in body
    if not has_brother:
        return "FAIL", body.strip()[-200:] or "NO-DATA"
    if want_brothermode is None:
        return "NO-DATA", "unknown tag: no expectation for brothermode@brother"
    if not want_brothermode:
        if has_brothermode:
            return "FAIL", "brothermode@brother still offered; brother@brother must be the only plugin"
        return "PASS", "brother@brother present, brothermode@brother absent by design"
    if has_brothermode:
        return "PASS", "brother@brother and brothermode@brother present"
    return "NO-DATA", ("brother@brother present, brothermode@brother absent "
                        "(expected shape before v1.0.8)")


def setup_public_clone():
    if not os.path.isdir(os.path.join(PUBCLONE, ".git")):
        setup_log = os.path.join(EVID, "public-clone-setup.log")
        p1 = sh(["git", "clone", "--quiet",
                 "https://github.com/khalilmaaouni/Brother", PUBCLONE],
                timeout=180)
        p2 = sh(["git", "checkout", "--quiet", TAG], cwd=PUBCLONE)
        write_log(setup_log, p1.stdout, p1.stderr, "\n", p2.stdout, p2.stderr)


def leg_b1():
    logf = os.path.join(EVID, "B1-marketplace-add.log")
    p = sh([CODEX_BUNDLED, "plugin", "marketplace", "add",
            "https://github.com/khalilmaaouni/Brother", "--ref", TAG],
           env=env_for(HOME_A), timeout=120)
    body = p.stdout + p.stderr
    write_log(logf, "$ codex plugin marketplace add ... --ref %s\n" % TAG, body)
    if p.returncode == 0 and "Added marketplace `brother`" in body:
        record("B1", "PASS", p.returncode, "Added marketplace `brother`", logf)
    elif "already added" in body.lower():
        record("B1", "PASS", p.returncode,
                "idempotent rerun: " + body.strip().splitlines()[-1], logf)
    else:
        record("B1", "FAIL", p.returncode, body.strip()[-200:] or "NO-DATA", logf)


def leg_b2():
    logf = os.path.join(EVID, "B2-plugin-add.log")
    lines = []

    p1 = sh([CODEX_BUNDLED, "plugin", "add", "brother@brother", "--json"],
            env=env_for(HOME_A), timeout=120)
    lines.append("$ codex plugin add brother@brother --json\n" +
                 p1.stdout + p1.stderr)
    v1, d1 = classify_plugin_add(p1.stdout + p1.stderr, p1.returncode,
                                  "brother@brother")

    p2 = sh([CODEX_BUNDLED, "plugin", "add", "brothermode@brother", "--json"],
            env=env_for(HOME_A), timeout=120)
    lines.append("$ codex plugin add brothermode@brother --json\n" +
                 p2.stdout + p2.stderr)
    v2, d2 = classify_plugin_add(p2.stdout + p2.stderr, p2.returncode,
                                  "brothermode@brother")

    write_log(logf, "\n\n".join(lines))

    global BROTHERMODE_INSTALLED
    BROTHERMODE_INSTALLED = (v2 == "PASS")

    if v1 == "FAIL" or v2 == "FAIL":
        verdict = "FAIL"
    elif v1 == "PASS" and v2 == "PASS":
        verdict = "PASS"
    else:
        verdict = "NO-DATA"
    record("B2", verdict, max(p1.returncode, p2.returncode),
           "brother: %s (%s); brothermode: %s (%s)" % (v1, d1, v2, d2), logf)


def leg_b3():
    logf = os.path.join(EVID, "B3-plugin-list-available.log")
    p = sh([CODEX_BUNDLED, "plugin", "list", "--available", "--json"],
           env=env_for(HOME_A), timeout=60)
    body = p.stdout + p.stderr
    write_log(logf, "$ codex plugin list --available --json\n", body)
    if p.returncode != 0:
        record("B3", "FAIL", p.returncode, body.strip()[-200:] or "NO-DATA", logf)
        return
    verdict, decisive = check_available_plugins(body, BROTHERMODE_INSTALLED)
    record("B3", verdict, p.returncode, decisive, logf)


def leg_b4():
    setup_public_clone()
    logf = os.path.join(EVID, "B4-hooks-install.log")
    script = os.path.join(PUBCLONE, "scripts", "codex_hooks_install.py")
    exported = os.path.isfile(script)
    p = sh(["python3", script, "--codex-home", HOME_A, "--trust",
            "--cwd", TOY], timeout=120)
    body = p.stdout + p.stderr
    write_log(logf,
              "# codex_hooks_install.py exported at %s repo root: %s\n"
              % (TAG, exported),
              "$ python3 %s --codex-home %s --trust --cwd %s\n" %
              (script, HOME_A, TOY), body)
    if p.returncode == 0 and "PASS: codex reports all 18 hook(s) trusted and enabled" in body:
        record("B4", "PASS", p.returncode,
               "18 hooks trusted (source: fresh public clone %s)" % TAG, logf)
    else:
        record("B4", "FAIL", p.returncode, body.strip()[-200:] or "NO-DATA", logf)


def leg_b5():
    logf = os.path.join(EVID, "B5-toy-setup.log")
    p1 = sh(["git", "log", "--oneline", "-1"], cwd=TOY)
    p2 = sh(["git", "status", "--short"], cwd=TOY)
    ok = (os.path.isfile(os.path.join(TOY, "mathlib.py")) and
          os.path.isfile(os.path.join(TOY, "test_mathlib.py")) and
          p1.returncode == 0 and p1.stdout.strip())
    write_log(logf, "$ git log --oneline -1\n", p1.stdout, p1.stderr,
              "\n$ git status --short\n", p2.stdout, p2.stderr)
    if ok:
        record("B5", "PASS", 0, "toy committed: %s" % p1.stdout.strip(), logf)
    else:
        record("B5", "FAIL", 1, "toy not ready", logf)


def leg_b10():
    logf = os.path.join(EVID, "B10-codex-smoke-regression.log")
    worktree_root = WORKTREE_SCRIPTS.rsplit("/scripts", 1)[0]
    p = sh(["python3", "scripts/codex_smoke.py"], cwd=worktree_root, timeout=600)
    body = p.stdout + p.stderr
    write_log(logf, "$ ( cd <worktree checkout> && python3 scripts/codex_smoke.py )\n"
              "# substituted the session's own worktree checkout for the shared\n"
              "# /Users/khalil.maaouni/brother-hub path: this agent runs isolated\n"
              "# in a worktree and git/tree operations against the shared checkout\n"
              "# are refused by the single writer fence.\n", body)
    m = re.search(r"^(PASS|FAIL|NO-DATA).*$", body, re.MULTILINE)
    decisive = m.group(0) if m else (body.strip().splitlines()[-1] if body.strip() else "NO-DATA")
    verdict = "PASS" if p.returncode == 0 and decisive.startswith("PASS") else (
        "NO-DATA" if decisive.startswith("NO-DATA") else "FAIL")
    record("B10", verdict, p.returncode, decisive, logf)


def seconds_until_1800_jst():
    jst = datetime.timezone(datetime.timedelta(hours=9))
    n = datetime.datetime.now(jst)
    target = n.replace(hour=18, minute=0, second=0, microsecond=0)
    if n >= target:
        return 0
    return (target - n).total_seconds()


def wait_for_gate():
    secs = seconds_until_1800_jst()
    if secs <= 0:
        say("gate: already past 18:00 JST, proceeding to credentialled legs")
        return
    say("gate: %.0fs remaining until 18:00 JST; sleeping once (no signed-in "
        "codex exec runs before then)" % secs)
    write_summary()
    time.sleep(secs)
    secs2 = seconds_until_1800_jst()
    if secs2 > 0:
        say("gate: clock check after wake shows %.0fs still remaining, "
            "sleeping the remainder" % secs2)
        time.sleep(secs2)
    say("gate: 18:00 JST reached, proceeding")


def run_codex_exec(argv, home, cwd, logf, timeout_s=1200):
    """Run one codex exec turn, whole output captured to logf, hard timeout,
    watcher that exits once the process is actually dead."""
    env = env_for(home)
    with open(logf, "w", encoding="utf-8") as fh:
        fh.write("$ %s\n" % " ".join(argv))
        fh.flush()
        proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT,
                                 cwd=cwd, env=env)
        start = time.time()
        marker_seen = False
        while True:
            rc = proc.poll()
            if rc is not None:
                return rc
            if time.time() - start > timeout_s:
                proc.kill()
                proc.wait()
                fh.write("\nWATCHER: hard timeout %ss reached, process killed\n"
                          % timeout_s)
                fh.flush()
                return 124
            if not marker_seen:
                try:
                    with open(logf, "r", encoding="utf-8", errors="replace") as rfh:
                        tail = rfh.read()[-2000:]
                    if re.search(r"^tokens used\s*$", tail, re.MULTILINE):
                        marker_seen = True
                        say("watcher: end marker 'tokens used' seen in %s, "
                            "waiting for process exit" % os.path.basename(logf))
                except OSError:
                    pass
            time.sleep(5)


def count_hooks(body):
    return {
        "SessionStart": len(re.findall(r"^hook: SessionStart$", body, re.MULTILINE)),
        "PreToolUse": len(re.findall(r"^hook: PreToolUse$", body, re.MULTILINE)),
        "PostToolUse": len(re.findall(r"^hook: PostToolUse$", body, re.MULTILINE)),
        "Stop": len(re.findall(r"^hook: Stop$", body, re.MULTILINE)),
    }


_AUTH_401 = r"(?<![0-9a-fA-F])401(?![0-9a-fA-F])"


def auth_failed(body):
    """True only for a real authentication failure: an HTTP 401, a status
    code 401, "401 Unauthorized", the bare word "Unauthorized", "not logged
    in", "login required", or "out of credits" (case insensitive). A bare
    401 inside a longer hex or path token (a worktree id like
    agent-ab1de7f03e9e0c401, for example) never matches: the digits must not
    be preceded or followed by another hex character."""
    patterns = [
        r"HTTP[ /]*" + _AUTH_401,
        r"status(?:_code)?\W*" + _AUTH_401,
        _AUTH_401 + r"\s+Unauthorized",
        r"\bUnauthorized\b",
        r"\bnot logged in\b",
        r"\blogin required\b",
        r"\bout of credits\b",
    ]
    return any(re.search(pat, body, re.IGNORECASE) for pat in patterns)


def find_receipt(body, search_roots):
    """A zip named in the transcript (older receipts), or an absolute
    receipt.json path named in the transcript (current brother_run shape:
    "brother_run: receipt: /path/.../receipt.json" or the markdown link
    "[Brother receipt](/path/.../receipt.json)"), or a brother*.zip found by
    walking search_roots. Every candidate is verified to exist on disk
    before it is returned."""
    zips = re.findall(r"[^\s\"]+\.zip", body)
    for z in zips:
        if os.path.isfile(z):
            return z
    receipt_jsons = re.findall(r"(/[^\s()\"]+receipt\.json)", body)
    for rj in receipt_jsons:
        if os.path.isfile(rj):
            return rj
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith(".zip") and "brother" in f.lower():
                    return os.path.join(dirpath, f)
    return None


def receipt_under_temp(path):
    """True when `path` sits under /tmp, /private/tmp or the process temp
    directory: a receipt there is lost at reboot, so it is not evidence."""
    real = os.path.realpath(path)
    for base in (tempfile.gettempdir(), "/tmp", "/private/tmp"):
        try:
            base_real = os.path.realpath(base)
        except OSError:
            continue
        if real == base_real or real.startswith(base_real + os.sep):
            return True
    return False


def receipt_proves_change(receipt_json):
    """Two or more scope.changed entries, each check_passed_before False,
    exit_code 0 and state verified. Anything else, including a receipt that
    is not a dict, is False (never a pass on a shape nobody checked)."""
    if not isinstance(receipt_json, dict):
        return False
    changed = (receipt_json.get("scope") or {}).get("changed") or []
    if len(changed) < 2:
        return False
    for entry in changed:
        if not isinstance(entry, dict):
            return False
        if entry.get("check_passed_before") is not False:
            return False
        if entry.get("exit_code") != 0 or entry.get("state") != "verified":
            return False
    return True


#: Which tags still ship brothermode@brother in the Codex marketplace. Up to
#: v1.0.8 it was offered beside brother@brother; from the portability release
#: on, brother@brother carries both products' hooks itself and brothermode is
#: ABSENT by design, so its plugin add must be refused. An unknown tag reads
#: None, which every caller turns into NO-DATA rather than a guess.
BROTHERMODE_EXPECTED = {"v1.0.6": True, "v1.0.7": True, "v1.0.8": True}


def expect_brothermode(tag):
    """True (offered), False (absent by design) or None (unknown tag)."""
    if tag in BROTHERMODE_EXPECTED:
        return BROTHERMODE_EXPECTED[tag]
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", tag or "")
    if not m:
        return None
    return False if tuple(int(x) for x in m.groups()) > (1, 0, 8) else True


def classify_brothermode_add_absent(body, returncode):
    """The post-1.0.8 rule for `codex plugin add brothermode@brother`: the
    refusal IS the pass, an install is the defect."""
    if returncode == 0 and '"pluginId": "brothermode@brother"' in body:
        return "FAIL", "brothermode@brother still installable; the marketplace must offer brother@brother only"
    if "not found in marketplace" in body.lower():
        return "PASS", "brothermode@brother refused: not found in marketplace (absent by design)"
    return "NO-DATA", body.strip()[-200:] or "NO-DATA"


def score_b6(rc, body, logf, binary_answered):
    """The B6 verdict logic, shared by leg_b6 (a live run) and rescore()
    (replaying an existing log with no codex call). Writes
    B6-receipt-listing.txt as a side effect. Returns (verdict, rc, decisive,
    logpath), the same shape record() stores."""
    hooks = count_hooks(body)
    receipt = find_receipt(body, [TOY, os.environ.get("TMPDIR", "/tmp")])
    tokens_m = re.search(r"^tokens used\s*\n([0-9,]+)", body, re.MULTILINE)
    tokens = tokens_m.group(1) if tokens_m else "NO-DATA"
    used_unittest = "unittest" in body
    hooks_fired = all(hooks[h] > 0 for h in
                       ("SessionStart", "PreToolUse", "PostToolUse", "Stop"))

    receipt_json = None
    receipt_parses = False
    receipt_contents = "NO-DATA: no zip/receipt path found in transcript"
    if receipt and receipt.endswith(".json"):
        try:
            with open(receipt, "r", encoding="utf-8") as rfh:
                receipt_json = json.load(rfh)
            receipt_parses = True
        except (OSError, ValueError) as exc:
            receipt_contents = "NO-DATA: receipt found but did not parse as JSON: %s" % exc
        if receipt_parses:
            keys = sorted(receipt_json.keys()) if isinstance(receipt_json, dict) else []
            def snippet(k):
                return str(receipt_json.get(k))[:300] if isinstance(receipt_json, dict) else "NO-DATA"
            receipt_contents = (
                "top level keys: %s\n\nintent: %s\n\nscope: %s\n\nreport: %s\n"
                % (", ".join(keys), snippet("intent"), snippet("scope"), snippet("report")))
    elif receipt:
        lp = sh(["unzip", "-l", receipt])
        receipt_contents = lp.stdout.strip()[:500]
        receipt_parses = True  # zip receipts are the older shape; unzip -l succeeding is enough

    # Portability release rules (2026-09-06). A receipt under a temp
    # directory is a FAIL, not a pass: the founder's order is that every
    # target is durable, and the v1.0.8 receipt landed under /private/tmp.
    # A PASS also needs the receipt to prove the change: two or more changed
    # files, each with check_passed_before False (the check was red before
    # the work), exit_code 0 and state verified.
    not_durable = bool(receipt) and receipt_under_temp(receipt)
    proves_change = receipt_proves_change(receipt_json) if receipt_parses else False
    ok = (rc == 0 and hooks_fired and used_unittest and receipt and receipt_parses
          and not not_durable and proves_change)
    decisive = ("binary=%s hooks=%s tokens=%s receipt=%s" %
                (binary_answered, hooks, tokens, receipt or "NO-DATA"))
    if not_durable:
        decisive += " receipt not durable: %s" % receipt
    elif receipt_parses and not proves_change:
        decisive += " receipt does not prove the change (needs 2+ changed files, check_passed_before false, exit 0, verified)"
    verdict = "PASS" if ok else ("NO-DATA" if not receipt else "FAIL")
    with open(os.path.join(EVID, "B6-receipt-listing.txt"), "w") as fh:
        fh.write("receipt path: %s\n\n%s\n" % (receipt, receipt_contents))
    return verdict, rc, decisive, logf


def leg_b6():
    logf = os.path.join(EVID, "B6-real-task.log")
    argv = documented_argv(CODEX_BUNDLED, TOY)
    say("B6: running documented command on app-bundled binary (20 min hard timeout)")
    rc = run_codex_exec(argv, HOME_A, TOY, logf)
    with open(logf, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()
    binary_answered = "app-bundled (%s)" % CODEX_BUNDLED

    if rc == 124 or auth_failed(body) or rc != 0:
        say("B6: bundled binary did not produce a clean pass (rc=%s); "
            "retrying step 6 alone on the npm binary" % rc)
        logf2 = os.path.join(EVID, "B6-real-task-npm-retry.log")
        argv2 = documented_argv(CODEX_NPM, TOY)
        rc2 = run_codex_exec(argv2, HOME_A, TOY, logf2)
        with open(logf2, "r", encoding="utf-8", errors="replace") as fh:
            body2 = fh.read()
        if rc2 == 0 and not auth_failed(body2):
            rc, body, logf = rc2, body2, logf2
            binary_answered = "npm (%s)" % CODEX_NPM
        else:
            hooks = count_hooks(body + body2)
            record("B6", "FAIL", rc,
                   "both binaries failed: bundled rc=%s npm rc=%s; hooks=%s"
                   % (rc, rc2, hooks), logf + "," + logf2)
            return

    verdict, rc, decisive, logpath = score_b6(rc, body, logf, binary_answered)
    record("B6", verdict, rc, decisive, logpath)


def leg_b7():
    setup_toy(TOY_B7)
    logf = os.path.join(EVID, "B7-negative-no-git-grant.log")
    argv = [CODEX_BUNDLED, "exec", "-s", "workspace-write", "-C", TOY_B7,
            TASK_SENTENCE]
    say("B7: running the same turn WITHOUT the writable_roots grant (negative leg)")
    rc = run_codex_exec(argv, HOME_A, TOY_B7, logf)
    with open(logf, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()
    refused = ("isolation could not be established" in body or
               "could not create leading directories" in body.lower() or
               "operation not permitted" in body.lower())
    receipt = find_receipt(body, [TOY_B7, os.environ.get("TMPDIR", "/tmp")])
    if refused:
        record("B7", "PASS", rc,
               "documented isolation refusal reproduced without the grant", logf)
    elif receipt or (rc == 0 and "TypeError" in body):
        record("B7", "FAIL", rc,
               "FINDING: turn appears to have succeeded without the grant; "
               "documented isolation refusal did not occur", logf)
    else:
        record("B7", "NO-DATA", rc, "no refusal text and no receipt found", logf)


def leg_b8():
    setup_home(HOME_B)
    logf = os.path.join(EVID, "B8-upgrade-path.log")
    lines = []

    p = sh([CODEX_BUNDLED, "plugin", "marketplace", "add",
            "https://github.com/khalilmaaouni/Brother", "--ref", PREV_TAG],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ marketplace add --ref %s\n" % PREV_TAG + p.stdout + p.stderr)

    p = sh([CODEX_BUNDLED, "plugin", "add", "brother@brother", "--json"],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ plugin add brother@brother (%s)\n" % PREV_TAG +
                 p.stdout + p.stderr)
    ver_before = re.search(r'"version"\s*:\s*"([^"]+)"', p.stdout + p.stderr)

    p = sh([CODEX_BUNDLED, "plugin", "add", "brothermode@brother", "--json"],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ plugin add brothermode@brother (%s)\n" % PREV_TAG +
                 p.stdout + p.stderr)
    bm_before_verdict, bm_before_decisive = classify_plugin_add(
        p.stdout + p.stderr, p.returncode, "brothermode@brother")

    p = sh([CODEX_BUNDLED, "plugin", "marketplace", "remove", "brother"],
           env=env_for(HOME_B), timeout=60)
    lines.append("$ marketplace remove brother\n" + p.stdout + p.stderr)
    p = sh([CODEX_BUNDLED, "plugin", "marketplace", "add",
            "https://github.com/khalilmaaouni/Brother", "--ref", TAG],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ marketplace add --ref %s\n" % TAG + p.stdout + p.stderr)

    p = sh([CODEX_BUNDLED, "plugin", "add", "brother@brother", "--json"],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ plugin add brother@brother (%s)\n" % TAG + p.stdout + p.stderr)
    ver_after = re.search(r'"version"\s*:\s*"([^"]+)"', p.stdout + p.stderr)

    p = sh([CODEX_BUNDLED, "plugin", "add", "brothermode@brother", "--json"],
           env=env_for(HOME_B), timeout=120)
    lines.append("$ plugin add brothermode@brother (%s)\n" % TAG +
                 p.stdout + p.stderr)
    expected = expect_brothermode(TAG)
    if expected is False:
        bm_after_verdict, bm_after_decisive = classify_brothermode_add_absent(
            p.stdout + p.stderr, p.returncode)
    else:
        bm_after_verdict, bm_after_decisive = classify_plugin_add(
            p.stdout + p.stderr, p.returncode, "brothermode@brother")

    p = sh([CODEX_BUNDLED, "plugin", "list", "--available", "--json"],
           env=env_for(HOME_B), timeout=60)
    lines.append("$ plugin list --available --json (post-upgrade)\n" +
                 p.stdout + p.stderr)
    list_verdict, list_decisive = check_available_plugins(
        p.stdout + p.stderr, want_brothermode=expected)

    setup_public_clone()
    hp = sh(["python3", os.path.join(PUBCLONE, "scripts", "codex_hooks_install.py"),
             "--codex-home", HOME_B, "--trust", "--cwd", TOY_B8], timeout=120)
    lines.append("$ hooks install (home B)\n" + hp.stdout + hp.stderr)

    setup_toy(TOY_B8)
    write_log(logf, "\n\n".join(lines))

    argv = documented_argv(CODEX_BUNDLED, TOY_B8)
    task_logf = os.path.join(EVID, "B8-task-run.log")
    say("B8: running the toy task once after upgrading %s -> %s" % (PREV_TAG, TAG))
    rc = run_codex_exec(argv, HOME_B, TOY_B8, task_logf)
    with open(task_logf, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()

    moved = (ver_before and ver_after and
             ver_before.group(1) != ver_after.group(1))
    completed = rc == 0 and not auth_failed(body)
    any_fail = "FAIL" in (bm_before_verdict, bm_after_verdict, list_verdict)
    any_no_data = "NO-DATA" in (bm_before_verdict, bm_after_verdict, list_verdict)
    decisive = ("version %s -> %s, task rc=%s; brothermode %s->%s (%s / %s); "
                "list=%s (%s)" % (
                    ver_before.group(1) if ver_before else "NO-DATA",
                    ver_after.group(1) if ver_after else "NO-DATA", rc,
                    bm_before_verdict, bm_after_verdict, bm_before_decisive,
                    bm_after_decisive, list_verdict, list_decisive))
    if not (moved and completed) or any_fail:
        verdict = "FAIL"
    elif any_no_data:
        verdict = "NO-DATA"
    else:
        verdict = "PASS"
    record("B8", verdict, rc, decisive, logf + "," + task_logf)


def leg_b9():
    logf = os.path.join(EVID, "B9-uninstall.log")
    setup_public_clone()
    script = os.path.join(PUBCLONE, "scripts", "codex_hooks_install.py")

    p1 = sh(["python3", script, "--codex-home", HOME_A, "--uninstall"], timeout=60)
    p2 = sh([CODEX_BUNDLED, "plugin", "remove", "brother@brother"],
            env=env_for(HOME_A), timeout=60)
    p3 = sh([CODEX_BUNDLED, "plugin", "marketplace", "remove", "brother"],
            env=env_for(HOME_A), timeout=60)
    p4 = sh(["python3", script, "--codex-home", HOME_A, "--uninstall"], timeout=60)

    body = "\n".join(["$ hooks --uninstall (1st)\n" + p1.stdout + p1.stderr,
                       "$ plugin remove brother@brother\n" + p2.stdout + p2.stderr,
                       "$ plugin marketplace remove brother\n" + p3.stdout + p3.stderr,
                       "$ hooks --uninstall (2nd)\n" + p4.stdout + p4.stderr])
    write_log(logf, body)

    first_ok = p1.returncode == 0 and "removed" in p1.stdout.lower()
    second_ok = p4.returncode == 0 and "NO-DATA" in (p4.stdout + p4.stderr)
    if first_ok and p2.returncode == 0 and p3.returncode == 0 and second_ok:
        record("B9", "PASS", 0, "uninstall route complete, second run NO-DATA at exit 0", logf)
    else:
        record("B9", "FAIL", 1,
               "p1=%s p2=%s p3=%s p4=%s" % (p1.returncode, p2.returncode,
                                             p3.returncode, p4.returncode), logf)


def build_argparser():
    p = argparse.ArgumentParser(
        description="X8/C7.2 Codex battery for the public Brother plugin.")
    p.add_argument("--tag", default="v1.0.8",
                    help="tag under test (default v1.0.8)")
    p.add_argument("--prev-tag", default="v1.0.7",
                    help="prior tag the B8 upgrade leg upgrades from (default v1.0.7)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--prep-only", action="store_true",
                        help="run only the credential-free legs (B1-B5, B10) and stop")
    group.add_argument("--signed-in", action="store_true",
                        help="run only the legs needing a real signed-in Codex (B6-B9); "
                             "reuses the isolated home a prior --prep-only run set up")
    p.add_argument("--selftest", action="store_true",
                    help="exercise argument parsing, tag derivation and the "
                         "both-plugins assertion on canned strings, no Codex call")
    p.add_argument("--rescore", action="store_true",
                    help="re-judge B6 (and B8 when its FAIL is the same "
                         "receipt-detection defect) from the logs already on "
                         "disk, no Codex call, rewriting only those leg= "
                         "lines in SUMMARY.txt")
    return p


def selftest():
    p = build_argparser()

    ns = p.parse_args([])
    assert ns.tag == "v1.0.8", ns.tag
    assert ns.prev_tag == "v1.0.7", ns.prev_tag
    assert ns.prep_only is False and ns.signed_in is False and ns.selftest is False
    assert ns.rescore is False, ns.rescore

    ns2 = p.parse_args(["--tag", "v2.0.0", "--prev-tag", "v1.0.8", "--prep-only"])
    assert ns2.tag == "v2.0.0", ns2.tag
    assert ns2.prev_tag == "v1.0.8", ns2.prev_tag
    assert ns2.prep_only is True and ns2.signed_in is False

    ns3 = p.parse_args(["--signed-in"])
    assert ns3.signed_in is True and ns3.prep_only is False

    try:
        p.parse_args(["--prep-only", "--signed-in"])
        raise AssertionError("expected --prep-only and --signed-in to be mutually exclusive")
    except SystemExit:
        pass

    assert pubclone_dir_for("v1.0.8") == os.path.join(EVID, "public-clone-v1.0.8")
    assert pubclone_dir_for("v1.0.7") == os.path.join(EVID, "public-clone-v1.0.7")

    both_present = ('{"plugins": [{"pluginId": "brother@brother", "version": "1.0.8"}, '
                     '{"pluginId": "brothermode@brother", "version": "1.0.8"}]}')
    v, d = check_available_plugins(both_present, want_brothermode=True)
    assert v == "PASS", (v, d)

    only_brother = '{"plugins": [{"pluginId": "brother@brother", "version": "1.0.7"}]}'
    v, d = check_available_plugins(only_brother, want_brothermode=True)
    assert v == "NO-DATA", (v, d)
    v, d = check_available_plugins(only_brother, want_brothermode=False)
    assert v == "PASS", (v, d)

    neither = '{"plugins": []}'
    v, d = check_available_plugins(neither, want_brothermode=True)
    assert v == "FAIL", (v, d)

    add_pass = '{"pluginId": "brothermode@brother", "version": "1.0.8"}'
    v, d = classify_plugin_add(add_pass, 0, "brothermode@brother")
    assert v == "PASS" and "1.0.8" in d, (v, d)

    add_no_data = 'Error: plugin "brothermode" not found in marketplace "brother"'
    v, d = classify_plugin_add(add_no_data, 1, "brothermode@brother")
    assert v == "NO-DATA", (v, d)

    add_fail = "Error: something else went wrong"
    v, d = classify_plugin_add(add_fail, 1, "brothermode@brother")
    assert v == "FAIL", (v, d)

    assert auth_failed("agent-ab1de7f03e9e0c401") is False
    assert auth_failed("HTTP 401 Unauthorized") is True
    assert auth_failed("ERROR: Your workspace is out of credits") is True

    print("selftest OK: argument parsing, mutual exclusion, tag derivation, "
          "both-plugins assertion all passed")
    return 0


def rescore_b6_line(old_line):
    """Re-judge B6 from B6-real-task.log on disk, no Codex call. old_line is
    the pre-rescore 'leg=B6 ...' line from SUMMARY.txt; its exit= and
    binary= fields are facts about a run already captured on disk (the
    process exit code cannot be recovered from the log body alone), never
    re-derived by guessing. Returns the new 'leg=B6 ...\n' line."""
    m_exit = re.search(r"exit=(\S+)", old_line)
    rc = int(m_exit.group(1)) if m_exit and re.match(r"^-?\d+$", m_exit.group(1)) else 0
    m_bin = re.search(r"binary=(.*?) hooks=", old_line)
    binary_answered = m_bin.group(1) if m_bin else "NO-DATA"
    logf = os.path.join(EVID, "B6-real-task.log")
    with open(logf, "r", encoding="utf-8", errors="replace") as fh:
        rbody = fh.read()
    verdict, rc, decisive, logpath = score_b6(rc, rbody, logf, binary_answered)
    decisive = decisive.replace("\n", " ")[:300]
    return "leg=B6 verdict=%s exit=%s decisive=%s log=%s\n" % (verdict, rc, decisive, logpath)


def rescore_b8_line(old_line):
    """Re-judge B8 from B8-upgrade-path.log and B8-task-run.log on disk, no
    Codex call. leg_b8's own verdict logic is reused unchanged: the
    plugin-add and plugin-list verdicts (bm_before, bm_after, list) and the
    version comparison are facts a prior, still-correct classification
    already computed from B8-upgrade-path.log (classify_plugin_add and
    check_available_plugins are not the defect being fixed), so old_line
    carries them forward exactly as rescore_b6_line carries over exit= and
    binary=. Only "completed" is re-derived, from B8-task-run.log through
    the corrected auth_failed() helper in place of the old "401" in body
    substring check (a worktree path's hex id ending in 401, such as
    agent-ab1de7f03e9e0c401, used to read as an auth failure). Returns the
    new 'leg=B8 ...\n' line, or old_line unchanged if its decisive= field
    does not match the shape leg_b8 writes."""
    m = re.search(
        r"version (\S+) -> (\S+), task rc=(\S+); brothermode (\S+)->(\S+) "
        r"\((.*?) / (.*?)\); list=(\S+) \((.*?)\)",
        old_line)
    if not m:
        return old_line
    (ver_before, ver_after, rc_s, bm_before_verdict, bm_after_verdict,
     bm_before_decisive, bm_after_decisive, list_verdict, list_decisive) = m.groups()
    rc = int(rc_s) if re.match(r"^-?\d+$", rc_s) else 0

    logf = os.path.join(EVID, "B8-upgrade-path.log")
    task_logf = os.path.join(EVID, "B8-task-run.log")
    with open(task_logf, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()

    moved = (ver_before != "NO-DATA" and ver_after != "NO-DATA" and
             ver_before != ver_after)
    completed = rc == 0 and not auth_failed(body)
    any_fail = "FAIL" in (bm_before_verdict, bm_after_verdict, list_verdict)
    any_no_data = "NO-DATA" in (bm_before_verdict, bm_after_verdict, list_verdict)
    decisive = ("version %s -> %s, task rc=%s; brothermode %s->%s (%s / %s); "
                "list=%s (%s)" % (ver_before, ver_after, rc, bm_before_verdict,
                                   bm_after_verdict, bm_before_decisive,
                                   bm_after_decisive, list_verdict, list_decisive))
    if not (moved and completed) or any_fail:
        verdict = "FAIL"
    elif any_no_data:
        verdict = "NO-DATA"
    else:
        verdict = "PASS"
    decisive = decisive.replace("\n", " ")[:300]
    return "leg=B8 verdict=%s exit=%s decisive=%s log=%s\n" % (
        verdict, rc, decisive, logf + "," + task_logf)


def rescore():
    """Re-judge B6 and B8 from the logs already on disk, without running
    codex: B6 from B6-real-task.log (score_b6, unchanged), B8 from
    B8-upgrade-path.log and B8-task-run.log (leg_b8's own verdict logic,
    unchanged, replayed through rescore_b8_line with the corrected
    auth_failed() helper standing in for the old "401" in body substring
    check). Backs up SUMMARY.txt to SUMMARY.txt.bak-b8 and, for the
    pre-existing B6 rescore history, to SUMMARY.txt.bak-try4, before writing
    anything; every line other than leg=B6 and leg=B8 is carried over byte
    for byte."""
    if not os.path.isfile(SUMMARY):
        print("rescore: NO-DATA: no SUMMARY.txt at %s" % SUMMARY)
        return 1

    backup_b8 = os.path.join(EVID, "SUMMARY.txt.bak-b8")
    shutil.copyfile(SUMMARY, backup_b8)
    backup = os.path.join(EVID, "SUMMARY.txt.bak-try4")
    shutil.copyfile(SUMMARY, backup)

    with open(SUMMARY, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    new_lines = []
    for line in lines:
        if line.startswith("leg=B6 "):
            new_lines.append(rescore_b6_line(line))
        elif line.startswith("leg=B8 "):
            new_lines.append(rescore_b8_line(line))
        else:
            new_lines.append(line)

    with open(SUMMARY, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    for line in new_lines:
        if line.startswith("leg=B6 ") or line.startswith("leg=B8 "):
            print(line.rstrip("\n"))
    return 0


def finish_and_report(before):
    after = os.path.join(EVID, "real-home-after.txt")
    capture_real_home(after)
    p = sh(["diff", "-u", before, after])
    with open(os.path.join(EVID, "real-home-diff.txt"), "w") as fh:
        fh.write(p.stdout if p.stdout else "IDENTICAL: no diff between before and after\n")
    write_summary()


def main(args):
    global TAG, PREV_TAG, PUBCLONE, BROTHERMODE_INSTALLED
    TAG = args.tag
    PREV_TAG = args.prev_tag
    PUBCLONE = pubclone_dir_for(TAG)
    BROTHERMODE_INSTALLED = False

    say("run_battery: starting (tag=%s prev-tag=%s)" % (TAG, PREV_TAG))
    before = os.path.join(EVID, "real-home-before.txt")
    if not os.path.isfile(before):
        capture_real_home(before)
        say("captured real-home-before.txt")

    run_prep = not args.signed_in
    run_signed_in = not args.prep_only

    if run_prep:
        setup_home(HOME_A)
        setup_toy(TOY)
        for leg in (leg_b1, leg_b2, leg_b3, leg_b4, leg_b5, leg_b10):
            leg()
            write_summary()

    if not run_signed_in:
        finish_and_report(before)
        say("run_battery: --prep-only complete, stopping before the signed-in legs")
        return

    if not run_prep:
        say("run_battery: --signed-in reusing existing isolated home %s "
            "(not recreated)" % HOME_A)
        setup_toy(TOY)

    wait_for_gate()

    for leg in (leg_b6, leg_b7, leg_b8, leg_b9):
        leg()
        write_summary()

    finish_and_report(before)
    say("run_battery: complete, SUMMARY.txt written")


if __name__ == "__main__":
    _args = build_argparser().parse_args()
    if _args.selftest:
        sys.exit(selftest())
    if _args.rescore:
        sys.exit(rescore())
    main(_args)

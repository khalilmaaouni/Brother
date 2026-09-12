"""
client_parity: the Cursor adapter must stay at parity with the Codex adapter
at every release, enforced by a check rather than by memory.

FOUNDER ORDER 2026-09-12, given when Cursor became an official adapter in
1.0.14, his words: "make sure to keep it up to date with Codex, for each
release".

WHY A CHECK AND NOT A HABIT. Codex reached its test bar over many
releases: a package test, a hook install test, a clean-install smoke, a
signed-in runbook and a release-tag battery. Cursor arrived as a pack with
none of the smoke half. A second client that silently lags the first is a
broken promise, and nothing but memory would have noticed the next Codex
surface landing without its Cursor twin.

WHAT IT DOES. It discovers every tracked path whose name contains "codex",
minus the research and planning trees that are allowed to talk about Codex
without shipping a twin. It requires each discovered path to be a PAIRS key,
to fall under an EXEMPT prefix, or to be a DEBT key. It checks that every
PAIRS entry with a present Codex side has a present Cursor side. It reads
scripts/check_all.sh and requires every registered codex- battery check to
map to a registered cursor- battery check, or to be exempt or in debt. It
compares the bundle plugin versions. It reports open debt and fails when a
debt is due. It reports stale table entries without failing.

WHAT IT GUARANTEES. A release cannot pass this check while the Cursor adapter
is missing a twin for a tracked Codex surface, while a registered Codex
battery check has no registered Cursor twin, while the bundle versions
differ, or while a parity debt has passed its due version.

WHAT IT DOES NOT CLAIM. It does not run the battery. It does not prove that
the Cursor twin is correct, only that it exists and is registered. It does
not replace human review of the twin's behavior. NO-DATA is never a pass: an
unreadable marketplace, a failed git call, or an empty Codex surface exits 2.

Driven backwards by --selftest over throwaway git repositories carrying one
case per refusal.

Python 3, standard library only. No network.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

PAIRS = {
    "bundle/.codex-plugin/plugin.json": "bundle/.cursor-plugin/plugin.json",
    "products/brothermode/.codex-plugin/plugin.json": "products/brothermode/.cursor-plugin/plugin.json",
    "docs/how-to/install-codex.md": "docs/how-to/install-cursor.md",
    "docs/codex/SMOKE-RUNBOOK.md": "docs/cursor/SMOKE-RUNBOOK.md",
    "products/brothermode/docs/runtimes/codex.AGENTS.md": "products/brothermode/docs/runtimes/cursor.AGENTS.md",
    "scripts/codex_smoke.py": "scripts/cursor_smoke.py",
    "scripts/test_codex_smoke.py": "scripts/test_cursor_smoke.py",
    "scripts/test_codex_package.py": "scripts/test_cursor_plugin.py",
    "scripts/codex_hooks_install.py": "scripts/cursor_plugin_install.py",
    "scripts/test_codex_hooks_install.py": "scripts/test_cursor_plugin.py",
    "scripts/codex_product_skills.py": "scripts/cursor_plugin_install.py",
    "scripts/test_codex_product_skills.py": "scripts/test_cursor_plugin.py",
}

EXEMPT = {
    "bundle/codex-skills/": "Codex needs a stripped skill copy; Cursor reads bundle/skills directly through its manifest",
    "scripts/codex_skills.py": "generator for bundle/codex-skills, which Cursor does not need",
    "bundle/runtime/codex_hooks_install.py": "bundled mirror of the Codex hook installer; Cursor plugins carry hooks in the manifest",
}

DEBT = {
    "scripts/codex_battery.py": ("release-tag battery that installs the public tag and runs signed-in legs; no Cursor twin yet", "1.0.15"),
    "scripts/test_codex_battery.py": ("self test of the Codex release-tag battery; no Cursor twin yet", "1.0.15"),
}

BATTERY_PAIRS = {
    "codex-smoke": "cursor-smoke",
    "codex-smoke-self": "cursor-smoke-self",
    "codex-package-self": "cursor-plugin-self",
    "codex-hooks-self": "cursor-hook-run-self",
}

BATTERY_EXEMPT = {
    "codex-skills-current": "Codex needs a stripped skill copy; Cursor reads bundle/skills directly through its manifest",
}

BATTERY_DEBT = {
    "codex-battery-self": ("self test of the Codex release-tag battery; no Cursor twin yet", "1.0.15"),
}


def _version_tuple(value):
    parts = []
    for piece in str(value).split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            parts.append(0)
    return tuple(parts)


def _path_exists(root, relative):
    return os.path.exists(os.path.join(root, relative))


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _git_ls_files(root):
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "git ls-files timed out after 60 seconds"
    except Exception as exc:
        return None, str(exc)

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git ls-files exited with code %d" % result.returncode
        return None, detail

    return result.stdout.splitlines(), None


def _is_exempt_surface(path):
    for key in EXEMPT:
        if key.endswith("/"):
            if path.startswith(key):
                return True
        else:
            if path == key:
                return True
    return False


def run_check(root):
    lines = []
    failures = []

    def fail(message):
        failures.append(message)
        lines.append("FAIL: " + message)

    tracked, git_error = _git_ls_files(root)
    if git_error is not None:
        lines.append("NO-DATA: " + git_error)
        return 2, lines

    codex_surface = []
    for path in tracked:
        lower = path.lower()
        if "codex" not in lower:
            continue
        if path.startswith("docs/plan/"):
            continue
        if path.startswith("docs/handover/"):
            continue
        if path.startswith("docs/decisions/"):
            continue
        if path.startswith("docs/research/"):
            continue
        if "/evidence/" in path:
            continue
        if "/program/" in path:
            continue
        if "/mistakes/" in path:
            continue
        if "/proposals/" in path:
            continue
        if path == "CODEX-REPORT.md":
            continue
        codex_surface.append(path)

    if not codex_surface:
        lines.append("NO-DATA: no Codex surface discovered")
        return 2, lines

    for path in codex_surface:
        if path in PAIRS:
            continue
        if path in DEBT:
            continue
        if _is_exempt_surface(path):
            continue
        fail("new Codex surface %s has no Cursor twin, exemption or debt entry in scripts/client_parity.py" % path)

    for codex_path, cursor_path in PAIRS.items():
        if _path_exists(root, codex_path):
            if not _path_exists(root, cursor_path):
                fail("PAIRS %s -> %s: cursor path missing" % (codex_path, cursor_path))

    check_all_path = os.path.join(root, "scripts/check_all.sh")
    registered = set()
    if os.path.exists(check_all_path):
        with open(check_all_path, "r", encoding="utf-8") as handle:
            for line in handle:
                match = re.match(r'^\s*run_check\s+"([^"]+)"', line)
                if match:
                    registered.add(match.group(1))
    else:
        fail("scripts/check_all.sh not found for battery mapping")

    for name in sorted(registered):
        if not name.startswith("codex-"):
            continue
        if name in BATTERY_PAIRS:
            cursor_name = BATTERY_PAIRS[name]
            if cursor_name not in registered:
                fail("battery codex check %s maps to %s which is not registered in scripts/check_all.sh" % (name, cursor_name))
        elif name in BATTERY_EXEMPT:
            continue
        elif name in BATTERY_DEBT:
            continue
        else:
            fail("battery codex check %s has no Cursor twin, exemption or debt entry in scripts/client_parity.py" % name)

    codex_ver_path = os.path.join(root, "bundle/.codex-plugin/plugin.json")
    cursor_ver_path = os.path.join(root, "bundle/.cursor-plugin/plugin.json")
    if not os.path.exists(codex_ver_path):
        fail("bundle/.codex-plugin/plugin.json is missing")
    if not os.path.exists(cursor_ver_path):
        fail("bundle/.cursor-plugin/plugin.json is missing")
    if os.path.exists(codex_ver_path) and os.path.exists(cursor_ver_path):
        try:
            codex_version = _read_json(codex_ver_path).get("version")
            cursor_version = _read_json(cursor_ver_path).get("version")
            if codex_version != cursor_version:
                fail("bundle versions differ: codex=%s cursor=%s" % (codex_version, cursor_version))
        except Exception as exc:
            fail("cannot read bundle plugin versions: %s" % exc)

    marketplace_path = os.path.join(root, ".claude-plugin/marketplace.json")
    if failures and not os.path.exists(marketplace_path):
        # A FAIL already found decides the exit code; NO-DATA never hides it.
        lines.append("NO-DATA: .claude-plugin/marketplace.json not found, debt dates unchecked")
        lines.append("FAIL: %d parity gap(s)" % len(failures))
        return 1, lines
    if not os.path.exists(marketplace_path):
        lines.append("NO-DATA: .claude-plugin/marketplace.json not found")
        return 2, lines

    try:
        marketplace = _read_json(marketplace_path)
        current = marketplace.get("metadata", {}).get("version")
        if not current:
            lines.append("NO-DATA: .claude-plugin/marketplace.json has no metadata.version")
            return 2, lines
    except Exception as exc:
        lines.append("NO-DATA: cannot read .claude-plugin/marketplace.json: %s" % exc)
        return 2, lines

    current_tuple = _version_tuple(current)
    open_debts = 0

    for path, (reason, until) in DEBT.items():
        if _path_exists(root, path):
            open_debts += 1
            lines.append("DEBT: %s (%s), due by %s" % (path, reason, until))
            if current_tuple >= _version_tuple(until):
                fail("parity debt %s was due by %s and the release is %s" % (path, until, current))

    for name, (reason, until) in BATTERY_DEBT.items():
        if name in registered:
            open_debts += 1
            lines.append("DEBT: %s (%s), due by %s" % (name, reason, until))
            if current_tuple >= _version_tuple(until):
                fail("parity debt %s was due by %s and the release is %s" % (name, until, current))

    for key in PAIRS:
        if not _path_exists(root, key):
            lines.append("STALE: %s, remove it from the table" % key)

    for key in EXEMPT:
        probe = key[:-1] if key.endswith("/") else key
        if not _path_exists(root, probe):
            lines.append("STALE: %s, remove it from the table" % key)

    for key in DEBT:
        if not _path_exists(root, key):
            lines.append("STALE: %s, remove it from the table" % key)

    for key in BATTERY_PAIRS:
        if key not in registered:
            lines.append("STALE: %s, remove it from the table" % key)

    for key in BATTERY_EXEMPT:
        if key not in registered:
            lines.append("STALE: %s, remove it from the table" % key)

    for key in BATTERY_DEBT:
        if key not in registered:
            lines.append("STALE: %s, remove it from the table" % key)

    if failures:
        lines.append("FAIL: %d parity gap(s)" % len(failures))
        return 1, lines

    lines.append(
        "PASS: Cursor is at parity with Codex (%d pairs, %d battery pairs, %d debts open)"
        % (len(PAIRS), len(BATTERY_PAIRS), open_debts)
    )
    return 0, lines


def _run_git(cwd, args, timeout=60):
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    return result.returncode, result.stderr.strip() or result.stdout.strip()


def _make_repo(root):
    _run_git(root, ["init"])
    _run_git(root, ["config", "user.name", "Test"])
    _run_git(root, ["config", "user.email", "test@example.com"])


def _write(root, relative, content):
    path = os.path.join(root, relative)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _commit_all(root, message):
    _run_git(root, ["add", "."])
    _run_git(root, ["commit", "-m", message])


def _base_repo(root, codex_ver="1.0.0", cursor_ver="1.0.0", marketplace_ver="1.0.0", extra_battery=None, include_debt=False):
    _make_repo(root)
    _write(root, "bundle/.codex-plugin/plugin.json", '{"version": "%s"}' % codex_ver)
    _write(root, "bundle/.cursor-plugin/plugin.json", '{"version": "%s"}' % cursor_ver)
    battery_lines = 'run_check "codex-smoke"\nrun_check "cursor-smoke"\n'
    if extra_battery:
        battery_lines += extra_battery + "\n"
    _write(root, "scripts/check_all.sh", battery_lines)
    _write(root, "scripts/codex_smoke.py", "# codex smoke\n")
    _write(root, "scripts/cursor_smoke.py", "# cursor smoke\n")
    _write(root, ".claude-plugin/marketplace.json", '{"metadata": {"version": "%s"}}' % marketplace_ver)
    if include_debt:
        _write(root, "scripts/codex_battery.py", "# codex battery\n")
    _commit_all(root, "base")


def _assert_case(name, condition, detail):
    if condition:
        print("selftest %s: OK" % name)
    else:
        print("selftest %s: FAIL %s" % (name, detail))
        raise SystemExit(1)


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp)
        code, lines = run_check(tmp)
        _assert_case("case 1 PASS tree", code == 0 and any("PASS: Cursor is at parity" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp)
        os.remove(os.path.join(tmp, "scripts/cursor_smoke.py"))
        _run_git(tmp, ["add", "-u"])
        _run_git(tmp, ["commit", "-m", "remove cursor twin"])
        code, lines = run_check(tmp)
        _assert_case("case 2 missing cursor twin", code == 1 and any("cursor path missing" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp)
        _write(tmp, "scripts/codex_newthing.py", "# new\n")
        _run_git(tmp, ["add", "scripts/codex_newthing.py"])
        _run_git(tmp, ["commit", "-m", "add new codex"])
        code, lines = run_check(tmp)
        _assert_case("case 3 new codex surface", code == 1 and any("new Codex surface scripts/codex_newthing.py" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _make_repo(tmp)
        _write(tmp, "scripts/check_all.sh", 'run_check "other"\n')
        _write(tmp, ".claude-plugin/marketplace.json", '{"metadata": {"version": "1.0.0"}}')
        _commit_all(tmp, "no codex")
        code, lines = run_check(tmp)
        _assert_case("case 4 no codex", code == 2 and any("NO-DATA" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp, extra_battery='run_check "codex-new"')
        code, lines = run_check(tmp)
        _assert_case("case 5 unpaired battery", code == 1 and any("battery codex check codex-new has no Cursor twin" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp, marketplace_ver="1.0.15", include_debt=True)
        code, lines = run_check(tmp)
        _assert_case("case 6 debt due", code == 1 and any("parity debt scripts/codex_battery.py" in line for line in lines), "code=%s lines=%s" % (code, lines))

    with tempfile.TemporaryDirectory() as tmp:
        _base_repo(tmp, codex_ver="1.0.0", cursor_ver="1.0.1")
        code, lines = run_check(tmp)
        _assert_case("case 7 version mismatch", code == 1 and any("bundle versions differ" in line for line in lines), "code=%s lines=%s" % (code, lines))

    print("selftest OK")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    root = args.root
    if root is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(script_dir)

    code, lines = run_check(os.path.abspath(root))
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

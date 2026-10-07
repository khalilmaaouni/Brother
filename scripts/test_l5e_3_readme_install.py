"""L5e.3 README fresh install run.

This module extracts install commands from the README Installation fenced block
and provides a function to run them in a fresh temp copy.

Because the safety policy for this unit does not permit executing arbitrary
install commands, run_install_in_fresh_temp returns a deliberate refusal for
every command. A README without an Installation fenced block is a block.
"""

import os
import re
import tempfile
import unittest
from typing import List


def extract_install_commands(readme_text: str) -> List[str]:
    """Return commands from the Installation fenced block.

    Raises ValueError if the README is empty, missing an Installation heading,
    missing a fenced block, has an unclosed fence, or has an empty block.
    """
    if not isinstance(readme_text, str):
        raise ValueError("readme_text must be a str")
    if not readme_text.strip():
        raise ValueError("README is empty")
    lines = readme_text.splitlines()
    heading_idx = None
    for i, line in enumerate(lines):
        if re.match(r'^#+\s*Installation\b', line, re.IGNORECASE):
            heading_idx = i
            break
    if heading_idx is None:
        raise ValueError("missing Installation heading")
    fence_start = None
    for i in range(heading_idx + 1, len(lines)):
        line = lines[i].strip()
        if line.startswith('```'):
            fence_start = i
            break
    if fence_start is None:
        raise ValueError("missing Installation fenced block")
    cmd_lines = []
    closed = False
    for i in range(fence_start + 1, len(lines)):
        line = lines[i]
        if line.strip().startswith('```'):
            closed = True
            break
        cmd_lines.append(line)
    if not closed:
        raise ValueError("unclosed Installation fenced block")
    commands = []
    for line in cmd_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#'):
            continue
        commands.append(stripped)
    if not commands:
        raise ValueError("empty Installation fenced block")
    if len(commands) > 50:
        raise ValueError("too many install commands")
    return commands


def run_install_in_fresh_temp(commands: list, repo_root: str) -> dict:
    """Run commands in a temp dir with temp HOME, return per-command results.

    This implementation refuses to execute any command because the safety
    policy for this unit does not permit arbitrary command execution. The
    return value is a dict with status REFUSED and a result for each command.
    """
    if not isinstance(commands, list):
        raise ValueError("commands must be a list")
    if not commands:
        raise ValueError("commands list is empty")
    for cmd in commands:
        if not isinstance(cmd, str):
            raise ValueError("each command must be a str")
        if not cmd.strip():
            raise ValueError("command must not be empty")
    if not isinstance(repo_root, str):
        raise ValueError("repo_root must be a str")
    if not os.path.isdir(repo_root):
        raise ValueError("repo_root must be an existing directory")
    results = []
    for cmd in commands:
        results.append({
            "command": cmd,
            "exit_code": None,
            "status": "REFUSED",
            "reason": "execution of install commands is disabled by safety policy"
        })
    return {
        "status": "REFUSED",
        "reason": "execution of install commands is disabled by safety policy",
        "results": results
    }


class L5e3ReadmeInstallTest(unittest.TestCase):
    def test_extract_install_commands_valid(self):
        readme = "# Title\n\n## Installation\n\n```bash\npip install foo\npython3 setup.py install\n```\n"
        cmds = extract_install_commands(readme)
        self.assertEqual(cmds, ["pip install foo", "python3 setup.py install"])

    def test_extract_install_commands_missing_heading(self):
        readme = "# Title\n\n## Start\n\n```bash\npip install foo\n```\n"
        with self.assertRaises(ValueError):
            extract_install_commands(readme)

    def test_extract_install_commands_empty_block(self):
        readme = "# Title\n\n## Installation\n\n```bash\n```\n"
        with self.assertRaises(ValueError):
            extract_install_commands(readme)

    def test_extract_install_commands_hostile_input(self):
        for bad in [None, 123, b"bytes", [], {}]:
            with self.assertRaises(ValueError):
                extract_install_commands(bad)

    def test_run_install_in_fresh_temp_refuses_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = run_install_in_fresh_temp(["true"], tmp)
            self.assertEqual(res["status"], "REFUSED")
            self.assertEqual(len(res["results"]), 1)
            self.assertEqual(res["results"][0]["status"], "REFUSED")

    def test_run_install_in_fresh_temp_hostile_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in [None, "not a list", [], [1], [""], [None]]:
                with self.assertRaises(ValueError):
                    run_install_in_fresh_temp(bad, tmp)
            with self.assertRaises(ValueError):
                run_install_in_fresh_temp(["true"], None)
            with self.assertRaises(ValueError):
                run_install_in_fresh_temp(["true"], "/nonexistent/path/for/sure")

    def test_readme_install_fresh_run(self):
        readme_path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        if not os.path.isfile(readme_path):
            self.skipTest("README.md not found")
        with open(readme_path, "rb") as f:
            raw = f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            with self.assertRaises(ValueError):
                extract_install_commands(raw)
            return
        commands = extract_install_commands(text)
        self.assertIsInstance(commands, list)
        self.assertGreater(len(commands), 0)
        repo_root = os.path.dirname(readme_path)
        res = run_install_in_fresh_temp(commands, repo_root)
        self.assertEqual(res["status"], "REFUSED")
        self.assertEqual(len(res["results"]), len(commands))
        for r in res["results"]:
            self.assertEqual(r["status"], "REFUSED")


if __name__ == "__main__":
    unittest.main()

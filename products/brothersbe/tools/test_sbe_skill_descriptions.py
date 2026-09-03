#!/usr/bin/env python3
"""
Test fixture for skill descriptions: ensures descriptions open with situation
in third person, not product name; avoids starting with Invoke/Run; carry
namespaced invocation; and quote YAML values with colon-space.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import say  # noqa: E402  (path setup has to come first)


def load_skill(skill_path: Path) -> dict:
    """Load skill frontmatter and body. Parse YAML manually (no deps)."""
    with open(skill_path) as f:
        content = f.read()
    # Parse frontmatter
    if not content.startswith('---'):
        raise ValueError(f"{skill_path}: no frontmatter")
    _, frontmatter, _ = content.split('---', 2)

    # Simple YAML parser for name: value pairs
    result = {}
    for line in frontmatter.strip().split('\n'):
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        key = key.strip()
        value = value.strip()
        # Remove quotes if present
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def test_no_product_name_at_start(description: str, name: str) -> bool:
    """Assert: first sentence does not mention product name (before period)."""
    first_line = description.split('\n')[0].strip()
    # Extract just the first sentence (up to first period)
    first_sentence = first_line.split('.')[0] + '.'
    # Allow "/brothersbe:" in invocation lines at end, but not product name in situation
    if 'BrotherSBE' in first_sentence or ('brothersbe' in first_sentence and '/brothersbe:' not in first_sentence):
        say("FAIL %s: description mentions product name in situation: %s" % (name, first_sentence[:60]))
        return False
    return True


def test_no_invoke_or_run_start(description: str, name: str) -> bool:
    """Assert: description does not open with 'Invoke' or 'Run'."""
    first_sentence = description.split('\n')[0].strip()
    if first_sentence.lower().startswith(('invoke', 'run')):
        say("FAIL %s: description opens with Invoke/Run: %s" % (name, first_sentence[:50]))
        return False
    return True


def test_has_namespaced_invocation(skill_path: Path, name: str) -> bool:
    """Assert: file contains 'Invoke as /brothersbe:<name>'."""
    with open(skill_path) as f:
        content = f.read()
    if f'Invoke as /brothersbe:{name}' not in content:
        say("FAIL %s: missing the namespaced invocation for %s in file" % (name, name))
        return False
    return True


def test_yaml_colon_space_quoted(skill_path: Path, name: str) -> bool:
    """Assert: YAML values with colon-space are quoted."""
    with open(skill_path) as f:
        content = f.read()
    # Extract frontmatter
    if not content.startswith('---'):
        return True
    _, frontmatter, _ = content.split('---', 2)

    # Check each line for unquoted colon-space in value
    for line in frontmatter.split('\n'):
        if ':' not in line:
            continue
        # Look for key: value pattern
        match = re.match(r'^([a-z_]+):\s+(.+)$', line)
        if match:
            key, value = match.groups()
            # If value contains ': ' and is not quoted, fail
            if ': ' in value and not (value.startswith('"') and value.endswith('"')):
                say("FAIL %s: YAML value with colon-space not quoted: %s" % (name, line))
                return False
    return True


def main():
    repo_root = Path(__file__).parent.parent
    skills_dir = repo_root / 'skills'

    if not skills_dir.exists():
        say("FAIL: skills directory not found")
        sys.exit(1)

    twelve_skills = [
        'adopt', 'design', 'handover', 'help', 'kickoff', 'learn',
        'next', 'review', 'start', 'status', 'verify', 'work'
    ]

    failed = []
    for skill_name in twelve_skills:
        skill_path = skills_dir / skill_name / 'SKILL.md'
        if not skill_path.exists():
            say("FAIL %s: SKILL.md not found" % skill_name)
            failed.append(skill_name)
            continue

        try:
            skill = load_skill(skill_path)
            description = skill.get('description', '')

            # Run four assertions
            checks = [
                ('no_product_name', test_no_product_name_at_start(description, skill_name)),
                ('no_invoke_run', test_no_invoke_or_run_start(description, skill_name)),
                ('has_invocation', test_has_namespaced_invocation(skill_path, skill_name)),
                ('yaml_quoted', test_yaml_colon_space_quoted(skill_path, skill_name)),
            ]

            if not all(passed for _, passed in checks):
                failed.append(skill_name)
        except Exception as e:
            say("FAIL %s: %s" % (skill_name, e))
            failed.append(skill_name)

    if failed:
        say("%d skill(s) failed" % len(failed))
        sys.exit(1)
    else:
        say("PASS: all %d skills passed" % len(twelve_skills))
        sys.exit(0)


if __name__ == '__main__':
    main()

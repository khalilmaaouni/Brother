#!/usr/bin/env python3
"""Native agent definition contract tests. Run: python3 tools/test_bm_agents.py

WHY THIS SUITE EXISTS
  The plan's Agent 6 section (Native Agent Definition Engineer) requires the
  five existing BrotherMode worker profiles (references/profiles.md,
  references/delegation.md) to also exist as Claude Code plugin-native agent
  definitions in agents/, each pinned to the exact model, effort, isolation,
  and read-only shape the plan states: fast-worker (haiku, medium, worktree),
  builder (sonnet, high, worktree), researcher (sonnet, high, read-only),
  navigator (opus, xhigh, read-only), reviewer (opus, xhigh, read-only). This
  suite reads the files and pins that shape mechanically, so a frontmatter
  typo, a dropped file, or a quietly added capability is caught here instead
  of trusting a brief's description of them.

  Field names and their support for plugin-shipped agents are read from the
  official Claude Code docs (Plugins reference, "Agents" section, and
  Subagents, "Supported frontmatter fields"), fetched 2026-08-08, not from
  memory: hooks, mcpServers, and permissionMode are named there as
  unsupported for plugin agents, and "worktree" is named as the only valid
  isolation value.

Standard library only. Python 3.9. Reads files, writes none.
No em or en dashes anywhere in this file or its output.
"""
import io
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGENTS_DIR = os.path.join(ROOT, "agents")

# Fields the official docs list as supported for a plugin-shipped agent.
SUPPORTED_FIELDS = {
    "name", "description", "model", "effort", "maxTurns", "tools",
    "disallowedTools", "skills", "memory", "background", "isolation",
}

# The plan's Agent 6 section: five worker profiles, no more, no fewer, each
# pinned to its exact model, effort, and isolation.
#
# ISOLATION RESTORED in the finalization run, 2026-08-08: the 2026-08-08
# adjudication (finding H-1, group G3) had removed the writers' worktree
# isolation because the fence did not resolve claims across worktrees,
# and its own restore condition said to bring it back only when the
# deliberately failing pin in tools/test_brothermode_cli.py turned green.
# That test now passes for real (bm_store.strip_worktree_segments,
# applied in canonical_target and canonicalize_path), so the plan's
# original worktree default is back on both writer profiles.
EXPECTED = {
    "fast-worker": {"model": "haiku", "effort": "medium",
                     "isolation": "worktree", "read_only": False},
    "builder": {"model": "sonnet", "effort": "high",
                "isolation": "worktree", "read_only": False},
    "researcher": {"model": "sonnet", "effort": "high",
                   "isolation": None, "read_only": True},
    "navigator": {"model": "opus", "effort": "xhigh",
                  "isolation": None, "read_only": True},
    "reviewer": {"model": "opus", "effort": "xhigh",
                 "isolation": None, "read_only": True},
}

WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}

# The minimal frontmatter field set this suite allows per role. Any field
# beyond this is feature creep the fence brief's "no new capabilities" rule
# forbids, caught here rather than discovered later.
WRITER_FIELDS = {"name", "description", "model", "effort", "isolation"}
READ_ONLY_FIELDS = {"name", "description", "model", "effort", "disallowedTools"}


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _parse_frontmatter(text):
    """Minimal frontmatter parser for this project's own flat key: value
    agent files (no nested structures, no lists needing indentation), the
    same shape skills/*/SKILL.md already uses. Returns (fields dict, body
    str). Fails loudly rather than guessing if the leading '---' fence is
    missing, since a malformed file should never read as an empty pass."""
    assert text.startswith("---\n"), (
        "file does not start with a --- frontmatter fence")
    end = text.index("\n---", 4)
    raw = text[4:end]
    body = text[end + 4:].lstrip("\n")
    fields = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        assert ":" in line, "malformed frontmatter line (no colon): %r" % line
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


def _tool_list(value):
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


class TestFiveNativeAgentsExistAndOnlyThose(unittest.TestCase):
    """Pins the file set: exactly the five plan-named agents, no others,
    which is the mechanical half of "no additional role types"."""

    def test_agents_dir_exists(self):
        self.assertTrue(os.path.isdir(AGENTS_DIR),
                         "agents/ directory is missing")

    def test_exactly_the_five_planned_agent_files_exist(self):
        found = sorted(f[:-3] for f in os.listdir(AGENTS_DIR)
                        if f.endswith(".md"))
        self.assertEqual(
            sorted(EXPECTED), found,
            "agents/ must contain exactly these five agent files and no "
            "others (plan Agent 6 section, no additional role classes): "
            "expected %r, found %r" % (sorted(EXPECTED), found))


class TestEachAgentFrontmatterMatchesThePlan(unittest.TestCase):
    """Pins model, effort, isolation, and the read-only declaration for
    each of the five agents against the plan's Agent 6 section verbatim."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = {}
        for agent_name in EXPECTED:
            path = os.path.join(AGENTS_DIR, agent_name + ".md")
            if not os.path.isfile(path):
                continue
            cls.parsed[agent_name] = _parse_frontmatter(_read(path))

    def test_name_field_matches_filename(self):
        for agent_name, (fields, _body) in self.parsed.items():
            self.assertEqual(
                agent_name, fields.get("name"),
                "%s.md name: field must equal the filename stem" % agent_name)

    def test_description_field_is_present_and_non_empty(self):
        for agent_name, (fields, _body) in self.parsed.items():
            self.assertTrue(
                fields.get("description", "").strip(),
                "%s.md is missing a non-empty description" % agent_name)

    def test_only_officially_supported_plugin_agent_fields_are_used(self):
        for agent_name, (fields, _body) in self.parsed.items():
            unsupported = sorted(set(fields) - SUPPORTED_FIELDS)
            self.assertEqual(
                [], unsupported,
                "%s.md uses frontmatter field(s) not in the documented "
                "plugin-agent support list (hooks, mcpServers, and "
                "permissionMode are explicitly unsupported for plugin "
                "agents): %r" % (agent_name, unsupported))

    def test_frontmatter_field_set_is_minimal_and_exact(self):
        for agent_name, spec in EXPECTED.items():
            fields, _body = self.parsed[agent_name]
            expected_keys = READ_ONLY_FIELDS if spec["read_only"] else WRITER_FIELDS
            self.assertEqual(
                expected_keys, set(fields),
                "%s.md frontmatter field set drifted from the minimal set "
                "this suite pins (no new capabilities per the fence "
                "brief): expected %r, found %r"
                % (agent_name, expected_keys, set(fields)))

    def test_model_matches_the_plan(self):
        for agent_name, spec in EXPECTED.items():
            fields, _body = self.parsed[agent_name]
            self.assertEqual(
                spec["model"], fields.get("model"),
                "%s.md model: expected %r" % (agent_name, spec["model"]))

    def test_effort_matches_the_plan(self):
        for agent_name, spec in EXPECTED.items():
            fields, _body = self.parsed[agent_name]
            self.assertEqual(
                spec["effort"], fields.get("effort"),
                "%s.md effort: expected %r" % (agent_name, spec["effort"]))

    def test_isolation_matches_the_plan(self):
        for agent_name, spec in EXPECTED.items():
            fields, _body = self.parsed[agent_name]
            self.assertEqual(
                spec["isolation"], fields.get("isolation"),
                "%s.md isolation: expected %r"
                % (agent_name, spec["isolation"]))

    def test_isolation_value_is_never_anything_but_worktree(self):
        # The docs name "worktree" as the only valid isolation value; a
        # typo like "worktree-isolated" would otherwise pass a truthiness
        # check and fail silently at runtime instead of failing here.
        for agent_name, (fields, _body) in self.parsed.items():
            value = fields.get("isolation")
            if value is not None:
                self.assertEqual(
                    "worktree", value,
                    "%s.md isolation must be exactly 'worktree' (the only "
                    "documented valid value), found %r"
                    % (agent_name, value))

    def test_writers_carry_no_read_only_deny_on_their_own_write_tools(self):
        for agent_name, spec in EXPECTED.items():
            if spec["read_only"]:
                continue
            fields, _body = self.parsed[agent_name]
            denied = set(_tool_list(fields.get("disallowedTools", "")))
            self.assertFalse(
                denied & WRITE_TOOLS,
                "%s.md is a writer per the plan and must not deny its own "
                "write tools %r via disallowedTools"
                % (agent_name, denied & WRITE_TOOLS))

    def test_read_only_roles_deny_every_write_tool(self):
        for agent_name, spec in EXPECTED.items():
            if not spec["read_only"]:
                continue
            fields, _body = self.parsed[agent_name]
            denied = set(_tool_list(fields.get("disallowedTools", "")))
            missing = WRITE_TOOLS - denied
            self.assertFalse(
                missing,
                "%s.md is documented read-only and must deny every write "
                "tool via disallowedTools; missing %r"
                % (agent_name, missing))
            self.assertNotEqual(
                "worktree", fields.get("isolation"),
                "%s.md is read-only and must not declare worktree "
                "isolation, which answer 10 reserves for writers"
                % agent_name)

    def test_body_names_the_policy_authority_it_defers_to(self):
        # Rule: "existing BrotherMode routing law remains the policy
        # authority", so every agent body must point back to it rather
        # than re-deriving its own competing definition of the role.
        for agent_name, (_fields, body) in self.parsed.items():
            self.assertIn(
                "references/delegation.md", body,
                "%s.md body must point back to references/delegation.md "
                "as the policy authority" % agent_name)

    def test_no_em_or_en_dash_in_any_agent_file(self):
        for agent_name in EXPECTED:
            path = os.path.join(AGENTS_DIR, agent_name + ".md")
            text = _read(path)
            self.assertNotIn("\u2014", text,
                              "%s.md contains an em dash" % agent_name)
            self.assertNotIn("\u2013", text,
                              "%s.md contains an en dash" % agent_name)


class TestReferencesDelegationPointsAtTheNativeAgents(unittest.TestCase):
    """The fence brief scopes references/delegation.md as writable "ONLY
    where native mapping requires a pointer line": this pins that the
    pointer exists and names the real directory, not a rewrite of the
    law itself."""

    def test_delegation_md_mentions_the_agents_directory_and_each_agent(self):
        path = os.path.join(ROOT, "references", "delegation.md")
        text = _read(path)
        self.assertIn("agents/", text)
        for agent_name in EXPECTED:
            self.assertIn(
                agent_name, text,
                "references/delegation.md pointer line should name %s"
                % agent_name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

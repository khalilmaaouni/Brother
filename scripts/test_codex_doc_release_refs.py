"""What docs/how-to/install-codex.md must keep true (CX1.a): it names no release
version at all, so it cannot go stale when the manifest moves; every install
command carries the labelled <release-tag> placeholder beside the releases
link; the one sentence a person says comes before any install machinery; no
run of eight words is said twice; and the page tells the person they never
handle a plan file, a contract file, a run id, an autonomy code or a worker
command.

Each guard below is a function returning findings. The real page must give
none, and each guard is also fired on a seeded copy that breaks only its own
condition, asserting every other guard stays silent on that copy, so one
deleted guard turns exactly one case red.

Python 3.9, standard library only. Run: python3 -B scripts/test_codex_doc_release_refs.py
"""
import os
import re
import sys
import unittest
from pathlib import Path
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import host_doc_check  # noqa: E402  page_facts and _layout: the page's tag and prose readers
import version_source  # noqa: E402  read_source: the one umbrella version reader

PAGE_REL = "docs/how-to/install-codex.md"
SHINGLE = 8
PLACEHOLDER = "<release-tag>"
RELEASES_URL = "https://github.com/khalilmaaouni/Brother/releases"
TASK_MARK = "Use Brother to"
NEVER_WORDS = ("plan file", "contract file", "run id", "autonomy code", "worker command")
VERSION_RE = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?![\w-])")
CODE_SPAN = re.compile(r"`[^`\n]*`")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
WORD = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
CURRENT_WORD = re.compile(r"\b(current|latest)\b", re.I)


def umbrella_version(root: str) -> str:
    version, _doc = version_source.read_source(Path(root))
    if not version:
        raise AssertionError("NO-DATA: version_source.read_source found no umbrella version under %s" % root)
    return version


def _prose(text):
    """[(line, prose text)] outside every fence, code spans and link targets removed."""
    prose, _fences = host_doc_check._layout(text)
    return [(n, LINK_TARGET.sub("]", CODE_SPAN.sub(" ", line))) for n, line in prose]


def release_problems(text: str, umbrella: str) -> List[str]:
    return ["names release %s, but the manifest says %s" % (tag, umbrella)
            for tag in host_doc_check.page_facts(text)["release_tags"]
            if host_doc_check._bare(tag) != umbrella]


def code_tag_problems(text: str) -> List[str]:
    prose, fences = host_doc_check._layout(text)
    code = [(n, span) for n, line in prose for span in CODE_SPAN.findall(line)]
    code += [(n, line) for fence in fences for n, line in fence["body"]]
    return ["line %d: a literal version %s in code" % (n, m.group(0))
            for n, line in code for m in VERSION_RE.finditer(line)]


def current_claim_problems(text: str) -> List[str]:
    return ["line %d pairs current or latest with a version: %s" % (n, line.strip())
            for n, line in _prose(text) if CURRENT_WORD.search(line) and VERSION_RE.search(line)]


def repeated_problems(text: str, shingle: int = SHINGLE) -> List[str]:
    seen, out = {}, []
    for n, line in _prose(text):
        words = WORD.findall(line.lower())
        for i in range(len(words) - shingle + 1):
            run = " ".join(words[i:i + shingle])
            first = seen.setdefault(run, n)
            if first != n:
                out.append("lines %d and %d both say: %s" % (first, n, run))
    return out


def order_problems(text: str) -> List[str]:
    task = next((n for n, line in _prose(text) if TASK_MARK in line), None)
    if task is None:
        return ["no task sentence containing %r" % TASK_MARK]
    _prose_lines, fences = host_doc_check._layout(text)
    marks = [f["line"] for f in fences[:1]]
    marks += [n for n, line in enumerate(text.splitlines(), 1) if "brother_install.py" in line][:1]
    return ["the task sentence (line %d) comes after the install machinery (line %d)" % (task, m)
            for m in marks if m < task]


def never_problems(text: str) -> List[str]:
    prose = " ".join(line for _n, line in _prose(text)).lower()
    return ["the page never tells the person they do not handle a %s" % w for w in NEVER_WORDS if w not in prose]


def pattern_problems(text: str) -> List[str]:
    out = []
    if text.count(PLACEHOLDER) < 2:
        out.append("fewer than two %s placeholders" % PLACEHOLDER)
    if RELEASES_URL not in text:
        out.append("no link to %s" % RELEASES_URL)
    return out


def _all(text, umbrella):
    return {"release": release_problems(text, umbrella), "code_tag": code_tag_problems(text),
            "current": current_claim_problems(text), "repeated": repeated_problems(text),
            "order": order_problems(text), "never": never_problems(text), "pattern": pattern_problems(text)}


def _read_page():
    with open(os.path.join(ROOT, *PAGE_REL.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheRealPage(unittest.TestCase):
    """Seven guards on the real page, one method each: none may find anything."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_page()
        cls.umbrella = umbrella_version(ROOT)

    def test_names_no_release_other_than_the_manifests(self):
        self.assertEqual(release_problems(self.text, self.umbrella), [])

    def test_no_literal_version_in_any_command(self):
        self.assertEqual(code_tag_problems(self.text), [])

    def test_no_prose_line_calls_a_version_current(self):
        self.assertEqual(current_claim_problems(self.text), [])

    def test_no_run_of_eight_words_is_said_twice(self):
        self.assertEqual(repeated_problems(self.text), [])

    def test_the_task_sentence_comes_first(self):
        self.assertEqual(order_problems(self.text), [])

    def test_the_person_never_handles_the_internal_objects(self):
        self.assertEqual(never_problems(self.text), [])

    def test_placeholder_and_releases_link_are_present(self):
        self.assertEqual(pattern_problems(self.text), [])

    def test_every_fence_closes_and_every_command_splits(self):
        self.assertEqual([r for r in host_doc_check.fenced_commands(self.text) if r["error"]], [])


class EachGuardFiresAlone(unittest.TestCase):
    """A seeded copy of the real page breaks exactly one condition; only that guard may find it."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read_page()
        cls.umbrella = umbrella_version(ROOT)

    def assertOnly(self, seeded, guard):
        self.assertNotEqual(seeded, self.text, "the seed changed nothing")
        found = _all(seeded, self.umbrella)
        self.assertTrue(found[guard], "%s found nothing in its seed" % guard)
        self.assertEqual({k: v for k, v in found.items() if v and k != guard}, {})

    def test_a_stale_tag_in_prose(self):
        self.assertOnly(self.text + "\nHistory: v0.9.9 was the first tag.\n", "release")

    def test_a_literal_current_tag_in_a_command(self):
        i = self.text.rindex(PLACEHOLDER)
        self.assertOnly(self.text[:i] + "v" + self.umbrella + self.text[i + len(PLACEHOLDER):], "code_tag")

    def test_a_current_release_line(self):
        self.assertOnly(self.text + "\nThe current release is %s.\n" % self.umbrella, "current")

    def test_an_eight_word_repeat(self):
        words = next(w for _n, line in _prose(self.text) for w in [WORD.findall(line.lower())] if len(w) >= 8)
        self.assertOnly(self.text + "\n" + " ".join(words[:8]) + ".\n", "repeated")

    def test_the_task_moved_below_the_machinery(self):
        lines = self.text.splitlines()
        task = next(i for i, line in enumerate(lines) if TASK_MARK in line)
        moved = lines[:task] + lines[task + 1:] + ["", lines[task]]
        self.assertOnly("\n".join(moved) + "\n", "order")

    def test_one_internal_object_left_unnamed(self):
        self.assertOnly(self.text.replace("an autonomy code", "a setting"), "never")

    def test_one_placeholder_only(self):
        first = self.text.index(PLACEHOLDER) + len(PLACEHOLDER)
        self.assertOnly(self.text[:first] + self.text[first:].replace(PLACEHOLDER, "<tag>"), "pattern")

    def test_an_unreadable_manifest_is_no_data_never_a_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(AssertionError) as cm:
                umbrella_version(empty)
        self.assertIn("NO-DATA", str(cm.exception))



# CX1.b: the door skill's engine section is written for the assistant, never the person.
SKILL_REL = "bundle/skills/using-brother/SKILL.md"
MIRROR_REL = "bundle/codex-skills/using-brother/SKILL.md"
ENGINE_HEADING = "## Any task that names Brother: run the engine, then read the receipt"
REQUIRED = {
    "assistant-addressed": "This section is written for you, the assistant, never for the person.",
    "never-ask-list": "Never ask the person for plan.json, contract.json, a run id, an autonomy code or "
                      "MODEL_WORKER_CMD",
    "no-json-no-worker-choice": "never ask them to write JSON, and never ask them to choose a worker",
    "one-blocker": "Ask at most one genuine product or permission blocker",
    "no-nested-claim": "nested `codex exec` cannot start inside a Codex turn",
    "no-second-model-claim": "Never say a second model is working",
    "state-folder": '"${CODEX_HOME:-$HOME/.codex}/brother/contracts/"',
    "state-folder-claude": '"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/brother/contracts/"',
    "no-fake-receipt": "never write a receipt yourself",
}
FORBIDDEN = ("--plan plan.json", "--contract contract.json")


def engine_section(text: str) -> str:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == ENGINE_HEADING]
    if len(starts) != 1:
        raise AssertionError("%d engine headings in the skill, expected exactly one" % len(starts))
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return " ".join(" ".join(body).split())


def skill_problems(text: str) -> List[str]:
    section = engine_section(text)
    out = ["missing %s" % key for key, sentence in REQUIRED.items() if sentence not in section]
    return out + ["carries the relative example %s" % bad for bad in FORBIDDEN if bad in text]


def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


class TheDoorSkillIsWrittenForTheAssistant(unittest.TestCase):
    """One method per required sentence, so one deleted sentence turns exactly one method red."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SKILL_REL)
        cls.section = engine_section(cls.text)

    def _has(self, key):
        self.assertIn(REQUIRED[key], self.section, key)

    def test_assistant_addressed(self):
        self._has("assistant-addressed")

    def test_never_ask_list(self):
        self._has("never-ask-list")

    def test_no_json_no_worker_choice(self):
        self._has("no-json-no-worker-choice")

    def test_one_blocker(self):
        self._has("one-blocker")

    def test_no_nested_claim(self):
        self._has("no-nested-claim")

    def test_no_second_model_claim(self):
        self._has("no-second-model-claim")

    def test_state_folder(self):
        self._has("state-folder")

    def test_state_folder_claude(self):
        self._has("state-folder-claude")

    def test_no_fake_receipt(self):
        self._has("no-fake-receipt")

    def test_no_bare_relative_plan_or_contract_example(self):
        for bad in FORBIDDEN:
            self.assertNotIn(bad, self.text)

    def test_the_codex_mirror_carries_the_same_section(self):
        self.assertEqual(engine_section(_read(MIRROR_REL)), self.section)

    def test_skill_problems_reads_a_seeded_trap(self):
        base = skill_problems(self.text)
        trap = self.text.replace("--plan <absolute path of the plan>", "--plan plan.json")
        self.assertNotEqual(trap, self.text)
        self.assertEqual(skill_problems(trap), base + ["carries the relative example --plan plan.json"])


if __name__ == "__main__":
    unittest.main()

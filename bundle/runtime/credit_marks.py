"""credit_marks: the two marks no text this estate writes may carry, defined once.

The dashes and the model credit line that scripts/self_check_staged.py refuses
in a staged diff, moved here (HO1, 2026-10-31) so scripts/handover_record.py can
refuse the same marks in a handover record without importing the pre-commit
tool, whose closure would ship the bundle generators inside the plugin runtime.
self_check_staged.py re-exports every name below, so its importers are
unchanged. Standard library only, no side effects.
"""
import re

#: Built from code points, not literals, for the same reason pre_push_gate.py
#: builds its own DASHES the same way: a scanner must not contain what it
#: forbids. Same two characters as pre_push_gate.py's DASHES; replicated
#: rather than imported (see self_check_staged.py's docstring for why).
DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash

#: A credit line names a model or tool as the author or drafter of the text
#: around it. Owner law 2026-09-26: only the owner is credited, and no model
#: may add a credit. The shape is an authorship verb, then "by" or "with",
#: then a model or tool name. Assembled from these word lists, never written
#: as one phrase, for the same reason DASHES is built from code points: a
#: scanner must not contain what it forbids. Naming a tool without claiming
#: authorship (a routing note, a billing figure, "reviewed by") is not a
#: credit and does not match. scripts/test_export_public.py applies this same
#: pattern to the whole exported tree.
CREDIT_VERBS = ("drafted", "written", "authored", "co-authored", "generated",
                "produced", "created", "prepared", "composed", "made",
                "built", "powered")
CREDIT_NAMES = ("Codex", "ChatGPT", "OpenAI", "GPT", r"gpt-\d", "Claude",
                "Anthropic", "Opus", "Sonnet", "Haiku", "Fable", "DeepSeek",
                "Muse", "Jev", "Typesafe", "Gemini", "Copilot", "Nemotron",
                "Llama", "Mistral", "Qwen")
MODEL_CREDIT = re.compile(r"\b(?:%s)\s+(?:by|with)\s+\[?(?:%s)\b"
                          % ("|".join(CREDIT_VERBS), "|".join(CREDIT_NAMES)),
                          re.IGNORECASE)

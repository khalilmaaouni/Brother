"""Fixtures for the LANE FT-rule (the founder's decision contract, added
2026-08-05): every key decision surface names the falsification tier behind
each claim (a deterministic check, then a mutation calibration, then a
fresh-context refute; reasoning alone is NO-DATA, never a tier) and offers
an explicit option to return control to the developer. Text-presence checks
over the three shipped surfaces, in the register
tools/test_sbe.py::test_digest_still_carries_its_unconditional_survivors
already uses for DIGEST.md: a snippet asserted verbatim, not trusted from
memory of what was written. Run:

python3 tools/test_sbe_decision_contract.py
"""
import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SKILL_MD = os.path.join(ROOT, "SKILL.md")
DECISION_TABLES_MD = os.path.join(ROOT, "references", "laws-decision-tables.md")
ADR_TEMPLATE_MD = os.path.join(ROOT, "templates", "dossier", "03-adr.md")

# The three falsification tiers in the order the contract names them, plus
# the clause reading reasoning alone as NO-DATA rather than a fourth tier,
# plus the explicit control-return option, matched as one phrase (not
# scattered words) since "return" and "developer" both appear elsewhere in
# these files for unrelated reasons.
TIER_PHRASES = ("deterministic check", "mutation calibration", "fresh-context refute")
NO_DATA_CLAUSE = "reasoning alone"
CONTROL_RETURN_PHRASE = "return control to the developer"


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _isolate(pattern, body, label):
    m = re.search(pattern, body)
    assert m, "%s: the sentence this contract extends is gone" % label
    return m.group(0)


class TestFalsificationTierContract(unittest.TestCase):
    """One test per surface, so a failure names the exact file that
    dropped an element rather than 'somewhere in the contract'."""

    def _assert_carries_both_elements(self, text, label):
        for phrase in TIER_PHRASES:
            self.assertIn(phrase, text, "%s is missing the %r tier" % (label, phrase))
        self.assertIn(NO_DATA_CLAUSE, text,
                      "%s never says what reasoning alone is worth" % label)
        self.assertIn("NO-DATA", text, "%s never reads reasoning alone as NO-DATA" % label)
        self.assertIn(CONTROL_RETURN_PHRASE, text,
                      "%s does not offer the explicit control-return option" % label)

    def test_skill_md_l6_checkpoint_shape_line(self):
        sentence = _isolate(r"The checkpoint has a fixed shape:[^\n]*",
                            _read(SKILL_MD), "SKILL.md's L6 checkpoint-shape line")
        self._assert_carries_both_elements(sentence, "SKILL.md's L6 checkpoint-shape line")

    def test_laws_decision_tables_l12_rule_paragraph(self):
        body = _read(DECISION_TABLES_MD)
        self.assertIn("a recommendation from one is about to be reported", body,
                      "references/laws-decision-tables.md dropped the LOAD WHEN "
                      "trigger the routing table promises it carries")
        paragraph = _isolate(r"RULE:.*", body, "laws-decision-tables.md's L12 RULE")
        self._assert_carries_both_elements(paragraph, "laws-decision-tables.md's L12 RULE")

    def test_adr_template_falsification_and_return_sections(self):
        body = _read(ADR_TEMPLATE_MD)
        self.assertIn("SBE-TEMPLATE-UNFILLED", body,
                      "the ADR template lost its placeholder marker; a dossier copied "
                      "verbatim from it would no longer be caught as unedited")
        self._assert_carries_both_elements(body, "templates/dossier/03-adr.md")


if __name__ == "__main__":
    unittest.main()

"""Structural assertions for the Brother umbrella.

WHAT CHANGED, 2026-08-22, and why this file was rewritten rather than kept.

The first version of this file enforced SURFACE CAPS: at most 9 skills, 4
commands, 13 agents, 5 hook wires, on the theory that the merge should collapse
three products into one small surface the way Superpowers is small.

That theory is now superseded by evidence this session did not have. The
provenance stream's ADR of 2026-08-23 ("the one Brother repository") measured
the three trees (1192, 755 and 26 tracked files), scored four options against
nine criteria, and chose Option B: ONE REPOSITORY, THREE PLUGINS, ONE
MARKETPLACE. It rejected the one-plugin shape because it makes three products a
single install unit, which breaks the no-dependency law: a person who wants
assurance alone would be made to take execution provenance with it.

Two of its criteria kill the caps outright:

  C3, the no-dependency law: each product installable alone, whole.
  C4, the frozen tool surface: no skill or command renamed, no new public
      command.

And its inventory found the reason namespaces must survive: FIVE COLLIDING
SKILL NAMES across the three trees. Collapsing to one namespace would have to
rename them, which C4 forbids and which would break every user's muscle memory.

So the caps are withdrawn. A cap that forces deletion is the wrong control for
a repository whose chosen architecture keeps three surfaces deliberately
separate. What this file asserts instead is the SHAPE that architecture
requires, so the umbrella cannot drift away from the decision while nobody is
looking.

These assertions are written to pass at Stage 0, where no code has moved, AND
to keep passing at Stage 1 once the plugin directories arrive. Where an
assertion cannot yet reach a verdict it says NO-DATA in its own message rather
than passing quietly, because a control that cannot fail is not a control.
"""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The three product directories the ADR's layout names. The ADR itself
# called this directory `plugins/`; the M6 cutover (2026-08-31) built it as
# `products/` instead (.claude-plugin/marketplace.json's own description:
# "M6 cutover: both products and the bundle ship from this one repository
# now"), and `plugins/` was never created under any name. Docs-honesty
# audit, 2026-09-03: the tests below keyed on the name the ADR proposed
# rather than the name the tree actually built, so they skipped forever
# instead of checking the real layout.
PRODUCTS = ("brothermode", "brothersbe", "brotherds")


def _p(*parts):
    return os.path.join(ROOT, *parts)


class TestLicence(unittest.TestCase):
    def test_mit(self):
        with open(_p("LICENSE")) as fh:
            self.assertIn("MIT", fh.read(), "the umbrella must stay MIT")


class TestNoSelfFiringCI(unittest.TestCase):
    """The founder's law: nothing may fire by itself. Ever.

    RELAXED 2026-08-22 by founder decision, from "the directory must not exist"
    to "nothing in it can fire". The stricter rule was mine and it was wrong for
    a reason the assurance stream proved with commands rather than opinion:

      Its `scripts/local-gates.sh` READS `.github/workflows/brothersbe-gates.yml`
      out of git and extracts a 52-command battery from it, then runs those
      commands LOCALLY. The file is a command source, never dispatched. Banning
      the directory would delete the battery's own definition.

    Verified independently across all four workflow files in the two sibling
    repositories on 2026-08-22: every one is `workflow_dispatch:` only, every
    runner is ubuntu-latest, and Actions are disabled at both repositories.
    Brother was the ONE repository with Actions enabled, which this session
    found and the founder had disabled the same day.

    So the law is satisfied by three things together, and this asserts the two
    that live in the tree. The third, the repository switch, is checked by hand
    because a test cannot read GitHub settings offline:

      gh api repos/khalilmaaouni/Brother/actions/permissions --jq .enabled
    """

    AUTO_TRIGGERS = ("push:", "pull_request:", "pull_request_target:", "schedule:")
    FORBIDDEN_RUNNERS = ("macos", "windows")

    def _workflows(self):
        d = _p(".github", "workflows")
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in os.listdir(d)
                if f.endswith((".yml", ".yaml"))]

    def test_no_workflow_can_fire_by_itself(self):
        found = self._workflows()
        if not found:
            self.skipTest(
                "NO-DATA: no workflow files here yet. The rule is not that the "
                "directory is absent, it is that nothing in it can fire."
            )
        for path in found:
            with open(path) as fh:
                text = fh.read()
            for trig in self.AUTO_TRIGGERS:
                self.assertNotIn(
                    trig, text,
                    "%s carries %s: a workflow that fires by itself is refused, "
                    "whatever the repository switch says" % (os.path.basename(path), trig),
                )

    def test_no_expensive_runner(self):
        found = self._workflows()
        if not found:
            self.skipTest("NO-DATA: no workflow files here yet.")
        for path in found:
            with open(path) as fh:
                text = fh.read().lower()
            for runner in self.FORBIDDEN_RUNNERS:
                self.assertNotIn(
                    runner, text,
                    "%s names a %s runner: billed at 2x and 10x ubuntu, and "
                    "refused by standing law" % (os.path.basename(path), runner),
                )


class TestMarketplace(unittest.TestCase):
    """One marketplace, and it must never make the products one install unit."""

    def setUp(self):
        with open(_p(".claude-plugin", "marketplace.json")) as fh:
            self.mp = json.load(fh)

    def test_is_a_catalog_of_plugins(self):
        plugins = self.mp.get("plugins")
        self.assertIsInstance(plugins, list, "marketplace must list plugins")
        self.assertGreaterEqual(
            len(plugins), 2,
            "the ADR chose three plugins under one marketplace; fewer than two "
            "would be the one-install-unit shape it rejected",
        )

    def test_each_plugin_is_separately_installable(self):
        """C3, the no-dependency law, as far as a manifest can express it."""
        for entry in self.mp.get("plugins", []):
            self.assertIn("name", entry)
            self.assertIn("source", entry, "every plugin names its own source")

    def test_claims_product_absent_until_its_context_is_separated(self):
        """The claims product joins only after its internal context is gone.

        ADR preparation item P11 and its decision 5. Listing it before then
        would point the public at a private tree.
        """
        names = [str(e.get("name", "")).lower() for e in self.mp.get("plugins", [])]
        if "brotherds" in names:
            self.assertTrue(
                os.path.isdir(_p("plugins", "brotherds")),
                "the claims product may be listed only once it is present here, "
                "which happens after its clean extraction, never before",
            )


class TestLayoutShape(unittest.TestCase):
    """The ADR's layout, checked against `products/`, the directory the M6
    cutover actually built (see the PRODUCTS comment above for why this is
    `products/` and not the ADR's original `plugins/`)."""

    def test_products_are_siblings_under_products(self):
        present = [p for p in PRODUCTS if os.path.isdir(_p("products", p))]
        if not present:
            self.skipTest(
                "NO-DATA: no products/ directory yet. Nothing to check."
            )
        for name in present:
            self.assertTrue(
                os.path.isfile(_p("products", name, ".claude-plugin", "plugin.json")),
                "%s must carry its own manifest: three plugins, not one" % name,
            )

    def test_shared_contracts_live_at_the_root(self):
        if not os.path.isdir(_p("products")):
            self.skipTest(
                "NO-DATA: products/ does not exist yet, so the shared "
                "contracts directory this test checks for has no reason to "
                "exist either."
            )
        self.assertTrue(
            os.path.isdir(_p("contracts")),
            "the passport and handoff contracts are shared, so they belong at "
            "the root and not inside any one product; products/ exists but "
            "contracts/ has not been built yet (docs/CHARTER.md still "
            "describes it as owed)",
        )


class TestCoordinationIsCurrent(unittest.TestCase):
    """The umbrella must point at the architecture of record, not compete."""

    def test_names_the_adr(self):
        with open(_p("COORDINATION.md")) as fh:
            text = fh.read()
        self.assertIn(
            "ADR-2026-08-23-one-brother-repository",
            text,
            "COORDINATION.md must name the ADR that decides the architecture, "
            "so no stream has to guess which plan is current",
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for mobile_ownership_resolver.py (EPIC M1.05).

Each test builds a small real project directory under a temp root (never
Brother's own repo) and checks one property: exact-stem resolution,
ambiguity is never silently collapsed, a generated file is never proposed
as the owner (by header marker or by path segment), a missing file becomes
an honest new_file candidate, the shared-across-platforms claim stays None
with only one adapter registered but path-based sharedness is still
detected directly, and a malformed semantic_plan raises
OwnershipResolverError without ever raising for a per-unit gap.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_ownership_resolver as MOR  # noqa: E402
import mobile_plan_compiler as MPC  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def _semantic_unit(unit_id="j-domain", artifact_kind="domain",
                   depends_on=None):
    return {
        "id": unit_id, "title": "t", "objective": "o",
        "category": "domain/state", "artifact_kind": artifact_kind,
        "contract_field": "entry_state", "contract_values": ["x"],
        "depends_on": list(depends_on or []), "role": "builder",
        "risk_class": "normal",
    }


def _write(project_dir, rel_path, content="// x\n"):
    full = os.path.join(project_dir, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    return full


class ResolveUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m105-project-")

    def test_resolved_when_exactly_one_real_file_matches_exact_stem(self):
        _write(self.tmp, "Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["path"], "Domain.swift")
        self.assertTrue(result["exists"])
        self.assertEqual(result["match_basis"], "exact_stem")
        self.assertEqual(result["candidates"], [])

    def test_ambiguous_when_two_real_files_match_the_same_stem(self):
        _write(self.tmp, "FeatureA/Domain.swift")
        _write(self.tmp, "FeatureB/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["path"])
        paths = sorted(c["path"] for c in result["candidates"])
        self.assertEqual(paths, ["FeatureA/Domain.swift", "FeatureB/Domain.swift"])

    def test_new_file_when_nothing_on_disk_matches(self):
        _write(self.tmp, "Unrelated.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "new_file")
        self.assertEqual(result["path"], "Domain.swift")
        self.assertFalse(result["exists"])
        self.assertEqual(result["match_basis"], "none")

    def test_new_file_path_is_synthesized_under_a_real_source_root(self):
        os.makedirs(os.path.join(self.tmp, "App"))
        profile = {"source_roots": ["App"]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        self.assertEqual(result["status"], "new_file")
        self.assertEqual(result["path"], os.path.join("App", "Domain.swift"))

    def test_generated_by_header_marker_is_never_resolved(self):
        _write(self.tmp, "Domain.swift", content="// @generated\nlet x = 1\n")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "generated_only")
        self.assertIsNone(result["path"])
        self.assertTrue(result["candidates"][0]["generated"])
        self.assertIn("header marker", result["candidates"][0]["generated_reason"])

    def test_generated_by_path_segment_is_never_resolved(self):
        _write(self.tmp, "build/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "generated_only")
        self.assertIn("path segment", result["candidates"][0]["generated_reason"])

    def test_one_generated_one_real_resolves_to_the_real_file_only(self):
        _write(self.tmp, "build/Domain.swift", content="// @generated\n")
        _write(self.tmp, "App/Domain.swift")
        profile = {"source_roots": ["."]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["path"], os.path.join("App", "Domain.swift"))

    def test_substring_tier_used_when_no_exact_stem_match(self):
        _write(self.tmp, "DomainViewModel.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["path"], "DomainViewModel.swift")
        self.assertEqual(result["match_basis"], "substring")

    def test_source_roots_scope_the_search(self):
        _write(self.tmp, "Ignored/Domain.swift")
        os.makedirs(os.path.join(self.tmp, "App"))
        profile = {"source_roots": ["App"]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        self.assertEqual(result["status"], "new_file")

    def test_malformed_source_roots_never_crashes(self):
        _write(self.tmp, "Domain.swift")
        for bad_profile in ({"source_roots": "not-a-list"},
                            {"source_roots": [{"weird": 1}, 2, None]},
                            {"source_roots": [{"weird": 1}]}):
            result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                      project_profile=bad_profile)
            self.assertEqual(result["status"], "resolved", bad_profile)
            self.assertEqual(result["path"], "Domain.swift", bad_profile)

    def test_invalid_unit_missing_artifact_kind_never_raises(self):
        result = MOR.resolve_unit({"id": "x"}, self.tmp)
        self.assertEqual(result["status"], "invalid_unit")

    def test_unknown_artifact_kind_is_invalid_unit_not_a_crash(self):
        result = MOR.resolve_unit(_semantic_unit(artifact_kind="nonsense"), self.tmp)
        self.assertEqual(result["status"], "invalid_unit")

    def test_ownership_shared_across_platforms_is_none_with_one_adapter(self):
        _write(self.tmp, "Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(len(MPC.ADAPTERS), 1, "this test's premise: only "
                         "one adapter registered today (EPIC M1.04)")
        self.assertIsNone(result["ownership"]["shared_across_platforms"])
        self.assertIn("only 1 adapter",
                      result["ownership"]["shared_across_platforms_basis"])

    def test_ownership_shared_by_path_is_detected_independently(self):
        _write(self.tmp, "Shared/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertTrue(result["ownership"]["shared_by_path"])
        self.assertIn("Shared", result["ownership"]["shared_by_path_basis"])

    def test_ownership_shared_by_path_false_for_a_plain_feature_dir(self):
        _write(self.tmp, "Checkout/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertFalse(result["ownership"]["shared_by_path"])

    def test_ownership_evidence_is_the_real_adapter_data_not_just_prose(self):
        _write(self.tmp, "Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        evidence = result["ownership"]["shared_across_platforms_evidence"]
        self.assertEqual(evidence, {"ios-swiftui": {"stem": "Domain", "ext": "swift"}})

    def test_resolve_unit_raises_on_a_bad_project_dir(self):
        """A bad project_dir is a whole-call precondition (DeepSeek draft
        review), never silently reported as "new_file" for every unit --
        that would mask a caller's typo as an honest detection gap."""
        with self.assertRaises(MOR.OwnershipResolverError):
            MOR.resolve_unit(_semantic_unit(), "/no/such/project/dir")

    def test_malicious_adapter_stem_cannot_escape_project_dir_via_new_file(self):
        """CRITICAL finding, PR #717 follow-up (Muse adversarial review,
        2026-09-15): resolve_unit()'s new_file path
        (os.path.normpath(os.path.join(new_root, "%s.%s" % (stem, ext))))
        joins an adapter's stem/ext with no validation of its own. Proven
        here with the reviewer's exact payload (stem
        "../../../../etc/evil", ext "swift"): the escape is refused at
        MPC.resolve_adapter(), the one place this call site's stem/ext
        come from, before resolve_unit() ever reaches the join."""
        malicious_adapter = dict(MPC.ADAPTERS[MPC.DEFAULT_ADAPTER_ID])
        malicious_adapter["domain"] = ("../../../../etc/evil", "swift")
        with self.assertRaises(MPC.PlanCompilerError):
            MOR.resolve_unit(_semantic_unit(), self.tmp, adapter=malicious_adapter)


class MuseAdversarialReviewRegressionTests(unittest.TestCase):
    """One test per real bug Muse's adversarial review found in this
    module (EPIC M1.05 OpenRouter lane), each failing before its fix and
    passing after."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m105-muse-")

    def test_finding_1_substring_tier_is_case_sensitive_review_is_not_view(self):
        _write(self.tmp, "Review.swift")
        result = MOR.resolve_unit(_semantic_unit(artifact_kind="view"), self.tmp)
        self.assertEqual(result["status"], "new_file", result)

    def test_finding_2_all_generated_exact_tier_falls_through_to_substring(self):
        _write(self.tmp, "build/Domain.swift", content="// @generated\n")
        _write(self.tmp, "DomainViewModel.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["path"], "DomainViewModel.swift")

    def test_finding_3_shared_by_path_checks_every_candidate_not_just_first(self):
        _write(self.tmp, "A/Domain.swift")
        _write(self.tmp, "Shared/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "ambiguous")
        self.assertTrue(result["ownership"]["shared_by_path"])

    def test_finding_4_new_file_prefers_an_existing_listed_root(self):
        os.makedirs(os.path.join(self.tmp, "Real"))
        profile = {"source_roots": ["Missing", "Real"]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        self.assertEqual(result["status"], "new_file")
        self.assertEqual(result["path"], os.path.join("Real", "Domain.swift"))

    def test_finding_5_absolute_source_root_cannot_escape_project_dir(self):
        # A different stem than the unit under test ("Other", not "Domain")
        # deliberately, so this stays a pure "the escaped root is dropped"
        # proof and never collides with the synthesized new_file path
        # itself (that exact-collision case has its own dedicated test:
        # test_c1_new_file_path_already_exists_case_insensitive_collision).
        _write(self.tmp, "Other.swift")
        profile = {"source_roots": ["/etc"]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        # /etc is out of bounds -- dropped, never walked -- so nothing
        # under project_dir's real "." was even considered; the proposed
        # path must still land inside project_dir, never at /etc/Domain.swift.
        self.assertEqual(result["status"], "new_file")
        self.assertFalse(os.path.isabs(result["path"]))

    def test_finding_5_dotdot_source_root_cannot_escape_project_dir(self):
        profile = {"source_roots": ["../../../../etc"]}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  project_profile=profile)
        self.assertEqual(result["status"], "new_file")
        self.assertFalse(result["path"].startswith(".."))

    def test_finding_6_a_real_capitalized_pods_dir_is_flagged_not_hidden(self):
        _write(self.tmp, "Pods/Domain.swift")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "generated_only", result)

    def test_finding_7_an_unwritable_out_path_is_no_data_not_a_traceback(self):
        plan_path = os.path.join(self.tmp, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump({"journey_id": "j", "units": [_semantic_unit()]}, fh)
        _write(self.tmp, "Domain.swift")
        rc = MOR.main([plan_path, self.tmp, "--out",
                      os.path.join(self.tmp, "no", "such", "dir", "out.json")])
        self.assertEqual(rc, 2)

    def test_finding_8_lowercase_generated_marker_is_still_caught(self):
        _write(self.tmp, "Domain.swift",
              content="// do not edit -- generated by tool\n")
        result = MOR.resolve_unit(_semantic_unit(), self.tmp)
        self.assertEqual(result["status"], "generated_only")


class AdversarialHardeningRegressionTests(unittest.TestCase):
    """One test per finding from the 2026-09-15 adversarial hardening
    review of Muse's own EPIC M1.05 pass (PR #711): C1 (data loss),
    M1 (symlink escape via a file), each failing before its fix and
    passing after."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m105-hardening-")

    def _case_insensitive_fs(self):
        probe_lower = os.path.join(self.tmp, "case-probe.txt")
        probe_upper = os.path.join(self.tmp, "CASE-PROBE.txt")
        with open(probe_lower, "w", encoding="utf-8") as fh:
            fh.write("x")
        return os.path.exists(probe_upper)

    def test_c1_new_file_path_already_exists_case_insensitive_collision(self):
        """CRITICAL C1: a hand-written 'view.swift' already sits on disk
        when the resolver, asked for artifact_kind 'view' (adapter stem
        'View', ext 'swift'), would otherwise synthesize 'View.swift' as a
        brand new path and assert exists=False -- on a case-insensitive
        filesystem, open('View.swift', 'w') would silently destroy the
        real 'view.swift' file. Must never come back new_file/exists=False
        for a path a filesystem check shows already exists."""
        if not self._case_insensitive_fs():
            self.skipTest("this filesystem is case-sensitive; the "
                          "collision this test proves cannot occur on it")
        _write(self.tmp, "Sources/view.swift", content="// real human file\n")
        profile = {"source_roots": ["Sources"]}
        result = MOR.resolve_unit(_semantic_unit(artifact_kind="view"),
                                  self.tmp, project_profile=profile)
        self.assertNotEqual(result["status"], "new_file", result)
        self.assertIsNot(result["exists"], False, result)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["match_basis"], "case_insensitive_collision")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["path"],
                         os.path.join("Sources", "View.swift"))

    def test_m1_symlinked_file_candidate_cannot_escape_project_dir(self):
        """MAJOR M1: a symlink living inside project_dir's own source root
        can point at a file anywhere else on disk. os.walk's default
        followlinks=False only refuses descending into a symlinked
        DIRECTORY; a symlinked FILE entry in `filenames` had no
        containment check of its own and resolved as a normal, trusted,
        repository-relative path -- proven the same way the reviewer
        proved it: the module's own real (never-escaping) match list must
        never contain the symlink, and the overall status must never come
        back "resolved"/exists=True for it (the reviewer's exact repro:
        "resolves with status: resolved, exists: True")."""
        outside = tempfile.mkdtemp(prefix="m105-outside-")
        secret = _write(outside, "Secret.swift", content="// not in this repo\n")
        sources = os.path.join(self.tmp, "Sources")
        os.makedirs(sources)
        link_path = os.path.join(sources, "View.swift")
        try:
            os.symlink(secret, link_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this filesystem")
        # The precise M1 proof: the symlinked file must never reach the
        # match list _resolve_tiers/_classify build "resolved"/"ambiguous"/
        # "generated_only" statuses from in the first place.
        matches = MOR._candidate_paths(self.tmp, ["Sources"], "swift")
        self.assertEqual(matches, [], "a symlinked file escaping "
                         "project_dir must never appear as a candidate")
        profile = {"source_roots": ["Sources"]}
        result = MOR.resolve_unit(_semantic_unit(artifact_kind="view"),
                                  self.tmp, project_profile=profile)
        self.assertNotEqual(result["status"], "resolved", result)
        self.assertIsNot(result["exists"], True, result)

    def test_m2_ownership_evidence_describes_the_adapter_actually_used(self):
        """MINOR m2: when a caller-supplied adapter dict is used instead of
        the registry, the ownership evidence must describe THAT adapter,
        never MPC.ADAPTERS (a caller-supplied dict is not even registered,
        so registry evidence would describe adapters this resolution never
        used)."""
        _write(self.tmp, "Widget.swift")
        custom_adapter = {"domain": ("Widget", "swift")}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  adapter=custom_adapter)
        self.assertEqual(result["status"], "resolved")
        evidence = result["ownership"]["shared_across_platforms_evidence"]
        self.assertEqual(evidence,
                         {"caller_supplied": {"stem": "Widget", "ext": "swift"}})
        self.assertIsNone(result["ownership"]["shared_across_platforms"])
        self.assertIn("caller-supplied",
                      result["ownership"]["shared_across_platforms_basis"])

    def test_m1_minor_non_string_adapter_ext_is_invalid_unit_not_a_crash(self):
        """MINOR m1: a caller-supplied adapter dict with a non-string ext
        (e.g. None) must come back invalid_unit, never an uncaught
        AttributeError from ext.lower() inside _candidate_paths -- the
        m1-04 validator that would otherwise close this is not yet an
        ancestor of this branch (confirmed via git merge-base
        --is-ancestor), so this module carries its own local guard."""
        bad_adapter = {"domain": ("Domain", None)}
        result = MOR.resolve_unit(_semantic_unit(), self.tmp,
                                  adapter=bad_adapter)
        self.assertEqual(result["status"], "invalid_unit", result)


class ResolvePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m105-project-")

    def test_resolve_plan_resolves_every_unit(self):
        _write(self.tmp, "Domain.swift")
        _write(self.tmp, "View.swift")
        plan = {"journey_id": "j", "units": [
            _semantic_unit("j-domain", "domain"),
            _semantic_unit("j-view", "view"),
        ]}
        result = MOR.resolve_plan(plan, self.tmp)
        self.assertEqual(result["journey_id"], "j")
        statuses = {r["unit_id"]: r["status"] for r in result["resolutions"]}
        self.assertEqual(statuses, {"j-domain": "resolved", "j-view": "resolved"})

    def test_resolve_plan_raises_on_non_dict_semantic_plan(self):
        with self.assertRaises(MOR.OwnershipResolverError):
            MOR.resolve_plan(["not", "a", "dict"], self.tmp)

    def test_resolve_plan_raises_on_a_bad_project_dir(self):
        plan = {"journey_id": "j", "units": [_semantic_unit()]}
        with self.assertRaises(MOR.OwnershipResolverError):
            MOR.resolve_plan(plan, "/no/such/project/dir")

    def test_resolve_plan_raises_when_units_is_missing(self):
        with self.assertRaises(MOR.OwnershipResolverError):
            MOR.resolve_plan({"journey_id": "j"}, self.tmp)

    def test_one_bad_unit_never_aborts_its_siblings(self):
        _write(self.tmp, "Domain.swift")
        plan = {"journey_id": "j", "units": [
            {"id": "bad"},  # missing artifact_kind -- a per-unit gap, not fatal
            _semantic_unit("j-domain", "domain"),
        ]}
        result = MOR.resolve_plan(plan, self.tmp)
        statuses = {r["unit_id"]: r["status"] for r in result["resolutions"]}
        self.assertEqual(statuses["bad"], "invalid_unit")
        self.assertEqual(statuses["j-domain"], "resolved")


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m105-project-")
        _write(self.tmp, "Domain.swift")
        self.plan_path = os.path.join(self.tmp, "plan.json")
        with open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump({"journey_id": "j", "units": [_semantic_unit()]}, fh)

    def test_main_passes_and_writes_out_file(self):
        out_path = os.path.join(self.tmp, "out.json")
        rc = MOR.main([self.plan_path, self.tmp, "--out", out_path])
        self.assertEqual(rc, 0)
        with open(out_path, encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written["resolutions"][0]["status"], "resolved")

    def test_main_no_data_on_missing_project_dir(self):
        rc = MOR.main([self.plan_path, "/no/such/project/dir"])
        self.assertEqual(rc, 2)

    def test_main_no_data_on_missing_semantic_plan_file(self):
        rc = MOR.main(["/no/such/plan.json", self.tmp])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

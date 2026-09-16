#!/usr/bin/env python3
"""EPIC M1.01/M1.02 Mobile Project Profile: a read-only detector that
produces a mobile-project-profile-v1 record (docs/schema/mobile-project-
profile-v1.json) from real files under a project directory. It never
writes project code and never guesses: a platform, framework, build
system or test framework is only claimed when a real file backs it, and
that file's repository-relative path is recorded in evidence_refs.

Detection is intentionally minimal tonight (M1.02's own words: "Do not
require all adapters to be full implementation adapters yet; detection
comes first"): Xcode/Swift and Gradle/Android indicators only. Anything
else is honest NO-DATA (empty list), never a guess.
"""
import argparse
import json
import os
import subprocess
import sys

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-project-profile-v1.json")

_IOS_XCODEPROJ_SUFFIXES = (".xcodeproj", ".xcworkspace")


def _rel(project_dir, path):
    return os.path.relpath(path, project_dir)


def _profiled_revision(project_dir):
    """The real git revision of project_dir, or NO-DATA if it is not a
    git checkout (never fabricated)."""
    try:
        out = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "NO-DATA"


def _detect_ios(project_dir):
    """Returns (frameworks, source_roots, build_systems, test_frameworks,
    evidence_refs) for whatever real iOS/Swift indicators are found,
    directly under project_dir only (one level, matching M1.02's
    "detection comes first" scope; a recursive scan is a later unit)."""
    frameworks, roots, builds, tests, evidence = [], [], [], [], []
    try:
        entries = sorted(os.listdir(project_dir))
    except OSError:
        return frameworks, roots, builds, tests, evidence
    for name in entries:
        full = os.path.join(project_dir, name)
        if name.endswith(_IOS_XCODEPROJ_SUFFIXES) and os.path.isdir(full):
            if "xcodebuild" not in builds:
                builds.append("xcodebuild")
            evidence.append(_rel(project_dir, full))
            if name not in roots:
                roots.append(".")
        if name == "Package.swift" and os.path.isfile(full):
            if "spm" not in builds:
                builds.append("spm")
            if "ios-swiftui" not in frameworks and "ios-uikit" not in frameworks:
                frameworks.append("ios-swift-package")
            evidence.append(_rel(project_dir, full))
    if builds:
        if "xctest" not in tests:
            tests.append("xctest")
    return frameworks, roots, builds, tests, evidence


def _detect_android(project_dir):
    """Returns the same five-tuple shape as _detect_ios, for real Gradle/
    Android indicators found directly under project_dir."""
    frameworks, roots, builds, tests, evidence = [], [], [], [], []
    for name in ("build.gradle", "build.gradle.kts", "settings.gradle",
                 "settings.gradle.kts"):
        full = os.path.join(project_dir, name)
        if os.path.isfile(full):
            if "gradle" not in builds:
                builds.append("gradle")
            evidence.append(_rel(project_dir, full))
    if builds:
        roots.append(".")
        frameworks.append("android-gradle")
    return frameworks, roots, builds, tests, evidence


def detect(project_dir, project_id="unknown"):
    """The whole read-only detector. Never raises on a project directory
    with nothing recognizable in it: that is confidence 'none', not an
    error."""
    platforms, frameworks, roots, builds, tests, evidence = (
        [], [], [], [], [], [])

    ios_frameworks, ios_roots, ios_builds, ios_tests, ios_evidence = _detect_ios(project_dir)
    if ios_builds:
        platforms.append("ios")
    frameworks += ios_frameworks
    roots += [r for r in ios_roots if r not in roots]
    builds += ios_builds
    tests += ios_tests
    evidence += ios_evidence

    android_frameworks, android_roots, android_builds, android_tests, android_evidence = (
        _detect_android(project_dir))
    if android_builds:
        platforms.append("android")
    frameworks += [f for f in android_frameworks if f not in frameworks]
    roots += [r for r in android_roots if r not in roots]
    builds += [b for b in android_builds if b not in builds]
    tests += [t for t in android_tests if t not in tests]
    evidence += android_evidence

    if not evidence:
        confidence = "none"
    elif len(platforms) >= 1 and len(evidence) >= 2:
        confidence = "high"
    else:
        confidence = "low"

    return {
        "schema_version": "mobile-project-profile-v1",
        "project_id": project_id,
        "profiled_revision": _profiled_revision(project_dir),
        "platforms": platforms,
        "frameworks": frameworks,
        "source_roots": roots,
        "build_systems": builds,
        "test_frameworks": tests,
        "confidence": confidence,
        "evidence_refs": evidence,
    }


def hand_rules(profile):
    """The one rule the keyword subset cannot express: confidence 'none'
    if and only if evidence_refs is empty, in both directions, so a
    detector bug can never claim confidence without evidence or evidence
    without confidence."""
    problems = []
    has_evidence = bool(profile.get("evidence_refs"))
    is_none = profile.get("confidence") == "none"
    if has_evidence and is_none:
        problems.append(
            "confidence: 'none' with %d evidence_refs present -- confidence "
            "must reflect the evidence, not contradict it"
            % len(profile["evidence_refs"]))
    if not has_evidence and not is_none:
        problems.append(
            "confidence: %r claimed with no evidence_refs -- every claim "
            "needs a real file backing it" % profile.get("confidence"))
    return problems


def check(profile, schema):
    problems = []
    CC.validate(profile, schema, "", problems)
    problems.extend(hand_rules(profile))
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("--project-id", default="unknown")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    profile = detect(args.project_dir, project_id=args.project_id)
    schema = CC.load_json(args.schema, "mobile-project-profile-v1 schema")
    problems = check(profile, schema)
    print(json.dumps(profile, indent=2, sort_keys=True))
    if problems:
        print("PROBLEMS:", file=sys.stderr)
        for p in problems:
            print(" - %s" % p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

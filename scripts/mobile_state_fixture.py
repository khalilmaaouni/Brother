#!/usr/bin/env python3
"""EPIC M2.01 Mobile State Fixture: a read-only loader/validator for
mobile-state-fixture-v1 records (docs/schema/mobile-state-fixture-v1.json).

This is SCHEMA AND VALIDATION ONLY, matching the unit's own scope: it
reads a fixture file, checks it against the schema plus a small set of
hand rules the schema's keyword subset cannot express, and reports
PASS/FAIL/NO-DATA. It never resets a simulator/emulator, never talks to
a backend, and never mutates keychain/keystore or app storage -- that is
M2.02's separate, later scope (state reset adapters). A fixture that
validates here is a well-formed DECLARATION of desired state, not yet an
achieved one.

Sibling modules in this repo (scripts/mobile_project_profile.py answers
"what is this project", scripts/mobile_toolchain_probe.py answers "what
can this machine run") are both GENERATORS: they detect/probe real
things and produce a record. This module is a VALIDATOR instead: the
record already exists as an author-written fixture file, and this module
only checks it. main() therefore takes a fixture path, not a project
directory or no arguments at all.

Exit contract, mirroring scripts/contract_check.py's own:
  0  PASS      the fixture satisfies the schema and the hand rules
  1  FAIL      one or more problems, each printed on its own line
  2  NO-DATA   the fixture or the schema could not be read as JSON

NO-DATA IS NOT A PASS: a fixture this module could not open or parse is
"could not look", never "looked and found nothing wrong".
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import contract_check as CC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCHEMA = os.path.join(ROOT, "docs", "schema", "mobile-state-fixture-v1.json")

#: Minimal BCP-47-ish shape for locale (e.g. "en-US"): this repo has no
#: existing locale whitelist/validator to reuse (checked), so a shape
#: check is the cheap, non-exhaustive backstop -- it will not catch every
#: invalid tag, only the obviously-garbage ones ("not a locale!!").
_LOCALE_SHAPE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

#: Reference-style fields (account_data_ref, checkpoint_ref, seeded_items,
#: external_services) must carry a POINTER, never an inline secret or PII
#: value. Mirrors this repo's own secret-scan shapes (github-desktop-push
#: skill's fail-closed grep) rather than inventing a new pattern: an
#: adversarial review of this schema (Muse, via the OpenRouter bridge,
#: 2026-09-15) found that a description saying "never a real value" is not
#: itself a guard. Not exhaustive -- a determined author can still evade
#: this -- but it catches the obvious cases (an email address, a bearer
#: token, a password= shape, a live https:// endpoint) cheaply.
_SECRET_SHAPE_PATTERN = re.compile(
    r"@.+\.|bearer\s|password\s*=|secret\s*=|token\s*=|"
    r"-----BEGIN|https?://|sk-[A-Za-z0-9]|AKIA[0-9A-Z]{10}",
    re.IGNORECASE)


def _reject_secret_shaped_values(problems, path, values):
    """values: an iterable of (field_label, value) pairs to check. Every
    non-string or empty value is skipped (that is a type/hand-rule problem
    reported elsewhere, not this rule's job)."""
    for label, value in values:
        if isinstance(value, str) and value and _SECRET_SHAPE_PATTERN.search(value):
            problems.append(
                "%s: looks like an inline secret/PII/live-endpoint value, "
                "not a reference -- %s must only ever point at a fixture, "
                "never carry one" % (label, path))


def _reject_blank_values(problems, values):
    """values: an iterable of (field_label, value) pairs. The schema's
    enforced keyword subset has no minLength/pattern, so an unconditionally
    required scalar can pass type:string as "" or "   " -- checked here by
    hand instead. Every non-string value is skipped (a type problem
    reported elsewhere)."""
    for label, value in values:
        if isinstance(value, str) and _is_blank(value):
            problems.append(
                "%s: must not be blank (empty or whitespace-only)" % label)


def _check_locale_shape(problems, value):
    if isinstance(value, str) and value and not _LOCALE_SHAPE.match(value):
        problems.append(
            "locale: %r does not look like a locale identifier "
            "(e.g. 'en-US')" % value)


def _check_timezone_is_real(problems, value):
    """A real IANA timezone-database lookup (stdlib zoneinfo), not a shape
    guess: "Mars/Olympus" matches an Area/Location shape but is not a real
    zone, so only an actual lookup catches it."""
    if isinstance(value, str) and value:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            problems.append(
                "timezone: %r is not a real IANA timezone name" % value)


def _check_clock_fixed_value_format(problems, value):
    """clock.fixed_value is documented as an ISO-8601 timestamp
    (docs/schema/mobile-state-fixture-v1.json); datetime.fromisoformat is
    the stdlib parser for it. Guarded, never let a ValueError raise
    uncaught (this repo's boundary-call rule). A trailing Z (UTC) is
    rewritten to +00:00 first: datetime.fromisoformat does not accept a
    bare Z suffix before Python 3.11, and this estate's floor is 3.9
    (same fix as scripts/l0_gate.py and elsewhere in this repo)."""
    if isinstance(value, str) and value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            problems.append(
                "clock.fixed_value: %r is not a valid ISO-8601 timestamp"
                % value)


def load_fixture(path):
    """Read and JSON-parse a fixture file. Raises CC.NoData (never a bare
    exception) on a missing file or invalid JSON, exactly as
    CC.load_json already does for every other schema-backed record in
    this repo -- reused rather than reimplemented."""
    return CC.load_json(path, "mobile-state-fixture-v1 fixture")


def _is_blank(value):
    """A value counts as "not provided" if it is missing/falsy (None,
    [], "", 0, False), OR a string that is empty once whitespace is
    stripped -- "   " is truthy in plain Python and would otherwise
    sail past every "required (non-empty)" rule below it routes
    through. Non-string values (lists, booleans) keep their plain
    falsy test unchanged."""
    if isinstance(value, str):
        return not value.strip()
    return not value


def _require_when(problems, obj, path, gate_field, gate_value, needed_field):
    """needed_field must be non-empty when obj[gate_field] == gate_value."""
    if obj.get(gate_field) == gate_value and _is_blank(obj.get(needed_field)):
        problems.append(
            "%s.%s: required (non-empty) when %s.%s is %r"
            % (path, needed_field, path, gate_field, gate_value))


def _forbid_unless(problems, obj, path, gate_field, gate_value, other_field):
    """other_field must be absent/empty whenever obj[gate_field] !=
    gate_value -- a stale value left over from a different mode (e.g. a
    checkpoint_ref surviving a switch to reset_mode 'clean') is exactly
    the kind of silently-wrong fixture this loader exists to refuse, not
    only a missing one. Folds in a real gap DeepSeek's independent draft
    of this schema caught: this module's first pass only checked the
    'required when' direction, never the 'must be absent otherwise' one."""
    if obj.get(gate_field) != gate_value and obj.get(other_field):
        problems.append(
            "%s.%s: must be absent/empty when %s.%s is not %r"
            % (path, other_field, path, gate_field, gate_value))


def hand_rules(fixture):
    """The rules the schema's keyword subset cannot express: each mode
    field gates a companion field two ways -- required when the mode
    needs it, forbidden (must stay absent) when it does not, so a
    fixture can never carry a stale companion value left over from a
    different mode. Documented in the schema's own per-field description
    so a reader of either file finds the same account. Returns every
    problem found, never stopping at the first (matching
    mobile_project_profile.hand_rules / mobile_toolchain_probe.
    hand_rules)."""
    problems = []

    app_storage = fixture.get("app_storage")
    if isinstance(app_storage, dict):
        _require_when(problems, app_storage, "app_storage", "reset_mode",
                      "restore-checkpoint", "checkpoint_ref")
        _forbid_unless(problems, app_storage, "app_storage", "reset_mode",
                       "restore-checkpoint", "checkpoint_ref")
        _require_when(problems, app_storage, "app_storage", "reset_mode",
                      "preserve-named-keys", "preserved_keys")
        _forbid_unless(problems, app_storage, "app_storage", "reset_mode",
                       "preserve-named-keys", "preserved_keys")

    keychain_policy = fixture.get("keychain_policy")
    if isinstance(keychain_policy, dict):
        _require_when(problems, keychain_policy, "keychain_policy", "mode",
                      "seeded", "seeded_items")
        _forbid_unless(problems, keychain_policy, "keychain_policy", "mode",
                       "seeded", "seeded_items")

    clock = fixture.get("clock")
    if isinstance(clock, dict):
        _require_when(problems, clock, "clock", "mode", "fixed", "fixed_value")
        _forbid_unless(problems, clock, "clock", "mode", "fixed", "fixed_value")

    network_profile = fixture.get("network_profile")
    if isinstance(network_profile, dict):
        _require_when(problems, network_profile, "network_profile", "mode",
                      "throttled", "detail")
        _forbid_unless(problems, network_profile, "network_profile", "mode",
                       "throttled", "detail")

    location = fixture.get("location")
    if isinstance(location, dict):
        mode = location.get("mode")
        lat = location.get("latitude")
        lon = location.get("longitude")
        has_lat = lat is not None
        has_lon = lon is not None
        if mode == "simulated" and not (has_lat and has_lon):
            problems.append(
                "location.latitude/longitude: both required when "
                "location.mode is 'simulated'")
        if mode != "simulated" and (has_lat or has_lon):
            problems.append(
                "location.latitude/longitude: must be absent when "
                "location.mode is not 'simulated'")
        if isinstance(lat, (int, float)) and not (-90 <= lat <= 90):
            problems.append(
                "location.latitude: %r is out of range -90..90" % lat)
        if isinstance(lon, (int, float)) and not (-180 <= lon <= 180):
            problems.append(
                "location.longitude: %r is out of range -180..180" % lon)

    cleanup = fixture.get("cleanup_policy")
    if isinstance(cleanup, dict):
        _require_when(problems, cleanup, "cleanup_policy", "required",
                      True, "method")
        _forbid_unless(problems, cleanup, "cleanup_policy", "required",
                       True, "method")

    app_storage_ref_check = fixture.get("app_storage") or {}
    keychain_ref_check = fixture.get("keychain_policy") or {}
    _reject_secret_shaped_values(problems, "account_data_ref", [
        ("account_data_ref", fixture.get("account_data_ref")),
    ])
    _reject_secret_shaped_values(problems, "app_storage.checkpoint_ref", [
        ("app_storage.checkpoint_ref", app_storage_ref_check.get("checkpoint_ref")),
    ])
    for i, item in enumerate(keychain_ref_check.get("seeded_items") or []):
        _reject_secret_shaped_values(
            problems, "keychain_policy.seeded_items",
            [("keychain_policy.seeded_items[%d]" % i, item)])
    for i, item in enumerate(fixture.get("external_services") or []):
        _reject_secret_shaped_values(
            problems, "external_services",
            [("external_services[%d]" % i, item)])

    # BLANK/GARBAGE SCALARS: the schema's enforced keyword subset has no
    # minLength/pattern (docs/schema/mobile-state-fixture-v1.json, top of
    # file), so these unconditionally-required scalars need a hand check.
    backend_dataset = fixture.get("backend_dataset")
    backend_dataset = backend_dataset if isinstance(backend_dataset, dict) else {}
    _reject_blank_values(problems, [
        ("account_data_ref", fixture.get("account_data_ref")),
        ("fixture_id", fixture.get("fixture_id")),
        ("backend_dataset.id", backend_dataset.get("id")),
        ("backend_dataset.version", backend_dataset.get("version")),
        ("locale", fixture.get("locale")),
        ("timezone", fixture.get("timezone")),
    ])
    for i, item in enumerate(app_storage_ref_check.get("preserved_keys") or []):
        _reject_blank_values(problems, [("app_storage.preserved_keys[%d]" % i, item)])
    for i, item in enumerate(keychain_ref_check.get("seeded_items") or []):
        _reject_blank_values(problems, [("keychain_policy.seeded_items[%d]" % i, item)])

    _check_locale_shape(problems, fixture.get("locale"))
    _check_timezone_is_real(problems, fixture.get("timezone"))
    clock_check = fixture.get("clock")
    if isinstance(clock_check, dict):
        _check_clock_fixed_value_format(problems, clock_check.get("fixed_value"))

    return problems


def check(fixture, schema):
    """Routes through CC.checked() (scripts/contract_check.py) so
    hand_rules only runs once validate() has confirmed the shapes it
    assumes -- the shared root-cause fix for a hand_rules that would
    otherwise crash on a structurally invalid fixture (e.g. app_storage
    given as an int instead of an object)."""
    return CC.checked(fixture, schema, lambda: hand_rules(fixture))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_path")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    try:
        fixture = load_fixture(args.fixture_path)
        schema = CC.load_json(args.schema, "mobile-state-fixture-v1 schema")
    except CC.NoData as exc:
        print("NO-DATA: %s" % exc, file=sys.stderr)
        return 2
    problems = check(fixture, schema)
    print(json.dumps(fixture, indent=2, sort_keys=True))
    if problems:
        print("PROBLEMS:", file=sys.stderr)
        for p in problems:
            print(" - %s" % p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

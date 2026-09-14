#!/usr/bin/env python3
"""Enabled Obsidian community plugins against the vault's own plugin policy.

99-System/obsidian-plugin-policy.json (see 50-Reference/plugin-trust-
measurement-2026-08-30.md for how it was classified) records each plugin's
version, main.js sha256 and a status of approved, review or refused. This
tool answers one question before the vault is trusted to run its plugins:
is every currently-ENABLED plugin (.obsidian/community-plugins.json) covered
by that policy, and is none of them refused. It never enables, disables or
edits a plugin; that stays the founder's call.

Exit 0: every enabled plugin is policy-covered and none is refused.
Exit 2: at least one enabled plugin is refused, missing from the policy, or
the vault/policy could not be read (NO-DATA). Python 3.9 floor, stdlib only.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import os
import sys


def cmd_check(vault):
    enabled_path = os.path.join(vault, ".obsidian", "community-plugins.json")
    policy_path = os.path.join(vault, "99-System", "obsidian-plugin-policy.json")
    try:
        with open(enabled_path, encoding="utf-8") as fh:
            enabled = json.load(fh)
    except (OSError, ValueError) as exc:
        print("bm_vault_plugins: NO-DATA, cannot read %s: %s" % (enabled_path, exc), file=sys.stderr)
        return 2
    try:
        with open(policy_path, encoding="utf-8") as fh:
            policy = json.load(fh)
    except (OSError, ValueError) as exc:
        print("bm_vault_plugins: NO-DATA, cannot read %s: %s" % (policy_path, exc), file=sys.stderr)
        return 2

    by_id = {p.get("id"): p for p in policy.get("plugins", [])}

    refused = [pid for pid in enabled if by_id.get(pid, {}).get("status") == "refused"]
    uncovered = [pid for pid in enabled if pid not in by_id]

    if refused or uncovered:
        for pid in refused:
            print("REFUSED: %s is enabled but policy status is refused" % pid)
        for pid in uncovered:
            print("UNCOVERED: %s is enabled but has no policy entry" % pid)
        return 2

    print("OK: %d plugin(s) enabled, all policy-covered, 0 refused" % len(enabled))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("check",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    args = ap.parse_args(argv)
    if not args.vault or not os.path.isdir(args.vault):
        print("bm_vault_plugins: NO-DATA, no readable vault at %r" % args.vault, file=sys.stderr)
        return 2
    return cmd_check(args.vault)


if __name__ == "__main__":
    sys.exit(main())

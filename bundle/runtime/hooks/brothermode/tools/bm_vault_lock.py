#!/usr/bin/env python3
"""bm_vault_lock: advisory writer-lock visibility for the Obsidian vault.

A peer session's already-staged files can end up swept into another session's
commit because "git commit" with no pathspec commits the whole index, not just
what was intended. The real fix is explicit pathspecs on every commit (applied
elsewhere); this lock adds VISIBILITY so a session can see another session is
mid-write before it even tries. It is advisory, not exclusive: acquire always
succeeds (last acquire wins), and the pre-commit hook only warns, never blocks,
on an active foreign lock. This is deliberate: a hard refusal here would have
the same blast-radius-exceeds-what-it-guards shape as the mistake it responds
to.

Lock file: "<vault>/.git/vault-writer.lock", JSON {"session", "acquired", "note"}.
Never committed: it lives inside .git/, which git itself never tracks.

  acquire --vault PATH --session ID --note TEXT   write the lock, always succeeds
  release --vault PATH --session ID               remove the lock, only if session matches
  check   --vault PATH                            print NONE / holder+ACTIVE / holder+STALE,
                                                    exit 0 always (informational, not a gate)

Python 3.9, standard library only, no network.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_VAULT = os.environ.get("BROTHERMODE_VAULT") or os.path.expanduser("~/Documents/Kay Vault")
STALE_SECONDS = 14400  # 4 hours, the estate's established stale-fence window


def _vault_root(cli_vault):
    if cli_vault:
        return cli_vault
    env = os.environ.get("BM_VAULT_ROOT")
    if env:
        return env
    return DEFAULT_VAULT


def _lock_path(vault_root):
    return os.path.join(vault_root, ".git", "vault-writer.lock")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _read_lock(path):
    """Returns the parsed lock dict, or None if absent/unreadable. An unreadable,
    corrupt, or structurally wrong (valid JSON that is not an object) lock file is
    treated the same as no lock: never a crash."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError) as e:
        sys.stderr.write("bm_vault_lock: cannot read %s: %s\n" % (path, e))
        return None
    if not isinstance(data, dict):
        sys.stderr.write(
            "bm_vault_lock: %s is valid JSON but not an object, treating as no lock\n" % path)
        return None
    return data


def cmd_acquire(args):
    vault = _vault_root(args.vault)
    path = _lock_path(vault)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = {"session": args.session, "acquired": _now_iso(), "note": args.note}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2, sort_keys=True)
        f.write("\n")
    print("acquired: %s" % path)
    print(json.dumps(lock, indent=2, sort_keys=True))
    return 0


def cmd_release(args):
    vault = _vault_root(args.vault)
    path = _lock_path(vault)
    lock = _read_lock(path)
    if lock is None:
        print("no lock held at %s, nothing to release" % path)
        return 0
    if lock.get("session") != args.session:
        print("refused: lock at %s is held by session %r, not %r" % (
            path, lock.get("session"), args.session))
        return 1
    os.remove(path)
    print("released: %s (was held by %s)" % (path, args.session))
    return 0


def cmd_check(args):
    vault = _vault_root(args.vault)
    path = _lock_path(vault)
    lock = _read_lock(path)
    if lock is None:
        print("NONE")
        return 0
    try:
        acquired_dt = _parse_iso(lock.get("acquired", ""))
        age_seconds = (datetime.now(timezone.utc) - acquired_dt).total_seconds()
        state = "STALE" if age_seconds > STALE_SECONDS else "ACTIVE"
    except ValueError:
        age_seconds = None
        state = "STALE"  # unparseable timestamp is never trusted as fresh
    print("holder: %s" % lock.get("session", "unknown"))
    print("acquired: %s" % lock.get("acquired", "unknown"))
    if lock.get("note"):
        print("note: %s" % lock["note"])
    print(state)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("acquire", help="write the lock (always succeeds, last acquire wins)")
    pa.add_argument("--vault", default=None)
    pa.add_argument("--session", required=True)
    pa.add_argument("--note", default="")

    pr = sub.add_parser("release", help="remove the lock, only if session matches")
    pr.add_argument("--vault", default=None)
    pr.add_argument("--session", required=True)

    pc = sub.add_parser("check", help="print lock state, exit 0 always")
    pc.add_argument("--vault", default=None)

    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "acquire":
        return cmd_acquire(args)
    if args.cmd == "release":
        return cmd_release(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())

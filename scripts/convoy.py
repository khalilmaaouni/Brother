"""convoy: the finding packet, the immutable unit of work of the Convoy conductor.

WHY A PACKET, AND WHY IT IS RE-RUN BEFORE IT IS BELIEVED. A finding that reaches
a fixer as prose ("this function drops a case") is a claim about a checkout,
carrying that checkout's date. A packet turns the claim into something a machine
can refute: a failing test, the exact text it must fail with, a control test that
must stay green, and the sha256 of the test file at the commit the claim was made
against. accept() does not read the packet's story. It rebuilds the base in a
throwaway detached worktree and watches the two tests run.

A packet is a JSON object:
  id, base_sha, behaviour, test_path, test_id, expected_failure_text,
  control_test_id, digest            (all required, non-empty strings)
  reads, writes, contracts, severity (optional, carried, never judged here)

ACCEPTED needs every one of these, checked in this order, first failure named:
  1. every required field is a non-empty string
  2. base_sha resolves to a commit
  3. test_path exists at base_sha and its sha256 equals digest
  4. `python3 -B -m unittest <test_id>` exits non-zero AND its combined output
     contains expected_failure_text (a test that is red for some OTHER reason
     proves nothing about the stated behaviour)
  5. `python3 -B -m unittest <control_test_id>` exits 0 (a control that is red
     means the fixture, not the behaviour, is broken)
Anything else is NO-DATA with the reason. There is no FAIL verdict: an
unverifiable packet is not a wrong finding, it is one nobody can act on yet, and
it is never upgraded to ACCEPTED. A timeout is NO-DATA for the same reason.

THE BASE IS RUN WHERE IT WAS CLAIMED. The runs happen in a temporary
`git worktree add --detach` of base_sha with the working directory at that
worktree's root, so a later fix on the branch cannot turn a true finding into a
false refusal and a dirty checkout cannot leak into the verdict. The worktree is
removed by its exact path afterwards, on every exit path, and only that path.

dedupe() collapses packets that name the same (test_id, base_sha): two workers
who found the same failing test on the same base found one thing. The first is
kept. A packet with no usable key is never merged with anything, because a
missing key is not evidence of sameness.

Python 3.9+, standard library only, plus git.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

REQUIRED = ("id", "base_sha", "behaviour", "test_path", "test_id",
            "expected_failure_text", "control_test_id", "digest")
DEFAULT_TIMEOUT = 300


def _run(argv, cwd, timeout):
    """Run one command; return (returncode, combined output) or (None, reason) on a timeout."""
    try:
        done = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout after %ss" % timeout
    except OSError as exc:
        return None, "could not run %s: %s" % (argv[0], exc)
    return done.returncode, done.stdout.decode("utf-8", "replace")


def _missing_field(packet):
    for name in REQUIRED:
        value = packet.get(name)
        if not isinstance(value, str) or not value.strip():
            return name
    return None


def _safe_arg(value):
    """An option-shaped value must never reach git or unittest as an argument."""
    return not value.startswith("-")


def _resolve_base(repo, sha, timeout):
    if not _safe_arg(sha):
        return None
    code, out = _run(["git", "rev-parse", "--verify", "--quiet", sha + "^{commit}"], repo, timeout)
    return out.strip() if code == 0 and out.strip() else None


def _digest_at(repo, sha, path, timeout):
    """sha256 hex of `path` at `sha`, or None when the file is not in that commit."""
    if not _safe_arg(path) or os.path.isabs(path):
        return None
    try:
        done = subprocess.run(["git", "show", "%s:%s" % (sha, path)], cwd=repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    return hashlib.sha256(done.stdout).hexdigest()


def _verdict_on_base(packet, root, timeout):
    """Conditions 4 and 5, run with cwd at the base worktree root."""
    for name in ("test_id", "control_test_id"):
        if not _safe_arg(packet[name]):
            return "NO-DATA", "%s is option-shaped" % name
    code, out = _run([sys.executable, "-B", "-m", "unittest", packet["test_id"]], root, timeout)
    if code is None:
        return "NO-DATA", "test_id: %s" % out
    if code == 0:
        return "NO-DATA", "test_id exited 0 on base: the behaviour is not red there"
    if packet["expected_failure_text"] not in out:
        return "NO-DATA", "test_id failed but its output lacks expected_failure_text"
    code, out = _run([sys.executable, "-B", "-m", "unittest", packet["control_test_id"]], root, timeout)
    if code is None:
        return "NO-DATA", "control_test_id: %s" % out
    if code != 0:
        return "NO-DATA", "control_test_id exited %d on base: the control is not green" % code
    return "ACCEPTED", "test red with the stated text, control green, on base"


def accept(packet, repo, timeout=DEFAULT_TIMEOUT):
    """Return (verdict, reason): ACCEPTED only when the packet reproduces on its base."""
    if not isinstance(packet, dict):
        return "NO-DATA", "packet is not a JSON object"
    missing = _missing_field(packet)
    if missing:
        return "NO-DATA", "field %s is missing or not a non-empty string" % missing
    repo = os.path.abspath(repo)
    base = _resolve_base(repo, packet["base_sha"], timeout)
    if base is None:
        return "NO-DATA", "base_sha does not resolve to a commit"
    digest = _digest_at(repo, base, packet["test_path"], timeout)
    if digest is None:
        return "NO-DATA", "test_path does not exist at base_sha"
    if digest != packet["digest"]:
        return "NO-DATA", "digest does not match test_path at base_sha"
    parent = tempfile.mkdtemp(prefix="convoy-")
    root = os.path.join(parent, "base")
    added = False
    try:
        code, out = _run(["git", "worktree", "add", "--detach", root, base], repo, timeout)
        if code != 0:
            return "NO-DATA", "could not create the base worktree: %s" % (out or "no output").strip()
        added = True
        return _verdict_on_base(packet, root, timeout)
    finally:
        if added:
            # our own throwaway tree, removed by its exact path
            _run(["git", "worktree", "remove", "--force", root], repo, timeout)
        shutil.rmtree(parent, ignore_errors=True)
        _run(["git", "worktree", "prune"], repo, timeout)


def _key(packet):
    if isinstance(packet, dict):
        test_id, base = packet.get("test_id"), packet.get("base_sha")
        if isinstance(test_id, str) and isinstance(base, str) and test_id and base:
            return (test_id, base)
    return None


def dedupe(packets):
    """One packet per (test_id, base_sha), first kept; keyless packets are all kept."""
    seen, out = set(), []
    for packet in packets:
        key = _key(packet)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(packet)
    return out


def _load(path):
    """Return (packet, None) or (None, reason)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)
    if not isinstance(data, dict):
        return None, "%s is not a JSON object" % path
    return data, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("packet-accept", help="re-run a packet on its base")
    pa.add_argument("packet")
    pa.add_argument("--repo", default=".")
    pa.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    pd = sub.add_parser("packet-dedupe", help="unique packets by (test_id, base_sha)")
    pd.add_argument("packets", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "packet-accept":
        packet, why = _load(args.packet)
        if packet is None:
            print("NO-DATA %s: %s" % (os.path.basename(args.packet), why))
            return 2
        verdict, reason = accept(packet, args.repo, timeout=args.timeout)
        if verdict == "ACCEPTED":
            print("ACCEPTED %s" % packet["id"])
            return 0
        print("NO-DATA %s: %s" % (packet.get("id", os.path.basename(args.packet)), reason))
        return 2

    packets = []
    for path in args.packets:
        packet, why = _load(path)
        if packet is None:
            # a packet that cannot be read is never silently dropped from the count
            print("NO-DATA %s" % why, file=sys.stderr)
            return 2
        packets.append(packet)
    unique = dedupe(packets)
    for packet in unique:
        print(packet.get("id", ""))
    print("UNIQUE %d of %d" % (len(unique), len(packets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

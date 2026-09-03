#!/usr/bin/env python3
"""The access policy contract: recall trims by identity BEFORE content is served.

WHY THIS EXISTS. No per-folder or per-note ACL exists anywhere in the Obsidian
ecosystem, so the ACL lives in Brother's retrieval layer or nowhere. Every
enterprise retrieval product that survived contact with real estates (Glean,
Copilot, Rovo) trims by identity at query time, before generation, because a
model that has already read a forbidden note has already leaked it. This module
is the ONE owner of the policy file's shape and the allow/deny decision;
bm_vault.py only calls it, so retrieval can never drift into a second reading
of the same rules.

THE POLICY FILE, vault-relative 99-System/access-policy.json (overridable):

  {
    "default": "allow",                          optional; "deny" flips it
    "groups": {"team": ["alice", "bob"]},        optional
    "rules": [
      {"identity": "bob", "path": "50-Private/*", "action": "deny"},
      {"group": "team",   "path": "*",            "action": "allow"},
      {"identity": "*",   "path": "40-Legal/*",   "action": "require_approval"}
    ]
  }

THE DECISION, stated in full because access control that surprises is worse
than none:
  - NO policy file at all: everything readable. That is today's behavior,
    stated openly, so adoption is opt-in and nothing breaks.
  - Every rule whose identity matches (the rule's identity equals the
    caller's, or "*", or the caller is in the rule's group) AND whose path
    glob matches the note's vault-relative path participates.
  - PRECEDENCE ON TIE, most restrictive wins: deny beats require_approval
    beats allow, whatever order the rules are written in.
  - No rule matches: the default applies. Deny-by-default happens ONLY when
    the policy declares "default": "deny" (default itself is only ever
    "allow" or "deny"; a rule's action additionally allows
    "require_approval", but that is never a valid default -- a fallback
    that always needs a human is not a default, it is a standing outage).
  - An ABSENT identity (no --identity, no BM_IDENTITY) is anonymous: it
    matches only "*" rules, and otherwise falls to the default, so a
    default-deny policy denies the anonymous caller.
  - A note OUTSIDE the vault (project memory, correction rules) has no
    vault-relative path; it is matched by its full path, which a "*" glob
    still covers, and otherwise falls to the default.

VB3-04: DUAL PRINCIPALS, REQUIRE_APPROVAL, FAIL CLOSED.

  REQUIRE_APPROVAL is a third verdict, alongside allow and deny: a rule can
  name an approval-required class of content (a path glob) rather than an
  outright allow or deny. decide() returns the string "require_approval" for
  such a note; it is the caller's job (bm_vault.py's recall path) to treat
  that exactly like deny -- withhold the content -- UNLESS an approval
  already exists through this estate's own promotion or approval-pane
  ceremony (bm_vault_promotions.py / bm_vault_pane.py), never by inventing a
  second approval store here.

  decide_dual(policy, human, agent, purpose, relpath) is the dual-principal
  entry point: an agent acting on a human's behalf is scoped to the
  INTERSECTION of both principals' access, never the union, because a
  narrower agent must never be a backdoor to a broader human's set. agent is
  optional (None): omitted, this is byte-identical to decide(policy, human,
  relpath), so single-machine, human-only callers are unaffected. purpose is
  accepted for the caller's own audit record (every decision's purpose is
  recorded there, VB7-04) and plays no part in matching: no rule vocabulary
  here is purpose-scoped.

  FAIL CLOSED belongs to the CALLER, not to this module, and is stated here
  only so the two halves of the contract sit next to each other: a policy
  module that cannot be imported, or whose decide() raises, must never be
  read by an enterprise-mode caller as "nothing is restricted". bm_vault.py's
  own _policy_deny is where that caller-side fallback lives (it must work
  even when THIS module is the thing that is missing or broken, so it holds
  its own, independent definition of "restricted" rather than calling back
  into a module already shown to be untrustworthy). Single-machine mode
  (no BROTHERMODE_ENTERPRISE=1) keeps today's behavior: a broken or absent
  policy module degrades to "not trimmed", stated on stderr, never a crash.

WHAT A DENIAL LOOKS LIKE downstream, and why: a withheld note NEVER prints
its title, path, or content, because naming what someone may not see is
itself a leak. It is counted once, in one summary line. A require_approval
verdict without a matching approval is withheld the identical way.

  check   validate the policy: unknown keys, bad rules, unreachable rules
          named; NO-DATA when the file is absent.

Exit 0 clean, 1 on findings, 2 NO-DATA. Python 3.9, stdlib only, writes
nothing anywhere.
"""
import fnmatch
import json
import os
import sys

POLICY_RELPATH = os.path.join("99-System", "access-policy.json")
TOP_KEYS = {"default", "groups", "rules"}
RULE_KEYS = {"identity", "group", "path", "action"}
# VB3-04: a RULE's action gets a third option, require_approval; "default" (the
# fallback with no matching rule) never does -- a fallback that always needs a
# human is not a fallback. DEFAULTS and ACTIONS are validated against
# separately for exactly this reason; do not collapse them back into one set.
DEFAULTS = {"allow", "deny"}
ACTIONS = {"allow", "deny", "require_approval"}
# Most-restrictive-wins rank, shared by decide()'s own tie-break and by
# decide_dual()'s intersection of two principals' verdicts.
_RANK = {"allow": 0, "require_approval": 1, "deny": 2}


def policy_path(vault, override=None):
    """The policy file's path: an explicit override, else vault-relative,
    else None when no vault is configured either."""
    if override:
        return override
    if vault:
        return os.path.join(vault, POLICY_RELPATH)
    return None


def load(path):
    """(policy dict | None, problems list). None with no problems when the
    file is absent: that is the opt-in state, not an error. A present but
    unreadable or malformed file is (None, [reason]): a broken policy must
    never silently become "everything readable"."""
    if not path or not os.path.isfile(path):
        return None, []
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as e:
        return None, ["unreadable policy %s: %s" % (path, e)]
    if not isinstance(loaded, dict):
        return None, ["policy %s is not a JSON object" % path]
    return loaded, []


def validate(policy):
    """Every problem in the policy, named. Empty list means clean."""
    problems = []
    for k in sorted(set(policy) - TOP_KEYS):
        problems.append("unknown top-level key %r" % k)
    default = policy.get("default", "allow")
    if default not in DEFAULTS:
        problems.append("default must be allow or deny, got %r" % default)
    groups = policy.get("groups", {})
    if not isinstance(groups, dict) or any(
            not isinstance(v, list) for v in groups.values()):
        problems.append("groups must map group name to a list of identities")
        groups = {}
    rules = policy.get("rules", [])
    if not isinstance(rules, list):
        return problems + ["rules must be a list"]
    seen_matchers = set()
    for i, rule in enumerate(rules):
        tag = "rule %d" % i
        if not isinstance(rule, dict):
            problems.append("%s is not an object" % tag)
            continue
        for k in sorted(set(rule) - RULE_KEYS):
            problems.append("%s: unknown key %r" % (tag, k))
        who, grp = rule.get("identity"), rule.get("group")
        if (who is None) == (grp is None):
            problems.append("%s: exactly one of identity or group required" % tag)
        if grp is not None and grp not in groups:
            problems.append("%s: group %r not declared in groups" % (tag, grp))
        path = rule.get("path")
        if not isinstance(path, str) or not path:
            problems.append("%s: path must be a non-empty glob string" % tag)
            path = None
        if rule.get("action") not in ACTIONS:
            problems.append("%s: action must be allow, deny or require_approval, got %r"
                            % (tag, rule.get("action")))
        # Unreachable: a later rule with the identical matcher can never
        # change the verdict of an earlier one (deny wins regardless of
        # order, and a duplicate allow adds nothing), so it is dead text
        # someone will one day edit expecting an effect.
        matcher = (who, grp, path)
        if path is not None:
            if matcher in seen_matchers:
                problems.append("%s: unreachable, an earlier rule has the "
                                "same identity and path" % tag)
            seen_matchers.add(matcher)
    return problems


def _identity_matches(rule, identity, groups):
    who, grp = rule.get("identity"), rule.get("group")
    if who is not None:
        return who == "*" or (identity is not None and who == identity)
    if grp is not None:
        return identity is not None and identity in groups.get(grp, [])
    return False


def decide(policy, identity, relpath):
    """"allow", "deny" or "require_approval" (VB3-04) for one identity and one note
    path. relpath is the note's vault-relative path (forward slashes) or, for a note
    outside the vault, its full path. Most-restrictive-wins on tie (deny beats
    require_approval beats allow); the default (always allow or deny, never
    require_approval) decides when no rule matches."""
    groups = policy.get("groups", {}) if isinstance(policy.get("groups"), dict) else {}
    rel = relpath.replace(os.sep, "/")
    matched = set()
    for rule in policy.get("rules", []):
        if not isinstance(rule, dict):
            continue
        if not _identity_matches(rule, identity, groups):
            continue
        pat = rule.get("path")
        if not isinstance(pat, str) or not fnmatch.fnmatch(rel, pat):
            continue
        action = rule.get("action")
        if action in _RANK:
            matched.add(action)
    if matched:
        return max(matched, key=_RANK.get)
    return "deny" if policy.get("default") == "deny" else "allow"


def decide_dual(policy, human, agent, purpose, relpath):
    """The dual-principal decision (VB3-04): an agent acting for a human is scoped to
    the INTERSECTION of both principals' access, never the union -- most restrictive
    of the two verdicts wins (deny > require_approval > allow), so an agent scoped
    narrower than its human can only narrow the outcome, never retrieve the human's
    broader set on the agent's own account. agent=None (the single-machine, human-only
    case) makes this byte-identical to decide(policy, human, relpath). purpose takes no
    part in the decision -- it exists so a caller can thread one call's purpose through
    to its own audit record (VB7-04); no rule vocabulary here is purpose-scoped."""
    del purpose  # accepted for the caller's audit record only; see docstring
    verdict = decide(policy, human, relpath)
    if agent is not None:
        agent_verdict = decide(policy, agent, relpath)
        if _RANK[agent_verdict] > _RANK[verdict]:
            verdict = agent_verdict
    return verdict


def cmd_check(path):
    if not path or not os.path.isfile(path):
        print("bm_vault_policy: NO-DATA, no policy file at %r. That is the "
              "opt-in state: with no policy, everything stays readable, "
              "exactly as before this module existed." % path)
        return 2
    policy, problems = load(path)
    if policy is None:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    problems = validate(policy)
    if problems:
        print("policy %s: %d finding(s)" % (path, len(problems)))
        for p in problems:
            print("  FINDING: %s" % p)
        return 1
    print("policy %s: clean, %d rule(s), default %s"
          % (path, len(policy.get("rules", [])), policy.get("default", "allow")))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] != "check":
        sys.stderr.write("bm_vault_policy: unknown command %r; known: check\n" % argv[0])
        return 2
    override = None
    if "--policy" in argv:
        i = argv.index("--policy")
        override = argv[i + 1] if i + 1 < len(argv) else None
    vault = os.environ.get("BM_VAULT_ROOT") or os.environ.get("BROTHERMODE_VAULT")
    return cmd_check(policy_path(vault, override))


if __name__ == "__main__":
    sys.exit(main())

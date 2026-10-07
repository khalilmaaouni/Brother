#!/usr/bin/env python3
"""A caller supplied registry cannot clear private content onto a third party transport.

WHAT THIS EXISTS TO CATCH, measured 2026-09-22 before the fix and reproduced verbatim. The rule
"a third party transport may only ever carry public content" was enforced inside load_registry(),
so it bound a registry read from DISK and nothing else. chain(), assert_may_send() and
model_call.call_one() all accept a registry as an ARGUMENT, and registry(reg) handed that argument
straight back unexamined. A caller passing its own dict therefore disarmed every gate at once:

    poison = {"external": {"id": "vendor/leaky", "transport": "bridge",
                           "privacy": "private", "quality": {"build": 9}, "cost": 1}}
    chain("build", PRIVATE, registry_arg=poison)            -> ['external']
    assert_may_send("external", PRIVATE, "build", poison)   -> True

This is the only failure on the module that sends content OFF the machine, so the check is kept
separate from the module's own selftest: a guard proved only by the file it guards is proved by
whoever was already willing to edit that file.

FIXTURES ARE ORTHOGONAL BY CONSTRUCTION. Every bad registry below differs from one good row in
EXACTLY ONE way, and every refusal is matched against the REASON it must give. That pairing is the
point: the sibling modules were measured this month passing a fixture that tripped two guards at
once, where deleting either guard changed nothing because the other still fired.

HERMETIC: nothing here reads the real registry, HOME, or the network, so it passes under an empty
HOME. Run:  python3 scripts/test_privacy_cannot_be_bypassed.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import model_router as R          # noqa: E402
import model_call as C            # noqa: E402


def good_row(**over):
    """One LAWFUL row: a local transport, so it may hold the most sensitive class there is.

    Every fixture below is this row with a single field changed, which is what makes a refusal
    attributable to that field and nothing else."""
    row = {"id": "x/ok", "transport": "claude", "privacy": R.PRIVATE,
           "quality": {"build": 7}, "cost": 1.0}
    row.update(over)
    # `kinds` is supplied unless a case is deliberately testing that it gets re-derived. MEASURED:
    # without this, a mutation that disables validation made the whole file die on KeyError before
    # any case printed, so the mutation was "killed" by a crash whose message named the wrong
    # thing. A fixture whose failure reports an incidental error proves nothing about the guard.
    row.setdefault("kinds", set(row["quality"]))
    return row


def refuses_saying(fn, text):
    """The call must raise Refused AND say why. A refusal for the wrong reason is a fixture that
    proves a different guard than the one it is named for, which is the error this file avoids."""
    try:
        fn()
    except R.Refused as exc:
        return text in str(exc), "refused with %r, wanted %r" % (str(exc)[:110], text)
    except Exception as exc:                      # noqa: BLE001
        return False, "raised %s, not Refused: %s" % (type(exc).__name__, exc)
    return False, "DID NOT REFUSE"


def main():
    cases, bad = [], []

    def case(name, ok, detail=""):
        cases.append(name)
        if not ok:
            bad.append("%s (%s)" % (name, detail))

    # ---------------------------------------------------------- the reported bypass, three doors
    # THE POISONED ROW: lawful in every respect except that a third party transport claims it may
    # hold private content. It must be refused at whichever gate the caller reaches first.
    poison = {"external": good_row(id="vendor/leaky", transport="bridge", privacy=R.PRIVATE,
                                   quality={"build": 9})}
    third = "third party"

    case("chain() refuses a caller supplied registry that clears a bridge for private content",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={}, registry_arg=dict(poison)),
                         third))
    case("assert_may_send() refuses the same registry at the wire",
         *refuses_saying(lambda: R.assert_may_send("external", R.PRIVATE, "build", dict(poison)),
                         third))
    case("may_receive() refuses it rather than answering True",
         *refuses_saying(lambda: R.may_receive("external", R.PRIVATE, dict(poison)), third))

    # call_one is the door that actually transmits. The runner must never be reached.
    reached = []

    def spy(argv, stdin, timeout):
        reached.append(argv)
        return {"returncode": 0, "stdout": "leaked", "stderr": ""}

    ok, detail = refuses_saying(
        lambda: C.call_one("external", "secret", "build", R.PRIVATE, 5, dict(poison), spy), third)
    case("model_call.call_one() refuses it before the transport runs", ok, detail)
    case("and the transport was never invoked", not reached, "runner ran: %r" % (reached,))

    # ---------------------------------------------------------- one condition per fixture
    # Each row below is good_row() with EXACTLY ONE field changed, so only one guard can fire.
    case("the codex transport is a third party too, not only the bridge",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={},
                                         registry_arg={"m": good_row(transport="codex")}), third))
    case("INTERNAL is not public, so a third party may not hold it either",
         *refuses_saying(lambda: R.chain("build", R.INTERNAL, rel={},
                                         registry_arg={"m": good_row(transport="bridge",
                                                                     privacy=R.INTERNAL)}), third))
    # FAIL DIRECTION: an unrecognised transport refuses. Kept at PUBLIC so the third party rule
    # cannot fire and only the transport guard can.
    case("an unrecognised transport refuses rather than being assumed local",
         *refuses_saying(lambda: R.chain("build", R.PUBLIC, rel={},
                                         registry_arg={"m": good_row(transport="webhook",
                                                                     privacy=R.PUBLIC)}),
                         "unknown transport"))
    case("an unrecognised privacy value refuses, never degrading to public",
         *refuses_saying(lambda: R.chain("build", R.PUBLIC, rel={},
                                         registry_arg={"m": good_row(privacy="banana")}),
                         "unknown privacy class"))

    # FAIL DIRECTION: an EXPLICIT null privacy refuses. Distinct from the missing-key case below:
    # a key present but empty is what a serialiser emits, and `None` is not a privacy class.
    case("privacy explicitly set to None refuses, since None is not a class",
         *refuses_saying(lambda: R.chain("build", R.PUBLIC, rel={},
                                         registry_arg={"m": good_row(privacy=None)}),
                         "unknown privacy class"))
    # THE CASE-VARIANT TRAP: a guard spelled `transport == "bridge"` would read "BRIDGE" as a
    # transport it does not know and wave it through. The allowlist refuses it instead. Held at
    # PRIVATE so a pass here would be a real leak, not a cosmetic one.
    case("a case variant transport refuses, never read as a transport we do not police",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={},
                                         registry_arg={"m": good_row(transport="BRIDGE",
                                                                     privacy=R.PRIVATE)}),
                         "unknown transport"))

    no_privacy = good_row()
    del no_privacy["privacy"]
    case("a row with NO privacy field refuses, since unlabelled is never public",
         *refuses_saying(lambda: R.chain("build", R.PUBLIC, rel={},
                                         registry_arg={"m": no_privacy}), "missing 'privacy'"))

    # ---------------------------------------------------------- shape edges
    case("an EMPTY registry refuses, never reading as nothing is forbidden",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={}, registry_arg={}),
                         "names no models"))
    case("a registry shaped as a LIST refuses instead of crashing on .values()",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={},
                                         registry_arg=[good_row()]), "names no models"))
    case("a row that is not a mapping refuses, rather than substring testing a string",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={},
                                         registry_arg={"m": "claude"}), "not a mapping"))
    # THE LAUNDERING SHAPE: selection picks by name, the ledger keys by id, so one endpoint offered
    # under two sets of terms lets a caller pick the permissive name and reach the same vendor.
    case("one vendor id offered under two different sets of terms refuses",
         *refuses_saying(lambda: R.chain("build", R.PRIVATE, rel={}, registry_arg={
             "strict": good_row(id="vendor/same"),
             "loose": good_row(id="vendor/same", transport="bridge", privacy=R.PUBLIC)}),
             "twice with different terms"))

    # ---------------------------------------------------------- the guard must still say YES
    # WITHOUT THESE THE FILE IS WORTHLESS: a guard that refuses everything passes every case above
    # while breaking the router completely.
    one = {"solo": good_row()}
    case("exactly one lawful local model still routes private content",
         R.chain("build", R.PRIVATE, rel={}, registry_arg=dict(one)) == ["solo"])
    lawful_bridge = {"pub": good_row(id="vendor/pub", transport="bridge", privacy=R.PUBLIC)}
    case("a lawful bridge row still carries PUBLIC content",
         R.chain("build", R.PUBLIC, rel={}, registry_arg=dict(lawful_bridge)) == ["pub"])
    case("assert_may_send still returns True for a lawful pairing",
         R.assert_may_send("pub", R.PUBLIC, "build", dict(lawful_bridge)) is True)

    # FAIL DIRECTION: an unlabelled sensitivity is treated as the MOST sensitive, so the same
    # lawful bridge row that carries public content must NOT be offered an unlabelled task.
    case("an unlabelled sensitivity fails closed to private and reaches no third party",
         R.chain("build", "unlabelled", rel={}, registry_arg=dict(lawful_bridge)) == [])

    # A caller supplied `kinds` that disagrees with its own quality map is not trusted.
    case("kinds is re-derived from quality, never taken on the caller's word",
         R.can_do("m", "build", {"m": good_row(quality={"decide": 9}, kinds={"build"})}) is False)

    print("test_privacy_cannot_be_bypassed: %d cases, %s"
          % (len(cases), "OK" if not bad else "FAILED: " + "; ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

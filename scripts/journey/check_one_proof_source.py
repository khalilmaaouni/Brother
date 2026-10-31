#!/usr/bin/env python3
"""J4's C.one-proof-source: is the verdict rendered once, or twice?

Until this existed the row carried `measured_by: PC1` and no check, so it read "the row
declares no check, so nothing measured it" forever and PC1H could never close.

THE ROW'S OWN DIFFICULTY, in its words: "Judging one proof source needs two that could
disagree, which needs a run and its receipt". PC1's objective is that
`receipt_door.verdict_lines` renders THROUGH proof_card "so there is one renderer".
Today it does not: it builds its text inline and calls brother_run.format_result_line
for the first line, while proof_card.render_text builds the card separately. Two
renderers of the same facts, free to drift apart.

HOW THIS MEASURES IT, and it is not a grep. A SUBSTITUTION PROBE: render the verdict
once with proof_card intact, then replace every public callable proof_card offers with a
stub and render again. If the second render differs from the first, or breaks, then
verdict_lines depends on proof_card and there is ONE renderer. If the two renders are
byte identical, verdict_lines does not touch proof_card at all and there are TWO.

An import that is never used cannot satisfy this, and neither can a mention in a
docstring: the only thing that changes the answer is the output actually coming from
the other module.

BOTH SHAPES OF THE DEPENDENCY ARE COVERED. `proof_card.render(...)` is caught by
stubbing the module's attributes; `from proof_card import render` is caught by also
stubbing any callable on receipt_door that was DEFINED in proof_card.py, found by
comparing the function's own __globals__ __file__. One without the other would let a
real dependency read as absent.

A BREAK IS A PASS, deliberately. If replacing proof_card makes verdict_lines raise, that
is the strongest possible evidence it depends on it. The probe records that it raised
rather than hiding it.

    python3 -B scripts/journey/check_one_proof_source.py
    python3 -B scripts/journey/check_one_proof_source.py --selftest

EXIT 0 PASS, 1 FAIL, 2 NO-DATA.
"""
import argparse
import importlib.util
import os
import sys

MARKER = "ONE-PROOF-SOURCE-PROBE"
#: A view with a unit and a changed file, so verdict_lines renders its per-unit lines
#: and not only the result line. The result line's own fields are valid values from
#: brother_run's RESULT_STATES, RELEASE_VALUES and NEXT_VALUES, so the line is the real
#: one rather than the safe fallback.
VIEW = {
    "readable": True,
    "state": "DONE",
    "release": "DECISION_RECORDED",
    "next": "review-receipt",
    "counts": {"PASS": 1, "FAIL": 0, "NO-DATA": 0},
    "units": [{"unit": "U1", "files": ["a.py"], "command": "python3 -B t.py",
               "exit_code": 0, "verdict": "PASS", "unproven_reason": ""}],
}


def load(path, name):
    """Load a module by path with its own directory importable, since both of these
    import siblings and a load without that raises ModuleNotFoundError."""
    directory = os.path.dirname(path)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _defined_in(func, path):
    """Was this callable defined in the file at `path`? Asked of the function's own
    globals, which is what catches a name imported with `from proof_card import x`."""
    try:
        return os.path.abspath(func.__globals__.get("__file__") or "") == path
    except AttributeError:
        return False


def stub_card(door, card):
    """Replace every public callable proof_card offers, in both places a caller could
    have bound it. Returns a restore callable."""
    def stub(*_a, **_k):
        return MARKER
    saved = []
    card_path = os.path.abspath(getattr(card, "__file__", "") or "")
    for name in dir(card):
        if name.startswith("_"):
            continue
        value = getattr(card, name, None)
        if callable(value):
            saved.append((card, name, value))
            setattr(card, name, stub)
    for name in dir(door):
        if name.startswith("_"):
            continue
        value = getattr(door, name, None)
        if callable(value) and _defined_in(value, card_path):
            saved.append((door, name, value))
            setattr(door, name, stub)

    def restore():
        for holder, name, value in saved:
            setattr(holder, name, value)
    return restore, len(saved)


def judge(baseline, patched, stubbed):
    """(exit code, one line). Pure, so the selftest judges THIS and not a copy.

    `baseline` is the verdict as rendered normally, or None when it could not be
    rendered at all. `patched` is the verdict with proof_card replaced, or the string
    RAISED. `stubbed` is how many callables were replaced."""
    if baseline is None:
        return 2, ("NO-DATA: the verdict could not be rendered at all, so nothing has "
                   "been shown about how many renderers there are")
    if not stubbed:
        return 2, ("NO-DATA: proof_card offered no public callable to replace, so the "
                   "probe had nothing to substitute and measured nothing")
    if patched == "RAISED":
        return 0, ("PASS: replacing proof_card made the verdict renderer break, so the "
                   "verdict is rendered through it and there is one proof source")
    if patched != baseline:
        return 0, ("PASS: replacing proof_card changed the verdict text, so the verdict "
                   "is rendered through it and there is one proof source")
    return 1, ("FAIL: the verdict text is byte identical with all %d of proof_card's "
               "callables replaced, so the verdict renderer does not use it: there are "
               "two renderers of the same facts and nothing stops them drifting apart"
               % stubbed)


def probe(door, card):
    """(baseline, patched, stubbed) for the installed pair."""
    try:
        baseline = "\n".join(door.verdict_lines(dict(VIEW)))
    except Exception:
        return None, None, 0
    restore, stubbed = stub_card(door, card)
    try:
        try:
            patched = "\n".join(door.verdict_lines(dict(VIEW)))
        except Exception:
            patched = "RAISED"
    finally:
        restore()
    return baseline, patched, stubbed


def _find(config, name):
    for base, dirs, files in os.walk(config):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if name in files:
            return os.path.join(base, name)
    return None


def selftest():
    cases = []

    def case(label, got, want):
        cases.append((label, want, got, got == want))

    class Card(object):
        __file__ = os.path.abspath("fake_proof_card.py")

        @staticmethod
        def render(_view):
            return "from the card"

    class OneSource(object):
        """A door that renders through the card."""
        @staticmethod
        def verdict_lines(view):
            return [Card.render(view)]

    class TwoSources(object):
        """A door that builds its own text and never touches the card."""
        @staticmethod
        def verdict_lines(_view):
            return ["built here"]

    class Broken(object):
        @staticmethod
        def verdict_lines(_view):
            raise RuntimeError("cannot render")

    case("a door rendering through the card is PASS",
         judge(*probe(OneSource, Card))[0], 0)
    case("a door with its own renderer is FAIL",
         judge(*probe(TwoSources, Card))[0], 1)
    case("a door that cannot render at all is NO-DATA",
         judge(*probe(Broken, Card))[0], 2)
    case("a card with nothing to replace is NO-DATA",
         judge("same", "same", 0)[0], 2)
    case("a renderer that breaks under substitution is PASS",
         judge("x", "RAISED", 3)[0], 0)
    case("identical text is FAIL however many were replaced",
         judge("x", "x", 23)[0], 1)
    # The FAIL line has to say how many were replaced, or a reader cannot tell a real
    # independence from a probe that substituted nothing.
    line = judge("x", "x", 23)[1]
    cases.append(("the FAIL names how many were replaced", True, "23" in line,
                  "23" in line))

    bad = 0
    for label, want, got, ok in cases:
        bad += 0 if ok else 1
        print("%-52s want %s got %s %s" % (label, want, got, "ok" if ok else "MISMATCH"))
    print("%d case(s), %s" % (len(cases), "OK" if not bad else "%d FAILURE(S)" % bad))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.environ.get("CLAUDE_CONFIG_DIR", ""))
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.config or not os.path.isdir(args.config):
        print("NO-DATA: CLAUDE_CONFIG_DIR %r is not a directory, so no installed plugin "
              "can be read and nothing has been shown about the proof source"
              % args.config)
        return 2
    door_path = _find(args.config, "receipt_door.py")
    card_path = _find(args.config, "proof_card.py")
    if door_path is None or card_path is None:
        print("NO-DATA: the installed plugin carries %s, so the two renderers could not "
              "both be read"
              % ("no receipt_door.py" if door_path is None else "no proof_card.py"))
        return 2
    try:
        card = load(card_path, "installed_proof_card")
        door = load(door_path, "installed_receipt_door")
    except Exception as exc:
        print("NO-DATA: the installed renderers could not be loaded (%s), so nothing "
              "has been shown" % repr(exc)[:90])
        return 2
    code, line = judge(*probe(door, card))
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())

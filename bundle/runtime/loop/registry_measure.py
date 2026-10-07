#!/usr/bin/env python3
"""Is the model registry still the one source, and is it fresh? (FX-31.6, R-FX-31-5, acceptance c14)

usage:
  registry_measure.py --check [--registry PATH] [--today YYYY-MM-DD]   derive every view, report freshness, exit 0
  registry_measure.py --views [--registry PATH]                        print the derived views as JSON
  registry_measure.py --selftest

--check loads the registry through model_router (a malformed registry REFUSES, exit 1: that is the loader's existing
direction), derives every legacy view from the rows (a malformed optional field REFUSES the same way), and then reads
`read_on`: within 90 days prints OK; older, missing, malformed or in the future prints WARN with a class 2 owner question
and STILL EXITS 0. Staleness is the owner's call and never stops the loop; a registry that cannot be read at all is a
different thing and does stop here. The rows themselves are not re-measured by this tool: it reports when they were.
"""
import datetime, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS file's own directory: the copy installed beside it
import model_router  # noqa: E402  (the registry loader, the views and the freshness rule live there)


def check(path=None, today=None, out=None):
    """Exit code for --check: 1 only when the registry or a view cannot be read; a freshness WARN is printed and exits 0."""
    out = out or sys.stdout
    try:
        doc = model_router.load_registry_document(path)
        views = model_router.derive_model_views(doc["models"])
    except model_router.Refused as exc:
        print("REFUSED: %s" % exc, file=out)
        return 1
    print("registry: %d rows; bridge aliases %d, dispatch ids %d, spoken %d, arm keys %d, mix %r, arm cost %r"
          % (len(doc["models"]), len(views["bridge_aliases"]), len(views["dispatch_ids"]), len(views["spoken"]),
             len(views["arm_of"]), views["default_mix"], views["arm_cost"]), file=out)
    status, text = model_router.check_registry_freshness(doc, today)
    print("%s: %s" % (status, text), file=out)
    return 0


def _today_arg(argv):
    """The --today value as a date, or None; a value that is not a date refuses the run rather than reading as today."""
    if "--today" not in argv:
        return None
    raw = argv[argv.index("--today") + 1]
    today = model_router.iso_date(raw)
    if today is None:
        raise model_router.Refused("--today %r is not a date (YYYY-MM-DD)" % raw)
    return today


def selftest():
    """Answer even when a case RAISES (the model_router rule): a readable refusal, never a bare traceback."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    import io, shutil, tempfile
    tmp = tempfile.mkdtemp(prefix="registry-measure-selftest-")
    today = datetime.date(2026, 10, 4)

    def row(**over):
        r = {"id": "x/ok", "transport": "claude", "privacy": "private", "quality": {"build": 5}, "cost": 1.0}
        r.update(over)
        return r

    def doc(read_on, models=None):
        d = {"models": models or {"solo": row()}}
        if read_on is not None:
            d["read_on"] = read_on
        return d

    def fresh(read_on, models=None):
        return model_router.check_registry_freshness(doc(read_on, models), today)

    def on_disk(text, tag):
        path = os.path.join(tmp, tag + ".json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))
        return path

    def run_check(path, today_=today):
        buf = io.StringIO()
        return check(path, today_, out=buf), buf.getvalue()

    days = lambda n: (today - datetime.timedelta(days=n)).isoformat()
    stale_rc, stale_text = run_check(on_disk(doc(days(91)), "stale"))
    fresh_rc, fresh_text = run_check(on_disk(doc(days(1)), "fresh"))
    corrupt_rc, corrupt_text = run_check(on_disk("{not json", "corrupt"))
    listed_rc, listed_text = run_check(on_disk("[1, 2]", "listed"))
    badview_rc, badview_text = run_check(on_disk(doc(days(1), {"solo": row(transport="bridge", privacy="public", aliases="x")}), "badview"))

    cases = [
        ("a registry measured yesterday is OK", fresh(days(1))[0] == "OK"),
        ("exactly 90 days is still OK: the bar is older THAN 90, so 90 itself is fresh", fresh(days(90))[0] == "OK"),
        ("91 days is WARN", fresh(days(91))[0] == "WARN"),
        ("the stale WARN names the age and asks the owner a class 2 question",
         "91 days ago" in fresh(days(91))[1] and "class 2" in fresh(days(91))[1]),
        ("a missing read_on is WARN naming the gap, never read as today", fresh(None) == ("WARN", fresh(None)[1]) and "no read_on" in fresh(None)[1]),
        ("a read_on that is not a date is WARN naming it, never guessed", fresh("yesterday")[0] == "WARN" and "not a date" in fresh("yesterday")[1]),
        ("a bare YYYYMMDD read_on is WARN on every interpreter, as text or as a number: the hyphenated shape is the only date",
         fresh("20261003")[0] == "WARN" and fresh(20261003)[0] == "WARN" and fresh("2026-13-40")[0] == "WARN"),
        ("a read_on in the future is WARN, never read as fresh", fresh(days(-1))[0] == "WARN" and "future" in fresh(days(-1))[1]),
        ("a document that is not a mapping is WARN, not a crash", model_router.check_registry_freshness(["x"], today)[0] == "WARN"),
        ("--check on a stale registry prints WARN and EXITS 0: staleness never blocks", stale_rc == 0 and "WARN:" in stale_text),
        ("--check on a fresh registry prints OK and exits 0", fresh_rc == 0 and "OK:" in fresh_text),
        ("--check prints the row count and every view's size before the verdict", "registry: 1 rows" in fresh_text and "bridge aliases 0" in fresh_text),
        ("--check on a registry that is not JSON REFUSES with exit 1: unreadable is not stale", corrupt_rc == 1 and "REFUSED" in corrupt_text and "not valid JSON" in corrupt_text),
        ("--check on a JSON document that is not a mapping REFUSES by name, never a crash and never a model",
         listed_rc == 1 and "REFUSED" in listed_text and "not a mapping" in listed_text),
        ("--check on a malformed optional field REFUSES with exit 1, naming the row and field",
         badview_rc == 1 and "REFUSED" in badview_text and "'aliases'" in badview_text and "solo" in badview_text),
        ("--today that is not a date is refused, never read as today", _refuses(lambda: _today_arg(["--today", "soon"]))),
        ("--today parses a date", _today_arg(["--today", "2026-10-04"]) == today),
        ("the real registry carries a read_on the freshness rule can read",
         model_router.check_registry_freshness(model_router.load_registry_document(), today)[1].startswith("the model registry was")),
    ]
    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def _refuses(fn):
    try:
        fn()
        return False
    except model_router.Refused:
        return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    path = argv[argv.index("--registry") + 1] if "--registry" in argv else None
    try:
        if "--views" in argv:
            print(json.dumps(model_router.derive_model_views(model_router.load_registry(path)), indent=2, sort_keys=True))
            return 0
        if "--check" in argv:
            return check(path, _today_arg(argv))
    except model_router.Refused as exc:
        print("REFUSED: %s" % exc)
        return 1
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())

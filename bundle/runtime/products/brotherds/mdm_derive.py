"""Derive MDM evaluation-gate inputs from evidence CSVs.

Commands
--------
clusters <csv>
    Read per-record cluster assignments from a CSV whose header includes
    record_id, predicted_cluster, and true_cluster. Build the predicted
    and true partitions, then derive pairwise and B-cubed metrics. The
    gold_clusters payload it returns is ready to paste into a claim's
    master_data.evaluation.gold_clusters, which is read by the
    M10.cluster_metrics gate.

drift <ref.csv> <cur.csv> [--bins N]
    Read a score column (values in [0, 1]) from two CSVs, histogram both
    into N equal-width bins on [0, 1] with the last bin including 1.0,
    and derive the reference and current proportions, the population
    stability index, and its band. The score_drift payload it returns is
    ready to paste into master_data.evaluation.score_drift, which is
    read by the M13.score_drift gate.

--selftest
    Run the built-in checks on temporary CSVs.

Every number is derived from evidence, never typed. The sibling
statistics module mdm_eval is reused for pairwise_metrics, bcubed, psi,
and psi_band; none of those are re-implemented here.
"""

import contextlib
import csv
import io
import json
import math
import os
import sys
import tempfile

try:
    import mdm_eval
except ImportError:  # pragma: no cover - package-import fallback
    from products.brotherds import mdm_eval


def _text(value):
    """Return a stripped string for a CSV cell, empty when absent."""
    return value.strip() if isinstance(value, str) else ""


def clusters_from_csv(path):
    """Derive partitions and metrics from a clusters evidence CSV."""
    required = ("record_id", "predicted_cluster", "true_cluster")
    pred_map = {}
    true_map = {}
    seen = {}
    n_records = 0
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError("missing required column(s): " + ", ".join(missing))
        for row in reader:
            line_no = reader.line_num
            rid = _text(row.get("record_id"))
            if rid == "":
                raise ValueError(f"empty record_id on line {line_no}")
            if rid in seen:
                raise ValueError(
                    f"duplicate record_id {rid!r} on line {line_no} "
                    f"(first seen on line {seen[rid]})"
                )
            seen[rid] = line_no
            pkey = _text(row.get("predicted_cluster"))
            tkey = _text(row.get("true_cluster"))
            pred_map.setdefault(pkey, []).append(rid)
            true_map.setdefault(tkey, []).append(rid)
            n_records += 1
    pred_clusters = sorted([sorted(group) for group in pred_map.values()])
    true_clusters = sorted([sorted(group) for group in true_map.values()])
    return {
        "n_records": n_records,
        "gold_clusters": {"pred": pred_clusters, "true": true_clusters},
        "pairwise": mdm_eval.pairwise_metrics(pred_clusters, true_clusters),
        "bcubed": mdm_eval.bcubed(pred_clusters, true_clusters),
    }


def _read_scores(path):
    """Read the score column of a CSV, returning a list of floats in [0, 1]."""
    scores = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        if "score" not in fields:
            raise ValueError(f"{path} must have a score column")
        for row in reader:
            line_no = reader.line_num
            raw = _text(row.get("score"))
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{path} invalid score on line {line_no}")
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise ValueError(f"{path} score out of range on line {line_no}")
            scores.append(value)
    return scores


def _histogram(values, bins):
    """Count values into `bins` equal-width bins on [0, 1], last bin includes 1.0."""
    counts = [0] * bins
    for value in values:
        idx = int(value * bins)
        if idx >= bins:
            idx = bins - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1
    return counts


def drift_from_csv(ref_path, cur_path, bins=10):
    """Derive score_drift proportions, PSI, and band from two score CSVs."""
    if not isinstance(bins, int) or isinstance(bins, bool) or bins < 2:
        raise ValueError("bins must be an integer >= 2")
    ref_scores = _read_scores(ref_path)
    cur_scores = _read_scores(cur_path)
    if not ref_scores:
        raise ValueError(f"{ref_path} has no rows")
    if not cur_scores:
        raise ValueError(f"{cur_path} has no rows")
    ref_counts = _histogram(ref_scores, bins)
    cur_counts = _histogram(cur_scores, bins)
    n_ref = len(ref_scores)
    n_cur = len(cur_scores)
    ref_props = [count / n_ref for count in ref_counts]
    cur_props = [count / n_cur for count in cur_counts]
    value = mdm_eval.psi(ref_props, cur_props)
    return {
        "bins": bins,
        "n_reference": n_ref,
        "n_current": n_cur,
        "score_drift": {
            "reference": [round(p, 6) for p in ref_props],
            "current": [round(p, 6) for p in cur_props],
            "acknowledged": False,
        },
        "psi": value,
        "band": mdm_eval.psi_band(value),
    }


_USAGE = (
    "usage: mdm_derive.py clusters <csv> | "
    "drift <ref.csv> <cur.csv> [--bins N] | --selftest"
)


def _run(argv):
    if len(argv) >= 2 and argv[1] == "clusters":
        if len(argv) != 3:
            raise ValueError("usage: mdm_derive.py clusters <csv>")
        result = clusters_from_csv(argv[2])
    elif len(argv) >= 2 and argv[1] == "drift":
        if len(argv) < 4:
            raise ValueError(
                "usage: mdm_derive.py drift <ref.csv> <cur.csv> [--bins N]"
            )
        bins = 10
        i = 4
        while i < len(argv):
            arg = argv[i]
            if arg == "--bins":
                if i + 1 >= len(argv):
                    raise ValueError("missing value for --bins")
                bins = int(argv[i + 1])
                i += 2
            elif arg.startswith("--bins="):
                bins = int(arg.split("=", 1)[1])
                i += 1
            else:
                raise ValueError(f"unknown argument: {arg}")
        result = drift_from_csv(argv[2], argv[3], bins)
    else:
        raise ValueError(_USAGE)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv):
    """CLI entry point: prints derived JSON on stdout, returns an exit code."""
    if len(argv) == 2 and argv[1] == "--selftest":
        try:
            failures = _selftest()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"SELFTEST FAIL: unexpected error: {exc}")
            return 1
        if failures:
            for msg in failures:
                print(f"SELFTEST FAIL: {msg}")
            return 1
        print("SELFTEST PASS")
        return 0
    try:
        return _run(argv)
    except Exception as exc:
        print(f"NO-DATA: {exc}")
        return 2


def _selftest():
    """Run the built-in checks on temporary CSVs, returning failure messages."""
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    def check_close(got, want, msg, places=4):
        if got is None:
            failures.append(f"{msg}: got None, expected {want}")
            return
        if abs(got - want) > (10 ** (-places)) / 2.0:
            failures.append(f"{msg}: got {got}, expected {want}")

    def write_temp(text):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", newline="") as handle:
            handle.write(text)
        return path

    # --- clusters_from_csv: happy path ---
    path = write_temp(
        "record_id,predicted_cluster,true_cluster,note\n"
        "a,p1,t1,x\n"
        "b,p1,t1,y\n"
        "c,p2,t2,z\n"
        "d,p2,t1,w\n"
    )
    try:
        res = clusters_from_csv(path)
        check(res["n_records"] == 4, f"clusters n_records got {res['n_records']}")
        check(
            res["gold_clusters"]["pred"] == [["a", "b"], ["c", "d"]],
            f"clusters pred partition got {res['gold_clusters']['pred']}",
        )
        check(
            res["gold_clusters"]["true"] == [["a", "b", "d"], ["c"]],
            f"clusters true partition got {res['gold_clusters']['true']}",
        )
        check(
            res["pairwise"]["tp"] == 1,
            f"clusters pairwise tp got {res['pairwise']['tp']}",
        )
        check(
            res["pairwise"]["fp"] == 1,
            f"clusters pairwise fp got {res['pairwise']['fp']}",
        )
        check(
            res["pairwise"]["fn"] == 2,
            f"clusters pairwise fn got {res['pairwise']['fn']}",
        )
        check_close(res["bcubed"]["precision"], 0.75, "clusters bcubed precision")
        check_close(res["bcubed"]["recall"], 2.0 / 3.0, "clusters bcubed recall")
    finally:
        os.unlink(path)

    # --- clusters_from_csv: duplicate record_id ---
    path = write_temp(
        "record_id,predicted_cluster,true_cluster\n"
        "a,p1,t1\n"
        "b,p1,t1\n"
        "a,p2,t2\n"
    )
    try:
        raised = False
        try:
            clusters_from_csv(path)
        except ValueError as exc:
            raised = True
            check("line 4" in str(exc), f"duplicate line number: {exc}")
            check("'a'" in str(exc), f"duplicate names id: {exc}")
        check(raised, "duplicate record_id should raise")
    finally:
        os.unlink(path)

    # --- clusters_from_csv: empty record_id ---
    path = write_temp(
        "record_id,predicted_cluster,true_cluster\n"
        "a,p1,t1\n"
        ",p2,t2\n"
    )
    try:
        raised = False
        try:
            clusters_from_csv(path)
        except ValueError as exc:
            raised = True
            check("line 3" in str(exc), f"empty id line number: {exc}")
        check(raised, "empty record_id should raise")
    finally:
        os.unlink(path)

    # --- clusters_from_csv: missing required column ---
    path = write_temp("record_id,predicted_cluster\na,p1\n")
    try:
        raised = False
        try:
            clusters_from_csv(path)
        except ValueError:
            raised = True
        check(raised, "missing column should raise")
    finally:
        os.unlink(path)

    # --- drift_from_csv: happy path ---
    body = "score\n" + "".join(f"{i / 10.0}\n" for i in range(10))
    ref = write_temp(body)
    cur = write_temp(body)
    try:
        res = drift_from_csv(ref, cur, bins=10)
        check(res["bins"] == 10, f"drift bins got {res['bins']}")
        check(res["n_reference"] == 10, f"drift n_reference got {res['n_reference']}")
        check(res["n_current"] == 10, f"drift n_current got {res['n_current']}")
        check(len(res["score_drift"]["reference"]) == 10, "drift reference length")
        check(len(res["score_drift"]["current"]) == 10, "drift current length")
        check(
            res["score_drift"]["acknowledged"] is False,
            "drift acknowledged must default False",
        )
        check_close(res["psi"], 0.0, "drift psi zero for identical inputs")
        check(res["band"] == "stable", f"drift band got {res['band']}")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- drift_from_csv: last bin includes 1.0 ---
    ref = write_temp("score\n1.0\n")
    cur = write_temp("score\n1.0\n")
    try:
        res = drift_from_csv(ref, cur, bins=10)
        rp = res["score_drift"]["reference"]
        check(rp[9] == 1.0, f"1.0 must land in last bin, got {rp}")
        check(all(p == 0.0 for p in rp[:9]), f"1.0 must not land elsewhere, got {rp}")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- drift_from_csv: bad score values ---
    for bad in ("abc", "nan", "inf", "1.5", "-0.5"):
        ref = write_temp("score\n" + bad + "\n")
        cur = write_temp("score\n0.5\n")
        try:
            raised = False
            try:
                drift_from_csv(ref, cur, 10)
            except ValueError as exc:
                raised = True
                check("line 2" in str(exc), f"bad score {bad!r} line number: {exc}")
                check(ref in str(exc), f"bad score {bad!r} names file: {exc}")
            check(raised, f"bad score {bad!r} should raise")
        finally:
            os.unlink(ref)
            os.unlink(cur)

    # --- drift_from_csv: bins < 2 ---
    ref = write_temp("score\n0.5\n")
    cur = write_temp("score\n0.5\n")
    try:
        raised = False
        try:
            drift_from_csv(ref, cur, 1)
        except ValueError:
            raised = True
        check(raised, "bins < 2 should raise")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- drift_from_csv: no rows in reference ---
    ref = write_temp("score\n")
    cur = write_temp("score\n0.5\n")
    try:
        raised = False
        try:
            drift_from_csv(ref, cur, 10)
        except ValueError:
            raised = True
        check(raised, "empty reference should raise")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- drift_from_csv: no rows in current ---
    ref = write_temp("score\n0.5\n")
    cur = write_temp("score\n")
    try:
        raised = False
        try:
            drift_from_csv(ref, cur, 10)
        except ValueError:
            raised = True
        check(raised, "empty current should raise")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- drift_from_csv: missing score column ---
    ref = write_temp("value\n0.5\n")
    cur = write_temp("score\n0.5\n")
    try:
        raised = False
        try:
            drift_from_csv(ref, cur, 10)
        except ValueError:
            raised = True
        check(raised, "missing score column should raise")
    finally:
        os.unlink(ref)
        os.unlink(cur)

    # --- main: bad input yields NO-DATA and exit code 2 ---
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["mdm_derive.py"])
    check(rc == 2, f"main no args rc got {rc}")
    check(
        buf.getvalue().startswith("NO-DATA:"),
        f"main no args output: {buf.getvalue()!r}",
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["mdm_derive.py", "bogus"])
    check(rc == 2, f"main bogus rc got {rc}")
    check(
        buf.getvalue().startswith("NO-DATA:"),
        f"main bogus output: {buf.getvalue()!r}",
    )

    return failures


if __name__ == "__main__":
    sys.exit(main(sys.argv))

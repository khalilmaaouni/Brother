#!/usr/bin/env python3
"""WBS-40.02 Source Snapshot: structural-only .xlsx snapshot record.

Generic tool, no source-specific logic: pass any .xlsx path on the command
line. Reads the file's structure with the standard library only (zipfile +
xml.etree.ElementTree, since .xlsx is a zip of XML parts) -- never opens a
cell value, only sheet names, row counts and byte-level facts. Per
docs/decisions/evidence-vocabulary-2026-09-13.json's EV-3 ruling, the
PASS/FAIL/NO-DATA triple is imported from scripts/evidence_obligation.py,
not redeclared here.
"""
import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET  # ponytail: stdlib parser, no XXE hardening; local/trusted files only per the brief (no new dependency, defusedxml not installed)
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_obligation import VERDICTS, exit_code_for_verdict  # noqa: E402

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def sha256_of_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_worksheet_targets(zf):
    """Return [(sheet_name, internal_zip_path), ...] in workbook order."""
    workbook_xml = zf.read("xl/workbook.xml")
    workbook_root = ET.fromstring(workbook_xml)

    rels_by_id = {}
    try:
        rels_xml = zf.read("xl/_rels/workbook.xml.rels")
        rels_root = ET.fromstring(rels_xml)
        for rel in rels_root:
            if _local(rel.tag) != "Relationship":
                continue
            rels_by_id[rel.get("Id")] = rel.get("Target")
    except KeyError:
        pass

    sheets = []
    for sheets_el in workbook_root:
        if _local(sheets_el.tag) != "sheets":
            continue
        for sheet_el in sheets_el:
            if _local(sheet_el.tag) != "sheet":
                continue
            name = sheet_el.get("name")
            rid = sheet_el.get("{%s}id" % REL_NS)
            target = rels_by_id.get(rid)
            if target:
                target = "xl/" + target if not target.startswith("xl/") else target
                target = os.path.normpath(target).replace(os.sep, "/")
            sheets.append((name, target))
    return sheets


def count_rows(zf, worksheet_path):
    if not worksheet_path:
        return None
    try:
        sheet_xml = zf.read(worksheet_path)
    except KeyError:
        return None
    count = 0
    for _event, elem in ET.iterparse(__import__("io").BytesIO(sheet_xml)):
        if _local(elem.tag) == "row":
            count += 1
        elem.clear()
    return count


def shared_strings_counts(zf):
    """Structural fact only: the <sst> count/uniqueCount attributes, never the strings."""
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return None
    root = ET.fromstring(raw)
    return {
        "count": root.get("count"),
        "unique_count": root.get("uniqueCount"),
    }


def build_snapshot(source_path, source_system_id=None, notes=None, now=None):
    """Compute the structural-only snapshot record for one .xlsx file.

    Never reads cell values. Returns a dict always carrying a 'verdict' key
    drawn from evidence_obligation.VERDICTS.
    """
    notes = list(notes or [])
    snapshot_time = (now or datetime.now(timezone.utc)).isoformat()

    record = {
        "subject": "master-source-snapshot",
        "source_system_identifier": source_system_id or "NO-DATA",
        "schema_version": "NO-DATA",
        "row_counts": {},
        "snapshot_time": snapshot_time,
        "fingerprint": "NO-DATA",
        "quality_observations": notes,
        "lineage_reference": source_path,
        "access_boundary": "private, vault-only, never public",
        "data_retention_restriction": "raw data stays in the approved environment; only this structural snapshot record may persist",
        "file_size_bytes": None,
        "verdict": "NO-DATA",
        "verdict_reason": "source file does not exist",
    }

    if not os.path.exists(source_path):
        return record

    record["file_size_bytes"] = os.path.getsize(source_path)

    try:
        record["fingerprint"] = sha256_of_file(source_path)
        with zipfile.ZipFile(source_path) as zf:
            sheets = resolve_worksheet_targets(zf)
            if not sheets:
                raise ValueError("no worksheets found in xl/workbook.xml")
            row_counts = {}
            for name, target in sheets:
                row_counts[name] = count_rows(zf, target)
            record["row_counts"] = row_counts
            record["schema_version"] = "sheets:%d" % len(sheets)
            sst = shared_strings_counts(zf)
            if sst is not None:
                record["shared_strings"] = sst
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        record["verdict"] = "FAIL"
        record["verdict_reason"] = "%s: %s" % (type(exc).__name__, exc)
        return record

    record["verdict"] = "PASS"
    record["verdict_reason"] = "read and hashed successfully"
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="path to the .xlsx source file")
    parser.add_argument("--source-system-id", default=None)
    parser.add_argument("--out", default=None, help="write JSON record to this path instead of stdout")
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="a quality observation to record verbatim (repeatable); never invented by this tool",
    )
    args = parser.parse_args(argv)

    record = build_snapshot(args.source, args.source_system_id, args.note)
    if record["verdict"] not in VERDICTS:
        raise AssertionError("source snapshot verdict %r outside the shared triple" % record["verdict"])

    text = json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)

    return exit_code_for_verdict(record["verdict"])


if __name__ == "__main__":
    sys.exit(main())

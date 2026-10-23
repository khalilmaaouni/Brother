"""Generate and validate release body tables from WBS and HOSTS sources."""
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _check_path(path: Any) -> str:
    if not isinstance(path, str) or path == "":
        raise ValueError("path must be a non-empty string")
    if os.path.isdir(path):
        raise ValueError("path is a directory: " + path)
    return path


def read_wbs(path: str) -> Dict[str, Any]:
    path = _check_path(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ValueError("cannot read WBS file {}: {}".format(path, exc))
    except ValueError as exc:
        raise ValueError("cannot parse WBS JSON {}: {}".format(path, exc))
    if not isinstance(data, dict):
        raise ValueError("WBS top level is not a dict: " + path)
    if "units" not in data:
        raise ValueError("WBS missing units key: " + path)
    units = data["units"]
    if not isinstance(units, list):
        raise ValueError("WBS units is not a list: " + path)
    if not units:
        raise ValueError("WBS units is empty: " + path)
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError("WBS unit {} is not a dict: {}".format(index, path))
        if "state" not in unit:
            raise ValueError("WBS unit {} missing state: {}".format(index, path))
        state = unit["state"]
        if not isinstance(state, str) or state == "":
            raise ValueError("WBS unit {} state is not a non-empty string: {}".format(index, path))
    return data


def read_hosts(path: str) -> Dict[str, str]:
    path = _check_path(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise ValueError("cannot read HOSTS file {}: {}".format(path, exc))
    except UnicodeDecodeError as exc:
        raise ValueError("HOSTS file is not UTF-8 {}: {}".format(path, exc))
    hosts: Dict[str, str] = {}
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line == "":
            continue
        if line.startswith("#"):
            continue
        if not line.startswith("|") or not line.endswith("|"):
            raise ValueError("HOSTS line {} is not a table row: {}".format(number, raw.rstrip("\n")))
        inner = line[1:-1]
        cells = [cell.strip() for cell in inner.split("|")]
        if len(cells) < 2:
            raise ValueError("HOSTS line {} has fewer than two cells: {}".format(number, raw.rstrip("\n")))
        first = cells[0].lower()
        if first in ("host", "hosts"):
            continue
        if all(cell != "" and all(ch in "-:" for ch in cell) for cell in cells):
            continue
        host = cells[0]
        state = cells[1]
        if host == "":
            raise ValueError("HOSTS line {} has empty host id".format(number))
        if state == "":
            raise ValueError("HOSTS line {} has empty state".format(number))
        if host in hosts:
            raise ValueError("HOSTS line {} duplicates host {}".format(number, host))
        hosts[host] = state
    if not hosts:
        raise ValueError("HOSTS file has no host rows")
    return hosts


def count_wbs_states(wbs: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(wbs, dict):
        raise ValueError("wbs must be a dict")
    if "units" not in wbs:
        raise ValueError("wbs missing units key")
    units = wbs["units"]
    if not isinstance(units, list) or not units:
        raise ValueError("wbs units must be a non-empty list")
    counts: Dict[str, int] = {}
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError("wbs unit {} is not a dict".format(index))
        if "state" not in unit:
            raise ValueError("wbs unit {} missing state".format(index))
        state = unit["state"]
        if not isinstance(state, str) or state == "":
            raise ValueError("wbs unit {} state is not a non-empty string".format(index))
        counts[state] = counts.get(state, 0) + 1
    return counts


def count_host_states(hosts: Dict[str, str]) -> Dict[str, int]:
    if not isinstance(hosts, dict):
        raise ValueError("hosts must be a dict")
    counts: Dict[str, int] = {}
    for host, value in hosts.items():
        if not isinstance(value, str) or value == "":
            raise ValueError("host {} state is not a non-empty string".format(host))
        counts[value] = counts.get(value, 0) + 1
    return counts


def render_wbs_table(wbs: Dict[str, Any]) -> str:
    counts = count_wbs_states(wbs)
    lines = ["| state | count |", "| --- | --- |"]
    for state in sorted(counts):
        lines.append("| {} | {} |".format(state, counts[state]))
    return "\n".join(lines) + "\n"


def render_hosts_table(hosts: Dict[str, str]) -> str:
    counts = count_host_states(hosts)
    lines = ["| state | count |", "| --- | --- |"]
    for state in sorted(counts):
        lines.append("| {} | {} |".format(state, counts[state]))
    return "\n".join(lines) + "\n"


def compose_release_body(prose: str, wbs_path: str, hosts_path: str) -> str:
    if not isinstance(prose, str):
        raise ValueError("prose must be a string")
    _check_path(wbs_path)
    _check_path(hosts_path)
    if "{{WBS_TABLE}}" not in prose or "{{HOSTS_TABLE}}" not in prose:
        raise ValueError("prose must contain {{WBS_TABLE}} and {{HOSTS_TABLE}}")
    if any(ch.isdigit() for ch in prose):
        raise ValueError("prose must not contain a figure; use a placeholder")
    wbs = read_wbs(wbs_path)
    hosts = read_hosts(hosts_path)
    wbs_table = render_wbs_table(wbs)
    hosts_table = render_hosts_table(hosts)
    body = prose.replace("{{WBS_TABLE}}", wbs_table).replace("{{HOSTS_TABLE}}", hosts_table)
    return body


def validate_release_body(body: str, wbs_path: str, hosts_path: str) -> None:
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    _check_path(wbs_path)
    _check_path(hosts_path)
    wbs = read_wbs(wbs_path)
    hosts = read_hosts(hosts_path)
    wbs_table = render_wbs_table(wbs)
    hosts_table = render_hosts_table(hosts)
    if wbs_table not in body or hosts_table not in body:
        raise ValueError("body is missing a generated table")
    remainder = body.replace(wbs_table, "", 1).replace(hosts_table, "", 1)
    if any(ch.isdigit() for ch in remainder):
        raise ValueError("body contains a figure outside the generated tables")
    expected_lines = set(wbs_table.splitlines()) | set(hosts_table.splitlines())
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if stripped not in expected_lines:
                raise ValueError("body contains a table row with no source")
    return None

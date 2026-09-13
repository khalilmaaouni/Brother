#!/usr/bin/env python3
"""Check that public host documentation matches shipped host manifests."""
import argparse
import json
import pathlib


HOST_MANIFESTS = {
    "Claude Code": (".claude-plugin", "marketplace.json"),
    "Codex": ("bundle", ".codex-plugin", "plugin.json"),
    "Cursor": ("bundle", ".cursor-plugin", "plugin.json"),
}
DOCS = ("README.md", "docs/reference/install-matrix.md")


def shipped_hosts(root):
    root = pathlib.Path(root)
    hosts, errors = [], []
    for host, parts in HOST_MANIFESTS.items():
        path = root.joinpath(*parts)
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            errors.append(f"{host}: {path}: {exc}")
            continue
        if not isinstance(data, dict) or not data.get("name"):
            errors.append(f"{host}: {path}: manifest is not a named object")
            continue
        hosts.append(host)
    return tuple(hosts), tuple(errors)


def documented_text(root):
    root = pathlib.Path(root)
    chunks, errors = [], []
    for relative in DOCS:
        try:
            chunks.append((root / relative).read_text(encoding="utf-8"))
        except OSError as exc:
            errors.append(f"{relative}: {exc}")
    return "\n".join(chunks), tuple(errors)


def documented_matrix_hosts(root):
    """Read host labels from the install matrix, without another registry."""
    path = pathlib.Path(root) / DOCS[1]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    hosts = []
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0] and cells[0] not in ("Host", "---"):
            hosts.append(cells[0])
    return tuple(hosts)


def check(root):
    hosts, manifest_errors = shipped_hosts(root)
    docs, doc_errors = documented_text(root)
    problems = list(manifest_errors) + list(doc_errors)
    for host in hosts:
        if host not in docs:
            problems.append(f"shipped host missing from public docs: {host}")
    for host in HOST_MANIFESTS:
        if host in docs and host not in hosts:
            problems.append(f"public docs name host without shipped surface: {host}")
    for host in documented_matrix_hosts(root):
        if host not in hosts:
            problems.append(f"public docs name host without shipped surface: {host}")
    return hosts, tuple(problems)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args(argv)
    hosts, problems = check(args.root)
    if problems:
        for problem in problems:
            print("FAIL: " + problem)
        return 1
    print("PASS: public host truth: " + ", ".join(hosts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

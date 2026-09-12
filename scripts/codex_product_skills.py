#!/usr/bin/env python3
"""Build opt-in Codex product packages outside a verified public export.

The caller supplies the pinned official marketplace checkout, or explicitly
names an already allowlisted export. Never copy a private hub checkout.
The umbrella remains the only hook owner. Product adapters carry their full
shipped support roots and preserve skill names and instruction bodies.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import codex_skills

PRODUCTS = ("brothermode", "brothersbe")
MARKETPLACE = "brother-product-skills"
RECORD = "ADAPTER-MANIFEST.json"


def _json(data):
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _files(root):
    if root.is_symlink():
        raise ValueError("export product root is a symlink")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("export contains a symlink; refusing a dependency outside its package")
        rel = path.relative_to(root)
        if any(part in (".git", "__pycache__") for part in rel.parts):
            continue
        if path.is_file() and path.suffix != ".pyc":
            result[rel.as_posix()] = path.read_bytes()
    return result


def _adapt(text, skill):
    lines, body = codex_skills.split_frontmatter(text)
    if lines is None:
        raise ValueError("skill has no closed frontmatter: " + skill)
    keys = [codex_skills._KEY_LINE.match(line) for line in lines]
    accepted = tuple(m.group(1) for m in keys if m and m.group(1) not in
                     ("disable-model-invocation", "disable_model_invocation"))
    kept, removed = codex_skills.strip_frontmatter(lines, accepted)
    restricted = any(re.search(r":\s*true\s*(?:#.*)?$", value, re.M)
                     for value in removed.values())
    for value in removed.values():
        if not re.search(r":\s*(?:true|false)\s*(?:#.*)?$", value, re.M):
            raise ValueError("unsupported invocation flag in skill: " + skill)
    return codex_skills.render(kept, body).encode(), restricted


def build(export_root, output_root):
    """Return a content-addressed, independently verifiable marketplace.

    Existing builds are verified before reuse. Source bytes are never written.
    Output must live outside the export, so generated packages cannot be picked
    up by a later source walk or overwrite the original Claude instructions.
    """
    source, output = Path(export_root).resolve(), Path(output_root).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("package output must be outside the source export")
    if (source / "docs/plan/EXPORT-ALLOWLIST.txt").exists():
        raise ValueError("source is a private checkout; build the allowlisted export first")
    catalog = json.loads((source / ".agents/plugins/marketplace.json").read_text())
    if catalog.get("name") != "brother":
        raise ValueError("source export is not the Brother marketplace")
    bundle = json.loads((source / "bundle/.codex-plugin/plugin.json").read_text())
    files, inventory, source_hashes = {}, {}, {}
    for product in PRODUCTS:
        original = _files(source / "products" / product)
        if not original or "SKILL.md" not in original:
            raise ValueError("export lacks complete product support root: " + product)
        source_hashes[product] = {p: _digest(b) for p, b in original.items()}
        product_hash = _digest(_json(source_hashes[product]))
        skill_names = sorted(p.split("/")[1] for p in original
                             if re.fullmatch(r"skills/[^/]+/SKILL\.md", p))
        if not skill_names:
            raise ValueError("export lacks product skills: " + product)
        inventory[product] = skill_names
        adapted = dict(original)
        # Codex auto-discovers this file even without a manifest hooks field.
        # Its commands are already registered by the umbrella plugin.
        adapted.pop("hooks/hooks.json", None)
        for skill in skill_names:
            rel = "skills/%s/SKILL.md" % skill
            adapted[rel], restricted = _adapt(original[rel].decode(), skill)
            if restricted:
                agent = "skills/%s/agents/openai.yaml" % skill
                if agent in original:
                    raise ValueError("existing agent metadata needs an explicit policy merge: " + skill)
                adapted[agent] = ("interface:\n  display_name: %s\n"
                    "  short_description: %s\npolicy:\n  allow_implicit_invocation: false\n" %
                    (json.dumps(skill), json.dumps("Run the existing %s skill." % skill))).encode()
        manifest = json.loads(original.get(".codex-plugin/plugin.json", _json(bundle)))
        claude = json.loads(original[".claude-plugin/plugin.json"])
        for key in ("name", "version", "description", "author", "homepage", "repository", "license", "keywords"):
            if key in claude:
                manifest[key] = claude[key]
        manifest["name"] = product
        manifest["version"] = str(manifest["version"]).split("+", 1)[0] + "+codex." + product_hash[:16]
        manifest["skills"] = "./skills/"
        interface = manifest.setdefault("interface", {})
        interface["displayName"] = "BrotherMode" if product == "brothermode" else "BrotherSBE"
        interface["shortDescription"] = manifest["description"]
        interface["longDescription"] = manifest["description"]
        adapted[".codex-plugin/plugin.json"] = _json(manifest)
        for rel, data in adapted.items():
            files["plugins/%s/%s" % (product, rel)] = data
    files[".agents/plugins/marketplace.json"] = _json({"name": MARKETPLACE,
        "interface": {"displayName": "Brother product skills"}, "plugins": [
        {"name": name, "source": {"source": "local", "path": "./plugins/" + name},
         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
         "category": "Productivity"} for name in PRODUCTS]})
    record = {"skills": inventory, "source_files": source_hashes,
              "files": {p: _digest(b) for p, b in files.items()}}
    identity = _digest(_json(record))
    target = output / identity / MARKETPLACE
    if target.exists():
        problems = verify(target)
        if problems:
            raise ValueError("existing generated package differs from its manifest")
    else:
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".product-skills-", dir=str(output)) as tmp:
            pending = Path(tmp) / MARKETPLACE
            for rel, data in files.items():
                dest = pending / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                source_rel = rel.split("/", 2)
                if len(source_rel) == 3 and source_rel[0] == "plugins":
                    source_file = source / "products" / source_rel[1] / source_rel[2]
                    if source_file.is_file():
                        shutil.copymode(source_file, dest)
            # Adapted skill bytes and the missing duplicate hook registry must
            # also be reflected in each product's own install receipt.
            for product in PRODUCTS:
                package = pending / "plugins" / product
                generator = package / "scripts/checksums.sh"
                if (package / "CHECKSUMS.sha256").is_file():
                    if not generator.is_file():
                        raise ValueError("export has a checksum manifest without its generator: " + product)
                    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(pending))
                    try:
                        result = subprocess.run(["sh", str(generator), "CHECKSUMS.sha256"],
                            cwd=str(package), env=env, capture_output=True, text=True, timeout=120)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        raise ValueError("product checksum generation could not run: " + product) from exc
                    if result.returncode != 0:
                        raise ValueError("product checksum generation failed: " + product)
            record["files"] = {p: _digest(data) for p, data in _files(pending).items()}
            (pending / RECORD).write_bytes(_json(record))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, target)
    return {"marketplace_root": str(target), "skills": inventory, "digest": identity}


def verify(marketplace_root):
    """An empty problem list means every recorded generated byte is present."""
    root = Path(marketplace_root)
    try:
        record = json.loads((root / RECORD).read_text())
        actual = _files(root)
        actual.pop(RECORD, None)
        got = {p: _digest(data) for p, data in actual.items()}
        return [] if got == record["files"] else ["generated package content mismatch"]
    except (OSError, ValueError, KeyError) as exc:
        return ["generated package could not be verified: " + str(exc)]

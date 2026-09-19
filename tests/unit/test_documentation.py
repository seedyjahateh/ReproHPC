"""Offline checks for citation syntax, immutable vendor schema, and guide links."""

import json
import re
from pathlib import Path

import jsonschema
import yaml

from reprohpc.io import sha256

ROOT = Path(__file__).resolve().parents[2]


def test_citation_against_official_versioned_schema():
    vendor = ROOT / "tests/vendor/cff-1.2.0"
    for entry in json.loads((vendor / "upstream.json").read_text()):
        assert sha256(vendor / entry["name"]) == entry["sha256"]
    schema = json.loads((vendor / "schema.json").read_text())
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text())
    jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(citation)


def test_local_documentation_links_resolve():
    documents = list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md"))
    missing = []
    for path in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.split("#", 1)[0].strip("<>")
            if target and "://" not in target and not (path.parent / target).exists():
                missing.append(f"{path.name}: {target}")
    assert not missing, missing

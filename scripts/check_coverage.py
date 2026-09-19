"""Enforce both PRD coverage thresholds; never treat a skipped integration as evidence."""

import xml.etree.ElementTree as ET

root = ET.parse("coverage.xml").getroot()
line = float(root.attrib["line-rate"])
branch = float(root.attrib["branch-rate"])
print(f"Statement coverage: {line:.1%}; branch coverage: {branch:.1%}")
if line < 0.85 or branch < 0.75:
    raise SystemExit("PRD M-12 not met: need >=85% statements and >=75% branches")

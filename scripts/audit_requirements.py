"""Check traceability completeness and published schema consistency."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    prd = (ROOT / "PRD.md").read_text(encoding="utf-8")
    tasks = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    required = set(re.findall(r"^\| ((?:G|US|FR|NFR|RE|T|M)-\d+) \|", prd, re.M))
    missing = [key for key in required if not re.search(rf"^- \[[ x]\] \*\*{key}\b", tasks, re.M)]
    if missing:
        raise SystemExit(f"Missing requirement checklist entries: {missing}")
    for path in (ROOT / "schemas").glob("*.json"):
        if json.loads(path.read_text()) != json.loads(
            (ROOT / "src/reprohpc/schemas" / path.name).read_text()
        ):
            raise SystemExit(f"Schema copies differ: {path.name}")
    complete = re.findall(r"^- \[x\] \*\*((?:G|US|FR|NFR|RE|T|M)-\d+)", tasks, re.M)
    print(
        f"Traceability: {len(required)} numbered requirements; {len(complete)} recorded as verified"
    )


if __name__ == "__main__":
    main()

"""Copy selected local acceptance facts, excluding work caches and unreviewed logs."""

import argparse
import shutil
from pathlib import Path

NAMES = {
    "evidence.json",
    "arrays-evidence.json",
    "accounting.tsv",
    "trace.tsv",
    "sacct.tsv",
    "status.json",
    "tasks.jsonl",
    "engine.json",
}
LOG_NAMES = {
    "driver.log",
    "console.log",
    "launcher.log",
    "job.log",
    "command.sh",
    "command.out",
    "command.err",
    "command.log",
    "exitcode",
    ".command.sh",
    ".command.out",
    ".command.err",
    ".command.log",
    ".exitcode",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-logs",
        action="store_true",
        help="Include diagnostics from public synthetic CI runs",
    )
    args = parser.parse_args()
    source, destination = args.root.resolve(), args.output.resolve()
    if destination.is_relative_to(source):
        raise SystemExit("Evidence destination must be outside the source tree")
    count = 0
    names = NAMES | LOG_NAMES if args.include_logs else NAMES
    for path in source.rglob("*"):
        if path.is_file() and path.name in names and not path.is_symlink():
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1
    print(f"Copied {count} raw evidence files from {source}")


if __name__ == "__main__":
    main()

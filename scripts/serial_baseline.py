"""Time the same baked scientific routine in one allocated container."""

import json
import subprocess
import sys
import time
from pathlib import Path

sif, spec, data, destination = sys.argv[1:]
output = Path(destination)
output.mkdir(parents=True, exist_ok=False)
program = """
import json, sys
from pathlib import Path
from reprohpc.task import analyze_batch
spec=json.loads(Path(sys.argv[1]).read_text())
analyze_batch(spec, [Path(sys.argv[2])/s['path'] for s in spec['samples']], Path(sys.argv[3]))
"""
started = time.monotonic()
subprocess.run(
    [
        "apptainer",
        "exec",
        "--cleanenv",
        "--bind",
        f"{Path(spec).parent},{data}",
        sif,
        "python",
        "-c",
        program,
        spec,
        data,
        destination,
    ],
    check=True,
)
(output / "timing.json").write_text(
    json.dumps({"elapsed_seconds": time.monotonic() - started}) + "\n"
)

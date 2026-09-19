"""Delegate controllers inside the private, disposable lab cgroup namespace only."""

import socket
from pathlib import Path

root = Path("/sys/fs/cgroup")
if socket.gethostname() != "reprohpc-lab" or Path("/proc/1/cgroup").read_text().strip() != "0::/":
    raise SystemExit("Refusing cgroup changes outside the private reprohpc-lab namespace")
if not (root / "cgroup.type").exists():
    raise SystemExit("Refusing changes to the host hierarchy root")
if (root / "system.slice").exists():
    raise SystemExit("Existing system.slice: inspect the lab before reinitializing")
# Slurm without systemd expects system.slice. A populated Docker cgroup with
# CPU controllers enabled becomes domain-threaded and cannot enable memory.
# Disable those controllers, move only namespace-visible processes to a leaf,
# then enable domain controllers on the now-empty parent.
enabled = (root / "cgroup.subtree_control").read_text().split()
if enabled:
    (root / "cgroup.subtree_control").write_text(" ".join("-" + name for name in enabled))
leaf = root / "bootstrap"
leaf.mkdir(exist_ok=True)
for pid in (root / "cgroup.procs").read_text().split():
    try:
        (leaf / "cgroup.procs").write_text(pid)
    except ProcessLookupError:
        pass
(root / "cgroup.subtree_control").write_text("+cpuset +cpu +memory +pids")
system = root / "system.slice"
system.mkdir()
(system / "cgroup.subtree_control").write_text("+cpuset +cpu +memory +pids")
print("Lab controllers:", (root / "cgroup.subtree_control").read_text().strip())

"""Disconnect only the private lab, execute both profiles, and restore its networks."""

import argparse
import json
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def call(*argv):
    return subprocess.check_output(argv, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sif", required=True, help="Already prepared path inside the lab")
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, help="Fresh absolute path inside /scratch")
    parser.add_argument("--python", default="python", help="Launcher interpreter inside the lab")
    args = parser.parse_args()
    if not args.output.startswith("/scratch/") or ".." in Path(args.output).parts:
        parser.error("Acceptance output must be a new directory inside /scratch")
    inspect = json.loads(call("docker", "inspect", "reprohpc-lab"))[0]
    if (
        inspect["Config"]["Hostname"] != "reprohpc-lab"
        or inspect["HostConfig"]["CgroupnsMode"] != "private"
    ):
        raise SystemExit("Refusing to change networks outside the private disposable lab")
    networks = list(inspect["NetworkSettings"]["Networks"])
    disconnected = []
    internal = "reprohpc-offline-" + uuid.uuid4().hex[:8]
    call("docker", "network", "create", "--internal", internal)
    connected = False
    try:
        call("docker", "network", "connect", internal, "reprohpc-lab")
        connected = True
        for network in networks:
            call("docker", "network", "disconnect", network, "reprohpc-lab")
            disconnected.append(network)
        state = json.loads(call("docker", "inspect", "reprohpc-lab"))[0]
        assert list(state["NetworkSettings"]["Networks"]) == [internal]
        network_state = json.loads(call("docker", "network", "inspect", internal))[0]
        assert network_state["Internal"] and not network_state["EnableIPv6"]
        interfaces = call("docker", "exec", "reprohpc-lab", "ls", "/sys/class/net")
        routes = call("docker", "exec", "reprohpc-lab", "cat", "/proc/net/route")
        assert not any(row.split()[1] == "00000000" for row in routes.splitlines()[1:])
        probes = json.loads(
            call(
                "docker",
                "exec",
                "reprohpc-lab",
                args.python,
                "-c",
                """
import json, socket
observed = {}
for address in ('1.1.1.1', '8.8.8.8'):
    try:
        with socket.create_connection((address, 443), timeout=2):
            raise AssertionError('Unexpected external connectivity')
    except OSError as exc:
        observed[address] = str(exc)
print(json.dumps(observed))
""",
            )
        )
        for profile in ("local", "slurm"):
            argv = [
                "docker",
                "exec",
                "-u",
                "researcher",
                "-e",
                "NXF_OFFLINE=true",
                "reprohpc-lab",
                args.python,
                "reprohpc",
                "run",
                "--profile",
                profile,
                "--params-file",
                "/workspace/params/demo.yaml",
                "--sif",
                args.sif,
                "--sif-sha256",
                args.sha256,
                "--outdir",
                f"{args.output}/{profile}",
                "--work-dir",
                f"{args.output}/{profile}-work",
                "--launch-dir",
                f"{args.output}/{profile}-launch",
            ]
            if profile == "slurm":
                argv += ["--account", "research"]
            subprocess.run(argv, check=True)
        comparison = json.loads(
            call(
                "docker",
                "exec",
                "-u",
                "researcher",
                "reprohpc-lab",
                args.python,
                "reprohpc",
                "compare",
                "--expected",
                f"{args.output}/local",
                "--actual",
                f"{args.output}/slurm",
            )
        )
        record = {
            "sif_sha256": args.sha256,
            "attached_networks_during_execution": [{"name": internal, "internal": True}],
            "interfaces_during_execution": interfaces.split(),
            "ipv4_routes": routes,
            "external_connection_failures": probes,
            "launcher_python": args.python,
            "profiles": ["local", "slurm"],
            "comparison": comparison,
            "run_root": args.output,
        }
        path = ROOT / "evidence/offline.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record, indent=2))
    finally:
        for network in disconnected:
            call("docker", "network", "connect", network, "reprohpc-lab")
        if connected:
            call("docker", "network", "disconnect", internal, "reprohpc-lab")
        call("docker", "network", "rm", internal)


if __name__ == "__main__":
    main()

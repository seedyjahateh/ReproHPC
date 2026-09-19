# Installation

The tested development stack is Linux x86-64, Python 3.12.3, Nextflow 25.10.4, Java 21, Apptainer 1.5.3, and Slurm 23.11.4. Exact downloaded tool checksums are in `environment.lock.json`; Python dependency versions and hashes are in `requirements*.lock`. The application image contains its own science code and libraries. The host launcher uses only the lightweight dependencies in `requirements-host.lock`; NumPy and OpenCV are required only inside the SIF. It checks fixed PNG headers and bounded bool NPY masks without loading scientific libraries.

The launcher reader implements the bounded two-dimensional bool subset of the [NPY format](https://numpy.org/doc/2.2/reference/generated/numpy.lib.format.html), including C/Fortran order and format versions 1–3. Object arrays and oversized/truncated files are rejected.

## Disposable Docker lab

From the repository on the Docker host (PowerShell or a Linux shell):

```text
python scripts/build_lab.py
docker run -d --init --name reprohpc-lab --hostname reprohpc-lab --add-host reprohpc-lab:127.0.0.1 --privileged --cgroupns private -v "${PWD}:/workspace" -v reprohpc-scratch:/scratch reprohpc-lab:dev
docker exec reprohpc-lab chown researcher:researcher /scratch
python scripts/build_candidate.py
docker exec reprohpc-lab cp /workspace/artifacts/reprohpc.sif /scratch/reprohpc.sif
docker exec reprohpc-lab bash ops/slurm/start.sh
docker exec -u researcher reprohpc-lab python reprohpc doctor --profile slurm
```

The name must be free: inspect an existing container before replacing it. `--init` reaps exited task processes. The explicit loopback hostname mapping keeps Slurm service discovery working with Docker networks disconnected. The lab is privileged solely to run nested Apptainer and delegated cgroups. No ports are published. Its disposable database credential is socket-local and is not a production credential. Do not run its bootstrap on a university host. It moves only processes visible inside its private cgroup namespace into a leaf before enabling CPU/memory controllers. Services then run with their own Unix accounts; science tasks run as UID 1001. See the [kernel cgroup-v2 delegation rules](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html) and [Slurm cgroup-v2 guide](https://slurm.schedmd.com/cgroup_v2.html).

Keep the SIF and work/cache/output on the Linux named volume. Repeated reads from a Windows bind mount can dominate container startup. Copy final artifacts to the repository with `docker cp` when needed. A shared host with competing workloads is unsuitable for performance acceptance.

For an existing development lab, [TASKS.md](../TASKS.md) identifies the current staged candidate's path and checksum. Select those exact bytes for continued verification; older candidate paths remain associated with their historical work caches.

To stop only this lab, run `docker stop reprohpc-lab`. This retains the container and named volume. Before removing either, export results and confirm no runs need their cache. Never use a global prune command for this project.

## University or dedicated Linux host

Ask the facilitator for the exact tested Java/Nextflow/Apptainer modules and an approved persistent driver location. Install the host environment with `python3.12 -m venv .venv` and `.venv/bin/pip install --require-hashes -r requirements-host.lock`. Use the verified Nextflow distribution listed in the environment lock. Apptainer/Slurm installation and memory enforcement belong to the site administrator.

Copy `conf/site.example.config` to a site-owned file and set partition, account, QoS, and bounded concurrency. The driver and workers must see the same absolute input, SIF, and work paths. Driver resources are additional to worker requests. Check `scontrol show config`, the partition, and accounting permissions before the integration suite. Compute tasks never submit child jobs.

`doctor` is a prerequisite check, not a cgroup or allocation acceptance test. Run the real Slurm tests before declaring a new site supported. Download/builds require network access; ordinary `run` does not download artifacts.

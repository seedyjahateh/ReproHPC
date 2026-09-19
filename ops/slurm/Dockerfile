FROM ubuntu@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3=3.12.3-0ubuntu2.1 python3-venv=3.12.3-0ubuntu2.1 \
    openjdk-21-jre-headless=21.0.12+8-1~24.04 slurm-wlm=23.11.4-1.2ubuntu5 \
    slurmdbd=23.11.4-1.2ubuntu5 mariadb-server munge ca-certificates curl git \
    squashfs-tools uidmap fuse2fs fuse-overlayfs fakeroot libseccomp2 \
    && rm -rf /var/lib/apt/lists/*
COPY apptainer_1.5.3_amd64.deb /tmp/apptainer.deb
RUN echo '82b0bdddf459087d202383360b8318d526ad6826c748a2f669913cc6aef9ee40  /tmp/apptainer.deb' | sha256sum -c - \
    && apt-get update && apt-get install -y /tmp/apptainer.deb && rm /tmp/apptainer.deb && rm -rf /var/lib/apt/lists/*
COPY nextflow-25.10.4-dist /usr/local/bin/nextflow
RUN echo '60db869a7618783e3ee60183a2b0c6c50ffc67221eabec60800b904991786aaa  /usr/local/bin/nextflow' | sha256sum -c - \
    && chmod 755 /usr/local/bin/nextflow
RUN python3 -m venv /opt/venv
COPY requirements-build.lock /tmp/requirements-build.lock
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements-build.lock
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements-dev.lock
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/workspace/src JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    NXF_OPTS=-Xmx1g OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
RUN useradd --create-home --uid 1001 researcher && mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm /run/munge \
    && chown -R slurm:slurm /var/spool/slurmctld /var/log/slurm && chown munge:munge /run/munge
WORKDIR /workspace
CMD ["sleep", "infinity"]

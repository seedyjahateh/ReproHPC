FROM ubuntu@sha256:a61567bd31828687156d735ea8eb01ba4e37636e225dd6a48ba94136a70d9d61
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3=3.12.3-0ubuntu2.1 python3-venv=3.12.3-0ubuntu2.1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/opt/reprohpc/src \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
    PYTHONHASHSEED=0 LC_ALL=C TZ=UTC
COPY requirements-build.lock /opt/reprohpc/requirements-build.lock
RUN pip install --require-hashes --only-binary=:all: -r /opt/reprohpc/requirements-build.lock
COPY requirements.lock /opt/reprohpc/requirements.lock
RUN pip install --require-hashes --only-binary=:all: -r /opt/reprohpc/requirements.lock
COPY src /opt/reprohpc/src
COPY LICENSE THIRD_PARTY_NOTICES.md /opt/reprohpc/
RUN python -c "import cv2,numpy,reprohpc; print(cv2.__version__,numpy.__version__)"
WORKDIR /work
CMD ["python", "-m", "reprohpc.task", "--help"]

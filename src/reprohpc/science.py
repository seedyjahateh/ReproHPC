"""The frozen demo-cv-v1 contract. No scheduler or filesystem state in computation."""

import hashlib
import io
import os
import random
from pathlib import Path

for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from .config import science_params  # noqa: E402
from .errors import ReproError  # noqa: E402
from .io import atomic_bytes, fingerprint, image_header, write_csv, write_json  # noqa: E402

OBJECT_FIELDS = [
    "sample_id",
    "object_id",
    "area_px",
    "centroid_x_px",
    "centroid_y_px",
    "mean_intensity",
    "touches_border",
    "area_um2",
]
IMAGE_FIELDS = [
    "sample_id",
    "width",
    "height",
    "object_count",
    "foreground_fraction",
    "mean_area_px",
    "qc",
    "parameter_sha256",
]


def seed_sample(seed, sample_id):
    value = (
        int.from_bytes(hashlib.sha256(f"{seed}:{sample_id}".encode()).digest()[:4], "big")
        % 2147483647
    )
    random.seed(value)
    np.random.seed(value)
    cv2.setRNGSeed(value)
    cv2.setNumThreads(1)
    cv2.setUseOptimized(False)
    cv2.ocl.setUseOpenCL(False)
    return value


def decode(path: Path):
    width, height = image_header(path)
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.dtype != np.uint8 or image.shape != (height, width):
        raise ReproError(f"Cannot decode valid grayscale image: {path}")
    return image


def analyze(image, sample_id, params, pixel_size_um=None):
    seed_sample(params["seed"], sample_id)
    kernel = params["gaussian_kernel"]
    blurred = (
        image
        if kernel == 1
        else cv2.GaussianBlur(
            image, (kernel, kernel), params["gaussian_sigma"], borderType=cv2.BORDER_REFLECT_101
        )
    )
    binary = (blurred > params["threshold"]).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=params["connectivity"], ltype=cv2.CV_32S
    )
    # Linear-time label lookup and intensity accumulation; avoid an image-sized mask per object.
    sums = np.bincount(labels.ravel(), weights=image.ravel(), minlength=count)
    keep = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] >= params["min_area_px"]) + 1
    lookup = np.zeros(count, dtype=bool)
    lookup[keep] = True
    mask = lookup[labels]
    order = sorted(
        keep,
        key=lambda i: (
            stats[i, cv2.CC_STAT_TOP],
            stats[i, cv2.CC_STAT_LEFT],
            centroids[i, 1],
            centroids[i, 0],
            int(i),
        ),
    )
    height, width = image.shape
    objects = []
    for object_id, label in enumerate(order, 1):
        x, y, w, h, area = map(int, stats[label])
        objects.append(
            dict(
                zip(
                    OBJECT_FIELDS,
                    [
                        sample_id,
                        object_id,
                        area,
                        float(centroids[label, 0]),
                        float(centroids[label, 1]),
                        float(sums[label] / area),
                        x == 0 or y == 0 or x + w == width or y + h == height,
                        None if pixel_size_um is None else area * pixel_size_um**2,
                    ],
                    strict=True,
                )
            )
        )
    qc = []
    if not objects:
        qc.append("NO_OBJECTS")
    if any(obj["touches_border"] for obj in objects):
        qc.append("BORDER_OBJECTS")
    metrics = dict(
        zip(
            IMAGE_FIELDS,
            [
                sample_id,
                width,
                height,
                len(objects),
                float(mask.sum() / mask.size),
                None if not objects else sum(o["area_px"] for o in objects) / len(objects),
                qc,
                fingerprint(science_params(params)),
            ],
            strict=True,
        )
    )
    metrics["schema_version"] = "1.0.0"
    return mask, objects, metrics


def write_analysis(path: Path, image, sample_id, params, pixel_size_um=None):
    mask, objects, metrics = analyze(image, sample_id, params, pixel_size_um)
    buffer = io.BytesIO()
    np.save(buffer, mask, allow_pickle=False)
    atomic_bytes(path / "mask.npy", buffer.getvalue())
    write_csv(path / "objects.csv", OBJECT_FIELDS, objects)
    write_json(path / "metrics.json", metrics)
    if params["write_previews"]:
        scale = min(1.0, 512 / max(image.shape))
        size = (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale)))
        preview = cv2.cvtColor(
            cv2.resize(image, size, interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR
        )
        small_mask = cv2.resize(mask.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST)
        contours, _ = cv2.findContours(small_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(preview, contours, -1, (0, 0, 255), 1)
        success, encoded = cv2.imencode(".png", preview)
        if not success:
            raise ReproError(f"Could not encode preview for {sample_id}", 4)
        atomic_bytes(path / "preview.png", encoded.tobytes())
    return metrics

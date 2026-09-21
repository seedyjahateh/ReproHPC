"""Deterministic serialization, atomic output, and confined file access."""

import ast
import csv
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ReproError


def image_header(path: Path):
    """Read only the fixed PNG header for bounded login-node preflight."""
    import struct

    with path.open("rb") as stream:
        header = stream.read(33)
    if len(header) != 33 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ReproError(f"Not a PNG image: {path}")
    width, height, depth, color = struct.unpack(">IIBB", header[16:26])
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and depth == 8 and color == 0):
        raise ReproError(
            f"Unsupported image {path}: {width}x{height}, depth={depth}, color={color}; require grayscale 8-bit <=4096x4096"
        )
    return width, height


@dataclass(frozen=True)
class Mask:
    shape: tuple[int, int]
    pixels: bytes

    @property
    def foreground(self):
        return self.pixels.count(b"\x01")


def read_mask(path: Path) -> Mask:
    """Read the bounded bool NPY contract without importing scientific libraries.

    NPY v1/v2/v3 follow numpy.lib.format; object/pickle arrays are never accepted.
    C and Fortran order normalize to row-major boolean bytes for comparison.
    """
    try:
        with path.open("rb") as stream:
            magic = stream.read(8)
            if magic[:6] != b"\x93NUMPY" or magic[6:] not in (
                b"\x01\x00",
                b"\x02\x00",
                b"\x03\x00",
            ):
                raise ValueError("unsupported NPY magic/version")
            length_size = 2 if magic[6] == 1 else 4
            raw_length = stream.read(length_size)
            length = int.from_bytes(raw_length, "little")
            if len(raw_length) != length_size or not 1 <= length <= 10000:
                raise ValueError("invalid or oversized NPY header")
            raw_header = stream.read(length)
            if len(raw_header) != length or not raw_header.endswith(b"\n"):
                raise ValueError("truncated NPY header")
            header = ast.literal_eval(raw_header.decode("utf-8" if magic[6] == 3 else "latin1"))
            if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
                raise ValueError("invalid NPY header keys")
            shape = header["shape"]
            if (
                header["descr"] != "|b1"
                or type(header["fortran_order"]) is not bool
                or not isinstance(shape, tuple)
                or len(shape) != 2
                or any(type(size) is not int or not 1 <= size <= 4096 for size in shape)
            ):
                raise ValueError("require a two-dimensional bool mask <=4096x4096")
            height, width = shape
            pixels = stream.read(height * width + 1)
            if len(pixels) != height * width:
                raise ValueError("truncated NPY data or trailing bytes")
            pixels = pixels.translate(bytes([0] + [1] * 255))
            if header["fortran_order"]:
                rows = bytearray(len(pixels))
                for column in range(width):
                    rows[column::width] = pixels[column * height : (column + 1) * height]
                pixels = bytes(rows)
            return Mask(shape, pixels)
    except (OSError, ValueError, SyntaxError, TypeError, RecursionError) as exc:
        raise ReproError(f"Invalid mask {path}: {exc}", 5) from exc


NIFTI_TYPES = {
    # NIfTI-1 datatype code: (name, array typecode, bytes per voxel)
    2: ("uint8", "B", 1),
    4: ("int16", "h", 2),
    8: ("int32", "i", 4),
    16: ("float32", "f", 4),
    64: ("float64", "d", 8),
    256: ("int8", "b", 1),
    512: ("uint16", "H", 2),
    768: ("uint32", "I", 4),
}


@dataclass(frozen=True)
class NiftiHeader:
    dims: tuple[int, ...]
    voxel_size_mm: tuple[float, ...]
    datatype: str
    vox_offset: int
    byteorder: str

    @property
    def voxels(self):
        count = 1
        for size in self.dims:
            count *= size
        return count


def _nifti_stream(path: Path):
    import gzip

    stream = path.open("rb")
    if path.name.endswith(".gz"):
        return gzip.GzipFile(fileobj=stream, mode="rb")
    return stream


def nifti_header(path: Path) -> NiftiHeader:
    """Read only the fixed 348-byte NIfTI-1 header, without scientific libraries.

    Accepts single-file NIfTI-1 (magic "n+1"), 3-D or 4-D with one volume, and the
    integer/float datatypes FSL writes. Everything else is rejected with the reason.
    """
    import math
    import struct

    try:
        with _nifti_stream(path) as stream:
            raw = stream.read(352)
    except (OSError, EOFError) as exc:
        raise ReproError(f"Cannot read NIfTI {path}: {exc}") from exc
    if len(raw) < 348:
        raise ReproError(f"Not a NIfTI-1 file (truncated header): {path}")
    for byteorder in ("<", ">"):
        if struct.unpack(f"{byteorder}i", raw[:4])[0] == 348:
            break
    else:
        raise ReproError(f"Not a single-file NIfTI-1 image (sizeof_hdr != 348): {path}")
    if raw[344:348] != b"n+1\x00":
        raise ReproError(f"Not a single-file NIfTI-1 image (magic {raw[344:348]!r}): {path}")
    dim = struct.unpack(f"{byteorder}8h", raw[40:56])
    datatype = struct.unpack(f"{byteorder}h", raw[70:72])[0]
    pixdim = struct.unpack(f"{byteorder}8f", raw[76:108])
    vox_offset = struct.unpack(f"{byteorder}f", raw[108:112])[0]
    rank = dim[0]
    if rank not in (3, 4) or (rank == 4 and dim[4] != 1):
        raise ReproError(f"Require one 3-D volume, found dim={dim[: rank + 1]}: {path}")
    dims = tuple(dim[1:4])
    if any(not 1 <= size <= 2048 for size in dims):
        raise ReproError(f"Unsupported NIfTI dimensions {dims}: {path}")
    sizes = tuple(float(value) for value in pixdim[1:4])
    if any(not (math.isfinite(size) and size > 0) for size in sizes):
        raise ReproError(f"Invalid voxel size {sizes}: {path}")
    if datatype not in NIFTI_TYPES:
        raise ReproError(f"Unsupported NIfTI datatype code {datatype}: {path}")
    if not (vox_offset >= 352 and vox_offset == int(vox_offset)):
        raise ReproError(f"Invalid NIfTI vox_offset {vox_offset}: {path}")
    return NiftiHeader(dims, sizes, NIFTI_TYPES[datatype][0], int(vox_offset), byteorder)


@dataclass(frozen=True)
class NiftiMask:
    header: NiftiHeader
    nonzero: int
    voxel_sha256: str


def read_nifti_mask(path: Path) -> NiftiMask:
    """Stream a NIfTI volume's voxels: count non-zero voxels and hash the voxel bytes.

    The hash covers voxel data only, not the header, so it identifies a mask by content
    independently of header text fields or gzip framing.
    """
    import array
    import sys

    header = nifti_header(path)
    _, code, width = next(value for value in NIFTI_TYPES.values() if value[0] == header.datatype)
    expected = header.voxels * width
    digest = hashlib.sha256()
    nonzero = 0
    remaining = expected
    chunk = width * 1024 * 1024
    try:
        with _nifti_stream(path) as stream:
            if len(stream.read(header.vox_offset)) != header.vox_offset:
                raise ReproError(f"Truncated NIfTI before voxel data: {path}")
            while remaining:
                block = stream.read(min(chunk, remaining))
                if not block or len(block) % width:
                    raise ReproError(f"Truncated NIfTI voxel data: {path}")
                digest.update(block)
                values = array.array(code, block)
                if (header.byteorder == ">") != (sys.byteorder == "big"):
                    values.byteswap()
                nonzero += len(values) - values.count(0)
                remaining -= len(block)
    except (OSError, EOFError) as exc:
        raise ReproError(f"Cannot read NIfTI {path}: {exc}") from exc
    return NiftiMask(header, nonzero, digest.hexdigest())


def canonical(value) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        )
        + "\n"
    ).encode("utf-8")


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value):
    atomic_bytes(path, canonical(value))


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_invalid_number)
    except (OSError, ValueError) as exc:
        raise ReproError(f"Cannot read JSON {path}: {exc}") from exc


def _invalid_number(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def confined(root: Path, relative: str) -> Path:
    if not relative or any(c in relative for c in "\x00\r\n\\"):
        raise ReproError(f"Invalid relative path: {relative!r}")
    path = Path(relative)
    if path.is_absolute() or ":" in relative or ".." in path.parts:
        raise ReproError(f"Path escapes declared root: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ReproError(f"Symlink escapes declared root: {relative}")
    return resolved


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, list):
        return ";".join(value)
    return value


def write_csv(path: Path, fields, rows):
    """Stream rows to a temporary sibling before publishing atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: csv_value(row[key]) for key in fields})
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

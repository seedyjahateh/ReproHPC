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

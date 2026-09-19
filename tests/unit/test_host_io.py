"""Cross-check the dependency-free launcher readers against actual NumPy files."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from reprohpc.errors import ReproError
from reprohpc.io import read_mask


@pytest.mark.parametrize("version", [(1, 0), (2, 0), (3, 0)])
@pytest.mark.parametrize("order", ["C", "F"])
def test_npy_mask_reader_matches_numpy(tmp_path, version, order):
    array = np.array([[False, True, False], [True, True, False]], dtype=bool, order=order)
    path = tmp_path / "mask.npy"
    with path.open("wb") as stream:
        np.lib.format.write_array(stream, array, version=version)
    mask = read_mask(path)
    assert mask.shape == array.shape
    assert mask.pixels == array.tobytes(order="C")
    assert mask.foreground == 3


@pytest.mark.parametrize(
    "array",
    [
        np.zeros((2, 2), dtype=np.uint8),
        np.array([object()]),
        np.zeros((1, 4097), dtype=bool),
        np.zeros(2, dtype=bool),
    ],
)
def test_reject_other_arrays(tmp_path, array):
    path = tmp_path / "bad.npy"
    np.save(path, array)
    with pytest.raises(ReproError, match="two-dimensional bool"):
        read_mask(path)


@pytest.mark.parametrize(
    "mutation", ["magic", "version", "length", "header", "shape", "short", "trailing"]
)
def test_reject_corrupt_masks(tmp_path, mutation):
    path = tmp_path / "bad.npy"
    np.save(path, np.ones((2, 3), dtype=bool))
    raw = path.read_bytes()
    if mutation == "magic":
        raw = b"invalid"
    elif mutation == "version":
        raw = raw[:6] + b"\x04\x00" + raw[8:]
    elif mutation == "length":
        raw = raw[:8] + b"\xff\xff" + raw[10:]
    elif mutation == "header":
        raw = raw.replace(b"'shape'", b"'other'")
    elif mutation == "shape":
        raw = raw.replace(b"(2, 3)", b"(0, 3)")
    elif mutation == "short":
        raw = raw[:-1]
    else:
        raw += b"extra"
    path.write_bytes(raw)
    with pytest.raises(ReproError, match="Invalid mask"):
        read_mask(path)


def test_numpy_nonzero_boolean_storage(tmp_path):
    path = tmp_path / "mask.npy"
    np.save(path, np.ones((1, 3), dtype=bool))
    path.write_bytes(path.read_bytes()[:-3] + b"\x00\x02\xff")
    assert read_mask(path).pixels == np.load(path).astype(np.uint8).tobytes()


def test_launcher_never_imports_scientific_dependencies():
    root = Path(__file__).resolve().parents[2]
    code = """
import importlib.abc
import sys
sys.path.insert(0, 'src')
class BlockScience(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'cv2'}:
            raise AssertionError('Launcher imported scientific dependency: ' + fullname)
sys.meta_path.insert(0, BlockScience())
from reprohpc.cli import parser
from reprohpc.provenance import compare
from reprohpc.data import validate_dataset
from pathlib import Path
parser().parse_args(['doctor'])
_, samples = validate_dataset(Path('data/demo/1.0.0/dataset.json'), Path('data/demo/1.0.0/samples.csv'), preflight=True)
assert len(samples) == 12
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True, capture_output=True)

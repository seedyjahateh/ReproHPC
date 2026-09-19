import numpy as np
import pytest

from reprohpc.config import resolve_params
from reprohpc.science import analyze, seed_sample


def test_analytical_geometry_and_calibration():
    image = np.zeros((20, 20), np.uint8)
    image[2:6, 3:8] = 200
    params = resolve_params({"gaussian_kernel": 1, "min_area_px": 20})
    mask, objects, metrics = analyze(image, "a", params, 0.5)
    assert mask.sum() == 20
    assert objects == [
        {
            "sample_id": "a",
            "object_id": 1,
            "area_px": 20,
            "centroid_x_px": 5.0,
            "centroid_y_px": 3.5,
            "mean_intensity": 200.0,
            "touches_border": False,
            "area_um2": 5.0,
        }
    ]
    assert metrics["object_count"] == 1


def test_threshold_empty_and_nulls():
    mask, objects, metrics = analyze(np.full((10, 10), 127, np.uint8), "a", resolve_params())
    assert not mask.any() and objects == []
    assert metrics["mean_area_px"] is None and metrics["qc"] == ["NO_OBJECTS"]


def test_connectivity_border_and_stable_ids():
    image = np.zeros((10, 10), np.uint8)
    image[0, 0] = image[1, 1] = image[8, 8] = 255
    params = resolve_params({"gaussian_kernel": 1, "min_area_px": 1, "connectivity": 4})
    assert len(analyze(image, "a", params)[1]) == 3
    params["connectivity"] = 8
    _, objects, metrics = analyze(image, "a", params)
    assert [x["area_px"] for x in objects] == [2, 1]
    assert objects[0]["touches_border"] and "BORDER_OBJECTS" in metrics["qc"]


def test_seed_independent_of_call_order():
    first = seed_sample(42, "a")
    seed_sample(42, "b")
    assert seed_sample(42, "a") == first
    assert seed_sample(43, "a") != first


@pytest.mark.parametrize(
    "change",
    [
        {"threshold": True},
        {"threshold": 256},
        {"gaussian_kernel": 2},
        {"gaussian_sigma": float("nan")},
        {"connectivity": 6},
        {"write_previews": "false"},
        {"batch_size": 0},
        {"seed": -1},
        {"algorithm": "unknown"},
        {"threshhold": 3},
    ],
)
def test_invalid_parameters(change):
    from reprohpc.errors import ReproError

    with pytest.raises(ReproError):
        resolve_params(change)

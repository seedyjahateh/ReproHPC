"""Validated parameter contract; execution and science remain distinct."""

import re
from pathlib import Path

import yaml

from .errors import ReproError

SCIENCE = {
    "algorithm": "demo-cv-v1",
    "seed": 42,
    "gaussian_kernel": 5,
    "gaussian_sigma": 1.0,
    "threshold": 127,
    "min_area_px": 20,
    "connectivity": 8,
}
DEFAULTS = {**SCIENCE, "batch_size": 16, "write_previews": True}
PATHS = {"dataset", "input_manifest", "reference"}
EXECUTION = {
    "partition": "demo",
    "account": "",
    "qos": "",
    "array_size": 4,
    "max_inflight": 8,
    "analysis_memory": "2 GB",
    "analysis_time": "10 min",
}
TOKEN = re.compile(r"^[A-Za-z0-9_.-]*$")


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ReproError(f"Duplicate or non-string configuration key: {key!r}", 2)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(path: Path):
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ReproError(f"Cannot read parameters {path}: {exc}", 2) from exc
    if not isinstance(data, dict):
        raise ReproError("Parameter file must contain a mapping", 2)
    return data


def resolve_params(data=None, overrides=None, *, base=None):
    supplied = {**(data or {}), **(overrides or {})}
    unknown = set(supplied) - (set(DEFAULTS) | PATHS | {"schema_version"})
    if unknown:
        raise ReproError(f"Unknown parameter(s): {', '.join(sorted(unknown))}", 2)
    params = {"schema_version": "1.0.0", **DEFAULTS, **supplied}
    if params["schema_version"] != "1.0.0":
        raise ReproError("Unsupported parameter schema_version; require 1.0.0", 2)
    bounds = {
        "seed": (0, 2147483647),
        "gaussian_kernel": (1, 31),
        "threshold": (0, 255),
        "min_area_px": (1, 16777216),
        "batch_size": (1, 64),
    }
    for key, (low, high) in bounds.items():
        if type(params[key]) is not int or not low <= params[key] <= high:
            raise ReproError(f"{key} must be an integer from {low} to {high}", 2)
    if params["gaussian_kernel"] % 2 != 1:
        raise ReproError("gaussian_kernel must be odd", 2)
    sigma = params["gaussian_sigma"]
    if type(sigma) not in (int, float) or not 0 < sigma <= 10:
        raise ReproError("gaussian_sigma must be finite and in (0, 10]", 2)
    params["gaussian_sigma"] = float(sigma)
    if type(params["connectivity"]) is not int or params["connectivity"] not in (4, 8):
        raise ReproError("connectivity must be 4 or 8", 2)
    if params["algorithm"] != "demo-cv-v1":
        raise ReproError("Only algorithm demo-cv-v1 is supported", 2)
    if type(params["write_previews"]) is not bool:
        raise ReproError("write_previews must be boolean", 2)
    if base is not None:
        for key in PATHS:
            value = params.get(key)
            if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
                raise ReproError(f"{key} must be a valid local path", 2)
            path = Path(value)
            params[key] = str((base / path).resolve())
            if not Path(params[key]).is_file():
                raise ReproError(f"{key} does not exist: {params[key]}", 3)
    return params


def science_params(params):
    return {key: params[key] for key in SCIENCE}


def validate_execution(settings):
    unknown = set(settings) - set(EXECUTION)
    if unknown:
        raise ReproError(f"Unknown execution settings: {sorted(unknown)}", 2)
    settings = {**EXECUTION, **settings}
    for key in ("partition", "account", "qos"):
        if not isinstance(settings[key], str) or not TOKEN.fullmatch(settings[key]):
            raise ReproError(f"Invalid scheduler {key}", 2)
    for key in ("array_size", "max_inflight"):
        if type(settings[key]) is not int or not 1 <= settings[key] <= 1000:
            raise ReproError(f"{key} must be an integer in [1, 1000]", 2)
    if settings["array_size"] > settings["max_inflight"]:
        raise ReproError("array_size exceeds max_inflight", 2)
    for key, pattern in (
        ("analysis_memory", r"[1-9][0-9]* (MB|GB)"),
        ("analysis_time", r"[1-9][0-9]* (sec|min|hour)"),
    ):
        if not isinstance(settings[key], str) or not re.fullmatch(pattern, settings[key]):
            raise ReproError(f"Invalid {key}: use a positive integer and supported unit", 2)
    return settings

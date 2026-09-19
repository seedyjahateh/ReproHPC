"""Published JSON Schema contracts used by producers and consumers."""

from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from .errors import ReproError
from .io import read_json


@lru_cache(maxsize=32)
def validator(name):
    schema = read_json(files("reprohpc").joinpath("schemas", f"{name}.json"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name, value):
    errors = sorted(
        validator(name).iter_errors(value),
        key=lambda error: str(list(error.path)),
    )
    if errors:
        error = errors[0]
        raise ReproError(
            f"{name} schema at {'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
        )
    return value

"""Version-gated runtime ROM patches for the FRLG localization research track.

No retail profile is bundled until its ROM, wrapper and staging layout are verified.
"""

import hashlib
import json
import math

PAGE_SIZE = 0x1000
ROM_BASE = 0x08000000


class LocalizationError(ValueError):
    """A patch, profile or console state that must not be written."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def integer(value, name, minimum=0, maximum=0xFFFFFFFF):
    if type(value) is not int or not minimum <= value <= maximum:
        raise LocalizationError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def digest(value, name):
    if not isinstance(value, str) or len(value) != 64:
        raise LocalizationError(f"{name} must be a lowercase SHA-256")
    if any(c not in "0123456789abcdef" for c in value):
        raise LocalizationError(f"{name} must be a lowercase SHA-256")
    return value


def unhex(value, name):
    if not isinstance(value, str) or len(value) % 2:
        raise LocalizationError(f"{name} must be whole hexadecimal bytes")
    try:
        result = bytes.fromhex(value)
    except ValueError as exc:
        raise LocalizationError(f"invalid {name}") from exc
    if result.hex() != value:
        raise LocalizationError(f"{name} must be lowercase hexadecimal without spaces")
    return result


def fields(value, required, optional=()):
    if not isinstance(value, dict):
        raise LocalizationError("expected a JSON object")
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or extra:
        raise LocalizationError(f"invalid fields: missing={sorted(missing)}, extra={sorted(extra)}")


def array(value, name):
    if not isinstance(value, list):
        raise LocalizationError(f"{name} must be an array")
    return value


def read_json(path):
    """Reject duplicate keys: a content identifier must have only one interpretation."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise LocalizationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        def constant(value):
            raise LocalizationError(f"non-finite JSON number: {value}")
        def floating(value):
            result = float(value)
            if not math.isfinite(result):
                return constant(value)
            return result
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                          parse_constant=constant, parse_float=floating)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LocalizationError(f"invalid JSON in {path}: {exc}") from exc

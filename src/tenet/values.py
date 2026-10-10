"""Immutable JSON values and stable, explicit serialization at trust boundaries."""
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
import hashlib
import json
import math


def freeze(value):
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('JSON object keys must be strings')
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f'unsupported JSON value: {type(value).__name__}')


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


def canonical(value):
    return json.dumps(thaw(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def utc_now():
    return datetime.now(timezone.utc)

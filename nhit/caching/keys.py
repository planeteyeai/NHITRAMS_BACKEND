"""Deterministic cache key generation."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def normalize_payload(value: Any) -> Any:
    """Normalize request data so equivalent payloads share a cache key."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): normalize_payload(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        normalized_items = [normalize_payload(item) for item in value]
        if normalized_items and all(isinstance(item, (str, int, float, bool)) or item is None for item in normalized_items):
            return sorted(
                normalized_items,
                key=lambda item: (item is None, type(item).__name__, str(item)),
            )
        return normalized_items
    return value


def payload_digest(payload: Any) -> str:
    normalized = normalize_payload(payload)
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def build_cache_key(*, namespace: str, version: int, payload: Any, scope: str = '') -> str:
    digest = payload_digest({'payload': payload, 'scope': scope or ''})
    return f'{namespace}:v{version}:{digest}'

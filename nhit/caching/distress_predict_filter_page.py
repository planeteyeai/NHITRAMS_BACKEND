"""distress_predict_filter page cache: gzip-compressed JSON bytes (DPGZ1).

Public API still receives the normal nested-list JSON. Redis stores
``DPGZ1\\n`` + gzip(JSON). HIT paths may return cached bytes for direct HTTP
serving when requested via ``serve_mode``.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from typing import Any, Callable, Literal

from rest_framework.renderers import JSONRenderer

from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import build_cache_key
from nhit.caching.service import (
    _acquire_population_lock,
    _release_population_lock,
    _wait_for_cached_value,
    cache_backend_name,
    get_namespace_version,
    is_cache_usable,
    safe_cache_get,
    safe_cache_set,
)

logger = logging.getLogger('nhit.cache')

DISTRESS_PREDICT_FILTER_PAGE_SCOPE = 'page_gzip_json_v1'
DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC = b'DPGZ1\n'
_CACHE_MAGIC = DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC
_GZIP_LEVEL = 6

ServeMode = Literal['python', 'gzip_bytes', 'json_bytes']


def encode_distress_predict_filter_page_cache(value: Any) -> bytes:
    """Serialize nested page response to gzip-compressed JSON bytes."""
    json_bytes = JSONRenderer().render(value)
    return DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC + gzip.compress(
        json_bytes, compresslevel=_GZIP_LEVEL
    )


def extract_distress_predict_filter_page_gzip_payload(raw: Any) -> bytes:
    """Validate DPGZ1 cache blob and return gzip(JSON) payload (no magic)."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ValueError(
            f'unsupported distress_predict_filter page cache type: {type(raw).__name__}'
        )
    payload = bytes(raw)
    if not payload.startswith(DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC):
        raise ValueError('unsupported distress_predict_filter page cache format')
    gzip_payload = payload[len(DISTRESS_PREDICT_FILTER_PAGE_CACHE_MAGIC) :]
    if not gzip_payload:
        raise ValueError('empty distress_predict_filter page gzip payload')
    if len(gzip_payload) < 2 or gzip_payload[0] != 0x1F or gzip_payload[1] != 0x8B:
        raise ValueError('distress_predict_filter page cache payload is not gzip')
    return gzip_payload


def decode_distress_predict_filter_page_cache(raw: Any) -> Any:
    """Decode cache bytes back to Python nested lists/dicts."""
    gzip_payload = extract_distress_predict_filter_page_gzip_payload(raw)
    json_bytes = gzip.decompress(gzip_payload)
    return json.loads(json_bytes)


def _hit_value_for_mode(cached_raw: Any, serve_mode: ServeMode) -> tuple[Any, str, dict[str, Any]]:
    timings: dict[str, Any] = {}
    t0 = time.perf_counter()
    gzip_payload = extract_distress_predict_filter_page_gzip_payload(cached_raw)
    timings['extract_ms'] = round((time.perf_counter() - t0) * 1000, 2)

    if serve_mode == 'gzip_bytes':
        return gzip_payload, 'direct_gzip_bytes', timings

    if serve_mode == 'json_bytes':
        t1 = time.perf_counter()
        json_bytes = gzip.decompress(gzip_payload)
        timings['gunzip_ms'] = round((time.perf_counter() - t1) * 1000, 2)
        timings['decode_ms'] = round(
            (timings['extract_ms'] or 0) + (timings['gunzip_ms'] or 0),
            2,
        )
        return json_bytes, 'direct_json_bytes', timings

    t1 = time.perf_counter()
    value = json.loads(gzip.decompress(gzip_payload))
    timings['decode_ms'] = round((time.perf_counter() - t0) * 1000, 2)
    timings['gunzip_json_loads_ms'] = round((time.perf_counter() - t1) * 1000, 2)
    return value, 'python_object', timings


def get_cached_distress_predict_filter_page(
    *,
    payload: dict[str, Any],
    builder: Callable[[], Any],
    timeout: int = CACHE_TTL_FILTER,
    serve_mode: ServeMode = 'python',
    version: int | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Get or build page response; Redis stores DPGZ1 + gzip JSON bytes.

    When *version* is provided, skip the namespace version Redis GET.
    """
    meta: dict[str, Any] = {
        'cache_hit': False,
        'cache_enabled': is_cache_usable(),
        'cache_backend': cache_backend_name().rsplit('.', 1)[-1],
        'cache_repr': DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        'serve_mode': serve_mode,
        'response_path': 'python_object',
        'redis_round_trips': 0,
    }

    if not is_cache_usable():
        start = time.perf_counter()
        value = builder()
        meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
        return _adapt_miss_value(value, serve_mode, meta, store=False, timeout=timeout)

    if version is None:
        t_ver0 = time.perf_counter()
        version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
        meta['version_get_ms'] = round((time.perf_counter() - t_ver0) * 1000, 2)
        meta['redis_round_trips'] = 1
    else:
        meta['version_get_ms'] = 0
        meta['version_reused'] = True
    cache_key = build_cache_key(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        version=version,
        payload=payload,
        scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    )
    digest = cache_key.rsplit(':', 1)[-1]
    meta['cache_key_suffix'] = digest[:12]

    def _try_hit(cached_raw: Any) -> tuple[Any, dict[str, Any]] | None:
        try:
            t_get_decode = time.perf_counter()
            value, response_path, timings = _hit_value_for_mode(cached_raw, serve_mode)
            meta.update(timings)
            meta['cache_hit'] = True
            meta['response_path'] = response_path
            if serve_mode == 'gzip_bytes':
                meta['redis_payload_bytes'] = (
                    len(cached_raw) if isinstance(cached_raw, (bytes, bytearray)) else None
                )
                meta['http_body_bytes'] = len(value)
            elif serve_mode == 'json_bytes':
                meta['http_body_bytes'] = len(value)
            meta['hit_adapt_ms'] = round((time.perf_counter() - t_get_decode) * 1000, 2)
            return value, meta
        except Exception:
            logger.warning(
                'distress_predict_filter page cache decode failed key_suffix=%s; rebuilding',
                digest[:12],
                exc_info=True,
            )
            return None

    get_timings: dict[str, Any] = {}
    t_redis0 = time.perf_counter()
    hit, cached_raw = safe_cache_get(cache_key, timings=get_timings)
    meta['redis_get_ms'] = round((time.perf_counter() - t_redis0) * 1000, 2)
    meta['page_data_get_ms'] = get_timings.get('cache_get_ms')
    meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 0) + 1
    if hit:
        adapted = _try_hit(cached_raw)
        if adapted is not None:
            return adapted

    def _build_and_store() -> tuple[Any, dict[str, Any]]:
        start = time.perf_counter()
        value = builder()
        meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
        meta['cache_hit'] = False
        return _adapt_miss_value(
            value,
            serve_mode,
            meta,
            store=True,
            timeout=timeout,
            cache_key=cache_key,
            digest=digest,
        )

    acquired = _acquire_population_lock(CACHE_NS_DISTRESS_PREDICT_FILTER, digest)
    if not acquired:
        hit, cached_raw = _wait_for_cached_value(cache_key)
        if hit:
            adapted = _try_hit(cached_raw)
            if adapted is not None:
                return adapted
    try:
        hit, cached_raw = safe_cache_get(cache_key)
        if hit:
            adapted = _try_hit(cached_raw)
            if adapted is not None:
                return adapted
        return _build_and_store()
    finally:
        if acquired:
            _release_population_lock(CACHE_NS_DISTRESS_PREDICT_FILTER, digest)


def _adapt_miss_value(
    value: Any,
    serve_mode: ServeMode,
    meta: dict[str, Any],
    *,
    store: bool,
    timeout: int,
    cache_key: str | None = None,
    digest: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    encoded = None
    if store and cache_key is not None:
        try:
            t_enc = time.perf_counter()
            encoded = encode_distress_predict_filter_page_cache(value)
            meta['encode_ms'] = round((time.perf_counter() - t_enc) * 1000, 2)
            meta['redis_payload_bytes'] = len(encoded)
            safe_cache_set(cache_key, encoded, timeout=timeout)
        except Exception:
            logger.warning(
                'distress_predict_filter page cache encode/set failed key_suffix=%s',
                (digest or '')[:12],
                exc_info=True,
            )
            encoded = None

    if serve_mode == 'gzip_bytes':
        if encoded is None:
            encoded = encode_distress_predict_filter_page_cache(value)
        gzip_payload = extract_distress_predict_filter_page_gzip_payload(encoded)
        meta['response_path'] = 'direct_gzip_bytes'
        meta['http_body_bytes'] = len(gzip_payload)
        return gzip_payload, meta

    if serve_mode == 'json_bytes':
        t0 = time.perf_counter()
        json_bytes = JSONRenderer().render(value)
        meta['render_ms'] = round((time.perf_counter() - t0) * 1000, 2)
        meta['response_path'] = 'direct_json_bytes'
        meta['http_body_bytes'] = len(json_bytes)
        return json_bytes, meta

    meta['response_path'] = 'python_object'
    return value, meta

"""traffic_filter response cache: gzip-compressed JSON bytes (not pickle).

Public API still receives the normal nested-list JSON. Redis stores a small
metadata wrapper around ``TFGZ1\\n`` + gzip(JSON):

    {'kind': 'tfgz1_ns_v1', 'ns_v': <namespace_version>, 'blob': <TFGZ1 bytes>}

HIT path uses ``cache.get_many([version_key, data_key])`` so namespace version
and value are fetched in **one Redis round trip**. Invalidation still uses
``bump_namespace_version('traffic_filter')``: entries whose ``ns_v`` no longer
matches the current namespace version are treated as misses.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from typing import Any, Callable, Literal

from rest_framework.renderers import JSONRenderer

from nhit.caching.constants import CACHE_NS_TRAFFIC_FILTER, CACHE_TTL_FILTER
from nhit.caching.keys import payload_digest
from nhit.caching.service import (
    _acquire_population_lock,
    _release_population_lock,
    _version_key,
    cache_backend_name,
    is_cache_usable,
    safe_cache_get_many,
    safe_cache_set,
)

logger = logging.getLogger('nhit.cache')

# Distinct from empty scope used by historical pickle traffic_filter entries.
TRAFFIC_FILTER_CACHE_SCOPE = 'gzip_json_v1'
TRAFFIC_FILTER_CACHE_MAGIC = b'TFGZ1\n'
# Wrapper kind for generation-aware entries (TFGZ1 blob unchanged inside).
TRAFFIC_FILTER_ENTRY_KIND = 'tfgz1_ns_v1'
_GZIP_LEVEL = 6
_LOCK_WAIT_SCHEDULE_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)

ServeMode = Literal['python', 'gzip_bytes', 'json_bytes']


def encode_traffic_filter_cache(value: Any) -> bytes:
    """Serialize nested traffic_filter response to gzip-compressed JSON bytes."""
    json_bytes = JSONRenderer().render(value)
    return TRAFFIC_FILTER_CACHE_MAGIC + gzip.compress(json_bytes, compresslevel=_GZIP_LEVEL)


def extract_traffic_filter_gzip_payload(raw: Any) -> bytes:
    """Validate TFGZ1 cache blob and return gzip(JSON) payload (no magic).

    Raises ValueError for unsupported/corrupt payloads.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise ValueError(f'unsupported traffic_filter cache type: {type(raw).__name__}')
    payload = bytes(raw)
    if not payload.startswith(TRAFFIC_FILTER_CACHE_MAGIC):
        raise ValueError('unsupported traffic_filter cache format')
    gzip_payload = payload[len(TRAFFIC_FILTER_CACHE_MAGIC) :]
    if not gzip_payload:
        raise ValueError('empty traffic_filter gzip payload')
    # Minimal gzip header check (ID1/ID2) — full inflate deferred to consumers.
    if len(gzip_payload) < 2 or gzip_payload[0] != 0x1F or gzip_payload[1] != 0x8B:
        raise ValueError('traffic_filter cache payload is not gzip')
    return gzip_payload


def decode_traffic_filter_cache(raw: Any) -> Any:
    """Decode TFGZ1 cache bytes back to Python nested lists/dicts."""
    gzip_payload = extract_traffic_filter_gzip_payload(raw)
    json_bytes = gzip.decompress(gzip_payload)
    return json.loads(json_bytes)


def build_traffic_filter_data_key(payload: Any) -> tuple[str, str]:
    """Return (stable data key, digest) — no Redis namespace-version round trip.

    Key shape:
        traffic_filter:data:gzip_json_v1:<sha256(payload+scope)>
    """
    digest = payload_digest({'payload': payload, 'scope': TRAFFIC_FILTER_CACHE_SCOPE})
    key = f'{CACHE_NS_TRAFFIC_FILTER}:data:{TRAFFIC_FILTER_CACHE_SCOPE}:{digest}'
    return key, digest


def wrap_traffic_filter_entry(*, ns_v: int, blob: bytes) -> dict[str, Any]:
    """Attach namespace generation to a TFGZ1 blob for invalidation checks."""
    return {
        'kind': TRAFFIC_FILTER_ENTRY_KIND,
        'ns_v': int(ns_v),
        'blob': bytes(blob),
    }


def unwrap_traffic_filter_entry(raw: Any, *, current_ns_v: int) -> bytes | None:
    """Return TFGZ1 bytes if entry is valid for *current_ns_v*; else None."""
    if not isinstance(raw, dict):
        return None
    if raw.get('kind') != TRAFFIC_FILTER_ENTRY_KIND:
        return None
    try:
        stored_v = int(raw.get('ns_v'))
    except (TypeError, ValueError):
        return None
    if stored_v != int(current_ns_v):
        return None
    blob = raw.get('blob')
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    return bytes(blob)


def _parse_namespace_version(value: Any) -> int:
    if value is None:
        return 1
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def _hit_value_for_mode(cached_raw: bytes, serve_mode: ServeMode) -> tuple[Any, str, dict[str, Any]]:
    """Return (value, response_path, timing_bits) for a validated TFGZ1 blob."""
    timings: dict[str, Any] = {}
    t0 = time.perf_counter()
    gzip_payload = extract_traffic_filter_gzip_payload(cached_raw)
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


def _fetch_entry(
    *,
    version_key: str,
    data_key: str,
) -> tuple[int, bytes | None, float]:
    """One round-trip fetch of namespace version + data entry.

    Returns (current_ns_v, tfgz1_blob_or_None, elapsed_ms).
    """
    t0 = time.perf_counter()
    results = safe_cache_get_many([version_key, data_key])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    current_ns_v = _parse_namespace_version(results.get(version_key))
    blob = unwrap_traffic_filter_entry(results.get(data_key), current_ns_v=current_ns_v)
    return current_ns_v, blob, elapsed_ms


def _wait_for_entry(*, version_key: str, data_key: str) -> tuple[int, bytes | None, float, dict[str, Any]]:
    """Poll with bounded backoff until lock holder populates a valid entry.

    Returns (current_ns_v, tfgz1_blob_or_None, fetch_elapsed_ms, wait_diag).
    ``wait_diag`` includes actual sleep time (separate from Redis fetch time).
    """
    total_fetch_ms = 0.0
    total_sleep_ms = 0.0
    poll_count = 0
    current_ns_v = 1
    for delay in _LOCK_WAIT_SCHEDULE_SECONDS:
        t_sleep0 = time.perf_counter()
        time.sleep(delay)
        total_sleep_ms += (time.perf_counter() - t_sleep0) * 1000
        poll_count += 1
        current_ns_v, blob, elapsed_ms = _fetch_entry(
            version_key=version_key,
            data_key=data_key,
        )
        total_fetch_ms += elapsed_ms
        if blob is not None:
            return current_ns_v, blob, total_fetch_ms, {
                'wait_sleep_ms': round(total_sleep_ms, 2),
                'wait_poll_count': poll_count,
                'wait_found_entry': True,
            }
    return current_ns_v, None, total_fetch_ms, {
        'wait_sleep_ms': round(total_sleep_ms, 2),
        'wait_poll_count': poll_count,
        'wait_found_entry': False,
    }


def get_cached_traffic_filter_response(
    *,
    payload: dict[str, Any],
    builder: Callable[[], Any],
    timeout: int = CACHE_TTL_FILTER,
    serve_mode: ServeMode = 'python',
) -> tuple[Any, dict[str, Any]]:
    """Get or build traffic_filter response; Redis stores gzip JSON bytes.

    ``serve_mode``:
      - ``python``: nested list/dicts (compat / tests)
      - ``gzip_bytes``: HTTP-ready gzip(JSON) without TFGZ1 (HIT or post-MISS encode)
      - ``json_bytes``: uncompressed JSON bytes (HIT gunzip, or render after MISS)
    """
    meta: dict[str, Any] = {
        'cache_hit': False,
        'cache_enabled': is_cache_usable(),
        'cache_backend': cache_backend_name().rsplit('.', 1)[-1],
        'cache_repr': TRAFFIC_FILTER_CACHE_SCOPE,
        'serve_mode': serve_mode,
        'response_path': 'python_object',
        'timeout': timeout,
        'redis_round_trips': 0,
    }

    if not is_cache_usable():
        start = time.perf_counter()
        value = builder()
        meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
        return _adapt_miss_value(
            value,
            serve_mode,
            meta,
            store=False,
            timeout=timeout,
            ns_v=1,
        )

    version_key = _version_key(CACHE_NS_TRAFFIC_FILTER)
    data_key, digest = build_traffic_filter_data_key(payload)
    meta['cache_key_suffix'] = digest[:12]
    meta['data_key_suffix'] = data_key[-12:]

    def _try_hit(cached_raw: bytes) -> tuple[Any, dict[str, Any]] | None:
        try:
            t_get_decode = time.perf_counter()
            value, response_path, timings = _hit_value_for_mode(cached_raw, serve_mode)
            meta.update(timings)
            meta['cache_hit'] = True
            meta['response_path'] = response_path
            if serve_mode == 'gzip_bytes':
                meta['redis_payload_bytes'] = len(cached_raw)
                meta['http_body_bytes'] = len(value)
            elif serve_mode == 'json_bytes':
                meta['http_body_bytes'] = len(value)
            meta['hit_adapt_ms'] = round((time.perf_counter() - t_get_decode) * 1000, 2)
            return value, meta
        except Exception:
            logger.warning(
                'traffic_filter cache decode failed key_suffix=%s; rebuilding',
                digest[:12],
                exc_info=True,
            )
            return None

    current_ns_v, cached_blob, fetch_ms = _fetch_entry(
        version_key=version_key,
        data_key=data_key,
    )
    meta['redis_get_ms'] = round(fetch_ms, 2)
    meta['redis_round_trips'] = 1
    meta['namespace_version'] = current_ns_v
    if cached_blob is not None:
        adapted = _try_hit(cached_blob)
        if adapted is not None:
            return adapted

    def _build_and_store(ns_v: int) -> tuple[Any, dict[str, Any]]:
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
            cache_key=data_key,
            digest=digest,
            ns_v=ns_v,
        )

    lock_diag: dict[str, Any] = {}
    acquired = _acquire_population_lock(CACHE_NS_TRAFFIC_FILTER, digest, diag=lock_diag)
    meta.update(lock_diag)
    if not acquired:
        current_ns_v, cached_blob, wait_ms, wait_diag = _wait_for_entry(
            version_key=version_key,
            data_key=data_key,
        )
        meta.update(wait_diag)
        meta['redis_get_ms'] = round(float(meta.get('redis_get_ms') or 0) + wait_ms, 2)
        meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 1) + int(
            wait_diag.get('wait_poll_count') or len(_LOCK_WAIT_SCHEDULE_SECONDS)
        )
        meta['namespace_version'] = current_ns_v
        if cached_blob is not None:
            adapted = _try_hit(cached_blob)
            if adapted is not None:
                return adapted
    else:
        meta.setdefault('wait_sleep_ms', 0)
        meta.setdefault('wait_poll_count', 0)
    try:
        current_ns_v, cached_blob, fetch_ms = _fetch_entry(
            version_key=version_key,
            data_key=data_key,
        )
        meta['redis_get_ms'] = round(float(meta.get('redis_get_ms') or 0) + fetch_ms, 2)
        meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 1) + 1
        meta['namespace_version'] = current_ns_v
        if cached_blob is not None:
            adapted = _try_hit(cached_blob)
            if adapted is not None:
                return adapted
        return _build_and_store(current_ns_v)
    finally:
        if acquired:
            _release_population_lock(CACHE_NS_TRAFFIC_FILTER, digest, diag=meta)


def _adapt_miss_value(
    value: Any,
    serve_mode: ServeMode,
    meta: dict[str, Any],
    *,
    store: bool,
    timeout: int,
    ns_v: int,
    cache_key: str | None = None,
    digest: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    encoded = None
    if store and cache_key is not None:
        try:
            t_enc = time.perf_counter()
            encoded = encode_traffic_filter_cache(value)
            entry = wrap_traffic_filter_entry(ns_v=ns_v, blob=encoded)
            meta['encode_ms'] = round((time.perf_counter() - t_enc) * 1000, 2)
            meta['namespace_version'] = ns_v
            set_timings: dict[str, Any] = {}
            set_ok = safe_cache_set(cache_key, entry, timeout=timeout, timings=set_timings)
            meta['cache_set_ok'] = set_ok
            meta['cache_set_ms'] = set_timings.get('cache_set_ms')
            meta['cache_set_result'] = set_timings.get('cache_set_result')
            meta['cache_set_raw_type'] = set_timings.get('cache_set_raw_type')
            if set_timings.get('cache_set_exception'):
                meta['cache_set_exception'] = set_timings['cache_set_exception']
            # Keep redis payload size after successful encode regardless of SET outcome.
            meta['redis_payload_bytes'] = len(encoded)
        except Exception:
            logger.warning(
                'traffic_filter cache encode/set failed key_suffix=%s',
                (digest or '')[:12],
                exc_info=True,
            )
            encoded = None

    if serve_mode == 'gzip_bytes':
        if encoded is None:
            encoded = encode_traffic_filter_cache(value)
        gzip_payload = extract_traffic_filter_gzip_payload(encoded)
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

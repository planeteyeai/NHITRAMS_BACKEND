"""Combined count+page cache lookup for distress_predict_filter (2 Redis RTTs).

Warm HIT path:
  1) namespace version GET (once)
  2) MGET count_key + page_key

Keeps DPGZ1 page format and separate count keys. Partial misses fall back to
existing builders with the shared version reused. Beyond-last-page requests
resolve count only and skip page rebuild.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from nhit.caching.constants import CACHE_NS_DISTRESS_PREDICT_FILTER, CACHE_TTL_FILTER
from nhit.caching.distress_predict_filter_count import (
    DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
    filter_payload_without_pagination,
    get_cached_distress_predict_filter_count,
)
from nhit.caching.distress_predict_filter_page import (
    DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    ServeMode,
    _hit_value_for_mode,
    get_cached_distress_predict_filter_page,
)
from nhit.caching.keys import build_cache_key
from nhit.caching.service import (
    cache_backend_name,
    get_namespace_version,
    is_cache_usable,
    safe_cache_get_many,
)

logger = logging.getLogger('nhit.cache')


def get_cached_distress_predict_filter_count_and_page(
    *,
    payload: dict[str, Any],
    count_builder: Callable[[], int],
    page_builder: Callable[[], Any],
    timeout: int = CACHE_TTL_FILTER,
    serve_mode: ServeMode = 'python',
    offset: int = 0,
) -> tuple[int, Any, dict[str, Any], dict[str, Any]]:
    """Return (total_count, page_body|None, count_meta, page_meta).

    Full HIT uses 2 Redis round trips (version + MGET). When ``offset >= total``,
    ``page_body`` is None and page rebuild is skipped.
    """
    count_meta: dict[str, Any] = {
        'cache_hit': False,
        'cache_enabled': is_cache_usable(),
        'cache_backend': cache_backend_name().rsplit('.', 1)[-1],
        'redis_round_trips': 0,
    }
    page_meta: dict[str, Any] = {
        'cache_hit': False,
        'cache_enabled': is_cache_usable(),
        'cache_backend': cache_backend_name().rsplit('.', 1)[-1],
        'cache_repr': DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
        'serve_mode': serve_mode,
        'response_path': 'python_object',
        'redis_round_trips': 0,
    }

    if not is_cache_usable():
        total = int(count_builder())
        count_meta['lookup_path'] = 'cache_disabled'
        if offset >= total:
            return total, None, count_meta, page_meta
        page_body, page_meta = get_cached_distress_predict_filter_page(
            payload=payload,
            builder=page_builder,
            timeout=timeout,
            serve_mode=serve_mode,
        )
        return total, page_body, count_meta, page_meta

    t_ver0 = time.perf_counter()
    version = get_namespace_version(CACHE_NS_DISTRESS_PREDICT_FILTER)
    version_ms = round((time.perf_counter() - t_ver0) * 1000, 2)
    count_meta['version_get_ms'] = version_ms
    page_meta['version_get_ms'] = version_ms
    page_meta['version_reused'] = True
    count_meta['redis_round_trips'] = 1
    page_meta['redis_round_trips'] = 1

    filter_payload = filter_payload_without_pagination(payload)
    count_key = build_cache_key(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        version=version,
        payload=filter_payload,
        scope=DISTRESS_PREDICT_FILTER_COUNT_SCOPE,
    )
    page_key = build_cache_key(
        namespace=CACHE_NS_DISTRESS_PREDICT_FILTER,
        version=version,
        payload=payload,
        scope=DISTRESS_PREDICT_FILTER_PAGE_SCOPE,
    )
    page_digest = page_key.rsplit(':', 1)[-1]
    page_meta['cache_key_suffix'] = page_digest[:12]
    count_meta['cache_key_suffix'] = count_key.rsplit(':', 1)[-1][:12]

    mget_timings: dict[str, Any] = {}
    t_mget0 = time.perf_counter()
    many = safe_cache_get_many([count_key, page_key], timings=mget_timings)
    mget_ms = round((time.perf_counter() - t_mget0) * 1000, 2)
    count_meta['data_get_ms'] = mget_ms
    count_meta['mget_ms'] = mget_ms
    page_meta['redis_get_ms'] = mget_ms
    page_meta['page_data_get_ms'] = mget_ms
    page_meta['mget_ms'] = mget_ms
    count_meta['redis_round_trips'] = int(count_meta['redis_round_trips']) + 1
    page_meta['redis_round_trips'] = int(page_meta['redis_round_trips']) + 1

    count_raw = many.get(count_key)
    page_raw = many.get(page_key)
    count_present = count_key in many and count_raw is not None
    page_present = page_key in many and page_raw is not None

    total_count: int | None = None
    if count_present:
        try:
            total_count = int(count_raw)
            count_meta['cache_hit'] = True
        except (TypeError, ValueError):
            logger.warning(
                'distress_predict_filter count cache corrupt key_suffix=%s; rebuilding',
                count_meta['cache_key_suffix'],
            )
            count_present = False
            total_count = None

    if not count_present or total_count is None:
        total_count, rebuilt_count_meta = get_cached_distress_predict_filter_count(
            payload=payload,
            builder=count_builder,
            timeout=timeout,
            version=version,
        )
        count_meta['cache_hit'] = False
        count_meta['lookup_path'] = 'mget_partial_rebuild_count'
        count_meta['build_ms'] = rebuilt_count_meta.get('build_ms')
        count_meta['redis_round_trips'] = int(count_meta.get('redis_round_trips') or 0) + int(
            rebuilt_count_meta.get('redis_round_trips') or 0
        )
    else:
        count_meta['lookup_path'] = 'mget_hit'

    if offset >= int(total_count):
        page_meta['lookup_path'] = 'skipped_beyond_last_page'
        return int(total_count), None, count_meta, page_meta

    page_body = None
    if page_present:
        try:
            t_adapt = time.perf_counter()
            value, response_path, timings = _hit_value_for_mode(page_raw, serve_mode)
            page_meta.update(timings)
            page_meta['cache_hit'] = True
            page_meta['response_path'] = response_path
            if serve_mode == 'gzip_bytes':
                page_meta['redis_payload_bytes'] = (
                    len(page_raw) if isinstance(page_raw, (bytes, bytearray)) else None
                )
                page_meta['http_body_bytes'] = len(value)
            elif serve_mode == 'json_bytes':
                page_meta['http_body_bytes'] = len(value)
            page_meta['hit_adapt_ms'] = round((time.perf_counter() - t_adapt) * 1000, 2)
            page_body = value
            page_meta['lookup_path'] = 'mget_hit'
        except Exception:
            logger.warning(
                'distress_predict_filter page cache decode failed key_suffix=%s; rebuilding',
                page_digest[:12],
                exc_info=True,
            )
            page_present = False
            page_body = None
            page_meta['cache_hit'] = False

    if page_body is None:
        page_body, rebuilt_page_meta = get_cached_distress_predict_filter_page(
            payload=payload,
            builder=page_builder,
            timeout=timeout,
            serve_mode=serve_mode,
            version=version,
        )
        for key, value in rebuilt_page_meta.items():
            if key == 'version_get_ms':
                continue
            page_meta[key] = value
        page_meta['version_get_ms'] = version_ms
        page_meta['version_reused'] = True
        page_meta['lookup_path'] = 'mget_partial_rebuild_page'
        page_meta['redis_round_trips'] = int(page_meta.get('redis_round_trips') or 0) + int(
            rebuilt_page_meta.get('redis_round_trips') or 0
        )

    return int(total_count), page_body, count_meta, page_meta

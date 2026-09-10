"""Safe cache get/set helpers with versioned namespaces."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache

from nhit.caching.constants import (
    CACHE_LOCK_TIMEOUT_SECONDS,
    CACHE_LOCK_WAIT_SCHEDULE_SECONDS,
)
from nhit.caching.keys import build_cache_key

logger = logging.getLogger('nhit.cache')

_VERSION_SUFFIX = ':version'
_LOCK_SUFFIX = ':lock'


def cache_backend_name() -> str:
    return settings.CACHES.get('default', {}).get('BACKEND', '')


def is_redis_configured() -> bool:
    return 'redis' in cache_backend_name().lower()


def is_cache_usable() -> bool:
    return getattr(settings, 'NHIT_CACHE_ENABLED', False)


def _version_key(namespace: str) -> str:
    return f'{namespace}{_VERSION_SUFFIX}'


def _lock_key(namespace: str, digest: str) -> str:
    return f'{namespace}{_LOCK_SUFFIX}:{digest}'


def get_namespace_version(namespace: str) -> int:
    if not is_cache_usable():
        return 1
    try:
        version = cache.get(_version_key(namespace))
    except Exception:
        logger.warning('cache version lookup failed namespace=%s', namespace, exc_info=True)
        return 1
    if version is None:
        return 1
    try:
        return int(version)
    except (TypeError, ValueError):
        return 1


def bump_namespace_version(namespace: str) -> int:
    """Increment a namespace version so existing keys are ignored."""
    if not is_cache_usable():
        return 1
    version_key = _version_key(namespace)
    try:
        current = get_namespace_version(namespace)
        new_version = current + 1
        cache.set(version_key, new_version, timeout=None)
        logger.info('cache version bumped namespace=%s version=%s', namespace, new_version)
        return new_version
    except Exception:
        logger.warning('cache version bump failed namespace=%s', namespace, exc_info=True)
        return get_namespace_version(namespace)


def safe_cache_get(
    key: str,
    *,
    timings: dict[str, Any] | None = None,
) -> tuple[bool, Any | None]:
    if not is_cache_usable():
        return False, None
    try:
        start = time.perf_counter()
        value = cache.get(key)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if timings is not None:
            # django-redis unpickles inside cache.get(); decode is not separable.
            timings['cache_get_ms'] = round(
                float(timings.get('cache_get_ms') or 0) + elapsed_ms,
                2,
            )
        if value is None:
            logger.debug('cache MISS key_suffix=%s lookup_ms=%.2f', key[-12:], elapsed_ms)
            return False, None
        logger.debug('cache HIT key_suffix=%s lookup_ms=%.2f', key[-12:], elapsed_ms)
        return True, value
    except Exception:
        logger.warning('cache get failed key_suffix=%s', key[-12:], exc_info=True)
        return False, None


def safe_cache_get_many(keys: list[str]) -> dict[str, Any]:
    """Fetch multiple keys; backends that support it use one Redis round trip."""
    if not is_cache_usable() or not keys:
        return {}
    try:
        return cache.get_many(keys) or {}
    except Exception:
        logger.warning('cache get_many failed count=%s', len(keys), exc_info=True)
        return {}


def _cache_op_result_token(result: Any) -> str:
    """Normalize cache backend return values for diagnostic logs (no secrets)."""
    if result is None:
        return 'none'
    if result is True:
        return 'true'
    if result is False:
        return 'false'
    return type(result).__name__


def safe_cache_set(
    key: str,
    value: Any,
    *,
    timeout: int,
    timings: dict[str, Any] | None = None,
) -> bool:
    if not is_cache_usable():
        if timings is not None:
            timings['cache_set_result'] = 'skipped_cache_disabled'
            timings['cache_set_ms'] = 0
        return False
    try:
        start = time.perf_counter()
        result = cache.set(key, value, timeout=timeout)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if timings is not None:
            timings['cache_write_ms'] = round(
                float(timings.get('cache_write_ms') or 0) + elapsed_ms,
                2,
            )
            # Explicit Redis SET diagnostics (distinct from encode_ms).
            timings['cache_set_ms'] = round(
                float(timings.get('cache_set_ms') or 0) + elapsed_ms,
                2,
            )
            timings['cache_set_result'] = _cache_op_result_token(result)
            timings['cache_set_raw_type'] = type(result).__name__
        # django-redis + IGNORE_EXCEPTIONS returns None on connection failure.
        # Other backends may return None/True; only treat Redis None as failure.
        if result is None and is_redis_configured():
            logger.warning('cache set returned None key_suffix=%s', key[-12:])
            return False
        if result is False:
            logger.warning('cache set returned False key_suffix=%s', key[-12:])
            return False
        logger.debug('cache SET key_suffix=%s ttl=%s', key[-12:], timeout)
        return True
    except Exception as exc:
        if timings is not None:
            timings['cache_set_result'] = 'exception'
            timings['cache_set_exception'] = type(exc).__name__
        logger.warning('cache set failed key_suffix=%s', key[-12:], exc_info=True)
        return False


def _acquire_population_lock(
    namespace: str,
    digest: str,
    *,
    diag: dict[str, Any] | None = None,
) -> bool:
    lock_key = _lock_key(namespace, digest)
    try:
        start = time.perf_counter()
        result = cache.add(lock_key, '1', timeout=CACHE_LOCK_TIMEOUT_SECONDS)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if diag is not None:
            diag['lock_add_result'] = _cache_op_result_token(result)
            diag['lock_add_raw_type'] = type(result).__name__
            diag['lock_add_ms'] = round(elapsed_ms, 2)
            # None (IGNORE_EXCEPTIONS swallow) and False both mean "not acquired".
            diag['lock_acquired'] = bool(result)
        # Preserve historical behavior: only an explicit True-ish add wins the lock.
        return bool(result)
    except Exception as exc:
        logger.warning('cache lock acquire failed namespace=%s', namespace, exc_info=True)
        if diag is not None:
            diag['lock_add_result'] = 'exception'
            diag['lock_add_exception'] = type(exc).__name__
            # Historical behavior on exception: proceed as builder (acquired=True).
            diag['lock_acquired'] = True
        return True


def _release_population_lock(
    namespace: str,
    digest: str,
    *,
    diag: dict[str, Any] | None = None,
) -> None:
    lock_key = _lock_key(namespace, digest)
    try:
        start = time.perf_counter()
        result = cache.delete(lock_key)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if diag is not None:
            diag['cache_delete_result'] = _cache_op_result_token(result)
            diag['cache_delete_raw_type'] = type(result).__name__
            diag['cache_delete_ms'] = round(elapsed_ms, 2)
    except Exception as exc:
        if diag is not None:
            diag['cache_delete_result'] = 'exception'
            diag['cache_delete_exception'] = type(exc).__name__
        logger.warning('cache lock release failed namespace=%s', namespace, exc_info=True)


def _wait_for_cached_value(
    cache_key: str,
    *,
    timings: dict[str, Any] | None = None,
) -> tuple[bool, Any | None]:
    """Poll with bounded backoff until the lock holder populates the key or wait expires."""
    for delay in CACHE_LOCK_WAIT_SCHEDULE_SECONDS:
        time.sleep(delay)
        hit, cached_value = safe_cache_get(cache_key, timings=timings)
        if hit:
            return True, cached_value
    return False, None


def get_or_set_cached_value(
    *,
    namespace: str,
    payload: Any,
    builder: Callable[[], Any],
    timeout: int,
    scope: str = '',
    use_lock: bool = False,
    timings: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return cached value or build, cache, and return a fresh value.

    When *timings* is provided, cache GET/SET durations are accumulated into it.
    Default callers pass nothing — overhead is a single None check per operation.
    """
    meta: dict[str, Any] = {
        'cache_hit': False,
        'cache_enabled': is_cache_usable(),
        'cache_backend': cache_backend_name().rsplit('.', 1)[-1],
        'redis_round_trips': 0,
    }

    if not is_cache_usable():
        start = time.perf_counter()
        value = builder()
        meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
        return value, meta

    t_ver0 = time.perf_counter()
    version = get_namespace_version(namespace)
    meta['version_get_ms'] = round((time.perf_counter() - t_ver0) * 1000, 2)
    meta['redis_round_trips'] = 1
    cache_key = build_cache_key(namespace=namespace, version=version, payload=payload, scope=scope)
    digest = cache_key.rsplit(':', 1)[-1]
    meta['cache_key_suffix'] = digest[:12]

    hit_timings: dict[str, Any] = {}
    hit, cached_value = safe_cache_get(cache_key, timings=hit_timings)
    meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 0) + 1
    meta['data_get_ms'] = hit_timings.get('cache_get_ms')
    if timings is not None and hit_timings.get('cache_get_ms') is not None:
        timings['cache_get_ms'] = round(
            float(timings.get('cache_get_ms') or 0) + float(hit_timings['cache_get_ms']),
            2,
        )
    if hit:
        meta['cache_hit'] = True
        return cached_value, meta

    if use_lock:
        acquired = _acquire_population_lock(namespace, digest)
        if not acquired:
            hit, cached_value = _wait_for_cached_value(cache_key, timings=timings)
            if hit:
                meta['cache_hit'] = True
                return cached_value, meta
        try:
            hit, cached_value = safe_cache_get(cache_key, timings=timings)
            meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 0) + 1
            if hit:
                meta['cache_hit'] = True
                return cached_value, meta
            start = time.perf_counter()
            value = builder()
            meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
            safe_cache_set(cache_key, value, timeout=timeout, timings=timings)
            meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 0) + 1
            return value, meta
        finally:
            if acquired:
                _release_population_lock(namespace, digest)

    start = time.perf_counter()
    value = builder()
    meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
    safe_cache_set(cache_key, value, timeout=timeout, timings=timings)
    meta['redis_round_trips'] = int(meta.get('redis_round_trips') or 0) + 1
    return value, meta

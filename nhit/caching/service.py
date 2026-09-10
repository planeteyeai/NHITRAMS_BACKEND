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


def safe_cache_set(
    key: str,
    value: Any,
    *,
    timeout: int,
    timings: dict[str, Any] | None = None,
) -> bool:
    if not is_cache_usable():
        return False
    try:
        start = time.perf_counter()
        result = cache.set(key, value, timeout=timeout)
        if timings is not None:
            timings['cache_write_ms'] = round(
                float(timings.get('cache_write_ms') or 0)
                + (time.perf_counter() - start) * 1000,
                2,
            )
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
    except Exception:
        logger.warning('cache set failed key_suffix=%s', key[-12:], exc_info=True)
        return False


def _acquire_population_lock(namespace: str, digest: str) -> bool:
    lock_key = _lock_key(namespace, digest)
    try:
        return cache.add(lock_key, '1', timeout=CACHE_LOCK_TIMEOUT_SECONDS)
    except Exception:
        logger.warning('cache lock acquire failed namespace=%s', namespace, exc_info=True)
        return True


def _release_population_lock(namespace: str, digest: str) -> None:
    lock_key = _lock_key(namespace, digest)
    try:
        cache.delete(lock_key)
    except Exception:
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
    }

    if not is_cache_usable():
        start = time.perf_counter()
        value = builder()
        meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
        return value, meta

    version = get_namespace_version(namespace)
    cache_key = build_cache_key(namespace=namespace, version=version, payload=payload, scope=scope)
    digest = cache_key.rsplit(':', 1)[-1]
    meta['cache_key_suffix'] = digest[:12]

    hit, cached_value = safe_cache_get(cache_key, timings=timings)
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
            if hit:
                meta['cache_hit'] = True
                return cached_value, meta
            start = time.perf_counter()
            value = builder()
            meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
            safe_cache_set(cache_key, value, timeout=timeout, timings=timings)
            return value, meta
        finally:
            if acquired:
                _release_population_lock(namespace, digest)

    start = time.perf_counter()
    value = builder()
    meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
    safe_cache_set(cache_key, value, timeout=timeout, timings=timings)
    return value, meta

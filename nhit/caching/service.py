"""Safe cache get/set helpers with versioned namespaces."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from django.conf import settings
from django.core.cache import cache

from nhit.caching.constants import (
    CACHE_LOCK_MAX_RETRIES,
    CACHE_LOCK_TIMEOUT_SECONDS,
    CACHE_LOCK_WAIT_SECONDS,
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


def safe_cache_get(key: str) -> tuple[bool, Any | None]:
    if not is_cache_usable():
        return False, None
    try:
        start = time.perf_counter()
        value = cache.get(key)
        elapsed_ms = (time.perf_counter() - start) * 1000
        if value is None:
            logger.debug('cache MISS key_suffix=%s lookup_ms=%.2f', key[-12:], elapsed_ms)
            return False, None
        logger.debug('cache HIT key_suffix=%s lookup_ms=%.2f', key[-12:], elapsed_ms)
        return True, value
    except Exception:
        logger.warning('cache get failed key_suffix=%s', key[-12:], exc_info=True)
        return False, None


def safe_cache_set(key: str, value: Any, *, timeout: int) -> bool:
    if not is_cache_usable():
        return False
    try:
        cache.set(key, value, timeout=timeout)
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


def get_or_set_cached_value(
    *,
    namespace: str,
    payload: Any,
    builder: Callable[[], Any],
    timeout: int,
    scope: str = '',
    use_lock: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """Return cached value or build, cache, and return a fresh value."""
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

    hit, cached_value = safe_cache_get(cache_key)
    if hit:
        meta['cache_hit'] = True
        return cached_value, meta

    if use_lock:
        acquired = _acquire_population_lock(namespace, digest)
        if not acquired:
            for _ in range(CACHE_LOCK_MAX_RETRIES):
                time.sleep(CACHE_LOCK_WAIT_SECONDS)
                hit, cached_value = safe_cache_get(cache_key)
                if hit:
                    meta['cache_hit'] = True
                    return cached_value, meta
        try:
            hit, cached_value = safe_cache_get(cache_key)
            if hit:
                meta['cache_hit'] = True
                return cached_value, meta
            start = time.perf_counter()
            value = builder()
            meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
            safe_cache_set(cache_key, value, timeout=timeout)
            return value, meta
        finally:
            if acquired:
                _release_population_lock(namespace, digest)

    start = time.perf_counter()
    value = builder()
    meta['build_ms'] = round((time.perf_counter() - start) * 1000, 2)
    safe_cache_set(cache_key, value, timeout=timeout)
    return value, meta

"""View helpers for transparent response caching."""

from __future__ import annotations

import logging
from typing import Any, Callable

from rest_framework import status
from rest_framework.response import Response

from nhit.caching.constants import CACHE_TTL_FILTER, CACHE_TTL_LIST, CACHE_TTL_MASTER
from nhit.caching.service import get_or_set_cached_value

logger = logging.getLogger('nhit.cache')


def cached_filter_response(
    *,
    namespace: str,
    payload: Any,
    builder: Callable[[], Any],
    scope: str = '',
    timeout: int = CACHE_TTL_FILTER,
) -> Response:
    """Cache successful filter/report POST responses."""
    data, meta = get_or_set_cached_value(
        namespace=namespace,
        payload=payload,
        builder=builder,
        timeout=timeout,
        scope=scope,
        use_lock=True,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            'cached_filter_response namespace=%s hit=%s build_ms=%s',
            namespace,
            meta.get('cache_hit'),
            meta.get('build_ms'),
        )
    return Response(data, status=status.HTTP_200_OK)


def cached_get_response(
    *,
    namespace: str,
    payload: Any,
    builder: Callable[[], Any],
    scope: str = '',
    timeout: int = CACHE_TTL_MASTER,
) -> Response:
    """Cache successful GET master/list responses."""
    data, meta = get_or_set_cached_value(
        namespace=namespace,
        payload=payload,
        builder=builder,
        timeout=timeout,
        scope=scope,
        use_lock=False,
    )
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            'cached_get_response namespace=%s hit=%s build_ms=%s',
            namespace,
            meta.get('cache_hit'),
            meta.get('build_ms'),
        )
    return Response(data, status=status.HTTP_200_OK)

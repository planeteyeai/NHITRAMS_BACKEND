"""Resolve Redis connection URL from environment (no secrets logged)."""

from __future__ import annotations


def resolve_redis_url(
    *,
    redis_internal_url: str | None = None,
    redis_url: str | None = None,
    use_internal: bool = False,
) -> str:
    """Resolve the Redis URL for django-redis.

    - On Railway (``use_internal=True``): prefer ``REDIS_INTERNAL_URL``, then ``REDIS_URL``.
    - Local/dev (``use_internal=False``): prefer ``REDIS_URL`` so public proxy works even when
      ``REDIS_INTERNAL_URL`` is present in ``.env`` (internal DNS is unreachable off Railway).
    - Never logs URLs or credentials. Empty/whitespace values are treated as unset.
    """
    internal = (redis_internal_url or '').strip()
    public = (redis_url or '').strip()
    if use_internal:
        if internal:
            return internal
        return public
    if public:
        return public
    return internal

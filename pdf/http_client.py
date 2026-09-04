"""HTTP helpers for PDF image downloads with optional connection reuse.

Uses urllib3 when available (already pulled in by boto3) so same-host S3
URLs can reuse keep-alive connections across concurrent workers. Falls back
to urllib.request when urllib3 is unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

USER_AGENT = 'NHIT-AI-Identifier-PDF/1.0'


class ImageHttpClient:
    """Thread-safe HTTP GET for image bytes with connection pooling when possible."""

    def __init__(self, *, timeout_seconds: float, max_bytes: int, maxsize: int = 4):
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self._local = threading.local()
        self._pool = None
        try:
            import urllib3

            self._pool = urllib3.PoolManager(
                num_pools=max(4, maxsize),
                maxsize=max(1, maxsize),
                timeout=urllib3.Timeout(
                    connect=self.timeout_seconds,
                    read=self.timeout_seconds,
                ),
                retries=False,
                headers={'User-Agent': USER_AGENT},
            )
        except Exception as exc:
            logger.info('urllib3 PoolManager unavailable (%s); using urllib fallback', exc)

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        """Return (body_bytes, content_type). Raises on transport errors."""
        if self._pool is not None:
            return self._get_urllib3(url)
        return self._get_urllib(url)

    def _get_urllib3(self, url: str) -> tuple[bytes, str]:
        response = self._pool.request(
            'GET',
            url,
            preload_content=False,
            redirect=True,
        )
        try:
            if response.status >= 400:
                raise OSError(f'HTTP {response.status}')
            content_type = (response.headers.get('Content-Type') or '').lower()
            chunks: list[bytes] = []
            total = 0
            while True:
                piece = response.read(256 * 1024)
                if not piece:
                    break
                total += len(piece)
                if total > self.max_bytes:
                    raise OSError('Image too large')
                chunks.append(piece)
            return b''.join(chunks), content_type
        finally:
            response.release_conn()

    def _get_urllib(self, url: str) -> tuple[bytes, str]:
        import urllib.request

        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = (response.headers.get('Content-Type') or '').lower()
            data = response.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise OSError('Image too large')
        return data, content_type

    def hosts_hint(self, urls: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for raw in urls:
            host = urlsplit(raw).netloc.lower()
            if host:
                counts[host] = counts.get(host, 0) + 1
        return counts

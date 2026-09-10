"""GZip middleware that skips binary downloads (PDF, zip, media)."""

from __future__ import annotations

import time

from django.middleware.gzip import GZipMiddleware

from traffic.services.traffic_filter_perf import (
    REQUEST_PERF_ATTR,
    log_traffic_filter_performance,
)


class SelectiveGZipMiddleware(GZipMiddleware):
    """Same as Django GZipMiddleware, but never compress PDF/binary attachments.

    Compressing ``application/pdf`` FileResponses breaks many browser download
    flows (fetch → blob → save) that persist the gzip payload as a ``.pdf``.

    When temporary ``TRAFFIC_FILTER_PERF_DEBUG`` instrumentation attached a
    ``traffic_filter_perf`` context on the request, record gzip duration and
    emit the structured TRAFFIC_FILTER_PERF log after compression. Disabled
    requests only pay for a single ``getattr``.
    """

    _SKIP_EXACT = frozenset({
        'application/pdf',
        'application/zip',
        'application/octet-stream',
        'application/gzip',
        'application/x-gzip',
    })
    _SKIP_PREFIXES = (
        'image/',
        'video/',
        'audio/',
    )

    def process_response(self, request, response):
        content_type = (response.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type in self._SKIP_EXACT:
            self._maybe_finalize_traffic_filter_perf(request, response, gzip_ms=0)
            return response
        if content_type.startswith(self._SKIP_PREFIXES):
            self._maybe_finalize_traffic_filter_perf(request, response, gzip_ms=0)
            return response
        disposition = (response.get('Content-Disposition') or '').lower()
        if 'attachment' in disposition and content_type.startswith('application/'):
            self._maybe_finalize_traffic_filter_perf(request, response, gzip_ms=0)
            return response

        perf = getattr(request, REQUEST_PERF_ATTR, None)
        if perf is None:
            return super().process_response(request, response)

        # Materialize DRF Response.content before the gzip timer so gzip_ms
        # does not include first-time JSON rendering.
        if hasattr(response, 'is_rendered') and not response.is_rendered:
            response.render()

        t_gzip0 = time.perf_counter()
        result = super().process_response(request, response)
        gzip_ms = round((time.perf_counter() - t_gzip0) * 1000, 2)
        self._maybe_finalize_traffic_filter_perf(request, result, gzip_ms=gzip_ms)
        return result

    @staticmethod
    def _maybe_finalize_traffic_filter_perf(request, response, *, gzip_ms: float) -> None:
        perf = getattr(request, REQUEST_PERF_ATTR, None)
        if not isinstance(perf, dict) or perf.get('logged'):
            return

        stage = dict(perf.get('stage_timings') or {})
        encoding = (response.get('Content-Encoding') or '').strip().lower() or None
        gzip_bytes = None
        try:
            content = getattr(response, 'content', None)
            if content is not None and encoding == 'gzip':
                gzip_bytes = len(content)
        except Exception:
            gzip_bytes = None

        t0 = perf.get('t0')
        server_total_ms = None
        if t0 is not None:
            server_total_ms = round((time.perf_counter() - float(t0)) * 1000, 2)

        fields = {
            'request_id': perf.get('request_id'),
            'cache': perf.get('cache'),
            'rows': perf.get('rows'),
            'groups': perf.get('groups'),
            'response_bytes': perf.get('response_bytes'),
            'gzip_bytes': gzip_bytes,
            'content_encoding': encoding or 'identity',
            'cache_get_ms': stage.get('cache_get_ms'),
            'cache_decode_ms': stage.get('cache_decode_ms'),
            'cache_lookup_ms': stage.get('cache_lookup_ms'),
            'cache_write_ms': stage.get('cache_write_ms'),
            'db_ms': stage.get('db_ms'),
            'db_query_count': stage.get('db_query_count'),
            'db_sql_ms': stage.get('db_sql_ms'),
            'db_slowest_ms': stage.get('db_slowest_ms'),
            'python_ms': stage.get('python_ms'),
            'serialize_ms': stage.get('serialize_ms'),
            'group_ms': stage.get('group_ms'),
            'json_ms': stage.get('json_ms'),
            'gzip_ms': gzip_ms,
            'server_total_ms': server_total_ms,
            'note': 'cache_decode_included_in_cache_get',
        }
        log_traffic_filter_performance(fields)
        perf['logged'] = True

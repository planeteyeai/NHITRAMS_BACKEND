"""Temporary Phase 4A timing middleware — not registered in production settings.

Activated only when a benchmark process inserts this class into MIDDLEWARE
via override_settings / runtime list mutation. When inactive (no marker header),
it is a single META check + passthrough.
"""

from __future__ import annotations

import time

MARKER_HEADER = 'HTTP_X_NHIT_PHASE4A'
REQUEST_ATTR = '_nhit_phase4a_timings'


class Phase4ATimingMiddleware:
    """Outer timing wrapper for end-to-end stage measurement (benchmark only)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.META.get(MARKER_HEADER):
            return self.get_response(request)

        timings: dict = {
            't0': time.perf_counter(),
        }
        setattr(request, REQUEST_ATTR, timings)

        t_before0 = time.perf_counter()
        # Remaining middleware + view happen inside get_response.
        # We approximate "before view" by marking at entry; view itself stamps
        # finer stages when the marker is present (see benchmark monkeypatch).
        timings['middleware_enter_ms'] = 0.0
        response = self.get_response(request)
        timings['server_handler_ms'] = round((time.perf_counter() - t_before0) * 1000, 3)
        timings['server_total_ms'] = round((time.perf_counter() - timings['t0']) * 1000, 3)

        # Expose timings on response for the test client (never for real clients
        # unless they send the private marker header in a benchmark process).
        response[f'X-NHIT-Phase4A-Server-Total-Ms'] = str(timings.get('server_total_ms'))
        if 'view_total_ms' in timings:
            response['X-NHIT-Phase4A-View-Total-Ms'] = str(timings['view_total_ms'])
        if 'redis_get_ms' in timings:
            response['X-NHIT-Phase4A-Redis-Get-Ms'] = str(timings['redis_get_ms'])
        if 'cache_hit' in timings:
            response['X-NHIT-Phase4A-Cache'] = 'HIT' if timings['cache_hit'] else 'MISS'
        if 'response_path' in timings:
            response['X-NHIT-Phase4A-Path'] = str(timings['response_path'])
        if 'db_queries' in timings:
            response['X-NHIT-Phase4A-Db-Queries'] = str(timings['db_queries'])

        # Attach full dict for in-process readers.
        response._nhit_phase4a_timings = timings  # type: ignore[attr-defined]
        return response

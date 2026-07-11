"""Lightweight health check used by Docker / load balancers."""

from django.db import connection
from django.http import JsonResponse
from django.views import View


class HealthCheckView(View):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        db_ok = False
        try:
            connection.ensure_connection()
            db_ok = True
        except Exception:
            db_ok = False

        status_code = 200 if db_ok else 503
        return JsonResponse(
            {
                'status': 'ok' if db_ok else 'degraded',
                'database': 'up' if db_ok else 'down',
            },
            status=status_code,
        )

"""Phase 4 Redis/network investigation benchmark (measurement only)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Phase 4: measure Redis GET/network latency for inventory_filter cache '
        '(no production behavior changes). Prefer running on Railway.'
    )

    def handle(self, *args, **options):
        from tools.benchmark_inventory_filter_phase4_redis_network import main

        raise SystemExit(main())

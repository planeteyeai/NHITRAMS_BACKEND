"""Measurement-only Redis payload/RTT benchmark for inventory_filter analysis."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Benchmark Redis GET latency vs payload size / pipeline / MGET '
        '(run inside Railway; does not change inventory_filter)'
    )

    def handle(self, *args, **options):
        from tools.benchmark_inventory_filter_redis_payload_rtt import main

        raise SystemExit(main())

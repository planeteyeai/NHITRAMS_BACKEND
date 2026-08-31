"""Run Railway internal Redis benchmark from manage.py."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Benchmark filter APIs: Redis OFF vs MISS vs HIT (run on Railway)'

    def handle(self, *args, **options):
        from tools.benchmark_railway_internal_redis import main

        raise SystemExit(main())

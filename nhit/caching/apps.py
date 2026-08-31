from django.apps import AppConfig


class NhitCachingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nhit.caching'
    verbose_name = 'NHIT caching'

    def ready(self) -> None:
        from nhit.caching.signals import connect_cache_invalidation_signals

        connect_cache_invalidation_signals()

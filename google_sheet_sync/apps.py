from django.apps import AppConfig


class GoogleSheetSyncConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'google_sheet_sync'
    verbose_name = 'Google Sheet Sync'

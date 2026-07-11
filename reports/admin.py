from django.contrib import admin

from .models import AIReport


@admin.register(AIReport)
class AIReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'prediction', 'confidence', 'created_at')
    list_filter = ('username', 'prediction')
    search_fields = ('username', 'prediction', 'image_path')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)

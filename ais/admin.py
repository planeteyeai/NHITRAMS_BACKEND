from django.contrib import admin
from .models import Accident


@admin.register(Accident)
class AccidentAdmin(admin.ModelAdmin):
    list_display = ('accident_id', 'project', 'segment', 'accident_date', 'nature_of_accident')
    search_fields = ('nature_of_accident', 'cause_of_accident')
    list_filter = ('accident_date',)

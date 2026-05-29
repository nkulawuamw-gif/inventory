from django.contrib import admin
from .models import ExportArchive, ImportLog


@admin.register(ExportArchive)
class ExportArchiveAdmin(admin.ModelAdmin):
    list_display = ['filename', 'filesize', 'created_by', 'created_at']
    readonly_fields = ['created_at']


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ['mode', 'status', 'created_by', 'created_at', 'completed_at']
    list_filter = ['mode', 'status']
    readonly_fields = ['created_at', 'completed_at']

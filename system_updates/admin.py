from django.contrib import admin
from .models import SystemVersion, UpdateRecord


@admin.register(SystemVersion)
class SystemVersionAdmin(admin.ModelAdmin):
    list_display = ['version', 'release_date', 'is_current', 'is_available', 'created_at']
    list_filter = ['is_current', 'is_available']
    search_fields = ['version']


@admin.register(UpdateRecord)
class UpdateRecordAdmin(admin.ModelAdmin):
    list_display = ['version_from', 'version_to', 'status', 'installed_by', 'created_at', 'completed_at']
    list_filter = ['status']
    readonly_fields = ['created_at', 'completed_at']

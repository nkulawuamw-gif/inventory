from django.contrib import admin

from .models import AuditLog, LoginSession, DailyAuditSummary


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'action', 'module', 'object_type',
        'object_id', 'created_at'
    ]
    list_filter = ['action', 'module', 'created_at']
    search_fields = [
        'user__username', 'description',
        'object_type', 'object_id'
    ]
    readonly_fields = ['created_at']
    date_hierarchy = 'created_at'


@admin.register(LoginSession)
class LoginSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'login_time', 'logout_time', 'ip_address']
    list_filter = ['login_time']
    search_fields = ['user__username']
    readonly_fields = ['login_time']


@admin.register(DailyAuditSummary)
class DailyAuditSummaryAdmin(admin.ModelAdmin):
    list_display = [
        'date', 'total_sales', 'sales_count',
        'refunds_count', 'stock_adjustments'
    ]
    list_filter = ['date']
    date_hierarchy = 'date'

import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.db.utils import OperationalError, ProgrammingError

from .models import AuditLog, LoginSession, DailyAuditSummary

logger = logging.getLogger(__name__)


@login_required
def audit_logs(request):
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        logs = AuditLog.objects.select_related('user').order_by('-created_at')[:100]
        today_audit = AuditLog.objects.filter(created_at__gte=today_start)
        login_sessions = LoginSession.objects.select_related('user').order_by('-login_time')[:50]

        today_summary = {
            'sales': today_audit.filter(action='SALE').count(),
            'refunds': today_audit.filter(action='REFUND').count(),
            'voids': today_audit.filter(action='VOID').count(),
            'adjustments': today_audit.filter(action='ADJUSTMENT').count(),
            'price_changes': today_audit.filter(action='PRICE_CHANGE').count(),
            'transfers': today_audit.filter(action='TRANSFER').count(),
            'logins': today_audit.filter(action='LOGIN').count(),
        }

        action_filter = request.GET.get('action', '')
        module_filter = request.GET.get('module', '')

        if action_filter:
            logs = logs.filter(action=action_filter)
        if module_filter:
            logs = logs.filter(module__icontains=module_filter)

    except (OperationalError, ProgrammingError) as e:
        logger.warning("Audit database not ready: %s", e)
        return render(request, 'audit/audit_logs.html', {
            'logs': [],
            'login_sessions': [],
            'today_summary': {},
            'action_filter': '',
            'module_filter': '',
            'action_choices': [],
            'error': 'Audit database tables not found. Run migrations on the server.',
            'page_title': 'Audit Log',
        })

    context = {
        'logs': logs,
        'login_sessions': login_sessions,
        'today_summary': today_summary,
        'action_filter': action_filter,
        'module_filter': module_filter,
        'action_choices': AuditLog.ACTION_CHOICES,
        'page_title': 'Audit Log',
    }
    return render(request, 'audit/audit_logs.html', context)

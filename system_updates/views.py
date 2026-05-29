import io
import os
import json
import subprocess
from datetime import datetime
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction, connection
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.core.management import call_command
from django.core.files.base import ContentFile

from .models import SystemVersion, UpdateRecord


CURRENT_VERSION = getattr(settings, 'SYSTEM_VERSION', '1.0.0')


def _get_db_vendor():
    return connection.vendor


def _create_db_backup():
    buf = io.BytesIO()
    vendor = _get_db_vendor()
    backup_info = {'vendor': vendor, 'created_at': timezone.now().isoformat()}

    if vendor == 'sqlite':
        db_path = settings.DATABASES['default']['NAME']
        if os.path.exists(db_path):
            with open(db_path, 'rb') as f:
                buf.write(f.read())
        backup_info['path'] = str(db_path)
    elif vendor == 'postgresql':
        try:
            import subprocess
            db_config = settings.DATABASES['default']
            env = os.environ.copy()
            result = subprocess.run(
                ['pg_dump', '--no-owner', '--no-acl', db_config['NAME']],
                capture_output=True, timeout=60, env=env,
            )
            if result.returncode == 0:
                buf.write(result.stdout)
            backup_info['size'] = len(result.stdout)
        except Exception as e:
            backup_info['error'] = str(e)
            raise
    else:
        backup_info['note'] = f'Unsupported vendor: {vendor}'

    backup_json = json.dumps(backup_info, default=str).encode()
    buf.write(b'\n-- BACKUP_META:')
    buf.write(backup_json)

    return buf.getvalue()


@staff_member_required
def system_updates_view(request):
    user = request.user
    if not user.is_superuser:
        messages.error(request, 'Access restricted to Super Administrators only.')
        return redirect('dashboard')

    current_version_obj = SystemVersion.objects.filter(is_current=True).first()
    current_display = current_version_obj.version if current_version_obj else CURRENT_VERSION

    latest_version = SystemVersion.objects.filter(is_available=True).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'check_updates':
            return handle_check_updates(request, user)

        if action == 'install_update':
            return handle_install_update(request, user)

        if action == 'rollback_update':
            return handle_rollback_update(request, user)

    update_history = UpdateRecord.objects.all()[:30]
    all_versions = SystemVersion.objects.all()[:20]

    return render(request, 'system_updates/system_updates.html', {
        'current_version': current_display,
        'latest_version': latest_version,
        'update_history': update_history,
        'all_versions': all_versions,
        'page_title': 'System Updates',
    })


@staff_member_required
def handle_check_updates(request, user):
    latest = SystemVersion.objects.filter(is_available=True).first()
    current = SystemVersion.objects.filter(is_current=True).first()
    current_ver = current.version if current else CURRENT_VERSION

    if latest and latest.version > current_ver:
        messages.success(
            request,
            f'Update available: v{current_ver} → v{latest.version} '
            f'({latest.release_date})'
        )
    elif latest:
        messages.info(request, f'You are up to date at v{current_ver}.')
    else:
        messages.info(request, f'Current version: v{current_ver}. No updates configured.')

    return redirect('system_updates')


@staff_member_required
def handle_install_update(request, user):
    version_to = request.POST.get('version_to', '').strip()
    if not version_to:
        messages.error(request, 'Target version not specified.')
        return redirect('system_updates')

    version_obj = SystemVersion.objects.filter(version=version_to).first()
    if not version_obj:
        messages.error(request, f'Version v{version_to} not found.')
        return redirect('system_updates')

    current = SystemVersion.objects.filter(is_current=True).first()
    version_from = current.version if current else CURRENT_VERSION

    record = UpdateRecord.objects.create(
        version_from=version_from,
        version_to=version_to,
        status='running',
        changelog=version_obj.changelog,
        installed_by=user,
    )

    try:
        messages.info(request, 'Creating database backup before update...')
        backup_bytes = _create_db_backup()
        backup_filename = f'pre_update_{version_from}_to_{version_to}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.sql'
        record.backup_file.save(backup_filename, ContentFile(backup_bytes))
        record.backup_size = len(backup_bytes)
        record.save()

        messages.info(request, 'Running database migrations...')
        call_command('migrate', verbosity=0)

        if current:
            current.is_current = False
            current.save()

        version_obj.is_current = True
        version_obj.save()

        record.status = 'completed'
        record.completed_at = timezone.now()
        record.save()

        messages.success(
            request,
            f'Update completed: v{version_from} → v{version_to}. '
            f'Backup saved: {backup_filename}'
        )

    except Exception as e:
        record.status = 'failed'
        record.error_log = str(e)
        record.save()
        messages.error(request, f'Update failed: {e}')

    return redirect('system_updates')


@staff_member_required
def handle_rollback_update(request, user):
    record_id = request.POST.get('record_id')
    if not record_id:
        messages.error(request, 'Update record not specified.')
        return redirect('system_updates')

    try:
        record = UpdateRecord.objects.get(id=record_id, status='completed')
    except UpdateRecord.DoesNotExist:
        messages.error(request, 'Update record not found.')
        return redirect('system_updates')

    if not record.backup_file:
        messages.error(request, 'No backup file available for rollback.')
        return redirect('system_updates')

    try:
        messages.info(request, 'Rolling back to previous version...')

        current = SystemVersion.objects.filter(is_current=True).first()
        if current:
            current.is_current = False
            current.save()

        prev_version = SystemVersion.objects.filter(
            version=record.version_from
        ).first()
        if prev_version:
            prev_version.is_current = True
            prev_version.save()

        rollback = UpdateRecord.objects.create(
            version_from=record.version_to,
            version_to=record.version_from,
            status='completed',
            changelog=f'Rollback from v{record.version_to} to v{record.version_from}',
            installed_by=user,
            completed_at=timezone.now(),
        )

        record.status = 'rolled_back'
        record.save()

        messages.success(
            request,
            f'Rolled back to v{record.version_from}. '
            f'Restore backup manually from: {record.backup_file.name}'
        )

    except Exception as e:
        messages.error(request, f'Rollback failed: {e}')

    return redirect('system_updates')

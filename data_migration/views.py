import json
import os
import io
import zipfile
from datetime import datetime
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction, connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Model

from stock_manager.models import (
    Item, Category, Sale, StockTransaction, Shop,
    UserProfile, Profile, Conversation, Message,
    Notification, Transfer, CompanyProfile, LandingPageContent,
)
from django.contrib.auth.models import User, Permission, Group

from .models import ExportArchive, ImportLog


EXPORTABLES = {
    'shops': {'model': Shop, 'fields': ['id', 'name', 'slug'], 'key': 'name'},
    'categories': {'model': Category, 'fields': ['id', 'name', 'created_at'], 'key': 'name'},
    'users': {
        'model': User, 'fields': [
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_active', 'is_staff', 'is_superuser', 'date_joined'
        ], 'key': 'username'
    },
    'user_profiles': {
        'model': UserProfile, 'fields': [
            'id', 'user_id', 'role', 'assigned_shop_id', 'permissions'
        ], 'key': 'user_id'
    },
    'profiles': {
        'model': Profile, 'fields': [
            'id', 'user_id', 'shop_id', 'is_shop_user'
        ], 'key': 'user_id'
    },
    'products': {'model': Item, 'fields': [
        'id', 'name', 'category', 'shop_id', 'quantity',
        'unit_price', 'adjustment', 'created_at', 'updated_at'
    ], 'key': 'name'},
    'sales': {'model': Sale, 'fields': [
        'id', 'item_id', 'quantity_sold', 'unit_price',
        'total_amount', 'sold_at'
    ], 'key': 'id'},
    'stock_transactions': {'model': StockTransaction, 'fields': [
        'id', 'item_id', 'source_shop_id', 'target_shop_id',
        'quantity', 'transaction_type', 'reason', 'created_at'
    ], 'key': 'id'},
    'conversations': {'model': Conversation, 'fields': [
        'id', 'last_message', 'last_message_time',
        'last_message_sender_id', 'created_at', 'updated_at'
    ], 'key': 'id'},
    'messages': {'model': Message, 'fields': [
        'id', 'conversation_id', 'sender_id', 'receiver_id',
        'content', 'timestamp', 'is_read', 'status', 'read_at'
    ], 'key': 'id'},
    'notifications': {'model': Notification, 'fields': [
        'id', 'user_id', 'sender_id', 'title', 'message',
        'is_read', 'created_at'
    ], 'key': 'id'},
    'transfers': {'model': Transfer, 'fields': [
        'id', 'transfer_code', 'sender_shop_id', 'receiver_shop_id',
        'created_by_id', 'status', 'notes', 'items_data', 'created_at'
    ], 'key': 'transfer_code'},
    'company_profile': {'model': CompanyProfile, 'fields': [
        'id', 'company_name', 'address', 'phone', 'email',
        'tax_id', 'receipt_footer', 'hero_title', 'hero_tagline'
    ], 'key': 'id'},
    'landing_page': {'model': LandingPageContent, 'fields': [
        'id', 'data'
    ], 'key': 'id'},
    'conversation_participants': {'model': None, 'fields': None, 'key': 'id'},
}


def serialize_value(val):
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return val


def export_model_data(model, fields):
    qs = model.objects.all()
    data = []
    for obj in qs:
        row = {}
        for f in fields:
            val = getattr(obj, f)
            row[f] = serialize_value(val)
        data.append(row)
    return data


def collect_export_data(options):
    archive_data = {}

    if options.get('include_products'):
        archive_data['products'] = export_model_data(Item, EXPORTABLES['products']['fields'])

    if options.get('include_categories'):
        archive_data['categories'] = export_model_data(Category, EXPORTABLES['categories']['fields'])

    if options.get('include_users'):
        archive_data['users'] = export_model_data(User, EXPORTABLES['users']['fields'])
        archive_data['user_profiles'] = export_model_data(UserProfile, EXPORTABLES['user_profiles']['fields'])
        archive_data['profiles'] = export_model_data(Profile, EXPORTABLES['profiles']['fields'])
        groups_data = []
        for g in Group.objects.all():
            groups_data.append({
                'id': g.id, 'name': g.name,
                'permissions': [p.codename for p in g.permissions.all()]
            })
        archive_data['groups'] = groups_data

    if options.get('include_sales'):
        archive_data['sales'] = export_model_data(Sale, EXPORTABLES['sales']['fields'])

    if options.get('include_transactions'):
        archive_data['stock_transactions'] = export_model_data(
            StockTransaction, EXPORTABLES['stock_transactions']['fields']
        )

    if options.get('include_chat'):
        archive_data['conversations'] = export_model_data(
            Conversation, EXPORTABLES['conversations']['fields']
        )
        archive_data['messages'] = export_model_data(Message, EXPORTABLES['messages']['fields'])
        cp_data = []
        for c in Conversation.objects.all():
            for p in c.participants.all():
                cp_data.append({'conversation_id': c.id, 'user_id': p.id})
        archive_data['conversation_participants'] = cp_data

    if options.get('include_notifications'):
        archive_data['notifications'] = export_model_data(
            Notification, EXPORTABLES['notifications']['fields']
        )

    if options.get('include_settings'):
        archive_data['shops'] = export_model_data(Shop, EXPORTABLES['shops']['fields'])
        archive_data['company_profile'] = export_model_data(
            CompanyProfile, EXPORTABLES['company_profile']['fields']
        )
        archive_data['landing_page'] = export_model_data(
            LandingPageContent, EXPORTABLES['landing_page']['fields']
        )
        archive_data['transfers'] = export_model_data(
            Transfer, EXPORTABLES['transfers']['fields']
        )

    archive_data['_exported_at'] = timezone.now().isoformat()
    archive_data['_version'] = '1.0'
    return archive_data


def build_export_zip(archive_data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps({
            'exported_at': archive_data.pop('_exported_at', ''),
            'version': archive_data.pop('_version', '1.0'),
            'tables': list(archive_data.keys()),
        }, indent=2))
        for table_name, rows in archive_data.items():
            if rows is not None:
                zf.writestr(f'{table_name}.json', json.dumps(rows, indent=2, default=str))
    return buf.getvalue()


@staff_member_required
def data_migration_view(request):
    user = request.user
    if not user.is_superuser:
        messages.error(request, 'Access restricted to Super Administrators only.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'export':
            return handle_export(request, user)

        elif action == 'import_preview':
            return handle_import_preview(request, user)

        elif action == 'import_execute':
            return handle_import_execute(request, user)

    export_options = [
        ('include_products', 'Products'),
        ('include_categories', 'Categories'),
        ('include_users', 'Users & Roles'),
        ('include_sales', 'Sales'),
        ('include_transactions', 'Stock Transactions'),
        ('include_chat', 'Chat Messages'),
        ('include_notifications', 'Notifications'),
        ('include_settings', 'Settings & Shops'),
    ]

    exports = ExportArchive.objects.all()[:20]
    imports = ImportLog.objects.all()[:20]
    return render(request, 'data_migration/data_migration.html', {
        'exports': exports,
        'imports': imports,
        'page_title': 'Data Backup & Migration',
        'export_options': export_options,
    })


@staff_member_required
def handle_export(request, user):
    options = {
        'include_products': request.POST.get('include_products') == 'on',
        'include_categories': request.POST.get('include_categories') == 'on',
        'include_users': request.POST.get('include_users') == 'on',
        'include_roles': request.POST.get('include_roles') == 'on',
        'include_sales': request.POST.get('include_sales') == 'on',
        'include_transactions': request.POST.get('include_transactions') == 'on',
        'include_chat': request.POST.get('include_chat') == 'on',
        'include_notifications': request.POST.get('include_notifications') == 'on',
        'include_settings': request.POST.get('include_settings') == 'on',
    }
    if 'include_roles' in options and options['include_roles']:
        options['include_users'] = True
    if 'include_settings' in options and options['include_settings']:
        pass

    archive_data = collect_export_data(options)
    zip_bytes = build_export_zip(archive_data)
    filename = f'backup_{timezone.now().strftime("%Y%m%d_%H%M%S")}.zip'

    export_archive = ExportArchive.objects.create(
        filesize=len(zip_bytes),
        created_by=user,
        **{k: v for k, v in options.items() if k.startswith('include_')},
    )
    export_archive.file.save(filename, ContentFile(zip_bytes))

    messages.success(
        request,
        f'Export complete: {filename} ({export_archive.filesize / 1024:.1f} KB)'
    )
    return redirect('data_migration')


def parse_import_zip(uploaded_file):
    data = {}
    try:
        with zipfile.ZipFile(uploaded_file, 'r') as zf:
            manifest_data = zf.read('manifest.json')
            manifest = json.loads(manifest_data)
            for table_name in manifest.get('tables', []):
                fname = f'{table_name}.json'
                if fname in zf.namelist():
                    raw = zf.read(fname)
                    data[table_name] = json.loads(raw)
            data['_manifest'] = manifest
    except Exception as e:
        raise ValueError(f'Invalid backup file: {e}')
    return data


@staff_member_required
def handle_import_preview(request, user):
    uploaded = request.FILES.get('import_file')
    if not uploaded:
        messages.error(request, 'Please select a backup file to import.')
        return redirect('data_migration')

    mode = request.POST.get('import_mode', 'add')
    if mode not in ('add', 'merge', 'replace'):
        messages.error(request, 'Invalid import mode.')
        return redirect('data_migration')

    try:
        import_data = parse_import_zip(uploaded)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('data_migration')

    summary = {}
    total_new = 0
    total_existing = 0
    total_changed = 0

    for table_name, rows in import_data.items():
        if table_name.startswith('_'):
            continue
        summary[table_name] = {
            'total_in_file': len(rows),
            'will_add': 0,
            'will_update': 0,
            'will_skip': 0,
        }
        if mode == 'replace':
            summary[table_name]['will_add'] = len(rows)
            total_new += len(rows)
        else:
            key_field = _get_key_field(table_name)
            existing_keys = _get_existing_keys(table_name, key_field)
            for row in rows:
                key = row.get(key_field)
                if key and key in existing_keys:
                    if mode == 'merge':
                        summary[table_name]['will_update'] += 1
                        total_changed += 1
                    else:
                        summary[table_name]['will_skip'] += 1
                else:
                    summary[table_name]['will_add'] += 1
                    total_new += 1

    preview_data = {
        'mode': mode,
        'mode_display': dict(ImportLog.MODE_CHOICES).get(mode, mode),
        'tables': summary,
        'total_new': total_new,
        'total_existing': total_existing,
        'total_changed': total_changed,
    }

    import_log = ImportLog.objects.create(
        mode=mode,
        status='preview',
        summary=preview_data,
        created_by=user,
    )

    request.session['import_preview_id'] = import_log.id
    request.session['import_data'] = {
        table: rows for table, rows in import_data.items()
        if not table.startswith('_')
    }
    request.session['import_manifest'] = import_data.get('_manifest', {})

    export_options = [
        ('include_products', 'Products'),
        ('include_categories', 'Categories'),
        ('include_users', 'Users & Roles'),
        ('include_sales', 'Sales'),
        ('include_transactions', 'Stock Transactions'),
        ('include_chat', 'Chat Messages'),
        ('include_notifications', 'Notifications'),
        ('include_settings', 'Settings & Shops'),
    ]

    return render(request, 'data_migration/data_migration.html', {
        'exports': ExportArchive.objects.all()[:20],
        'imports': ImportLog.objects.all()[:20],
        'page_title': 'Data Backup & Migration',
        'preview': preview_data,
        'import_log_id': import_log.id,
        'export_options': export_options,
    })


def _get_key_field(table_name):
    mapping = {
        'products': 'name',
        'categories': 'name',
        'users': 'username',
        'user_profiles': 'user_id',
        'profiles': 'user_id',
        'shops': 'name',
        'sales': 'id',
        'stock_transactions': 'id',
        'conversations': 'id',
        'messages': 'id',
        'notifications': 'id',
        'transfers': 'transfer_code',
        'company_profile': 'id',
        'landing_page': 'id',
        'groups': 'name',
        'conversation_participants': 'id',
    }
    return mapping.get(table_name, 'id')


def _get_existing_keys(table_name, key_field):
    config = EXPORTABLES.get(table_name)
    if not config or config['model'] is None:
        return set()
    model = config['model']
    try:
        vals = model.objects.values_list(key_field, flat=True)
        return set(vals)
    except Exception:
        return set()


@transaction.atomic
def _import_replace(table_name, rows, import_log):
    config = EXPORTABLES.get(table_name)
    if not config or config['model'] is None:
        return 0
    model = config['model']
    model.objects.all().delete()
    return _bulk_create(model, config['fields'], rows)


def _import_add(table_name, rows, import_log):
    config = EXPORTABLES.get(table_name)
    if not config or config['model'] is None:
        return 0, 0
    model = config['model']
    key_field = _get_key_field(table_name)
    existing_keys = _get_existing_keys(table_name, key_field)
    added = 0
    skipped = 0
    for row in rows:
        key = row.get(key_field)
        if key and key in existing_keys:
            skipped += 1
            continue
        _create_row(model, config['fields'], row)
        added += 1
        if key:
            existing_keys.add(key)
    return added, skipped


def _import_merge(table_name, rows, import_log):
    config = EXPORTABLES.get(table_name)
    if not config or config['model'] is None:
        return 0, 0, 0
    model = config['model']
    key_field = _get_key_field(table_name)
    existing_keys = _get_existing_keys(table_name, key_field)
    added = 0
    updated = 0
    skipped = 0
    for row in rows:
        key = row.get(key_field)
        if key and key in existing_keys:
            try:
                obj = model.objects.get(**{key_field: key})
                changed = False
                for f in config['fields']:
                    if f == key_field or f == 'id':
                        continue
                    new_val = _parse_value(row.get(f), model, f)
                    old_val = getattr(obj, f)
                    if str(new_val) != str(old_val):
                        setattr(obj, f, new_val)
                        changed = True
                if changed:
                    obj.save()
                    updated += 1
                else:
                    skipped += 1
            except model.DoesNotExist:
                _create_row(model, config['fields'], row)
                added += 1
        else:
            _create_row(model, config['fields'], row)
            added += 1
            if key:
                existing_keys.add(key)
    return added, updated, skipped


def _create_row(model, fields, row):
    kwargs = {}
    for f in fields:
        if f in row:
            kwargs[f] = _parse_value(row[f], model, f)
    try:
        obj = model(**kwargs)
        obj.save()
        return obj
    except Exception:
        return None


def _parse_value(val, model, field_name):
    if val is None:
        return None
    field = model._meta.get_field(field_name)
    if hasattr(field, 'remote_field') and field.remote_field:
        try:
            return int(val)
        except (ValueError, TypeError):
            return val
    internal_type = field.get_internal_type()
    if internal_type in ('DecimalField', 'FloatField'):
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal('0')
    if internal_type == 'IntegerField':
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0
    if internal_type == 'BooleanField':
        if isinstance(val, bool):
            return val
        return str(val).lower() in ('true', '1', 'yes')
    if internal_type in ('DateTimeField', 'DateField'):
        if isinstance(val, str):
            try:
                from dateutil import parser
                return parser.parse(val)
            except ImportError:
                return val
        return val
    return val


def _bulk_create(model, fields, rows):
    count = 0
    for row in rows:
        obj = _create_row(model, fields, row)
        if obj:
            count += 1
    return count


@staff_member_required
def handle_import_execute(request, user):
    import_log_id = request.POST.get('import_log_id')
    if import_log_id:
        try:
            import_log = ImportLog.objects.get(id=import_log_id, created_by=user)
        except ImportLog.DoesNotExist:
            messages.error(request, 'Import session not found.')
            return redirect('data_migration')
    else:
        messages.error(request, 'Import session not found.')
        return redirect('data_migration')

    import_data = request.session.pop('import_data', None)
    if not import_data:
        messages.error(request, 'Import data expired. Please upload again.')
        return redirect('data_migration')

    mode = import_log.mode
    results = {}
    overall_status = 'completed'

    try:
        with transaction.atomic():
            savepoint = transaction.savepoint()

            for table_name in _get_import_order(mode):
                rows = import_data.get(table_name)
                if not rows:
                    continue
                if mode == 'replace':
                    count = _import_replace(table_name, rows, import_log)
                    results[table_name] = {'replaced': count}
                elif mode == 'add':
                    added, skipped = _import_add(table_name, rows, import_log)
                    results[table_name] = {'added': added, 'skipped': skipped}
                elif mode == 'merge':
                    added, updated, skipped = _import_merge(table_name, rows, import_log)
                    results[table_name] = {'added': added, 'updated': updated, 'skipped': skipped}

            import_log.status = 'completed'
            import_log.completed_at = timezone.now()
            import_log.summary = {'mode': mode, 'results': results}
            import_log.save()

    except Exception as e:
        import_log.status = 'failed'
        import_log.error_log = str(e)
        import_log.save()
        overall_status = 'failed'
        messages.error(request, f'Import failed: {e}')

    if overall_status == 'completed':
        messages.success(request, 'Import completed successfully.')
    return redirect('data_migration')


def _get_import_order(mode):
    order = [
        'shops',
        'categories',
        'users',
        'user_profiles',
        'profiles',
        'groups',
        'products',
        'conversations',
        'conversation_participants',
        'messages',
        'notifications',
        'transfers',
        'stock_transactions',
        'sales',
        'company_profile',
        'landing_page',
    ]
    return order

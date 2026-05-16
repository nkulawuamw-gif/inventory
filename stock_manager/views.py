import json
import math
from datetime import date
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.db.models import Q, Sum, F, ExpressionWrapper, DecimalField
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone
from collections import defaultdict
from .models import Shop, Item, Sale, StockTransaction, UserProfile, BusinessPeriod, PeriodOpeningStock, Receipt, ReceiptItem, CompanyProfile, WhatsAppSetting, WhatsAppMessage, LandingPageContent
from .middleware import shop_access_required


def get_user_profile(user):
    if user.is_superuser:
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def filter_items_by_user(user, qs):
    profile = get_user_profile(user)
    if profile and not profile.is_admin and profile.assigned_shop:
        return qs.filter(shop=profile.assigned_shop)
    return qs


def auto_deduct_from_warehouse(item_name, shop, quantity, unit_price, category=''):
    warehouse = Shop.objects.filter(name='Warehouse').first()
    if not warehouse or shop == warehouse:
        return

    warehouse_item = Item.objects.filter(name__iexact=item_name, shop=warehouse).first()
    if warehouse_item and quantity > 0:
        deduct = min(quantity, warehouse_item.quantity)
        if deduct > 0:
            warehouse_item.quantity -= deduct
            warehouse_item.save()
            StockTransaction.objects.create(
                item=warehouse_item,
                source_shop=warehouse,
                target_shop=shop,
                quantity=deduct,
                transaction_type='transfer',
                reason=f'Auto-deducted: stocked into {shop.name}',
            )


def get_shop_inventory_data(opening_stock=None):
    if opening_stock is None:
        opening_stock = {}
    items = Item.objects.all()

    grouped = defaultdict(lambda: {
        'name': '',
        'shops': {},
        'total_stocked_in': 0,
        'total_stocked_out': 0,
        'total_qty': 0,
        'total_value': 0,
        'category': '',
        'opening_qty': 0,
        'opening_value': 0,
    })

    for item in items:
        key = item.name.lower()
        open_data = opening_stock.get(key, {})
        open_qty = open_data.get('quantity', 0)
        open_price = open_data.get('unit_price', 0)

        if key not in grouped:
            grouped[key] = {
                'name': item.name,
                'shops': {},
                'total_stocked_in': 0,
                'total_stocked_out': 0,
                'total_qty': 0,
                'total_value': 0,
                'category': item.category,
                'opening_qty': open_qty,
                'opening_value': open_qty * open_price,
            }

        sold_qty = Sale.objects.filter(item=item).aggregate(total=Sum('quantity_sold'))['total'] or 0
        stocked_in = item.quantity + sold_qty

        grouped[key]['shops'][item.shop.name] = {
            'stocked_in': stocked_in,
            'stocked_out': sold_qty,
            'balance': item.quantity,
            'unit_price': item.unit_price,
            'value': item.quantity * item.unit_price,
            'is_low_stock': item.is_low_stock,
        }
        grouped[key]['total_stocked_in'] += stocked_in
        grouped[key]['total_stocked_out'] += sold_qty
        grouped[key]['total_qty'] += item.quantity
        grouped[key]['total_value'] += float(item.quantity * item.unit_price)
        grouped[key]['shop_count'] = grouped[key].get('shop_count', 0) + 1
        grouped[key]['total_unit_price'] = grouped[key].get('total_unit_price', 0) + float(item.unit_price)

    for item_data in grouped.values():
        if item_data['total_qty'] > 0:
            item_data['avg_unit_price'] = item_data['total_value'] / item_data['total_qty']
        else:
            item_data['avg_unit_price'] = 0

    return sorted(grouped.values(), key=lambda x: x['name'])


def get_active_period():
    return BusinessPeriod.objects.filter(is_closed=False).first()


def get_period_opening_stock(period, shop=None):
    if not period:
        return {}
    qs = PeriodOpeningStock.objects.filter(period=period)
    if shop:
        qs = qs.filter(shop=shop)
    opening = {}
    for o in qs:
        key = o.item_name.lower()
        opening[key] = {
            'quantity': o.quantity,
            'unit_price': float(o.unit_price),
            'category': o.category,
        }
    return opening


@shop_access_required
def dashboard(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    all_shops = Shop.objects.all()
    if not user_is_admin and profile.assigned_shop:
        all_shops = Shop.objects.filter(id=profile.assigned_shop.id)

    active_period = get_active_period()
    opening_stock = get_period_opening_stock(active_period) if active_period else {}

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_warehouse_item':
            item_name = request.POST.get('item_name', '').strip()
            quantity = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)
            category = request.POST.get('category', '').strip()
            warehouse = Shop.objects.filter(name='Warehouse').first()
            if warehouse and item_name and quantity:
                existing = Item.objects.filter(name__iexact=item_name, shop=warehouse).first()
                if existing:
                    existing.quantity += int(quantity)
                    existing.unit_price = float(unit_price)
                    if category:
                        existing.category = category
                    existing.save()
                    messages.success(request, f'Added {quantity}x "{item_name}" to existing Warehouse stock (now {existing.quantity})')
                else:
                    Item.objects.create(
                        name=item_name,
                        shop=warehouse,
                        quantity=int(quantity),
                        unit_price=float(unit_price),
                        category=category,
                    )
                    messages.success(request, f'Added "{item_name}" to Warehouse')
            else:
                messages.error(request, 'Missing item name, quantity, or Warehouse shop not found')
            return redirect('dashboard')

        elif action == 'edit_warehouse_item':
            item_id = request.POST.get('item_id')
            quantity = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)
            category = request.POST.get('category', '').strip()
            try:
                item = Item.objects.get(id=item_id, shop__name='Warehouse')
                item.quantity = int(quantity)
                item.unit_price = float(unit_price)
                item.category = category
                item.save()
                messages.success(request, f'Updated "{item.name}" in Warehouse')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity or price')
            return redirect('dashboard')

        elif action == 'delete_warehouse_item':
            item_id = request.POST.get('item_id')
            try:
                item = Item.objects.get(id=item_id, shop__name='Warehouse')
                name = item.name
                item.delete()
                messages.success(request, f'Deleted "{name}" from Warehouse')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found or already deleted')
            return redirect('dashboard')

    if user_is_admin:
        inventory_data = get_shop_inventory_data(opening_stock)
    else:
        items = filter_items_by_user(request.user, Item.objects.all())
        grouped = defaultdict(lambda: {
            'name': '',
            'shops': {},
            'total_stocked_in': 0,
            'total_stocked_out': 0,
            'total_qty': 0,
            'total_value': 0,
            'category': '',
            'opening_qty': 0,
            'opening_value': 0,
        })
        for item in items:
            key = item.name.lower()
            open_data = opening_stock.get(key, {})
            open_qty = open_data.get('quantity', 0)
            open_price = open_data.get('unit_price', 0)
            if key not in grouped:
                grouped[key] = {
                    'name': item.name,
                    'shops': {},
                    'total_stocked_in': 0,
                    'total_stocked_out': 0,
                    'total_qty': 0,
                    'total_value': 0,
                    'category': item.category,
                    'opening_qty': open_qty,
                    'opening_value': open_qty * open_price,
                }
            sold_qty = Sale.objects.filter(item=item).aggregate(total=Sum('quantity_sold'))['total'] or 0
            stocked_in = item.quantity + sold_qty
            grouped[key]['shops'][item.shop.name] = {
                'stocked_in': stocked_in,
                'stocked_out': sold_qty,
                'balance': item.quantity,
                'unit_price': item.unit_price,
                'value': item.quantity * item.unit_price,
                'is_low_stock': item.is_low_stock,
            }
            grouped[key]['total_stocked_in'] += stocked_in
            grouped[key]['total_stocked_out'] += sold_qty
            grouped[key]['total_qty'] += item.quantity
            grouped[key]['total_value'] += float(item.quantity * item.unit_price)
            grouped[key]['shop_count'] = grouped[key].get('shop_count', 0) + 1
            grouped[key]['total_unit_price'] = grouped[key].get('total_unit_price', 0) + float(item.unit_price)
        for item_data in grouped.values():
            if item_data['total_qty'] > 0:
                item_data['avg_unit_price'] = item_data['total_value'] / item_data['total_qty']
            else:
                item_data['avg_unit_price'] = 0
        inventory_data = sorted(grouped.values(), key=lambda x: x['name'])

    shop_items = Item.objects.all()
    if not user_is_admin:
        shop_items = shop_items.filter(shop=profile.assigned_shop)

    total_items = shop_items.count()
    total_stock_value = sum(item.total_value for item in shop_items)
    low_stock_items = shop_items.filter(quantity__lt=5).select_related('shop')
    low_stock_count = low_stock_items.count()

    total_sales = Sale.objects.filter(item__in=shop_items)
    total_sales_count = total_sales.count()
    total_sales_amount = total_sales.aggregate(total=Sum('total_amount'))['total'] or 0

    from django.db.models import Count
    shop_sales_data = []
    for shop in all_shops:
        shop_sales = Sale.objects.filter(item__shop=shop)
        shop_total = shop_sales.aggregate(total=Sum('total_amount'))['total'] or 0
        shop_count = shop_sales.count()
        shop_sales_data.append({
            'shop': shop,
            'total_amount': shop_total,
            'count': shop_count,
        })

    shop_stock_data = []
    shop_value_data = []
    for shop in all_shops:
        shop_items = Item.objects.filter(shop=shop)
        shop_qty = shop_items.aggregate(total=Sum('quantity'))['total'] or 0
        shop_value = sum(item.total_value for item in shop_items)
        shop_stock_data.append({'shop': shop.name, 'quantity': shop_qty})
        shop_value_data.append({'shop': shop.name, 'value': float(shop_value)})

    category_data = {}
    for item in Item.objects.all():
        cat = item.category if item.category else 'Uncategorized'
        category_data[cat] = category_data.get(cat, 0) + item.quantity

    context = {
        'inventory_data': inventory_data,
        'total_items': total_items,
        'total_stock_value': total_stock_value,
        'low_stock_items': low_stock_items,
        'low_stock_count': low_stock_count,
        'total_sales_count': total_sales_count,
        'total_sales_amount': total_sales_amount,
        'shop_sales_data': shop_sales_data,
        'shop_stock_data_json': json.dumps(shop_stock_data),
        'shop_value_data_json': json.dumps(shop_value_data),
        'category_labels_json': json.dumps(list(category_data.keys())),
        'category_values_json': json.dumps(list(category_data.values())),
        'all_shops': all_shops,
        'active_period': active_period,
        'page_title': 'Main Inventory Dashboard',
    }
    return render(request, 'stock_manager/dashboard.html', context)


def process_csv_import(csv_file):
    import csv
    import io
    text_file = io.TextIOWrapper(csv_file.file, encoding='utf-8-sig')
    reader = csv.reader(text_file)
    header = next(reader, None)
    if not header:
        return 0, 0, 0, ['CSV file is empty']

    header_map = {}
    aliases = {
        'item_name': ['item_name', 'item', 'item name', 'name', 'product', 'product name'],
        'shop_name': ['shop_name', 'shop name', 'shop', 'store', 'store name', 'branch'],
        'category': ['category', 'type', 'item category', 'item type', 'product category'],
        'quantity': ['quantity', 'qty', 'stock', 'amount', 'count', 'stocked_in', 'stocked in'],
        'unit_price': ['unit_price', 'price', 'unit price', 'cost', 'rate', 'avg_unit_cost', 'avg unit cost', 'total_cost', 'total cost'],
    }
    for idx, h in enumerate(header):
        clean = h.strip().lower().replace(' ', '_')
        for field, variants in aliases.items():
            if clean in variants or h.strip().lower() in variants:
                header_map[field] = idx
                break

    if not header_map or 'item_name' not in header_map:
        col_count = len(header)
        # Check if this looks like warehouse layout: Item, Category, Stocked In, Stocked Out, Balance, Avg Unit Cost, Total Cost
        has_shop_col = 'shop_name' in header_map
        header_map = {
            'item_name': 0,
        }
        if col_count > 1:
            header_map['category'] = 1
        if col_count > 2:
            header_map['quantity'] = 2
        if col_count > 5:
            header_map['unit_price'] = 5
        elif col_count > 3:
            header_map['unit_price'] = 3

    created = 0
    updated = 0
    skipped = 0
    errors = []
    item_col = header_map.get('item_name', 0)
    shop_col = header_map.get('shop_name', None)
    cat_col = header_map.get('category', 1)
    qty_col = header_map.get('quantity', 2)
    price_col = header_map.get('unit_price', 5)

    for i, row in enumerate(reader, start=2):
        try:
            if not row or all(field.strip() == '' for field in row):
                continue

            item_name = row[item_col].strip() if item_col < len(row) else ''
            shop_name = row[shop_col].strip() if shop_col is not None and shop_col < len(row) else 'Warehouse'
            category = row[cat_col].strip() if cat_col < len(row) else ''
            qty_raw = row[qty_col].strip() if qty_col < len(row) else '0'
            price_raw = row[price_col].strip() if price_col < len(row) else '0'

            if not item_name:
                skipped += 1
                continue

            try:
                quantity = int(qty_raw.replace(',', '')) if qty_raw else 0
            except ValueError:
                try:
                    quantity = int(float(qty_raw.replace(',', '')))
                except ValueError:
                    quantity = 0

            try:
                unit_price = float(price_raw.replace(',', '')) if price_raw else 0
            except ValueError:
                unit_price = 0

            try:
                shop = Shop.objects.get(name__iexact=shop_name)
            except Shop.DoesNotExist:
                errors.append(f'Row {i}: Shop "{shop_name}" not found')
                continue

            existing = Item.objects.filter(name__iexact=item_name, shop=shop).first()
            if existing:
                existing.quantity += quantity
                existing.unit_price = unit_price
                if category:
                    existing.category = category
                existing.save()
                updated += 1
            else:
                Item.objects.create(
                    name=item_name,
                    shop=shop,
                    quantity=quantity,
                    unit_price=unit_price,
                    category=category,
                )
                auto_deduct_from_warehouse(item_name, shop, quantity, unit_price, category)
                created += 1
        except Exception as e:
            errors.append(f'Row {i}: {str(e)}')
            continue

    return created, updated, skipped, errors


@shop_access_required
def dashboard_bulk_import(request):
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if csv_file:
            created, updated, skipped, errors = process_csv_import(csv_file)
            if created > 0 or updated > 0:
                msg = f'Imported {created} new items'
                if updated > 0:
                    msg += f', updated {updated} existing items'
                if skipped > 0:
                    msg += f', skipped {skipped} empty rows'
                messages.success(request, msg)
            if errors:
                messages.error(request, f'{len(errors)} errors occurred. First: {errors[0]}')
        else:
            messages.error(request, 'No file uploaded')
    return redirect('dashboard')


@shop_access_required
def search_items(request):
    query = request.GET.get('q', '').strip()
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    shops = Shop.objects.all()
    if not user_is_admin and profile.assigned_shop:
        shops = Shop.objects.filter(id=profile.assigned_shop.id)

    if query:
        items = Item.objects.filter(name__icontains=query).select_related('shop')
        if not user_is_admin and profile.assigned_shop:
            items = items.filter(shop=profile.assigned_shop)
    else:
        items = Item.objects.none()

    grouped = defaultdict(lambda: {'name': '', 'shops': {}, 'total_qty': 0})

    for item in items:
        key = item.name.lower()
        if key not in grouped:
            grouped[key] = {
                'name': item.name,
                'shops': {},
                'total_qty': 0,
            }
        grouped[key]['shops'][item.shop.name] = {
            'quantity': item.quantity,
            'unit_price': item.unit_price,
            'is_low_stock': item.is_low_stock,
        }
        grouped[key]['total_qty'] += item.quantity

    search_results = sorted(grouped.values(), key=lambda x: x['name'])

    context = {
        'query': query,
        'search_results': search_results,
        'all_shops': shops,
        'page_title': f'Search: {query}' if query else 'Search Inventory',
    }
    return render(request, 'stock_manager/search.html', context)


@shop_access_required
def shop_dashboard(request, shop_slug):
    shop = get_object_or_404(Shop, name__iexact=shop_slug.replace('-', ' '))
    is_warehouse = shop.name == 'Warehouse'

    active_period = get_active_period()
    opening_stock = {}
    if active_period:
        opening_stock = get_period_opening_stock(active_period, shop=shop)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'record_sale' and not is_warehouse:
            item_id = request.POST.get('item_id', '')
            qty_sold = request.POST.get('quantity_sold', 0)

            if item_id and int(qty_sold) >= 0:
                item = get_object_or_404(Item, id=item_id, shop=shop)
                qty_sold = int(qty_sold)
                if qty_sold > item.quantity:
                    messages.error(request, f'Not enough stock! Only {item.quantity} available')
                else:
                    total_amount = item.unit_price * qty_sold
                    Sale.objects.create(
                        item=item,
                        quantity_sold=qty_sold,
                        unit_price=item.unit_price,
                        total_amount=total_amount,
                    )
                    item.quantity -= qty_sold
                    item.save()
                    messages.success(request, f'Recorded sale: {qty_sold}x {item.name} (MWK {total_amount:,.2f})')

        elif action == 'add_warehouse_item' and is_warehouse:
            item_name = request.POST.get('item_name', '').strip()
            category = request.POST.get('category', '').strip()
            qty = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)
            confirm_dup = request.POST.get('confirm_duplicate') == '1'
            if item_name and qty:
                try:
                    qty = int(qty)
                    unit_price = float(unit_price)
                except (ValueError, TypeError):
                    qty = 0
                    unit_price = 0
                existing = Item.objects.filter(name__iexact=item_name, shop=shop).first()
                if existing and not confirm_dup:
                    request.session['wh_duplicate'] = {
                        'item_name': item_name,
                        'category': category,
                        'quantity': qty,
                        'unit_price': unit_price,
                        'existing_id': existing.id,
                        'existing_qty': existing.quantity,
                        'existing_price': float(existing.unit_price),
                    }
                    return redirect('shop_dashboard', shop_slug=shop_slug)
                if existing:
                    existing.quantity += qty
                    existing.unit_price = unit_price
                    if category:
                        existing.category = category
                    existing.save()
                    messages.success(request, f'Updated "{item_name}" in Warehouse')
                else:
                    Item.objects.create(
                        shop=shop, name=item_name, category=category or '',
                        quantity=qty, unit_price=unit_price,
                    )
                    messages.success(request, f'Added "{item_name}" to Warehouse')
            request.session.pop('wh_duplicate', None)
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'dismiss_duplicate' and is_warehouse:
            request.session.pop('wh_duplicate', None)
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'bulk_transfer' and is_warehouse:
            csv_file = request.FILES.get('csv_file')
            if not csv_file:
                messages.error(request, 'No file uploaded')
                return redirect('shop_dashboard', shop_slug=shop_slug)
            try:
                decoded = csv_file.read().decode('utf-8-sig').splitlines()
                reader = __import__('csv').DictReader(decoded)
                total = 0
                errors = []
                for i, row in enumerate(reader, start=2):
                    item_name = (row.get('Item Name') or '').strip()
                    target_shop_name = (row.get('Target Shop') or '').strip()
                    qty_str = (row.get('Quantity') or '').strip()
                    if not item_name or not target_shop_name or not qty_str:
                        errors.append(f'Row {i}: missing fields')
                        continue
                    try:
                        qty = int(qty_str)
                    except ValueError:
                        errors.append(f'Row {i}: invalid quantity "{qty_str}"')
                        continue
                    if qty <= 0:
                        continue
                    target_shop = Shop.objects.filter(name__iexact=target_shop_name.strip()).first()
                    if not target_shop:
                        errors.append(f'Row {i}: shop "{target_shop_name}" not found')
                        continue
                    if target_shop.is_warehouse:
                        errors.append(f'Row {i}: cannot transfer to Warehouse')
                        continue
                    source_item = Item.objects.filter(name__iexact=item_name, shop=shop).first()
                    if not source_item:
                        errors.append(f'Row {i}: item "{item_name}" not found in Warehouse')
                        continue
                    if qty > source_item.quantity:
                        errors.append(f'Row {i}: insufficient stock for "{item_name}" (have {source_item.quantity}, need {qty})')
                        continue
                    target_item, created = Item.objects.get_or_create(
                        name=source_item.name,
                        shop=target_shop,
                        defaults={
                            'quantity': qty,
                            'unit_price': source_item.unit_price,
                            'category': source_item.category,
                        }
                    )
                    if not created:
                        target_item.quantity += qty
                        target_item.save()
                    source_item.quantity -= qty
                    source_item.save()
                    StockTransaction.objects.create(
                        item=source_item,
                        source_shop=shop,
                        target_shop=target_shop,
                        quantity=qty,
                        transaction_type='transfer',
                        reason=f'Bulk transfer to {target_shop.name}',
                    )
                    total += 1
                if total:
                    messages.success(request, f'Bulk transfer complete: {total} item(s) transferred')
                if errors:
                    for err in errors:
                        messages.error(request, err)
            except Exception as e:
                messages.error(request, f'Error processing file: {str(e)}')
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'edit_warehouse_item' and is_warehouse:
            item_id = request.POST.get('item_id')
            category = request.POST.get('category', '').strip()
            qty = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)
            try:
                item = Item.objects.get(id=item_id, shop=shop)
                item.category = category or ''
                item.quantity = int(qty)
                item.unit_price = float(unit_price)
                item.save()
                messages.success(request, f'Updated "{item.name}"')
            except (Item.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Invalid item or values')
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'delete_warehouse_item' and is_warehouse:
            item_id = request.POST.get('item_id')
            try:
                item = Item.objects.get(id=item_id, shop=shop)
                name = item.name
                item.delete()
                messages.success(request, f'Deleted "{name}" from Warehouse')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'bulk_delete_warehouse' and is_warehouse:
            item_ids = request.POST.getlist('item_ids')
            valid_ids = []
            for iid in item_ids:
                try:
                    valid_ids.append(int(iid))
                except (ValueError, TypeError):
                    pass
            if valid_ids:
                count, _ = Item.objects.filter(id__in=valid_ids, shop=shop).delete()
                messages.success(request, f'Deleted {count} items from Warehouse')
            else:
                messages.error(request, 'No valid items selected')
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'transfer_selected' and is_warehouse:
            item_ids = request.POST.getlist('item_ids')
            target_shop_id = request.POST.get('target_shop', '')
            qty_str = request.POST.get('transfer_qty', '0')
            errors = []
            success_count = 0
            try:
                qty = int(qty_str)
            except (ValueError, TypeError):
                qty = 0
            if not target_shop_id:
                messages.error(request, 'No target shop selected')
            else:
                target_shop = get_object_or_404(Shop, id=target_shop_id)
                if target_shop.is_warehouse:
                    messages.error(request, 'Cannot transfer to Warehouse')
                else:
                    for iid in item_ids:
                        try:
                            iid = int(iid)
                        except (ValueError, TypeError):
                            continue
                        item = Item.objects.filter(id=iid, shop=shop).first()
                        if not item:
                            errors.append(f'Item ID {iid} not found')
                            continue
                        if item.quantity < qty:
                            errors.append(f'Not enough stock for "{item.name}" (available: {item.quantity}, needed: {qty})')
                            continue
                        target_item = Item.objects.filter(shop=target_shop, name__iexact=item.name).first()
                        if target_item:
                            target_item.quantity += qty
                            target_item.save()
                        else:
                            target_item = Item.objects.create(shop=target_shop, name=item.name, category=item.category, quantity=qty, unit_price=item.unit_price)
                        item.quantity -= qty
                        item.save()
                        StockTransaction.objects.create(
                            item=item, source_shop=shop, target_shop=target_shop,
                            quantity=qty, transaction_type='transfer'
                        )
                        StockTransaction.objects.create(
                            item=target_item, source_shop=shop, target_shop=target_shop,
                            quantity=qty, transaction_type='stock_in'
                        )
                        success_count += 1
                if success_count:
                    messages.success(request, f'Transferred {success_count} item(s) to {target_shop.name}')
                for err in errors:
                    messages.error(request, err)
            return redirect('shop_dashboard', shop_slug=shop_slug)

        elif action == 'transfer_item' and is_warehouse:
            item_id = request.POST.get('item_id', '')
            target_shop_id = request.POST.get('target_shop', '')
            qty_transfer = request.POST.get('quantity_transfer', 0)

            if item_id and target_shop_id and int(qty_transfer) >= 0:
                item = get_object_or_404(Item, id=item_id, shop=shop)
                target_shop = get_object_or_404(Shop, id=target_shop_id)
                qty_transfer = int(qty_transfer)
                if qty_transfer > item.quantity:
                    messages.error(request, f'Not enough stock in Warehouse! Only {item.quantity} available')
                else:
                    target_item, created = Item.objects.get_or_create(
                        name=item.name,
                        shop=target_shop,
                        defaults={
                            'quantity': qty_transfer,
                            'unit_price': item.unit_price,
                            'category': item.category,
                        }
                    )
                    if not created:
                        target_item.quantity += qty_transfer
                        target_item.save()
                    item.quantity -= qty_transfer
                    item.save()
                    StockTransaction.objects.create(
                        item=item,
                        source_shop=shop,
                        target_shop=target_shop,
                        quantity=qty_transfer,
                        transaction_type='transfer',
                        reason=f'Warehouse transfer to {target_shop.name}',
                    )
                    messages.success(request, f'Transferred {qty_transfer}x {item.name} to {target_shop.name}')

        return redirect('shop_dashboard', shop_slug=shop_slug)

    items = Item.objects.filter(shop=shop).order_by('name')

    total_items = items.count()
    total_qty = items.aggregate(total=Sum('quantity'))['total'] or 0
    total_value = sum(item.total_value for item in items)
    low_stock_items = items.filter(quantity__lt=5)

    stock_transactions = StockTransaction.objects.filter(
        Q(source_shop=shop) | Q(target_shop=shop)
    ).select_related('item', 'source_shop', 'target_shop').order_by('-created_at')[:50]

    total_transactions = len(stock_transactions)
    total_transferred_qty = sum(tx.quantity for tx in stock_transactions)

    if is_warehouse:
        other_shops = Shop.objects.exclude(name='Warehouse')
        warehouse_stocked_in = 0
        warehouse_stocked_out = 0
        stock_out_by_item = {}
        opening_by_item = {}
        for item in items:
            stocked_out = StockTransaction.objects.filter(item=item, source_shop=shop, transaction_type='transfer').aggregate(total=Sum('quantity'))['total'] or 0
            warehouse_stocked_out += stocked_out
            warehouse_stocked_in += item.quantity + stocked_out
            stock_out_by_item[item.id] = stocked_out
            open_data = opening_stock.get(item.name.lower(), {})
            opening_by_item[item.id] = open_data.get('quantity', 0)

        warehouse_item_map = {}
        for wi in items:
            warehouse_item_map[wi.name.lower()] = wi

        total_value_by_item = {}
        for item in items:
            open_qty = opening_by_item.get(item.id, 0)
            stocked_in = item.quantity + stock_out_by_item.get(item.id, 0)
            total_value_by_item[item.id] = (open_qty + stocked_in) * float(item.unit_price)

        context = {
            'shop': shop,
            'items': items,
            'total_items': total_items,
            'total_qty': total_qty,
            'total_value': total_value,
            'low_stock_items': low_stock_items,
            'other_shops': other_shops,
            'warehouse_stocked_in': warehouse_stocked_in,
            'warehouse_stocked_out': warehouse_stocked_out,
            'stock_out_by_item': stock_out_by_item,
            'opening_by_item': opening_by_item,
            'total_value_by_item': total_value_by_item,
            'warehouse_item_map': warehouse_item_map,
            'warehouse_item_list': items,
            'active_period': active_period,
            'stock_transactions': stock_transactions,
            'total_transactions': total_transactions,
            'total_transferred_qty': total_transferred_qty,
            'is_warehouse': True,
            'page_title': f'{shop.name} Inventory',
        }
    else:
        shop_sales = Sale.objects.filter(item__shop=shop).select_related('item').order_by('-sold_at')[:20]
        today_sales = Sale.objects.filter(item__shop=shop, sold_at__date=timezone.now())
        today_total = today_sales.aggregate(total=Sum('total_amount'))['total'] or 0

        context = {
            'shop': shop,
            'items': items,
            'total_items': total_items,
            'total_qty': total_qty,
            'total_value': total_value,
            'low_stock_items': low_stock_items,
            'shop_sales': shop_sales,
            'today_total': today_total,
            'stock_transactions': stock_transactions,
            'total_transactions': total_transactions,
            'total_transferred_qty': total_transferred_qty,
            'active_period': active_period,
            'is_warehouse': False,
            'page_title': f'{shop.name} Inventory',
        }
    return render(request, 'stock_manager/shop_dashboard.html', context)


def generate_receipt_number():
    today = date.today()
    prefix = today.strftime('RCP-%Y%m%d-')
    last = Receipt.objects.filter(receipt_number__startswith=prefix).order_by('-receipt_number').first()
    if last:
        num = int(last.receipt_number.split('-')[-1]) + 1
    else:
        num = 1
    return f'{prefix}{num:04d}'


@shop_access_required
def point_of_sale(request, shop_slug):
    try:
        shop = get_object_or_404(Shop, name__iexact=shop_slug.replace('-', ' '))
    except Exception:
        return JsonResponse({'error': 'Shop not found'}, status=404)
    is_warehouse = shop.name == 'Warehouse'

    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                try:
                    data = json.loads(request.body)
                except Exception:
                    return JsonResponse({'error': 'Invalid JSON body'}, status=400)
            else:
                data = request.POST
            action = data.get('action')

            if action == 'complete_sale':
                raw_items = data.get('items', '[]')
                if isinstance(raw_items, str):
                    try:
                        items_data = json.loads(raw_items)
                    except Exception:
                        return JsonResponse({'error': 'Invalid items data'}, status=400)
                else:
                    items_data = raw_items
                if not items_data:
                    return JsonResponse({'error': 'No items in cart'}, status=400)

                customer_name = data.get('customer_name', '').strip()
                amount_received = float(data.get('amount_received', 0))

                subtotal = 0.0
                line_items = []
                errors = []

                for line in items_data:
                    item_id = line.get('item_id')
                    qty = int(line.get('quantity', 0))
                    if not item_id or qty < 1:
                        continue
                    try:
                        item = Item.objects.get(id=item_id, shop=shop)
                    except Item.DoesNotExist:
                        errors.append(f'Item id {item_id} not found')
                        continue
                    if qty > item.quantity:
                        errors.append(f'Not enough stock for {item.name}: have {item.quantity}, need {qty}')
                        continue
                    line_total = float(item.unit_price) * qty
                    subtotal += line_total
                    line_items.append({'item': item, 'qty': qty, 'unit_price': float(item.unit_price), 'total': line_total})

                if errors:
                    return JsonResponse({'error': '; '.join(errors)}, status=400)

                total = subtotal
                change = max(0, amount_received - total)

                receipt = Receipt.objects.create(
                    receipt_number=generate_receipt_number(),
                    shop=shop,
                    customer_name=customer_name,
                    subtotal=subtotal,
                    total=total,
                    amount_received=amount_received,
                    change=change,
                    created_by=request.user if request.user.is_authenticated else None,
                )

                for li in line_items:
                    ReceiptItem.objects.create(
                        receipt=receipt,
                        item=li['item'],
                        item_name=li['item'].name,
                        quantity=li['qty'],
                        unit_price=li['unit_price'],
                        total=li['total'],
                    )
                    li['item'].quantity -= li['qty']
                    li['item'].save()

                return JsonResponse({'receipt_id': receipt.id, 'receipt_number': receipt.receipt_number})

            return JsonResponse({'error': 'Invalid action'}, status=400)
        except Exception as e:
            return JsonResponse({'error': f'Server error: {str(e)}'}, status=500)

    items = Item.objects.filter(shop=shop).order_by('name')
    today_receipts = Receipt.objects.filter(shop=shop, created_at__date=timezone.now())
    today_total = today_receipts.aggregate(total=Sum('total'))['total'] or 0
    today_count = today_receipts.count()

    context = {
        'shop': shop,
        'items': items,
        'today_total': today_total,
        'today_count': today_count,
        'page_title': f'Point of Sale - {shop.name}',
    }
    return render(request, 'stock_manager/point_of_sale.html', context)


@shop_access_required
def sales_history(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    receipts = Receipt.objects.select_related('shop', 'created_by').prefetch_related('items')
    if not user_is_admin and profile.assigned_shop:
        receipts = receipts.filter(shop=profile.assigned_shop)

    q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    shop_filter = request.GET.get('shop', '')

    if q:
        receipts = receipts.filter(
            Q(receipt_number__icontains=q) | Q(customer_name__icontains=q)
        )
    if date_from:
        receipts = receipts.filter(created_at__date__gte=date_from)
    if date_to:
        receipts = receipts.filter(created_at__date__lte=date_to)
    if user_is_admin and shop_filter:
        receipts = receipts.filter(shop_id=shop_filter)

    receipts = receipts.order_by('-created_at')[:100]

    total_sales = receipts.aggregate(total=Sum('total'))['total'] or 0

    context = {
        'receipts': receipts,
        'total_sales': total_sales,
        'query': q,
        'date_from': date_from,
        'date_to': date_to,
        'selected_shop': shop_filter,
        'user_is_admin': user_is_admin,
        'page_title': 'Sales History',
    }
    return render(request, 'stock_manager/sales_history.html', context)


@shop_access_required
def print_receipt(request, receipt_id):
    receipt = get_object_or_404(Receipt, id=receipt_id)
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin
    if not user_is_admin and profile.assigned_shop and receipt.shop != profile.assigned_shop:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    context = {
        'receipt': receipt,
        'page_title': f'Receipt {receipt.receipt_number}',
    }
    return render(request, 'stock_manager/receipt_print.html', context)


@shop_access_required
def export_csv(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="inventory_export.csv"'

    writer = __import__('csv').writer(response)
    writer.writerow(['Item Name', 'Category', 'Shop', 'Quantity', 'Unit Price', 'Total Value', 'Low Stock'])

    items = Item.objects.select_related('shop').order_by('name', 'shop__name')
    if not user_is_admin and profile.assigned_shop:
        items = items.filter(shop=profile.assigned_shop)

    for item in items:
        writer.writerow([
            item.name,
            item.category,
            item.shop.name,
            item.quantity,
            item.unit_price,
            item.total_value,
            'YES' if item.is_low_stock else 'NO',
        ])

    return response


@shop_access_required
def admin_manage(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    all_shops = Shop.objects.all()
    if not user_is_admin and profile.assigned_shop:
        all_shops = Shop.objects.filter(id=profile.assigned_shop.id)

    active_period = get_active_period()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_item':
            item_name = request.POST.get('item_name', '').strip()
            shop_id = request.POST.get('shop', '')
            quantity = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)
            category = request.POST.get('category', '').strip()

            if item_name and shop_id:
                shop = get_object_or_404(Shop, id=shop_id)
                existing = Item.objects.filter(name__iexact=item_name, shop=shop).first()
                if existing:
                    existing.quantity += int(quantity)
                    existing.unit_price = float(unit_price)
                    if category:
                        existing.category = category
                    existing.save()
                    messages.success(request, f'Added {quantity}x "{item_name}" to existing stock at {shop.name} (now {existing.quantity})')
                else:
                    Item.objects.create(
                        name=item_name,
                        shop=shop,
                        quantity=int(quantity),
                        unit_price=float(unit_price),
                        category=category,
                    )
                    messages.success(request, f'Added "{item_name}" to {shop.name}')
                auto_deduct_from_warehouse(item_name, shop, int(quantity), float(unit_price), category)

        elif action == 'edit_item':
            item_id = request.POST.get('item_id')
            quantity = request.POST.get('quantity', 0)
            unit_price = request.POST.get('unit_price', 0)

            try:
                item = Item.objects.get(id=item_id)
                item.quantity = int(quantity)
                item.unit_price = float(unit_price)
                item.save()
                messages.success(request, f'Updated "{item.name}"')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity or price')

        elif action == 'delete_item':
            item_id = request.POST.get('item_id')
            try:
                item = Item.objects.get(id=item_id)
                name = item.name
                shop = item.shop.name
                item.delete()
                messages.success(request, f'Deleted "{name}" from {shop}')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found or already deleted')

        elif action == 'bulk_delete':
            item_ids = request.POST.getlist('item_ids')
            valid_ids = []
            for iid in item_ids:
                try:
                    valid_ids.append(int(iid))
                except (ValueError, TypeError):
                    pass
            if valid_ids:
                count, _ = Item.objects.filter(id__in=valid_ids).delete()
                messages.success(request, f'Deleted {count} items')
            else:
                messages.error(request, 'No valid items selected')

        elif action == 'delete_all':
            count = Item.objects.count()
            Item.objects.all().delete()
            messages.success(request, f'Deleted all {count} items from inventory')

        elif action == 'stock_transaction':
            item_id = request.POST.get('item_id', '')
            transaction_type = request.POST.get('transaction_type', '')
            quantity = request.POST.get('quantity', 0)
            from_shop_id = request.POST.get('from_shop', '')
            to_shop_id = request.POST.get('to_shop', '')
            reason = request.POST.get('reason', '').strip()

            if not item_id or not transaction_type or str(quantity).strip() == '':
                messages.error(request, 'Please fill in all required fields')
                return redirect('admin_manage')

            try:
                quantity = int(quantity)
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity')
                return redirect('admin_manage')

            if transaction_type == 'stock_in':
                if not to_shop_id:
                    messages.error(request, 'Please select a destination shop')
                    return redirect('admin_manage')
                try:
                    to_shop = Shop.objects.get(id=to_shop_id)
                    item = Item.objects.get(id=item_id, shop=to_shop)
                except Shop.DoesNotExist:
                    messages.error(request, 'Destination shop not found')
                    return redirect('admin_manage')
                except Item.DoesNotExist:
                    messages.error(request, 'Item not found in selected shop')
                    return redirect('admin_manage')

                item.quantity += quantity
                item.save()
                StockTransaction.objects.create(
                    item=item,
                    source_shop=None,
                    target_shop=to_shop,
                    quantity=quantity,
                    transaction_type='stock_in',
                    reason=reason,
                )
                messages.success(request, f'Stocked in {quantity}x {item.name} to {to_shop.name}')
            elif not from_shop_id:
                messages.error(request, 'Please select a source shop')
            elif from_shop_id == to_shop_id:
                messages.error(request, 'From and To shops cannot be the same')
            else:
                try:
                    from_shop = Shop.objects.get(id=from_shop_id)
                except Shop.DoesNotExist:
                    messages.error(request, 'Source shop not found')
                    return redirect('admin_manage')

                to_shop = None
                if to_shop_id:
                    try:
                        to_shop = Shop.objects.get(id=to_shop_id)
                    except Shop.DoesNotExist:
                        messages.error(request, 'Target shop not found')
                        return redirect('admin_manage')

                try:
                    item = Item.objects.get(id=item_id, shop=from_shop)
                except Item.DoesNotExist:
                    messages.error(request, 'Item not found in source shop')
                    return redirect('admin_manage')

                item.quantity -= quantity
                item.save()

                if transaction_type == 'transfer' and to_shop:
                    target_item, created = Item.objects.get_or_create(
                        name=item.name,
                        shop=to_shop,
                        defaults={
                            'quantity': quantity,
                            'unit_price': item.unit_price,
                            'category': item.category,
                        }
                    )
                    if not created:
                        target_item.quantity += quantity
                        target_item.save()

                StockTransaction.objects.create(
                    item=item,
                    source_shop=from_shop,
                    target_shop=to_shop if transaction_type == 'transfer' else None,
                    quantity=quantity,
                    transaction_type=transaction_type,
                    reason=reason,
                )
                if transaction_type == 'transfer':
                    messages.success(request, f'Transferred {quantity}x {item.name} from {from_shop.name} to {to_shop.name}')
                else:
                    messages.success(request, f'Processed {transaction_type}: {quantity}x {item.name} from {from_shop.name}')

        elif action == 'bulk_import':
            csv_file = request.FILES.get('csv_file')
            if csv_file:
                created, updated, skipped, errors = process_csv_import(csv_file)
                if created > 0 or updated > 0:
                    msg = f'Imported {created} new items'
                    if updated > 0:
                        msg += f', updated {updated} existing items'
                    if skipped > 0:
                        msg += f', skipped {skipped} empty rows'
                    messages.success(request, msg)
                if errors:
                    messages.error(request, f'{len(errors)} errors occurred. First: {errors[0]}')
            else:
                messages.error(request, 'No file uploaded')

        return redirect('admin_manage')

    shop_filter = request.GET.get('shop', '')
    items = Item.objects.select_related('shop').all()
    if not user_is_admin and profile.assigned_shop:
        items = items.filter(shop=profile.assigned_shop)
    if shop_filter:
        items = items.filter(shop_id=shop_filter)

    items = items.order_by('name', 'shop__name')

    stock_transactions = StockTransaction.objects.select_related('item__shop', 'source_shop', 'target_shop').order_by('-created_at')[:50]
    if not user_is_admin and profile.assigned_shop:
        stock_transactions = stock_transactions.filter(source_shop=profile.assigned_shop)

    context = {
        'all_shops': all_shops,
        'items': items,
        'stock_transactions': stock_transactions,
        'shop_filter': shop_filter,
        'active_period': active_period,
        'page_title': 'Manage Inventory',
    }
    return render(request, 'stock_manager/admin_manage.html', context)


@shop_access_required
def settings_view(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin:
        messages.error(request, 'Only admins can access settings.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'open_period':
            name = request.POST.get('name', '').strip()
            period_type = request.POST.get('period_type', 'monthly')
            start_date = request.POST.get('start_date', '')
            end_date = request.POST.get('end_date', '')
            notes = request.POST.get('notes', '').strip()

            if not name or not start_date or not end_date:
                messages.error(request, 'Name, start date, and end date are required.')
            else:
                period = BusinessPeriod.objects.create(
                    name=name,
                    period_type=period_type,
                    start_date=start_date,
                    end_date=end_date,
                    notes=notes,
                )
                for item in Item.objects.select_related('shop').all():
                    PeriodOpeningStock.objects.create(
                        period=period,
                        item_name=item.name,
                        category=item.category,
                        shop=item.shop,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                    )
                messages.success(request, f'Business period "{name}" opened with {Item.objects.count()} items carried forward.')

        elif action == 'close_period':
            period_id = request.POST.get('period_id')
            try:
                period = BusinessPeriod.objects.get(id=period_id, is_closed=False)
                period.is_closed = True
                period.closed_at = timezone.now()
                period.closed_by = request.user
                period.save()
                messages.success(request, f'Business period "{period.name}" closed.')
            except BusinessPeriod.DoesNotExist:
                messages.error(request, 'Period not found or already closed.')

        elif action == 'add_shop':
            name = request.POST.get('name', '').strip()
            if not name:
                messages.error(request, 'Shop name is required.')
            elif Shop.objects.filter(name__iexact=name).exists():
                messages.error(request, f'Shop "{name}" already exists.')
            else:
                Shop.objects.create(name=name)
                messages.success(request, f'Shop "{name}" created.')

        elif action == 'delete_shop':
            shop_id = request.POST.get('shop_id')
            try:
                shop = Shop.objects.get(id=shop_id)
                if shop.items.exists() or shop.receipts.exists():
                    messages.error(request, f'Cannot delete "{shop.name}" — it has items or receipts linked to it.')
                else:
                    shop.delete()
                    messages.success(request, f'Shop "{shop.name}" deleted.')
            except Shop.DoesNotExist:
                messages.error(request, 'Shop not found.')

        elif action == 'add_user':
            username = request.POST.get('username', '').strip()
            email = request.POST.get('email', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            password = request.POST.get('password', '')
            role = request.POST.get('role', 'shop_user')
            shop_id = request.POST.get('shop_id')

            if not username or not password:
                messages.error(request, 'Username and password are required.')
            elif User.objects.filter(username=username).exists():
                messages.error(request, f'Username "{username}" is already taken.')
            else:
                user = User.objects.create(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                )
                user.set_password(password)
                user.save()
                assigned_shop = Shop.objects.filter(id=shop_id).first() if shop_id else None
                UserProfile.objects.create(
                    user=user,
                    role=role,
                    assigned_shop=assigned_shop,
                )
                messages.success(request, f'User "{username}" created.')

        elif action == 'reset_password':
            user_id = request.POST.get('user_id')
            new_password = request.POST.get('new_password', '')
            try:
                user = User.objects.get(id=user_id)
                if user.is_superuser:
                    messages.error(request, 'Cannot reset password for superuser.')
                elif not new_password or len(new_password) < 4:
                    messages.error(request, 'Password must be at least 4 characters.')
                else:
                    user.set_password(new_password)
                    user.save()
                    messages.success(request, f'Password for "{user.username}" has been reset.')
            except User.DoesNotExist:
                messages.error(request, 'User not found.')

        elif action == 'delete_user':
            user_id = request.POST.get('user_id')
            try:
                user = User.objects.get(id=user_id)
                if user.is_superuser:
                    messages.error(request, 'Cannot delete superuser.')
                else:
                    name = user.username
                    user.delete()
                    messages.success(request, f'User "{name}" deleted.')
            except User.DoesNotExist:
                messages.error(request, 'User not found.')

        elif action == 'save_company':
            company_name = request.POST.get('company_name', '').strip()
            if company_name:
                profile = CompanyProfile.get_profile()
                profile.company_name = company_name
                profile.address = request.POST.get('address', '').strip()
                profile.phone = request.POST.get('phone', '').strip()
                profile.email = request.POST.get('email', '').strip()
                profile.tax_id = request.POST.get('tax_id', '').strip()
                profile.receipt_footer = request.POST.get('receipt_footer', '').strip()
                profile.save()
                messages.success(request, 'Company profile updated.')
            else:
                messages.error(request, 'Company name is required.')

        elif action.startswith('save_landing'):
            content = LandingPageContent.get_content()
            data = content.data

            if action == 'save_landing_hero':
                data['hero'] = {
                    'title': request.POST.get('hero_title', '').strip(),
                    'subtitle': request.POST.get('hero_subtitle', '').strip(),
                }

            elif action == 'save_landing_about':
                features = request.POST.get('about_features', '').strip()
                data['about'] = {
                    'tag': request.POST.get('about_tag', '').strip(),
                    'heading': request.POST.get('about_heading', '').strip(),
                    'text_1': request.POST.get('about_text_1', '').strip(),
                    'text_2': request.POST.get('about_text_2', '').strip(),
                    'features': [f.strip() for f in features.split('\n') if f.strip()],
                }

            elif action == 'save_landing_products':
                data['products'] = {
                    'tag': request.POST.get('products_tag', '').strip(),
                    'heading': request.POST.get('products_heading', '').strip(),
                    'subtitle': request.POST.get('products_subtitle', '').strip(),
                }

            elif action == 'save_landing_why':
                cards_text = request.POST.get('why_cards', '').strip()
                cards = []
                for line in cards_text.split('\n'):
                    line = line.strip()
                    if line:
                        parts = [p.strip() for p in line.split('|')]
                        if len(parts) >= 3:
                            cards.append({'icon': parts[0], 'title': parts[1], 'text': parts[2]})
                data['why'] = {
                    'tag': request.POST.get('why_tag', '').strip(),
                    'heading': request.POST.get('why_heading', '').strip(),
                    'subtitle': request.POST.get('why_subtitle', '').strip(),
                    'cards': cards,
                }

            elif action == 'save_landing_contact':
                data['contact'] = {
                    'tag': request.POST.get('contact_tag', '').strip(),
                    'heading': request.POST.get('contact_heading', '').strip(),
                    'subtitle': request.POST.get('contact_subtitle', '').strip(),
                    'whatsapp': request.POST.get('contact_whatsapp', '').strip(),
                    'phone': request.POST.get('contact_phone', '').strip(),
                    'location': request.POST.get('contact_location', '').strip(),
                    'email': request.POST.get('contact_email', '').strip(),
                    'cta_heading': request.POST.get('cta_heading', '').strip(),
                    'cta_text': request.POST.get('cta_text', '').strip(),
                }

            elif action == 'save_landing_footer':
                data['footer'] = {
                    'brand': request.POST.get('footer_brand', '').strip(),
                    'description': request.POST.get('footer_description', '').strip(),
                }

            content.data = data
            content.save()
            messages.success(request, 'Landing page settings saved.')

        elif action == 'save_whatsapp':
            setting = WhatsAppSetting.get_profile()
            setting.phone_number = request.POST.get('phone_number', '').strip()
            setting.business_name = request.POST.get('business_name', '').strip()
            setting.greeting_message = request.POST.get('greeting_message', '').strip()
            setting.webhook_secret = request.POST.get('webhook_secret', '').strip()
            api_key = request.POST.get('api_key', '').strip()
            if api_key:
                setting.api_key = api_key
            setting.is_active = request.POST.get('is_active') == '1'
            setting.save()
            messages.success(request, 'WhatsApp settings saved.')

        return redirect('settings')

    periods = BusinessPeriod.objects.all()
    active_period = periods.filter(is_closed=False).first()
    opening_count = PeriodOpeningStock.objects.filter(period=active_period).count() if active_period else 0
    users = User.objects.filter(is_superuser=False).select_related('profile__assigned_shop').order_by('username')

    landing_content = LandingPageContent.get_content()
    landing_data = landing_content.data

    context = {
        'periods': periods,
        'active_period': active_period,
        'opening_count': opening_count,
        'users': users,
        'page_title': 'Settings',
        'landing_data': landing_data,
    }
    return render(request, 'stock_manager/settings.html', context)


@shop_access_required
def download_template(request):
    csv = __import__('csv')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="warehouse_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(['Item', 'Category', 'Stocked In', 'Stocked Out', 'Balance', 'Avg Unit Cost', 'Total Cost'])
    writer.writerow(['Sugar', 'Food', 10, '', '', 1500.00, 15000.00])
    writer.writerow(['Rice', 'Food', 5, '', '', 2500.00, 12500.00])
    writer.writerow(['Soap', 'Household', 20, '', '', 800.00, 16000.00])
    return response


@shop_access_required
def download_bulk_transfer_template(request):
    csv = __import__('csv')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="warehouse_bulk_transfer_template.csv"'
    writer = csv.writer(response)
    writer.writerow(['Item Name', 'Target Shop', 'Quantity'])
    writer.writerow(['Sugar', 'Blantyre 1', 5])
    writer.writerow(['Rice', 'Lilongwe', 3])
    writer.writerow(['Soap', 'Mzuzu', 10])
    return response


@shop_access_required
def financial_report(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    all_shops = Shop.objects.all()
    if not user_is_admin and profile.assigned_shop:
        all_shops = Shop.objects.filter(id=profile.assigned_shop.id)

    date_filter = request.GET.get('date', '')
    shop_filter = request.GET.get('shop', '')

    active_period = get_active_period()
    opening_stock = get_period_opening_stock(active_period) if active_period else {}

    shop_financials = []
    grand_stocked_in = 0
    grand_sold = 0
    grand_unsold = 0
    grand_opening_value = 0

    shops = all_shops
    if shop_filter:
        shops = shops.filter(id=shop_filter)

    for shop in shops:
        items = Item.objects.filter(shop=shop)
        stocked_in_value = 0
        sold_value = 0
        unsold_value = 0
        opening_value = 0

        for item in items:
            sold_qty = Sale.objects.filter(item=item).aggregate(total=Sum('quantity_sold'))['total'] or 0
            total_stocked_qty = item.quantity + sold_qty

            item_stocked_in = total_stocked_qty * float(item.unit_price)
            item_sold = sold_qty * float(item.unit_price)
            item_unsold = item.quantity * float(item.unit_price)

            stocked_in_value += item_stocked_in
            sold_value += item_sold
            unsold_value += item_unsold

            open_data = opening_stock.get(item.name.lower(), {})
            opening_value += open_data.get('quantity', 0) * open_data.get('unit_price', 0)

        shop_financials.append({
            'shop': shop,
            'stocked_in_value': stocked_in_value,
            'sold_value': sold_value,
            'unsold_value': unsold_value,
            'opening_value': opening_value,
        })

        grand_stocked_in += stocked_in_value
        grand_sold += sold_value
        grand_unsold += unsold_value
        grand_opening_value += opening_value

    context = {
        'shop_financials': shop_financials,
        'grand_stocked_in': grand_stocked_in,
        'grand_sold': grand_sold,
        'grand_unsold': grand_unsold,
        'grand_opening_value': grand_opening_value,
        'all_shops': all_shops,
        'selected_shop': shop_filter,
        'date_filter': date_filter,
        'active_period': active_period,
        'page_title': 'Financial Report',
    }
    return render(request, 'stock_manager/financial_report.html', context)


def landing_view(request):
    company = CompanyProfile.get_profile()
    landing = LandingPageContent.get_content()
    return render(request, 'stock_manager/landing.html', {'company': company, 'landing': landing})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    from django.contrib.auth.forms import AuthenticationForm
    from django.contrib.auth import login as auth_login
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        auth_login(request, form.get_user())
        next_url = request.GET.get('next', 'dashboard')
        return redirect(next_url)
    company = CompanyProfile.get_profile()
    return render(request, 'stock_manager/login.html', {'form': form, 'company': company})


def logout_view(request):
    auth_logout(request)
    next_url = request.GET.get('next', 'login')
    return redirect(next_url)


@login_required
def change_password(request):
    if request.method == 'POST':
        current = request.POST.get('current_password', '')
        new_pass = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')

        if not request.user.check_password(current):
            messages.error(request, 'Current password is incorrect.')
        elif not new_pass or len(new_pass) < 4:
            messages.error(request, 'New password must be at least 4 characters.')
        elif new_pass != confirm:
            messages.error(request, 'Passwords do not match.')
        else:
            request.user.set_password(new_pass)
            request.user.save()
            messages.success(request, 'Your password has been changed.')
            return redirect('dashboard')

    return redirect('dashboard')

from email import errors
import json
from decimal import Decimal
import math
from datetime import date
from collections import defaultdict

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.db.models import Q, Sum, F, ExpressionWrapper, DecimalField
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth import get_user_model
User = get_user_model()
from django.db import connection
from django.utils import timezone

from .models import (
    Shop, Item, Sale, StockTransaction, UserProfile, Notification,
    PERMISSION_CHOICES, LandingPageContent, Category, get_company_profile,
)

from .middleware import shop_access_required
from .communication_views import _notify_shop_users


# ========================
# USER HELPERS
# ========================

def get_user_profile(user):
    if user.is_superuser:
        return None
    try:
        return user.user_profile
    except UserProfile.DoesNotExist:
        return None


def filter_items_by_user(user, qs):
    profile = get_user_profile(user)
    if profile and not profile.is_admin and profile.assigned_shop:
        return qs.filter(shop=profile.assigned_shop)
    return qs


# ========================
# STOCK LOGIC
# ========================

def auto_deduct_from_warehouse(item_name, shop, quantity, unit_price, category=''):
    warehouse = Shop.objects.filter(name='Warehouse').first()

    if not warehouse or shop == warehouse:
        return

    warehouse_item = Item.objects.filter(
        name__iexact=item_name,
        shop=warehouse
    ).first()

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



# ========================
# INVENTORY AGGREGATION
# ========================

def get_shop_inventory_data(opening_stock=None):
    if opening_stock is None:
        opening_stock = {}

    # ✅ optimized query
    items = Item.objects.select_related('shop').all()

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
        'shop_count': 0,
        'total_unit_price': 0,
    })

    for item in items:
        key = item.name.lower()

        open_data = opening_stock.get(key, {})
        open_qty = open_data.get('quantity', 0)
        open_price = open_data.get('unit_price', 0)

        if grouped[key]['name'] == '':
            grouped[key].update({
                'name': item.name,
                'category': item.category,
                'opening_qty': open_qty,
                'opening_value': open_qty * open_price,
            })

        # ✅ SAFE shop handling
        shop_name = item.shop.name if item.shop else "Unknown Shop"

        # ✅ aggregate sold qty
        sold_qty = Sale.objects.filter(item=item).aggregate(
            total=Sum('quantity_sold')
        )['total'] or 0

        stocked_in = item.quantity + sold_qty

        # ✅ FIXED (was wrongly indented before)
        grouped[key]['shops'][shop_name] = {
            'stocked_in': stocked_in,
            'stocked_out': sold_qty,
            'balance': item.quantity,
            'unit_price': float(item.unit_price),
            'value': float(item.quantity * item.unit_price),
            'is_low_stock': item.is_low_stock,
        }

        grouped[key]['total_stocked_in'] += stocked_in
        grouped[key]['total_stocked_out'] += sold_qty
        grouped[key]['total_qty'] += item.quantity
        grouped[key]['total_value'] += float(item.quantity * item.unit_price)
        grouped[key]['shop_count'] += 1
        grouped[key]['total_unit_price'] += float(item.unit_price)

    # ========================
    # FINAL CALCULATIONS
    # ========================

    for item_data in grouped.values():
        if item_data['total_qty'] > 0:
            item_data['avg_unit_price'] = (
                item_data['total_value'] / item_data['total_qty']
            )
        else:
            item_data['avg_unit_price'] = 0

    return sorted(grouped.values(), key=lambda x: x['name'])


# ========================
# BUSINESS PERIOD
# ========================

def get_active_period():
    return None


def get_period_opening_stock(period, shop=None):
    return {}


# ========================
# DASHBOARD VIEW
# ========================

@login_required
def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('landing')

    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin and profile and profile.assigned_shop:
        return redirect('shop_dashboard', shop_slug=profile.assigned_shop.slug)

    all_shops = Shop.objects.all()
    active_period = get_active_period()
    opening_stock = get_period_opening_stock(active_period) if active_period else {}

    if user_is_admin or request.user.is_superuser:
        inventory_data = get_shop_inventory_data(opening_stock)
        shop_items = Item.objects.all()
    else:
        inventory_data = {}
        shop_items = Item.objects.filter(shop=profile.assigned_shop) if profile and profile.assigned_shop else Item.objects.none()
    total_items = shop_items.count()
    total_stock_value = sum(float(item.total_value) for item in shop_items)
    low_stock_items = shop_items.filter(quantity__lt=5)
    low_stock_count = low_stock_items.count()

    total_sales = Sale.objects.filter(item__in=shop_items)
    total_sales_count = total_sales.count()
    total_sales_amount = total_sales.aggregate(total=Sum('total_amount'))['total'] or 0

    if user_is_admin or request.user.is_superuser:
        shop_stock_data = []
        shop_value_data = []
        for s in all_shops:
            s_items = Item.objects.filter(shop=s)
            total_qty = sum(item.quantity for item in s_items)
            total_val = sum(float(item.total_value) for item in s_items)
            shop_stock_data.append({'shop': s.name, 'quantity': total_qty})
            shop_value_data.append({'value': total_val})

        shop_sales_data = []
        for s in all_shops:
            s_items = Item.objects.filter(shop=s)
            s_sales = Sale.objects.filter(item__in=s_items)
            s_count = s_sales.count()
            s_amount = s_sales.aggregate(total=Sum('total_amount'))['total'] or 0
            shop_sales_data.append({
                'shop': s,
                'count': s_count,
                'total_amount': float(s_amount),
            })
    else:
        shop_stock_data = []
        shop_value_data = []
        shop_sales_data = []

    category_data = defaultdict(int)
    for item in shop_items:
        cat = item.category or 'Uncategorized'
        category_data[cat] += item.quantity

    category_labels = list(category_data.keys())
    category_values = list(category_data.values())

    context = {
        'inventory_data': inventory_data,
        'total_items': total_items,
        'total_stock_value': total_stock_value,
        'low_stock_items': low_stock_items,
        'low_stock_count': low_stock_count,
        'total_sales_count': total_sales_count,
        'total_sales_amount': total_sales_amount,
        'all_shops': all_shops,
        'active_period': active_period,
        'page_title': 'Main Inventory Dashboard',
        'shop_stock_data_json': json.dumps(shop_stock_data),
        'shop_value_data_json': json.dumps(shop_value_data),
        'category_labels_json': json.dumps(category_labels),
        'category_values_json': json.dumps(category_values),
        'shop_sales_data': shop_sales_data,
    }

    return render(request, 'stock_manager/dashboard.html', context)


# =========================
# BULK IMPORT VIEW
# =========================
@shop_access_required
def dashboard_bulk_import(request):
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')

        if not csv_file:
            messages.error(request, 'No file uploaded')
            return redirect('dashboard_bulk_import')

        created, updated, skipped, errors = process_csv_import(csv_file)

        msg_parts = []
        if created > 0:
            msg_parts.append(f'Imported {created} new items')
        if updated > 0:
            msg_parts.append(f'updated {updated} existing items')
        if skipped > 0:
            msg_parts.append(f'skipped {skipped} empty rows')

        if msg_parts:
            messages.success(request, ', '.join(msg_parts))
        if errors:
            messages.error(request, f'{len(errors)} errors occurred. First: {errors[0]}')

        return redirect('dashboard_bulk_import')

    return redirect('dashboard')


# =========================
# POINT OF SALE (POS)
# =========================

@shop_access_required
def point_of_sale(request, shop_slug):
    shop = Shop.objects.filter(slug=shop_slug).first()
    if not shop:
        shop = get_object_or_404(Shop, name__iexact=shop_slug)

    is_warehouse = shop.name == 'Warehouse'

    # Inline shop check (second layer after decorator)
    if not request.user.is_superuser:
        profile = get_user_profile(request.user)
        if not profile or not profile.assigned_shop or profile.assigned_shop != shop:
            return HttpResponseForbidden("Access Denied")

    if request.method == 'POST':
        try:
            # =========================
            # HANDLE DATA INPUT
            # =========================
            if request.content_type == 'application/json':
                try:
                    data = json.loads(request.body)
                except Exception:
                    return JsonResponse({'error': 'Invalid JSON body'}, status=400)
            else:
                data = request.POST

            action = data.get('action')

            # =========================
            # COMPLETE SALE
            # =========================
            if action == 'complete_sale':

                raw_items = data.get('items', '[]')

                try:
                    items_data = json.loads(raw_items) if isinstance(raw_items, str) else raw_items
                except Exception:
                    return JsonResponse({'error': 'Invalid items data'}, status=400)

                if not items_data:
                    return JsonResponse({'error': 'No items in cart'}, status=400)

                customer_name = data.get('customer_name', '').strip()

                try:
                    amount_received = Decimal(str(data.get('amount_received', 0)))
                except Exception:
                    return JsonResponse({'error': 'Invalid amount received'}, status=400)

                subtotal = Decimal('0.00')
                line_items = []
                errors = []

                # =========================
                # VALIDATE ITEMS
                # =========================
                for line in items_data:
                    item_id = line.get('item_id')

                    try:
                        qty = int(line.get('quantity', 0))
                    except (ValueError, TypeError):
                        continue

                    if not item_id or qty < 1:
                        continue

                    try:
                        item = Item.objects.get(id=item_id, shop=shop)
                    except Item.DoesNotExist:
                        errors.append(f'Item id {item_id} not found')
                        continue

                    if qty > item.quantity:
                        errors.append(
                            f'Not enough stock for {item.name}: have {item.quantity}, need {qty}'
                        )
                        continue

                    unit_price = item.unit_price
                    line_total = unit_price * qty

                    subtotal += line_total

                    line_items.append({
                        'item': item,
                        'qty': qty,
                        'unit_price': unit_price,
                        'total': line_total
                    })

                if errors:
                    return JsonResponse({'error': '; '.join(errors)}, status=400)

                total = subtotal

                if amount_received < total:
                    return JsonResponse({
                        'error': f'Amount received (MWK {amount_received:.2f}) is less than total (MWK {total:.2f})'
                    }, status=400)

                change = amount_received - total

                # =========================
                # SAVE SALES + UPDATE STOCK
                # =========================
                for entry in line_items:
                    item = entry['item']
                    qty = entry['qty']

                    Sale.objects.create(
                        item=item,
                        quantity_sold=qty,
                        unit_price=entry['unit_price'],
                        total_amount=entry['total'],
                    )

                    item.quantity -= qty
                    item.save()

                return JsonResponse({
                    'success': True,
                    'change': float(change),
                    'message': 'Sale completed successfully',
                })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Invalid request'}, status=400)


# =========================
# SALES HISTORY
# =========================

@shop_access_required
def sales_history(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    sales = Sale.objects.select_related('item', 'item__shop').order_by('-sold_at')[:100]

    if not user_is_admin and profile.assigned_shop:
        sales = sales.filter(item__shop=profile.assigned_shop)

    q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    selected_shop = request.GET.get('shop', '')

    if q:
        sales = sales.filter(Q(item__name__icontains=q))

    if date_from:
        sales = sales.filter(sold_at__date__gte=date_from)

    if date_to:
        sales = sales.filter(sold_at__date__lte=date_to)

    if selected_shop:
        sales = sales.filter(item__shop_id=selected_shop)

    total_sales = sales.aggregate(total=Sum('total_amount'))['total'] or 0
    all_shops = Shop.objects.all().order_by('name')
    if not user_is_admin and profile.assigned_shop:
        all_shops = Shop.objects.filter(id=profile.assigned_shop.id)

    context = {
        'sales': sales,
        'total_sales': total_sales,
        'query': q,
        'date_from': date_from,
        'date_to': date_to,
        'selected_shop': selected_shop,
        'user_is_admin': user_is_admin,
        'all_shops': all_shops,
        'page_title': 'Sales History',
    }

    return render(request, 'stock_manager/sales_history.html', context)


# =========================
# SALE DETAIL
# =========================

@shop_access_required
def sale_detail(request, sale_id):
    sale = get_object_or_404(Sale.objects.select_related('item', 'item__shop'), id=sale_id)

    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if (
        not user_is_admin and
        profile and profile.assigned_shop and
        sale.item.shop != profile.assigned_shop
    ):
        messages.error(request, 'Access denied.')
        return redirect('sales_history')

    return render(request, 'stock_manager/sale_detail.html', {
        'sale': sale,
        'page_title': f'Sale #{sale.id}',
    })


@shop_access_required
def delete_receipt(request, receipt_id):
    messages.error(request, 'Receipt management is not available.')
    return redirect('sales_history')


# =========================
# EXPORT CSV (INVENTORY)
# =========================

@shop_access_required
def export_csv(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="inventory_export.csv"'

    import csv
    writer = csv.writer(response)

    writer.writerow([
        'Item Name', 'Category', 'Shop',
        'Quantity', 'Unit Price',
        'Total Value', 'Low Stock'
    ])

    items = Item.objects.select_related('shop').order_by('name', 'shop__name')

    if not user_is_admin and profile.assigned_shop:
        items = items.filter(shop=profile.assigned_shop)

    for item in items:
        writer.writerow([
            item.name,
            item.category,
            item.shop.name if item.shop else '',
            item.quantity,
            item.unit_price,
            item.total_value,
            'YES' if item.is_low_stock else 'NO',
        ])

    return response


# =========================



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
        header_map = {
            'item_name': 0,
        }
        if col_count > 1:
            header_map['category'] = 1
        if col_count > 2:
            header_map['quantity'] = 2
        if col_count > 3:
            header_map['unit_price'] = 3

    created = 0
    updated = 0
    skipped = 0
    errors = []
    item_col = header_map.get('item_name', 0)
    shop_col = header_map.get('shop_name', None)
    cat_col = header_map.get('category', 1)
    qty_col = header_map.get('quantity', 2)
    price_col = header_map.get('unit_price', 3)

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
    # Fallback: try slug first, then name match for existing shops with null slugs
    shop = Shop.objects.filter(slug=shop_slug).first()
    if not shop:
        shop = get_object_or_404(Shop, name__iexact=shop_slug)

    if not request.user.is_superuser:
        profile = get_user_profile(request.user)
        if not profile or not profile.assigned_shop or profile.assigned_shop != shop:
            return HttpResponseForbidden("Access Denied")

    is_warehouse = shop.name == 'Warehouse'
    all_shops = Shop.objects.all()
    other_shops = all_shops.exclude(name='Warehouse') if is_warehouse else []
    active_period = get_active_period()
    opening_stock = get_period_opening_stock(active_period) if active_period else {}

    items = Item.objects.filter(shop=shop).order_by('name')

    total_items = items.count()
    total_qty = items.aggregate(total=Sum('quantity'))['total'] or 0
    total_value = sum(float(item.total_value) for item in items)
    low_stock_items = items.filter(quantity__lt=5)

    opening_by_item = {}
    stock_out_by_item = {}
    total_value_by_item = {}
    warehouse_stocked_in = 0
    warehouse_stocked_out = 0

    # ---- POST: Warehouse stock transfer ----
    if request.method == 'POST' and is_warehouse:
        action = request.POST.get('action')
        if action == 'transfer_item':
            item_id = request.POST.get('item_id')
            target_shop_id = request.POST.get('target_shop')
            try:
                qty = int(request.POST.get('quantity_transfer', 0))
            except (ValueError, TypeError):
                qty = 0

            if not item_id or not target_shop_id or qty <= 0:
                messages.error(request, 'Missing or invalid transfer fields.')
                return redirect('shop_dashboard', shop_slug=shop_slug)

            try:
                source_item = Item.objects.get(id=item_id, shop=shop)
            except Item.DoesNotExist:
                messages.error(request, 'Item not found in warehouse.')
                return redirect('shop_dashboard', shop_slug=shop_slug)

            if qty > source_item.quantity:
                messages.error(request, f'Not enough stock. Available: {source_item.quantity}, Requested: {qty}')
                return redirect('shop_dashboard', shop_slug=shop_slug)

            try:
                target_shop = Shop.objects.get(id=target_shop_id)
            except Shop.DoesNotExist:
                messages.error(request, 'Target shop not found.')
                return redirect('shop_dashboard', shop_slug=shop_slug)

            # Deduct from warehouse
            source_item.quantity -= qty
            source_item.save()

            # Add to target shop (create item if missing)
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

            # Record the transfer
            StockTransaction.objects.create(
                item=source_item,
                source_shop=shop,
                target_shop=target_shop,
                quantity=qty,
                transaction_type='transfer',
                reason=f'Warehouse transfer to {target_shop.name}',
            )

            # Notify users assigned to the target shop (with real-time WebSocket push)
            _notify_shop_users(
                target_shop,
                request.user,
                'Stock Transfer Received',
                f'You have received {qty} × {source_item.name} from {shop.name}.',
            )

            messages.success(request, f'Transfer successfully done to {target_shop.name}')
            return redirect('shop_dashboard', shop_slug=shop_slug)

    # ---- GET: Build context ----
    for item in items:
        key = item.name.lower()
        open_data = opening_stock.get(key, {})
        opening_by_item[item.id] = open_data.get('quantity', 0)

        sold_qty = Sale.objects.filter(item=item).aggregate(
            total=Sum('quantity_sold')
        )['total'] or 0
        stock_out_by_item[item.id] = sold_qty
        total_value_by_item[item.id] = float(item.total_value)

        if is_warehouse:
            warehouse_stocked_in += item.quantity + sold_qty
            warehouse_stocked_out += sold_qty

    today_total = 0
    shop_sales = Sale.objects.none()
    if not is_warehouse:
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        shop_sales_qs = Sale.objects.filter(item__in=items).select_related('item')
        today_sales = shop_sales_qs.filter(sold_at__gte=today_start)
        today_total = today_sales.aggregate(total=Sum('total_amount'))['total'] or 0
        shop_sales = shop_sales_qs.order_by('-sold_at')[:50]

    # ---- Cross-shop inventory overview (warehouse only) ----
    cross_shop_items = []
    cross_shop_shops = []
    cross_shop_categories = []
    if is_warehouse:
        non_warehouse_shops = Shop.objects.exclude(name__iexact='warehouse').order_by('name')
        cross_shop_shops = [s.name for s in non_warehouse_shops]
        all_item_names = (
            Item.objects.exclude(shop__name__iexact='warehouse')
            .values('name')
            .distinct()
            .order_by('name')
        )
        seen_categories = set()
        for entry in all_item_names:
            name = entry['name']
            first_item = Item.objects.filter(name__iexact=name).exclude(shop__name__iexact='warehouse').first()
            category = first_item.category if first_item else ''
            row = {'name': name, 'category': category, 'shops': {}}
            for s in non_warehouse_shops:
                item = Item.objects.filter(name__iexact=name, shop=s).first()
                row['shops'][s.name] = item.quantity if item else 0
            cross_shop_items.append(row)
            if category and category not in seen_categories:
                seen_categories.add(category)
        cross_shop_categories = sorted(seen_categories)

    stock_transactions = StockTransaction.objects.filter(
        Q(source_shop=shop) | Q(target_shop=shop)
    ).select_related('item', 'source_shop', 'target_shop').order_by('-created_at')[:50]
    total_transactions = stock_transactions.count()
    total_transferred_qty = stock_transactions.aggregate(
        total=Sum('quantity')
    )['total'] or 0

    context = {
        'shop': shop,
        'is_warehouse': is_warehouse,
        'total_items': total_items,
        'total_qty': total_qty,
        'total_value': total_value,
        'today_total': today_total,
        'low_stock_items': low_stock_items,
        'warehouse_stocked_in': warehouse_stocked_in,
        'warehouse_stocked_out': warehouse_stocked_out,
        'items': items,
        'other_shops': other_shops,
        'opening_by_item': opening_by_item,
        'stock_out_by_item': stock_out_by_item,
        'total_value_by_item': total_value_by_item,
        'active_period': active_period,
        'total_transactions': total_transactions,
        'total_transferred_qty': total_transferred_qty,
        'stock_transactions': stock_transactions,
        'shop_sales': shop_sales,
        'cross_shop_items': cross_shop_items if is_warehouse else [],
        'cross_shop_shops': cross_shop_shops if is_warehouse else [],
        'cross_shop_categories': cross_shop_categories if is_warehouse else [],
        'page_title': f'{shop.name} Dashboard',
    }

    return render(request, 'stock_manager/shop_dashboard.html', context)


@shop_access_required
def shop_inventory(request, shop_slug):
    shop = Shop.objects.filter(slug=shop_slug).first()
    if not shop:
        shop = get_object_or_404(Shop, name__iexact=shop_slug)
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin and profile and profile.assigned_shop and profile.assigned_shop != shop:
        messages.error(request, 'Access denied to this shop.')
        return redirect('shop_dashboard', shop_slug=profile.assigned_shop.slug)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_item':
            item_name = request.POST.get('item_name', '').strip()
            category = request.POST.get('category', '').strip()
            try:
                quantity = int(request.POST.get('quantity', 0))
                unit_price = float(request.POST.get('unit_price', 0))
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity or price')
                return redirect('shop_inventory', shop_slug=shop_slug)

            if item_name:
                existing = Item.objects.filter(name__iexact=item_name, shop=shop).first()
                if existing:
                    existing.quantity += quantity
                    existing.unit_price = unit_price
                    if category:
                        existing.category = category
                    existing.save()
                    messages.success(request, f'Added {quantity}x "{item_name}" (now {existing.quantity})')
                else:
                    Item.objects.create(name=item_name, shop=shop, quantity=quantity, unit_price=unit_price, category=category)
                    messages.success(request, f'Added "{item_name}"')
            return redirect('shop_inventory', shop_slug=shop_slug)

        elif action == 'edit_item':
            item_id = request.POST.get('item_id')
            item_name = request.POST.get('item_name', '').strip()
            category = request.POST.get('category', '').strip()
            try:
                item = Item.objects.get(id=item_id, shop=shop)
                item.name = item_name or item.name
                item.quantity = int(request.POST.get('quantity', 0))
                item.unit_price = float(request.POST.get('unit_price', 0))
                item.category = category
                item.save()
                messages.success(request, f'Updated "{item.name}"')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            return redirect('shop_inventory', shop_slug=shop_slug)

        elif action == 'delete_item':
            item_id = request.POST.get('item_id')
            try:
                item = Item.objects.get(id=item_id, shop=shop)
                name = item.name
                item.delete()
                messages.success(request, f'Deleted "{name}"')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            return redirect('shop_inventory', shop_slug=shop_slug)

    items = Item.objects.filter(shop=shop).order_by('name')
    total_items = items.count()
    total_value = sum(float(item.total_value) for item in items)

    return render(request, 'stock_manager/shop_inventory.html', {
        'shop': shop,
        'items': items,
        'total_items': total_items,
        'total_value': total_value,
        'page_title': f'{shop.name} Inventory',
    })





@login_required
def settings_view(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin:
        messages.error(request, 'Only admins can access settings.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_shop':
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
                if shop.items.exists():
                    messages.error(request, f'Cannot delete "{shop.name}" — it has items linked to it.')
                else:
                    shop.delete()
                    messages.success(request, f'Shop "{shop.name}" deleted.')
            except Shop.DoesNotExist:
                messages.error(request, 'Shop not found.')

        elif action == 'edit_shop':
            shop_id = request.POST.get('shop_id')
            name = request.POST.get('name', '').strip()
            if not shop_id or not name:
                messages.error(request, 'Shop and new name are required.')
            else:
                try:
                    shop = Shop.objects.get(id=shop_id)
                    if Shop.objects.filter(name__iexact=name).exclude(id=shop.id).exists():
                        messages.error(request, f'Shop "{name}" already exists.')
                    else:
                        shop.name = name
                        shop.save()
                        messages.success(request, f'Shop renamed to "{name}".')
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
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    password=password,
                )
                assigned_shop = Shop.objects.filter(id=shop_id).first() if shop_id else None
                profile, created = UserProfile.objects.get_or_create(user=user)
                profile.role = role
                profile.assigned_shop = assigned_shop
                profile.save()
                messages.success(request, f'User "{username}" created.')

        elif action == 'reset_password':
            user_id = request.POST.get('user_id')
            new_password = request.POST.get('new_password', '')
            if not user_id or not new_password or len(new_password) < 4:
                messages.error(request, 'User ID and password (min 4 chars) are required.')
            else:
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

        elif action == 'edit_user':
            user_id = request.POST.get('user_id')
            username = request.POST.get('username', '').strip()
            email = request.POST.get('email', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            role = request.POST.get('role', 'shop_user')
            shop_id = request.POST.get('shop_id')
            if not user_id or not username:
                messages.error(request, 'User and username are required.')
            else:
                try:
                    user = User.objects.get(id=user_id)
                    if user.is_superuser:
                        messages.error(request, 'Cannot edit superuser.')
                    elif User.objects.filter(username=username).exclude(id=user.id).exists():
                        messages.error(request, f'Username "{username}" is already taken.')
                    else:
                        user.username = username
                        user.email = email
                        user.first_name = first_name
                        user.last_name = last_name
                        new_password = request.POST.get('password', '').strip()
                        if new_password:
                            if len(new_password) < 4:
                                messages.error(request, 'Password must be at least 4 characters.')
                                return redirect('settings')
                            user.set_password(new_password)
                        user.save()
                        profile, created = UserProfile.objects.get_or_create(user=user)
                        profile.role = role
                        profile.assigned_shop = Shop.objects.filter(id=shop_id).first() if shop_id else None
                        perms = request.POST.getlist('permissions')
                        profile.permissions = perms if perms else None
                        profile.save()
                        messages.success(request, f'User "{username}" updated.')
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
            company = get_company_profile()
            company.company_name = request.POST.get('company_name', '').strip() or 'My Store'
            company.address = request.POST.get('address', '').strip()
            company.phone = request.POST.get('phone', '').strip()
            company.email = request.POST.get('email', '').strip()
            company.save()
            messages.success(request, 'Company profile saved.')

        elif action == 'save_landing':
            landing = LandingPageContent.objects.first()
            if not landing:
                landing = LandingPageContent.objects.create(data={})

            data = {
                'hero': {
                    'title': request.POST.get('hero_title', ''),
                    'subtitle': request.POST.get('hero_subtitle', ''),
                },
                'about': {
                    'tag': request.POST.get('about_tag', ''),
                    'heading': request.POST.get('about_heading', ''),
                    'text_1': request.POST.get('about_text_1', ''),
                    'text_2': request.POST.get('about_text_2', ''),
                    'features': [f.strip() for f in request.POST.get('about_features', '').split('\n') if f.strip()],
                },
                'products': {
                    'tag': request.POST.get('products_tag', ''),
                    'heading': request.POST.get('products_heading', ''),
                    'subtitle': request.POST.get('products_subtitle', ''),
                },
                'why': {
                    'tag': request.POST.get('why_tag', ''),
                    'heading': request.POST.get('why_heading', ''),
                    'subtitle': request.POST.get('why_subtitle', ''),
                    'cards': [
                        {'icon': request.POST.get(f'why_icon_{i}', ''), 'title': request.POST.get(f'why_title_{i}', ''), 'text': request.POST.get(f'why_text_{i}', '')}
                        for i in range(1, 6)
                        if request.POST.get(f'why_title_{i}', '')
                    ],
                },
                'contact': {
                    'tag': request.POST.get('contact_tag', ''),
                    'heading': request.POST.get('contact_heading', ''),
                    'subtitle': request.POST.get('contact_subtitle', ''),
                    'whatsapp': request.POST.get('contact_whatsapp', ''),
                    'phone': request.POST.get('contact_phone', ''),
                    'location': request.POST.get('contact_location', ''),
                    'email': request.POST.get('contact_email', ''),
                    'cta_heading': request.POST.get('contact_cta_heading', ''),
                    'cta_text': request.POST.get('contact_cta_text', ''),
                },
                'footer': {
                    'brand': request.POST.get('footer_brand', ''),
                    'description': request.POST.get('footer_description', ''),
                },
            }

            landing.data = data

            if request.FILES.get('landing_image'):
                landing.image = request.FILES['landing_image']

            landing.save()
            messages.success(request, 'Landing page updated.')

        return redirect('settings')

    # Ensure all non-superuser users have a UserProfile to prevent 500 on template access
    for user in User.objects.filter(is_superuser=False, user_profile__isnull=True):
        UserProfile.objects.create(user=user)

    users = User.objects.filter(is_superuser=False).select_related('user_profile__assigned_shop').order_by('username')

    landing = LandingPageContent.objects.first()
    landing_data = landing.data if landing and landing.data else {}

    defaults = {
        'hero': {'title': 'Welcome to VARNISHIELA BEAUTY PALOUR', 'subtitle': 'Your trusted destination for authentic hygiene and beauty products.'},
        'about': {'tag': 'About Us', 'heading': 'Who We Are', 'text_1': 'VARNISHIELA BEAUTY PALOUR is a trusted name in personal care and beauty products. We are passionate about helping you look and feel your best with high-quality hygiene essentials at affordable prices.', 'text_2': 'From body lotions to premium perfumes, every product in our collection is carefully selected to meet the highest standards of quality and safety.', 'features': ['100% Authentic Products', 'Affordable Prices', 'Fast & Reliable Delivery', 'Customer Happiness Guaranteed']},
        'products': {'tag': 'Our Collection', 'heading': 'Shop by Category', 'subtitle': 'Explore our range of premium hygiene and beauty products.'},
        'why': {'tag': 'Why Choose Us', 'heading': 'We Care About Your Confidence', 'subtitle': "Here's why our customers trust us", 'cards': [
            {'icon': 'fa-medal', 'title': 'High Quality', 'text': 'Every product meets strict quality standards.'},
            {'icon': 'fa-wallet', 'title': 'Affordable Prices', 'text': "Premium care doesn't have to break the bank."},
            {'icon': 'fa-handshake', 'title': 'Trusted Shop', 'text': 'Established reputation for honesty and reliability.'},
            {'icon': 'fa-rocket', 'title': 'Fast Service', 'text': 'Quick processing and timely delivery every time.'},
            {'icon': 'fa-heart', 'title': 'Customer Satisfaction', 'text': 'Your happiness is our top priority.'},
        ]},
        'contact': {'tag': 'Get In Touch', 'heading': 'Contact Us', 'subtitle': "We'd love to hear from you", 'whatsapp': '+265883994035', 'phone': '+265883994035', 'location': 'Blantyre, Malawi', 'email': 'info@varnishiela.com', 'cta_heading': 'Ready to Glow?', 'cta_text': 'Place your order today and experience the VARNISHIELA difference. Fast delivery across Malawi.'},
        'footer': {'brand': 'VARNISHIELA', 'description': 'Your trusted beauty & hygiene store. Quality products for a confident you.'},
    }

    for section, fields in defaults.items():
        if section not in landing_data:
            landing_data[section] = fields
        else:
            for key, value in fields.items():
                if key not in landing_data[section]:
                    landing_data[section][key] = value

    context = {
        'users': users,
        'page_title': 'Settings',
        'permission_choices': PERMISSION_CHOICES,
        'company': get_company_profile(),
        'landing_data': landing_data,
        'landing_has_image': bool(landing and landing.image),
    }
    return render(request, 'stock_manager/settings.html', context)


@shop_access_required


def download_template(request):
    csv = __import__('csv')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="warehouse_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(['Item Name', 'Category', 'Quantity', 'Unit Price'])
    writer.writerow(['Sugar', 'Food', 50, 1500.00])
    writer.writerow(['Rice', 'Food', 30, 2500.00])
    writer.writerow(['Soap', 'Household', 100, 800.00])
    writer.writerow(['Cooking Oil', 'Food', 20, 4500.00])
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




@ensure_csrf_cookie
def landing_view(request):
    shops = Shop.objects.exclude(name__iexact='warehouse').order_by('name')
    items = Item.objects.exclude(shop__name__iexact='warehouse').select_related('shop').order_by('shop__name', 'name')
    landing = LandingPageContent.objects.first()

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'stock_manager/landing.html', {'items': items, 'shops': shops, 'landing': landing})




def logout_view(request):
    auth_logout(request)
    next_url = request.GET.get('next', 'landing')
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


# =========================
# CATEGORY MANAGEMENT
# =========================

@login_required
def manage_categories(request):
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin
    has_perm = user_is_admin or (profile and profile.permissions and 'settings' in profile.permissions)
    if not has_perm:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_category':
            name = request.POST.get('name', '').strip()
            if name:
                Category.objects.get_or_create(name=name)
                if request.GET.get('ajax'):
                    return JsonResponse({'status': 'ok', 'name': name})
                messages.success(request, f'Category "{name}" added.')
            else:
                if request.GET.get('ajax'):
                    return JsonResponse({'error': 'Name is required'}, status=400)
                messages.error(request, 'Category name is required.')
        elif action == 'delete_category':
            cat_id = request.POST.get('category_id')
            try:
                cat = Category.objects.get(id=cat_id)
                name = cat.name
                cat.delete()
                messages.success(request, f'Category "{name}" deleted.')
            except Category.DoesNotExist:
                messages.error(request, 'Category not found.')
        return redirect('manage_categories')

    categories = Category.objects.all()
    return render(request, 'stock_manager/manage_categories.html', {
        'categories': categories,
        'page_title': 'Manage Categories',
    })

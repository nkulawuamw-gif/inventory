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
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone

from .models import (
    Shop, Item, Sale, StockTransaction, UserProfile,
    BusinessPeriod, PeriodOpeningStock, Receipt, ReceiptItem,
    CompanyProfile, WhatsAppSetting, WhatsAppMessage, LandingPageContent
)

from .middleware import shop_access_required


# ========================
# USER HELPERS
# ========================

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


# ========================
# DASHBOARD VIEW
# ========================

@login_required
def dashboard(request):
    if not request.user.is_authenticated:
        return redirect('login')

    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin and profile and profile.assigned_shop:
        shop_slug = profile.assigned_shop.name.replace(' ', '-').lower()
        return redirect('shop_dashboard', shop_slug=shop_slug)

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
            sales_persons = UserProfile.objects.filter(
                user__receipt__shop=s
            ).distinct()
            shop_sales_data.append({
                'shop': s,
                'sales_persons': sales_persons,
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
# RECEIPT NUMBER GENERATOR
# =========================

def generate_receipt_number():
    today = date.today()
    prefix = today.strftime('RCP-%Y%m%d-')

    last = Receipt.objects.filter(
        receipt_number__startswith=prefix
    ).order_by('-receipt_number').first()

    if last:
        try:
            num = int(last.receipt_number.split('-')[-1]) + 1
        except (ValueError, IndexError):
            num = 1
    else:
        num = 1

    return f'{prefix}{num:04d}'


# =========================
# POINT OF SALE (POS)
# =========================

@shop_access_required
def point_of_sale(request, shop_slug):
    shop = get_object_or_404(
        Shop,
        name__iexact=shop_slug.replace('-', ' ')
    )

    is_warehouse = shop.name == 'Warehouse'

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
                # CREATE RECEIPT
                # =========================
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
                        receipt=receipt
                    )

                    item.quantity -= qty
                    item.save()

                return JsonResponse({
                    'success': True,
                    'receipt_id': receipt.id,
                    'receipt_number': receipt.receipt_number,
                    'change': float(change),
                    'message': 'Sale completed successfully'
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

    receipts = Receipt.objects.select_related(
        'shop', 'created_by'
    ).prefetch_related('items')

    if not user_is_admin and profile.assigned_shop:
        receipts = receipts.filter(shop=profile.assigned_shop)

    q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    shop_filter = request.GET.get('shop')

    if q:
        receipts = receipts.filter(
            Q(receipt_number__icontains=q) |
            Q(customer_name__icontains=q)
        )

    if date_from:
        receipts = receipts.filter(created_at__date__gte=date_from)

    if date_to:
        receipts = receipts.filter(created_at__date__lte=date_to)

    if user_is_admin and shop_filter:
        receipts = receipts.filter(shop_id=shop_filter)

    receipts = receipts.order_by('-created_at')[:100]

    total_sales = receipts.aggregate(
        total=Sum('total')
    )['total'] or 0

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


# =========================
# PRINT RECEIPT
# =========================

@shop_access_required
def print_receipt(request, receipt_id):
    receipt = get_object_or_404(Receipt, id=receipt_id)

    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if (
        not user_is_admin and
        profile.assigned_shop and
        receipt.shop != profile.assigned_shop
    ):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    context = {
        'receipt': receipt,
        'page_title': f'Receipt {receipt.receipt_number}',
    }

    return render(request, 'stock_manager/receipt_print.html', context)


# =========================
# EXPORT CSV
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
# ADMIN MANAGEMENT
# =========================

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
            shop_id = request.POST.get('shop')
            category = request.POST.get('category', '').strip()

            if not user_is_admin and profile.assigned_shop and str(profile.assigned_shop.id) != shop_id:
                messages.error(request, 'You can only add items to your assigned shop.')
                return redirect('admin_manage')

            try:
                quantity = int(request.POST.get('quantity', 0))
                unit_price = float(request.POST.get('unit_price', 0))
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity or price')
                return redirect('admin_manage')

            if item_name and shop_id:
                shop_obj = get_object_or_404(Shop, id=shop_id)

                existing = Item.objects.filter(
                    name__iexact=item_name,
                    shop=shop_obj
                ).first()

                if existing:
                    existing.quantity += quantity
                    existing.unit_price = unit_price
                    if category:
                        existing.category = category
                    existing.save()
                    messages.success(request, f'Added {quantity}x "{item_name}" (now {existing.quantity})')
                else:
                    Item.objects.create(
                        name=item_name,
                        shop=shop_obj,
                        quantity=quantity,
                        unit_price=unit_price,
                        category=category,
                    )
                    messages.success(request, f'Added "{item_name}"')

                auto_deduct_from_warehouse(item_name, shop_obj, quantity, unit_price, category)
            else:
                messages.error(request, 'Missing item name or shop')

            return redirect('admin_manage')

        elif action == 'edit_item':
            item_id = request.POST.get('item_id')
            item_name = request.POST.get('item_name', '').strip()
            category = request.POST.get('category', '').strip()

            try:
                if user_is_admin or request.user.is_superuser:
                    item = Item.objects.get(id=item_id)
                else:
                    item = Item.objects.get(id=item_id, shop=profile.assigned_shop)
                item.name = item_name or item.name
                item.quantity = int(request.POST.get('quantity', 0))
                item.unit_price = float(request.POST.get('unit_price', 0))
                item.category = category
                item.save()
                messages.success(request, f'Updated "{item.name}"')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')
            except (ValueError, TypeError):
                messages.error(request, 'Invalid quantity or price')

            return redirect('admin_manage')

        elif action == 'delete_item':
            item_id = request.POST.get('item_id')

            try:
                if user_is_admin or request.user.is_superuser:
                    item = Item.objects.get(id=item_id)
                else:
                    item = Item.objects.get(id=item_id, shop=profile.assigned_shop)
                name = item.name
                item.delete()
                messages.success(request, f'Deleted "{name}"')
            except Item.DoesNotExist:
                messages.error(request, 'Item not found')

            return redirect('admin_manage')

        elif action == 'stock_in':
            item_id = request.POST.get('item_id')
            to_shop_id = request.POST.get('to_shop')
            quantity = int(request.POST.get('quantity', 0))
            reason = request.POST.get('reason', '').strip()

            if not user_is_admin and profile.assigned_shop and str(profile.assigned_shop.id) != to_shop_id:
                messages.error(request, 'You can only stock in to your assigned shop.')
                return redirect('admin_manage')

            if item_id and to_shop_id and quantity > 0:
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
            elif not to_shop_id:
                messages.error(request, 'Please select a destination shop')
            else:
                messages.error(request, 'Missing required fields')

            return redirect('admin_manage')

        elif action == 'transfer_stock':
            item_id = request.POST.get('item_id')
            from_shop_id = request.POST.get('from_shop')
            to_shop_id = request.POST.get('to_shop')
            quantity = int(request.POST.get('quantity', 0))
            reason = request.POST.get('reason', '').strip()
            transaction_type = request.POST.get('transaction_type', 'transfer')

            if not user_is_admin and profile.assigned_shop and str(profile.assigned_shop.id) != from_shop_id:
                messages.error(request, 'You can only transfer stock from your assigned shop.')
                return redirect('admin_manage')

            if not from_shop_id:
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

            return redirect('admin_manage')

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
        stock_transactions = stock_transactions.filter(
            Q(source_shop=profile.assigned_shop) | Q(target_shop=profile.assigned_shop)
        )

    context = {
        'all_shops': all_shops,
        'items': items,
        'stock_transactions': stock_transactions,
        'shop_filter': shop_filter,
        'active_period': active_period,
        'page_title': 'Manage Inventory',
    }
    return render(request, 'stock_manager/admin_manage.html', context)





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
    shop = get_object_or_404(Shop, slug=shop_slug)

    if not request.user.is_superuser:
        profile = getattr(request.user, 'profile', None)
        if profile is None or profile.assigned_shop != shop:
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
        'page_title': f'{shop.name} Dashboard',
    }

    return render(request, 'stock_manager/shop_dashboard.html', context)


@shop_access_required
def shop_inventory(request, shop_slug):
    shop = get_object_or_404(Shop, name__iexact=shop_slug.replace('-', ' '))
    profile = get_user_profile(request.user)
    user_is_admin = profile is None or profile.is_admin

    if not user_is_admin and profile and profile.assigned_shop and profile.assigned_shop != shop:
        messages.error(request, 'Access denied to this shop.')
        return redirect('shop_dashboard', shop_slug=profile.assigned_shop.name.replace(' ', '-').lower())

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
                        user.save()
                        profile, created = UserProfile.objects.get_or_create(user=user)
                        profile.role = role
                        profile.assigned_shop = Shop.objects.filter(id=shop_id).first() if shop_id else None
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

            if request.FILES.get('landing_image'):
                content.image = request.FILES['landing_image']
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
    shops = Shop.objects.exclude(name__iexact='warehouse').order_by('name')
    items = Item.objects.exclude(shop__name__iexact='warehouse').select_related('shop').order_by('shop__name', 'name')
    return render(request, 'stock_manager/landing.html', {'company': company, 'landing': landing, 'items': items, 'shops': shops})




def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        print("USERNAME:", username)
        print("PASSWORD:", password)

        user = authenticate(request, username=username, password=password)

        print("USER:", user)

        if user is not None:
            login(request, user)
            if request.user.is_superuser:
                return redirect('admin_manage')
            elif hasattr(request.user, 'profile') and request.user.profile.assigned_shop:
                return redirect('shop_dashboard', shop_slug=request.user.profile.assigned_shop.slug)
            else:
                return redirect('login')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'stock_manager/login.html')




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

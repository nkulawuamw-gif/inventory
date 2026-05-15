import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.db.models import Q, Sum, F, ExpressionWrapper, DecimalField
from django.contrib import messages
from django.db import connection
from django.utils import timezone
from collections import defaultdict
from .models import Shop, Item, Sale, StockTransaction, UserProfile, BusinessPeriod, PeriodOpeningStock
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
                quantity = int(qty_raw.replace(',', ''))
            except ValueError:
                try:
                    quantity = int(float(qty_raw.replace(',', '')))
                except ValueError:
                    errors.append(f'Row {i}: Invalid quantity "{qty_raw}"')
                    continue

            try:
                unit_price = float(price_raw.replace(',', ''))
            except ValueError:
                errors.append(f'Row {i}: Invalid price "{price_raw}"')
                continue

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

            if item_id and int(qty_sold) > 0:
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

        elif action == 'transfer_item' and is_warehouse:
            item_id = request.POST.get('item_id', '')
            target_shop_id = request.POST.get('target_shop', '')
            qty_transfer = request.POST.get('quantity_transfer', 0)

            if item_id and target_shop_id and int(qty_transfer) > 0:
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
                    auto_deduct_from_warehouse(item.name, target_shop, qty_transfer, float(item.unit_price), item.category)
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

    if is_warehouse:
        other_shops = Shop.objects.exclude(name='Warehouse')
        warehouse_stocked_in = 0
        warehouse_stocked_out = 0
        stock_out_by_item = {}
        opening_by_item = {}
        for item in items:
            stocked_out = StockTransaction.objects.filter(item__shop=shop, item__name__iexact=item.name, transaction_type='transfer').aggregate(total=Sum('quantity'))['total'] or 0
            warehouse_stocked_out += stocked_out
            warehouse_stocked_in += item.quantity + stocked_out
            stock_out_by_item[item.id] = stocked_out
            open_data = opening_stock.get(item.name.lower(), {})
            opening_by_item[item.id] = open_data.get('quantity', 0)

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
            'active_period': active_period,
            'stock_transactions': stock_transactions,
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
            'active_period': active_period,
            'is_warehouse': False,
            'page_title': f'{shop.name} Inventory',
        }
    return render(request, 'stock_manager/shop_dashboard.html', context)


@shop_access_required
def point_of_sale(request, shop_slug):
    shop = get_object_or_404(Shop, name__iexact=shop_slug.replace('-', ' '))
    is_warehouse = shop.name == 'Warehouse'

    if request.method == 'POST' and not is_warehouse:
        action = request.POST.get('action')

        if action == 'record_sale':
            item_id = request.POST.get('item_id', '')
            qty_sold = request.POST.get('quantity_sold', 0)

            if item_id and int(qty_sold) > 0:
                item = get_object_or_404(Item, id=item_id, shop=shop)
                qty_sold = int(qty_sold)
                total_amount = item.unit_price * qty_sold
                Sale.objects.create(
                    item=item,
                    quantity_sold=qty_sold,
                    unit_price=item.unit_price,
                    total_amount=total_amount,
                )
                item.quantity -= qty_sold
                item.save()
                messages.success(request, f'Sold: {qty_sold}x {item.name} (MWK {total_amount:,.2f})')

        return redirect('point_of_sale', shop_slug=shop_slug)

    items = Item.objects.filter(shop=shop).order_by('name')
    shop_sales = Sale.objects.filter(item__shop=shop).select_related('item').order_by('-sold_at')[:20]
    today_sales = Sale.objects.filter(item__shop=shop, sold_at__date=timezone.now())
    today_total = today_sales.aggregate(total=Sum('total_amount'))['total'] or 0

    context = {
        'shop': shop,
        'items': items,
        'shop_sales': shop_sales,
        'today_total': today_total,
        'page_title': f'Point of Sale - {shop.name}',
    }
    return render(request, 'stock_manager/point_of_sale.html', context)


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

        return redirect('settings')

    periods = BusinessPeriod.objects.all()
    active_period = periods.filter(is_closed=False).first()
    opening_count = PeriodOpeningStock.objects.filter(period=active_period).count() if active_period else 0

    context = {
        'periods': periods,
        'active_period': active_period,
        'opening_count': opening_count,
        'page_title': 'Settings',
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

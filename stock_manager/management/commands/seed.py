from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from stock_manager.models import (
    Shop, Item, Sale, StockTransaction, UserProfile,
    BusinessPeriod, PeriodOpeningStock, Receipt, ReceiptItem,
    CompanyProfile, WhatsAppSetting
)
from datetime import date, timedelta
import random


class Command(BaseCommand):
    help = 'Seed the database with initial data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding database...')

        # ========================
        # 1. COMPANY PROFILE
        # ========================
        company, _ = CompanyProfile.objects.get_or_create(
            company_name='Varnishiela Beauty Palour',
            defaults={
                'address': 'Blantyre, Malawi',
                'phone': '+265 888 888 888',
                'email': 'info@varnishiela.com',
                'receipt_footer': 'Thank you for your patronage!',
                'hero_title': 'Welcome to Varnishiela Beauty Palour',
                'hero_tagline': 'Your trusted destination for authentic beauty products.',
            }
        )
        self.stdout.write(f'  Company: {company.company_name}')

        # ========================
        # 2. SHOPS
        # ========================
        shop_names = ['Warehouse', 'Zomba', 'Blantyre 1', 'Blantyre 2', 'Lilongwe', 'Mzuzu']
        shops = {}
        for name in shop_names:
            s, _ = Shop.objects.get_or_create(name=name)
            shops[name] = s
            self.stdout.write(f'  Shop: {s.name}')

        # ========================
        # 3. SUPERUSER
        # ========================
        if not User.objects.filter(username='sheila').exists():
            sheila = User.objects.create_superuser(
                username='sheila',
                email='nkulawuamw@gmail.com',
                password='admin123',
                first_name='Sheila',
                last_name='Admin'
            )
            self.stdout.write(f'  Superuser: sheila (password: admin123)')
        else:
            sheila = User.objects.get(username='sheila')
            self.stdout.write(f'  Superuser: sheila (already exists)')

        # ========================
        # 4. EXTRA USERS
        # ========================
        users_data = [
            ('john', 'John', 'Banda', 'shop_user', 'Blantyre 1'),
            ('mary', 'Mary', 'Kumwenda', 'shop_user', 'Zomba'),
            ('peter', 'Peter', 'Mwale', 'shop_user', 'Lilongwe'),
            ('grace', 'Grace', 'Phiri', 'shop_user', 'Mzuzu'),
            ('chifundo', 'Chifundo', 'Nkhoma', 'shop_user', 'Blantyre 2'),
            ('admin2', 'Admin', 'Two', 'admin', None),
        ]
        created_users = []
        for uname, first, last, role, shop_name in users_data:
            if User.objects.filter(username=uname).exists():
                continue
            u = User.objects.create_user(
                username=uname,
                password='password123',
                first_name=first,
                last_name=last,
            )
            profile = UserProfile.objects.get(user=u)
            profile.role = role
            if shop_name and shop_name in shops:
                profile.assigned_shop = shops[shop_name]
            profile.save()
            created_users.append(u)
            self.stdout.write(f'  User: {uname} (password: password123, role: {role}, shop: {shop_name or "All"})')

        all_users = [sheila] + created_users
        shop_list = [s for name, s in shops.items() if name != 'Warehouse']
        warehouse = shops['Warehouse']

        # ========================
        # 5. ITEMS
        # ========================
        items_data = [
            ('Sugar', 'Food', 1500.00),
            ('Rice', 'Food', 2500.00),
            ('Cooking Oil', 'Food', 3500.00),
            ('Salt', 'Food', 500.00),
            ('Tea', 'Beverages', 1200.00),
            ('Soap', 'Household', 800.00),
            ('Notebooks', 'Stationery', 600.00),
            ('Pens', 'Stationery', 200.00),
            ('Batteries', 'Electronics', 1500.00),
            ('USB Cables', 'Electronics', 2000.00),
            ('Flour', 'Food', 1800.00),
            ('Matches', 'Household', 300.00),
            ('Toothpaste', 'Personal Care', 1000.00),
            ('Towels', 'Household', 2500.00),
            ('Chargers', 'Electronics', 5000.00),
            ('Body Lotion', 'Personal Care', 4500.00),
            ('Perfume', 'Personal Care', 12000.00),
            ('Shampoo', 'Personal Care', 5500.00),
            ('Petroleum Jelly', 'Personal Care', 2500.00),
            ('Roll-on Deodorant', 'Personal Care', 3500.00),
        ]

        wh_qty_range = (20, 100)
        shop_qty_range = (2, 15)

        item_count = 0
        for name, category, price in items_data:
            item, _ = Item.objects.get_or_create(
                name=name,
                shop=warehouse,
                defaults={
                    'category': category,
                    'quantity': random.randint(*wh_qty_range),
                    'unit_price': price,
                }
            )
            item_count += 1
            for shop in shop_list:
                qty = random.randint(*shop_qty_range)
                Item.objects.get_or_create(
                    name=name,
                    shop=shop,
                    defaults={
                        'category': category,
                        'quantity': qty,
                        'unit_price': price,
                    }
                )
                item_count += 1

        self.stdout.write(f'  Items created: {item_count}')

        # ========================
        # 6. BUSINESS PERIOD
        # ========================
        today = date.today()
        period_start = today.replace(day=1)
        if period_start.month == 12:
            period_end = period_start.replace(year=period_start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            period_end = period_start.replace(month=period_start.month + 1, day=1) - timedelta(days=1)

        period, created = BusinessPeriod.objects.get_or_create(
            name=f'{period_start.strftime("%B %Y")}',
            defaults={
                'period_type': 'monthly',
                'start_date': period_start,
                'end_date': period_end,
                'is_closed': False,
            }
        )
        if created:
            self.stdout.write(f'  Period: {period.name}')

            for item in Item.objects.select_related('shop').all():
                PeriodOpeningStock.objects.create(
                    period=period,
                    item_name=item.name,
                    category=item.category,
                    shop=item.shop,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
            self.stdout.write(f'  Opening stock records created')
        else:
            self.stdout.write(f'  Period: {period.name} (already exists)')

        # ========================
        # 7. SAMPLE SALES & RECEIPTS
        # ========================
        if not Receipt.objects.exists():
            receipt_count = 0
            for shop in shop_list:
                shop_items = Item.objects.filter(shop=shop).order_by('?')
                for _ in range(random.randint(2, 5)):
                    items_in_receipt = shop_items[:random.randint(1, 4)]
                    if not items_in_receipt:
                        continue

                    subtotal = 0
                    line_data = []
                    for item in items_in_receipt:
                        qty = random.randint(1, min(3, item.quantity))
                        line_total = float(item.unit_price) * qty
                        subtotal += line_total
                        line_data.append((item, qty, line_total))

                    today_dt = timezone.now()
                    receipt = Receipt.objects.create(
                        receipt_number=f'SEED-{shop.id}-{_}-{today_dt.strftime("%Y%m%d")}',
                        shop=shop,
                        customer_name=random.choice(['', 'Mary K.', 'John B.', 'Grace P.', 'Peter M.', 'Chifundo N.']),
                        subtotal=subtotal,
                        total=subtotal,
                        amount_received=subtotal + random.choice([0, 100, 200, 500]),
                        change=0,
                        created_at=today_dt - timedelta(days=random.randint(0, 7), hours=random.randint(0, 12)),
                        created_by=random.choice(all_users),
                    )
                    receipt.change = float(receipt.amount_received) - float(receipt.total)
                    receipt.save()

                    for item, qty, total in line_data:
                        ReceiptItem.objects.create(
                            receipt=receipt,
                            item=item,
                            item_name=item.name,
                            quantity=qty,
                            unit_price=item.unit_price,
                            total=total,
                        )
                        Sale.objects.create(
                            item=item,
                            quantity_sold=qty,
                            unit_price=item.unit_price,
                            total_amount=total,
                            receipt=receipt,
                            sold_at=receipt.created_at,
                        )
                        item.quantity -= qty
                        item.save()

                    receipt_count += 1
            self.stdout.write(f'  Sample receipts & sales: {receipt_count}')

        # ========================
        # 8. WHATSAPP SETTINGS
        # ========================
        WhatsAppSetting.objects.get_or_create(
            phone_number='+265888888888',
            defaults={
                'business_name': company.company_name,
                'greeting_message': 'Hello! Welcome to Varnishiela Beauty Palour. How can we assist you today?',
                'is_active': True,
            }
        )
        self.stdout.write('  WhatsApp settings configured')

        # ========================
        # SUMMARY
        # ========================
        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete!\n'
            f'  Shops: {Shop.objects.count()}\n'
            f'  Items: {Item.objects.count()}\n'
            f'  Users: {User.objects.count()} (superusers: {User.objects.filter(is_superuser=True).count()})\n'
            f'  Receipts: {Receipt.objects.count()}\n'
            f'  Sales: {Sale.objects.count()}\n'
            f'  Period: {BusinessPeriod.objects.filter(is_closed=False).count()} open'
        ))

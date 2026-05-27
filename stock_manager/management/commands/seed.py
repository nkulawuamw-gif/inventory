from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
User = get_user_model()
from django.utils import timezone
from stock_manager.models import (
    Shop, Item, Sale, StockTransaction, UserProfile,
)
from datetime import date, timedelta
import random


class Command(BaseCommand):
    help = 'Seed the database with initial data'

    def handle(self, *args, **kwargs):
        self.stdout.write('Seeding database...')

        # ========================
        # 1. SHOPS
        # ========================
        shop_names = ['Warehouse', 'Zomba', 'Blantyre 1', 'Blantyre 2', 'Lilongwe', 'Mzuzu']
        shops = {}
        for name in shop_names:
            s, _ = Shop.objects.get_or_create(name=name)
            shops[name] = s
            self.stdout.write(f'  Shop: {s.name}')

        # ========================
        # 2. SUPERUSER
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
        # 3. EXTRA USERS
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
        # 4. ITEMS
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
        # 5. SAMPLE SALES
        # ========================
        sale_count = 0
        for shop in shop_list:
            shop_items = Item.objects.filter(shop=shop).order_by('?')
            for _ in range(random.randint(2, 5)):
                items_in_sale = shop_items[:random.randint(1, 3)]
                if not items_in_sale:
                    continue
                for item in items_in_sale:
                    qty = random.randint(1, min(3, item.quantity))
                    total = float(item.unit_price) * qty
                    Sale.objects.create(
                        item=item,
                        quantity_sold=qty,
                        unit_price=item.unit_price,
                        total_amount=total,
                        sold_at=timezone.now() - timedelta(days=random.randint(0, 7), hours=random.randint(0, 12)),
                    )
                    item.quantity -= qty
                    item.save()
                    sale_count += 1

        self.stdout.write(f'  Sample sales: {sale_count}')

        # ========================
        # SUMMARY
        # ========================
        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete!\n'
            f'  Shops: {Shop.objects.count()}\n'
            f'  Items: {Item.objects.count()}\n'
            f'  Users: {User.objects.count()} (superusers: {User.objects.filter(is_superuser=True).count()})\n'
            f'  Sales: {Sale.objects.count()}'
        ))

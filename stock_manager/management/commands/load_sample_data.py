from django.core.management.base import BaseCommand
from stock_manager.models import Shop, Item


class Command(BaseCommand):
    help = 'Load sample inventory data for all shops'

    def handle(self, *args, **kwargs):
        self.stdout.write('Creating shops...')

        shops_data = ['Zomba', 'Blantyre 1', 'Blantyre 2', 'Lilongwe', 'Mzuzu']
        shops = {}
        for name in shops_data:
            shop, created = Shop.objects.get_or_create(name=name)
            shops[name] = shop
            if created:
                self.stdout.write(f'  Created: {shop.name}')

        self.stdout.write('Creating items...')

        items_data = [
            ('Sugar', 'Food', {'Zomba': 4, 'Blantyre 1': 4, 'Blantyre 2': 5, 'Lilongwe': 2, 'Mzuzu': 3}, 1500.00),
            ('Rice', 'Food', {'Zomba': 10, 'Blantyre 1': 8, 'Blantyre 2': 12, 'Lilongwe': 6, 'Mzuzu': 9}, 2500.00),
            ('Cooking Oil', 'Food', {'Zomba': 3, 'Blantyre 1': 5, 'Blantyre 2': 7, 'Lilongwe': 2, 'Mzuzu': 4}, 3500.00),
            ('Salt', 'Food', {'Zomba': 20, 'Blantyre 1': 15, 'Blantyre 2': 25, 'Lilongwe': 18, 'Mzuzu': 22}, 500.00),
            ('Tea', 'Beverages', {'Zomba': 8, 'Blantyre 1': 6, 'Blantyre 2': 10, 'Lilongwe': 4, 'Mzuzu': 7}, 1200.00),
            ('Soap', 'Household', {'Zomba': 15, 'Blantyre 1': 12, 'Blantyre 2': 18, 'Lilongwe': 10, 'Mzuzu': 14}, 800.00),
            ('Notebooks', 'Stationery', {'Zomba': 30, 'Blantyre 1': 25, 'Blantyre 2': 35, 'Lilongwe': 20, 'Mzuzu': 28}, 600.00),
            ('Pens', 'Stationery', {'Zomba': 50, 'Blantyre 1': 40, 'Blantyre 2': 60, 'Lilongwe': 35, 'Mzuzu': 45}, 200.00),
            ('Batteries', 'Electronics', {'Zomba': 2, 'Blantyre 1': 3, 'Blantyre 2': 4, 'Lilongwe': 1, 'Mzuzu': 2}, 1500.00),
            ('USB Cables', 'Electronics', {'Zomba': 10, 'Blantyre 1': 8, 'Blantyre 2': 12, 'Lilongwe': 6, 'Mzuzu': 9}, 2000.00),
            ('Flour', 'Food', {'Zomba': 6, 'Blantyre 1': 4, 'Blantyre 2': 8, 'Lilongwe': 3, 'Mzuzu': 5}, 1800.00),
            ('Matches', 'Household', {'Zomba': 40, 'Blantyre 1': 35, 'Blantyre 2': 50, 'Lilongwe': 30, 'Mzuzu': 38}, 300.00),
            ('Toothpaste', 'Personal Care', {'Zomba': 3, 'Blantyre 1': 5, 'Blantyre 2': 7, 'Lilongwe': 2, 'Mzuzu': 4}, 1000.00),
            ('Towels', 'Household', {'Zomba': 8, 'Blantyre 1': 6, 'Blantyre 2': 10, 'Lilongwe': 4, 'Mzuzu': 7}, 2500.00),
            ('Chargers', 'Electronics', {'Zomba': 1, 'Blantyre 1': 2, 'Blantyre 2': 3, 'Lilongwe': 1, 'Mzuzu': 2}, 5000.00),
        ]

        created_count = 0
        for name, category, shop_quantities, price in items_data:
            for shop_name, qty in shop_quantities.items():
                item, created = Item.objects.get_or_create(
                    name=name,
                    shop=shops[shop_name],
                    defaults={
                        'category': category,
                        'quantity': qty,
                        'unit_price': price,
                    }
                )
                if created:
                    created_count += 1

        self.stdout.write(self.style.SUCCESS(f'Loaded {created_count} new inventory records across {len(shops)} shops'))

import json
from django.core.management.base import BaseCommand
from django.core import serializers
from django.db.models.signals import post_save
from django.contrib.auth.models import User
from stock_manager.models import create_user_profile


class Command(BaseCommand):
    help = 'Migrate SQLite data dump to PostgreSQL'

    def add_arguments(self, parser):
        parser.add_argument('fixture', nargs='?', default='data_dump.json')

    def handle(self, *args, **options):
        fixture = options['fixture']
        self.stdout.write(f'Loading fixture: {fixture}')

        with open(fixture, 'r', encoding='utf-8') as f:
            objects = json.load(f)

        # disconnect signal so UserProfile doesn't auto-create on User save
        post_save.disconnect(sender=User, dispatch_uid='create_user_profile')

        # order by dependency (parents before children)
        def sort_key(o):
            p = o['model']
            rank = {'auth': 0, 'stock_manager.shop': 1, 'stock_manager.companyprofile': 1,
                    'stock_manager.whatsappsetting': 1, 'stock_manager.userprofile': 2,
                    'stock_manager.item': 3, 'stock_manager.businessperiod': 4,
                    'stock_manager.periodopeningstock': 5, 'stock_manager.receipt': 6,
                    'stock_manager.receiptitem': 7, 'stock_manager.sale': 8,
                    'stock_manager.stocktransaction': 9, 'stock_manager.call': 10,
                    'stock_manager.userpresence': 11, 'stock_manager.landingpagecontent': 12}
            return rank.get(p, 99)
        objects.sort(key=sort_key)

        from django.db import transaction

        total = len(objects)
        loaded = 0
        errors = []
        last_model = ''

        with transaction.atomic():
            for i, obj_data in enumerate(objects):
                model_name = obj_data.get('model', '')
                if model_name != last_model:
                    self.stdout.write(f'  {model_name}...')
                    last_model = model_name
                try:
                    for deserialized_obj in serializers.deserialize('json', json.dumps([obj_data])):
                        deserialized_obj.save()
                        loaded += 1
                except Exception as e:
                    errors.append(f'  PK={obj_data.get("pk")} ({obj_data.get("model")}): {str(e)[:120]}')

        # reconnect signal
        post_save.connect(create_user_profile, sender=User, dispatch_uid='create_user_profile')

        self.stdout.write(self.style.SUCCESS(f'Loaded {loaded}/{total} objects'))
        if errors:
            self.stderr.write(f'Errors ({len(errors)}):')
            for e in errors[:20]:
                self.stderr.write(str(e))

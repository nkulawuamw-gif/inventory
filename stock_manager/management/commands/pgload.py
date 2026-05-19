import json
from django.core.management.base import BaseCommand
from django.core import serializers
from django.db import connection


class Command(BaseCommand):
    help = 'Load fixture into PostgreSQL without atomic transaction'

    def add_arguments(self, parser):
        parser.add_argument('fixture', nargs='+')

    def handle(self, *args, **options):
        for fixture in options['fixture']:
            self.stdout.write(f'Loading {fixture}...')
            with open(fixture, 'r', encoding='utf-8') as f:
                objects = json.load(f)

            total = 0
            for obj_data in objects:
                for deserialized_obj in serializers.deserialize('json', json.dumps([obj_data])):
                    try:
                        deserialized_obj.save()
                        total += 1
                    except Exception as e:
                        self.stderr.write(f'  SKIP PK={obj_data.get("pk")}: {str(e)[:120]}')

                if total % 50 == 0:
                    self.stdout.write(f'  ... {total}')

            self.stdout.write(f'  Loaded {total}/{len(objects)} objects')

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Ensure the status column exists on Message table (safe to run repeatedly)'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name='stock_manager_message' AND column_name='status'
                    ) THEN
                        ALTER TABLE stock_manager_message
                        ADD COLUMN status varchar(20) NOT NULL DEFAULT 'sent';
                    END IF;
                END $$;
            """)
            self.stdout.write(self.style.SUCCESS('Ensured status column exists on stock_manager_message'))

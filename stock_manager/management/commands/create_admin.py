from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from stock_manager.models import UserProfile


class Command(BaseCommand):
    help = 'Create or reset the default admin/superuser accounts'

    def handle(self, *args, **options):
        sheila, created = User.objects.get_or_create(
            username='sheila',
            defaults={
                'email': 'nkulawuamw@gmail.com',
                'is_superuser': True,
                'is_staff': True,
                'is_active': True,
            }
        )
        sheila.set_password('admin123')
        sheila.is_superuser = True
        sheila.is_staff = True
        sheila.is_active = True
        sheila.save()

        UserProfile.objects.get_or_create(user=sheila, defaults={'role': 'admin'})

        if created:
            self.stdout.write(self.style.SUCCESS('Created superuser: sheila / admin123'))
        else:
            self.stdout.write(self.style.SUCCESS('Reset superuser: sheila / admin123'))

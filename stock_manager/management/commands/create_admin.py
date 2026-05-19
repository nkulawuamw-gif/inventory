from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create default admin user for production"

    def handle(self, *args, **kwargs):
        User = get_user_model()

        username = "admin"
        email = "admin@example.com"
        password = "admin1234"

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING("Admin already exists"))
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password
        )

        self.stdout.write(self.style.SUCCESS("Admin user created successfully"))
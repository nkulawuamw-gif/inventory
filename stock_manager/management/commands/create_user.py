from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
User = get_user_model()
from stock_manager.models import Shop, UserProfile


class Command(BaseCommand):
    help = 'Create a user with shop assignment and role'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)
        parser.add_argument('email', type=str, default='', nargs='?')
        parser.add_argument('--role', type=str, choices=['admin', 'shop_user'], default='shop_user')
        parser.add_argument('--shop', type=str, help='Shop name (required for shop_user)')
        parser.add_argument('--password', type=str, help='Set password (default: password123)')
        parser.add_argument('--first-name', type=str, default='')
        parser.add_argument('--last-name', type=str, default='')

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        role = options['role']
        shop_name = options['shop']
        password = options['password'] or 'password123'
        first_name = options['first_name']
        last_name = options['last_name']

        if role == 'shop_user' and not shop_name:
            raise CommandError('Shop name is required for shop_user role')

        if role == 'admin' and shop_name:
            shop = Shop.objects.filter(name__iexact=shop_name).first()
            if not shop:
                raise CommandError(f'Shop "{shop_name}" not found')

        if User.objects.filter(username=username).exists():
            raise CommandError(f'User "{username}" already exists')

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        profile = UserProfile.objects.create(
            user=user,
            role=role,
        )

        if role == 'shop_user' and shop_name:
            shop = Shop.objects.filter(name__iexact=shop_name).first()
            if not shop:
                user.delete()
                raise CommandError(f'Shop "{shop_name}" not found')
            profile.assigned_shop = shop
            profile.save()

        role_display = 'Admin (All Shops)' if role == 'admin' else f'Shop User: {shop_name}'
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created user "{username}" - {role_display}'
            )
        )

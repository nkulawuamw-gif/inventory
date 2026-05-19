from django.apps import AppConfig


class StockManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stock_manager'

    def ready(self):
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()

            if not User.objects.filter(username='admin').exists():
                User.objects.create_superuser(
                    username='admin',
                    email='admin@example.com',
                    password='admin1234'
                )
                print("Superuser created")
        except Exception as e:
            print("Superuser creation skipped:", e)

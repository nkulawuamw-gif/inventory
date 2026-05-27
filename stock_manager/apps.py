from django.apps import AppConfig


class StockManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stock_manager'

    def ready(self):
        # Import signals so they register properly
        import stock_manager.models  # noqa
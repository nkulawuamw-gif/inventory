import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'best_inventory_system.settings_pg')

application = get_wsgi_application()
# PythonAnywhere WSGI Configuration File
# Copy the content below into your PythonAnywhere Web App's WSGI configuration file
# (found at: /var/www/yourusername_pythonanywhere_com_wsgi.py)

import os
import sys

# Replace 'yourusername' with your actual PythonAnywhere username
path = '/home/yourusername/best-inventory-system'
if path not in sys.path:
    sys.path.insert(0, path)

# Set the Django settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'best_inventory_system.settings'

# Environment variables for production (set these in PythonAnywhere Web App config)
# os.environ['DJANGO_SECRET_KEY'] = 'your-secret-key-here'
# os.environ['DJANGO_DEBUG'] = 'False'
# os.environ['DJANGO_ALLOWED_HOSTS'] = 'yourusername.pythonanywhere.com'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

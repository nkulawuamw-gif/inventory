#!/bin/bash
# Wait for database to be ready
echo "Waiting for database..."
for i in $(seq 1 30); do
    python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'best_inventory_system.settings')
import django; django.setup()
from django.db import connection; connection.ensure_connection(); connection.close()
" 2>/dev/null && echo "Database ready!" && break
    echo "Attempt $i: database not ready yet... sleeping 2s"
    sleep 2
done

python manage.py migrate --noinput

exec daphne best_inventory_system.asgi:application --bind 0.0.0.0 --port $PORT --proxy-headers

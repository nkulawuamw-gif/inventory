# Generated manually — fix duplicate 'placeholder' room_names that break unique constraint on PostgreSQL

import uuid
from django.db import migrations


def fix_placeholders(apps, schema_editor):
    Call = apps.get_model('stock_manager', 'Call')
    for call in Call.objects.filter(room_name='placeholder'):
        call.room_name = f"call_{uuid.uuid4().hex[:12]}"
        call.save()


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0027_alter_call_options_alter_message_options_and_more'),
    ]

    operations = [
        migrations.RunPython(fix_placeholders, reverse_code=migrations.RunPython.noop),
    ]

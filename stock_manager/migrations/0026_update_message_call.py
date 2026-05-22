# Generated manually — migrate Message and Call models to simplified schema

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0025_receipt_pdf_file'),
    ]

    operations = [
        # --- Message changes ---
        migrations.RenameField(
            model_name='message',
            old_name='body',
            new_name='content',
        ),
        migrations.RenameField(
            model_name='message',
            old_name='created_at',
            new_name='timestamp',
        ),
        migrations.RemoveField(
            model_name='message',
            name='audio_file',
        ),
        migrations.RemoveField(
            model_name='message',
            name='duration',
        ),
        # --- Call changes ---
        migrations.RenameField(
            model_name='call',
            old_name='callee',
            new_name='receiver',
        ),
        migrations.RenameField(
            model_name='call',
            old_name='started_at',
            new_name='timestamp',
        ),
        migrations.RemoveField(
            model_name='call',
            name='ended_at',
        ),
        migrations.RemoveField(
            model_name='call',
            name='signaling_data',
        ),
        migrations.AddField(
            model_name='call',
            name='room_name',
            field=models.CharField(default='placeholder', max_length=255, unique=True),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='call',
            name='call_type',
            field=models.CharField(choices=[('audio', 'Audio'), ('video', 'Video')], max_length=10),
        ),
        migrations.AlterField(
            model_name='call',
            name='status',
            field=models.CharField(default='ringing', max_length=20),
        ),
        migrations.AlterField(
            model_name='call',
            name='caller',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='made_calls', to='auth.user'),
        ),
    ]

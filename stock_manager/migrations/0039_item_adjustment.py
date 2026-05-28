from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0038_companyprofile'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='adjustment',
            field=models.IntegerField(default=0),
        ),
    ]

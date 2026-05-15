from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0007_add_unit_price_to_sale'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sale',
            name='unit_price',
            field=models.DecimalField(decimal_places=2, max_digits=10),
        ),
    ]

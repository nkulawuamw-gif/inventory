from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0037_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_name', models.CharField(default='My Store', max_length=200)),
                ('address', models.TextField(blank=True, default='')),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('tax_id', models.CharField(blank=True, default='', max_length=100)),
                ('receipt_footer', models.CharField(blank=True, default='Thank you for your business!', max_length=300)),
                ('hero_title', models.CharField(blank=True, default='', max_length=300)),
                ('hero_tagline', models.TextField(blank=True, default='')),
            ],
            options={
                'verbose_name': 'Company Profile',
                'verbose_name_plural': 'Company Profile',
            },
        ),
    ]

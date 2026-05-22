from django.db import migrations
from django.utils.text import slugify


def populate_shop_slugs(apps, schema_editor):
    Shop = apps.get_model('stock_manager', 'Shop')
    for shop in Shop.objects.filter(slug__isnull=True):
        shop.slug = slugify(shop.name)
        # Ensure uniqueness: append suffix if slug already exists
        base_slug = shop.slug
        counter = 1
        while Shop.objects.filter(slug=shop.slug).exclude(id=shop.id).exists():
            shop.slug = f'{base_slug}-{counter}'
            counter += 1
        shop.save(update_fields=['slug'])


def reverse_func(apps, schema_editor):
    Shop = apps.get_model('stock_manager', 'Shop')
    Shop.objects.all().update(slug=None)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_manager', '0023_message_audio_file_message_duration_and_more'),
    ]

    operations = [
        migrations.RunPython(populate_shop_slugs, reverse_func),
    ]

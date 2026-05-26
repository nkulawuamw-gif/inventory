from django.db import models
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()
from django.utils import timezone
from django.utils.text import slugify


class Shop(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def is_warehouse(self):
        return self.name.strip().lower() == 'warehouse'


PERMISSION_CHOICES = [
    ('dashboard', 'Main Dashboard'),
    ('admin_manage', 'Manage Inventory'),
    ('financial_report', 'Financial Report'),
    ('sales_history', 'Sales History'),
    ('chat', 'Chat'),
    ('calls', 'Calls'),
    ('meetings', 'Meetings'),
    ('whatsapp', 'WhatsApp'),
    ('point_of_sale', 'Point of Sale'),
    ('inventory', 'Inventory'),
    ('export_csv', 'Export CSV'),
    ('dashboard_bulk_import', 'Bulk Import'),
    ('settings', 'Settings'),
]


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin (All Shops)'),
        ('shop_user', 'Shop User (Single Shop)'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='user_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='shop_user')
    assigned_shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_users')
    permissions = models.JSONField(null=True, blank=True, default=None, help_text="List of granted permission keys. None = no extra access.")

    class Meta:
        verbose_name = 'User Profile'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def assigned_shop_name(self):
        return self.assigned_shop.name if self.assigned_shop else 'All Shops'

    def has_permission(self, perm_name):
        if self.is_admin:
            return True
        if self.permissions is None:
            return False
        return perm_name in self.permissions

    def get_permissions_display(self):
        if self.is_admin:
            return 'All (admin)'
        if not self.permissions:
            return 'None'
        perm_map = dict(PERMISSION_CHOICES)
        return ', '.join(perm_map.get(p, p) for p in self.permissions)


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True)
    is_shop_user = models.BooleanField(default=False)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.user.username


class Item(models.Model):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default='')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='items')
    quantity = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return f"{self.name} - {self.shop.name}"

    @property
    def total_value(self):
        return self.quantity * self.unit_price

    @property
    def is_low_stock(self):
        return self.quantity < 5


class Sale(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='sales')
    quantity_sold = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    receipt = models.ForeignKey('Receipt', on_delete=models.CASCADE, null=True, blank=True, related_name='sales')
    sold_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sold_at']

    def __str__(self):
        return f"{self.quantity_sold}x {self.item.name} @ {self.item.shop.name}"


class StockTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('transfer', 'Transfer'),
        ('damaged', 'Damaged'),
        ('lost', 'Lost/Stolen'),
        ('returned', 'Returned to Supplier'),
        ('expiry', 'Expired'),
        ('other', 'Other'),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='stock_transactions')
    source_shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, related_name='source_transactions')
    target_shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='target_transactions')
    quantity = models.IntegerField()
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        source = self.source_shop.name if self.source_shop else 'N/A'
        return f"{self.get_transaction_type_display()}: {self.quantity}x {self.item.name} from {source}"

    def get_target_display(self):
        if self.target_shop:
            return f"to {self.target_shop.name}"
        return ""


class Message(models.Model):
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
    ]
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name="received_messages")
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='sent')

    def __str__(self):
        return f"{self.sender} -> {self.receiver}"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=500, blank=True, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.message[:50]}"


def create_notification(user, message, link=''):
    Notification.objects.create(user=user, message=message, link=link)


class BusinessPeriod(models.Model):
    PERIOD_TYPES = [
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
        ('custom', 'Custom'),
    ]

    name = models.CharField(max_length=200)
    period_type = models.CharField(max_length=20, choices=PERIOD_TYPES, default='monthly')
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='closed_periods')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Business Period'
        verbose_name_plural = 'Business Periods'

    def __str__(self):
        status = 'Closed' if self.is_closed else 'Open'
        return f"{self.name} ({self.start_date} - {self.end_date}) [{status}]"


class PeriodOpeningStock(models.Model):
    period = models.ForeignKey(BusinessPeriod, on_delete=models.CASCADE, related_name='opening_stocks')
    item_name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True, default='')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Period Opening Stock'
        verbose_name_plural = 'Period Opening Stocks'
        unique_together = ['period', 'item_name', 'shop']
        ordering = ['item_name']

    def __str__(self):
        return f"{self.item_name} @ {self.shop.name} ({self.period.name})"


def _receipt_pdf_path(instance, filename):
    return f'receipts/{instance.shop.slug}/{instance.receipt_number}.pdf'

class Receipt(models.Model):
    receipt_number = models.CharField(max_length=20, unique=True)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='receipts')
    customer_name = models.CharField(max_length=200, blank=True, default='')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    change = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    pdf_file = models.FileField(upload_to=_receipt_pdf_path, blank=True, null=True, verbose_name='Receipt PDF')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.receipt_number} - {self.shop.name}"


class ReceiptItem(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name='items')
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True)
    item_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.item_name} x{self.quantity}"


class CompanyProfile(models.Model):
    company_name = models.CharField(max_length=200, default='My Store')
    address = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    tax_id = models.CharField(max_length=100, blank=True, default='')
    receipt_footer = models.CharField(max_length=300, blank=True, default='Thank you for your business!')
    hero_title = models.CharField(max_length=300, blank=True, default='')
    hero_tagline = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Company Profile'
        verbose_name_plural = 'Company Profile'

    def __str__(self):
        return self.company_name

    @classmethod
    def get_profile(cls):
        obj = cache.get('company_profile')
        if obj is None:
            obj = cls.objects.first()
            if obj is None:
                obj = cls.objects.create()
            cache.set('company_profile', obj, 3600)
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete('company_profile')


class WhatsAppSetting(models.Model):
    phone_number = models.CharField(max_length=20, blank=True, default='')
    business_name = models.CharField(max_length=200, blank=True, default='My Business')
    webhook_secret = models.CharField(max_length=100, blank=True, default='')
    api_key = models.TextField(blank=True, default='')
    greeting_message = models.TextField(blank=True, default='Hello! Welcome to our store. How can we assist you today?')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'WhatsApp Setting'
        verbose_name_plural = 'WhatsApp Settings'

    def __str__(self):
        return f'WhatsApp - {self.phone_number or "Not configured"}'

    @classmethod
    def get_profile(cls):
        obj = cache.get('whatsapp_setting')
        if obj is None:
            obj = cls.objects.first()
            if obj is None:
                obj = cls.objects.create()
            cache.set('whatsapp_setting', obj, 3600)
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete('whatsapp_setting')


class LandingPageContent(models.Model):
    data = models.JSONField(default=dict, blank=True)
    image = models.ImageField(upload_to="landing/", blank=True, null=True)

    class Meta:
        verbose_name = 'Landing Page Content'
        verbose_name_plural = 'Landing Page Content'

    def __str__(self):
        return 'Landing Page Settings'

    @classmethod
    def get_content(cls):
        obj = cache.get('landing_page_content')
        if obj is None:
            obj = cls.objects.first()
            if obj is None:
                obj = cls.objects.create()
            cache.set('landing_page_content', obj, 3600)
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete('landing_page_content')


class WhatsAppMessage(models.Model):
    customer_number = models.CharField(max_length=20, db_index=True)
    customer_name = models.CharField(max_length=200, blank=True, default='')
    wa_message_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    body = models.TextField()
    is_from_customer = models.BooleanField(default=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    replied_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.customer_name or self.customer_number
        return f'{who}: {self.body[:50]}'


from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=User, dispatch_uid='create_user_profile')
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def create_shop_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(post_save, sender=User)
def save_shop_profile(sender, instance, **kwargs):
    Profile.objects.get_or_create(user=instance)
    instance.profile.save()


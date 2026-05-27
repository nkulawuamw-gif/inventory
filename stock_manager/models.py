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
    transfer = models.ForeignKey('Transfer', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_transactions')

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


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('message', 'New Message'),
        ('system', 'System Alert'),
        ('transfer', 'Stock Transfer'),
        ('sale', 'New Sale'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_notifications')
    title = models.CharField(max_length=200)
    message = models.TextField()
    type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='message')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    link = models.CharField(max_length=500, blank=True, default='')
    transfer = models.ForeignKey('Transfer', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
        ]

    def __str__(self):
        return f"{self.title} - {self.user.username}"


class Transfer(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('received', 'Received'),
        ('rejected', 'Rejected'),
    ]
    transfer_code = models.CharField(max_length=20, unique=True)
    sender_shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='outgoing_transfers')
    receiver_shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='incoming_transfers')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_transfers')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['transfer_code']),
        ]

    def __str__(self):
        return f"{self.transfer_code} ({self.sender_shop} -> {self.receiver_shop})"

    def save(self, *args, **kwargs):
        if not self.transfer_code:
            import time
            self.transfer_code = f"TXN-{int(time.time() * 1000) % 100000:05d}"
        super().save(*args, **kwargs)


class TransferItem(models.Model):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=200)
    quantity = models.IntegerField()

    def __str__(self):
        return f"{self.quantity}x {self.item_name}"


from django.db.models.signals import post_save, pre_save
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


def push_notification_to_channel_layer(notification):
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        sender_name = notification.sender.get_full_name().strip() or notification.sender.username if notification.sender else ''
        transfer_code = notification.transfer.transfer_code if notification.transfer else ''
        async_to_sync(channel_layer.group_send)(
            f"notifications_{notification.user.id}",
            {
                "type": "new_notification",
                "id": notification.id,
                "title": notification.title,
                "message": notification.message,
                "notification_type": notification.type,
                "sender_name": sender_name,
                "link": notification.link,
                "transfer_code": transfer_code,
                "created_at": notification.created_at.isoformat(),
            }
        )
    except Exception:
        pass


@receiver(pre_save, sender=Transfer)
def track_transfer_status_change(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = Transfer.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except Transfer.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Transfer)
def transfer_notification_handler(sender, instance, created, **kwargs):
    try:
        if created and instance.status == 'sent':
            profiles = Profile.objects.filter(shop=instance.receiver_shop).select_related('user')
            items_summary = ', '.join([f"{item.quantity}x {item.item_name}" for item in instance.items.all()])
            for profile in profiles:
                user = profile.user
                if user == instance.created_by:
                    continue
                notif = Notification.objects.create(
                    user=user,
                    sender=instance.created_by,
                    title=f"Transfer from {instance.sender_shop.name}",
                    message=f"Transfer {instance.transfer_code}: {items_summary}",
                    type='transfer',
                    link='/transfers/',
                    transfer=instance,
                )
                push_notification_to_channel_layer(notif)

        if not created:
            old_status = None
            if hasattr(instance, '_old_status'):
                old_status = instance._old_status

            if old_status != 'sent' and instance.status == 'sent':
                profiles = Profile.objects.filter(shop=instance.receiver_shop).select_related('user')
                items_summary = ', '.join([f"{item.quantity}x {item.item_name}" for item in instance.items.all()])
                for profile in profiles:
                    user = profile.user
                    if user == instance.created_by:
                        continue
                    notif = Notification.objects.create(
                        user=user,
                        sender=instance.created_by,
                        title=f"Transfer from {instance.sender_shop.name}",
                        message=f"Transfer {instance.transfer_code}: {items_summary}",
                        type='transfer',
                        link='/transfers/',
                        transfer=instance,
                    )
                    push_notification_to_channel_layer(notif)

            if old_status != 'received' and instance.status == 'received':
                notif = Notification.objects.create(
                    user=instance.created_by,
                    sender=None,
                    title=f"Transfer Received by {instance.receiver_shop.name}",
                    message=f"Transfer {instance.transfer_code} has been received and confirmed.",
                    type='transfer',
                    link='/transfers/',
                    transfer=instance,
                )
                push_notification_to_channel_layer(notif)

            if old_status != 'rejected' and instance.status == 'rejected':
                notif = Notification.objects.create(
                    user=instance.created_by,
                    sender=None,
                    title=f"Transfer Rejected by {instance.receiver_shop.name}",
                    message=f"Transfer {instance.transfer_code} has been rejected.",
                    type='transfer',
                    link='/transfers/',
                    transfer=instance,
                )
                push_notification_to_channel_layer(notif)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("transfer_notification_handler failed")


@receiver(post_save, sender=StockTransaction)
def stock_transaction_transfer_handler(sender, instance, created, **kwargs):
    if not created or instance.transaction_type != 'transfer' or not instance.target_shop:
        return
    if instance.transfer:
        return
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        creator = User.objects.filter(is_superuser=True).first()
        if not creator:
            profile = Profile.objects.filter(shop=instance.source_shop).select_related('user').first()
            creator = profile.user if profile else User.objects.first()
        if not creator:
            return

        transfer = Transfer.objects.create(
            sender_shop=instance.source_shop,
            receiver_shop=instance.target_shop,
            created_by=creator,
            status='sent',
            notes=instance.reason or f"Auto-transfer: {instance.item.name}",
        )
        TransferItem.objects.create(
            transfer=transfer,
            item_name=instance.item.name,
            quantity=instance.quantity,
        )
        instance.transfer = transfer
        instance.save(update_fields=['transfer'])

        profiles = Profile.objects.filter(shop=instance.target_shop).select_related('user')
        for profile in profiles:
            user = profile.user
            if user == creator:
                continue
            notif = Notification.objects.create(
                user=user,
                sender=creator,
                title=f"Transfer from {instance.source_shop.name}",
                message=f"Transfer {transfer.transfer_code}: {instance.quantity}x {instance.item.name}",
                type='transfer',
                link='/transfers/',
                transfer=transfer,
            )
            push_notification_to_channel_layer(notif)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("stock_transaction_transfer_handler failed")


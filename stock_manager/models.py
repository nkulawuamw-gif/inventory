import logging
import time

from django.db import models
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from django.utils.text import slugify
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

User = get_user_model()
logger = logging.getLogger(__name__)

# =========================
# SHOP
# =========================

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


# =========================
# PERMISSIONS
# =========================

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


# =========================
# USER PROFILE (SAFE)
# =========================

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin (All Shops)'),
        ('shop_user', 'Shop User (Single Shop)'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='user_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='shop_user')
    assigned_shop = models.ForeignKey(
        Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_users'
    )
    permissions = models.JSONField(null=True, blank=True, default=None)

    class Meta:
        verbose_name = 'User Profile'

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == 'admin'

    def has_permission(self, perm_name):
        if self.is_admin:
            return True
        if not self.permissions:
            return False
        return perm_name in self.permissions


# =========================
# PROFILE (ONLINE STATUS)
# =========================

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True)
    is_shop_user = models.BooleanField(default=False)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.user.username


# =========================
# ITEM
# =========================

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
        indexes = [models.Index(fields=['name'])]

    def __str__(self):
        return f"{self.name} - {self.shop.name}"

    @property
    def total_value(self):
        return self.quantity * self.unit_price

    @property
    def is_low_stock(self):
        return self.quantity <= 5


# =========================
# SALE
# =========================

class Sale(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='sales')
    quantity_sold = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    sold_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sold_at']


# =========================
# STOCK TRANSACTION
# =========================

class StockTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('transfer', 'Transfer'),
        ('damaged', 'Damaged'),
        ('lost', 'Lost/Stolen'),
        ('returned', 'Returned'),
        ('expiry', 'Expired'),
        ('other', 'Other'),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    source_shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, related_name='source_transactions')
    target_shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.IntegerField()
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']


# =========================
# MESSAGE (CHAT)
# =========================

class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_messages")
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name="received_messages")
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-timestamp']


# =========================
# NOTIFICATION
# =========================

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


# =========================
# TRANSFER
# =========================

class Transfer(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('received', 'Received'),
        ('rejected', 'Rejected'),
    ]

    transfer_code = models.CharField(max_length=20, unique=True, blank=True)
    sender_shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='outgoing_transfers')
    receiver_shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='incoming_transfers')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.transfer_code:
            self.transfer_code = f"TXN-{int(time.time() * 1000) % 100000:05d}"
        super().save(*args, **kwargs)


# =========================
# SAFE SIGNALS
# =========================

@receiver(post_save, sender=User)
def create_user_profiles(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)
        Profile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Profile)
def ensure_profile_safe(sender, instance, **kwargs):
    # prevents missing shop crashes later
    if instance.shop is None:
        return


# =========================
# LANDING PAGE CONTENT
# =========================

class LandingPageContent(models.Model):
    data = models.JSONField(default=dict, blank=True)
    image = models.ImageField(upload_to='landing/', blank=True, null=True)

    class Meta:
        verbose_name = 'Landing Page Content'
        verbose_name_plural = 'Landing Page Content'

    def __str__(self):
        return 'Landing Page'


# =========================
# SAFE HEARTBEAT HELPERS (used by views)
# =========================

def safe_get_profile(user):
    """
    IMPORTANT: prevents 500 errors in heartbeat/chat APIs
    """
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile
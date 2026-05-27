from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Shop, Item, Sale, StockTransaction,
    UserProfile, Profile, Message,
    Notification, Transfer, LandingPageContent, Category
)

User = get_user_model()


# ---------------- USER ADMIN ----------------

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Shop Assignment'
    fields = ['role', 'assigned_shop']


class CustomUserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]

    list_display = [
        'username', 'email', 'first_name', 'last_name',
        'is_staff', 'get_shop_role'
    ]

    @admin.display(description='Role / Shop')
    def get_shop_role(self, obj):
        try:
            profile = obj.user_profile
            if profile.is_admin:
                return 'Admin (All Shops)'
            return f'Shop User: {profile.assigned_shop_name}'
        except UserProfile.DoesNotExist:
            return 'Not configured'


# Safe unregister (prevents crash if already unregistered)
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

admin.site.register(User, CustomUserAdmin)
admin.site.register(Profile)


# ---------------- LANDING PAGE ----------------

@admin.register(LandingPageContent)
class LandingPageContentAdmin(admin.ModelAdmin):
    list_display = ['__str__']
    fields = ['data', 'image']


# ---------------- SHOP + ITEMS ----------------

class ItemInline(admin.TabularInline):
    model = Item
    extra = 1
    fields = ['name', 'category', 'quantity', 'unit_price']


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ['name', 'item_count', 'assigned_user_count']
    search_fields = ['name']
    inlines = [ItemInline]

    @admin.display(description="Total Items")
    def item_count(self, obj):
        return obj.items.count()

    @admin.display(description="Assigned Users")
    def assigned_user_count(self, obj):
        return obj.assigned_users.count()


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'shop', 'category', 'quantity',
        'unit_price', 'total_value_display', 'low_stock'
    ]
    list_filter = ['shop', 'category']
    search_fields = ['name', 'shop__name']
    list_editable = ['quantity', 'unit_price']
    autocomplete_fields = ['shop']

    @admin.display(description="Total Value")
    def total_value_display(self, obj):
        return f"MWK {obj.total_value:,.2f}"

    @admin.display(boolean=True, description="Low Stock")
    def low_stock(self, obj):
        return obj.quantity < 5


# ---------------- SALES ----------------

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['item', 'shop_name', 'quantity_sold', 'total_amount', 'sold_at']
    list_filter = ['item__shop', 'sold_at']
    search_fields = ['item__name', 'item__shop__name']

    @admin.display(description="Shop")
    def shop_name(self, obj):
        return obj.item.shop.name


# ---------------- STOCK ----------------

@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = [
        'item', 'source_shop', 'target_shop',
        'quantity', 'transaction_type', 'created_at'
    ]
    list_filter = ['transaction_type', 'source_shop', 'target_shop']
    search_fields = ['item__name', 'source_shop__name']


# ---------------- MESSAGES ----------------

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'short_content', 'is_read', 'timestamp']
    list_filter = ['is_read', 'timestamp']
    search_fields = ['sender__username', 'receiver__username', 'content']

    @admin.display(description="Message")
    def short_content(self, obj):
        return obj.content[:50]


# ---------------- TRANSFERS ----------------

@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = [
        'transfer_code', 'sender_shop', 'receiver_shop',
        'created_by', 'status', 'created_at'
    ]
    list_filter = ['status', 'sender_shop', 'receiver_shop']
    search_fields = ['transfer_code', 'sender_shop__name', 'receiver_shop__name']
    readonly_fields = ['transfer_code', 'created_at']


# ---------------- NOTIFICATIONS ----------------

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'is_read', 'created_at']
    list_filter = ['is_read', 'created_at']
    search_fields = ['title', 'message', 'user__username']


# ---------------- CATEGORIES ----------------

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']
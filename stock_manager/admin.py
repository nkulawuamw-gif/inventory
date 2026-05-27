from django.contrib import admin
from django.contrib.auth import get_user_model
User = get_user_model()
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Shop, Item, Sale, Receipt, ReceiptItem, StockTransaction, UserProfile, Profile, Message, BusinessPeriod, CompanyProfile, WhatsAppSetting, WhatsAppMessage, Notification


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Shop Assignment'
    fields = ['role', 'assigned_shop']


class CustomUserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'get_shop_role']

    @admin.display(description='Role / Shop')
    def get_shop_role(self, obj):
        try:
            profile = obj.user_profile
            if profile.is_admin:
                return 'Admin (All Shops)'
            return f'Shop User: {profile.assigned_shop_name}'
        except UserProfile.DoesNotExist:
            return 'Not configured'


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
admin.site.register(Profile)


class ItemInline(admin.TabularInline):
    model = Item
    extra = 1
    fields = ['name', 'category', 'quantity', 'unit_price']


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ['name', 'item_count', 'assigned_user_count']
    search_fields = ['name']
    inlines = [ItemInline]

    def item_count(self, obj):
        return obj.items.count()

    item_count.short_description = 'Total Items'

    def assigned_user_count(self, obj):
        return obj.assigned_users.count()

    assigned_user_count.short_description = 'Assigned Users'


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'shop', 'category', 'quantity', 'unit_price', 'total_value', 'is_low_stock']
    list_filter = ['shop', 'category']
    search_fields = ['name', 'shop__name']
    list_editable = ['quantity', 'unit_price']
    autocomplete_fields = ['shop']

    def total_value(self, obj):
        return f"MWK {obj.total_value:,.2f}"

    def is_low_stock(self, obj):
        return obj.quantity < 5

    is_low_stock.boolean = True
    is_low_stock.short_description = 'Low Stock'


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['item', 'shop_name', 'quantity_sold', 'total_amount', 'sold_at']
    list_filter = ['item__shop', 'sold_at']
    search_fields = ['item__name', 'item__shop__name']

    def shop_name(self, obj):
        return obj.item.shop.name

    shop_name.short_description = 'Shop'


@admin.register(StockTransaction)
class StockTransactionAdmin(admin.ModelAdmin):
    list_display = ['item', 'source_shop', 'target_shop', 'quantity', 'transaction_type', 'created_at']
    list_filter = ['transaction_type', 'source_shop', 'target_shop']
    search_fields = ['item__name', 'source_shop__name']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['sender', 'receiver', 'content_short', 'is_read', 'timestamp']
    list_filter = ['is_read', 'timestamp']
    search_fields = ['sender__username', 'receiver__username', 'content']

    def content_short(self, obj):
        return obj.content[:50]
    content_short.short_description = 'Message'


@admin.register(BusinessPeriod)
class BusinessPeriodAdmin(admin.ModelAdmin):
    list_display = ['name', 'period_type', 'start_date', 'end_date', 'is_closed', 'closed_at', 'closed_by']
    list_filter = ['period_type', 'is_closed']
    search_fields = ['name']
    date_hierarchy = 'start_date'


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'shop', 'customer_name', 'total', 'created_at', 'created_by']
    list_filter = ['shop', 'created_at']
    search_fields = ['receipt_number', 'customer_name']
    date_hierarchy = 'created_at'


@admin.register(ReceiptItem)
class ReceiptItemAdmin(admin.ModelAdmin):
    list_display = ['receipt', 'item_name', 'quantity', 'unit_price', 'total']
    search_fields = ['item_name', 'receipt__receipt_number']


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'phone', 'email']


@admin.register(WhatsAppSetting)
class WhatsAppSettingAdmin(admin.ModelAdmin):
    list_display = ['phone_number', 'business_name', 'is_active']


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = ['customer_number', 'customer_name', 'body', 'is_from_customer', 'is_read', 'created_at']
    list_filter = ['is_from_customer', 'is_read']
    search_fields = ['customer_number', 'customer_name', 'body']
    date_hierarchy = 'created_at'


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'type', 'is_read', 'created_at']
    list_filter = ['type', 'is_read', 'created_at']
    search_fields = ['title', 'message', 'user__username']
